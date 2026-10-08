"""One distinct, last-slot-only planning input after a frozen whole-parent call.

This module is pure contract/prompt plumbing. It never submits a request,
chooses movie cuts, alters the frozen reply, renders, or registers a policy.
The caller must independently bind the genuinely cropped parent input and
authorize the single planning stage and its sole format repair.
"""
from copy import deepcopy
import json

from ..contract import require, rows, text
from . import slot_source_feedback as feedback
from .slot_finecut_contracts import ROOT_OUTPUT_INSTRUCTION

POLICY = "sf_local_source_replan_v2_3"
STAGE = "sf_3_local_source_replan_v2"
RESULT_NAME = "result_local_source_replan_v2.json"
FIELDS = {"status", "baseline_id", "parent_sha256", "slot_id", "candidate",
          "segments", "timing_checks", "boundary_transition", "limitations", "reason"}


def _slot(policy):
    slot = next(s for s in policy["original_outline"]["slots"]
                if s["slot_id"] == policy["blocked_slot_id"])
    require(slot == policy["original_outline"]["slots"][-1], "local_replan:last_slot_only")
    return slot


def validate_input_scope(scope, policy, parent):
    """Require the actual cropped parent slot, never a renamed full-parent input."""
    slot = _slot(policy)
    require(isinstance(scope, dict) and scope == {
        "kind": "continuous_window", "source_sha256": parent["sha256"],
        "source_start_s": slot["start_s"], "source_end_s": slot["end_s"]},
        "local_replan:genuine_cropped_parent_scope_required")
    require(0 <= slot["start_s"] < slot["end_s"] <= parent["duration_s"]
            and slot["end_s"] - slot["start_s"] < parent["duration_s"],
            "local_replan:whole_parent_scope_forbidden")
    return scope


def revised_proposal(value, policy):
    require(value.get("status") == "planned", "local_replan:unavailable_has_no_proposal")
    return {"baseline_id": value["baseline_id"], "parent_sha256": value["parent_sha256"],
            "slot_id": policy["blocked_slot_id"], "candidates": [deepcopy(value["candidate"])]}


def replacement_assembly(value, policy):
    """Mechanically splice only model-owned last-slot output into the frozen EDL."""
    require(value.get("status") == "planned", "local_replan:unavailable_has_no_assembly")
    slot = _slot(policy)
    assembly = deepcopy(policy["original_assembly"])
    blocked_id = slot["slot_id"]
    old_ids = {s["segment_id"] for s in assembly["plan"]["segments"] if s["slot_id"] == blocked_id}
    segments = deepcopy(value["segments"])
    new_ids = [s["segment_id"] for s in segments]
    assembly["plan"]["segments"] = [s for s in assembly["plan"]["segments"]
                                    if s["slot_id"] != blocked_id] + segments
    assembly["plan"]["slots"][-1] = {"slot_id": blocked_id,
        "intended_takeaway": slot["intended_takeaway"], "segment_ids": new_ids}
    assembly["selections"][-1] = {"slot_id": blocked_id,
                                  "candidate_id": value["candidate"]["candidate_id"]}
    assembly["segment_bindings"] = [r for r in assembly["segment_bindings"]
                                     if r["segment_id"] not in old_ids] + [
        {"segment_id": ident, "slot_id": blocked_id,
         "candidate_id": value["candidate"]["candidate_id"], "operation_index": i}
        for i, ident in enumerate(new_ids)]
    assembly["timing_checks"] = [r for r in assembly["timing_checks"]
                                  if r["segment_id"] not in old_ids] + deepcopy(value["timing_checks"])
    assembly["transition_checks"] = [r for r in assembly["transition_checks"]
        if r["from_segment_id"] not in old_ids and r["to_segment_id"] not in old_ids
    ] + deepcopy(value["boundary_transition"])
    for binding in assembly["plan"]["editing_bindings"]:
        replacements = []
        inserted = False
        for ident in binding["segment_ids"]:
            if ident in old_ids:
                if not inserted:
                    replacements.extend(new_ids)
                    inserted = True
            else:
                replacements.append(ident)
        binding["segment_ids"] = replacements
    # Keep all old limitations verbatim and append only new model limitations.
    assembly["limitations"] = assembly["limitations"] + deepcopy(value["limitations"])
    return assembly


def revised_full_proposals(value, policy):
    proposal = revised_proposal(value, policy)
    return deepcopy([proposal if p["slot_id"] == policy["blocked_slot_id"] else p
                     for p in policy["original_proposals"]])


def validate_replan(value, policy, parent, catalog, windows, reference, methods, facts, *, navigation=None):
    """Validate one flat model answer with the existing strict evidence contracts."""
    require(isinstance(value, dict) and set(value) == FIELDS, "local_replan:flat_output_fields_required")
    require(value["baseline_id"] == parent["baseline_id"] and value["parent_sha256"] == parent["sha256"]
            and value["slot_id"] == policy["blocked_slot_id"], "local_replan:parent_or_slot_binding")
    text(value["reason"], "local_replan/reason")
    for limitation in rows(value["limitations"], "local_replan/limitations", nonempty=False):
        text(limitation, "local_replan/limitation")
    if value["status"] == "unavailable":
        require(bool(value["limitations"]) and value["candidate"] is None
                and value["segments"] == value["timing_checks"] == value["boundary_transition"] == [],
                "local_replan:unavailable_must_stop_without_edl")
        return value
    require(value["status"] == "planned" and isinstance(value["candidate"], dict),
            "local_replan:planned_candidate_required")
    rows(value["segments"], "local_replan/segments")
    rows(value["timing_checks"], "local_replan/timing_checks")
    rows(value["boundary_transition"], "local_replan/boundary_transition")
    require(all(s.get("slot_id") == policy["blocked_slot_id"] for s in value["segments"]),
            "local_replan:only_blocked_slot_segments_allowed")
    wrapped = {"status": "planned", "baseline_id": value["baseline_id"],
               "parent_sha256": value["parent_sha256"], "change_reason": value["reason"],
               "revised_proposals": [revised_proposal(value, policy)],
               "replacement_assembly": replacement_assembly(value, policy),
               "limitations": value["limitations"]}
    # Includes source/role/range containment, original information and exposure
    # minima, meaningful source geometry, frame-level bypass rejection, all
    # visual-claim/timing bindings, original obligations, and adjacent checks.
    feedback.validate_replan(wrapped, policy, parent, catalog, windows, reference, methods,
                            facts, navigation=navigation)
    # A fallible/unresolved meaning label does not waive the unchanged exposure
    # obligation. If it cannot be met, the bounded local stage is unavailable.
    for operation in value["candidate"]["operations"]:
        for essential in operation["essential_intervals"]:
            exposure = (essential["source_end_s"] - essential["source_start_s"]) / operation["speed"]
            if essential.get("continues_in_tail_frame", False):
                exposure += operation["freeze_tail_s"]
            require(exposure + .001 >= essential["min_readable_s"],
                    "local_replan:declared_exposure_below_original_minimum")
    return value


def prompt(policy, parent, catalog, windows, facts, *, navigation=None, knowledge=""):
    """Small local context; no duplicated full assembly or unknown reply body."""
    slot = _slot(policy)
    operation = policy["original_operation"]
    original = parent["provenance"][operation["parent_segment_index"]]
    window = next(w for w in windows if w["window_id"] == original["window_id"])
    source = next(s for s in catalog["sources"] if s["source_id"] == original["source_id"])
    previous_segment = next(s for s in reversed(policy["original_assembly"]["plan"]["segments"])
                            if s["slot_id"] != slot["slot_id"])
    prior_proposal = next(p for p in policy["original_proposals"] if p["slot_id"] == slot["slot_id"])
    contract = {"status": "planned", "baseline_id": parent["baseline_id"],
        "parent_sha256": parent["sha256"], "slot_id": slot["slot_id"],
        "candidate": {"candidate_id": "new_local_candidate", "operations": [{
            "parent_segment_index": operation["parent_segment_index"],
            "source_in_s": "自主选择的原片number", "source_out_s": "自主选择的原片number",
            "speed": 1, "freeze_tail_s": 0, "reason": "保留/舍去/变速依据", "evidence_ids": ["已有父事实ID"],
            "essential_intervals": [{"source_start_s": "原片number", "source_end_s": "原片number",
                "min_readable_s": "不低于原值的number", "information": "original_information原文",
                "evidence_ids": ["已有父事实ID"], "continues_in_tail_frame": False}]}],
            "meaning_status": "preserved", "rationale": "局部选择理由", "limitations": []},
        "segments": [{"segment_id": "new_local_segment", "slot_id": slot["slot_id"],
            "source_id": original["source_id"], "window_id": original["window_id"],
            "source_in_s": "candidate对应operation原值", "source_out_s": "对应原值",
            "speed": 1, "freeze_tail_s": 0, "role_ids": original["role_ids"], "framing": "fit", "look": "none",
            "visual_claims": [{"claim_id": "new_local_claim", "kind": "visual_action",
                               "description": "本次微切实际应可见的主张，另行独立核验"}]}],
        "timing_checks": [{"segment_id": "new_local_segment", "essential_claims": [
            {"essential_interval_index": 0, "claim_ids": ["new_local_claim"]}]}],
        "boundary_transition": [{"from_segment_id": previous_segment["segment_id"],
            "to_segment_id": "new_local_segment", "relation": "从前段到本段的观众可见关系", "status": "planned"}],
        "limitations": [], "reason": "真正源几何改变及保留原信息的依据"}
    payload = {"task": "one_last_slot_only_distinct_planning_input", "slot": slot,
        "provided_media_time_domain": {"local_start_s": 0, "local_end_s": slot["end_s"] - slot["start_s"],
            "parent_output_start_s": slot["start_s"], "parent_output_end_s": slot["end_s"]},
        "original_information": operation["essential_intervals"], "original_operation": operation,
        "parent_provenance_segment": original, "known_local_proposal": prior_proposal,
        "known_parent_navigation": navigation if navigation is not None else facts,
        "source": {k: source[k] for k in ("source_id", "sha256", "duration_s")},
        "watched_source_range": {k: window[k] for k in ("window_id", "source_start_s", "source_end_s", "observation")},
        "entry_boundary": {"from_segment_id": previous_segment["segment_id"],
            "original_relation": policy["original_assembly"]["transition_checks"][-1]["relation"]},
        "response_contract": contract,
        "allowed_values": {"status": ["planned", "unavailable"], "meaning_status": ["preserved", "unresolved"],
            "visual_claim_kind": ["visual_action", "visual_outcome", "identity"],
            "framing": ["fit", "crop"], "look": ["none", "grayscale"], "transition_status": ["planned", "unresolved"]}}
    return (ROOT_OUTPUT_INSTRUCTION +
        "只规划提供的最后一段局部父视频，不返回完整plan或replacement_assembly，不复制上下文。"
        "之前整片任务结果不明并冻结，本任务媒体只含这一slot；不要续写或猜测该未知回复。"
        "自主选择1至2段真正不同的关键瞬间微切，source_in_s/out_s用原电影秒数，"
        "且每段须严格位于original_operation范围内部或其真子区间，按原先顺序。"
        "不能复用整个旧范围，不能拆开后联合覆盖整个旧范围，不能靠微移一帧/epsilon规避已耗尽观察。"
        "保留slot所有表达义务、original_information中的information原文及每项min_readable_s，不能降低最低曝光。"
        "速度、停留由你决定；尾帧停留只能增加在最后一帧持续存在的信息曝光，不能补已结束的动作。"
        "候选的preserved只是提案，父navigation不是源动作完成/时长证据；原片与成片后续仍独立审看。"
        "candidate为一个标准候选对象，segments为每个operation对应的完整EDL列表，不返回candidates包装。"
        "每个essential必须有非空evidence_ids，每个EDL的visual_claims要由timing_checks完整绑定。"
        "boundary_transition是列表：先前一段末镜到新首镜，再按顺序列两微切之间的关系；一镜时只一个关系。"
        "所有时间/速度/停留/min_readable_s为JSON number，ID引用和枚举从上下文取合法单值。"
        "无法在这些条件下完成时status=unavailable、candidate=null、segments/timing_checks/"
        "boundary_transition=[]，limitations及reason说明实际阻碍，随后停止。\n" +
        knowledge + "\n" +
        json.dumps(payload, ensure_ascii=False, allow_nan=False))
