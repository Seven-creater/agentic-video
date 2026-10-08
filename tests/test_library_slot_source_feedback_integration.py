"""Synthetic integration of the distinct feedback predecessor and terminal stop."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from omni_story.library import slot_finecut_budget as budget, slot_source_feedback as feedback
from omni_story.library.slot_finecut_assembly_binding import request_bound_assembly
from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_slot_finecut_execute import prepared
from test_library_slot_finecut_assembly_binding import bound_fixture, register, invoke
from test_library_slot_source_feedback import planned


@pytest.fixture
def integrated(bound_fixture):
    state, grant, _, parent, outline, proposals, catalog, reference, methods = bound_fixture
    operation = proposals[-1]['candidates'][0]['operations'][0]
    source = next(s for s in catalog['sources'] if s['source_id'] == parent['provenance'][-1]['source_id'])
    scope = {'kind': 'continuous_window', 'source_sha256': source['sha256'],
             'source_start_s': operation['source_in_s'], 'source_end_s': operation['source_out_s']}
    exhausted = []
    for attempt in (0, 1):
        ident = 'prior_exhausted_' + str(attempt)
        request = {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video', 'media_sha256': 'c' * 64,
                   'observation_scope': scope, 'arguments': {'prompt': 'Synthetic historical source observation.'}}
        response = {'result': {'content': [{'type': 'text', 'text': '{}'}]}}
        for filename, value in (('request.json', request), ('response.json', response),
                                ('protocol_failure.json', {'attempt': attempt, 'error': 'Synthetic historical failure'})):
            write_json(state.output / 'calls' / ident / filename, value)
        exhausted.append({'id': ident, 'name': 'semantic_slice_3_abcdefabcdefabcd' + ('_repair' if attempt else ''),
            'status': 'received', 'request_sha256': json_sha(request), 'response_sha256': json_sha(response),
            'repair_of': 'prior_exhausted_0' if attempt else None})
    # Historical source failures belong before the active grant prefix, just
    # like real 069/070, so they cannot masquerade as a forward slot stage.
    state.data['calls'] = state.data['calls'][:2] + exhausted + state.data['calls'][2:]
    state.data['request_count'] = len(state.data['calls'])
    grant['baseline_request_count'] = 4
    stop_key = 'sf_parent_protocol_stop_3_point_navigation_resume'
    stop = json.loads(Path(state.data['artifacts'][stop_key][0]['path']).read_text(encoding='utf-8'))
    stop['request_count'] = len(state.data['calls'])
    state.set_artifact(stop_key, stop)
    result_path = Path(grant['execution_directory']) / 'render_3/result_point_navigation_resume.json'
    result = json.loads(result_path.read_text(encoding='utf-8'))
    result['stop_receipt'] = stop
    write_json(result_path, result)
    state.set_artifact('sf_result_3_point_navigation_resume', {'result_path': str(result_path),
                                                          'result_sha256': budget.sha256_file(result_path)})
    write_json(state.output / 'library_state.json', state.data)
    assembly = register(bound_fixture)
    policy = feedback.record_source_feedback(state, 'Synthetic separate feedback authorization.')
    write_json(state.output / 'library_state.json', state.data)
    return state, grant, policy, parent, catalog, reference, methods, assembly


@pytest.mark.parametrize('first', ['feedback', 'old_source', 'assemble', 'blind'])
def test_only_bound_feedback_may_replace_old_next_stage(integrated, first):
    state, grant, policy, _, _, _, _, assembly = integrated
    name = {'feedback': feedback.STAGE, 'old_source': assembly['next_stage'],
            'assemble': 'sf_3_assemble', 'blind': 'sf_3_blind'}[first]
    state.data['calls'].append({'id': 'forward', 'name': name, 'status': 'submitted', 'repair_of': None})
    if first == 'feedback':
        assert request_bound_assembly(state.output, state.data, grant) == assembly
        assert budget._parent_stops(state.output, state.data, grant)[3]['repair_call_id'] == assembly['repair_call_id']
    else:
        with pytest.raises((ValueError, LibraryStopped), match='slot_finecut:|source_feedback:'):
            request_bound_assembly(state.output, state.data, grant)
    if shutil.which('node'):
        checked = invoke(state, grant)
        assert (checked.returncode == 0) == (first == 'feedback'), checked.stderr
        checked = invoke(state, grant, function='parentProtocolStops', options={
            'boundAssembly': assembly, 'sourceFeedback': policy})
        assert (checked.returncode == 0) == (first == 'feedback'), checked.stderr


def append_feedback_pair(integrated):
    state, _, _, *_ = integrated
    request = json.loads((state.output / 'calls/synthetic_assembly_0/request.json').read_text(encoding='utf-8'))
    for attempt in (0, 1):
        ident = 'feedback_failed_' + str(attempt)
        response = {'result': {'content': [{'type': 'text', 'text': '{}'}]}}
        request['arguments']['prompt'] = 'Synthetic feedback format attempt ' + str(attempt)
        for filename, value in (('request.json', request), ('response.json', response), ('protocol_failure.json', {'attempt': attempt})):
            write_json(state.output / 'calls' / ident / filename, value)
        state.data['calls'].append({'id': ident, 'name': feedback.STAGE + ('_repair' if attempt else ''),
            'status': 'received', 'request_sha256': json_sha(request), 'response_sha256': json_sha(response),
            'repair_of': 'feedback_failed_0' if attempt else None})
    state.data['request_count'] = len(state.data['calls'])


def test_feedback_terminal_stop_cannot_be_lifted_by_assembly_binding(integrated):
    state, grant, policy, _, _, _, _, assembly = integrated
    old_stop = Path(state.data['artifacts']['sf_parent_protocol_stop_3_point_navigation_resume'][0]['path'])
    old_bytes = old_stop.read_bytes()
    append_feedback_pair(integrated)
    receipt = budget.SlotFinecutState.freeze_parent_protocol_failure(state, 3,
        'model_protocol_repair_exhausted:' + feedback.STAGE)
    assert state.data['artifacts'].get('sf_parent_protocol_stop_3_source_feedback_resume')
    assert old_stop.read_bytes() == old_bytes
    assert budget._parent_stops(state.output, state.data, grant)[3] == receipt
    for name in (assembly['next_stage'], 'sf_3_blind'):
        state.data['calls'].append({'id': 'forbidden', 'name': name, 'status': 'submitted'})
        with pytest.raises(LibraryStopped, match='stopped_parent_cannot_repeat'):
            budget._parent_stops(state.output, state.data, grant)
        if shutil.which('node'):
            checked = invoke(state, grant, function='parentProtocolStops', options={
                'boundAssembly': assembly, 'sourceFeedback': policy})
            assert checked.returncode != 0 and 'stopped_parent_cannot_repeat' in checked.stderr
        state.data['calls'].pop()


def test_source_feedback_stage_requires_separate_authorization(integrated):
    state, grant, policy, *_ = integrated
    del state.data['artifacts'][feedback.POLICY]
    state._received = lambda names, name: {'synthetic_predecessor': name}
    with pytest.raises(LibraryStopped, match='source_feedback_not_authorized'):
        budget.SlotFinecutState._progress(state, feedback.STAGE, {}, None)
    with pytest.raises(LibraryStopped, match='unknown_stage'):
        budget._stage('sf_3_source_feedback_replan_v2')


@pytest.mark.parametrize('selected', ['replacement', 'old'])
def test_claim_render_binds_replacement_model_reply_not_original_assembly(integrated, monkeypatch, selected):
    state, grant, policy, parent, catalog, reference, methods, original = integrated
    value, facts = planned(integrated[:7])
    feedback.validate_replan(value, policy, parent, catalog, parent['allowed_windows'], reference, methods, facts)
    assembly = value['replacement_assembly'] if selected == 'replacement' else original['body']
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
    monkeypatch.setattr(budget, '_validate_added', lambda *args: {})
    def received(names, name):
        if name == feedback.STAGE:
            return value
        if name == 'sf_3_assemble':
            return original['body']
        for observation, checked in zip(observations, checks):
            key = json_sha({'segment': observation['segment_id'], 'sha': observation['source_sha256'],
                            'in': observation['source_in_s'], 'out': observation['source_out_s']})[:16]
            if name == 'semantic_slice_21_' + key:
                return observation
            if name == 'semantic_claims_21_' + key:
                return checked
        raise AssertionError(name)
    state._received = received
    if selected == 'replacement':
        directory = budget.SlotFinecutState.claim_render(state, 3)
        assert directory == folder / 'render'
        assert not directory.exists()  # Claim only; this test never renders.
    else:
        with pytest.raises(LibraryStopped, match='assembly_differs_from_model_reply'):
            budget.SlotFinecutState.claim_render(state, 3)
