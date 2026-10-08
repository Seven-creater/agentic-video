"""A synthetic local microcut with real PTS grids, proxies and FFmpeg output.

Only state/provider boundaries are fake. No movie footage or real model calls
are used, and these tests cannot establish GLM editing quality.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import microclip as pipeline, microclip_frames, microclip_state
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Synthetic integration requires FFmpeg and FFprobe')


def _after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


@pytest.fixture
def microcut(tmp_path, monkeypatch):
    parent = tmp_path / 'synthetic_geometry.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=96x64:r=30:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(parent)],
                   check=True, capture_output=True)
    output = tmp_path / 'synthetic_task'
    output.mkdir()
    execution = output / 'microclip'
    knowledge = output / 'generic_knowledge.md'
    knowledge.write_text('Observe new visual evidence. Use actual frame IDs. Preserve visible changes.', encoding='utf-8')
    authority = {'policy': 'synthetic_microclip', 'execution_directory': str(execution),
                 'knowledge_path': str(knowledge), 'parent': {'path': str(parent), 'sha256': sha256_file(parent)},
                 'slot': {'slot_id': 's1', 'start_s': 0, 'end_s': 4}}
    shared = {'calls': [{'name': 'historical', 'status': 'received'} for _ in range(207)],
              'new_calls': [], 'inputs': {}, 'renders': [], 'plan': None, 'frames': [],
              'edges': [], 'mode': 'success', 'protected': {parent: parent.read_bytes(), knowledge: knowledge.read_bytes()}}

    class FakeState:
        def __init__(self, root):
            self.output = Path(root)
            self.authorization = authority
            self.data = {'calls': shared['calls']}

        def _reload(self):
            self.data = {'calls': shared['calls']}

        def set_artifact(self, name, descriptor):
            shared['inputs'][name] = deepcopy(descriptor)

        def claim_render(self, plan, source):
            assert not shared['renders'], 'Only one local render is permitted'
            assert source['sha256'] == authority['parent']['sha256']
            shared['renders'].append(str(execution / 'render'))
            shared['plan'] = deepcopy(plan)
            return execution / 'render'

        def finish(self, value):
            assert all(path.read_bytes() == original for path, original in shared['protected'].items())
            path = execution / 'result.json'
            if path.exists():
                assert json.loads(path.read_text(encoding='utf-8')) == value
            else:
                write_json(path, value)
            return path

    class FakeGLM:
        def __init__(self, state):
            self.state = state

        def call(self, name, prompt, media, validator, *, scope, image=False):
            assert Path(media).is_file()
            assert 0 <= scope['source_start_s'] < scope['source_end_s']
            if shared['mode'] == 'unknown':
                call = {'name': name, 'status': 'uncertain'}
                shared['new_calls'].append(call)
                shared['calls'].append(call)
                raise LibraryStopped('synthetic_paid_reply_unknown_no_replay')
            if name.startswith('mc_observe_'):
                assert image and scope['kind'] == 'sparse_contact_sheet'
                rows = _after(prompt, '\n当前附件帧映射：')
                shared['frames'].append(rows)
                index = int(name.rsplit('_', 1)[1])
                next_view = {'action': 'ready', 'reason': 'Synthetic geometric change is visible.'}
                if index == 0:
                    a, b = rows[1], rows[-2]
                    if shared['mode'] == 'unfocused':
                        a, b = rows[0], rows[-1]
                    elif shared['mode'] == 'no_new_frames':
                        a = b = rows[1]
                    next_view = {'action': 'zoom', 'start_frame_id': a['frame_id'], 'end_frame_id': b['frame_id'],
                                 'question': 'Inspect how the synthetic geometric shape changes.',
                                 'reason': 'Narrow the observation to inspect a visible change.'}
                value = {'frames': [{'frame_id': row['frame_id'],
                                    'visible_action_or_state': 'Colored geometric pattern.', 'visible_text': ''}
                                   for row in rows], 'visible_event': 'A synthetic pattern changes over time.',
                         'missing_information': [] if index else ['The local geometric change needs inspection.'],
                         'next_observation': next_view, 'limitations': ['Synthetic imagery has no narrative.']}
            elif name in {'mc_motion', 'mc_blind'}:
                assert not image
                assert '原速事实：' not in prompt and '模型微剪：' not in prompt
                assert 'intended_takeaway' not in prompt and 'Synthetic changing geometry.' not in prompt
                duration = scope['source_end_s'] - scope['source_start_s']
                value = {'visible_meaning': 'Moving colored geometry.', 'events': [
                    {'start_s': 0, 'end_s': duration, 'visible_content': 'Colored geometry visibly moves.',
                     'text_evidence': ''}], 'limitations': ['No story inference in this synthetic fixture.']}
            elif name == 'mc_anchors':
                assert not image
                rows = shared['frames'][-1]
                value = {'start_frame_id': rows[1]['frame_id'], 'peak_frame_id': rows[3]['frame_id'],
                         'end_frame_id': rows[-2]['frame_id'], 'reason': 'Inspect real synthetic boundaries.',
                         'expected_visible_change': 'Geometric motion changes.', 'limitations': []}
            elif name.startswith('mc_edges_'):
                assert image and scope['kind'] == 'sparse_contact_sheet'
                rows = _after(prompt, '\n当前帧映射：')
                shared['edges'].append(rows)
                action = 'stop' if shared['mode'] == 'edge_stop' else 'ready'
                value = {'frames': [{'frame_id': row['frame_id'],
                                    'visible_action_or_state': 'Synthetic geometric boundary.', 'visible_text': ''}
                                   for row in rows], 'visible_event': 'Motion near a proposed synthetic boundary.',
                         'missing_information': [], 'next_observation': {
                             'action': action, 'reason': 'The synthetic boundary is unresolved.' if action == 'stop'
                             else 'The synthetic boundary is observable.'}, 'limitations': []}
            elif name == 'mc_plan':
                rows = shared['frames'][-1]
                a = shared['frames'][0][0] if shared['mode'] == 'unconfirmed_cut' else rows[1]
                value = {'shots': [{'start_frame_id': a['frame_id'], 'end_frame_id': rows[-2]['frame_id'],
                                    'speed': 2, 'hold_s': 0, 'reason': 'Condense visible synthetic motion.',
                                    'visible_change': 'Geometric motion remains visible.'}],
                         'preserved_visible_meaning': 'Visible geometric movement.',
                         'omitted_content': ['Repeated synthetic motion.'], 'limitations': ['Synthetic clip only.']}
            elif name == 'mc_review':
                assert '实际剪后盲读：' in prompt and '原速事实：' in prompt
                value = {'status': 'partial', 'key_moment_selection': 'partial', 'economy': 'partial',
                         'readability': 'partial', 'reason': 'Synthetic geometry is not a narrative quality test.',
                         'limitations': ['This cannot demonstrate specialist editing quality.']}
            else:
                raise AssertionError('Unexpected model stage: ' + name)
            call = {'name': name, 'status': 'received'}
            shared['new_calls'].append(call)
            shared['calls'].append(call)
            validator(value)
            return value

    monkeypatch.setattr(microclip_state, 'MicroclipState', FakeState)
    monkeypatch.setattr(pipeline, 'CodexMCP', FakeGLM)
    return output, execution, authority, shared


def test_real_grids_microcut_blind_review_and_finished_cache_make_zero_new_calls(microcut):
    output, execution, authority, shared = microcut
    result = pipeline.execute(output)
    assert result['status'] == 'candidate_with_limitations'
    assert result['model_quality_gate_passed'] is False
    assert Path(result['final_video']).is_file() and sha256_file(result['final_video']) == result['final_sha256']
    assert [call['name'] for call in shared['new_calls']] == [
        'mc_observe_0', 'mc_observe_1', 'mc_motion', 'mc_anchors',
        'mc_edges_0', 'mc_edges_1', 'mc_edges_2', 'mc_plan', 'mc_blind', 'mc_review']
    first, second = shared['frames']
    assert len(first) == len(second) == 6
    assert {row['frame_id'] for row in first}.isdisjoint(row['frame_id'] for row in second)
    assert second[0]['source_time_s'] >= first[1]['source_time_s']
    assert second[-1]['frame_end_s'] <= first[-2]['frame_end_s']
    selected = shared['plan']['segments'][0]
    assert selected['source_in_s'] == second[1]['source_time_s']
    assert selected['source_out_s'] == second[-2]['frame_end_s']
    assert selected['source_out_s'] > second[-2]['source_time_s'], 'End frame must actually be retained'
    expected = (selected['source_out_s'] - selected['source_in_s']) / selected['speed']
    assert result['measured_duration_s'] == pytest.approx(expected, abs=.04)
    all_seen = [*first, *second, *(row for edge in shared['edges'] for row in edge)]
    assert len({row['frame_id'] for row in all_seen}) == len(all_seen), 'Every new grid must add distinct source frames'
    assert result['observed_frames'] == len(all_seen) and len(shared['renders']) == 1
    catalog = microclip_frames.frame_catalog(authority['parent']['path'])['frames']
    positions = {row['frame_id']: row['decode_frame_index'] for row in catalog}
    for edge in shared['edges']:
        indices = sorted(positions[row['frame_id']] for row in edge)
        assert len(edge) == 6 and indices[-1] - indices[0] <= 6, 'Edges are genuine adjacent presentation frames'
    prior_calls, prior_renders = deepcopy(shared['new_calls']), deepcopy(shared['renders'])
    assert pipeline.execute(output) == result
    assert shared['new_calls'] == prior_calls and shared['renders'] == prior_renders


@pytest.mark.parametrize(('mode', 'reason'), [('unfocused', 'no_progress:observation_did_not_focus'),
                                           ('no_new_frames', 'no_new_frames_in_interval')])
def test_no_focus_or_no_new_frames_stops_without_motion_plan_render_or_loop(microcut, mode, reason):
    output, _, _, shared = microcut
    shared['mode'] = mode
    result = pipeline.execute(output)
    assert result['status'] == 'stopped' and reason in result['error']
    assert len(shared['new_calls']) == 1 and shared['renders'] == [] and result['final_video'] is None
    assert pipeline.execute(output) == result and len(shared['new_calls']) == 1


def test_unknown_paid_reply_is_preserved_and_finished_stop_does_not_replay(microcut):
    output, _, _, shared = microcut
    shared['mode'] = 'unknown'
    result = pipeline.execute(output)
    assert result['status'] == 'stopped' and 'reply_unknown_no_replay' in result['error']
    assert shared['new_calls'] == [{'name': 'mc_observe_0', 'status': 'uncertain'}]
    assert shared['renders'] == []
    assert pipeline.execute(output) == result
    assert shared['new_calls'] == [{'name': 'mc_observe_0', 'status': 'uncertain'}]


@pytest.mark.parametrize(('mode', 'reason'), [('edge_stop', 'boundary_not_confirmed'),
                                           ('unconfirmed_cut', 'boundaries_require_observed_neighbor_frames')])
def test_unconfirmed_boundary_stops_without_render_or_automatic_replan(microcut, mode, reason):
    output, _, _, shared = microcut
    shared['mode'] = mode
    result = pipeline.execute(output)
    assert result['status'] == 'stopped' and reason in result['error']
    assert shared['renders'] == [] and result['final_video'] is None
    assert not any(c['name'] in {'mc_blind', 'mc_review'} for c in shared['new_calls'])
    calls = deepcopy(shared['new_calls'])
    assert pipeline.execute(output) == result and shared['new_calls'] == calls
