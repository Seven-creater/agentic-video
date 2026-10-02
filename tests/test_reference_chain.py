"""Offline chain/effect regressions. Synthetic model replies are not a model quality evaluation."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from omni_story import editing, pipeline, prompts, production, reference
from test_pipeline import FakeAPI, measured, inputs, run
from test_production import edit_plan, sources, production_plan, refresh_synthetic_reference


def transfer_fixture(tmp_path):
    _, directory, _, video = run(tmp_path)
    return production.load_reference_transfer(directory, video)


@pytest.mark.parametrize("budget", [12, 4])
def test_explicit_reedit_reuses_media_preserves_old_budget_and_never_regenerates(tmp_path, monkeypatch, budget):
    _, directory, _, video = run(tmp_path)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=gray:s=160x90:r=24:d=2", "-c:v", "libx264", str(video)], check=True)
    refresh_synthetic_reference(directory, video)
    old = directory / "production"
    production.write(old / "production_plan.json", production_plan())
    for mid in ("M1", "M2"):
        source = old / "video_jobs" / mid / "video.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(video.read_bytes())
        production.write(source.parent / "submission.json", {"status": "completed", "path": str(source),
                         "sha256": pipeline.sha(source)})
    for i in range(36):
        production.write(old / "calls" / str(i) / "request.json", {"synthetic": True})
    before = {str(p): pipeline.sha(p) for p in old.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("re-edit must not construct generation backends")
    monkeypatch.setattr(production, "AliyunImages", forbidden)
    monkeypatch.setattr(production, "MiniMaxH3", forbidden)
    monkeypatch.setattr(production, "REEDIT_LIMITS", {**production.REEDIT_LIMITS, "model_calls": budget})

    class Omni:
        cfg = {"model": "synthetic"}
        calls = 0
        def request(self, text, **kwargs):
            self.calls += 1
            if text.startswith(prompts.REFERENCE.removesuffix("Input: ")):
                assert kwargs["fps"] == 2
                response = FakeAPI().request(text, media=kwargs["media"])
                body = json.loads(response["body_text"])
                value = json.loads(body["choices"][0]["message"]["content"])
                value["sections"][0]["end_s"] = 2
            elif text.startswith(production.prompts.WATCH):
                payload = json.loads(text.split("Input: ", 1)[1])
                assert payload["reference_transfer"]["schema_version"] == "reference_transfer_v2"
                value = {"material_id": "supplied ID" if payload["material_id"] == "M2" else payload["material_id"], "events": [
                    {"start_s": 0, "end_s": 1, "visible": "synthetic"}], "usable_information": [], "limitations": []}
            elif text.startswith(production.prompts.EDIT):
                value = edit_plan()
            elif text.startswith(production.prompts.FINAL_REVIEW):
                assert kwargs["media"].is_file()
                value = {"decision": "accept", "viewer_reading": "synthetic", "reason": "synthetic",
                         "issues": [], "replacement_plan": None, "style_review": [
                             {"method_id": "D1", "status": "adapted", "start_s": 0, "end_s": 1,
                              "evidence": "synthetic actual output"}]}
            else:
                pytest.fail("Unexpected call; no story/image/video rerun or parallel reference editing")
            return {"http_status": 200, "body_text": json.dumps({"choices": [
                {"message": {"content": json.dumps(value)}}]})}
    api = Omni()
    result = production.reedit_existing_media(video, directory, runner=api)
    root = directory / "editing_continuations/joint_reference_v2"
    assert before == {str(p): pipeline.sha(p) for p in old.rglob("*") if p.is_file()}
    assert len(list((old / "calls").glob("*/request.json"))) == 36
    assert result["new_image_jobs"] == result["new_video_jobs"] == 0
    assert production.load(root / "authorization_scope.json")["old_model_request_count"] == 36
    assert production.load(root / "production/calls/reference/request.json")["requested_fps"] == 2
    normalized = production.load(root / "production/calls/watch_M2/normalization.json")
    assert normalized["rule"] == "literal_supplied_ID_placeholder_for_single_requested_media"
    if budget == 12:
        assert result["status"] == "model_checked_final_video" and api.calls == 5
        assert result["previous_model_requests"] == 36 and result["additional_model_requests"] == 5
        assert result["upstream_story_and_assets"] == "frozen_previous_chain_not_recreated"
        assert production.reedit_existing_media(video, directory, runner=api) == result and api.calls == 5
    else:
        assert result["status"] == "video_candidate_with_limitations"
        assert result["final_model_review_status"] == "not_run_budget_exhausted" and result["model_review"] is None
        assert result["style_transfer"]["reviewed_video_sha256"] is None
        assert api.calls == 4
        production.reedit_existing_media(video, directory, runner=api)
        assert api.calls == 4  # the explicit extension cannot silently reset itself


def test_one_shared_analysis_reaches_every_writing_stage(tmp_path):
    result, directory, api, _ = run(tmp_path)
    assert result["status"] == "model_checked_screenplay_candidate"
    transfer = production.load(directory / "reference_transfer.json")
    for text, media in api.seen:
        if text.startswith((prompts.ROUTES, prompts.OUTLINE, prompts.SEGMENT)):
            assert json.loads(text.split("Input: ", 1)[1])["reference_transfer"] == transfer
    assert sum(media is not None for _, media in api.seen) == 1
    assert transfer["timestamp_basis"] == "model_estimates_not_cut_ground_truth"
    assert transfer["editing"]["methods"][0]["status"] == "observed"
    assert transfer["reading_sha256"] == pipeline.sha(directory / "reference_reading.json")
    assert "reference_transfer" not in next(t for t, _ in api.seen if t.startswith(prompts.BLIND))


def test_model_requested_local_view_no_prior_conclusion_or_human_answer(tmp_path, monkeypatch):
    class WithQuestion(FakeAPI):
        def request(self, text, *, media=None, tokens=0, fps=None):
            if text.startswith(prompts.REFERENCE_LOCAL):
                self.seen.append((text, media))
                payload = json.loads(text.split("Input: ", 1)[1])
                assert fps == 4 and media is not None and payload["source_start_s"] == 1
                assert "audience_takeaway" not in payload and "reference_reading" not in payload
                reply = {"question_id": "Q1", "observations": [{"start_s": 0, "end_s": 1,
                         "observed": "synthetic adjacent image change", "modality": "visual"}],
                         "purpose_hypotheses": [], "uncertainties": []}
                return {"http_status": 200, "elapsed_s": 0, "body_text": json.dumps({"choices": [
                    {"message": {"content": json.dumps(reply)}}]})}
            response = super().request(text, media=media, tokens=tokens)
            if text.startswith(prompts.REFERENCE):
                body = json.loads(response["body_text"])
                value = json.loads(body["choices"][0]["message"]["content"])
                value["editing"]["inspection_requests"] = [{"question_id": "Q1", "start_s": 1,
                    "end_s": 2, "question": "what changes across this window?", "reason": "uncertain seam"}]
                body["choices"][0]["message"]["content"] = json.dumps(value)
                response["body_text"] = json.dumps(body)
            return response
    def local_view(source, target, start, end):
        assert (start, end) == (1, 2)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"synthetic original local media")
    monkeypatch.setattr(editing, "local_view", local_view)
    _, directory, api, _ = run(tmp_path, WithQuestion())
    transfer = production.load(directory / "reference_transfer.json")
    assert len(api.seen) == 11
    assert len(transfer["editing"]["local_observations"]) == 1
    metadata = production.load(directory / "calls/002_reference_local_0/request_metadata.json")
    assert metadata["requested_fps"] == 4 and metadata["actual_sampling"] == "provider_not_reported"
    assert "local_observations" not in production.load(directory / "reference_reading_draft.json")["editing"]


def test_reference_method_unknown_and_empty_are_not_forced_roles(tmp_path):
    transfer = transfer_fixture(tmp_path)
    value = deepcopy(transfer["editing"])
    value["methods"][0]["status"] = "unknown"
    reference.validate_editing(value, {"S1"}, 37.533, False)
    value["methods"] = []
    reference.validate_editing(value, {"S1"}, 37.533, False)
    reference.validate_mapping([], {"editing": value}, 1)
    reference.validate_style_review([], {"editing": value}, 1)


def test_missing_audio_and_excess_local_questions_fail_structurally(tmp_path):
    value = transfer_fixture(tmp_path)["editing"]
    value["music_region"] = {"start_s": 0, "end_s": 1, "basis": "claimed music"}
    with pytest.raises(ValueError, match="without_audio"):
        reference.validate_editing(value, {"S1"}, 37.533, False)
    value["music_region"] = None
    value["inspection_requests"] = [{}] * 3
    with pytest.raises(ValueError, match="inspection_budget"):
        reference.validate_editing(value, {"S1"}, 37.533, False)


def test_transfer_cannot_change_parents_or_silently_replace_the_content(tmp_path):
    _, directory, _, video = run(tmp_path)
    original = production.load(directory / "reference_transfer.json")
    production.write(directory / "reference_transfer.json", {**original, "reference_sha256": "wrong"})
    with pytest.raises(ValueError, match="parent_mismatch"):
        production.load_reference_transfer(directory, video)
    production.write(directory / "reference_transfer.json", {**original, "audience_takeaway": "replacement"})
    with pytest.raises(ValueError, match="transfer_changed"):
        production.load_reference_transfer(directory, video)


def test_edit_mapping_is_bookkeeping_not_automatic_style_quality(tmp_path):
    transfer = transfer_fixture(tmp_path)
    value = edit_plan()
    value["style_mapping"][0].update(status="unavailable", segment_indices=[])
    editing.compile_plan(value, sources(), None, 37.5, transfer=transfer)
    value["style_mapping"] = []
    compiled = editing.compile_plan(value, sources(), None, 37.5, transfer=transfer)
    assert compiled["missing_style_mapping_ids"] == ["D1"]
    value["style_mapping"] = [{"method_id": "D1", "status": "applied", "segment_indices": [99], "explanation": "x"}]
    with pytest.raises(ValueError, match="mapping_indices"):
        editing.compile_plan(value, sources(), None, 37.5, transfer=transfer)
    with pytest.raises(ValueError, match="review_interval"):
        reference.validate_style_review([{"method_id": "D1", "status": "visible", "start_s": 0,
            "end_s": 99, "evidence": "claimed effect"}], transfer, 1)
    reference.validate_style_review([], transfer, 1)


def test_contiguous_rows_are_not_counted_as_new_editor_cuts():
    catalog = [{"material_id": f"M{i}", "path": "synthetic", "sha256": "synthetic",
                "measured_duration_s": 16} for i in range(1, 6)]
    value = edit_plan()
    value["segments"] = [{"material_id": f"M{i}", "in_s": a, "out_s": b, "speed": 1,
                          "fade_in_s": 0, "fade_out_s": 0, "purpose": "synthetic"}
                         for i, intervals in enumerate(([(0, 4.5), (4.5, 7.5)],
                             [(3, 8), (8, 12), (12, 15)], [(4, 7), (7, 9.5)],
                             [(0, 3.5), (3.5, 7.5), (7.5, 12)], [(6.2, 9.5), (9.5, 14.8)]), 1)
                         for a, b in intervals]
    compiled = editing.compile_plan(value, catalog, None, 37.5)
    metrics = editing.edit_metrics(compiled, catalog)
    assert metrics["segment_rows"] == 12 and len(metrics["continuous_source_runs"]) == 5
    assert metrics["effective_edit_boundaries"] == 4
    assert metrics["policy"] == "measurement_for_Omni_not_a_quality_gate"
    # Omitting even a small interval really is a source jump, irrespective of raw row count.
    value["segments"][1]["in_s"] += .5
    metrics = editing.edit_metrics(editing.compile_plan(value, catalog, None, 37.5), catalog)
    assert len(metrics["continuous_source_runs"]) == 6


@pytest.mark.parametrize("look", [{"type": "unknown"},
    {"type": "color_reveal", "at_s": .5, "duration_s": 1},
    {"type": "color_reveal", "at_s": 0, "duration_s": 0}])
def test_unsupported_or_out_of_bounds_effects_never_execute(look):
    value = edit_plan()
    value["segments"][0]["look"] = look
    with pytest.raises(ValueError):
        editing.compile_plan(value, sources(), None, 37.5)


def test_real_color_reveal_and_local_original_sound(tmp_path):
    source = tmp_path / "red.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=160x90:r=24:d=2", "-f", "lavfi", "-i", "sine=duration=2:frequency=440",
        "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    local = tmp_path / "local.mp4"
    editing.local_view(source, local, .5, 1.5)
    assert abs(float(pipeline.probe(local)["format"]["duration"]) - 1) < 2 / 24
    assert any(s["codec_type"] == "audio" for s in pipeline.probe(local)["streams"])
    value = edit_plan()
    value["segments"] = [{**value["segments"][0], "out_s": 2,
        "look": {"type": "color_reveal", "at_s": .5, "duration_s": .5}}]
    catalog = [{**sources()[0], "path": str(source), "sha256": pipeline.sha(source)}]
    compiled = editing.compile_plan(value, catalog, None, 2)
    final = editing.render(compiled, tmp_path / "render", source, pipeline.probe(source))
    def pixel(time):
        data = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(time), "-i", str(final),
            "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            check=True, capture_output=True).stdout
        return tuple(data[:3])
    before, after = pixel(.1), pixel(1.5)
    assert max(before) - min(before) < 6
    assert after[0] > after[1] + 100 and after[0] > after[2] + 100
    assert abs(float(pipeline.probe(final)["format"]["duration"]) - 2) < 1 / 24
    assert pipeline.sha(source) == catalog[0]["sha256"]
