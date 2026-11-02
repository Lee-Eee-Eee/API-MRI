"""Refresh public model metadata and source-attributed benchmark rows; no model calls."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://models.dev/models.json'

def build(raw: bytes) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict) or not 100 <= len(data) <= 10000:
        raise ValueError('Unexpected models.dev catalog')
    models = []
    for identifier, item in data.items():
        if not isinstance(item, dict) or not isinstance(item.get('name'), str):
            continue
        models.append({key: item[key] for key in ('id', 'name', 'family', 'release_date', 'last_updated',
            'open_weights', 'tool_call', 'benchmarks') if key in item} | {'id': identifier})
    return {'schema': 'api-mri.model-reference.v1', 'retrieved_at': date.today().isoformat(),
        'source': URL, 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'provenance': 'models.dev community catalog; benchmark rows retain their original publisher URLs',
        'models': models}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    if args.input:
        raw = args.input.read_bytes()
    else:
        response = requests.get(URL, timeout=40); response.raise_for_status(); raw = response.content
    value = build(raw)
    dest = ROOT/'src/relay_audit/data/model_reference.json'
    dest.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f"Saved {len(value['models'])} models and {sum(len(m.get('benchmarks', [])) for m in value['models'])} benchmark rows")

if __name__ == '__main__':
    main()
