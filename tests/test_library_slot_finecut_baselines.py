"""Prepare synthetic FFmpeg parents without a model, provider or paid-run ledger."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library.media import sha256_file
from omni_story.library.render import render_library_video
from omni_story.library import slot_finecut_baselines as b
from omni_story.library.state import json_sha

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="Synthetic parent verification requires FFmpeg")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def run(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=96x64:r=10:d=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(source)], check=True, capture_output=True)
    reference = tmp_path / "ref.mp4"
    shutil.copyfile(source, reference)
    sha, ref_sha = sha256_file(source), sha256_file(reference)
    output = tmp_path / "same_run"
    output.mkdir()
    catalog = {"sources": [{"source_id": "movie", "path": str(source.resolve()),
                           "sha256": sha, "duration_s": 5.0, "audio_stream_index": None}]}
    write(output / "catalog" / "inventory.json", catalog)
    write(output / "reference_catalog" / "inventory.json", {"sources": [
        {"source_id": "ref", "path": str(reference.resolve()), "sha256": ref_sha, "duration_s": 5.0}]})
    write(output / "reference_reading.json", {"reference_sha256": ref_sha,
                                               "theme": "synthetic model fixture, no quality claim"})
    window = {"window_id": "w", "status": "watched", "kind": "continuous_window",
              "path": str(source.resolve()), "sha256": sha, "source_id": "movie",
              "source_sha256": sha, "source_start_s": 0.0, "source_end_s": 5.0,
              "observation": {"window_id": "w", "source_id": "movie", "roles": [{"role_id": "A"}],
                              "events": [{"local_start_s": 0, "local_end_s": 5,
                                          "observed_fact": "synthetic geometry", "role_ids": ["A"]}],
                              "usable_ranges": [{"local_in_s": 0, "local_out_s": 5, "role_ids": ["A"]}],
                              "uncertainties": ["fixture is not real model evidence"]}}
    write(output / "watched_windows.json", [window])
    state = {"task_id": "existing_synthetic_task", "input_lock": {"reference_sha256": ref_sha,
             "library_sources": [{"source_id": "movie", "sha256": sha}]},
             "calls": [{"id": "old_unknown", "status": "uncertain", "name": "untouched"}],
             "request_count": 131}
    for round_no in (0, 3):
        segments = [{"segment_id": "s1", "slot_id": "slot1", "source_id": "movie", "window_id": "w",
                     "source_in_s": .2, "source_out_s": 1.2, "speed": 1, "role_ids": ["A"]},
                    {"segment_id": "s2", "slot_id": "slot2", "source_id": "movie", "window_id": "w",
                     "source_in_s": 2.4, "source_out_s": 2.8, "speed": .5, "role_ids": ["A"]}]
        if round_no == 3:
            segments[-1]["freeze_tail_s"] = .2
        plan = {"reference_sha256": ref_sha, "segments": segments, "audio_mode": "silent",
                "slots": [{"slot_id": "slot1", "segment_ids": ["s1"], "intended_takeaway": "fixture1"},
                          {"slot_id": "slot2", "segment_ids": ["s2"], "intended_takeaway": "fixture2"}]}
        write(output / f"plan_{round_no}.json", plan)
        render_library_video(catalog, plan, output / f"render_{round_no}", fps=10, width=96, height=128)
        call_id = f"known_plan_{round_no}"
        request, response = {"fixture": "no model", "round": round_no}, {"content": plan}
        for name, body in (("request", request), ("response", response), ("parsed", plan)):
            write(output / "calls" / call_id / (name + ".json"), body)
        state["calls"].append({"id": call_id, "status": "received",
                               "request_sha256": json_sha(request), "response_sha256": json_sha(response)})
    write(output / "library_state.json", state)
    return output


def test_prepare_is_same_directory_idempotent_and_does_not_change_ledger(run):
    before = (run / "library_state.json").read_bytes()
    first = b.prepare(run)
    second = b.prepare(run)
    assert first == second
    assert (run / "library_state.json").read_bytes() == before
    assert first["execution_authorized"] is False
    assert [p["baseline_id"] for p in first["parents"]] == ["render_0", "render_3"]
    assert Path(first["preparation_path"]).parent == run / "artifacts" / b.SCHEMA
    assert all(Path(row["path"]).is_absolute() for row in first["protected_files"])
    assert first["parents"][0]["duration_s"] == pytest.approx(1.8)
    assert first["parents"][1]["duration_s"] == pytest.approx(2.0)
    assert first["parents"][1]["provenance"][0]["role_ids"] == ["A"]
    assert first["reference"]["audio_stream_index"] is None
    assert first["reference"]["cached_methods"] is None
    assert first["reference"]["cache_provenance"]["methods_path"] is None


def test_mapping_separates_real_hold_from_motion_and_retains_watched_facts(run):
    record = b.prepare(run)
    parent = record["parents"][1]
    motion, hold = parent["output_mapping"]
    assert motion["hold_output_in_s"] is None
    assert hold["output_in_s"] == 1
    assert hold["motion_output_out_s"] == pytest.approx(1.8)
    assert hold["hold_output_in_s"] == pytest.approx(1.8)
    assert hold["hold_output_out_s"] == 2
    assert hold["hold_source"]["source_out_s"] == 2.8
    assert parent["allowed_windows"] == read(run / "watched_windows.json")
    assert parent["original_plan"] == read(run / "plan_3.json")
    assert parent["original_model_calls"][0]["call_id"] == "known_plan_3"


@pytest.mark.parametrize("target", ["render_0/final.mp4", "watched_windows.json", "plan_0.json",
                                    "calls/known_plan_0/request.json"])
def test_existing_preparation_refuses_changed_evidence(run, target):
    b.prepare(run)
    path = run / target
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="sha_mismatch|evidence_changed|call_json_sha"):
        b.prepare(run)


def test_preparation_identity_tampering_is_rejected(run):
    record = b.prepare(run)
    path = Path(record["preparation_path"])
    edited = read(path)
    edited["parents"][0]["duration_s"] += .1
    write(path, edited)
    with pytest.raises(ValueError, match="preparation_identity"):
        b.load_preparation(path)


def test_preparation_cannot_be_relocated_to_a_new_run(run, tmp_path):
    record = b.prepare(run)
    path = Path(record["preparation_path"])
    other = tmp_path / path.name
    shutil.copyfile(path, other)
    with pytest.raises(ValueError, match="outside_original_run"):
        b.load_preparation(other)


def test_boolean_round_does_not_match_an_existing_preparation(run):
    b.prepare(run)
    with pytest.raises(ValueError, match="invalid_baseline_rounds"):
        b.prepare(run, (False, 3))


def test_ledger_can_grow_without_invalidating_original_parent_manifest(run):
    record = b.prepare(run)
    state = read(run / "library_state.json")
    state["calls"].append({"id": "later_received", "status": "received", "name": "unrelated"})
    state["request_count"] += 1
    write(run / "library_state.json", state)
    assert b.load_preparation(record["preparation_path"]) == record


def test_output_gap_is_rejected_even_when_render_input_identity_is_updated(run):
    path = run / "render_0" / "render_input.json"
    frozen = read(path)
    frozen["compiled"]["segments"][1]["output_in_s"] += .1
    write(path, frozen)
    result_path = run / "render_0" / "render_result.json"
    result = read(result_path)
    result["input_identity"] = json_sha(frozen)
    result["provenance"] = deepcopy(frozen["compiled"]["segments"])
    write(result_path, result)
    with pytest.raises(ValueError, match="timeline_gap"):
        b.load_baselines(run)


def test_baseline_source_range_must_remain_inside_watched_window(run):
    watched = read(run / "watched_windows.json")
    watched[0]["source_end_s"] = 2.5
    write(run / "watched_windows.json", watched)
    with pytest.raises(ValueError, match="outside_watched"):
        b.load_baselines(run)


def test_copyable_delivery_paths_include_complete_path_and_folder(run):
    path = run / "render_3" / "final.mp4"
    expected = f"完整视频路径：{path.resolve()}\n所在文件夹：{path.parent.resolve()}"
    assert b.delivery_paths({"rendered_path": str(path)}) == expected
    assert b.delivery_paths(path) == expected


@pytest.mark.parametrize("preferred", [False, True])
def test_reference_methods_precedence_and_bytes_are_bound(run, preferred):
    sha = read(run / "reference_reading.json")["reference_sha256"]
    fallback_path = run / "editing_reference_v2.json"
    write(fallback_path, {"reference_sha256": sha, "methods": [{"method_id": "fallback"}]})
    expected_path = fallback_path
    if preferred:
        expected_path = run / "artifacts" / "editing_revision_v1" / "reference_methods.json"
        write(expected_path, {"reference_sha256": sha, "methods": [{"method_id": "preferred"}]})
    record = b.prepare(run)
    assert record["reference"]["cached_methods"] == read(expected_path)
    provenance = record["reference"]["cache_provenance"]
    assert provenance["methods_path"] == str(expected_path.resolve())
    assert provenance["methods_sha256"] == sha256_file(expected_path)
    assert any(row["path"] == str(expected_path.resolve()) for row in record["protected_files"])
    expected_path.write_bytes(expected_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="evidence_changed"):
        b.load_preparation(record["preparation_path"])


def test_reference_methods_cannot_claim_a_different_reference(run):
    write(run / "editing_reference_v2.json", {"reference_sha256": "0" * 64, "methods": []})
    with pytest.raises(ValueError, match="reference_methods_sha_mismatch"):
        b.prepare(run)


def test_reference_audio_index_must_refer_to_actual_audio_stream(run):
    path = run / "reference_catalog" / "inventory.json"
    catalog = read(path)
    catalog["sources"][0]["audio_stream_index"] = 0  # Synthetic stream 0 is video.
    write(path, catalog)
    with pytest.raises(ValueError, match="reference_audio_stream_mismatch"):
        b.prepare(run)
