"""Real FFmpeg checks for original-time cuts, selected sound and frozen output."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from omni_story.library import render as r


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg media verification requires ffmpeg and ffprobe")


def run(args):
    result = subprocess.run(args, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    directory = tmp_path_factory.mktemp("library_media")
    source, reference = directory / "source.mp4", directory / "reference.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=red:s=96x64:r=25:d=8",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=8",
         "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,4)'",
         "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-c:v", "libx264",
         "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata:s:a:0", "language=eng",
         "-metadata:s:a:1", "language=zho", str(source)])
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=1700:sample_rate=48000:duration=1", "-c:a", "pcm_s16le", str(reference)])
    return source, reference


def catalog(media):
    source, _ = media
    return {"sources": [{"source_id": "movie", "path": str(source), "sha256": sha(source),
                         "duration_s": 8, "audio_stream_index": 2}]}


def plan(mode="source"):
    return {"segments": [{"source_id": "movie", "window_id": "observed_blue",
                          "source_in_s": 6.4, "source_out_s": 7.4, "speed": 1,
                          "look": "none", "framing": "fit"}], "audio_mode": mode,
            "source_gain_db": 0, "reference_gain_db": -3,
            "reference_audio": {"start_s": 0, "end_s": 1, "stream_index": 0, "loop": True}}


def inspect(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))


def samples(path):
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
               "-ar", "48000", "-f", "s16le", "-"])
    return struct.unpack("<" + "h" * (len(raw) // 2), raw)


def energy(values, hz):
    # Ignore AAC priming and inspect a stable 0.25-second interior window.
    values = values[4800:16800]
    a = sum(v * math.cos(2 * math.pi * hz * i / 48000) for i, v in enumerate(values))
    b = sum(v * math.sin(2 * math.pi * hz * i / 48000) for i, v in enumerate(values))
    return math.hypot(a, b)


def test_original_seconds_selected_audio_and_fit_canvas(media, tmp_path):
    result = r.render_library_video(catalog(media), plan(), tmp_path, width=96, height=128)
    metadata = inspect(result["rendered_path"])
    assert abs(float(metadata["format"]["duration"]) - 1) < 0.075
    assert metadata["streams"][0]["width"] == 96
    assert metadata["streams"][0]["height"] == 128
    assert int(metadata["streams"][0]["nb_frames"]) == 24
    rgb = run(["ffmpeg", "-v", "error", "-i", result["rendered_path"], "-frames:v", "1",
               "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    center = (64 * 96 + 48) * 3
    assert rgb[center + 2] > 150 and rgb[center] < 70, "must cut blue original 6.4s, not output-FPS frame indices"
    assert max(rgb[:3]) < 10, "fit preserves black letterboxing"
    audio = samples(result["rendered_path"])
    assert energy(audio, 1000) > energy(audio, 440) * 20, "global audio stream 2 must be Mandarin selection"
    row = result["provenance"][0]
    assert (row["source_in_s"], row["source_out_s"]) == (6.4, 7.4)
    assert (row["output_in_s"], row["output_out_s"]) == (0, 1)
    assert row["source_audio_stream_index"] == 2
    segment = json.loads((tmp_path / "segment_000.json").read_text())
    assert segment["source_seek_s"] == pytest.approx(4.4), "do not decode each slice from movie beginning"


@pytest.mark.parametrize("mode", ["reference", "mix", "silent"])
def test_audio_modes_are_actual_media(media, tmp_path, mode):
    result = r.render_library_video(catalog(media), plan(mode), tmp_path, media[1], width=96, height=128)
    audio_streams = [s for s in inspect(result["rendered_path"])["streams"] if s["codec_type"] == "audio"]
    assert bool(audio_streams) == (mode != "silent")
    if mode != "silent":
        values = samples(result["rendered_path"])
        assert energy(values, 1700) > energy(values, 440) * 20
        if mode == "mix":
            assert energy(values, 1000) > energy(values, 440) * 20
        else:
            assert energy(values, 1700) > energy(values, 1000) * 20


def test_speed_rounding_concatenation_and_looped_sound(media, tmp_path):
    value = plan("mix")
    value["segments"] = [{**value["segments"][0], "source_in_s": 0.2, "source_out_s": 1.2, "speed": 0.5},
                         {**value["segments"][0], "source_in_s": 5.1, "source_out_s": 6.1,
                          "speed": 2, "framing": "crop", "look": "grayscale"}]
    result = r.render_library_video(catalog(media), value, tmp_path, media[1], width=96, height=128)
    assert result["duration_s"] == 2.5
    assert abs(float(inspect(result["rendered_path"])["format"]["duration"]) - 2.5) < 0.075
    assert result["provenance"][1]["output_in_s"] == 2
    assert result["provenance"][1]["output_out_s"] == 2.5
    raw = samples(result["rendered_path"])
    assert max(abs(v) for v in raw[int(2.1 * 48000):int(2.3 * 48000)]) > 100, "loop retains reference audio past 1s"


def test_frozen_cache_validates_output_and_never_renders_again(media, tmp_path, monkeypatch):
    inputs, value = catalog(media), plan()
    result = r.render_library_video(inputs, value, tmp_path, width=96, height=128)
    calls = []
    original = r._run
    def guarded(args, **kwargs):
        calls.append(args)
        assert Path(str(args[0])).stem == "ffprobe", "cached result must not rerun FFmpeg"
        return original(args, **kwargs)
    monkeypatch.setattr(r, "_run", guarded)
    assert r.render_library_video(inputs, value, tmp_path, width=96, height=128) == result
    assert len(calls) == 1
    Path(result["rendered_path"]).write_bytes(b"changed output")
    with pytest.raises(ValueError, match="cache_artifact_changed"):
        r.render_library_video(inputs, value, tmp_path, width=96, height=128)


def test_source_hash_once_per_source_and_changed_source_rejected(media, tmp_path, monkeypatch):
    inputs, value = catalog(media), plan()
    value["segments"].append(deepcopy(value["segments"][0]))
    source = Path(inputs["sources"][0]["path"])
    seen, original = [], r._sha
    def counted(path):
        seen.append(Path(path))
        return original(path)
    monkeypatch.setattr(r, "_sha", counted)
    r.render_library_video(inputs, value, tmp_path, width=96, height=128)
    assert seen.count(source) == 1
    copied = tmp_path / "changed_source.mp4"
    copied.write_bytes(source.read_bytes() + b"modified")
    inputs["sources"][0]["path"] = str(copied)
    with pytest.raises(ValueError, match="source_changed"):
        r.render_library_video(inputs, value, tmp_path, width=96, height=128)


def test_changed_plan_and_unobserved_outside_media_ranges_fail(media, tmp_path):
    inputs, value = catalog(media), plan()
    r.render_library_video(inputs, value, tmp_path, width=96, height=128)
    value["segments"][0]["source_in_s"] = 6.5
    with pytest.raises(ValueError, match="input_changed"):
        r.render_library_video(inputs, value, tmp_path, width=96, height=128)
    value["segments"][0]["source_out_s"] = 9
    with pytest.raises(ValueError, match="invalid_number"):
        r.render_library_video(inputs, value, tmp_path / "bad", width=96, height=128)
    inputs["sources"][0]["duration_s"] = 10
    with pytest.raises(ValueError, match="outside_actual_media"):
        r.render_library_video(inputs, value, tmp_path / "bad", width=96, height=128)


def test_invalid_stream_and_missing_reference_are_technical_failures(media, tmp_path):
    inputs = catalog(media)
    inputs["sources"][0]["audio_stream_index"] = 0
    with pytest.raises(ValueError, match="invalid_audio_stream"):
        r.render_library_video(inputs, plan(), tmp_path, width=96, height=128)
    with pytest.raises(ValueError, match="reference_audio_missing"):
        r.render_library_video(catalog(media), plan("mix"), tmp_path, width=96, height=128)


def test_legacy_compile_keeps_original_identity_shape(media):
    value = plan()
    compiled = r.compile_library_plan(catalog(media), value, width=96, height=128)
    assert compiled == {"renderer_version": "library_render_v1", "plan": value,
                        "segments": [{"segment_index": 0, "source_id": "movie", "window_id": "observed_blue",
                                      "source_path": str(media[0].resolve()), "source_sha256": sha(media[0]),
                                      "source_in_s": 6.4, "source_out_s": 7.4, "speed": 1.,
                                      "look": "none", "framing": "fit", "frames": 24,
                                      "output_in_s": 0., "output_out_s": 1., "duration_s": 1.,
                                      "source_audio_stream_index": 2}],
                        "fps": 24, "width": 96, "height": 128, "total_frames": 24,
                        "duration_s": 1., "audio_mode": "source", "source_gain_db": 0., "reference_gain_db": -3.}


def held_plan(mode="source"):
    value = plan(mode)
    value["segments"][0].update(source_in_s=3.7, source_out_s=4.1, freeze_tail_s=0.5)
    return value


def rgb_frames(path, width=96, height=128):
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    size = width * height * 3
    return [raw[i:i + size] for i in range(0, len(raw), size)]


def test_freeze_uses_actual_tail_frame_and_silences_original_sound(media, tmp_path):
    value = held_plan()
    result = r.render_library_video(catalog(media), value, tmp_path, fps=30, width=96, height=128)
    row = result["provenance"][0]
    assert result["renderer_version"] == "library_render_v2"
    assert (row["motion_frames"], row["freeze_frames"], row["frames"]) == (12, 15, 27)
    assert row["duration_s"] == pytest.approx(0.9)
    assert row["freeze_source"] == {"source_sha256": sha(media[0]), "source_in_s": 3.7, "source_out_s": 4.1,
                                    "selection": "last_output_frame_of_trimmed_source_range_before_hold"}
    frames = rgb_frames(result["rendered_path"])
    center = (64 * 96 + 48) * 3
    assert frames[0][center] > 150 and frames[0][center + 2] < 70
    assert frames[11][center + 2] > 150 and frames[11][center] < 70
    assert all(frame[center + 2] > 150 and frame[center] < 70 for frame in frames[12:])
    assert len(frames) == 27
    pcm = samples(tmp_path / "source_audio.wav")
    assert len(pcm) == 43200
    assert energy(pcm, 1000) > energy(pcm, 440) * 20
    assert max(abs(v) for v in pcm[19200:]) == 0, "source sound must not repeat into the freeze"
    final_pcm = samples(result["rendered_path"])
    assert max(abs(v) for v in final_pcm[28800:40000]) < 10
    assert float(inspect(result["rendered_path"])["format"]["duration"]) == pytest.approx(0.9, abs=0.05)


@pytest.mark.parametrize("mode", ["reference", "mix"])
def test_reference_music_remains_during_freeze(media, tmp_path, mode):
    result = r.render_library_video(catalog(media), held_plan(mode), tmp_path, media[1],
                                    fps=30, width=96, height=128)
    tail = samples(result["rendered_path"])[int(0.5 * 48000):]
    assert energy(tail, 1700) > energy(tail, 1000) * 20
    assert result["duration_s"] == pytest.approx(0.9)


def captioned_plan(text="努力之后\n获得认可", mode="silent"):
    value = held_plan(mode)
    value["segments"][0]["caption"] = {"text": text, "start_s": 0.5, "end_s": 0.9,
                                      "position": "center", "font_size": 16,
                                      "evidence": [{"window_id": "observed_blue", "event_indices": [0]}]}
    return value


def white_pixels(frame):
    return sum(all(c > 185 for c in frame[i:i + 3]) for i in range(0, len(frame), 3))


def test_chinese_caption_is_visible_only_in_selected_output_frames(media, tmp_path):
    result = r.render_library_video(catalog(media), captioned_plan(), tmp_path,
                                    fps=30, width=96, height=128)
    frames = rgb_frames(result["rendered_path"])
    assert all(white_pixels(frame) == 0 for frame in frames[:15])
    assert all(white_pixels(frame) > 30 for frame in frames[15:])
    caption = result["provenance"][0]["caption"]
    assert caption["start_frame"] == 15 and caption["end_frame"] == 27
    assert caption["font_sha256"] == sha(caption["font_path"])
    assert sha(tmp_path / caption["font_file"]) == caption["font_sha256"]
    assert caption["evidence"] == [{"window_id": "observed_blue", "event_indices": [0]}]


def test_caption_filter_content_is_inert_text_and_v2_cache_is_frozen(media, tmp_path, monkeypatch):
    text = "中文 %{eif:1+1:d}\n';[in]movie=never.mp4[out]\\"
    value = captioned_plan(text)
    calls, original = [], r._run
    def record(args, **kwargs):
        calls.append(args)
        return original(args, **kwargs)
    monkeypatch.setattr(r, "_run", record)
    result = r.render_library_video(catalog(media), value, tmp_path, fps=30, width=320, height=240)
    assert (tmp_path / "segment_000_caption.txt").read_text(encoding="utf-8") == text
    draw_args = next(args[args.index("-vf") + 1] for args in calls if "-vf" in args)
    assert "expansion=none" in draw_args and "textfile=segment_000_caption.txt" in draw_args
    assert text not in draw_args
    assert white_pixels(rgb_frames(result["rendered_path"], width=320, height=240)[20]) > 30
    calls.clear()
    assert r.render_library_video(catalog(media), value, tmp_path, fps=30, width=320, height=240) == result
    assert all(Path(str(args[0])).stem == "ffprobe" for args in calls)
    (tmp_path / result["provenance"][0]["caption"]["font_file"]).write_bytes(b"changed font")
    with pytest.raises(ValueError, match="caption_font_changed"):
        r.render_library_video(catalog(media), value, tmp_path, fps=30, width=320, height=240)


@pytest.mark.parametrize("hold", [-1, 11, True, math.inf])
def test_invalid_freeze_duration_is_rejected_at_renderer_boundary(media, hold):
    value = held_plan()
    value["segments"][0]["freeze_tail_s"] = hold
    with pytest.raises(ValueError, match="invalid_number:freeze_tail_s"):
        r.compile_library_plan(catalog(media), value)


@pytest.mark.parametrize("update", [{"text": ""}, {"text": "a\x00b"}, {"text": "a\rb"},
                                    {"text": "a" * 301}, {"end_s": 3}, {"position": "movie=x"},
                                    {"font_size": True}, {"fontfile": "untrusted.ttf"}])
def test_invalid_caption_is_rejected_at_renderer_boundary(media, update):
    value = captioned_plan()
    value["segments"][0]["caption"].update(update)
    with pytest.raises(ValueError, match="library_render_.*caption"):
        r.compile_library_plan(catalog(media), value)


def test_caption_frame_rounding_stays_inside_actual_picture(media):
    value = captioned_plan()
    value["segments"][0].update(source_in_s=1, source_out_s=2.01, freeze_tail_s=0.01)
    value["segments"][0]["caption"].update(start_s=0.8, end_s=1.02)
    compiled = r.compile_library_plan(catalog(media), value)
    assert compiled["duration_s"] == 1
    caption = compiled["segments"][0]["caption"]
    assert caption["requested_end_s"] == 1.02 and caption["end_s"] == 1
    assert caption["end_frame"] == compiled["segments"][0]["frames"]


def test_caption_font_missing_is_explicit_failure(media, tmp_path, monkeypatch):
    monkeypatch.setattr(r.Path, "is_file", lambda self: False)
    with pytest.raises(ValueError, match="caption_font_missing"):
        r.render_library_video(catalog(media), captioned_plan(), tmp_path)


def test_freeze_duration_counts_toward_v2_total_cap(media):
    value = held_plan()
    value["segments"] = [deepcopy(value["segments"][0]) for _ in range(201)]
    with pytest.raises(ValueError, match="output_exceeds_180_seconds"):
        r.compile_library_plan(catalog(media), value, fps=30)
    compiled = r.compile_library_plan(catalog(media), value, fps=30, max_duration_s=None)
    assert compiled["duration_s"] == pytest.approx(180.9)
    assert compiled["total_frames"] == 5427


def test_duration_cap_does_not_change_existing_render_cache(media, tmp_path, monkeypatch):
    value = held_plan("silent")
    result = r.render_library_video(catalog(media), value, tmp_path, width=96, height=128)
    frozen = (tmp_path / "render_input.json").read_bytes()
    original = r._run
    def guarded(args, **kwargs):
        assert Path(str(args[0])).stem == "ffprobe", "accepted cap changes must reuse the video"
        return original(args, **kwargs)
    monkeypatch.setattr(r, "_run", guarded)
    assert r.render_library_video(catalog(media), value, tmp_path, width=96, height=128,
                                  max_duration_s=None) == result
    assert (tmp_path / "render_input.json").read_bytes() == frozen


def test_caption_layout_rejects_clipped_words_without_rewriting_them(media, tmp_path):
    value = captioned_plan("中" * 27)
    value["segments"][0]["caption"]["font_size"] = 32
    before = deepcopy(value)
    with pytest.raises(ValueError, match="caption_layout_outside_canvas"):
        r.validate_caption_layout(value, 720, 1280)
    with pytest.raises(ValueError, match="caption_layout_outside_canvas"):
        r.render_library_video(catalog(media), value, tmp_path, fps=30, width=720, height=1280)
    assert value == before and not (tmp_path / "final.mp4").exists()
    value["segments"][0]["caption"]["text"] = "中" * 9 + "\n" + "中" * 9 + "\n" + "中" * 9
    layout = r.validate_caption_layout(value, 720, 1280)[0]
    assert layout["line_count"] == 3 and layout["measured_width_px"] <= 720 * 0.84


@pytest.mark.parametrize("position", ["top", "center", "bottom"])
def test_multiline_caption_ink_and_border_fit_safe_canvas(media, tmp_path, position):
    value = captioned_plan("中文\n画面")
    value["segments"][0]["caption"].update(position=position, font_size=20)
    result = r.render_library_video(catalog(media), value, tmp_path, fps=30, width=160, height=240)
    caption = result["provenance"][0]["caption"]
    layout = caption["layout"]
    frame = rgb_frames(result["rendered_path"], width=160, height=240)[20]
    coordinates = [(i // 3 % 160, i // 3 // 160) for i in range(0, len(frame), 3)
                   if all(channel > 185 for channel in frame[i:i + 3])]
    assert coordinates
    xs, ys = zip(*coordinates)
    assert min(xs) - 2 >= layout["safe_margin_x_px"]
    assert max(xs) + 2 < 160 - layout["safe_margin_x_px"]
    assert min(ys) - 2 >= layout["safe_margin_y_px"]
    assert max(ys) + 2 < 240 - layout["safe_margin_y_px"]
    assert layout["line_count"] == 2
    if position == "top":
        assert max(ys) < 120
    elif position == "bottom":
        assert min(ys) > 120
    else:
        assert min(ys) < 120 < max(ys)
