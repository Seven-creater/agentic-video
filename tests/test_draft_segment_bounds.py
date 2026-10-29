"""Real EDL excerpts; window probes are explicit mutations, not missing replies."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import contracts, line_windows, story_context
from omni_story.library.render import compile_library_plan
from test_mask_and_line_window import short_plan


EVIDENCE = json.loads((Path(__file__).parent / 'fixtures/e2e_draft_outside_segments.json').read_text())


def probe(segments, **changes):
    return dict(id='probe', text='好。', voice='Ethan', mode='narration',
                segment_ids=[segments[-1]['segment_id']], **changes)


@pytest.mark.parametrize('start,end', [(4, 7), (3.9, 6.4)])
def test_real_repaired_edl_rejects_stale_window_even_when_floor_is_deferred(start, end):
    segments = deepcopy(EVIDENCE['after_segments'])
    draft = [probe(segments, start_s=start, end_s=end)]
    # The received repair shortened the third shot by .5s. A window can have
    # enough speech time and still be outside its selected shot (4..6.5).
    assert EVIDENCE['before_segments'][-1]['source_out_s'] - EVIDENCE['before_segments'][-1]['source_in_s'] == 3
    with line_windows.defer_plan_line_windows() as warnings:
        with pytest.raises(ValueError, match='audio_line_window_outside_segments') as caught:
            contracts.validate_audio_draft(draft, segments)
    assert warnings == []
    violation = caught.value.violation
    assert violation['path'] == '$.audio_draft[0]'
    assert violation['value'] == dict(start_s=start, end_s=end)
    assert violation['expected'] == '4.0 ≤ start_s < end_s ≤ 6.5'
    assert violation['repair_hint']
    row = violation['offending_lines'][0]
    prompt = line_windows.line_window_prompt(draft, segments=segments)
    assert json.dumps(line_windows.draft_line_window_details(draft, segments), ensure_ascii=False) in prompt
    assert row['segments'][-1]['end_s'] == 6.5
    # An explicit correction is accepted and survives the real post-render gate.
    draft[0].update(start_s=4, end_s=6.5)
    contracts.validate_audio_draft(draft, segments)
    source = dict(source_id=segments[0]['source_id'], duration_s=2000, path='unused.mp4', sha256='a' * 64)
    compiled = compile_library_plan([source], dict(segments=segments, audio_mode='silent'), fps=30)
    reference = dict(reference_sha256='b' * 64, theme='参考目标', intended_takeaway='参考表达')
    context = story_context.build_rough_context(reference, dict(segments=segments, audio_draft=draft),
        dict(sha256='c' * 64, duration_s=compiled['duration_s'], provenance=compiled['segments']))
    assert context['audio_draft'][0]['end_s'] == 6.5


@pytest.mark.parametrize('fps', [24, 30, 60])
def test_prompt_and_resolver_use_rendered_frames_for_any_edl(fps):
    segments = deepcopy(EVIDENCE['after_segments'])
    for segment in segments:
        segment.update(speed=1.07, freeze_tail_s=.117)
    draft = [probe(segments)]
    source = dict(source_id=segments[0]['source_id'], duration_s=2000, path='unused.mp4', sha256='a' * 64)
    compiled = compile_library_plan([source], dict(segments=segments, audio_mode='silent'), fps=fps)
    rows = line_windows.draft_line_window_details(draft, segments, fps=fps)
    assert rows[0]['start_s'] == compiled['segments'][-1]['output_in_s']
    assert rows[0]['end_s'] == compiled['segments'][-1]['output_out_s']
    for actual, expected in zip(rows[0]['segments'], compiled['segments'][-1:]):
        assert actual['output_duration_s'] == pytest.approx(expected['duration_s'])
    assert json.dumps(rows, ensure_ascii=False) in line_windows.line_window_prompt(draft, segments=segments, fps=fps)


@pytest.mark.parametrize('kind', ['stale', 'quantized_end', 'quantized_anchor'])
def test_rough_plan_preflight_uses_actual_output_bounds(kind):
    plan, catalog, window = short_plan()
    segment = deepcopy(EVIDENCE['after_segments'][-1])
    segment['role_ids'] = []  # Isolate time validation from the missing cast evidence.
    plan['segments'] = [segment]
    plan['slots'][0].update(slot_id=segment['slot_id'], segment_ids=[segment['segment_id']])
    catalog['sources'][0].update(source_id=segment['source_id'], duration_s=2000)
    window.update(window_id=segment['window_id'], source_id=segment['source_id'],
                  source_start_s=1260, source_end_s=1290)
    window['observation'].update(window_id=segment['window_id'], source_id=segment['source_id'])
    draft = probe([segment], start_s=0, end_s=3)
    if kind != 'stale':
        segment['speed'] = 1.008  # Nominal 2.480..s rounds down to 2.466..s.
        nominal = (segment['source_out_s'] - segment['source_in_s']) / segment['speed']
        draft.update(end_s=nominal)
        if kind == 'quantized_anchor':
            draft.pop('start_s')
            draft.pop('end_s')
            draft['start_anchor'] = dict(segment_id=segment['segment_id'], offset_s=nominal - .001)
    plan['audio_draft'] = [draft]
    with pytest.raises(ValueError, match='audio_line_window_outside_segments') as caught:
        contracts.validate_plan(plan, catalog, [window], plan['reference_sha256'],
                                require_audio_draft=True, allow_unfocused=True)
    assert set(('path', 'value', 'expected', 'repair_hint')) <= caught.value.violation.keys()


def test_fine_plan_uses_the_same_frame_bounds(tmp_path):
    from test_plan_line_window_exhaustion import fine
    plan, check, _ = fine(tmp_path)
    segment = plan['segments'][0]
    real = EVIDENCE['after_segments'][-1]
    segment.update(source_in_s=0, source_out_s=real['source_out_s'] - real['source_in_s'], speed=1.008)
    nominal = segment['source_out_s'] / segment['speed']
    plan['audio_draft'] = [probe([segment], start_s=0, end_s=nominal)]
    with pytest.raises(ValueError, match='audio_line_window_outside_segments'):
        check(plan)
