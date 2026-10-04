"""Evidence accounting and offline audit tests; no model calls or real library."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import editing
from omni_story.library.media import sha256_file
from omni_story.library.render import render_library_video


@pytest.fixture
def candidates():
    windows = {"w1": {"window_id": "w1", "status": "watched"},
               "w2": {"window_id": "w2", "status": "watched"},
               "w3": {"window_id": "w3", "status": "pending"}}
    plan = {"segments": [{"window_id": "w1"}], "candidate_dispositions": [
        {"window_id": "w1", "decision": "selected", "reason": "used in the actual EDL"},
        {"window_id": "w2", "decision": "not_selected", "reason": "does not support this combination"},
    ]}
    return plan, windows


def test_every_watched_candidate_has_one_decision_consistent_with_edl(candidates):
    plan, windows = candidates
    assert editing.validate_candidate_dispositions(plan, windows) is plan
    editing.validate_candidate_dispositions(plan, list(windows.values()))


@pytest.mark.parametrize("mutation,error", [
    (lambda p: p["candidate_dispositions"].pop(), "accounted_for_exactly_once"),
    (lambda p: p["candidate_dispositions"].append(deepcopy(p["candidate_dispositions"][0])), "duplicate_id"),
    (lambda p: p["candidate_dispositions"][1].update(window_id="w3"), "accounted_for_exactly_once"),
    (lambda p: p["candidate_dispositions"][0].update(decision="not_selected"), "disagrees_with_edl"),
    (lambda p: p["candidate_dispositions"][1].update(decision="selected"), "disagrees_with_edl"),
    (lambda p: p["candidate_dispositions"][0].update(reason=""), "text_required"),
    (lambda p: p["segments"].append({"window_id": "w3"}), "selected_window_not_watched"),
])
def test_candidates_cannot_be_dropped_or_falsely_marked_as_used(candidates, mutation, error):
    plan, windows = candidates
    mutation(plan)
    with pytest.raises(ValueError, match=error):
        editing.validate_candidate_dispositions(plan, windows)


@pytest.fixture
def fine_editing():
    window = {"window_id": "fine_1", "source_id": "movie", "source_start_s": 100, "source_end_s": 120}
    reference = {"methods": [{"method_id": "contrast"}, {"method_id": "montage"}]}
    data = {"window_id": "fine_1", "source_id": "movie", "roles": [], "events": [],
            "usable_ranges": [], "uncertainties": [], "editing_observations": [
                {"method_id": "contrast", "local_start_s": 1, "local_end_s": 3,
                 "observed_form": "a wide view of the completed action is followed by a close reaction",
                 "potential_use": "this may emphasize the difference in the two characters' responses",
                 "limitations": ["the detector's boundary candidate is not itself confirmation of this relation"]}
            ]}
    return data, window, reference


def test_fine_editing_keeps_observed_form_separate_from_possible_use_without_full_method_coverage(fine_editing):
    data, window, reference = fine_editing
    original = deepcopy(data)
    assert editing.validate_fine_editing(data, window, reference) is data
    assert data == original
    data["editing_observations"] = []
    editing.validate_fine_editing(data, window, reference)


@pytest.mark.parametrize("mutation,error", [
    (lambda d: d.update(source_id="other"), "source_id_mismatch"),
    (lambda d: d.pop("editing_observations"), "list_required"),
    (lambda d: d["editing_observations"][0].update(method_id="unseen"), "unknown_method"),
    (lambda d: d["editing_observations"][0].update(local_start_s=-1), "finite_number_required"),
    (lambda d: d["editing_observations"][0].update(local_end_s=21), "range_outside_window"),
    (lambda d: d["editing_observations"][0].update(local_end_s=1), "range_outside_window"),
    (lambda d: d["editing_observations"][0].update(observed_form=""), "text_required"),
    (lambda d: d["editing_observations"][0].update(potential_use=""), "text_required"),
    (lambda d: d["editing_observations"][0].update(source_start_s=101), "mixed_time_domains"),
])
def test_fine_editing_rejects_unbound_methods_wrong_time_domains_and_missing_evidence_fields(
        fine_editing, mutation, error):
    data, window, reference = fine_editing
    mutation(data)
    with pytest.raises(ValueError, match=error):
        editing.validate_fine_editing(data, window, reference)


@pytest.fixture
def method_review():
    reference = {"reference_sha256": "ref", "methods": [
        {"method_id": "contrast", "requires_audio": False},
        {"method_id": "montage", "requires_audio": False},
    ]}
    review = {"reference_sha256": "ref", "editing_status": "pass", "method_checks": [
        {"method_id": method["method_id"], "form_status": "pass", "function_status": "pass",
         "audio_status": "not_applicable", "output_evidence": [
             {"start_s": 0, "end_s": 1, "observed_fact": "visible candidate evidence"}], "limitations": []}
        for method in reference["methods"]
    ]}
    return review, reference


def test_complete_method_review_retains_exact_actual_output_evidence(method_review):
    review, reference = method_review
    assert editing.validate_method_review(review, reference, 2) is review


@pytest.mark.parametrize("mutation,error", [
    (lambda r: r.update(reference_sha256="other"), "reference_sha_changed"),
    (lambda r: r["method_checks"].pop(), "checked_exactly_once"),
    (lambda r: r["method_checks"][1].update(method_id="unknown"), "checked_exactly_once"),
    (lambda r: r["method_checks"][0].update(form_status="partial"), "overall_pass"),
    (lambda r: r["method_checks"][0].update(audio_status="pass"), "audio_not_auditioned"),
    (lambda r: r["method_checks"][0].update(output_evidence=[]), "list_required"),
    (lambda r: r["method_checks"][0]["output_evidence"][0].update(end_s=3), "outside_output"),
    (lambda r: r["method_checks"][0]["output_evidence"][0].update(observed_fact=""), "text_required"),
])
def test_review_cannot_claim_unobserved_audio_or_global_success_without_method_evidence(
        method_review, mutation, error):
    review, reference = method_review
    mutation(review)
    with pytest.raises(ValueError, match=error):
        editing.validate_method_review(review, reference, 2)


def test_required_audio_stays_unverifiable_and_blocks_overall_pass(method_review):
    review, reference = method_review
    reference["methods"][0]["requires_audio"] = True
    review["method_checks"][0]["audio_status"] = "unverifiable"
    with pytest.raises(ValueError, match="overall_pass"):
        editing.validate_method_review(review, reference, 2)
    review["editing_status"] = "partial"
    editing.validate_method_review(review, reference, 2)
    review["method_checks"][0]["audio_status"] = "not_applicable"
    with pytest.raises(ValueError, match="required_audio_is_unverifiable"):
        editing.validate_method_review(review, reference, 2)


def test_nonpassing_method_may_preserve_missing_evidence_without_coercion(method_review):
    review, reference = method_review
    review["editing_status"] = "fail"
    review["method_checks"][0].update(form_status="fail", function_status="unverifiable",
                                      output_evidence=[], limitations=["no visible contrast found"])
    original = deepcopy(review)
    editing.validate_method_review(review, reference, 2)
    assert review == original


def test_compact_timeline_does_not_promote_proxy_pts_to_original_movie_evidence():
    timeline = {"source_sha256": "proxy_sha", "record_sha256": "record", "source_start_s": 0,
                "spec":{"threshold_percent":3.0},
                "source_end_s": 3, "cuts": [{"source_time_s": 1, "score_percent": 50, "native_pts": 12}],
                "shots": [{"start_s": 0, "end_s": 1, "duration_s": 1, "native_start_pts": 0},
                          {"start_s": 1, "end_s": 3, "duration_s": 2, "native_start_pts": 12}]}
    compact = editing.compact_timeline(timeline)
    assert compact["source_sha256"] == "proxy_sha"
    assert compact["observed_media_duration_s"] == 3
    assert compact["detector_threshold_percent"] == 3.0
    assert compact["cuts"] == [{"time_s": 1, "score_percent": 50}]
    assert "native_pts" not in json.dumps(compact)
    assert any("proxy timings are not original-film" in item for item in compact["limitations"])


@pytest.fixture
def audit_run(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required")
    reference = tmp_path / "reference.mp4"
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=black:s=96x64:r=10:d=2", "-vf",
        "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='gte(t,1)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(reference)
    ], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    source = {"source_id": "toy", "path": str(reference), "sha256": sha256_file(reference),
              "duration_s": 2, "video_stream_index": 0, "audio_stream_index": None}
    output = tmp_path / "run"
    plan = {"audio_mode": "silent", "segments": [
        {"source_id": "toy", "window_id": "fine_a", "source_in_s": 0, "source_out_s": 0.8},
        {"source_id": "toy", "window_id": "fine_b", "source_in_s": 1.2, "source_out_s": 2},
    ]}
    render = render_library_video({"sources": [source]}, plan, output / "render_0",
                                  fps=10, width=96, height=64)
    (output / "result.json").write_text(json.dumps({
        "reference_sha256": source["sha256"], "final_video": render["rendered_path"],
        "final_sha256": render["sha256"], "selected_round": 0,
    }), encoding="utf-8")
    (output / "library_state.json").write_text('{"requests":43,"unknown":["original"]}', encoding="utf-8")
    calls = output / "calls"
    calls.mkdir()
    (calls / "original_response.json").write_text('{"editing_status":"partial"}', encoding="utf-8")
    return output, reference


def test_real_offline_audit_appends_evidence_and_preserves_every_original_file(audit_run):
    output, reference = audit_run
    original = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    observation = editing.audit_existing_editing(output, reference)
    report_path = Path(observation["report_path"])
    assert report_path.parent == output / "artifacts" / "editing_observation_v1"
    report = observation["report"]
    assert report["models_called"] == report["new_renders"] == 0
    assert report["detector_threshold_percent"] == 3.0
    assert report["reference_sha256"] == sha256_file(reference)
    assert len(report["reference_timeline"]["cuts"]) == 1
    assert report["renders"][0]["selected"] is True
    assert report["renders"][0]["edl_association"]["edl_segment_count"] == 2
    assert report["renders"][0]["edl_association"]["edl_join_count"] == 1
    first_bytes = report_path.read_bytes()
    second = editing.audit_existing_editing(output, reference)
    assert second["report_path"] != str(report_path)
    assert report_path.read_bytes() == first_bytes
    high_threshold = editing.audit_existing_editing(output, reference, threshold=100)
    assert high_threshold["report"]["detector_threshold_percent"] == 100
    assert high_threshold["report"]["reference_timeline"]["cuts"] == []
    assert report_path.read_bytes() == first_bytes
    assert all(path.read_bytes() == content for path, content in original.items())


@pytest.mark.parametrize("alter,error", [
    ("reference", "reference_sha_changed"), ("render", "render_sha_changed"),
    ("result", "selected_result_not_bound_to_render"),
])
def test_audit_hash_preflight_fails_before_new_observation_artifacts(audit_run, alter, error):
    output, reference = audit_run
    if alter == "reference":
        reference.write_bytes(reference.read_bytes() + b"changed")
    elif alter == "render":
        movie = output / "render_0" / "final.mp4"
        movie.write_bytes(movie.read_bytes() + b"changed")
    else:
        path = output / "result.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["final_sha256"] = "incorrect"
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        editing.audit_existing_editing(output, reference)
    assert not (output / "artifacts" / "editing_observation_v1").exists()
