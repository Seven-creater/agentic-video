"""Forward, fact-grounded edit reconstruction; no calls, state or creative edits.

Original information is kept once per slot/information/minimum group. Several
model-owned shots can share that obligation without multiplying its exposure.
This validates a proposal only; independent source/output reviews remain needed.
"""
from __future__ import annotations

from copy import deepcopy
import json

from ..contract import ids, number, refs, require, rows, text
from . import contracts
from .render import compile_library_plan, validate_caption_layout
from .state import json_sha
from .slot_finecut_contracts import ROOT_OUTPUT_INSTRUCTION

PROTOCOL = "sf_fact_grounded_reconstruction_v1"
FIELDS = {"status", "baseline_id", "parent_sha256", "plan", "essential_groups",
          "transition_checks", "limitations", "reason"}
SOURCE_KINDS = {"visual_action", "visual_state", "visual_outcome", "visible_text"}


def canonical_information_groups(outline, proposals, *, prior_assembly=None):
    """Copy unchanged obligations; selected proposals avoid unchosen alternatives.

The caller binds this result before the new task. Repeated information with the
same minimum is one obligation, even when old operations repeated it verbatim.
Different original minima remain separate; neither is lowered here.
"""
    slots = {s["slot_id"] for s in outline["slots"]}
    require(ids(proposals, "slot_id", "reconstruction/original_proposals") == slots,
            "reconstruction:original_proposal_slot_coverage")
    selections = ({r["slot_id"]: r["candidate_id"] for r in prior_assembly["selections"]}
                  if prior_assembly is not None else None)
    if prior_assembly is not None:
        require(ids(prior_assembly["selections"], "slot_id", "reconstruction/original_selections") == slots,
                "reconstruction:original_selection_slot_coverage")
    groups = {}
    for proposal in proposals:
        sid = proposal["slot_id"]
        require(sid in slots, "reconstruction:original_information_unknown_slot")
        if selections is not None:
            require(selections[sid] in {c["candidate_id"] for c in proposal["candidates"]},
                    "reconstruction:original_selected_candidate_missing")
        for candidate in proposal["candidates"]:
            if selections is not None and selections.get(sid) != candidate["candidate_id"]:
                continue
            for operation in candidate["operations"]:
                for essential in operation["essential_intervals"]:
                    info = essential["information"]
                    text(info, "reconstruction/original_information")
                    minimum = number(essential["min_readable_s"], "reconstruction/original_minimum", .001)
                    key = (sid, info, minimum)
                    if key not in groups:
                        groups[key] = {"group_id": "info_" + json_sha(list(key))[:20],
                            "slot_id": sid, "information": info,
                            "min_readable_s": deepcopy(essential["min_readable_s"])}
    require(bool(groups), "reconstruction:original_information_required")
    return list(groups.values())


def _union_duration(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return sum(end-start for start, end in merged)


def proposed_group_exposure(plan, group):
    """Union proposed visible output intervals, including only explicit tail holds.

No source evidence is inferred. The runtime must replace these proposed bounds
with supported independent source facts and recheck the same information group.
"""
    fps, cursor, mapped = plan["fps"], 0., {}
    for segment in plan["segments"]:
        motion = round((segment["source_out_s"]-segment["source_in_s"])/segment["speed"]*fps)/fps
        hold = round(segment.get("freeze_tail_s", 0)*fps)/fps
        mapped[segment["segment_id"]] = (segment, cursor, motion, hold)
        cursor += motion + hold
    intervals = []
    for exposure in group["exposures"]:
        segment, offset, motion, hold = mapped[exposure["segment_id"]]
        a = (exposure["source_start_s"]-segment["source_in_s"])/segment["speed"]
        b = (exposure["source_end_s"]-segment["source_in_s"])/segment["speed"]
        intervals.append((offset+min(motion, a), offset+min(motion, b)))
        if exposure["continues_in_tail_frame"] and hold:
            intervals.append((offset+motion, offset+motion+hold))
    return _union_duration(intervals)


def _operation_claims(segment):
    require("visual_claims" not in segment, "reconstruction:legacy_mixed_claims_not_allowed")
    source = rows(segment.get("source_claims"), "reconstruction/source_claims")
    source_ids = ids(source, "claim_id", "reconstruction/source_claims")
    for claim in source:
        require(set(claim) == {"claim_id", "kind", "description"}, "reconstruction:source_claim_fields")
        require(claim["kind"] in SOURCE_KINDS, "reconstruction:source_claim_kind")
        text(claim["description"], "reconstruction/source_claim/description")
    operations = rows(segment.get("output_operation_claims"), "reconstruction/output_operation_claims", nonempty=False)
    operation_ids = ids(operations, "claim_id", "reconstruction/output_operation_claims", nonempty=False)
    require(not source_ids & operation_ids, "reconstruction:source_output_claim_id_overlap")
    seen = set()
    for claim in operations:
        require(set(claim) == {"claim_id", "kind", "description", "expected_value"},
                "reconstruction:output_operation_claim_fields")
        kind = claim["kind"]
        require(kind in {"speed", "tail_hold"} and kind not in seen, "reconstruction:operation_kind_or_duplicate")
        seen.add(kind)
        text(claim["description"], "reconstruction/output_operation/description")
        expected = number(claim["expected_value"], "reconstruction/output_operation/expected")
        actual = segment["speed"] if kind == "speed" else segment.get("freeze_tail_s", 0)
        require(expected == actual, "reconstruction:operation_value_changed")
    require(segment["speed"] == 1 or "speed" in seen, "reconstruction:retiming_needs_output_claim")
    require(not segment.get("freeze_tail_s", 0) or "tail_hold" in seen, "reconstruction:hold_needs_output_claim")
    return source_ids | operation_ids


def _motion_runs(segments, fps):
    """Ignore IDs and fictitious cuts that reproduce continuous source playback."""
    result = []
    for original in segments:
        segment = {key: original[key] for key in ("source_id", "source_in_s", "source_out_s")}
        segment.update(speed=original.get("speed", 1), freeze_tail_s=original.get("freeze_tail_s", 0))
        if result and result[-1]["source_id"] == segment["source_id"] and not result[-1]["freeze_tail_s"] \
                and result[-1]["speed"] == segment["speed"] \
                and abs(result[-1]["source_out_s"]-segment["source_in_s"])*fps < .5:
            result[-1]["source_out_s"] = segment["source_out_s"]
            result[-1]["freeze_tail_s"] = segment["freeze_tail_s"]
        else:
            result.append(segment)
    return result


def _substantively_changed(segments, old, fps):
    segments, old = _motion_runs(segments, fps), _motion_runs(old, fps)
    if len(segments) != len(old):
        return True
    for a, b in zip(segments, old, strict=True):
        if a["source_id"] != b["source_id"]:
            return True
        for key in ("source_in_s", "source_out_s"):
            if abs(round(a[key]*fps)-round(b[key]*fps)) > 1:
                return True
        durations = [round((r["source_out_s"]-r["source_in_s"])/r.get("speed", 1)*fps) for r in (a, b)]
        if abs(durations[0]-durations[1]) > 1:
            return True
        if abs(round(a.get("freeze_tail_s", 0)*fps)-round(b.get("freeze_tail_s", 0)*fps)) > 1:
            return True
    return False


def _block_scope_replay(plan, catalog, blocked_scopes):
    sources = {s["source_id"]: s for s in catalog["sources"]}
    fps = plan["fps"]
    for blocked in blocked_scopes:
        scope = blocked.get("scope", blocked)
        if scope.get("kind") != "continuous_window":
            continue
        start, end = scope["source_start_s"], scope["source_end_s"]
        overlaps = []
        for segment in plan["segments"]:
            if sources[segment["source_id"]]["sha256"] != scope["source_sha256"]:
                continue
            a, b = segment["source_in_s"], segment["source_out_s"]
            require(not (a <= start + 1e-9 and b >= end - 1e-9), "reconstruction:blocked_whole_scope_replay")
            if a < end and start < b:
                overlaps.append((max(a, start), min(b, end)))
        if overlaps:
            # Real microcuts must omit more than a quantized source frame, rather
            # than renaming/reencoding/splitting the exhausted whole envelope.
            require((end-start-_union_duration(overlaps))*fps > 1.000001,
                    "reconstruction:blocked_union_or_epsilon_replay")


def validate_reconstruction(value, parent, outline, catalog, windows, reference, methods,
                            original_information_groups, blocked_scopes=(), *, prior_assembly=None):
    """Validate model choices without changing any old contract or source fact."""
    require(isinstance(value, dict) and set(value) == FIELDS, "reconstruction:root_fields")
    require(value["baseline_id"] == parent["baseline_id"] and value["parent_sha256"] == parent["sha256"],
            "reconstruction:parent_binding")
    text(value["reason"], "reconstruction/reason")
    for limitation in rows(value["limitations"], "reconstruction/limitations", nonempty=False):
        text(limitation, "reconstruction/limitation")
    if value["status"] == "unavailable":
        require(value["plan"] is None and value["essential_groups"] == value["transition_checks"] == []
                and bool(value["limitations"]), "reconstruction:unavailable_has_no_plan")
        return value
    require(value["status"] == "planned", "reconstruction:status")
    plan = value["plan"]
    contracts.validate_plan(plan, catalog, windows, reference["sha256"], reference["duration_s"],
        reference_audio_stream_index=reference.get("audio_stream_index"), editing_reference=methods)
    compile_library_plan(catalog, plan, fps=plan["fps"], width=plan["width"], height=plan["height"])
    validate_caption_layout(plan, plan["width"], plan["height"])
    require([(s["slot_id"], s["intended_takeaway"]) for s in plan["slots"]] ==
            [(s["slot_id"], s["intended_takeaway"]) for s in outline["slots"]],
            "reconstruction:original_slot_obligation_changed")
    require([s["slot_id"] for s in plan["segments"]] ==
            [sid for slot in plan["slots"] for sid in [slot["slot_id"]]*len(slot["segment_ids"])],
            "reconstruction:segment_slot_order_changed")
    all_claim_ids = set()
    segments = {s["segment_id"]: s for s in plan["segments"]}
    for segment in plan["segments"]:
        known = _operation_claims(segment)
        require(not known & all_claim_ids, "reconstruction:duplicate_global_claim_id")
        all_claim_ids.update(known)
    expected_groups = {g["group_id"]: g for g in original_information_groups}
    groups = rows(value["essential_groups"], "reconstruction/essential_groups")
    require(ids(groups, "group_id", "reconstruction/essential_groups") == set(expected_groups),
            "reconstruction:original_information_must_be_retained_exactly_once")
    for group in groups:
        original = expected_groups[group["group_id"]]
        number(group.get("min_readable_s"), "reconstruction/group_minimum", .001)
        require(set(group) == {"group_id", "slot_id", "information", "min_readable_s", "exposures"}
                and all(group[k] == original[k] for k in original),
                "reconstruction:original_information_or_minimum_changed")
        for exposure in rows(group["exposures"], "reconstruction/exposures"):
            require(set(exposure) == {"segment_id", "source_start_s", "source_end_s",
                                     "continues_in_tail_frame", "source_claim_ids"},
                    "reconstruction:exposure_fields")
            segment = segments.get(exposure["segment_id"])
            require(segment is not None and segment["slot_id"] == group["slot_id"],
                    "reconstruction:exposure_segment_or_slot_changed")
            start = number(exposure["source_start_s"], "reconstruction/exposure_start")
            end = number(exposure["source_end_s"], "reconstruction/exposure_end")
            require(segment["source_in_s"] <= start < end <= segment["source_out_s"],
                    "reconstruction:exposure_outside_slice")
            tail = exposure["continues_in_tail_frame"]
            require(type(tail) is bool and (not tail or end == segment["source_out_s"]),
                    "reconstruction:hold_cannot_expose_earlier_information")
            refs(exposure["source_claim_ids"], {c["claim_id"] for c in segment["source_claims"]},
                 "reconstruction/exposure_claims", nonempty=True)
        require(proposed_group_exposure(plan, group)+.001 >= group["min_readable_s"],
                "reconstruction:shared_information_exposure_below_original_minimum")
    transitions = rows(value["transition_checks"], "reconstruction/transitions", nonempty=False)
    require([(r.get("from_segment_id"), r.get("to_segment_id")) for r in transitions] ==
            [(a["segment_id"], b["segment_id"]) for a, b in zip(plan["segments"], plan["segments"][1:])],
            "reconstruction:adjacent_transitions_must_be_checked_once")
    for transition in transitions:
        require(transition.get("status") in {"planned", "unresolved"}, "reconstruction:transition_status")
        text(transition.get("relation"), "reconstruction/transition/relation")
    _block_scope_replay(plan, catalog, blocked_scopes)
    require(_substantively_changed(plan["segments"], parent["provenance"], plan["fps"]),
            "reconstruction:unchanged_parent_is_not_progress")
    if prior_assembly is not None:
        require(_substantively_changed(plan["segments"], prior_assembly["plan"]["segments"], plan["fps"]),
                "reconstruction:only_relabeling_failed_plan_is_not_progress")
    return value


def reconstruction_prompt(parent, outline, catalog, windows, reference, methods,
                          original_information_groups, source_facts, *, navigation=None,
                          prior_assembly=None, blocked_scopes=(), knowledge=""):
    """New facts-first creative decision task, never a third old format repair."""
    prior_plan = (prior_assembly["plan"] if prior_assembly is not None else
                  parent.get("render_input", {}).get("plan", {}))
    settings = {k: deepcopy(v) for k, v in prior_plan.items()
                if k not in {"segments", "slots", "editing_bindings", "focus_role_bindings"}}
    contract = {"status": "planned", "baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
        "plan": {**settings, "focus_role_bindings": [{"window_id": "已有window_id", "role_id": "该window确认角色",
            "identity_evidence": "已有窗口身份记忆，本项不要求每个微切证明整窗口"}],
            "slots": [{"slot_id": "原slot_id", "intended_takeaway": "原文逐字保留", "segment_ids": ["new_seg"]}],
            "segments": [{"segment_id": "new_seg", "slot_id": "原slot_id", "source_id": "已有source_id",
                "window_id": "已watch窗口", "source_in_s": "原片秒数number", "source_out_s": "原片秒数number",
                "speed": 1, "freeze_tail_s": 0, "role_ids": ["当前切片确实可见、重叠event有依据的角色"],
                "framing": "fit", "look": "none",
                "source_claims": [{"claim_id": "src_fact", "kind": "visual_state", "description": "一条实际源画面原子主张"}],
                "output_operation_claims": []}],
            "editing_bindings": [{"method_id": "已有method_id", "status": "planned", "segment_ids": ["new_seg"],
                "intended_relation": "观众将看到的关系", "operation": "本次操作", "verification": "在实际成片检查",
                "limitations": []}]},
        "essential_groups": [{"group_id": "original_information_groups原ID", "slot_id": "原slot_id",
            "information": "原文逐字保留", "min_readable_s": "原minimum number不得改变",
            "exposures": [{"segment_id": "new_seg", "source_start_s": "原片秒数number",
                "source_end_s": "原片秒数number", "continues_in_tail_frame": False, "source_claim_ids": ["src_fact"]}]}],
        "transition_checks": [{"from_segment_id": "前镜ID", "to_segment_id": "后镜ID",
            "relation": "相邻镜观众可见的信息承接", "status": "planned"}], "limitations": [], "reason": "依据事实重新剪辑的实质变化"}
    payload = {"task": PROTOCOL, "parent": {k: parent[k] for k in
        ("baseline_id", "sha256", "duration_s", "provenance")}, "original_outline": outline,
        "original_information_groups": original_information_groups,
        "independent_source_facts": source_facts, "watched_windows": windows,
        "sources": [{k: s[k] for k in ("source_id", "sha256", "duration_s")} for s in catalog["sources"]],
        "reference": reference, "reference_methods": methods, "parent_navigation_only": navigation,
        "prior_plan_executable_only": [{k: s[k] for k in ("source_id", "source_in_s", "source_out_s", "speed")}
            | {"freeze_tail_s": s.get("freeze_tail_s", 0)} for s in prior_plan.get("segments", [])],
        "blocked_observation_scopes": blocked_scopes, "response_contract": contract,
        "allowed_values": {"status": ["planned", "unavailable"], "source_claim_kind": sorted(SOURCE_KINDS),
            "output_operation_kind": ["speed", "tail_hold"], "transition_status": ["planned", "unresolved"]}}
    return (ROOT_OUTPUT_INSTRUCTION +
        "这是新的事实优先剪辑重建任务，不是旧失败回复的第三次格式修复，不猜测任何未知回复。"
        "从已有中性源观察、可用范围与原信息要求自主形成真正精炼的新EDL；原slot顺序和主旨原文保留。"
        "每镜role_ids自主选择当前微切可见角色，不复制父长片段演员全集；须一个usable_range兼容且与当前时间重叠event吻合。"
        "source_claims只写原片可见的原子动作、姿态、结果或文字，不能混入未来慢放、定格、添加字幕。"
        "source visual_state可表示可见姿态；visual_outcome涉及结果而非随意给动作换标签，因果未被看见须保留限制。"
        "任何非1 speed必须有output_operation_claims中kind=speed、expected_value=实际speed；"
        "任何非0尾停须kind=tail_hold、expected_value=freeze_tail_s，它们在真实输出核验。"
        "每个original_information_groups逐字保留一次、minimum原值不变；同一信息可跨多镜exposures共享，"
        "按实际输出时间并集计曝光，不要求每镜重复整个group minimum，也不重复计算同镜重叠证据。"
        "每exposure的source_claim_ids绑定本镜原子源主张；仅原片范围内正区间，尾停只增加真正持续到最后帧的信息。"
        "父导航和宽窗口描述不能证明精切动作完成或实际时长；最终切片会独立观察、比较与实际成片盲读。"
        "不得复用blocked整范围、拆分后联合覆盖全部、只微移一帧或换编码避开耗尽观察。"
        "必须相比父视频及上一失败组装改变真实切片、顺序、速度或停留；改ID/角色标签/主张文字不算新剪辑。"
        "最多32切片，speed在0.5到2之间，尾停不超过10秒；全部相邻镜按顺序transition_checks一次。"
        "无法保留原信息或证据不够时status=unavailable、plan=null、essential_groups/transition_checks=[]，说明limitations。"
        "仅返回根对象，所有数值为JSON number，ID从上下文取合法引用，枚举选一个值。\n" + knowledge + "\n" +
        json.dumps(payload, ensure_ascii=False, allow_nan=False))
