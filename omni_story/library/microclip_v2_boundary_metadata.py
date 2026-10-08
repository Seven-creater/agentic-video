"""One known repair's inactive observation fields, kept outside its decision.

The received 238 reply already selects a real candidate. Its sole protocol
failure is carrying the exact earlier 232 observation range alongside confirm.
This projection preserves every other field, every raw reply and old failure;
it never establishes a confirmed boundary or authorizes another navigation.
"""
from copy import deepcopy
from pathlib import Path

from .forward_slot_budget import _history, _prefix_sha
from .pipeline import _read
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, json_sha

ARTIFACT = "mc2_boundary_inactive_range_resume"
POLICY = "microclip_v2_boundary_inactive_range_projection_v1"
BASELINE = 238
CALL_ID = "glm_238_mc2_edge_1_nav_1_repair"
ORIGINAL_ID = "glm_237_mc2_edge_1_nav_1"
PRIOR_ID = "glm_232_mc2_edge_1_nav_0"
STEM = "mc2_edge_1_nav_1"
NEXT_STAGE = "mc2_edge_1_confirm_1"
ERROR = "microclip_v2:boundary_confirm_has_no_observation_envelope"


def _require(ok, reason):
    if not ok:
        raise LibraryStopped("microclip_v2_boundary_metadata:" + reason)


def _files(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def _raw(output, call):
    from .contracts import parse_model_json
    folder = output / "calls" / call["id"]
    response = _read(folder / "response.json")
    _require(call["status"] == "received" and json_sha(response) == call["response_sha256"], "known_received_reply_required")
    text = "\n".join(row["text"] for row in response["result"]["content"] if row.get("type") == "text")
    return parse_model_json(text), text


def _projection(output, data, auth):
    from .microclip_v2_boundary_state import _parsed
    from .microclip_v2_boundary_contracts import validate_navigation
    _require(len(data["calls"]) >= BASELINE, "known_238_required")
    original, call, prior = data["calls"][236], data["calls"][237], data["calls"][231]
    _require(original["id"] == ORIGINAL_ID and original["name"] == STEM and not original.get("repair_of")
             and call["id"] == CALL_ID and call["name"] == STEM + "_repair"
             and call.get("repair_of") == ORIGINAL_ID and prior["id"] == PRIOR_ID,
             "sole_known_repair_binding")
    raw, text = _raw(output, call)
    original_raw, original_text = _raw(output, original)
    for item, raw_text, attempt in ((original, original_text, 0), (call, text, 1)):
        folder = output / "calls" / item["id"]
        _require(not (folder / "parsed.json").exists(), "historical_failure_cannot_be_relabelled")
        failure = _read(folder / "protocol_failure.json")
        _require(failure.get("error") == ERROR and failure.get("attempt") == attempt
                 and failure.get("model_text") == raw_text, "only_inactive_range_failure_allowed")
    _require(original_raw.get("action") == raw.get("action") == "confirm"
             and original_raw.get("source_start_s") == original_raw.get("source_end_s") == 0
             and raw.get("source_start_s") is not None and raw.get("source_end_s") is not None
             and not raw.get("blocking_questions"), "known_confirm_projection_only")
    observed = _parsed(output, prior)
    _require(observed.get("action") == "observe"
             and raw["source_start_s"] == observed["source_start_s"]
             and raw["source_end_s"] == observed["source_end_s"], "range_must_equal_completed_observation")
    plan_rows = data["artifacts"].get("mc2_boundary_page_plan_1_0", [])
    _require(len(plan_rows) == 1, "completed_observation_plan_required")
    plan = _read(plan_rows[0]["path"])
    _require(plan["decision_call_id"] == PRIOR_ID and plan["decision_stage"] == prior["name"]
             and plan["source_start_s"] == observed["source_start_s"]
             and plan["source_end_s"] == observed["source_end_s"], "completed_observation_plan_changed")
    catalog = {}
    received_page_stems = set()
    for candidate in data["calls"][:BASELINE]:
        folder = output / "calls" / candidate["id"]
        rows = data["artifacts"].get("mc2_input_" + candidate["name"].removesuffix("_repair"), [])
        if candidate["status"] != "received" or not rows or not (folder / "parsed.json").exists() or (folder / "protocol_failure.json").exists():
            continue
        _parsed(output, candidate)
        descriptor = _read(rows[0]["path"])
        if descriptor["tool"] == "analyze_image" and descriptor["observation_scope"]["source_sha256"] == auth["parent"]["sha256"]:
            catalog.update({f["frame_id"]: f for f in _read(descriptor["lineage_path"])["frames"]})
            received_page_stems.add(candidate["name"].removesuffix("_repair"))
    _require(all(f"mc2_edge_1_view_0_{index}" in received_page_stems for index in range(len(plan["pages"]))),
             "all_observation_pages_received_required")
    from .microclip_v2_boundary_state import blocked_call
    _, previous = blocked_call(output, data["calls"], 1)
    anchor_call = next(c for c in reversed(data["calls"][:BASELINE]) if c["name"] in ("mc2_anchors", "mc2_anchors_repair") and (output / "calls" / c["id"] / "parsed.json").exists())
    intent_call = next(c for c in reversed(data["calls"][:BASELINE]) if c["name"] == "mc2_intent")
    event, intent = _parsed(output, anchor_call)["events"][0], _parsed(output, intent_call)
    projected = deepcopy(raw)
    projected["source_start_s"] = projected["source_end_s"] = None
    # This is the complete current semantic/identity/observed-frame contract.
    # Passing it is model evidence only; a new neighbor confirmation is required.
    validate_navigation(projected, catalog, auth["slot"], event, intent, previous,
                        new_frame_ids=plan["new_frame_ids"])
    context = {"kind": "non_executable_prior_observation", "prior_call_id": PRIOR_ID,
               "prior_response_sha256": prior["response_sha256"], "page_plan_sha256": plan_rows[0]["sha256"],
               "source_start_s": raw["source_start_s"], "source_end_s": raw["source_end_s"]}
    return call, raw, projected, context


def load_metadata_resume(output, data, auth, *, force=False):
    rows = data["artifacts"].get(ARTIFACT, [])
    if not rows:
        return None
    output = Path(output).resolve(strict=True)
    _require(len(rows) == 1 and auth.get("boundary_resume"), "one_bound_metadata_resume_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    value = _read(path)
    _require(path.is_relative_to(output / "artifacts") and json_sha(value) == rows[0]["sha256"]
             and value["policy"] == POLICY and value["task_id"] == data["task_id"]
             and value["baseline_request_count"] == BASELINE
             and len(data["calls"]) == data["request_count"] >= BASELINE
             and value["prefix_calls_sha256"] == json_sha(data["calls"][:BASELINE])
             and value["boundary_authorization_sha256"] == json_sha(auth["boundary_resume"])
             and value["next_stage"] == NEXT_STAGE and value["new_renders"] == 0
             and value["reuse_original_unused_render"] is True and value["goal_resumed"] is False
             and value["automatic_round_loops"] is False and value["numeric_total_request_limit"] is None
             and isinstance(value["user_instruction"], str) and value["user_instruction"].strip(), "permission_or_prefix_changed")
    for key, old in value["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == old, "historical_artifact_changed")
    for ident, old in value["baseline_call_files"].items():
        _require(_files(output / "calls" / ident) == old, "historical_call_files_changed")
    for row in value["protected_files"]:
        _require(sha256_file(row["path"], force=force) == row["sha256"], "historical_bytes_changed")
    for row in value["journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "historical_journal_changed")
    call, raw, projected, context = _projection(output, data, auth)
    _require(value["call_id"] == call["id"] and value["model_response_sha256"] == call["response_sha256"]
             and value["raw_value_sha256"] == json_sha(raw)
             and value["projected_value"] == projected and value["projected_value_sha256"] == json_sha(projected)
             and value["prior_observation_context"] == context, "projection_binding_changed")
    return value


def record_metadata_resume(output, user_instruction):
    """CPU-only compatibility receipt; no paid retry or new render permission."""
    from .microclip_v2_boundary_state import load_boundary_auth
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    auth = load_boundary_auth(output, data, force=True)
    if auth.get("boundary_metadata_resume"):
        return auth
    _require(len(data["calls"]) == data["request_count"] == BASELINE
             and all(c["status"] in {"received", "uncertain", "failed_known"} for c in data["calls"])
             and data["artifacts"].get("mc2_boundary_result") and not data["artifacts"].get("mc2_render_claim")
             and not Path(auth["allowed_render_directory"]).exists()
             and isinstance(user_instruction, str) and user_instruction.strip(), "settled_unused_known_stop_required")
    receipt = _read(data["artifacts"]["mc2_boundary_result"][0]["path"])
    result = _read(receipt["result_path"])
    _require(result.get("status") == "stopped" and result.get("error") == "model_protocol_repair_exhausted:" + STEM
             and result.get("model_quality_gate_passed") is False and result.get("final_video") is None,
             "known_238_protocol_stop_required")
    call, raw, projected, context = _projection(output, data, auth)
    files, prefixes = _history(output)
    proofs = {row["path"]: row for row in files}
    for row in auth["boundary_resume"]["protected_files"]:
        proofs.setdefault(row["path"], row)
    payload = {"policy": POLICY, "task_id": data["task_id"], "baseline_request_count": BASELINE,
               "prefix_calls_sha256": json_sha(data["calls"]), "boundary_authorization_sha256": json_sha(auth["boundary_resume"]),
               "baseline_artifacts": deepcopy(data["artifacts"]),
               "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"]},
               "protected_files": sorted(proofs.values(), key=lambda row: row["path"]), "journal_prefixes": prefixes,
               "call_id": call["id"], "model_response_sha256": call["response_sha256"], "raw_value_sha256": json_sha(raw),
               "projected_value": projected, "projected_value_sha256": json_sha(projected),
               "prior_observation_context": context, "next_stage": NEXT_STAGE, "new_renders": 0,
               "reuse_original_unused_render": True, "goal_resumed": False, "automatic_round_loops": False,
               "numeric_total_request_limit": None, "user_instruction": user_instruction}
    LibraryState(output, data["input_lock"], max_requests=data["max_requests"]).set_artifact(ARTIFACT, payload)
    return load_boundary_auth(output, force=True)


def received_projection(output, data, stem):
    if stem not in {STEM, STEM + "_repair"} or not data["artifacts"].get(ARTIFACT):
        return None
    from .microclip_v2_boundary_state import load_boundary_auth
    auth = load_boundary_auth(output, data)
    value = auth["boundary_metadata_resume"]
    return deepcopy(data["calls"][BASELINE - 1]), deepcopy(value["projected_value"])
