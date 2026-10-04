"""One evidence-feedback replan with immutable failed replies and bounded calls.

Model replies are synthetic fixtures. Real-media cases exercise the production
queue, validators and FFmpeg; they do not establish GLM creative quality.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from test_library_execute import _bridge, _read, inputs
from test_library_revision import _name
from test_library_semantic_continuation import (
    _apis, _assert_protected_history, _baseline_and_revision,
    _continuation_responses, _protected_history,
)
from omni_story.library.state import LibraryState, json_sha, write_json


FFMPEG_REQUIRED = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Real FFmpeg/FFprobe integration requires both tools",
)


def _replan_responses(reference, library, output, *, failure="range", replan_fails=False):
    base = _continuation_responses(reference, library, output)

    def answer(job):
        name = _name(job)
        # Narrow fixture event evidence independently of the later intended plan.
        # The legacy fixture still satisfies its original nonsemantic contract.
        value = base({**job, "job_id": "glm_000_continuation_3_plan"}) \
            if name.startswith("continuation_3_replan") else base(job)
        if name.startswith("fine_") and failure in {"caption_range", "caption_display"}:
            if failure == "caption_range":
                value["events"].append({"local_start_s": 2.6, "local_end_s": 2.9,
                    "observed_fact": "later synthetic square event", "role_ids": ["red_square"]})
            else:
                value["events"][0]["local_end_s"] = .9
        if name.startswith("continuation_3_plan") or (
                name.startswith("continuation_3_replan") and replan_fails):
            segment = value["segments"][0]
            if failure == "range":
                segment["source_in_s"] = 1.25  # Watched, but outside usable evidence.
            elif failure == "outside":
                segment["source_in_s"] = .75  # Outside the completed fine window.
            elif failure == "unknown_source":
                segment["source_id"] = "src_missing_fixture"
            else:
                segment["caption"] = {"text": "synthetic fact", "start_s": 1,
                    "end_s": 1.8, "position": "center", "font_size": 18,
                    "evidence": [{"window_id": segment["window_id"],
                                  "event_indices": [1 if failure == "caption_range" else 0]}]}
        return value

    return answer


@FFMPEG_REQUIRED
@pytest.mark.parametrize("failure", ["range", "caption_range", "caption_display"])
def test_known_evidence_failure_replans_once_and_cached_resume_skips_old_failure(inputs, failure):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _replan_responses(reference, library, output, failure=failure)) as jobs:
        baseline, previous = _baseline_and_revision(inputs)
        before = _protected_history(output)
        authorize(output, "进行端到端测试，GLM自主决定看什么")
        result = continuation(reference, library, output)
        assert len(jobs) == result["usage"]["requests"] == 25
        names = [_name(job) for job in jobs[17:]]
        assert names[:4] == ["continuation_3_reference", "continuation_3_plan",
                             "continuation_3_plan_repair", "continuation_3_replan"]
        assert names[-3:] == ["semantic_claims_batch_3", "continuation_3_blind", "continuation_3_review"]
        state = _read(output / "library_state.json")
        failed = [c for c in state["calls"] if c["name"] in
                  {"continuation_3_plan", "continuation_3_plan_repair"}]
        assert len(failed) == 2 and all(c["status"] == "received" for c in failed)
        assert failed[1]["repair_of"] == failed[0]["id"]
        expected = {"range": "plan:range_not_supported_by_fine_observation",
                    "caption_range": "plan:caption_event_outside_selected_range",
                    "caption_display": "semantic:caption_evidence_not_visible_during_display"}[failure]
        for call in failed:
            folder = output / "calls" / call["id"]
            assert _read(folder / "protocol_failure.json")["error"] == expected
            assert not (folder / "parsed.json").exists()
        allocation = _read(state["artifacts"]["semantic_replan_budget"][0]["path"])
        assert allocation["baseline_request_count"] == 20
        assert allocation["remaining_at_allocation"] == 20
        assert allocation["max_segments"] == 6
        assert allocation["reserved_max_requests"] == 20
        assert allocation["original_failure_preserved"] is True
        assert _read(output / "result.json") == baseline
        assert _read(output / "result_revision_2.json") == previous
        _assert_protected_history(output, before)
        protected = _protected_history(output)
        calls = deepcopy(state["calls"])
        assert continuation(reference, library, output) == result
        assert len(jobs) == 25
        assert _read(output / "library_state.json")["calls"] == calls
        _assert_protected_history(output, protected)
        prompt = next(j["arguments"]["prompt"] for j in jobs
                      if _name(j) == "continuation_3_replan")
        for leaked in ("5–17", "5到17", "断臂", "踢腿", "功夫熊猫", "9.07"):
            assert leaked not in prompt
        assert "failed_model_plan" in prompt and "contract_diagnostics" in prompt
        assert not (output / "render_4").exists()


@FFMPEG_REQUIRED
@pytest.mark.parametrize("failure", ["outside", "unknown_source"])
def test_source_bounds_and_unknown_source_are_not_semantic_replan_eligible(inputs, failure):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _replan_responses(reference, library, output, failure=failure)) as jobs:
        _baseline_and_revision(inputs)
        before = _protected_history(output)
        authorize(output, "进行端到端测试")
        with pytest.raises(ValueError, match="plan_failure_not_semantic_replan_eligible"):
            continuation(reference, library, output)
        assert len(jobs) == _read(output / "library_state.json")["request_count"] == 20
        assert not any(_name(j).startswith("continuation_3_replan") for j in jobs)
        assert not _read(output / "library_state.json")["artifacts"].get("semantic_replan_budget")
        assert not (output / "render_3").exists()
        _assert_protected_history(output, before)


@FFMPEG_REQUIRED
def test_replan_own_repair_exhaustion_never_creates_third_semantic_attempt(inputs):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _replan_responses(reference, library, output, replan_fails=True)) as jobs:
        _baseline_and_revision(inputs)
        authorize(output, "进行端到端测试")
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted:continuation_3_replan"):
            continuation(reference, library, output)
        assert len(jobs) == 22
        state = _read(output / "library_state.json")
        allocation = deepcopy(state["artifacts"]["semantic_replan_budget"])
        calls = deepcopy(state["calls"])
        protected = _protected_history(output)
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted:continuation_3_replan"):
            continuation(reference, library, output)
        assert len(jobs) == 22
        assert [_name(j) for j in jobs[20:]] == ["continuation_3_replan", "continuation_3_replan_repair"]
        resumed = _read(output / "library_state.json")
        assert resumed["calls"] == calls and resumed["artifacts"]["semantic_replan_budget"] == allocation
        assert not (output / "render_3").exists()
        _assert_protected_history(output, protected)


def _known_failed_plan_state(tmp_path, *, historic=60, failure="plan:range_not_supported_by_fine_observation"):
    state = LibraryState(tmp_path / "run", {"fixture": "semantic_replan_unit"}, max_requests=80)
    for index in range(historic):
        call, _ = state.begin_call(f"history_{index}", {"fixture_request": index})
        state.complete_call(call, {"fixture_known_reply": index})
    plan = {"segments": []}
    original, _ = state.begin_call("continuation_3_plan", {"fixture_plan": "original"})
    state.complete_call(original, {"result": {"content": [{"type": "text", "text": json.dumps(plan)}]}})
    write_json(state.output / "calls" / original["id"] / "protocol_failure.json", {"error": failure})
    repair, _ = state.begin_call("continuation_3_plan_repair", {"fixture_plan": "repair"}, repair_of=original)
    state.complete_call(repair, {"result": {"content": [{"type": "text", "text": json.dumps(plan)}]}})
    write_json(state.output / "calls" / repair["id"] / "protocol_failure.json", {"error": failure})
    return state


class _CaptureCall:
    def __init__(self):
        self.calls = []

    def call(self, name, prompt, media, validator):
        self.calls.append((name, prompt, media))
        value = {"fixture_replan_reply": True}
        validator(value)
        return value


def _unit_replan(state, capture=None):
    from omni_story.library.semantic_continuation import _semantic_replan
    captured = capture or _CaptureCall()
    limits = []
    result = _semantic_replan(captured, state, {"render_capabilities": {"max_segments": 6}},
        Path("fixture.mp4"), [], lambda value, maximum: limits.append(maximum), {"max_segments": 6})
    return result, captured, limits


def test_real_80_call_cap_allocates_five_slices_after_62_known_calls_and_is_frozen(tmp_path):
    state = _known_failed_plan_state(tmp_path)
    before = deepcopy(state.data["calls"])
    _, capture, limits = _unit_replan(state)
    assert limits == [5]
    assert capture.calls[0][0] == "continuation_3_replan"
    entry = state.data["artifacts"]["semantic_replan_budget"][0]
    allocation = _read(entry["path"])
    assert allocation["baseline_request_count"] == 62
    assert allocation["remaining_at_allocation"] == 18
    assert allocation["max_segments"] == 5 and allocation["reserved_max_requests"] == 18
    assert state.data["calls"] == before and state.data["request_count"] == 62
    # A consumed, known later call must not lower/reallocate the persisted cap.
    call, _ = state.begin_call("fixture_after_allocation", {"fixture": "later_known_call"})
    state.complete_call(call, {"fixture": "later_known_reply"})
    _, _, resumed_limits = _unit_replan(state)
    assert resumed_limits == [5]
    assert _read(entry["path"]) == allocation
    assert len(state.data["artifacts"]["semantic_replan_budget"]) == 1


@pytest.mark.parametrize("unknown", ["submitted", "uncertain", "failed_known"])
def test_unknown_plan_outcome_cannot_be_reclassified_by_semantic_replan(tmp_path, unknown):
    state = _known_failed_plan_state(tmp_path)
    # The transport status itself is intentionally a fixture; preserve it exactly.
    state.data["calls"][-1]["status"] = unknown
    state._save()
    before = deepcopy(state.data)
    capture = _CaptureCall()
    with pytest.raises(ValueError, match="known_plan_and_repair_required"):
        _unit_replan(state, capture)
    assert capture.calls == [] and state.data == before
    assert not state.data["artifacts"].get("semantic_replan_budget")


def test_insufficient_replan_budget_cannot_reset_cap_or_register_another_task(tmp_path):
    state = _known_failed_plan_state(tmp_path, historic=70)
    before = deepcopy(state.data)
    capture = _CaptureCall()
    with pytest.raises(ValueError, match="insufficient_budget_for_semantic_replan"):
        _unit_replan(state, capture)
    assert capture.calls == [] and state.data == before
    assert state.data["request_count"] == 72 and state.max_requests == 80
    assert not state.data["artifacts"].get("semantic_replan_budget")


@pytest.mark.parametrize("tamper", ["baseline", "max_segments", "delete_allocation"])
def test_paid_replan_cannot_rebind_or_delete_its_original_allocation(tmp_path, tamper):
    state = _known_failed_plan_state(tmp_path)
    _unit_replan(state)
    call, _ = state.begin_call("continuation_3_replan", {"fixture": "paid_replan"})
    state.complete_call(call, {"fixture": "known_replan_reply"})
    entry = state.data["artifacts"]["semantic_replan_budget"][0]
    if tamper == "delete_allocation":
        state.data["artifacts"].pop("semantic_replan_budget")
    else:
        allocation = _read(entry["path"])
        if tamper == "baseline":
            allocation.update(baseline_request_count=60, remaining_at_allocation=20,
                              max_segments=6, reserved_max_requests=20)
        else:
            allocation.update(max_segments=4, reserved_max_requests=16)
        write_json(entry["path"], allocation)
        entry["sha256"] = json_sha(allocation)  # Even rehashing cannot relax the ledger-derived cap.
    state._save()
    capture = _CaptureCall()
    expected = "paid_replan_requires_original_allocation" if tamper == "delete_allocation" else "invalid_replan_reservation"
    with pytest.raises(ValueError, match=expected):
        _unit_replan(state, capture)
    assert capture.calls == [] and state.data["request_count"] == 63
    assert state.data["calls"][-1]["id"] == call["id"]
