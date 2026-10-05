"""Context projection removes plans, not evidence or the reference target."""
from copy import deepcopy
import json

import pytest

from omni_story.library import round_context
from omni_story.library.state import json_sha


def fact(index=0, *, outcome="Visible result."):
    observation = {"protocol": "visual_narrative_primary_v2", "segment_id": "seg_" + str(index),
        "source_sha256": "a" * 64, "source_in_s": index * 10, "source_out_s": index * 10 + 3,
        "characters": [{"character_id": "person", "appearance": "Observed person."}],
        "evidence": [{"evidence_id": "evidence", "kind": "visual_outcome", "local_start_s": 1,
                      "local_end_s": 2, "description": outcome, "character_ids": ["person"],
                      "basis_evidence_ids": []}], "uncertainties": ["Identity could be mistaken."]}
    return {"call_id": "call_" + str(index), "request_sha256": "b" * 64,
        "response_sha256": "c" * 64, "observation_sha256": json_sha(observation),
        "media_sha256": "d" * 64, "original_observation": observation,
        "fallible_independent_model_observation": True}


def fixture():
    facts = [fact(0), fact(1)]
    context = {"reference": {"sha256": "e" * 64, "theme": "The unchanged model interpretation."},
        "editing_reference": {"methods": [{"method_id": "method", "uncertainties": ["Unknown original speed."]}]},
        "reference_duration_s": 22, "reference_audio_stream_index": 0,
        "catalog": {"sources": [{"source_id": "film", "sha256": "a" * 64}]},
        "render_capabilities": {"max_segments": 32, "speed": [.5, 2]},
        "watched_windows": [{"window_id": "window_" + str(i), "source_id": "film", "source_sha256": "a" * 64,
            "source_start_s": 100 * i, "source_end_s": 100 * i + 90,
            "observation": {"roles": [{"role_id": "A", "appearance": "Original observation.", "identity_confirmed": True}],
                "events": [{"local_start_s": 0, "local_end_s": 5, "description": "Model event " + str(i)}],
                "usable_ranges": [{"local_in_s": 0, "local_out_s": 5, "role_ids": ["A"], "event_indices": [0]}],
                "uncertainties": ["A broad label can be wrong."]}} for i in range(16)],
        "source_range_navigation": {"rows": [{"source_start_s": 0, "source_end_s": 5}],
            "previous_known_failure_diagnostics": [{"error": "Range roles do not match."}]},
        "source_cut_navigation": {"window_ids": list(range(16)), "records": [{"candidate_table": [[1, .3], [2, .2]]}]},
        "known_exhausted_slice_inputs": [{"scope": {"source_sha256": "a" * 64, "source_start_s": 311,
            "source_end_s": 316}, "original_call": "old_69", "repair_call": "old_70"}],
        "craft_supplement": {"knowledge_sha256": "f" * 64, "inspection": {"uncertainties": ["Unknown method."]}},
        "goal_guide": "Generic guidance, not a story.", "coarse_index": [{"summary": "Fallible navigation."}],
        "research_refinement_evidence": {"round": 9, "knowledge_sha256": "f" * 64,
                                         "source_observations": deepcopy(facts)},
        "actual_feedback": {"previous_round": 8, "actual_render_exists": False, "actual_render": None,
            "previous_goal_result": {"status": "stopped_source_counterevidence", "new_renders": 0,
                "details": {"blockers": [{"claim_id": "old_action", "kind": "visual_action", "status": "unsupported",
                    "reason": "The action is missing.", "limitations": ["No direct outcome."]}]}},
            "records": [
                {"call_id": "draft", "stage": "active_8_draft", "request_sha256": "d" * 64,
                    "response_sha256": "e" * 64, "model_value": {"slots": [{"slot_id": "old_slot",
                    "intended_takeaway": "An unsupported old event."}], "segments": [], "duration": {"target_s": 45}}},
                {"call_id": "finecut", "stage": "active_8_finecut", "model_value": {"plan": {"slots": [],
                    "segments": []}, "decisions": [{"reason": "Old creative justification."}]}},
                {"call_id": "trim", "stage": "active_8_trim_abcdef0123456789", "model_value": {
                    "parent_segment_id": "seg_1", "kept_slices": [{"source_in_s": 0, "source_out_s": 3}],
                    "deletion_checks": [], "obligation_status": "unresolved", "limitations": ["A claim is not visible."],
                    "uncertainties": ["Identity unknown."]}},
                {"call_id": "call_0", "stage": "semantic_slice_8_abcdef0123456789",
                    "model_value": deepcopy(facts[0]["original_observation"])},
                {"call_id": "check", "stage": "semantic_claims_8_abcdef0123456789", "model_value": {
                    "claim_checks": [{"claim_id": "old_action", "status": "unsupported", "evidence_ids": [],
                                      "reason": "Not visible.", "limitations": ["Missing result."]}], "uncertainties": []}},
                {"call_id": "failed", "stage": "semantic_slice_8_0123456789abcdef", "protocol_failure": {
                    "error": "missing_field", "attempt": 1, "model_text": "Long malformed model output."},
                 "no_valid_typed_fact_or_verdict": True},
                {"artifact": "goal_cached_8_slice_key", "bound_record": {
                    "target_stage": "semantic_slice_8_key", "call_id": "call_1", "segment": {"segment_id": "seg_1"},
                    "value": deepcopy(facts[1]["original_observation"]), "proxy": {"codec_noise": "No need to duplicate."}}},
                {"artifact": "goal_cached_8_claims_key", "bound_record": {
                    "call_id": "checks", "observation": deepcopy(facts[1]["original_observation"]),
                    "claims": [{"claim_id": "c", "description": "Actual claim."}], "hypotheses": [{"role_id": "A"}],
                    "value": {"claim_checks": [{"claim_id": "c", "status": "partial", "limitations": ["Incomplete."]}]}}}
            ]}}
    return context, facts


def test_full_library_reference_navigation_knowledge_and_fact_values_remain_exact():
    context, facts = fixture()
    before = deepcopy((context, facts))
    result = round_context.project(context, facts)
    for key in context.keys() - {"actual_feedback", "research_refinement_evidence"}:
        assert result[key] == context[key]
    assert len(result["watched_windows"]) == 16
    assert result["research_refinement_evidence"] == context["research_refinement_evidence"]
    assert result["context_projection"]["policy"] == round_context.POLICY
    assert "are historical attempts" in result["context_projection"]["instruction"]
    assert "not obligations" in result["context_projection"]["instruction"]
    assert (context, facts) == before


def test_old_creative_plans_raw_failure_bodies_removed_with_hashes_and_blockers_retained():
    context, facts = fixture()
    result = round_context.project(context, facts)
    old_records, records = context["actual_feedback"]["records"], result["actual_feedback"]["records"]
    for i in (0, 1):
        assert "model_value" not in records[i]
        assert records[i]["archived_model_value_sha256"] == json_sha(old_records[i]["model_value"])
    assert records[2]["local_proposal_feedback"] == {k: old_records[2]["model_value"][k]
        for k in ("parent_segment_id", "obligation_status", "limitations", "uncertainties")}
    assert "kept_slices" not in records[2]["local_proposal_feedback"]
    assert records[5]["protocol_failure"] == {"error": "missing_field", "attempt": 1}
    assert records[5]["no_valid_typed_fact_or_verdict"] is True
    assert result["actual_feedback"]["previous_goal_result"] == context["actual_feedback"]["previous_goal_result"]


def test_duplicated_facts_become_hash_refs_but_checks_keep_non_supported_statuses():
    context, facts = fixture()
    result = round_context.project(context, facts)
    records = result["actual_feedback"]["records"]
    assert records[3]["model_value_reference"]["observation_sha256"] == facts[0]["observation_sha256"]
    assert "model_value" not in records[3]
    assert records[6]["bound_record"]["value_reference"]["observation_sha256"] == facts[1]["observation_sha256"]
    assert records[7]["bound_record"]["observation_reference"]["observation_sha256"] == facts[1]["observation_sha256"]
    assert records[4]["model_value"] == context["actual_feedback"]["records"][4]["model_value"]
    assert records[7]["bound_record"]["value"]["claim_checks"][0]["status"] == "partial"


def test_all_facts_available_to_new_draft_without_a_prior_model_plan_or_api_call():
    context, facts = fixture()
    del context["research_refinement_evidence"]
    result = round_context.project(context, facts)
    assert result["research_refinement_evidence"]["source_observations"] == facts
    assert result["context_projection"]["source_observation_count"] == 2
    assert "quality_pass" not in result and "model_goal_gate_passed" not in result


def test_distinct_conflicting_observations_are_not_merged_or_resolved():
    context, facts = fixture()
    conflict = fact(0, outcome="No visible result.")
    facts += [conflict, deepcopy(facts[0])]
    result = round_context.project(context, facts)
    assert result["research_refinement_evidence"]["source_observations"] == facts[:3]
    assert [row["original_observation"]["evidence"][0]["description"] for row in result["research_refinement_evidence"]["source_observations"]] == [
        "Visible result.", "Visible result.", "No visible result."]


def test_same_fact_distinct_call_provenance_is_not_silently_deleted():
    context, facts = fixture()
    duplicate_observation = deepcopy(facts[0])
    duplicate_observation["call_id"] = "another_bound_received_call"
    facts.append(duplicate_observation)
    result = round_context.project(context, facts)
    assert result["research_refinement_evidence"]["source_observations"] == facts


def test_unmatched_feedback_observation_is_kept_instead_of_an_unresolvable_reference():
    context, facts = fixture()
    independent = fact(2)["original_observation"]
    context["actual_feedback"]["records"].append({"stage": "semantic_slice_8_other", "model_value": independent})
    result = round_context.project(context, facts)
    assert result["actual_feedback"]["records"][-1]["model_value"] == independent


def test_missing_previously_bound_facts_cannot_be_silently_removed_from_shared_context():
    context, facts = fixture()
    with pytest.raises(ValueError, match="previous_bound_fact_omitted"):
        round_context.project(context, facts[:1])


def test_artifact_failure_model_text_is_omitted_recursively_without_changing_failure_evidence():
    context, facts = fixture()
    context["actual_feedback"]["records"].append({"actual_technical_failure": {
        "error": "Validation exhausted.", "no_automatic_paid_replay": True,
        "failure": {"model_text": "Very long invalid output.", "request_sha256": "a" * 64}}})
    result = round_context.project(context, facts)
    assert result["actual_feedback"]["records"][-1]["actual_technical_failure"] == {
        "error": "Validation exhausted.", "no_automatic_paid_replay": True,
        "failure": {"request_sha256": "a" * 64}}


@pytest.mark.parametrize("mutation,error", [
    (lambda c,f: c.update(reference=None), "reference_required"),
    (lambda c,f: c.update(watched_windows=[]), "complete_windows_required"),
    (lambda c,f: c.update(actual_feedback=[]), "feedback_object_required"),
    (lambda c,f: c["actual_feedback"].update(records="raw response"), "feedback_records_array_required"),
    (lambda c,f: f.append({"model_text": "Failed reply is not typed evidence."}), "typed_source_record_required"),
    (lambda c,f: f[0].update(observation_sha256="b" * 64), "observation_hash_changed"),
    (lambda c,f: c.update(research_refinement_evidence=[]), "research_evidence_object_required"),
])
def test_invalid_projection_inputs_fail_without_modifying_historical_records(mutation, error):
    context, facts = fixture()
    mutation(context, facts)
    before = deepcopy((context, facts))
    with pytest.raises(ValueError, match=error):
        round_context.project(context, facts)
    assert (context, facts) == before


def test_projection_results_have_no_mutable_aliases_to_original_evidence():
    context, facts = fixture()
    result = round_context.project(context, facts)
    result["watched_windows"][0]["observation"]["events"][0]["description"] = "Changed."
    result["research_refinement_evidence"]["source_observations"][0]["original_observation"]["evidence"][0]["description"] = "Changed."
    result["actual_feedback"]["previous_goal_result"]["details"]["blockers"][0]["status"] = "supported"
    assert context["watched_windows"][0]["observation"]["events"][0]["description"] == "Model event 0"
    assert facts[0]["original_observation"]["evidence"][0]["description"] == "Visible result."
    assert context["actual_feedback"]["previous_goal_result"]["details"]["blockers"][0]["status"] == "unsupported"


def test_size_report_counts_exact_json_utf8_bytes_and_never_claims_glm_token_compression():
    before, after = {"text": "原始正文" * 100}, {"text": "短文"}
    report = round_context.size_report(before, after)
    encoding = dict(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert report["before_chars"] == len(json.dumps(before, **encoding))
    assert report["after_utf8_bytes"] == len(json.dumps(after, **encoding).encode("utf-8"))
    assert report["removed_utf8_bytes"] == report["before_utf8_bytes"] - report["after_utf8_bytes"]
    assert report["glm_token_count"] is None
    assert report["utf8_reduction_fraction"] > 0


def test_size_report_honestly_reports_growth_when_evidence_is_added():
    report = round_context.size_report({}, {"facts": "New evidence."})
    assert report["removed_chars"] < 0 and report["removed_utf8_bytes"] < 0
