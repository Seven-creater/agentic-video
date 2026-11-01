"""Offline coverage for checkpoint admission and the picture-first experiment."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, contracts, pipeline, prompts, protocol_feedback, server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_resume_from import failed, read, receipt
from test_server_cli import home
from test_protocol_feedback import harness as model_harness


def visual_script():
    return [dict(beat_id=f'b{i}', who='店主', to_whom='客人', action=action,
                 emotion='坚定而关切', outcome=outcome, why_silent_readable='手势、表情和行动结果连贯可见')
            for i, (action, outcome) in enumerate([
                ('拦下过多的订单', '客人停下递单'), ('认真完成手头一单', '客人满意点头'),
                ('客人带朋友回到店里', '店主与客人相视微笑')])]


def picture_plan():
    return dict(slots=[dict(slot_id='s', segment_ids=['seg'], visual_beat_ids=['b0', 'b1', 'b2'])],
                segments=[dict(segment_id='seg', window_id='w', role_ids=['A'])],
                audio_draft=[dict(id='line', text='认真做好。', mode='narration', picture_relation='parallel',
                                  segment_ids=['seg'], voice='Ethan')])


def test_picture_first_prompt_rules_and_default_passthrough():
    context = dict(creative_style='picture_first', visual_script=visual_script())
    search = prompts.picture_first_search_prompt(prompts.search_prompt(context), context)
    plan = prompts.picture_first_plan_prompt(prompts.plan_prompt(context), context)
    assert all(word in search for word in ('visual_script', '3–8', 'to_whom', '连续', '不写对白', '不复制参考'))
    assert all(word in plan for word in ('visual_beat_ids', 'picture_relation', *contracts.PICTURE_RELATIONS,
                                       '路才越走越宽', '熊猫', '再对着选定画面写'))
    for context in ({}, {'creative_audio': True}):
        assert prompts.picture_first_search_prompt(prompts.search_prompt(context), context) == prompts.search_prompt(context)
        assert prompts.picture_first_plan_prompt(prompts.plan_prompt(context), context) == prompts.plan_prompt(context)
    blind = prompts.blind_prompt(4)
    assert 'visual_script' not in blind and 'picture_relation' not in blind and '没有剧本' in blind
    check = prompts.silent_check_prompt(visual_script(), dict(observed_story='visible actions'))
    assert 'audio_draft' not in check


def test_default_prompt_bytes_match_983c38c():
    context = dict(creative_audio=True, reference_duration_s=4, reference_audio_stream_index=None)
    # Computed from HEAD 983c38c before adding the opt-in wrappers.
    samples = [
        (prompts.search_prompt(context), '224edf874fbce93250f351fcff76c679f397d7f5814f7d33fa9fa3aa980c9942'),
        (prompts.plan_prompt(context), '8c7e20704fa99560e38d26d92cfcb150b8f0ac9b6b3fd164730d8ef874de554a'),
        (prompts.blind_prompt(4), 'f4cdd819148dbca450af4c2631875726ff2dc3506a7f1a8692a4f82176413f11'),
        (prompts.reference_prompt('a' * 64, 4), '544a728336a13003f44df988b088a7c7200187ae146c346748047a1652c64f7c')]
    assert all(hashlib.sha256(prompt.encode()).hexdigest() == digest for prompt, digest in samples)


def test_default_lock_bytes_match_983c38c(tmp_path, monkeypatch):
    source = dict(source_id='source', sha256='a' * 64, path='fixture.mp4', duration_s=4, audio_stream_index=None)
    monkeypatch.setattr(pipeline, '_catalog', lambda *a: dict(sources=[source]))
    class Captured(Exception):
        pass
    def factory(state):
        assert json_sha(state.input_lock) == '06f92fc7c302a2041799ad1fecfdcc3461306c8e2f1db827e162e011873802d5'
        assert 'creative_style' not in state.input_lock['configuration']
        raise Captured()
    with pytest.raises(Captured):
        pipeline.execute(tmp_path / 'ref', tmp_path / 'library', tmp_path / 'run', asr=False, max_fine=None,
            model_factory=factory, provider_config=dict(workflow='reference_rough_skill_v1', creative_audio=True))


def test_other_style_validator_does_not_require_picture_fields(failed):
    plan = deepcopy(failed.plan)
    plan['segments'][0]['source_id'] = 'film'
    catalog = dict(sources=[dict(source_id='film', sha256=sha256_file(failed.source), duration_s=120)])
    options = dict(allow_unfocused=True, require_audio_draft=True, reference=failed.reading)
    contracts.validate_plan(plan, catalog, [failed.window], sha256_file(failed.ref), 4, **options)
    with pytest.raises(ValueError, match='visual_beat_ids_required'):
        contracts.validate_plan(plan, catalog, [failed.window], sha256_file(failed.ref), 4,
            creative_style='picture_first', visual_script=visual_script(), **options)


def test_visual_script_uses_the_existing_one_repair_budget(tmp_path, monkeypatch):
    valid = dict(reason='continuous scene', windows=[], visual_script=visual_script())
    model, media, sent = model_harness(tmp_path, monkeypatch, [dict(reason='scene', windows=[]), valid])
    def validate(value):
        contracts.validate_search(value, dict(sources=[dict(source_id='film', sha256='a' * 64, duration_s=120)]), allow_empty=True)
        contracts.validate_visual_script(value.get('visual_script'))
    result = model.call('search_0', prompts.picture_first_search_prompt('search', dict(creative_style='picture_first')),
                        media, validate, image=True)
    assert result == valid and len(sent) == 2
    assert [call['name'] for call in model.state.data['calls']] == ['search_0', 'search_0_repair']


def test_production_text_transport_is_text_only_repaired_recorded_and_reused(tmp_path, monkeypatch):
    import subprocess
    model, _, _ = model_harness(tmp_path, monkeypatch, [], provider=clean_chain.PROVIDER['provider'])
    model.queue = model.output / 'mcp_queue'
    model.queue.mkdir()
    model.executable, model.package_root, model.timeout_s = '/fake/opencode', tmp_path, 1
    valid = dict(beats=[dict(beat_id=beat['beat_id'], told=True, evidence='visible action') for beat in visual_script()],
                 scene_jumps_confusing=False)
    replies = [dict(valid, scene_jumps_confusing='false'), valid]
    sent = []
    def run(argv, **kwargs):
        assert argv[0] == '/fake/opencode'
        config = json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT'])
        assert config['mcp'] == {} and config['permission'] == {'*': 'deny'}
        sent.append(argv)
        events = [dict(type='text', part=dict(text=json.dumps(replies.pop(0)))),
                  dict(type='step_finish', part=dict(tokens=dict(input=100, output=30)))]
        kwargs['stdout'].write(('\n'.join(json.dumps(event) for event in events) + '\n').encode())
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    validator = lambda value: contracts.validate_silent_check(value, visual_script())
    prompt = prompts.silent_check_prompt(visual_script(), dict(observed_story='visible'))
    assert model.call_text('silent_check_0', prompt, validator) == valid
    assert model.call_text('silent_check_0', 'changed prompt is ignored on resume', validator) == valid
    assert len(sent) == 2
    assert [call['name'] for call in model.state.data['calls']] == ['silent_check_0', 'silent_check_0_repair']
    for call in model.state.data['calls']:
        request = read(model.output / 'calls' / call['id'] / 'request.json')
        assert request['tool'] == 'text_completion' and set(request['arguments']) == {'prompt'}
        assert call['usage'] == dict(prompt_tokens=100, completion_tokens=30)


@pytest.mark.parametrize('mutation', ['missing', 'empty', 'wrong_type', 'duplicate', 'too_few', 'too_many'])
def test_visual_script_shape(mutation):
    beats = visual_script()
    if mutation == 'missing':
        del beats[0]['who']
    elif mutation == 'empty':
        beats[0]['action'] = ' '
    elif mutation == 'wrong_type':
        beats[0]['emotion'] = []
    elif mutation == 'duplicate':
        beats[1]['beat_id'] = beats[0]['beat_id']
    elif mutation == 'too_few':
        beats.pop()
    else:
        beats *= 3
    with pytest.raises(ValueError):
        contracts.validate_visual_script(beats)


@pytest.mark.parametrize('relation', [None, 'literal', '', {}, []])
def test_missing_or_invalid_picture_relation_has_allowed_value_feedback(relation):
    plan = picture_plan()
    if relation is None:
        del plan['audio_draft'][0]['picture_relation']
    else:
        plan['audio_draft'][0]['picture_relation'] = relation
    with pytest.raises(ValueError, match='picture_relation_required') as caught:
        contracts.validate_picture_first_plan(plan, visual_script())
    feedback = protocol_feedback.diagnose('plan_0', plan, dict(creative_style='picture_first',
        visual_script=visual_script(), validation_error=caught.value))
    assert any(row['path'] == '$.audio_draft[0].picture_relation' and
               row['expected'] == list(contracts.PICTURE_RELATIONS) for row in feedback)


def test_sync_speech_requires_dialogue_and_visible_speaker_in_its_own_segments():
    plan = picture_plan()
    line = plan['audio_draft'][0]
    line['picture_relation'] = 'sync_speech'
    with pytest.raises(ValueError, match='sync_speech_requires_dialogue'):
        contracts.validate_picture_first_plan(plan, visual_script())
    line.update(mode='dialogue', speaker=dict(window_id='w', role_id='A'))
    contracts.validate_picture_first_plan(plan, visual_script())
    plan['segments'].insert(0, dict(segment_id='anchor', window_id='w', role_ids=['A']))
    plan['segments'][1]['role_ids'] = ['B']
    line['identity_anchor_segment_id'] = 'anchor'
    contracts.validate_dialogue_speakers([line], plan['segments'])
    with pytest.raises(ValueError, match='speaker_not_in_selected_segments'):
        contracts.validate_picture_first_plan(plan, visual_script())


def test_every_visual_beat_must_be_referenced_by_nonempty_slot():
    plan = picture_plan()
    plan['slots'][0]['visual_beat_ids'].pop()
    with pytest.raises(ValueError, match='unreferenced_visual_beat') as caught:
        contracts.validate_picture_first_plan(plan, visual_script())
    assert caught.value.violation['expected']['untold_beat_ids'] == ['b2']
    plan['slots'].append(dict(slot_id='empty', visual_beat_ids=['b2'], segment_ids=[]))
    with pytest.raises(ValueError, match='unreferenced_visual_beat'):
        contracts.validate_picture_first_plan(plan, visual_script())


@pytest.mark.parametrize('command', ['start', '_run'])
@pytest.mark.parametrize('flags', [[], ['--creative-audio', '--material-search', 'question_first']])
def test_picture_first_cli_rejects_incompatible_modes(command, flags, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main([command, '--reference', 'ref', '--library', 'films', '--output', 'out',
                  '--creative-style', 'picture_first', *flags])
    assert error.value.code == 2
    assert 'picture_first requires --creative-audio' in capsys.readouterr().err


@pytest.mark.parametrize('style', [None, 'default', 'picture_first'])
@pytest.mark.parametrize('mode', ['library', 'film'])
def test_detached_style_forwarding_and_fresh_default(home, monkeypatch, style, mode):
    ref = home / 'ref.mp4'
    ref.write_bytes(b'ref')
    library = home / 'films'
    library.mkdir()
    (library / 'film.mp4').write_bytes(b'film')
    write_json(home / 'credentials.json', dict(Z_AI_API_KEY='synthetic', DASHSCOPE_API_KEY='synthetic'))
    (home / 'credentials.json').chmod(0o600)
    commands, options = [], []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda argv, *a: commands.append(argv) or dict(state='starting'))
    monkeypatch.setattr(clean_chain, 'execute', lambda *a, **k: options.append(k) or dict(usage={}))
    argv = ['--home', str(home), 'start', '--reference', str(ref), '--library', str(library),
            '--output', str(home / 'new'), '--creative-audio', '--material-search', mode]
    if style:
        argv += ['--creative-style', style]
    assert cli.main(argv) == 0
    command = commands[0]
    expected = 'picture_first' if style == 'picture_first' else 'picture_aware'
    assert command[command.index('--creative-style') + 1] == expected
    assert cli.main(command[3:]) == 0
    actual = options.pop()['provider_config']
    assert actual.get('creative_style') == expected
    if style != 'picture_first':
        plain = [arg for arg in argv]
        if style:
            plain = plain[:-2]
        plain[2] = '_run'
        assert cli.main(plain) == 0
        assert actual == options[-1]['provider_config']


class ReachedTTS(Exception):
    pass


@pytest.fixture
def experiment(failed, monkeypatch):
    from omni_story.library import visual_story_trial
    events, prompts_seen, sent = [], {}, []
    decisions, jumps = [True], [False, False]
    failed.plan['slots'][0]['visual_beat_ids'] = ['b0', 'b1', 'b2']
    failed.plan['audio_draft'][0]['picture_relation'] = 'parallel'
    failed.state['calls'][4] = receipt(failed.task, 'plan_0_repair', failed.plan, failed.ref)
    write_json(failed.task / 'library_state.json', failed.state)
    config = cli.resume_config(failed.task, failed.ref)
    config.update(creative_audio=True, creative_style='picture_first', material_search='library',
                  workflow='reference_rough_skill_v1')
    ref = dict(source_id='ref', sha256=sha256_file(failed.ref), path=str(failed.ref), duration_s=4, audio_stream_index=None)
    source = dict(source_id='film', sha256=sha256_file(failed.source), path=str(failed.source), duration_s=120, audio_stream_index=None)
    def catalog(path, folder):
        result = dict(sources=[deepcopy(ref if path == failed.ref else source)])
        write_json(Path(folder) / 'inventory.json', result)
        return result
    def inventory(path, *args):
        return dict(sources=[dict(source_id='actual', path=str(path), sha256=sha256_file(path), duration_s=4)])
    def proxy(source, start, end, cache, **kwargs):
        path = Path(cache) / (kwargs['label'] + '.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'silent actual render proxy:' + Path(source['path']).read_bytes())
        return dict(path=str(path))
    def render(catalog, plan, folder, **kwargs):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'actual.mp4'
        path.write_bytes(json.dumps(plan['segments'], sort_keys=True).encode())
        result = dict(rendered_path=str(path), sha256=sha256_file(path), measured_duration_s=4, provenance=[])
        write_json(folder / 'render_result.json', result)
        events.append(folder.name)
        return result
    monkeypatch.setattr(pipeline, '_catalog', catalog)
    monkeypatch.setattr(pipeline, 'inventory_sources', inventory)
    monkeypatch.setattr(clean_chain, 'inventory_sources', inventory)
    monkeypatch.setattr(visual_story_trial, 'proxy', proxy)
    monkeypatch.setattr(pipeline, 'create_contact_sheet', lambda *a, **k: dict(path=str(failed.ref)))
    monkeypatch.setattr(pipeline, '_adaptive_coarse', lambda *a, **k: pytest.fail('must reuse watched windows'))
    monkeypatch.setattr(pipeline, '_validate_question_first_windows', lambda *a: None)
    monkeypatch.setattr(clean_chain, 'render_library_video', render)
    monkeypatch.setattr(clean_chain, 'build_rough_context', lambda *a, **k: dict(planned=True))
    def tts(*a, **k):
        events.append('TTS')
        raise ReachedTTS()
    monkeypatch.setattr(clean_chain, 'synthesize_draft', tts)
    class Model(pipeline.CodexMCP):
        provider = clean_chain.PROVIDER['provider']
        def __init__(self, state):
            self.state, self.output, self.exclusions = state, state.output, []
        def call(self, name, prompt, media, validator, **kwargs):
            if media is not None and kwargs.get('scope') is None:
                kwargs['scope'] = dict(kind='sparse_contact_sheet', source_sha256=sha256_file(media), source_start_s=0, source_end_s=4)
            return super().call(name, prompt, media, validator, **kwargs)
        def _fake_submit(self, name, request, *, repair_of=None):
            self.state._reload()
            prior = next((c for c in self.state.data['calls'] if c['request_sha256'] == json_sha(request)), None)
            if prior:
                return pipeline.CodexMCP._submit(self, name, request, repair_of=repair_of)
            prompts_seen[name] = request['arguments']['prompt']
            sent.append(name)
            if name.startswith('search_'):
                assert name == 'search_0', 'silent revision cannot search'
                value = dict(reason='同一场戏讲完整的关系变化', windows=[], visual_script=visual_script())
            elif name == 'plan_1':
                value = deepcopy(failed.plan)
                value['segments'][0].update(source_in_s=104, source_out_s=108)
                assert 'untold_beats' in prompts_seen[name] and 'observed_story' in prompts_seen[name]
            elif name.startswith('blind_'):
                events.append(name)
                assert 'audio_draft' not in prompts_seen[name] and 'visual_script' not in prompts_seen[name]
                value = dict(observed_story='店主婉拒递单，认真做事，客人带朋友回来。', apparent_theme='用原则赢得信任',
                    evidence=[dict(start_s=0, end_s=4, observed_fact='连续的一场店内互动')], main_characters=['店主', '客人'], confusions=[])
            elif name.startswith('silent_check_'):
                events.append(name)
                assert request['tool'] == 'text_completion' and set(request['arguments']) == {'prompt'}
                index = int(name.rsplit('_', 1)[1])
                value = dict(beats=[dict(beat_id=beat['beat_id'], told=decisions[index], evidence='盲看见到了动作与结果')
                                    for beat in visual_script()], scene_jumps_confusing=jumps[index])
            else:
                pytest.fail('unexpected stage ' + name)
            call, _ = self.state.begin_call(name, request, repair_of=repair_of)
            response = dict(result=dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))]))
            self.state.complete_call(call, response)
            return call, response
        _submit = _fake_submit
        _submit_text = _fake_submit
    output = failed.root / 'picture'
    def run_chain():
        return clean_chain.execute(failed.ref, failed.library, output, model_factory=Model,
            provider_config=config, reference_seed=config['resume_from']['reference_seed'], asr=False)
    def run_rough(target=None, checkpoint=None):
        actual_config = {**config, **(checkpoint or {})}
        return pipeline.execute(failed.ref, failed.library, target or output, model_factory=Model,
            provider_config={**clean_chain.PROVIDER, **actual_config}, reference_seed=actual_config['resume_from']['reference_seed'],
            asr=False, max_fine=None, max_requests=None, render_fn=render)
    return SimpleNamespace(run_chain=run_chain, run_rough=run_rough, decisions=decisions, jumps=jumps, events=events,
        prompts=prompts_seen, sent=sent, output=output, config=config, failed=failed)


@pytest.mark.parametrize('decisions', [[True], [False, True], [False, False]])
@pytest.mark.parametrize('mode', ['library', 'film'])
def test_silent_gate_controls_actual_tts_entry(experiment, decisions, mode):
    experiment.decisions[:] = decisions
    experiment.config['material_search'] = mode
    expected = ReachedTTS if decisions[-1] else LibraryStopped
    with pytest.raises(expected) as caught:
        experiment.run_chain()
    assert experiment.events.count('TTS') == int(decisions[-1])
    assert ('plan_1' in experiment.sent) == (len(decisions) == 2)
    assert experiment.sent.count('search_0') == 1
    gate = read(experiment.output / 'silent_gate.json')
    assert [check['passed'] for check in gate['checks']] == decisions
    assert all(check['blind_reading']['observed_story'] for check in gate['checks'])
    assert gate['passed'] == decisions[-1]
    assert read(experiment.output / 'library_state.json')['input_lock']['configuration']['creative_style'] == 'picture_first'
    assert read(experiment.output / 'visual_script.json') == visual_script()
    if decisions[-1]:
        assert experiment.events[-1] == 'TTS'
    else:
        assert str(caught.value) == 'silent_story_not_readable'


def test_resume_reuses_recorded_blind_and_text_check_without_new_requests(experiment):
    experiment.decisions[:] = [False, True]
    first = experiment.run_rough()
    before = list(experiment.sent)
    state = read(experiment.output / 'library_state.json')
    second = experiment.run_rough()
    assert first['selected_round'] == second['selected_round'] == 1
    assert experiment.sent == before
    assert read(experiment.output / 'library_state.json')['request_count'] == state['request_count']
    assert len(read(experiment.output / 'silent_gate.json')['checks']) == 2


def test_confusing_scene_jumps_require_one_revision_even_when_all_beats_are_told(experiment):
    experiment.decisions[:] = [True, True]
    experiment.jumps[:] = [True, False]
    result = experiment.run_rough()
    assert result['selected_round'] == 1 and experiment.sent.count('plan_1') == 1
    gate = read(experiment.output / 'silent_gate.json')
    assert [row['passed'] for row in gate['checks']] == [False, True]


def test_tts_gate_rejects_changed_render_or_gate_record(experiment):
    result = experiment.run_rough()
    state = SimpleNamespace(output=experiment.output, data=read(experiment.output / 'library_state.json'))
    with pytest.raises(LibraryStopped, match='silent_story_not_readable'):
        pipeline.require_silent_gate(state, dict(result, final_sha256='changed'))
    gate = read(experiment.output / 'silent_gate.json')
    gate['checks'][0]['verdict']['beats'][0]['told'] = False
    write_json(experiment.output / 'silent_gate.json', gate)
    with pytest.raises(LibraryStopped, match='silent_gate_record_changed'):
        pipeline.require_silent_gate(state, result)


def test_resume_from_child_reuses_parent_silent_receipts(experiment):
    experiment.run_rough()
    before = list(experiment.sent)
    write_json(experiment.output / '.omni-server/job.json', dict(job_id='parent', state='failed'))
    checkpoint = cli.resume_config(experiment.output, experiment.failed.ref)
    child = experiment.output / 'evaluations/resume'
    result = experiment.run_rough(child, checkpoint)
    assert result['selected_round'] == 0 and experiment.sent == before
    state = read(child / 'library_state.json')
    assert state['request_count'] == 0
    assert len(state['artifacts']['picture_first_reply_reuse']) == 2
    pipeline.require_silent_gate(SimpleNamespace(output=child, data=state), result)


@pytest.mark.parametrize('decoration', ['music', 'strategy'])
def test_resume_accepted_decorated_reference(failed, decoration):
    accepted = deepcopy(failed.reading)
    stored = deepcopy(accepted)
    config = failed.state['input_lock']['configuration']
    if decoration == 'music':
        accepted['strategy']['timing_driver'] = 'music'
        accepted['strategy']['speech_mode'] = 'none'
        stored = dict(accepted, audio_delivery=dict(kind='reference_original_soundtrack', path=str(failed.ref)))
    else:
        config.update(reference_strategy=dict(adaptation_mode='reinterpret', speech_mode='mixed', timing_driver='action'),
                      reference_speech_form='角色对话')
        stored.update(strategy=config['reference_strategy'], speech_form=config['reference_speech_form'])
    failed.state['calls'][1] = receipt(failed.task, 'reference_repair', accepted, failed.ref)
    write_json(failed.task / 'calls/glm_reference_repair/parsed.json', accepted)
    write_json(failed.task / 'reference_reading.json', stored)
    write_json(failed.task / 'library_state.json', failed.state)
    result = cli.resume_config(failed.task, failed.ref)
    assert result['resume_from']['reference_seed']['source_call_id'] == 'glm_reference_repair'


def test_resume_never_selects_rejected_reference_hidden_by_override(failed):
    config = failed.state['input_lock']['configuration']
    config['reference_strategy'] = dict(failed.reading['strategy'])
    rejected = dict(failed.reading, strategy=dict(speech_mode='invalid'))
    failed.state['calls'][1] = receipt(failed.task, 'reference_repair', rejected, failed.ref)
    write_json(failed.task / 'calls/glm_reference_repair/protocol_failure.json', dict(error='invalid strategy'))
    write_json(failed.task / 'library_state.json', failed.state)
    with pytest.raises(LibraryStopped, match='accepted_received_reference_required'):
        cli.resume_config(failed.task, failed.ref)


def test_resume_preplan_watched_refusal_is_accurate(failed):
    failed.state['calls'] = failed.state['calls'][:3]
    write_json(failed.task / 'library_state.json', failed.state)
    with pytest.raises(LibraryStopped, match='failed before planning') as caught:
        cli.resume_config(failed.task, failed.ref)
    assert 'no watched checkpoint' not in str(caught.value)
