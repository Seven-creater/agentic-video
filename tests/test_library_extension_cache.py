"""Cross-round real-media/queue cache reuse; replies are synthetic, no GLM."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library import extension_budget as extension
from omni_story.library.media import inventory_sources, prepare_window
from omni_story.library.pipeline import CodexMCP
from omni_story.library.semantic_pipeline import observe_selected_slices
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from test_library_execute import _bridge, _read, _semantic_fixture_responses, inputs

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Requires FFmpeg')


def setup_cached_round(inputs, *, changed_claim=False):
    reference, library, output = inputs
    catalog = inventory_sources(library, output / 'catalog')
    source = catalog['sources'][0]
    plan = {'slots': [{'slot_id': 'slot_1', 'intended_takeaway': 'synthetic visible geometry'}],
            'segments': [{'segment_id': 'segment_1', 'source_id': source['source_id'], 'window_id': 'window_1',
                          'source_in_s': 1, 'source_out_s': 2, 'role_ids': [],
                          'visual_claims': [{'claim_id': 'visible', 'kind': 'visual_action', 'description': 'red square visible'}]}]}
    windows = [{'window_id': 'window_1', 'observation': {'roles': []}}]
    write_json(output / 'watched_windows.json', windows)
    state = LibraryState(output, {'reference_sha256': 'a' * 64,
        'library_sources': [{k: s[k] for k in ('source_id', 'sha256')} for s in catalog['sources']]})
    state.enable_independent_continuation()
    with _bridge(output, _semantic_fixture_responses(reference, library)) as jobs:
        prior = observe_selected_slices(CodexMCP(state), plan, {source['source_id']: source}, windows,
                                       output / 'media_cache', output, 3)
        assert len(jobs) == 2
    historical = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()
                  and p.name not in {'library_state.json', 'mcp_current.json', 'mcp_ready.json', 'mcp_tools.json'}}
    extension.authorize(output, 'synthetic test authorization; no real model')
    if changed_claim:
        plan['segments'][0]['visual_claims'][0]['description'] = 'same geometry, newly phrased claim'
    state = extension.stage_state(output)
    with _bridge(output, lambda job: {'plan': plan} if 'finecut' in job['job_id'] else plan):
        glm = CodexMCP(state)
        glm.call('active_4_draft', 'synthetic distinct draft', reference, lambda v: None)
        glm.call('active_4_finecut', 'synthetic distinct refinement', reference, lambda v: None)
    write_json(output / 'artifacts/active_finecut_continuation_v1/plan.json', plan)
    return state, plan, source, windows, historical, prior


@pytest.mark.parametrize('changed_claim', [False, True])
def test_cross_round_facts_and_identical_claims_reuse_received_cache_and_allow_blind(inputs, changed_claim):
    state, plan, source, windows, historical, prior = setup_cached_round(inputs, changed_claim=changed_claim)
    reference, library, output = inputs
    before = state.usage()['requests']
    with _bridge(output, _semantic_fixture_responses(reference, library)) as jobs:
        current = observe_selected_slices(CodexMCP(state), plan, {source['source_id']: source}, windows,
                                         output / 'media_cache', output, 4)
        assert len(jobs) == (1 if changed_claim else 0)
        assert current['observations'] == prior['observations']
        proof_names = {name for name in _read(state.path)['artifacts'] if name.startswith(extension.CACHE_PREFIX)}
        assert len(proof_names) == (1 if changed_claim else 2)
        assert state.usage()['requests'] == before + len(jobs)
        # Production validator must accept both facts+claims fully cached, as
        # well as cached facts followed by an actual new comparison call.
        call, _ = state.begin_call('active_4_blind', {'synthetic': 'blind accepted after bound final evidence',
            'media_sha256': 'b' * 64, 'observation_scope': {'kind': 'complete_file', 'source_sha256': 'b' * 64,
                                                         'source_start_s': 0, 'source_end_s': 1}})
        state.complete_call(call, {'fixture': True})
        assert all(p.read_bytes() == old for p, old in historical.items())
        count = state.usage()['requests']
        resumed = extension.stage_state(output)
        assert resumed.usage()['requests'] == count
        for artifact_name in proof_names:
            assert len(resumed.data['artifacts'][artifact_name]) == 1


@pytest.mark.parametrize('target', ['proxy', 'lineage', 'parsed', 'source', 'proof', 'plan', 'claim_description'])
def test_cache_tampering_is_rejected_without_new_model_calls(inputs, target):
    state, plan, source, windows, _, _ = setup_cached_round(inputs)
    reference, library, output = inputs
    with _bridge(output, _semantic_fixture_responses(reference, library)) as jobs:
        observe_selected_slices(CodexMCP(state), plan, {source['source_id']: source}, windows,
                                output / 'media_cache', output, 4)
        assert len(jobs) == 0
        proxy = prepare_window(source, 1, 2, output / 'media_cache', fps=30)
        data = _read(state.path)
        fact = next(c for c in data['calls'] if c['name'].startswith('semantic_slice_3_'))
        proof_entry = next(entries[0] for name, entries in data['artifacts'].items()
                           if name.startswith(extension.CACHE_PREFIX + 'claims'))
        path = {'proxy': Path(proxy['path']), 'lineage': Path(proxy['path']).parent / 'lineage.json',
                'parsed': output / 'calls' / fact['id'] / 'parsed.json', 'source': Path(source['path']),
                'proof': Path(proof_entry['path']), 'plan': output / 'artifacts/active_finecut_continuation_v1/plan.json'}
        if target == 'claim_description':
            proof = _read(proof_entry['path'])
            proof['claims'][0]['description'] = 'forged unsupported claim'
            write_json(proof_entry['path'], proof)
            proof_entry['sha256'] = json_sha(proof)
            write_json(state.path, data)
        else:
            path[target].write_bytes(b'changed synthetic cache')
        before = _read(state.path)['request_count']
        with pytest.raises((LibraryStopped, ValueError)):
            extension.stage_state(output)
        assert _read(state.path)['request_count'] == before
        assert len(jobs) == 0


def test_blind_rejects_final_segment_without_facts_or_claim_binding_even_when_first_is_cached(inputs):
    state, plan, source, windows, _, _ = setup_cached_round(inputs)
    reference, library, output = inputs
    # Add a second final segment to the model-owned refined plan before proof
    # registration. Only its facts/claims completion may satisfy the blind gate.
    second = deepcopy(plan['segments'][0])
    second.update(segment_id='segment_2', source_in_s=3, source_out_s=4)
    second['visual_claims'][0]['claim_id'] = 'visible_2'
    plan['segments'].append(second)
    data = _read(state.path)
    finecut = next(c for c in data['calls'] if c['name'] == 'active_4_finecut')
    reply = {'status': 'complete', 'result': {'content': [{'type': 'text', 'text': json.dumps({'plan': plan})}]}}
    write_json(output / 'calls' / finecut['id'] / 'response.json', reply)
    write_json(output / 'calls' / finecut['id'] / 'parsed.json', {'plan': plan})
    finecut['response_sha256'] = json_sha(reply)
    write_json(state.path, data)
    write_json(output / 'artifacts/active_finecut_continuation_v1/plan.json', plan)
    with _bridge(output, _semantic_fixture_responses(reference, library)) as jobs:
        partial_plan = {**plan, 'segments': plan['segments'][:1]}
        observe_selected_slices(CodexMCP(extension.stage_state(output)), partial_plan, {source['source_id']: source}, windows,
                                output / 'media_cache', output, 4)
        with pytest.raises(LibraryStopped, match='blind_missing_final_slice_bindings'):
            extension.stage_state(output).begin_call('active_4_blind', {'synthetic': 'missing one final slice'})
        assert len(jobs) == 0
