"""Synthetic evidence tests; none assert real model/video editing quality."""
from copy import deepcopy

import pytest

from omni_story.library import microclip_v2_contracts as contract


SOURCE = {'source_id': 'synthetic', 'sha256': 'a' * 64}
SLOT = {'slot_id': 's1', 'start_s': 10., 'end_s': 12.,
        'intended_takeaway': 'An original model claim.',
        'entry_state': 'An original entry claim.', 'exit_state': 'An original exit claim.',
        'link_to_previous': '', 'link_to_next': 'An original next-link claim.'}
FRAMES = {f'f{i}': {'frame_id': f'f{i}', 'source_sha256': SOURCE['sha256'],
          'source_time_s': t, 'frame_end_s': end} for i, (t, end) in enumerate(
              [(10, 10.1), (10.1, 10.5), (10.5, 11), (11, 11.4), (11.4, 11.8), (11.8, 12)])}


def neutral(shown=None):
    shown = shown or list(FRAMES.values())
    return {'frames': [{'frame_id': f['frame_id'], 'visible_action_or_state': 'Visible geometry.',
                        'visible_text': ''} for f in shown],
            'visible_event': 'A shape changes.', 'missing_information': [], 'limitations': []}


def intent():
    return {'status': 'ready', 'blocking_questions': [], 'limitations': [],
            'original_claim_checks': [{'claim_id': key, 'original_text': SLOT[key],
                'verdict': 'supported', 'reason': 'Observed evidence.', 'evidence_frame_ids': ['f0']}
                for key in contract.CLAIM_KEYS if SLOT.get(key)],
            'obligations': [{'obligation_id': 'o0', 'description': 'Preserve visible state.',
                'support': 'visual', 'evidence_frame_ids': ['f0'],
                'original_claim_ids': ['intended_takeaway', 'entry_state']},
                {'obligation_id': 'o1', 'description': 'Preserve visible change.',
                 'support': 'mixed', 'evidence_frame_ids': ['f5'],
                 'original_claim_ids': ['exit_state', 'link_to_next']}],
            'search_regions': [{'region_id': 'r0', 'start_frame_id': 'f0', 'end_frame_id': 'f2',
                'question': 'Which state is visible?', 'obligation_ids': ['o0']},
                {'region_id': 'r1', 'start_frame_id': 'f3', 'end_frame_id': 'f5',
                 'question': 'Which change is visible?', 'obligation_ids': ['o1']}]}


def anchors():
    return {'status': 'ready', 'resolved_conflicts': [], 'blocking_questions': [], 'limitations': [],
            'events': [{'event_id': 'e0', 'obligation_ids': ['o0'], 'start_frame_id': 'f0',
                        'end_frame_id': 'f2', 'reason': 'An observed event.'},
                       {'event_id': 'e1', 'obligation_ids': ['o1'], 'start_frame_id': 'f3',
                        'end_frame_id': 'f5', 'reason': 'Another observed event.'}]}


def proposal():
    return {'shots': [{'start_frame_id': 'f0', 'end_frame_id': 'f2', 'speed': 1,
                      'hold_s': .4, 'reason': 'Preserve a state.', 'visible_change': 'A visible state.',
                      'obligation_ids': ['o0']},
                     {'start_frame_id': 'f3', 'end_frame_id': 'f5', 'speed': .5,
                      'hold_s': .6, 'reason': 'Preserve a change.', 'visible_change': 'A visible change.',
                      'obligation_ids': ['o1']}],
            'obligation_coverage': [{'obligation_id': 'o0', 'shot_indices': [0],
                'source_evidence_frame_ids': ['f0'], 'exposure_s': 1., 'necessary_exposure_s': .6,
                'reason': 'Screen-time allocation, not proof of human readability.'},
                {'obligation_id': 'o1', 'shot_indices': [1], 'source_evidence_frame_ids': ['f5'],
                 'exposure_s': 2.6, 'necessary_exposure_s': 1.5, 'reason': 'The cited tail can receive a hold.'}],
            'preserved_visible_meaning': 'An observed state changes.',
            'omitted_content': ['A redundant interval.'], 'limitations': []}


def check():
    return {'status': 'ready', 'blocking_questions': [], 'limitations': [],
            'obligation_checks': [{'obligation_id': ident, 'verdict': 'supported',
                                   'reason': 'Bound source evidence.'} for ident in ('o0', 'o1')]}


def review():
    return {'status': 'pass', 'key_moment_selection': 'pass', 'economy': 'pass', 'readability': 'pass',
            'reason': 'Actual synthetic output evidence.', 'limitations': [],
            'obligation_checks': [{'obligation_id': 'o0', 'verdict': 'pass', 'output_start_s': 0,
                                  'output_end_s': 1., 'reason': 'Observed output state.'},
                                 {'obligation_id': 'o1', 'verdict': 'pass', 'output_start_s': 1.4,
                                  'output_end_s': 4., 'reason': 'Observed output change.'}]}


def test_exact_neutral_frame_coverage_and_limited_blind_reading():
    value = neutral()
    assert contract.validate_frames(value, list(FRAMES.values())) is value
    value['observation_status'] = 'limited'
    value['limitations'] = ['Only some motion was observable.']
    assert contract.validate_blind_page(value, list(FRAMES.values())) is value
    value['frames'].pop()
    with pytest.raises(ValueError, match='all_shown_frames_once'):
        contract.validate_frames(value, list(FRAMES.values()))


def test_limited_motion_reply_is_not_a_format_failure():
    value = {'visible_meaning': 'Some geometry is visible.', 'events': [
        {'start_s': 0, 'end_s': 1, 'visible_content': 'A shape moves.', 'text_evidence': ''}],
        'observation_status': 'limited', 'limitations': ['Incomplete dynamic evidence.']}
    assert contract.validate_motion(value, 2) is value
    value['events'][0]['end_s'] = 3
    with pytest.raises(ValueError, match='local_event_time'):
        contract.validate_motion(value, 2)


def test_all_nonempty_original_claims_are_bound_and_unsupported_is_valid():
    value = intent()
    value['status'] = 'blocked'
    value['blocking_questions'] = ['Original interpretation remains unsupported.']
    value['original_claim_checks'][0].update(verdict='unsupported', evidence_frame_ids=[])
    assert contract.validate_intent(value, SLOT, FRAMES) is value
    assert {r['claim_id'] for r in value['original_claim_checks']} == {
        'intended_takeaway', 'entry_state', 'exit_state', 'link_to_next'}
    value['original_claim_checks'][0]['original_text'] = 'A rewritten answer.'
    with pytest.raises(ValueError, match='original_claim_text_unchanged'):
        contract.validate_intent(value, SLOT, FRAMES)


def test_original_claims_cannot_be_supported_then_dropped_from_editing_obligations():
    value = intent()
    value['obligations'][1]['original_claim_ids'].remove('exit_state')
    with pytest.raises(ValueError, match='all_original_claims_need_obligations'):
        contract.validate_intent(value, SLOT, FRAMES)
    value = intent()
    value['obligations'][0]['original_claim_ids'].append('fabricated_claim')
    with pytest.raises(ValueError, match='obligation_original_claim_ids'):
        contract.validate_intent(value, SLOT, FRAMES)


def test_ancillary_obligation_can_have_no_original_claim_without_dropping_a_claim():
    value = intent()
    value['obligations'].append({'obligation_id': 'o2', 'description': 'An additional visible transition.',
        'support': 'visual', 'evidence_frame_ids': ['f2'], 'original_claim_ids': []})
    value['search_regions'][0]['obligation_ids'].append('o2')
    assert contract.validate_intent(value, SLOT, FRAMES) is value


@pytest.mark.parametrize('error', ['claim_omitted', 'obligation_unmapped', 'unseen_frame', 'duplicate_region'])
def test_intent_cannot_drop_or_invent_evidence(error):
    value = intent()
    if error == 'claim_omitted': value['original_claim_checks'].pop()
    if error == 'obligation_unmapped': value['search_regions'][1]['obligation_ids'] = ['o0']
    if error == 'unseen_frame': value['obligations'][1]['evidence_frame_ids'] = ['unknown']
    if error == 'duplicate_region': value['search_regions'][1]['region_id'] = 'r0'
    with pytest.raises(ValueError): contract.validate_intent(value, SLOT, FRAMES)


def test_multiple_events_cover_obligations_and_blocked_status_is_valid():
    value = anchors()
    value.update(status='blocked', blocking_questions=['Boundary remains unclear.'])
    assert contract.validate_anchors(value, FRAMES, intent()) is value
    value['events'][1]['start_frame_id'] = 'f2'
    with pytest.raises(ValueError, match='ordered_nonoverlapping_events'):
        contract.validate_anchors(value, FRAMES, intent())


def test_region_and_event_array_order_matches_execution_stage_numbers():
    value = intent()
    value['search_regions'].reverse()
    with pytest.raises(ValueError, match='sequential_region_ids'):
        contract.validate_intent(value, SLOT, FRAMES)
    value = anchors()
    value['events'].reverse()
    with pytest.raises(ValueError, match='sequential_event_ids'):
        contract.validate_anchors(value, FRAMES, intent())


def test_anchor_must_be_visible_on_edge_page_and_blocked_boundary_is_valid():
    shown = [FRAMES['f0'], FRAMES['f1']]
    value = {**neutral(shown), 'anchor_frame_id': 'f0', 'confirmed_frame_id': 'f1',
             'status': 'confirmed', 'blocking_questions': [], 'reason': 'Actual adjacent frames.'}
    assert contract.validate_edge(value, shown, 'f0') is value
    value.update(status='blocked', confirmed_frame_id=None, blocking_questions=['Cannot confirm the edge.'])
    assert contract.validate_edge(value, shown, 'f0') is value
    value['anchor_frame_id'] = 'f2'
    with pytest.raises(ValueError, match='anchor_present'):
        contract.validate_edge(value, shown, 'f0')


def test_plan_compiles_real_pts_and_counts_hold_only_for_cited_tail():
    value = proposal()
    plan = contract.validate_plan(SOURCE, SLOT, value, FRAMES, set(FRAMES), intent())
    assert [(s['source_in_s'], s['source_out_s']) for s in plan['segments']] == [(10, 11), (11, 12)]
    assert plan['segments'][1]['freeze_tail_s'] == .6
    assert plan['segments'][0]['window_id'] == SLOT['slot_id']
    value['obligation_coverage'][0]['exposure_s'] = 1.4
    with pytest.raises(ValueError, match='exposure_matches_executable_allocation'):
        contract.validate_plan(SOURCE, SLOT, value, FRAMES, set(FRAMES), intent())


@pytest.mark.parametrize('error', ['outside_evidence', 'foreign_evidence', 'unknown_boundary', 'lost_obligation',
                                  'wrong_shot_assignment', 'insufficient_exposure', 'zero_motion'])
def test_plan_rejects_unbound_or_unallocated_information(error):
    value, frames, boundaries = proposal(), deepcopy(FRAMES), set(FRAMES)
    if error == 'outside_evidence': value['obligation_coverage'][0]['source_evidence_frame_ids'] = ['f5']
    if error == 'foreign_evidence':
        value['obligation_coverage'][0]['source_evidence_frame_ids'] = ['f1']
        frames['f1']['source_sha256'] = 'b' * 64
    if error == 'unknown_boundary': boundaries.remove('f2')
    if error == 'lost_obligation': value['obligation_coverage'].pop()
    if error == 'wrong_shot_assignment': value['obligation_coverage'][0]['shot_indices'] = [1]
    if error == 'insufficient_exposure': value['obligation_coverage'][0]['necessary_exposure_s'] = 2
    if error == 'zero_motion':
        frames['f0']['frame_end_s'] = 10 + 1 / 30
        value['shots'][0].update(end_frame_id='f0', speed=2)
    with pytest.raises(ValueError): contract.validate_plan(SOURCE, SLOT, value, frames, boundaries, intent())


def test_source_partial_is_a_semantic_result_not_protocol_error():
    value = check()
    value.update(status='blocked', blocking_questions=['The source does not show the required result.'])
    value['obligation_checks'][1]['verdict'] = 'unsupported'
    assert contract.validate_source_check(value, intent()) is value
    value['obligation_checks'][1]['obligation_id'] = 'o0'
    with pytest.raises(ValueError, match='all_source_obligations_once'):
        contract.validate_source_check(value, intent())


def test_failed_output_obligation_can_report_no_evidence_without_invented_time():
    value = review()
    value.update(status='partial', readability='fail')
    value['obligation_checks'][1].update(verdict='fail', output_start_s=None, output_end_s=None)
    assert contract.validate_review(value, intent(), 4) is value
    value['obligation_checks'][1]['verdict'] = 'pass'
    with pytest.raises(ValueError, match='missing_output_evidence_only_for_fail'):
        contract.validate_review(value, intent(), 4)


def test_twelve_shots_are_allowed_with_mechanical_obligation_binding():
    frames = {f'f{i}': {'frame_id': f'f{i}', 'source_sha256': SOURCE['sha256'],
              'source_time_s': i / 10, 'frame_end_s': (i + 1) / 10} for i in range(12)}
    slot = {'slot_id': 's0', 'start_s': 0, 'end_s': 1.2}
    task = {'obligations': [{'obligation_id': 'o0'}]}
    value = {'shots': [{'start_frame_id': ident, 'end_frame_id': ident, 'speed': 1, 'hold_s': 0,
              'reason': 'A source state.', 'visible_change': 'Visible geometry.', 'obligation_ids': ['o0']}
              for ident in frames], 'obligation_coverage': [{'obligation_id': 'o0',
              'shot_indices': list(range(12)), 'source_evidence_frame_ids': list(frames),
              'exposure_s': 1.2, 'necessary_exposure_s': 1, 'reason': 'Allocated output time.'}],
              'preserved_visible_meaning': 'A source change.', 'omitted_content': [], 'limitations': []}
    assert len(contract.validate_plan(SOURCE, slot, value, frames, set(frames), task)['segments']) == 12


@pytest.mark.parametrize('validator,args', [(contract.validate_motion, (2,)),
    (contract.validate_intent, (SLOT, FRAMES)), (contract.validate_anchors, (FRAMES, intent())),
    (contract.validate_review, (intent(), 4))])
def test_nonobject_replies_raise_protocol_value_error(validator, args):
    with pytest.raises(ValueError): validator(None, *args)
