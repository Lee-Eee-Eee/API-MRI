import { randomUUID } from 'node:crypto';

export class DshAdapterError extends Error {
  constructor(message, code = 'unsupported_parameter', status = 422) {
    super(message); this.name = 'DshAdapterError'; this.code = code; this.status = status;
    this.safeForClient = true;
  }
}
const fail = (message, code, status) => { throw new DshAdapterError(message, code, status); };
// Raw provider messages, status values, and arbitrary codes can contain credentials.
// Only fixed protocol codes cross the bridge; an upstream auth failure is not a
// failure of the temporary local lease, so keep it under a 502 response.
const UPSTREAM_FAILURES = Object.freeze({
  AUTH: ['dsh_upstream_auth', 'DSH 上游鉴权失败，请在 DSH 中检查该服务的登录或密钥。'],
  MISSING_CREDENTIAL: ['dsh_upstream_auth', 'DSH 上游尚未配置凭据，请先在 DSH 中完成登录或配置密钥。'],
  INVALID_CREDENTIAL: ['dsh_upstream_auth', 'DSH 上游凭据无效，请在 DSH 中更新登录或密钥。'],
  QUOTA: ['dsh_upstream_quota', 'DSH 上游额度不足，请在对应服务中检查余额或用量。'],
  ACCOUNT_QUOTA: ['dsh_upstream_quota', 'DSH 上游账户额度不足，请检查该账户的用量。'],
  RATE_LIMIT: ['dsh_upstream_rate_limit', 'DSH 上游触发限流，请稍后重试。'],
  TIMEOUT: ['dsh_upstream_timeout', 'DSH 上游响应超时。'],
  TRANSPORT: ['dsh_upstream_transport', 'DSH 与上游的连接中断，请检查该服务的网络连接。'],
  INVALID_REQUEST: ['dsh_upstream_request', 'DSH 上游拒绝了本次请求参数。'],
  UNSUPPORTED_REASONING_EFFORT: ['unsupported_reasoning', 'DSH 当前模型不支持指定的推理设置。'],
  NO_ADAPTER: ['dsh_adapter_unavailable', 'DSH 当前模型的服务适配器不可用，请刷新模型目录。'],
  SERVER: ['dsh_upstream_server', 'DSH 上游服务暂时不可用。'],
  EMPTY_RESPONSE: ['dsh_upstream_empty', 'DSH 上游返回了空响应。'],
  CONTEXT_WINDOW_EXCEEDED: ['dsh_upstream_context', '本次请求超过了 DSH 上游模型的上下文限制。'],
  ABORTED: ['dsh_request_cancelled', 'DSH 模型调用已取消。'],
});
export function publicCompletionError(error) {
  if (error instanceof DshAdapterError) return { status: error.status, code: error.code, message: error.message };
  const mapped = Object.hasOwn(UPSTREAM_FAILURES, error?.code) ? UPSTREAM_FAILURES[error.code] : null;
  return mapped ? { status: error.code === 'UNSUPPORTED_REASONING_EFFORT' ? 422 : 502, code: mapped[0], message: mapped[1] }
    : { status: 502, code: 'dsh_model_error', message: 'DSH 模型调用失败，请检查该模型在 DSH 中的连接。' };
}
function upstreamFailure(code) {
  const value = publicCompletionError({ code });
  throw new DshAdapterError(value.message, value.code, value.status);
}
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.length > 0;
function keys(value, allowed, location) {
  if (!object(value)) fail(`${location} must be an object.`, 'invalid_request');
  if (Object.keys(value).some(key => !allowed.includes(key))) fail(`Unsupported fields in ${location}.`);
}
export function encodeModelId(provider, model) {
  return `dsh:${encodeURIComponent(provider)}:${encodeURIComponent(model)}`;
}
export function decodeModelId(id) {
  if (typeof id !== 'string' || !/^dsh:[^:]+:[^:]+$/.test(id)) fail('Select a configured DSH model.', 'invalid_model');
  try {
    const [, p, m] = id.split(':');
    const provider = decodeURIComponent(p), model = decodeURIComponent(m);
    if (!provider || !model || encodeModelId(provider, model) !== id) throw new Error();
    return { provider, model };
  } catch { return fail('Invalid DSH model identifier.', 'invalid_model'); }
}

/** Public adapter catalogs only; no settings or credential service is read. */
export async function listConfiguredModels(llm) {
  const providers = await llm.listProviders();
  const groups = await Promise.all(providers.map(async provider => {
    try {
      const models = await llm.listModels(provider.id);
      return Promise.all(models.filter(row => row.provider === provider.id && text(row.id)).map(async row => {
        let fingerprint = null;
        try {
          const info = await llm.resolveModelInfo?.(provider.id, row.id);
          if (info?.reasoning?.efforts) fingerprint = info.reasoning.efforts.some(effort => effort.id === 'off');
        } catch { /* Catalog membership does not imply complete exact-model metadata. */ }
        return {
          id: encodeModelId(provider.id, row.id), model: row.id, provider: provider.id,
          name: row.name || row.id, provider_name: provider.name || provider.id,
          label: `${row.name || row.id} · ${provider.name || provider.id}`,
          // True means the DSH adapter advertises an off control. Runtime evidence
          // must still establish that the upstream actually honored that control.
          fingerprint_supported: fingerprint,
        };
      }));
    } catch { return []; }
  }));
  return groups.flat();
}

function content(value) {
  if (typeof value === 'string') return value.length ? [{ type: 'text', text: value }] : [];
  if (!Array.isArray(value)) fail('Only text message content is supported.', 'unsupported_content');
  return value.map(part => {
    keys(part, ['type', 'text'], 'content');
    if (part.type !== 'text' || typeof part.text !== 'string') fail('Only text message content is supported.', 'unsupported_content');
    return { type: 'text', text: part.text };
  });
}
function messagesFor(rows, route) {
  if (!Array.isArray(rows) || !rows.length || rows.length > 100) fail('messages must contain 1–100 messages.', 'invalid_request');
  return rows.map(row => {
    keys(row, ['role', 'content', 'tool_calls', 'tool_call_id'], 'message');
    if (!['system', 'user', 'assistant', 'tool'].includes(row.role)) fail('Unsupported message role.');
    const blocks = content(row.content ?? (row.role === 'assistant' ? '' : undefined));
    if (row.role !== 'assistant' && row.tool_calls !== undefined || row.role !== 'tool' && row.tool_call_id !== undefined) fail('Tool fields do not match message role.', 'invalid_request');
    if (row.role === 'user') return { role: 'user', content: blocks };
    const id = randomUUID();
    if (row.role === 'system') return { id, role: 'system', source: { kind: 'system-prompt' }, content: blocks };
    if (row.role === 'tool') {
      if (!text(row.tool_call_id)) fail('Tool result requires tool_call_id.', 'invalid_request');
      return { id, role: 'tool', source: { kind: 'tool', callId: row.tool_call_id }, toolCallId: row.tool_call_id, content: blocks };
    }
    if (row.tool_calls !== undefined) {
      if (!Array.isArray(row.tool_calls)) fail('tool_calls must be an array.', 'invalid_request');
      for (const call of row.tool_calls) {
        keys(call, ['id', 'type', 'function'], 'tool call');
        keys(call.function, ['name', 'arguments'], 'tool call function');
        if (call.type !== 'function' || !text(call.id) || !text(call.function.name) || typeof call.function.arguments !== 'string') fail('Invalid tool call.', 'invalid_request');
        blocks.push({ type: 'tool-call', id: call.id, name: call.function.name, arguments: call.function.arguments });
      }
    }
    return { id, role: 'assistant', source: { kind: 'model', ...route }, content: blocks };
  });
}

/** Resolve supported controls before a host commits an HTTP/SSE success status. */
export async function validateCompletionRequest(llm, request, { signal } = {}) {
  if (signal?.aborted) fail('DSH request cancelled.', 'ABORTED', 499);
  keys(request, ['model', 'messages', 'stream', 'stream_options', 'temperature', 'max_tokens', 'max_completion_tokens',
    'stop', 'reasoning_effort', 'reasoning', 'tools', 'tool_choice', 'usage'], 'completion request');
  // OpenRouter-style usage is a response option used by API-MRI's probes.
  // DSH already produces terminal usage; never forward this vendor field.
  if (request.usage !== undefined) {
    keys(request.usage, ['include'], 'usage');
    if (typeof request.usage.include !== 'boolean') fail('usage.include must be boolean.', 'invalid_request');
  }
  const route = decodeModelId(request.model);
  const options = { ...route, messages: messagesFor(request.messages, route), signal };
  if (request.stream !== undefined && typeof request.stream !== 'boolean') fail('stream must be boolean.', 'invalid_request');
  if (request.stream_options !== undefined) {
    keys(request.stream_options, ['include_usage'], 'stream_options');
    if (typeof request.stream_options.include_usage !== 'boolean') fail('include_usage must be boolean.', 'invalid_request');
  }
  if (request.temperature !== undefined) {
    if (typeof request.temperature !== 'number' || !Number.isFinite(request.temperature) || request.temperature < 0 || request.temperature > 2) fail('temperature must be within 0–2.', 'invalid_request');
    options.temperature = request.temperature;
  }
  if (request.max_tokens !== undefined && request.max_completion_tokens !== undefined) fail('Choose one output token limit.', 'invalid_request');
  const maxTokens = request.max_tokens ?? request.max_completion_tokens;
  if (maxTokens !== undefined) {
    if (!Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > 65536) fail('Output token limit must be within 1–65536.', 'invalid_request');
    options.maxTokens = maxTokens;
  }
  if (request.stop !== undefined) {
    const stop = typeof request.stop === 'string' ? [request.stop] : request.stop;
    if (!Array.isArray(stop) || stop.length > 4 || stop.some(value => !text(value))) fail('stop must contain up to four nonempty strings.', 'invalid_request');
    options.stop = stop;
  }
  if (request.tools !== undefined) {
    if (!Array.isArray(request.tools) || request.tools.length > 64) fail('tools must be an array with at most 64 entries.', 'invalid_request');
    options.tools = request.tools.map(tool => {
      keys(tool, ['type', 'function'], 'tool'); keys(tool.function, ['name', 'description', 'parameters'], 'tool function');
      if (tool.type !== 'function' || !text(tool.function.name) || !object(tool.function.parameters)
        || tool.function.description !== undefined && typeof tool.function.description !== 'string') fail('Invalid tool schema.', 'invalid_request');
      return { name: tool.function.name, description: tool.function.description || '', parameters: tool.function.parameters };
    });
  }
  if (request.tool_choice === 'none') {
    if (options.tools?.length && request.messages.some(row => row.role === 'assistant' && row.tool_calls?.length))
      fail('DSH cannot force tool_choice none while retaining historical tool declarations.', 'unsupported_tool_choice');
    delete options.tools;
  }
  else if (request.tool_choice !== undefined && request.tool_choice !== 'auto') fail('DSH adapter does not expose forced tool_choice.');
  let effort = request.reasoning_effort;
  if (request.reasoning !== undefined) {
    keys(request.reasoning, ['enabled'], 'reasoning');
    if (request.reasoning.enabled !== false) fail('Only an explicit disabled reasoning request is supported.');
    if (effort !== undefined && effort !== 'off') fail('Conflicting reasoning controls.', 'invalid_request');
    effort = 'off';
  }
  if (effort !== undefined) {
    if (!text(effort) || typeof llm.resolveModelInfo !== 'function') fail('DSH model does not expose this reasoning effort.');
    const info = await llm.resolveModelInfo(route.provider, route.model, signal);
    if (!info.reasoning?.efforts?.some(item => item.id === effort)) fail('DSH model does not expose this reasoning effort.');
    options.reasoningEffort = effort;
  }
  return { options, modelId: request.model, ...route };
}

function usageFor(value) {
  const valid = number => typeof number === 'number' && Number.isFinite(number) && number >= 0;
  const count = name => valid(value[name]) ? value[name] : undefined;
  const input = count('inputTokens'), output = count('outputTokens');
  const cacheRead = count('cacheReadTokens'), cacheWrite = count('cacheWriteTokens');
  const prompt = input === undefined ? undefined : input + (cacheRead ?? 0) + (cacheWrite ?? 0);
  const total = count('totalTokens') ?? (prompt !== undefined && output !== undefined ? prompt + output : undefined);
  const reasoning = count('reasoningTokens');
  // DSH's pi-ai adapter folds reasoning into outputTokens and omits the split.
  // Missing reasoning accounting must remain missing, rather than becoming zero.
  return { ...(prompt === undefined ? {} : { prompt_tokens: prompt }),
    ...(output === undefined ? {} : { completion_tokens: output }),
    ...(total === undefined ? {} : { total_tokens: total }),
    ...(cacheRead === undefined && cacheWrite === undefined ? {} : {
      prompt_tokens_details: { ...(cacheRead === undefined ? {} : { cached_tokens: cacheRead }),
        ...(cacheWrite === undefined ? {} : { cache_write_tokens: cacheWrite }) } }),
    ...(reasoning === undefined ? {} : { completion_tokens_details: { reasoning_tokens: reasoning } }) };
}

/** One DSH adapter attempt, with no Agent tools, context injection or retry loop. */
export async function* completionChunks(llm, request, { signal, prepared } = {}) {
  if (signal?.aborted) fail('DSH request cancelled.', 'ABORTED', 499);
  prepared ||= await validateCompletionRequest(llm, request, { signal });
  const id = `chatcmpl-${randomUUID()}`, created = Math.floor(Date.now() / 1000);
  const chunk = (delta, finish = null) => ({ id, object: 'chat.completion.chunk', created, model: prepared.modelId,
    choices: [{ index: 0, delta, finish_reason: finish }] });
  yield chunk({ role: 'assistant' });
  let usage, ended = false;
  const tools = new Map(), deltas = new Set();
  function tool(index, value, complete = false) {
    let entry = tools.get(index);
    const first = !entry;
    if (first) { entry = { index: tools.size, arguments: '' }; tools.set(index, entry); }
    if (value.id && entry.id && value.id !== entry.id || value.name && entry.name && value.name !== entry.name)
      fail('DSH tool identity changed within one block.', 'invalid_stream', 502);
    const newId = value.id && !entry.id, newName = value.name && !entry.name;
    if (newId) entry.id = value.id;
    if (newName) entry.name = value.name;
    let argumentsDelta = value.argumentsDelta || '';
    if (complete) {
      if (typeof value.arguments !== 'string' || !value.arguments.startsWith(entry.arguments))
        fail('DSH completed tool arguments disagree with the streamed arguments.', 'invalid_stream', 502);
      argumentsDelta = value.arguments.slice(entry.arguments.length);
    }
    entry.arguments += argumentsDelta;
    if (!first && !newId && !newName && !argumentsDelta) return null;
    return { index: entry.index, ...(first ? { type: 'function' } : {}), ...(newId ? { id: entry.id } : {}), function: {
      ...(newName ? { name: value.name } : {}), arguments: argumentsDelta,
    } };
  }

  for await (const value of llm.stream({ ...prepared.options, signal: signal || prepared.options.signal })) {
    if (signal?.aborted) fail('DSH request cancelled.', 'ABORTED', 499);
    if (value.type === 'text-delta' || value.type === 'reasoning-delta') {
      deltas.add(value.index); yield chunk({ [value.type === 'text-delta' ? 'content' : 'reasoning_content']: value.text });
    } else if (value.type === 'tool-call-delta') {
      deltas.add(value.index);
      const update = tool(value.index, value);
      if (update) yield chunk({ tool_calls: [update] });
    } else if (value.type === 'block-end') {
      if (value.block.type === 'tool-call') {
        const update = tool(value.index, value.block, true);
        if (update) yield chunk({ tool_calls: [update] });
      }
      else if (!deltas.has(value.index)) {
        if (value.block.type === 'text') yield chunk({ content: value.block.text });
        else if (value.block.type === 'reasoning') yield chunk({ reasoning_content: value.block.text });
        else fail('DSH returned unsupported content.', 'unsupported_content', 502);
      }
    } else if (value.type === 'usage') usage = usageFor(value.usage);
    else if (value.type === 'finish') {
      const reason = value.reason?.kind;
      if (reason === 'error' || reason === 'aborted') upstreamFailure(reason === 'aborted' ? 'ABORTED' : value.reason.failure?.code);
      const finish = { stop: 'stop', 'tool-calls': 'tool_calls', 'max-tokens': 'length' }[reason];
      if (!finish) fail('DSH returned an unknown finish reason.', 'invalid_stream', 502);
      yield chunk({}, finish); ended = true; break;
    } else if (value.type !== 'block-start') fail('DSH returned an unknown stream event.', 'invalid_stream', 502);
  }
  if (!ended) fail('DSH stream ended without a terminal result.', 'incomplete_stream', 502);
  if (usage && (request.stream !== true || request.stream_options?.include_usage || request.usage?.include === true)) {
    yield { id, object: 'chat.completion.chunk', created, model: prepared.modelId, choices: [], usage };
  }
}
