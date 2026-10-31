(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = {
    token: null,
    ready: false,
    view: "quality",
    specific: false,
    plan: null,
    planSignature: null,
    planRevision: 0,
    busy: false,
    running: null,
    run: null,
    runs: [],
    bank: { models: [] },
    packs: [],
    probes: [],
    scenarios: [],
    poll: null,
    updatePoll: null,
    updateRunning: false,
    pairPlan: null,
    pairSignature: null,
    embedOrigins: [],
    version: "",
  };
  const views = {
    quality: {
      title: "连接与速度",
      description: "测量一次请求从发出到回答完成的过程。",
      emptyTitle: "同一次请求，分开看等待和生成",
      emptyDescription:
        "响应头、首段正文、末段正文与连接结束分别记录。服务自报 token 用量可用时，计算输出吞吐。",
    },
    tools: {
      title: "工具调用",
      description: "检查工具选择、参数格式和结果回填。",
      emptyTitle: "一个完整的工具调用回路",
      emptyDescription:
        "模型生成参数，工具返回合成数据，再由模型读出结果。另测自动选用工具和无须工具的回答。",
    },
    identity: {
      title: "模型指纹",
      description: "用已有参考回答，寻找最接近的模型候选。",
      emptyTitle: "一个回答，同时比较多个候选",
      emptyDescription:
        "固定短探针复用公开参考分布。逐题贡献和删去单个探针后的排序变化，展示候选结论依赖哪些回答。",
    },
    showcase: {
      title: "示例馆",
      description: "同一道题，看代码、画面与调用代价。",
    },
    history: {
      title: "运行记录",
      description: "重新打开报告，导出记录或离线复算。",
    },
    library: {
      title: "参考库",
      description: "查看模型标签、采集日期和数据来源。",
    },
    research: {
      title: "研究实验",
      description: "用可控情境和自定义对照，追踪差异怎样产生。",
    },
  };
  const modeNames = {
    showcase: "经典题试作",
    showcase_demo: "公开作品回放",
    showcase_offline: "试作记录复算",
    service: "服务检查",
    service_demo: "服务合成示例",
    service_offline: "服务记录复算",
    identify: "模型候选识别",
    identify_demo: "候选合成示例",
    identify_offline: "候选记录复算",
    fingerprint: "指定型号对照",
    fingerprint_demo: "型号对照示例",
    fingerprint_offline: "型号记录复算",
    demo: "统计合成演示",
    lab: "机制实验",
    live: "双端点比较",
    offline: "双端点复算",
  };
  const statusNames = {
    queued: "排队中",
    running: "正在运行",
    completed: "已完成",
    failed: "运行中断",
    cancelled: "已停止",
    candidate_shortlist: "已得到模型候选",
    unknown_or_changed: "库外或行为变化",
    insufficient_evidence: "证据不足",
    difference_detected: "发现显著差异",
    no_difference_detected: "未检出明显差异",
    behavior_difference: "发现行为差异",
    no_behavior_difference: "未检出行为差异",
    no_significant_difference: "未检出显著差异",
    partial: "部分完成",
  };
  const stopNames = {
    budget_exhausted: "预算已用完，剩余额度不足以完成下一组探针。",
    demo_budget_exhausted: "合成示例已完成设定预算。",
    budget_max_requests: "已达到请求次数预算。",
    budget_max_output_tokens: "已达到累计输出配额。",
    cancelled: "已按你的操作停止。",
    response_cache_hit: "发现整段回答缓存，已停止重复采样。",
    reasoning_returned: "端点返回推理内容，与固定参考协议不匹配。",
    no_discriminating_shared_probe: "剩余共享探针无法继续区分候选。",
    no_informative_cells: "剩余探针没有可用分歧。",
    no_more_cells: "可用共同探针已用尽。",
    protocol_incompatible: "端点未接受固定探针协议。",
    invalid: "回答未满足探针格式，已保存原始记录。",
    empty: "返回了空回答。",
    refusal: "端点拒绝回答探针。",
    truncated: "回答达到输出限制。",
    http_error: "服务返回 HTTP 错误。",
    request_error: "连接或请求失败。",
    completed: "计划已完成。",
    stable_shortlist: "候选达到分阶段停止条件，已节省后续请求。",
  };
  const taskNames = {
    "animal-random": "随机动物",
    "city-random": "随机城市",
    "coin-flip": "抛硬币",
    "color-favorite": "偏好颜色",
    "color-random": "随机颜色",
    "letter-random": "随机字母",
    "num-favorite": "偏好数字",
    "num10-random": "1到10随机数",
    "num100-random": "1到100随机数",
    "word-random": "随机词语",
  };
  const languageNames = { zh: "中文", en: "英文", ar: "阿拉伯文", ru: "俄文" };
  const toolLabels = {
    tool_forced: [
      "指定工具与参数",
      "按指定名称调用，检查参数字段、类型和取值。",
    ],
    tool_roundtrip: ["工具结果回填", "返回合成数据，再检查模型是否读出结果。"],
    tool_auto: ["自动选择工具", "模型自行决定是否调用，并选对工具。"],
    tool_no_call: ["无需工具的回答", "简单任务应直接回答，避免多余工具调用。"],
  };
  const num = (v, d = 0) =>
    typeof v === "number" && Number.isFinite(v)
      ? v.toLocaleString("zh-CN", {
          minimumFractionDigits: d,
          maximumFractionDigits: d,
        })
      : "—";
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const text = (v, f = "—") =>
    v === null || v === undefined || v === ""
      ? f
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
  const ms = (v) =>
    !finite(v) ? "—" : v < 1000 ? `${num(v)} ms` : `${num(v / 1000, 2)} s`;
  const pct = (v) => (finite(v) ? `${num(v * 100, 1)}%` : "—");
  const dateRange = (v) => {
    if (v && typeof v === "object" && v.first) {
      const first = String(v.first).slice(0, 10),
        last = String(v.last || v.first).slice(0, 10);
      return first === last ? first : `${first} 至 ${last}`;
    }
    return text(v).slice(0, 10);
  };
  const date = (v) => {
    const d = new Date(v);
    return Number.isNaN(d.valueOf())
      ? text(v)
      : new Intl.DateTimeFormat("zh-CN", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(d);
  };
  function el(tag, cls, value) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (value !== undefined && value !== null) e.textContent = String(value);
    return e;
  }
  function btn(label, handler, cls = "text-button") {
    const b = el("button", cls, label);
    b.type = "button";
    b.addEventListener("click", handler);
    return b;
  }
  function showError(message, id = "form-error") {
    const node = $(id);
    node.textContent = message || "";
    node.hidden = !message;
  }
  let toastTimer;
  function toast(message) {
    $("toast").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      $("toast").hidden = true;
    }, 4500);
  }
  function apiError(data) {
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail))
      return data.detail.map((v) => v.msg || "输入不正确").join("；");
    return "连接未完成，请重试。";
  }
  async function api(path, body) {
    const opts = { headers: { Accept: "application/json" }, cache: "no-store" };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-Relay-Token"] = state.token || "";
      opts.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, opts);
    } catch (_) {
      throw new Error("无法连接工作台，请确认 API-MRI 仍在运行。");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiError(data));
    return data;
  }
  function setBusy(value) {
    state.busy = value;
    updateButtons();
  }
  function updateButtons() {
    const locked =
      !state.ready ||
      state.busy ||
      Boolean(state.running) ||
      state.updateRunning;
    [
      "plan-button",
      "start-button",
      "try-demo",
      "empty-demo",
      "run-lab",
      "run-stat-demo",
      "pair-plan-button",
      "pair-start",
      "import-submit",
    ].forEach((id) => ($(id).disabled = locked));
    $("update-library").disabled = locked;
    window.API_MRI_SHOWCASE?.setLocked(locked);
  }
  function closeMenu() {
    const was = $("navigation").classList.contains("open");
    $("navigation").classList.remove("open");
    $("menu-button").setAttribute("aria-expanded", "false");
    $("menu-button").setAttribute("aria-label", "打开导航");
    if (was) $("main").inert = false;
  }
  function route(focus = false) {
    const hash = location.hash.slice(1);
    const target = views[hash] ? hash : "quality";
    const previous = state.view;
    state.view = target;
    const data = views[target];
    $("page-title").textContent = data.title;
    $("page-description").textContent = data.description;
    document.title = `${data.title} · API-MRI`;
    document.querySelectorAll("[data-view]").forEach((a) => {
      if (a.dataset.view === target) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    const isCheck = ["quality", "tools", "identity"].includes(target);
    if (isCheck && previous !== target) {
      $("report-container").hidden = true;
      $("empty-result").hidden = false;
    }
    $("check-view").hidden = !isCheck;
    ["history", "library", "research", "showcase"].forEach(
      (v) => ($(v + "-view").hidden = target !== v),
    );
    $("try-demo").hidden = !isCheck;
    $("service-settings").hidden = target === "identity";
    $("service-protocol-fields").hidden = target === "identity";
    $("stable-stop-field").hidden = target !== "identity" || state.specific;
    $("identity-settings").hidden = target !== "identity";
    $("stream-fields").hidden = target === "tools";
    $("include-tools-label").hidden = target !== "quality";
    $("tool-settings-note").hidden = target !== "tools";
    $("no-cache-label").hidden = target !== "identity" || state.specific;
    $("service-budget-fields").hidden =
      target === "identity" && !state.specific;
    if (previous === "identity" && ["quality", "tools"].includes(target)) {
      $("request-cap").value = "6";
      $("token-cap").value = "768";
    }
    $("request-cap").max = target === "identity" ? "500" : "32";
    $("token-cap").max = target === "identity" ? "1000000" : "8192";
    if (isCheck) {
      $("empty-title").textContent = data.emptyTitle;
      $("empty-description").textContent = data.emptyDescription;
      renderMechanism(target);
    }
    invalidatePlan();
    closeMenu();
    if (target === "history") renderHistory();
    if (target === "library") renderLibrary();
    document.dispatchEvent(new CustomEvent("api-mri:view", { detail: { view: target } }));
    if (focus) $("main").focus();
  }
  function renderMechanism(view) {
    // Fixed explanatory illustrations; no figures here represent measurements.
    const drawings = {
      quality: {
        label: "请求从客户端进入待测端点，端点处理后沿连接返回正文片段。",
        captions: ["发出请求", "端点处理", "正文逐段返回"],
        art: "<g stroke-linecap='round' stroke-linejoin='round'><circle cx='295' cy='103' r='87' fill='#e6ecda'/><path d='M219 68a86 86 0 0 1 100-48M239 176a86 86 0 0 0 128-85' fill='none' stroke='#bdc9ab' stroke-width='1.2'/><ellipse cx='298' cy='177' rx='78' ry='10' fill='#d9e0cb'/><path d='M133 112c44 0 57-35 112-35' fill='none' stroke='#98482e' stroke-width='2.4'/><path d='m237 71 8 6-8 6' fill='none' stroke='#98482e' stroke-width='2.4'/><path d='M357 95h21c18 0 20-15 38-15' fill='none' stroke='#738365' stroke-width='2.2'/><path d='m408 74 8 6-8 6' fill='none' stroke='#738365' stroke-width='2.2'/><path d='M357 109h17c24 0 21 28 47 28' fill='none' stroke='#b6c3a4' stroke-width='1.4'/><path d='M46 92h80a5 5 0 0 1 5 5v59H41V97a5 5 0 0 1 5-5Z' fill='#fffef9' stroke='#738365' stroke-width='1.6'/><path d='m41 156-13 9h116l-13-9Z' fill='#e6ecda' stroke='#738365' stroke-width='1.6'/><path d='M49 101h74v47H49Z' fill='#f4f4ee'/><path d='m65 116-8 7 8 7m42-14 8 7-8 7m-20-16-6 20' fill='none' stroke='#535e49' stroke-width='1.8'/><path d='M72 160h29' stroke='#a3b192' stroke-width='1.4'/><path d='m246 41 31-15 82 14-31 15Z' fill='#fffef9' stroke='#738365' stroke-width='1.5'/><path d='m328 55 31-15v115l-31 15Z' fill='#536647' stroke='#738365' stroke-width='1.5'/><path d='m246 41 82 14v115l-82-14Z' fill='#6f805f' stroke='#526144' stroke-width='1.7'/><path d='m258 62 57 10v23l-57-10Zm0 36 57 10v23l-57-10Zm0 36 57 10v13l-57-10Z' fill='#f4f4ee' stroke='#c1cbb5' stroke-width='1.1'/><path d='m266 75 25 4m-25 32 25 4m-25 28 18 3' stroke='#83946d' stroke-width='2'/><ellipse cx='306' cy='81' rx='2.8' ry='3.1' fill='#98482e'/><ellipse cx='306' cy='117' rx='2.8' ry='3.1' fill='#738365'/><path d='m340 69 8-4m-8 18 8-4m-8 18 8-4m-8 18 8-4m-8 18 8-4m-8 18 8-4' stroke='#8e9f79' stroke-width='1.3'/><path d='m429 67 81-8 18 18 9 92-99 10Z' fill='#dce5cc'/><path d='m415 60 80-8 18 18 9 92-99 10Z' fill='#fffef9' stroke='#738365' stroke-width='1.4'/><path d='m495 52 2 20 16-2Z' fill='#e2c7a9' stroke='#a56847' stroke-width='1.1'/><path d='m432 85 41-4m-40 16 62-6m-61 19 52-5m-51 18 62-6m-61 19 35-3' stroke='#8c9d79' stroke-width='2'/><path d='M427 185c12-19 25 19 38 0s25 19 38 0 25 19 38 0' fill='none' stroke='#98482e' stroke-width='1.8'/><circle cx='176' cy='101' r='3.5' fill='#a56847'/><path d='m172 94 7 3m-16 12 7-3' stroke='#cfb395' stroke-width='1'/></g>",
      },
      tools: {
        label:
          "模型给出结构化参数，合成工具读取表中字段，返回内容供模型完成回答。",
        captions: ["模型给出参数", "工具返回数据", "模型读取结果"],
        art: "<g stroke-linecap='round' stroke-linejoin='round'><path d='M176 153c-16-48 11-103 68-118 44-12 102 3 127 44 28 47 6 95-40 108-62 18-131 9-155-34Z' fill='#e6ecda'/><path d='M174 118c1-47 37-83 86-89m85 18c29 18 45 48 39 81' fill='none' stroke='#bdc9ab' stroke-width='1.2'/><path d='M135 96c38-4 50-32 92-32' fill='none' stroke='#98482e' stroke-width='2.4'/><path d='m219 58 8 6-8 6' fill='none' stroke='#98482e' stroke-width='2.4'/><path d='M365 103c35 0 39-20 66-20' fill='none' stroke='#738365' stroke-width='2.2'/><path d='m423 77 8 6-8 6' fill='none' stroke='#738365' stroke-width='2.2'/><path d='M483 153c-7 48-147 62-278 43-65-10-103-20-111-44' fill='none' stroke='#b5c1a3' stroke-width='1.4'/><path d='m90 161 4-9 8 5' fill='none' stroke='#738365' stroke-width='1.6'/><g transform='rotate(-9 89 112)'><path d='M40 47h90v128H40l-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8Z' fill='#d6dfc7' transform='translate(6 6)'/><path d='M40 47h90v128H40l-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8-5-8 5-8Z' fill='#fffef9' stroke='#738365' stroke-width='1.5'/><path d='M50 65h67M50 151h67' stroke='#c6d0bb' stroke-width='1'/><circle cx='53' cy='57' r='2.3' fill='#e2c7a9'/><circle cx='64' cy='57' r='2.3' fill='#e2c7a9'/><path d='M66 87c-10 0-6 14-14 17 8 4 4 17 14 17m41-34c10 0 6 14 14 17-8 4-4 17-14 17' fill='none' stroke='#98482e' stroke-width='2'/><path d='M77 97h19m-19 12h12m-12 12h19' stroke='#83946d' stroke-width='1.8'/></g><path d='m227 62 122 9 18 15-123-9Z' fill='#fffef9' stroke='#738365' stroke-width='1.4'/><path d='m244 77 123 9v89l-123-9Z' fill='#c5d1b5' stroke='#738365' stroke-width='1.4'/><path d='m227 62 122 9v89l-122-9Z' fill='#fffef9' stroke='#738365' stroke-width='1.6'/><path d='m227 62 122 9v22l-122-9Z' fill='#738365'/><path d='m241 74 44 3m12 1 34 3' stroke='#fffef9' stroke-width='2'/><path d='m227 106 122 9m-122 13 122 9m-122 13 122 9m-88-69v67m47-64v67' stroke='#bdc9ae' stroke-width='1.1'/><circle cx='244' cy='95' r='2.7' fill='#98482e'/><path d='m272 98 16 1m-49 19 11 1m22 1 16 1m-49 19 11 1m22 1 16 1' stroke='#83946d' stroke-width='1.9'/><path d='m319 140 5 6 10-13' fill='none' stroke='#98482e' stroke-width='2.3'/><g transform='rotate(6 481 111)'><path d='M430 54h99v102h-60l-16 15v-15h-23Z' fill='#e2c7a9' transform='translate(6 7)'/><path d='M430 54h99v102h-60l-16 15v-15h-23Z' fill='#fffef9' stroke='#738365' stroke-width='1.5'/><path d='M444 73h55m-55 14h68m-68 14h37' stroke='#8b9a79' stroke-width='1.8'/><circle cx='503' cy='129' r='13' fill='#e6ecda'/><path d='m496 128 5 5 10-12' fill='none' stroke='#738365' stroke-width='2'/></g></g>",
      },
      identity: {
        label: "同一组短回答进入共享参考分布，与多份参考比较后得到候选顺序。",
        captions: ["收集短回答", "比较参考分布", "排列候选"],
        art: "<g stroke-linecap='round' stroke-linejoin='round'><ellipse cx='294' cy='108' rx='101' ry='87' fill='#e6ecda'/><path d='M203 69c14-35 50-56 87-55m57 18c37 20 56 64 42 102' fill='none' stroke='#bdc9ab' stroke-width='1.2'/><ellipse cx='295' cy='192' rx='96' ry='8' fill='#d6dfc7'/><path d='M135 116c38 0 42-34 87-34' fill='none' stroke='#98482e' stroke-width='2.3'/><path d='m214 76 8 6-8 6' fill='none' stroke='#98482e' stroke-width='2.3'/><path d='M377 104h20c20 0 19-30 41-30m-41 30c20 0 19 21 41 21m-41-21c20 0 19 66 41 66' fill='none' stroke='#98a783' stroke-width='1.5'/><path d='m431 68 7 6-7 6m0 39 7 6-7 6m0 33 7 6-7 6' fill='none' stroke='#738365' stroke-width='1.5'/><g transform='rotate(-14 84 110)'><rect x='32' y='61' width='93' height='58' rx='5' fill='#e6ecda' stroke='#98a783' stroke-width='1.1'/><path d='M47 81h36m-36 13h58' stroke='#98a783' stroke-width='1.8'/></g><g transform='rotate(7 90 119)'><rect x='46' y='96' width='92' height='58' rx='5' fill='#fffef9' stroke='#738365' stroke-width='1.5'/><path d='M61 116h27m-27 13h58' stroke='#98482e' stroke-width='1.8'/><circle cx='123' cy='139' r='3.1' fill='#a56847'/></g><g transform='rotate(-12 275 118)'><path d='M219 46h117v145H219Z' fill='#d6dfc7' stroke='#98a783' stroke-width='1.2'/><path d='M232 64h56m-56 13h84' stroke='#a4b191' stroke-width='1.6'/><path d='M234 165c16-37 24-15 37-42s24-25 48-44' fill='none' stroke='#738365' stroke-width='2'/></g><g transform='rotate(8 312 110)'><path d='M252 34h118v147H252Z' fill='#f1e7d8' stroke='#af9e83' stroke-width='1.2'/><path d='M266 53h62m-62 12h86' stroke='#bca98d' stroke-width='1.5'/><path d='M266 157c12 0 20-33 33-33s16 23 28 23 14-13 28-15' fill='none' stroke='#a56847' stroke-width='1.8'/></g><g transform='rotate(-2 294 117)'><path d='M237 41h120v148H237Z' fill='#fffef9' stroke='#738365' stroke-width='1.6'/><path d='M251 58h49' stroke='#535e49' stroke-width='2'/><path d='M251 69h89' stroke='#c4ceb8' stroke-width='1.2'/><path d='M251 165h91' stroke='#b9c5a9' stroke-width='1.1'/><path d='M259 164v-34m14 34v-63m14 63V88m14 76v-60m14 60v-39m14 39v-19' stroke='#92a378' stroke-width='7'/><path d='M287 164V88' stroke='#98482e' stroke-width='7'/><path d='M251 178h35m13 0h42' stroke='#c3ccb8' stroke-width='1.1'/></g><g><path d='M451 52h84v40h-84Z' fill='#e9d8c3' stroke='#a56847' stroke-width='1.2'/><path d='M451 104h84v40h-84Z' fill='#fffef9' stroke='#a4b191' stroke-width='1.2'/><path d='M451 156h84v37h-84Z' fill='#fffef9' stroke='#c0cbb2' stroke-width='1.2'/><circle cx='465' cy='72' r='4' fill='#98482e'/><circle cx='465' cy='124' r='4' fill='#92a378'/><circle cx='465' cy='174' r='4' fill='#bfcbb0'/><path d='M478 67h43m-43 10h27' stroke='#80533b' stroke-width='1.8'/><path d='M478 119h43m-43 10h27m-27 40h43m-43 10h27' stroke='#8e9e7d' stroke-width='1.8'/></g></g>",
      },
    };
    const drawing = drawings[view] || drawings.quality;
    $("scan-preview").innerHTML =
      `<svg class="mechanism-illustration" viewBox="0 0 560 220" role="img" aria-label="${drawing.label}">${drawing.art}</svg><div class="illustration-legend">${drawing.captions.map((caption) => `<span>${caption}</span>`).join("")}</div>`;
  }
  function invalidatePlan() {
    state.plan = null;
    state.planSignature = null;
    state.planRevision++;
    $("plan-box").hidden = true;
    $("start-button").hidden = true;
    $("replan-button").hidden = true;
    $("plan-button").hidden = false;
    $("run-cost-note").textContent = "查看计划不会调用模型。";
    showError("");
  }
  function candidate(withKey = false) {
    const v = {
      api_base: $("api-base").value.trim(),
      model: $("api-model").value.trim(),
    };
    if (withKey) v.api_key = $("api-key").value.trim();
    return v;
  }
  function payload(withKey = false) {
    const c = candidate(withKey),
      timeout = Number($("timeout").value);
    if (state.view === "identity") {
      if (state.specific) {
        const b = {};
        if ($("request-cap").value)
          b.max_requests = Number($("request-cap").value);
        if ($("token-cap").value)
          b.max_output_tokens = Number($("token-cap").value);
        return {
          mode: "fingerprint",
          candidate: c,
          reference_pack: $("reference-pack").value,
          profile: $("fingerprint-profile").value,
          timeout,
          budget: b,
        };
      }
      const count = Number($("identify-budget").value);
      return {
        mode: "identify",
        candidate: c,
        timeout,
        budget: { max_requests: count, max_output_tokens: count * 16 },
        api2d_no_cache: $("no-cache").checked,
        stop_policy:
          $("stable-stop").checked && count === 32
            ? "stable_shortlist"
            : "fixed",
      };
    }
    return {
      mode: "service",
      candidate: c,
      checks:
        state.view === "tools"
          ? ["tools"]
          : $("include-tools").checked
            ? ["stream", "tools"]
            : ["stream"],
      samples: Number($("samples").value),
      max_tokens: Number($("output-tokens").value),
      token_parameter: $("token-parameter").value,
      stream_usage: $("stream-usage").checked,
      timeout,
      budget: {
        max_requests: Number($("request-cap").value),
        max_output_tokens: Number($("token-cap").value),
      },
    };
  }
  function signature(value) {
    return JSON.stringify(value);
  }
  function planNumbers(plan, mode) {
    if (mode === "service")
      return [
        plan.maximum_requests ?? plan.planned_requests,
        plan.maximum_requested_output_tokens,
      ];
    if (mode === "identify")
      return [plan.budget?.max_requests, plan.budget?.max_output_tokens];
    if (mode === "fingerprint")
      return [
        plan.max_requests ?? plan.request_count,
        plan.max_output_tokens_total,
      ];
    return [
      plan.total_requests ?? plan.request_count ?? plan.max_requests,
      plan.max_output_tokens_total ?? plan.total_max_output_tokens,
    ];
  }
  function makePlan(plan, mode, node) {
    node.replaceChildren();
    const values = planNumbers(plan, mode);
    const ns = el("div", "plan-numbers");
    [
      ["最多请求", values[0], "次"],
      ["申请输出配额", values[1], "tokens"],
    ].forEach(([label, v, unit]) => {
      const n = el("div");
      n.append(el("span", "", label));
      const strong = el("strong", "num", num(v));
      strong.append(el("small", "", unit));
      n.append(strong);
      ns.append(n);
    });
    node.append(ns);
    if (mode === "service") {
      node.append(
        el(
          "p",
          "",
          (plan.stages || []).map((s) => s.label).join(" → ") ||
            "按所选检查依次采样。",
        ),
      );
      node.append(el("p", "", "顺序发送，不自动重试。"));
    } else if (mode === "identify") {
      node.append(
        el(
          "p",
          "",
          `一组 4 次回答；最多 ${num(plan.max_completed_cells)} 组，同时比较 ${num(plan.candidate_count ?? state.bank.model_count)} 个标签。`,
        ),
      );
    } else if (mode === "fingerprint") {
      node.append(
        el(
          "p",
          "",
          `${num(plan.probe_count)} 道探针，分 ${num(plan.batch_count)} 批。${plan.profile === "screening" ? "初筛只报告回答差异。" : "按该参考包判据比较。"}`,
        ),
      );
      if (plan.budget_can_complete_minimum === false)
        node.append(
          el("p", "plan-warning", "预算无法覆盖完整计划，只会保存部分证据。"),
        );
    } else node.append(el("p", "", "两端按相同任务和设置采样。"));
    node.hidden = false;
  }
  async function preparePlan() {
    if (state.busy || state.running) return;
    if (!$("api-base").reportValidity() || !$("api-model").reportValidity())
      return;
    showError("");
    setBusy(true);
    const revision = state.planRevision,
      value = payload(false);
    try {
      const result = await api("/api/plan", value);
      if (revision !== state.planRevision) return;
      state.plan = result;
      state.planSignature = signature(value);
      makePlan(result, value.mode, $("plan-box"));
      $("plan-button").hidden = true;
      $("start-button").hidden = false;
      $("replan-button").hidden = false;
      $("start-button").textContent =
        `开始检测 · 最多 ${num(planNumbers(result, value.mode)[0])} 次`;
      $("run-cost-note").textContent =
        "开始后使用待测账户额度。输入和额外推理费用另记。";
    } catch (err) {
      showError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function startPlanned() {
    if (!state.plan || state.planSignature !== signature(payload(false)))
      return showError("配置已改变，请重新查看计划。");
    if (!$("api-key").value.trim())
      return showError("填写本次运行使用的 API Key。");
    if (!$("api-key").reportValidity()) return;
    await submitRun(payload(true));
  }
  async function demo() {
    if (state.view === "identity") {
      if (state.specific) {
        if (!$("reference-pack").value) return toast("先选择一个参考型号。");
        return submitRun({
          mode: "fingerprint_demo",
          reference_pack: $("reference-pack").value,
          profile: $("fingerprint-profile").value,
          variant: "different",
        });
      }
      const count = Number($("identify-budget").value);
      return submitRun({
        mode: "identify_demo",
        budget: { max_requests: count, max_output_tokens: count * 16 },
      });
    }
    const checks =
      state.view === "tools"
        ? ["tools"]
        : $("include-tools").checked
          ? ["stream", "tools"]
          : ["stream"];
    return submitRun({ mode: "service_demo", checks });
  }
  function viewForRun(run) {
    if (run.mode.startsWith("showcase")) return "showcase";
    if (run.mode.startsWith("identify") || run.mode.startsWith("fingerprint"))
      return "identity";
    if (run.mode.startsWith("service")) {
      const report = run.reports?.find(
        (entry) => entry.report.kind === "service_quality",
      )?.report;
      if (report?.checks?.length === 1 && report.checks[0] === "tools")
        return "tools";
      if (!run.reports?.length && state.view === "tools") return "tools";
      return "quality";
    }
    return "research";
  }
  async function submitRun(value) {
    if (state.busy || state.running) return;
    setBusy(true);
    showError("");
    let accepted = false;
    let acceptedRun = null;
    try {
      const run = await api("/api/runs", value);
      accepted = true;
      acceptedRun = run;
      state.running = run.id;
      state.run = run;
      state.runs = [run, ...state.runs.filter((r) => r.id !== run.id)];
      if (["lab", "live", "offline", "demo"].includes(value.mode)) {
        if (state.view !== "research") location.hash = "research";
      } else if (!["quality", "tools", "identity"].includes(state.view))
        location.hash = viewForRun(run);
      $("api-key").value = "";
      $("api-key").type = "password";
      $("toggle-key").setAttribute("aria-pressed", "false");
      $("toggle-key").setAttribute("aria-label", "显示 API Key");
      $("pair-key").value = "";
      invalidatePlan();
      renderProgress(run);
      if (!value.mode.startsWith("showcase")) {
        $("progress-title").tabIndex = -1;
        $("progress-title").focus({ preventScroll: true });
      }
      pollLater(150);
      toast(
        value.mode === "showcase_demo" ? "正在回放已公开作品，无新模型调用。" : value.mode.includes("demo") || value.mode === "lab"
          ? "正在生成合成示例，无模型调用。"
          : "任务已开始，已完成的记录会保留。",
      );
    } catch (err) {
      showError(err.message);
      toast(err.message);
    } finally {
      if (value.candidate) delete value.candidate.api_key;
      if (value.reference) delete value.reference.api_key;
      setBusy(false);
      if (accepted && !value.mode.startsWith("showcase") && matchMedia("(max-width:720px)").matches)
        $("inspection").scrollIntoView({ block: "start", behavior: "smooth" });
    }
    return acceptedRun;
  }
  function pollLater(delay = 800) {
    clearTimeout(state.poll);
    state.poll = setTimeout(pollRun, delay);
  }
  async function pollRun() {
    if (!state.running) return;
    try {
      const run = await api(`/api/runs/${encodeURIComponent(state.running)}`);
      state.run = run;
      state.runs = [run, ...state.runs.filter((r) => r.id !== run.id)];
      const focusWasOnProgress = $("progress-area").contains(
        document.activeElement,
      );
      renderProgress(run);
      if (["queued", "running"].includes(run.status)) pollLater(900);
      else {
        state.running = null;
        const returnFocus = focusWasOnProgress;
        updateButtons();
        renderReports(run);
        if (returnFocus) {
          const heading =
            $("report-container").querySelector("h2") ||
            $("research-result").querySelector("h2");
          if (heading) {
            heading.tabIndex = -1;
            heading.focus({ preventScroll: true });
          }
        }
        toast(
          run.status === "cancelled"
            ? "检测已停止，已完成的记录已保存。"
            : run.reports?.length
              ? "检查完成，结果已保存。"
              : run.error || "运行已结束。",
        );
        await loadHistory(false);
        if (run.status === "failed" && !run.reports?.length)
          toast(run.error || "任务中断，未形成报告。");
      }
    } catch (err) {
      $("progress-message").textContent = err.message;
      pollLater(3500);
    }
  }
  function renderProgress(run) {
    if (run.mode.startsWith("showcase")) {
      window.API_MRI_SHOWCASE?.renderProgress(run);
      return;
    }
    const active = ["queued", "running"].includes(run.status),
      p = run.progress || {};
    $("progress-area").hidden = !active;
    $("progress-title").textContent =
      `${modeNames[run.mode] || "检查"} · ${statusNames[run.status] || run.status}`;
    $("progress-message").textContent = p.message || "正在准备";
    $("progress-count").textContent = p.total
      ? `${num(p.completed || 0)} / ${num(p.total)}`
      : "";
    if (p.total) {
      $("progress").value = Math.min(100, ((p.completed || 0) / p.total) * 100);
    } else $("progress").removeAttribute("value");
    $("cancel-run").disabled = !active;
  }
  function metric(label, value, note) {
    const n = el("div", "metric"),
      strong = el("strong", value === "—" ? "missing" : "num");
    const parts = String(value).match(new RegExp("^(.*?)( tokens/s| ms| s)$"));
    if (parts) {
      strong.append(
        document.createTextNode(parts[1] + " "),
        el("span", "unit", parts[2].trim()),
      );
    } else strong.textContent = value;
    if (String(value).includes(" / ")) strong.classList.add("paired-value");
    n.append(el("span", "", label), strong);
    if (note) n.append(el("small", "", note));
    return n;
  }
  function reportHeader(root, run, report, title, description) {
    const time = el("div", "report-time");
    time.append(
      el(
        "span",
        report.synthetic ? "demo-label" : "",
        report.synthetic ? "合成示例 · 实际模型调用 0 次" : "本次采样",
      ),
      el("span", "", date(run.created_at)),
    );
    const header = el("div", "report-header");
    const signal = document.createElementNS(
      "http://www.w3.org/2000/svg",
      "svg",
    );
    signal.setAttribute("class", "report-signal");
    signal.setAttribute("viewBox", "0 0 48 48");
    signal.setAttribute("aria-hidden", "true");
    signal.innerHTML =
      '<path d="M5 15c7-10 13 10 20 0s13 10 20 0M5 24c7-10 13 10 20 0s13 10 20 0M5 33c7-10 13 10 20 0s13 10 20 0"/>';
    header.append(signal, time, el("h2", "", title));
    if (description) header.append(el("p", "", description));
    root.append(header);
  }
  function section(root, title, note) {
    const s = el("section", "result-section");
    s.append(el("h3", "", title));
    if (note) s.append(el("p", "", note));
    root.append(s);
    return s;
  }
  function detail(root, title, value) {
    const d = el("details", "detail-block");
    d.append(
      el("summary", "", title),
      el(
        "pre",
        "",
        typeof value === "string" ? value : JSON.stringify(value, null, 2),
      ),
    );
    root.append(d);
    return d;
  }
  function table(headers, classes = []) {
    const wrap = el("div", "table-scroll"),
      t = el("table", "result-table"),
      head = el("thead"),
      tr = el("tr");
    headers.forEach((v, i) => {
      const th = el("th", classes[i] || "", v);
      th.scope = "col";
      tr.append(th);
    });
    head.append(tr);
    const body = el("tbody");
    t.append(head, body);
    wrap.append(t);
    return { wrap, t, body };
  }
  function appendUsage(root, report) {
    const cost = report.cost || {},
      usage = cost.usage || {};
    let message;
    if (report.synthetic)
      message = `合成 ${num(cost.simulated_request_count ?? cost.request_count)} 条请求记录，实际模型调用 0 次。`;
    else
      message = `已发送 ${num(cost.real_requests ?? cost.request_count)} 次；申请输出配额 ${num(cost.requested_output_tokens)}。服务自报输入 ${num(usage.prompt_tokens ?? usage.input_tokens)}、输出 ${num(usage.completion_tokens ?? usage.output_tokens)} tokens。`;
    root.append(el("p", "cost-line", message));
    if (!report.synthetic && usage.complete === false)
      root.append(
        el("p", "score-note", "部分请求没有完整返回用量，已报小计保留缺项。"),
      );
  }
  function exports(root, run, entry) {
    const row = el("div", "report-actions");
    const formats = [
      ["json", "JSON 报告"],
      ["md", "Markdown"],
    ];
    if (
      ["service_quality", "identification", "fingerprint"].includes(
        entry.report.kind,
      ) ||
      entry.report.fingerprint
    )
      formats.push(["jsonl", "原始记录 JSONL"]);
    else formats.push(["csv", "逐题 CSV"]);
    for (const [format, label] of formats) {
      const a = el("a", "", label);
      a.href = `/api/runs/${encodeURIComponent(run.id)}/download/${encodeURIComponent(entry.name)}/${format}`;
      a.download = "";
      row.append(a);
    }
    row.append(btn("导入复算", () => openImport(run, entry)));
    root.append(row);
  }
  function recordsDetail(root, run, entry) {
    const d = el("details", "report-records");
    d.append(el("summary", "", "查看已保存的原始记录"));
    d.addEventListener("toggle", async () => {
      if (!d.open || d.dataset.loaded) return;
      d.dataset.loaded = "true";
      const p = el("p", "muted", "正在读取记录…");
      d.append(p);
      try {
        const v = await api(
          `/api/runs/${encodeURIComponent(run.id)}/records?report=${encodeURIComponent(entry.name)}`,
        );
        p.remove();
        d.append(el("pre", "", JSON.stringify(v, null, 2)));
      } catch (err) {
        p.textContent = err.message;
        delete d.dataset.loaded;
      }
    });
    root.append(d);
  }
  function renderService(root, run, entry) {
    const r = entry.report,
      q = r.service_quality || {},
      connection = q.connection || {},
      stream = q.stream || {},
      tools = q.tools || {},
      requests = q.requests || [];
    const toolRows = Object.entries(toolLabels).filter(
      ([id]) => tools[id] && tools[id].reason !== "not_selected",
    );
    const hasStreams = (stream.attempted_samples || 0) > 0;
    const streamTimes = requests
      .filter((v) => v.stage?.startsWith("stream") && !v.error)
      .map((v) => v.timing?.response_ms)
      .filter(finite)
      .sort((a, b) => a - b);
    const streamMedian = streamTimes.length
      ? (streamTimes[Math.floor((streamTimes.length - 1) / 2)] +
          streamTimes[Math.floor(streamTimes.length / 2)]) /
        2
      : null;
    const toolFailures = toolRows.filter(([id]) =>
      ["failed", "error"].includes(tools[id].status),
    ).length;
    reportHeader(
      root,
      run,
      r,
      r.status === "cancelled"
        ? "已停止，记录已保留"
        : r.status === "partial"
          ? "已取得部分检查记录"
          : toolFailures
            ? `有 ${num(toolFailures)} 项工具检查未通过`
            : hasStreams
              ? "请求过程已记录"
              : "工具调用检查已完成",
      hasStreams
        ? `${num(connection.successful_requests)} / ${num(connection.attempted_requests)} 次请求完成。耗时包含网络、网关与模型等待。`
        : "指定调用、自动选择和结果回填分别检查。",
    );
    const metrics = el("div", "metric-strip");
    if (hasStreams) {
      metrics.append(
        metric(
          "首段正文 · 中位数",
          ms(stream.first_content_ms?.median),
          `${num(stream.first_content_ms?.count)} 次有效记录`,
        ),
        metric(
          "全程输出速率 · 中位数",
          finite(stream.output_tokens_per_second?.median)
            ? `${num(stream.output_tokens_per_second.median, 1)} tokens/s`
            : "—",
          "服务自报输出 tokens / 总耗时",
        ),
        metric(
          "流式完成 · 中位数",
          ms(stream.response_ms?.median ?? streamMedian),
          `成功 ${num(stream.successful_samples)} / ${num(stream.attempted_samples)} 次`,
        ),
      );
    } else {
      metrics.append(
        metric("指定工具", toolStatus(tools.tool_forced?.status)),
        metric("结果回填", toolStatus(tools.tool_roundtrip?.status)),
        metric("自动选择", toolStatus(tools.tool_auto?.status)),
      );
    }
    root.append(metrics);
    if (hasStreams) {
      const s = section(
          root,
          "请求时间切片",
          "每行使用同一时间刻度。首段正文不含推理内容；末段正文之后仍可能返回用量。",
        ),
        legend = el("div", "timing-legend");
      [
        ["", "等待正文"],
        ["output", "输出正文"],
        ["tail", "收尾"],
      ].forEach(([cls, label]) =>
        legend.append(el("span", `legend-key ${cls}`, label)),
      );
      s.append(legend);
      const rows = requests.filter((v) => v.stage?.startsWith("stream"));
      const scale = Math.max(1, ...rows.map((v) => v.timing?.response_ms || 0));
      const tb = table(
        ["请求", "时间分布", "首段正文", "总耗时"],
        ["identity-cell", "timing-cell", "num", "num"],
      );
      tb.t.classList.add("timing-table");
      for (const row of rows) {
        const tr = el("tr"),
          name = el("td", "identity-cell");
        name.append(
          el("strong", "", `请求 ${Number(row.index) + 1}`),
          el(
            "small",
            "",
            row.error
              ? errorReason(row.error)
              : row.finish_reason === "length"
                ? "达到输出限制"
                : "已完成",
          ),
        );
        const timing = row.timing || {},
          ct = el("td", "timing-cell"),
          track = el("div", "timing-track");
        const end = timing.response_ms || 0,
          start = timing.first_content_ms,
          last = timing.last_content_ms;
        const pieces = finite(start)
          ? [
              [0, start, "timing-wait"],
              [start, finite(last) ? last : start, "timing-output"],
              [finite(last) ? last : start, end, "timing-tail"],
            ]
          : [[0, end, "timing-wait"]];
        for (const [a, b, cls] of pieces) {
          const v = el("span", cls);
          v.style.left = `${Math.max(0, (a / scale) * 100)}%`;
          v.style.width = `${Math.max(0, ((b - a) / scale) * 100)}%`;
          track.append(v);
        }
        track.setAttribute("role", "img");
        track.setAttribute(
          "aria-label",
          `请求 ${Number(row.index) + 1}，首段正文 ${ms(start)}，完成 ${ms(end)}`,
        );
        ct.append(track);
        const firstTime = el("td", "num timing-first", ms(start));
        firstTime.dataset.label = "首段";
        firstTime.setAttribute("aria-label", `首段正文 ${ms(start)}`);
        const endTime = el("td", "num timing-end", ms(end));
        endTime.dataset.label = "完成";
        endTime.setAttribute("aria-label", `总耗时 ${ms(end)}`);
        tr.append(name, ct, firstTime, endTime);
        tb.body.append(tr);
      }
      s.append(tb.wrap);
      const sc = el("div", "timing-scale");
      sc.append(el("span", "", "0 s"), el("span", "", ms(scale)));
      s.append(sc);
      const d = el("details", "detail-block");
      d.append(el("summary", "", "分段间隔、推理与用量"));
      const vv = table(
        ["指标", "中位数", "P95", "记录数"],
        ["", "num", "num", "num"],
      );
      for (const [label, value, formatter] of [
        ["响应头", connection.headers_ms, ms],
        ["推理首段", stream.first_reasoning_ms, ms],
        ["正文分段间隔", stream.content_event_gaps_ms, ms],
        [
          "可见字符速度",
          stream.characters_per_second,
          (v) => (finite(v) ? `${num(v, 1)} 字符/s` : "—"),
        ],
      ]) {
        const tr = el("tr");
        tr.append(
          el("td", "", label),
          el("td", "num", formatter(value?.median)),
          el("td", "num", formatter(value?.p95)),
          el("td", "num", num(value?.count)),
        );
        vv.body.append(tr);
      }
      d.append(
        vv.wrap,
        el(
          "p",
          "score-note",
          `达到输出限制 ${num(stream.truncated_samples)} 次。分段是传输事件，不能当作 token；少量样本的 P95 仅描述本次记录。`,
        ),
      );
      s.append(d);
    }
    if (toolRows.length) {
      const s = section(root, "工具调用回路");
      for (const [id, [label, note]] of toolRows) {
        const result = tools[id],
          pass = result.status === "passed",
          bad = ["failed", "error"].includes(result.status),
          row = el("div", `tool-step ${pass ? "good" : bad ? "bad" : ""}`),
          body = el("div");
        body.append(el("h4", "", label), el("p", "", note));
        if (result.reason) body.append(el("p", "", errorReason(result.reason)));
        row.append(
          el("span", "step-mark", pass ? "·" : bad ? "×" : "—"),
          body,
          el(
            "span",
            `status-line ${pass ? "good" : bad ? "bad" : ""}`,
            toolStatus(result.status),
          ),
        );
        s.append(row);
      }
      s.append(el("p", "score-note", "工具使用固定的合成数据。"));
    }
    appendUsage(root, r);
    detail(root, "请求详情与测量定义", {
      requests,
      definitions: r.definitions,
      collection: r.collection,
      cost: r.cost,
      notes: r.notes,
    });
    recordsDetail(root, run, entry);
    exports(root, run, entry);
  }
  function toolStatus(v) {
    return (
      {
        passed: "通过",
        failed: "未通过",
        error: "请求失败",
        skipped: "未执行",
      }[v] || "—"
    );
  }
  function errorReason(value) {
    if (typeof value === "object")
      return text(value.code || value.type || value.message || value);
    return (
      {
        timeout: "请求超时",
        generation_error: "生成失败",
        content_filtered: "内容受限",
        api_error: "接口返回错误",
        request_timeout: "请求超时",
        generation_error: "生成失败",
        content_filtered: "内容受限",
        api_error: "接口返回错误",
        network_error: "网络连接失败",
        request_failed: "请求未完成",
        empty_content: "未返回正文",
        invalid_sse_choice: "流式消息格式无效",
        invalid_sse_json: "流式事件不是有效 JSON",
        invalid_stream_response: "流式响应不完整",
        sse_api_error: "服务在流中返回错误",
        permission_error: "服务拒绝访问",
        server_error: "服务端错误",
        stream_not_supported: "接口未返回流式响应",
        invalid_json_response: "响应不是有效 JSON",
        invalid_chat_response: "响应格式不符合接口协议",
        expected_one_tool_call: "未返回恰好一个工具调用",
        invalid_tool_call_id: "工具调用标识无效",
        wrong_tool: "选择了错误工具",
        truncated_tool_call: "工具调用被输出限制截断",
        name_and_arguments_valid: "工具名称、字段和取值均正确",
        invalid_tool_arguments_json: "工具参数无法按 JSON 解析",
        returned_fixture_read: "模型正确读出了工具返回值",
        tool_result_not_reproduced: "模型未按要求返回工具结果",
        direct_answer_without_tool: "直接回答正确，未调用多余工具",
        unneeded_call_or_wrong_answer: "调用了多余工具，或直接回答不符合要求",
        not_reached_or_dependency_failed: "尚未执行，或前序工具调用未通过",
        connect_error: "连接失败",
        rate_limited: "触发限流",
        authentication_error: "鉴权失败",
        http_error: "HTTP 请求失败",
        invalid_arguments: "参数格式或取值不符合要求",
        unexpected_tool: "调用了未指定的工具",
        missing_tool_call: "没有返回工具调用",
        roundtrip_not_available: "前序工具调用未完成",
        unsupported: "接口不支持该参数",
        valid: "格式和取值符合要求",
        cancelled: "已停止",
      }[value] || text(value)
    );
  }
  function renderIdentification(root, run, entry) {
    const r = entry.report,
      c = r.coverage || {},
      e = r.evidence || {},
      ranking = r.ranking || [],
      top = ranking[0];
    reportHeader(
      root,
      run,
      r,
      statusNames[r.status] || "模型候选记录",
      r.status === "candidate_shortlist"
        ? `当前首位为 ${top?.label || top?.model || "—"}。候选由固定探针的回答分布计算。`
        : r.status === "unknown_or_changed"
          ? "当前分布未通过参考库接受条件。最近候选保留供复核版本、配置和路由变化。"
          : "有效回答、候选间隔或采样条件尚不足以形成候选结论。",
    );
    const strip = el("div", "metric-strip");
    strip.append(
      metric(
        "完成探针组",
        num(c.completed_cells),
        `${num(c.valid_answers)} 条有效回答`,
      ),
      metric("比较候选", num(c.compared_candidates), "同一回答复用比较"),
      metric(
        "本次请求",
        num(r.cost?.request_count),
        `申请 ${num(r.cost?.requested_output_tokens)} 输出配额`,
      ),
    );
    root.append(strip);
    const s = section(
        root,
        "候选谱",
        "JSD 距离越小，回答分布越接近。距离与排名间隔均不表示身份概率。",
      ),
      list = el("div", "ranking");
    const makeRow = (row, i) => {
      const n = el("div", "ranking-row"),
        identity = el("div");
      identity.append(
        el("div", "rank-label", row.label || row.model),
        el("div", "rank-description", row.family),
      );
      const bar = el("div", "rank-distance"),
        fill = el("span");
      fill.style.width = `${Math.max(0, Math.min(100, (row.distance || 0) * 100))}%`;
      bar.append(fill);
      identity.append(bar);
      n.append(
        el("span", "rank-index", `${i + 1}.`),
        identity,
        el("span", "rank-value num", num(row.distance, 4)),
      );
      return n;
    };
    ranking.slice(0, 5).forEach((row, i) => list.append(makeRow(row, i)));
    if (!ranking.length)
      list.append(el("p", "empty-list", "尚无完整探针组可比较。"));
    s.append(list);
    if (ranking.length > 5) {
      const d = el("details", "detail-block");
      d.append(el("summary", "", `其余 ${num(ranking.length - 5)} 个候选`));
      const rest = el("div", "ranking");
      ranking.slice(5).forEach((row, i) => rest.append(makeRow(row, i + 5)));
      d.append(rest);
      s.append(d);
    }
    const leave = e.leave_one_probe_out || {};
    if (leave.evaluated) {
      root.append(
        el(
          "p",
          "stability-note",
          `逐次删去一组探针后，首位候选保持 ${num(leave.same_leader_count)} / ${num(leave.evaluated)} 次。这个比例说明本次排序对探针组合的敏感程度。`,
        ),
      );
    }
    if (e.trajectory?.length) {
      const d = el("details", "detail-block");
      d.append(el("summary", "", "候选怎样随回答累积变化"));
      const t = table(
        ["请求", "首位候选", "距离", "间隔"],
        ["num", "", "num", "num"],
      );
      e.trajectory.forEach((v) => {
        const name = state.bank.models?.find((m) => m.model === v.first);
        const tr = el("tr");
        tr.append(
          el("td", "num", num(v.requests)),
          el("td", "", name?.label || text(v.first)),
          el("td", "num", num(v.distance, 4)),
          el("td", "num", num(v.gap, 4)),
        );
        t.body.append(tr);
      });
      d.append(t.wrap);
      root.append(d);
    }
    if (e.per_probe?.length) {
      const ss = section(
        root,
        "哪些回答影响了排序",
        "距离差为次位候选减去首位候选；负值表示该题更接近次位。",
      );
      e.per_probe.forEach((probe) => {
        const d = el("details", "probe-card"),
          ref = probe.reference_comparison || [];
        d.append(
          el(
            "summary",
            "",
            `${probe.task_id === "letter-random" && probe.lang === "zh" ? "随机汉字" : taskNames[probe.task_id] || probe.task_id || probe.cell_id} · ${languageNames[probe.lang] || probe.lang || ""} · 距离差 ${num(probe.leader_margin, 4)}`,
          ),
        );
        if (probe.prompt) d.append(el("pre", "", text(probe.prompt)));
        const groups = el("div", "probe-distribution");
        groups.append(distribution("本次回答", probe.observed_counts));
        ref
          .slice(0, 2)
          .forEach((v) =>
            groups.append(distribution(v.label || v.model, v.reference_counts)),
          );
        d.append(groups);
        ss.append(d);
      });
    }
    const stop = section(root, "停止原因");
    stop.append(el("p", "", stopNames[r.stop_reason] || text(r.stop_reason)));
    if (r.stopping?.early_stopped)
      stop.append(
        el(
          "p",
          "",
          `保留 ${num(r.stopping.unused_request_budget)} 次请求、${num(r.stopping.unused_output_token_cap)} 输出配额未使用。`,
        ),
      );
    appendUsage(root, r);
    detail(root, "参考来源、校准与复算依据", {
      bank: r.bank,
      validation: r.validation,
      calibration: r.calibration,
      provenance: e.provenance,
      stopping: r.stopping,
      invalid_answers: c.invalid_answers,
      leave_one_probe_out: e.leave_one_probe_out,
      notes: r.notes,
    });
    recordsDetail(root, run, entry);
    exports(root, run, entry);
  }
  function distribution(title, counts) {
    const d = el("div");
    d.append(el("h4", "", title));
    Object.entries(counts || {})
      .sort((a, b) => b[1] - a[1])
      .forEach(([answer, n]) => {
        const row = el("div", "probe-answer");
        row.append(el("span", "", answer), el("span", "num", `${num(n)} 次`));
        d.append(row);
      });
    if (!Object.keys(counts || {}).length) d.append(el("p", "muted", "未记录"));
    return d;
  }
  function renderFingerprint(root, run, entry) {
    const r = entry.report,
      fp = r.fingerprint || {},
      summary = fp.summary || {},
      screen = r.profile === "screening";
    reportHeader(
      root,
      run,
      r,
      screen ? "初筛回答已记录" : statusNames[r.status] || "指定型号对照",
      fp.verdict_explanation,
    );
    const strip = el("div", "metric-strip");
    strip.append(
      metric("已解析回答", `${num(summary.answered)} / ${num(summary.total)}`),
      metric("与参考不同", num(summary.mismatches)),
      metric("缺失回答", num(summary.missing)),
    );
    root.append(strip);
    const reference = fp.reference || {};
    root.append(
      el(
        "p",
        "score-note",
        `${reference.label || reference.model || "参考型号"} · ${reference.provider || "提供方未记录"} · 采集 ${dateRange(reference.calibrated_at || reference.collected_at)}`,
      ),
    );
    const s = section(
      root,
      "逐题比较",
      screen ? "少量固定探针用于查看差异，本次初筛不作型号判决。" : null,
    );
    const filter = el("input");
    filter.type = "search";
    filter.placeholder = "搜索题目，或输入“不一致”";
    filter.setAttribute("aria-label", "筛选逐题比较");
    s.append(filter);
    const tb = table(
      ["题目", "参考 / 待测回答", "结果", ""],
      ["identity-cell", "", "", "num"],
    );
    const probes = fp.probes || [];
    let limit = 12;
    function draw() {
      tb.body.replaceChildren();
      const query = filter.value.toLowerCase();
      const found = probes.filter((p) =>
        `${p.name || p.prompt_id || p.id || ""} ${p.question || p.prompt || ""} ${p.domain || ""} ${p.match === false ? "不一致" : ""}`
          .toLowerCase()
          .includes(query),
      );
      found.slice(0, limit).forEach((p) => {
        const tr = el("tr"),
          name = el("td", "identity-cell");
        name.append(
          el("strong", "", p.name || p.prompt_id || p.id || "探针"),
          el("small", "", p.domain || ""),
        );
        const answers = el("td");
        answers.append(
          el("div", "", `参考 ${probeAnswer(p, "reference")}`),
          el("small", "", `待测 ${probeAnswer(p, "candidate")}`),
        );
        const match = p.match ?? p.matched ?? p.is_match;
        const result =
          match === true ? "一致" : match === false ? "不一致" : "缺失";
        const action = el("td");
        action.append(
          btn("展开", () => {
            const details = el("tr"),
              cell = el("td");
            cell.colSpan = 4;
            cell.append(el("pre", "", JSON.stringify(p, null, 2)));
            details.append(cell);
            if (tr.nextElementSibling?.dataset.probeDetails) {
              tr.nextElementSibling.remove();
            } else {
              details.dataset.probeDetails = "true";
              tr.after(details);
            }
          }),
        );
        tr.append(name, answers, el("td", "", result), action);
        tb.body.append(tr);
      });
      more.hidden = limit >= found.length;
    }
    const more = btn("展开更多题目", () => {
      limit += 30;
      draw();
    });
    filter.addEventListener("input", () => {
      limit = 12;
      draw();
    });
    s.append(tb.wrap, more);
    draw();
    appendUsage(root, r);
    studyResult(root, r);
    detail(root, "参考、判据和采样状态", {
      reference,
      settings: fp.settings,
      domains: fp.domains,
      assessment_reasons: fp.assessment_reasons,
      statistics: fp.upstream_statistics,
      collection: r.collection,
      cost: r.cost,
    });
    recordsDetail(root, run, entry);
    exports(root, run, entry);
  }
  function probeAnswer(p, role) {
    const value = p[role];
    if (value && typeof value === "object")
      return text(value.answer ?? value.normalized ?? value.parsed ?? value);
    return text(p[`${role}_answer`] ?? value);
  }
  function renderPair(root, run, entry) {
    const r = entry.report,
      roles = r.diagnostics?.roles || {},
      left = roles.reference || {},
      right = roles.candidate || {};
    reportHeader(
      root,
      run,
      r,
      statusNames[r.status] || "双端点比较结果",
      r.lab?.scenario?.description ||
        "参考与待测端点按相同任务比较，逐题结果保留原始回答。",
    );
    const strip = el("div", "metric-strip");
    strip.append(
      metric(
        "成功率 · 参考 / 待测",
        `${pct(left.success_rate)} / ${pct(right.success_rate)}`,
      ),
      metric(
        "任务通过率",
        `${pct(left.checks?.pass_rate)} / ${pct(right.checks?.pass_rate)}`,
      ),
      metric(
        "完成比较",
        `${num(r.coverage?.tested_prompts)} / ${num(r.coverage?.family_size)}`,
      ),
    );
    root.append(strip);
    const service = section(
      root,
      "服务记录",
      "吞吐采用服务自报输出 token 除以客户端总耗时；工具次数来自记录中的工具字段。",
    );
    const serviceTable = table(
      ["指标", "参考端点", "待测端点"],
      ["", "num", "num"],
    );
    const seconds = (value) => ms(finite(value) ? value * 1000 : null);
    const health = (role) =>
      ({
        healthy: "全部请求成功",
        degraded: "部分请求失败",
        unavailable: "没有成功请求",
      })[role.link_health?.status] || "未记录";
    const toolCalls = (role) =>
      role.tool_calls?.status === "measured"
        ? num(role.tool_calls.total)
        : "未测量";
    for (const [label, a, b] of [
      ["连接状态", health(left), health(right)],
      [
        "响应耗时 · 中位数",
        seconds(left.elapsed_seconds?.median),
        seconds(right.elapsed_seconds?.median),
      ],
      [
        "响应耗时 · P95",
        seconds(left.elapsed_seconds?.p95),
        seconds(right.elapsed_seconds?.p95),
      ],
      [
        "输出吞吐 · token/s",
        num(left.throughput?.output_tokens_per_second_median, 2),
        num(right.throughput?.output_tokens_per_second_median, 2),
      ],
      ["工具调用次数", toolCalls(left), toolCalls(right)],
    ]) {
      const row = el("tr");
      row.append(el("td", "", label), el("td", "num", a), el("td", "num", b));
      serviceTable.body.append(row);
    }
    service.append(serviceTable.wrap);
    const s = section(root, "逐题比较");
    const tb = table(
      ["题目", "结果", "成功回答 · 参考 / 待测", ""],
      ["identity-cell", "", "num", ""],
    );
    for (const p of r.prompt_results || []) {
      const tr = el("tr"),
        action = el("td");
      action.append(
        btn("回答", async () => {
          const rows = await api(
            `/api/runs/${encodeURIComponent(run.id)}/records?report=${encodeURIComponent(entry.name)}`,
          ).catch((err) => ({ error: err.message }));
          const v = {};
          Object.entries(rows).forEach(
            ([key, values]) =>
              (v[key] = Array.isArray(values)
                ? values.filter((row) => row.prompt_id === p.prompt_id)
                : values),
          );
          detail(s, `${p.prompt_id} · 两侧回答`, v).open = true;
        }),
      );
      tr.append(
        el(
          "td",
          "",
          state.probes.find((v) => v.id === p.prompt_id)?.name || p.prompt_id,
        ),
        el("td", "", statusNames[p.status] || p.status),
        el(
          "td",
          "num",
          `${num(p.counts?.reference?.successes)} / ${num(p.counts?.candidate?.successes)}`,
        ),
        action,
      );
      tb.body.append(tr);
    }
    s.append(tb.wrap);
    if (r.lab) detail(root, "情境设置与实验解释", r.lab);
    studyResult(root, r);
    detail(root, "统计设置、覆盖与原因", {
      settings: r.settings,
      coverage: r.coverage,
      reasons: r.reasons,
      diagnostics: r.diagnostics,
    });
    recordsDetail(root, run, entry);
    exports(root, run, entry);
  }
  function studyResult(root, r) {
    if (!Object.keys(r.study || {}).length) return;
    const d = el("details", "detail-block");
    d.append(el("summary", "", "服务声明与研究记录"));
    for (const item of r.claim_matrix || []) {
      const box = el("div", "study-result");
      box.append(
        el("h3", "", item.label),
        el("p", "", `声明：${text(item.claim)}`),
        el("p", "", `本次观察：${text(item.observation)}`),
        el("p", "", `后续证据：${text(item.next_evidence)}`),
      );
      d.append(box);
    }
    d.append(el("pre", "", JSON.stringify(r.study, null, 2)));
    root.append(d);
  }
  function renderReports(run) {
    state.run = run;
    if (run.mode.startsWith("showcase")) {
      window.API_MRI_SHOWCASE?.renderRun(run);
      return;
    }
    const isResearch = ["lab", "live", "offline", "demo"].includes(run.mode),
      target = isResearch ? $("research-result") : $("report-container");
    target.replaceChildren();
    if (!run.reports?.length) {
      if (run.error) {
        target.append(el("p", "notice error", run.error));
        target.hidden = false;
      }
      return;
    }
    if (!isResearch) {
      $("empty-result").hidden = true;
      target.hidden = false;
    } else target.classList.add("research-result-header");
    const content = el("div"),
      tabs = el("div", "report-selector");
    function select(entry) {
      content.replaceChildren();
      [...tabs.children].forEach((b) =>
        b.classList.toggle("selected", b.dataset.name === entry.name),
      );
      const r = entry.report;
      if (r.kind === "service_quality") renderService(content, run, entry);
      else if (r.kind === "identification")
        renderIdentification(content, run, entry);
      else if (r.kind === "fingerprint" || r.fingerprint)
        renderFingerprint(content, run, entry);
      else renderPair(content, run, entry);
    }
    if (run.reports.length > 1) {
      run.reports.forEach((entry) => {
        const b = btn(
          { honest: "同来源", substituted: "替换" }[entry.name] || entry.name,
          () => select(entry),
          "",
        );
        b.dataset.name = entry.name;
        tabs.append(b);
      });
      target.append(tabs);
    }
    target.append(content);
    select(run.reports[0]);
  }
  async function openRun(id) {
    try {
      const run = await api(`/api/runs/${encodeURIComponent(id)}`);
      const view = viewForRun(run);
      if (state.view !== view) {
        location.hash = view;
        route();
      }
      renderProgress(run);
      renderReports(run);
      if (["queued", "running"].includes(run.status)) {
        state.running = id;
        pollLater(100);
        updateButtons();
      }
      if (view !== "showcase" && matchMedia("(max-width:720px)").matches)
        $(
          view === "research" ? "research-result" : "inspection",
        ).scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (err) {
      toast(err.message);
    }
  }
  function renderHistory() {
    const node = $("history-list");
    node.replaceChildren();
    const query = $("history-search").value.toLowerCase();
    const rows = state.runs.filter((r) =>
      JSON.stringify({
        id: r.id,
        mode: modeNames[r.mode],
        created: r.created_at,
        reports: r.reports,
      })
        .toLowerCase()
        .includes(query),
    );
    if (!rows.length) {
      node.append(
        el(
          "p",
          "empty-list",
          query
            ? "没有匹配的运行记录。"
            : !state.ready
              ? "正在读取运行记录…"
              : "还没有运行记录。先试用一份示例，或连接你的 API。",
        ),
      );
      return;
    }
    rows.forEach((run) => {
      const row = el("article", "history-row"),
        name = el("div"),
        reports = run.reports || [],
        report = reports[0]?.report || {};
      name.append(
        el(
          "h3",
          "",
          report.candidate?.model ||
            report.study?.service_name ||
            modeNames[run.mode] ||
            run.mode,
        ),
        el("p", "", report.task?.title || modeNames[run.mode] || run.mode),
      );
      const status = el("div", "history-status");
      status.append(
        el(
          "span",
          `status-line ${run.status === "failed" ? "bad" : ""}`,
          statusNames[run.status] || run.status,
        ),
      );
      if (report.synthetic)
        status.append(el("small", "", "合成数据 · 无模型调用"));
      if (report.public_source_demo)
        status.append(el("small", "", "公开作品回放 · 无新模型调用"));
      row.append(
        name,
        status,
        el("time", "history-date", date(run.created_at)),
        btn("打开", () => openRun(run.id)),
      );
      node.append(row);
    });
  }
  async function loadHistory(resume = true) {
    const data = await api("/api/runs");
    state.runs = data.runs || [];
    renderHistory();
    if (resume) {
      const active = state.runs.find((r) =>
        ["queued", "running"].includes(r.status),
      );
      if (active) {
        state.running = active.id;
        renderProgress(active);
        pollLater(300);
      }
    }
  }
  function renderPacks() {
    const query = $("reference-filter").value.toLowerCase(),
      old = $("reference-pack").value;
    $("reference-pack").replaceChildren();
    const packs = state.packs.filter((p) =>
      `${p.label || ""} ${p.model || ""} ${p.provider || ""} ${p.id}`
        .toLowerCase()
        .includes(query),
    );
    packs.forEach((p) =>
      $("reference-pack").append(
        new Option(`${p.label || p.model} · ${num(p.probe_count)} 题`, p.id),
      ),
    );
    if (packs.some((p) => p.id === old)) $("reference-pack").value = old;
    const pack = state.packs.find((p) => p.id === $("reference-pack").value);
    $("pack-note").textContent = pack
      ? `${pack.provider || "提供方未记录"} · ${dateRange(pack.calibrated_at || pack.collected_at)} · ${pack.license || ""}`
      : "没有匹配的参考型号。";
    const importOld = $("import-pack").value;
    $("import-pack").replaceChildren();
    state.packs.forEach((p) =>
      $("import-pack").append(new Option(p.label || p.model, p.id)),
    );
    if (state.packs.some((p) => p.id === importOld))
      $("import-pack").value = importOld;
  }
  function sourceLink(url, label) {
    const a = el("a", "", label);
    try {
      const parsed = new URL(url);
      if (!["https:", "http:"].includes(parsed.protocol)) throw Error();
      a.href = parsed.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    } catch (_) {
      return el("span", "", label);
    }
    return a;
  }
  function renderLibrary() {
    const bank = state.bank;
    $("library-bank-note").textContent =
      `${dateRange(bank.collected_at)} · One Token Is Enough 公开回答`;
    {
      const n = $("library-bank");
      n.replaceChildren();
      [
        [bank.model_count, "候选标签"],
        [bank.cell_count, "任务与语言组合"],
      ].forEach(([value, label]) => {
        const d = el("div");
        d.append(el("strong", "num", num(value)), el("p", "", label));
        n.append(d);
      });
    }
    const query = $("library-search").value.toLowerCase(),
      rows = state.packs.filter((p) =>
        `${p.label || ""} ${p.model || ""} ${p.provider || ""} ${p.id}`
          .toLowerCase()
          .includes(query),
      );
    $("library-count").textContent = `${num(rows.length)} 份`;
    const tb = table(
      ["型号 / 提供方", "采集日期", "题目", "来源"],
      ["identity-cell", "num", "num", ""],
    );
    tb.t.classList.add("library-table");
    rows.forEach((p) => {
      const tr = el("tr"),
        name = el("td", "identity-cell"),
        source = el("td");
      name.append(
        el("strong", "", p.label || p.model),
        el("small", "", p.provider || "提供方未记录"),
      );
      source.append(
        sourceLink(
          p.source_url || p.source?.url || "https://github.com/Ooo0ption/KBF",
          "原始数据",
        ),
      );
      tr.append(
        name,
        el("td", "num", dateRange(p.calibrated_at || p.collected_at)),
        el("td", "num", num(p.probe_count)),
        source,
      );
      tb.body.append(tr);
    });
    $("library-list").replaceChildren(tb.wrap);
  }
  async function updateLibrary() {
    setBusy(true);
    try {
      const job = await api("/api/reference-library/update", {});
      state.updateRunning = true;
      renderUpdate(job);
      pollUpdate(600);
    } catch (err) {
      toast(err.message);
    } finally {
      setBusy(false);
    }
  }
  function renderUpdate(job) {
    const box = $("library-update");
    box.hidden = false;
    box.replaceChildren();
    box.append(
      el(
        "p",
        "",
        job.error ||
          job.progress?.message ||
          {
            completed: "参考库更新已完成。",
            failed: "参考库更新未完成。",
            cancelled: "已停止更新。",
          }[job.status] ||
          "正在读取固定上游的数据目录。",
      ),
    );
    if (["queued", "running"].includes(job.status)) {
      box.append(
        btn("停止更新", async () => {
          const result = await api(
            "/api/reference-library/update/cancel",
            {},
          ).catch((err) => ({ error: err.message }));
          renderUpdate(result);
        }),
      );
    }
    if (job.result) detail(box, "本次新增、跳过与数据校验", job.result);
  }
  function pollUpdate(delay = 1000) {
    clearTimeout(state.updatePoll);
    state.updatePoll = setTimeout(async () => {
      try {
        const job = await api("/api/reference-library/update");
        renderUpdate(job);
        state.updateRunning = ["queued", "running"].includes(job.status);
        updateButtons();
        if (state.updateRunning) pollUpdate();
        else {
          const library = await api("/api/reference-library");
          state.packs = library.reference_packs || state.packs;
          renderPacks();
          renderLibrary();
        }
      } catch (err) {
        toast(err.message);
        state.updateRunning = false;
        updateButtons();
      }
    }, delay);
  }
  function configureSpecific(specific) {
    state.specific = specific;
    $("identity-candidates").classList.toggle("selected", !specific);
    $("identity-specific").classList.toggle("selected", specific);
    $("identity-candidates").setAttribute("aria-pressed", String(!specific));
    $("identity-specific").setAttribute("aria-pressed", String(specific));
    $("candidate-settings").hidden = specific;
    $("specific-settings").hidden = !specific;
    $("service-budget-fields").hidden = !specific;
    $("no-cache-label").hidden = specific;
    $("stable-stop-field").hidden = specific;
    if (specific) {
      $("request-cap").value = "4";
      $("token-cap").value = "4096";
    }
    invalidatePlan();
  }
  function openImport(run = null, entry = null) {
    const mode = run?.mode || "";
    $("import-kind").value = mode.startsWith("showcase")
      ? "showcase_offline"
      : mode.startsWith("identify")
      ? "identify_offline"
      : mode.startsWith("fingerprint")
        ? "fingerprint_offline"
        : mode.startsWith("service")
          ? "service_offline"
          : mode
            ? "offline"
            : state.view === "identity"
              ? state.specific
                ? "fingerprint_offline"
                : "identify_offline"
              : "service_offline";
    if (entry?.report.fingerprint?.reference?.id)
      $("import-pack").value = entry.report.fingerprint.reference.id;
    importKind();
    showError("", "import-error");
    closeMenu();
    $("import-dialog").showModal();
  }
  function importKind() {
    const kind = $("import-kind").value;
    $("import-pack-field").hidden = kind !== "fingerprint_offline";
    $("import-reference-field").hidden = kind !== "offline";
    $("import-file-label").textContent =
      kind === "offline" ? "待测端记录（最多 2 MB）" : "记录文件（最多 4 MB）";
  }
  async function importRecords(event) {
    event.preventDefault();
    const file = $("import-file").files[0],
      kind = $("import-kind").value,
      limit = kind === "offline" ? 2 : 4;
    if (!file || file.size > limit * 1024 * 1024)
      return showError(
        `选择不超过 ${limit} MB 的 JSONL 文件。`,
        "import-error",
      );
    try {
      const value = { mode: kind };
      if (kind === "offline") {
        const ref = $("import-reference").files[0];
        if (!ref || ref.size > 2 * 1024 * 1024)
          return showError("选择不超过 2 MB 的参考记录。", "import-error");
        value.reference_jsonl = await ref.text();
        value.candidate_jsonl = await file.text();
        value.permutations = 199;
      } else {
        value.evidence_jsonl = await file.text();
        if (kind === "fingerprint_offline")
          value.reference_pack = $("import-pack").value;
      }
      $("import-dialog").close();
      await submitRun(value);
    } catch (err) {
      showError(err.message, "import-error");
    }
  }
  function pairPayload(withKey = false) {
    const selected = [
      ...document.querySelectorAll("#pair-probes input:checked"),
    ].map((c) => state.probes.find((p) => p.id === c.value));
    const value = {
      mode: "live",
      reference: {
        endpoint_id: "reference",
        model_declared: $("pair-model").value.trim(),
        base_url: $("pair-base").value.trim(),
        reference_kind: "unknown",
      },
      candidate: {
        endpoint_id: "candidate",
        model_declared: $("api-model").value.trim(),
        base_url: $("api-base").value.trim(),
        reference_kind: "unknown",
      },
      prompts: selected.map(({ id, input, check }) => ({ id, input, check })),
      repeats: Number($("pair-repeats").value),
      max_tokens: Number($("pair-tokens").value),
      temperature: 0.7,
      permutations: 199,
      min_samples: 8,
      study: {
        service_name: $("study-name").value.trim(),
        model_claim: $("study-claim").value.trim(),
        source_url: $("study-source").value.trim(),
      },
    };
    if (withKey) {
      value.reference.api_key = $("pair-key").value.trim();
      value.candidate.api_key = $("api-key").value.trim();
    }
    return value;
  }
  async function pairPlan(event) {
    event.preventDefault();
    if (!$("api-base").value.trim() || !$("api-model").value.trim())
      return showError("先在检查页填写待测 API 的地址和模型名。", "pair-error");
    if (!document.querySelector("#pair-probes input:checked"))
      return showError("至少选择一道任务。", "pair-error");
    setBusy(true);
    try {
      const value = pairPayload(false),
        plan = await api("/api/plan", value);
      state.pairPlan = plan;
      state.pairSignature = signature(value);
      makePlan(plan, "live", $("pair-plan"));
      $("pair-start").hidden = false;
      showError("", "pair-error");
    } catch (err) {
      showError(err.message, "pair-error");
    } finally {
      setBusy(false);
    }
  }
  async function startPair() {
    if (
      !state.pairPlan ||
      state.pairSignature !== signature(pairPayload(false))
    )
      return showError("配置已改变，请重新核对计划。", "pair-error");
    if (!$("pair-key").value.trim() || !$("api-key").value.trim())
      return showError(
        "请填写两侧 API Key；待测 Key 位于检查页。",
        "pair-error",
      );
    await submitRun(pairPayload(true));
    state.pairPlan = null;
    $("pair-start").hidden = true;
    $("pair-plan").hidden = true;
  }
  function initResearch() {
    const select = $("lab-scenario");
    select.replaceChildren();
    state.scenarios.forEach((s) =>
      select.append(new Option(s.name || s.title || s.id, s.id)),
    );
    scenarioChanged();
    const probes = $("pair-probes");
    probes.replaceChildren();
    state.probes.forEach((p, i) => {
      const label = el("label", "check-label"),
        input = el("input");
      input.type = "checkbox";
      input.value = p.id;
      input.checked = i < 2;
      label.append(input, document.createTextNode(p.name || p.id));
      probes.append(label);
    });
  }
  function scenarioChanged() {
    const s = state.scenarios.find((v) => v.id === $("lab-scenario").value);
    $("lab-description").textContent = s?.description || s?.summary || "";
  }
  async function initialize() {
    setBusy(true);
    try {
      const boot = await api("/api/bootstrap");
      state.token = boot.session_token;
      state.ready = true;
      state.version = boot.version;
      state.embedOrigins = boot.embedding?.origins || [];
      state.bank = boot.identification_bank || { models: [] };
      state.packs = boot.reference_packs || [];
      state.probes = boot.probes || [];
      state.scenarios = boot.scenarios || [];
      $("version").textContent = `API-MRI ${boot.version}`;
      $("bank-summary").textContent =
        `${num(state.bank.model_count)} 个参考标签 · ${dateRange(state.bank.collected_at)} 采集`;
      $("connection-error").hidden = true;
      $("connection-state").classList.remove("offline");
      $("connection-label").textContent = "已连接";
      renderPacks();
      renderLibrary();
      initResearch();
      document.dispatchEvent(new CustomEvent("api-mri:workbench-ready"));
      if (pendingEmbedHello) acknowledgeEmbed(pendingEmbedHello);
      await loadHistory();
      const update = await api("/api/reference-library/update");
      if (["queued", "running"].includes(update.status)) {
        state.updateRunning = true;
        renderUpdate(update);
        pollUpdate();
      }
    } catch (err) {
      state.ready = false;
      $("connection-error").hidden = false;
      $("connection-error-text").textContent = err.message;
      $("connection-state").classList.add("offline");
      $("connection-label").textContent = "未连接";
    } finally {
      setBusy(false);
    }
  }
  $("check-form").addEventListener("submit", (event) => {
    event.preventDefault();
    preparePlan();
  });
  $("start-button").addEventListener("click", startPlanned);
  $("replan-button").addEventListener("click", preparePlan);
  $("try-demo").addEventListener("click", demo);
  $("empty-demo").addEventListener("click", demo);
  $("reconnect").addEventListener("click", initialize);
  $("check-form").addEventListener("input", (event) => {
    if (event.target.id !== "api-key") invalidatePlan();
  });
  $("check-form").addEventListener("change", (event) => {
    if (event.target.id !== "api-key") invalidatePlan();
  });
  $("toggle-key").addEventListener("click", () => {
    const show = $("api-key").type === "password";
    $("api-key").type = show ? "text" : "password";
    $("toggle-key").setAttribute("aria-pressed", String(show));
    $("toggle-key").setAttribute(
      "aria-label",
      show ? "隐藏 API Key" : "显示 API Key",
    );
  });
  $("identify-budget").addEventListener("change", () => {
    const available = Number($("identify-budget").value) === 32;
    $("stable-stop").disabled = !available;
    if (!available) $("stable-stop").checked = false;
  });
  $("identity-candidates").addEventListener("click", () =>
    configureSpecific(false),
  );
  $("identity-specific").addEventListener("click", () =>
    configureSpecific(true),
  );
  $("reference-filter").addEventListener("input", renderPacks);
  $("reference-pack").addEventListener("change", renderPacks);
  $("fingerprint-profile").addEventListener("change", () => {
    if ($("fingerprint-profile").value === "full") {
      $("request-cap").value = "";
      $("token-cap").value = "";
    } else {
      $("request-cap").value = "4";
      $("token-cap").value = "4096";
    }
  });
  $("cancel-run").addEventListener("click", async () => {
    if (!state.running) return;
    $("cancel-run").disabled = true;
    try {
      const r = await api(
        `/api/runs/${encodeURIComponent(state.running)}/cancel`,
        {},
      );
      renderProgress(r);
      $("progress-message").textContent =
        "当前请求结束后停止，已完成记录会保留。";
    } catch (err) {
      toast(err.message);
    }
  });
  $("history-search").addEventListener("input", renderHistory);
  $("refresh-history").addEventListener("click", () =>
    loadHistory().catch((err) => toast(err.message)),
  );
  $("library-search").addEventListener("input", renderLibrary);
  $("update-library").addEventListener("click", updateLibrary);
  $("lab-scenario").addEventListener("change", scenarioChanged);
  $("run-lab").addEventListener("click", () =>
    submitRun({
      mode: "lab",
      scenario: $("lab-scenario").value,
      repeats: 12,
      permutations: 199,
      min_samples: 8,
      seed: 11,
    }),
  );
  $("run-stat-demo").addEventListener("click", () =>
    submitRun({ mode: "demo", repeats: 12, permutations: 199 }),
  );
  $("pair-form").addEventListener("submit", pairPlan);
  $("pair-start").addEventListener("click", startPair);
  $("pair-form").addEventListener("input", () => {
    state.pairPlan = null;
    $("pair-start").hidden = true;
    $("pair-plan").hidden = true;
  });
  ["open-import", "mobile-import"].forEach((id) =>
    $(id).addEventListener("click", () => openImport()),
  );
  $("close-import").addEventListener("click", () => $("import-dialog").close());
  $("import-kind").addEventListener("change", importKind);
  $("import-form").addEventListener("submit", importRecords);
  $("menu-button").addEventListener("click", () => {
    const open = !$("navigation").classList.contains("open");
    $("navigation").classList.toggle("open", open);
    $("menu-button").setAttribute("aria-expanded", String(open));
    $("menu-button").setAttribute("aria-label", open ? "关闭导航" : "打开导航");
    $("main").inert = open;
    if (open) $("navigation").querySelector("[aria-current=page]")?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("navigation").classList.contains("open")) {
      closeMenu();
      $("menu-button").focus();
    }
  });
  let pendingEmbedHello = null;
  function acknowledgeEmbed(event) {
    if (!state.ready) { pendingEmbedHello = event; return; }
    pendingEmbedHello = null;
    if (!state.embedOrigins.includes(event.origin)) return;
    event.source.postMessage({ type: "api-mri:ready", nonce: event.data.nonce, version: state.version }, event.origin);
  }
  window.addEventListener("message", (event) => {
    if (window.parent === window || event.source !== window.parent || event.data?.type !== "api-mri:hello") return;
    if (typeof event.data.nonce !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(event.data.nonce)) return;
    acknowledgeEmbed(event);
  });
  window.apiMRI = Object.freeze({
    request: api,
    submit: submitRun,
    toast,
    ready: () => state.ready,
    locked: () => !state.ready || state.busy || Boolean(state.running) || state.updateRunning,
    connection: () => ({ api_base: $("api-base").value, model: $("api-model").value }),
  });
  window.addEventListener("hashchange", () => route(true));
  window.addEventListener("resize", () => {
    if (innerWidth > 1000) closeMenu();
  });
  route();
  initialize();
})();
