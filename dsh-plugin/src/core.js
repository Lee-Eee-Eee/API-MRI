export const DEFAULT_BACKEND = 'http://127.0.0.1:8765';
export const STORAGE_KEY = 'api-mri.backend-origin';

/** Accept an origin, never an arbitrary URL or a host resolved from user input. */
export function parseLocalOrigin(value) {
  if (typeof value !== 'string') throw new Error('请输入 API-MRI 地址。');
  const candidate = value.trim();
  const match = /^(https?):\/\/(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?\/?$/i.exec(candidate);
  if (!match || (match[3] && (Number(match[3]) < 1 || Number(match[3]) > 65535))) {
    throw new Error('地址须为 http(s)://127.0.0.1、localhost 或 [::1]，可附端口。');
  }
  return new URL(candidate).origin;
}

export function readSavedOrigin(storage) {
  try { return parseLocalOrigin(storage?.getItem(STORAGE_KEY) || DEFAULT_BACKEND); }
  catch { return DEFAULT_BACKEND; }
}

export function saveOrigin(storage, origin) {
  const safeOrigin = parseLocalOrigin(origin);
  try { storage?.setItem(STORAGE_KEY, safeOrigin); } catch { /* Private browsing may disable storage. */ }
  return safeOrigin;
}

export function createNonce(cryptoProvider) {
  if (!cryptoProvider?.getRandomValues) throw new Error('浏览器不支持安全连接握手。');
  const bytes = new Uint8Array(24);
  cryptoProvider.getRandomValues(bytes);
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}

/** Only readiness metadata crosses the frame boundary. No API-MRI content is read. */
export function createHandshake({ eventTarget, targetWindow, targetOrigin, nonce,
  onReady, onTimeout, setTimer = setTimeout, clearTimer = clearTimeout, timeoutMs = 8000 }) {
  const origin = parseLocalOrigin(targetOrigin);
  if (!/^[a-f0-9]{48}$/.test(nonce)) throw new Error('Invalid handshake nonce');
  let timer = null;
  let listening = false;
  let finished = false;
  let disposed = false;

  function detach() {
    if (timer !== null) { clearTimer(timer); timer = null; }
    if (listening) { eventTarget.removeEventListener('message', receive); listening = false; }
  }
  function receive(event) {
    if (disposed || finished || !listening || event.origin !== origin || event.source !== targetWindow) return;
    const data = event.data;
    if (!data || typeof data !== 'object' || data.type !== 'api-mri:ready' || data.nonce !== nonce) return;
    finished = true;
    detach();
    const version = typeof data.version === 'string' && /^[A-Za-z0-9.+-]{1,40}$/.test(data.version)
      ? data.version : '';
    onReady({ version });
  }
  function start() {
    if (disposed || finished || listening) return;
    eventTarget.addEventListener('message', receive);
    listening = true;
    timer = setTimer(() => {
      if (disposed || finished || !listening) return;
      finished = true;
      detach();
      onTimeout();
    }, timeoutMs);
    try { targetWindow.postMessage({ type: 'api-mri:hello', nonce }, origin); }
    catch { finished = true; detach(); onTimeout(); }
  }
  return {
    start,
    pause: detach,
    resume: start,
    dispose() { disposed = true; detach(); }
  };
}

export function startupCommands(dshOrigin) {
  const origin = parseLocalOrigin(dshOrigin);
  return {
    windows: `.\\API-MRI.exe --embed-origin ${origin} --no-browser`,
    source: `python -m relay_audit.cli ui --embed-origin ${origin}`
  };
}
