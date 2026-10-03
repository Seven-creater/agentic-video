"""Same hosted AV/text contract; credentials stay in memory, not command lines."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse


def curl_json(curl, url, key, method, payload=None, *, headers=(), timeout=600, noproxy=None):
    """Small config on stdin keeps credentials off argv; body avoids curl's 10 MB config-line cap."""
    def quote(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["url = " + quote(url), "request = " + quote(method),
             "header = " + quote("Authorization: Bearer " + key),
             'header = "Content-Type: application/json"']
    lines += ["header = " + quote(value) for value in headers]
    body_path = None
    started = time.monotonic()
    try:
        if payload is not None:
            with tempfile.NamedTemporaryFile(prefix="omni_request_", suffix=".json", delete=False) as stream:
                body_path = Path(stream.name)
                stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode())
            lines.append("data-binary = " + quote("@" + str(body_path)))
        args = [curl, "--silent", "--show-error", "--max-time", str(timeout),
                "-w", "\n%{http_code}", "-K", "-"]
        if noproxy is not None:
            args[1:1] = ["--noproxy", noproxy]
        if sys.platform == "win32":
            args.insert(1, "--ssl-revoke-best-effort")
        proc = subprocess.run(args, input=("\n".join(lines) + "\n").encode(),
                              capture_output=True, timeout=timeout + 5)
        if proc.returncode:
            raise RuntimeError("API_transport_failed:" + str(proc.returncode))
        raw, status = proc.stdout.rsplit(b"\n", 1)
        return {"http_status": int(status), "body_text": raw.decode().replace(key, "[REDACTED]"),
                "elapsed_s": time.monotonic() - started}
    finally:
        if body_path is not None:
            body_path.unlink(missing_ok=True)  # exact temporary file created above, no recursive cleanup


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

    def request(self, text, *, media: Path | None = None, images=(), fps=None, tokens=6000):
        # DashScope rejects json_object before inference if no message names JSON.
        # This is an output-format instruction, never a story/image-review answer.
        format_hint_added = "json" not in text.lower()
        if format_hint_added:
            text = "Return a JSON object.\n" + text
        content = text
        if media is not None and images:
            raise ValueError("use_one_media_kind_per_request")
        if images:
            content = [{"type": "text", "text": text}]
            for path in images:
                path = Path(path)
                mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
                content.append({"type": "image_url", "image_url": {"url":
                    "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode("ascii")}})
        if media is not None:
            if media.stat().st_size >= 10_000_000:
                raise ValueError("inline_video_exceeds_existing_10mb_contract")
            content = [{"type": "text", "text": text}, {"type": "video_url", "video_url": {
                "url": "data:;base64," + base64.b64encode(media.read_bytes()).decode("ascii"),
                "fps": fps or self.cfg["reference_fps_requested"]}}]
        payload = {"model": self.cfg["model"], "messages": [{"role": "user", "content": content}],
                   "temperature": 0, "max_tokens": tokens,
                   "reasoning_effort": "none", "response_format": {"type": "json_object"}}
        if media is not None:
            payload["modalities"] = ["text"]

        response = curl_json(self.curl, self.endpoint, self.key, "POST", payload)
        return {**response, "transport_json_instruction_added": format_hint_added}
