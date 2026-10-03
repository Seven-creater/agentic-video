"""下载层单测：manifest 语义 + CDN 直下（mock HTTP，不打真实网络）。"""
from __future__ import annotations

import json

import pytest
requests = pytest.importorskip("requests")

from omni_story.discovery.download.direct_downloader import DirectCDNDownloader
from omni_story.discovery.download.manifest import Manifest
from omni_story.discovery.download.models import DownloadItem

MP4_HEAD = b"\x00\x00\x00 ftypisom" + b"\x00" * (2 << 20)  # >2MB 满足默认阈值


class FakeStreamResponse:
    def __init__(self, status_code=200, body: bytes = b""):
        self.status_code = status_code
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, headers=None, stream=None, timeout=None, allow_redirects=None):
        self.calls.append({"url": url, "headers": headers})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_downloader(tmp_path, script, min_bytes=1024):
    return DirectCDNDownloader(
        tmp_path, user_agent="UA-test", referer="https://www.douyin.com/",
        min_valid_bytes=min_bytes, timeout=5, session=FakeSession(script),
    )


ITEM = DownloadItem(aweme_id="123", url="https://cdn.test/v.mp4", title="t", media_type=4)


# ---------- manifest ----------

def test_manifest_success_roundtrip(tmp_path):
    videos_dir = tmp_path / "videos"
    vpath = videos_dir / "123" / "video.mp4"
    vpath.parent.mkdir(parents=True)
    vpath.write_bytes(b"x" * 10)
    m = Manifest(videos_dir / "manifest.json", videos_dir)
    m.record_success("123", url="u", title="t", video_path=vpath, file_size=10)
    m.save()
    m2 = Manifest(videos_dir / "manifest.json", videos_dir)
    assert m2.is_done("123") is True


def test_manifest_success_but_file_deleted_not_done(tmp_path):
    videos_dir = tmp_path / "videos"
    vpath = videos_dir / "123" / "video.mp4"
    vpath.parent.mkdir(parents=True)
    vpath.write_bytes(b"x")
    m = Manifest(videos_dir / "manifest.json", videos_dir)
    m.record_success("123", url="u", title=None, video_path=vpath, file_size=1)
    vpath.unlink()  # 文件被删
    assert m.is_done("123") is False


def test_manifest_failure_appends_error_history(tmp_path):
    videos_dir = tmp_path / "videos"
    m = Manifest(videos_dir / "manifest.json", videos_dir)
    m.record_failure("A", url="u", title=None, stage="download", error="boom-1")
    m.record_failure("A", url="u", title=None, stage="download", error="boom-2")
    entry = m.get("A")
    assert entry["status"] == "failed"
    assert entry["attempts"] == 2
    assert [e["error"] for e in entry["errors"]] == ["boom-1", "boom-2"]


def test_manifest_success_after_failure_keeps_history(tmp_path):
    """回归：上轮失败、本轮成功（2026-09-06 真实踩坑路径）。"""
    videos_dir = tmp_path / "videos"
    vpath = videos_dir / "A" / "video.mp4"
    vpath.parent.mkdir(parents=True)
    vpath.write_bytes(b"x" * 100)
    m = Manifest(videos_dir / "manifest.json", videos_dir)
    m.record_failure("A", url="u", title=None, stage="download", error="broken pipe")
    m.record_success("A", url="u", title="t", video_path=vpath, file_size=100)
    entry = m.get("A")
    assert entry["status"] == "success"
    assert entry["attempts"] == 2                      # 失败1次 + 成功1次
    assert entry["errors"][0]["error"] == "broken pipe"  # 历史保留
    assert m.is_done("A") is True


# ---------- direct downloader ----------

def test_download_success_writes_mp4(tmp_path):
    d = make_downloader(tmp_path, [FakeStreamResponse(body=MP4_HEAD)])
    r = d.download_video(ITEM)
    assert r.success and r.status == "success"
    assert r.video_path == tmp_path / "123" / "video.mp4"
    assert r.video_path.exists()
    assert not (tmp_path / "123" / "video.mp4.part").exists()  # 无 .part 残留
    assert r.file_size_bytes == len(MP4_HEAD)


def test_download_accepts_actual_douyin_28_byte_ftyp_box(tmp_path):
    body = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 20000
    result = make_downloader(tmp_path, [FakeStreamResponse(body=body)]).download_video(ITEM)
    assert result.success and result.video_path.read_bytes() == body


@pytest.mark.parametrize("box_size", [8, 17, 1000000])
def test_download_rejects_malformed_ftyp_box_sizes(tmp_path, box_size):
    body = box_size.to_bytes(4, "big") + b"ftypisom" + b"\x00" * 20000
    result = make_downloader(tmp_path, [FakeStreamResponse(body=body)]).download_video(ITEM)
    assert not result.success and "not_mp4" in result.error


def test_download_403_fails(tmp_path):
    d = make_downloader(tmp_path, [FakeStreamResponse(status_code=403)])
    r = d.download_video(ITEM)
    assert not r.success
    assert "http" in r.error and "403" in r.error
    assert not (tmp_path / "123" / "video.mp4").exists()


def test_download_too_small_cleans_part(tmp_path):
    d = make_downloader(tmp_path, [FakeStreamResponse(body=b"\x00\x00\x00 ftypisom")])
    r = d.download_video(ITEM)
    assert not r.success and "too_small" in r.error
    assert not list((tmp_path / "123").glob("*.part"))


def test_download_not_mp4_fails(tmp_path):
    d = make_downloader(tmp_path, [FakeStreamResponse(body=b"<html>err page</html>" + b"x" * 5000)])
    r = d.download_video(ITEM)
    assert not r.success and "not_mp4" in r.error


def test_download_network_error(tmp_path):
    d = make_downloader(tmp_path, [requests.ConnectionError("reset")])
    r = d.download_video(ITEM)
    assert not r.success and "network" in r.error


def test_download_sends_browser_headers(tmp_path):
    d = make_downloader(tmp_path, [FakeStreamResponse(body=MP4_HEAD)])
    d.download_video(ITEM)
    sess = d._session
    h = sess.calls[0]["headers"]
    assert h["User-Agent"] == "UA-test"
    assert h["Referer"] == "https://www.douyin.com/"
