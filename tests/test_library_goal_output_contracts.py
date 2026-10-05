"""Goal-only actual-output bindings; synthetic judgments are not viewer truth."""
from copy import deepcopy

import pytest

from omni_story.library import goal_output_contracts as goal


@pytest.fixture
def case():
    sha = "a" * 64
    rendered = {"sha256": sha, "measured_duration_s": 6,
        "provenance": [
            {"segment_id": "segment_a", "slot_id": "slot_a", "output_in_s": 0, "output_out_s": 2},
            {"segment_id": "segment_b", "slot_id": "slot_b", "output_in_s": 2, "output_out_s": 4},
            {"segment_id": "segment_c", "slot_id": "slot_a", "output_in_s": 4, "output_out_s": 6}]}
    blind = {"video_sha256": sha, "evidence": [
        {"evidence_id": "output_action", "kind": "visual_action", "start_s": .5, "end_s": 1.5,
         "observed_fact": "A geometric object visibly moves."},
        {"evidence_id": "output_outcome", "kind": "visual_outcome", "start_s": 4.5, "end_s": 5.5,
         "observed_fact": "The geometric object remains in a new position."},
        {"evidence_id": "output_text", "kind": "visible_text", "start_s": .5, "end_s": 1.5,
         "observed_fact": "A written instruction is visible."},
        {"evidence_id": "output_guess", "kind": "inference", "start_s": .5, "end_s": 1.5,
         "observed_fact": "The object may intend something."}]}
    claims = [{"claim_id": "action", "kind": "visual_action", "segment_id": "segment_a"},
              {"claim_id": "identity", "kind": "identity", "segment_id": "segment_a"},
              {"claim_id": "outcome", "kind": "visual_outcome", "segment_id": "segment_c"},
              {"claim_id": "slot", "kind": "slot_takeaway", "owner_id": "slot_a", "segment_id": None},
              {"claim_id": "draft", "kind": "slot_takeaway", "owner_id": "old_slot",
               "origin": "draft_obligation", "segment_ids": ["segment_a", "segment_c"]}]
    review = {"video_sha256": sha, "fact_checks": [
        {"claim_id": claim["claim_id"], "status": "supported", "evidence_refs": ["source_reference"],
         "blind_evidence_ids": ["output_outcome"] if claim["kind"] == "visual_outcome"
              else ["output_action", "output_outcome"] if claim["kind"] == "slot_takeaway" else ["output_action"]}
        for claim in claims]}
    return review, blind, claims, rendered


def test_direct_actual_output_support_preserves_all_inputs(case):
    original = deepcopy(case)
    assert goal.validate_goal_review(*case) is case[0]
    assert case == original


def test_existing_strict_review_can_be_valid_but_source_only_claims_fail_goal_extension(case):
    """Apply the new condition after a valid original protocol, not instead of it."""
    from omni_story.library import semantic_audit as audit
    review, blind, claims, rendered = case
    blind.update(protocol=audit.SEMANTIC_PROTOCOL, observed_story="Synthetic visible geometry moves.",
        apparent_theme="A synthetic visible state change.", main_characters=["Geometric object"],
        confusions=[], text_dependency="none")
    for row in blind["evidence"]:
        row.update(claim_id="blind_" + row["evidence_id"],
            basis_evidence_ids=["output_action"] if row["kind"] == "inference" else [])
    observations = [{"segment_id": row["segment_id"], "evidence": [
        {"evidence_id": "source_action", "kind": "visual_action", "character_ids": ["object"]},
        {"evidence_id": "source_outcome", "kind": "visual_outcome", "character_ids": ["object"]}]}
        for row in rendered["provenance"]]
    for claim, check in zip(claims, review["fact_checks"]):
        check.update(blind_evidence_ids=[], reason="Synthetic independent source observation.", limitations=[],
            evidence_refs=[{"segment_id": claim.get("segment_id") or "segment_a",
                "evidence_id": "source_outcome" if claim["kind"] == "visual_outcome" else "source_action"}])
    for row in blind["evidence"]:
        review["fact_checks"].append({"claim_id": row["claim_id"], "status": "supported",
            "evidence_refs": [], "blind_evidence_ids": [
                "output_action" if row["kind"] == "inference" else row["evidence_id"]],
            "reason": "Synthetic actual-output observation.", "limitations": []})
    review.update(protocol=audit.SEMANTIC_PROTOCOL, theme_status="pass", editing_status="pass",
        continuity_status="pass", visual_narrative_status="pass", contradictions=[])
    assert audit.validate_semantic_review(review, blind, observations, claims, 6, rendered["sha256"]) is review
    with pytest.raises(ValueError, match="output_evidence:list_required"):
        goal.validate_goal_review(review, blind, claims, rendered)


@pytest.mark.parametrize("kind", ["visual_action", "visual_outcome", "identity", "slot_takeaway"])
def test_source_only_supported_claim_cannot_pass_output_gate(case, kind):
    review, blind, claims, rendered = case
    claim = next(row for row in claims if row["kind"] == kind)
    check = next(row for row in review["fact_checks"] if row["claim_id"] == claim["claim_id"])
    check["blind_evidence_ids"] = []
    with pytest.raises(ValueError, match="output_evidence:list_required"):
        goal.validate_goal_review(review, blind, claims, rendered)


@pytest.mark.parametrize("evidence_id", ["output_text", "output_guess"])
def test_text_or_inference_does_not_prove_visible_action(case, evidence_id):
    case[0]["fact_checks"][0]["blind_evidence_ids"] = [evidence_id]
    with pytest.raises(ValueError, match="requires_direct_visible_output"):
        goal.validate_goal_review(*case)


def test_visual_outcome_needs_actual_output_outcome(case):
    case[0]["fact_checks"][2]["blind_evidence_ids"] = ["output_action"]
    with pytest.raises(ValueError, match="requires_direct_visible_output"):
        goal.validate_goal_review(*case)


def test_correct_evidence_in_a_different_segment_does_not_support_local_claim(case):
    case[0]["fact_checks"][0]["blind_evidence_ids"] = ["output_outcome"]
    with pytest.raises(ValueError, match="requires_direct_visible_output"):
        goal.validate_goal_review(*case)


def test_touching_the_segment_boundary_is_not_visible_overlap(case):
    case[1]["evidence"][0].update(start_s=2, end_s=3)
    with pytest.raises(ValueError, match="requires_direct_visible_output"):
        goal.validate_goal_review(*case)


@pytest.mark.parametrize("status", ["partial", "unsupported", "unverifiable"])
def test_limited_claims_are_preserved_without_fabricating_output_evidence(case, status):
    for check in case[0]["fact_checks"]:
        check.update(status=status, blind_evidence_ids=[])
    original = deepcopy(case)
    assert goal.validate_goal_review(*case) is case[0]
    assert case == original


def test_slot_support_can_span_several_disjoint_real_output_segments(case):
    case[0]["fact_checks"][3]["blind_evidence_ids"] = ["output_action", "output_outcome"]
    goal.validate_goal_review(*case)


def test_slot_support_cannot_use_a_different_slot(case):
    case[2][3]["owner_id"] = "slot_b"
    with pytest.raises(ValueError, match="requires_direct_visible_output"):
        goal.validate_goal_review(*case)


def test_original_draft_obligation_uses_its_final_segment_bindings(case):
    case[2][4]["segment_ids"] = ["segment_b"]
    with pytest.raises(ValueError, match="requires_direct_visible_output"):
        goal.validate_goal_review(*case)


def test_raw_renderer_indices_are_not_guessed_to_be_model_segment_ids(case):
    for index, row in enumerate(case[3]["provenance"]):
        row.pop("segment_id")
        row["segment_index"] = index
    with pytest.raises(ValueError, match="segment_id:text_required"):
        goal.validate_goal_review(*case)


def test_slot_without_actual_slot_binding_cannot_be_called_supported(case):
    for row in case[3]["provenance"]:
        row.pop("slot_id")
    with pytest.raises(ValueError, match="slot_provenance_binding_required"):
        goal.validate_goal_review(*case)


@pytest.mark.parametrize("target", ["review", "blind"])
def test_actual_output_sha_cannot_be_substituted(case, target):
    case[0 if target == "review" else 1]["video_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="actual_video_binding_changed"):
        goal.validate_goal_review(*case)


def test_direct_output_evidence_must_be_within_actual_video(case):
    case[1]["evidence"][0].update(end_s=7)
    with pytest.raises(ValueError, match="direct_evidence_outside_video"):
        goal.validate_goal_review(*case)
