"""Synthetic paid-transport authorization checks; never touches the real run."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_library_forward_slot_budget import prepared as old_prepared
from omni_story.library import microclip_state as mc, parent_cut_state, microclip_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def registered(old_prepared, monkeypatch):
    root, _, grant = old_prepared
    data = read(root / "library_state.json")
    for index in range(191, 207):
        ident = f"old_{index + 1:03d}"
        req, reply = {"old": index}, {"reply": index}
        write_json(root / "calls" / ident / "request.json", req)
        write_json(root / "calls" / ident / "response.json", reply)
        data["calls"].append({"id": ident, "name": "old_complete", "status": "received",
            "request_sha256": json_sha(req), "response_sha256": json_sha(reply), "usage": {}, "repair_of": None})
    data["request_count"] = 207
    parent = root / "render_0" / "final.mp4"
    parent.parent.mkdir(parents=True)
    parent.write_bytes(b"synthetic immutable 77-second parent")
    source = {"round": 0, "path": str(parent), "sha256": sha256_file(parent), "duration_s": 77.0}
    outline = root / "artifacts" / "parent_outline.json"
    write_json(outline, {"parent_sha256": source["sha256"], "slots": [
        {"slot_id": "s1", "start_s": 0.0, "end_s": 15.0}, {"slot_id": "s2", "start_s": 15.0, "end_s": 29.0}]})
    for i in (0, 3):
        path = root / "artifacts" / f"pc_result_{i}.json"
        value = {"completed": True}
        write_json(path, value)
        data["artifacts"][f"pc_result_{i}"] = [{"path": str(path), "sha256": json_sha(value)}]
    unknown = []
    for c in data["calls"]:
        if c["status"] == "uncertain":
            req = read(root / "calls" / c["id"] / "request.json")
            unknown.append({"call_id": c["id"], "request_sha256": c["request_sha256"],
                "media_sha256": req["media_sha256"], "scope": req["observation_scope"]})
    old = {"parents": [source], "outline_paths": {"0": str(outline)}, "authorization_path": grant["authorization_path"],
        "authorization_sha256": grant["authorization_sha256"], "unknown_inputs": unknown}
    monkeypatch.setattr(parent_cut_state, "get_auth", lambda *args, **kwargs: old)
    write_json(root / "library_state.json", data)
    auth = mc.record_authorization(root, "Run one slot-local iterative finecut after skill research.")
    monkeypatch.setattr(microclip_frames, "verify_grid", lambda path, **kwargs: read(path))
    return root, auth


def image_input(state, stage="mc_observe_0", start=0.0, end=15.0, *, anchor_key=None, anchor_frame_id=None):
    auth, root = state.authorization, state.output
    folder = root / "new_inputs" / stage
    folder.mkdir(parents=True, exist_ok=True)
    grid, png = folder / "grid.png", folder / "f_one.png"
    grid.write_bytes(("synthetic grid " + stage).encode())
    png.write_bytes(("synthetic frame " + stage).encode())
    parent = auth["parent"]
    scope = {"kind": "sparse_contact_sheet", "source_sha256": parent["sha256"], "source_start_s": start, "source_end_s": end}
    manifest = folder / "manifest.json"
    row = {"frame_id": "f_" + stage, "source_sha256": parent["sha256"], "source_time_s": start,
        "frame_end_s": start + .04, "decode_frame_index": int(start * 25), "pts": int(start * 25),
        "pts_time_s": start, "time_base": [1, 25], "png_path": str(png), "png_sha256": sha256_file(png)}
    value = {"schema": "microclip_grid_v1", "manifest_path": str(manifest),
        "source": {"path": parent["path"], "sha256": parent["sha256"], "timeline_origin_s": 0.0},
        "request": {"source_path": parent["path"], "source_sha256": parent["sha256"], "start_s": start, "end_s": end},
        "grid": {"path": str(grid), "sha256": sha256_file(grid), "crop_box": None, "resize_only": True}, "frames": [row]}
    write_json(manifest, value)
    (folder / "manifest.sha256").write_text(sha256_file(manifest), encoding="ascii")
    descriptor = {"policy": mc.POLICY, "stage": stage, "tool": "analyze_image", "media_path": str(grid),
        "media_sha256": sha256_file(grid), "observation_scope": scope, "lineage_path": str(manifest), "lineage_sha256": sha256_file(manifest)}
    if anchor_key:
        descriptor.update(anchor_key=anchor_key, anchor_frame_id=anchor_frame_id)
    state.set_artifact("mc_input_" + stage, descriptor)
    request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_image",
        "arguments": {"image_source": str(grid), "prompt": stage}, "media_sha256": sha256_file(grid), "observation_scope": scope}
    return stage, request


def complete(state, call, value):
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}})
    write_json(state.output / "calls" / call["id"] / "parsed.json", value)


def node(root, auth, code):
    if not shutil.which("node"):
        pytest.skip("Node required")
    module = (Path(mc.__file__).parent / "mcp_microclip_guard.mjs").as_uri()
    env = {"OMNI_LIBRARY_MICROCLIP_AUTH_FILE": auth["authorization_path"], "OMNI_LIBRARY_MICROCLIP_AUTH_SHA256": auth["authorization_sha256"]}
    script = f"import * as g from {json.dumps(module)};const root={json.dumps(str(root))},env={json.dumps(env)};{code}"
    return subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)


def test_register_mechanical_first_small_slot_and_idempotent_artifact(registered):
    root, auth = registered
    assert auth["baseline_request_count"] == 207 and auth["slot"]["slot_id"] == "s1"
    assert auth["numeric_total_request_limit"] is None
    state = mc.MicroclipState(root)
    state.set_artifact("mc_test", {"immutable": 1})
    state.set_artifact("mc_test", {"immutable": 1})
    with pytest.raises(LibraryStopped, match="existing_artifact_read_only"):
        state.set_artifact("mc_test", {"immutable": 2})
    assert node(root, auth, "console.log(g.loadMicroclip(root,env).state.calls.length)").stdout.strip() == "207"


def test_single_lane_and_exact_queued_grid_guard(registered):
    root, auth = registered
    state = mc.MicroclipState(root)
    name, req = image_input(state)
    call, _ = state.begin_call(name, req)
    assert call["id"].startswith("glm_208_")
    queued = {"job_id": call["id"], "tool": req["tool"], "arguments": req["arguments"]}
    path = root / "mcp_queue" / (call["id"] + ".request.json")
    write_json(path, queued)
    result = node(root, auth, f"console.log(g.microclipRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env))")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Infinity"
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call("mc_observe_1", req)
    queued["arguments"]["prompt"] = "changed"
    write_json(path, queued)
    assert node(root, auth, f"g.microclipRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env)").returncode != 0


def test_only_one_known_format_repair_and_no_third_attempt(registered):
    root, _ = registered
    state = mc.MicroclipState(root)
    name, req = image_input(state)
    call, folder = state.begin_call(name, req)
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": "invalid"}]}})
    with pytest.raises(LibraryStopped, match="known_format_failure_required"):
        state.begin_call(name + "_repair", req, repair_of=call)
    write_json(folder / "protocol_failure.json", {"error": "known parse error"})
    repaired = deepcopy(req)
    repaired["arguments"]["prompt"] = "repair once"
    repair, _ = state.begin_call(name + "_repair", repaired, repair_of=call)
    state.complete_call(repair, {"result": {"content": [{"type": "text", "text": "invalid again"}]}})
    with pytest.raises(LibraryStopped, match="repeated_stage"):
        state.begin_call(name + "_repair", repaired, repair_of=call)
    with pytest.raises(LibraryStopped, match="predecessor_not_parsed"):
        state.begin_call(*image_input(state, "mc_observe_1", 1.0, 2.0))


def test_unknown_and_old_call_mutation_blocked(registered):
    root, _ = registered
    state = mc.MicroclipState(root)
    call, _ = state.begin_call(*image_input(state))
    state.fail_call(call, "synthetic transport lost result")
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call("mc_motion", {})
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state.fail_call(state.data["calls"][0], "cannot change old reply")


@pytest.mark.parametrize("change", ["reply", "prefix", "old_artifact", "new_old_file"])
def test_historical_protection_python_and_node(registered, change):
    root, auth = registered
    if change == "reply":
        (root / "calls" / "old_001" / "response.json").write_text("{}")
    elif change == "prefix":
        (root / "mcp_http_sf_3.jsonl").write_text("changed prefix")
    elif change == "old_artifact":
        data = read(root / "library_state.json")
        data["artifacts"]["pc_result_0"] = []
        write_json(root / "library_state.json", data)
    else:
        (root / "calls" / "old_001" / "extra.json").write_text("{}")
    with pytest.raises(LibraryStopped):
        mc.get_auth(root, force=True)
    assert node(root, auth, "g.loadMicroclip(root,env)").returncode != 0


def test_append_journal_frozen_queue_and_stop_without_render(registered):
    root, auth = registered
    with (root / "mcp_http_sf_3.jsonl").open("ab") as f:
        f.write(b'{"new":"legitimate"}\n')
    mc.get_auth(root, force=True)
    result = node(root, auth, "const skip=g.microclipFrozenSlotJob(root,env);console.log(skip(root+'/mcp_queue/glm_166_sf_3_source_feedback_replan_v1.request.json'));")
    assert result.returncode == 0 and result.stdout.strip() == "true"
    state = mc.MicroclipState(root)
    path = state.finish({"status": "stopped", "model_quality_gate_passed": False})
    assert read(path)["status"] == "stopped"
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call("mc_observe_0", {})


def test_observation_progress_and_required_motion_predecessors(registered):
    root, _ = registered
    state = mc.MicroclipState(root)
    with pytest.raises(LibraryStopped, match="predecessor_not_parsed"):
        state.begin_call("mc_motion", {})
    call, _ = state.begin_call(*image_input(state))
    complete(state, call, {"visible": "synthetic first facts"})
    with pytest.raises(LibraryStopped, match="predecessor_not_parsed"):
        state.begin_call("mc_motion", {})
    second, _ = state.begin_call(*image_input(state, "mc_observe_1", 1.0, 2.0))
    complete(state, second, {"visible": "new detailed facts"})
    assert state.usage()["requests"] == 209


def video_input(state, stage, *, final=None, duration=15.0):
    parent = state.authorization["parent"]
    source_path = str(final) if final else parent["path"]
    source_sha = sha256_file(source_path)
    folder = state.output / "new_inputs" / stage
    folder.mkdir(parents=True, exist_ok=True)
    media = folder / "video.mp4"
    media.write_bytes(("synthetic actual crop " + stage).encode())
    scope = {"kind": "continuous_window", "source_sha256": source_sha, "source_start_s": 0.0, "source_end_s": duration}
    lineage = folder / "lineage.json"
    write_json(lineage, {**scope, "path": str(media), "sha256": sha256_file(media), "source_path": source_path})
    descriptor = {"policy": mc.POLICY, "stage": stage, "tool": "analyze_video", "media_path": str(media),
        "media_sha256": sha256_file(media), "observation_scope": scope, "lineage_path": str(lineage), "lineage_sha256": sha256_file(lineage)}
    state.set_artifact("mc_input_" + stage, descriptor)
    req = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video", "arguments": {"video_source": str(media), "prompt": stage},
        "media_sha256": sha256_file(media), "observation_scope": scope}
    return stage, req


def test_anchor_neighborhoods_compile_binding_and_actual_review(registered):
    from omni_story.library.microclip_contracts import validate_plan
    root, auth = registered
    state = mc.MicroclipState(root)
    for stage, start, end in (("mc_observe_0", 0.0, 15.0), ("mc_observe_1", 2.0, 4.0)):
        call, _ = state.begin_call(*image_input(state, stage, start, end))
        complete(state, call, {"facts": stage})
    call, _ = state.begin_call(*video_input(state, "mc_motion"))
    complete(state, call, {"facts": "independent motion"})
    anchor_value = {"start_frame_id": "f_mc_observe_0", "peak_frame_id": "f_mc_observe_1", "end_frame_id": "f_mc_observe_1",
        "reason": "synthetic model anchors", "expected_visible_change": "synthetic visible change", "limitations": []}
    call, _ = state.begin_call(*video_input(state, "mc_anchors"))
    complete(state, call, anchor_value)
    with pytest.raises(LibraryStopped, match="predecessor_not_parsed"):
        state.begin_call("mc_plan", {})
    for index, key in enumerate(("start", "peak", "end")):
        start, end = (0.0, .2) if index == 0 else (1.8, 2.2)
        name, req = image_input(state, f"mc_edges_{index}", start, end,
                               anchor_key=key, anchor_frame_id=anchor_value[key + "_frame_id"])
        call, _ = state.begin_call(name, req)
        write_json(root / "mcp_queue" / (call["id"] + ".request.json"), {"job_id": call["id"], "tool": req["tool"], "arguments": req["arguments"]})
        result = node(root, auth, f"console.log(g.microclipRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env))")
        assert result.returncode == 0, result.stderr
        complete(state, call, {"edge": "confirmed"})
    frames, boundaries = {}, set(anchor_value[k + "_frame_id"] for k in ("start", "peak", "end"))
    for key, entries in state.data["artifacts"].items():
        if key.startswith(("mc_input_mc_observe_", "mc_input_mc_edges_")):
            mf = read(read(entries[0]["path"])["lineage_path"])
            frames.update({f["frame_id"]: f for f in mf["frames"]})
            if key.startswith("mc_input_mc_edges_"):
                boundaries.update(f["frame_id"] for f in mf["frames"])
    model = {"shots": [{"start_frame_id": "f_mc_edges_0", "end_frame_id": "f_mc_edges_2", "speed": 1, "hold_s": 0,
        "reason": "synthetic deletion", "visible_change": "synthetic result"}], "preserved_visible_meaning": "synthetic meaning", "omitted_content": [], "limitations": []}
    call, _ = state.begin_call(*video_input(state, "mc_plan"))
    complete(state, call, model)
    source = {**auth["parent"], "source_id": "synthetic_parent"}
    expected = validate_plan(source, auth["slot"], model, frames, boundaries)
    changed = deepcopy(expected)
    changed["segments"][0]["speed"] = 2
    with pytest.raises(LibraryStopped, match="differs_from_model"):
        state.claim_render(changed, source)
    directory = state.claim_render(expected, source)
    directory.mkdir()
    final = directory / "final.mp4"
    final.write_bytes(b"synthetic rendered local candidate")
    write_json(directory / "render_result.json", {"sha256": sha256_file(final), "measured_duration_s": 1.84})
    for stem, value in (("mc_blind", {"visible_meaning": "synthetic blind facts"}), ("mc_review", {"status": "partial"})):
        call, _ = state.begin_call(*video_input(state, stem, final=final, duration=1.84))
        complete(state, call, value)
    with pytest.raises(LibraryStopped, match="model_review_did_not_pass"):
        state.finish({"status": "pass", "model_quality_gate_passed": True})
    result = state.finish({"status": "candidate_with_limitations", "model_quality_gate_passed": False,
        "final_video": str(final), "final_sha256": sha256_file(final)})
    assert read(result)["status"] == "candidate_with_limitations"
