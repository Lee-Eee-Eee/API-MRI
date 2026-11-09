"""Immutable, protocol-bound shared-probe references; no network or model calls."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

LEGACY_PATH = Path(__file__).parent / 'data/identification/one-token-202607.json'
PUBLIC_INDEX_PATH = LEGACY_PATH.parent / 'public-index.json'
SCHEMA = 'api-mri-identification-bank/1'
PROFILE = 'core8-v1'
QUALIFICATION = 'experimental_reference'
MAX_BANK_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 1024 * 1024
MAX_BANKS = 64
MAX_MODELS = 256
MIN_REFERENCE_REPETITIONS = 5
CORE_CELL_IDS = (
    'letter-random|zh', 'animal-random|zh', 'city-random|ar', 'word-random|ru',
    'letter-random|en', 'color-favorite|ru', 'num100-random|en', 'coin-flip|zh',
)
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_PROVENANCE_KINDS = {'operator_declared', 'first_party_route_claim', 'public_research_record'}


def canonical_bytes(bank: dict) -> bytes:
    return json.dumps(bank, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def bank_digest(bank: dict) -> str:
    return hashlib.sha256(canonical_bytes(bank)).hexdigest()


def _text(value, field, limit=300):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid reference {field}.')
    return value


def _dates(value):
    if not isinstance(value, dict) or set(value) != {'first', 'last'}:
        raise ValueError('Reference collection dates are required.')
    for item in value.values():
        if not isinstance(item, str) or len(item) > 40:
            raise ValueError('Invalid reference date.')
        try:
            datetime.fromisoformat(item.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Invalid reference date.') from None
    if value['first'][:10] > value['last'][:10]:
        raise ValueError('Reference dates are out of order.')
    return deepcopy(value)


def _stamp(path):
    resolved = Path(path).resolve()
    info = resolved.stat()
    return (str(resolved), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@lru_cache(maxsize=4)
def _legacy_state(stamp):
    raw = Path(stamp[0]).read_bytes()
    if len(raw) > 8_000_000:
        raise ValueError('Historical reference exceeds its size limit.')
    bank = json.loads(raw)
    if bank.get('schema') != SCHEMA or not isinstance(bank.get('models'), list) or len(bank['models']) < 2:
        raise ValueError('Invalid historical reference.')
    return bank, hashlib.sha256(raw).hexdigest(), bank_digest(bank)


def _legacy_view():
    # Private, read-only module view. Public callers always receive a detached copy.
    return _legacy_state(_stamp(LEGACY_PATH))[0]


def _legacy_raw_digest():
    return _legacy_state(_stamp(LEGACY_PATH))[1]


def _legacy_canonical_digest():
    return _legacy_state(_stamp(LEGACY_PATH))[2]


def legacy_bank():
    return deepcopy(_legacy_view())


def core_cells(base_bank=None):
    bank = base_bank if base_bank is not None else _legacy_view()
    by_id = {cell['id']: cell for cell in bank['cells']}
    if any(identifier not in by_id for identifier in CORE_CELL_IDS):
        raise ValueError('Reference does not contain the fixed core8-v1 prompts.')
    return [deepcopy(by_id[identifier]) for identifier in CORE_CELL_IDS]


def _counts(value, identifiers, *, copy_values=True, minimum=MIN_REFERENCE_REPETITIONS):
    if not isinstance(value, dict) or set(value) != set(identifiers):
        raise ValueError('Each rolling model needs every fixed core probe.')
    total = 0
    result = {}
    for identifier in identifiers:
        counts = value[identifier]
        if not isinstance(counts, dict) or not (0 if minimum == 0 else 1) <= len(counts) <= 512:
            raise ValueError('Invalid reference answer counts.')
        for answer, count in counts.items():
            if not isinstance(answer, str) or not answer or len(answer) > 200 or any(ord(c) < 32 for c in answer):
                raise ValueError('Invalid normalized reference answer.')
            if type(count) is not int or not 1 <= count <= 100000:
                raise ValueError('Reference counts must be positive integers.')
        repetitions = sum(counts.values())
        if not minimum <= repetitions <= 100000:
            raise ValueError('Each core probe needs at least five reference repetitions.')
        total += repetitions
        if copy_values:
            result[identifier] = deepcopy(counts)
    return result, total


def _provenance(value):
    allowed = {'kind', 'label', 'provider', 'url', 'records_sha256', 'license', 'route', 'note'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('kind') not in _PROVENANCE_KINDS:
        raise ValueError('Reference source role must be declared explicitly.')
    result = {'kind': value['kind'], 'label': _text(value.get('label'), 'source label')}
    for field in ('provider', 'license', 'route', 'note'):
        if field in value:
            result[field] = _text(value[field], field, 1000 if field == 'note' else 300)
    if 'url' in value:
        url = _text(value['url'], 'source URL', 1000)
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Source URL must be HTTPS and contain no credentials or query.')
        result['url'] = url
    if 'records_sha256' in value:
        if not isinstance(value['records_sha256'], str) or not _SHA.fullmatch(value['records_sha256']):
            raise ValueError('Invalid source record digest.')
        result['records_sha256'] = value['records_sha256']
    # This value describes a declaration; it cannot certify a provider or model.
    return result


def _new_model(row):
    allowed = {'model', 'label', 'family', 'providers', 'counts', 'collected_at', 'provenance', 'aliases', 'holdout_counts'}
    if not isinstance(row, dict) or set(row) - allowed:
        raise ValueError('Unsupported rolling model fields.')
    result = {field: _text(row.get(field), field, 200) for field in ('model', 'label', 'family')}
    counts = row.get('counts')
    if not isinstance(counts, dict) or set(counts) - set(CORE_CELL_IDS):
        raise ValueError('Reference counts must use the fixed core probes.')
    result['counts'], _ = _counts({identifier: counts.get(identifier, {}) for identifier in CORE_CELL_IDS}, CORE_CELL_IDS, minimum=0)
    if sum(sum(values.values()) >= MIN_REFERENCE_REPETITIONS for values in result['counts'].values()) < 6:
        raise ValueError('New references need at least six core probes with five valid repetitions.')
    result['collected_at'] = _dates(row.get('collected_at'))
    result['provenance'] = _provenance(row.get('provenance'))
    for field in ('providers', 'aliases'):
        values = row.get(field, [])
        if (not isinstance(values, list) or len(values) > 30 or any(not isinstance(v, str) for v in values)
                or len(set(values)) != len(values)):
            raise ValueError(f'Invalid reference {field}.')
        result[field] = [_text(v, field, 200) for v in values]
    if 'holdout_counts' in row:
        holdout = row['holdout_counts']
        if not isinstance(holdout, dict) or set(holdout) - set(CORE_CELL_IDS):
            raise ValueError('Holdout observations must cover the fixed core.')
        holdout = {identifier: holdout.get(identifier, {}) for identifier in CORE_CELL_IDS}
        for counts in holdout.values():
            if not isinstance(counts, dict) or any(not isinstance(k, str) or not k or len(k) > 200
                    or type(v) is not int or not 1 <= v <= 100000 for k, v in counts.items()):
                raise ValueError('Invalid holdout counts.')
            if sum(counts.values()) > 4:
                raise ValueError('Holdout counts cannot exceed four fixed attempts per probe.')
        result['holdout_counts'] = deepcopy(holdout)
    return result


def build_rolling_bank(new_models, *, bank_id, built_on=None, base_bank=None):
    """Keep all legacy labels on one fixed core; append explicitly declared observations."""
    if not isinstance(bank_id, str) or not _ID.fullmatch(bank_id) or bank_id in {'legacy', _legacy_view()['id']}:
        raise ValueError('Invalid reference bank identifier.')
    if not isinstance(new_models, list) or not 1 <= len(new_models) <= MAX_MODELS - 132:
        raise ValueError('Provide at least one new reference model.')
    base = base_bank if base_bank is not None else _legacy_view()
    cells = core_cells(base)
    models = []
    historical = {row['model'] for row in _legacy_view()['models']}
    for row in base['models']:
        counts, _ = _counts({identifier: row['counts'].get(identifier, {}) for identifier in CORE_CELL_IDS}, CORE_CELL_IDS,
                             minimum=MIN_REFERENCE_REPETITIONS if row['model'] in historical else 0)
        models.append({**deepcopy({key: value for key, value in row.items() if key != 'counts'}), 'counts': counts, 'collected_at': deepcopy(row.get('collected_at', base['collected_at'])),
            'provenance': deepcopy(row.get('provenance', {'kind': 'public_research_record', 'label': 'Bruckner PAMELA 2026-07',
                'url': base['source']['data'], 'license': 'CC-BY-4.0'}))})
    known = {row['model'] for row in models}
    additions = [_new_model(row) for row in new_models]
    for row in additions:
        if row['model'] in known:
            if row['model'] in historical:
                raise ValueError('Historical labels cannot be overwritten; use a distinct service reference ID.')
            models = [previous for previous in models if previous['model'] != row['model']]
        known.add(row['model'])
        models.append(row)
    new_first = min(row['collected_at']['first'] for row in additions)
    new_last = max(row['collected_at']['last'] for row in additions)
    result = {'schema': SCHEMA, 'id': bank_id, 'profile': PROFILE, 'qualification': QUALIFICATION,
        'source': {'data': 'https://github.com/Lee-Eee-Eee/API-MRI', 'kind': 'mixed_declared_observations',
                   'legacy_bank': _legacy_view()['id'], 'legacy_sha256': _legacy_canonical_digest(),
                   'parent_bank': base['id'], 'parent_sha256': bank_digest(base)},
        'collected_at': {'first': min(base['collected_at']['first'], new_first), 'last': max(base['collected_at']['last'], new_last)},
        'new_observations_collected_at': {'first': new_first, 'last': new_last},
        'built_on': built_on or date.today().isoformat(), 'reference_split': 'legacy reference counts plus newly declared reference-only counts; holdout excluded',
        'normalization': base['normalization'], 'cells': cells, 'models': models, 'calibration': {},
        'validation': {'identity_authenticated': False, 'open_set_calibrated': False,
                       'reference_labels': 'mixed author/operator declarations', 'heldout_accuracy': None}}
    return validate_bank_snapshot(result)


def validate_bank_snapshot(bank, *, expected_sha=None):
    """Validate executable protocol fields, not a model-identity claim."""
    if not isinstance(bank, dict) or bank.get('schema') != SCHEMA or bank.get('profile') != PROFILE:
        raise ValueError('Unsupported rolling reference snapshot.')
    allowed = {'schema', 'id', 'profile', 'qualification', 'source', 'collected_at', 'new_observations_collected_at',
               'built_on', 'reference_split', 'normalization', 'cells', 'models', 'calibration', 'validation'}
    if set(bank) != allowed:
        raise ValueError('Unsupported reference snapshot fields.')
    raw = canonical_bytes(bank)
    if len(raw) > MAX_BANK_BYTES:
        raise ValueError('Rolling reference snapshot exceeds 2 MiB.')
    if expected_sha is not None and (not isinstance(expected_sha, str) or not _SHA.fullmatch(expected_sha)
                                     or hashlib.sha256(raw).hexdigest() != expected_sha):
        raise ValueError('Reference snapshot digest mismatch.')
    if (not isinstance(bank.get('id'), str) or not _ID.fullmatch(bank['id'])
            or bank['id'] in {'legacy', _legacy_view()['id']}):
        raise ValueError('Invalid reference bank identifier.')
    if bank.get('qualification') != QUALIFICATION or bank.get('calibration') != {}:
        raise ValueError('Rolling references cannot inherit historical calibration gates.')
    if bank.get('cells') != core_cells():
        raise ValueError('Rolling references must retain the exact fixed core8-v1 prompts.')
    _dates(bank.get('collected_at'))
    _dates(bank.get('new_observations_collected_at'))
    _text(bank.get('built_on'), 'build date', 40)
    for name in ('source', 'reference_split', 'normalization', 'validation'):
        if name not in bank:
            raise ValueError('Incomplete reference snapshot metadata.')
    models = bank.get('models')
    if not isinstance(models, list) or not 2 <= len(models) <= MAX_MODELS:
        raise ValueError('Invalid reference model count.')
    known = set()
    baseline = _legacy_view()
    if bank['normalization'] != baseline['normalization']:
        raise ValueError('Rolling reference normalization must match the pinned protocol.')
    if bank['validation'] != {'identity_authenticated': False, 'open_set_calibrated': False,
                            'reference_labels': 'mixed author/operator declarations', 'heldout_accuracy': None}:
        raise ValueError('A reference snapshot cannot authenticate its own identity or calibration.')
    source = bank['source']
    if (not isinstance(source, dict) or set(source) != {'data', 'kind', 'legacy_bank', 'legacy_sha256', 'parent_bank', 'parent_sha256'}
            or source['data'] != 'https://github.com/Lee-Eee-Eee/API-MRI' or source['kind'] != 'mixed_declared_observations'
            or source['legacy_bank'] != baseline['id'] or source['legacy_sha256'] != _legacy_canonical_digest()
            or not isinstance(source['parent_bank'], str) or not _ID.fullmatch(source['parent_bank'])
            or not isinstance(source['parent_sha256'], str) or not _SHA.fullmatch(source['parent_sha256'])):
        raise ValueError('Invalid rolling reference source binding.')
    baseline_by_model = {row['model']: row for row in baseline['models']}
    for row in models:
        for field in ('model', 'label', 'family'):
            _text(row.get(field), field, 200)
        if row['model'] in known:
            raise ValueError('Duplicate reference model.')
        known.add(row['model'])
        _counts(row.get('counts'), CORE_CELL_IDS, copy_values=False,
                minimum=MIN_REFERENCE_REPETITIONS if row['model'] in baseline_by_model else 0)
        _dates(row.get('collected_at'))
        _provenance(row.get('provenance'))
        if row['model'] in baseline_by_model:
            original = baseline_by_model[row['model']]
            if row['counts'] != {identifier: original['counts'][identifier] for identifier in CORE_CELL_IDS}:
                raise ValueError('Historical labels cannot be silently rewritten in a rolling bank.')
            if row.get('label') != original['label'] or row.get('family') != original['family']:
                raise ValueError('Historical label metadata cannot be silently renamed.')
        else:
            _new_model(row)
    if not set(baseline_by_model) <= known:
        raise ValueError('Rolling bank must retain all historical reference labels.')
    if sum(all(sum(row['counts'][identifier].values()) >= MIN_REFERENCE_REPETITIONS for row in models)
           for identifier in CORE_CELL_IDS) < 6:
        raise ValueError('All reference candidates need at least six jointly comparable core probes.')
    return deepcopy(bank)


def _metadata(bank, sha, *, builtin=False):
    return {'id': bank['id'], 'label': '2026年7月 · 历史校准库' if builtin else bank['id'],
        'sha256': sha, 'profile': bank.get('profile', 'legacy40'),
        'collected_at': deepcopy(bank['collected_at']), 'model_count': len(bank['models']),
        'qualification': 'historical_calibration' if builtin else bank['qualification'], 'builtin': builtin,
        'core_cell_count': len(bank['cells']),
        'comparable_cell_count': sum(all(sum(row['counts'].get(cell['id'], {}).values()) >= MIN_REFERENCE_REPETITIONS
                                    for row in bank['models']) for cell in bank['cells']),
        'new_observations_collected_at': deepcopy(bank.get('new_observations_collected_at')),
        'models': [{key: deepcopy(row[key]) for key in ('model', 'label', 'family', 'providers', 'collected_at', 'provenance', 'aliases') if key in row}
                   for row in bank['models']]}


def _root(library_dir):
    return Path(library_dir) / 'identification'


def _safe_path(root, path):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Reference storage path escapes its library.')
    return path


def _catalog(library_dir):
    root = _root(library_dir)
    path = _safe_path(root, root / 'catalog.json')
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError('Invalid rolling reference catalog.')
    rows = json.loads(path.read_bytes())
    if not isinstance(rows, list) or len(rows) > MAX_BANKS:
        raise ValueError('Invalid rolling reference catalog.')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not _ID.fullmatch(row['id'])
                or row['id'] in seen or not isinstance(row.get('sha256'), str) or not _SHA.fullmatch(row['sha256'])):
            raise ValueError('Invalid rolling reference catalog entry.')
        seen.add(row['id'])
    return rows


def _bundled_catalog():
    path = PUBLIC_INDEX_PATH
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError('Invalid bundled public reference index.')
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or set(value) != {'schema', 'updated_at', 'banks'}
            or value.get('schema') != 'api-mri-public-reference-index/1'
            or not isinstance(value.get('updated_at'), str)):
        raise ValueError('Invalid bundled public reference index.')
    try:
        datetime.fromisoformat(value['updated_at'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid public reference index date.') from None
    rows = value.get('banks')
    if not isinstance(rows, list) or len(rows) > MAX_BANKS:
        raise ValueError('Invalid public reference index banks.')
    known = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id', 'sha256', 'filename'}
                or not isinstance(row['id'], str) or not _ID.fullmatch(row['id'])
                or row['id'] in known or row['id'] in {'legacy', _legacy_view()['id']}
                or not isinstance(row['sha256'], str) or not _SHA.fullmatch(row['sha256'])
                or not isinstance(row['filename'], str) or not row['filename'].endswith('.json')
                or not _ID.fullmatch(row['filename'][:-5])):
            raise ValueError('Invalid public reference index entry.')
        known.add(row['id'])
    return rows


def _entries(library_dir):
    entries = [(row, PUBLIC_INDEX_PATH.parent / row['filename']) for row in _bundled_catalog()]
    known = {row['id']: row['sha256'] for row, _ in entries}
    if library_dir is not None:
        for row in _catalog(library_dir):
            if row['id'] in known:
                if known[row['id']] != row['sha256']:
                    raise ValueError('A local reference cannot shadow an immutable bundled version.')
            else:
                known[row['id']] = row['sha256']
                entries.append((row, _root(library_dir) / 'banks' / (row['sha256'] + '.json')))
    return entries


def load_bank(bank_id=None, library_dir=None):
    base = _legacy_view()
    if bank_id in {None, 'legacy', base['id'], _legacy_raw_digest()}:
        return deepcopy(base)
    if not isinstance(bank_id, str):
        raise ValueError('Reference bank is unavailable.')
    found = next(((row, path) for row, path in _entries(library_dir) if row['id'] == bank_id or row['sha256'] == bank_id), None)
    if found is None:
        raise ValueError('Reference bank is unavailable.')
    row, path = found
    root = _root(library_dir) if library_dir is not None and path.parent == _root(library_dir) / 'banks' else PUBLIC_INDEX_PATH.parent
    path = _safe_path(root, path)
    stamp = _stamp(path)
    if path.is_symlink() or stamp[3] > MAX_BANK_BYTES:
        raise ValueError('Invalid immutable reference snapshot.')
    return deepcopy(_validated_file(stamp, _stamp(LEGACY_PATH), row['sha256'], row['id']))


@lru_cache(maxsize=8)
def _validated_file(stamp, legacy_stamp, expected_sha, expected_id):
    path = Path(stamp[0])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('Reference snapshot file digest mismatch.')
    bank = validate_bank_snapshot(json.loads(raw), expected_sha=expected_sha)
    if bank['id'] != expected_id or _stamp(path) != stamp or _stamp(LEGACY_PATH) != legacy_stamp:
        raise ValueError('Reference catalog identity mismatch.')
    return bank


def list_banks(library_dir=None):
    base = _legacy_view()
    rows = [_metadata(base, _legacy_raw_digest(), builtin=True)]
    for row, _ in _entries(library_dir):
        rows.append(_metadata(load_bank(row['id'], library_dir), row['sha256']))
    return rows


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(canonical_bytes(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_bank(bank, library_dir):
    checked = validate_bank_snapshot(bank)
    sha = bank_digest(checked)
    root = _root(library_dir)
    root.mkdir(parents=True, exist_ok=True)
    # Reuse the existing cross-process update lock; never publish a torn catalog.
    from .reference_library import _acquire_update_lock, _release_update_lock
    lock = _acquire_update_lock(root / '.publish.lock')
    if lock is None:
        raise RuntimeError('Another reference publication is running.')
    try:
        rows = _catalog(library_dir)
        existing = next((row for row, _ in _entries(library_dir) if row['id'] == checked['id']), None)
        if existing:
            if existing['sha256'] != sha:
                raise ValueError('Reference version is immutable; use a new bank identifier.')
            return _metadata(load_bank(checked['id'], library_dir), sha)
        if len(rows) >= MAX_BANKS:
            raise ValueError('Reference snapshot catalog is full; export old evidence before maintenance.')
        target = _safe_path(root, root / 'banks' / (sha + '.json'))
        target.parent.mkdir(exist_ok=True)
        raw = canonical_bytes(checked)
        if target.exists():
            if target.is_symlink() or target.read_bytes() != raw:
                raise ValueError('Immutable reference snapshot conflict.')
        else:
            with target.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        _atomic_json(root / 'catalog.json', rows + [{'id': checked['id'], 'sha256': sha}])
        return _metadata(checked, sha)
    finally:
        _release_update_lock(lock)


def active_bank_id(library_dir):
    path = None if library_dir is None else _safe_path(_root(library_dir), _root(library_dir) / 'active.json')
    if path is None or not path.exists():
        bundled = [load_bank(row['id']) for row in _bundled_catalog()]
        return max(bundled, key=lambda bank: (bank['new_observations_collected_at']['last'], bank['id']))['id'] if bundled else None
    if path.is_symlink() or path.stat().st_size > 1024:
        raise ValueError('Invalid active reference pointer.')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {'bank_id', 'sha256'}:
        raise ValueError('Invalid active reference pointer.')
    bank = load_bank(value['bank_id'], library_dir)
    expected = (_legacy_raw_digest() if bank['id'] == _legacy_view()['id'] else bank_digest(bank))
    if value['sha256'] != expected:
        raise ValueError('Active reference pointer digest mismatch.')
    return bank['id']


def activate_bank(bank_id, library_dir):
    bank = load_bank(bank_id, library_dir)
    builtin = bank['id'] == _legacy_view()['id']
    sha = _legacy_raw_digest() if builtin else bank_digest(bank)
    _atomic_json(_root(library_dir) / 'active.json', {'bank_id': bank['id'], 'sha256': sha})
    return _metadata(bank, sha, builtin=builtin)


def coverage(bank, claimed_model, aliases=None):
    """Only exact declared identities are matched; aliases remain unconfirmed."""
    wanted = claimed_model if isinstance(claimed_model, str) else ''
    models = bank['models']
    matched = next((row for row in models if row['model'] == wanted), None)
    alias_set = set(aliases or []) | {wanted}
    potential = [row for row in models if alias_set.intersection(row.get('aliases', [])) or row['label'] in alias_set]
    result = {'claimed_model': wanted, 'bank_id': bank['id'], 'qualification': bank.get('qualification', 'historical_calibration'),
              'match': 'exact' if matched else 'unconfirmed_alias' if potential else 'missing'}
    reference = matched or (potential[0] if len(potential) == 1 else None)
    if reference:
        counts = reference['counts']
        usable = [sum(v.values()) for v in counts.values() if sum(v.values()) >= MIN_REFERENCE_REPETITIONS]
        result.update(model_id=reference['model'], label=reference['label'], reference_cells=len(usable), core_cell_count=len(bank['cells']),
            min_repetitions=min(usable, default=0),
            collected_at=deepcopy(reference.get('collected_at', bank['collected_at'])),
            provenance=deepcopy(reference.get('provenance', {'kind': 'public_research_record', 'label': 'PAMELA author records'})))
    if not matched and potential:
        result['possible_model_ids'] = [row['model'] for row in potential]
    return result
