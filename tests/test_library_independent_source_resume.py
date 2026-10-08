"""Independent inputs retain unknown outcomes; temporary synthetic records only."""
from copy import deepcopy
import json

import pytest

from omni_story.library import goal_budget, independent_source_resume as independent
from omni_story.library.state import json_sha, write_json
from test_library_preplanning import FakeState


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    state = FakeState(tmp_path)
    state.data['task_id'] = 'synthetic-task'
    state.data['input_lock'] = {'reference_sha256': 'b' * 64}
    state.set_artifact('goal_research_network_10', {'synthetic_client_stop': True})
    sources = [{'source_id': 'movie_a', 'sha256': 'a' * 64}]
    write_json(tmp_path / 'catalog/inventory.json', {'sources': sources})
    points = [(1300, 1307), (2287, 2289), (3495.5, 3503), (4716, 4721.5), (4454, 4459), (4832, 4842)]
    draft = {'segments': [{'segment_id': f'seg_{i}', 'source_id': 'movie_a',
                           'source_in_s': a, 'source_out_s': b} for i, (a, b) in enumerate(points, 1)]}
    calls = [{'id': f'glm_{i:03d}_fixture', 'name': 'fixture_' + str(i), 'status': 'received'}
             for i in range(1, 132)]
    for index, ident, name, sha, bounds in [
        (3, 'glm_004_coarse_978d5360_01', 'coarse_original', 'a' * 64, (600., 1200.)),
        (130, 'glm_131_active_10_draft', 'active_10_draft', 'b' * 64, (0., 21.933333))]:
        request = {'media_sha256': str(index) * 32, 'observation_scope': {
            'kind': 'continuous_window' if index == 130 else 'sparse_contact_sheet',
            'source_sha256': sha, 'source_start_s': bounds[0], 'source_end_s': bounds[1]}}
        calls[index] = {'id': ident, 'name': name, 'status': 'uncertain', 'request_sha256': json_sha(request)}
        write_json(tmp_path / 'calls' / ident / 'request.json', request)
    request = {'arguments': {'prompt': 'Synthetic original draft'}, 'media_sha256': 'c' * 64}
    response = {'result': {'content': [{'type': 'text', 'text': json.dumps(draft)}]}}
    call = {'id': independent.DRAFT_CALL, 'name': 'active_9_draft_repair', 'status': 'received',
            'request_sha256': json_sha(request), 'response_sha256': json_sha(response)}
    calls[127] = call
    for name, value in [('request', request), ('response', response), ('parsed', draft)]:
        write_json(tmp_path / 'calls' / call['id'] / (name + '.json'), value)
    state.data.update(calls=calls, request_count=131)
    monkeypatch.setattr(goal_budget, 'stage_state', lambda output: state)
    monkeypatch.setattr(goal_budget, 'get_authorization', lambda state: {})
    from omni_story.library import goal_feedback_continuation
    monkeypatch.setattr(goal_feedback_continuation, '_exhausted_slice_scopes', lambda state: [])
    return state


def activate(state):
    return independent.enable(state.output, 'Synthetic new independent input authorization.')


def test_strategy_keeps_originals_and_is_idempotent(recorded):
    old = deepcopy(recorded.data['calls'])
    record = activate(recorded)
    assert record['baseline_request_count'] == 131
    assert record['prefacts'][0]['segment']['source_in_s'] == 1300
    assert record['unknown_inputs'][0]['scope']['source_start_s'] == 600
    assert independent.load(recorded, 10) is None
    assert independent.load(recorded, 11) == record
    assert independent.load(recorded, 12) is None
    writes = recorded.writes
    assert activate(recorded) == record and recorded.writes == writes
    assert recorded.data['calls'] == old


def test_new_pending_and_unknown_remain_blockers(recorded):
    activate(recorded)
    independent.assert_no_unsettled(recorded, 11)
    for status in ('submitted', 'uncertain', 'failed_known'):
        recorded.data['calls'].append({'id': 'new_input', 'name': 'semantic_slice_11_new', 'status': status})
        with pytest.raises(ValueError, match='new_or_pending_outcome_unknown'):
            independent.assert_no_unsettled(recorded, 11)
        recorded.data['calls'].pop()


def test_reference_other_unknown_and_same_scope_cannot_be_replayed(recorded):
    record = activate(recorded)
    original = record['unknown_inputs'][0]
    good = {'media_sha256': 'e' * 64, 'observation_scope': {'kind': 'continuous_window',
            'source_sha256': 'a' * 64, 'source_start_s': 1300, 'source_end_s': 1307}}
    independent.check_request(record, good)
    for request in ({**good, 'media_sha256': original['media_sha256']},
                    {**good, 'observation_scope': original['scope']}):
        with pytest.raises(ValueError, match='unknown_input_replay_forbidden'):
            independent.check_request(record, request)
    request = deepcopy(good)
    request['observation_scope']['source_sha256'] = record['reference_sha256']
    with pytest.raises(ValueError, match='reference_carrier_forbidden'):
        independent.check_request(record, request)


def test_unknown_status_or_original_file_changes_invalidate_strategy(recorded):
    activate(recorded)
    recorded.data['calls'][-1]['status'] = 'received'
    with pytest.raises(ValueError, match='old_calls_changed'):
        independent.load(recorded, 11)
    recorded.data['calls'][-1]['status'] = 'uncertain'
    path = recorded.output / 'calls' / independent.DRAFT_CALL / 'parsed.json'
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='protected_call_file_changed'):
        independent.load(recorded, 11)


def test_prefact_permission_is_exact_and_only_one_repair_name(recorded):
    record = activate(recorded)
    stage = record['prefacts'][0]['stage']
    assert independent.permit_prefact(stage, record)
    assert independent.permit_prefact(stage + '_repair', record)
    assert not independent.permit_prefact(stage + '_repair_repair', record)
    assert not independent.permit_prefact('semantic_slice_12_new', record)


def test_activation_rejects_live_call_and_later_round_without_snapshot(recorded):
    recorded.data['calls'][20]['status'] = 'submitted'
    with pytest.raises(ValueError, match='wrong_or_pending_baseline'):
        activate(recorded)
    assert not (recorded.output / 'artifacts' / independent.POLICY).exists()
