import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import { listConfiguredModels, validateCompletionRequest, completionChunks, publicCompletionError } from './dsh-adapter.js';

export const BRIDGE_LIMITS = Object.freeze({ maxRequests: 128, maxOutputTokens: 65536, lifetimeMs: 2 * 60 * 60 * 1000 });
const MAX_BODY = 512 * 1024;
class BridgeError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; this.safeForClient = true; }
}
function fault(status, code, message) { return new BridgeError(status, code, message); }

export async function readJson(req, maximum = MAX_BODY) {
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maximum) throw fault(413, 'request_too_large', '请求内容过大。');
    chunks.push(Buffer.from(chunk));
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw fault(400, 'invalid_json', '需要 JSON 请求。'); }
}

function aggregate(chunks, model) {
  const tools = new Map(); let content = '', reasoning = '', finish = 'stop', usage;
  let id = 'dsh-' + randomBytes(12).toString('hex');
  for (const chunk of chunks) {
    if (chunk.id) id = chunk.id;
    if (chunk.usage) usage = chunk.usage;
    for (const choice of chunk.choices || []) {
      if (choice.finish_reason) finish = choice.finish_reason;
      const delta = choice.delta || {};
      content += delta.content || ''; reasoning += delta.reasoning_content || '';
      for (const tool of delta.tool_calls || []) {
        const current = tools.get(tool.index) || { id: '', type: 'function', function: { name: '', arguments: '' } };
        if (tool.id) current.id = tool.id;
        if (tool.function?.name) current.function.name += tool.function.name;
        current.function.arguments += tool.function?.arguments || '';
        tools.set(tool.index, current);
      }
    }
  }
  const message = { role: 'assistant', content: content || (tools.size ? null : '') };
  if (reasoning) message.reasoning_content = reasoning;
  if (tools.size) message.tool_calls = [...tools.values()];
  return { id, object: 'chat.completion', created: Math.floor(Date.now() / 1000), model,
    choices: [{ index: 0, message, finish_reason: finish }], ...(usage ? { usage } : {}) };
}

/** A per-plugin, loopback-only adapter. Upstream credentials never leave ctx.llm. */
export function createModelBridge(llm, { now = Date.now, lifetimeMs = BRIDGE_LIMITS.lifetimeMs } = {}) {
  const leases = new Map(); let server, starting, apiBase, closed = false;
  async function catalog() { return listConfiguredModels(llm); }
  function prune() { for (const [key, lease] of leases) if (lease.expires <= now()) leases.delete(key); }
  async function address() {
    if (closed) throw fault(503, 'bridge_closed', '插件已关闭。');
    if (!starting) starting = new Promise((resolve, reject) => {
      server = createServer((req, res) => { void handle(req, res); });
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => {
        apiBase = `http://127.0.0.1:${server.address().port}/v1`;
        server.unref(); resolve(apiBase);
      });
    });
    return starting;
  }
  async function issue({ model, maxRequests, maxOutputTokens }) {
    if (!Number.isSafeInteger(maxRequests) || maxRequests < 1 || maxRequests > BRIDGE_LIMITS.maxRequests
        || !Number.isSafeInteger(maxOutputTokens) || maxOutputTokens < 1 || maxOutputTokens > BRIDGE_LIMITS.maxOutputTokens)
      throw fault(422, 'invalid_budget', 'DSH 调用预算须在 128 次请求、65536 输出配额以内。');
    if (!(await catalog()).some(row => row.id === model)) throw fault(422, 'model_unavailable', '这个 DSH 模型已不可用，请刷新目录。');
    prune(); if (leases.size >= 32) throw fault(429, 'too_many_leases', '调用授权过多，请稍后重试。');
    const base = await address(), token = randomBytes(32).toString('hex');
    leases.set(token, { model, maxRequests, maxOutputTokens, requests: 0, reserved: 0, busy: false, expires: now() + lifetimeMs });
    return { candidate: { api_base: base, model, api_key: token }, expiresAt: new Date(now() + lifetimeMs).toISOString(),
      transport: 'dsh-adapter', maxRequests, maxOutputTokens };
  }
  async function handle(req, res) {
    const send = (status, value) => {
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-API-MRI-Transport': 'dsh-adapter' });
      res.end(JSON.stringify(value));
    };
    let lease, controller, acquired = false;
    try {
      const expectedHost = new URL(apiBase).host;
      if (closed || req.socket.remoteAddress !== '127.0.0.1' || req.headers.host !== expectedHost || req.headers.origin)
        throw fault(403, 'forbidden', '只接受检测器的本地调用。');
      if (req.url !== '/v1/chat/completions' || req.method !== 'POST') throw fault(404, 'not_found', '没有这个接口。');
      prune(); const match = /^Bearer ([a-f0-9]{64})$/.exec(req.headers.authorization || '');
      lease = match && leases.get(match[1]);
      if (!lease) throw fault(401, 'authorization_expired', 'DSH 调用授权已失效，请重新开始检测。');
      if (lease.busy) throw fault(409, 'concurrent_request', 'DSH 检测按顺序执行请求。');
      const body = await readJson(req);
      if (lease.busy) throw fault(409, 'concurrent_request', 'DSH 检测按顺序执行请求。');
      if (body.model !== lease.model) throw fault(403, 'model_mismatch', '调用授权与所选模型不一致。');
      const quota = body.max_completion_tokens ?? body.max_tokens;
      if (!Number.isSafeInteger(quota) || quota < 1 || quota > BRIDGE_LIMITS.maxOutputTokens)
        throw fault(422, 'missing_output_limit', '每个请求必须指定输出配额。');
      if (lease.requests >= lease.maxRequests || lease.reserved + quota > lease.maxOutputTokens)
        throw fault(429, 'budget_exhausted', '已达到本次 DSH 检测预算。');
      lease.busy = true; acquired = true; lease.requests++; lease.reserved += quota;
      controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 300000); timer.unref();
      res.once('close', () => { clearTimeout(timer); controller.abort(); });
      const prepared = await validateCompletionRequest(llm, body, { signal: controller.signal });
      res.setHeader('Cache-Control', 'no-store'); res.setHeader('X-API-MRI-Transport', 'dsh-adapter');
      if (body.stream) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        for await (const chunk of completionChunks(llm, body, { signal: controller.signal, prepared })) {
          if (controller.signal.aborted) break;
          res.write(`data: ${JSON.stringify(chunk)}\n\n`);
        }
        if (!controller.signal.aborted) res.end('data: [DONE]\n\n');
      } else {
        const chunks = [];
        for await (const chunk of completionChunks(llm, body, { signal: controller.signal, prepared })) {
          if (controller.signal.aborted) break;
          chunks.push(chunk);
        }
        if (!controller.signal.aborted) send(200, aggregate(chunks, body.model));
      }
    } catch (error) {
      const safe = error instanceof BridgeError ? error : publicCompletionError(error);
      const value = { error: { code: safe.code, message: safe.message } };
      if (!res.headersSent) send(safe.status, value);
      else if (!res.destroyed && !controller?.signal.aborted) {
        // A terminal error frame retains the cause without pretending a successful
        // [DONE]. Raw DSH/provider messages never reach this branch.
        res.end(`data: ${JSON.stringify(value)}\n\n`);
      } else res.destroy();
    } finally { if (acquired) lease.busy = false; }
  }
  async function dispose() {
    closed = true; leases.clear();
    if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  }
  return { catalog, address, issue, dispose };
}
