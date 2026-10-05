from copy import deepcopy
import math

import pytest

from omni_story.library.goal_caption_diagnostics import diagnostics
from omni_story.library.semantic_audit import validate_caption_temporal_evidence


def windows():
    return [{"window_id": "window_a", "source_start_s": 100,
        "observation": {"events": [
            {"local_start_s": 1, "local_end_s": 2},
            {"local_start_s": 4, "local_end_s": 5},
            {"local_start_s": 8, "local_end_s": 9}]}}]


def plan():
    return {"fps": 30, "segments": [{"segment_id": "segment_a", "window_id": "window_a",
        "source_in_s": 100, "source_out_s": 106, "speed": 1,
        "caption": {"start_s": 0, "end_s": 3,
            "evidence": [{"window_id": "window_a", "event_indices": [0, 1, 2]}]}}]}


def test_all_original_conflicts_reported_without_edit_suggestions_or_mutation():
    value, observed = plan(), windows()
    before = deepcopy((value, observed))
    conflicts = diagnostics(value, observed)
    assert [r["event_index"] for r in conflicts] == [1, 2]
    assert conflicts[0]["errors"] == ["semantic:caption_evidence_not_visible_during_display"]
    assert conflicts[1]["errors"] == ["plan:caption_event_outside_selected_range",
        "semantic:caption_evidence_not_visible_during_display"]
    assert conflicts[0]["selected_source_range"] == [100, 106]
    assert conflicts[0]["caption_output_local_range"] == [0, 3]
    assert conflicts[0]["caption_source_display_range"] == [100, 103]
    assert conflicts[0]["recorded_event_source_range"] == [104, 105]
    assert conflicts[1]["recorded_event_source_range"] == [108, 109]
    assert (value, observed) == before
    assert not any("replacement" in k or "suggest" in k for r in conflicts for k in r)
    conflicts[0]["selected_source_range"].append(999)
    assert (value, observed) == before


def test_list_and_keyed_window_contexts_match():
    observed = windows()
    assert diagnostics(plan(), observed) == diagnostics(plan(), {w["window_id"]: w for w in observed})


def test_reports_all_segments_and_bindings_not_only_first_failure():
    value = plan()
    second = deepcopy(value["segments"][0])
    second["segment_id"] = "segment_b"
    second["caption"]["evidence"] = [
        {"window_id": "window_a", "event_indices": [1]},
        {"window_id": "window_a", "event_indices": [2]}]
    value["segments"].append(second)
    assert [(r["segment_id"], r["event_index"]) for r in diagnostics(value, windows())] == [
        ("segment_a", 1), ("segment_a", 2), ("segment_b", 1), ("segment_b", 2)]


def test_boundary_touch_is_not_positive_overlap():
    value = plan()
    value["segments"][0]["caption"].update(start_s=2, end_s=4)
    conflicts = diagnostics(value, windows())
    assert [r["event_index"] for r in conflicts] == [0, 1, 2]


@pytest.mark.parametrize("speed", [.5, 1, 2])
def test_display_mapping_matches_existing_semantic_contract(speed):
    value = plan()
    value["segments"][0]["speed"] = speed
    conflicts = diagnostics(value, windows())
    for index in range(3):
        single = deepcopy(value)
        single["segments"][0]["caption"]["evidence"][0]["event_indices"] = [index]
        temporal_failure = any(r["event_index"] == index and
            "semantic:caption_evidence_not_visible_during_display" in r["errors"] for r in conflicts)
        if temporal_failure:
            with pytest.raises(ValueError, match="caption_evidence_not_visible_during_display"):
                validate_caption_temporal_evidence(single, windows())
        else:
            validate_caption_temporal_evidence(single, windows())


def test_hold_maps_to_final_real_frame_not_future_event():
    value = plan()
    value["segments"][0].update(source_out_s=105, freeze_tail_s=2)
    value["segments"][0]["caption"].update(start_s=5, end_s=7)
    conflicts = diagnostics(value, windows())
    assert [r["event_index"] for r in conflicts] == [0, 2]
    assert conflicts[0]["caption_source_display_range"] == pytest.approx([105 - 1 / 30, 105])


def test_valid_visible_binding_has_no_diagnostic():
    value = plan()
    value["segments"][0]["caption"]["evidence"][0]["event_indices"] = [0]
    assert diagnostics(value, windows()) == []


@pytest.mark.parametrize("mutation", [
    lambda p, w: p.pop("fps"),
    lambda p, w: p["segments"][0].pop("speed"),
    lambda p, w: p["segments"][0]["caption"].pop("start_s"),
    lambda p, w: w[0].pop("source_start_s"),
    lambda p, w: p["segments"][0].update(source_in_s=math.nan),
    lambda p, w: p["segments"][0].update(speed=True),
    lambda p, w: p["segments"][0]["caption"]["evidence"][0].update(event_indices=[True, 99, "1"]),
    lambda p, w: p["segments"][0]["caption"]["evidence"][0].update(window_id="unknown")])
def test_missing_or_malformed_fields_remain_structural_errors_not_invented_values(mutation):
    value, observed = plan(), windows()
    mutation(value, observed)
    before = deepcopy((value, observed))
    assert diagnostics(value, observed) == []
    # NaN cannot be compared by equality, but its original instance is retained.
    assert value.keys() == before[0].keys() and observed == before[1]


def test_malformed_roots_are_deferred_to_the_original_validator():
    assert diagnostics(None, windows()) == []
    assert diagnostics({"segments": [None]}, None) == []
