"""First question-first full run: white masks and 0.45-second TTS slots."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_story.library import contracts, pipeline, visual_audio


SHA = 'cf2fec1024243517c1811dce0bba3c1790af887823ab93ec543627606323b64e'


def source_and_glm(tmp_path, mode='question_first'):
    source = dict(sha256=SHA, path='real-kfp3.mkv', source_id='film3')
    glm = SimpleNamespace(state=SimpleNamespace(output=tmp_path, input_lock=dict(configuration=dict(
        material_search=mode, source_subtitle_regions={SHA: [dict(x=.2, y=.9, w=.4, h=.1, color='#FFFFFF', reason='legacy')]}))),
        call=Mock(side_effect=AssertionError('question-first must never ask a model for masks')))
    raw = dict(source_start_s=17.05, source_end_s=22, sha256='b' * 64)
    return source, glm, raw


def test_qfs_windows_get_one_persisted_full_black_bar_without_model_call(tmp_path, monkeypatch):
    from omni_story.library import subtitle_bar
    source, glm, raw = source_and_glm(tmp_path)
    masks = pipeline._source_subtitles(glm, source, raw)
    assert masks == [dict(x=0, y=950 / 1080, w=1, h=130 / 1080, color='#000000',
                          start_s=17.05, end_s=22, reason='verified_library_native_frames')]
    saved = json.loads((tmp_path / 'question_first_film_info' / (SHA + '.json')).read_text())
    assert saved['picture_bottom_px'] == 950 and saved['measurement']['evidence']
    monkeypatch.setattr(subtitle_bar, 'probe_media', Mock(side_effect=AssertionError('verified bars need no measurement')))
    source.pop('subtitle_bar')
    second = pipeline._source_subtitles(glm, source, dict(raw, source_start_s=30, source_end_s=33), image=True)
    assert (second[0]['start_s'], second[0]['end_s']) == (30, 33)
    glm.call.assert_not_called()


@pytest.mark.parametrize('change', [dict(color='#FFFFFF'), dict(x=.2, w=.6), dict(w=.9),
    dict(y=.9, h=.1), dict(y=.86, h=.14), dict(start_s=18), dict(end_s=21)])
def test_white_partial_or_partial_time_masks_are_rejected(tmp_path, change):
    from omni_story.library.subtitle_bar import validate_question_first_masks
    source, glm, raw = source_and_glm(tmp_path)
    masks = pipeline._source_subtitles(glm, source, raw)
    masks[0].update(change)
    with pytest.raises(ValueError, match='full_black_bar_and_window'):
        validate_question_first_masks(masks, source['subtitle_bar'], raw['source_start_s'], raw['source_end_s'])


def test_library_mask_override_is_unchanged_and_qfs_ignores_it(tmp_path):
    source, glm, raw = source_and_glm(tmp_path, mode='library')
    masks = pipeline._source_subtitles(glm, source, raw)
    assert masks == [dict(x=.2, y=.9, w=.4, h=.1, color='#FFFFFF', reason='legacy', start_s=17.05, end_s=22)]
    glm.state.input_lock['configuration']['material_search'] = 'question_first'
    assert pipeline._source_subtitles(glm, source, raw)[0]['color'] == '#000000'
    glm.call.assert_not_called()


@pytest.mark.parametrize('masks', [[], None])
def test_missing_qfs_masks_are_rejected(tmp_path, masks):
    from omni_story.library.subtitle_bar import validate_question_first_masks
    source, glm, raw = source_and_glm(tmp_path)
    pipeline._source_subtitles(glm, source, raw)
    with pytest.raises(ValueError, match='full_black_bar_and_window'):
        validate_question_first_masks(masks, source['subtitle_bar'], 17.05, 22)


@pytest.mark.parametrize('case', ['plain', 'corner_logo', 'dark_samples', 'thin_bar', 'no_bar', 'wide_overlay'])
def test_unverified_encodes_measure_full_width_columns_across_content(tmp_path, monkeypatch, case):
    import numpy as np
    from omni_story.library import subtitle_bar
    source = dict(sha256='a' * 64, path='different-encode.mkv',
                  content_range=dict(content_start_s=20, content_end_s=120))
    monkeypatch.setattr(subtitle_bar, 'probe_media', lambda path: dict(video_stream_index=0,
        streams=[dict(index=0, width=200, height=100)]))
    frames = np.zeros((12, 100, 200), dtype=np.uint8)
    bottom = 96 if case == 'thin_bar' else 100 if case == 'no_bar' else 88
    frames[:, 12:bottom, :] = 128
    if case == 'corner_logo':
        # A bottom-right watermark occupies 20% of width, below the true edge.
        frames[:, 90:99, 160:] = 255
    elif case == 'wide_overlay':
        frames[:, 90:99, 120:] = 255
    commands = []
    def raw_frames(command, **kwargs):
        commands.append(command)
        sampled = np.zeros_like(frames) if case == 'dark_samples' and len(commands) < 9 else frames
        return SimpleNamespace(stdout=sampled.tobytes())
    monkeypatch.setattr(subtitle_bar.subprocess, 'run', raw_frames)
    if case in {'thin_bar', 'no_bar', 'wide_overlay'}:
        expected = 'column_spread_too_large' if case == 'wide_overlay' else 'below_five_percent'
        with pytest.raises(ValueError, match=expected):
            subtitle_bar.question_first_masks(source, 25, 30, tmp_path)
        assert not list(tmp_path.glob('*.json'))
        return
    masks = subtitle_bar.question_first_masks(source, 25, 30, tmp_path)
    assert masks[0]['y'] == .88 and masks[0]['h'] == .12 and masks[0]['color'] == '#000000'
    assert len(commands) == 9
    assert len({c[c.index('-ss') + 1] for c in commands}) == 9
    assert all(c[c.index('-pix_fmt') + 1] == 'gray' and '-vf' not in c for c in commands)
    measurement = source['subtitle_bar']['measurement']
    assert measurement['method'] == 'ffmpeg_gray_column_median_v2'
    assert measurement['column_iqr_px'] == 0
    assert len(measurement['samples']) == 9
    assert all(s['frame_count'] == 12 for s in measurement['samples'])
    subtitle_bar.question_first_masks(source, 40, 45, tmp_path)
    assert len(commands) == 9


def test_real_line_minima_and_punctuation_counting():
    from omni_story.library.line_windows import min_line_window_s
    assert min_line_window_s('摔惨了。') == pytest.approx(.7)
    assert min_line_window_s('绝学压箱底——轰天锤！') == pytest.approx(1.44)
    twenty = '强者的账弱者买单追逐闯进集市百姓承担代价'
    assert len(twenty) == 20
    assert min_line_window_s(twenty) == pytest.approx(.18 * len(twenty))
    assert min_line_window_s('A 1，中！') == min_line_window_s('A1中')
    assert min_line_window_s('。') == .7


def short_plan():
    observation = dict(window_id='w', source_id='film3', roles=[], uncertainties=[],
        events=[dict(local_start_s=0, local_end_s=30, observed_fact='摔倒', role_ids=[])],
        usable_ranges=[dict(local_in_s=0, local_out_s=30, event_indices=[0], role_ids=[], continuity_notes='连续')])
    window = dict(window_id='w', source_id='film3', source_sha256=SHA, source_start_s=0, source_end_s=30,
                  status='watched', observation=observation)
    plan = dict(reference_sha256=SHA, audio_mode='silent', width=640, height=360, fps=30, limitations=[],
        slots=[dict(slot_id='s', intended_takeaway='摔倒', segment_ids=['seg'])],
        segments=[dict(segment_id='seg', slot_id='s', source_id='film3', window_id='w', role_ids=[],
                       source_in_s=17.05, source_out_s=17.5, speed=1, look='none', framing='fit')],
        audio_draft=[dict(id='line', text='摔惨了。', voice='Ethan', mode='narration', segment_ids=['seg'])])
    catalog = dict(sources=[dict(source_id='film3', sha256=SHA, duration_s=30)])
    return plan, catalog, window


def test_plan_validator_rejects_real_045_window_with_repairable_violation():
    from omni_story.library.line_windows import min_line_window_s
    plan, catalog, window = short_plan()
    with pytest.raises(ValueError, match='audio_line_window_below_minimum') as caught:
        contracts.validate_plan(plan, catalog, [window], SHA, require_audio_draft=True, allow_unfocused=True)
    assert caught.value.violation['path'] == '$.audio_draft[0]'
    assert caught.value.violation['value'] == pytest.approx(.45)
    assert caught.value.violation['expected'] == min_line_window_s('摔惨了。')
    assert 'shorten the text or widen the window' in caught.value.violation['repair_hint']


@pytest.mark.parametrize('text', ['让开——这不是你们的仗！', '强者的账弱者买单追逐闯进集市百姓承担代价'])
def test_rough_plan_passes_received_tts_from_prompt_context(text):
    from omni_story.library.replay_check import validator
    plan, catalog, window = short_plan()
    plan['segments'][0]['source_out_s'] = 19.85  # 2.8s window
    plan['audio_draft'][0]['text'] = text
    receipt = dict(id='line', text=text, voice='Ethan', actual_duration_s=2.16)
    context = dict(reference=dict(reference_sha256=SHA, theme='摔倒', intended_takeaway='摔倒'),
        catalog=catalog, watched_windows=[window], reference_duration_s=30,
        reference_audio_stream_index=None,
        received_audio_for_edit=dict(draft=deepcopy(plan['audio_draft']), actual_tts_lengths=[receipt]))
    state = dict(input_lock=dict(reference_sha256=SHA, configuration=dict(creative_audio=True)))
    check = validator(None, state, dict(name='plan_0'), dict(prompt=json.dumps(context)))
    check(plan)
    contracts.validate_plan(plan, catalog, [window], SHA, require_audio_draft=True,
                            allow_unfocused=True, received_tts=[receipt])
    # Prove the receipt is wired through, including when it raises the floor.
    context['received_audio_for_edit']['actual_tts_lengths'][0]['actual_duration_s'] = 3.16
    check = validator(None, state, dict(name='plan_0'), dict(prompt=json.dumps(context)))
    with pytest.raises(ValueError, match='audio_line_window_below_minimum') as caught:
        check(plan)
    assert caught.value.violation['expected'] == 3.16


class Capture:
    def call(self, stage, prompt, video, validate, **kwargs):
        self.prompt, self.validate = prompt, validate
        raise RuntimeError('captured')


@pytest.mark.parametrize('stage', ['rough_audio_timing_1', 'audio_plan_v1'])
def test_timing_revision_prompt_and_validator_share_minimum(tmp_path, stage):
    from omni_story.library.line_windows import line_window_prompt, min_line_window_s
    line = dict(id='line', text='摔惨了。', voice='Ethan', mode='narration', segment_ids=['seg'], start_s=0, end_s=4)
    context = dict(reference=dict(reference_sha256=SHA, theme='摔倒', intended_takeaway='摔倒'),
        duration_s=4, segments=[dict(id='seg', start_s=0, end_s=4, purpose='摔倒')], audio_draft=[line],
        timing_problems=[dict(line_id='line')])
    mcp = Capture()
    with pytest.raises(RuntimeError, match='captured'):
        visual_audio.revise_draft_timing(context, mcp, __file__, tmp_path, stage=stage)
    assert line_window_prompt(context['audio_draft']) in mcp.prompt
    reply = dict(audio_draft=[dict(line, start_s=1, end_s=1.45)])
    with pytest.raises(ValueError, match='audio_line_window_below_minimum') as caught:
        mcp.validate(reply)
    assert caught.value.violation['path'] == '$.audio_draft[0]'
    assert caught.value.violation['expected'] == min_line_window_s(line['text'])
    mcp.validate(dict(audio_draft=[dict(line, end_s=min_line_window_s(line['text']))]))


def test_independent_fine_audio_validator_uses_same_rule():
    from omni_story.library.audio_contracts import validate_plan
    observed = dict(actors=[], events=[dict(id='e', start_s=0, end_s=4, visible='摔倒', actor_ids=[],
        speech_mode='narration', speech_actor_id=None)], limitations=[])
    reply = dict(lines=[dict(id='line', text='摔惨了。', voice='Ethan', mode='narration', speaker_id=None,
        start_s=1, end_s=1.45, event_ids=['e'], visual_basis='摔倒')], limitations=[])
    with pytest.raises(ValueError, match='audio_line_window_below_minimum'):
        validate_plan(reply, observed, 4)
    reply['lines'][0].update(end_s=1.7, min_window_s=.7)
    validate_plan(reply, observed, 4)


def test_prompts_use_helper_numbers_and_give_each_existing_line_minimum():
    from omni_story.library.line_windows import (line_window_prompt, min_line_window_s,
        MIN_LINE_WINDOW_S, SPOKEN_CHAR_SECONDS)
    line = dict(id='real_line', text='摔惨了。')
    prompt = line_window_prompt([line])
    assert f'max({MIN_LINE_WINDOW_S:.1f}, {SPOKEN_CHAR_SECONDS:.2f} × spoken_chars)' in prompt
    assert json.dumps({'real_line': min_line_window_s(line['text'])}) in prompt
    assert 'min_window_s' in prompt and '不计标点' in prompt
    from omni_story.library.prompts import plan_prompt
    assert prompt in plan_prompt(dict(creative_audio=True, received_audio_for_edit=dict(draft=[line])))


def test_empty_and_none_lines_are_exempt():
    from omni_story.library.line_windows import validate_line_window
    validate_line_window(dict(text='', mode='narration'), .01, '$.audio_draft[0]')
    validate_line_window(dict(text='摔惨了。', mode='none'), .01, '$.audio_draft[0]')


def test_exact_received_tts_overrides_estimate_but_changed_text_or_voice_does_not():
    from omni_story.library.line_windows import validate_line_window
    line = dict(text='让开——这不是你们的仗！', voice='Ethan')
    receipt = [dict(text=line['text'], voice='Ethan', actual_duration_s=2.16)]
    validate_line_window(line, 3.0, '$.audio_draft[0]', received_tts=receipt)
    for changed in (dict(line, text='让开'), dict(line, voice='Moon')):
        with pytest.raises(ValueError, match='audio_line_window_below_minimum'):
            validate_line_window(changed, .45, '$.audio_draft[0]', received_tts=receipt)
    with pytest.raises(ValueError) as caught:
        validate_line_window(line, 2, '$.audio_draft[0]', received_tts=receipt)
    assert caught.value.violation['expected'] == 2.16


def test_fine_window_validation_rejects_white_qfs_bar(tmp_path):
    _, _, window = short_plan()
    source, glm, raw = source_and_glm(tmp_path)
    window['source_subtitle_masks'] = pipeline._source_subtitles(glm, source, dict(raw, source_start_s=0, source_end_s=30))
    window['question_first_subtitle_bar'] = source['subtitle_bar']
    window['source_subtitle_masks'][0]['color'] = '#FFFFFF'
    with pytest.raises(ValueError, match='full_black_bar_and_window'):
        contracts.validate_fine(window['observation'], window)
