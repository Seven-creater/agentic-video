"""Measured scheduling, recorded-request compatibility, and fine review time domains."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, story_context, story_finecut, visual_audio
from omni_story.library.media import sha256_file
from test_clean_chain import harness


FIXTURES = Path(__file__).parent / 'fixtures'


def evidence(name='story_context'):
    return json.loads((FIXTURES / f's18_{name}.json').read_text(encoding='utf-8'))


def line(ident, start, end, segment='s', **fields):
    return dict(id=ident, text='好。', voice='Serena', mode='narration', segment_ids=[segment],
                start_s=start, end_s=end, **fields)


def context(lines, durations, *, segments=None, duration=10, timing='measured'):
    return dict(reference=dict(reference_sha256='a' * 64, theme='参考目标', intended_takeaway='完整表达',
                               strategy=dict(speech_mode='mixed')),
        duration_s=duration, audio_timing=timing,
        segments=segments or [dict(id='s', start_s=0., end_s=float(duration), purpose='实际动作')],
        audio_draft=lines, received_tts=[dict(id=row['id'], text=row['text'], voice=row['voice'],
            actual_duration_s=seconds) for row, seconds in zip(lines, durations)])


def schedule(story):
    return story_context.schedule_draft(story, reschedule=True)


def test_s18_measured_receipts_recover_core_lines_and_keep_later_windows():
    original = evidence()
    snapshot = deepcopy(original)
    result = schedule(original)
    rows = {row['id']: row for row in result['audio_draft']}
    assert (rows['line_2']['start_s'], rows['line_2']['end_s']) == (0, 1.2)
    assert (rows['line_1']['start_s'], rows['line_1']['end_s']) == pytest.approx((1.68, 6.08))
    assert result['audio_draft'][2:] == original['audio_draft'][2:]
    assert [row['id'] for row in result['audio_draft']] == [row['id'] for row in original['audio_draft']]
    assert result['timing_problems'] == result['dropped_lines'] == result['partial_reasons'] == []
    assert not result['partial']
    assert result['received_tts'] == original['received_tts']
    for before, after in zip(original['audio_draft'], result['audio_draft']):
        assert {key: value for key, value in before.items() if key not in ('start_s', 'end_s')} == {
            key: value for key, value in after.items() if key not in ('start_s', 'end_s')}
    assert result['schedule_adjustments'] == [dict(type='measured_slide_earlier', line_id='line_1',
                                                  from_s=2., to_s=pytest.approx(1.68))]
    assert story_context.schedule_draft(result) == result
    story_context.validate_story_context(result)
    assert original == snapshot


def test_chronological_order_with_stable_draft_ties_and_original_output_order():
    story = context([line('late', 5, 10), line('tie_a', 0, 10), line('tie_b', 0, 10)], [1, 1, 2])
    result = schedule(story)
    assert [(row['id'], row['start_s'], row['end_s']) for row in result['audio_draft']] == [
        ('late', 5, 6), ('tie_a', 0, 1), ('tie_b', 1, 3)]
    assert not result['timing_problems']


def test_remeasurement_keeps_original_bounds_and_other_partial_evidence():
    story = context([line('voice', 2, 5)], [1], timing='revised')
    first = schedule(story)
    assert first['audio_draft'][0]['end_s'] == 3
    first['received_tts'][0]['actual_duration_s'] = 2
    again = schedule(first)
    assert not again['timing_problems']
    assert again['audio_draft'][0]['end_s'] == 4
    assert story_context.schedule_draft(again) == again
    again.update(partial=True, partial_reasons=['audio_timing_unresolved'])
    resolved = schedule(again)
    assert not resolved['partial'] and resolved['partial_reasons'] == []
    original = evidence()
    original['partial_reasons'].append('independent_visual_limit')
    recovered = schedule(original)
    assert recovered['partial'] and recovered['partial_reasons'] == ['independent_visual_limit']


def test_failed_line_never_occupies_time_or_blocks_later_dialogue():
    bad = line('bad', 0, 2)
    bad['mode'] = 'dialogue'
    good = line('good', 1, 4)
    story = context([bad, good], [20, 1])
    result = schedule(story)
    assert result['timing_problems'] == [dict(line_id='bad', actual_duration_s=20, available_s=10,
        reason='tts_exceeds_window', blocked_by=[], blocked_until_s=0, free_intervals=[[0, 10]])]
    assert (result['audio_draft'][1]['start_s'], result['audio_draft'][1]['end_s']) == (1, 2)
    dropped = story_context.drop_timing_lines(result)
    assert [row['id'] for row in story_context.mixable_draft(dropped)] == ['good']
    assert dropped['partial'] and dropped['dropped_lines'][0]['id'] == 'bad'


@pytest.mark.parametrize('timing', ['measured', 'revised'])
def test_slide_respects_first_segment_and_previous_placed_end(timing):
    segments = [dict(id='a', start_s=0, end_s=3, purpose='动作'),
                dict(id='b', start_s=3, end_s=7, purpose='结果')]
    story = context([line('first', 0, 3, 'a'), line('slide', 5, 7, 'b')], [2, 3],
                    segments=segments, timing=timing)
    result = schedule(story)
    assert (result['audio_draft'][1]['start_s'], result['audio_draft'][1]['end_s']) == (4, 7)
    assert result['schedule_adjustments'] == [dict(type='measured_slide_earlier', line_id='slide',
                                                  from_s=5, to_s=4)]
    story['received_tts'][1]['actual_duration_s'] = 5.1
    assert schedule(story)['timing_problems'][0]['line_id'] == 'slide'


@pytest.mark.parametrize('mode,speaker,duration,total,allowed', [
    ('narration', None, 3.5, 6, True),
    ('narration', None, 4., 6, True),
    ('narration', None, 4.01, 6, False),
    ('narration', None, 3.5, 3, False),
    ('dialogue', None, 3.5, 6, False),
    ('narration', dict(window_id='w', role_id='A'), 3.5, 6, False),
])
def test_narration_overrun_requires_free_timeline_at_most_one_second(mode, speaker, duration, total, allowed):
    draft = line('voice', 0, 3)
    draft['mode'] = mode
    if speaker:
        draft['speaker'] = speaker
    story = context([draft], [duration], duration=total,
        segments=[dict(id='s', start_s=0, end_s=3, purpose='动作')])
    result = schedule(story)
    assert bool(result['timing_problems']) is not allowed
    if allowed:
        assert result['schedule_adjustments'] == [dict(type='narration_overrun', line_id='voice', seconds=duration - 3)]
        story_context.validate_story_context(result)
    else:
        assert result['audio_draft'] == story['audio_draft']
    assert story_context.schedule_draft(result) == result


def test_overrun_must_follow_already_placed_speech_and_cannot_overlap_it():
    segments = [dict(id='a', start_s=0, end_s=2, purpose='动作'),
                dict(id='b', start_s=4, end_s=5, purpose='结果')]
    first = line('first', 0, 2, 'a')
    second = line('second', 4, 5, 'b')
    second['mode'] = 'dialogue'
    result = schedule(context([first, second], [3, 1], segments=segments, duration=5))
    assert [(row['start_s'], row['end_s']) for row in result['audio_draft']] == [(0, 3), (4, 5)]
    assert not result['timing_problems']
    segments[1].update(start_s=2, end_s=4)
    second.update(start_s=2, end_s=4)
    result = schedule(context([first, second], [3, 1], segments=segments, duration=5))
    assert result['timing_problems'][0]['line_id'] == 'first'
    assert (result['audio_draft'][1]['start_s'], result['audio_draft'][1]['end_s']) == (2, 3)


@pytest.mark.parametrize('blocked', [False, True])
def test_anchored_lines_remain_strict_and_failed_anchor_occupies_nothing(blocked):
    anchored = line('anchor', 1, 3, start_anchor=dict(segment_id='s', offset_s=1))
    lines, durations = [anchored, line('later', 3, 5)], [3, 1]
    if blocked:
        lines.insert(0, line('first', 0, 3))
        durations.insert(0, 2)
    result = schedule(context(lines, durations, timing='revised'))
    problem = result['timing_problems'][0]
    assert problem['reason'] == ('start_anchor_overlap' if blocked else 'tts_exceeds_window')
    assert result['audio_draft'][-1]['start_s'] == 3
    assert not result['schedule_adjustments']


def test_revision_uses_new_bounds_and_reports_current_blockers(tmp_path):
    story = context([line('bad', 0, 2), line('good', 2, 5)], [4, 1], timing='revised')
    result = schedule(story)
    assert result['timing_problems'][0]['line_id'] == 'bad'
    class MCP:
        def call(self, stage, prompt, video, validator, **kwargs):
            assert json.loads(prompt.split('\n')[0])['editable_line_ids'] == ['bad']
            reply = dict(audio_draft=deepcopy(result['audio_draft']))
            reply['audio_draft'][0]['text'] = '短。'
            validator(reply)
            return reply
    revised = visual_audio.revise_draft_timing(result, MCP(), __file__, tmp_path)
    revised['received_tts'][0]['actual_duration_s'] = 1
    fixed = schedule(revised)
    assert not fixed['timing_problems']
    assert fixed['audio_draft'][1] == result['audio_draft'][1]


def test_recorded_replay_uses_legacy_rules_until_explicitly_rescheduled():
    story = evidence()
    story.pop('dropped_lines')  # Recorded timing-revision prompts precede dropping.
    original = deepcopy(story)
    legacy = story_context.schedule_draft(story)
    assert legacy == story
    assert 'measured_schedule' not in legacy
    assert schedule(story)['timing_problems'] == []
    assert story == original


def test_chain_resume_keeps_recorded_context_bytes(harness, monkeypatch):
    case = harness()
    case['arguments']['provider_config'] = dict(creative_audio=True)
    case['review']['missing_core_meaning'] = []
    reading = case['seed']['full_response']['reference']
    reading['intended_takeaway'] = '完整主旨'
    original_pipeline = clean_chain.pipeline.execute
    captured = {}
    def pipeline(*args, **kwargs):
        rough = original_pipeline(*args, **kwargs)
        story = evidence()
        story['reference']['reference_sha256'] = reading['reference_sha256']
        story['video_sha256'] = rough['final_sha256']
        for row in story['received_tts']:
            path = case['arguments']['output'] / (row['id'] + '.wav')
            path.write_bytes(b'recorded waveform')
            row.update(audio_path=str(path), audio_sha256=sha256_file(path))
        path = case['arguments']['output'] / 'rough_audio/story_context.json'
        path.parent.mkdir()
        captured['bytes'] = json.dumps(story, ensure_ascii=False, indent=1).encode() + b'\n\n'
        captured['path'], captured['story'] = path, story
        path.write_bytes(captured['bytes'])
        return rough
    class ReachedFine(Exception):
        pass
    def fine(state, mcp, context, *args):
        assert context['story_context'] == captured['story']
        assert captured['path'].read_bytes() == captured['bytes']
        raise ReachedFine()
    monkeypatch.setattr(clean_chain.pipeline, 'execute', pipeline)
    monkeypatch.setattr(clean_chain, 'build_rough_context', lambda *a, **kw: pytest.fail('Recorded context must be reused'))
    monkeypatch.setattr(clean_chain, 'synthesize_draft', lambda *a, **kw: pytest.fail('Resume must not reschedule or call TTS'))
    monkeypatch.setattr(clean_chain, 'execute_finecut', fine)
    with pytest.raises(ReachedFine):
        clean_chain.execute(**case['arguments'])
    assert captured['path'].read_bytes() == captured['bytes']


@pytest.mark.parametrize('recorded', [False, True])
@pytest.mark.parametrize('same_text', [False, True])
def test_fine_review_uses_actual_timeline_and_preserves_recorded_context(monkeypatch, tmp_path, recorded, same_text):
    flow = story_finecut.StoryFinecut.__new__(story_finecut.StoryFinecut)
    flow.story_context = evidence()
    flow.reference = dict(reference=flow.story_context['reference'], story_context=flow.story_context)
    flow.render_source = dict(source_id='chain_parent')
    flow.observed, flow.inspections, flow.knowledge, flow.base = dict(story=[]), [], '', tmp_path
    flow.auth = dict(parent=dict(duration_s=55), target_duration_s=31)
    flow.state = SimpleNamespace(data=dict(calls=[dict(name=story_finecut.PREFIX + 'review')] if recorded else []))
    monkeypatch.setattr(flow, 'blind_proxy', lambda result: dict(path='blind'))
    monkeypatch.setattr(story_finecut.trial, 'proxy', lambda *a, **kw: dict(path='actual'))
    captured = {}
    def call(name, prompt, media, scope, validator, **kwargs):
        if name.endswith('blind'):
            return dict(status='partial', facts=[], problems=[], limitations=[])
        captured.update(json.JSONDecoder().raw_decode(prompt.lstrip())[0])
        return dict(status='partial')
    monkeypatch.setattr(flow, 'call', call)
    before = deepcopy(flow.story_context)
    plan = evidence('fine_plan')
    if same_text:
        next(row for row in plan['audio_draft'] if row['id'] == 'line_1')['text'] = before['audio_draft'][0]['text']
    flow.review(plan, evidence('fine_render'), 0)
    assert flow.story_context == before
    story = captured['story_context']
    if recorded:
        assert story == before
    else:
        assert story['duration_s'] == 31
        assert 'rough_audio_preview' not in story
        assert captured['reference']['story_context'] == story
        assert all(0 <= row['start_s'] < row['end_s'] <= 31 for row in story['audio_draft'])
        assert not story['timing_problems'] and not story['dropped_lines']
        assert {row['id']: row['start_s'] for row in story['audio_draft']}['line_5'] < 31
        assert story['audio_timing'] == ('measured' if same_text else 'estimated')
        assert ('measured_schedule' in story) is same_text
