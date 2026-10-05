from copy import deepcopy
import pytest

from omni_story.library.goal_source_ranges import range_table, plan_diagnostics, enforce_plan_ranges


def windows():
    return [{'window_id': 'w', 'source_id': 's', 'source_sha256': 'a' * 64,
        'source_start_s': 100, 'observation': {'usable_ranges': [
            {'local_in_s': 2, 'local_out_s': 10, 'role_ids': ['a', 'b'], 'event_indices': [0]},
            {'local_in_s': 11, 'local_out_s': 20, 'role_ids': ['a'], 'event_indices': [1]}]}}]


def plan(start=103, end=109, roles=None):
    return {'segments': [{'segment_id': 'seg', 'window_id': 'w', 'source_id': 's',
        'source_in_s': start, 'source_out_s': end, 'role_ids': roles or ['a', 'b']}]}


def test_all_original_ranges_offset_and_no_mutation():
    original = windows()
    saved = deepcopy(original)
    rows = range_table(original)
    assert [(r['source_in_s'], r['source_out_s']) for r in rows] == [(102, 110), (111, 120)]
    assert rows[1]['role_ids'] == ['a'] and rows[1]['event_indices'] == [1]
    rows[0]['role_ids'].append('not_evidence')
    assert original == saved


def test_range_gap_and_pooled_roles_rejected_without_suggested_cuts():
    result = plan_diagnostics(plan(109, 114), windows(), [])
    assert result[0]['cause'] == 'no_single_usable_range_contains_slice'
    assert len(result[0]['all_recorded_ranges_for_window']) == 2
    result = plan_diagnostics(plan(112, 114), windows(), [])
    assert result[0]['cause'] == 'roles_not_allowed_in_containing_range'
    assert not any('replacement' in k for k in result[0])
    assert plan_diagnostics(plan(), windows(), []) == []
    with pytest.raises(ValueError, match='mechanical_blockers'):
        enforce_plan_ranges(plan(109, 114), windows(), [])


def test_reports_all_blockers_and_exhausted_lineage_even_with_changed_id():
    exhausted = [{'scope': {'kind': 'continuous_window', 'source_sha256': 'a' * 64,
        'source_start_s': 103, 'source_end_s': 109}, 'original_call': 'old', 'repair_call': 'repair'}]
    value = plan()
    value['segments'][0]['segment_id'] = 'new_id'
    value['segments'].append(plan(109, 114)['segments'][0])
    diagnostics = plan_diagnostics(value, windows(), exhausted)
    assert len(diagnostics) == 2
    assert diagnostics[0]['original_call'] == 'old'
    assert diagnostics[1]['cause'] == 'no_single_usable_range_contains_slice'


def test_malformed_input_defers_to_original_structure_contract():
    assert plan_diagnostics(None, windows(), []) == []
    assert plan_diagnostics({'segments': [None, {'source_in_s': 'invalid'}]}, windows(), []) == []
