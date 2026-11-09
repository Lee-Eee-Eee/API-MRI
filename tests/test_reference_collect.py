"""Reference capture protocol, hard budgets and replay publication, without model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from relay_audit import fingerprint as fp
from relay_audit import identification_library as lib
from relay_audit import reference_collect as collect


@pytest.fixture(autouse=True)
def isolated_public_index(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, 'PUBLIC_INDEX_PATH', tmp_path / 'bundled/public-index.json')


def payload(**kwargs):
    return {'candidate': {'api_base': 'https://example.test/v1', 'model': 'new-latest',
                          'api_key': 'fixture-only-secret'},
            'source': {'kind': 'first_party_route_claim', 'label': 'New model',
                       'provider': 'Fixture provider', 'model_alias': 'new-model',
                       'url': 'https://example.test/models/new'}, **kwargs}


def answer(cell, holdout):
    if cell['normalize_as'] == 'integer':
        return '29' if holdout else '17'
    if cell['normalize_as'] == 'binary':
        return '反面' if holdout else '正面'
    return 'b' if holdout else 'a'


def fake_call(counter, *, fail_at=None, extra=None, offset=0):
    cells = lib.core_cells()
    def call(client, config, key, body, secrets, cancelled):
        n = len(counter) + offset
        counter.append(deepcopy(body))
        row = {'http_status': 200, 'raw_response': {'choices': [{'message': {
            'content': answer(cells[n // 12], n % 12 >= 8)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11}},
            'error': None, 'response_headers': {}, 'elapsed_seconds': .01}
        if n == fail_at:
            row.update(http_status=503, error='http_503', raw_response=None)
        if extra:
            row['raw_response']['extra'] = extra
        return row
    return call


def read_records(out):
    return (out / 'reference_records.jsonl').read_text(encoding='utf8')


def rechain(rows):
    previous = None
    for i, row in enumerate(rows):
        row.update(sequence=i, previous_hash=previous)
        row.pop('record_hash', None)
        row['record_hash'] = fp.content_hash(row)
        previous = row['record_hash']
    return '\n'.join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows) + '\n'


@pytest.fixture
def complete_capture(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls))
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    return out, report, calls


def test_complete_capture_is_96_calls_1536_quota_and_disjoint_64_32(complete_capture):
    out, report, calls = complete_capture
    assert len(calls) == 96
    assert all(body['max_tokens'] == 16 and body['temperature'] == 1
               and body['reasoning'] == {'enabled': False} for body in calls)
    assert report['cost']['requested_output_tokens'] == 1536
    assert report['reference']['reference_answers'] == 64
    assert report['reference']['holdout_answers'] == 32
    assert report['reference']['can_publish'] is True
    assert report['evaluation']['identity_confirmed'] is False
    model = report['model_reference']
    for cell in lib.core_cells():
        assert model['counts'][cell['id']] == {answer(cell, False).lower() if cell['normalize_as'] != 'binary' else 'h': 8}
        assert model['holdout_counts'][cell['id']] == {answer(cell, True).lower() if cell['normalize_as'] != 'binary' else 't': 4}
    raw = read_records(out)
    assert 'fixture-only-secret' not in raw
    assert len(collect.parse_reference_records(raw)) == 98


@pytest.mark.parametrize('budget,expected', [({'max_requests': 5}, 5), ({'max_output_tokens': 31}, 1),
                                           ({'max_output_tokens': 1}, 0),
                                           ({'max_requests': 256, 'max_output_tokens': 4096}, 96)])
def test_hard_budget_never_starts_unaffordable_request(tmp_path, monkeypatch, budget, expected):
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls))
    out = tmp_path / 'capture'
    plan = collect.plan_reference_capture(payload(budget=budget))
    report = collect.run_reference_capture(payload(budget=budget), out)
    assert len(calls) == expected == plan['maximum_requests']
    assert report['cost']['requested_output_tokens'] == expected * 16
    assert report['collection']['stop_code'] == ('complete' if expected == 96 else 'budget_exhausted')
    collect.parse_reference_records(read_records(out))


def test_first_failure_stops_without_retry_and_cannot_publish(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls, fail_at=2))
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    assert len(calls) == 3
    assert report['collection']['stop_code'] == 'request_failed'
    assert report['reference']['can_publish'] is False
    collect.parse_reference_records(read_records(out))
    with pytest.raises(ValueError):
        collect.publish_capture(out / 'reference_records.jsonl', tmp_path / 'library')
    assert not (tmp_path / 'library/identification/catalog.json').exists()


def test_cancellation_before_first_request_is_replayable(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls))
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out, cancelled=lambda: True)
    assert not calls
    assert report['collection']['cancelled'] is True
    collect.parse_reference_records(read_records(out))


@pytest.mark.parametrize('mutation', ['manifest_type', 'manifest_run', 'request_run', 'finish_run', 'split',
    'body', 'replicate', 'time', 'duration', 'http', 'headers', 'error', 'finish_reason', 'failure', 'missing_response', 'extra'])
def test_rehashed_protocol_or_metadata_tampering_is_rejected(complete_capture, mutation):
    out, _, _ = complete_capture
    rows = json.loads('[' + ','.join(read_records(out).splitlines()) + ']')
    if mutation == 'manifest_type': rows[0]['type'] = 'request'
    elif mutation == 'manifest_run': rows[0]['run_id'] = 'bad'
    elif mutation == 'request_run': rows[1]['run_id'] = 'f' * 32
    elif mutation == 'finish_run': rows[-1]['run_id'] = 'f' * 32
    elif mutation == 'split': rows[9]['split'] = 'reference'
    elif mutation == 'body': rows[1]['request']['max_tokens'] = 32
    elif mutation == 'replicate': rows[1]['replicate'] = False
    elif mutation == 'time': rows[1]['requested_at'] = '2020-01-01T00:00:00+00:00'
    elif mutation == 'duration': rows[1]['elapsed_seconds'] = -1
    elif mutation == 'http': rows[1]['http_status'] = True
    elif mutation == 'headers': rows[1]['response_headers'] = []
    elif mutation == 'error': rows[1]['error'] = 42
    elif mutation == 'finish_reason': rows[-1]['stop_reason'] = 'budget_exhausted'
    elif mutation == 'failure': rows[-1]['failure'] = 'transport_or_format_error'
    elif mutation == 'missing_response': del rows[1]['raw_response']
    elif mutation == 'extra': rows[1]['synthetic'] = True
    with pytest.raises(ValueError):
        collect.parse_reference_records(rechain(rows))


def test_plain_content_tampering_fails_hash_chain(complete_capture):
    out, _, _ = complete_capture
    with pytest.raises(ValueError):
        collect.parse_reference_records(read_records(out).replace('"replicate": 0', '"replicate": 1', 1))


def test_duplicate_json_keys_are_rejected_even_when_decoded_hash_would_match(complete_capture):
    out, _, _ = complete_capture
    text = read_records(out).replace('"replicate": 0', '"replicate": 0, "replicate": 0', 1)
    with pytest.raises(ValueError): collect.parse_reference_records(text)


@pytest.mark.parametrize('invalid', ['cache', 'reasoning', 'truncated', 'redacted_secret'])
def test_invalid_provider_behaviour_stops_immediately_and_secrets_are_removed(tmp_path, monkeypatch, invalid):
    calls = []
    base = fake_call(calls)
    def wrapped(*args):
        row = base(*args)
        if invalid == 'cache': row['response_headers']['x-api2d-cache'] = 'hit'
        elif invalid == 'reasoning': row['raw_response']['usage']['completion_tokens_details'] = {'reasoning_tokens': 1}
        elif invalid == 'truncated': row['raw_response']['choices'][0]['finish_reason'] = 'length'
        else:
            row['raw_response']['choices'][0]['message']['content'] = 'fixture-only-secret'
            row['error'] = 'fixture-only-secret'
        return row
    monkeypatch.setattr(fp, '_call', wrapped)
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    assert len(calls) == 1 and report['reference']['can_publish'] is False
    raw = read_records(out)
    assert 'fixture-only-secret' not in raw
    collect.parse_reference_records(raw)


def test_premature_budget_stop_and_failed_capture_marked_complete_are_rejected(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls, fail_at=0))
    out = tmp_path / 'failed'
    collect.run_reference_capture(payload(), out)
    rows = collect.parse_reference_records(read_records(out))
    rows[-1]['stop_reason'] = 'complete'
    with pytest.raises(ValueError): collect.parse_reference_records(rechain(rows))
    rows[-2]['error'] = None
    rows[-2]['http_status'] = 200
    rows[-2]['raw_response'] = {'choices': [{'message': {'content': 'a'}}]}
    rows[-1].update(stop_reason='budget_exhausted', failure=None)
    with pytest.raises(ValueError): collect.parse_reference_records(rechain(rows))


def test_capture_stops_with_bounded_evidence_even_for_large_envelopes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls, extra='x' * 70000))
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    assert len(calls) < 96
    assert (out / 'reference_records.jsonl').stat().st_size <= collect.MAX_EVIDENCE_BYTES
    rows = collect.parse_reference_records(read_records(out))
    assert rows[-2]['error'] == 'evidence_size_limit'
    assert report['collection']['stop_code'] == 'request_failed'


def test_publish_keeps_raw_digest_counts_and_active_bank(complete_capture, tmp_path):
    out, report, _ = complete_capture
    directory = tmp_path / 'library'
    lib.activate_bank('legacy', directory)
    active_before = (directory / 'identification/active.json').read_bytes()
    raw = (out / 'reference_records.jsonl').read_bytes()
    metadata = collect.publish_capture(out / 'reference_records.jsonl', directory)
    bank = lib.load_bank(metadata['id'], directory)
    model = next(row for row in bank['models'] if row['model'] == report['model_reference']['model'])
    assert model['provenance']['records_sha256'] == hashlib.sha256(raw).hexdigest()
    assert model['provenance']['url'] == 'https://example.test/models/new'
    assert model['counts'] == report['model_reference']['counts']
    assert model['holdout_counts'] == report['model_reference']['holdout_counts']
    assert bank['validation']['identity_authenticated'] is False
    assert (directory / 'identification/active.json').read_bytes() == active_before


def test_publish_reads_one_bounded_snapshot(complete_capture, tmp_path, monkeypatch):
    out, _, _ = complete_capture
    path = out / 'reference_records.jsonl'
    original, count = Path.read_bytes, []
    def once(self):
        if self == path:
            count.append(1)
            if len(count) > 1: raise AssertionError('evidence was reopened')
        return original(self)
    monkeypatch.setattr(Path, 'read_bytes', once)
    collect.publish_capture(path, tmp_path / 'library')
    assert len(count) == 1
    too_large = tmp_path / 'too-large.jsonl'
    too_large.write_bytes(b' ' * (collect.MAX_EVIDENCE_BYTES + 1))
    with pytest.raises(ValueError): collect.publish_capture(too_large, tmp_path / 'library')


def test_publish_rejects_symbolic_link(complete_capture, tmp_path):
    out, _, _ = complete_capture
    link = tmp_path / 'linked.jsonl'
    try:
        link.symlink_to(out / 'reference_records.jsonl')
    except OSError:
        pytest.skip('This Windows account cannot create symbolic links')
    with pytest.raises(ValueError): collect.publish_capture(link, tmp_path / 'library')


@pytest.mark.parametrize('source_url', ['http://example.test', 'https://u:p@example.test', 'https://example.test?key=x'])
def test_source_url_cannot_carry_credentials(source_url):
    config = payload()
    config['source']['url'] = source_url
    with pytest.raises(ValueError): collect.plan_reference_capture(config)


def test_unpublishable_metadata_is_rejected_before_any_call(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, '_call', lambda *args: pytest.fail('unexpected model call'))
    config = payload()
    config['source']['provider'] = '很长的服务名' * 30
    with pytest.raises(ValueError): collect.run_reference_capture(config, tmp_path / 'capture')
    assert not (tmp_path / 'capture').exists()


def test_unpublishable_normalized_answer_uses_fixed_attempts(tmp_path, monkeypatch):
    calls = []
    base = fake_call(calls)
    def wrapped(*args):
        row = base(*args)
        row['raw_response']['choices'][0]['message']['content'] = 'a' * 201
        return row
    monkeypatch.setattr(fp, '_call', wrapped)
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    assert len(calls) == 96 and report['collection']['failure'] is None
    assert sum(report['reference']['invalid_answers'].values()) == 96
    assert report['reference']['invalid_answers']['normalized_answer_too_long'] == 60
    assert report['reference']['can_publish'] is False
    collect.parse_reference_records(read_records(out))


def legacy_seed(complete_text, count=62):
    rows = [json.loads(line) for line in complete_text.splitlines()]
    manifest = rows[0]
    for key in ('capture_policy', 'resume_source_sha256', 'resume_source_run_id', 'resume_receipt', 'reused_requests'):
        manifest.pop(key)
    requests = rows[1:count + 1]
    for row in requests:
        row.pop('reused')
    requests[-1]['raw_response']['choices'][0]['message']['content'] = 'У меня нет любимого цвета'
    finish = {'type': 'finish', 'run_id': manifest['run_id'], 'stop_reason': 'request_failed', 'failure': 'invalid'}
    return rechain([manifest, *requests, finish])


def test_resume_old_semantic_partial_reuses_62_and_makes_only_remaining_34_calls(complete_capture, tmp_path, monkeypatch):
    original, _, _ = complete_capture
    seed = legacy_seed(read_records(original))
    seed_rows = collect.parse_reference_records(seed)
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls, offset=62))
    plan = collect.plan_reference_capture(payload(), seed_records=seed)
    assert plan['maximum_requests'] == 34 and plan['maximum_output_tokens'] == 544
    assert plan['reused_requests'] == 62
    out = tmp_path / 'resumed'
    report = collect.run_reference_capture(payload(), out, seed_records=seed)
    assert len(calls) == 34
    assert report['cost']['real_requests'] == report['cost']['new_requests'] == 34
    assert report['cost']['requested_output_tokens'] == 544
    assert report['cost']['reused_requests'] == 62
    assert report['cost']['reused_requested_output_tokens'] == 992
    assert report['cost']['capture_attempts'] == 96
    assert report['reference']['valid_answers'] == 95
    assert report['reference']['reference_answers'] == 63
    assert report['reference']['holdout_answers'] == 32
    assert report['reference']['invalid_answers'] == {'invalid': 1}
    assert report['reference']['can_publish'] is True
    rows = collect.parse_reference_records(read_records(out))
    assert rows[0]['resume_source_sha256'] == hashlib.sha256(seed.encode()).hexdigest()
    assert rows[62]['original_record_hash'] == seed_rows[62]['record_hash']
    assert rows[62]['reused'] is True and rows[63]['reused'] is False
    assert rows[63]['cell_id'] == 'color-favorite|ru' and rows[63]['replicate'] == 2
    # Both original files remain byte-for-byte unchanged.
    assert collect.parse_reference_records(seed) == seed_rows


@pytest.mark.parametrize('change', ['source', 'model', 'manual_base', 'hash', 'copied_response', 'copied_split', 'reused_count', 'nested'])
def test_resume_receipt_mismatch_is_rejected(complete_capture, tmp_path, monkeypatch, change):
    original, _, _ = complete_capture
    seed = legacy_seed(read_records(original))
    config = payload()
    if change in {'source', 'model', 'manual_base'}:
        if change == 'source': config['source']['label'] = 'Different source'
        elif change == 'model': config['candidate']['model'] = 'different-model'
        else: config['candidate']['api_base'] = 'https://different.test/v1'
        with pytest.raises(ValueError): collect.plan_reference_capture(config, seed_records=seed)
        return
    monkeypatch.setattr(fp, '_call', fake_call([], offset=62))
    out = tmp_path / 'resumed'
    collect.run_reference_capture(config, out, seed_records=seed)
    rows = collect.parse_reference_records(read_records(out))
    if change == 'hash': rows[0]['resume_source_sha256'] = 'a' * 64
    elif change == 'copied_response': rows[1]['raw_response']['choices'][0]['message']['content'] = 'b'
    elif change == 'copied_split': rows[9]['split'] = 'reference'
    elif change == 'reused_count': rows[0]['reused_requests'] = 61
    elif change == 'nested':
        with pytest.raises(ValueError): collect.plan_reference_capture(config, seed_records=read_records(out))
        return
    with pytest.raises(ValueError): collect.parse_reference_records(rechain(rows))


def test_semantic_invalid_attempt_does_not_retry_or_stop(tmp_path, monkeypatch):
    calls = []
    base = fake_call(calls)
    def wrapped(*args):
        row = base(*args)
        if len(calls) == 62:
            row['raw_response']['choices'][0]['message']['content'] = 'У меня нет любимого цвета'
        return row
    monkeypatch.setattr(fp, '_call', wrapped)
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    assert len(calls) == 96
    rows = collect.parse_reference_records(read_records(out))
    assert rows[62]['replicate'] == 1 and rows[63]['replicate'] == 2
    assert report['reference']['reference_answers'] == 63
    assert report['reference']['invalid_answers'] == {'invalid': 1}
    assert report['reference']['can_publish'] is True


def test_dsh_resume_permits_only_recorded_bridge_port_change(complete_capture, tmp_path, monkeypatch):
    original, _, _ = complete_capture
    rows = collect.parse_reference_records(legacy_seed(read_records(original)))
    dsh_model = 'dsh:fixture-provider:new-latest'
    rows[0]['config']['candidate'] = {'api_base': 'http://127.0.0.1:18001/v1/chat/completions', 'model': dsh_model}
    for row in rows[1:-1]:
        row['request']['model'] = dsh_model
        row['response_headers']['x-api-mri-transport'] = 'dsh-adapter'
    seed = rechain(rows)
    config = payload()
    config['candidate'].update(api_base='http://127.0.0.1:18002/v1', model=dsh_model)
    assert collect.plan_reference_capture(config, seed_records=seed)['maximum_requests'] == 34
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls, offset=62))
    out = tmp_path / 'resumed'
    collect.run_reference_capture(config, out, seed_records=seed)
    collect.parse_reference_records(read_records(out))
    assert len(calls) == 34
    rows[1]['response_headers'] = {}
    with pytest.raises(ValueError): collect.plan_reference_capture(config, seed_records=rechain(rows))


def test_resume_budget_limits_only_new_attempts(complete_capture, tmp_path, monkeypatch):
    original, _, _ = complete_capture
    seed = legacy_seed(read_records(original))
    calls = []
    monkeypatch.setattr(fp, '_call', fake_call(calls, offset=62))
    config = payload(budget={'max_requests': 3, 'max_output_tokens': 48})
    out = tmp_path / 'resumed'
    report = collect.run_reference_capture(config, out, seed_records=seed)
    assert len(calls) == 3
    assert report['cost']['real_requests'] == 3
    assert report['cost']['capture_attempts'] == 65
    assert report['collection']['stop_code'] == 'budget_exhausted'
    assert report['reference']['can_publish'] is False
    collect.parse_reference_records(read_records(out))


def test_semantic_invalid_slots_remain_empty_and_sparse_capture_cannot_publish(tmp_path, monkeypatch):
    calls = []
    base = fake_call(calls)
    def wrapped(*args):
        row = base(*args)
        n = len(calls) - 1
        if n < 36:
            row['raw_response']['choices'][0]['message']['content'] = ''
        return row
    monkeypatch.setattr(fp, '_call', wrapped)
    out = tmp_path / 'capture'
    report = collect.run_reference_capture(payload(), out)
    assert len(calls) == 96
    assert report['reference']['reference_eligible_cells'] == 5
    assert report['reference']['full_holdout_cells'] == 5
    assert report['reference']['can_publish'] is False
    assert len(report['model_reference']['counts']) == len(report['model_reference']['holdout_counts']) == 8
    assert report['model_reference']['counts']['letter-random|zh'] == {}
    assert report['model_reference']['holdout_counts']['letter-random|zh'] == {}
    collect.parse_reference_records(read_records(out))
