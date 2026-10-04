"""Synthetic-media, production-queue continuation checks, not GLM quality claims."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from omni_story.library.extension_budget import authorize, stage_state
from omni_story.library.finecut_continuation import execute_finecut_continuation
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.pipeline import execute
from omni_story.library.reference_craft import execute_reference_craft
from omni_story.library.state import LibraryStopped
from test_library_active_finecut_execute import _responses
from test_library_execute import _bridge, _editing_fixture_responses, _read, inputs
from test_library_reference_craft import responses as craft_responses

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                               reason="Production media integration requires FFmpeg/FFprobe")


def _name(job):
    return job["job_id"].split("_", 2)[2]


def _history(output):
    return {p: p.read_bytes() for p in output.rglob("*") if p.is_file()
            and p.name not in {"library_state.json", "mcp_current.json", "mcp_ready.json", "mcp_tools.json"}}


def _setup(inputs, *, permission=True):
    reference, library, output = inputs
    with _bridge(output, _editing_fixture_responses(reference, library)):
        baseline = execute(reference, library, output, span_s=3, frames=2, max_fine=1,
                           max_requests=80, asr=False, editing_v2=True)
    ref = _read(output / "reference_catalog/inventory.json")["sources"][0]
    selection, analysis = craft_responses(ref)
    with _bridge(output, lambda job: selection if "craft_scan" in _name(job) else analysis):
        craft = execute_reference_craft(reference, library, output)
    assert baseline["usage"]["requests"] == 11 and craft["usage"]["requests"] == 13
    history = _history(output)
    if permission:
        authorize(output, "synthetic user task authorization; no real GLM request")
    return history


def _answer(inputs, *, redundant=False, malformed=False, fail_at=None, bad_range=False):
    reference, library, output = inputs
    base = _responses(reference, library, redundant=redundant)
    def answer(job):
        name = _name(job)
        if fail_at and name in {fail_at, fail_at + "_repair"}:
            return {"invalid": "synthetic protocol failure"}
        mapped = {"active_4_draft": "plan_0", "active_4_finecut": "finecut_0",
                  "active_4_blind": "blind_0", "active_4_economy": "economy_0", "active_4_review": "review_0"}
        actual = name.removesuffix("_repair")
        target = mapped.get(actual, name)
        if name.endswith("_repair") and actual == "active_4_finecut":
            target = "finecut_0_repair"
        translated = {**job, "job_id": "glm_000_" + target}
        value = base(translated)
        if actual == "active_4_draft":
            assert value["segments"][0]["visual_claims"]
            if bad_range:
                value["segments"][0]["source_out_s"] = 99
        if malformed and name == "active_4_finecut":
            value["decisions"].pop()
        return value
    return answer


@pytest.mark.parametrize("redundant,malformed", [(False, False), (True, False), (False, True)])
def test_one_authorized_render_independent_final_slices_and_cached_resume(inputs, redundant, malformed):
    history = _setup(inputs)
    reference, library, output = inputs
    original_calls = deepcopy(_read(output / "library_state.json")["calls"])
    with _bridge(output, _answer(inputs, redundant=redundant, malformed=malformed)) as jobs:
        result = execute_finecut_continuation(reference, library, output)
        assert len(jobs) == result["usage"]["extension_requests"] == (10 if malformed else 9)
        assert result["usage"]["requests"] == len(original_calls) + len(jobs)
        assert result["usage"]["base_max_requests"] == 80
        assert result["usage"]["extension_max_requests"] is None
        assert result["usage"]["max_requests"] is None
        assert result["usage"]["effective_request_limit"] is None
        assert result["request_limit_policy"] == "progress_guard_no_numeric_request_cap_v1"
        assert _read(output / "library_state.json")["max_requests"] == 80
        assert result["active_finecut_gate_passed"] is (not redundant)
        assert result["semantic_gate_passed"] is True
        assert result["status"] == ("library_candidate_with_limitations" if redundant else "model_checked_library_candidate")
        assert result["selected_round"] == 4
        assert result["new_renders"] == 1 and result["new_unique_fine_windows"] == 0
        assert not any("search" in _name(j) or "reference" in _name(j) or "select" in _name(j) for j in jobs)
        rendered = _read(output / "render_4/render_result.json")
        assert [(s["source_in_s"], s["source_out_s"], s["speed"]) for s in rendered["provenance"]] == [(1.5, 2, 2), (3, 3.5, .5)]
        assert rendered["provenance"][-1]["freeze_tail_s"] == .2
        assert probe_media(result["final_video"])["duration_s"] == pytest.approx(1.466667, abs=.05)
        facts = [j for j in jobs if _name(j).startswith("semantic_slice_4_")]
        claims = [j for j in jobs if _name(j).startswith("semantic_claims_4_")]
        assert len(facts) == len(claims) == 2
        for job in facts:
            prompt = job["arguments"]["prompt"]
            assert "slot_1" not in prompt and "red_square" not in prompt and "required_claims：" not in prompt
            media = _read(Path(job["arguments"]["video_source"]).parent / "lineage.json")
            assert (media["source_start_s"], media["source_end_s"]) in {(1.5, 2), (3, 3.5)}
        manifest = _read(result["semantic_evidence_path"])
        assert manifest["comparison_mode"] == "separate_per_slice"
        assert len(manifest["observations"]) == len(manifest["segment_checks"]) == 2
        assert any(c.get("origin") == "draft_obligation" for c in manifest["required_claims"])
        economy = next(j for j in jobs if _name(j) == "active_4_economy")
        assert "fixture selected interval" not in economy["arguments"]["prompt"]
        blind = next(j for j in jobs if _name(j) == "active_4_blind")
        assert "slot_1" not in blind["arguments"]["prompt"]
        assert all(path.read_bytes() == content for path, content in history.items())
        assert _read(output / "library_state.json")["calls"][:len(original_calls)] == original_calls
        before_resume = _history(output)
        (output / "mcp_ready.json").unlink()
        (output / "mcp_stop").write_text("stopped fixture connection", encoding="utf-8")
        assert execute_finecut_continuation(reference, library, output) == result
        assert len(jobs) == result["usage"]["extension_requests"]
        assert all(path.read_bytes() == content for path, content in before_resume.items())
    assert sha256_file(result["final_video"]) == result["final_sha256"]
    assert len(list(output.glob("render_*/final.mp4"))) == 2  # historical 0 and sole new 4


def test_missing_permission_refuses_before_any_new_file_or_model(inputs):
    _setup(inputs, permission=False)
    reference, library, output = inputs
    before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(LibraryStopped, match="authorization_required"):
        execute_finecut_continuation(reference, library, output)
    after = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    assert before == after
    assert not (output / "render_4").exists()


@pytest.mark.parametrize("stage", ["active_4_blind", "active_4_economy", "active_4_review"])
def test_known_output_protocol_failure_delivers_real_candidate_without_verdict_or_replay(inputs, stage):
    history = _setup(inputs)
    reference, library, output = inputs
    with _bridge(output, _answer(inputs, fail_at=stage)) as jobs:
        result = execute_finecut_continuation(reference, library, output)
        assert result["review"] is None and result["review_status"] == "incomplete_protocol_failure"
        assert result["semantic_gate_passed"] is result["active_finecut_gate_passed"] is False
        assert result["status"] == "library_candidate_with_limitations"
        assert result["review_failure"]["stage"] == stage
        assert len(result["review_failure"]["calls"]) == 2
        assert Path(result["final_video"]).is_file()
        state = _read(output / "library_state.json")
        originals = [c for c in state["calls"] if c["name"] == stage]
        repairs = [c for c in state["calls"] if c["name"] == stage + "_repair"]
        assert len(originals) == len(repairs) == 1
        assert repairs[0]["repair_of"] == originals[0]["id"]
        assert all(not (output / "calls" / c["id"] / "parsed.json").exists() for c in originals + repairs)
        assert all(path.read_bytes() == content for path, content in history.items())
        count = len(jobs)
        assert execute_finecut_continuation(reference, library, output) == result
        assert len(jobs) == count


def test_unobserved_source_cut_is_not_repaired_by_hand_or_rendered(inputs):
    history = _setup(inputs)
    reference, library, output = inputs
    with _bridge(output, _answer(inputs, bad_range=True)) as jobs:
        for _ in range(2):
            with pytest.raises(ValueError, match="model_protocol_repair_exhausted:active_4_draft"):
                execute_finecut_continuation(reference, library, output)
            assert len(jobs) == 2
    assert not (output / "render_4").exists()
    assert not (output / "result_active_finecut_4.json").exists()
    assert all(path.read_bytes() == content for path, content in history.items())


@pytest.mark.parametrize("status", ["submitted", "uncertain"])
def test_unsettled_extension_call_never_creates_another_submission(inputs, status):
    _setup(inputs)
    state = stage_state(inputs[2])
    call, _ = state.begin_call("active_4_draft", {"test": "unknown synthetic transport outcome"})
    if status == "uncertain":
        state.fail_call(call, "synthetic lost response", uncertain=True)
    count = state.usage()["requests"]
    with pytest.raises(ValueError, match="unsettled_extension_call_no_new_submission"):
        execute_finecut_continuation(*inputs)
    assert state.usage()["requests"] == count
    assert not list((inputs[2] / "mcp_queue").glob(call["id"] + ".request.json"))
    assert _read(inputs[2] / "library_state.json")["calls"][-1]["status"] == status


@pytest.mark.parametrize("target", ["plan", "video", "result"])
def test_cached_completion_rejects_changed_new_artifacts_without_new_calls(inputs, target):
    _setup(inputs)
    with _bridge(inputs[2], _answer(inputs)) as jobs:
        result = execute_finecut_continuation(*inputs)
        count = len(jobs)
        path = {"plan": inputs[2] / "artifacts/active_finecut_continuation_v1/plan.json",
                "video": Path(result["final_video"]), "result": inputs[2] / "result_active_finecut_4.json"}[target]
        path.write_text('{"changed":"fixture corruption"}', encoding="utf-8")
        with pytest.raises(ValueError, match="result_changed|completed_file_changed"):
            execute_finecut_continuation(*inputs)
        assert len(jobs) == count
