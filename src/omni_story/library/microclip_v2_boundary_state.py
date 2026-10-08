"""Append-only, same-event boundary navigation after a known semantic stop.

The old adapter is verified exactly once when recording this permission. Later
work validates the frozen 231-call prefix without teaching old retry grants to
accept more requests. No model facts or creative decisions are repaired here.
"""
from copy import deepcopy
from pathlib import Path
import re

from .forward_slot_budget import _history, _prefix_sha
from .pipeline import _read
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, json_sha, scope_fingerprint

ARTIFACT = "mc2_boundary_navigation_resume"
POLICY = "microclip_v2_boundary_navigation_resume_v1"
BASELINE = 231
STAGES = r"^mc2_edge_([0-9]|1[01])_(nav|confirm)_([0-3])(_repair)?$|^mc2_edge_([0-9]|1[01])_view_([0-2])_([0-3])(_repair)?$"


def require(condition, reason):
    if not condition:
        raise LibraryStopped("microclip_v2_boundary:" + reason)


def stage(name):
    match = re.fullmatch(STAGES, name)
    if not match:
        return None
    if match[1] is not None:
        return int(match[1]), match[2], int(match[3]), None, bool(match[4])
    return int(match[5]), "view", int(match[6]), int(match[7]), bool(match[8])


def _files(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def _parsed(output, call):
    from .contracts import parse_model_json
    require(call and call["status"] == "received", "received_predecessor_required")
    folder = output / "calls" / call["id"]
    value = _read(folder / "parsed.json")
    raw = "\n".join(row["text"] for row in _read(folder / "response.json")["result"]["content"] if row.get("type") == "text")
    require(not (folder / "protocol_failure.json").exists() and parse_model_json(raw) == value,
            "parsed_not_original_reply")
    return value


def blocked_call(output, calls, index):
    stem = f"mc2_edge_{index}"
    call = next((c for c in reversed(calls) if c["name"] in (stem, stem + "_repair")), None)
    value = _parsed(output, call)
    require(value.get("status") == "blocked" and value.get("blocking_questions"), "original_semantic_block_required")
    return call, value


def _resume_proof(output, data, value, *, force=False):
    base = value["base_authorization"]
    require(value["policy"] == POLICY and value["task_id"] == data["task_id"]
            and value["baseline_request_count"] == BASELINE
            and data["request_count"] == len(data["calls"]) >= BASELINE
            and value["prefix_calls_sha256"] == json_sha(data["calls"][:BASELINE])
            and value["input_lock_sha256"] == json_sha(data["input_lock"])
            and value["base_policy_version"] == data["policy_version"]
            and value["base_request_limit"] == data["max_requests"]
            and value["new_renders"] == 0 and value["reuse_original_unused_render"] is True
            and value["goal_resumed"] is value["automatic_round_loops"] is False
            and value["numeric_total_request_limit"] is None and value["stage_pattern"] == STAGES
            and value["first_stage"] == "mc2_edge_1_nav_0"
            and isinstance(value["user_instruction"], str) and value["user_instruction"].strip(), "permission_or_prefix_changed")
    require(sha256_file(base["authorization_path"], force=force) == base["authorization_sha256"]
            and sha256_file(base["parent"]["path"], force=force) == base["parent"]["sha256"]
            and base["slot"] in _read(base["outline_path"])["slots"]
            and sha256_file(value["knowledge_path"], force=force) == value["knowledge_sha256"], "scope_or_knowledge_changed")
    for key, old in value["baseline_artifacts"].items():
        require(data["artifacts"].get(key) == old, "historical_artifact_changed")
    for ident, old in value["baseline_call_files"].items():
        require(_files(output / "calls" / ident) == old, "historical_call_files_changed")
    for row in value["protected_files"]:
        require(sha256_file(row["path"], force=force) == row["sha256"], "historical_bytes_changed")
    for row in value["journal_prefixes"]:
        require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "historical_journal_changed")
    for lost in base["unknown_inputs"]:
        require(not any((output / "calls" / lost["call_id"] / p).exists() for p in ("response.json", "parsed.json")), "unknown_reply_fabricated")
    require(sorted(d.name for d in output.glob("render_*") if d.is_dir()) == base["baseline_render_directories"], "unapproved_legacy_render")
    require(not Path(base["allowed_render_directory"]).exists() or data["artifacts"].get("mc2_render_claim"), "render_without_claim")
    return base


def load_boundary_auth(output, data=None, *, force=False):
    output = Path(output).resolve(strict=True)
    data = data or _read(output / "library_state.json")
    rows = data["artifacts"].get(ARTIFACT, [])
    require(len(rows) == 1, "one_permission_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    value = _read(path)
    require(path.is_relative_to(output / "artifacts") and json_sha(value) == rows[0]["sha256"], "permission_artifact_changed")
    base = _resume_proof(output, data, value, force=force)
    from .microclip_v2_state import _stage
    names, pending = {}, 0
    for call in data["calls"][BASELINE:]:
        navigation = stage(call["name"])
        repair = navigation[-1] if navigation else _stage(call["name"])[1]
        require(call["name"] not in names and call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "new_stage_or_status_invalid")
        request = _read(output / "calls" / call["id"] / "request.json")
        require(json_sha(request) == call["request_sha256"], "new_request_changed")
        if repair:
            old = names.get(call["name"][:-7])
            require(old and old["status"] == "received" and list(names)[-1] == old["name"]
                    and call.get("repair_of") == old["id"], "sole_repair_binding")
            previous = _read(output / "calls" / old["id"] / "request.json")
            require(previous["media_sha256"] == request["media_sha256"]
                    and scope_fingerprint(previous["observation_scope"]) == scope_fingerprint(request["observation_scope"]), "repair_input_changed")
        else:
            require(not call.get("repair_of"), "original_has_repair")
        if call["status"] == "received":
            require(json_sha(_read(output / "calls" / call["id"] / "response.json")) == call["response_sha256"], "new_response_changed")
        pending += call["status"] == "submitted"
        names[call["name"]] = call
    require(pending <= 1, "single_lane_required")
    for key, entries in data["artifacts"].items():
        if not key.startswith("mc2_"):
            continue
        require(len(entries) == 1 and json_sha(_read(entries[0]["path"])) == entries[0]["sha256"], "artifact_changed")
        artifact = _read(entries[0]["path"])
        if key.startswith("mc2_input_"):
            require(sha256_file(artifact["media_path"], force=force) == artifact["media_sha256"]
                    and sha256_file(artifact["lineage_path"], force=force) == artifact["lineage_sha256"], "input_bytes_changed")
        for proof in artifact.get("completed_files", []):
            require(sha256_file(proof["path"], force=force) == proof["sha256"], "completed_bytes_changed")
        if key.startswith("mc2_effective_edge_"):
            effective_edge(output, data, int(key.rsplit("_", 1)[-1]))
        if key.startswith("mc2_boundary_page_plan_"):
            page_plan_proof(output, data, base, artifact, force=force)
    runtime = {**deepcopy(base), "boundary_resume": value}
    from .microclip_v2_boundary_metadata import load_metadata_resume
    runtime["boundary_metadata_resume"] = load_metadata_resume(output, data, runtime, force=force)
    return runtime


def record_boundary_resume(output, user_instruction, knowledge_path):
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(ARTIFACT):
        return load_boundary_auth(output, data, force=True)
    from .microclip_v2_state import get_auth
    base = get_auth(output, force=True)
    require(data["request_count"] == len(data["calls"]) == BASELINE
            and data["artifacts"].get("mc2_dispatch_result") and not data["artifacts"].get("mc2_render_claim")
            and not Path(base["allowed_render_directory"]).exists()
            and all(c["status"] in {"received", "uncertain", "failed_known"} for c in data["calls"]), "settled_unused_trial_required")
    blocked, _ = blocked_call(output, data["calls"], 1)
    require(blocked["id"] == "glm_231_mc2_edge_1", "known_boundary_stop_required")
    receipt = _read(data["artifacts"]["mc2_dispatch_result"][0]["path"])
    result = _read(receipt["result_path"])
    require(result["status"] == "stopped" and result["error"].startswith("boundary_missing_essential_evidence:")
            and result["final_video"] is None and result["model_quality_gate_passed"] is False,
            "known_boundary_stop_required")
    require(isinstance(user_instruction, str) and user_instruction.strip(), "explicit_instruction_required")
    original = Path(knowledge_path).resolve(strict=True)
    knowledge = Path(base["execution_directory"]) / "boundary_navigation" / "knowledge.md"
    require(not knowledge.exists() or knowledge.read_bytes() == original.read_bytes(), "knowledge_already_bound")
    knowledge.parent.mkdir(parents=True, exist_ok=True)
    if not knowledge.exists():
        knowledge.write_bytes(original.read_bytes())
    files, prefixes = _history(output)
    proofs = {row["path"]: row for row in files}
    for row in base["protected_files"]:
        proofs.setdefault(row["path"], row)
    payload = {"policy": POLICY, "task_id": data["task_id"], "baseline_request_count": BASELINE,
               "prefix_calls_sha256": json_sha(data["calls"]), "input_lock_sha256": json_sha(data["input_lock"]),
               "base_policy_version": data["policy_version"], "base_request_limit": data["max_requests"],
               "base_authorization": deepcopy(base), "baseline_artifacts": deepcopy(data["artifacts"]),
               "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"]},
               "protected_files": sorted(proofs.values(), key=lambda row: row["path"]), "journal_prefixes": prefixes,
               "original_blocked_call_id": blocked["id"], "first_stage": "mc2_edge_1_nav_0",
               "knowledge_path": str(knowledge), "knowledge_sha256": sha256_file(knowledge),
               "original_knowledge_path": str(original), "original_knowledge_sha256": sha256_file(original),
               "new_renders": 0, "reuse_original_unused_render": True, "goal_resumed": False,
               "automatic_round_loops": False, "numeric_total_request_limit": None,
               "stage_pattern": STAGES, "user_instruction": user_instruction}
    LibraryState(output, data["input_lock"], max_requests=data["max_requests"]).set_artifact(ARTIFACT, payload)
    return load_boundary_auth(output, force=True)


def effective_edge(output, data, index):
    rows = data["artifacts"].get(f"mc2_effective_edge_{index}", [])
    if not rows:
        return None
    require(len(rows) == 1, "one_effective_edge_required")
    receipt = _read(rows[0]["path"])
    require(receipt["policy"] == POLICY and receipt["edge_index"] == index
            and receipt["event_index"] == index // 2 and json_sha(receipt) == rows[0]["sha256"], "effective_edge_binding_changed")
    call = next((c for c in data["calls"][BASELINE:] if c["id"] == receipt["confirmed_call_id"]), None)
    parsed = _parsed(output, call)
    navigation = stage(call["name"])
    require(navigation and navigation[0] == index and navigation[1] == "confirm", "confirmed_navigation_required")
    original, _ = blocked_call(output, data["calls"], index)
    request = _read(output / "calls" / call["id"] / "request.json")
    descriptor_rows = data["artifacts"].get("mc2_input_" + call["name"].removesuffix("_repair"), [])
    require(len(descriptor_rows) == 1 and receipt["input_descriptor_sha256"] == descriptor_rows[0]["sha256"]
            and receipt["original_blocked_call_id"] == original["id"]
            and receipt["model_response_sha256"] == call["response_sha256"]
            and receipt["parsed_sha256"] == json_sha(parsed) and parsed.get("action") == "confirm"
            and parsed.get("confirmed_frame_id") == receipt["confirmed_frame_id"]
            and not parsed.get("blocking_questions") and request["tool"] == "analyze_image", "effective_reply_changed")
    descriptor = _read(descriptor_rows[0]["path"])
    anchors = next((c for c in reversed(data["calls"]) if c["name"] in ("mc2_anchors", "mc2_anchors_repair")), None)
    event = _parsed(output, anchors)["events"][index // 2]
    require(parsed.get("event_id") == event["event_id"]
            and parsed.get("anchor_frame_id") == event[("start", "end")[index % 2] + "_frame_id"]
            and set(parsed.get("obligation_ids", [])) == set(event["obligation_ids"])
            and any(f["frame_id"] == parsed["confirmed_frame_id"] for f in _read(descriptor["lineage_path"])["frames"]),
            "effective_event_or_candidate_changed")
    return call, {**parsed, "status": "confirmed"}, descriptor


def record_effective_edge(state, index, confirmed_call, confirmed_result=None):
    state.assert_protected()
    raw = _parsed(state.output, confirmed_call)
    require(confirmed_result is None or raw == confirmed_result, "effective_reply_not_original")
    original, _ = blocked_call(state.output, state.data["calls"], index)
    navigation = stage(confirmed_call["name"])
    require(navigation and navigation[0] == index and navigation[1] == "confirm"
            and raw.get("action") == "confirm" and raw.get("confirmed_frame_id") and not raw.get("blocking_questions"), "confirmed_navigation_required")
    descriptor = state.data["artifacts"]["mc2_input_" + confirmed_call["name"].removesuffix("_repair")][0]
    event = state._events()[index // 2]
    require(raw.get("event_id") == event["event_id"]
            and raw.get("anchor_frame_id") == event[("start", "end")[index % 2] + "_frame_id"]
            and set(raw.get("obligation_ids", [])) == set(event["obligation_ids"])
            and any(f["frame_id"] == raw["confirmed_frame_id"] for f in _read(_read(descriptor["path"])["lineage_path"])["frames"]),
            "effective_event_or_candidate_changed")
    payload = {"policy": POLICY, "edge_index": index, "event_index": index // 2,
               "original_blocked_call_id": original["id"], "confirmed_call_id": confirmed_call["id"],
               "model_response_sha256": confirmed_call["response_sha256"], "parsed_sha256": json_sha(raw),
               "confirmed_frame_id": raw["confirmed_frame_id"], "input_descriptor_sha256": descriptor["sha256"]}
    return state.set_artifact(f"mc2_effective_edge_{index}", payload)


def predecessor(state, name):
    navigation = stage(name)
    require(navigation is not None and state.authorization.get("boundary_resume"), "permission_required")
    index, phase, round_index, page, _ = navigation
    original, _ = blocked_call(state.output, state.data["calls"], index)
    require(index < 2 * len(state._events()) and not state.data["artifacts"].get(f"mc2_effective_edge_{index}"), "same_unresolved_event_required")
    if phase == "nav":
        if round_index:
            rows = state.data["artifacts"].get(f"mc2_boundary_page_plan_{index}_{round_index - 1}", [])
            require(len(rows) == 1, "new_evidence_need_required")
            plan = _read(rows[0]["path"])
            require(plan.get("new_frame_ids"), "new_evidence_need_required")
            for i in range(len(plan["pages"])):
                state._received(f"mc2_edge_{index}_view_{round_index - 1}_{i}")
    elif phase == "view":
        rows = state.data["artifacts"].get(f"mc2_boundary_page_plan_{index}_{round_index}", [])
        require(len(rows) == 1, "bound_page_plan_required")
        plan = _read(rows[0]["path"])
        call, decision = state._received(plan["decision_stage"])
        require(decision.get("action") == "observe", "model_requested_observation_required")
        if page:
            state._received(f"mc2_edge_{index}_view_{round_index}_{page - 1}")
    else:
        _, decision = state._received(f"mc2_edge_{index}_nav_{round_index}")
        require(decision.get("action") == "confirm", "model_selected_candidate_required")


def page_plan_proof(output, data, auth, plan, *, force=False):
    """Only newly observed presentation frames count, never prepared media."""
    from .microclip_v2_state import _verify_grid
    decision_index = next((i for i, c in enumerate(data["calls"]) if c["id"] == plan.get("decision_call_id")), None)
    require(decision_index is not None, "page_decision_call_required")
    decision_call = data["calls"][decision_index]
    navigation = stage(decision_call["name"])
    decision = _parsed(output, decision_call)
    require(navigation and navigation[1] in {"nav", "confirm"} and navigation[2] < 3
            and decision_call["name"].removesuffix("_repair") == plan["decision_stage"]
            and decision.get("action") == "observe"
            and plan["source_start_s"] == decision["source_start_s"]
            and plan["source_end_s"] == decision["source_end_s"]
            and auth["slot"]["start_s"] <= plan["source_start_s"] < plan["source_end_s"] <= auth["slot"]["end_s"]
            and plan["source_end_s"] - plan["source_start_s"] <= .600001
            and 1 <= len(plan["pages"]) <= 4, "page_decision_scope_changed")
    observed = set()
    for call in data["calls"][:decision_index + 1]:
        folder = output / "calls" / call["id"]
        rows = data["artifacts"].get("mc2_input_" + call["name"].removesuffix("_repair"), [])
        if call["status"] != "received" or not rows or not (folder / "parsed.json").exists() or (folder / "protocol_failure.json").exists():
            continue
        d = _read(rows[0]["path"])
        if d["tool"] == "analyze_image" and d["observation_scope"]["source_sha256"] == auth["parent"]["sha256"]:
            observed.update(f["frame_id"] for f in _read(d["lineage_path"])["frames"])
    continuous = {}
    for row in plan["pages"]:
        manifest = _verify_grid(row["manifest_path"], source_path=auth["parent"]["path"], force=force)
        require(len(manifest["frames"]) <= 6, "six_frames_per_page_required")
        for frame in manifest["frames"]:
            if plan["source_start_s"] <= frame["source_time_s"] < plan["source_end_s"]:
                continuous[frame["frame_id"]] = frame
    frames = sorted(continuous.values(), key=lambda row: row["source_time_s"])
    new = set(continuous) - observed
    require(frames and new and set(plan["new_frame_ids"]) == new
            and all(b["decode_frame_index"] == a["decode_frame_index"] + 1 for a, b in zip(frames, frames[1:])),
            "actual_new_continuous_frames_required")
    return plan


def check_input(state, name, request):
    """Additional same-event constraints; the caller verifies media/grid bytes."""
    index, phase, round_index, page, _ = stage(name)
    d = _read(state.data["artifacts"]["mc2_input_" + name.removesuffix("_repair")][0]["path"])
    event = state._events()[index // 2]
    require(d.get("edge_index") == index and d.get("event_id") == event["event_id"]
            and d.get("anchor_key") == ("start", "end")[index % 2]
            and d.get("boundary_round") == round_index and d.get("phase") == phase, "same_event_descriptor_required")
    if phase == "view":
        nav_call, _ = state._received(f"mc2_edge_{index}_nav_{round_index}")
        rows = state.data["artifacts"].get(f"mc2_boundary_page_plan_{index}_{round_index}", [])
        require(len(rows) == 1, "bound_page_plan_required")
        plan = page_plan_proof(state.output, state.data, state.authorization, _read(rows[0]["path"]))
        call, decision = state._received(plan["decision_stage"])
        require(plan["decision_stage"] in (f"mc2_edge_{index}_nav_{round_index}", f"mc2_edge_{index}_confirm_{round_index}")
                and plan.get("decision_call_id") == call["id"] and plan.get("nav_call_id") == nav_call["id"]
                and d.get("nav_call_id") == nav_call["id"]
                and d.get("page_index") == page and page < len(plan["pages"])
                and d.get("lineage_path") == plan["pages"][page]["manifest_path"]
                and 1 <= len(plan["pages"]) <= 4 and plan.get("new_frame_ids")
                and plan.get("source_start_s") == decision.get("source_start_s")
                and plan.get("source_end_s") == decision.get("source_end_s")
                and 0 < plan["source_end_s"] - plan["source_start_s"] <= .600001,
                "new_model_requested_frames_required")
    elif phase == "confirm":
        call, decision = state._received(f"mc2_edge_{index}_nav_{round_index}")
        frames = _read(d["lineage_path"])["frames"]
        candidate = next((f for f in frames if f["frame_id"] == decision.get("confirmed_frame_id")), None)
        scope = scope_fingerprint(request["observation_scope"])
        slot = state.authorization["slot"]
        require(d.get("nav_call_id") == call["id"] and candidate
                and d.get("selected_frame_id") == candidate["frame_id"]
                and max(slot["start_s"], candidate["source_time_s"] - .25) <= scope[2] <= candidate["source_time_s"] < scope[3]
                <= min(slot["end_s"], candidate["source_time_s"] + .25) + 1e-6,
                "confirmation_observation_binding_required")
    elif round_index:
        previous = _read(state.data["artifacts"][f"mc2_boundary_page_plan_{index}_{round_index - 1}"][0]["path"])
        last = previous["pages"][-1]["manifest_path"]
        require(d["lineage_path"] == last, "navigation_must_reuse_received_page")
    else:
        original = _read(state.data["artifacts"][f"mc2_input_mc2_edge_{index}"][0]["path"])
        require(d["lineage_path"] == original["lineage_path"], "first_navigation_reuses_blocked_evidence")
