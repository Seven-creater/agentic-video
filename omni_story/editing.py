"""Execute model-selected real-media intervals. No hidden creative selection or time stretching."""
from __future__ import annotations

import math
from pathlib import Path
import re
import subprocess

from .pipeline import json_sha, probe, sha, write

FPS = 24


def command(args, *, cwd=None):
    proc = subprocess.run(args, cwd=cwd, capture_output=True)
    if proc.returncode:
        raise RuntimeError("media_tool_failed:" + proc.stderr.decode(errors="replace")[-2000:])


def number(value, lo, hi, field):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError("edit_number_invalid:" + field)
    return value


def compile_plan(plan, sources, music_region, reference_duration, *, transfer=None):
    catalog = {s["material_id"]: s for s in sources}
    rows = plan.get("segments")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("edit_segment_count")
    compiled, cursor, used = [], 0, {}
    for row in rows:
        source = catalog.get(row.get("material_id"))
        if source is None:
            raise ValueError("edit_unknown_material")
        start = number(row.get("in_s"), 0, source["measured_duration_s"], "in_s")
        end = number(row.get("out_s"), 0, source["measured_duration_s"], "out_s")
        a, b = round(start * FPS), round(end * FPS)
        if not 0 <= a < b <= round(source["measured_duration_s"] * FPS):
            raise ValueError("edit_empty_or_out_of_range")
        for u, v in used.setdefault(source["material_id"], []):
            if max(a, u) < min(b, v):
                raise ValueError("edit_duplicate_source_interval")
        used[source["material_id"]].append((a, b))
        speed = number(row.get("speed"), 0.5, 2, "speed")
        frames = round((b - a) / speed)
        fi = round(number(row.get("fade_in_s"), 0, frames / FPS, "fade_in_s") * FPS)
        fo = round(number(row.get("fade_out_s"), 0, frames / FPS, "fade_out_s") * FPS)
        if frames < 1 or fi + fo > frames:
            raise ValueError("edit_fades_overlap")
        look = row.get("look", {"type": "none"})
        if not isinstance(look, dict) or look.get("type") not in {"none", "grayscale", "color_reveal"}:
            raise ValueError("edit_unsupported_look")
        if look["type"] == "color_reveal":
            when = number(look.get("at_s"), 0, frames / FPS, "color_reveal_at")
            length = number(look.get("duration_s"), 1 / FPS, frames / FPS, "color_reveal_duration")
            if when + length > frames / FPS:
                raise ValueError("edit_color_reveal_outside_slice")
        compiled.append({**row, "source_path": source["path"], "source_sha256": source["sha256"],
                         "source_start_frame": a, "source_end_frame": b, "frames": frames,
                         "fade_in_frames": fi, "fade_out_frames": fo,
                         "timeline_start_s": cursor / FPS, "timeline_end_s": (cursor + frames) / FPS})
        cursor += frames
    total = cursor / FPS
    if total > 180:
        raise ValueError("edit_resource_limit_180_seconds")
    music = plan.get("music", {})
    if type(music.get("enabled")) is not bool or music.get("mode") not in ("trim", "loop"):
        raise ValueError("edit_music_policy")
    if music["enabled"] and music_region is None:
        raise ValueError("edit_music_not_observed")
    number(music.get("gain_db"), -30, 0, "music_gain")
    number(music.get("fade_in_s"), 0, total, "music_fade_in")
    number(music.get("fade_out_s"), 0, total, "music_fade_out")
    if transfer is not None:
        from .reference import validate_mapping
        validate_mapping(plan.get("style_mapping"), transfer, len(rows))
    return {"segments": compiled, "duration_s": total, "total_frames": cursor,
            "music": music, "music_region": music_region,
            "reference_duration_s": reference_duration,
            "duration_ratio": total / reference_duration}


def edit_metrics(compiled, sources):
    """Report real source retention and continuous runs, not JSON row count as cut count."""
    rows = compiled["segments"]
    runs = []
    for i, row in enumerate(rows):
        previous = rows[i - 1] if i else None
        contiguous = (previous is not None and previous["material_id"] == row["material_id"]
            and previous["source_end_frame"] == row["source_start_frame"]
            and previous["speed"] == row["speed"]
            and previous.get("look", {"type": "none"}) == row.get("look", {"type": "none"})
            and row.get("look", {"type": "none"})["type"] != "color_reveal"
            and not previous["fade_out_frames"] and not row["fade_in_frames"])
        if contiguous:
            runs[-1]["segment_indices"].append(i)
            runs[-1]["source_end_s"] = row["source_end_frame"] / FPS
            runs[-1]["timeline_end_s"] = row["timeline_end_s"]
        else:
            runs.append({"segment_indices": [i], "material_id": row["material_id"],
                "source_start_s": row["source_start_frame"] / FPS,
                "source_end_s": row["source_end_frame"] / FPS,
                "timeline_start_s": row["timeline_start_s"], "timeline_end_s": row["timeline_end_s"]})
    retained = {s["material_id"]: sum((r["source_end_frame"] - r["source_start_frame"]) / FPS
        for r in rows if r["material_id"] == s["material_id"]) for s in sources}
    return {"segment_rows": len(rows), "continuous_source_runs": runs,
        "effective_edit_boundaries": len(runs) - 1,
        "boundary_scope": "editor joins only; internal camera cuts not measured",
        "duration_s": compiled["duration_s"], "reference_duration_ratio": compiled["duration_ratio"],
        "retained_source_seconds": retained,
        "source_retention_ratio": sum(retained.values()) / sum(s["measured_duration_s"] for s in sources),
        "slice_durations_s": [r["frames"] / FPS for r in rows],
        "policy": "measurement_for_Omni_not_a_quality_gate"}


def local_view(source, target, start, end):
    """Original sound/picture together; offsets retained by the caller, no effect synthesis."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    command(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-ss", str(start), "-t", str(end-start),
        "-vf", "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24",
        "-c:v", "libx264", "-crf", "26", "-c:a", "aac", str(target)])
    if target.stat().st_size >= 10_000_000:
        raise ValueError("local_review_exceeds_10mb")


def canvas(reference_probe):
    stream = next(s for s in reference_probe["streams"] if s["codec_type"] == "video")
    w, h = stream["width"], stream["height"]
    scale = min(1.0, 1280 / max(w, h), 768 / min(w, h))
    return max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)


def review_copy(source, target):
    command(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf",
             "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24", "-c:v", "libx264",
             "-preset", "fast", "-crf", "29", "-c:a", "aac", "-b:a", "64k", str(target)])
    if target.stat().st_size >= 10_000_000:
        raise ValueError("review_copy_exceeds_10mb")


def audio_measurements(source):
    """Measured silence is tool evidence for Omni, not a rule that music must fill a film."""
    metadata = probe(source)
    if not any(s["codec_type"] == "audio" for s in metadata["streams"]):
        return {"audio_stream_present": False, "silences": []}
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(source), "-af",
        "silencedetect=noise=-50dB:d=1", "-vn", "-f", "null", "-"], capture_output=True)
    if proc.returncode:
        raise RuntimeError("audio_measurement_failed:" + proc.stderr.decode(errors="replace")[-1000:])
    text = proc.stderr.decode(errors="replace")
    starts = [float(v) for v in re.findall(r"silence_start: ([0-9.]+)", text)]
    ends = [float(v) for v in re.findall(r"silence_end: ([0-9.]+)", text)]
    return {"audio_stream_present": True, "threshold_db": -50, "minimum_duration_s": 1,
            "silences": [{"start_s": a, "end_s": b} for a, b in zip(starts, ends)]}


def render(compiled, directory, reference, reference_probe):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = directory / "final.mp4"
    marker = directory / "render_result.json"
    if marker.exists():
        from .media_backends import load
        prior = load(marker)
        if (sha(result) != prior["sha256"] or prior["compiled_sha256"] != sha(directory / "compiled.json")
                or prior["compiled_identity"] != json_sha(compiled)):
            raise ValueError("render_output_changed")
        return result
    write(directory / "compiled.json", compiled)
    w, h = canvas(reference_probe)
    pieces = []
    for i, row in enumerate(compiled["segments"]):
        source = Path(row["source_path"])
        if sha(source) != row["source_sha256"]:
            raise ValueError("source_changed_after_observation")
        filters = ["fps=24", f"trim=start_frame={row['source_start_frame']}:end_frame={row['source_end_frame']}",
                   f"setpts=(PTS-STARTPTS)/{row['speed']}", "fps=24",
                   f"scale={w}:{h}:force_original_aspect_ratio=decrease", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                   "setsar=1", "tpad=stop_mode=clone:stop=1", f"trim=end_frame={row['frames']}"]
        look = row.get("look", {"type": "none"})
        if look["type"] == "grayscale":
            filters.append("hue=s=0")
        elif look["type"] == "color_reveal":
            filters.append(f"hue=s='clip((t-{look['at_s']})/{look['duration_s']},0,1)'")
        if row["fade_in_frames"]:
            filters.append(f"fade=t=in:start_frame=0:nb_frames={row['fade_in_frames']}")
        if row["fade_out_frames"]:
            filters.append(f"fade=t=out:start_frame={row['frames']-row['fade_out_frames']}:nb_frames={row['fade_out_frames']}")
        part = directory / f"part_{i:02}.mp4"
        command(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-an", "-vf", ",".join(filters),
                 "-frames:v", str(row["frames"]), "-pix_fmt", "yuv420p", "-c:v", "libx264",
                 "-preset", "fast", "-crf", "20", str(part)])
        pieces.append(part)
    (directory / "concat.txt").write_text("".join(f"file '{p.name}'\n" for p in pieces), encoding="utf-8")
    picture = directory / "picture.mp4"
    command(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt",
             "-c", "copy", "picture.mp4"], cwd=directory)
    return mux_music(compiled, picture, directory, reference)


def mux_music(compiled, picture, directory, reference):
    """Copy the existing picture stream unchanged and apply only model-selected audio settings."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = directory / "final.mp4"
    marker = directory / "render_result.json"
    if marker.exists():
        from .media_backends import load
        prior = load(marker)
        if (sha(result) != prior["sha256"] or prior["compiled_sha256"] != sha(directory / "compiled.json")
                or prior["compiled_identity"] != json_sha(compiled)):
            raise ValueError("render_output_changed")
        return result
    write(directory / "compiled.json", compiled)
    music, total = compiled["music"], compiled["duration_s"]
    args = ["ffmpeg", "-y", "-v", "error", "-i", str(picture)]
    if music["enabled"]:
        region = compiled["music_region"]
        audio = directory / "music.wav"
        command(["ffmpeg", "-y", "-v", "error", "-ss", str(region["start_s"]), "-i", str(reference),
                 "-t", str(region["end_s"] - region["start_s"]), "-vn", "-c:a", "pcm_s16le", str(audio)])
        if music["mode"] == "loop":
            args += ["-stream_loop", "-1"]
        args += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
        af = [f"apad=whole_dur={total}", f"atrim=duration={total}", f"volume={music['gain_db']}dB"]
        if music["fade_in_s"]:
            af.append(f"afade=t=in:st=0:d={music['fade_in_s']}")
        if music["fade_out_s"]:
            af.append(f"afade=t=out:st={total-music['fade_out_s']}:d={music['fade_out_s']}")
        args += ["-af", ",".join(af), "-c:a", "aac", "-ar", "48000", "-b:a", "128k"]
    else:
        args += ["-an"]
    args += ["-c:v", "copy", "-t", str(total), "-movflags", "+faststart", str(result)]
    command(args)
    command(["ffmpeg", "-v", "error", "-i", str(result), "-f", "null", "-"])
    measured = probe(result)
    if abs(float(measured["format"]["duration"]) - total) > 2 / FPS:
        raise ValueError("render_duration_mismatch")
    review_copy(result, directory / "review.mp4")
    write(marker, {"path": str(result), "sha256": sha(result), "measured": measured,
                   "compiled_sha256": sha(directory / "compiled.json"), "compiled_identity": json_sha(compiled),
                   "music_tempo_changed": False})
    return result
