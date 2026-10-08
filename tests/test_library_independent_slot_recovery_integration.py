"""Exercise production ledger/predecessor gates with synthetic frozen records.

The full recovery proof is tested separately. Here compatibility readers are
isolated at their validated-policy boundary; no real state or model is used.
"""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from omni_story.library import independent_slot_recovery as recovery
from omni_story.library import slot_finecut_budget as budget
from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_independent_slot_recovery import fixture, register, local_request
from test_library_slot_finecut_execute import prepared
from test_library_slot_finecut_assembly_binding import bound_fixture, invoke
from test_library_slot_source_feedback_integration import integrated, append_feedback_pair
from test_library_slot_source_feedback import planned


@pytest.fixture
def connected(fixture, monkeypatch):
    state, grant = fixture
    # Known predecessor files are supplied before registering the immutable
    # recovery proof, just as the actual 165 candidate already exists.
    write_json(state.output / 'calls' / state.data['calls'][145]['id'] / 'parsed.json', {'synthetic': 'known proposal'})
    policy = register(fixture)
    grant.update(baseline_request_count=131, unknown_inputs=policy['unknown_inputs'][:2])
    state.authorization = grant
    state.lock_path = state.output / '.synthetic.lock'
    state._reload = lambda: None
    state._save = lambda: write_json(state.output / 'library_state.json', state.data)
    state._progress = lambda *args: budget.SlotFinecutState._progress(state, *args)
    state._check_input = lambda *args: budget.SlotFinecutState._check_input(state, *args)
    state._received = lambda names, name: {'status': 'planned', 'synthetic_known_predecessor': name}
    def names(*args):
        result = {}
        for index, name in ((132, 'sf_0_outline'), (133, 'sf_3_outline'),
                            (145, 'sf_0_proposal_' + 'a' * 16), (144, 'sf_3_assemble')):
            result[name] = {**state.data['calls'][index], 'name': name}
        result[budget.SOURCE_FEEDBACK_STAGE] = state.data['calls'][165]
        for call in state.data['calls'][166:]:
            result[call['name']] = call
        return result
    monkeypatch.setattr(budget, '_validate_added', names)
    monkeypatch.setattr(budget, '_parallel_execution', lambda *args: {'failed_call_ids': []})
    monkeypatch.setattr(budget, '_parent_stops', lambda *args: {})
    monkeypatch.setattr(budget, '_independent_recovery', lambda *args: policy)
    monkeypatch.setattr(budget, '_request_bound_parent_facts', lambda *args: None)
    monkeypatch.setattr(budget, '_parent_point_navigation', lambda *args: None)
    monkeypatch.setattr(budget, '_request_bound_assembly', lambda *args: None)
    monkeypatch.setattr(budget, '_request_bound_proposal_retiming', lambda *args: None)
    monkeypatch.setattr(budget, '_source_feedback', lambda *args: {'stage': budget.SOURCE_FEEDBACK_STAGE})
    return state, grant, policy


def assemble_request(state):
    return {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video', 'media_sha256': 'e' * 64,
            'observation_scope': {'kind': 'continuous_window', 'source_sha256': 'd' * 64,
                                  'source_start_s': 0, 'source_end_s': 77.366667},
            'arguments': {'video_source': str(state.output / 'synthetic77.mp4'), 'prompt': 'Synthetic assembly'}}


def test_both_first_stages_can_submit_concurrently_without_replaying_frozen_166(connected):
    state, grant, policy = connected
    old = deepcopy(state.data['calls'])
    first, _ = budget.SlotFinecutState.begin_call(state, 'sf_0_assemble', assemble_request(state))
    second, _ = budget.SlotFinecutState.begin_call(state, recovery.LOCAL_STAGE, local_request(policy))
    assert first['status'] == second['status'] == 'submitted'
    assert state.data['request_count'] == 168
    assert state.data['calls'][:166] == old and state.data['max_requests'] == 80
    assert state.data['calls'][165]['status'] == 'uncertain'
    with pytest.raises(LibraryStopped, match='new_or_pending_outcome_unknown'):
        budget.SlotFinecutState.begin_call(state, recovery.LOCAL_STAGE + '_repair', local_request(policy), repair_of=second)


def test_new_unknown_blocks_all_future_submission_even_if_old_166_is_admitted(connected):
    state, _, policy = connected
    state.data['calls'].append({'id': 'new_lost_original', 'name': 'sf_0_assemble', 'status': 'uncertain',
                               'request_sha256': 'f' * 64, 'repair_of': None})
    state.data['request_count'] += 1
    with pytest.raises(LibraryStopped, match='new_or_pending_outcome_unknown'):
        budget.SlotFinecutState.begin_call(state, recovery.LOCAL_STAGE, local_request(policy))
    assert len(state.data['calls']) == 167


def test_old166_format_repair_is_not_an_independent_task(connected):
    state, _, policy = connected
    with pytest.raises(ValueError, match='stage_not_authorized'):
        budget.SlotFinecutState.begin_call(state, budget.SOURCE_FEEDBACK_STAGE + '_repair',
                                          local_request(policy), repair_of=state.data['calls'][165])
    assert len(state.data['calls']) == 166


def test_local_replan_must_pass_before_source_claims_or_blind_review(connected):
    state, _, _ = connected
    def unavailable(names, name):
        return {'status': 'unavailable'} if name == recovery.LOCAL_STAGE else {'synthetic': name}
    state._received = unavailable
    for name in ('semantic_slice_21_' + 'e' * 16, 'semantic_claims_21_' + 'e' * 16, 'sf_3_blind'):
        with pytest.raises(LibraryStopped, match='unavailable_local_replan_cannot_continue'):
            budget.SlotFinecutState._progress(state, name, {}, None)
    with pytest.raises(LibraryStopped, match='independent_local_replan_not_authorized'):
        state.authorization = {}
        # The absence of policy is tested at the reader boundary.
        from unittest.mock import patch
        with patch.object(budget, '_independent_recovery', return_value=None):
            budget.SlotFinecutState._progress(state, recovery.LOCAL_STAGE, {}, None)


def test_source_claims_follow_their_exact_slice_and_output_checks_keep_predecessors(connected):
    state, _, _ = connected
    names = {'sf_3_outline': {}, 'sf_3_assemble': {}, recovery.LOCAL_STAGE: {},
             'semantic_slice_21_' + 'e' * 16: {}}
    seen = []
    def known(names, name):
        seen.append(name)
        return {'status': 'planned'}
    state._received = known
    budget.SlotFinecutState._progress(state, 'semantic_claims_21_' + 'e' * 16, names, None)
    assert recovery.LOCAL_STAGE in seen and 'semantic_slice_21_' + 'e' * 16 in seen
    names['semantic_claims_21_' + 'e' * 16] = {}
    seen.clear()
    budget.SlotFinecutState._progress(state, 'sf_3_review', names, None)
    assert all(name in seen for name in ('sf_3_blind', 'sf_3_economy', 'semantic_claims_21_' + 'e' * 16))


def test_exhausted_exact_source_still_blocks_under_new_recovery(connected):
    state, grant, policy = connected
    source = {'kind': 'continuous_window', 'source_sha256': 'f' * 64, 'source_start_s': 311, 'source_end_s': 316}
    # Use early synthetic records; no new attempt is created for this exhausted
    # request. The actual same-scope blocker comes from the production function.
    for index in (68, 69):
        call = state.data['calls'][index]
        call.update(name='semantic_slice_3_' + 'a' * 16 + ('_repair' if index == 69 else ''),
                    repair_of=state.data['calls'][68]['id'] if index == 69 else None)
        request = {'observation_scope': source, 'media_sha256': 'c' * 64, 'arguments': {'prompt': 'Exhausted synthetic observation'}}
        call['request_sha256'] = json_sha(request)
        write_json(state.output / 'calls' / call['id'] / 'request.json', request)
    request = {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video', 'media_sha256': 'b' * 64,
               'observation_scope': source, 'arguments': {'prompt': 'Different encoding cannot replay exhausted observation'}}
    with pytest.raises(LibraryStopped, match='known_exhausted_slice_lineage_no_third_observation'):
        budget.SlotFinecutState._check_input(state, 'semantic_slice_21_' + 'b' * 16, request, grant)


@pytest.mark.parametrize('selected', ['local_reply', 'old_bound_assembly'])
def test_sole_render_claim_binds_local_reply_assembly_not_frozen166_or_oldassembly(integrated, monkeypatch, selected):
    from omni_story.library.slot_local_replan import replacement_assembly
    state, grant, policy, parent, catalog, reference, methods, bound = integrated
    value, _ = planned(integrated[:7])
    replacement = value['replacement_assembly']
    blocked = policy['blocked_slot_id']
    local_reply = {'status': 'planned', 'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
        'slot_id': blocked, 'candidate': value['revised_proposals'][0]['candidates'][0],
        'segments': [s for s in replacement['plan']['segments'] if s['slot_id'] == blocked],
        'timing_checks': [r for r in replacement['timing_checks'] if r['segment_id'] not in
                          {s['segment_id'] for s in replacement['plan']['segments'] if s['slot_id'] != blocked}],
        'boundary_transition': [r for r in replacement['transition_checks'] if
                                r['to_segment_id'] in {s['segment_id'] for s in replacement['plan']['segments'] if s['slot_id'] == blocked}],
        'limitations': [], 'reason': 'Synthetic distinct local plan.'}
    local_assembly = replacement_assembly(local_reply, policy)
    assembly = local_assembly if selected == 'local_reply' else bound['body']
    folder = Path(grant['execution_directory']) / 'render_3'
    write_json(folder / 'assembly.json', assembly)
    observations, checks = [], []
    sources = {s['source_id']: s for s in catalog['sources']}
    for segment in assembly['plan']['segments']:
        observations.append({'segment_id': segment['segment_id'], **{k: segment[k] for k in
            ('source_id', 'source_in_s', 'source_out_s')}, 'source_sha256': sources[segment['source_id']]['sha256']})
        checks.append({'segment_id': segment['segment_id'], 'claim_checks': []})
    write_json(folder / 'source_manifest.json', {'protocol': SEMANTIC_PROTOCOL, 'plan_sha256': json_sha(assembly['plan']),
        'observations': observations, 'segment_checks': checks, 'required_claims': [], 'timing_counterevidence': []})
    grant['authorization_path'] = str(Path(grant['execution_directory']) / 'authorization.json')
    lost = {'id': recovery.LOST_CALL, 'name': budget.SOURCE_FEEDBACK_STAGE, 'status': 'uncertain',
            'request_sha256': 'b' * 64, 'repair_of': None}
    state.data['calls'].append(lost)
    state.data['request_count'] = len(state.data['calls'])
    monkeypatch.setattr(budget, '_validate_added', lambda *args: {})
    monkeypatch.setattr(budget, '_independent_recovery', lambda *args: {
        'frozen_unknown_inputs': [{'call_id': lost['id'], 'request_sha256': lost['request_sha256']}]})
    def received(names, name):
        if name == recovery.LOCAL_STAGE:
            return local_reply
        if name == 'sf_3_assemble':
            return bound['body']
        if name == budget.SOURCE_FEEDBACK_STAGE:
            raise AssertionError('Frozen166 reply must never be consumed as known assembly')
        for observation, check in zip(observations, checks):
            key = json_sha({'segment': observation['segment_id'], 'sha': observation['source_sha256'],
                            'in': observation['source_in_s'], 'out': observation['source_out_s']})[:16]
            if name == 'semantic_slice_21_' + key:
                return observation
            if name == 'semantic_claims_21_' + key:
                return check
        raise AssertionError(name)
    state._received = received
    if selected == 'local_reply':
        directory = budget.SlotFinecutState.claim_render(state, 3)
        assert directory == folder / 'render' and not directory.exists()
    else:
        with pytest.raises(LibraryStopped, match='assembly_differs_from_model_reply'):
            budget.SlotFinecutState.claim_render(state, 3)


def test_independent_terminal_stop_cannot_be_lifted_in_python_or_node(integrated, monkeypatch):
    state, grant, policy, _, _, _, _, bound = integrated
    original_request = json.loads((state.output / 'calls/synthetic_assembly_0/request.json').read_text(encoding='utf-8'))
    lost = {'id': recovery.LOST_CALL, 'name': budget.SOURCE_FEEDBACK_STAGE, 'status': 'uncertain',
            'request_sha256': json_sha(original_request), 'repair_of': None}
    write_json(state.output / 'calls' / lost['id'] / 'request.json', original_request)
    state.data['calls'].append(lost)
    state.data['request_count'] = len(state.data['calls'])
    append_feedback_pair(integrated)
    for attempt, call in enumerate(state.data['calls'][-2:]):
        call['name'] = recovery.LOCAL_STAGE + ('_repair' if attempt else '')
    state.data['artifacts'][recovery.POLICY] = [{'path': 'Validated at separate proof boundary', 'sha256': 'd' * 64}]
    monkeypatch.setattr(budget, '_independent_recovery', lambda *args: {
        'frozen_unknown_inputs': [{'call_id': lost['id'], 'request_sha256': lost['request_sha256']}]})
    receipt = budget.SlotFinecutState.freeze_parent_protocol_failure(state, 3,
        'model_protocol_repair_exhausted:' + recovery.LOCAL_STAGE)
    assert state.data['artifacts'].get('sf_parent_protocol_stop_3_independent_resume')
    assert budget._parent_stops(state.output, state.data, grant)[3] == receipt
    state.data['calls'].append({'id': 'forbidden_after_terminal', 'name': recovery.LOCAL_STAGE,
                               'status': 'submitted', 'repair_of': None})
    with pytest.raises(LibraryStopped, match='stopped_parent_cannot_repeat'):
        budget._parent_stops(state.output, state.data, grant)
    checked = invoke(state, grant, function='parentProtocolStops', options={'boundAssembly': bound, 'sourceFeedback': policy})
    assert checked.returncode != 0 and 'stopped_parent_cannot_repeat' in checked.stderr
