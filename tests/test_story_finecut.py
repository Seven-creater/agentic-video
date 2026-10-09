"""Injected finite skill flow: synthetic media and fake MCP, never a cloud call."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import story_finecut as flow
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import LibraryStopped


class State:
    def __init__(self, output):
        self.output = output
        self.data = {'request_count': 35}
        self.artifacts = {}

    def _reload(self):
        pass

    def set_artifact(self, name, value):
        if name in self.artifacts:
            assert self.artifacts[name] == value
        self.artifacts[name] = deepcopy(value)


def instance(tmp_path):
    value = flow.StoryFinecut.__new__(flow.StoryFinecut)
    value.base = tmp_path
    value.auth = {'parent': {'duration_s': 147.0}, 'reference': {'duration_s': 21.933333},
                  'target_duration_s': 21.933333, 'target_tolerance_s': 2.0}
    value.render_source = {'source_id': 'chain_parent', 'path': 'synthetic-parent.mp4',
                           'sha256': 'a' * 64, 'duration_s': 147.0, 'audio_stream_index': None}
    value.observed = {'story': [{'id': 'beat_a'}, {'id': 'beat_b'}]}
    return value


def plan():
    return {'segments': [
        {'segment_id': 'a', 'source_id': 'chain_parent', 'window_id': 'full_parent_observed',
         'source_in_s': 110.0, 'source_out_s': 121.0, 'speed': 1.0, 'freeze_tail_s': 0,
         'look': 'none', 'framing': 'fit', 'contribution_to': ['beat_a'], 'reason': 'visible state'},
        {'segment_id': 'b', 'source_id': 'chain_parent', 'window_id': 'full_parent_observed',
         'source_in_s': 130.0, 'source_out_s': 140.8, 'speed': 1.0, 'freeze_tail_s': .1,
         'look': 'none', 'framing': 'fit', 'contribution_to': ['beat_b'], 'reason': 'visible change'}
    ], 'limitations': []}


def test_plan_uses_dynamic_parent_beyond_77_and_actual_reference_target(tmp_path):
    value = instance(tmp_path)
    value.plan_check(plan())
    too_long = plan()
    too_long['segments'][0]['source_out_s'] = 140
    with pytest.raises(ValueError, match='target_close_to_reference'):
        value.plan_check(too_long)
    too_short = plan()
    too_short['segments'][1]['source_out_s'] = 131
    with pytest.raises(ValueError, match='target_close_to_reference'):
        value.plan_check(too_short)


@pytest.mark.parametrize('change,expected', [
    ('caption', 'no_new_explanatory'), ('missing_contribution', 'all_story_contributions'),
    ('hold', 'finite_number'), ('source', 'fixed_parent_source'), ('music', 'preserve_original_music')])
def test_invalid_creative_choices_are_rejected_not_rewritten(tmp_path, change, expected):
    value, proposed = instance(tmp_path), plan()
    if change == 'caption': proposed['segments'][0]['caption'] = {'text': 'invented answer'}
    elif change == 'missing_contribution': proposed['segments'][1]['contribution_to'] = ['beat_a']
    elif change == 'hold':
        proposed['segments'][1]['freeze_tail_s'] = 2.2
        proposed['segments'][1]['source_out_s'] = 138.7
    elif change == 'source': proposed['segments'][0]['source_id'] = 'unbound'
    else: proposed['segments'][1]['source_out_s'] = 141.2
    original = deepcopy(proposed)
    with pytest.raises(ValueError, match=expected):
        value.plan_check(proposed)
    assert proposed == original


@pytest.mark.parametrize('mutation', ['object_uncertainty', 'past_end', 'reverse', 'missing_basis'])
def test_local_facts_require_authentic_scope_and_explicit_uncertainty_type(mutation):
    value = {'facts': [{'start_s': 10.1, 'end_s': 10.5, 'description': 'visible state', 'basis': 'picture'}],
             'uncertainties': []}
    if mutation == 'object_uncertainty': value['uncertainties'] = [{'text': 'unknown'}]
    elif mutation == 'past_end': value['facts'][0]['end_s'] = 12
    elif mutation == 'reverse': value['facts'][0]['end_s'] = 10
    else: value['facts'][0].pop('basis')
    with pytest.raises(ValueError):
        flow.StoryFinecut.facts_check(value, 10, 11)


def test_sparse_facts_use_source_pts_not_local_playback_seconds():
    flow.StoryFinecut.facts_check({'facts': [{'time_s': 10.2, 'description': 'visible shape',
                                            'basis': 'picture'}], 'uncertainties': ['between frames unknown']},
                                 10, 11, sparse=True)
    with pytest.raises(ValueError):
        flow.StoryFinecut.facts_check({'facts': [{'time_s': .2, 'description': 'visible shape',
                                                'basis': 'picture'}], 'uncertainties': []},
                                     10, 11, sparse=True)


def test_revision_changes_only_actual_problem_location(tmp_path):
    value, original = instance(tmp_path), plan()
    compiled = flow.compile_library_plan([value.render_source], {'segments': original['segments'],
        'audio_mode': 'silent'}, fps=30, width=1280, height=720)
    rendered = {'provenance': compiled['segments'], 'duration_s': compiled['duration_s']}
    review = {'target': {'problems': [{'start_s': 1, 'end_s': 3, 'description': 'hard to recognize'}]}}
    revised = deepcopy(original)
    revised['segments'][0].update(source_in_s=110.2, source_out_s=121.2)
    value.revision_check(revised, original, rendered, review)
    revised['segments'][1].update(source_in_s=130.2, source_out_s=141.0)
    with pytest.raises(ValueError, match='revision_changes_unrelated_segment'):
        value.revision_check(revised, original, rendered, review)
    revised = deepcopy(original)
    revised['segments'].pop()
    with pytest.raises(ValueError):
        value.revision_check(revised, original, rendered, review)


def test_rewording_is_not_executed_progress_and_no_third_render(tmp_path):
    value, original = instance(tmp_path), plan()
    rewritten = deepcopy(original)
    rewritten['segments'][0].update(segment_id='new_name', reason='new words only')
    assert flow.trial.editing_fingerprint(original, value.render_source) == \
           flow.trial.editing_fingerprint(rewritten, value.render_source)
    with pytest.raises(LibraryStopped, match='only_one_revision_render'):
        value.render(original, 2)


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is required for authentic synthetic media verification')
    root = tmp_path_factory.mktemp('story_finecut_media')
    result = []
    for name, duration in [('parent', 4), ('reference', 2)]:
        path = root / (name + '.mp4')
        subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
            f'color=red:s=160x90:r=30:d={duration}', '-f', 'lavfi', '-i',
            f'sine=frequency=700:sample_rate=48000:duration={duration}', '-vf',
            "drawbox=x=0:y=0:w=iw:h=ih:color=yellow:t=fill:enable='gte(t,0.75)',"
            "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,2)'",
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'ultrafast',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)],
            check=True, capture_output=True, timeout=30)
        measured = probe_media(path)
        result.append({'path': str(path), 'sha256': sha256_file(path), 'duration_s': measured['duration_s'],
                       'video_stream_index': measured['video_stream_index'], 'audio_stream_index': 1})
    return result


def synthetic_observation(inspect=True):
    return {'story': [{'id': 'red', 'contribution': 'first visible state', 'entry': 'unknown', 'exit': 'red'},
                      {'id': 'blue', 'contribution': 'second visible state', 'entry': 'red', 'exit': 'blue'}],
            'facts': [], 'inspect': [{'target': 'parent', 'start_s': 1.7, 'end_s': 2.3, 'step_s': .1,
                                     'question': 'What visible color changes?'}] if inspect else [],
            'limitations': []}


def synthetic_plan():
    result = {'segments': [], 'limitations': []}
    for key, start, end, hold in [('red', .2, 1.2, 0), ('blue', 2.2, 3.1, .1)]:
        result['segments'].append({'segment_id': key, 'source_id': 'chain_parent',
            'window_id': 'full_parent_observed', 'source_in_s': start, 'source_out_s': end,
            'speed': 1.0, 'freeze_tail_s': hold, 'look': 'none', 'framing': 'fit',
            'contribution_to': [key], 'key_change': 'visible color state', 'reason': 'recognize state',
            'relation_to_next': 'state changes'})
    return result


def review(status='partial', *, target=False, revise=False):
    value = {'apparent_story': 'visible color states', 'status': status,
             'problems': [{'start_s': .2, 'end_s': .6, 'description': 'state too fast'}] if revise else [],
             'limitations': ['picture alone does not establish causality'] if status == 'partial' else []}
    if target:
        value.update(next_action='revise' if revise else 'deliver', revision_reason='adjust state timing',
                     editing_methods_used=[])
    else:
        value['facts'] = [{'start_s': 0, 'end_s': 1, 'description': 'visible red/yellow state'}]
    return value


class FakeMCP:
    def __init__(self, state, *, no_progress=False):
        self.state, self.calls, self.no_progress = state, [], no_progress

    def call(self, name, prompt, path, validator, *, image=False, scope):
        assert name.startswith(flow.PREFIX)
        assert Path(path).is_file()
        self.calls.append({'name': name, 'prompt': prompt, 'path': str(path), 'scope': scope, 'image': image})
        self.state.data['request_count'] += 1
        kind = name.removeprefix(flow.PREFIX)
        if kind == 'observe': value = synthetic_observation()
        elif kind.startswith('inspect_') or kind.startswith('detail_'):
            value = {'facts': [], 'uncertainties': ['unshown frames remain unknown']}
        elif kind in {'plan', 'revise'}:
            value = synthetic_plan()
            if kind == 'revise':
                if self.no_progress: value['segments'][0]['reason'] = 'rewording only'
                else: value['segments'][0].update(source_in_s=.3, source_out_s=1.3)
        elif kind.startswith('blind'): value = review()
        elif kind == 'review': value = review(target=True, revise=True)
        elif kind == 'review_r': value = review('pass', target=True)
        else: pytest.fail('unexpected stage:' + kind)
        validator(value)
        return deepcopy(value)


def test_actual_rough_to_fine_two_renders_then_delivery_and_cache_without_old_trial_auth(media, tmp_path, monkeypatch):
    parent, reference = media
    monkeypatch.setattr(flow.trial.Trial, '__init__', lambda *a: pytest.fail('old Trial constructor used'))
    monkeypatch.setattr(flow.trial, 'get_auth', lambda *a: pytest.fail('old trial authorization read'))
    state = State(tmp_path)
    mcp = FakeMCP(state)
    context = {'reference': {'reference_sha256': reference['sha256'], 'theme': 'SECRET_REFERENCE_ANSWER'},
               'editing_reference': {'SECRET_EDL_ANSWER': 'not for blind context'},
               'evidence_limit': 'known received seed only', 'source_call_id': 'known_060'}
    result = flow.execute_finecut(state, mcp, context, parent, reference, tmp_path / 'fine')
    assert result['duration_s'] == pytest.approx(2, abs=1 / 30)
    assert result['parent_source_sha256'] == parent['sha256']
    assert result['selected_render']['provenance'][0]['source_in_s'] == .3
    assert result['review']['blind']['status'] == 'partial'
    assert result['review']['target']['status'] == 'pass'
    assert result['joint_quality_gate'] is False
    assert result['joint_quality_gate_passed'] is False
    assert result['status'] == 'completed_with_model_review_limits'
    assert state.data['request_count'] == 44 and result['new_requests'] == 9
    names = [row['name'].removeprefix(flow.PREFIX) for row in mcp.calls]
    assert names == ['observe', 'inspect_0', 'detail_0_0', 'plan', 'blind', 'review', 'revise', 'blind_r', 'review_r']
    for call in mcp.calls:
        if call['name'].removeprefix(flow.PREFIX).startswith('blind'):
            assert 'SECRET_REFERENCE_ANSWER' not in call['prompt']
            assert 'SECRET_EDL_ANSWER' not in call['prompt']
    metadata = probe_media(result['final_video'])
    video = next(row for row in metadata['streams'] if row['codec_type'] == 'video')
    assert int(video['nb_frames']) == 60
    assert any(row['codec_type'] == 'audio' for row in metadata['streams'])
    assert sha256_file(result['final_video']) == result['final_sha256']
    assert len(list((tmp_path / 'fine').glob('render_*/render_result.json'))) == 2
    returned = flow.execute_finecut(state, mcp, context, parent, reference, tmp_path / 'fine')
    assert returned == result and len(mcp.calls) == 9
    # Delivery music stays normal speed even though the visual ranges were selected separately.
    import array
    raw = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', result['final_video'],
        '-ac', '1', '-ar', '48000', '-f', 's16le', '-'], check=True, capture_output=True, timeout=30).stdout
    samples = array.array('h', raw)[4800:48000]
    hz = sum(a <= 0 < b for a, b in zip(samples, samples[1:])) / (len(samples) / 48000)
    assert hz == pytest.approx(700, abs=8)


def test_no_editing_progress_delivers_existing_candidate_without_repeated_render(media, tmp_path):
    parent, reference = media
    state = State(tmp_path)
    mcp = FakeMCP(state, no_progress=True)
    result = flow.execute_finecut(state, mcp,
        {'reference': {'reference_sha256': reference['sha256']}}, parent, reference, tmp_path / 'fine')
    assert result['selected_render']['provenance'][0]['source_in_s'] == .2
    assert len(list((tmp_path / 'fine').glob('render_*/render_result.json'))) == 1
    assert flow.PREFIX + 'no_progress' in state.artifacts
    assert len(mcp.calls) == 7


@pytest.mark.parametrize('changed', ['sha', 'duration', 'video_stream', 'reference_context', 'reference_audio'])
def test_constructor_rejects_changed_actual_input_before_model_call(media, tmp_path, changed):
    parent, reference = deepcopy(media)
    context = {'reference': {'reference_sha256': reference['sha256']}}
    if changed == 'sha': parent['sha256'] = '0' * 64
    elif changed == 'duration': parent['duration_s'] += .2
    elif changed == 'video_stream': parent['video_stream_index'] = 9
    elif changed == 'reference_context': context['reference']['reference_sha256'] = '0' * 64
    else: reference['audio_stream_index'] = 0
    state = State(tmp_path)
    mcp = FakeMCP(state)
    with pytest.raises(LibraryStopped):
        flow.execute_finecut(state, mcp, context, parent, reference, tmp_path / 'fine')
    assert mcp.calls == []


def test_no_new_stage_directory_outside_injected_task(media, tmp_path):
    parent, reference = media
    state = State(tmp_path / 'task')
    with pytest.raises(LibraryStopped, match='stage_directory_outside_task'):
        flow.execute_finecut(state, FakeMCP(state),
            {'reference': {'reference_sha256': reference['sha256']}}, parent, reference, tmp_path / 'outside')
