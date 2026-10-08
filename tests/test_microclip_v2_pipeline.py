"""Synthetic integration: separated slot information remains legally reachable."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import microclip_v2 as pipeline
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Requires local FFmpeg')


def _after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


@pytest.fixture
def task(tmp_path, monkeypatch):
    from omni_story.library import microclip_v2_state
    parent = tmp_path / 'synthetic.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=30:d=4',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(parent)], check=True, capture_output=True)
    output = tmp_path / 'task'
    output.mkdir()
    knowledge = output / 'knowledge.md'
    knowledge.write_text('Preserve independently verified information.', encoding='utf-8')
    slot = {'slot_id': 's1', 'start_s': 0, 'end_s': 4, 'intended_takeaway': 'OLD_SLOT_REQUIREMENT',
            'entry_state': 'ENTRY_REQUIREMENT', 'exit_state': 'EXIT_REQUIREMENT'}
    auth = {'policy': 'synthetic_v2', 'execution_directory': str(output / 'v2'),
            'parent': {'path': str(parent), 'sha256': sha256_file(parent)}, 'slot': slot,
            'knowledge_path': str(knowledge), 'baseline_request_count': 218}
    shared = {'calls': [], 'inputs': {}, 'plan': None, 'catalog': {}, 'mode': 'limited', 'renders': 0}

    class State:
        def __init__(self, root):
            self.output, self.authorization = Path(root), auth
            self.data = {'calls': []}

        def _reload(self):
            pass

        def set_artifact(self, key, value):
            if key in shared['inputs']:
                assert shared['inputs'][key] == value
            shared['inputs'][key] = deepcopy(value)

        def claim_render(self, plan, source):
            shared['renders'] += 1
            assert shared['renders'] == 1
            shared['plan'] = deepcopy(plan)
            return Path(auth['execution_directory']) / 'render'

        def finish(self, value):
            path = Path(auth['execution_directory']) / 'result.json'
            write_json(path, value)
            return path

    class GLM:
        def __init__(self, state):
            self.state = state

        def call(self, name, prompt, media, validator, *, image=False, scope):
            shared['calls'].append(name)
            if name in ('mc2_overview', 'mc2_motion', 'mc2_blind_video') or name.startswith('mc2_blind_page_'):
                assert 'OLD_SLOT_REQUIREMENT' not in prompt
                assert 'ENTRY_REQUIREMENT' not in prompt
            if image:
                descriptor = shared['inputs']['mc2_input_' + name]
                rows = json.loads(Path(descriptor['lineage_path']).read_text(encoding='utf-8'))['frames']
                shared['catalog'].update({r['frame_id']: r for r in rows})
                value = {'frames': [{'frame_id': r['frame_id'], 'visible_action_or_state': 'Colored pattern.',
                                    'visible_text': ''} for r in rows], 'visible_event': 'Changing geometry.',
                         'missing_information': [], 'limitations': []}
                if name == 'mc2_overview':
                    shared['overview'] = rows
                elif name == 'mc2_intent':
                    assert 'OLD_SLOT_REQUIREMENT' in prompt
                    rows = shared['overview']
                    value = {'status': 'ready', 'original_claim_checks': [{'claim_id': key,
                        'original_text': slot[key], 'verdict': 'supported', 'reason': 'Synthetic evidence.',
                        'evidence_frame_ids': [rows[0]['frame_id']]} for key in
                        ('intended_takeaway', 'entry_state', 'exit_state')], 'obligations': [{'obligation_id': f'o{i}',
                        'description': 'Synthetic separated information.', 'support': 'visual',
                        'original_claim_ids': [list(x) for x in [('intended_takeaway', 'entry_state'), (), ('exit_state',)]][i],
                        'evidence_frame_ids': [rows[i*2]['frame_id']]} for i in range(3)],
                        'search_regions': [{'region_id': f'r{i}', 'start_frame_id': rows[i*2]['frame_id'],
                        'end_frame_id': rows[i*2+1]['frame_id'], 'question': 'Inspect this separate region.',
                        'obligation_ids': [f'o{i}']} for i in range(3)], 'blocking_questions': [], 'limitations': []}
                elif name == 'mc2_anchors':
                    rows = shared['overview']
                    value = {'status': 'ready', 'events': [{'event_id': f'e{i}', 'obligation_ids': [f'o{i}'],
                        'start_frame_id': rows[i*2]['frame_id'], 'end_frame_id': rows[i*2+1]['frame_id'],
                        'reason': 'Separated necessary state.'} for i in range(3)],
                        'resolved_conflicts': [], 'blocking_questions': [], 'limitations': []}
                elif name.startswith('mc2_edge_'):
                    candidate = descriptor['anchor_frame_id']
                    assert candidate in {r['frame_id'] for r in rows}
                    value.update({'anchor_frame_id': candidate, 'confirmed_frame_id': candidate,
                                  'status': 'confirmed', 'blocking_questions': [], 'reason': 'Boundary visible.'})
                    if shared['mode'] == 'blocked_edge':
                        value.update(status='blocked', blocking_questions=['Necessary outcome missing.'])
                elif name.startswith('mc2_blind_page_'):
                    value['observation_status'] = 'complete'
            else:
                descriptor = shared['inputs']['mc2_input_' + name]
                lineage = json.loads(Path(descriptor['lineage_path']).read_text(encoding='utf-8'))
                assert lineage['audio_present'] is False
                if name.startswith('mc2_slice_'):
                    assert 'OLD_SLOT_REQUIREMENT' not in prompt and 'ENTRY_REQUIREMENT' not in prompt
                duration = scope['source_end_s'] - scope['source_start_s']
                value = {'visible_meaning': 'Changing geometry.', 'events': [{'start_s': 0, 'end_s': duration,
                    'visible_content': 'Geometry changes.', 'text_evidence': ''}], 'observation_status': 'complete',
                    'limitations': []}
                if name == 'mc2_plan':
                    rows = shared['overview']
                    value = {'shots': [], 'obligation_coverage': [], 'preserved_visible_meaning': 'All separated states.',
                             'omitted_content': ['Repeated geometric patterns.'], 'limitations': []}
                    for i in range(3):
                        a, b = rows[i*2], rows[i*2+1]
                        value['shots'].append({'start_frame_id': a['frame_id'], 'end_frame_id': b['frame_id'],
                            'speed': 1.5, 'hold_s': 0, 'reason': 'Condense repeated motion.',
                            'visible_change': 'Geometric state.', 'obligation_ids': [f'o{i}']})
                        exposure = (b['frame_end_s'] - a['source_time_s']) / 1.5
                        value['obligation_coverage'].append({'obligation_id': f'o{i}', 'shot_indices': [i],
                            'source_evidence_frame_ids': [a['frame_id']], 'exposure_s': exposure,
                            'necessary_exposure_s': min(.2, exposure), 'reason': 'Synthetic allocation only.'})
                elif name == 'mc2_source_check':
                    value = {'status': 'ready', 'obligation_checks': [{'obligation_id': f'o{i}',
                        'verdict': 'supported', 'reason': 'Selected evidence present.'} for i in range(3)],
                        'blocking_questions': [], 'limitations': []}
                    if shared['mode'] == 'unsupported_source':
                        value['obligation_checks'][0]['verdict'] = 'unsupported'
                elif name == 'mc2_blind_video' and shared['mode'] == 'limited':
                    value.update(observation_status='limited', limitations=['Only initial state observed.'])
                elif name == 'mc2_review':
                    assert '独立密帧盲读：' in prompt
                    value = {'status': 'pass', 'key_moment_selection': 'pass', 'economy': 'pass', 'readability': 'pass',
                        'obligation_checks': [{'obligation_id': f'o{i}', 'verdict': 'pass', 'output_start_s': 0,
                            'output_end_s': duration, 'reason': 'Synthetic state visible.'} for i in range(3)],
                        'reason': 'Synthetic plumbing only.', 'limitations': []}
            validator(value)
            return value

    monkeypatch.setattr(microclip_v2_state, 'SlotMicroclipState', State)
    monkeypatch.setattr(pipeline, 'CodexMCP', GLM)
    return output, auth, shared


def test_full_slot_boundaries_multiple_regions_and_incomplete_blind_cannot_pass(task):
    output, auth, shared = task
    result = pipeline.execute(output)
    assert result['status'] == 'candidate_with_limitations'
    assert result['model_quality_gate_passed'] is False
    assert result['observation_complete'] is False
    assert result['semantic_complete'] is True
    assert result['events'] == 3 and Path(result['final_video']).is_file()
    assert sha256_file(result['final_video']) == result['final_sha256']
    assert shared['plan']['segments'][0]['source_in_s'] == 0
    assert shared['plan']['segments'][-1]['source_out_s'] == 4
    assert shared['renders'] == 1
    original_calls = list(shared['calls'])
    assert pipeline.execute(output) == result and shared['calls'] == original_calls


@pytest.mark.parametrize('mode', ['blocked_edge', 'unsupported_source'])
def test_semantic_missing_evidence_stops_without_format_retry_or_render(task, mode):
    output, auth, shared = task
    shared['mode'] = mode
    result = pipeline.execute(output)
    assert result['status'] == 'stopped' and result['final_video'] is None
    assert shared['renders'] == 0
    assert not any('repair' in name for name in shared['calls'])
