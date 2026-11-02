"""Create a small candidate ledger; never activate fingerprints or execute remote code."""
from datetime import date
import hashlib
import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'src/relay_audit/data'
WATCH=[
    ('kbf','https://api.github.com/repos/Ooo0ption/KBF/commits/main','sha','68aefbbab879469152aaac9fd988a86c30ee4739'),
    ('pamela','https://zenodo.org/api/records/21278557','updated',None),
    ('pamela-config','https://zenodo.org/api/records/21278793','updated',None),
]
def main():
    output=ROOT/'build/data-candidates';output.mkdir(parents=True,exist_ok=True)
    files={}
    for name in ('model_reference.json','benchmark_snapshots.json'):
        raw=(DATA/name).read_bytes()
        value=json.loads(raw)
        if len(raw)>4*1024*1024 or not isinstance(value,dict):raise ValueError('Candidate data too large or invalid')
        (output/name).write_bytes(raw)
        files[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    watch=[]
    for kind,url,key,pinned in WATCH:
        try:
            r=requests.get(url,timeout=20);r.raise_for_status();remote=r.json()
            latest=remote.get(key)
            active=pinned
            state='review_upstream' if pinned and latest!=pinned else 'metadata_observed'
            if kind.startswith('pamela'):
                result=requests.get(url+'/versions/latest',timeout=20);result.raise_for_status()
                active=url.rsplit('/',1)[1];latest=str(result.json()['id'])
                state='new_version_needs_calibration' if latest!=active else 'current_record_is_latest'
            watch.append({'source':kind,'url':url,'observed':latest,
                'active_reference':active,'state':state,
                'activation':'requires compatible probes, licensing, heldout and cross-date/provider calibration'})
        except Exception as e:watch.append({'source':kind,'url':url,'state':'unavailable','error_type':type(e).__name__})
    manifest={'schema':'api-mri.data-candidates.v1','created_at':date.today().isoformat(),
        'state':'candidate_only','files':files,'upstream_watch':watch,
        'fingerprint_activated':False,'code_updated':False,'model_api_calls':0}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'candidate_files':len(files),'watch_sources':len(watch),'model_api_calls':0}))
if __name__=='__main__':main()
