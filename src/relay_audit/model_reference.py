"""Shared display identity for public examples and capability references.

Canonical identity is metadata joining, never authentication of a live endpoint.
No fuzzy nearest-model matching: unknown names retain their own identity.
"""
from __future__ import annotations
from functools import lru_cache
import json
from pathlib import Path
import re

PATH = Path(__file__).parent/'data/model_reference.json'
VENDORS = {'openai', 'anthropic', 'google', 'deepseek', 'qwen', 'z-ai', 'moonshotai',
           'x-ai', 'xiaomi', 'minimax', 'meta', 'meta-llama', 'mistralai', 'stepfun', 'inclusionai'}

def name_key(value: str) -> str:
    value = str(value).strip().lower()
    if '/' in value and value.split('/', 1)[0] in VENDORS:
        value = value.split('/', 1)[1]
    return re.sub(r'[^a-z0-9]', '', value)

@lru_cache(maxsize=1)
def reference() -> dict:
    return json.loads(PATH.read_text(encoding='utf-8'))

@lru_cache(maxsize=1)
def _index() -> dict:
    candidates = {}
    for model in reference()['models']:
        for alias in (model['id'], model['name']):
            candidates.setdefault(name_key(alias), {})[model['id']] = model
    # An ambiguous key never silently picks the first provider or version.
    return {key: next(iter(items.values())) for key, items in candidates.items() if len(items) == 1}

def model_identity(label: str) -> dict:
    original = str(label)
    plain, _, variant = original.partition(' · ')
    model = _index().get(name_key(plain))
    # Explicit source aliases only. Sampling / quantization suffixes stay visible.
    aliases = {'gemini3pro': 'google/gemini-3-pro-preview', 'gemini30propreview': 'google/gemini-3-pro-preview',
        'fable5': 'anthropic/claude-fable-5', 'opus48': 'anthropic/claude-opus-4.8',
        'sonnet5': 'anthropic/claude-sonnet-5',
        'claudehaiku45': 'anthropic/claude-haiku-4-5-20251001',
        'claudeopus45': 'anthropic/claude-opus-4-5-20251101',
        'claudesonnet45': 'anthropic/claude-sonnet-4-5-20250929'}
    if model is None and name_key(plain) in aliases:
        target = aliases[name_key(plain)]
        model = _index().get(name_key(target)) or next((m for m in reference()['models'] if m['id'] == target), None)
    if model is None:
        stripped = re.sub(r'\s+(?:high|max|medium|low)$', '', plain, flags=re.I)
        model = _index().get(name_key(stripped))
        if model:
            variant = ' · '.join(filter(None, (plain[len(stripped):].strip(), variant)))
    if model:
        return {'id': model['id'], 'name': model['name'], 'variant': variant,
            'release_date': model.get('release_date'), 'date_source': reference()['source'],
            'match': 'catalog_alias', 'original_label': original}
    # Formatting vendor prefixes is safe even when metadata is unavailable.
    display = plain.split('/', 1)[1] if '/' in plain and plain.split('/', 1)[0].lower() in VENDORS else plain
    return {'id': 'unresolved:'+name_key(plain), 'name': display, 'variant': variant,
            'release_date': None, 'date_source': None, 'match': 'unresolved', 'original_label': original}

BENCHMARKS = [
    {'id': 'bfcl', 'name': 'BFCL V4', 'category': '工具与代理', 'unit': '分场景准确率',
     'url': 'https://gorilla.cs.berkeley.edu/leaderboard.html',
     'method': '区分原生工具调用与提示模拟，检查调用结构、参数、多轮与代理任务。',
     'compare': '同一 BFCL 版本、FC/Prompt 模式及推理配置'},
    {'id': 'swe', 'name': 'SWE-bench', 'category': '编程与代理', 'unit': '% Resolved',
     'url': 'https://www.swebench.com/',
     'method': '修复真实仓库问题，以测试通过情况计算解决率；保留 Agent、模型和预算。',
     'compare': '同一题集、Agent/Harness、预算及尝试次数'},
    {'id': 'terminal', 'name': 'Terminal-Bench', 'category': '编程与代理', 'unit': '任务通过率',
     'url': 'https://www.tbench.ai/leaderboard',
     'method': '在终端环境完成可执行任务；模型、Agent 和环境共同组成参赛系统。',
     'compare': '同一题集版本、Agent、时间与 token 预算'},
    {'id': 'livebench', 'name': 'LiveBench', 'category': '推理与知识', 'unit': '分领域分数',
     'url': 'https://livebench.ai/',
     'method': '以可验证答案衡量多个领域，按题集批次阅读结果。',
     'compare': '同一发布批次、推理档位和评分版本'},
    {'id': 'aa', 'name': 'Artificial Analysis', 'category': '综合与成本', 'unit': '版本化指数、速度与成本',
     'url': 'https://artificialanalysis.ai/leaderboards/models',
     'method': '综合指数按版本选择任务与权重；另列输出速度、价格和推理配置。',
     'compare': '同一指数版本和推理配置；成本保留工作负载'},
    {'id': 'arena', 'name': 'Arena', 'category': '人类偏好', 'unit': '分赛道偏好与区间',
     'url': 'https://arena.ai/leaderboard',
     'method': '通过用户对战或任务选择聚合偏好，按文本、代码、视觉等赛道查看。',
     'compare': '同一赛道、时间窗口、样本数与置信区间'},
]

def capability_reference() -> dict:
    value = reference()
    # Source snapshots are merged by explicit display identity; neither scores
    # nor versions are averaged, and the original labels remain in each row.
    models = [dict(m, benchmarks=list(m.get('benchmarks', []))) for m in value['models']]
    by_id = {m['id']: m for m in models}
    snapshot_path = PATH.with_name('benchmark_snapshots.json')
    snapshots = json.loads(snapshot_path.read_text('utf-8')) if snapshot_path.is_file() else {'scores': []}
    for row in snapshots['scores']:
        info = model_identity(row['model'])
        model = by_id.get(info['id'])
        if model is None:
            model = {'id': info['id'], 'name': info['name'], 'release_date': None, 'benchmarks': []}
            models.append(model); by_id[model['id']] = model
        model['benchmarks'].append(row)
    return {'retrieved_at': value['retrieved_at'], 'source': value['source'],
        'snapshots': snapshots.get('sources', []),
        'benchmarks': BENCHMARKS, 'models': models}
