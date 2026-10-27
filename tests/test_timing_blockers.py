"""Measured earlier speech must be editable when it consumes a later line's slot."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from omni_story.library import pipeline, replay_check, story_context, visual_audio
from omni_story.library.state import LibraryState, json_sha


@pytest.fixture
def panda():
    # Recorded creative_1f954ae finecut: vo_3 22..35, TTS 7.52; dl_2 25..28.
    segments = [dict(id=f'clip_{i}', start_s=start, end_s=end, purpose='实际动作',
                     window_id='panda', role_ids=['A']) for i, start, end in
                [(5, 22., 25.), (6, 25., 28.), (7, 28., 31.), (8, 31., 35.), (9, 35., 41.)]]
    draft = [dict(id='vo_3', text='追逐闯进了集市。车轮、货摊、窄巷，所有过日子的地方，都成了他们比赛的赛道。',
                  voice='Serena', mode='narration', segment_ids=[f'clip_{i}' for i in range(5, 9)],
                  start_s=22., end_s=35.),
             dict(id='dl_2', text='让开——这不是你们的仗！', voice='Ethan', mode='dialogue',
                  speaker=dict(window_id='panda', role_id='A'), segment_ids=['clip_6'], start_s=25., end_s=28.),
             dict(id='safe', text='强者的账，弱者买单。', voice='Serena', mode='narration',
                  segment_ids=['clip_9'], start_s=35., end_s=41.)]
    context = dict(reference=dict(reference_sha256='reference', theme='强者的账，弱者买单',
                   intended_takeaway='无辜者承担代价', strategy=dict(speech_mode='mixed')),
                   duration_s=41., segments=segments, audio_draft=draft, cast=[], audio_timing='estimated')
    context['received_tts'] = [dict(id=line['id'], actual_duration_s=duration)
                               for line, duration in zip(draft, [7.52, 2.16, 1.2])]
    story_context.validate_story_context(context)
    return context


def measured(context, tmp_path):
    context = deepcopy(context)
    durations = {line['text']: audio['actual_duration_s']
                 for line, audio in zip(context['audio_draft'], context.pop('received_tts'))}
    def tts(text, voice, folder, **kwargs):
        return dict(duration_s=durations[text], audio_path='injected', audio_sha256='injected')
    return story_context.synthesize_draft(context, tmp_path, tts=tts)


class Capture:
    def call(self, stage, prompt, video, validator, **kwargs):
        self.stage, self.prompt, self.validate = stage, prompt, validator
        raise RuntimeError('captured')


def revision(context, tmp_path, stage='audio_plan_v1'):
    mcp = Capture()
    with pytest.raises(RuntimeError, match='captured'):
        visual_audio.revise_draft_timing(context, mcp, __file__, tmp_path, stage=stage)
    return mcp


def test_real_panda_blocker_and_zero_slot(panda, tmp_path):
    result = measured(panda, tmp_path)
    assert result['timing_problems'] == [dict(line_id='dl_2', actual_duration_s=2.16, available_s=0,
        reason='tts_exceeds_window', blocked_by=['vo_3'], blocked_until_s=29.52, free_intervals=[])]
    assert visual_audio.timing_editable_line_ids(result['timing_problems']) == ['dl_2', 'vo_3']


def test_free_intervals_include_gaps_and_all_overlapping_lines(panda, tmp_path):
    panda['segments'][0]['start_s'] = 20.
    panda['audio_draft'][0].update(segment_ids=['clip_5'], start_s=22., end_s=25.)
    panda['audio_draft'][1].update(segment_ids=['clip_5', 'clip_6', 'clip_7', 'clip_8'], start_s=20., end_s=35.)
    panda['audio_timing'] = 'revised'
    panda['received_tts'][0]['actual_duration_s'] = 2.
    panda['received_tts'][1]['actual_duration_s'] = 12.
    problem = measured(panda, tmp_path)['timing_problems'][0]
    assert problem['blocked_by'] == ['vo_3']
    assert problem['blocked_until_s'] == 24.
    assert problem['free_intervals'] == [[20., 22.], [24., 35.]]
    assert story_context.timing_window_details([('a', 2, 4), ('b', 6, 8)], 1, 9, 8) == dict(
        blocked_by=['a', 'b'], blocked_until_s=8, free_intervals=[[1, 2], [4, 6], [8, 9]])


def test_overlong_and_anchor_problems_keep_existing_values(panda, tmp_path):
    panda['received_tts'][0]['actual_duration_s'] = 14.
    problem = measured(panda, tmp_path)['timing_problems'][0]
    assert problem == dict(line_id='vo_3', actual_duration_s=14., available_s=13.,
        reason='tts_exceeds_window', blocked_by=[], blocked_until_s=0., free_intervals=[[22., 35.]])
    panda['received_tts'][0]['actual_duration_s'] = 4.
    panda['audio_draft'][1]['start_anchor'] = dict(segment_id='clip_6', offset_s=0.)
    problem = measured(panda, tmp_path)['timing_problems'][0]
    assert problem['reason'] == 'start_anchor_overlap'
    assert problem['blocked_by'] == ['vo_3'] and problem['free_intervals'] == [[26., 28.]]


@pytest.mark.parametrize('stage', ['rough_audio_timing_1', 'audio_plan_v1'])
def test_revision_prompt_validator_and_protected_echo(panda, tmp_path, stage):
    context = measured(panda, tmp_path)
    context['audio_draft'][2]['end_s'] = 36.199999999999996
    mcp = revision(context, tmp_path, stage)
    payload, _ = json.JSONDecoder().raw_decode(mcp.prompt)
    assert payload['editable_line_ids'] == ['dl_2', 'vo_3']
    assert 'vo_3' in mcp.prompt and '阻塞至29.52秒' in mcp.prompt
    assert '空闲区间free_intervals=[]' in mcp.prompt and '缩短阻塞句文本' in mcp.prompt
    reply = dict(audio_draft=deepcopy(context['audio_draft']))
    reply['audio_draft'][0].update(text='追逐闯进集市。', end_s=25.)
    reply['audio_draft'][2]['end_s'] = 36.2
    mcp.validate(reply)
    assert reply['audio_draft'][2]['end_s'] == 36.199999999999996
    reply['audio_draft'][2]['text'] = '不允许改动'
    with pytest.raises(ValueError, match='audio_draft_timing_unrelated_line_changed:safe'):
        mcp.validate(reply)


@pytest.mark.parametrize('field,value', [('voice', 'Cherry'), ('mode', 'dialogue'),
    ('segment_ids', ['clip_5']), ('speaker', dict(window_id='other', role_id='A'))])
def test_blocker_role_changes_still_rejected(panda, tmp_path, field, value):
    context = measured(panda, tmp_path)
    mcp = revision(context, tmp_path)
    reply = dict(audio_draft=deepcopy(context['audio_draft']))
    reply['audio_draft'][0][field] = value
    with pytest.raises(ValueError, match='audio_draft_timing_role_changed:vo_3'):
        mcp.validate(reply)


def test_independent_fine_plan_uses_current_scope_and_protects_roles(panda, tmp_path):
    context = measured(panda, tmp_path)
    previous = [dict(line, speaker_id=None) for line in context['audio_draft']]
    feedback = dict(previous_plan=dict(lines=previous), timing_problems=context['timing_problems'],
                    editable_line_ids=['dl_2'])  # Previously recorded narrower request.
    assert visual_audio.revision_feedback(feedback)['editable_line_ids'] == ['dl_2', 'vo_3']
    reply = dict(lines=deepcopy(previous))
    reply['lines'][0].update(text='追逐闯进集市。', end_s=25.)
    visual_audio.plan_revision_check(reply, feedback)
    reply['lines'][2]['text'] = '不可改'
    with pytest.raises(ValueError, match='audio_plan_revision_protected_line_changed:safe'):
        visual_audio.plan_revision_check(reply, feedback)
    reply['lines'][2] = deepcopy(previous[2])
    reply['lines'][0]['voice'] = 'Cherry'
    with pytest.raises(ValueError, match='audio_plan_revision_protected_line_changed:vo_3'):
        visual_audio.plan_revision_check(reply, feedback)


def test_independent_fine_synthesize_reports_blockers(panda, tmp_path):
    flow = visual_audio.VisualAudio.__new__(visual_audio.VisualAudio)
    flow.story_context, flow.tts_cache_dir = {}, tmp_path
    flow.state = SimpleNamespace(input_lock={})
    durations = iter([7.52, 4.])
    flow.tts = lambda *args, **kwargs: dict(duration_s=next(durations), audio_path='injected', audio_sha256='injected')
    _, problems = flow.synthesize(dict(lines=panda['audio_draft'][:2]))
    assert problems[0] == dict(line_id='dl_2', actual_duration_s=4., available_s=3.,
        description='实际TTS超过画面窗口，须缩短文本或依据画面另选窗口',
        blocked_by=['vo_3'], blocked_until_s=29.52, free_intervals=[])


def test_independent_fine_plan_prompt_uses_blocker_scope(panda, tmp_path):
    context = measured(panda, tmp_path)
    flow = visual_audio.VisualAudio.__new__(visual_audio.VisualAudio)
    flow.story_context, flow.duration = context, context['duration_s']
    mcp = Capture()
    flow.call = mcp.call
    feedback = dict(previous_plan=dict(lines=context['audio_draft']), timing_problems=context['timing_problems'])
    with pytest.raises(RuntimeError, match='captured'):
        flow.plan(__file__, {}, 1, feedback)
    payload, _ = json.JSONDecoder().raw_decode(mcp.prompt)
    assert payload['feedback']['editable_line_ids'] == ['dl_2', 'vo_3']
    assert '阻塞至29.52秒' in mcp.prompt and '空闲区间free_intervals=[]' in mcp.prompt


@pytest.mark.parametrize('stage', ['rough_audio_timing_1', 'audio_plan_v1'])
def test_recorded_scope_is_recomputed_by_replay_validator(panda, tmp_path, stage):
    context = measured(panda, tmp_path)
    for problem in context['timing_problems']:
        for key in ('blocked_by', 'blocked_until_s', 'free_intervals'):
            problem.pop(key)
    request = dict(arguments=dict(prompt=json.dumps(dict(story_context=context, editable_line_ids=['dl_2']))))
    validate = replay_check.validator(tmp_path, dict(input_lock={}), dict(name=stage), request)
    reply = dict(audio_draft=deepcopy(context['audio_draft']))
    reply['audio_draft'][0].update(text='追逐闯进集市。', end_s=25.)
    validate(reply)


@pytest.mark.parametrize('stage', ['rough_audio_timing_1', 'audio_plan_v1'])
def test_resume_reuses_recorded_prompt_with_current_validator(panda, tmp_path, monkeypatch, stage):
    context = measured(panda, tmp_path)
    current = revision(context, tmp_path, stage)
    state = LibraryState(tmp_path / 'run', dict(configuration={}))
    media = tmp_path / 'media.bin'
    media.write_bytes(b'recorded media')
    original = dict(tool='analyze_video', arguments=dict(video_source=str(media), prompt='已录制的旧提示词'),
        media_sha256=pipeline.sha256_file(media), provider=pipeline.CodexMCP.provider,
        policy_version=pipeline.prompts.POLICY_VERSION)
    call, _ = state.begin_call(stage, original)
    reply = dict(audio_draft=deepcopy(context['audio_draft']))
    reply['audio_draft'][0].update(text='追逐闯进集市。', end_s=25.)
    state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(reply))])))
    mcp = pipeline.CodexMCP.__new__(pipeline.CodexMCP)
    mcp.state, mcp.output = state, state.output
    def submit(name, request, **kwargs):
        assert request == original
        return pipeline.CodexMCP._submit(mcp, name, request, **kwargs)
    monkeypatch.setattr(mcp, '_submit', submit)
    assert mcp.call(stage, current.prompt, media, current.validate) == reply
    assert len(state.data['calls']) == 1
