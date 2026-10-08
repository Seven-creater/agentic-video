"""Distinct cropped-input planning contracts; synthetic media, no paid calls."""
from copy import deepcopy
import json

import pytest

from omni_story.library import slot_local_replan as local
from omni_story.library.state import json_sha
from test_library_slot_finecut_execute import prepared
from test_library_slot_finecut_assembly_binding import bound_fixture
from test_library_slot_source_feedback import feedback_fixture, planned


def flat(fixture):
    old, facts = planned(fixture)
    assembly = old['replacement_assembly']
    value = {'status': 'planned', 'baseline_id': old['baseline_id'], 'parent_sha256': old['parent_sha256'],
        'slot_id': fixture[2]['blocked_slot_id'], 'candidate': old['revised_proposals'][0]['candidates'][0],
        'segments': [assembly['plan']['segments'][-1]], 'timing_checks': [assembly['timing_checks'][-1]],
        'boundary_transition': [assembly['transition_checks'][-1]], 'limitations': [],
        'reason': 'Synthetic actual microcut, not a repeated whole-parent task.'}
    return value, facts


def validate(value, facts, fixture):
    _, _, policy, parent, catalog, reference, methods = fixture
    return local.validate_replan(value, policy, parent, catalog, parent['allowed_windows'], reference, methods, facts)


def test_flat_model_cut_splices_only_blocked_slot_and_preserves_inputs(feedback_fixture):
    value, facts = flat(feedback_fixture)
    policy, parent = feedback_fixture[2:4]
    original_digest = json_sha(policy)
    assert validate(value, facts, feedback_fixture) is value
    assembly = local.replacement_assembly(value, policy)
    assert assembly['plan']['segments'][:-1] == policy['original_assembly']['plan']['segments'][:-1]
    assert assembly['plan']['slots'][:-1] == policy['original_assembly']['plan']['slots'][:-1]
    assert assembly['plan']['slots'][-1]['intended_takeaway'] == policy['original_outline']['slots'][-1]['intended_takeaway']
    assert assembly['plan']['segments'][-1] == value['segments'][0]
    assert local.revised_full_proposals(value, policy)[:-1] == policy['original_proposals'][:-1]
    assert json_sha(policy) == original_digest
    slot = policy['original_outline']['slots'][-1]
    assert local.validate_input_scope({'kind': 'continuous_window', 'source_sha256': parent['sha256'],
        'source_start_s': slot['start_s'], 'source_end_s': slot['end_s']}, policy, parent)


@pytest.mark.parametrize('scope_change', ['whole_parent', 'movie_sha', 'epsilon_boundary', 'different_kind'])
def test_new_input_must_be_genuine_bound_last_slot_not_unknown_whole_parent(feedback_fixture, scope_change):
    policy, parent = feedback_fixture[2:4]
    slot = policy['original_outline']['slots'][-1]
    scope = {'kind': 'continuous_window', 'source_sha256': parent['sha256'],
             'source_start_s': slot['start_s'], 'source_end_s': slot['end_s']}
    if scope_change == 'whole_parent':
        scope['source_start_s'] = 0
    elif scope_change == 'movie_sha':
        scope['source_sha256'] = parent['provenance'][-1]['source_sha256']
    elif scope_change == 'epsilon_boundary':
        scope['source_start_s'] += .00001
    else:
        scope['kind'] = 'renamed_full_parent'
    with pytest.raises(ValueError, match='genuine_cropped_parent_scope'):
        local.validate_input_scope(scope, policy, parent)


@pytest.mark.parametrize('mutation,error', [
    ('full_range', 'proper_ordered_microcuts'), ('lower_min', 'minimum_changed'),
    ('information', 'minimum_changed'), ('unblocked_slot', 'only_blocked_slot_segments'),
    ('source', 'unknown_source|source_or_identity'), ('roles', 'confirmed_roles|source_or_identity'),
    ('unknown_whole_body', 'flat_output_fields'), ('missing_timing', 'essential|timing'),
    ('wrong_boundary', 'adjacent_transitions'), ('speed_mismatch', 'proposed_operation_changed'),
    ('out_of_parent', 'proper_ordered_microcuts'), ('unknown_fact_id', 'unknown_or_duplicate_ref')])
def test_flat_answer_cannot_relax_facts_minima_or_choose_other_footage(feedback_fixture, mutation, error):
    value, facts = flat(feedback_fixture)
    original = feedback_fixture[2]['original_operation']
    op = value['candidate']['operations'][0]
    if mutation == 'full_range':
        op.update(source_in_s=original['source_in_s'], source_out_s=original['source_out_s'])
        op['essential_intervals'][0].update(source_start_s=op['source_in_s'], source_end_s=op['source_out_s'])
    elif mutation == 'lower_min':
        op['essential_intervals'][0]['min_readable_s'] = .05
    elif mutation == 'information':
        op['essential_intervals'][0]['information'] = 'Altered creative obligation'
    elif mutation == 'unblocked_slot':
        value['segments'][0]['slot_id'] = 's1'
    elif mutation == 'source':
        value['segments'][0]['source_id'] = 'invented_asset'
    elif mutation == 'roles':
        value['segments'][0]['role_ids'] = ['invented_person']
    elif mutation == 'unknown_whole_body':
        value['replacement_assembly'] = {'plan': 'Pretend unknown original feedback reply'}
    elif mutation == 'missing_timing':
        value['timing_checks'][0]['essential_claims'] = []
    elif mutation == 'wrong_boundary':
        value['boundary_transition'][0]['from_segment_id'] = 'invented_previous'
    elif mutation == 'speed_mismatch':
        value['segments'][0]['speed'] = .5
    elif mutation == 'out_of_parent':
        op.update(source_in_s=original['source_in_s']-.1, source_out_s=original['source_out_s']-.1)
        op['essential_intervals'][0].update(source_start_s=op['source_in_s'], source_end_s=op['source_out_s'])
    elif mutation == 'unknown_fact_id':
        op['evidence_ids'] = ['made_up_fact']
    with pytest.raises(ValueError, match=error):
        validate(value, facts, feedback_fixture)


@pytest.mark.parametrize('removed_frames', [.0001, 1])
def test_epsilon_or_one_frame_movie_trim_is_not_an_independent_observation(feedback_fixture, removed_frames):
    value, facts = flat(feedback_fixture)
    old = feedback_fixture[2]['original_operation']
    fps = feedback_fixture[3]['render_input']['compiled']['fps']
    op = value['candidate']['operations'][0]
    op.update(source_in_s=old['source_in_s'], source_out_s=old['source_out_s']-removed_frames/fps)
    op['essential_intervals'][0].update(source_start_s=op['source_in_s'], source_end_s=op['source_out_s'])
    value['segments'][0].update(source_in_s=op['source_in_s'], source_out_s=op['source_out_s'])
    with pytest.raises(ValueError, match='epsilon_or_one_frame_trim_is_not_progress'):
        validate(value, facts, feedback_fixture)


def test_two_real_microcuts_have_model_owned_internal_transition(feedback_fixture):
    value, facts = flat(feedback_fixture)
    old = feedback_fixture[2]['original_operation']
    a, b = old['source_in_s'], old['source_out_s']
    op = value['candidate']['operations'][0]
    op.update(source_in_s=a, source_out_s=a+.3)
    op['essential_intervals'][0].update(source_start_s=a, source_end_s=a+.3)
    second = deepcopy(op)
    second.update(source_in_s=b-.3, source_out_s=b)
    second['essential_intervals'][0].update(source_start_s=b-.3, source_end_s=b)
    value['candidate']['operations'].append(second)
    first_segment = value['segments'][0]
    first_segment.update(source_in_s=a, source_out_s=a+.3)
    second_segment = deepcopy(first_segment)
    second_segment.update(segment_id='second_model_segment', source_in_s=b-.3, source_out_s=b)
    second_segment['visual_claims'][0]['claim_id'] = 'second_model_claim'
    value['segments'].append(second_segment)
    value['timing_checks'].append({'segment_id': 'second_model_segment', 'essential_claims': [
        {'essential_interval_index': 0, 'claim_ids': ['second_model_claim']}]})
    value['boundary_transition'].append({'from_segment_id': first_segment['segment_id'],
        'to_segment_id': 'second_model_segment', 'relation': 'Model-proposed disjoint moment relation.', 'status': 'planned'})
    assert validate(value, facts, feedback_fixture) is value
    value['boundary_transition'].pop()
    with pytest.raises(ValueError, match='adjacent_transitions'):
        validate(value, facts, feedback_fixture)


def test_original_five_second_minimum_stays_binding_even_for_a_shorter_cut(feedback_fixture):
    value, facts = flat(feedback_fixture)
    policy = feedback_fixture[2]
    policy['original_operation']['essential_intervals'][0]['min_readable_s'] = 5
    op = value['candidate']['operations'][0]
    op.update(speed=.5, freeze_tail_s=4)
    op['essential_intervals'][0].update(min_readable_s=5, continues_in_tail_frame=True)
    value['segments'][0].update(speed=.5, freeze_tail_s=4)
    assert validate(value, facts, feedback_fixture) is value
    op['essential_intervals'][0]['min_readable_s'] = 4.9
    with pytest.raises(ValueError, match='minimum_changed'):
        validate(value, facts, feedback_fixture)


def test_unresolved_label_cannot_waive_the_original_five_second_exposure(feedback_fixture):
    value, facts = flat(feedback_fixture)
    feedback_fixture[2]['original_operation']['essential_intervals'][0]['min_readable_s'] = 5
    value['candidate']['meaning_status'] = 'unresolved'
    value['candidate']['limitations'] = ['Synthetic visual meaning remains unresolved.']
    value['candidate']['operations'][0]['essential_intervals'][0]['min_readable_s'] = 5
    with pytest.raises(ValueError, match='declared_exposure_below_original_minimum'):
        validate(value, facts, feedback_fixture)


def test_unavailable_is_a_terminal_answer_with_no_creative_plan(feedback_fixture):
    value, facts = flat(feedback_fixture)
    value.update(status='unavailable', candidate=None, segments=[], timing_checks=[], boundary_transition=[],
                 limitations=['Synthetic source constraints cannot support a genuine microcut.'])
    assert validate(value, facts, feedback_fixture) is value
    value['limitations'] = []
    with pytest.raises(ValueError, match='unavailable_must_stop'):
        validate(value, facts, feedback_fixture)


def test_prompt_is_local_flat_and_contains_no_full_assembly_or_unknown_reply(feedback_fixture):
    _, _, policy, parent, catalog, *_ = feedback_fixture
    _, facts = flat(feedback_fixture)
    prompt = local.prompt(policy, parent, catalog, parent['allowed_windows'], facts)
    payload = json.loads(prompt[prompt.index('{"task":'):])
    assert set(payload['response_contract']) == local.FIELDS
    assert payload['slot'] == policy['original_outline']['slots'][-1]
    assert payload['original_information'] == policy['original_operation']['essential_intervals']
    assert 'replacement_assembly' not in payload and 'assembly_template' not in payload
    assert 'original_assembly' not in payload and 'unknown_reply' not in payload
    assert isinstance(payload['response_contract']['candidate'], dict)
    assert isinstance(payload['response_contract']['segments'], list)
    assert isinstance(payload['response_contract']['boundary_transition'], list)
    assert 'sf_3_source_feedback_replan_v1' not in prompt
    assert '不能降低最低曝光' in prompt
