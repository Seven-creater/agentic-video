"""Window-local source seconds need unique evidence for the entire reply."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, protocol_feedback, replay_check
from omni_story.library.plan_normalisation import normalise_plan


FIXTURES = Path(__file__).with_name('fixtures')
TIME_FIELDS = ('source_in_s', 'source_out_s')
REASON = 'map_window_local_time_to_source'


def read(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def production_check(context, plan):
    # Bind the actual pipeline closure, including normalisation, line windows,
    # storyboard policy, repeated-footage checks and dialogue validation.
    state = dict(input_lock=dict(reference_sha256=plan['reference_sha256'],
        configuration=dict(creative_audio=True, workflow='reference_rough_skill_v1')))
    return replay_check.validator(None, state, dict(name='plan_0'), dict(prompt=json.dumps(context)))


def mappings(plan):
    return [c for c in plan.get('plan_normalisations', []) if c['reason'] == REASON]


def case():
    window = dict(window_id='w', source_id='film', status='watched',
        source_offset_s=100, source_start_s=100, source_end_s=110,
        observation=dict(roles=[dict(role_id='A', identity_confirmed=True)],
            events=[dict(local_start_s=0, local_end_s=10, role_ids=['A'])],
            usable_ranges=[dict(local_in_s=1, local_out_s=9, role_ids=['A'])]))
    segment = dict(segment_id='s', window_id='w', source_id='film', slot_id='slot',
        source_in_s=2, source_out_s=4, role_ids=['A'], speed=1, reason='keep this text')
    plan = dict(segments=[segment], focus_role_bindings=[],
        slots=[dict(slot_id='slot', segment_ids=['s'])], audio_draft=[])
    return plan, window


def test_fix6_attempt_2_maps_all_twelve_then_passes_full_production_validation():
    # Extracted verbatim from fix6/glm_223_plan_0_repair2 protocol_failure.json
    # and the watched_windows/catalog/reference context in its request.json.
    context, plan = read('s14_context'), read('s14_attempt_2')
    before = deepcopy(plan)
    windows = {w['window_id']: w for w in context['watched_windows']}
    assert len(plan['segments']) == 12
    check = production_check(context, plan)
    check(plan)
    assert len(mappings(plan)) == 12
    for index, (old, new, change) in enumerate(zip(before['segments'], plan['segments'], mappings(plan))):
        offset = windows[old['window_id']]['source_offset_s']
        expected = {key: old[key] + offset for key in TIME_FIELDS}
        assert new == dict(old, **expected)
        assert change == dict(path=f'$.segments[{index}]',
            before={key: old[key] for key in TIME_FIELDS}, after=expected,
            reason=REASON, source_offset_s=offset)
    assert [line['text'] for line in plan['audio_draft']] == [line['text'] for line in before['audio_draft']]
    snapshot = deepcopy(plan)
    check(plan)
    assert plan == snapshot


def test_fix5_output_timeline_counterexample_is_unchanged_and_still_rejected():
    # Existing S13 fixtures exactly match glm_216_plan_0_repair2's model_text
    # and its request context; only seg_1 would map to a supported range.
    context, plan = read('s13_context'), read('s13_attempt_2')
    before = deepcopy(plan)
    check = production_check(context, plan)
    with pytest.raises(ValueError, match='^visual_library:range_outside_content$') as caught:
        check(plan)
    assert plan['segments'] == before['segments']
    assert not mappings(plan)
    assert caught.value.violation['path'] == '$.segments[0].source_in_s'
    snapshot = deepcopy(plan)
    with pytest.raises(ValueError, match='^visual_library:range_outside_content$'):
        check(plan)
    assert plan == snapshot

    feedback = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan',
        windows=context['watched_windows'], catalog=context['catalog'], validation_error=caught.value))
    first = feedback[0]
    window = next(w for w in context['watched_windows'] if w['window_id'] == plan['segments'][0]['window_id'])
    offset = window['source_offset_s']
    assert '原片绝对秒数 = source_offset_s + local time' in first['hint']
    assert f'source_offset_s={offset}秒' in first['hint']
    assert json.dumps(first['expected']['allowed_source_ranges'], ensure_ascii=False) in first['hint']
    assert plan == snapshot


@pytest.mark.parametrize('as_dict', [False, True])
def test_unique_local_range_changes_only_times_with_audit_and_is_idempotent(as_dict):
    plan, window = case()
    before = deepcopy(plan)
    windows = {window['window_id']: window} if as_dict else [window]
    normalise_plan(plan, windows)
    assert plan['segments'] == [dict(before['segments'][0], source_in_s=102, source_out_s=104)]
    assert {k: v for k, v in plan.items() if k != 'plan_normalisations'} == dict(
        before, segments=plan['segments'])
    assert mappings(plan) == [dict(path='$.segments[0]',
        before=dict(source_in_s=2, source_out_s=4), after=dict(source_in_s=102, source_out_s=104),
        reason=REASON, source_offset_s=100)]
    snapshot = deepcopy(plan)
    normalise_plan(plan, windows)
    assert plan == snapshot


@pytest.mark.parametrize('start,end', [(102, 104), (99.9989995, 104), (102, 110.001)])
def test_absolute_window_times_use_validator_tolerance_and_stay_untouched(start, end):
    plan, window = case()
    window['observation']['usable_ranges'][0].update(local_in_s=0, local_out_s=10)
    plan['segments'][0].update(source_in_s=start, source_out_s=end)
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before


def test_ambiguous_two_usable_ranges_stay_untouched():
    plan, window = case()
    window['observation']['usable_ranges'].append(dict(local_in_s=2, local_out_s=5, role_ids=['A']))
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before


@pytest.mark.parametrize('mismatch', ['usable_range', 'unconfirmed', 'event', 'event_boundary'])
def test_role_rules_are_identical_to_the_validator(mismatch):
    plan, window = case()
    observation = window['observation']
    if mismatch == 'usable_range':
        observation['usable_ranges'][0]['role_ids'] = []
    elif mismatch == 'unconfirmed':
        observation['roles'][0]['identity_confirmed'] = False
    else:
        observation['events'][0]['local_start_s'] = 4 if mismatch == 'event_boundary' else 5
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before


@pytest.mark.parametrize('reverse', [False, True])
def test_partial_convertibility_changes_none_in_either_order(reverse):
    plan, window = case()
    plan['segments'].append(dict(plan['segments'][0], segment_id='bad', source_in_s=8, source_out_s=10))
    if reverse:
        plan['segments'].reverse()
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before


def test_reply_can_mix_absolute_and_uniquely_convertible_local_segments():
    plan, window = case()
    plan['segments'].append(dict(plan['segments'][0], segment_id='absolute', source_in_s=106, source_out_s=108))
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan['segments'] == [dict(before['segments'][0], source_in_s=102, source_out_s=104), before['segments'][1]]
    assert len(mappings(plan)) == 1


@pytest.mark.parametrize('missing', ['source_offset_s', 'watched'])
def test_mapping_needs_a_trusted_watched_window_offset(missing):
    plan, window = case()
    if missing == 'watched':
        window['status'] = 'pending'
    else:
        window.pop(missing)
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == before


def test_mapping_uses_source_offset_and_respects_catalog_content_bounds():
    plan, window = case()
    window['source_offset_s'] = 101
    before = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan['segments'] == [dict(before['segments'][0], source_in_s=103, source_out_s=105)]
    plan = deepcopy(before)
    catalog = dict(sources=[dict(source_id='film', duration_s=200,
        content_range=dict(content_start_s=104, content_end_s=150))])
    normalise_plan(plan, [window], catalog=catalog)
    assert plan == before


def test_unconverted_local_range_feedback_includes_offset_and_absolute_choices():
    plan, window = case()
    window['observation']['usable_ranges'] *= 2
    normalise_plan(plan, [window])
    choices = contracts.plan_source_range_contract(window)
    error = contracts.plan_field_error('visual_library:range_outside_content',
        '$.segments[0].source_in_s', 2, 'selected_range', allowed=choices)
    before = deepcopy(plan)
    rows = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', windows=[window], validation_error=error))
    for row in (rows[0], next(row for row in rows if row['path'].endswith('.source_in_s/source_out_s'))):
        assert '原片绝对秒数 = source_offset_s + local time' in row['hint']
        assert 'source_offset_s=100秒' in row['hint']
        assert json.dumps(choices['allowed_source_ranges'], ensure_ascii=False) in row['hint']
    assert plan == before
