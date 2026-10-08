"""A locally blocked, undispatched retry can resume once; no real requests."""
from copy import deepcopy
from pathlib import Path

import pytest

from test_microclip_v2_known500_state import resumed, received, adapter_known500, cpu_stopped, registered, old_registered, old_prepared
from omni_story.library import microclip_v2_state as mc
from omni_story.library.state import LibraryStopped, write_json


@pytest.fixture
def dispatch(resumed):
    state, retry, original = resumed
    failed, _ = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    state.fail_call(failed, "library_mcp_retry_or_budget_blocked", uncertain=False)
    state.finish({"status": "stopped", "model_quality_gate_passed": False, "error": "local no-dispatch stop"})
    state.authorization["known_failure_dispatch_resume"] = {
        "failed_call_id": failed["id"], "failed_stage": failed["name"],
        "retry_stage": mc.KNOWN_DISPATCH_STAGE, "baseline_request_count": 225}
    request = {**deepcopy(retry), "known_failure_retry_of": failed["id"]}
    return state, request, original, failed


def test_dispatch_is_an_explicit_static_alias_and_first_new_stage(dispatch):
    state, request, _, _ = dispatch
    assert mc._stage(mc.KNOWN_DISPATCH_STAGE) == ("region_1", False)
    assert mc._stage(mc.KNOWN_DISPATCH_STAGE + "_repair") == ("region_1", True)
    with pytest.raises(LibraryStopped, match="known_failure_dispatch_next_stage_required"):
        state.begin_call("mc2_anchors", {})
    call, _ = state.begin_call(mc.KNOWN_DISPATCH_STAGE, request)
    assert call["id"] == "glm_226_mc2_region_1_retry_dispatch"


@pytest.mark.parametrize("change", ["old_marker", "extra_marker", "prompt", "media", "scope"])
def test_dispatch_cannot_change_original_request_or_use_old_retry_marker(dispatch, change):
    state, request, original, failed = dispatch
    if change == "old_marker": request["known_failure_retry_of"] = original["id"]
    elif change == "extra_marker": request["known_failure_dispatch_of"] = failed["id"]
    elif change == "prompt": request["arguments"]["prompt"] += " change answer"
    elif change == "media": request["media_sha256"] = "c" * 64
    else: request["observation_scope"]["source_end_s"] = 10.0
    with pytest.raises(LibraryStopped, match="known_failure_retry_input_changed"):
        state.begin_call(mc.KNOWN_DISPATCH_STAGE, request)
    assert state.data["request_count"] == 225


def test_dispatch_needs_separate_permission_and_freezes_both_failed_calls(dispatch):
    state, request, original, failed = dispatch
    for call in (original, failed):
        with pytest.raises(LibraryStopped, match="historical_call_read_only"):
            state.reclassify_uncertain(call, evidence={})
    state.authorization["known_failure_dispatch_resume"] = None
    state.data["calls"][-1]["status"] = "received"
    with pytest.raises(LibraryStopped, match="known_failure_dispatch_resume_required"):
        state.begin_call(mc.KNOWN_DISPATCH_STAGE, request)


def test_logical_predecessor_uses_received_dispatch_and_exactly_one_format_repair(dispatch):
    state, request, _, _ = dispatch
    call, _ = state.begin_call(mc.KNOWN_DISPATCH_STAGE, request)
    received(state, call, {"invalid": True}, failure=True)
    fixed = deepcopy(request)
    fixed["arguments"]["prompt"] += "\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。{}"
    repaired, _ = state.begin_call(mc.KNOWN_DISPATCH_STAGE + "_repair", fixed, repair_of=call)
    received(state, repaired, {"facts": "received dispatch facts"})
    assert state._received("mc2_region_1")[0]["id"] == repaired["id"]
    assert state._received("mc2_region_1_retry")[0]["id"] == repaired["id"]
    assert all(c["status"] == "failed_known" for c in state.data["calls"][223:225])
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(mc.KNOWN_DISPATCH_STAGE + "_repair", fixed, repair_of=call)


def test_dispatch_cannot_continue_after_another_failure(dispatch):
    state, request, _, _ = dispatch
    call, _ = state.begin_call(mc.KNOWN_DISPATCH_STAGE, request)
    state.fail_call(call, "another failure", uncertain=False)
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call("mc2_anchors", {})


def test_dispatch_result_preserves_three_old_results_and_freezes_execution(dispatch):
    state, _, _, _ = dispatch
    folder = Path(state.authorization["execution_directory"])
    old = {f: f.read_bytes() for f in folder.glob("result*.json")}
    result = state.finish({"status": "stopped", "model_quality_gate_passed": False, "error": "new local stop"})
    assert result.name == "result_dispatch_recovered.json"
    assert state.data["artifacts"].get("mc2_dispatch_result")
    assert all(f.read_bytes() == content for f, content in old.items())
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(mc.KNOWN_DISPATCH_STAGE, {})


def test_full_adapter_dispatch_proof_node_predecessor_and_finish(adapter_known500):
    from test_library_microclip_v2_state import image_input, complete, read, queued_guard, node
    from omni_story.library import microclip_v2_known500 as proof
    root, auth, original_request, original_failed, originals = adapter_known500
    state = mc.SlotMicroclipState(root)
    descriptor = read(state.data["artifacts"]["mc2_input_mc2_region_1"][0]["path"])
    state.set_artifact("mc2_input_" + proof.RETRY_STAGE, {**descriptor, "stage": proof.RETRY_STAGE})
    retry = {**deepcopy(original_request), "known_failure_retry_of": original_failed["id"]}
    failed, _ = state.begin_call(proof.RETRY_STAGE, retry)
    queued_guard(state, auth, failed, retry)
    message = "Error: Unexpected error: analyze-image analysis failed: Network error: library_mcp_retry_or_budget_blocked"
    write_json(root / "mcp_queue" / (failed["id"] + ".response.json"), {"status": "error", "result": {
        "content": [{"type": "text", "text": message}], "isError": True}})
    state.fail_call(failed, "library_mcp_retry_or_budget_blocked", uncertain=False)
    state.finish({"status": "stopped", "error": "official_MCP_failure:" + failed["id"],
        "model_quality_gate_passed": False, "final_video": None, "final_sha256": None, "measured_duration_s": None})
    originals[Path(auth["execution_directory"]) / "result_network_recovered.json"] = (
        Path(auth["execution_directory"]) / "result_network_recovered.json").read_bytes()
    originals[root / "calls" / failed["id"] / "failure.json"] = (root / "calls" / failed["id"] / "failure.json").read_bytes()
    auth = proof.record_dispatch_resume(root, "Repair the proven pre-dispatch CPU guard and continue the unused trial.")
    assert auth["known_failure_dispatch_resume"]["baseline_request_count"] == 225
    state = mc.SlotMicroclipState(root)
    state.set_artifact("mc2_input_" + mc.KNOWN_DISPATCH_STAGE, {**descriptor, "stage": mc.KNOWN_DISPATCH_STAGE})
    dispatched = {**deepcopy(original_request), "known_failure_retry_of": failed["id"]}
    call, _ = state.begin_call(mc.KNOWN_DISPATCH_STAGE, dispatched)
    queued_guard(state, auth, call, dispatched)
    complete(state, call, {"facts": "actual synthetic dispatch response"})
    assert state._received("mc2_region_1")[0]["id"] == call["id"]
    anchors_input = image_input(state, "mc2_anchors", rows=[("f0", 0.0, .04), ("f6", 6.0, 6.04), ("f15", 14.96, 15.0)])
    next_call, _ = state.begin_call(*anchors_input)
    queued_guard(state, auth, next_call, anchors_input[1])
    complete(state, next_call, {"status": "blocked", "facts": "synthetic bounded semantic stop"})
    result = state.finish({"status": "stopped", "error": "bounded synthetic stop", "model_quality_gate_passed": False})
    assert result.name == "result_dispatch_recovered.json"
    assert all(file.read_bytes() == original for file, original in originals.items())
    check = node(root, auth, "console.log(g.loadMicroclipV2(root,env).dispatchResume.baseline_request_count)")
    assert check.returncode == 0, check.stderr
    assert check.stdout.strip() == "225"
