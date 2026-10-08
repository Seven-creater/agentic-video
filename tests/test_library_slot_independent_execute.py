"""Synthetic independent-recovery branch, with real validators and FFmpeg.

Only the policy-file admission boundary is mocked. No model, actual movie run,
or provider state is touched; these tests do not establish GLM editing quality.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import slot_finecut as sf, slot_local_replan as local, slot_source_feedback as feedback
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import json_sha, write_json
from test_library_slot_finecut_execute import prepared, FakeGLM, FakeState
from test_library_slot_finecut_assembly_binding import bound_fixture
from test_library_slot_source_feedback import feedback_fixture
from test_library_slot_local_replan import flat


class IndependentState(FakeState):
    def __init__(self, output, data, grant, protected, folder, policy):
        super().__init__(output, protected)
        self.data, self.authorization, self.folder, self.policy = data, grant, folder, policy
        self.source_plans = []

    def derived_assembly(self, stage, *, media_sha256):
        if stage == 'sf_3_assemble':
            return deepcopy(self.policy['original_assembly'])
        return None

    def assert_source_inputs(self, plan, catalog):
        super().assert_source_inputs(plan, catalog)
        blocked = self.policy['original_operation']
        assert all((s['source_in_s'], s['source_out_s']) !=
                   (blocked['source_in_s'], blocked['source_out_s']) for s in plan['segments'])
        self.source_plans.append(deepcopy(plan))

    def claim_render(self, round_no):
        super().claim_render(round_no)
        return self.folder / 'render'


class IndependentGLM(FakeGLM):
    """Known cache bodies stay immutable; only distinct forward work is counted."""
    def __init__(self, state, parent, policy, local_value, facts, *, unsupported=False):
        super().__init__(state, parent, unsupported=unsupported)
        self.policy, self.local_value = policy, local_value
        self.attempted = []
        self.cached = {'sf_3_outline': policy['original_outline']}
        for slot, proposal in zip(policy['original_outline']['slots'], policy['original_proposals'], strict=True):
            key = json_sha({'parent': parent['sha256'], 'slot': slot})[:16]
            report = deepcopy(facts)
            report.update(slot_id=slot['slot_id'], slot_start_s=slot['start_s'], slot_end_s=slot['end_s'])
            report['evidence'][0]['end_s'] = slot['end_s'] - slot['start_s']
            self.cached['sf_3_facts_' + key] = report
            self.cached['sf_3_proposal_' + key] = proposal

    def call(self, name, prompt, media, validator, **kwargs):
        self.attempted.append(name)
        assert name != feedback.STAGE and name != feedback.STAGE + '_repair', 'Unknown whole-parent call replayed'
        if name in self.cached:
            result = deepcopy(self.cached[name])
            validator(result)
            return result
        return super().call(name, prompt, media, validator, **kwargs)

    def answer(self, name, prompt, media):
        if name == local.STAGE:
            slot = self.policy['original_outline']['slots'][-1]
            lineage = json.loads((Path(media).parent / 'lineage.json').read_text(encoding='utf-8'))
            scope = {k: lineage[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
            assert scope == {'kind': 'continuous_window', 'source_sha256': self.parent['sha256'],
                             'source_start_s': slot['start_s'], 'source_end_s': slot['end_s']}
            assert scope != self.policy['observation_scope']
            assert probe_media(media)['duration_s'] == pytest.approx(slot['end_s']-slot['start_s'], abs=.04)
            payload = json.loads(prompt[prompt.index('{"task":'):])
            assert payload['slot'] == slot and 'assembly_template' not in payload
            assert isinstance(payload['response_contract']['candidate'], dict)
            if self.local_value['status'] == 'planned':
                self.plan = local.replacement_assembly(self.local_value, self.policy)['plan']
            return deepcopy(self.local_value)
        return super().answer(name, prompt, media)


def setup(feedback_fixture, monkeypatch, *, unavailable=False, unsupported=False):
    old_state, grant, policy, parent, catalog, reference, methods = feedback_fixture
    value, facts = flat(feedback_fixture)
    if unavailable:
        value.update(status='unavailable', candidate=None, segments=[], timing_checks=[], boundary_transition=[],
                     limitations=['Synthetic unavailable source microcut; stop without another round.'])
    # A real unknown request remains frozen in this synthetic ledger. The
    # admission-policy validator is separately covered by transport/budget tests.
    request = {'media_sha256': policy['media_sha256'], 'observation_scope': policy['observation_scope'],
               'arguments': {'prompt': 'Frozen synthetic whole-parent request with no reply'}}
    unknown_path = old_state.output / 'calls/unknown_feedback166/request.json'
    write_json(unknown_path, request)
    old_state.data['calls'].append({'id': 'unknown_feedback166', 'name': feedback.STAGE,
        'status': 'uncertain', 'request_sha256': json_sha(request)})
    recovery = old_state.output / 'synthetic_independent_policy.json'
    write_json(recovery, {'fixture': 'Separately authorized independent inputs, no replay'})
    old_state.data['artifacts']['sf_independent_slot_recovery_v1'] = [{'path': str(recovery), 'sha256': json_sha(
        {'fixture': 'Separately authorized independent inputs, no replay'})}]
    folder = Path(grant['execution_directory']) / 'render_3'
    protected = {Path(row['path']): Path(row['path']).read_bytes() for row in policy['protected_files']}
    protected[unknown_path] = unknown_path.read_bytes()
    state = IndependentState(old_state.output, old_state.data, grant, protected, folder, policy)
    # Mock only hash-bound policy admission, retaining all real creative/output
    # validators, local media preparation, exact-source ordering and rendering.
    monkeypatch.setattr(feedback, 'read_validate', lambda output, data, authorization: deepcopy(policy))
    glm = IndependentGLM(state, parent, policy, value, facts, unsupported=unsupported)
    preparation = json.loads(Path(grant['preparation_path']).read_text(encoding='utf-8'))
    return state, glm, preparation, parent, folder, methods, policy, value, unknown_path


def test_independent_local_branch_uses_real_crop_then_source_facts_and_actual_output_reviews(feedback_fixture, monkeypatch):
    state, glm, preparation, parent, folder, methods, policy, value, unknown = setup(feedback_fixture, monkeypatch)
    unknown_bytes = unknown.read_bytes()
    result = sf._run_parent(glm, state, preparation, parent, folder, methods, 'Synthetic locked generic knowledge.')
    assert result['status'] == 'model_checked_candidate' and result['model_quality_gate_passed'] is True
    assert state.renders == [3] and probe_media(result['final_video'])['duration_s'] == pytest.approx(
        result['measured_duration_s'], abs=.04)
    assert sha256_file(result['final_video']) == result['final_sha256']
    expected = local.replacement_assembly(value, policy)
    assert json.loads((folder / 'assembly.json').read_text(encoding='utf-8')) == expected
    assert expected['plan']['segments'][:-1] == policy['original_assembly']['plan']['segments'][:-1]
    assert state.source_plans == [expected['plan']]
    names = [job['name'] for job in glm.jobs]
    assert names[0] == local.STAGE and feedback.STAGE not in glm.attempted
    facts = [i for i, n in enumerate(names) if n.startswith('semantic_slice_')]
    claims = [i for i, n in enumerate(names) if n.startswith('semantic_claims_')]
    assert len(facts) == len(claims) == 2 and max(facts) < min(claims)
    assert names[-3:] == ['sf_3_blind', 'sf_3_economy', 'sf_3_review']
    assert unknown.read_bytes() == unknown_bytes
    assert next(c for c in state.data['calls'] if c['id'] == 'unknown_feedback166')['status'] == 'uncertain'
    assert not (unknown.parent / 'response.json').exists()
    count = len(glm.jobs)
    assert sf._run_parent(None, state, preparation, parent, folder, methods, '') == result
    assert len(glm.jobs) == count and state.renders == [3]


def test_unavailable_local_answer_stops_before_source_requests_or_render(feedback_fixture, monkeypatch):
    state, glm, preparation, parent, folder, methods, _, _, unknown = setup(
        feedback_fixture, monkeypatch, unavailable=True)
    before = unknown.read_bytes()
    result = sf._run_parent(glm, state, preparation, parent, folder, methods, '')
    assert result['status'] == 'stopped_local_replan_unavailable' and not result['model_quality_gate_passed']
    assert [job['name'] for job in glm.jobs] == [local.STAGE]
    assert state.renders == state.source_plans == [] and not list(folder.rglob('final.mp4'))
    assert not result.get('final_video') and unknown.read_bytes() == before
    assert (folder / 'result_independent_resume.json').exists()
    assert sf._run_parent(None, state, preparation, parent, folder, methods, '') == result


def test_new_local_plan_source_counterevidence_stops_without_render_or_blind_review(feedback_fixture, monkeypatch):
    state, glm, preparation, parent, folder, methods, _, _, unknown = setup(
        feedback_fixture, monkeypatch, unsupported=True)
    before = unknown.read_bytes()
    result = sf._run_parent(glm, state, preparation, parent, folder, methods, '')
    assert result['status'] == 'stopped_source_counterevidence' and result['source_blockers']
    assert state.renders == [] and not result.get('final_video')
    assert [job['name'] for job in glm.jobs][0] == local.STAGE
    assert not any(job['name'].endswith(('_blind', '_economy', '_review')) for job in glm.jobs)
    assert feedback.STAGE not in glm.attempted and unknown.read_bytes() == before
