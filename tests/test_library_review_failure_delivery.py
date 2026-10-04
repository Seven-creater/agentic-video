"""Playable delivery after known exhausted final-review protocol failures.

Real synthetic media uses the production queue and FFmpeg pipeline. Fake model
replies verify recovery and honest status only, never actual editing quality.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import threading

import pytest

from test_library_execute import _read, _run, inputs
from test_library_revision import _name
from test_library_semantic_continuation import (
    _apis, _assert_protected_history, _baseline_and_revision,
    _continuation_responses, _protected_history,
)
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, write_json


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Real FFmpeg/FFprobe integration requires both tools",
)


@contextmanager
def _review_bridge(output, answer, *, mode="empty"):
    """Write actual empty-body replies, which the generic JSON fixture cannot."""
    queue = output / "mcp_queue"
    queue.mkdir(exist_ok=True)
    stopped = threading.Event()
    received, failures, held = [], [], set()

    def serve():
        while not stopped.is_set():
            for path in sorted(queue.glob("*.request.json")):
                response = path.with_name(path.name.replace(".request.json", ".response.json"))
                if response.exists() or path.name in held:
                    continue
                job = _read(path)
                name = _name(job)
                try:
                    received.append(job)
                    if name.startswith("continuation_3_review") and mode == "pending":
                        held.add(path.name)
                        continue
                    if name.startswith("continuation_3_review") and mode == "unknown":
                        write_json(response, {"status": "unknown", "error": "Synthetic paid reply missing."})
                    elif name.startswith("continuation_3_review") and mode == "empty":
                        write_json(response, {"status": "complete", "finish_reason": "length",
                            "result": {"content": [{"type": "text", "text": ""}]}})
                    else:
                        value = answer(job)
                        write_json(response, {"status": "complete", "result": {"content": [{
                            "type": "text", "text": json.dumps(value)}]}})
                except Exception as error:
                    failures.append(repr(error))
                    write_json(response, {"status": "error", "error": repr(error)})
            stopped.wait(.01)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield received
    finally:
        stopped.set()
        thread.join(timeout=2)
        assert not thread.is_alive() and not failures, failures


@pytest.mark.parametrize("unsupported", [False, True])
def test_empty_known_final_review_and_one_repair_deliver_actual_video_without_fabricating_a_review(inputs, unsupported):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _review_bridge(output, _continuation_responses(reference, library, output, unsupported=unsupported)) as jobs:
        baseline, previous = _baseline_and_revision(inputs)
        legacy = _protected_history(output)
        authorize(output, "进行完整端到端测试，GLM自主观察与剪辑")
        result = continuation(reference, library, output)
        assert len(jobs) == result["usage"]["requests"] == 24
        assert result["review"] is None
        assert result["review_status"] == "incomplete_protocol_failure"
        assert result["semantic_gate_passed"] is False
        assert result["status"] == "library_candidate_with_limitations"
        assert result["selected_round"] == 3 and result["effective_render_limit"] == 4
        assert result["actual_fine_windows"] == 1 and result["new_unique_fine_windows"] == 0
        assert _read(output / "result.json") == baseline
        assert _read(output / "result_revision_2.json") == previous
        final = Path(result["final_video"])
        assert final == output / "render_3" / "final.mp4"
        assert result["final_sha256"] == sha256_file(final)
        assert _read(output / "result_semantic_revision_3.json") == result
        assert not (output / "render_4").exists()
        assert not (output / "artifacts" / "semantic_continuation_v1" / "review.json").exists()
        state = _read(output / "library_state.json")
        reviews = [call for call in state["calls"] if call["name"].startswith("continuation_3_review")]
        assert len(reviews) == 2 and all(c["status"] == "received" for c in reviews)
        assert reviews[1]["repair_of"] == reviews[0]["id"]
        for call in reviews:
            folder = output / "calls" / call["id"]
            assert _read(folder / "response.json")["result"]["content"][0]["text"] == ""
            assert _read(folder / "protocol_failure.json")["error"].startswith("Expecting value:")
            assert not (folder / "parsed.json").exists()
        failure = result["review_failure"]
        assert len(failure["calls"]) == 2
        assert {binding["call_id"] for binding in failure["calls"]} == {c["id"] for c in reviews}
        assert failure["additional_model_requests"] == 0
        proof = _read(result["semantic_evidence_path"])
        assert len(proof["observations"]) == len(proof["segment_checks"]) == 1
        if unsupported:
            assert any(c["status"] == "unsupported" for c in proof["segment_checks"][0]["claim_checks"])
        _assert_protected_history(output, legacy)
        completed = _protected_history(output)
        calls = deepcopy(state["calls"])
        assert continuation(reference, library, output) == result
        assert len(jobs) == 24
        assert _read(output / "library_state.json")["calls"] == calls
        _assert_protected_history(output, completed)
    _run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"])
    assert probe_media(final)["duration_s"] == pytest.approx(2, abs=.05)


@pytest.mark.parametrize("mode", ["unknown", "pending"])
def test_unknown_or_pending_final_review_cannot_be_delivered_as_known_protocol_failure(inputs, mode, monkeypatch):
    from omni_story.library import semantic_continuation
    authorize, continuation = _apis()
    reference, library, output = inputs
    original_mcp = semantic_continuation.CodexMCP
    if mode == "pending":
        monkeypatch.setattr(semantic_continuation, "CodexMCP", lambda state: original_mcp(state, timeout_s=.5))
    with _review_bridge(output, _continuation_responses(reference, library, output), mode=mode) as jobs:
        _baseline_and_revision(inputs)
        authorize(output, "进行完整端到端测试")
        expected = "official_MCP_failure" if mode == "unknown" else "MCP_wait_timed_out"
        with pytest.raises(LibraryStopped, match=expected):
            continuation(reference, library, output)
        assert len(jobs) == 23
        state = _read(output / "library_state.json")
        review = state["calls"][-1]
        assert review["name"] == "continuation_3_review"
        assert review["status"] == ("uncertain" if mode == "unknown" else "submitted")
        assert not (output / "result_semantic_revision_3.json").exists()
        assert (output / "render_3" / "final.mp4").is_file()
        assert not any(c["name"] == "continuation_3_review_repair" for c in state["calls"])
        assert not state["artifacts"].get("incomplete_output_review")


def test_internal_semantic_validation_error_is_not_a_generic_delivery_fallback(inputs, monkeypatch):
    from omni_story.library import semantic_continuation
    authorize, continuation = _apis()
    reference, library, output = inputs

    def broken_validator(*args, **kwargs):
        raise RuntimeError("Synthetic internal semantic-review validation bug.")

    with _review_bridge(output, _continuation_responses(reference, library, output), mode="valid") as jobs:
        _baseline_and_revision(inputs)
        authorize(output, "进行完整端到端测试")
        monkeypatch.setattr(semantic_continuation.audit, "validate_semantic_review", broken_validator)
        with pytest.raises(RuntimeError, match="internal semantic-review validation bug"):
            continuation(reference, library, output)
        assert len(jobs) == 23
        state = _read(output / "library_state.json")
        assert state["calls"][-1]["name"] == "continuation_3_review"
        assert state["calls"][-1]["status"] == "received"
        assert not (output / "result_semantic_revision_3.json").exists()
        assert not state["artifacts"].get("incomplete_output_review")


def test_unexpected_value_error_is_not_generalized_into_known_review_exhaustion(tmp_path):
    from omni_story.library.semantic_continuation import _review_protocol_failure
    state = LibraryState(tmp_path / "run", {"fixture": "unexpected_review_exception"}, max_requests=80)
    before = deepcopy(state.data)
    with pytest.raises(ValueError, match="only_exhausted_review_can_deliver_incomplete_candidate"):
        _review_protocol_failure(state, "continuation_3_review", ValueError("Synthetic unexpected validation error."))
    assert state.data == before and state.data["request_count"] == 0


@pytest.mark.parametrize("repair_status", ["submitted", "uncertain"])
def test_known_failure_helper_itself_rejects_pending_or_unknown_repair(tmp_path, repair_status):
    from omni_story.library.semantic_continuation import _review_protocol_failure
    state = LibraryState(tmp_path / "run", {"fixture": "review_outcome_guard"}, max_requests=80)
    original, _ = state.begin_call("continuation_3_review", {"fixture": "known_original"})
    state.complete_call(original, {"result": {"content": [{"type": "text", "text": ""}]}})
    repair, _ = state.begin_call("continuation_3_review_repair", {"fixture": "pending_repair"}, repair_of=original)
    if repair_status == "uncertain":
        state.fail_call(repair, "Synthetic missing paid reply.", uncertain=True)
    before = deepcopy(state.data)
    with pytest.raises(ValueError, match="unknown_or_pending_review_cannot_be_completed"):
        _review_protocol_failure(state, "continuation_3_review",
                                 ValueError("model_protocol_repair_exhausted:continuation_3_review"))
    assert state.data == before and state.data["request_count"] == 2
    assert not state.data["artifacts"].get("incomplete_output_review")
