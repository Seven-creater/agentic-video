"""Append-only continuation from three frozen unknown inputs.

This grants one genuinely local planning task, not a replay of the lost whole
parent task. It makes no model requests, selects no footage, and changes no old
authorization, request, failure, review, or quota record.
"""
from copy import deepcopy
from pathlib import Path
import re

from ..contract import require, text
from .pipeline import _read
from .independent_source_resume import _scope as request_scope
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import json_sha, scope_fingerprint

POLICY = ARTIFACT = "sf_independent_slot_recovery_v1"
BASELINE = 166
LOST_CALL = "glm_166_sf_3_source_feedback_replan_v1"
LOCAL_STAGE = "sf_3_local_source_replan_v2"
LOCAL_REPLAN_STAGE = LOCAL_STAGE
LOCAL_STAGE_PATTERN = r"^sf_3_local_source_replan_v2(?:_repair)?$"
FROZEN_IDS = ["glm_004_coarse_978d5360_01", "glm_131_active_10_draft", LOST_CALL]
OBSERVATION_ARTIFACT = "sf_unknown_166_observation_v1"
RESULT_NAMES = {0: "result_independent_recovery_0.json", 3: "result_independent_recovery_3.json"}
FIRST_STAGES = {0: "sf_0_assemble", 3: LOCAL_STAGE}
_STANDARD = re.compile(r"^(?:sf_(?:0|3)_(?:assemble|blind|economy|review)|semantic_(?:slice|claims)_(?:20|21)_[a-f0-9]{16})(?:_repair)?$")


def _require(condition, reason):
    require(condition, "independent_slot_recovery:" + reason)


def stage_parent(name):
    _require(isinstance(name, str) and (_STANDARD.fullmatch(name) or re.fullmatch(LOCAL_STAGE_PATTERN, name)),
             "stage_not_authorized")
    pieces = name.split("_")
    return int(pieces[1]) if pieces[0] == "sf" else {20: 0, 21: 3}[int(pieces[2])]


def _proof(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha256_file(path)}


def _bound_inputs(output, data, grant):
    output = Path(output).resolve(strict=True)
    _require(data["request_count"] == len(data["calls"]) == BASELINE, "activation_baseline_must_be_166")
    unknowns = [c for c in data["calls"] if c["status"] == "uncertain"]
    _require([c["id"] for c in unknowns] == FROZEN_IDS and
             all(c["status"] in {"received", "uncertain"} for c in data["calls"]),
             "unexpected_unknown_or_pending_baseline")
    _require(data["calls"][-1]["id"] == LOST_CALL and data["calls"][-1]["name"] == "sf_3_source_feedback_replan_v1"
             and not data["calls"][-1].get("repair_of"), "lost_call_changed")
    _require(grant["task_id"] == data["task_id"] and grant["input_lock_sha256"] == json_sha(data["input_lock"])
             and Path(grant["original_output"]).resolve() == output and grant["max_slices_per_parent"] == 32
             and grant["renders_per_parent"] == grant["repairs_per_stage"] == 1 and grant["new_unique_windows"] == 0
             and grant["additional_requests"] is grant["effective_request_limit"] is None
             and grant["base_request_limit"] == data["max_requests"], "original_grant_changed")
    _require(not any(data["artifacts"].get(f"sf_{p}_render_claim") for p in (0, 3)), "new_render_already_claimed")
    preparation = _read(grant["preparation_path"])
    parent = next(p for p in preparation["parents"] if p["round"] == 3)
    _require(abs(parent["duration_s"] - 34) < .001, "parent_must_be_actual_34_seconds")
    exclusions = []
    for call in unknowns:
        folder = output / "calls" / call["id"]
        request = _read(folder / "request.json")
        _require(json_sha(request) == call["request_sha256"] and not (folder / "response.json").exists()
                 and not (folder / "parsed.json").exists(), "unknown_request_or_reply_changed")
        scope = request_scope(request)
        exclusions.append({"call_id": call["id"], "request_sha256": call["request_sha256"],
                           "media_sha256": request["media_sha256"], "scope": deepcopy(scope)})
    lost = exclusions[-1]
    _require(lost["scope"] == {"kind": "continuous_window", "source_sha256": parent["sha256"],
                              "source_start_s": 0, "source_end_s": 34}, "lost_whole_parent_scope_changed")
    entries = data["artifacts"].get(OBSERVATION_ARTIFACT, [])
    _require(len(entries) == 1, "unknown_observation_receipt_required")
    observation = _read(entries[0]["path"])
    _require(json_sha(observation) == entries[0]["sha256"] and observation["call_id"] == LOST_CALL
             and observation["response_captured"] is False and observation["no_replay"] is True
             and observation["request_count_before"] == BASELINE and observation["new_requests"] == observation["new_renders"] == 0,
             "unknown_observation_receipt_changed")
    folder = output / "calls" / LOST_CALL
    reclassification = _read(folder / "uncertain_reclassification.json")
    previous = {k: v for k, v in data["calls"][-1].items() if k != "reconciled_at"}
    previous["status"] = "submitted"
    _require(reclassification["previous_record"] == previous and observation["previous_call"] == previous
             and any(e.get("observation_artifact") == entries[0] for e in reclassification["http_evidence"]),
             "uncertain_reclassification_changed")
    queue_request = output / "mcp_queue" / (LOST_CALL + ".request.json")
    started = output / "mcp_queue" / (LOST_CALL + ".started.json")
    queued = _read(queue_request)
    request = _read(folder / "request.json")
    _require(queued == {"job_id": LOST_CALL, "tool": request["tool"], "arguments": request["arguments"]}
             and _read(started)["job_id"] == LOST_CALL
             and not (queue_request.parent / (LOST_CALL + ".response.json")).exists(), "lost_queue_changed")
    transport = {"call_id": LOST_CALL, "request_sha256": lost["request_sha256"]}
    for label, file in (("call_request", folder / "request.json"), ("queue_request", queue_request),
                        ("queue_started", started), ("reclassification", folder / "uncertain_reclassification.json")):
        transport[label + "_path"] = str(file.resolve())
        transport[label + "_byte_sha256"] = sha256_file(file)
    scope = {"kind": "continuous_window", "source_sha256": parent["sha256"], "source_start_s": 27, "source_end_s": 34}
    known = []
    for call in data["calls"]:
        if call["status"] != "received" or not call["name"].startswith("sf_3_facts_") or call.get("repair_of"):
            continue
        request = _read(output / "calls" / call["id"] / "request.json")
        if scope_fingerprint(request["observation_scope"]) == scope_fingerprint(scope):
            known.append((call, request))
    _require(len(known) == 1, "known_local_parent_media_required")
    call, request = known[0]
    media = Path(request["arguments"]["video_source"]).resolve(strict=True)
    lineage_path = media.parent / "lineage.json"
    lineage = _read(lineage_path)
    _require(json_sha(request) == call["request_sha256"] and request["provider"] == "official_vision_mcp_in_codex"
             and request["tool"] == "analyze_video" and request["media_sha256"] == lineage["sha256"] == sha256_file(media)
             and scope_fingerprint(lineage) == scope_fingerprint(scope) and Path(lineage["path"]).resolve() == media
             and lineage["source_offset_s"] == 27 and abs(lineage["media_duration_s"] - 7) <= .001
             and lineage["time_mapping"] == "source_time_s = source_offset_s + proxy_time_s"
             and lineage["spec"]["fps"] == 30 and lineage["spec"]["kind"] == "continuous_window"
             and scope_fingerprint(lineage["spec"]) == scope_fingerprint(scope)
             and sha256_file(parent["path"]) == parent["sha256"], "local_media_mapping_changed")
    local = {"stage": LOCAL_STAGE, "known_media_call_id": call["id"], "parent_round": 3,
             "parent_sha256": parent["sha256"], "path": str(media), "media_sha256": request["media_sha256"],
             "observation_scope": scope, "lineage_path": str(lineage_path), "lineage_sha256": sha256_file(lineage_path),
             "source_offset_s": 27, "duration_s": 7, "fps": 30,
             "time_mapping": "source_time_s = source_offset_s + proxy_time_s"}
    artifacts = deepcopy(data["artifacts"])
    files = {}
    for entries in artifacts.values():
        for entry in entries:
            file = Path(entry["path"]).resolve(strict=True)
            _require(json_sha(_read(file)) == entry["sha256"], "old_artifact_changed")
            files[str(file)] = _proof(file)
    for call in data["calls"]:
        for file in sorted((output / "calls" / call["id"]).glob("*")):
            if file.is_file():
                files[str(file.resolve())] = _proof(file)
    for file in [queue_request, started, media, lineage_path]:
        files[str(file.resolve())] = _proof(file)
    for call in unknowns:
        request = _read(output / "calls" / call["id"] / "request.json")
        if request.get("observation_scope") is None:
            old_media = request["arguments"].get("image_source") or request["arguments"].get("video_source")
            file = Path(old_media).parent / "lineage.json"
            files[str(file.resolve())] = _proof(file)
    return {"unknown_inputs": exclusions, "frozen_unknown_inputs": deepcopy(exclusions),
            "transport_binding": transport, "local_replan_input": local,
            "baseline_artifacts": artifacts, "protected_files": sorted(files.values(), key=lambda p: p["path"])}


def assert_settled_or_admitted(data, policy):
    admitted = set(policy["frozen_unknown_call_ids"])
    _require(not any(c["status"] not in {"received", "submitted"} and
                     not (c["status"] == "uncertain" and c["id"] in admitted) for c in data["calls"]),
             "new_outcome_unknown")
    pending = [c for c in data["calls"][policy["baseline_request_count"]:] if c["status"] == "submitted"]
    _require(len(pending) <= 2 and len({stage_parent(c["name"]) for c in pending}) == len(pending),
             "one_pending_per_parent")


def admissible_status(policy, call):
    if call["status"] in {"received", "submitted"}:
        return True
    return policy is not None and call["status"] == "uncertain" and any(
        old["call_id"] == call["id"] and old["request_sha256"] == call["request_sha256"]
        for old in policy["frozen_unknown_inputs"])


def read_validate(output, data, grant):
    entries = data["artifacts"].get(POLICY, [])
    if not entries:
        return None
    _require(len(entries) == 1, "one_policy_required")
    policy = _read(entries[0]["path"])
    _require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == POLICY
             and policy["task_id"] == data["task_id"] and policy["preparation_id"] == grant["preparation_id"]
             and Path(policy["output"]).resolve() == Path(output).resolve()
             and policy["input_lock_sha256"] == json_sha(data["input_lock"])
             and policy["base_request_limit"] == data["max_requests"] == grant["base_request_limit"]
             and policy["baseline_request_count"] == BASELINE <= len(data["calls"])
             and data["request_count"] == len(data["calls"])
             and json_sha(data["calls"][:BASELINE]) == policy["prefix_calls_sha256"]
             and policy["frozen_unknown_call_ids"] == FROZEN_IDS and policy["first_stages"] == {str(k): v for k, v in FIRST_STAGES.items()}
             and policy["repairs_per_stage"] == policy["renders_per_parent"] == 1 and policy["max_slices_per_parent"] == 32
             and policy["new_unique_windows"] == 0 and policy["effective_request_limit"] is None
             and policy["no_numeric_request_ceiling"] is True and policy["old_failures_preserved"] is True
             and policy["no_automatic_replanning"] is True and policy["goal_resumed"] is False
             and policy["local_input_is_new_scope_not_unknown_reply"] is True,
             "policy_or_prefix_changed")
    text(policy.get("user_instruction"), "independent_slot_recovery/user_instruction")
    baseline = {**data, "request_count": BASELINE, "calls": data["calls"][:BASELINE], "artifacts": policy["baseline_artifacts"]}
    bound = _bound_inputs(output, baseline, grant)
    _require(all(policy[k] == v for k, v in bound.items()), "bound_input_changed")
    _require(all(data["artifacts"].get(k) == v for k, v in policy["baseline_artifacts"].items()), "old_artifact_ledger_changed")
    assert_settled_or_admitted(data, policy)
    later = data["calls"][BASELINE:]
    _require(not any(c["name"] in {"sf_3_source_feedback_replan_v1", "sf_3_source_feedback_replan_v1_repair"} for c in later),
             "unknown_stage_cannot_be_replayed")
    for parent in (0, 3):
        own = [c for c in later if stage_parent(c["name"]) == parent]
        _require(not own or own[0]["name"] == FIRST_STAGES[parent], "first_independent_stage_changed")
    local = [c for c in later if re.fullmatch(LOCAL_STAGE_PATTERN, c["name"])]
    _require(len(local) <= 2 and all(c["name"] == (LOCAL_STAGE if i == 0 else LOCAL_STAGE + "_repair")
             and (not c.get("repair_of") if i == 0 else c.get("repair_of") == local[0]["id"]
                  and local[0]["status"] == "received") for i, c in enumerate(local)), "local_stage_or_repair_repeated")
    return policy


def record_independent_slot_recovery(state, user_instruction):
    """Explicit user's continuation only; never call a model or reset state."""
    from .slot_finecut_budget import STATE_MUTEX
    with STATE_MUTEX:
        grant = state.assert_protected()
        existing = read_validate(state.output, state.data, grant)
        if existing:
            return existing
        text(user_instruction, "independent_slot_recovery/user_instruction")
        inputs = _bound_inputs(state.output, state.data, grant)
        policy = {"policy": POLICY, "task_id": state.data["task_id"], "preparation_id": grant["preparation_id"],
                  "output": str(Path(state.output).resolve()), "input_lock_sha256": json_sha(state.data["input_lock"]),
                  "base_request_limit": state.data["max_requests"], "baseline_request_count": BASELINE,
                  "prefix_calls_sha256": json_sha(state.data["calls"]), "frozen_unknown_call_ids": FROZEN_IDS,
                  "first_stages": {str(k): v for k, v in FIRST_STAGES.items()}, "user_instruction": user_instruction,
                  "repairs_per_stage": 1, "renders_per_parent": 1, "max_slices_per_parent": 32,
                  "new_unique_windows": 0, "effective_request_limit": None, "no_numeric_request_ceiling": True,
                  "old_failures_preserved": True, "no_automatic_replanning": True, "goal_resumed": False,
                  "local_input_is_new_scope_not_unknown_reply": True, **inputs}
        state.set_artifact(POLICY, policy)
        state.assert_protected()
        return policy


def check_input(policy, name, request):
    if policy is None:
        return False
    stage_parent(name)
    _require(request.get("provider") == "official_vision_mcp_in_codex" and request.get("tool") == "analyze_video",
             "official_provider_required")
    fingerprint = scope_fingerprint(request.get("observation_scope"))
    _require(not any(json_sha(request) == old["request_sha256"] or request.get("media_sha256") == old["media_sha256"]
                     or fingerprint == scope_fingerprint(old["scope"]) for old in policy["unknown_inputs"]),
             "unknown_input_replay_forbidden")
    if re.fullmatch(LOCAL_STAGE_PATTERN, name):
        local = policy["local_replan_input"]
        _require(request["media_sha256"] == local["media_sha256"]
                 and fingerprint == scope_fingerprint(local["observation_scope"])
                 and Path(request["arguments"]["video_source"]).resolve() == Path(local["path"]).resolve(),
                 "local_replan_input_changed")
        return True
    return False


def check_next_stage(policy, data, parent, name):
    """Recognize only the first independent transition; normal guards follow."""
    if policy is None:
        return False
    _require(parent in FIRST_STAGES and stage_parent(name) == parent, "parent_changed")
    own = [c for c in data["calls"][BASELINE:] if stage_parent(c["name"]) == parent]
    if not own:
        _require(name == FIRST_STAGES[parent], "first_independent_stage_changed")
        return True
    return False
