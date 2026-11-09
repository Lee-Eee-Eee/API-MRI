"""Re-rank validated observations against another immutable reference, offline."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from . import identification as ident
from . import identification_library as library
from .records import content_hash

SCHEMA = 'api-mri-identification-recompare/1'
MAX_BYTES = 4 * 1024 * 1024
_FIELDS = {'schema', 'source_records', 'source_records_sha256', 'target_bank_snapshot',
           'target_bank_sha256', 'record_hash'}


def _source_text(records):
    return '\n'.join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in records)


def _source_digest(records):
    return hashlib.sha256(library.canonical_bytes(records)).hexdigest()


def _validate_target(bank, expected_sha):
    if not isinstance(bank, dict) or library.bank_digest(bank) != expected_sha:
        raise ValueError('重排目标参考摘要不一致。')
    if bank.get('profile') == library.PROFILE:
        return library.validate_bank_snapshot(bank, expected_sha=expected_sha)
    # A historical snapshot is allowed only if it is exactly the pinned bank.
    pinned = ident.load_identification_bank()
    if library.canonical_bytes(bank) != library.canonical_bytes(pinned):
        raise ValueError('重排目标不属于支持的固定参考协议。')
    return deepcopy(bank)


def parse_recomparison(text):
    if not isinstance(text, str) or len(text.encode('utf8')) > MAX_BYTES:
        raise ValueError('离线重排记录不能超过4 MiB。')
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict) or set(value) != _FIELDS or value['schema'] != SCHEMA:
            raise ValueError
        expected = content_hash({key: item for key, item in value.items() if key != 'record_hash'})
        if value['record_hash'] != expected:
            raise ValueError
        records = value['source_records']
        if not isinstance(records, list) or not 2 <= len(records) <= 34:
            raise ValueError
        if value['source_records_sha256'] != _source_digest(records):
            raise ValueError
        # This verifies the original protocol, reference hash and every link.
        ident.parse_identification_records(_source_text(records))
        _validate_target(value['target_bank_snapshot'], value['target_bank_sha256'])
        return value
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, IndexError):
        raise ValueError('离线重排的原记录链、目标参考或摘要不一致。') from None


def _compatible_request(row, cell, source_cell):
    if source_cell is None or any(source_cell.get(field) != cell.get(field)
                                  for field in ('lang', 'normalize_as', 'answer_space')):
        return False
    body = row.get('request')
    if not isinstance(body, dict):
        return False
    temperature = body.get('temperature')
    return (body.get('messages') == [{'role': 'system', 'content': cell['system']},
                                      {'role': 'user', 'content': cell['user']}]
            and type(temperature) in {int, float} and temperature == 1
            and type(body.get('max_tokens')) is int and body['max_tokens'] == ident.TOKEN_CAP
            and isinstance(body.get('reasoning'), dict) and set(body['reasoning']) == {'enabled'}
            and body['reasoning']['enabled'] is False)


def _report(envelope):
    source = envelope['source_records']
    source_bank = ident._manifest_bank(source[0])
    target = _validate_target(envelope['target_bank_snapshot'], envelope['target_bank_sha256'])
    source_rows = [row for row in source if row['type'] == 'request']
    source_cells = {cell['id']: cell for cell in source_bank['cells']}
    # Every ranked candidate is compared on the exact same complete probe set.
    target_cells = ident._common_cells(target, target['models'])
    by_messages = {(cell['system'], cell['user']): cell for cell in target_cells}
    groups, valid_rows = {}, []
    for row in source_rows:
        messages = row.get('request', {}).get('messages')
        if not isinstance(messages, list) or len(messages) != 2:
            continue
        cell = by_messages.get((messages[0].get('content'), messages[1].get('content')))
        if cell is None or not _compatible_request(row, cell, source_cells.get(row['cell_id'])):
            continue
        _, status = ident._sample(row, cell)
        if status != 'valid':
            continue
        groups.setdefault(cell['id'], []).append(row)
        valid_rows.append((row, cell['id']))
    complete = {identifier for identifier, rows in groups.items() if len(rows) == ident.BLOCK}
    # Preserve the original acquisition order in the explanatory trajectory.
    # The detached view is never serialized as another model capture.
    reusable = [{**row, 'cell_id': identifier} for row, identifier in valid_rows if identifier in complete]
    target_view = deepcopy(target)
    target_view['calibration'] = {}
    config = deepcopy(source[0]['config'])
    config['candidate_models'] = sorted(row['model'] for row in target['models'])
    report_records = [{'type': 'manifest', 'config': config, 'synthetic': source[0]['synthetic']},
                      *reusable, {'type': 'finish', 'state': 'complete', 'failure': None,
                                  'stop_reason': 'offline_recomparison'}]
    report = ident._report(target_view, config, report_records)
    original = ident._report(source_bank, source[0]['config'], source)
    completed = report['coverage']['completed_cells']
    report['status'] = 'reference_shortlist' if completed >= ident.MIN_CELLS and report['ranking'] else 'insufficient_evidence'
    report['identity_decision'] = 'abstained'
    report['probability'] = None
    report['reused_cost'] = original['cost']
    report['cost'] = {'request_count': 0, 'real_requests': 0, 'requested_output_tokens': 0,
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
                  'reported_requests': 0, 'missing_requests': 0, 'complete': True,
                  'source': 'offline_recomparison', 'synthetic': source[0]['synthetic']},
        'note': '本次重排仅复用已保存回答；原始调用成本另存于reused_cost。'}
    report['budget'] = {'max_requests': 0, 'max_output_tokens': 0}
    report['stopping'] = {'policy': 'offline_recomparison', 'early_stopped': False,
        'sequential_experimental': False, 'fixed_horizon_metrics_apply_to_policy': False,
        'unused_request_budget': 0, 'unused_output_token_cap': 0}
    report['calibration'].update(method='descriptive_offline_recomparison', applicable_gate=None,
        ranking_gap=None, inside_distance_envelope=None, open_set_gate_passed=None,
        scope='offline_recomparison_without_identity_calibration',
        interpretation='使用同协议的已采回答重排；没有套用历史开放集门槛。')
    report['validation'].update(fixed_budget_evaluation='not_applicable_offline_recomparison',
        stop_policy_evaluation='not_applicable_offline_recomparison', live_accuracy_evaluated=False,
        source_stratum_audit=None)
    report['stop_reason'] = 'offline_recomparison'
    report['reanalysis'] = {'source_bank_id': source_bank['id'], 'source_bank_sha256': source[0]['bank_sha256'],
        'target_bank_id': target['id'], 'target_bank_sha256': envelope['target_bank_sha256'],
        'source_records_sha256': envelope['source_records_sha256'], 'source_requests': len(source_rows),
        'reused_requests': len(reusable), 'reused_cells': completed,
        'skipped_requests': len(source_rows) - len(reusable), 'model_api_calls': 0,
        'reused_cost_scope': 'entire_original_source_run'}
    report['notes'] = ['使用已保存、已验证的原始采样回答；本次模型调用为0。',
        '所有候选使用同一组完整4条有效回答的单元；不兼容或不足4条的单元未加入排名。',
        '原调用链与原参考摘要保留在重排封装中，未把旧调用记作本次采样。',
        '本次排名为描述性比较；目标参考的历史校准门槛没有参与判定。']
    return report


def _write(envelope, out):
    text = library.canonical_bytes(envelope).decode('utf8') + '\n'
    if len(text.encode('utf8')) > MAX_BYTES:
        raise ValueError('离线重排记录不能超过4 MiB。')
    report = _report(envelope)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'identification_records.jsonl').write_text(text, encoding='utf8')
    ident._write_report(directory, report)
    return report


def recompare_identification(source_text, bank_id, out, *, library_dir=None):
    source = ident.parse_identification_records(source_text, library_dir=library_dir)
    bank = library.load_bank(bank_id, library_dir)
    envelope = {'schema': SCHEMA, 'source_records': source, 'source_records_sha256': _source_digest(source),
                'target_bank_snapshot': bank, 'target_bank_sha256': library.bank_digest(bank)}
    envelope['record_hash'] = content_hash(envelope)
    # Validate the same representation that another computer will replay.
    checked = parse_recomparison(library.canonical_bytes(envelope).decode('utf8'))
    return _write(checked, out)


def replay_recomparison(envelope, out):
    if isinstance(envelope, Path):
        text = envelope.read_text(encoding='utf8')
    elif isinstance(envelope, str):
        text = envelope
    elif isinstance(envelope, dict):
        text = library.canonical_bytes(envelope).decode('utf8')
    else:
        raise ValueError('需要有效的离线重排封装。')
    return _write(parse_recomparison(text), out)
