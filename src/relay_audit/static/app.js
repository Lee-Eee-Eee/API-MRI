"use strict";

// This workbench keeps credentials in the current form/request only. It does
// not use localStorage, sessionStorage, analytics, or third-party resources.
(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    token: null,
    probes: [],
    scenarios: [],
    referencePacks: [],
    referenceLibrary: null,
    referenceUpdate: null,
    referenceUpdateTimer: null,
    referenceUpdateRequest: false,
    referenceUpdatePollFailed: false,
    referenceUpdateCancelling: false,
    mode: "identify",
    identificationBank: null,
    identificationPlan: null,
    identificationRevision: 0,
    fingerprintPlan: null,
    planRevision: 0,
    planning: false,
    fingerprintReport: null,
    fingerprintPage: 0,
    fingerprintRows: [],
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
  const modeLabels = { identify: "模型候选识别", identify_demo: "候选识别合成演示", identify_offline: "候选识别复算", fingerprint: "单 API 指纹检测", fingerprint_demo: "指纹离线体验", fingerprint_offline: "指纹记录复算", demo: "统计演示", lab: "机制实验台", live: "双端点检测", offline: "已有记录复算" };
  const statusLabels = { queued: "排队中", running: "运行中", completed: "已完成", cancelled: "已取消", failed: "运行失败" };
  const phaseLabels = { queued: "等待开始", preparing: "准备测试题目", collecting: "正在采集回答", sampling: "正在采集回答", comparing: "正在比较回答分布", analyzing: "正在分析记录", analysis: "正在分析记录", completed: "分析完成", cancelled: "本次运行已取消", failed: "本次运行未完成" };
  const reportLabels = { identification: "模型候选报告", fingerprint: "参考指纹报告", honest: "相同来源对照", substituted: "已知变化对照", comparison: "服务比较报告" };
  const legacyProbeNames = { color_blue: "颜色指令 · 蓝色", color_green: "颜色指令 · 绿色", color_amber: "颜色指令 · 琥珀色" };
  const domainNames = {
    chemistry_bp: "化学 · 沸点", chemistry_mp: "化学 · 熔点", physics: "物理 · 材料性质",
    astronomy: "天文", biology: "生物 · 染色体", math: "数学", programming: "编程历史",
    medical: "医学 · 半衰期", pop_culture: "流行文化", internet_culture: "网络文化",
    chinese_history: "中国历史", chinese_geography: "中国地理", chinese_internet: "中文互联网",
    chinese_literature: "中国文学", crypto_params: "密码学参数",
  };
  const domainName = (domain) => domainNames[domain] || domain || "未分组";
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

  function selectedPack() {
    return state.referencePacks.find((pack) => pack.id === $("reference-pack").value) || null;
  }

  function sourceLink(url, label) {
    try {
      const parsed = new URL(url);
      if (!["https:", "http:"].includes(parsed.protocol)) return element("span", "", label);
      const link = element("a", "", label);
      link.href = parsed.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return link;
    } catch (_) { return element("span", "", label); }
  }

  function addDefinition(list, label, value) {
    list.append(element("dt", "", label), element("dd", "", textValue(value)));
  }

  const modelOrder = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
  const referenceFamilies = ["GPT", "Claude", "DeepSeek", "GLM", "Kimi", "Gemini", "Qwen", "Llama", "其他模型"];

  function referenceFamily(pack) {
    const name = `${pack.label || ""} ${pack.model || ""}`.toLowerCase();
    return referenceFamilies.find((family) => family !== "其他模型" && name.includes(family.toLowerCase())) || "其他模型";
  }

  function referenceSelectionSignature(pack) {
    return pack ? JSON.stringify(pack) : "";
  }

  function sortedReferencePacks() {
    return [...state.referencePacks].sort((left, right) => {
      const family = referenceFamilies.indexOf(referenceFamily(left)) - referenceFamilies.indexOf(referenceFamily(right));
      if (family) return family;
      const name = modelOrder.compare(right.label || right.model || "", left.label || left.model || "");
      return name || String(right.generated_at || "").localeCompare(String(left.generated_at || ""));
    });
  }

  function renderReferenceProviders() {
    const select = $("reference-provider");
    const previous = select.value;
    clear(select);
    const all = element("option", "", "全部提供方");
    all.value = "";
    select.append(all);
    [...new Set(state.referencePacks.map((pack) => pack.provider || "未记录"))].sort(modelOrder.compare).forEach((provider) => {
      const option = element("option", "", provider);
      option.value = provider;
      select.append(option);
    });
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  }

  function renderReferencePacks(suggestModel = false) {
    const select = $("reference-pack");
    const previous = select.value;
    const query = $("reference-search").value.trim().toLocaleLowerCase();
    const provider = $("reference-provider").value;
    const visible = sortedReferencePacks().filter((pack) =>
      (!provider || (pack.provider || "未记录") === provider) &&
      (!query || `${pack.label || ""} ${pack.model || ""} ${pack.provider || ""} ${pack.id || ""}`.toLocaleLowerCase().includes(query)));
    clear(select);
    let group = null;
    let family = null;
    visible.forEach((pack) => {
      if (referenceFamily(pack) !== family) {
        family = referenceFamily(pack);
        group = element("optgroup");
        group.label = family;
        select.append(group);
      }
      const option = element("option", "", `${pack.label || pack.model} · ${number(pack.probe_count)} 题`);
      option.value = pack.id;
      group.append(option);
    });
    if (visible.some((pack) => pack.id === previous)) select.value = previous;
    if (!visible.length) {
      const option = element("option", "", state.referencePacks.length ? "没有匹配的参考指纹" : "暂无可用参考指纹");
      option.value = "";
      select.append(option);
      $("reference-card").replaceChildren(element("p", "subtle", state.referencePacks.length ? "试试其他模型关键词，或清除提供方筛选。参考需与待测服务宣称的版本对应。" : "可以抓取公开参考库更新，或使用双端点检测建立对照。"));
    } else {
      renderReferenceCard(suggestModel || previous !== select.value);
    }
    $("reference-match-note").textContent = `${number(visible.length)} / ${number(state.referencePacks.length)} 份参考 · 模型家族分组，较新数字版本优先。选择与服务宣称一致的版本。`;
    if (previous !== select.value) invalidateFingerprintPlan();
    else updateButtons();
  }

  function updateReason(reason) {
    const raw = textValue(reason, "未记录原因");
    const mismatch = /^self_test_(hamming|total|correct)_mismatch:recorded=([^,]+),recomputed=(.+)$/.exec(raw);
    if (mismatch) {
      const label = { hamming: "自测不匹配数", total: "自测有效题数", correct: "自测匹配数" }[mismatch[1]];
      return `${label}无法复现：原记录 ${mismatch[2]}，重新计算 ${mismatch[3]}`;
    }
    if (raw.startsWith("github_rate_limit")) return "GitHub 请求额度已用完，请稍后重试";
    const http = /^upstream_http_(\d{3})$/.exec(raw);
    if (http) return `上游返回 HTTP ${http[1]}，未取得参考文件`;
    const messages = {
      invalid_json: "文件无法解析为 JSON",
      invalid_pack_object: "参考文件的顶层格式不正确",
      invalid_reference_model: "参考模型名称缺失或格式不正确",
      invalid_generated_at: "参考采集日期缺失或格式不正确",
      invalid_calibrated_at: "参考自测日期缺失或格式不正确",
      unsupported_prompt_numbering: "题目编号格式与当前检测方法不兼容",
      unsupported_reference_protocol: "参考使用的接口协议暂不支持",
      invalid_reference_endpoint: "参考接口地址格式不正确",
      invalid_probe_count: "参考题目数量为空或超出支持范围",
      probe_counter_mismatch: "记录的题数与实际题目数量不一致",
      invalid_probe: "题目记录格式不正确",
      invalid_probe_name: "题目名称缺失或格式不正确",
      unsupported_domain: "参考包含当前检测方法尚不支持的题目领域",
      invalid_reference_value: "参考答案缺失或不是有效数值",
      invalid_probe_number: "题目中的数值格式不正确",
      missing_reference_self_test: "缺少该参考模型的完整自测记录",
      unsupported_self_test_settings: "自测提示或参数与当前检测方法不一致",
      invalid_self_test_values: "自测答案格式不完整，无法重新计算",
      insufficient_reference_self_coverage: "自测中可用答案不足，暂不安装",
      self_test_match_vector_mismatch: "逐题匹配记录无法由原始自测答案复现",
      invalid_provider_map: "提供方记录格式不正确",
      invalid_provider: "提供方名称格式不正确",
      git_blob_hash_mismatch: "下载文件的校验值与上游目录记录不一致",
      tree_size_mismatch: "下载文件大小与上游目录记录不一致",
      immutable_reference_path_conflict: "同一参考版本的文件内容发生变化，已保留原记录",
      upstream_license_missing: "上游未提供所需的许可文件",
      upstream_license_changed: "上游许可文件已变化，需要人工核对后再更新",
      cached_license_changed: "本机缓存的许可文件校验失败",
      upstream_timeout: "连接上游超时，请稍后重试",
      upstream_network_error: "未能连接上游，请检查网络后重试",
      upstream_redirect_rejected: "上游要求跳转到其他地址，已停止获取",
      update_time_limit: "本次更新已达到时间上限，可再次抓取继续检查",
      cancelled: "更新已取消",
      reference_update_already_running: "另一项参考库更新正在运行",
      no_compatible_reference_directory: "公开目录中尚未找到兼容的参考数据",
      invalid_or_truncated_upstream_tree: "上游目录记录不完整，无法核对参考文件",
      invalid_resolved_commit: "未能取得有效的上游版本编号",
      invalid_commit: "参考版本编号格式不正确",
      unsupported_source_path: "文件不在受支持的公开参考目录中",
      untrusted_upstream_url: "获取地址不属于已配置的公开数据来源",
      unsupported_tree_entry_or_size: "文件类型或大小超出当前支持范围",
      pack_count_limit: "本次检查已达到参考数量上限",
      pack_size_limit: "参考文件为空或超过大小上限",
      response_size_limit: "上游返回内容超过大小上限",
      catalog_size_or_path_limit: "缓存目录文件的路径或大小未通过检查",
      invalid_catalog: "缓存目录无法读取或格式不正确",
      invalid_catalog_entry: "缓存目录中的参考条目格式不正确",
      cached_catalog_shadows_installed_reference: "缓存条目与已安装的同名参考冲突，已使用安装版本",
      cached_file_unreadable: "缓存文件无法读取，已跳过",
      unsafe_filename: "参考文件名未通过检查",
      unsafe_library_directory: "本机参考缓存目录未通过路径检查",
      unsafe_update_lock: "更新锁文件未通过检查",
      library_write_error: "参考文件未能保存到本机",
      library_read_or_write_error: "本机参考目录读取或保存失败",
      update_summary_write_error: "更新摘要未能保存到本机",
      excessive_structure: "参考数据结构过于复杂，超出处理上限",
      excessive_string: "参考字段长度超出处理上限",
      non_finite_number: "参考包含无效数值",
      invalid_object_key: "参考包含格式不正确的字段名",
      "Reference pack path or size check failed": "缓存参考的路径或大小未通过检查",
      "Reference pack integrity check failed": "缓存参考文件的完整性校验失败",
      "Reference pack catalog does not match its contents": "缓存目录记录与参考文件内容不一致",
      "Unknown reference pack; select an installed reference pack": "该参考尚未安装，请选择本机已有参考",
    };
    return messages[raw] || raw;
  }

  function appendUpdateReason(item, reason) {
    const raw = textValue(reason, "未记录原因");
    const readable = updateReason(reason);
    item.append(element("span", "", readable));
    if (readable !== raw) {
      const detail = element("details");
      detail.append(element("summary", "", "技术码"), element("code", "", raw));
      item.append(detail);
    }
  }

  function applyReferenceLibrary(library) {
    if (!library || !Array.isArray(library.reference_packs)) return;
    const previous = selectedPack();
    const previousSignature = referenceSelectionSignature(previous);
    state.referenceLibrary = library;
    state.referencePacks = library.reference_packs;
    const warnings = Array.isArray(library.warnings) ? library.warnings : [];
    $("reference-cache-warning").hidden = !warnings.length;
    $("reference-cache-warning-message").textContent = warnings.length ? `${number(warnings.length)} 个缓存文件未能读取，已跳过。其余参考仍可使用，可重新抓取更新。` : "";
    const warningList = $("reference-cache-warning-list");
    clear(warningList);
    warnings.forEach((warning) => {
      const item = element("li");
      item.append(element("strong", "", warning.filename || "缓存文件"));
      appendUpdateReason(item, warning.reason || "文件未通过检查");
      warningList.append(item);
    });
    renderReferenceProviders();
    renderReferencePacks(!previous);
    if (previousSignature !== referenceSelectionSignature(selectedPack())) invalidateFingerprintPlan();
    if (library.last_update) renderReferenceUpdate(library.last_update);
  }

  function referenceUpdateRunning() {
    return Boolean(state.referenceUpdate && ["queued", "running"].includes(state.referenceUpdate.status));
  }

  function referenceUpdateRows(title, rows, success = false) {
    if (!Array.isArray(rows) || !rows.length) return null;
    const section = element("section", "reference-update-list");
    section.append(element("h5", "", `${title} · ${number(rows.length)}`));
    const list = element("ul");
    rows.forEach((row) => {
      if (typeof row === "string") return list.append(element("li", "", row));
      const item = element("li");
      item.append(element("strong", "", row.label || row.model || row.filename || row.id || "参考文件"));
      if (row.reason) appendUpdateReason(item, row.reason);
      else if (success && row.generated_at) item.append(element("span", "", `采集 ${String(row.generated_at).slice(0, 10)}`));
      list.append(item);
    });
    section.append(list);
    return section;
  }

  function renderReferenceUpdate(job) {
    if (!job) return;
    // A persisted last_update may be the result itself rather than a job wrapper.
    const result = job.result || (Array.isArray(job.added) || Array.isArray(job.catalog) ? job : null);
    const active = ["queued", "running"].includes(job.status);
    const progress = job.progress || {};
    const completed = numeric(progress.completed) ?? numeric(progress.checked) ?? 0;
    const total = numeric(progress.total) ?? 0;
    const ratio = total > 0 ? Math.min(100, Math.max(0, completed / total * 100)) : 0;
    const message = $("reference-update-message");
    if (active) message.textContent = progress.message || (job.status === "queued" ? "更新任务已排队，准备读取公开目录…" : `正在检查参考文件${total ? ` · ${number(completed)} / ${number(total)}` : "…"}`);
    else if (result) {
      const added = Array.isArray(result.added) ? result.added.length : 0;
      const existing = Array.isArray(result.existing) ? result.existing.length : 0;
      const failed = Array.isArray(result.failed) ? result.failed.length : 0;
      const skipped = Array.isArray(result.skipped) ? result.skipped.length : 0;
      const prefix = job.status === "cancelled" || result.status === "cancelled" ? "更新已取消" : job.status === "failed" || result.status === "failed" ? "更新未完成" : "参考库检查完成";
      message.textContent = `${prefix} · 新增 ${number(added)}，已有 ${number(existing)}${skipped ? `，跳过 ${number(skipped)}` : ""}${failed ? `，失败 ${number(failed)}` : ""}`;
    } else if (job.status === "failed") message.textContent = "更新未完成，可重新抓取。";
    else if (job.status === "cancelled") message.textContent = "更新已取消，已保存的参考仍可使用。";
    const checkedAt = (result && result.checked_at) || job.checked_at || job.finished_at || job.completed_at;
    if (checkedAt) $("reference-update-time").textContent = `最近检查 ${dateLabel(checkedAt)}`;
    const bar = $("reference-update-progress");
    bar.hidden = !active;
    bar.classList.toggle("indeterminate", active && total <= 0);
    bar.setAttribute("aria-valuenow", String(Math.round(ratio)));
    if (total > 0) bar.setAttribute("aria-valuetext", `${completed} / ${total} 个文件`);
    else bar.removeAttribute("aria-valuetext");
    bar.firstElementChild.style.width = `${ratio}%`;
    const error = job.error || (result && result.error);
    showError(error ? updateReason(error) : "", "reference-update-error");
    const details = $("reference-update-details");
    details.hidden = !result;
    if (result) {
      const container = $("reference-update-detail-content");
      clear(container);
      const info = element("dl", "reference-dl");
      addDefinition(info, "上游版本", result.upstream_commit || "未取得");
      addDefinition(info, "检查时间", result.checked_at || checkedAt || "未记录");
      container.append(info);
      [["已新增", result.added, true], ["已存在", result.existing, true], ["已跳过", result.skipped], ["获取失败", result.failed]].forEach(([title, rows, success]) => {
        const section = referenceUpdateRows(title, rows, success);
        if (section) container.append(section);
      });
      $("reference-update-summary").textContent = `查看更新详情${Array.isArray(result.catalog) ? ` · 本机 ${number(result.catalog.length)} 份参考` : ""}`;
    }
    updateButtons();
  }

  function scheduleReferenceUpdatePoll(delay = 900) {
    window.clearTimeout(state.referenceUpdateTimer);
    state.referenceUpdateTimer = window.setTimeout(pollReferenceUpdate, delay);
  }

  async function refreshReferenceLibrary() {
    const library = await api("/api/reference-library");
    applyReferenceLibrary(library);
    return library;
  }

  async function pollReferenceUpdate() {
    try {
      const job = await api("/api/reference-library/update");
      state.referenceUpdatePollFailed = false;
      state.referenceUpdate = job;
      renderReferenceUpdate(job);
      if (referenceUpdateRunning()) scheduleReferenceUpdatePoll();
      else {
        state.referenceUpdateCancelling = false;
        await refreshReferenceLibrary();
        renderReferenceUpdate(job);
        updateButtons();
      }
    } catch (error) {
      state.referenceUpdatePollFailed = true;
      const nextStep = referenceUpdateRunning() ? "点击“重新连接更新任务”可继续查看进度。" : "未能读取更新后的参考列表，可再次抓取更新。";
      showError(`${error.message} ${nextStep}`, "reference-update-error");
      updateButtons();
    }
  }

  async function startReferenceUpdate() {
    if (state.referenceUpdateRequest || !state.token) return;
    if (referenceUpdateRunning() && state.referenceUpdatePollFailed) return pollReferenceUpdate();
    if (referenceUpdateRunning() || hasRunningTask() || state.planning || state.submitting) return;
    state.referenceUpdateRequest = true;
    state.referenceUpdatePollFailed = false;
    showError("", "reference-update-error");
    $("reference-update-message").textContent = "正在启动参考库更新…";
    updateButtons();
    try {
      const job = await api("/api/reference-library/update", { method: "POST", body: "{}" });
      state.referenceUpdate = job;
      renderReferenceUpdate(job);
      scheduleReferenceUpdatePoll(250);
    } catch (error) { showError(error.message, "reference-update-error"); }
    finally { state.referenceUpdateRequest = false; updateButtons(); }
  }

  async function cancelReferenceUpdate() {
    if (!referenceUpdateRunning() || state.referenceUpdateCancelling) return;
    state.referenceUpdateCancelling = true;
    updateButtons();
    try {
      const job = await api("/api/reference-library/update/cancel", { method: "POST", body: "{}" });
      if (job && job.status) { state.referenceUpdate = job; renderReferenceUpdate(job); }
      if (referenceUpdateRunning()) $("reference-update-message").textContent = "已请求取消，正在结束当前文件检查…";
      scheduleReferenceUpdatePoll(250);
    } catch (error) {
      state.referenceUpdateCancelling = false;
      showError(error.message, "reference-update-error");
      updateButtons();
    }
  }

  function renderReferenceCard(suggestModel = false) {
    const pack = selectedPack();
    if (!pack) return;
    const card = $("reference-card");
    clear(card);
    card.append(element("h4", "", "本次使用的参考"));
    const list = element("dl", "reference-dl");
    addDefinition(list, "提供方", pack.provider || "未记录");
    addDefinition(list, "参考题数", `${number(pack.probe_count)} 题`);
    addDefinition(list, "采集日期", pack.generated_at ? String(pack.generated_at).slice(0, 10) : "未记录");
    addDefinition(list, "来源核验", pack.source_verified ? "已核验" : "上游公开记录，尚未独立核验");
    card.append(list);
    const details = element("details");
    details.append(element("summary", "", "来源、版本与文件校验"));
    details.append(element("p", "", `参考模型：${pack.model || pack.label}。自测日期：${pack.calibrated_at ? String(pack.calibrated_at).slice(0, 10) : "未记录"}。许可：${textValue(pack.license)}。`));
    if (pack.source_url) details.append(sourceLink(pack.source_url, "查看上游参考文件 ↗"));
    details.append(element("code", "", `SHA-256 ${textValue(pack.sha256)}`));
    card.append(details);
    if (suggestModel) $("fingerprint-model").value = pack.model || "";
  }

  function invalidateFingerprintPlan() {
    state.planRevision += 1;
    state.fingerprintPlan = null;
    $("fingerprint-plan").hidden = true;
    $("fingerprint-plan-note").textContent = "核对所选题目和采样预算，再开始调用。实际费用以服务账单为准。";
    $("start-fingerprint").textContent = fingerprintProfile() === "screening" ? "开始初筛 →" : "开始完整比较 →";
    updateButtons();
  }

  function fingerprintProfile() {
    return document.querySelector('input[name="fingerprint-profile"]:checked').value;
  }

  const profileBudgets = { screening: ["4", "4096"], full: ["", ""] };
  let activeProfile = "screening";
  function changeFingerprintProfile() {
    profileBudgets[activeProfile] = [$("fingerprint-budget-requests").value, $("fingerprint-budget-output").value];
    activeProfile = fingerprintProfile();
    [$("fingerprint-budget-requests").value, $("fingerprint-budget-output").value] = profileBudgets[activeProfile];
    $("fingerprint-budget-requests").placeholder = activeProfile === "full" ? "完整计划默认值" : "4";
    $("fingerprint-budget-output").placeholder = activeProfile === "full" ? "完整计划默认值" : "4096";
    $("fingerprint-budget-help").textContent = activeProfile === "screening"
      ? "默认最多 4 次请求、4,096 输出配额。到达任一上限就停止；输入与服务端额外计量仍需查看账单。"
      : "留空使用完整参考包的计划上限，也可填写更低预算。全量比较通常需要几十至上百次请求，预算不足时仅保存部分证据。";
    invalidateFingerprintPlan();
  }

  function fingerprintPayload() {
    const budget = {};
    if ($("fingerprint-budget-requests").value !== "") budget.max_requests = Number($("fingerprint-budget-requests").value);
    if ($("fingerprint-budget-output").value !== "") budget.max_output_tokens = Number($("fingerprint-budget-output").value);
    return {
      mode: "fingerprint",
      reference_pack: $("reference-pack").value,
      profile: fingerprintProfile(),
      budget,
      candidate: { api_base: $("fingerprint-base").value.trim(), model: $("fingerprint-model").value.trim(), api_key: $("fingerprint-key").value.trim() },
      timeout: Number($("fingerprint-timeout").value),
      study: studyPayload("fingerprint"),
    };
  }

  async function prepareFingerprintPlan() {
    if (state.planning || state.submitting || hasRunningTask() || referenceUpdateRunning() || state.referenceUpdateRequest || !$("panel-fingerprint").reportValidity()) return;
    const revision = state.planRevision;
    state.planning = true;
    $("prepare-fingerprint").textContent = "核对中…";
    showError("");
    updateButtons();
    try {
      const result = await api("/api/plan", { method: "POST", body: JSON.stringify(fingerprintPayload()) });
      if (revision !== state.planRevision) return;
      state.fingerprintPlan = result;
      const box = $("fingerprint-plan");
      clear(box);
      const screening = result.profile === "screening";
      box.append(element("h4", "", `${screening ? "低成本初筛" : "完整参考比较"}计划 · 尚未调用模型`));
      const metrics = element("div", "plan-metrics");
      [["本次题目 / 全包题目", `${number(result.probe_count)} / ${number(result.reference_probe_count ?? result.probe_count)}`], ["本次请求上限", number(result.max_requests ?? result.request_count)], ["本次输出配额上限", number(result.max_output_tokens_total)]].forEach(([label, value]) => {
        const item = element("div");
        item.append(element("span", "", label), element("strong", "", value));
        metrics.append(item);
      });
      box.append(metrics);
      box.append(element("p", "", `${number(result.batch_count)} 批固定探针，每批最多 ${number(result.batch_size)} 题。参数适配与重试也占用上述预算。输出配额按请求上限累计，实际 token 用量另记。`));
      if (result.budget) box.append(element("p", "", `你设置的预算：最多 ${number(result.budget.max_requests)} 次请求、${number(result.budget.max_output_tokens)} 输出配额；计划上限同时受所选探针数量限制。`));
      const selection = result.selection || {};
      if (Array.isArray(selection.domains) && selection.domains.length) box.append(element("p", "plan-selection", `覆盖领域：${selection.domains.map(domainName).join("、")}。题目在调用前确定。`));
      if (result.budget_can_complete_minimum === false) {
        box.append(element("p", "plan-budget-warning", `当前预算低于完成计划的最低需求（${number(result.minimum_requests)} 次请求、${number(result.minimum_output_tokens_total)} 输出配额）。可提高预算，或按此预算只保存已取得的部分回答。`));
      }
      if (Array.isArray(result.notes) && result.notes.length) {
        const notes = element("ul");
        result.notes.forEach((note) => notes.append(element("li", "", textValue(note))));
        box.append(notes);
      }
      box.hidden = false;
      $("fingerprint-plan-note").textContent = "开始后会使用待测账户额度。可随时取消，已发出的请求仍可能计费。";
      $("start-fingerprint").textContent = `${screening ? "开始初筛" : "开始完整比较"} · 最多 ${number(result.max_requests ?? result.request_count)} 次`;
      box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) { showError(error.message); }
    finally {
      state.planning = false;
      $("prepare-fingerprint").textContent = "核对采样计划";
      updateButtons();
    }
  }

  function fingerprintDisplay(report) {
    if (report.profile === "screening") {
      const outcome = ((report.fingerprint || {}).screening || report.screening || {}).outcome;
      if (outcome === "incomplete") return { kind: "screening", symbol: "…", title: "初筛未完成，已保留部分回答", explanation: "预算、采集或解析条件限制了这次观察。下方可检查已完成的题目、缺失项和停止原因。" };
      return { kind: "screening", symbol: "·", title: "初筛完成，尚不能判断模型身份", explanation: outcome === "differences_observed" ? "少量探针中观察到与参考不同的回答。先检查具体差异，再决定是否值得投入更多调用复核。" : "已取得的回答中未观察到差异。少量题目相同仍可能来自不同模型，需要更多证据才能判断。" };
    }
    if (report.status === "difference_detected") return { kind: "difference", symbol: "≠", title: "发现显著差异", explanation: "待测回答与参考指纹的偏离超过本次检验阈值。查看具体题目，再结合版本、配置与服务承诺复核。" };
    if (report.status === "no_difference_detected") return { kind: "", symbol: "≈", title: "未检出明显差异", explanation: "本次探针未发现超出参考自测误差的明显偏离。这是一次行为检验结果，不能单独确认服务端模型身份。" };
    return { kind: "incomplete", symbol: "…", title: "证据尚不充分", explanation: "可解析回答不足，或记录未满足本次检验条件。已取得的回答保留在下方，可据此检查失败与缺失。" };
  }

  function fingerprintMetric(label, value, note) {
    const metric = element("div", "metric-card");
    const rendered = element("div", "metric-values");
    if (/[\u4e00-\u9fff]/.test(value)) rendered.append(element("span", "metric-text", value));
    else rendered.textContent = value;
    metric.append(element("span", "", label), rendered, element("small", "", note));
    return metric;
  }

  function probability(value) {
    const n = numeric(value);
    if (n === null) return "—";
    if (n === 0) return "低于计算精度";
    return n < 0.001 ? n.toExponential(2) : n.toFixed(3);
  }

  function probeAnswer(probe, role) {
    return probe[`${role}_answer`] ?? probe[`${role}_value`] ?? null;
  }

  function probeOutcome(probe) {
    if (probeAnswer(probe, "candidate") === null || typeof probe.match !== "boolean" || ["error", "missing", "unparsed"].includes(probe.status)) return "missing";
    return probe.match === true ? "match" : "mismatch";
  }

  function renderFingerprintCost(report) {
    const box = $("fp-cost");
    clear(box);
    box.hidden = false;
    box.append(element("h4", "", report.synthetic ? "离线演示 · 未调用模型 API" : "本次采样开销"));
    const cost = report.cost || {};
    const usage = cost.usage || {};
    const requestCount = cost.request_count ?? (report.collection || {}).requests;
    const countLabel = numeric(requestCount) === null ? "未记录" : number(requestCount);
    const requestedLabel = numeric(cost.requested_output_tokens) === null ? "未记录" : number(cost.requested_output_tokens);
    const actualLabel = numeric(usage.total_tokens) === null ? "未知" : number(usage.total_tokens);
    const fields = usage.reported_fields || {};
    const totalReported = usage.complete || (numeric(requestCount) !== null && requestCount > 0 && fields.total_tokens === requestCount && numeric(usage.total_tokens) !== null);
    const summary = element("div", "cost-values");
    [[report.synthetic ? "模拟请求" : "实际请求", countLabel], ["已分配输出配额", requestedLabel], [totalReported ? "服务端报告总用量" : "已报告用量小计", actualLabel]].forEach(([label, value]) => {
      const item = element("div");
      item.append(element("span", "", label), element("strong", "", value));
      summary.append(item);
    });
    box.append(summary);
    if (report.synthetic) box.append(element("p", "", "请求与配额数字用于演示采样流程；没有真实 token 消耗。"));
    else if (usage.complete) box.append(element("p", "", `服务端已报告用量：输入 ${numeric(usage.prompt_tokens) === null ? "未知" : number(usage.prompt_tokens)}，输出 ${numeric(usage.completion_tokens) === null ? "未知" : number(usage.completion_tokens)} tokens。输出配额是请求上限，和实际用量分别记录；费用以账单为准。`));
    else {
      const coverage = [["输入", "prompt_tokens"], ["输出", "completion_tokens"], ["总计", "total_tokens"]]
        .filter(([, field]) => numeric(fields[field]) !== null)
        .map(([label, field]) => `${label} ${number(fields[field])}/${countLabel}`);
      const introduction = numeric(usage.reported_requests) !== null ? `${number(usage.reported_requests)} 次请求提供了至少一项用量。` : "这份记录的用量字段尚不完整。";
      const conclusion = totalReported ? "总用量由服务端报告，输入或输出分项仍有缺失；费用以账单为准。" : "实际总用量未知，已报告小计不能代表完整开销。";
      box.append(element("p", "", `${introduction}${coverage.length ? `字段覆盖（响应数）：${coverage.join("，")}。` : ""}${conclusion}`));
    }
    const stopCode = (report.collection || {}).stop_code;
    const stopLabels = { budget_max_requests: "已达到请求次数预算，保留已取得的回答。", budget_max_output_tokens: "剩余输出配额不足以发送下一次请求，保留已取得的回答。" };
    if (stopLabels[stopCode]) box.append(element("p", "budget-stop", stopLabels[stopCode]));
  }

  function renderFingerprintReport(run, entry) {
    const report = entry.report || {};
    const fingerprint = report.fingerprint || {};
    const reference = fingerprint.reference || {};
    const summary = fingerprint.summary || {};
    const screening = report.profile === "screening";
    const display = fingerprintDisplay(report);
    state.fingerprintReport = { run, entry };
    state.fingerprintRows = Array.isArray(fingerprint.probes) ? fingerprint.probes : [];
    state.fingerprintPage = 0;
    state.recordRequest += 1;
    $("fp-banner").className = `result-banner ${display.kind}`;
    $("fp-banner").querySelector(".result-symbol").textContent = display.symbol;
    $("fp-scope").textContent = `${report.synthetic ? "合成样本 · " : ""}${screening ? "低成本初筛 / 描述性比较" : "完整参考比较 / KBF"}`;
    $("fp-headline").textContent = display.title;
    $("fp-explanation").textContent = screening ? display.explanation : fingerprint.verdict_explanation || display.explanation;
    $("fp-metrics").replaceChildren(
      fingerprintMetric("可解析覆盖率", percent(summary.coverage), `${number(summary.answered)} / ${number(summary.total)} 题取得可用答案`),
      fingerprintMetric("回答一致", number(summary.matches), "与参考包记录的回答一致"),
      fingerprintMetric("答案不一致", number(summary.mismatches), "仅统计可解析的答案"),
      screening ? fingerprintMetric("本次观察范围", `${number(summary.total)} 题`, "固定少量探针，尚未校准鉴别能力") : report.status === "insufficient_evidence" ? fingerprintMetric("统计判断", "本次不作判断", "存在缺失或未满足完整比较条件") : fingerprintMetric("统计检验 · p 值", probability(summary.p_value), summary.p_value === 0 ? "计算返回 0，展开设置查看原值" : `显著性阈值 ${textValue((fingerprint.settings || {}).alpha, "0.05")}`),
    );
    renderFingerprintCost(report);
    const context = $("fp-reference-info");
    clear(context);
    context.append(element("strong", "", reference.label || reference.model || "参考指纹"));
    context.append(element("span", "", `提供方 ${textValue(reference.provider)}`));
    context.append(element("span", "", `采集 ${reference.generated_at ? String(reference.generated_at).slice(0, 10) : "日期未记录"}`));
    context.append(element("span", "pill neutral", reference.source_verified ? "来源已核验" : "上游来源尚未独立核验"));
    const domainStrip = $("fp-domains");
    clear(domainStrip);
    (Array.isArray(fingerprint.domains) ? fingerprint.domains : []).forEach((domain) => {
      const item = element("div", "domain-stat");
      const caption = element("div");
      caption.append(element("strong", "", domainName(domain.domain)), element("span", "", `${number(domain.matches)} / ${number(domain.answered)}`));
      const track = element("div", "domain-track");
      const matched = element("span", "matched");
      const mismatched = element("span", "mismatched");
      const total = numeric(domain.total) || 1;
      matched.style.width = `${Math.max(0, Math.min(100, (numeric(domain.matches) || 0) / total * 100))}%`;
      mismatched.style.width = `${Math.max(0, Math.min(100, (numeric(domain.mismatches) || 0) / total * 100))}%`;
      track.append(matched, mismatched);
      item.append(caption, track, element("p", "domain-caption", `一致 / 可解析 · 共 ${number(domain.total)} 题`));
      domainStrip.append(item);
    });
    domainStrip.hidden = !domainStrip.childElementCount;
    $("fp-search").value = "";
    $("fp-outcome").value = "all";
    const domainSelect = $("fp-domain");
    clear(domainSelect);
    const allDomains = element("option", "", "全部领域");
    allDomains.value = "all";
    domainSelect.append(allDomains);
    [...new Set(state.fingerprintRows.map((probe) => probe.domain).filter(Boolean))].forEach((domain) => {
      const option = element("option", "", domainName(domain));
      option.value = domain;
      domainSelect.append(option);
    });
    $("fp-viewer").hidden = true;
    renderFingerprintRows();
    const method = $("fp-method");
    clear(method);
    method.append(element("p", "", screening ? "调用前固定选择跨领域完整批次，逐题描述与参考回答的一致和差异。该小样本方法尚未校准鉴别能力，不计算显著性或身份概率。" : "使用整个参考包，沿用 KBF 的单侧二项检验及参考自测误差上界。实际误报率与检出能力尚待独立校准；未检出差异仍不能证明模型相同。"));
    const metadata = element("dl", "study-dl");
    addDefinition(metadata, "参考包", reference.id || reference.label);
    addDefinition(metadata, "模型 / 提供方", `${textValue(reference.model)} / ${textValue(reference.provider)}`);
    addDefinition(metadata, "参考采集日期", reference.generated_at);
    addDefinition(metadata, "参考自测日期", reference.calibrated_at);
    if (!screening) {
      addDefinition(metadata, "自测错误率", percent(summary.reference_error_rate));
      addDefinition(metadata, "检验使用的误差上界", percent(summary.p0));
      addDefinition(metadata, "p 值（计算原值）", summary.p_value);
    }
    addDefinition(metadata, "检查深度", screening ? "低成本初筛" : "完整参考比较");
    if (fingerprint.selection) addDefinition(metadata, "采样选择", JSON.stringify(fingerprint.selection));
    addDefinition(metadata, "参考包 SHA-256", reference.sha256);
    method.append(metadata);
    if (reference.source_url) method.append(sourceLink(reference.source_url, "查看上游参考文件 ↗"));
    method.append(element("pre", "", JSON.stringify(fingerprint.settings || {}, null, 2)));
    if (report.collection) method.append(element("p", "", `采集状态：${textValue(report.collection.state, report.collection.cancelled ? "已取消" : "已保存")}。实际请求数：${number(report.collection.requests)}。`));
    const study = report.study || {};
    if (Object.keys(study).length) {
      method.append(element("h4", "", "本次关联的研究案例"));
      const studyList = element("dl", "study-dl");
      studyFields.filter(({ key }) => study[key]).forEach(({ key, label }) => addDefinition(studyList, label, study[key]));
      method.append(studyList);
    }
    const exports = $("fp-exports");
    clear(exports);
    [["md", "报告 MD"], ["json", "数据 JSON"], ["csv", "逐题 CSV"], ["jsonl", "原始 JSONL"]].forEach(([format, label]) => {
      const link = element("a", "button quiet", `↓ ${label}`);
      link.href = `/api/runs/${encodeURIComponent(run.id)}/download/${encodeURIComponent(entry.name)}/${format}`;
      link.setAttribute("download", "");
      exports.append(link);
    });
  }

  function filteredFingerprintRows() {
    const search = $("fp-search").value.trim().toLocaleLowerCase();
    const outcome = $("fp-outcome").value;
    const domain = $("fp-domain").value;
    return state.fingerprintRows.filter((probe) => {
      if (outcome !== "all" && probeOutcome(probe) !== outcome) return false;
      if (domain !== "all" && probe.domain !== domain) return false;
      return !search || [probe.prompt, probe.domain, domainName(probe.domain), probe.name, probe.prompt_id, probe.index].join(" ").toLocaleLowerCase().includes(search);
    });
  }

  function renderFingerprintRows() {
    const rows = filteredFingerprintRows();
    const pageSize = 25;
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    state.fingerprintPage = Math.max(0, Math.min(state.fingerprintPage, pages - 1));
    const offset = state.fingerprintPage * pageSize;
    const body = $("fp-probe-results");
    clear(body);
    rows.slice(offset, offset + pageSize).forEach((probe) => {
      const row = element("tr");
      row.dataset.promptId = String(probe.index);
      const name = element("td");
      const prompt = textValue(probe.prompt, probe.name || probe.prompt_id);
      name.append(element("strong", "", prompt.length > 125 ? `${prompt.slice(0, 125)}…` : prompt));
      name.append(element("small", "", `${probe.prompt_id || `#${Number(probe.index) + 1}`} · ${domainName(probe.domain)} / ${textValue(probe.domain)}`));
      const result = probeOutcome(probe);
      const status = element("td");
      status.append(element("span", `pill ${result === "match" ? "teal" : result === "mismatch" ? "amber" : "neutral"}`, result === "match" ? "一致" : result === "mismatch" ? "不一致" : "无可用答案"));
      const action = element("td");
      const button = element("button", "text-button", "查看回答 →");
      button.type = "button";
      button.setAttribute("aria-label", `查看第 ${Number(probe.index) + 1} 题的证据`);
      button.addEventListener("click", () => showFingerprintProbe(probe));
      action.append(button);
      row.append(name, element("td", "", textValue(probeAnswer(probe, "reference"))), element("td", "", textValue(probeAnswer(probe, "candidate"))), status, action);
      body.append(row);
    });
    if (!rows.length) {
      const row = element("tr");
      const cell = element("td", "subtle", "没有符合筛选条件的题目。试试清空搜索，或选择“全部判断”。");
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
    }
    $("fp-filter-count").textContent = `${rows.length} / ${state.fingerprintRows.length} 题`;
    $("fp-page-count").textContent = rows.length ? `第 ${offset + 1}–${Math.min(offset + pageSize, rows.length)} 题 · 第 ${state.fingerprintPage + 1} / ${pages} 页` : "没有匹配结果";
    $("fp-prev").disabled = state.fingerprintPage === 0;
    $("fp-next").disabled = state.fingerprintPage + 1 >= pages;
  }

  async function showFingerprintProbe(probe) {
    const current = state.fingerprintReport;
    if (!current) return;
    const requestVersion = ++state.recordRequest;
    $("fp-viewer").hidden = false;
    $("fp-viewer-title").textContent = `${probe.prompt_id || `第 ${Number(probe.index) + 1} 题`} · ${domainName(probe.domain)}`;
    $("fp-viewer-meta").textContent = `批次 ${textValue(probe.batch_id ?? probe.batch_index)} · ${current.entry.report.synthetic ? "合成演示记录" : "本次观测记录"}`;
    $("fp-reference-answer").textContent = textValue(probeAnswer(probe, "reference"));
    $("fp-candidate-answer").textContent = textValue(probeAnswer(probe, "candidate"), "未解析");
    $("fp-question").textContent = textValue(probe.prompt);
    $("fp-raw-response").textContent = "正在读取完整响应…";
    $("fp-raw-details").open = false;
    $("fp-probe-error").hidden = !probe.error;
    $("fp-probe-error").textContent = textValue(probe.error, "");
    document.querySelectorAll("#fp-probe-results tr").forEach((row) => row.classList.toggle("selected", row.dataset.promptId === String(probe.index)));
    $("fp-viewer").scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const key = `${current.run.id}/${current.entry.name}`;
      let records = state.records.get(key);
      if (!records) {
        records = await api(`/api/runs/${encodeURIComponent(current.run.id)}/records?report=${encodeURIComponent(current.entry.name)}`);
        state.records.set(key, records);
      }
      if (requestVersion !== state.recordRequest) return;
      const rows = records.fingerprint || [];
      const record = rows.find((item) => item.type === "request" && item.sequence === probe.selected_request_sequence);
      if (!record) {
        $("fp-raw-response").textContent = probe.raw_response_excerpt ? `未找到完整批次记录，以下为报告中的预览：\n\n${textValue(probe.raw_response)}` : textValue(probe.raw_response, "这道题没有对应的响应记录。");
        return;
      }
      const raw = record.raw_response;
      const content = raw && typeof raw === "object" && Array.isArray(raw.choices) && raw.choices[0] && raw.choices[0].message ? raw.choices[0].message.content : raw;
      $("fp-raw-response").textContent = textValue(content, "（这次请求没有返回文本）");
      const finish = raw && typeof raw === "object" && Array.isArray(raw.choices) && raw.choices[0] ? raw.choices[0].finish_reason : null;
      $("fp-viewer-meta").textContent += ` · 请求 #${textValue(record.sequence)}${finish ? ` · finish_reason: ${finish}` : ""}`;
      if (record.error) {
        $("fp-probe-error").textContent = textValue(record.error);
        $("fp-probe-error").hidden = false;
      }
    } catch (error) {
      if (requestVersion !== state.recordRequest) return;
      $("fp-raw-response").textContent = `${error.message}\n可以下载原始 JSONL 查看完整记录。`;
    }
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
    document.querySelectorAll(".mode-tabs").forEach((group) => {
      if (!group.querySelector('.mode-tab[aria-selected="true"]')) {
        const first = group.querySelector(".mode-tab");
        if (first) first.tabIndex = 0;
      }
    });
    $("reference-library").hidden = !mode.startsWith("fingerprint");
    if (!mode.startsWith("fingerprint") && mode !== "identify") {
      $("advanced-modes").hidden = false;
      $("toggle-advanced").setAttribute("aria-expanded", "true");
    }
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
    return state.runs.some((run) => !terminalStatuses.has(run.status));
  }

  function updateButtons() {
    const libraryBusy = referenceUpdateRunning() || state.referenceUpdateRequest;
    const busy = !state.token || state.submitting || hasRunningTask() || libraryBusy;
    $("prepare-identify").disabled = busy || state.planning || !state.identificationBank;
    $("start-identify").disabled = busy || state.planning || !state.identificationPlan;
    $("start-identify-demo").disabled = busy || !state.identificationBank;
    $("start-identify-replay").disabled = busy || !$("identify-file").files.length;
    $("start-demo").disabled = busy;
    $("start-lab").disabled = busy || !state.scenarios.length;
    $("start-offline").disabled = busy;
    const current = plan();
    $("start-live").disabled = busy || current.prompts.length === 0 || current.count > 1000 || current.tokens > 250000;
    const missingPack = !selectedPack();
    $("prepare-fingerprint").disabled = busy || state.planning || missingPack;
    $("start-fingerprint").disabled = busy || state.planning || missingPack || !state.fingerprintPlan;
    $("start-fingerprint-demo").disabled = busy || missingPack;
    $("start-fingerprint-offline").disabled = busy || missingPack;
    $("reference-pack").disabled = busy || !state.referencePacks.length;
    $("reference-search").disabled = !state.token || hasRunningTask() || state.planning;
    $("reference-provider").disabled = !state.token || hasRunningTask() || state.planning;
    const reconnect = referenceUpdateRunning() && state.referenceUpdatePollFailed;
    $("update-reference-library").disabled = !state.token || state.referenceUpdateRequest || state.submitting || state.planning || hasRunningTask() || (referenceUpdateRunning() && !reconnect);
    $("update-reference-library").textContent = reconnect ? "重新连接更新任务" : libraryBusy ? "正在抓取参考库…" : "↻ 抓取参考库更新";
    $("cancel-reference-update").hidden = !referenceUpdateRunning();
    $("cancel-reference-update").disabled = state.referenceUpdateCancelling;
    $("cancel-reference-update").textContent = state.referenceUpdateCancelling ? "正在取消…" : "取消更新";
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

  function renderIdentificationBank() {
    const bank = state.identificationBank;
    const container = $("identify-bank");
    clear(container);
    if (!bank) { container.textContent = "本机尚未安装共享探针参考。"; return; }
    const date = bank.collected_at || {};
    const first = typeof date === "string" ? date : date.first;
    container.append(element("strong", "", `${number(bank.model_count)} 个候选标签 · ${number(bank.cell_count)} 个任务与语言单元`),
      element("p", "", `公开参考采于 ${String(first || "未知日期").slice(0, 10)}。模型标签沿用数据作者记录；新版本、未收录模型和不同部署可表现为库外或排序混淆。`));
    const candidates = $("identify-candidates"), demos = $("identify-demo-model");
    candidates.replaceChildren(new Option("全部参考模型", "all"));
    demos.replaceChildren();
    const families = [...new Set((bank.models || []).map(m => m.family))].sort();
    families.forEach(family => {
      const count = bank.models.filter(m => m.family === family).length;
      if (count >= 2) candidates.append(new Option(`${family} · ${count} 个候选`, family));
    });
    (bank.models || []).forEach(model => demos.append(new Option(model.label || model.model, model.model)));
  }

  function identificationPayload() {
    const count = Number($("identify-budget").value);
    const payload = {mode: "identify", candidate: {api_base: $("identify-base").value.trim(), model: $("identify-model").value.trim()}, timeout: Number($("identify-timeout").value),
      budget: {max_requests: count, max_output_tokens: count * 16}, api2d_no_cache: $("identify-no-cache").checked};
    const key = $("identify-key").value.trim();
    if (key) payload.candidate.api_key = key;
    const family = $("identify-candidates").value;
    if (family !== "all") payload.candidate_models = (state.identificationBank.models || []).filter(m => m.family === family).map(m => m.model);
    return payload;
  }

  function invalidateIdentificationPlan() {
    state.identificationRevision += 1;
    state.identificationPlan = null;
    $("identify-plan").hidden = true;
    $("identify-plan-note").textContent = "先查看计划，不发送模型请求。";
    updateButtons();
  }

  async function prepareIdentification() {
    if (state.planning || state.submitting || hasRunningTask()) return;
    const payload = identificationPayload();
    if (!payload.candidate.api_base || !payload.candidate.model) return showError("先填写 Base URL 和接口模型名，再查看计划。");
    const revision = ++state.identificationRevision;
    state.planning = true; updateButtons(); showError("");
    try {
      const result = await api("/api/plan", {method: "POST", body: JSON.stringify(payload)});
      if (revision !== state.identificationRevision) return;
      state.identificationPlan = result;
      const container = $("identify-plan"); clear(container);
      container.append(element("strong", "", `最多 ${number(result.budget.max_requests)} 次请求 · ${number(result.budget.max_output_tokens)} 输出配额 · ${number(result.candidate_count)} 个候选`),
        element("p", "", `每单元采 ${number(result.protocol.samples_per_cell)} 次，最多完成 ${number(result.max_completed_cells)} 单元。已采回答同时比较全部所选候选；不逐个模型另行提问。`),
        element("p", "", "优先发送候选回答分歧较大、输入较短的探针。模型标签只用于向服务发起调用，不参与候选打分。"));
      (result.notes || []).forEach(note => container.append(element("p", "subtle", note)));
      container.hidden = false;
      $("identify-plan-note").textContent = "计划已核对。开始后会调用你填写的端点。";
    } catch(error) { showError(error.message); }
    finally {state.planning = false; updateButtons();}
  }

  function renderIdentificationReport(run, entry) {
    const report = entry.report || {}, coverage = report.coverage || {}, cost = report.cost || {};
    const labels = {candidate_shortlist: "已得到模型候选", unknown_or_changed: "库外模型或服务行为变化", insufficient_evidence: "本次证据不足以形成候选结论"};
    $("identify-headline").textContent = labels[report.status] || "候选识别记录";
    $("identify-scope").textContent = report.synthetic ? "合成回答 · 流程演示" : "单端点 · 共享探针参考";
    $("identify-explanation").textContent = report.synthetic ? "这组回答从参考分布生成，用来展示流程；准确率另以独立留出的公开回答评估。" :
      (report.status === "candidate_shortlist" ? "下表列出与本轮回答最接近的参考标签。当前结果保留身份拒判，候选顺序用于后续复核。" :
      report.status === "unknown_or_changed" ? "当前回答没有通过本轮参考库的接受条件。保留最近候选，供检查版本、配置和路由变化。" : "查看停止原因和有效单元数；原始记录已保存，可在本机复算。");
    const metrics = $("identify-metrics"); clear(metrics);
    [["比较候选", coverage.candidate_count], ["完成单元", coverage.completed_cells], [report.synthetic ? "合成请求" : "发送请求", cost.request_count], ["请求输出配额", cost.requested_output_tokens]].forEach(([label, value]) => {
      const item = element("div"); item.append(element("span", "", label), element("strong", "", number(value))); metrics.append(item);
    });
    const usage = cost.usage || {};
    $("identify-usage").textContent = report.synthetic ? "此为合成演示，实际模型请求为 0，不产生模型调用费用。" :
      `供应商已报用量：输入 ${textValue(usage.prompt_tokens, "未知")}，输出 ${textValue(usage.completion_tokens, "未知")}，其中推理 ${textValue(usage.reasoning_tokens, "未知")} tokens。${usage.complete ? "输入、输出及总量均已返回。" : "存在用量缺项；已报小计不等于完整账单。"}`;
    const body = $("identify-ranking"); clear(body);
    body.classList.remove("expanded");
    $("identify-more").hidden = (report.ranking || []).length <= 5;
    $("identify-more").setAttribute("aria-expanded", "false");
    $("identify-more").textContent = `展开前${(report.ranking || []).length}个候选`;
    (report.ranking || []).forEach(row => {
      const tr = element("tr");
      [row.rank, row.label || row.model, row.family, typeof row.distance === "number" ? row.distance.toFixed(4) : "—"].forEach(value => tr.append(element("td", "", value)));
      body.append(tr);
    });
    if (!(report.ranking || []).length) {const tr = element("tr"), td = element("td", "", "尚无可比较的完整单元。"); td.colSpan = 4; tr.append(td); body.append(tr);}
    $("identify-families").textContent = (report.family_ranking || []).slice(0, 6).map(row => `${row.family} · ${typeof row.distance === "number" ? row.distance.toFixed(4) : "—"}`).join("；") || "尚无家族候选。";
    const stopLabels = {budget_exhausted: "已达到本次预算；剩余额度不足以完成下一个单元", demo_budget_exhausted: "合成演示已完成设定预算", budget_max_requests: "已达到请求次数预算", budget_max_output_tokens: "已达到请求输出配额", cancelled: "用户取消", response_cache_hit: "发现整段回答缓存", reasoning_returned: "端点返回推理内容，与参考协议不匹配", no_discriminating_shared_probe: "剩余共享探针没有可用于区分候选的分歧", no_informative_cells: "剩余探针没有可用分歧", no_more_cells: "可用共享探针已用尽", protocol_incompatible: "端点不支持参考采样协议", invalid: "回答格式不符合固定探针协议，已保留当前记录", empty: "端点返回空回答", refusal: "端点拒绝回答探针", truncated: "回答被截断", http_error: "端点返回HTTP错误", request_error: "连接或请求失败", completed: "计划已完成"};
    $("identify-stop").textContent = stopLabels[report.stop_reason] || textValue(report.stop_reason);
    $("identify-method").textContent = JSON.stringify({reference: report.bank, calibration: report.calibration, coverage: report.coverage, cost: report.cost, notes: report.notes}, null, 2);
    const exports = $("identify-exports"); clear(exports);
    [["json", "JSON报告"], ["md", "Markdown报告"], ["jsonl", "原始回答JSONL"]].forEach(([format, label]) => {
      const a = element("a", "button quiet", label); a.href = `/api/runs/${encodeURIComponent(run.id)}/download/${encodeURIComponent(entry.name)}/${format}`; a.download = ""; exports.append(a);
    });
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
    if (referenceUpdateRunning() || state.referenceUpdateRequest) return showError("参考库正在更新。完成后核对所选版本，再开始检测。");
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
      if (payload.mode === "identify") {
        $("identify-key").value = "";
        invalidateIdentificationPlan();
      }
      if (payload.mode === "fingerprint") {
        $("fingerprint-key").value = "";
        $("fingerprint-key").type = "password";
        $("toggle-key").textContent = "显示";
        $("toggle-key").setAttribute("aria-pressed", "false");
        $("toggle-key").setAttribute("aria-label", "显示 API 密钥");
        invalidateFingerprintPlan();
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
    if (run.status === "completed") toast("分析完成，可以查看逐题证据与原始回答。");
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
    const identification = report.kind === "identification";
    $("identify-report").hidden = !identification;
    if (identification) {
      $("fingerprint-report").hidden = true;
      $("pair-report").hidden = true;
      return renderIdentificationReport(run, entry);
    }
    const fingerprint = report.kind === "fingerprint" || Boolean(report.fingerprint);
    $("fingerprint-report").hidden = !fingerprint;
    $("pair-report").hidden = fingerprint;
    if (fingerprint) return renderFingerprintReport(run, entry);
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
      renderMetric("输出吞吐 · token/s", number((left.throughput || {}).output_tokens_per_second_median), number((right.throughput || {}).output_tokens_per_second_median), "仅统计成功且同时报告用量与耗时的请求"),
      renderMetric("工具调用", (left.tool_calls || {}).status === "measured" ? number((left.tool_calls || {}).total) : "未测量", (right.tool_calls || {}).status === "measured" ? number((right.tool_calls || {}).total) : "未测量", "没有工具证据时保持未知，不把文本回答当作工具调用"),
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
    serviceDetails.textContent = `P95 响应耗时 A / B：${duration((left.elapsed_seconds || {}).p95)} / ${duration((right.elapsed_seconds || {}).p95)}；链接健康：${textValue((left.link_health || {}).status)} / ${textValue((right.link_health || {}).status)}。${report.synthetic ? "以下为合成数据中的计量示例" : "服务端自报计量"}：输入 ${number((left.reported_tokens || {}).input_tokens)} / ${number((right.reported_tokens || {}).input_tokens)}，输出 ${number((left.reported_tokens || {}).output_tokens)} / ${number((right.reported_tokens || {}).output_tokens)} tokens。`;
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
      const fingerprintReport = reports.find((entry) => entry.report && entry.report.fingerprint);
      if (fingerprintReport) {
        const fingerprint = fingerprintReport.report.fingerprint;
        const variant = { match: "接近参考", different: "明显偏离", incomplete: "采样不完整" }[fingerprint.demo_variant];
        const profileLabel = fingerprintReport.report.profile === "screening" ? "低成本初筛" : "完整参考比较";
        title = `${(fingerprint.reference || {}).label || "参考指纹"} · ${profileLabel}${variant ? ` · ${variant}` : ""}`;
      }
      if (studyReport) {
        const study = studyReport.report.study;
        title = [study.case_id, study.service_name].filter(Boolean).map((value) => textValue(value)).join(" · ") || title;
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
      state.identificationBank = bootstrap.identification_bank || null;
      renderIdentificationBank();
      state.probes = Array.isArray(bootstrap.probes) ? bootstrap.probes : [];
      state.scenarios = Array.isArray(bootstrap.scenarios) ? bootstrap.scenarios : [];
      state.referencePacks = Array.isArray(bootstrap.reference_packs) ? bootstrap.reference_packs : [];
      $("app-version").textContent = bootstrap.version ? `v${bootstrap.version}` : "";
      renderProbes();
      renderScenarios();
      renderReferenceProviders();
      renderReferencePacks(true);
      showError("", "connection-error");
      await loadHistory(true);
      try {
        const library = await refreshReferenceLibrary();
        state.referenceUpdate = library.last_update || null;
        if (referenceUpdateRunning()) scheduleReferenceUpdatePoll(300);
      } catch (error) { showError(error.message, "reference-update-error"); }
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
    setMode(state.mode);
    updateButtons();
  }

  document.querySelectorAll(".mode-tab").forEach((tab) => {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = Array.from(tab.closest('[role="tablist"]').querySelectorAll(".mode-tab"));
      const index = tabs.indexOf(tab);
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

  $("prepare-identify").addEventListener("click", prepareIdentification);
  $("identify-more").addEventListener("click", () => {
    const expanded = $("identify-ranking").classList.toggle("expanded");
    $("identify-more").setAttribute("aria-expanded", String(expanded));
    $("identify-more").textContent = expanded ? "收起至前5个候选" : `展开前${$("identify-ranking").children.length}个候选`;
  });
  ["identify-base", "identify-model", "identify-key", "identify-budget", "identify-candidates", "identify-timeout", "identify-no-cache"].forEach(id => {
    $(id).addEventListener("input", invalidateIdentificationPlan);
    $(id).addEventListener("change", invalidateIdentificationPlan);
  });
  $("identify-file").addEventListener("change", updateButtons);
  $("identify-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!state.identificationPlan || state.submitting || hasRunningTask() || !event.currentTarget.reportValidity()) return;
    await submitRun(identificationPayload(), true);
  });
  $("start-identify-demo").addEventListener("click", () => {
    const count = Number($("identify-budget").value);
    submitRun({mode: "identify_demo", model_id: $("identify-demo-model").value, budget: {max_requests: count, max_output_tokens: count * 16}});
  });
  $("start-identify-replay").addEventListener("click", async () => {
    const file = $("identify-file").files[0];
    if (!file || file.size > 4 * 1024 * 1024) return showError("请选择不超过4 MB的识别记录JSONL。");
    try {await submitRun({mode: "identify_offline", evidence_jsonl: await file.text()});}
    catch(error) {showError(error.message);}
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

  $("toggle-advanced").addEventListener("click", () => {
    const expanded = $("advanced-modes").hidden;
    $("advanced-modes").hidden = !expanded;
    $("toggle-advanced").setAttribute("aria-expanded", String(expanded));
    if (!expanded && !state.mode.startsWith("fingerprint")) setMode("fingerprint");
    else if (expanded) setMode(state.mode);
  });
  $("reference-pack").addEventListener("change", () => {
    renderReferenceCard(true);
    invalidateFingerprintPlan();
  });
  $("reference-search").addEventListener("input", () => renderReferencePacks());
  $("reference-provider").addEventListener("change", () => renderReferencePacks());
  $("update-reference-library").addEventListener("click", startReferenceUpdate);
  $("cancel-reference-update").addEventListener("click", cancelReferenceUpdate);
  $("open-pair-comparison").addEventListener("click", () => {
    setMode("live");
    $("panel-live").scrollIntoView({ behavior: "smooth", block: "start" });
    $("reference-base").focus({ preventScroll: true });
  });
  ["fingerprint-base", "fingerprint-model", "fingerprint-key", "fingerprint-timeout", "fingerprint-budget-requests", "fingerprint-budget-output"].forEach((id) => {
    $(id).addEventListener("input", invalidateFingerprintPlan);
    $(id).addEventListener("change", invalidateFingerprintPlan);
  });
  document.querySelectorAll('input[name="fingerprint-profile"]').forEach((input) => input.addEventListener("change", changeFingerprintProfile));
  $("toggle-key").addEventListener("click", () => {
    const visible = $("fingerprint-key").type === "password";
    $("fingerprint-key").type = visible ? "text" : "password";
    $("toggle-key").textContent = visible ? "隐藏" : "显示";
    $("toggle-key").setAttribute("aria-pressed", String(visible));
    $("toggle-key").setAttribute("aria-label", visible ? "隐藏 API 密钥" : "显示 API 密钥");
  });
  $("prepare-fingerprint").addEventListener("click", prepareFingerprintPlan);
  $("panel-fingerprint").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.submitting || state.planning || hasRunningTask() || !event.currentTarget.reportValidity()) return;
    if (!state.fingerprintPlan) return prepareFingerprintPlan();
    await submitRun(fingerprintPayload());
  });
  $("panel-fingerprint_demo").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.submitting || hasRunningTask() || !selectedPack()) return;
    await submitRun({ mode: "fingerprint_demo", reference_pack: selectedPack().id, profile: $("fingerprint-demo-profile").value, variant: document.querySelector('input[name="fingerprint-variant"]:checked').value });
  });
  $("fingerprint-file").addEventListener("change", (event) => {
    const file = event.target.files[0];
    $("fingerprint-file-name").textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "尚未选择文件";
  });
  $("panel-fingerprint_offline").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.submitting || hasRunningTask() || !selectedPack() || !event.currentTarget.reportValidity()) return;
    const file = $("fingerprint-file").files[0];
    if (!file) return showError("请选择 API-MRI 导出的原始指纹 JSONL 文件。");
    if (file.size > 4 * 1024 * 1024) return showError("原始记录请控制在 4 MB 以内。更大的记录可使用命令行复算。");
    state.submitting = true;
    updateButtons();
    try {
      await submitRun({ mode: "fingerprint_offline", reference_pack: selectedPack().id, evidence_jsonl: await file.text() });
    } catch (_) { showError("未能读取所选文件，请重新选择本机的 JSONL 文件。"); }
    finally { state.submitting = false; updateButtons(); }
  });
  ["fp-search", "fp-outcome", "fp-domain"].forEach((id) => $(id).addEventListener(id === "fp-search" ? "input" : "change", () => {
    state.fingerprintPage = 0;
    renderFingerprintRows();
    $("fp-viewer").hidden = true;
    state.recordRequest += 1;
  }));
  $("fp-prev").addEventListener("click", () => { state.fingerprintPage -= 1; renderFingerprintRows(); });
  $("fp-next").addEventListener("click", () => { state.fingerprintPage += 1; renderFingerprintRows(); });
  $("fp-viewer-close").addEventListener("click", () => { $("fp-viewer").hidden = true; state.recordRequest += 1; });

  renderStudyForm("live");
  renderStudyForm("offline");
  renderStudyForm("fingerprint");
  initialize();
})();
