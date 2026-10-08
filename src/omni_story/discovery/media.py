"""Whole-file verification and an explicit, recorded <=9 MB analysis derivative."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess

from ..pipeline import probe, sha, write


def inspect(path):
    path = Path(path).resolve()
    measured = probe(path)
    video = next(s for s in measured["streams"] if s["codec_type"] == "video")
    if video.get("width", 0) <= 0 or video.get("height", 0) <= 0:
        raise ValueError("video_dimensions_invalid")
    # ffprobe may accept truncated MP4 metadata; actually decode the full file as well.
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
                    "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"],
                   check=True, capture_output=True, timeout=180)
    return measured


def prepare(path, *, expected_duration=None):
    original = Path(path).resolve()
    measured = inspect(original)
    duration = float(measured["format"]["duration"])
    if expected_duration and abs(duration - expected_duration) > max(0.5, expected_duration * 0.02):
        raise ValueError("download_duration_mismatch")
    original_sha = sha(original)
    analysis = original
    derived = False
    if original.stat().st_size >= 10_000_000:
        analysis = original.with_name("analysis.mp4")
        previous = original.with_name("media_lineage.json")
        if analysis.exists() and previous.exists():
            saved = json.loads(previous.read_text(encoding="utf-8"))
            if saved["original_sha256"] != original_sha or saved["analysis_sha256"] != sha(analysis):
                raise ValueError("analysis_cache_sha_mismatch")
        else:
            audio = any(s["codec_type"] == "audio" for s in measured["streams"])
            bitrate = math.floor(9_000_000 * 8 * 0.94 / duration) - (96_000 if audio else 0)
            if bitrate < 80_000:
                raise ValueError("analysis_bitrate_too_low")
            partial = original.with_name("analysis.part.mp4")
            log = original.with_name("analysis_pass")
            common = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(original),
                      "-map", "0:v:0", "-vf", "scale='if(gte(iw,ih),min(720,iw),-2)':'if(gte(iw,ih),-2,min(720,ih))'",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", str(bitrate),
                      "-passlogfile", str(log)]
            subprocess.run(common + ["-pass", "1", "-an", "-f", "null", "-"],
                           check=True, capture_output=True, timeout=300)
            subprocess.run(common + ["-pass", "2", "-map", "0:a?", "-c:a", "aac", "-b:a", "96k",
                                      "-movflags", "+faststart", str(partial)],
                           check=True, capture_output=True, timeout=300)
            if partial.stat().st_size >= 10_000_000:
                raise ValueError("analysis_exceeds_10mb")
            partial.replace(analysis)
        derived = True
    actual = inspect(analysis) if derived else measured
    if abs(float(actual["format"]["duration"]) - duration) > 0.1:
        raise ValueError("analysis_changed_timeline")
    before_audio = sum(s["codec_type"] == "audio" for s in measured["streams"])
    after_audio = sum(s["codec_type"] == "audio" for s in actual["streams"])
    if before_audio != after_audio:
        raise ValueError("analysis_audio_stream_lost")
    if sha(original) != original_sha:
        raise ValueError("original_changed")
    result = {"original_path": str(original), "original_sha256": original_sha,
              "analysis_path": str(analysis), "analysis_sha256": sha(analysis),
              "derived": derived, "duration_s": duration, "audio_stream_present": bool(before_audio),
              "original_metadata": measured, "analysis_metadata": actual}
    write(original.with_name("media_lineage.json"), result)
    return result


def verify_cached(row):
    for key in ("original", "analysis"):
        path = Path(row[key + "_path"])
        if not path.is_file() or sha(path) != row[key + "_sha256"]:
            raise ValueError("cached_media_missing_or_changed:" + key)
    return row


def restore_downloads(state):
    """Recover a completed download if a crash preceded its candidate-state save."""
    for aid, row in state.data["candidates"].items():
        if row.get("media") or row.get("status") != "observed":
            continue
        original = state.output / "videos" / aid / "video.mp4"
        if not original.is_file():
            continue
        lineage = original.with_name("media_lineage.json")
        try:
            if lineage.is_file():
                saved = json.loads(lineage.read_text(encoding="utf-8"))
                if Path(saved["original_path"]).resolve() != original.resolve():
                    raise ValueError("restored_download_path_mismatch")
                actual_analysis = Path(saved["analysis_path"]).resolve()
                if actual_analysis not in {original.resolve(), original.with_name("analysis.mp4").resolve()}:
                    raise ValueError("restored_analysis_path_mismatch")
                restored = verify_cached(saved)
            else:
                restored = prepare(original, expected_duration=row.get("duration_s"))
            row.update(media=restored, status="verified")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            row.update(status="skipped", failure="restore_media:" + str(exc)[:500])
        state.save()
