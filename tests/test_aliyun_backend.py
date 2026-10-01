import json
from pathlib import Path
import subprocess

import pytest

from omni_story.media_backends import AliyunImages, MiniMaxH3, load
from omni_story import production
from omni_story.pipeline import sha, write


def backend(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    return AliyunImages()


def test_original_dashscope_model_contracts_and_reference_counts(tmp_path, monkeypatch):
    api = backend(monkeypatch)
    image = tmp_path / "input.png"
    image.write_bytes(b"synthetic PNG")
    assert api.payload("prompt", [], "928x1664", "character")["model"] == "qwen-image-3.0-pro"
    assert api.payload("prompt", [], "1664x928", "location")["model"] == "wan2.7-image-pro"
    small = api.payload("prompt", [image] * 3, "1664x928", "frame")
    assert small["model"] == "qwen-image-edit-plus-2025-12-15"
    assert small["parameters"]["n"] == 1 and small["parameters"]["size"] == "1664*928"
    assert sum("image" in row for row in small["input"]["messages"][0]["content"]) == 3
    assert api.payload("prompt", [image] * 5, "1664x928", "frame")["model"] == "wan2.7-image-pro"
    with pytest.raises(ValueError, match="reference_limit"):
        api.payload("prompt", [image] * 10, "1664x928", "frame")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/v1")
    with pytest.raises(ValueError, match="supported_https"):
        AliyunImages()


def test_aliyun_key_never_appears_in_process_argv_or_response(monkeypatch):
    api = backend(monkeypatch)
    def fake(cmd, **kwargs):
        assert "synthetic-secret" not in " ".join(cmd)
        assert b"synthetic-secret" in kwargs["input"]
        assert b"X-DashScope-Async: enable" in kwargs["input"]
        return type("Process", (), {"returncode": 0, "stdout": b'{"message":"synthetic-secret"}\n200'})()
    monkeypatch.setattr(subprocess, "run", fake)
    response = api.http("POST", "/api/v1/services/aigc/image-generation/generation", {}, asynchronous=True)
    assert "synthetic-secret" not in json.dumps(response)
    assert "synthetic-secret" not in json.dumps(api.cfg)


def test_uncertain_sync_image_response_never_resubmits(tmp_path, monkeypatch):
    api = backend(monkeypatch)
    calls = []
    def failed(*args, **kwargs):
        calls.append(args[0])
        raise RuntimeError("synthetic lost response")
    monkeypatch.setattr(api, "http", failed)
    with pytest.raises(RuntimeError, match="lost response"):
        api.generate(tmp_path / "job", "p")
    with pytest.raises(RuntimeError, match="no_automatic_POST"):
        api.generate(tmp_path / "job", "p")
    assert calls == ["POST"]


def test_local_config_preflight_certificate_cannot_be_reused(tmp_path, monkeypatch):
    api = backend(monkeypatch)
    job = tmp_path / "image_jobs_aliyun/frame_M01"
    write(job / "submission.json", {"status": "submission_claimed"})
    write(job / "request.json", {"synthetic": "input"})
    write(tmp_path / "run_failure.json", {"reason": "aliyun_image_transport_failed:26"})
    def offline(cmd, **kwargs):
        assert b'url = "http://127.0.0.1:9"' in kwargs["input"]
        assert api.key.encode() not in kwargs["input"]
        return type("Process", (), {"returncode": 26, "stdout": b"",
            "stderr": b"curl: option -K: error encountered when reading a file"})()
    monkeypatch.setattr(subprocess, "run", offline)
    assert api.recover_legacy_config_failure(job, {"synthetic": "body"})
    assert not api.recover_legacy_config_failure(job, {"synthetic": "body"})


def test_explicit_backend_restore_keeps_original_trace_and_budget(tmp_path):
    prior = {"models": {"image": {"backend": "installed_image_skill_helper"}, "video": {"model": "MiniMax-H3"}}}
    write(tmp_path / "input_lineage.json", prior)
    old_sha = sha(tmp_path / "input_lineage.json")
    write(tmp_path / "image_jobs/master_C1/submission.json", {"status": "failed_or_uncertain"})
    current = {"models": {"image": {"backend": "aliyun_dashscope"}, "video": {"model": "MiniMax-H3"}}}
    assert production.cloud_restore_lineage(tmp_path, current).name == "aliyun_api_lineage.json"
    assert sha(tmp_path / "input_lineage.json") == old_sha
    assert production.image_job_count(tmp_path) == 1
    assert load(tmp_path / "backend_restore.json")["H3_config_unchanged"]
    write(tmp_path / "image_jobs_aliyun/master_C1/submission.json", {"status": "completed"})
    assert production.image_job_count(tmp_path) == 2
    write(tmp_path / "video_jobs/M1/submission.json", {"status": "submission_claimed"})
    with pytest.raises(ValueError, match="existing_video_inputs"):
        production.cloud_restore_lineage(tmp_path, current)
    write(tmp_path / "aliyun_api_lineage.json", current)
    assert production.cloud_restore_lineage(tmp_path, current).name == "aliyun_api_lineage.json"


def test_h3_still_uses_original_official_v2_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com")
    api = MiniMaxH3()
    image = tmp_path / "frame.png"
    image.write_bytes(b"synthetic")
    seen = []
    def request(method, path, payload=None):
        seen.append((method, path, payload))
        if method == "POST":
            return {"http_status": 200, "body": {"task_id": "synthetic-task"}}
        return {"http_status": 200, "body": {"task": {"status": "failed"}}}
    monkeypatch.setattr(api, "http", request)
    with pytest.raises(RuntimeError, match="terminal_failure"):
        api.generate(tmp_path / "job", "p", image, 8, "16:9")
    assert seen[0][:2] == ("POST", "/v2/video_generation")
    assert seen[1][:2] == ("GET", "/v2/query/video_generation/synthetic-task")
    assert seen[0][2]["model"] == "MiniMax-H3" and seen[0][2]["resolution"] == "768P"
    assert seen[0][2]["content"][1]["role"] == "first_frame"
    assert not any(row.get("role") == "reference_image" for row in seen[0][2]["content"])
    with pytest.raises(RuntimeError, match="terminal_failure"):
        api.generate(tmp_path / "job", "p", image, 8, "16:9")
    assert sum(method == "POST" for method, *_ in seen) == 1
