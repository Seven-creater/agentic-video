"""One independently authorized, model-owned microcut of an existing parent slot.

This adapter preserves the settled 207-call prefix and never reopens old stages.
"""
from copy import deepcopy
from pathlib import Path
import re
from threading import RLock

from .pipeline import _read
from .forward_slot_budget import _history, _prefix_sha
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, _timestamp, file_lock, json_sha, scope_fingerprint, write_json

POLICY = ARTIFACT = "microclip_finecut_v1"
BASELINE = 207
RESUME_ARTIFACT = "mc_infrastructure_resume"
RESUME_POLICY = "microclip_grid_publication_resume_v1"
RESUME_BASELINE = 209
STAGES = r"^mc_(observe_[0-3]|motion|anchors|edges_[0-2]|plan|blind|review)(_repair)?$"
_MUTEX = RLock()


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("microclip:" + reason)


def _stage(name):
    match = re.fullmatch(STAGES, name)
    _require(match is not None, "stage_not_authorized")
    return match[1], bool(match[2])


def _files(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def _known_publication_stop(output, data, auth):
    """Recognize this one pre-submission CPU failure, never a model failure."""
    added = data["calls"][BASELINE:RESUME_BASELINE]
    _require(len(added) == 2 and [c["name"] for c in added] == ["mc_observe_0", "mc_observe_1"]
             and all(c["status"] == "received" and not c.get("repair_of") for c in added), "resume_received_209_prefix_required")
    folder = Path(auth["execution_directory"])
    result_path = folder / "result.json"
    rows = data["artifacts"].get("mc_result", [])
    _require(len(rows) == 1, "resume_old_result_required")
    receipt_path = Path(rows[0]["path"]).resolve(strict=True)
    receipt, result = _read(receipt_path), _read(result_path)
    _require(receipt["policy"] == POLICY and Path(receipt["result_path"]).resolve() == result_path
             and receipt["model_status"] == result.get("status") == "stopped"
             and result.get("model_quality_gate_passed") is False and result.get("final_video") is None
             and result.get("final_sha256") is None and result.get("measured_duration_s") is None
             and result.get("observed_frames") == 12 and result.get("observation_rounds") == 2, "resume_cpu_stop_only")
    match = re.fullmatch(r"\[WinError 5\].*?: '(.+)' -> '(.+)'", result.get("error", ""))
    _require(match is not None, "resume_grid_publication_error_required")
    paths = [Path(re.sub(r"\\+", lambda _: "\\", value)).resolve() for value in match.groups()]
    temporary, destination = paths
    token = re.fullmatch(r"\._grid_([a-f0-9]{12})_[a-f0-9]{8}", temporary.name)
    _require(token and temporary.parent == destination.parent == folder / "frames" / "view_2"
             and re.fullmatch(r"grid_[a-f0-9]{20}", destination.name)
             and destination.name[5:17] == token[1] and temporary.is_dir(), "resume_view_2_publication_only")
    from .contracts import parse_model_json
    from .microclip_contracts import validate_observation
    catalog = {}
    for index, call in enumerate(added):
        call_folder = output / "calls" / call["id"]
        descriptor = _read(data["artifacts"][f"mc_input_mc_observe_{index}"][0]["path"])
        shown = _read(descriptor["lineage_path"])["frames"]
        catalog.update({f["frame_id"]: f for f in shown})
        value = _read(call_folder / "parsed.json")
        raw = "\n".join(c["text"] for c in _read(call_folder / "response.json")["result"]["content"] if c.get("type") == "text")
        _require(not (call_folder / "protocol_failure.json").exists() and parse_model_json(raw) == value,
                 "resume_received_observation_required")
        validate_observation(value, shown, catalog, first=index == 0)
        _require(value["next_observation"]["action"] == "zoom", "resume_original_zoom_required")
    _require(len(catalog) == result["observed_frames"], "resume_observed_frame_count_changed")
    return result_path, receipt_path, result["error"]


def _infrastructure_resume(output, data, auth, *, force=False):
    rows = data["artifacts"].get(RESUME_ARTIFACT, [])
    if not rows:
        return None
    _require(len(rows) == 1, "one_infrastructure_resume_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    value = _read(path)
    result_path, receipt_path, error = _known_publication_stop(output, data, auth)
    _require(path.is_relative_to(output / "artifacts") and json_sha(value) == rows[0]["sha256"]
             and value["policy"] == RESUME_POLICY and value["task_id"] == data["task_id"]
             and value["original_authorization_sha256"] == json_sha(auth)
             and value["baseline_request_count"] == RESUME_BASELINE
             and len(data["calls"]) >= RESUME_BASELINE
             and value["prefix_calls_sha256"] == json_sha(data["calls"][:RESUME_BASELINE])
             and value["result_path"] == str(result_path) and value["receipt_path"] == str(receipt_path)
             and value["known_cpu_error"] == error and value["next_stage"] == "mc_observe_2"
             and value["new_renders"] == 0 and value["reuse_original_unused_render"] is True
             and not any(value["baseline_artifacts"].get(key) for key in ("mc_render_claim", "mc_recovered_result", "mc_input_mc_observe_2"))
             and set(value["baseline_call_files"]) == {c["id"] for c in data["calls"][BASELINE:RESUME_BASELINE]}
             and bool(value["user_instruction"].strip()), "infrastructure_resume_binding_changed")
    _require(sha256_file(result_path, force=force) == value["result_sha256"]
             and sha256_file(receipt_path, force=force) == value["receipt_sha256"], "old_stop_result_changed")
    for key, previous in value["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == previous, "resume_historical_artifact_changed")
    for ident, previous in value["baseline_call_files"].items():
        _require(_files(output / "calls" / ident) == previous, "resume_historical_call_files_changed")
    for proof in value["protected_files"]:
        _require(sha256_file(proof["path"], force=force) == proof["sha256"], "resume_historical_bytes_changed")
    for proof in value["journal_prefixes"]:
        _require(_prefix_sha(proof["path"], proof["bytes"], force=force) == proof["sha256"], "resume_journal_prefix_changed")
    return value


def record_infrastructure_resume(output, user_instruction):
    """Append a narrowly scoped continuation; no requests or new render grant."""
    output = Path(output).resolve(strict=True)
    auth = get_auth(output, force=True)
    if auth["infrastructure_resume"] is not None:
        return auth
    data = _read(output / "library_state.json")
    _require(isinstance(user_instruction, str) and bool(user_instruction.strip())
             and data["request_count"] == len(data["calls"]) == RESUME_BASELINE
             and not any(data["artifacts"].get(key) for key in ("mc_render_claim", "mc_recovered_result", "mc_input_mc_observe_2"))
             and not Path(auth["allowed_render_directory"]).exists(), "resume_unused_pre_render_grant_required")
    result_path, receipt_path, error = _known_publication_stop(output, data, auth)
    files, prefixes = _history(output)
    original = _read(auth["authorization_path"])
    payload = {"policy": RESUME_POLICY, "task_id": data["task_id"],
        "original_authorization_sha256": json_sha(original), "baseline_request_count": RESUME_BASELINE,
        "prefix_calls_sha256": json_sha(data["calls"]), "baseline_artifacts": deepcopy(data["artifacts"]),
        "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"][BASELINE:]},
        "result_path": str(result_path), "result_sha256": sha256_file(result_path),
        "receipt_path": str(receipt_path), "receipt_sha256": sha256_file(receipt_path),
        "known_cpu_error": error, "next_stage": "mc_observe_2", "new_renders": 0,
        "reuse_original_unused_render": True, "protected_files": files, "journal_prefixes": prefixes,
        "user_instruction": user_instruction}
    MicroclipState(output).set_artifact(RESUME_ARTIFACT, payload)
    return get_auth(output, force=True)


def record_authorization(output, user_instruction):
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(ARTIFACT):
        return get_auth(output)
    from .parent_cut_state import get_auth as parent_auth
    old = parent_auth(output, force=True)
    _require(bool(user_instruction.strip()) and data["request_count"] == len(data["calls"]) == BASELINE
             and all(c["status"] in {"received", "uncertain"} for c in data["calls"])
             and all(data["artifacts"].get(f"pc_result_{i}") for i in (0, 3)), "settled_207_and_completed_parents_required")
    parent = next(p for p in old["parents"] if p["round"] == 0)
    outline = _read(old["outline_paths"]["0"])
    _require(outline["parent_sha256"] == parent["sha256"] == sha256_file(parent["path"]), "parent_changed")
    slot = next((s for s in outline["slots"] if 5 <= s["end_s"] - s["start_s"] <= 15), None)
    _require(slot and 0 <= slot["start_s"] < slot["end_s"] <= parent["duration_s"], "small_slot_required")
    base = output / "artifacts" / POLICY
    knowledge = base / "GLM_MICROCLIP_FINECUT.md"
    text = (Path(__file__).with_name("craft_knowledge") / "GLM_MICROCLIP_FINECUT.md").read_text(encoding="utf-8")
    _require(not knowledge.exists() or knowledge.read_text(encoding="utf-8") == text, "knowledge_already_bound")
    if not knowledge.exists():
        knowledge.parent.mkdir(parents=True, exist_ok=True)
        knowledge.write_text(text, encoding="utf-8")
    files, prefixes = _history(output)
    p = {"policy": POLICY, "task_id": data["task_id"], "original_output": str(output),
        "input_lock_sha256": json_sha(data["input_lock"]), "base_request_limit": data["max_requests"],
        "baseline_request_count": BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
        "baseline_artifacts": deepcopy(data["artifacts"]), "baseline_policy_version": data["policy_version"],
        "baseline_render_directories": sorted(d.name for d in output.glob("render_*") if d.is_dir()),
        "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"]},
        "protected_files": files, "journal_prefixes": prefixes,
        "original_authorization_path": old["authorization_path"], "original_authorization_sha256": old["authorization_sha256"],
        "parent": {k: parent[k] for k in ("path", "sha256", "duration_s")}, "slot": deepcopy(slot),
        "outline_path": old["outline_paths"]["0"], "execution_directory": str(base),
        "allowed_render_directory": str(base / "render"), "knowledge_path": str(knowledge), "knowledge_sha256": sha256_file(knowledge),
        "unknown_inputs": deepcopy(old["unknown_inputs"]), "user_instruction": user_instruction,
        "max_concurrency": 1, "new_renders": 1, "repairs_per_stage": 1, "numeric_total_request_limit": None,
        "automatic_round_loops": False, "goal_resumed": False, "stage_pattern": STAGES,
        "reference_and_library_new_inputs_forbidden": True}
    LibraryState(output, data["input_lock"], max_requests=data["max_requests"]).set_artifact(ARTIFACT, p)
    return get_auth(output)


def get_auth(output, *, force=False):
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
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
             and p["reference_and_library_new_inputs_forbidden"] is True
             and Path(p["execution_directory"]) == output / "artifacts" / POLICY
             and Path(p["allowed_render_directory"]) == Path(p["execution_directory"]) / "render"
             and sha256_file(p["knowledge_path"], force=force) == p["knowledge_sha256"]
             and sha256_file(p["original_authorization_path"], force=force) == p["original_authorization_sha256"], "scope_changed")
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
    names, pending = {}, 0
    for call in data["calls"][BASELINE:]:
        _, repair = _stage(call["name"])
        _require(call["name"] not in names and call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "new_stage_or_status_invalid")
        req = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(req) == call["request_sha256"], "new_request_changed")
        if repair:
            old = names.get(call["name"][:-7])
            _require(old and old["status"] == "received" and call.get("repair_of") == old["id"]
                     and list(names)[-1] == old["name"], "sole_repair_binding")
            original = _read(output / "calls" / old["id"] / "request.json")
            _require(original["media_sha256"] == req["media_sha256"] and scope_fingerprint(original["observation_scope"]) == scope_fingerprint(req["observation_scope"]), "repair_input_changed")
        else:
            _require(not call.get("repair_of"), "original_has_repair")
        if call["status"] == "received":
            _require(json_sha(_read(output / "calls" / call["id"] / "response.json")) == call["response_sha256"], "new_response_changed")
        pending += call["status"] == "submitted"
        names[call["name"]] = call
    _require(pending <= 1, "single_lane_required")
    for key, entries in data["artifacts"].items():
        if key.startswith("mc_"):
            _require(len(entries) == 1 and json_sha(_read(entries[0]["path"])) == entries[0]["sha256"], "new_artifact_changed")
            value = _read(entries[0]["path"])
            if key.startswith("mc_input_"):
                _require(sha256_file(value["media_path"], force=force) == value["media_sha256"]
                         and sha256_file(value["lineage_path"], force=force) == value["lineage_sha256"], "input_bytes_changed")
            for proof in value.get("completed_files", []):
                _require(sha256_file(proof["path"], force=force) == proof["sha256"], "completed_bytes_changed")
    _require(not Path(p["allowed_render_directory"]).exists() or data["artifacts"].get("mc_render_claim"), "render_without_claim")
    resume = _infrastructure_resume(output, data, p, force=force)
    return {**p, "authorization_path": str(path), "authorization_sha256": sha256_file(path, force=force),
            "infrastructure_resume": resume}


class MicroclipState(LibraryState):
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
        for call in reversed(self.data["calls"][BASELINE:]):
            folder = self.output / "calls" / call["id"]
            if call["name"] in (stem, stem + "_repair") and call["status"] == "received" and (folder / "parsed.json").exists():
                value = _read(folder / "parsed.json")
                raw = "\n".join(c["text"] for c in _read(folder / "response.json")["result"]["content"] if c.get("type") == "text")
                _require(not (folder / "protocol_failure.json").exists() and parse_model_json(raw) == value, "parsed_not_original_reply")
                return call, value
        raise LibraryStopped("microclip:predecessor_not_parsed:" + stem)

    def _predecessor(self, kind, added):
        if kind == "observe_0":
            _require(not added, "first_observation_required")
        elif kind.startswith("observe_"):
            _require(not any(_stage(c["name"])[0] in {"motion", "anchors", "plan", "blind", "review"} for c in added), "observe_after_plan")
            self._received("mc_observe_" + str(int(kind[-1]) - 1))
        elif kind == "motion":
            self._received("mc_observe_0")
            self._received("mc_observe_1")
        elif kind == "anchors":
            self._received("mc_motion")
        elif kind.startswith("edges_"):
            self._received("mc_anchors")
            if kind != "edges_0":
                self._received("mc_edges_" + str(int(kind[-1]) - 1))
        elif kind == "plan":
            self._received("mc_anchors")
            for index in range(3):
                self._received("mc_edges_" + str(index))
        elif kind == "blind":
            _require(self.data["artifacts"].get("mc_render_claim"), "render_required")
        elif kind == "review":
            self._received("mc_blind")

    def _check_input(self, name, request):
        kind, _ = _stage(name)
        stem = name.removesuffix("_repair")
        rows = self.data["artifacts"].get("mc_input_" + stem, [])
        _require(len(rows) == 1, "input_descriptor_required")
        d = _read(rows[0]["path"])
        image = kind.startswith(("observe_", "edges_"))
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
        if kind in {"blind", "review"}:
            final = Path(self.authorization["allowed_render_directory"]) / "final.mp4"
            actual = _read(final.parent / "render_result.json")
            _require(self.data["artifacts"].get("mc_render_claim") and scope[0] in {"continuous_window", "complete_file"}
                     and scope[1] == actual["sha256"] == sha256_file(final)
                     and scope[2:] == (0.0, float(actual["measured_duration_s"])), "review_actual_output_only")
            _require(scope == scope_fingerprint(lineage) and lineage["sha256"] == request["media_sha256"]
                     and Path(lineage["path"]).resolve() == media and Path(lineage["source_path"]).resolve() == final, "actual_output_lineage_required")
        else:
            parent, slot = self.authorization["parent"], self.authorization["slot"]
            _require(scope[1] == parent["sha256"] and slot["start_s"] <= scope[2] < scope[3] <= slot["end_s"], "parent_slot_only")
            if image:
                _require(scope[0] == "sparse_contact_sheet", "sparse_image_scope_required")
                from .microclip_frames import verify_grid
                verified = verify_grid(d["lineage_path"], source_path=parent["path"])
                _require(verified["source"]["sha256"] == parent["sha256"] and scope[2:] ==
                         (float(verified["request"]["start_s"]), float(verified["request"]["end_s"]))
                         and Path(verified["grid"]["path"]).resolve() == media
                         and verified["grid"]["sha256"] == request["media_sha256"], "actual_grid_lineage_required")
                if kind.startswith("edges_"):
                    _, anchors = self._received("mc_anchors")
                    key = ("start", "peak", "end")[int(kind[-1])]
                    ident = anchors[key + "_frame_id"]
                    _require(d.get("anchor_frame_id") == ident and d.get("anchor_key") == key, "model_anchor_binding_required")
                    prior_frames = []
                    for name, entries in self.data["artifacts"].items():
                        if name.startswith("mc_input_mc_observe_"):
                            prior_frames.extend(_read(_read(entries[0]["path"])["lineage_path"])["frames"])
                    anchor = next((f for f in prior_frames if f["frame_id"] == ident), None)
                    _require(anchor and max(slot["start_s"], anchor["source_time_s"] - .25) <= scope[2] <= anchor["source_time_s"] < scope[3]
                             <= min(slot["end_s"], anchor["source_time_s"] + .25), "anchor_neighborhood_range_required")
            else:
                _require(scope[0] == "continuous_window" and scope[2:] == (float(slot["start_s"]), float(slot["end_s"])), "full_slot_video_required")
                _require(scope == scope_fingerprint(lineage) and lineage["sha256"] == request["media_sha256"]
                         and Path(lineage["path"]).resolve() == media and Path(lineage["source_path"]).resolve() == Path(parent["path"]).resolve(), "normal_speed_slot_lineage_required")

    def begin_call(self, name, request, *, repair_of=None):
        with _MUTEX, file_lock(self.lock_path):
            self.assert_protected()
            kind, repair = _stage(name)
            added = self.data["calls"][BASELINE:]
            ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            _require(all(c["status"] == "received" for c in added), "new_unknown_or_pending")
            resumed = self.authorization["infrastructure_resume"] is not None
            _require((not self.data["artifacts"].get("mc_result") or resumed)
                     and not self.data["artifacts"].get("mc_recovered_result")
                     and not any(c["name"] == name for c in added), "finished_or_repeated_stage")
            if resumed and len(self.data["calls"]) == RESUME_BASELINE:
                _require(name == self.authorization["infrastructure_resume"]["next_stage"], "resume_next_stage_required")
            if repair:
                _require(added and added[-1]["name"] == name[:-7] and ident == added[-1]["id"] and not added[-1].get("repair_of")
                         and not any(c.get("repair_of") == ident for c in added), "sole_repair_required")
                folder = self.output / "calls" / ident
                _require((folder / "protocol_failure.json").exists() and not (folder / "parsed.json").exists(), "known_format_failure_required")
                old = _read(folder / "request.json")
                _require(old["media_sha256"] == request["media_sha256"] and scope_fingerprint(old["observation_scope"]) == scope_fingerprint(request["observation_scope"]), "repair_input_changed")
            else:
                _require(ident is None, "original_has_repair")
                if added:
                    self._received(added[-1]["name"].removesuffix("_repair"))
                self._predecessor(kind, added)
            self._check_input(name, request)
            digest = json_sha(request)
            _require(not any(c["request_sha256"] == digest for c in self.data["calls"]), "request_replay")
            count = self.data["request_count"] + 1
            folder = self.output / "calls" / f"glm_{count:03d}_{name}"
            folder.mkdir(parents=True, exist_ok=False)
            write_json(folder / "request.json", request)
            call = {"id": folder.name, "name": name, "status": "submitted", "submitted_at": _timestamp(), "request_sha256": digest, "usage": {}, "repair_of": ident}
            self.data["calls"].append(call)
            self.data["request_count"] = count
            self._save()
            return deepcopy(call), folder

    def _new(self, call):
        self.assert_protected()
        ident = call["id"] if isinstance(call, dict) else call
        _require(any(c["id"] == ident for c in self.data["calls"][BASELINE:]), "historical_call_read_only")

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
            _require(name.startswith("mc_"), "old_artifact_read_only")
            if self.data["artifacts"].get(name):
                row = self.data["artifacts"][name][0]
                _require(_read(row["path"]) == value, "existing_artifact_read_only")
                return Path(row["path"])
            return super().set_artifact(name, value)

    def claim_render(self, plan, source=None):
        with _MUTEX:
            auth = self.assert_protected()
            _require(all(c["status"] == "received" for c in self.data["calls"][BASELINE:]), "new_unknown_or_pending")
            _require(not self.data["artifacts"].get("mc_recovered_result")
                     and (not self.data["artifacts"].get("mc_result") or auth["infrastructure_resume"] is not None),
                     "finished_experiment_read_only")
            call, model_plan = self._received("mc_plan")
            from .microclip_contracts import validate_plan
            from .microclip_frames import verify_grid
            frames = {}
            for name, rows in self.data["artifacts"].items():
                if name.startswith(("mc_input_mc_observe_", "mc_input_mc_edges_")):
                    descriptor = _read(rows[0]["path"])
                    manifest = verify_grid(descriptor["lineage_path"], source_path=auth["parent"]["path"])
                    for frame in manifest["frames"]:
                        _require(frame["frame_id"] not in frames or frames[frame["frame_id"]] == frame, "frame_id_conflict")
                        frames[frame["frame_id"]] = frame
            _require(source and source["sha256"] == auth["parent"]["sha256"] == sha256_file(source["path"])
                     and Path(source["path"]).resolve() == Path(auth["parent"]["path"]).resolve(), "actual_parent_source_required")
            _, anchors = self._received("mc_anchors")
            boundary_ids = {anchors[k + "_frame_id"] for k in ("start", "peak", "end")}
            for index in range(3):
                self._received(f"mc_edges_{index}")
                descriptor = _read(self.data["artifacts"][f"mc_input_mc_edges_{index}"][0]["path"])
                boundary_ids.update(f["frame_id"] for f in _read(descriptor["lineage_path"])["frames"])
            expected = validate_plan(source, auth["slot"], model_plan, frames, boundary_ids)
            _require(plan == expected, "compiled_plan_differs_from_model")
            directory = Path(auth["allowed_render_directory"])
            payload = {"policy": POLICY, "directory": str(directory), "max_renders": 1, "plan_sha256": json_sha(plan),
                "model_call_id": call["id"], "model_response_sha256": call["response_sha256"], "model_plan_sha256": json_sha(model_plan)}
            if self.data["artifacts"].get("mc_render_claim"):
                _require(_read(self.data["artifacts"]["mc_render_claim"][0]["path"]) == payload, "render_claim_changed")
            else:
                _require(not directory.exists(), "render_exists_without_claim")
                self.set_artifact("mc_render_claim", payload)
            return directory

    def finish(self, payload):
        with _MUTEX:
            auth = self.assert_protected()
            _require(not any(c["status"] == "submitted" for c in self.data["calls"][BASELINE:]), "new_call_pending")
            if payload.get("model_quality_gate_passed") is True:
                self._received("mc_blind")
                _, value = self._received("mc_review")
                _require(all(value.get(k) == "pass" for k in ("status", "key_moment_selection", "economy", "readability")), "model_review_did_not_pass")
                _require(self.data["artifacts"].get("mc_render_claim"), "quality_pass_requires_render")
            if payload.get("final_video"):
                final = Path(auth["allowed_render_directory"]) / "final.mp4"
                actual = _read(final.parent / "render_result.json")
                _require(self.data["artifacts"].get("mc_render_claim")
                         and Path(payload["final_video"]).resolve() == final
                         and payload["final_sha256"] == actual["sha256"] == sha256_file(final), "final_actual_video_required")
            folder = Path(auth["execution_directory"])
            recovered = auth["infrastructure_resume"] is not None
            path = folder / ("result_recovered.json" if recovered else "result.json")
            _require(not path.exists() or _read(path) == payload, "result_already_bound")
            if not path.exists():
                write_json(path, payload)
            receipt = {"policy": POLICY, "result_path": str(path), "model_status": payload.get("status"),
                "completed_files": [{"path": str(f), "sha256": sha256_file(f)} for f in sorted(folder.rglob("*")) if f.is_file() and f.suffix != ".tmp"]}
            artifact = "mc_recovered_result" if recovered else "mc_result"
            if self.data["artifacts"].get(artifact):
                _require(_read(self.data["artifacts"][artifact][0]["path"]) == receipt, "result_receipt_changed")
            else:
                self.set_artifact(artifact, receipt)
            return path

    def usage(self):
        self.assert_protected()
        return {**super().usage(), "max_requests": None, "base_max_requests": self.authorization["base_request_limit"], "microclip_baseline_requests": BASELINE}
