"""Bounded report normalization preserves model verdicts and source evidence.

Pure fixtures exercise deterministic JSON recovery and immutable call records.
They neither submit model requests nor establish real-video semantic quality.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_library_semantic_audit import SOURCE_SHA, comparison, slice_data
from omni_story.library import contracts, semantic_audit as audit
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, json_sha, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _normalizer(raw):
    from omni_story.library.semantic_continuation import _normalize_batch_report
    return _normalize_batch_report(raw)


def _batch_fixture(tmp_path, slice_data, *, mutation=None):
    plan, segment, proxy, observation = deepcopy(slice_data)
    carrier = tmp_path / "known_carrier.bin"
    carrier.write_bytes(b"Immutable synthetic carrier for unit report validation.")
    proxy.update(path=str(carrier), sha256=sha256_file(carrier), kind="continuous_window")
    observation["proxy_sha256"] = proxy["sha256"]
    claims, check = comparison(plan, segment, observation)
    reason = 'The "inner cue" does not establish the claimed action.'
    row = next(c for c in check["claim_checks"] if c["claim_id"] == "vc_seg2_action")
    row.update(status="unsupported", evidence_ids=[], reason=reason, limitations=[])
    report = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_checks": [check]}
    if mutation:
        mutation(report, observation)
    raw = json.dumps(report).replace('\\"inner cue\\"', '"inner cue"')
    records = [{"observation": observation, "required_claims": claims,
                "proxy_path": str(carrier), "role_hypotheses": []}]

    def validator(value):
        # The production pipeline already validated these exact facts first.
        # Revalidate fixture facts here to make that prerequisite explicit.
        audit.validate_segment_observation(observation, segment, SOURCE_SHA, proxy)
        if value.get("protocol") != audit.SEMANTIC_PROTOCOL:
            raise ValueError("semantic:batch_protocol")
        rows = value.get("segment_checks")
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("semantic:batch_checks_must_cover_all_slices")
        if rows[0].get("segment_id") != segment["segment_id"]:
            raise ValueError("semantic:unknown_or_duplicate_batch_segment")
        audit.validate_segment_claim_check(rows[0], observation, claims)

    return raw, report, records, validator, proxy


def _known_failed_batch(tmp_path, slice_data, *, mutation=None, original_text=None,
                        repair_text="", repair=True):
    raw, report, records, validator, proxy = _batch_fixture(tmp_path, slice_data, mutation=mutation)
    if original_text is not None:
        raw = original_text
    state = LibraryState(tmp_path / "run", {"fixture": "batch_report_reconciliation"}, max_requests=80)
    name = "semantic_claims_batch_3"
    request = {"tool": "analyze_video", "media_sha256": proxy["sha256"],
               "arguments": {"video_source": proxy["path"], "prompt": "Known report fixture."}}
    original, _ = state.begin_call(name, request)
    state.complete_call(original, {"result": {"content": [{"type": "text", "text": raw}]}})
    write_json(state.output / "calls" / original["id"] / "protocol_failure.json",
               {"error": "Expecting ',' delimiter: line 1 column 100 (char 99)", "attempt": 0})
    if repair:
        fixed, _ = state.begin_call(name + "_repair", {**request, "arguments": {
            **request["arguments"], "prompt": "One allowed format repair fixture."}}, repair_of=original)
        state.complete_call(fixed, {"finish_reason": "length", "result": {
            "content": [{"type": "text", "text": repair_text}]}}, usage={"completion_tokens": 16384})
        write_json(state.output / "calls" / fixed["id"] / "protocol_failure.json",
                   {"error": "Expecting value: line 1 column 1 (char 0)", "attempt": 1})
    return state, name, raw, report, records, validator


class _NoPost:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Batch normalization must never request another repair.")


def _reconcile(state, name, records, validator, glm=None):
    from omni_story.library.semantic_continuation import reconcile_batch_report
    return reconcile_batch_report(glm or _NoPost(state), name, records, validator)


def _protected(state):
    return {str(path.relative_to(state.output)): path.read_bytes()
            for path in (state.output / "calls").rglob("*") if path.is_file()}


def _assert_preserved(state, before):
    for path, raw in before.items():
        assert (state.output / path).read_bytes() == raw, path


def test_quote_only_normalization_preserves_words_and_copies_same_claim_reason_without_promoting_verdict(tmp_path, slice_data):
    raw, original, _, validator, _ = _batch_fixture(tmp_path, slice_data)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    normalized, edits, copies = _normalizer(raw)
    validator(normalized)
    expected = deepcopy(original)
    unsupported = next(c for c in expected["segment_checks"][0]["claim_checks"]
                       if c["claim_id"] == "vc_seg2_action")
    unsupported["limitations"] = [unsupported["reason"]]
    assert normalized == expected
    assert len(edits) == 2 and all(edit["inserted"] == "\\" for edit in edits)
    assert copies == [{"segment_id": "seg_2", "claim_id": "vc_seg2_action",
                       "reason": unsupported["reason"]}]
    assert unsupported["status"] == "unsupported" and unsupported["evidence_ids"] == []
    # Apply only the recorded backslashes to prove that no text was rewritten.
    escaped = raw
    for edit in edits:
        i = edit["position_in_previous_text"]
        escaped = escaped[:i] + edit["inserted"] + escaped[i:]
    restored = json.loads(escaped)
    assert restored == original


def test_known_original_and_empty_exhausted_repair_normalize_with_zero_posts_and_immutable_history(tmp_path, slice_data):
    state, name, raw, _, records, validator = _known_failed_batch(tmp_path, slice_data)
    before = _protected(state)
    calls = deepcopy(state.data["calls"])
    glm = _NoPost(state)
    normalized = _reconcile(state, name, records, validator, glm)
    assert glm.calls == [] and state.data["calls"] == calls
    assert state.data["request_count"] == 2 and state.max_requests == 80
    record = _read(state.data["artifacts"]["batch_report_reconciliation"][0]["path"])
    assert record["raw_model_text"] == raw and record["batch_report"] == normalized
    assert len(record["call_bindings"]) == 2 and len(record["quote_repairs"]) == 2
    assert record["additional_model_requests"] == 0 and record["original_failure_preserved"] is True
    assert record["statuses_claim_ids_evidence_ids_unchanged"] is True
    assert "not a full model protocol pass" in record["protocol_limit"]
    _assert_preserved(state, before)
    assert all(not (state.output / "calls" / call["id"] / "parsed.json").exists() for call in calls)
    saved = deepcopy(state.data)
    assert _reconcile(state, name, records, validator, glm) == normalized
    assert state.data == saved and glm.calls == []
    _assert_preserved(state, before)


@pytest.mark.parametrize("raw", [
    "", "   ", '{"segment_checks": [', '{"reason": "unfinished',
    '{"a": 1 "b": 2}', '{"a": "text"123}', '{"a": "text""next"}',
    '{"a": "text"[1]}', '{"a": "text"{}}', '{"a": "text"\\bad}',
    '{"a": "text"}', '[{"a":"text"}]',
])
def test_other_syntax_empty_truncation_and_nonobject_never_receive_generic_content_repair(raw):
    with pytest.raises((ValueError, KeyError)):
        _normalizer(raw)


def test_quote_recovery_has_hard_sixteen_insertion_cap():
    raw = json.dumps({"reason": ' '.join('"inner cue" tail' for _ in range(9))})
    raw = raw.replace('\\"inner cue\\"', '"inner cue"')
    with pytest.raises(ValueError, match="batch_syntax_not_quote_only_recoverable|batch_quote_repair_limit"):
        _normalizer(raw)


def test_sixteen_quote_insertions_plus_one_root_close_have_a_final_parse_round():
    reason = " ".join('"cue" remains unknown.' for _ in range(8))
    report = {"segment_checks": [{"segment_id": "seg_fixture", "claim_checks": [{
        "claim_id": "claim_fixture", "status": "unsupported", "evidence_ids": [],
        "reason": reason, "limitations": []}]}]}
    raw = json.dumps(report).replace('\\"cue\\"', '"cue"')[:-1]
    normalized, edits, copies = _normalizer(raw)
    assert sum(edit["inserted"] == "\\" for edit in edits) == 16
    assert sum(edit.get("operation") == "close_root_object" for edit in edits) == 1
    assert len(edits) == 17 and copies[0]["reason"] == reason
    expected = deepcopy(report)
    expected["segment_checks"][0]["claim_checks"][0]["limitations"] = [reason]
    assert normalized == expected


def test_complete_report_missing_only_final_root_brace_is_recoverable_without_missing_facts(tmp_path, slice_data):
    raw, original, records, validator, _ = _batch_fixture(tmp_path, slice_data)
    assert raw.endswith("}") and raw[:-1].endswith("]")
    state, name, missing, _, records, validator = _known_failed_batch(
        tmp_path, slice_data, original_text=raw[:-1])
    before = _protected(state)
    normalized = _reconcile(state, name, records, validator)
    validator(normalized)
    expected = deepcopy(original)
    row = next(c for c in expected["segment_checks"][0]["claim_checks"] if c["claim_id"] == "vc_seg2_action")
    row["limitations"] = [row["reason"]]
    assert normalized == expected
    record = _read(state.data["artifacts"]["batch_report_reconciliation"][0]["path"])
    assert record["raw_model_text"] == missing
    assert record["quote_repairs"][-1]["operation"] == "close_root_object"
    assert record["quote_repairs"][-1]["inserted"] == "}"
    assert len(record["quote_repairs"]) == 3
    _assert_preserved(state, before)


@pytest.mark.parametrize("drop", ["segment", "claim", "reason"])
def test_final_root_brace_recovery_never_invents_truncated_rows_claims_or_reason(tmp_path, slice_data, drop):
    raw, report, _, _, _ = _batch_fixture(tmp_path, slice_data)
    if drop == "segment":
        report["segment_checks"] = []
        broken = json.dumps(report)[:-1]
    elif drop == "claim":
        report["segment_checks"][0]["claim_checks"].pop()
        broken = json.dumps(report)[:-1]
    else:
        broken = raw[:raw.index('"inner cue"') + len('"inner cu')]
    state, name, _, _, records, validator = _known_failed_batch(tmp_path, slice_data, original_text=broken)
    before = _protected(state)
    with pytest.raises(ValueError):
        _reconcile(state, name, records, validator)
    assert state.data["request_count"] == 2
    assert not state.data["artifacts"].get("batch_report_reconciliation")
    _assert_preserved(state, before)


@pytest.mark.parametrize("mutation,match", [
    (lambda r,o: r["segment_checks"][0].update(observation_sha256="d" * 64), "claim_check_observation_changed"),
    (lambda r,o: r["segment_checks"][0].update(segment_id="missing_segment"), "unknown_or_duplicate_batch_segment"),
    (lambda r,o: r["segment_checks"][0]["claim_checks"][0].update(claim_id="made_up_claim"), "checked_exactly_once"),
    (lambda r,o: r["segment_checks"][0]["claim_checks"][0].update(evidence_ids=["made_up_evidence"]), "unknown_or_duplicate_ref"),
    (lambda r,o: r["segment_checks"][0]["claim_checks"][0].update(evidence_ids=["src_inference"]), "cannot_prove_visible_action"),
    (lambda r,o: o.update(source_sha256="f" * 64), "observation_binding_changed:source_sha256"),
    (lambda r,o: o.update(source_in_s=2200), "observation_binding_changed:source_in_s"),
    (lambda r,o: o["evidence"][0].update(local_end_s=9), "outside_observed_slice"),
    (lambda r,o: r.update(protocol="unknown_protocol"), "batch_protocol"),
])
def test_quote_recovery_cannot_bypass_source_fact_hash_claim_identity_time_or_evidence_gates(tmp_path, slice_data, mutation, match):
    state, name, _, _, records, validator = _known_failed_batch(tmp_path, slice_data, mutation=mutation)
    before = _protected(state)
    glm = _NoPost(state)
    with pytest.raises(ValueError, match=match):
        _reconcile(state, name, records, validator, glm)
    assert glm.calls == [] and state.data["request_count"] == 2
    assert not state.data["artifacts"].get("batch_report_reconciliation")
    _assert_preserved(state, before)


@pytest.mark.parametrize("which,status", [
    (0, "submitted"), (0, "uncertain"), (0, "failed_known"),
    (1, "submitted"), (1, "uncertain"), (1, "failed_known"),
])
def test_unknown_original_or_repair_never_normalizes_or_submits_a_third_request(tmp_path, slice_data, which, status):
    state, name, _, _, records, validator = _known_failed_batch(tmp_path, slice_data)
    state.data["calls"][which]["status"] = status
    state._save()
    before = deepcopy(state.data)
    glm = _NoPost(state)
    assert _reconcile(state, name, records, validator, glm) is None
    assert state.data == before and glm.calls == []


@pytest.mark.parametrize("repair_text,repair", [("", False), ("{}", True), ("{", True)])
def test_missing_or_nonempty_repair_is_not_a_new_engineering_repair_loop(tmp_path, slice_data, repair_text, repair):
    state, name, _, _, records, validator = _known_failed_batch(
        tmp_path, slice_data, repair=repair, repair_text=repair_text)
    before = deepcopy(state.data)
    glm = _NoPost(state)
    assert _reconcile(state, name, records, validator, glm) is None
    assert state.data == before and glm.calls == []


@pytest.mark.parametrize("rehash", [False, True])
def test_rebinding_artifact_hash_cannot_promote_unsupported_status_or_change_raw_reason(tmp_path, slice_data, rehash):
    state, name, _, _, records, validator = _known_failed_batch(tmp_path, slice_data)
    _reconcile(state, name, records, validator)
    before = _protected(state)
    entry = state.data["artifacts"]["batch_report_reconciliation"][0]
    record = _read(entry["path"])
    changed = next(c for c in record["batch_report"]["segment_checks"][0]["claim_checks"]
                   if c["claim_id"] == "vc_seg2_action")
    changed.update(status="supported", evidence_ids=["src_action"], reason="Human replacement pass.", limitations=[])
    write_json(entry["path"], record)
    if rehash:
        entry["sha256"] = json_sha(record)
        state._save()
    expected = "reconciled_batch_report_changed" if rehash else "batch_reconciliation_modified"
    with pytest.raises(ValueError, match=expected):
        _reconcile(state, name, records, validator)
    assert state.data["request_count"] == 2
    _assert_preserved(state, before)


def test_actual_carrier_hash_change_is_not_eligible_for_report_recovery(tmp_path, slice_data):
    state, name, _, _, records, validator = _known_failed_batch(tmp_path, slice_data)
    Path(records[0]["proxy_path"]).write_bytes(b"Different carrier media bytes.")
    before = _protected(state)
    with pytest.raises(ValueError, match="batch_reply_or_media_changed"):
        _reconcile(state, name, records, validator)
    assert state.data["request_count"] == 2
    _assert_preserved(state, before)


def test_nonsupported_reason_copy_requires_existing_nonempty_reason_and_only_empty_limitations(tmp_path, slice_data):
    raw, report, _, _, _ = _batch_fixture(tmp_path, slice_data)
    for reason in ("", None):
        invalid = deepcopy(report)
        row = next(c for c in invalid["segment_checks"][0]["claim_checks"] if c["claim_id"] == "vc_seg2_action")
        row["reason"] = reason
        with pytest.raises(ValueError):
            _normalizer(json.dumps(invalid))
    supplied = deepcopy(report)
    row = next(c for c in supplied["segment_checks"][0]["claim_checks"] if c["claim_id"] == "vc_seg2_action")
    row["limitations"] = ["Model already stated a limitation."]
    normalized, edits, copies = _normalizer(json.dumps(supplied))
    assert normalized == supplied and edits == [] and copies == []
    row["limitations"] = None
    normalized, _, copies = _normalizer(json.dumps(supplied))
    assert normalized == supplied and copies == []  # Strict supplied validator rejects None.


def test_default_semantic_pipeline_keeps_strict_json_and_never_enables_batch_reconciliation(tmp_path, slice_data, monkeypatch):
    from omni_story.library import semantic_pipeline
    raw, _, _, _, proxy = _batch_fixture(tmp_path, slice_data)
    plan, segment, _, observation = deepcopy(slice_data)
    observation["proxy_sha256"] = proxy["sha256"]
    source = {"source_id": segment["source_id"], "sha256": SOURCE_SHA}
    monkeypatch.setattr(semantic_pipeline, "prepare_window", lambda *args, **kwargs: deepcopy(proxy))

    class StrictDefault:
        def __init__(self):
            self.calls = []

        def call(self, name, prompt, media, validator, **kwargs):
            self.calls.append(name)
            value = contracts.parse_model_json(raw) if name.startswith("semantic_claims_batch_") else deepcopy(observation)
            validator(value)
            return value

    glm = StrictDefault()
    windows = [{"window_id": segment["window_id"], "observation": {"roles": []}}]
    with pytest.raises(ValueError, match="Expecting ',' delimiter"):
        semantic_pipeline.observe_selected_slices(glm, plan, {segment["source_id"]: source}, windows,
            tmp_path / "cache", tmp_path / "output", 0, batched_comparison=True)
    assert len(glm.calls) == 2 and glm.calls[1] == "semantic_claims_batch_0"
    assert not (tmp_path / "output" / "semantic_audit" / "round_0" / "manifest.json").exists()
