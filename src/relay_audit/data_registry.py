"""Small, local status ledger; reading this never checks the network."""
from datetime import date
import hashlib
import json
from pathlib import Path

DATA=Path(__file__).parent/'data'

def data_status(library_dir=None) -> dict:
    bank=DATA/'identification/one-token-202607.json'
    model=DATA/'model_reference.json'
    benchmark=DATA/'benchmark_snapshots.json'
    examples=DATA/'showcase/catalog.json'
    rows=[]
    for identifier,title,path,policy in [
        ('fingerprint','识别参考分布',bank,'新数据先检验协议与校准，验证后启用'),
        ('models','模型目录',model,'每周检查，按快照更新'),
        ('examples','效果例题与作品',examples,'精选题目，保留原始运行条件'),
        ('benchmarks','公开榜单',benchmark,'每周留快照，分别保留评测版本'),
    ]:
        raw=path.read_bytes();value=json.loads(raw)
        if identifier=='fingerprint':
            stamp=value.get('collected_at',{}).get('last','')[:10]
            label=value['id'];note='采集日期；跨期与跨渠道验证待补'
        else:
            stamp=value.get('retrieved_at') or max((e.get('source',{}).get('retrieved_at','') for e in value.get('examples',[])),default='')
            label=hashlib.sha256(raw).hexdigest()[:12];note='快照日期'
        rows.append({'id':identifier,'name':title,'date':stamp or None,'version':label,
            'sha256':hashlib.sha256(raw).hexdigest(),'policy':policy,'note':note})
    from .identification_library import active_bank_id, bank_digest, load_bank
    active=active_bank_id(library_dir)
    if active:
        current=load_bank(active,library_dir)
        for row in rows:
            if row['id']=='fingerprint':
                row.update(date=current.get('new_observations_collected_at',current['collected_at'])['last'][:10],
                    version=current['id'],sha256=bank_digest(current),
                    note='新增参考的采集日期；历史型号保留各自原采集日期')
    return {'checked_at':date.today().isoformat(),'network_requests':0,'entries':rows}
