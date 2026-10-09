"""Historical generic prompts and renderer must survive packaging unchanged."""
import hashlib
import json
from pathlib import Path

import pytest

from omni_story.library.resources import original_rough_v1 as original


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
