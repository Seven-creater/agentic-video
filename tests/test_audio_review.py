import base64
import json
import shutil
import subprocess

import pytest

from omni_story.library import audio_review as review


class FakeAPI:
    def __init__(self, result=None):
        self.key = "synthetic-private-key"
        self.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        self.cfg = {"model": "qwen3.8-omni-flash", "reference_fps_requested": 2.0,
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "temperature": 0, "api_key": self.key}
        self.result = result
        self.calls = []

    def request(self, prompt, *, media):
        self.calls.append((prompt, media))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or response({"verdict": "failed", "heard": "实际声音"})


def response(value, *, status=200):
    reply = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"http_status": status, "elapsed_s": 0.5,
            "body_text": json.dumps({"choices": [{"message": {"content": reply}}],
                                     "usage": {"total_tokens": 123}}, ensure_ascii=False)}


@pytest.fixture
def media(tmp_path, monkeypatch):
    path = tmp_path / "actual_audio_video.mp4"
    path.write_bytes(b"synthetic actual file; mock probe, no paid API")
    monkeypatch.setattr(review, "probe_media", lambda path: {
        "duration_s": 12000.0, "streams": [{"index": 0, "codec_type": "video"},
                                         {"index": 1, "codec_type": "audio"}]})
    return path


def test_success_writes_before_send_and_reuses_negative_received_reply(media, tmp_path):
    directory = tmp_path / "review"
    api = FakeAPI()
    original = api.request
    def observe_submission(prompt, *, media):
        state = json.loads((directory / "state.json").read_text())
        assert state["status"] == "submitted"
        assert (directory / "request.json").is_file()
        assert not (directory / "response.json").exists()
        return original(prompt, media=media)
    api.request = observe_submission
    value = review.review_audio_video(media, directory, prompt="Listen and watch. Return JSON.", api=api)
    cached = review.review_audio_video(media, directory, prompt="Listen and watch. Return JSON.", api=api)
    assert value["parsed"]["verdict"] == cached["parsed"]["verdict"] == "failed"
    assert value["reply"] == cached["reply"]
    assert value["usage"] == {"total_tokens": 123}
    assert not value["cache_hit"] and cached["cache_hit"]
    assert len(api.calls) == 1 and api.calls[0][1] == media.resolve()
    assert value["video_sha256"] == review.sha256_file(media)
    assert value["response_sha256"] == review.sha256_file(directory / "response.json")
    record = json.loads((directory / "request.json").read_text())
    assert record["duration_s"] == 12000.0  # No overall duration cap.
    assert record["audio_stream_indices"] == [1]
    assert record["base64_size_bytes"] == 4 * ((media.stat().st_size + 2) // 3)


def test_transport_unknown_is_not_replayed_and_does_not_log_exception_secrets(media, tmp_path):
    api = FakeAPI(RuntimeError("synthetic-private-key data:;base64,YQ== https://example.test/?signature=secret"))
    directory = tmp_path / "review"
    for _ in range(2):
        with pytest.raises(RuntimeError, match="request_outcome_unknown_no_replay"):
            review.review_audio_video(media, directory, prompt="Listen. Return JSON.", api=api)
    assert len(api.calls) == 1
    state = json.loads((directory / "state.json").read_text())
    assert state["status"] == "uncertain" and state["error_type"] == "RuntimeError"
    all_text = "".join(path.read_text() for path in directory.glob("*.json"))
    assert api.key not in all_text and "signature=" not in all_text and "base64," not in all_text


def test_interrupted_submitted_state_blocks_replay(media, tmp_path):
    api = FakeAPI()
    directory = tmp_path / "review"
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    api.request = interrupt
    with pytest.raises(KeyboardInterrupt):
        review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    assert json.loads((directory / "state.json").read_text())["status"] == "submitted"
    api.request = lambda *args, **kwargs: pytest.fail("must not replay")
    with pytest.raises(RuntimeError, match="request_outcome_unknown_no_replay"):
        review.review_audio_video(media, directory, prompt="Return JSON.", api=api)


@pytest.mark.parametrize("mutation", ["video", "prompt", "model", "endpoint", "request_file"])
def test_changed_binding_stops_without_another_request(media, tmp_path, mutation):
    api = FakeAPI()
    directory, prompt = tmp_path / "review", "Return JSON."
    review.review_audio_video(media, directory, prompt=prompt, api=api)
    if mutation == "video":
        media.write_bytes(b"different real input")
    elif mutation == "prompt":
        prompt += " Changed."
    elif mutation == "model":
        api.cfg["model"] = "another-model"
    elif mutation == "endpoint":
        api.endpoint = "https://another.example.test/chat/completions"
    else:
        (directory / "request.json").write_text("{}")
    with pytest.raises(ValueError, match="request_binding_changed_no_replay"):
        review.review_audio_video(media, directory, prompt=prompt, api=api)
    assert len(api.calls) == 1


@pytest.mark.parametrize("mutation", ["modify", "delete"])
def test_received_response_tampering_stops_without_replay(media, tmp_path, mutation):
    api = FakeAPI()
    directory = tmp_path / "review"
    review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    path = directory / "response.json"
    if mutation == "modify":
        path.write_text(json.dumps(response({"verdict": "passed"})))
    else:
        path.unlink()
    with pytest.raises(ValueError, match="received_response_missing_or_modified"):
        review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    assert len(api.calls) == 1


def test_mute_video_rejected_before_api_or_ledger(media, tmp_path, monkeypatch):
    monkeypatch.setattr(review, "probe_media", lambda path: {
        "duration_s": 1.0, "streams": [{"index": 0, "codec_type": "video"}]})
    api = FakeAPI()
    directory = tmp_path / "review"
    with pytest.raises(ValueError, match="actual_video_has_no_audio_stream"):
        review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    assert not directory.exists() and api.calls == []


@pytest.mark.parametrize("size", [7_499_998, 7_499_999, 7_500_000])
def test_inline_limit_checks_base64_padding_not_original_bytes(media, tmp_path, size):
    with media.open("r+b") as stream:
        stream.truncate(size)
    api = FakeAPI()
    with pytest.raises(ValueError, match="inline_base64_exceeds_10mb_prepare_audio_preserving_proxy"):
        review.review_audio_video(media, tmp_path / "review", prompt="Return JSON.", api=api)
    assert api.calls == []


def test_largest_valid_inline_input_is_accepted(media, tmp_path):
    with media.open("r+b") as stream:
        stream.truncate(7_499_997)
    api = FakeAPI()
    assert review.review_audio_video(media, tmp_path / "review", prompt="Return JSON.", api=api)["parsed"]
    assert len(api.calls) == 1


def test_received_bad_json_is_preserved_and_never_resubmitted(media, tmp_path):
    api = FakeAPI(response("not JSON"))
    directory = tmp_path / "review"
    value = review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    cached = review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    assert value["parsed"] is None and value["reply"] == "not JSON"
    assert value["parse_error"] == "response_or_reply_json_invalid" and cached["cache_hit"]
    assert len(api.calls) == 1


def test_response_prompt_and_config_redaction_preserves_review_content(media, tmp_path):
    api = FakeAPI(response({"heard": "voice", "note": "synthetic-private-key data:;base64,YQ== https://a.test/v?signature=secret"}))
    directory = tmp_path / "review"
    value = review.review_audio_video(media, directory,
                                     prompt="Return JSON. synthetic-private-key", api=api)
    assert value["parsed"]["heard"] == "voice"
    all_text = "".join(path.read_text() for path in directory.glob("*.json"))
    assert api.key not in all_text and "signature=" not in all_text and "base64," not in all_text
    assert "[REDACTED]" in all_text and "[REDACTED_MEDIA]" in all_text
    assert "[REDACTED_URL]" in all_text


def test_source_changed_inside_request_is_received_but_cannot_be_reused(media, tmp_path):
    api = FakeAPI()
    original = api.request
    def mutate(prompt, *, media):
        value = original(prompt, media=media)
        media.write_bytes(b"changed during request")
        return value
    api.request = mutate
    directory = tmp_path / "review"
    with pytest.raises(ValueError, match="video_changed_during_request_no_replay"):
        review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
    assert json.loads((directory / "state.json").read_text())["status"] == "received"
    assert (directory / "response.json").is_file() and len(api.calls) == 1


def test_known_http_error_is_received_and_not_retried(media, tmp_path):
    api = FakeAPI({"http_status": 401, "body_text": json.dumps({"error": {"message": "denied"}})})
    directory = tmp_path / "review"
    for _ in range(2):
        value = review.review_audio_video(media, directory, prompt="Return JSON.", api=api)
        assert value["status"] == "received" and value["parse_error"] == "http_status_not_200"
    assert len(api.calls) == 1
    assert json.loads((directory / "state.json").read_text())["status"] == "received"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Requires media tools")
@pytest.mark.parametrize("has_audio", [True, False])
def test_actual_probe_and_qwen_payload_preserve_complete_video_audio(tmp_path, monkeypatch, has_audio):
    path = tmp_path / "actual.mp4"
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25:d=0.4"]
    if has_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=660:sample_rate=16000:duration=0.4", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(args, check=True, capture_output=True)
    api = review.QwenAPI.__new__(review.QwenAPI)
    config = FakeAPI()
    api.key, api.endpoint, api.cfg, api.curl = config.key, config.endpoint, config.cfg, "unused-mock-curl"
    payloads = []
    def capture(curl, endpoint, key, method, payload):
        payloads.append(payload)
        return response({"heard": "synthetic tone"})
    monkeypatch.setattr("omni_story.api.curl_json", capture)
    if not has_audio:
        with pytest.raises(ValueError, match="actual_video_has_no_audio_stream"):
            review.review_audio_video(path, tmp_path / "review", prompt="Listen and watch. Return JSON.", api=api)
        assert payloads == []
        return
    result = review.review_audio_video(path, tmp_path / "review", prompt="Listen and watch. Return JSON.", api=api)
    content = payloads[0]["messages"][0]["content"]
    assert content[1]["type"] == "video_url"
    encoded = content[1]["video_url"]["url"].split("base64,", 1)[1]
    assert base64.b64decode(encoded) == path.read_bytes()
    assert result["parsed"]["heard"] == "synthetic tone"
    assert result["video_sha256"] == review.sha256_file(path)
