"""Picture-aware defaults, content checks and advisory review, entirely offline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, contracts, pipeline, prompts, protocol_feedback, server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_picture_first import picture_plan, ReachedTTS
from test_resume_from import failed, read, receipt
from test_server_cli import home
from test_clean_chain import harness as chain_harness


@pytest.mark.parametrize('style', [None, 'picture_aware', 'classic', 'picture_first'])
@pytest.mark.parametrize('mode', ['library', 'film'])
def test_fresh_cli_records_style_in_input_lock_and_detached_command(home, monkeypatch, style, mode):
    ref, library, output = home / 'ref.mp4', home / 'films', home / 'run'
    ref.write_bytes(b'reference')
    library.mkdir()
    (library / 'film.mp4').write_bytes(b'film')
    write_json(home / 'credentials.json', dict(Z_AI_API_KEY='synthetic', DASHSCOPE_API_KEY='synthetic'))
    (home / 'credentials.json').chmod(0o600)
    commands, locks = [], []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda command, *a: commands.append(command) or {})
    source = dict(source_id='source', sha256='a' * 64, path='fixture.mp4', duration_s=4, audio_stream_index=None)
    monkeypatch.setattr(pipeline, '_catalog', lambda *a: dict(sources=[source]))
    class Captured(Exception):
        pass
    def factory(state):
        locks.append(state.input_lock)
        raise Captured()
    def execute(reference, library, output, **options):
        return pipeline.execute(reference, library, output, max_fine=None, asr=False,
            provider_config=options['provider_config'], model_factory=factory)
    monkeypatch.setattr(clean_chain, 'execute', execute)
    argv = ['--home', str(home), 'start', '--reference', str(ref), '--library', str(library),
            '--output', str(output), '--creative-audio', '--material-search', mode]
    if style:
        argv += ['--creative-style', style]
    cli.main(argv)
    command = commands[0]
    expected = style or 'picture_aware'
    assert command[command.index('--creative-style') + 1] == expected
    with pytest.raises(Captured):
        cli.main(command[3:])
    assert locks[0]['configuration']['creative_style'] == expected
    assert read(output / 'library_state.json')['input_lock'] == locks[0]


@pytest.mark.parametrize('style', [None, 'classic', 'picture_first', 'picture_aware'])
def test_old_lock_and_recovery_keep_the_recorded_style(tmp_path, style):
    folder = tmp_path / 'old'
    config = dict(creative_audio=True)
    if style:
        config['creative_style'] = style
    write_json(folder / 'library_state.json', dict(input_lock=dict(configuration=config)))
    before = (folder / 'library_state.json').read_bytes()
    args = SimpleNamespace(output=folder, creative_audio=True)
    assert cli._creative_style(args) == style  # same-task/capacity recovery
    args.output, args.resume_from = tmp_path / 'new', folder
    assert cli._creative_style(args) == style  # detached child resume
    assert (folder / 'library_state.json').read_bytes() == before
    assert 'creative_style' not in config if style is None else config['creative_style'] == style


def test_classic_prompt_bytes_and_picture_aware_search_delta():
    context = dict(creative_audio=True, reference_duration_s=4, reference_audio_stream_index=None)
    for factory, wrapper, digest in [
        (prompts.search_prompt, prompts.picture_first_search_prompt, '224edf874fbce93250f351fcff76c679f397d7f5814f7d33fa9fa3aa980c9942'),
        (prompts.plan_prompt, prompts.picture_first_plan_prompt, '8c7e20704fa99560e38d26d92cfcb150b8f0ac9b6b3fd164730d8ef874de554a')]:
        base = factory(context)
        assert hashlib.sha256(wrapper(base, dict(creative_style='classic')).encode()).hexdigest() == digest
    search = prompts.picture_first_search_prompt('old search', dict(creative_style='picture_aware'))
    assert search == 'old search\n优先选择可见动作、人物关系和情绪贴合主旨的素材，优先连续场景而非零散镜头。'
    plan = prompts.picture_first_plan_prompt('old plan', dict(creative_style='picture_aware'))
    assert all(term in plan for term in (*contracts.PICTURE_RELATIONS, '规矩立住了，路才越走越宽', '两条', '不要求visual_script'))


@pytest.mark.parametrize('relation', [None, 'wrong', [], {}, *contracts.PICTURE_RELATIONS])
def test_picture_relation_validator_and_feedback(relation):
    plan = picture_plan()
    plan['audio_draft'][0]['picture_relation'] = relation
    plan['slots'][0].pop('visual_beat_ids')  # no visual script or beat coverage required
    if relation == 'sync_speech':
        with pytest.raises(ValueError, match='sync_speech_requires_dialogue'):
            contracts.validate_picture_aware_plan(plan)
        plan['audio_draft'][0].update(mode='dialogue', speaker=dict(window_id='w', role_id='A'))
    if relation in contracts.PICTURE_RELATIONS:
        contracts.validate_picture_aware_plan(plan)
    else:
        with pytest.raises(ValueError, match='picture_relation_required') as caught:
            contracts.validate_picture_aware_plan(plan)
        feedback = protocol_feedback.diagnose('plan_0', plan,
            dict(creative_style='picture_aware', validation_error=caught.value))
        assert any(row['path'] == '$.audio_draft[0].picture_relation' and
                   row['expected'] == list(contracts.PICTURE_RELATIONS) for row in feedback)


def test_sync_speech_cannot_use_offscreen_identity_anchor():
    plan = picture_plan()
    plan['segments'].insert(0, dict(segment_id='anchor', window_id='w', role_ids=['B']))
    plan['audio_draft'][0].update(picture_relation='sync_speech', mode='dialogue',
        speaker=dict(window_id='w', role_id='B'), identity_anchor_segment_id='anchor')
    with pytest.raises(ValueError, match='speaker_not_in_selected_segments'):
        contracts.validate_picture_aware_plan(plan)


@pytest.mark.parametrize('single,multiple', [(0, 4), (2, 4), (3, 0), (3, 4)])
def test_only_exact_single_segment_lines_count(single, multiple):
    plan = picture_plan()
    template = plan['audio_draft'][0]
    plan['segments'].append(dict(segment_id='other', window_id='w', role_ids=['A']))
    plan['audio_draft'] = [dict(template, id=f'l{i}', segment_ids=['seg'] if i < single else ['seg', 'other'])
                           for i in range(single + multiple)]
    if single <= 2:
        contracts.validate_picture_aware_plan(plan)
    else:
        with pytest.raises(ValueError, match='too_many_single_segment_lines') as caught:
            contracts.validate_picture_aware_plan(plan)
        feedback = protocol_feedback.diagnose('plan_0', plan,
            dict(creative_style='picture_aware', validation_error=caught.value))
        hint = feedback[0]['hint']
        assert all(term in hint for term in ('seg', 'l0', 'l1', 'l2', '拆分镜头范围', '移动一句'))


@pytest.fixture
def aware(failed, monkeypatch):
    from omni_story.library import visual_story_trial
    sent, events, seen, failures = [], [], {}, {}
    issues, jumps = ['ok'], [False, False]
    roles = [dict(clear=True, consistent=True, evidence='画面与台词交代了店主和客人，各由同一人物扮演。')
             for _ in range(3)]
    failed.plan['audio_draft'][0]['picture_relation'] = 'additive'
    failed.state['calls'][4] = receipt(failed.task, 'plan_0_repair', failed.plan, failed.ref)
    write_json(failed.task / 'library_state.json', failed.state)
    config = cli.resume_config(failed.task, failed.ref)
    config.update(creative_audio=True, creative_style='picture_aware', material_search='library',
                  workflow='reference_rough_skill_v1')
    def catalog(path, folder):
        source_path, ident, duration = (failed.ref, 'ref', 4) if path == failed.ref else (failed.source, 'film', 120)
        result = dict(sources=[dict(source_id=ident, sha256=sha256_file(source_path), path=str(source_path),
                                   duration_s=duration, audio_stream_index=None)])
        write_json(Path(folder) / 'inventory.json', result)
        return result
    def inventory(path, *args):
        return dict(sources=[dict(source_id='actual', path=str(path), sha256=sha256_file(path), duration_s=4)])
    def proxy(source, start, end, cache, **kwargs):
        path = Path(cache) / (kwargs['label'] + '.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'actual silent proxy:' + Path(source['path']).read_bytes())
        return dict(path=str(path))
    def render(catalog, plan, folder, **kwargs):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'actual.mp4'
        path.write_bytes(json.dumps(plan['segments'], sort_keys=True).encode())
        result = dict(rendered_path=str(path), sha256=sha256_file(path), measured_duration_s=4,
            provenance=[dict(output_in_s=0, output_out_s=4)])
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
        def submit(self, name, request, *, repair_of=None):
            self.state._reload()
            if any(call['request_sha256'] == json_sha(request) for call in self.state.data['calls']):
                return pipeline.CodexMCP._submit(self, name, request, repair_of=repair_of)
            sent.append(name)
            seen[name] = request['arguments']['prompt']
            failure = failures.get(name.removesuffix('_repair2').removesuffix('_repair'))
            if failure:
                call, _ = self.state.begin_call(name, request, repair_of=repair_of)
                if failure == 'repair_exhausted':
                    response = dict(result=dict(content=[dict(type='text', text='{}')]))
                    self.state.complete_call(call, response)
                    return call, response
                self.state.fail_call(call, 'simulated_review_failure', uncertain=failure == 'uncertain')
                raise LibraryStopped('opencode_vision_job_failed_no_retry:' + call['id'])
            if name.startswith('plan_'):
                assert name in {'plan_1', 'plan_2'} and '静音审查反馈' in seen[name] and 'issues' in seen[name]
                value = deepcopy(failed.plan)
                start = 104 if name == 'plan_1' else 106
                value['segments'][0].update(source_in_s=start, source_out_s=start + 4)
            elif name.startswith('blind_'):
                events.append(name)
                assert 'audio_draft' not in seen[name] and 'reference_theme' not in seen[name]
                value = dict(observed_story='两个人在店里交流。', apparent_theme='无法判断', main_characters=['店主', '客人'],
                    evidence=[dict(start_s=0, end_s=4, observed_fact='连续店内交流')], confusions=[])
            elif name.startswith('silent_review_'):
                events.append(name)
                assert request['tool'] == 'text_completion' and set(request['arguments']) == {'prompt'}
                assert all(term in seen[name] for term in ('reference_theme', 'watched_observation', 'picture_relation',
                    'start_s', 'end_s', '珍惜陪伴。'))
                index = int(name.rsplit('_', 1)[1])
                value = dict(lines=[dict(line_id='line', issue=issues[index], evidence='店内交流，与本句的关系证据')],
                    scene_jumps_confusing=jumps[index],
                    readable_situation=dict(who='两个人', where='店内', relationship='交流', emotion='平静'))
                if '"content_check":true' in seen[name]:
                    value['roles'] = roles[index]
            else:
                pytest.fail('unexpected stage: ' + name)
            call, _ = self.state.begin_call(name, request, repair_of=repair_of)
            response = dict(result=dict(content=[dict(type='text', text=json.dumps(value, ensure_ascii=False))]))
            self.state.complete_call(call, response)
            return call, response
        _submit = submit
        _submit_text = submit
    output = failed.root / 'aware'
    def rough(target=None, checkpoint=None):
        actual = {**clean_chain.PROVIDER, **config, **(checkpoint or {})}
        return pipeline.execute(failed.ref, failed.library, target or output, model_factory=Model,
            provider_config=actual, reference_seed=actual['resume_from']['reference_seed'], asr=False,
            max_fine=None, max_requests=None, render_fn=render)
    def chain():
        return clean_chain.execute(failed.ref, failed.library, output, model_factory=Model,
            provider_config=config, reference_seed=config['resume_from']['reference_seed'], asr=False)
    return SimpleNamespace(rough=rough, chain=chain, issues=issues, jumps=jumps, roles=roles, sent=sent, events=events,
        seen=seen, failures=failures, output=output, failed=failed, config=config)


@pytest.mark.parametrize('issues', [['ok'], ['invisible_claim'], ['contradiction', 'ok'],
    ['keyword_illustration', 'speaker_mismatch'], ['speaker_mismatch', 'ok']])
@pytest.mark.parametrize('mode', ['library', 'film'])
def test_advisory_revision_once_then_tts_even_with_remaining_issues(aware, issues, mode):
    aware.issues[:] = issues
    aware.config['material_search'] = mode
    with pytest.raises(ReachedTTS):
        aware.chain()
    assert aware.events[-1] == 'TTS' and aware.events.count('TTS') == 1
    assert aware.sent.count('plan_1') == len(issues) - 1
    assert not any(name.startswith('search_') for name in aware.sent)
    review = read(aware.output / 'silent_review.json')
    assert [row['verdict']['lines'][0]['issue'] for row in review['reviews']] == issues
    assert not review['advisory'] and 0 <= review['score'] <= 100
    assert read(aware.output / 'result.json')['silent_review'] == review
    assert not (aware.output / 'visual_script.json').exists()


def test_confusing_jumps_trigger_only_one_revision(aware):
    aware.issues[:] = ['ok', 'ok']
    aware.jumps[:] = [True, True]
    result = aware.rough()
    assert result['selected_round'] == 1 and aware.sent.count('plan_1') == 1


@pytest.mark.parametrize('issues', [['ok'], ['contradiction', 'ok']])
def test_same_task_and_child_resume_reuse_both_review_replies(aware, issues):
    aware.issues[:] = issues
    first = aware.rough()
    sent = list(aware.sent)
    assert aware.rough()['silent_review'] == first['silent_review']
    assert aware.sent == sent
    write_json(aware.output / '.omni-server/job.json', dict(job_id='parent', state='failed'))
    checkpoint = cli.resume_config(aware.output, aware.failed.ref)
    child = aware.output / 'evaluations/resume'
    result = aware.rough(child, checkpoint)
    assert result['silent_review'] == first['silent_review'] and aware.sent == sent
    state = read(child / 'library_state.json')
    assert state['request_count'] == 0
    assert len(state['artifacts']['picture_aware_reply_reuse']) == 2 * len(issues)


def test_unknown_review_never_replayed(aware):
    aware.rough()
    path = aware.output / 'library_state.json'
    saved = read(path)
    saved['calls'][-1].update(status='uncertain')
    write_json(path, saved)
    before = list(aware.sent)
    result = aware.rough()
    assert result['silent_review']['review_unavailable']['call_id'] == saved['calls'][-1]['id']
    assert not result['silent_review']['revision_suggested']
    assert aware.sent == before


@pytest.mark.parametrize('stage', ['blind_0', 'silent_review_0'])
def test_child_resume_does_not_replay_unknown_review(aware, stage):
    aware.rough()
    path = aware.output / 'library_state.json'
    saved = read(path)
    next(call for call in saved['calls'] if call['name'] == stage)['status'] = 'uncertain'
    write_json(path, saved)
    write_json(aware.output / '.omni-server/job.json', dict(job_id='parent', state='failed'))
    checkpoint = cli.resume_config(aware.output, aware.failed.ref)
    before = list(aware.sent)
    result = aware.rough(aware.output / 'evaluations/resume', checkpoint)
    assert 'outcome_unknown_no_replay' in result['silent_review']['review_unavailable']['reason']
    assert aware.sent == before


@pytest.mark.parametrize('stage', ['blind_0', 'silent_review_0'])
@pytest.mark.parametrize('failure', ['failed_known', 'uncertain', 'repair_exhausted'])
def test_unavailable_advisory_continues_to_tts_without_revision_or_replay(aware, stage, failure):
    aware.failures[stage] = failure
    with pytest.raises(ReachedTTS):
        aware.chain()
    result = read(aware.output / 'result.json')
    review = read(aware.output / 'silent_review.json')
    unavailable = review['review_unavailable']
    assert unavailable['call_id'].endswith(stage + ('_repair2' if failure == 'repair_exhausted' else ''))
    assert unavailable['reason']
    assert result['silent_review'] == review
    assert not review['revision_suggested'] and review['score'] is None
    assert 'plan_1' not in aware.sent and aware.events[-1] == 'TTS'
    sent = list(aware.sent)
    assert aware.rough()['silent_review'] == review
    assert aware.sent == sent
    write_json(aware.output / '.omni-server/job.json', dict(job_id='parent', state='failed'))
    checkpoint = cli.resume_config(aware.output, aware.failed.ref)
    result = aware.rough(aware.output / 'evaluations/resume', checkpoint)
    assert result['silent_review']['review_unavailable']['call_id'] == unavailable['call_id']
    assert aware.sent == sent


def test_pre_compaction_review_replays_exact_record_and_prompt(aware, monkeypatch):
    with monkeypatch.context() as patch:
        # Bootstrap the historical schema; replay must then detect it on its own.
        patch.setattr(pipeline, '_recorded_picture_aware_review', lambda *a: dict(verdict={}))
        first = aware.rough()
    sent = list(aware.sent)
    record = (aware.output / 'silent_review.json').read_bytes()
    files = {path: path.read_bytes() for path in (aware.output / 'calls').glob('*/*.json')}
    assert aware.rough()['silent_review'] == first['silent_review']
    assert (aware.output / 'silent_review.json').read_bytes() == record
    assert all(path.read_bytes() == saved for path, saved in files.items())
    write_json(aware.output / '.omni-server/job.json', dict(job_id='parent', state='failed'))
    checkpoint = cli.resume_config(aware.output, aware.failed.ref)
    child = aware.output / 'evaluations/resume'
    assert aware.rough(child, checkpoint)['silent_review'] == first['silent_review']
    assert (child / 'silent_review.json').read_bytes() == record
    assert aware.sent == sent


def test_run_outcome_contains_advisory_reviews_even_on_later_failure(aware, monkeypatch):
    aware.rough()
    def run(args):
        raise ReachedTTS()
    monkeypatch.setattr(cli, '_run_task', run)
    with pytest.raises(ReachedTTS):
        cli._run(SimpleNamespace(output=aware.output))
    assert read(aware.output / 'run_outcome.json')['silent_review'] == read(aware.output / 'silent_review.json')


def test_chain_result_includes_reviews_and_score(chain_harness, monkeypatch):
    case = chain_harness()
    # Stop at a pre-existing later quality decision; advisory metadata must still be delivered.
    case['review']['theme_status'] = 'fail'
    case['review']['missing_core_meaning'] = []
    case['arguments']['provider_config'] = dict(creative_audio=True, creative_style='picture_aware')
    original = pipeline.execute
    review = dict(advisory=True, reviews=[dict(round=0, score=50), dict(round=1, score=50)], score=50)
    def execute(*args, **kwargs):
        rough = original(*args, **kwargs)
        write_json(case['arguments']['output'] / 'silent_review.json', review)
        return rough
    monkeypatch.setattr(pipeline, 'execute', execute)
    result = clean_chain.execute(**case['arguments'])
    assert result['silent_review'] == review
    assert read(case['arguments']['output'] / 'chain_result.json')['silent_review'] == review
    assert result['cache_bindings'][result['silent_review_path']] == sha256_file(result['silent_review_path'])


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'issue', 'boolean', 'situation'])
def test_silent_review_reply_schema(mutation):
    value = dict(lines=[dict(line_id='line', issue='ok', evidence='可见交流')], scene_jumps_confusing=False,
                 readable_situation=dict(who='两个人', where='店里', relationship='交流', emotion='平静'))
    contracts.validate_silent_review(value, [dict(id='line')])
    if mutation == 'missing':
        value['lines'] = []
    elif mutation == 'duplicate':
        value['lines'] *= 2
    elif mutation == 'unknown':
        value['lines'][0]['line_id'] = 'unknown'
    elif mutation == 'issue':
        value['lines'][0]['issue'] = []
    elif mutation == 'boolean':
        value['scene_jumps_confusing'] = 'false'
    else:
        value['readable_situation'].pop('emotion')
    with pytest.raises(ValueError):
        contracts.validate_silent_review(value, [dict(id='line')])


def test_review_timeline_uses_actual_render_times_and_observation_source_offsets(tmp_path, monkeypatch):
    plan = dict(fps=30, segments=[
        dict(segment_id='a', window_id='w', source_in_s=100, source_out_s=104, speed=2),
        dict(segment_id='b', window_id='w', source_in_s=104, source_out_s=106, speed=1)],
        audio_draft=[dict(id='line', text='两人商量。', mode='narration', picture_relation='additive',
                          segment_ids=['a', 'b'])])
    window = dict(window_id='w', source_id='film', source_start_s=100, source_end_s=110,
                  observation=dict(events=[dict(local_start_s=0, local_end_s=6, observed_fact='两人交流')]))
    state = SimpleNamespace(output=tmp_path, input_lock=dict(configuration={}), data=dict(artifacts={}),
                            set_artifact=lambda *a: None)
    blind = dict(observed_story='两人交流')
    monkeypatch.setattr(pipeline, '_silent_blind_reading', lambda *a: blind)
    seen = []
    def check(name, prompt, validator):
        seen.append((name, prompt))
        value = dict(lines=[dict(line_id='line', issue='ok', evidence='两人交流')], scene_jumps_confusing=False,
            roles=dict(clear=True, consistent=True, evidence='台词与画面对应'),
            readable_situation=dict(who='两个人', where='室内', relationship='交流', emotion='平静'))
        validator(value)
        return value
    rendered = dict(sha256='render', provenance=[dict(output_in_s=0, output_out_s=2.1),
                                               dict(output_in_s=2.1, output_out_s=4.1)])
    entry = pipeline._picture_aware_review(state, SimpleNamespace(call_text=check), rendered, plan,
                                          dict(theme='主旨'), [window], 0, tmp_path)
    line = entry['timeline'][0]
    assert (line['start_s'], line['end_s']) == (0, 4.1)
    assert line['segments'][1]['start_s'] == 2.1
    assert line['segments'][0]['window_source_start_s'] == 100
    assert line['segments'][0]['watched_observation'] == window['observation']
    assert seen[0][0] == 'silent_review_0'
    assert entry['prompt_bytes'] == len(seen[0][1].encode('utf-8'))


def test_large_watched_windows_pack_only_selected_facts_once(tmp_path, monkeypatch):
    from omni_story.library.state import LibraryState
    observation = dict(events=[dict(local_start_s=i, local_end_s=i + 1,
        observed_fact=f'事件{i}：' + '两人交流。' * 20, role_ids=['A'] if i < 8 else ['B'],
        unused_analysis='不需要的分析' * 100) for i in range(120)],
        usable_ranges=[dict(local_in_s=i, local_out_s=i + 1, role_ids=['A'] if i < 8 else ['B'],
            camera='中景', continuity_notes=f'连续{i}', event_indices=[i]) for i in range(120)],
        roles=[dict(role_id='A', state='两人交流', identity_confirmed=True, identity_evidence='同框'),
               dict(role_id='B', state='不相关角色', identity_confirmed=True, identity_evidence='远处')],
        uncertainties=['不相关的不确定信息' * 100])
    windows = [dict(window_id=f'w{i}', source_id='film', source_start_s=100, source_end_s=220,
                    observation=deepcopy(observation)) for i in range(40)]
    plan = dict(fps=30, segments=[dict(segment_id=f's{i}', window_id=f'w{i}', source_in_s=100 + i,
        source_out_s=101 + i, role_ids=['A']) for i in range(8)], audio_draft=[dict(id=f'l{i}',
        text='他们正在商量下一步。', picture_relation='additive', mode='narration', speaker=None,
        segment_ids=[f's{j}' for j in range(i % 4, i % 4 + 4)]) for i in range(32)])
    before = deepcopy(windows)
    state = LibraryState(tmp_path, dict(configuration=dict(creative_style='picture_aware')))
    blind = dict(observed_story='两个人在室内商量。')
    monkeypatch.setattr(pipeline, '_silent_blind_reading', lambda *a: blind)
    seen = []
    def check(name, prompt, validator):
        seen.append(prompt)
        value = dict(lines=[dict(line_id=line['id'], issue='ok', evidence='两人交流') for line in plan['audio_draft']],
            roles=dict(clear=True, consistent=True, evidence='台词与画面对应'),
            scene_jumps_confusing=False,
            readable_situation=dict(who='两人', where='室内', relationship='同伴', emotion='平静'))
        validator(value)
        return value
    entry = pipeline._picture_aware_review(state, SimpleNamespace(call_text=check), dict(sha256='render'), plan,
        dict(theme='主题', intended_takeaway='珍惜陪伴。后面无须重复。', value_judgment=dict(evidence=['大段证据'] * 1000)),
        windows, 0, tmp_path)
    prompt = seen[0]
    context = json.loads('{' + prompt.split('\n{', 1)[1])
    assert entry['prompt_bytes'] == len(prompt.encode('utf-8')) < pipeline.TEXT_ARGV_MAX_BYTES
    assert len(context['segments']) == 7
    assert all('segment_ids' in line and 'segments' not in line for line in context['timeline'])
    assert context['reference_theme'] == '珍惜陪伴。'
    assert '不相关' not in prompt and '不需要的分析' not in prompt and '大段证据' not in prompt
    for segment in context['segments']:
        facts = segment['watched_observation']
        assert len(facts['events']) == len(facts['usable_ranges']) == len(facts['roles']) == 1
        assert facts['events'][0]['local_start_s'] == segment['source_in_s'] - 100
        assert facts['roles'][0]['role_id'] == 'A'
    assert windows == before
    assert read(tmp_path / 'silent_review.json')['reviews'][0]['prompt_bytes'] == entry['prompt_bytes']
    legacy_timeline = deepcopy(entry['timeline'])
    for line in legacy_timeline:
        for segment in line['segments']:
            segment['watched_observation'] = observation
    assert len(prompts.silent_review_prompt(blind, dict(theme='主题'), legacy_timeline,
        render_sha256='render', legacy=True).encode('utf-8')) > 187_969
    assert prompts.silent_review_prompt(blind, dict(intended_takeaway='珍惜陪伴。'), entry['timeline'],
        render_sha256='render') == prompt


@pytest.mark.parametrize('stage', ['blind', 'silent_check'])
def test_picture_first_review_failures_keep_strict_gate(tmp_path, monkeypatch, stage):
    def fail(*args):
        raise LibraryStopped('opencode_vision_job_failed_no_retry:glm_001_' + stage + '_0')
    monkeypatch.setattr(pipeline, '_silent_blind_reading', fail if stage == 'blind' else lambda *a: {})
    state = SimpleNamespace(output=tmp_path, input_lock=dict(configuration={}))
    with pytest.raises(LibraryStopped, match='opencode_vision_job_failed_no_retry:'):
        pipeline._silent_story_check(state, SimpleNamespace(call_text=fail), dict(sha256='render'), [], 0, tmp_path)
    assert not (tmp_path / 'silent_review.json').exists()


@pytest.mark.parametrize('uncertain', [False, True])
def test_parent_failed_legacy_review_without_report_is_not_resent(tmp_path, monkeypatch, uncertain):
    from omni_story.library.state import LibraryState
    plan = dict(segments=[dict(segment_id='seg', window_id='w', source_in_s=100, source_out_s=104)],
        audio_draft=[dict(id='line', text='两人商量。', mode='narration', picture_relation='additive', segment_ids=['seg'])])
    window = dict(window_id='w', source_id='film', source_start_s=100, source_end_s=110, observation=dict(events=[]))
    parent = LibraryState(tmp_path / 'parent', dict(configuration=dict(creative_style='picture_aware')))
    old_prompt = prompts.silent_review_prompt({}, dict(theme='旧主旨'), [], render_sha256='render', legacy=True)
    call, _ = parent.begin_call('silent_review_0', dict(tool='text_completion', arguments=dict(prompt=old_prompt)))
    parent.fail_call(call, 'original spawn or unknown failure', uncertain=uncertain)
    ledger = parent.output / 'library_state.json'
    state = LibraryState(tmp_path / 'child', dict(configuration=dict(creative_style='picture_aware',
        resume_from=dict(ledger=dict(path=str(ledger), sha256=sha256_file(ledger))))))
    monkeypatch.setattr(pipeline, '_silent_blind_reading', lambda *a: {})
    glm = SimpleNamespace(call_text=lambda *a: pytest.fail('must not resend the legacy failed review'))
    entry = pipeline._picture_aware_review(state, glm, dict(sha256='render'), plan, dict(theme='主旨'),
                                           [window], 0, tmp_path)
    assert entry['review_unavailable']['call_id'] == call['id']
    assert not entry['revision_suggested'] and state.data['request_count'] == 0
