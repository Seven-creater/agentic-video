"""Future, explicit same-run authorization for two parent slot fine cuts.

Importing or preparing this adapter grants nothing and never starts the Goal.
The historical quota and uncertain calls stay unchanged in the original ledger.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
from pathlib import Path
import re
from threading import RLock
from types import SimpleNamespace

from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .pipeline import _read
from .state import LibraryState, LibraryStopped, _timestamp, file_lock, json_sha, scope_fingerprint, write_json

POLICY = ARTIFACT = "slot_finecut_comparison_v1"
REQUEST_LIMIT_POLICY = "progress_guard_no_numeric_request_cap_v1"
ALLOWED_STAGE_PATTERN = (r"^(?:sf_(?:0|3)_(?:outline|facts_[a-f0-9]{16}|proposal_[a-f0-9]{16}|"
                         r"assemble|blind|economy|review)|semantic_(?:slice|claims)_(?:20|21)_[a-f0-9]{16})(?:_repair)?$")
ADMITTED_UNKNOWN_IDS = {"glm_004_coarse_978d5360_01", "glm_131_active_10_draft"}
PARENT_STOP_POLICY = "slot_finecut_independent_parent_after_known_protocol_stop_v1"
PARALLEL_POLICY = "sf_parallel_execution_v1"
REPLACEMENT_OUTLINE = "sf_0_outline_v2"
REQUEST_BOUND_FACT_POLICY = "sf_request_bound_parent_facts_v1"
PARENT_FACT_TAXONOMY = "parent_fact_inference_nonvisual_v2"
POINT_NAVIGATION_POLICY = "sf_parent_point_navigation_v1"
POINT_NAVIGATION_STAGE = "sf_3_facts_cbdaef3a7197c66f"
POINT_NAVIGATION_STAGES = {0: "sf_0_facts_d106649756615136", 3: POINT_NAVIGATION_STAGE}
REQUEST_BOUND_ASSEMBLY_POLICY = "sf_request_bound_assembly_v1_3"
REQUEST_BOUND_PROPOSAL_RETIMING_POLICY = "sf_request_bound_proposal_retiming_v1_0"
SOURCE_FEEDBACK_POLICY = "sf_source_feedback_replan_v1_3"
SOURCE_FEEDBACK_STAGE = "sf_3_source_feedback_replan_v1"
INDEPENDENT_RECOVERY_POLICY = "sf_independent_slot_recovery_v1"
LOCAL_REPLAN_STAGE = "sf_3_local_source_replan_v2"
STATE_MUTEX = RLock()


def _serialized(method):
    @wraps(method)
    def locked(*args, **kwargs):
        with STATE_MUTEX:
            return method(*args, **kwargs)
    return locked


def _prompt_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) else json_sha(value)


def _parallel_execution(output, data, record):
    entries = data["artifacts"].get(PARALLEL_POLICY, [])
    if not entries:
        return None
    _require(len(entries) == 1, "one_parallel_authorization_required")
    policy = _read(entries[0]["path"])
    _require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == PARALLEL_POLICY
             and policy["task_id"] == data["task_id"] and policy["preparation_id"] == record["preparation_id"]
             and policy["authorization_record_sha256"] == data["artifacts"][ARTIFACT][0]["sha256"]
             and policy["max_concurrent_parents"] == 2 and policy["parent_rounds"] == [0, 3]
             and policy["replacement_outline_stage"] == REPLACEMENT_OUTLINE
             and bool(policy["user_instruction"].strip())
             and _prompt_digest(policy["prompt_policy"]) == policy["prompt_policy_sha256"], "parallel_authorization_changed")
    count = policy["baseline_request_count"]
    _require(type(count) is int and record["baseline_request_count"] + 2 <= count <= len(data["calls"])
             and json_sha(data["calls"][:count]) == policy["prefix_calls_sha256"], "parallel_baseline_changed")
    calls = [c for c in data["calls"][record["baseline_request_count"]:count] if _stage(c["name"])[0] == 0]
    _require(len(calls) == 2 and calls[0]["name"] == "sf_0_outline" and calls[1]["name"] == "sf_0_outline_repair"
             and calls[1].get("repair_of") == calls[0]["id"] and not calls[0].get("repair_of")
             and all(c["status"] == "received" for c in calls)
             and policy["failed_call_ids"] == [c["id"] for c in calls], "parallel_requires_known_outline_failure")
    files = []
    for attempt, call in enumerate(calls):
        folder = output / "calls" / call["id"]
        _require(not (folder / "parsed.json").exists()
                 and _read(folder / "protocol_failure.json")["attempt"] == attempt,
                 "parallel_cannot_relabel_old_failure")
        files.extend({"path": str(folder / name), "sha256": sha256_file(folder / name)}
                     for name in ("request.json", "response.json", "protocol_failure.json"))
    _require(policy["protected_failure_files"] == files, "parallel_failure_files_changed")
    return policy


@_serialized
def authorize_parallel_execution(state, user_instruction, prompt_policy):
    """Append one explicit corrected-outline attempt and two independent lanes."""
    record = state.assert_protected()
    existing = _parallel_execution(state.output, state.data, record)
    if existing:
        _require(existing["prompt_policy_sha256"] == _prompt_digest(prompt_policy), "parallel_prompt_policy_changed")
        return existing
    _require(isinstance(user_instruction, str) and bool(user_instruction.strip()), "instruction_required")
    _require(isinstance(prompt_policy, (str, dict)) and bool(prompt_policy), "prompt_policy_required")
    added = state.data["calls"][record["baseline_request_count"]:]
    _require(all(c["status"] == "received" for c in added), "new_or_pending_outcome_unknown")
    calls = [c for c in added if _stage(c["name"])[0] == 0]
    _require(len(calls) == 2 and calls[0]["name"] == "sf_0_outline" and calls[1]["name"] == "sf_0_outline_repair"
             and calls[1].get("repair_of") == calls[0]["id"], "parallel_requires_known_outline_failure")
    files = []
    for attempt, call in enumerate(calls):
        folder = state.output / "calls" / call["id"]
        _require(not (folder / "parsed.json").exists()
                 and _read(folder / "protocol_failure.json")["attempt"] == attempt,
                 "parallel_cannot_relabel_old_failure")
        files.extend({"path": str(folder / name), "sha256": sha256_file(folder / name)}
                     for name in ("request.json", "response.json", "protocol_failure.json"))
    policy = {"policy": PARALLEL_POLICY, "task_id": state.data["task_id"], "preparation_id": record["preparation_id"],
              "authorization_record_sha256": state.data["artifacts"][ARTIFACT][0]["sha256"],
              "baseline_request_count": len(state.data["calls"]), "prefix_calls_sha256": json_sha(state.data["calls"]),
              "max_concurrent_parents": 2, "parent_rounds": [0, 3], "replacement_outline_stage": REPLACEMENT_OUTLINE,
              "failed_call_ids": [c["id"] for c in calls], "protected_failure_files": files,
              "prompt_policy": deepcopy(prompt_policy), "prompt_policy_sha256": _prompt_digest(prompt_policy),
              "user_instruction": user_instruction, "old_failures_preserved": True, "repairs_per_stage": 1}
    state.set_artifact(PARALLEL_POLICY, policy)
    state.assert_protected()
    return policy


def _fact_binding_inputs(output, data, record, parent, stage):
    """Recheck known replies and request time mapping; never infer movie events."""
    from .contracts import parse_model_json
    from .slot_finecut_contracts import validate_facts, validate_slots
    _require(parent == 0 and _stage(stage)[:2] == (parent, "facts") and not stage.endswith("_repair"),
             "request_bound_fact_stage_invalid")
    calls = [c for c in data["calls"][record["baseline_request_count"]:] if c["name"] in {stage, stage + "_repair"}]
    _require(len(calls) == 2 and calls[0]["name"] == stage and calls[1]["name"] == stage + "_repair"
             and not calls[0].get("repair_of") and calls[1].get("repair_of") == calls[0]["id"]
             and all(c["status"] == "received" for c in calls), "request_bound_fact_requires_known_sole_repair")
    preparation = _read(record["preparation_path"])
    selected_parent = next(p for p in preparation["parents"] if p["round"] == parent)
    folder = Path(record["execution_directory"]) / f"render_{parent}"
    outline_path = folder / "outline.json"
    outline = validate_slots(_read(outline_path), selected_parent)
    files, values, requests = [], [], []
    for attempt, call in enumerate(calls):
        call_folder = output / "calls" / call["id"]
        _require(not (call_folder / "parsed.json").exists(), "request_bound_fact_cannot_relabel_old_reply")
        request, response, failure = (_read(call_folder / name) for name in
                                      ("request.json", "response.json", "protocol_failure.json"))
        _require(json_sha(request) == call["request_sha256"] and json_sha(response) == call["response_sha256"]
                 and failure["attempt"] == attempt and failure["error"] == "slot_finecut:fact_time_binding_changed",
                 "request_bound_fact_original_failure_changed")
        raw = "\n".join(c["text"] for c in response["result"]["content"] if c.get("type") == "text")
        value = parse_model_json(raw)
        _require("time_domain" not in value, "request_bound_fact_only_missing_domain")
        values.append(value)
        requests.append(request)
        files.extend({"path": str(call_folder / name), "sha256": sha256_file(call_folder / name)}
                     for name in ("request.json", "response.json", "protocol_failure.json"))
    original, repair = requests
    slot = next(s for s in outline["slots"] if s["slot_id"] == values[1]["slot_id"])
    _require(slot["start_s"] == 0, "request_bound_fact_first_slot_only_no_time_shift")
    _require(json_sha({"parent": selected_parent["sha256"], "slot": slot})[:16] == _stage(stage)[2],
             "request_bound_fact_slot_key_changed")
    prompt = original["arguments"]["prompt"]
    position = prompt.rfind("\n{")
    _require(position >= 0 and "所有start_s/end_s是这个提供片段的局部输出秒数，从0开始" in prompt,
             "request_bound_fact_local_prompt_required")
    import json
    context = json.loads(prompt[position + 1:])
    contract = context["response_contract"]
    _require(contract["time_domain"] == "slot_local_output" and all(context[k] == contract[k] == values[1][k]
             for k in ("baseline_id", "parent_sha256", "slot_id", "slot_start_s", "slot_end_s")),
             "request_bound_fact_prompt_binding_changed")
    media = Path(original["arguments"]["video_source"]).resolve(strict=True)
    lineage_path = media.parent / "lineage.json"
    lineage = _read(lineage_path)
    expected_scope = {"kind": "continuous_window", "source_sha256": selected_parent["sha256"],
                      "source_start_s": slot["start_s"], "source_end_s": slot["end_s"]}
    _require(original["provider"] == repair["provider"] == "official_vision_mcp_in_codex"
             and original["tool"] == repair["tool"] == "analyze_video"
             and Path(repair["arguments"]["video_source"]).resolve() == media
             and sha256_file(media) == original["media_sha256"] == repair["media_sha256"] == lineage["sha256"]
             and scope_fingerprint(original["observation_scope"]) == scope_fingerprint(repair["observation_scope"])
                 == scope_fingerprint(expected_scope) == scope_fingerprint(lineage)
             and lineage["source_offset_s"] == slot["start_s"]
             and abs(lineage["media_duration_s"] - (slot["end_s"] - slot["start_s"])) <= .001
             and lineage["time_mapping"] == "source_time_s = source_offset_s + proxy_time_s",
             "request_bound_fact_media_mapping_changed")
    derived = deepcopy(values[1])
    derived["time_domain"] = "slot_local_output"
    # Each known attempt must have no other structural problem under the new
    # nonvisual inference taxonomy. The chosen body is ONLY the existing repair.
    for value in values:
        test = deepcopy(value)
        test["time_domain"] = "slot_local_output"
        validate_facts(test, selected_parent, slot)
    stop_entries = data["artifacts"].get(f"sf_parent_protocol_stop_{parent}", [])
    result_entries = data["artifacts"].get(f"sf_result_{parent}", [])
    _require(len(stop_entries) == len(result_entries) == 1, "request_bound_fact_stopped_result_required")
    stop, result_receipt = (_read(entries[0]["path"]) for entries in (stop_entries, result_entries))
    result_path = folder / "result.json"
    result = _read(result_path)
    _require(json_sha(stop) == stop_entries[0]["sha256"] and stop["original_call_id"] == calls[0]["id"]
             and stop["repair_call_id"] == calls[1]["id"] and stop["error"] == "model_protocol_repair_exhausted:" + stage
             and json_sha(result_receipt) == result_entries[0]["sha256"]
             and sha256_file(result_path) == result_receipt["result_sha256"]
             and result["status"] == "stopped_protocol_failure", "request_bound_fact_stop_changed")
    extra = [outline_path, lineage_path, media, result_path, Path(stop_entries[0]["path"]), Path(result_entries[0]["path"])]
    files.extend({"path": str(path), "sha256": sha256_file(path)} for path in extra)
    return {"parent_round": parent, "stage": stage, "original_call_id": calls[0]["id"], "repair_call_id": calls[1]["id"],
            "next_stage": stage.replace("_facts_", "_proposal_"), "body": derived,
            "model_body_sha256": json_sha(values[1]), "media_sha256": original["media_sha256"],
            "observation_scope": expected_scope, "protected_files": files}


def _request_bound_parent_facts(output, data, record):
    entries = data["artifacts"].get(REQUEST_BOUND_FACT_POLICY, [])
    if not entries:
        return None
    _require(len(entries) == 1, "one_request_bound_fact_record_required")
    policy = _read(entries[0]["path"])
    _require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == REQUEST_BOUND_FACT_POLICY
             and policy["task_id"] == data["task_id"] and policy["preparation_id"] == record["preparation_id"]
             and policy["taxonomy_version"] == PARENT_FACT_TAXONOMY
             and policy["original_contract_status"] == "failed"
             and policy["program_field_additions"] == {"time_domain": "slot_local_output"}
             and policy["semantic_truth_established"] is False and policy["old_parsed_created"] is False,
             "request_bound_fact_policy_changed")
    count = policy["baseline_request_count"]
    _require(type(count) is int and record["baseline_request_count"] <= count <= len(data["calls"])
             and json_sha(data["calls"][:count]) == policy["prefix_calls_sha256"], "request_bound_fact_prefix_changed")
    bound = _fact_binding_inputs(output, data, record, policy["parent_round"], policy["stage"])
    _require(all(policy[k] == value for k, value in bound.items()), "request_bound_fact_body_or_evidence_changed")
    return policy


@_serialized
def record_request_bound_parent_facts(state, parent=0, stage=None):
    """Append a limited derived body; no call, old parser file or fact is changed."""
    record = state.assert_protected()
    existing = _request_bound_parent_facts(state.output, state.data, record)
    if existing:
        _require(existing["parent_round"] == parent and existing["stage"] == stage, "request_bound_fact_cannot_replace")
        return existing
    _require(all(c["status"] == "received" for c in state.data["calls"][record["baseline_request_count"]:]),
             "new_or_pending_outcome_unknown")
    bound = _fact_binding_inputs(state.output, state.data, record, parent, stage)
    policy = {"policy": REQUEST_BOUND_FACT_POLICY, "task_id": state.data["task_id"],
              "preparation_id": record["preparation_id"], "taxonomy_version": PARENT_FACT_TAXONOMY,
              "baseline_request_count": len(state.data["calls"]), "prefix_calls_sha256": json_sha(state.data["calls"]),
              "original_contract_status": "failed", "program_field_additions": {"time_domain": "slot_local_output"},
              "semantic_truth_established": False, "old_parsed_created": False,
              "protocol_limitations": ["The old response failed the original contract; this is a separate forward protocol record.",
                  "Only the absent time_domain is program-bound from the original request and actual media lineage.",
                  "Model kind=inference remains nonvisual interpretation; it does not establish visible action.",
                  "Model facts, evidence times and basis remain fallible and require exact-source and actual-output review."], **bound}
    state.set_artifact(REQUEST_BOUND_FACT_POLICY, policy)
    state.assert_protected()
    return policy


def _point_navigation_inputs(output, data, record, parent, stage):
    from .contracts import parse_model_json
    from .slot_finecut_contracts import validate_parent_navigation, validate_slots
    _require(parent in POINT_NAVIGATION_STAGES and stage == POINT_NAVIGATION_STAGES[parent], "point_navigation_scope_invalid")
    calls = [c for c in data["calls"][record["baseline_request_count"]:] if c["name"] in {stage, stage + "_repair"}]
    _require(len(calls) == 2 and calls[0]["name"] == stage and calls[1]["name"] == stage + "_repair"
             and all(c["status"] == "received" for c in calls) and not calls[0].get("repair_of")
             and calls[1].get("repair_of") == calls[0]["id"], "point_navigation_known_sole_repair_required")
    selected_parent = next(p for p in _read(record["preparation_path"])["parents"] if p["round"] == parent)
    folder = Path(record["execution_directory"]) / f"render_{parent}"
    outline_path = folder / "outline.json"
    outline = validate_slots(_read(outline_path), selected_parent)
    requests, values, files = [], [], []
    expected_errors = ("slot_finecut/fact:outside_bound_interval",
                       "slot_finecut:fact_time_binding_changed" if parent == 3 else "slot_finecut/fact:outside_bound_interval")
    for attempt, call in enumerate(calls):
        call_folder = output / "calls" / call["id"]
        _require(not (call_folder / "parsed.json").exists(), "point_navigation_cannot_relabel_old_reply")
        request, response, failure = (_read(call_folder / name) for name in
                                      ("request.json", "response.json", "protocol_failure.json"))
        _require(json_sha(request) == call["request_sha256"] and json_sha(response) == call["response_sha256"]
                 and failure["attempt"] == attempt and failure["error"] == expected_errors[attempt],
                 "point_navigation_old_failure_changed")
        raw = "\n".join(c["text"] for c in response["result"]["content"] if c.get("type") == "text")
        values.append(parse_model_json(raw))
        requests.append(request)
        files.extend({"path": str(call_folder / name), "sha256": sha256_file(call_folder / name)}
                     for name in ("request.json", "response.json", "protocol_failure.json"))
    original, repair = requests
    chosen_index = 1 if parent == 3 else 0
    chosen = values[chosen_index]
    slot = next(s for s in outline["slots"] if s["slot_id"] == chosen["slot_id"])
    start, end = (16, 27) if parent == 3 else (29, 41)
    duration = end - start
    _require(slot["start_s"] == start and slot["end_s"] == end and values[0]["slot_start_s"] == start
             and values[0]["slot_end_s"] == end and all(v.get("time_domain") == "slot_local_output" for v in values)
             and (parent != 3 or (values[1]["slot_start_s"] == 0 and values[1]["slot_end_s"] == duration
                                  and values[0]["evidence"] == values[1]["evidence"])),
             "point_navigation_report_time_domains_changed")
    disagreements = []
    for field in sorted(set(values[0]) | set(values[1])):
        if values[0].get(field) != values[1].get(field):
            disagreements.append({"field": field, "original_present": field in values[0], "repair_present": field in values[1],
                                  "original_value": values[0].get(field), "repair_value": values[1].get(field)})
    prompt = original["arguments"]["prompt"]
    position = prompt.rfind("\n{")
    _require(position >= 0 and "所有start_s/end_s是这个提供片段的局部输出秒数，从0开始" in prompt,
             "point_navigation_local_prompt_required")
    import json
    context = json.loads(prompt[position + 1:])
    contract = context["response_contract"]
    _require(contract["time_domain"] == "slot_local_output" and all(context[k] == contract[k] == values[0][k]
             for k in ("baseline_id", "parent_sha256", "slot_id", "slot_start_s", "slot_end_s")),
             "point_navigation_request_binding_changed")
    media = Path(original["arguments"]["video_source"]).resolve(strict=True)
    lineage_path = media.parent / "lineage.json"
    lineage = _read(lineage_path)
    scope = {"kind": "continuous_window", "source_sha256": selected_parent["sha256"],
             "source_start_s": slot["start_s"], "source_end_s": slot["end_s"]}
    _require(original["provider"] == repair["provider"] == "official_vision_mcp_in_codex"
             and original["tool"] == repair["tool"] == "analyze_video"
             and Path(repair["arguments"]["video_source"]).resolve() == media
             and sha256_file(media) == original["media_sha256"] == repair["media_sha256"] == lineage["sha256"]
             and scope_fingerprint(original["observation_scope"]) == scope_fingerprint(repair["observation_scope"])
                 == scope_fingerprint(scope) == scope_fingerprint(lineage)
             and lineage["source_offset_s"] == start and abs(lineage["media_duration_s"] - duration) <= .001
             and lineage["time_mapping"] == "source_time_s = source_offset_s + proxy_time_s", "point_navigation_media_changed")
    support = [{"evidence_id": e["evidence_id"], "start_s": e["start_s"], "end_s": e["end_s"],
                "temporal_kind": "point" if e["start_s"] == e["end_s"] else "interval",
                "instant_only": e["start_s"] == e["end_s"], "duration_unknown": e["start_s"] == e["end_s"],
                "cannot_prove_completed_action": e["start_s"] == e["end_s"], "exposure_proof": False}
               for e in chosen["evidence"]]
    _require(any(row["instant_only"] for row in support), "point_navigation_no_point_observation")
    envelope = {"schema_version": POINT_NAVIGATION_POLICY, "request_binding": {
        "baseline_id": selected_parent["baseline_id"], "parent_sha256": selected_parent["sha256"], "slot_id": slot["slot_id"],
        "parent_slot_start_s": start, "parent_slot_end_s": end, "local_start_s": 0, "local_end_s": duration,
        "time_domain": "slot_local_output", "media_sha256": original["media_sha256"], "observation_scope": scope},
        "model_report": deepcopy(chosen), "temporal_support": support,
        "unresolved_model_disagreements": disagreements}
    validate_parent_navigation(envelope, selected_parent, slot)
    stop_key = "sf_parent_protocol_stop_0_metadata_bound_resume" if parent == 0 else "sf_parent_protocol_stop_3"
    result_key = "sf_result_0_metadata_bound_resume" if parent == 0 else "sf_result_3"
    stop_entries, result_entries = (data["artifacts"].get(key, []) for key in (stop_key, result_key))
    _require(len(stop_entries) == len(result_entries) == 1, "point_navigation_stopped_result_required")
    stop, receipt = (_read(entries[0]["path"]) for entries in (stop_entries, result_entries))
    result_path = folder / ("result_metadata_bound_resume.json" if parent == 0 else "result.json")
    stopped_result = _read(result_path)
    _require(json_sha(stop) == stop_entries[0]["sha256"] and stop["original_call_id"] == calls[0]["id"]
             and stop["repair_call_id"] == calls[1]["id"] and stop["error"] == "model_protocol_repair_exhausted:" + stage
             and json_sha(receipt) == result_entries[0]["sha256"] and sha256_file(result_path) == receipt["result_sha256"]
             and Path(receipt["result_path"]).resolve() == result_path.resolve()
             and stopped_result["status"] == "stopped_protocol_failure" and stopped_result.get("stop_receipt") == stop
             and stopped_result.get("baseline_id") == selected_parent["baseline_id"]
             and stopped_result.get("parent_sha256") == selected_parent["sha256"], "point_navigation_stop_changed")
    files.extend({"path": str(path), "sha256": sha256_file(path)} for path in
                 (outline_path, lineage_path, media, result_path, Path(stop_entries[0]["path"]), Path(result_entries[0]["path"])))
    if parent == 0:
        old_stop_entries = data["artifacts"].get("sf_parent_protocol_stop_0", [])
        _require(len(old_stop_entries) == 1, "point_navigation_old_stop_required")
        old_stop_path = Path(old_stop_entries[0]["path"])
        _require(json_sha(_read(old_stop_path)) == old_stop_entries[0]["sha256"], "point_navigation_old_stop_changed")
        old_result_entries = data["artifacts"].get("sf_result_0", [])
        _require(len(old_result_entries) == 1, "point_navigation_old_result_required")
        old_result_receipt_path = Path(old_result_entries[0]["path"])
        old_result_receipt = _read(old_result_receipt_path)
        old_result_path = folder / "result.json"
        _require(json_sha(old_result_receipt) == old_result_entries[0]["sha256"]
                 and Path(old_result_receipt["result_path"]).resolve() == old_result_path.resolve()
                 and sha256_file(old_result_path) == old_result_receipt["result_sha256"]
                 and _read(old_result_path)["status"] == "stopped_protocol_failure", "point_navigation_old_result_changed")
        parallel_stops = [p for p in Path(record["execution_directory"]).glob("parallel_stop_*.json")
                          if _read(p).get("request_count") == 153 and any(
                              row.get("baseline_id") == "render_0" and row.get("error") == "slot_finecut:parent_stop_error_changed"
                              for row in _read(p).get("errors", []))]
        _require(len(parallel_stops) == 1, "point_navigation_parallel_stop_required")
        files.extend({"path": str(p), "sha256": sha256_file(p)} for p in
                     (old_stop_path, old_result_path, old_result_receipt_path, parallel_stops[0]))
    return {"parent_round": parent, "stage": stage, "original_call_id": calls[0]["id"], "repair_call_id": calls[1]["id"],
            "next_stage": stage.replace("_facts_", "_proposal_"), "media_sha256": original["media_sha256"],
            "chosen_call_id": calls[chosen_index]["id"], "observation_scope": scope, "model_body_sha256": json_sha(chosen),
            "envelope": envelope, "protected_files": files}


def _parent_point_navigation(output, data, record, parent):
    entries = data["artifacts"].get(f"{POINT_NAVIGATION_POLICY}_{parent}", [])
    if not entries:
        return None
    _require(len(entries) == 1, "one_point_navigation_record_required")
    policy = _read(entries[0]["path"])
    _require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == POINT_NAVIGATION_POLICY
             and policy["task_id"] == data["task_id"] and policy["preparation_id"] == record["preparation_id"] and policy["parent_round"] == parent
             and policy["original_contract_status"] == "failed" and policy["retroactive_fact_pass"] is False
             and policy["old_parsed_created"] is False and policy["semantic_truth_established"] is False,
             "point_navigation_policy_changed")
    count = policy["baseline_request_count"]
    _require(type(count) is int and record["baseline_request_count"] <= count <= len(data["calls"])
             and json_sha(data["calls"][:count]) == policy["prefix_calls_sha256"], "point_navigation_prefix_changed")
    bound = _point_navigation_inputs(output, data, record, policy["parent_round"], policy["stage"])
    _require(all(policy[k] == value for k, value in bound.items()), "point_navigation_body_or_evidence_changed")
    return policy


@_serialized
def record_parent_point_navigation(state, parent=3, stage=None):
    stage = POINT_NAVIGATION_STAGES.get(parent) if stage is None else stage
    record = state.assert_protected()
    existing = _parent_point_navigation(state.output, state.data, record, parent)
    if existing:
        _require(existing["parent_round"] == parent and existing["stage"] == stage, "point_navigation_cannot_replace")
        return existing
    _require(all(c["status"] == "received" for c in state.data["calls"][record["baseline_request_count"]:]),
             "new_or_pending_outcome_unknown")
    bound = _point_navigation_inputs(state.output, state.data, record, parent, stage)
    policy = {"policy": POINT_NAVIGATION_POLICY, "task_id": state.data["task_id"], "preparation_id": record["preparation_id"],
        "baseline_request_count": len(state.data["calls"]), "prefix_calls_sha256": json_sha(state.data["calls"]),
        "original_contract_status": "failed", "retroactive_fact_pass": False, "old_parsed_created": False,
        "semantic_truth_established": False, "protocol_limitations": [
            "The original and sole repair still fail their historical fact contract; this is preliminary navigation only.",
            "The unchanged model report and the separate parent/global and media/local request bindings remain explicitly distinct.",
            "Point observations preserve their exact model timestamps and do not establish duration, exposure or completed action.",
            "All final source facts retain positive evidence intervals; actual-output blind and target reviews remain required.",
            "Disagreements between the original and repair are unresolved model evidence, never merged or treated as true."], **bound}
    state.set_artifact(f"{POINT_NAVIGATION_POLICY}_{parent}", policy)
    state.assert_protected()
    return policy


def _parent_stops(output, data, record):
    """Bind known exhausted stages, without converting their replies to passes."""
    stops = {}
    keys = [(0, 'sf_parent_protocol_stop_0'), (0, 'sf_parent_protocol_stop_0_metadata_bound_resume'),
            (0, 'sf_parent_protocol_stop_0_point_navigation_resume'), (0, 'sf_parent_protocol_stop_0_timing_bound_resume'),
            (3, 'sf_parent_protocol_stop_3'),
            (3, 'sf_parent_protocol_stop_3_point_navigation_resume'),
            (3, 'sf_parent_protocol_stop_3_assembly_bound_resume'),
            (3, 'sf_parent_protocol_stop_3_source_feedback_resume'),
            (0, 'sf_parent_protocol_stop_0_independent_resume'),
            (3, 'sf_parent_protocol_stop_3_independent_resume')]
    if not any(data['artifacts'].get(key) for _, key in keys):
        return stops
    # These receipts share one immutable state view. Recheck each compatibility
    # once per scan, rather than once again for every historical stop namespace.
    parallel = _parallel_execution(output, data, record)
    bound = _request_bound_parent_facts(output, data, record)
    navigation_by_parent = {parent: _parent_point_navigation(output, data, record, parent)
                            for parent in {parent for parent, key in keys if data['artifacts'].get(key)}}
    assembly = _request_bound_assembly(output, data, record)
    timing = _request_bound_proposal_retiming(output, data, record)
    feedback = _source_feedback(output, data, record)
    for parent, key in keys:
        entries = data['artifacts'].get(key, [])
        if not entries:
            continue
        _require(len(entries) == 1, 'one_parent_stop_required')
        receipt = _read(entries[0]['path'])
        _require(json_sha(receipt) == entries[0]['sha256'] and receipt['policy'] == PARENT_STOP_POLICY
                 and receipt['preparation_id'] == record['preparation_id'] and receipt['parent_round'] == parent,
                 'parent_stop_changed')
        count = receipt['request_count']
        _require(type(count) is int and record['baseline_request_count'] + 2 <= count <= len(data['calls']),
                 'parent_stop_invalid_count')
        _require(parent not in stops or stops[parent]['request_count'] <= count, 'parent_stop_namespace_order_changed')
        parent_calls = [c for c in data['calls'][record['baseline_request_count']:count] if _stage(c['name'])[0] == parent]
        original, repair = parent_calls[-2:]
        _require(original['id'] == receipt['original_call_id'] and repair['id'] == receipt['repair_call_id']
                 and _stage(original['name'])[0] == parent and not original.get('repair_of')
                 and repair['name'] == original['name'] + '_repair' and repair.get('repair_of') == original['id']
                 and original['status'] == repair['status'] == 'received'
                 and receipt['error'] == 'model_protocol_repair_exhausted:' + original['name'],
                 'parent_stop_not_known_exhausted_repair')
        expected_files = []
        for attempt, call in enumerate((original, repair)):
            folder = output / 'calls' / call['id']
            _require(not (folder / 'parsed.json').exists(), 'parent_stop_cannot_hide_parsed_reply')
            failure = _read(folder / 'protocol_failure.json')
            _require(failure['attempt'] == attempt, 'parent_stop_failure_attempt_changed')
            for name in ('request.json', 'response.json', 'protocol_failure.json'):
                path = folder / name
                expected_files.append({'path': str(path), 'sha256': sha256_file(path)})
            _require(json_sha(_read(folder / 'request.json')) == call['request_sha256']
                     and json_sha(_read(folder / 'response.json')) == call['response_sha256'],
                     'parent_stop_reply_binding_changed')
        _require(receipt['files'] == expected_files, 'parent_stop_files_changed')
        other = 3 if parent == 0 else 0
        target = other
        _require(receipt['independent_parent_round'] == target, 'parent_stop_target_changed')
        later = data['calls'][count:]
        navigation = navigation_by_parent[parent]
        resumed = any(row is not None and row['parent_round'] == parent and row['repair_call_id'] == repair['id']
                      and (not any(_stage(c['name'])[0] == parent for c in later) or
                           next(c['name'] for c in later if _stage(c['name'])[0] == parent) ==
                           (feedback['stage'] if row is assembly and feedback is not None else row['next_stage']))
                      for row in (bound, navigation, assembly, timing)
                      if not key.endswith(('_assembly_bound_resume', '_timing_bound_resume', '_source_feedback_resume', '_independent_resume')))
        _require(not any(_stage(c['name'])[0] == parent for c in later) or resumed or
                 (parallel is not None and parent == 0 and original['name'] == 'sf_0_outline'
                  and next(c['name'] for c in later if _stage(c['name'])[0] == parent) == REPLACEMENT_OUTLINE),
                 'stopped_parent_cannot_repeat')
        stops[parent] = receipt
    return stops


def _request_bound_assembly(output, data, record):
    from .slot_finecut_assembly_binding import request_bound_assembly
    return request_bound_assembly(output, data, record)


def _source_feedback(output, data, record):
    from .slot_source_feedback import read_validate
    return read_validate(output, data, record)


def _independent_recovery(output, data, record):
    if not data['artifacts'].get(INDEPENDENT_RECOVERY_POLICY):
        return None
    from .independent_slot_recovery import read_validate
    return read_validate(output, data, record)


def _bound_stage_is(data, artifact, stage):
    """Cheap, hash-checked routing only; matching bodies still get full checks."""
    entries = data['artifacts'].get(artifact, [])
    if not entries:
        return False
    _require(len(entries) == 1, 'one_bound_stage_record_required')
    policy = _read(entries[0]['path'])
    _require(json_sha(policy) == entries[0]['sha256'], 'bound_stage_policy_changed')
    return policy.get('stage') == stage


@_serialized
def record_request_bound_assembly(state, parent=3):
    from .slot_finecut_assembly_binding import record_request_bound_assembly as register
    return register(state, parent=parent)


def _request_bound_proposal_retiming(output, data, record):
    from .slot_proposal_timing_binding import read_validate
    return read_validate(output, data, record)


@_serialized
def record_request_bound_proposal_retiming(state, parent=0):
    from .slot_proposal_timing_binding import record_request_bound_proposal_retiming as register
    return register(state, parent=parent)


def execution_folder(output, preparation_id):
    """Use a short directory while keeping the full preparation identity bound."""
    if not isinstance(preparation_id, str) or re.fullmatch(r"slot_finecut_preparation_[a-f0-9]{64}", preparation_id) is None:
        raise LibraryStopped("slot_finecut:invalid_preparation_identity")
    digest = preparation_id.rsplit("_", 1)[1]
    return Path(output).resolve() / "artifacts" / POLICY / ("sf_" + digest[:12])


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("slot_finecut:" + reason)


def _stage(name):
    _require(isinstance(name, str) and (re.fullmatch(ALLOWED_STAGE_PATTERN, name)
             or name in {REPLACEMENT_OUTLINE, REPLACEMENT_OUTLINE + "_repair",
                         SOURCE_FEEDBACK_STAGE, SOURCE_FEEDBACK_STAGE + "_repair",
                         LOCAL_REPLAN_STAGE, LOCAL_REPLAN_STAGE + "_repair"}), "unknown_stage")
    stem = name.removesuffix("_repair")
    if stem == SOURCE_FEEDBACK_STAGE:
        return 3, 'source_feedback', None
    if stem == LOCAL_REPLAN_STAGE:
        return 3, 'local_replan', None
    parts = stem.split("_")
    if parts[0] == "sf":
        return int(parts[1]), parts[2], parts[3] if len(parts) == 4 else None
    return {20: 0, 21: 3}[int(parts[2])], parts[1], parts[3]


def _unknown_inputs(output, calls):
    from .independent_source_resume import _scope
    inputs = []
    for call in calls:
        if call["status"] != "uncertain":
            continue
        request = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(request) == call["request_sha256"], "unknown_original_request_changed")
        inputs.append({"call_id": call["id"], "request_sha256": call["request_sha256"],
                       "media_sha256": request["media_sha256"], "scope": _scope(request)})
    return inputs


def _exhausted_inputs(output, calls):
    from .goal_feedback_continuation import _exhausted_slice_scopes
    return _exhausted_slice_scopes(SimpleNamespace(output=output, data={"calls": calls}))


def blocked_source_scopes(state):
    """Mechanical no-replay boundaries; these do not propose replacement cuts."""
    record = state.assert_protected()
    return deepcopy(record["unknown_inputs"] + _exhausted_inputs(state.output, state.data["calls"]))


def authorize(output, preparation_path, user_instruction):
    """Append permission only when explicitly invoked with future user intent."""
    from .extension_budget import _protected_files
    from .slot_finecut_baselines import load_preparation
    output = Path(output).resolve(strict=True)
    preparation_path = Path(preparation_path).resolve(strict=True)
    _require(isinstance(user_instruction, str) and bool(user_instruction.strip()), "instruction_required")
    _require(preparation_path.is_relative_to(output / "artifacts"), "preparation_outside_original_run")
    with file_lock(output / ".slot_finecut_authorization.lock"):
        saved = _read(output / "library_state.json")
        if saved["artifacts"].get(ARTIFACT):
            record = load_authorization(output)
            _require(Path(record["preparation_path"]) == preparation_path, "preparation_cannot_be_replaced")
            return record
        preparation = load_preparation(preparation_path)
        _require(Path(preparation["output"]).resolve() == output and preparation["task_id"] == saved["task_id"]
                 and preparation["input_lock_sha256"] == json_sha(saved["input_lock"])
                 and preparation["baseline_rounds"] == [0, 3], "preparation_not_same_task")
        _require(type(saved["max_requests"]) is int and 1 <= saved["max_requests"] <= 80
                 and saved["request_count"] == len(saved["calls"]), "invalid_base_ledger")
        _require(all(c["status"] in {"received", "uncertain"} for c in saved["calls"]), "pending_or_failed_baseline")
        unknowns = [c["id"] for c in saved["calls"] if c["status"] == "uncertain"]
        _require(set(unknowns) <= ADMITTED_UNKNOWN_IDS, "unapproved_unknown_baseline")
        _require(not any(re.fullmatch(ALLOWED_STAGE_PATTERN, c["name"]) for c in saved["calls"]),
                 "stage_already_paid_without_authorization")
        folder = execution_folder(output, preparation["preparation_id"])
        _require(not folder.exists(), "incomplete_authorization_snapshot")
        protected = {row["path"]: {"path": row["path"], "sha256": row["sha256"]}
                     for row in preparation["protected_files"] + _protected_files(output)}
        unknown_inputs = _unknown_inputs(output, saved["calls"])
        methods_path = next((p for p in (output / "artifacts/editing_revision_v1/reference_methods.json",
                                        output / "editing_reference_v2.json") if p.is_file()), None)
        _require(methods_path is not None, "cached_reference_methods_required")
        protected[str(methods_path.resolve())] = {"path": str(methods_path.resolve()), "sha256": sha256_file(methods_path)}
        folder.mkdir(parents=True)
        snapshot = folder / "baseline_calls.json"
        write_json(snapshot, saved["calls"])
        knowledge_path = folder / "SLOT_FINECUT.md"
        knowledge_path.write_bytes((Path(__file__).with_name("craft_knowledge") / "SLOT_FINECUT.md").read_bytes())
        record = {"policy": POLICY, "request_limit_policy": REQUEST_LIMIT_POLICY,
            "task_id": saved["task_id"], "original_output": str(output),
            "input_lock_sha256": json_sha(saved["input_lock"]), "base_request_limit": saved["max_requests"],
            "baseline_request_count": saved["request_count"], "baseline_calls_count": len(saved["calls"]),
            "baseline_calls_path": str(snapshot),
            "baseline_calls_sha256": sha256_file(snapshot), "prefix_calls_sha256": json_sha(saved["calls"]),
            "admitted_unknown_call_ids": unknowns, "unknown_inputs": unknown_inputs,
            "exhausted_source_inputs": _exhausted_inputs(output, saved["calls"]),
            "preparation_path": str(preparation_path), "preparation_sha256": sha256_file(preparation_path),
            "preparation_id": preparation["preparation_id"], "parent_rounds": [0, 3],
            "execution_directory": str(folder),
            "protected_files": sorted(protected.values(), key=lambda row: row["path"]),
            "baseline_artifact_names": sorted(saved["artifacts"]),
            "baseline_render_directories": sorted(p.name for p in output.glob("render_*") if p.is_dir()),
            "allowed_render_directories": [str(folder / f"render_{parent}" / "render") for parent in (0, 3)],
            "knowledge_path": str(knowledge_path), "knowledge_sha256": sha256_file(knowledge_path),
            "reference_methods_path": str(methods_path.resolve()), "reference_methods_sha256": sha256_file(methods_path),
            "user_instruction": user_instruction, "allowed_stage_pattern": ALLOWED_STAGE_PATTERN,
            "additional_requests": None, "effective_request_limit": None,
            "max_slices_per_parent": 32, "renders_per_parent": 1, "repairs_per_stage": 1,
            "new_unique_windows": 0, "validated_on_resume": True, "automatic_round_loops": False}
        authorization_path = folder / "authorization.json"
        with file_lock(output / ".state.lock"):
            current = _read(output / "library_state.json")
            _require(current == saved, "state_changed_during_authorization")
            write_json(authorization_path, record)
            current["artifacts"][ARTIFACT] = [{"path": str(authorization_path),
                                              "sha256": json_sha(record), "at": _timestamp()}]
            write_json(output / "library_state.json", current)
        return load_authorization(output)


def _validate_added(output, calls, record):
    added = calls[record["baseline_request_count"]:]
    names = {}
    parent_last = {}
    parallel = _parallel_execution(output, _read(output / "library_state.json"), record)
    pending = [c for c in added if c["status"] == "submitted"]
    _require(len(pending) <= (2 if parallel else 1) and
             len({_stage(c["name"])[0] for c in pending}) == len(pending), "parallel_pending_parent_limit")
    for call in added:
        parent, kind, key = _stage(call["name"])
        if kind == 'source_feedback':
            _require(_source_feedback(output, _read(output / 'library_state.json'), record) is not None,
                     'source_feedback_not_authorized')
        if kind == 'local_replan':
            _require(_independent_recovery(output, _read(output / 'library_state.json'), record) is not None,
                     'independent_local_replan_not_authorized')
        _require(call["name"] not in names, "duplicate_stage_in_ledger")
        if call["name"].endswith("_repair"):
            original = names.get(call["name"][:-7])
            _require(original is not None and original["status"] == "received"
                     and call.get("repair_of") == original["id"] and not original.get("repair_of")
                     and (parent_last.get(parent) == original["name"] if parallel else list(names)[-1] == original["name"]),
                     "repair_binding_changed")
        else:
            _require(call.get("repair_of") is None, "original_has_repair_parent")
        names[call["name"]] = call
        parent_last[parent] = call["name"]
        request = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(request) == call["request_sha256"], "new_request_changed")
        if call.get("repair_of"):
            original_request = _read(output / "calls" / call["repair_of"] / "request.json")
            _require(request["media_sha256"] == original_request["media_sha256"]
                     and scope_fingerprint(request["observation_scope"]) ==
                         scope_fingerprint(original_request["observation_scope"]), "repair_media_or_scope_changed")
        if call["status"] == "received":
            response = _read(output / "calls" / call["id"] / "response.json")
            _require(json_sha(response) == call["response_sha256"], "new_response_changed")
    for parent in (0, 3):
        for kind in ("facts", "slice"):
            _require(sum(_stage(c["name"])[:2] == (parent, kind) and not c.get("repair_of")
                         for c in added) <= record["max_slices_per_parent"], "slice_limit_exceeded")
    return names


def load_authorization(output):
    """Read and revalidate permission; never activate it or rewrite history."""
    from .slot_finecut_baselines import load_preparation
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    entries = data["artifacts"].get(ARTIFACT, [])
    _require(len(entries) == 1, "one_authorization_required")
    authorization_path = Path(entries[0]["path"]).resolve(strict=True)
    _require(authorization_path.is_relative_to(output / "artifacts" / POLICY), "authorization_outside_original_run")
    record = _read(authorization_path)
    _require(json_sha(record) == entries[0]["sha256"] and record.get("policy") == POLICY
             and record["request_limit_policy"] == REQUEST_LIMIT_POLICY
             and record["task_id"] == data["task_id"] and Path(record["original_output"]).resolve() == output
             and record["input_lock_sha256"] == json_sha(data["input_lock"])
             and record["base_request_limit"] == data["max_requests"], "authorization_changed")
    _require(record["parent_rounds"] == [0, 3] and record["max_slices_per_parent"] == 32
             and record["renders_per_parent"] == record["repairs_per_stage"] == 1
             and record["new_unique_windows"] == 0 and record["validated_on_resume"] is True
             and record["automatic_round_loops"] is False
             and record["additional_requests"] is record["effective_request_limit"] is None
             and record["allowed_stage_pattern"] == ALLOWED_STAGE_PATTERN
             and record["allowed_render_directories"] == [str(authorization_path.parent / f"render_{parent}" / "render")
                                                           for parent in (0, 3)], "execution_scope_changed")
    count = record["baseline_request_count"]
    _require(type(count) is int and count >= 0 and data["request_count"] == len(data["calls"])
             and record["baseline_calls_count"] == count and len(data["calls"]) >= count, "ledger_count_changed")
    snapshot = Path(record["baseline_calls_path"]).resolve(strict=True)
    _require(snapshot.parent == authorization_path.parent and sha256_file(snapshot) == record["baseline_calls_sha256"],
             "baseline_snapshot_changed")
    baseline = _read(snapshot)
    _require(len(baseline) == count and json_sha(baseline) == record["prefix_calls_sha256"]
             and data["calls"][:count] == baseline, "original_calls_changed")
    unknowns = [c["id"] for c in baseline if c["status"] == "uncertain"]
    _require(record["admitted_unknown_call_ids"] == unknowns and set(unknowns) <= ADMITTED_UNKNOWN_IDS
             and record["unknown_inputs"] == _unknown_inputs(output, baseline), "unknown_exclusions_changed")
    _require(record["exhausted_source_inputs"] == _exhausted_inputs(output, baseline), "exhausted_exclusions_changed")
    preparation_path = Path(record["preparation_path"]).resolve(strict=True)
    _require(preparation_path.is_relative_to(output / "artifacts")
             and sha256_file(preparation_path) == record["preparation_sha256"], "preparation_changed")
    preparation = load_preparation(preparation_path)
    _require(preparation["preparation_id"] == record["preparation_id"]
             and preparation["task_id"] == data["task_id"] and Path(preparation["output"]).resolve() == output
             and preparation["input_lock_sha256"] == record["input_lock_sha256"], "preparation_binding_changed")
    _require(authorization_path.parent == execution_folder(output, record["preparation_id"])
             and record["execution_directory"] == str(authorization_path.parent),
             "authorization_preparation_directory_changed")
    for item in record["protected_files"]:
        path = Path(item["path"]).resolve(strict=True)
        _require(sha256_file(path) == item["sha256"], "protected_file_changed:" + str(path))
    knowledge = Path(record["knowledge_path"]).resolve(strict=True)
    _require(knowledge == authorization_path.parent / "SLOT_FINECUT.md"
             and sha256_file(knowledge) == record["knowledge_sha256"], "knowledge_snapshot_changed")
    _require(sha256_file(record["reference_methods_path"]) == record["reference_methods_sha256"], "reference_methods_changed")
    directories = {p.name for p in output.glob("render_*") if p.is_dir()}
    original_dirs = set(record["baseline_render_directories"])
    _require(original_dirs == directories,
             "unapproved_or_missing_render_directory")
    for parent, directory in zip((0, 3), record["allowed_render_directories"], strict=True):
        claims = data["artifacts"].get(f"sf_{parent}_render_claim", [])
        if claims:
            _require(len(claims) == 1, "multiple_render_claims")
            claim = _read(claims[0]["path"])
            _require(json_sha(claim) == claims[0]["sha256"] and claim["policy"] == POLICY
                     and claim["parent_round"] == parent and claim["directory"] == directory
                     and claim["preparation_id"] == record["preparation_id"] and claim["max_renders"] == 1
                     and sha256_file(claim["source_manifest_path"]) == claim["source_manifest_sha256"]
                     and sha256_file(claim["assembly_path"]) == claim["assembly_sha256"], "render_claim_changed")
        if Path(directory).exists():
            _require(bool(claims), "render_without_claim")
    _validate_added(output, data["calls"], record)
    _parallel_execution(output, data, record)
    _request_bound_parent_facts(output, data, record)
    for parent in (0, 3):
        _parent_point_navigation(output, data, record, parent)
    _request_bound_assembly(output, data, record)
    _request_bound_proposal_retiming(output, data, record)
    _source_feedback(output, data, record)
    _independent_recovery(output, data, record)
    _parent_stops(output, data, record)
    return {**record, "authorization_path": str(authorization_path),
            "authorization_sha256": sha256_file(authorization_path)}


class SlotFinecutState(LibraryState):
    """One pass per parent, sole repairs, independent inputs and original ledger."""
    @_serialized
    def __init__(self, output):
        record = load_authorization(output)
        saved = _read(Path(output) / "library_state.json")
        super().__init__(output, saved["input_lock"], max_requests=saved["max_requests"])
        self.max_requests = float("inf")
        self.authorization = record

    @_serialized
    def assert_protected(self):
        self.authorization = load_authorization(self.output)
        self._reload()
        return self.authorization

    @_serialized
    def freeze_parent_protocol_failure(self, parent, error):
        """Stop this known exhausted branch; permit only an untouched other parent."""
        record = self.assert_protected()
        _require(type(parent) is int and parent in (0, 3), 'unknown_parent')
        suffix = ('_independent_resume' if self.data['artifacts'].get(INDEPENDENT_RECOVERY_POLICY) else
                  '_source_feedback_resume' if parent == 3 and self.data['artifacts'].get(SOURCE_FEEDBACK_POLICY) else
                  '_assembly_bound_resume' if parent == 3 and self.data['artifacts'].get(REQUEST_BOUND_ASSEMBLY_POLICY) else
                  '_timing_bound_resume' if parent == 0 and self.data['artifacts'].get(REQUEST_BOUND_PROPOSAL_RETIMING_POLICY) else
                  '_point_navigation_resume' if self.data['artifacts'].get(f'{POINT_NAVIGATION_POLICY}_{parent}') else
                  '_metadata_bound_resume' if parent == 0 and self.data['artifacts'].get(REQUEST_BOUND_FACT_POLICY) else '')
        key = f'sf_parent_protocol_stop_{parent}' + suffix
        existing = _parent_stops(self.output, self.data, record)
        if self.data['artifacts'].get(key):
            receipt = _read(self.data['artifacts'][key][0]['path'])
            _require(receipt['error'] == str(error), 'parent_stop_error_changed')
            return receipt
        added = self.data['calls'][record['baseline_request_count']:]
        parallel = _parallel_execution(self.output, self.data, record)
        own = [c for c in added if _stage(c['name'])[0] == parent]
        recovery = _independent_recovery(self.output, self.data, record)
        from .independent_slot_recovery import admissible_status
        _require(len(own) >= 2 and all(admissible_status(recovery, c) for c in added)
                 and all(c['status'] != 'submitted' and admissible_status(recovery, c)
                         for c in (own if parallel else added)), 'new_or_pending_outcome_unknown')
        original, repair = own[-2:]
        _require(_stage(original['name'])[0] == parent and not original.get('repair_of')
                 and repair.get('repair_of') == original['id'] and repair['name'] == original['name'] + '_repair'
                 and str(error) == 'model_protocol_repair_exhausted:' + original['name'],
                 'parent_stop_not_known_exhausted_repair')
        files = []
        for attempt, call in enumerate((original, repair)):
            folder = self.output / 'calls' / call['id']
            _require(not (folder / 'parsed.json').exists()
                     and _read(folder / 'protocol_failure.json')['attempt'] == attempt,
                     'parent_stop_requires_original_failures')
            files.extend({'path': str(folder / name), 'sha256': sha256_file(folder / name)}
                         for name in ('request.json', 'response.json', 'protocol_failure.json'))
        other = 3 if parent == 0 else 0
        receipt = {'policy': PARENT_STOP_POLICY, 'preparation_id': record['preparation_id'],
            'parent_round': parent, 'independent_parent_round': other,
            'request_count': len(self.data['calls']), 'original_call_id': original['id'],
            'repair_call_id': repair['id'], 'error': str(error), 'files': files,
            'no_replay_or_quality_pass': True}
        self.set_artifact(key, receipt)
        self.assert_protected()
        return receipt

    @_serialized
    def assert_source_inputs(self, plan, catalog):
        """Check every proposed source scope before the first exact paid fact."""
        blocked = blocked_source_scopes(self)
        recovery = _independent_recovery(self.output, self.data, self.authorization)
        if recovery:
            blocked.extend(recovery['frozen_unknown_inputs'])
        sources = {s["source_id"]: s for s in catalog["sources"]}
        _require(0 < len(plan["segments"]) <= self.authorization["max_slices_per_parent"], "slice_limit_exceeded")
        for segment in plan["segments"]:
            source = sources[segment["source_id"]]
            scope = {"kind": "continuous_window", "source_sha256": source["sha256"],
                     "source_start_s": segment["source_in_s"], "source_end_s": segment["source_out_s"]}
            _require(not any(scope_fingerprint(scope) == scope_fingerprint(old["scope"]) for old in blocked),
                     "selected_source_scope_blocked_no_replacement")
        return plan

    def _received(self, names, name):
        from .goal_budget import _call_value
        if name.startswith('sf_0_facts_') and _bound_stage_is(self.data, REQUEST_BOUND_FACT_POLICY, name):
            bound = _request_bound_parent_facts(self.output, self.data, self.authorization)
            return deepcopy(bound['body'])
        if name == 'sf_3_assemble' and _bound_stage_is(self.data, REQUEST_BOUND_ASSEMBLY_POLICY, name):
            assembly = _request_bound_assembly(self.output, self.data, self.authorization)
            return deepcopy(assembly['body'])
        if name.startswith('sf_0_proposal_') and _bound_stage_is(self.data, REQUEST_BOUND_PROPOSAL_RETIMING_POLICY, name):
            timing = _request_bound_proposal_retiming(self.output, self.data, self.authorization)
            return deepcopy(timing['body'])
        for parent in (0, 3):
            if name == POINT_NAVIGATION_STAGES[parent] and _bound_stage_is(self.data, f'{POINT_NAVIGATION_POLICY}_{parent}', name):
                navigation = _parent_point_navigation(self.output, self.data, self.authorization, parent)
                return deepcopy(navigation['envelope'])
        if name == "sf_0_outline" and _parallel_execution(self.output, self.data, self.authorization):
            name = REPLACEMENT_OUTLINE
        calls = [names[n] for n in (name, name + "_repair") if n in names]
        _require(bool(calls) and calls[-1]["status"] == "received"
                 and (self.output / "calls" / calls[-1]["id"] / "parsed.json").is_file(),
                 "predecessor_unsettled:" + name)
        return _call_value(self.output, calls[-1])[1]

    @_serialized
    def derived_fact(self, stage, *, media_sha256=None):
        if not stage.startswith('sf_0_facts_') or not _bound_stage_is(self.data, REQUEST_BOUND_FACT_POLICY, stage):
            return None
        record = self.assert_protected()
        bound = _request_bound_parent_facts(self.output, self.data, record)
        if bound is None or stage != bound["stage"]:
            return None
        _require(media_sha256 is None or media_sha256 == bound["media_sha256"], "request_bound_fact_current_media_changed")
        return deepcopy(bound["body"])

    @_serialized
    def derived_parent_navigation(self, stage, *, media_sha256=None):
        matching = next((parent for parent in (0, 3) if stage == POINT_NAVIGATION_STAGES[parent]
                         and _bound_stage_is(self.data, f'{POINT_NAVIGATION_POLICY}_{parent}', stage)), None)
        if matching is None:
            return None
        record = self.assert_protected()
        navigation = _parent_point_navigation(self.output, self.data, record, matching)
        _require(media_sha256 is None or media_sha256 == navigation['media_sha256'], 'point_navigation_current_media_changed')
        return deepcopy(navigation['envelope'])

    @_serialized
    def derived_assembly(self, stage, *, media_sha256=None):
        if stage != 'sf_3_assemble' or not _bound_stage_is(self.data, REQUEST_BOUND_ASSEMBLY_POLICY, stage):
            return None
        record = self.assert_protected()
        assembly = _request_bound_assembly(self.output, self.data, record)
        if assembly is None or stage != assembly['stage']:
            return None
        _require(media_sha256 is None or media_sha256 == assembly['media_sha256'], 'request_bound_assembly_current_media_changed')
        return deepcopy(assembly['body'])

    @_serialized
    def derived_proposal_retiming(self, stage, *, media_sha256=None):
        if not stage.startswith('sf_0_proposal_') or not _bound_stage_is(self.data, REQUEST_BOUND_PROPOSAL_RETIMING_POLICY, stage):
            return None
        record = self.assert_protected()
        timing = _request_bound_proposal_retiming(self.output, self.data, record)
        if timing is None or stage != timing['stage']:
            return None
        _require(media_sha256 is None or media_sha256 == timing['media_sha256'], 'proposal_retiming_current_media_changed')
        return deepcopy(timing['body'])

    def _progress(self, name, names, repair_of):
        parent, kind, key = _stage(name)
        _require(name not in names, "duplicate_stage")
        if name.endswith("_repair"):
            previous = names.get(name[:-7])
            ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            _require(previous is not None and previous["status"] == "received" and ident == previous["id"]
                     and [n for n in names if _stage(n)[0] == parent][-1] == previous["name"]
                     and not previous.get("repair_of"), "sole_repair_requires_original")
            return
        _require(repair_of is None, "original_has_repair_parent")
        prefix = f"sf_{parent}_"
        if kind == "outline":
            parallel = _parallel_execution(self.output, self.data, self.authorization)
            _require(key != "v2" or parallel is not None, "replacement_outline_not_authorized")
            return
        self._received(names, prefix + "outline")
        if kind in {"facts", "proposal"}:
            _require(not any(n.startswith(prefix + "assemble") for n in names), "slot_stage_after_assemble")
            if kind == "proposal":
                self._received(names, prefix + "facts_" + key)
        elif kind == "assemble":
            proposals = [n for n in names if n.startswith(prefix + "proposal_") and not n.endswith("_repair")]
            _require(bool(proposals), "assemble_requires_proposals")
            for proposal in proposals:
                self._received(names, proposal)
            for fact in [n for n in names if n.startswith(prefix + "facts_") and not n.endswith("_repair")]:
                self._received(names, fact.replace("_facts_", "_proposal_"))
        else:
            self._received(names, prefix + "assemble")
            recovery = _independent_recovery(self.output, self.data, self.authorization)
            feedback = _source_feedback(self.output, self.data, self.authorization) if parent == 3 else None
            if kind == 'local_replan':
                _require(recovery is not None and name == LOCAL_REPLAN_STAGE,
                         'independent_local_replan_not_authorized')
                return
            if kind == 'source_feedback':
                _require(feedback is not None and name == feedback['stage'], 'source_feedback_not_authorized')
                return
            if recovery is not None and parent == 3:
                replanned = self._received(names, LOCAL_REPLAN_STAGE)
                _require(replanned['status'] == 'planned', 'unavailable_local_replan_cannot_continue')
            elif feedback is not None:
                replanned = self._received(names, feedback['stage'])
                _require(replanned['status'] == 'planned', 'unavailable_source_feedback_cannot_continue')
            semantic = f"semantic_{{}}_{20 if parent == 0 else 21}_"
            if kind in {"slice", "claims"}:
                _require(not any(n.startswith(prefix + "blind") for n in names), "source_stage_after_blind")
            if kind == "claims":
                self._received(names, semantic.format("slice") + key)
            elif kind in {"blind", "economy", "review"}:
                slices = [n for n in names if n.startswith(semantic.format("slice")) and not n.endswith("_repair")]
                _require(bool(slices), "blind_requires_exact_source_checks")
                for sliced in slices:
                    self._received(names, sliced.replace("semantic_slice_", "semantic_claims_"))
                if kind in {"economy", "review"}:
                    self._received(names, prefix + "blind")
                if kind == "review":
                    self._received(names, prefix + "economy")

    def _check_input(self, name, request, record):
        from .goal_feedback_continuation import _exhausted_slice_scopes
        fingerprint = scope_fingerprint(request.get("observation_scope"))
        media = request.get("media_sha256")
        _require(isinstance(media, str) and re.fullmatch(r"[a-f0-9]{64}", media), "bound_media_sha_required")
        for old in record["unknown_inputs"]:
            _require(json_sha(request) != old["request_sha256"] and media != old["media_sha256"]
                     and fingerprint != scope_fingerprint(old["scope"]), "unknown_input_replay_forbidden")
        _, kind, _ = _stage(name)
        recovery = _independent_recovery(self.output, self.data, record)
        if recovery:
            from .independent_slot_recovery import check_input
            check_input(recovery, name, request)
        assembly = _request_bound_assembly(self.output, self.data, record)
        feedback = _source_feedback(self.output, self.data, record)
        from .slot_proposal_timing_binding import check_input
        check_input(_request_bound_proposal_retiming(self.output, self.data, record), name, request)
        if assembly is not None and _stage(name)[0] == 3 and kind == 'assemble':
            _require(False, 'request_bound_assembly_no_third_assembly')
        if kind == 'source_feedback':
            _require(feedback is not None and request.get('media_sha256') == feedback['media_sha256']
                     and fingerprint == scope_fingerprint(feedback['observation_scope']),
                     'source_feedback_parent_media_changed')
        bound = _request_bound_parent_facts(self.output, self.data, record)
        if bound is not None and kind in {"facts", "slice"}:
            _require(fingerprint != scope_fingerprint(bound["observation_scope"]) and media != bound["media_sha256"],
                     "request_bound_fact_no_third_observation")
        if kind in {'facts', 'slice'}:
            for parent in (0, 3):
                navigation = _parent_point_navigation(self.output, self.data, record, parent)
                if navigation is not None:
                    _require(fingerprint != scope_fingerprint(navigation['observation_scope']) and
                             media != navigation['media_sha256'], 'point_navigation_no_third_observation')
        if kind in {"facts", "slice"} and not name.endswith("_repair"):
            _require(not any(fingerprint == scope_fingerprint(old["scope"]) for old in _exhausted_slice_scopes(self)),
                     "known_exhausted_slice_lineage_no_third_observation")
        if kind == "claims" and not name.endswith("_repair"):
            from .explicit_claims import comparison_fingerprint, old_claim_input
            current = old_claim_input(request.get("arguments", {}).get("prompt"))
            if current is not None:
                for old in self.data["calls"]:
                    if not old["name"].startswith("semantic_claims_") or old.get("repair_of") or old["status"] != "received":
                        continue
                    repairs = [c for c in self.data["calls"] if c.get("repair_of") == old["id"] and c["status"] == "received"]
                    if len(repairs) != 1 or any((self.output / "calls" / c["id"] / "parsed.json").is_file() for c in [old, *repairs]):
                        continue
                    prior = old_claim_input(_read(self.output / "calls" / old["id"] / "request.json").get("arguments", {}).get("prompt"))
                    _require(prior is None or comparison_fingerprint(*prior) != comparison_fingerprint(*current),
                             "known_exhausted_claim_input_no_third_comparison")

    @_serialized
    def begin_call(self, name, request, *, repair_of=None):
        record = self.assert_protected()
        with file_lock(self.lock_path):
            self._reload()
            parallel = _parallel_execution(self.output, self.data, record)
            added = self.data["calls"][record["baseline_request_count"]:]
            parent = _stage(name)[0]
            recovery = _independent_recovery(self.output, self.data, record)
            from .independent_slot_recovery import admissible_status, check_next_stage
            _require(not any(not admissible_status(recovery, c) for c in added)
                     and not any(c["status"] == "submitted" and (not parallel or _stage(c["name"])[0] == parent) for c in added),
                     "new_or_pending_outcome_unknown")
            names = _validate_added(self.output, self.data["calls"], record)
            stops = _parent_stops(self.output, self.data, record)
            bound = _request_bound_parent_facts(self.output, self.data, record)
            navigation = _parent_point_navigation(self.output, self.data, record, parent)
            assembly = _request_bound_assembly(self.output, self.data, record)
            timing = _request_bound_proposal_retiming(self.output, self.data, record)
            feedback = _source_feedback(self.output, self.data, record)
            if recovery:
                check_next_stage(recovery, self.data, parent, name)
            resumed = any(row is not None and row['parent_round'] == parent and parent in stops
                          and stops[parent]['repair_call_id'] == row['repair_call_id'] for row in (bound, navigation, assembly, timing)
                          if not any(self.data['artifacts'].get(f'sf_parent_protocol_stop_{parent}' + suffix)
                                     for suffix in ('_assembly_bound_resume', '_timing_bound_resume', '_source_feedback_resume', '_independent_resume')))
            _require(parent not in stops or (parallel is not None and parent == 0
                     and stops[parent]['original_call_id'] == parallel['failed_call_ids'][0]) or resumed,
                     'stopped_parent_cannot_repeat')
            own_names = [n for n in names if not parallel or _stage(n)[0] == parent]
            if own_names:
                previous = names[own_names[-1]]
                if name != previous["name"] + "_repair":
                    independent = any(row['repair_call_id'] == previous['id']
                        and row['request_count'] == len(self.data['calls'])
                        and row['independent_parent_round'] is not None
                        and name == f"sf_{row['independent_parent_round']}_outline" for row in stops.values())
                    replacement = parallel is not None and name == REPLACEMENT_OUTLINE and previous["id"] in parallel["failed_call_ids"]
                    resolved = bound is not None and previous['id'] == bound['repair_call_id'] and name == bound['next_stage']
                    point_resolved = navigation is not None and previous['id'] == navigation['repair_call_id'] and name == navigation['next_stage']
                    assembly_resolved = assembly is not None and previous['id'] == assembly['repair_call_id'] and name == \
                        (feedback['stage'] if feedback is not None else assembly['next_stage'])
                    timing_resolved = timing is not None and previous['id'] == timing['repair_call_id'] and name == timing['next_stage']
                    local_resolved = recovery is not None and parent == 3 and previous['name'] == SOURCE_FEEDBACK_STAGE and \
                        previous['status'] == 'uncertain' and name == LOCAL_REPLAN_STAGE
                    _require((self.output / "calls" / previous["id"] / "parsed.json").is_file() or independent or replacement or resolved or point_resolved or assembly_resolved or timing_resolved or local_resolved,
                             "previous_stage_protocol_failure_no_new_loop")
            self._progress(name, names, repair_of)
            self._check_input(name, request, record)
            if name.endswith("_repair"):
                original = names[name[:-7]]
                original_request = _read(self.output / "calls" / original["id"] / "request.json")
                _require(request["media_sha256"] == original_request["media_sha256"]
                         and scope_fingerprint(request["observation_scope"]) ==
                             scope_fingerprint(original_request["observation_scope"]), "repair_media_or_scope_changed")
            digest = json_sha(request)
            _require(not any(c["request_sha256"] == digest for c in self.data["calls"]), "request_already_recorded")
            parent, kind, _ = _stage(name)
            if kind in {"facts", "slice"} and not name.endswith("_repair"):
                _require(sum(_stage(c["name"])[:2] == (parent, kind) and not c.get("repair_of")
                             for c in names.values()) < record["max_slices_per_parent"], "slice_limit_exceeded")
            count = self.data["request_count"] + 1
            call_id = f"glm_{count:03d}_{name}"
            folder = self.output / "calls" / call_id
            folder.mkdir(parents=True, exist_ok=False)
            write_json(folder / "request.json", request)
            ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            call = {"id": call_id, "name": name, "status": "submitted", "submitted_at": _timestamp(),
                    "request_sha256": digest, "usage": {}, "repair_of": ident}
            self.data["request_count"] = count
            self.data["calls"].append(call)
            self._save()
            return deepcopy(call), folder

    def _new(self, call):
        record = self.assert_protected()
        ident = call["id"] if isinstance(call, dict) else call
        _require(any(c["id"] == ident for c in self.data["calls"][record["baseline_request_count"]:]),
                 "historical_call_read_only")

    @_serialized
    def complete_call(self, call, response, *, usage=None):
        self._new(call)
        return super().complete_call(call, response, usage=usage)

    @_serialized
    def fail_call(self, call, error, *, uncertain=True):
        self._new(call)
        return super().fail_call(call, error, uncertain=uncertain)

    @_serialized
    def reconcile_received(self, call, response, *, evidence, usage=None):
        self._new(call)
        return super().reconcile_received(call, response, evidence=evidence, usage=usage)

    @_serialized
    def reclassify_uncertain(self, call, *, evidence):
        self._new(call)
        return super().reclassify_uncertain(call, evidence=evidence)

    def enable_independent_continuation(self):
        raise LibraryStopped("slot_finecut:continuation_policy_is_bound_use_separate_authorization")

    @_serialized
    def set_artifact(self, name, payload):
        record = self.assert_protected()
        _require(name not in record["baseline_artifact_names"] and name != ARTIFACT
                 and isinstance(name, str) and name.startswith("sf_"), "historical_or_unknown_artifact_read_only")
        _require(not self.data["artifacts"].get(name), "artifact_already_recorded")
        return super().set_artifact(name, payload)

    @_serialized
    def claim_render(self, parent):
        """Bind source checks before the sole render; reuse the same cache claim."""
        from .research_readability import source_counterevidence
        _require(type(parent) is int and parent in (0, 3), "unknown_parent")
        record = self.assert_protected()
        names = _validate_added(self.output, self.data["calls"], record)
        self._received(names, f"sf_{parent}_assemble")
        parallel = _parallel_execution(self.output, self.data, record)
        recovery = _independent_recovery(self.output, self.data, record)
        from .independent_slot_recovery import admissible_status
        _require(not any(not admissible_status(recovery, c) or
                 (c["status"] == "submitted" and (not parallel or _stage(c["name"])[0] == parent))
                 for c in self.data["calls"][record["baseline_request_count"]:]), "new_or_pending_outcome_unknown")
        artifact = f"sf_{parent}_render_claim"
        folder = Path(record["authorization_path"]).parent / f"render_{parent}"
        directory = folder / "render"
        assembly_path, manifest_path = folder / "assembly.json", folder / "source_manifest.json"
        _require(assembly_path.is_file() and manifest_path.is_file(), "source_manifest_required")
        assembly, manifest = _read(assembly_path), _read(manifest_path)
        _require(manifest["plan_sha256"] == json_sha(assembly["plan"])
                 and len(manifest["observations"]) == len(manifest["segment_checks"]) == len(assembly["plan"]["segments"])
                 and not source_counterevidence(manifest)
                 and isinstance(manifest.get("timing_counterevidence"), list) and not manifest["timing_counterevidence"],
                 "source_counterevidence_or_missing_checks")
        feedback = _source_feedback(self.output, self.data, record) if parent == 3 else None
        if parent == 3 and recovery is not None:
            from .slot_local_replan import replacement_assembly
            received_assembly = replacement_assembly(self._received(names, LOCAL_REPLAN_STAGE), feedback)
        else:
            received_assembly = (self._received(names, feedback['stage'])['replacement_assembly'] if feedback is not None
                                 else self._received(names, f"sf_{parent}_assemble"))
        _require(received_assembly == assembly, "assembly_differs_from_model_reply")
        for segment in assembly["plan"]["segments"]:
            observation = next((v for v in manifest["observations"] if v["segment_id"] == segment["segment_id"]), None)
            _require(observation is not None and all(observation[k] == segment[k]
                     for k in ("source_id", "source_in_s", "source_out_s")), "source_manifest_segment_changed")
            key = json_sha({"segment": segment["segment_id"], "sha": observation["source_sha256"],
                            "in": segment["source_in_s"], "out": segment["source_out_s"]})[:16]
            recorded_observation = self._received(names, f"semantic_slice_{20 if parent == 0 else 21}_{key}")
            recorded_check = self._received(names, f"semantic_claims_{20 if parent == 0 else 21}_{key}")
            checked = next((v for v in manifest["segment_checks"] if v["segment_id"] == segment["segment_id"]), None)
            _require(recorded_observation == observation and recorded_check == checked,
                     "source_manifest_differs_from_model_replies")
        payload = {"policy": POLICY, "parent_round": parent, "directory": str(directory),
                   "preparation_id": record["preparation_id"], "max_renders": 1,
                   "assembly_path": str(assembly_path), "assembly_sha256": sha256_file(assembly_path),
                   "source_manifest_path": str(manifest_path), "source_manifest_sha256": sha256_file(manifest_path)}
        if self.data["artifacts"].get(artifact):
            _require(_read(self.data["artifacts"][artifact][0]["path"]) == payload, "render_claim_changed")
            return directory
        _require(not directory.exists(), "render_exists_before_claim")
        self.set_artifact(artifact, payload)
        return directory

    @_serialized
    def usage(self):
        record = self.assert_protected()
        value = super().usage()
        return {**value, "max_requests": None, "base_max_requests": record["base_request_limit"],
                "effective_request_limit": None, "additional_requests": None,
                "slot_finecut_baseline_requests": record["baseline_request_count"],
                "request_limit_policy": REQUEST_LIMIT_POLICY}
