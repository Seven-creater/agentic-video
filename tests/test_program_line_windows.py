"""Program-owned plan windows, including the three received S13 replies."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, line_windows, protocol_feedback, replay_check
from omni_story.library.plan_normalisation import normalise_plan
from test_mask_and_line_window import short_plan


FIXTURES = Path(__file__).with_name('fixtures')


def read(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def case(span=2.5, *, text='甲' * 10, second='乙', fps=30):
    plan, catalog, window = short_plan()
    plan['segments'][0].update(source_in_s=1, source_out_s=1 + span)
    plan['audio_draft'][0]['text'] = text
    if second is not None:
        plan['audio_draft'].append(dict(plan['audio_draft'][0], id='second', text=second))
    plan['fps'] = fps
    return plan, catalog, window


def groups(plan, **kwargs):
    return line_windows.draft_line_window_groups(plan['audio_draft'], plan['segments'], fps=plan['fps'], **kwargs)


def normalise(plan, catalog, window, **kwargs):
    return normalise_plan(plan, [window], catalog=catalog, **kwargs)


def test_need_based_allocation_reserves_minima_before_sharing_slack():
    plan, _, _ = case(4, second='乙' + '。' * 100)
    first, second = plan['audio_draft']
    old_proportional = 4 * len(first['text']) / (len(first['text']) + len(second['text']))
    assert old_proportional < line_windows.min_line_window_s(first['text'])
    windows = line_windows.draft_line_windows(plan['audio_draft'], plan['segments'])
    assert windows['line'] == pytest.approx((0, 1.8 + 1.5 * 10 / 111))
    assert windows['second'][-1] == 4
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])


def test_exact_receipts_supply_each_reserved_minimum():
    plan, _, _ = case(4)
    receipts = [dict(text=line['text'], voice=line['voice'], actual_duration_s=duration)
                for line, duration in zip(plan['audio_draft'], [.5, 2])]
    resolved = line_windows.draft_line_windows(plan['audio_draft'], plan['segments'], received_tts=receipts)
    assert resolved['line'][1] == pytest.approx(.5 + 1.5 * 10 / 11)
    line_windows.validate_draft_line_windows(plan['audio_draft'], plan['segments'], received_tts=receipts)


@pytest.mark.parametrize('offset', [0, .1, 1])
def test_previously_feasible_proportional_group_remains_feasible(offset):
    plan, _, _ = case(6, text='甲乙丙丁', second='戊己庚辛')
    plan['audio_draft'][1]['start_anchor'] = dict(segment_id='seg', offset_s=offset)
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])
    resolved = line_windows.draft_line_windows(plan['audio_draft'], plan['segments'])
    assert all(end - start >= .72 for start, end in resolved.values())


def test_infeasible_group_reports_shared_deficit_and_available_source_extensions():
    plan, catalog, window = case(2)
    with pytest.raises(ValueError, match='^audio_line_window_below_minimum$') as caught:
        contracts.validate_audio_draft(plan['audio_draft'], plan['segments'], fps=30, windows=[window], catalog=catalog)
    violation = caught.value.violation
    assert violation['segment_ids'] == ['seg']
    assert violation['span_s'] == 2
    assert violation['demand_s'] == pytest.approx(2.5)
    assert violation['deficit_s'] == pytest.approx(.5)
    assert violation['spoken_chars_to_cut'] == 3
    assert [line['id'] for line in violation['lines']] == ['line', 'second']
    assert violation['extendable_seconds']['seg'] == [dict(usable_range_index=0,
        source_in_s=0, source_out_s=30, extend_before_s=1, extend_after_s=27)]
    diagnosed = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', validation_error=caught.value))
    group, = [item for item in diagnosed if item.get('code') == str(caught.value)]
    assert group['deficit_s'] == violation['deficit_s']
    assert 'start_s/end_s' not in group['repair_hint']
    prompt = line_windows.line_window_prompt(plan['audio_draft'], segments=plan['segments'], program_arranged=True)
    assert '窗口由程序安排' in prompt and '不填写或计算' in prompt
    budgets, _ = json.JSONDecoder().raw_decode(prompt.split('当前分组窗口预算：', 1)[1])
    assert budgets[0]['span_s'] == violation['span_s']
    assert budgets[0]['demand_s'] == violation['demand_s']
    assert budgets[0]['deficit_s'] == violation['deficit_s']
    assert budgets[0]['lines'] == [dict(id=row['id'], text=row['text'], min_window_s=row['min_window_s'])
                                  for row in violation['lines']]


@pytest.mark.parametrize('parent', [None, dict(duration_s=30, segments=[], audio_draft=[])])
def test_all_explicit_times_removed_once_in_rough_and_fine_paths(parent):
    plan, _, _ = case(4)
    plan['audio_draft'][0].update(start_s=99, end_s=100)
    plan['audio_draft'][1]['end_s'] = -1
    before = deepcopy(plan)
    normalise_plan(plan, parent=parent)
    changes = plan['plan_normalisations']
    assert [(c['path'], c['before'], c['reason']) for c in changes] == [
        ('$.audio_draft[0]', dict(start_s=99, end_s=100), 'program_line_window'),
        ('$.audio_draft[1]', dict(end_s=-1), 'program_line_window')]
    for old, line in zip(before['audio_draft'], plan['audio_draft']):
        assert line == {key: value for key, value in old.items() if key not in {'start_s', 'end_s'}}
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])
    snapshot = deepcopy(plan)
    normalise_plan(plan, parent=parent)
    assert plan == snapshot


def test_draft_without_explicit_times_is_unchanged():
    plan, _, _ = case(4)
    before = deepcopy(plan)
    normalise_plan(plan)
    assert plan == before


@pytest.mark.parametrize('span,offset,drop', [(4, 3, True), (4, .2, False), (2, .4, False)])
def test_anchors_dropped_only_when_they_alone_make_group_infeasible(span, offset, drop):
    plan, _, _ = case(span)
    anchor = dict(segment_id='seg', offset_s=offset)
    plan['audio_draft'][0]['start_anchor'] = anchor
    normalise_plan(plan)
    assert ('start_anchor' not in plan['audio_draft'][0]) == drop
    changes = [c for c in plan.get('plan_normalisations', []) if c['reason'] == 'drop_start_anchor_for_line_window']
    assert len(changes) == int(drop)
    if drop:
        assert changes[0]['before'] == anchor
        contracts.validate_audio_draft(plan['audio_draft'], plan['segments'])
    before = deepcopy(plan)
    normalise_plan(plan)
    assert plan == before


@pytest.mark.parametrize('fps,speed', [(24, 1), (30, 2), (60, .5)])
def test_small_extension_uses_output_frames_and_speed(fps, speed):
    plan, catalog, window = case(1.96 * speed, second=None, fps=fps)
    plan['segments'][0]['speed'] = speed
    receipt = [dict(text=plan['audio_draft'][0]['text'], voice='Ethan', actual_duration_s=2)]
    before = groups(plan, received_tts=receipt)[0]['span_s']
    normalise(plan, catalog, window, received_tts=receipt)
    after = groups(plan, received_tts=receipt)[0]['span_s']
    assert after >= 2
    assert (after - before) * fps == pytest.approx(round((after - before) * fps))
    change, = [c for c in plan['plan_normalisations'] if c['reason'] == 'extend_segment_for_line_window']
    assert change['segment_id'] == 'seg'
    assert change['deficit_s'] == pytest.approx(2 - before)
    assert change['after'] - change['before'] == pytest.approx((after - before) * speed)
    snapshot = deepcopy(plan)
    normalise(plan, catalog, window, received_tts=receipt)
    assert plan == snapshot


def test_extension_can_use_first_segment_backwards_without_fps():
    plan, catalog, window = case(2)
    plan.pop('fps')
    window['observation']['usable_ranges'][0]['local_out_s'] = 3
    normalise(plan, catalog, window)
    assert plan['segments'][0]['source_in_s'] == pytest.approx(.5)
    assert plan['segments'][0]['source_out_s'] == 3


@pytest.mark.parametrize('span,receipt', [(1.85, 1.9), (1.95, 2.1)])
def test_half_frame_rounding_adds_exactly_the_requested_frames(span, receipt):
    plan, catalog, window = case(span, second=None, fps=10)
    received = [dict(text=plan['audio_draft'][0]['text'], voice='Ethan', actual_duration_s=receipt)]
    before = groups(plan, received_tts=received)[0]['span_s']
    normalise(plan, catalog, window, received_tts=received)
    assert groups(plan, received_tts=received)[0]['span_s'] == pytest.approx(receipt)
    assert (receipt - before) * 10 == pytest.approx(1)


@pytest.mark.parametrize('reason', ['range', 'role_union', 'role_event', 'large_deficit', 'duration', 'duration_unknown', 'content', 'adjacent'])
def test_extension_refused_when_support_or_budget_is_missing(reason):
    plan, catalog, window = case(2)
    kwargs = {}
    usable = window['observation']['usable_ranges'][0]
    if reason == 'large_deficit':
        plan['audio_draft'][0]['text'] = '甲' * 20
    elif reason == 'duration':
        kwargs.update(storyboard=dict(storyboard=[]), reference_duration_s=2 / 1.2)
    elif reason == 'duration_unknown':
        kwargs.update(storyboard=dict(storyboard=[]))
    elif reason == 'content':
        catalog['sources'][0]['content_range'] = dict(content_start_s=1, content_end_s=3)
    elif reason == 'adjacent':
        original = plan['segments'][0]
        plan['segments'] = [dict(original, segment_id='previous', source_in_s=0, source_out_s=1), original,
                            dict(original, segment_id='next', source_in_s=3, source_out_s=4)]
    else:
        usable.update(local_in_s=1, local_out_s=3)
        if reason in {'role_union', 'role_event'}:
            window['observation']['roles'] = [dict(role_id='A', identity_confirmed=True)]
            plan['segments'][0]['role_ids'] = ['A']
            usable['role_ids'] = ['A']
            window['observation']['events'][0]['role_ids'] = [] if reason == 'role_event' else ['A']
            if reason == 'role_union':
                window['observation']['usable_ranges'].append(dict(usable, local_in_s=0, local_out_s=30, role_ids=[]))
    original_segments = deepcopy(plan['segments'])
    normalise(plan, catalog, window, **kwargs)
    assert plan['segments'] == original_segments
    assert not any(c['reason'] == 'extend_segment_for_line_window' for c in plan.get('plan_normalisations', []))


@pytest.mark.parametrize('attempt,expected', [(0, 'audio_line_window_below_minimum'),
    (1, None), (2, 'visual_library:range_outside_content')])
def test_three_received_replies_replay_through_production_normalisation_and_validation(attempt, expected):
    context, plan = read('s13_context'), read(f's13_attempt_{attempt}')
    before = deepcopy(plan)
    assert all('start_s' in line and 'end_s' in line for line in plan['audio_draft'])
    deficits = [g for g in groups(plan) if g['deficit_s'] > 1e-6]
    assert [g['segment_ids'] for g in deficits] == ([['seg_6']] if attempt == 0 else [])
    state = dict(input_lock=dict(reference_sha256=plan['reference_sha256'],
        configuration=dict(creative_audio=True, workflow='reference_rough_skill_v1')))
    check = replay_check.validator(None, state, dict(name='plan_0'), dict(prompt=json.dumps(context)))
    if expected is None:
        check(plan)  # The real attempt now passes the full production validator.
    else:
        with pytest.raises(ValueError, match='^' + expected + '$') as caught:
            check(plan)
    assert len([c for c in plan['plan_normalisations'] if c['reason'] == 'program_line_window']) == len(plan['audio_draft'])
    if attempt == 1:
        trimmed, = [c for c in plan['plan_normalisations'] if c['reason'] == 'trim_for_storyboard_duration']
        assert trimmed['segment_id'] == before['segments'][-1]['segment_id']
        assert trimmed['excess_s'] == pytest.approx(45.5 - 45.4992108)
        assert trimmed['before'] - trimmed['after'] == pytest.approx(1 / 30)
        assert plan['segments'] == before['segments'][:-1] + [dict(before['segments'][-1], source_out_s=trimmed['after'])]
        contracts.validate_audio_draft(plan['audio_draft'], plan['segments'], fps=30, cast=plan.get('cast'))
    else:
        assert plan['segments'] == before['segments']
    if attempt == 0:
        violation = caught.value.violation
        assert violation['segment_ids'] == ['seg_6']
        assert violation['span_s'] == 5
        assert violation['demand_s'] == pytest.approx(5.04)
        assert violation['deficit_s'] == pytest.approx(.04)
        assert violation['spoken_chars_to_cut'] == 1
        assert [line['id'] for line in violation['lines']] == ['ad_07', 'ad_08', 'ad_09']
        assert violation['extendable_seconds']['seg_6'] == [dict(usable_range_index=0,
            source_in_s=3468.5, source_out_s=3473.5, extend_before_s=0, extend_after_s=0)]
    elif attempt == 2:
        assert caught.value.violation['path'] == '$.segments[0].source_in_s'
        assert caught.value.violation['value'] == 0
    snapshot = deepcopy(plan)
    if expected is None:
        check(plan)
    else:
        with pytest.raises(ValueError, match='^' + expected + '$'):
            check(plan)
    assert plan == snapshot


def test_s13_small_deficit_extends_two_frames_when_support_and_duration_allow():
    context, plan = read('s13_context'), read('s13_attempt_0')
    selected = plan['segments'][5]
    window = next(w for w in context['watched_windows'] if w['window_id'] == selected['window_id'])
    window['observation']['usable_ranges'][0]['local_out_s'] += .1
    # Isolate extension from the real plan's already exceeded storyboard cap.
    normalise_plan(plan, context['watched_windows'], catalog=context['catalog'], film_cast=context['film_cast'])
    assert selected['source_out_s'] == pytest.approx(3473.5 + 2 / 30)
    contracts.validate_audio_draft(plan['audio_draft'], plan['segments'], cast=plan['cast'], fps=30)


def test_s11_narration_golden_keeps_its_verdict_and_creative_fields():
    from test_fine_plan_mechanical import finecut
    evidence = read('s11_golden')
    plan = evidence['reply']
    creative = [{k: v for k, v in line.items() if k not in {'start_s', 'end_s'}} for line in plan['audio_draft']]
    flow = finecut(evidence)
    flow.plan_check(plan)
    assert plan['audio_draft'] == creative
    before = deepcopy(plan)
    flow.plan_check(plan)
    assert plan == before



def duration_case(*args, **kwargs):
    plan, catalog, window = case(*args, **kwargs)
    plan['focus_role_bindings'] = []  # Already normalized; isolate duration edits.
    return plan, catalog, window


def duration_context(low, high):
    return dict(storyboard=dict(storyboard=[dict(id='core', core=True, start_s=0,
        end_s=low, story_role='铺垫', lines=[])]), reference_duration_s=low / .8, max_duration_s=high)


@pytest.mark.parametrize('fps,speed', [(None, 1), (10, 1), (24, 2), (60, .5)])
@pytest.mark.parametrize('trimming', [True, False])
def test_storyboard_small_duration_repair_uses_output_frames_and_is_idempotent(fps, speed, trimming):
    import math
    plan, catalog, window = duration_case(4 * speed, second=None, fps=fps)
    plan['segments'][0]['speed'] = speed
    kwargs = duration_context(3, 3.91) if trimming else duration_context(4.09, 6)
    before = deepcopy(plan)
    normalise(plan, catalog, window, **kwargs)
    change, = [c for c in plan['plan_normalisations'] if c['reason'].endswith('_for_storyboard_duration')]
    assert change['reason'] == ('trim_for_storyboard_duration' if trimming else 'extend_for_storyboard_duration')
    amount = math.ceil(.09 * fps - 1e-9) / fps if fps else .09
    expected = 4 + (-amount if trimming else amount)
    assert (plan['segments'][0]['source_out_s'] - 1) / speed == pytest.approx(expected)
    assert change['before'] == before['segments'][0]['source_out_s']
    assert change['after'] == plan['segments'][0]['source_out_s']
    assert change['excess_s' if trimming else 'deficit_s'] == pytest.approx(.09)
    assert plan['audio_draft'] == before['audio_draft']
    assert plan['slots'] == before['slots']
    assert {k: v for k, v in plan['segments'][0].items() if k != 'source_out_s'} == {
        k: v for k, v in before['segments'][0].items() if k != 'source_out_s'}
    snapshot = deepcopy(plan)
    normalise(plan, catalog, window, **kwargs)
    assert plan == snapshot


@pytest.mark.parametrize('trimming', [True, False])
@pytest.mark.parametrize('difference', [1, 1.00001])
def test_storyboard_duration_repair_has_strict_one_second_budget(trimming, difference):
    plan, catalog, window = duration_case(4, second=None)
    kwargs = duration_context(2.5, 4 - difference) if trimming else duration_context(4 + difference, 7)
    before = deepcopy(plan)
    normalise(plan, catalog, window, **kwargs)
    if difference > 1:
        assert plan == before
    else:
        assert plan['segments'][0]['source_out_s'] == pytest.approx(5 + (-1 if trimming else 1))


def test_storyboard_trim_refused_atomically_if_any_group_or_anchor_would_fail():
    for anchor in (False, True):
        plan, catalog, window = duration_case(4, second=None)
        if anchor:
            plan['audio_draft'][0]['start_anchor'] = dict(segment_id='seg', offset_s=3.2)
            receipts = []  # This group has .8 seconds after its anchor, enough for a short line.
            plan['audio_draft'][0]['text'] = '甲'
        else:
            receipts = [dict(text=plan['audio_draft'][0]['text'], voice='Ethan', actual_duration_s=4)]
        before = deepcopy(plan)
        normalise(plan, catalog, window, received_tts=receipts, **duration_context(3, 3.8))
        assert plan == before


def test_storyboard_trim_walks_backwards_without_emptying_any_slot_or_core_cell():
    plan, catalog, window = duration_case(2, second=None, fps=10)
    original = plan['segments'][0]
    plan['segments'].append(dict(original, segment_id='tail', slot_id='tail_slot', source_in_s=10, source_out_s=10.3))
    plan['slots'][0]['storyboard_cell_ids'] = []
    plan['slots'].append(dict(slot_id='tail_slot', intended_takeaway='结尾', segment_ids=['tail'], storyboard_cell_ids=['core']))
    plan['audio_draft'][0].update(text='甲', segment_ids=['seg', 'tail'])
    before = deepcopy(plan)
    kwargs = duration_context(1, 1.3)
    normalise(plan, catalog, window, **kwargs)
    changes = [c for c in plan['plan_normalisations'] if c['reason'] == 'trim_for_storyboard_duration']
    assert [c['segment_id'] for c in changes] == ['tail', 'seg']
    assert [c['before'] - c['after'] for c in changes] == pytest.approx([.2, .8])
    assert plan['slots'] == before['slots']
    assert plan['audio_draft'] == before['audio_draft']
    timeline = line_windows.segment_output_timeline(plan['segments'], fps=10)
    assert timeline['tail']['output_duration_s'] == pytest.approx(.1)
    contracts.validate_plan_storyboard(plan, dict(reference_storyboard=kwargs['storyboard']),
        kwargs['reference_duration_s'], [window], catalog, max_duration_s=kwargs['max_duration_s'])


def test_storyboard_trim_refused_if_it_would_remove_the_last_core_slot_frame():
    plan, catalog, window = duration_case(.1, text='甲', second=None, fps=10)
    plan['audio_draft'] = []
    plan['slots'][0]['storyboard_cell_ids'] = ['core']
    plan['segments'][0]['freeze_tail_s'] = 2  # A hold cannot replace the last motion frame.
    before = deepcopy(plan)
    normalise(plan, catalog, window, speech_mode='none', **duration_context(.04, .05))
    assert plan == before


@pytest.mark.parametrize('reason', ['range', 'role_union', 'role_event', 'content', 'adjacent'])
def test_storyboard_extension_uses_the_s13_support_and_neighbour_limits(reason):
    plan, catalog, window = duration_case(4, second=None)
    usable = window['observation']['usable_ranges'][0]
    usable.update(local_in_s=1, local_out_s=5)
    if reason in {'role_union', 'role_event'}:
        window['observation']['roles'] = [dict(role_id='A', identity_confirmed=True)]
        plan['segments'][0]['role_ids'] = ['A']
        usable['role_ids'] = ['A']
        window['observation']['events'][0]['role_ids'] = [] if reason == 'role_event' else ['A']
        if reason == 'role_union':
            window['observation']['usable_ranges'].append(dict(usable, local_in_s=0, local_out_s=30, role_ids=[]))
    elif reason == 'content':
        usable.update(local_in_s=0, local_out_s=30)
        catalog['sources'][0]['content_range'] = dict(content_start_s=1, content_end_s=5)
    elif reason == 'adjacent':
        usable.update(local_in_s=0, local_out_s=30)
        original = plan['segments'][0]
        plan['segments'] = [dict(original, segment_id='previous', source_in_s=0, source_out_s=1), original,
                            dict(original, segment_id='next', source_in_s=5, source_out_s=30)]
        plan['slots'][0]['segment_ids'] = ['previous', 'seg', 'next']
    duration = sum(s['source_out_s'] - s['source_in_s'] for s in plan['segments'])
    before = deepcopy(plan)
    normalise(plan, catalog, window, **duration_context(duration + .2, duration * 1.5))
    assert plan == before


def test_storyboard_extension_uses_start_before_partial_end_and_retries_earlier_segments():
    plan, catalog, window = duration_case(4, second=None, fps=10)
    window['observation']['usable_ranges'][0]['local_out_s'] = 5.1
    normalise(plan, catalog, window, **duration_context(4.4, 6))
    assert plan['segments'][0]['source_in_s'] == pytest.approx(.6)
    assert plan['segments'][0]['source_out_s'] == 5
    snapshot = deepcopy(plan)
    normalise(plan, catalog, window, **duration_context(4.4, 6))
    assert plan == snapshot

    plan, catalog, window = duration_case(4, second=None, fps=10)
    plan['segments'].append(dict(plan['segments'][0], segment_id='tail', source_in_s=29, source_out_s=30))
    plan['slots'][0]['segment_ids'].append('tail')
    window['observation']['usable_ranges'] = [dict(window['observation']['usable_ranges'][0], local_in_s=a, local_out_s=b)
                                              for a, b in [(1, 5.2), (29, 30)]]
    normalise(plan, catalog, window, **duration_context(5.2, 7))
    change, = [c for c in plan['plan_normalisations'] if c['reason'] == 'extend_for_storyboard_duration']
    assert change['segment_id'] == 'seg'
    assert change['after'] == pytest.approx(5.2)


def test_storyboard_duration_repair_leaves_all_segments_when_only_partial_extension_exists():
    plan, catalog, window = duration_case(4, second=None, fps=10)
    window['observation']['usable_ranges'][0].update(local_in_s=1, local_out_s=5.1)
    before = deepcopy(plan)
    normalise(plan, catalog, window, **duration_context(4.2, 6))
    assert plan == before


def test_storyboard_extension_can_share_supported_capacity_last_segment_first():
    plan, catalog, window = duration_case(4, second=None, fps=10)
    plan['segments'].append(dict(plan['segments'][0], segment_id='tail', source_in_s=10, source_out_s=11))
    plan['slots'][0]['segment_ids'].append('tail')
    usable = window['observation']['usable_ranges'][0]
    window['observation']['usable_ranges'] = [dict(usable, local_in_s=a, local_out_s=b)
                                              for a, b in [(1, 5.2), (10, 11.1)]]
    normalise(plan, catalog, window, **duration_context(5.3, 7))
    changes = [c for c in plan['plan_normalisations'] if c['reason'] == 'extend_for_storyboard_duration']
    assert [c['segment_id'] for c in changes] == ['tail', 'seg']
    assert [c['after'] - c['before'] for c in changes] == pytest.approx([.1, .2])


@pytest.mark.parametrize('trimming', [True, False])
def test_storyboard_frame_rounding_cannot_overshoot_the_opposite_bound(trimming):
    plan, catalog, window = duration_case(4, second=None, fps=10)
    kwargs = duration_context(3.94, 3.96) if trimming else duration_context(4.04, 4.06)
    before = deepcopy(plan)
    normalise(plan, catalog, window, **kwargs)
    assert plan == before


@pytest.mark.parametrize('reason', ['role_event', 'caption_end', 'caption_event', 'malformed_caption_event'])
def test_storyboard_trim_preserves_existing_role_and_caption_constraints(reason):
    plan, catalog, window = duration_case(4, second=None)
    tail_event = dict(local_start_s=4.9, local_end_s=5, role_ids=[], observed_fact='尾部动作')
    window['observation']['events'].append(tail_event)
    if reason == 'role_event':
        window['observation']['roles'] = [dict(role_id='A', identity_confirmed=True)]
        plan['segments'][0]['role_ids'] = ['A']
        window['observation']['usable_ranges'][0]['role_ids'] = ['A']
        tail_event['role_ids'] = ['A']
    else:
        plan['segments'][0]['caption'] = dict(text='字幕', start_s=0,
            end_s=4 if reason == 'caption_end' else 1,
            evidence=[dict(window_id='w', event_indices=[99 if reason == 'malformed_caption_event' else 1])])
    before = deepcopy(plan)
    normalise(plan, catalog, window, **duration_context(3, 3.8))
    assert plan == before


@pytest.mark.parametrize('kwargs', [{}, dict(storyboard=dict(storyboard=[]))])
def test_storyboard_duration_repair_requires_an_applicable_range(kwargs):
    plan, catalog, window = duration_case(4, second=None)
    before = deepcopy(plan)
    normalise(plan, catalog, window, **kwargs)
    assert plan == before


@pytest.mark.parametrize('low,high', [(2, 2.9), (5.1, 7)])
def test_remaining_storyboard_duration_feedback_has_budget_and_absolute_source_domain(low, high):
    plan, catalog, window = duration_case(4, second=None)
    kwargs = duration_context(low, high)
    before = deepcopy(plan)
    normalise(plan, catalog, window, **kwargs)
    assert plan == before  # Both misses exceed the mechanical one-second budget.
    with pytest.raises(ValueError, match='^plan:storyboard_duration_out_of_range$') as caught:
        contracts.validate_plan_storyboard(plan, dict(reference_storyboard=kwargs['storyboard']),
            kwargs['reference_duration_s'], [window], catalog, max_duration_s=high)
    detail = caught.value.violation['expected']
    assert detail['actual_duration_s'] == 4
    assert detail['target_duration_s'] == [low, high]
    assert detail['excess_s'] == pytest.approx(max(0, 4 - high))
    assert detail['deficit_s'] == pytest.approx(max(0, low - 4))
    feedback = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', validation_error=caught.value))
    hint = next(row['hint'] for row in feedback if row['path'] == '$.segments')
    assert f'当前总时长4.0秒，允许范围[{detail["target_duration_s"][0]}, {detail["target_duration_s"][1]}]秒' in hint
    assert '超出上限' in hint and '低于下限' in hint
    assert '原片绝对秒数' in hint and '不能填写输出时间轴秒数' in hint
