"""Offline carryover checks use real ledgers and normal CodexMCP JSON repairs."""
import json
from pathlib import Path

import pytest

from test_restoration_policy import registered, reply, trial
from omni_story.library import restored_selected_completion as flow
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def review(reference_sha, *, theme='partial', continuity='pass'):
    return dict(reference_sha256=reference_sha, theme_status=theme, editing_status='partial',
        continuity_status=continuity, evidence=['Observed actual action at output 1–2s.'],
        limitations=['Native dynamic sampling remains unknown.'], revision_requests=[])


@pytest.fixture
def settled(trial, monkeypatch):
    state, _ = registered(trial)
    lane = state.output
    selected = 0
    video = lane / 'render_0/final.mp4'
    video.parent.mkdir()
    video.write_bytes(b'original actual rough')
    source = dict(source_id='actual_rough', path=str(video), sha256=sha256_file(video),
                  duration_s=83.0, video_stream_index=0, audio_stream_index=1)
    ref_sha = state.input_lock['reference_sha256']
    reference = dict(source_id='reference', path=str(lane / 'reference.mp4'),
                     sha256=ref_sha, duration_s=21.933333, video_stream_index=0, audio_stream_index=1)
    write_json(lane / 'reference_catalog/inventory.json', {'sources': [reference]})
    write_json(lane / 'selected_rough_catalog/inventory.json', {'sources': [source]})
    plan = {'segments': [{'segment_id': 'current_choice', 'source_in_s': 1, 'source_out_s': 84}]}
    blind = {'apparent_story': 'Current independently observed rough sequence.'}
    provenance = [{'segment_id': 'current_choice', 'output_in_s': 0, 'output_out_s': 83}]
    prior_review = review(ref_sha, theme='fail', continuity='fail')
    context = dict(reference=trial[2]['full_response']['reference'], actual_render_sha256=source['sha256'],
                   blind_reading=blind, plan=plan, provenance=provenance)
    request = {'tool': 'analyze_video',
        'arguments': {'video_source': str(video), 'prompt': flow.rough_templates.review_prompt(context)},
        'media_sha256': source['sha256'], 'provider': flow.RestorationMCP.provider,
        'policy_version': state.data['policy_version']}
    call, folder = state.begin_call('review_0', request)
    state.complete_call(call, reply(prior_review))
    write_json(folder / 'parsed.json', prior_review)
    write_json(lane / 'plan_0.json', plan)
    write_json(lane / 'blind_reading_0.json', blind)
    write_json(lane / 'review_0.json', prior_review)
    write_json(lane / 'render_0/render_result.json', dict(rendered_path=str(video), sha256=source['sha256'],
               provenance=provenance, measured_duration_s=83.0))
    write_json(lane / 'render_0/render_input.json', {'compiled': {'plan': plan, 'segments': provenance}})
    rough = dict(final_video=str(video), final_sha256=source['sha256'], selected_round=selected,
        review=prior_review, selected_review_path=str(lane / 'review_0.json'))
    write_json(lane / 'result.json', rough)
    handoff = dict(policy='actual_reviewed_rough_to_skill_v1', rough_video_path=str(video),
        rough_source_sha256=source['sha256'], rough_duration_s=83.0, selected_round=selected,
        rough_review_call_id=call['id'], rough_review=prior_review,
        original_method='original_rough_v1', target_is_reference_duration=True)
    write_json(lane / 'rough_handoff.json', handoff)
    state.set_artifact('restored_rough_handoff', {'path': str(lane / 'rough_handoff.json'), 'sha256': json_sha(handoff)})
    original = dict(policy=flow.policy.POLICY, status='rough_content_not_established', rough=rough,
        rough_video=str(video), rough_sha256=source['sha256'], rough_duration_s=83.0,
        final_video=str(video), final_sha256=source['sha256'], joint_quality_gate=False)
    write_json(lane / 'restoration_result.json', original)
    (lane / 'mcp_stop').write_bytes(b'restoration_settled')
    controller = lane / 'controllers/bootstrap_fix_v1/.omni-server'
    job = dict(job_id='completed-original-job', state='succeeded', exit_code=0,
        finished_at='2026-10-09T10:00:00Z', supervisor_pid=1234, child_pid=1235,
        command=['python', '-m', 'omni_story.library.server_restored', '_run', '--output', str(lane)])
    write_json(controller / 'job.json', job)
    (controller / 'run.lock').write_text(job['job_id'] + '\n', encoding='utf-8')
    fake_proxy = lane / 'cache/current_full_proxy.mp4'
    fake_proxy.parent.mkdir()
    fake_proxy.write_bytes(b'actual current rough full proxy')
    monkeypatch.setattr(flow, 'prepare_window', lambda given, start, end, cache, fps:
        {'path': str(fake_proxy), 'sha256': sha256_file(fake_proxy)})
    return state, original, source, reference, controller


def bind(settled):
    state = settled[0]
    path = flow.register(state.output)
    return sha256_file(path)


def model(state, digest, monkeypatch, values):
    pending = list(values)
    submitted = []
    def submit(self, name, request, *, repair_of=None):
        submitted.append(name)
        call, _ = state.begin_call(name, request, repair_of=repair_of)
        value = pending.pop(0)
        if isinstance(value, Exception):
            state.fail_call(call, str(value), uncertain=True)
            raise value
        response = reply(value)
        state.complete_call(call, response)
        return call, response
    # Only the actual native POST is replaced. CompletionMCP guards and normal
    # CodexMCP parse/validator/one-repair logic run unchanged.
    monkeypatch.setattr(flow.RestorationMCP, '_submit', submit)
    mcp = object.__new__(flow.CompletionMCP)
    mcp.state, mcp.output, mcp.correction_sha256 = state, state.output, digest
    return mcp, submitted


def forbidden_fine(*args, **kwargs):
    raise AssertionError('An extra finecut was attempted')


def test_register_binds_actual_settled_job_and_preserves_every_original_byte(settled):
    state = settled[0]
    before = {path: path.read_bytes() for path in state.output.rglob('*') if path.is_file()}
    digest = bind(settled)
    proof = flow.load(state.output, digest)
    assert proof['original_state']['calls'] == state.data['calls']
    assert proof['selected_stage'] == 'selected_review_v2_0'
    assert proof['historical043_provenance'] == flow.historical.provenance()
    assert proof['added_fine_renders'] == proof['added_rough_renders'] == 0
    assert all(path.read_bytes() == raw for path, raw in before.items())
    with pytest.raises(LibraryStopped, match='already_registered'):
        flow.register(state.output)


@pytest.mark.parametrize('status', ['submitted', 'uncertain', 'failed_known'])
def test_registration_refuses_every_unsettled_status(settled, status):
    state = settled[0]
    data = read(state.path)
    data['calls'][-1]['status'] = status
    write_json(state.path, data)
    with pytest.raises(LibraryStopped, match='unsettled_original'):
        flow.register(state.output)
    assert not (state.output / flow.PROOF).exists()


@pytest.mark.parametrize('problem', ['running', 'supervisor', 'group', 'lock', 'failure', 'absent_result', 'omission'])
def test_registration_requires_proven_omission_after_fully_dead_original(settled, monkeypatch, problem):
    state, _, _, _, controller = settled
    if problem == 'running':
        job = read(controller / 'job.json'); job['state'] = 'running'
        write_json(controller / 'job.json', job)
    elif problem == 'supervisor':
        monkeypatch.setattr(flow.server_jobs, '_identity', lambda pid: 'running')
    elif problem == 'group':
        monkeypatch.setattr(flow.server_jobs, '_group_running', lambda pid: True)
    elif problem == 'lock':
        (controller / 'run.lock').write_text('another-job', encoding='utf-8')
    elif problem == 'failure':
        write_json(state.output / 'restoration_failure.json', {'error': 'terminal'})
    elif problem == 'absent_result':
        (state.output / 'restoration_result.json').unlink()
    else:
        state.set_artifact('editing_review_policy', {'policy': 'already present'})
    with pytest.raises(LibraryStopped):
        flow.register(state.output)
    assert not (state.output / flow.PROOF).exists()


@pytest.mark.parametrize('theme,continuity', [('fail', 'pass'), ('partial', 'fail'), ('unverifiable', 'pass')])
def test_failed_corrected_review_settles_without_finecut(settled, monkeypatch, theme, continuity):
    state, _, _, reference, _ = settled
    digest = bind(settled)
    mcp, names = model(state, digest, monkeypatch,
        [review(reference['sha256'], theme=theme, continuity=continuity)])
    monkeypatch.setattr(flow, 'execute_finecut', forbidden_fine)
    result = flow.execute(state, mcp, correction_sha256=digest)
    assert result['status'] == 'selected_review_content_not_established_no_finecut'
    assert names == ['selected_review_v2_0']
    assert not (state.output / 'skill_finecut').exists()
    assert not (state.output / flow.SUPPLEMENT).exists()


def test_corrected_partial_gate_passes_actual_review_to_unused_fine_observation(settled, monkeypatch):
    state, _, source, reference, _ = settled
    digest = bind(settled)
    corrected = review(reference['sha256'])
    mcp, names = model(state, digest, monkeypatch, [corrected, {'observed': 'current actual rough'}])
    before_result = (state.output / 'restoration_result.json').read_bytes()
    before_handoff = (state.output / 'rough_handoff.json').read_bytes()
    before_stop = (state.output / 'mcp_stop').read_bytes()
    def refine(given, client, context, parent, ref, base):
        assert given is state and parent == source and ref == reference
        assert base == state.output / 'skill_finecut'
        assert context['source_call_id'] == 'glm_001_reference'
        assert context['prior_rough_review'] == corrected
        assert context['prior_rough_evidence']['blind_reading']['apparent_story'].startswith('Current')
        given._reload()
        supplement = flow._supplement(state.output, state.data, read(state.output / flow.PROOF))
        assert supplement['original_handoff'] == read(state.output / 'rough_handoff.json')
        # The next actual call is independently guarded against the unchanged
        # original handoff plus supplemental corrected gate.
        proxy = state.output / 'cache/current_full_proxy.mp4'
        client.call(flow.PREFIX + 'observe', json.dumps(context), proxy, lambda value: None,
            scope={'kind': 'continuous_window', 'source_sha256': source['sha256'],
                   'source_start_s': 0, 'source_end_s': source['duration_s']})
        fine = state.output / 'skill_finecut/delivery/final.mp4'
        fine.parent.mkdir(parents=True)
        fine.write_bytes(b'unused original fine output')
        return dict(final_video=str(fine), final_sha256=sha256_file(fine), joint_quality_gate=True)
    monkeypatch.setattr(flow, 'execute_finecut', refine)
    result = flow.execute(state, mcp, correction_sha256=digest)
    assert result['status'] == 'selected_review_completed_original_finecut_completed'
    assert result['joint_quality_gate'] is False  # Rough theme partial remains partial.
    assert names == ['selected_review_v2_0', flow.PREFIX + 'observe']
    assert (state.output / 'restoration_result.json').read_bytes() == before_result
    assert (state.output / 'rough_handoff.json').read_bytes() == before_handoff
    assert (state.output / 'mcp_stop').read_bytes() == before_stop
    state._reload()
    assert len(state.data['artifacts']['restored_rough_handoff']) == 1


def test_normal_selected_review_gets_only_one_json_repair_and_preserves_failure(settled, monkeypatch):
    state, _, _, reference, _ = settled
    digest = bind(settled)
    mcp, names = model(state, digest, monkeypatch,
        [{'invalid': 'first'}, review(reference['sha256'], theme='fail')])
    monkeypatch.setattr(flow, 'execute_finecut', forbidden_fine)
    result = flow.execute(state, mcp, correction_sha256=digest)
    assert names == ['selected_review_v2_0', 'selected_review_v2_0_repair']
    state._reload()
    first, repair_call = state.data['calls'][-2:]
    assert (state.output / 'calls' / first['id'] / 'protocol_failure.json').is_file()
    assert repair_call['repair_of'] == first['id']
    assert result['corrected_review_call_id'] == repair_call['id']


@pytest.mark.parametrize('values,error_match', [
    ([{'invalid': 1}, {'invalid': 2}], 'repair_exhausted'),
    ([LibraryStopped('unknown_transport_no_replay')], 'unknown_transport'),
])
def test_stage_failure_is_terminal_and_never_replayed(settled, monkeypatch, values, error_match):
    state = settled[0]
    digest = bind(settled)
    mcp, names = model(state, digest, monkeypatch, values)
    with pytest.raises((LibraryStopped, ValueError), match=error_match):
        flow.execute(state, mcp, correction_sha256=digest)
    failure = read(state.output / flow.FAILURE)
    assert failure['automatic_restart'] is False
    before = list(names)
    with pytest.raises(LibraryStopped, match='terminal_no_restart'):
        flow.execute(state, mcp, correction_sha256=digest)
    assert names == before


def test_execution_claim_prevents_crash_resume_even_without_failure_file(settled):
    digest = bind(settled)
    state = settled[0]
    write_json(state.output / flow.STARTED, {'interrupted': True})
    with pytest.raises(LibraryStopped, match='already_started_no_restart'):
        flow.execute(state, None, correction_sha256=digest)


def test_completed_result_is_cache_only_and_rechecks_actual_output_sha(settled, monkeypatch):
    state, _, source, reference, _ = settled
    digest = bind(settled)
    mcp, names = model(state, digest, monkeypatch, [review(reference['sha256'], theme='fail')])
    result = flow.execute(state, mcp, correction_sha256=digest)
    before = state.path.read_bytes()
    assert flow.execute(state, None, correction_sha256=digest) == result
    assert names == ['selected_review_v2_0'] and state.path.read_bytes() == before
    # The original rough is also a protected file, so either check rejects it.
    Path(source['path']).write_bytes(b'changed actual output')
    with pytest.raises(LibraryStopped, match='protected_original_changed|final_cache_changed'):
        flow.execute(state, None, correction_sha256=digest)


def test_completed_original_fine_is_reused_even_when_corrected_rough_fails(settled, monkeypatch):
    state, original, source, reference, _ = settled
    fine = state.output / 'skill_finecut/delivery/final.mp4'
    fine.parent.mkdir(parents=True)
    fine.write_bytes(b'already completed original fine')
    fine_result = dict(final_video=str(fine), final_sha256=sha256_file(fine), joint_quality_gate=True)
    write_json(state.output / 'skill_finecut/result.json', fine_result)
    request = {'tool': 'analyze_video', 'arguments': {'video_source': str(fine), 'prompt': 'prior actual fine'},
        'media_sha256': sha256_file(fine), 'provider': flow.RestorationMCP.provider,
        'policy_version': state.data['policy_version']}
    call, folder = state.begin_call(flow.PREFIX + 'observe', request)
    state.complete_call(call, reply({'observed': 'prior actual fine'}))
    write_json(folder / 'parsed.json', {'observed': 'prior actual fine'})
    original.update(status='restored_rough_to_fine_completed', finecut=fine_result,
                    final_video=str(fine), final_sha256=sha256_file(fine))
    write_json(state.output / 'restoration_result.json', original)
    digest = bind(settled)
    mcp, names = model(state, digest, monkeypatch, [review(reference['sha256'], theme='fail')])
    monkeypatch.setattr(flow, 'execute_finecut', forbidden_fine)
    result = flow.execute(state, mcp, correction_sha256=digest)
    assert result['status'] == 'selected_review_completed_existing_fine_reused'
    assert result['final_video'] == str(fine) and result['joint_quality_gate'] is False
    assert names == ['selected_review_v2_0']


def test_no_new_rough_stage_or_additional_handoff_can_pass_completion_guard(settled):
    state = settled[0]
    digest = bind(settled)
    request = {'tool': 'analyze_image', 'arguments': {'image_source': str(state.output / 'new.jpg'), 'prompt': 'new'},
               'media_sha256': 'f' * 64}
    call, _ = state.begin_call('search_1', request)
    state.complete_call(call, reply({'new': True}))
    with pytest.raises(LibraryStopped, match='new_rough_or_extra_stage_forbidden'):
        flow.load(state.output, digest)
    # Independently, no duplicate restored_rough_handoff artifact is allowed.
    data = read(state.path)
    data['calls'] = data['calls'][:-1]; data['request_count'] -= 1
    write_json(state.path, data)
    state.set_artifact('restored_rough_handoff', {'path': str(state.output / 'rough_handoff.json'), 'sha256': 'f' * 64})
    with pytest.raises(LibraryStopped, match='handoff_must_remain_single'):
        flow.load(state.output, digest)


def test_first_fine_observation_needs_corrected_gate_not_merely_old_handoff(settled):
    state = settled[0]
    digest = bind(settled)
    request = {'tool': 'analyze_video', 'arguments': {'video_source': str(state.output / 'new.mp4'), 'prompt': 'fine'},
               'media_sha256': 'f' * 64}
    call, _ = state.begin_call(flow.PREFIX + 'observe', request)
    state.complete_call(call, reply({'new': True}))
    with pytest.raises(LibraryStopped, match='corrected_handoff_required'):
        flow.load(state.output, digest)


def test_full_old_call_and_artifact_prefix_cannot_be_reclassified(settled):
    state = settled[0]
    digest = bind(settled)
    data = read(state.path)
    data['calls'][0]['usage']['completion_tokens'] = 99
    write_json(state.path, data)
    with pytest.raises(LibraryStopped, match='original_ledger_prefix_changed'):
        flow.load(state.output, digest)


def test_correction_requires_exact_registered_sha(settled):
    state = settled[0]
    bind(settled)
    with pytest.raises(LibraryStopped, match='completion_proof_changed'):
        flow.execute(state, None, correction_sha256='f' * 64)


@pytest.mark.parametrize('changed', ['measured_duration_s', 'compiled_plan', 'compiled_provenance'])
def test_registration_binds_full_actual_duration_and_executed_plan(settled, changed):
    lane = settled[0].output
    if changed == 'measured_duration_s':
        path = lane / 'render_0/render_result.json'
        value = read(path)
        value['measured_duration_s'] = 82
    else:
        path = lane / 'render_0/render_input.json'
        value = read(path)
        key = 'plan' if changed == 'compiled_plan' else 'segments'
        value['compiled'][key] = {'changed': True}
    write_json(path, value)
    with pytest.raises(LibraryStopped, match='selected_actual_binding_changed'):
        flow.register(lane)


def test_a_fine_failure_is_terminal_without_new_alias_or_reexecution(settled, monkeypatch):
    state, _, _, reference, _ = settled
    digest = bind(settled)
    mcp, names = model(state, digest, monkeypatch, [review(reference['sha256'])])
    fine_attempts = []
    def fail(*args, **kwargs):
        fine_attempts.append(True)
        raise LibraryStopped('fine_actual_stage_terminal')
    monkeypatch.setattr(flow, 'execute_finecut', fail)
    with pytest.raises(LibraryStopped, match='fine_actual_stage_terminal'):
        flow.execute(state, mcp, correction_sha256=digest)
    assert read(state.output / flow.FAILURE)['automatic_restart'] is False
    with pytest.raises(LibraryStopped, match='terminal_no_restart'):
        flow.execute(state, mcp, correction_sha256=digest)
    assert len(fine_attempts) == 1 and names == ['selected_review_v2_0']


def test_selected_round_one_binds_only_its_unused_alias(settled, monkeypatch):
    state, original, source, reference, _ = settled
    lane = state.output
    old_review = original['rough']['review']
    old_request = read(lane / 'calls' / state.data['calls'][0]['id'] / 'request.json')
    request = {**old_request, 'arguments': {**old_request['arguments'], 'prompt':
        old_request['arguments']['prompt'] + '\nCurrent second candidate.'}}
    call, folder = state.begin_call('review_1', request)
    state.complete_call(call, reply(old_review))
    write_json(folder / 'parsed.json', old_review)
    for stem in ('plan', 'blind_reading', 'review'):
        (lane / f'{stem}_1.json').write_bytes((lane / f'{stem}_0.json').read_bytes())
    (lane / 'render_1').mkdir()
    for name in ('render_result.json', 'render_input.json'):
        (lane / 'render_1' / name).write_bytes((lane / 'render_0' / name).read_bytes())
    original['rough'].update(selected_round=1, selected_review_path=str(lane / 'review_1.json'))
    write_json(lane / 'result.json', original['rough'])
    write_json(lane / 'restoration_result.json', original)
    handoff = read(lane / 'rough_handoff.json')
    handoff.update(selected_round=1, rough_review_call_id=call['id'])
    write_json(lane / 'rough_handoff.json', handoff)
    entry = state.data['artifacts']['restored_rough_handoff'][0]
    write_json(entry['path'], {'path': str(lane / 'rough_handoff.json'), 'sha256': json_sha(handoff)})
    data = read(state.path)
    data['artifacts']['restored_rough_handoff'][0]['sha256'] = json_sha(read(Path(entry['path'])))
    write_json(state.path, data)
    digest = bind(settled)
    assert read(lane / flow.PROOF)['selected_stage'] == 'selected_review_v2_1'
    mcp, names = model(state, digest, monkeypatch, [review(reference['sha256'], theme='fail')])
    result = flow.execute(state, mcp, correction_sha256=digest)
    assert result['selected_round'] == 1 and names == ['selected_review_v2_1']


def test_native_bootstrap_failure_is_terminal_with_zero_new_calls(settled, monkeypatch):
    from omni_story.library import server_cli
    state = settled[0]
    digest = bind(settled)
    original_count = state.data['request_count']
    def blocked(home):
        raise LibraryStopped('missing_native_configuration')
    monkeypatch.setattr(server_cli, 'settings', blocked)
    with pytest.raises(LibraryStopped, match='missing_native_configuration'):
        flow.main(['--home', str(state.output / 'unused_home'), '_run', '--output', str(state.output),
                   '--correction-sha256', digest])
    failure = read(state.output / flow.FAILURE)
    assert failure['request_count'] == original_count and failure['automatic_restart'] is False
    assert not (state.output / flow.STARTED).exists()
    with pytest.raises(LibraryStopped, match='terminal_no_restart'):
        flow.execute(state, None, correction_sha256=digest)
