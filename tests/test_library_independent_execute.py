"""Production queue/FFmpeg on synthetic history; no real model or film."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library import goal_budget as goal, research_resume, flat_refinement, audience_obligations
from omni_story.library import independent_source_resume as independent
from omni_story.library.state import json_sha, write_json
from omni_story.library.media import sha256_file
from test_library_execute import inputs, _bridge, _read
from test_library_goal_feedback_continuation import prepare
from test_library_research_goal_execute import _research_answer, _all_bytes
from test_library_active_finecut_execute import _responses

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Needs FFmpeg')


def _history(inputs):
    reference, library, output = inputs
    prepare(inputs)
    research_resume.enable(output, 'Synthetic source-evidence work', first_round=5)
    state = goal.stage_state(output)
    grant = goal.get_authorization(state)
    for r in range(5, 11):
        state.set_artifact(f'goal_round_{r}', {'round': r, 'task_id': state.data['task_id'],
            'input_lock_sha256': json_sha(state.data['input_lock']), 'goal_guide_sha256': grant['goal_guide_sha256'],
            'observation_compatibility': grant['observation_compatibility'], 'new_unique_windows': 0, 'max_renders': 1})
        if r < 10:
            result = {'status': 'stopped_protocol_failure', 'selected_round': r, 'new_renders': 0,
                      'test_note': 'Synthetic stopped history, no quality claim.'}
            write_json(output / f'result_goal_feedback_{r}.json', result)
            state.set_artifact(f'goal_result_{r}', {'result_sha256': json_sha(result), 'completed_files': []})
    flat_refinement.enable(output, 'Synthetic serializer')
    research_resume.enable_preplanning(output, 'Synthetic fact-first strategy')
    audience_obligations.enable(output, 'Synthetic audience and sampled-source strategy')
    old = _read(output / 'library_state.json')
    assert len(old['calls']) == 13
    prototype = _responses(reference, library)({'job_id': 'glm_000_plan_0', 'arguments': {'prompt': ''}})
    seed = deepcopy(prototype)
    ranges = [(1.5, 1.7), (1.8, 1.9), (2, 2.2), (2.3, 2.4), (2.5, 2.7), (3, 3.1)]
    seed['segments'] = [{**deepcopy(prototype['segments'][0]), 'segment_id': f'seg_{i}',
                         'source_in_s': a, 'source_out_s': b} for i, (a, b) in enumerate(ranges, 1)]
    for s in seed['segments']:
        s.pop('caption', None)
        s.pop('freeze_tail_s', None)
    seed['slots'][0]['segment_ids'] = [s['segment_id'] for s in seed['segments']]
    for b in seed['editing_bindings']:
        b['segment_ids'] = seed['slots'][0]['segment_ids']
    requests = [('active_5_draft', prototype, None), ('active_5_finecut', {'plan': prototype}, None)]
    for n in range(16, 122):
        requests.append((f'semantic_slice_5_{n:016x}', {'synthetic_historical_placeholder': n}, None))
    requests += [('active_6_draft', {}, None), ('active_6_draft_repair', {}, 'active_6_draft'),
                 ('active_7_draft', {}, None), ('active_8_draft', {}, None),
                 ('active_8_draft_repair', {}, 'active_8_draft'), ('active_9_draft', {}, None),
                 ('active_9_draft_repair', seed, 'active_9_draft'), ('active_9_finecut', {}, None),
                 ('active_9_finecut_repair', {}, 'active_9_finecut'), ('active_10_draft', None, None)]
    ids = {}
    for offset, (name, value, parent) in enumerate(requests, 14):
        ident = f'glm_{offset:03d}_{name}'
        request = {'arguments': {'prompt': 'Synthetic historical fixture ' + ident},
                   'media_sha256': 'd' * 64, 'tool': 'analyze_video'}
        if value is None:
            ref = _read(output / 'reference_catalog/inventory.json')['sources'][0]
            request['observation_scope'] = {'kind': 'continuous_window', 'source_sha256': ref['sha256'],
                                           'source_start_s': 0, 'source_end_s': ref['duration_s']}
        folder = output / 'calls' / ident
        write_json(folder / 'request.json', request)
        call = {'id': ident, 'name': name, 'status': 'received' if value is not None else 'uncertain',
                'request_sha256': json_sha(request), 'repair_of': ids.get(parent), 'usage': {}}
        if value is not None:
            response = {'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}
            write_json(folder / 'response.json', response)
            write_json(folder / 'parsed.json', value)
            call['response_sha256'] = json_sha(response)
        old['calls'].append(call)
        ids[name] = ident
    old['request_count'] = len(old['calls'])
    assert old['request_count'] == 131
    write_json(output / 'library_state.json', old)
    goal.get_authorization(type('ReadOnly', (), {'output': output})())
    state = goal.stage_state(output)
    state.set_artifact('goal_research_network_10', {'test_note': 'Synthetic unknown original, no POST sent.'})
    folder = output / 'artifacts' / independent.POLICY
    folder.mkdir()
    snapshot = folder / 'baseline_state.json'
    write_json(snapshot, state.data)
    c = state.data['calls'][-1]
    req = _read(output / 'calls' / c['id'] / 'request.json')
    model = state.data['calls'][127]
    assert model['id'] == independent.DRAFT_CALL
    sources = {s['source_id']: s for s in _read(output / 'catalog/inventory.json')['sources']}
    rows = independent._prefacts(seed, sources)
    record = {'policy': independent.POLICY, 'round': 11, 'task_id': old['task_id'],
        'input_lock_sha256': json_sha(old['input_lock']), 'reference_sha256': old['input_lock']['reference_sha256'],
        'baseline_request_count': 131, 'activation_baseline_requests': 131,
        'baseline_state_path': str(snapshot), 'baseline_state_sha256': sha256_file(snapshot),
        'prefix_calls_sha256': json_sha(state.data['calls']),
        'protected_files': [{'path': str(p.resolve()), 'sha256': sha256_file(p)} for p in (output / 'calls').glob('*/*') if p.is_file()],
        'admitted_unknown_call_ids': [c['id']], 'unknown_inputs': [{'call_id': c['id'],
             'request_sha256': c['request_sha256'], 'media_sha256': req['media_sha256'], 'scope': independent._scope(req)}],
        'prefacts': rows, 'planning_carrier_stage': rows[0]['stage'], 'model_draft_call_id': model['id'],
        'model_draft_request_sha256': model['request_sha256'], 'model_draft_response_sha256': model['response_sha256'],
        'model_draft_sha256': json_sha(seed), 'model_timeout_ms': 1200000, 'tool_timeout_ms': 1260000,
        'reference_media_forbidden': True, 'old_calls_unchanged': True,
        'reference_is_known_cached_model_interpretation': True, 'no_numeric_request_ceiling': True,
        'new_unique_windows': 0, 'new_renders': 1, 'repairs_per_stage': 1}
    state.set_artifact(independent.ARTIFACT, record)
    return state, record


def test_independent_prefacts_before_draft_real_render_and_zero_call_resume(inputs):
    reference, library, output = inputs
    state, record = _history(inputs)
    old_calls = deepcopy(state.data['calls'])
    old_files = {p: p.read_bytes() for p in (output / 'calls').glob('*/*') if p.is_file()}
    oracle = _research_answer(inputs, variant=True)
    seen = []

    def answer(job):
        stage = job['job_id'].split('_', 2)[2].removesuffix('_repair')
        request = _read(output / 'calls' / job['job_id'] / 'request.json')
        independent.check_request(record, request)
        seen.append(stage)
        prompt = job['arguments']['prompt']
        if stage == 'active_11_draft':
            assert set(p['stage'] for p in record['prefacts']) <= set(seen)
            assert job['tool'] == 'analyze_image'
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
            assert context['independent_input_boundary']['reference_binding'].startswith('Previously received')
            assert len(context['research_refinement_evidence']['source_observations']) >= 6
        if stage == 'active_11_finecut':
            assert job['tool'] == 'analyze_image'
            inp = json.JSONDecoder().raw_decode(prompt[prompt.index('{"original_draft":'):])[0]
            shim = deepcopy(job)
            shim['arguments']['prompt'] = json.dumps({'policy': 'synthetic_adapter', 'draft': inp['original_draft'], 'evidence': inp['observations'],
                                                     'test_note': 'timing_checks evidence_timing_refinement_v1'})
            return oracle(shim)
        if stage.startswith('semantic_claims_11_'):
            inp = json.JSONDecoder().raw_decode(prompt[prompt.index('{"observation":'):])[0]
            shim = deepcopy(job)
            shim['arguments']['prompt'] = '\nobservation：' + json.dumps(inp['observation']) + \
                '\nrequired_claims：' + json.dumps(inp['required_claims']) + '\nrole_hypotheses：' + json.dumps(inp['role_hypotheses'])
            return oracle(shim)
        return oracle(job)

    with _bridge(output, answer) as jobs:
        result = independent.execute(reference, library, output)
        assert result['selected_round'] == 11 and result['new_renders'] == 1
        assert result['active_finecut_gate_passed'] is True
        assert _read(output / 'library_state.json')['calls'][:131] == old_calls
        assert all(p.read_bytes() == raw for p, raw in old_files.items())
        before = _all_bytes(output)
        count = len(jobs)
        assert independent.execute(reference, library, output) == result
        assert len(jobs) == count and _all_bytes(output) == before
