"""Synthetic parent-timeline finishing with real media preparation and rendering.

Only state/provider boundaries are fake. These tests make no model requests,
touch no real movie run, and do not establish editing quality.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from omni_story.library import parent_cut_pipeline as pipeline, parent_cut_state
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import write_json


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Synthetic integration requires FFmpeg and FFprobe')


def _json_after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


@pytest.fixture
def finishing(tmp_path, monkeypatch):
    parent = tmp_path / 'synthetic_parent.mp4'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=96x64:r=30:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(parent)],
                   check=True, capture_output=True)
    output = tmp_path / 'synthetic_task'
    output.mkdir()
    execution = output / 'parent_cut'
    knowledge = output / 'generic_knowledge.md'
    knowledge.write_text('Select visible key moments; retain legible outcomes.', encoding='utf-8')
    parent_sha = sha256_file(parent)
    outlines = {}
    for number in (0, 3):
        path = output / f'outline_{number}.json'
        write_json(path, {'parent_sha256': parent_sha, 'slots': [
            {'slot_id': 's1', 'start_s': 0, 'end_s': 2, 'intended_takeaway': 'Synthetic changing geometry.'},
            {'slot_id': 's2', 'start_s': 2, 'end_s': 4, 'intended_takeaway': 'Synthetic continuing geometry.'}]})
        outlines[str(number)] = str(path)
    authority = {'execution_directory': str(execution), 'outline_paths': outlines,
                 'knowledge_path': str(knowledge), 'parents': [
                     {'round': n, 'path': str(parent), 'sha256': parent_sha, 'duration_s': 4}
                     for n in (0, 3)]}
    protected = {p: p.read_bytes() for p in (parent, knowledge, *map(Path, outlines.values()))}
    shared = {'calls': [], 'renders': [], 'plans': {}, 'barrier_hits': [], 'fail_parent': None,
              'protected_checks': 0}
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    class FakeState:
        def __init__(self, root):
            self.output = Path(root)
            self.authorization = authority
            self.data = {'calls': shared['calls']}

        def _reload(self):
            self.data = {'calls': shared['calls']}

        def claim_render(self, number, plan):
            with lock:
                assert number not in shared['renders'], 'A parent was rendered twice'
                shared['renders'].append(number)
                shared['plans'][number] = deepcopy(plan)
            return execution / f'render_{number}' / 'render'

        def finish(self, number, value):
            path = execution / f'render_{number}' / 'result.json'
            if path.exists():
                assert json.loads(path.read_text(encoding='utf-8')) == value
            else:
                write_json(path, value)
            return path

        def assert_protected(self):
            assert all(p.read_bytes() == original for p, original in protected.items())
            shared['protected_checks'] += 1

    class FakeGLM:
        def __init__(self, state):
            self.state = state
            self.first_call = True

        def call(self, name, prompt, media, validator, *, scope):
            number = int(name.split('_')[1])
            assert Path(media).is_file()
            assert 0 <= scope['source_start_s'] < scope['source_end_s']
            if self.first_call:
                self.first_call = False
                with lock:
                    shared['barrier_hits'].append(number)
                barrier.wait(timeout=30)
            if '_slot_' in name:
                assert scope['source_sha256'] == parent_sha
                slot = _json_after(prompt, '原模型段落导航：')
                assert probe_media(media)['duration_s'] == pytest.approx(2, abs=.04)
                if shared['fail_parent'] == number:
                    # Model protocol owns its sole repair; execution never
                    # starts a fresh creative round after both known failures.
                    for suffix in ('', '_repair'):
                        with lock:
                            shared['calls'].append({'name': name + suffix, 'status': 'received'})
                    raise ValueError('synthetic_known_protocol_failure_after_sole_repair')
                value = {'slot_id': slot['slot_id'], 'shots': [
                    {'in_s': .25, 'out_s': .75, 'speed': .5, 'hold_s': 0,
                     'reason': 'Slow visible geometry for inspection.', 'visible_content': 'Colored geometry.'},
                    {'in_s': 1, 'out_s': 1.8, 'speed': 2, 'hold_s': .1,
                     'reason': 'Condense continuing motion.', 'visible_content': 'Changing geometry.'}],
                    'preserved_meaning': 'Synthetic geometry remains visible.', 'limitations': []}
            else:
                actual = _json_after(prompt, '\n')
                assert scope['source_sha256'] == actual['video_sha256']
                value = {'video_sha256': actual['video_sha256'], 'observed_meaning': 'Synthetic geometric motion.',
                         'status': 'partial', 'slots': [
                             {'slot_id': s['slot_id'], 'status': 'partial',
                              'readability': 'Synthetic fixture is not a narrative evaluation.',
                              'redundancy': 'Visible motion is condensed.', 'evidence': [
                                  {'start_s': s['start_s'], 'end_s': s['end_s'],
                                   'visible_content': 'Synthetic colored geometry.'}]}
                             for s in actual['slot_intervals']],
                         'limitations': ['Synthetic partial review; no quality claim.']}
            result = validator(value)
            with lock:
                shared['calls'].append({'name': name, 'status': 'received'})
            return result

    monkeypatch.setattr(parent_cut_state, 'ParentCutState', FakeState)
    monkeypatch.setattr(pipeline, 'CodexMCP', FakeGLM)
    return output, execution, parent, parent_sha, shared, authority


def test_parallel_finishing_renders_actual_candidates_and_cache_makes_zero_calls(finishing):
    output, execution, parent, original_sha, shared, _ = finishing
    result = pipeline.execute(output)
    assert sorted(shared['barrier_hits']) == sorted(shared['renders']) == [0, 3]
    assert len(shared['calls']) == 8
    assert shared['protected_checks'] == 1 and sha256_file(parent) == original_sha
    for candidate in result['results']:
        assert candidate['status'] == 'candidate_with_limitations'
        assert candidate['model_quality_gate_passed'] is False
        assert Path(candidate['final_video']).is_file()
        assert sha256_file(candidate['final_video']) == candidate['final_sha256']
        assert candidate['measured_duration_s'] == pytest.approx(3, abs=.04)
        number = int(candidate['baseline_id'].split('_')[1])
        plan = shared['plans'][number]
        assert [s['source_in_s'] for s in plan['segments']] == [.25, 1, 2.25, 3]
        assert [s['speed'] for s in plan['segments']] == [.5, 2, .5, 2]
        assert {s['source_id'] for s in plan['segments']} == {'src_' + original_sha[:16]}
        provenance = json.loads((execution / candidate['baseline_id'] / 'render/render_result.json').read_text())
        assert len(provenance['provenance']) == 4
    prior_calls = deepcopy(shared['calls'])
    prior_renders = list(shared['renders'])
    assert pipeline.execute(output) == result
    assert shared['calls'] == prior_calls and shared['renders'] == prior_renders
    assert shared['protected_checks'] == 2


def test_known_one_parent_protocol_failure_does_not_cancel_other_lane(finishing):
    output, _, _, _, shared, _ = finishing
    shared['fail_parent'] = 3
    result = pipeline.execute(output)
    good, stopped = result['results']
    assert good['baseline_id'] == 'render_0' and Path(good['final_video']).is_file()
    assert stopped['baseline_id'] == 'render_3' and stopped['status'] == 'stopped'
    assert 'sole_repair' in stopped['error'] and not stopped.get('final_video')
    assert shared['renders'] == [0]
    failed = [c['name'] for c in shared['calls'] if c['name'].startswith('pc_3_')]
    assert len(failed) == 2 and failed[1] == failed[0] + '_repair'
    before = deepcopy(shared['calls'])
    assert pipeline.execute(output) == result and shared['calls'] == before


def test_changed_parent_sha_stops_before_planning_or_render(finishing):
    output, _, _, _, shared, authority = finishing
    for parent in authority['parents']:
        parent['sha256'] = 'f' * 64
    result = pipeline.execute(output)
    assert all(v['status'] == 'stopped' and v['error'] == 'parent_cut:actual_parent_changed'
               for v in result['results'])
    assert shared['calls'] == shared['renders'] == shared['barrier_hits'] == []
