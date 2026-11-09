/** Isolated installed DSH + its official pi-ai adapter + API-MRI HTTP jobs.
 * No GUI, private profile, external endpoint, or real model inference is used.
 * node dsh-plugin/tests/host-model-e2e.mjs [--plugin-root package] [--runtime API-MRI.exe] [--out result.json]
 */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, readFile, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

const option = name => { const i = process.argv.indexOf(name); return i < 0 ? undefined : process.argv[i + 1]; };
const source = path.resolve(option('--plugin-root') || path.join(path.dirname(fileURLToPath(import.meta.url)), '../..'));
const python = path.resolve(option('--python') || path.join(source, '.core-venv/Scripts/python.exe'));
const runtime = option('--runtime') && path.resolve(option('--runtime'));
const version = JSON.parse(await readFile(path.join(source, 'package.json'), 'utf8')).version;
const installation = path.join(process.env.USERPROFILE, 'AppData/Local/Programs/DeepSeek Harness');
const executable = path.join(installation, 'DeepSeek Harness.exe');
const modules = path.join(installation, 'resources/app.asar/dsh/node_modules');
const cli = path.join(modules, '@deepseek-ai/dsh-desktop-host/lib/cli.js');
const officialAdapter = pathToFileURL(path.join(modules, '@deepseek-ai/dsh-llm-pi-ai/lib/index.js')).href;
const isolated = await mkdtemp(path.join(tmpdir(), 'api-mri-model-e2e-'));
const home = path.join(isolated, 'home'), profile = path.join(home, 'profiles/probe');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const result = { schema: 1, checkedAt: new Date().toISOString(), pluginVersion: version, status: 'failed',
  isolatedProfile: true, privateConfigurationRead: false, realModelCalls: 0,
  workbenchExecution: runtime ? 'packaged-executable' : 'source-python',
  upstream: 'loopback synthetic OpenAI SSE', adapter: 'installed DSH official dsh-llm-pi-ai', checks: {}, measurements: {} };
const processes = [];
let hostOutput = '', apiOutput = '', cookie, hostUrl, apiUrl, upstreamCalls = 0;
const syntheticKey = 'api-mri-synthetic-fixture-not-a-real-key';
const bankPath = runtime
  ? path.join(path.dirname(runtime), '_internal/relay_audit/data/identification/one-token-202607.json')
  : path.join(source, 'src/relay_audit/data/identification/one-token-202607.json');
const bank = JSON.parse(await readFile(bankPath, 'utf8'));
const observations = [];

function answerFor(messages) {
  const cell = bank.cells.find(cell => messages.some(row => row.content === cell.user));
  if (!cell) throw new Error('Unknown synthetic fingerprint prompt.');
  let answer = Object.keys(bank.models.find(model => model.counts[cell.id])?.counts[cell.id] || {})[0];
  if (cell.normalize_as === 'coin' || cell.normalize_as === 'binary') {
    const names = { en: ['heads', 'tails'], ru: ['орёл', 'решка'], zh: ['正面', '反面'], ar: ['صورة', 'كتابة'] };
    answer = names[cell.lang][answer === 'h' ? 0 : 1];
  }
  return answer;
}

const upstream = createServer(async (req, res) => {
  try {
    if (req.url !== '/v1/chat/completions' || req.method !== 'POST') { res.writeHead(404); return res.end(); }
    let raw = ''; for await (const part of req) raw += part;
    const body = JSON.parse(raw); upstreamCalls++;
    assert.equal(req.headers.authorization, `Bearer ${syntheticKey}`);
    observations.push({ reasoning: body.reasoning_effort, maxTokens: body.max_tokens ?? body.max_completion_tokens,
      toolMessages: body.messages.filter(row => row.role === 'tool').length, tools: body.tools?.length || 0 });
    const id = `synthetic-${upstreamCalls}`, created = Math.floor(Date.now() / 1000);
    const frame = (delta, finish = null) => ({ id, object: 'chat.completion.chunk', created, model: body.model,
      choices: [{ index: 0, delta, finish_reason: finish }] });
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
    const emit = value => res.write(`data: ${JSON.stringify(value)}\n\n`);
    emit(frame({ role: 'assistant', content: '' }));
    const user = [...body.messages].reverse().find(row => row.role === 'user')?.content || '';
    const toolResults = body.messages.filter(row => row.role === 'tool');
    let finish = 'stop', outputTokens = 1;
    if (toolResults.length) {
      const value = toolResults.map(row => JSON.parse(row.content)).find(row => row.receipt);
      assert.ok(value?.receipt, 'Roundtrip must contain the actual generated receipt.');
      emit(frame({ content: `${value.value} ${value.unit} ${value.receipt}` })); outputTokens = 12;
    } else if (user.includes('HELLO')) emit(frame({ content: 'HELLO' }));
    else if (body.tools?.length) {
      finish = 'tool_calls';
      const tools = [{ name: 'lookup_sample', args: { sample_id: 'MRI-17', field: 'temperature' } }];
      // Exercise the legitimate optional unit lookup in the automatic-tool task.
      if (body.tools.length > 1) tools.push({ name: 'lookup_unit_legend', args: { unit: 'C' } });
      for (const [index, tool] of tools.entries()) {
        emit(frame({ tool_calls: [{ index, id: `fixture-call-${index}`, type: 'function', function: { name: tool.name, arguments: '' } }] }));
        const args = JSON.stringify(tool.args), cut = Math.floor(args.length / 2);
        emit(frame({ tool_calls: [{ index, function: { arguments: args.slice(0, cut) } }] }));
        emit(frame({ tool_calls: [{ index, function: { arguments: args.slice(cut) } }] }));
      }
      outputTokens = 24;
    } else if (user.includes('integers 1 through 40')) {
      for (let n = 1; n <= 40; n++) { emit(frame({ content: `${n}${n < 40 ? '\n' : ''}` })); await delay(2); }
      outputTokens = 79;
    } else emit(frame({ content: answerFor(body.messages) }));
    emit(frame({}, finish));
    emit({ id, object: 'chat.completion.chunk', created, model: body.model, choices: [],
      usage: { prompt_tokens: 80, completion_tokens: outputTokens, total_tokens: 80 + outputTokens,
        prompt_tokens_details: { cached_tokens: 0 }, completion_tokens_details: { reasoning_tokens: 0 } } });
    res.end('data: [DONE]\n\n');
  } catch (error) {
    result.fixtureError = String(error.message).replaceAll(syntheticKey, '[redacted]');
    if (!res.headersSent) res.writeHead(500);
    res.end();
  }
});

function child(command, args, env, onData) {
  const value = spawn(command, args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  value.stdout.on('data', part => onData(part.toString('utf8')));
  value.stderr.on('data', part => onData(part.toString('utf8')));
  processes.push(value); return value;
}
async function waitUntil(predicate, message, milliseconds = 60000) {
  const end = Date.now() + milliseconds;
  while (Date.now() < end) { if (predicate()) return; await delay(100); }
  throw new Error(message);
}
async function json(url, { method = 'GET', body, headers = {} } = {}) {
  const response = await fetch(url, { method, headers: { ...headers, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(15000) });
  const value = await response.json();
  assert.ok(response.ok, `HTTP ${response.status} at ${new URL(url).pathname} (${body?.mode || 'host'}): ${JSON.stringify(value).replaceAll(syntheticKey, '[redacted]')}`);
  return value;
}

try {
  await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve));
  const upstreamBase = `http://127.0.0.1:${upstream.address().port}/v1`;
  await mkdir(path.join(profile, 'node_modules'), { recursive: true });
  await symlink(source, path.join(profile, 'node_modules/dsh-api-mri'), 'junction');
  const fixture = path.join(profile, 'node_modules/api-mri-synthetic-provider');
  await mkdir(fixture);
  await writeFile(path.join(fixture, 'package.json'), JSON.stringify({ name: 'api-mri-synthetic-provider', version: '1.0.0', type: 'module',
    main: './index.js', dsh: { manifestVersion: 1, bundle: { patch: './cordis.patch.yml' } } }));
  await writeFile(path.join(fixture, 'cordis.patch.yml'), '- insert:\n    - id: api-mri-synthetic-provider\n      name: api-mri-synthetic-provider\n');
  const providers = { 'api-mri-fixture': { api: 'openai-completions', baseURL: upstreamBase,
    apiKeyEnv: 'API_MRI_FIXTURE_KEY', displayName: 'Synthetic integration fixture',
    models: [{ id: 'synthetic', name: 'Synthetic fixture model', contextWindow: 8192, maxTokens: 1024,
      reasoningEfforts: { off: 'none', low: 'low' } }] } };
  await writeFile(path.join(fixture, 'index.js'), `import { apply as register } from ${JSON.stringify(officialAdapter)};
export const name='api-mri-synthetic-provider';
export const inject=['llm'];
export function apply(ctx){
  // The normal host already declares pi-ai's built-in directory. This test
  // contributes only its new route while retaining official dispatch/metadata.
  const llm = new Proxy(ctx.llm,{get(target,key){
    if(key==='registerConfigurableProviders') return entries=>target.registerConfigurableProviders(entries.filter(row=>row.provider==='api-mri-fixture'));
    const value=Reflect.get(target,key); return typeof value==='function'?value.bind(target):value;
  }});
  const scoped = new Proxy(ctx,{get(target,key){
    if(key==='llm') return llm;
    const value=Reflect.get(target,key); return typeof value==='function'?value.bind(target):value;
  }});
  register(scoped,{providers:{get:()=>(${JSON.stringify(providers)})}});
}
`);
  await writeFile(path.join(profile, 'package.json'), JSON.stringify({ name: 'api-mri-isolated-model-e2e', private: true,
    dependencies: { 'dsh-api-mri': `link:${source.replaceAll('\\', '/')}`, 'api-mri-synthetic-provider': 'file:node_modules/api-mri-synthetic-provider' },
    dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', 'api-mri-synthetic-provider', 'dsh-api-mri'] } } }));
  await writeFile(path.join(profile, 'cordis.patch.yml'), '[]\n');
  const env = {};
  for (const name of ['SystemRoot', 'WINDIR', 'COMSPEC', 'PATH', 'Path', 'PATHEXT', 'USERPROFILE', 'TEMP', 'TMP']) if (process.env[name]) env[name] = process.env[name];
  Object.assign(env, { DSH_HOME: home, DSH_AGENTS_HOME: path.join(isolated, 'agents'), APPDATA: path.join(isolated, 'roaming'),
    LOCALAPPDATA: path.join(isolated, 'local'), ELECTRON_RUN_AS_NODE: '1', DSH_PRODUCT_ANALYTICS_OTLP_URL: '', NO_COLOR: '1',
    API_MRI_FIXTURE_KEY: syntheticKey });
  await mkdir(env.DSH_AGENTS_HOME, { recursive: true });
  const host = child(executable, ['--expose-internals', cli, '--profile', 'probe', '--host', '127.0.0.1', '--port', '0', '--no-open'], env,
    value => { hostOutput = (hostOutput + value).slice(-500000); });
  let launch;
  await waitUntil(() => {
    const matches = hostOutput.match(/https?:\/\/(?:127\.0\.0\.1|localhost):\d+[^\s"'<>]*/g) || [];
    launch = matches.map(value => { try { return new URL(value.replace(/\u001b\[[0-9;]*m/g, '')); } catch { return null; } }).find(value => value?.searchParams.has('token'));
    if (host.exitCode !== null) throw new Error('Isolated DSH exited before ready.');
    return launch;
  }, 'Isolated DSH did not provide a launch URL.');
  hostUrl = launch.origin;
  const auth = await fetch(launch.href, { redirect: 'manual', signal: AbortSignal.timeout(10000) });
  cookie = auth.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
  assert.ok(cookie); result.checks.hostAuthentication = true;
  const catalog = await json(hostUrl + '/api/api-mri/models', { headers: { Cookie: cookie } });
  assert.equal(catalog.models.length, 1);
  assert.equal(catalog.models[0].fingerprint_supported, true);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(catalog.apiBase).hostname));
  const model = catalog.models[0].id;
  result.checks.configuredModelViaOfficialAdapter = true;

  let web;
  if (runtime) {
    const data = path.join(isolated, 'runtime-data');
    web = child(runtime, ['--headless', '--no-browser', '--port', '0', '--data-dir', data,
      '--embed-origin', 'dsh-app://app'], env, value => { apiOutput = (apiOutput + value).slice(-500000); });
    const end = Date.now() + 60000;
    while (!apiUrl && Date.now() < end) {
      try {
        const session = JSON.parse(await readFile(path.join(data, '.portable-session.json'), 'utf8'));
        if (session.version === version && Number.isInteger(session.port) && session.port > 0)
          apiUrl = `http://127.0.0.1:${session.port}`;
      } catch {}
      if (web.exitCode !== null) throw new Error('Packaged API-MRI exited before ready.');
      if (!apiUrl) await delay(100);
    }
    assert.ok(apiUrl, 'Packaged API-MRI did not publish its port.');
  } else {
  const pythonEntry = path.join(isolated, 'web.py');
  await writeFile(pythonEntry, `import socket,uvicorn\nfrom relay_audit.web import create_app\ns=socket.socket();s.bind(('127.0.0.1',0))\nprint('API_MRI_TEST_PORT='+str(s.getsockname()[1]),flush=True)\nuvicorn.Server(uvicorn.Config(create_app(${JSON.stringify(path.join(isolated, 'runs'))},embed_origins=['dsh-app://app']),access_log=False,log_level='warning')).run(sockets=[s])\n`);
  web = child(python, [pythonEntry], { ...env, PYTHONPATH: path.join(source, 'src'), PYTHONIOENCODING: 'utf-8' }, value => { apiOutput = (apiOutput + value).slice(-500000); });
  await waitUntil(() => {
    const port = /API_MRI_TEST_PORT=(\d+)/.exec(apiOutput)?.[1];
    if (web.exitCode !== null) throw new Error('Source API-MRI server exited before ready.');
    if (port) apiUrl = `http://127.0.0.1:${port}`;
    return port;
  }, 'Source API-MRI did not bind a port.');
  }
  let bootstrap;
  await waitUntil(() => Boolean(apiUrl), 'Missing API-MRI address.');
  for (let attempt = 0; attempt < 50; attempt++) {
    try { bootstrap = await json(apiUrl + '/api/bootstrap'); break; } catch { await delay(100); }
  }
  assert.ok(bootstrap?.session_token);
  assert.equal(bootstrap.version, version);
  const api = (route, body) => json(apiUrl + route, { ...(body ? { method: 'POST', body } : {}), headers: { 'x-relay-token': bootstrap.session_token } });
  async function completedJob(run, description) {
    let state;
    const end = Date.now() + 60000;
    do { await delay(100); state = await api(`/api/runs/${run.id}`); } while (['queued', 'running'].includes(state.status) && Date.now() < end);
    assert.equal(state.status, 'completed', `${description} job failed: ${state.error || state.progress?.message}`);
    assert.ok(state.reports?.length);
    // The completed envelope is persisted just before the worker releases its lock.
    await delay(50); return state;
  }
  async function job(payload) {
    return completedJob(await api('/api/runs', payload), payload.mode);
  }
  async function authorized(payload) {
    result.stage = payload.mode + ':' + (payload.checks?.join('+') || 'probes');
    const candidate = { api_base: catalog.apiBase, model };
    const before = upstreamCalls;
    const plan = await api('/api/plan', { ...payload, candidate });
    assert.equal(upstreamCalls, before, 'Planning made a model call.');
    const maxRequests = plan.maximum_requests ?? plan.max_requests ?? plan.budget.max_requests;
    const maxOutputTokens = plan.maximum_output_tokens ?? plan.max_output_tokens_total ?? plan.budget.max_output_tokens;
    const lease = await json(hostUrl + '/api/api-mri/lease', { method: 'POST', headers: { Cookie: cookie },
      body: { model, maxRequests, maxOutputTokens, embedOrigin: 'dsh-app://app' } });
    const state = await job({ ...payload, candidate: lease.candidate });
    return { state, plan };
  }
  async function live(payload, reportName, verify) {
    const { state } = await authorized(payload);
    const report = state.reports.find(row => row.name === reportName).report;
    verify(report);
    const evidence = await fetch(`${apiUrl}/api/runs/${state.id}/download/${reportName}/jsonl`).then(value => value.text());
    const before = upstreamCalls;
    result.stage += ':replay';
    const replay = await job({ mode: payload.mode + '_offline', evidence_jsonl: evidence });
    assert.deepEqual(replay.reports[0].report, report, `${payload.mode} replay differs from captured evidence`);
    assert.equal(upstreamCalls, before, 'Offline replay made a model call.');
    return report;
  }
  const speed = await live({ mode: 'service', checks: ['stream'], samples: 2 }, 'service', report => {
    assert.equal(report.service_quality.stream.successful_samples, 2);
    assert.ok(report.service_quality.stream.first_content_ms.median > 0);
    assert.ok(report.service_quality.stream.output_tokens_per_second.median > 0);
  });
  result.checks.speedAndReplay = true;
  result.measurements.speedRequests = speed.cost.request_count;
  const tools = await live({ mode: 'service', checks: ['tools'], max_tokens: 256, reasoning_off: true, tool_roundtrip_choice: 'auto', budget: { max_requests: 4, max_output_tokens: 1024 } }, 'service', report => {
    const value = report.service_quality.tools;
    assert.equal(value.tool_forced.status, 'unsupported');
    assert.equal(value.tool_auto.status, 'passed');
    assert.equal(value.tool_roundtrip.status, 'passed');
    assert.equal(value.tool_no_call.status, 'passed');
  });
  result.checks.toolAutoMultipleCallsRoundtripAndReplay = true;
  result.measurements.toolRequests = tools.cost.request_count;
  const identify = await live({ mode: 'identify', budget: { max_requests: 12, max_output_tokens: 192 } }, 'identification', report => {
    assert.equal(report.collection.failure, null);
    assert.ok(report.cost.request_count === 12 || report.cost.real_requests === 12);
    assert.equal(report.coverage.valid_answers, 12);
    assert.equal(report.coverage.completed_cells, 3);
    assert.ok(report.ranking.length > 0);
    assert.equal(report.identity_decision, 'abstained');
    assert.ok(observations.filter(row => row.maxTokens === 16).length === 12);
    assert.ok(observations.filter(row => row.maxTokens === 16).every(row => row.reasoning === 'none'));
  });
  result.checks.identificationProtocolAndReplay = true;
  result.measurements.identificationRequests = identify.cost.request_count ?? identify.cost.real_requests;
  result.measurements.identificationCompletedCells = identify.coverage.completed_cells;

  result.stage = 'reference_collect:baseline';
  const initialLibrary = await api('/api/identification-library');
  const initialBankId = initialLibrary.active_bank_id;
  const initialModelCount = initialLibrary.bank.model_count;
  assert.ok(initialBankId && Number.isInteger(initialModelCount) && initialModelCount > 0);
  const referenceSource = { kind: 'operator_declared', label: 'Synthetic loopback reference',
    provider: 'api-mri-e2e-fixture', model_alias: catalog.models[0].model || 'synthetic' };
  const beforeCapture = upstreamCalls;
  const captured = await authorized({ mode: 'reference_collect', source: referenceSource,
    budget: { max_requests: 96, max_output_tokens: 1536 } });
  assert.equal(captured.plan.maximum_requests, 96);
  assert.equal(captured.plan.maximum_output_tokens, 1536);
  assert.equal(upstreamCalls - beforeCapture, 96);
  const reference = captured.state.reports.find(row => row.name === 'reference')?.report;
  assert.equal(reference?.kind, 'reference_collection');
  assert.equal(reference.status, 'ready_to_publish');
  assert.equal(reference.reference.can_publish, true);
  assert.equal(reference.reference.cells, 8);
  assert.equal(reference.reference.reference_answers, 64);
  assert.equal(reference.reference.holdout_answers, 32);
  assert.equal(reference.cost.request_count, 96);
  assert.equal(reference.cost.requested_output_tokens, 1536);
  assert.deepEqual(reference.source, referenceSource);
  const referenceEvidence = await fetch(`${apiUrl}/api/runs/${captured.state.id}/download/reference/jsonl`).then(value => value.text());
  assert.equal(referenceEvidence.includes(syntheticKey), false);
  const referenceRecords = referenceEvidence.trim().split('\n').map(line => JSON.parse(line));
  const referenceRequests = referenceRecords.filter(row => row.type === 'request');
  assert.equal(referenceRequests.length, 96);
  assert.equal(referenceRequests.filter(row => row.split === 'reference').length, 64);
  assert.equal(referenceRequests.filter(row => row.split === 'holdout').length, 32);
  assert.ok(observations.slice(-96).every(row => row.maxTokens === 16 && row.reasoning === 'none'));
  result.checks.referenceCapture96ViaOfficialAdapter = true;
  result.measurements.referenceRequests = reference.cost.request_count;
  result.measurements.referenceHoldoutAnswers = reference.reference.holdout_answers;

  result.stage = 'reference_collect:publish';
  const beforePublish = upstreamCalls;
  const published = await api('/api/identification-library/publish', { run_id: captured.state.id });
  const publishedBankId = published.bank_id;
  assert.ok(publishedBankId && publishedBankId !== initialBankId);
  const activeLibrary = await api('/api/identification-library?model=' + encodeURIComponent(referenceSource.model_alias));
  assert.equal(activeLibrary.active_bank_id, publishedBankId);
  assert.equal(activeLibrary.bank.id, publishedBankId);
  assert.equal(activeLibrary.bank.model_count, initialModelCount + 1);
  assert.equal(activeLibrary.coverage.match, 'unconfirmed_alias');
  assert.equal(activeLibrary.coverage.model_id, reference.reference.model);
  assert.equal(activeLibrary.coverage.reference_cells, 8);
  assert.equal((await api('/api/bootstrap')).identification_bank.id, publishedBankId);
  const repeatedPublish = await api('/api/identification-library/publish', { run_id: captured.state.id });
  assert.equal(repeatedPublish.bank_id, publishedBankId);
  assert.equal(repeatedPublish.library.bank.model_count, initialModelCount + 1);
  assert.equal(upstreamCalls, beforePublish, 'Publishing or metadata inspection made a model call.');
  result.checks.referencePublishActiveAndIdempotent = true;
  result.measurements.referenceModelsBeforePublish = initialModelCount;
  result.measurements.referenceModelsAfterPublish = activeLibrary.bank.model_count;

  const beforeRolling = upstreamCalls;
  const rolling = await authorized({ mode: 'identify', bank_id: publishedBankId,
    budget: { max_requests: 12, max_output_tokens: 192 } });
  const rollingReport = rolling.state.reports.find(row => row.name === 'identification')?.report;
  assert.equal(upstreamCalls - beforeRolling, 12);
  assert.equal(rollingReport?.status, 'reference_shortlist');
  assert.equal(rollingReport.identity_decision, 'abstained');
  assert.equal(rollingReport.bank.id, publishedBankId);
  assert.equal(rollingReport.coverage.compared_candidates, initialModelCount + 1);
  assert.equal(rollingReport.coverage.completed_cells, 3);
  assert.equal(rollingReport.coverage.valid_answers, 12);
  assert.equal(rollingReport.calibration.applicable_gate, null);
  const addedCandidate = rollingReport.ranking.find(row => row.model === reference.reference.model);
  assert.ok(addedCandidate, 'Newly published reference did not enter the rolling ranking.');
  assert.equal(addedCandidate.distance, 0, 'Identical synthetic responses must match their own reference distribution.');
  const rollingEvidence = await fetch(`${apiUrl}/api/runs/${rolling.state.id}/download/identification/jsonl`).then(value => value.text());
  assert.equal(JSON.parse(rollingEvidence.split('\n')[0]).bank_snapshot.id, publishedBankId);
  result.checks.publishedRollingIdentification = true;
  result.measurements.publishedRollingIdentificationRequests = rollingReport.cost.request_count;

  result.stage = 'identify:zero-call-recompare';
  const beforeRecompare = upstreamCalls;
  const reordered = await completedJob(await api('/api/identification-library/recompare', {
    run_id: rolling.state.id, bank_id: initialBankId,
  }), 'identify_recompare');
  const reorderedReport = reordered.reports.find(row => row.name === 'identification')?.report;
  assert.equal(upstreamCalls, beforeRecompare, 'Recomparison made a model call.');
  assert.equal(reorderedReport?.reanalysis.model_api_calls, 0);
  assert.equal(reorderedReport.reanalysis.source_bank_id, publishedBankId);
  assert.equal(reorderedReport.reanalysis.target_bank_id, initialBankId);
  assert.equal(reorderedReport.reanalysis.reused_requests, 12);
  assert.equal(reorderedReport.coverage.compared_candidates, initialModelCount);
  assert.equal(reorderedReport.cost.request_count, 0);
  assert.equal(reorderedReport.cost.real_requests, 0);
  assert.equal(reorderedReport.cost.requested_output_tokens, 0);
  assert.deepEqual(reorderedReport.reused_cost, rollingReport.cost);
  const reorderedEvidence = await fetch(`${apiUrl}/api/runs/${reordered.id}/download/identification/jsonl`).then(value => value.text());
  assert.equal(JSON.parse(reorderedEvidence).schema, 'api-mri-identification-recompare/1');
  assert.equal(await fetch(`${apiUrl}/api/runs/${rolling.state.id}/download/identification/jsonl`).then(value => value.text()), rollingEvidence,
    'Recomparison changed the original capture.');
  result.checks.zeroCallRecomparePreservesOriginalEvidence = true;
  result.measurements.recomparisonCalls = upstreamCalls - beforeRecompare;
  result.measurements.reusedFingerprintAnswers = reorderedReport.reanalysis.reused_requests;

  result.stage = 'identify:rolling-and-recompare-offline-replay';
  const beforeReplay = upstreamCalls;
  for (const [evidence, expected] of [[rollingEvidence, rollingReport], [reorderedEvidence, reorderedReport]]) {
    assert.equal(evidence.includes(syntheticKey), false);
    const replay = await job({ mode: 'identify_offline', evidence_jsonl: evidence });
    assert.deepEqual(replay.reports[0].report, expected, 'Rolling/recomparison replay differs from its saved evidence.');
    assert.equal(upstreamCalls, beforeReplay, 'Rolling/recomparison offline replay made a model call.');
  }
  result.checks.rollingAndRecomparisonOfflineReplay = true;
  result.measurements.offlineReplayCalls = upstreamCalls - beforeReplay;
  result.measurements.upstreamSyntheticRequests = upstreamCalls;
  result.checks.actualToolResultsReachedUpstream = observations.some(row => row.toolMessages >= 2);
  assert.ok(result.checks.actualToolResultsReachedUpstream);
  result.status = 'passed';
} catch (error) {
  result.error = String(error.message).replaceAll(syntheticKey, '[redacted]').replace(/([?&]token=)[^\s]+/g, '$1[redacted]');
  // Only retain source/fixture diagnostics; never dump a host launch URL or auth cookie.
  result.apiDiagnostics = apiOutput.replaceAll(isolated, '[fixture]').slice(-2000);
  if (option('--debug')) {
    const redacted = hostOutput.replace(/([?&]token=)[^\s]+/g, '$1[redacted]').replaceAll(syntheticKey, '[redacted]').replaceAll(isolated, '[fixture]');
    result.hostDiagnostics = redacted.slice(-6000);
  }
} finally {
  for (const process of processes.reverse()) if (process.exitCode === null) process.kill();
  upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve));
  await delay(500); result.ownedProcessesStopped = true;
  const target = path.resolve(isolated);
  assert.equal(path.dirname(target), path.resolve(tmpdir()));
  assert.ok(path.basename(target).startsWith('api-mri-model-e2e-'));
  try { await rm(target, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 }); }
  catch { result.fixtureRetainedForLockedFiles = true; }
}
if (option('--out')) await writeFile(path.resolve(option('--out')), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result));
process.exitCode = result.status === 'passed' ? 0 : 1;
