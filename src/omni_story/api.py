"""Same hosted AV/text contract; credentials stay in memory, not command lines."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse


class APITransportError(RuntimeError):
    """A failed transfer with redacted evidence; never permission to resubmit."""
    def __init__(self,code,evidence,response=None):
        super().__init__('API_transport_failed:'+str(code))
        self.evidence=evidence
        self.response=response  # In memory only; may contain a temporary audio URL.


def _transport_error(code,stdout,stderr,headers,key,elapsed):
    raw,status=stdout or b'',None
    try:
        body,tail=raw.rsplit(b'\n',1)
        if re.fullmatch(rb'[0-9]{3}',tail):raw,status=body,int(tail) if 100<=int(tail)<=599 else None
    except ValueError:pass
    text=raw.decode(errors='replace').replace(key,'[REDACTED]') if key else raw.decode(errors='replace')
    try:parsed=json.loads(text)
    except ValueError:parsed=None
    request_id=parsed.get('request_id','') if isinstance(parsed,dict) else ''
    if not request_id:
        match=re.search(r'(?im)^(?:x-request-id|x-dashscope-request-id|x-acs-request-id):\s*([^\r\n]+)',headers)
        request_id=match.group(1).strip() if match else ''
    if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,200}',request_id):request_id=''
    diagnostic=(stderr or b'').decode(errors='replace')
    if key:diagnostic=diagnostic.replace(key,'[REDACTED]')
    diagnostic=re.sub(r'Bearer\s+[^\s\"\'<>]+','Bearer [REDACTED]',diagnostic,flags=re.I)
    diagnostic=re.sub(r'https?://[^\s\"\'<>]+','[URL_REDACTED]',diagnostic)[:1200]
    evidence=dict(curl_exit_code=code,http_status=status,request_id=request_id,elapsed_s=elapsed,
        stderr=diagnostic,response_bytes=len(raw),response_sha256=hashlib.sha256(raw).hexdigest(),
        complete_json=isinstance(parsed,dict))
    response=dict(http_status=status,body_text=text,elapsed_s=elapsed,transport_evidence=evidence) if status and isinstance(parsed,dict) else None
    return APITransportError(code,evidence,response)


def curl_json(curl, url, key, method, payload=None, *, headers=(), timeout=600, noproxy=None):
    """Small config on stdin keeps credentials off argv; body avoids curl's 10 MB config-line cap."""
    def quote(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["url = " + quote(url), "request = " + quote(method),
             "header = " + quote("Authorization: Bearer " + key),
             'header = "Content-Type: application/json"']
    lines += ["header = " + quote(value) for value in headers]
    body_path,header_path = None,None
    started = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(prefix='omni_response_',suffix='.headers',delete=False) as stream:
            header_path=Path(stream.name)
        if payload is not None:
            with tempfile.NamedTemporaryFile(prefix="omni_request_", suffix=".json", delete=False) as stream:
                body_path = Path(stream.name)
                stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode())
            lines.append("data-binary = " + quote("@" + str(body_path)))
        args = [curl, "--silent", "--show-error", "--max-time", str(timeout),
                '--dump-header',str(header_path),"-w", "\n%{http_code}", "-K", "-"]
        if noproxy is not None:
            args[1:1] = ["--noproxy", noproxy]
        if sys.platform == "win32":
            args.insert(1, "--ssl-revoke-best-effort")
        try:
            proc = subprocess.run(args, input=("\n".join(lines) + "\n").encode(),
                                  capture_output=True, timeout=timeout + 5)
        except subprocess.TimeoutExpired as error:
            raise _transport_error('timeout',error.stdout,error.stderr,header_path.read_text(errors='replace'),key,time.monotonic()-started) from None
        if proc.returncode:
            raise _transport_error(proc.returncode,proc.stdout,proc.stderr,header_path.read_text(errors='replace'),key,time.monotonic()-started)
        raw, status = proc.stdout.rsplit(b"\n", 1)
        return {"http_status": int(status), "body_text": raw.decode().replace(key, "[REDACTED]"),
                "elapsed_s": time.monotonic() - started}
    finally:
        if body_path is not None:
            body_path.unlink(missing_ok=True)  # exact temporary file created above, no recursive cleanup
        if header_path is not None:header_path.unlink(missing_ok=True)


class QwenAPI:
    def __init__(self, *, temporary_upload=False):
        self.temporary_upload = temporary_upload
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
        if temporary_upload:
            if parsed.hostname != 'dashscope.aliyuncs.com':
                raise ValueError('temporary_media:official_mainland_endpoint_required')
            self.cfg['media_transport'] = 'official_account_model_bound_upload_48h'

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
        headers, uploaded_url = (), None
        if media is not None:
            encoded_size = 4 * ((media.stat().st_size + 2) // 3)
            if getattr(self, 'temporary_upload', False):
                from .temporary_media import upload_video
                uploaded_url = upload_video(media, self.key, self.cfg['model'])
                url = uploaded_url
                headers = ('X-DashScope-OssResourceResolve: enable',)
            elif encoded_size >= 10_000_000:
                raise ValueError("inline_video_exceeds_existing_10mb_contract")
            else:
                url = "data:;base64," + base64.b64encode(media.read_bytes()).decode("ascii")
            content = [{"type": "text", "text": text}, {"type": "video_url", "video_url": {
                "url": url,
                "fps": fps or self.cfg["reference_fps_requested"]}}]
        payload = {"model": self.cfg["model"], "messages": [{"role": "user", "content": content}],
                   "temperature": 0, "max_tokens": tokens,
                   "reasoning_effort": "none", "response_format": {"type": "json_object"}}
        if media is not None:
            payload["modalities"] = ["text"]

        response = (curl_json(self.curl, self.endpoint, self.key, "POST", payload, headers=headers)
                    if headers else curl_json(self.curl, self.endpoint, self.key, "POST", payload))
        if uploaded_url:
            response['body_text'] = response['body_text'].replace(uploaded_url, '[REDACTED_MEDIA_URL]')
        return {**response, "transport_json_instruction_added": format_hint_added}
