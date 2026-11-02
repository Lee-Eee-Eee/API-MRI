(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let catalog, loading = false;
  const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!=null)n.textContent=String(text); if(cls)n.className=cls; return n;};
  function link(text, value) {
    const n=el('a',text); try {const url=new URL(value); if(url.protocol!=='https:')return el('span',text); n.href=url.href;}catch{return el('span',text);}
    n.target='_blank'; n.rel='noopener noreferrer'; return n;
  }
  function options() {
    if(!catalog)return;
    const q=$('capability-query').value.toLowerCase(), previous=$('capability-model').value;
    const models=catalog.models.filter(m=>m.benchmarks?.length && `${m.id} ${m.name}`.toLowerCase().includes(q))
      .sort((a,b)=>(b.release_date||'').localeCompare(a.release_date||'')||a.name.localeCompare(b.name));
    $('capability-model').replaceChildren(...models.map(m=>{const n=el('option',`${m.name} · ${m.release_date||'日期未收录'}`);n.value=m.id;return n;}));
    if(models.some(m=>m.id===previous))$('capability-model').value=previous;
    render();
  }
  function render() {
    const root=$('capability-scores');root.replaceChildren();
    const m=catalog.models.find(m=>m.id===$('capability-model').value);
    if(!m){root.append(el('p','没有匹配的公开成绩。'));return;}
    const title=el('div',null,'capability-heading');title.append(el('h2',m.name),el('span',`${m.benchmarks.length} 条来源成绩`));root.append(title);
    root.append(el('p',`公开成绩快照 · ${catalog.retrieved_at}。models.dev 汇集记录与原榜快照分项保留；查看每行出处和运行条件。`,'muted'));
    const wrap=el('div',null,'capability-table-wrap'),table=el('table',null,'capability-table');
    const head=el('thead'),hr=el('tr');for(const text of ['评测 / 版本','成绩 / 单位','运行条件','出处'])hr.append(el('th',text));head.append(hr);table.append(head);
    const body=el('tbody');
    for(const b of m.benchmarks){const row=el('tr');row.append(el('td',[b.name,b.version].filter(Boolean).join(' · ')),
      el('td',`${b.score ?? '—'} ${b.metric||''}`),el('td',[b.harness,b.variant,b.dataset].filter(Boolean).join(' · ')||'—'));
      const td=el('td');td.append(link(b.date||'原始发布',b.source));row.append(td);body.append(row);}
    table.append(body);wrap.append(table);root.append(wrap);
  }
  async function load(){
    if(catalog||loading||!window.apiMRI?.ready())return;loading=true;
    try{catalog=await window.apiMRI.request('/api/capability-reference');options();
      $('benchmark-directory').replaceChildren(...catalog.benchmarks.map(b=>{
        const box=el('article'),title=el('h3');title.append(link(b.name+' ↗',b.url));
        box.append(el('span',b.category,'benchmark-category'),title,el('p',b.method),el('p',`对照条件：${b.compare}`,'muted'));return box;
      }));
    }catch(e){$('capability-scores').replaceChildren(el('p',e.message));}finally{loading=false;}
  }
  $('capability-model').addEventListener('change',render);$('capability-query').addEventListener('input',options);
  document.addEventListener('api-mri:workbench-ready',load);
  document.addEventListener('api-mri:view',e=>{if(e.detail?.view==='capabilities')load();});
  if(window.apiMRI?.ready())load();
})();
