"""Native-frame FFmpeg evidence tests; no model API or library-film traversal."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import shot_timeline as st
from omni_story.library.media import sha256_file
from omni_story.library.render import render_library_video


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg and FFprobe required")


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native_scene_evidence")
    normal, offset = directory / "normal.mp4", directory / "offset.mp4"
    for path, shift in ((normal, 0), (offset, 2)):
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=black:s=96x64:r=10:d=6", "-vf",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='gte(t,2)*lt(t,4)',setpts=PTS+{shift}/TB",
            "-fps_mode", "passthrough", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)
        ], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    return normal, offset


def source(path):
    return {"source_id": "movie", "path": str(path), "sha256": sha256_file(path),
            "video_stream_index": 0, "duration_s": 6, "audio_stream_index": None}


def test_actual_cuts_native_pts_and_source_origin(videos, tmp_path):
    timeline = st.detect_shot_timeline(source(videos[1]), tmp_path)
    assert [cut["source_time_s"] for cut in timeline["cuts"]] == pytest.approx([2, 4], abs=0.0001)
    assert [cut["native_pts_time_s"] for cut in timeline["cuts"]] == pytest.approx([4, 6])
    assert timeline["source_origin_pts_time_s"] == 2
    assert timeline["source_start_s"] == 0
    assert timeline["source_end_s"] == pytest.approx(6)
    assert timeline["first_native_pts"] > 0
    assert timeline["cuts"][0]["native_pts"] > timeline["first_native_pts"]
    assert [shot["duration_s"] for shot in timeline["shots"]] == pytest.approx([2, 2, 2])
    assert timeline["shots"][-1]["native_end_pts"] is None  # Terminal frame duration is an estimate.
    assert timeline["score_summary"]["frame_count"] == 60
    assert len(timeline["cuts"]) == 2
    assert all(cut["score_percent"] > 80 for cut in timeline["cuts"])
    assert "ffmpeg version" in timeline["spec"]["tool_versions"]["ffmpeg"]
    assert Path(timeline["raw_metadata_path"]).is_file()


def test_threshold_and_cache_bound_to_sha_and_raw_scores(videos, tmp_path, monkeypatch):
    path = tmp_path / "copy.mp4"
    path.write_bytes(videos[0].read_bytes())
    old_source = source(path)
    first = st.detect_shot_timeline(old_source, tmp_path / "cache")
    original_run = st._run
    def cached_only(args, **kwargs):
        assert "-i" not in args, "Known cache must not rerun the detector"
        return original_run(args, **kwargs)
    monkeypatch.setattr(st, "_run", cached_only)
    assert st.detect_shot_timeline(old_source, tmp_path / "cache") == first
    monkeypatch.setattr(st, "_run", original_run)
    high = st.detect_shot_timeline(old_source, tmp_path / "cache", threshold=100)
    assert high["cuts"] == []
    assert len(high["shots"]) == 1
    assert high["input_identity"] != first["input_identity"]
    with path.open("ab") as stream:
        stream.write(b"new-source-version")
    with pytest.raises(ValueError, match="source_sha_mismatch"):
        st.detect_shot_timeline(old_source, tmp_path / "cache")
    updated = st.detect_shot_timeline(source(path), tmp_path / "cache")
    assert updated["input_identity"] != first["input_identity"]
    assert Path(first["manifest_path"]).is_file(), "Prior evidence must remain available"
    Path(updated["raw_metadata_path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="cache_modified"):
        st.detect_shot_timeline(source(path), tmp_path / "cache")


def test_actual_render_has_join_and_inherited_cut_not_edl_row_count(videos, tmp_path):
    item = source(videos[0])
    plan = {"audio_mode": "silent", "segments": [
        {"source_id": "movie", "window_id": "fine_1", "source_in_s": 0, "source_out_s": 1},
        {"source_id": "movie", "window_id": "fine_2", "source_in_s": 2.5, "source_out_s": 5.5},
    ]}
    rendered = render_library_video({"sources": [item]}, plan, tmp_path / "render", fps=10, width=96, height=64)
    final = source(rendered["rendered_path"])
    timeline = st.detect_shot_timeline(final, tmp_path / "timeline")
    original_timeline = deepcopy(timeline)
    annotated = st.associate_edl_boundaries(timeline, rendered)
    assert annotated["edl_segment_count"] == 2
    assert annotated["edl_join_count"] == 1
    assert annotated["detected_cut_candidate_count"] == 2
    assert len(timeline["shots"]) == 3, "Two EDL ranges contain three detected shot intervals"
    assert [cut["output_time_s"] for cut in annotated["cuts"]] == pytest.approx([1, 2.5])
    assert [cut["origin_classification"] for cut in annotated["cuts"]] == ["edl_join_candidate", "inherited_source_candidate"]
    assert annotated["joins"][0]["detected_cut_indices"] == [0]
    assert annotated["cuts"][1]["source_mapping"]["source_time_s_estimate"] == pytest.approx(4)
    assert timeline == original_timeline, "Annotations do not retroactively edit detector evidence"


def test_identical_picture_edl_join_is_retained_without_detected_cut(videos, tmp_path):
    item = source(videos[0])
    plan = {"audio_mode": "silent", "segments": [
        {"source_id": "movie", "window_id": "fine_1", "source_in_s": 0, "source_out_s": 0.5},
        {"source_id": "movie", "window_id": "fine_2", "source_in_s": 0.5, "source_out_s": 1},
    ]}
    rendered = render_library_video({"sources": [item]}, plan, tmp_path / "render", fps=10, width=96, height=64)
    timeline = st.detect_shot_timeline(source(rendered["rendered_path"]), tmp_path / "timeline")
    association = st.associate_edl_boundaries(timeline, rendered)
    assert association["detected_cut_candidate_count"] == 0
    assert association["edl_join_count"] == 1
    assert association["joins"][0]["detected_cut_indices"] == []
    assert len(timeline["shots"]) == 1


def test_frozen_tail_mapping_never_runs_past_source_out():
    timeline = {"source_sha256": "actual", "cuts": [
        {"cut_index": 0, "source_time_s": 1.7, "native_pts": 17, "score_percent": 15}
    ]}
    rendered = {"sha256": "actual", "fps": 10, "provenance": [
        {"segment_index": 0, "source_id": "movie", "source_sha256": "film", "window_id": "fine_1",
         "source_in_s": 10, "source_out_s": 11, "output_in_s": 0, "output_out_s": 2,
         "frames": 20, "motion_frames": 10, "freeze_tail_s": 1, "speed": 1},
    ]}
    association = st.associate_edl_boundaries(timeline, rendered)
    mapping = association["cuts"][0]["source_mapping"]
    assert mapping["mapping_kind"] == "frozen_tail"
    assert mapping["source_time_s_estimate"] == 11
    wrong = {**rendered, "sha256": "other"}
    with pytest.raises(ValueError, match="render_sha_mismatch"):
        st.associate_edl_boundaries(timeline, wrong)


@pytest.mark.parametrize("threshold", [0, -1, 101, True, float("nan")])
def test_invalid_detector_threshold_rejected_before_tools(tmp_path, threshold):
    with pytest.raises(ValueError, match="invalid_threshold"):
        st.detect_shot_timeline({}, tmp_path, threshold=threshold)
