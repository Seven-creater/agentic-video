"""下载层通用数据结构：DownloadItem / DownloadResult / VideoDownloader Protocol。

上层 pipeline 只依赖这些结构，不感知具体下载实现（CDN 直下 / 未来其它工具）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class DownloadItem:
    aweme_id: str
    url: str
    title: str | None = None
    media_type: int | None = None   # 4=视频，2=图集（真实数据实测）


@dataclass
class DownloadResult:
    success: bool
    video_path: Path | None = None
    audio_path: Path | None = None      # Phase 1 恒 None（榜单无独立音频直链）
    metadata_path: Path | None = None
    error: str | None = None
    aweme_id: str = ""
    url: str = ""
    # success=true: "success"；false: "failed"
    # 跳过: "skipped_already_downloaded" / "skipped_image_work"
    status: str = "failed"
    file_size_bytes: int | None = None
    duration_seconds: float | None = None


@runtime_checkable
class VideoDownloader(Protocol):
    """统一下载接口：单条失败不抛异常，通过 DownloadResult.error 汇报。"""

    def download_video(self, item: DownloadItem) -> DownloadResult: ...
