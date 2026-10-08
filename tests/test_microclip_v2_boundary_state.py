"""Same-event continuation and both transport guards on synthetic inputs only."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from test_library_microclip_v2_state import image_input, node, queued_guard, complete
from omni_story.library import microclip_v2_state as mc, microclip_v2_boundary_state as boundary, microclip_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, LibraryState, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def resumed(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    parent = root / "parent.mp4"
    parent.write_bytes(b"synthetic actual input")
    slot = {"slot_id": "s0", "start_s": 0.0, "end_s": 15.0}
    outline = root / "outline.json"
    write_json(outline, {"slots": [slot]})
    folder = root / "artifacts" / mc.POLICY
    folder.mkdir(parents=True)
    grant_file = root / "artifacts" / "base_grant.json"
    write_json(grant_file, {"policy": "old immutable grant"})
    grant = {"policy": mc.POLICY, "stage_pattern": mc.STAGES, "authorization_path": str(grant_file),
             "authorization_sha256": sha256_file(grant_file), "execution_directory": str(folder),
             "allowed_render_directory": str(folder / "render"), "parent": {"path": str(parent), "sha256": sha256_file(parent)},
             "slot": slot, "outline_path": str(outline), "baseline_render_directories": [], "base_request_limit": 80,
             "protected_files": [], "unknown_inputs": [], "infrastructure_resume": {"next_stage": "mc2_region_0"},
             "known_failure_resume": None, "known_failure_dispatch_resume": None}
    base = LibraryState(root, {"synthetic": "fixed input"}, max_requests=80)
    data = base.data
    event = {"event_id": "e0", "start_frame_id": "f0", "end_frame_id": "f1", "obligation_ids": ["o0"]}
    for n in range(1, 232):
        name = "mc2_anchors" if n == 229 else "mc2_edge_0" if n == 230 else "mc2_edge_1" if n == 231 else "old_settled"
        ident = f"glm_{n:03d}_{name}"
        request = {"old": n}
        value = {"events": [event], "status": "ready", "blocking_questions": []} if n == 229 else {
            "status": "blocked" if n == 231 else "confirmed", "blocking_questions": ["need later frames"] if n == 231 else [],
            "confirmed_frame_id": "f0" if n == 230 else "f1"}
        response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
        call_folder = root / "calls" / ident
        write_json(call_folder / "request.json", request)
        write_json(call_folder / "response.json", response)
        write_json(call_folder / "parsed.json", value)
        data["calls"].append({"id": ident, "name": name, "status": "received", "request_sha256": json_sha(request),
                              "response_sha256": json_sha(response), "repair_of": None, "usage": {}})
    data["request_count"] = 231
    base._save()
    base.authorization = grant
    image_input(base, "mc2_edge_1", 1.7, 1.96, rows=[("f1", 1.8, 1.84)],
                event_id="e0", anchor_frame_id="f1", anchor_key="end")
    data = base.data
    result = folder / "result_dispatch_recovered.json"
    write_json(result, {"status": "stopped", "error": "boundary_missing_essential_evidence:known reply",
                        "final_video": None, "model_quality_gate_passed": False})
    base._save()
    base.set_artifact("mc2_dispatch_result", {"policy": mc.POLICY, "result_path": str(result)})
    knowledge = tmp_path / "generic.md"
    knowledge.write_text("Generic boundary reasoning, no creative source answer.", encoding="utf-8")
    old_get = mc.get_auth
    checks = []
    monkeypatch.setattr(mc, "get_auth", lambda *a, **kw: checks.append(kw) or grant)
    auth = boundary.record_boundary_resume(root, "Continue this boundary's missing observation.", knowledge)
    monkeypatch.setattr(mc, "get_auth", old_get)
    monkeypatch.setattr(microclip_frames, "verify_grid", lambda path, **kw: read(path))
    state = mc.SlotMicroclipState(root)
    monkeypatch.setattr(state, "_received", state._received)
    return root, auth, checks, state, event


def nav_input(state, index=1, round_index=0, **extra):
    name = f"mc2_edge_{index}_nav_{round_index}"
    original = read(state.data["artifacts"][f"mc2_input_mc2_edge_{index}"][0]["path"])
    descriptor = {**original, "stage": name, "edge_index": index, "event_id": "e0", "anchor_key": "end",
                  "boundary_round": round_index, "phase": "nav", **extra}
    state.set_artifact("mc2_input_" + name, descriptor)
    return name, {"provider": "official_vision_mcp_in_codex", "tool": "analyze_image",
                  "arguments": {"image_source": descriptor["media_path"], "prompt": name},
                  "media_sha256": descriptor["media_sha256"], "observation_scope": descriptor["observation_scope"]}


def decision(action="observe", **extra):
    return {"action": action, "event_id": "e0", "anchor_frame_id": "f1", "obligation_ids": ["o0"],
            "evidence_frame_ids": ["f1"], "blocking_questions": [], "limitations": [],
            "confirmed_frame_id": "f1" if action == "confirm" else None,
            "source_start_s": 1.9 if action == "observe" else None,
            "source_end_s": 2.2 if action == "observe" else None, **extra}


def test_registration_freezes_231_once_and_does_not_revalidate_old_helpers(resumed):
    root, auth, checks, state, _ = resumed
    assert checks == [{"force": True}]
    assert auth["boundary_resume"]["new_renders"] == 0 and auth["boundary_resume"]["goal_resumed"] is False
    state.assert_protected()
    assert checks == [{"force": True}]
    assert node(root, auth, "console.log(g.loadMicroclipV2(root,env).boundaryResume.baseline_request_count)").stdout.strip() == "231"


def test_navigation_pattern_separate_and_bounded():
    assert re.fullmatch(mc.STAGES, "mc2_edge_1_nav_0") is None
    assert mc._stage("mc2_edge_1_confirm_3_repair") == ("boundary", True)
    assert boundary.stage("mc2_edge_1_view_3_0") is None
    assert boundary.stage("mc2_edge_1_nav_4") is None
    assert boundary.stage("mc2_edge_12_nav_0") is None


def test_first_request_and_node_guard_bind_original_event(resumed):
    root, auth, checks, state, _ = resumed
    before = deepcopy(state.data["calls"])
    with pytest.raises(LibraryStopped, match="boundary_next_stage_required"):
        state.begin_call("mc2_plan", {})
    name, request = nav_input(state)
    call, _ = state.begin_call(name, request)
    queued_guard(state, auth, call, request)
    assert state.data["calls"][:231] == before
    complete(state, call, decision())
    assert checks == [{"force": True}]


@pytest.mark.parametrize("change", ["call", "file", "artifact", "source", "knowledge"])
def test_frozen_prefix_and_bytes_fail_in_both_guards(resumed, change):
    root, auth, _, state, _ = resumed
    if change == "call":
        value = read(root / "library_state.json")
        value["calls"][230]["status"] = "failed_known"
        write_json(root / "library_state.json", value)
    elif change == "file":
        (root / "calls" / state.data["calls"][230]["id"] / "parsed.json").write_text("{}")
    elif change == "artifact":
        value = read(root / "library_state.json")
        value["artifacts"]["mc2_dispatch_result"] = []
        write_json(root / "library_state.json", value)
    elif change == "source":
        Path(auth["parent"]["path"]).write_bytes(b"changed movie")
    else:
        Path(auth["boundary_resume"]["knowledge_path"]).write_text("changed generic knowledge")
    with pytest.raises(LibraryStopped):
        mc.get_auth(root, force=True)
    result = node(root, auth, "g.loadMicroclipV2(root,env)")
    assert result.returncode != 0


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_unknown_or_failed_new_request_cannot_start_another_stage(resumed, status):
    _, _, _, state, _ = resumed
    call, folder = state.begin_call(*nav_input(state))
    if status != "submitted":
        state.fail_call(call, "synthetic known/unknown stop", uncertain=status == "uncertain")
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call("mc2_edge_1_nav_1", {})


def test_original_blocked_reply_and_call_are_read_only(resumed):
    _, _, _, state, _ = resumed
    original = state.data["calls"][230]
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state._new(original)
    assert state._received("mc2_edge_1")[1]["status"] == "blocked"


def test_confirmation_routes_effective_reply_without_overwriting_original(resumed):
    root, auth, _, state, _ = resumed
    original = root / "calls" / state.data["calls"][230]["id"] / "parsed.json"
    old_bytes = original.read_bytes()
    nav, _ = state.begin_call(*nav_input(state))
    complete(state, nav, decision("confirm"))
    name, request = image_input(state, "mc2_edge_1_confirm_0", 1.7, 1.96, rows=[("f1", 1.8, 1.84)],
        edge_index=1, event_id="e0", anchor_key="end", boundary_round=0, phase="confirm",
        selected_frame_id="f1", nav_call_id=nav["id"])
    call, _ = state.begin_call(name, request)
    queued_guard(state, auth, call, request)
    complete(state, call, decision("confirm"))
    current = state.data["calls"][-1]
    boundary.record_effective_edge(state, 1, current)
    effective, value = state._received("mc2_edge_1")
    assert effective["id"] == call["id"] and value["status"] == "confirmed" and value["action"] == "confirm"
    assert original.read_bytes() == old_bytes
    assert node(root, auth, "console.log(g.loadMicroclipV2(root,env).state.calls.length)").stdout.strip() == "233"


def test_nav_confirmation_cannot_be_final_effective_receipt(resumed):
    _, _, _, state, _ = resumed
    nav, _ = state.begin_call(*nav_input(state))
    complete(state, nav, decision("confirm"))
    with pytest.raises(LibraryStopped, match="confirmed_navigation_required"):
        boundary.record_effective_edge(state, 1, state.data["calls"][-1])


def test_new_result_does_not_replace_original_stop(resumed):
    root, auth, _, state, _ = resumed
    old = Path(auth["execution_directory"]) / "result_dispatch_recovered.json"
    before = old.read_bytes()
    result = state.finish({"status": "stopped", "error": "new semantic limitation", "model_quality_gate_passed": False})
    assert result.name == "result_boundary_recovered.json" and old.read_bytes() == before
    assert read(root / "library_state.json")["artifacts"].get("mc2_boundary_result")
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call(*nav_input(state))


@pytest.mark.parametrize("change", ["stage", "index", "event", "anchor", "obligations", "frame"])
def test_effective_receipt_cannot_forge_event_or_unshown_frame(tmp_path, change):
    calls = [{"id": f"dummy_{n}", "name": "dummy", "status": "received"} for n in range(231)]
    artifacts = {}
    event = {"event_id": "e0", "start_frame_id": "f0", "end_frame_id": "f1", "obligation_ids": ["o0"]}
    values = [(228, "mc2_anchors", {"events": [event]}),
              (230, "mc2_edge_1", {"status": "blocked", "blocking_questions": ["known counterevidence"]})]
    for index, name, value in values:
        call = {"id": f"glm_{index+1}_{name}", "name": name, "status": "received"}
        calls[index] = call
        write_json(tmp_path / "calls" / call["id"] / "parsed.json", value)
        write_json(tmp_path / "calls" / call["id"] / "response.json", {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}})
    value = decision("confirm")
    name = "mc2_edge_1_confirm_0"
    if change == "stage": name = "mc2_edge_1_nav_0"
    if change == "index": name = "mc2_edge_2_confirm_0"
    if change == "event": value["event_id"] = "e1"
    if change == "anchor": value["anchor_frame_id"] = "wrong"
    if change == "obligations": value["obligation_ids"] = []
    if change == "frame": value["confirmed_frame_id"] = "unshown"
    response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
    call = {"id": "new_confirm", "name": name, "status": "received", "response_sha256": json_sha(response)}
    calls.append(call)
    folder = tmp_path / "calls" / call["id"]
    write_json(folder / "response.json", response)
    write_json(folder / "parsed.json", value)
    write_json(folder / "request.json", {"tool": "analyze_image"})
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"frames": [{"frame_id": "f1"}]})
    descriptor = {"lineage_path": str(manifest)}
    descriptor_file = tmp_path / "descriptor.json"
    write_json(descriptor_file, descriptor)
    artifacts["mc2_input_" + name] = [{"path": str(descriptor_file), "sha256": json_sha(descriptor)}]
    receipt = {"policy": boundary.POLICY, "edge_index": 1, "event_index": 0,
               "original_blocked_call_id": calls[230]["id"], "confirmed_call_id": call["id"],
               "model_response_sha256": call["response_sha256"], "parsed_sha256": json_sha(value),
               "confirmed_frame_id": value["confirmed_frame_id"], "input_descriptor_sha256": json_sha(descriptor)}
    receipt_file = tmp_path / "receipt.json"
    write_json(receipt_file, receipt)
    artifacts["mc2_effective_edge_1"] = [{"path": str(receipt_file), "sha256": json_sha(receipt)}]
    with pytest.raises(LibraryStopped):
        boundary.effective_edge(tmp_path, {"calls": calls, "artifacts": artifacts}, 1)


@pytest.mark.parametrize("bad", [None, "fake_id", "already_received", "noncontinuous"])
def test_page_progress_is_actual_new_frame_difference(tmp_path, monkeypatch, bad):
    parent = tmp_path / "parent.mp4"
    parent.write_bytes(b"synthetic source")
    source_sha = sha256_file(parent)
    call = {"id": "navigation", "name": "mc2_edge_1_nav_0", "status": "received"}
    value = decision("observe")
    write_json(tmp_path / "calls" / call["id"] / "parsed.json", value)
    write_json(tmp_path / "calls" / call["id"] / "response.json", {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}})
    rows = [{"frame_id": "new_a", "source_time_s": 1.92, "decode_frame_index": 48},
            {"frame_id": "new_b", "source_time_s": 1.96, "decode_frame_index": 49}]
    if bad == "noncontinuous": rows[1]["decode_frame_index"] = 50
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"frames": rows})
    monkeypatch.setattr(mc, "_verify_grid", lambda *args, **kw: read(manifest))
    artifacts = {}
    if bad == "already_received":
        descriptor = {"tool": "analyze_image", "lineage_path": str(manifest), "observation_scope": {"source_sha256": source_sha}}
        descriptor_path = tmp_path / "descriptor.json"
        write_json(descriptor_path, descriptor)
        artifacts["mc2_input_mc2_edge_1_nav_0"] = [{"path": str(descriptor_path)}]
    plan = {"decision_call_id": call["id"], "decision_stage": call["name"], "source_start_s": 1.9, "source_end_s": 2.2,
            "pages": [{"manifest_path": str(manifest)}], "new_frame_ids": ["fake"] if bad == "fake_id" else ["new_a", "new_b"]}
    auth = {"parent": {"path": str(parent), "sha256": source_sha}, "slot": {"start_s": 0.0, "end_s": 15.0}}
    if bad is None:
        assert boundary.page_plan_proof(tmp_path, {"calls": [call], "artifacts": artifacts}, auth, plan) == plan
    else:
        with pytest.raises(LibraryStopped, match="actual_new_continuous_frames_required"):
            boundary.page_plan_proof(tmp_path, {"calls": [call], "artifacts": artifacts}, auth, plan)
