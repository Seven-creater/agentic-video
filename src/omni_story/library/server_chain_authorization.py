"""Explicit same-task authorization for playable rough cut followed by fine cut.

The settled 35-call history is immutable. This artifact grants one rough render
and at most two fine renders, without repairing or reinterpreting old failures.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from . import contracts, server_capacity_recovery
from .media import sha256_file, verify_source
from .pipeline import _http_evidence
from .state import LibraryState, LibraryStopped, json_sha

POLICY = 'opencode_one_chain_e2e_v1'
KEY = 'server_one_chain_e2e_authorization'
NAME = 'one_chain_e2e_v1'
STAGE_PREFIX = 'chain_e2e_v1_'
FAILURE_REPORT = 'failure_one_chain_e2e_v1.json'
BASELINE_CALLS = 35
WRAPPER_KEY = 'server_slice_uncertainty_wrapper_reconciliation'
RENDER_GRANT = {'rough': 1, 'fine': 2, 'total': 3}
REPAIR_MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
MUTABLE_RUNTIME_FILES = {'library_state.json', '.state.lock', 'mcp_stop', 'mcp_current.json',
                         'current_status.json'}


def allowed_stage(name):
    """Enumerate finite rough/fine stages, including their sole format repair."""
    if not isinstance(name, str):
        return False
    return re.fullmatch(re.escape(STAGE_PREFIX) +
        r'(?:rough_(?:blind|review)|fine_(?:observe|plan|blind|review|revise|blind_r|review_r|'
        r'inspect_[0-3]|detail_[0-3]_[0-5]))(?:_repair)?', name) is not None


def stage_dependencies(name):
    """Require the preceding settled work rather than accepting isolated stages."""
    if not allowed_stage(name):
        return ()
    name = name.removesuffix('_repair').removeprefix(STAGE_PREFIX)
    previous = {'rough_review': 'rough_blind', 'fine_observe': 'rough_review',
                'fine_plan': 'fine_observe', 'fine_blind': 'fine_plan',
                'fine_review': 'fine_blind', 'fine_revise': 'fine_review',
                'fine_blind_r': 'fine_revise', 'fine_review_r': 'fine_blind_r'}
    names = [previous[name]] if name in previous else []
    if name.startswith('fine_inspect_'):
        index = int(name.rsplit('_', 1)[1])
        names = ['fine_observe'] + ([f'fine_inspect_{index - 1}'] if index else [])
    elif name.startswith('fine_detail_'):
        index, page = map(int, name.rsplit('_', 2)[1:])
        names = [f'fine_inspect_{index}'] + ([f'fine_detail_{index}_{page - 1}'] if page else [])
    return tuple(STAGE_PREFIX + prior for prior in names)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _require(condition, reason):
    if not condition:
        raise LibraryStopped('server_chain_authorization_' + reason)


def _inside(output, path):
    path = Path(path).absolute()
    parent = path
    while parent != output and parent.is_relative_to(output):
        _require(not parent.is_symlink(), 'linked_task_file')
        parent = parent.parent
    resolved = path.resolve(strict=True)
    _require(resolved.is_relative_to(output), 'file_outside_task')
    return resolved


def _bound(output, path):
    path = _inside(output, path)
    return {'path': str(path), 'sha256': sha256_file(path)}


def _text(reply):
    return '\n'.join(row['text'] for row in reply['result']['content'] if row.get('type') == 'text')


def _known_http(output, call, text):
    records = _http_evidence(output, call['id'])
    requests = [row for row in records if row.get('type') == 'request']
    responses = [row for row in records if row.get('type') == 'response']
    _require(len(requests) == len(responses) == 1 and responses[0]['status'] == 200 and
             requests[0]['seq'] == responses[0]['seq'], 'HTTP_reply_not_known')
    choice = json.loads(responses[0]['body'])['choices'][0]
    _require(choice['finish_reason'] == 'stop' and choice['message']['content'] == text,
             'HTTP_reply_changed')


def _controller(output):
    from . import server_jobs
    directory = output / '.omni-server/recoveries' / server_jobs.REMAINING_RECOVERY_NAME
    job_path = _inside(output, directory / 'job.json')
    job = _read(job_path)
    data = _read(output / 'library_state.json')
    remaining = data['artifacts'][server_capacity_recovery.REMAINING_KEY][0]
    capacity = data['artifacts'][server_capacity_recovery.KEY][0]
    binding = job.get('recovery', {})
    _require(binding.get('name') == server_jobs.REMAINING_RECOVERY_NAME and
             binding.get('authorization_path') == capacity['path'] and
             binding.get('authorization_sha256') == sha256_file(capacity['path']) and
             binding.get('remaining_candidate', {}).get('path') == remaining['path'] and
             binding.get('remaining_candidate', {}).get('sha256') == sha256_file(remaining['path']),
             'previous_controller_binding_changed')
    _require(job.get('state') == 'failed' and job.get('exit_code') == 1 and
             not server_jobs._identity(job.get('supervisor_pid', 0)) and
             not server_jobs._group_running(job.get('child_pid', 0)) and
             _inside(output, directory / 'run.lock').read_text(encoding='utf-8').strip() == job['job_id'],
             'previous_controller_not_stopped')
    return job


def _zero_renders(output):
    _require(not (output / 'result.json').exists() and not any(output.glob('render_[0-9]*')),
             'old_candidate_already_rendered')


def _source_files(catalog, reference, *, hash_sources):
    source_files = []
    for source in catalog['sources'] + [reference]:
        path = verify_source(source)
        before = path.stat()
        if hash_sources:
            _require(sha256_file(path) == source['sha256'], 'source_bytes_changed')
        after = path.stat()
        _require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                 'source_changed_during_verification')
        source_files.append({'path': str(path), 'sha256': source['sha256'],
                             'size_bytes': after.st_size, 'mtime_ns': after.st_mtime_ns})
    return source_files


def _inputs(output, data, *, hash_sources):
    """Bind the accepted rough EDL to old paid evidence and original media."""
    _require(server_capacity_recovery.remaining_candidate(output) is not None,
             'remaining_candidate_missing')
    cfg = data['input_lock']['configuration']
    _require(data['max_requests'] is None and cfg['max_rounds'] == cfg['max_renders'] == 2 and
             cfg['max_fine'] == 16 and not data['artifacts'].get(WRAPPER_KEY), 'old_route_scope_changed')
    catalog = _read(output / 'catalog/inventory.json')
    reference = _read(output / 'reference_catalog/inventory.json')['sources'][0]
    _require([{k: source[k] for k in ('source_id', 'sha256')} for source in catalog['sources']] ==
             data['input_lock']['library_sources'] and
             reference['sha256'] == data['input_lock']['reference_sha256'], 'input_catalog_changed')
    source_files = _source_files(catalog, reference, hash_sources=hash_sources)
    windows = _read(output / 'watched_windows.json')
    _require(isinstance(windows, list) and len(windows) == 12 and
             len({window['window_id'] for window in windows}) == 12, 'watched_window_set_changed')
    server_capacity_recovery._remaining_windows(output, data, windows, 16)
    rough = _read(output / 'draft_plan_1.json')
    contracts.validate_plan(rough, catalog, windows, reference['sha256'], reference['duration_s'],
                            reference_audio_stream_index=reference['audio_stream_index'])
    plans = [call for call in data['calls'] if call['name'] in {'plan_1', 'plan_1_repair'}]
    _require(len(plans) == 2 and plans[0]['name'] == 'plan_1' and plans[0].get('repair_of') is None and
             plans[1]['name'] == 'plan_1_repair' and plans[1].get('repair_of') == plans[0]['id'] and
             plans[1] == data['calls'][31], 'rough_plan_link_changed')
    accepted = plans[-1]
    folder = _inside(output, output / 'calls' / accepted['id'])
    request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
    _require(accepted['status'] == 'received' and json_sha(request) == accepted['request_sha256'] and
             json_sha(reply) == accepted['response_sha256'] and reply.get('finish_reason') == 'stop' and
             _read(folder / 'parsed.json') == rough and contracts.parse_model_json(_text(reply)) == rough,
             'rough_plan_paid_reply_changed')
    _known_http(output, accepted, _text(reply))
    return {'catalog': catalog, 'reference': reference, 'windows': windows, 'source_files': source_files,
            'rough_plan': rough, 'rough_plan_sha256': json_sha(rough),
            'paid_rough_plan_call_id': accepted['id'],
            'paid_rough_plan_request_sha256': accepted['request_sha256'],
            'paid_rough_plan_response_sha256': accepted['response_sha256']}


def _terminal(output, data):
    _require(data['request_count'] == len(data['calls']) == BASELINE_CALLS and
             len({call['id'] for call in data['calls']}) == BASELINE_CALLS and
             all(call['status'] == 'received' for call in data['calls']), 'requires_settled_35_call_terminal')
    _zero_renders(output)
    original, repair = data['calls'][-2:]
    _require(original['name'].startswith('semantic_slice_1_') and original.get('repair_of') is None and
             repair['name'] == original['name'] + '_repair' and repair.get('repair_of') == original['id'] and
             len([call for call in data['calls'] if call.get('repair_of') == original['id']]) == 1,
             'terminal_pair_changed')
    for attempt, call in enumerate((original, repair)):
        folder = _inside(output, output / 'calls' / call['id'])
        reply, failure = _read(folder / 'response.json'), _read(folder / 'protocol_failure.json')
        _require(reply.get('finish_reason') == 'stop' and failure['error'] ==
                 'semantic/uncertainties:text_required' and failure['attempt'] == attempt and
                 failure['model_text'] == _text(reply) and not (folder / 'parsed.json').exists(),
                 'terminal_failure_changed')
        _known_http(output, call, _text(reply))
    error = 'model_protocol_repair_exhausted:' + original['name']
    _require(_read(output / 'failure_remaining_candidate_v1.json')['error'] == error and
             error in (output / '.omni-server/recoveries/remaining_candidate_v1/job.log').read_text(encoding='utf-8'),
             'terminal_report_changed')
    return _controller(output)


def _old_calls(output, data):
    files = []
    for call in data['calls']:
        folder = _inside(output, output / 'calls' / call['id'])
        _require(json_sha(_read(folder / 'request.json')) == call['request_sha256'] and
                 json_sha(_read(folder / 'response.json')) == call['response_sha256'], 'old_call_changed')
        files.extend(str(_inside(output, path)) for path in folder.rglob('*') if path.is_file())
    return sorted(files)


def _new_calls(output, data, old):
    originals, repairs, accepted_stages = {}, set(), {}
    digests = {call['request_sha256'] for call in old['calls']}
    terminal_requests = [_read(output / 'calls' / call['id'] / 'request.json') for call in old['calls'][-2:]]
    previous_settled, previous = True, None
    for call in data['calls'][BASELINE_CALLS:]:
        _require(previous_settled, 'new_work_after_unsettled_call')
        name = call['name']
        _require(allowed_stage(name) and
                 call['request_sha256'] not in digests, 'unbound_or_replayed_new_call')
        request = _read(_inside(output, output / 'calls' / call['id'] / 'request.json'))
        _require(json_sha(request) == call['request_sha256'], 'new_request_changed')
        scope = request.get('observation_scope', {})
        for terminal in terminal_requests:
            prior_scope = terminal.get('observation_scope', {})
            _require(request.get('media_sha256') != terminal.get('media_sha256') and
                     not (scope.get('source_sha256') == prior_scope.get('source_sha256') and
                          scope.get('source_start_s') == prior_scope.get('source_start_s') and
                          scope.get('source_end_s') == prior_scope.get('source_end_s')),
                     'old_terminal_slice_replayed')
        if call.get('repair_of') is None:
            _require(not name.endswith('_repair') and name not in originals, 'duplicate_original_stage')
            for prior_name in stage_dependencies(name):
                _require(prior_name in accepted_stages, 'new_stage_dependency_not_parsed')
            originals[name] = call
        else:
            parent = next((prior for prior in originals.values() if prior['id'] == call['repair_of']), None)
            _require(parent is not None and parent['status'] == 'received' and
                     name == parent['name'] + '_repair' and parent['id'] not in repairs,
                     'extra_or_unbound_repair')
            _require(previous is not None and previous['id'] == parent['id'], 'repair_not_immediate')
            original = _read(output / 'calls' / parent['id'] / 'request.json')
            _require(original == {**request, 'arguments': {**request['arguments'],
                          'prompt': original['arguments']['prompt']}} and
                     request['arguments']['prompt'].startswith(original['arguments']['prompt'] + REPAIR_MARKER),
                     'repair_input_changed')
            repairs.add(parent['id'])
        _require(call['status'] in {'received', 'submitted', 'uncertain', 'failed_known'},
                 'new_call_status_invalid')
        digests.add(call['request_sha256'])
        previous_settled = call['status'] == 'received'
        parsed = output / 'calls' / call['id'] / 'parsed.json'
        if previous_settled:
            reply = _read(_inside(output, parsed.with_name('response.json')))
            _require(json_sha(reply) == call['response_sha256'], 'new_reply_changed')
            if parsed.exists():
                _require(_read(_inside(output, parsed)) == contracts.parse_model_json(_text(reply)),
                         'new_parsed_reply_changed')
                accepted_stages[name.removesuffix('_repair')] = call
        previous = call


def load(output):
    """Read the registered policy while allowing only its finite new stages."""
    output = Path(output).resolve(strict=True)
    data = _read(output / 'library_state.json')
    entries = data.get('artifacts', {}).get(KEY, [])
    if not entries:
        return None
    _require(len(entries) == 1, 'duplicate_authorization')
    proof = _read(_inside(output, entries[0]['path']))
    _require(json_sha(proof) == entries[0]['sha256'] and proof['policy'] == POLICY and
             proof['recovery_name'] == NAME and proof['stage_prefix'] == STAGE_PREFIX and
             proof['output'] == str(output) and proof['task_id'] == data['task_id'] and
             proof['input_lock'] == data['input_lock'] and proof['baseline_request_count'] == BASELINE_CALLS and
             proof['render_grant'] == RENDER_GRANT and proof['new_candidate_rounds'] == 0 and
             all(type(proof['render_grant'][key]) is int for key in RENDER_GRANT) and
             type(proof['max_fine_actual_revisions']) is int and proof['max_fine_actual_revisions'] == 1 and
             type(proof['repairs_per_stage']) is int and proof['repairs_per_stage'] == 1 and
             proof['no_unknown_replay'] is True and proof['goal_resumed'] is False and
             proof['old_wrapper_consumed'] is False and not data['artifacts'].get(WRAPPER_KEY) and
             type(proof['user_instruction']) is str and bool(proof['user_instruction'].strip()),
             'authorization_scope_changed')
    _require(proof['execution_directory'] == str(output / 'artifacts' / NAME) and
             proof['rough_directory'] == str(output / 'artifacts' / NAME / 'rough') and
             proof['fine_target_duration_s'] == proof['reference']['duration_s'] and
             type(proof['fine_target_tolerance_s']) in (int, float) and
             proof['fine_target_tolerance_s'] == 2.0 and
             proof['source_integrity_policy'] ==
                 'actual_SHA_at_registration_then_immutable_catalog_and_stat_on_load' and
             proof['semantic_joint_policy'] ==
                 'actual_rough_content_and_character_echo_then_picture_only_fine_review' and
             proof['rough_edl_is_original_model_plan'] is True and
             proof['old_152_second_refinement_is_not_fine_input'] is True and
             proof['original_failures_preserved'] is True, 'chain_policy_changed')
    baseline = _inside(output, proof['baseline_state_path'])
    _require(sha256_file(baseline) == proof['baseline_state_sha256'], 'baseline_changed')
    old = _read(baseline)
    _require(old['request_count'] == len(old['calls']) == BASELINE_CALLS and
             old['calls'] == data['calls'][:BASELINE_CALLS] and
             old['input_lock'] == data['input_lock'] and old['task_id'] == data['task_id'] and
             old['max_requests'] is data['max_requests'] is None and
             all(call['status'] == 'received' for call in old['calls']) and
             data['request_count'] == len(data['calls']) and len(data['calls']) >= BASELINE_CALLS and
             len({call['id'] for call in data['calls']}) == len(data['calls']) and
             all(data['artifacts'].get(key, [])[:len(rows)] == rows for key, rows in old['artifacts'].items()),
             'history_changed')
    protected = proof['protected_files']
    _require(isinstance(protected, list) and
             len({record['path'] for record in protected}) == len(protected), 'protected_file_set_invalid')
    for record in protected:
        _require(sha256_file(_inside(output, record['path'])) == record['sha256'], 'old_file_changed')
    _require(_old_calls(output, old) == proof['old_call_files'] and
             set(proof['old_call_files']).issubset({record['path'] for record in protected}),
             'old_call_file_set_changed')
    with (output / 'mcp_http.jsonl').open('rb') as handle:
        prefix = handle.read(proof['http_prefix_bytes'])
    _require(len(prefix) == proof['http_prefix_bytes'] and
             hashlib.sha256(prefix).hexdigest() == proof['http_prefix_sha256'], 'old_HTTP_prefix_changed')
    # Registration reproduced the complete old protocol. Protected bytes and the
    # HTTP prefix already guarantee its paid evidence; do not parse the100+MB
    # journal again or re-hash14GB of originals for every new model stage.
    catalog = _read(output / 'catalog/inventory.json')
    reference = _read(output / 'reference_catalog/inventory.json')['sources'][0]
    rough = _read(output / 'draft_plan_1.json')
    accepted = old['calls'][31]
    inputs = {'catalog': catalog, 'reference': reference,
              'windows': _read(output / 'watched_windows.json'),
              'source_files': _source_files(catalog, reference, hash_sources=False),
              'rough_plan': rough, 'rough_plan_sha256': json_sha(rough),
              'paid_rough_plan_call_id': accepted['id'],
              'paid_rough_plan_request_sha256': accepted['request_sha256'],
              'paid_rough_plan_response_sha256': accepted['response_sha256']}
    _require(all(proof[key] == value for key, value in inputs.items()), 'input_evidence_changed')
    _new_calls(output, data, old)
    return proof


def launch_binding(output):
    """Require the exact old terminal; the controller's mkdir owns launch-once."""
    output = Path(output).resolve(strict=True)
    proof = load(output)
    _require(proof is not None, 'authorization_missing')
    data = _read(output / 'library_state.json')
    job = _terminal(output, data)
    _require(job['job_id'] == proof['failed_controller_job_id'], 'previous_controller_changed')
    return proof


def register(output, user_instruction, *, registry_path=None):
    """Bind explicit end-to-end permission before any new render or model call."""
    output = Path(output).resolve(strict=True)
    _require(type(user_instruction) is str and bool(user_instruction.strip()), 'user_instruction_required')
    _require(load(output) is None, 'already_registered')
    _require(not (output / '.omni-server/recoveries' / NAME).exists(), 'controller_already_exists')
    _require(not (output / 'artifacts' / NAME).exists(), 'execution_directory_already_exists')
    data = _read(output / 'library_state.json')
    inputs = _inputs(output, data, hash_sources=True)
    job = _terminal(output, data)
    call_files = _old_calls(output, data)
    existing = [path for path in output.rglob('*') if path.is_file()]
    runtime, protected = [], []
    for path in existing:
        relative = str(path.relative_to(output)).replace('\\', '/')
        if relative in MUTABLE_RUNTIME_FILES:
            runtime.append({**_bound(output, path), 'original_text': path.read_bytes().decode('utf-8')
                            if relative in {'mcp_stop', 'mcp_current.json', 'current_status.json'} else None})
        elif relative != 'mcp_http.jsonl':
            protected.append(_bound(output, path))
    baseline = output / 'artifacts/server_one_chain_e2e_baseline_v1.json'
    _require(not baseline.exists(), 'baseline_already_exists')
    state = LibraryState(output, data['input_lock'], max_requests=None, registry_path=registry_path)
    _require(_read(output / 'library_state.json') == data, 'state_changed_during_registration')
    journal = (output / 'mcp_http.jsonl').read_bytes()
    proof = {'policy': POLICY, 'recovery_name': NAME, 'stage_prefix': STAGE_PREFIX,
        'output': str(output), 'task_id': data['task_id'], 'input_lock': data['input_lock'],
        'user_instruction': user_instruction, 'baseline_request_count': BASELINE_CALLS,
        'baseline_state_path': str(baseline), 'baseline_state_sha256': sha256_file(output / 'library_state.json'),
        'protected_files': protected, 'old_call_files': call_files, 'old_mutable_runtime_files': runtime,
        'http_prefix_bytes': len(journal), 'http_prefix_sha256': hashlib.sha256(journal).hexdigest(),
        'failed_controller_job_id': job['job_id'], 'render_grant': dict(RENDER_GRANT),
        'new_candidate_rounds': 0, 'max_fine_actual_revisions': 1, 'repairs_per_stage': 1,
        'no_unknown_replay': True, 'goal_resumed': False, 'old_wrapper_consumed': False,
        'execution_directory': str(output / 'artifacts' / NAME),
        'rough_directory': str(output / 'artifacts' / NAME / 'rough'),
        'fine_target_duration_s': inputs['reference']['duration_s'], 'fine_target_tolerance_s': 2.0,
        'source_integrity_policy': 'actual_SHA_at_registration_then_immutable_catalog_and_stat_on_load',
        'semantic_joint_policy': 'actual_rough_content_and_character_echo_then_picture_only_fine_review',
        'rough_edl_is_original_model_plan': True, 'old_152_second_refinement_is_not_fine_input': True,
        'original_failures_preserved': True, **deepcopy(inputs)}
    baseline.write_bytes((output / 'library_state.json').read_bytes())
    baseline.chmod(0o600)
    path = state.set_artifact(KEY, proof)
    load(output)
    return path
