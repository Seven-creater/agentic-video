"""Narrow missing-field reconciliation never edits source evidence or retries.

These are pure protocol/state tests. The source observations are synthetic
fixtures, not confirmation of any movie action or model understanding.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_library_semantic_audit import SOURCE_SHA, slice_data
from omni_story.library import semantic_audit as audit
from omni_story.library.state import LibraryState, json_sha, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _failed_observation_state(tmp_path, slice_data, *, mutation=None, repair=True):
    """Known original/one-repair chain; no external model or media processing."""
    _, segment, proxy, valid = deepcopy(slice_data)
    state = LibraryState(tmp_path / "run", {"fixture": "missing_observation_uncertainties"}, max_requests=80)
    missing = deepcopy(valid)
    missing.pop("uncertainties")
    missing["extra_untyped_inferences"] = ["Synthetic task meaning, not typed source evidence."]
    if mutation:
        mutation(missing)
    original_value = deepcopy(missing)
    original_value["evidence"][0]["local_end_s"] = original_value["evidence"][0]["local_start_s"]
    name = "semantic_slice_3_fixture"
    request = {"tool": "analyze_video", "arguments": {"video_source": str(tmp_path / "fixture.mp4"),
                "prompt": "Independent source observation fixture."},
               "media_sha256": proxy["sha256"], "observation_scope": {
                   "kind": "continuous_window", "source_sha256": SOURCE_SHA,
                   "source_start_s": segment["source_in_s"], "source_end_s": segment["source_out_s"]}}
    original, _ = state.begin_call(name, request)
    state.complete_call(original, {"result": {"content": [{"type": "text", "text": json.dumps(original_value)}]}})
    write_json(state.output / "calls" / original["id"] / "protocol_failure.json",
               {"error": "semantic/evidence:outside_observed_slice", "attempt": 0})
    if repair:
        fixed, _ = state.begin_call(name + "_repair", {**request, "arguments": {
            **request["arguments"], "prompt": "One allowed fixture format repair."}}, repair_of=original)
        state.complete_call(fixed, {"result": {"content": [{"type": "text", "text": json.dumps(missing)}]}})
        write_json(state.output / "calls" / fixed["id"] / "protocol_failure.json",
                   {"error": "semantic/uncertainties:list_required", "attempt": 1})
    return state, name, segment, proxy, missing


def _protected(state):
    return {str(path.relative_to(state.output)): path.read_bytes()
            for path in (state.output / "calls").rglob("*") if path.is_file()}


def _assert_preserved(state, before):
    for path, raw in before.items():
        assert (state.output / path).read_bytes() == raw, path


def test_default_typed_observation_contract_still_rejects_missing_uncertainties(slice_data):
    _, segment, proxy, observation = deepcopy(slice_data)
    observation.pop("uncertainties")
    with pytest.raises(ValueError, match="semantic/uncertainties:list_required"):
        audit.validate_segment_observation(observation, segment, SOURCE_SHA, proxy)


class _NoPost:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Reconciliation cannot submit another model request.")


def _reconcile(state, name, segment, proxy, glm=None):
    from omni_story.library.semantic_continuation import reconcile_missing_observation_uncertainties
    return reconcile_missing_observation_uncertainties(
        glm or _NoPost(state), name, segment, {"source_id": segment["source_id"], "sha256": SOURCE_SHA}, proxy)


def test_only_missing_uncertainties_adds_explicit_program_limitation_without_request_or_evidence_changes(tmp_path, slice_data):
    state, name, segment, proxy, raw = _failed_observation_state(tmp_path, slice_data)
    before = _protected(state)
    calls = deepcopy(state.data["calls"])
    glm = _NoPost(state)
    observed = _reconcile(state, name, segment, proxy, glm)
    assert glm.calls == []
    assert state.data["request_count"] == 2 and state.max_requests == 80
    assert state.data["calls"] == calls
    assert {key: value for key, value in observed.items() if key != "uncertainties"} == raw
    assert len(observed["uncertainties"]) == 1
    assert "Program protocol limitation" in observed["uncertainties"][0]
    assert "omitted" in observed["uncertainties"][0] and "unknown" in observed["uncertainties"][0]
    assert "untyped inference is not validated evidence" in observed["uncertainties"][0]
    assert observed["evidence"] == raw["evidence"]
    assert observed["characters"] == raw["characters"]
    assert observed["extra_untyped_inferences"] == raw["extra_untyped_inferences"]
    audit.validate_segment_observation(observed, segment, SOURCE_SHA, proxy)
    entry = state.data["artifacts"]["source_observation_reconciliation"][0]
    record = _read(entry["path"])
    assert record["raw_model_object"] == raw and record["observation"] == observed
    assert record["program_added_field"] == "uncertainties"
    assert record["additional_model_requests"] == 0
    assert record["original_failure_preserved"] is True and record["facts_times_identities_unchanged"] is True
    assert len(record["call_bindings"]) == 2
    _assert_preserved(state, before)
    assert all(not (state.output / "calls" / call["id"] / "parsed.json").exists() for call in calls)
    state_before_resume = deepcopy(state.data)
    assert _reconcile(state, name, segment, proxy, glm) == observed
    assert state.data == state_before_resume and glm.calls == []
    _assert_preserved(state, before)


@pytest.mark.parametrize("mutation,match", [
    (lambda o: o.update(source_sha256="d" * 64), "observation_binding_changed:source_sha256"),
    (lambda o: o.update(source_id="different_source"), "observation_binding_changed:source_id"),
    (lambda o: o.update(source_in_s=2200), "observation_binding_changed:source_in_s"),
    (lambda o: o.update(source_out_s=2290), "observation_binding_changed:source_out_s"),
    (lambda o: o.update(proxy_sha256="e" * 64), "observation_binding_changed:proxy_sha256"),
    (lambda o: o.update(observed_duration_s=9), "observation_duration_changed"),
    (lambda o: o["evidence"][0].update(local_end_s=9), "outside_observed_slice"),
    (lambda o: o["evidence"][0].update(local_end_s=0), "outside_observed_slice"),
    (lambda o: o["evidence"][0].update(kind="invented_kind"), "unknown_evidence_kind"),
    (lambda o: o["evidence"][0].update(character_ids=["unknown_character"]), "unknown_or_duplicate_ref"),
    (lambda o: o["evidence"][3].update(basis_evidence_ids=[]), "inference_requires_direct_evidence"),
    (lambda o: o.update(required_claims=[]), "mixed_time_domains"),
])
def test_missing_field_does_not_relax_source_time_kind_character_or_inference_contracts(tmp_path, slice_data, mutation, match):
    state, name, segment, proxy, _ = _failed_observation_state(tmp_path, slice_data, mutation=mutation)
    before = _protected(state)
    calls = deepcopy(state.data["calls"])
    glm = _NoPost(state)
    with pytest.raises(ValueError, match=match):
        _reconcile(state, name, segment, proxy, glm)
    assert glm.calls == [] and state.data["calls"] == calls
    assert not state.data["artifacts"].get("source_observation_reconciliation")
    _assert_preserved(state, before)


@pytest.mark.parametrize("which,status", [
    (0, "submitted"), (0, "uncertain"), (0, "failed_known"),
    (1, "submitted"), (1, "uncertain"), (1, "failed_known"),
])
def test_unknown_original_or_repair_cannot_be_reconciled_or_replayed(tmp_path, slice_data, which, status):
    state, name, segment, proxy, _ = _failed_observation_state(tmp_path, slice_data)
    state.data["calls"][which]["status"] = status
    state._save()
    before = deepcopy(state.data)
    glm = _NoPost(state)
    assert _reconcile(state, name, segment, proxy, glm) is None
    assert glm.calls == [] and state.data == before


def test_no_original_repair_is_not_an_implicit_third_repair_or_field_fill(tmp_path, slice_data):
    state, name, segment, proxy, _ = _failed_observation_state(tmp_path, slice_data, repair=False)
    before = deepcopy(state.data)
    glm = _NoPost(state)
    assert _reconcile(state, name, segment, proxy, glm) is None
    assert glm.calls == [] and state.data == before and state.data["request_count"] == 1


@pytest.mark.parametrize("value", [None, "invalid report", [], ["Already supplied by model."]])
def test_present_uncertainties_is_never_rewritten_even_if_failure_metadata_says_missing(tmp_path, slice_data, value):
    state, name, segment, proxy, _ = _failed_observation_state(
        tmp_path, slice_data, mutation=lambda o: o.update(uncertainties=value))
    before = _protected(state)
    with pytest.raises(ValueError, match="only_omitted_uncertainty_field_eligible"):
        _reconcile(state, name, segment, proxy)
    assert state.data["request_count"] == 2
    assert not state.data["artifacts"].get("source_observation_reconciliation")
    _assert_preserved(state, before)


@pytest.mark.parametrize("rehash", [False, True])
def test_saved_reconciliation_cannot_rewrite_evidence_even_after_artifact_hash_rebinding(tmp_path, slice_data, rehash):
    state, name, segment, proxy, _ = _failed_observation_state(tmp_path, slice_data)
    _reconcile(state, name, segment, proxy)
    before = _protected(state)
    calls = deepcopy(state.data["calls"])
    entry = state.data["artifacts"]["source_observation_reconciliation"][0]
    record = _read(entry["path"])
    record["observation"]["evidence"][0]["description"] = "Human fabricated replacement source event."
    write_json(entry["path"], record)
    if rehash:
        entry["sha256"] = json_sha(record)
        state._save()
    expected = "reconciled_observation_facts_changed" if rehash else "observation_reconciliation_modified"
    with pytest.raises(ValueError, match=expected):
        _reconcile(state, name, segment, proxy)
    assert state.data["calls"] == calls and state.data["request_count"] == 2
    _assert_preserved(state, before)


def test_default_semantic_pipeline_does_not_enable_continuation_field_reconciliation(tmp_path, slice_data, monkeypatch):
    from omni_story.library import semantic_pipeline
    plan, segment, proxy, raw = deepcopy(slice_data)
    raw.pop("uncertainties")
    proxy.update(path=str(tmp_path / "fixture.mp4"), kind="continuous_window")
    source = {"source_id": segment["source_id"], "sha256": SOURCE_SHA}
    monkeypatch.setattr(semantic_pipeline, "prepare_window", lambda *args, **kwargs: deepcopy(proxy))

    class StrictDefault:
        def __init__(self):
            self.calls = []

        def call(self, name, prompt, media, validator, **kwargs):
            self.calls.append(name)
            validator(deepcopy(raw))
            raise AssertionError("Missing report field must remain invalid on default semantic route.")

    glm = StrictDefault()
    windows = [{"window_id": segment["window_id"], "observation": {"roles": []}}]
    with pytest.raises(ValueError, match="semantic/uncertainties:list_required"):
        semantic_pipeline.observe_selected_slices(glm, plan, {segment["source_id"]: source}, windows,
            tmp_path / "cache", tmp_path / "output", 0)
    assert len(glm.calls) == 1 and glm.calls[0].startswith("semantic_slice_0_")
    assert not (tmp_path / "output" / "semantic_audit").exists()
