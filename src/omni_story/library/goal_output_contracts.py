"""Goal-only actual-output support after the existing strict semantic review.

The renderer's raw provenance has numeric indices. Callers must supply the
model-ID timing manifest from ``active_finecut.economy_manifest`` and attach the
validated plan's slot IDs. This module never guesses a segment ID from an index,
changes an observation, or treats model evidence as viewer truth.
"""
from ..contract import ids, number, refs, require, rows, text

POLICY = "goal_output_visible_support_v1"
VISUAL_KINDS = {"visual_action", "visual_outcome"}
REQUIRED_KINDS = VISUAL_KINDS | {"identity", "slot_takeaway"}

REVIEW_INSTRUCTION = """
本轮追加实际输出可见性核验。源片里存在的动作不等于裁切、变速和拼接后的成片里仍然可见。
对required_claims中visual_action、visual_outcome、identity和slot_takeaway的supported判定，
除保留原来源核查外，还须在blind_evidence_ids引用静音盲读的直接画面证据。
visual_outcome必须引用实际输出的visual_outcome；文字和inference不能代替画面动作、结果或身份。
局部片段主张的直接证据必须与该segment_id在actual_output_manifest中的输出区间相交。
段落主旨和草案表达义务可引用多个输出区间，限于其slot或segment_ids所绑定的实际片段。
盲读未观察到关键内容时，保留partial/unsupported/unverifiable，不凭源片或计划升级为supported。
不得新增或改写盲读证据ID、时间和事实来满足此约束；任何支持仍是模型判断，不是观众真值。
"""

ECONOMY_INSTRUCTION = """
检查实际成片每段内部的必要关键瞬间、理解所需前提和可见结果，给出输出秒数证据。
分别指出可删的等待、重复动作或表情、无新信息的过程，及仍需保留的动作阅读时间。
把整段标为necessary不能代替段内核查；不能因为段落相关就默认其中每一秒都有必要。
重复过程可删或按需压缩，关键瞬间可按可读性保留或强调，不要求每段采用变速技巧。
只依据当前视频、实际输出时间和独立静音盲读，不补入参考剧情、源电影常识或精剪理由。
若有可删区间或尚未核实的冗余，保留partial/fail及具体时间证据，不能只靠解释宣称精炼。
"""


def _scope(claim, timeline):
    """Return only the actual output ranges owned by this model claim."""
    segment_id = claim.get("segment_id")
    if segment_id is not None:
        require(segment_id in timeline, "goal_review:claim_segment_not_in_output")
        return [timeline[segment_id]]
    if "segment_ids" in claim:
        refs(claim["segment_ids"], set(timeline), "goal_review/claim_segments", nonempty=True)
        return [timeline[segment_id] for segment_id in claim["segment_ids"]]
    owner = claim.get("owner_id")
    text(owner, "goal_review/slot_owner")
    intervals = [row for row in timeline.values() if row.get("slot_id") == owner]
    require(bool(intervals), "goal_review:slot_provenance_binding_required")
    return intervals


def validate_goal_review(review, blind, required_claims, rendered):
    """Require supported visual/slot claims to cite direct output observations.

Run the original strict validator first. ``rendered`` is its actual-output
timing manifest (SHA, measured duration, segment IDs and source-bound timing),
with optional slot IDs. Non-supported verdicts retain their original meaning.
This additional constraint does not prove the semantics of a model's citation.
"""
    actual_sha = rendered.get("sha256")
    text(actual_sha, "goal_review/actual_video_sha256")
    require(review.get("video_sha256") == blind.get("video_sha256") == actual_sha,
            "goal_review:actual_video_binding_changed")
    duration = number(rendered.get("measured_duration_s"), "goal_review/output_duration", .001)
    provenance = rows(rendered.get("provenance"), "goal_review/provenance")
    ids(provenance, "segment_id", "goal_review/output_segments")
    timeline = {}
    for row in provenance:
        start = number(row.get("output_in_s"), "goal_review/segment_start")
        end = number(row.get("output_out_s"), "goal_review/segment_end")
        require(start < end <= duration + .001, "goal_review:output_segment_outside_video")
        timeline[row["segment_id"]] = row
    evidence = rows(blind.get("evidence"), "goal_review/blind_evidence")
    ids(evidence, "evidence_id", "goal_review/blind_ids")
    by_evidence = {row["evidence_id"]: row for row in evidence}
    checks = rows(review.get("fact_checks"), "goal_review/fact_checks")
    ids(checks, "claim_id", "goal_review/checked_ids")
    by_check = {row["claim_id"]: row for row in checks}
    ids(required_claims, "claim_id", "goal_review/required_ids", nonempty=False)
    for claim in required_claims:
        check = by_check.get(claim["claim_id"])
        require(check is not None, "goal_review:required_claim_missing")
        if claim.get("kind") not in REQUIRED_KINDS or check.get("status") != "supported":
            continue
        refs(check.get("blind_evidence_ids"), set(by_evidence), "goal_review/output_evidence", nonempty=True)
        intervals = _scope(claim, timeline)
        allowed = {"visual_outcome"} if claim["kind"] == "visual_outcome" else VISUAL_KINDS
        matched = False
        for evidence_id in check["blind_evidence_ids"]:
            row = by_evidence[evidence_id]
            if row.get("kind") not in allowed:
                continue
            start = number(row.get("start_s"), "goal_review/evidence_start")
            end = number(row.get("end_s"), "goal_review/evidence_end")
            require(start < end <= duration + .001, "goal_review:direct_evidence_outside_video")
            if any(start < interval["output_out_s"] and interval["output_in_s"] < end
                   for interval in intervals):
                matched = True
        require(matched, "goal_review:supported_claim_requires_direct_visible_output")
    return review
