"""Forward reconstruction rejection/union tests; no model or real movie IO."""
from copy import deepcopy
import json

import pytest

from omni_story.library import fact_grounded_edit_plan as new
from omni_story.library.state import json_sha


@pytest.fixture
def fixture():
    source = {"source_id": "film", "sha256": "b"*64, "duration_s": 20,
              "path": "/synthetic/fixture.mp4", "audio_stream_index": None}
    catalog = {"sources": [source]}
    reference = {"sha256": "c"*64, "duration_s": 2, "audio_stream_index": None}
    window = {"window_id": "w1", "source_id": "film", "source_sha256": source["sha256"],
        "status": "watched", "source_start_s": 0, "source_end_s": 10,
        "observation": {"window_id": "w1", "source_id": "film",
            "roles": [{"role_id": role, "identity_confirmed": True, "state": "Synthetic figure.",
                "identity_evidence": "Synthetic geometry."} for role in ("A", "B")],
            "events": [{"local_start_s": 0, "local_end_s": 4, "role_ids": ["A"],
                "observed_fact": "Synthetic A visible."},
                {"local_start_s": 4, "local_end_s": 10, "role_ids": ["B"],
                 "observed_fact": "Synthetic B visible."}],
            "usable_ranges": [{"local_in_s": 0, "local_out_s": 10, "role_ids": ["A", "B"],
                "event_indices": [0, 1], "continuity_notes": "Synthetic range; exact facts still required."}],
            "uncertainties": []}}
    outline = {"slots": [{"slot_id": "slot1", "intended_takeaway": "Original synthetic message."}]}
    parent = {"baseline_id": "render_3", "sha256": "a"*64, "duration_s": 10,
        "provenance": [{"source_id": "film", "window_id": "w1", "source_in_s": 0,
            "source_out_s": 10, "speed": 1, "role_ids": ["A", "B"]}]}
    methods = {"reference_sha256": reference["sha256"], "methods": [{"method_id": "method1"}]}
    groups = [{"group_id": "g1", "slot_id": "slot1", "information": "Full original compound information.",
               "min_readable_s": 5}]
    segments = [{"segment_id": f"seg{i}", "slot_id": "slot1", "source_id": "film", "window_id": "w1",
        "source_in_s": a, "source_out_s": b, "speed": 1, "freeze_tail_s": 0, "role_ids": [role],
        "framing": "fit", "look": "none", "source_claims": [{"claim_id": f"claim{i}",
            "kind": "visual_state", "description": "Synthetic state, without future operations."}],
        "output_operation_claims": []} for i, (a, b, role) in enumerate(((0, 2.5, "A"), (5, 7.5, "B")))]
    plan = {"reference_sha256": reference["sha256"], "focus_role_id": "focus_A",
        "focus_role_bindings": [{"window_id": "w1", "role_id": "A", "identity_evidence": "Window memory."}],
        "slots": [{**outline["slots"][0], "segment_ids": ["seg0", "seg1"]}], "segments": segments,
        "editing_bindings": [{"method_id": "method1", "status": "planned", "segment_ids": ["seg0", "seg1"],
            "intended_relation": "Synthetic relation.", "operation": "Disjoint cuts.",
            "verification": "Actual output inspection required.", "limitations": []}],
        "fps": 30, "width": 96, "height": 128, "audio_mode": "silent",
        "source_gain_db": 0, "reference_gain_db": 0, "limitations": []}
    value = {"status": "planned", "baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
        "plan": plan, "essential_groups": [{**groups[0], "exposures": [
            {"segment_id": s["segment_id"], "source_start_s": s["source_in_s"], "source_end_s": s["source_out_s"],
             "continues_in_tail_frame": False, "source_claim_ids": [s["source_claims"][0]["claim_id"]]} for s in segments]}],
        "transition_checks": [{"from_segment_id": "seg0", "to_segment_id": "seg1",
            "relation": "Model-proposed synthetic connection.", "status": "planned"}],
        "limitations": [], "reason": "New model-owned disjoint cut reconstruction."}
    prior = {"plan": {"segments": deepcopy(parent["provenance"])}}
    return value, parent, outline, catalog, [window], reference, methods, groups, prior


def validate(data, fixture, **kwargs):
    _, parent, outline, catalog, windows, reference, methods, groups, prior = fixture
    return new.validate_reconstruction(data, parent, outline, catalog, windows, reference, methods, groups,
        prior_assembly=prior, **kwargs)


def test_original_group_minimum_is_shared_across_real_disjoint_microcuts(fixture):
    data = fixture[0]
    before = json_sha(fixture)
    assert new.proposed_group_exposure(data["plan"], data["essential_groups"][0]) == 5
    assert validate(data, fixture) is data
    assert json_sha(fixture) == before
    assert "quality_pass" not in data
    # Each 2.5-second cut remains below 5. The same obligation is not multiplied.
    assert all(s["source_out_s"]-s["source_in_s"] < 5 for s in data["plan"]["segments"])


def test_overlapping_exposures_do_not_count_twice(fixture):
    data = fixture[0]
    group = data["essential_groups"][0]
    group["exposures"].append(deepcopy(group["exposures"][0]))
    assert new.proposed_group_exposure(data["plan"], group) == 5
    group["exposures"] = [group["exposures"][0], group["exposures"][2]]
    assert new.proposed_group_exposure(data["plan"], group) == 2.5
    with pytest.raises(ValueError, match="shared_information_exposure_below_original_minimum"):
        validate(data, fixture)


def test_model_role_subset_passes_but_inherited_whole_parent_roles_fail(fixture):
    data = fixture[0]
    assert validate(data, fixture)
    data["plan"]["segments"][0]["role_ids"] = ["A", "B"]
    with pytest.raises(ValueError, match="segment_role_missing_overlapping_event"):
        validate(data, fixture)


@pytest.mark.parametrize("mutation,error", [
    ("min", "information_or_minimum"), ("information", "information_or_minimum"),
    ("missing_group", "essential_groups"), ("duplicate_group", "duplicate_id"),
    ("slot_takeaway", "original_slot_obligation"), ("parent_sha", "parent_binding"),
    ("source_sha", "source_sha_changed"), ("outside_window", "outside_watched_window"),
    ("cross_usable", "range_not_supported"), ("source_claim_kind", "source_claim_kind"),
    ("speed", "retiming_needs_output_claim"), ("hold", "hold_needs_output_claim"),
    ("fake_claim", "unknown_or_duplicate_ref"), ("exposure_outside", "exposure_outside_slice"),
    ("earlier_hold", "hold_cannot_expose_earlier_information"),
    ("missing_transition", "adjacent_transitions"), ("global_claim_duplicate", "duplicate_global_claim_id")])
def test_reconstruction_preserves_obligations_and_rejects_fabricated_bindings(fixture, mutation, error):
    data = fixture[0]
    segment = data["plan"]["segments"][0]
    group = data["essential_groups"][0]
    if mutation == "min":
        group["min_readable_s"] = 4
    elif mutation == "information":
        group["information"] = "Changed information."
    elif mutation == "missing_group":
        data["essential_groups"] = []
    elif mutation == "duplicate_group":
        data["essential_groups"].append(deepcopy(group))
    elif mutation == "slot_takeaway":
        data["plan"]["slots"][0]["intended_takeaway"] = "Changed message."
    elif mutation == "parent_sha":
        data["parent_sha256"] = "d"*64
    elif mutation == "source_sha":
        fixture[4][0]["source_sha256"] = "d"*64
    elif mutation == "outside_window":
        segment.update(source_in_s=10, source_out_s=12)
    elif mutation == "cross_usable":
        fixture[4][0]["observation"]["usable_ranges"][0].update(local_out_s=2, role_ids=["A"], event_indices=[0])
    elif mutation == "source_claim_kind":
        segment["source_claims"][0]["kind"] = "future_slowmotion"
    elif mutation == "speed":
        segment["speed"] = .5
    elif mutation == "hold":
        segment["freeze_tail_s"] = 1
    elif mutation == "fake_claim":
        group["exposures"][0]["source_claim_ids"] = ["unknown_source_fact"]
    elif mutation == "exposure_outside":
        group["exposures"][0]["source_end_s"] = 3
    elif mutation == "earlier_hold":
        group["exposures"][0].update(source_end_s=2, continues_in_tail_frame=True)
    elif mutation == "missing_transition":
        data["transition_checks"] = []
    elif mutation == "global_claim_duplicate":
        data["plan"]["segments"][1]["source_claims"][0]["claim_id"] = "claim0"
    with pytest.raises(ValueError, match=error):
        validate(data, fixture)


def test_tail_hold_adds_only_declared_last_frame_information(fixture):
    data = fixture[0]
    segment = data["plan"]["segments"][0]
    segment["freeze_tail_s"] = 2.5
    segment["output_operation_claims"] = [{"claim_id": "hold0", "kind": "tail_hold",
        "description": "Actual output tail hold.", "expected_value": 2.5}]
    group = data["essential_groups"][0]
    group["exposures"] = [group["exposures"][0]]
    group["exposures"][0]["continues_in_tail_frame"] = True
    assert new.proposed_group_exposure(data["plan"], group) == 5
    assert validate(data, fixture)
    segment["output_operation_claims"][0]["expected_value"] = 2.4
    with pytest.raises(ValueError, match="operation_value_changed"):
        validate(data, fixture)


def test_exhausted_input_requires_real_microcuts_not_split_full_envelope(fixture):
    data = fixture[0]
    blocked = [{"kind": "continuous_window", "source_sha256": "b"*64,
                "source_start_s": 0, "source_end_s": 10}]
    assert validate(data, fixture, blocked_scopes=blocked)
    data["plan"]["segments"][0].update(source_in_s=0, source_out_s=4)
    data["plan"]["segments"][1].update(source_in_s=4, source_out_s=10)
    data["plan"]["segments"][1]["freeze_tail_s"] = 2
    data["plan"]["segments"][1]["output_operation_claims"] = [{"claim_id": "hold1", "kind": "tail_hold",
        "description": "Model-selected hold.", "expected_value": 2}]
    with pytest.raises(ValueError, match="blocked_union_or_epsilon_replay"):
        validate(data, fixture, blocked_scopes=blocked)


def test_relabeling_same_failed_edl_is_not_substantive_reconstruction(fixture):
    data = fixture[0]
    fixture[-1]["plan"]["segments"] = deepcopy(data["plan"]["segments"])
    with pytest.raises(ValueError, match="only_relabeling_failed_plan_is_not_progress"):
        validate(data, fixture)


def test_splitting_continuous_playback_or_epsilon_is_not_progress():
    old = [{"source_id": "film", "source_in_s": 0, "source_out_s": 10, "speed": 1}]
    split = [{"source_id": "film", "source_in_s": 0, "source_out_s": 4, "speed": 1},
             {"source_id": "film", "source_in_s": 4, "source_out_s": 10, "speed": 1}]
    assert new._substantively_changed(split, old, 30) is False
    split[-1]["source_out_s"] -= 1/30
    assert new._substantively_changed(split, old, 30) is False
    split[-1]["source_out_s"] -= 2/30
    assert new._substantively_changed(split, old, 30) is True


def test_boolean_minimum_and_legacy_mixed_claims_cannot_enter_new_contract(fixture):
    data = fixture[0]
    fixture[-2][0]["min_readable_s"] = 1
    data["essential_groups"][0]["min_readable_s"] = True
    with pytest.raises(ValueError, match="finite_number_required"):
        validate(data, fixture)
    data["essential_groups"][0]["min_readable_s"] = 1
    data["plan"]["segments"][0]["visual_claims"] = []
    with pytest.raises(ValueError, match="legacy_mixed_claims"):
        validate(data, fixture)


def test_unavailable_preserves_terminal_status_without_a_plan(fixture):
    data = fixture[0]
    data.update(status="unavailable", plan=None, essential_groups=[], transition_checks=[],
        limitations=["Synthetic input cannot meet the original requirements."])
    assert validate(data, fixture) is data
    data["limitations"] = []
    with pytest.raises(ValueError, match="unavailable_has_no_plan"):
        validate(data, fixture)


def test_canonicalization_deduplicates_each_original_information_without_lowering_minima(fixture):
    outline = fixture[2]
    essential = {"information": "Original full text.", "min_readable_s": 5}
    proposal = {"slot_id": "slot1", "candidates": [{"candidate_id": "selected", "operations": [
        {"essential_intervals": [deepcopy(essential)]}, {"essential_intervals": [deepcopy(essential)]}]},
        {"candidate_id": "unselected", "operations": [{"essential_intervals": [
            {"information": "Different alternative.", "min_readable_s": 8}]}]}]}
    prior = {"selections": [{"slot_id": "slot1", "candidate_id": "selected"}]}
    before = deepcopy(proposal)
    groups = new.canonical_information_groups(outline, [proposal], prior_assembly=prior)
    assert len(groups) == 1 and groups[0]["information"] == essential["information"]
    assert groups[0]["min_readable_s"] == 5 and proposal == before
    proposal["candidates"][0]["operations"][1]["essential_intervals"][0]["min_readable_s"] = 6
    assert {g["min_readable_s"] for g in new.canonical_information_groups(outline, [proposal],
        prior_assembly=prior)} == {5, 6}
    prior["selections"][0]["candidate_id"] = "nonexistent"
    with pytest.raises(ValueError, match="original_selected_candidate_missing"):
        new.canonical_information_groups(outline, [proposal], prior_assembly=prior)


def test_prompt_is_new_fact_grounded_task_and_separates_source_output_checks(fixture):
    value, parent, outline, catalog, windows, reference, methods, groups, prior = fixture
    facts = [{"evidence_id": "neutral1", "description": "Recorded neutral evidence."}]
    prompt = new.reconstruction_prompt(parent, outline, catalog, windows, reference, methods, groups, facts,
        navigation={"navigation_only": True}, prior_assembly=prior)
    payload = json.loads(prompt[prompt.index('{"task":'):])
    assert payload["task"] == new.PROTOCOL
    assert payload["independent_source_facts"] == facts
    assert payload["original_information_groups"] == groups
    assert payload["parent_navigation_only"] == {"navigation_only": True}
    assert "source_claims" in prompt and "output_operation_claims" in prompt
    assert "不复制父长片段演员全集" in prompt and "不要求每镜重复整个group minimum" in prompt
    assert "unknown_reply" not in payload
