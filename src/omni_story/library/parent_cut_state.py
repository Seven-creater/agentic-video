"""One direct fine-cut of each immutable, already rendered parent timeline."""
from copy import deepcopy
from pathlib import Path
import re
from threading import RLock

from .pipeline import _read
from .forward_slot_budget import _history, _prefix_sha
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryState, LibraryStopped, _timestamp, file_lock, json_sha, scope_fingerprint, write_json

POLICY = ARTIFACT = "sf_parent_timeline_finecut_v1"
BASELINE = 194
STAGES = r"^pc_(0|3)_(slot_[a-f0-9]{16}|blind|review)(_repair)?$"
_MUTEX = RLock()


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("parent_cut:" + reason)


def _stage(name):
    match = re.fullmatch(STAGES, name)
    _require(match is not None, "stage_not_authorized")
    return int(match[1]), match[2], bool(match[3])


def record_authorization(output, user_instruction):
    output = Path(output).resolve(strict=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(ARTIFACT):
        return get_auth(output)
    from .forward_slot_budget import get_auth as old_auth
    old = old_auth(output)
    _require(bool(user_instruction.strip()) and data["request_count"] == len(data["calls"]) == BASELINE
             and all(c["status"] in {"received", "uncertain"} for c in data["calls"]), "settled_194_and_instruction_required")
    _require(not any(data["artifacts"].get(f"sfv2_{p}_render_claim") or data["artifacts"].get(f"sf_{p}_render_claim")
                     for p in (0, 3)) and not any(Path(p).exists() for p in old["allowed_render_directories"]), "original_render_grant_used")
    base = Path(old["old_execution_directory"]) / "parent_timeline"
    knowledge = base / "PARENT_FINECUT.md"
    text = (Path(__file__).with_name("craft_knowledge") / "PARENT_FINECUT.md").read_text(encoding="utf-8")
    _require(not knowledge.exists() or knowledge.read_text(encoding="utf-8") == text, "knowledge_already_bound")
    if not knowledge.exists():
        knowledge.parent.mkdir(parents=True, exist_ok=True)
        knowledge.write_text(text, encoding="utf-8")
    files, prefixes = _history(output)
    files = {row["path"]: row for row in files}
    for row in old["protected_files"]:
        _require(row["path"] not in files or files[row["path"]]["sha256"] == row["sha256"], "old_proof_conflict")
        files[row["path"]] = row
    prep = _read(old["preparation_path"])
    policy = {"policy": POLICY, "task_id": data["task_id"], "original_output": str(output),
        "input_lock_sha256": json_sha(data["input_lock"]), "base_request_limit": data["max_requests"],
        "baseline_request_count": BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
        "baseline_artifacts": deepcopy(data["artifacts"]), "baseline_policy_version": data["policy_version"],
        "baseline_render_directories": sorted(p.name for p in output.glob("render_*") if p.is_dir()),
        "baseline_call_files": {c["id"]: sorted(str(p.relative_to(output / "calls" / c["id"]))
            for p in (output / "calls" / c["id"]).rglob("*") if p.is_file()) for c in data["calls"]},
        "protected_files": sorted(files.values(), key=lambda r: r["path"]), "journal_prefixes": prefixes,
        "original_authorization_path": old["original_authorization_path"],
        "original_authorization_sha256": old["original_authorization_sha256"],
        "execution_directory": str(base), "allowed_render_directories": [str(base / f"render_{p}" / "render") for p in (0, 3)],
        "knowledge_path": str(knowledge), "knowledge_sha256": sha256_file(knowledge),
        "parents": [{k: p[k] for k in ("round", "path", "sha256", "duration_s")} for p in prep["parents"]],
        "outline_paths": {str(p): str(Path(old["old_execution_directory"]) / f"render_{p}" / "outline.json") for p in (0, 3)},
        "unknown_inputs": old["unknown_inputs"], "user_instruction": user_instruction,
        "parent_rounds": [0, 3], "max_concurrent_parents": 2, "renders_per_parent": 1,
        "repairs_per_stage": 1, "numeric_total_request_limit": None, "automatic_round_loops": False,
        "goal_resumed": False, "stage_pattern": STAGES}
    LibraryState(output, data["input_lock"], max_requests=data["max_requests"]).set_artifact(ARTIFACT, policy)
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
    _require(p["stage_pattern"] == STAGES and p["parent_rounds"] == [0, 3] and p["max_concurrent_parents"] == 2
             and p["renders_per_parent"] == p["repairs_per_stage"] == 1 and p["numeric_total_request_limit"] is None
             and p["automatic_round_loops"] is p["goal_resumed"] is False
             and sha256_file(p["knowledge_path"], force=force) == p["knowledge_sha256"], "scope_changed")
    grant = _read(p["original_authorization_path"])
    _require(sha256_file(p["original_authorization_path"], force=force) == p["original_authorization_sha256"]
             and grant["renders_per_parent"] == 1 and Path(p["execution_directory"]) == Path(grant["execution_directory"]) / "parent_timeline"
             and p["allowed_render_directories"] == [str(Path(p["execution_directory"]) / f"render_{i}" / "render") for i in (0, 3)]
             and not any(Path(d).exists() for d in grant["allowed_render_directories"]), "original_grant_changed_or_used")
    _require(sorted(d.name for d in output.glob("render_*") if d.is_dir()) == p["baseline_render_directories"], "unapproved_legacy_render")
    for key, old in p["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == old, "historical_artifact_changed")
    for ident, old in p["baseline_call_files"].items():
        folder = output / "calls" / ident
        _require(sorted(str(f.relative_to(folder)) for f in folder.rglob("*") if f.is_file()) == old, "historical_call_files_changed")
    for row in p["protected_files"]:
        _require(sha256_file(row["path"], force=force) == row["sha256"], "historical_bytes_changed")
    for row in p["journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "journal_prefix_changed")
    names, last, pending = {}, {}, []
    for call in data["calls"][BASELINE:]:
        parent, _, repair = _stage(call["name"])
        _require(call["name"] not in names and call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "new_stage_or_status_invalid")
        req = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(req) == call["request_sha256"], "new_request_changed")
        if repair:
            old = names.get(call["name"][:-7])
            _require(old and old["status"] == "received" and call.get("repair_of") == old["id"]
                     and last.get(parent) == old["name"], "sole_repair_binding")
            orig = _read(output / "calls" / old["id"] / "request.json")
            _require(orig["media_sha256"] == req["media_sha256"] and scope_fingerprint(orig["observation_scope"]) == scope_fingerprint(req["observation_scope"]), "repair_input_changed")
        else:
            _require(not call.get("repair_of"), "original_has_repair")
        if call["status"] == "received":
            _require(json_sha(_read(output / "calls" / call["id"] / "response.json")) == call["response_sha256"], "new_response_changed")
        if call["status"] == "submitted":
            pending.append(parent)
        names[call["name"]], last[parent] = call, call["name"]
    _require(len(pending) <= 2 and len(set(pending)) == len(pending), "same_parent_pending")
    for parent, directory in zip((0, 3), p["allowed_render_directories"], strict=True):
        claim = data["artifacts"].get(f"pc_{parent}_render_claim", [])
        _require(len(claim) <= 1 and (not Path(directory).exists() or claim), "render_without_claim")
    for key, entries in data["artifacts"].items():
        if not key.startswith("pc_"):
            continue
        _require(len(entries) == 1 and json_sha(_read(entries[0]["path"])) == entries[0]["sha256"], "new_artifact_changed")
        for proof in _read(entries[0]["path"]).get("completed_files", []):
            _require(sha256_file(proof["path"], force=force) == proof["sha256"], "completed_bytes_changed")
    return {**p, "authorization_path": str(path), "authorization_sha256": sha256_file(path, force=force)}


class ParentCutState(LibraryState):
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
        own = [c for c in self.data["calls"][BASELINE:] if c["name"] in (stem, stem + "_repair")]
        for call in reversed(own):
            folder = self.output / "calls" / call["id"]
            if call["status"] == "received" and (folder / "parsed.json").exists():
                value = _read(folder / "parsed.json")
                raw = "\n".join(c["text"] for c in _read(folder / "response.json")["result"]["content"] if c.get("type") == "text")
                _require(not (folder / "protocol_failure.json").exists() and parse_model_json(raw) == value, "parsed_not_original_reply")
                return call, value
        raise LibraryStopped("parent_cut:predecessor_not_parsed:" + stem)

    def _check_input(self, name, request):
        parent, kind, _ = _stage(name)
        media = Path(request.get("arguments", {}).get("video_source", "")).resolve(strict=True)
        _require(request.get("provider") == "official_vision_mcp_in_codex" and request.get("tool") == "analyze_video"
                 and sha256_file(media) == request.get("media_sha256"), "official_bound_video_required")
        scope = scope_fingerprint(request["observation_scope"])
        for lost in self.authorization["unknown_inputs"]:
            _require(json_sha(request) != lost["request_sha256"] and request["media_sha256"] != lost["media_sha256"]
                     and scope != scope_fingerprint(lost["scope"]), "unknown_input_replay")
        if kind.startswith("slot_"):
            source = next(p for p in self.authorization["parents"] if p["round"] == parent)
            slots = _read(self.authorization["outline_paths"][str(parent)])["slots"]
            slot = next((s for s in slots if kind == "slot_" + json_sha(s)[:16]), None)
            _require(scope[0] in {"continuous_window", "complete_file"} and scope[1] == source["sha256"]
                     and slot and scope[2:] == (float(slot["start_s"]), float(slot["end_s"]))
                     and 0 <= scope[2] < scope[3] <= source["duration_s"], "parent_timeline_only")
            if media != Path(source["path"]).resolve():
                lineage = _read(media.parent / "lineage.json")
                _require(scope == scope_fingerprint(lineage) and lineage["sha256"] == request["media_sha256"]
                         and Path(lineage["path"]).resolve() == media and Path(lineage["source_path"]).resolve() == Path(source["path"]).resolve(), "actual_crop_lineage_required")
            else:
                _require(scope[2:] == (0.0, float(source["duration_s"])), "full_parent_scope_required")
        else:
            final = Path(self.authorization["allowed_render_directories"][(0, 3).index(parent)]) / "final.mp4"
            actual = _read(final.parent / "render_result.json")
            _require(self.data["artifacts"].get(f"pc_{parent}_render_claim")
                     and scope[0] in {"complete_file", "continuous_window"} and scope[1] == actual["sha256"] == sha256_file(final)
                     and scope[2:] == (0.0, float(actual["measured_duration_s"])), "review_actual_output_only")
            if media != final:
                lineage = _read(media.parent / "lineage.json")
                _require(scope == scope_fingerprint(lineage) and lineage["sha256"] == request["media_sha256"]
                         and Path(lineage["path"]).resolve() == media and Path(lineage["source_path"]).resolve() == final,
                         "actual_output_proxy_lineage_required")

    def begin_call(self, name, request, *, repair_of=None):
        with _MUTEX, file_lock(self.lock_path):
            self.assert_protected()
            parent, kind, repair = _stage(name)
            added = self.data["calls"][BASELINE:]
            own = [c for c in added if _stage(c["name"])[0] == parent]
            ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            _require(all(c["status"] in {"received", "submitted"} for c in added) and not any(c["status"] == "submitted" for c in own), "unknown_or_own_pending")
            _require(not self.data["artifacts"].get(f"pc_result_{parent}") and not any(c["name"] == name for c in added), "finished_or_repeated_stage")
            if repair:
                _require(own and own[-1]["name"] == name[:-7] and own[-1]["status"] == "received" and ident == own[-1]["id"]
                         and not own[-1].get("repair_of") and not any(c.get("repair_of") == ident for c in added), "sole_repair_required")
                old = _read(self.output / "calls" / ident / "request.json")
                _require(old["media_sha256"] == request["media_sha256"] and scope_fingerprint(old["observation_scope"]) == scope_fingerprint(request["observation_scope"]), "repair_media_changed")
            else:
                _require(ident is None, "original_has_repair")
                if own:
                    self._received(own[-1]["name"].removesuffix("_repair"))
                if kind.startswith("slot_"):
                    _require(not any(_stage(c["name"])[1] in {"blind", "review"} for c in own), "slot_after_render_review")
                elif kind == "review":
                    self._received(f"pc_{parent}_blind")
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

    def set_artifact(self, name, value):
        with _MUTEX:
            self.assert_protected()
            _require(name.startswith("pc_") and not self.data["artifacts"].get(name), "old_or_existing_artifact_read_only")
            return super().set_artifact(name, value)

    def claim_render(self, parent, plan):
        from .parent_timeline_cut import build_plan, validate_slot_cut
        with _MUTEX:
            auth = self.assert_protected()
            own = [c for c in self.data["calls"][BASELINE:] if _stage(c["name"])[0] == parent]
            _require(parent in (0, 3) and all(c["status"] in {"received", "submitted"} for c in self.data["calls"][BASELINE:])
                     and own and not any(c["status"] == "submitted" for c in own), "unknown_or_own_pending")
            folder = Path(auth["execution_directory"]) / f"render_{parent}"
            catalog_path = folder / "parent_catalog" / "inventory.json"
            source = _read(catalog_path)["sources"][0]
            old_parent = next(p for p in auth["parents"] if p["round"] == parent)
            _require(source["sha256"] == old_parent["sha256"] == sha256_file(source["path"])
                     and Path(source["path"]).resolve() == Path(old_parent["path"]).resolve(), "parent_source_changed")
            outline = _read(auth["outline_paths"][str(parent)])
            cuts = []
            for slot in outline["slots"]:
                _, value = self._received(f"pc_{parent}_slot_{json_sha(slot)[:16]}")
                cuts.append(validate_slot_cut(value, slot))
            expected = build_plan(source, outline, cuts, audio_mode="source")
            _require(expected == plan, "compiled_plan_differs_from_model_cuts")
            directory = folder / "render"
            plan_path = folder / "final_plan.json"
            _require(not plan_path.exists() or _read(plan_path) == plan, "plan_already_bound")
            if not plan_path.exists():
                write_json(plan_path, plan)
            payload = {"policy": POLICY, "parent_round": parent, "directory": str(directory), "max_renders": 1,
                "plan_sha256": json_sha(plan), "completed_files": [{"path": str(f), "sha256": sha256_file(f)} for f in (plan_path, catalog_path)]}
            key = f"pc_{parent}_render_claim"
            if self.data["artifacts"].get(key):
                _require(_read(self.data["artifacts"][key][0]["path"]) == payload, "render_claim_changed")
            else:
                _require(not directory.exists(), "render_exists_without_claim")
                self.set_artifact(key, payload)
            return directory

    def finish(self, parent, payload):
        with _MUTEX:
            auth = self.assert_protected()
            _require(parent in (0, 3) and not any(c["status"] == "submitted" and _stage(c["name"])[0] == parent for c in self.data["calls"][BASELINE:]), "own_call_pending")
            if payload.get("model_quality_gate_passed") is True:
                for kind in ("blind", "review"):
                    _, review = self._received(f"pc_{parent}_{kind}")
                    _require(review.get("status") == "pass" and review.get("slots")
                             and all(s.get("status") == "pass" for s in review["slots"]), "model_review_did_not_pass")
                _require(self.data["artifacts"].get(f"pc_{parent}_render_claim"), "quality_pass_requires_render")
            folder = Path(auth["execution_directory"]) / f"render_{parent}"
            path = folder / "result.json"
            _require(not path.exists() or _read(path) == payload, "result_already_bound")
            if not path.exists():
                write_json(path, payload)
            receipt = {"policy": POLICY, "parent_round": parent, "result_path": str(path), "model_status": payload.get("status"),
                "completed_files": [{"path": str(f), "sha256": sha256_file(f)} for f in sorted(folder.rglob("*")) if f.is_file() and f.suffix != ".tmp"]}
            if self.data["artifacts"].get(f"pc_result_{parent}"):
                _require(_read(self.data["artifacts"][f"pc_result_{parent}"][0]["path"]) == receipt, "result_receipt_changed")
            else:
                self.set_artifact(f"pc_result_{parent}", receipt)
            return path

    def usage(self):
        self.assert_protected()
        return {**super().usage(), "max_requests": None, "base_max_requests": self.authorization["base_request_limit"], "parent_timeline_baseline_requests": BASELINE}
