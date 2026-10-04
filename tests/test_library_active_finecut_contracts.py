"""Pure contract checks for model-owned fine cuts and actual-output economy.

All plans and reviews below are synthetic dictionaries. These tests establish
accounting, provenance and conservative gates, not GLM editing quality.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import active_finecut
from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL, semantic_review_passes
from omni_story.library.state import LibraryState, write_json


def _segment(segment_id, slot_id, start, end, *, speed=1, hold=0):
    return {
        "segment_id": segment_id, "slot_id": slot_id,
        "source_id": "src_fixture", "window_id": "window_fixture",
        "source_in_s": start, "source_out_s": end, "speed": speed,
        "freeze_tail_s": hold, "role_ids": ["local_actor"],
        "look": "none", "framing": "fit",
        "visual_claims": [{"claim_id": "claim_" + segment_id,
                           "kind": "visual_action", "description": "synthetic visible state"}],
    }


def _draft():
    return {
        "reference_sha256": "a" * 64, "focus_role_id": "focus_fixture",
        "focus_role_bindings": [{"window_id": "window_fixture", "role_id": "local_actor",
                                 "identity_evidence": "synthetic appearance evidence"}],
        "slots": [
            {"slot_id": "old_setup", "intended_takeaway": "original setup obligation",
             "segment_ids": ["d1"]},
            {"slot_id": "old_change", "intended_takeaway": "original change obligation",
             "segment_ids": ["d2"]},
        ],
        "segments": [_segment("d1", "old_setup", 10, 18),
                     _segment("d2", "old_change", 30, 36)],
        "editing_bindings": [{"method_id": "method_observed", "status": "planned",
            "segment_ids": ["d1", "d2"], "intended_relation": "synthetic relation",
            "operation": "hard cuts", "verification": "inspect actual output", "limitations": []}],
        "candidate_dispositions": [], "audio_mode": "silent", "source_gain_db": 0,
        "reference_gain_db": 0, "width": 720, "height": 1280, "fps": 30, "limitations": [],
    }


def _decision(segment_id, *, origin="general_optimization", method_ids=None):
    return {
        "segment_id": segment_id, "new_information": "observable new information",
        "in_out_reason": "retain the necessary beginning and result",
        "speed_reason": "readability determines the playback rate",
        "hold_reason": "no unsupported outcome is added by holding a frame",
        "origin": origin, "reference_method_ids": list(method_ids or []),
    }


def _refinement():
    draft = _draft()
    plan = deepcopy(draft)
    plan["slots"] = [{"slot_id": "new_combined", "intended_takeaway": "both original obligations",
                      "segment_ids": ["n1", "n2"]}]
    # 2 / .5 + 4 / 2 + 1 held second = 7 output seconds.
    plan["segments"] = [_segment("n1", "new_combined", 10.5, 12.5, speed=.5),
                        _segment("n2", "new_combined", 31, 35, speed=2, hold=1)]
    plan["editing_bindings"][0]["segment_ids"] = ["n1", "n2"]
    value = {
        "plan": plan, "decisions": [_decision("n1"), _decision("n2")],
        "obligation_coverage": [
            {"slot_id": "old_setup", "segment_ids": ["n1"], "status": "preserved",
             "reason": "model proposes support for the original setup"},
            {"slot_id": "old_change", "segment_ids": ["n2"], "status": "preserved",
             "reason": "model proposes support for the original change"},
        ],
        "draft_dispositions": [
            {"segment_id": "d1", "decision": "replaced", "replacement_segment_ids": ["n1"],
             "reason": "shorter source interval retains the required information"},
            {"segment_id": "d2", "decision": "replaced", "replacement_segment_ids": ["n2"],
             "reason": "model proposes a distinct short source interval"},
        ],
        "duration": {"target_s": 7, "total_s": 7, "over_target_reason": ""},
    }
    return draft, value


def test_refinement_can_reshape_slots_without_mutating_original_obligations():
    draft, value = _refinement()
    original_draft, original_reply = deepcopy(draft), deepcopy(value)
    active_finecut.validate_refinement(value, draft, 2)
    assert draft == original_draft
    assert value == original_reply
    assert [s["slot_id"] for s in value["plan"]["slots"]] == ["new_combined"]


def test_unresolved_obligation_remains_a_valid_report_and_is_not_rewritten():
    draft, value = _refinement()
    unresolved = value["obligation_coverage"][1]
    unresolved.update(status="unresolved", segment_ids=[], reason="required result has not been found")
    before = deepcopy(value)
    active_finecut.validate_refinement(value, draft, 2)
    assert value == before
    assert value["obligation_coverage"][1]["status"] == "unresolved"


def test_removed_draft_slice_does_not_remove_its_story_obligation():
    draft, value = _refinement()
    value["draft_dispositions"][0].update(decision="removed", replacement_segment_ids=[])
    active_finecut.validate_refinement(value, draft, 2)
    assert value["obligation_coverage"][0]["slot_id"] == "old_setup"
    assert draft["slots"][0]["intended_takeaway"] == "original setup obligation"


def test_retained_source_range_can_receive_a_model_selected_playback_rate():
    draft, value = _refinement()
    retained = deepcopy(draft["segments"][0])
    retained.update(slot_id="new_combined", speed=.5)
    value["plan"]["segments"][0] = retained
    value["plan"]["slots"][0]["segment_ids"] = ["d1", "n2"]
    value["plan"]["editing_bindings"][0]["segment_ids"] = ["d1", "n2"]
    value["decisions"][0] = _decision("d1")
    value["obligation_coverage"][0]["segment_ids"] = ["d1"]
    value["draft_dispositions"][0].update(decision="retained", replacement_segment_ids=["d1"])
    value["duration"] = {"target_s": 7, "total_s": 19,
                         "over_target_reason": "slow playback is needed to read this synthetic action"}
    active_finecut.validate_refinement(value, draft, 2)


@pytest.mark.parametrize("field,value", [
    ("source_id", "src_other"), ("window_id", "window_other"),
    ("source_in_s", 10.25), ("source_out_s", 17.75),
])
def test_retained_disposition_cannot_hide_changed_source_provenance(field, value):
    draft, reply = _refinement()
    retained = deepcopy(draft["segments"][0])
    retained.update(slot_id="new_combined")
    retained[field] = value
    reply["plan"]["segments"][0] = retained
    reply["plan"]["slots"][0]["segment_ids"] = ["d1", "n2"]
    reply["plan"]["editing_bindings"][0]["segment_ids"] = ["d1", "n2"]
    reply["decisions"][0] = _decision("d1")
    reply["obligation_coverage"][0]["segment_ids"] = ["d1"]
    reply["draft_dispositions"][0].update(decision="retained", replacement_segment_ids=["d1"])
    total = (retained["source_out_s"] - retained["source_in_s"]) / retained["speed"] + 3
    reply["duration"].update(total_s=total, over_target_reason="necessary retained information")
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(reply, draft, 2)


@pytest.mark.parametrize("collection,key", [
    ("decisions", "segment_id"), ("obligation_coverage", "slot_id"),
    ("draft_dispositions", "segment_id"),
])
@pytest.mark.parametrize("defect", ["missing", "duplicate", "unknown"])
def test_every_new_slice_original_obligation_and_draft_disposition_is_accounted_once(collection, key, defect):
    draft, value = _refinement()
    if defect == "missing":
        value[collection].pop()
    elif defect == "duplicate":
        value[collection].append(deepcopy(value[collection][0]))
    else:
        value[collection][-1][key] = "unknown_fixture_id"
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


@pytest.mark.parametrize("collection,field", [
    ("obligation_coverage", "segment_ids"),
    ("draft_dispositions", "replacement_segment_ids"),
])
def test_coverage_and_replacement_references_must_exist_in_final_edl(collection, field):
    draft, value = _refinement()
    value[collection][0][field] = ["missing_final_slice"]
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


@pytest.mark.parametrize("decision,replacements", [
    ("removed", ["n1"]), ("replaced", []), ("unrecognized", ["n1"]),
])
def test_draft_disposition_and_replacement_mapping_cannot_disagree(decision, replacements):
    draft, value = _refinement()
    value["draft_dispositions"][0].update(decision=decision, replacement_segment_ids=replacements)
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


@pytest.mark.parametrize("field", [
    "new_information", "in_out_reason", "speed_reason", "hold_reason",
])
def test_each_finecut_choice_needs_a_readable_reason(field):
    draft, value = _refinement()
    value["decisions"][0][field] = " "
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


def test_general_optimization_does_not_need_an_invented_reference_technique():
    draft, value = _refinement()
    assert all(d["reference_method_ids"] == [] for d in value["decisions"])
    active_finecut.validate_refinement(value, draft, 2)


def test_reference_transfer_cites_a_known_reference_method():
    draft, value = _refinement()
    value["decisions"][0] = _decision("n1", origin="reference_transfer", method_ids=["method_observed"])
    active_finecut.validate_refinement(value, draft, 2)


@pytest.mark.parametrize("origin,method_ids", [
    ("invented_origin", []), ("reference_transfer", []),
    ("reference_transfer", ["unknown_method"]),
])
def test_refinement_cannot_claim_unsupported_reference_transfer(origin, method_ids):
    draft, value = _refinement()
    value["decisions"][0].update(origin=origin, reference_method_ids=method_ids)
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


def test_final_microcuts_cannot_exceed_the_reserved_audit_capacity():
    draft, value = _refinement()
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 1)


@pytest.mark.parametrize("total", [6, 8, float("nan"), float("inf"), True])
def test_duration_accounting_includes_actual_speed_and_tail_hold(total):
    draft, value = _refinement()
    value["duration"]["total_s"] = total
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


def test_duration_over_target_requires_an_explicit_information_preservation_reason():
    draft, value = _refinement()
    value["duration"].update(target_s=6, over_target_reason="")
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)
    value["duration"]["over_target_reason"] = "necessary action and result need the extra second"
    active_finecut.validate_refinement(value, draft, 2)


@pytest.mark.parametrize("target", [0, -1, float("nan"), float("inf"), True])
def test_duration_target_is_positive_finite_seconds(target):
    draft, value = _refinement()
    value["duration"]["target_s"] = target
    with pytest.raises(ValueError):
        active_finecut.validate_refinement(value, draft, 2)


def _rendered():
    return {"sha256": "b" * 64, "measured_duration_s": 7,
            "provenance": [{"segment_id": "n1", "output_in_s": 0, "output_out_s": 4},
                           {"segment_id": "n2", "output_in_s": 4, "output_out_s": 7}]}


def _economy_review():
    return {"video_sha256": "b" * 64, "economy_status": "pass", "narrative_readability": "pass",
            "segment_checks": [
                {"segment_id": "n1", "status": "necessary", "output_evidence": [
                    {"start_s": .5, "end_s": 2, "observed_fact": "synthetic output information"}],
                 "reason": "this interval establishes a necessary visible state"},
                {"segment_id": "n2", "status": "necessary", "output_evidence": [
                    {"start_s": 4.5, "end_s": 6.5, "observed_fact": "different synthetic information"}],
                 "reason": "this interval shows a distinct visible change"},
            ], "limitations": []}


def test_economy_report_is_bound_to_actual_render_and_does_not_modify_it():
    rendered, review = _rendered(), _economy_review()
    original_render, original_review = deepcopy(rendered), deepcopy(review)
    active_finecut.validate_economy_review(review, rendered)
    assert rendered == original_render
    assert review == original_review


def test_economy_cannot_review_a_different_video_sha():
    value = _economy_review()
    value["video_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        active_finecut.validate_economy_review(value, _rendered())


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unknown"])
def test_economy_checks_cover_actual_segments_exactly_once(defect):
    value = _economy_review()
    if defect == "missing":
        value["segment_checks"].pop()
    elif defect == "duplicate":
        value["segment_checks"].append(deepcopy(value["segment_checks"][0]))
    else:
        value["segment_checks"][-1]["segment_id"] = "not_rendered"
    with pytest.raises(ValueError):
        active_finecut.validate_economy_review(value, _rendered())


@pytest.mark.parametrize("start,end", [
    (-.1, 1), (3.5, 4.5), (4, 5), (1, 1), (2, 1),
    (0, float("nan")), (True, 2),
])
def test_output_evidence_must_lie_in_its_own_rendered_segment(start, end):
    value = _economy_review()
    value["segment_checks"][0]["output_evidence"][0].update(start_s=start, end_s=end)
    with pytest.raises(ValueError):
        active_finecut.validate_economy_review(value, _rendered())


@pytest.mark.parametrize("status", ["redundant", "uncertain"])
def test_pass_cannot_hide_redundant_or_uncertain_rendered_information(status):
    value = _economy_review()
    value["segment_checks"][0]["status"] = status
    with pytest.raises(ValueError):
        active_finecut.validate_economy_review(value, _rendered())
    value.update(economy_status="partial", limitations=["remaining information economy is unresolved"])
    active_finecut.validate_economy_review(value, _rendered())


@pytest.mark.parametrize("readability", ["partial", "fail"])
def test_shorter_output_is_not_a_pass_if_its_narrative_is_unreadable(readability):
    value = _economy_review()
    value["narrative_readability"] = readability
    with pytest.raises(ValueError):
        active_finecut.validate_economy_review(value, _rendered())


def test_economy_has_no_universal_maximum_shot_length():
    # A long necessary observation is legitimate; measured length alone is not redundancy.
    rendered, review = _rendered(), _economy_review()
    rendered["measured_duration_s"] = 34
    rendered["provenance"][0]["output_out_s"] = 30
    rendered["provenance"][1].update(output_in_s=30, output_out_s=34)
    review["segment_checks"][1]["output_evidence"][0].update(start_s=31, end_s=33)
    active_finecut.validate_economy_review(review, rendered)


def _state(tmp_path):
    return LibraryState(tmp_path / "run", {"reference_sha256": "a" * 64,
        "library_sources": [], "configuration": {"max_fine": 16, "max_renders": 2}}, max_requests=80)


def test_forward_policy_does_not_activate_without_a_request(tmp_path):
    state = _state(tmp_path)
    before = deepcopy(state.data)
    assert active_finecut.enable_policy(state, False) is False
    assert state.data == before


def test_forward_policy_is_persisted_once_and_resumed_without_new_calls(tmp_path):
    state = _state(tmp_path)
    original_input = deepcopy(state.data["input_lock"])
    assert active_finecut.enable_policy(state, True) is True
    once = deepcopy(state.data)
    assert active_finecut.enable_policy(state, True) is True
    assert active_finecut.enable_policy(state, False) is True
    assert state.data == once
    assert state.data["input_lock"] == original_input
    assert state.data["request_count"] == 0 and state.data["calls"] == []
    assert state.data["max_requests"] == 80


@pytest.mark.parametrize("name", [
    "plan_0", "plan_1_repair", "continuation_3_plan", "continuation_3_plan_repair",
])
def test_forward_policy_cannot_reinterpret_paid_plans(tmp_path, name):
    state = _state(tmp_path)
    state.begin_call(name, {"synthetic_request": name})
    before = deepcopy(state.data)
    with pytest.raises((ValueError, RuntimeError)):
        active_finecut.enable_policy(state, True)
    assert state.data == before


def test_forward_policy_cannot_grant_an_additional_render(tmp_path):
    state = _state(tmp_path)
    (state.output / "render_0").mkdir()
    before = deepcopy(state.data)
    with pytest.raises((ValueError, RuntimeError)):
        active_finecut.enable_policy(state, True)
    assert state.data == before


def test_forward_policy_rejects_modified_saved_contract(tmp_path):
    state = _state(tmp_path)
    active_finecut.enable_policy(state, True)
    artifact_entries = next(iter(state.data["artifacts"].values()))
    entry = artifact_entries[-1]
    payload = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
    payload["unauthorized_contract_change"] = True
    write_json(entry["path"], payload)
    with pytest.raises((ValueError, RuntimeError)):
        active_finecut.enable_policy(state, False)


def _semantic_candidate(round_no, *, economy_pass=True, obligations_preserved=True):
    """A verified-selection fixture; no frame content is asserted by this helper."""
    review = {"theme_status": "pass", "editing_status": "pass", "continuity_status": "pass",
              "visual_narrative_status": "pass", "contradictions": [], "revision_requests": [],
              "fact_checks": [
                  {"claim_id": "source_claim", "status": "supported"},
                  {"claim_id": "new_slot_claim", "status": "supported"},
                  {"claim_id": "blind_claim", "status": "supported"},
              ]}
    blind = {"confusions": [], "text_dependency": "none", "evidence": [
        {"evidence_id": "blind_e1", "claim_id": "blind_claim", "kind": "visual_action",
         "start_s": 0, "end_s": 1, "observed_fact": "synthetic independent visible state",
         "basis_evidence_ids": []}]}
    candidate = {"round": round_no, "review": review, "blind": blind,
        "segment_checks": [{"segment_id": "n1", "claim_checks": [
            {"claim_id": "source_claim", "status": "supported"}]}],
        "expected_segment_ids": ["n1"],
        "refinement": {"obligation_coverage": [{"slot_id": "old_setup",
            "status": "preserved" if obligations_preserved else "unresolved"}]},
        "economy": {"economy_status": "pass" if economy_pass else "partial",
            "narrative_readability": "pass", "segment_checks": [
                {"segment_id": "n1", "status": "necessary" if economy_pass else "redundant"}]},
    }
    assert semantic_review_passes(review, blind, candidate["segment_checks"], expected_segment_ids=["n1"])
    return candidate


def _selection(round_no, *, limitations=None):
    return {"selected_round": round_no, "reason": "compare verified synthetic output evidence",
            "evidence_claim_ids": ["source_claim"], "limitations": list(limitations or [])}


def test_joint_selection_cannot_discard_an_available_semantic_and_economy_pass():
    candidates = [_semantic_candidate(0, economy_pass=False), _semantic_candidate(1)]
    with pytest.raises(ValueError):
        active_finecut.validate_selection(_selection(0, limitations=["selected candidate has redundancy"]), candidates)
    active_finecut.validate_selection(_selection(1), candidates)


@pytest.mark.parametrize("limited_dimension", ["economy", "obligation"])
def test_limited_active_candidate_selection_requires_its_own_limitations(limited_dimension):
    candidate = _semantic_candidate(0, economy_pass=limited_dimension != "economy",
                                    obligations_preserved=limited_dimension != "obligation")
    # The legacy semantic gate passes, so this exercises the additional active gate.
    with pytest.raises(ValueError):
        active_finecut.validate_selection(_selection(0), [candidate])
    choice = _selection(0, limitations=["active information requirement remains unresolved"])
    original_candidate, original_choice = deepcopy(candidate), deepcopy(choice)
    active_finecut.validate_selection(choice, [candidate])
    assert candidate == original_candidate
    assert choice == original_choice


def _output_manifest():
    return {"protocol": SEMANTIC_PROTOCOL, "observations": [], "segment_checks": [],
            "required_claims": [{"claim_id": "new_slot_claim", "kind": "slot_takeaway",
                                 "description": "new combined formulation", "owner_id": "new_combined"}],
            "limitations": ["fixture facts are not a human quality verdict"]}


def test_original_information_obligations_enter_actual_output_claims_without_rewriting_inputs():
    draft, refinement = _refinement()
    manifest = _output_manifest()
    originals = deepcopy((manifest, draft, refinement))
    bound = active_finecut.bind_draft_obligations(manifest, draft, refinement)
    assert bound is not manifest
    assert (manifest, draft, refinement) == originals
    original_claims = [c for c in bound["required_claims"] if c.get("origin") == "draft_obligation"]
    assert len(original_claims) == len(draft["slots"])
    by_slot = {c["owner_id"]: c for c in original_claims}
    coverage = {c["slot_id"]: c["segment_ids"] for c in refinement["obligation_coverage"]}
    for slot in draft["slots"]:
        claim = by_slot[slot["slot_id"]]
        assert claim["kind"] == "slot_takeaway"
        assert claim["description"] == slot["intended_takeaway"]
        assert claim["segment_ids"] == coverage[slot["slot_id"]]
    assert bound["required_claims"][0] == manifest["required_claims"][0]
    claim_ids = [c["claim_id"] for c in bound["required_claims"]]
    assert len(claim_ids) == len(set(claim_ids))
    assert active_finecut.bind_draft_obligations(manifest, draft, refinement) == bound


def test_same_original_obligation_claim_ids_are_stable_while_its_content_is_not_silently_changed():
    draft, refinement = _refinement()
    first = active_finecut.bind_draft_obligations(_output_manifest(), draft, refinement)
    second = active_finecut.bind_draft_obligations(_output_manifest(), deepcopy(draft), deepcopy(refinement))
    assert first == second
    changed_draft = deepcopy(draft)
    changed_draft["slots"][0]["intended_takeaway"] = "a different obligation in a different synthetic task"
    changed = active_finecut.bind_draft_obligations(_output_manifest(), changed_draft, refinement)
    old_claim = next(c for c in first["required_claims"] if c.get("origin") == "draft_obligation"
                     and c["owner_id"] == "old_setup")
    new_claim = next(c for c in changed["required_claims"] if c.get("origin") == "draft_obligation"
                     and c["owner_id"] == "old_setup")
    assert new_claim["description"] == changed_draft["slots"][0]["intended_takeaway"]
    assert new_claim["claim_id"] != old_claim["claim_id"]


def test_original_unsupported_obligation_blocks_success_even_when_all_new_slots_pass():
    draft, refinement = _refinement()
    manifest = active_finecut.bind_draft_obligations(_output_manifest(), draft, refinement)
    candidate = _semantic_candidate(0)
    original_claims = [c for c in manifest["required_claims"] if c.get("origin") == "draft_obligation"]
    candidate["review"]["fact_checks"].extend(
        {"claim_id": c["claim_id"], "status": "supported"} for c in original_claims)
    assert semantic_review_passes(candidate["review"], candidate["blind"], candidate["segment_checks"],
                                  expected_segment_ids=candidate["expected_segment_ids"])
    missing = next(c for c in candidate["review"]["fact_checks"]
                   if c["claim_id"] == original_claims[0]["claim_id"])
    missing.update(status="unsupported", limitations=["original required state is not visible"])
    assert next(c for c in candidate["review"]["fact_checks"]
                if c["claim_id"] == "new_slot_claim")["status"] == "supported"
    assert not semantic_review_passes(candidate["review"], candidate["blind"], candidate["segment_checks"],
                                      expected_segment_ids=candidate["expected_segment_ids"])
