"""Synthetic interval feedback regressions; no live model or historical run I/O."""
from copy import deepcopy
import json

import pytest

from omni_story.library import semantic_audit as audit
from omni_story.library.pipeline import CodexMCP
from omni_story.library.state import LibraryState
from test_library_pipeline import task, _fake_bridge, _reply


SOURCE_SHA = "a" * 64
PROXY_SHA = "b" * 64
VIDEO_SHA = "c" * 64
INTERVAL_ERROR = "semantic/evidence:outside_observed_slice"
NUMBER_ERROR = "semantic/evidence:finite_number_required"
REPAIR_MARKER = "\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。"


def source_data(intervals=((0, 1), (2, 2), (3, 3), (4, 4), (0, 1)), duration=5):
    segment = {"segment_id": "synthetic_segment", "source_id": "synthetic_source",
               "source_in_s": 100, "source_out_s": 100 + duration}
    proxy = {"sha256": PROXY_SHA, "source_id": segment["source_id"],
             "source_sha256": SOURCE_SHA, "source_start_s": 100,
             "source_end_s": 100 + duration, "duration_s": duration}
    value = {"protocol": audit.SEMANTIC_PROTOCOL, **segment,
             "source_sha256": SOURCE_SHA, "proxy_sha256": PROXY_SHA,
             "observed_duration_s": duration, "characters": [], "uncertainties": [],
             "evidence": [{"evidence_id": f"e{i + 1}", "kind": "visual_action",
                           "local_start_s": start, "local_end_s": end,
                           "description": "Synthetic visible action; not a movie observation.",
                           "character_ids": [], "basis_evidence_ids": []}
                          for i, (start, end) in enumerate(intervals)]}
    return segment, proxy, value


def validate_source(value, segment, proxy):
    return audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy)


def diagnostic(value, segment, proxy, error=INTERVAL_ERROR):
    with pytest.raises(ValueError) as caught:
        validate_source(value, segment, proxy)
    assert str(caught.value) == error
    result = caught.value.diagnostics
    # Invalid numeric inputs must not make failure records invalid JSON.
    json.dumps(result, allow_nan=False)
    assert result["observed_duration_s"] == proxy["duration_s"]
    assert result["start_field"] == "local_start_s"
    assert result["end_field"] == "local_end_s"
    assert result["required_relation"] == "0 <= start < end <= observed_duration_s"
    assert result["end_tolerance_s"] == 0.001
    return result


@pytest.mark.parametrize("intervals,invalid_indices", [
    (((0, 1), (2, 2), (3, 3), (4, 4), (0, 1)), (1, 2, 3)),
    (((0, 0), (2, 2), (3, 3), (4, 4), (0, 0)), (0, 1, 2, 3, 4)),
])
def test_original_and_failed_repair_shapes_report_every_zero_duration_without_normalization(
        intervals, invalid_indices):
    segment, proxy, value = source_data(intervals)
    before = deepcopy((segment, proxy, value))
    result = diagnostic(value, segment, proxy)
    assert result["invalid_intervals"] == [
        {"evidence_id": f"e{i + 1}", "row_index": i, "start_s": intervals[i][0],
         "end_s": intervals[i][1], "problem": "zero_duration"}
        for i in invalid_indices]
    assert (segment, proxy, value) == before


def test_later_unrepresentable_integer_cannot_mask_the_original_interval_error():
    segment, proxy, value = source_data(((0, 0), (10 ** 400, 10 ** 400)))
    result = diagnostic(value, segment, proxy)
    assert [row["problem"] for row in result["invalid_intervals"]] == [
        "zero_duration", "invalid_time_value"]
    assert result["invalid_intervals"][1]["start_s"] == "<int:outside_finite_time_range>"


@pytest.mark.parametrize("start,end,problem,error", [
    (2, 2, "zero_duration", INTERVAL_ERROR),
    (3, 2, "reversed_interval", INTERVAL_ERROR),
    (0, 5.01, "outside_observed_slice", INTERVAL_ERROR),
    (6, 7, "outside_observed_slice", INTERVAL_ERROR),
    (-1, 1, "negative_time", NUMBER_ERROR),
    (0, -1, "negative_time", NUMBER_ERROR),
    (True, 1, "invalid_time_value", NUMBER_ERROR),
    (0, False, "invalid_time_value", NUMBER_ERROR),
    (None, 1, "invalid_time_value", NUMBER_ERROR),
    ("2", 3, "invalid_time_value", NUMBER_ERROR),
])
def test_failures_keep_original_verdict_and_distinguish_interval_defects(start, end, problem, error):
    segment, proxy, value = source_data(((start, end),))
    before = deepcopy((segment, proxy, value))
    result = diagnostic(value, segment, proxy, error)
    assert result["invalid_intervals"] == [{"evidence_id": "e1", "row_index": 0,
        "start_s": start, "end_s": end, "problem": problem}]
    assert (segment, proxy, value) == before


def test_first_failure_does_not_hide_other_invalid_rows_or_change_error_order():
    intervals = ((2, 2), (0, 5.2), (3, 2), (-1, 1), (True, 1), (0, 1))
    segment, proxy, value = source_data(intervals)
    result = diagnostic(value, segment, proxy)
    assert [row["problem"] for row in result["invalid_intervals"]] == [
        "zero_duration", "outside_observed_slice", "reversed_interval", "negative_time",
        "invalid_time_value"]
    assert [row["row_index"] for row in result["invalid_intervals"]] == list(range(5))
    assert value["evidence"][-1]["local_end_s"] == 1


@pytest.mark.parametrize("mutation,error", [
    (lambda value: value["evidence"][0].update(kind="invalid_kind", local_end_s=0),
     "semantic:unknown_evidence_kind"),
    (lambda value: value["evidence"][0].update(description=""),
     "semantic/evidence/fact:text_required"),
    (lambda value: value["evidence"][1].update(evidence_id="e1"),
     "semantic/evidence:duplicate_id"),
])
def test_interval_collection_does_not_replace_an_earlier_noninterval_validation(mutation, error):
    segment, proxy, value = source_data()
    mutation(value)
    before = deepcopy(value)
    with pytest.raises(ValueError) as caught:
        validate_source(value, segment, proxy)
    assert str(caught.value) == error
    assert not hasattr(caught.value, "diagnostics")
    assert value == before


@pytest.mark.parametrize("value,projection", [
    (float("nan"), "NaN"), (float("inf"), "Infinity"), (float("-inf"), "-Infinity")])
@pytest.mark.parametrize("field", ["local_start_s", "local_end_s"])
def test_nonfinite_values_are_string_projected_only_in_diagnostics(value, projection, field):
    segment, proxy, observed = source_data(((0, 1),))
    observed["evidence"][0][field] = value
    result = diagnostic(observed, segment, proxy, NUMBER_ERROR)
    row = result["invalid_intervals"][0]
    assert row["problem"] == "invalid_time_value"
    assert row["start_s" if field == "local_start_s" else "end_s"] == projection
    assert observed["evidence"][0][field] is value


def test_nonserializable_time_projection_does_not_use_object_text_or_mutate_input():
    class UnserializableTime:
        def __repr__(self):
            return "PRIVATE_CONTENT_MUST_NOT_APPEAR"

        __str__ = __repr__

    bad_time = UnserializableTime()
    segment, proxy, value = source_data(((bad_time, 1),))
    result = diagnostic(value, segment, proxy, NUMBER_ERROR)
    projected = result["invalid_intervals"][0]["start_s"]
    assert isinstance(projected, str) and "UnserializableTime" in projected
    assert "PRIVATE_CONTENT_MUST_NOT_APPEAR" not in json.dumps(result, allow_nan=False)
    assert value["evidence"][0]["local_start_s"] is bad_time


@pytest.mark.parametrize("duration,start,end,accepted", [
    (.05, 0, .000001, True),
    (.05, .01, .05, True),
    (.05, .049, .051, True),
    (.05, .01, .051001, False),
    (5, 4, 5.001, True),
    (5, 4, 5.001001, False),
])
def test_original_positive_subsecond_and_one_millisecond_tolerance_are_unchanged(
        duration, start, end, accepted):
    segment, proxy, value = source_data(((start, end),), duration)
    before = deepcopy(value)
    if accepted:
        assert validate_source(value, segment, proxy) is value
    else:
        result = diagnostic(value, segment, proxy)
        assert result["invalid_intervals"][0]["problem"] == "outside_observed_slice"
    assert value == before


def test_blind_evidence_diagnostics_use_output_fields_and_keep_every_invalid_interval():
    value = {"protocol": audit.SEMANTIC_PROTOCOL, "video_sha256": VIDEO_SHA,
             "observed_story": "Synthetic sequence.", "apparent_theme": "Unknown.",
             "main_characters": [], "confusions": [], "text_dependency": "none",
             "evidence": [{"evidence_id": f"blind_e{i}", "claim_id": f"blind_c{i}",
                           "kind": "visual_action", "start_s": start, "end_s": end,
                           "observed_fact": "Synthetic visible change.", "basis_evidence_ids": []}
                          for i, (start, end) in enumerate(((1, 1), (0, 6)), 1)]}
    before = deepcopy(value)
    with pytest.raises(ValueError, match=INTERVAL_ERROR) as caught:
        audit.validate_visual_blind(value, 5, VIDEO_SHA)
    result = caught.value.diagnostics
    assert result["start_field"] == "start_s" and result["end_field"] == "end_s"
    assert result["observed_duration_s"] == 5
    assert result["invalid_intervals"] == [
        {"evidence_id": "blind_e1", "row_index": 0, "start_s": 1, "end_s": 1,
         "problem": "zero_duration"},
        {"evidence_id": "blind_e2", "row_index": 1, "start_s": 0, "end_s": 6,
         "problem": "outside_observed_slice"}]
    json.dumps(result, allow_nan=False)
    assert value == before


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_sole_repair_gets_all_intervals_and_restart_cannot_gain_third_request_or_rewrite_calls(task):
    state, media = task
    segment, proxy, original = source_data()
    _, _, failed_repair = source_data(((0, 0), (2, 2), (3, 3), (4, 4), (0, 0)))
    expected_original = diagnostic(original, segment, proxy)
    expected_repair = diagnostic(failed_repair, segment, proxy)
    validator = lambda value: validate_source(value, segment, proxy)
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply(json.dumps(original)), _reply(json.dumps(failed_repair))]) as requests:
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted:synthetic_slice") as caught:
            client.call("synthetic_slice", "Generic independent fact reading.", media, validator)
        assert len(requests) == 2
        feedback = json.loads(requests[1]["arguments"]["prompt"].split(REPAIR_MARKER, 1)[1])
        assert feedback["validation_error"] == INTERVAL_ERROR
        assert feedback["validation_diagnostics"] == expected_original
        assert json.loads(feedback["previous_response"]) == original
    assert caught.value.diagnostics == expected_repair
    assert caught.value.__cause__.diagnostics == expected_repair
    assert len(state.data["calls"]) == state.usage()["requests"] == 2
    for call, expected in zip(state.data["calls"], (expected_original, expected_repair)):
        folder = state.output / "calls" / call["id"]
        assert read(folder / "protocol_failure.json")["validation_diagnostics"] == expected
        assert not (folder / "parsed.json").exists()
    assert state.data["calls"][1]["repair_of"] == state.data["calls"][0]["id"]
    protected = {path: path.read_bytes() for path in (state.output / "calls").glob("*/*") if path.is_file()}
    queue_before = {path: path.read_bytes() for path in client.queue.glob("*") if path.is_file()}
    resumed = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    with pytest.raises(ValueError, match="model_protocol_repair_exhausted:synthetic_slice") as resumed_error:
        CodexMCP(resumed, timeout_s=.1).call("synthetic_slice", "Changed forward prompt.", media, validator)
    assert resumed_error.value.diagnostics == expected_repair
    assert resumed.usage()["requests"] == 2
    assert all(path.read_bytes() == content for path, content in protected.items())
    assert {path: path.read_bytes() for path in client.queue.glob("*") if path.is_file()} == queue_before
