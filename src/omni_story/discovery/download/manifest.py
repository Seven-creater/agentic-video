"""下载台账（data/videos/manifest.json）：resume 与跳过的唯一事实源。

结构：
    {"version": 1, "downloads": {"<aweme_id>": {
        "status": "success|failed",
        "url": "...", "title": "...",
        "video_path": "data/videos/<id>/video.mp4",   # 仓库根相对路径
        "file_size_bytes": 123,
        "attempts": 2,
        "errors": [{"at": "ISO8601", "stage": "...", "error": "..."}],  # 追加不覆盖
        "downloaded_at": "ISO8601",
    }}}

跳过判定 = status=="success" 且 video_path 文件仍存在（防"记录成功但文件被删"）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class Manifest:
    def __init__(self, path: Path, videos_dir: Path):
        self.path = path
        self.videos_dir = videos_dir
        self.data: dict[str, Any] = {"version": 1, "downloads": {}}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("downloads"), dict):
                self.data = loaded

    # ---- 查询 ----
    def get(self, aweme_id: str, default: dict | None = None) -> dict | None:
        return self.data["downloads"].get(aweme_id, default)

    def is_done(self, aweme_id: str) -> bool:
        """success 且文件还在 → 可跳过。"""
        entry = self.get(aweme_id)
        if not entry or entry.get("status") != "success":
            return False
        rel = entry.get("video_path")
        if not rel:
            return False
        return (self.videos_dir.parent / rel).exists() if not Path(rel).is_absolute() else Path(rel).exists()

    # ---- 写入 ----
    def record_success(self, aweme_id: str, *, url: str, title: str | None, video_path: Path, file_size: int) -> None:
        prev = self.get(aweme_id) or {}
        self.data["downloads"][aweme_id] = {
            "status": "success",
            "url": url,
            "title": title,
            "video_path": video_path.relative_to(self.videos_dir.parent).as_posix(),
            "file_size_bytes": file_size,
            "attempts": prev.get("attempts", 0) + 1,
            "errors": prev.get("errors", []),
            "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        }

    def record_failure(self, aweme_id: str, *, url: str, title: str | None, stage: str, error: str) -> None:
        prev = self.get(aweme_id) or {"attempts": 0, "errors": []}
        self.data["downloads"][aweme_id] = {
            "status": "failed",
            "url": url,
            "title": title,
            "video_path": None,
            "attempts": prev.get("attempts", 0) + 1,
            "errors": prev.get("errors", []) + [{"at": datetime.now().isoformat(timespec="seconds"), "stage": stage, "error": error[:500]}],
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
