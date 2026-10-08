"""Synthetic forward point navigation mirror; no real media, run or MCP."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import slot_finecut_budget as budget
from omni_story.library.slot_finecut_contracts import neutral_facts_prompt
from omni_story.library.state import json_sha, write_json


pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Guard mirror requires Node")


def fixture(tmp_path, parent=3):
    root = tmp_path / "synthetic"
    root.mkdir()
    start, end = (16, 27) if parent == 3 else (29, 41)
    selected = {"round": parent, "baseline_id": f"render_{parent}", "sha256": "c" * 64,
                "duration_s": end}
    fields = {k: "Synthetic slot description" for k in (
        "intended_takeaway", "entry_state", "exit_state", "link_to_previous", "link_to_next")}
    slot = {"slot_id": "s3", "start_s": start, "end_s": end, **fields}
    folder = root / "execution" / f"render_{parent}"
    write_json(folder / "outline.json", {"baseline_id": selected["baseline_id"], "parent_sha256": selected["sha256"],
        "slots": [{"slot_id": "previous", "start_s": 0, "end_s": start, **fields}, slot],
        "limitations": [], "uncertainties": []})
    preparation = root / "preparation.json"
    write_json(preparation, {"parents": [selected]})
    media = root / "proxy" / "window.mp4"
    media.parent.mkdir()
    media.write_bytes(b"Synthetic non-video media for hash binding only")
    media_sha = budget.sha256_file(media)
    scope = {"kind": "continuous_window", "source_sha256": selected["sha256"], "source_start_s": start, "source_end_s": end}
    write_json(media.parent / "lineage.json", {**scope, "sha256": media_sha, "source_offset_s": start,
        "media_duration_s": end - start, "time_mapping": "source_time_s = source_offset_s + proxy_time_s"})
    original = {"baseline_id": selected["baseline_id"], "parent_sha256": selected["sha256"], "slot_id": "s3",
        "slot_start_s": start, "slot_end_s": end, "time_domain": "slot_local_output", "evidence": [
            {"evidence_id": "e1", "start_s": 0, "end_s": 1.0, "kind": "state", "basis": "visual",
             "observed_fact": "Synthetic independently reported state."},
            {"evidence_id": "e2", "start_s": 2, "end_s": 2, "kind": "action", "basis": "visible_text",
             "observed_fact": "Synthetic point cue; no duration is established."},
            {"evidence_id": "e3", "start_s": 9, "end_s": 10, "kind": "inference", "basis": "inference",
             "observed_fact": "Synthetic fallible interpretation."}], "limitations": [], "uncertainties": []}
    repair = deepcopy(original)
    if parent == 3:
        repair.update(slot_start_s=0, slot_end_s=end - start)
    else:
        repair["evidence"][1]["end_s"] = 3
        repair["evidence"][2]["end_s"] = 9
        del repair["limitations"], repair["uncertainties"]
    stage = budget.POINT_NAVIGATION_STAGES[parent]
    calls, protected = [], []
    for attempt, body in enumerate((original, repair)):
        ident = f"synthetic_{parent}_{attempt}"
        request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video", "media_sha256": media_sha,
            "observation_scope": scope, "arguments": {"video_source": str(media),
            "prompt": neutral_facts_prompt(selected, slot) if attempt == 0 else "Synthetic sole saved repair"}}
        response = {"result": {"content": [{"type": "text", "text": json.dumps(body)}]}}
        call = {"id": ident, "name": stage + ("_repair" if attempt else ""), "status": "received",
            "request_sha256": json_sha(request), "response_sha256": json_sha(response),
            "repair_of": calls[0]["id"] if attempt else None}
        for name, value in (("request.json", request), ("response.json", response), ("protocol_failure.json", {
            "attempt": attempt, "error": "slot_finecut:fact_time_binding_changed" if parent == 3 and attempt else
                "slot_finecut/fact:outside_bound_interval"})):
            file = root / "calls" / ident / name
            write_json(file, value)
            protected.append({"path": str(file), "sha256": budget.sha256_file(file)})
        calls.append(call)
    stop = {"policy": budget.PARENT_STOP_POLICY, "preparation_id": "synthetic_preparation", "parent_round": parent,
        "independent_parent_round": 0 if parent == 3 else 3, "request_count": 2,
        "original_call_id": calls[0]["id"], "repair_call_id": calls[1]["id"],
        "error": "model_protocol_repair_exhausted:" + stage, "files": protected}
    result_path = folder / "result.json"
    failed_result = {"status": "stopped_protocol_failure", "baseline_id": selected["baseline_id"],
                     "parent_sha256": selected["sha256"], "stop_receipt": stop}
    write_json(result_path, failed_result)
    data = {"task_id": "synthetic_task", "calls": calls, "request_count": 2, "max_requests": 80,
            "input_lock": {}, "artifacts": {}}
    record = {"baseline_request_count": 0, "preparation_path": str(preparation),
              "execution_directory": str(folder.parent), "preparation_id": "synthetic_preparation"}
    state = SimpleNamespace(output=root, data=data, authorization=record, assert_protected=lambda: record)

    def artifact(name, value):
        file = root / (name + ".json")
        write_json(file, value)
        data["artifacts"][name] = [{"path": str(file), "sha256": json_sha(value)}]

    state.set_artifact = artifact
    artifact(f"sf_parent_protocol_stop_{parent}", stop)
    if parent == 0:
        artifact("sf_parent_protocol_stop_0_metadata_bound_resume", stop)
        metadata_result = folder / "result_metadata_bound_resume.json"
        write_json(metadata_result, {**failed_result, "namespace": "metadata_bound_resume"})
        artifact("sf_result_0_metadata_bound_resume", {
            "result_path": str(metadata_result), "result_sha256": budget.sha256_file(metadata_result)})
        write_json(folder.parent / "parallel_stop_synthetic.json", {"request_count": 153,
            "errors": [{"baseline_id": "render_0", "error": "slot_finecut:parent_stop_error_changed"}]})
    artifact(f"sf_result_{parent}", {"result_path": str(result_path), "result_sha256": budget.sha256_file(result_path)})
    policy = budget.record_parent_point_navigation(state, parent=parent)
    write_json(root / "library_state.json", data)
    return state, record, policy, original, repair


def invoke(state, record, *, function="parentPointNavigation", options=None):
    write_json(state.output / "library_state.json", state.data)
    module = Path("omni_story/library/mcp_slot_finecut_guard.mjs").resolve().as_uri()
    code = """const fs=await import('node:fs');const guard=await import(process.argv[1]);
const state=JSON.parse(fs.readFileSync(process.argv[2]+'/library_state.json','utf8'));
try {const result=guard[process.argv[4]](process.argv[2],state,JSON.parse(process.argv[3]),JSON.parse(process.argv[5]));
process.stdout.write(JSON.stringify(result));}catch(e){process.stderr.write(String(e.message));process.exitCode=3;}"""
    return subprocess.run(["node", "--input-type=module", "-e", code, module, str(state.output), json.dumps(record),
        function, json.dumps(options if options is not None else 3)], text=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("parent", [0, 3])
def test_mirror_preserves_chosen_full_body_and_disagreements(tmp_path, parent):
    state, record, policy, original, repair = fixture(tmp_path, parent)
    before = {p: p.read_bytes() for p in state.output.rglob("*") if p.is_file()}
    checked = invoke(state, record, options=parent)
    assert checked.returncode == 0, checked.stderr
    result = json.loads(checked.stdout)
    assert result["envelope"]["model_report"] == (repair if parent == 3 else original)
    point = next(e for e in result["envelope"]["temporal_support"] if e["instant_only"])
    assert point["start_s"] == point["end_s"]
    assert point["duration_unknown"] and point["cannot_prove_completed_action"] and point["exposure_proof"] is False
    assert result["retroactive_fact_pass"] is False
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not list(state.output.rglob("parsed.json"))
    if parent == 0:
        missing = next(e for e in result["envelope"]["unresolved_model_disagreements"] if e["field"] == "limitations")
        assert missing["repair_present"] is False and missing["repair_value"] is None
        assert policy["chosen_call_id"] == state.data["calls"][0]["id"]


@pytest.mark.parametrize("tamper", ["body", "point_end", "exposure", "disagreement", "binding", "chosen", "failure", "parsed"])
def test_point_mirror_rejects_tampering_even_after_artifact_rehash(tmp_path, tamper):
    state, record, policy, _, _ = fixture(tmp_path)
    if tamper == "body":
        policy["envelope"]["model_report"]["evidence"][1]["observed_fact"] = "Rewritten creative event"
    elif tamper == "point_end":
        policy["envelope"]["temporal_support"][1]["end_s"] = 2.1
    elif tamper == "exposure":
        policy["envelope"]["temporal_support"][1]["exposure_proof"] = True
    elif tamper == "disagreement":
        policy["envelope"]["unresolved_model_disagreements"] = []
    elif tamper == "binding":
        policy["envelope"]["request_binding"]["parent_slot_start_s"] = 0
    elif tamper == "chosen":
        policy["chosen_call_id"] = state.data["calls"][0]["id"]
    elif tamper == "failure":
        path = state.output / "calls" / state.data["calls"][0]["id"] / "protocol_failure.json"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        write_json(state.output / "calls" / state.data["calls"][0]["id"] / "parsed.json", {})
    state.set_artifact("sf_parent_point_navigation_v1_3", policy)
    checked = invoke(state, record)
    assert checked.returncode != 0 and "library_mcp_slot_point_navigation_" in checked.stderr


@pytest.mark.parametrize("parent", [0, 3])
def test_new_failed_result_cannot_carry_an_older_stop_even_after_hash_refresh(tmp_path, parent):
    state, record, policy, _, _ = fixture(tmp_path, parent)
    result_key = "sf_result_0_metadata_bound_resume" if parent == 0 else "sf_result_3"
    receipt_path = Path(state.data["artifacts"][result_key][0]["path"])
    receipt = json.loads(receipt_path.read_text())
    result_path = Path(receipt["result_path"])
    result = json.loads(result_path.read_text())
    result["stop_receipt"]["original_call_id"] = "older_failed_stage"
    write_json(result_path, result)
    receipt["result_sha256"] = budget.sha256_file(result_path)
    state.set_artifact(result_key, receipt)
    for row in policy["protected_files"]:
        if Path(row["path"]) in {result_path, receipt_path}:
            row["sha256"] = budget.sha256_file(row["path"])
    state.set_artifact(f"sf_parent_point_navigation_v1_{parent}", policy)
    checked = invoke(state, record, options=parent)
    assert checked.returncode != 0 and "point_navigation_stop" in checked.stderr


def append_failure_pair(state, parent, stage):
    original_request = json.loads((state.output / "calls" / state.data["calls"][0]["id"] / "request.json").read_text())
    files = []
    for attempt in (0, 1):
        ident = f"later_{parent}_{attempt}"
        request = deepcopy(original_request)
        request["arguments"]["prompt"] = f"Synthetic later protocol attempt {attempt}"
        response = {"result": {"content": [{"type": "text", "text": "{}"}]}}
        state.data["calls"].append({"id": ident, "name": stage + ("_repair" if attempt else ""), "status": "received",
            "request_sha256": json_sha(request), "response_sha256": json_sha(response),
            "repair_of": f"later_{parent}_0" if attempt else None})
        for name, value in (("request.json", request), ("response.json", response),
                            ("protocol_failure.json", {"attempt": attempt, "error": "Synthetic known contract failure"})):
            file = state.output / "calls" / ident / name
            write_json(file, value)
            files.append({"path": str(file), "sha256": budget.sha256_file(file)})
    state.data["request_count"] = len(state.data["calls"])
    return files


@pytest.mark.parametrize("parent", [0, 3])
def test_terminal_point_receipt_blocks_further_parent_stages(tmp_path, parent):
    state, record, policy, _, _ = fixture(tmp_path, parent)
    files = append_failure_pair(state, parent, policy["next_stage"])
    receipt = {"policy": budget.PARENT_STOP_POLICY, "preparation_id": record["preparation_id"], "parent_round": parent,
        "independent_parent_round": 0 if parent == 3 else 3, "request_count": len(state.data["calls"]),
        "original_call_id": f"later_{parent}_0", "repair_call_id": f"later_{parent}_1", "files": files,
        "error": "model_protocol_repair_exhausted:" + policy["next_stage"]}
    state.set_artifact(f"sf_parent_protocol_stop_{parent}_point_navigation_resume", receipt)
    state.data["calls"].append({"id": "forbidden", "name": f"sf_{parent}_assemble", "status": "submitted"})
    checked = invoke(state, record, function="parentProtocolStops", options={"pointNavigation": {str(parent): policy}})
    assert checked.returncode != 0 and "stopped_parent_cannot_repeat" in checked.stderr


@pytest.mark.parametrize("parent", [0, 3])
def test_first_forward_call_must_be_same_slot_proposal(tmp_path, parent):
    state, record, _, _, _ = fixture(tmp_path, parent)
    state.data["calls"].append({"id": "wrong_next", "name": f"sf_{parent}_assemble", "status": "submitted"})
    checked = invoke(state, record, options=parent)
    assert checked.returncode != 0 and "resume_stage" in checked.stderr


def test_matching_parent_proposal_is_allowed_after_known_stop(tmp_path):
    state, record, policy, _, _ = fixture(tmp_path)
    state.data["calls"].append({"id": "next", "name": policy["next_stage"], "status": "submitted"})
    checked = invoke(state, record, function="parentProtocolStops", options={"pointNavigation": {"3": policy}})
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize("replay", ["media", "scope"])
def test_full_guard_blocks_third_point_input_observation(tmp_path, replay):
    state, record, policy, _, _ = fixture(tmp_path)
    append_failure_pair(state, 3, policy["next_stage"])
    # Both known proposal replies remain received; the forward point namespace
    # has not been stopped in this synthetic guard-only fixture.
    baseline_path = state.output / "baseline.json"
    write_json(baseline_path, [])
    methods, knowledge = state.output / "methods.json", state.output / "knowledge.md"
    write_json(methods, {"methods": []})
    knowledge.write_text("Synthetic knowledge", encoding="utf-8")
    grant = {**record, "policy": budget.POLICY, "task_id": state.data["task_id"], "original_output": str(state.output),
        "request_limit_policy": budget.REQUEST_LIMIT_POLICY, "base_request_limit": 80, "input_lock_sha256": json_sha({}),
        "baseline_calls_path": str(baseline_path), "baseline_calls_sha256": budget.sha256_file(baseline_path),
        "prefix_calls_sha256": json_sha([]), "allowed_stage_pattern": budget.ALLOWED_STAGE_PATTERN,
        "preparation_sha256": budget.sha256_file(record["preparation_path"]),
        "reference_methods_path": str(methods), "reference_methods_sha256": budget.sha256_file(methods),
        "knowledge_path": str(knowledge), "knowledge_sha256": budget.sha256_file(knowledge),
        "protected_files": [], "admitted_unknown_call_ids": [], "exhausted_source_inputs": []}
    state.set_artifact(budget.POLICY, grant)
    request = json.loads((state.output / "calls" / state.data["calls"][0]["id"] / "request.json").read_text())
    if replay == "media":
        request["observation_scope"]["source_start_s"], request["observation_scope"]["source_end_s"] = 50, 51
    else:
        request["media_sha256"] = "d" * 64
    call = {"id": "current", "name": "semantic_slice_21_" + "f" * 16, "status": "submitted", "repair_of": None,
        "request_sha256": json_sha(request)}
    state.data["calls"].append(call)
    state.data["request_count"] = len(state.data["calls"])
    write_json(state.output / "calls" / "current" / "request.json", request)
    write_json(state.output / "library_state.json", state.data)
    auth = Path(state.data["artifacts"][budget.POLICY][0]["path"])
    env = {"OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE": str(auth), "OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256": budget.sha256_file(auth)}
    module = Path("omni_story/library/mcp_slot_finecut_guard.mjs").resolve().as_uri()
    code = """const {slotRequestLimit}=await import(process.argv[1]);Object.assign(process.env,JSON.parse(process.argv[3]));
try{slotRequestLimit(process.argv[2],{job_id:'current'});}catch(e){process.stderr.write(String(e.message));process.exitCode=3;}"""
    checked = subprocess.run(["node", "--input-type=module", "-e", code, module, str(state.output), json.dumps(env)],
        text=True, capture_output=True, timeout=30)
    assert checked.returncode != 0 and "point_navigation_no_third_observation" in checked.stderr
