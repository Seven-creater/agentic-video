"""Timing arithmetic and source feedback contracts; no model or media calls."""
from copy import deepcopy
import math

import pytest

from omni_story.library import research_readability as readability
from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL


def refinement():
    return {"plan": {"segments": [
        {"segment_id": "seg_a", "source_in_s": 100, "source_out_s": 104, "speed": 1,
            "visual_claims": [{"claim_id": "action_a", "kind": "visual_action", "description": "Visible action."},
                {"claim_id": "result_a", "kind": "visual_outcome", "description": "Visible outcome."}]},
        {"segment_id": "seg_b", "source_in_s": 200, "source_out_s": 201, "speed": 1,
            "visual_claims": [{"claim_id": "identity_b", "kind": "identity", "description": "Visible participant."}]}]},
        "timing_checks": [
            {"segment_id": "seg_a", "purpose": "action", "min_readable_s": 2,
                "reason": "Both action and result require time.", "essential_source_intervals": [
                    {"source_start_s": 100, "source_end_s": 101, "visible_information": "Action moment.", "claim_ids": ["action_a"], "min_readable_s": 1},
                    {"source_start_s": 103, "source_end_s": 104, "visible_information": "Result moment.", "claim_ids": ["result_a"], "min_readable_s": 1}]},
            {"segment_id": "seg_b", "purpose": "identity", "min_readable_s": 1,
                "reason": "Read the participant.", "essential_source_intervals": [
                    {"source_start_s": 200, "source_end_s": 201, "visible_information": "Visible participant.", "claim_ids": ["identity_b"], "min_readable_s": 1}]}],
        "transition_checks": [{"from_segment_id": "seg_a", "to_segment_id": "seg_b", "relation": "A result followed by a reaction.",
            "status": "planned", "reason": "Check subject continuity in actual output."}]}


def manifest():
    kinds = {"action_a": "visual_action", "result_a": "visual_outcome", "identity_b": "identity",
             "caption_a": "caption", "role_a": "role_presence"}
    claims = [{"claim_id": cid, "kind": kind, "description": "Recorded claim " + cid,
        "segment_id": "seg_b" if cid == "identity_b" else "seg_a"} for cid, kind in kinds.items()]
    claims.append({"claim_id": "slot_c", "kind": "slot_takeaway", "description": "Output assembly target.", "owner_id": "slot_1"})
    records = [{"segment_id": sid, "protocol": SEMANTIC_PROTOCOL, "claim_checks": [
        {"claim_id": c["claim_id"], "status": "supported", "reason": "Actual short-source cue.",
         "evidence_ids": ["ev_" + c["claim_id"]], "limitations": []}
        for c in claims if c.get("segment_id") == sid]} for sid in ("seg_a", "seg_b")]
    return {"protocol": SEMANTIC_PROTOCOL, "required_claims": claims, "segment_checks": records}


def test_valid_timing_returns_original_without_inventing_quality_or_mutation():
    value = refinement()
    before = deepcopy(value)
    assert readability.validate_timing(value) is value
    assert value == before
    assert "gate_passed" not in value and "quality_status" not in value


def test_overlapping_claim_intervals_are_union_not_sum():
    value = refinement()
    first = value["timing_checks"][0]
    first["essential_source_intervals"][0].update(source_start_s=100, source_end_s=102)
    first["essential_source_intervals"][1].update(source_start_s=101, source_end_s=103)
    first["min_readable_s"] = 3.5
    with pytest.raises(ValueError, match="essential_exposure_below_model_minimum"):
        readability.validate_timing(value)
    first["min_readable_s"] = 3
    assert readability.validate_timing(value) is value


def test_only_essential_seconds_count_not_the_full_selected_clip():
    value = refinement()
    value["timing_checks"][0]["min_readable_s"] = 3
    # Four selected seconds contain only two essential seconds.
    with pytest.raises(ValueError, match="essential_exposure_below_model_minimum"):
        readability.validate_timing(value)


@pytest.mark.parametrize("speed,minimum,accepted", [(2, 1, True), (2, 1.01, False), (.5, 4, True), (.5, 4.01, False)])
def test_final_playback_speed_changes_core_exposure(speed, minimum, accepted):
    value = refinement()
    value["plan"]["segments"][0]["speed"] = speed
    value["timing_checks"][0]["min_readable_s"] = minimum
    for interval in value["timing_checks"][0]["essential_source_intervals"]:
        interval['min_readable_s'] = minimum / 2
    if accepted:
        readability.validate_timing(value)
    else:
        with pytest.raises(ValueError, match="essential_exposure_below_model_minimum"):
            readability.validate_timing(value)


def test_tail_hold_counts_once_only_when_core_reaches_the_tail():
    value = refinement()
    value["plan"]["segments"][0]["freeze_tail_s"] = 2
    value["timing_checks"][0]["min_readable_s"] = 4
    readability.validate_timing(value)
    value["timing_checks"][0]["essential_source_intervals"][1].update(source_start_s=101, source_end_s=102)
    with pytest.raises(ValueError, match="essential_exposure_below_model_minimum"):
        readability.validate_timing(value)


def test_overlapping_tail_claims_do_not_multiply_hold_time():
    value = refinement()
    value["plan"]["segments"][0]["freeze_tail_s"] = 2
    value["timing_checks"][0]["essential_source_intervals"][0].update(source_start_s=102, source_end_s=104)
    value["timing_checks"][0]["min_readable_s"] = 4.1
    with pytest.raises(ValueError, match="essential_exposure_below_model_minimum"):
        readability.validate_timing(value)


def test_long_action_cannot_hide_unreadably_short_identity_interval():
    value = refinement()
    value['plan']['segments'][0]['visual_claims'][1]['kind'] = 'identity'
    first = value['timing_checks'][0]
    first['min_readable_s'] = 3
    action, identity = first['essential_source_intervals']
    action.update(source_end_s=103.9, min_readable_s=2)
    identity.update(source_start_s=103.9, min_readable_s=.5)
    # Total union exposure is four seconds but identity is visible for .1s.
    with pytest.raises(ValueError, match='essential_exposure_below_model_minimum:interval'):
        readability.validate_timing(value)
    value['plan']['segments'][0]['freeze_tail_s'] = .5
    readability.validate_timing(value)


def test_hold_cannot_extend_a_short_earlier_interval():
    value = refinement()
    value['plan']['segments'][0]['freeze_tail_s'] = 3
    first = value['timing_checks'][0]
    first['min_readable_s'] = 2
    action, outcome = first['essential_source_intervals']
    action.update(source_end_s=100.1, min_readable_s=.5)
    outcome.update(source_start_s=101, min_readable_s=2)
    # The hold belongs to the result image, not the earlier action.
    with pytest.raises(ValueError, match='essential_exposure_below_model_minimum:interval'):
        readability.validate_timing(value)


@pytest.mark.parametrize('minimum', [None, 0, -1, math.nan, math.inf, True])
def test_each_essential_interval_needs_its_own_positive_finite_estimate(minimum):
    value = refinement()
    value['timing_checks'][0]['essential_source_intervals'][0]['min_readable_s'] = minimum
    with pytest.raises(ValueError, match='interval_min_readable'):
        readability.validate_timing(value)


@pytest.mark.parametrize("minimum", [0, -1, math.nan, math.inf, True, "1"])
def test_minimum_is_positive_finite_model_estimate(minimum):
    value = refinement()
    value["timing_checks"][0]["min_readable_s"] = minimum
    with pytest.raises(ValueError, match="min_readable"):
        readability.validate_timing(value)


def test_no_universal_minimum_is_imposed():
    value = refinement()
    value["timing_checks"][0]["min_readable_s"] = .00001
    readability.validate_timing(value)


@pytest.mark.parametrize("start,end", [(99, 101), (103, 105), (101, 101), (102, 101)])
def test_essential_information_must_exist_inside_selected_source(start, end):
    value = refinement()
    value["timing_checks"][0]["essential_source_intervals"][0].update(source_start_s=start, source_end_s=end)
    with pytest.raises(ValueError, match="essential_interval_outside_selected_source"):
        readability.validate_timing(value)


@pytest.mark.parametrize("claim_ids", [[], ["identity_b"], ["action_a", "action_a"]])
def test_all_core_claims_need_own_segment_timing_without_cross_segment_refs(claim_ids):
    value = refinement()
    value["timing_checks"][0]["essential_source_intervals"][0]["claim_ids"] = claim_ids
    with pytest.raises(ValueError, match="core_claim_missing_timing|unknown_or_duplicate_ref"):
        readability.validate_timing(value)


def test_context_without_explicit_core_claims_still_needs_visible_interval():
    value = refinement()
    value["plan"]["segments"][1]["visual_claims"] = []
    value["timing_checks"][1]["purpose"] = "context"
    value["timing_checks"][1]["essential_source_intervals"][0]["claim_ids"] = []
    readability.validate_timing(value)
    value["timing_checks"][1]["essential_source_intervals"] = []
    with pytest.raises(ValueError, match="essential_intervals:list_required"):
        readability.validate_timing(value)


def test_timing_covers_every_final_segment_exactly_once():
    value = refinement()
    value["timing_checks"].pop()
    with pytest.raises(ValueError, match="timing_must_cover_final_segments"):
        readability.validate_timing(value)
    value = refinement()
    value["timing_checks"].append(deepcopy(value["timing_checks"][0]))
    with pytest.raises(ValueError, match="duplicate_id"):
        readability.validate_timing(value)


@pytest.mark.parametrize("mutation,error", [
    (lambda v: v.update(transition_checks=[]), "transitions_must_cover"),
    (lambda v: v["transition_checks"].append(deepcopy(v["transition_checks"][0])), "duplicate_transition"),
    (lambda v: v["transition_checks"][0].update(from_segment_id="seg_b", to_segment_id="seg_a"), "unknown_or_duplicate_transition"),
    (lambda v: v["transition_checks"][0].update(status="pass"), "transition_is_not_quality_verdict")])
def test_transitions_cover_actual_neighbors_only_as_proposals(mutation, error):
    value = refinement()
    mutation(value)
    with pytest.raises(ValueError, match=error):
        readability.validate_timing(value)


def test_unresolved_transition_and_single_segment_are_valid_proposals():
    value = refinement()
    value["transition_checks"][0]["status"] = "unresolved"
    readability.validate_timing(value)
    value["plan"]["segments"].pop()
    value["timing_checks"].pop()
    value["transition_checks"] = []
    readability.validate_timing(value)


def test_source_feedback_retains_exact_core_ids_and_original_non_supported_status():
    value = manifest()
    value["segment_checks"][0]["claim_checks"][0].update(status="unsupported", limitations=["No action observed."])
    value["segment_checks"][0]["claim_checks"][1].update(status="partial", limitations=["Outcome incomplete."])
    value["segment_checks"][1]["claim_checks"][0].update(status="unverifiable", limitations=["Participant unresolved."])
    before = deepcopy(value)
    result = readability.source_counterevidence(value)
    assert [(r["claim_id"], r["segment_id"], r["status"]) for r in result] == [
        ("action_a", "seg_a", "unsupported"), ("result_a", "seg_a", "partial"), ("identity_b", "seg_b", "unverifiable")]
    assert value == before
    result[0]["limitations"].append("new")
    assert value == before


def test_missing_core_checks_and_entire_missing_segment_are_feedback_not_pass():
    value = manifest()
    value["segment_checks"][0]["claim_checks"].pop(0)
    value["segment_checks"].pop()
    result = readability.source_counterevidence(value)
    assert [(r["claim_id"], r["status"], r["evidence_ids"]) for r in result] == [
        ("action_a", "missing", []), ("identity_b", "missing", [])]
    assert all(r["reason"] and r["limitations"] for r in result)


def test_no_source_check_records_cannot_establish_any_core_claim():
    value = manifest()
    value["segment_checks"] = []
    result = readability.source_counterevidence(value)
    assert {(r["segment_id"], r["claim_id"], r["status"]) for r in result} == {
        ("seg_a", "action_a", "missing"), ("seg_a", "result_a", "missing"),
        ("seg_b", "identity_b", "missing")}


def test_caption_wording_and_role_presence_failure_do_not_override_supported_visual_content():
    value = manifest()
    for check in value["segment_checks"][0]["claim_checks"]:
        if check["claim_id"] in {"caption_a", "role_a"}:
            check.update(status="unsupported", evidence_ids=[], limitations=["Literal wording differs."])
    assert readability.source_counterevidence(value) == []
    # A root summary must not upgrade the actual nested core check.
    value["claim_checks"] = [{"claim_id": "action_a", "status": "supported"}]
    value["segment_checks"][0]["claim_checks"][0].update(status="unsupported", limitations=["Action absent."])
    assert readability.source_counterevidence(value)[0]["status"] == "unsupported"


@pytest.mark.parametrize("mutation,error", [
    (lambda v: v.update(protocol="legacy"), "semantic_protocol_required"),
    (lambda v: v["segment_checks"][0]["claim_checks"][0].update(claim_id="unknown"), "wrong_source_segment"),
    (lambda v: v["segment_checks"][1]["claim_checks"][0].update(claim_id="action_a"), "wrong_source_segment"),
    (lambda v: v["segment_checks"][0]["claim_checks"][0].update(status="pass"), "unknown_source_claim_status"),
    (lambda v: v["segment_checks"][0]["claim_checks"].append(deepcopy(v["segment_checks"][0]["claim_checks"][0])), "duplicate_id")])
def test_source_feedback_rejects_ambiguous_or_foreign_check_bindings(mutation, error):
    value = manifest()
    mutation(value)
    with pytest.raises(ValueError, match=error):
        readability.source_counterevidence(value)


def test_refinement_supplement_has_contract_and_no_hand_picked_film_answers():
    context = {"final_framing_must_be_observed": True}
    before = deepcopy(context)
    prompt = readability.refinement_supplement("Generic evidence timing knowledge.", context)
    assert readability.POLICY in prompt and "timing_checks" in prompt and "transition_checks" in prompt
    assert "min_readable_s" in prompt and "essential_source_intervals" in prompt
    assert "quality pass" in prompt and "planned/unresolved" in prompt
    assert "功夫熊猫" not in prompt and "5–17" not in prompt and "4714" not in prompt
    assert context == before
