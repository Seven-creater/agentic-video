"""One CPU-only catalog continuation, with all paid replies and stop intact."""
from copy import deepcopy
from pathlib import Path

import pytest

from test_library_microclip_v2_state import registered, old_registered, old_prepared, image_input, video_input, complete, read, node, queued_guard
from omni_story.library import microclip_v2_state as mc
from omni_story.library import microclip_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json


@pytest.fixture
def cpu_stopped(registered, monkeypatch):
    root, auth, _ = registered
    def checked_synthetic_grid(path, **kwargs):
        value = read(path)
        if any(sha256_file(f["png_path"]) != f["png_sha256"] for f in value["frames"]):
            raise ValueError("synthetic actual PNG byte mismatch")
        return value
    monkeypatch.setattr(microclip_frames, "verify_grid", checked_synthetic_grid)
    state = mc.SlotMicroclipState(root)
    rows = [("f0", 0.0, .04), ("f3", 3.0, 3.04), ("f6", 6.0, 6.04),
            ("f9", 9.0, 9.04), ("f12", 12.0, 12.04), ("f15", 14.96, 15.0)]
    original = image_input(state, "mc2_overview", rows=rows)
    call, _ = state.begin_call(*original)
    complete(state, call, {"frames": [{"frame_id": i, "visible_action_or_state": "neutral state", "visible_text": ""} for i, _, _ in rows],
                           "visible_event": "neutral sequence", "missing_information": [], "limitations": []})
    name, request = video_input(state, "mc2_motion")
    call, folder = state.begin_call(name, request)
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": "known invalid JSON"}]}})
    write_json(folder / "protocol_failure.json", {"error": "format"})
    repaired = deepcopy(request)
    repaired["arguments"]["prompt"] = "one format repair"
    call, _ = state.begin_call("mc2_motion_repair", repaired, repair_of=call)
    complete(state, call, {"visible_meaning": "neutral visible facts", "observation_status": "complete", "limitations": [],
                           "events": [{"start_s": 0.0, "end_s": 15.0, "visible_content": "synthetic neutral motion", "text_evidence": ""}]})
    intent = {"status": "ready", "blocking_questions": [], "limitations": [], "original_claim_checks": [],
              "obligations": [{"obligation_id": "o0", "description": "synthetic event", "support": "visual",
                               "evidence_frame_ids": ["f0", "f6"], "original_claim_ids": []}],
              "search_regions": [{"region_id": "r0", "start_frame_id": "f0", "end_frame_id": "f6",
                                  "question": "synthetic missing detail", "obligation_ids": ["o0"]}]}
    call, _ = state.begin_call(*image_input(state, "mc2_intent", rows=rows))
    complete(state, call, intent)
    region_rows = [("f0", 0.0, .04), ("f1", 1.2, 1.24), ("f2", 2.4, 2.44),
                   ("f3", 3.0, 3.04), ("f4", 4.8, 4.84), ("f5", 5.96, 6.0)]
    request = image_input(state, "mc2_region_0", 0, 6.04, rows=region_rows, requested_targets={"f3": 3.033333333}, region_id="r0")
    result = {"status": "stopped", "error": "microclip_v2:frame_id_conflict", "model_quality_gate_passed": False,
              "final_video": None, "final_sha256": None, "measured_duration_s": None, "observed_source_frames": 10}
    state.finish(result)
    return root, auth, request


def test_same_decoded_frame_can_have_different_requested_selector_time(cpu_stopped):
    root, auth, _ = cpu_stopped
    state = mc.SlotMicroclipState(root)
    frames = state._frames()
    assert len(frames) == 10 and frames["f3"]["requested_time_s"] == 3.033333333
    first, second = deepcopy(frames["f3"]), deepcopy(frames["f3"])
    second["requested_time_s"] = 3.0
    assert mc._stable_frame(first) == mc._stable_frame(second)
    second["png_sha256"] = "different actual pixels"
    assert mc._stable_frame(first) != mc._stable_frame(second)


def test_narrow_cpu_resume_reuses_replies_and_preserves_old_result(cpu_stopped):
    root, old_auth, request = cpu_stopped
    state = mc.SlotMicroclipState(root)
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(*request)
    original = (Path(old_auth["execution_directory"]) / "result.json").read_bytes()
    auth = mc.record_infrastructure_resume(root, "Repair the deterministic CPU metadata mismatch and continue the unused trial.")
    assert auth["infrastructure_resume"]["new_renders"] == 0
    assert mc.record_infrastructure_resume(root, "already registered")["authorization_sha256"] == old_auth["authorization_sha256"]
    state = mc.SlotMicroclipState(root)
    with pytest.raises(LibraryStopped, match="resume_next_stage_required"):
        state.begin_call("mc2_anchors", {})
    call, _ = state.begin_call(*request)
    assert call["id"].startswith("glm_223_mc2_region_0")
    queued_guard(state, auth, call, request[1])
    complete(state, call, {"facts": "new region evidence"})
    result = state.finish({"status": "stopped", "error": "different later limitation", "model_quality_gate_passed": False})
    assert result.name == "result_recovered.json"
    assert (Path(auth["execution_directory"]) / "result.json").read_bytes() == original
    assert read(root / "library_state.json")["request_count"] == 223
    assert node(root, auth, "console.log(g.loadMicroclipV2(root,env).resume.baseline_request_count)").stdout.strip() == "222"
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call("mc2_anchors", {})


@pytest.mark.parametrize("change", ["error", "render", "uncertain", "intent_parsed", "frame_pixels"])
def test_resume_rejects_other_failures_or_changed_completed_evidence(cpu_stopped, change):
    root, auth, _ = cpu_stopped
    data = read(root / "library_state.json")
    if change == "error":
        path = Path(auth["execution_directory"]) / "result.json"
        value = read(path)
        value["error"] = "different error"
        write_json(path, value)
    elif change == "render":
        Path(auth["allowed_render_directory"]).mkdir()
    elif change == "uncertain":
        data["calls"][-1]["status"] = "uncertain"
        write_json(root / "library_state.json", data)
    elif change == "intent_parsed":
        write_json(root / "calls" / data["calls"][-1]["id"] / "parsed.json", {"invented": "replacement intent"})
    else:
        descriptor = read(data["artifacts"]["mc2_input_mc2_region_0"][0]["path"])
        manifest = read(descriptor["lineage_path"])
        Path(manifest["frames"][0]["png_path"]).write_bytes(b"changed actual image")
    with pytest.raises((LibraryStopped, ValueError)):
        mc.record_infrastructure_resume(root, "Cannot recover unrelated or changed evidence.")
