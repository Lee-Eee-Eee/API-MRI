import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import vm from 'node:vm';
import { runtimeRequestOrigin } from '../src/runtime.js';

// Exercise the real route body over HTTP with the Desktop protocol's forwarding
// contract: Origin removed, host authentication cookie injected, JSON preserved.
// The runtime is a fixture so this test never downloads software or calls models.
async function fixture() {
  const source = (await readFile(new URL('../src/index.js', import.meta.url), 'utf8'))
    .replace(/^import[^\n]*\r?\n/gm, '').replace(/^export /gm, '');
  const routes = new Map(), disposers = [], starts = [];
  let phase = 'idle', disposed = 0;
  const runtime = { snapshot: () => ({ phase }), status: async () => ({ phase }), start: async origin => {
    starts.push(origin); phase = 'ready'; return { phase, url: 'http://127.0.0.1:19091' };
  }, dispose: () => { disposed++; } };
  const context = {
    createRuntimeManager: () => runtime,
    createModelBridge: () => ({ dispose() {} }),
    readJson: async () => assert.fail('Runtime route owns its bounded JSON reader'),
    BRIDGE_LIMITS: {}, runtimeRequestOrigin, Buffer,
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nthis.mount = apply;`, context);
  context.mount({ effect(fn) { disposers.push(fn()); }, llm: {},
    webServer: { register(route) { routes.set(route.path, route); return () => routes.delete(route.path); } },
    connection: { requestRejection(req) {
      return req.headers.cookie === 'dsh-host=fixture-auth' ? undefined : 'forbidden';
    } },
  });
  const server = createServer((req, res) => {
    const route = routes.get(req.url);
    if (!route) { res.writeHead(404); res.end(); return; }
    void Promise.resolve(route.handler(req, res)).catch(() => { res.writeHead(500); res.end('{}'); });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}/api/api-mri/runtime`;
  const call = (body, { method = 'POST', auth = true, origin } = {}) => fetch(url, {
    method, signal: AbortSignal.timeout(5000),
    headers: { ...(auth ? { Cookie: 'dsh-host=fixture-auth' } : {}),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(origin ? { Origin: origin } : {}) },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  return { call, starts, runtime, get disposed() { return disposed; },
    async close() { disposers.reverse().forEach(dispose => dispose?.()); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); } };
}

test('Desktop forwarded startup authenticates without Origin and connects without a manually selected port', async () => {
  const f = await fixture();
  try {
    const response = await f.call({ embedOrigin: 'dsh-app://app' });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { phase: 'ready', url: 'http://127.0.0.1:19091' });
    assert.deepEqual(f.starts, ['dsh-app://app']);
    const poll = await f.call(undefined, { method: 'GET' });
    assert.equal(poll.status, 200); assert.equal((await poll.json()).phase, 'ready');
  } finally { await f.close(); }
  assert.equal(f.disposed, 1);
});

test('missing host authentication, mismatched Web origin and oversized startup bodies cannot start a runtime', async () => {
  const f = await fixture();
  try {
    assert.equal((await f.call({ embedOrigin: 'dsh-app://app' }, { auth: false })).status, 403);
    assert.equal((await f.call({ embedOrigin: 'dsh-app://app' }, { origin: 'http://127.0.0.1:3088' })).status, 400);
    assert.equal((await f.call({ embedOrigin: 'http://127.0.0.1:3088' })).status, 400);
    assert.equal((await f.call({ embedOrigin: 'dsh-app://app', padding: 'a'.repeat(1024) })).status, 413);
    assert.equal((await f.call(undefined, { method: 'PUT' })).status, 405);
    assert.deepEqual(f.starts, []);
  } finally { await f.close(); }
});

test('Web startup uses the same authenticated route with matching Origin', async () => {
  const f = await fixture();
  try {
    assert.equal((await f.call({ embedOrigin: 'http://127.0.0.1:3088' }, { origin: 'http://127.0.0.1:3088' })).status, 200);
    assert.deepEqual(f.starts, ['http://127.0.0.1:3088']);
  } finally { await f.close(); }
});
