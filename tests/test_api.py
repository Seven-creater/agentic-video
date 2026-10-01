import json
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
        body = json.loads(json.loads(line.split(" = ", 1)[1]))
        seen.append(body)
        return SimpleNamespace(returncode=0, stdout=b'{}\n200')
    monkeypatch.setattr("omni_story.api.subprocess.run", capture)
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic")
    result = api.request("Inspect actual images. Return an object.", images=[image])
    content = seen[-1]["messages"][0]["content"]
    assert content[0]["text"] == "Return a JSON object.\nInspect actual images. Return an object."
    assert result["transport_json_instruction_added"]
    api.request("Return JSON only.")
    assert seen[-1]["messages"][0]["content"] == "Return JSON only."
