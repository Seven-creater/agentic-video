"""Structural editing checks and append-only, offline observations.

The checks bind model claims to records and media ranges. They do not establish
an editing technique, narrative quality, or sound quality as ground truth.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..contract import forbid_keys, ids, number, require, rows, text
from .contracts import validate_fine
from .media import sha256_file
from .shot_timeline import associate_edl_boundaries, detect_shot_timeline


def validate_candidate_dispositions(plan, windows):
    """Account for every watched candidate without silently dropping evidence."""
    if isinstance(windows, list):
        ids(windows, "window_id", "candidate/windows", nonempty=False)
        windows = {window["window_id"]: window for window in windows}
    require(isinstance(windows, dict), "candidate/windows:object_required")
    watched = {window_id for window_id, window in windows.items() if window.get("status") == "watched"}
    selected = {segment.get("window_id") for segment in rows(plan.get("segments"), "candidate/segments")}
    require(selected <= watched, "candidate:selected_window_not_watched")
    dispositions = plan.get("candidate_dispositions")
    found = ids(dispositions, "window_id", "candidate/dispositions", nonempty=False)
    require(found == watched, "candidate:watched_windows_must_be_accounted_for_exactly_once")
    for item in dispositions:
        decision = item.get("decision")
        require(isinstance(decision, str) and decision in {"selected", "not_selected"}, "candidate:decision")
        require((decision == "selected") == (item["window_id"] in selected),
                "candidate:decision_disagrees_with_edl")
        text(item.get("reason"), "candidate/reason")
    return plan


def validate_fine_editing(data, window, editing_reference):
    """Keep model-visible forms separate from task-specific possible uses.

    The ranges are local seconds in this continuous observation window. These
    model descriptions and detector candidates are not verified editing truth.
    """
    validate_fine(data, window)
    method_ids = ids(editing_reference.get("methods"), "method_id", "fine_editing/methods")
    duration = window["source_end_s"] - window["source_start_s"]
    observations = rows(data.get("editing_observations"), "fine_editing/observations", nonempty=False)
    for observation in observations:
        require(isinstance(observation, dict), "fine_editing/observation:object_required")
        text(observation.get("method_id"), "fine_editing/method_id")
        require(observation["method_id"] in method_ids, "fine_editing:unknown_method")
        start = number(observation.get("local_start_s"), "fine_editing/local_start_s")
        end = number(observation.get("local_end_s"), "fine_editing/local_end_s")
        require(start < end <= duration + 0.001, "fine_editing:range_outside_window")
        text(observation.get("observed_form"), "fine_editing/observed_form")
        text(observation.get("potential_use"), "fine_editing/potential_use")
        for limitation in rows(observation.get("limitations"), "fine_editing/limitations", nonempty=False):
            text(limitation, "fine_editing/limitation")
        forbid_keys(observation, {"source_start_s", "source_end_s", "source_in_s", "source_out_s", "timestamp_s"})
    return data


def validate_method_review(review, editing_reference, duration_s):
    """Require method-level actual-output evidence and preserve audio limits."""
    require(isinstance(review, dict), "method_review:object_required")
    require(review.get("reference_sha256") == editing_reference.get("reference_sha256"),
            "method_review:reference_sha_changed")
    method_ids = ids(editing_reference.get("methods"), "method_id", "method_review/methods")
    checks = review.get("method_checks")
    check_ids = ids(checks, "method_id", "method_review/checks")
    require(check_ids == method_ids, "method_review:methods_must_be_checked_exactly_once")
    methods = {method["method_id"]: method for method in editing_reference["methods"]}
    all_visual_pass = True
    requires_audio = False
    for check in checks:
        for key in ("form_status", "function_status"):
            require(isinstance(check.get(key), str)
                    and check[key] in {"pass", "partial", "fail", "unverifiable"}, "method_review:" + key)
        audio = check.get("audio_status")
        require(isinstance(audio, str) and audio in {"unverifiable", "not_applicable"},
                "method_review:audio_not_auditioned")
        required = methods[check["method_id"]]["requires_audio"]
        require(not required or audio == "unverifiable", "method_review:required_audio_is_unverifiable")
        requires_audio |= required
        all_visual_pass &= check["form_status"] == check["function_status"] == "pass"
        for evidence in rows(check.get("output_evidence"), "method_review/output_evidence",
                             nonempty="pass" in (check["form_status"], check["function_status"])):
            require(isinstance(evidence, dict), "method_review/evidence:object_required")
            start = number(evidence.get("start_s"), "method_review/evidence/start_s")
            end = number(evidence.get("end_s"), "method_review/evidence/end_s")
            require(start < end <= duration_s + 0.001, "method_review:evidence_outside_output")
            text(evidence.get("observed_fact"), "method_review/observed_fact")
        for limitation in rows(check.get("limitations"), "method_review/limitations", nonempty=False):
            text(limitation, "method_review/limitation")
    require(isinstance(review.get("editing_status"), str)
            and review["editing_status"] in {"pass", "partial", "fail", "unverifiable"},
            "method_review:editing_status")
    require(review["editing_status"] != "pass" or (all_visual_pass and not requires_audio),
            "method_review:overall_pass_without_complete_method_evidence")
    return review


def compact_timeline(timeline):
    """Return file-bound cut candidates without original-film PTS claims."""
    return {
        "source_sha256": timeline["source_sha256"], "record_sha256": timeline.get("record_sha256"),
        "detector_threshold_percent":timeline["spec"]["threshold_percent"],
        "observed_media_duration_s": timeline["source_end_s"] - timeline["source_start_s"],
        "source_start_s": timeline["source_start_s"], "source_end_s": timeline["source_end_s"],
        "cuts": [{"time_s": cut["source_time_s"], "score_percent": cut["score_percent"]}
                 for cut in timeline["cuts"]],
        "shot_intervals": [{key: shot[key] for key in ("start_s", "end_s", "duration_s")}
                           for shot in timeline["shots"]],
        "limitations": [
            "Threshold-based visual-change candidates and intervals, not semantic shots or editing-technique truth.",
            "This describes only the SHA-bound observed file; proxy timings are not original-film native-frame evidence.",
            "The terminal frame duration is estimated; flashes and motion may add candidates and gradual changes may be missed.",
            "No sound, music rhythm or narrative interpretation is measured.",
        ],
    }


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_existing_editing(output, reference, *, threshold=3.0):
    """Append CPU observations of an existing reference and existing renders.

    All bound media hashes are checked before creating the new artifact folder.
    The audit makes no model call, creates no render, and never rewrites state,
    result, model responses, or an earlier observation report.
    """
    threshold = number(threshold, "editing_audit/threshold")
    require(0 < threshold <= 100, "editing_audit:threshold_out_of_range")
    output, reference = Path(output).resolve(strict=True), Path(reference).resolve(strict=True)
    result = _read(output / "result.json")
    reference_sha = sha256_file(reference)
    require(reference_sha == result.get("reference_sha256"), "editing_audit:reference_sha_changed")
    render_records = []
    for marker in sorted(output.glob("render_*/render_result.json")):
        manifest = _read(marker)
        video = Path(manifest["rendered_path"]).resolve(strict=True)
        require(video.parent == marker.parent.resolve(), "editing_audit:render_path_outside_record_directory")
        actual_sha = sha256_file(video)
        require(actual_sha == manifest.get("sha256"), "editing_audit:render_sha_changed")
        if "final" in manifest.get("hashes", {}):
            require(actual_sha == manifest["hashes"]["final"], "editing_audit:render_hash_record_changed")
        render_records.append((marker, manifest, video, actual_sha))
    require(bool(render_records), "editing_audit:no_render_records")
    selected = Path(result["final_video"]).resolve(strict=True)
    require(any(video == selected and digest == result.get("final_sha256")
                for _, _, video, digest in render_records), "editing_audit:selected_result_not_bound_to_render")
    directory = output / "artifacts" / "editing_observation_v1"
    directory.mkdir(parents=True, exist_ok=True)
    reference_timeline = detect_shot_timeline(
        {"source_id": "fixed_reference", "path": str(reference), "sha256": reference_sha},
        directory / "cache", threshold=threshold)
    render_observations = []
    for marker, manifest, video, digest in render_records:
        timeline = detect_shot_timeline(
            {"source_id": marker.parent.name, "path": str(video), "sha256": digest},
            directory / "cache", threshold=threshold)
        render_observations.append({
            "render_record_path": str(marker), "render_record_sha256": sha256_file(marker),
            "rendered_path": str(video), "render_sha256": digest,
            "selected": video == selected, "timeline": compact_timeline(timeline),
            "timeline_manifest_path": timeline["manifest_path"],
            "edl_association": associate_edl_boundaries(timeline, manifest),
        })
    report = {
        "version": "editing_observation_v1", "models_called": 0, "new_renders": 0,
        "detector_threshold_percent": threshold,
        "result_record_path": str(output / "result.json"), "result_record_sha256": sha256_file(output / "result.json"),
        "reference_path": str(reference), "reference_sha256": reference_sha,
        "reference_timeline": compact_timeline(reference_timeline),
        "reference_timeline_manifest_path": reference_timeline["manifest_path"],
        "renders": render_observations,
        "limitations": ["This offline audit supplies visual-change and EDL evidence, not a new quality verdict.",
                        "Existing model reviews, selection, state, request budgets and rendered media remain unchanged."],
    }
    index = 1
    while (directory / f"report_{index:03d}.json").exists():
        index += 1
    report_path = directory / f"report_{index:03d}.json"
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    return {"report_path": str(report_path), "report": report}
