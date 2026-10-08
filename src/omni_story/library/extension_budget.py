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

from .media import sha256_file, verify_source
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json

POLICY = "active_finecut_extension_v2"
REQUEST_LIMIT_POLICY = "progress_guard_no_numeric_request_cap_v1"
AUTHORIZATION = "active_finecut_extension_authorization"
ALLOWED_STAGE_PATTERN = r"^(?:active_4_(?:draft|finecut|blind|economy|review)|semantic_(?:slice|claims)_4_[a-f0-9]{16})(?:_repair)?$"
PACKAGE = Path(__file__).with_name("craft_knowledge")
_MUTABLE_ROOT = {"library_state.json", "current_status.json", "mcp_current.json", "mcp_ready.json", "mcp_tools.json"}
CACHE_POLICY = "exact_slice_received_cache_binding_v1"
CACHE_PREFIX = "active_4_cached_evidence_"
GOAL_AUTHORIZATION = 'goal_feedback_authorization'
GOAL_POLICY = 'goal_feedback_extension_v1'


def _slice_key(segment, source_sha):
    return json_sha({'segment': segment['segment_id'], 'sha': source_sha,
                     'in': segment['source_in_s'], 'out': segment['source_out_s']})[:16]


def _model_value(output, call):
    from .contracts import parse_model_json
    _require(call['status'] == 'received', 'cached_call_not_received')
    folder = output / 'calls' / call['id']
    request, reply, parsed = (_read(folder / file) for file in ('request.json', 'response.json', 'parsed.json'))
    _require(json_sha(request) == call['request_sha256'] and json_sha(reply) == call['response_sha256'],
             'cached_call_record_changed')
    text = '\n'.join(c['text'] for c in reply['result']['content'] if c.get('type') == 'text')
    _require(parse_model_json(text) == parsed, 'cached_parsed_differs_from_reply')
    return request, parsed


def _final_plan(output, data):
    candidates = [c for c in data['calls'] if c['name'] in {'active_4_finecut', 'active_4_finecut_repair'}
                  and c['status'] == 'received' and (output / 'calls' / c['id'] / 'parsed.json').is_file()]
    _require(bool(candidates), 'cache_requires_bound_final_plan')
    _, refined = _model_value(output, candidates[-1])
    plan = _read(output / 'artifacts/active_finecut_continuation_v1/plan.json')
    _require(refined['plan'] == plan, 'cached_final_plan_changed')
    return plan


def _cache_proofs(output, data, authorization, *, proposed=None):
    """Verify received old calls, exact media and final-plan semantics afresh."""
    from . import semantic_audit as audit, semantic_prompts
    records = {name: entries for name, entries in data['artifacts'].items() if name.startswith(CACHE_PREFIX)}
    if proposed:
        records[proposed[0]] = None
    if not records:
        return {}
    plan = _final_plan(output, data)
    segments = {s['segment_id']: s for s in plan['segments']}
    baseline = _read(authorization['baseline_state_path'])['calls']
    source_lock = {s['source_id']: s['sha256'] for s in data['input_lock']['library_sources']}
    catalog = {s['source_id']: s for s in _read(output / 'catalog/inventory.json')['sources']}
    windows = {w['window_id']: w for w in _read(output / 'watched_windows.json')}
    proofs = {}
    for name, entries in records.items():
        if proposed and name == proposed[0]:
            proof = proposed[1]
        else:
            _require(len(entries) == 1, 'one_cache_proof_only')
            record_path = Path(entries[0]['path']).resolve(strict=True)
            _require(record_path.is_relative_to(output / 'artifacts'), 'unsafe_cache_proof_path')
            proof = _read(record_path)
            _require(json_sha(proof) == entries[0]['sha256'], 'cache_proof_changed')
        _require(proof['policy'] == CACHE_POLICY, 'cache_proof_changed')
        stage, key = proof['stage'], proof['key']
        _require(stage in {'slice', 'claims'} and name == CACHE_PREFIX + stage + '_' + key,
                 'cache_proof_stage_changed')
        source, proxy, segment = proof['source'], proof['proxy'], proof['segment']
        _require(segment['segment_id'] in segments and segment == {k: segments[segment['segment_id']][k]
                 for k in ('segment_id', 'source_id', 'source_in_s', 'source_out_s')}, 'cache_slice_not_in_final_plan')
        _require(proof['plan_sha256'] == json_sha(plan) and key == _slice_key(segment, source['sha256']), 'cache_plan_binding_changed')
        _require(source_lock.get(source['source_id']) == source['sha256'] and segment['source_id'] == source['source_id'],
                 'cache_source_not_in_locked_library')
        _require(catalog.get(source['source_id']) == source, 'cache_source_differs_from_protected_catalog')
        verify_source(source)
        lineage_path = _bound_file(output, proof['lineage_path'], proof['lineage_sha256'])
        _require(_read(lineage_path) == proxy and proxy['kind'] == 'continuous_window'
                 and proxy['spec']['fps'] == 30, 'cached_media_lineage_changed')
        for field in ('kind', 'source_sha256', 'source_start_s', 'source_end_s'):
            _require(proxy[field] == proxy['spec'][field], 'cached_media_spec_changed')
        _bound_file(output, proxy['path'], proxy['sha256'])
        for frame in proxy.get('frames', []):
            _bound_file(output, frame['path'], frame['sha256'])
        call = next((c for c in baseline if c['id'] == proof['call_id']), None)
        _require(call is not None and re.fullmatch(r'semantic_' + stage + r'_\d+_[a-f0-9]{16}(?:_repair)?', call['name']),
                 'cache_call_not_original_slice_stage')
        request, value = _model_value(output, call)
        _require(proof['request_sha256'] == call['request_sha256'] and proof['response_sha256'] == call['response_sha256']
                 and proof['value_sha256'] == json_sha(value) and proof['value'] == value, 'cache_call_binding_changed')
        _require(request['tool'] == 'analyze_video' and request['media_sha256'] == proxy['sha256']
                 and Path(request['arguments']['video_source']).resolve() == Path(proxy['path']).resolve(),
                 'cache_call_media_changed')
        scope = request.get('observation_scope')
        if scope is not None:
            _require(all(scope[k] == proxy[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')),
                     'cache_call_scope_changed')
        if stage == 'slice':
            audit.validate_segment_observation(value, segment, source['sha256'], proxy)
            expected = semantic_prompts.slice_observation_prompt(segment, source, proxy)
        else:
            observation, claims, hypotheses = proof['observation'], proof['claims'], proof['hypotheses']
            audit.validate_segment_observation(observation, segment, source['sha256'], proxy)
            _require(claims == audit.segment_required_claims(plan, segments[segment['segment_id']]), 'cached_claims_not_final_plan')
            window = windows.get(segments[segment['segment_id']]['window_id'])
            _require(window is not None and hypotheses == window['observation']['roles'], 'cached_role_hypotheses_changed')
            audit.validate_segment_claim_check(value, observation, claims)
            expected = semantic_prompts.slice_claim_prompt(observation, claims, hypotheses)
        prompt_request = request
        if call.get('repair_of'):
            parent = next((c for c in baseline if c['id'] == call['repair_of']), None)
            _require(parent is not None and parent['status'] == 'received' and not parent.get('repair_of'), 'cache_repair_parent_changed')
            prompt_request = _read(output / 'calls' / parent['id'] / 'request.json')
            _require(json_sha(prompt_request) == parent['request_sha256'], 'cache_original_request_changed')
        _require(prompt_request['arguments']['prompt'] == expected, 'cache_independent_prompt_changed')
        proofs[(stage, key)] = proof
    for (stage, key), proof in proofs.items():
        if stage == 'claims':
            facts = proofs.get(('slice', key))
            new_calls = [c for c in data['calls'][authorization['baseline_request_count']:]
                         if c['name'] in {'semantic_slice_4_' + key, 'semantic_slice_4_' + key + '_repair'}
                         and c['status'] == 'received' and (output / 'calls' / c['id'] / 'parsed.json').is_file()]
            observed = facts['value'] if facts else _model_value(output, new_calls[-1])[1] if new_calls else None
            _require(observed == proof['observation'], 'cached_claims_observation_changed')
    _require(len({key for _, key in proofs}) <= authorization['max_segments'], 'cached_slice_limit_exceeded')
    return proofs


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


def _check_progress(call, names, proofs=None, final_keys=None):
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
        keys = {re.fullmatch(r'semantic_(?:slice|claims)_4_([a-f0-9]{16})(?:_repair)?', n)[1]
                for n in names if n.startswith('semantic_')}
        keys.update(key for _, key in (proofs or {}))
        if final_keys is not None:
            _require(keys == final_keys, 'blind_missing_final_slice_bindings')
        _require(bool(keys), "blind_before_source_evidence")
        for key in keys:
            for stage in ('slice', 'claims'):
                if (stage, key) not in (proofs or {}):
                    received('semantic_' + stage + '_4_' + key)
    elif stem == "active_4_economy":
        received("active_4_blind")
    elif stem == "active_4_review":
        received("active_4_economy")
    elif stem == "active_4_draft":
        _require(not names or name.endswith("_repair"), "draft_after_stage_advanced")


def _validate_added_calls(output, data, value):
    baseline = value["baseline_request_count"]
    added = data["calls"][baseline:]
    proofs = _cache_proofs(output, data, value)
    final_keys = {_slice_key(s, next(row['sha256'] for row in data['input_lock']['library_sources']
                                   if row['source_id'] == s['source_id']))
                  for s in _final_plan(output, data)['segments']} if proofs else None
    names, slice_keys = {}, set(key for _, key in proofs)
    for offset, call in enumerate(added, baseline + 1):
        name = call["name"]
        _require(re.fullmatch(ALLOWED_STAGE_PATTERN, name) is not None, "stage_not_authorized")
        _require(call["id"] == f"glm_{offset:03d}_{name}", "call_sequence_changed")
        _require(name not in names, "duplicate_paid_stage")
        _check_progress(call, names, proofs, final_keys)
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
                _require((original is not None and original["status"] == "received") or ('slice', match[2]) in proofs,
                         "claims_before_observation")
        _require(call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "invalid_call_status")
        folder = output / "calls" / call["id"]
        _require(json_sha(_read(folder / "request.json")) == call["request_sha256"], "request_changed")
        if call["status"] == "received":
            _require(json_sha(_read(folder / "response.json")) == call["response_sha256"], "response_changed")
    _require(len(slice_keys) <= value["max_segments"], "slice_limit_exceeded")


def validate_authorization_snapshot(output, data, render_directories):
    """Pure strict v2 validation against an explicitly supplied frozen view."""
    output = Path(output).resolve(strict=True)
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
    actual_dirs = set(render_directories)
    _require(original_dirs.issubset(actual_dirs) and actual_dirs - original_dirs <= {"render_4", "render_catalog_4"},
             "unapproved_render_directory")
    _validate_added_calls(output, data, value)
    return value


def _goal_snapshot(output, data):
    """Bind the frozen round4 prefix without granting any Goal write rights."""
    records = data['artifacts'].get(GOAL_AUTHORIZATION, [])
    _require(len(records) == 1, 'one_goal_authorization_required')
    path = Path(records[0]['path']).resolve(strict=True)
    _require(path.is_relative_to(output / 'artifacts'), 'unsafe_goal_authorization_path')
    goal = _read(path)
    _require(json_sha(goal) == records[0]['sha256'] and goal['policy'] == GOAL_POLICY, 'goal_authorization_changed')
    _require(goal['task_id'] == data['task_id'] and goal['input_lock_sha256'] == json_sha(data['input_lock']),
             'goal_task_or_input_changed')
    snapshot = _bound_file(output, goal['baseline_state_path'], goal['baseline_state_sha256'])
    frozen = _read(snapshot)
    count = goal['baseline_request_count']
    _require(type(count) is int and count == frozen['request_count'] == len(frozen['calls'])
             and goal['baseline_calls_sha256'] == json_sha(frozen['calls']), 'goal_baseline_invalid')
    _require(data['request_count'] == len(data['calls']) and data['calls'][:count] == frozen['calls'],
             'goal_frozen_call_prefix_changed')
    _require({k:v for k,v in data.items() if k not in {'calls','request_count','artifacts'}} ==
             {k:v for k,v in frozen.items() if k not in {'calls','request_count','artifacts'}},
             'goal_frozen_state_header_changed')
    for name, history in frozen['artifacts'].items():
        _require(data['artifacts'].get(name) == history, 'goal_frozen_artifact_history_changed:' + name)
    _require(set(goal['render_directories']).issubset({p.name for p in output.glob('render_*') if p.is_dir()}),
             'goal_frozen_render_missing')
    return frozen, goal


def get_authorization(state):
    """Verify live v2, or its strictly frozen prefix after a Goal extension."""
    output = _output(state)
    data = _read(output / 'library_state.json')
    if data['artifacts'].get(GOAL_AUTHORIZATION):
        # A frozen view is an old-stage read boundary, not a Goal permission
        # shortcut. Validate the complete live Goal grant first; that validator
        # uses validate_authorization_snapshot directly to avoid recursion.
        from .goal_budget import get_authorization as get_goal_authorization
        get_goal_authorization(state)
        frozen, goal = _goal_snapshot(output, data)
        return validate_authorization_snapshot(output, frozen, goal['render_directories'])
    return validate_authorization_snapshot(output, data, [p.name for p in output.glob('render_*') if p.is_dir()])


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
        _require(not saved['artifacts'].get(GOAL_AUTHORIZATION), 'round4_frozen_by_goal_use_goal_entry')
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
        _require(not _read(self.path)['artifacts'].get(GOAL_AUTHORIZATION), 'round4_frozen_by_goal_use_goal_entry')
        value = get_authorization(self)
        _require(re.fullmatch(ALLOWED_STAGE_PATTERN, name or "") is not None, "stage_not_authorized")
        self._reload()
        added = self.data["calls"][value["baseline_request_count"]:]
        proofs = _cache_proofs(self.output, self.data, value)
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
            keys.update(key for _, key in proofs)
            _require(match[2] in keys or len(keys) < value["max_segments"], "slice_limit_exceeded")
            if match[1] == "claims":
                _require(any(c["name"] == "semantic_slice_4_" + match[2] and c["status"] == "received" for c in added)
                         or ('slice', match[2]) in proofs,
                         "claims_before_observation")
        final_keys = {_slice_key(s, next(row['sha256'] for row in self.data['input_lock']['library_sources']
                                       if row['source_id'] == s['source_id']))
                      for s in _final_plan(self.output, self.data)['segments']} if proofs else None
        _check_progress({"name": name}, {c["name"]: c for c in added}, proofs, final_keys)
        return super().begin_call(name, request, repair_of=repair_of)

    def bind_cached_slice_evidence(self, name, segment, source, proxy, result, *, observation=None, claims=None, hypotheses=None):
        """Append proof only when CodexMCP reused a received baseline digest."""
        match = re.fullmatch(r'semantic_(slice|claims)_4_([a-f0-9]{16})', name)
        if match is None:
            return
        authorization = get_authorization(self)
        self._reload()
        if any(c['name'] in {name, name + '_repair'} for c in self.data['calls'][authorization['baseline_request_count']:]):
            return
        stage, key = match.groups()
        artifact_name = CACHE_PREFIX + stage + '_' + key
        if self.data['artifacts'].get(artifact_name):
            proof = _cache_proofs(self.output, self.data, authorization)[(stage, key)]
            _require(proof['value'] == result, 'cached_return_value_changed')
            return
        plan = _final_plan(self.output, self.data)
        minimal = {k: segment[k] for k in ('segment_id', 'source_id', 'source_in_s', 'source_out_s')}
        lineage = Path(proxy['path']).parent / 'lineage.json'
        _require(_read(lineage) == proxy, 'cache_actual_lineage_changed')
        baseline = _read(authorization['baseline_state_path'])['calls']
        candidates = []
        for call in baseline:
            if call['status'] != 'received' or not re.fullmatch(r'semantic_' + stage + r'_\d+_[a-f0-9]{16}(?:_repair)?', call['name']):
                continue
            folder = self.output / 'calls' / call['id']
            if (folder / 'parsed.json').is_file() and _read(folder / 'parsed.json') == result:
                request, value = _model_value(self.output, call)
                if request.get('media_sha256') == proxy['sha256']:
                    candidates.append(call)
        _require(len(candidates) == 1, 'cached_observation_requires_one_bound_received_call')
        call = candidates[0]
        proof = {'policy': CACHE_POLICY, 'stage': stage, 'key': key, 'target_stage': name,
            'plan_sha256': json_sha(plan), 'segment': minimal, 'source': deepcopy(source), 'proxy': deepcopy(proxy),
            'lineage_path': str(lineage), 'lineage_sha256': sha256_file(lineage),
            'call_id': call['id'], 'request_sha256': call['request_sha256'], 'response_sha256': call['response_sha256'],
            'value': deepcopy(result), 'value_sha256': json_sha(result),
            'evidence_role': 'Reuse existing independently observed facts or compare the same exact claims; no new model request.'}
        if stage == 'claims':
            proof.update(observation=deepcopy(observation), claims=deepcopy(claims), hypotheses=deepcopy(hypotheses))
        # Validate the proposed binding before appending it. No old evidence is
        # changed and a failed proof must not grant a new claims submission.
        _cache_proofs(self.output, self.data, authorization, proposed=(artifact_name, proof))
        self.set_artifact(artifact_name, proof)

    def _new_call_only(self, call):
        _require(not _read(self.path)['artifacts'].get(GOAL_AUTHORIZATION), 'round4_frozen_by_goal_use_goal_entry')
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
        _require(not _read(self.path)['artifacts'].get(GOAL_AUTHORIZATION), 'round4_frozen_by_goal_use_goal_entry')
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
