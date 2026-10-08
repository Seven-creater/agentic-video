"""Narrow state mechanics for a user-resumed, captured HTTP500; no model calls."""
from copy import deepcopy
import re

import pytest

from test_microclip_v2_infrastructure_resume import cpu_stopped, registered, old_registered, old_prepared
from omni_story.library import microclip_v2_state as mc
from omni_story.library.state import LibraryStopped, json_sha, write_json


@pytest.fixture
def resumed(tmp_path, monkeypatch):
    state = object.__new__(mc.SlotMicroclipState)
    state.output, state.path = tmp_path, tmp_path / "library_state.json"
    state.lock_path = tmp_path / ".state.lock"
    request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_image",
               "arguments": {"image_source": str(tmp_path / "grid.png"), "prompt": "Exact original question."},
               "media_sha256": "a" * 64, "observation_scope": {"kind": "sparse_contact_sheet",
               "source_sha256": "b" * 64, "source_start_s": 6.0, "source_end_s": 9.0}}
    original = {"id": "glm_224_mc2_region_1", "name": "mc2_region_1", "status": "failed_known",
                "request_sha256": json_sha(request), "repair_of": None}
    write_json(tmp_path / "calls" / original["id"] / "request.json", request)
    write_json(tmp_path / "calls" / original["id"] / "failure.json", {"error": "captured HTTP500", "uncertain": False})
    calls = [{"id": f"historical_{i}", "name": f"old_{i}", "status": "received",
              "request_sha256": json_sha({"index": i}), "repair_of": None} for i in range(223)] + [original]
    state.data = {"request_count": 224, "calls": calls, "artifacts": {"mc2_result": [{}], "mc2_recovered_result": [{}]}}
    folder = tmp_path / "execution"
    write_json(folder / "result.json", {"status": "stopped", "error": "old CPU error"})
    write_json(folder / "result_recovered.json", {"status": "stopped", "error": "old captured500"})
    state.authorization = {"infrastructure_resume": {"next_stage": "mc2_region_0"},
        "known_failure_resume": {"failed_call_id": original["id"], "failed_stage": original["name"],
                                 "retry_stage": mc.KNOWN_RETRY_STAGE, "baseline_request_count": 224},
        "execution_directory": str(folder), "allowed_render_directory": str(folder / "render")}
    monkeypatch.setattr(state, "assert_protected", lambda: state.authorization)
    monkeypatch.setattr(state, "_check_input", lambda *args: None)
    monkeypatch.setattr(state, "_predecessor", lambda *args: None)
    state._save()
    retry = {**deepcopy(request), "known_failure_retry_of": original["id"]}
    return state, retry, original


def received(state, call, value, *, failure=False):
    folder = state.output / "calls" / call["id"]
    import json
    response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
    write_json(folder / "response.json", response)
    call.update(status="received", response_sha256=json_sha(response))
    stored = next(c for c in state.data["calls"] if c["id"] == call["id"])
    stored.update(call)
    write_json(folder / ("protocol_failure.json" if failure else "parsed.json"), value)
    state._save()


def test_alias_is_separate_from_locked_stage_pattern():
    assert re.fullmatch(mc.STAGES, mc.KNOWN_RETRY_STAGE) is None
    assert mc._stage(mc.KNOWN_RETRY_STAGE) == ("region_1", False)
    assert mc._stage(mc.KNOWN_RETRY_STAGE + "_repair") == ("region_1", True)
    with pytest.raises(LibraryStopped, match="stage_not_authorized"):
        mc._stage("mc2_region_2_retry")


def test_first_new_request_is_exact_static_alias_and_preserves_failed_record(resumed):
    state, retry, original = resumed
    before = deepcopy(original)
    old_bytes = (state.output / "calls" / original["id"] / "failure.json").read_bytes()
    with pytest.raises(LibraryStopped, match="known_failure_next_stage_required"):
        state.begin_call("mc2_anchors", {})
    call, folder = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    assert call["id"] == "glm_225_mc2_region_1_retry" and folder.exists()
    assert state.data["calls"][223] == before
    assert (state.output / "calls" / original["id"] / "failure.json").read_bytes() == old_bytes


@pytest.mark.parametrize("change", ["arguments", "media", "scope", "retry_of", "tool"])
def test_transport_alias_cannot_change_original_provider_input(resumed, change):
    state, retry, _ = resumed
    if change == "arguments":
        retry["arguments"]["prompt"] += " new creative instruction"
    elif change == "media":
        retry["media_sha256"] = "c" * 64
    elif change == "scope":
        retry["observation_scope"]["source_end_s"] = 10.0
    elif change == "retry_of":
        retry.pop("known_failure_retry_of")
    else:
        retry["tool"] = "analyze_video"
    with pytest.raises(LibraryStopped, match="known_failure_retry_input_changed"):
        state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    assert state.data["request_count"] == 224


def test_alias_requires_separate_permission(resumed):
    state, retry, _ = resumed
    state.authorization["known_failure_resume"] = None
    state.data["calls"][-1]["status"] = "received"
    with pytest.raises(LibraryStopped, match="known_failure_resume_required"):
        state.begin_call(mc.KNOWN_RETRY_STAGE, retry)


def test_old_known_failure_is_read_only_after_resume(resumed):
    state, retry, original = resumed
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state._new(original)
    call, _ = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    state._new(call)


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_only_original_captured_failed_call_can_be_exempted(resumed, status):
    state, retry, _ = resumed
    state.data["calls"][220]["status"] = status
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call(mc.KNOWN_RETRY_STAGE, retry)


def test_logical_predecessor_reads_new_reply_without_relabelling_old_failure(resumed):
    state, retry, original = resumed
    call, _ = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    received(state, call, {"facts": "actual new response"})
    selected, value = state._received("mc2_region_1")
    assert selected["id"] == call["id"] and value == {"facts": "actual new response"}
    assert state.data["calls"][223]["status"] == "failed_known"
    assert not (state.output / "calls" / original["id"] / "parsed.json").exists()
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    write_json(state.output / "calls" / call["id"] / "parsed.json", {"invented": True})
    with pytest.raises(LibraryStopped, match="parsed_not_original_reply"):
        state._received("mc2_region_1")


def test_known_retry_gets_at_most_one_received_format_repair(resumed):
    state, retry, _ = resumed
    call, _ = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    received(state, call, {"invalid": "received format failure"}, failure=True)
    repaired = deepcopy(retry)
    repaired["arguments"]["prompt"] += "\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。{}"
    repair, _ = state.begin_call(mc.KNOWN_RETRY_STAGE + "_repair", repaired, repair_of=call)
    received(state, repair, {"facts": "known sole repaired reply"})
    assert state._received("mc2_region_1")[0]["id"] == repair["id"]
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(mc.KNOWN_RETRY_STAGE + "_repair", repaired, repair_of=call)


def test_repair_cannot_replace_the_original_question(resumed):
    state, retry, _ = resumed
    call, _ = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    received(state, call, {"invalid": True}, failure=True)
    changed = deepcopy(retry)
    changed["arguments"]["prompt"] = "Choose different footage instead."
    with pytest.raises(LibraryStopped, match="known_failure_repair_prompt_changed"):
        state.begin_call(mc.KNOWN_RETRY_STAGE + "_repair", changed, repair_of=call)
    assert state.data["request_count"] == 225


def test_failed_or_uncertain_retry_blocks_any_further_paid_stage(resumed):
    state, retry, _ = resumed
    call, _ = state.begin_call(mc.KNOWN_RETRY_STAGE, retry)
    next(c for c in state.data["calls"] if c["id"] == call["id"])["status"] = "failed_known"
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call("mc2_anchors", {})


def test_finishing_records_new_result_without_overwriting_either_stop(resumed, monkeypatch):
    state, _, _ = resumed
    folder = state.output / "execution"
    originals = {name: (folder / name).read_bytes() for name in ("result.json", "result_recovered.json")}
    artifacts = []
    monkeypatch.setattr(state, "set_artifact", lambda name, value: artifacts.append((name, value)))
    path = state.finish({"status": "stopped", "model_quality_gate_passed": False, "error": "new bounded stop"})
    assert path.name == "result_network_recovered.json" and artifacts[0][0] == "mc2_network_result"
    assert all((folder / name).read_bytes() == previous for name, previous in originals.items())
    state.data["artifacts"]["mc2_network_result"] = [{}]
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(mc.KNOWN_RETRY_STAGE, {"unused": True})


@pytest.fixture
def adapter_known500(cpu_stopped):
    """Construct two-region model history entirely in a pytest temporary run."""
    import base64
    import json
    from pathlib import Path
    from test_library_microclip_v2_state import image_input, complete, read, queued_guard, node
    from omni_story.library import microclip_v2_known500 as proof

    root, old_auth, region0_input = cpu_stopped
    # This is synthetic fixture construction before the 222-prefix is frozen.
    # No production recovery edits any earlier model decision or raw response.
    data = read(root / "library_state.json")
    prior = data["calls"][-1]
    prior_folder = root / "calls" / prior["id"]
    intent = read(prior_folder / "parsed.json")
    intent["search_regions"].append({"region_id": "r1", "start_frame_id": "f6", "end_frame_id": "f15",
        "question": "synthetic later event?", "obligation_ids": ["o0"]})
    response = {"result": {"content": [{"type": "text", "text": json.dumps(intent)}]}}
    write_json(prior_folder / "parsed.json", intent)
    write_json(prior_folder / "response.json", response)
    prior["response_sha256"] = json_sha(response)
    write_json(root / "library_state.json", data)
    mc.record_infrastructure_resume(root, "Synthetic CPU continuation before a later known HTTP500.")
    state = mc.SlotMicroclipState(root)
    call, _ = state.begin_call(*region0_input)
    complete(state, call, {"facts": "synthetic first region"})
    name, request = image_input(state, "mc2_region_1", 6.0, 15.0,
        rows=[("f6", 6.0, 6.04), ("f15", 14.96, 15.0)], region_id="r1")
    failed, _ = state.begin_call(name, request)
    assert failed["id"] == proof.FAILED_CALL
    write_json(root / "mcp_queue" / (failed["id"] + ".request.json"),
        {"job_id": failed["id"], "tool": request["tool"], "arguments": request["arguments"]})
    write_json(root / "mcp_queue" / (failed["id"] + ".response.json"),
        {"status": "error", "result": {"isError": True, "content": [{"type": "text", "text": "Error: captured500"}]}})
    sent = {"type": "request", "seq": 150, "job_id": failed["id"],
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "body": {
        "model": "glm-5.3-flash", "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                base64.b64encode(Path(request["arguments"]["image_source"]).read_bytes()).decode()}},
            {"type": "text", "text": request["arguments"]["prompt"]}]}]}}
    got = {"type": "response", "seq": 150, "job_id": failed["id"], "status": 500,
        "body": json.dumps({"error": {"code": "1234", "message": "Internal network failure, error id: synthetic"}})}
    (root / "mcp_http.jsonl").write_text("\n".join(json.dumps(v) for v in (sent, got)) + "\n", encoding="utf-8")
    state.fail_call(failed, "captured500", uncertain=False)
    state.finish({"status": "stopped", "error": "official_MCP_failure:" + failed["id"],
        "model_quality_gate_passed": False, "final_video": None, "final_sha256": None, "measured_duration_s": None})
    originals = {file: file.read_bytes() for file in (
        Path(old_auth["execution_directory"]) / "result.json",
        Path(old_auth["execution_directory"]) / "result_recovered.json",
        root / "calls" / failed["id"] / "failure.json")}
    auth = proof.record_resume(root, "Continue once after the explicitly captured known HTTP500.")
    assert auth["known_failure_resume"]["baseline_request_count"] == 224
    return root, auth, request, failed, originals


def test_full_adapter_known500_alias_node_predecessor_and_append_only_finish(adapter_known500):
    from test_library_microclip_v2_state import image_input, complete, read, queued_guard, node
    from omni_story.library import microclip_v2_known500 as proof
    root, auth, request, failed, originals = adapter_known500
    state = mc.SlotMicroclipState(root)
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state.reclassify_uncertain(failed, evidence={"must": "not mutate old500"})
    descriptor = read(state.data["artifacts"]["mc2_input_mc2_region_1"][0]["path"])
    state.set_artifact("mc2_input_" + proof.RETRY_STAGE, {**descriptor, "stage": proof.RETRY_STAGE})
    retry = {**deepcopy(request), "known_failure_retry_of": failed["id"]}
    call, _ = state.begin_call(proof.RETRY_STAGE, retry)
    queued_guard(state, auth, call, retry)
    complete(state, call, {"facts": "actual received synthetic retry"})
    assert state._received("mc2_region_1")[0]["id"] == call["id"]
    rows = [("f0", 0.0, .04), ("f6", 6.0, 6.04), ("f15", 14.96, 15.0)]
    anchors_input = image_input(state, "mc2_anchors", rows=rows)
    next_call, _ = state.begin_call(*anchors_input)
    queued_guard(state, auth, next_call, anchors_input[1])
    complete(state, next_call, {"status": "blocked", "facts": "synthetic bounded semantic stop"})
    result = state.finish({"status": "stopped", "error": "synthetic stopped after new facts", "model_quality_gate_passed": False})
    assert result.name == "result_network_recovered.json"
    assert all(file.read_bytes() == original for file, original in originals.items())
    assert node(root, auth, "console.log(g.loadMicroclipV2(root,env).networkResume.baseline_request_count)").stdout.strip() == "224"
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call("mc2_plan", {})
