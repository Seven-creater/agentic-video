from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from omni_story import pipeline as p, prompts


def plan():
    return {"schema_version": "story_plan_v2", "title": "synthetic model choice",
        "intended_takeaway": "synthetic abstract message",
        "characters": [{"id": "C1", "visible_identity": "fixed visible appearance"}],
        "locations": [{"id": "L1", "visible_description": "fixed room layout"}], "props": [],
        "units": [{"unit_id": f"U{i}", "caused_by": [] if i == 1 else ["U1"],
            "character_ids": ["C1"], "location_id": "L1", "prop_ids": [],
            "entry_state": "state before", "character_goal": "visible goal", "event_summary": "visible event",
            "exit_state": "state after", "intended_viewer_update": "author effect", "elapsed_time_hint": None}
            for i in (1, 2)]}


class FakeAPI:
    cfg = {"model": "synthetic", "reference_fps_requested": 2.0}

    def __init__(self, *, mode="normal"):
        self.seen = []
        self.mode = mode
        self.review_count = 0

    def request(self, request, *, media=None, tokens=0):
        self.seen.append((request, media))
        payload = json.loads(request[request.index("Input: ") + len("Input: "):])
        if self.mode == "transport":
            raise RuntimeError("synthetic connection failure")
        if media:
            value = {"schema_version": "autonomous_reference_reading_v2", "sections": [{
                "section_id": "S1", "start_s": 0, "end_s": 37.5, "visible_events": ["source event"],
                "video_text_statements": ["attributed statement"], "audible_events": [],
                "new_information": "source information", "interpretation_hypothesis": "tentative interpretation",
                "editing_observation": "synthetic pacing observation"}],
                "audience_takeaway": "source abstract meaning", "initial_viewer_judgment": "unknown",
                "updated_viewer_judgment": "unknown", "ending_effect": "source closing", "uncertainties": [],
                "editing": {"methods": [{"method_id": "D1", "section_ids": ["S1"],
                    "start_s": 0, "end_s": 1, "status": "observed", "observation": "synthetic pace",
                    "purpose_hypothesis": "synthetic function", "adaptation_goal": "select informative moments"}],
                    "music_region": None, "uncertainties": [], "inspection_requests": []}}
        elif request.startswith(prompts.ROUTES):
            value = {"schema_version": "autonomous_routes_v1", "reference_relation": {
                "audience_takeaway": "abstract meaning", "information_sequence": ["function"],
                "evidence_mechanism": "mechanism", "ending_function": "closing", "uncertainties": []},
                "routes": [{"route_id": f"R{i}", "title": "model idea", "premise": "model premise",
                    "visible_action_and_result": "model visible outcome", "why_original": "different event",
                    "production_risks": []} for i in (1, 2, 3)], "selected_route_id": "R2",
                "selection_reason": "model selection"}
        elif request.startswith(prompts.REVIEW):
            self.review_count += 1
            blocked = self.mode in {"block", "revise"} and (self.mode == "block" or self.review_count == 1)
            invalid = self.mode == "protocol" and self.review_count == 1
            value = {"schema_version": "autonomous_review_v1", "verdict": "revise" if blocked else "pass",
                "issues": [{"path": "/title", "severity": "blocking", "kind": "contradiction",
                            "reason": "synthetic explicit contradiction"}] if blocked else [], "limitations": []}
            if invalid:
                value.pop("verdict")
        elif request.startswith("Repair only"):
            value = {"schema_version": "autonomous_review_v1", "verdict": "pass", "issues": [], "limitations": []}
        elif request.startswith(prompts.BLIND):
            value = {"schema_version": "autonomous_blind_v1", "viewer_takeaway": "inferred meaning",
                "sequence_reading": [{"unit_id": u["unit_id"], "reading": "visible inference"}
                                     for u in payload["visible_story"]["units"]],
                "verdict": "usable", "issues": [], "limitations": []}
        elif request.startswith(prompts.ALIGNMENT):
            value = {"schema_version": "autonomous_alignment_v1", "structure_similarity": "soft preference",
                **{key: {"status": "supported", "reason": "mapping"} for key in ("takeaway", "evidence_mechanism")},
                "limitations": []}
        elif request.startswith(prompts.SELECT_AVAILABLE):
            value = {"selected_candidate_id": "draft_1", "reason": "synthetic best available; issues remain"}
        elif request.startswith(prompts.OUTLINE) or request.startswith(prompts.REVISION):
            value = plan()
        else:
            unit = deepcopy(payload["current_unit"])
            unit.pop("event_summary")
            value = {**unit, "schema_version": "story_segment_v2", "state_after": "state after",
                "dialogue_or_voiceover": None, "on_screen_text": None, "omittable_processes": [],
                "events": [{"event_id": unit["unit_id"] + "_E1", "before": "state before",
                    "action": "visible model action", "after": "state after",
                    "observable_evidence": "visible result", "essential": True}]}
        body = {"choices": [{"message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        return {"http_status": 200, "body_text": json.dumps(body), "elapsed_s": 0.1}


def inputs(tmp_path):
    video = tmp_path / "second.mp4"
    video.write_bytes(b"synthetic input only")
    return video, tmp_path / "run"


def measured(_video):
    return {"format": {"duration": "37.533"}, "streams": [{"codec_type": "video"}]}


def run(tmp_path, api=None):
    video, output = inputs(tmp_path)
    api = api or FakeAPI()
    result = p.execute(video, output, runner=api, media_probe=measured)
    return result, output, api, video


def test_full_autonomy_video_only_and_prefix_handoff(tmp_path):
    result, output, api, video = run(tmp_path)
    assert result["status"] == "model_checked_screenplay_candidate"
    assert result["actual_model_calls"] == len(api.seen) == 10
    assert sum(media is not None for _, media in api.seen) == 1
    assert result["human_approval_used"] is False and result["media_verified"] is False
    assert not result["production_release_allowed"] and not result["image_video_generation"]
    second = next(text for text, _ in api.seen if text.startswith(prompts.SEGMENT)
                  and '"unit_id":"U2"' in text.split("Input: ", 1)[1].split('"current_unit"', 1)[1])
    payload = json.loads(second.split("Input: ", 1)[1])
    assert payload["prior_state_after"] == "state after"
    assert len(payload["locked_previous_segments"]) == 1
    blind = next(text for text, _ in api.seen if text.startswith(prompts.BLIND))
    assert "intended_takeaway" not in blind and "intended_viewer_update" not in blind
    assert "abstract_reference_relation" not in blind and "source information" not in blind
    assert (output / "screenplay.json").is_file() and (output / "asset_bible.json").is_file()
    for row in json.loads((output / "manifest.json").read_text(encoding="utf-8")):
        assert p.sha(output / row["path"]) == row["sha256"]
    assert json.loads((output / "input_lineage.json").read_text())["semantic_inputs"] == ["reference_video_only"]
    assert video.read_bytes() == b"synthetic input only"


def test_repeat_output_and_new_directory_cannot_reset_budget(tmp_path):
    _, output, api, video = run(tmp_path)
    with pytest.raises(FileExistsError):
        p.execute(video, output, runner=api, media_probe=measured)
    with pytest.raises(FileExistsError):
        p.execute(video, tmp_path / "reset", runner=api, media_probe=measured)
    assert len(api.seen) == 10


def test_auto_revision_uses_only_model_feedback(tmp_path):
    result, _, api, _ = run(tmp_path, FakeAPI(mode="revise"))
    assert result["status"] == "model_checked_screenplay_candidate"
    assert len(api.seen) == 12
    revised = next(text for text, _ in api.seen if text.startswith(prompts.REVISION))
    assert "synthetic explicit contradiction" in revised
    assert "human_story" not in revised and "user_accepted" not in revised


def test_exhausted_revision_never_fakes_pass(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    api = FakeAPI(mode="block")
    loop = p.Loop(output, api, p.code_snapshot())
    result = loop.stage("outline", prompts.OUTLINE, {}, lambda v: p.contract.validate_outline(v, v))
    assert result == plan() and len(api.seen) == 5
    assert loop.state["stages"]["outline"]["status"] == "best_available_with_limitations"
    assert loop.state["stages"]["outline"]["selected_candidate_id"] == "draft_1"
    assert json.loads((output / "outline_review_1.json").read_text())["verdict"] == "revise"
    assert json.loads((output / "outline_review_2.json").read_text())["verdict"] == "revise"
    assert (output / "outline_draft_2.json").is_file()
    assert (output / "outline_selection.json").is_file()


def test_protocol_repair_not_story_feedback(tmp_path):
    result, output, api, _ = run(tmp_path, FakeAPI(mode="protocol"))
    assert result["status"] == "model_checked_screenplay_candidate" and len(api.seen) == 11
    assert len(list(output.glob("calls/*/validation.json"))) == 11
    assert sum(text.startswith("Repair only") for text, _ in api.seen) == 1


def test_transport_failure_is_recorded_without_retry(tmp_path):
    result, output, api, _ = run(tmp_path, FakeAPI(mode="transport"))
    assert result["status"] == "blocked" and len(api.seen) == 1
    assert (output / "run_failure.json").is_file()
    assert len(list(output.glob("calls/*/request.txt"))) == 1


def test_global_budget_stops_before_next_request(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "MAX_CALLS", 3)
    result, _, api, _ = run(tmp_path)
    assert result["status"] == "blocked" and result["reason"] == "model_call_budget_exhausted"
    assert len(api.seen) == 3


def test_unknown_reference_change_and_long_process_are_legal():
    value = plan()
    value["units"][0]["elapsed_time_hint"] = "hours"
    p.contract.validate_outline(value, value)
    assert "judgment reversal" in prompts.BLIND
    assert "need not involve" in prompts.ROUTES
    assert not any(name in "\n".join(prompts.ALL.values()) for name in
                   ("老人厨师", "攀岩者", "修瓷", "sculptor", "user_accepted"))


def test_explicit_identity_and_unknown_ids_are_structural_failures():
    value = plan()
    value["units"][0]["character_ids"] = ["C99"]
    with pytest.raises(ValueError, match="unknown_or_duplicate_ref"):
        p.contract.validate_outline(value, value)


def test_unresolved_or_risk_review_cannot_be_reported_as_blocking():
    review = {"schema_version": "autonomous_review_v1", "verdict": "pass",
              "issues": [{"path": "/title", "severity": "risk", "kind": "detail", "reason": "not tested"}]}
    p.validate_review(review, plan())
    review["verdict"] = "revise"
    with pytest.raises(ValueError, match="inconsistent"):
        p.validate_review(review, plan())


def test_cli_has_no_human_override_or_story_input():
    proc = subprocess.run([sys.executable, "-m", "omni_story", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "--video" in proc.stdout
    assert not any(flag in proc.stdout for flag in ("--parent", "--accept", "--seed", "--story", "--approve"))


def test_standalone_modules_never_import_original_repo():
    import ast
    for path in (p.ROOT / "omni_story").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 10))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("src.", "scripts.", "final_route"))


def test_api_keeps_key_out_of_command_line_and_output(monkeypatch):
    from omni_story.api import QwenAPI
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-secret")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/v1")
    def fake(cmd, **kwargs):
        assert "unit-test-secret" not in " ".join(cmd)
        assert b"unit-test-secret" in kwargs["input"]
        return type("Process", (), {"returncode": 0, "stdout": b'{"message":"unit-test-secret"}\n200'})()
    monkeypatch.setattr(subprocess, "run", fake)
    api = QwenAPI()
    value = api.request("test")
    assert "unit-test-secret" not in json.dumps(value) and "unit-test-secret" not in json.dumps(api.cfg)
