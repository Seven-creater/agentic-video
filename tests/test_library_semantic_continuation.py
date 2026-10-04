"""Append-only semantic continuation with actual synthetic FFmpeg media.

Only the external MCP replies are fixtures. These checks establish bounded
execution, independent inputs, provenance and recovery; they are not evidence
that GLM learned a reference's editing methods or produced a good real story.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from test_library_execute import (
    _bridge, _editing_fixture_responses, _fixture_responses, _read, _run, inputs,
)
from test_library_revision import _name, _revision_responses
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.pipeline import execute
from omni_story.library.state import LibraryState, LibraryStopped, json_sha


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Real FFmpeg/FFprobe integration requires both tools",
)


def _apis():
    from omni_story.library.semantic_continuation import (
        authorize_semantic_continuation, execute_semantic_continuation,
    )
    return authorize_semantic_continuation, execute_semantic_continuation


def _baseline_and_revision(inputs):
    from omni_story.library.revision import (
        authorize_editing_revision, execute_editing_revision,
    )
    reference, library, output = inputs
    baseline = execute(reference, library, output, span_s=3, frames=2,
                       max_fine=1, max_requests=40, asr=False)
    authorize_editing_revision(output, "需要")
    previous = execute_editing_revision(reference, library, output)
    assert baseline["usage"]["requests"] == 10
    assert previous["usage"]["requests"] == 17
    return baseline, previous


def _protected_history(output):
    """Snapshot every existing file except the two intentionally appendable ones."""
    return {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*")
            if p.is_file() and p.name not in {"library_state.json", "current_status.json"}}


def _assert_protected_history(output, before):
    for relative, raw in before.items():
        assert (output / relative).read_bytes() == raw, relative


def _decode_after(prompt, marker):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


def _continuation_responses(reference, library, output, *, unsupported=False,
                            missing_coverage_once=False, missing_coverage_always=False,
                            two_slices=False):
    from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL
    legacy = _fixture_responses(reference, library)
    editing = _editing_fixture_responses(reference, library)
    previous = _revision_responses(reference, library, output)

    def answer(job):
        name = _name(job)
        prompt = job["arguments"]["prompt"]
        if name.startswith("continuation_3_reference"):
            reading = legacy({**job, "job_id": "glm_000_reference"})
            methods = editing({**job, "job_id": "glm_000_editing_reference_v2"})
            methods["methods"][0].update(
                form="continuous geometric view", source_start_s=0, source_end_s=4,
                verification_rule="subject stays visible during the output")
            result = {"reference": reading, "editing_reference": methods,
                "coverage": [{"start_s": 0, "end_s": 4,
                              "observed_content": "yellow synthetic field throughout"}],
                "observation_strategy": "inspect the entire four-second input and its changes"}
            if missing_coverage_once and name == "continuation_3_reference":
                result["coverage"][0]["end_s"] = 3
            if missing_coverage_always:
                result["coverage"] = [
                    {"start_s": 0, "end_s": 1, "observed_content": "yellow synthetic field"},
                    {"start_s": 2, "end_s": 3, "observed_content": "yellow synthetic field remains"}]
            return result
        if name.startswith("continuation_3_plan"):
            plan = editing({**job, "job_id": "glm_000_plan_0"})
            segment = plan["segments"][0]
            segment.pop("caption")
            segment.pop("freeze_tail_s")
            segment["visual_claims"] = [{"claim_id": "vc_stable", "kind": "visual_action",
                                         "description": "square remains visible"}]
            plan["editing_bindings"][0].update(
                operation="play the independently selected source range",
                verification="check visibility in the actual output")
            if two_slices:
                second = deepcopy(segment)
                segment.update(source_in_s=1.5, source_out_s=2, speed=2)
                second.update(segment_id="segment_2", source_in_s=3, source_out_s=3.5, speed=.5,
                    visual_claims=[{"claim_id": "vc_second", "kind": "visual_action",
                                    "description": "square remains visible in the second slice"}])
                plan["segments"].append(second)
                plan["slots"][0]["segment_ids"].append("segment_2")
                plan["editing_bindings"][0]["segment_ids"].append("segment_2")
            return plan
        if name.startswith("semantic_slice_3_"):
            value = _decode_after(prompt, "metadata：")
            value.update(characters=[{"character_id": "observed_1", "appearance": "red geometric square"}],
                evidence=[{"evidence_id": "source_visible", "kind": "visual_action", "local_start_s": 0,
                    "local_end_s": value["observed_duration_s"], "description": "red square visible on black field",
                    "character_ids": ["observed_1"], "basis_evidence_ids": []}], uncertainties=[])
            return value
        if name.startswith("semantic_claims_batch_3"):
            records = _decode_after(prompt, "\nrecords：")
            checks = []
            for record in records:
                observation = record["observation"]
                checks.append({"protocol": SEMANTIC_PROTOCOL, "segment_id": observation["segment_id"],
                    "observation_sha256": json_sha(observation), "claim_checks": [
                        {"claim_id": claim["claim_id"],
                         "status": "unsupported" if unsupported and claim["kind"] == "visual_action" else "supported",
                         "evidence_ids": [] if unsupported and claim["kind"] == "visual_action" else ["source_visible"],
                         "reason": "synthetic independent evidence comparison",
                         "limitations": ["synthetic required action absent"]
                            if unsupported and claim["kind"] == "visual_action" else []}
                        for claim in record["required_claims"]], "uncertainties": []})
            return {"protocol": SEMANTIC_PROTOCOL, "segment_checks": checks}
        if name.startswith("continuation_3_blind"):
            value = legacy({**job, "job_id": "glm_000_blind_0"})
            value.update(**_decode_after(prompt, "绑定："), text_dependency="none")
            value["evidence"][0].update(evidence_id="blind_visible", claim_id="blind_stable",
                kind="visual_action", basis_evidence_ids=[])
            return value
        if name.startswith("continuation_3_review"):
            value = editing({**job, "job_id": "glm_000_review_0"})
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
            blind = context["blind_reading"]
            claims = context["required_claims"] + [
                {"claim_id": evidence["claim_id"], "kind": evidence["kind"]}
                for evidence in blind["evidence"]]
            value.update(protocol=SEMANTIC_PROTOCOL, video_sha256=context["video_sha256"],
                visual_narrative_status="partial" if unsupported else "pass", fact_checks=[
                    {"claim_id": claim["claim_id"],
                     "status": "unsupported" if unsupported and claim["kind"] == "visual_action"
                        and claim["claim_id"] != "blind_stable" else "supported",
                     "evidence_refs": [{"segment_id": claim.get("segment_id") or "segment_1",
                                        "evidence_id": "source_visible"}],
                     "blind_evidence_ids": [], "reason": "synthetic exact-slice fact comparison",
                     "limitations": ["synthetic required action absent"]
                        if unsupported and claim["kind"] == "visual_action"
                           and claim["claim_id"] != "blind_stable" else []}
                    for claim in claims], contradictions=[])
            value["method_checks"][0]["output_evidence"] = [{
                "start_s": 0, "end_s": context["output_duration_s"],
                "observed_fact": "red square remains visible in the actual synthetic render"}]
            if unsupported:
                value.update(theme_status="partial", editing_status="partial")
            return value
        return previous(job)

    return answer


def test_continuation_requires_authorization_before_new_calls_or_files(inputs):
    _, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output)) as jobs:
        _baseline_and_revision(inputs)
        before = {str(p.relative_to(output)): p.read_bytes()
                  for p in output.rglob("*") if p.is_file()}
        with pytest.raises(LibraryStopped, match="requires_explicit_user_authorization"):
            continuation(reference, library, output)
        assert len(jobs) == _read(output / "library_state.json")["request_count"] == 17
        assert {str(p.relative_to(output)): p.read_bytes()
                for p in output.rglob("*") if p.is_file()} == before
        assert not (output / "render_3").exists()


@pytest.mark.parametrize("unsupported", [False, True])
def test_authorized_continuation_preserves_old_records_and_resumes_without_calls(inputs, unsupported):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output, unsupported=unsupported)) as jobs:
        baseline, previous = _baseline_and_revision(inputs)
        history = _protected_history(output)
        state_before = _read(output / "library_state.json")
        policy = authorize(output, "那就进行端到端跑一遍")
        authorization_state = (output / "library_state.json").read_bytes()
        assert authorize(output, "那就进行端到端跑一遍") == policy
        assert (output / "library_state.json").read_bytes() == authorization_state
        assert policy["baseline_request_count"] == 17
        assert policy["max_requests"] == 40 and policy["render_index"] == 3
        assert policy["authorized_additional_renders"] == 1
        _assert_protected_history(output, history)

        result = continuation(reference, library, output)
        assert len(jobs) == result["usage"]["requests"] == 23
        assert result["usage"]["max_requests"] == 40
        assert result["semantic_gate_passed"] is (not unsupported)
        assert result["status"] == ("library_candidate_with_limitations" if unsupported
                                     else "model_checked_library_candidate")
        assert result["selected_round"] == 3 and result["effective_render_limit"] == 4
        assert result["actual_fine_windows"] == 1 and result["new_unique_fine_windows"] == 0
        assert result["human_creative_inputs"] == []
        final = output / "render_3" / "final.mp4"
        assert Path(result["final_video"]) == final
        assert result["final_sha256"] == sha256_file(final)
        assert _read(output / "result_semantic_revision_3.json") == result
        assert _read(output / "result.json") == baseline
        assert _read(output / "result_revision_2.json") == previous
        assert not (output / "render_4").exists()
        state_after = _read(output / "library_state.json")
        assert state_after["input_lock"] == state_before["input_lock"]
        assert state_after["calls"][:17] == state_before["calls"]
        _assert_protected_history(output, history)

        reference_job = next(j for j in jobs if _name(j) == "continuation_3_reference")
        assert sha256_file(reference_job["arguments"]["video_source"]) == sha256_file(reference)
        prompt = reference_job["arguments"]["prompt"]
        for leaked in ("5–17", "5到17", "断臂", "踢腿", "功夫熊猫", "9.07",
                       "visible geometric subject", "one visible subject is maintained"):
            assert leaked not in prompt
        reading = _read(result["reference_analysis_path"])
        assert reading["coverage"][0]["start_s"] == 0
        assert reading["coverage"][-1]["end_s"] == pytest.approx(probe_media(reference)["duration_s"])
        source_job = next(j for j in jobs if _name(j).startswith("semantic_slice_3_"))
        source_prompt = source_job["arguments"]["prompt"]
        assert "square remains visible" not in source_prompt
        assert "slot_1" not in source_prompt and "red_square" not in source_prompt
        lineage = _read(Path(source_job["arguments"]["video_source"]).parent / "lineage.json")
        assert (lineage["source_start_s"], lineage["source_end_s"]) == (1.5, 3.5)
        assert lineage["source_sha256"] == sha256_file(library / "a.mkv")

        resumed_history = _protected_history(output)
        calls_after = _read(output / "library_state.json")["calls"]
        assert continuation(reference, library, output) == result
        assert len(jobs) == 23
        assert _read(output / "library_state.json")["calls"] == calls_after
        _assert_protected_history(output, resumed_history)
        # A later authorized render must not change legacy revision selection
        # inputs, reply digests or its historical paid-count/result snapshot.
        from omni_story.library.revision import execute_editing_revision
        assert execute_editing_revision(reference, library, output) == previous
        assert len(jobs) == 23
        _assert_protected_history(output, history)

    _run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"])
    assert probe_media(final)["duration_s"] == pytest.approx(2, abs=.05)


def test_continuation_reads_all_exact_slices_before_batched_claims(inputs):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output, two_slices=True)) as jobs:
        _baseline_and_revision(inputs)
        before = _protected_history(output)
        authorize(output, "进行端到端测试，模型自主学习完整参考")
        result = continuation(reference, library, output)
        assert len(jobs) == result["usage"]["requests"] == 24
        new = jobs[17:]
        names = [_name(j) for j in new]
        assert names[0:2] == ["continuation_3_reference", "continuation_3_plan"]
        assert all(name.startswith("semantic_slice_3_") for name in names[2:4])
        assert names[4:] == ["semantic_claims_batch_3", "continuation_3_blind", "continuation_3_review"]
        fact_rows = _decode_after(new[4]["arguments"]["prompt"], "\nrecords：")
        assert {r["observation"]["segment_id"] for r in fact_rows} == {"segment_1", "segment_2"}
        for row in fact_rows:
            assert row["observation"]["evidence"][0]["description"] == "red square visible on black field"
        proof = _read(result["semantic_evidence_path"])
        assert proof["comparison_mode"] == "all_facts_before_batched_comparison"
        assert len(proof["observations"]) == len(proof["segment_checks"]) == 2
        assert result["semantic_gate_passed"] is True
        _assert_protected_history(output, before)
    manifest = _read(output / "render_3" / "render_result.json")
    assert [(p["source_in_s"], p["source_out_s"]) for p in manifest["provenance"]] == [(1.5, 2), (3, 3.5)]
    assert probe_media(result["final_video"])["duration_s"] == pytest.approx(1.266666667, abs=.05)


def test_continuation_full_reference_coverage_repair_is_counted_once(inputs):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output, missing_coverage_once=True)) as jobs:
        _baseline_and_revision(inputs)
        before = _protected_history(output)
        authorize(output, "进行端到端测试")
        result = continuation(reference, library, output)
        assert len(jobs) == result["usage"]["requests"] == 24
        state = _read(output / "library_state.json")
        original = next(c for c in state["calls"] if c["name"] == "continuation_3_reference")
        repaired = next(c for c in state["calls"] if c["name"] == "continuation_3_reference_repair")
        assert repaired["repair_of"] == original["id"]
        failure = _read(output / "calls" / original["id"] / "protocol_failure.json")
        assert "reference_tail_not_covered" in failure["error"]
        assert not (output / "calls" / original["id"] / "parsed.json").exists()
        assert result["semantic_gate_passed"] is True
        assert continuation(reference, library, output) == result
        assert len(jobs) == 24
        _assert_protected_history(output, before)


def test_continuation_authorization_cannot_extend_exhausted_budget(inputs):
    authorize, _ = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output)) as jobs:
        _baseline_and_revision(inputs)
        recorded = _read(output / "library_state.json")
        state = LibraryState(output, recorded["input_lock"], max_requests=40)
        for index in range(23):
            call, _ = state.begin_call("synthetic_consumed_" + str(index), {"fixture_known_call": index})
            state.complete_call(call, {"fixture_known_reply": index})
        before = _protected_history(output)
        with pytest.raises(ValueError, match="insufficient_budget_for_complete_run"):
            authorize(output, "进行端到端测试")
        assert len(jobs) == 17
        after = _read(output / "library_state.json")
        assert after["max_requests"] == after["request_count"] == 40
        assert not after["artifacts"].get("semantic_continuation_authorization")
        assert not (output / "render_3").exists()
        _assert_protected_history(output, before)


def test_continuation_authorization_binds_historical_revision_before_any_call(inputs):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output)) as jobs:
        _baseline_and_revision(inputs)
        authorize(output, "进行端到端测试")
        path = output / "result_revision_2.json"
        original = path.read_bytes()
        value = json.loads(original)
        value["synthetic_tampering"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        try:
            with pytest.raises(ValueError, match="historical_file_changed"):
                continuation(reference, library, output)
            assert len(jobs) == _read(output / "library_state.json")["request_count"] == 17
            assert not (output / "render_3").exists()
        finally:
            path.write_bytes(original)


def test_continuation_does_not_replay_uncertain_reference_media(inputs):
    authorize, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(reference, library, output)) as jobs:
        _baseline_and_revision(inputs)
        recorded = _read(output / "library_state.json")
        state = LibraryState(output, recorded["input_lock"], max_requests=40)
        digest = sha256_file(reference)
        call, _ = state.begin_call("synthetic_uncertain_reference", {
            "tool": "analyze_video", "arguments": {
                "video_source": str(reference), "prompt": "synthetic original lost reply"},
            "media_sha256": digest, "observation_scope": {
                "kind": "complete_file", "source_sha256": digest, "source_start_s": 0,
                "source_end_s": probe_media(reference)["duration_s"]},
        })
        state.fail_call(call, "synthetic missing reply", uncertain=True)
        state.enable_independent_continuation()
        authorize(output, "端到端测试，保留原不明请求")
        original_call = deepcopy(_read(output / "library_state.json")["calls"][-1])
        before = _protected_history(output)
        with pytest.raises(LibraryStopped, match="unknown_media_must_not_be_resubmitted"):
            continuation(reference, library, output)
        assert len(jobs) == 17
        after = _read(output / "library_state.json")
        assert after["request_count"] == 18 and after["max_requests"] == 40
        assert after["calls"][-1] == original_call
        assert original_call["status"] == "uncertain"
        assert not any(c["name"].startswith("continuation_3_") for c in after["calls"])
        assert not (output / "render_3").exists()
        _assert_protected_history(output, before)


def _known_partial_reference(inputs, jobs):
    """Complete the one original/repair pair; neither is a full-contract pass."""
    authorize, continuation = _apis()
    reference, library, output = inputs
    _baseline_and_revision(inputs)
    history = _protected_history(output)
    authorize(output, "进行端到端测试，模型自主观察完整参考")
    with pytest.raises(ValueError, match="model_protocol_repair_exhausted:continuation_3_reference"):
        continuation(reference, library, output)
    assert len(jobs) == _read(output / "library_state.json")["request_count"] == 19
    reference_calls = [c for c in _read(output / "library_state.json")["calls"]
                       if c["name"].startswith("continuation_3_reference")]
    assert len(reference_calls) == 2
    assert all(c["status"] == "received" for c in reference_calls)
    assert reference_calls[1]["repair_of"] == reference_calls[0]["id"]
    for call in reference_calls:
        folder = output / "calls" / call["id"]
        assert "reference_coverage_gap" in _read(folder / "protocol_failure.json")["error"]
        assert not (folder / "parsed.json").exists()
    assert not (output / "render_3").exists()
    _assert_protected_history(output, history)
    return reference_calls, history


def test_known_partial_reference_navigation_resume_preserves_failed_calls_and_model_core(inputs):
    from omni_story.library.semantic_continuation import reconcile_partial_reference
    _, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(
            reference, library, output, missing_coverage_always=True)) as jobs:
        reference_calls, history = _known_partial_reference(inputs, jobs)
        failed_records = _read(output / "library_state.json")["calls"]
        protected_failure_files = {
            str(p.relative_to(output)): p.read_bytes()
            for call in reference_calls for p in (output / "calls" / call["id"]).glob("*")
            if p.is_file()}
        repaired_reply = _read(output / "calls" / reference_calls[1]["id"] / "response.json")
        model_value = json.loads(repaired_reply["result"]["content"][0]["text"])
        model_value_before = deepcopy(model_value)
        reconciled = reconcile_partial_reference(output)
        assert len(jobs) == 19
        assert reconciled["full_response"] == model_value_before
        assert reconciled["source_call_id"] == reference_calls[1]["id"]
        assert reconciled["response_sha256"] == reference_calls[1]["response_sha256"]
        assert reconciled["request_sha256"] == reference_calls[1]["request_sha256"]
        assert reconciled["media_sha256"] == sha256_file(reference)
        assert reconciled["reported_coverage_gaps"] == [[1, 2], [3, 4]]
        assert reconciled["status"] == "core_contract_valid_coverage_report_partial_model_estimate"
        assert reconciled["no_model_replay_or_human_content_repair"] is True
        assert reconcile_partial_reference(output) == reconciled
        assert _read(output / "library_state.json")["calls"] == failed_records
        _assert_protected_history(output, protected_failure_files)

        result = continuation(reference, library, output)
        assert len(jobs) == result["usage"]["requests"] == 24
        assert result["semantic_gate_passed"] is True
        assert result["status"] == "library_candidate_with_limitations"
        assert result["reference_analysis_status"] == reconciled["status"]
        assert result["reference_analysis_limit"] == reconciled["evidence_limit"]
        assert _read(result["reference_analysis_path"]) == model_value_before
        assert _read(output / "library_state.json")["calls"][:19] == failed_records
        assert [_name(j) for j in jobs[19:]][0] == "continuation_3_plan"
        assert not any(_name(j).startswith("continuation_3_reference") for j in jobs[19:])
        planning = next(j for j in jobs[19:] if _name(j) == "continuation_3_plan")
        prompt = planning["arguments"]["prompt"]
        context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
        assert context["reference"] == model_value_before["reference"]
        assert context["editing_reference"] == model_value_before["editing_reference"]
        assert context["reference_observation_limit"] == reconciled["evidence_limit"]
        _assert_protected_history(output, history)
        _assert_protected_history(output, protected_failure_files)
        assert all(not (output / "calls" / c["id"] / "parsed.json").exists()
                   for c in reference_calls)
        before_resume = _protected_history(output)
        assert continuation(reference, library, output) == result
        assert len(jobs) == 24
        _assert_protected_history(output, before_resume)


@pytest.mark.parametrize("rebind_artifact_hash", [False, True])
def test_partial_reference_reconciliation_rejects_artifact_or_model_core_tampering(inputs, rebind_artifact_hash):
    from omni_story.library.semantic_continuation import reconcile_partial_reference
    from omni_story.library.state import write_json
    _, continuation = _apis()
    reference, library, output = inputs
    with _bridge(output, _continuation_responses(
            reference, library, output, missing_coverage_always=True)) as jobs:
        reference_calls, history = _known_partial_reference(inputs, jobs)
        reconcile_partial_reference(output)
        state = _read(output / "library_state.json")
        entry = state["artifacts"]["partial_reference_reconciliation"][-1]
        artifact = Path(entry["path"])
        original_artifact = artifact.read_bytes()
        original_state = (output / "library_state.json").read_bytes()
        changed = json.loads(original_artifact)
        changed["full_response"]["reference"]["theme"] = "human invented replacement theme"
        write_json(artifact, changed)
        if rebind_artifact_hash:
            state["artifacts"]["partial_reference_reconciliation"][-1]["sha256"] = json_sha(changed)
            write_json(output / "library_state.json", state)
        try:
            expected = "reconciled_model_content_changed" if rebind_artifact_hash else "reconciliation_modified"
            with pytest.raises(ValueError, match=expected):
                continuation(reference, library, output)
            assert len(jobs) == 19
            current = _read(output / "library_state.json")
            assert current["request_count"] == 19
            assert current["calls"] == state["calls"]
            assert not (output / "render_3").exists()
            assert not any(c["name"] == "continuation_3_plan" for c in current["calls"])
            assert all(not (output / "calls" / c["id"] / "parsed.json").exists()
                       for c in reference_calls)
            _assert_protected_history(output, history)
        finally:
            artifact.write_bytes(original_artifact)
            (output / "library_state.json").write_bytes(original_state)
