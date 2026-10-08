"""Synthetic ledger/transport guards; no model calls or real run mutations."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import forward_slot_budget as forward, slot_finecut_budget
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / "original_run"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"Synthetic library source; not a real movie.")
    lock = {"reference_sha256": "a" * 64,
            "library_sources": [{"source_id": "src_fixture", "sha256": sha256_file(source)}]}
    state = LibraryState(root, lock)
    data = deepcopy(state.data)
    unknowns = {3: "glm_004_coarse_978d5360_01", 130: "glm_131_active_10_draft",
                165: "glm_166_sf_3_source_feedback_replan_v1"}
    for index in range(191):
        ident = unknowns.get(index, f"old_{index + 1:03d}")
        request = {"synthetic_old": index}
        status = "received"
        if index in unknowns:
            status = "uncertain"
            media = root / "old_unknown" / str(index) / "video.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(("old unknown input " + str(index)).encode())
            scope = {"kind": "sparse_contact_sheet" if index == 3 else "continuous_window",
                     "source_sha256": str(index % 10) * 64, "source_start_s": 0.0, "source_end_s": 34.0}
            request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video",
                       "media_sha256": sha256_file(media), "observation_scope": scope,
                       "arguments": {"video_source": str(media), "prompt": "Old immutable unknown task"}}
            write_json(root / "mcp_queue" / (ident + ".request.json"),
                       {"job_id": ident, "tool": request["tool"], "arguments": request["arguments"]})
            write_json(root / "mcp_queue" / (ident + ".started.json"), {"job_id": ident, "started": "old"})
        row = {"id": ident, "name": "old_synthetic", "status": status,
               "request_sha256": json_sha(request), "repair_of": None, "usage": {}}
        write_json(root / "calls" / ident / "request.json", request)
        if status == "received":
            reply = {"old_response": index}
            write_json(root / "calls" / ident / "response.json", reply)
            row["response_sha256"] = json_sha(reply)
        data["calls"].append(row)
    data["request_count"] = 191
    base = root / "artifacts" / "original_comparison"
    base.mkdir(parents=True)
    for name in ("preparation.json", "methods.json", "knowledge.md"):
        (base / name).write_text("Synthetic immutable infrastructure.", encoding="utf-8")
    grant = {"execution_directory": str(base), "renders_per_parent": 1, "repairs_per_stage": 1,
             "parent_rounds": [0, 3], "allowed_render_directories": [str(base / f"render_{p}" / "render") for p in (0, 3)],
             "preparation_path": str(base / "preparation.json"), "reference_methods_path": str(base / "methods.json"),
             "knowledge_path": str(base / "knowledge.md")}
    original_path = base / "authorization.json"
    write_json(original_path, grant)
    grant.update(authorization_path=str(original_path), authorization_sha256=sha256_file(original_path))
    data["artifacts"]["slot_finecut_comparison_v1"] = [{"path": str(original_path), "sha256": json_sha(_read(original_path))}]
    write_json(root / "library_state.json", data)
    journal = root / "mcp_http_sf_3.jsonl"
    journal.write_bytes(b'{"type":"synthetic_old_evidence","number":1.0}\n')
    monkeypatch.setattr(slot_finecut_budget, "load_authorization", lambda path: grant)
    return root, source, grant


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def registered(prepared):
    root, source, grant = prepared
    auth = forward.record_authorization(root, "Continue the two independent existing video tests after fixing source/output scope.")
    return root, source, grant, auth


def request(root, key, *, source_sha=None, start=1.0, end=3.0, prompt=None):
    media = root / "forward_inputs" / key / "video.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    if not media.exists():
        media.write_bytes(("Synthetic input " + key).encode())
    scope = {"kind": "continuous_window", "source_sha256": source_sha or "9" * 64,
             "source_start_s": start, "source_end_s": end}
    write_json(media.parent / "lineage.json", scope)
    return {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video",
            "media_sha256": sha256_file(media), "observation_scope": scope,
            "arguments": {"video_source": str(media), "prompt": prompt or ("Independent task " + key)}}


def complete(state, call, body):
    reply = {"status": "complete", "result": {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}]}}
    state.complete_call(call, reply)
    write_json(state.output / "calls" / call["id"] / "parsed.json", body)


def queued(state, call):
    value = _read(state.output / "calls" / call["id"] / "request.json")
    write_json(state.output / "mcp_queue" / (call["id"] + ".request.json"),
               {"job_id": call["id"], "tool": value["tool"], "arguments": value["arguments"]})


def node(registered, code):
    root, _, _, auth = registered
    tool = shutil.which("node")
    if not tool:
        pytest.skip("Node unavailable")
    module = (Path(__file__).parents[1] / "omni_story/library/mcp_forward_slot_guard.mjs").as_uri()
    script = "import * as g from " + json.dumps(module) + ";\n" + \
        "const root=" + json.dumps(str(root)) + ";const env=" + json.dumps({
            "OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE": auth["authorization_path"],
            "OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256": auth["authorization_sha256"]}) + ";\n" + code
    return subprocess.run([tool, "--input-type=module", "-e", script], capture_output=True, text=True)


def test_activation_freezes_191_and_is_idempotent(registered):
    root, _, _, auth = registered
    before = (root / "library_state.json").read_bytes()
    again = forward.record_authorization(root, "Already authorized continuation")
    assert again == auth
    assert (root / "library_state.json").read_bytes() == before
    assert auth["numeric_total_request_limit"] is None and not auth["goal_resumed"]
    assert auth["max_slices_per_parent"] == 32 and auth["renders_per_parent"] == 1
    assert auth["old_execution_directory"] == registered[2]["execution_directory"]
    checked = node(registered, "console.log(g.loadForwardSlot(root,env).policy.baseline_request_count)")
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip() == "191"


@pytest.mark.parametrize("what", ["old_reply", "old_artifact", "old_call_prefix", "new_historical_parsed", "journal_prefix"])
def test_history_changes_rejected_in_python_and_node(registered, what):
    root, _, grant, _ = registered
    if what == "old_reply":
        write_json(root / "calls/old_001/response.json", {"changed": True})
    elif what == "old_artifact":
        Path(grant["knowledge_path"]).write_text("changed", encoding="utf-8")
    elif what == "old_call_prefix":
        data = _read(root / "library_state.json"); data["calls"][0]["usage"] = {"fake": 1}; write_json(root / "library_state.json", data)
    elif what == "new_historical_parsed":
        write_json(root / "calls/old_001/parsed.json", {"retroactive_pass": True})
    else:
        (root / "mcp_http_sf_3.jsonl").write_bytes(b"changed old bytes")
    with pytest.raises(LibraryStopped):
        forward.get_auth(root)
    assert node(registered, "g.loadForwardSlot(root,env)").returncode != 0


def test_journal_append_allowed_and_frozen_queue_skipped_without_reply(registered):
    root, _, _, _ = registered
    with (root / "mcp_http_sf_3.jsonl").open("ab") as stream:
        stream.write(b'{"new_known_independent_request":true}\n')
    forward.get_auth(root)
    result = node(registered, "const skip=g.forwardFrozenSlotJob(root,env);console.log(skip(root+'/mcp_queue/glm_166_sf_3_source_feedback_replan_v1.request.json'))")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"
    assert not (root / "mcp_queue/glm_166_sf_3_source_feedback_replan_v1.response.json").exists()


def test_two_lanes_overlap_with_one_pending_each(registered):
    root, _, _, _ = registered
    state = forward.ForwardSlotState(root)
    call0, _ = state.begin_call("sfv2_0_reconstruct", request(root, "r0"))
    call3, _ = state.begin_call("sfv2_3_reconstruct", request(root, "r3", start=27.0, end=34.0))
    queued(state, call0); queued(state, call3)
    with pytest.raises(LibraryStopped, match="same_parent_pending"):
        state.begin_call("sfv2_0_slice_" + "a" * 16, request(root, "r0slice"))
    check = node(registered, "console.log(g.forwardSlotRequestLimit(root,{job_id:" + json.dumps(call0["id"]) + "},env))")
    assert check.returncode == 0, check.stderr
    assert check.stdout.strip() == "Infinity"
    complete(state, call0, {"plan": {"synthetic": 0}})
    complete(state, call3, {"plan": {"synthetic": 3}})
    assert state.usage()["requests"] == 193


def test_new_unknown_halts_both_lanes_and_old_calls_readonly(registered):
    root, _, _, _ = registered
    state = forward.ForwardSlotState(root)
    call, _ = state.begin_call("sfv2_0_reconstruct", request(root, "unknownnew"))
    state.fail_call(call, "Synthetic lost reply", uncertain=True)
    with pytest.raises(LibraryStopped, match="new_unknown_or_failure_stops_all"):
        state.begin_call("sfv2_3_reconstruct", request(root, "othernew"))
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state.complete_call("old_001", {"fake": "new"})


def test_one_repair_only_no_third_stage_and_same_scope(registered):
    root, _, _, _ = registered
    state = forward.ForwardSlotState(root)
    original_request = request(root, "repair")
    original, _ = state.begin_call("sfv2_0_reconstruct", original_request)
    state.complete_call(original, {"status": "complete", "result": {"content": [{"type": "text", "text": "invalid"}]}})
    changed = deepcopy(original_request); changed["arguments"]["prompt"] += " sole format repair"
    repair, _ = state.begin_call("sfv2_0_reconstruct_repair", changed, repair_of=original)
    complete(state, repair, {"plan": {"fixed": True}})
    with pytest.raises(LibraryStopped):
        state.begin_call("sfv2_0_reconstruct_repair_repair", changed, repair_of=repair)
    with pytest.raises(LibraryStopped, match="stage_repetition"):
        state.begin_call("sfv2_0_reconstruct", changed)


def test_unknown_media_and_scope_not_replayable(registered):
    root, _, _, auth = registered
    state = forward.ForwardSlotState(root)
    lost = auth["unknown_inputs"][-1]
    value = request(root, "renamed_lost", source_sha=lost["scope"]["source_sha256"], start=0.0, end=34.0)
    with pytest.raises(LibraryStopped, match="unknown_input_replay"):
        state.begin_call("sfv2_3_reconstruct", value)
    assert len(_read(root / "library_state.json")["calls"]) == 191


def test_final_slice_count_and_actual_source_sha(registered):
    root, source, _, _ = registered
    state = forward.ForwardSlotState(root)
    catalog = {"sources": [{"source_id": "src_fixture", "sha256": sha256_file(source), "path": str(source)}]}
    segment = {"source_id": "src_fixture", "source_in_s": 1.0, "source_out_s": 3.0}
    state.assert_source_inputs({"segments": [segment]}, catalog)
    with pytest.raises(LibraryStopped, match="final_slice_limit"):
        state.assert_source_inputs({"segments": [segment] * 33}, catalog)
    source.write_bytes(b"Modified actual movie")
    with pytest.raises(LibraryStopped, match="actual_source_changed"):
        state.assert_source_inputs({"segments": [segment]}, catalog)


def setup_render(registered):
    root, source, _, _ = registered
    state = forward.ForwardSlotState(root)
    plan = {"segments": [{"segment_id": "new_seg", "source_id": "src_fixture", "source_in_s": 1.0, "source_out_s": 3.0}]}
    reconstruct, _ = state.begin_call("sfv2_0_reconstruct", request(root, "reconstruct"))
    complete(state, reconstruct, {"plan": plan})
    sliced_request = request(root, "source", source_sha=sha256_file(source))
    sliced, _ = state.begin_call("sfv2_0_slice_" + "a" * 16, sliced_request)
    observation = {"segment_id": "old_source_label", "source_id": "src_fixture", "source_sha256": sha256_file(source),
                   "source_in_s": 1.0, "source_out_s": 3.0, "proxy_sha256": sliced_request["media_sha256"]}
    complete(state, sliced, observation)
    comparison = {"protocol": "scoped_source_evidence_v1", "plan_sha256": json_sha(plan),
                  "segments": [{"segment_id": "new_seg", "observation_sha256": json_sha(observation),
                                "claim_checks": [{"claim_id": "atomic", "status": "supported"}], "uncertainties": []}], "limitations": []}
    compared, _ = state.begin_call("sfv2_0_compare", request(root, "batch_comparison"))
    complete(state, compared, comparison)
    state._reload()
    source_call = state._call(sliced)
    binding = {"segment_id": "new_seg", "observation_sha256": json_sha(observation),
               **{k: observation[k] for k in ("source_id", "source_sha256", "source_in_s", "source_out_s", "proxy_sha256")},
               "origin_call_id": sliced["id"], "origin_request_sha256": source_call["request_sha256"],
               "origin_response_sha256": source_call["response_sha256"],
               "origin_parsed_file_sha256": sha256_file(root / "calls" / sliced["id"] / "parsed.json")}
    manifest = {"protocol": "scoped_source_evidence_v1", "plan_sha256": json_sha(plan), "observations": [observation],
                "observation_bindings": [binding], "comparison": comparison, "compare_call_id": compared["id"], "blockers": []}
    return state, plan, manifest


def test_render_claim_bound_to_model_plan_exact_facts_and_single_directory(registered):
    state, plan, manifest = setup_render(registered)
    directory = state.claim_render(0, plan, manifest)
    assert str(directory).endswith("fact_grounded_v1\\render_0\\render") or str(directory).endswith("fact_grounded_v1/render_0/render")
    assert not directory.exists()
    assert state.claim_render(0, plan, manifest) == directory
    changed = deepcopy(plan); changed["segments"][0]["source_out_s"] = 4.0
    with pytest.raises(LibraryStopped):
        state.claim_render(0, changed, manifest)
    assert "sfv2_0_render_claim" in state.data["artifacts"]
    assert "sf_0_render_claim" not in state.data["artifacts"]


@pytest.mark.parametrize("change", ["source_range", "source_fact_hash", "source_claim_unsupported", "blockers", "old_parsed_mutation"])
def test_invalid_manifest_blocks_render(registered, change):
    state, plan, manifest = setup_render(registered)
    if change == "source_range":
        manifest["observation_bindings"][0]["source_out_s"] = 4.0
    elif change == "source_fact_hash":
        manifest["observation_bindings"][0]["observation_sha256"] = "b" * 64
    elif change == "source_claim_unsupported":
        # The changed comparison must match the model reply and remains a gate failure.
        comparison = deepcopy(manifest["comparison"])
        comparison["segments"][0]["claim_checks"][0]["status"] = "unsupported"
        call = state._call(manifest["compare_call_id"])
        folder = state.output / "calls" / call["id"]
        response = {"status": "complete", "result": {"content": [{"type": "text", "text": json.dumps(comparison)}]}}
        write_json(folder / "response.json", response); write_json(folder / "parsed.json", comparison)
        call["response_sha256"] = json_sha(response); state._save()
        manifest["comparison"] = comparison
    elif change == "blockers":
        manifest["blockers"] = [{"reason": "Missing visible result"}]
    else:
        binding = manifest["observation_bindings"][0]
        write_json(state.output / "calls" / binding["origin_call_id"] / "parsed.json", {"invented": True})
    with pytest.raises((LibraryStopped, KeyError)):
        state.claim_render(0, plan, manifest)
    assert not Path(state.authorization["allowed_render_directories"][0]).exists()


def test_finish_is_immutable_and_closes_lane(registered):
    root, _, _, _ = registered
    state = forward.ForwardSlotState(root)
    result = {"baseline_id": "render_0", "status": "stopped_source_counterevidence", "new_video": None}
    path = state.finish(0, result)
    assert state.finish(0, result) == path
    with pytest.raises(LibraryStopped, match="parent_already_finished"):
        state.begin_call("sfv2_0_reconstruct", request(root, "forbidden_restart"))
    path.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(LibraryStopped, match="completed_result_changed"):
        forward.get_auth(root)


def test_node_rejects_wrong_queued_job_before_dispatch(registered):
    root, _, _, _ = registered
    state = forward.ForwardSlotState(root)
    call, _ = state.begin_call("sfv2_0_reconstruct", request(root, "queuebinding"))
    queued(state, call)
    queue_path = root / "mcp_queue" / (call["id"] + ".request.json")
    bad = _read(queue_path); bad["arguments"]["prompt"] = "tampered"; write_json(queue_path, bad)
    check = node(registered, "g.forwardSlotRequestLimit(root,{job_id:" + json.dumps(call["id"]) + "},env)")
    assert check.returncode != 0 and "actual_request_binding" in check.stderr


def test_render_without_claim_and_quality_pass_without_reviews_rejected(registered):
    root, _, _, auth = registered
    state = forward.ForwardSlotState(root)
    with pytest.raises(LibraryStopped, match="predecessor_not_parsed"):
        state.finish(0, {"model_quality_gate_passed": True})
    Path(auth["allowed_render_directories"][0]).mkdir(parents=True)
    with pytest.raises(LibraryStopped, match="render_without_claim"):
        forward.get_auth(root)
    result = node(registered, "g.loadForwardSlot(root,env)")
    assert result.returncode != 0 and "render_without_claim" in result.stderr


def test_failed_reply_cannot_be_given_new_parsed_pass(registered):
    root, _, _, _ = registered
    state = forward.ForwardSlotState(root)
    call, _ = state.begin_call("sfv2_0_reconstruct", request(root, "failed_then_fabricated"))
    complete(state, call, {"plan": {"test": True}})
    write_json(root / "calls" / call["id"] / "protocol_failure.json", {"error": "Original known format failure"})
    with pytest.raises(LibraryStopped, match="failed_reply_cannot_be_relabelled"):
        state.begin_call("sfv2_0_compare", request(root, "compare_after_failure"))


def test_exhausted_exact_source_lineage_cannot_be_observed_again(prepared, monkeypatch):
    root, source, _, = prepared
    from omni_story.library import goal_feedback_continuation
    blocked = {"kind": "continuous_window", "source_sha256": sha256_file(source), "source_start_s": 1.0, "source_end_s": 3.0}
    monkeypatch.setattr(goal_feedback_continuation, "_exhausted_slice_scopes", lambda state: [
        {"scope": blocked, "original_call": "synthetic_original", "repair_call": "synthetic_repair"}])
    auth = forward.record_authorization(root, "Explicit independently reconstructed task")
    state = forward.ForwardSlotState(root)
    reconstructed, _ = state.begin_call("sfv2_0_reconstruct", request(root, "new_reconstruct"))
    complete(state, reconstructed, {"plan": {"synthetic": True}})
    with pytest.raises(LibraryStopped, match="exhausted_source_observation"):
        state.begin_call("sfv2_0_slice_" + "f" * 16, request(root, "renamed_old_slice", source_sha=sha256_file(source)))
    with pytest.raises(LibraryStopped, match="blocked_exact_source_input"):
        state.assert_source_inputs({"segments": [{"source_id": "src_fixture", "source_in_s": 1.0, "source_out_s": 3.0}]},
            {"sources": [{"source_id": "src_fixture", "path": str(source), "sha256": sha256_file(source)}]})
