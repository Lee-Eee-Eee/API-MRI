from copy import deepcopy
import math
import pytest
from relay_audit.capability_rankings import build_rankings, capability_rankings


def catalog(rows):
    return {'retrieved_at': '2026-11-02', 'benchmarks': [{'id': 'test', 'name': 'Directory'}],
        'models': [{'id': model, 'name': model, 'release_date': '2026-10-01', 'benchmarks': entries}
                   for model, entries in rows.items()]}


def score(value, **extra):
    return {'name': 'Benchmark', 'version': 'v1', 'metric': 'accuracy', 'dataset': 'public',
        'harness': 'Fixed agent', 'source': 'https://example.org/board',
        'score': value, 'date': '2026-11-02', **extra}


def test_one_model_best_configuration_ties_and_original_records_are_preserved():
    source = catalog({'A': [score(7, variant='low'), score(10, variant='high')],
                      'B': [score(10, variant='max')], 'C': [score(0)]})
    original = deepcopy(source)
    result = build_rankings(source)
    board = result['boards'][0]
    assert board['model_count'] == 3 and board['record_count'] == 4
    assert [(r['model'], r['rank'], r['score']) for r in board['rows']] == [('A', 1, 10), ('B', 1, 10), ('C', 3, 0)]
    assert board['rows'][0]['variant'] == 'high'
    assert [r['score'] for r in board['rows'][0]['variants']] == [10, 7]
    assert source == original
    # A filtered frontend view retains these assigned positions.
    assert [r['rank'] for r in board['rows'] if r['model'] == 'C'] == [3]


@pytest.mark.parametrize('field,value', [
    ('version', 'v2'), ('metric', 'Elo'), ('dataset', 'private'),
    ('harness', 'Other agent'), ('source', 'https://example.org/other'),
])
def test_different_units_versions_harness_datasets_and_sources_are_separate(field, value):
    rows = {m: [score(i), score(i+100, **{field: value})] for i, m in enumerate(['A', 'B', 'C'])}
    boards = build_rankings(catalog(rows))['boards']
    assert len(boards) == 2
    assert {b[field] for b in boards} == {score(0)[field], value}
    assert all(b['model_count'] == 3 and b['record_count'] == 3 for b in boards)


def test_explicit_tool_access_is_separate_from_unknown_and_reasoning_effort():
    entries = {m: [score(i, variant='with tools; high'), score(i+1, variant='without tools; low'),
                   score(i+2, variant='no tools; high'), score(i+3, variant='max with fallback')]
               for i, m in enumerate(['A', 'B', 'C'])}
    boards = {b['condition']: b for b in build_rankings(catalog(entries))['boards']}
    assert set(boards) == {'with tools', 'without tools', None}
    assert boards['without tools']['record_count'] == 6
    assert all(r['variant'] == 'no tools; high' for r in boards['without tools']['rows'])
    assert all(r['variant'] == 'max with fallback' for r in boards[None]['rows'])


def test_lower_is_better_uses_minimum_and_competition_ranks():
    data = catalog({'A': [score(.2, metric='overall edit distance'), score(.1, metric='overall edit distance')],
                    'B': [score(.1, metric='overall edit distance')], 'C': [score(.3, metric='overall edit distance')]})
    b = build_rankings(data)['boards'][0]
    assert b['direction'] == 'lower'
    assert [(r['score'], r['rank']) for r in b['rows']] == [(.1, 1), (.1, 1), (.3, 3)]


@pytest.mark.parametrize('metric', [None, 'score', 'average position score', 'new metric'])
def test_unknown_direction_has_no_invented_ranking(metric):
    b = build_rankings(catalog({'C': [score(100, metric=metric)], 'A': [score(1, metric=metric)],
                                'B': [score(10, metric=metric)]}))['boards'][0]
    assert b['direction'] is None
    assert [r['model'] for r in b['rows']] == ['A', 'B', 'C']
    assert all(r['rank'] is None for r in b['rows'])


def test_nonfinite_missing_boolean_string_and_duplicate_configurations_do_not_create_models():
    invalid = [score(None), score(True), score('9'), score(math.nan), score(math.inf)]
    data = catalog({'A': [score(3), score(3)], 'B': [score(2)], 'C': invalid})
    assert build_rankings(data)['boards'] == []
    data['models'].append({'id': 'D', 'name': 'D', 'benchmarks': [score(0)]})
    b = build_rankings(data)['boards'][0]
    assert b['model_count'] == 3 and b['record_count'] == 4
    assert len(b['rows'][0]['variants']) == 2


def test_board_ids_and_rank_order_are_stable_when_records_are_reordered():
    data = catalog({'A': [score(5), score(10, variant='high')], 'B': [score(6)], 'C': [score(7)]})
    first = build_rankings(data)
    data['models'].reverse()
    for m in data['models']: m['benchmarks'].reverse()
    assert build_rankings(data) == first


def test_real_catalog_defaults_to_original_aa_snapshot_and_omits_sparse_boards():
    result = capability_rankings()
    board = next(b for b in result['boards'] if b['id'] == result['default_board'])
    assert board['name'] == 'Artificial Analysis Intelligence Index'
    assert board['source_kind'] == 'snapshot'
    assert board['version'] == 'snapshot 2026-11-02'
    assert board['direction'] == 'higher'
    assert board['record_count'] == 88 and board['model_count'] == 42
    assert len(board['rows']) == 42
    assert all(b['model_count'] >= 3 for b in result['boards'])
    assert all(len({r['model_id'] for r in b['rows']}) == len(b['rows']) for b in result['boards'])
    assert result['benchmarks'] and result['retrieved_at']


def test_rankings_endpoint_returns_only_rankable_cohorts_without_outgoing_calls(tmp_path,monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from relay_audit.web import create_app
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',lambda *a,**k:pytest.fail('unexpected outgoing HTTP'))
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8765') as client:
        response=client.get('/api/capability-rankings')
        assert response.status_code==200
        value=response.json()
        assert value['boards'] and all(b['direction'] in {'higher','lower'} for b in value['boards'])
        assert any(b['id']==value['default_board'] for b in value['boards'])
        assert all(len(b['rows'])==b['model_count']>=3 for b in value['boards'])


@pytest.mark.parametrize('rankable', [True, False])
def test_endpoint_repairs_default_after_direction_filter(tmp_path, monkeypatch, rankable):
    from fastapi.testclient import TestClient
    from relay_audit.web import create_app
    boards = [{'id': 'unknown', 'direction': None}]
    if rankable:
        boards.append({'id': 'known', 'direction': 'higher'})
    monkeypatch.setattr('relay_audit.capability_rankings.capability_rankings',
                        lambda: {'default_board': 'unknown', 'boards': boards})
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8765') as client:
        value = client.get('/api/capability-rankings').json()
    assert value['default_board'] == ('known' if rankable else None)
    assert len(value['boards']) == int(rankable)
