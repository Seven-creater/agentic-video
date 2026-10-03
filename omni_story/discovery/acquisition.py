"""Read only same-work metadata from detail responses the browser already observed."""
from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse


_DETAIL_HOSTS = {"douyin.com", "www.douyin.com", "www.iesdouyin.com"}
_DETAIL_PATHS = {"/aweme/v1/web/aweme/detail", "/aweme/v1/aweme/detail"}
_MAX_BODY_BYTES = 4 * 1024 * 1024


def _numeric_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value)
    return result if result.isascii() and result.isdecimal() else None


def _https_url(value):
    """Keep usable HTTPS addresses; this does not establish network observation."""
    if not isinstance(value, str) or len(value) > 8192 or re.search(r"\s|[\x00-\x1f\x7f]", value):
        return False
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None or
                parsed.password is not None or parsed.fragment or parsed.port not in {None, 443}):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            labels = host.split(".")
            return (len(host) <= 253 and len(labels) >= 2 and
                    labels[-1].lower() not in {"local", "localhost", "internal", "home", "lan"} and
                    all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                        for label in labels))
    except (ValueError, UnicodeError):
        return False


def parse_observed_detail(request_url: str, response_body: str | bytes | Mapping,
                          aweme_id: str) -> dict | None:
    """Return media identifiers only after request and response work IDs both match.

    The caller must obtain this body from an actual observed browser response and
    independently match the returned media to the player's network traffic. This
    helper performs no requests and returns no account or author metadata.
    """
    aid = _numeric_id(aweme_id)
    if aid is None or not _https_url(request_url):
        return None
    parsed = urlparse(request_url)
    if parsed.hostname not in _DETAIL_HOSTS or parsed.path.rstrip("/") not in _DETAIL_PATHS:
        return None
    if parse_qs(parsed.query).get("aweme_id") != [aid]:
        return None
    try:
        if isinstance(response_body, bytes):
            if len(response_body) > _MAX_BODY_BYTES:
                return None
            response_body = response_body.decode("utf-8")
        if isinstance(response_body, str):
            if len(response_body) > _MAX_BODY_BYTES:
                return None
            response_body = json.loads(response_body)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(response_body, Mapping):
        return None
    if response_body.get("status_code", 0) != 0:
        return None
    detail = response_body.get("aweme_detail")
    if not isinstance(detail, Mapping) or _numeric_id(detail.get("aweme_id")) != aid:
        return None
    video = detail.get("video")
    if not isinstance(video, Mapping):
        return None
    urls, video_ids = [], []

    def add_id(value):
        if (isinstance(value, str) and 0 < len(value) <= 256 and
                re.fullmatch(r"[a-zA-Z0-9_-]+", value) and value not in video_ids):
            video_ids.append(value)

    for key in ("play_addr", "playAddr", "download_addr", "downloadAddr"):
        address = video.get(key)
        if not isinstance(address, Mapping):
            continue
        values = address.get("url_list", address.get("urlList", []))
        if isinstance(values, list):
            for url in values[:64]:
                if _https_url(url) and url not in urls:
                    urls.append(url)
        add_id(address.get("uri"))
    add_id(video.get("vid"))
    add_id(video.get("video_id"))
    if not urls and not video_ids:
        return None
    return {"aweme_id": aid, "urls": urls, "video_ids": video_ids}
