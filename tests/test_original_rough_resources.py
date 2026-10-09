"""Historical generic prompts and renderer must survive packaging unchanged."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library.resources import original_rough_v1 as original
from omni_story.library.resources import historical_selected_review_v1 as selected_review


REFERENCE_SHA = "2f95e24edd2cf4b79cc1f40f7e202174c53a6abcf084e728bb49e3ca92938a17"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_runtime_prefixes_match_recorded_historical_request_hashes():
    origins = original.provenance()["historical_request_origins"]
    context = {"reference_duration_s": 21.933333, "reference_audio_stream_index": 0}
    suffix = json.dumps(context, ensure_ascii=False)
    prefixes = {
        "reference": original.reference_prompt(REFERENCE_SHA, 21.933333),
        "zoom_search": original.zoom_search_prompt(context)[:-len(suffix)],
        "search": original.search_prompt(context)[:-len(suffix)],
        "plan": original.plan_prompt(context)[:-len(suffix)],
        "blind": original.blind_prompt(77.366667),
        "review": original.review_prompt(context)[:-len(suffix)],
        "select": original.select_prompt([])[:-2],
    }
    payload = json.dumps({"window": {}, "context": context}, ensure_ascii=False)
    prefixes["fine"] = original.fine_prompt({}, context)[:-len(payload)]
    payload = json.dumps({"source_id": "synthetic", "coverage_s": [0, 10],
                          "frame_times": [], "context": context}, ensure_ascii=False)
    prompt = original.coarse_prompt("synthetic", [0, 10], [], context)
    marker = prompt.index(payload)
    prefixes["coarse"] = prompt[:marker]
    assert sha(prompt[marker + len(payload):]) == origins["coarse"]["generic_suffix_sha256"]
    for name, prefix in prefixes.items():
        assert sha(prefix) == origins[name]["generic_prefix_sha256"], name


def test_archive_is_original_renderer_and_no_worked_movie_answer_is_packaged():
    proof = original.provenance()
    assert proof["archives"]["archived_render.py"]["sha256"] == (
        "e3d8e1f140c0dfcbe3455fa0622ae5a23db44df5705aa0594ea4f57fbe0bc1bf")
    assert original.render_library_video.__module__.endswith("original_rough_v1.archived_render")
    assert proof["historical_context_and_model_answers_included"] is False
    templates = (Path(original.__file__).parent / "templates.json").read_text(encoding="utf-8")
    for answer in ("功夫熊猫", "role_po", "1273.85", "2219.50", "4770.30",
                   "77.366667", "21.933333", "无臂女性", "神龙大侠", "watched_windows"):
        assert answer not in templates


def test_stage_specific_history_and_task_parameters_remain_distinct():
    assert "本任务是异源素材的主旨迁移" not in original.BASE
    assert "本任务是异源素材的主旨迁移" in original.select_prompt([])
    prompt = original.search_prompt({"max_windows_this_round": 8})
    assert "一次请求不超过12个" in prompt
    assert '"max_windows_this_round": 8' in prompt
    assert "77" not in original.blind_prompt(36.5)
    assert "36.5秒" in original.blind_prompt(36.5)
    for index, expected in ((0, '"stream_index": 0'), (2, '"stream_index": 2'),
                            (None, '"reference_audio":null'), (True, '"reference_audio":null')):
        prompt = original.plan_prompt({"reference_audio_stream_index": index,
                                       "reference_duration_s": 6.5})
        assert expected in prompt
        if type(index) is int:
            assert '"end_s": 6.5' in prompt
        else:
            assert '"audio_mode":"source"' in prompt


def test_round_zero_keeps_original_helpers_and_only_exposes_phase_stages():
    phase = original.for_round(0)
    assert vars(phase) == {
        "fine_prompt": original.fine_prompt,
        "plan_prompt": original.plan_prompt,
        "review_prompt": original.review_prompt,
    }
    context = {"reference_duration_s": 6.5, "reference_audio_stream_index": 2,
               "question": "本任务是异源素材的主旨迁移", "round_no": 1}
    assert phase.fine_prompt({}, context) == original.fine_prompt({}, context)
    assert phase.plan_prompt(context) == original.plan_prompt(context)
    assert phase.review_prompt(context) == original.review_prompt(context)


def test_second_round_prefixes_match_actual029039041_and043():
    phase = original.for_round(1)
    assert set(vars(phase)) == {"fine_prompt", "plan_prompt", "review_prompt"}
    assert phase.review_prompt is selected_review.review_prompt
    context = {"reference_duration_s": 21.933333, "reference_audio_stream_index": 0}
    suffix = json.dumps(context, ensure_ascii=False)
    fine_suffix = json.dumps({"window": {}, "context": context}, ensure_ascii=False)
    assert sha(phase.fine_prompt({}, context)[:-len(fine_suffix)]) == (
        "077041caca45b00197e78464cf7e3eddff7593ccd86d3125bf23bd49e887eb55")
    assert sha(phase.plan_prompt(context)[:-len(suffix)]) == (
        "71b272222bc3be93f58883c56737d30cf25219e8f8b95f7b7495225d5746ba15")
    assert sha(phase.review_prompt(context)[:-len(suffix)]) == (
        "2acbf827ddfa126ca31def2fbcb59257bd149ab8f1f1fb2d2c446c3ba9ce4ac7")
    # Phase selection never changes default search or blind instructions.
    assert "本任务是异源素材的主旨迁移" not in original.search_prompt({})
    assert "本任务是异源素材的主旨迁移" not in original.blind_prompt(36.5)


@pytest.mark.parametrize("round_no", [-1, 2, None, True, False, 1.0, "1"])
def test_round_selection_requires_explicit_original_round(round_no):
    with pytest.raises(ValueError, match="historical_rough_round_required"):
        original.for_round(round_no)


def test_phase_provenance_binds_generic_insertion_and_original_requests():
    proof = original.phase_provenance()
    assert original.provenance()["phase_clarification"] == proof
    assert proof["resource_sha256"] == (
        "3ea096bc1512b63a985bd0712b9da38ca5a14090bb8bdcf14ff29de0c0b6de55")
    assert proof["base_insertion_characters"] == 188
    assert proof["base_insertion_utf8_bytes"] == 490
    assert proof["base_insertion_sha256"] == (
        "a16b643aaa2ef3613f19c16bfd14a05c4a29d982477c7ccfb3bb71f2291c6327")
    assert proof["origins"]["fine"]["request_sha256"] == (
        "46d17fb84329751019c4441bd2751ec20ec06a80d47fd822e2acbbd53b862934")
    assert proof["origins"]["plan"]["request_sha256"] == (
        "40e9d34ad3de1b634973069cc573176ac23a2320c61030bf13913ab465197d95")
    assert proof["review_origin"]["request_sha256"] == (
        "5ea7393f635632ae76436a7d13ca765a010930d7e3bc82f056ed066b113901bf")
    assert proof["review_origin"]["generic_prefix_sha256"] == (
        proof["selected_actual_video_review"]["template_sha256"])
    assert proof["selected_actual_video_review"] == selected_review.provenance()
    assert proof["historical_context_and_model_answers_included"] is False
    resource_text = (Path(original.__file__).parent / "phase_clarification.json").read_text(
        encoding="utf-8")
    for answer in ("功夫熊猫", "role_po", "1273.85", "2219.50", "4770.30",
                   "77.366667", "21.933333", "无臂女性", "神龙大侠", "watched_windows"):
        assert answer not in resource_text


def test_changed_phase_resource_is_rejected(tmp_path, monkeypatch):
    resource = Path(original.__file__).parent / "phase_clarification.json"
    (tmp_path / resource.name).write_bytes(resource.read_bytes() + b" ")
    monkeypatch.setattr(original, "_ROOT", tmp_path)
    with pytest.raises(ValueError, match="historical_rough_phase_clarification_changed"):
        original.phase_provenance()
    with pytest.raises(ValueError, match="historical_rough_phase_clarification_changed"):
        original.for_round(1)


def test_phase_provenance_rejects_changed_existing_review_template(tmp_path, monkeypatch):
    root = Path(selected_review.__file__).parent
    shutil.copytree(root, tmp_path / "selected_review")
    target = tmp_path / "selected_review" / "review_prefix.txt"
    target.write_bytes(target.read_bytes() + b" ")
    monkeypatch.setattr(selected_review, "_ROOT", target.parent)
    with pytest.raises(ValueError, match="historical_selected_review_template_changed"):
        original.phase_provenance()


def test_historical_renderer_keeps_frame_rounding_and_model_choices(tmp_path):
    catalog = {"sources": [{"source_id": "synthetic", "path": str(tmp_path / "source.mp4"),
                            "duration_s": 20, "sha256": "f" * 64, "audio_stream_index": 2}]}
    plan = {"segments": [
        {"source_id": "synthetic", "window_id": "synthetic_window",
         "source_in_s": 1.02, "source_out_s": 2.98, "speed": 1,
         "framing": "fit", "look": "none"},
        {"source_id": "synthetic", "window_id": "synthetic_window",
         "source_in_s": 4, "source_out_s": 5, "speed": 0.5,
         "framing": "fit", "look": "none"},
    ], "audio_mode": "source", "source_gain_db": -12}
    result = original.compile_library_plan(catalog, plan, fps=30)
    assert result["renderer_version"] == "library_render_v1"
    assert result["plan"] == plan
    assert result["total_frames"] == 119
    assert result["duration_s"] == 119 / 30
    assert [s["source_in_s"] for s in result["segments"]] == [1.02, 4]
    assert result["source_gain_db"] == -12
    plan["segments"][0]["source_out_s"] = 21
    with pytest.raises(ValueError, match="source_out_s"):
        original.compile_library_plan(catalog, plan, fps=30)
