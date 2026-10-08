"""One bounded creative replan after a selected source input is exhausted.

This is a new model planning stage, never a third assembly format repair or
source observation. It does not choose source times or modify historical bodies.
"""
from copy import deepcopy
import json
import re
from pathlib import Path

from ..contract import require, rows, text
from .contracts import parse_model_json
from .pipeline import _read, _window_context
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import json_sha, scope_fingerprint

POLICY = "sf_source_feedback_replan_v1_3"
STAGE = "sf_3_source_feedback_replan_v1"
RESULT_NAME = "result_source_feedback_resume.json"
ASSEMBLY_POLICY = "sf_request_bound_assembly_v1_3"


def _inputs(output, data, grant):
    from .slot_finecut_budget import _exhausted_inputs
    output = Path(output)
    entries = data["artifacts"].get(ASSEMBLY_POLICY, [])
    require(len(entries) == 1, "source_feedback:bound_assembly_required")
    binding = _read(entries[0]["path"])
    require(json_sha(binding) == entries[0]["sha256"] and binding["parent_round"] == 3
            and binding["chosen_call_id"] == binding["repair_call_id"], "source_feedback:assembly_changed")
    body = binding["body"]
    call = next(c for c in data["calls"] if c["id"] == binding["chosen_call_id"])
    require(call["status"] == "received", "source_feedback:known_reply_required")
    call_folder = output / "calls" / call["id"]
    response = _read(call_folder / "response.json")
    raw = parse_model_json("\n".join(c["text"] for c in response["result"]["content"] if c.get("type") == "text"))
    require(json_sha(response) == call["response_sha256"] and {**raw, **binding["program_field_additions"]} == body
            and not (call_folder / "parsed.json").exists(), "source_feedback:old_reply_cannot_be_rewritten")
    preparation = _read(grant["preparation_path"])
    parent = next(p for p in preparation["parents"] if p["round"] == 3)
    folder = Path(grant["execution_directory"]) / parent["baseline_id"]
    outline, proposals = _read(folder / "outline.json"), _read(folder / "proposals.json")
    catalog = _read(output / "catalog/inventory.json")
    sources = {s["source_id"]: s for s in catalog["sources"]}
    exhausted = _exhausted_inputs(output, data["calls"])
    blockers = []
    for segment in body["plan"]["segments"]:
        scope = {"kind": "continuous_window", "source_sha256": sources[segment["source_id"]]["sha256"],
                 "source_start_s": segment["source_in_s"], "source_end_s": segment["source_out_s"]}
        for prior in exhausted:
            if scope_fingerprint(scope) == scope_fingerprint(prior["scope"]):
                blockers.append({"segment_id": segment["segment_id"], "slot_id": segment["slot_id"], **deepcopy(prior)})
    require(len(blockers) == 1, "source_feedback:exactly_one_exhausted_segment_required")
    blocked = blockers[0]
    slot = next(s for s in outline["slots"] if s["slot_id"] == blocked["slot_id"])
    require(body["plan"]["slots"][-1]["slot_id"] == slot["slot_id"], "source_feedback:last_slot_only")
    selection = next(s for s in body["selections"] if s["slot_id"] == slot["slot_id"])
    candidate = next(c for p in proposals if p["slot_id"] == slot["slot_id"]
                     for c in p["candidates"] if c["candidate_id"] == selection["candidate_id"])
    require(len(candidate["operations"]) == 1, "source_feedback:one_blocked_operation_required")
    operation = candidate["operations"][0]
    original = parent["provenance"][operation["parent_segment_index"]]
    require((operation["source_in_s"], operation["source_out_s"]) ==
            (original["source_in_s"], original["source_out_s"]) ==
            (blocked["scope"]["source_start_s"], blocked["scope"]["source_end_s"]),
            "source_feedback:blocked_whole_parent_operation_required")
    files = deepcopy(binding["protected_files"])
    for file in [Path(entries[0]["path"]), folder / "outline.json", folder / "proposals.json"]:
        if not any(Path(row["path"]).resolve() == file.resolve() for row in files):
            files.append({"path": str(file), "sha256": sha256_file(file)})
    for ident in (blocked["original_call"], blocked["repair_call"]):
        for name in ("request.json", "response.json", "protocol_failure.json"):
            file = output / "calls" / ident / name
            files.append({"path": str(file), "sha256": sha256_file(file)})
    require(all(sha256_file(row["path"]) == row["sha256"] for row in files), "source_feedback:history_changed")
    return {"parent_round": 3, "stage": STAGE, "assembly_binding_path": entries[0]["path"],
            "assembly_binding_sha256": entries[0]["sha256"], "original_assembly": deepcopy(body),
            "original_outline": outline, "original_proposals": proposals,
            "blocked_segment": blocked, "blocked_slot_id": slot["slot_id"],
            "original_operation": operation, "media_sha256": binding["media_sha256"],
            "observation_scope": binding["observation_scope"], "protected_files": files,
            "original_essential_intervals_sha256": json_sha(operation["essential_intervals"]),
            "max_replacement_operations": 2, "max_final_slices": len(body["plan"]["segments"]) + 1}


def read_validate(output, data, grant):
    entries = data["artifacts"].get(POLICY, [])
    if not entries:
        return None
    require(len(entries) == 1, "source_feedback:one_policy_required")
    policy = _read(entries[0]["path"])
    require(json_sha(policy) == entries[0]["sha256"] and policy["policy"] == POLICY
            and policy["task_id"] == data["task_id"] and policy["preparation_id"] == grant["preparation_id"]
            and policy["planning_attempts"] == policy["repairs_per_stage"] == 1
            and policy["new_unique_windows"] == 0 and policy["renders_per_parent"] == 1
            and policy["no_automatic_replanning"] is True and policy["result_name"] == RESULT_NAME
            and policy["old_failures_preserved"] is True and policy["semantic_truth_established"] is False,
            "source_feedback:policy_changed")
    text(policy.get("user_instruction"), "source_feedback/user_instruction")
    count = policy["baseline_request_count"]
    require(type(count) is int and grant["baseline_request_count"] <= count <= len(data["calls"])
            and json_sha(data["calls"][:count]) == policy["prefix_calls_sha256"], "source_feedback:prefix_changed")
    require(all(c["status"] == "received" for c in data["calls"][grant["baseline_request_count"]:count]),
            "source_feedback:pending_or_unknown_blocks_activation")
    baseline = {**data, "calls": data["calls"][:count]}
    bound = _inputs(output, baseline, grant)
    require(all(policy[k] == v for k, v in bound.items()), "source_feedback:inputs_changed")
    later = [c for c in data["calls"][count:] if
             re.match(r"^(?:sf_3_|semantic_(?:slice|claim|claims)_21_)", c["name"])]
    require(not later or later[0]["name"] == STAGE, "source_feedback:first_stage_changed")
    calls = [c for c in data["calls"][count:] if c["name"] in {STAGE, STAGE + "_repair"}]
    require(len(calls) <= 2 and all(c["name"] == (STAGE if i == 0 else STAGE + "_repair")
            and (not c.get("repair_of") if i == 0 else c.get("repair_of") == calls[0]["id"]
                 and calls[0]["status"] == "received") for i, c in enumerate(calls)), "source_feedback:no_replanning_loop")
    return policy


def record_source_feedback(state, user_instruction):
    """Register once before any new feedback call; no old record is rewritten."""
    from .slot_finecut_budget import STATE_MUTEX
    with STATE_MUTEX:
        grant = state.assert_protected()
        existing = read_validate(state.output, state.data, grant)
        if existing:
            return existing
        text(user_instruction, "source_feedback/user_instruction")
        require(all(c["status"] == "received" for c in state.data["calls"][grant["baseline_request_count"]:]),
                "source_feedback:pending_or_unknown_blocks_activation")
        require(not state.data["artifacts"].get("sf_3_render_claim"), "source_feedback:render_already_claimed")
        bound = _inputs(state.output, state.data, grant)
        policy = {"policy": POLICY, "task_id": state.data["task_id"], "preparation_id": grant["preparation_id"],
                  "baseline_request_count": len(state.data["calls"]), "prefix_calls_sha256": json_sha(state.data["calls"]),
                  "planning_attempts": 1, "repairs_per_stage": 1, "new_unique_windows": 0, "renders_per_parent": 1,
                  "no_automatic_replanning": True, "result_name": RESULT_NAME, "user_instruction": user_instruction,
                  "old_failures_preserved": True, "semantic_truth_established": False,
                  "protocol_limitations": [
                      "One new model-owned creative proposal, not a third assembly repair or source observation.",
                      "Original slot obligations, essential information and minimum exposure remain binding.",
                      "A changed source geometry is only proposed progress until independent source and actual-output reviews.",
                      "Any unavailable proposal, exhausted repair, source blocker or review failure stops without another replan."], **bound}
        state.set_artifact(POLICY, policy)
        return read_validate(state.output, state.data, grant)


def validate_replan(value, policy, parent, catalog, windows, reference, methods, facts, *, navigation=None):
    """Validate real model microcuts; progress is proposed until source review."""
    from .slot_finecut import validate_assembly
    from .slot_finecut_contracts import validate_proposal
    require(isinstance(value, dict) and value.get("baseline_id") == parent["baseline_id"]
            and value.get("parent_sha256") == parent["sha256"], "source_feedback:parent_binding")
    text(value.get("change_reason"), "source_feedback/change_reason")
    limitations = rows(value.get("limitations"), "source_feedback/limitations", nonempty=False)
    for limitation in limitations:
        text(limitation, "source_feedback/limitation")
    if value.get("status") == "unavailable":
        require(bool(limitations) and not value.get("revised_proposals") and not value.get("replacement_assembly"),
                "source_feedback:unavailable_must_stop")
        return value
    require(value.get("status") == "planned", "source_feedback:status")
    revised = rows(value.get("revised_proposals"), "source_feedback/revised_proposals")
    require(len(revised) == 1 and revised[0].get("slot_id") == policy["blocked_slot_id"],
            "source_feedback:only_blocked_slot_revised")
    proposal = revised[0]
    slot = next(s for s in policy["original_outline"]["slots"] if s["slot_id"] == policy["blocked_slot_id"])
    validate_proposal(proposal, parent, slot, facts, windows, navigation=navigation)
    require(len(proposal["candidates"]) == 1, "source_feedback:one_replacement_candidate")
    candidate = proposal["candidates"][0]
    previous_candidates = next(p["candidates"] for p in policy["original_proposals"]
                               if p["slot_id"] == slot["slot_id"])
    require(candidate["candidate_id"] not in {c["candidate_id"] for c in previous_candidates},
            "source_feedback:new_candidate_id_required")
    operations, old = candidate["operations"], policy["original_operation"]
    require(0 < len(operations) <= policy["max_replacement_operations"], "source_feedback:replacement_operation_cap")
    essentials = old["essential_intervals"]
    required_information = {e["information"]: max(r["min_readable_s"] for r in essentials
                                                if r["information"] == e["information"]) for e in essentials}
    retained, changed_geometry = set(), False
    previous_end = old["source_in_s"]
    for operation in operations:
        require(operation["parent_segment_index"] == old["parent_segment_index"]
                and old["source_in_s"] <= operation["source_in_s"] < operation["source_out_s"] <= old["source_out_s"]
                and (operation["source_in_s"], operation["source_out_s"]) != (old["source_in_s"], old["source_out_s"])
                and previous_end <= operation["source_in_s"], "source_feedback:proper_ordered_microcuts_required")
        previous_end = operation["source_out_s"]
        for essential in operation["essential_intervals"]:
            info = essential["information"]
            require(info in required_information and essential["min_readable_s"] >= required_information[info],
                    "source_feedback:original_information_or_minimum_changed")
            retained.add(info)
            changed_geometry |= all((essential["source_start_s"], essential["source_end_s"]) !=
                                    (e["source_start_s"], e["source_end_s"]) for e in essentials if e["information"] == info)
    require(retained == set(required_information) and changed_geometry, "source_feedback:essential_geometry_must_change")
    retained_duration = sum(o["source_out_s"] - o["source_in_s"] for o in operations)
    require(retained_duration < old["source_out_s"] - old["source_in_s"], "source_feedback:full_envelope_split_is_not_progress")
    fps = parent['render_input']['compiled']['fps']
    removed_frames = round((old['source_out_s'] - old['source_in_s']) * fps) - sum(
        round((o['source_out_s'] - o['source_in_s']) * fps) for o in operations)
    require(removed_frames > 1, 'source_feedback:epsilon_or_one_frame_trim_is_not_progress')
    assembly = value.get("replacement_assembly")
    require(isinstance(assembly, dict), "source_feedback:replacement_assembly_object_required")
    proposals = [proposal if p["slot_id"] == slot["slot_id"] else p for p in policy["original_proposals"]]
    validate_assembly(assembly, parent, policy["original_outline"], proposals, catalog, windows, reference, methods)
    baseline = policy["original_assembly"]
    blocked_ids = {s["segment_id"] for s in baseline["plan"]["segments"] if s["slot_id"] == slot["slot_id"]}
    new_ids = {s["segment_id"] for s in assembly["plan"]["segments"] if s["slot_id"] == slot["slot_id"]}
    require(not (blocked_ids & new_ids), "source_feedback:new_source_segment_ids_required")
    require(len(assembly["plan"]["segments"]) <= policy["max_final_slices"], "source_feedback:final_slice_cap")
    require({k: v for k, v in assembly["plan"].items() if k not in {"segments", "slots", "editing_bindings"}} ==
            {k: v for k, v in baseline["plan"].items() if k not in {"segments", "slots", "editing_bindings"}},
            "source_feedback:unrelated_plan_settings_changed")
    for key in ("selections", "segment_bindings", "timing_checks"):
        owner = (lambda row: row["segment_id"]) if key != "selections" else (lambda row: row["slot_id"])
        allowed = blocked_ids if key != "selections" else {slot["slot_id"]}
        require([r for r in assembly[key] if owner(r) not in allowed and not
                 (key != "selections" and owner(r) in new_ids)] ==
                [r for r in baseline[key] if owner(r) not in allowed], "source_feedback:unblocked_binding_changed")
    require([s for s in assembly["plan"]["segments"] if s["slot_id"] != slot["slot_id"]] ==
            [s for s in baseline["plan"]["segments"] if s["slot_id"] != slot["slot_id"]], "source_feedback:unblocked_segments_changed")
    require([s for s in assembly["plan"]["slots"] if s["slot_id"] != slot["slot_id"]] ==
            [s for s in baseline["plan"]["slots"] if s["slot_id"] != slot["slot_id"]], "source_feedback:unblocked_slots_changed")
    unblocked_ids = {s["segment_id"] for s in baseline["plan"]["segments"] if s["slot_id"] != slot["slot_id"]}
    require([t for t in assembly['transition_checks'] if t['from_segment_id'] in unblocked_ids and t['to_segment_id'] in unblocked_ids] ==
            [t for t in baseline['transition_checks'] if t['from_segment_id'] in unblocked_ids and t['to_segment_id'] in unblocked_ids],
            'source_feedback:unblocked_transitions_changed')
    require([{k: v for k, v in b.items() if k != 'segment_ids'} for b in assembly['plan']['editing_bindings']] ==
            [{k: v for k, v in b.items() if k != 'segment_ids'} for b in baseline['plan']['editing_bindings']],
            'source_feedback:original_method_commitments_changed')
    for new, original in zip(assembly['plan']['editing_bindings'], baseline['plan']['editing_bindings'], strict=True):
        require([ident for ident in new['segment_ids'] if ident not in new_ids] ==
                [ident for ident in original['segment_ids'] if ident not in blocked_ids]
                and bool(set(new['segment_ids']) & new_ids) == bool(set(original['segment_ids']) & blocked_ids),
                'source_feedback:unblocked_method_bindings_changed')
    return value


def revised_full_proposals(value, policy):
    """Project the model's one revised slot into the unchanged proposal list."""
    require(value.get('status') == 'planned', 'source_feedback:unavailable_has_no_proposals')
    proposal = value['revised_proposals'][0]
    return deepcopy([proposal if p['slot_id'] == policy['blocked_slot_id'] else p for p in policy['original_proposals']])


def prompt(policy, parent, catalog, windows, reference, methods, knowledge, facts, *, navigation=None):
    from .slot_finecut import assembly_prompt
    from .slot_finecut_contracts import ROOT_OUTPUT_INSTRUCTION
    template = assembly_prompt(parent, policy["original_outline"], policy["original_proposals"],
                               catalog, windows, reference, methods, knowledge)
    return (ROOT_OUTPUT_INSTRUCTION + "这是独立的源证据反馈重规划，不是旧汇总的第三次修复，也不是重复观察耗尽范围。"
            "只修改blocked_slot内源范围；其它已选操作和完整段记录保持原值。"
            "从原parent_segment中自主选择1到2个真正不同的关键瞬间微切，或返回unavailable并停止。"
            "不把编码、改ID、改文字、只改速度、微移一帧当作剪辑进展；说明留下/舍去的信息及关系。"
            "所有原slot义务逐字保留，原essential信息逐条保留、min_readable_s不得降低。"
            "新原片范围必须严格短于原耗尽范围，不能拆分再覆盖原完整包络。"
            "父输出导航不证明源动作、完成结果或曝光，新方案全部最终源片仍独立精看。"
            "一次规划及一次格式修复后，任一源证据或真实成片门槛失败就停止，不自动再次重规划。\n" +
            json.dumps({"original_assembly": policy["original_assembly"], "original_outline": policy["original_outline"],
                "original_proposals": policy["original_proposals"], "blocked_segment": policy["blocked_segment"],
                "original_operation": policy["original_operation"], "parent_output_facts": facts,
                **({"parent_navigation": navigation} if navigation is not None else {}),
                "parent": parent, "watched_windows": [_window_context(w, include_speech=False) for w in windows],
                "assembly_protocol_reference": template,
                "response_contract": {"status": "planned", "baseline_id": parent["baseline_id"],
                    "parent_sha256": parent["sha256"], "change_reason": "实质源信息变化及关联依据",
                    "revised_proposals": [{"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
                        "slot_id": policy["blocked_slot_id"], "candidates": []}],
                    "replacement_assembly": {}, "limitations": []},
                "allowed_statuses": ["planned", "unavailable"], "unavailable_contract": {
                    "status": "unavailable", "baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
                    "change_reason": "当前证据无法提供可执行微切", "revised_proposals": [],
                    "replacement_assembly": None, "limitations": ["具体无法继续的依据"]}}, ensure_ascii=False))
