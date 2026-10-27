"""One terminal record owned by the omni-server worker, including early stops."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import sys
import traceback

from .contracts import json_safe
from .state import LibraryStopped, write_json


def stop_diagnostic(stage, error):
    frame = sys._getframe(1)
    return dict(stage=stage, error=error, exception_type='WorkflowStop',
                raised_at=f'{frame.f_code.co_filename}:{frame.f_lineno}')


def _read(path):
    value = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    if not isinstance(value, dict):
        raise ValueError('run_outcome_invalid_record:' + str(path))
    return value


def _ledgers(output):
    return [path for path in (output/'library_state.json',
        output/'skill_finecut/creative_audio/library_state.json') if path.is_file()]


def partial_after_exhaustion(output, error):
    """Reuse authenticated failed-call records and existing renders; never call a model."""
    prefix = 'model_protocol_repair_exhausted:'
    if not str(error).startswith(prefix):
        return None
    from .finecut_continuation import _known_protocol_failure
    from .media import sha256_file
    output = Path(output)
    stage = str(error)[len(prefix):]
    ledger = next((path for path in _ledgers(output)
        if any(call['name'] == stage for call in _read(path).get('calls', []))), None)
    if ledger is None:
        return None
    state = SimpleNamespace(output=ledger.parent, data=_read(ledger))
    failure = _known_protocol_failure(state, stage, error)
    # Prefer a verified audio/subtitled candidate, then the latest fine render, then rough.
    candidates = []
    for base in (output, output/'skill_finecut'):
        for path in base.glob('render_*/render_result.json'):
            value = _read(path)
            video = Path(value.get('rendered_path', ''))
            if video.is_file() and sha256_file(video) == value.get('sha256'):
                candidates.append((int(base != output), int(path.parent.name.split('_')[-1]), value))
    for priority, path, path_key, sha_key in (
            (2, output/'skill_finecut/creative_audio/audio_result.json', 'final_video', 'final_sha256'),
            (3, output/'skill_finecut/delivery/mux.json', None, 'sha256'),
            (4, output/'final_subtitled.mp4.receipt.json', 'path', 'sha256')):
        if not path.is_file():
            continue
        value = _read(path)
        video = Path(value.get(path_key, '') if path_key else path.parent/'story_finecut.mp4')
        if video.is_file() and sha256_file(video) == value.get(sha_key):
            candidates.append((priority, 0, dict(rendered_path=str(video), sha256=value[sha_key])))
    if not candidates:
        return None
    selected = max(candidates, key=lambda item: item[:2])[2]
    result = dict(status='completed_partial', partial=True, partial_reasons=[str(error)],
        final_video=selected['rendered_path'], final_sha256=selected['sha256'],
        joint_quality_gate=False, protocol_failure=failure)
    write_json(output/'partial_delivery.json', json_safe(result))
    return result


def write_run_outcome(output, result=None, error=None):
    output = Path(output).resolve()
    result = result or {}
    fine = result.get('finecut', {})
    audio = result.get('creative_audio') or fine.get('creative_audio') or {}
    measurement = result.get('audio_measurement') or audio.get('audio_measurement')
    status = result.get('status', 'stopped')
    partial = status == 'completed_partial'
    delivered = error is None and not partial and (result.get('joint_quality_gate') is True or (
        result.get('status') == 'rough_audio_ready' and result.get('content_delivery_ready') is True
        and bool(result.get('final_video'))))
    outcome = 'delivered' if delivered else 'partial' if partial and error is None else 'stopped'
    record = dict(outcome=outcome, status=status, final_video=result.get('final_video') if outcome != 'stopped' else None,
        partial_reasons=result.get('partial_reasons', []))
    if measurement is None:
        receipts = [*output.glob('rough_audio/preview.mp4.receipt.json'),
                    *sorted(output.glob('skill_finecut/creative_audio/candidate_v*.mp4.receipt.json'))]
        if receipts:
            try:
                measurement = _read(receipts[-1]).get('audio_measurement')
            except (OSError, ValueError, TypeError) as read_error:
                record.setdefault('diagnostic_read_errors', []).append(dict(path=str(receipts[-1]), error=str(read_error)))
    if measurement:
        record['audio_measurement'] = measurement
    usage = result.get('usage') or result.get('vision_usage')
    if usage:
        record['model_calls'] = usage.get('requests')
        if 'cost_estimate' in usage:
            record['cost_estimate'] = usage['cost_estimate']
    calls = []
    for ledger in _ledgers(output):
        try:
            saved = _read(ledger)
            for call in saved.get('calls', []):
                calls.append((call, ledger.parent))
        except (OSError, ValueError, TypeError) as read_error:
            record.setdefault('diagnostic_read_errors', []).append(dict(path=str(ledger), error=str(read_error)))
    if calls:
        record['model_calls'] = len(calls)
        record['repairs'] = sum(bool(call.get('repair_of')) for call, _ in calls)
    last, last_root = max(calls, key=lambda row: row[0].get('submitted_at', ''), default=({}, output))
    if outcome == 'stopped':
        raised_at = None
        diagnostic = result.get('stop_diagnostic') or fine.get('stop_diagnostic') or audio.get('stop_diagnostic') or {}
        frame_stage = None
        if error is not None:
            frames = traceback.extract_tb(error.__traceback__)
            inside = [frame for frame in frames if 'omni_story' in Path(frame.filename).parts]
            if inside:
                raised_at = f'{inside[-1].filename}:{inside[-1].lineno}'
                frame_stage = Path(inside[-1].filename).stem + '.' + inside[-1].name
        failure_path = last_root/'calls'/last.get('id', '')/'protocol_failure.json'
        codes = audio.get('reason_codes') or (measurement or {}).get('reasons') or []
        record.update(stage=(str(error).split(':', 1)[1] if error is not None and
            str(error).startswith('model_protocol_repair_exhausted:') else
            diagnostic.get('stage') or frame_stage or audio.get('status') or last.get('name') or status),
            error=str(error) if error is not None else diagnostic.get('error') or ';'.join(codes) or status,
            exception_type=type(error).__name__ if error is not None else 'WorkflowStop',
            raised_at=raised_at or diagnostic.get('raised_at'), last_call_id=last.get('id'),
            protocol_failure=str(failure_path) if failure_path.is_file() else None)
    if outcome == 'stopped':
        record['error_code'] = (str(error).split(':', 1)[0] if isinstance(error, (ValueError, LibraryStopped)) else
            type(error).__name__ if error is not None else record['error'].split(';', 1)[0])
    write_json(output/'run_outcome.json', json_safe(record))
    return record
