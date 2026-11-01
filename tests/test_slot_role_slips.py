"""S15: redundant slots and subinterval role evidence, without creative repair."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, protocol_feedback
from omni_story.library.plan_normalisation import normalise_plan, normalise_storyboard_slots
from test_plan_normalisation import dialogue_plan, evidence_plan
from test_window_local_times import production_check


FIXTURES = Path(__file__).with_name('fixtures')


def read(name):
    return json.loads((FIXTURES / ('s15_' + name + '.json')).read_text())


def changes(plan, reason):
    return [c for c in plan.get('plan_normalisations', []) if c['reason'] == reason]


@pytest.mark.parametrize('attempt', [0, 1, 2])
def test_real_replies_report_the_next_production_violation(attempt):
    # Verbatim model_text from fix7/glm_202, 203, 204 protocol_failure.json;
    # context is the identical JSON object in all three recorded request prompts.
    context, plan = read('context'), read(f'attempt_{attempt}')
    original = deepcopy(plan)
    check = production_check(context, plan)
    result = dict(attempt=attempt, passed=attempt < 2)
    if attempt < 2:
        check(plan)
        dropped, = changes(plan, 'drop_empty_slot_cells_carried')
        assert dropped['slot_id'] == 'b1' and dropped['carrier_slot_ids'] == ['b3']
        assert dropped['cells'] == original['slots'][0]['storyboard_cell_ids']
        assert all(s['slot_id'] != 'b1' for s in plan['slots'])
        assert plan['segments'] == original['segments']
    else:
        with pytest.raises(ValueError) as caught:
            check(plan)
        error, detail = caught.value, caught.value.violation
        result.update(violation=str(error), **{key: detail[key] for key in ('path', 'value', 'expected')})
        assert str(error) == 'audio_line_window_below_minimum'
        assert detail['path'] == '$.audio_draft[11]'
        assert detail['value'] == 3.7333333333333307
        assert detail['expected'] == 4.859999999999999
        dropped, = changes(plan, 'drop_role_without_event_evidence')
        assert dropped == dict(path='$.segments[4].role_ids', before=['A', 'B'], after=['B'],
            reason='drop_role_without_event_evidence', segment_id='seg4',
            window_id='window_c801dc7f9b36a28f', role_id='A', interval=[2389, 2392])
        assert plan['segments'][4]['role_ids'] == ['B']
        assert plan['segments'] == [dict(s, role_ids=['B']) if i == 4 else s
                                    for i, s in enumerate(original['segments'])]
    # The own-beat merge is redundant; all three genuine merges still count.
    assert plan['storyboard_merges'] == original['storyboard_merges'][:3]
    noop, = changes(plan, 'drop_noop_storyboard_merge')
    assert noop == dict(path='$.storyboard_merges[3]', before=original['storyboard_merges'][3],
        after=None, reason='drop_noop_storyboard_merge', cell_id='s7', into_slot_id='b9')
    contracts.validate_plan_storyboard(plan, context['reference'], context['reference_duration_s'],
        context['watched_windows'], context['catalog'], storyboard_beats=context['storyboard_beats'],
        max_duration_s=None, max_segments=None)
    if attempt < 2:
        assert [c['path'] for c in changes(plan, 'anchored_relation_offscreen')] == [
            '$.audio_draft[7].audio_relation[0].relation', '$.audio_draft[13].audio_relation[0].relation']
        for index in (7, 13, 16):
            assert plan['audio_draft'][index]['audio_relation'][0]['relation'] == 'offscreen'
            assert plan['audio_draft'][index]['identity_anchor_segment_id'] == 'seg5'
    assert set(c for slot in plan['slots'] for c in slot['storyboard_cell_ids']) == {
        cell['id'] for cell in context['reference']['reference_storyboard']['storyboard']}
    assert [line['text'] for line in plan['audio_draft']] == [line['text'] for line in original['audio_draft']]
    print(json.dumps(result, ensure_ascii=False))
    snapshot = deepcopy(plan)
    if attempt < 2:
        check(plan)
    else:
        with pytest.raises(ValueError, match='^' + str(error) + '$'):
            check(plan)
    assert plan == snapshot


def slot_case():
    return dict(segments=[dict(segment_id='seg', slot_id='carrier')], slots=[
        dict(slot_id='empty', segment_ids=[], storyboard_cell_ids=['c1']),
        dict(slot_id='carrier', segment_ids=['seg'], storyboard_cell_ids=['c1', 'c2'])])


@pytest.mark.parametrize('duplicate', [False, True])
def test_drop_only_redundant_slot_after_rebuilding_ownership(duplicate):
    plan = slot_case()
    if duplicate:
        plan['slots'][0]['segment_ids'] = ['seg']
    normalise_storyboard_slots(plan)
    assert [s['slot_id'] for s in plan['slots']] == ['carrier']
    dropped, = changes(plan, 'drop_empty_slot_cells_carried')
    assert dropped['slot_id'] == 'empty' and dropped['cells'] == ['c1']
    assert dropped['carrier_slot_ids'] == ['carrier']
    snapshot = deepcopy(plan)
    normalise_storyboard_slots(plan)
    assert plan == snapshot


@pytest.mark.parametrize('reference', ['uncarried', 'merge_target', 'other_field', 'mapping_key'])
def test_keep_empty_slot_with_uncovered_cell_or_any_live_reference(reference):
    plan = slot_case()
    if reference == 'uncarried':
        plan['slots'][0]['storyboard_cell_ids'].append('uncovered')
    elif reference == 'merge_target':
        plan['storyboard_merges'] = [dict(cell_id='c2', into_slot_id='empty', reason='declared target')]
    elif reference == 'other_field':
        plan['editing_bindings'] = [dict(slot_ids=['empty'])]
    else:
        plan['slot_annotations'] = {'empty': dict(note='keep this reference')}
    snapshot = deepcopy(plan)
    normalise_storyboard_slots(plan, dict(storyboard=[dict(id='c2', start_s=0, core=True)]))
    assert plan == snapshot
    assert not changes(plan, 'drop_empty_slot_cells_carried')


def test_merge_can_supply_carrier_and_core_cell_stays_explicitly_covered():
    plan = slot_case()
    plan['slots'][1]['storyboard_cell_ids'] = ['c2']
    plan['storyboard_merges'] = [dict(cell_id='c1', into_slot_id='carrier', reason='model carrying choice')]
    storyboard = dict(storyboard=[dict(id='c1', start_s=0, core=True), dict(id='c2', start_s=1)])
    normalise_storyboard_slots(plan, storyboard)
    assert len(plan['slots']) == 1
    assert plan['slots'][0]['storyboard_cell_ids'] == ['c1', 'c2']
    assert plan['storyboard_merges'] == []
    snapshot = deepcopy(plan)
    normalise_storyboard_slots(plan, storyboard)
    assert plan == snapshot


def test_cells_can_be_carried_by_different_occupied_slots():
    plan = slot_case()
    plan['slots'][0]['storyboard_cell_ids'] = ['c1', 'c2']
    plan['slots'][1]['storyboard_cell_ids'] = ['c1']
    plan['segments'].append(dict(segment_id='other_seg', slot_id='other'))
    plan['slots'].append(dict(slot_id='other', segment_ids=['other_seg'], storyboard_cell_ids=['c2']))
    normalise_storyboard_slots(plan)
    dropped, = changes(plan, 'drop_empty_slot_cells_carried')
    assert dropped['cells'] == ['c1', 'c2']
    assert dropped['carrier_slot_ids'] == ['carrier', 'other']


@pytest.mark.parametrize('duplicate', [False, True])
def test_remaining_slot_feedback_names_cells_and_carriers(duplicate):
    plan = slot_case()
    if duplicate:
        plan['slots'][0]['segment_ids'] = ['seg']
    else:
        plan['slots'][0]['storyboard_cell_ids'].append('uncovered')
    error = 'plan:segments_must_belong_to_exactly_one_slot' if duplicate else 'plan/slot_segments:list_required'
    snapshot = deepcopy(plan)
    rows = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan',
        validation_error=error, require_audio_draft=False))
    row = next(r for r in rows if r['path'] == '$.slots[0].segment_ids')
    assert row['expected'] == dict(slot_id='empty',
        storyboard_cell_ids=['c1'] if duplicate else ['c1', 'uncovered'], owned_segment_ids=[],
        cell_carriers={'c1': ['carrier']} if duplicate else {'c1': ['carrier'], 'uncovered': []})
    assert all(text in row['hint'] for text in ('empty', 'c1', 'carrier'))
    assert plan == snapshot


@pytest.mark.parametrize('start,end,expected', [(17.05, 20, []), (17, 20, []),
    (16.999, 20, ['A']), (14, 17, ['A']), (14, 14.001, ['A'])])
def test_role_evidence_uses_validator_strict_intersection(start, end, expected):
    plan, _, window = evidence_plan()
    window['observation']['events'][0].update(local_start_s=14, local_end_s=17)
    plan['segments'][0].update(source_in_s=start, source_out_s=end)
    plan['audio_draft'] = []  # Isolate role evidence from existing line-window extension.
    segment = deepcopy(plan['segments'][0])
    rule = contracts.plan_segment_role_contract(window, start=start, end=end)
    assert rule['overlapping_role_ids'] == expected
    normalise_plan(plan, [window])
    assert plan['segments'][0] == dict(segment, role_ids=expected)
    contracts.validate_plan_segment_roles(plan['segments'][0], window, '$.segments[0]')
    assert bool(changes(plan, 'drop_role_without_event_evidence')) == (not expected)
    snapshot = deepcopy(plan)
    normalise_plan(plan, [window])
    assert plan == snapshot


def test_unconfirmed_and_unknown_roles_are_kept_for_validator():
    plan, _, window = evidence_plan()
    window['observation']['roles'][0]['identity_confirmed'] = False
    plan['segments'][0]['role_ids'] = ['A', 'unknown']
    normalise_plan(plan, [window])
    assert plan['segments'][0]['role_ids'] == ['A', 'unknown']
    with pytest.raises(ValueError, match='plan/confirmed_roles'):
        contracts.validate_plan_segment_roles(plan['segments'][0], window, '$.segments[0]')


@pytest.mark.parametrize('valid_observation', [False, True])
def test_valid_evidence_can_drop_a_confirmed_role_with_no_actor_events(valid_observation):
    plan, _, window = evidence_plan()
    observation = window['observation']
    observation['events'][0]['role_ids'] = []
    if valid_observation:
        observation['usable_ranges'][0]['role_ids'] = []
        contracts.validate_fine(observation, window)
    else:
        with pytest.raises(ValueError, match='fine:usable_role_missing_event_evidence'):
            contracts.validate_fine(observation, window)
    normalise_plan(plan, [window])
    assert plan['segments'][0]['role_ids'] == ([] if valid_observation else ['A'])
    assert bool(changes(plan, 'drop_role_without_event_evidence')) == valid_observation


@pytest.mark.parametrize('alternative', [False, True])
def test_dropped_role_leaves_dialogue_to_existing_rules_and_preserves_cast(alternative):
    plan, catalog, window = evidence_plan()
    # Establish canonical bindings once, then remove A only from the selected
    # subinterval. The cast/focus references must not be pruned to make it pass.
    normalise_plan(plan, [window])
    cast, focus = deepcopy(plan['cast']), deepcopy(plan['focus_role_bindings'])
    window['observation']['events'][0].update(local_start_s=10, local_end_s=15)
    if alternative:
        plan['segments'].append(dict(plan['segments'][0], segment_id='visible', source_in_s=10, source_out_s=14))
        plan['slots'][0]['segment_ids'].append('visible')
    normalise_plan(plan, [window], speech_mode='dialogue')
    assert plan['segments'][0]['role_ids'] == []
    assert plan['cast'] == cast and plan['focus_role_bindings'] == focus
    if alternative:
        assert plan['audio_draft'][0]['segment_ids'] == ['visible']
        assert changes(plan, 'nearest_visible_segment_fits_line_window')
        contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'], require_audio_draft=True)
        contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'], cast=cast, windows=[window])
    else:
        assert plan['audio_draft'][0]['segment_ids'] == ['seg']
        with pytest.raises(ValueError, match='plan:dialogue_speaker_not_in_selected_segments'):
            contracts.validate_dialogue_speakers(plan['audio_draft'], plan['segments'], cast=cast, windows=[window])
        with pytest.raises(ValueError, match='plan:focus_role_not_confirmed_in_selected_footage'):
            contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'], require_audio_draft=True)
    snapshot = deepcopy(plan)
    normalise_plan(plan, [window], speech_mode='dialogue')
    assert plan == snapshot


@pytest.mark.parametrize('target', ['occupied', 'empty', 'duplicate', 'absent'])
@pytest.mark.parametrize('cells_present', [False, True])
@pytest.mark.parametrize('beat_form', ['beat', 'slot'])
def test_noop_merge_requires_owning_beat_and_occupied_slot(target, cells_present, beat_form):
    plan = slot_case()
    if not cells_present:
        plan['slots'][1].pop('storyboard_cell_ids')
    if target in {'empty', 'duplicate'}:
        plan['segments'][0]['slot_id'] = 'empty'
        plan['slots'][0]['segment_ids'] = ['seg']
        if target == 'empty':
            plan['slots'][1]['segment_ids'] = []
    elif target == 'absent':
        plan['slots'].pop(1)
        plan['segments'][0]['slot_id'] = 'empty'
        plan['slots'][0]['segment_ids'] = ['seg']
    real = dict(cell_id='c1', into_slot_id='carrier', reason='other beat carried here')
    noop = dict(cell_id='c2', into_slot_id='carrier', reason='own beat already carries it')
    plan['storyboard_merges'] = [real, noop]
    storyboard = dict(storyboard=[dict(id='c1', start_s=0), dict(id='c2', start_s=1)])
    # The production slot form includes absorbed cells from other beats.
    beats = ([dict(id='carrier', reference_evidence=dict(cell_ids=['c2']))] if beat_form == 'beat' else
        [dict(slot_id='carrier', cell_ids=['c2', 'c1'], absorbed_cell_ids=['c1'])])
    normalise_storyboard_slots(plan, storyboard, storyboard_beats=beats)
    assert plan['storyboard_merges'] == ([real] if target == 'occupied' else [real, noop])
    assert len(changes(plan, 'drop_noop_storyboard_merge')) == (target == 'occupied')
    if target == 'occupied':
        assert plan['slots'][-1]['storyboard_cell_ids'] == (['c1', 'c2'] if cells_present else ['c2'])
    snapshot = deepcopy(plan)
    normalise_storyboard_slots(plan, storyboard, storyboard_beats=beats)
    assert plan == snapshot


@pytest.mark.parametrize('anchor', ['s0', 's1', 's2', 'unknown', None])
@pytest.mark.parametrize('windows', [(), None])
def test_only_valid_anchor_changes_invisible_on_screen_relation(anchor, windows):
    plan = dialogue_plan()
    line = plan['audio_draft'][0]
    line.pop('start_s')
    line.pop('end_s')
    # One selected segment is visible, so existing speaker relocation is skipped
    # in every case and cannot mask this repair's valid-anchor restriction.
    line['segment_ids'] = ['s0', 's1']
    line['audio_relation'] = [dict(segment_id='s0', relation='on_screen'),
        dict(segment_id='s1', relation='on_screen', note='preserve metadata'),
        dict(segment_id='s1', relation='offscreen')]
    if anchor is not None:
        line['identity_anchor_segment_id'] = anchor
    original = deepcopy(line)
    normalise_plan(plan, windows)
    expected = deepcopy(original)
    if anchor == 's0':
        expected['audio_relation'][1]['relation'] = 'offscreen'
        contracts.validate_dialogue_speakers([line], plan['segments'])
    assert line == expected
    edits = changes(plan, 'anchored_relation_offscreen')
    assert edits == ([dict(path='$.audio_draft[0].audio_relation[1].relation',
        before='on_screen', after='offscreen', reason='anchored_relation_offscreen')] if anchor == 's0' else [])
    snapshot = deepcopy(plan)
    normalise_plan(plan, windows)
    assert plan == snapshot


@pytest.mark.parametrize('visibility', ['local', 'visual_event', 'entity_ids', 'entity_role'])
def test_anchor_and_relation_visibility_match_validator(visibility):
    plan = dialogue_plan()
    line = plan['audio_draft'][0]
    line.pop('start_s')
    line.pop('end_s')
    line['identity_anchor_segment_id'] = 's0'
    line['segment_ids'] = ['s0', 's1']
    line['audio_relation'].append(dict(segment_id='s0', relation='on_screen'))
    anchor = plan['segments'][0]
    windows = []
    if visibility != 'local':
        anchor.update(window_id='other', role_ids=[])
    if visibility == 'visual_event':
        anchor['visual_events'] = [dict(window_id='w', role_ids=['B'])]
    elif visibility.startswith('entity_'):
        # The validator includes observed entity aliases even for unconfirmed
        # roles; this repair must not substitute a different visibility rule.
        windows = [dict(window_id='w', observation=dict(roles=[dict(role_id='B', entity_id='same')])),
            dict(window_id='other', observation=dict(roles=[dict(role_id='C', entity_id='same')]))]
        if visibility == 'entity_ids':
            anchor['entity_ids'] = ['same']
        else:
            anchor['role_ids'] = ['C']
    # Visibility comes from the anchor; s1 remains a reaction shot.
    check_line = deepcopy(line)
    check_line['audio_relation'][0]['relation'] = 'offscreen'
    contracts.validate_dialogue_speakers([check_line], plan['segments'], windows=windows)
    normalise_plan(plan, windows)
    assert line['identity_anchor_segment_id'] == 's0'
    assert line['audio_relation'] == check_line['audio_relation']
    assert len(changes(plan, 'anchored_relation_offscreen')) == 1
    contracts.validate_dialogue_speakers([line], plan['segments'], windows=windows)
    snapshot = deepcopy(plan)
    normalise_plan(plan, windows)
    assert plan == snapshot
