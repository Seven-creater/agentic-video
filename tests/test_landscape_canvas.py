"""The input lock pins new creative canvases without changing historical replies."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import audio_render, plan_normalisation as n, prompts, render, story_finecut
from omni_story.library.clean_chain import ROUGH_PROMPTS
from omni_story.library.state import write_json
from test_fine_plan_mechanical import finecut, read


def test_fresh_creative_lock_and_all_plan_passes_are_landscape(tmp_path):
    config = dict(creative_audio=True)
    n.configure_output_canvas(config, tmp_path)
    assert config['output_canvas'] == '1280x720'
    for _ in range(3):  # Rough, fine and revision replies use the same rule.
        plan = dict(width=720, height=1280, fps=24, cast=[], segments=[])
        n.normalise_plan(plan, output_canvas=config['output_canvas'])
        assert (plan['width'], plan['height'], plan['fps']) == (1280, 720, 24)
        assert plan['plan_normalisations'] == [
            dict(path='$.width', before=720, after=1280, reason='canvas_landscape_1280x720'),
            dict(path='$.height', before=1280, after=720, reason='canvas_landscape_1280x720')]
        snapshot = deepcopy(plan)
        n.normalise_plan(plan, output_canvas=config['output_canvas'])
        assert plan == snapshot
    write_json(tmp_path / 'library_state.json', dict(input_lock=dict(configuration=config)))
    resumed = dict(creative_audio=True)
    n.configure_output_canvas(resumed, tmp_path)
    assert resumed == config


def test_old_lock_and_noncreative_config_keep_historical_canvas(tmp_path):
    config = dict(creative_audio=True)
    lock = dict(input_lock=dict(configuration=config))
    path = tmp_path / 'library_state.json'
    write_json(path, lock)
    raw = path.read_bytes()
    resumed = dict(config, output_canvas=n.landscape_canvas_key())
    n.configure_output_canvas(resumed, tmp_path)
    assert resumed == config and path.read_bytes() == raw
    plan = dict(width=720, height=1280, fps=30, cast=[], segments=[])
    before = deepcopy(plan)
    n.normalise_plan(plan, output_canvas=resumed.get('output_canvas'))
    assert plan == before
    other = dict(creative_audio=False)
    n.configure_output_canvas(other, tmp_path / 'other')
    assert other == dict(creative_audio=False)


@pytest.mark.parametrize('canvas', [{}, dict(width='720', height=None), dict(width=1280.0, height=720.0)])
def test_fixed_canvas_always_uses_integer_dimensions(canvas):
    n.normalise_plan(canvas, output_canvas=n.landscape_canvas_key())
    assert (canvas['width'], canvas['height']) == n.LANDSCAPE_CANVAS
    assert type(canvas['width']) is type(canvas['height']) is int
    assert all(c['reason'] == 'canvas_landscape_1280x720' for c in canvas['plan_normalisations'])


@pytest.mark.parametrize('template', [prompts.plan_prompt, ROUGH_PROMPTS.plan_prompt])
def test_fresh_prompt_example_and_instruction_pin_canvas(template):
    old_context = dict(creative_audio=True, reference_duration_s=10)
    old = template(old_context)
    assert '"width":720,"height":1280' in old
    assert prompts.landscape_plan_prompt(old, old_context) == old
    context = dict(old_context, output_canvas=n.landscape_canvas_key())
    prompt = prompts.landscape_plan_prompt(template(context), context)
    assert '"width":1280,"height":720' in prompt
    assert '"width":720,"height":1280' not in prompt
    assert '横屏1280×720' in prompt


def test_fine_plan_and_revision_ignore_vertical_parent_canvas():
    evidence = read('golden')
    flow = finecut(evidence)
    flow.state.input_lock['configuration']['output_canvas'] = n.landscape_canvas_key()
    flow.auth['parent'].update(width=720, height=1280)
    for _ in range(2):
        plan = deepcopy(evidence['reply'])
        plan.update(width=720, height=1280)
        flow.plan_check(plan)
        assert (plan['width'], plan['height']) == n.LANDSCAPE_CANVAS
        assert '横屏1280×720' in flow.plan_prompt(flow.observed)


def test_fine_render_from_vertical_parent_preserves_model_framing(tmp_path, monkeypatch):
    flow = story_finecut.StoryFinecut.__new__(story_finecut.StoryFinecut)
    flow.base, flow.adaptive = tmp_path, True
    flow.state = SimpleNamespace(input_lock=dict(configuration=dict(creative_audio=True,
        output_canvas=n.landscape_canvas_key())), set_artifact=lambda *args: None)
    flow.story_context = {}
    flow.render_source = dict(source_id='chain_parent', path=str(tmp_path / 'vertical.mp4'), sha256='parent',
        duration_s=2, width=720, height=1280)
    flow.roi = dict(picture_crop=[0, 400, 720, 480])
    flow.status = lambda *args, **kwargs: None
    plan = dict(width=720, height=1280, segments=[dict(segment_id='one', source_id='chain_parent',
        window_id='full_parent_observed', source_in_s=0, source_out_s=1, framing='crop', speed=1)])
    before = deepcopy(plan)

    def compile_render(catalog, value, directory, **options):
        assert value['segments'][0]['source_crop'] == flow.roi['picture_crop']
        assert value['segments'][0]['framing'] == 'crop'
        return render.compile_library_plan(catalog, value, **{key: options[key]
            for key in ('fps', 'width', 'height', 'max_duration_s')})

    monkeypatch.setattr(story_finecut, 'render_library_video', compile_render)
    compiled = flow.render(plan, 0)
    assert (compiled['width'], compiled['height']) == n.LANDSCAPE_CANVAS
    assert plan == before


def test_landscape_subtitle_layout_validates(monkeypatch):
    # Use an installed font on macOS too; production font selection is unchanged.
    ImageFont = pytest.importorskip('PIL.ImageFont')
    try:
        font_path = render._caption_font()
    except ValueError:
        font_path = Path(ImageFont.truetype('Arial.ttf', 24).path)
    monkeypatch.setattr(render, '_caption_font', lambda: font_path)
    plan = dict(segments=[dict(caption=dict(text='Landscape subtitles', font_size=32, position='bottom'))])
    layouts = render.validate_caption_layout(plan, *n.LANDSCAPE_CANVAS)
    assert 0 in layouts


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_real_fine_mux_and_subtitle_keep_landscape_from_vertical_parent(tmp_path, monkeypatch):
    ImageFont = pytest.importorskip('PIL.ImageFont')
    try:
        font_path = render._caption_font()
    except ValueError:
        font_path = Path(ImageFont.truetype('Arial.ttf', 24).path)
    monkeypatch.setattr(render, '_caption_font', lambda: font_path)
    parent = tmp_path / 'vertical.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=red:s=720x1280:r=30:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(parent)], check=True)
    source = dict(source_id='chain_parent', path=str(parent), sha256=audio_render.sha256_file(parent),
        duration_s=1, audio_stream_index=None)
    plan = dict(segments=[dict(source_id='chain_parent', window_id='full_parent_observed',
        source_in_s=0, source_out_s=1, framing='fit')],
        audio_mode='silent')
    rendered = render.render_library_video([source], plan, tmp_path / 'fine', fps=30,
        width=n.LANDSCAPE_CANVAS[0], height=n.LANDSCAPE_CANVAS[1])
    mixed = audio_render.mix_speech(rendered['rendered_path'], [], tmp_path / 'candidate.mp4')
    final = audio_render.subtitle_final_video(mixed['path'],
        [dict(text='Landscape', start_s=0, end_s=1)], tmp_path / 'final_subtitled.mp4')
    assert (mixed['width'], mixed['height']) == (final['width'], final['height']) == n.LANDSCAPE_CANVAS
    assert final['fully_decoded']
