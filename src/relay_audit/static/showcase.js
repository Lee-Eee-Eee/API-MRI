(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const state = {
    catalog: null, task: "pelican-svg", loading: false, localBusy: false,
    plan: null, signature: null, revision: 0, locked: true, run: null,
    assets: new Map(), requests: { left: 0, right: 0 },
    still: { left: reducedMotion.matches, right: reducedMotion.matches },
  };
  const bridge = () => window.apiMRI;
  const node = (tag, cls, text) => {
    const element = document.createElement(tag);
    if (cls) element.className = cls;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  };
  const number = (value, digits = 0) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits }) : "—";
  const date = (value) => value ? String(value).slice(0, 10) : "未记录";
  function link(label, href, cls) {
    const element = node("a", cls, label);
    try {
      const url = new URL(href, location.origin);
      if (!["https:", "http:"].includes(url.protocol)) return node("span", cls, label);
      element.href = url.href;
    } catch { return node("span", cls, label); }
    element.target = "_blank";
    element.rel = "noopener noreferrer";
    return element;
  }
  function button(label, action, cls = "text-button") {
    const element = node("button", cls, label);
    element.type = "button";
    element.addEventListener("click", action);
    return element;
  }
  function error(message) {
    $("showcase-form-error").textContent = message || "";
    $("showcase-form-error").hidden = !message;
  }
  const task = () => state.catalog?.tasks.find((item) => item.id === state.task);
  const report = () => state.run?.reports?.find((item) => item.name === "showcase")?.report;
  function locked() { return state.locked || state.localBusy || !state.catalog || !bridge()?.ready(); }
  function updateControls() {
    $("showcase-plan-button").disabled = locked();
    $("showcase-start").disabled = locked() || !state.plan;
    $("showcase-to-trial").disabled = !state.catalog;
    $("showcase-plan-button").hidden = Boolean(state.plan);
    $("showcase-api-key").disabled = locked();
  }
  function invalidate() {
    state.plan = null;
    state.signature = null;
    state.revision += 1;
    $("showcase-plan").hidden = true;
    $("showcase-api-key").value = "";
    error("");
    updateControls();
  }
  function payload(withKey = false) {
    const candidate = {
      api_base: $("showcase-api-base").value.trim(),
      model: $("showcase-api-model").value.trim(),
    };
    if (withKey) candidate.api_key = $("showcase-api-key").value.trim();
    return {
      mode: "showcase", task_id: state.task, candidate,
      max_tokens: Number($("showcase-max-tokens").value),
      token_parameter: $("showcase-token-parameter").value,
      timeout: Number($("showcase-timeout").value),
    };
  }
  function fillConnection() {
    const connection = bridge()?.connection?.();
    if (!connection) return;
    if (!$("showcase-api-base").value) $("showcase-api-base").value = connection.api_base || "";
    if (!$("showcase-api-model").value) $("showcase-api-model").value = connection.model || "";
  }
  async function loadCatalog() {
    if (state.loading || !bridge()?.ready()) return;
    state.loading = true;
    $("showcase-load-error").hidden = true;
    try {
      const value = await bridge().request("/api/showcase");
      if (!Array.isArray(value.tasks) || !value.tasks.length || !Array.isArray(value.examples)) throw new Error("题集暂时没有可读取的作品。");
      state.catalog = value;
      $("showcase-task").replaceChildren();
      value.tasks.forEach((item) => {
        const option = node("option", null, item.title);
        option.value = item.id;
        $("showcase-task").append(option);
      });
      if (!value.tasks.some((item) => item.id === state.task)) state.task = value.tasks[0].id;
      $("showcase-task").disabled = false;
      const bundled = value.examples.filter((item) => item.asset).length;
      $("showcase-collection-summary").textContent = `${value.tasks.length} 道题，${bundled} 份随项目提供的作品，${value.examples.length - bundled} 条原作链接。收集于 ${date(value.collected_at)}。`;
      const previous = report();
      selectTask(previous?.task?.id || state.task, { rightTrial: Boolean(previous) });
      fillConnection();
    } catch (err) {
      $("showcase-load-error-text").textContent = err.message;
      $("showcase-load-error").hidden = false;
      for (const side of ["left", "right"]) $("showcase-stage-" + side).replaceChildren(node("p", null, "作品读取失败，可重新读取。"));
    } finally { state.loading = false; updateControls(); }
  }
  function selections() {
    const items = state.catalog.examples.filter((item) => item.task_id === state.task);
    const value = report();
    return { items, trial: value?.task?.id === state.task ? value : null };
  }
  function populateSelect(side, selected) {
    const select = $("showcase-" + side), { items, trial } = selections();
    select.replaceChildren();
    if (trial) {
      const option = node("option", null, `当前记录 · ${trial.candidate?.model || "未记录模型"}`);
      option.value = "__trial__";
      select.append(option);
    }
    for (const [bundled, label] of [[true, "随项目提供"], [false, "查看原站作品"]]) {
      const subset = items.filter((item) => Boolean(item.asset) === bundled);
      if (!subset.length) continue;
      const group = node("optgroup");
      group.label = label;
      subset.forEach((item) => {
        const suffix = item.asset && /-[123ab]\.svg$/i.test(item.asset) ? ` · ${item.asset.split("/").pop().match(/-([123ab])\.svg$/i)[1]}` : "";
        const option = node("option", null, item.model + suffix);
        option.value = item.id;
        group.append(option);
      });
      select.append(group);
    }
    if (!select.options.length) {
      const option = node("option", null, "尚未打包作品");
      option.value = "";
      select.append(option);
      select.disabled = true;
    } else {
      select.disabled = false;
      if (Array.from(select.options).some((item) => item.value === selected)) select.value = selected;
      else select.selectedIndex = 0;
    }
  }
  function selectTask(id, { rightTrial = false } = {}) {
    if (!state.catalog?.tasks.some((item) => item.id === id)) return;
    const changed = state.task !== id;
    state.task = id;
    $("showcase-task").value = id;
    const current = task();
    $("showcase-task-description").textContent = current.description || "";
    $("showcase-prompt").textContent = current.prompt;
    $("showcase-inspect").replaceChildren(...(current.inspect || []).map((value) => node("li", null, value)));
    $("showcase-adaptation").textContent = current.adaptation || current.source_workflow_note || "";
    $("showcase-adaptation").hidden = !$("showcase-adaptation").textContent;
    $("showcase-task-sources").replaceChildren(...(current.sources || []).map((value) => link(value.title || "题目出处", value.url)));
    const available = selections();
    const compare = available.items.length + (available.trial ? 1 : 0) > 1;
    $("showcase-work-right").hidden = !compare;
    $("showcase-view").querySelector(".showcase-gallery").classList.toggle("single-work", !compare);
    const preferred = {
      left: id === "pelican-svg" ? "bbinwang-qwen3-8-27b-ud-q4-k-xl" : "",
      right: rightTrial ? "__trial__" : id === "pelican-svg" ? "nebius-kimi-k2-5-a" : "",
    };
    for (const side of ["left", "right"]) {
      populateSelect(side, preferred[side]);
      renderSelected(side);
    }
    if (changed) invalidate();
    updateControls();
  }
  function pairRow(list, label, value) {
    list.append(node("dt", null, label), node("dd", null, value === null || value === undefined ? "未记录" : value));
  }
  function sourceDetails(example, sourceText) {
    const details = node("details"), summary = node("summary", null, "来源与参数");
    details.append(summary);
    const data = node("dl", "showcase-source-data");
    pairRow(data, "作者", example.source?.author || example.source?.repository || "未记录");
    pairRow(data, "生成日期", date(example.generated_at));
    if (example.source?.published_at) pairRow(data, "发表日期", date(example.source.published_at));
    if (example.source?.snapshot_at) pairRow(data, "代码快照", date(example.source.snapshot_at));
    pairRow(data, "收集日期", date(example.source?.retrieved_at));
    pairRow(data, "许可", typeof example.license === "string" ? example.license : example.license?.spdx || "原站查看");
    const settings = example.reported_settings || {};
    const labels = { provider: "接口", harness: "运行方式", temperature: "温度", top_p: "top-p", top_k: "top-k", reasoning_effort: "推理档位", max_tokens: "输出上限", sampling_profile: "采样档", presence_penalty: "presence penalty", context_window: "上下文", seed: "随机种子" };
    Object.entries(labels).forEach(([key, label]) => {
      if (settings[key] !== null && settings[key] !== undefined) pairRow(data, label, settings[key]);
    });
    if (example.reported_workflow?.api_turns) pairRow(data, "API 回合", number(example.reported_workflow.api_turns));
    if (example.source?.commit) pairRow(data, "固定提交", example.source.commit.slice(0, 12));
    if (example.sha256) pairRow(data, "SHA-256", example.sha256.slice(0, 16) + "…");
    details.append(data);
    const links = node("div", "showcase-source-links");
    if (example.source?.url) links.append(link("原始出处 ↗", example.source.url));
    if (example.artifact_url) links.append(link("查看完整作品 ↗", example.artifact_url));
    details.append(links);
    if (example.notes?.length) details.append(node("p", "showcase-source-note", example.notes.join(" ")));
    if (sourceText !== undefined) {
      const code = node("details"), codeTitle = node("summary", null, "SVG 源码");
      code.append(codeTitle, node("pre", null, sourceText));
      details.append(code);
    }
    return details;
  }
  function staticSvg(source) {
    const parsed = new DOMParser().parseFromString(source, "image/svg+xml");
    if (parsed.querySelector("parsererror")) return source;
    parsed.querySelectorAll("animate, animateTransform, animateMotion, set").forEach((item) => item.remove());
    const style = parsed.createElementNS("http://www.w3.org/2000/svg", "style");
    style.textContent = "* { animation: none !important; transition: none !important; }";
    parsed.documentElement.append(style);
    return new XMLSerializer().serializeToString(parsed);
  }
  function showSvg(stage, source, alt, side, actions) {
    const animated = /<(?:[\w-]+:)?(?:animate|animateTransform|animateMotion|set)\b|@keyframes\b|animation(?:-name)?\s*:/i.test(source);
    const image = node("img");
    image.alt = alt;
    const update = () => { image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(state.still[side] ? staticSvg(source) : source); };
    image.addEventListener("error", () => stage.replaceChildren(node("p", null, "图形未能呈现，原始代码仍可查看。")), { once: true });
    update();
    stage.replaceChildren(image);
    if (animated) {
      const toggle = button("", () => {
        state.still[side] = !state.still[side];
        toggle.textContent = state.still[side] ? "播放动画" : "静止预览";
        toggle.setAttribute("aria-pressed", String(state.still[side]));
        update();
      });
      toggle.textContent = state.still[side] ? "播放动画" : "静止预览";
      toggle.setAttribute("aria-pressed", String(state.still[side]));
      actions.append(toggle);
    }
  }
  async function renderSelected(side) {
    const revision = ++state.requests[side];
    const stage = $("showcase-stage-" + side), caption = $("showcase-caption-" + side);
    caption.replaceChildren();
    stage.setAttribute("aria-busy", "false");
    const id = $("showcase-" + side).value;
    if (id === "__trial__") { renderTrialPreview(side); return; }
    const example = state.catalog.examples.find((item) => item.id === id);
    if (!example) {
      const empty = node("div", "showcase-external");
      empty.append(node("h3", null, task()?.title || ""), node("p", null, "这道题尚未打包公开作品。可以查看原题，或发出一次试作。"));
      for (const value of task()?.sources || []) empty.append(link("查看题目出处 ↗", value.url));
      stage.replaceChildren(empty);
      return;
    }
    if (!example.asset) {
      const external = node("div", "showcase-external");
      const detail = example.reported_outcome === "no_svg_returned" ? "原作者记录了未返回图形的这次运行。" : "作品与完整运行记录保存在原作者页面。";
      external.append(node("h3", null, example.model), node("p", null, detail), link("查看原作 ↗", example.artifact_url || example.source?.url));
      stage.replaceChildren(external);
      caption.append(sourceDetails(example));
      return;
    }
    stage.replaceChildren(node("p", null, "正在读取作品。"));
    stage.setAttribute("aria-busy", "true");
    try {
      let data = state.assets.get(example.id);
      if (!data) {
        data = await bridge().request("/api/showcase/examples/" + encodeURIComponent(example.id));
        state.assets.set(example.id, data);
      }
      if (revision !== state.requests[side]) return;
      if (typeof data.source !== "string") throw new Error("作品没有可读取的 SVG 源码。");
      const line = node("div", "showcase-caption-line");
      line.append(node("span", null, `${example.source?.author || "公开作品"} · ${date(example.source?.snapshot_at || example.source?.published_at)}`));
      showSvg(stage, data.source, `${example.model}：${task().title}`, side, line);
      caption.append(line, sourceDetails(example, data.source));
    } catch (err) {
      if (revision !== state.requests[side]) return;
      const failure = node("div", "showcase-external");
      failure.append(node("p", null, err.message), button("重新读取", () => renderSelected(side)));
      stage.replaceChildren(failure);
    } finally { if (revision === state.requests[side]) stage.setAttribute("aria-busy", "false"); }
  }
  function renderTrialPreview(side) {
    const value = report(), stage = $("showcase-stage-" + side), caption = $("showcase-caption-" + side);
    if (!value) { stage.replaceChildren(node("p", null, "尚无这次试作的作品。")); return; }
    const artifact = value.artifact || {}, line = node("div", "showcase-caption-line");
    const origin = value.public_source_demo ? state.run.mode === "showcase_offline" ? "导入的公开作品" : "公开作品回放" : state.run.mode === "showcase_offline" ? "导入的试作记录" : "本次试作";
    line.append(node("span", null, `${origin} · ${date(value.created_at)}`));
    if (!artifact.format_valid || !artifact.source) stage.replaceChildren(node("p", null, "返回内容未形成完整图形，可在下方查看源码与记录。"));
    else if (artifact.format === "svg") showSvg(stage, artifact.source, `${value.candidate?.model}：${value.task?.title}`, side, line);
    else {
      const frame = node("iframe");
      frame.title = `${value.task?.title || "试作"} HTML 预览`;
      frame.setAttribute("sandbox", "allow-scripts");
      frame.referrerPolicy = "no-referrer";
      frame.src = `/api/runs/${encodeURIComponent(state.run.id)}/showcase-preview`;
      stage.replaceChildren(frame);
    }
    caption.replaceChildren(line);
    if (!artifact.complete) caption.append(node("p", "showcase-source-note", "本次交付未完整结束；当前画面来自已收到的内容。"));
    if (value.public_source_demo && value.published_example) caption.append(sourceDetails(value.published_example, artifact.source));
    else {
      const details = node("details");
      details.append(node("summary", null, "返回源码"), node("pre", null, artifact.source || value.output || ""));
      caption.append(details);
    }
  }
  async function plan() {
    if (locked() || !$("showcase-form").reportValidity()) return;
    const revision = state.revision, value = payload(), signature = JSON.stringify(value);
    state.localBusy = true;
    error(""); updateControls();
    try {
      const result = await bridge().request("/api/plan", value);
      if (revision !== state.revision || signature !== JSON.stringify(payload())) return;
      state.plan = result; state.signature = signature;
      $("showcase-plan-summary").textContent = `${result.task.title} · ${result.candidate.model} · 1 次请求 · 输出上限 ${number(result.maximum_requested_output_tokens)} tokens`;
      $("showcase-plan-endpoint").textContent = result.candidate.api_base;
      $("showcase-plan-note").textContent = result.cost_note;
      $("showcase-plan").hidden = false;
      $("showcase-plan-title").tabIndex = -1;
      $("showcase-plan-title").focus({ preventScroll: true });
    } catch (err) { error(err.message); }
    finally { state.localBusy = false; updateControls(); }
  }
  async function start() {
    if (locked()) return;
    if (!state.plan || state.signature !== JSON.stringify(payload())) { invalidate(); error("配置已改变，请重新核对计划。"); return; }
    const key = $("showcase-api-key");
    if (!key.value.trim() || !key.reportValidity()) { error("请填写这次请求使用的 API Key。"); key.focus(); return; }
    const value = payload(true);
    key.value = "";
    state.localBusy = true; error(""); updateControls();
    try {
      const accepted = await bridge().submit(value);
      if (accepted) {
        state.plan = null; state.signature = null; $("showcase-plan").hidden = true;
        renderProgress(accepted);
        $("showcase-progress-title").focus({ preventScroll: true });
      }
    } catch (err) { error(err.message); }
    finally { delete value.candidate.api_key; state.localBusy = false; updateControls(); }
  }
  function renderProgress(run) {
    if (!run?.mode?.startsWith("showcase")) return;
    state.run = run;
    const active = ["queued", "running"].includes(run.status);
    $("showcase-progress").hidden = !active;
    if (!active) return;
    $("showcase-progress-title").textContent = run.status === "queued" ? "等待试作" : "正在试作";
    $("showcase-progress-message").textContent = run.progress?.message || "等待这一次请求返回，完成后保存原始记录。";
    $("showcase-cancel").disabled = false;
  }
  function metric(list, label, value) {
    const item = node("div"); item.append(node("dt", null, label), node("dd", null, value)); list.append(item);
  }
  function renderRun(run) {
    const value = run?.reports?.find((item) => item.name === "showcase")?.report;
    if (!value) return;
    state.run = run;
    $("showcase-progress").hidden = true;
    $("showcase-result").hidden = false;
    const names = { completed: "已完成", partial: "部分完成", failed: "请求失败", cancelled: "已停止" };
    const offline = run.mode === "showcase_offline";
    $("showcase-result-title").textContent = `${value.task.title} · ${value.candidate.model}`;
    $("showcase-result-status").textContent = `${names[value.status] || value.status} · ${offline ? "导入记录离线复算" : value.public_source_demo ? "公开作品回放" : "单次 API 试作"} · ${date(value.created_at)}`;
    const usage = value.usage || {}, metrics = $("showcase-result-metrics");
    metrics.replaceChildren();
    metric(metrics, offline ? "原记录请求" : "实际请求", number(value.cost?.request_count));
    metric(metrics, "完整响应", typeof value.timing?.elapsed_seconds === "number" ? number(value.timing.elapsed_seconds, 2) + " s" : "—");
    metric(metrics, "输入用量", number(usage.prompt_tokens) + (typeof usage.prompt_tokens === "number" ? " tokens" : ""));
    metric(metrics, "输出用量", number(usage.completion_tokens) + (typeof usage.completion_tokens === "number" ? " tokens" : ""));
    const notes = [];
    if (offline) notes.push("本次复算新增请求：0。");
    if (!value.artifact?.complete) notes.push(value.collection?.stop_reason === "truncated" ? "达到输出上限，代码可能被截断。" : "交付未完整结束，已收到的内容仍保留。");
    if (value.error) notes.push(`记录状态：${value.error}。`);
    if (typeof usage.reasoning_tokens === "number") notes.push(`供应商另报推理用量 ${number(usage.reasoning_tokens)} tokens。`);
    $("showcase-result-note").textContent = notes.join(" ");
    $("showcase-result-note").hidden = !notes.length;
    $("showcase-result-output").textContent = value.output || value.artifact?.source || "未收到正文。";
    $("showcase-result-record").textContent = JSON.stringify({ candidate: value.candidate, usage: value.usage, timing: value.timing, cost: value.cost, collection: value.collection, artifact: { format: value.artifact?.format, complete: value.artifact?.complete, format_valid: value.artifact?.format_valid, sha256: value.artifact?.sha256 } }, null, 2);
    $("showcase-exports").replaceChildren();
    for (const format of ["json", "md", "jsonl"]) {
      const item = node("a", null, format === "md" ? "Markdown" : format.toUpperCase());
      item.href = `/api/runs/${encodeURIComponent(run.id)}/download/showcase/${format}`;
      item.download = "";
      $("showcase-exports").append(item);
    }
    if (state.catalog) selectTask(value.task.id, { rightTrial: true });
    updateControls();
  }
  async function cancel() {
    if (!state.run?.id) return;
    $("showcase-cancel").disabled = true;
    try {
      await bridge().request(`/api/runs/${encodeURIComponent(state.run.id)}/cancel`, {});
      $("showcase-progress-message").textContent = "正在停止；已收到的内容会保留。";
    } catch (err) { error(err.message); $("showcase-cancel").disabled = false; }
  }
  window.API_MRI_SHOWCASE = {
    renderProgress, renderRun,
    setLocked(value) { state.locked = Boolean(value); updateControls(); },
  };
  $("showcase-task").addEventListener("change", (event) => selectTask(event.target.value));
  for (const side of ["left", "right"]) $("showcase-" + side).addEventListener("change", () => renderSelected(side));
  $("showcase-form").addEventListener("submit", (event) => { event.preventDefault(); plan(); });
  for (const event of ["input", "change"]) $("showcase-form").addEventListener(event, invalidate);
  $("showcase-start").addEventListener("click", start);
  $("showcase-change-plan").addEventListener("click", () => { invalidate(); $("showcase-api-base").focus(); });
  $("showcase-cancel").addEventListener("click", cancel);
  $("showcase-reload").addEventListener("click", loadCatalog);
  $("showcase-to-trial").addEventListener("click", () => {
    fillConnection();
    $("showcase-trial").scrollIntoView({ behavior: reducedMotion.matches ? "instant" : "smooth", block: "start" });
    $("showcase-api-base").focus({ preventScroll: true });
  });
  $("showcase-copy-prompt").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(task()?.prompt || ""); bridge()?.toast("提示已复制。"); }
    catch { bridge()?.toast("复制失败，可在提示中选取文本。"); }
  });
  document.addEventListener("api-mri:workbench-ready", () => { state.locked = bridge().locked(); loadCatalog(); });
  document.addEventListener("api-mri:view", (event) => {
    if (event.detail?.view === "showcase") { fillConnection(); if (!state.catalog) loadCatalog(); }
  });
  reducedMotion.addEventListener("change", (event) => {
    state.still.left = state.still.right = event.matches;
    if (state.catalog) for (const side of ["left", "right"]) renderSelected(side);
  });
  if (bridge()?.ready()) { state.locked = bridge().locked(); loadCatalog(); }
})();
