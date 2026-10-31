import React from 'react';
import { DEFAULT_BACKEND, parseLocalOrigin, readSavedOrigin, saveOrigin,
  createNonce, createHandshake, startupCommands } from './core.js';

export const name = 'dsh-api-mri';
export const inject = ['betterSidebar'];

const h = React.createElement;
const CSS = `
.api-mri-companion{--mri-paper:#f4f4ee;--mri-ink:#282c27;--mri-olive:#272e29;--mri-copper:#98482e;--mri-sage:#738365;box-sizing:border-box;display:flex;flex-direction:column;height:100%;min-height:0;color:var(--mri-ink);background:var(--mri-paper);font:13px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif;color-scheme:light}
.api-mri-companion *{box-sizing:border-box}.api-mri-companion button,.api-mri-companion input{font:inherit}.api-mri-companion button,.api-mri-companion a{touch-action:manipulation}.api-mri-companion :focus-visible{outline:3px solid var(--mri-copper);outline-offset:3px}
.api-mri-companion__bar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:10px 14px;background:var(--mri-olive);color:#f6f4e9;flex-shrink:0}.api-mri-companion__mark{display:block;flex-shrink:0}.api-mri-companion__wordmark{font-size:14px;font-weight:650;letter-spacing:.03em;margin-right:auto}.api-mri-companion__bar a,.api-mri-companion__bar button{font-size:12px;color:#f6f4e9;text-decoration:none;background:transparent;border:0;padding:3px 4px;cursor:pointer}.api-mri-companion__bar a:hover,.api-mri-companion__bar button:hover{text-decoration:underline}
.api-mri-companion__setup{padding:clamp(18px,5vw,32px);overflow:auto;min-height:0;max-width:640px;width:100%;margin:0 auto}.api-mri-companion__setup h2{font-size:24px;font-weight:600;line-height:1.25;letter-spacing:-.03em;margin:14px 0 8px}.api-mri-companion__setup p{margin:0 0 22px;color:#596153}.api-mri-companion__form{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px}.api-mri-companion label{font-size:12px;font-weight:650}.api-mri-companion__form input{flex:1 1 180px;min-width:0;width:100%;border:1px solid #a7ab9a;background:#fffef8;padding:10px 11px;border-radius:5px;color:var(--mri-ink)}.api-mri-companion__connect{border:0;border-radius:5px;padding:10px 17px;background:var(--mri-copper);color:#fffaf0;font-weight:600;cursor:pointer}.api-mri-companion__connect:hover{background:#7d3823}.api-mri-companion__connect:disabled{opacity:.5;cursor:default}
.api-mri-companion__error{color:#8c3822!important;font-size:12px;margin:10px 0 18px!important}.api-mri-companion__launch{margin:26px 0 0;border-top:1px solid #cdd0c2;padding-top:16px}.api-mri-companion__launch summary{cursor:pointer;font-weight:600}.api-mri-companion__launch p{font-size:12px;margin:11px 0 7px}.api-mri-companion__launch code{display:block;background:#e6e8dc;padding:12px;overflow-wrap:anywhere;white-space:pre-wrap;font:11px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;border-radius:4px}.api-mri-companion__launch a{display:inline-block;margin-top:11px;color:var(--mri-copper)}
.api-mri-companion__frame-wrap{position:relative;display:flex;flex:1;min-height:0}.api-mri-companion__frame{display:block;flex:1;width:100%;height:100%;min-height:0;border:0;background:var(--mri-paper)}.api-mri-companion__pending{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:var(--mri-paper);padding:24px;text-align:center;color:#596153}.api-mri-companion__status{font-size:11px;color:#d9dfc7}.api-mri-companion__failure{position:absolute;inset:0;overflow:auto;background:var(--mri-paper);padding:24px}.api-mri-companion__failure h2{font-size:19px;margin:0 0 12px}.api-mri-companion__failure p{margin:0 0 18px;overflow-wrap:anywhere}.api-mri-companion__failure button{border:0;border-radius:5px;padding:9px 14px;background:var(--mri-copper);color:#fffaf0;cursor:pointer}.api-mri-companion__failure a{margin-left:14px;color:var(--mri-copper)}
@media(prefers-reduced-motion:no-preference){.api-mri-companion__connect{transition:background .15s ease}}`;

function Mark({ size = 24 }) {
  return h('svg', { width: size, height: size, viewBox: '0 0 28 28', 'aria-hidden': true, className: 'api-mri-companion__mark' },
    h('rect', { x: 2, y: 13, width: 5, height: 12, rx: 1, fill: '#cb9c76' }),
    h('rect', { x: 11, y: 6, width: 5, height: 19, rx: 1, fill: '#e4ddc4' }),
    h('rect', { x: 20, y: 2, width: 5, height: 23, rx: 1, fill: '#a6b28a' }));
}

function localHostOrigin() {
  try { return parseLocalOrigin(window.location.origin); } catch { return null; }
}

function storedOrigin() {
  try { return readSavedOrigin(window.localStorage); } catch { return DEFAULT_BACKEND; }
}

function LaunchHelp({ origin, open = false }) {
  if (!origin) return null;
  const commands = startupCommands(origin);
  return h('details', { className: 'api-mri-companion__launch', open },
    h('summary', null, '启动 API-MRI'),
    h('p', null, 'Windows：在解压后的程序目录运行'),
    h('code', null, commands.windows),
    h('p', null, 'macOS / Linux：在源码的 Python 环境运行'),
    h('code', null, commands.source),
    h('a', { href: 'https://github.com/Lee-Eee-Eee/API-MRI/releases', target: '_blank', rel: 'noopener noreferrer' }, '下载与安装指南 ↗'));
}

export function Workbench({ visible = true }) {
  const hostOrigin = localHostOrigin();
  const inputId = React.useId();
  const [address, setAddress] = React.useState(storedOrigin);
  const [connection, setConnection] = React.useState(null);
  const [state, setState] = React.useState('idle');
  const [error, setError] = React.useState('');
  const [loadGeneration, setLoadGeneration] = React.useState(0);
  const [version, setVersion] = React.useState('');
  const frame = React.useRef(null);
  const attempt = React.useRef(0);

  React.useEffect(() => {
    // After a failed attempt only the explicit reconnect action creates a new attempt.
    if (!connection || state === 'error' || !visible || !frame.current?.contentWindow) return undefined;
    let handshake;
    try {
      handshake = createHandshake({
        eventTarget: window,
        targetWindow: frame.current.contentWindow,
        targetOrigin: connection.origin,
        nonce: createNonce(window.crypto),
        onReady: ({ version: readyVersion }) => { setVersion(readyVersion); setState('ready'); setError(''); },
        onTimeout: () => { setState('error'); setError('没有收到 API-MRI 的连接响应。请确认程序已启动，并允许当前 DSH 地址嵌入。'); }
      });
      handshake.start();
    } catch (cause) { setState('error'); setError(cause.message); }
    return () => handshake?.dispose();
  }, [connection, visible, loadGeneration]);

  function connect(event) {
    event?.preventDefault();
    try {
      if (!hostOrigin) throw new Error('请在 localhost、127.0.0.1 或 [::1] 上打开 DSH Web。');
      const origin = parseLocalOrigin(address);
      if (origin === hostOrigin) throw new Error('API-MRI 与 DSH 须使用不同端口。');
      setAddress(origin);
      try { saveOrigin(window.localStorage, origin); } catch { /* Storage is optional. */ }
      attempt.current += 1;
      setConnection({ origin, id: attempt.current });
      setState('loading');
      setError('');
      setVersion('');
    } catch (cause) { setError(cause.message); }
  }

  function disconnect() {
    setConnection(null);
    setState('idle');
    setError('');
    setVersion('');
  }

  return h('section', { className: 'api-mri-companion', 'aria-label': 'API-MRI' },
    h('style', null, CSS),
    h('header', { className: 'api-mri-companion__bar' }, h(Mark, {}),
      h('span', { className: 'api-mri-companion__wordmark' }, 'API-MRI'),
      connection && state === 'ready' && h('span', { className: 'api-mri-companion__status', role: 'status' }, version ? `v${version}` : '已连接'),
      connection && h('a', { href: connection.origin, target: '_blank', rel: 'noopener noreferrer', 'aria-label': '在新窗口打开 API-MRI' }, '打开 ↗'),
      connection && h('button', { type: 'button', onClick: disconnect }, '更换连接')),
    !connection ? h('div', { className: 'api-mri-companion__setup' },
      h(Mark, { size: 44 }),
      h('h2', null, '连接你的 API-MRI'),
      h('p', null, '在侧栏检查连接、吞吐、工具调用与模型指纹。'),
      h('form', { onSubmit: connect },
        h('label', { htmlFor: inputId }, '服务地址'),
        h('div', { className: 'api-mri-companion__form' },
          h('input', { id: inputId, value: address, onChange: event => setAddress(event.target.value),
            type: 'text', inputMode: 'url', autoCapitalize: 'none', autoCorrect: 'off', spellCheck: false,
            placeholder: DEFAULT_BACKEND, 'aria-invalid': !!error, 'aria-describedby': error ? `${inputId}-error` : undefined }),
          h('button', { className: 'api-mri-companion__connect', type: 'submit', disabled: !hostOrigin }, '连接'))),
      (error || !hostOrigin) && h('p', { className: 'api-mri-companion__error', id: `${inputId}-error`, role: 'alert' }, error || '请在 localhost、127.0.0.1 或 [::1] 上打开 DSH Web。'),
      h(LaunchHelp, { origin: hostOrigin, open: true }))
      : h('div', { className: 'api-mri-companion__frame-wrap' },
        h('iframe', { key: connection.id, ref: frame, className: 'api-mri-companion__frame', src: connection.origin,
          title: 'API-MRI 工作台', sandbox: 'allow-scripts allow-same-origin allow-downloads allow-forms',
          referrerPolicy: 'no-referrer', onLoad: () => setLoadGeneration(value => value + 1) }),
        state === 'loading' && h('div', { className: 'api-mri-companion__pending', role: 'status', 'aria-live': 'polite' }, '正在连接 API-MRI…'),
        state === 'error' && h('div', { className: 'api-mri-companion__failure', role: 'alert' },
          h('h2', null, '连接尚未就绪'), h('p', null, error),
          h('button', { type: 'button', onClick: connect }, '重新连接'),
          h('a', { href: connection.origin, target: '_blank', rel: 'noopener noreferrer' }, '打开检查 ↗'),
          h(LaunchHelp, { origin: hostOrigin, open: true }))));
}

export function apply(ctx) {
  ctx.effect(() => ctx.betterSidebar.registerTab({
    id: 'api-mri:workbench', title: 'API-MRI', single: true, order: 60,
    icon: h(Mark, {}), component: ({ visible }) => h(Workbench, { visible })
  }));
}
