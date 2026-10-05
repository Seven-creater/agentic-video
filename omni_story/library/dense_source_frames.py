"""Forward-only sampled image carriers for independently selected source slices.

The normal-speed proxy remains the source scope. The image is a separate,
hash-bound carrier, never continuous footage or a new watched movie window.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

from .media import _run, sha256_file
from .semantic_prompts import explicit_slice_observation_prompt
from .state import write_json


POLICY = "dense_source_frames_v1"
MAX_FRAMES = 36
TARGET_FRAMES_PER_SECOND = 6
TILE_WIDTH = 480
COLUMNS = 4
LABEL_HEIGHT = 38
LIMITATIONS = (
    "Sampled still frames, not continuous playback or full-frame inspection. "
    "Unsampled action, absence, exact event boundaries and cloud image processing remain unverified."
)


def _require(condition, reason):
    if not condition:
        raise ValueError("dense_source_frames:" + reason)


def _proxy_path(proxy):
    path = Path(proxy["path"]).resolve(strict=True)
    _require(proxy.get("kind") == "continuous_window" and proxy.get("spec", {}).get("fps") == 30,
             "normal_30fps_proxy_required")
    _require(sha256_file(path) == proxy["sha256"], "proxy_sha_changed")
    lineage = path.parent / "lineage.json"
    _require(lineage.is_file() and json.loads(lineage.read_text(encoding="utf-8")) == proxy,
             "normal_proxy_lineage_changed")
    duration = proxy["duration_s"]
    _require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
             "duration_invalid")
    for field in ("kind", "source_sha256", "source_start_s", "source_end_s"):
        _require(proxy.get(field) == proxy["spec"].get(field), "proxy_spec_changed")
    _require(abs(duration - (proxy["source_end_s"] - proxy["source_start_s"])) <= .25,
             "proxy_range_duration_changed")
    return path


def _bind_source(segment, source, proxy):
    _require(segment["source_id"] == source["source_id"] == proxy["source_id"]
             and source["sha256"] == proxy["source_sha256"]
             and segment["source_in_s"] == proxy["source_start_s"]
             and segment["source_out_s"] == proxy["source_end_s"], "selected_range_changed")


def _sample_indices(duration):
    count = min(MAX_FRAMES, math.ceil(duration * TARGET_FRAMES_PER_SECOND))
    # Midpoint strata, mapped to decoded proxy frame indices at the recorded 30fps.
    return [math.floor((index + .5) * duration / count * 30) for index in range(count)]


def _carrier(proxy, manifest, manifest_path):
    image = manifest["image"]
    metadata = {"policy": POLICY, "carrier_sha256": image["sha256"],
        "manifest_sha256": sha256_file(manifest_path), "normal_proxy_sha256": proxy["sha256"],
        "source_sha256": proxy["source_sha256"], "source_start_s": proxy["source_start_s"],
        "source_end_s": proxy["source_end_s"], "submitted_source_span_s": proxy["duration_s"],
        "continuous": False, "audio_present": False, "sample_frames": [
            {key: row[key] for key in ("frame_id", "frame_index", "local_time_s", "source_time_s", "sha256")}
            for row in manifest["frames"]], "limitations": LIMITATIONS}
    return {"path": image["path"], "sha256": image["sha256"], "image": True,
        "manifest_path": str(manifest_path), "manifest_sha256": metadata["manifest_sha256"],
        "prompt_metadata": metadata}


def saved_media(proxy):
    """Read and verify an existing carrier without generating or repairing files."""
    from PIL import Image

    proxy_path = _proxy_path(proxy)
    folder = proxy_path.parent / "dense_frames"
    manifest_path = folder / "manifest.json"
    _require(not (folder / "lineage.json").exists(), "carrier_must_not_replace_source_scope")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(manifest.get("policy") == POLICY and manifest.get("normal_proxy") == proxy,
             "manifest_proxy_changed")
    indices = _sample_indices(proxy["duration_s"])
    _require(manifest.get("sampling") == {"strategy": "uniform_midpoint_proxy_frame_indices",
        "proxy_fps": 30, "target_frames_per_second": TARGET_FRAMES_PER_SECOND,
        "max_frames": MAX_FRAMES, "frame_count": len(indices), "continuous": False}, "sampling_changed")
    frames = manifest.get("frames")
    _require(isinstance(frames, list) and len(frames) == len(indices), "frame_count_changed")
    for ordinal, (row, index) in enumerate(zip(frames, indices)):
        path = (folder / f"frame_{ordinal:03d}.jpg").resolve(strict=True)
        local = index / 30
        _require(row["path"] == str(path) and row["frame_id"] == f"F{ordinal:02d}"
                 and row["frame_index"] == index and row["local_time_s"] == local
                 and row["source_time_s"] == proxy["source_start_s"] + local
                 and sha256_file(path) == row["sha256"], "frame_binding_changed")
    image_path = (folder / "sheet.jpg").resolve(strict=True)
    image = manifest["image"]
    _require(image["path"] == str(image_path) and image["sha256"] == sha256_file(image_path)
             and image["size_bytes"] == image_path.stat().st_size and image["size_bytes"] < 5_000_000,
             "image_binding_changed")
    with Image.open(image_path) as sheet:
        _require(sheet.size == (image["width"], image["height"])
                 and 0 < sheet.width < 6000 and 0 < sheet.height < 6000,
                 "image_dimensions_changed")
    _require(manifest.get("layout") == {"tile_width": TILE_WIDTH, "columns": COLUMNS,
        "label_height": LABEL_HEIGHT, "labels": "frame_id and normal_proxy_local_seconds"}
        and manifest.get("limitations") == LIMITATIONS, "carrier_policy_changed")
    return _carrier(proxy, manifest, manifest_path)


def _prompt(segment, source, proxy, carrier):
    _bind_source(segment, source, proxy)
    # Keep the current typed output contract, replacing its continuous-video intro.
    contract = explicit_slice_observation_prompt(segment, source, proxy).split("\n", 3)[3]
    return """你是独立无声源片事实观察员，没有创作计划、人物猜测、参考主题或目标答案。
当前实际输入是一张带F编号和正常代理局部秒标签的时序采样联系表，不是连续视频。
图片由同一正常速度30fps精切代理按时间均匀采样；标签属于证据导航，不是电影画内文字。
样本间还有未提交的帧，不能声称逐帧、连续或完整观看。云端图片处理方式仍未知。
禁止补出采样帧没有显示的动作、结果、动机、身份或帧间过程；未采到不能证明absence或事件未发生。
静态位置或姿态可以写可见事实；多帧直接显示的姿态/位置变化可写visual_action，列出F编号和实际前后状态。
单帧姿态不能证明完整动作；未显示的连续路径、接触、动作因果只能标inference并引用直接事实。
结果只能描述采样帧实际显示的状态；动作/结果顺序不清、缺关键过程、遮挡或身份不明写uncertainties。
每条description引用所依据F编号，区间使用它们的局部秒；区间仅定位采样证据，不声称该区间连续动作。
单帧事实可用不超过一帧时长的非零定位区间，始末须在代理源时域内，不能填零长区间。
normal_proxy_sha256/proxy_sha256仍绑定保留的正常代理；actual carrier/manifest/frame SHA绑定实际提交图片。
observed_duration_s是原正常代理的源时间跨度，不表示每一帧或每一秒均已观察。
不依靠电影常识、音频、更长窗口或先前剧情补事实。不把标签当visible_text电影内容。
采样载体绑定：""" + json.dumps(carrier["prompt_metadata"], ensure_ascii=False) + """
""" + contract


def prompt(segment, source, proxy):
    """Reconstruct the forward prompt from verified files for cache validation."""
    return _prompt(segment, source, proxy, saved_media(proxy))


def make_media(segment, source, proxy):
    """Create or validate the sampled carrier; leave the normal proxy unchanged."""
    from PIL import Image, ImageDraw, ImageFont

    proxy_path = _proxy_path(proxy)
    _bind_source(segment, source, proxy)
    folder = proxy_path.parent / "dense_frames"
    manifest_path = folder / "manifest.json"
    if manifest_path.is_file():
        carrier = saved_media(proxy)
        return {**carrier, "prompt": _prompt(segment, source, proxy, carrier)}
    _require(not folder.exists(), "incomplete_carrier_preserved_do_not_overwrite")
    folder.mkdir()
    indices = _sample_indices(proxy["duration_s"])
    frames = []
    height = math.ceil(TILE_WIDTH * 9 / 16)
    sheet = Image.new("RGB", (COLUMNS * TILE_WIDTH,
        math.ceil(len(indices) / COLUMNS) * (height + LABEL_HEIGHT)), "black")
    font = ImageFont.load_default(size=20)
    for ordinal, index in enumerate(indices):
        frame_path = (folder / f"frame_{ordinal:03d}.jpg").resolve()
        local = index / 30
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(proxy_path),
            "-map", f"0:{proxy['metadata'].get('video_stream_index', 0)}", "-vf",
            f"select=eq(n\\,{index}),scale={TILE_WIDTH}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={TILE_WIDTH}:{height}:(ow-iw)/2:(oh-ih)/2", "-frames:v", "1", "-q:v", "3",
            str(frame_path)], timeout=120)
        with Image.open(frame_path) as picture:
            origin = ((ordinal % COLUMNS) * TILE_WIDTH, (ordinal // COLUMNS) * (height + LABEL_HEIGHT))
            sheet.paste(picture.convert("RGB"), origin)
            ImageDraw.Draw(sheet).text((origin[0] + 8, origin[1] + height + 6),
                f"F{ordinal:02d}  local {local:.3f}s", font=font, fill="white")
        frames.append({"frame_id": f"F{ordinal:02d}", "frame_index": index,
            "local_time_s": local, "source_time_s": proxy["source_start_s"] + local,
            "path": str(frame_path), "sha256": sha256_file(frame_path)})
    image_path = (folder / "sheet.jpg").resolve()
    sheet.save(image_path, "JPEG", quality=85)
    _require(image_path.stat().st_size < 5_000_000 and sheet.width < 6000 and sheet.height < 6000,
             "official_image_size_limit")
    _proxy_path(proxy)
    manifest = {"policy": POLICY, "normal_proxy": deepcopy(proxy), "frames": frames,
        "sampling": {"strategy": "uniform_midpoint_proxy_frame_indices", "proxy_fps": 30,
            "target_frames_per_second": TARGET_FRAMES_PER_SECOND, "max_frames": MAX_FRAMES,
            "frame_count": len(indices), "continuous": False},
        "image": {"path": str(image_path), "sha256": sha256_file(image_path),
            "size_bytes": image_path.stat().st_size, "width": sheet.width, "height": sheet.height},
        "layout": {"tile_width": TILE_WIDTH, "columns": COLUMNS, "label_height": LABEL_HEIGHT,
            "labels": "frame_id and normal_proxy_local_seconds"}, "limitations": LIMITATIONS}
    write_json(manifest_path, manifest)
    carrier = saved_media(proxy)
    return {**carrier, "prompt": _prompt(segment, source, proxy, carrier)}
