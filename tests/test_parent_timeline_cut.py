import copy

import pytest

from omni_story.library.parent_timeline_cut import (
    build_plan, review_prompt, slot_prompt, validate_review, validate_slot_cut,
)
from omni_story.library.render import compile_library_plan


SHA = 'a' * 64
SOURCE = {'source_id': 'src_' + SHA[:16], 'sha256': SHA, 'path': '/actual/parent.mp4',
          'duration_s': 10, 'audio_stream_index': 1}
OUTLINE = {'parent_sha256': SHA, 'slots': [
    {'slot_id': 's1', 'start_s': 0, 'end_s': 6, 'intended_takeaway': 'model navigation'},
    {'slot_id': 's2', 'start_s': 6, 'end_s': 10, 'intended_takeaway': 'model result'},
]}


def cut(slot_id='s1', shots=None):
    return {'slot_id': slot_id, 'shots': shots or [shot(1, 3, .5, .5)],
            'preserved_meaning': 'visible action and result', 'limitations': []}


def shot(start, end, speed=1, hold=0):
    return {'in_s': start, 'out_s': end, 'speed': speed, 'hold_s': hold,
            'reason': 'retain key visible action', 'visible_content': 'observed action'}


def test_local_mapping_and_readability_hold_compile_without_movie_source():
    outline = copy.deepcopy(OUTLINE)
    cuts = [cut(), cut('s2', [shot(0, 4, 1, 2)])]
    plan = build_plan(SOURCE, outline, cuts)
    assert outline == OUTLINE
    assert plan['segments'][1]['source_in_s'] == 6
    assert plan['segments'][1]['source_out_s'] == 10
    assert plan['audio_mode'] == 'source'
    compiled = compile_library_plan([SOURCE], plan, fps=30, width=720, height=1280)
    assert compiled['duration_s'] == 10.5
    assert all(s['source_sha256'] == SHA for s in compiled['segments'])
    assert compiled['segments'][1]['freeze_tail_s'] == 2


@pytest.mark.parametrize('key,value', [
    ('in_s', -1), ('out_s', 7), ('in_s', True), ('out_s', float('nan')),
    ('speed', .49), ('speed', 2.1), ('hold_s', 3.1), ('reason', ''),
])
def test_invalid_shot(key, value):
    value_cut = cut()
    value_cut['shots'][0][key] = value
    with pytest.raises(ValueError):
        validate_slot_cut(value_cut, OUTLINE['slots'][0])


@pytest.mark.parametrize('shots', [[], [shot(0, 2), shot(1, 3)],
                                [shot(0, .001)], [shot(0, 1)] * 9])
def test_no_empty_overlapping_subframe_or_excess_shots(shots):
    value = cut()
    value['shots'] = shots
    with pytest.raises(ValueError):
        validate_slot_cut(value, OUTLINE['slots'][0])


def test_no_op_split_is_not_finecut():
    with pytest.raises(ValueError, match='no_actual_edit'):
        build_plan(SOURCE, OUTLINE, [cut('s1', [shot(0, 3), shot(3, 6)]),
                                    cut('s2', [shot(0, 4)])])


def test_parent_sha_and_coverage_bound():
    outline = copy.deepcopy(OUTLINE)
    outline['parent_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='parent_sha256'):
        build_plan(SOURCE, outline, [cut(), cut('s2')])
    outline = copy.deepcopy(OUTLINE)
    outline['slots'][1]['start_s'] = 7
    with pytest.raises(ValueError, match='slot_contiguous'):
        build_plan(SOURCE, outline, [cut(), cut('s2')])


def test_prompt_local_time_and_review_no_intended_plot():
    assert '局部秒' in slot_prompt(OUTLINE['slots'][1])
    prompt = review_prompt(SHA, 8, [{'slot_id': 's1', 'start_s': 0, 'end_s': 8}])
    assert 'model navigation' not in prompt


def test_review_real_sha_and_interval_binding():
    review = {'video_sha256': SHA, 'observed_meaning': 'apparent meaning', 'status': 'partial',
              'slots': [{'slot_id': 's1', 'status': 'partial', 'readability': 'result too short',
                         'redundancy': 'repeated pose', 'evidence': [
                             {'start_s': 1, 'end_s': 2, 'visible_content': 'actual result'}]}],
              'limitations': ['model assessment, not human truth']}
    assert validate_review(review, SHA, 3, ['s1']) == review
    with pytest.raises(ValueError, match='review_sha'):
        validate_review(review, 'b' * 64, 3, ['s1'])
    review['slots'][0]['evidence'][0]['end_s'] = 4
    with pytest.raises(ValueError, match='review_end'):
        validate_review(review, SHA, 3, ['s1'])
