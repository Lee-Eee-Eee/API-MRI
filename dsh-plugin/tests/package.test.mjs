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
  assert.equal(manifest.version, '0.8.0');
  assert.equal(manifest.dsh.bundle.patch, './dsh-plugin/cordis.patch.yml');
  assert.deepEqual(manifest.dsh.client, { platform: 'web', inject: ['dsh-better-sidebar'] });
  assert.equal(manifest.peerDependencies['dsh-better-sidebar'], '>=0.28.2 <0.29.0');
  assert.equal(manifest.peerDependencies.react, '^18.2.0');
  assert.equal(manifest.dependencies, undefined);
  assert.equal(manifest.optionalDependencies, undefined);
  for (const hook of ['preinstall', 'install', 'postinstall', 'prepare']) assert.equal(manifest.scripts[hook], undefined);
  assert.equal(await readFile(new URL('../cordis.patch.yml', import.meta.url), 'utf8'), '- insert:\n    - id: api-mri\n      name: dsh-api-mri\n');
  assert.equal(host.name, manifest.name);
  assert.equal(host.apply(new Proxy({}, { get() { throw Error('Host service was accessed'); } })), undefined);
});

test('checked-in browser factory registers one disposable BetterSidebar tab', async () => {
  const source = await readFile(new URL('../lib/client.js', import.meta.url), 'utf8');
  let registration;
  const React = { createElement: (type, props, ...children) => ({ type, props, children }) };
  const window = { __ModuleLoader__: { load(value) { registration = value; } } };
  vm.runInNewContext(source, { window, URL, setTimeout, clearTimeout, Uint8Array });
  assert.equal(registration.id, manifest.name);
  const client = registration.factory(id => { assert.equal(id, 'react'); return React; });
  assert.equal(client.name, manifest.name);
  assert.deepEqual([...client.inject], ['betterSidebar']);
  let tab, effects = 0, disposed = 0, cleanup;
  client.apply({ effect(effect) { effects++; cleanup = effect(); },
    betterSidebar: { registerTab(value) { tab = value; return () => disposed++; } } });
  assert.equal(effects, 1);
  assert.equal(tab.id, 'api-mri:workbench');
  assert.equal(tab.single, true);
  assert.equal(tab.title, 'API-MRI');
  assert.equal(typeof tab.component, 'function');
  assert.equal(tab.component({ visible: false }).props.visible, false);
  cleanup(); assert.equal(disposed, 1);
  assert.ok(source.includes("sandbox: 'allow-scripts allow-same-origin allow-downloads allow-forms'"));
  assert.ok(!source.includes('allow-top-navigation'));
  assert.ok(!source.includes("postMessage({ type: 'api-mri:hello', nonce }, '*')"));
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
