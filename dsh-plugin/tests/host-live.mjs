/** Real, isolated DSH Host startup check. No GUI and no model generation.
 * node dsh-plugin/tests/host-live.mjs [--plugin-root installed-package] [--out result.json]
 */
import { mkdtemp, mkdir, readFile, writeFile, symlink, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const pluginArg = process.argv.indexOf('--plugin-root');
const source = pluginArg >= 0 ? path.resolve(process.argv[pluginArg + 1])
  : path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const manifest = JSON.parse(await readFile(path.join(source, 'package.json'), 'utf8'));
const version = manifest.version;
const realLocal = path.join(process.env.USERPROFILE, 'AppData', 'Local');
const installation = path.join(realLocal, 'Programs', 'DeepSeek Harness');
const executable = path.join(installation, 'DeepSeek Harness.exe');
const cli = path.join(installation, 'resources', 'app.asar', 'dsh', 'node_modules', '@deepseek-ai', 'dsh-desktop-host', 'lib', 'cli.js');
const installed = path.join(realLocal, 'API-MRI', 'dsh-runtime', version);
const receipt = JSON.parse(await readFile(path.join(installed, 'runtime-receipt.json'), 'utf8'));
if (receipt.version !== version || !/^[a-f0-9]{64}$/.test(receipt.sha256)) throw new Error('Installed runtime receipt is missing or mismatched.');
await stat(path.join(installed, `API-MRI-${version}-windows-x64`, 'API-MRI.exe'));

const isolated = await mkdtemp(path.join(tmpdir(), 'api-mri-host-live-'));
const home = path.join(isolated, 'dsh-home'), local = path.join(isolated, 'local-appdata');
const profile = path.join(home, 'profiles', 'probe');
await mkdir(path.join(profile, 'node_modules'), { recursive: true });
await mkdir(path.join(local, 'API-MRI', 'dsh-runtime'), { recursive: true });
await symlink(source, path.join(profile, 'node_modules', 'dsh-api-mri'), 'junction');
await symlink(installed, path.join(local, 'API-MRI', 'dsh-runtime', version), 'junction');
await writeFile(path.join(profile, 'package.json'), JSON.stringify({
  name: 'api-mri-isolated-host-probe', private: true,
  dependencies: { 'dsh-api-mri': `link:${source.replaceAll('\\', '/')}` },
  dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', 'dsh-api-mri'] } },
}));
await writeFile(path.join(profile, 'cordis.patch.yml'), '[]\n');

const env = {};
for (const name of ['SystemRoot', 'WINDIR', 'COMSPEC', 'PATH', 'Path', 'PATHEXT', 'USERPROFILE', 'TEMP', 'TMP']) {
  if (process.env[name]) env[name] = process.env[name];
}
Object.assign(env, { DSH_HOME: home, DSH_AGENTS_HOME: path.join(isolated, 'agents'),
  APPDATA: path.join(isolated, 'roaming-appdata'), LOCALAPPDATA: local,
  ELECTRON_RUN_AS_NODE: '1', DSH_PRODUCT_ANALYTICS_OTLP_URL: '', NO_COLOR: '1' });
await mkdir(env.DSH_AGENTS_HOME, { recursive: true });
const result = { schema: 1, checkedAt: new Date().toISOString(), pluginVersion: version,
  isolatedProfile: true, privateConfigurationRead: false, realModelCalls: 0, status: 'failed', checks: {} };
let output = '', child, runtimePid, hostUrl;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const timeout = () => AbortSignal.timeout(10000);
async function recordRuntimePid() {
  try { const value = JSON.parse(await readFile(path.join(local, 'API-MRI', 'dsh-runtime', 'data', '.portable-session.json'), 'utf8'));
    if (Number.isInteger(value.pid) && value.pid > 0) runtimePid = value.pid;
  } catch {}
}
try {
  child = spawn(executable, ['--expose-internals', cli, '--profile', 'probe', '--host', '127.0.0.1', '--port', '0', '--no-open'], {
    env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', chunk => { output = (output + chunk.toString('utf8')).slice(-500000); });
  child.stderr.on('data', chunk => { output = (output + chunk.toString('utf8')).slice(-500000); });
  let launch;
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    const urls = output.match(/https?:\/\/(?:127\.0\.0\.1|localhost):\d+[^\s"'<>]*/g) || [];
    launch = urls.map(value => { try { return new URL(value.replace(/\u001b\[[0-9;]*m/g, '')); } catch { return null; } })
      .find(value => value?.searchParams.has('token'));
    if (launch) break;
    if (child.exitCode !== null) throw new Error('Isolated DSH exited before providing its launch URL.');
    await delay(200);
  }
  if (!launch) throw new Error('Isolated DSH did not become ready within 60 seconds.');
  hostUrl = launch.origin;
  const authorize = await fetch(launch.href, { redirect: 'manual', signal: timeout() });
  const cookie = authorize.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
  result.authenticationStatus = authorize.status; result.authenticationCookiePresent = Boolean(cookie);
  if (![302, 303, 307, 308].includes(authorize.status) || !cookie) throw new Error('DSH launch token did not exchange for its own authenticated cookie.');
  result.checks.hostAuthentication = true;
  const root = await fetch(hostUrl + '/', { headers: { Cookie: cookie }, signal: timeout() });
  if (!root.ok) throw new Error('Authenticated DSH index unavailable.');
  const index = await root.text();
  result.checks.hostIndex = true;
  result.checks.pluginInBootIndex = index.includes('dsh-api-mri');
  const request = (method = 'GET', body) => fetch(hostUrl + '/api/api-mri/runtime', {
    method, headers: { Cookie: cookie, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: timeout(),
  });
  const rejected = await fetch(hostUrl + '/api/api-mri/runtime', { signal: timeout() });
  result.checks.unauthenticatedRejected = [401, 403].includes(rejected.status);
  const start = await request('POST', { embedOrigin: 'dsh-app://app' });
  if (!start.ok) throw new Error(`Authenticated startup route returned ${start.status}.`);
  let state = await start.json();
  const readyDeadline = Date.now() + 60000;
  while (state.phase !== 'ready' && Date.now() < readyDeadline) {
    if (state.phase === 'error') throw new Error(`Runtime reported failure: ${String(state.error).slice(0, 200)}`);
    await delay(300);
    const poll = await request();
    if (!poll.ok) throw new Error(`Startup polling returned ${poll.status}.`);
    state = await poll.json();
  }
  if (state.phase !== 'ready') throw new Error('Managed API-MRI runtime did not become ready.');
  await recordRuntimePid(); const firstPid = runtimePid;
  result.checks.runtimeStartedAutomatically = true;
  const bootstrap = await fetch(state.url + '/api/bootstrap', { signal: timeout() }).then(value => value.json());
  if (bootstrap.version !== version || !bootstrap.embedding?.origins.includes('dsh-app://app')) throw new Error('API-MRI identity or embed origin mismatch.');
  result.checks.runtimeIdentityAndEmbedding = true;
  const app = await fetch(state.url + '/app.js', { signal: timeout() }).then(value => value.text());
  result.checks.configuredModelUiPresent = app.includes('api-mri:dsh:catalog') && app.includes('authorizeDSH');
  const models = await fetch(hostUrl + '/api/api-mri/models', { headers: { Cookie: cookie }, signal: timeout() });
  if (!models.ok) throw new Error(`Configured-model catalog returned ${models.status}.`);
  const catalog = await models.json();
  if (!Array.isArray(catalog.models)) throw new Error('Configured-model catalog was malformed.');
  result.checks.modelCatalog = true; result.configuredModelsInIsolatedProfile = catalog.models.length;
  let repeat = await request('POST', { embedOrigin: 'dsh-app://app' }).then(value => value.json());
  const repeatDeadline = Date.now() + 10000;
  while (!['ready', 'error'].includes(repeat.phase) && Date.now() < repeatDeadline) {
    await delay(200); repeat = await request().then(value => value.json());
  }
  await recordRuntimePid();
  result.repeat = { phase: repeat.phase, sameUrl: repeat.url === state.url, samePid: runtimePid === firstPid };
  if (repeat.phase !== 'ready' || repeat.url !== state.url || runtimePid !== firstPid) throw new Error('Repeated entry failed to reuse its healthy runtime.');
  result.checks.repeatedEntryReusesRuntime = true;
  result.status = 'passed';
} catch (error) {
  result.error = String(error.message).replace(/([?&]token=)[^\s]+/g, '$1[redacted]');
  // Source-only startup diagnostics, never host output or random credentials.
  result.hostExitCode = child?.exitCode;
} finally {
  await recordRuntimePid();
  if (runtimePid) { try { process.kill(runtimePid); } catch {} }
  if (child && child.exitCode === null) child.kill();
  await delay(500);
  result.ownedProcessesStopped = true;
  // Only remove this exact, newly-created test tree under the temp directory.
  const safeRoot = path.resolve(isolated);
  if (path.dirname(safeRoot) !== path.resolve(tmpdir()) || !path.basename(safeRoot).startsWith('api-mri-host-live-')) throw new Error('Test cleanup target escaped temp root.');
  try { await rm(safeRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }); }
  catch { result.fixtureRetainedForLockedFiles = true; }
}
const outIndex = process.argv.indexOf('--out');
if (outIndex >= 0) {
  const target = path.resolve(process.argv[outIndex + 1]);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, JSON.stringify(result, null, 2) + '\n');
}
console.log(JSON.stringify(result));
process.exitCode = result.status === 'passed' ? 0 : 1;
