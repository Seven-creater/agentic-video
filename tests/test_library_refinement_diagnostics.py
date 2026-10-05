"""Aggregate feedback remains read-only and never launders malformed replies."""
from copy import deepcopy
import math

import pytest

from omni_story.library import active_finecut, refinement_diagnostics, research_readability


def example():
    segments = [
        {"segment_id": "a", "slot_id": "opening", "source_id": "source", "window_id": "w",
         "source_in_s": 100, "source_out_s": 104, "speed": 1, "look": "none", "framing": "fit",
         "visual_claims": [{"claim_id": "act", "kind": "visual_action", "description": "Action."},
                           {"claim_id": "out", "kind": "visual_outcome", "description": "Result."}]},
        {"segment_id": "b", "slot_id": "ending", "source_id": "source", "window_id": "w",
         "source_in_s": 200, "source_out_s": 201, "speed": 1, "look": "none", "framing": "fit",
         "visual_claims": [{"claim_id": "who", "kind": "identity", "description": "Participant."}]},
    ]
    plan = {"segments": segments, "audio_mode": "silent", "editing_bindings": [{"method_id": "method"}],
            "slots": [{"slot_id": "opening", "segment_ids": ["a"], "intended_takeaway": "An action."},
                      {"slot_id": "ending", "segment_ids": ["b"], "intended_takeaway": "A response."}]}
    value = {"plan": plan,
             "decisions": [{"segment_id": s["segment_id"], "origin": "general_optimization", "reference_method_ids": [],
                            "new_information": "Information.", "in_out_reason": "Boundary.",
                            "speed_reason": "Speed.", "hold_reason": "No hold."} for s in segments],
             "obligation_coverage": [{"slot_id": s["slot_id"], "segment_ids": s["segment_ids"],
                                      "status": "preserved", "reason": "Retained information."} for s in plan["slots"]],
             "draft_dispositions": [{"segment_id": s["segment_id"], "decision": "retained",
                                     "replacement_segment_ids": [], "reason": "Same source."} for s in segments],
             "duration": {"target_s": 5, "total_s": 5, "over_target_reason": ""},
             "timing_checks": [
                 {"segment_id": "a", "purpose": "action", "min_readable_s": 2, "reason": "Read both events.",
                  "essential_source_intervals": [
                      {"source_start_s": 100, "source_end_s": 101, "claim_ids": ["act"],
                       "visible_information": "Action.", "min_readable_s": 1},
                      {"source_start_s": 103, "source_end_s": 104, "claim_ids": ["out"],
                       "visible_information": "Result.", "min_readable_s": 1}]},
                 {"segment_id": "b", "purpose": "identity", "min_readable_s": 1, "reason": "Read person.",
                  "essential_source_intervals": [
                      {"source_start_s": 200, "source_end_s": 201, "claim_ids": ["who"],
                       "visible_information": "Participant.", "min_readable_s": 1}]}],
             "transition_checks": [{"from_segment_id": "a", "to_segment_id": "b", "status": "planned",
                                    "relation": "Action to response.", "reason": "Check actual output."}]}
    return value, deepcopy(plan)


def codes(value, draft):
    return {(e["path"], e["code"]) for e in refinement_diagnostics.diagnostics(value, draft)}


def test_valid_proposal_has_no_errors_but_does_not_change_or_pass_anything():
    value, draft = example()
    before = deepcopy((value, draft))
    assert refinement_diagnostics.diagnostics(value, draft) == []
    active_finecut.validate_refinement(value, draft, 8)
    research_readability.validate_timing(value)
    assert (value, draft) == before
    assert "quality_status" not in value and "gate_passed" not in value


@pytest.mark.parametrize("alias", ["draft", "original_draft"])
def test_wrong_envelope_enum_and_duration_are_reported_together_without_normalizing(alias):
    value, original = example()
    wrong_plan = value.pop("plan")
    for key in ("decisions", "obligation_coverage", "draft_dispositions", "duration"):
        wrong_plan[key] = value.pop(key)
    value[alias] = wrong_plan
    value["timing_checks"][0]["purpose"] = "action/result"
    wrong_plan["duration"]["total_s"] = 7
    before = deepcopy((value, original))
    errors = refinement_diagnostics.diagnostics(value, original)
    assert ("$.plan", "wrong_location") in {(e["path"], e["code"]) for e in errors}
    assert any(e["code"] == "enum" and e["path"].endswith(".purpose") for e in errors)
    assert any(e["code"] == "duration_math" and e["expected"] == 5 and e["actual"] == 7 for e in errors)
    assert all(set(e) == {"path", "code", "expected", "actual"} for e in errors)
    assert (value, original) == before and "plan" not in value
    with pytest.raises(ValueError, match="plan_required"):
        active_finecut.validate_refinement(value, original, 8)


@pytest.mark.parametrize("value", [None, [], "reply", {}, {"plan": {}},
    {"plan": {"segments": [None, {}, {"segment_id": []}], "slots": "bad"}},
    {"plan": {"segments": [{"segment_id": "x", "visual_claims": [{"claim_id": []}]}], "slots": []},
     "timing_checks": [{"segment_id": "x", "essential_source_intervals": [None, {}]}]},
    {"transition_checks": [{"from_segment_id": {}, "to_segment_id": []}]}])
def test_missing_unknown_or_malformed_fields_do_not_crash_or_supply_defaults(value):
    before = deepcopy(value)
    errors = refinement_diagnostics.diagnostics(value, {})
    assert errors and all(set(e) == {"path", "code", "expected", "actual"} for e in errors)
    assert value == before


def test_every_root_field_is_required_at_its_actual_root_location():
    value, draft = example()
    errors = refinement_diagnostics.diagnostics({}, draft)
    assert {e["path"] for e in errors if e["code"] == "missing"} == {"$." + k for k in value}
    value["plan"]["segments"][0]["speed"] = 0
    errors = refinement_diagnostics.diagnostics(value, draft)
    assert any(e["path"] == "$.duration.total_s" and e["code"] == "blocked" for e in errors)
    assert any(e["path"].endswith(".exposure") and e["code"] == "blocked" for e in errors)


def test_all_local_enum_families_reject_slash_strings_and_quality_status():
    value, draft = example()
    value["plan"]["audio_mode"] = "reference/source"
    value["plan"]["segments"][0].update(look="none/grayscale", framing="fit/crop")
    value["plan"]["segments"][0]["visual_claims"][0]["kind"] = "visual_action/identity"
    value["decisions"][0]["origin"] = "general_optimization/reference_transfer"
    value["obligation_coverage"][0]["status"] = "preserved/unresolved"
    value["draft_dispositions"][0]["decision"] = "retained/replaced"
    value["timing_checks"][0]["purpose"] = "action/result"
    value["transition_checks"][0]["status"] = "pass"
    assert len([e for e in refinement_diagnostics.diagnostics(value, draft) if e["code"] == "enum"]) == 9


def test_ids_coverage_obligations_and_dispositions_are_independent():
    value, draft = example()
    value["decisions"].pop()
    value["obligation_coverage"].pop()
    value["draft_dispositions"].pop()
    value["timing_checks"].append(deepcopy(value["timing_checks"][0]))
    value["plan"]["slots"][0]["intended_takeaway"] = "Rewritten answer."
    result = codes(value, draft)
    assert {(p, "coverage") for p in ("$.decisions", "$.obligation_coverage", "$.draft_dispositions")} <= result
    assert ("$.timing_checks[2].segment_id", "duplicate_id") in result
    assert ("$.plan.slots[0].intended_takeaway", "original_obligation_changed") in result


def test_removed_retained_and_method_refs_keep_actual_source_and_id_constraints():
    value, draft = example()
    value["draft_dispositions"][0]["decision"] = "removed"
    value["plan"]["segments"][1]["source_in_s"] = 200.1
    value["decisions"][0]["reference_method_ids"] = ["method", "foreign"]
    result = codes(value, draft)
    assert ("$.draft_dispositions[0]", "removed_still_selected") in result
    assert ("$.draft_dispositions[1]", "retained_source_changed") in result
    assert ("$.decisions[0].reference_method_ids", "refs") in result
    assert ("$.decisions[0].reference_method_ids", "optimization_method_refs") in result


def test_slot_membership_matches_the_segments_own_slots():
    value, draft = example()
    value["plan"]["slots"][0]["segment_ids"] = ["b"]
    assert ("$.plan.slots[0].segment_ids", "slot_assignment") in codes(value, draft)


def test_timing_claim_refs_are_local_complete_and_not_duplicate():
    value, draft = example()
    value["timing_checks"][0]["essential_source_intervals"][0]["claim_ids"] = ["who", "who"]
    result = codes(value, draft)
    assert ("$.timing_checks[0].essential_source_intervals[0].claim_ids", "refs") in result
    assert ("$.timing_checks[0].claim_ids", "coverage") in result


def test_union_does_not_double_count_overlap_or_subtract_contained_intervals():
    value, draft = example()
    intervals = value["timing_checks"][0]["essential_source_intervals"]
    intervals[0].update(source_end_s=103)
    intervals[1].update(source_start_s=101, source_end_s=102)
    value["timing_checks"][0]["min_readable_s"] = 3
    assert refinement_diagnostics.diagnostics(value, draft) == []
    value["timing_checks"][0]["min_readable_s"] = 3.1
    assert ("$.timing_checks[0]", "union_exposure") in codes(value, draft)


@pytest.mark.parametrize("speed,minimum,expect_error", [(2, 1, False), (2, 1.1, True), (.5, 4, False)])
def test_retiming_changes_interval_and_union_exposure(speed, minimum, expect_error):
    value, draft = example()
    value["plan"]["segments"][0]["speed"] = speed
    value["duration"]["total_s"] = 4 / speed + 1
    value["duration"]["over_target_reason"] = "Visible information requires it."
    value["timing_checks"][0]["min_readable_s"] = minimum
    for interval in value["timing_checks"][0]["essential_source_intervals"]:
        interval["min_readable_s"] = minimum / 2
    errors = refinement_diagnostics.diagnostics(value, draft)
    assert bool([e for e in errors if e["code"] in {"union_exposure", "interval_exposure"}]) == expect_error


def test_long_action_cannot_mask_brief_identity_and_only_tail_interval_gets_hold():
    value, draft = example()
    intervals = value["timing_checks"][0]["essential_source_intervals"]
    intervals[0].update(source_end_s=103.9, min_readable_s=2)
    intervals[1].update(source_start_s=103.9, min_readable_s=.5)
    value["plan"]["segments"][0]["visual_claims"][1]["kind"] = "identity"
    value["timing_checks"][0]["min_readable_s"] = 3
    assert ("$.timing_checks[0].essential_source_intervals[1]", "interval_exposure") in codes(value, draft)
    value["plan"]["segments"][0]["freeze_tail_s"] = .5
    value["duration"].update(total_s=5.5, over_target_reason="Read identity.")
    assert refinement_diagnostics.diagnostics(value, draft) == []
    intervals[0].update(source_end_s=100.1, min_readable_s=.5)
    assert ("$.timing_checks[0].essential_source_intervals[0]", "interval_exposure") in codes(value, draft)


@pytest.mark.parametrize("minimum", [None, 0, -1, True, "1", math.nan, math.inf])
def test_each_interval_and_segment_needs_positive_finite_numeric_minimum(minimum):
    value, draft = example()
    value["timing_checks"][0]["min_readable_s"] = minimum
    value["timing_checks"][0]["essential_source_intervals"][0]["min_readable_s"] = minimum
    result = codes(value, draft)
    assert ("$.timing_checks[0].min_readable_s", "number") in result
    assert ("$.timing_checks[0].essential_source_intervals[0].min_readable_s", "number") in result


def test_outside_interval_blocks_union_but_independent_duration_still_reports():
    value, draft = example()
    value["timing_checks"][0]["essential_source_intervals"][0]["source_start_s"] = 99
    value["duration"]["total_s"] = 8
    result = codes(value, draft)
    assert ("$.timing_checks[0].essential_source_intervals[0]", "interval_outside_source") in result
    assert ("$.duration.total_s", "duration_math") in result
    assert ("$.timing_checks[0]", "union_exposure") not in result


def test_transitions_cover_every_actual_neighbor_once_and_are_proposals():
    value, draft = example()
    value["transition_checks"] += [deepcopy(value["transition_checks"][0]),
                                  {"from_segment_id": "b", "to_segment_id": "a", "status": "pass"}]
    result = codes(value, draft)
    assert ("$.transition_checks[1]", "adjacency") in result
    assert ("$.transition_checks[2]", "adjacency") in result
    assert ("$.transition_checks[2].status", "enum") in result
    value["transition_checks"] = []
    assert ("$.transition_checks", "coverage") in codes(value, draft)


def test_diagnostic_records_do_not_alias_mutable_original_payloads():
    value, draft = example()
    value["decisions"][0]["reference_method_ids"] = ["foreign"]
    errors = refinement_diagnostics.diagnostics(value, draft)
    observed = next(e for e in errors if e["path"] == "$.decisions[0].reference_method_ids" and e["code"] == "refs")
    observed["actual"].append("changed")
    assert value["decisions"][0]["reference_method_ids"] == ["foreign"]


def test_duplicate_or_malformed_id_does_not_hide_its_independent_enum_error():
    value, draft = example()
    duplicate = deepcopy(value["timing_checks"][0])
    duplicate["purpose"] = "action/result"
    value["timing_checks"].append(duplicate)
    value["timing_checks"].append({"segment_id": [], "purpose": "pass"})
    result = codes(value, draft)
    assert ("$.timing_checks[2].segment_id", "duplicate_id") in result
    assert ("$.timing_checks[2].purpose", "enum") in result
    assert ("$.timing_checks[3].segment_id", "id") in result
    assert ("$.timing_checks[3].purpose", "enum") in result


def test_pathological_numeric_reply_is_diagnostic_not_an_arithmetic_crash():
    value, draft = example()
    value["plan"]["segments"][0]["source_out_s"] = 10 ** 400
    result = codes(value, draft)
    assert ("$.plan.segments[0].source_out_s", "number") in result
    assert ("$.duration.total_s", "blocked") in result
