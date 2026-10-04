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
