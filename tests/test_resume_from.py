"""Checkpoint admission and the production pre-render path, entirely offline."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, pipeline, server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_protocol_feedback import harness, reference
from test_v4_replay_regressions import file_bytes, watched_window


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def receipt(task, name, value, media):
    call_id = 'glm_' + name
    request = dict(arguments=dict(image_source=str(media), prompt='recorded prompt'),
                   media_sha256=sha256_file(media))
    response = dict(result=dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))]))
    folder = task / 'calls' / call_id
    write_json(folder / 'request.json', request)
    write_json(folder / 'response.json', response)
    # Deliberately no parsed.json for plans: current validation must read replies.
    if name.startswith('fine_'):
        write_json(folder / 'parsed.json', value)
    return dict(id=call_id, name=name, status='received', request_sha256=json_sha(request),
                response_sha256=json_sha(response))


@pytest.fixture
def failed(tmp_path, monkeypatch):
    task = tmp_path / 'failed'
    task.mkdir()
    ref = tmp_path / 'reference.mp4'
    ref.write_bytes(b'synthetic reference')
    library = tmp_path / 'library'
    library.mkdir()
    source = library / 'source.mp4'
    source.write_bytes(b'synthetic original')
    window, raw = watched_window(task, 'watched')
    window['source_sha256'] = sha256_file(source)
    write_json(Path(window['path']).parent / 'lineage.json',
               {k: v for k, v in window.items() if k != 'observation'})
    reading = reference()
    reading.update(reference_sha256=sha256_file(ref),
        strategy=dict(adaptation_mode='reinterpret', speech_mode='narration', timing_driver='speech'))
    plan = dict(reference_sha256=sha256_file(ref), audio_mode='silent', reference_audio=None,
        width=640, height=360, fps=30, limitations=[],
        slots=[dict(slot_id='s', intended_takeaway='陪伴', segment_ids=['seg'])],
        segments=[dict(segment_id='seg', slot_id='s', source_id='old_wrong_source', window_id='watched',
            role_ids=[], source_in_s=100, source_out_s=104, speed=1, look='none', framing='fit')],
        audio_draft=[dict(id='line', text='珍惜陪伴。', voice='Ethan', mode='narration', segment_ids=['seg'])])
    invalid = deepcopy(plan)
    invalid['segments'][0]['window_id'] = 'invented_window'
    calls = [receipt(task, 'reference', dict(reading, theme='unaccepted reading'), ref),
             receipt(task, 'reference_repair', reading, ref),
             receipt(task, 'fine_watched', raw, window['path'])]
    for name, value in zip(('plan_0', 'plan_0_repair', 'plan_0_repair2'), (invalid, plan, invalid)):
        calls.append(receipt(task, name, value, ref))
    write_json(task / 'reference_reading.json', reading)
    write_json(task / 'watched_windows.json', [window])
    write_json(task / 'run_outcome.json', dict(stage='plan_0', error='model_protocol_repair_exhausted:plan_0'))
    write_json(task / '.omni-server/job.json', dict(job_id='failed', state='failed'))
    state = dict(task_id='failed', request_count=len(calls), calls=calls,
        artifacts={}, input_lock=dict(configuration=dict(creative_audio=True, prior_requests=0)))
    write_json(task / 'library_state.json', state)
    monkeypatch.setattr(cli.server_jobs, '_group_running', lambda pid: False)
    return SimpleNamespace(task=task, ref=ref, library=library, source=source, window=window,
        reading=reading, plan=plan, state=state, invalid=invalid, root=tmp_path)


class ReachedRender(Exception):
    pass


def run_to_render(case, monkeypatch, *, mode='library', fallback=False, output=None):
    config = cli.resume_config(case.task, case.ref)
    config.update(creative_audio=True, workflow='reference_rough_skill_v1', material_search=mode)
    seed = config['resume_from']['reference_seed']
    source = dict(source_id='film', sha256=sha256_file(case.source), path=str(case.source),
                  duration_s=120, audio_stream_index=None)
    ref = dict(source_id='ref', sha256=sha256_file(case.ref), path=str(case.ref),
               duration_s=4, audio_stream_index=None)
    monkeypatch.setattr(pipeline, '_catalog', lambda path, output:
        dict(sources=[deepcopy(ref if path == case.ref else source)]))
    monkeypatch.setattr(pipeline, 'create_contact_sheet', lambda *args, **kwargs: dict(path=str(case.ref)))
    if mode == 'film':
        # Mask measurement is media IO; window and observation checks stay real.
        monkeypatch.setattr(pipeline, '_validate_question_first_windows', lambda *args: None)
    monkeypatch.setattr(pipeline, '_adaptive_coarse', lambda *args, **kwargs:
        pytest.fail('resume must skip coarse/overview/zoom'))
    calls, validators, rendered = [], [], []
    class Model:
        def __init__(self, state):
            self.state, self.exclusions = state, []
        def call(self, name, prompt, media, validator, **kwargs):
            assert fallback and name == 'plan_0', 'unexpected model call: ' + name
            assert 'resume_previous_attempts' in prompt
            assert 'violations' in prompt and 'invented_window' in prompt
            calls.append(name)
            validators.append(validator)
            value = deepcopy(case.plan)
            validator(value)
            request = dict(arguments=dict(prompt=prompt, image_source=str(media)), media_sha256=sha256_file(media))
            call, _ = self.state.begin_call(name, request)
            self.state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(value))])))
            return value
    def render(catalog, plan, *args, **kwargs):
        rendered.append(deepcopy(plan))
        assert plan['segments'][0]['source_id'] == 'film'  # Full normalisation ran.
        assert plan['width'] == 1280 and plan['height'] == 720
        raise ReachedRender()
    output = output or case.task / 'evaluations/resume'
    before = {path: value for path, value in file_bytes(case.task).items() if not path.is_relative_to(output)}
    with pytest.raises(ReachedRender):
        pipeline.execute(case.ref, case.library, output, asr=False, max_fine=None,
            model_factory=Model, provider_config=config, reference_seed=seed, render_fn=render)
    assert all(path.read_bytes() == value for path, value in before.items())
    state = read(output / 'library_state.json')
    artifact = read(state['artifacts']['resume_checkpoint'][-1]['path'])
    assert state['input_lock']['configuration']['resume_from'] == config['resume_from']
    assert read(output / 'watched_windows.json')[0]['observation'] == case.window['observation']
    assert rendered
    return artifact, calls, validators, output


@pytest.mark.parametrize('mode', ['library', 'film'])
def test_middle_attempt_now_valid_uses_full_current_path_without_model_calls(failed, monkeypatch, mode):
    artifact, calls, _, _ = run_to_render(failed, monkeypatch, mode=mode)
    assert calls == []
    assert artifact['failed_stage'] == 'plan_0'
    assert artifact['reused'] == dict(reference_call_id='glm_reference_repair',
        windows_sha256=sha256_file(failed.task / 'watched_windows.json'), plan_call_id='glm_plan_0_repair')
    assert artifact['new_plan_calls'] == 0
    assert artifact['calls_saved'] == dict(reference=2, material=1, plan=3, total=6)
    assert [row['call_id'] for row in artifact['current_violations']] == ['glm_plan_0_repair2']


@pytest.mark.parametrize('mode', ['library', 'film'])
def test_none_valid_one_new_plan_and_no_observation_calls(failed, monkeypatch, mode):
    failed.state['calls'][4] = receipt(failed.task, 'plan_0_repair', failed.invalid, failed.ref)
    write_json(failed.task / 'library_state.json', failed.state)
    artifact, calls, _, _ = run_to_render(failed, monkeypatch, mode=mode, fallback=True)
    assert calls == ['plan_0']
    assert artifact['reused']['plan_call_id'] == 'new_plan'
    assert artifact['new_plan_calls'] == 1
    assert len(artifact['current_violations']) == 3
    assert artifact['calls_saved']['plan'] == 0


def test_fresh_plan_keeps_normal_two_repair_budget(failed, monkeypatch):
    failed.state['calls'][4] = receipt(failed.task, 'plan_0_repair', failed.invalid, failed.ref)
    write_json(failed.task / 'library_state.json', failed.state)
    _, _, validators, _ = run_to_render(failed, monkeypatch, fallback=True)
    scratch = failed.root / 'fresh_budget'
    scratch.mkdir()
    model, media, sent = harness(scratch, monkeypatch,
        [deepcopy(failed.invalid), deepcopy(failed.invalid), deepcopy(failed.plan)],
        provider='official_vision_mcp_in_opencode')
    value = model.call('plan_0', 'New plan from reused windows', media, validators[0], image=True)
    assert value['segments'][0]['source_id'] == 'film'
    assert len(sent) == 3
    assert [call['name'] for call in model.state.data['calls']] == ['plan_0', 'plan_0_repair', 'plan_0_repair2']


def test_seed_chain_keeps_all_attempts_and_original_observations(failed, monkeypatch):
    _, _, _, output = run_to_render(failed, monkeypatch)
    write_json(output / '.omni-server/job.json', dict(job_id='child', state='failed'))
    # The child made no plan calls; resume still walks its pinned seed chain.
    before = file_bytes(failed.task)
    config = cli.resume_config(output, failed.ref)
    assert [row['call_id'] for row in config['resume_from']['plan_seeds']] == [
        'glm_plan_0_repair2', 'glm_plan_0_repair', 'glm_plan_0']
    assert config['received_visual_seed']['ledger']['path'] == str(failed.task / 'library_state.json')
    assert file_bytes(failed.task) == before
    write_json(failed.task / 'watched_windows.json', [])
    with pytest.raises(LibraryStopped, match='visual_seed_changed'):
        cli.resume_config(output, failed.ref)


@pytest.mark.parametrize('watched', [False, True])
def test_failed_before_planning_is_refused(failed, watched):
    failed.state['calls'] = failed.state['calls'][:3]
    write_json(failed.task / 'library_state.json', failed.state)
    if not watched:
        write_json(failed.task / 'watched_windows.json', [])
    with pytest.raises(LibraryStopped, match='failed before planning'):
        cli.resume_config(failed.task, failed.ref)


@pytest.mark.parametrize('command', ['start', '_run'])
@pytest.mark.parametrize('flags', [[], ['--creative-audio', '--material-search', 'question_first']])
def test_cli_rejects_noncreative_and_question_first(command, flags, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main([command, '--reference', 'ref', '--library', 'films', '--output', 'out',
                  '--resume-from', 'failed', *flags])
    assert error.value.code == 2
    assert '--resume-from requires --creative-audio' in capsys.readouterr().err


def test_detached_command_implies_parent_and_worker_pins_checkpoint(failed, monkeypatch):
    home = failed.root / 'home'
    write_json(home / 'server.json', dict(project_root=str(failed.root), mcp_package_root='/fake',
        opencode_executable='/fake'))
    write_json(home / 'credentials.json', dict(Z_AI_API_KEY='synthetic', DASHSCOPE_API_KEY='synthetic'))
    (home / 'credentials.json').chmod(0o600)
    commands, options = [], []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda command, *args:
        commands.append(command) or dict(state='preflight_only'))
    monkeypatch.setattr(clean_chain, 'execute', lambda *args, **kwargs:
        options.append(kwargs) or dict(usage=dict(requests=0)))
    output = failed.task / 'evaluations/resume'
    args = ['--home', str(home), 'start', '--reference', str(failed.ref), '--library', str(failed.library),
        '--output', str(output), '--creative-audio', '--resume-from', str(failed.task)]
    assert cli.main(args) == 0
    command = commands[0]
    assert command[command.index('--resume-from') + 1] == str(failed.task)
    assert command[command.index('--parent-task') + 1] == str(failed.task)
    assert cli.main(command[3:]) == 0
    config = options[0]['provider_config']
    assert config['resume_from']['failed_task'] == str(failed.task)
    assert options[0]['reference_seed'] == config['resume_from']['reference_seed']
    assert config['parent_baseline']['request_count'] == 6


def test_record_integrity_failures_never_trigger_a_new_plan(failed, monkeypatch):
    config = cli.resume_config(failed.task, failed.ref)
    write_json(failed.task / 'calls/glm_plan_0_repair2/response.json', dict(changed=True))
    with pytest.raises(LibraryStopped, match='received_plan_record_changed'):
        pipeline._resume_plan(config['resume_from'], lambda plan: None)


def test_restart_keeps_identical_input_lock_and_makes_no_calls(failed, monkeypatch):
    _, _, _, output = run_to_render(failed, monkeypatch)
    before = json.dumps(read(output / 'library_state.json')['input_lock'], ensure_ascii=False)
    artifact, calls, _, _ = run_to_render(failed, monkeypatch, output=output)
    assert calls == [] and artifact['new_plan_calls'] == 0
    assert json.dumps(read(output / 'library_state.json')['input_lock'], ensure_ascii=False) == before


def test_resume_of_resume_tries_child_attempts_before_all_ancestor_attempts(failed, monkeypatch):
    _, _, _, output = run_to_render(failed, monkeypatch)
    state = read(output / 'library_state.json')
    state['calls'] = [receipt(output, 'plan_0', failed.invalid, failed.ref)]
    state['request_count'] = 1
    write_json(output / 'library_state.json', state)
    write_json(output / '.omni-server/job.json', dict(job_id='child', state='failed'))
    config = cli.resume_config(output, failed.ref)
    seeds = config['resume_from']['plan_seeds']
    assert len(seeds) == 4
    assert seeds[0]['ledger']['path'] == str(output / 'library_state.json')
    def check(value):
        if value['segments'][0]['window_id'] != 'watched':
            raise ValueError('plan:unknown_window')
    _, selected, rejected = pipeline._resume_plan(config['resume_from'], check)
    assert selected['call_id'] == 'glm_plan_0_repair'
    assert len(rejected) == 2


@pytest.mark.parametrize('valid_received', [False, True])
def test_uncertain_plan_is_never_replayed(failed, valid_received):
    failed.state['calls'].append(dict(id='unknown', name='plan_0_repair2', status='uncertain'))
    write_json(failed.task / 'library_state.json', failed.state)
    config = cli.resume_config(failed.task, failed.ref)
    assert all(seed['call_id'] != 'unknown' for seed in config['resume_from']['plan_seeds'])
    def check(value):
        if not valid_received or value['segments'][0]['window_id'] != 'watched':
            raise ValueError('plan:invalid')
    if valid_received:
        assert pipeline._resume_plan(config['resume_from'], check)[1]['call_id'] == 'glm_plan_0_repair'
    else:
        with pytest.raises(LibraryStopped, match='unknown_plan_outcome_no_replay'):
            pipeline._resume_plan(config['resume_from'], check)


def test_known_failed_plan_without_reply_can_start_fresh(failed, monkeypatch):
    failed.state['calls'] = failed.state['calls'][:3] + [
        dict(id='known_failed', name='plan_0', status='failed_known')]
    write_json(failed.task / 'library_state.json', failed.state)
    # This checkpoint reached planning; unlike a pre-plan failure it is resumable.
    config = cli.resume_config(failed.task, failed.ref)
    assert config['resume_from']['plan_seeds'] == []


def test_accepted_reference_must_match_reading_and_received_record_hash(failed):
    write_json(failed.task / 'reference_reading.json', dict(failed.reading, theme='unreceived meaning'))
    with pytest.raises(LibraryStopped, match='accepted_received_reference_required'):
        cli.resume_config(failed.task, failed.ref)
    write_json(failed.task / 'reference_reading.json', failed.reading)
    write_json(failed.task / 'calls/glm_reference_repair/response.json', dict(changed=True))
    with pytest.raises(LibraryStopped, match='reference_cache_original_record_changed'):
        cli.resume_config(failed.task, failed.ref)


def test_revalidation_does_not_change_recorded_bytes(failed):
    config = cli.resume_config(failed.task, failed.ref)
    before = file_bytes(failed.task)
    def validator(value):
        value['derived_field'] = 'current code'
        if value['segments'][0]['window_id'] != 'watched':
            raise ValueError('plan:unknown_window')
    first = pipeline._resume_plan(config['resume_from'], validator)
    second = pipeline._resume_plan(config['resume_from'], validator)
    assert first == second
    assert first[1]['call_id'] == 'glm_plan_0_repair'
    assert file_bytes(failed.task) == before
