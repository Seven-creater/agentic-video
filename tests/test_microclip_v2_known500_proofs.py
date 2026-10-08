"""Synthetic known transport proof tests: no account, model or actual movie."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import microclip_v2_known500 as proof, microclip_v2_state as mc
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def known500(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    folder = root / "artifacts" / "microclip_slot_finecut_v2"
    folder.mkdir(parents=True)
    media = folder / "grid.png"
    media.write_bytes(b"synthetic actual PNG input")
    lineage = folder / "manifest.json"
    write_json(lineage, {"synthetic": "bound frame table"})
    scope = {"kind": "sparse_contact_sheet", "source_sha256": "e" * 64,
             "source_start_s": 6.0, "source_end_s": 9.04}
    request = {"tool": "analyze_image", "provider": "official_vision_mcp_in_codex",
               "arguments": {"image_source": str(media), "prompt": "Original neutral observation prompt."},
               "media_sha256": sha256_file(media), "policy_version": "original",
               "observation_scope": scope}
    descriptor = {"stage": proof.FAILED_STAGE, "tool": request["tool"], "media_path": str(media),
                  "media_sha256": request["media_sha256"], "observation_scope": scope,
                  "lineage_path": str(lineage), "lineage_sha256": sha256_file(lineage)}
    data = {"task_id": "synthetic", "request_count": 224, "calls": [], "artifacts": {}}
    for n in range(1, 225):
        ident = proof.FAILED_CALL if n == 224 else "old_%03d" % n
        call_folder = root / "calls" / ident
        call_folder.mkdir(parents=True)
        value = request if n == 224 else {"historical": n}
        write_json(call_folder / "request.json", value)
        data["calls"].append({"id": ident, "name": proof.FAILED_STAGE if n == 224 else "old_stage",
                              "status": "failed_known" if n == 224 else "received",
                              "request_sha256": json_sha(value), "repair_of": None})
    original = root / "calls" / proof.FAILED_CALL
    write_json(original / "failure.json", {"error": "Error: wrapper network failure", "uncertain": False})
    queued = root / "mcp_queue"
    queued.mkdir()
    write_json(queued / (proof.FAILED_CALL + ".request.json"),
               {"job_id": proof.FAILED_CALL, "tool": request["tool"], "arguments": request["arguments"]})
    write_json(queued / (proof.FAILED_CALL + ".response.json"),
               {"status": "error", "result": {"isError": True,
                "content": [{"type": "text", "text": "Error: known transport wrapper failure"}]}})
    sent = {"type": "request", "seq": 150, "job_id": proof.FAILED_CALL,
            "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "body": {"model": "glm-5.3-flash", "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                    base64.b64encode(media.read_bytes()).decode()}}, {"type": "text", "text": request["arguments"]["prompt"]}]}]}}
    received = {"type": "response", "seq": 150, "job_id": proof.FAILED_CALL, "status": 500,
                "body": json.dumps({"error": {"code": "1234", "message": "Internal network failure, error id: synthetic, please try again later."}})}
    journal = root / "mcp_http.jsonl"
    journal.write_text("\n".join(json.dumps(row) for row in (sent, received)) + "\n", encoding="utf-8")
    auth_path = root / "artifacts" / "microclip_slot_finecut_v2_001.json"
    auth = {"execution_directory": str(folder), "allowed_render_directory": str(folder / "render"),
            "authorization_path": str(auth_path), "protected_files": []}
    write_json(auth_path, auth)
    data["artifacts"]["microclip_slot_finecut_v2"] = [{"path": str(auth_path), "sha256": json_sha(auth)}]
    result_path = folder / "result_recovered.json"
    write_json(result_path, {"status": "stopped", "error": "official_MCP_failure:" + proof.FAILED_CALL,
                            "model_quality_gate_passed": False, "final_video": None,
                            "final_sha256": None, "measured_duration_s": None})

    def artifact(name, value):
        path = root / "artifacts" / (name + "_001.json")
        write_json(path, value)
        current = read(root / "library_state.json") if (root / "library_state.json").exists() else data
        current["artifacts"][name] = [{"path": str(path), "sha256": json_sha(value)}]
        write_json(root / "library_state.json", current)
        return path

    artifact("mc2_input_" + proof.FAILED_STAGE, descriptor)
    artifact("mc2_infrastructure_resume", {"old": "separate CPU proof"})
    artifact("mc2_recovered_result", {"policy": "microclip_slot_finecut_v2",
        "result_path": str(result_path), "model_status": "stopped"})
    checks = []

    def get_auth(output, *, force=False):
        checks.append(force)
        current = read(root / "library_state.json")
        value = deepcopy(auth)
        value["known_failure_resume"] = proof.load_resume(root, current, auth, force=force)
        value["known_failure_dispatch_resume"] = proof.load_dispatch_resume(root, current, auth,
            value["known_failure_resume"], force=force)
        return value

    monkeypatch.setattr(mc, "get_auth", get_auth)
    monkeypatch.setattr(mc, "SlotMicroclipState", lambda output: SimpleNamespace(set_artifact=artifact))
    return root, auth, request, checks


def node_load(root, auth):
    if not shutil.which("node"):
        pytest.skip("Node required")
    module = (Path(proof.__file__).parent / "mcp_microclip_known500.mjs").as_uri()
    code = f"import fs from 'node:fs';import {{loadKnownHttp500Resume}} from {json.dumps(module)};" \
        f"const root={json.dumps(str(root))};const state=JSON.parse(fs.readFileSync(root+'/library_state.json','utf8'));" \
        f"console.log(loadKnownHttp500Resume(root,state,{json.dumps(auth)}).baseline_request_count);"
    return subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True)


def test_exact_known500_registration_is_append_only_and_shared_with_node(known500):
    root, auth, request, checks = known500
    original = read(root / "library_state.json")["calls"]
    old_result = (Path(auth["execution_directory"]) / "result_recovered.json").read_bytes()
    result = proof.record_resume(root, "Continue the single corrected trial after the known service failure.")
    assert checks == [True, True]
    policy = result["known_failure_resume"]
    assert policy["baseline_request_count"] == 224 and policy["max_transport_retries"] == 1
    assert policy["bound_request"] == request and policy["new_renders"] == 0 and not policy["goal_resumed"]
    assert read(root / "library_state.json")["calls"] == original
    assert (Path(auth["execution_directory"]) / "result_recovered.json").read_bytes() == old_result
    assert node_load(root, auth).returncode == 0
    assert proof.record_resume(root, "already registered")["known_failure_resume"] == policy


@pytest.mark.parametrize("change", ["unknown", "status401", "quota", "model_content", "two_posts",
    "unmatched_response", "unknown_result", "missing_http", "parsed", "protocol_failure", "media", "native_prompt", "native_image", "result", "render"])
def test_registration_rejects_anything_but_the_exact_known_internal_500(known500, change):
    root, auth, request, _ = known500
    state_path = root / "library_state.json"
    data = read(state_path)
    journal = root / "mcp_http.jsonl"
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    if change == "unknown":
        data["calls"][-1]["status"] = "uncertain"
        write_json(state_path, data)
    elif change == "status401": events[-1]["status"] = 401
    elif change == "quota": events[-1]["body"] = json.dumps({"error": {"code": "1302", "message": "quota exceeded"}})
    elif change == "model_content": events[-1]["body"] = json.dumps({"error": {"code": "1234", "message": "Internal network failure, error id: x"}, "choices": []})
    elif change == "two_posts": events.append(events[0])
    elif change == "unmatched_response": events[-1]["seq"] = 151
    elif change == "unknown_result": events.append({"type": "unknown_result", "job_id": proof.FAILED_CALL})
    elif change == "missing_http": events.clear()
    elif change in {"parsed", "protocol_failure"}:
        write_json(root / "calls" / proof.FAILED_CALL / (change + ".json"), {"must": "remain absent"})
    elif change == "media": Path(request["arguments"]["image_source"]).write_bytes(b"modified")
    elif change == "native_prompt": events[0]["body"]["messages"][0]["content"][1]["text"] = "different prompt"
    elif change == "native_image": events[0]["body"]["messages"][0]["content"][0]["image_url"]["url"] = "data:image/png;base64,eA=="
    elif change == "result":
        path = Path(auth["execution_directory"]) / "result_recovered.json"
        value = read(path);value["error"] = "other failure";write_json(path, value)
    elif change == "render": Path(auth["allowed_render_directory"]).mkdir()
    journal.write_text("\n".join(json.dumps(row) for row in events) + "\n")
    with pytest.raises(LibraryStopped):
        proof.record_resume(root, "Continue, but this evidence is not the authorized known failure.")


@pytest.mark.parametrize("change", ["historical_reply", "old_call_added_file", "call_status", "journal_prefix", "duplicate_224_http"])
def test_loaded_proof_and_node_reject_mutated_frozen_history(known500, change):
    root, auth, _, _ = known500
    proof.record_resume(root, "One known failure continuation.")
    data = read(root / "library_state.json")
    if change == "historical_reply": (root / "calls" / "old_001" / "request.json").write_text("{}")
    elif change == "old_call_added_file": (root / "calls" / proof.FAILED_CALL / "parsed.json").write_text("{}")
    elif change == "call_status": data["calls"][-1]["status"] = "received";write_json(root / "library_state.json", data)
    elif change == "journal_prefix":
        journal = root / "mcp_http.jsonl";journal.write_text(journal.read_text().replace('"seq": 150', '"seq": 151'))
    else:
        journal = root / "mcp_http.jsonl"
        with journal.open("a") as stream: stream.write(journal.read_text().splitlines()[0] + "\n")
    with pytest.raises(LibraryStopped): proof.load_resume(root, read(root / "library_state.json"), auth, force=True)
    assert node_load(root, auth).returncode != 0


def test_exact_alias_then_one_preserving_format_repair(known500):
    root, auth, request, _ = known500
    proof.record_resume(root, "Continue once.")
    data = read(root / "library_state.json")
    request = {**request, "known_failure_retry_of": proof.FAILED_CALL}
    ident = "glm_225_" + proof.RETRY_STAGE
    write_json(root / "calls" / ident / "request.json", request)
    data["calls"].append({"id": ident, "name": proof.RETRY_STAGE, "status": "received",
                          "request_sha256": json_sha(request), "repair_of": None})
    fixed = deepcopy(request)
    fixed["arguments"]["prompt"] += proof._REPAIR_PREFIX + '{"validation_error":"format"}'
    repaired_id = "glm_226_" + proof.RETRY_STAGE + "_repair"
    write_json(root / "calls" / repaired_id / "request.json", fixed)
    data["calls"].append({"id": repaired_id, "name": proof.RETRY_STAGE + "_repair", "status": "submitted",
                          "request_sha256": json_sha(fixed), "repair_of": ident})
    data["request_count"] = 226
    write_json(root / "library_state.json", data)
    assert proof.load_resume(root, data, auth)["failed_call_id"] == proof.FAILED_CALL
    assert node_load(root, auth).returncode == 0
    fixed["arguments"]["prompt"] = "replaced original prompt"
    write_json(root / "calls" / repaired_id / "request.json", fixed)
    data["calls"][-1]["request_sha256"] = json_sha(fixed)
    write_json(root / "library_state.json", data)
    with pytest.raises(LibraryStopped, match="repair_prompt"):
        proof.load_resume(root, data, auth)
    assert node_load(root, auth).returncode != 0


def test_journal_append_checks_tail_without_reparsing_original_lines(known500, monkeypatch):
    root, auth, _, _ = known500
    proof.record_resume(root, "Continue once.")
    original_loads = proof.json.loads
    parsed = []
    def tracked(raw, *args, **kwargs):
        if isinstance(raw, bytes): parsed.append(raw)
        return original_loads(raw, *args, **kwargs)
    monkeypatch.setattr(proof.json, "loads", tracked)
    with (root / "mcp_http.jsonl").open("a") as stream:
        stream.write(json.dumps({"type": "request", "job_id": "later_independent_call"}) + "\n")
    proof.load_resume(root, read(root / "library_state.json"), auth)
    assert parsed == []


@pytest.fixture
def undispatched225(known500):
    root, auth, request, checks = known500
    proof.record_resume(root, "One bounded known HTTP 500 retry.")
    data = read(root / "library_state.json")
    request = {**request, "known_failure_retry_of": proof.FAILED_CALL}
    folder = root / "calls" / proof.DISPATCH_FAILED_CALL
    write_json(folder / "request.json", request)
    write_json(folder / "failure.json", {"error": "Error: library_mcp_retry_or_budget_blocked", "uncertain": False})
    data["calls"].append({"id": proof.DISPATCH_FAILED_CALL, "name": proof.RETRY_STAGE,
        "status": "failed_known", "request_sha256": json_sha(request), "repair_of": None})
    data["request_count"] = 225
    write_json(root / "mcp_queue" / (proof.DISPATCH_FAILED_CALL + ".request.json"),
               {"job_id": proof.DISPATCH_FAILED_CALL, "tool": request["tool"], "arguments": request["arguments"]})
    message = "Error: Unexpected error: analyze-image analysis failed: Network error: library_mcp_retry_or_budget_blocked"
    write_json(root / "mcp_queue" / (proof.DISPATCH_FAILED_CALL + ".response.json"),
               {"status": "error", "result": {"content": [{"type": "text", "text": message}], "isError": True}})
    parent = read(data["artifacts"][proof.ARTIFACT][0]["path"])
    descriptor = {**parent["bound_input_descriptor"], "stage": proof.RETRY_STAGE}
    result_path = Path(auth["execution_directory"]) / "result_network_recovered.json"
    write_json(result_path, {"status": "stopped", "error": "official_MCP_failure:" + proof.DISPATCH_FAILED_CALL,
        "model_quality_gate_passed": False, "final_video": None, "final_sha256": None, "measured_duration_s": None})
    for name, value in [("mc2_input_" + proof.RETRY_STAGE, descriptor), ("mc2_network_result",
        {"policy": "microclip_slot_finecut_v2", "result_path": str(result_path), "model_status": "stopped"})]:
        path = root / "artifacts" / (name + "_001.json")
        write_json(path, value)
        data["artifacts"][name] = [{"path": str(path), "sha256": json_sha(value)}]
    write_json(root / "library_state.json", data)
    return root, auth, request, checks


def test_no_post_dispatch_recovery_preserves_both_failed_calls_and_results(undispatched225):
    root, auth, request, _ = undispatched225
    old = read(root / "library_state.json")
    result = proof.record_dispatch_resume(root, "Fix the local dispatch guard; the original authorized POST retry is still unused.")
    dispatch = result["known_failure_dispatch_resume"]
    assert dispatch["baseline_request_count"] == 225 and dispatch["actual_post_count_for_failed_call"] == 0
    assert dispatch["retry_stage"] == proof.DISPATCH_STAGE and dispatch["new_renders"] == 0
    assert read(root / "library_state.json")["calls"] == old["calls"]
    assert node_load(root, auth).returncode == 0
    assert proof.record_dispatch_resume(root, "idempotent")["known_failure_dispatch_resume"] == dispatch


@pytest.mark.parametrize("change", ["post", "unknown", "quota", "model_reply", "parsed", "scope", "render"])
def test_dispatch_registration_requires_exact_guard_error_and_zero_http(undispatched225, change):
    root, auth, request, _ = undispatched225
    data = read(root / "library_state.json")
    if change == "post":
        with (root / "mcp_http.jsonl").open("a") as stream:
            stream.write(json.dumps({"type": "request", "job_id": proof.DISPATCH_FAILED_CALL, "seq": 151}) + "\n")
    elif change == "unknown": data["calls"][-1]["status"] = "uncertain";write_json(root / "library_state.json", data)
    elif change == "quota":
        path = root / "mcp_queue" / (proof.DISPATCH_FAILED_CALL + ".response.json")
        reply = read(path);reply["result"]["content"][0]["text"] = "Error: quota exceeded";write_json(path, reply)
    elif change in {"model_reply", "parsed"}:
        write_json(root / "calls" / proof.DISPATCH_FAILED_CALL / ("response.json" if change == "model_reply" else "parsed.json"), {"model": "content"})
    elif change == "scope":
        request["observation_scope"] = {**request["observation_scope"], "source_start_s": 7.0}
        write_json(root / "calls" / proof.DISPATCH_FAILED_CALL / "request.json", request)
        data["calls"][-1]["request_sha256"] = json_sha(request);write_json(root / "library_state.json", data)
    else: Path(auth["allowed_render_directory"]).mkdir()
    with pytest.raises(LibraryStopped): proof.record_dispatch_resume(root, "Continue the same unused authorized retry.")


def test_dispatch_alias_is_exactly_226_and_preserves_native_arguments(undispatched225):
    root, auth, _, _ = undispatched225
    resumed = proof.record_dispatch_resume(root, "Continue unused POST retry.")
    data = read(root / "library_state.json")
    request = {**resumed["known_failure_dispatch_resume"]["bound_request"],
               "known_failure_retry_of": proof.DISPATCH_FAILED_CALL}
    ident = "glm_226_" + proof.DISPATCH_STAGE
    write_json(root / "calls" / ident / "request.json", request)
    data["calls"].append({"id": ident, "name": proof.DISPATCH_STAGE, "status": "submitted",
                          "request_sha256": json_sha(request), "repair_of": None})
    data["request_count"] = 226
    write_json(root / "library_state.json", data)
    assert proof.load_resume(root, data, auth)["baseline_request_count"] == 224
    assert node_load(root, auth).returncode == 0
    request["arguments"] = {**request["arguments"], "prompt": "different creative instruction"}
    write_json(root / "calls" / ident / "request.json", request)
    data["calls"][-1]["request_sha256"] = json_sha(request);write_json(root / "library_state.json", data)
    with pytest.raises(LibraryStopped, match="differs_from_original"):
        proof.load_resume(root, data, auth)
    assert node_load(root, auth).returncode != 0


def test_dispatch_proof_rejects_later_added_225_http_in_python_and_node(undispatched225):
    root, auth, _, _ = undispatched225
    proof.record_dispatch_resume(root, "Continue unused retry.")
    with (root / "mcp_http.jsonl").open("a") as stream:
        stream.write(json.dumps({"type": "unknown_result", "job_id": proof.DISPATCH_FAILED_CALL}) + "\n")
    with pytest.raises(LibraryStopped, match="actual_http"):
        proof.load_resume(root, read(root / "library_state.json"), auth)
    assert node_load(root, auth).returncode != 0
