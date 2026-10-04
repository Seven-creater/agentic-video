"""Render model-selected original-media seconds with immutable provenance.

This module executes an EDL; it does not choose story content or source ranges.
Source seconds are relative to the beginning of the media, never output-FPS
frame numbers. The caller validates that each range was actually inspected.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess


RENDER_VERSION = "library_render_v1"


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run(args, *, cwd=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, capture_output=True)
    if result.returncode:
        raise RuntimeError("library_media_tool_failed:" + result.stderr.decode(errors="replace")[-3000:])
    return result.stdout


def _probe(path, ffprobe):
    return json.loads(_run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path]))


def _number(value, lo, hi, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError("library_render_invalid_number:" + name)
    return float(value)


def _audio_index(metadata, requested, name):
    if requested is None:
        return None
    if type(requested) is not int or not any(
            s.get("index") == requested and s.get("codec_type") == "audio" for s in metadata["streams"]):
        raise ValueError("library_render_invalid_audio_stream:" + name)
    return requested


def _source_duration(metadata):
    duration = metadata.get("format", {}).get("duration")
    if duration is None:
        durations = [float(s["duration"]) for s in metadata["streams"] if s.get("duration") is not None]
        duration = max(durations, default=0)
    return _number(float(duration), 0.000001, math.inf, "measured_duration_s")


def _seek_args(path, start, end):
    # Accurate input seek decodes the preceding keyframe and discards frames
    # before this bounded pre-roll. trim then works on the remaining local PTS.
    seek = max(0.0, start - 2.0)
    return ["-ss", f"{seek:.9f}", "-t", f"{end - seek + 0.25:.9f}", "-i", str(path)], start - seek, end - seek, seek


def compile_library_plan(catalog, plan, *, fps=24, width=720, height=1280):
    """Validate seconds and freeze model choices; no creative range adjustment."""
    sources = catalog.get("sources") if isinstance(catalog, dict) else catalog
    if not isinstance(sources, list) or not isinstance(plan, dict):
        raise ValueError("library_render_invalid_inputs")
    mapping = {}
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            raise ValueError("library_render_invalid_source")
        if source["source_id"] in mapping:
            raise ValueError("library_render_duplicate_source_id")
        mapping[source["source_id"]] = source
    if type(fps) is not int or not 1 <= fps <= 120:
        raise ValueError("library_render_invalid_fps")
    if any(type(n) is not int or n < 2 or n > 4096 or n % 2 for n in (width, height)):
        raise ValueError("library_render_invalid_canvas")
    rows = plan.get("segments")
    if not isinstance(rows, list) or not rows:
        raise ValueError("library_render_empty_segments")
    mode = plan.get("audio_mode", "source")
    if mode not in {"reference", "source", "mix", "silent"}:
        raise ValueError("library_render_invalid_audio_mode")
    source_gain = _number(plan.get("source_gain_db", 0), -60, 12, "source_gain_db")
    reference_gain = _number(plan.get("reference_gain_db", -9), -60, 12, "reference_gain_db")
    compiled, cursor = [], 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("source_id") not in mapping:
            raise ValueError("library_render_unknown_source")
        source = mapping[row["source_id"]]
        duration = _number(source.get("duration_s"), 0.000001, math.inf, "duration_s")
        start = _number(row.get("source_in_s"), 0, duration, "source_in_s")
        end = _number(row.get("source_out_s"), 0, duration, "source_out_s")
        speed = _number(row.get("speed", 1), 0.5, 2, "speed")
        if end <= start:
            raise ValueError("library_render_empty_or_out_of_range")
        frames = round((end - start) / speed * fps)
        if frames < 1:
            raise ValueError("library_render_slice_shorter_than_output_frame")
        look, framing = row.get("look", "none"), row.get("framing", "fit")
        if look not in {"none", "grayscale"} or framing not in {"fit", "crop"}:
            raise ValueError("library_render_unsupported_transform")
        window_id = row.get("window_id")
        if not isinstance(window_id, str) or not window_id:
            raise ValueError("library_render_missing_window_id")
        compiled.append({"segment_index": index, "source_id": row["source_id"], "window_id": window_id,
                         "source_path": str(Path(source["path"]).resolve()), "source_sha256": source["sha256"],
                         "source_in_s": start, "source_out_s": end, "speed": speed,
                         "look": look, "framing": framing, "frames": frames,
                         "output_in_s": cursor / fps, "output_out_s": (cursor + frames) / fps,
                         "duration_s": frames / fps,
                         "source_audio_stream_index": source.get("audio_stream_index")})
        cursor += frames
    return {"renderer_version": RENDER_VERSION, "plan": plan, "segments": compiled,
            "fps": fps, "width": width, "height": height, "total_frames": cursor,
            "duration_s": cursor / fps, "audio_mode": mode,
            "source_gain_db": source_gain, "reference_gain_db": reference_gain}


def _cached_files(marker, identity, files):
    if not marker.exists():
        return False
    prior = _read(marker)
    if prior.get("input_identity") != identity:
        raise ValueError("library_render_cache_input_changed")
    for label, path in files.items():
        if not path.is_file() or _sha(path) != prior.get("hashes", {}).get(label):
            raise ValueError("library_render_cache_artifact_changed:" + label)
    return True


def _render_slice(row, directory, compiled, ffmpeg, *, need_audio):
    stem = f"segment_{row['segment_index']:03d}"
    picture, audio, marker = directory / (stem + ".mp4"), directory / (stem + ".wav"), directory / (stem + ".json")
    files = {"picture": picture, **({"audio": audio} if need_audio else {})}
    identity = _identity({"row": row, "fps": compiled["fps"], "width": compiled["width"],
                          "height": compiled["height"], "need_audio": need_audio, "version": RENDER_VERSION})
    if _cached_files(marker, identity, files):
        return picture, audio
    input_args, start, end, seek = _seek_args(row["source_path"], row["source_in_s"], row["source_out_s"])
    width, height, fps = compiled["width"], compiled["height"], compiled["fps"]
    vf = [f"trim=start={start:.9f}:end={end:.9f}", f"setpts=(PTS-STARTPTS)/{row['speed']:.9f}", f"fps={fps}"]
    if row["framing"] == "fit":
        vf += [f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2",
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"]
    else:
        vf += [f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2",
               f"crop={width}:{height}"]
    vf += ["setsar=1"]
    if row["look"] == "grayscale":
        vf.append("hue=s=0")
    vf += [f"tpad=stop_mode=clone:stop_duration={1/fps:.9f}", f"trim=end_frame={row['frames']}"]
    _run([ffmpeg, "-y", "-v", "error", *input_args, "-map", "0:v:0", "-an", "-vf", ",".join(vf),
          "-frames:v", row["frames"], "-c:v", "libx264", "-preset", "fast", "-crf", "20",
          "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", picture])
    if need_audio:
        duration = row["duration_s"]
        if row["source_audio_stream_index"] is None:
            _run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                  "-t", f"{duration:.9f}", "-c:a", "pcm_s16le", audio])
        else:
            af = [f"atrim=start={start:.9f}:end={end:.9f}", "asetpts=PTS-STARTPTS",
                  f"atempo={row['speed']:.9f}", "aresample=48000",
                  "aformat=sample_fmts=s16:channel_layouts=stereo",
                  f"apad=whole_dur={duration:.9f}", f"atrim=duration={duration:.9f}"]
            _run([ffmpeg, "-y", "-v", "error", *input_args, "-map", f"0:{row['source_audio_stream_index']}",
                  "-vn", "-af", ",".join(af), "-c:a", "pcm_s16le", audio])
    _write(marker, {"input_identity": identity, "source_seek_s": seek,
                    "hashes": {label: _sha(path) for label, path in files.items()}})
    return picture, audio


def _concat(paths, list_path, output, ffmpeg, *, audio=False):
    # Generated names alone enter this ffconcat file; external paths never do.
    list_path.write_text("".join(f"file '{path.name}'\n" for path in paths), encoding="utf-8")
    options = ["-vn", "-c:a", "pcm_s16le"] if audio else ["-an", "-c:v", "copy"]
    _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_path.name,
          *options, output.name], cwd=list_path.parent)


def render_library_video(catalog, plan, output_dir, reference_path=None, *,
                         fps=24, width=720, height=1280, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Return final video and provenance; an existing directory binds one EDL.

    audio_stream_index refers to the global FFprobe stream index. The caller
    chooses Mandarin and any reference-audio interval; no language guessing or
    music separation is performed here. Missing source audio produces explicit
    silence for that interval. J/L cuts are not implemented by this renderer.
    """
    compiled = compile_library_plan(catalog, plan, fps=fps, width=width, height=height)
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    used_sources = {}
    for row in compiled["segments"]:
        source_id = row["source_id"]
        if source_id not in used_sources:
            path = Path(row["source_path"])
            if not path.is_file() or _sha(path) != row["source_sha256"]:
                raise ValueError("library_render_source_changed:" + source_id)
            metadata = _probe(path, ffprobe)
            if not any(s.get("codec_type") == "video" for s in metadata["streams"]):
                raise ValueError("library_render_source_has_no_video:" + source_id)
            used_sources[source_id] = {"path": str(path), "sha256": row["source_sha256"],
                                       "duration_s": _source_duration(metadata),
                                       "audio_stream_index": _audio_index(metadata, row["source_audio_stream_index"], source_id)}
        if row["source_out_s"] > used_sources[source_id]["duration_s"] + 0.0001:
            raise ValueError("library_render_source_range_outside_actual_media")
    mode, total = compiled["audio_mode"], compiled["duration_s"]
    reference = None
    if mode in {"reference", "mix"}:
        if reference_path is None or not Path(reference_path).is_file():
            raise ValueError("library_render_reference_audio_missing")
        reference_path = Path(reference_path).resolve()
        metadata = _probe(reference_path, ffprobe)
        region = plan.get("reference_audio", {})
        if not isinstance(region, dict):
            raise ValueError("library_render_invalid_reference_audio")
        default_index = next((s["index"] for s in metadata["streams"] if s.get("codec_type") == "audio"), None)
        stream = _audio_index(metadata, region.get("stream_index", default_index), "reference")
        if stream is None:
            raise ValueError("library_render_reference_has_no_audio")
        duration = _source_duration(metadata)
        start = _number(region.get("start_s", 0), 0, duration, "reference_audio.start_s")
        end = _number(region.get("end_s", duration), 0, duration, "reference_audio.end_s")
        if end <= start or type(region.get("loop", False)) is not bool:
            raise ValueError("library_render_invalid_reference_audio_range")
        reference = {"path": str(reference_path), "sha256": _sha(reference_path), "stream_index": stream,
                     "start_s": start, "end_s": end, "loop": region.get("loop", False)}
    frozen = {"compiled": compiled, "sources": used_sources, "reference_audio": reference}
    identity = _identity(frozen)
    input_marker, result_marker = directory / "render_input.json", directory / "render_result.json"
    if input_marker.exists():
        if _identity(_read(input_marker)) != identity:
            raise ValueError("library_render_input_changed_use_new_output_directory")
    else:
        _write(input_marker, frozen)
    result_path = directory / "final.mp4"
    if _cached_files(result_marker, identity, {"final": result_path}):
        return _read(result_marker)
    pictures, audios = [], []
    for row in compiled["segments"]:
        picture, audio = _render_slice(row, directory, compiled, ffmpeg, need_audio=mode in {"source", "mix"})
        pictures.append(picture)
        audios.append(audio)
    picture = directory / "picture.mp4"
    _concat(pictures, directory / "picture_concat.txt", picture, ffmpeg)
    mux_args = [ffmpeg, "-y", "-v", "error", "-i", picture]
    filters, next_input = [], 1
    if mode in {"source", "mix"}:
        source_audio = directory / "source_audio.wav"
        _concat(audios, directory / "audio_concat.txt", source_audio, ffmpeg, audio=True)
        mux_args += ["-i", source_audio]
        filters.append(f"[{next_input}:a:0]volume={compiled['source_gain_db']}dB,apad=whole_dur={total:.9f},atrim=duration={total:.9f}[source]")
        next_input += 1
    if reference:
        reference_audio = directory / "reference_audio.wav"
        args, start, end, _ = _seek_args(reference["path"], reference["start_s"], reference["end_s"])
        _run([ffmpeg, "-y", "-v", "error", *args, "-map", f"0:{reference['stream_index']}", "-vn", "-af",
              f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,aresample=48000",
              "-ac", "2", "-c:a", "pcm_s16le", reference_audio])
        if reference["loop"]:
            mux_args += ["-stream_loop", "-1"]
        mux_args += ["-i", reference_audio]
        filters.append(f"[{next_input}:a:0]volume={compiled['reference_gain_db']}dB,apad=whole_dur={total:.9f},atrim=duration={total:.9f}[reference]")
    if mode == "mix":
        filters.append(f"[source][reference]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,atrim=duration={total:.9f}[audio]")
        audio_label = "[audio]"
    else:
        audio_label = "[source]" if mode == "source" else "[reference]"
    mux_args += ["-map", "0:v:0", "-c:v", "copy"]
    if mode == "silent":
        mux_args += ["-an"]
    else:
        mux_args += ["-filter_complex", ";".join(filters), "-map", audio_label, "-c:a", "aac", "-b:a", "160k"]
    temporary = directory / "final.part.mp4"
    _run([*mux_args, "-t", f"{total:.9f}", "-movflags", "+faststart", temporary])
    metadata = _probe(temporary, ffprobe)
    measured = _source_duration(metadata)
    if abs(measured - total) > max(0.075, 1 / fps):
        raise ValueError("library_render_output_duration_mismatch")
    video_stream = next(s for s in metadata["streams"] if s.get("codec_type") == "video")
    if (int(video_stream.get("nb_frames", compiled["total_frames"])) != compiled["total_frames"]
            or video_stream.get("width") != width or video_stream.get("height") != height):
        raise ValueError("library_render_output_video_mismatch")
    if any(s.get("codec_type") == "audio" for s in metadata["streams"]) != (mode != "silent"):
        raise ValueError("library_render_output_audio_mismatch")
    os.replace(temporary, result_path)
    result = {"input_identity": identity, "renderer_version": RENDER_VERSION,
              "rendered_path": str(result_path), "manifest_path": str(result_marker),
              "sha256": _sha(result_path), "hashes": {"final": _sha(result_path)},
              "duration_s": total, "measured_duration_s": measured, "fps": fps,
              "audio_mode": mode, "reference_audio": reference, "sources": used_sources,
              "provenance": compiled["segments"], "limitations": [
                  "Source cuts select presentation timestamps in the original media; output duration is rounded to output frames.",
                  "Reference audio is the selected original soundtrack, not an automatically separated music stem.",
                  "J/L cuts and automatic dialogue/music separation are not implemented."]}
    _write(result_marker, result)
    return result
