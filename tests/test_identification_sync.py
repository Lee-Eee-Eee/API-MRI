"""Data-only public sync is bounded, immutable and never activates a bank."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json

import httpx
import pytest

from relay_audit import identification_library as lib
from relay_audit import identification_sync as sync


@pytest.fixture(autouse=True)
def isolated_public_index(monkeypatch, tmp_path):
    monkeypatch.setattr(lib, 'PUBLIC_INDEX_PATH', tmp_path / 'bundled/public-index.json')


def rolling(identifier='rolling-fixture'):
    row = {'model': 'service/fixture/model', 'label': 'Fixture model', 'family': 'Fixture',
        'providers': ['Fixture'], 'aliases': ['fixture-latest'],
        'counts': {c['id']: {'17' if c['normalize_as'] == 'integer' else 'h' if c['normalize_as'] == 'binary' else 'a': 8}
                   for c in lib.core_cells()},
        'holdout_counts': {c['id']: {'29' if c['normalize_as'] == 'integer' else 't' if c['normalize_as'] == 'binary' else 'b': 4}
                          for c in lib.core_cells()},
        'collected_at': {'first': '2026-11-09', 'last': '2026-11-09'},
        'provenance': {'kind': 'first_party_route_claim', 'label': 'Operator declaration',
                       'records_sha256': 'a' * 64}}
    return lib.build_rolling_bank([row], bank_id=identifier, built_on='2026-11-09')


def index_and_files(banks):
    files = {b['id'] + '.json': lib.canonical_bytes(b) for b in banks}
    index = {'schema': 'api-mri-public-reference-index/1', 'updated_at': '2026-11-09T00:00:00+00:00',
        'banks': [{'id': b['id'], 'filename': b['id'] + '.json',
                   'sha256': hashlib.sha256(files[b['id'] + '.json']).hexdigest()} for b in banks]}
    return index, files


def mock_client(index, files, requests, *, redirect=False):
    def handler(request):
        requests.append(str(request.url))
        assert request.method == 'GET'
        assert str(request.url).startswith(sync.PUBLIC_ROOT)
        assert 'authorization' not in request.headers
        if redirect:
            return httpx.Response(302, headers={'location': 'https://external.test/steal'})
        if str(request.url) == sync.INDEX_URL:
            return httpx.Response(200, content=json.dumps(index).encode())
        return httpx.Response(200, content=files[request.url.path.rsplit('/', 1)[-1]])
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_updates_data_once_skips_known_hash_and_never_activates(tmp_path):
    bank = rolling()
    index, files = index_and_files([bank])
    requests = []
    lib.activate_bank('legacy', tmp_path)
    pointer = (tmp_path / 'identification/active.json').read_bytes()
    with mock_client(index, files, requests) as client:
        result = sync.sync_public_references(tmp_path, client=client)
        assert result['status'] == 'updated' and result['added'] == 1
        assert result['model_api_calls'] == 0
        assert requests == [sync.INDEX_URL, sync.PUBLIC_ROOT + 'rolling-fixture.json']
        assert sync.sync_public_references(tmp_path, client=client)['status'] == 'cached'
        assert len(requests) == 2
        forced = sync.sync_public_references(tmp_path, force=True, client=client)
        assert forced['status'] == 'up_to_date' and forced['unchanged'] == 1
        assert requests[-1] == sync.INDEX_URL and len(requests) == 3
    assert lib.load_bank(bank['id'], tmp_path) == bank
    assert (tmp_path / 'identification/active.json').read_bytes() == pointer
    assert lib.load_bank(bank['id'], tmp_path)['validation']['identity_authenticated'] is False
    assert lib.load_bank(bank['id'], tmp_path)['validation']['open_set_calibrated'] is False


@pytest.mark.parametrize('age_days,expected', [(6, False), (7.01, True), (-1, True)])
def test_ttl_is_seven_days_and_future_dates_do_not_disable_updates(tmp_path, age_days, expected):
    lib._atomic_json(sync._state_path(tmp_path), {'status': 'updated',
        'last_checked': (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()})
    assert sync.sync_status(tmp_path)['due'] is expected


@pytest.mark.parametrize('state', [[], 42, None, 'text', {'last_checked': 42}, {'last_checked': 'bad'},
    {'status': 'updated', 'last_checked': datetime.now(timezone.utc).isoformat(), 'added': 'many'},
    {'status': 'failed', 'last_checked': datetime.now(timezone.utc).isoformat()}])
def test_bad_or_failed_state_remains_retryable(tmp_path, state):
    lib._atomic_json(sync._state_path(tmp_path), state)
    assert sync.sync_status(tmp_path)['due'] is True


def test_force_type_checked_even_when_ttl_cached(tmp_path):
    lib._atomic_json(sync._state_path(tmp_path), {'status': 'updated', 'last_checked': datetime.now(timezone.utc).isoformat()})
    with pytest.raises(ValueError, match='boolean'):
        sync.sync_public_references(tmp_path, force=0)


def test_redirect_is_rejected_even_if_injected_client_follows_redirects(tmp_path):
    requests = []
    with mock_client({}, {}, requests, redirect=True) as client:
        with pytest.raises(ValueError, match='download failed'):
            sync.sync_public_references(tmp_path, client=client)
    assert requests == [sync.INDEX_URL]
    assert sync.sync_status(tmp_path)['due'] is True


def test_connection_failure_has_safe_retryable_result(tmp_path):
    def fail(request):
        raise httpx.ConnectError('private endpoint details',request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ValueError,match='Public reference download failed'):
            sync.sync_public_references(tmp_path,client=client)
    assert sync.sync_status(tmp_path)['due'] is True


@pytest.mark.parametrize('mutation', ['bad_digest', 'bad_json', 'nan', 'wrong_id', 'claim_identity',
                                    'sha_type', 'traversal', 'extra', 'old_version_digest'])
def test_invalid_updates_preserve_old_snapshot_and_pointer(tmp_path, mutation):
    old = rolling('rolling-old')
    metadata = lib.publish_bank(old, tmp_path)
    lib.activate_bank(metadata['id'], tmp_path)
    old_catalog = (tmp_path / 'identification/catalog.json').read_bytes()
    pointer = (tmp_path / 'identification/active.json').read_bytes()
    bank = rolling('rolling-next')
    index, files = index_and_files([bank])
    row = index['banks'][0]
    if mutation == 'bad_digest': row['sha256'] = 'f' * 64
    elif mutation == 'bad_json': files[row['filename']] = b'broken json'
    elif mutation == 'nan': files[row['filename']] = b'{"invalid":NaN}'
    elif mutation == 'wrong_id': bank['id'] = 'different-id'
    elif mutation == 'claim_identity': bank['validation']['identity_authenticated'] = True
    elif mutation == 'sha_type': row['sha256'] = 42
    elif mutation == 'traversal': row['filename'] = '../rolling-next.json'
    elif mutation == 'extra': row['url'] = 'https://other.test/bank.json'
    elif mutation == 'old_version_digest':
        row.update(id=old['id'], filename=old['id'] + '.json', sha256='b' * 64)
    if mutation in {'wrong_id', 'claim_identity'}:
        files[row['filename']] = lib.canonical_bytes(bank)
    if mutation in {'bad_json', 'nan', 'wrong_id', 'claim_identity'}:
        row['sha256'] = hashlib.sha256(files[row['filename']]).hexdigest()
    with mock_client(index, files, []) as client:
        with pytest.raises(ValueError):
            sync.sync_public_references(tmp_path, client=client)
    assert (tmp_path / 'identification/catalog.json').read_bytes() == old_catalog
    assert (tmp_path / 'identification/active.json').read_bytes() == pointer
    assert lib.load_bank(old['id'], tmp_path) == old


def test_valid_first_snapshot_is_not_published_when_later_download_is_bad(tmp_path):
    index, files = index_and_files([rolling('rolling-first'), rolling('rolling-second')])
    files['rolling-second.json'] = b'broken'
    with mock_client(index, files, []) as client:
        with pytest.raises(ValueError): sync.sync_public_references(tmp_path, client=client)
    assert not (tmp_path / 'identification/catalog.json').exists()


@pytest.mark.parametrize('updated_at', [None, 42, 'bad', '2026-11-09'])
def test_invalid_index_date_cannot_publish(tmp_path, updated_at):
    index, files = index_and_files([rolling()])
    index['updated_at'] = updated_at
    with mock_client(index, files, []) as client:
        with pytest.raises(ValueError): sync.sync_public_references(tmp_path, client=client)
    assert not (tmp_path / 'identification/catalog.json').exists()


def test_malformed_json_keeps_existing_data(tmp_path):
    previous = rolling('rolling-old')
    lib.publish_bank(previous, tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'broken'))) as client:
        with pytest.raises(ValueError): sync.sync_public_references(tmp_path, client=client)
    assert lib.load_bank(previous['id'], tmp_path) == previous
    assert sync.sync_status(tmp_path)['due'] is True


@pytest.mark.parametrize('target', ['https://evil.test/index.json', sync.PUBLIC_ROOT + '../secret.json',
                                  sync.PUBLIC_ROOT + 'file.json?token=x'])
def test_download_accepts_only_fixed_project_raw_data_url(target):
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('unexpected network'))) as client:
        with pytest.raises(ValueError): sync._download(client, target, 100)


def test_streamed_size_limit_keeps_library_empty(tmp_path, monkeypatch):
    index, files = index_and_files([rolling()])
    monkeypatch.setattr(sync, 'MAX_SNAPSHOT', 16)
    with mock_client(index, files, []) as client:
        with pytest.raises(ValueError, match='size limit'):
            sync.sync_public_references(tmp_path, client=client)
    assert not (tmp_path / 'identification/catalog.json').exists()


def test_atomic_status_failure_does_not_change_active_pointer(tmp_path, monkeypatch):
    lib.activate_bank('legacy', tmp_path)
    pointer = (tmp_path / 'identification/active.json').read_bytes()
    index, files = index_and_files([rolling()])
    original = lib._atomic_json
    def fail_state(path, value):
        if path.name == 'public-sync.json': raise OSError('fixture interruption')
        original(path, value)
    monkeypatch.setattr(lib, '_atomic_json', fail_state)
    with mock_client(index, files, []) as client:
        with pytest.raises(OSError): sync.sync_public_references(tmp_path, client=client)
    assert (tmp_path / 'identification/active.json').read_bytes() == pointer
    assert lib.load_bank('rolling-fixture', tmp_path)['validation']['identity_authenticated'] is False
