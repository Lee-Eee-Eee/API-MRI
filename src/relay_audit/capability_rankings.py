"""Source-scoped benchmark rankings, built entirely from bundled data.

An entry is a model in a published configuration.  Groups retain the exact
benchmark, metric, version, dataset, harness and source.  Explicit tool access
is a separate condition; reasoning effort stays visible in the configurations.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import re


GROUP_FIELDS = ('name', 'version', 'metric', 'dataset', 'harness', 'source')
_HIGHER = {
    'accuracy', 'success rate', 'resolve rate', 'resolved', 'percent correct',
    'percent resolved', 'pass rate', 'win rate', 'wins or ties', 'f1', 'em',
    'elo', 'rating', 'binary completion rate', 'all-pass rate', 'tasks solved',
    'mean sequence-match ratio', 'dominance score', 'strict-pass',
    'share of rollouts passing at least 95% of tests',
}
_LOWER = {
    'overall edit distance', 'edit distance', 'word error rate',
    'character error rate', 'error rate', 'latency', 'latency (s)',
    'latency (ms)', 'cost', 'cost (usd)',
}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _condition(variant) -> str:
    value = _text(variant).casefold()
    without = bool(re.search(r'\b(?:without|no)\s+tools?\b', value))
    with_tools = bool(re.search(r'\bwith\s+tools?\b', value))
    if without and with_tools:
        return 'mixed tool conditions'
    return 'without tools' if without else 'with tools' if with_tools else ''


def _direction(name: str, metric: str) -> str | None:
    value = metric.casefold()
    if value in _LOWER:
        return 'lower'
    if value in _HIGHER or re.fullmatch(r'(?:average |strict )?pass@\d+', value):
        return 'higher'
    if name.casefold().startswith('artificial analysis ') and value in {
        'index', 'index points', 'index score', 'average pass@1',
    }:
        return 'higher'
    # "score", "percent" and "average position score" do not specify the
    # direction by themselves.  New metrics require an explicit decision.
    return None


def _raw_key(record: dict) -> tuple:
    row = record['raw']
    return (_text(row.get('date')), json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


def _best(records: list[dict], direction: str | None) -> dict:
    if direction is None:
        # A representative original record, without calling it a best score.
        return max(records, key=_raw_key)
    score = (max if direction == 'higher' else min)(r['raw']['score'] for r in records)
    return max((r for r in records if r['raw']['score'] == score), key=_raw_key)


def build_rankings(catalog: dict) -> dict:
    groups = defaultdict(list)
    for model in catalog.get('models', []):
        model_id = _text(model.get('id'))
        if not model_id:
            continue
        for raw in model.get('benchmarks', []):
            score = raw.get('score')
            if type(score) not in (int, float) or not math.isfinite(score):
                continue
            key = tuple(_text(raw.get(k)) for k in GROUP_FIELDS) + (_condition(raw.get('variant')),)
            if not key[0]:
                continue
            groups[key].append({'model_id': model_id, 'model': _text(model.get('name')) or model_id,
                'release_date': model.get('release_date'), 'raw': deepcopy(raw)})

    boards = []
    for key, records in groups.items():
        per_model = defaultdict(list)
        for record in records:
            per_model[record['model_id']].append(record)
        if len(per_model) < 3:
            continue
        info = dict(zip(GROUP_FIELDS, key[:6]))
        direction = _direction(info['name'], info['metric'])
        rows = []
        for model_records in per_model.values():
            chosen = _best(model_records, direction)
            raw = chosen['raw']
            variants = sorted(model_records, key=_raw_key, reverse=True)
            if direction:
                variants.sort(key=lambda r: r['raw']['score'], reverse=direction == 'higher')
            rows.append({'rank': None, 'model_id': chosen['model_id'], 'model': chosen['model'],
                'release_date': chosen['release_date'], 'score': raw['score'],
                'variant': raw.get('variant'), 'source': raw.get('source'), 'date': raw.get('date'),
                'variants': [r['raw'] for r in variants]})
        rows.sort(key=lambda r: (r['model'].casefold(), r['model_id']))
        if direction:
            rows.sort(key=lambda r: r['score'], reverse=direction == 'higher')
            previous = object()
            rank = None
            for position, row in enumerate(rows, 1):
                if row['score'] != previous:
                    rank = position
                row['rank'] = rank
                previous = row['score']
        stable_key = json.dumps(key, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        source_kind = 'snapshot' if any(
            r['raw'].get('provenance') == 'original leaderboard table'
            or _text(r['raw'].get('version')).startswith('snapshot ') for r in records) else 'reports'
        dates = [_text(r['raw'].get('date')) for r in records]
        boards.append({'id': 'board-' + hashlib.sha256(stable_key).hexdigest()[:16],
            **info, 'condition': key[6] or None, 'source_kind': source_kind,
            'date': max(dates) or None, 'direction': direction,
            'model_count': len(per_model), 'record_count': len(records), 'rows': rows})

    aa = [b for b in boards if b['source_kind'] == 'snapshot'
          and b['name'].casefold() == 'artificial analysis intelligence index'
          and b['source'] == 'https://artificialanalysis.ai/leaderboards/models']
    preferred = max(aa, key=lambda b: (b['date'] or '', b['model_count'], b['id'])) if aa else None
    boards.sort(key=lambda b: (b['id'] != (preferred or {}).get('id'), b['source_kind'] != 'snapshot',
        b['direction'] is None, -b['model_count'], b['name'].casefold(), b['version'], b['id']))
    return {'retrieved_at': catalog.get('retrieved_at'),
        'default_board': boards[0]['id'] if boards else None, 'boards': boards,
        'benchmarks': deepcopy(catalog.get('benchmarks', []))}


def capability_rankings() -> dict:
    from .model_reference import capability_reference
    return build_rankings(capability_reference())
