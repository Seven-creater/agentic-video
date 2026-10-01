"""Execute model-selected real-media intervals. No hidden creative selection or time stretching."""
from __future__ import annotations

import math
from pathlib import Path
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


def compile_plan(plan, sources, music_region, reference_duration):
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
    return {"segments": compiled, "duration_s": total, "total_frames": cursor,
            "music": music, "music_region": music_region,
            "reference_duration_s": reference_duration,
            "duration_ratio": total / reference_duration}


def canvas(reference_probe):
    stream = next(s for s in reference_probe["streams"] if s["codec_type"] == "video")
    w, h = stream["width"], stream["height"]
    scale = min(1.0, 1280 / max(w, h), 768 / min(w, h))
    return max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)


def review_copy(source, target):
    command(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf",
             "scale=640:640:force_original_aspect_ratio=decrease,fps=24", "-c:v", "libx264",
             "-preset", "fast", "-crf", "29", "-c:a", "aac", "-b:a", "64k", str(target)])
    if target.stat().st_size >= 10_000_000:
        raise ValueError("review_copy_exceeds_10mb")


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
