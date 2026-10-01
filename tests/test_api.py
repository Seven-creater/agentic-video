import json
from pathlib import Path
from types import SimpleNamespace

from omni_story.api import QwenAPI


def test_json_object_transport_names_json_without_creative_answer(monkeypatch, tmp_path):
    api = QwenAPI.__new__(QwenAPI)
    api.key, api.endpoint, api.curl = "synthetic-key", "https://example.invalid/chat", "curl"
    api.cfg = {"model": "synthetic", "reference_fps_requested": 2}
    seen = []
    def capture(args, **kwargs):
        assert api.key not in " ".join(args)
        line = next(v for v in kwargs["input"].decode().splitlines() if v.startswith("data-binary = "))
        body_file = Path(json.loads(line.split(" = ", 1)[1])[1:])
        body = json.loads(body_file.read_text())
        seen.append(body_file)
        seen.append(body)
        return SimpleNamespace(returncode=0, stdout=b'{}\n200')
    monkeypatch.setattr("omni_story.api.subprocess.run", capture)
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic")
    result = api.request("Inspect actual images. Return an object.", images=[image])
    content = seen[-1]["messages"][0]["content"]
    assert content[0]["text"] == "Return a JSON object.\nInspect actual images. Return an object."
    assert result["transport_json_instruction_added"]
    assert not seen[-2].exists()
    api.request("Return JSON only.")
    assert seen[-1]["messages"][0]["content"] == "Return JSON only."


def test_large_request_body_is_not_a_curl_config_line(monkeypatch):
    from omni_story.api import curl_json
    observed = []
    def capture(args, **kwargs):
        config = kwargs["input"].decode()
        assert len(config) < 1000 and "large body" not in config
        line = next(v for v in config.splitlines() if v.startswith("data-binary = "))
        path = Path(json.loads(line.split(" = ", 1)[1])[1:])
        assert path.stat().st_size > 10 * 1024 * 1024
        observed.append(path)
        return SimpleNamespace(returncode=0, stdout=b'{}\n200')
    monkeypatch.setattr("omni_story.api.subprocess.run", capture)
    curl_json("curl", "https://example.invalid", "synthetic", "POST", {"large body": "x" * 11_000_000})
    assert not observed[0].exists()
