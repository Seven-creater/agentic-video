"""Repair feedback for audio-draft-only replies names the failing line, never phantom segment errors."""
from copy import deepcopy
import json

import pytest

from omni_story.library import contracts
from omni_story.library.protocol_feedback import diagnose
from omni_story.library import visual_audio


SEGMENTS = [dict(segment_id=f'seg_{i}', start_s=i * 4.0, end_s=i * 4.0 + 4) for i in range(1, 5)]


def line(ident, segments, **extra):
    return dict(id=ident, text='街上的人还在跑。', voice='Serena', mode='narration', segment_ids=segments,
                audio_relation=[dict(segment_id=sid, relation='narration') for sid in segments], **extra)


def draft():
    return [line('vo_1', ['seg_1', 'seg_2']), line('vo_2', ['seg_3']), line('vo_3', ['seg_4'])]


def test_audio_relation_outside_line_names_that_line_and_keeps_message():
    value = draft()
    value[1]['audio_relation'].append(dict(segment_id='seg_4', relation='cutaway'))
    with pytest.raises(ValueError) as caught:
        contracts.validate_audio_draft(value, SEGMENTS)
    assert str(caught.value) == 'plan/audio_draft/audio_relation/segments:unknown_or_duplicate_ref'
    assert caught.value.violation['path'] == '$.audio_draft[1]'
    assert 'hint' not in caught.value.violation


def test_existing_precise_violation_is_not_replaced():
    value = draft()
    value[0]['start_anchor'] = dict(segment_id='seg_1', offset_s=9)
    with pytest.raises(ValueError, match='start_anchor_outside_segment_duration') as caught:
        contracts.validate_audio_draft(value, SEGMENTS)
    assert caught.value.violation['path'] == 'plan/audio_draft/start_anchor/offset_s'


def test_draft_only_reply_feedback_lists_only_the_failing_line():
    """Real Kung Fu Panda timing repair: one bad audio_relation produced 13 phantom segment/speaker errors."""
    value = draft()
    value[1]['audio_relation'].append(dict(segment_id='seg_4', relation='cutaway'))
    with pytest.raises(ValueError) as caught:
        contracts.validate_audio_draft(value, SEGMENTS)
    items = diagnose('rough_audio_timing_1', {'audio_draft': value},
                     {'validation_error': str(caught.value), 'violation': caught.value.violation})
    assert [item['path'] for item in items] == ['$.audio_draft[1]']
    assert 'audio_draft[1]' in items[0]['hint']


def test_plan_reply_still_gets_full_segment_scan():
    plan = dict(segments=[], audio_draft=draft())
    items = diagnose('chain_e2e_v1_fine_plan', plan, {'validation_error': 'plan:segments_required'})
    assert '$.segments' in [item['path'] for item in items]


def timing_validator(monkeypatch):
    context = dict(duration_s=16, audio_draft=draft(), timing_problems=[dict(line_id='vo_2')])
    captured = {}
    monkeypatch.setattr('omni_story.library.story_context.validate_story_context', lambda value: None)

    class MCP:
        def call(self, stage, prompt, video, validate, scope=None):
            captured['validate'] = validate
            raise RuntimeError('stop')
    with pytest.raises(RuntimeError):
        visual_audio.revise_draft_timing(context, MCP(), __file__, None, scope={})
    return context, captured['validate']


def test_unrelated_timing_line_change_names_path_fields_and_original(monkeypatch):
    context, validate = timing_validator(monkeypatch)
    reply = deepcopy(context['audio_draft'])
    reply[2]['end_s'] = 15.5
    with pytest.raises(ValueError, match='audio_draft_timing_unrelated_line_changed:vo_3') as caught:
        validate({'audio_draft': reply})
    violation = caught.value.violation
    assert violation['path'] == '$.audio_draft[2]'
    assert violation['value'] == dict(id='vo_3', changed_fields=['end_s'])
    assert "['vo_2']" in violation['expected'] and json.dumps(context['audio_draft'][2], ensure_ascii=False) in violation['expected']


def test_timing_role_change_names_path(monkeypatch):
    context, validate = timing_validator(monkeypatch)
    reply = deepcopy(context['audio_draft'])
    reply[1]['voice'] = 'Ethan'
    with pytest.raises(ValueError, match='audio_draft_timing_role_changed:vo_2') as caught:
        validate({'audio_draft': reply})
    assert caught.value.violation['path'] == '$.audio_draft[1]'


def test_non_finite_reply_values_stay_strict_json(tmp_path):
    from omni_story.library.state import write_json
    value = draft()
    value[1]['mode'] = float('nan')
    value[1]['segment_ids'] = [float('inf')]
    with pytest.raises(ValueError) as caught:
        contracts.validate_audio_draft(value, SEGMENTS)
    write_json(tmp_path / 'failure.json', {'violation': contracts.json_safe(caught.value.violation)})
    assert 'non_finite_number' in (tmp_path / 'failure.json').read_text(encoding='utf-8')


def test_plan_stage_audio_only_reply_still_reports_missing_segments():
    items = diagnose('chain_e2e_v1_fine_plan', {'audio_draft': draft()},
                     {'validation_error': 'plan/segments:list_required'})
    assert '$.segments' in [item['path'] for item in items]
    items = diagnose('plan_0', {'audio_draft': draft()},
                     {'stage': 'plan', 'validation_error': 'plan/segments:list_required'})
    assert '$.segments' in [item['path'] for item in items]


def test_dropped_unrelated_timing_line_points_to_its_original_position(monkeypatch):
    context, validate = timing_validator(monkeypatch)
    reply = deepcopy(context['audio_draft'][:2])
    with pytest.raises(ValueError, match='unrelated_line_changed:vo_3') as caught:
        validate({'audio_draft': reply})
    assert caught.value.violation['path'] == '$.audio_draft[2]'
    assert caught.value.violation['repair_hint'].startswith('把vo_3原样恢复')
    items = diagnose('rough_audio_timing_1', {'audio_draft': reply},
                     {'validation_error': str(caught.value), 'violation': caught.value.violation})
    assert items[0]['hint'].startswith('把vo_3原样恢复')

