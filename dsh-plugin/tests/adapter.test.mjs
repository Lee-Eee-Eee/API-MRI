import test from 'node:test';
import assert from 'node:assert/strict';
import { listConfiguredModels, encodeModelId, decodeModelId, validateCompletionRequest, completionChunks } from '../src/dsh-adapter.js';

const id = encodeModelId('route:gateway', 'vendor/model:name');
const request = extra => ({ model: id, messages: [{ role: 'user', content: 'Synthetic probe' }], max_tokens: 32, temperature: 0, ...extra });
const collect = async stream => { const output = []; for await (const item of stream) output.push(item); return output; };
function model(events, inspect = () => {}) {
  return { async *stream(options) { inspect(options); yield* events; },
    async resolveModelInfo() { return { reasoning: { efforts: [{ id: 'off' }, { id: 'high' }] } }; } };
}

test('catalog uses public fields and opaque route ids, retaining healthy providers', async () => {
  const llm = { listProviders: () => [{ id: 'route:gateway', name: 'Gateway', secret: 'hidden' }, { id: 'broken', name: 'Broken' }],
    async listModels(provider) { if (provider === 'broken') throw Error('do not expose this'); return [{ provider, id: 'vendor/model:name', name: 'Model', apiKey: 'hidden' }]; } };
  const rows = await listConfiguredModels(llm);
  assert.equal(rows.length, 1); assert.equal(rows[0].id, id); assert.equal(rows[0].label, 'Model · Gateway');
  assert.ok(!JSON.stringify(rows).includes('hidden'));
  assert.deepEqual(decodeModelId(id), { provider: 'route:gateway', model: 'vendor/model:name' });
  for (const value of ['x', 'dsh:a:b:c', 'dsh:%zz:b', 'dsh:a:%62']) assert.throws(() => decodeModelId(value));
});

test('request conversion preserves token cap, temperature, stop and advertised reasoning effort', async () => {
  const prepared = await validateCompletionRequest(model([]), request({ stop: ['END'], reasoning_effort: 'off' }));
  assert.equal(prepared.options.maxTokens, 32); assert.equal(prepared.options.temperature, 0);
  assert.deepEqual(prepared.options.stop, ['END']); assert.equal(prepared.options.reasoningEffort, 'off');
  assert.deepEqual(prepared.options.messages, [{ role: 'user', content: [{ type: 'text', text: 'Synthetic probe' }] }]);
  const other = await validateCompletionRequest(model([]), request({ max_tokens: undefined, max_completion_tokens: 23 }));
  assert.equal(other.options.maxTokens, 23);
  const off = await validateCompletionRequest(model([]), request({ reasoning: { enabled: false } }));
  assert.equal(off.options.reasoningEffort, 'off');
});

test('unsupported controls fail before a model call, including forced tools and guessed reasoning-off', async () => {
  let calls = 0; const llm = model([], () => calls++);
  for (const extra of [{ seed: 1 }, { reasoning: { enabled: true } }, { response_format: {} }, { top_p: 1 },
    { tool_choice: { type: 'function', function: { name: 'probe' } } }, { tool_choice: 'required' },
    { reasoning_effort: 'disabled' }, { max_completion_tokens: 10 }, { temperature: NaN }, { stream_options: { include_usage: true, extra: 1 } }]) {
    await assert.rejects(validateCompletionRequest(llm, request(extra)), error => error.status === 422 && error.safeForClient === true);
  }
  assert.equal(calls, 0);
  await assert.rejects(validateCompletionRequest({ resolveModelInfo: async () => ({}) }, request({ reasoning: { enabled: false } })));
  await assert.rejects(validateCompletionRequest({ resolveModelInfo: async () => ({ reasoning: { efforts: [{ id: 'none' }] } }) }, request({ reasoning: { enabled: false } })));
});

test('fingerprint capability distinguishes advertised off, unsupported and unknown metadata', async () => {
  const llm = {
    listProviders: () => [{ id: 'p', name: 'P' }],
    listModels: async () => ['off', 'high', 'unknown'].map(id => ({ provider: 'p', id, name: id })),
    resolveModelInfo: async (p, id) => id === 'unknown' ? {} : { reasoning: { efforts: [{ id }] } },
  };
  const rows = await listConfiguredModels(llm);
  assert.deepEqual(rows.map(row => row.fingerprint_supported), [true, false, null]);
});

test('tool declarations and history preserve function calls and tool result ids; none removes tools', async () => {
  const tool = { type: 'function', function: { name: 'lookup', description: 'Synthetic', parameters: { type: 'object' } } };
  const messages = [{ role: 'system', content: 'Test only' }, { role: 'user', content: 'lookup' },
    { role: 'assistant', content: null, tool_calls: [{ id: 'call-1', type: 'function', function: { name: 'lookup', arguments: '{"x":1}' } }] },
    { role: 'tool', tool_call_id: 'call-1', content: '{"value":2}' }];
  const prepared = await validateCompletionRequest(model([]), request({ tools: [tool], tool_choice: 'auto', messages }));
  assert.equal(prepared.options.tools[0].name, 'lookup');
  assert.equal(prepared.options.messages[0].source.kind, 'system-prompt');
  assert.equal(prepared.options.messages[2].source.provider, 'route:gateway');
  assert.equal(prepared.options.messages[2].content[0].arguments, '{"x":1}');
  assert.equal(prepared.options.messages[3].source.callId, 'call-1');
  assert.equal(prepared.options.messages[3].toolCallId, 'call-1');
  const none = await validateCompletionRequest(model([]), request({ tools: [tool], tool_choice: 'none' }));
  assert.equal(none.options.tools, undefined);
  await assert.rejects(validateCompletionRequest(model([]), request({ tools: [{ ...tool, function: { ...tool.function, strict: true } }] })));
});

test('stream converts deltas and disjoint cache usage without duplicating block-end content', async () => {
  const events = [{ type: 'text-delta', index: 0, text: 'Hi' }, { type: 'block-end', index: 0, block: { type: 'text', text: 'Hi' } },
    { type: 'usage', usage: { inputTokens: 4, cacheReadTokens: 3, cacheWriteTokens: 2, outputTokens: 5, reasoningTokens: 1 } },
    { type: 'finish', reason: { kind: 'stop' } }];
  let seen; const chunks = await collect(completionChunks(model(events, value => { seen = value; }), request({ stream: true, stream_options: { include_usage: true } })));
  assert.equal(seen.provider, 'route:gateway'); assert.equal(seen.model, 'vendor/model:name');
  assert.equal(chunks.flatMap(c => c.choices).map(c => c.delta.content || '').join(''), 'Hi');
  assert.equal(chunks.at(-2).choices[0].finish_reason, 'stop');
  assert.equal(chunks.at(-1).usage.prompt_tokens, 9); assert.equal(chunks.at(-1).usage.total_tokens, 14);
  assert.equal(chunks.at(-1).usage.completion_tokens_details.reasoning_tokens, 1);
});

test('stream tool indexes are contiguous and final blocks do not duplicate arguments', async () => {
  const events = [{ type: 'tool-call-delta', index: 2, id: 'c1', name: 'lookup', argumentsDelta: '{' },
    { type: 'tool-call-delta', index: 2, id: 'c1', name: 'lookup', argumentsDelta: '}' },
    { type: 'block-end', index: 2, block: { type: 'tool-call', id: 'c1', name: 'lookup', arguments: '{}' } },
    { type: 'block-end', index: 4, block: { type: 'tool-call', id: 'c2', name: 'other', arguments: '{}' } },
    { type: 'finish', reason: { kind: 'tool-calls' } }];
  const chunks = await collect(completionChunks(model(events), request({ stream: false })));
  const calls = chunks.flatMap(c => c.choices).flatMap(c => c.delta.tool_calls || []);
  assert.deepEqual(calls.map(c => c.index), [0, 0, 1]);
  assert.equal(calls.filter(c => c.index === 0).map(c => c.function.arguments).join(''), '{}');
  assert.equal(calls.filter(c => c.index === 0).map(c => c.function.name || '').join(''), 'lookup');
  assert.equal(calls.filter(c => c.index === 0).map(c => c.id || '').join(''), 'c1');
  assert.equal(chunks.at(-1).choices[0].finish_reason, 'tool_calls');
});

test('incomplete, failed and cancelled model attempts cannot become successful completions', async () => {
  await assert.rejects(collect(completionChunks(model([]), request())), error => error.code === 'incomplete_stream');
  await assert.rejects(collect(completionChunks(model([{ type: 'finish', reason: { kind: 'error', failure: { code: 'AUTH', message: 'secret raw provider message' } } }]), request())), error => error.code === 'AUTH' && !error.message.includes('secret') && error.safeForClient === false);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(collect(completionChunks(model([{ type: 'text-delta', index: 0, text: 'x' }]), request(), { signal: controller.signal })), error => error.code === 'ABORTED');
});
