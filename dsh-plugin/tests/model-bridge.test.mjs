import test from 'node:test';
import assert from 'node:assert/strict';
import { request as httpRequest } from 'node:http';
import { createModelBridge } from '../src/model-bridge.js';
import { encodeModelId } from '../src/dsh-adapter.js';

const id = encodeModelId('p', 'm');
const body = extra => ({ model: id, messages: [{ role: 'user', content: 'Synthetic probe' }], max_tokens: 8, ...extra });
const finish = { type: 'finish', reason: { kind: 'stop' } };
function mock(stream) {
  let calls = 0;
  return {
    get calls() { return calls; },
    listProviders: () => [{ id: 'p', name: 'Provider' }],
    listModels: async () => ['m', 'other'].map(model => ({ provider: 'p', id: model, name: model })),
    resolveModelInfo: async () => ({ reasoning: { efforts: [{ id: 'off' }] } }),
    async *stream(options) {
      calls++;
      if (stream) yield* stream(options);
      else { yield { type: 'text-delta', index: 0, text: 'OK' }; yield { type: 'usage', usage: { inputTokens: 3, outputTokens: 1 } }; yield finish; }
    },
  };
}
const issue = (bridge, extra = {}) => bridge.issue({ model: id, maxRequests: 8, maxOutputTokens: 64, ...extra });
const call = (lease, value = body(), headers = {}, options = {}) => fetch(lease.candidate.api_base + '/chat/completions', {
  method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + lease.candidate.api_key, ...headers },
  body: JSON.stringify(value), ...options,
});
const rawCall = (lease, headers) => new Promise((resolve, reject) => {
  const req = httpRequest(lease.candidate.api_base + '/chat/completions', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + lease.candidate.api_key, ...headers } }, res => {
    res.resume(); res.on('end', () => resolve(res.statusCode));
  });
  req.on('error', reject); req.end(JSON.stringify(body()));
});

test('HTTP bridge binds one configured model and rejects absent or forged authorization and browser origins', async () => {
  const llm = mock(), bridge = createModelBridge(llm);
  try {
    const lease = await issue(bridge);
    const missing = await call(lease, body(), { Authorization: '' }); assert.equal(missing.status, 401);
    const forged = await call(lease, body(), { Authorization: 'Bearer ' + '0'.repeat(64) }); assert.equal(forged.status, 401);
    const other = await call(lease, body({ model: encodeModelId('p', 'other') })); assert.equal(other.status, 403);
    const origin = await call(lease, body(), { Origin: 'http://127.0.0.1:9999' }); assert.equal(origin.status, 403);
    assert.equal(await rawCall(lease, { Host: 'evil.test' }), 403);
    assert.equal(llm.calls, 0);
    const good = await call(lease); assert.equal(good.status, 200);
    const value = await good.json(); assert.equal(value.choices[0].message.content, 'OK');
    assert.equal(value.usage.total_tokens, 4); assert.equal(good.headers.get('x-api-mri-transport'), 'dsh-adapter');
  } finally { await bridge.dispose(); }
});

test('request count and reserved output ceilings are enforced before model calls', async () => {
  const llm = mock(), bridge = createModelBridge(llm);
  try {
    const count = await issue(bridge, { maxRequests: 1 });
    assert.equal((await call(count)).status, 200); assert.equal((await call(count)).status, 429);
    const output = await issue(bridge, { maxOutputTokens: 12 });
    assert.equal((await call(output)).status, 200); assert.equal((await call(output)).status, 429);
    assert.equal(llm.calls, 2);
    await assert.rejects(issue(bridge, { maxRequests: 129 }));
    await assert.rejects(issue(bridge, { maxOutputTokens: 65537 }));
    await assert.rejects(issue(bridge, { model: encodeModelId('p', 'not-listed') }));
  } finally { await bridge.dispose(); }
});

test('leases expire using their issued lifetime and are not model API keys', async () => {
  let now = 1000; const llm = mock(), bridge = createModelBridge(llm, { now: () => now, lifetimeMs: 10 });
  try {
    const lease = await issue(bridge); assert.match(lease.candidate.api_key, /^[a-f0-9]{64}$/);
    now = 1010;
    const expired = await call(lease); assert.equal(expired.status, 401);
    assert.equal(llm.calls, 0); assert.ok(!(await expired.text()).includes(lease.candidate.api_key));
  } finally { await bridge.dispose(); }
});

test('real SSE retains data frames, finish and usage; unsupported controls return 422 before SSE', async () => {
  const llm = mock(), bridge = createModelBridge(llm);
  try {
    const lease = await issue(bridge);
    const bad = await call(lease, body({ stream: true, tool_choice: 'required' }));
    assert.equal(bad.status, 422); assert.match(bad.headers.get('content-type'), /application\/json/); assert.equal(llm.calls, 0);
    const res = await call(lease, body({ stream: true, stream_options: { include_usage: true } }));
    assert.equal(res.status, 200); assert.match(res.headers.get('content-type'), /text\/event-stream/);
    const frames = (await res.text()).split('\n\n').filter(Boolean).map(line => line.slice(6));
    assert.equal(frames.pop(), '[DONE]');
    const chunks = frames.map(JSON.parse);
    assert.equal(chunks.flatMap(row => row.choices).map(row => row.delta.content || '').join(''), 'OK');
    assert.equal(chunks.at(-2).choices[0].finish_reason, 'stop'); assert.equal(chunks.at(-1).usage.total_tokens, 4);
  } finally { await bridge.dispose(); }
});

test('provider exceptions and terminal error messages cannot leak upstream secrets', async () => {
  for (const stream of [async function* () { throw Object.assign(new Error('FAKE-UPSTREAM-SECRET'), { status: 401, code: 'FAKE-UPSTREAM-SECRET' }); },
    async function* () { yield { type: 'finish', reason: { kind: 'error', failure: { code: 'AUTH', message: 'FAKE-UPSTREAM-SECRET' } } }; }]) {
    const llm = mock(stream), bridge = createModelBridge(llm);
    try {
      const res = await call(await issue(bridge)); assert.equal(res.status, 502);
      const value = await res.text(); assert.ok(!value.includes('FAKE-UPSTREAM-SECRET')); assert.match(value, /dsh_model_error|dsh_upstream_auth/);
    } finally { await bridge.dispose(); }
  }
});

test('concurrent rejections do not release another request lock, and disconnect cancels its model call', async () => {
  let entered, cancelled;
  const started = new Promise(resolve => { entered = resolve; });
  const stopped = new Promise(resolve => { cancelled = resolve; });
  const llm = mock(async function* ({ signal }) {
    entered(); yield { type: 'text-delta', index: 0, text: 'A' };
    await new Promise(resolve => signal.addEventListener('abort', () => { cancelled(); resolve(); }, { once: true }));
  });
  const bridge = createModelBridge(llm);
  try {
    const lease = await issue(bridge), controller = new AbortController();
    const first = await call(lease, body({ stream: true }), {}, { signal: controller.signal });
    await started;
    assert.equal((await call(lease)).status, 409); assert.equal((await call(lease)).status, 409);
    assert.equal(llm.calls, 1); controller.abort();
    await Promise.race([stopped, new Promise((_, reject) => setTimeout(() => reject(Error('abort did not reach DSH')), 1500))]);
    await first.body.cancel().catch(() => {});
  } finally { await bridge.dispose(); }
});

test('two already-reading bodies cannot both enter one model lease', async () => {
  let entered; const started = new Promise(resolve => { entered = resolve; });
  const llm = mock(async function* ({ signal }) {
    entered();
    await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
  });
  const bridge = createModelBridge(llm);
  const requests = [];
  try {
    const lease = await issue(bridge), url = new URL(lease.candidate.api_base + '/chat/completions');
    function partial() {
      let resolve; const response = new Promise(done => { resolve = done; });
      const req = httpRequest(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + lease.candidate.api_key } }, res => {
        let content = ''; res.on('data', data => { content += data; }); res.on('end', () => resolve({ status: res.statusCode, content }));
      });
      req.on('error', () => resolve({ status: 0 })); req.write('{'); requests.push(req); return { req, response };
    }
    const a = partial(), b = partial();
    await new Promise(resolve => setTimeout(resolve, 30));
    const remainder = JSON.stringify(body()).slice(1); a.req.end(remainder); await started; b.req.end(remainder);
    const result = await Promise.race([b.response, new Promise(resolve => setTimeout(() => resolve({ status: -1 }), 500))]);
    assert.equal(result.status, 409); assert.equal(llm.calls, 1);
  } finally { for (const req of requests) req.destroy(); await bridge.dispose(); }
});


test('identity request with vendor usage option reaches the configured model', async () => {
  let received;
  const llm = mock(async function* (options) { received = options; yield { type: 'text-delta', index: 0, text: '42' }; yield finish; });
  const bridge = createModelBridge(llm);
  try {
    const res = await call(await issue(bridge), body({ usage: { include: true }, reasoning: { enabled: false }, temperature: 0 }));
    assert.equal(res.status, 200); assert.equal(llm.calls, 1);
    assert.equal(received.reasoningEffort, 'off'); assert.equal(received.usage, undefined);
    assert.equal((await res.json()).choices[0].message.content, '42');
  } finally { await bridge.dispose(); }
});

test('upstream failures have fixed safe categories in JSON and SSE', async () => {
  for (const [code, expected] of [['AUTH', 'dsh_upstream_auth'], ['QUOTA', 'dsh_upstream_quota'],
    ['RATE_LIMIT', 'dsh_upstream_rate_limit'], ['TIMEOUT', 'dsh_upstream_timeout'], ['UNTRUSTED-SECRET', 'dsh_model_error']]) {
    for (const stream of [false, true]) {
      const llm = mock(async function* () { yield { type: 'finish', reason: { kind: 'error', failure: { code, message: 'PRIVATE-PROVIDER-SECRET' } } }; });
      const bridge = createModelBridge(llm);
      try {
        const res = await call(await issue(bridge), body({ stream }));
        const raw = await res.text();
        assert.equal(res.status, stream ? 200 : 502);
        assert.ok(!raw.includes('PRIVATE-PROVIDER-SECRET'));
        assert.ok(!raw.includes('UNTRUSTED-SECRET'));
        assert.match(raw, new RegExp(expected));
        if (stream) assert.ok(!raw.includes('[DONE]'));
      } finally { await bridge.dispose(); }
    }
  }
});

test('provider cannot opt raw private errors into the trusted bridge validation path', async () => {
  const llm = mock(async function* () {
    throw Object.assign(new Error('PRIVATE-PROVIDER-SECRET'), { code: 'PRIVATE-PROVIDER-SECRET', status: 422, safeForClient: true });
  });
  const bridge = createModelBridge(llm);
  try {
    const res = await call(await issue(bridge)); const raw = await res.text();
    assert.equal(res.status, 502); assert.match(raw, /dsh_model_error/); assert.ok(!raw.includes('PRIVATE-PROVIDER-SECRET'));
  } finally { await bridge.dispose(); }
});
