from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback


FIXTURES = Path(__file__).parent / 'fixtures/e2e_plan_role_events_20261030'
CALLS = ('glm_166_plan_0', 'glm_167_plan_0_repair', 'glm_168_plan_0_repair2')
ERROR = 'plan:segment_role_missing_overlapping_event_evidence'


def received(attempt=0):
    return json.loads((FIXTURES / (CALLS[attempt] + '_excerpt.json')).read_text())


def windows():
    fine = json.loads((FIXTURES / 'glm_165_fine.json').read_text())
    source_sha = fine['roles'][0]['entity_id'].split(':')[0]
    plan = received()
    result = []
    # The first two observations are controlled context, not recovered footage.
    # The last observation is the complete real reply; preserve its event gaps.
    for ident, start, end, events in (
            (plan['segments'][0]['window_id'], 212, 222, [(0, 10, ['A_asker'])]),
            (plan['segments'][2]['window_id'], 510, 526,
             [(0, 8, ['A_asker']), (8, 11, ['B_seated_listener']), (11, 16, ['A_asker'])]),
            (fine['window_id'], 1719.5, 1730.5, None)):
        window = dict(window_id=ident, source_id=fine['source_id'], source_sha256=source_sha,
                      source_start_s=start, source_end_s=end, status='watched')
        observation = deepcopy(fine)
        if events is not None:
            observation.update(window_id=ident,
                events=[dict(local_start_s=a, local_end_s=b, role_ids=roles,
                             observed_fact='controlled test context') for a, b, roles in events],
                usable_ranges=[dict(local_in_s=0, local_out_s=end-start,
                    event_indices=list(range(len(events))), role_ids=['A_asker'],
                    continuity_notes='controlled test context')])
        window['observation'] = pipeline._fine_navigation_points(observation, window)
        contracts.validate_fine(window['observation'], window)
        result.append(window)
    return result


def validate(plan, observed):
    window = observed[0]
    catalog = dict(sources=[dict(source_id=window['source_id'], sha256=window['source_sha256'],
        duration_s=1800, content_range=dict(content_start_s=30, content_end_s=1790))])
    return contracts.validate_plan(plan, catalog, observed, plan['reference_sha256'], allow_unfocused=True)


def gap_plan(local_in, local_out):
    original, window = received(), windows()[-1]
    segment = deepcopy(original['segments'][-1])
    segment.update(source_in_s=window['source_start_s'] + local_in,
                   source_out_s=window['source_start_s'] + local_out)
    plan = dict(reference_sha256=original['reference_sha256'], segments=[segment],
        slots=[dict(slot_id=segment['slot_id'], intended_takeaway='test event support',
                    segment_ids=[segment['segment_id']])],
        audio_mode='silent', width=720, height=1280, fps=30, limitations=[])
    return plan, window


@pytest.mark.parametrize('local_in,local_out', [(2, 3), (8, 9)])
def test_real_fine_event_gap_has_repairable_violation_and_shared_prompt_contract(local_in, local_out):
    plan, window = gap_plan(local_in, local_out)
    before = deepcopy((plan, window))
    with pytest.raises(ValueError, match='^' + ERROR + '$') as caught:
        validate(plan, [window])
    detail = caught.value.violation
    assert detail['path'] == '$.segments[0].role_ids'
    assert detail['value'] == ['A_asker']
    expected = detail['expected']
    assert expected['overlapping_role_ids'] == []
    assert expected['confirmed_role_ids'] == ['A_asker']
    assert expected['role_event_ranges']['A_asker'] == [
        [1719.5, 1721.5], [1722.5, 1727.5], [1728.5, 1730.5]]
    contract = contracts.plan_field_contract(windows=[window])['segment_roles']
    assert contract['windows'][0]['role_event_ranges'] == expected['role_event_ranges']
    assert detail['repair_hint'] == contract['repair_hint']
    assert 'role_ids=[]' in detail['repair_hint']
    assert '原片' in detail['repair_hint']
    assert (plan, window) == before
    # Both legal repairs were already legal: no weakening of role evidence.
    plan['segments'][0]['role_ids'] = []
    validate(plan, [window])
    plan['segments'][0]['role_ids'] = ['A_asker']
    plan['segments'][0]['source_in_s'] -= .01
    validate(plan, [window])


@pytest.mark.parametrize('attempt', [0, 1, 2])
def test_real_plan_fields_report_role_problem_even_when_time_error_is_first(attempt):
    plan, observed = received(attempt), windows()
    before = deepcopy((plan, observed))
    with pytest.raises(ValueError) as caught:
        validate(plan, observed)
    assert str(caught.value) == ('visual_library:range_outside_content' if attempt == 1 else ERROR)
    found = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', windows=observed,
        validation_error=caught.value, require_audio_draft=False))
    role = next(item for item in found if item['path'] == '$.segments[2].role_ids')
    assert role['value'] == json.dumps(plan['segments'][2]['role_ids'])
    assert role['expected']['confirmed_role_ids'] == ['A_asker']
    assert role['expected']['role_event_ranges']['A_asker'] == [[510, 518], [521, 526]]
    if attempt != 1:
        assert role['expected']['overlapping_role_ids'] == ['B_seated_listener']
    else:
        assert {f'$.segments[{i}].source_in_s/source_out_s' for i in range(5)} <= {
            item['path'] for item in found}
    assert not any(item['path'] == '$' for item in found)
    assert (plan, observed) == before


def test_diagnosis_collects_all_current_event_gaps_without_previous_round_memory():
    plan, window = gap_plan(2, 3)
    second = deepcopy(plan['segments'][0])
    second.update(segment_id='second', source_in_s=1727.5, source_out_s=1728.5)
    plan['segments'].append(second)
    plan['slots'][0]['segment_ids'].append('second')
    found = protocol_feedback.diagnose('plan_0', plan, dict(stage='plan', windows=[window],
        validation_error=ERROR, require_audio_draft=False))
    assert {row['path'] for row in found} == {'$.segments[0].role_ids', '$.segments[1].role_ids'}
    assert all(row['expected']['overlapping_role_ids'] == [] for row in found)


def test_existing_repair_budget_gets_specific_roles_and_never_resends(tmp_path, monkeypatch):
    from test_protocol_feedback import harness, feedback

    observed = windows()
    mcp, media, sent = harness(tmp_path, monkeypatch, [received(i) for i in range(3)],
                             provider='official_vision_mcp_in_opencode')

    def validator(value):
        validate(value, observed)

    validator.protocol_context = dict(stage='plan', windows=observed, require_audio_draft=False)
    for _ in range(2):
        with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_0$'):
            mcp.call('plan_0', 'prompt', media, validator, image=True)
        assert len(sent) == 3
    for request in sent[1:]:
        assert any(row['path'] == '$.segments[2].role_ids' for row in feedback(request)['violations'])
    assert not list((mcp.output / 'calls').glob('*/parsed.json'))
