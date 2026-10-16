"""Report creative-audio child usage without merging independent request ledgers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .media import sha256_file
from .state import LibraryStopped


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _binding(path):
    return dict(ledger_path=str(path.resolve()), ledger_sha256=sha256_file(path))


def _add_usage(total, value):
    # Provider usage may include nested token details; never copy text or config.
    for key, number in value.items():
        if isinstance(number, dict):
            _add_usage(total.setdefault(key, {}), number)
        elif type(number) in (int, float) and math.isfinite(number) and number >= 0:
            total[key] = total.get(key, 0) + number


def _service_usage(paths, *, audiovisual=False):
    records, statuses, captured = [], {}, {}
    for path in paths:
        saved = _read(path)
        status = saved['status']
        if status not in {'submitted', 'uncertain', 'received', 'rejected'}:
            raise LibraryStopped('creative_audio:invalid_service_usage_status')
        if audiovisual:
            if saved.get('policy') != 'actual-audio-video-review-v1':
                raise LibraryStopped('creative_audio:invalid_av_usage_ledger')
        elif saved.get('schema_version') != 'tts_line_v1' or saved.get('request_count') != 1:
            raise LibraryStopped('creative_audio:invalid_tts_usage_ledger')
        record = dict(_binding(path), status=status, request_sha256=saved['request_sha256'])
        usage = saved.get('usage', {})
        if audiovisual and status == 'received':
            response_path = path.with_name('response.json')
            if (not response_path.is_file() or
                    sha256_file(response_path) != saved.get('response_sha256')):
                raise LibraryStopped('creative_audio:av_usage_response_changed')
            response = _read(response_path)
            body_text = response['body_text']
            if hashlib.sha256(body_text.encode('utf-8')).hexdigest() != saved.get('body_sha256'):
                raise LibraryStopped('creative_audio:av_usage_body_changed')
            try:
                body = json.loads(body_text)
            except ValueError:
                body = {}
            usage = body.get('usage', {}) if isinstance(body, dict) else {}
            record.update(response_path=str(response_path.resolve()),
                          response_sha256=saved['response_sha256'])
        record['usage'] = {}
        if isinstance(usage, dict):
            _add_usage(record['usage'], usage)
            _add_usage(captured, usage)
        records.append(record)
        statuses[status] = statuses.get(status, 0) + 1
    return dict(requests=len(records), statuses=statuses, captured_usage=captured,
        usage_scope='captured_responses_only_unknown_charges_not_zero_not_vision_or_account_quota',
        uncertain_calls=[row['ledger_path'] for row in records
                         if row['status'] in {'submitted', 'uncertain'}], ledgers=records)


def _vision_usage(path):
    saved = _read(path)
    calls, count = saved['calls'], saved['request_count']
    if type(count) is not int or count != len(calls):
        raise LibraryStopped('creative_audio:vision_call_count_changed')
    return dict(_binding(path), task_id=saved['task_id'], requests=count,
        max_requests=saved.get('max_requests'),
        token_usage_scope='captured_responses_only_unknown_charges_not_zero',
        prompt_tokens=sum(row.get('usage', {}).get('prompt_tokens', 0) or 0 for row in calls),
        completion_tokens=sum(row.get('usage', {}).get('completion_tokens', 0) or 0 for row in calls),
        uncertain_calls=[row['id'] for row in calls if row['status'] in {'submitted', 'uncertain'}]), saved


def task_usage(state, creative_audio_root=None):
    """Sum task vision submissions; retain TTS and AV service usage separately.

    The parent's state/call IDs stay unchanged. Reading snapshots counts every
    child submission once, including failed/unknown outcomes and reused audio.
    """
    parent = state.usage()
    standalone = creative_audio_root is None
    root = Path(state.output if standalone else creative_audio_root)
    vision_path = root / 'library_state.json'
    configuration = getattr(state, 'input_lock', {}).get('configuration', {})
    prior_rows = list(configuration.get('prior_audio_evaluations', []))
    child = None
    input_lock = getattr(state, 'input_lock', {})
    cache_dirs = {Path(input_lock['tts_cache_dir']).resolve()} if input_lock.get('tts_cache_dir') else set()
    if not standalone and vision_path.is_file():
        child, child_saved = _vision_usage(vision_path)
        prior_rows.extend(child_saved['input_lock']['configuration'].get('prior_audio_evaluations', []))
        if child_saved['input_lock'].get('tts_cache_dir'):
            cache_dirs.add(Path(child_saved['input_lock']['tts_cache_dir']).resolve())
    prior, seen = [], {Path(state.output).resolve() / 'library_state.json', vision_path.resolve()}
    for row in prior_rows:
        path = Path(row['path']).resolve(strict=True)
        if sha256_file(path) != row['sha256']:
            raise LibraryStopped('creative_audio:prior_evaluation_ledger_changed')
        if path in seen:
            continue
        seen.add(path)
        usage, saved = _vision_usage(path)
        prior.append(usage)
        if saved['input_lock'].get('tts_cache_dir'):
            cache_dirs.add(Path(saved['input_lock']['tts_cache_dir']).resolve())
        prior_rows.extend(saved['input_lock']['configuration'].get('prior_audio_evaluations', []))
    roots = [root, Path(state.output) / 'rough_audio', *(Path(row['ledger_path']).parent for row in prior)]
    tts_paths = sorted({path.resolve() for folder in roots for path in folder.glob('tts/*/state.json')} |
                       {path.resolve() for folder in cache_dirs for path in folder.glob('*/state.json')})
    av_paths = sorted({path for folder in roots for path in folder.glob('av_review_v*/state.json')})
    if not standalone and child is None and not tts_paths and not av_paths and not prior:
        return parent
    result = deepcopy(parent)
    result['vision_usage_scope'] = 'task_vision_ledgers_only_not_account_quota'
    result['vision_ledgers'] = dict(parent=deepcopy(parent))
    if child is not None:
        result['vision_ledgers']['creative_audio'] = child
        result['requests'] += child['requests']
        for key in ('prompt_tokens', 'completion_tokens'):
            result[key] = result.get(key, 0) + child[key]
        result['uncertain_calls'] = [*result.get('uncertain_calls', []),
            *('creative_audio/' + call_id for call_id in child['uncertain_calls'])]
        # A parent per-ledger limit must not be mistaken for a shared task cap.
        result['max_requests'] = None
        result['max_requests_scope'] = 'independent_vision_ledger_limits_only'
    if prior:
        result['prior_audio_evaluations'] = prior
        result['prior_requests'] = sum(row['requests'] for row in prior)
        result['cumulative_requests'] = result['requests'] + result['prior_requests']
        result['prior_uncertain_calls'] = [dict(ledger_path=row['ledger_path'], call_id=call_id)
            for row in prior for call_id in row['uncertain_calls']]
        result['prior_token_usage'] = {key:sum(row[key] for row in prior)
                                       for key in ('prompt_tokens', 'completion_tokens')}
    result['audio_services'] = dict(tts=_service_usage(tts_paths),
                                    qwen_av=_service_usage(av_paths, audiovisual=True))
    return result


def usage_bindings(usage):
    """Files that a completed result must bind to keep its usage reproducible."""
    bindings = {}
    records = [*usage.get('vision_ledgers', {}).values(), *usage.get('prior_audio_evaluations', [])]
    for service in usage.get('audio_services', {}).values():
        records.extend(service['ledgers'])
    for record in records:
        if 'ledger_path' in record:
            bindings[record['ledger_path']] = record['ledger_sha256']
        if 'response_path' in record:
            bindings[record['response_path']] = record['response_sha256']
    return bindings
