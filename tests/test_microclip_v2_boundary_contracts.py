from copy import deepcopy

import pytest

from omni_story.library.microclip_v2_boundary_contracts import (
    NAVIGATION_ACTION_EXAMPLES, NAVIGATION_FIELD_RULES, validate_navigation)


@pytest.fixture
def context():
    catalog = {f'f{i}': {'frame_id': f'f{i}', 'source_sha256': 'actual-source',
                         'source_time_s': i / 30, 'frame_end_s': (i + 1) / 30,
                         'decode_frame_index': i} for i in range(30)}
    slot = {'start_s': 0, 'end_s': 1}
    event = {'event_id': 'e0', 'obligation_ids': ['o0', 'o1']}
    intent = {'obligations': [{'obligation_id': 'o0'}, {'obligation_id': 'o1'}]}
    previous = {'anchor_frame_id': 'f5', 'status': 'blocked',
                'blocking_questions': ['The nominated existing cut is not visible.'],
                'cut_intent': 'existing_shot_boundary'}
    return catalog, slot, event, intent, previous


def navigation(action='observe', **extra):
    row = {'event_id': 'e0', 'anchor_frame_id': 'f5', 'obligation_ids': ['o0', 'o1'],
           'action': action, 'cut_intent': 'existing_shot_boundary',
           'question': 'Where does the visible shot actually change?', 'resolution': '',
           'source_start_s': .2, 'source_end_s': .6, 'confirmed_frame_id': None,
           'evidence_frame_ids': ['f5'], 'blocking_questions': ['Shot change not confirmed.'],
           'limitations': []}
    if action == 'confirm':
        row.update(source_start_s=None, source_end_s=None, confirmed_frame_id='f10',
                   evidence_frame_ids=['f10'], blocking_questions=[],
                   resolution='New frame f10 is the last frame of the observed state; both '
                              'original obligations remain supported by the cited frames.')
    elif action == 'blocked':
        row.update(source_start_s=None, source_end_s=None, evidence_frame_ids=[])
    return {**row, **extra}


def test_numeric_observation_request_needs_no_fabricated_endpoint_ids(context):
    value = navigation(source_start_s=.205, source_end_s=.595)
    assert validate_navigation(value, *context) is value


@pytest.mark.parametrize('extra,error', [
    ({'event_id': 'e1'}, 'boundary_event_unchanged'),
    ({'anchor_frame_id': 'f6'}, 'boundary_candidate_unchanged'),
    ({'obligation_ids': ['o0']}, 'boundary_all_original_event_obligations'),
    ({'obligation_ids': ['new']}, 'boundary_obligation_ids'),
    ({'source_end_s': .81}, 'boundary_observation_capacity'),
    ({'source_start_s': -.1}, 'boundary_observation_in_slot'),
    ({'source_end_s': 1.1}, 'boundary_observation_in_slot'),
    ({'source_start_s': True}, 'boundary_observation_start_s'),
    ({'source_end_s': float('nan')}, 'boundary_observation_end_s'),
    ({'start_frame_id': 'f5'}, 'boundary_observation_request_is_not_a_cut'),
    ({'question': ''}, 'boundary_specific_observation_question'),
    ({'confirmed_frame_id': 'f10'}, 'boundary_observe_not_confirmed'),
    ({'blocking_questions': []}, 'boundary_observe_needs_unresolved_question'),
    ({'blocking_questions': ['']}, 'boundary_specific_blocking_question'),
    ({'evidence_frame_ids': ['unknown']}, 'boundary_observed_evidence'),
])
def test_invalid_observation_contract(context, extra, error):
    with pytest.raises(ValueError, match=error):
        validate_navigation(navigation(**extra), *context)


def test_final_confirmation_needs_current_attachment_and_new_evidence(context):
    value = navigation('confirm')
    assert validate_navigation(value, *context, new_frame_ids=['f10'],
                               shown_frame_ids=['f9', 'f10', 'f11']) is value
    with pytest.raises(ValueError, match='boundary_confirmed_frame_in_current_attachment'):
        validate_navigation(value, *context, new_frame_ids=['f10'], shown_frame_ids=['f9'])


def test_semantic_trim_can_explicitly_resolve_hard_cut_block_without_new_frames(context):
    previous = deepcopy(context[-1])
    value = navigation('confirm', cut_intent='semantic_trim',
                       resolution='This is a new within-shot trim, not the former claimed '
                                  'source cut. f10 still shows the two retained states; '
                                  'the old cut-location contradiction remains recorded.')
    assert validate_navigation(value, *context) is value
    assert context[-1] == previous
    context[-1]['cut_intent'] = 'semantic_trim'
    with pytest.raises(ValueError, match='boundary_confirmation_needs_new_evidence'):
        validate_navigation(value, *context)


@pytest.mark.parametrize('extra,error', [
    ({'confirmed_frame_id': 'unknown'}, 'boundary_confirmed_observed_frame'),
    ({'evidence_frame_ids': ['f9']}, 'boundary_confirmation_cites_selected_frame'),
    ({'blocking_questions': ['Missing outcome.']}, 'boundary_confirm_cannot_keep_blockers'),
    ({'source_start_s': .2}, 'boundary_confirm_has_no_observation_envelope'),
    ({'resolution': ''}, 'boundary_explained_resolution'),
    ({'resolution': 'confirmed'}, 'boundary_resolution_not_action_label'),
])
def test_invalid_confirmation_contract(context, extra, error):
    with pytest.raises(ValueError, match=error):
        validate_navigation(navigation('confirm', **extra), *context, new_frame_ids=['f10'])


def test_unchanged_evidence_cannot_claim_progress(context):
    with pytest.raises(ValueError, match='boundary_confirmation_needs_new_evidence'):
        validate_navigation(navigation('confirm'), *context)


def test_blocked_is_a_valid_semantic_reply(context):
    value = navigation('blocked')
    assert validate_navigation(value, *context) is value
    with pytest.raises(ValueError, match='boundary_blocked_needs_question'):
        validate_navigation(navigation('blocked', blocking_questions=[]), *context)
    with pytest.raises(ValueError, match='boundary_blocked_has_no_executable_choice'):
        validate_navigation(navigation('blocked', confirmed_frame_id='f10'), *context)


def test_confirmation_evidence_cannot_cross_real_sources(context):
    context[0]['f10']['source_sha256'] = 'different-source'
    with pytest.raises(ValueError, match='boundary_evidence_same_source'):
        validate_navigation(navigation('confirm'), *context, new_frame_ids=['f10'])


def test_forward_examples_explicitly_separate_observation_and_confirmation_fields():
    assert set(NAVIGATION_ACTION_EXAMPLES) == {'observe', 'confirm', 'blocked'}
    observe = NAVIGATION_ACTION_EXAMPLES['observe']
    assert observe['action'] == 'observe'
    assert type(observe['source_start_s']) in (int, float)
    assert type(observe['source_end_s']) in (int, float)
    assert observe['source_start_s'] < observe['source_end_s']
    assert observe['confirmed_frame_id'] is None
    for action in ('confirm', 'blocked'):
        example = NAVIGATION_ACTION_EXAMPLES[action]
        assert example['action'] == action
        assert example['source_start_s'] is example['source_end_s'] is None
    assert NAVIGATION_ACTION_EXAMPLES['confirm']['blocking_questions'] == []
    assert NAVIGATION_ACTION_EXAMPLES['blocked']['blocking_questions']
    assert '数值0不是null' in NAVIGATION_FIELD_RULES
    assert '不要保留或复制上轮observe的范围' in NAVIGATION_FIELD_RULES
    assert 'cut_intent必须是existing_shot_boundary或semantic_trim' in NAVIGATION_FIELD_RULES


@pytest.mark.parametrize('alias', ['active_in_shot_cut', 'semantic_inshot', 'hard_cut', None])
def test_cut_intent_unknown_alias_is_rejected_with_exact_enum_diagnostic(context, alias):
    with pytest.raises(ValueError, match='boundary_cut_intent: allowed values are '
                                        'existing_shot_boundary or semantic_trim'):
        validate_navigation(navigation(cut_intent=alias), *context)


@pytest.mark.parametrize('action', ['confirm', 'blocked'])
@pytest.mark.parametrize('start,end', [(0, 0), (.2, .6)])
def test_non_observe_numeric_range_is_still_rejected_with_actionable_diagnostic(context,
                                                                               action, start, end):
    with pytest.raises(ValueError, match='JSON null or omitted.*numeric 0 is not null'):
        validate_navigation(navigation(action, source_start_s=start, source_end_s=end),
                            *context, new_frame_ids=['f10'])


@pytest.mark.parametrize('action', ['confirm', 'blocked'])
def test_non_observe_range_fields_can_be_omitted_without_changing_semantic_gates(context, action):
    value = navigation(action)
    del value['source_start_s']
    del value['source_end_s']
    assert validate_navigation(value, *context, new_frame_ids=['f10']) is value
    if action == 'confirm':
        value['blocking_questions'] = ['Still missing an essential outcome.']
        with pytest.raises(ValueError, match='boundary_confirm_cannot_keep_blockers'):
            validate_navigation(value, *context, new_frame_ids=['f10'])
