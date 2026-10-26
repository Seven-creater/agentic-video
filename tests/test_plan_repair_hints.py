import json
from omni_story.library import pipeline


def test_silent_line_states_new_voice_script_is_still_required():
    text = pipeline.SILENT_WITH_CREATIVE_AUDIO
    assert 'audio_draft' in text and '不能省略' in text and '原声' in text


def test_missing_audio_draft_hint():
    hint = pipeline.repair_hint('plan/audio_draft:list_required', '{}')
    assert 'audio_draft' in hint and '不用原声' in hint


def test_speaker_hint_names_the_offending_line_and_where_the_speaker_appears():
    plan = dict(segments=[dict(segment_id='seg_6', window_id='w1', role_ids=['A']),
                          dict(segment_id='seg_7', window_id='w1', role_ids=['B', 'A'])],
                audio_draft=[dict(id='aud_6', mode='dialogue', speaker=dict(window_id='w1', role_id='B'), segment_ids=['seg_6']),
                             dict(id='aud_7', mode='dialogue', speaker=dict(window_id='w1', role_id='A'), segment_ids=['seg_6']),
                             dict(id='aud_8', mode='narration', segment_ids=['seg_6'])])
    hint = pipeline.repair_hint('plan:dialogue_speaker_not_in_selected_segments', json.dumps(plan))
    assert 'aud_6' in hint and "['seg_7']" in hint and 'aud_7' not in hint and 'aud_8' not in hint


def test_unparseable_reply_still_gets_general_speaker_hint():
    hint = pipeline.repair_hint('plan:dialogue_speaker_not_in_selected_segments', 'not json')
    assert 'identity_anchor_segment_id' in hint and '问题句' not in hint


def test_unknown_error_has_no_hint():
    assert pipeline.repair_hint('plan:other', '{}') is None


def test_whole_second_evidence_end_is_clamped_to_real_duration():
    from omni_story.library import contracts
    row = dict(start_s=39, end_s=41)
    value = dict(visible_evidence=[row])
    contracts.normalize_reference(value, 40.84)
    assert row['end_s'] == 40.84
    assert value['normalization'][0]['normalized'] == 40.84


def test_far_evidence_is_clamped_and_empty_intervals_are_dropped():
    from omni_story.library import contracts
    value = dict(visible_evidence=[dict(start_s=39, end_s=41.84),
        dict(start_s=41, end_s=41.5), dict(start_s=5, end_s=5)])
    contracts.normalize_reference(value, 40.84)
    assert value['visible_evidence'] == [dict(start_s=39, end_s=40.84)]
    assert len(value['normalization']) == 3
    assert all(row['dropped'] for row in value['normalization'][1:])


def test_evidence_bounds_hint():
    hint = pipeline.repair_hint('reference/evidence:invalid_or_out_of_bounds', '{}')
    assert '向上取整' in hint
