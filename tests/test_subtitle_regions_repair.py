"""subtitle_regions format slips: widen point times, report everything else at once.

Replays the three real replies of server job
prod_27_发现三个问题_11021100/evaluations/rule2/evaluations/len1 (release 5b35ad5,
stopped with model_protocol_repair_exhausted after 9 calls):

- glm_007 returned pixel rectangles (y=902 on a 720x406 window, so not the
  watched file's pixels and never rescaled) plus a one-second subtitle written
  as the point start_s == end_s == 18 (tests/fixtures/s35_attempt0_pixel_point_time.json,
  trimmed from 22 masks to 7; masks[0] and masks[6] keep the recorded values);
- glm_008 divided those pixels by 1000 and produced x+w > 1 ratios
  (tests/fixtures/s35_repair1_ratio_sum_overflow.json);
- glm_003 in an earlier window of the same run returned valid normalized masks
  (tests/fixtures/s35_valid_normalized.json, same shape).
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_story.library import pipeline
from omni_story.library.audio_contracts import validate_regions
from omni_story.library.protocol_feedback import diagnose
from omni_story.library.state import json_sha


FIXTURES = Path(__file__).parent / 'fixtures'
SHA = 'a' * 64
WINDOW = dict(source_start_s=0, source_end_s=60.0, sha256='b' * 64, path='missing-window.mp4',
              kind='continuous_window', source_sha256=SHA)


def reply(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def make_glm(tmp_path, model_reply):
    """Mimic glm.call: validate a copy of the recorded reply and return its view."""
    def call(name, prompt, media, validator, *, image=False, scope=None):
        value = deepcopy(model_reply)
        validator(value)
        return value
    glm = SimpleNamespace(state=SimpleNamespace(output=tmp_path,
        input_lock=dict(configuration={}), data=dict(calls=[])), call=Mock(side_effect=call))
    return glm


def with_call_ledger(glm, raw, *, image=False, call_id='call_region_1'):
    stage = 'subtitle_regions_' + json_sha({'media_sha256': raw['sha256'],
        'source_start_s': raw['source_start_s'], 'source_end_s': raw['source_end_s'],
        'image': image})[:16]
    glm.state.data['calls'].append(dict(name=stage, id=call_id))
    return stage


def violations_for(model_reply, duration=60.0):
    """The repair feedback glm.call would build for a rejected regions reply."""
    value = deepcopy(model_reply)
    try:
        validate_regions(value, duration)
    except ValueError as error:
        return str(error), diagnose('subtitle_regions_e386d99f815c489d', deepcopy(model_reply),
            {'validation_error': str(error), 'violation': getattr(error, 'violation', None),
             'media_duration_s': duration})
    pytest.fail('reply unexpectedly passed validation')


def test_pixel_reply_is_never_rescaled_even_with_known_frame(tmp_path):
    # The real window was 720x406 yet the model wrote y=902: its pixel space is
    # not the watched file's, so the program must not guess a scale.
    raw = dict(WINDOW, metadata=dict(streams=[dict(index=0, codec_type='video',
                                                   width=720, height=406)]))
    glm = make_glm(tmp_path, reply('s35_attempt0_pixel_point_time.json'))
    with_call_ledger(glm, raw)
    with pytest.raises(ValueError, match='normalized_rectangle'):
        pipeline._source_subtitles(glm, source=dict(sha256=SHA), raw=raw)
    assert not (tmp_path / 'region_normalisation').exists()


def test_point_time_widening_is_recorded_for_the_call(tmp_path):
    fixed = reply('s35_valid_normalized.json')
    fixed['masks'][0].update(start_s=18, end_s=18)
    glm = make_glm(tmp_path, fixed)
    stage = with_call_ledger(glm, WINDOW)
    masks = pipeline._source_subtitles(glm, source=dict(sha256=SHA), raw=dict(WINDOW))
    assert (masks[0]['start_s'], masks[0]['end_s']) == (18, 19)
    recorded = json.loads((tmp_path / 'region_normalisation' / (stage + '.json')
                           ).read_text(encoding='utf-8'))
    assert recorded == [dict(kind='point_time_widened', mask_count=1, widened_s=1)]


def test_attempt0_with_unknown_frame_reports_rectangle_and_point_time_together(tmp_path):
    glm = make_glm(tmp_path, reply('s35_attempt0_pixel_point_time.json'))
    with_call_ledger(glm, WINDOW)
    with pytest.raises(ValueError, match='normalized_rectangle'):
        pipeline._source_subtitles(glm, source=dict(sha256=SHA), raw=dict(WINDOW))
    error, items = violations_for(reply('s35_attempt0_pixel_point_time.json'))
    # The stage error keeps the first violation name, so ledgers stay comparable.
    assert error == 'audio_contracts:regions.masks[0].normalized_rectangle'
    by_path = {item['path']: item for item in items}
    assert 'x=223 > 1' in by_path['$.masks[0]']['expected']
    # One feedback round lists the point-time mask next to the pixel rectangles.
    assert 'start_s=18 end_s=18' in by_path['$.masks[6]']['expected']
    assert '0 ≤ start_s < end_s ≤ 60.0' in by_path['$.masks[6]']['expected']
    assert set(by_path) == {f'$.masks[{i}]' for i in range(7)}


def test_repair1_reply_lists_every_mask_with_sum_over_one(tmp_path):
    error, items = violations_for(reply('s35_repair1_ratio_sum_overflow.json'))
    assert error == 'audio_contracts:regions.masks[0].normalized_rectangle'
    by_path = {item['path']: item for item in items}
    assert 'x+w=1.166 > 1' in by_path['$.masks[0]']['expected']
    assert 'x+w=1.15 > 1' in by_path['$.masks[1]']['expected']
    assert 'x+w=1.25 > 1' in by_path['$.masks[3]']['expected']
    assert 'color "white" (need #RRGGBB)' in by_path['$.masks[3]']['expected']
    assert '$.masks[2]' not in by_path  # the valid mask is not reported


def test_point_at_duration_covers_the_last_second():
    value = dict(masks=[dict(x=.1, y=.8, w=.5, h=.1, start_s=60, end_s=60,
                             color='#000000', reason='末尾叠字')],
                 protected_text=[], limitations=[])
    validate_regions(value, 60.0)
    assert (value['masks'][0]['start_s'], value['masks'][0]['end_s']) == (59.0, 60.0)
    inside = dict(masks=[dict(x=.1, y=.8, w=.5, h=.1, start_s=18, end_s=18,
                              color='#000000', reason='说话人标签')],
                  protected_text=[], limitations=[])
    validate_regions(inside, 60.0)
    assert (inside['masks'][0]['start_s'], inside['masks'][0]['end_s']) == (18, 19)
    outside = dict(masks=[dict(x=.1, y=.8, w=.5, h=.1, start_s=61, end_s=61,
                               color='#000000', reason='越界')],
                   protected_text=[], limitations=[])
    with pytest.raises(ValueError, match='time_range'):
        validate_regions(outside, 60.0)


def test_valid_reply_is_unchanged_and_needs_no_normalisation():
    raw_reply = reply('s35_valid_normalized.json')
    value = deepcopy(raw_reply)
    recorded = ['stale']
    validate_regions(value, 60.0, normalisations=recorded)
    assert recorded == []
    assert value == raw_reply
    assert json.dumps(value, ensure_ascii=False) == json.dumps(raw_reply, ensure_ascii=False)


def mask(**fields):
    return dict(dict(x=.1, y=.8, w=.5, h=.1, start_s=1, end_s=2, color='#000000',
                     reason='底部叠字'), **fields)


def test_error_string_is_the_original_contracts_first_violation():
    # Boundary noise that clamps to an empty interval comes first in the old
    # contract and must stay first, listed next to the later color problem.
    value = dict(masks=[mask(start_s=-0.0000008, end_s=-0.0000005), mask(color='white')],
                 protected_text=[], limitations=[])
    error, items = violations_for(value)
    assert error == 'regions.masks[0].time_range'
    assert {item['path'] for item in items} == {'$.masks[0]', '$.masks[1]'}


def test_rejected_reply_is_not_partly_widened():
    value = dict(masks=[mask(start_s=18, end_s=18), mask(start_s=60.0000005, end_s=60.0000008)],
                 protected_text=[], limitations=[])
    original = deepcopy(value)
    with pytest.raises(ValueError):
        validate_regions(value, 60.0)
    assert value == original


def test_every_bad_field_of_one_mask_is_named_with_its_value():
    value = dict(masks=[mask(x='0.1', y='0.8')], protected_text=[], limitations=[])
    error, items = violations_for(value)
    assert len(items) == 1
    assert items[0]['value'] == {'x': '0.1', 'y': '0.8'}
    assert 'x="0.1"' in items[0]['expected'] and 'y="0.8"' in items[0]['expected']


def test_a_non_numeric_coordinate_does_not_hide_another_out_of_range_one():
    value = dict(masks=[mask(x='0.1', y=2)], protected_text=[], limitations=[])
    error, items = violations_for(value)
    assert len(items) == 1
    assert 'x="0.1" (need finite number)' in items[0]['expected']
    assert 'y=2 > 1' in items[0]['expected'] and 'y+h=2.1 > 1' in items[0]['expected']
