"""Fine observation: every over-budget inspect range is reported in one round.

Server job prod_27_发现三个问题_11021100/evaluations/rule2/evaluations/len1/
evaluations/len2 (release 3b289bc) asked for 11 local inspections of a 139 s
parent, 8 of them longer than 6 s. Each repair named only the first one
([14,24], then [30,38]); the model fixed exactly that one, and the fine stage
stopped with model_protocol_repair_exhausted:chain_e2e_v1_fine_observe.
"""
import pytest

from omni_story.library import visual_story_trial as trial
from omni_story.library.protocol_feedback import diagnose

AUTH = dict(parent=dict(duration_s=139.0), reference=dict(duration_s=119.4),
            unbounded_inspections=True)
RANGES = [(0, 6), (14, 19), (19, 24), (30, 36), (45, 52), (52, 62), (62, 75), (79, 90),
          (103, 113), (113, 122), (131, 139)]


def rows(ranges):
    return [dict(target='parent', start_s=float(a), end_s=float(b), step_s=0.2, question='核对动作')
            for a, b in ranges]


def test_every_over_budget_range_is_listed_with_its_numbers():
    with pytest.raises(ValueError) as caught:
        trial.inspect_check(rows(RANGES), AUTH)
    error = caught.value
    assert str(error) == 'local_inspection_max6s_max36_requested_frames_shrink_range_or_raise_step'
    assert error.violation['path'] == '$.inspect[4]'
    items = diagnose('chain_e2e_v1_fine_observe', dict(story=[], facts=[], inspect=rows(RANGES)),
                     {'validation_error': str(error), 'violation': error.violation})
    paths = [item['path'] for item in items]
    assert paths[:7] == [f'$.inspect[{i}]' for i in (4, 5, 6, 7, 8, 9, 10)]
    assert 'duration=10s, frames=50' in items[1]['expected']


def test_single_offender_feedback_is_unchanged():
    with pytest.raises(ValueError) as caught:
        trial.inspect_check(rows([(0, 6), (14, 24)]), AUTH)
    assert caught.value.violation == dict(path='$.inspect[1]', value=[14.0, 24.0, 0.2],
                                          expected=trial.local_inspection_contract()['instruction'])


def test_first_failure_order_and_name_are_kept():
    bad = rows([(14, 24), (30, 38)])
    bad[1]['target'] = 'elsewhere'
    with pytest.raises(ValueError, match='local_inspection_max6s'):
        trial.inspect_check(bad, AUTH)
    earlier = rows([(0, 6), (14, 24)])
    earlier[0]['question'] = ''
    with pytest.raises(ValueError, match='inspect_target_question_required'):
        trial.inspect_check(earlier, AUTH)


def test_within_budget_passes():
    trial.inspect_check(rows([(0, 6), (14, 19), (131, 137)]), AUTH)


def test_a_later_malformed_row_cannot_replace_the_first_error():
    bad = rows([(14, 24), (0, 6)])
    bad[1]['target'] = []
    with pytest.raises(ValueError) as caught:
        trial.inspect_check(bad, AUTH)
    assert caught.value.violation['path'] == '$.inspect[0]'
    malformed_first = rows([(0, 6)])
    malformed_first[0]['target'] = []
    with pytest.raises(TypeError):
        trial.inspect_check(malformed_first, AUTH)
