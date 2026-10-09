"""Same-task interval continuation checks; no SSH, credentials or live models."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import opencode_provider, server_interval_resume as resume, server_jobs
from omni_story.library.media import sha256_file
from omni_story.library.pipeline import CodexMCP
from omni_story.library.prompts import POLICY_VERSION
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from test_semantic_interval_diagnostics import source_data
from test_library_pipeline import _fake_bridge, _reply


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(output):
    return {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def assert_preserved(output, before, *, exclude=()):
    assert all((output / path).read_bytes() == raw for path, raw in before.items()
               if str(path) not in exclude)


def response(value):
    return {"status": "complete", "finish_reason": "stop", "result": {"content": [
        {"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}}


def http_pair(call, reply, status=200):
    text = reply["result"]["content"][0]["text"]
    return [{"type": "request", "seq": int(call["id"].split("_")[1]),
             "job_id": call["id"], "hash": json_sha({"call": call["id"]})},
            {"type": "response", "seq": int(call["id"].split("_")[1]),
             "job_id": call["id"], "status": status,
             "body": json.dumps({"choices": [{"finish_reason": "stop", "message": {
                 "content": text}}], "usage": {"completion_tokens": 50}})}]


@pytest.fixture
def stopped_task(tmp_path, monkeypatch):
    output = tmp_path / "same_task"
    config = {**opencode_provider.PROVIDER, "max_rounds": 2, "max_renders": 2,
              "max_fine": 16, "asr": False}
    lock = {"configuration": config, "reference_sha256": "c" * 64}
    registry = tmp_path / "registry.json"
    state = LibraryState(output, lock, max_requests=None, registry_path=registry)
    proxy_path = output / "media_cache" / "bound_slice" / "window.mp4"
    proxy_path.parent.mkdir(parents=True)
    proxy_path.write_bytes(b"synthetic exact-slice bytes, never submitted to a model")
    segment, proxy, observed = source_data()
    proxy.update(path=str(proxy_path), sha256=sha256_file(proxy_path), kind="continuous_window")
    observed["proxy_sha256"] = proxy["sha256"]
    write_json(proxy_path.parent / "lineage.json", proxy)
    scope = {k: proxy[k] for k in ("kind", "source_sha256", "source_start_s", "source_end_s")}
    request = {"tool": "analyze_video", "arguments": {"video_source": str(proxy_path),
        "prompt": "Independent actual-picture observation. No creative target or teacher plot."},
        "provider": opencode_provider.PROVIDER["provider"], "policy_version": POLICY_VERSION,
        "media_sha256": proxy["sha256"], "observation_scope": scope}
    for i in range(27):
        old = deepcopy(request)
        old["arguments"]["prompt"] = f"Previously completed synthetic stage {i}."
        call, _ = state.begin_call(f"prior_{i}", old)
        state.complete_call(call, response({"observed": i}))
        write_json(output / "calls" / call["id"] / "parsed.json", {"observed": i})
    state.set_artifact("old_policy", {"interpretations_are_not_truth": True})
    stage = "semantic_slice_0_" + "1" * 16
    pairs = []
    original = None
    for attempt in range(2):
        actual = deepcopy(request)
        value = deepcopy(observed)
        if attempt:
            actual["arguments"]["prompt"] += resume.MARKER + "Old generic error feedback."
            for evidence in value["evidence"]:
                evidence["local_end_s"] = evidence["local_start_s"]
        call, _ = state.begin_call(stage + ("_repair" if attempt else ""), actual,
                                   repair_of=original)
        reply = response(value)
        state.complete_call(call, reply)
        write_json(output / "calls" / call["id"] / "protocol_failure.json", {
            "error": "semantic/evidence:outside_observed_slice", "attempt": attempt,
            "model_text": reply["result"]["content"][0]["text"]})
        pairs.extend(http_pair(call, reply))
        original = original or call
    (output / "mcp_http.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in pairs), encoding="utf-8")
    write_json(output / "failure.json", {"error": "model_protocol_repair_exhausted:" + stage,
                                         "no_automatic_paid_replay": True})
    write_json(output / "plan_0.json", {"segments": [segment]})
    write_json(output / "draft_plan_0.json", {"segments": [segment], "historical": "draft"})
    write_json(output / "finecut_0.json", {"plan": {"segments": [segment]}})
    write_json(output / ".omni-server/job.json", {"job_id": "old-job-token", "state": "failed",
        "exit_code": 1, "supervisor_pid": 10001, "child_pid": 10002})
    (output / ".omni-server/run.lock").write_text("old-job-token\n", encoding="utf-8")
    (output / ".omni-server/job.log").write_text("Original known failure.\n", encoding="utf-8")
    monkeypatch.setattr(server_jobs, "_identity", lambda pid: None)
    monkeypatch.setattr(server_jobs, "_group_running", lambda pid: False)
    state._reload()
    return SimpleNamespace(state=state, output=output, registry=registry, request=request,
                           proxy=proxy, segment=segment, stage=stage)


def test_registration_appends_bound_authorization_without_rewriting_or_resetting_history(stopped_task):
    case = stopped_task
    before = snapshot(case.output)
    old = deepcopy(case.state.data)
    proof = resume.register(case.output, case.registry)
    assert Path(proof["baseline_state_path"]).read_bytes() == before[Path("library_state.json")]
    assert proof["baseline_request_count"] == 29 and proof["new_rounds"] == 0
    assert (proof["max_rounds"], proof["max_renders"], proof["max_fine"]) == (2, 2, 16)
    case.state._reload()
    assert case.state.data["calls"] == old["calls"]
    assert case.state.data["request_count"] == 29 and case.state.data["max_requests"] is None
    assert case.state.data["input_lock"] == old["input_lock"]
    assert len(case.state.data["artifacts"][resume.KEY]) == 1
    assert_preserved(case.output, before, exclude=("library_state.json",))
    registered = snapshot(case.output)
    assert resume.register(case.output, case.registry) == proof == resume.load(case.output)
    assert snapshot(case.output) == registered


@pytest.mark.parametrize("mutation", ["prefix", "input_lock", "old_artifact", "baseline",
    "authorization", "request", "response", "old_failure", "controller", "http_prefix"])
def test_changed_registered_history_is_rejected_before_any_new_work(stopped_task, mutation):
    case = stopped_task
    proof = resume.register(case.output, case.registry)
    data = read(case.state.path)
    if mutation in {"prefix", "input_lock", "old_artifact"}:
        if mutation == "prefix":
            data["calls"][0]["usage"] = {"completion_tokens": 999}
        elif mutation == "input_lock":
            data["input_lock"]["configuration"]["max_fine"] += 1
        else:
            data["artifacts"]["old_policy"] = []
        write_json(case.state.path, data)
    else:
        targets = {"baseline": Path(proof["baseline_state_path"]),
            "authorization": Path(data["artifacts"][resume.KEY][0]["path"]),
            "request": case.output / "calls" / data["calls"][0]["id"] / "request.json",
            "response": case.output / "calls" / data["calls"][0]["id"] / "response.json",
            "old_failure": case.output / "failure.json",
            "controller": case.output / ".omni-server/job.json",
            "http_prefix": case.output / "mcp_http.jsonl"}
        target = targets[mutation]
        if mutation == "authorization":
            changed = read(target)
            changed["user_instruction"] = "Different unapproved scope."
            write_json(target, changed)
        else:
            target.write_bytes(b" " + target.read_bytes())
    before = snapshot(case.output)
    with pytest.raises(LibraryStopped):
        resume.load(case.output)
    assert snapshot(case.output) == before


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_unknown_or_unsettled_prefix_cannot_register_continuation(stopped_task, status):
    case = stopped_task
    data = read(case.state.path)
    data["calls"][0]["status"] = status
    write_json(case.state.path, data)
    before = snapshot(case.output)
    with pytest.raises(LibraryStopped, match="original_scope_changed"):
        resume.register(case.output, case.registry)
    assert snapshot(case.output) == before


def replace_last_response(case, value):
    data = read(case.state.path)
    call = data["calls"][-1]
    reply = response(value)
    call["response_sha256"] = json_sha(reply)
    write_json(case.output / "calls" / call["id"] / "response.json", reply)
    write_json(case.output / "calls" / call["id"] / "protocol_failure.json", {
        "error": "semantic/evidence:outside_observed_slice", "attempt": 1,
        "model_text": reply["result"]["content"][0]["text"]})
    write_json(case.state.path, data)
    journal = case.output / "mcp_http.jsonl"
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    rows[-2:] = http_pair(call, reply)
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_received_actual_outside_range_failure_cannot_claim_zero_duration_remedy(stopped_task):
    case = stopped_task
    call = case.state.data["calls"][-1]
    value = json.loads(read(case.output / "calls" / call["id"] / "response.json")["result"]["content"][0]["text"])
    value["evidence"][0]["local_end_s"] = 5.5
    replace_last_response(case, value)
    before = snapshot(case.output)
    with pytest.raises(LibraryStopped, match="not_known_zero_duration_failure"):
        resume.register(case.output, case.registry)
    assert snapshot(case.output) == before


def test_modified_source_slice_cannot_register_a_correction(stopped_task):
    case = stopped_task
    Path(case.proxy["path"]).write_bytes(b"different media is not original evidence")
    before = snapshot(case.output)
    with pytest.raises(LibraryStopped, match="failed_proxy_changed"):
        resume.register(case.output, case.registry)
    assert snapshot(case.output) == before


def test_wrong_total_call_count_cannot_be_hidden_behind_an_unchanged_prefix(stopped_task):
    case = stopped_task
    resume.register(case.output, case.registry)
    data = read(case.state.path)
    data["request_count"] += 1
    write_json(case.state.path, data)
    before = snapshot(case.output)
    with pytest.raises(LibraryStopped):
        resume.load(case.output)
    assert snapshot(case.output) == before


@pytest.mark.parametrize("case_name", ["live_supervisor", "live_group", "http500", "result", "render"])
def test_registration_requires_stopped_known_reply_and_unused_render_scope(stopped_task, monkeypatch, case_name):
    case = stopped_task
    if case_name == "live_supervisor":
        monkeypatch.setattr(server_jobs, "_identity", lambda pid: "live-birth-token")
    elif case_name == "live_group":
        monkeypatch.setattr(server_jobs, "_group_running", lambda pid: True)
    elif case_name == "http500":
        journal = case.output / "mcp_http.jsonl"
        rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        rows[-1]["status"] = 500
        journal.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    elif case_name == "result":
        write_json(case.output / "result.json", {"status": "old completed candidate"})
    else:
        (case.output / "render_0").mkdir()
    before = snapshot(case.output)
    with pytest.raises(LibraryStopped):
        resume.register(case.output, case.registry)
    assert snapshot(case.output) == before


def test_corrected_request_carries_all_actual_diagnostics_and_no_creative_answer(stopped_task):
    case = stopped_task
    proof = resume.register(case.output, case.registry)
    expected = proof["expected_request"]
    assert {k: v for k, v in expected.items() if k != "arguments"} == {
        k: v for k, v in case.request.items() if k != "arguments"}
    assert expected["arguments"]["video_source"] == case.proxy["path"]
    prompt = expected["arguments"]["prompt"]
    assert prompt.startswith(case.request["arguments"]["prompt"])
    feedback = json.loads(prompt.split(resume.MARKER, 1)[1])
    invalid = feedback["validation_diagnostics"]["invalid_intervals"]
    assert [row["evidence_id"] for row in invalid] == ["e1", "e2", "e3", "e4", "e5"]
    assert all(row["problem"] == "zero_duration" for row in invalid)
    assert feedback["validation_diagnostics"]["observed_duration_s"] == 5
    assert json.loads(feedback["previous_response"])["evidence"][0]["local_end_s"] == 0
    assert "plan" not in feedback and "theme" not in feedback
    assert "不能给点时间随意补时长" in prompt


def test_only_the_exhausted_observation_is_aliased_and_remaining_stages_keep_native_calls(stopped_task, monkeypatch):
    case = stopped_task
    proof = resume.register(case.output, case.registry)
    forwarded = []
    def native(self, name, prompt, media, validator, **options):
        forwarded.append((name, prompt, media, validator, options))
        return {"synthetic": "transport stub"}
    monkeypatch.setattr(opencode_provider.OpenCodeMCP, "call", native)
    client = object.__new__(resume.IntervalResumeMCP)
    client.resume_proof = proof
    validator = lambda value: value
    scope = deepcopy(case.request["observation_scope"])
    assert client.call(case.stage, "legacy prompt", case.proxy["path"], validator, scope=scope)
    assert forwarded[0] == (resume.ALIAS, proof["expected_request"]["arguments"]["prompt"],
                            case.proxy["path"], validator, {"scope": scope})
    for stage in ["reference", "plan_0", "semantic_claims_0_" + "1" * 16, "blind_0", "plan_1"]:
        client.call(stage, "Original native prompt.", case.proxy["path"], validator, scope=scope)
        assert forwarded[-1] == (stage, "Original native prompt.", case.proxy["path"],
                                 validator, {"scope": scope})
    assert case.state.usage()["requests"] == 29


@pytest.mark.parametrize("change", ["media", "scope", "missing_scope"])
def test_alias_rejects_unbound_media_or_range_before_transport(stopped_task, monkeypatch, change):
    case = stopped_task
    proof = resume.register(case.output, case.registry)
    def forbidden(*args, **kwargs):
        pytest.fail("an invalid correction must not reach model transport")
    monkeypatch.setattr(opencode_provider.OpenCodeMCP, "call", forbidden)
    client = object.__new__(resume.IntervalResumeMCP)
    client.resume_proof = proof
    media = case.proxy["path"]
    options = {"scope": deepcopy(case.request["observation_scope"])}
    if change == "media":
        media = case.output / "wrong_slice.mp4"
        media.write_bytes(b"Different evidence.")
    elif change == "scope":
        options["scope"]["source_end_s"] += 1
    else:
        options = {}
    with pytest.raises(LibraryStopped, match="correction_input_changed"):
        client.call(case.stage, "old prompt", media, lambda value: value, **options)
    assert case.state.usage()["requests"] == 29


def test_new_alias_has_one_native_repair_and_cached_restart_reuses_31_call_ledger(stopped_task, monkeypatch):
    case = stopped_task
    proof = resume.register(case.output, case.registry)
    old_calls = deepcopy(case.state.data["calls"])
    protected = {path: path.read_bytes() for path in (case.output / "calls").rglob("*") if path.is_file()}
    # Replace only the live transport with the existing local queue simulator.
    # The real call builder, validator, repair linkage and cache path still run.
    monkeypatch.setattr(opencode_provider.OpenCodeMCP, "_submit", CodexMCP._submit)
    client = object.__new__(resume.IntervalResumeMCP)
    write_json(case.output / "mcp_ready.json", {"test_fake": True})
    CodexMCP.__init__(client, case.state, timeout_s=2)
    client.resume_proof = proof
    _, _, invalid = source_data(((0, 0),))
    _, _, valid = source_data(((0, 1),))
    for value in (invalid, valid):
        value["proxy_sha256"] = case.proxy["sha256"]
    validator = lambda value: resume.semantic_audit.validate_segment_observation(
        value, case.segment, case.proxy["source_sha256"], case.proxy)
    scope = deepcopy(case.request["observation_scope"])
    with _fake_bridge(case.output, [_reply(json.dumps(invalid)), _reply(json.dumps(valid))]) as jobs:
        actual = client.call(case.stage, "unused legacy text", case.proxy["path"], validator, scope=scope)
        assert actual == valid
        assert len(jobs) == 2
        assert jobs[0]["arguments"]["prompt"] == proof["expected_request"]["arguments"]["prompt"]
        assert jobs[1]["arguments"]["prompt"].startswith(jobs[0]["arguments"]["prompt"] + resume.MARKER)
    case.state._reload()
    assert case.state.data["calls"][:29] == old_calls
    assert [call["name"] for call in case.state.data["calls"][29:]] == [resume.ALIAS, resume.ALIAS + "_repair"]
    assert case.state.data["calls"][-1]["repair_of"] == case.state.data["calls"][-2]["id"]
    assert case.state.usage()["requests"] == 31
    queue_before = snapshot(client.queue)
    assert client.call(case.stage, "legacy text after reconnect", case.proxy["path"], validator, scope=scope) == valid
    assert snapshot(client.queue) == queue_before
    assert case.state.usage()["requests"] == 31
    assert all(path.read_bytes() == raw for path, raw in protected.items())
    assert resume.load(case.output)["baseline_request_count"] == 29


def test_cli_launches_fixed_module_in_own_controller_but_reuses_original_output(stopped_task, monkeypatch, capsys):
    case = stopped_task
    launches = []
    home = case.output.parent / "private_home"
    monkeypatch.setattr(resume, "settings", lambda value: {"project_root": str(case.output.parent)})
    monkeypatch.setattr(resume, "load_history", lambda config: None)
    monkeypatch.setattr(resume, "credential", lambda value: "synthetic-not-a-real-key")
    registrations = []
    def registered(output, registry):
        registrations.append((output, registry))
        return {"synthetic": "authorization"}
    monkeypatch.setattr(resume, "register", registered)
    def start(command, control_output, cwd):
        launches.append((command, control_output, cwd))
        return {"state": "synthetic_launched_without_a_process"}
    monkeypatch.setattr(server_jobs, "start", start)
    resume.main(["start", "--home", str(home), "--output", str(case.output)])
    command, control_output, cwd = launches[0]
    assert command[1:4] == ["-m", "omni_story.library.server_interval_resume", "_run"]
    assert "__main__" not in command
    assert command[command.index("--output") + 1] == str(case.output)
    assert control_output == case.output / "controllers" / "interval_resume_v1"
    assert cwd == case.output.parent
    assert registrations == [(case.output, home / "shared/server_library_runs.json")]
    assert json.loads(capsys.readouterr().out)["state"] == "synthetic_launched_without_a_process"
