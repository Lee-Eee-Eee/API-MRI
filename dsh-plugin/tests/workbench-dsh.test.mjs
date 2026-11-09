import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';

const html = await readFile(new URL('../../src/relay_audit/static/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('../../src/relay_audit/static/app.js', import.meta.url), 'utf8');
const origin = 'dsh-app://app', nonce = 'workbench-test-nonce-12345';
const base = 'http://127.0.0.1:9876/v1';
const models = [{ id: 'configured:model-one', label: 'Model One · Provider A', provider: 'Provider A', model: 'model-one', fingerprint_supported: true },
  { id: 'configured:model-two', label: 'Model Two · Provider B', provider: 'Provider B', model: 'model-two' }];
const settle = () => new Promise(resolve => setImmediate(resolve));

// Execute the shipped app and its actual event handlers. This minimal DOM owns
// form validation, selection, bubbling and disabled state; it never opens HTTP.
async function workbench({ planRequests = 2, planTokens = 256, library = null, completedRun = null,
  completedRunStatus = 'completed', resumeRequests = 34, resumeTokens = 544, reusedRequests = 62, recomparisonReport = null } = {}) {
  const nodes = new Map(), messages = [], requests = [], timers = new Map();
  let timerId = 0;
  const banks = library?.banks || [{ id: 'legacy-test-bank', label: 'Historical test reference', model_count: 2, cell_count: 40,
    collected_at: { first: '2026-07-06', last: '2026-07-08' }, models: [{ model: 'model-one', label: 'Model One' }] }];
  let activeBank = library?.active_bank_id || banks[0].id;
  let sync = library?.sync || { due: false }, submittedMode = null;
  const published = [];
  const librarySnapshot = () => ({ banks, active_bank_id: activeBank, selected_bank_id: activeBank, bank: banks.find(b => b.id === activeBank), sync, published_run_ids: published });
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
      identification_bank: librarySnapshot().bank, reference_packs: [] };
    else if (path === '/api/identification-library') value = librarySnapshot();
    else if (path.startsWith('/api/identification-library?')) {
      const query = new URL(path, 'http://127.0.0.1').searchParams, model = query.get('model'), bank = banks.find(b => b.id === query.get('bank_id'));
      const exact = bank?.models.find(m => m.model === model), aliased = bank?.models.find(m => m.aliases?.includes(model));
      value = { ...librarySnapshot(), coverage: { claimed_model: model, bank_id: bank?.id,
        match: exact ? 'exact' : aliased ? 'unconfirmed_alias' : 'missing', ...(aliased ? { model_id: aliased.model } : {}),
        reference_cells: 8, min_repetitions: 8, collected_at: bank?.collected_at } };
    }
    else if (path === '/api/identification-library/activate') { activeBank = body.bank_id; value = librarySnapshot(); }
    else if (path === '/api/identification-library/sync') {
      sync = { status: 'completed', due: false, last_checked: '2026-11-09T09:00:00Z', added: 0, model_api_calls: 0 }; value = librarySnapshot();
    }
    else if (path === '/api/identification-library/publish') {
      published.push(body.run_id); activeBank = banks.at(-1).id; value = { bank_id: activeBank, bank: banks.at(-1), library: librarySnapshot() };
    }
    else if (path === '/api/identification-library/recompare') value = { id: 'recompared-run', mode: 'identify_offline', status: 'queued', reports: [] };
    else if (path === '/api/runs/recompared-run' && recomparisonReport) value = { id: 'recompared-run', mode: 'identify_offline',
      status: 'completed', created_at: '2026-11-09T09:00:00Z', reports: [recomparisonReport] };
    else if (path === '/api/runs' && !body) value = { runs: [] };
    else if (path === '/api/reference-library/update') value = { status: 'idle' };
    else if (path === '/api/plan') {
      const count = body.resume_run_id ? resumeRequests : planRequests, tokens = body.resume_run_id ? resumeTokens : planTokens;
      value = { maximum_requests: count, maximum_output_tokens: tokens, maximum_requested_output_tokens: tokens,
        reused_requests: body.resume_run_id ? reusedRequests : 0,
        max_requests: count, max_output_tokens_total: tokens, budget: { max_requests: count, max_output_tokens: tokens } };
    }
    else if (path === '/api/runs') { submittedMode = body.mode; value = { id: 'accepted-run', mode: body.mode, status: 'queued', reports: [] }; }
    else if (path === '/api/runs/accepted-run' && completedRun) value = { id: 'accepted-run', mode: submittedMode,
      status: completedRunStatus, created_at: '2026-11-09T09:00:00Z', reports: [completedRun] };
    else throw new Error(`unexpected HTTP: ${path}`);
    return { ok: true, json: async () => value };
  };
  const sandbox = { window, document, fetch, URL, URLSearchParams, Intl, crypto: webcrypto, console,
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
  const navigate = async view => { sandbox.location.hash = '#' + view; await window.fire('hashchange'); await settle(); };
  return { window, nodes, messages, requests, timers, message, hello, catalog, navigate, api: window.apiMRI };
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
  for (const mode of ['service', 'identify', 'fingerprint', 'showcase', 'reference_collect']) {
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

test('DSH defaults leave room for tool JSON and preserve the advertised reasoning control', async () => {
  const app = await workbench(); await app.hello(); await app.catalog();
  assert.equal(app.nodes.get('output-tokens').value, '256');
  assert.equal(app.nodes.get('token-cap').value, '1536');
  assert.equal(app.nodes.get('timeout').value, '60');
  assert.equal(app.api.dsh().models[0].fingerprint_supported, true);
  await app.nodes.get('check-form').fire('submit');
  const plan = app.requests.filter(r => r.path === '/api/plan').at(-1).body;
  assert.equal(plan.reasoning_off, true);
  assert.equal(plan.tool_roundtrip_choice, 'auto');
});

test('unconfirmed reasoning-off support blocks shared probes before requesting an authorization', async () => {
  for (const supported of [null, false]) {
    const app = await workbench(); await app.hello();
    await app.catalog({ models: [{ ...models[0], fingerprint_supported: supported }] });
    await assert.rejects(app.api.request('/api/runs', { mode: 'identify', candidate: app.api.candidate() }), /未声明可关闭推理/);
    assert.equal(app.messages.some(message => message.value.type === 'api-mri:dsh:lease'), false);
    assert.equal(app.requests.some(request => request.path === '/api/runs' && request.body), false);
  }
});

test('coverage is checked before a run and never changes the candidate ranking payload', async () => {
  const app = await workbench(); await app.hello(); await app.catalog(); await app.navigate('identity');
  assert.match(app.nodes.get('coverage-title').textContent, /有同名参考/);
  assert.match(app.nodes.get('coverage-description').textContent, /2026-07-06/);
  app.nodes.get('api-dsh-model').value = models[1].id;
  await app.nodes.get('api-dsh-model').fire('change'); await settle();
  assert.match(app.nodes.get('coverage-title').textContent, /当前库未收录/);
  const query = app.requests.filter(r => r.path.startsWith('/api/identification-library?')).at(-1);
  assert.equal(new URL(query.path, 'http://127.0.0.1').searchParams.get('model'), models[1].model);
  assert.equal(query.body, undefined);
  assert.equal(app.messages.some(m => m.value.type === 'api-mri:dsh:lease'), false);
  assert.equal(app.requests.some(r => r.path === '/api/runs' && r.body), false);
  // Missing coverage is metadata, not a filter that can hide older candidates.
  await app.catalog(); app.nodes.get('api-dsh-model').value = models[0].id;
  await app.nodes.get('api-dsh-model').fire('change');
  await app.nodes.get('check-form').fire('submit');
  const plan = app.requests.filter(r => r.path === '/api/plan').at(-1).body;
  assert.equal(plan.bank_id, 'legacy-test-bank');
  assert.equal(plan.candidate_models, undefined);
  assert.equal(plan.candidate.model, models[0].id);
});

test('selecting a reference snapshot is free, updates the bank id and invalidates the displayed plan', async () => {
  const banks = [{ id: 'pamela-test', label: 'Historical', model_count: 2, models: [], collected_at: '2026-07-08' },
    { id: 'rolling-current', label: 'Recent', model_count: 2, models: [], collected_at: '2026-11-09' }];
  const app = await workbench({ library: { banks, active_bank_id: banks[0].id } });
  await app.hello(); await app.catalog(); await app.navigate('identity');
  await app.nodes.get('check-form').fire('submit'); await settle();
  assert.equal(app.nodes.get('start-button').hidden, false, app.nodes.get('form-error').textContent);
  app.nodes.get('identification-bank').value = banks[1].id;
  await app.nodes.get('identification-bank').fire('change'); await settle();
  assert.equal(app.nodes.get('start-button').hidden, true);
  assert.equal(app.nodes.get('identification-bank').value, banks[1].id);
  assert.equal(app.nodes.get('stable-stop-field').hidden, true);
  await app.nodes.get('check-form').fire('submit');
  const plan = app.requests.filter(r => r.path === '/api/plan').at(-1).body;
  assert.equal(plan.bank_id, banks[1].id); assert.equal(plan.stop_policy, 'fixed');
  assert.equal(app.messages.some(m => m.value.type === 'api-mri:dsh:lease'), false);
});

test('a fresh channel snapshot is visible even when a floating model alias is not version authenticated', async () => {
  const bank = { id: 'rolling-current', label: 'Recent', model_count: 2, collected_at: '2026-11-09',
    models: [{ model: 'service/provider/model-one', label: 'Model One', aliases: ['model-one'] }] };
  const app = await workbench({ library: { banks: [bank], active_bank_id: bank.id } });
  await app.hello(); await app.catalog(); await app.navigate('identity');
  assert.match(app.nodes.get('coverage-title').textContent, /有通道参考/);
  assert.match(app.nodes.get('coverage-description').textContent, /2026-11-09/);
  assert.match(app.nodes.get('coverage-description').textContent, /固定模型版本未确认/);
  assert.doesNotMatch(app.nodes.get('coverage-title').textContent, /未收录/);
  assert.equal(app.requests.some(r => r.path === '/api/runs' && r.body), false);
});

test('reference collection needs an explicit start and obtains only its displayed 96 request lease', async () => {
  const app = await workbench({ planRequests: 96, planTokens: 1536 });
  await app.hello(); await app.catalog(); await app.navigate('identity');
  await app.nodes.get('identity-collect').fire('click');
  assert.equal(app.nodes.get('reference-collect-settings').hidden, false);
  assert.equal(app.nodes.get('empty-heading').textContent, '建立一份近期参考');
  assert.equal(app.nodes.get('empty-title').textContent, '补齐一个型号的近期参考');
  assert.match(app.nodes.get('scan-preview').innerHTML, /分开参考与留出/);
  assert.equal(app.nodes.get('candidate-settings').hidden, true);
  assert.equal(app.nodes.get('reference-source-alias').value, models[0].model);
  assert.equal(app.nodes.get('try-demo').hidden, true);
  await app.nodes.get('check-form').fire('submit'); await settle();
  const planned = app.requests.filter(r => r.path === '/api/plan').at(-1).body;
  assert.equal(planned.mode, 'reference_collect');
  assert.deepEqual(planned.budget, { max_requests: 96, max_output_tokens: 1536 });
  assert.equal(planned.source.kind, 'operator_declared');
  assert.equal(planned.source.provider, models[0].provider);
  assert.equal(app.messages.some(m => m.value.type === 'api-mri:dsh:lease'), false);
  const pending = app.nodes.get('start-button').fire('click'); await settle();
  const lease = app.messages.at(-1).value;
  assert.equal(lease.type, 'api-mri:dsh:lease', app.nodes.get('form-error').textContent); assert.equal(lease.maxRequests, 96); assert.equal(lease.maxOutputTokens, 1536);
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: lease.requestId,
    candidate: { api_base: base, model: models[0].id, api_key: 'temporary-reference-capability' } });
  await pending;
  const sent = app.requests.find(r => r.path === '/api/runs' && r.body).body;
  assert.equal(sent.mode, 'reference_collect'); assert.equal(sent.source.kind, 'operator_declared');
  assert.equal(app.requests.some(r => r.path === '/api/identification-library/publish'), false);
});

test('public fingerprint sync runs once when due and never starts model sampling', async () => {
  const app = await workbench({ library: { banks: [{ id: 'rolling', label: 'Recent', models: [], model_count: 0 }],
    active_bank_id: 'rolling', sync: { due: true } } });
  await settle();
  const automatic = app.requests.filter(r => r.path === '/api/identification-library/sync');
  assert.equal(automatic.length, 1); assert.deepEqual(automatic[0].body, { force: false });
  await app.nodes.get('refresh-identification-library').fire('click'); await settle();
  assert.equal(app.requests.filter(r => r.path === '/api/identification-library/sync').length, 1);
  await app.nodes.get('sync-identification-library').fire('click');
  assert.deepEqual(app.requests.filter(r => r.path === '/api/identification-library/sync').at(-1).body, { force: true });
  assert.equal(app.requests.some(r => r.path === '/api/runs' && r.body), false);
  assert.equal(app.messages.some(m => m.value.type === 'api-mri:dsh:lease'), false);
});

test('completed reference samples publish only after a separate explicit action', async () => {
  const banks = [{ id: 'pamela-test', label: 'Historical', models: [], model_count: 1 },
    { id: 'rolling-new', label: 'Recent', models: [], model_count: 2, collected_at: '2026-11-09' }];
  const completedRun = { name: 'reference', report: { kind: 'reference_collection', status: 'ready_to_publish',
    reference: { model: 'service/provider/model-one', label: 'Model One', provider: 'Provider A', cells: 8,
      reference_answers: 64, holdout_answers: 32, can_publish: true },
    collection: { failure: null }, cost: { request_count: 96, requested_output_tokens: 1536 } } };
  const app = await workbench({ planRequests: 96, planTokens: 1536, library: { banks, active_bank_id: banks[0].id }, completedRun });
  await app.hello(); await app.catalog(); await app.navigate('identity'); await app.nodes.get('identity-collect').fire('click');
  const pending = app.api.submit({ mode: 'reference_collect', candidate: app.api.candidate() }); await settle();
  const lease = app.messages.at(-1).value;
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: lease.requestId,
    candidate: { api_base: base, model: models[0].id, api_key: 'temporary-reference-capability' } });
  await pending;
  await [...app.timers.values()].find(fn => fn.name === 'pollRun')(); await settle();
  const walk = node => [node, ...(node.children || []).flatMap(walk)];
  const publish = walk(app.nodes.get('report-container')).find(node => node.tagName === 'BUTTON' && node.textContent === '发布并使用');
  assert.ok(publish, JSON.stringify({progress:app.nodes.get('progress-message').textContent, error:app.nodes.get('form-error').textContent,requests:app.requests.map(r=>r.path)}));
  assert.equal(publish.disabled, false);
  assert.equal(app.requests.some(r => r.path === '/api/identification-library/publish'), false);
  await publish.fire('click');
  const request = app.requests.find(r => r.path === '/api/identification-library/publish');
  assert.deepEqual(request.body, { run_id: 'accepted-run' });
  assert.equal(publish.disabled, true); assert.equal(publish.textContent, '已发布并使用');
  assert.equal(app.nodes.get('identification-bank').value, banks[1].id);
});

async function partialReferenceWorkbench() {
  const source = { label: 'Saved original label', model_alias: models[0].model, provider: models[0].provider, kind: 'first_party_route_claim', url: 'https://provider.example/models/model-one' };
  const partial = { name: 'reference', report: { kind: 'reference_collection', status: 'partial',
    source, candidate: { api_base: base, model: models[0].id },
    reference: { model: 'service/provider/model-one', label: source.label, provider: source.provider, cells: 5,
      reference_answers: 41, holdout_answers: 20, can_publish: false, can_resume: true },
    collection: { failure: 'invalid', completed_requests: 62 }, cost: { request_count: 62, requested_output_tokens: 992 } } };
  const app = await workbench({ planRequests: 96, planTokens: 1536, completedRun: partial, completedRunStatus: 'failed' });
  await app.hello(); await app.catalog(); await app.navigate('identity'); await app.nodes.get('identity-collect').fire('click');
  app.nodes.get('reference-source-label').value = source.label;
  app.nodes.get('reference-source-kind').value = source.kind;
  await app.nodes.get('check-form').fire('submit'); await settle();
  const pending = app.nodes.get('start-button').fire('click'); await settle();
  const lease = app.messages.at(-1).value;
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: lease.requestId,
    candidate: { api_base: base, model: models[0].id, api_key: 'temporary-reference-capability' } });
  await pending;
  await [...app.timers.values()].find(fn => fn.name === 'pollRun')(); await settle();
  const walk = node => [node, ...(node.children || []).flatMap(walk)];
  const resume = walk(app.nodes.get('report-container')).find(node => node.tagName === 'BUTTON' && node.textContent === '继续剩余采样');
  assert.ok(resume, app.nodes.get('progress-message').textContent);
  return { app, resume, source };
}

test('partial reference capture resumes its original source and leases only the missing requests', async () => {
  const { app, resume, source } = await partialReferenceWorkbench();
  const before = app.messages.filter(message => message.value.type === 'api-mri:dsh:lease').length;
  await resume.fire('click'); await settle();
  assert.equal(app.nodes.get('reference-resume-note').hidden, false);
  assert.match(app.nodes.get('reference-resume-description').textContent, /62/);
  assert.match(app.nodes.get('reference-collection-summary').textContent, /34/);
  assert.match(app.nodes.get('reference-collection-summary').textContent, /544/);
  assert.equal(app.nodes.get('api-dsh-model').value, models[0].id);
  assert.equal(app.nodes.get('reference-source-label').value, source.label);
  assert.equal(app.nodes.get('reference-source-kind').value, source.kind);
  assert.equal(app.messages.filter(message => message.value.type === 'api-mri:dsh:lease').length, before);
  // A timeout adjustment changes the next attempt, not the reference source.
  app.nodes.get('timeout').value = '90'; await app.nodes.get('timeout').fire('input');
  await app.nodes.get('check-form').fire('submit'); await settle();
  const plan = app.requests.filter(request => request.path === '/api/plan').at(-1).body;
  assert.equal(plan.resume_run_id, 'accepted-run'); assert.deepEqual(plan.source, source);
  assert.equal(app.nodes.get('start-button').textContent, '继续补采 · 最多 34 次');
  const pending = app.nodes.get('start-button').fire('click'); await settle();
  const lease = app.messages.at(-1).value;
  assert.equal(lease.type, 'api-mri:dsh:lease'); assert.equal(lease.maxRequests, 34); assert.equal(lease.maxOutputTokens, 544);
  await app.message({ type: 'api-mri:dsh:lease-result', nonce, requestId: lease.requestId,
    candidate: { api_base: base, model: models[0].id, api_key: 'new-temporary-reference-capability' } });
  await pending;
  const continued = app.requests.filter(request => request.path === '/api/runs' && request.body).at(-1).body;
  assert.equal(continued.resume_run_id, 'accepted-run'); assert.deepEqual(continued.source, source);
  assert.equal(continued.candidate.api_key, 'new-temporary-reference-capability');
});

test('changing source details or model cancels reference continuation before another request', async () => {
  for (const change of ['reference-source-label', 'api-dsh-model']) {
    const { app, resume } = await partialReferenceWorkbench();
    await resume.fire('click'); await settle();
    if (change === 'api-dsh-model') await app.catalog({ models: models.map(model => ({ ...model, fingerprint_supported: true })) });
    app.nodes.get(change).value = change === 'api-dsh-model' ? models[1].id : 'A different source label';
    await app.nodes.get(change).fire(change === 'api-dsh-model' ? 'change' : 'input'); await settle();
    assert.equal(app.nodes.get('reference-resume-note').hidden, true);
    await app.nodes.get('check-form').fire('submit'); await settle();
    const plan = app.requests.filter(request => request.path === '/api/plan').at(-1).body;
    assert.equal(plan.resume_run_id, undefined); assert.equal(plan.mode, 'reference_collect');
    assert.equal(app.requests.filter(request => request.path === '/api/runs' && request.body).length, 1);
  }
});

test('switching away from reference capture restores the identification empty state', async () => {
  const app = await workbench(); await app.navigate('identity');
  await app.nodes.get('identity-collect').fire('click');
  assert.equal(app.nodes.get('empty-heading').textContent, '建立一份近期参考');
  await app.nodes.get('identity-candidates').fire('click');
  assert.equal(app.nodes.get('empty-heading').textContent, '等待一次扫描');
  assert.equal(app.nodes.get('empty-title').textContent, '同一组回答，比较全部候选');
  assert.doesNotMatch(app.nodes.get('scan-preview').innerHTML, /分开参考与留出/);
});

test('an older fingerprint report can be reordered locally without a DSH lease, API key or model call', async () => {
  const banks = [{ id: 'old-reference', label: 'Old', models: [], model_count: 1 },
    { id: 'rolling-new', label: 'Recent', models: [], model_count: 2, collected_at: '2026-11-09' }];
  const completedRun = { name: 'identification', report: { kind: 'identification', status: 'reference_shortlist',
    bank: { id: banks[0].id }, coverage: { completed_cells: 4, valid_answers: 16, compared_candidates: 1 },
    ranking: [{ model: 'service/older/model-one', distance: 0.1 }], stop_reason: 'shared_core_complete',
    cost: { request_count: 16, requested_output_tokens: 256 } } };
  const recomparisonReport = { name: 'identification', report: { ...completedRun.report, bank: { id: banks[1].id },
    reanalysis: { source_bank_id: banks[0].id, target_bank_id: banks[1].id, reused_requests: 16, model_api_calls: 0 } } };
  const app = await workbench({ library: { banks, active_bank_id: banks[0].id }, completedRun, recomparisonReport });
  await app.hello(); await app.catalog(); await app.navigate('identity');
  await app.api.submit({ mode: 'identify_demo' });
  await [...app.timers.values()].find(fn => fn.name === 'pollRun')(); await settle();
  const walk = node => [node, ...(node.children || []).flatMap(walk)];
  const action = () => walk(app.nodes.get('report-container')).find(node => node.tagName === 'BUTTON' && node.textContent === '用当前库重排 · 0 token');
  assert.equal(action(), undefined);
  app.nodes.get('identification-bank').value = banks[1].id;
  await app.nodes.get('identification-bank').fire('change'); await settle();
  assert.ok(action());
  const before = app.requests.length;
  await action().fire('click');
  assert.deepEqual(app.requests.slice(before), [{ path: '/api/identification-library/recompare', body: { run_id: 'accepted-run', bank_id: banks[1].id } }]);
  assert.equal(app.messages.some(message => message.value.type === 'api-mri:dsh:lease'), false);
  await [...app.timers.values()].find(fn => fn.name === 'pollRun')(); await settle();
  const text = walk(app.nodes.get('report-container')).map(node => node.textContent || '').join('\n');
  assert.match(text, /复用 16 条回答，本次调用 0 次/);
  assert.doesNotMatch(text, /已发送 16 次/);
  assert.equal(action(), undefined);
});
