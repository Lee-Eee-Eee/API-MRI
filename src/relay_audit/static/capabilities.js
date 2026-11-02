(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  };
  const number = value => typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('zh-CN', {maximumFractionDigits: 4}) : '—';
  let catalog, loading = false, limit = 20;

  function link(text, value) {
    const n = el('a', text);
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.username || url.password) return el('span', text);
      n.href = url.href;
    } catch { return el('span', text); }
    n.target = '_blank'; n.rel = 'noopener noreferrer';
    return n;
  }
  const board = () => catalog?.boards.find(b => b.id === $('capability-cohort').value);
  const version = value => value?.replace(/^snapshot /, '快照 ') || '版本未标注';
  function cohortLabel(b) {
    let host = '';
    try { host = new URL(b.source).hostname.replace(/^www\./, ''); } catch {}
    const stamp = b.date && !(b.version || '').includes(b.date) ? b.date : '';
    return [version(b.version), b.dataset, b.harness, b.condition, b.metric, host, stamp].filter(Boolean).join(' · ');
  }
  function chooseBenchmark(preferred) {
    const choices = catalog.boards.filter(b => b.name === $('capability-benchmark').value);
    const select = $('capability-cohort');
    const labels = choices.map(cohortLabel);
    select.replaceChildren(...choices.map(b => {
      let label = cohortLabel(b);
      if (labels.filter(value => value === label).length > 1) {
        try { label += ' · ' + decodeURIComponent(new URL(b.source).pathname).replace(/\/$/, '').split('/').filter(Boolean).slice(-2).join('/'); } catch {}
      }
      const option = el('option', label); option.value = b.id; return option;
    }));
    if (choices.some(b => b.id === preferred)) select.value = preferred;
    select.disabled = !choices.length;
    limit = 20; render();
  }
  function renderDirectory() {
    $('benchmark-directory').replaceChildren(...catalog.benchmarks.map(b => {
      const box = el('article'), title = el('h3'); title.append(link(b.name + ' ↗', b.url));
      box.append(el('span', b.category, 'benchmark-category'), title, el('p', b.method), el('p', b.compare, 'muted'));
      return box;
    }));
  }
  function rowDetails(item, b) {
    const content = el('div', null, 'ranking-details');
    const metadata = el('dl');
    for (const [label, value] of [['模型标识', item.model_id], ['模型发布', item.release_date], ['成绩日期', item.date]]) {
      if (value) metadata.append(el('dt', label), el('dd', value));
    }
    content.append(metadata);
    const variants = item.variants || [];
    if (variants.length > 1) {
      content.append(el('p', `已收录的 ${variants.length} 份配置`, 'ranking-detail-title'));
      const table = el('table', null, 'ranking-variants');
      const head = el('thead'), hrow = el('tr');
      for (const name of ['运行配置', '得分', '日期']) hrow.append(el('th', name));
      head.append(hrow); table.append(head);
      const body = el('tbody');
      for (const value of variants) {
        const row = el('tr');
        row.append(el('td', value.variant || '—'), el('td', number(value.score)), el('td', value.date || '—'));
        body.append(row);
      }
      table.append(body); content.append(table);
    }
    content.append(link('查看成绩出处 ↗', item.source || b.source));
    return content;
  }
  function render() {
    const root = $('capability-scores'); root.replaceChildren();
    const b = board();
    if (!b) { root.append(el('p', '暂无可排列的同组成绩。')); return; }
    const query = $('capability-query').value.trim().toLowerCase();
    const rows = b.rows.filter(r => !query || `${r.model} ${r.model_id}`.toLowerCase().includes(query));
    const heading = el('div', null, 'capability-heading');
    heading.append(el('h2', b.name), link('原始来源 ↗', b.source)); root.append(heading);
    const note = el('p', null, 'ranking-context');
    note.append(el('span', b.source_kind === 'snapshot' ? '快照内排名' : '来源内排名'),
      el('span', `${b.model_count} 个型号 · ${b.record_count} 份配置`),
      el('span', b.date || (catalog.retrieved_at ? `收录于 ${catalog.retrieved_at}` : '日期未标注')));
    root.append(note);
    root.append(el('p', b.direction
      ? `${b.metric || '原始分值'} · ${b.direction === 'lower' ? '越低越好' : '越高越好'} · 每型号展示已收录配置中的最佳成绩`
      : '原来源未明确计分方向，暂按模型名称列出。', 'ranking-rule'));
    if (!rows.length) { root.append(el('p', '此榜单没有匹配的型号。', 'ranking-empty')); return; }

    const wrap = el('div', null, 'capability-table-wrap'), table = el('table', null, 'capability-table ranking-table');
    const caption = el('caption', `${b.name}，${cohortLabel(b)}。展开型号查看配置与出处。`, 'sr-only');
    table.append(caption);
    const head = el('thead'), hrow = el('tr');
    for (const name of ['名次', '模型', '得分', '运行配置']) { const th = el('th', name); th.scope = 'col'; hrow.append(th); }
    head.append(hrow); table.append(head);
    const body = el('tbody');
    rows.slice(0, limit).forEach((item, index) => {
      const row = el('tr', null, item.rank != null && item.rank <= 3 ? 'ranking-leading' : '');
      const place = el('th', item.rank == null ? '—' : item.rank, 'ranking-place'); place.scope = 'row';
      const model = el('td'), toggle = el('button', null, 'ranking-model'); toggle.type = 'button';
      const detailId = `ranking-detail-${index}`;
      toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-controls', detailId);
      const chevron = el('span', '⌄', 'ranking-chevron'); chevron.setAttribute('aria-hidden', 'true');
      toggle.append(el('span', item.model), chevron); model.append(toggle);
      const score = el('td', number(item.score), 'ranking-score'); score.title = String(item.score);
      row.append(place, model, score, el('td', item.variant || '—', 'ranking-configuration'));
      const detail = el('tr', null, 'ranking-detail-row'); detail.id = detailId; detail.hidden = true;
      const cell = el('td'); cell.colSpan = 4; detail.append(cell);
      toggle.addEventListener('click', () => {
        const open = detail.hidden; detail.hidden = !open;
        toggle.setAttribute('aria-expanded', String(open));
        if (open && !cell.childNodes.length) cell.append(rowDetails(item, b));
      });
      body.append(row, detail);
    });
    table.append(body); wrap.append(table); root.append(wrap);
    if (rows.length > limit) {
      const more = el('button', `显示其余 ${rows.length - limit} 个型号`, 'text-button ranking-more'); more.type = 'button';
      more.addEventListener('click', () => {
        const firstNew = limit; limit = rows.length; render();
        root.querySelectorAll('.ranking-model')[firstNew]?.focus();
      }); root.append(more);
    }
  }
  async function load() {
    if (catalog || loading || !window.apiMRI?.ready()) return;
    loading = true;
    try {
      catalog = await window.apiMRI.request('/api/capability-rankings');
      const preferred = catalog.boards.find(b => b.id === catalog.default_board) || catalog.boards[0];
      const names = [...new Set(catalog.boards.map(b => b.name))];
      $('capability-benchmark').replaceChildren(...names.map(name => { const n = el('option', name); n.value = name; return n; }));
      $('capability-benchmark').disabled = !names.length;
      if (preferred) $('capability-benchmark').value = preferred.name;
      chooseBenchmark(preferred?.id); renderDirectory();
    } catch (e) {
      catalog = null;
      const retry = el('button', '重新读取', 'text-button'); retry.type = 'button'; retry.addEventListener('click', load);
      $('capability-scores').replaceChildren(el('p', e.message), retry);
    } finally { loading = false; }
  }
  async function loadDataStatus() {
    try {
      const data = await window.apiMRI.request('/api/data-status');
      const table = el('table', null, 'capability-table'), body = el('tbody');
      for (const row of data.entries) {
        const tr = el('tr'); tr.append(el('td', row.name), el('td', row.date || '日期未记录'), el('td', row.policy)); body.append(tr);
      }
      table.append(body);
      $('data-status-table').replaceChildren(table, el('p', '指纹分布、效果题目与榜单分别持有版本。资料更新不改变旧记录采用的参考和方法。', 'muted'));
    } catch { $('data-status-table').textContent = '资料版本暂时无法读取。'; }
  }
  $('capability-benchmark').addEventListener('change', () => chooseBenchmark());
  $('capability-cohort').addEventListener('change', () => { limit = 20; render(); });
  $('capability-query').addEventListener('input', () => { limit = 20; render(); });
  document.addEventListener('api-mri:workbench-ready', () => { loadDataStatus(); if (location.hash === '#capabilities') load(); });
  document.addEventListener('api-mri:view', e => { if (e.detail?.view === 'capabilities') load(); });
  if (window.apiMRI?.ready()) { loadDataStatus(); if (location.hash === '#capabilities') load(); }
})();
