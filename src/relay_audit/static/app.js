"use strict";

// This workbench keeps credentials in the current form/request only. It does
// not use localStorage, sessionStorage, analytics, or third-party resources.
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    token: null,
    probes: [],
    scenarios: [],
    mode: "demo",
    runs: [],
    selectedRun: null,
    selectedReport: null,
    watchedRunId: null,
    pollTimer: null,
    submitting: false,
    records: new Map(),
    cancellationRequests: new Set(),
    samplePrompt: null,
    sampleRecords: null,
    recordRequest: 0,
    toastTimer: null,
  };
  const terminalStatuses = new Set(["completed", "cancelled", "failed"]);
  const modeLabels = { demo: "离线演示", lab: "机制实验台", live: "双端点检测", offline: "已有记录复算" };
  const statusLabels = { queued: "排队中", running: "运行中", completed: "已完成", cancelled: "已取消", failed: "运行失败" };
  const phaseLabels = { queued: "等待开始", preparing: "准备测试题目", collecting: "正在采集回答", sampling: "正在采集回答", comparing: "正在比较回答分布", analyzing: "正在分析记录", analysis: "正在分析记录", completed: "分析完成", cancelled: "本次运行已取消", failed: "本次运行未完成" };
  const reportLabels = { honest: "相同来源对照", substituted: "已知变化对照", comparison: "服务比较报告" };
  const legacyProbeNames = { color_blue: "颜色指令 · 蓝色", color_green: "颜色指令 · 绿色", color_amber: "颜色指令 · 琥珀色" };
  const studyFields = [
    { key: "case_id", label: "案例编号", placeholder: "例如 C-03，便于与案例台账关联" },
    { key: "service_name", label: "服务名称", placeholder: "这次研究的服务" },
    { key: "product", label: "套餐或渠道", placeholder: "具体套餐、渠道或产品" },
    { key: "observed_on", label: "观察日期", type: "date" },
    { key: "source_url", label: "承诺来源链接", placeholder: "https://…", type: "url", wide: true },
    { key: "model_claim", label: "模型承诺", placeholder: "例如：宣称的模型、版本和配置", multiline: true },
    { key: "price_claim", label: "价格或权益原文", placeholder: "保留币种、计费单位和适用条件", multiline: true },
    { key: "route_claim", label: "路由与切换承诺", placeholder: "例如：固定上游、备用模型、自动切换", multiline: true },
    { key: "data_policy_claim", label: "数据处理承诺", placeholder: "例如：输入输出是否留存、是否用于训练", multiline: true },
    { key: "remedy_claim", label: "故障与补救承诺", placeholder: "例如：投诉、退款、余额与服务中断处理", multiline: true },
    { key: "research_question", label: "这次想回答什么问题", placeholder: "例如：宣称固定模型的渠道，表现是否稳定？", multiline: true },
    { key: "notes", label: "备注与待核实线索", placeholder: "其他能帮助解释这次测试的情况", multiline: true, wide: true },
  ];
  const reasons = {
    no_valid_records: "没有可用的请求记录。",
    unverified_reference_kind: "参考来源尚未核实，暂不能据此判断模型一致性。",
    model_declared_mismatch: "两侧填写的模型名称或版本不一致。",
    config_hash_mismatch: "两侧请求参数不一致，需要先对齐配置。",
    overlapping_observation_ids: "两侧包含重复的观测编号，可能重复使用了同一批记录。",
    shared_source_log: "两侧来自同一个日志文件，不能作为独立对照。",
    synthetic_and_real_records_mixed: "合成记录与真实调用记录混在了一起。",
    synthetic_label_and_reference_kind_inconsistent: "合成数据标记与参考来源不一致。",
    invalid_sampling_plan_metadata: "记录中的采样计划格式不完整。",
    inconsistent_sampling_plan_metadata: "同一批记录的采样计划不一致。",
    duplicate_replicate: "同一题目出现了重复的采样序号。",
    missing_prompt: "缺少这道题的回答。",
    failed_requests: "这道题包含失败请求，需要结合完整记录复核。",
    below_engineering_sample_threshold: "成功回答数不足，尚未达到本次分析的采样要求。",
    dependent_or_retried_observations: "记录含重试、缓存或非独立采样标记。",
    unexpected_prompt_outside_plan: "存在原采样计划以外的题目。",
    planned_replicate_coverage_mismatch: "实际采样序号与计划不一致，可能缺少部分记录。",
    replicate_coverage_mismatch: "两侧采样序号没有一一对应。",
    prompt_hash_mismatch: "两侧实际收到的问题内容不一致。",
    completion_exceeds_character_limit_no_truncation_applied: "部分回答超过当前分析的字符上限，未自动截短。",
    all_successful_responses_empty: "两侧成功请求均未返回文字。",
    met_invalid_or_nonfinite_result: "统计计算没有得到有效数值。",
    met_dependency_unavailable: "缺少 Model Equality Testing 依赖，请检查安装。",
    blocked_by_global_evidence_mismatch: "这批记录的来源、参数或采样信息需要先对齐。",
    holm_corrected_distribution_test_rejected: "多题校正后，这道题的回答分布仍表现出差异。",
    statistical_non_rejection: "此次未检出这道题的回答分布差异。",
    power_not_calibrated: "尚未校准真实模型上的检出能力，未检出差异不能确认模型相同。",
    incomplete_or_invalid_observed_prompt_family: "部分题目未能完成有效比较。",
    no_holm_corrected_behavior_difference_detected: "此次未检出通过多题校正的行为差异。",
    individual_signals_do_not_resolve_family_coverage_or_matching_failures: "个别题目有差异信号，但整批记录仍有采样缺失或匹配问题。",
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    node.replaceChildren();
  }

  function textValue(value, fallback = "—") {
    if (value === undefined || value === null || value === "") return fallback;
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function promptName(id) {
    const probe = state.probes.find((item) => item.id === id);
    return probe ? probe.name : (legacyProbeNames[id] || id);
  }

  function numeric(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function number(value) {
    const parsed = numeric(value);
    return parsed === null ? "—" : parsed.toLocaleString("zh-CN");
  }

  function percent(value) {
    const parsed = numeric(value);
    return parsed === null ? "—" : `${(parsed * 100).toFixed(parsed === 1 || parsed === 0 ? 0 : 1)}%`;
  }

  function duration(value) {
    const parsed = numeric(value);
    if (parsed === null) return "—";
    return parsed < 1 ? `${Math.round(parsed * 1000)} ms` : `${parsed.toFixed(2)} s`;
  }

  function dateLabel(value) {
    if (!value) return "时间未记录";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return textValue(value);
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }

  function errorMessage(data) {
    const detail = data && data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((entry) => {
        const location = Array.isArray(entry.loc) ? entry.loc.filter((v) => v !== "body").join(" · ") : "";
        return `${location ? location + "：" : ""}${entry.msg || "输入格式不正确"}`;
      }).join("；");
    }
    return "请求没有完成，请检查本机服务是否仍在运行。";
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...options.headers };
    if (options.method && options.method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-Relay-Token"] = state.token || "";
    }
    let response;
    try {
      response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options, headers });
    } catch (_) {
      throw new Error("连接不到本机服务。请确认启动窗口仍在运行，然后重试。");
    }
    let data;
    try {
      data = await response.json();
    } catch (_) {
      throw new Error("本机服务返回了无法读取的内容，请检查启动窗口中的提示。");
    }
    if (!response.ok) throw new Error(errorMessage(data));
    return data;
  }

  function showError(message, target = "form-error") {
    const node = $(target);
    node.textContent = message;
    node.hidden = !message;
  }

  function toast(message) {
    window.clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    state.toastTimer = window.setTimeout(() => { $("toast").hidden = true; }, 4500);
  }

  function setMode(mode, focus = false) {
    state.mode = mode;
    document.querySelectorAll(".mode-tab").forEach((tab) => {
      const selected = tab.dataset.mode === mode;
      tab.classList.toggle("selected", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      $(`panel-${tab.dataset.mode}`).hidden = !selected;
      if (selected && focus) tab.focus();
    });
    showError("");
    updateButtons();
  }

  function selectedProbes() {
    return Array.from(document.querySelectorAll("input[data-probe-id]:checked"))
      .map((input) => state.probes.find((probe) => probe.id === input.dataset.probeId))
      .filter(Boolean);
  }

  function plan() {
    const prompts = selectedProbes();
    const repeats = Number($("repeats").value);
    const maxTokens = Number($("max-tokens").value);
    const count = prompts.length * repeats * 2;
    const tokens = count * maxTokens;
    return { prompts, repeats, maxTokens, count, tokens };
  }

  function updatePlan() {
    const current = plan();
    const summary = $("request-summary");
    clear(summary);
    summary.append(element("strong", "", number(current.count)), document.createTextNode(` 次请求 · ${current.prompts.length} 题 × ${current.repeats || 0} 次 × 2 个服务`));
    $("token-summary").textContent = `最多 ${number(current.tokens)} 个输出 tokens，另有输入用量；实际费用以服务账单为准。`;
    $("start-live").textContent = `发送 ${number(current.count)} 次请求并开始`;
    if (current.count > 1000 || current.tokens > 250000) {
      $("token-summary").textContent = "本次计划过大：请将请求数降至 1,000 次以内，输出额度上限降至 250,000 tokens 以内。";
    }
    $("toggle-probes").textContent = current.prompts.length === state.probes.length ? "取消全选" : "选择全部";
    updateButtons();
  }

  function hasRunningTask() {
    return state.runs.some((run) => run.id === state.watchedRunId && !terminalStatuses.has(run.status));
  }

  function updateButtons() {
    const busy = !state.token || state.submitting || hasRunningTask();
    $("start-demo").disabled = busy;
    $("start-lab").disabled = busy || !state.scenarios.length;
    $("start-offline").disabled = busy;
    const current = plan();
    $("start-live").disabled = busy || current.prompts.length === 0 || current.count > 1000 || current.tokens > 250000;
  }

  function renderProbes() {
    const container = $("probe-list");
    clear(container);
    state.probes.forEach((probe, index) => {
      const card = element("div", "probe-card");
      const label = element("label");
      const input = element("input");
      input.type = "checkbox";
      input.checked = true;
      input.dataset.probeId = probe.id;
      input.id = `probe-${index}`;
      const copy = element("span");
      copy.append(element("strong", "", probe.name || probe.id));
      if (probe.description) copy.append(element("span", "probe-description", probe.description));
      label.append(input, copy);
      const details = element("details");
      details.append(element("summary", "", "查看题目"), element("pre", "", textValue(probe.input, "题目内容未提供")));
      card.append(label, details);
      container.append(card);
      input.addEventListener("change", updatePlan);
    });
    if (!state.probes.length) container.append(element("p", "subtle", "没有载入预设题目，请检查安装或使用已有记录复算。"));
    updatePlan();
  }

  function selectedScenario() {
    const input = document.querySelector("input[name=lab-scenario]:checked");
    return input ? state.scenarios.find((scenario) => scenario.id === input.value) : null;
  }

  function renderScenarioExplanation() {
    const scenario = selectedScenario();
    if (!scenario) return;
    $("scenario-name").textContent = scenario.name || scenario.id;
    $("scenario-mechanism").textContent = textValue(scenario.mechanism, scenario.description || "");
    $("scenario-signal").textContent = scenario.expected_signal ? `观察重点：${textValue(scenario.expected_signal)}` : "";
  }

  function renderScenarios() {
    const list = $("scenario-list");
    clear(list);
    state.scenarios.forEach((scenario, index) => {
      const label = element("label", "scenario-card");
      const input = element("input");
      input.type = "radio";
      input.name = "lab-scenario";
      input.value = scenario.id;
      input.checked = scenario.id === "substitution" || (index === 0 && !state.scenarios.some((entry) => entry.id === "substitution"));
      const copy = element("span");
      copy.append(element("strong", "", scenario.name || scenario.id), element("small", "", scenario.description || ""));
      label.append(input, copy);
      input.addEventListener("change", renderScenarioExplanation);
      list.append(label);
    });
    if (!state.scenarios.length) list.append(element("p", "subtle", "当前服务没有提供实验情境，请更新安装后重试。"));
    renderScenarioExplanation();
  }

  function renderStudyForm(mode) {
    const details = element("details", "study-form");
    details.append(element("summary", "", "关联到研究案例（可选）"));
    const content = element("div", "study-form-content");
    content.append(element("p", "", "把服务宣称、价格和研究问题与这次检测放在一起。以下内容由你填写，连同报告保存，方便回到具体案例讨论。"));
    const grid = element("div", "study-grid");
    studyFields.forEach((field) => {
      const wrapper = element("div", field.wide ? "wide" : "");
      const id = `${mode}-study-${field.key}`;
      const label = element("label", "", field.label);
      label.htmlFor = id;
      const input = element(field.multiline ? "textarea" : "input");
      input.id = id;
      if (!field.multiline) input.type = field.type || "text";
      if (field.placeholder) input.placeholder = field.placeholder;
      const limits = { case_id: 120, service_name: 200, product: 300, observed_on: 10, source_url: 2048, notes: 4000 };
      input.maxLength = limits[field.key] || 2000;
      input.autocomplete = "off";
      if (field.type === "url") input.spellcheck = false;
      wrapper.append(label, input);
      grid.append(wrapper);
    });
    content.append(grid);
    details.append(content);
    $(`${mode}-study-container`).append(details);
  }

  function studyPayload(mode) {
    const values = {};
    studyFields.forEach(({ key }) => {
      const value = $(`${mode}-study-${key}`).value.trim();
      if (value) values[key] = value;
    });
    return values;
  }

  function renderStudyResult(study, matrix) {
    const values = study && typeof study === "object" ? study : {};
    const populated = studyFields.filter(({ key }) => values[key] !== undefined && values[key] !== null && values[key] !== "");
    $("study-result").hidden = !populated.length;
    const content = $("study-result-content");
    clear(content);
    if (!populated.length) return;
    content.append(element("p", "", "以下是本次填写的案例资料和服务宣称。检测结果可帮助讨论其中可观察的部分；来源、价格口径和数据处理仍需结合资料核实。"));
    const description = element("dl", "study-dl");
    populated.forEach(({ key, label }) => {
      description.append(element("dt", "", label), element("dd", "", textValue(values[key])));
    });
    content.append(description);
    if (Array.isArray(matrix) && matrix.length) {
      const cards = element("div", "claim-cards");
      cards.append(element("h4", "", "从这次观察，走向下一步核实"));
      matrix.forEach((entry) => {
        const card = element("article", "claim-card");
        card.append(element("strong", "", textValue(entry.label, "服务承诺")));
        const observation = element("p");
        observation.append(element("span", "", "本次观察 "), document.createTextNode(textValue(entry.observation)));
        const next = element("p");
        next.append(element("span", "", "继续核实 "), document.createTextNode(textValue(entry.next_evidence)));
        card.append(observation, next);
        cards.append(card);
      });
      content.append(cards);
    }
  }

  function renderLabResult(lab) {
    const container = $("lab-result");
    container.hidden = !lab;
    clear(container);
    if (!lab) return;
    const details = typeof lab.scenario === "object" && lab.scenario !== null ? lab.scenario : lab;
    const scenarioId = typeof lab.scenario === "string" ? lab.scenario : details.id;
    const known = state.scenarios.find((entry) => entry.id === scenarioId) || {};
    const metadata = { ...known, ...details };
    container.append(element("span", "eyebrow", "实验揭示 · 程序设定的中转机制"));
    container.append(element("h4", "", metadata.name || "本次合成实验"));
    if (metadata.mechanism) container.append(element("p", "", textValue(metadata.mechanism)));
    if (metadata.expected_signal) container.append(element("p", "", `观察重点：${textValue(metadata.expected_signal)}`));
    if (lab.reveal) container.append(element("p", "", textValue(lab.reveal)));
    const alternatives = lab.alternatives || metadata.alternatives;
    if (lab.identifiability_demo || lab.measurement_note || alternatives) {
      const discussion = element("details", "lab-discussion");
      discussion.append(element("summary", "", "其他解释与课堂讨论"));
      if (lab.identifiability_demo) discussion.append(element("p", "lab-alt", textValue(lab.identifiability_demo)));
      if (lab.measurement_note) discussion.append(element("p", "lab-alt", textValue(lab.measurement_note)));
      if (alternatives) {
        discussion.append(element("p", "lab-alt", "回到真实服务，还可能有这些解释："));
        const list = element("ul");
        (Array.isArray(alternatives) ? alternatives : [alternatives]).forEach((alternative) => list.append(element("li", "", textValue(alternative))));
        discussion.append(list);
      }
      container.append(discussion);
    }
  }

  function livePayload() {
    const current = plan();
    return {
      mode: "live",
      reference: { endpoint_id: "reference", model_declared: $("reference-model").value.trim(), base_url: $("reference-base").value.trim(), api_key: $("reference-key").value.trim(), reference_kind: $("reference-kind").value },
      candidate: { endpoint_id: "candidate", model_declared: $("candidate-model").value.trim(), base_url: $("candidate-base").value.trim(), api_key: $("candidate-key").value.trim(), reference_kind: "unknown" },
      prompts: current.prompts.map(({ id, input, check }) => ({ id, input, check })),
      repeats: current.repeats,
      max_tokens: current.maxTokens,
      temperature: Number($("temperature").value),
      permutations: 199,
      min_samples: 8,
      study: studyPayload("live"),
    };
  }

  async function submitRun(payload, checkPlan = false) {
    state.submitting = true;
    updateButtons();
    showError("");
    try {
      if (checkPlan) await api("/api/plan", { method: "POST", body: JSON.stringify(payload) });
      const run = await api("/api/runs", { method: "POST", body: JSON.stringify(payload) });
      if (payload.mode === "live") {
        $("reference-key").value = "";
        $("candidate-key").value = "";
      }
      upsertRun(run);
      state.watchedRunId = run.id;
      state.selectedRun = run;
      state.selectedReport = null;
      $("results").hidden = true;
      renderProgress(run);
      renderHistory();
      $("active-run").scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (terminalStatuses.has(run.status)) finishRun(run);
      else schedulePoll(500);
    } catch (error) {
      showError(error.message);
      $("form-error").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } finally {
      state.submitting = false;
      updateButtons();
    }
  }

  function upsertRun(run) {
    if (!run || !run.id) return;
    const index = state.runs.findIndex((item) => item.id === run.id);
    if (index === -1) state.runs.unshift(run);
    else state.runs[index] = run;
    if (state.selectedRun && state.selectedRun.id === run.id) state.selectedRun = run;
  }

  function schedulePoll(delay = 1400) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = window.setTimeout(pollRun, delay);
  }

  async function pollRun() {
    const id = state.watchedRunId;
    if (!id) return;
    try {
      const run = await api(`/api/runs/${encodeURIComponent(id)}`);
      if (state.watchedRunId !== id) return;
      upsertRun(run);
      renderHistory();
      if (state.selectedRun && state.selectedRun.id === id) renderProgress(run);
      if (terminalStatuses.has(run.status)) finishRun(run);
      else schedulePoll();
    } catch (error) {
      $("progress-message").textContent = `${error.message} 5 秒后重新连接。`;
      schedulePoll(5000);
    }
  }

  function renderProgress(run) {
    const progress = run.progress || {};
    const total = numeric(progress.total) || 0;
    const completed = numeric(progress.completed) || 0;
    const percentDone = total > 0 ? Math.min(100, Math.max(0, completed / total * 100)) : (terminalStatuses.has(run.status) ? 100 : 0);
    const finished = terminalStatuses.has(run.status);
    $("active-run").hidden = false;
    $("active-run").classList.toggle("is-processing", !finished);
    $("progress-mode").textContent = `${modeLabels[run.mode] || "检测任务"} · ${statusLabels[run.status] || run.status}`;
    $("progress-title").textContent = finished ? (phaseLabels[run.status] || "分析完成") : (phaseLabels[progress.phase] || "正在运行检测");
    $("progress-message").textContent = textValue(run.error || progress.message, finished ? "记录已保存在历史结果中。" : "正在处理，请稍候。首次加载分析依赖可能需要更久。");
    $("progress-count").textContent = `${number(completed)} / ${number(total)}`;
    $("progress-bar").setAttribute("aria-valuenow", String(Math.round(percentDone)));
    $("progress-bar").setAttribute("aria-valuetext", `${completed} / ${total}`);
    $("progress-bar").firstElementChild.style.width = `${percentDone}%`;
    $("cancel-run").hidden = finished;
    const cancelling = Boolean(run.cancel_requested) || state.cancellationRequests.has(run.id);
    $("cancel-run").disabled = cancelling;
    $("cancel-run").textContent = cancelling ? "正在取消…" : "取消本次运行";
  }

  function finishRun(run) {
    window.clearTimeout(state.pollTimer);
    state.cancellationRequests.delete(run.id);
    if (state.selectedRun && state.selectedRun.id === run.id) {
      renderProgress(run);
      if (Array.isArray(run.reports) && run.reports.length) renderResults(run);
      else $("results").hidden = true;
    }
    updateButtons();
    if (run.status === "completed") toast("分析完成，可以查看逐题结果与两侧回答。");
    if (run.status === "failed") toast("本次运行未完成，请查看进度区域的具体原因。");
  }

  function reasonText(code) {
    if (typeof code !== "string") return textValue(code);
    const pieces = code.split(":");
    let role = "";
    if (["reference", "candidate", "between_roles"].includes(pieces[0])) {
      role = pieces.shift();
    }
    const key = pieces.join(":");
    let explanation = reasons[key];
    if (!explanation && key.startsWith("mixed_")) {
      const fields = { endpoint_id: "服务来源", run_id: "运行批次", model_declared: "模型名称", config_hash: "请求参数", reference_kind: "参考来源类型" };
      explanation = `同一组记录中混入了不同${fields[key.slice(6)] || "配置"}。`;
    }
    if (!explanation && key.startsWith("invalid_record_at_index:")) explanation = `第 ${Number(key.split(":")[1]) + 1} 条记录格式不完整。`;
    if (!explanation && key.startsWith("met_execution_failed:")) explanation = "统计分析未能完成，请检查依赖与采样记录。";
    if (!explanation) return code;
    const prefix = { reference: "参考侧：", candidate: "待测侧：", between_roles: "" }[role] || "";
    return prefix + explanation;
  }

  function reportDisplay(report) {
    if (report.status === "behavior_difference") return { kind: "difference", symbol: "≠", title: "发现行为差异", short: "存在差异", pill: "amber", explanation: "在本次题目与参数下，两侧回答的分布出现了差异。结合下面的任务表现和原始回答，找出值得继续核实的地方。" };
    const complete = report.coverage && report.coverage.complete_observed_family;
    if (complete) return { kind: "", symbol: "≈", title: "此次未检出差异", short: "未检出差异", pill: "teal", explanation: "这组题目中，未检出通过多题校正的回答分布差异。尚不能确认模型相同；更换任务、增加独立采样，可继续积累证据。" };
    return { kind: "incomplete", symbol: "…", title: "目前还不能完整比较", short: "证据不足", pill: "neutral", explanation: "部分记录缺失、采样不足或比较条件未对齐。下面保留已取得的表现与回答，分析依据中列出了需要补齐的地方。" };
  }

  function promptDisplay(result) {
    if (result.status === "behavior_difference") return { label: "存在差异", pill: "amber" };
    if (result.tested) return { label: "未检出差异", pill: "teal" };
    return { label: "尚不能比较", pill: "neutral" };
  }

  function renderResults(run) {
    const reports = Array.isArray(run.reports) ? run.reports : [];
    if (!reports.length) return;
    state.selectedRun = run;
    if (!reports.some((entry) => entry.name === state.selectedReport)) state.selectedReport = reports[0].name;
    $("results").hidden = false;
    $("result-date").textContent = `${modeLabels[run.mode] || "检测任务"} · ${dateLabel(run.created_at)}`;
    const tabs = $("report-tabs");
    clear(tabs);
    tabs.hidden = reports.length < 2;
    reports.forEach((entry, index) => {
      const tab = element("button", "report-tab", reportLabels[entry.name] || entry.name);
      tab.type = "button";
      tab.id = `report-tab-${index}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(entry.name === state.selectedReport));
      tab.tabIndex = entry.name === state.selectedReport ? 0 : -1;
      tab.addEventListener("click", () => selectReport(run, entry.name));
      tab.addEventListener("keydown", (event) => {
        const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === "ArrowRight") next = (index + 1) % reports.length;
        if (event.key === "ArrowLeft") next = (index + reports.length - 1) % reports.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = reports.length - 1;
        selectReport(run, reports[next].name);
        $(`report-tab-${next}`).focus();
      });
      tabs.append(tab);
    });
    const entry = reports.find((report) => report.name === state.selectedReport);
    renderReport(run, entry);
  }

  function selectReport(run, name) {
    state.selectedReport = name;
    state.sampleRecords = null;
    state.samplePrompt = null;
    state.recordRequest += 1;
    renderResults(run);
  }

  function renderMetric(label, left, right, note) {
    const metric = element("div", "metric-card");
    metric.append(element("span", "", label));
    const values = element("div", "metric-values");
    values.append(element("span", "metric-a", left), element("span", "metric-separator", "/"), element("span", "metric-b", right));
    metric.append(values, element("small", "", note));
    return metric;
  }

  function renderReport(run, entry) {
    const report = entry.report || {};
    const display = reportDisplay(report);
    $("result-banner").className = `result-banner ${display.kind}`.trim();
    $("result-banner").querySelector(".result-symbol").textContent = display.symbol;
    $("result-scope").textContent = report.synthetic ? "合成样本 · 演示结果" : "本次采样 · 行为比较";
    $("result-headline").textContent = display.title;
    $("result-explanation").textContent = display.explanation;
    renderLabResult(report.lab);
    renderStudyResult(report.study, report.claim_matrix);
    const diagnostics = report.diagnostics || {};
    const left = (diagnostics.roles || {}).reference || {};
    const right = (diagnostics.roles || {}).candidate || {};
    const metrics = $("metrics");
    clear(metrics);
    metrics.append(
      renderMetric("请求成功率", percent(left.success_rate), percent(right.success_rate), "A 参考 / B 待测"),
      renderMetric("任务通过率", percent((left.checks || {}).pass_rate), percent((right.checks || {}).pass_rate), "仅统计有可执行检查的题目"),
      renderMetric("响应耗时 · 中位数", duration((left.elapsed_seconds || {}).median), duration((right.elapsed_seconds || {}).median), report.synthetic ? "合成数据中的示例值" : "包含采集时的网络与服务等待"),
      renderMetric("成功回答数", number(left.successes), number(right.successes), `${number(left.errors)} / ${number(right.errors)} 次请求失败`),
    );
    const coverage = report.coverage || {};
    $("tested-count").textContent = `${number(coverage.tested_prompts)} / ${number(coverage.family_size)} 题完成比较`;
    const body = $("prompt-results");
    clear(body);
    const results = Array.isArray(report.prompt_results) ? report.prompt_results : [];
    results.forEach((result) => {
      const row = element("tr");
      row.dataset.promptId = result.prompt_id;
      const displayName = promptName(result.prompt_id);
      const name = element("td");
      name.append(element("strong", "", displayName));
      if (displayName !== result.prompt_id) name.append(element("small", "", result.prompt_id));
      const outcome = promptDisplay(result);
      const status = element("td");
      status.append(element("span", `pill ${outcome.pill}`, outcome.label));
      const detail = Array.isArray(result.reasons) && result.reasons.find((code) => !["statistical_non_rejection", "power_not_calibrated", "holm_corrected_distribution_test_rejected"].includes(code));
      if (detail) status.append(element("small", "", reasonText(detail)));
      const counts = result.counts || {};
      const countCell = element("td", "", `${number((counts.reference || {}).successes)} / ${number((counts.candidate || {}).successes)}`);
      const promptDiagnostics = (diagnostics.prompts || []).find((item) => item.prompt_id === result.prompt_id);
      const roles = promptDiagnostics ? promptDiagnostics.roles || {} : {};
      const checkCell = element("td", "", `${percent(((roles.reference || {}).checks || {}).pass_rate)} / ${percent(((roles.candidate || {}).checks || {}).pass_rate)}`);
      const action = element("td");
      const button = element("button", "text-button", "查看回答 →");
      button.type = "button";
      button.setAttribute("aria-label", `查看 ${displayName} 的两侧回答`);
      button.addEventListener("click", () => showSamples(run, entry.name, result.prompt_id));
      action.append(button);
      row.append(name, status, countCell, checkCell, action);
      body.append(row);
    });
    if (!results.length) {
      const cell = element("td", "subtle", "本次没有可展示的逐题结果，请查看分析依据。");
      cell.colSpan = 5;
      const row = element("tr");
      row.append(cell);
      body.append(row);
    }
    $("sample-viewer").hidden = true;
    const list = $("result-reasons");
    clear(list);
    const combinedReasons = Array.from(new Set(Array.isArray(report.reasons) ? report.reasons : []));
    if (!combinedReasons.length) list.append(element("li", "", "全部题目已按记录中的采样计划完成比较。"));
    combinedReasons.forEach((code) => list.append(element("li", "", reasonText(code))));
    const settings = report.settings || {};
    const methodSettings = $("method-settings");
    clear(methodSettings);
    [`重排 ${number(settings.permutations)} 次`, `每侧最低 ${number(settings.min_samples)} 个样本`, `显著性水平 ${numeric(settings.alpha) === null ? "—" : settings.alpha}`, `多题校正：Holm`, `随机种子 ${number(settings.seed)}`].forEach((label) => methodSettings.append(element("span", "", label)));
    const serviceDetails = element("p", "subtle");
    serviceDetails.textContent = `P95 响应耗时 A / B：${duration((left.elapsed_seconds || {}).p95)} / ${duration((right.elapsed_seconds || {}).p95)}。${report.synthetic ? "以下为合成数据中的计量示例" : "服务端自报计量"}：输入 ${number((left.reported_tokens || {}).input_tokens)} / ${number((right.reported_tokens || {}).input_tokens)}，输出 ${number((left.reported_tokens || {}).output_tokens)} / ${number((right.reported_tokens || {}).output_tokens)} tokens。`;
    methodSettings.append(serviceDetails);
    const exports = $("export-buttons");
    clear(exports);
    [["md", "报告 MD"], ["json", "数据 JSON"], ["csv", "逐题 CSV"]].forEach(([format, label]) => {
      const link = element("a", "button quiet", `↓ ${label}`);
      link.href = `/api/runs/${encodeURIComponent(run.id)}/download/${encodeURIComponent(entry.name)}/${format}`;
      link.setAttribute("download", "");
      link.setAttribute("aria-label", `下载${label}`);
      exports.append(link);
    });
  }

  async function showSamples(run, reportName, promptId) {
    state.samplePrompt = promptId;
    const requestVersion = ++state.recordRequest;
    const key = `${run.id}/${reportName}`;
    $("sample-viewer").hidden = false;
    $("sample-title").textContent = `${promptName(promptId)} · 回答对照`;
    $("sample-note").textContent = "同一题目、相同采样序号。";
    $("reference-response").textContent = "正在读取记录…";
    $("candidate-response").textContent = "正在读取记录…";
    $("reference-response-meta").textContent = "";
    $("candidate-response-meta").textContent = "";
    clear($("replicate-select"));
    document.querySelectorAll("#prompt-results tr").forEach((row) => row.classList.toggle("selected", row.dataset.promptId === promptId));
    $("sample-viewer").scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      let records = state.records.get(key);
      if (!records) {
        records = await api(`/api/runs/${encodeURIComponent(run.id)}/records?report=${encodeURIComponent(reportName)}`);
        state.records.set(key, records);
      }
      if (requestVersion !== state.recordRequest || !state.selectedRun || state.selectedRun.id !== run.id || state.selectedReport !== reportName) return;
      state.sampleRecords = records;
      const allRows = [...(records.reference || []), ...(records.candidate || [])].filter((record) => record.prompt_id === promptId);
      const replicates = Array.from(new Set(allRows.map((record) => record.replicate))).sort((a, b) => a - b);
      const select = $("replicate-select");
      clear(select);
      replicates.forEach((value) => {
        const option = element("option", "", `第 ${Number(value) + 1} 次`);
        option.value = value;
        select.append(option);
      });
      if (!replicates.length) {
        $("sample-note").textContent = "这道题没有可展示的原始记录。";
        $("reference-response").textContent = "暂无记录。";
        $("candidate-response").textContent = "暂无记录。";
      } else renderSamplePair();
    } catch (error) {
      if (requestVersion !== state.recordRequest) return;
      $("reference-response").textContent = error.message;
      $("candidate-response").textContent = "原始记录暂时无法读取，可先下载报告。";
    }
  }

  function renderSamplePair() {
    if (!state.sampleRecords) return;
    const replicate = Number($("replicate-select").value);
    ["reference", "candidate"].forEach((role) => {
      const rows = state.sampleRecords[role] || [];
      const record = rows.find((entry) => entry.prompt_id === state.samplePrompt && Number(entry.replicate) === replicate);
      if (!record) {
        $(`${role}-response`).textContent = "这个采样序号没有对应记录。";
        $(`${role}-response-meta`).textContent = "缺失";
        return;
      }
      const success = record.status === "success";
      const response = success ? textValue(record.response, "（返回了空文本）") : textValue(record.error, "这次请求失败，详见导出记录。");
      $(`${role}-response`).textContent = response;
      $(`${role}-response-meta`).textContent = `${success ? "成功" : "失败"} · ${duration(record.elapsed_seconds)}`;
    });
  }

  function renderHistory() {
    $("history-count").textContent = String(state.runs.length);
    $("history-empty").hidden = state.runs.length > 0;
    const list = $("history-list");
    clear(list);
    [...state.runs].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || ""))).forEach((run) => {
      const item = element("div", "history-item");
      item.append(element("div", "history-icon", run.mode === "demo" ? "▷" : run.mode === "lab" ? "⚗" : run.mode === "offline" ? "↥" : "⇄"));
      const description = element("div", "history-description");
      const modeLabel = modeLabels[run.mode] || "检测任务";
      const reports = Array.isArray(run.reports) ? run.reports : [];
      const studyReport = reports.find((entry) => entry.report && entry.report.study);
      const labReport = reports.find((entry) => entry.report && entry.report.lab && entry.report.lab.scenario);
      let title = modeLabel;
      if (studyReport) {
        const study = studyReport.report.study;
        title = [study.case_id, study.service_name].filter(Boolean).map((value) => textValue(value)).join(" · ") || modeLabel;
      } else if (labReport) {
        const scenario = labReport.report.lab.scenario;
        const metadata = typeof scenario === "object" ? scenario : state.scenarios.find((entry) => entry.id === scenario);
        title = metadata && metadata.name ? metadata.name : modeLabel;
      }
      description.append(element("strong", "", title));
      const reportCount = Array.isArray(run.reports) ? run.reports.length : 0;
      description.append(element("p", "", `${title !== modeLabel ? modeLabel + " · " : ""}${dateLabel(run.created_at)}${reportCount ? ` · ${reportCount} 份报告` : ""}`));
      item.append(description, element("span", `pill ${run.status === "completed" ? "teal" : run.status === "failed" ? "red" : "neutral"}`, statusLabels[run.status] || run.status));
      const button = element("button", "text-button", terminalStatuses.has(run.status) ? "打开结果 →" : "查看进度 →");
      button.type = "button";
      button.setAttribute("aria-label", `打开 ${dateLabel(run.created_at)} 的${title}`);
      button.addEventListener("click", () => openRun(run.id));
      item.append(button);
      list.append(item);
    });
  }

  async function openRun(id) {
    try {
      const run = await api(`/api/runs/${encodeURIComponent(id)}`);
      upsertRun(run);
      state.selectedRun = run;
      state.selectedReport = null;
      state.recordRequest += 1;
      renderProgress(run);
      if (Array.isArray(run.reports) && run.reports.length) renderResults(run);
      else $("results").hidden = true;
      if (!terminalStatuses.has(run.status)) {
        state.watchedRunId = run.id;
        schedulePoll(500);
      }
      ($( "results").hidden ? $("active-run") : $("results")).scrollIntoView({ behavior: "smooth", block: "start" });
      updateButtons();
    } catch (error) {
      toast(error.message);
    }
  }

  async function loadHistory(resume = false) {
    const payload = await api("/api/runs");
    state.runs = Array.isArray(payload.runs) ? payload.runs : [];
    renderHistory();
    if (resume) {
      const active = state.runs.find((run) => !terminalStatuses.has(run.status));
      if (active) {
        state.watchedRunId = active.id;
        state.selectedRun = active;
        renderProgress(active);
        schedulePoll(500);
      }
    }
    updateButtons();
  }

  async function initialize() {
    try {
      const bootstrap = await api("/api/bootstrap");
      state.token = bootstrap.session_token;
      state.probes = Array.isArray(bootstrap.probes) ? bootstrap.probes : [];
      state.scenarios = Array.isArray(bootstrap.scenarios) ? bootstrap.scenarios : [];
      $("app-version").textContent = bootstrap.version ? `v${bootstrap.version}` : "";
      renderProbes();
      renderScenarios();
      showError("", "connection-error");
      await loadHistory(true);
    } catch (error) {
      const alert = $("connection-error");
      clear(alert);
      alert.append(document.createTextNode(`${error.message} `));
      const retry = element("button", "text-button", "重新连接");
      retry.type = "button";
      retry.addEventListener("click", initialize);
      alert.append(retry);
      alert.hidden = false;
    }
    updateButtons();
  }

  document.querySelectorAll(".mode-tab").forEach((tab, index, tabs) => {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      setMode(tabs[next].dataset.mode, true);
    });
  });

  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((entry) => entry.classList.toggle("active", entry === link));
    });
  });

  $("start-demo").addEventListener("click", () => submitRun({ mode: "demo", repeats: 12, permutations: 199 }));
  $("panel-lab").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.submitting || hasRunningTask() || !event.currentTarget.reportValidity()) return;
    const scenario = selectedScenario();
    if (!scenario) return showError("请选择一个实验情境。");
    await submitRun({ mode: "lab", scenario: scenario.id, repeats: Number($("lab-repeats").value), permutations: 199, min_samples: 8, seed: 11 });
  });
  $("copy-model").addEventListener("click", () => {
    $("candidate-model").value = $("reference-model").value;
    $("candidate-model").focus();
  });
  $("toggle-probes").addEventListener("click", () => {
    const allSelected = selectedProbes().length === state.probes.length;
    document.querySelectorAll("input[data-probe-id]").forEach((input) => { input.checked = !allSelected; });
    updatePlan();
  });
  ["repeats", "max-tokens", "temperature"].forEach((id) => $(id).addEventListener("input", updatePlan));
  $("panel-live").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.submitting || hasRunningTask() || !event.currentTarget.reportValidity()) return;
    const current = plan();
    if (!current.prompts.length) return showError("请至少选择一道测试题目。");
    if (current.count > 1000 || current.tokens > 250000) return showError("请减少题目、重复次数或输出上限，使本次请求不超过 1,000 次、输出额度不超过 250,000 tokens。");
    if ($("reference-model").value.trim() !== $("candidate-model").value.trim()) return showError("两侧模型名称需要一致，才能比较相同承诺下的表现。请核对模型版本；右侧可点击“与参考相同”。");
    await submitRun(livePayload(), true);
  });

  ["reference", "candidate"].forEach((role) => {
    $(`${role}-file`).addEventListener("change", (event) => {
      const file = event.target.files[0];
      $(`${role}-file-name`).textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "尚未选择";
    });
  });
  $("panel-offline").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.submitting || hasRunningTask() || !event.currentTarget.reportValidity()) return;
    const reference = $("reference-file").files[0];
    const candidate = $("candidate-file").files[0];
    if (!reference || !candidate) return showError("请分别选择参考与待测服务的 JSONL 文件。");
    if (reference.size > 2 * 1024 * 1024 || candidate.size > 2 * 1024 * 1024) return showError("单个文件请控制在 2 MB 以内。更大的记录可使用命令行复算。");
    state.submitting = true;
    updateButtons();
    try {
      const [referenceText, candidateText] = await Promise.all([reference.text(), candidate.text()]);
      await submitRun({ mode: "offline", reference_jsonl: referenceText, candidate_jsonl: candidateText, permutations: 199, min_samples: 8, study: studyPayload("offline") });
    } catch (_) {
      showError("未能读取所选文件，请重新选择本机的 JSONL 文件。");
    } finally {
      state.submitting = false;
      updateButtons();
    }
  });

  $("cancel-run").addEventListener("click", async () => {
    const run = state.selectedRun;
    if (!run || terminalStatuses.has(run.status)) return;
    state.cancellationRequests.add(run.id);
    $("cancel-run").disabled = true;
    $("cancel-run").textContent = "正在取消…";
    try {
      const updated = await api(`/api/runs/${encodeURIComponent(run.id)}/cancel`, { method: "POST", body: "{}" });
      upsertRun(updated);
      renderProgress(updated);
      renderHistory();
      if (terminalStatuses.has(updated.status)) finishRun(updated);
      else {
        $("cancel-run").disabled = true;
        $("cancel-run").textContent = "正在取消…";
        schedulePoll(500);
      }
    } catch (error) {
      state.cancellationRequests.delete(run.id);
      $("cancel-run").disabled = false;
      $("cancel-run").textContent = "取消本次运行";
      toast(error.message);
    }
  });
  $("replicate-select").addEventListener("change", renderSamplePair);
  $("refresh-history").addEventListener("click", async () => {
    $("refresh-history").disabled = true;
    try { await loadHistory(); toast("历史列表已更新。"); }
    catch (error) { toast(error.message); }
    finally { $("refresh-history").disabled = false; }
  });

  renderStudyForm("live");
  renderStudyForm("offline");
  initialize();
})();
