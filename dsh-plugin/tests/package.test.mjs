import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { build } from '../build.mjs';
import { createMarketIndex } from '../market-index.mjs';
import * as host from '../src/index.js';

const manifest = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8'));

test('official DSH bundle and client manifests select shared host services', async () => {
  assert.equal(manifest.name, 'dsh-api-mri');
  assert.equal(manifest.version, '0.9.3');
  assert.equal(manifest.dsh.bundle.patch, './dsh-plugin/cordis.patch.yml');
  assert.deepEqual(manifest.dsh.client, { platform: 'web', inject: ['@deepseek-ai/dsh-client-ui-slots'] });
  assert.equal(manifest.peerDependencies['dsh-better-sidebar'], undefined);
  assert.equal(manifest.peerDependencies.react, '^18.2.0');
  assert.equal(manifest.dependencies, undefined);
  assert.equal(manifest.optionalDependencies, undefined);
  for (const hook of ['preinstall', 'install', 'postinstall', 'prepare']) assert.equal(manifest.scripts[hook], undefined);
  assert.equal(await readFile(new URL('../cordis.patch.yml', import.meta.url), 'utf8'), '- insert:\n    - id: api-mri\n      name: dsh-api-mri\n');
  assert.equal(host.name, manifest.name);
  assert.deepEqual(host.inject, ['webServer','connection','llm']);
});

test('host authenticates model directory and leases before accessing configured models', async () => {
  const routes = new Map(), disposers = []; let denied = true, directoryReads = 0;
  host.apply({ effect(fn) { disposers.push(fn()); },
    connection: { requestRejection() { return denied ? 'forbidden' : undefined; } },
    webServer: { register(route) { routes.set(route.path, route); return () => routes.delete(route.path); } },
    llm: { listProviders() { directoryReads++; return [{id:'provider',name:'Configured provider'}]; },
      async listModels() { return [{provider:'provider',id:'model',name:'Configured model',apiKey:'must-not-leak'}]; },
      stream() { assert.fail('Listing and authorizing must not call a model'); } },
  });
  async function call(path, method='GET', body) {
    let status, value;
    const req = {method,headers:{},async *[Symbol.asyncIterator]() {if(body)yield Buffer.from(JSON.stringify(body));}};
    const res = {writeHead(s) {status=s;},end(text) {value=JSON.parse(text);}};
    await routes.get(path).handler(req,res); return {status,value};
  }
  try {
    assert.equal((await call('/api/api-mri/models')).status,403);
    assert.equal((await call('/api/api-mri/lease','POST',{})).status,403); assert.equal(directoryReads,0);
    denied=false;
    const catalog=await call('/api/api-mri/models');
    assert.equal(catalog.status,200);assert.equal(catalog.value.models.length,1);
    assert.ok(!JSON.stringify(catalog.value).includes('must-not-leak'));
    const lease=await call('/api/api-mri/lease','POST',{embedOrigin:'dsh-app://app',model:'dsh:provider:model',maxRequests:1,maxOutputTokens:32});
    assert.equal(lease.status,200);assert.equal(lease.value.candidate.model,'dsh:provider:model');
    assert.match(lease.value.candidate.api_key,/^[a-f0-9]{64}$/);
    assert.equal((await call('/api/api-mri/lease','POST',{embedOrigin:'https://evil.test'})).status,400);
  } finally {disposers.reverse().forEach(dispose=>dispose?.());}
});

test('checked-in browser factory registers a disposable native navigation entry and main panel', async () => {
  const source = await readFile(new URL('../lib/client.js', import.meta.url), 'utf8');
  let registration;
  const React = { createElement: (type, props, ...children) => ({ type, props, children }) };
  const window = { __ModuleLoader__: { load(value) { registration = value; } } };
  vm.runInNewContext(source, { window, URL, setTimeout, clearTimeout, Uint8Array });
  assert.equal(registration.id, manifest.name);
  const client = registration.factory(id => { assert.equal(id, 'react'); return React; });
  assert.equal(client.name, manifest.name);
  assert.deepEqual([...client.inject], ['slots']);
  const registrations = [], cleanup = [];
  let disposed = 0;
  client.apply({ slots: {
    inject(name, effect) { cleanup.push(effect()); },
    register(value, component) { registrations.push({ ...value, component }); return () => disposed++; },
  }});
  assert.equal(registrations.length, 2);
  const nav = registrations.find(r => r.name === 'sidebar.panellist');
  const panel = registrations.find(r => r.name === 'main');
  assert.equal(nav.id, 'api-mri');
  assert.equal(nav.label, 'API-MRI');
  assert.equal(nav.component().props.size, 16);
  assert.equal(panel.key, nav.id);
  assert.equal(typeof panel.component, 'function');
  cleanup.forEach(dispose => dispose()); assert.equal(disposed, 2);
  let stateIndex = 0;
  const states = ['http://127.0.0.1:8767', { origin: 'http://127.0.0.1:8767', id: 1 }, 'ready', '', 0, '0.8.0'];
  React.useId = () => 'test-address';
  React.useState = () => [states[stateIndex++], () => {}];
  React.useRef = () => ({ current: null });
  React.useEffect = () => {};
  window.location = { origin: 'http://127.0.0.1:3088' };
  const tree = panel.component({ visible: true });
  function findFrame(node) {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'iframe') return node;
    for (const child of node.children || []) { const found = findFrame(child); if (found) return found; }
    return null;
  }
  const frame = findFrame(tree);
  assert.ok(frame);
  assert.deepEqual(frame.props.sandbox.split(' ').sort(), ['allow-downloads', 'allow-forms', 'allow-popups',
    'allow-popups-to-escape-sandbox', 'allow-same-origin', 'allow-scripts'].sort());
  assert.equal(frame.props.allow, 'clipboard-write');
  assert.equal(frame.props.referrerPolicy, 'no-referrer');
  assert.ok(!frame.props.sandbox.includes('allow-top-navigation'));
  assert.ok(!source.includes("postMessage({ type: 'api-mri:hello', nonce }, '*')"));
  function renderSetup(location) {
    window.location = location;
    stateIndex = 0;
    React.useState = () => [[states[0], null, 'idle', '', 0, ''][stateIndex++], () => {}];
    return panel.component({ visible: true });
  }
  function findButton(node, text) {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'button' && node.children.includes(text)) return node;
    for (const child of node.children || []) { const found = findButton(child, text); if (found) return found; }
    return null;
  }
  const desktop = renderSetup({ origin: 'dsh-app://app' });
  assert.equal(findButton(desktop, '连接').props.disabled, false);
  const opaque = renderSetup({ origin: 'null', href: 'file:///app' });
  assert.equal(findButton(opaque, '连接').props.disabled, true);
});

test('browser and host outputs match source without a package build hook', async () => {
  // Rebuild outside the checkout so this check never repairs a stale release file.
  const { mkdtemp } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const outDir = await mkdtemp(join(tmpdir(), 'api-mri-dsh-build-'));
  const result = await build({ outDir });
  assert.equal(result.browser, await readFile(new URL('../lib/client.js', import.meta.url), 'utf8'));
  assert.equal(result.host, await readFile(new URL('../lib/index.js', import.meta.url), 'utf8'));
});

test('own market index pins one immutable source and uses official format 1', () => {
  const commit = 'ab'.repeat(20);
  const index = createMarketIndex({ commit, publishedAt: 1793420000 });
  assert.equal(index.format, 1);
  const entry = index.plugins[0];
  assert.equal(entry.name, manifest.name);
  assert.equal(entry.versions[0].version, manifest.version);
  assert.equal(entry.versions[0].source.commit, commit);
  assert.equal(entry.versions[0].source.url, 'https://github.com/Lee-Eee-Eee/API-MRI');
  assert.equal(entry.metadata.category, 'tools');
  for (const bad of ['v0.8.0', '0'.repeat(40), 'AB'.repeat(20), 'abcd']) {
    assert.throws(() => createMarketIndex({ commit: bad, publishedAt: 1793420000 }));
  }
  assert.throws(() => createMarketIndex({ commit, publishedAt: -1 }));
});
