"""Finecut continuity and review-scoped deletion, without media or model calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from omni_story.library import story_finecut as flow


def finecut(*, audio=True):
    value = flow.StoryFinecut.__new__(flow.StoryFinecut)
    value.state = SimpleNamespace(input_lock={'configuration': {'creative_audio': audio}})
    value.auth = dict(parent=dict(duration_s=80), reference=dict(duration_s=12),
                      target_duration_s=12, target_tolerance_s=2)
    value.render_source = dict(source_id='chain_parent', path='synthetic.mp4', sha256='a' * 64,
                               duration_s=80, audio_stream_index=None)
    value.observed = dict(story=[dict(id='main')])
    value.knowledge, value.reference, value.inspections = 'skill', {}, []
    value.story_context = dict(reference=dict(theme='选择与信任')) if audio else None
    original = dict(segments=[dict(segment_id=f'clip_{i}', source_id='chain_parent',
        window_id='full_parent_observed', source_in_s=i * 10, source_out_s=i * 10 + 2,
        speed=1, freeze_tail_s=0, look='none', framing='fit', contribution_to=['main'],
        reason='实际信息贡献') for i in range(1, 7)], limitations=[])
    if audio:
        original['audio_draft'] = [dict(id='main_voice', text='守住品质才能赢得信任。', voice='Serena',
            mode='narration', segment_ids=['clip_1']), dict(id='closing', text='对方终于作出了回应。',
            voice='Serena', mode='narration', segment_ids=['clip_6'],
            audio_relation=[dict(segment_id='clip_6', relation='narration')],
            start_anchor=dict(segment_id='clip_6', offset_s=0), identity_anchor_segment_id='clip_6')]
    value.plan_check(original)
    return value, original


def rendered(value, original):
    compiled = flow.compile_library_plan([value.render_source],
        dict(segments=original['segments'], audio_mode='silent'), fps=flow.FPS,
        width=flow.WIDTH, height=flow.HEIGHT)
    return dict(duration_s=compiled['duration_s'], provenance=compiled['segments'])


def review(*intervals):
    return dict(target=dict(problems=[dict(start_s=start, end_s=end, description='突兀的夜间拥抱段')
                                     for start, end in (intervals or ((10.25, 11.75),))]))


def without(original, *deleted):
    revised = deepcopy(original)
    revised['segments'] = [row for row in revised['segments'] if row['segment_id'] not in deleted]
    revised['audio_draft'] = [line for line in revised.get('audio_draft', [])
                             if not set(line['segment_ids']) & set(deleted)]
    return revised


def move_line(line, sid):
    line['segment_ids'] = [sid]
    line['audio_relation'] = [dict(segment_id=sid, relation='narration')]
    line['start_anchor'] = dict(segment_id=sid, offset_s=0)
    line['identity_anchor_segment_id'] = sid


@pytest.mark.parametrize('audio', [False, True])
def test_planning_prompt_checks_continuity_and_audio_migration(audio):
    value, _ = finecut(audio=audio)
    prompt = value.plan_prompt(value.observed)
    for text in ('逐对检查相邻段落连贯性', '时间、场景或地点明显跳变', '观众可理解的衔接线索',
                 '同一人物或物件延续、动作承接、旁白交代', '否则删除或调整该段',
                 '前文没有铺垫、突然出现的新角色或新事件段，若非主旨必需则删除',
                 'audio_draft台词须一并删除或改挂到相邻保留段', '说话人可见及identity_anchor规则',
                 '不能靠电影常识补结果', 'speed为0.5–2，freeze_tail_s为0–2秒'):
        assert text in prompt


def test_revision_prompt_scopes_deletion_to_original_output_review_intervals():
    value, original = finecut()
    prompt = value.plan_prompt(value.observed, revision=dict(plan=original, review=review()))
    for text in ('可删除审核点名问题区间内的整段', '原计划渲染后的输出区间',
                 'review.target.problems的start_s/end_s有重叠', '不得新增段落，至少保留1段',
                 '保留段的segment_id、相对顺序和contribution_to不变',
                 '与问题区间重叠的段落可修改入出点/操作或整段删除',
                 '问题区间之间不超过1秒的空隙也算作问题区间',
                 '问题区间之外的段落源范围和操作不变、不得删除',
                 'audio_draft台词须一并删除或改挂到相邻保留段'):
        assert text in prompt
    assert '逐对检查相邻段落连贯性' not in prompt
    assert '初次规划时，已证伪事件可省略' in prompt


@pytest.mark.parametrize('audio', [False, True])
def test_deleting_reviewed_last_segment_passes(audio):
    value, original = finecut(audio=audio)
    revised = without(original, 'clip_6')
    snapshot = deepcopy((original, revised))
    value.revision_check(revised, original, rendered(value, original), review())
    assert (original, revised) == snapshot


def test_deleting_unrelated_segment_reports_which_segment_and_why():
    value, original = finecut()
    with pytest.raises(ValueError, match='revision_deletes_unrelated_segment') as caught:
        value.revision_check(without(original, 'clip_2'), original, rendered(value, original), review())
    violation = caught.value.violation
    assert violation['path'] == '$.original.segments[1].segment_id'
    assert violation['value'] == 'clip_2'
    assert 'does not overlap' in violation['expected'] and 'clip_2' in violation['expected']


@pytest.mark.parametrize('gap', [.5, 1.0, 1.0 + 5e-7])
@pytest.mark.parametrize('action', ['delete', 'modify', 'modify_with_deletion'])
def test_segment_in_small_review_gap_can_be_deleted_or_modified(gap, action):
    value, original = finecut()
    original['segments'][1]['source_out_s'] = 20 + gap
    feedback = review((2 + gap, 5), (0, 2), (1, 2))
    revised = without(original, 'clip_2') if action == 'delete' else deepcopy(original)
    if action != 'delete':
        revised['segments'][1]['source_out_s'] += .25
        revised['segments'][1]['look'] = 'grayscale'
    if action == 'modify_with_deletion':
        revised = without(revised, 'clip_3')
    snapshot = deepcopy((original, revised, feedback))
    value.revision_check(revised, original, rendered(value, original), feedback)
    assert (original, revised, feedback) == snapshot


@pytest.mark.parametrize('gap', [1.0 + 2e-6, 1.5])
def test_segment_in_larger_review_gap_cannot_be_deleted(gap):
    value, original = finecut()
    original['segments'][1]['source_out_s'] = 20 + gap
    feedback = review((2 + gap, 6), (0, 2), (1, 2))
    with pytest.raises(ValueError, match='revision_deletes_unrelated_segment') as caught:
        value.revision_check(without(original, 'clip_2'), original, rendered(value, original), feedback)
    assert caught.value.violation['value'] == 'clip_2'
    assert str([(0, 2), (2 + gap, 6)]) in caught.value.violation['expected']


@pytest.mark.parametrize('delete', [False, True])
def test_unrelated_modification_error_shows_merged_review_intervals(delete):
    value, original = finecut()
    revised = without(original, 'clip_6') if delete else deepcopy(original)
    revised['segments'][0]['look'] = 'grayscale'
    with pytest.raises(ValueError, match='revision_changes_unrelated_segment') as caught:
        value.revision_check(revised, original, rendered(value, original), review((11, 12), (9, 10), (8, 9)))
    assert caught.value.violation['value']['segment_id'] == 'clip_1'
    assert '[(8, 12)]' in caught.value.violation['expected']


@pytest.mark.parametrize('field,new', [('source_in_s', 10.2), ('source_out_s', 12.2), ('speed', 1.25),
    ('freeze_tail_s', .25), ('look', 'grayscale'), ('framing', 'crop'), ('source_crop', [0, 0, 320, 240]),
    ('source_subtitle_masks', [dict(x=0, y=0, w=.2, h=.2, start_s=10, end_s=11)]),
    ('source_id', 'other_source'), ('window_id', 'other_window'), ('speed', 3), ('freeze_tail_s', .001)])
def test_deletion_cannot_change_retained_operations(field, new):
    value, original = finecut()
    revised = without(original, 'clip_6')
    revised['segments'][0][field] = new
    with pytest.raises(ValueError, match='revision_changes_unrelated_segment') as caught:
        value.revision_check(revised, original, rendered(value, original), review())
    assert caught.value.violation['value']['segment_id'] == 'clip_1'
    assert 'unchanged' in caught.value.violation['expected']


@pytest.mark.parametrize('field,new', [('speed', 1.25), ('source_in_s', 50.25)])
def test_deletion_can_change_retained_segment_inside_problem_interval(field, new):
    value, original = finecut()
    revised = without(original, 'clip_6')
    revised['segments'][-1][field] = new
    value.revision_check(revised, original, rendered(value, original), review((8, 12)))


@pytest.mark.parametrize('replace', [False, True])
def test_cannot_add_segment_even_when_replacing_another(replace):
    value, original = finecut()
    revised = deepcopy(original)
    new = dict(revised['segments'][-1], segment_id='new_clip')
    if replace:
        revised['segments'][-1] = new
    else:
        revised['segments'].append(new)
    with pytest.raises(ValueError, match='revision_cannot_add_segment') as caught:
        value.revision_check(revised, original, rendered(value, original), review())
    assert caught.value.violation['value'] == 'new_clip'


def test_cannot_delete_all_segments():
    value, original = finecut()
    revised = dict(segments=[], audio_draft=[], limitations=[])
    with pytest.raises(ValueError, match='revision_requires_retained_segment') as caught:
        value.revision_check(revised, original, rendered(value, original), review((0, 12)))
    assert caught.value.violation['path'] == '$.segments'
    assert 'at least one' in caught.value.violation['expected']


@pytest.mark.parametrize('field', ['segment_ids', 'audio_relation', 'start_anchor', 'identity_anchor_segment_id'])
def test_deleted_audio_references_are_rejected_with_precise_path(field):
    value, original = finecut()
    revised = deepcopy(original)
    revised['segments'].pop()
    move_line(revised['audio_draft'][-1], 'clip_5')
    revised['audio_draft'][-1][field] = deepcopy(original['audio_draft'][-1][field])
    with pytest.raises(ValueError, match='deleted_segment_audio_reference') as caught:
        value.revision_check(revised, original, rendered(value, original), review())
    assert caught.value.violation['path'].startswith('$.audio_draft[1].' + field)
    assert caught.value.violation['value'] == 'clip_6'


def test_deleted_audio_can_move_to_adjacent_retained_segment():
    value, original = finecut()
    revised = deepcopy(original)
    revised['segments'].pop()
    move_line(revised['audio_draft'][-1], 'clip_5')
    value.revision_check(revised, original, rendered(value, original), review())


def test_deleted_audio_cannot_move_to_unrelated_segment():
    value, original = finecut()
    revised = deepcopy(original)
    revised['segments'].pop()
    move_line(revised['audio_draft'][-1], 'clip_3')
    with pytest.raises(ValueError, match='deleted_segment_audio_requires_adjacent_segment') as caught:
        value.revision_check(revised, original, rendered(value, original), review())
    assert caught.value.violation['value'] == dict(line_id='closing', segment_id='clip_3')


def test_migrated_audio_still_requires_established_identity_anchor():
    value, original = finecut()
    revised = deepcopy(original)
    revised['segments'].pop()
    move_line(revised['audio_draft'][-1], 'clip_5')
    revised['audio_draft'][0]['identity_anchor_segment_id'] = 'clip_5'
    with pytest.raises(ValueError, match='dialogue_identity_anchor_not_established'):
        value.revision_check(revised, original, rendered(value, original), review())


def test_multiple_deletions_use_original_positions_and_nearest_retained_neighbors():
    value, original = finecut()
    revised = deepcopy(original)
    revised['segments'] = revised['segments'][1:4]
    revised['audio_draft'][0]['segment_ids'] = ['clip_2']
    move_line(revised['audio_draft'][-1], 'clip_4')
    value.revision_check(revised, original, rendered(value, original), review((0, 2), (8, 12)))


@pytest.mark.parametrize('interval,allowed', [((8.58, 8.6), False), ((8.6, 8.61), True)])
def test_overlap_uses_renderer_frame_rounding_speed_and_freeze(interval, allowed):
    value, original = finecut()
    original['segments'][0].update(source_out_s=11.04, speed=2, freeze_tail_s=.05)
    result = rendered(value, original)
    assert result['provenance'][-1]['output_in_s'] == 8.6
    revised = without(original, 'clip_6')
    if allowed:
        value.revision_check(revised, original, result, review(interval))
    else:
        with pytest.raises(ValueError, match='revision_deletes_unrelated_segment'):
            value.revision_check(revised, original, result, review(interval))


@pytest.mark.parametrize('delete', [False, True])
@pytest.mark.parametrize('change', ['order', 'duplicate', 'contribution'])
def test_retained_order_ids_and_contributions_cannot_change(delete, change):
    value, original = finecut()
    revised = without(original, 'clip_6') if delete else deepcopy(original)
    if change == 'order':
        revised['segments'][:2] = reversed(revised['segments'][:2])
    elif change == 'duplicate':
        revised['segments'][1] = deepcopy(revised['segments'][0])
    else:
        revised['segments'][0]['contribution_to'] = ['other']
    with pytest.raises(ValueError, match='local_revision_preserves_') as caught:
        value.revision_check(revised, original, rendered(value, original), review())
    assert 'clip_' in str(caught.value.violation)


def test_no_deletion_keeps_existing_local_revision_behavior():
    value, original = finecut()
    result, feedback = rendered(value, original), review((0, 2))
    value.revision_check(deepcopy(original), original, result, feedback)
    revised = deepcopy(original)
    revised['segments'][0].update(source_in_s=10.2, source_out_s=12.4, speed=1.25, freeze_tail_s=.2,
                                   look='grayscale', framing='crop')
    value.revision_check(revised, original, result, feedback)
    revised['segments'][1]['source_out_s'] = 22.2
    with pytest.raises(ValueError, match='revision_changes_unrelated_segment') as caught:
        value.revision_check(revised, original, result, feedback)
    assert caught.value.violation['value']['segment_id'] == 'clip_2'
