"""Real media/queue/resume checks; fixture replies do not measure editing quality."""
from pathlib import Path
import shutil

import pytest

from omni_story.library.media import inventory_sources, probe_media, sha256_file
from omni_story.library.reference_craft import (
    ALLOCATION, POLICY, execute_reference_craft, load_knowledge, validate_analysis, validate_selection)
from omni_story.library.state import LibraryState, write_json
from test_library_execute import _bridge, _read, inputs

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="Real FFmpeg/FFprobe integration requires both tools")


def setup_task(inputs, max_requests=4):
    reference, library, output = inputs
    ref = inventory_sources(reference, output / "reference_catalog")["sources"][0]
    sources = inventory_sources(library, output / "catalog")["sources"]
    lock = {"reference_sha256": ref["sha256"], "library_sources": [
        {k: s[k] for k in ("source_id", "sha256")} for s in sources],
        "configuration": {"max_fine": 16, "max_renders": 2}}
    state = LibraryState(output, lock, max_requests=max_requests)
    write_json(output / "result_semantic_revision_3.json", {"status": "limited", "usage": 0})
    (output / "render_3").mkdir()
    (output / "render_3" / "final.mp4").write_bytes(b"protected old film")
    return ref, state


def responses(ref):
    selection = {"reference_sha256": ref["sha256"], "visual_takeaway": "fixture observation",
        "coverage": [{"start_s": 0, "end_s": ref["duration_s"], "visible_information": "visible fixture"}],
        "inspection_windows": [
            {"window_id": "w1", "start_s": .5, "end_s": 1.5, "question": "motion?", "card_ids": ["speed_retiming"]},
            {"window_id": "w2", "start_s": 2.5, "end_s": 3.5, "question": "cut?", "card_ids": ["temporal_ellipsis"]}],
        "observation_limits": "cloud sampling unknown"}
    analysis = {"reference_sha256": ref["sha256"], "visual_takeaway": "fixture observation",
        "inspection_answers": [{"window_id": w["window_id"], "answer": "fixture", "remaining_uncertainty": "sampling"}
                               for w in selection["inspection_windows"]],
        "methods": [{"method_id": "m1", "card_id": "unknown", "status": "unknown", "start_s": .5, "end_s": 3.5,
            "visible_evidence": "no conclusive fixture evidence", "alternative_explanation": "static frame",
            "intended_function": "unknown", "material_requirements": "verify actual footage", "operation": "none",
            "implementation": "not_chosen", "application_condition": "only with evidence", "verification": "actual output",
            "reference_speed_factor": None, "uncertainty": "unknown"}],
        "audio_rhythm_status": "unverified", "remaining_gaps": "outside inspected range"}
    return selection, analysis


def test_queue_executes_model_ranges_and_preserves_old_outputs(inputs):
    ref, state = setup_task(inputs)
    reference, library, output = inputs
    before = {p: p.read_bytes() for p in [output / "result_semantic_revision_3.json", output / "render_3" / "final.mp4"]}
    selection, analysis = responses(ref)
    def answer(job):
        return selection if "craft_scan" in job["job_id"] else analysis
    with _bridge(output, answer) as received:
        result = execute_reference_craft(reference, library, output)
    assert len(received) == result["new_requests"] == 2
    assert result["new_renders"] == result["new_library_fine_windows"] == 0
    media = result["inspection"]
    assert (media["source_start_s"], media["source_end_s"]) == (.5, 3.5)
    assert media["source_offset_s"] == .5 and media["spec"]["fps"] == 30
    assert abs(probe_media(media["path"])["duration_s"] - 3) < .1
    assert media["source_sha256"] == sha256_file(reference)
    assert all(p.read_bytes() == content for p, content in before.items())
    snapshot = Path(_read(output / "library_state.json")["artifacts"][ALLOCATION][0]["path"])
    allocation = _read(snapshot)
    assert allocation["reserved_requests"] == 4
    assert load_knowledge(allocation["knowledge_snapshot"])["sha256"] == allocation["knowledge_sha256"]
    # Completed resume needs neither a live MCP nor another paid call.
    (output / "mcp_ready.json").unlink()
    (output / "mcp_stop").write_text("stopped")
    assert execute_reference_craft(reference, library, output) == result
    assert _read(output / "library_state.json")["request_count"] == 2
    write_json(output / "result_semantic_revision_3.json", {"tampered": True})
    with pytest.raises(ValueError, match="historical_file_changed"):
        execute_reference_craft(reference, library, output)


def test_two_repairs_exhaust_four_reserved_calls_without_replay(inputs):
    ref, state = setup_task(inputs)
    reference, library, output = inputs
    selection, analysis = responses(ref)
    def answer(job):
        if job["job_id"].endswith("craft_scan"):
            return {"bad": "format"}
        if "craft_scan_repair" in job["job_id"]:
            return selection
        return {"bad": "format"}  # Both original and bounded repair fail.
    with _bridge(output, answer) as received:
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted"):
            execute_reference_craft(reference, library, output)
        assert len(received) == 4
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted"):
            execute_reference_craft(reference, library, output)
        assert len(received) == 4
    assert state.usage()["requests"] == 4
    assert not state.data["artifacts"].get("reference_craft_result")


@pytest.mark.parametrize("mutation,error", [
    (lambda s: s["inspection_windows"][0].update(start_s=-1), "finite_number"),
    (lambda s: s["inspection_windows"][0].update(end_s=99), "out_of_bounds"),
    (lambda s: s["inspection_windows"][0].update(card_ids=["../private"]), "unknown_or_duplicate"),
    (lambda s: s["coverage"][0].update(start_s=.1), "coverage_gap"),
])
def test_selection_rejects_unbound_ranges_and_cards(inputs, mutation, error):
    ref, _ = setup_task(inputs)
    selection, _ = responses(ref)
    mutation(selection)
    with pytest.raises(ValueError, match=error):
        validate_selection(selection, ref, load_knowledge())


@pytest.mark.parametrize("changes,error", [
    ({"start_s": 0}, "outside_completed_inspection"),
    ({"reference_speed_factor": .5}, "unmeasured_reference_speed"),
    ({"operation": "smooth_speed_ramp", "implementation": "supported"}, "unavailable_operation"),
])
def test_analysis_cannot_claim_unwatched_evidence_or_unimplemented_effects(inputs, changes, error):
    ref, _ = setup_task(inputs)
    selection, analysis = responses(ref)
    analysis["methods"][0].update(changes)
    with pytest.raises(ValueError, match=error):
        validate_analysis(analysis, ref, load_knowledge(), selection, {"source_start_s": .5, "source_end_s": 3.5})


def test_insufficient_budget_does_not_create_allocation(inputs):
    _, state = setup_task(inputs, max_requests=3)
    with pytest.raises(ValueError, match="four_request"):
        execute_reference_craft(*inputs)
    assert state.usage()["requests"] == 0
    assert not state.data["artifacts"].get(ALLOCATION)


def test_old_continuation_resume_cannot_rewrite_historical_usage(inputs, monkeypatch):
    from omni_story.library import semantic_continuation
    ref, _ = setup_task(inputs)
    selection, analysis = responses(ref)
    output = inputs[2]
    old = (output / "result_semantic_revision_3.json").read_bytes()
    with _bridge(output, lambda job: selection if "craft_scan" in job["job_id"] else analysis):
        execute_reference_craft(*inputs)
    monkeypatch.setattr(semantic_continuation, "_authorization", lambda state: {})
    monkeypatch.setattr(semantic_continuation, "_execute", lambda *args: pytest.fail("old film must not execute again"))
    result = semantic_continuation.execute_semantic_continuation(*inputs)
    assert result["usage"] == 0
    assert (output / "result_semantic_revision_3.json").read_bytes() == old


@pytest.mark.parametrize("target,error", [
    ("media", "inspection_media_changed"),
    ("lineage", "inspection_lineage_changed"),
    ("response", "recorded_response_modified"),
    ("parsed", "parsed_model_content_changed"),
])
def test_completed_resume_rejects_changed_new_evidence(inputs, target, error):
    ref, state = setup_task(inputs)
    selection, analysis = responses(ref)
    with _bridge(inputs[2], lambda job: selection if "craft_scan" in job["job_id"] else analysis):
        result = execute_reference_craft(*inputs)
    media = Path(result["inspection"]["path"])
    if target == "media":
        media.write_bytes(b"changed proxy")
    elif target == "lineage":
        write_json(media.parent / "lineage.json", {"wrong_mapping": True})
    else:
        call_id = result["model_bindings"]["craft_inspect"]["call_id"]
        write_json(inputs[2] / "calls" / call_id / (target + ".json"), {"changed": True})
    with pytest.raises(ValueError, match=error):
        execute_reference_craft(*inputs)
    assert state.usage()["requests"] == 2
