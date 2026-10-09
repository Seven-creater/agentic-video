"""Same-task rough-to-fine grants use local synthetic evidence, never paid calls."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts
from omni_story.library import server_capacity_recovery as recovery
from omni_story.library import server_chain_authorization as chain, server_jobs
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_server_capacity_recovery import append_http, node, original, read, response, snapshot, unchanged
from test_server_remaining_candidate import rejected


USER = '我建议直接到服务器上端到端测试一遍，看哪里错了，就修改'


@pytest.fixture
def stopped_chain(rejected):
    """Match real positions31 failed draft,32 sole accepted draft,33 old refinement."""
    state, _, old_windows, _, auth_path, reference, library, catalog = rejected
    remaining_path = recovery.register_remaining_candidate(state.output, registry_path=state.registry_path)
    source, planning = catalog['sources'][0], state.output / 'planning.png'

    def record(name, value, media, *, parent=None, error=None, scope=None):
        request = {'tool': 'analyze_image' if scope is None else 'analyze_video',
            'arguments': {'image_source' if scope is None else 'video_source': str(media),
                          'prompt': 'Synthetic local stage ' + name},
            'media_sha256': sha256_file(media), 'provider': state.input_lock['configuration']['provider'],
            'policy_version': state.data['policy_version']}
        if scope is not None:
            request['observation_scope'] = {key: scope[key] for key in
                ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
        call, folder = state.begin_call(name, request, repair_of=parent)
        raw = json.dumps(value, ensure_ascii=False)
        reply = response(raw, finish='stop')
        state.complete_call(call, reply)
        if error is None:
            write_json(folder / 'parsed.json', value)
        else:
            write_json(folder / 'protocol_failure.json', {'error': error,
                'attempt': int(parent is not None), 'model_text': raw})
        append_http(state.output, call['id'], state.usage()['requests'], raw, finish='stop', tokens=10)
        return call

    record('search_1', {'windows': []}, planning)
    windows = deepcopy(old_windows)
    for index in range(4):
        start, end = index * 10.0, (index + 1) * 10.0
        path = state.output / f'new_fine_{index}.mp4'
        path.write_bytes(('new synthetic fine ' + str(index)).encode())
        window_id = 'window_' + json_sha({'source_sha256': source['sha256'], 'start': start, 'end': end})[:16]
        window = deepcopy(old_windows[0])
        window.update(kind='continuous_window', window_id=window_id, source_start_s=start, source_end_s=end,
                      source_offset_s=start, path=str(path), sha256=sha256_file(path))
        window['observation']['window_id'] = window_id
        record('fine_' + window_id[7:], window['observation'], path, scope=window)
        windows.append(window)
    write_json(state.output / 'watched_windows.json', windows)
    first_window = windows[0]
    segments = [{'segment_id': f'seg_{index}', 'slot_id': f'slot_{index}',
        'source_id': source['source_id'], 'window_id': first_window['window_id'],
        'source_in_s': 101.0, 'source_out_s': 108.0, 'speed': 1.0,
        'freeze_tail_s': {1: 10.0, 2: 10.0, 3: 8.0}.get(index, 0.0),
        'role_ids': ['subject'], 'look': 'none', 'framing': 'fit'} for index in range(1, 18)]
    draft = {'reference_sha256': sha256_file(reference), 'focus_role_id': 'subject',
        'focus_role_bindings': [{'window_id': first_window['window_id'], 'role_id': 'subject',
            'identity_evidence': 'synthetic observed shape'}],
        'slots': [{'slot_id': f'slot_{index}', 'intended_takeaway': f'original model obligation {index}',
                   'segment_ids': [f'seg_{index}']} for index in range(1, 18)],
        'segments': segments, 'audio_mode': 'silent', 'source_gain_db': -12, 'reference_gain_db': 0,
        'width': 160, 'height': 240, 'fps': 30, 'limitations': ['Synthetic model proposal only.']}
    contracts.validate_plan(draft, catalog, windows, draft['reference_sha256'], 4.0,
                            reference_audio_stream_index=None)
    failed = record('plan_1', {'synthetic': 'missing plan fields'}, planning,
                    error='plan:reference_sha256_mismatch')
    record('plan_1_repair', draft, planning, parent=failed)
    plan = deepcopy(draft)
    plan['segments'][2]['freeze_tail_s'] = 10.0
    plan['segments'][3]['freeze_tail_s'] = 3.0
    refinement = {'plan': plan, 'duration': {'target_s': 150.0, 'total_s': 152.0,
                                          'over_target_reason': 'Old model target is intentionally wrong.'}}
    record('finecut_1', refinement, planning)
    write_json(state.output / 'draft_plan_1.json', draft)
    write_json(state.output / 'finecut_1.json', refinement)
    write_json(state.output / 'plan_1.json', plan)
    segment = plan['segments'][0]
    proxy_path = state.output / 'media_cache/exact_slice/window.mp4'
    proxy_path.parent.mkdir(parents=True)
    proxy_path.write_bytes(b'Synthetic old exact source slice, never decoded or submitted.')
    proxy = dict(kind='continuous_window', path=str(proxy_path), sha256=sha256_file(proxy_path),
        source_sha256=source['sha256'], source_start_s=101.0, source_end_s=108.0,
        source_offset_s=101.0, duration_s=7.0)
    stage = 'semantic_slice_1_' + json_sha({'segment': segment['segment_id'], 'sha': source['sha256'],
        'in': segment['source_in_s'], 'out': segment['source_out_s']})[:16]
    observation = {'synthetic': 'old typed evidence must remain untouched',
                   'uncertainties': [{'description': 'An original unknown remains unresolved.'}]}
    original_call = record(stage, observation, proxy_path, error='semantic/uncertainties:text_required', scope=proxy)
    observation['uncertainties'] = [{'text': 'An original unknown remains unresolved.'}]
    repair_call = record(stage + '_repair', observation, proxy_path, parent=original_call,
                         error='semantic/uncertainties:text_required', scope=proxy)
    controller = state.output / '.omni-server/recoveries' / server_jobs.REMAINING_RECOVERY_NAME
    write_json(controller / 'job.json', {'job_id': 'known-slice-wrapper-control', 'state': 'failed',
        'exit_code': 1, 'supervisor_pid': 77777, 'child_pid': 88888,
        'recovery': {'name': server_jobs.REMAINING_RECOVERY_NAME, 'authorization_path': str(auth_path),
            'authorization_sha256': sha256_file(auth_path), 'remaining_candidate': {
                'path': str(remaining_path), 'sha256': sha256_file(remaining_path)}}})
    (controller / 'job.log').write_bytes(('ValueError: model_protocol_repair_exhausted:' + stage + '\n').encode())
    (controller / 'run.lock').write_bytes(b'known-slice-wrapper-control\n')
    write_json(state.output / 'failure_remaining_candidate_v1.json',
               {'error': 'model_protocol_repair_exhausted:' + stage, 'type': 'ValueError'})
    (state.output / 'mcp_stop').write_bytes(b'remaining_candidate_stopped\n')
    state._reload()
    data = state.data
    write_json(state.output / 'mcp_current.json', {'job_id': data['calls'][-1]['id']})
    write_json(state.output / 'current_status.json', {'stage': 'old_stopped', 'requests': 35})
    state._reload()
    assert state.usage()['requests'] == len(state.data['calls']) == 35
    return dict(state=state, source=source, proxy=proxy, segment=segment, stage=stage,
                draft=draft, plan=plan, refinement=refinement, controller=controller,
                original_call=original_call, repair_call=repair_call, reference=reference, library=library)


def register(fixture):
    return chain.register(fixture['state'].output, USER, registry_path=fixture['state'].registry_path)


def append_call(fixture, stage='rough_blind', *, parent=None, status='received', request=None):
    state = fixture['state']
    media = state.output / 'artifacts' / chain.NAME / 'rough' / 'synthetic_proxy.mp4'
    media.parent.mkdir(parents=True, exist_ok=True)
    if not media.exists():
        media.write_bytes(b'New rough proxy synthetic bytes, never decoded or submitted.')
    if request is None and parent:
        request = read(state.output / 'calls' / parent['id'] / 'request.json')
        request['arguments']['prompt'] += chain.REPAIR_MARKER + json.dumps({
            'validation_error': 'synthetic type failure', 'previous_response': 'Synthetic rejected original.'})
    request = request or {'tool': 'analyze_video', 'arguments': {'video_source': str(media), 'prompt': stage},
        'media_sha256': sha256_file(media), 'provider': state.input_lock['configuration']['provider'],
        'policy_version': state.data['policy_version'], 'observation_scope': {
            'kind': 'continuous_window', 'source_sha256': sha256_file(media),
            'source_start_s': 0, 'source_end_s': 147}}
    call, folder = state.begin_call(chain.STAGE_PREFIX + stage, request, repair_of=parent)
    if status == 'received':
        state.complete_call(call, response('{"synthetic":"new received reply"}', finish='stop'))
        write_json(folder / 'parsed.json', {'synthetic': 'new received reply'})
    elif status != 'submitted':
        state.fail_call(call, 'synthetic failure', uncertain=status == 'uncertain')
    return call, folder


def change_proof(state, path, transform):
    proof = read(path)
    transform(proof)
    write_json(path, proof)
    data = read(state.path)
    data['artifacts'][chain.KEY][0]['sha256'] = json_sha(proof)
    write_json(state.path, data)


def test_register_binds_original147_rough_and_all_old_bytes_without_recovery(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    before, old = snapshot(state.output), deepcopy(read(state.path))
    assert chain.load(state.output) is None
    path = register(fixture)
    proof = read(path)
    assert chain.load(state.output) == chain.launch_binding(state.output) == proof
    assert proof['user_instruction'] == USER
    assert proof['baseline_request_count'] == 35 and proof['render_grant'] == {'rough': 1, 'fine': 2, 'total': 3}
    assert proof['rough_plan'] == fixture['draft'] and proof['rough_plan'] != fixture['plan']
    assert proof['rough_plan_sha256'] == json_sha(fixture['draft'])
    assert proof['paid_rough_plan_call_id'] == old['calls'][31]['id']
    assert proof['new_candidate_rounds'] == 0 and proof['max_fine_actual_revisions'] == 1
    assert proof['old_wrapper_consumed'] is False and proof['goal_resumed'] is False
    assert Path(proof['baseline_state_path']).read_bytes() == before[Path('library_state.json')]
    assert proof['source_files'] and all(row['sha256'] == sha256_file(row['path']) for row in proof['source_files'])
    runtime = {Path(row['path']).name: row for row in proof['old_mutable_runtime_files']}
    assert runtime['mcp_current.json']['original_text'] == before[Path('mcp_current.json')].decode()
    assert runtime['mcp_stop']['original_text'] == before[Path('mcp_stop')].decode()
    assert all(Path(record['path']).name != 'mcp_current.json' for record in proof['protected_files'])
    unchanged(state.output, before, except_state=True)
    state._reload()
    assert state.data['calls'] == old['calls'] and state.usage()['requests'] == 35
    assert not list(state.output.glob('render_*')) and not state.data['artifacts'].get(chain.WRAPPER_KEY)
    after = snapshot(state.output)
    with pytest.raises(LibraryStopped, match='already_registered'):
        register(fixture)
    assert snapshot(state.output) == after


def test_launch_allows_supervisor_owned_directory_but_no_second_launch_after_new_call(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    proof = read(register(fixture))
    controller = state.output / '.omni-server/recoveries' / chain.NAME
    controller.mkdir()
    write_json(controller / 'job.json', {'state': 'starting', 'job_id': 'new-owned-control'})
    assert chain.launch_binding(state.output) == proof
    append_call(fixture)
    assert chain.load(state.output) == proof
    with pytest.raises(LibraryStopped, match='requires_settled_35_call_terminal'):
        chain.launch_binding(state.output)


@pytest.mark.parametrize('name', [
    'rough_blind', 'rough_review', 'fine_observe', 'fine_inspect_0', 'fine_inspect_3',
    'fine_detail_0_0', 'fine_detail_3_5', 'fine_plan', 'fine_blind', 'fine_review',
    'fine_revise', 'fine_blind_r', 'fine_review_r'])
def test_allowed_finite_stage_and_sole_repair_names(name):
    assert chain.allowed_stage(chain.STAGE_PREFIX + name)
    assert chain.allowed_stage(chain.STAGE_PREFIX + name + '_repair')
    assert not chain.allowed_stage(chain.STAGE_PREFIX + name + '_repair_repair')


@pytest.mark.parametrize('name', [None, '', 'plan_1', 'rough_render', 'rough_blind_2',
                                 'fine_inspect_4', 'fine_detail_0_6', 'fine_detail_4_0',
                                 'fine_plan_2', 'fine_reread', 'fine_revise_r'])
def test_unknown_extra_stage_names_not_authorized(name):
    assert not chain.allowed_stage(None if name is None else chain.STAGE_PREFIX + name)


@pytest.mark.parametrize('case', ['unknown', 'pending', 'render', 'result', 'wrong_exit', 'running_supervisor',
    'running_group', 'wrapper_registered', 'already_controller', 'changed_source', 'wrong_draft',
    'rough_paid_position', 'terminal_protocol', 'terminal_http', 'call_request'])
def test_register_rejects_wrong_terminal_without_writing_authorization(stopped_chain, monkeypatch, case):
    fixture, state = stopped_chain, stopped_chain['state']
    if case in {'unknown', 'pending', 'wrapper_registered', 'rough_paid_position'}:
        data = read(state.path)
        if case == 'wrapper_registered':
            data['artifacts'][chain.WRAPPER_KEY] = [{'path': 'not-active', 'sha256': '0' * 64}]
        elif case == 'rough_paid_position':
            data['calls'][31]['name'] = 'finecut_1'
        else:
            data['calls'][-1]['status'] = 'uncertain' if case == 'unknown' else 'submitted'
        write_json(state.path, data)
    elif case in {'render', 'result'}:
        (state.output / ('render_1' if case == 'render' else 'result.json')).mkdir()
    elif case == 'wrong_exit':
        path = fixture['controller'] / 'job.json'
        value = read(path)
        value['exit_code'] = 0
        write_json(path, value)
    elif case.startswith('running_'):
        monkeypatch.setattr(server_jobs, '_identity' if case == 'running_supervisor' else '_group_running',
                            lambda pid: True)
    elif case == 'already_controller':
        (state.output / '.omni-server/recoveries' / chain.NAME).mkdir()
    elif case == 'changed_source':
        Path(fixture['source']['path']).write_bytes(b'modified original source bytes')
    elif case == 'wrong_draft':
        write_json(state.output / 'draft_plan_1.json', fixture['plan'])
    elif case == 'terminal_protocol':
        write_json(state.output / 'calls' / fixture['repair_call']['id'] / 'protocol_failure.json',
                   {'error': 'different error'})
    elif case == 'terminal_http':
        with (state.output / 'mcp_http.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'request', 'job_id': fixture['repair_call']['id'], 'seq': 999}) + '\n')
    else:
        (state.output / 'calls' / state.data['calls'][31]['id'] / 'request.json').write_bytes(b'{"changed":true}')
    before = snapshot(state.output)
    with pytest.raises((LibraryStopped, ValueError, FileNotFoundError, KeyError)):
        register(fixture)
    assert snapshot(state.output) == before
    assert not read(state.path)['artifacts'].get(chain.KEY)


@pytest.mark.parametrize('case', ['old_calls', 'old_response', 'old_failure', 'old_controller', 'old_plan',
    'old_queue_added', 'old_call_file_added', 'http_prefix', 'baseline', 'catalog', 'source_stat',
    'scope_render', 'scope_target', 'scope_instruction', 'scope_no_unknown', 'proof_hash', 'duplicate_proof'])
def test_load_rejects_bound_history_or_policy_changes(stopped_chain, case):
    fixture, state = stopped_chain, stopped_chain['state']
    path = register(fixture)
    proof = read(path)
    if case == 'old_calls':
        data = read(state.path)
        data['calls'][0]['usage']['completion_tokens'] = 111
        write_json(state.path, data)
    elif case in {'proof_hash', 'duplicate_proof'}:
        data = read(state.path)
        if case == 'proof_hash':
            data['artifacts'][chain.KEY][0]['sha256'] = '0' * 64
        else:
            data['artifacts'][chain.KEY].append(deepcopy(data['artifacts'][chain.KEY][0]))
        write_json(state.path, data)
    elif case.startswith('scope_'):
        def transform(value):
            if case == 'scope_render':
                value['render_grant']['fine'] = 3
            elif case == 'scope_target':
                value['fine_target_duration_s'] = 150
            elif case == 'scope_instruction':
                value['user_instruction'] = ''
            else:
                value['no_unknown_replay'] = False
        change_proof(state, path, transform)
    elif case == 'source_stat':
        Path(fixture['source']['path']).write_bytes(b'Changed library movie bytes')
    elif case == 'old_call_file_added':
        (state.output / 'calls' / fixture['original_call']['id'] / 'parsed.json').write_bytes(b'{}')
    elif case == 'old_queue_added':
        # New files outside historical call folders may be legitimate new evidence.
        # A new old-call response is a forbidden reinterpretation of the old job.
        target = state.output / 'calls' / fixture['repair_call']['id'] / 'extra_received.json'
        target.write_bytes(b'{}')
    else:
        target = {'old_response': state.output / 'calls' / fixture['repair_call']['id'] / 'response.json',
            'old_failure': state.output / 'failure_remaining_candidate_v1.json',
            'old_controller': fixture['controller'] / 'job.json', 'old_plan': state.output / 'draft_plan_1.json',
            'http_prefix': state.output / 'mcp_http.jsonl', 'baseline': Path(proof['baseline_state_path']),
            'catalog': state.output / 'catalog/inventory.json'}[case]
        raw = target.read_bytes()
        target.write_bytes(b' ' + raw[1:] if case == 'http_prefix' else raw + b' ')
    before = snapshot(state.output)
    with pytest.raises((LibraryStopped, ValueError)):
        chain.load(state.output)
    assert snapshot(state.output) == before


def test_runtime_markers_and_new_stage_artifacts_are_mutable_without_changing_baseline(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    proof = read(register(fixture))
    (state.output / 'mcp_stop').unlink()
    write_json(state.output / 'mcp_current.json', {'job_id': 'new-chain-job'})
    write_json(state.output / 'current_status.json', {'stage': 'new-stage'})
    state.set_artifact('chain_e2e_v1_rough_render', {'synthetic': 'never rendered'})
    append_call(fixture)
    assert chain.load(state.output) == proof


def test_one_sole_repair_and_then_next_stage_preserve_order(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    proof = read(register(fixture))
    original_call, folder = append_call(fixture)
    (folder / 'parsed.json').unlink()
    write_json(folder / 'protocol_failure.json', {'error': 'synthetic type failure', 'attempt': 0})
    append_call(fixture, 'rough_blind_repair', parent=original_call)
    append_call(fixture, 'rough_review')
    append_call(fixture, 'fine_observe')
    assert chain.load(state.output) == proof


def test_real_python_registration_is_readable_by_native_consumer(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    register(fixture)
    loader = Path(chain.__file__).with_suffix('.mjs')
    def policy():
        return node('const m=await import(process.argv[1]); '
                    'try {console.log(m.chainAuthorization(process.argv[2]).authorization.policy)} '
                    'catch(error) {console.log(error.message)}', loader.as_uri(), state.output)
    assert policy() == chain.POLICY
    original_call, folder = append_call(fixture)
    (folder / 'parsed.json').unlink()
    append_call(fixture, 'rough_blind_repair', parent=original_call)
    append_call(fixture, 'rough_review')
    append_call(fixture, 'fine_observe')
    assert chain.load(state.output)['policy'] == policy() == chain.POLICY


@pytest.mark.parametrize('case', ['no_predecessor', 'unparsed_predecessor'])
def test_stage_requires_prior_parsed_reply(stopped_chain, case):
    fixture, state = stopped_chain, stopped_chain['state']
    register(fixture)
    if case == 'unparsed_predecessor':
        _, folder = append_call(fixture)
        (folder / 'parsed.json').unlink()
    append_call(fixture, 'rough_review')
    with pytest.raises(LibraryStopped, match='new_stage_dependency_not_parsed'):
        chain.load(state.output)


def test_failed_format_reply_remains_hash_bound_without_parsed_file(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    register(fixture)
    _, folder = append_call(fixture)
    (folder / 'parsed.json').unlink()
    assert chain.load(state.output) is not None
    write_json(folder / 'response.json', response('{"changed":"failed original"}', finish='stop'))
    with pytest.raises(LibraryStopped, match='new_reply_changed'):
        chain.load(state.output)


def test_loader_uses_verified_source_stat_not_movie_hash_each_request(stopped_chain, monkeypatch):
    fixture, state = stopped_chain, stopped_chain['state']
    proof = read(register(fixture))
    sources = {Path(row['path']).resolve() for row in proof['source_files']}
    original_sha = chain.sha256_file
    def guarded_sha(path):
        assert Path(path).resolve() not in sources, '14 GB source hashing belongs only to registration'
        return original_sha(path)
    monkeypatch.setattr(chain, 'sha256_file', guarded_sha)
    assert chain.load(state.output) == proof


@pytest.mark.parametrize('status', ['submitted', 'uncertain', 'failed_known'])
def test_only_last_new_call_may_be_unsettled_and_launch_never_restarts_it(stopped_chain, status):
    fixture, state = stopped_chain, stopped_chain['state']
    proof = read(register(fixture))
    append_call(fixture, status=status)
    assert chain.load(state.output) == proof
    with pytest.raises(LibraryStopped):
        chain.launch_binding(state.output)
    data = read(state.path)
    prior = deepcopy(data['calls'][-1])
    later = deepcopy(prior)
    later.update(id='glm_999_chain_e2e_v1_rough_review', name=chain.STAGE_PREFIX + 'rough_review',
                 request_sha256='d' * 64, status='received')
    data['calls'].append(later)
    data['request_count'] += 1
    write_json(state.path, data)
    with pytest.raises(LibraryStopped, match='new_work_after_unsettled_call'):
        chain.load(state.output)


@pytest.mark.parametrize('case', ['extra_stage', 'duplicate_stage', 'duplicate_digest', 'repair_without_parent',
                                 'repair_twice', 'terminal_media_replay', 'terminal_scope_replay'])
def test_new_calls_are_finite_sole_repairs_and_do_not_replay_old_source_slice(stopped_chain, case):
    fixture, state = stopped_chain, stopped_chain['state']
    register(fixture)
    if case == 'extra_stage':
        append_call(fixture, 'fine_inspect_4')
    elif case in {'terminal_media_replay', 'terminal_scope_replay'}:
        request = read(state.output / 'calls' / fixture['repair_call']['id'] / 'request.json')
        request['arguments']['prompt'] = 'Different text cannot turn old source slice into new work.'
        if case == 'terminal_scope_replay':
            request['media_sha256'] = 'c' * 64
        append_call(fixture, request=request)
    elif case == 'repair_without_parent':
        append_call(fixture, 'rough_blind_repair')
    else:
        original_call, _ = append_call(fixture)
        if case == 'repair_twice':
            first, _ = append_call(fixture, 'rough_blind_repair', parent=original_call)
            data = read(state.path)
            second = deepcopy(data['calls'][-1])
            second.update(id='glm_999_chain_e2e_v1_rough_blind_repair', request_sha256='d' * 64)
            # The duplicate-stage predicate precedes request file reads only for old digests;
            # retain a consistent new request to exercise repair ownership itself.
            source_request = read(state.output / 'calls' / first['id'] / 'request.json')
            source_request['arguments']['prompt'] += '\nsecond repair'
            second['request_sha256'] = json_sha(source_request)
            write_json(state.output / 'calls' / second['id'] / 'request.json', source_request)
        else:
            data = read(state.path)
            second = deepcopy(data['calls'][-1])
            second['id'] = 'glm_999_chain_e2e_v1_rough_blind'
            if case == 'duplicate_stage':
                source_request = read(state.output / 'calls' / original_call['id'] / 'request.json')
                source_request['arguments']['prompt'] += '\nnew duplicate stage'
                second['request_sha256'] = json_sha(source_request)
                write_json(state.output / 'calls' / second['id'] / 'request.json', source_request)
        data['calls'].append(second)
        data['request_count'] += 1
        write_json(state.path, data)
    with pytest.raises((LibraryStopped, ValueError)):
        chain.load(state.output)
