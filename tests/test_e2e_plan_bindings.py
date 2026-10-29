"""Binding feedback regression using the complete fields in the redacted evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, pipeline, protocol_feedback


FIXTURES = Path(__file__).parent / 'fixtures' / 'e2e_plan_bindings'


def received(attempt):
    names = ['glm_172_plan_0', 'glm_173_plan_0_repair', 'glm_174_plan_0_repair2']
    return json.loads((FIXTURES / (names[attempt] + '.json')).read_text())['plan']


def observations():
    """The fine reply is real; the two absent observations are explicit test context.

    Their confirmed/entity states follow the recorded rejections, not invented pixels.
    The production ingestion helper assigns the fine reply's missing local entity IDs.
    """
    plan = received(1)
    owner = plan['cast'][0]['entity_id']
    sha = owner.split(':')[0]
    first, unconfirmed, last = [row['window_id'] for row in plan['focus_role_bindings']]
    fine = json.loads((FIXTURES / 'glm_171_fine.json').read_text())
    second_sha = fine['source_id'].removeprefix('src_') + '0' * 48
    windows = []
    for ident, start, end, source_id, digest, confirmed in (
            (first, 286.5, 296.5, 'src_' + sha[:16], sha, True),
            (unconfirmed, 300.5, 310.5, 'src_' + sha[:16], sha, False),
            (last, 949.5, 957.5, fine['source_id'], second_sha, True)):
        window = dict(window_id=ident, source_id=source_id, source_sha256=digest,
                      source_start_s=start, source_end_s=end, status='watched')
        if ident == last:
            observation = pipeline._fine_navigation_points(fine, window)
            # Same local-entity assignment as shared-library ingestion. The full SHA
            # is absent from the redacted evidence; its suffix is synthetic here.
            for role in observation['roles']:
                role['entity_id'] = digest + ':' + ident + '_' + role['role_id']
        else:
            observation = dict(window_id=ident, source_id=source_id,
                roles=[dict(role_id='A_shopkeeper', identity_confirmed=confirmed,
                            entity_id=owner if confirmed else digest + ':local_unconfirmed',
                            state='test context', identity_evidence='test context')],
                events=[], usable_ranges=[], uncertainties=[])
        window['observation'] = observation
        contracts.validate_fine(observation, window)
        windows.append(window)
    return windows


def rejected(plan, windows):
    with pytest.raises(ValueError) as caught:
        validate(plan, windows)
    return caught.value


def validate(plan, windows):
    catalog = dict(sources=[dict(source_id=w['source_id'], sha256=w['source_sha256'], duration_s=1200)
                            for w in {w['source_id']: w for w in windows}.values()])
    contracts.validate_plan(plan, catalog, windows, plan['reference_sha256'])


@pytest.mark.parametrize('attempt', [0, 1])
def test_first_cast_rejection_also_reports_unconfirmed_focus(attempt):
    plan, windows = received(attempt), observations()
    before = deepcopy(plan)
    error = rejected(plan, windows)
    assert str(error) == 'plan:cast_entity_not_supported_by_observation'
    found = protocol_feedback.diagnose('plan_0', plan,
        dict(windows=windows, validation_error=error, require_audio_draft=False))
    paths = {row['path'] for row in found}
    assert {'$.cast[0].bindings[1]', '$.focus_role_bindings[1]'} <= paths
    focus = next(row for row in found if row['path'] == '$.focus_role_bindings[1]')
    assert focus['expected']['allowed_bindings'] == []
    assert focus['hint'] == contracts.plan_field_contract()['focus_role_bindings']['repair_hint']
    assert plan == before


def test_last_reply_reports_focus_and_all_source_time_errors_together():
    plan, windows = received(2), observations()
    before = deepcopy(plan)
    error = rejected(plan, windows)
    assert str(error) == 'plan/focus_binding_confirmed_role:unknown_or_duplicate_ref'
    found = protocol_feedback.diagnose('plan_0', plan,
        dict(windows=windows, validation_error=error, require_audio_draft=False))
    paths = {row['path'] for row in found}
    assert '$.focus_role_bindings[1]' in paths
    assert {f'$.segments[{i}].source_in_s/source_out_s' for i in range(5)} <= paths
    assert plan == before  # Never prune identities or convert editorial source times.


def test_focus_rejection_has_repairable_violation_and_observed_options():
    plan, windows = received(2), observations()
    error = rejected(plan, windows)
    detail = error.violation
    assert detail['path'] == '$.focus_role_bindings[1]'
    assert detail['value'] == plan['focus_role_bindings'][1]
    assert detail['expected']['allowed_bindings'] == []
    assert detail['repair_hint']
    # An unfounded focus does not become usable footage after removing the bad row.
    plan['focus_role_bindings'] = [plan['focus_role_bindings'][1]]
    assert rejected(plan, windows).violation['expected']['allowed_bindings'] == []


def test_contract_lists_exact_entities_and_explains_source_local_cast_split():
    plan, windows = received(1), observations()
    contract = contracts.plan_field_contract(windows=windows)
    options = contract['cast']['allowed_bindings']
    assert len(options) == 3  # One confirmed role in the first window, two in the real fine reply.
    assert not any(row['window_id'] == windows[1]['window_id'] for row in options)
    local = next(row for row in options if row['window_id'] == windows[2]['window_id']
                 and row['role_id'] == 'A_shopkeeper')
    assert local['entity_id'] == windows[2]['observation']['roles'][0]['entity_id']
    assert local['entity_id'] != plan['cast'][0]['entity_id']
    assert '来源' in contract['cast']['repair_hint'] and '拆' in contract['cast']['repair_hint']
    with pytest.raises(ValueError) as caught:
        contracts.validate_task_cast(plan['cast'], windows)
    assert caught.value.violation['expected']['allowed_bindings'] == [
        row for row in options if row['window_id'] == windows[2]['window_id']]
    # Separate observed entities with the same voice already are a legal expression.
    character = plan['cast'][0]
    binding = character['bindings'].pop()
    plan['cast'].append(dict(cast_id='other_source', entity_id=local['entity_id'],
                             voice=character['voice'], bindings=[binding]))
    assert len(contracts.validate_task_cast(plan['cast'], windows)) == 2


def test_feedback_reports_each_independent_cast_binding_in_one_pass():
    plan, windows = received(1), observations()
    plan['cast'][0]['bindings'].append(dict(window_id=windows[1]['window_id'], role_id='A_shopkeeper'))
    found = protocol_feedback.diagnose('plan_0', plan,
        dict(windows=windows, validation_error=rejected(plan, windows), require_audio_draft=False))
    paths = {row['path'] for row in found}
    assert {'$.cast[0].bindings[1]', '$.cast[0].bindings[2]', '$.focus_role_bindings[1]'} <= paths


def test_real_fields_get_complete_first_feedback_without_more_attempts_or_resends(tmp_path, monkeypatch):
    from test_protocol_feedback import harness, feedback

    windows = observations()
    mcp, media, sent = harness(tmp_path, monkeypatch, [received(i) for i in range(3)],
                             provider='official_vision_mcp_in_opencode')

    def validator(plan):
        validate(plan, windows)

    validator.protocol_context = dict(stage='plan', windows=windows, require_audio_draft=False)
    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_0$'):
        mcp.call('plan_0', 'prompt', media, validator)
    paths = {row['path'] for row in feedback(sent[1])['violations']}
    assert {'$.cast[0].bindings[1]', '$.focus_role_bindings[1]'} <= paths
    assert len(sent) == 3  # The invalid last reply remains rejected at the original budget.
    with pytest.raises(ValueError, match='^model_protocol_repair_exhausted:plan_0$'):
        mcp.call('plan_0', 'prompt', media, validator)
    assert len(sent) == 3
    assert all(row['status'] == 'received' for row in mcp.state.data['calls'])
    assert not list((mcp.output / 'calls').glob('*/parsed.json'))
