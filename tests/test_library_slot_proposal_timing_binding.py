"""Synthetic known-failure recovery: no paid requests, real state or renders."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import slot_finecut_budget as budget
from omni_story.library import slot_proposal_timing_binding as binding
from omni_story.library.slot_finecut_contracts import parent_navigation, validate_proposal
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_slot_finecut_point_binding import fixture as point_fixture


def fixture(tmp_path):
    state, grant, _, _, _ = point_fixture(tmp_path, 0)
    metadata_stop = state.data["artifacts"]["sf_parent_protocol_stop_0_metadata_bound_resume"][0]["path"]
    state.set_artifact("sf_parent_protocol_stop_0", json.loads(Path(metadata_stop).read_text()))
    folder = Path(grant["execution_directory"]) / "render_0"
    selected = json.loads(Path(grant["preparation_path"]).read_text())["parents"][0]
    selected["duration_s"] = 60
    selected["provenance"] = [{"output_in_s": 41, "output_out_s": 60, "source_id": "synthetic_source",
        "source_sha256": "d" * 64, "window_id": "synthetic_window", "role_ids": ["synthetic_role"]}]
    write_json(grant["preparation_path"], {"parents": [selected]})
    outline = json.loads((folder / "outline.json").read_text())
    slot = {**outline["slots"][-1], "slot_id": "s5", "start_s": 41, "end_s": 60}
    outline["slots"].append(slot)
    write_json(folder / "outline.json", outline)
    # Record the point policy after constructing the complete synthetic parent.
    budget.record_parent_point_navigation(state, 0)
    state.data["calls"].append({"id": "synthetic_point_navigation_next_stage",
        "name": budget.POINT_NAVIGATION_STAGES[0].replace("_facts_", "_proposal_"), "status": "received"})
    facts = {"baseline_id": "render_0", "parent_sha256": selected["sha256"], "slot_id": "s5", "slot_start_s": 41,
        "slot_end_s": 60, "time_domain": "slot_local_output", "evidence": [{"evidence_id": "e1", "start_s": 1,
        "end_s": 3, "kind": "state", "basis": "visual", "observed_fact": "Synthetic fallible model state."}],
        "limitations": [], "uncertainties": []}
    navigation = parent_navigation(facts, selected, slot)
    navigation_path = folder / "slots" / binding.STAGE.rsplit("_", 1)[1] / "navigation.json"
    write_json(navigation_path, navigation)
    window = {"window_id": "synthetic_window", "source_id": "synthetic_source", "source_sha256": "d" * 64,
        "source_start_s": 100, "source_end_s": 140, "observation": {"usable_ranges": [
        {"local_in_s": 0, "local_out_s": 30, "role_ids": ["synthetic_role"]}]}}
    write_json(state.output / "watched_windows.json", [window])
    context = {"parent": selected, "slot": slot, "parent_output_facts": facts, "parent_navigation": navigation,
        "watched_windows": [window], "response_contract": {"baseline_id": "render_0", "parent_sha256": selected["sha256"],
        "slot_id": "s5", "candidates": []}}
    def operation(a=100, b=104, minimum=2):
        return {"parent_segment_index": 0, "source_in_s": a, "source_out_s": b, "speed": 1, "freeze_tail_s": 0,
            "evidence_ids": ["e1"], "reason": "Synthetic model operation.", "essential_intervals": [{
            "source_start_s": a, "source_end_s": b, "min_readable_s": minimum,
            "information": "Synthetic essential state.", "evidence_ids": ["e1"], "continues_in_tail_frame": True}]}
    body = {"baseline_id": "render_0", "parent_sha256": selected["sha256"], "slot_id": "s5", "candidates": [
        {"candidate_id": f"c{i+1}", "meaning_status": "preserved", "rationale": "Synthetic model rationale.",
         "limitations": [], "operations": [operation() for _ in range(n)] + [operation(120, 122.37, 3.0)]}
        for i, n in enumerate([3, 4, 2])]}
    original_body = deepcopy(body)
    original_body["candidates"][0]["operations"][-1]["source_out_s"] = 131
    original_body["candidates"][0]["operations"][-1]["essential_intervals"][0]["source_end_s"] = 131
    media = state.output / "new_proxy/window.mp4"
    media.parent.mkdir()
    media.write_bytes(b"Synthetic known proposal proxy byte fixture")
    media_sha = budget.sha256_file(media)
    scope = {"kind": "continuous_window", "source_sha256": selected["sha256"], "source_start_s": 40, "source_end_s": 60}
    write_json(media.parent / "lineage.json", {**scope, "sha256": media_sha, "source_offset_s": 40,
        "media_duration_s": 20, "time_mapping": "source_time_s = source_offset_s + proxy_time_s"})
    new_calls, files = [], []
    for attempt, value in enumerate((original_body, body)):
        ident = f"synthetic_proposal_{attempt}"
        request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video", "media_sha256": media_sha,
            "observation_scope": scope, "arguments": {"video_source": str(media), "prompt": "Synthetic instruction\n" +
            json.dumps(context) + "\n" + json.dumps({"mechanical_diagnostics_only": []}) if attempt == 0 else "Saved sole repair"}}
        response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
        try:
            validate_proposal(value, selected, slot, facts, [window], navigation=navigation)
        except ValueError as error:
            failure = {"attempt": attempt, "error": str(error), "model_text": json.dumps(value)}
        else:
            raise AssertionError("Synthetic historical failure must fail")
        call = {"id": ident, "name": binding.STAGE + ("_repair" if attempt else ""), "status": "received",
            "request_sha256": json_sha(request), "response_sha256": json_sha(response),
            "repair_of": new_calls[0]["id"] if attempt else None}
        for name, value in (("request.json", request), ("response.json", response), ("protocol_failure.json", failure)):
            file = state.output / "calls" / ident / name
            write_json(file, value)
            files.append({"path": str(file), "sha256": budget.sha256_file(file)})
        new_calls.append(call)
    state.data["calls"].extend(new_calls)
    state.data["request_count"] = len(state.data["calls"])
    stop = {"policy": budget.PARENT_STOP_POLICY, "preparation_id": grant["preparation_id"], "parent_round": 0,
        "independent_parent_round": 3, "request_count": len(state.data["calls"]), "original_call_id": new_calls[0]["id"],
        "repair_call_id": new_calls[1]["id"], "error": "model_protocol_repair_exhausted:" + binding.STAGE, "files": files}
    state.set_artifact("sf_parent_protocol_stop_0_point_navigation_resume", stop)
    result_path = folder / "result_point_navigation_resume.json"
    write_json(result_path, {"status": "stopped_protocol_failure", "stop_receipt": stop,
        "baseline_id": selected["baseline_id"], "parent_sha256": selected["sha256"]})
    state.set_artifact("sf_result_0_point_navigation_resume", {"result_path": str(result_path),
        "result_sha256": budget.sha256_file(result_path)})
    contract = {"version": "synthetic_forward_navigation", "preserve_old_contracts": True}
    state.set_artifact("sf_forward_parent_navigation_contract_v1", {"policy": "sf_forward_parent_navigation_contract_v1",
        "task_id": state.data["task_id"], "baseline_request_count": len(state.data["calls"]),
        "prefix_calls_sha256": json_sha(state.data["calls"]), "contract": contract, "contract_sha256": json_sha(contract)})
    return state, grant, body


def invoke(state, grant, *, stops=False, options=None):
    write_json(state.output / "library_state.json", state.data)
    module = Path("omni_story/library/" + ("mcp_slot_finecut_guard.mjs" if stops else
                  "mcp_slot_proposal_timing_binding.mjs")).resolve().as_uri()
    code = """const fs=await import('node:fs');const m=await import(process.argv[1]);
try {const state=JSON.parse(fs.readFileSync(process.argv[2]+'/library_state.json','utf8'));
const grant=JSON.parse(process.argv[3]);const result=process.argv[4]==='stops' ?
 m.parentProtocolStops(process.argv[2],state,grant,JSON.parse(process.argv[5])) :
 m.readValidateProposalRetiming(process.argv[2],state,grant);process.stdout.write(JSON.stringify(result));}
catch(e){process.stderr.write(e.message);process.exitCode=3;}"""
    return subprocess.run(["node", "--input-type=module", "-e", code, module, str(state.output), json.dumps(grant),
                           "stops" if stops else "record", json.dumps(options or {})],
                          capture_output=True, text=True, timeout=30)


def test_preserves_entire_model_body_and_failure_bytes(tmp_path):
    state, grant, body = fixture(tmp_path)
    before = {p: p.read_bytes() for p in state.output.rglob("*") if p.is_file()}
    record = binding.record_request_bound_proposal_retiming(state)
    assert record["body"] == body and record["retroactive_proposal_pass"] is False
    assert record["program_transform_changes"] == [] and len(record["retiming_operations"]) == 3
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not list(state.output.rglob("parsed.json"))
    assert binding.body_for_stage(state.output, state.data, grant, binding.STAGE, record["media_sha256"]) == body
    assert binding.record_request_bound_proposal_retiming(state) == record
    with pytest.raises(LibraryStopped, match="current_media_changed"):
        binding.body_for_stage(state.output, state.data, grant, binding.STAGE, "e" * 64)
    with pytest.raises(LibraryStopped, match="no_third_proposal"):
        binding.check_input(record, binding.STAGE, {})
    with pytest.raises(LibraryStopped, match="no_third_observation"):
        binding.check_input(record, "sf_0_facts_ffffffffffffffff", {"media_sha256": record["media_sha256"],
                            "observation_scope": record["observation_scope"]})


@pytest.mark.parametrize("tamper", ["minimum", "speed", "hold", "geometry", "whitelist", "shortfalls", "failure", "parsed", "first_stage"])
def test_rejects_tampering_after_policy_hash_refresh(tmp_path, tamper):
    state, grant, _ = fixture(tmp_path)
    record = binding.record_request_bound_proposal_retiming(state)
    op = record["body"]["candidates"][0]["operations"][3]
    if tamper == "minimum":
        op["essential_intervals"][0]["min_readable_s"] = 2
    elif tamper == "speed":
        op["speed"] = .5
    elif tamper == "hold":
        op["freeze_tail_s"] = 1
    elif tamper == "geometry":
        op["source_out_s"] = 125
    elif tamper == "whitelist":
        record["retiming_operations"][0]["operation_index"] = 0
    elif tamper == "shortfalls":
        record["mechanical_shortfalls"][0]["model_min_readable_s"] = 2
    elif tamper == "failure":
        file = state.output / "calls/synthetic_proposal_1/protocol_failure.json"
        file.write_bytes(file.read_bytes() + b" ")
    elif tamper == "parsed":
        write_json(state.output / "calls/synthetic_proposal_1/parsed.json", {})
    else:
        state.data["calls"].append({"name": "sf_0_proposal_ffffffffffffffff", "status": "received"})
    state.set_artifact(binding.ARTIFACT, record)
    with pytest.raises((LibraryStopped, ValueError)):
        binding.read_validate(state.output, state.data, grant)
    if shutil.which("node"):
        checked = invoke(state, grant)
        assert checked.returncode != 0, checked.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="Node guard mirror requires Node")
def test_node_mirror_accepts_same_unchanged_model_body(tmp_path):
    state, grant, body = fixture(tmp_path)
    binding.record_request_bound_proposal_retiming(state)
    checked = invoke(state, grant)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["body"] == body


def test_pending_new_call_blocks_registration(tmp_path):
    state, _, _ = fixture(tmp_path)
    state.data["calls"].append({"name": "sf_3_review", "status": "submitted"})
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        binding.record_request_bound_proposal_retiming(state)
    assert binding.ARTIFACT not in state.data["artifacts"]


def test_first_assembly_failure_uses_terminal_namespace_and_cannot_unfreeze(tmp_path):
    state, grant, _ = fixture(tmp_path)
    policy = binding.record_request_bound_proposal_retiming(state)
    request = json.loads((state.output / "calls/synthetic_proposal_0/request.json").read_text())
    for attempt in (0, 1):
        ident = f"synthetic_assembly_failed_{attempt}"
        response = {"result": {"content": [{"type": "text", "text": "{}"}]}}
        state.data["calls"].append({"id": ident, "name": "sf_0_assemble" + ("_repair" if attempt else ""),
            "status": "received", "request_sha256": json_sha(request), "response_sha256": json_sha(response),
            "repair_of": "synthetic_assembly_failed_0" if attempt else None})
        for name, value in (("request.json", request), ("response.json", response),
                            ("protocol_failure.json", {"attempt": attempt, "error": "synthetic assembly failure"})):
            write_json(state.output / "calls" / ident / name, value)
    stop = budget.SlotFinecutState.freeze_parent_protocol_failure(state, 0, "model_protocol_repair_exhausted:sf_0_assemble")
    assert state.data["artifacts"].get("sf_parent_protocol_stop_0_timing_bound_resume")
    assert budget._parent_stops(state.output, state.data, grant)[0] == stop
    point = budget._parent_point_navigation(state.output, state.data, grant, 0)
    options = {"boundProposalRetiming": policy, "pointNavigation": {"0": point}}
    if shutil.which("node"):
        checked = invoke(state, grant, stops=True, options=options)
        assert checked.returncode == 0, checked.stderr
    state.data["calls"].append({"id": "forbidden_after_stop", "name": "sf_0_blind", "status": "submitted"})
    with pytest.raises(LibraryStopped, match="stopped_parent_cannot_repeat"):
        budget._parent_stops(state.output, state.data, grant)
    if shutil.which("node"):
        checked = invoke(state, grant, stops=True, options=options)
        assert checked.returncode != 0 and "stopped_parent_cannot_repeat" in checked.stderr
