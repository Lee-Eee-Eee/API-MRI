import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';

const html = await readFile(new URL('../../src/relay_audit/static/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('../../src/relay_audit/static/app.js', import.meta.url), 'utf8');
const origin = 'dsh-app://app', nonce = 'workbench-test-nonce-12345';
const base = 'http://127.0.0.1:9876/v1';
const models = [{ id: 'configured:model-one', label: 'Model One · Provider A', provider: 'Provider A', model: 'model-one' },
  { id: 'configured:model-two', label: 'Model Two · Provider B', provider: 'Provider B', model: 'model-two' }];
const settle = () => new Promise(resolve => setImmediate(resolve));

// Execute the shipped app and its actual event handlers. This minimal DOM owns
// form validation, selection, bubbling and disabled state; it never opens HTTP.
async function workbench({ planRequests = 2, planTokens = 256 } = {}) {
  const nodes = new Map(), messages = [], requests = [], timers = new Map();
  let timerId = 0;
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.listeners = new Map();
      this.value = ''; this.disabled = false; this.hidden = false; this.checked = false;
      this.required = false; this.style = {}; this.dataset = {}; this.attrs = new Map();
      const classes = new Set();
      this.classList = { add: (...v) => v.forEach(x => classes.add(x)), remove: (...v) => v.forEach(x => classes.delete(x)),
        contains: v => classes.has(v), toggle: (v, on) => { const yes = on ?? !classes.has(v); if (yes) classes.add(v); else classes.delete(v); return yes; } };
    }
    addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(fn); }
    async fire(type, options = {}) {
      const event = { type, target: this, preventDefault() {}, ...options };
      for (const fn of this.listeners.get(type) || []) await fn(event);
      if (['input', 'change'].includes(type) && this.form && this.form !== this) await this.form.fire(type, event);
    }
    dispatchEvent(event) { for (const fn of this.listeners.get(event.type) || []) fn(event); return true; }
    append(...items) { for (const item of items) { this.children.push(item); if (item && typeof item === 'object') item.parentElement = this; }
      if (this.tagName === 'SELECT' && !this.children.some(x => x.value === this.value)) this.value = this.children[0]?.value || ''; }
    appendChild(item) { this.append(item); return item; }
    replaceChildren(...items) { this.children = []; this.value = this.tagName === 'SELECT' ? '' : this.value; this.append(...items); }
    setAttribute(name, value) { this.attrs.set(name, String(value)); }
    removeAttribute(name) { this.attrs.delete(name); }
    getAttribute(name) { return this.attrs.get(name) ?? null; }
    querySelectorAll() { return []; }
    querySelector() { return new Element(); }
    reportValidity() { return this.disabled || !this.required || Boolean(this.value); }
    focus() { document.activeElement = this; }
    contains(other) { return other === this || this.children.includes(other); }
    scrollIntoView() {}
    remove() {}
  }
  for (const match of html.matchAll(/<([a-z0-9-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
    const item = new Element(match[1], match[3]), attrs = match[2];
    item.value = /\bvalue="([^"]*)"/.exec(attrs)?.[1] || '';
    item.required = /\brequired\b/.test(attrs); item.hidden = /\bhidden\b/.test(attrs);
    item.disabled = /\bdisabled\b/.test(attrs); item.checked = /\bchecked\b/.test(attrs);
    nodes.set(item.id, item);
  }
  for (const match of html.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/gi)) {
    const select = nodes.get(match[1]);
    for (const entry of match[2].matchAll(/<option\b([^>]*)>([^<]*)<\/option>/gi)) {
      const option = new Element('option'); option.textContent = entry[2];
      option.value = /value="([^"]*)"/.exec(entry[1])?.[1] ?? entry[2]; select.append(option);
      if (/\bselected\b/.test(entry[1])) select.value = option.value;
    }
  }
  for (const match of html.matchAll(/<form\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/form>/gi)) {
    const form = nodes.get(match[1]);
    for (const id of match[2].matchAll(/\bid="([^"]+)"/g)) if (nodes.has(id[1])) nodes.get(id[1]).form = form;
  }
  const document = new Element('document');
  document.activeElement = null;
  document.getElementById = id => { assert.ok(nodes.has(id), `unknown DOM id: ${id}`); return nodes.get(id); };
  document.createElement = tag => new Element(tag);
  document.createElementNS = (_, tag) => new Element(tag);
  document.createTextNode = text => ({ textContent: text });
  const parent = { postMessage: (value, targetOrigin) => messages.push({ value, targetOrigin }) };
  const window = new Element('window'); window.parent = parent;
  const fetch = async (path, options = {}) => {
    const body = options.body && JSON.parse(options.body); requests.push({ path, body });
    let value;
    if (path === '/api/bootstrap') value = { session_token: 'local-test-token', version: 'test',
      capabilities: { research_extensions: false }, embedding: { origins: [origin] },
      identification_bank: { models: [], model_count: 0 }, reference_packs: [] };
    else if (path === '/api/runs' && !body) value = { runs: [] };
    else if (path === '/api/reference-library/update') value = { status: 'idle' };
    else if (path === '/api/plan') value = { maximum_requests: planRequests, maximum_requested_output_tokens: planTokens,
      max_requests: planRequests, max_output_tokens_total: planTokens, budget: { max_requests: planRequests, max_output_tokens: planTokens } };
    else if (path === '/api/runs') value = { id: 'accepted-run', mode: body.mode, status: 'queued', reports: [] };
    else throw new Error(`unexpected HTTP: ${path}`);
    return { ok: true, json: async () => value };
  };
  const sandbox = { window, document, fetch, URL, Intl, crypto: webcrypto, console,
    location: { hash: '#quality', origin: 'http://127.0.0.1:8765' },
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    Option: function(text, value) { const item = new Element('option'); item.textContent = text; item.value = value; return item; },
  };
  vm.runInNewContext(source, sandbox, { filename: 'app.js' });
  await settle(); assert.equal(window.apiMRI.ready(), true, nodes.get('connection-error-text').textContent);
  const message = (value, overrides = {}) => window.fire('message', { source: parent, origin, data: value, ...overrides });
  const hello = () => message({ type: 'api-mri:hello', nonce });
  const catalog = (extra = {}, overrides) => message({ type: 'api-mri:dsh:catalog', nonce, apiBase: base, models, ...extra }, overrides);
  return { window, nodes, messages, requests, timers, message, hello, catalog, api: window.apiMRI };
}

test('workbench accepts only its authorized parent handshake and matching catalog nonce/origin', async () => {
  const app = await workbench();
  await app.catalog(); assert.equal(app.api.dsh().available, false);
  await app.hello();
  await app.catalog({}, { origin: 'https://evil.test' });
  await app.catalog({ nonce: 'wrong-nonce-value-123456' });
  await app.catalog({}, { source: {} });
  await app.catalog({ apiBase: 'http://example.com/v1' });
  assert.equal(app.api.dsh().available, false);
  await app.catalog();
  assert.equal(app.api.usesDSH(), true); assert.equal(app.api.usesDSH('showcase'), true);
  assert.equal(app.nodes.get('api-manual-key').hidden, true);
  assert.equal(app.nodes.get('api-base').disabled, true);
  assert.equal(app.nodes.get('api-model').required, false);
  assert.equal(app.nodes.get('api-dsh-model').children[0].textContent, models[0].label);
});

test('empty/refreshed DSH catalog preserves source and has a required empty selection', async () => {
  const app = await workbench(); await app.hello(); await app.catalog({ models: [] });
  assert.equal(app.api.usesDSH(), true);
  assert.equal(app.nodes.get('api-dsh-model').reportValidity(), false);
  assert.match(app.nodes.get('api-dsh-note').textContent, /配置模型/);
  await app.nodes.get('api-dsh-refresh').fire('click');
  assert.equal(app.messages.at(-1).value.type, 'api-mri:dsh:refresh');
  await app.catalog(); app.nodes.get('api-dsh-model').value = models[1].id;
  await app.catalog(); assert.equal(app.nodes.get('api-dsh-model').value, models[1].id);
});

test('model/source changes invalidate a displayed plan; direct mode restores manual validation', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  await app.nodes.get('check-form').fire('submit'); await settle(); assert.equal(app.nodes.get('start-button').hidden, false);
  app.nodes.get('api-dsh-model').value = models[1].id;
  await app.nodes.get('api-dsh-model').fire('change'); assert.equal(app.nodes.get('start-button').hidden, true);
  await app.nodes.get('check-form').fire('submit'); await settle(); assert.equal(app.nodes.get('start-button').hidden, false);
  app.nodes.get('api-source').value = 'manual'; await app.nodes.get('api-source').fire('change');
  assert.equal(app.nodes.get('start-button').hidden, true);
  assert.equal(app.nodes.get('api-base').disabled, false); assert.equal(app.nodes.get('api-base').required, true);
  assert.equal(app.nodes.get('api-manual-key').hidden, false);
});

test('run lease is bounded by the actual plan, ignores forged replies and submits only matching capability', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  const pending = app.api.request('/api/runs', { mode: 'service', candidate: app.api.candidate() });
  await settle();
  const request = app.messages.at(-1).value;
  assert.equal(request.type, 'api-mri:dsh:lease');
  assert.equal(request.maxRequests, 2); assert.equal(request.maxOutputTokens, 256);
  const reply = { type: 'api-mri:dsh:lease-result', nonce, requestId: request.requestId,
    candidate: { api_base: base, model: models[0].id, api_key: 'temporary-bridge-capability' } };
  await app.message({ ...reply, nonce: 'forged-other-nonce' });
  assert.equal(app.requests.filter(r => r.path === '/api/runs' && r.body).length, 0);
  await app.message(reply); await pending;
  const sent = app.requests.find(r => r.path === '/api/runs' && r.body).body;
  assert.equal(sent.candidate.api_key, 'temporary-bridge-capability');
  assert.equal(sent.candidate.model, models[0].id);
  assert.equal(app.api.dsh().models[0].api_key, undefined);
});

test('oversized plans never ask for a lease or call a model', async () => {
  for (const limits of [{ planRequests: 129 }, { planTokens: 65537 }]) {
    const app = await workbench(limits); await app.hello(); await app.catalog();
    await assert.rejects(app.api.request('/api/runs', { mode: 'service', candidate: app.api.candidate() }), /128/);
    assert.equal(app.messages.some(m => m.value.type === 'api-mri:dsh:lease'), false);
    assert.equal(app.requests.some(r => r.path === '/api/runs' && r.body), false);
  }
});

test('every core mode and showcase authorize its planned maxima without retaining the lease token', async () => {
  for (const mode of ['service', 'identify', 'fingerprint', 'showcase']) {
    const app = await workbench({ planRequests: 4, planTokens: 4096 }); await app.hello(); await app.catalog();
    const candidate = app.api.candidate(false, mode === 'showcase' ? 'showcase' : 'main');
    const pending = app.api.request('/api/runs', { mode, candidate }); await settle();
    const request = app.messages.at(-1).value;
    assert.equal(request.maxRequests, 4); assert.equal(request.maxOutputTokens, 4096);
    await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: request.requestId,
      candidate: { ...candidate, api_key: 'temporary-bridge-capability' } });
    await pending;
    assert.equal(candidate.api_key, undefined);
    assert.equal(app.api.candidate(false, mode === 'showcase' ? 'showcase' : 'main').api_key, undefined);
  }
});

test('lease timeout fails before run submission', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  const pending = app.api.request('/api/runs', { mode: 'service', candidate: app.api.candidate() });
  const rejection = assert.rejects(pending, /授权超时/); await settle();
  [...app.timers.values()].at(-1)(); await rejection;
  assert.equal(app.requests.some(r => r.path === '/api/runs' && r.body), false);
});

test('mismatched lease model is rejected and handshake changes revoke pending authorization', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  let pending = app.api.request('/api/runs', { mode: 'showcase', candidate: app.api.candidate(false, 'showcase') });
  let rejection = assert.rejects(pending, /不匹配/); await settle();
  let request = app.messages.at(-1).value;
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: request.requestId,
    candidate: { api_base: base, model: models[1].id, api_key: 'temporary-bridge-capability' } });
  await rejection;
  pending = app.api.request('/api/runs', { mode: 'service', candidate: app.api.candidate() });
  rejection = assert.rejects(pending, /连接已更新/); await settle();
  await app.message({ type: 'api-mri:hello', nonce: 'a-new-valid-handshake-12345' }); await rejection;
  assert.equal(app.api.dsh().available, false);
});

test('selectors lock while a run is awaiting DSH authorization and unlock on an authorization error', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  const pending = app.api.submit({ mode: 'service', candidate: app.api.candidate() }); await settle();
  assert.equal(app.nodes.get('api-source').disabled, true);
  assert.equal(app.nodes.get('api-dsh-model').disabled, true);
  assert.equal(app.nodes.get('showcase-source').disabled, true);
  const request = app.messages.at(-1).value;
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: request.requestId, error: '测试授权失败' });
  await pending;
  assert.equal(app.nodes.get('api-source').disabled, false);
  assert.equal(app.nodes.get('api-dsh-model').disabled, false);
});

test('selectors remain locked after the authorized run has entered the queue', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  const pending = app.api.submit({ mode: 'service', candidate: app.api.candidate() }); await settle();
  const request = app.messages.at(-1).value;
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: request.requestId,
    candidate: { api_base: base, model: models[0].id, api_key: 'temporary-bridge-capability' } });
  const accepted = await pending; assert.equal(accepted.id, 'accepted-run');
  assert.equal(app.nodes.get('api-source').disabled, true);
  assert.equal(app.nodes.get('api-dsh-model').disabled, true);
  assert.equal(app.nodes.get('showcase-source').disabled, true);
});
