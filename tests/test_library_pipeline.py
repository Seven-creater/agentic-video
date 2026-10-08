"""Local queue simulations only: no model endpoint, key, or HTTP request."""
from contextlib import contextmanager
import json
import threading

import pytest

from omni_story.library.media import sha256_file
from omni_story.library.pipeline import CodexMCP
from omni_story.library import pipeline as library_pipeline
from omni_story.library.prompts import POLICY_VERSION, blind_prompt
from omni_story.library.state import LibraryState, LibraryStopped, write_json


@pytest.fixture
def task(tmp_path):
    output = tmp_path / "run"
    state = LibraryState(output, {"reference_sha256": "test_ref"}, max_requests=8)
    write_json(output / "mcp_ready.json", {"model": "glm-5.3-flash", "test_fake": True})
    media = tmp_path / "fixture.mp4"
    # CodexMCP hashes/size-checks media; the simulated bridge never decodes it.
    media.write_bytes(b"local queue fixture only")
    return state, media


def _request(media, prompt):
    return {"tool": "analyze_video", "arguments": {"video_source": str(media.resolve()), "prompt": prompt},
            "media_sha256": sha256_file(media), "provider": "official_vision_mcp_in_codex",
            "policy_version": POLICY_VERSION}


def _reply(model_text):
    return {"status": "complete", "result": {"content": [{"type": "text", "text": model_text}]}}


def _validator(value):
    if value.get("valid") is not True:
        raise ValueError("valid_boolean_required")


@contextmanager
def _fake_bridge(output, replies):
    queue = output / "mcp_queue"
    queue.mkdir(exist_ok=True)
    stopped = threading.Event()
    received = []
    failures = []

    def serve():
        while not stopped.is_set():
            for request_path in sorted(queue.glob("*.request.json")):
                response_path = request_path.with_name(request_path.name.replace(".request.json", ".response.json"))
                if response_path.exists():
                    continue
                job = json.loads(request_path.read_text(encoding="utf-8"))
                received.append(job)
                if len(received) > len(replies):
                    failures.append("unexpected_extra_request")
                    write_json(response_path, {"status": "unknown", "error": "unexpected extra request"})
                else:
                    write_json(response_path, replies[len(received) - 1])
            stopped.wait(0.01)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield received
    finally:
        stopped.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert failures == []


def test_completed_reply_is_recovered_without_replay(task):
    state, media = task
    call, _ = state.begin_call("reference", _request(media, "fixed prompt"))
    queue = state.output / "mcp_queue"
    queue.mkdir()
    write_json(queue / (call["id"] + ".response.json"), _reply('{"valid":true}'))
    (state.output / "mcp_http.jsonl").write_text(json.dumps({
        "type": "response", "job_id": call["id"], "body": json.dumps({
            "usage": {"prompt_tokens": 8, "completion_tokens": 3}})}) + "\n", encoding="utf-8")
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    client = CodexMCP(restarted, timeout_s=1)
    assert restarted.data["calls"][0]["status"] == "received"
    assert client.call("reference", "fixed prompt", media, _validator) == {"valid": True}
    assert list(queue.glob("*.request.json")) == []
    assert restarted.usage()["requests"] == 1
    assert restarted.usage()["prompt_tokens"] == 8


def test_stopped_connection_blocks_new_work_without_consuming_budget(task):
    state, media = task
    (state.output/'mcp_stop').touch()
    client = CodexMCP(state,timeout_s=0.1)
    with pytest.raises(LibraryStopped,match='connection_stopped'):
        client.call('reference','fixed prompt',media,_validator)
    assert state.usage()['requests'] == 0
    assert not list(client.queue.glob('*.request.json'))


def test_stopped_connection_still_reuses_received_result(task):
    state,media = task
    client = CodexMCP(state,timeout_s=1)
    with _fake_bridge(state.output,[_reply('{"valid":true}')]):
        assert client.call('reference','fixed prompt',media,_validator) == {'valid':True}
    (state.output/'mcp_stop').touch()
    assert CodexMCP(state,timeout_s=0.1).call('reference','fixed prompt',media,_validator) == {'valid':True}
    assert state.usage()['requests'] == 1


def test_format_repair_is_one_request_and_restart_reuses_both(task):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('not json'), _reply('{"valid":true}')]) as submitted:
        assert client.call("reference", "fixed prompt", media, _validator) == {"valid": True}
        assert len(submitted) == 2
        assert state.data["calls"][1]["repair_of"] == state.data["calls"][0]["id"]
        assert "previous_response" in submitted[1]["arguments"]["prompt"]
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached_client = CodexMCP(restarted, timeout_s=0.1)
    assert cached_client.call("reference", "fixed prompt", media, _validator) == {"valid": True}
    assert restarted.usage()["requests"] == 2


def test_failed_repair_does_not_gain_another_attempt_after_restart(task):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('{"valid":false}'), _reply('{"valid":false}')]) as submitted:
        with pytest.raises(ValueError, match="repair_exhausted"):
            client.call("reference", "fixed prompt", media, _validator)
        assert len(submitted) == 2
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached_client = CodexMCP(restarted, timeout_s=0.1)
    with pytest.raises(ValueError, match="repair_exhausted"):
        cached_client.call("reference", "fixed prompt", media, _validator)
    assert restarted.usage()["requests"] == 2


def test_cross_stage_cache_validation_preserves_old_failure_and_sole_repair(task):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('{"valid":false}'), _reply('{"valid":false}')]):
        with pytest.raises(ValueError, match="repair_exhausted"):
            client.call("original", "fixed prompt", media, _validator)
    protected = {p: p.read_bytes() for p in (state.output / 'calls').glob('*/*') if p.is_file()}
    jobs = list(client.queue.glob('*.request.json'))
    def later_validator(value):
        raise ValueError('different_later_contract_error')
    with pytest.raises(ValueError, match='repair_exhausted:later'):
        client.call('later', 'fixed prompt', media, later_validator)
    assert state.usage()['requests'] == 2
    assert list(client.queue.glob('*.request.json')) == jobs
    assert all(p.read_bytes() == original for p, original in protected.items())
    diagnostics = list((state.output / 'artifacts/cached_reply_validation').glob('*.json'))
    assert len(diagnostics) == 2
    assert all(json.loads(p.read_text(encoding='utf-8'))['error'] == 'different_later_contract_error'
               for p in diagnostics)


def test_cross_stage_known_failure_cannot_gain_a_new_repair(task):
    state, media = task
    call, folder = state.begin_call('original', _request(media, 'fixed prompt'))
    state.complete_call(call, _reply('{"valid":false}'))
    write_json(folder / 'protocol_failure.json', {'error': 'original_failure'})
    protected = (folder / 'protocol_failure.json').read_bytes()
    with pytest.raises(LibraryStopped, match='historical_format_failure_no_new_repair'):
        CodexMCP(state).call('later', 'fixed prompt', media, _validator)
    assert state.usage()['requests'] == 1
    assert (folder / 'protocol_failure.json').read_bytes() == protected


def test_cross_stage_reinterpreted_failure_needs_explicit_derived_binding(task):
    state, media = task
    call, folder = state.begin_call('original', _request(media, 'fixed prompt'))
    state.complete_call(call, _reply('{"valid":true}'))
    write_json(folder / 'protocol_failure.json', {'error': 'old_stricter_failure'})
    with pytest.raises(LibraryStopped, match='historical_reply_requires_explicit_derived_binding'):
        CodexMCP(state).call('later', 'fixed prompt', media, _validator)
    assert not (folder / 'parsed.json').exists()
    assert state.usage()['requests'] == 1


def test_unknown_queue_reply_blocks_all_following_submissions(task):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [{"status": "unknown", "error": "reply lost after submission"}]):
        with pytest.raises(LibraryStopped, match="official_MCP_failure"):
            client.call("reference", "fixed prompt", media, _validator)
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached_client = CodexMCP(restarted, timeout_s=0.1)
    with pytest.raises(LibraryStopped, match="outcome_unknown"):
        cached_client.call("new", "changed prompt", media, _validator)
    assert restarted.usage()["requests"] == 1


def test_wait_timeout_keeps_submission_for_late_reply_recovery(task):
    state, media = task
    client = CodexMCP(state, timeout_s=0)
    with pytest.raises(LibraryStopped, match="wait_timed_out"):
        client.call("reference", "fixed prompt", media, _validator)
    assert state.data["calls"][0]["status"] == "submitted"
    call = state.data["calls"][0]
    write_json(state.output / "mcp_queue" / (call["id"] + ".response.json"), _reply('{"valid":true}'))
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    recovered = CodexMCP(restarted, timeout_s=0.1)
    assert recovered.call("reference", "fixed prompt", media, _validator) == {"valid": True}
    assert restarted.usage()["requests"] == 1


def test_blind_prompt_contains_no_reference_or_plan_payload():
    prompt = blind_prompt(21.9333)
    assert "21.9333" in prompt
    assert "独立描述" in prompt
    for field in ("reference_sha256", "intended_takeaway", "watched_windows", "segment_ids", "source_in_s"):
        assert field not in prompt


def _journal(output, entries):
    (output / "mcp_http.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries),
                                          encoding="utf-8")


def _http_response(call, *, content="", finish_reason="length", choices=True):
    body = {"usage": {"prompt_tokens": 55, "completion_tokens": 8192}}
    if choices:
        body["choices"] = [{"message": {"content": content}, "finish_reason": finish_reason}]
    return {"type": "response", "seq": 1, "job_id": call["id"], "status": 200,
            "body": json.dumps(body)}


def test_http_200_empty_length_reply_reconciles_failed_known_and_repairs_once(task):
    state, media = task
    call, folder = state.begin_call("reference", _request(media, "fixed prompt"))
    state.fail_call(call, "MCP isError: empty assistant content", uncertain=False)
    failed_snapshot = json.loads((folder / "failure.json").read_text(encoding="utf-8"))
    queue = state.output / "mcp_queue"
    queue.mkdir()
    error_reply = {"status": "error", "result": {"isError": True, "content": [
        {"type": "text", "text": "empty assistant final"}]}}
    write_json(queue / (call["id"] + ".response.json"), error_reply)
    _journal(state.output, [
        {"type": "request", "seq": 1, "job_id": call["id"]},
        _http_response(call, content="", finish_reason="length"),
    ])

    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    client = CodexMCP(restarted, timeout_s=2)
    reconciled = restarted.data["calls"][0]
    assert reconciled["status"] == "received"
    assert reconciled["reconciled_from_http"] is True
    assert reconciled["usage"]["completion_tokens"] == 8192
    reconciliation = json.loads((folder / "transport_reconciliation.json").read_text(encoding="utf-8"))
    assert reconciliation["previous_record"]["status"] == "failed_known"
    assert json.loads((folder / "failure.json").read_text(encoding="utf-8")) == failed_snapshot
    assert json.loads((queue / (call["id"] + ".response.json")).read_text(encoding="utf-8")) == error_reply
    recovered = json.loads((folder / "response.json").read_text(encoding="utf-8"))
    assert recovered["finish_reason"] == "length"
    assert recovered["result"]["content"][0]["text"] == ""

    with _fake_bridge(state.output, [_reply('{"valid":true}')]) as submitted:
        assert client.call("reference", "fixed prompt", media, _validator) == {"valid": True}
        assert len(submitted) == 1  # Only the distinct format repair is submitted.
        assert submitted[0]["job_id"] == restarted.data["calls"][1]["id"]
    assert restarted.usage()["requests"] == 2
    assert restarted.usage()["completion_tokens"] == 8192
    assert restarted.data["calls"][1]["repair_of"] == call["id"]

    # A second restart reuses the original HTTP evidence and successful repair.
    again = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached = CodexMCP(again, timeout_s=0.1)
    assert cached.call("reference", "fixed prompt", media, _validator) == {"valid": True}
    assert again.usage()["requests"] == 2
    assert again.usage()["completion_tokens"] == 8192


def test_length_failure_records_reasoning_usage_without_replaying_original(task):
    state, media = task
    call, folder = state.begin_call("search", _request(media, "fixed prompt"))
    entry = _http_response(call, content='{"windows":[', finish_reason="length")
    body = json.loads(entry["body"])
    body["usage"].update(completion_tokens=16384,
                         completion_tokens_details={"reasoning_tokens": 16124})
    entry["body"] = json.dumps(body)
    _journal(state.output, [entry])
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('{"valid":true}')]) as submitted:
        assert client.call("search", "fixed prompt", media, _validator) == {"valid": True}
        assert len(submitted) == 1
        assert submitted[0]["job_id"].endswith("search_repair")
        assert "不得删字段或证据" in submitted[0]["arguments"]["prompt"]
    failure = json.loads((folder / "protocol_failure.json").read_text(encoding="utf-8"))
    assert failure["output_limit"] == {"finish_reason": "length", "completion_tokens": 16384,
        "reasoning_tokens": 16124, "content_characters": len('{"windows":[')}
    protected = {p: p.read_bytes() for p in (state.output / "calls").glob("*/*") if p.is_file()}
    assert CodexMCP(state).call("search", "changed forward prompt", media, _validator) == {"valid": True}
    assert state.usage()["requests"] == 2
    assert all(p.read_bytes() == original for p, original in protected.items())


@pytest.mark.parametrize("unknown_record", [False, True])
def test_http_unknown_plus_tool_error_is_uncertain_and_blocks(task, unknown_record):
    state, media = task
    call, _ = state.begin_call("reference", _request(media, "fixed prompt"))
    queue = state.output / "mcp_queue"
    queue.mkdir()
    write_json(queue / (call["id"] + ".response.json"),
               {"status": "error", "result": {"isError": True, "content": []}, "error": "network failed"})
    entries = [{"type": "request", "seq": 1, "job_id": call["id"]}]
    if unknown_record:
        entries.append({"type": "unknown_result", "seq": 1, "job_id": call["id"], "error": "timeout"})
    _journal(state.output, entries)
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    client = CodexMCP(restarted, timeout_s=0.1)
    assert restarted.data["calls"][0]["status"] == "uncertain"
    with pytest.raises(LibraryStopped, match="outcome_unknown"):
        client.call("next", "different prompt", media, _validator)
    assert restarted.usage()["requests"] == 1


def test_http_body_without_choices_cannot_reconcile_failed_known(task):
    state, media = task
    call, folder = state.begin_call("reference", _request(media, "fixed prompt"))
    state.fail_call(call, "MCP error", uncertain=False)
    _journal(state.output, [_http_response(call, choices=False)])
    queue = state.output / "mcp_queue"
    queue.mkdir()
    write_json(queue / (call["id"] + ".response.json"), {"status": "error", "error": "no model reply"})
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    client = CodexMCP(restarted, timeout_s=0.1)
    assert restarted.data["calls"][0]["status"] == "failed_known"
    assert not (folder / "transport_reconciliation.json").exists()
    with pytest.raises(LibraryStopped, match="not_received_no_replay"):
        client.call("reference", "fixed prompt", media, _validator)
    with pytest.raises(LibraryStopped, match="requires_model_reply"):
        restarted.reconcile_received(call, _reply('{"valid":true}'), evidence=_http_response(call, choices=False))


def test_wrong_http_job_cannot_modify_state_or_reconciliation_artifacts(task):
    state, media = task
    call, folder = state.begin_call("reference", _request(media, "fixed prompt"))
    state.fail_call(call, "MCP error", uncertain=False)
    prior_state = json.loads(state.path.read_text(encoding="utf-8"))
    wrong = _http_response({"id": "glm_999_other_job"}, content='{"valid":true}', finish_reason="stop")
    with pytest.raises(LibraryStopped, match="job_mismatch"):
        state.reconcile_received(call, _reply('{"valid":true}'), evidence=wrong)
    assert json.loads(state.path.read_text(encoding="utf-8")) == prior_state
    assert not (folder / "transport_reconciliation.json").exists()
    _journal(state.output, [wrong])
    CodexMCP(state, timeout_s=0.1)
    assert state.data == prior_state
    assert not (folder / "response.json").exists()


def test_legacy_failed_known_with_http_unknown_must_be_reclassified_and_block(task):
    state, media = task
    call, _ = state.begin_call("reference", _request(media, "fixed prompt"))
    # Simulate the old bridge's overly broad "error -> failed_known" mapping.
    state.fail_call(call, "tool error was previously classified as known", uncertain=False)
    queue = state.output / "mcp_queue"
    queue.mkdir()
    write_json(queue / (call["id"] + ".response.json"), {"status": "error", "error": "network failure"})
    _journal(state.output, [{"type": "request", "seq": 1, "job_id": call["id"]},
                           {"type": "unknown_result", "seq": 1, "job_id": call["id"]}])
    client = CodexMCP(state, timeout_s=0.1)
    assert state.data["calls"][0]["status"] == "uncertain"
    with pytest.raises(LibraryStopped, match="outcome_unknown"):
        client.call("next", "different prompt", media, _validator)


def test_codex_new_independent_media_automatically_binds_real_lineage(tmp_path):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "test_ref"}, max_requests=8)
    write_json(state.output / "mcp_ready.json", {"test_fake": True})
    lost_media = tmp_path / "lost" / "window.mp4"
    lost_media.parent.mkdir()
    lost_media.write_bytes(b"old unknown input")
    old_scope = {"kind": "continuous_window", "source_sha256": "a" * 64,
                 "source_start_s": 600, "source_end_s": 690}
    write_json(lost_media.parent / "lineage.json", old_scope)
    # Original old request did not carry the new scope field.
    lost, _ = state.begin_call("old", _request(lost_media, "old prompt"))
    state.fail_call(lost, "unknown paid result", uncertain=True)
    state.enable_independent_continuation()
    new_media = tmp_path / "new" / "window.mp4"
    new_media.parent.mkdir()
    new_media.write_bytes(b"different measured input")
    new_scope = {"kind": "continuous_window", "source_sha256": "a" * 64,
                 "source_start_s": 3000, "source_end_s": 3090}
    write_json(new_media.parent / "lineage.json", new_scope)
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('{"valid":true}')]) as submitted:
        assert client.call("new", "independent new observation", new_media, _validator) == {"valid": True}
        assert len(submitted) == 1
    latest = state.data["calls"][1]
    request = json.loads((state.output / "calls" / latest["id"] / "request.json").read_text(encoding="utf-8"))
    assert request["observation_scope"] == new_scope
    assert state.usage()["requests"] == 2
    assert state.usage()["uncertain_calls"] == [lost["id"]]


def test_codex_continuation_does_not_mutate_digest_of_received_legacy_reference(task):
    state, media = task
    request = _request(media, "fixed prompt")
    received, folder = state.begin_call("reference", request)
    state.complete_call(received, _reply('{"valid":true}'))
    saved_request = (folder / "request.json").read_bytes()
    lost_request = {"media_sha256": "another_media", "observation_scope": {
        "kind": "continuous_window", "source_sha256": "a" * 64, "source_start_s": 600, "source_end_s": 690}}
    lost, _ = state.begin_call("unknown", lost_request)
    state.fail_call(lost, "unknown paid result", uncertain=True)
    state.enable_independent_continuation()
    client = CodexMCP(state, timeout_s=0.1)
    assert client.call("reference", "fixed prompt", media, _validator) == {"valid": True}
    assert state.usage()["requests"] == 2
    assert (folder / "request.json").read_bytes() == saved_request
    assert list((state.output / "mcp_queue").glob("*.request.json")) == []


def test_same_stage_new_context_reuses_original_received_reply(task):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('{"valid":true}')]) as submitted:
        client.call("overview", "original known_roles=[]", media, _validator)
        assert len(submitted) == 1
    original = state.data["calls"][0]
    request_path = state.output / "calls" / original["id"] / "request.json"
    prior_bytes = request_path.read_bytes()
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached = CodexMCP(restarted, timeout_s=0.1)
    assert cached.call("overview", "changed known_roles=[new_role]", media, _validator) == {"valid": True}
    assert restarted.usage()["requests"] == 1
    assert request_path.read_bytes() == prior_bytes
    assert len(list((state.output / "mcp_queue").glob("*.request.json"))) == 1


def test_same_stage_changed_context_repairs_only_from_original_prompt(task):
    state, media = task
    original_prompt = "ORIGINAL_CONTEXT known_roles=[]"
    original, folder = state.begin_call("overview", _request(media, original_prompt))
    state.complete_call(original, _reply('not json'))
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('{"valid":true}')]) as submitted:
        assert client.call("overview", "NEW_CONTEXT known_roles=[later_zoom]", media, _validator) == {"valid": True}
        assert len(submitted) == 1
        assert submitted[0]["arguments"]["prompt"].startswith(original_prompt)
        assert "NEW_CONTEXT" not in submitted[0]["arguments"]["prompt"]
    assert state.usage()["requests"] == 2
    assert state.data["calls"][1]["repair_of"] == original["id"]
    saved_original = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    assert saved_original["arguments"]["prompt"] == original_prompt
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached = CodexMCP(restarted, timeout_s=0.1)
    assert cached.call("overview", "THIRD_CONTEXT more roles", media, _validator) == {"valid": True}
    assert restarted.usage()["requests"] == 2


def test_same_paid_stage_media_change_is_rejected_without_new_submission(task):
    state, media = task
    original, _ = state.begin_call("overview", _request(media, "original context"))
    state.complete_call(original, _reply('{"valid":true}'))
    media.write_bytes(b"a different media file under the same path")
    client = CodexMCP(state, timeout_s=0.1)
    with pytest.raises(LibraryStopped, match="paid_stage_input_changed"):
        client.call("overview", "new context", media, _validator)
    assert state.usage()["requests"] == 1
    assert list((state.output / "mcp_queue").glob("*.request.json")) == []


def test_exhausted_stage_does_not_get_new_attempt_when_context_changes(task):
    state, media = task
    client = CodexMCP(state, timeout_s=2)
    with _fake_bridge(state.output, [_reply('not json'), _reply('{"valid":false}')]) as submitted:
        with pytest.raises(ValueError, match="repair_exhausted"):
            client.call("overview", "original known_roles=[]", media, _validator)
        assert len(submitted) == 2
    restarted = LibraryState(state.output, {"reference_sha256": "test_ref"}, max_requests=8)
    cached = CodexMCP(restarted, timeout_s=0.1)
    with pytest.raises(ValueError, match="repair_exhausted"):
        cached.call("overview", "new known_roles=[later_region_role]", media, _validator)
    assert restarted.usage()["requests"] == 2
    assert len(list((state.output / "mcp_queue").glob("*.request.json"))) == 2


def _synthetic_sheets(tmp_path, *, unknown_second):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "test_ref"}, max_requests=8)
    sources, sheets = [], {}
    for index in (1, 2):
        source = {"source_id": f"src_{index}", "sha256": str(index) * 64, "duration_s": 100,
                  "path": str(tmp_path / f"source_{index}.mp4"), "audio_stream_index": None}
        sources.append(source)
        media = tmp_path / f"sheet_{index}" / "contact_sheet.jpg"
        media.parent.mkdir()
        media.write_bytes(f"synthetic sheet {index}".encode())
        scope = {"kind": "sparse_contact_sheet", "source_sha256": source["sha256"],
                 "source_start_s": 0, "source_end_s": 100}
        sheet = {**scope, "spec": dict(scope), "source_id": source["source_id"], "path": str(media),
                 "frames": [{"frame_id": f"frame_{index}", "source_time_s": 50}], "sha256": sha256_file(media)}
        write_json(media.parent / "lineage.json", sheet)
        sheets[source["source_id"]] = sheet
        request = {**_request(media, "original overview"), "tool": "analyze_image",
                   "arguments": {"image_source": str(media), "prompt": "original overview"},
                   "observation_scope": scope}
        call, _ = state.begin_call("overview_" + source["source_id"], request)
        if index == 2 and unknown_second:
            state.fail_call(call, "lost original reply", uncertain=True)
        else:
            observed = {"source_id": source["source_id"], "coverage_s": [0, 100], "roles": [],
                        "events": [{"timestamp_s": 50, "observed_fact": "an observed landscape", "role_ids": []}],
                        "uncertainties": []}
            state.complete_call(call, _reply(json.dumps(observed)))
    return state, {"sources": sources}, sheets


def test_adaptive_unknown_last_overview_uses_valid_sheet_and_does_not_replay(tmp_path, monkeypatch):
    state, catalog, sheets = _synthetic_sheets(tmp_path, unknown_second=True)
    monkeypatch.setattr(library_pipeline, "create_contact_sheet",
                        lambda source, *args, **kwargs: sheets[source["source_id"]])
    seen = []

    class FakeGLM:
        def call(self, name, prompt, media, validator, **kwargs):
            seen.append((name, str(media)))
            assert name == "coarse_zoom_search"  # Neither overview is resubmitted.
            decision = {"reason": "reuse inspected region", "windows": [{"source_id": "src_1",
                        "start_s": 0, "end_s": 100, "question": "inspect", "role_ids": []}]}
            validator(decision)
            return decision

    coarse, failures, planning_image = library_pipeline._adaptive_coarse(
        state, FakeGLM(), catalog, {}, tmp_path / "cache", frames=18, span_s=600)
    assert seen == [("coarse_zoom_search", sheets["src_1"]["path"])]
    assert planning_image == sheets["src_1"]["path"]
    assert [entry["source_id"] for entry in coarse] == ["src_1"]
    assert any(entry["status"] == "unobserved_unknown_paid_result" for entry in failures)
    assert state.usage()["requests"] == 2


def test_adaptive_zoom_resume_uses_original_media_when_new_index_order_differs(tmp_path, monkeypatch):
    state, catalog, sheets = _synthetic_sheets(tmp_path, unknown_second=False)
    decision = {"reason": "original region selection", "windows": [{"source_id": "src_1",
                "start_s": 0, "end_s": 100, "question": "inspect", "role_ids": []}]}
    original_media = sheets["src_1"]["path"]
    request = {"tool": "analyze_image", "arguments": {"image_source": original_media, "prompt": "original selection"},
               "media_sha256": sha256_file(original_media), "policy_version": POLICY_VERSION}
    zoom, _ = state.begin_call("coarse_zoom_search", request)
    state.complete_call(zoom, _reply(json.dumps(decision)))
    monkeypatch.setattr(library_pipeline, "create_contact_sheet",
                        lambda source, *args, **kwargs: sheets[source["source_id"]])
    seen = []

    class FakeGLM:
        def call(self, name, prompt, media, validator, **kwargs):
            seen.append((name, str(media)))
            validator(decision)
            return decision

    library_pipeline._adaptive_coarse(state, FakeGLM(), catalog, {}, tmp_path / "cache", frames=18, span_s=600)
    assert seen == [("coarse_zoom_search", original_media)]
    assert state.usage()["requests"] == 3
def test_evidence_packing_preserves_observations_and_source_times():
    from copy import deepcopy
    from omni_story.library.pipeline import _window_context
    raw = {'window_id':'w_1','source_id':'s_1','source_sha256':'source_sha',
           'source_start_s':100,'source_end_s':110,'source_offset_s':100,
           'metadata':{'streams':[{'codec_name':'h264'}]},'path':'private_cache.mp4',
           'observation':{'events':[{'local_start_s':2,'local_end_s':3,'observed_fact':'visible action'}]},
           'asr':{'source_offset_s':100,'audio_stream_index':2,'segments':[
               {'source_start_s':102,'source_end_s':103,'local_start_s':2,'local_end_s':3,
                'text':'original ASR text','words':[{'text':'original','source_start_s':102}]}]}}
    frozen = deepcopy(raw)
    packed = _window_context(raw)
    assert raw == frozen
    assert packed['observation'] == raw['observation']
    assert packed['source_offset_s'] == 100
    assert packed['asr']['segments'][0]['text'] == 'original ASR text'
    assert packed['asr']['segments'][0]['source_start_s'] == 102
    assert 'metadata' not in packed and 'path' not in packed
    assert 'words' not in packed['asr']['segments'][0]
    assert 'asr' not in _window_context(raw, include_speech=False)


def test_console_entrypoint_returns_success_code_after_valid_result(tmp_path, monkeypatch):
    from omni_story.library.__main__ import main
    from omni_story.library import pipeline
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'fixture; execution stubbed')
    library = tmp_path / 'library'
    library.mkdir()
    def completed(ref, sources, output, *, asr, editing_v2=False, semantic_audit=False):
        assert ref == reference.resolve() and sources == library.resolve()
        assert asr is False
        assert editing_v2 is False
        assert semantic_audit is False
        return {'status':'model_checked_library_candidate','final_video':'actual.mp4'}
    monkeypatch.setattr(pipeline, 'execute', completed)
    assert main(['--reference',str(reference),'--library',str(library),
                 '--output',str(tmp_path/'run'),'--no-asr']) == 0


@pytest.mark.parametrize('next_round', [False, True])
def test_goal_entrypoint_dispatches_only_authorized_goal_workflow(tmp_path, monkeypatch, next_round):
    from omni_story.library.__main__ import main
    from omni_story.library import goal_feedback_continuation as goal, pipeline
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'fixture; execution stubbed')
    library = tmp_path / 'library'
    library.mkdir()
    def run(ref, sources, output, **kwargs):
        assert ref == reference.resolve() and sources == library.resolve()
        assert kwargs == ({'start_next':True} if next_round else {})
    monkeypatch.setattr(goal,'execute_goal_continuation',run)
    monkeypatch.setattr(pipeline,'execute',lambda *a,**k:pytest.fail('Goal used old route'))
    argv = ['--reference',str(reference),'--library',str(library),
            '--output',str(tmp_path/'run'),'--continue-goal']
    assert main(argv + (['--goal-next'] if next_round else [])) == 0


def test_goal_next_cannot_start_an_unrelated_route(tmp_path):
    from omni_story.library.__main__ import main
    with pytest.raises(SystemExit):
        main(['--reference','unused.mp4','--library','unused','--output',str(tmp_path),'--goal-next'])


def test_offline_editing_entrypoint_never_connects_model(tmp_path, monkeypatch, capsys):
    from omni_story.library.__main__ import main
    from omni_story.library import editing, pipeline
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'fixture; audit stubbed')
    output = tmp_path / 'run'
    output.mkdir()
    def offline(directory, ref):
        assert directory == output.resolve() and ref == reference.resolve()
        return {'report_path':'local_report.json','report':{'models_called':0,'new_renders':0}}
    monkeypatch.setattr(editing,'audit_existing_editing',offline)
    monkeypatch.setattr(pipeline,'execute',lambda *a,**k:pytest.fail('offline audit connected execution'))
    assert main(['--reference',str(reference),'--library',str(tmp_path/'unused'),
                 '--output',str(output),'--audit-editing']) == 0
    assert 'local_report.json' in capsys.readouterr().out
