from copy import deepcopy
import json

import pytest

from omni_story.library.contracts import (
    parse_model_json, validate_coarse, validate_editing_reference, validate_fine, validate_plan, validate_reference,
    validate_search,
)
from omni_story.library.state import LibraryState, LibraryStopped
from omni_story.library.prompts import plan_prompt


@pytest.fixture
def evidence():
    catalog = {"sources": [{"source_id": "movie_1", "sha256": "movie_hash", "duration_s": 200,
                             "audio_stream_index": 1}]}
    observation = {
        "window_id": "w_1", "source_id": "movie_1",
        "roles": [{"role_id": "hero", "identity_confirmed": True, "state": "red shirt",
                   "identity_evidence": "face and shirt visible"}],
        "events": [{"local_start_s": 2, "local_end_s": 8, "observed_fact": "hero lifts a box",
                    "role_ids": ["hero"]}],
        "usable_ranges": [{"local_in_s": 2, "local_out_s": 8, "event_indices": [0],
                           "role_ids": ["hero"], "continuity_notes": "box remains held"}],
        "uncertainties": [],
    }
    window = {"window_id": "w_1", "source_id": "movie_1", "source_sha256": "movie_hash",
              "source_start_s": 100, "source_end_s": 110, "status": "watched", "observation": observation}
    plan = {"reference_sha256": "ref_hash", "focus_role_id": "hero",
            "focus_role_bindings": [{"window_id": "w_1", "role_id": "hero",
                                     "identity_evidence": "observed face and clothes"}],
            "slots": [{"slot_id": "slot_1", "intended_takeaway": "visible action",
                       "segment_ids": ["seg_1"]}],
            "segments": [{"segment_id": "seg_1", "slot_id": "slot_1", "source_id": "movie_1",
                          "window_id": "w_1", "source_in_s": 102, "source_out_s": 108,
                          "speed": 1, "look": "none", "framing": "fit", "role_ids": ["hero"]}],
            "audio_mode": "reference", "source_gain_db": -12, "reference_gain_db": 0,
            "reference_audio": {"start_s": 0, "end_s": 10, "stream_index": 1, "loop": False},
            "width": 720, "height": 1280, "fps": 30, "limitations": []}
    return catalog, {"w_1": window}, plan


def test_exact_source_mapping_is_required(evidence):
    catalog, windows, plan = evidence
    assert validate_plan(plan, catalog, windows, "ref_hash", 20) is plan
    plan["segments"][0].update(source_in_s=2, source_out_s=8)
    with pytest.raises(ValueError, match="outside_watched_window"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


@pytest.mark.parametrize("mutation,error", [
    (lambda c, w, p: p.update(reference_sha256="other"), "reference_sha_changed"),
    (lambda c, w, p: w["w_1"].update(source_sha256="other"), "source_sha_changed"),
    (lambda c, w, p: w["w_1"].update(status="coarse"), "not_watched|not_fine_watched"),
    (lambda c, w, p: p["segments"][0].update(window_id="unseen"), "unknown_window"),
    (lambda c, w, p: p["segments"][0].update(source_id="unseen"), "unknown_source"),
    (lambda c, w, p: p["segments"][0].update(source_in_s=101), "not_supported_by_fine"),
    (lambda c, w, p: p["segments"][0].update(source_out_s=111), "outside_watched_window"),
    (lambda c, w, p: p["segments"][0].update(source_out_s=201), "out_of_bounds"),
    (lambda c, w, p: p["segments"][0].update(local_in_s=2), "mixed_time_domains"),
    (lambda c, w, p: p["segments"][0].update(role_ids=["villain"]), "unknown_or_duplicate_ref"),
    (lambda c, w, p: p["focus_role_bindings"][0].update(role_id="unseen"), "unknown_or_duplicate_ref"),
    (lambda c, w, p: p["reference_audio"].update(end_s=21), "out_of_bounds"),
])
def test_invalid_edit_provenance_is_rejected(evidence, mutation, error):
    catalog, windows, plan = deepcopy(evidence)
    mutation(catalog, windows, plan)
    with pytest.raises(ValueError, match=error):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_unconfirmed_character_cannot_support_a_cut(evidence):
    catalog, windows, plan = evidence
    windows["w_1"]["observation"]["roles"][0]["identity_confirmed"] = False
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


@pytest.mark.parametrize("binding,error", [
    (None, "list_required"),
    ([], "list_required"),
    ([{"window_id": "unknown", "role_id": "hero", "identity_evidence": "visible face"}], "unknown_window"),
    ([{"window_id": "w_1", "role_id": "hero", "identity_evidence": ""}], "text_required"),
])
def test_focus_requires_explicit_window_local_identity(evidence, binding, error):
    catalog, windows, plan = evidence
    plan["focus_role_bindings"] = binding
    with pytest.raises(ValueError, match=error):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_canonical_focus_does_not_implicitly_equal_local_role(evidence):
    catalog, windows, plan = evidence
    plan["focus_role_id"] = "stable_canonical_identity"
    assert validate_plan(plan, catalog, windows, "ref_hash", 20) is plan
    plan["focus_role_bindings"].append(deepcopy(plan["focus_role_bindings"][0]))
    with pytest.raises(ValueError, match="duplicate_focus_binding_window"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_selected_subrange_requires_role_event_evidence(evidence):
    catalog, windows, plan = evidence
    observed = windows["w_1"]["observation"]
    observed["usable_ranges"][0]["local_out_s"] = 9
    observed["events"][0]["local_end_s"] = 3
    plan["segments"][0].update(source_in_s=104, source_out_s=105)
    with pytest.raises(ValueError, match="segment_role_missing_overlapping_event_evidence"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_fine_range_requires_overlapping_event(evidence):
    _, windows, _ = evidence
    observation = windows["w_1"]["observation"]
    observation["usable_ranges"][0].update(local_in_s=8, local_out_s=9)
    with pytest.raises(ValueError, match="does_not_overlap"):
        validate_fine(observation, windows["w_1"])


def test_coarse_timestamps_cannot_be_edl(evidence):
    catalog, _, _ = evidence
    coarse = {"source_id": "movie_1", "coverage_s": [0, 200], "roles": [],
              "events": [{"timestamp_s": 100, "observed_fact": "a landscape", "role_ids": []}],
              "uncertainties": []}
    validate_coarse(coarse, catalog)
    coarse["events"][0]["source_in_s"] = 100
    with pytest.raises(ValueError, match="mixed_time_domains"):
        validate_coarse(coarse, catalog)


def test_search_bounds_and_reference_lock(evidence):
    catalog, _, _ = evidence
    search = {"reason": "need visible result", "windows": [{"source_id": "movie_1", "start_s": 100,
             "end_s": 131, "question": "what is the result", "role_ids": ["hero"]}]}
    validate_search(search, catalog)
    search["windows"][0]["end_s"] = 201
    with pytest.raises(ValueError, match="out_of_bounds"):
        validate_search(search, catalog)
    with pytest.raises(ValueError, match="sha_changed"):
        validate_reference({"reference_sha256": "wrong"}, "ref_hash", 20)


def test_parser_rejects_prose_or_multiple_values():
    assert parse_model_json('```json\n{"ok":true}\n```') == {"ok": True}
    with pytest.raises(json.JSONDecodeError):
        parse_model_json('{"ok":true} extra')
    with pytest.raises(ValueError, match="object_required"):
        parse_model_json('[]')


@pytest.mark.parametrize("speed", [0.5, 1, 2])
def test_renderer_supported_speed_boundaries(evidence, speed):
    catalog, windows, plan = evidence
    plan["segments"][0]["speed"] = speed
    validate_plan(plan, catalog, windows, "ref_hash", 20)


@pytest.mark.parametrize("speed", [0.25, 0.499, 2.001, 4])
def test_render_unsupported_speed_is_rejected_before_render(evidence, speed):
    catalog, windows, plan = evidence
    plan["segments"][0]["speed"] = speed
    with pytest.raises(ValueError, match="speed_out_of_range"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


@pytest.mark.parametrize("fps", [True, 1.0, 29.97, 0, 61])
def test_renderer_requires_integer_frame_rate(evidence, fps):
    catalog, windows, plan = evidence
    plan["fps"] = fps
    with pytest.raises(ValueError, match="fps_integer"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


@pytest.mark.parametrize("fps", [1, 30, 60])
def test_valid_integer_frame_rate_boundaries(evidence, fps):
    catalog, windows, plan = evidence
    plan["fps"] = fps
    validate_plan(plan, catalog, windows, "ref_hash", 20)


def _repeat_segments(plan, count, *, speed=1, end_s=108):
    base = plan["segments"][0]
    plan["segments"] = [{**base, "segment_id": f"seg_{index}", "speed": speed, "source_out_s": end_s}
                        for index in range(count)]
    plan["slots"][0]["segment_ids"] = [segment["segment_id"] for segment in plan["segments"]]


def test_segment_limit_accepts_32_but_rejects_33(evidence):
    catalog, windows, plan = evidence
    _repeat_segments(plan, 32, end_s=107)
    validate_plan(plan, catalog, windows, "ref_hash", 20)
    _repeat_segments(plan, 33, end_s=107)
    with pytest.raises(ValueError, match="segment_count_exceeds_32"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_duration_limit_accounts_for_speed_and_accepts_exact_180(evidence):
    catalog, windows, plan = evidence
    # Each observed six-second slice becomes twelve seconds at speed 0.5.
    _repeat_segments(plan, 15, speed=0.5)
    validate_plan(plan, catalog, windows, "ref_hash", 20)
    _repeat_segments(plan, 16, speed=0.5)
    with pytest.raises(ValueError, match="duration_exceeds_180"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_reference_audio_stream_is_bound_to_actual_metadata(evidence):
    catalog, windows, plan = evidence
    plan["reference_audio"]["stream_index"] = 3
    validate_plan(plan, catalog, windows, "ref_hash", 20, reference_audio_stream_index=3)
    with pytest.raises(ValueError, match="reference_audio_stream_mismatch"):
        validate_plan(plan, catalog, windows, "ref_hash", 20, reference_audio_stream_index=1)


@pytest.mark.parametrize("mode", ["reference", "mix"])
def test_no_reference_audio_rejects_modes_that_require_it(evidence, mode):
    catalog, windows, plan = evidence
    plan["audio_mode"] = mode
    with pytest.raises(ValueError, match="reference_has_no_audio"):
        validate_plan(plan, catalog, windows, "ref_hash", 20, reference_audio_stream_index=None)


@pytest.mark.parametrize("mode", ["source", "silent"])
def test_no_reference_audio_still_allows_source_and_silent(evidence, mode):
    catalog, windows, plan = evidence
    plan["audio_mode"] = mode
    validate_plan(plan, catalog, windows, "ref_hash", 20, reference_audio_stream_index=None)


def test_plan_prompt_example_uses_actual_reference_audio_metadata():
    context = {"reference_audio_stream_index": 3, "reference_duration_s": 4.5}
    prompt = plan_prompt(context)
    # The example is multiline; JSON's raw decoder reads it before the appended context.
    example, _ = json.JSONDecoder().raw_decode(prompt.split("输出：", 1)[1].strip())
    assert example["reference_audio"]["stream_index"] == 3
    assert example["reference_audio"]["end_s"] == 4.5
    assert "speed 0.5至2" in prompt and "最多32个segments" in prompt
    assert "fps必须为1至60的整数" in prompt
    no_audio, _ = json.JSONDecoder().raw_decode(plan_prompt({"reference_audio_stream_index": None})
                                               .split("输出：", 1)[1].strip())
    assert no_audio["audio_mode"] == "source"
    assert no_audio["reference_audio"] is None


def test_restart_preserves_request_budget_and_inputs(tmp_path):
    output = tmp_path / "run"
    state = LibraryState(output, {"reference_sha256": "ref_hash"}, max_requests=2)
    call, folder = state.begin_call("reference", {"media_sha": "ref_hash"})
    assert (folder / "request.json").exists()
    assert state.data["request_count"] == 1
    state.complete_call(call, {"raw": "response"}, usage={"prompt_tokens": 12, "completion_tokens": 5})
    restarted = LibraryState(output, {"reference_sha256": "ref_hash"}, max_requests=2)
    assert restarted.usage()["prompt_tokens"] == 12
    repair, _ = restarted.begin_call("repair", {"repair": True}, repair_of=call)
    restarted.complete_call(repair, {"valid": True})
    with pytest.raises(LibraryStopped, match="budget_exhausted"):
        restarted.begin_call("coarse", {"frame": 3})
    with pytest.raises(LibraryStopped, match="locked_inputs"):
        LibraryState(output, {"reference_sha256": "changed"}, max_requests=2)


@pytest.mark.parametrize("failure", ["pending", "uncertain"])
def test_unknown_submission_blocks_restart_replay(tmp_path, failure):
    output = tmp_path / "run"
    state = LibraryState(output, {"reference_sha256": "ref"})
    call, _ = state.begin_call("reference", {"input": "one"})
    if failure == "uncertain":
        state.fail_call(call, "connection lost after submission")
    restarted = LibraryState(output, {"reference_sha256": "ref"})
    assert restarted.usage()["uncertain_calls"] == [call["id"]]
    with pytest.raises(LibraryStopped, match="outcome_unknown"):
        restarted.begin_call("different", {"input": "another"})
    with pytest.raises(LibraryStopped, match="original_output"):
        LibraryState(tmp_path / "replacement", {"reference_sha256": "ref"})


def test_received_results_cannot_be_replayed_and_format_repair_is_bounded(tmp_path):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "ref"})
    call, _ = state.begin_call("reference", {"input": "one"})
    state.complete_call(call, {"raw": "invalid format"})
    with pytest.raises(LibraryStopped, match="already_recorded"):
        state.begin_call("again", {"input": "one"})
    repair, _ = state.begin_call("repair", {"input": "repair"}, repair_of=call)
    state.complete_call(repair, {"raw": "still invalid"})
    with pytest.raises(LibraryStopped, match="repair_limit"):
        state.begin_call("repair_again", {"input": "repair again"}, repair_of=call)
    with pytest.raises(LibraryStopped, match="received_original"):
        state.begin_call("repair_repair", {"input": "repair a repair"}, repair_of=repair)


def test_artifacts_keep_history_and_known_failure_is_recorded(tmp_path):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "ref"})
    first = state.set_artifact("plan", {"revision": 1})
    second = state.set_artifact("plan", {"revision": 2})
    assert first != second and json.loads(first.read_text(encoding="utf-8"))["revision"] == 1
    call, _ = state.begin_call("video", {"input": "one"})
    state.fail_call(call, "HTTP 401 response recorded", uncertain=False)
    assert state.data["calls"][0]["status"] == "failed_known"
    assert state.usage()["uncertain_calls"] == []


def _scoped_request(media_sha, *, start=600, end=1200):
    return {"media_sha256": media_sha, "arguments": {"image_source": "fixture.jpg", "prompt": "observe"},
            "observation_scope": {"kind": "sparse_contact_sheet", "source_sha256": "a" * 64,
                                  "source_start_s": start, "source_end_s": end}}


def _unknown_task(tmp_path):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "ref_hash"}, max_requests=3)
    request = _scoped_request("lost_image_hash")
    call, folder = state.begin_call("old_coarse", request)
    state.fail_call(call, "no HTTP response after POST", uncertain=True)
    return state, call, folder, request


def test_independent_continuation_preserves_unknown_budget_and_history(tmp_path):
    state, lost, folder, _ = _unknown_task(tmp_path)
    original_record = deepcopy(state.data["calls"][0])
    original_failure = (folder / "failure.json").read_bytes()
    original_input_lock = deepcopy(state.data["input_lock"])
    state.enable_independent_continuation()
    state.enable_independent_continuation()
    assert len(state.data["artifacts"]["continuation_policy"]) == 1
    fresh, _ = state.begin_call("overview", _scoped_request("different_image_hash", start=0, end=5400))
    state.complete_call(fresh, {"new": "observation"}, usage={"completion_tokens": 10})
    assert state.usage()["requests"] == 2
    assert state.usage()["uncertain_calls"] == [lost["id"]]
    assert state.data["calls"][0] == original_record
    assert state.data["input_lock"] == original_input_lock
    assert (folder / "failure.json").read_bytes() == original_failure
    restarted = LibraryState(state.output, {"reference_sha256": "ref_hash"}, max_requests=3)
    assert restarted.data["continuation_policy"] == "independent_media_no_unknown_replay_v1"
    final, _ = restarted.begin_call("another", _scoped_request("third_hash", start=3000, end=3600))
    restarted.complete_call(final, {"new": "other observation"})
    with pytest.raises(LibraryStopped, match="budget_exhausted"):
        restarted.begin_call("extra", _scoped_request("fourth_hash", start=4200, end=4800))
    with pytest.raises(LibraryStopped, match="original_output"):
        LibraryState(tmp_path / "new_run", {"reference_sha256": "ref_hash"}, max_requests=3)


def test_independent_continuation_is_opt_in(tmp_path):
    state, _, _, _ = _unknown_task(tmp_path)
    assert not state.data.get("continuation_policy")
    with pytest.raises(LibraryStopped, match="outcome_unknown"):
        state.begin_call("new_work", _scoped_request("different_image_hash", start=0, end=5400))
    assert state.usage()["requests"] == 1


@pytest.mark.parametrize("new_request", [
    _scoped_request("lost_image_hash", start=0, end=5400),
    _scoped_request("reencoded_hash"),
    _scoped_request("lost_image_hash"),
])
def test_unknown_media_hash_scope_and_original_request_cannot_be_replayed(tmp_path, new_request):
    state, _, _, _ = _unknown_task(tmp_path)
    state.enable_independent_continuation()
    with pytest.raises(LibraryStopped, match="must_not_be_resubmitted"):
        state.begin_call("retry_disguised_as_new", new_request)
    assert state.usage()["requests"] == 1


def test_modified_unknown_request_is_detected(tmp_path):
    state, _, folder, original = _unknown_task(tmp_path)
    state.enable_independent_continuation()
    altered = deepcopy(original)
    altered["arguments"]["prompt"] = "changed recorded prompt"
    (folder / "request.json").write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(LibraryStopped, match="unknown_original_request_modified"):
        state.begin_call("new", _scoped_request("different_hash", start=0, end=5400))
    assert state.usage()["requests"] == 1


def test_unknown_request_cannot_be_format_repaired_even_with_different_media(tmp_path):
    state, call, _, _ = _unknown_task(tmp_path)
    state.enable_independent_continuation()
    with pytest.raises(LibraryStopped, match="received_original_response"):
        state.begin_call("repair", _scoped_request("different_hash", start=0, end=5400), repair_of=call)
    assert state.usage()["requests"] == 1


def test_independent_policy_does_not_bypass_a_pending_submission(tmp_path):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "ref_hash"})
    call, _ = state.begin_call("pending", _scoped_request("pending_hash"))
    state.enable_independent_continuation()
    with pytest.raises(LibraryStopped, match="outcome_unknown"):
        state.begin_call("different", _scoped_request("different_hash", start=0, end=5400))
    assert state.usage()["uncertain_calls"] == [call["id"]]
    assert state.usage()["requests"] == 1


def test_extra_purpose_field_cannot_change_same_unknown_content_scope(tmp_path):
    state, _, _, _ = _unknown_task(tmp_path)
    state.enable_independent_continuation()
    request = _scoped_request("reencoded_hash")
    request["observation_scope"]["purpose"] = "claim this is independent"
    with pytest.raises(LibraryStopped, match="must_not_be_resubmitted|media_scope"):
        state.begin_call("disguised", request)
    assert state.usage()["requests"] == 1


@pytest.mark.parametrize("scope", [{}, {"kind": "sparse_contact_sheet"}])
def test_independent_scope_must_contain_all_measured_fields(tmp_path, scope):
    state, _, _, _ = _unknown_task(tmp_path)
    state.enable_independent_continuation()
    request = _scoped_request("new_hash")
    request["observation_scope"] = scope
    with pytest.raises(LibraryStopped, match="media_scope"):
        state.begin_call("unbound", request)


@pytest.fixture
def editing_reference():
    reading = {
        "reference_sha256": "ref_hash", "theme": "effort earns recognition",
        "intended_takeaway": "the visible result disproves the initial dismissal",
        "visible_evidence": [{"start_s": 0, "end_s": 4, "observed_fact": "dismissal then result",
                              "supports": "the main takeaway"}],
        "editing_methods": [
            {"method": "adjacent contrast", "function": "show the reversal", "start_s": 0,
             "end_s": 4, "visual_evidence": "opposed images follow one another"},
            {"method": "final freeze", "function": "leave time to inspect the result", "start_s": 4,
             "end_s": 6, "visual_evidence": "the ending image is held"},
        ], "uncertainties": ["precise cut frames have not been measured"],
    }
    methods = {
        "reference_sha256": "ref_hash", "methods": [
            {"method_id": "contrast", "reference_method_index": 0, "form": "adjacent contrast",
             "function": "show the reversal", "source_start_s": 0, "source_end_s": 4,
             "evidence_type": "model_estimate", "requires_audio": False,
             "material_requirements": ["two opposed states"],
             "verification_rule": "inspect the adjacent images and their contrast", "uncertainties": []},
            {"method_id": "freeze", "reference_method_index": 1, "form": "held ending image",
             "function": "leave time to inspect the visible result", "source_start_s": 4, "source_end_s": 6,
             "evidence_type": "model_estimate", "requires_audio": False,
             "material_requirements": ["an observed result image"],
             "verification_rule": "inspect the held image in the actual output", "uncertainties": []},
        ], "uncertainties": [],
    }
    return reading, methods


def test_editing_spec_is_append_only_and_covers_original_methods(editing_reference):
    reading, methods = editing_reference
    original = deepcopy(reading)
    assert validate_editing_reference(methods, "ref_hash", 20, reading) is methods
    assert reading == original


@pytest.mark.parametrize("mutation,error", [
    (lambda d: d.update(reference_sha256="other"), "sha_changed"),
    (lambda d: d.update(theme="a weaker meaning"), "theme_changed"),
    (lambda d: d.update(intended_takeaway="a new takeaway"), "intended_takeaway_changed"),
    (lambda d: d["methods"].pop(), "covered_exactly_once"),
    (lambda d: d["methods"][1].update(reference_method_index=0), "covered_exactly_once"),
    (lambda d: d["methods"][1].update(reference_method_index=True), "unknown_reference_method_index"),
    (lambda d: d["methods"][1].update(reference_method_index=2), "unknown_reference_method_index"),
    (lambda d: d["methods"][0].update(source_end_s=21), "out_of_bounds"),
    (lambda d: d["methods"][0].update(evidence_type="local_measurement"), "model_estimate_required"),
    (lambda d: d["methods"][0].update(requires_audio="no"), "requires_audio_boolean_required"),
    (lambda d: d["methods"][0].update(material_requirements=[]), "list_required"),
])
def test_editing_spec_rejects_removed_methods_changed_meaning_and_measurement_claims(
        editing_reference, mutation, error):
    reading, methods = editing_reference
    mutation(methods)
    with pytest.raises(ValueError, match=error):
        validate_editing_reference(methods, "ref_hash", 20, reading)


def _editing_bindings(plan, methods):
    plan["editing_bindings"] = [
        {"method_id": method["method_id"], "status": "planned", "segment_ids": ["seg_1"],
         "intended_relation": "the selected material may express this relation",
         "operation": "use the explicitly planned segment operation",
         "verification": "inspect the rendered material", "limitations": []}
        for method in methods["methods"]
    ]


def test_strict_plan_retains_legacy_compatibility_and_requires_each_method(evidence, editing_reference):
    catalog, windows, plan = evidence
    _, methods = editing_reference
    validate_plan(plan, catalog, windows, "ref_hash", 20)
    with pytest.raises(ValueError, match="editing_bindings:list_required"):
        validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods)
    _editing_bindings(plan, methods)
    assert validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods) is plan


@pytest.mark.parametrize("mutation,error", [
    (lambda p: p["editing_bindings"].pop(), "bound_exactly_once"),
    (lambda p: p["editing_bindings"][0].update(method_id="other"), "bound_exactly_once"),
    (lambda p: p["editing_bindings"][0].update(status="pass"), "editing_binding_status"),
    (lambda p: p["editing_bindings"][0].update(segment_ids=[]), "list_required"),
    (lambda p: p["editing_bindings"][0].update(segment_ids=["unseen"]), "unknown_or_duplicate_ref"),
    (lambda p: p["editing_bindings"][0].update(status="unavailable", segment_ids=[]), "list_required"),
])
def test_strict_plan_cannot_skip_methods_or_claim_they_already_passed(
        evidence, editing_reference, mutation, error):
    catalog, windows, plan = evidence
    _, methods = editing_reference
    _editing_bindings(plan, methods)
    mutation(plan)
    with pytest.raises(ValueError, match=error):
        validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods)


@pytest.mark.parametrize("status", ["unavailable", "unverifiable"])
def test_missing_method_can_be_recorded_with_explicit_limitation(evidence, editing_reference, status):
    catalog, windows, plan = evidence
    _, methods = editing_reference
    _editing_bindings(plan, methods)
    plan["editing_bindings"][0].update(status=status, segment_ids=[], limitations=["evidence is missing"])
    validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods)


def test_method_binding_sequence_follows_actual_edl_order(evidence, editing_reference):
    catalog, windows, plan = evidence
    _, methods = editing_reference
    _repeat_segments(plan, 2)
    _editing_bindings(plan, methods)
    for binding in plan["editing_bindings"]:
        binding["segment_ids"] = ["seg_0", "seg_1"]
    validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods)
    plan["editing_bindings"][0]["segment_ids"].reverse()
    with pytest.raises(ValueError, match="not_in_edl_order"):
        validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods)


def _caption(segment):
    segment["caption"] = {
        "text": "Observed\nresult", "start_s": 0, "end_s": 6, "position": "bottom", "font_size": 32,
        "evidence": [{"window_id": "w_1", "event_indices": [0]}],
    }
    return segment["caption"]


def test_caption_can_use_freeze_output_time_with_source_event_provenance(evidence):
    catalog, windows, plan = evidence
    segment = plan["segments"][0]
    segment.update(speed=2, freeze_tail_s=2)
    caption = _caption(segment)
    caption.update(start_s=3, end_s=5)
    assert validate_plan(plan, catalog, windows, "ref_hash", 20) is plan


@pytest.mark.parametrize("mutation,error", [
    (lambda s, c: s.update(freeze_tail_s=-1), "finite_number_required"),
    (lambda s, c: s.update(freeze_tail_s=10.001), "freeze_tail_exceeds"),
    (lambda s, c: s.update(freeze_tail_s=True), "finite_number_required"),
    (lambda s, c: c.update(end_s=6.1), "out_of_bounds"),
    (lambda s, c: c.update(font_size=True), "font_size_integer"),
    (lambda s, c: c.update(font_size=121), "font_size_integer"),
    (lambda s, c: c.update(position="left"), "caption_position"),
    (lambda s, c: c.update(text="x" * 301), "exceeds_300"),
    (lambda s, c: c.update(text="one\r\ntwo"), "control_character"),
    (lambda s, c: c.update(text="one\0two"), "control_character"),
    (lambda s, c: c.update(text="one\x7ftwo"), "control_character"),
    (lambda s, c: c.update(position=[]), "caption_position"),
    (lambda s, c: c.update(font_file="arbitrary-file.ttf"), "unsupported_caption_field"),
    (lambda s, c: c.update(evidence=[]), "list_required"),
    (lambda s, c: c["evidence"][0].update(window_id="unseen"), "window_mismatch"),
    (lambda s, c: c["evidence"][0].update(event_indices=[True]), "unknown_event_index"),
    (lambda s, c: c["evidence"][0].update(event_indices=[1]), "unknown_event_index"),
    (lambda s, c: c["evidence"][0].update(event_indices=[0, 0]), "duplicate_event_index"),
])
def test_caption_and_freeze_cannot_escape_output_time_or_observation_provenance(evidence, mutation, error):
    catalog, windows, plan = evidence
    segment = plan["segments"][0]
    caption = _caption(segment)
    mutation(segment, caption)
    with pytest.raises(ValueError, match=error):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_caption_cannot_cite_an_observed_event_outside_selected_source_slice(evidence):
    catalog, windows, plan = evidence
    observation = windows["w_1"]["observation"]
    observation["events"].append({"local_start_s": 8, "local_end_s": 10,
                                  "observed_fact": "hero leaves later", "role_ids": ["hero"]})
    caption = _caption(plan["segments"][0])
    caption["evidence"][0]["event_indices"] = [1]
    with pytest.raises(ValueError, match="event_outside_selected_range"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_freeze_time_counts_towards_total_render_budget(evidence):
    catalog, windows, plan = evidence
    _repeat_segments(plan, 15, speed=0.5)
    plan["segments"][-1]["freeze_tail_s"] = 0.01
    with pytest.raises(ValueError, match="duration_exceeds_180"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


@pytest.mark.parametrize("caption_start,caption_end,error", [
    (0, 6.0005, "caption_exceeds_nominal_duration"),
    (0, 5.61, "caption_exceeds_quantized_duration"),
    (0, 0.001, "caption_empty_quantized_interval"),
])
def test_caption_ranges_must_survive_renderer_frame_quantization(evidence, caption_start, caption_end, error):
    catalog, windows, plan = evidence
    segment = plan["segments"][0]
    if error == "caption_exceeds_quantized_duration":
        segment["source_out_s"] = 107.61
    caption = _caption(segment)
    caption.update(start_s=caption_start, end_s=caption_end)
    with pytest.raises(ValueError, match=error):
        validate_plan(plan, catalog, windows, "ref_hash", 20)


def test_total_frame_quantization_is_strict_for_v2_but_preserves_legacy_boundary(evidence, editing_reference):
    catalog, windows, plan = evidence
    _, methods = editing_reference
    # Nominal duration is 32 * 5.625 == 180 seconds. Each slice rounds to
    # 169 frames at 30 fps. Legacy v1 validation and rendering accepted this;
    # append-only v2 rules must not invalidate an already paid v1 plan cache.
    _repeat_segments(plan, 32, end_s=107.625)
    validate_plan(plan, catalog, windows, "ref_hash", 20)
    _editing_bindings(plan, methods)
    with pytest.raises(ValueError, match="quantized_duration_exceeds_180"):
        validate_plan(plan, catalog, windows, "ref_hash", 20, editing_reference=methods)


@pytest.mark.parametrize("operation", ["caption", "freeze"])
def test_actual_new_operations_activate_quantized_total_budget(evidence, operation):
    catalog, windows, plan = evidence
    _repeat_segments(plan, 32, end_s=107.625)
    if operation == "caption":
        _caption(plan["segments"][0])["end_s"] = 5.625
    else:
        plan["segments"][-1].update(source_out_s=107.60, freeze_tail_s=0.01)
    with pytest.raises(ValueError, match="quantized_duration_exceeds_180"):
        validate_plan(plan, catalog, windows, "ref_hash", 20)
