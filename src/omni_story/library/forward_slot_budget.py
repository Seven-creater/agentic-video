"""One separately bound reconstruction of each existing fine-cut parent.

The old adapter is validated only before activation. Later work is isolated by
the immutable 191-call prefix, not by exceptions to its exhausted stages.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
from threading import RLock
from types import SimpleNamespace

from .pipeline import _read
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, _timestamp, file_lock, json_sha, scope_fingerprint, write_json

POLICY = ARTIFACT = "sf_fact_grounded_reconstruction_v1"
BASELINE = 191
STAGES = r"^sfv2_(0|3)_(reconstruct|slice_[a-f0-9]{16}|compare|blind|economy|review)(_repair)?$"
FROZEN_IDS = {"glm_004_coarse_978d5360_01", "glm_131_active_10_draft", "glm_166_sf_3_source_feedback_replan_v1"}
_MUTEX = RLock()
_PREFIX_PROOFS = {}


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("forward_slot:" + reason)


def _stage(name):
    match = re.fullmatch(STAGES, name)
    _require(match is not None, "stage_not_authorized")
    return int(match[1]), match[2], bool(match[3])


def _prefix_sha(path, size, *, force=False):
    path = Path(path).resolve(strict=True)
    stamp = lambda: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns,
                     path.stat().st_ino, path.stat().st_dev)
    before = stamp()
    key = (str(path), size)
    if not force and _PREFIX_PROOFS.get(key, (None,))[0] == before:
        _require(stamp() == before, "journal_changed_during_cached_proof")
        return _PREFIX_PROOFS[key][1]
    h = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = size
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            _require(bool(chunk), "journal_prefix_truncated")
            remaining -= len(chunk)
            h.update(chunk)
    result = h.hexdigest()
    # A concurrent append changes stat without altering the bound prefix. Cache
    # only stable reads; expected prefix SHA still validates every uncached read.
    if stamp() == before:
        _PREFIX_PROOFS[key] = (before, result)
    return result


def _history(output):
    files, prefixes = [], []
    for path in sorted(output.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(output)
        if (path.suffix in {".lock", ".tmp"} or relative.parts[0] == "library_state.json"
                or path.name in {"mcp_stop", "mcp_ready.json", "mcp_tools.json", "current_status.json"}
                or path.name.startswith("mcp_current")):
            continue
        if path.name.startswith(("mcp_http", "mcp_server")):
            size = path.stat().st_size
            prefixes.append({"path": str(path), "bytes": size, "sha256": _prefix_sha(path, size)})
        else:
            files.append({"path": str(path), "sha256": sha256_file(path)})
    return files, prefixes


def record_authorization(output, user_instruction):
    """Register permission once; does not make a request or resume the Goal."""
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(ARTIFACT):
        return get_auth(output)
    from .slot_finecut_budget import load_authorization, _unknown_inputs
    from .goal_feedback_continuation import _exhausted_slice_scopes
    grant = load_authorization(output)
    _require(isinstance(user_instruction, str) and bool(user_instruction.strip()), "explicit_instruction_required")
    _require(data["request_count"] == len(data["calls"]) == BASELINE
             and all(c["status"] in {"received", "uncertain"} for c in data["calls"])
             and {c["id"] for c in data["calls"] if c["status"] == "uncertain"} == FROZEN_IDS,
             "activation_requires_settled_191_prefix")
    _require(not any(data["artifacts"].get(f"sf_{p}_render_claim") for p in (0, 3))
             and not any(Path(p).exists() for p in grant["allowed_render_directories"]), "original_render_grant_used")
    files, prefixes = _history(output)
    # The base grant also protects actual inputs outside the run directory (for
    # example the fixed reference). Keep those byte proofs, not only their JSON.
    by_path = {str(Path(row["path"]).resolve()): row for row in files}
    for row in grant.get("protected_files", []):
        path = str(Path(row["path"]).resolve(strict=True))
        _require(path not in by_path or by_path[path]["sha256"] == row["sha256"], "base_input_proof_conflict")
        by_path[path] = {"path": path, "sha256": row["sha256"]}
    files = sorted(by_path.values(), key=lambda row: row["path"])
    exhausted = _exhausted_slice_scopes(SimpleNamespace(output=output, data=data))
    # Include exhausted parent observations as well as exact source observations.
    for call in data["calls"]:
        if call.get("repair_of") or "_facts_" not in call["name"]:
            continue
        repairs = [c for c in data["calls"] if c.get("repair_of") == call["id"]]
        if len(repairs) == 1 and all(c["status"] == "received" and not
                (output / "calls" / c["id"] / "parsed.json").exists() for c in [call, *repairs]):
            request = _read(output / "calls" / call["id"] / "request.json")
            if request.get("observation_scope"):
                exhausted.append({"scope": request["observation_scope"], "original_call": call["id"],
                                  "repair_call": repairs[0]["id"], "status": "known_protocol_exhausted"})
    base = Path(grant["execution_directory"]) / "fact_grounded_v1"
    policy = {"policy": POLICY, "task_id": data["task_id"], "original_output": str(output),
        "input_lock_sha256": json_sha(data["input_lock"]), "base_request_limit": data["max_requests"],
        "baseline_request_count": BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
        "baseline_call_files": {c["id"]: sorted(str(p.relative_to(output / "calls" / c["id"]))
            for p in (output / "calls" / c["id"]).rglob("*") if p.is_file()) for c in data["calls"]},
        "baseline_artifacts": deepcopy(data["artifacts"]), "baseline_policy_version": data["policy_version"],
        "baseline_render_directories": sorted(p.name for p in output.glob("render_*") if p.is_dir()),
        "original_authorization_path": grant["authorization_path"],
        "original_authorization_sha256": grant["authorization_sha256"],
        "old_execution_directory": grant["execution_directory"], "preparation_path": grant["preparation_path"],
        "reference_methods_path": grant["reference_methods_path"], "knowledge_path": grant["knowledge_path"],
        "execution_directory": str(base), "allowed_render_directories": [str(base / f"render_{p}" / "render") for p in (0, 3)],
        "user_instruction": user_instruction, "unknown_inputs": _unknown_inputs(output, data["calls"]),
        "exhausted_source_inputs": exhausted, "protected_files": files, "journal_prefixes": prefixes,
        "parent_rounds": [0, 3], "max_concurrent_parents": 2, "max_slices_per_parent": 32,
        "renders_per_parent": 1, "repairs_per_stage": 1, "new_unique_windows": 0,
        "numeric_total_request_limit": None, "automatic_round_loops": False, "stage_pattern": STAGES,
        "goal_resumed": False, "source_fact_bodies_immutable": True,
        "new_task": "Independent atomic fact-grounded reconstruction; no third repair of 169 or 191."}
    state = LibraryState(output, data["input_lock"], max_requests=data["max_requests"])
    state.set_artifact(ARTIFACT, policy)
    return get_auth(output)


def get_auth(output, *, force=False):
    """Recheck immutable history and all new-stage records, without activation."""
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    entries = data["artifacts"].get(ARTIFACT, [])
    _require(len(entries) == 1, "one_forward_authorization_required")
    path = Path(entries[0]["path"]).resolve(strict=True)
    policy = _read(path)
    _require(path.is_relative_to(output / "artifacts") and json_sha(policy) == entries[0]["sha256"]
             and policy["policy"] == POLICY and policy["task_id"] == data["task_id"]
             and Path(policy["original_output"]).resolve() == output
             and policy["input_lock_sha256"] == json_sha(data["input_lock"])
             and policy["base_request_limit"] == data["max_requests"]
             and policy["baseline_policy_version"] == data["policy_version"]
             and policy["baseline_request_count"] == BASELINE <= len(data["calls"])
             and data["request_count"] == len(data["calls"])
             and policy["prefix_calls_sha256"] == json_sha(data["calls"][:BASELINE]), "policy_or_baseline_changed")
    _require(policy["parent_rounds"] == [0, 3] and policy["max_concurrent_parents"] == 2
             and policy["max_slices_per_parent"] == 32 and policy["renders_per_parent"] == policy["repairs_per_stage"] == 1
             and policy["new_unique_windows"] == 0 and policy["numeric_total_request_limit"] is None
             and policy["automatic_round_loops"] is policy["goal_resumed"] is False
             and policy["stage_pattern"] == STAGES, "forward_scope_changed")
    grant = _read(policy["original_authorization_path"])
    _require(sha256_file(policy["original_authorization_path"], force=force) == policy["original_authorization_sha256"]
             and grant["renders_per_parent"] == grant["repairs_per_stage"] == 1
             and grant["parent_rounds"] == [0, 3]
             and Path(policy["execution_directory"]) == Path(grant["execution_directory"]) / "fact_grounded_v1"
             and policy["allowed_render_directories"] == [str(Path(policy["execution_directory"]) / f"render_{p}" / "render") for p in (0, 3)],
             "original_grant_or_new_directory_changed")
    _require(not any(Path(p).exists() for p in grant["allowed_render_directories"])
             and sorted(p.name for p in output.glob("render_*") if p.is_dir()) == policy["baseline_render_directories"],
             "unapproved_original_render_directory")
    for key, rows in policy["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == rows, "old_artifacts_changed:" + key)
    for call_id, paths in policy["baseline_call_files"].items():
        _require(sorted(str(p.relative_to(output / "calls" / call_id)) for p in
                        (output / "calls" / call_id).rglob("*") if p.is_file()) == paths,
                 "historical_call_file_added_or_removed")
    for row in policy["protected_files"]:
        _require(sha256_file(row["path"], force=force) == row["sha256"], "old_file_changed:" + row["path"])
    for row in policy["journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "original_journal_prefix_changed")
    unknowns = [c for c in data["calls"][:BASELINE] if c["status"] == "uncertain"]
    _require({c["id"] for c in unknowns} == FROZEN_IDS, "frozen_unknowns_changed")
    for call in unknowns:
        _require(not (output / "calls" / call["id"] / "response.json").exists()
                 and not (output / "calls" / call["id"] / "parsed.json").exists(), "unknown_response_fabricated")
    names, last, pending = {}, {}, []
    for call in data["calls"][BASELINE:]:
        parent, kind, repair = _stage(call["name"])
        _require(call["name"] not in names and call["status"] in {"received", "submitted", "uncertain", "failed_known"}, "new_stage_or_status_invalid")
        if repair:
            original = names.get(call["name"][:-7])
            _require(original is not None and original["status"] == "received" and
                     call.get("repair_of") == original["id"] and last.get(parent) == original["name"], "repair_binding_changed")
        else:
            _require(not call.get("repair_of"), "original_has_repair_parent")
        request = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(request) == call["request_sha256"], "new_request_changed")
        if repair:
            old = _read(output / "calls" / call["repair_of"] / "request.json")
            _require(old["media_sha256"] == request["media_sha256"] and scope_fingerprint(old["observation_scope"]) ==
                     scope_fingerprint(request["observation_scope"]), "repair_media_changed")
        if call["status"] == "received":
            _require(json_sha(_read(output / "calls" / call["id"] / "response.json")) == call["response_sha256"], "new_response_changed")
        if call["status"] == "submitted":
            pending.append(parent)
        names[call["name"]], last[parent] = call, call["name"]
    _require(len(pending) <= 2 and len(set(pending)) == len(pending), "parallel_pending_limit")
    for parent, directory in zip((0, 3), policy["allowed_render_directories"], strict=True):
        _require(not data["artifacts"].get(f"sf_{parent}_render_claim"), "old_adapter_render_claim_conflict")
        rows = data["artifacts"].get(f"sfv2_{parent}_render_claim", [])
        _require(len(rows) <= 1 and (not Path(directory).exists() or bool(rows)), "render_without_claim")
        if rows:
            claim = _read(rows[0]["path"])
            _require(json_sha(claim) == rows[0]["sha256"] and claim["directory"] == directory
                     and claim["parent_round"] == parent and claim["max_renders"] == 1
                     and sha256_file(claim["source_manifest_path"], force=force) == claim["source_manifest_sha256"]
                     and sha256_file(claim["plan_path"], force=force) == claim["plan_file_sha256"], "render_claim_changed")
    for name, rows in data["artifacts"].items():
        if not name.startswith("sfv2_"):
            continue
        _require(len(rows) == 1, "new_artifact_repeated")
        artifact = _read(rows[0]["path"])
        _require(json_sha(artifact) == rows[0]["sha256"], "new_artifact_changed")
        if name.startswith("sfv2_result_"):
            _require(sha256_file(artifact["result_path"], force=force) == artifact["result_sha256"], "completed_result_changed")
            for proof in artifact["completed_files"]:
                _require(sha256_file(proof["path"], force=force) == proof["sha256"], "completed_file_changed")
    return {**policy, "authorization_path": str(path), "authorization_sha256": sha256_file(path, force=force)}


class ForwardSlotState(LibraryState):
    def __init__(self, output):
        self.authorization = get_auth(output)
        saved = _read(Path(output) / "library_state.json")
        super().__init__(output, saved["input_lock"], max_requests=saved["max_requests"])
        self.max_requests = float("inf")

    def assert_protected(self):
        with _MUTEX:
            self.authorization = get_auth(self.output)
            self._reload()
            return self.authorization

    def _received(self, name):
        original = next((c for c in self.data["calls"][BASELINE:] if c["name"] == name), None)
        if original:
            repaired = next((c for c in self.data["calls"][BASELINE:] if c.get("repair_of") == original["id"]), None)
            for call in (repaired, original):
                if call and call["status"] == "received" and (self.output / "calls" / call["id"] / "parsed.json").exists():
                    _require(not (self.output / "calls" / call["id"] / "protocol_failure.json").exists(),
                             "failed_reply_cannot_be_relabelled_parsed")
                    value = _read(self.output / "calls" / call["id"] / "parsed.json")
                    from .contracts import parse_model_json
                    reply = _read(self.output / "calls" / call["id"] / "response.json")
                    raw = "\n".join(c["text"] for c in reply["result"]["content"] if c.get("type") == "text")
                    _require(parse_model_json(raw) == value, "parsed_body_differs_from_original_reply")
                    return call, value
        raise LibraryStopped("forward_slot:predecessor_not_parsed:" + name)

    def _check_input(self, name, request):
        _, kind, repair = _stage(name)
        media = Path(request.get("arguments", {}).get("video_source", "")).resolve(strict=True)
        _require(request.get("provider") == "official_vision_mcp_in_codex" and request.get("tool") == "analyze_video"
                 and sha256_file(media) == request.get("media_sha256"), "official_bound_video_required")
        scope = scope_fingerprint(request.get("observation_scope"))
        lineage = media.parent / "lineage.json"
        if lineage.exists():
            _require(scope == scope_fingerprint(_read(lineage)), "actual_lineage_changed")
        for lost in self.authorization["unknown_inputs"]:
            _require(json_sha(request) != lost["request_sha256"] and request["media_sha256"] != lost["media_sha256"]
                     and scope != scope_fingerprint(lost["scope"]), "unknown_input_replay")
        if kind.startswith("slice_") and not repair:
            _require(not any(scope == scope_fingerprint(row["scope"]) for row in self.authorization["exhausted_source_inputs"]),
                     "exhausted_source_observation")

    def begin_call(self, name, request, *, repair_of=None):
        with _MUTEX, file_lock(self.lock_path):
            auth = self.assert_protected()
            parent, kind, repair = _stage(name)
            added = self.data["calls"][BASELINE:]
            _require(not any(c["status"] not in {"received", "submitted"} for c in added), "new_unknown_or_failure_stops_all")
            own = [c for c in added if _stage(c["name"])[0] == parent]
            _require(not any(c["status"] == "submitted" for c in own), "same_parent_pending")
            _require(not self.data["artifacts"].get(f"sfv2_result_{parent}"), "parent_already_finished")
            _require(not any(c["name"] == name for c in added), "stage_repetition")
            ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            if repair:
                _require(bool(own) and own[-1]["name"] == name[:-7] and own[-1]["status"] == "received"
                         and ident == own[-1]["id"] and not own[-1].get("repair_of")
                         and not any(c.get("repair_of") == ident for c in added), "sole_repair_required")
                old = _read(self.output / "calls" / ident / "request.json")
                _require(old["media_sha256"] == request["media_sha256"] and scope_fingerprint(old["observation_scope"]) ==
                         scope_fingerprint(request["observation_scope"]), "repair_media_changed")
            else:
                _require(ident is None, "original_has_repair_parent")
                if own:
                    _require((self.output / "calls" / own[-1]["id"] / "parsed.json").exists(), "previous_known_protocol_failure")
                if kind == "reconstruct":
                    _require(not own, "one_reconstruction_only")
                else:
                    self._received(f"sfv2_{parent}_reconstruct")
                    if kind.startswith("slice_"):
                        _require(not any(_stage(c["name"])[1] in {"compare", "blind", "economy", "review"} for c in own), "slice_after_comparison")
                        _require(sum(_stage(c["name"])[1].startswith("slice_") and not c.get("repair_of") for c in own) < auth["max_slices_per_parent"], "slice_limit")
                    elif kind == "compare":
                        _require(not any(_stage(c["name"])[1] in {"blind", "economy", "review"} for c in own), "comparison_after_review")
                    else:
                        self._received(f"sfv2_{parent}_compare")
                        _require(bool(self.data["artifacts"].get(f"sfv2_{parent}_render_claim")), "review_requires_render_claim")
                        if kind in {"economy", "review"}:
                            self._received(f"sfv2_{parent}_blind")
                        if kind == "review":
                            self._received(f"sfv2_{parent}_economy")
            self._check_input(name, request)
            digest = json_sha(request)
            _require(not any(c["request_sha256"] == digest for c in self.data["calls"]), "request_already_recorded")
            count = self.data["request_count"] + 1
            call_id = f"glm_{count:03d}_{name}"
            folder = self.output / "calls" / call_id
            folder.mkdir(parents=True, exist_ok=False)
            write_json(folder / "request.json", request)
            call = {"id": call_id, "name": name, "status": "submitted", "submitted_at": _timestamp(),
                    "request_sha256": digest, "usage": {}, "repair_of": ident}
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

    def set_artifact(self, name, payload):
        with _MUTEX:
            auth = self.assert_protected()
            _require(isinstance(name, str) and name.startswith("sfv2_") and name not in auth["baseline_artifacts"]
                     and not self.data["artifacts"].get(name), "old_or_existing_artifact_read_only")
            return super().set_artifact(name, payload)

    def assert_source_inputs(self, plan, catalog):
        auth = self.assert_protected()
        source_map = {s["source_id"]: s for s in catalog["sources"]}
        locked = {s["source_id"]: s["sha256"] for s in self.data["input_lock"]["library_sources"]}
        _require(0 < len(plan["segments"]) <= auth["max_slices_per_parent"], "final_slice_limit")
        for segment in plan["segments"]:
            source = source_map[segment["source_id"]]
            _require(source["sha256"] == locked[source["source_id"]] == sha256_file(source["path"]), "actual_source_changed")
            scope = {"kind": "continuous_window", "source_sha256": source["sha256"],
                     "source_start_s": segment["source_in_s"], "source_end_s": segment["source_out_s"]}
            _require(not any(scope_fingerprint(scope) == scope_fingerprint(row["scope"]) for row in
                             auth["unknown_inputs"] + auth["exhausted_source_inputs"]), "blocked_exact_source_input")

    def claim_render(self, parent, plan, source_manifest):
        with _MUTEX:
            auth = self.assert_protected()
            _require(parent in (0, 3) and all(c["status"] in {"received", "submitted"} for c in self.data["calls"][BASELINE:])
                     and not any(c["status"] == "submitted" and _stage(c["name"])[0] == parent for c in self.data["calls"][BASELINE:]), "own_call_pending_or_new_unknown")
            compare_call, comparison = self._received(f"sfv2_{parent}_compare")
            _, reconstruction = self._received(f"sfv2_{parent}_reconstruct")
            _require(reconstruction["plan"] == plan, "plan_differs_from_model_reconstruction")
            _require(source_manifest["plan_sha256"] == comparison["plan_sha256"] == json_sha(plan)
                     and source_manifest.get("blockers") == [] and source_manifest["comparison"] == comparison
                     and source_manifest["compare_call_id"] == compare_call["id"], "unbound_or_blocked_comparison")
            segments = {s["segment_id"]: s for s in plan["segments"]}
            bindings = source_manifest["observation_bindings"]
            _require(0 < len(segments) == len(plan["segments"]) == len(bindings) <= 32
                     and {b["segment_id"] for b in bindings} == set(segments)
                     and {s["segment_id"] for s in comparison["segments"]} == set(segments), "all_final_slices_required")
            for checked in comparison["segments"]:
                _require(all(c["status"] == "supported" for c in checked["claim_checks"]), "source_claim_counterevidence")
            for binding in bindings:
                segment = segments[binding["segment_id"]]
                call = self._call(binding["origin_call_id"])
                folder = self.output / "calls" / call["id"]
                observation = _read(folder / "parsed.json")
                request = _read(folder / "request.json")
                _require(call["status"] == "received" and (call["name"].startswith("semantic_slice_") or
                         _stage(call["name"])[1].startswith("slice_")), "neutral_source_facts_required")
                _require(not (folder / "protocol_failure.json").exists(), "failed_source_fact_cannot_be_relabelled")
                _require(call["request_sha256"] == binding["origin_request_sha256"] and call["response_sha256"] == binding["origin_response_sha256"]
                         and sha256_file(folder / "parsed.json") == binding["origin_parsed_file_sha256"]
                         and json_sha(observation) == binding["observation_sha256"]
                         and any(o == observation for o in source_manifest["observations"]), "immutable_source_fact_binding")
                _require(all(segment[k] == observation[k] == binding[k] for k in ("source_id", "source_in_s", "source_out_s"))
                         and observation["source_sha256"] == binding["source_sha256"]
                         and observation["proxy_sha256"] == binding["proxy_sha256"] == request["media_sha256"]
                         == sha256_file(request["arguments"]["video_source"]), "actual_exact_slice_binding")
                checked = next(s for s in comparison["segments"] if s["segment_id"] == segment["segment_id"])
                _require(checked["observation_sha256"] == binding["observation_sha256"], "comparison_fact_hash_changed")
            directory = Path(auth["execution_directory"]) / f"render_{parent}" / "render"
            folder = directory.parent
            folder.mkdir(parents=True, exist_ok=True)
            plan_path, manifest_path = folder / "final_plan.json", folder / "source_manifest.json"
            for path, value in ((plan_path, plan), (manifest_path, source_manifest)):
                _require(not path.exists() or _read(path) == value, "render_input_already_bound")
                if not path.exists():
                    write_json(path, value)
            payload = {"policy": POLICY, "parent_round": parent, "directory": str(directory), "max_renders": 1,
                       "plan_path": str(plan_path), "plan_file_sha256": sha256_file(plan_path),
                       "plan_sha256": json_sha(plan), "source_manifest_path": str(manifest_path),
                       "source_manifest_sha256": sha256_file(manifest_path)}
            key = f"sfv2_{parent}_render_claim"
            if self.data["artifacts"].get(key):
                _require(_read(self.data["artifacts"][key][0]["path"]) == payload, "render_claim_changed")
            else:
                _require(not directory.exists(), "render_exists_without_claim")
                self.set_artifact(key, payload)
            return directory

    def finish(self, parent, payload):
        with _MUTEX:
            auth = self.assert_protected()
            _require(parent in (0, 3) and not any(c["status"] == "submitted" and _stage(c["name"])[0] == parent
                     for c in self.data["calls"][BASELINE:]), "cannot_finish_pending_parent")
            if payload.get("model_quality_gate_passed") is True:
                for kind in ("blind", "economy", "review"):
                    self._received(f"sfv2_{parent}_{kind}")
                _require(bool(self.data["artifacts"].get(f"sfv2_{parent}_render_claim")), "quality_pass_requires_actual_render_claim")
            folder = Path(auth["execution_directory"]) / f"render_{parent}"
            path = folder / "result.json"
            _require(not path.exists() or _read(path) == payload, "result_already_recorded")
            if not path.exists():
                write_json(path, payload)
            key = f"sfv2_result_{parent}"
            receipt = {"policy": POLICY, "parent_round": parent, "result_path": str(path),
                "result_sha256": sha256_file(path), "completed_files": [{"path": str(p), "sha256": sha256_file(p)}
                    for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix != ".tmp"]}
            if self.data["artifacts"].get(key):
                _require(_read(self.data["artifacts"][key][0]["path"]) == receipt, "result_receipt_changed")
            else:
                self.set_artifact(key, receipt)
            return path

    def usage(self):
        self.assert_protected()
        return {**super().usage(), "max_requests": None, "base_max_requests": self.authorization["base_request_limit"],
                "effective_request_limit": None, "forward_baseline_requests": BASELINE}

    def enable_independent_continuation(self):
        raise LibraryStopped("forward_slot:policy_is_bound")
