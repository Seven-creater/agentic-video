"""Synthetic contracts test plumbing and scoped obligations, never film quality."""
from copy import deepcopy
import json

import pytest

from omni_story.library import scoped_edit_evidence as scoped
from omni_story.library.state import json_sha


@pytest.fixture
def case():
    segment = {"segment_id": "new_slice", "slot_id": "slot_1", "window_id": "window_1",
        "role_ids": ["actor"], "speed": .75, "freeze_tail_s": 1,
        "source_claims": [
            {"claim_id": "source_action", "kind": "visual_action", "description": "The actor lowers an object."},
            {"claim_id": "source_state", "kind": "visual_state", "description": "The object stays on a table."},
            {"claim_id": "source_result", "kind": "visual_outcome", "description": "The object ends on the table."},
            {"claim_id": "source_text", "kind": "visible_text", "description": "READY is visible."}],
        "output_operation_claims": [
            {"claim_id": "output_speed", "kind": "speed", "description": "The action is played at 0.75x.", "expected_value": .75},
            {"claim_id": "output_hold", "kind": "tail_hold", "description": "The last real frame stays for one second.", "expected_value": 1}],
        "visual_claims": [{"claim_id": "legacy", "kind": "identity", "description": "A legacy compound claim."}],
        "caption": {"text": "An added title."}}
    slots = [{"slot_id": "slot_1", "intended_takeaway": "An action leads to an observable result."}]
    plan = {"segments": [segment], "slots": deepcopy(slots),
        "focus_role_bindings": [{"window_id": "window_1", "role_id": "actor",
            "identity_evidence": "The actor appears at multiple window times and repeatedly changes position."}]}
    # This is a previously received body; the new target ID is an envelope field.
    observation = {"segment_id": "historical_slice", "characters": [
        {"character_id": "visible_actor", "appearance": "A figure wearing a blue coat."}],
        "evidence": [
            {"evidence_id": "action", "kind": "visual_action", "local_start_s": 0, "local_end_s": 1,
             "description": "The actor lowers the object.", "character_ids": ["visible_actor"]},
            {"evidence_id": "state", "kind": "visual_action", "local_start_s": 1, "local_end_s": 3,
             "description": "The object remains on the table.", "character_ids": ["visible_actor"]},
            {"evidence_id": "text", "kind": "visible_text", "local_start_s": 0, "local_end_s": 2,
             "description": "READY", "character_ids": []},
            {"evidence_id": "inference", "kind": "inference", "local_start_s": 0, "local_end_s": 3,
             "description": "The action might mean agreement.", "character_ids": ["visible_actor"]}]}
    claims = scoped.scoped_segment_claims(plan, segment)
    checks = []
    for claim in claims:
        kind = claim["kind"]
        evidence_ids = ["action"]
        basis = {"explanation": "The cited observation explicitly records this information."}
        if kind == "role_presence":
            relation = "appearance_matches_context"
            basis.update(role_id=claim["role_id"], character_ids=["visible_actor"])
        elif kind == "visual_action":
            relation = "direct_action"
        elif kind == "visual_state":
            relation, evidence_ids = "observed_state", ["state"]
        elif kind == "visual_outcome":
            relation, evidence_ids = "observed_result", ["state"]
            basis.update(mode="explicit_result_state", result_evidence_id="state",
                         result_quote="object remains on the table")
        else:
            relation, evidence_ids = "text_reading", ["text"]
        checks.append({"claim_id": claim["claim_id"], "status": "supported", "evidence_ids": evidence_ids,
            "semantic_relation": relation, "basis": basis, "reason": "The observed cue matches the claim.",
            "limitations": []})
    comparison = {"protocol": scoped.PROTOCOL, "segment_id": "new_slice",
        "observation_sha256": json_sha(observation), "claim_checks": checks, "uncertainties": []}
    return plan, slots, observation, claims, comparison


def validate(case):
    _, _, observation, claims, comparison = case
    return scoped.validate_scoped_comparison(comparison, observation, claims, target_segment_id="new_slice")


def row(case, claim_id):
    return next(check for check in case[-1]["claim_checks"] if check["claim_id"] == claim_id)


def test_short_slice_obligations_do_not_inherit_window_identity_or_output_operations(case):
    plan, _, _, claims, _ = case
    assert {claim["kind"] for claim in claims} == {
        "role_presence", "visual_action", "visual_state", "visual_outcome", "visible_text"}
    assert all(claim["scope"] == "source_slice" for claim in claims)
    assert not any("multiple window times" in claim["description"] for claim in claims)
    context = scoped.window_identity_context(plan, plan["segments"][0])
    assert context == plan["focus_role_bindings"]
    context[0]["identity_evidence"] = "Changed copy."
    assert plan["focus_role_bindings"][0]["identity_evidence"].startswith("The actor")


def test_legacy_visual_claims_are_not_silently_normalized(case):
    plan = case[0]
    del plan["segments"][0]["source_claims"]
    with pytest.raises(ValueError, match="source_claims:list_required"):
        scoped.scoped_segment_claims(plan, plan["segments"][0])


def test_old_action_kind_can_explicitly_support_state_without_mutation(case):
    before = deepcopy(case)
    assert validate(case) is case[-1]
    assert case == before
    assert case[2]["evidence"][1]["kind"] == "visual_action"


@pytest.mark.parametrize("mutation,match", [
    (lambda c: c[-1].update(observation_sha256="0" * 64), "observation_changed"),
    (lambda c: c[-1]["claim_checks"].pop(), "checked_exactly_once"),
    (lambda c: row(c, "source_state").pop("semantic_relation"), "semantic_relation_required"),
    (lambda c: row(c, "source_state")["basis"].update(explanation=""), "text_required"),
    (lambda c: row(c, "source_state").update(semantic_relation="direct_action"), "state_needs"),
    (lambda c: row(c, "source_result").update(semantic_relation="direct_action"), "outcome_needs"),
    (lambda c: row(c, "source_result")["basis"].update(mode="relevant_action"), "explicit_result_state_basis"),
    (lambda c: row(c, "source_result")["basis"].update(result_quote="An invented result."), "quote_not_in_observation"),
    (lambda c: row(c, "source_result")["basis"].update(result_evidence_id="action"), "must_be_cited_direct_visual"),
    (lambda c: row(c, "source_action").update(evidence_ids=["text"]), "direct_visual_evidence"),
    (lambda c: row(c, "source_state").update(evidence_ids=["inference"]), "direct_visual_evidence"),
    (lambda c: row(c, "source_text").update(evidence_ids=["action"]), "text_reading_evidence"),
])
def test_unknown_unbound_or_wrong_semantic_basis_cannot_pass(case, mutation, match):
    mutation(case)
    with pytest.raises(ValueError, match=match):
        validate(case)


def test_stable_role_presence_binds_observed_character_without_requiring_whole_window(case):
    presence = next(check for check in case[-1]["claim_checks"]
                    if check["semantic_relation"] == "appearance_matches_context")
    presence["basis"]["role_id"] = "different_role"
    with pytest.raises(ValueError, match="stable_role_id_changed"):
        validate(case)
    presence["basis"]["role_id"] = "actor"
    presence["evidence_ids"] = ["text"]
    with pytest.raises(ValueError, match="direct_visual_evidence"):
        validate(case)


@pytest.mark.parametrize("relation", ["unknown", "not_observed", "insufficient_scope"])
def test_unknown_and_missing_scope_allow_limited_result_but_never_support_or_counterevidence(case, relation):
    check = row(case, "source_action")
    check.update(semantic_relation=relation)
    with pytest.raises(ValueError, match="unknown_cannot"):
        validate(case)
    check.update(status="partial", limitations=["The record does not establish this part."])
    validate(case)
    check.update(status="unverifiable", evidence_ids=[])
    validate(case)
    check.update(status="unsupported")
    with pytest.raises(ValueError, match="unknown_cannot"):
        validate(case)


def test_unsupported_requires_observed_counterevidence_not_omission(case):
    check = row(case, "source_action")
    check.update(status="unsupported", semantic_relation="contradicted",
                 limitations=["The directly observed state contradicts the proposed action."], evidence_ids=[])
    with pytest.raises(ValueError, match="direct_counterevidence"):
        validate(case)
    check["evidence_ids"] = ["state"]
    validate(case)


def test_result_can_use_explicit_before_after_records_but_not_same_action_twice(case):
    check = row(case, "source_result")
    check.update(semantic_relation="observed_transition", evidence_ids=["action", "state"])
    check["basis"] = {"explanation": "The action is followed by the recorded result state.",
        "before_evidence_ids": ["action"], "after_evidence_ids": ["state"],
        "change": "The object changes from moving to resting on the table.",
        "result_evidence_id": "state", "result_quote": "object remains on the table"}
    validate(case)
    check["basis"]["before_evidence_ids"] = ["state"]
    with pytest.raises(ValueError, match="distinct_before_after"):
        validate(case)


def test_only_actual_output_claims_cover_original_takeaway_and_transformed_operations(case):
    plan, original_slots = case[:2]
    assert scoped.validate_scoped_plan_claims(plan, original_slots) is plan
    output = scoped.scoped_output_claims(plan, original_slots)
    assert {claim["kind"] for claim in output} == {"slot_takeaway", "output_speed", "output_tail_hold"}
    assert all(claim["scope"] == "actual_output" for claim in output)
    assert output[0]["description"] == original_slots[0]["intended_takeaway"]
    case[-1]["claim_checks"] = []
    with pytest.raises(ValueError, match="only_current_source_slice_claims"):
        scoped.validate_scoped_comparison(case[-1], case[2], output, target_segment_id="new_slice")


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p["slots"][0].update(intended_takeaway="A weaker paraphrase."), "obligation_changed"),
    (lambda p: p.update(slots=[{"slot_id": "another", "intended_takeaway": "Another one."}]), "obligation_removed"),
    (lambda p: p["segments"][0]["output_operation_claims"].pop(), "missing_actual_output_claim"),
    (lambda p: p["segments"][0]["output_operation_claims"][0].update(expected_value=1), "value_changed"),
    (lambda p: p["segments"][0]["source_claims"][0].update(kind="identity"), "source_claim_kind"),
    (lambda p: p["segments"][0]["source_claims"][0].update(speed=.75), "cannot_be_source_claim"),
])
def test_original_obligations_and_operation_bindings_cannot_be_weakened(case, mutation, match):
    plan, slots = case[:2]
    mutation(plan)
    with pytest.raises(ValueError, match=match):
        scoped.validate_scoped_plan_claims(plan, slots)


def batch_case(case, count=16):
    _, _, observation, claims, comparison = case
    envelopes, by_segment, segments = [], {}, []
    for index in range(count):
        target = "target_" + str(index)
        envelopes.append({"segment_id": target, "observation": deepcopy(observation),
                          "observation_sha256": json_sha(observation)})
        required = deepcopy(claims)
        result = deepcopy(comparison)
        result["segment_id"] = target
        for claim, check in zip(required, result["claim_checks"], strict=True):
            claim["segment_id"] = target
            claim["claim_id"] += "_" + str(index)
            check["claim_id"] = claim["claim_id"]
        by_segment[target] = required
        segments.append(result)
    value = {"protocol": scoped.PROTOCOL, "plan_sha256": "d" * 64,
             "segments": segments, "limitations": []}
    return envelopes, by_segment, value


def test_batch_covers_all_sixteen_new_targets_without_renaming_old_observations(case):
    envelopes, claims, value = batch_case(case)
    before = deepcopy((envelopes, claims, value))
    assert scoped.validate_batch_comparison(value, envelopes, claims, plan_sha256="d" * 64) is value
    assert (envelopes, claims, value) == before
    assert {e["observation"]["segment_id"] for e in envelopes} == {"historical_slice"}
    prompt = scoped.comparison_prompt(envelopes, claims, {"context": "Bound window context."},
                                      plan_sha256="d" * 64)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert len(payload["response_contract"]["segments"]) == 16
    assert payload["immutable_observations"] == envelopes


@pytest.mark.parametrize("mutation,match", [
    (lambda e,c,v: v["segments"].pop(), "all_segments"),
    (lambda e,c,v: v.update(plan_sha256="a" * 64), "plan_changed"),
    (lambda e,c,v: e[0]["observation"].update(segment_id="tampered"), "envelope_hash_changed"),
    (lambda e,c,v: c.pop("target_0"), "claim_segments_changed"),
    (lambda e,c,v: v["segments"][0].update(observation_sha256="b" * 64), "observation_changed"),
])
def test_batch_rejects_incomplete_unbound_or_changed_facts(case, mutation, match):
    envelopes, claims, value = batch_case(case)
    mutation(envelopes, claims, value)
    with pytest.raises(ValueError, match=match):
        scoped.validate_batch_comparison(value, envelopes, claims, plan_sha256="d" * 64)
