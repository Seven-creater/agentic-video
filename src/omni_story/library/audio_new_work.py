"""Read-only authorization for fresh audio work while excluding lost requests.

This receipt never resolves an unknown outcome or submits a request. Its human
authorization must be supplied by the caller from the actual user conversation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from .media import sha256_file
from .state import LibraryStopped, json_sha, scope_fingerprint

POLICY = 'audio_new_work_after_unknown_v1'


def _require(condition, reason):
    if not condition:
        raise LibraryStopped('audio_new_work:' + reason)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _binding(row):
    _require(isinstance(row, dict) and set(row) == {'path', 'sha256'}, 'binding_fields_required')
    _require(isinstance(row['path'], str) and Path(row['path']).is_absolute(), 'absolute_path_required')
    path = Path(row['path']).resolve(strict=True)
    _require(sha256_file(path) == row['sha256'], 'bound_file_changed')
    return path, dict(path=str(path), sha256=row['sha256'])


def _source(value):
    _require(isinstance(value, dict) and set(value) ==
             {'path', 'sha256', 'duration_s', 'video_stream_index'}, 'source_fields_required')
    _, binding = _binding({key: value[key] for key in ('path', 'sha256')})
    scope_fingerprint(dict(kind='complete_file', source_sha256=binding['sha256'],
                           source_start_s=0, source_end_s=value['duration_s']))
    _require(type(value['video_stream_index']) is int and value['video_stream_index'] >= 0,
             'source_stream_required')
    return dict(binding, duration_s=value['duration_s'], video_stream_index=value['video_stream_index'])


def _authorization(value):
    _require(isinstance(value, dict) and set(value) == {'source', 'date', 'summary'} and
             value['source'] == 'human_user', 'explicit_human_authorization_required')
    _require(isinstance(value['date'], str), 'authorization_date_required')
    try:
        valid_date = date.fromisoformat(value['date']).isoformat() == value['date']
    except ValueError:
        valid_date = False
    _require(valid_date, 'authorization_date_required')
    summary = value['summary']
    _require(isinstance(summary, str) and bool(summary.strip()) and len(summary) <= 1000,
             'sanitized_authorization_summary_required')
    _require(not re.search(r'(?i)bearer\s|sk-[a-z0-9]{8,}|(?:api[_ -]?key|auth[_ -]?token|secret)\s*[:=]|'
                           r'\b[a-f0-9]{32,}\b|\b[a-z0-9_-]{48,}\b', summary),
             'authorization_must_not_contain_credentials')
    return deepcopy(value)


def _unknown(path, saved, call):
    folder = path.parent / 'calls' / call['id']
    request_path = folder / 'request.json'
    request = _read(request_path)
    _require(json_sha(request) == call['request_sha256'], 'unknown_request_changed')
    _require(request.get('tool') in {'analyze_image', 'analyze_video'}, 'unknown_tool_required')
    args = request['arguments']
    argument = 'image_source' if request['tool'] == 'analyze_image' else 'video_source'
    _require(isinstance(args.get('prompt'), str) and bool(args['prompt'].strip()), 'unknown_prompt_required')
    media, media_binding = _binding(dict(path=args[argument], sha256=request['media_sha256']))
    scope = request.get('observation_scope')
    lineage_path = media.parent / 'lineage.json'
    lineage = None
    if lineage_path.is_file():
        value = _read(lineage_path)
        derived = {key: value[key] for key in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
        _require(value['sha256'] == request['media_sha256'] and
                 Path(value['path']).resolve() == media and
                 all(value.get('spec', {}).get(key, item) == item for key, item in derived.items()),
                 'unknown_lineage_changed')
        _require(scope is None or scope_fingerprint(scope) == scope_fingerprint(derived),
                 'unknown_scope_changed')
        scope = derived
        lineage = dict(path=str(lineage_path), sha256=sha256_file(lineage_path))
    scope_fingerprint(scope)
    pairs = []
    for journal in sorted(path.parent.glob('mcp_http*.jsonl')):
        entries = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines() if line.strip()]
        entries = [entry for entry in entries if entry.get('job_id') == call['id']]
        if not entries:
            continue
        _require(not any(entry.get('type') == 'response' for entry in entries),
                 'unknown_has_captured_response')
        requests = [entry for entry in entries if entry.get('type') == 'request']
        unknowns = [entry for entry in entries if entry.get('type') == 'unknown_result']
        _require(len(requests) == len(unknowns) == 1 and
                 type(requests[0].get('seq')) is int and requests[0]['seq'] > 0 and
                 requests[0]['seq'] == unknowns[0].get('seq'), 'unknown_http_evidence_required')
        original, lost = requests[0], unknowns[0]
        body = original.get('body', {})
        user_text = [row.get('text') for message in body.get('messages', [])
                     if message.get('role') == 'user' and isinstance(message.get('content'), list)
                     for row in message['content'] if isinstance(row, dict) and row.get('type') == 'text']
        _require(user_text == [args['prompt']], 'unknown_http_prompt_changed')
        _require(isinstance(original.get('hash'), str) and
                 re.fullmatch('[a-f0-9]{64}', original['hash']), 'unknown_native_body_hash_required')
        pairs.append(dict(journal_path=str(journal), journal_sha256=sha256_file(journal),
                          seq=original['seq'], native_body_sha256=original['hash'],
                          request_event_sha256=json_sha(original), unknown_event_sha256=json_sha(lost)))
    _require(len(pairs) == 1, 'unknown_http_evidence_required')
    return dict(call_id=call['id'], source_task_id=saved['task_id'], source_ledger_path=str(path),
        source_ledger_sha256=sha256_file(path), request_path=str(request_path),
        request_sha256=call['request_sha256'], request_file_sha256=sha256_file(request_path),
        tool=request['tool'], media_path=str(media), media_sha256=media_binding['sha256'],
        scope=deepcopy(scope), prompt_sha256=hashlib.sha256(args['prompt'].encode('utf-8')).hexdigest(),
        lineage=lineage, http_evidence=pairs[0])


def _priors(rows):
    _require(isinstance(rows, list) and bool(rows), 'all_prior_roots_required')
    roots = {}
    for row in rows:
        path, binding = _binding(row)
        roots[str(path)] = binding
    pending, seen, ledgers, exclusions, services = list(roots.values()), set(), [], [], []
    while pending:
        path, binding = _binding(pending.pop())
        if path in seen:
            continue
        seen.add(path)
        saved = _read(path)
        calls = saved['calls']
        _require(isinstance(calls, list) and all(isinstance(call, dict) and
                 isinstance(call.get('id'), str) and re.fullmatch('[A-Za-z0-9_-]+', call['id'])
                 for call in calls), 'safe_prior_call_id_required')
        _require(isinstance(calls, list) and type(saved['request_count']) is int and
                 saved['request_count'] == len(calls) and
                 len({call['id'] for call in calls}) == len(calls), 'prior_call_count_changed')
        _require(all(call.get('status') in {'received', 'failed_known', 'uncertain'} for call in calls),
                 'prior_pending_or_unrecognized_outcome')
        ledgers.append(dict(binding, task_id=saved['task_id'], requests=len(calls),
                            statuses=dict(Counter(call['status'] for call in calls))))
        pending.extend(saved['input_lock']['configuration'].get('prior_audio_evaluations', []))
        for call in calls:
            if call['status'] == 'uncertain':
                exclusions.append(_unknown(path, saved, call))
        for kind, pattern in [('tts', 'tts/*/state.json'), ('audiovisual_review', 'av_review_v*/state.json')]:
            for service_path in sorted(path.parent.glob(pattern)):
                service = _read(service_path)
                _require(service.get('status') in {'received', 'rejected'}, 'prior_audio_service_unknown')
                row = dict(path=str(service_path.resolve()), sha256=sha256_file(service_path),
                           status=service['status'], kind=kind)
                if kind == 'audiovisual_review' and service['status'] == 'received':
                    response = service_path.with_name('response.json')
                    _require(response.is_file() and sha256_file(response) == service.get('response_sha256'),
                             'prior_audio_service_response_changed')
                    row.update(response_path=str(response.resolve()), response_sha256=sha256_file(response))
                services.append(row)
    _require(bool(exclusions), 'prior_unknown_required')
    return (sorted(roots.values(), key=lambda row: row['path']),
            sorted(ledgers, key=lambda row: row['path']),
            sorted(services, key=lambda row: row['path']),
            sorted(exclusions, key=lambda row: (row['source_ledger_path'], row['call_id'])))


def build_receipt(video, reference, prior_audio_evaluations, authorization):
    """Derive a receipt from original files; the caller saves and SHA-pins it."""
    authorization = _authorization(authorization)
    video, reference = _source(video), _source(reference)
    roots, ledgers, services, exclusions = _priors(prior_audio_evaluations)
    _require(all(video['sha256'] not in {row['media_sha256'], row['scope']['source_sha256']}
                 for row in exclusions), 'fresh_source_required')
    return dict(policy=POLICY, authorization=authorization, fresh_source_required=True,
                no_subtitle_seed=True, video=video, reference=reference, prior_roots=roots,
                prior_ledgers=ledgers, audio_services=services, unknown_exclusions=exclusions)


def validate_receipt(record, *, video, reference, prior_audio_evaluations, subtitle_region_seed=None):
    """Require the exact receipt/source/history pins; never trust supplied exclusions."""
    _require(record is not None, 'explicit_new_work_receipt_required')
    _require(subtitle_region_seed is None, 'subtitle_seed_not_allowed')
    path, _ = _binding(record)
    saved = _read(path)
    _require(isinstance(saved, dict) and saved.get('policy') == POLICY,
             'receipt_policy_required')
    expected = build_receipt(video, reference, prior_audio_evaluations, saved.get('authorization'))
    _require(json_sha(saved) == json_sha(expected), 'receipt_sources_history_or_exclusions_changed')
    return expected


def validate_audio_new_work(state):
    """Return derived exclusions, or None so the caller keeps its default block."""
    configuration = state.input_lock['configuration']
    record = configuration.get('audio_new_work')
    if record is None:
        return None
    receipt = validate_receipt(record, video=state.input_lock['video'], reference=state.input_lock['reference'],
        prior_audio_evaluations=configuration.get('prior_audio_evaluations', []),
        subtitle_region_seed=configuration.get('subtitle_region_seed'))
    return receipt['unknown_exclusions']


def check_new_request(request, exclusions):
    """Exclude original lost bytes/request and overlapping original source scope."""
    _require(isinstance(request, dict) and isinstance(exclusions, list), 'request_and_exclusions_required')
    media_sha, scope = request.get('media_sha256'), request.get('observation_scope')
    _require(isinstance(media_sha, str) and re.fullmatch('[a-f0-9]{64}', media_sha), 'request_media_sha_required')
    scope_fingerprint(scope)
    fingerprint = json_sha(request)
    for old in exclusions:
        previous = old['scope']
        overlap = (scope['source_sha256'] == previous['source_sha256'] and
                   scope['source_start_s'] < previous['source_end_s'] and
                   previous['source_start_s'] < scope['source_end_s'])
        _require(media_sha != old['media_sha256'] and fingerprint != old['request_sha256'] and not overlap,
                 'unknown_request_must_not_be_replayed:' + old['call_id'])
