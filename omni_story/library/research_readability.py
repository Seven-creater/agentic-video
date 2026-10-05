"""Pure evidence timing proposals and source counterevidence, not quality verdicts."""
from __future__ import annotations

from copy import deepcopy
import json

from ..contract import ids, number, refs, require, rows, text
from .semantic_audit import CLAIM_STATUSES, SEMANTIC_PROTOCOL

POLICY = "evidence_timing_refinement_v1"
CORE_KINDS = {"visual_action", "visual_outcome", "identity"}
PURPOSES = {"setup", "action", "result", "reaction", "identity", "context"}


def refinement_supplement(knowledge, context):
    """Add generic exposure/transition obligations without supplying edit answers."""
    text(knowledge, "readability/knowledge")
    require(isinstance(context, dict), "readability/context:object_required")
    return "\n" + knowledge + "\n" + json.dumps({
        "policy": POLICY,
        "evidence_context": context,
        "instruction": "仍须返回完整精剪refinement及其完整最终EDL、decisions、obligation_coverage、"
            "draft_dispositions和duration；以下字段是附加要求。自主选择每段必要的可见信息，"
            "不要以整段时长代替关键画面的曝光时间。必要源区间必须位于最终选段之内，"
            "claim_ids只能引用该段visual_claims，并覆盖其中全部核心动作、结果和身份主张。"
            "每个必要源区间另估计自己的min_readable_s，逐项核对实际曝光，不能以长动作"
            "掩盖短到看不清的身份或结果画面。"
            "先确认关键瞬间存在，再决定删减、速度和尾帧保持。按区间并集计算核心曝光，"
            "重叠区间不能重复计时；只有核心信息延续到选段最后画面时才计入尾帧保持。"
            "min_readable_s是你根据实际信息、最终景别/构图、动作复杂度估计的最低阅读时间，"
            "必须大于零，不是统一秒数阈值或已测量的人类理解结果。每个相邻切点说明信息关系；"
            "缺少联系标unresolved，不能凭借同一人物、字幕或电影常识写成质量通过。"
            "所有检查都是方案，之后仍需独立源片事实与实际成片静音审核。",
        "additional_response_contract": {
            "timing_checks": [{"segment_id": "最终片段ID", "purpose": "setup/action/result/reaction/identity/context",
                "essential_source_intervals": [{"source_start_s": "选段内源时间",
                    "source_end_s": "选段内源时间", "visible_information": "这段实际须看见的关键信息",
                    "claim_ids": ["本段visual_claim ID"], "min_readable_s": "这个区间单独需要的正数输出秒数"}],
                "min_readable_s": "模型估计的正数输出秒数", "reason": "为何这些信息需要此曝光时间"}],
            "transition_checks": [{"from_segment_id": "相邻前段ID", "to_segment_id": "相邻后段ID",
                "relation": "实际画面应如何建立动作、结果或上下文联系",
                "status": "planned/unresolved", "reason": "已有联系或缺少的证据"}]},
        "evidence_limit": "Model timing and transition proposals are estimates, not observed semantic truth or a quality pass."
    }, ensure_ascii=False)


def _union_duration(intervals):
    start, end = sorted(intervals)[0]
    total = 0.0
    for left, right in sorted(intervals)[1:]:
        if left <= end:
            end = max(end, right)
        else:
            total += end - start
            start, end = left, right
    return total + end - start


def validate_timing(refinement):
    """Check model-estimated core exposure arithmetic and complete cut proposals."""
    require(isinstance(refinement, dict), "readability/refinement:object_required")
    plan = refinement.get("plan")
    require(isinstance(plan, dict), "readability/plan:object_required")
    segment_ids = ids(plan.get("segments"), "segment_id", "readability/segments")
    segments = {s["segment_id"]: s for s in plan["segments"]}
    checks = refinement.get("timing_checks")
    require(ids(checks, "segment_id", "readability/timing_checks") == segment_ids,
            "readability:timing_must_cover_final_segments")
    for check in checks:
        segment = segments[check["segment_id"]]
        require(check.get("purpose") in PURPOSES, "readability:unknown_purpose")
        text(check.get("reason"), "readability/timing_reason")
        minimum = number(check.get("min_readable_s"), "readability/min_readable_s")
        require(minimum > 0, "readability:min_readable_must_be_positive")
        source_in = number(segment.get("source_in_s"), "readability/source_in_s")
        source_out = number(segment.get("source_out_s"), "readability/source_out_s")
        require(source_in < source_out, "readability:invalid_selected_interval")
        speed = number(segment.get("speed", 1), "readability/speed", .5)
        hold = number(segment.get("freeze_tail_s", 0), "readability/freeze_tail_s")
        require(speed <= 2 and hold <= 10, "readability:invalid_transform")
        claims = segment.get("visual_claims", [])
        allowed = ids(claims, "claim_id", "readability/visual_claims", nonempty=False)
        for claim in claims:
            require(claim.get("kind") in CORE_KINDS, "readability:unknown_visual_claim_kind")
            text(claim.get("description"), "readability/visual_claim_description")
        intervals, covered = [], set()
        for interval in rows(check.get("essential_source_intervals"), "readability/essential_intervals"):
            require(isinstance(interval, dict), "readability/essential_interval:object_required")
            start = number(interval.get("source_start_s"), "readability/essential_start")
            end = number(interval.get("source_end_s"), "readability/essential_end")
            require(source_in <= start < end <= source_out, "readability:essential_interval_outside_selected_source")
            text(interval.get("visible_information"), "readability/visible_information")
            refs(interval.get("claim_ids"), allowed, "readability/essential_claim_ids")
            interval_minimum = number(interval.get("min_readable_s"), "readability/interval_min_readable_s")
            require(interval_minimum > 0, "readability:interval_min_readable_must_be_positive")
            interval_exposure = (end - start) / speed + (hold if end == source_out else 0)
            require(interval_exposure + 1e-9 >= interval_minimum,
                    "readability:essential_exposure_below_model_minimum:interval")
            covered.update(interval["claim_ids"])
            intervals.append((start, end))
        require(covered == allowed, "readability:core_claim_missing_timing")
        # A hold extends only the final image. Earlier information cannot gain
        # exposure merely because an unrelated later image is frozen.
        exposure = _union_duration(intervals) / speed
        if any(end == source_out for _, end in intervals):
            exposure += hold
        require(exposure + 1e-9 >= minimum, "readability:essential_exposure_below_model_minimum")
    expected = {(left["segment_id"], right["segment_id"])
                for left, right in zip(plan["segments"], plan["segments"][1:])}
    found = set()
    for check in rows(refinement.get("transition_checks"), "readability/transition_checks", nonempty=False):
        require(isinstance(check, dict), "readability/transition_check:object_required")
        pair = (check.get("from_segment_id"), check.get("to_segment_id"))
        require(all(isinstance(v, str) for v in pair) and pair in expected and pair not in found,
                "readability:unknown_or_duplicate_transition")
        found.add(pair)
        text(check.get("relation"), "readability/transition_relation")
        text(check.get("reason"), "readability/transition_reason")
        require(check.get("status") in {"planned", "unresolved"}, "readability:transition_is_not_quality_verdict")
    require(found == expected, "readability:transitions_must_cover_adjacent_segments")
    return refinement


def source_counterevidence(manifest):
    """Return exact per-source core claim blockers from the current manifest.

    Output/montage obligations have no per-source result. Caption wording and
    role-presence checks do not substitute for core visual claims. Nothing here
    upgrades unsupported, partial, unverifiable or absent checks to supported.
    """
    require(isinstance(manifest, dict), "readability/manifest:object_required")
    require(manifest.get("protocol") == SEMANTIC_PROTOCOL, "readability:semantic_protocol_required")
    claims = manifest.get("required_claims")
    ids(claims, "claim_id", "readability/required_claims", nonempty=False)
    by_id = {c["claim_id"]: c for c in claims}
    core = []
    for claim in claims:
        text(claim.get("kind"), "readability/claim_kind")
        text(claim.get("description"), "readability/claim_description")
        if claim["kind"] in CORE_KINDS:
            text(claim.get("segment_id"), "readability/core_segment_id")
            core.append(claim)
    records = manifest.get("segment_checks")
    ids(records, "segment_id", "readability/segment_checks", nonempty=False)
    checked = {}
    for record in records:
        checks = record.get("claim_checks")
        ids(checks, "claim_id", "readability/source_claim_checks", nonempty=False)
        for check in checks:
            cid = check["claim_id"]
            require(cid in by_id and by_id[cid].get("segment_id") == record["segment_id"],
                    "readability:claim_check_wrong_source_segment")
            require(cid not in checked, "readability:duplicate_source_claim_check")
            require(check.get("status") in CLAIM_STATUSES, "readability:unknown_source_claim_status")
            text(check.get("reason"), "readability/source_claim_reason")
            for limitation in rows(check.get("limitations"), "readability/source_claim_limitations", nonempty=False):
                text(limitation, "readability/source_claim_limitation")
            evidence = rows(check.get("evidence_ids"), "readability/source_evidence_ids", nonempty=False)
            require(all(isinstance(e, str) and e.strip() for e in evidence) and len(evidence) == len(set(evidence)),
                    "readability:invalid_source_evidence_ids")
            checked[cid] = check
    feedback = []
    for claim in core:
        check = checked.get(claim["claim_id"])
        if check and check["status"] == "supported":
            continue
        feedback.append({"segment_id": claim["segment_id"], "claim_id": claim["claim_id"],
            "kind": claim["kind"], "description": claim["description"],
            "status": check["status"] if check else "missing",
            "reason": check["reason"] if check else "Required core visual claim has no independent source check.",
            "limitations": deepcopy(check["limitations"]) if check else ["No support may be inferred from an absent check."],
            "evidence_ids": list(check["evidence_ids"]) if check else []})
    return feedback
