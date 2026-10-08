from copy import deepcopy
import pytest

from omni_story.library.microclip_contracts import compile_plan, validate_plan, validate_anchors, validate_observation


SOURCE = {'source_id': 'src_' + 'a' * 16, 'sha256': 'a' * 64}
SLOT = {'slot_id': 's1', 'start_s': 10.0, 'end_s': 11.0}
FRAMES = {f'f{i}': {'frame_id': f'f{i}', 'source_sha256': SOURCE['sha256'],
          'source_time_s': t, 'frame_end_s': end} for i, (t, end) in enumerate(
              [(10, 10.04), (10.04, 10.13), (10.13, 10.2), (10.9, 11.0)])}


def proposal():
    return {'shots': [{'start_frame_id': 'f1', 'end_frame_id': 'f2', 'speed': .5,
             'hold_s': .3, 'reason': 'Keep a visible change', 'visible_change': 'A visible state changes'}],
            'preserved_visible_meaning': 'A state change', 'omitted_content': ['Redundant wait'], 'limitations': []}


def test_variable_pts_end_is_next_frame_not_guessed_fps():
    segment = compile_plan(SOURCE, SLOT, proposal(), FRAMES)['segments'][0]
    assert (segment['source_in_s'], segment['source_out_s']) == (10.04, 10.2)
    assert segment['window_id'] == SLOT['slot_id']
    assert segment['speed'] == .5 and segment['freeze_tail_s'] == .3


def test_sparse_boundary_cannot_return_after_neighbor_confirmation():
    with pytest.raises(ValueError, match='boundaries_require'):
        validate_plan(SOURCE, SLOT, proposal(), FRAMES, {'f0', 'f1', 'f3'})


@pytest.mark.parametrize('change', ['foreign_sha', 'unknown_end', 'past_slot', 'backward'])
def test_reject_unexecutable_boundaries(change):
    frames, value = deepcopy(FRAMES), proposal()
    if change == 'foreign_sha': frames['f1']['source_sha256'] = 'b' * 64
    if change == 'unknown_end': frames['f2']['frame_end_s'] = None
    if change == 'past_slot': frames['f2']['frame_end_s'] = 12
    if change == 'backward': value['shots'].append(deepcopy(value['shots'][0]))
    with pytest.raises(ValueError): compile_plan(SOURCE, SLOT, value, frames)


def test_anchors_must_be_distinct_ordered_actual_frames():
    value = {'start_frame_id': 'f0', 'peak_frame_id': 'f1', 'end_frame_id': 'f3',
             'reason': 'Observe change', 'expected_visible_change': 'Visible result', 'limitations': []}
    assert validate_anchors(value, FRAMES) == value
    value['peak_frame_id'] = 'f0'
    with pytest.raises(ValueError): validate_anchors(value, FRAMES)


def test_single_source_frame_speedup_must_survive_output_frame_rounding():
    frames, value = deepcopy(FRAMES), proposal()
    frames['f1']['frame_end_s'] = frames['f1']['source_time_s'] + 1 / 30
    value['shots'][0].update(end_frame_id='f1', speed=2, hold_s=1)
    with pytest.raises(ValueError, match='at_least_one_output_motion_frame'):
        compile_plan(SOURCE, SLOT, value, frames)


def test_partial_grid_description_is_not_a_complete_observation():
    value = {'frames': [{'frame_id': 'f0', 'visible_action_or_state': 'A shape', 'visible_text': ''}],
             'visible_event': 'A visible state', 'missing_information': [], 'limitations': [],
             'next_observation': {'action': 'ready', 'reason': 'Enough evidence'}}
    with pytest.raises(ValueError, match='all_shown_frames_once'):
        validate_observation(value, list(FRAMES.values()), FRAMES)
