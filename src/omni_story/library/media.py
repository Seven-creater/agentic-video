"""Source-bound media observations for the independent movie-library route.

Sparse sheets are navigation evidence, never continuous playback or edit ranges.
Window proxy time maps to the original file as source_offset_s + proxy_time_s.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess

MEDIA_EXTENSIONS = {".mp4", ".mkv", ".mov", ".m4v", ".webm", ".avi"}
MEDIA_POLICY = "library-media-v1"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(path)


def _run(args: list[str], *, timeout: int = 900) -> str:
    completed = subprocess.run(args, capture_output=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-4000:])
    return completed.stdout.decode("utf-8", errors="replace")


def probe_media(path: str | Path) -> dict:
    path = Path(path).resolve(strict=True)
    result = json.loads(_run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ], timeout=120))
    videos = [row for row in result["streams"] if row.get("codec_type") == "video"
              and not row.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("source_has_no_video")
    duration = float(result["format"].get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source_duration_invalid")
    result["duration_s"] = duration
    result["video_stream_index"] = int(videos[0]["index"])
    return result


def select_audio_stream(streams: list[dict]) -> int | None:
    """Prefer explicit Mandarin titles, then Chinese language, then default audio."""
    audio = [row for row in streams if row.get("codec_type") == "audio"]
    if not audio:
        return None
    def score(row: dict) -> tuple[int, int, int]:
        tags = {str(k).lower(): str(v).lower() for k, v in row.get("tags", {}).items()}
        title = tags.get("title", "")
        mandarin_title = any(word in title for word in
                             ("国语", "普通话", "中文", "mandarin", "chinese"))
        chinese_language = tags.get("language", "") in {"chi", "zho", "cmn", "zh"}
        return (3 if mandarin_title else 2 if chinese_language else 0,
                int(row.get("disposition", {}).get("default", 0)), -int(row["index"]))
    return int(max(audio, key=score)["index"])


def inventory_sources(paths_or_directory, output_dir: str | Path) -> dict:
    """Hash actual files once per inventory pass; retain raw streams and metadata."""
    if isinstance(paths_or_directory, (str, Path)):
        location = Path(paths_or_directory)
        paths = sorted(location.iterdir()) if location.is_dir() else [location]
    else:
        paths = sorted(Path(p) for p in paths_or_directory)
    sources = []
    for item in paths:
        if not item.is_file() or item.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        path = item.resolve(strict=True)
        before = path.stat()
        measured = probe_media(path)
        digest = sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("source_changed_during_inventory:" + str(path))
        selected = select_audio_stream(measured["streams"])
        sources.append({
            "source_id": "src_" + digest[:16], "path": str(path), "sha256": digest,
            "duration_s": measured["duration_s"], "video_stream_index": measured["video_stream_index"],
            "audio_stream_index": selected, "streams": measured["streams"],
            "format": measured["format"], "size_bytes": after.st_size, "mtime_ns": after.st_mtime_ns,
            "audio_selection_policy": "mandarin_title_then_language_then_default",
        })
    if not sources:
        raise ValueError("library_contains_no_video_files")
    result = {"policy": MEDIA_POLICY, "sources": sources}
    _write_json(Path(output_dir) / "inventory.json", result)
    return result


def verify_source(source: dict) -> Path:
    """Inventory hashes bind content; stat changes require a new inventory pass."""
    path = Path(source["path"]).resolve(strict=True)
    current = path.stat()
    if "size_bytes" in source and "mtime_ns" in source:
        if (current.st_size, current.st_mtime_ns) != (source["size_bytes"], source["mtime_ns"]):
            raise ValueError("source_changed_since_inventory:" + str(path))
    elif sha256_file(path) != source["sha256"]:
        raise ValueError("source_sha_mismatch:" + str(path))
    return path


def validate_window(source: dict, start_s: float, end_s: float) -> tuple[float, float]:
    start, end = float(start_s), float(end_s)
    if not all(math.isfinite(value) for value in (start, end)):
        raise ValueError("source_range_not_finite")
    if start < 0 or end <= start or end > float(source["duration_s"]) + 0.001:
        raise ValueError("source_range_out_of_bounds")
    return start, min(end, float(source["duration_s"]))


def _cache_folder(cache_dir: str | Path, spec: dict) -> Path:
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:20]
    folder = Path(cache_dir).resolve() / key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cached(folder: Path, spec: dict) -> dict | None:
    record = folder / "lineage.json"
    if not record.is_file():
        return None
    saved = json.loads(record.read_text(encoding="utf-8"))
    if saved.get("spec") != spec:
        raise ValueError("media_cache_spec_mismatch")
    if not Path(saved["path"]).is_file() or sha256_file(saved["path"]) != saved["sha256"]:
        raise ValueError("media_cache_missing_or_modified")
    for frame in saved.get("frames", []):
        if not Path(frame["path"]).is_file() or sha256_file(frame["path"]) != frame["sha256"]:
            raise ValueError("media_cache_frame_missing_or_modified")
    return saved


def prepare_window(source: dict, start_s: float, end_s: float, cache_dir: str | Path, *,
                   max_bytes: int = 7_500_000, max_long_edge: int = 720, fps: int = 12) -> dict:
    """Create an actual continuous H.264/AAC proxy with explicit original-time mapping."""
    path = verify_source(source)
    start, end = validate_window(source, start_s, end_s)
    if max_bytes <= 0 or max_long_edge < 2 or fps <= 0:
        raise ValueError("proxy_options_invalid")
    spec = {"policy": MEDIA_POLICY, "kind": "continuous_window", "source_sha256": source["sha256"],
            "source_start_s": start, "source_end_s": end, "audio_stream_index": source.get("audio_stream_index"),
            "max_bytes": int(max_bytes), "max_long_edge": int(max_long_edge), "fps": int(fps)}
    folder = _cache_folder(cache_dir, spec)
    saved = _cached(folder, spec)
    if saved is not None:
        return saved
    audio_index = source.get("audio_stream_index")
    audio_bitrate = 64_000 if audio_index is not None else 0
    bitrate = min(1_200_000, math.floor(max_bytes * 8 * 0.80 / (end - start)) - audio_bitrate)
    if bitrate < 40_000:
        raise ValueError("proxy_range_too_long_for_size_budget")
    partial = folder / "window.part.mp4"
    target = folder / "window.mp4"
    scale = (f"scale=w='if(gte(iw,ih),min({max_long_edge},iw),-2)':"
             f"h='if(gte(iw,ih),-2,min({max_long_edge},ih))',fps={fps}")
    for attempt in range(3):
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}",
                "-i", str(path), "-t", f"{end - start:.6f}", "-map", f"0:{source.get('video_stream_index', 0)}",
                "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-b:v", str(bitrate), "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2)]
        if audio_index is not None:
            args += ["-map", f"0:{audio_index}", "-c:a", "aac", "-b:a", "64k", "-ac", "1"]
        else:
            args += ["-an"]
        args += ["-sn", "-dn", "-map_metadata", "-1", "-movflags", "+faststart", str(partial)]
        _run(args)
        if partial.stat().st_size < max_bytes:
            break
        bitrate = max(40_000, int(bitrate * 0.65))
    else:
        raise ValueError("proxy_exceeds_size_budget")
    measured = probe_media(partial)
    if abs(measured["duration_s"] - (end - start)) > max(0.20, 2 / fps):
        raise ValueError("proxy_duration_mapping_invalid")
    if any(s.get("codec_type") == "audio" for s in measured["streams"]) != (audio_index is not None):
        raise ValueError("proxy_audio_stream_lost")
    verify_source(source)
    partial.replace(target)
    result = {"spec": spec, "kind": "continuous_window", "path": str(target), "sha256": sha256_file(target),
              "source_id": source["source_id"], "source_path": str(path), "source_sha256": source["sha256"],
              "source_start_s": start, "source_end_s": end, "source_offset_s": start,
              "media_duration_s": measured["duration_s"], "duration_s": measured["duration_s"],
              "size_bytes": target.stat().st_size, "audio_stream_index": audio_index,
              "audio_present": audio_index is not None, "metadata": measured,
              "time_mapping": "source_time_s = source_offset_s + proxy_time_s",
              "mapping_tolerance_s": max(0.10, 1 / fps)}
    _write_json(folder / "lineage.json", result)
    return result


def create_contact_sheet(source: dict, start_s: float, end_s: float, cache_dir: str | Path, *,
                         frame_count: int = 18, columns: int = 6, tile_width: int = 320) -> dict:
    """Uniform sparse navigation samples, visibly labelled with ORIGINAL timestamps."""
    from PIL import Image, ImageDraw, ImageFont

    path = verify_source(source)
    start, end = validate_window(source, start_s, end_s)
    if frame_count <= 0 or columns <= 0 or tile_width < 32:
        raise ValueError("contact_sheet_options_invalid")
    spec = {"policy": MEDIA_POLICY, "kind": "sparse_contact_sheet", "source_sha256": source["sha256"],
            "source_start_s": start, "source_end_s": end, "frame_count": frame_count,
            "columns": columns, "tile_width": tile_width}
    folder = _cache_folder(cache_dir, spec)
    saved = _cached(folder, spec)
    if saved is not None:
        return saved
    # Midpoint strata avoid end-of-file seeks while sampling the whole requested range.
    times = [start + (index + 0.5) * (end - start) / frame_count for index in range(frame_count)]
    height = math.ceil(tile_width * 9 / 16)
    label_height = 32
    sheet = Image.new("RGB", (columns * tile_width, math.ceil(frame_count / columns) * (height + label_height)), "black")
    font = ImageFont.load_default(size=17)
    frames = []
    for index, timestamp in enumerate(times):
        frame_path = folder / f"frame_{index:03d}.jpg"
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{timestamp:.6f}",
              "-i", str(path), "-map", f"0:{source.get('video_stream_index', 0)}", "-frames:v", "1",
              "-vf", f"scale={tile_width}:{height}:force_original_aspect_ratio=decrease,pad={tile_width}:{height}:(ow-iw)/2:(oh-ih)/2",
              "-q:v", "3", str(frame_path)], timeout=120)
        frame_id = f"{source['source_id']}_{timestamp:.3f}_{index:03d}"
        with Image.open(frame_path) as picture:
            sheet.paste(picture.convert("RGB"), ((index % columns) * tile_width, (index // columns) * (height + label_height)))
        origin = ((index % columns) * tile_width + 5, (index // columns) * (height + label_height) + height + 6)
        ImageDraw.Draw(sheet).text(origin, f"F{index:02d}  original {timestamp:.2f}s", font=font, fill="white")
        frames.append({"frame_id": frame_id, "sheet_label": f"F{index:02d}", "source_time_s": timestamp,
                       "path": str(frame_path), "sha256": sha256_file(frame_path)})
    verify_source(source)
    target = folder / "contact_sheet.jpg"
    partial = folder / "contact_sheet.part.jpg"
    sheet.save(partial, "JPEG", quality=85)
    partial.replace(target)
    result = {"spec": spec, "kind": "sparse_contact_sheet", "path": str(target), "sha256": sha256_file(target),
              "source_id": source["source_id"], "source_path": str(path), "source_sha256": source["sha256"],
              "source_start_s": start, "source_end_s": end, "frames": frames,
              "continuous": False, "audio_present": False,
              "evidence_limit": "Sparse frames only; not proof of continuous action, absence, or exact edit points."}
    _write_json(folder / "lineage.json", result)
    return result
