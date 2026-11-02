"""Read score facts from the original public AA table. No model calls."""
from __future__ import annotations
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup

URL='https://artificialanalysis.ai/leaderboards/models'
ROOT=Path(__file__).resolve().parents[1]
def build(raw: bytes) -> dict:
    page=BeautifulSoup(raw,'html.parser')
    rows=[]
    for table in page.select('table'):
        if 'Intelligence Index' not in table.get_text():continue
        for tr in table.select('tbody tr'):
            cells=tr.find_all('td',recursive=False)
            if len(cells)<4:continue
            name=cells[0].get_text(' ',strip=True)
            score=cells[3].get_text(' ',strip=True)
            if not re.fullmatch(r'\d+(?:\.\d+)?',score):continue
            match=re.fullmatch(r'(.+?)\s*\(([^()]*)\)',name)
            model,variant=(match[1],match[2]) if match else (name,'')
            rows.append({'model':model,'name':'Artificial Analysis Intelligence Index',
                'score':float(score),'metric':'index points','variant':variant,
                'version':'snapshot '+date.today().isoformat(),'source':URL,'date':date.today().isoformat(),
                'publisher':'Artificial Analysis','provenance':'original leaderboard table'})
    if len(rows)<10:raise ValueError('AA table changed; keep the previous snapshot')
    return {'schema':'api-mri.benchmark-snapshot.v1','retrieved_at':date.today().isoformat(),
        'sources':[{'url':URL,'sha256':hashlib.sha256(raw).hexdigest()}], 'scores':rows}

if __name__=='__main__':
    response=requests.get(URL,timeout=40);response.raise_for_status()
    value=build(response.content)
    dest=ROOT/'src/relay_audit/data/benchmark_snapshots.json'
    dest.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('Saved',len(value['scores']),'AA score records')
