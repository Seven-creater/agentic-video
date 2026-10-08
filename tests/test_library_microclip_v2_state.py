"""Synthetic append-only/transport checks; no accounts, models, or real runs."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_library_microclip_state import registered as old_registered, old_prepared, read, complete
from omni_story.library import microclip_state, microclip_v2_state as mc, microclip_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


@pytest.fixture
def registered(old_registered, monkeypatch):
    root, old = old_registered
    data = read(root / "library_state.json")
    for index in range(207, 218):
        ident = f"old_{index + 1:03d}"
        req, reply = {"old": index}, {"reply": index}
        write_json(root / "calls" / ident / "request.json", req)
        write_json(root / "calls" / ident / "response.json", reply)
        data["calls"].append({"id": ident, "name": "settled_old_stage", "status": "received",
                              "request_sha256": json_sha(req), "response_sha256": json_sha(reply),
                              "usage": {}, "repair_of": None})
    data["request_count"] = 218
    final = Path(old["allowed_render_directory"]) / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"settled limited microclip v1 candidate")
    result = final.parent.parent / "result_recovered.json"
    write_json(result, {"status": "candidate_with_limitations", "final_video": str(final),
                        "final_sha256": sha256_file(final), "model_quality_gate_passed": False})
    receipt = root / "artifacts" / "mc_recovered_result_001.json"
    value = {"policy": microclip_state.POLICY, "result_path": str(result)}
    write_json(receipt, value)
    data["artifacts"]["mc_recovered_result"] = [{"path": str(receipt), "sha256": json_sha(value)}]
    write_json(root / "library_state.json", data)
    checks = []
    monkeypatch.setattr(microclip_state, "get_auth", lambda *a, **kw: checks.append(kw) or old)
    monkeypatch.setattr(microclip_frames, "verify_grid", lambda path, **kwargs: read(path))
    auth = mc.record_authorization(root, "Continue with one corrected full-slot experiment.")
    return root, auth, checks


def node(root, auth, code):
    if not shutil.which("node"):
        pytest.skip("Node required")
    module = (Path(mc.__file__).parent / "mcp_microclip_v2_guard.mjs").as_uri()
    env = {"OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE": auth["authorization_path"],
           "OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256": auth["authorization_sha256"]}
    script = f"import * as g from {json.dumps(module)};const root={json.dumps(str(root))},env={json.dumps(env)};{code}"
    return subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)


def image_input(state, stage, start=0.0, end=15.0, rows=None, requested_targets=None, **extra):
    root, parent = state.output, state.authorization["parent"]
    folder = root / "new_inputs" / stage
    folder.mkdir(parents=True, exist_ok=True)
    grid = folder / "grid.png"
    grid.write_bytes(("synthetic grid " + stage).encode())
    frames = []
    for ident, time, last in rows or [("f0", start, start + .04)]:
        png = folder / (ident + ".png")
        png.write_bytes(("synthetic frame " + ident).encode())
        frames.append({"frame_id": ident, "source_sha256": parent["sha256"], "source_time_s": time,
                       "requested_time_s": (requested_targets or {}).get(ident, time),
                       "frame_end_s": last, "decode_frame_index": round(time * 25), "pts": round(time * 25),
                       "pts_time_s": time, "time_base": [1, 25], "png_path": str(png), "png_sha256": sha256_file(png)})
    scope = {"kind": "sparse_contact_sheet", "source_sha256": parent["sha256"], "source_start_s": start, "source_end_s": end}
    manifest = folder / "manifest.json"
    value = {"schema": "microclip_grid_v1", "manifest_path": str(manifest),
             "source": {"path": parent["path"], "sha256": parent["sha256"], "timeline_origin_s": 0.0},
             "request": {"source_path": parent["path"], "source_sha256": parent["sha256"], "start_s": start, "end_s": end},
             "grid": {"path": str(grid), "sha256": sha256_file(grid), "crop_box": None, "resize_only": True}, "frames": frames}
    write_json(manifest, value)
    (folder / "manifest.sha256").write_text(sha256_file(manifest), encoding="ascii")
    descriptor = {"policy": mc.POLICY, "stage": stage, "tool": "analyze_image", "media_path": str(grid),
                  "media_sha256": sha256_file(grid), "observation_scope": scope, "lineage_path": str(manifest),
                  "lineage_sha256": sha256_file(manifest), **extra}
    state.set_artifact("mc2_input_" + stage, descriptor)
    return stage, {"provider": "official_vision_mcp_in_codex", "tool": "analyze_image",
                   "arguments": {"image_source": str(grid), "prompt": stage}, "media_sha256": sha256_file(grid), "observation_scope": scope}


def video_input(state, stage, start=0.0, end=15.0, **extra):
    folder = state.output / "new_inputs" / stage
    folder.mkdir(parents=True, exist_ok=True)
    media, parent = folder / "video.mp4", state.authorization["parent"]
    media.write_bytes(("synthetic crop " + stage).encode())
    scope = {"kind": "continuous_window", "source_sha256": parent["sha256"], "source_start_s": start, "source_end_s": end}
    lineage = folder / "lineage.json"
    write_json(lineage, {**scope, "path": str(media), "sha256": sha256_file(media), "source_path": parent["path"], "audio_present": False})
    state.set_artifact("mc2_input_" + stage, {"policy": mc.POLICY, "stage": stage, "tool": "analyze_video",
                       "media_path": str(media), "media_sha256": sha256_file(media), "observation_scope": scope,
                       "lineage_path": str(lineage), "lineage_sha256": sha256_file(lineage), **extra})
    return stage, {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video",
                   "arguments": {"video_source": str(media), "prompt": stage}, "media_sha256": sha256_file(media), "observation_scope": scope}


def queued_guard(state, auth, call, req):
    write_json(state.output / "mcp_queue" / (call["id"] + ".request.json"),
               {"job_id": call["id"], "tool": req["tool"], "arguments": req["arguments"]})
    result = node(state.output, auth, f"console.log(g.microclipV2RequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env))")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Infinity"


def test_registration_frozen_prefix_and_no_revalidation_of_old_adapter(registered):
    root, auth, checks = registered
    assert len(checks) == 1 and auth["baseline_request_count"] == 218
    assert auth["numeric_total_request_limit"] is None and auth["goal_resumed"] is False
    state = mc.SlotMicroclipState(root)
    name, req = image_input(state, "mc2_overview")
    call, _ = state.begin_call(name, req)
    queued_guard(state, auth, call, req)
    complete(state, call, {"known": "new overview"})
    state.usage()
    assert len(checks) == 1 and state.usage()["requests"] == 219
    assert node(root, auth, "console.log(g.loadMicroclipV2(root,env).state.calls.length)").stdout.strip() == "219"


def test_one_repair_requires_known_failure_and_unknown_cannot_replay(registered):
    root, auth, _ = registered
    state = mc.SlotMicroclipState(root)
    name, req = image_input(state, "mc2_overview")
    call, folder = state.begin_call(name, req)
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": "invalid"}]}})
    with pytest.raises(LibraryStopped, match="known_format_failure_required"):
        state.begin_call(name + "_repair", req, repair_of=call)
    write_json(folder / "protocol_failure.json", {"error": "format"})
    fixed = deepcopy(req)
    fixed["arguments"]["prompt"] = "one repair"
    repair, _ = state.begin_call(name + "_repair", fixed, repair_of=call)
    queued_guard(state, auth, repair, fixed)
    state.fail_call(repair, "transport lost", uncertain=True)
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending"):
        state.begin_call("mc2_motion", {})
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state.fail_call(state.data["calls"][0], "old")


@pytest.mark.parametrize("change", ["reply", "prefix", "old_artifact", "new_old_file"])
def test_history_mutations_block_python_and_node(registered, change):
    root, auth, _ = registered
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
    assert node(root, auth, "g.loadMicroclipV2(root,env)").returncode != 0


def test_frozen_jobs_and_one_immutable_result(registered):
    root, auth, _ = registered
    result = node(root, auth, "const skip=g.microclipV2FrozenSlotJob(root,env);console.log(skip(root+'/mcp_queue/glm_166_sf_3_source_feedback_replan_v1.request.json'));")
    assert result.returncode == 0 and result.stdout.strip() == "true"
    state = mc.SlotMicroclipState(root)
    state.finish({"status": "stopped", "model_quality_gate_passed": False})
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call("mc2_overview", {})


def prepare_plan(registered, *, omit_anchor=False):
    root, auth, _ = registered
    state = mc.SlotMicroclipState(root)
    rows = [("f0", 0.0, .04), ("f5", 5.0, 5.04), ("f10", 10.0, 10.04), ("f14", 14.96, 15.0)]
    for input_, value in [(image_input(state, "mc2_overview", rows=rows), {"facts": "neutral overview"}),
                          (video_input(state, "mc2_motion"), {"observation_status": "complete"})]:
        call, _ = state.begin_call(*input_)
        queued_guard(state, auth, call, input_[1])
        complete(state, call, value)
    intent = {"status": "ready", "blocking_questions": [], "limitations": [], "original_claim_checks": [],
              "obligations": [{"obligation_id": "o1", "description": "synthetic before and after", "support": "visual",
                               "evidence_frame_ids": ["f0", "f14"], "original_claim_ids": []}],
              "search_regions": [{"region_id": "r0", "start_frame_id": "f0", "end_frame_id": "f5", "question": "first event?", "obligation_ids": ["o1"]},
                                 {"region_id": "r1", "start_frame_id": "f10", "end_frame_id": "f14", "question": "result?", "obligation_ids": ["o1"]}]}
    call, _ = state.begin_call(*image_input(state, "mc2_intent", rows=rows))
    complete(state, call, intent)
    for i, selected in enumerate((rows[:2], rows[2:])):
        name, req = image_input(state, f"mc2_region_{i}", selected[0][1], selected[-1][2], rows=selected, region_id=f"r{i}")
        call, _ = state.begin_call(name, req)
        queued_guard(state, auth, call, req)
        complete(state, call, {"facts": "synthetic independently observed"})
    anchors = {"status": "ready", "blocking_questions": [], "resolved_conflicts": [], "limitations": [],
               "events": [{"event_id": f"e{i}", "start_frame_id": selected[0][0], "end_frame_id": selected[-1][0],
                           "obligation_ids": ["o1"], "reason": "synthetic necessary event"} for i, selected in enumerate((rows[:2], rows[2:]))]}
    call, _ = state.begin_call(*image_input(state, "mc2_anchors", rows=rows))
    complete(state, call, anchors)
    for i, (ident, time, end) in enumerate(rows):
        shown_id = "different_frame" if omit_anchor and i == 0 else ident
        name, req = image_input(state, f"mc2_edge_{i}", max(0.0, time - .08), min(15.0, time + .08),
                               rows=[(shown_id, time, end)], anchor_frame_id=ident, anchor_key=("start", "end")[i % 2], event_id=f"e{i // 2}")
        call, _ = state.begin_call(name, req)
        queued_guard(state, auth, call, req)
        complete(state, call, {"status": "confirmed", "blocking_questions": [], "confirmed_frame_id": ident})
    model = {"shots": [{"start_frame_id": a, "end_frame_id": b, "speed": 1, "hold_s": 0, "reason": "necessary information",
                        "visible_change": "synthetic change", "obligation_ids": ["o1"]} for a, b in (("f0", "f5"), ("f10", "f14"))],
             "preserved_visible_meaning": "synthetic whole-slot event", "omitted_content": [], "limitations": [],
             "obligation_coverage": [{"obligation_id": "o1", "shot_indices": [0, 1], "source_evidence_frame_ids": ["f0", "f14"],
                                      "exposure_s": 10.04, "necessary_exposure_s": 4, "reason": "allocated actual screen time"}]}
    call, _ = state.begin_call(*video_input(state, "mc2_plan"))
    complete(state, call, model)
    return state, auth


@pytest.mark.parametrize("verdict", ["supported", "unsupported"])
def test_multiple_events_remain_reachable_exact_slice_checks_and_semantic_block(registered, verdict):
    state, auth = prepare_plan(registered)
    source = {**auth["parent"], "source_id": "synthetic_parent"}
    plan = state._planned(source)
    assert [(s["source_in_s"], s["source_out_s"]) for s in plan["segments"]] == [(0, 5.04), (10, 15)]
    with pytest.raises(LibraryStopped, match="predecessor_not_parsed"):
        state.begin_call("mc2_source_check", {})
    for i, segment in enumerate(plan["segments"]):
        bounds = {k: segment[k] for k in ("source_in_s", "source_out_s")}
        name, req = video_input(state, f"mc2_slice_{i}", bounds["source_in_s"], bounds["source_out_s"], slice_index=i, source_range=bounds)
        call, _ = state.begin_call(name, req)
        queued_guard(state, auth, call, req)
        complete(state, call, {"observation_status": "complete"})
    call, _ = state.begin_call(*video_input(state, "mc2_source_check"))
    complete(state, call, {"status": "ready", "blocking_questions": [], "limitations": [],
                          "obligation_checks": [{"obligation_id": "o1", "verdict": verdict, "reason": "synthetic actual slice comparison"}]})
    if verdict == "unsupported":
        with pytest.raises(LibraryStopped, match="source_semantics_blocked"):
            state.claim_render(plan, source)
        assert not Path(auth["allowed_render_directory"]).exists()
    else:
        changed = deepcopy(plan)
        changed["segments"][0]["speed"] = 2
        with pytest.raises(LibraryStopped, match="compiled_plan_differs_from_model"):
            state.claim_render(changed, source)
        assert state.claim_render(plan, source) == Path(auth["allowed_render_directory"])
        assert state.claim_render(plan, source) == Path(auth["allowed_render_directory"])
        with pytest.raises(LibraryStopped, match="old_artifact_read_only"):
            state.set_artifact("mc_result", {})


def test_missing_nominee_anchor_blocked_before_transport(registered):
    with pytest.raises(LibraryStopped, match="anchor_must_be_shown_with_neighbors"):
        prepare_plan(registered, omit_anchor=True)


def test_full_decoded_vfr_coverage_does_not_override_sampling_quality_limit(tmp_path, monkeypatch):
    # Unit check of the quality gate: all actual decoder images may still be too
    # far apart. This is an observation limitation, not a format-repair trigger.
    state = object.__new__(mc.SlotMicroclipState)
    auth = {"allowed_render_directory": str(tmp_path / "render"), "execution_directory": str(tmp_path)}
    coverage, page_plan = tmp_path / "coverage.json", tmp_path / "page_plan.json"
    write_json(coverage, {"target_gap_met": False, "all_decoded_frames_shown": True})
    write_json(page_plan, {"coverage_path": str(coverage)})
    state.data = {"calls": [], "artifacts": {"mc2_blind_page_plan": [{"path": str(page_plan)}]}}
    monkeypatch.setattr(state, "assert_protected", lambda: auth)
    monkeypatch.setattr(state, "_source_gate", lambda: {"obligation_checks": [{"verdict": "supported"}]})
    monkeypatch.setattr(state, "_blind_pages", lambda: [{}])
    monkeypatch.setattr(state, "_planned", lambda: {"segments": [{}]})
    intent = {"original_claim_checks": [], "obligations": [{"support": "visual"}]}
    monkeypatch.setattr(state, "_received", lambda stem: ({}, intent if stem == "mc2_intent" else {"observation_status": "complete"}))
    with pytest.raises(LibraryStopped, match="output_sampling_limited"):
        state.finish({"model_quality_gate_passed": True})
    assert not (tmp_path / "result.json").exists()
