"""Shared content/editing handoff. Checks bookkeeping, never invents editing evidence."""
from copy import deepcopy

from . import contract


def validate_editing(value, sections, duration, has_audio):
    contract.require(isinstance(value, dict), "reference_editing_object")
    methods = contract.rows(value.get("methods"), "editing/methods", nonempty=False)
    seen = set()
    for row in methods:
        contract.require(isinstance(row, dict), "reference_method_object")
        mid = row.get("method_id")
        contract.text(mid, "method_id")
        contract.require(mid not in seen, "duplicate_reference_method")
        seen.add(mid)
        contract.refs(row.get("section_ids"), sections, "method_sections", nonempty=True)
        a = contract.number(row.get("start_s"), "method_start")
        b = contract.number(row.get("end_s"), "method_end")
        contract.require(a < b <= duration, "reference_method_interval")
        contract.require(row.get("status") in {"observed", "hypothesis", "unknown"}, "method_status")
        for key in ("observation", "purpose_hypothesis", "adaptation_goal"):
            contract.text(row.get(key), key)
    contract.rows(value.get("uncertainties"), "editing/uncertainties", nonempty=False)
    region = value.get("music_region")
    if region is not None:
        contract.require(isinstance(region, dict) and has_audio, "reference_music_without_audio")
        a = contract.number(region.get("start_s"), "music_start")
        b = contract.number(region.get("end_s"), "music_end")
        contract.require(a < b <= duration, "reference_music_interval")
        contract.text(region.get("basis"), "music_basis")
    questions = contract.rows(value.get("inspection_requests", []), "inspection_requests", nonempty=False)
    contract.require(len(questions) <= 2, "reference_inspection_budget")
    question_ids = set()
    requests = set()
    for row in questions:
        contract.require(isinstance(row, dict), "inspection_question_object")
        qid = row.get("question_id")
        contract.text(qid, "question_id")
        contract.require(qid not in question_ids, "duplicate_inspection_question")
        question_ids.add(qid)
        a = contract.number(row.get("start_s"), "inspection_start")
        b = contract.number(row.get("end_s"), "inspection_end")
        contract.require(a < b <= duration, "inspection_interval")
        for key in ("question", "reason"):
            contract.text(row.get(key), key)
        request = (a, b, row["question"])
        contract.require(request not in requests and b-a < duration, "duplicate_or_full_reference_inspection")
        requests.add(request)


def build_transfer(reading, duration, video_sha, reading_sha):
    """Preserve uncertainty and parent identity; timestamps are model estimates, not detected cuts."""
    contract.require(reading.get("schema_version") == "autonomous_reference_reading_v2", "joint_reading_required")
    return {"schema_version": "reference_transfer_v2", "reference_sha256": video_sha,
        "reading_sha256": reading_sha, "reference_duration_s": duration,
        "status": "model_observed_candidate", "timestamp_basis": "model_estimates_not_cut_ground_truth",
        "audience_takeaway": reading["audience_takeaway"],
        "ending_effect": reading["ending_effect"],
        "sections": [{"section_id": s["section_id"],
            "relative_start": s["start_s"] / duration, "relative_end": s["end_s"] / duration,
            "information_task": s["new_information"], "editing_observation": s["editing_observation"]}
            for s in reading["sections"]],
        "editing": deepcopy(reading["editing"]), "uncertainties": deepcopy(reading["uncertainties"])}


def validate_mapping(rows, transfer, segment_count):
    """Validate declared mappings. An omitted method remains unknown, not a render blocker."""
    expected = {m["method_id"] for m in transfer["editing"]["methods"]}
    rows = [] if rows is None else rows
    contract.rows(rows, "style_mapping", nonempty=False)
    seen = set()
    for row in rows:
        contract.require(isinstance(row, dict), "style_mapping_object")
        mid = row.get("method_id")
        contract.require(mid in expected and mid not in seen, "style_mapping_unknown_or_duplicate")
        seen.add(mid)
        contract.require(row.get("status") in {"applied", "adapted", "unavailable", "uncertain"}, "style_mapping_status")
        indices = contract.rows(row.get("segment_indices"), "segment_indices", nonempty=False)
        contract.require(all(type(i) is int and 0 <= i < segment_count for i in indices)
                         and len(indices) == len(set(indices)), "style_mapping_indices")
        contract.require(row["status"] not in {"applied", "adapted"} or bool(indices), "style_mapping_empty_application")
        contract.text(row.get("explanation"), "style_mapping_explanation")


def validate_coverage(plan, transfer):
    expected = {m["method_id"] for m in transfer["editing"]["methods"]}
    materials = {m["material_id"] for m in plan["materials"]}
    seen = set()
    for row in contract.rows(plan.get("editing_intentions"), "editing_intentions", nonempty=False):
        contract.require(isinstance(row, dict), "editing_intention_object")
        mid = row.get("method_id")
        contract.require(mid in expected and mid not in seen, "editing_intention_unknown_or_duplicate")
        seen.add(mid)
        contract.refs(row.get("material_ids"), materials, "editing_intention_materials")
        contract.text(row.get("coverage_intent"), "coverage_intent")
        contract.rows(row.get("limitations"), "coverage_limitations", nonempty=False)
    contract.require(seen == expected, "editing_intention_coverage")


def validate_style_review(rows, transfer, duration):
    expected = {m["method_id"] for m in transfer["editing"]["methods"]}
    rows = [] if rows is None else rows
    contract.rows(rows, "style_review", nonempty=False)
    seen = set()
    for row in rows:
        contract.require(isinstance(row, dict), "style_review_object")
        mid = row.get("method_id")
        contract.require(mid in expected and mid not in seen, "style_review_unknown_or_duplicate")
        seen.add(mid)
        contract.require(row.get("status") in {"visible", "applied", "adapted", "not_visible", "cannot_verify"}, "style_review_status")
        contract.text(row.get("evidence"), "style_review_evidence")
        a, b = row.get("start_s"), row.get("end_s")
        if row["status"] in {"visible", "applied", "adapted"}:
            a = contract.number(a, "style_review_start")
            b = contract.number(b, "style_review_end")
            contract.require(a < b <= duration, "style_review_interval")
        else:
            contract.require(a is None and b is None, "unverified_style_has_timestamp")
