"""One explicitly authorized original-method trial linked to immutable old ledgers.

The lane is new execution, not a retry of the failed 147-second chain. Historical
requests remain charged and unknown media remain excluded from every new POST.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re

from .contracts import parse_model_json
from .media import sha256_file
from .state import LibraryStopped, json_sha, write_json

NAME = 'restored_original_method_v1'
POLICY = 'explicit_original_rough_method_restoration_v1'
BASELINE_CALLS = 36
HISTORICAL_CALLS = 274
REPAIR_MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
LIMITS = {'rough_candidates': 2, 'fine_windows': 16, 'fine_renders': 2,
          'fine_actual_revisions': 1, 'repairs_per_stage': 1}
_ROUGH = r'(?:coarse_zoom_search|overview_[a-f0-9]{8}|(?:zoom|fine)_[a-f0-9]{16}|(?:search|plan|blind|economy|review)_[01]|select_render|selected_review_v2_[01])'
_FINE = r'chain_e2e_v1_fine_(?:observe|inspect_[0-3]|detail_[0-3]_[0-5]|plan|blind(?:_r)?|review(?:_r)?|revise)'


def allowed_stage(name):
    return isinstance(name, str) and re.fullmatch(f'(?:{_ROUGH}|{_FINE})(?:_repair)?', name) is not None


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _require(ok, reason):
    if not ok:
        raise LibraryStopped('restoration_policy_' + reason)


def _bound(path):
    path = Path(path).resolve(strict=True)
    _require(path.is_file(), 'bound_file_required')
    return {'path': str(path), 'sha256': sha256_file(path)}


def _verify_file(item):
    path = Path(item['path'])
    _require(path.is_absolute() and path.resolve(strict=True) == path and
             sha256_file(path) == item['sha256'], 'bound_history_changed')


def _text(reply):
    return '\n'.join(row['text'] for row in reply['result']['content'] if row.get('type') == 'text')


def _baseline(parent):
    from . import server_jobs
    state = _read(parent / 'library_state.json')
    calls = state['calls']
    _require(state['request_count'] == len(calls) == BASELINE_CALLS and
             all(row['status'] == 'received' for row in calls[:-1]) and
             calls[-1]['status'] == 'failed_known' and
             calls[-1]['name'] == 'chain_e2e_v1_rough_blind' and
             calls[-1].get('error') == 'opencode_no_original_vision_reply',
             'requires_original_36_terminal')
    queue_reply = parent / 'mcp_queue' / (calls[-1]['id'] + '.response.json')
    failed_record = parent / 'calls' / calls[-1]['id'] / 'failure.json'
    native = _read(queue_reply)
    failure = _read(failed_record)
    _require(native.get('status') == 'error' and
             'server_chain_authorization_old_file_changed' in _text(native) and
             failure == {'error': calls[-1]['error'], 'uncertain': False}, 'known_prePOST_failure_changed')
    job_path = parent / '.omni-server/recoveries/one_chain_e2e_v1/job.json'
    job = _read(job_path)
    _require(job['state'] == 'failed' and job['exit_code'] == 1 and
             not server_jobs._identity(job.get('supervisor_pid', 0)) and
             not server_jobs._group_running(job.get('child_pid', 0)), 'previous_job_not_stopped')
    # Stream once at registration; never parse the large historical journal on
    # each model request. Frozen journal bytes prove this check remains valid.
    journal = parent / 'mcp_http.jsonl'
    if journal.exists():
        with journal.open('rb') as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    _require(row.get('job_id') != calls[-1]['id'], 'old_36_has_HTTP_event')
    files = [parent / 'library_state.json', job_path,
             parent / '.omni-server/recoveries/one_chain_e2e_v1/run.lock',
             parent / 'failure_one_chain_e2e_v1.json', queue_reply, failed_record]
    if journal.exists():
        files.append(journal)
    for call in calls:
        directory = parent / 'calls' / call['id']
        request = _read(directory / 'request.json')
        _require(json_sha(request) == call['request_sha256'], 'old_request_changed')
        files.append(directory / 'request.json')
        if call['status'] == 'received':
            response = _read(directory / 'response.json')
            _require(json_sha(response) == call['response_sha256'], 'old_response_changed')
            files.append(directory / 'response.json')
    return state, [_bound(path) for path in files]


def register(parent_output, lane, user_instruction, reference_seed, *, history, method_provenance=None):
    """Write one new-trial proof without modifying the parent state or old calls."""
    parent = Path(parent_output).resolve(strict=True)
    lane = Path(lane).resolve()
    _require(lane == parent / 'artifacts' / NAME and not lane.exists(), 'lane_already_exists_or_outside_parent')
    _require(isinstance(user_instruction, str) and bool(user_instruction.strip()), 'explicit_instruction_required')
    baseline, files = _baseline(parent)
    reference_catalog_path = parent / 'reference_catalog/inventory.json'
    reference = _read(reference_catalog_path)['sources'][0]
    _require(reference['sha256'] == baseline['input_lock']['reference_sha256'] and
             type(reference['duration_s']) in (int, float) and math.isfinite(reference['duration_s']) and
             reference['duration_s'] > 0, 'actual_reference_catalog_changed')
    files.append(_bound(reference_catalog_path))
    historical = history['historical_calls']
    _require(history['baseline_requests'] == len(historical) == HISTORICAL_CALLS and
             json_sha(historical) == history['baseline_calls_sha256'], 'historical_274_changed')
    unknown = history['unknown_inputs']
    _require([row['call_id'] for row in unknown] ==
             [row['id'] for row in historical if row['status'] == 'uncertain'], 'unknown_exclusions_changed')
    seed = deepcopy(reference_seed)
    first = next((row for row in historical if row['id'] == 'glm_001_reference'), None)
    request, reply = seed['seed_request'], seed['seed_response']
    _require(first is not None and first['status'] == 'received' and seed['source_call_id'] == first['id'] and
             seed['request_sha256'] == first['request_sha256'] == json_sha(request) and
             seed['response_sha256'] == first['response_sha256'] == json_sha(reply) and
             seed['full_response'] == {'reference': parse_model_json(_text(reply))} and
             seed['reference_sha256'] == baseline['input_lock']['reference_sha256'] ==
             seed['full_response']['reference']['reference_sha256'], 'original_001_seed_changed')
    proof = {'policy': POLICY, 'name': NAME, 'lane': str(lane), 'parent_output': str(parent),
        'user_instruction': user_instruction, 'parent_task_id': baseline['task_id'],
        'parent_requests': BASELINE_CALLS, 'historical_requests': HISTORICAL_CALLS,
        'historical_calls_sha256': history['baseline_calls_sha256'], 'protected_files': files,
        'reference_seed': seed, 'reference_seed_sha256': json_sha(seed), 'unknown_inputs': deepcopy(unknown),
        'reference_duration_s': reference['duration_s'],
        'unknown_inputs_sha256': json_sha(unknown), 'method_provenance': deepcopy(method_provenance or {}),
        'limits': dict(LIMITS), 'goal_resumed': False, 'old_ledgers_unchanged': True,
        'new_autonomous_decisions_not_teacher_EDL': True, 'whole_reference_POST_forbidden': True,
        'billing_policy': 'historical_274_plus_parent_36_plus_restoration_lane_requests'}
    lane.mkdir(parents=True)
    path = lane / 'authorization.json'
    write_json(path, proof)
    return path


def configuration(path):
    """Input-lock markers make the separately authorized trial explicit."""
    path = Path(path).resolve(strict=True)
    return {'restoration_policy': NAME, 'restoration_authorization_path': str(path),
            'restoration_authorization_sha256': sha256_file(path)}


def _request_allowed(proof, name, request):
    _require(allowed_stage(name), 'stage_not_permitted')
    scope = request.get('observation_scope') or {}
    reference = proof['reference_seed']['reference_sha256']
    _require(request.get('media_sha256') != reference and
             not (scope.get('source_sha256') == reference and
                  (scope.get('kind') == 'complete_file' or
                   type(scope.get('source_start_s')) in (int, float) and scope['source_start_s'] == 0 and
                   type(scope.get('source_end_s')) in (int, float) and
                   scope['source_end_s'] >= proof['reference_duration_s'] - .001)),
             'whole_reference_POST_forbidden')
    if scope.get('source_sha256') == reference:
        start, end = scope.get('source_start_s'), scope.get('source_end_s')
        _require(scope.get('kind') == 'continuous_window' and type(start) in (int, float) and
                 type(end) in (int, float) and math.isfinite(start) and math.isfinite(end) and
                 0 <= start < end <= proof['reference_duration_s'] and end - start <= 6,
                 'reference_local_scope_invalid')
    for old in proof['unknown_inputs']:
        prior = old.get('scope') or {}
        same_scope = (scope.get('source_sha256') is not None and
            scope.get('source_sha256') == prior.get('source_sha256') and
            scope.get('source_start_s') == prior.get('source_start_s') and
            scope.get('source_end_s') == prior.get('source_end_s'))
        _require(request.get('media_sha256') != old['media_sha256'] and
                 json_sha(request) != old.get('request_sha256') and not same_scope,
                 'historical_unknown_observation_no_replay:' + old['call_id'])


def _handoff(lane, state):
    entries = state.get('artifacts', {}).get('restored_rough_handoff', [])
    _require(len(entries) == 1, 'actual_rough_handoff_required')
    item = entries[0]
    path = Path(item['path']).resolve(strict=True)
    _require(path.is_relative_to(lane) and json_sha(_read(path)) == item['sha256'], 'rough_handoff_changed')
    binding = _read(path)
    handoff_path = Path(binding['path']).resolve(strict=True)
    _require(handoff_path.is_relative_to(lane) and json_sha(_read(handoff_path)) == binding['sha256'],
             'rough_handoff_changed')
    handoff = _read(handoff_path)
    call = next((row for row in state['calls'] if row['id'] == handoff['rough_review_call_id']), None)
    _require(handoff['selected_round'] in (0, 1) and call is not None and call['status'] == 'received' and
             call['name'].removesuffix('_repair') in {'review_0', 'review_1', 'selected_review_v2_0', 'selected_review_v2_1'} and
             (lane / 'calls' / call['id'] / 'parsed.json').is_file(), 'actual_rough_review_required')
    video = Path(handoff['rough_video_path']).resolve(strict=True)
    _require(video.is_relative_to(lane) and sha256_file(video) == handoff['rough_source_sha256'], 'rough_video_changed')


def load(lane):
    """Read-only check before every new stage; old ledgers are never reconciled."""
    lane = Path(lane).resolve(strict=True)
    state_path = lane / 'library_state.json'
    if state_path.exists():
        state = _read(state_path)
    else:
        # Registration precedes inventory and LibraryState creation. Validate
        # the existing grant at bootstrap without writing or inventing a ledger.
        state = {'input_lock': {'configuration': configuration(lane / 'authorization.json')},
                 'max_requests': None, 'request_count': 0, 'calls': [], 'artifacts': {}}
    config = state['input_lock']['configuration']
    if config.get('restoration_policy') is None:
        return None
    _require(config['restoration_policy'] == NAME and state['max_requests'] is None, 'configuration_changed')
    path = Path(config['restoration_authorization_path'])
    _require(path == lane / 'authorization.json' and sha256_file(path) ==
             config['restoration_authorization_sha256'], 'authorization_changed')
    proof = _read(path)
    _require(proof['policy'] == POLICY and proof['name'] == NAME and proof['lane'] == str(lane) and
             lane == Path(proof['parent_output']) / 'artifacts' / NAME and proof['limits'] == LIMITS and
             proof['parent_requests'] == BASELINE_CALLS and proof['historical_requests'] == HISTORICAL_CALLS and
             not proof['goal_resumed'] and proof['old_ledgers_unchanged'] and
             proof['new_autonomous_decisions_not_teacher_EDL'] and proof['whole_reference_POST_forbidden'] and
             json_sha(proof['reference_seed']) == proof['reference_seed_sha256'] and
             json_sha(proof['unknown_inputs']) == proof['unknown_inputs_sha256'], 'authorization_scope_changed')
    for item in proof['protected_files']:
        _verify_file(item)
    _require(state['request_count'] == len(state['calls']) and
             len({row['id'] for row in state['calls']}) == len(state['calls']), 'lane_count_changed')
    names, repairs, previous = set(), set(), None
    for call in state['calls']:
        _require(previous is None or previous['status'] == 'received', 'work_after_unsettled_request')
        request = _read(lane / 'calls' / call['id'] / 'request.json')
        _require(json_sha(request) == call['request_sha256'], 'lane_request_changed')
        _request_allowed(proof, call['name'], request)
        _require(call['name'] not in names, 'duplicate_stage')
        if call.get('repair_of'):
            _require(previous is not None and previous['id'] == call['repair_of'] and
                     not previous.get('repair_of') and previous['id'] not in repairs and
                     call['name'] == previous['name'] + '_repair', 'extra_or_unbound_repair')
            original = _read(lane / 'calls' / previous['id'] / 'request.json')
            _require(original == {**request, 'arguments': {**request['arguments'], 'prompt': original['arguments']['prompt']}} and
                     request['arguments']['prompt'].startswith(original['arguments']['prompt'] + REPAIR_MARKER),
                     'repair_input_changed')
            repairs.add(previous['id'])
        else:
            _require(not call['name'].endswith('_repair'), 'repair_without_parent')
        if call['name'].startswith('chain_e2e_v1_fine_'):
            _handoff(lane, state)
        names.add(call['name'])
        previous = call
    _require(sum(name.startswith('fine_') and not name.endswith('_repair') for name in names) <= 16 and
             sum(name.startswith('zoom_') and not name.endswith('_repair') for name in names) <= 4,
             'observation_scope_exhausted')
    return proof


def aggregate_usage(proof, lane_requests):
    return {'historical_requests': proof['historical_requests'], 'parent_requests': proof['parent_requests'],
            'restoration_requests': lane_requests,
            'lineage_cumulative_vision_requests': proof['historical_requests'] + proof['parent_requests'] + lane_requests}
