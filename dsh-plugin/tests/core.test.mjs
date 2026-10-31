import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_BACKEND, STORAGE_KEY, parseLocalOrigin, readSavedOrigin, saveOrigin,
  createNonce, createHandshake, startupCommands } from '../src/core.js';

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
