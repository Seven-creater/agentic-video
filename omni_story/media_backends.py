"""Bounded paid jobs: persist intent before submission; never replay an uncertain POST."""
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

from .pipeline import json_sha, probe, sha, write


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def claim(directory, identity):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "submission.json"
    if path.exists():
        prior = load(path)
        if prior["identity"] != identity:
            raise ValueError("job_input_changed:" + directory.name)
        return prior, False
    # Exclusive create also prevents two coordinators submitting the same job.
    with path.open("x", encoding="utf-8") as stream:
        json.dump({"identity": identity, "status": "submission_claimed", "pid": os.getpid()}, stream)
    return load(path), True


class ImageHelper:
    """Installed image skill helper. No Python HTTP image implementation or silent provider fallback."""
    def __init__(self):
        self.home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
        suffix = ".exe" if sys.platform == "win32" else ""
        self.helper = self.home / "skills/openai-codex-image-skills/bin" / ("codex-image-helper" + suffix)
        if not self.helper.is_file():
            raise ValueError("installed_image_skill_helper_missing")
        self.artifact_root = self.home / "output/imagegen"
        self.cfg = {"model": "gpt-image-2", "quality": "high", "n": 1,
                    "transport_retries": 0, "backend": "installed_image_skill_helper",
                    "helper_sha256": sha(self.helper)}

    def generate(self, directory, prompt, references=(), *, size="1536x1024"):
        directory = Path(directory)
        identity = json_sha({"prompt": prompt, "references": [sha(p) for p in references],
                             "size": size, "config": self.cfg})
        old, fresh = claim(directory, identity)
        target = self.artifact_root / (identity + ".png")
        if old.get("status") == "completed":
            if not target.is_file() or sha(target) != old["sha256"]:
                raise ValueError("image_output_changed")
            return old
        if not fresh:
            # Recover a locally written result after a coordinator interruption, never resubmit.
            if target.is_file() and target.stat().st_size > 0:
                result = {**old, "status": "completed", "path": str(target), "sha256": sha(target),
                          "recovered_local_output": True}
                write(directory / "submission.json", result)
                return result
            raise RuntimeError("image_submission_uncertain_or_failed:no_automatic_resubmit")
        directory.joinpath("prompt.txt").write_text(prompt, encoding="utf-8")
        write(directory / "inputs.json", {"reference_shas": [sha(p) for p in references],
                                         "config": self.cfg, "size": size})
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        args = [str(self.helper), "--prompt-file", str(directory / "prompt.txt"), "--out", str(target),
                "--model", self.cfg["model"], "--quality", "high", "--size", size,
                "--max-retries", "0", "--timeout", "900", "--quiet"]
        for image in references:
            args += ["--image", str(image)]
        proc = subprocess.run(args, capture_output=True, timeout=910)
        # Provider settings/credentials are never copied into the repository.
        if not target.is_file() or target.stat().st_size == 0:
            write(directory / "submission.json", {**old, "status": "failed_or_uncertain",
                                                   "process_exit": proc.returncode})
            message = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
            for key in ("DASHSCOPE_API_KEY", "MINIMAX_API_KEY"):
                secret = os.environ.get(key)
                if secret:
                    message = message.replace(secret, "[REDACTED]")
            raise RuntimeError("image_helper_failed:" + (message[:500] or str(proc.returncode)))
        # Validate the helper artifact without re-opening it for display.
        info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                               "-of", "json", str(target)], check=True, capture_output=True, text=True)
        write(directory / "image_probe.json", json.loads(info.stdout))
        result = {**old, "status": "completed", "path": str(target), "sha256": sha(target),
                  "input_shas": [sha(p) for p in references], "prompt_sha256": sha(directory / "prompt.txt"),
                  "model_config_sha256": json_sha(self.cfg)}
        write(directory / "submission.json", result)
        return result


class MiniMaxH3:
    def __init__(self):
        self.key = os.environ.get("MINIMAX_API_KEY", "")
        self.base = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com").rstrip("/")
        parsed = urlparse(self.base)
        if not self.key or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("MINIMAX_API_KEY_or_https_base_missing")
        self.curl = shutil.which("curl.exe" if sys.platform == "win32" else "curl")
        if not self.curl:
            raise ValueError("curl_missing")
        self.cfg = {"model": "MiniMax-H3", "resolution": "768P", "base_url": self.base,
                    "post_retries": 0, "polling_interval_s": 20, "max_poll_seconds": 1800}

    def http(self, method, resource, payload=None):
        def quote(value):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines = ["url = " + quote(self.base + resource), "request = " + quote(method),
                 "header = " + quote("Authorization: Bearer " + self.key),
                 'header = "Content-Type: application/json"']
        if payload is not None:
            lines.append("data-binary = " + quote(json.dumps(payload, ensure_ascii=True, separators=(",", ":"))))
        args = [self.curl, "--silent", "--show-error", "--max-time", "300", "-w", "\n%{http_code}", "-K", "-"]
        if sys.platform == "win32":
            args.insert(1, "--ssl-revoke-best-effort")
        proc = subprocess.run(args, input=("\n".join(lines) + "\n").encode(), capture_output=True, timeout=305)
        if proc.returncode:
            raise RuntimeError("H3_transport_failed:" + str(proc.returncode))
        raw, status = proc.stdout.rsplit(b"\n", 1)
        return {"http_status": int(status), "body": json.loads(raw.decode().replace(self.key, "[REDACTED]"))}

    def generate(self, directory, prompt, first_frame, duration_s, ratio):
        directory = Path(directory)
        identity = json_sha({"prompt": prompt, "first_frame_sha256": sha(first_frame),
                             "duration_s": duration_s, "ratio": ratio, "config": self.cfg})
        old, fresh = claim(directory, identity)
        video = directory / "video.mp4"
        if old.get("status") == "completed":
            if not video.is_file() or sha(video) != old["sha256"]:
                raise ValueError("video_output_changed")
            return old
        task_file = directory / "task.json"
        if not task_file.exists():
            if not fresh:
                raise RuntimeError("H3_submission_uncertain:no_automatic_POST")
            metadata = {"model": self.cfg["model"], "resolution": "768P", "duration": duration_s,
                        "ratio": ratio, "prompt": prompt, "first_frame_sha256": sha(first_frame)}
            write(directory / "request.json", metadata)
            payload = {k: metadata[k] for k in ("model", "resolution", "duration", "ratio")}
            payload["content"] = [{"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                    base64.b64encode(Path(first_frame).read_bytes()).decode("ascii")}, "role": "first_frame"}]
            response = self.http("POST", "/v2/video_generation", payload)
            write(directory / "submit_response.json", response)
            body = response["body"]
            task_id = body.get("task_id")
            if response["http_status"] not in (200, 201) or not task_id:
                raise RuntimeError("H3_submission_rejected:see_submit_response")
            write(task_file, {"task_id": task_id, "request_sha256": sha(directory / "request.json")})
        task_id = load(task_file)["task_id"]
        until = time.monotonic() + self.cfg["max_poll_seconds"]
        while time.monotonic() < until:
            response = self.http("GET", "/v2/query/video_generation/" + task_id)
            polls = directory / "polls"
            polls.mkdir(exist_ok=True)
            write(polls / (str(time.time_ns()) + ".json"), response)
            if response["http_status"] != 200:
                raise RuntimeError("H3_query_failed:task_preserved")
            task = response["body"].get("task", response["body"])
            status = task.get("status")
            if status == "succeeded":
                url = task.get("content", {}).get("url")
                if not isinstance(url, str) or urlparse(url).scheme != "https":
                    raise ValueError("H3_output_https_url_missing")
                if not video.is_file():
                    temp = video.with_suffix(".download")
                    subprocess.run([self.curl, "--silent", "--show-error", "--fail", "--location",
                                    "--max-time", "300", "--output", str(temp), url],
                                   check=True, capture_output=True, timeout=305)
                    temp.replace(video)
                measured = probe(video)
                write(directory / "media_probe.json", measured)
                result = {**old, "status": "completed", "task_id": task_id, "path": str(video),
                    "sha256": sha(video), "measured_duration_s": float(measured["format"]["duration"]),
                    "request_duration_s": duration_s, "first_frame_sha256": sha(first_frame),
                    "request_sha256": sha(directory / "request.json"), "config_sha256": json_sha(self.cfg)}
                write(directory / "submission.json", result)
                return result
            if status in ("failed", "cancelled"):
                write(directory / "submission.json", {**old, "status": status, "task_id": task_id})
                raise RuntimeError("H3_terminal_failure:" + status)
            if status not in ("queued", "running", "pending", "processing"):
                raise RuntimeError("H3_unknown_status:task_preserved:" + str(status))
            time.sleep(self.cfg["polling_interval_s"])
        raise RuntimeError("H3_still_pending:resume_polls_existing_task_only")
