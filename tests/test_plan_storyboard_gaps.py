"""Real s10 replies and evidence; pure local normalisation/validation only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, plan_normalisation, prompts, replay_check
from test_plan_storyboard import check, fixture


FIXTURES = Path(__file__).parent / 'fixtures'


def read(name):
    return json.loads((FIXTURES / f's10_{name}.json').read_text())


def real_validator(context):
    reference = context['reference']
    return replay_check.nested_validator(pipeline.execute, 'validate_current_plan',
        context=context, creative_audio=True, editing_reference=None, editing_v2=False,
        maximum_segments=32, progress_driven=True,
        ref=dict(sha256=reference['reference_sha256'], duration_s=context['reference_duration_s'],
                 audio_stream_index=context['reference_audio_stream_index']),
        reference_reading=reference, semantic_audit=False, semantic_pipeline=None,
        sources=context['catalog'], strategy=contracts.reference_strategy(reference, creative_audio=True),
        visual_only=True, windows=context['watched_windows'])


@pytest.mark.parametrize('attempt', [1, 2])
def test_real_replies_repair_membership_and_carry_core_without_new_creative_content(attempt):
    context, plan = read('context'), read(f'attempt_{attempt}')
    original = deepcopy(plan)
    old_count = len(plan.get('plan_normalisations', []))
    assert [g['beat_id'] for g in context['search_0']['question_first_gaps']
            if g['reason'] == 'no_minimal_visual_candidate'] == ['b1', 'b5']
    assert all(not b['candidates'] for b in context['storyboard_beats'] if b['id'] in {'b1', 'b5'})
    plan_normalisation.normalise_plan(plan, context['watched_windows'], catalog=context['catalog'],
        film_cast=context['film_cast'], speech_mode=context['reference']['strategy']['speech_mode'],
        fps=plan['fps'], storyboard_beats=context['slots'],
        storyboard=context['reference']['reference_storyboard'])
    changes = plan['plan_normalisations'][old_count:]
    rebuilt = [c for c in changes if c['reason'] == 'rebuild_slot_segment_ids_from_segment_slot_id']
    assert bool(rebuilt) == (attempt == 1)
    if attempt == 1:
        assert rebuilt == [dict(path='$.slots[2].segment_ids', before=['seg_2'], after=['seg_3'],
                                reason='rebuild_slot_segment_ids_from_segment_slot_id')]
    slots = {s['slot_id']: s for s in plan['slots']}
    assert slots['b4']['segment_ids'] == ['seg_3']
    assert slots['b2']['storyboard_cell_ids'] == ['s1', 's2']
    assert slots['b6']['storyboard_cell_ids'] == ['s4', 's5']
    carried = [c for c in changes if c['reason'] == 'core_cell_carried_by_slot']
    assert [c['model_reason'] for c in carried] == [m['reason'] for m in original['storyboard_merges'][:2]]
    assert plan['storyboard_merges'] == original['storyboard_merges'][2:]
    assert plan['segments'] == original['segments']
    assert plan['audio_draft'] == original['audio_draft']
    contracts.validate_plan_storyboard(plan, context['reference'], context['reference_duration_s'],
        context['watched_windows'], context['catalog'], storyboard_beats=context['storyboard_beats'],
        max_duration_s=None, max_segments=None)
    real_validator(context)(plan)  # No subsequent violation in either real reply.
    before = deepcopy(plan)
    real_validator(context)(plan)
    assert plan == before


def test_real_pipeline_fills_core_metadata_at_contract_boundary():
    context, plan = read('context'), read('attempt_2')
    # The production wrapper only passes slots (without core flags/times) to
    # normalise_plan; validate_plan has the complete reference storyboard.
    real_validator(context)(plan)
    assert plan['slots'][0]['storyboard_cell_ids'] == ['s1', 's2']
    assert plan['slots'][3]['storyboard_cell_ids'] == ['s4', 's5']


def test_contract_boundary_rejects_malformed_normalisation_history_as_value_error():
    plan, reference, windows, catalog, beats = fixture('none')
    plan['slots'][1]['segment_ids'] = ['seg0']
    plan['plan_normalisations'] = {}
    with pytest.raises(ValueError, match='plan:normalisations_array_required'):
        contracts.validate_plan(plan, catalog, windows, reference['reference_sha256'], 28,
                                reference=reference, storyboard_beats=beats)


@pytest.mark.parametrize('invalid', ['unknown_slot', 'missing_slot', 'empty_slot', 'unknown_segment'])
def test_membership_rebuild_leaves_ambiguous_or_invalid_mapping_to_validator(invalid):
    plan, reference, _, _, beats = fixture('none')
    plan['slots'][1]['segment_ids'] = ['seg0']
    if invalid == 'unknown_slot':
        plan['segments'][0]['slot_id'] = 'unknown'
    elif invalid == 'missing_slot':
        plan['segments'][0].pop('slot_id')
    elif invalid == 'empty_slot':
        plan['segments'][0]['slot_id'] = 'b2'
    else:
        plan['slots'][0]['segment_ids'] = ['unknown']
    original = deepcopy(plan['slots'])
    plan_normalisation.normalise_plan(plan, storyboard_beats=beats, storyboard=reference['reference_storyboard'])
    assert plan['slots'] == original
    assert not any(c['reason'] == 'rebuild_slot_segment_ids_from_segment_slot_id'
                   for c in plan.get('plan_normalisations', []))


def test_membership_rebuild_preserves_segments_order_and_ignores_order_only_difference():
    plan, reference, _, _, beats = fixture('none')
    plan['segments'][1]['slot_id'] = 'b1'
    del plan['slots'][1]
    plan['slots'][0]['segment_ids'] = ['seg1', 'seg0']
    original = deepcopy(plan)
    plan_normalisation.normalise_plan(plan, storyboard=reference['reference_storyboard'])
    assert plan == original  # Both memberships already agree; no reorder alone.
    plan['slots'][1]['segment_ids'] = ['seg0', 'seg2']
    plan_normalisation.normalise_plan(plan, storyboard_beats=beats)
    assert plan['slots'][0]['segment_ids'] == ['seg0', 'seg1']
    assert plan['slots'][1]['segment_ids'] == ['seg2']


@pytest.mark.parametrize('target', ['unknown', 'empty'])
def test_core_merge_requires_known_segment_bearing_target(target):
    data = fixture()
    plan, reference = data[:2]
    into = 'unknown' if target == 'unknown' else 'b2'
    if target == 'empty':
        plan['slots'][1]['segment_ids'] = []
        plan['segments'][1]['slot_id'] = 'b3'
        plan['slots'][2]['segment_ids'].insert(0, 'seg1')
    merge = dict(cell_id='s1', into_slot_id=into, reason='模型提供的原因')
    plan['storyboard_merges'] = [merge]
    plan_normalisation.normalise_plan(plan, storyboard=reference['reference_storyboard'])
    assert plan['storyboard_merges'] == [merge]
    with pytest.raises(ValueError, match='plan:storyboard_merge_invalid') as caught:
        check(*data)
    assert caught.value.diagnostics['issues'][0]['reason'] == 'core_cell_merged'


def test_already_carried_core_merge_is_removed_and_model_reason_recorded():
    data = fixture()
    plan, reference = data[:2]
    plan['storyboard_merges'] = [dict(cell_id='s1', into_slot_id='b1', reason='模型提供的原因')]
    plan_normalisation.normalise_plan(plan, storyboard=reference['reference_storyboard'])
    assert plan['storyboard_merges'] == []
    assert plan['plan_normalisations'][0]['reason'] == 'core_cell_carried_by_slot'
    assert plan['plan_normalisations'][0]['model_reason'] == '模型提供的原因'
    check(*data)


def test_noncore_merges_keep_the_same_thirty_percent_cap_after_core_carry():
    data = fixture()
    plan, reference = data[:2]
    plan['slots'][0]['storyboard_cell_ids'] = []
    plan['storyboard_merges'] = [dict(cell_id=f's{i}', into_slot_id='b2', reason='模型提供的原因')
                                for i in range(1, 5)]
    plan_normalisation.normalise_plan(plan, storyboard=reference['reference_storyboard'])
    assert [m['cell_id'] for m in plan['storyboard_merges']] == ['s2', 's3', 's4']
    check(*data)  # Seven cells allow three non-core merges, plus the carried core.
    plan['storyboard_merges'].append(dict(cell_id='s5', into_slot_id='b2', reason='模型提供的原因'))
    with pytest.raises(ValueError, match='plan:storyboard_merge_invalid') as caught:
        check(*data)
    assert caught.value.diagnostics['issues'][-1] == dict(reason='merge_cap_exceeded', merged_cells=4, maximum=3)


@pytest.mark.parametrize('observed', [False, True])
def test_core_feedback_and_prompt_share_actionable_hint(observed):
    data = fixture()
    plan, reference, _, _, beats = data
    plan['slots'][0]['storyboard_cell_ids'] = []
    if not observed:
        beats[0]['candidates'] = []
    before = deepcopy(plan)
    with pytest.raises(ValueError, match='plan:storyboard_core_cell_uncovered') as caught:
        check(*data)
    hint = contracts.storyboard_core_cell_repair_hint(has_observed_windows=observed)
    assert caught.value.violation['expected']['repair_hint'] == hint
    assert caught.value.diagnostics['repair_hint'] == hint
    assert caught.value.diagnostics['uncovered_cells'][0]['beats'][0]['observed_window_ids'] == (['w'] if observed else [])
    assert plan == before
    if not observed:
        assert all(word in hint for word in ['优先重释', '相邻', '含segment', 'storyboard_cell_ids', '画面加配音',
                                             '不要写入storyboard_merges'])
        context = dict(reference=reference, reference_duration_s=28)
        prompt = prompts.storyboard_plan_prompt(lambda c: json.dumps(c), context)
        assert hint in prompt
        assert '核心格不得合并' not in prompt
    else:
        assert hint == '为每个缺失核心格选择其节拍已观察窗口的真实片段，并在含segment的slot标注格ID；核心格不能合并。'


def test_mixed_core_feedback_identifies_which_cell_can_use_its_own_windows():
    data = fixture()
    plan, reference, _, _, beats = data
    reference['reference_storyboard']['storyboard'][1]['core'] = True
    plan['slots'][0]['storyboard_cell_ids'] = []
    plan['slots'][1]['storyboard_cell_ids'] = []
    beats[0]['candidates'] = []
    with pytest.raises(ValueError, match='plan:storyboard_core_cell_uncovered') as caught:
        check(*data)
    assert caught.value.diagnostics['repair_hint'] == (
        's1：' + contracts.storyboard_core_cell_repair_hint() + '\n' +
        's2：' + contracts.storyboard_core_cell_repair_hint(has_observed_windows=True))


def test_legacy_normalisation_and_prompt_leave_storyboard_references_untouched():
    plan, reference, _, _, _ = fixture('none')
    reference.pop('reference_storyboard')
    plan['audio_draft'] = []
    plan['slots'][1]['segment_ids'] = ['seg0']
    plan['storyboard_merges'] = [dict(cell_id='s1', into_slot_id='b2', reason='模型提供的原因')]
    original = deepcopy(plan)
    plan_normalisation.normalise_plan(plan)
    assert plan == original
    context = dict(reference=reference)
    template = lambda c: json.dumps(c, ensure_ascii=False)
    assert prompts.storyboard_plan_prompt(template, context) == template(context)
