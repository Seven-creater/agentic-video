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
                      for i in (1, 2)], "coverage_limitations": [], "editing_intentions": [
                          {"method_id": "D1", "material_ids": ["M1", "M2"],
                           "coverage_intent": "synthetic meaningful moments", "limitations": []}]}


def edit_plan():
    return {"segments": [{"material_id": "M" + str(i), "in_s": 0, "out_s": 0.75,
        "speed": 1.0, "fade_in_s": 0, "fade_out_s": 0, "purpose": "synthetic info"} for i in (1, 2)],
        "music": {"enabled": False, "mode": "trim", "gain_db": -10, "fade_in_s": 0, "fade_out_s": 0},
        "rationale": "synthetic model decision", "limitations": [], "style_mapping": [
            {"method_id": "D1", "status": "adapted", "segment_indices": [0, 1],
             "explanation": "synthetic evidence selected"}]}


def refresh_synthetic_reference(out, video):
    """Tests replace placeholder bytes with FFmpeg media; rebind only this synthetic parent."""
    lineage = p.load(out / "input_lineage.json")
    lineage["video_sha256"] = sha(video)
    write(out / "input_lineage.json", lineage)
    reading = p.load(out / "reference_reading.json")
    duration = float(p.probe(video)["format"]["duration"])
    reading["sections"][0]["end_s"] = duration
    write(out / "reference_reading.json", reading)
    transfer = p.reference.build_transfer(reading, duration, sha(video), sha(out / "reference_reading.json"))
    write(out / "reference_transfer.json", transfer)
    write(out / "manifest.json", [{"path": str(f.relative_to(out)), "sha256": sha(f)}
        for f in out.rglob("*") if f.is_file() and f.name != "manifest.json"])


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


@pytest.mark.parametrize("use_frame", [False, True])
def test_h3_text_only_is_explicit_and_keeps_image_contract(tmp_path, use_frame):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"synthetic image")
    backend = MiniMaxH3.__new__(MiniMaxH3)
    backend.cfg = {"model": "synthetic", "resolution": "768P", "max_poll_seconds": 1}
    seen = []
    def http(method, resource, payload=None):
        seen.append((method, payload))
        if method == "POST":
            return {"http_status": 200, "body": {"task_id": "synthetic-task"}}
        raise RuntimeError("synthetic query interrupted")
    backend.http = http
    job = tmp_path / "job"
    supplied_frame = frame if use_frame else None
    for _ in range(2):
        with pytest.raises(RuntimeError, match="query interrupted"):
            backend.generate(job, "synthetic motion", supplied_frame, 10, "16:9")
    posts = [payload for method, payload in seen if method == "POST"]
    assert len(posts) == 1
    assert posts[0]["content"][0] == {"type": "text", "text": "synthetic motion"}
    assert len(posts[0]["content"]) == (2 if use_frame else 1)
    request = json.loads((job / "request.json").read_text())
    assert request["first_frame_sha256"] == (sha(frame) if use_frame else None)
    if use_frame:
        assert posts[0]["content"][1]["role"] == "first_frame"
    else:
        assert request["generation_mode"] == "text_to_video"
        with pytest.raises(ValueError, match="input_changed"):
            backend.generate(job, "synthetic motion", frame, 10, "16:9")
        assert len([method for method, _ in seen if method == "POST"]) == 1


@pytest.mark.parametrize("lose_retry_response", [False, True])
def test_h3_balance_recovery_needs_approval_preserves_failure_and_never_replays(tmp_path, lose_retry_response):
    backend = MiniMaxH3.__new__(MiniMaxH3)
    backend.cfg = {"model": "synthetic", "resolution": "768P", "max_poll_seconds": 1}
    posts = []
    def http(method, resource, payload=None):
        if method != "POST":
            raise RuntimeError("synthetic query interrupted")
        posts.append(deepcopy(payload))
        if len(posts) == 1:
            return {"http_status": 402, "body": {"error": {"type": "insufficient_balance_error"}}}
        if lose_retry_response:
            raise RuntimeError("synthetic retry reply lost")
        return {"http_status": 200, "body": {"task_id": "synthetic-task"}}
    backend.http = http
    job = tmp_path / "job"
    with pytest.raises(RuntimeError, match="submission_rejected"):
        backend.generate(job, "p", None, 10, "16:9")
    original_response_sha = sha(job / "submit_response.json")
    original_request_sha = sha(job / "request.json")
    with pytest.raises(RuntimeError, match="no_automatic_POST"):
        backend.generate(job, "p", None, 10, "16:9")
    assert len(posts) == 1
    with pytest.raises(RuntimeError, match="reply lost|query interrupted"):
        backend.generate(job, "p", None, 10, "16:9", balance_retry_approved=True)
    assert len(posts) == 2 and posts[0] == posts[1]
    assert sha(job / "submit_response.json") == original_response_sha
    assert sha(job / "request.json") == original_request_sha
    assert (job / "balance_recharge_retry/authorization.json").exists()
    with pytest.raises(RuntimeError, match="no_automatic_POST|query interrupted"):
        backend.generate(job, "p", None, 10, "16:9", balance_retry_approved=True)
    assert len(posts) == 2


@pytest.mark.parametrize("response", [
    {"http_status": 500, "body": {}},
    {"http_status": 402, "body": {"error": {"type": "other_error"}}},
    {"http_status": 402, "body": {"error": {"type": "insufficient_balance_error"}, "task_id": "possible-task"}},
])
def test_h3_balance_recovery_cannot_replay_uncertain_or_other_rejection(tmp_path, response):
    backend = MiniMaxH3.__new__(MiniMaxH3)
    backend.cfg = {"model": "synthetic", "resolution": "768P"}
    calls = []
    def http(method, *args):
        calls.append(method)
        return response
    backend.http = http
    job = tmp_path / "job"
    with pytest.raises(RuntimeError, match="submission_rejected"):
        backend.generate(job, "p", None, 10, "16:9")
    with pytest.raises(RuntimeError, match="no_automatic_POST"):
        backend.generate(job, "p", None, 10, "16:9", balance_retry_approved=True)
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


def test_only_literal_master_aliases_normalize_without_rewriting_prompts(tmp_path):
    _, out, _, _ = run_story(tmp_path)
    story = p.load(out / "screenplay.json")
    value = production_plan()
    for row in value["assets"]:
        row["asset_id"] += "_master"
    for row in value["materials"]:
        row["asset_ids"] = [v + "_master" for v in row["asset_ids"]]
    normalized = p.normalize_plan_ids(value, story)
    p.validate_plan(normalized, story, p.LIMITS)
    assert normalized["asset_id_aliases"] == {"C1_master": "C1", "L1_master": "L1"}
    assert normalized["materials"][0]["video_prompt"] == value["materials"][0]["video_prompt"]
    value["assets"][0]["asset_id"] = "new_person_master"
    with pytest.raises(ValueError, match="asset_coverage"):
        p.validate_plan(p.normalize_plan_ids(value, story), story, p.LIMITS)


def test_raw_identifier_recovery_uses_no_new_model_request(tmp_path):
    class NeverCall:
        cfg = {"model": "synthetic"}
        def request(self, *args, **kwargs):
            pytest.fail("must not pay for another request")
    calls = p.Calls(tmp_path, NeverCall(), p.code_snapshot(), {"model_calls": 2})
    identity = json_sha({"prompt": "p", "payload": {}, "media_shas": [],
                         "model_config": NeverCall.cfg, "tokens": 6500})
    write(tmp_path / "calls/one/request.json", {"identity": identity})
    (tmp_path / "calls/one/raw.txt").write_text('{"id":"C1_master"}')
    normalize = lambda v: {"id": v["id"].removesuffix("_master")}
    assert calls.call("one", "p", {}, p.validate_object, normalizer=normalize) == {"id": "C1"}
    assert p.load(tmp_path / "calls/one/normalization.json")["additional_model_calls"] == 0


def test_parent_manifest_and_reference_identity(tmp_path):
    _, out, _, video = run_story(tmp_path)
    p.verify_parent(out, video)
    (out / "asset_bible.json").write_text("{}")
    with pytest.raises(ValueError, match="manifest_mismatch"):
        p.verify_parent(out, video)


def test_frame_review_does_not_freeze_story_initial_prop_state():
    material = {"asset_ids": ["P"], "entry_state": "object is open", "main_action": "inspect contents"}
    bible = {"P": {"id": "P", "visible_description": "red box", "initial_state": "object is closed"}}
    original = deepcopy((material, bible))
    intent = p.frame_review_intent(material, bible)
    assert intent["asset_descriptions"]["P"] == {"id": "P"}
    assert "initial_state" not in intent["asset_descriptions"]["P"]
    assert "story_initial_state" not in intent["asset_descriptions"]["P"]
    assert intent["material"]["entry_state"] == "object is open"
    assert intent["state_scope"]["candidate_start_state"] == "material.entry_state and material.start_frame_prompt"
    assert (material, bible) == original
    intent["material"]["entry_state"] = "mutated"
    assert material["entry_state"] == "object is open"


def test_frame_review_uses_current_frame_without_story_or_future_states():
    material = {"material_id": "M2", "asset_ids": ["P"], "entry_state": "box is open",
                "start_frame_prompt": "open red box on a table",
                "main_action": "close the box", "exit_state": "box is closed",
                "video_prompt": "after inspection close the box"}
    bible = {"P": {"id": "P", "visible_description": "red box",
                   "initial_state": "locked in a cupboard",
                   "story_initial_state": "stored elsewhere"}}
    original = deepcopy((material, bible))
    intent = p.frame_review_intent(material, bible)
    assert intent["material"] == {key: material[key] for key in
                                  ("material_id", "asset_ids", "entry_state", "start_frame_prompt")}
    assert intent["asset_descriptions"]["P"] == {"id": "P"}
    assert "locked in a cupboard" not in json.dumps(intent)
    assert "stored elsewhere" not in json.dumps(intent)
    assert "close the box" not in json.dumps(intent)
    assert (material, bible) == original
    assert "decision" not in intent


def test_frame_review_preserves_unknown_current_state_and_master_identity_id():
    material = {"asset_ids": ["P"], "entry_state": "unknown"}
    bible = {"P": {"id": "P", "visible_description": "blue case"}}
    intent = p.frame_review_intent(material, bible)
    assert intent["asset_descriptions"] == {"P": {"id": "P"}}
    assert intent["material"]["entry_state"] == "unknown"
    assert "decision" not in intent


def test_frame_review_does_not_copy_lifecycle_states_embedded_in_asset_prose():
    material = {"asset_ids": ["C", "L"], "entry_state": "unknown"}
    bible = {"C": {"id": "C", "visible_identity": "early expression tired, later delighted"},
             "L": {"id": "L", "visible_description": "daylight first, night at the end"}}
    original = deepcopy(bible)
    intent = p.frame_review_intent(material, bible)
    assert intent["asset_descriptions"] == {"C": {"id": "C"}, "L": {"id": "L"}}
    assert "tired" not in json.dumps(intent)
    assert "daylight first" not in json.dumps(intent)
    assert bible == original


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


def test_image_review_policy_change_does_not_reuse_old_decision(tmp_path):
    from omni_story import production_prompts
    class Fake:
        cfg = {"model": "synthetic"}
        seen = 0
        def request(self, *args, **kwargs):
            self.seen += 1
            value = {"decision": "pass", "visible_facts": [], "blocking_issues": [],
                     "risks": ["synthetic minor difference"], "repair_prompt": None}
            return {"http_status": 200, "body_text": json.dumps({"choices": [
                {"message": {"content": json.dumps(value)}}]})}
    fake = Fake()
    calls = p.Calls(tmp_path, fake, p.code_snapshot(), {"model_calls": 2})
    prior = calls.call("frame_review", "synthetic prior policy", {}, p.validate_image_review)
    with pytest.raises(ValueError, match="input_changed"):
        calls.call("frame_review", production_prompts.IMAGE_REVIEW, {}, p.validate_image_review)
    assert fake.seen == 1
    assert p.load(tmp_path / "calls/frame_review/parsed.json") == prior
    review = calls.call("frame_coarse_review", production_prompts.IMAGE_REVIEW, {}, p.validate_image_review)
    assert fake.seen == 2 and review["decision"] == "pass"
    with pytest.raises(ValueError, match="inconsistent"):
        p.validate_image_review({**review, "blocking_issues": ["synthetic major contradiction"]})


def test_only_definitive_missing_json_400_gets_one_recorded_recovery(tmp_path):
    class Fake:
        cfg = {"model": "synthetic"}
        seen = 0
        def request(self, *args, **kwargs):
            self.seen += 1
            return {"http_status": 200, "body_text": json.dumps({"choices": [{"message": {"content": "{}"}}]})}
    fake = Fake()
    calls = p.Calls(tmp_path, fake, p.code_snapshot(), {"model_calls": 3})
    identity = json_sha({"prompt": "p", "payload": {}, "media_shas": [],
                         "model_config": fake.cfg, "tokens": 6500})
    write(tmp_path / "calls/one/request.json", {"identity": identity})
    write(tmp_path / "calls/one/response.json", {"http_status": 400,
        "body_text": "'messages' must contain the word 'json' in some form"})
    assert calls.call("one", "p", {}, p.validate_object) == {}
    assert calls.call("one", "p", {}, p.validate_object) == {} and fake.seen == 1
    assert p.load(tmp_path / "calls/one/response.json")["http_status"] == 400
    assert (tmp_path / "calls/one/transport_recovery.json").exists()
    write(tmp_path / "calls/two/request.json", {"identity": identity})
    write(tmp_path / "calls/two/response.json", {"http_status": 500, "body_text": "uncertain"})
    with pytest.raises(RuntimeError, match="no_automatic_replay"):
        calls.call("two", "p", {}, p.validate_object)
    assert fake.seen == 1


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


def test_review_copy_nonstandard_video_aspect_ratio_has_even_dimensions(tmp_path):
    source, target = tmp_path / "wide.mp4", tmp_path / "review.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=1920x1072:r=24:d=0.5", "-c:v", "libx264", str(source)], check=True)
    editing.review_copy(source, target)
    stream = next(s for s in p.probe(target)["streams"] if s["codec_type"] == "video")
    assert stream["width"] % 2 == stream["height"] % 2 == 0


def test_actual_audio_silence_is_measured_not_inferred_from_edit_plan(tmp_path):
    source = tmp_path / "silent_tail.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=160x90:r=24:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-af", "apad=whole_dur=3", "-c:v", "libx264", "-c:a", "aac", "-t", "3", str(source)], check=True)
    measured = editing.audio_measurements(source)
    assert measured["audio_stream_present"] is True and len(measured["silences"]) == 1
    assert 0.98 < measured["silences"][0]["start_s"] < 1.05
    assert 2.98 < measured["silences"][0]["end_s"] < 3.05


def test_music_only_continuation_uses_last_shared_call_and_keeps_picture(tmp_path, monkeypatch):
    _, out, _, reference = run_story(tmp_path)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=160x90:r=24:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-c:a", "aac", "-t", "3", str(reference)], check=True)
    lineage = p.load(out / "input_lineage.json")
    lineage["video_sha256"] = sha(reference)
    write(out / "input_lineage.json", lineage)
    write(out / "manifest.json", [{"path": str(f.relative_to(out)), "sha256": sha(f)}
                                 for f in out.rglob("*") if f.is_file() and f.name != "manifest.json"])
    prod = out / "production"
    plan = edit_plan()
    for row in plan["segments"]:
        row["out_s"] = 1.5
    plan["music"]["enabled"] = True
    region = {"start_s": 0, "end_s": .5, "basis": "synthetic heard music"}
    catalog = [{**s, "path": str(reference), "sha256": sha(reference)} for s in sources()]
    compiled = editing.compile_plan(plan, catalog, region, 3)
    original = editing.render(compiled, prod / "render_0", reference, p.probe(reference))
    prior = {"status": "model_checked_final_video", "final_video": str(original),
        "final_video_sha256": sha(original), "model_review": {"decision": "accept"},
        "candidate_selection": {"selected_candidate_id": "render_0"}}
    write(prod / "result.json", prior)
    write(prod / "source_inventory.json", catalog)
    write(prod / "reference_editing.json", {"music_region": region})
    write(prod / "edit_plan_0.json", plan)
    for i in range(35):
        write(prod / f"calls/prior_{i}/request.json", {"synthetic": True})
    def forbidden(*args, **kwargs):
        pytest.fail("music continuation cannot instantiate image/video generation")
    monkeypatch.setattr(p, "AliyunImages", forbidden)
    monkeypatch.setattr(p, "MiniMaxH3", forbidden)
    class Omni:
        cfg = {"model": "synthetic Omni"}
        calls = 0
        def request(self, text, **kwargs):
            self.calls += 1
            assert text.startswith(p.prompts.MUSIC_REPAIR) and kwargs.get("media")
            value = {"decision": "adjust", "music": {**plan["music"], "mode": "loop"},
                     "reason": "synthetic model music choice", "limitations": []}
            return {"http_status": 200, "body_text": json.dumps({"choices": [
                {"message": {"content": json.dumps(value)}}]})}
    omni = Omni()
    result = p.continue_music_coverage(reference, out, prior, runner=omni)
    assert result["status"] == "video_candidate_with_limitations"
    assert result["music_coverage_continuation"]["music"]["mode"] == "loop"
    assert result["final_mix_model_review"] == "not_run" and omni.calls == 1
    assert len(list((prod / "calls").glob("*/request.json"))) == 36
    assert result["measured_audio"]["silences"] == []
    assert p.load(prod / "edit_plan_music_1.json")["segments"] == plan["segments"]
    def picture_hash(path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
            "-c", "copy", "-f", "hash", "-hash", "sha256", "-"], check=True, capture_output=True).stdout
    assert picture_hash(result["final_video"]) == picture_hash(original)
    assert p.continue_music_coverage(reference, out, result, runner=omni) == result and omni.calls == 1
    assert sha(original) == prior["final_video_sha256"]


@pytest.mark.parametrize("repair_once,reject_all,style_missing", [(False, False, False), (True, False, False),
    (True, True, False), (False, False, True)])
def test_full_production_fake_backends_and_real_media_tools(tmp_path, repair_once, reject_all, style_missing):
    _, out, _, video = run_story(tmp_path)
    # Replace the fake source with a real video and refresh only the synthetic parent fixture.
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=gray:s=160x90:r=24:d=2", "-c:v", "libx264", str(video)], check=True)
    refresh_synthetic_reference(out, video)
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
        repair_requested = False
        def request(self, text, **kwargs):
            if text.startswith((p.prompts.PLAN, p.prompts.WATCH, p.prompts.EDIT, p.prompts.FINAL_REVIEW)):
                payload = json.loads(text.split("Input: ", 1)[1])
                assert payload["reference_transfer"]["schema_version"] == "reference_transfer_v2"
            if text.startswith(p.prompts.PLAN):
                value = production_plan()
            elif text.startswith(p.prompts.IMAGE_REVIEW):
                assert kwargs.get("images")
                payload = json.loads(text.split("Input: ", 1)[1])
                if reject_all or (repair_once and "material" in payload["intent"] and not self.repair_requested):
                    self.repair_requested = True
                    value = {"decision": "revise", "visible_facts": [], "blocking_issues": ["synthetic mismatch"],
                             "risks": [], "repair_prompt": "synthetic targeted correction"}
                    return {"http_status": 200, "body_text": json.dumps({"choices": [{"message": {"content": json.dumps(value)}}]})}
                if kwargs["images"][0].parent.name.endswith("_repair"):
                    assert len(kwargs["images"]) == 3
                    assert all(path.parent.name.startswith("master_") for path in kwargs["images"][1:])
                    assert payload["labels"] == ["candidate", "identity master 1", "identity master 2"]
                value = {"decision": "pass", "visible_facts": [], "blocking_issues": [], "risks": [], "repair_prompt": None}
            elif text.startswith(p.prompts.SELECT_IMAGE):
                assert len(kwargs["images"]) == 2
                value = {"selected_candidate_id": "master_C1", "reason": "synthetic best available; flaw remains"}
            elif text.startswith(p.prompts.WATCH):
                assert kwargs.get("media")
                mid = json.loads(text.split("Input: ", 1)[1])["material_id"]
                value = {"material_id": mid, "events": [{"start_s": 0, "end_s": 1, "visible": "synthetic"}],
                         "usable_information": [], "limitations": []}
            elif text.startswith(p.prompts.REFERENCE_EDITING):
                pytest.fail("new chain must not issue a late parallel full reference analysis")
            elif text.startswith(p.prompts.EDIT):
                value = edit_plan()
            else:
                assert kwargs.get("media")
                value = {"decision": "accept", "viewer_reading": "synthetic", "reason": "synthetic",
                         "issues": [], "replacement_plan": None, "style_review": [
                             {"method_id": "D1", "status": "not_visible" if style_missing else "adapted",
                              "start_s": None if style_missing else 0, "end_s": None if style_missing else 1,
                              "evidence": "synthetic visible pacing"}]}
            return {"http_status": 200, "body_text": json.dumps({"choices": [{"message": {"content": json.dumps(value)}}]})}
    images, videos = Images(), Videos()
    result = p.execute_production(video, out, runner=Omni(), image_backend=images, video_backend=videos)
    assert result["status"] == ("video_candidate_with_limitations" if style_missing else "model_checked_final_video"), result
    assert images.calls == 4 + int(repair_once) and videos.calls == 2
    assert result["actual_model_calls"] == 9 + int(repair_once) + int(reject_all)
    assert result["style_transfer"]["reference_origin"] == "shared_initial_reference_analysis"
    assert result["human_creative_inputs"] == [] and not result["music_tempo_changed"]
    assert Path(result["final_video"]).is_file()
    again = p.execute_production(video, out, runner=Omni(), image_backend=images, video_backend=videos)
    assert again == result and images.calls == 4 + int(repair_once) and videos.calls == 2
    if reject_all:
        inventory = p.load(out / "production/asset_inventory.json")
        assert inventory["C1"]["review"]["decision"] == "revise"
        assert inventory["C1"]["selection"]["selected_candidate_id"] == "master_C1"


def test_editing_resume_never_constructs_generation_backends(tmp_path, monkeypatch):
    _, out, _, video = run_story(tmp_path)
    prod = out / "production"
    write(prod / "production_plan.json", production_plan())
    for mid in ("M1", "M2"):
        source = prod / "video_jobs" / mid / "video.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"synthetic completed video")
        write(source.parent / "submission.json", {"status": "completed", "path": str(source),
                                                  "sha256": sha(source)})
    def forbidden(*args, **kwargs):
        pytest.fail("editing resume must not initialize image/H3 backends")
    monkeypatch.setattr(p, "AliyunImages", forbidden)
    monkeypatch.setattr(p, "MiniMaxH3", forbidden)
    monkeypatch.setattr(p, "probe", lambda _: {"format": {"duration": "2"}})
    seen = []
    def edit(reference, directory, story, catalog, calls, limits, metadata):
        seen.append(catalog)
        assert [s["measured_duration_s"] for s in catalog] == [2, 2]
        assert limits["model_calls"] == p.LIMITS["model_calls"]
        final = directory / "final.mp4"
        final.write_bytes(b"synthetic final film")
        return {"status": "video_candidate_with_limitations", "final_video": str(final),
                "final_video_sha256": sha(final), "model_review": {"decision": "unable"},
                "audio_measurement_review": True}
    monkeypatch.setattr(p, "edit_sources", edit)
    runner = type("Omni", (), {"cfg": {"model": "synthetic"}})()
    result = p.finish_existing_media(video, out, runner=runner)
    assert result["new_image_jobs"] == result["new_video_jobs"] == 0
    assert result["model_review"]["decision"] == "unable"
    assert p.finish_existing_media(video, out, runner=runner) == result and len(seen) == 1
    Path(result["final_video"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="final_output_changed"):
        p.finish_existing_media(video, out, runner=runner)


def test_unapproved_content_candidate_is_not_a_technical_failure(tmp_path):
    _, out, _, video = run_story(tmp_path)
    result = p.load(out / "result.json")
    result["status"] = "screenplay_needs_review"
    write(out / "result.json", result)
    rows = p.load(out / "manifest.json")
    next(row for row in rows if row["path"] == "result.json")["sha256"] = sha(out / "result.json")
    write(out / "manifest.json", rows)
    assert p.verify_parent(out, video)["status"] == "screenplay_needs_review"
    result["status"] = "blocked"
    write(out / "result.json", result)
    with pytest.raises(ValueError, match="screenplay_not_completed"):
        p.verify_parent(out, video)
