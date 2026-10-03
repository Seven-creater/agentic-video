"""CDN 直下下载器（Gate A 实测结论：本地可直连 douyinvod/douyinstatic CDN，无需 Cookie）。

流程：
    下载到 <videos_dir>/<id>/video.mp4.part（流式）→ 校验（大小 + mp4 魔数）→ 原子改名 video.mp4
    - 签名 URL 有时效：调用方应抓榜后尽快下载
    - .part 残留不视为已下载，下次重下
    - 跟随重定向（实测 302 内部重签名）
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from .models import DownloadItem, DownloadResult

logger = logging.getLogger(__name__)

MP4_MAGIC = (b"\x00\x00\x00 ftyp", b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x20ftyp")


class DirectCDNDownloader:
    def __init__(
        self,
        videos_dir: Path,
        *,
        user_agent: str,
        referer: str,
        min_valid_bytes: int = 10240,
        timeout: float = 600.0,
        session: requests.Session | None = None,
    ):
        self.videos_dir = Path(videos_dir)
        self.min_valid_bytes = int(min_valid_bytes)
        self.timeout = float(timeout)
        self._session = session or requests.Session()
        self._headers = {"User-Agent": user_agent, "Referer": referer}

    def download_video(self, item: DownloadItem) -> DownloadResult:
        out_dir = self.videos_dir / item.aweme_id
        final_path = out_dir / "video.mp4"
        part_path = out_dir / "video.mp4.part"

        def _fail(stage: str, error: str) -> DownloadResult:
            logger.error("[download %s] %s: %s", item.aweme_id, stage, error)
            return DownloadResult(success=False, error=f"{stage}: {error}", aweme_id=item.aweme_id, url=item.url)

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            with self._session.get(item.url, headers=self._headers, stream=True, timeout=self.timeout, allow_redirects=True) as resp:
                if resp.status_code != 200:
                    return _fail("http", f"status={resp.status_code}")
                part_path.unlink(missing_ok=True)
                size = 0
                with part_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            f.write(chunk)
                            size += len(chunk)
        except requests.RequestException as exc:
            part_path.unlink(missing_ok=True)
            return _fail("network", f"{type(exc).__name__}: {exc}")

        if size < self.min_valid_bytes:
            part_path.unlink(missing_ok=True)
            return _fail("too_small", f"仅 {size} 字节（<{self.min_valid_bytes}），疑似错误页")

        with part_path.open("rb") as f:
            head = f.read(32)
        if not any(head.startswith(m) for m in MP4_MAGIC):
            part_path.unlink(missing_ok=True)
            return _fail("not_mp4", f"文件头非 mp4: {head[:16]!r}")

        part_path.replace(final_path)
        logger.info("[download %s] 成功 %d 字节 → %s", item.aweme_id, size, final_path)
        return DownloadResult(
            success=True, video_path=final_path, aweme_id=item.aweme_id, url=item.url,
            status="success", file_size_bytes=size,
        )
