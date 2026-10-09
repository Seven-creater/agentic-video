"""Opt-in engineering limits with synthetic plans; no model or server calls."""
from copy import deepcopy

import pytest

from omni_story.library import render
from omni_story.library import visual_story_trial as trial


def source():
    return {"source_id": "parent", "path": "synthetic-parent.mp4", "sha256": "2" * 64,
            "duration_s": 400, "audio_stream_index": None}


def plan(duration=1, hold=0, count=1):
    row = {"segment_id": "clip", "source_id": "parent", "window_id": "full_parent_observed",
           "source_in_s": 0, "source_out_s": duration, "speed": 1, "freeze_tail_s": hold,
           "contribution_to": ["beat"], "reason": "synthetic observed contribution"}
    return {"segments": [deepcopy(row) for _ in range(count)]}


def auth(target):
    return {"parent": {"duration_s": 400}, "reference": {"duration_s": target},
            "target_duration_s": target, "target_tolerance_s": .05}


def test_over_180_second_hold_plan_requires_explicit_unbounded_duration():
    value = plan(181, 1)
    with pytest.raises(ValueError, match="output_exceeds_180_seconds"):
        render.compile_library_plan([source()], value, fps=30)
    compiled = render.compile_library_plan([source()], value, fps=30, max_duration_s=None)
    assert compiled["duration_s"] == 182 and compiled["total_frames"] == 5460
    assert compiled["segments"][0]["freeze_frames"] == 30
    assert render.compile_library_plan([source()], value, fps=30, max_duration_s=200) == compiled


def test_duration_cap_is_validation_only_and_keeps_identity_shape():
    value = plan(1, .4)
    original = render.compile_library_plan([source()], value)
    assert render.compile_library_plan([source()], value, max_duration_s=None) == original
    assert "max_duration_s" not in original
    assert trial.editing_fingerprint(value, source(), max_duration_s=None) == trial.editing_fingerprint(value, source())


def test_renderer_forwards_unbounded_cap_before_processing_media(tmp_path, monkeypatch):
    received = []
    original = render.compile_library_plan
    def capture(*args, **kwargs):
        received.append(kwargs["max_duration_s"])
        compiled = original(*args, **kwargs)
        assert compiled["duration_s"] == 182
        raise RuntimeError("stop_before_media")
    monkeypatch.setattr(render, "compile_library_plan", capture)
    with pytest.raises(RuntimeError, match="stop_before_media"):
        render.render_library_video([source()], plan(181, 1), tmp_path, max_duration_s=None)
    assert received == [None] and not tmp_path.joinpath("render_input.json").exists()


def test_more_than_24_segments_requires_explicit_unbounded_segment_count():
    value = plan(count=25)
    with pytest.raises(ValueError, match="segments_1_to_24"):
        trial.plan_check(value, auth(25), source(), [{"id": "beat"}])
    trial.plan_check(value, auth(25), source(), [{"id": "beat"}], max_segments=None)


def test_long_skill_plan_and_fingerprint_forward_duration_opt_in():
    value = plan(181, 1)
    with pytest.raises(ValueError, match="output_exceeds_180_seconds"):
        trial.plan_check(value, auth(182), source(), [{"id": "beat"}])
    trial.plan_check(value, auth(182), source(), [{"id": "beat"}], max_duration_s=None)
    with pytest.raises(ValueError, match="output_exceeds_180_seconds"):
        trial.editing_fingerprint(value, source())
    assert trial.editing_fingerprint(value, source(), max_duration_s=None)


def test_unbounded_segments_still_require_nonempty_model_choices():
    with pytest.raises(ValueError, match="segments_nonempty_list_required"):
        trial.plan_check({"segments": []}, auth(1), source(), [{"id": "beat"}], max_segments=None)


def inspections(count=5):
    return [{"target": "parent", "start_s": i, "end_s": i + 1, "step_s": .1,
             "question": "What changed in this synthetic interval?"} for i in range(count)]


def test_unbounded_inspections_remove_list_cap_with_explicit_true_only():
    value = inspections()
    with pytest.raises(ValueError, match="inspect_list_max_four"):
        trial.inspect_check(value, auth(20))
    trial.inspect_check(value, {**auth(20), "unbounded_inspections": True})
    with pytest.raises(ValueError, match="inspect_list_max_four"):
        trial.inspect_check(value, {**auth(20), "unbounded_inspections": "true"})


@pytest.mark.parametrize("end,step", [(7, .5), (5, .1), (401, 1)])
def test_unbounded_inspections_keep_per_call_precision_and_source_bounds(end, step):
    value = inspections()
    value[0].update(end_s=end, step_s=step)
    with pytest.raises(ValueError):
        trial.inspect_check(value, {**auth(20), "unbounded_inspections": True})


def test_unbounded_duration_still_rejects_out_of_source_range():
    with pytest.raises(ValueError, match="invalid_number:source_out_s"):
        render.compile_library_plan([source()], plan(401, 1), max_duration_s=None)
