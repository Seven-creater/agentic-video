"""Synthetic media execution, not a GLM editing-quality evaluation.

The fake replies exercise the production contracts and real FFmpeg execution;
they never contact a model or modify the actual movie-library run.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess

import pytest

from omni_story.library import slot_finecut as sf, slot_finecut_baselines as baselines
from omni_story.library.media import inventory_sources, probe_media, sha256_file
from omni_story.library.render import render_library_video
from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL
from omni_story.library.state import LibraryStopped, json_sha, write_json


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Synthetic integration requires FFmpeg and FFprobe')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def test_forward_navigation_cannot_reinterpret_paid_old_stages(tmp_path):
    old = {'name': 'sf_0_facts_old', 'status': 'received'}
    authorization = {'baseline_request_count': 0, 'knowledge_sha256': 'a' * 64}
    policy = {'policy': sf.local.FORWARD_NAVIGATION_POLICY['version'], 'task_id': 'synthetic',
              'baseline_request_count': 1, 'prefix_calls_sha256': json_sha([old]),
              'contract': sf.local.FORWARD_NAVIGATION_POLICY,
              'contract_sha256': json_sha(sf.local.FORWARD_NAVIGATION_POLICY),
              'knowledge_sha256': authorization['knowledge_sha256']}
    path = tmp_path / 'policy.json'
    write_json(path, policy)
    state = SimpleNamespace(data={'task_id': 'synthetic', 'calls': [old],
        'artifacts': {policy['policy']: [{'path': str(path), 'sha256': json_sha(policy)}]}},
        authorization=authorization, _reload=lambda: None)
    assert sf._forward_parent_navigation(state, old['name']) is False
    assert sf._forward_parent_navigation(state, 'sf_3_facts_new') is True
    state.data['calls'].append({'name': 'sf_3_facts_new', 'status': 'received'})
    assert sf._forward_parent_navigation(state, 'sf_3_facts_new') is True
    state.data['calls'][0]['name'] = 'rewritten_old_stage'
    with pytest.raises(ValueError, match='forward_navigation_policy_changed'):
        sf._forward_parent_navigation(state, 'sf_3_facts_new')


def _payload(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt[prompt.index(marker):])[0]


@pytest.fixture
def prepared(tmp_path):
    movie = tmp_path / 'movie.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=96x64:r=30:d=6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(movie)],
                   check=True, capture_output=True)
    reference = tmp_path / 'reference.mp4'
    shutil.copyfile(movie, reference)
    output = tmp_path / 'existing_task'
    output.mkdir()
    catalog = inventory_sources(movie, output / 'catalog')
    reference_catalog = inventory_sources(reference, output / 'reference_catalog')
    source = catalog['sources'][0]
    ref = reference_catalog['sources'][0]
    window = {'window_id': 'w1', 'status': 'watched', 'kind': 'continuous_window',
        'source_id': source['source_id'], 'source_sha256': source['sha256'],
        'path': str(movie.resolve()), 'sha256': source['sha256'],
        'source_start_s': 0, 'source_end_s': 6,
        'observation': {'window_id': 'w1', 'source_id': source['source_id'],
            'roles': [{'role_id': 'A', 'identity_confirmed': True, 'state': 'visible synthetic geometry',
                       'identity_evidence': 'colored geometry against the frame'}],
            'events': [{'local_start_s': 0, 'local_end_s': 6, 'observed_fact': 'Synthetic geometry stays visible.',
                        'role_ids': ['A']}],
            'usable_ranges': [{'local_in_s': 0, 'local_out_s': 6, 'role_ids': ['A'],
                               'event_indices': [0], 'continuity_notes': 'synthetic test only'}],
            'uncertainties': []}}
    write_json(output / 'watched_windows.json', [window])
    write_json(output / 'reference_reading.json', {'reference_sha256': ref['sha256'],
        'theme': 'Synthetic fixture, not a real narrative evaluation.'})
    methods = {'reference_sha256': ref['sha256'], 'methods': [{'method_id': 'm1',
        'requires_audio': False, 'form': 'visible geometric subject', 'function': 'keep the subject readable'}]}
    write_json(output / 'editing_reference_v2.json', methods)
    state = {'task_id': 'synthetic_existing_task', 'artifacts': {}, 'calls': [],
        'input_lock': {'reference_sha256': ref['sha256'], 'library_sources': [
            {'source_id': source['source_id'], 'sha256': source['sha256']}]}}
    for round_no, cuts in ((0, [(0, 2), (3, 5)]), (3, [(.5, 1.5), (3.5, 4.5)])):
        plan = {'reference_sha256': ref['sha256'], 'focus_role_id': 'subject',
            'focus_role_bindings': [{'window_id': 'w1', 'role_id': 'A', 'identity_evidence': 'Synthetic geometry.'}],
            'slots': [{'slot_id': 'old1', 'intended_takeaway': 'OLD_CREATIVE_ANSWER', 'segment_ids': ['old_s1']},
                      {'slot_id': 'old2', 'intended_takeaway': 'OLD_CREATIVE_ANSWER_2', 'segment_ids': ['old_s2']}],
            'segments': [{'segment_id': 'old_s' + str(i+1), 'slot_id': 'old' + str(i+1),
                'source_id': source['source_id'], 'window_id': 'w1', 'source_in_s': a, 'source_out_s': b,
                'speed': 1, 'freeze_tail_s': .3 if round_no == 3 and i == 1 else 0,
                'look': 'none', 'framing': 'fit', 'role_ids': ['A'],
                'visual_claims': [{'claim_id': 'old_v' + str(i), 'kind': 'visual_action',
                                  'description': 'OLD_SOURCE_ANSWER'}]} for i, (a, b) in enumerate(cuts)],
            'editing_bindings': [{'method_id': 'm1', 'status': 'planned', 'segment_ids': ['old_s1', 'old_s2'],
                'intended_relation': 'Geometry remains visible.', 'operation': 'hard cut',
                'verification': 'visible geometry', 'limitations': []}],
            'audio_mode': 'silent', 'source_gain_db': 0, 'reference_gain_db': 0,
            'width': 96, 'height': 128, 'fps': 30, 'limitations': []}
        write_json(output / f'plan_{round_no}.json', plan)
        render_library_video(catalog, plan, output / f'render_{round_no}', fps=30, width=96, height=128)
        call_id = f'known_plan_{round_no}'
        request, response = {'fixture': 'no real model', 'round': round_no}, {'fixture_plan': plan}
        for name, value in (('request', request), ('response', response), ('parsed', plan)):
            write_json(output / 'calls' / call_id / (name + '.json'), value)
        state['calls'].append({'id': call_id, 'name': call_id, 'status': 'received',
            'request_sha256': json_sha(request), 'response_sha256': json_sha(response)})
    write_json(output / 'library_state.json', state)
    record = baselines.prepare(output)
    protected = {Path(row['path']): Path(row['path']).read_bytes() for row in record['protected_files']}
    return output, record, methods, protected


class FakeState:
    def __init__(self, output, protected):
        self.output, self.protected = output, protected
        self.data = {'calls': [], 'artifacts': {}}
        self.renders = []

    def _reload(self):
        pass

    def assert_protected(self):
        assert all(path.read_bytes() == original for path, original in self.protected.items())
        return {'unknown_inputs': []}

    def assert_source_inputs(self, plan, catalog):
        self.assert_protected()
        assert len(plan['segments']) == 2

    def claim_render(self, round_no):
        assert type(round_no) is int and round_no in (0, 3)
        assert round_no not in self.renders
        self.renders.append(round_no)
        return self.output / 'synthetic_finecuts' / f'render_{round_no}' / 'render'

    def set_artifact(self, name, value):
        assert name not in self.data['artifacts']
        path = self.output / 'synthetic_receipts' / (name + '.json')
        write_json(path, value)
        self.data['artifacts'][name] = [{'path': str(path), 'sha256': json_sha(value)}]


class FakeGLM:
    """Deterministic test replies always pass the production validator."""
    def __init__(self, state, parent, *, unsupported=False):
        self.state, self.parent, self.unsupported = state, parent, unsupported
        self.jobs, self.proposals = [], []

    def call(self, name, prompt, media, validator, **kwargs):
        assert Path(media).is_file()
        self.jobs.append({'name': name, 'prompt': prompt, 'media': str(media), **kwargs})
        value = self.answer(name, prompt, media)
        validator(value)
        self.state.data['calls'].append({'id': 'fixture_' + str(len(self.state.data['calls'])),
                                        'name': name, 'status': 'received'})
        return deepcopy(value)

    def answer(self, name, prompt, media):
        parent = self.parent
        binding = {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256']}
        if name.endswith('_outline'):
            split = parent['provenance'][0]['output_out_s']
            self.outline = {**binding, 'slots': [{'slot_id': 'new' + str(i+1), 'start_s': a, 'end_s': b,
                'intended_takeaway': 'NEW_INFORMATION_' + str(i+1), 'entry_state': 'Visible geometry',
                'exit_state': 'Visible geometry after cut', 'link_to_previous': 'Opening' if i == 0 else 'Visible subject continues',
                'link_to_next': 'Subject continues' if i == 0 else 'Ending'}
                for i, (a, b) in enumerate(((0, split), (split, parent['duration_s'])))],
                'limitations': [], 'uncertainties': []}
            return self.outline
        if '_facts_' in name:
            payload = _payload(prompt, '{"baseline_id":')
            assert 'NEW_INFORMATION_' not in prompt and 'OLD_CREATIVE_ANSWER' not in prompt
            return {key: payload[key] for key in binding} | {
                'slot_id': payload['slot_id'], 'slot_start_s': payload['slot_start_s'], 'slot_end_s': payload['slot_end_s'],
                'time_domain': 'slot_local_output', 'evidence': [{'evidence_id': 'parent_visible', 'start_s': 0,
                    'end_s': payload['observed_duration_s'], 'observed_fact': 'Synthetic geometric subject is visible.',
                    'kind': 'shape', 'basis': 'visual'}], 'limitations': [], 'uncertainties': []}
        if '_proposal_' in name:
            payload = _payload(prompt, '{"parent":')
            navigation = _payload(prompt, '{"mechanical_no_replay_source_scopes":')
            assert isinstance(navigation['mechanical_no_replay_source_scopes'], list)
            slot = payload['slot']
            index = next(i for i, s in enumerate(self.outline['slots']) if s['slot_id'] == slot['slot_id'])
            original = parent['provenance'][index]
            a, b = original['source_in_s'] + .2, original['source_out_s'] - .2
            proposal = {**binding, 'slot_id': slot['slot_id'], 'candidates': [{'candidate_id': 'c1',
                'operations': [{'parent_segment_index': index, 'source_in_s': a, 'source_out_s': b,
                    'speed': 2 if index == 0 else .5, 'freeze_tail_s': 0 if index == 0 else .1,
                    'reason': 'Synthetic retiming and visibility fixture.', 'evidence_ids': ['parent_visible'],
                    'essential_intervals': [{'source_start_s': a, 'source_end_s': b, 'min_readable_s': .1,
                        'information': 'Visible geometric subject', 'evidence_ids': ['parent_visible'],
                        'continues_in_tail_frame': index == 1}]}],
                'meaning_status': 'preserved', 'rationale': 'Fixture choice, not semantic truth.', 'limitations': []}]}
            self.proposals.append(proposal)
            return proposal
        if name.endswith('_assemble'):
            assert 'OLD_CREATIVE_ANSWER' not in prompt and 'OLD_SOURCE_ANSWER' not in prompt
            shape = _payload(prompt, '{"parent":')
            assert isinstance(shape['response_contract']['plan'], dict)
            assert shape['response_contract']['plan']['segments'][0]['framing'] in shape['allowed_enum_values']['framing']
            navigation = _payload(prompt, '{"mechanical_no_replay_source_scopes":')
            assert isinstance(navigation['mechanical_no_replay_source_scopes'], list)
            plan = deepcopy(parent['original_plan'])
            plan['segments'], plan['slots'] = [], []
            bindings, timing = [], []
            for index, (slot, proposal) in enumerate(zip(self.outline['slots'], self.proposals, strict=True)):
                operation = proposal['candidates'][0]['operations'][0]
                original = parent['provenance'][index]
                segment_id, claim_id = f'final_{index}', f'visible_{index}'
                plan['segments'].append({'segment_id': segment_id, 'slot_id': slot['slot_id'],
                    'source_id': original['source_id'], 'window_id': original['window_id'], 'role_ids': original['role_ids'],
                    **{key: operation[key] for key in ('source_in_s', 'source_out_s', 'speed', 'freeze_tail_s')},
                    'look': 'none', 'framing': 'fit', 'visual_claims': [{'claim_id': claim_id, 'kind': 'visual_action',
                        'description': 'The synthetic geometric subject remains visible.'}]})
                plan['slots'].append({'slot_id': slot['slot_id'], 'intended_takeaway': slot['intended_takeaway'],
                                      'segment_ids': [segment_id]})
                bindings.append({'segment_id': segment_id, 'slot_id': slot['slot_id'], 'candidate_id': 'c1', 'operation_index': 0})
                timing.append({'segment_id': segment_id, 'essential_claims': [{'essential_interval_index': 0, 'claim_ids': [claim_id]}]})
            plan['editing_bindings'][0]['segment_ids'] = ['final_0', 'final_1']
            self.plan = plan
            return {**binding, 'selections': [{'slot_id': s['slot_id'], 'candidate_id': 'c1'} for s in self.outline['slots']],
                'plan': plan, 'segment_bindings': bindings, 'timing_checks': timing,
                'transition_checks': [{'from_segment_id': 'final_0', 'to_segment_id': 'final_1',
                    'relation': 'Synthetic subject remains visible across the cut.', 'status': 'planned'}], 'limitations': []}
        if name.startswith('semantic_slice_'):
            assert 'NEW_INFORMATION_' not in prompt and 'required_claims：' not in prompt
            value = json.loads(prompt.split('metadata：', 1)[1])
            value.update(characters=[{'character_id': 'observed_A', 'appearance': 'synthetic colored geometry'}],
                evidence=[{'evidence_id': 'source_visible', 'kind': 'visual_action', 'local_start_s': 0,
                    'local_end_s': value['observed_duration_s'], 'description': 'Synthetic geometry is visible.',
                    'character_ids': ['observed_A'], 'basis_evidence_ids': []}], uncertainties=[])
            return value
        if name.startswith('semantic_claims_'):
            observation = json.loads(prompt.split('observation：', 1)[1].split('\nrequired_claims：', 1)[0])
            claims = json.loads(prompt.split('\nrequired_claims：', 1)[1].split('\nrole_hypotheses：', 1)[0])
            return {'protocol': SEMANTIC_PROTOCOL, 'segment_id': observation['segment_id'],
                'observation_sha256': json_sha(observation), 'claim_checks': [{'claim_id': c['claim_id'],
                    'status': 'unsupported' if self.unsupported and c['kind'] == 'visual_action' else 'supported',
                    'evidence_ids': [] if self.unsupported and c['kind'] == 'visual_action' else ['source_visible'],
                    'reason': 'Synthetic test evidence comparison.',
                    'limitations': ['Required action is not supported.'] if self.unsupported and c['kind'] == 'visual_action' else []}
                    for c in claims], 'uncertainties': []}
        if name.endswith('_blind'):
            assert 'NEW_INFORMATION_' not in prompt and 'OLD_CREATIVE_ANSWER' not in prompt
            value = json.loads(prompt.split('绑定：', 1)[1])
            duration = probe_media(media)['duration_s']
            value.update(observed_story='Geometry remains visible.', apparent_theme='Fixture visual continuity.',
                main_characters=['synthetic geometric subject'], text_dependency='none', confusions=[],
                evidence=[{'evidence_id': 'blind_visible', 'claim_id': 'blind_c1', 'kind': 'visual_action',
                    'start_s': 0, 'end_s': min(.1, duration), 'observed_fact': 'Synthetic geometry stays visible.',
                    'basis_evidence_ids': []}])
            return value
        if name.endswith('_economy'):
            assert 'NEW_INFORMATION_' not in prompt and 'OLD_CREATIVE_ANSWER' not in prompt
            context = _payload(prompt, '{"actual_output":')
            actual = context['actual_output']
            return {'video_sha256': actual['sha256'], 'economy_status': 'pass', 'narrative_readability': 'pass',
                'segment_checks': [{'segment_id': s['segment_id'], 'status': 'necessary',
                    'output_evidence': [{'start_s': s['output_in_s'], 'end_s': s['output_out_s'],
                                        'observed_fact': 'Synthetic subject remains visible.'}], 'reason': 'Synthetic test judgment.'}
                    for s in actual['provenance']], 'limitations': ['Synthetic judgments are not GLM quality evidence.']}
        if name.endswith('_review'):
            context = _payload(prompt, '{"reference":')
            assert context['essential_timing_proposals'] and context['parent_slot_obligations']
            blind = context['blind_reading']
            evidence = [{'segment_id': o['segment_id'], 'evidence_id': 'source_visible'} for o in context['source_observations']]
            checks = [{'claim_id': c['claim_id'], 'status': 'supported',
                'evidence_refs': [next((e for e in evidence if e['segment_id'] == c.get('segment_id')), evidence[0])],
                'blind_evidence_ids': [], 'reason': 'Synthetic comparison.', 'limitations': []} for c in context['required_claims']]
            checks.extend({'claim_id': e['claim_id'], 'status': 'supported', 'evidence_refs': [],
                'blind_evidence_ids': [e['evidence_id']], 'reason': 'Synthetic actual output fact.', 'limitations': []}
                for e in blind['evidence'])
            return {'protocol': SEMANTIC_PROTOCOL, 'reference_sha256': context['reference']['reference_sha256'],
                'video_sha256': context['video_sha256'], 'theme_status': 'pass', 'editing_status': 'pass',
                'continuity_status': 'pass', 'visual_narrative_status': 'pass', 'evidence': ['Synthetic output fixture.'],
                'limitations': ['Synthetic replies do not establish editing quality.'], 'revision_requests': [],
                'fact_checks': checks, 'contradictions': [], 'method_checks': [{'method_id': 'm1',
                    'form_status': 'pass', 'function_status': 'pass', 'audio_status': 'not_applicable',
                    'output_evidence': [{'start_s': 0, 'end_s': min(.1, context['output_duration_s']),
                        'observed_fact': 'Synthetic geometric subject is visible.'}], 'limitations': []}]}
        raise AssertionError('unexpected fake stage: ' + name)


def test_two_parents_execute_real_media_all_facts_before_claims_and_zero_call_resume(prepared):
    output, preparation, methods, protected = prepared
    state = FakeState(output, protected)
    results, all_jobs = [], []
    knowledge = sf.local.HANDBOOK.read_text(encoding='utf-8')
    for parent in preparation['parents']:
        glm = FakeGLM(state, parent)
        folder = output / 'synthetic_finecuts' / parent['baseline_id']
        result = sf._run_parent(glm, state, preparation, parent, folder, methods, knowledge)
        assert result['status'] == 'model_checked_candidate' and result['model_quality_gate_passed'] is True
        assert probe_media(result['final_video'])['duration_s'] == pytest.approx(result['measured_duration_s'], abs=.04)
        assert sha256_file(result['final_video']) == result['final_sha256']
        names = [j['name'] for j in glm.jobs]
        facts = [i for i, name in enumerate(names) if name.startswith('semantic_slice_')]
        claims = [i for i, name in enumerate(names) if name.startswith('semantic_claims_')]
        assert len(facts) == len(claims) == 2 and max(facts) < min(claims)
        assert names[-3:] == [f'sf_{parent["round"]}_{stage}' for stage in ('blind', 'economy', 'review')]
        assert all(j['scope']['source_sha256'] == parent['provenance'][0]['source_sha256']
                   for j in glm.jobs if j['name'].startswith('semantic_slice_'))
        before = {p: p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        count = len(glm.jobs)
        assert sf._run_parent(None, state, preparation, parent, folder, methods, knowledge) == result
        assert len(glm.jobs) == count and all(p.read_bytes() == value for p, value in before.items())
        results.append(result)
        all_jobs.extend(glm.jobs)
    assert state.renders == [0, 3]
    assert results[0]['parent_sha256'] != results[1]['parent_sha256']
    assert all(path.read_bytes() == original for path, original in protected.items())
    assert {'semantic_slice_20', 'semantic_slice_21'} <= {j['name'].rsplit('_', 1)[0] for j in all_jobs}
    # Corrupting result metadata cannot turn its self-reported list into authority.
    parent = preparation['parents'][0]
    result_path = output / 'synthetic_finecuts' / parent['baseline_id'] / 'result.json'
    edited = _read(result_path)
    edited['completed_files'] = []
    write_json(result_path, edited)
    with pytest.raises(ValueError, match='completed_result_changed'):
        sf._run_parent(None, state, preparation, parent, result_path.parent, methods, knowledge)


def test_known_source_counterevidence_stops_before_render_or_output_review(prepared):
    output, preparation, methods, protected = prepared
    state = FakeState(output, protected)
    parent = preparation['parents'][0]
    glm = FakeGLM(state, parent, unsupported=True)
    folder = output / 'synthetic_finecuts' / parent['baseline_id']
    result = sf._run_parent(glm, state, preparation, parent, folder, methods,
                            sf.local.HANDBOOK.read_text(encoding='utf-8'))
    assert result['status'] == 'stopped_source_counterevidence' and result['source_blockers']
    assert state.renders == [] and not result.get('final_video')
    assert not any(j['name'].endswith(('_blind', '_economy', '_review')) for j in glm.jobs)
    assert not list(folder.rglob('final.mp4'))
    count = len(glm.jobs)
    assert sf._run_parent(None, state, preparation, parent, folder, methods, '') == result
    assert len(glm.jobs) == count
    assert all(path.read_bytes() == original for path, original in protected.items())


def test_execute_does_not_infer_permission_from_preparation(prepared):
    output, _, _, protected = prepared
    before = (output / 'library_state.json').read_bytes()
    with pytest.raises(LibraryStopped, match='one_authorization_required'):
        sf.execute(output)
    assert (output / 'library_state.json').read_bytes() == before
    assert all(path.read_bytes() == original for path, original in protected.items())
    assert not (output / 'mcp_queue').exists()


def test_executable_change_detection_includes_framing_and_audio_parameters():
    original = {'segments': [{'source_id': 'movie', 'window_id': 'w', 'source_in_s': 1,
        'source_out_s': 2, 'speed': 1, 'framing': 'fit', 'look': 'none'}],
        'fps': 30, 'width': 96, 'height': 128, 'audio_mode': 'silent'}
    crop, audio = deepcopy(original), deepcopy(original)
    crop['segments'][0]['framing'] = 'crop'
    audio['audio_mode'] = 'source'
    assert sf.executable_identity(original) != sf.executable_identity(crop)
    assert sf.executable_identity(original) != sf.executable_identity(audio)


@pytest.mark.parametrize('change', ['valid_model_retiming', 'still_short', 'unlisted_speed', 'source_cut'])
def test_bound_retiming_cannot_change_cuts_or_skip_exposure(prepared, change):
    output, preparation, methods, protected = prepared
    parent = preparation['parents'][0]
    glm = FakeGLM(FakeState(output, protected), parent)
    media = Path(parent['path'])
    outline = glm.answer('sf_0_outline', sf.local.slots_prompt(parent, {}), media)
    for slot in outline['slots']:
        facts = glm.answer('sf_0_facts_test', sf.local.neutral_facts_prompt(parent, slot), media)
        prompt = sf.local.proposal_prompt(sf._view(parent), slot, facts, parent['allowed_windows'])
        prompt += '\n' + json.dumps({'mechanical_no_replay_source_scopes': []})
        glm.answer('sf_0_proposal_test', prompt, media)
    catalog = _read(output / 'catalog/inventory.json')
    prompt = sf.assembly_prompt(parent, outline, glm.proposals, catalog, parent['allowed_windows'],
        preparation['reference'], methods, '') + '\n' + json.dumps({'mechanical_no_replay_source_scopes': []})
    assembly = glm.answer('sf_0_assemble', prompt, media)
    # Preserve the proposal's minimum. Only a future model plan may retime this
    # listed operation; no source boundaries or other operation may change.
    glm.proposals[0]['candidates'][0]['operations'][0]['essential_intervals'][0]['min_readable_s'] = 1
    assembly['plan']['segments'][0]['speed'] = .5
    if change == 'still_short':
        assembly['plan']['segments'][0]['speed'] = 2
    elif change == 'unlisted_speed':
        assembly['plan']['segments'][1]['speed'] = 1
    elif change == 'source_cut':
        assembly['plan']['segments'][0]['source_in_s'] += .1
    args = (assembly, parent, outline, glm.proposals, catalog, parent['allowed_windows'], preparation['reference'], methods)
    if change == 'valid_model_retiming':
        assert sf.validate_assembly(*args, retiming_operations=(('new1', 'c1', 0),)) is assembly
    else:
        with pytest.raises(ValueError, match='final_model_retiming_still_short|proposed_operation_changed'):
            sf.validate_assembly(*args, retiming_operations=(('new1', 'c1', 0),))
