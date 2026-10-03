from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from omni_story.discovery import media, review
from omni_story.discovery.state import DiscoveryStopped, LIMITS, State
from omni_story.pipeline import sha


def state(tmp_path):
    return State(tmp_path / "run", {"model": "synthetic"})


def fake_media(tmp_path):
    path = tmp_path / "original.mp4"
    path.write_bytes(b"synthetic media, injected probe only")
    return {"original_path": str(path), "analysis_path": str(path),
            "original_sha256": sha(path), "analysis_sha256": sha(path),
            "duration_s": 10.0, "audio_stream_present": True, "derived": False}


def assessment(aid):
    return {"aweme_id": aid, "potentially_useful": True,
            "evidence": [{"start_s": 0, "end_s": 10, "observed": "synthetic fixture event", "modality": "mixed"}],
            "story_transfer": "relation", "innovation_space": "latitude", "asset_feasibility": "feasible",
            "editing_learning": "information order", "limitations": ["synthetic unresolved criticism"], "uncertainties": []}


class FakeOmni:
    cfg = {"model": "synthetic"}
    def __init__(self, answers):
        self.answers, self.requests = iter(answers), []
    def request(self, text, *, media=None, tokens=0):
        self.requests.append((text, media))
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        body = {"choices": [{"message": {"content": json.dumps(answer)}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
        return {"http_status": 200, "body_text": json.dumps(body), "elapsed_s": 0.01}


def add_candidate(s, tmp_path, aid="123"):
    row = {"aweme_id": aid, "page_url": "https://www.douyin.com/video/" + aid, "media": fake_media(tmp_path),
           "likes": 999999, "preview_reason": "must not contaminate Omni story evaluation"}
    s.candidate(row)
    return row


def test_budget_persists_and_duplicate_ids_do_not_spend_candidates(tmp_path):
    s = state(tmp_path)
    assert s.candidate({"aweme_id": "1"})
    assert not s.candidate({"aweme_id": "1"})
    s.data["qwen_calls"] = LIMITS["qwen_calls"]
    s.save()
    restored = state(tmp_path)
    with pytest.raises(DiscoveryStopped, match="qwen_call_budget"):
        restored.begin_call("qwen", "step", {})
    assert len(restored.data["candidates"]) == 1
    with pytest.raises(ValueError, match="aweme"):
        s.candidate({"aweme_id": "../../escape"})
    with pytest.raises(DiscoveryStopped, match="configuration"):
        State(s.output, {"model": "different"})


def test_write_before_send_and_unknown_submission_blocks_same_process_and_resume(tmp_path):
    s = state(tmp_path)
    call, folder = s.begin_call("qwen", "step", {"text": "request"})
    assert (folder / "request.json").is_file()
    assert json.loads(s.path.read_text())["calls"][0]["status"] == "submitted"
    with pytest.raises(DiscoveryStopped, match="unknown"):
        s.begin_call("qwen", "next", {})
    with pytest.raises(DiscoveryStopped, match="unknown"):
        state(tmp_path)


def test_login_pause_excludes_human_wait_and_keeps_budget(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("omni_story.discovery.state.time.monotonic", lambda: clock[0])
    s = state(tmp_path)
    clock[0] = 10
    with s.human_pause("captcha"):
        clock[0] = 5000
        s.checkpoint()
        assert s.data["active_seconds"] == 10
        assert s.remaining_seconds() == LIMITS["active_seconds"] - 10
    clock[0] += 5
    s.checkpoint()
    assert s.data["active_seconds"] == 15


def test_verified_download_survives_crash_before_candidate_save(tmp_path):
    s = state(tmp_path)
    original = s.output / "videos/123/video.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"synthetic previously validated download")
    lineage = {"original_path": str(original), "analysis_path": str(original),
               "original_sha256": sha(original), "analysis_sha256": sha(original), "duration_s": 10.0}
    original.with_name("media_lineage.json").write_text(json.dumps(lineage), encoding="utf-8")
    s.candidate({"aweme_id": "123", "status": "observed", "duration_s": 10.0})
    restored = state(tmp_path)
    media.restore_downloads(restored)
    assert restored.data["candidates"]["123"]["status"] == "verified"
    assert restored.data["candidates"]["123"]["media"] == lineage
    assert restored.data["omni_calls"] == restored.data["qwen_calls"] == 0


def test_omni_repair_removes_video_and_cached_review_never_reposts(tmp_path):
    s = state(tmp_path)
    candidate = add_candidate(s, tmp_path)
    bad = assessment("123")
    bad["evidence"][0]["end_s"] = 100
    api = FakeOmni([bad, assessment("123")])
    result = review.audition(s, candidate, api)
    assert len(api.requests) == 2
    assert api.requests[0][1] is not None and api.requests[1][1] is None
    assert "999999" not in api.requests[0][0] and "contaminate" not in api.requests[0][0]
    candidate.pop("review")  # crash between confirmed response and candidate save
    assert review.audition(s, candidate, api) == result
    assert len(api.requests) == 2
    assert s.data["omni_calls"] == 2


def test_lost_omni_reply_does_not_retry(tmp_path):
    s = state(tmp_path)
    candidate = add_candidate(s, tmp_path)
    api = FakeOmni([RuntimeError("uncertain transport")])
    with pytest.raises(DiscoveryStopped, match="unknown"):
        review.audition(s, candidate, api)
    assert len(api.requests) == 1
    assert s.data["calls"][0]["status"] == "submitted"


def test_confirmed_list_content_review_resumes_without_another_post(tmp_path):
    s = state(tmp_path)
    candidate = add_candidate(s, tmp_path)

    class ListContentOmni(FakeOmni):
        def request(self, *args, **kwargs):
            response = super().request(*args, **kwargs)
            body = json.loads(response["body_text"])
            message = body["choices"][0]["message"]
            text = message["content"]
            midpoint = len(text) // 2
            message["content"] = [{"type": "text", "text": text[:midpoint]},
                                  {"type": "text", "text": text[midpoint:]}]
            response["body_text"] = json.dumps(body)
            return response

    api = ListContentOmni([assessment("123")])
    confirmed = review.audition(s, candidate, api)
    candidate.pop("review")  # confirmed response persisted; candidate save was interrupted
    s.save()
    restored = state(tmp_path)
    resumed = review.audition(restored, restored.data["candidates"]["123"], api)
    assert resumed == confirmed
    assert len(api.requests) == restored.data["omni_calls"] == 1


def test_invalid_selection_cannot_invent_media_and_review_cap_is_three(tmp_path):
    s = state(tmp_path)
    for aid in ("1", "2", "3"):
        candidate = add_candidate(s, tmp_path, aid)
        candidate["review"] = assessment(aid)
    extra = add_candidate(s, tmp_path, "4")
    assert review.audition(s, extra, FakeOmni([])) is None
    api = FakeOmni([{"selected_aweme_id": "999", "reason": "fake", "limitations": []},
                    {"selected_aweme_id": "2", "reason": "best actual candidate", "limitations": ["risk"]}])
    selected = review.select(s, api)
    assert selected["selected_aweme_id"] == "2" and len(api.requests) == 2
    assert "999999" not in api.requests[0][0]


def test_only_verified_file_enters_screenplay_and_reference_stage_makes_no_story_calls(tmp_path):
    s = state(tmp_path)
    candidate = add_candidate(s, tmp_path)
    candidate["review"] = assessment("123")
    selected = {"selected_aweme_id": "123", "reason": "Omni chose", "limitations": []}
    called = []
    def execute(video, output):
        called.append((video, output))
        assert video == Path(candidate["media"]["analysis_path"])
        return {"status": "model_checked_screenplay_candidate"}
    assert review.handoff(s, selected, stage="reference", execute=execute)["status"] == "reference_selected"
    assert not called
    result = review.handoff(s, selected, execute=execute)
    assert len(called) == 1 and not result["image_video_generation"]
    assert result["limitations"] == candidate["review"]["limitations"]
    Path(candidate["media"]["original_path"]).write_bytes(b"modified")
    with pytest.raises(ValueError, match="changed"):
        review.handoff(s, selected, execute=execute)


def test_incomplete_story_never_restarts(tmp_path):
    s = state(tmp_path)
    candidate = add_candidate(s, tmp_path)
    candidate["review"] = assessment("123")
    (s.output / "screenplay").mkdir()
    with pytest.raises(DiscoveryStopped, match="incomplete_screenplay"):
        review.handoff(s, {"selected_aweme_id": "123", "reason": "model", "limitations": []},
                       execute=lambda *a: pytest.fail("must not replay story"))


def test_ffmpeg_whole_video_copy_preserves_audio_duration_and_original(tmp_path):
    path = tmp_path / "full.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=800x450:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "5", "-c:v", "libx264", "-b:v", "22M",
                    "-minrate", "22M", "-maxrate", "22M", "-bufsize", "44M", "-x264-params", "nal-hrd=cbr",
                    "-c:a", "aac", str(path)], check=True, capture_output=True)
    assert path.stat().st_size >= 10_000_000
    before = sha(path)
    result = media.prepare(path, expected_duration=5)
    assert result["derived"] and result["audio_stream_present"] and sha(path) == before
    assert Path(result["analysis_path"]).stat().st_size < 10_000_000
    assert abs(float(result["analysis_metadata"]["format"]["duration"]) - 5) < 0.1
    video = next(s for s in result["analysis_metadata"]["streams"] if s["codec_type"] == "video")
    assert max(video["width"], video["height"]) <= 720
    assert media.prepare(path)["analysis_sha256"] == result["analysis_sha256"]


def test_corrupted_mp4_header_is_not_enough(tmp_path):
    path = tmp_path / "bad.mp4"
    path.write_bytes(b"\x00\x00\x00 ftypisom" + b"x" * 20000)
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        media.prepare(path)
