"""Import pinned public galleries. No model calls or downloaded code execution."""
import concurrent.futures as cf
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'src/relay_audit/data/showcase'
CACHE = ROOT.parent / 'tmp/showcase-expansion'
DATE = '2026-11-01'
PINS = {'vastxie/pelican-gallery-data':'641d786c2f9d5725e25a5417c9785f05fa88bc01',
        'jaymollica/frogs':'0b074c7946902ed4fd52d44d5cff174acc961634',
        'jdmnk/design-benchmark':'42109688e5f333143c116d6ef0161e4cabc662cd',
        'diegocp01/html_ai_battle':'2cca2683469911e10a4bf2c9b4a9cab4589767cc'}

def get(url):
    path = CACHE / hashlib.sha256(url.encode()).hexdigest()
    if path.exists(): return path.read_bytes()
    r = requests.get(url, timeout=35); r.raise_for_status()
    path.write_bytes(r.content)
    return r.content

def raw(repo, path): return get(f'https://raw.githubusercontent.com/{repo}/{PINS[repo]}/{path}')
def blob(repo, path): return f'https://github.com/{repo}/blob/{PINS[repo]}/{path}'
def key(value): return re.sub('[^a-z0-9]+','-',value.lower()).strip('-')[:65]

def main():
    catalog = json.loads((DATA/'catalog.json').read_text(encoding='utf-8'))
    # Re-runs replace only this importer’s entries; all earlier observations survive.
    catalog['tasks'] = [t for t in catalog['tasks'] if t.get('collection') != 'expanded-20261101']
    catalog['examples'] = [e for e in catalog['examples'] if e.get('collection') != 'expanded-20261101']
    tasks, examples = catalog['tasks'], catalog['examples']
    def task(tid, title, prompt, fmt, category, url, **extra):
        if tid not in {t['id'] for t in tasks}:
            tasks.append(dict(id=tid,title=title,prompt=prompt,format=fmt,category=category,
                sources=[dict(url=url)],collection='expanded-20261101',**extra))
        return tid
    def example(tid, model, url, fmt='svg', **extra):
        eid='expanded-'+hashlib.sha256((tid+model+url).encode()).hexdigest()[:24]
        e=dict(id=eid,task_id=tid,model=model,format=fmt,display='external_link',license=None,
            source=dict(url=url,retrieved_at=DATE),origin='published_model_output',
            model_label_provenance=dict(kind='author_reported',evidence=url,independently_verified=False),
            reported_settings={},collection='expanded-20261101',**extra)
        examples.append(e); return e
    # Gally: all 30 prompts and all actual model/output links, not two examples.
    base='https://gally.net/temp/20251107pelican-alternatives/'
    soup=BeautifulSoup(get(base+'index.html'),'html.parser')
    titles=['章鱼弹管风琴','长颈鹿组装落地钟','海星开推土机','驼鹿指挥旋转木马','火烈鸟修望远镜','刺猬拉手风琴','水母驾驶摩天轮','大象打字','变色龙调钢琴','企鹅抛接电锯','树懒开挖掘机','蜻蜓平衡吊灯','犀牛画灯塔','海马用显微镜','孔雀拉陶坯','袋鼠爬无线电塔','龙虾擦竖琴','豪猪推割草机','壁虎装卫星天线','鬣蜥雕图腾柱','犰狳抬吊桥','螳螂看六分仪','鸵鸟拉黄包车','鱿鱼拆印刷机','蝴蝶检查蒸汽机','螃蟹下消防梯','捕蝇草吞路灯','珊瑚清洁船舵','海葵穿织布机','兰花撑藤架']
    for i, section in enumerate(soup.select('.prompt-section')):
        prompt=re.sub(r'^#\d+\s*','',section.select_one('.prompt-header').get_text(' ',strip=True))
        tid=next((t['id'] for t in tasks if t['prompt'].strip().rstrip('.')==prompt),f'gally-{i+1:02}')
        task(tid,titles[i],prompt,'svg','空间关系',base+'index.html')
        for card in section.select('.model-card'):
            img=card.select_one('img'); name=card.select_one('.model-name')
            if img and name:
                e=example(tid,name.get_text(' ',strip=True),urljoin(base,img['src']))
                e['remote_preview_url']=urljoin(base,img['src'])
    # Newer animation runs: retain individual budgets/harnesses, never merge modes.
    repo='vastxie/pelican-gallery-data'; d=json.loads(raw(repo,'results.json'))
    tid=task('pelican-animation-cn','鹈鹕骑车 · 中文动画',d['prompt'],'html','动画与机构',blob(repo,'results.json'))
    for row in d['results']:
        e=example(tid,row['model'],blob(repo,row['artifact']),'html')
        e['reported_settings']={k:row.get(k) for k in ('reasoning_effort','harness','harness_version','configuration_label','model_id')}
        e['reported_duration_seconds']=row.get('duration_seconds');e['reported_cost_usd']=row.get('cost_usd')
        e['generated_at']=row.get('generated_at');e['reported_artifact_sha256']=row.get('sha256')
        e['source'].update(repository=repo,commit=PINS[repo])
    # MIT-declared committed design runs; network-dependent WebGL stays link-only.
    repo='jdmnk/design-benchmark'
    tree=json.loads((CACHE/'design-benchmark.json').read_text())['tree']
    summaries=[x['path'] for x in tree if x['path'].startswith('examples/') and x['path'].endswith('/summary.json')]
    jobs=[]
    license_path=DATA/'LICENSES/design-benchmark-README.md';license_path.write_bytes(raw(repo,'README.md'))
    names={'pulsar-css':'脉冲星灯塔 · 纯CSS','black-hole-css':'黑洞吸积盘 · 纯CSS','black-hole-spin':'旋转黑洞','black-hole-spin-sota':'旋转黑洞 · SOTA','jupiter':'木星云层','ringed-giant':'行星环','ringed-giant-sota':'行星环 · SOTA'}
    for path in summaries:
        d=json.loads(raw(repo,path));name=d['name'];tid='design-'+key(path[len('examples/'):].replace('/summary.json',''))
        config_path=f'config/examples/{name}.config.json'
        try: config=json.loads(raw(repo,config_path))
        except requests.HTTPError: config={}
        task(tid,names.get(name,name),d['prompt'],'html','科学可视化',blob(repo,path),system_prompt=config.get('systemPrompt',''))
        for row in d['models']:
            artifact=str(Path(path).parent/'pages'/f"{row['slug']}.html").replace('\\','/')
            e=example(tid,row['label'],blob(repo,artifact),'html')
            e['reported_settings']={'provider':row.get('provider'),'model_id':row.get('modelId'),'system_prompt':config.get('systemPrompt')}
            e['reported_usage']=row.get('usage');e['reported_duration_seconds']=row.get('elapsedMs',0)/1000
            e['generated_at']=row.get('startedAt');e['source'].update(repository=repo,commit=PINS[repo])
            if row.get('ok') and ('css' in name): jobs.append((e,repo,artifact,'MIT','LICENSES/design-benchmark-README.md'))
    # FROGS: latest actual attempt per model and prompt arm, not cherry-picked score.
    repo='jaymollica/frogs';tree=json.loads((CACHE/'frogs.json').read_text())['tree']
    readme=raw(repo,'README.md').decode();prompt=raw(repo,'prompt.txt').decode().strip()
    (DATA/'LICENSES/FROGS-CC-BY-4.0.txt').write_bytes(raw(repo,'LICENSE-DATA'))
    latest={}
    for path in sorted(x['path'] for x in tree if x['path'].endswith('/legacy-meta.json')):
        for row in json.loads(raw(repo,path)):
            variant=row.get('variant','habsburg'); k=(row['model'],variant)
            if k not in latest or row.get('ts',0)>latest[k][1].get('ts',0): latest[k]=(path,row)
    for (model,variant),(path,row) in latest.items():
        # Exact prompts for alternate arms are taken from the canonical prompt files.
        if variant!='habsburg': continue
        tid=task('frogs-jaw','青蛙下颌 · 重复采样',prompt,'svg','结构与语义',blob(repo,'prompt.txt'))
        artifact=str(Path(path).parent/'svg'/row['file']).replace('\\','/') if row.get('file') else None
        if not artifact: continue
        e=example(tid,model,blob(repo,artifact));e['reported_settings']={'provider':'OpenRouter','harness':'FROGS','system_prompt':None}
        e['generated_at']=datetime.fromtimestamp(row['ts'],timezone.utc).isoformat() if row.get('ts') else None
        e['reported_usage']={'completion_tokens':row.get('tokens_out')};e['reported_duration_seconds']=row.get('duration_ms',0)/1000
        e['source'].update(repository=repo,commit=PINS[repo],author='Jay Mollica / FROGS benchmark')
        if row.get('ok'):jobs.append((e,repo,artifact,'CC-BY-4.0','LICENSES/FROGS-CC-BY-4.0.txt'))
    # Wider practical HTML tasks: preserve original one-shot prompts and artifact links.
    repo='diegocp01/html_ai_battle';tree=json.loads((CACHE/'html_ai_battle.json').read_text())['tree']
    for path in [x['path'] for x in tree if x['path'].startswith('experiments/') and x['path'].endswith('/README.md')]:
        text=raw(repo,path).decode();s=BeautifulSoup('', 'html.parser')
        blocks=re.findall(r'```[^\n]*\n(.*?)```',text,re.S)
        original=re.search(r'##[^\n]*Original Prompt\s*\n(.*?)(?:\n---|\n##)',text,re.S)
        prompt=original.group(1).strip() if original else next((b.strip() for b in blocks if len(b)>80 and '<html' not in b.lower()),'')
        prompt=re.sub(r'^```[^\n]*\n|\n```$','',prompt).strip()
        if not prompt:continue
        folder=str(Path(path).parent).replace('\\','/');name=Path(folder).name
        label={'airplane':'飞机降落','backflip':'人体后空翻','basketball':'篮球罚球','blooming_flower':'花朵绽放','dna':'DNA双螺旋','dog_fetch':'狗追球','drifting_car':'赛车漂移','dvd_logo':'DVD标志碰撞','flappy_bird':'Flappy Bird游戏','gravity_cloth':'重力布料','new_years':'跨年倒计时','paper_airplane':'纸飞机','physics_simulation':'粒子物理模拟','pirate_ship':'海盗船','snake':'贪吃蛇游戏','spaceship':'飞船','spongebob':'卡通角色','super_mario':'平台跳跃游戏'}.get(re.sub(r'_\d+$','',name),name.replace('_',' '))
        tid=task('battle-'+key(name),label,prompt[:16000],'html','交互与模拟',blob(repo,path))
        for x in tree:
            if x['path'].startswith(folder+'/') and x['path'].endswith('.html'):
                example(tid,Path(x['path']).stem,blob(repo,x['path']),'html')
    def install(job):
        e,repo,path,license_id,license_file=job
        try:
            raw_bytes=raw(repo,path)
            if len(raw_bytes)>256*1024: return
            text=raw_bytes.decode('utf-8')
            if e['format']=='svg':
                root=ET.fromstring(text)
                if root.tag.split('}')[-1]!='svg' or re.search(r'<script|\bon\w+\s*=|(?:href|src)\s*=\s*[\"\'](?:https?:|//)',text,re.I):return
            elif re.search(r'(?:src|href)\s*=\s*[\"\'](?:https?:|//)|\bimport\s',text,re.I):return
            suffix='.'+e['format'];filename=e['id']+suffix;(DATA/'assets'/filename).write_bytes(raw_bytes)
            e.update(asset='assets/'+filename,sha256=hashlib.sha256(raw_bytes).hexdigest(),size_bytes=len(raw_bytes),display='bundled_asset',license={'spdx':license_id,'file':license_file},asset_changes='none')
        except (requests.RequestException,ValueError,ET.ParseError):pass
    with cf.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(install,jobs))
    for t in tasks:t.setdefault('category',{'svg':'空间关系','html':'动画与机构'}[t['format']])
    assert len(tasks)<=100 and len(examples)<=1000
    catalog['collected_at']=DATE
    (DATA/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    stats=dict(tasks=len(tasks),examples=len(examples),model_labels=len({e['model'] for e in examples}),bundled=sum(bool(e.get('asset')) for e in examples),bundled_tasks=len({e['task_id'] for e in examples if e.get('asset')}),pins=PINS)
    (DATA/'expansion-20261101.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False))

if __name__=='__main__':main()
