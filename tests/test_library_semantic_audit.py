"""Evidence protocol regressions; fixture judgments are not model/video truth."""
from copy import deepcopy

import pytest

from omni_story.library import semantic_audit as audit
from omni_story.library.state import json_sha

SOURCE_SHA = "a" * 64
PROXY_SHA = "b" * 64
VIDEO_SHA = "c" * 64


@pytest.fixture
def slice_data():
    segment = {"segment_id": "seg_2", "slot_id": "slot_2", "source_id": "movie_1",
        "window_id": "w_2", "source_in_s": 2281, "source_out_s": 2289,
        "speed": 1, "role_ids": ["panda"], "visual_claims": [
            {"claim_id": "vc_seg2_action", "kind": "visual_action", "description": "A visible action happens."}],
        "caption": {"text": "面对强压", "start_s": 0, "end_s": 4,
                    "evidence": [{"window_id": "w_2", "event_indices": [0]}]}}
    plan = {"segments": [segment], "fps": 30,
        "focus_role_bindings": [{"window_id": "w_2", "role_id": "panda", "identity_evidence": "Black-white panda."}],
        "slots": [{"slot_id": "slot_2", "intended_takeaway": "The assembled actions express perseverance."}],
        "editing_bindings": [{"method_id": "method_0", "segment_ids": ["seg_2"],
            "intended_relation": "Weakness followed by effective action.",
            "operation": "Two neighboring ranges are hard-cut together.",
            "verification": "The actual output shows a visible change."}]}
    proxy = {"sha256": PROXY_SHA, "source_id": "movie_1", "source_sha256": SOURCE_SHA,
             "source_start_s": 2281, "source_end_s": 2289, "duration_s": 8}
    observation = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": "seg_2", "source_id": "movie_1",
        "source_sha256": SOURCE_SHA, "source_in_s": 2281, "source_out_s": 2289,
        "proxy_sha256": PROXY_SHA, "observed_duration_s": 8,
        "characters": [{"character_id": "visible_panda", "appearance": "Black-white fur, broad body."}],
        "evidence": [
            {"evidence_id": "src_action", "kind": "visual_action", "local_start_s": 0, "local_end_s": 8,
             "description": "The panda faces the small instructor and speaks.",
             "character_ids": ["visible_panda"], "basis_evidence_ids": []},
            {"evidence_id": "src_text", "kind": "visible_text", "local_start_s": 3, "local_end_s": 6,
             "description": "I never give up.", "character_ids": [], "basis_evidence_ids": []},
            {"evidence_id": "src_outcome", "kind": "visual_outcome", "local_start_s": 7, "local_end_s": 8,
             "description": "The panda remains standing.", "character_ids": ["visible_panda"], "basis_evidence_ids": []},
            {"evidence_id": "src_inference", "kind": "inference", "local_start_s": 3, "local_end_s": 6,
             "description": "The panda seems determined.", "character_ids": ["visible_panda"],
             "basis_evidence_ids": ["src_action", "src_text"]}], "uncertainties": []}
    return plan, segment, proxy, observation


def comparison(plan, segment, observation):
    claims = audit.segment_required_claims(plan, segment)
    record = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": segment["segment_id"],
              "observation_sha256": json_sha(observation), "claim_checks": [
                  {"claim_id": c["claim_id"], "status": "supported", "evidence_ids": ["src_action"],
                   "reason": "The exact slice provides an observed cue.", "limitations": []}
                  for c in claims], "uncertainties": []}
    return claims, record


def blind_data():
    return {"protocol": audit.SEMANTIC_PROTOCOL, "video_sha256": VIDEO_SHA,
        "observed_story": "The panda speaks to the instructor.",
        "apparent_theme": "Determination is suggested, achievement remains uncertain.",
        "main_characters": ["Black-white panda", "Small red-brown instructor"],
        "text_dependency": "assists", "confusions": [], "evidence": [
            {"evidence_id": "blind_action", "claim_id": "blind_dialogue", "kind": "visual_action",
             "start_s": 0, "end_s": 8, "observed_fact": "The characters face each other and speak.",
             "basis_evidence_ids": []},
            {"evidence_id": "blind_text", "claim_id": "blind_words", "kind": "visible_text",
             "start_s": 3, "end_s": 6, "observed_fact": "I never give up.", "basis_evidence_ids": []},
            {"evidence_id": "blind_inference", "claim_id": "blind_determination", "kind": "inference",
             "start_s": 3, "end_s": 6, "observed_fact": "The panda may be determined.",
             "basis_evidence_ids": ["blind_action", "blind_text"]}]}


def review_data(required, blind):
    checks = [{"claim_id": c["claim_id"], "status": "supported",
               "evidence_refs": [{"segment_id": "seg_2", "evidence_id": "src_action"}],
               "blind_evidence_ids": [], "reason": "Compare the recorded direct cue.", "limitations": []}
              for c in required]
    for evidence in blind["evidence"]:
        checks.append({"claim_id": evidence["claim_id"], "status": "supported",
                       "evidence_refs": [], "blind_evidence_ids": [
                           evidence["evidence_id"] if evidence["kind"] != "inference" else "blind_action"],
                       "reason": "The typed record supports this comparison.", "limitations": []})
    return {"protocol": audit.SEMANTIC_PROTOCOL, "video_sha256": VIDEO_SHA,
            "theme_status": "pass", "editing_status": "pass", "continuity_status": "pass",
            "visual_narrative_status": "pass", "fact_checks": checks, "contradictions": []}


def test_observe_exact_slice_before_plan_comparison_and_keep_inputs_immutable(slice_data):
    plan, segment, proxy, observation = slice_data
    before = deepcopy(slice_data)
    assert audit.validate_segment_observation(observation, segment, SOURCE_SHA, proxy) is observation
    claims, check = comparison(plan, segment, observation)
    assert audit.validate_segment_claim_check(check, observation, claims) is check
    assert slice_data == before
    assert {c["kind"] for c in claims} == {"role_presence", "identity", "caption", "visual_action"}
    # A slot or montage cannot be imposed as a complete requirement on each short slice.
    assert not any(c["kind"].startswith("editing_") or c["kind"] == "slot_takeaway" for c in claims)
    assert len(audit.output_required_claims(plan)) == 4


@pytest.mark.parametrize("mutation,match", [
    (lambda s,p,o: p.update(source_start_s=2200, source_end_s=2290), "exact_slice_proxy_required"),
    (lambda s,p,o: o.update(proxy_sha256="d"*64), "observation_binding_changed"),
    (lambda s,p,o: o.update(source_in_s=2200), "observation_binding_changed"),
    (lambda s,p,o: o["evidence"][0].update(local_end_s=9), "outside_observed_slice"),
    (lambda s,p,o: o["evidence"][0].update(local_start_s=2281, local_end_s=2289), "outside_observed_slice"),
    (lambda s,p,o: o["evidence"][0].update(character_ids=["absent_character"]), "unknown_or_duplicate_ref"),
    (lambda s,p,o: o["evidence"][3].update(basis_evidence_ids=[]), "inference_requires_direct_evidence"),
    (lambda s,p,o: o.update(required_claims=[]), "mixed_time_domains"),
])
def test_broad_window_plan_leak_and_unbound_facts_cannot_be_exact_observation(slice_data, mutation, match):
    _, segment, proxy, observation = slice_data
    mutation(segment, proxy, observation)
    with pytest.raises(ValueError, match=match):
        audit.validate_segment_observation(observation, segment, SOURCE_SHA, proxy)


def test_all_planned_slice_claims_are_checked_without_rewriting_observation(slice_data):
    plan, segment, _, observation = slice_data
    claims, check = comparison(plan, segment, observation)
    check["claim_checks"].pop()
    with pytest.raises(ValueError, match="checked_exactly_once"):
        audit.validate_segment_claim_check(check, observation, claims)
    _, check = comparison(plan, segment, observation)
    observation["evidence"][0]["description"] = "Changed after the first observation."
    with pytest.raises(ValueError, match="observation_changed"):
        audit.validate_segment_claim_check(check, observation, claims)


@pytest.mark.parametrize("evidence_ids", [["src_text"], ["src_inference"]])
def test_visual_action_cannot_be_supported_by_only_text_or_inference(slice_data, evidence_ids):
    plan, segment, _, observation = slice_data
    claims, check = comparison(plan, segment, observation)
    row = next(c for c in check["claim_checks"] if c["claim_id"] == "vc_seg2_action")
    row["evidence_ids"] = evidence_ids
    with pytest.raises(ValueError, match="cannot_prove_visible_action"):
        audit.validate_segment_claim_check(check, observation, claims)
    row.update(status="unsupported", limitations=["The slice provides no explosive action evidence."])
    audit.validate_segment_claim_check(check, observation, claims)


def test_silent_blind_distinguishes_visible_text_from_inferred_meaning():
    blind = blind_data()
    assert audit.validate_visual_blind(blind, 8, VIDEO_SHA) is blind
    blind["evidence"][2]["basis_evidence_ids"] = ["blind_inference"]
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        audit.validate_visual_blind(blind, 8, VIDEO_SHA)


def test_dialogue_vs_explosive_action_unresolved_conflict_cannot_complete(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    review["contradictions"] = [{"contradiction_id": "dialogue_or_explosion",
        "claim_ids": ["vc_seg2_action", "blind_dialogue"], "status": "unresolved",
        "reason": "The plan asserts explosive action; the immutable reading records dialogue.",
        "resolution": None, "evidence_refs": [{"segment_id": "seg_2", "evidence_id": "src_action"}],
        "blind_evidence_ids": ["blind_action"]}]
    with pytest.raises(ValueError, match="unresolved_contradiction_blocks_pass"):
        audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)
    review.update(theme_status="partial", visual_narrative_status="partial")
    audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)
    assert not audit.semantic_review_passes(review, blind, [source_check])


def test_conflict_cannot_be_called_resolved_using_only_a_caption(slice_data):
    plan, segment, _, observation = slice_data
    required, _ = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    review["contradictions"] = [{"contradiction_id": "identity_conflict",
        "claim_ids": ["vc_seg2_action", "blind_dialogue"], "status": "resolved",
        "reason": "A comparison disagreed.", "resolution": "Caption supposedly establishes the fact.",
        "evidence_refs": [], "blind_evidence_ids": ["blind_text"]}]
    with pytest.raises(ValueError, match="resolution_needs_direct_visual_evidence"):
        audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)


def test_resolved_wrong_blind_claim_is_preserved_not_relabelled_supported(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    rejected = next(c for c in review["fact_checks"] if c["claim_id"] == "blind_dialogue")
    rejected.update(status="unsupported", limitations=["The original blind reading made an identification error."])
    review["contradictions"] = [{"contradiction_id": "corrected_blind_error",
        "claim_ids": ["vc_seg2_action", "blind_dialogue"], "status": "resolved",
        "reason": "Independent slice evidence resolves the discrepancy.",
        "resolution": "Retain the wrong original blind claim as unsupported.",
        "rejected_claim_ids": ["blind_dialogue"],
        "evidence_refs": [{"segment_id": "seg_2", "evidence_id": "src_action"}], "blind_evidence_ids": []}]
    audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)
    assert audit.semantic_review_passes(review, blind, [source_check], expected_segment_ids=["seg_2"])
    assert rejected["status"] == "unsupported"
    review["contradictions"][0]["rejected_claim_ids"] = ["vc_seg2_action"]
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)


def test_visible_action_without_visible_result_cannot_prove_required_outcome(slice_data):
    _, segment, _, observation = slice_data
    required = [{"claim_id": "required_result", "kind": "visual_outcome", "description": "An outcome is visible."}]
    check = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": segment["segment_id"],
        "observation_sha256": json_sha(observation), "claim_checks": [{
            "claim_id": "required_result", "status": "supported", "evidence_ids": ["src_action"],
            "reason": "Only the action was seen.", "limitations": []}], "uncertainties": []}
    with pytest.raises(ValueError, match="cannot_prove_visible_action"):
        audit.validate_segment_claim_check(check, observation, required)
    check["claim_checks"][0]["evidence_ids"] = ["src_outcome"]
    audit.validate_segment_claim_check(check, observation, required)


@pytest.mark.parametrize("failure", ["drop_claim", "unknown_evidence", "unsupported", "text_essential", "blind_changed"])
def test_output_review_cannot_skip_hard_claims_or_promote_missing_evidence(slice_data, failure):
    plan, segment, _, observation = slice_data
    required, _ = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    if failure == "drop_claim":
        review["fact_checks"].pop()
    elif failure == "unknown_evidence":
        review["fact_checks"][0]["evidence_refs"][0]["evidence_id"] = "nonexistent"
    elif failure == "unsupported":
        review["fact_checks"][0].update(status="unsupported", limitations=["Not visible."])
    elif failure == "text_essential":
        blind["text_dependency"] = "essential"
    else:
        blind["video_sha256"] = "d"*64
    with pytest.raises(ValueError):
        audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)


def test_final_gate_needs_actual_slice_comparisons_not_just_review_pass(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA)
    assert not audit.semantic_review_passes(review, blind)
    assert audit.semantic_review_passes(review, blind, [source_check])
    assert not audit.semantic_review_passes(review, blind, [source_check], expected_segment_ids=["seg_1", "seg_2"])
    assert not audit.semantic_review_passes(review, blind, [source_check, source_check])
    source_check["claim_checks"][0].update(status="unverifiable", limitations=["Identity unclear."])
    assert not audit.semantic_review_passes(review, blind, [source_check])


def test_remaining_revision_request_blocks_completion_even_when_flags_all_pass(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    review["revision_requests"] = ["Recheck the final action result in the actual output."]
    assert not audit.semantic_review_passes(review, blind, [source_check], expected_segment_ids=["seg_2"])
    candidate = {"round": 2, "review": review, "blind": blind, "segment_checks": [source_check],
                 "expected_segment_ids": ["seg_2"]}
    choice = {"selected_round": 2, "reason": "Retain a technically valid candidate.",
              "evidence_claim_ids": ["blind_dialogue"], "limitations": []}
    with pytest.raises(ValueError, match="needs_limitations"):
        audit.validate_semantic_selection(choice, [candidate])
    choice["limitations"] = list(review["revision_requests"])
    audit.validate_semantic_selection(choice, [candidate])


def test_whole_review_cannot_promote_independent_unsupported_slice_claim(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    original = next(c for c in source_check["claim_checks"] if c["claim_id"] == "vc_seg2_action")
    original.update(status="unsupported", limitations=["The exact source provides dialogue only."])
    blind = blind_data()
    review = review_data(required, blind)
    review.update(theme_status="partial", visual_narrative_status="partial")
    with pytest.raises(ValueError, match="cannot_promote_unsupported_slice_claim"):
        audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA,
                                       segment_checks=[source_check])
    row = next(c for c in review["fact_checks"] if c["claim_id"] == "vc_seg2_action")
    row.update(status="unsupported", limitations=["The independent exact-slice fact is preserved."])
    audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA,
                                   segment_checks=[source_check])
    source_check["observation_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="source_check_observation_changed"):
        audit.validate_semantic_review(review, blind, [observation], required, 8, VIDEO_SHA,
                                       segment_checks=[source_check])


@pytest.mark.parametrize("status", ["partial", "unsupported", "unverifiable"])
def test_selection_does_not_call_non_supported_slice_claim_verified(slice_data, status):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    original = next(c for c in source_check["claim_checks"] if c["claim_id"] == "vc_seg2_action")
    original.update(status=status, limitations=["The exact source does not establish this claim."])
    blind = blind_data()
    review = review_data(required, blind)
    review.update(theme_status="partial", visual_narrative_status="partial")
    candidate = {"round": 2, "review": review, "blind": blind, "segment_checks": [source_check],
                 "expected_segment_ids": ["seg_2"]}
    choice = {"selected_round": 2, "reason": "Compare evidence, keeping limitations.",
              "evidence_claim_ids": ["vc_seg2_action"], "limitations": ["Partial candidate."]}
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        audit.validate_semantic_selection(choice, [candidate])
    choice["evidence_claim_ids"] = ["blind_dialogue"]
    audit.validate_semantic_selection(choice, [candidate])


def test_selection_excludes_claims_in_unresolved_contradictions(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    review.update(theme_status="partial", visual_narrative_status="partial")
    review["contradictions"] = [{"contradiction_id": "dialogue_or_explosion",
        "claim_ids": ["vc_seg2_action", "blind_dialogue"], "status": "unresolved",
        "reason": "The two claims disagree.", "resolution": None,
        "evidence_refs": [], "blind_evidence_ids": []}]
    candidate = {"round": 2, "review": review, "blind": blind, "segment_checks": [source_check]}
    choice = {"selected_round": 2, "reason": "Keep the best available limited film.",
              "evidence_claim_ids": ["blind_dialogue"], "limitations": ["Unresolved action conflict."]}
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        audit.validate_semantic_selection(choice, [candidate])
    choice["evidence_claim_ids"] = ["blind_words"]
    audit.validate_semantic_selection(choice, [candidate])


def test_caption_display_and_actual_evidence_must_overlap_not_just_selected_range(slice_data):
    plan, segment, _, _ = slice_data
    windows = {"w_2": {"window_id": "w_2", "source_start_s": 2200, "observation": {
        "events": [{"local_start_s": 87, "local_end_s": 89}]}}}
    # This is the actual run's defect: display [2281,2285], evidence [2287,2289].
    with pytest.raises(ValueError, match="not_visible_during_display"):
        audit.validate_caption_temporal_evidence(plan, windows)
    segment["caption"].update(start_s=6, end_s=8)
    assert audit.validate_caption_temporal_evidence(plan, list(windows.values())) is plan


def test_caption_speed_and_freeze_mapping_and_no_implicit_foreshadow(slice_data):
    plan, segment, _, _ = slice_data
    windows = {"w_2": {"window_id": "w_2", "source_start_s": 2200, "observation": {
        "events": [{"local_start_s": 87, "local_end_s": 89}]}}}
    segment["speed"] = 2
    segment["caption"].update(start_s=3, end_s=4)
    audit.validate_caption_temporal_evidence(plan, windows)  # source [2287,2289]
    segment["freeze_tail_s"] = 2
    segment["caption"].update(start_s=4, end_s=6)
    audit.validate_caption_temporal_evidence(plan, windows)  # held final real frame
    segment["caption"]["evidence_mode"] = "foreshadow"
    with pytest.raises(ValueError, match="not_supported"):
        audit.validate_caption_temporal_evidence(plan, windows)


def test_selection_can_only_cite_checked_claims_and_preserves_partial_limitations(slice_data):
    plan, segment, _, observation = slice_data
    required, source_check = comparison(plan, segment, observation)
    blind = blind_data()
    review = review_data(required, blind)
    candidate = {"round": 2, "review": review, "blind": blind, "segment_checks": [source_check]}
    choice = {"selected_round": 2, "reason": "Compare verified output evidence.",
              "evidence_claim_ids": ["blind_dialogue"], "limitations": []}
    assert audit.validate_semantic_selection(choice, [candidate]) is choice
    candidate["expected_segment_ids"] = ["seg_1", "seg_2"]
    with pytest.raises(ValueError, match="needs_limitations"):
        audit.validate_semantic_selection(choice, [candidate])
    candidate["expected_segment_ids"] = ["seg_2"]
    audit.validate_semantic_selection(choice, [candidate])
    choice["evidence_claim_ids"] = ["unknown_claim"]
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        audit.validate_semantic_selection(choice, [candidate])
    choice["evidence_claim_ids"] = ["blind_dialogue"]
    review.update(theme_status="partial", visual_narrative_status="partial")
    with pytest.raises(ValueError, match="needs_limitations"):
        audit.validate_semantic_selection(choice, [candidate])
    choice["limitations"] = ["The visible result does not establish the requested relationship."]
    audit.validate_semantic_selection(choice, [candidate])
