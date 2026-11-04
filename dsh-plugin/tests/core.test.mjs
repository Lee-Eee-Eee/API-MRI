import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_BACKEND, STORAGE_KEY, parseLocalOrigin, readSavedOrigin, saveOrigin,
  createNonce, createHandshake, createModelConnector, startupCommands, DESKTOP_ORIGIN, parseDshOrigin, hostOriginForLocation } from '../src/core.js';

test('DSH host accepts only the fixed desktop origin and existing local Web origins', () => {
  for (const input of ['dsh-app://app', 'dsh-app://app/']) assert.equal(parseDshOrigin(input), DESKTOP_ORIGIN);
  assert.equal(parseDshOrigin('http://localhost:3080/'), 'http://localhost:3080');
  for (const input of ['null', null, 'dsh-app://other', 'dsh-app://app.evil', 'dsh-app://app:3080',
    'dsh-app://user@app', 'dsh-app://user:pass@app', 'dsh-app://app/path', 'dsh-app://app//',
    'dsh-app://app?key=value', 'dsh-app://app#x', 'dsh-app://%61pp', 'file://app',
    'https://remote.example', ' dsh-app://app', 'dsh-app://app\n']) {
    assert.throws(() => parseDshOrigin(input), { name: 'Error' }, String(input));
  }
  // A DSH page origin never becomes a model-backend address or a handshake destination.
  assert.throws(() => parseLocalOrigin(DESKTOP_ORIGIN));
  assert.throws(() => saveOrigin(null, DESKTOP_ORIGIN));
});

test('desktop location fallback never accepts a generic null or opaque page', () => {
  const desktop = { origin: 'null', protocol: 'dsh-app:', hostname: 'app', host: 'app', port: '', href: 'dsh-app://app/' };
  assert.equal(hostOriginForLocation({ origin: DESKTOP_ORIGIN }), DESKTOP_ORIGIN);
  assert.equal(hostOriginForLocation(desktop), DESKTOP_ORIGIN);
  assert.equal(hostOriginForLocation({ origin: 'http://127.0.0.1:3080' }), 'http://127.0.0.1:3080');
  for (const input of [null, {}, { origin: 'null' }, { ...desktop, origin: 'https://evil.example' },
    { ...desktop, protocol: 'file:' }, { ...desktop, hostname: 'other' }, { ...desktop, host: 'app:3080' },
    { ...desktop, port: '3080' }, { ...desktop, href: 'dsh-app://user@app/' },
    { ...desktop, href: 'dsh-app://app/path' }, { ...desktop, href: 'dsh-app://app?query' },
    { ...desktop, href: 'file:///app' }]) assert.throws(() => hostOriginForLocation(input), { name: 'Error' });
  assert.deepEqual(startupCommands(DESKTOP_ORIGIN), {
    windows: '.\\API-MRI.exe --embed-origin dsh-app://app --no-browser',
    source: 'python -m relay_audit.cli ui --embed-origin dsh-app://app'
  });
});

test('backend address accepts exact loopback origins with valid ports', () => {
  for (const [input, expected] of [
    [DEFAULT_BACKEND, DEFAULT_BACKEND], [' http://localhost:9000/ ', 'http://localhost:9000'],
    ['https://LOCALHOST:443', 'https://localhost'], ['http://127.0.0.1:80/', 'http://127.0.0.1'],
    ['http://[::1]:65535', 'http://[::1]:65535'], ['https://[::1]:1/', 'https://[::1]:1']
  ]) assert.equal(parseLocalOrigin(input), expected);
});

test('backend address rejects remote hosts, credential URLs, paths and alternate IP spellings', () => {
  for (const input of [null, '', 'localhost:8765', 'ftp://localhost', 'file:///tmp', 'javascript:alert(1)',
    'https://example.com', 'http://localhost.evil', 'http://localhost.', 'http://127.1', 'http://2130706433',
    'http://0x7f000001', 'http://127.0.0.2', 'http://[::ffff:127.0.0.1]', 'http://127.0.0.1@evil.test',
    'http://user:pass@localhost', 'http://localhost?key=secret', 'http://localhost#key', 'http://localhost/path',
    'http://localhost//', 'http://localhost:0', 'http://localhost:65536', 'http://localhost:999999',
    'http://localhost:12\n34', 'http://localhost\\@example.com', 'http://%6cocalhost']) {
    assert.throws(() => parseLocalOrigin(input), { name: 'Error' }, String(input));
  }
});

test('storage persists one validated address and tolerates unavailable storage', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  assert.equal(readSavedOrigin(storage), DEFAULT_BACKEND);
  saveOrigin(storage, 'http://localhost:9000/');
  assert.deepEqual([...values], [[STORAGE_KEY, 'http://localhost:9000']]);
  values.set(STORAGE_KEY, 'https://evil.test');
  assert.equal(readSavedOrigin(storage), DEFAULT_BACKEND);
  assert.throws(() => saveOrigin(storage, 'http://localhost?key=secret'));
  assert.equal(readSavedOrigin({ getItem() { throw Error('disabled'); } }), DEFAULT_BACKEND);
  assert.equal(saveOrigin({ setItem() { throw Error('disabled'); } }, DEFAULT_BACKEND), DEFAULT_BACKEND);
});

test('nonce uses browser cryptography and startup commands use verified entry points', () => {
  assert.equal(createNonce({ getRandomValues(bytes) { bytes.fill(12); } }), '0c'.repeat(24));
  assert.throws(() => createNonce({}));
  assert.deepEqual(startupCommands('http://localhost:3000/'), {
    windows: '.\\API-MRI.exe --embed-origin http://localhost:3000 --no-browser',
    source: 'python -m relay_audit.cli ui --embed-origin http://localhost:3000'
  });
  assert.throws(() => startupCommands('https://remote.example'));
});

function harness() {
  const listeners = new Set();
  const timers = new Map();
  const messages = [];
  const ready = [];
  let timeouts = 0;
  let serial = 0;
  const eventTarget = { addEventListener(type, fn) { assert.equal(type, 'message'); listeners.add(fn); },
    removeEventListener(type, fn) { assert.equal(type, 'message'); listeners.delete(fn); } };
  const targetWindow = { postMessage: (data, origin) => messages.push({ data, origin }) };
  const origin = DEFAULT_BACKEND;
  const nonce = 'ab'.repeat(24);
  const channel = createHandshake({ eventTarget, targetWindow, targetOrigin: origin, nonce,
    onReady: data => ready.push(data), onTimeout: () => timeouts++,
    setTimer(fn) { timers.set(++serial, fn); return serial; }, clearTimer(id) { timers.delete(id); } });
  return { channel, targetWindow, origin, nonce, messages, listeners, timers, ready,
    get timeouts() { return timeouts; },
    receive(event = {}) { for (const fn of [...listeners]) fn({ origin, source: targetWindow, data: { type: 'api-mri:ready', nonce, version: '0.8.0' }, ...event }); },
    expire() { for (const fn of [...timers.values()]) fn(); } };
}

test('handshake targets exact origin and checks source, origin and nonce', () => {
  const env = harness();
  env.channel.start();
  assert.deepEqual(env.messages, [{ data: { type: 'api-mri:hello', nonce: env.nonce }, origin: env.origin }]);
  env.receive({ origin: 'http://localhost:8765' });
  env.receive({ source: {} });
  env.receive({ data: { type: 'api-mri:ready', nonce: 'cd'.repeat(24), version: '0.8.0' } });
  env.receive({ data: { type: 'other', nonce: env.nonce } });
  env.receive({ data: null });
  assert.deepEqual(env.ready, []);
  env.receive({ data: { type: 'api-mri:ready', nonce: env.nonce, version: '0.8.0', apiKey: 'never-forward-this', report: 'private' } });
  assert.deepEqual(env.ready, [{ version: '0.8.0' }]);
  assert.equal(env.listeners.size, 0);
  assert.equal(env.timers.size, 0);
  env.channel.start();
  env.expire();
  assert.equal(env.messages.length, 1);
  assert.equal(env.timeouts, 0);
});

test('pause removes listeners and timers; resume and disposal are idempotent', () => {
  const env = harness();
  env.channel.start();
  env.channel.start();
  assert.equal(env.messages.length, 1);
  env.channel.pause();
  env.receive(); env.expire();
  assert.equal(env.ready.length, 0);
  assert.equal(env.listeners.size, 0);
  assert.equal(env.timers.size, 0);
  env.channel.resume();
  assert.equal(env.messages.length, 2);
  env.channel.dispose();
  env.channel.dispose(); env.channel.resume();
  env.receive(); env.expire();
  assert.equal(env.listeners.size, 0);
  assert.equal(env.timers.size, 0);
  assert.equal(env.ready.length, 0);
  assert.equal(env.timeouts, 0);
});

test('timeout is finite and malformed version never crosses callback', () => {
  const timed = harness(); timed.channel.start(); timed.expire(); timed.expire();
  assert.equal(timed.timeouts, 1);
  assert.equal(timed.listeners.size, 0);
  assert.equal(timed.timers.size, 0);
  timed.receive(); assert.equal(timed.ready.length, 0);
  const badVersion = harness(); badVersion.channel.start();
  badVersion.receive({ data: { type: 'api-mri:ready', nonce: badVersion.nonce, version: '<script>alert(1)</script>' } });
  assert.deepEqual(badVersion.ready, [{ version: '' }]);
});

test('configured-model connector checks frame identity and nonce before granting bounded calls', async () => {
  const listeners = new Set(), sent = [], calls = [];
  const targetWindow = { postMessage: (data, origin) => sent.push({ data, origin }) };
  const nonce = 'ac'.repeat(24), origin = DEFAULT_BACKEND;
  const connector = createModelConnector({ nonce, targetWindow, targetOrigin: origin, hostOrigin: DESKTOP_ORIGIN,
    eventTarget: { addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) },
    request: async (path, body) => { calls.push({ path, body }); return path === 'models'
      ? { models: [{ id:'dsh:p:m', label:'Model', provider:'p', model:'m' }], apiBase:'http://127.0.0.1:12345/v1' }
      : { candidate:{api_base:'http://127.0.0.1:12345/v1',model:'dsh:p:m',api_key:'short-lived-test-only'} }; },
  });
  const dispatch = async event => { for (const listener of listeners) await listener(event); };
  connector.start(); await Promise.resolve();
  assert.equal(calls.length, 1); assert.equal(sent[0].data.type, 'api-mri:dsh:catalog');
  const event = { source:targetWindow, origin, data:{type:'api-mri:dsh:lease',nonce,requestId:'abcd1234',model:'dsh:p:m',maxRequests:2,maxOutputTokens:32} };
  await dispatch({...event,source:{}}); await dispatch({...event,origin:'http://evil.test'});
  await dispatch({...event,data:{...event.data,nonce:'wrong'}});
  assert.equal(calls.length, 1);
  await dispatch(event);
  assert.deepEqual(calls[1],{path:'lease',body:{model:'dsh:p:m',maxRequests:2,maxOutputTokens:32,embedOrigin:DESKTOP_ORIGIN}});
  assert.equal(sent[1].data.type,'api-mri:dsh:lease-result');
  assert.equal(sent[1].data.nonce,nonce); assert.equal(sent[1].origin,origin);
  connector.dispose(); assert.equal(listeners.size,0);
  await dispatch(event); assert.equal(calls.length,2);
});
