"""Append-only Goal authorization and progress guard, without a request quota."""
from copy import deepcopy
from pathlib import Path
import re
import shutil

from .media import sha256_file, verify_source
from .pipeline import _read
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json

POLICY = "goal_feedback_extension_v1"
AUTHORIZATION = "goal_feedback_authorization"
REQUEST_LIMIT_POLICY = "progress_guard_no_numeric_request_cap_v1"
OBSERVATION_COMPATIBILITY = {'policy':'goal_uncertainty_description_validation_v1',
    'scope':'semantic_(slice|claims)_round>=5','raw_json_unchanged':True,'omitted_uncertainties_allowed':False}
ALLOWED_STAGE_PATTERN = r"^(?:active_(?:[5-9]|[1-9][0-9]+)_(?:draft|finecut|blind|economy|review)|semantic_(?:slice|claims)_(?:[5-9]|[1-9][0-9]+)_[a-f0-9]{16})(?:_repair)?$"
PACKAGE = Path(__file__).with_name("craft_knowledge")


def _require(test, message):
    if not test:
        raise LibraryStopped("goal:" + message)


def _artifact(data, name):
    records = data["artifacts"].get(name, [])
    _require(len(records) == 1, "one_artifact_required:" + name)
    value = _read(records[0]["path"])
    _require(json_sha(value) == records[0]["sha256"], "artifact_changed:" + name)
    return value


def _bound(output, path, sha):
    path = Path(path).resolve(strict=True)
    _require(path.is_relative_to(output) and sha256_file(path) == sha, "bound_file_changed:" + str(path))
    return path


def _stage(name):
    stem = name.removesuffix("_repair")
    if stem.startswith("semantic_"):
        _, stage, round_no, key = stem.split("_")
        return int(round_no), 2, stage, key
    _, round_no, stage = stem.split("_")
    return int(round_no), {"draft": 0, "finecut": 1, "blind": 3, "economy": 4, "review": 5}[stage], stage, None


def _call_value(output, call):
    from .contracts import parse_model_json
    folder = output / "calls" / call["id"]
    _require(call["status"] == "received", "cache_call_not_received")
    request, response, value = (_read(folder / f) for f in ("request.json", "response.json", "parsed.json"))
    _require(json_sha(request) == call["request_sha256"] and json_sha(response) == call["response_sha256"], "cache_raw_changed")
    raw = "\n".join(c["text"] for c in response["result"]["content"] if c.get("type") == "text")
    _require(parse_model_json(raw) == value, "cache_parsed_changed")
    return request, value


def _cache_proofs(output, data, *, proposed=None):
    from . import semantic_audit as audit, semantic_prompts, active_observation_compat as compat
    proofs = {}
    records = {n for n in data['artifacts'] if n.startswith('goal_cached_')}
    if proposed:
        records.add(proposed[0])
    catalog = {s['source_id']: s for s in _read(output / 'catalog/inventory.json')['sources']} if records else {}
    locked = {s['source_id']: s['sha256'] for s in data['input_lock']['library_sources']}
    windows = {w['window_id']: w for w in _read(output / 'watched_windows.json')} if records else {}
    for name in sorted(records):
        proof = proposed[1] if proposed and name == proposed[0] else _artifact(data, name)
        round_no, _, stage, key = _stage(proof["target_stage"])
        _require(name == f"goal_cached_{round_no}_{stage}_{key}", "cache_stage_changed")
        segment, source, proxy = proof["segment"], proof["source"], proof["proxy"]
        plan = _read(output / f"artifacts/goal_feedback_round_{round_no}/plan.json")
        full = next((s for s in plan['segments'] if s['segment_id'] == segment['segment_id']), None)
        _require(full is not None and segment == {k: full[k] for k in ('segment_id','source_id','source_in_s','source_out_s')}
                 and json_sha(plan) == proof['plan_sha256'], 'cache_plan_changed')
        candidates = [c for c in data['calls'] if c['name'] in {f'active_{round_no}_finecut',f'active_{round_no}_finecut_repair'}
                      and c['status'] == 'received' and (output/'calls'/c['id']/'parsed.json').is_file()]
        _require(bool(candidates) and _call_value(output,candidates[-1])[1]['plan'] == plan, 'cache_final_plan_not_model_bound')
        _require(locked.get(source['source_id']) == source['sha256'] and segment['source_id'] == source['source_id']
                 and catalog.get(source['source_id']) == source, 'cache_source_not_locked')
        _require(key == json_sha({'segment':segment['segment_id'],'sha':source['sha256'],
                 'in':segment['source_in_s'],'out':segment['source_out_s']})[:16], 'cache_key_changed')
        verify_source(source)
        _bound(output, proxy["path"], proxy["sha256"])
        _require(_read(Path(proxy["path"]).parent / "lineage.json") == proxy, "cache_lineage_changed")
        _require(proxy['kind'] == 'continuous_window' and proxy['spec']['fps'] == 30, 'cache_media_sampling_changed')
        for field in ('kind','source_sha256','source_start_s','source_end_s'):
            _require(proxy[field] == proxy['spec'][field], 'cache_media_spec_changed')
        for frame in proxy.get('frames',[]):
            _bound(output,frame['path'],frame['sha256'])
        _require(proxy["source_sha256"] == source["sha256"] and proxy["source_start_s"] == segment["source_in_s"]
                 and proxy["source_end_s"] == segment["source_out_s"], "cache_source_changed")
        call = next((c for c in data["calls"] if c["id"] == proof["call_id"]), None)
        _require(call is not None and re.fullmatch(r'semantic_' + stage + r'_\d+_[a-f0-9]{16}(?:_repair)?',call['name']), "cache_call_missing")
        request, value = _call_value(output, call)
        _require(value == proof['value'] and request['tool'] == 'analyze_video' and request['media_sha256'] == proxy['sha256']
                 and Path(request['arguments']['video_source']).resolve() == Path(proxy['path']).resolve(), 'cache_binding_changed')
        if request.get('observation_scope') is not None:
            _require(all(request['observation_scope'][k] == proxy[k] for k in ('kind','source_sha256','source_start_s','source_end_s')), 'cache_scope_changed')
        if stage == "slice":
            compat.validate_observation(value, segment, source["sha256"], proxy, enabled=True)
            expected = semantic_prompts.slice_observation_prompt(segment, source, proxy)
        else:
            claims = audit.segment_required_claims(plan, full)
            _require(claims == proof["claims"], "cached_claims_not_final_plan")
            _require(proof['hypotheses'] == windows[full['window_id']]['observation']['roles'], 'cached_role_hypotheses_changed')
            compat.validate_observation(proof["observation"], segment, source["sha256"], proxy, enabled=True)
            compat.validate_claims(value, proof["observation"], claims, enabled=True)
            expected = semantic_prompts.slice_claim_prompt(proof["observation"], claims, proof["hypotheses"])
        parent = next((c for c in data["calls"] if c["id"] == call.get("repair_of")), None)
        if call.get('repair_of'):
            _require(parent is not None and parent['status'] == 'received' and not parent.get('repair_of'), 'cache_repair_parent_changed')
        prompt_request = _read(output / "calls" / parent["id"] / "request.json") if parent else request
        _require(json_sha(prompt_request) == (parent or call)['request_sha256'], 'cache_original_request_changed')
        _require(prompt_request["arguments"]["prompt"] == expected, "cached_independent_prompt_changed")
        proofs[proof["target_stage"]] = proof
    for name, proof in proofs.items():
        r,_,stage,key = _stage(name)
        if stage == 'claims':
            facts = proofs.get(f'semantic_slice_{r}_{key}')
            calls = [c for c in data['calls'] if c['name'] in {f'semantic_slice_{r}_{key}',f'semantic_slice_{r}_{key}_repair'}
                     and c['status']=='received' and (output/'calls'/c['id']/'parsed.json').is_file()]
            observed = facts['value'] if facts else _call_value(output,calls[-1])[1] if calls else None
            _require(observed == proof['observation'], 'cached_claims_observation_changed')
    return proofs


def _progress(output, data, name, names, proofs, repair_of=None):
    repair_of=repair_of['id'] if isinstance(repair_of,dict) else repair_of
    r,phase,stage,key = _stage(name)
    registration = _artifact(data, f'goal_round_{r}')
    _require(registration['round'] == r and r >= 5, 'round_registration_changed')
    grant=_artifact(data,AUTHORIZATION)
    _require(registration.get('task_id')==data['task_id'] and registration.get('input_lock_sha256')==json_sha(data['input_lock'])
             and registration.get('goal_guide_sha256')==grant['goal_guide_sha256']
             and registration.get('observation_compatibility')==OBSERVATION_COMPATIBILITY
             and registration.get('new_unique_windows')==0 and registration.get('max_renders')==1,'round_scope_changed')
    if not names:
        _require(r==5,'first_round_must_be_five')
    if names:
        previous = next(reversed(names.values()))
        pr,pp,_,_ = _stage(previous['name'])
        _require(r >= pr and (r > pr or phase >= pp), 'stage_progress_regressed')
        if r > pr:
            _require(r == pr+1 and data['artifacts'].get(f'goal_result_{pr}'), 'previous_round_not_finished')
    if name.endswith('_repair'):
        parent = names.get(name[:-7])
        _require(parent is not None and parent['status']=='received' and repair_of==parent['id']
                 and next(reversed(names))==name[:-7], 'invalid_repair_parent')
        return
    _require(repair_of is None, 'original_repair_parent')
    def received(stem):
        calls = [c for n,c in names.items() if n in {stem,stem+'_repair'}]
        _require((calls and calls[-1]['status']=='received' and (output/'calls'/calls[-1]['id']/'parsed.json').is_file())
                 or stem in proofs, 'predecessor_unsettled:'+stem)
    if stage == 'finecut':
        received(f'active_{r}_draft')
    elif stage in {'slice','claims','blind'}:
        received(f'active_{r}_finecut')
        if stage == 'claims':
            received(f'semantic_slice_{r}_{key}')
        if stage == 'blind':
            plan = _read(output/f'artifacts/goal_feedback_round_{r}/plan.json')
            sources = {s['source_id']:s['sha256'] for s in data['input_lock']['library_sources']}
            for segment in plan['segments']:
                k = json_sha({'segment':segment['segment_id'],'sha':sources[segment['source_id']],
                    'in':segment['source_in_s'],'out':segment['source_out_s']})[:16]
                received(f'semantic_slice_{r}_{k}')
                received(f'semantic_claims_{r}_{k}')
    elif stage == 'economy':
        received(f'active_{r}_blind')
    elif stage == 'review':
        received(f'active_{r}_economy')


def get_authorization(state):
    from .extension_budget import validate_authorization_snapshot
    output = Path(state.output).resolve(strict=True)
    data = _read(output / "library_state.json")
    _require(len(data['artifacts'].get(AUTHORIZATION,[]))==1,'authorization_required')
    authorization_path = Path(data['artifacts'][AUTHORIZATION][0]['path']).resolve(strict=True)
    _require(authorization_path.is_relative_to(output/'artifacts'),'unsafe_authorization_path')
    value = _artifact(data, AUTHORIZATION)
    _require(value["policy"] == POLICY and value["task_id"] == data["task_id"], "task_or_policy_changed")
    _require(value["input_lock_sha256"] == json_sha(data["input_lock"]), "input_lock_changed")
    _require(type(value['base_request_limit']) is int and type(data['max_requests']) is int
             and value["base_request_limit"] == data["max_requests"] and 1 <= data["max_requests"] <= 80
             and value["additional_requests"] is None and value["effective_request_limit"] is None
             and value["request_limit_policy"] == REQUEST_LIMIT_POLICY, "request_policy_changed")
    _require(value["allowed_stage_pattern"] == ALLOWED_STAGE_PATTERN and value["max_segments"] == 32
             and value["first_round"] == 5 and value["new_unique_windows"] == 0 and value["repairs_per_stage"] == 1,
             "execution_scope_changed")
    _require(value.get('observation_compatibility')==OBSERVATION_COMPATIBILITY,'observation_compatibility_changed')
    _require(isinstance(value["user_authorization"], str) and value["user_authorization"].strip(), "authorization_text_missing")
    frozen = _read(_bound(output, value["baseline_state_path"], value["baseline_state_sha256"]))
    baseline = value["baseline_request_count"]
    _require(type(baseline) is int and baseline>=0 and baseline == frozen["request_count"] == len(frozen["calls"])
             and json_sha(frozen["calls"]) == value["baseline_calls_sha256"], "baseline_invalid")
    _require(data["request_count"] == len(data["calls"]) and data["calls"][:baseline] == frozen["calls"], "historical_calls_changed")
    _require({k: v for k, v in data.items() if k not in {"calls", "request_count", "artifacts"}} ==
             {k: v for k, v in frozen.items() if k not in {"calls", "request_count", "artifacts"}}, "state_header_changed")
    for key, history in frozen["artifacts"].items():
        _require(data["artifacts"].get(key) == history, "historical_artifacts_changed:" + key)
    for item in value["protected_files"] + value["knowledge_files"]:
        _bound(output, item["path"], item["sha256"])
    for prefix in ("handbook", "goal_guide"):
        _bound(output, value[prefix + "_path"], value[prefix + "_sha256"])
    validate_authorization_snapshot(output, frozen, value["render_directories"])
    proofs = _cache_proofs(output, data)
    names = {}
    for offset, call in enumerate(data["calls"][baseline:], baseline + 1):
        name = call["name"]
        _require(re.fullmatch(ALLOWED_STAGE_PATTERN, name) is not None and name not in names, "duplicate_or_unknown_stage")
        _require(call["id"] == f"glm_{offset:03d}_{name}", "call_sequence_changed")
        round_no, phase, stage, key = _stage(name)
        _progress(output,data,name,names,proofs,call.get('repair_of'))
        folder = output / "calls" / call["id"]
        _require(json_sha(_read(folder / "request.json")) == call["request_sha256"], "request_changed")
        if call["status"] == "received":
            _require(json_sha(_read(folder / "response.json")) == call["response_sha256"], "response_changed")
            if (folder/'parsed.json').is_file():
                _call_value(output,call)
        _require(call["status"] in {"submitted", "received", "uncertain", "failed_known"}, "call_status")
        names[name] = call
    allowed_dirs = set(value["render_directories"])
    for name in data["artifacts"]:
        if re.fullmatch(r"goal_round_[0-9]+", name):
            r = _artifact(data, name)["round"]
            allowed_dirs.update({f"render_{r}", f"render_catalog_{r}"})
        if re.fullmatch(r'goal_result_[0-9]+',name):
            result=_artifact(data,name)
            for row in result['completed_files']:
                _bound(output,row['path'],row['sha256'])
    actual_dirs={p.name for p in output.glob('render_*') if p.is_dir()}
    _require(set(value['render_directories'])<=actual_dirs<=allowed_dirs,'unapproved_or_missing_render_directory')
    return value


def authorize(output, authorization_text, *, goal_id=None):
    from .extension_budget import validate_authorization_snapshot, _protected_files
    _require(isinstance(authorization_text, str) and authorization_text.strip(), "authorization_text_missing")
    output = Path(output).resolve(strict=True)
    probe = type("ReadOnly", (), {"output": output})()
    with file_lock(output / ".goal_authorization.lock"):
        saved = _read(output / "library_state.json")
        if saved["artifacts"].get(AUTHORIZATION):
            return get_authorization(probe)
        directories = sorted(p.name for p in output.glob("render_*") if p.is_dir())
        old = validate_authorization_snapshot(output, saved, directories)
        _require(not any(c["status"] == "submitted" for c in saved["calls"]), "pending_call")
        _require(not any(c["status"] == "uncertain" for c in saved["calls"][old["baseline_request_count"]:]), "new_uncertain_call")
        _require(all(c['status']=='received' for c in saved['calls'][old['baseline_request_count']:]), 'all_extension_replies_must_be_known_received')
        folder = output / "artifacts" / POLICY
        _require(not folder.exists(), "incomplete_authorization_snapshot")
        protected = _protected_files(output)
        folder.mkdir()
        snapshot = folder / "baseline_state.json"
        write_json(snapshot, saved)
        knowledge = folder / "knowledge"
        shutil.copytree(PACKAGE, knowledge)
        files = [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in sorted(knowledge.rglob("*")) if p.is_file()]
        handbook, guide = knowledge / "ACTIVE_FINE_CUT.md", knowledge / "GOAL_EDITING.md"
        value = {"policy": POLICY, "task_id": saved["task_id"], "user_authorization": authorization_text,
            "goal_id": goal_id, "input_lock_sha256": json_sha(saved["input_lock"]),
            "base_request_limit": saved["max_requests"], "baseline_request_count": saved["request_count"],
            "baseline_calls_sha256": json_sha(saved["calls"]), "baseline_state_path": str(snapshot),
            "baseline_state_sha256": sha256_file(snapshot), "render_directories": directories,
            "protected_files": protected, "knowledge_files": files, "first_round": 5,
            "max_segments": 32, "new_unique_windows": 0, "repairs_per_stage": 1,
            "additional_requests": None, "effective_request_limit": None, "request_limit_policy": REQUEST_LIMIT_POLICY,
            "allowed_stage_pattern": ALLOWED_STAGE_PATTERN, "handbook_path": str(handbook),
            "handbook_sha256": sha256_file(handbook), "goal_guide_path": str(guide), "goal_guide_sha256": sha256_file(guide),
            "stop_policy": "No duplicate substantive EDL, no unknown replay, no repeated stage, one known format repair.",
            "model_review_is_not_human_truth": True}
        value['observation_compatibility'] = OBSERVATION_COMPATIBILITY.copy()
        state = LibraryState(output, saved["input_lock"], max_requests=saved["max_requests"])
        state.set_artifact(AUTHORIZATION, value)
        return get_authorization(probe)


authorize_goal = authorize


class GoalState(LibraryState):
    def __init__(self, output):
        output = Path(output).resolve(strict=True)
        get_authorization(type("ReadOnly", (), {"output": output})())
        saved = _read(output / "library_state.json")
        super().__init__(output, saved["input_lock"], max_requests=saved["max_requests"])
        self.max_requests = float("inf")

    def begin_call(self, name, request, *, repair_of=None):
        policy = get_authorization(self)
        self._reload()
        added = self.data["calls"][policy["baseline_request_count"]:]
        _require(not any(c["status"] in {"submitted", "uncertain"} for c in added), "new_outcome_unknown_no_replay")
        _require(re.fullmatch(ALLOWED_STAGE_PATTERN, name or "") is not None and not any(c["name"] == name for c in added), "duplicate_or_unknown_stage")
        r, _, stage, key = _stage(name)
        _artifact(self.data, f"goal_round_{r}")
        proofs = _cache_proofs(self.output, self.data)
        _progress(self.output,self.data,name,{c['name']:c for c in added},proofs,repair_of)
        if stage == 'slice' and not name.endswith('_repair'):
            scope = request.get('observation_scope')
            for old in self.data['calls']:
                if not old['name'].startswith('semantic_slice_') or old.get('repair_of') or old['status']!='received':
                    continue
                repairs = [c for c in self.data['calls'] if c.get('repair_of')==old['id']]
                if len(repairs)!=1 or repairs[0]['status']!='received':
                    continue
                if any((self.output/'calls'/c['id']/'parsed.json').is_file() for c in (old,repairs[0])):
                    continue
                prior = _read(self.output/'calls'/old['id']/'request.json')
                if scope and prior.get('observation_scope') == scope:
                    raise LibraryStopped('goal:known_exhausted_slice_lineage_no_third_observation')
        return super().begin_call(name, request, repair_of=repair_of)

    def _new(self, call):
        policy = get_authorization(self)
        self._reload()
        cid = call["id"] if isinstance(call, dict) else call
        _require(any(c["id"] == cid for c in self.data["calls"][policy["baseline_request_count"]:]), "historical_call_read_only")

    def complete_call(self, call, response, *, usage=None):
        self._new(call)
        return super().complete_call(call, response, usage=usage)

    def fail_call(self, call, error, *, uncertain=True):
        self._new(call)
        return super().fail_call(call, error, uncertain=uncertain)

    def reconcile_received(self, call, response, *, evidence, usage=None):
        self._new(call)
        return super().reconcile_received(call, response, evidence=evidence, usage=usage)

    def reclassify_uncertain(self, call, *, evidence):
        self._new(call)
        return super().reclassify_uncertain(call,evidence=evidence)

    def set_artifact(self, name, payload):
        policy = get_authorization(self)
        frozen = _read(policy["baseline_state_path"])
        _require(name != AUTHORIZATION and name not in frozen["artifacts"], "historical_artifact_read_only")
        self._reload()
        if name.startswith(('goal_round_','goal_result_','goal_cached_')) and self.data['artifacts'].get(name):
            _require(_artifact(self.data,name)==payload,'goal_stage_artifact_immutable')
            return Path(self.data['artifacts'][name][0]['path'])
        return super().set_artifact(name, payload)

    def bind_cached_slice_evidence(self, name, segment, source, proxy, result, *, observation=None, claims=None, hypotheses=None):
        self._reload()
        if any(c["name"] in {name, name + "_repair"} for c in self.data["calls"]):
            return
        r, _, stage, key = _stage(name)
        artifact = f"goal_cached_{r}_{stage}_{key}"
        if self.data["artifacts"].get(artifact):
            _require(_cache_proofs(self.output, self.data)[name]["value"] == result, "cached_return_changed")
            return
        matches = []
        for call in self.data["calls"]:
            if call["status"] == "received" and call["name"].startswith("semantic_" + stage + "_") and (self.output / "calls" / call["id"] / "parsed.json").is_file():
                request, value = _call_value(self.output, call)
                if value == result and request["media_sha256"] == proxy["sha256"]:
                    matches.append(call)
        _require(len(matches) == 1, "cache_requires_one_bound_received_call")
        plan = _read(self.output / f"artifacts/goal_feedback_round_{r}/plan.json")
        proof = {"target_stage": name, "call_id": matches[0]["id"], "segment": {k:segment[k] for k in ('segment_id','source_id','source_in_s','source_out_s')},
                 "source": deepcopy(source), "proxy": deepcopy(proxy), "value": deepcopy(result), "plan_sha256": json_sha(plan)}
        if stage == "claims":
            proof.update(observation=deepcopy(observation), claims=deepcopy(claims), hypotheses=deepcopy(hypotheses))
        _cache_proofs(self.output, self.data, proposed=(artifact,proof))
        self.set_artifact(artifact, proof)

    def usage(self):
        policy = get_authorization(self)
        value = super().usage()
        return {**value, "max_requests": None, "effective_request_limit": None, "additional_requests": None,
                "base_max_requests": policy["base_request_limit"], "goal_baseline_requests": policy["baseline_request_count"],
                "goal_requests": value["requests"] - policy["baseline_request_count"], "request_limit_policy": REQUEST_LIMIT_POLICY}


def stage_state(output):
    return GoalState(output)
