"""An append-only request allowance for one model-owned fine-cut continuation.

The original LibraryState header remains unchanged. Only this validated facade
can run the new uncapped workflow; authorization is never inferred by a read.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil

from .media import sha256_file
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json

POLICY = "active_finecut_extension_v2"
REQUEST_LIMIT_POLICY = "progress_guard_no_numeric_request_cap_v1"
AUTHORIZATION = "active_finecut_extension_authorization"
ALLOWED_STAGE_PATTERN = r"^(?:active_4_(?:draft|finecut|blind|economy|review)|semantic_(?:slice|claims)_4_[a-f0-9]{16})(?:_repair)?$"
PACKAGE = Path(__file__).with_name("craft_knowledge")
_MUTABLE_ROOT = {"library_state.json", "current_status.json", "mcp_current.json", "mcp_ready.json", "mcp_tools.json"}


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("extension:" + reason)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _output(state):
    return Path(os.fspath(state.output)).resolve(strict=True)


def _entry(data):
    entries = data["artifacts"].get(AUTHORIZATION, [])
    _require(bool(entries), "authorization_required")
    _require(len(entries) == 1, "one_authorization_only")
    return entries[0]


def _bound_file(output, value, sha):
    path = Path(value).resolve(strict=True)
    _require(path.is_relative_to(output) and path.is_file(), "unsafe_snapshot_path")
    _require(sha256_file(path) == sha, "historical_file_changed:" + str(path))
    return path


def _check_progress(call, names):
    name = call["name"]
    stem = name[:-7] if name.endswith("_repair") else name
    def phase(stage):
        if stage.startswith("semantic_"):
            return 2
        return {"active_4_draft": 0, "active_4_finecut": 1, "active_4_blind": 3,
                "active_4_economy": 4, "active_4_review": 5}[stage.removesuffix("_repair")]
    if names:
        previous_name = next(reversed(names))
        _require(phase(stem) >= phase(previous_name), "stage_progress_regressed")
        if name.endswith("_repair"):
            _require(previous_name == stem, "repair_after_stage_advanced")
    def received(stage):
        prior = names.get(stage + "_repair", names.get(stage))
        _require(prior is not None and prior["status"] == "received", "stage_predecessor_unsettled:" + stage)
    if stem == "active_4_finecut":
        received("active_4_draft")
    elif stem.startswith("semantic_"):
        received("active_4_finecut")
    elif stem == "active_4_blind":
        keys = {n[len("semantic_slice_4_"):].removesuffix("_repair")
                for n in names if n.startswith("semantic_slice_4_")}
        _require(bool(keys), "blind_before_source_evidence")
        for key in keys:
            received("semantic_slice_4_" + key)
            received("semantic_claims_4_" + key)
    elif stem == "active_4_economy":
        received("active_4_blind")
    elif stem == "active_4_review":
        received("active_4_economy")
    elif stem == "active_4_draft":
        _require(not names or name.endswith("_repair"), "draft_after_stage_advanced")


def _validate_added_calls(output, data, value):
    baseline = value["baseline_request_count"]
    added = data["calls"][baseline:]
    names, slice_keys = {}, set()
    for offset, call in enumerate(added, baseline + 1):
        name = call["name"]
        _require(re.fullmatch(ALLOWED_STAGE_PATTERN, name) is not None, "stage_not_authorized")
        _require(call["id"] == f"glm_{offset:03d}_{name}", "call_sequence_changed")
        _require(name not in names, "duplicate_paid_stage")
        _check_progress(call, names)
        if name.endswith("_repair"):
            parent = names.get(name[:-7])
            _require(parent is not None and parent["status"] == "received" and call.get("repair_of") == parent["id"],
                     "invalid_repair_parent")
        else:
            _require(call.get("repair_of") is None, "original_has_repair_parent")
        names[name] = call
        match = re.fullmatch(r"semantic_(slice|claims)_4_([a-f0-9]{16})(?:_repair)?", name)
        if match:
            slice_keys.add(match[2])
            if match[1] == "claims":
                original = names.get("semantic_slice_4_" + match[2])
                _require(original is not None and original["status"] == "received", "claims_before_observation")
        _require(call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "invalid_call_status")
        folder = output / "calls" / call["id"]
        _require(json_sha(_read(folder / "request.json")) == call["request_sha256"], "request_changed")
        if call["status"] == "received":
            _require(json_sha(_read(folder / "response.json")) == call["response_sha256"], "response_changed")
    _require(len(slice_keys) <= value["max_segments"], "slice_limit_exceeded")


def get_authorization(state):
    """Verify the live ledger, frozen baseline, knowledge and one-render bound."""
    output = _output(state)
    data = _read(output / "library_state.json")
    entry = _entry(data)
    path = Path(entry["path"]).resolve(strict=True)
    _require(path.is_relative_to(output / "artifacts"), "unsafe_authorization_path")
    value = _read(path)
    _require(json_sha(value) == entry["sha256"], "authorization_changed")
    _require(value["policy"] == POLICY and value["task_id"] == data["task_id"], "task_or_policy_changed")
    _require(isinstance(value["user_authorization"], str) and bool(value["user_authorization"].strip()), "authorization_text_missing")
    baseline = value["baseline_request_count"]
    _require(type(baseline) is int and 0 <= baseline <= data["max_requests"] <= 80,
             "baseline_request_count_invalid")
    _require(value["base_request_limit"] == data["max_requests"] and value["additional_requests"] is None
             and value["effective_request_limit"] is None
             and value["request_limit_policy"] == REQUEST_LIMIT_POLICY, "request_limits_changed")
    _require(value["render_index"] == 4 and value["additional_renders"] == 1
             and value["max_segments"] == 32 and value["new_unique_windows"] == 0
             and value["allowed_stage_pattern"] == ALLOWED_STAGE_PATTERN, "execution_limits_changed")
    _require(value["input_lock_sha256"] == json_sha(data["input_lock"]), "input_lock_changed")
    frozen_path = _bound_file(output, value["baseline_state_path"], value["baseline_state_sha256"])
    frozen = _read(frozen_path)
    _require(frozen["request_count"] == baseline == len(frozen["calls"])
             and json_sha(frozen["calls"]) == value["baseline_calls_sha256"], "baseline_snapshot_invalid")
    _require(data["request_count"] == len(data["calls"]) and data["calls"][:baseline] == frozen["calls"],
             "historical_calls_changed")
    _require({k: v for k, v in data.items() if k not in {"request_count", "calls", "artifacts"}} ==
             {k: v for k, v in frozen.items() if k not in {"request_count", "calls", "artifacts"}},
             "historical_state_header_changed")
    for key, history in frozen["artifacts"].items():
        _require(data["artifacts"].get(key) == history, "historical_artifact_history_changed:" + key)
    for item in value["protected_files"]:
        _bound_file(output, item["path"], item["sha256"])
    for item in value["knowledge_files"]:
        _bound_file(output, item["path"], item["sha256"])
    _bound_file(output, value["handbook_path"], value["handbook_sha256"])
    original_dirs = set(value["render_directories"])
    actual_dirs = {p.name for p in output.glob("render_*") if p.is_dir()}
    _require(original_dirs.issubset(actual_dirs) and actual_dirs - original_dirs <= {"render_4", "render_catalog_4"},
             "unapproved_render_directory")
    _validate_added_calls(output, data, value)
    return value


def _protected_files(output):
    files = {p for p in output.glob("*.json") if p.name not in _MUTABLE_ROOT}
    for name in ("calls", "artifacts", "media_cache", "asr_cache", "qa_cache", "semantic_audit", "catalog", "reference_catalog"):
        files.update(p for p in (output / name).rglob("*") if p.is_file())
    for folder in output.glob("render_*"):
        files.update(p for p in folder.rglob("*") if p.is_file())
    return [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in sorted(files)]


def authorize(output, authorization_text, additional_requests=None):
    """Record trusted task intent and explicit absence of a numeric quota once.

    The one-pass stage protocol still blocks repeated, pending and unknown work.
    Original numerical proposals remain protected historical evidence.
    """
    _require(isinstance(authorization_text, str) and bool(authorization_text.strip()), "authorization_text_missing")
    _require(additional_requests is None, "numeric_request_allowance_not_authorized")
    output = Path(output).resolve(strict=True)
    with file_lock(output / ".extension_budget.lock"):
        saved = _read(output / "library_state.json")
        probe = type("ExistingState", (), {"output": output})()
        if saved["artifacts"].get(AUTHORIZATION):
            return get_authorization(probe)
        _require(saved["request_count"] == len(saved["calls"]) <= saved["max_requests"] <= 80, "invalid_base_ledger")
        _require(not any(c["status"] == "submitted" for c in saved["calls"]), "pending_original_request")
        _require(not (output / "render_4").exists() and not (output / "render_catalog_4").exists(), "render_4_without_authorization")
        _require(not any(p.name[7:].isdigit() and int(p.name[7:]) > 3 for p in output.glob("render_*") if p.is_dir()),
                 "unapproved_original_render")
        _require(not any(re.fullmatch(ALLOWED_STAGE_PATTERN, c["name"]) for c in saved["calls"]), "stage_already_submitted_without_authorization")
        # Validate existing policies in their original completed view before any
        # permission or knowledge snapshot is written.
        state = LibraryState(output, saved["input_lock"], max_requests=saved["max_requests"])
        verify_historical_allocation(state)
        protected = _protected_files(output)
        folder = output / "artifacts" / POLICY
        _require(not folder.exists(), "incomplete_authorization_snapshot_do_not_overwrite")
        folder.mkdir()
        snapshot = folder / "baseline_state.json"
        write_json(snapshot, saved)
        knowledge = folder / "knowledge"
        knowledge_files = []
        for source in sorted(PACKAGE.rglob("*")):
            if source.is_file():
                target = knowledge / source.relative_to(PACKAGE)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                knowledge_files.append({"path": str(target.resolve()), "sha256": sha256_file(target)})
        handbook = knowledge / "ACTIVE_FINE_CUT.md"
        value = {"policy": POLICY, "task_id": saved["task_id"], "user_authorization": authorization_text,
            "budget_source": "user_removed_program_request_cap_one_pass_progress_guard",
            "input_lock_sha256": json_sha(saved["input_lock"]), "base_request_limit": saved["max_requests"],
            "baseline_request_count": saved["request_count"], "baseline_calls_sha256": json_sha(saved["calls"]),
            "additional_requests": None, "effective_request_limit": None, "request_limit_policy": REQUEST_LIMIT_POLICY,
            "render_index": 4, "additional_renders": 1, "max_segments": 32, "new_unique_windows": 0,
            "allowed_stage_pattern": ALLOWED_STAGE_PATTERN, "repairs_per_stage": 1,
            "baseline_state_path": str(snapshot), "baseline_state_sha256": sha256_file(snapshot),
            "render_directories": sorted(p.name for p in output.glob("render_*") if p.is_dir()),
            "protected_files": protected, "knowledge_files": knowledge_files,
            "handbook_path": str(handbook), "handbook_sha256": sha256_file(handbook),
            "no_unknown_replay": True, "model_review_is_not_human_truth": True}
        state.set_artifact(AUTHORIZATION, value)
        return get_authorization(state)


class ExtensionState(LibraryState):
    """Persist base80, run the validated one-pass workflow without a numeric cap."""
    def __init__(self, output):
        output = Path(output).resolve(strict=True)
        get_authorization(type("ExistingState", (), {"output": output})())
        saved = _read(output / "library_state.json")
        super().__init__(output, saved["input_lock"], max_requests=saved["max_requests"])
        get_authorization(self)
        self.max_requests = float("inf")
        self._extension_ready = True

    def _reload(self):
        super()._reload()
        if getattr(self, "_extension_ready", False):
            get_authorization(self)
            self.max_requests = float("inf")

    def begin_call(self, name, request, *, repair_of=None):
        value = get_authorization(self)
        _require(re.fullmatch(ALLOWED_STAGE_PATTERN, name or "") is not None, "stage_not_authorized")
        self._reload()
        added = self.data["calls"][value["baseline_request_count"]:]
        _require(not any(c["status"] == "submitted" for c in self.data["calls"]), "request_outcome_unknown_no_replay")
        _require(not any(c["status"] == "uncertain" for c in added), "new_request_outcome_unknown_no_replay")
        _require(not any(c["name"] == name for c in added), "duplicate_paid_stage")
        if name.endswith("_repair"):
            parent_id = repair_of["id"] if isinstance(repair_of, dict) else repair_of
            parent = next((c for c in added if c["id"] == parent_id), None)
            _require(parent is not None and parent["name"] == name[:-7], "invalid_repair_parent")
        else:
            _require(repair_of is None, "original_has_repair_parent")
        match = re.fullmatch(r"semantic_(slice|claims)_4_([a-f0-9]{16})(?:_repair)?", name)
        if match:
            keys = {re.fullmatch(r"semantic_(?:slice|claims)_4_([a-f0-9]{16})(?:_repair)?", c["name"])[1]
                    for c in added if c["name"].startswith("semantic_")}
            _require(match[2] in keys or len(keys) < value["max_segments"], "slice_limit_exceeded")
            if match[1] == "claims":
                _require(any(c["name"] == "semantic_slice_4_" + match[2] and c["status"] == "received" for c in added),
                         "claims_before_observation")
        _check_progress({"name": name}, {c["name"]: c for c in added})
        return super().begin_call(name, request, repair_of=repair_of)

    def _new_call_only(self, call):
        value = get_authorization(self)
        call_id = call["id"] if isinstance(call, dict) else call
        self._reload()
        _require(any(c["id"] == call_id for c in self.data["calls"][value["baseline_request_count"]:]), "historical_call_is_read_only")

    def complete_call(self, call, response, *, usage=None):
        self._new_call_only(call)
        return super().complete_call(call, response, usage=usage)

    def fail_call(self, call, error, *, uncertain=True):
        self._new_call_only(call)
        return super().fail_call(call, error, uncertain=uncertain)

    def reconcile_received(self, call, response, *, evidence, usage=None):
        self._new_call_only(call)
        return super().reconcile_received(call, response, evidence=evidence, usage=usage)

    def reclassify_uncertain(self, call, *, evidence):
        self._new_call_only(call)
        return super().reclassify_uncertain(call, evidence=evidence)

    def set_artifact(self, name, payload):
        value = get_authorization(self)
        frozen = _read(value["baseline_state_path"])
        _require(name != AUTHORIZATION and name not in frozen["artifacts"], "historical_artifact_is_read_only")
        return super().set_artifact(name, payload)

    def usage(self):
        value = get_authorization(self)
        usage = super().usage()
        count = usage["requests"] - value["baseline_request_count"]
        return {**usage, "max_requests": None, "base_max_requests": value["base_request_limit"],
            "effective_request_limit": None, "request_limit_policy": REQUEST_LIMIT_POLICY,
            "extension_baseline_requests": value["baseline_request_count"], "extension_requests": count,
            "extension_max_requests": None, "extension_remaining_requests": None,
            "extension_policy": POLICY}


def stage_state(output):
    return ExtensionState(output)


class _HistoricalOutput:
    """Actual paths for bound historical reads; glob omits new render directories."""
    def __init__(self, output, directories):
        self.path, self.directories = output, set(directories)

    def __fspath__(self):
        return os.fspath(self.path)

    def __str__(self):
        return str(self.path)

    def __truediv__(self, value):
        return self.path / value

    def glob(self, pattern):
        for path in self.path.glob(pattern):
            first = path.relative_to(self.path).parts[0]
            if not first.startswith("render_") or first in self.directories:
                yield path


class HistoricalState:
    """Read-only ledger view; never use this view for continuation execution."""
    def __init__(self, state):
        self.live = state
        value = get_authorization(state)
        self.path = _output(state) / "library_state.json"
        self.output = _HistoricalOutput(_output(state), value["render_directories"])
        self.max_requests = value["base_request_limit"]
        self._reload()
        self.input_lock = deepcopy(self.data["input_lock"])

    def _reload(self):
        value = get_authorization(self.live)
        self.data = deepcopy(_read(value["baseline_state_path"]))

    def usage(self):
        self._reload()
        return LibraryState.usage(self)

    def _read_only(self, *args, **kwargs):
        raise LibraryStopped("extension:historical_view_is_read_only")

    _save = begin_call = complete_call = fail_call = set_artifact = _read_only
    reconcile_received = reclassify_uncertain = enable_independent_continuation = _read_only


def historical_state(state):
    if isinstance(state, HistoricalState):
        state._reload()
        return state
    data = _read(_output(state) / "library_state.json")
    return HistoricalState(state) if data["artifacts"].get(AUTHORIZATION) else state


def verify_historical_allocation(state):
    """Run original strict policy checks against their hash-bound completed view."""
    state = historical_state(state)
    from .reference_craft import ALLOCATION, _artifact, _verify_allocation
    if state.data["artifacts"].get(ALLOCATION):
        _verify_allocation(state, _artifact(state, ALLOCATION))
    if state.data["artifacts"].get("semantic_continuation_authorization"):
        from .semantic_continuation import _authorization
        _authorization(state)
    if state.data["artifacts"].get("editing_revision_authorization"):
        from .revision import _authorization
        _authorization(state)
    return state
