from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from omni_story import editing, production as p
from omni_story.media_backends import ImageHelper, MiniMaxH3, claim
from omni_story.pipeline import json_sha, sha, write
from test_pipeline import plan as story_plan, run as run_story


def production_plan():
    return {"schema_version": "production_plan_v1", "assets": [
        {"asset_id": i, "master_prompt": "synthetic image"} for i in ("C1", "L1")],
        "materials": [{"material_id": "M" + str(i), "unit_ids": ["U" + str(i)],
                       "event_ids": ["U" + str(i) + "_E1"], "asset_ids": ["C1", "L1"],
                       "generation_duration_s": 8,
                       **{k: "synthetic visible process" for k in ("entry_state", "main_action", "exit_state",
                          "essential_evidence", "start_frame_prompt", "video_prompt")}}
                      for i in (1, 2)], "coverage_limitations": [], "editing_intentions": []}


def edit_plan():
    return {"segments": [{"material_id": "M" + str(i), "in_s": 0, "out_s": 0.75,
        "speed": 1.0, "fade_in_s": 0, "fade_out_s": 0, "purpose": "synthetic info"} for i in (1, 2)],
        "music": {"enabled": False, "mode": "trim", "gain_db": -10, "fade_in_s": 0, "fade_out_s": 0},
        "rationale": "synthetic model decision", "limitations": []}


def sources():
    return [{"material_id": "M" + str(i), "measured_duration_s": 2,
             "path": "synthetic", "sha256": "synthetic"} for i in (1, 2)]


def test_paid_job_claim_is_exclusive_and_inputs_are_bound(tmp_path):
    value, fresh = claim(tmp_path / "job", "identity")
    assert fresh and value["status"] == "submission_claimed"
    assert claim(tmp_path / "job", "identity")[1] is False
    with pytest.raises(ValueError, match="input_changed"):
        claim(tmp_path / "job", "different")


def test_uncertain_h3_submission_never_posts_again(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"synthetic")
    backend = MiniMaxH3.__new__(MiniMaxH3)
    backend.cfg = {"model": "synthetic"}
    calls = []
    def failed(method, *args):
        calls.append(method)
        raise RuntimeError("synthetic lost POST reply")
    backend.http = failed
    with pytest.raises(RuntimeError, match="lost POST"):
        backend.generate(tmp_path / "job", "p", frame, 8, "16:9")
    with pytest.raises(RuntimeError, match="no_automatic_POST"):
        backend.generate(tmp_path / "job", "p", frame, 8, "16:9")
    assert calls == ["POST"]


def test_image_resume_checks_output_sha_without_helper(tmp_path):
    backend = ImageHelper.__new__(ImageHelper)
    backend.artifact_root = tmp_path / "artifacts"
    backend.artifact_root.mkdir()
    backend.cfg = {"model": "synthetic"}
    identity = json_sha({"prompt": "p", "references": [], "size": "1536x1024", "config": backend.cfg})
    target = backend.artifact_root / (identity + ".png")
    target.write_bytes(b"synthetic valid cached bytes")
    write(tmp_path / "job/submission.json", {"identity": identity, "status": "completed", "sha256": sha(target)})
    assert backend.generate(tmp_path / "job", "p")["status"] == "completed"
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="output_changed"):
        backend.generate(tmp_path / "job", "p")


def test_measured_source_bounds_no_duplicate_or_stretched_bgm():
    compiled = editing.compile_plan(edit_plan(), sources(), None, 37.5)
    assert compiled["duration_s"] == 1.5 and compiled["segments"][1]["timeline_start_s"] == .75
    for bad, error in (("range", "number_invalid"), ("duplicate", "duplicate"), ("stretch", "music_policy")):
        plan = edit_plan()
        if bad == "range":
            plan["segments"][0]["out_s"] = 8  # requested length cannot stand in for measured 2 seconds
        elif bad == "duplicate":
            plan["segments"].append(deepcopy(plan["segments"][0]))
        else:
            plan["music"]["mode"] = "stretch"
        with pytest.raises(ValueError, match=error):
            editing.compile_plan(plan, sources(), None, 37.5)


def test_plan_keeps_story_material_and_edit_time_separate(tmp_path):
    _, out, _, _ = run_story(tmp_path)
    story = json.loads((out / "screenplay.json").read_text(encoding="utf-8"))
    p.validate_plan(production_plan(), story, p.LIMITS)
    plan = production_plan()
    plan["materials"][0]["in_s"] = 0
    with pytest.raises(ValueError, match="premature_source_slice"):
        p.validate_plan(plan, story, p.LIMITS)
    plan = production_plan()
    plan["materials"][1]["unit_ids"] = ["U1"]
    with pytest.raises(ValueError, match="event_unit_mismatch"):
        p.validate_plan(plan, story, p.LIMITS)


def test_parent_manifest_and_reference_identity(tmp_path):
    _, out, _, video = run_story(tmp_path)
    p.verify_parent(out, video)
    (out / "asset_bible.json").write_text("{}")
    with pytest.raises(ValueError, match="manifest_mismatch"):
        p.verify_parent(out, video)


def test_calls_cache_and_budget_do_not_replay(tmp_path):
    class Fake:
        cfg = {"model": "synthetic"}
        seen = 0
        def request(self, *args, **kwargs):
            self.seen += 1
            return {"http_status": 200, "body_text": json.dumps({"choices": [{"message": {"content": "{}"}}]})}
    fake = Fake()
    calls = p.Calls(tmp_path, fake, p.code_snapshot(), {"model_calls": 1})
    calls.call("one", "p", {}, p.validate_object)
    calls.call("one", "p", {}, p.validate_object)
    assert fake.seen == 1
    with pytest.raises(ValueError, match="budget"):
        calls.call("two", "p", {}, p.validate_object)
    with pytest.raises(ValueError, match="input_changed"):
        calls.call("one", "p", {"changed": True}, p.validate_object)


def test_real_renderer_and_cache_use_actual_model_ranges(tmp_path):
    paths = []
    for i, color in enumerate(("blue", "red")):
        path = tmp_path / f"M{i+1}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c={color}:s=160x90:r=24:d=2", "-c:v", "libx264", str(path)], check=True)
        paths.append(path)
    catalog = [{**s, "path": str(path), "sha256": sha(path)} for s, path in zip(sources(), paths)]
    compiled = editing.compile_plan(edit_plan(), catalog, None, 37.5)
    metadata = {"streams": [{"codec_type": "audio"}, {"codec_type": "video", "width": 160, "height": 90}]}
    final = editing.render(compiled, tmp_path / "render", paths[0], metadata)
    assert final.is_file() and abs(float(p.probe(final)["format"]["duration"]) - 1.5) < 1 / 24
    assert editing.render(compiled, tmp_path / "render", paths[0], metadata) == final
    changed = deepcopy(compiled)
    changed["segments"][0]["speed"] = 1.5
    with pytest.raises(ValueError, match="output_changed"):
        editing.render(changed, tmp_path / "render", paths[0], metadata)


def test_full_production_fake_backends_and_real_media_tools(tmp_path):
    _, out, _, video = run_story(tmp_path)
    # Replace the fake source with a real video and refresh only the synthetic parent fixture.
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=gray:s=160x90:r=24:d=2", "-c:v", "libx264", str(video)], check=True)
    lineage = p.load(out / "input_lineage.json")
    lineage["video_sha256"] = sha(video)
    write(out / "input_lineage.json", lineage)
    write(out / "manifest.json", [{"path": str(f.relative_to(out)), "sha256": sha(f)}
                                for f in out.rglob("*") if f.is_file() and f.name != "manifest.json"])
    class Images:
        cfg = {"model": "synthetic image"}
        calls = 0
        def generate(self, directory, prompt, references=(), **kwargs):
            self.calls += 1
            directory.mkdir(parents=True)
            image = directory / "image.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", str(image)], check=True)
            value = {"status": "completed", "path": str(image), "sha256": sha(image)}
            write(directory / "submission.json", value)
            return value
    class Videos:
        cfg = {"model": "synthetic H3"}
        calls = 0
        def generate(self, directory, *args):
            self.calls += 1
            directory.mkdir(parents=True)
            target = directory / "video.mp4"
            target.write_bytes(video.read_bytes())
            value = {"status": "completed", "path": str(target), "sha256": sha(target), "measured_duration_s": 2}
            write(directory / "submission.json", value)
            return value
    class Omni:
        cfg = {"model": "synthetic Omni"}
        def request(self, text, **kwargs):
            if text.startswith(p.prompts.PLAN):
                value = production_plan()
            elif text.startswith(p.prompts.IMAGE_REVIEW):
                assert kwargs.get("images")
                value = {"decision": "pass", "visible_facts": [], "blocking_issues": [], "risks": [], "repair_prompt": None}
            elif text.startswith(p.prompts.WATCH):
                assert kwargs.get("media")
                mid = json.loads(text.split("Input: ", 1)[1])["material_id"]
                value = {"material_id": mid, "events": [{"start_s": 0, "end_s": 1, "visible": "synthetic"}],
                         "usable_information": [], "limitations": []}
            elif text.startswith(p.prompts.REFERENCE_EDITING):
                value = {"observed_editing": [], "transferable_preferences": [], "uncertain": [], "music_region": None}
            elif text.startswith(p.prompts.EDIT):
                value = edit_plan()
            else:
                assert kwargs.get("media")
                value = {"decision": "accept", "viewer_reading": "synthetic", "reason": "synthetic",
                         "issues": [], "replacement_plan": None}
            return {"http_status": 200, "body_text": json.dumps({"choices": [{"message": {"content": json.dumps(value)}}]})}
    images, videos = Images(), Videos()
    result = p.execute_production(video, out, runner=Omni(), image_backend=images, video_backend=videos)
    assert result["status"] == "model_checked_final_video", result
    assert images.calls == 4 and videos.calls == 2
    assert result["actual_model_calls"] == 10
    assert result["human_creative_inputs"] == [] and not result["music_tempo_changed"]
    assert Path(result["final_video"]).is_file()
    again = p.execute_production(video, out, runner=Omni(), image_backend=images, video_backend=videos)
    assert again == result and images.calls == 4 and videos.calls == 2
