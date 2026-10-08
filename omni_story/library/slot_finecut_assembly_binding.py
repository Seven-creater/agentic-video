"""Forward request binding of one known failed assembly, without creative edits."""
from copy import deepcopy
import json
from pathlib import Path
import re

from .contracts import parse_model_json
from .pipeline import _read
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import json_sha, scope_fingerprint

POLICY = "sf_request_bound_assembly_v1_3"


def _context(prompt):
    values = []
    for match in re.finditer(r"\n\{", prompt):
        try:
            value, _ = json.JSONDecoder().raw_decode(prompt[match.start() + 1:])
        except ValueError:
            continue
        if isinstance(value, dict) and {"parent", "outline", "local_proposals", "response_contract"} <= value.keys():
            values.append(value)
    return values


def binding_inputs(output, data, record, parent=3):
    from .slot_finecut import _view, assembly_prompt, validate_assembly
    from .slot_finecut_budget import _require
    _require(parent == 3, "request_bound_assembly_scope_invalid")
    stage = "sf_3_assemble"
    calls = [c for c in data["calls"][record["baseline_request_count"]:] if c["name"] in {stage, stage + "_repair"}]
    _require(len(calls) == 2 and calls[0]["name"] == stage and calls[1]["name"] == stage + "_repair"
             and not calls[0].get("repair_of") and calls[1].get("repair_of") == calls[0]["id"]
             and all(c["status"] == "received" for c in calls), "request_bound_assembly_known_sole_repair_required")
    preparation = _read(record["preparation_path"])
    selected = next(p for p in preparation["parents"] if p["round"] == parent)
    folder = Path(record["execution_directory"]) / "render_3"
    outline_path, proposals_path = folder / "outline.json", folder / "proposals.json"
    outline, proposals = _read(outline_path), _read(proposals_path)
    catalog_path = Path(output) / "catalog/inventory.json"
    catalog, reference, methods = _read(catalog_path), preparation["reference"], _read(record["reference_methods_path"])
    files, requests, responses = [], [], []
    for attempt, call in enumerate(calls):
        call_folder = Path(output) / "calls" / call["id"]
        _require(not (call_folder / "parsed.json").exists(), "request_bound_assembly_cannot_relabel_old_reply")
        request, response, failure = (_read(call_folder / name) for name in
                                      ("request.json", "response.json", "protocol_failure.json"))
        _require(json_sha(request) == call["request_sha256"] and json_sha(response) == call["response_sha256"]
                 and failure["attempt"] == attempt and isinstance(failure["error"], str),
                 "request_bound_assembly_original_failure_changed")
        raw = "\n".join(c["text"] for c in response["result"]["content"] if c.get("type") == "text")
        if attempt == 0:
            try:
                parse_model_json(raw)
            except (ValueError, json.JSONDecodeError):
                pass
            else:
                _require(False, "request_bound_assembly_original_must_remain_syntax_failure")
            _require("delimiter" in failure["error"], "request_bound_assembly_original_syntax_failure_changed")
        else:
            body = parse_model_json(raw)
            _require(failure["error"] == "slot_finecut:assembly_parent_changed"
                     and "baseline_id" not in body and "parent_sha256" not in body,
                     "request_bound_assembly_only_missing_parent_fields")
        requests.append(request)
        responses.append(response)
        files.extend({"path": str(call_folder / name), "sha256": sha256_file(call_folder / name)}
                     for name in ("request.json", "response.json", "protocol_failure.json"))
    original, repair = requests
    contexts = _context(original["arguments"]["prompt"])
    _require(len(contexts) == 1, "request_bound_assembly_one_original_context_required")
    context = contexts[0]
    additions = {"baseline_id": selected["baseline_id"], "parent_sha256": selected["sha256"]}
    _require(context["parent"] == _view(selected) and context["outline"] == outline
             and context["local_proposals"] == proposals and
             all(context["response_contract"][k] == v for k, v in additions.items()),
             "request_bound_assembly_request_context_changed")
    knowledge = Path(record["knowledge_path"]).read_text(encoding="utf-8")
    expected_prompt = assembly_prompt(selected, outline, proposals, catalog, selected["allowed_windows"], reference, methods, knowledge)
    _require(original["arguments"]["prompt"].startswith(expected_prompt + "\n"),
             "request_bound_assembly_original_prompt_changed")
    media = Path(original["arguments"]["video_source"]).resolve(strict=True)
    lineage_path = media.parent / "lineage.json"
    lineage = _read(lineage_path)
    expected_scope = {"kind": "continuous_window", "source_sha256": selected["sha256"],
                      "source_start_s": 0.0, "source_end_s": selected["duration_s"]}
    _require(original["provider"] == repair["provider"] == "official_vision_mcp_in_codex"
             and original["tool"] == repair["tool"] == "analyze_video"
             and Path(repair["arguments"]["video_source"]).resolve() == media
             and sha256_file(media) == original["media_sha256"] == repair["media_sha256"] == lineage["sha256"]
             and scope_fingerprint(original["observation_scope"]) == scope_fingerprint(repair["observation_scope"])
                 == scope_fingerprint(expected_scope) == scope_fingerprint(lineage)
             and lineage["source_offset_s"] == 0 and abs(lineage["media_duration_s"] - selected["duration_s"]) <= .001
             and lineage["time_mapping"] == "source_time_s = source_offset_s + proxy_time_s",
             "request_bound_assembly_media_changed")
    derived = {**deepcopy(body), **additions}
    validate_assembly(derived, selected, outline, proposals, catalog, selected["allowed_windows"], reference, methods,
                      allow_typographic_target_variants=True)
    stop_key, result_key = "sf_parent_protocol_stop_3_point_navigation_resume", "sf_result_3_point_navigation_resume"
    stop_entries, result_entries = (data["artifacts"].get(k, []) for k in (stop_key, result_key))
    _require(len(stop_entries) == len(result_entries) == 1, "request_bound_assembly_original_stop_required")
    stop_path, receipt_path = Path(stop_entries[0]["path"]), Path(result_entries[0]["path"])
    stop, receipt = _read(stop_path), _read(receipt_path)
    result_path = folder / "result_point_navigation_resume.json"
    result = _read(result_path)
    _require(json_sha(stop) == stop_entries[0]["sha256"] and json_sha(receipt) == result_entries[0]["sha256"]
             and stop["original_call_id"] == calls[0]["id"] and stop["repair_call_id"] == calls[1]["id"]
             and stop["error"] == "model_protocol_repair_exhausted:" + stage
             and Path(receipt["result_path"]).resolve() == result_path.resolve()
             and sha256_file(result_path) == receipt["result_sha256"] and result["status"] == "stopped_protocol_failure"
             and result["stop_receipt"] == stop and result["baseline_id"] == selected["baseline_id"]
             and result["parent_sha256"] == selected["sha256"], "request_bound_assembly_original_stop_changed")
    extra = [Path(record["preparation_path"]), catalog_path, Path(record["reference_methods_path"]),
             Path(record["knowledge_path"]), outline_path, proposals_path, media, lineage_path,
             stop_path, receipt_path, result_path]
    files.extend({"path": str(p), "sha256": sha256_file(p)} for p in extra)
    for item in result["completed_files"]:
        _require(sha256_file(item["path"]) == item["sha256"], "request_bound_assembly_completed_history_changed")
        if not any(Path(row["path"]).resolve() == Path(item["path"]).resolve() for row in files):
            files.append({"path": item["path"], "sha256": item["sha256"]})
    first = derived["plan"]["segments"][0]
    source = next(s for s in catalog["sources"] if s["source_id"] == first["source_id"])
    key = json_sha({"segment": first["segment_id"], "sha": source["sha256"],
                    "in": first["source_in_s"], "out": first["source_out_s"]})[:16]
    return {"parent_round": 3, "stage": stage, "original_call_id": calls[0]["id"],
            "repair_call_id": calls[1]["id"], "chosen_call_id": calls[1]["id"],
            "next_stage": "semantic_slice_21_" + key, "body": derived, "program_field_additions": additions,
            "model_body_sha256": json_sha(body), "media_sha256": original["media_sha256"],
            "observation_scope": expected_scope, "protected_files": files}


def request_bound_assembly(output, data, record):
    from .slot_finecut_budget import _require, _stage
    entries = data["artifacts"].get(POLICY, [])
    if not entries:
        return None
    _require(len(entries) == 1, "one_request_bound_assembly_record_required")
    policy = _read(entries[0]["path"])
    _require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == POLICY
             and policy["task_id"] == data["task_id"] and policy["preparation_id"] == record["preparation_id"]
             and policy["original_contract_status"] == "failed" and policy["old_parsed_created"] is False
             and policy["semantic_truth_established"] is False
             and policy["target_comparison_policy"] == "curly_double_quote_only_v1",
             "request_bound_assembly_policy_changed")
    count = policy["baseline_request_count"]
    _require(type(count) is int and record["baseline_request_count"] <= count <= len(data["calls"])
             and json_sha(data["calls"][:count]) == policy["prefix_calls_sha256"]
             and all(c['status'] == 'received' for c in data['calls'][record['baseline_request_count']:count]),
             "request_bound_assembly_prefix_changed")
    bound = binding_inputs(output, data, record, policy["parent_round"])
    _require(all(policy[k] == v for k, v in bound.items()), "request_bound_assembly_body_or_evidence_changed")
    later = [c for c in data["calls"][count:] if _stage(c["name"])[0] == 3]
    from .slot_source_feedback import read_validate
    feedback = read_validate(output, data, record)
    next_stage = feedback['stage'] if feedback is not None else policy['next_stage']
    _require(not later or later[0]["name"] == next_stage, "request_bound_assembly_resume_stage_changed")
    return policy


def record_request_bound_assembly(state, parent=3):
    from .slot_finecut_budget import _require
    record = state.assert_protected()
    existing = request_bound_assembly(state.output, state.data, record)
    if existing:
        _require(existing["parent_round"] == parent, "request_bound_assembly_cannot_replace")
        return existing
    _require(all(c["status"] == "received" for c in state.data["calls"][record["baseline_request_count"]:]),
             "new_or_pending_outcome_unknown")
    bound = binding_inputs(state.output, state.data, record, parent)
    policy = {"policy": POLICY, "task_id": state.data["task_id"], "preparation_id": record["preparation_id"],
              "baseline_request_count": len(state.data["calls"]), "prefix_calls_sha256": json_sha(state.data["calls"]),
              "original_contract_status": "failed", "old_parsed_created": False, "semantic_truth_established": False,
              "target_comparison_policy": "curly_double_quote_only_v1",
              "protocol_limitations": ["Historical syntax and parent-binding failures remain failed with no parsed backfill.",
                  "Only absent program parent fields are added from the original paid request and actual parent lineage.",
                  "Only curly double quotation marks are ignored when comparing slot targets; words and creative choices are unchanged.",
                  "Original slot obligations remain verbatim for final target review; exact-source and actual-output gates remain required."],
              **bound}
    state.set_artifact(POLICY, policy)
    state.assert_protected()
    return policy
