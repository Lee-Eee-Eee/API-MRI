"""Versioned references preserve protocol, costs, qualification and old evidence."""
from copy import deepcopy
import json

import pytest

from relay_audit import identification as ident
from relay_audit import identification_library as lib


@pytest.fixture(autouse=True)
def isolated_public_index(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, 'PUBLIC_INDEX_PATH', tmp_path / 'bundled/public-index.json')


def observation(model='service/provider/new-model', date='2026-11-09'):
    baseline = lib.legacy_bank()
    return {'model': model, 'label': 'New model', 'family': 'new-model', 'providers': ['Declared route'],
        'aliases': ['new-model-latest'],
        'counts': {cell['id']: deepcopy(baseline['models'][0]['counts'][cell['id']]) for cell in lib.core_cells()},
        'collected_at': {'first': date, 'last': date},
        'provenance': {'kind': 'operator_declared', 'label': 'Synthetic reference fixture', 'records_sha256': 'a' * 64}}


def rolling(identifier='rolling-fixture', models=None, base=None):
    return lib.build_rolling_bank(models or [observation()], bank_id=identifier, built_on='2026-11-09', base_bank=base)


def payload(bank_id):
    return {'candidate': {'api_base': 'https://example.test/v1', 'model': 'endpoint-alias', 'api_key': 'fixture-only-secret'},
        'bank_id': bank_id, 'budget': {'max_requests': 12, 'max_output_tokens': 192}}


def test_fixed_core_keeps_every_historical_label_and_no_historical_gates():
    bank = rolling()
    assert len(bank['models']) == len(lib.legacy_bank()['models']) + 1
    assert [cell['id'] for cell in bank['cells']] == list(lib.CORE_CELL_IDS)
    assert bank['calibration'] == {}
    assert bank['validation']['identity_authenticated'] is False
    assert bank['validation']['open_set_calibrated'] is False
    assert len(ident._common_cells(bank, bank['models'])) == 8
    assert all(set(row['counts']) == set(lib.CORE_CELL_IDS) for row in bank['models'])


def test_publication_is_immutable_and_lookup_supports_pinned_hash(tmp_path):
    first = rolling()
    metadata = lib.publish_bank(first, tmp_path)
    assert lib.load_bank(metadata['id'], tmp_path) == first
    assert lib.load_bank(metadata['sha256'], tmp_path) == first
    assert lib.publish_bank(first, tmp_path) == metadata
    assert len(lib.list_banks(tmp_path)) == 2
    changed = rolling(models=[observation(date='2026-11-10')])
    with pytest.raises(ValueError, match='immutable'):
        lib.publish_bank(changed, tmp_path)
    assert lib.load_bank(metadata['id'], tmp_path) == first


def test_later_bank_retains_other_new_models_and_refreshes_only_requested_route(tmp_path):
    first = rolling(models=[observation('service/p/one'), observation('service/p/two')])
    lib.publish_bank(first, tmp_path)
    refresh = observation('service/p/one', '2026-11-10')
    latest = rolling('rolling-next', [refresh], first)
    assert len(latest['models']) == len(first['models'])
    by_id = {row['model']: row for row in latest['models']}
    assert by_id['service/p/one']['collected_at']['last'] == '2026-11-10'
    assert by_id['service/p/two']['collected_at']['last'] == '2026-11-09'
    lib.publish_bank(latest, tmp_path)
    assert lib.load_bank(first['id'], tmp_path) == first


@pytest.mark.parametrize('field', ['calibration', 'cells', 'counts', 'validation', 'metadata'])
def test_import_cannot_inject_protocol_gates_rewrite_history_or_claim_identity(field):
    bank = rolling()
    if field == 'calibration':
        bank['calibration'] = {'open_set_gates': {'3': {'max_distance': 1, 'min_gap': 0}}}
    elif field == 'cells':
        bank['cells'][0]['system'] = 'Use a different secret instruction.'
    elif field == 'counts':
        bank['models'][0]['counts'][lib.CORE_CELL_IDS[0]] = {'forged': 8}
    elif field == 'validation':
        bank['validation']['identity_authenticated'] = True
    else:
        bank['api_key'] = 'a key may not be embedded as metadata'
    with pytest.raises(ValueError):
        lib.validate_bank_snapshot(bank)


def test_incomplete_counts_and_credential_urls_are_rejected():
    row = observation()
    for identifier in lib.CORE_CELL_IDS[:3]:
        row['counts'][identifier] = {'one': 4}
    with pytest.raises(ValueError, match='five'):
        rolling(models=[row])
    row = observation()
    row['provenance']['url'] = 'https://user:secret@example.test/api'
    with pytest.raises(ValueError, match='credentials'):
        rolling(models=[row])


@pytest.mark.parametrize('usable', [3, 5])
def test_fewer_than_six_usable_reference_cells_cannot_publish(usable):
    row = observation()
    for identifier in lib.CORE_CELL_IDS[usable:]:
        row['counts'][identifier] = {}
    with pytest.raises(ValueError, match='six'):
        rolling(models=[row])


def test_six_usable_cells_preserve_all_candidates_and_fixed_core_metadata(tmp_path):
    row = observation()
    row['counts'][lib.CORE_CELL_IDS[0]] = {}
    row['counts'][lib.CORE_CELL_IDS[1]] = {'insufficient': 4}
    row['holdout_counts'] = {identifier: {} for identifier in lib.CORE_CELL_IDS}
    bank = rolling(models=[row])
    metadata = lib.publish_bank(bank, tmp_path)
    assert metadata['model_count'] == 133
    assert metadata['core_cell_count'] == 8 and metadata['comparable_cell_count'] == 6
    assert len(ident._common_cells(bank, bank['models'])) == 6
    coverage = lib.coverage(bank, row['model'])
    assert coverage['reference_cells'] == 6 and coverage['core_cell_count'] == 8
    assert coverage['min_repetitions'] >= 5
    plan = ident.plan_identification(payload(bank['id']), library_dir=tmp_path)
    assert plan['candidate_count'] == 133 and plan['shared_cell_count'] == 6


def test_individual_six_cell_references_with_only_five_joint_cells_are_rejected():
    first, second = observation('service/p/first'), observation('service/p/second')
    for identifier in lib.CORE_CELL_IDS[:2]:
        first['counts'][identifier] = {}
    for identifier in lib.CORE_CELL_IDS[1:3]:
        second['counts'][identifier] = {}
    with pytest.raises(ValueError, match='jointly'):
        rolling(models=[first, second])


def test_catalog_replacement_failure_keeps_previous_publication_readable(tmp_path, monkeypatch):
    first = rolling()
    lib.publish_bank(first, tmp_path)
    old_catalog = (tmp_path / 'identification/catalog.json').read_bytes()
    next_bank = rolling('rolling-next', [observation('service/p/next')], first)
    monkeypatch.setattr(lib, '_atomic_json', lambda *args: (_ for _ in ()).throw(OSError('fixture interruption')))
    with pytest.raises(OSError):
        lib.publish_bank(next_bank, tmp_path)
    assert (tmp_path / 'identification/catalog.json').read_bytes() == old_catalog
    assert lib.load_bank(first['id'], tmp_path) == first
    assert len(lib.list_banks(tmp_path)) == 2


def test_coverage_distinguishes_exact_declared_route_alias_and_missing():
    bank = rolling()
    exact = lib.coverage(bank, 'service/provider/new-model')
    assert exact['match'] == 'exact'
    assert exact['provenance']['kind'] == 'operator_declared'
    assert exact['collected_at']['last'] == '2026-11-09'
    assert lib.coverage(bank, 'new-model-latest')['match'] == 'unconfirmed_alias'
    assert lib.coverage(bank, 'not-collected')['match'] == 'missing'


def test_activation_is_explicit_pinned_and_can_return_to_historical_bank(tmp_path):
    bank = rolling()
    lib.publish_bank(bank, tmp_path)
    assert lib.active_bank_id(tmp_path) is None
    active = lib.activate_bank(bank['id'], tmp_path)
    assert active['id'] == bank['id']
    assert lib.active_bank_id(tmp_path) == bank['id']
    lib.activate_bank('legacy', tmp_path)
    assert lib.active_bank_id(tmp_path) == lib.legacy_bank()['id']
    pointer = tmp_path / 'identification/active.json'
    data = json.loads(pointer.read_bytes())
    data['sha256'] = '0' * 64
    pointer.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(ValueError, match='digest'):
        lib.active_bank_id(tmp_path)


def test_modified_snapshot_cannot_be_loaded_from_an_intact_catalog(tmp_path):
    metadata = lib.publish_bank(rolling(), tmp_path)
    path = tmp_path / 'identification/banks' / (metadata['sha256'] + '.json')
    value = json.loads(path.read_bytes())
    value['models'][-1]['label'] = 'tampered'
    path.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(ValueError, match='digest'):
        lib.load_bank(metadata['id'], tmp_path)


def test_cached_reads_are_detached_and_stat_changes_invalidate_validation(tmp_path, monkeypatch):
    bank = rolling()
    metadata = lib.publish_bank(bank, tmp_path)
    validator = lib.validate_bank_snapshot
    checked = []
    def counting(value, **kwargs):
        checked.append(1)
        return validator(value, **kwargs)
    monkeypatch.setattr(lib, 'validate_bank_snapshot', counting)
    loaded = lib.load_bank(bank['id'], tmp_path)
    loaded['models'][-1]['counts'][lib.CORE_CELL_IDS[0]] = {'mutation': 999}
    assert lib.load_bank(bank['id'], tmp_path) == bank
    assert len(checked) == 1
    path = tmp_path / 'identification/banks' / (metadata['sha256'] + '.json')
    modified = deepcopy(bank)
    modified['models'][-1]['label'] = 'changed after caching'
    path.write_bytes(lib.canonical_bytes(modified))
    with pytest.raises(ValueError, match='digest'):
        lib.load_bank(bank['id'], tmp_path)


def test_legacy_cache_tracks_file_identity_and_never_exposes_mutable_shared_state(tmp_path, monkeypatch):
    value = lib.legacy_bank()
    path = tmp_path / 'historical.json'
    path.write_bytes(lib.canonical_bytes(value))
    monkeypatch.setattr(lib, 'LEGACY_PATH', path)
    monkeypatch.setattr(ident, 'BANK_PATH', path)
    first = lib.legacy_bank()
    first['models'][0]['label'] = 'only a caller mutation'
    assert lib.legacy_bank()['models'][0]['label'] != first['models'][0]['label']
    assert ident.load_identification_bank()['models'][0]['label'] != first['models'][0]['label']
    value['models'][0]['label'] = 'file replacement is now visible'
    path.write_bytes(lib.canonical_bytes(value))
    assert lib.legacy_bank()['models'][0]['label'] == value['models'][0]['label']
    assert ident.load_identification_bank()['models'][0]['label'] == value['models'][0]['label']


def bundle(bank, directory):
    directory.mkdir(parents=True, exist_ok=True)
    filename = bank['id'] + '.json'
    (directory / filename).write_bytes(lib.canonical_bytes(bank))
    return {'id': bank['id'], 'sha256': lib.bank_digest(bank), 'filename': filename}


def test_bundled_rolling_reference_is_default_without_changing_legacy_zero_arg_loader(tmp_path):
    bank = rolling()
    directory = lib.PUBLIC_INDEX_PATH.parent
    row = bundle(bank, directory)
    lib.PUBLIC_INDEX_PATH.write_text(json.dumps({'schema': 'api-mri-public-reference-index/1',
        'updated_at': '2026-11-09', 'banks': [row]}), encoding='utf8')
    assert lib.load_bank(bank['id']) == bank
    assert lib.load_bank(row['sha256']) == bank
    assert len(lib.list_banks()) == 2
    assert lib.active_bank_id(None) == bank['id']
    assert lib.active_bank_id(tmp_path / 'user') == bank['id']
    assert ident.load_identification_bank()['id'] == lib.legacy_bank()['id']
    lib.activate_bank('legacy', tmp_path / 'user')
    assert lib.active_bank_id(tmp_path / 'user') == lib.legacy_bank()['id']


def test_latest_bundled_date_is_selected_and_local_same_id_cannot_shadow(tmp_path):
    old = rolling('rolling-old')
    latest = rolling('rolling-new', [observation(date='2026-11-10')], old)
    directory = lib.PUBLIC_INDEX_PATH.parent
    rows = [bundle(old, directory), bundle(latest, directory)]
    lib.PUBLIC_INDEX_PATH.write_text(json.dumps({'schema': 'api-mri-public-reference-index/1',
        'updated_at': '2026-11-10', 'banks': rows}), encoding='utf8')
    assert lib.active_bank_id(None) == latest['id']
    assert len(lib.list_banks(tmp_path / 'local')) == 3
    assert lib.publish_bank(old, tmp_path / 'local')['sha256'] == rows[0]['sha256']
    bad = rolling('rolling-old', [observation(date='2026-11-11')])
    with pytest.raises(ValueError, match='immutable'):
        lib.publish_bank(bad, tmp_path / 'local')


def test_public_index_cannot_traverse_or_mislabel_its_snapshot(tmp_path):
    directory = lib.PUBLIC_INDEX_PATH.parent
    row = bundle(rolling(), directory)
    row['filename'] = '../elsewhere.json'
    lib.PUBLIC_INDEX_PATH.write_text(json.dumps({'schema': 'api-mri-public-reference-index/1',
        'updated_at': '2026-11-09', 'banks': [row]}), encoding='utf8')
    with pytest.raises(ValueError, match='entry'):
        lib.list_banks()


def test_rolling_plan_and_self_contained_run_replay_without_installed_library(tmp_path, monkeypatch):
    bank = rolling()
    lib.publish_bank(bank, tmp_path / 'library')
    plan = ident.plan_identification(payload(bank['id']), library_dir=tmp_path / 'library')
    assert plan['shared_cell_count'] == 8
    assert plan['candidate_count'] == 133
    assert plan['validation']['fixed_budget_evaluation'] == 'not_calibrated'
    assert 'fixture-only-secret' not in json.dumps(plan)

    def call(client, config, key, body, secrets, cancelled=None):
        cell = next(cell for cell in bank['cells'] if cell['user'] == body['messages'][1]['content'])
        value = next(iter(bank['models'][0]['counts'][cell['id']]))
        if cell['normalize_as'] == 'binary':
            value = next(k for k, v in ident.COIN[cell['lang']].items() if v == value)
        return {'http_status': 200, 'error': None, 'elapsed_seconds': .01,
            'raw_response': {'choices': [{'message': {'content': value}, 'finish_reason': 'stop'}],
                             'usage': {'prompt_tokens': 20, 'completion_tokens': 1, 'total_tokens': 21}}}

    monkeypatch.setattr(ident.fp, '_call', call)
    report = ident.run_identification(payload(bank['id']), tmp_path / 'live', library_dir=tmp_path / 'library')
    assert report['status'] == 'reference_shortlist'
    assert report['identity_decision'] == 'abstained'
    assert report['calibration']['applicable_gate'] is None
    assert report['stopping']['fixed_horizon_metrics_apply_to_policy'] is False
    assert report['coverage']['valid_answers'] == 12
    text = (tmp_path / 'live/identification_records.jsonl').read_text('utf8')
    manifest = json.loads(text.splitlines()[0])
    assert manifest['bank_snapshot'] == bank
    assert manifest['bank_sha256'] == lib.bank_digest(bank)
    assert 'fixture-only-secret' not in text
    # An empty second library simulates another computer; the record carries its reference.
    parsed = ident.parse_identification_records(text, library_dir=tmp_path / 'empty-machine')
    replay = ident.replay_identification(parsed, tmp_path / 'replay', library_dir=tmp_path / 'empty-machine')
    assert replay == report


def test_fixed_attempt_blocks_keep_semantic_failures_without_spending_retries(tmp_path, monkeypatch):
    bank = rolling()
    lib.publish_bank(bank, tmp_path / 'library')
    calls = []
    def call(client, config, key, body, secrets, cancelled=None):
        cell = next(cell for cell in bank['cells'] if cell['user'] == body['messages'][1]['content'])
        calls.append(cell['id'])
        if cell['id'] == lib.CORE_CELL_IDS[0]:
            value = 'I cannot answer.'
        else:
            value = next(iter(bank['models'][0]['counts'][cell['id']]))
            if cell['normalize_as'] == 'binary':
                value = next(k for k, v in ident.COIN[cell['lang']].items() if v == value)
        return {'http_status': 200, 'error': None, 'elapsed_seconds': .01,
                'raw_response': {'choices': [{'message': {'content': value}, 'finish_reason': 'stop'}]}}
    monkeypatch.setattr(ident.fp, '_call', call)
    request = {**payload(bank['id']), 'budget': {'max_requests': 16, 'max_output_tokens': 256}}
    report = ident.run_identification(request, tmp_path / 'run', library_dir=tmp_path / 'library')
    assert calls == [identifier for identifier in lib.CORE_CELL_IDS[:4] for _ in range(4)]
    assert report['status'] == 'reference_shortlist'
    assert report['coverage']['completed_cells'] == 3
    assert report['coverage']['invalid_answers'] == {'refusal': 4}
    assert report['coverage']['incomplete_cells'] == [lib.CORE_CELL_IDS[0]]
    assert report['cost']['real_requests'] == 16
    assert report['cost']['requested_output_tokens'] == 256
    path = tmp_path / 'run/identification_records.jsonl'
    rows = ident.parse_identification_records(path.read_text('utf8'))
    assert rows[0]['sampling_policy'] == ident.CORE_SAMPLING_POLICY
    assert ident.replay_identification(rows, tmp_path / 'replay') == report


def test_fixed_attempt_blocks_still_stop_transport_failure_after_one_attempt(tmp_path, monkeypatch):
    bank = rolling()
    lib.publish_bank(bank, tmp_path / 'library')
    calls = []
    def failed(*args):
        calls.append(1)
        return {'http_status': 503, 'error': 'http_503', 'raw_response': None, 'elapsed_seconds': .01}
    monkeypatch.setattr(ident.fp, '_call', failed)
    report = ident.run_identification(payload(bank['id']), tmp_path / 'run', library_dir=tmp_path / 'library')
    assert len(calls) == report['cost']['real_requests'] == 1
    assert report['status'] == 'insufficient_evidence'
    assert report['collection']['state'] == 'partial'
    ident.parse_identification_records((tmp_path / 'run/identification_records.jsonl').read_text('utf8'))


def test_unmarked_rolling_records_keep_the_old_strict_semantic_stop(tmp_path):
    bank = rolling()
    config, _, _ = ident._config(payload(bank['id']), bank=bank)
    cell = ident.select_next_cell(bank, bank['models'], {})
    rows = [{'type': 'manifest', 'schema': ident.SCHEMA, 'config': config, 'synthetic': False,
             'bank_snapshot': bank, 'bank_sha256': lib.bank_digest(bank)},
            {'type': 'request', 'cell_id': cell['id'], 'request': ident._body(config, cell),
             'raw_response': {'choices': [{'message': {'content': 'I cannot answer.'}, 'finish_reason': 'stop'}]},
             'http_status': 200, 'error': None, 'elapsed_seconds': .01},
            {'type': 'finish', 'state': 'partial', 'stop_reason': 'refusal', 'failure': 'refusal'}]
    def encoded(values):
        previous = None
        for i, row in enumerate(values):
            row.update(sequence=i, previous_hash=previous)
            row.pop('record_hash', None)
            row['record_hash'] = ident.content_hash(row)
            previous = row['record_hash']
        return '\n'.join(json.dumps(row, ensure_ascii=False) for row in values)
    assert len(ident.parse_identification_records(encoded(rows))) == 3
    extra = deepcopy(rows[1])
    with pytest.raises(ValueError):
        ident.parse_identification_records(encoded([rows[0], rows[1], extra, rows[-1]]))


def test_embedded_snapshot_tampering_fails_even_after_rehashing_record_chain(tmp_path):
    bank = rolling()
    lib.publish_bank(bank, tmp_path / 'library')
    ident.demo_identification(tmp_path / 'demo', bank_id=bank['id'], library_dir=tmp_path / 'library',
                              budget={'max_requests': 12, 'max_output_tokens': 192})
    records = [json.loads(line) for line in (tmp_path / 'demo/identification_records.jsonl').read_text('utf8').splitlines()]
    records[0]['bank_snapshot']['cells'][0]['user'] += ' malicious edit'
    previous = None
    for row in records:
        row['previous_hash'] = previous
        row.pop('record_hash')
        row['record_hash'] = ident.content_hash(row)
        previous = row['record_hash']
    with pytest.raises(ValueError, match='参考哈希'):
        ident.parse_identification_records('\n'.join(json.dumps(row) for row in records))
