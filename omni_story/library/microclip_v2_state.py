"""Append-only authorization for one whole-slot refinement of a settled parent.

The completed microclip v1 is validated once before registration. After the
first new call, its history is protected by the frozen 218-call prefix rather
than by teaching an exhausted adapter to accept new stages.
"""
from copy import deepcopy
from pathlib import Path
import re
from threading import RLock

from .pipeline import _read
from .forward_slot_budget import _history, _prefix_sha
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, _timestamp, file_lock, json_sha, scope_fingerprint, write_json

POLICY = ARTIFACT = "microclip_slot_finecut_v2"
BASELINE = 218
RESUME_ARTIFACT = "mc2_infrastructure_resume"
RESUME_POLICY = "microclip_v2_frame_catalog_resume_v1"
RESUME_BASELINE = 222
STAGES = r"^mc2_(overview|motion|intent|region_[0-5]|anchors|edge_(?:[0-9]|1[01])|plan|slice_(?:[0-9]|1[01])|source_check|blind_video|blind_page_[0-9]|review)(_repair)?$"
KNOWN_RETRY_STAGE = "mc2_region_1_retry"
KNOWN_DISPATCH_STAGE = "mc2_region_1_retry_dispatch"
_MUTEX = RLock()
_VERIFIED_GRIDS = {}


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("microclip_v2:" + reason)


def _stage(name):
    from .microclip_v2_boundary_state import stage as boundary_stage
    navigation = boundary_stage(name)
    if navigation:
        return "boundary", navigation[-1]
    if name in (KNOWN_RETRY_STAGE, KNOWN_RETRY_STAGE + "_repair",
                KNOWN_DISPATCH_STAGE, KNOWN_DISPATCH_STAGE + "_repair"):
        return "region_1", name.endswith("_repair")
    match = re.fullmatch(STAGES, name)
    _require(match is not None, "stage_not_authorized")
    return match[1], bool(match[2])


def _files(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def _stable_frame(row):
    # requested_time_s is the selector's target, not the decoded frame's PTS.
    # The same presentation frame may legitimately appear in several grids.
    return {k: v for k, v in row.items() if k not in {"png_path", "requested_time_s"}}


def _verify_grid(manifest, *, source_path, force=False):
    """Reuse CPU decoding only while every bound input byte proof still agrees.

    This cache is local to the v2 adapter. Successful decoded-frame validation
    is reused; known/unknown model requests and old manifests are never changed.
    The stat-validated SHA helper still checks metadata before each cache hit.
    """
    from .microclip_frames import verify_grid
    path = Path(manifest).resolve(strict=True)
    source = Path(source_path).resolve(strict=True)
    manifest_sha = sha256_file(path, force=force)
    value = _read(path)
    inputs = {path, path.with_name("manifest.sha256"), source,
              Path(value["grid"]["path"]).resolve(strict=True)}
    inputs.update(Path(frame["png_path"]).resolve(strict=True) for frame in value["frames"])
    key = (str(path), str(source), manifest_sha,
           tuple((str(file), sha256_file(file, force=force)) for file in sorted(inputs)))
    if not force and key in _VERIFIED_GRIDS:
        return deepcopy(_VERIFIED_GRIDS[key])
    result = verify_grid(path, source_path=source)
    # Do not cache a proof if any file changed while decoding.
    after = (str(path), str(source), sha256_file(path, force=True),
             tuple((str(file), sha256_file(file, force=True)) for file in sorted(inputs)))
    _require(after == key, "grid_input_changed_during_validation")
    # Remove obsolete generations for this manifest; memory remains bounded by
    # the finite v2 input set rather than by changes to files.
    for previous in tuple(_VERIFIED_GRIDS):
        if previous[:2] == key[:2]:
            del _VERIFIED_GRIDS[previous]
    _VERIFIED_GRIDS[key] = deepcopy(result)
    return deepcopy(result)


def _known_catalog_stop(output, data, auth, *, force=False):
    """Bind exactly the pre-submission CPU conflict; not a model retry grant."""
    from .contracts import parse_model_json
    from .microclip_v2_contracts import validate_frames, validate_motion, validate_intent
    added = data["calls"][BASELINE:RESUME_BASELINE]
    names = ["mc2_overview", "mc2_motion", "mc2_motion_repair", "mc2_intent"]
    _require(len(added) == 4 and [c["name"] for c in added] == names
             and all(c["status"] == "received" for c in added)
             and added[2].get("repair_of") == added[1]["id"]
             and all(not c.get("repair_of") for c in (added[0], added[1], added[3])), "resume_known_222_prefix_required")
    folder = Path(auth["execution_directory"])
    result_path = folder / "result.json"
    rows = data["artifacts"].get("mc2_result", [])
    _require(len(rows) == 1, "resume_original_stop_required")
    receipt_path = Path(rows[0]["path"]).resolve(strict=True)
    result, receipt = _read(result_path), _read(receipt_path)
    _require(receipt["policy"] == POLICY and Path(receipt["result_path"]).resolve() == result_path
             and receipt["model_status"] == result.get("status") == "stopped"
             and result.get("error") == "microclip_v2:frame_id_conflict"
             and result.get("model_quality_gate_passed") is False
             and result.get("final_video") is None and result.get("final_sha256") is None
             and result.get("measured_duration_s") is None and result.get("observed_source_frames") == 10,
             "resume_cpu_catalog_stop_only")
    parsed = {}
    for call in (added[0], added[2], added[3]):
        call_folder = output / "calls" / call["id"]
        value = _read(call_folder / "parsed.json")
        raw = "\n".join(c["text"] for c in _read(call_folder / "response.json")["result"]["content"] if c.get("type") == "text")
        _require(not (call_folder / "protocol_failure.json").exists() and parse_model_json(raw) == value, "resume_known_parsed_predecessors_required")
        parsed[call["name"]] = value
    original_folder = output / "calls" / added[1]["id"]
    _require((original_folder / "protocol_failure.json").exists() and not (original_folder / "parsed.json").exists(),
             "resume_preserves_known_motion_failure")
    descriptor = _read(data["artifacts"]["mc2_input_mc2_overview"][0]["path"])
    overview = _verify_grid(descriptor["lineage_path"], source_path=auth["parent"]["path"], force=force)
    validate_frames(parsed["mc2_overview"], overview["frames"])
    validate_motion(parsed["mc2_motion_repair"], auth["slot"]["end_s"] - auth["slot"]["start_s"])
    catalog = {f["frame_id"]: f for f in overview["frames"]}
    validate_intent(parsed["mc2_intent"], auth["slot"], catalog)
    region_rows = data["artifacts"].get("mc2_input_mc2_region_0", [])
    _require(len(region_rows) == 1, "resume_prepared_region_required")
    region = _read(region_rows[0]["path"])
    shown = _verify_grid(region["lineage_path"], source_path=auth["parent"]["path"], force=force)
    repeated = [f for f in shown["frames"] if f["frame_id"] in catalog]
    _require(repeated and any(f.get("requested_time_s") != catalog[f["frame_id"]].get("requested_time_s") for f in repeated)
             and all(_stable_frame(f) == _stable_frame(catalog[f["frame_id"]]) for f in repeated)
             and len(set(catalog) | {f["frame_id"] for f in shown["frames"]}) == 10,
             "resume_selector_metadata_conflict_only")
    return result_path, receipt_path, result["error"]


def _infrastructure_resume(output, data, auth, *, force=False):
    rows = data["artifacts"].get(RESUME_ARTIFACT, [])
    if not rows:
        return None
    _require(len(rows) == 1, "one_infrastructure_resume_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    value = _read(path)
    result_path = Path(auth["execution_directory"]) / "result.json"
    original_rows = data["artifacts"].get("mc2_result", [])
    _require(len(original_rows) == 1, "resume_original_stop_required")
    receipt_path = Path(original_rows[0]["path"]).resolve(strict=True)
    error = "microclip_v2:frame_id_conflict"
    _require(path.is_relative_to(output / "artifacts") and json_sha(value) == rows[0]["sha256"]
             and value["policy"] == RESUME_POLICY and value["task_id"] == data["task_id"]
             and value["original_authorization_sha256"] == json_sha(auth)
             and value["baseline_request_count"] == RESUME_BASELINE and len(data["calls"]) >= RESUME_BASELINE
             and value["prefix_calls_sha256"] == json_sha(data["calls"][:RESUME_BASELINE])
             and value["result_path"] == str(result_path) and value["receipt_path"] == str(receipt_path)
             and value["known_cpu_error"] == error and value["next_stage"] == "mc2_region_0"
             and value["new_renders"] == 0 and value["reuse_original_unused_render"] is True
             and not any(value["baseline_artifacts"].get(k) for k in ("mc2_render_claim", "mc2_recovered_result"))
             and set(value["baseline_call_files"]) == {c["id"] for c in data["calls"][BASELINE:RESUME_BASELINE]}
             and isinstance(value["user_instruction"], str) and value["user_instruction"].strip(), "infrastructure_resume_binding_changed")
    _require(sha256_file(result_path, force=force) == value["result_sha256"]
             and sha256_file(receipt_path, force=force) == value["receipt_sha256"], "original_cpu_stop_changed")
    for key, previous in value["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == previous, "resume_historical_artifact_changed")
    for ident, previous in value["baseline_call_files"].items():
        _require(_files(output / "calls" / ident) == previous, "resume_historical_call_files_changed")
    for proof in value["protected_files"]:
        _require(sha256_file(proof["path"], force=force) == proof["sha256"], "resume_historical_bytes_changed")
    for proof in value["journal_prefixes"]:
        _require(_prefix_sha(proof["path"], proof["bytes"], force=force) == proof["sha256"], "resume_journal_prefix_changed")
    _known_catalog_stop(output, data, auth, force=force)
    return value


def record_infrastructure_resume(output, user_instruction):
    """Continue this one CPU stop; all paid replies and unused render stay bound."""
    output = Path(output).resolve(strict=True)
    auth = get_auth(output, force=True)
    if auth["infrastructure_resume"] is not None:
        return auth
    data = _read(output / "library_state.json")
    _require(isinstance(user_instruction, str) and user_instruction.strip()
             and data["request_count"] == len(data["calls"]) == RESUME_BASELINE
             and not any(data["artifacts"].get(k) for k in ("mc2_recovered_result", "mc2_render_claim"))
             and not Path(auth["allowed_render_directory"]).exists(), "resume_settled_unused_render_required")
    result_path, receipt_path, error = _known_catalog_stop(output, data, auth, force=True)
    files, prefixes = _history(output)
    payload = {"policy": RESUME_POLICY, "task_id": data["task_id"],
               "original_authorization_sha256": json_sha(_read(auth["authorization_path"])),
               "baseline_request_count": RESUME_BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
               "baseline_artifacts": deepcopy(data["artifacts"]),
               "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"][BASELINE:]},
               "result_path": str(result_path), "result_sha256": sha256_file(result_path),
               "receipt_path": str(receipt_path), "receipt_sha256": sha256_file(receipt_path),
               "known_cpu_error": error, "next_stage": "mc2_region_0", "new_renders": 0,
               "reuse_original_unused_render": True, "protected_files": files, "journal_prefixes": prefixes,
               "user_instruction": user_instruction}
    SlotMicroclipState(output).set_artifact(RESUME_ARTIFACT, payload)
    return get_auth(output, force=True)


def record_authorization(output, user_instruction):
    """Record the current user continuation; no paid calls or Goal resumption."""
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(ARTIFACT):
        return get_auth(output)
    from .microclip_state import get_auth as old_auth
    old = old_auth(output, force=True)
    _require(isinstance(user_instruction, str) and user_instruction.strip()
             and data["request_count"] == len(data["calls"]) == BASELINE
             and all(c["status"] in {"received", "uncertain"} for c in data["calls"])
             and data["artifacts"].get("mc_recovered_result"), "settled_completed_218_required")
    receipt = _read(data["artifacts"]["mc_recovered_result"][0]["path"])
    result_path = Path(receipt["result_path"])
    result = _read(result_path)
    _require(result.get("final_video") and result.get("final_sha256") == sha256_file(result["final_video"])
             and Path(result["final_video"]).resolve() == Path(old["allowed_render_directory"]) / "final.mp4",
             "completed_original_candidate_required")
    base = output / "artifacts" / POLICY
    knowledge = base / "GLM_SLOT_MICROCLIP_V2.md"
    original_knowledge = Path(__file__).resolve().parents[2] / "craft_knowledge" / knowledge.name
    text = original_knowledge.read_text(encoding="utf-8")
    _require(not knowledge.exists() or knowledge.read_text(encoding="utf-8") == text, "knowledge_already_bound")
    if not knowledge.exists():
        knowledge.parent.mkdir(parents=True, exist_ok=True)
        knowledge.write_text(text, encoding="utf-8")
    files, prefixes = _history(output)
    # Include byte proofs outside the run (fixed source inputs), too.
    by_path = {str(Path(row["path"]).resolve()): row for row in files}
    for row in old["protected_files"]:
        name = str(Path(row["path"]).resolve(strict=True))
        _require(name not in by_path or by_path[name]["sha256"] == row["sha256"], "old_proof_conflict")
        by_path[name] = {"path": name, "sha256": row["sha256"]}
    p = {"policy": POLICY, "task_id": data["task_id"], "original_output": str(output),
         "input_lock_sha256": json_sha(data["input_lock"]), "base_request_limit": data["max_requests"],
         "baseline_request_count": BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
         "baseline_artifacts": deepcopy(data["artifacts"]), "baseline_policy_version": data["policy_version"],
         "baseline_render_directories": sorted(d.name for d in output.glob("render_*") if d.is_dir()),
         "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"]},
         "protected_files": sorted(by_path.values(), key=lambda row: row["path"]), "journal_prefixes": prefixes,
         "original_authorization_path": old["authorization_path"],
         "original_authorization_sha256": old["authorization_sha256"],
         "original_result_path": str(result_path), "original_result_sha256": sha256_file(result_path),
         "parent": deepcopy(old["parent"]), "slot": deepcopy(old["slot"]), "outline_path": old["outline_path"],
         "execution_directory": str(base), "allowed_render_directory": str(base / "render"),
         "knowledge_path": str(knowledge), "knowledge_sha256": sha256_file(knowledge),
         "unknown_inputs": deepcopy(old["unknown_inputs"]), "user_instruction": user_instruction,
         "max_concurrency": 1, "new_renders": 1, "repairs_per_stage": 1,
         "numeric_total_request_limit": None, "automatic_round_loops": False, "goal_resumed": False,
         "stage_pattern": STAGES, "max_regions": 6, "max_events": 6, "max_blind_pages": 10,
         "reference_and_library_new_inputs_forbidden": True,
         "original_slot_intent_is_fallible_model_navigation": True}
    LibraryState(output, data["input_lock"], max_requests=data["max_requests"]).set_artifact(ARTIFACT, p)
    return get_auth(output, force=True)


def get_auth(output, *, force=False):
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    from .microclip_v2_boundary_state import ARTIFACT as boundary_artifact, load_boundary_auth
    if data["artifacts"].get(boundary_artifact):
        return load_boundary_auth(output, data, force=force)
    rows = data["artifacts"].get(ARTIFACT, [])
    _require(len(rows) == 1, "one_authorization_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    p = _read(path)
    _require(path.is_relative_to(output / "artifacts") and json_sha(p) == rows[0]["sha256"]
             and p["policy"] == POLICY and p["task_id"] == data["task_id"] and Path(p["original_output"]) == output
             and p["input_lock_sha256"] == json_sha(data["input_lock"]) and p["base_request_limit"] == data["max_requests"]
             and p["baseline_policy_version"] == data["policy_version"] and p["baseline_request_count"] == BASELINE
             and len(data["calls"]) == data["request_count"] >= BASELINE
             and p["prefix_calls_sha256"] == json_sha(data["calls"][:BASELINE]), "policy_or_history_changed")
    _require(p["stage_pattern"] == STAGES and p["max_concurrency"] == p["new_renders"] == p["repairs_per_stage"] == 1
             and p["numeric_total_request_limit"] is None and p["automatic_round_loops"] is p["goal_resumed"] is False
             and p["max_regions"] == p["max_events"] == 6 and p["max_blind_pages"] == 10
             and p["reference_and_library_new_inputs_forbidden"] is True
             and p["original_slot_intent_is_fallible_model_navigation"] is True
             and Path(p["execution_directory"]) == output / "artifacts" / POLICY
             and Path(p["allowed_render_directory"]) == Path(p["execution_directory"]) / "render"
             and sha256_file(p["knowledge_path"], force=force) == p["knowledge_sha256"]
             and sha256_file(p["original_authorization_path"], force=force) == p["original_authorization_sha256"]
             and sha256_file(p["original_result_path"], force=force) == p["original_result_sha256"], "scope_changed")
    _require(sha256_file(p["parent"]["path"], force=force) == p["parent"]["sha256"]
             and p["slot"] in _read(p["outline_path"])["slots"], "parent_or_slot_changed")
    _require(sorted(d.name for d in output.glob("render_*") if d.is_dir()) == p["baseline_render_directories"], "unapproved_legacy_render")
    for key, old in p["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == old, "historical_artifact_changed")
    for ident, old in p["baseline_call_files"].items():
        _require(_files(output / "calls" / ident) == old, "historical_call_files_changed")
    for row in p["protected_files"]:
        _require(sha256_file(row["path"], force=force) == row["sha256"], "historical_bytes_changed")
    for row in p["journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "journal_prefix_changed")
    for lost in p["unknown_inputs"]:
        _require(not any((output / "calls" / lost["call_id"] / name).exists() for name in ("response.json", "parsed.json")), "unknown_reply_fabricated")
    # The static transport alias is separate from the original authorization.
    # Its proof is checked before accepting any later stage or paid request.
    from .microclip_v2_known500 import load_resume, load_dispatch_resume
    runtime = {**p, "authorization_path": str(path)}
    known_resume = load_resume(output, data, runtime, force=force)
    dispatch_resume = load_dispatch_resume(output, data, runtime, known_resume, force=force)
    names, pending = {}, 0
    for call in data["calls"][BASELINE:]:
        kind, repair = _stage(call["name"])
        _require(kind != "boundary", "boundary_permission_required")
        if call["name"] in (KNOWN_RETRY_STAGE, KNOWN_RETRY_STAGE + "_repair"):
            _require(known_resume is not None, "known_failure_resume_required")
        if call["name"] in (KNOWN_DISPATCH_STAGE, KNOWN_DISPATCH_STAGE + "_repair"):
            _require(dispatch_resume is not None, "known_failure_dispatch_resume_required")
        _require(call["name"] not in names and call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "new_stage_or_status_invalid")
        req = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(req) == call["request_sha256"], "new_request_changed")
        if repair:
            old = names.get(call["name"][:-7])
            _require(old and old["status"] == "received" and call.get("repair_of") == old["id"]
                     and list(names)[-1] == old["name"], "sole_repair_binding")
            original = _read(output / "calls" / old["id"] / "request.json")
            _require(original["media_sha256"] == req["media_sha256"]
                     and scope_fingerprint(original["observation_scope"]) == scope_fingerprint(req["observation_scope"]), "repair_input_changed")
        else:
            _require(not call.get("repair_of"), "original_has_repair")
        if call["status"] == "received":
            _require(json_sha(_read(output / "calls" / call["id"] / "response.json")) == call["response_sha256"], "new_response_changed")
        pending += call["status"] == "submitted"
        names[call["name"]] = call
    _require(pending <= 1, "single_lane_required")
    for key, entries in data["artifacts"].items():
        if key.startswith("mc2_"):
            _require(len(entries) == 1 and json_sha(_read(entries[0]["path"])) == entries[0]["sha256"], "new_artifact_changed")
            value = _read(entries[0]["path"])
            if key.startswith("mc2_input_"):
                _require(sha256_file(value["media_path"], force=force) == value["media_sha256"]
                         and sha256_file(value["lineage_path"], force=force) == value["lineage_sha256"], "input_bytes_changed")
            for proof in value.get("completed_files", []):
                _require(sha256_file(proof["path"], force=force) == proof["sha256"], "completed_bytes_changed")
    _require(not Path(p["allowed_render_directory"]).exists() or data["artifacts"].get("mc2_render_claim"), "render_without_claim")
    resume = _infrastructure_resume(output, data, p, force=force)
    return {**p, "authorization_path": str(path), "authorization_sha256": sha256_file(path, force=force),
            "infrastructure_resume": resume, "known_failure_resume": known_resume,
            "known_failure_dispatch_resume": dispatch_resume}


class SlotMicroclipState(LibraryState):
    def __init__(self, output):
        self.authorization = get_auth(output)
        data = _read(Path(output) / "library_state.json")
        super().__init__(output, data["input_lock"], max_requests=data["max_requests"])
        self.max_requests = float("inf")

    def assert_protected(self):
        self.authorization = get_auth(self.output)
        self._reload()
        return self.authorization

    def _received(self, stem):
        from .contracts import parse_model_json
        if self.authorization.get("boundary_metadata_resume"):
            from .microclip_v2_boundary_metadata import received_projection
            projected = received_projection(self.output, self.data, stem)
            if projected:
                return projected
        if self.authorization.get("boundary_resume") and re.fullmatch(r"mc2_edge_([0-9]|1[01])", stem):
            from .microclip_v2_boundary_state import effective_edge
            effective = effective_edge(self.output, self.data, int(stem.rsplit("_", 1)[-1]))
            if effective:
                return effective[:2]
        resume = self.authorization.get("known_failure_resume")
        dispatch = self.authorization.get("known_failure_dispatch_resume")
        if dispatch and resume and stem in (resume["failed_stage"], resume["retry_stage"]):
            stem = dispatch["retry_stage"]
        elif resume and stem == resume["failed_stage"]:
            stem = resume["retry_stage"]
        for call in reversed(self.data["calls"][BASELINE:]):
            folder = self.output / "calls" / call["id"]
            if call["name"] in (stem, stem + "_repair") and call["status"] == "received" and (folder / "parsed.json").exists():
                value = _read(folder / "parsed.json")
                raw = "\n".join(c["text"] for c in _read(folder / "response.json")["result"]["content"] if c.get("type") == "text")
                _require(not (folder / "protocol_failure.json").exists() and parse_model_json(raw) == value, "parsed_not_original_reply")
                return call, value
        raise LibraryStopped("microclip_v2:predecessor_not_parsed:" + stem)

    def _regions(self):
        _, intent = self._received("mc2_intent")
        regions = intent["search_regions"]
        _require(intent["status"] == "ready" and not intent["blocking_questions"]
                 and not any(c["claim_id"] in {"intended_takeaway", "entry_state", "exit_state"}
                             and c["verdict"] == "unsupported" for c in intent["original_claim_checks"])
                 and 1 <= len(regions) <= 6 and [r["region_id"] for r in regions] == [f"r{i}" for i in range(len(regions))],
                 "ready_model_region_ids_required")
        return regions

    def _events(self):
        _, anchors = self._received("mc2_anchors")
        events = anchors["events"]
        _require(anchors["status"] == "ready" and not anchors["blocking_questions"]
                 and 1 <= len(events) <= 6 and [e["event_id"] for e in events] == [f"e{i}" for i in range(len(events))], "ready_model_events_required")
        return events

    def _source_gate(self):
        _, value = self._received("mc2_source_check")
        _, intent = self._received("mc2_intent")
        from .microclip_v2_contracts import validate_source_check
        validate_source_check(value, intent)
        _require(value.get("status") == "ready"
                 and not any(c.get("verdict") == "unsupported" for c in value["obligation_checks"])
                 and not value.get("blocking_questions"), "source_semantics_blocked")
        return value

    def _blind_pages(self):
        rows = self.data["artifacts"].get("mc2_blind_page_plan", [])
        _require(len(rows) == 1, "actual_output_page_plan_required")
        value = _read(rows[0]["path"])
        actual = _read(Path(self.authorization["allowed_render_directory"]) / "render_result.json")
        _require(sha256_file(value["coverage_path"]) == value["coverage_sha256"], "output_coverage_bytes_changed")
        coverage = _read(value["coverage_path"])
        pages = value["pages"]
        _require(value["policy"] == POLICY and value["final_sha256"] == actual["sha256"]
                 and coverage["schema"] == "microclip_dense_coverage_v1"
                 and coverage["source_sha256"] == actual["sha256"]
                 and coverage["start_s"] == 0 and abs(coverage["end_s"] - actual["measured_duration_s"]) <= 1e-6
                 and coverage["page_count"] == len(pages) == len(coverage["page_bindings"])
                 and 1 <= len(pages) <= 10, "actual_output_page_plan_changed")
        end, frames = 0.0, []
        for i, row in enumerate(pages):
            binding = coverage["page_bindings"][i]
            manifest = _verify_grid(binding["manifest_path"], source_path=Path(self.authorization["allowed_render_directory"]) / "final.mp4")
            _require(row["stage"] == f"mc2_blind_page_{i}" and row["start_s"] >= end - 1e-6
                     and row["start_s"] < row["end_s"] <= actual["measured_duration_s"] + 1e-6
                     and row["start_s"] == manifest["request"]["start_s"] and row["end_s"] == manifest["request"]["end_s"]
                     and binding["request_sha256"] == manifest["request_sha256"]
                     and binding["grid_sha256"] == manifest["grid"]["sha256"]
                     and binding["frame_ids"] == [f["frame_id"] for f in manifest["frames"]], "output_page_binding_changed")
            frames.extend(manifest["frames"])
            end = row["end_s"]
        _require(frames[0]["frame_id"] == coverage["first_frame_id"]
                 and frames[-1]["frame_id"] == coverage["last_frame_id"]
                 and frames[0]["source_time_s"] <= 1e-6
                 and abs(frames[-1]["frame_end_s"] - actual["measured_duration_s"]) <= 1e-6,
                 "output_frame_endpoints_incomplete")
        return pages

    def _predecessor(self, kind, added):
        if kind == "overview":
            _require(not added, "first_overview_required")
        elif kind == "motion":
            self._received("mc2_overview")
        elif kind == "intent":
            self._received("mc2_motion")
        elif kind.startswith("region_"):
            index = int(kind.split("_")[-1])
            _require(index < len(self._regions()), "region_not_model_requested")
            _require(not any(_stage(c["name"])[0] in {"anchors", "plan", "source_check", "blind_video", "review"} for c in added), "region_after_anchors")
            if index:
                self._received(f"mc2_region_{index - 1}")
        elif kind == "anchors":
            for index in range(len(self._regions())):
                self._received(f"mc2_region_{index}")
        elif kind.startswith("edge_"):
            index = int(kind.split("_")[-1])
            _require(index < 2 * len(self._events()), "edge_not_model_requested")
            if index:
                self._received(f"mc2_edge_{index - 1}")
        elif kind == "plan":
            for index in range(2 * len(self._events())):
                _, edge = self._received(f"mc2_edge_{index}")
                _require(edge["status"] == "confirmed" and not edge["blocking_questions"], "unconfirmed_boundary")
        elif kind == "source_check":
            for index in range(len(self._planned()["segments"])):
                self._received(f"mc2_slice_{index}")
        elif kind.startswith("slice_"):
            index = int(kind.split("_")[-1])
            _require(index < len(self._planned()["segments"]), "slice_not_model_selected")
            if index:
                self._received(f"mc2_slice_{index - 1}")
        elif kind == "blind_video":
            self._source_gate()
            _require(self.data["artifacts"].get("mc2_render_claim"), "render_required")
        elif kind.startswith("blind_page_"):
            self._received("mc2_blind_video")
            index = int(kind.split("_")[-1])
            _require(index < len(self._blind_pages()), "blind_page_not_requested")
            if index:
                self._received(f"mc2_blind_page_{index - 1}")
        elif kind == "review":
            self._received("mc2_blind_video")
            for index in range(len(self._blind_pages())):
                self._received(f"mc2_blind_page_{index}")

    def _frames(self):
        frames = {}
        for name, entries in self.data["artifacts"].items():
            if name.startswith("mc2_input_") and not name.startswith("mc2_input_mc2_blind_page_"):
                d = _read(entries[0]["path"])
                if d["tool"] != "analyze_image":
                    continue
                manifest = _verify_grid(d["lineage_path"], source_path=self.authorization["parent"]["path"])
                for frame in manifest["frames"]:
                    _require(frame["frame_id"] not in frames or _stable_frame(frames[frame["frame_id"]]) == _stable_frame(frame), "frame_id_conflict")
                    frames[frame["frame_id"]] = frame
        return frames

    def _boundaries(self):
        boundaries = set()
        ordered = []
        for index in range(2 * len(self._events())):
            _, edge = self._received(f"mc2_edge_{index}")
            _require(edge["status"] == "confirmed" and not edge["blocking_questions"], "unconfirmed_boundary")
            from .microclip_v2_boundary_state import effective_edge
            effective = effective_edge(self.output, self.data, index) if self.authorization.get("boundary_resume") else None
            descriptor = effective[2] if effective else _read(self.data["artifacts"][f"mc2_input_mc2_edge_{index}"][0]["path"])
            shown = _read(descriptor["lineage_path"])["frames"]
            _require(edge["confirmed_frame_id"] in {f["frame_id"] for f in shown}, "confirmed_boundary_not_shown")
            boundaries.add(edge["confirmed_frame_id"])
            ordered.append(next(f for f in shown if f["frame_id"] == edge["confirmed_frame_id"]))
        if self.authorization.get("boundary_resume"):
            for index in range(0, len(ordered), 2):
                _require(ordered[index]["source_time_s"] <= ordered[index + 1]["source_time_s"], "event_boundary_order_invalid")
                if index:
                    _require(ordered[index - 1]["frame_end_s"] <= ordered[index]["source_time_s"] + 1e-6, "event_boundaries_overlap")
        return boundaries

    def _planned(self, source=None):
        _, model_plan = self._received("mc2_plan")
        _, intent = self._received("mc2_intent")
        from .microclip_v2_contracts import validate_plan
        source = source or {**self.authorization["parent"], "source_id": "guard_parent"}
        return validate_plan(source, self.authorization["slot"], model_plan, self._frames(), self._boundaries(), intent)

    def _check_input(self, name, request):
        kind, _ = _stage(name)
        stem = name.removesuffix("_repair")
        rows = self.data["artifacts"].get("mc2_input_" + stem, [])
        _require(len(rows) == 1, "input_descriptor_required")
        d = _read(rows[0]["path"])
        image = kind in {"overview", "intent", "anchors", "boundary"} or kind.startswith(("region_", "edge_", "blind_page_"))
        media = Path(request.get("arguments", {}).get("image_source" if image else "video_source", "")).resolve(strict=True)
        _require(d["policy"] == POLICY and d["stage"] == stem and d["tool"] == request.get("tool") == ("analyze_image" if image else "analyze_video")
                 and request.get("provider") == "official_vision_mcp_in_codex" and Path(d["media_path"]).resolve() == media
                 and sha256_file(media) == d["media_sha256"] == request.get("media_sha256")
                 and sha256_file(d["lineage_path"]) == d["lineage_sha256"], "official_bound_media_required")
        scope = scope_fingerprint(request["observation_scope"])
        _require(scope == scope_fingerprint(d["observation_scope"]), "descriptor_scope_changed")
        for lost in self.authorization["unknown_inputs"]:
            _require(json_sha(request) != lost["request_sha256"] and request["media_sha256"] != lost["media_sha256"]
                     and scope != scope_fingerprint(lost["scope"]), "unknown_input_replay")
        lineage = _read(d["lineage_path"])
        output_stage = kind in {"blind_video", "review"} or kind.startswith("blind_page_")
        if output_stage:
            final = Path(self.authorization["allowed_render_directory"]) / "final.mp4"
            actual = _read(final.parent / "render_result.json")
            _require(self.data["artifacts"].get("mc2_render_claim") and scope[1] == actual["sha256"] == sha256_file(final), "actual_output_only")
            source_path = final
            allowed_start, allowed_end = 0.0, float(actual["measured_duration_s"])
        else:
            parent, slot = self.authorization["parent"], self.authorization["slot"]
            source_path = Path(parent["path"])
            allowed_start, allowed_end = float(slot["start_s"]), float(slot["end_s"])
            _require(scope[1] == parent["sha256"], "parent_slot_only")
        _require(allowed_start <= scope[2] < scope[3] <= allowed_end + 1e-6, "authorized_range_only")
        if image:
            _require(scope[0] == "sparse_contact_sheet", "sparse_image_scope_required")
            verified = _verify_grid(d["lineage_path"], source_path=source_path)
            _require(verified["source"]["sha256"] == scope[1] and scope[2:] ==
                     (float(verified["request"]["start_s"]), float(verified["request"]["end_s"]))
                     and Path(verified["grid"]["path"]).resolve() == media
                     and verified["grid"]["sha256"] == request["media_sha256"]
                     and 1 <= len(verified["frames"]) <= 6, "actual_grid_lineage_required")
            if kind == "boundary":
                from .microclip_v2_boundary_state import check_input
                check_input(self, name, request)
            elif kind.startswith("region_"):
                index = int(kind.split("_")[-1])
                regions = self._regions()
                _require(index < len(regions), "region_not_model_requested")
                region = regions[index]
                frames = self._frames()
                left, right = frames[region["start_frame_id"]], frames[region["end_frame_id"]]
                _require(d.get("region_id") == region["region_id"] and scope[2:] ==
                         (float(left["source_time_s"]), float(right["frame_end_s"])), "model_region_binding_required")
            elif kind.startswith("edge_"):
                index = int(kind.split("_")[-1])
                events = self._events()
                _require(index < 2 * len(events), "edge_not_model_requested")
                event, key = events[index // 2], ("start", "end")[index % 2]
                ident = event[key + "_frame_id"]
                anchor = self._frames().get(ident)
                _require(anchor and d.get("anchor_frame_id") == ident and d.get("anchor_key") == key
                         and d.get("event_id") == event["event_id"]
                         and ident in {f["frame_id"] for f in verified["frames"]}
                         and max(allowed_start, anchor["source_time_s"] - .25) <= scope[2] <= anchor["source_time_s"] < scope[3]
                         <= min(allowed_end, anchor["source_time_s"] + .25) + 1e-6, "anchor_must_be_shown_with_neighbors")
            elif kind.startswith("blind_page_"):
                row = self._blind_pages()[int(kind.split("_")[-1])]
                _require(scope[2:] == (float(row["start_s"]), float(row["end_s"])), "actual_page_scope_changed")
            else:
                _require(scope[2:] == (allowed_start, allowed_end), "whole_slot_overview_required")
        else:
            if kind.startswith("slice_"):
                index = int(kind.split("_")[-1])
                segment = self._planned()["segments"][index]
                selected = (float(segment["source_in_s"]), float(segment["source_out_s"]))
                _require(d.get("slice_index") == index and d.get("source_range") ==
                         {"source_in_s": segment["source_in_s"], "source_out_s": segment["source_out_s"]}
                         and scope[2:] == selected, "exact_model_slice_required")
            else:
                _require(scope[2:] == (allowed_start, allowed_end), "full_continuous_video_required")
            _require(scope[0] == "continuous_window", "continuous_video_required")
            _require(scope == scope_fingerprint(lineage) and lineage["sha256"] == request["media_sha256"]
                     and Path(lineage["path"]).resolve() == media and Path(lineage["source_path"]).resolve() == source_path.resolve()
                     and lineage.get("audio_present") is False, "silent_normal_speed_video_lineage_required")

    def begin_call(self, name, request, *, repair_of=None):
        with _MUTEX, file_lock(self.lock_path):
            self.assert_protected()
            kind, repair = _stage(name)
            added = self.data["calls"][BASELINE:]
            ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            known = self.authorization.get("known_failure_resume")
            dispatch = self.authorization.get("known_failure_dispatch_resume")
            boundary = self.authorization.get("boundary_resume")
            metadata = self.authorization.get("boundary_metadata_resume")
            _require(all(c["status"] == "received" or known and c["id"] == known["failed_call_id"]
                         and c["status"] == "failed_known" or dispatch and c["id"] == dispatch["failed_call_id"]
                         and c["status"] == "failed_known" for c in added), "new_unknown_or_pending")
            if name in (KNOWN_RETRY_STAGE, KNOWN_RETRY_STAGE + "_repair"):
                _require(known is not None, "known_failure_resume_required")
            if name in (KNOWN_DISPATCH_STAGE, KNOWN_DISPATCH_STAGE + "_repair"):
                _require(dispatch is not None, "known_failure_dispatch_resume_required")
            resumed = self.authorization["infrastructure_resume"] is not None
            _require((not self.data["artifacts"].get("mc2_result") or resumed)
                     and (not self.data["artifacts"].get("mc2_recovered_result") or known)
                     and (not self.data["artifacts"].get("mc2_network_result") or dispatch)
                     and (not self.data["artifacts"].get("mc2_dispatch_result") or boundary)
                     and (not self.data["artifacts"].get("mc2_boundary_result") or metadata)
                     and not self.data["artifacts"].get("mc2_boundary_metadata_result")
                     and not any(c["name"] == name for c in added), "finished_or_repeated_stage")
            if resumed and len(self.data["calls"]) == RESUME_BASELINE:
                _require(name == self.authorization["infrastructure_resume"]["next_stage"], "resume_next_stage_required")
            first_known_retry = known and len(self.data["calls"]) == known["baseline_request_count"]
            if first_known_retry:
                _require(name == known["retry_stage"], "known_failure_next_stage_required")
            first_dispatch = dispatch and len(self.data["calls"]) == dispatch["baseline_request_count"]
            if first_dispatch:
                _require(name == dispatch["retry_stage"], "known_failure_dispatch_next_stage_required")
            if boundary and len(self.data["calls"]) == boundary["baseline_request_count"]:
                _require(name == boundary["first_stage"], "boundary_next_stage_required")
            if metadata and len(self.data["calls"]) == metadata["baseline_request_count"]:
                _require(name == metadata["next_stage"], "boundary_metadata_next_stage_required")
            if kind == "boundary":
                _require(boundary is not None, "boundary_permission_required")
            if name in (KNOWN_RETRY_STAGE, KNOWN_RETRY_STAGE + "_repair",
                        KNOWN_DISPATCH_STAGE, KNOWN_DISPATCH_STAGE + "_repair"):
                original = _read(self.output / "calls" / known["failed_call_id"] / "request.json")
                expected = deepcopy(original)
                expected["known_failure_retry_of"] = known["failed_call_id"]
                if name in (KNOWN_DISPATCH_STAGE, KNOWN_DISPATCH_STAGE + "_repair"):
                    expected["known_failure_retry_of"] = dispatch["failed_call_id"]
                if repair:
                    prompt = request.get("arguments", {}).get("prompt")
                    prefix = original["arguments"]["prompt"] + "\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。"
                    _require(isinstance(prompt, str) and prompt.startswith(prefix) and len(prompt) > len(prefix),
                             "known_failure_repair_prompt_changed")
                    expected["arguments"]["prompt"] = prompt
                _require(request == expected, "known_failure_retry_input_changed")
            if repair:
                _require(added and added[-1]["name"] == name[:-7] and ident == added[-1]["id"] and not added[-1].get("repair_of")
                         and not any(c.get("repair_of") == ident for c in added), "sole_repair_required")
                folder = self.output / "calls" / ident
                _require((folder / "protocol_failure.json").exists() and not (folder / "parsed.json").exists(), "known_format_failure_required")
                old = _read(folder / "request.json")
                _require(old["media_sha256"] == request["media_sha256"]
                         and scope_fingerprint(old["observation_scope"]) == scope_fingerprint(request["observation_scope"]), "repair_input_changed")
            else:
                _require(ident is None, "original_has_repair")
                if added and not (first_known_retry or first_dispatch):
                    self._received(added[-1]["name"].removesuffix("_repair"))
                if kind == "boundary":
                    from .microclip_v2_boundary_state import predecessor
                    predecessor(self, name)
                else:
                    self._predecessor(kind, added)
            self._check_input(name, request)
            digest = json_sha(request)
            _require(not any(c["request_sha256"] == digest for c in self.data["calls"]), "request_replay")
            count = self.data["request_count"] + 1
            folder = self.output / "calls" / f"glm_{count:03d}_{name}"
            folder.mkdir(parents=True, exist_ok=False)
            write_json(folder / "request.json", request)
            call = {"id": folder.name, "name": name, "status": "submitted", "submitted_at": _timestamp(),
                    "request_sha256": digest, "usage": {}, "repair_of": ident}
            self.data["calls"].append(call)
            self.data["request_count"] = count
            self._save()
            return deepcopy(call), folder

    def _new(self, call):
        self.assert_protected()
        ident = call["id"] if isinstance(call, dict) else call
        known = self.authorization.get("known_failure_resume")
        dispatch = self.authorization.get("known_failure_dispatch_resume")
        boundary = self.authorization.get("boundary_resume")
        metadata = self.authorization.get("boundary_metadata_resume")
        baseline = metadata["baseline_request_count"] if metadata else boundary["baseline_request_count"] if boundary else dispatch["baseline_request_count"] if dispatch else known["baseline_request_count"] if known else BASELINE
        _require(any(c["id"] == ident for c in self.data["calls"][baseline:]), "historical_call_read_only")

    def complete_call(self, call, response, *, usage=None):
        with _MUTEX:
            self._new(call)
            return super().complete_call(call, response, usage=usage)

    def fail_call(self, call, error, *, uncertain=True):
        with _MUTEX:
            self._new(call)
            return super().fail_call(call, error, uncertain=uncertain)

    def reconcile_received(self, call, response, *, evidence, usage=None):
        with _MUTEX:
            self._new(call)
            return super().reconcile_received(call, response, evidence=evidence, usage=usage)

    def reclassify_uncertain(self, call, *, evidence):
        with _MUTEX:
            self._new(call)
            return super().reclassify_uncertain(call, evidence=evidence)

    def set_artifact(self, name, value):
        with _MUTEX:
            self.assert_protected()
            _require(name.startswith("mc2_"), "old_artifact_read_only")
            if self.data["artifacts"].get(name):
                row = self.data["artifacts"][name][0]
                _require(_read(row["path"]) == value, "existing_artifact_read_only")
                return Path(row["path"])
            return super().set_artifact(name, value)

    def claim_render(self, plan, source=None):
        with _MUTEX:
            auth = self.assert_protected()
            known = auth.get("known_failure_resume")
            dispatch = auth.get("known_failure_dispatch_resume")
            boundary = auth.get("boundary_resume")
            metadata = auth.get("boundary_metadata_resume")
            _require(all(c["status"] == "received" or known and c["id"] == known["failed_call_id"]
                         and c["status"] == "failed_known" or dispatch and c["id"] == dispatch["failed_call_id"]
                         and c["status"] == "failed_known" for c in self.data["calls"][BASELINE:])
                     and (not self.data["artifacts"].get("mc2_result") or auth["infrastructure_resume"] is not None)
                     and (not self.data["artifacts"].get("mc2_recovered_result") or known)
                     and (not self.data["artifacts"].get("mc2_network_result") or dispatch)
                     and (not self.data["artifacts"].get("mc2_dispatch_result") or boundary)
                     and (not self.data["artifacts"].get("mc2_boundary_result") or metadata)
                     and not self.data["artifacts"].get("mc2_boundary_metadata_result"), "unfinished_known_run_required")
            self._source_gate()
            call, model_plan = self._received("mc2_plan")
            _, intent = self._received("mc2_intent")
            boundaries = self._boundaries()
            _require(source and source["sha256"] == auth["parent"]["sha256"] == sha256_file(source["path"])
                     and Path(source["path"]).resolve() == Path(auth["parent"]["path"]).resolve(), "actual_parent_source_required")
            expected = self._planned(source)
            _require(plan == expected, "compiled_plan_differs_from_model")
            directory = Path(auth["allowed_render_directory"])
            payload = {"policy": POLICY, "directory": str(directory), "max_renders": 1, "plan_sha256": json_sha(plan),
                       "model_call_id": call["id"], "model_response_sha256": call["response_sha256"],
                       "model_plan_sha256": json_sha(model_plan), "intent_sha256": json_sha(intent),
                       "confirmed_boundary_ids": sorted(boundaries)}
            if self.data["artifacts"].get("mc2_render_claim"):
                _require(_read(self.data["artifacts"]["mc2_render_claim"][0]["path"]) == payload, "render_claim_changed")
            else:
                _require(not directory.exists(), "render_exists_without_claim")
                self.set_artifact("mc2_render_claim", payload)
            return directory

    def finish(self, payload):
        with _MUTEX:
            auth = self.assert_protected()
            _require(not any(c["status"] == "submitted" for c in self.data["calls"][BASELINE:]), "new_call_pending")
            if payload.get("model_quality_gate_passed") is True:
                source_check = self._source_gate()
                _, video = self._received("mc2_blind_video")
                for index in range(len(self._blind_pages())):
                    self._received(f"mc2_blind_page_{index}")
                _, value = self._received("mc2_review")
                _, intent = self._received("mc2_intent")
                _, motion = self._received("mc2_motion")
                _require(motion["observation_status"] == video["observation_status"] == "complete"
                         and all(c["verdict"] == "supported" for c in intent["original_claim_checks"])
                         and all(o["support"] == "visual" for o in intent["obligations"])
                         and all(c["verdict"] == "supported" for c in source_check["obligation_checks"]),
                         "semantic_observation_incomplete")
                for index in range(len(self._planned()["segments"])):
                    _, facts = self._received(f"mc2_slice_{index}")
                    _require(facts["observation_status"] == "complete", "slice_observation_incomplete")
                page_plan = _read(self.data["artifacts"]["mc2_blind_page_plan"][0]["path"])
                coverage = _read(page_plan["coverage_path"])
                _require(coverage["target_gap_met"], "output_sampling_limited")
                actual = _read(Path(auth["allowed_render_directory"]) / "render_result.json")
                from .microclip_v2_contracts import validate_review
                validate_review(value, intent, actual["measured_duration_s"])
                _require(all(value.get(k) == "pass" for k in ("status", "key_moment_selection", "economy", "readability"))
                         and all(row["verdict"] == "pass" for row in value["obligation_checks"]), "model_review_did_not_pass")
                for index in range(len(self._blind_pages())):
                    _, page = self._received(f"mc2_blind_page_{index}")
                    _require(page["observation_status"] == "complete", "output_observation_incomplete")
                _require(self.data["artifacts"].get("mc2_render_claim"), "quality_pass_requires_render")
            if payload.get("final_video"):
                final = Path(auth["allowed_render_directory"]) / "final.mp4"
                actual = _read(final.parent / "render_result.json")
                _require(self.data["artifacts"].get("mc2_render_claim") and Path(payload["final_video"]).resolve() == final
                         and payload["final_sha256"] == actual["sha256"] == sha256_file(final), "final_actual_video_required")
            folder = Path(auth["execution_directory"])
            recovered = auth["infrastructure_resume"] is not None
            known = auth.get("known_failure_resume") is not None
            dispatch = auth.get("known_failure_dispatch_resume") is not None
            boundary = auth.get("boundary_resume") is not None
            metadata = auth.get("boundary_metadata_resume") is not None
            path = folder / ("result_boundary_metadata_recovered.json" if metadata else "result_boundary_recovered.json" if boundary else "result_dispatch_recovered.json" if dispatch else "result_network_recovered.json" if known else "result_recovered.json" if recovered else "result.json")
            _require(not path.exists() or _read(path) == payload, "result_already_bound")
            if not path.exists():
                write_json(path, payload)
            receipt = {"policy": POLICY, "result_path": str(path), "model_status": payload.get("status"),
                       "completed_files": [{"path": str(f), "sha256": sha256_file(f)} for f in sorted(folder.rglob("*"))
                                           if f.is_file() and f.suffix != ".tmp"]}
            artifact = "mc2_boundary_metadata_result" if metadata else "mc2_boundary_result" if boundary else "mc2_dispatch_result" if dispatch else "mc2_network_result" if known else "mc2_recovered_result" if recovered else "mc2_result"
            if self.data["artifacts"].get(artifact):
                _require(_read(self.data["artifacts"][artifact][0]["path"]) == receipt, "result_receipt_changed")
            else:
                self.set_artifact(artifact, receipt)
            return path

    def usage(self):
        self.assert_protected()
        return {**super().usage(), "max_requests": None, "base_max_requests": self.authorization["base_request_limit"],
                "microclip_v2_baseline_requests": BASELINE}
