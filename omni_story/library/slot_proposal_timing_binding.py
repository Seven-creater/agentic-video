"""One forward assembly may retime a known, unchanged preliminary proposal.

This is neither a third proposal attempt nor an old readability pass. The
program records arithmetic shortfalls; GLM still chooses every final transform.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from .contracts import parse_model_json
from .pipeline import _read
from .slot_finecut_contracts import validate_proposal, validate_slots
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryStopped, json_sha, scope_fingerprint

POLICY = "sf_request_bound_proposal_retiming_v1"
ARTIFACT = POLICY + "_0"
STAGE = "sf_0_proposal_cf0cecff65df19b1"


def _require(ok, reason):
    if not ok:
        raise LibraryStopped("slot_finecut:proposal_retiming_" + reason)


def _context(prompt):
    decoder, values = json.JSONDecoder(), []
    for offset in range(len(prompt)):
        if prompt[offset:offset + 2] != "\n{":
            continue
        try:
            value, _ = decoder.raw_decode(prompt[offset + 1:])
        except ValueError:
            continue
        if isinstance(value, dict) and {"parent", "slot", "parent_output_facts", "watched_windows"} <= value.keys():
            values.append(value)
    _require(len(values) == 1, "one_original_context_required")
    return values[0]


def _artifact_file(data, key):
    entries = data["artifacts"].get(key, [])
    _require(len(entries) == 1, "one_" + key + "_required")
    file = Path(entries[0]["path"])
    value = _read(file)
    _require(json_sha(value) == entries[0]["sha256"], "artifact_changed:" + key)
    return file, value


def _inputs(output, data, grant):
    from .slot_finecut_budget import _parent_point_navigation
    calls = [c for c in data["calls"][grant["baseline_request_count"]:]
             if c["name"] in {STAGE, STAGE + "_repair"}]
    _require(len(calls) == 2 and calls[0]["name"] == STAGE and calls[1]["name"] == STAGE + "_repair"
             and not calls[0].get("repair_of") and calls[1].get("repair_of") == calls[0]["id"]
             and all(c["status"] == "received" for c in calls), "known_sole_repair_required")
    selected = next(p for p in _read(grant["preparation_path"])["parents"] if p["round"] == 0)
    folder = Path(grant["execution_directory"]) / "render_0"
    outline_path = folder / "outline.json"
    outline = validate_slots(_read(outline_path), selected)
    files, requests, bodies, failures = [], [], [], []
    for attempt, call in enumerate(calls):
        call_folder = output / "calls" / call["id"]
        _require(not (call_folder / "parsed.json").exists(), "cannot_relabel_old_reply")
        request, response, failure = (_read(call_folder / name) for name in
                                      ("request.json", "response.json", "protocol_failure.json"))
        _require(json_sha(request) == call["request_sha256"] and json_sha(response) == call["response_sha256"]
                 and failure["attempt"] == attempt, "reply_binding_changed")
        raw = "\n".join(c["text"] for c in response["result"]["content"] if c.get("type") == "text")
        body = parse_model_json(raw)
        _require(parse_model_json(failure["model_text"]) == body, "failure_model_body_changed")
        requests.append(request)
        bodies.append(body)
        failures.append(failure)
        files.extend({"path": str(call_folder / name), "sha256": sha256_file(call_folder / name)}
                     for name in ("request.json", "response.json", "protocol_failure.json"))
    original, repair = requests
    body = bodies[1]
    context = _context(original["arguments"]["prompt"])
    slot = next(s for s in outline["slots"] if s["slot_id"] == body["slot_id"])
    _require(slot == outline["slots"][-1] and context["slot"] == slot
             and context["parent"] == {k: selected[k] for k in context["parent"]}
             and context["response_contract"]["slot_id"] == body["slot_id"]
             and context["response_contract"]["baseline_id"] == selected["baseline_id"]
             and context["response_contract"]["parent_sha256"] == selected["sha256"], "parent_slot_binding_changed")
    navigation_path = folder / "slots" / STAGE.rsplit("_", 1)[1] / "navigation.json"
    navigation = _read(navigation_path)
    _require(context.get("parent_navigation") == navigation
             and context["parent_output_facts"] == navigation["model_report"], "navigation_changed")
    windows_path = output / "watched_windows.json"
    all_windows = {w["window_id"]: w for w in _read(windows_path)}
    windows = context["watched_windows"]
    _require(bool(windows) and len({w["window_id"] for w in windows}) == len(windows)
             and all(w == {k: all_windows[w["window_id"]][k] for k in w} for w in windows), "selected_windows_changed")
    media = Path(original["arguments"]["video_source"]).resolve(strict=True)
    lineage_path = media.parent / "lineage.json"
    lineage = _read(lineage_path)
    scope = original["observation_scope"]
    _require(original["provider"] == repair["provider"] == "official_vision_mcp_in_codex"
             and original["tool"] == repair["tool"] == "analyze_video"
             and Path(repair["arguments"]["video_source"]).resolve() == media
             and sha256_file(media) == original["media_sha256"] == repair["media_sha256"] == lineage["sha256"]
             and scope_fingerprint(scope) == scope_fingerprint(repair["observation_scope"]) == scope_fingerprint(lineage)
             and scope["source_sha256"] == selected["sha256"] and scope["source_start_s"] <= slot["start_s"]
             and scope["source_end_s"] >= slot["end_s"] and lineage["source_offset_s"] == scope["source_start_s"]
             and abs(lineage["media_duration_s"] - (scope["source_end_s"] - scope["source_start_s"])) <= .001
             and lineage["time_mapping"] == "source_time_s = source_offset_s + proxy_time_s", "media_changed")
    _require(failures[0]["error"] == "slot_finecut:operation_not_in_one_compatible_usable_range",
             "original_failure_changed")
    validate_proposal(body, selected, slot, navigation["model_report"], windows,
                      navigation=navigation, enforce_readability=False)
    try:
        validate_proposal(body, selected, slot, navigation["model_report"], windows, navigation=navigation)
    except (LibraryStopped, ValueError) as error:
        _require(str(error) == failures[1]["error"] and str(error).startswith("slot_finecut:declared_readability_shortfall:"),
                 "sole_repair_failure_changed")
    else:
        _require(False, "missing_original_shortfall")
    shortfalls = json.loads(failures[1]["error"].split(":", 2)[2])["mechanical_shortfalls"]
    operations = [{"slot_id": slot["slot_id"], "candidate_id": row["candidate_id"],
                   "operation_index": row["operation_index"]} for row in shortfalls]
    _require(len(operations) == len({tuple(row.values()) for row in operations}) == 3
             and [(r["candidate_id"], r["operation_index"], r["essential_interval_index"]) for r in shortfalls]
                 == [("c1", 3, 0), ("c2", 4, 0), ("c3", 2, 0)], "shortfall_scope_changed")
    point = _parent_point_navigation(output, data, grant, 0)
    _require(point is not None, "prior_point_navigation_required")
    point_path, _ = _artifact_file(data, "sf_parent_point_navigation_v1_0")
    forward_path, forward = _artifact_file(data, "sf_forward_parent_navigation_contract_v1")
    count = forward["baseline_request_count"]
    _require(forward["policy"] == "sf_forward_parent_navigation_contract_v1" and forward["task_id"] == data["task_id"]
             and type(count) is int and grant["baseline_request_count"] <= count <= len(data["calls"])
             and json_sha(data["calls"][:count]) == forward["prefix_calls_sha256"]
             and json_sha(forward["contract"]) == forward["contract_sha256"], "forward_contract_changed")
    stop_path, stop = _artifact_file(data, "sf_parent_protocol_stop_0_point_navigation_resume")
    receipt_path, receipt = _artifact_file(data, "sf_result_0_point_navigation_resume")
    result_path = folder / "result_point_navigation_resume.json"
    result = _read(result_path)
    _require(stop["parent_round"] == 0 and stop["preparation_id"] == grant["preparation_id"]
             and stop["original_call_id"] == calls[0]["id"] and stop["repair_call_id"] == calls[1]["id"]
             and stop["error"] == "model_protocol_repair_exhausted:" + STAGE and stop["files"] == files
             and Path(receipt["result_path"]).resolve() == result_path.resolve()
             and sha256_file(result_path) == receipt["result_sha256"]
             and result.get("stop_receipt") == stop and result["status"] == "stopped_protocol_failure"
             and result["baseline_id"] == selected["baseline_id"] and result["parent_sha256"] == selected["sha256"],
             "stop_or_result_changed")
    files.extend({"path": str(p), "sha256": sha256_file(p)} for p in
                 (Path(grant["preparation_path"]), outline_path, navigation_path, windows_path, lineage_path, media,
                  point_path, forward_path, stop_path, receipt_path, result_path))
    return {"parent_round": 0, "stage": STAGE, "next_stage": "sf_0_assemble", "original_call_id": calls[0]["id"],
            "repair_call_id": calls[1]["id"], "chosen_call_id": calls[1]["id"], "media_sha256": original["media_sha256"],
            "observation_scope": scope, "body": body, "model_body_sha256": json_sha(body), "slot_id": slot["slot_id"],
            "mechanical_shortfalls": shortfalls, "retiming_operations": operations, "protected_files": files}


def read_validate(output, data, grant):
    entries = data["artifacts"].get(ARTIFACT, [])
    if not entries:
        return None
    _require(len(entries) == 1, "one_record_required")
    policy = _read(entries[0]["path"])
    _require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == POLICY
             and policy["task_id"] == data["task_id"] and policy["preparation_id"] == grant["preparation_id"]
             and policy["original_contract_status"] == "failed" and policy["retroactive_proposal_pass"] is False
             and policy["old_parsed_created"] is False and policy["semantic_truth_established"] is False
             and policy["program_transform_changes"] == [] and policy["allowed_model_changes"] == ["speed", "freeze_tail_s"],
             "policy_changed")
    count = policy["baseline_request_count"]
    _require(type(count) is int and grant["baseline_request_count"] <= count <= len(data["calls"])
             and json_sha(data["calls"][:count]) == policy["prefix_calls_sha256"]
             and all(c["status"] == "received" for c in data["calls"][grant["baseline_request_count"]:count]),
             "settled_prefix_changed")
    bound = _inputs(Path(output), data, grant)
    _require(all(policy.get(k) == value for k, value in bound.items()), "body_or_evidence_changed")
    later = [c for c in data["calls"][count:] if c["name"].startswith("sf_0_") or c["name"].startswith("semantic_") and "_20_" in c["name"]]
    _require(not later or later[0]["name"] == policy["next_stage"], "first_resume_stage_changed")
    return policy


def record_request_bound_proposal_retiming(state, parent=0):
    from .slot_finecut_budget import STATE_MUTEX
    with STATE_MUTEX:
        _require(parent == 0, "only_parent_zero")
        grant = state.assert_protected()
        existing = read_validate(state.output, state.data, grant)
        if existing:
            return existing
        _require(all(c["status"] == "received" for c in state.data["calls"][grant["baseline_request_count"]:]),
                 "new_or_pending_outcome_unknown")
        _require(not any(c["name"].startswith("sf_0_assemble") for c in state.data["calls"][grant["baseline_request_count"]:]),
                 "assembly_already_submitted")
        bound = _inputs(state.output, state.data, grant)
        policy = {"policy": POLICY, "task_id": state.data["task_id"], "preparation_id": grant["preparation_id"],
            "baseline_request_count": len(state.data["calls"]), "prefix_calls_sha256": json_sha(state.data["calls"]),
            "original_contract_status": "failed", "retroactive_proposal_pass": False, "old_parsed_created": False,
            "semantic_truth_established": False, "program_transform_changes": [],
            "allowed_model_changes": ["speed", "freeze_tail_s"], "protocol_limitations": [
                "The whole sole repair remains unchanged preliminary model evidence, with its original readability failure.",
                "Only the listed operations may receive model-selected speed/hold changes in the first unsubmitted assembly.",
                "Source geometry, order, roles, evidence, essential intervals and model readability minima remain bound.",
                "No third proposal or same-lineage observation is permitted; independent source and output gates still apply."], **bound}
        state.set_artifact(ARTIFACT, policy)
        state.assert_protected()
        return policy


def body_for_stage(output, data, grant, stage, media_sha256=None):
    policy = read_validate(output, data, grant)
    if policy is None or stage != policy["stage"]:
        return None
    _require(media_sha256 is None or media_sha256 == policy["media_sha256"], "current_media_changed")
    return deepcopy(policy["body"])


def check_input(policy, stage, request):
    if policy is None:
        return
    _require(stage not in {policy["stage"], policy["stage"] + "_repair"}, "no_third_proposal")
    if "_facts_" in stage or stage.startswith("semantic_slice_"):
        _require(request.get("media_sha256") != policy["media_sha256"]
                 and scope_fingerprint(request.get("observation_scope")) != scope_fingerprint(policy["observation_scope"]),
                 "no_third_observation")
