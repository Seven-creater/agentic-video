"""Offline replay and finecut speaker provenance, using synthetic JSON only."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import replay_check as replay, story_finecut as flow
from omni_story.library.story_context import validate_story_context


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def layout():
    return dict(status='pass', framing_ok=True, subtitle_ok=True, no_credits=True,
                no_distortion=True, edit_coherence_ok=True, issues=[], visual_notes=['实际画面'], limitations=[])


def task(path, replies, *, configuration=None):
    calls = []
    for index, (value, options) in enumerate(replies):
        call_id = f'call_{index}'
        stage = options.get('stage', 'final_layout_review_' + f'{index:016x}')
        calls.append(dict(id=call_id, name=stage, status=options.get('status', 'received'),
                          repair_of=options.get('repair_of')))
        folder = path / 'calls' / call_id
        request = dict(arguments=dict(prompt=options.get('prompt', '')), observation_scope=dict(
            kind='continuous_window', source_start_s=0, source_end_s=10, source_sha256='a' * 64))
        request.update(options.get('request', {}))
        write(folder / 'request.json', request)
        source = options.get('source', 'text')
        if source == 'text':
            write(folder / 'response.json', dict(text=json.dumps(value)))
            write(folder / 'parsed.json', layout())
        elif source == 'content':
            write(folder / 'response.json', dict(result=dict(content=[dict(type='text', text=json.dumps(value))])))
        else:
            write(folder / 'parsed.json', value)
        if options.get('failed'):
            write(folder / 'protocol_failure.json', dict(error='historical rejection'))
    state = dict(input_lock=dict(configuration=configuration or {}), calls=calls)
    write(path / 'library_state.json', state)
    return state


def test_call_classification_nested_tasks_original_text_and_read_only(tmp_path, monkeypatch):
    import socket
    import subprocess
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network call'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('media/model subprocess'))
    bad = dict(layout(), issues=[dict(start_s=-1, end_s=2, description='画面遮挡')])
    task(tmp_path / 'first', [(layout(), {}), (bad, {}), (layout(), dict(failed=True)),
        (bad, dict(failed=True)), (layout(), dict(source='content')),
        (layout(), dict(source='parsed')), (layout(), dict(stage='unknown_stage')),
        (layout(), dict(request=dict(observation_scope={}))), (bad, dict(status='uncertain'))])
    task(tmp_path / 'first/evaluations/second', [(layout(), {}),
        (layout(), dict(repair_of='call_0', stage='final_layout_review_0000000000000000_repair'))])
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = replay.replay_check(tmp_path)
    assert report['summary'] == dict(ok=4, regression=1, fixed=2, still_failing=1, unsupported=2)
    assert len(report['tasks']) == 2
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
    regression = next(row for row in report['rows'] if row['category'] == 'regression')
    assert regression['reply_source'] == 'response.json:text'
    assert regression['violation']['path'] == 'issues[0].start_s'
    assert regression['call_id'] == 'call_1'
    unsupported = [row for row in report['rows'] if row['category'] == 'unsupported']
    assert all(row['reason'] and row['passed_now'] is None for row in unsupported)
    text = replay.format_report(report)
    assert regression['error'] in text and regression['stage'] in text and 'violation' in text


@pytest.mark.parametrize('provider,creative,explicit,expected', [
    ('official_vision_mcp_in_opencode', True, False, 'ok'),
    ('official_vision_mcp_in_codex', True, False, 'regression'),
    ('official_vision_mcp_in_opencode', False, False, 'regression'),
    ('official_vision_mcp_in_codex', False, True, 'ok')])
def test_replay_uses_recorded_normalization_configuration(tmp_path, provider, creative, explicit, expected):
    value = dict(layout(), status='needs_revision', framing_ok=False,
                 issues=[dict(start_s=-1, end_s=2, description='画面遮挡')])
    task(tmp_path / 'task', [(value, {})], configuration=dict(provider=provider,
        creative_audio=creative, normalize_observation_times=explicit))
    assert replay.replay_check(tmp_path)['rows'][0]['category'] == expected


def test_missing_context_is_unsupported_and_json_output_cannot_write_data(tmp_path, capsys):
    root = tmp_path / 'data'
    task(root / 'task', [(dict(segments=[]), dict(stage='chain_e2e_v1_fine_plan'))])
    assert replay.main([str(root), '--json', str(tmp_path / 'report.json')]) == 0
    report = json.loads((tmp_path / 'report.json').read_text())
    assert report['summary']['unsupported'] == 1
    assert 'missing artifact' in report['rows'][0]['reason']
    assert 'unsupported' in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        replay.main([str(root), '--json', str(root / 'report.json')])
    assert error.value.code == 2
    assert not (root / 'report.json').exists()


def speaker_fixture():
    value = flow.StoryFinecut.__new__(flow.StoryFinecut)
    value.state = SimpleNamespace(input_lock=dict(configuration=dict(creative_audio=True,
        provider='official_vision_mcp_in_opencode', workflow='reference_rough_skill_v1')))
    reference = dict(reference_sha256='b' * 64, theme='选择与信任', intended_takeaway='守住品质赢得信任',
                     strategy=dict(speech_mode='mixed'))
    value.reference = dict(reference=reference)
    value.auth = dict(parent=dict(duration_s=12), reference=dict(duration_s=10),
                      target_duration_s=10, target_tolerance_s=2)
    value.adaptive = True
    value.render_source = dict(source_id='chain_parent', path='synthetic.mp4', sha256='a' * 64,
                               duration_s=12, audio_stream_index=None)
    value.observed = dict(story=[dict(id='beat', contribution='建立角色')], facts=[], inspect=[], limitations=[])
    event = dict(start_s=2, end_s=4, window_id='original_window', role_ids=['unlisted_role'],
                 entity_ids=['unlisted_entity'], observed_fact='角色出现', complete_event=True)
    parent = dict(id='rough', start_s=0, end_s=12, purpose='建立角色',
                  visual_events=[event], entity_ids=['unlisted_entity'],
                  window_id='original_window', role_ids=['unlisted_role'])
    cast = [dict(cast_id='other', entity_id='other_entity', voice='Moon',
                 bindings=[dict(window_id='other_window', role_id='other_role')])]
    line = dict(id='line', text='我守住品质。', voice='Ethan', mode='dialogue',
                speaker=dict(window_id='original_window', role_id='unlisted_role'),
                segment_ids=['rough'], start_s=0, end_s=4)
    value.story_context = dict(reference=reference, video_sha256='a' * 64, duration_s=12,
                              cast=cast, segments=[parent], audio_draft=[line])
    segment = dict(segment_id='clip', source_id='chain_parent', window_id='full_parent_observed',
        source_in_s=1, source_out_s=5, speed=1.5, freeze_tail_s=.2, look='none', framing='fit',
        contribution_to=['beat'], reason='角色回应')
    draft = {key: deepcopy(item) for key, item in line.items() if key not in {'start_s', 'end_s', 'speaker'}}
    draft.update(segment_ids=['clip'], audio_relation=[dict(segment_id='clip', relation='on_screen')])
    return value, dict(segments=[segment], audio_draft=[draft], limitations=[])


def test_unlisted_speaker_surviving_event_matches_delivery_without_mutation():
    value, plan = speaker_fixture()
    before = deepcopy((value.story_context, plan))
    value.plan_check(plan)
    compiled = flow.compile_library_plan([value.render_source], dict(segments=plan['segments'], audio_mode='silent'),
        fps=flow.FPS, width=flow.WIDTH, height=flow.HEIGHT, max_duration_s=None)
    rendered = dict(sha256='c' * 64, duration_s=compiled['duration_s'], provenance=compiled['segments'])
    early = value.fine_context(plan)
    delivery = value.fine_context(plan, rendered)
    assert early['segments'] == delivery['segments']
    assert early['audio_draft'] == delivery['audio_draft']
    validate_story_context(delivery)
    event = early['segments'][0]['visual_events'][0]
    assert event['window_id'] == 'original_window' and event['role_ids'] == ['unlisted_role']
    assert early['audio_draft'][0]['speaker'] == before[0]['audio_draft'][0]['speaker']
    assert (value.story_context, plan) == before


@pytest.mark.parametrize('wrong_window', [False, True])
def test_trimmed_or_unrelated_role_fails_both_early_and_delivery(wrong_window):
    value, plan = speaker_fixture()
    if wrong_window:
        value.story_context['segments'][0]['visual_events'][0]['window_id'] = 'unrelated_window'
    else:
        plan['segments'][0].update(source_in_s=5, source_out_s=9, role_ids=['unlisted_role'],
                                   entity_ids=['unlisted_entity'])
    errors = []
    for check in (lambda: value.plan_check(plan), lambda: value.fine_context(plan)):
        with pytest.raises(ValueError, match='dialogue_speaker_not_in_selected_segments') as error:
            check()
        errors.append(error.value.violation)
    assert errors[0] == errors[1]
    assert "实际出现在[]" in errors[0]['expected']


@pytest.mark.parametrize('selection', ['result', 'review', 'review_r', 'review_r2'])
def test_replay_fine_plan_and_delivery_rebuild_same_context(tmp_path, selection):
    value, plan = speaker_fixture()
    root = tmp_path / 'data'
    context = dict(reference=value.reference, observed=value.observed, source=value.render_source,
                   story_context=value.story_context)
    state = task(root / 'task', [(plan, dict(stage=flow.PREFIX + 'plan', prompt=json.dumps(context)))],
                 configuration=value.state.input_lock['configuration'])
    base = root / 'task/skill_finecut'
    write(base / 'input.json', dict(parent=value.render_source,
        reference=dict(duration_s=10), target_tolerance_s=2))
    write(root / 'task/reference_reading.json', value.reference['reference'])
    write(root / 'task/rough_audio/story_context.json', value.story_context)
    compiled = flow.compile_library_plan([value.render_source], dict(segments=plan['segments'], audio_mode='silent'),
        fps=flow.FPS, width=flow.WIDTH, height=flow.HEIGHT, max_duration_s=None)
    index = {'result': 0, 'review': 0, 'review_r': 1, 'review_r2': 2}[selection]
    render_dir = base / f'render_{index}'
    rendered = dict(sha256='c' * 64, duration_s=compiled['duration_s'], provenance=compiled['segments'],
                    manifest_path=f'/server/task/skill_finecut/render_{index}/render_manifest.json')
    write(render_dir / 'render_input.json', dict(compiled=dict(plan=plan)))
    delivered = value.fine_context(plan, rendered)
    if selection == 'result':
        write(base / 'result.json', dict(selected_render=rendered, story_context=delivered))
        write(base / 'story_context.json', delivered)
    else:
        state['calls'].append(dict(id='review_call', name=flow.PREFIX + selection, status='received'))
        write(root / 'task/library_state.json', state)
        write(root / 'task/calls/review_call/parsed.json', dict(status='pass', problems=[],
            apparent_story='角色回应', limitations=[], missing_core_meaning=[], next_action='deliver'))
        write(root / 'task/calls/review_call/request.json', dict(observation_scope=dict(
            source_start_s=0, source_end_s=compiled['duration_s'])))
        write(render_dir / 'render_result.json', rendered)
        write(root / 'task/chain_failure.json', dict(error='plan:dialogue_speaker_not_in_selected_segments'))
    report = replay.replay_check(root)
    assert [row['category'] for row in report['rows']] == (['ok', 'ok'] if selection == 'result' else ['ok', 'ok', 'fixed'])
    assert report['summary']['regression'] == 0
    # A later rendering that loses the event must fail the same speaker contract.
    plan['segments'][0].update(source_in_s=5, source_out_s=9)
    write(render_dir / 'render_input.json', dict(compiled=dict(plan=plan)))
    failed = replay.replay_check(root)['rows'][-1]
    assert failed['category'] == ('regression' if selection == 'result' else 'still_failing')
    assert failed['violation']['path'] == '$.audio_draft[0].speaker'


@pytest.mark.skipif(not os.environ.get('OMNI_REPLAY_DATA'), reason='OMNI_REPLAY_DATA is not set')
def test_real_data_has_no_regressions():
    report = replay.replay_check(Path(os.environ['OMNI_REPLAY_DATA']))
    assert report['tasks'] and report['rows'], 'replay dataset has no tasks or received calls'
    assert report['summary']['regression'] == 0, replay.format_report(report)
    assert report['summary']['unsupported'] == 0, replay.format_report(report)
