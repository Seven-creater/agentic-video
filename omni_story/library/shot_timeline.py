"""CPU scene-change evidence, with native PTS and separate EDL joins.

FFmpeg's image-difference score proposes boundaries; it does not establish
semantic shots or an editing technique. No frame sampling or model call occurs.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess

from .media import sha256_file


SHOT_TIMELINE_VERSION = "ffmpeg_scdet_native_v1"
_HEADER = re.compile(r"frame:(\d+)\s+pts:(-?\d+)\s+pts_time:([^\s]+)")


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _run(args, *, timeout=900):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("shot_timeline_tool_failed:" + result.stderr.decode(errors="replace")[-3000:])
    return result.stdout.decode("utf-8", errors="replace")


def _write(path, value):
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    partial.replace(path)


def _score_rows(raw):
    """Read metadata output; scd.time records the detector's threshold decision."""
    current = None
    for line in raw.splitlines():
        header = _HEADER.fullmatch(line.strip())
        if header:
            if current is not None:
                yield current
            current = {"frame_index": int(header[1]), "native_pts": int(header[2]),
                       "printed_pts_time_s": float(header[3])}
        elif current is not None and line.startswith("lavfi.scd."):
            key, value = line.split("=", 1)
            current[key.removeprefix("lavfi.scd.")] = float(value)
    if current is not None:
        yield current


def detect_shot_timeline(source: dict, cache_dir, *, threshold=10.0,
                         ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    """Decode native frames and return threshold-based cut/interval evidence.

    ``source`` uses the inventory's path/SHA/global video-stream index. Native
    PTS are retained; source seconds subtract the actual format start time.
    Existing cache entries bind SHA, tool versions and detector configuration.
    """
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 < threshold <= 100:
        raise ValueError("shot_timeline_invalid_threshold")
    path = Path(source["path"]).resolve(strict=True)
    before = path.stat()
    digest = sha256_file(path)
    if digest != source["sha256"]:
        raise ValueError("shot_timeline_source_sha_mismatch")
    tool_versions = {"ffmpeg": _run([ffmpeg, "-version"], timeout=30).splitlines()[0],
                     "ffprobe": _run([ffprobe, "-version"], timeout=30).splitlines()[0]}
    metadata = json.loads(_run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path], timeout=120))
    videos = [s for s in metadata["streams"] if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    stream_index = source.get("video_stream_index", videos[0]["index"] if videos else None)
    stream = next((s for s in videos if s["index"] == stream_index), None)
    if stream is None:
        raise ValueError("shot_timeline_invalid_video_stream")
    time_base = Fraction(stream["time_base"])
    origin = float(metadata.get("format", {}).get("start_time", stream.get("start_time", 0)))
    spec = {"version": SHOT_TIMELINE_VERSION, "source_path": str(path),
            "source_id": source.get("source_id"), "source_sha256": digest,
            "video_stream_index": stream_index, "threshold_percent": float(threshold),
            "tool_versions": tool_versions, "time_base": str(time_base),
            "native_resolution": [stream["width"], stream["height"]]}
    identity = _identity(spec)
    folder = Path(cache_dir).resolve() / identity[:20]
    marker, raw_path = folder / "timeline.json", folder / "scdet_metadata.txt"
    if marker.exists():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        content = {k: v for k, v in saved.items() if k != "record_sha256"}
        if (saved.get("spec") != spec or saved.get("record_sha256") != _identity(content)
                or not raw_path.is_file() or sha256_file(raw_path) != saved.get("raw_metadata_sha256")):
            raise ValueError("shot_timeline_cache_modified")
        return saved
    folder.mkdir(parents=True, exist_ok=True)
    raw = _run([ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-copyts", "-i", path,
                "-map", f"0:{stream_index}", "-an", "-vf",
                f"scdet=threshold={threshold:.9f},metadata=mode=print:file=-",
                "-fps_mode", "passthrough", "-f", "null", "-"])
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("shot_timeline_source_changed_during_detection")
    rows = list(_score_rows(raw))
    if not rows or any("score" not in row for row in rows):
        raise ValueError("shot_timeline_no_frame_scores")
    for row in rows:
        row["native_pts_time_s"] = float(row["native_pts"] * time_base)
        row["source_time_s"] = row["native_pts_time_s"] - origin
    steps = [b["native_pts_time_s"] - a["native_pts_time_s"] for a, b in zip(rows, rows[1:])]
    if any(step <= 0 for step in steps):
        raise ValueError("shot_timeline_non_monotonic_pts")
    # Metadata has no final frame duration. Retain this endpoint estimate as
    # uncertainty rather than manufacturing an exact terminal PTS.
    last_duration = statistics.median(steps) if steps else 1 / float(Fraction(stream["avg_frame_rate"]))
    start, end = rows[0]["source_time_s"], rows[-1]["source_time_s"] + last_duration
    cuts = [{"cut_index": index, "frame_index": row["frame_index"], "native_pts": row["native_pts"],
             "native_pts_time_s": row["native_pts_time_s"], "source_time_s": row["source_time_s"],
             "score_percent": row["score"], "mafd_percent": row.get("mafd"),
             "boundary_status": "scene_change_candidate"}
            for index, row in enumerate(row for row in rows[1:] if "time" in row)]
    points = [start, *[cut["source_time_s"] for cut in cuts], end]
    native_points = [rows[0]["native_pts"], *[cut["native_pts"] for cut in cuts], None]
    shots = [{"shot_index": index, "start_s": left, "end_s": right, "duration_s": right - left,
              "native_start_pts": native_points[index], "native_end_pts": native_points[index + 1],
              "boundary_status": "threshold_based_interval"}
             for index, (left, right) in enumerate(zip(points, points[1:]))]
    raw_partial = raw_path.with_suffix(".txt.part")
    raw_partial.write_text(raw, encoding="utf-8")
    raw_partial.replace(raw_path)
    result = {"version": SHOT_TIMELINE_VERSION, "spec": spec, "input_identity": identity,
              "source_id": source.get("source_id"), "source_path": str(path), "source_sha256": digest,
              "video_stream_index": stream_index, "native_time_base": str(time_base),
              "source_origin_pts_time_s": origin, "source_start_s": start, "source_end_s": end,
              "first_native_pts": rows[0]["native_pts"], "last_native_pts": rows[-1]["native_pts"],
              "terminal_frame_duration_estimate_s": last_duration,
              "cuts": cuts, "shots": shots,
              "score_summary": {"frame_count": len(rows), "cut_candidate_count": len(cuts),
                                "max_score_percent": max(row["score"] for row in rows)},
              "raw_metadata_path": str(raw_path), "raw_metadata_sha256": sha256_file(raw_path),
              "manifest_path": str(marker), "limitations": [
                  "Scene-score candidates are not semantic shot or editing-technique ground truth.",
                  "Flashes, animation, motion, titles and overlays can cause false positives; gradual changes can be missed.",
                  "Native frames are decoded at their original resolution without visual-token compression.",
                  "The terminal frame duration is estimated from median PTS spacing; candidate boundary PTS are retained exactly.",
                  "No sound, music rhythm or narrative interpretation is measured."]}
    result["record_sha256"] = _identity(result)
    _write(marker, result)
    return result


def _source_mapping(row, output_time_s, fps):
    elapsed = output_time_s - row["output_in_s"]
    motion_duration = row.get("motion_duration_s", row.get("motion_frames", row["frames"]) / fps)
    if "motion_frames" not in row and "motion_duration_s" not in row:
        motion_duration = (row["source_out_s"] - row["source_in_s"]) / row.get("speed", 1)
    frozen = row.get("freeze_tail_s", 0) > 0 and elapsed >= motion_duration - 1e-9
    estimate = row["source_in_s"] + elapsed * row.get("speed", 1)
    return {"segment_index": row["segment_index"], "source_id": row["source_id"],
            "source_sha256": row["source_sha256"], "window_id": row.get("window_id"),
            "source_time_s_estimate": min(row["source_out_s"], max(row["source_in_s"], estimate)),
            "mapping_kind": "frozen_tail" if frozen else "edl_affine_estimate",
            "mapping_limit": "EDL-derived source-second estimate, not a decoded original-film frame PTS."}


def associate_edl_boundaries(timeline: dict, render_result: dict, *, tolerance_s=None) -> dict:
    """Return fresh annotations; never alter a detector, render or old result.

    EDL joins are authoritative edit operations, even when no visible scene
    change is detected. Other candidates can be inherited source cuts or changes
    caused by overlays/transforms; source-origin classification remains tentative.
    """
    if timeline["source_sha256"] != render_result["sha256"]:
        raise ValueError("shot_timeline_render_sha_mismatch")
    rows = render_result.get("provenance", render_result.get("compiled", {}).get("segments", []))
    fps = render_result.get("fps", render_result.get("compiled", {}).get("fps"))
    if not rows or type(fps) not in (int, float) or fps <= 0:
        raise ValueError("shot_timeline_render_provenance_missing")
    tolerance = 1 / fps if tolerance_s is None else tolerance_s
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("shot_timeline_invalid_join_tolerance")
    for left, right in zip(rows, rows[1:]):
        if abs(left["output_out_s"] - right["output_in_s"]) > 1e-6:
            raise ValueError("shot_timeline_noncontiguous_edl")
    joins = [{"join_index": index, "output_time_s": right["output_in_s"],
              "left_source": _source_mapping(left, left["output_out_s"], fps),
              "right_source": _source_mapping(right, right["output_in_s"], fps),
              "detected_cut_indices": []}
             for index, (left, right) in enumerate(zip(rows, rows[1:]))]
    annotated = []
    for cut in timeline["cuts"]:
        output_time = cut["source_time_s"]
        nearby = [join for join in joins if abs(join["output_time_s"] - output_time) <= tolerance + 1e-9]
        join = min(nearby, key=lambda item: abs(item["output_time_s"] - output_time), default=None)
        row = next((row for row in rows if row["output_in_s"] <= output_time < row["output_out_s"]), None)
        if row is None:
            raise ValueError("shot_timeline_cut_outside_edl")
        annotation = {**cut, "output_time_s": output_time,
                      "origin_classification": "edl_join_candidate" if join else "inherited_source_candidate",
                      "source_mapping": _source_mapping(row, output_time, fps),
                      "matched_join_index": join["join_index"] if join else None}
        if join is not None:
            join["detected_cut_indices"].append(cut["cut_index"])
        annotated.append(annotation)
    return {"version": "edl_scene_association_v1", "render_sha256": render_result["sha256"],
            "timeline_record_sha256": timeline.get("record_sha256"), "tolerance_s": tolerance,
            "edl_segment_count": len(rows), "edl_join_count": len(joins),
            "detected_cut_candidate_count": len(annotated), "joins": joins, "cuts": annotated,
            "limitations": ["EDL segment count is not shot count; each segment can contain several source cuts.",
                            "Join proximity is an association, not proof of a particular editing technique.",
                            "Within-segment candidates may be inherited cuts, flashes, or generated visual changes.",
                            "Source mappings use EDL seconds/speed and frame rounding, not decoded original-film PTS."]}
