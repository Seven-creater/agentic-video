"""Actual local FFmpeg media tests; no model API or model downloads."""
from __future__ import annotations

import array
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library.asr import transcribe_window
from omni_story.library.media import (
    create_contact_sheet, inventory_sources, prepare_window, probe_media,
    select_audio_stream, sha256_file,
)

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg and FFprobe required")


@pytest.fixture
def source(tmp_path):
    movie = tmp_path / "双音轨.mkv"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=16000:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=16000:duration=6",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "pcm_s16le", "-metadata:s:a:0", "title=English", "-metadata:s:a:0", "language=eng",
        "-metadata:s:a:1", "title=国语", "-metadata:s:a:1", "language=eng", str(movie)
    ], check=True, capture_output=True, timeout=30)
    return inventory_sources([movie], tmp_path / "inventory")["sources"][0]


def _tone_frequency(path: Path) -> float:
    decoded = subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-ac", "1", "-ar", "16000", "-f", "s16le", "-"
    ], check=True, capture_output=True, timeout=30).stdout
    samples = array.array("h", decoded)
    # Exclude codec edges; positive zero crossings measure a pure sine's frequency.
    samples = samples[1600:-1600]
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    return crossings / (len(samples) / 16000)


def test_inventory_prefers_mandarin_title_over_incorrect_language(source, tmp_path):
    assert source["audio_stream_index"] == 2
    assert source["streams"][2]["tags"]["language"] == "eng"
    assert source["sha256"] == sha256_file(source["path"])
    assert source["duration_s"] == pytest.approx(6, abs=0.1)
    record = json.loads((tmp_path / "inventory" / "inventory.json").read_text(encoding="utf-8"))
    assert record["sources"][0]["sha256"] == source["sha256"]


def test_window_is_continuous_small_and_maps_original_time(source, tmp_path):
    row = prepare_window(source, 1.25, 3.75, tmp_path / "cache", max_bytes=75_000)
    assert row["kind"] == "continuous_window"
    assert row["size_bytes"] < 75_000
    assert row["source_offset_s"] == 1.25
    assert row["source_start_s"] == 1.25
    assert row["source_end_s"] == 3.75
    assert row["media_duration_s"] == pytest.approx(2.5, abs=0.2)
    assert row["source_sha256"] == source["sha256"]
    assert row["audio_stream_index"] == 2
    assert _tone_frequency(Path(row["path"])) == pytest.approx(700, abs=8)
    original_mtime = Path(row["path"]).stat().st_mtime_ns
    again = prepare_window(source, 1.25, 3.75, tmp_path / "cache", max_bytes=75_000)
    assert again["path"] == row["path"]
    assert Path(again["path"]).stat().st_mtime_ns == original_mtime
    assert not Path(row["path"]).with_name("window.part.mp4").exists()


def test_source_changes_require_new_inventory_and_new_cache(source, tmp_path):
    row = prepare_window(source, 1, 3, tmp_path / "cache")
    path = Path(source["path"])
    with path.open("ab") as stream:
        stream.write(b"test-new-source-version")
    os.utime(path, None)
    with pytest.raises(ValueError, match="source_changed_since_inventory"):
        prepare_window(source, 1, 3, tmp_path / "cache")
    new_source = inventory_sources([path], tmp_path / "new-inventory")["sources"][0]
    changed = prepare_window(new_source, 1, 3, tmp_path / "cache")
    assert new_source["sha256"] != source["sha256"]
    assert changed["path"] != row["path"]
    assert Path(row["path"]).is_file()  # Old observations remain preserved.


def test_modified_proxy_is_not_silently_reused(source, tmp_path):
    row = prepare_window(source, 0, 2, tmp_path / "cache")
    with Path(row["path"]).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="media_cache_missing_or_modified"):
        prepare_window(source, 0, 2, tmp_path / "cache")


def test_sparse_sheet_has_original_frame_mapping_and_no_continuity_claim(source, tmp_path):
    pytest.importorskip("PIL")
    row = create_contact_sheet(source, 2, 6, tmp_path / "cache", frame_count=4, columns=2, tile_width=160)
    assert row["continuous"] is False
    assert row["audio_present"] is False
    assert [frame["source_time_s"] for frame in row["frames"]] == [2.5, 3.5, 4.5, 5.5]
    assert [frame["sheet_label"] for frame in row["frames"]] == ["F00", "F01", "F02", "F03"]
    assert all(Path(frame["path"]).is_file() for frame in row["frames"])
    again = create_contact_sheet(source, 2, 6, tmp_path / "cache", frame_count=4, columns=2, tile_width=160)
    assert again["sha256"] == row["sha256"]


def test_asr_offsets_segment_and_word_times_and_reuses_cache(source, tmp_path):
    class LocalStub:
        calls = 0
        def transcribe(self, audio, **kwargs):
            self.calls += 1
            assert kwargs["vad_filter"] and kwargs["word_timestamps"]
            assert _tone_frequency(Path(audio)) == pytest.approx(700, abs=8)
            words = [SimpleNamespace(word="hello", start=0.4, end=0.8, probability=0.9)]
            segment = SimpleNamespace(start=0.3, end=1.2, text="hello", words=words,
                                      avg_logprob=-0.1, no_speech_prob=0.05)
            return iter([segment]), SimpleNamespace(language="en", language_probability=0.8)
    model = LocalStub()
    row = transcribe_window(source, 2, 4, tmp_path / "asr", model=model)
    assert row["segments"][0]["source_start_s"] == 2.3
    assert row["segments"][0]["source_end_s"] == 3.2
    assert row["segments"][0]["words"][0]["source_start_s"] == 2.4
    assert row["evidence_kind"] == "automatic_transcription_unverified"
    assert row["audio_stream_index"] == 2
    assert transcribe_window(source, 2, 4, tmp_path / "asr", model=model) == row
    assert model.calls == 1


def _asr_stub(segment_start=0.3, segment_end=1.2, word_start=0.4, word_end=0.8):
    class LocalStub:
        calls = 0
        def transcribe(self, audio, **kwargs):
            self.calls += 1
            segment = SimpleNamespace(start=segment_start, end=segment_end, text="hello",
                words=[SimpleNamespace(word="hello", start=word_start, end=word_end, probability=0.9)],
                avg_logprob=-0.1, no_speech_prob=0.05)
            return iter([segment]), SimpleNamespace(language="en", language_probability=0.8)
    return LocalStub()


@pytest.mark.parametrize("start,end", [
    (float("nan"), 1), (0, float("inf")), (float("-inf"), 1),
    (-0.06, 1), (0, 2.26), (1, 0.5), (2.1, 2.2),
])
def test_asr_rejects_nonfinite_or_unusable_segment_times(source, tmp_path, start, end):
    with pytest.raises(ValueError, match="asr_timestamp"):
        transcribe_window(source, 2, 4, tmp_path / "asr", model=_asr_stub(start, end))
    assert not list((tmp_path / "asr").rglob("transcript.json"))
    raw_files = list((tmp_path / "asr").rglob("raw_asr_*.json"))
    assert len(raw_files) == 1
    assert json.loads(raw_files[0].read_text(encoding="utf-8"))["segments"][0]["text"] == "hello"


@pytest.mark.parametrize("start,end", [
    (float("nan"), 0.8), (0.4, float("inf")), (-0.06, 0.8),
    (0.4, 2.26), (0.1, 0.8), (0.4, 1.4), (0.7, 0.6),
])
def test_asr_rejects_nonfinite_and_out_of_segment_word_times(source, tmp_path, start, end):
    with pytest.raises(ValueError, match="asr_(timestamp|word_timestamp)"):
        transcribe_window(source, 2, 4, tmp_path / "asr", model=_asr_stub(word_start=start, word_end=end))


def test_asr_boundary_rounding_is_explicit_and_retains_raw_values(source, tmp_path):
    row = transcribe_window(source, 2, 4, tmp_path / "asr", model=_asr_stub(-0.02, 2.10, -0.01, 2.05))
    segment = row["segments"][0]
    assert (segment["local_start_s"], segment["local_end_s"]) == (0, 2)
    assert (segment["source_start_s"], segment["source_end_s"]) == (2, 4)
    assert (segment["raw_asr_local_start_s"], segment["raw_asr_local_end_s"]) == (-0.02, 2.10)
    assert segment["boundary_clamped"] is True
    word = segment["words"][0]
    assert (word["source_start_s"], word["source_end_s"]) == (2, 4)
    assert (word["raw_asr_local_start_s"], word["raw_asr_local_end_s"]) == (-0.01, 2.05)
    assert word["boundary_clamped"] is True
    assert row["time_validation"]["raw_times_are_not_edit_ranges"] is True


@pytest.mark.parametrize("target,value", [
    ("segment_nan", float("nan")), ("segment_end", 4.1), ("word_nan", float("nan")), ("word_end", 3.8),
])
def test_asr_reads_validate_cache_without_replaying_or_rewriting(source, tmp_path, target, value):
    model = _asr_stub()
    row = transcribe_window(source, 2, 4, tmp_path / "asr", model=model)
    record = Path(row["audio_path"]).with_name("transcript.json")
    if target == "segment_nan":
        row["segments"][0]["local_start_s"] = value
    elif target == "segment_end":
        row["segments"][0].update(local_end_s=2.1, source_end_s=value)
    elif target == "word_nan":
        row["segments"][0]["words"][0]["source_start_s"] = value
    else:
        row["segments"][0]["words"][0]["source_end_s"] = value
    record.write_text(json.dumps(row), encoding="utf-8")
    before = record.read_bytes()
    with pytest.raises(ValueError, match="asr_cache"):
        transcribe_window(source, 2, 4, tmp_path / "asr", model=model)
    assert record.read_bytes() == before
    assert model.calls == 1


def test_valid_legacy_asr_cache_remains_byte_identical(source, tmp_path):
    model = _asr_stub()
    row = transcribe_window(source, 2, 4, tmp_path / "asr", model=model)
    row.pop("time_validation")
    for segment in row["segments"]:
        for item in [segment, *segment["words"]]:
            for key in ("raw_asr_local_start_s", "raw_asr_local_end_s", "boundary_clamped"):
                item.pop(key)
    record = Path(row["audio_path"]).with_name("transcript.json")
    record.write_text(json.dumps(row), encoding="utf-8")
    before = record.read_bytes()
    assert transcribe_window(source, 2, 4, tmp_path / "asr", model=model) == row
    assert record.read_bytes() == before
    assert model.calls == 1


@pytest.mark.parametrize("timestamp", [0.0, 1.2])
def test_zero_duration_word_preserves_text_without_usable_time(source, tmp_path, timestamp):
    model = _asr_stub(segment_start=0, segment_end=1.2, word_start=timestamp, word_end=timestamp)
    row = transcribe_window(source, 2, 4, tmp_path / "asr", model=model)
    segment = row["segments"][0]
    word = segment["words"][0]
    assert (segment["source_start_s"], segment["source_end_s"]) == (2, 3.2)
    assert word["text"] == "hello"
    assert word["alignment_issue"] == "zero_duration" and word["timing_usable"] is False
    assert "source_start_s" not in word and "source_end_s" not in word
    assert word["raw_asr_local_start_s"] == word["raw_asr_local_end_s"] == timestamp
    raw = json.loads(Path(row["raw_response_path"]).read_text(encoding="utf-8"))
    assert raw["segments"][0]["words"][0]["start"] == timestamp
    assert raw["audio_sha256"] == row["audio_sha256"]
    assert transcribe_window(source, 2, 4, tmp_path / "asr", model=model) == row
    assert model.calls == 1


def test_rounding_can_make_word_unusable_without_losing_segment(source, tmp_path):
    row = transcribe_window(source, 2, 4, tmp_path / "asr",
                            model=_asr_stub(segment_start=0, segment_end=2, word_start=2.01, word_end=2.03))
    word = row["segments"][0]["words"][0]
    assert word["alignment_issue"] == "clipped_to_empty"
    assert word["timing_usable"] is False
    assert "source_start_s" not in word and "source_end_s" not in word


def test_zero_duration_outside_window_is_still_rejected_and_raw_saved(source, tmp_path):
    with pytest.raises(ValueError, match="asr_timestamp_out_of_window"):
        transcribe_window(source, 2, 4, tmp_path / "asr", model=_asr_stub(word_start=3, word_end=3))
    raw = json.loads(next((tmp_path / "asr").rglob("raw_asr_*.json")).read_text(encoding="utf-8"))
    assert raw["segments"][0]["words"][0]["start"] == 3
    assert not list((tmp_path / "asr").rglob("transcript.json"))


def test_cache_unusable_word_cannot_expose_edit_timestamps(source, tmp_path):
    row = transcribe_window(source, 2, 4, tmp_path / "asr", model=_asr_stub(word_start=0.5, word_end=0.5))
    row["segments"][0]["words"][0]["source_start_s"] = 2.5
    record = Path(row["audio_path"]).with_name("transcript.json")
    record.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="asr_cache_unusable_word_has_source_times"):
        transcribe_window(source, 2, 4, tmp_path / "asr", model=_asr_stub())


@pytest.mark.parametrize("start,end", [(2, 1), (-1, 2), (0, 8), (float("nan"), 2)])
def test_invalid_original_ranges_are_rejected(source, tmp_path, start, end):
    with pytest.raises(ValueError, match="source_range"):
        prepare_window(source, start, end, tmp_path / "cache")


def test_audio_selection_without_audio_and_language_fallback():
    assert select_audio_stream([]) is None
    assert select_audio_stream([
        {"index": 1, "codec_type": "audio", "tags": {"language": "eng"}, "disposition": {"default": 1}},
        {"index": 2, "codec_type": "audio", "tags": {"language": "zho"}},
    ]) == 2
