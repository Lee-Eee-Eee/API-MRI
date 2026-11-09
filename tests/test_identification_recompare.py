"""A newer reference can reuse paid observations without another API request."""
from copy import deepcopy
import json

import pytest

from relay_audit import identification as ident
from relay_audit import identification_library as library
from relay_audit import identification_recompare as recompare
from relay_audit.records import content_hash


def row(identifier, answers):
    return {'model': identifier, 'label': identifier, 'family': 'fixture', 'aliases': [identifier + '-latest'],
        'counts': deepcopy(answers), 'collected_at': {'first': '2026-11-09', 'last': '2026-11-09'},
        'provenance': {'kind': 'operator_declared', 'label': 'Synthetic offline fixture'}}


@pytest.fixture
def observations(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'PUBLIC_INDEX_PATH', tmp_path / 'no-bundled-index.json')
    original = library.legacy_bank()
    counts = {cell['id']: deepcopy(original['models'][0]['counts'][cell['id']]) for cell in library.core_cells()}
    source_bank = library.build_rolling_bank([row('service/fixture/old', counts)], bank_id='source-fixture')
    library.publish_bank(source_bank, tmp_path / 'library')
    source_cells = {cell['user']: cell for cell in source_bank['cells']}
    calls = []
    def call(client, config, key, body, secrets, cancelled=None):
        cell = source_cells[body['messages'][-1]['content']]
        value = next(iter(counts[cell['id']]))
        if cell['normalize_as'] == 'binary':
            value = next(key for key, normalized in ident.COIN[cell['lang']].items() if normalized == value)
        calls.append(body)
        return {'http_status': 200, 'error': None, 'elapsed_seconds': .01,
            'raw_response': {'choices': [{'message': {'content': value}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 1, 'total_tokens': 21}}}
    monkeypatch.setattr(ident.fp, '_call', call)
    source_report = ident.run_identification({'bank_id': source_bank['id'],
        'candidate': {'api_base': 'https://example.test/v1', 'model': 'service-alias', 'api_key': 'fixture-private-key'},
        'budget': {'max_requests': 12, 'max_output_tokens': 192}}, tmp_path / 'source', library_dir=tmp_path / 'library')
    source_path = tmp_path / 'source/identification_records.jsonl'
    source_text = source_path.read_text('utf8')
    fresh_counts = deepcopy(counts)
    for cell_id, values in source_report['coverage']['cells'].items():
        fresh_counts[cell_id] = {answer: count * 2 for answer, count in values.items()}
    target_bank = library.build_rolling_bank([row('service/fixture/fresh', fresh_counts)],
        bank_id='target-fixture', base_bank=source_bank)
    library.publish_bank(target_bank, tmp_path / 'library')
    assert len(calls) == 12
    monkeypatch.setattr(ident.fp, '_call', lambda *args, **kwargs: pytest.fail('Offline re-ranking made a model call.'))
    return tmp_path, source_path, source_text, source_report, source_bank, target_bank


def test_new_model_recomparison_uses_zero_calls_and_preserves_original_cost_and_bytes(observations):
    root, source_path, source_text, old_report, source_bank, target_bank = observations
    original_bytes = source_path.read_bytes()
    report = recompare.recompare_identification(source_text, target_bank['id'], root / 'recompare', library_dir=root / 'library')
    assert report['status'] == 'reference_shortlist'
    assert report['identity_decision'] == 'abstained'
    assert report['calibration']['applicable_gate'] is None
    assert report['coverage']['candidate_count'] == 134
    assert report['coverage']['compared_candidates'] == 134
    assert any(item['model'] == 'service/fixture/fresh' for item in report['ranking'])
    assert report['reanalysis']['source_bank_id'] == source_bank['id']
    assert report['reanalysis']['target_bank_id'] == target_bank['id']
    assert report['reanalysis']['source_requests'] == report['reanalysis']['reused_requests'] == 12
    assert report['reanalysis']['model_api_calls'] == 0
    assert report['cost']['request_count'] == report['cost']['real_requests'] == 0
    assert report['cost']['requested_output_tokens'] == 0
    assert report['reused_cost'] == old_report['cost']
    assert report['reused_cost']['real_requests'] == 12
    assert source_path.read_bytes() == original_bytes
    assert 'fixture-private-key' not in (root / 'recompare/identification_records.jsonl').read_text('utf8')


def test_self_contained_envelope_replays_on_an_empty_library(observations):
    root, _, text, _, _, target = observations
    report = recompare.recompare_identification(text, target['id'], root / 'first', library_dir=root / 'library')
    path = root / 'first/identification_records.jsonl'
    assert len(path.read_text('utf8').splitlines()) == 1
    envelope = recompare.parse_recomparison(path.read_text('utf8'))
    assert envelope['schema'] == recompare.SCHEMA
    assert isinstance(envelope['source_records'], list)
    assert envelope['target_bank_snapshot'] == target
    assert recompare.replay_recomparison(envelope, root / 'empty-machine-replay') == report
    # The envelope is an analysis, not another original capture.
    with pytest.raises(ValueError):
        ident.parse_identification_records(path.read_text('utf8'))


def test_recomparison_to_historical_bank_never_reuses_its_calibration(observations):
    root, _, text, _, _, _ = observations
    report = recompare.recompare_identification(text, 'legacy', root / 'legacy', library_dir=root / 'library')
    assert report['status'] == 'reference_shortlist'
    assert report['calibration']['applicable_gate'] is None
    assert report['calibration']['open_set_gate_passed'] is None
    assert report['stopping']['fixed_horizon_metrics_apply_to_policy'] is False
    assert report['cost']['real_requests'] == 0


def test_historical_observations_outside_target_core_are_excluded(observations):
    root, _, _, _, _, target = observations
    ident.demo_identification(root / 'legacy-source', budget={'max_requests': 32, 'max_output_tokens': 512})
    path = root / 'legacy-source/identification_records.jsonl'
    source = path.read_text('utf8')
    records = ident.parse_identification_records(source)
    target_core = set(library.CORE_CELL_IDS)
    expected = sum(row['type'] == 'request' and row['cell_id'] in target_core for row in records)
    report = recompare.recompare_identification(source, target['id'], root / 'from-legacy', library_dir=root / 'library')
    assert report['reanalysis']['source_requests'] == 32
    assert report['reanalysis']['reused_requests'] == expected
    assert expected < 32
    assert all(probe['cell_id'] in target_core for probe in report['evidence']['per_probe'])


def test_only_complete_valid_cells_enter_recomparison(observations):
    root, _, text, _, _, target = observations
    records = [json.loads(line) for line in text.splitlines()]
    # Retain a valid fixed-attempts capture with one semantic-invalid response.
    records[1]['raw_response']['choices'][0]['message']['content'] = 'I cannot answer.'
    previous = None
    for record in records:
        record['previous_hash'] = previous
        record.pop('record_hash')
        record['record_hash'] = content_hash(record)
        previous = record['record_hash']
    source = '\n'.join(json.dumps(row, ensure_ascii=False) for row in records)
    ident.parse_identification_records(source)
    report = recompare.recompare_identification(source, target['id'], root / 'partial', library_dir=root / 'library')
    assert report['status'] == 'insufficient_evidence'
    assert report['coverage']['completed_cells'] == 2
    assert report['reanalysis']['reused_requests'] == 8
    assert report['reanalysis']['skipped_requests'] == 4
    assert report['cost']['request_count'] == 0


@pytest.mark.parametrize('target', ['source', 'target', 'envelope'])
def test_mutated_source_or_target_or_envelope_is_rejected(observations, target):
    root, _, text, _, _, bank = observations
    recompare.recompare_identification(text, bank['id'], root / 'first', library_dir=root / 'library')
    envelope = json.loads((root / 'first/identification_records.jsonl').read_text('utf8'))
    if target == 'source':
        envelope['source_records'][1]['request']['temperature'] = 0
        envelope['source_records_sha256'] = recompare._source_digest(envelope['source_records'])
    elif target == 'target':
        envelope['target_bank_snapshot']['cells'][0]['system'] = 'Changed protocol'
        envelope['target_bank_sha256'] = library.bank_digest(envelope['target_bank_snapshot'])
    else:
        envelope['record_hash'] = '0' * 64
    if target != 'envelope':
        envelope['record_hash'] = content_hash({key: value for key, value in envelope.items() if key != 'record_hash'})
    with pytest.raises(ValueError):
        recompare.parse_recomparison(json.dumps(envelope, ensure_ascii=False))


def test_oversized_envelope_is_rejected_before_decoding():
    with pytest.raises(ValueError, match='4 MiB'):
        recompare.parse_recomparison(' ' * (recompare.MAX_BYTES + 1))
