"""Routing avoids irrelevant scans while matching bindings retain full checks."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import slot_finecut_budget as budget, goal_budget
from omni_story.library.state import json_sha, write_json


CASES = [
    ('derived_fact', budget.REQUEST_BOUND_FACT_POLICY, 'sf_0_facts_' + 'a' * 16, '_request_bound_parent_facts'),
    ('derived_assembly', budget.REQUEST_BOUND_ASSEMBLY_POLICY, 'sf_3_assemble', '_request_bound_assembly'),
    ('derived_proposal_retiming', budget.REQUEST_BOUND_PROPOSAL_RETIMING_POLICY,
     'sf_0_proposal_' + 'a' * 16, '_request_bound_proposal_retiming'),
    ('derived_parent_navigation', budget.POINT_NAVIGATION_POLICY + '_3', budget.POINT_NAVIGATION_STAGES[3], '_parent_point_navigation'),
]


def state_with_policy(tmp_path, artifact, stage):
    p = tmp_path / (artifact + '.json')
    policy = {'stage': stage}
    write_json(p, policy)
    count = []
    state = SimpleNamespace(output=tmp_path, data={'artifacts': {artifact: [{'path': str(p), 'sha256': json_sha(policy)}]}},
                            authorization={})
    state.assert_protected = lambda: count.append('full') or {}
    return state, count


@pytest.mark.parametrize('method,artifact,stage,helper', CASES)
def test_stage_mismatch_skips_full_authorization(tmp_path, method, artifact, stage, helper):
    state, count = state_with_policy(tmp_path, artifact, stage)
    assert getattr(budget.SlotFinecutState, method)(state, 'sf_3_blind') is None
    assert count == []
    if method == 'derived_fact':
        assert getattr(budget.SlotFinecutState, method)(state, 'sf_0_facts_' + 'b' * 16) is None
    if method == 'derived_proposal_retiming':
        assert getattr(budget.SlotFinecutState, method)(state, 'sf_0_proposal_' + 'b' * 16) is None
    assert count == []


@pytest.mark.parametrize('method,artifact,stage,helper', CASES)
def test_matching_stage_still_checks_full_authorization_and_binding(tmp_path, monkeypatch, method, artifact, stage, helper):
    state, count = state_with_policy(tmp_path, artifact, stage)
    body = {'synthetic': 'unchanged'}
    policy = {'stage': stage, 'body': body, 'envelope': body, 'media_sha256': 'c' * 64}
    checked = []
    monkeypatch.setattr(budget, helper, lambda *args: checked.append('binding') or policy)
    assert getattr(budget.SlotFinecutState, method)(state, stage, media_sha256='c' * 64) == body
    assert count == ['full'] and checked == ['binding']


def test_received_plain_stage_does_not_recheck_other_bindings(tmp_path, monkeypatch):
    state = SimpleNamespace(output=tmp_path, data={'artifacts': {}}, authorization={})
    def unexpected(*args):
        raise AssertionError('irrelevant compatibility scan')
    for name in ('_request_bound_parent_facts', '_request_bound_assembly', '_request_bound_proposal_retiming', '_parent_point_navigation'):
        monkeypatch.setattr(budget, name, unexpected)
    call = {'id': 'cached_outline', 'name': 'sf_3_outline', 'status': 'received'}
    write_json(tmp_path / 'calls/cached_outline/parsed.json', {'synthetic': True})
    monkeypatch.setattr(goal_budget, '_call_value', lambda *args: (None, {'synthetic': True}))
    assert budget.SlotFinecutState._received(state, {'sf_3_outline': call}, 'sf_3_outline') == {'synthetic': True}


def test_stop_receipts_share_one_fully_checked_binding_set(tmp_path, monkeypatch):
    data = {'calls': [], 'artifacts': {}}
    record = {'baseline_request_count': 0, 'preparation_id': 'synthetic'}
    first_stage = budget.POINT_NAVIGATION_STAGES[3]
    stages = [first_stage, 'sf_3_assemble']
    for pair, stage in enumerate(stages):
        files = []
        for attempt in (0, 1):
            ident = f'known_{pair}_{attempt}'
            request, response = {'synthetic_pair': pair, 'attempt': attempt}, {'synthetic_reply': attempt}
            data['calls'].append({'id': ident, 'name': stage + ('_repair' if attempt else ''), 'status': 'received',
                'repair_of': f'known_{pair}_0' if attempt else None, 'request_sha256': json_sha(request), 'response_sha256': json_sha(response)})
            for filename, value in (('request.json', request), ('response.json', response), ('protocol_failure.json', {'attempt': attempt})):
                p = tmp_path / 'calls' / ident / filename
                write_json(p, value)
                files.append({'path': str(p), 'sha256': budget.sha256_file(p)})
        receipt = {'policy': budget.PARENT_STOP_POLICY, 'preparation_id': 'synthetic', 'parent_round': 3,
            'request_count': len(data['calls']), 'independent_parent_round': 0, 'original_call_id': f'known_{pair}_0',
            'repair_call_id': f'known_{pair}_1', 'error': 'model_protocol_repair_exhausted:' + stage, 'files': files}
        name = 'sf_parent_protocol_stop_3' + ('_point_navigation_resume' if pair else '')
        p = tmp_path / (name + '.json')
        write_json(p, receipt)
        data['artifacts'][name] = [{'path': str(p), 'sha256': json_sha(receipt)}]
    counts = {}
    def checked(name, value=None):
        def call(*args):
            counts[name] = counts.get(name, 0) + 1
            return value
        return call
    for name in ('_parallel_execution', '_request_bound_parent_facts', '_request_bound_assembly', '_request_bound_proposal_retiming', '_source_feedback'):
        monkeypatch.setattr(budget, name, checked(name))
    navigation = {'parent_round': 3, 'repair_call_id': 'known_0_1', 'next_stage': 'sf_3_assemble'}
    monkeypatch.setattr(budget, '_parent_point_navigation', checked('_parent_point_navigation', navigation))
    assert budget._parent_stops(tmp_path, data, record)[3]['repair_call_id'] == 'known_1_1'
    assert set(counts.values()) == {1}
