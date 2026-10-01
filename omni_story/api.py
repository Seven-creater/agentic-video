"""Same hosted AV/text contract; credentials stay in memory, not command lines."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse


class QwenAPI:
    def __init__(self):
        self.key = os.environ.get("DASHSCOPE_API_KEY", "")
        base = os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if not self.key:
            raise ValueError("DASHSCOPE_API_KEY_missing")
        parsed = urlparse(base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("API_base_must_be_credential_free_https")
        self.endpoint = base.rstrip("/") + "/chat/completions"
        self.curl = shutil.which("curl.exe" if sys.platform == "win32" else "curl")
        if not self.curl:
            raise ValueError("curl_missing")
        self.cfg = {"model": "qwen3.8-omni-flash", "base_url": base.rstrip("/"),
                    "temperature": 0, "reasoning_effort": "none",
                    "response_format": "json_object", "reference_fps_requested": 2.0,
                    "timeout_s": 600, "transport_retries": 0}

    def request(self, text, *, media: Path | None = None, tokens=6000):
        content = text
        if media is not None:
            if media.stat().st_size >= 10_000_000:
                raise ValueError("inline_video_exceeds_existing_10mb_contract")
            content = [{"type": "text", "text": text}, {"type": "video_url", "video_url": {
                "url": "data:;base64," + base64.b64encode(media.read_bytes()).decode("ascii"),
                "fps": self.cfg["reference_fps_requested"]}}]
        payload = {"model": self.cfg["model"], "messages": [{"role": "user", "content": content}],
                   "temperature": 0, "max_tokens": tokens,
                   "reasoning_effort": "none", "response_format": {"type": "json_object"}}
        if media is not None:
            payload["modalities"] = ["text"]

        def quote(value):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        config = "\n".join(["url = " + quote(self.endpoint), 'request = "POST"',
            "header = " + quote("Authorization: Bearer " + self.key),
            'header = "Content-Type: application/json"',
            "data-binary = " + quote(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))]) + "\n"
        args = [self.curl, "--silent", "--show-error", "--max-time", "600", "-w", "\n%{http_code}", "-K", "-"]
        if sys.platform == "win32":
            args.insert(1, "--ssl-revoke-best-effort")
        started = time.monotonic()
        proc = subprocess.run(args, input=config.encode("utf-8"), capture_output=True, timeout=605)
        elapsed = time.monotonic() - started
        if proc.returncode:
            raise RuntimeError("API_transport_failed:" + str(proc.returncode))
        raw, code = proc.stdout.rsplit(b"\n", 1)
        # Persist full response, even if HTTP/content parsing later fails.
        return {"http_status": int(code), "body_text": raw.decode("utf-8").replace(self.key, "[REDACTED]"),
                "elapsed_s": elapsed}
