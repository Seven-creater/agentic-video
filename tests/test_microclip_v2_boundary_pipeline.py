"""Real synthetic frames through the new resolver and existing output gates."""
from copy import deepcopy
from pathlib import Path

import pytest

from test_microclip_v2_pipeline import task
from omni_story.library import microclip_v2 as pipeline
from omni_story.library import microclip_v2_state, microclip_v2_boundary_state as boundary_state


@pytest.fixture
def navigation(task, monkeypatch):
    output, auth, shared = task
    auth['boundary_resume'] = {'policy': boundary_state.POLICY, 'knowledge_path': auth['knowledge_path']}
    shared.update(parsed={}, effective={}, navigation_mode='observe', selected_id=None)
    State = microclip_v2_state.SlotMicroclipState
    State.assert_protected = lambda self: auth
    def received(self, stem):
        return {'id': stem, 'name': stem, 'status': 'received'}, deepcopy(shared['parsed'][stem])
    State._received = received
    State._events = lambda self: shared['parsed']['mc2_anchors']['events']
    def finish(self, value):
        from omni_story.library.state import write_json
        path = Path(auth['execution_directory']) / 'result_boundary_recovered.json'
        write_json(path, value)
        return path
    State.finish = finish

    def effective(root, data, index):
        return shared['effective'].get(index)
    def record(state, index, call, value):
        d = shared['inputs']['mc2_input_' + call['name']]
        shared['effective'][index] = (call, {**value, 'status': 'confirmed'}, d)
    monkeypatch.setattr(boundary_state, 'effective_edge', effective)
    monkeypatch.setattr(boundary_state, 'record_effective_edge', record)

    OriginalGLM = pipeline.CodexMCP
    class GLM(OriginalGLM):
        def call(self, name, prompt, media, validator, *, image=False, scope):
            if name.startswith('mc2_edge_1_'):
                shared['calls'].append(name)
                d = shared['inputs']['mc2_input_' + name]
                import json
                rows = json.loads(Path(d['lineage_path']).read_text(encoding='utf-8'))['frames']
                anchor = shared['parsed']['mc2_edge_1']['anchor_frame_id']
                if '_view_' in name:
                    value = {'frames': [{'frame_id': r['frame_id'], 'visible_action_or_state': 'Changing geometry.',
                        'visible_text': ''} for r in rows], 'visible_event': 'Geometry changes.',
                        'missing_information': [], 'limitations': []}
                    shared['selected_id'] = rows[-1]['frame_id']
                else:
                    action = 'observe' if name.endswith('_nav_0') and shared['navigation_mode'] == 'observe' else 'confirm'
                    if shared['navigation_mode'] == 'blocked':
                        action = 'blocked'
                    selected = shared['selected_id'] or anchor
                    value = {'event_id': 'e0', 'anchor_frame_id': anchor, 'obligation_ids': ['o0'],
                        'action': action, 'cut_intent': 'semantic_trim', 'question': 'Confirm continuing state.',
                        'resolution': 'Choose an intentional in-shot ending; do not claim an original hard cut.',
                        'source_start_s': .8 if action == 'observe' else None,
                        'source_end_s': 1.2 if action == 'observe' else None,
                        'confirmed_frame_id': selected if action == 'confirm' else None,
                        'evidence_frame_ids': [selected] if action == 'confirm' else [anchor],
                        'blocking_questions': [] if action == 'confirm' else ['State after candidate is unknown.'],
                        'limitations': ['Synthetic model decisions are not editing quality evidence.']}
                    if shared['navigation_mode'] == 'no_progress':
                        value.update(action='observe', source_start_s=0, source_end_s=.01,
                                     confirmed_frame_id=None, evidence_frame_ids=[anchor],
                                     blocking_questions=['Already seen frame.'])
                validator(value)
            else:
                value = super().call(name, prompt, media, lambda _: None, image=image, scope=scope)
                if name == 'mc2_edge_1':
                    value.update(status='blocked', blocking_questions=['Proposed hard cut has no visible switch.'])
                if name == 'mc2_intent':
                    value['original_claim_checks'][0]['verdict'] = 'partial'
                    value['obligations'][0]['support'] = 'mixed'
                if name == 'mc2_plan' and shared['selected_id']:
                    ident = shared['selected_id']
                    # The model's synthetic plan uses its own newly confirmed ID.
                    descriptor = shared['effective'][1][2]
                    import json
                    rows = json.loads(Path(descriptor['lineage_path']).read_text(encoding='utf-8'))['frames']
                    selected_row = next(r for r in rows if r['frame_id'] == ident)
                    value['shots'][0]['end_frame_id'] = ident
                    start = shared['overview'][0]['source_time_s']
                    value['obligation_coverage'][0]['exposure_s'] = (selected_row['frame_end_s'] - start) / 1.5
                validator(value)
            shared['parsed'][name] = deepcopy(value)
            return value
    monkeypatch.setattr(pipeline, 'CodexMCP', GLM)
    return output, auth, shared


@pytest.mark.parametrize('mode', ['observe', 'semantic_trim'])
def test_new_evidence_or_explicit_trim_then_neighbor_confirmation_and_actual_review(navigation, mode):
    output, auth, shared = navigation
    shared['navigation_mode'] = mode
    result = pipeline.execute(output)
    assert result['final_video'] and Path(result['final_video']).exists()
    assert shared['renders'] == 1
    assert result['model_quality_gate_passed'] is False
    assert result['semantic_complete'] is False  # Old partial/mixed is retained.
    calls = shared['calls']
    nav = 'mc2_edge_1_nav_0'
    confirm = 'mc2_edge_1_confirm_1' if mode == 'observe' else 'mc2_edge_1_confirm_0'
    assert calls.index(nav) < calls.index(confirm) < calls.index('mc2_plan')
    assert 'mc2_source_check' in calls and 'mc2_blind_video' in calls and 'mc2_review' in calls
    assert shared['parsed']['mc2_edge_1']['status'] == 'blocked'
    if mode == 'observe':
        assert any('_view_' in name for name in calls)
        assert shared['plan']['segments'][0]['source_out_s'] > shared['overview'][1]['frame_end_s']
    old_calls = list(calls)
    assert pipeline.execute(output) == result
    assert shared['calls'] == old_calls and shared['renders'] == 1


@pytest.mark.parametrize('mode', ['blocked', 'no_progress'])
def test_semantic_stop_or_no_new_decoder_frame_cannot_loop_or_render(navigation, mode):
    output, auth, shared = navigation
    shared['navigation_mode'] = mode
    result = pipeline.execute(output)
    assert result['status'] == 'stopped' and not result['final_video'] and shared['renders'] == 0
    assert shared['calls'].count('mc2_edge_1_nav_0') == 1
    assert not any('_repair' in name or '_view_' in name for name in shared['calls'])


def test_effective_edge_resume_reuses_confirmed_real_frames_without_new_model_call(navigation):
    from omni_story.library.microclip_v2_boundary import resolve_boundary
    from omni_story.library.microclip import _read
    output, auth, shared = navigation
    result = pipeline.execute(output)
    assert result['final_video']
    original_calls = list(shared['calls'])
    state = microclip_v2_state.SlotMicroclipState(output)
    event = shared['parsed']['mc2_anchors']['events'][0]
    original = _read(shared['inputs']['mc2_input_mc2_edge_1']['lineage_path'])
    catalog = {r['frame_id']: r for r in shared['overview']}
    class NoNewGLM:
        def call(self, *args, **kwargs):
            raise AssertionError('Effective edge recovery must not call the model.')
    value = resolve_boundary(NoNewGLM(), state, Path(auth['execution_directory']), auth['parent'],
        auth['slot'], 1, event, 'end', original, shared['parsed']['mc2_edge_1'],
        shared['parsed']['mc2_intent'], catalog, [])
    assert value['confirmed_frame_id'] == shared['selected_id']
    assert value['confirmed_frame_id'] in catalog
    assert value['status'] == 'confirmed'
    assert shared['calls'] == original_calls and shared['renders'] == 1


def test_bound_projection_skips_old_nav_but_requires_new_neighbor_and_output_checks(navigation, monkeypatch):
    from omni_story.library import microclip_v2_boundary_metadata as metadata
    from omni_story.library.state import write_json
    output, auth, shared = navigation
    auth['boundary_metadata_resume'] = {'policy': metadata.POLICY}
    def projection(root, data, stem):
        if stem != 'mc2_edge_1_nav_1':
            return None
        value = {'event_id': 'e0', 'anchor_frame_id': shared['parsed']['mc2_edge_1']['anchor_frame_id'],
            'obligation_ids': ['o0'], 'action': 'confirm', 'cut_intent': 'semantic_trim',
            'question': '', 'resolution': 'Use observed new state for intentional in-shot ending.',
            'source_start_s': None, 'source_end_s': None, 'confirmed_frame_id': shared['selected_id'],
            'evidence_frame_ids': [shared['selected_id']], 'blocking_questions': [],
            'limitations': ['Synthetic protocol projection; not independent quality evidence.']}
        shared['parsed'][stem] = deepcopy(value)
        return {'id': 'existing_received_repair'}, value
    monkeypatch.setattr(metadata, 'received_projection', projection)
    def finish(self, value):
        path = Path(auth['execution_directory']) / 'result_boundary_metadata_recovered.json'
        write_json(path, value)
        return path
    monkeypatch.setattr(microclip_v2_state.SlotMicroclipState, 'finish', finish)
    result = pipeline.execute(output)
    assert result['final_video'] and shared['renders'] == 1
    assert 'mc2_edge_1_nav_1' not in shared['calls']
    assert 'mc2_edge_1_nav_1_repair' not in shared['calls']
    assert 'mc2_edge_1_confirm_1' in shared['calls']
    assert all(stage in shared['calls'] for stage in ('mc2_plan', 'mc2_source_check', 'mc2_blind_video', 'mc2_review'))
    assert result['model_quality_gate_passed'] is False and result['semantic_complete'] is False
    before = list(shared['calls'])
    assert pipeline.execute(output) == result and shared['calls'] == before
