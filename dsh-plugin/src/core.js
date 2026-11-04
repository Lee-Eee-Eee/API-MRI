export const DEFAULT_BACKEND = 'http://127.0.0.1:8765';
export const STORAGE_KEY = 'api-mri.backend-origin';
export const DESKTOP_ORIGIN = 'dsh-app://app';

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

/** The DSH renderer may be its one privileged desktop origin or a local Web origin. */
export function parseDshOrigin(value) {
  if (typeof value === 'string' && /^dsh-app:\/\/app\/?$/.test(value)) return DESKTOP_ORIGIN;
  return parseLocalOrigin(value);
}

export function hostOriginForLocation(location) {
  try { return parseDshOrigin(location?.origin); }
  catch (error) {
    // Some URL implementations serialize custom schemes as "null". Derive
    // only from the exact official application URL, never from "null" alone.
    if (location?.origin === 'null' && location.protocol === 'dsh-app:'
      && location.hostname === 'app' && location.host === 'app' && location.port === ''
      && typeof location.href === 'string' && /^dsh-app:\/\/app\/?$/.test(location.href)) {
      return DESKTOP_ORIGIN;
    }
    throw error;
  }
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
  const origin = parseDshOrigin(dshOrigin);
  return {
    windows: `.\\API-MRI.exe --embed-origin ${origin} --no-browser`,
    source: `python -m relay_audit.cli ui --embed-origin ${origin}`
  };
}

/** Bind one trusted workbench frame to the host's non-secret model directory. */
export function createModelConnector({ eventTarget, targetWindow, targetOrigin, nonce, hostOrigin, request }) {
  const origin = parseLocalOrigin(targetOrigin);
  let disposed = false, listening = false;
  const post = value => { if (!disposed) targetWindow.postMessage({ ...value, nonce }, origin); };
  async function catalog() {
    try { post({ type: 'api-mri:dsh:catalog', ...await request('models') }); }
    catch { post({ type: 'api-mri:dsh:catalog', models: [], error: '暂时无法读取 DSH 模型，请刷新目录。' }); }
  }
  async function receive(event) {
    if (disposed || event.source !== targetWindow || event.origin !== origin || event.data?.nonce !== nonce) return;
    const data = event.data;
    if (data.type === 'api-mri:dsh:refresh') return catalog();
    if (data.type !== 'api-mri:dsh:lease' || typeof data.requestId !== 'string' || !/^[A-Za-z0-9_-]{8,80}$/.test(data.requestId)) return;
    try {
      const value = await request('lease', { model: data.model, maxRequests: data.maxRequests,
        maxOutputTokens: data.maxOutputTokens, embedOrigin: hostOrigin });
      post({ type: 'api-mri:dsh:lease-result', requestId: data.requestId, candidate: value.candidate });
    } catch (error) {
      post({ type: 'api-mri:dsh:lease-result', requestId: data.requestId, error: error.message || '无法创建 DSH 调用授权。' });
    }
  }
  return {
    start() { if (disposed || listening) return; listening = true; eventTarget.addEventListener('message', receive); void catalog(); },
    dispose() { disposed = true; if (listening) eventTarget.removeEventListener('message', receive); listening = false; },
  };
}
