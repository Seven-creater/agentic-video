"""Read-only proofs for one explicitly authorized, known HTTP 500 retry.

An unknown result, model response, quota failure or second transport failure is
never a retry grant. Registration appends a separate policy; old calls remain
failed and the original unused render is the only render authorization.
"""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import re

from .forward_slot_budget import _history, _prefix_sha
from .pipeline import _read
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import LibraryStopped, json_sha, scope_fingerprint

ARTIFACT = "mc2_known_http500_resume"
POLICY = "microclip_v2_known_http500_retry_v1"
BASELINE = 224
FAILED_CALL = "glm_224_mc2_region_1"
FAILED_STAGE = "mc2_region_1"
RETRY_STAGE = "mc2_region_1_retry"
DISPATCH_ARTIFACT = "mc2_known_http500_dispatch_resume"
DISPATCH_POLICY = "microclip_v2_known_http500_dispatch_resume_v1"
DISPATCH_BASELINE = 225
DISPATCH_FAILED_CALL = "glm_225_mc2_region_1_retry"
DISPATCH_STAGE = "mc2_region_1_retry_dispatch"
_JOURNAL_CACHE = {}
_REPAIR_PREFIX = "\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。"


def _require(condition, reason):
    if not condition:
        raise LibraryStopped("microclip_v2_known500:" + reason)


def _files(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def _journal_events(path, job_id, *, force=False):
    """Scan once, then only appended bytes; retain exact original line proofs."""
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    key = (str(path), job_id)
    stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
    old = None if force else _JOURNAL_CACHE.get(key)
    if old and old[0] == stamp:
        return deepcopy(old[1])
    selected, offset, index = [], 0, 0
    digest = hashlib.sha256()
    if old and stat.st_size >= old[0][0] and old[4]:
        _require(_prefix_sha(path, old[0][0]) == old[3].hexdigest(), "http_cached_prefix_changed")
        selected, offset, index, digest = deepcopy(old[1]), old[0][0], old[2], old[3].copy()
    needle = job_id.encode("utf-8")
    with path.open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            index += 1
            digest.update(line)
            if needle in line:
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError) as error:
                    raise LibraryStopped("microclip_v2_known500:invalid_http_evidence") from error
                if value.get("job_id") == job_id:
                    selected.append({"event": value, "offset": offset, "bytes": len(line),
                                     "line": index, "sha256": hashlib.sha256(line).hexdigest()})
            offset += len(line)
    end = path.stat()
    _require((end.st_size, end.st_mtime_ns, end.st_ctime_ns, end.st_ino) == stamp,
             "http_journal_changed_during_scan")
    # Only a complete newline boundary can be a subsequent append start.
    with path.open("rb") as stream:
        stream.seek(max(0, stat.st_size - 1))
        complete = stream.read(1) == b"\n"
    _JOURNAL_CACHE[key] = (stamp, deepcopy(selected), index, digest.copy(), complete)
    return selected


def _known_failure(output, data, auth, *, force=False):
    _require(data["request_count"] == len(data["calls"]) >= BASELINE, "request_count_changed")
    call = data["calls"][BASELINE - 1]
    _require(call["id"] == FAILED_CALL and call["name"] == FAILED_STAGE
             and call["status"] == "failed_known" and not call.get("repair_of"), "known_224_required")
    _require(all(c["status"] == "received" for c in data["calls"][218:BASELINE - 1]),
             "settled_known_predecessors_required")
    folder = output / "calls" / FAILED_CALL
    _require(not any((folder / name).exists() for name in
                     ("response.json", "parsed.json", "protocol_failure.json")), "failed_call_has_model_result")
    failure_path = folder / "failure.json"
    failure = _read(failure_path)
    _require(failure.get("uncertain") is False and isinstance(failure.get("error"), str)
             and failure["error"], "known_failure_receipt_required")
    request_path = folder / "request.json"
    request = _read(request_path)
    _require(json_sha(request) == call["request_sha256"]
             and request.get("provider") == "official_vision_mcp_in_codex"
             and request.get("tool") == "analyze_image" and "known_failure_retry_of" not in request,
             "official_original_image_request_required")
    arguments = request.get("arguments", {})
    _require(set(arguments) == {"image_source", "prompt"} and isinstance(arguments["prompt"], str)
             and arguments["prompt"] and sha256_file(arguments["image_source"], force=force) == request["media_sha256"],
             "original_request_media_changed")
    scope_fingerprint(request["observation_scope"])
    rows = data["artifacts"].get("mc2_input_" + FAILED_STAGE, [])
    _require(len(rows) == 1, "original_input_descriptor_required")
    descriptor_path = Path(rows[0]["path"]).resolve(strict=True)
    descriptor = _read(descriptor_path)
    _require(json_sha(descriptor) == rows[0]["sha256"] and descriptor["stage"] == FAILED_STAGE
             and descriptor["tool"] == request["tool"]
             and Path(descriptor["media_path"]).resolve() == Path(arguments["image_source"]).resolve()
             and descriptor["media_sha256"] == request["media_sha256"]
             and descriptor["observation_scope"] == request["observation_scope"]
             and sha256_file(descriptor["lineage_path"], force=force) == descriptor["lineage_sha256"],
             "original_input_descriptor_changed")
    queued_path = output / "mcp_queue" / (FAILED_CALL + ".request.json")
    queued = _read(queued_path)
    _require(queued["job_id"] == FAILED_CALL and queued["tool"] == request["tool"]
             and queued["arguments"] == arguments, "original_queue_request_changed")
    reply_path = output / "mcp_queue" / (FAILED_CALL + ".response.json")
    reply = _read(reply_path)
    result = reply.get("result", {})
    content = result.get("content", [])
    _require(reply.get("status") == "error" and result.get("isError") is True
             and content and all(row.get("type") == "text" and
                 isinstance(row.get("text"), str) and row["text"].startswith("Error:") for row in content)
             and not any(key in reply or key in result for key in ("choices", "usage", "model")),
             "error_only_queue_response_required")
    journal = output / "mcp_http.jsonl"
    events = _journal_events(journal, FAILED_CALL, force=force)
    _require(len(events) == 2 and [row["event"].get("type") for row in events] == ["request", "response"],
             "exactly_one_http_attempt_required")
    sent, received = [row["event"] for row in events]
    _require(sent.get("seq") == received.get("seq") and isinstance(sent.get("seq"), int)
             and sent.get("url") == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
             and received.get("status") == 500, "matched_http500_required")
    try:
        body = json.loads(received["body"])
    except (KeyError, TypeError, ValueError) as error:
        raise LibraryStopped("microclip_v2_known500:captured_error_body_required") from error
    _require(set(body) == {"error"} and isinstance(body["error"], dict)
             and set(body["error"]) == {"code", "message"} and str(body["error"]["code"]) == "1234"
             and isinstance(body["error"]["message"], str)
             and body["error"]["message"].startswith("Internal network failure, error id: "),
             "only_internal_network_failure_supported")
    native = sent.get("body", {})
    _require(native.get("model") == "glm-5.3-flash", "original_native_model_changed")
    blocks = [block for message in native.get("messages", []) if isinstance(message.get("content"), list)
              for block in message["content"]]
    texts = [block["text"] for block in blocks if block.get("type") == "text"]
    images = [block.get("image_url", {}).get("url") for block in blocks if block.get("type") == "image_url"]
    _require(texts == [arguments["prompt"]] and len(images) == 1 and
             isinstance(images[0], str) and images[0].startswith("data:image/png;base64,"),
             "native_prompt_media_binding_changed")
    try:
        actual_media = base64.b64decode(images[0].split(",", 1)[1], validate=True)
    except ValueError as error:
        raise LibraryStopped("microclip_v2_known500:native_image_invalid") from error
    _require(hashlib.sha256(actual_media).hexdigest() == request["media_sha256"], "native_image_bytes_changed")
    recovered = data["artifacts"].get("mc2_recovered_result", [])
    _require(len(recovered) == 1 and data["artifacts"].get("mc2_infrastructure_resume"),
             "previous_cpu_resume_and_stop_required")
    receipt_path = Path(recovered[0]["path"]).resolve(strict=True)
    receipt = _read(receipt_path)
    result_path = Path(auth["execution_directory"]) / "result_recovered.json"
    stopped = _read(result_path)
    _require(json_sha(receipt) == recovered[0]["sha256"]
             and receipt["policy"] == "microclip_slot_finecut_v2"
             and Path(receipt["result_path"]).resolve() == result_path.resolve()
             and receipt.get("model_status") == stopped.get("status") == "stopped"
             and stopped.get("error") == "official_MCP_failure:" + FAILED_CALL
             and stopped.get("model_quality_gate_passed") is False
             and all(stopped.get(key) is None for key in ("final_video", "final_sha256", "measured_duration_s")),
             "known_http_stop_only")
    _require(not data["artifacts"].get("mc2_render_claim") and not
             data["artifacts"].get("mc2_network_result") and not Path(auth["allowed_render_directory"]).exists(),
             "unused_original_render_required")
    return {"bound_request": request, "bound_input_descriptor": descriptor,
            "descriptor_path": str(descriptor_path), "descriptor_sha256": sha256_file(descriptor_path, force=force),
            "failed_result_path": str(result_path), "failed_result_sha256": sha256_file(result_path, force=force),
            "failed_receipt_path": str(receipt_path), "failed_receipt_sha256": sha256_file(receipt_path, force=force),
            "failure_path": str(failure_path), "failure_sha256": sha256_file(failure_path, force=force),
            "queue_response_path": str(reply_path), "queue_response_sha256": sha256_file(reply_path, force=force),
            "original_http": {"path": str(journal.resolve()), "seq": sent["seq"], "status": 500,
                "error_body": body, "events": [{k: row[k] for k in ("offset", "bytes", "line", "sha256")} for row in events]}}


def load_resume(output, data, auth, *, force=False):
    rows = data["artifacts"].get(ARTIFACT, [])
    if not rows:
        return None
    output = Path(output).resolve(strict=True)
    _require(len(rows) == 1, "one_retry_authorization_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    p = _read(path)
    _require(path.is_relative_to(output / "artifacts") and json_sha(p) == rows[0]["sha256"]
             and p["policy"] == POLICY and p["task_id"] == data["task_id"]
             and p["baseline_request_count"] == BASELINE
             and p["original_authorization_sha256"] == sha256_file(auth["authorization_path"], force=force)
             and p["failed_call_id"] == FAILED_CALL and p["failed_stage"] == FAILED_STAGE
             and p["retry_stage"] == RETRY_STAGE and p["max_transport_retries"] == p["repairs_per_stage"] == 1
             and p["new_renders"] == 0 and p["reuse_original_unused_render"] is True
             and p["goal_resumed"] is False and isinstance(p["user_instruction"], str) and p["user_instruction"].strip()
             and Path(p["unused_render_sentinel"]).resolve() ==
                 Path(auth["execution_directory"]).resolve() / ".known500_original_unused_render"
             and p["prefix_calls_sha256"] == json_sha(data["calls"][:BASELINE])
             and set(p["baseline_call_files"]) == {c["id"] for c in data["calls"][:BASELINE]},
             "retry_authorization_binding_changed")
    for key, previous in p["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == previous, "historical_artifact_changed")
    for ident, previous in p["baseline_call_files"].items():
        _require(_files(output / "calls" / ident) == previous, "historical_call_files_changed")
    for row in p["protected_files"]:
        _require(sha256_file(row["path"], force=force) == row["sha256"], "historical_bytes_changed")
    for row in p["journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "historical_journal_changed")
    # Validate the original failure against frozen baseline data. Current output
    # may now exist under the original grant; it cannot erase the old stop.
    baseline = deepcopy(data)
    baseline["calls"] = baseline["calls"][:BASELINE]
    baseline["request_count"] = BASELINE
    baseline["artifacts"] = deepcopy(p["baseline_artifacts"])
    known = _known_failure(output, baseline, {**auth, "allowed_render_directory": p["unused_render_sentinel"]}, force=force)
    _require(all(p.get(key) == value for key, value in known.items()), "known_failure_proof_changed")
    from .microclip_v2_state import STAGES
    added = data["calls"][BASELINE:]
    if added:
        first = added[0]
        _require(first["id"] == "glm_225_" + RETRY_STAGE and first["name"] == RETRY_STAGE
                 and not first.get("repair_of"), "retry_must_be_first_new_call")
    dispatch = load_dispatch_resume(output, data, auth, p, force=force)
    seen = set()
    for index, call in enumerate(added):
        _require(call["name"] not in seen and call["status"] in {"received", "submitted", "uncertain", "failed_known"},
                 "new_stage_repeated_or_invalid")
        seen.add(call["name"])
        request_path = output / "calls" / call["id"] / "request.json"
        request = _read(request_path)
        _require(json_sha(request) == call["request_sha256"], "new_request_changed")
        if call["name"] in {DISPATCH_STAGE, DISPATCH_STAGE + "_repair"}:
            _require(dispatch is not None, "dispatch_resume_required")
        elif call["name"] in {RETRY_STAGE, RETRY_STAGE + "_repair"}:
            original = deepcopy(request)
            _require(original.pop("known_failure_retry_of", None) == FAILED_CALL, "retry_parent_required")
            if call["name"].endswith("_repair"):
                _require(index == 1 and call.get("repair_of") == added[0]["id"]
                         and added[0]["status"] == "received", "sole_retry_format_repair_required")
                prompt = original["arguments"]["prompt"]
                expected = p["bound_request"]["arguments"]["prompt"]
                _require(prompt.startswith(expected + _REPAIR_PREFIX), "repair_prompt_must_preserve_original")
                original["arguments"]["prompt"] = expected
            _require(original == p["bound_request"], "retry_request_differs_from_original")
        else:
            _require(re.fullmatch(STAGES, call["name"]) and not call["name"].startswith(FAILED_STAGE)
                     and "known_failure_retry_of" not in request, "later_retry_not_authorized")
    return p


def _known_dispatch_stop(output, data, auth, known, *, force=False):
    """The local guard denied dispatch: this reservation made zero POSTs."""
    _require(data["request_count"] == len(data["calls"]) >= DISPATCH_BASELINE,
             "dispatch_count_changed")
    call = data["calls"][DISPATCH_BASELINE - 1]
    _require(call["id"] == DISPATCH_FAILED_CALL and call["name"] == RETRY_STAGE
             and call["status"] == "failed_known" and not call.get("repair_of"),
             "known_no_dispatch_225_required")
    folder = output / "calls" / DISPATCH_FAILED_CALL
    _require(not any((folder / name).exists() for name in
                     ("response.json", "parsed.json", "protocol_failure.json")),
             "undispatched_call_has_model_result")
    request = _read(folder / "request.json")
    expected = {**known["bound_request"], "known_failure_retry_of": FAILED_CALL}
    _require(request == expected and json_sha(request) == call["request_sha256"],
             "original_225_request_changed")
    failure_path = folder / "failure.json"
    failure = _read(failure_path)
    _require(failure.get("uncertain") is False and isinstance(failure.get("error"), str)
             and "library_mcp_retry_or_budget_blocked" in failure["error"],
             "exact_local_dispatch_error_required")
    queue_path = output / "mcp_queue" / (DISPATCH_FAILED_CALL + ".request.json")
    queue = _read(queue_path)
    _require(queue["job_id"] == DISPATCH_FAILED_CALL and queue["tool"] == request["tool"]
             and queue["arguments"] == request["arguments"], "undispatched_queue_request_changed")
    response_path = output / "mcp_queue" / (DISPATCH_FAILED_CALL + ".response.json")
    response = _read(response_path)
    message = "Error: Unexpected error: analyze-image analysis failed: Network error: library_mcp_retry_or_budget_blocked"
    _require(response.get("status") == "error" and response.get("result") ==
             {"content": [{"type": "text", "text": message}], "isError": True},
             "only_known_dispatch_guard_error_supported")
    journals = sorted(output.glob("mcp_http*.jsonl"))
    _require(journals and output / "mcp_http.jsonl" in journals, "dispatch_http_journal_required")
    no_dispatch = []
    for journal in journals:
        _require(not _journal_events(journal, DISPATCH_FAILED_CALL, force=force),
                 "225_has_actual_http_evidence")
        size = journal.stat().st_size
        no_dispatch.append({"path": str(journal.resolve()), "bytes": size,
                            "sha256": _prefix_sha(journal, size, force=force)})
    rows = data["artifacts"].get("mc2_input_" + RETRY_STAGE, [])
    _require(len(rows) == 1, "undispatched_input_descriptor_required")
    descriptor_path = Path(rows[0]["path"]).resolve(strict=True)
    descriptor = _read(descriptor_path)
    _require(json_sha(descriptor) == rows[0]["sha256"] and descriptor ==
             {**known["bound_input_descriptor"], "stage": RETRY_STAGE},
             "undispatched_descriptor_changed")
    stop_rows = data["artifacts"].get("mc2_network_result", [])
    _require(len(stop_rows) == 1, "dispatch_stop_receipt_required")
    receipt_path = Path(stop_rows[0]["path"]).resolve(strict=True)
    receipt = _read(receipt_path)
    result_path = Path(auth["execution_directory"]) / "result_network_recovered.json"
    result = _read(result_path)
    _require(json_sha(receipt) == stop_rows[0]["sha256"] and receipt["policy"] == "microclip_slot_finecut_v2"
             and Path(receipt["result_path"]).resolve() == result_path.resolve()
             and receipt.get("model_status") == result.get("status") == "stopped"
             and result.get("error") == "official_MCP_failure:" + DISPATCH_FAILED_CALL
             and result.get("model_quality_gate_passed") is False
             and all(result.get(key) is None for key in ("final_video", "final_sha256", "measured_duration_s")),
             "known_no_dispatch_stop_only")
    _require(not data["artifacts"].get("mc2_render_claim") and not
             data["artifacts"].get("mc2_dispatch_result") and not Path(auth["allowed_render_directory"]).exists(),
             "dispatch_unused_original_render_required")
    parent_rows = data["artifacts"].get(ARTIFACT, [])
    _require(len(parent_rows) == 1 and _read(parent_rows[0]["path"]) == known,
             "original_known500_grant_changed")
    return {"parent_resume_path": parent_rows[0]["path"],
            "parent_resume_sha256": sha256_file(parent_rows[0]["path"], force=force),
            "bound_request": deepcopy(known["bound_request"]),
            "bound_input_descriptor": descriptor,
            "descriptor_path": str(descriptor_path), "descriptor_sha256": sha256_file(descriptor_path, force=force),
            "failed_result_path": str(result_path), "failed_result_sha256": sha256_file(result_path, force=force),
            "failed_receipt_path": str(receipt_path), "failed_receipt_sha256": sha256_file(receipt_path, force=force),
            "failure_path": str(failure_path), "failure_sha256": sha256_file(failure_path, force=force),
            "queue_response_path": str(response_path), "queue_response_sha256": sha256_file(response_path, force=force),
            "no_dispatch_journal_prefixes": no_dispatch}


def load_dispatch_resume(output, data, auth, known, *, force=False):
    rows = data["artifacts"].get(DISPATCH_ARTIFACT, [])
    if not rows:
        return None
    output = Path(output).resolve(strict=True)
    _require(len(rows) == 1, "one_dispatch_recovery_required")
    path = Path(rows[0]["path"]).resolve(strict=True)
    p = _read(path)
    _require(path.is_relative_to(output / "artifacts") and json_sha(p) == rows[0]["sha256"]
             and p["policy"] == DISPATCH_POLICY and p["task_id"] == data["task_id"]
             and p["baseline_request_count"] == DISPATCH_BASELINE
             and p["original_authorization_sha256"] == sha256_file(auth["authorization_path"], force=force)
             and p["failed_call_id"] == DISPATCH_FAILED_CALL and p["failed_stage"] == RETRY_STAGE
             and p["retry_stage"] == DISPATCH_STAGE and p["max_transport_retries"] == p["repairs_per_stage"] == 1
             and p["actual_post_count_for_failed_call"] == p["new_renders"] == 0
             and p["reuse_original_unused_render"] is True and p["goal_resumed"] is False
             and Path(p["unused_render_sentinel"]).resolve() ==
                 Path(auth["execution_directory"]).resolve() / ".known500_dispatch_unused_render"
             and isinstance(p["user_instruction"], str) and p["user_instruction"].strip()
             and p["prefix_calls_sha256"] == json_sha(data["calls"][:DISPATCH_BASELINE])
             and set(p["baseline_call_files"]) == {c["id"] for c in data["calls"][:DISPATCH_BASELINE]},
             "dispatch_policy_binding_changed")
    for key, previous in p["baseline_artifacts"].items():
        _require(data["artifacts"].get(key) == previous, "dispatch_historical_artifact_changed")
    for ident, previous in p["baseline_call_files"].items():
        _require(_files(output / "calls" / ident) == previous, "dispatch_historical_call_files_changed")
    for row in p["protected_files"]:
        _require(sha256_file(row["path"], force=force) == row["sha256"], "dispatch_historical_bytes_changed")
    for row in p["journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "dispatch_journal_changed")
    baseline = deepcopy(data)
    baseline["calls"] = baseline["calls"][:DISPATCH_BASELINE]
    baseline["request_count"] = DISPATCH_BASELINE
    baseline["artifacts"] = deepcopy(p["baseline_artifacts"])
    current = _known_dispatch_stop(output, baseline,
        {**auth, "allowed_render_directory": p["unused_render_sentinel"]}, known, force=force)
    # Journals may append independent new work. Their frozen prefix is already
    # validated above; zero events for 225 must remain true throughout the tail.
    current.pop("no_dispatch_journal_prefixes")
    _require(all(p.get(key) == value for key, value in current.items()), "no_dispatch_proof_changed")
    for row in p["no_dispatch_journal_prefixes"]:
        _require(_prefix_sha(row["path"], row["bytes"], force=force) == row["sha256"], "no_dispatch_prefix_changed")
    added = data["calls"][DISPATCH_BASELINE:]
    if added:
        _require(added[0]["id"] == "glm_226_" + DISPATCH_STAGE and added[0]["name"] == DISPATCH_STAGE
                 and not added[0].get("repair_of"), "dispatch_must_be_first_new_call")
    seen = set()
    from .microclip_v2_state import STAGES
    for index, call in enumerate(added):
        _require(call["name"] not in seen, "dispatch_stage_repeated")
        seen.add(call["name"])
        request = _read(output / "calls" / call["id"] / "request.json")
        _require(json_sha(request) == call["request_sha256"], "dispatch_request_changed")
        if call["name"] in {DISPATCH_STAGE, DISPATCH_STAGE + "_repair"}:
            original = deepcopy(request)
            _require(original.pop("known_failure_retry_of", None) == DISPATCH_FAILED_CALL,
                     "dispatch_parent_required")
            if call["name"].endswith("_repair"):
                _require(index == 1 and call.get("repair_of") == added[0]["id"] and
                         added[0]["status"] == "received", "sole_dispatch_format_repair_required")
                expected = p["bound_request"]["arguments"]["prompt"]
                _require(original["arguments"]["prompt"].startswith(expected + _REPAIR_PREFIX),
                         "dispatch_repair_prompt_changed")
                original["arguments"]["prompt"] = expected
            _require(original == p["bound_request"], "dispatch_request_differs_from_original")
        else:
            _require(re.fullmatch(STAGES, call["name"]) and not call["name"].startswith(FAILED_STAGE)
                     and "known_failure_retry_of" not in request, "another_dispatch_retry_not_authorized")
    return p


def record_dispatch_resume(output, user_instruction):
    from .microclip_v2_state import get_auth, SlotMicroclipState
    output = Path(output).resolve(strict=True)
    auth = get_auth(output, force=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(DISPATCH_ARTIFACT):
        return auth
    _require(isinstance(user_instruction, str) and user_instruction.strip()
             and data["request_count"] == len(data["calls"]) == DISPATCH_BASELINE
             and auth.get("known_failure_resume") is not None, "settled_225_and_original_grant_required")
    known = _known_dispatch_stop(output, data, auth, auth["known_failure_resume"], force=True)
    files, prefixes = _history(output)
    by_path = {row["path"]: row for row in files}
    for row in auth["protected_files"]:
        by_path.setdefault(row["path"], row)
    payload = {"policy": DISPATCH_POLICY, "task_id": data["task_id"],
        "original_authorization_sha256": sha256_file(auth["authorization_path"], force=True),
        "baseline_request_count": DISPATCH_BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
        "baseline_artifacts": deepcopy(data["artifacts"]),
        "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"]},
        "protected_files": sorted(by_path.values(), key=lambda row: row["path"]), "journal_prefixes": prefixes,
        "failed_call_id": DISPATCH_FAILED_CALL, "failed_stage": RETRY_STAGE, "retry_stage": DISPATCH_STAGE,
        "max_transport_retries": 1, "repairs_per_stage": 1, "actual_post_count_for_failed_call": 0,
        "new_renders": 0, "reuse_original_unused_render": True, "goal_resumed": False,
        "unused_render_sentinel": str(Path(auth["execution_directory"]) / ".known500_dispatch_unused_render"),
        "user_instruction": user_instruction, **known}
    SlotMicroclipState(output).set_artifact(DISPATCH_ARTIFACT, payload)
    return get_auth(output, force=True)


def record_resume(output, user_instruction):
    """Append permission only after full validation of the settled known stop."""
    from .microclip_v2_state import get_auth, SlotMicroclipState
    output = Path(output).resolve(strict=True)
    auth = get_auth(output, force=True)
    data = _read(output / "library_state.json")
    if data["artifacts"].get(ARTIFACT):
        return auth
    _require(isinstance(user_instruction, str) and user_instruction.strip()
             and data["request_count"] == len(data["calls"]) == BASELINE,
             "explicit_continue_at_settled_224_required")
    known = _known_failure(output, data, auth, force=True)
    files, prefixes = _history(output)
    by_path = {row["path"]: row for row in files}
    for row in auth["protected_files"]:
        by_path.setdefault(row["path"], row)
    payload = {"policy": POLICY, "task_id": data["task_id"],
        "original_authorization_sha256": sha256_file(auth["authorization_path"], force=True),
        "baseline_request_count": BASELINE, "prefix_calls_sha256": json_sha(data["calls"]),
        "baseline_artifacts": deepcopy(data["artifacts"]),
        "baseline_call_files": {c["id"]: _files(output / "calls" / c["id"]) for c in data["calls"]},
        "protected_files": sorted(by_path.values(), key=lambda row: row["path"]), "journal_prefixes": prefixes,
        "failed_call_id": FAILED_CALL, "failed_stage": FAILED_STAGE, "retry_stage": RETRY_STAGE,
        "max_transport_retries": 1, "repairs_per_stage": 1, "new_renders": 0,
        "reuse_original_unused_render": True, "goal_resumed": False,
        # The sentinel is a bound, never-created path used only when validating
        # the historical unused-render condition after the actual render exists.
        "unused_render_sentinel": str(Path(auth["execution_directory"]) / ".known500_original_unused_render"),
        "user_instruction": user_instruction, **known}
    SlotMicroclipState(output).set_artifact(ARTIFACT, payload)
    return get_auth(output, force=True)
