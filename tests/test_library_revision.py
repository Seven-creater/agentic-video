"""Exercise an explicitly authorized revision with real synthetic FFmpeg media.

Only MCP replies are fixtures. These checks cover authorization, bounded calls,
source evidence, actual hold/caption rendering and immutable legacy history;
they do not assess model creativity or successful editing-style transfer.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from test_library_execute import (
    _bridge, _editing_fixture_responses, _execute, _fixture_responses,
    _read, _run, inputs,
)
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import LibraryState, LibraryStopped


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Real FFmpeg/FFprobe integration requires both tools",
)


def _apis():
    # Import at execution time so the shared synthetic fixtures remain usable
    # while the revision implementation is being assembled.
    from omni_story.library.revision import (
        authorize_editing_revision, execute_editing_revision,
    )
    return authorize_editing_revision, execute_editing_revision


def _name(job):
    return job["job_id"].split("_", 2)[2]


def _revision_responses(reference, library, output, *, omit_first_binding=False,
                        observation_conflict=False):
    legacy = _fixture_responses(reference, library)
    editing = _editing_fixture_responses(reference, library)

    def answer(job):
        name = _name(job)
        if not name.startswith("revision_2_"):
            return legacy(job)
        if name == "revision_2_methods":
            return editing({**job, "job_id": "glm_000_editing_reference_v2"})
        window_id = _read(output / "watched_windows.json")[0]["window_id"]
        if name == "revision_2_select_windows":
            return {"reason": "reuse the already observed stable square", "window_ids": [window_id]}
        if name.startswith("revision_2_observe_"):
            return {"editing_observations": [{
                "method_id": "method_0", "local_start_s": 0.5, "local_end_s": 2.5,
                "observed_form": "colored square is stable in a continuous frame",
                "potential_use": "preserve subject visibility during the actual tail-frame hold",
                "limitations": [],
            }], "observation_conflicts": ["synthetic reread conflicts with historical usable action"]
                if observation_conflict else []}
        if name.startswith("revision_2_plan"):
            value = editing({**job, "job_id": "glm_000_plan_0"})
            if omit_first_binding and name == "revision_2_plan":
                value.pop("editing_bindings")
            return value
        if name == "revision_2_blind":
            return legacy({**job, "job_id": "glm_000_blind_0"})
        if name.startswith("revision_2_review"):
            return editing({**job, "job_id": "glm_000_review_0"})
        if name == "revision_2_select_render":
            return {"selected_round": 2, "reason": "fixture revision includes the actual held tail and caption"}
        raise AssertionError("Unexpected revision job: " + name)

    return answer


def _history(output):
    """Snapshot legacy evidence and media, excluding appendable state/status."""
    root_files = (
        "result.json", "input_lock.json", "reference_reading.json", "reference_asr.json",
        "reference_inventory.json", "library_inventory.json", "coarse_index.json",
        "watched_windows.json", "plan_0.json", "blind_0.json", "review_0.json",
    )
    found = {output / name for name in root_files if (output / name).is_file()}
    for directory in ("calls", "render_0", "render_1", "media_cache"):
        found.update(path for path in (output / directory).rglob("*") if path.is_file())
    return {str(path.relative_to(output)): path.read_bytes() for path in found}


def _assert_history_unchanged(output, before):
    for relative, contents in before.items():
        assert (output / relative).read_bytes() == contents, relative


def test_revision_requires_authorization_before_new_calls_or_history_changes(inputs):
    _, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _fixture_responses(reference, library)) as requests:
        baseline = _execute(inputs)
        before = {str(path.relative_to(output)): path.read_bytes()
                  for path in output.rglob("*") if path.is_file()}
        with pytest.raises(LibraryStopped, match="authoriz"):
            revise(reference, library, output)
        assert len(requests) == baseline["usage"]["requests"] == 10
        after = {str(path.relative_to(output)): path.read_bytes()
                 for path in output.rglob("*") if path.is_file()}
        assert after == before
        assert not (output / "render_2").exists()


def test_authorized_revision_preserves_history_budget_and_cached_resume(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output)) as requests:
        baseline = _execute(inputs)
        before = _history(output)
        state_before = _read(output / "library_state.json")
        authorize(output, "需要")
        authorized_state = (output / "library_state.json").read_bytes()
        authorize(output, "需要")
        assert (output / "library_state.json").read_bytes() == authorized_state
        assert _read(output / "library_state.json")["input_lock"] == state_before["input_lock"]
        assert _read(output / "library_state.json")["max_requests"] == 24
        assert _read(output / "library_state.json")["request_count"] == 10
        _assert_history_unchanged(output, before)

        revised = revise(reference, library, output)
        assert len(requests) == revised["usage"]["requests"] == 17
        assert revised["usage"]["max_requests"] == 24
        assert revised["selected_round"] == 2
        final = output / "render_2" / "final.mp4"
        assert Path(revised["final_video"]) == final
        assert revised["final_sha256"] == sha256_file(final)
        assert _read(output / "result_revision_2.json") == revised
        assert _read(output / "result.json") == baseline
        assert not (output / "render_3").exists()
        assert len(_read(output / "watched_windows.json")) == 1
        state = _read(output / "library_state.json")
        assert state["input_lock"] == state_before["input_lock"]
        assert state["max_requests"] == state_before["max_requests"]
        assert state["calls"][:10] == state_before["calls"]
        assert [_name(job) for job in requests[10:]] == [
            "revision_2_methods", "revision_2_select_windows",
            "revision_2_observe_" + _read(output / "watched_windows.json")[0]["window_id"],
            "revision_2_plan", "revision_2_blind", "revision_2_review", "revision_2_select_render",
        ]
        _assert_history_unchanged(output, before)
        final_before = final.read_bytes()
        revised_before = (output / "result_revision_2.json").read_bytes()
        all_request_bytes = {str(path.relative_to(output)): path.read_bytes()
                             for path in (output / "calls").glob("*/request.json")}
        resumed = revise(reference, library, output)
        assert resumed == revised
        assert len(requests) == 17
        from omni_story.library.__main__ import main
        assert main(["--reference", str(reference), "--library", str(library),
                     "--output", str(output), "--revise-editing"]) == 0
        assert len(requests) == 17
        assert final.read_bytes() == final_before
        assert (output / "result_revision_2.json").read_bytes() == revised_before
        assert {str(path.relative_to(output)): path.read_bytes()
                for path in (output / "calls").glob("*/request.json")} == all_request_bytes
        _assert_history_unchanged(output, before)

    _run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"])
    assert probe_media(final)["duration_s"] == pytest.approx(2.5, abs=0.05)
    manifest = _read(output / "render_2" / "render_result.json")
    row = manifest["provenance"][0]
    assert (row["motion_frames"], row["freeze_frames"], row["frames"]) == (60, 15, 75)
    assert (row["source_in_s"], row["source_out_s"]) == (1.5, 3.5)
    assert row["source_sha256"] == sha256_file(library / "a.mkv")
    assert row["freeze_source"]["source_sha256"] == row["source_sha256"]
    assert row["caption"]["text"] == "红色方块\n保持可见"
    assert (row["caption"]["start_frame"], row["caption"]["end_frame"]) == (60, 75)
    assert revised["review"]["method_checks"][0]["audio_status"] == "not_applicable"
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(final), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    size = 160 * 240 * 3
    frames = [raw[index:index + size] for index in range(0, len(raw), size)]
    def white_pixels(frame):
        return sum(all(channel > 185 for channel in frame[index:index + 3])
                   for index in range(0, len(frame), 3))
    assert len(frames) == 75
    assert all(white_pixels(frame) == 0 for frame in frames[:60])
    assert all(white_pixels(frame) > 30 for frame in frames[60:])


def test_revision_missing_binding_has_one_counted_format_repair(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output, omit_first_binding=True)) as requests:
        _execute(inputs)
        before = _history(output)
        authorize(output, "需要")
        revised = revise(reference, library, output)
        assert len(requests) == revised["usage"]["requests"] == 18
        _assert_history_unchanged(output, before)
    state = _read(output / "library_state.json")
    original = next(call for call in state["calls"] if call["name"] == "revision_2_plan")
    repair = next(call for call in state["calls"] if call["name"] == "revision_2_plan_repair")
    assert repair["repair_of"] == original["id"]
    assert original["status"] == repair["status"] == "received"
    failure = _read(output / "calls" / original["id"] / "protocol_failure.json")
    assert "editing_bindings" in failure["error"]
    assert not (output / "calls" / original["id"] / "parsed.json").exists()
    assert not (output / "render_3").exists()


def test_authorization_is_bound_to_original_result_before_any_new_call(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output)) as requests:
        _execute(inputs)
        authorize(output, "需要")
        original = (output / "result.json").read_bytes()
        value = json.loads(original)
        value["limitations"] = ["tampered synthetic history"]
        (output / "result.json").write_text(json.dumps(value), encoding="utf-8")
        try:
            with pytest.raises(ValueError, match="historical_artifact_changed"):
                revise(reference, library, output)
            assert len(requests) == 10
            assert _read(output / "library_state.json")["request_count"] == 10
            assert not (output / "render_2").exists()
        finally:
            (output / "result.json").write_bytes(original)


def test_authorized_revision_does_not_extend_original_request_budget(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output)) as requests:
        _execute(inputs)
        recorded = _read(output / "library_state.json")
        state = LibraryState(output, recorded["input_lock"], max_requests=24)
        for index in range(14):
            call, _ = state.begin_call("synthetic_consumed_" + str(index), {"synthetic_fixture": index})
            state.complete_call(call, {"synthetic_known_reply": index})
        authorize(output, "需要")
        before = _history(output)
        with pytest.raises(LibraryStopped, match="model_request_budget_exhausted"):
            revise(reference, library, output)
        assert len(requests) == 10
        assert _read(output / "library_state.json")["request_count"] == 24
        assert _read(output / "library_state.json")["max_requests"] == 24
        assert not (output / "render_2").exists()
        assert not any(call["name"].startswith("revision_2_")
                       for call in _read(output / "library_state.json")["calls"])
        _assert_history_unchanged(output, before)


def test_authorization_cannot_replay_uncertain_media_or_observation_lineage(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output)) as requests:
        _execute(inputs)
        recorded = _read(output / "library_state.json")
        state = LibraryState(output, recorded["input_lock"], max_requests=24)
        digest = sha256_file(reference)
        call, _ = state.begin_call("synthetic_uncertain_reference", {
            "tool": "analyze_video", "arguments": {"video_source": str(reference), "prompt": "synthetic lost reply"},
            "media_sha256": digest,
            "observation_scope": {"kind": "complete_file", "source_sha256": digest,
                                  "source_start_s": 0, "source_end_s": probe_media(reference)["duration_s"]},
        })
        state.fail_call(call, "synthetic original response not captured", uncertain=True)
        state.enable_independent_continuation()
        authorize(output, "需要")
        before = _history(output)
        with pytest.raises(LibraryStopped, match="unknown_media_must_not_be_resubmitted"):
            revise(reference, library, output)
        assert len(requests) == 10
        state_after = _read(output / "library_state.json")
        assert state_after["request_count"] == 11
        assert next(item for item in state_after["calls"] if item["id"] == call["id"])["status"] == "uncertain"
        assert not (output / "render_2").exists()
        assert not any(item["name"].startswith("revision_2_") for item in state_after["calls"])
        _assert_history_unchanged(output, before)


def test_reread_conflict_blocks_historical_ranges_without_rewriting_them(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output, observation_conflict=True)) as requests:
        _execute(inputs)
        before = _history(output)
        assert _read(output / "watched_windows.json")[0]["observation"]["usable_ranges"]
        authorize(output, "需要")
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted:revision_2_plan"):
            revise(reference, library, output)
        assert len(requests) == 15
        state = _read(output / "library_state.json")
        assert state["request_count"] == 15
        original = next(call for call in state["calls"] if call["name"] == "revision_2_plan")
        repair = next(call for call in state["calls"] if call["name"] == "revision_2_plan_repair")
        assert repair["repair_of"] == original["id"]
        assert "range_not_supported_by_fine_observation" in _read(
            output / "calls" / original["id"] / "protocol_failure.json")["error"]
        planning = _read(output / "artifacts" / "editing_revision_v1" / "planning_windows.json")[0]
        assert planning["observation"]["usable_ranges"] == []
        assert planning["observation"]["revision_edl_permission"] == "blocked_due_to_observation_conflicts"
        assert not (output / "render_2").exists()
        assert not (output / "result_revision_2.json").exists()
        _assert_history_unchanged(output, before)


def test_cached_revision_reply_tampering_stops_without_replay_or_new_render(inputs):
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output)) as requests:
        _execute(inputs)
        legacy = _history(output)
        authorize(output, "需要")
        revised = revise(reference, library, output)
        final_before = Path(revised["new_video"]).read_bytes()
        result_before = (output / "result_revision_2.json").read_bytes()
        state = _read(output / "library_state.json")
        call = next(item for item in state["calls"] if item["name"] == "revision_2_methods")
        path = output / "calls" / call["id"] / "response.json"
        original = path.read_bytes()
        tampered = json.loads(original)
        tampered["synthetic_tampering"] = True
        path.write_text(json.dumps(tampered), encoding="utf-8")
        try:
            with pytest.raises(LibraryStopped, match="recorded_model_request_or_reply_modified"):
                revise(reference, library, output)
            assert len(requests) == 17
            assert _read(output / "library_state.json")["request_count"] == 17
            assert Path(revised["new_video"]).read_bytes() == final_before
            assert (output / "result_revision_2.json").read_bytes() == result_before
            assert not (output / "render_3").exists()
            _assert_history_unchanged(output, legacy)
        finally:
            path.write_bytes(original)


def test_revision_skips_received_legacy_fine_reply_without_parsed_evidence(inputs):
    """A transport reply alone cannot displace completed, valid observation evidence."""
    authorize, revise = _apis()
    reference, library, output = inputs
    with _bridge(output, _revision_responses(reference, library, output)) as requests:
        _execute(inputs)
        recorded = _read(output / "library_state.json")
        state = LibraryState(output, recorded["input_lock"], max_requests=24)
        call, folder = state.begin_call("fine_unusable_legacy", {
            "synthetic_failed_fine_observation": "received reply with unusable protocol content",
        })
        reply = {"status": "complete", "result": {"content": [
            {"type": "text", "text": "synthetic invalid JSON response"},
        ]}}
        state.complete_call(call, reply)
        assert not (folder / "parsed.json").exists()
        invalid_before = _read(output / "library_state.json")["calls"][-1]
        assert invalid_before["status"] == "received"
        assert _read(output / "library_state.json")["request_count"] == 11
        before = _history(output)
        authorize(output, "需要")
        revised = revise(reference, library, output)
        # The recorded invalid legacy call counts toward budget but was never
        # dispatched through this fake bridge, so transport-job count is 17.
        assert revised["usage"]["requests"] == 18
        assert len(requests) == 17
        assert revised["selected_round"] == 2
        assert not (folder / "parsed.json").exists()
        assert _read(folder / "response.json") == reply
        assert next(item for item in _read(output / "library_state.json")["calls"]
                    if item["id"] == call["id"]) == invalid_before
        _assert_history_unchanged(output, before)
        resumed = revise(reference, library, output)
        assert resumed == revised
        assert resumed["usage"]["requests"] == 18
        assert len(requests) == 17
        _assert_history_unchanged(output, before)
