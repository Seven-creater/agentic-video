"""Capacity recovery owns separate controls and preserves settled history."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from omni_story.library import server_jobs as jobs
from omni_story.library.state import json_sha


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_task(tmp_path):
    output = tmp_path / "original"
    original = output / ".omni-server"
    original.mkdir(parents=True)
    job = {"job_id": "original-token", "state": "failed", "exit_code": 1,
           "supervisor_pid": 11111, "child_pid": 22222,
           "command": ["original-worker"], "output": str(output), "automatic_restart": False}
    jobs._write_json(original / "job.json", job)
    (original / "run.lock").write_bytes(b"original-token\n")
    (original / "job.log").write_bytes(b"original failed\n")
    jobs._write_json(output / "failure.json", {"error": "known output truncation"})
    state = {"task_id": "synthetic-task", "request_count": 2, "calls": [{"id": "one", "status": "received"},
                                             {"id": "two", "status": "received"}],
             "input_lock": {"preserve": True, "configuration": {"max_rounds": 2, "max_renders": 2}},
             "artifacts": {}}
    authorization = {"policy": jobs.RECOVERY_POLICY, "recovery_name": jobs.RECOVERY_NAME,
                     "output": str(output), "original_job_id": job["job_id"],
                     "original_job_sha256": sha(original / "job.json"),
                     "task_id": state["task_id"], "input_lock": state["input_lock"],
                     "baseline_request_count": state["request_count"]}
    authorization_path = output / "artifacts/server_output_capacity_recovery_001.json"
    authorization_path.parent.mkdir()
    baseline_path = output / "artifacts/server_output_capacity_baseline_v1.json"
    jobs._write_json(baseline_path, state)
    authorization.update(baseline_state_path=str(baseline_path), baseline_state_sha256=sha(baseline_path))
    jobs._write_json(authorization_path, authorization)
    state["artifacts"]["server_output_capacity_recovery"] = [
        {"path": str(authorization_path), "sha256": json_sha(authorization), "at": "synthetic"}]
    jobs._write_json(output / "library_state.json", state)
    return output, authorization_path


def setup_launch(tmp_path, monkeypatch):
    output, authorization = original_task(tmp_path)
    monkeypatch.setattr(jobs.sys, "platform", "linux")
    monkeypatch.setattr(jobs, "_identity", lambda pid: "new-birth" if pid == 33333 else None)
    monkeypatch.setattr(jobs, "_group_running", lambda group_id: False)
    calls = []

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Supervisor", (), {"pid": 33333})()

    monkeypatch.setattr(jobs.subprocess, "Popen", launch)
    return output, authorization, calls


def launch_recovery(output, authorization, cwd):
    return jobs.start_recovery(["corrected-worker", "argument with spaces"], output, cwd,
                               authorization_path=authorization, authorization_sha256=sha(authorization))


def setup_preflight(tmp_path, monkeypatch):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    first = launch_recovery(output, authorization, tmp_path)
    directory = jobs._directory(output, jobs.RECOVERY_NAME)
    first.update(state="failed", exit_code=1, supervisor_pid=44444, child_pid=55555)
    jobs._write_json(directory / "job.json", first)
    (directory / "run.lock").write_text(first["job_id"] + "\n", encoding="utf-8")
    (directory / "job.log").write_text("Traceback\nLibraryStopped: library_file_set_changed\n", encoding="utf-8")
    state = jobs._read_json(output / "library_state.json")
    proof = {"policy": jobs.PREFLIGHT_POLICY, "task_id": state["task_id"],
             "input_lock": state["input_lock"], "baseline_request_count": state["request_count"],
             "first_recovery_job_path": str(directory / "job.json"),
             "first_recovery_job_sha256": sha(directory / "job.json"),
             "first_recovery_log_path": str(directory / "job.log"),
             "first_recovery_log_sha256": sha(directory / "job.log"),
             "first_recovery_job_id": first["job_id"],
             "original_capacity_authorization_sha256": sha(authorization),
             "recovery_name": jobs.PREFLIGHT_RECOVERY_NAME, "new_model_requests_authorized": 0}
    proof_path = output / "artifacts/server_output_capacity_preflight_fix_001.json"
    jobs._write_json(proof_path, proof)
    state["artifacts"]["server_output_capacity_preflight_fix"] = [
        {"path": str(proof_path), "sha256": json_sha(proof), "at": "synthetic"}]
    jobs._write_json(output / "library_state.json", state)
    return output, authorization, proof_path, calls


def launch_preflight(output, authorization, cwd):
    return jobs.start_recovery(["catalog-corrected-worker"], output, cwd,
        authorization_path=authorization, authorization_sha256=sha(authorization),
        recovery_name=jobs.PREFLIGHT_RECOVERY_NAME)


def rewrite_preflight_proof(output, proof_path, proof):
    jobs._write_json(proof_path, proof)
    state_path = output / "library_state.json"
    state = jobs._read_json(state_path)
    state["artifacts"]["server_output_capacity_preflight_fix"][0]["sha256"] = json_sha(proof)
    jobs._write_json(state_path, state)


def setup_remaining(tmp_path, monkeypatch):
    output, authorization, _, launches = setup_preflight(tmp_path, monkeypatch)
    controller = launch_preflight(output, authorization, tmp_path)
    directory = jobs._directory(output, jobs.PREFLIGHT_RECOVERY_NAME)
    controller.update(state="failed", exit_code=1, supervisor_pid=66666, child_pid=77777)
    jobs._write_json(directory / "job.json", controller)
    (directory / "run.lock").write_text(controller["job_id"] + "\n", encoding="utf-8")
    (directory / "job.log").write_text("ValueError: model_protocol_repair_exhausted:plan_0_capacity_v2\n",
                                       encoding="utf-8")
    state_path = output / "library_state.json"
    state = jobs._read_json(state_path)
    original = None
    for name in ("plan_0_capacity_v2", "plan_0_capacity_v2_repair"):
        call = {"id": f"glm_{len(state['calls']) + 1:03d}_{name}", "name": name, "status": "received",
                "repair_of": original["id"] if original else None}
        folder = output / "calls" / call["id"]
        folder.mkdir(parents=True)
        request = {"model_owned_prompt": name}
        response = {"status": "complete", "model_reply": "synthetic rejected candidate"}
        jobs._write_json(folder / "request.json", request)
        jobs._write_json(folder / "response.json", response)
        jobs._write_json(folder / "protocol_failure.json", {"error": (
            "plan:caption_event_outside_selected_range" if original else "extra trailing JSON")})
        call.update(request_sha256=json_sha(request), response_sha256=json_sha(response))
        state["calls"].append(call)
        original = call
    state["request_count"] = len(state["calls"])
    jobs._write_json(state_path, state)
    failure_path = output / "failure_capacity_recovery_v1.json"
    jobs._write_json(failure_path, {"error": "model_protocol_repair_exhausted:plan_0_capacity_v2"})
    baseline_path = output / "artifacts/server_remaining_candidate_baseline_v1.json"
    jobs._write_json(baseline_path, state)
    proof = {"policy": jobs.REMAINING_POLICY, "task_id": state["task_id"], "input_lock": state["input_lock"],
             "consumed_candidate": 0, "remaining_candidate": 1, "new_candidate_rounds": 0,
             "baseline_request_count": state["request_count"], "baseline_state_path": str(baseline_path),
             "baseline_state_sha256": sha(baseline_path), "failed_controller_job_path": str(directory / "job.json"),
             "failed_controller_job_sha256": sha(directory / "job.json"),
             "failed_controller_job_id": controller["job_id"], "failed_controller_log_path": str(directory / "job.log"),
             "failed_controller_log_sha256": sha(directory / "job.log"), "failure_report_path": str(failure_path),
             "failure_report_sha256": sha(failure_path), "failed_plan_call_id": original["id"],
             "request_sha256": original["request_sha256"], "response_sha256": original["response_sha256"],
             "failed_plan_protocol_failure_sha256": sha(output / "calls" / original["id"] / "protocol_failure.json"),
             "original_capacity_authorization_sha256": sha(authorization), "no_unknown_replay": True}
    proof_path = output / "artifacts/server_remaining_candidate_feedback_001.json"
    jobs._write_json(proof_path, proof)
    state["artifacts"]["server_remaining_candidate_feedback"] = [
        {"path": str(proof_path), "sha256": json_sha(proof), "at": "synthetic"}]
    jobs._write_json(state_path, state)
    return output, authorization, proof_path, launches


def launch_remaining(output, authorization, cwd):
    return jobs.start_recovery(["remaining-initial-candidate-worker"], output, cwd,
        authorization_path=authorization, authorization_sha256=sha(authorization),
        recovery_name=jobs.REMAINING_RECOVERY_NAME)


def rewrite_remaining_proof(output, proof_path, proof):
    jobs._write_json(proof_path, proof)
    state_path = output / "library_state.json"
    state = jobs._read_json(state_path)
    state["artifacts"]["server_remaining_candidate_feedback"][0]["sha256"] = json_sha(proof)
    jobs._write_json(state_path, state)


def preserved_files(output):
    return {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}


def assert_preserved(output, original):
    assert all((output / relative).read_bytes() == data for relative, data in original.items())


def test_recovery_is_detached_once_and_original_controls_are_immutable(tmp_path, monkeypatch):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    before = preserved_files(output)
    job = launch_recovery(output, authorization, tmp_path)
    assert job["job_id"] != "original-token"
    assert job["state"] == "starting" and job["automatic_restart"] is False
    assert job["output"] == str(output)
    assert job["recovery"]["original_state_sha256"] == sha(output / "library_state.json")
    argv, options = calls[0]
    assert argv[-2:] == ["--recovery-name", "output_capacity_v1"]
    assert options["start_new_session"] and options["close_fds"]
    assert options["stdin"] == subprocess.DEVNULL
    assert str(output / ".omni-server/recoveries/output_capacity_v1") in options["stdout"].name
    assert_preserved(output, before)
    with pytest.raises(FileExistsError):
        launch_recovery(output, authorization, tmp_path)
    assert len(calls) == 1
    assert jobs.status(output)["job_id"] == "original-token"


@pytest.mark.parametrize("kind", ["submitted", "uncertain", "failed_known"])
def test_only_all_received_calls_allow_capacity_recovery(tmp_path, monkeypatch, kind):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    state = jobs._read_json(output / "library_state.json")
    state["calls"][-1]["status"] = kind
    jobs._write_json(output / "library_state.json", state)
    before = preserved_files(output)
    with pytest.raises(ValueError, match="all original calls received"):
        launch_recovery(output, authorization, tmp_path)
    assert not calls and not (output / ".omni-server/recoveries").exists()
    assert_preserved(output, before)


@pytest.mark.parametrize("state,exit_code", [("running", None), ("unknown", None),
                                            ("succeeded", 0), ("stopped", -15), ("failed", 7)])
def test_recovery_is_not_a_generic_restart(tmp_path, monkeypatch, state, exit_code):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    path = output / ".omni-server/job.json"
    original = jobs._read_json(path)
    original.update(state=state, exit_code=exit_code)
    jobs._write_json(path, original)
    record = jobs._read_json(authorization)
    record["original_job_sha256"] = sha(path)
    jobs._write_json(authorization, record)
    state_data = jobs._read_json(output / "library_state.json")
    state_data["artifacts"]["server_output_capacity_recovery"][0]["sha256"] = json_sha(record)
    jobs._write_json(output / "library_state.json", state_data)
    with pytest.raises(ValueError, match="known failed job"):
        launch_recovery(output, authorization, tmp_path)
    assert not calls


@pytest.mark.parametrize("alive", ["supervisor", "child_group"])
def test_original_processes_must_be_settled(tmp_path, monkeypatch, alive):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    if alive == "supervisor":
        monkeypatch.setattr(jobs, "_identity", lambda pid: "still-alive")
    else:
        monkeypatch.setattr(jobs, "_group_running", lambda group_id: True)
    with pytest.raises(ValueError, match="still running"):
        launch_recovery(output, authorization, tmp_path)
    assert not calls


def test_authorization_byte_digest_and_registered_json_digest_are_distinct(tmp_path, monkeypatch):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    record = jobs._read_json(authorization)
    with pytest.raises(ValueError, match="bytes changed"):
        jobs.start_recovery(["worker"], output, tmp_path, authorization_path=authorization,
                            authorization_sha256=json_sha(record))
    authorization.write_text(json.dumps(record), encoding="utf-8")
    # Whitespace changes the bytes SHA, while the registered json_sha is unchanged.
    assert sha(authorization) != json_sha(record)
    launch_recovery(output, authorization, tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["unregistered", "json_digest", "original_job", "failure_missing", "count", "run_lock"])
def test_changed_baseline_or_unregistered_authorization_cannot_launch(tmp_path, monkeypatch, change):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    if change in {"unregistered", "json_digest", "count"}:
        state = jobs._read_json(output / "library_state.json")
        if change == "unregistered":
            state["artifacts"] = {}
        elif change == "json_digest":
            state["artifacts"]["server_output_capacity_recovery"][0]["sha256"] = "0" * 64
        else:
            state["request_count"] = 3
        jobs._write_json(output / "library_state.json", state)
    elif change == "original_job":
        with (output / ".omni-server/job.json").open("a") as handle:
            handle.write(" ")
    elif change == "run_lock":
        (output / ".omni-server/run.lock").write_text("a-different-job", encoding="utf-8")
    else:
        (output / "failure.json").unlink()
    before = preserved_files(output)
    with pytest.raises((ValueError, FileNotFoundError)):
        launch_recovery(output, authorization, tmp_path)
    assert not calls
    assert_preserved(output, before)


@pytest.mark.parametrize("name", ["another_round", "../outside", "/tmp/outside", ""])
def test_recovery_name_is_a_fixed_single_control_scope(tmp_path, monkeypatch, name):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="unsupported recovery name"):
        jobs.start_recovery(["worker"], output, tmp_path, authorization_path=authorization,
                            authorization_sha256=sha(authorization), recovery_name=name)
    assert not calls


def test_recovery_control_symlink_cannot_escape_original_output(tmp_path, monkeypatch):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (output / ".omni-server/recoveries").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="inside original output"):
        launch_recovery(output, authorization, tmp_path)
    assert not calls and not any(outside.iterdir())


def test_recovery_stop_and_logs_are_token_bound_to_the_new_control(tmp_path, monkeypatch):
    output, authorization, _ = setup_launch(tmp_path, monkeypatch)
    before = preserved_files(output)
    job = launch_recovery(output, authorization, tmp_path)
    directory = output / ".omni-server/recoveries/output_capacity_v1"
    (directory / "job.log").write_text("new recovery log\n", encoding="utf-8")
    jobs._write_json(directory / "stop.json", {"job_id": "original-token"})
    assert not jobs.status(output, recovery_name=jobs.RECOVERY_NAME)["stop_requested"]
    jobs.stop(output, recovery_name=jobs.RECOVERY_NAME)
    assert jobs._read_json(directory / "stop.json")["job_id"] == job["job_id"]
    assert jobs.logs(output, recovery_name=jobs.RECOVERY_NAME) == "new recovery log\n"
    assert jobs.logs(output) == "original failed\n"
    assert_preserved(output, before)


def test_supervisor_rechecks_immutable_baseline_before_corrected_worker(tmp_path, monkeypatch):
    output, authorization, calls = setup_launch(tmp_path, monkeypatch)
    job = launch_recovery(output, authorization, tmp_path)
    state_path = output / "library_state.json"
    state_path.write_text(state_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="baseline changed"):
        jobs._supervise(output, job["job_id"], jobs.RECOVERY_NAME)
    assert len(calls) == 1  # Only the supervisor was launched, no corrected worker.
    assert jobs.status(output, recovery_name=jobs.RECOVERY_NAME)["state"] == "failed"
    assert not (output / ".omni-server/recoveries/output_capacity_v1/run.lock").exists()
    assert (output / ".omni-server/run.lock").read_bytes() == b"original-token\n"


def test_recovery_supervisor_runs_at_most_once_and_preserves_original_job(tmp_path, monkeypatch):
    output, authorization, _ = setup_launch(tmp_path, monkeypatch)
    before = preserved_files(output)
    job = launch_recovery(output, authorization, tmp_path)
    children = []

    class Child:
        pid = 44444
        returncode = None

        def wait(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: children.append((a, k)) or Child())
    monkeypatch.setattr(jobs, "_leader_exited", lambda child: True)
    monkeypatch.setattr(jobs.os, "killpg", lambda *a: None, raising=False)
    jobs._supervise(output, job["job_id"], jobs.RECOVERY_NAME)
    assert jobs.status(output, recovery_name=jobs.RECOVERY_NAME)["state"] == "succeeded"
    with pytest.raises(ValueError, match="initial state"):
        jobs._supervise(output, job["job_id"], jobs.RECOVERY_NAME)
    assert len(children) == 1
    assert children[0][1]["start_new_session"] is True
    assert_preserved(output, before)


def test_recovery_cli_status_and_stop_use_the_explicit_control(tmp_path, monkeypatch, capsys):
    output, authorization, _ = setup_launch(tmp_path, monkeypatch)
    job = launch_recovery(output, authorization, tmp_path)
    args = ["--output", str(output), "--recovery-name", jobs.RECOVERY_NAME]
    assert jobs.main(["status", *args]) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == job["job_id"]
    assert jobs.main(["stop", *args]) == 0
    assert json.loads(capsys.readouterr().out)["stop_requested"] is True


def test_catalog_preflight_carryover_is_once_and_preserves_both_old_controls(tmp_path, monkeypatch, capsys):
    output, authorization, proof_path, calls = setup_preflight(tmp_path, monkeypatch)
    before = preserved_files(output)
    job = launch_preflight(output, authorization, tmp_path)
    assert len(calls) == 2  # First recovery and the one separate carryover supervisor.
    assert calls[-1][0][-2:] == ["--recovery-name", jobs.PREFLIGHT_RECOVERY_NAME]
    assert job["recovery"]["authorization_path"] == str(authorization)
    assert job["recovery"]["preflight_fix"]["path"] == str(proof_path)
    assert job["recovery"]["preflight_fix"]["sha256"] == sha(proof_path)
    assert_preserved(output, before)
    with pytest.raises(FileExistsError):
        launch_preflight(output, authorization, tmp_path)
    assert len(calls) == 2
    assert jobs.status(output, recovery_name=jobs.RECOVERY_NAME)["state"] == "failed"
    args = ["--output", str(output), "--recovery-name", jobs.PREFLIGHT_RECOVERY_NAME]
    assert jobs.main(["status", *args]) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == job["job_id"]
    assert jobs.main(["stop", *args]) == 0
    assert json.loads(capsys.readouterr().out)["stop_requested"] is True
    assert jobs.main(["logs", *args]) == 0
    assert capsys.readouterr().out == ""
    assert not (jobs._directory(output, jobs.RECOVERY_NAME) / "stop.json").exists()
    assert_preserved(output, before)


@pytest.mark.parametrize("case", ["unregistered", "duplicate_proof", "proof_digest", "task_id", "input_lock",
    "policy", "recovery_name", "baseline_request_count", "original_capacity_authorization_sha256",
    "new_model_requests_authorized", "appended_call", "first_job_bytes", "first_log_bytes",
    "first_job_path", "first_log_path", "first_job_id", "first_lock", "wrong_failure"])
def test_catalog_preflight_requires_registered_unchanged_unused_capacity_proof(tmp_path, monkeypatch, case):
    output, authorization, proof_path, calls = setup_preflight(tmp_path, monkeypatch)
    state_path = output / "library_state.json"
    state = jobs._read_json(state_path)
    proof = jobs._read_json(proof_path)
    first_directory = jobs._directory(output, jobs.RECOVERY_NAME)
    if case in {"unregistered", "duplicate_proof", "proof_digest", "appended_call"}:
        entries = state["artifacts"]["server_output_capacity_preflight_fix"]
        if case == "unregistered":
            state["artifacts"].pop("server_output_capacity_preflight_fix")
        elif case == "duplicate_proof":
            entries.append(dict(entries[0]))
        elif case == "proof_digest":
            entries[0]["sha256"] = "0" * 64
        else:
            state["calls"].append({"id": "unexpected-alias", "status": "received"})
            state["request_count"] += 1
        jobs._write_json(state_path, state)
    elif case in {"first_job_bytes", "first_log_bytes", "first_lock", "wrong_failure"}:
        filename = "job.json" if case == "first_job_bytes" else "run.lock" if case == "first_lock" else "job.log"
        path = first_directory / filename
        if case == "wrong_failure":
            path.write_text("LibraryStopped: some_other_failure\n", encoding="utf-8")
            proof["first_recovery_log_sha256"] = sha(path)
            rewrite_preflight_proof(output, proof_path, proof)
        elif case == "first_lock":
            path.write_text("wrong-first-token\n", encoding="utf-8")
        else:
            path.write_bytes(path.read_bytes() + b" ")
    else:
        field = {"first_job_path": "first_recovery_job_path", "first_log_path": "first_recovery_log_path",
                 "first_job_id": "first_recovery_job_id"}.get(case, case)
        proof[field] = (str(output / ".omni-server/job.json") if case == "first_job_path" else
                        str(output / ".omni-server/job.log") if case == "first_log_path" else
                        {"changed": True} if case == "input_lock" else
                        1 if case in {"baseline_request_count", "new_model_requests_authorized"} else "changed")
        rewrite_preflight_proof(output, proof_path, proof)
    before = preserved_files(output)
    with pytest.raises(ValueError):
        launch_preflight(output, authorization, tmp_path)
    assert len(calls) == 1
    assert not jobs._directory(output, jobs.PREFLIGHT_RECOVERY_NAME).exists()
    assert_preserved(output, before)


@pytest.mark.parametrize("state,exit_code", [("running", None), ("unknown", None),
    ("succeeded", 0), ("stopped", -15), ("failed", 7)])
def test_catalog_preflight_is_not_a_generic_capacity_restart(tmp_path, monkeypatch, state, exit_code):
    output, authorization, proof_path, calls = setup_preflight(tmp_path, monkeypatch)
    job_path = jobs._directory(output, jobs.RECOVERY_NAME) / "job.json"
    first = jobs._read_json(job_path)
    first.update(state=state, exit_code=exit_code)
    jobs._write_json(job_path, first)
    proof = jobs._read_json(proof_path)
    proof["first_recovery_job_sha256"] = sha(job_path)
    rewrite_preflight_proof(output, proof_path, proof)
    with pytest.raises(ValueError, match="known first failed capacity job"):
        launch_preflight(output, authorization, tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("alive", ["supervisor", "child_group"])
def test_catalog_preflight_requires_first_capacity_process_group_to_exit(tmp_path, monkeypatch, alive):
    output, authorization, _, calls = setup_preflight(tmp_path, monkeypatch)
    if alive == "supervisor":
        monkeypatch.setattr(jobs, "_identity", lambda pid: "first-still-alive" if pid == 44444 else None)
    else:
        monkeypatch.setattr(jobs, "_group_running", lambda pid: pid == 55555)
    with pytest.raises(ValueError, match="still running"):
        launch_preflight(output, authorization, tmp_path)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["first_job", "first_log", "proof", "first_lock"])
def test_catalog_preflight_supervisor_rechecks_proof_before_worker(tmp_path, monkeypatch, change):
    output, authorization, proof_path, calls = setup_preflight(tmp_path, monkeypatch)
    job = launch_preflight(output, authorization, tmp_path)
    first_directory = jobs._directory(output, jobs.RECOVERY_NAME)
    path = {"first_job": first_directory / "job.json", "first_log": first_directory / "job.log",
            "first_lock": first_directory / "run.lock", "proof": proof_path}[change]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        jobs._supervise(output, job["job_id"], jobs.PREFLIGHT_RECOVERY_NAME)
    assert len(calls) == 2  # No corrected worker after either supervisor launch.
    assert jobs.status(output, recovery_name=jobs.PREFLIGHT_RECOVERY_NAME)["state"] == "failed"
    assert not (jobs._directory(output, jobs.PREFLIGHT_RECOVERY_NAME) / "run.lock").exists()


def test_remaining_initial_candidate_has_one_control_and_preserves_all_prior_records(tmp_path, monkeypatch, capsys):
    output, authorization, proof_path, launches = setup_remaining(tmp_path, monkeypatch)
    before = preserved_files(output)
    job = launch_remaining(output, authorization, tmp_path)
    assert len(launches) == 3
    assert launches[-1][0][-2:] == ["--recovery-name", jobs.REMAINING_RECOVERY_NAME]
    assert job["recovery"]["authorization_path"] == str(authorization)
    assert job["recovery"]["remaining_candidate"]["path"] == str(proof_path)
    assert job["recovery"]["remaining_candidate"]["sha256"] == sha(proof_path)
    assert_preserved(output, before)
    with pytest.raises(FileExistsError):
        launch_remaining(output, authorization, tmp_path)
    assert len(launches) == 3
    for name in (None, jobs.RECOVERY_NAME, jobs.PREFLIGHT_RECOVERY_NAME):
        assert jobs.status(output, recovery_name=name)["state"] == "failed"
    args = ["--output", str(output), "--recovery-name", jobs.REMAINING_RECOVERY_NAME]
    assert jobs.main(["status", *args]) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == job["job_id"]
    assert jobs.main(["stop", *args]) == 0
    assert json.loads(capsys.readouterr().out)["stop_requested"] is True
    assert_preserved(output, before)


@pytest.mark.parametrize("case", ["unregistered", "duplicate_proof", "proof_digest", "policy", "task_id", "input_lock",
    "baseline_request_count", "consumed_candidate", "remaining_candidate", "new_candidate_rounds", "no_unknown_replay",
    "original_capacity_authorization_sha256", "appended_call", "old_call_prefix", "baseline_bytes", "initial_baseline_bytes",
    "request_bytes", "response_bytes", "protocol_bytes", "protocol_reason", "failure_bytes", "failure_reason",
    "controller_job_bytes", "controller_log_bytes", "controller_lock", "controller_job_path", "controller_log_path",
    "failure_report_path", "failed_plan_call_id", "request_sha256", "response_sha256", "failed_plan_protocol_failure_sha256",
    "later_candidate", "render", "result", "alias_names", "repair_parent"])
def test_remaining_candidate_requires_exact_received_prefix_and_known_semantic_failure(tmp_path, monkeypatch, case):
    output, authorization, proof_path, launches = setup_remaining(tmp_path, monkeypatch)
    state_path = output / "library_state.json"
    state = jobs._read_json(state_path)
    proof = jobs._read_json(proof_path)
    controller_directory = jobs._directory(output, jobs.PREFLIGHT_RECOVERY_NAME)
    call_directory = output / "calls" / proof["failed_plan_call_id"]
    if case in {"unregistered", "duplicate_proof", "proof_digest", "appended_call", "old_call_prefix",
                "later_candidate", "alias_names", "repair_parent"}:
        entries = state["artifacts"]["server_remaining_candidate_feedback"]
        if case == "unregistered":
            state["artifacts"].pop("server_remaining_candidate_feedback")
        elif case == "duplicate_proof":
            entries.append(dict(entries[0]))
        elif case == "proof_digest":
            entries[0]["sha256"] = "0" * 64
        elif case == "appended_call":
            state["calls"].append({"id": "unauthorized-followup", "name": "search_1", "status": "received"})
            state["request_count"] += 1
        elif case == "old_call_prefix":
            state["calls"][0]["changed"] = True
        elif case == "later_candidate":
            state["calls"][0]["name"] = "plan_1"
        elif case == "alias_names":
            state["calls"][-1]["name"] = "plan_0_capacity_v2_extra_repair"
        else:
            state["calls"][-1]["repair_of"] = "another-parent"
        jobs._write_json(state_path, state)
        if case in {"old_call_prefix", "later_candidate", "alias_names", "repair_parent"}:
            baseline_path = Path(proof["baseline_state_path"])
            jobs._write_json(baseline_path, state)
            proof["baseline_state_sha256"] = sha(baseline_path)
            rewrite_remaining_proof(output, proof_path, proof)
    elif case in {"baseline_bytes", "initial_baseline_bytes", "request_bytes", "response_bytes", "protocol_bytes",
                  "protocol_reason", "failure_bytes", "failure_reason", "controller_job_bytes", "controller_log_bytes",
                  "controller_lock"}:
        auth = jobs._read_json(authorization)
        path = {"baseline_bytes": Path(proof["baseline_state_path"]),
                "initial_baseline_bytes": Path(auth["baseline_state_path"]),
                "request_bytes": call_directory / "request.json", "response_bytes": call_directory / "response.json",
                "protocol_bytes": call_directory / "protocol_failure.json", "protocol_reason": call_directory / "protocol_failure.json",
                "failure_bytes": Path(proof["failure_report_path"]), "failure_reason": Path(proof["failure_report_path"]),
                "controller_job_bytes": controller_directory / "job.json", "controller_log_bytes": controller_directory / "job.log",
                "controller_lock": controller_directory / "run.lock"}[case]
        if case in {"protocol_reason", "failure_reason"}:
            jobs._write_json(path, {"error": "some_other_failure"})
            proof["failed_plan_protocol_failure_sha256" if case == "protocol_reason" else "failure_report_sha256"] = sha(path)
            rewrite_remaining_proof(output, proof_path, proof)
        elif case == "controller_lock":
            path.write_text("wrong-controller-token\n", encoding="utf-8")
        elif case in {"request_bytes", "response_bytes"}:
            jobs._write_json(path, {"changed": True})
        else:
            path.write_bytes(path.read_bytes() + b" ")
    elif case in {"render", "result"}:
        (output / ("render_0" if case == "render" else "result.json")).mkdir()
    else:
        field = {"controller_job_path": "failed_controller_job_path",
                 "controller_log_path": "failed_controller_log_path"}.get(case, case)
        proof[field] = (str(output / ".omni-server/job.json") if case == "controller_job_path" else
                        str(output / ".omni-server/job.log") if case == "controller_log_path" else
                        str(output / "failure.json") if case == "failure_report_path" else
                        {"changed": True} if case == "input_lock" else
                        False if case == "no_unknown_replay" else
                        1 if case in {"baseline_request_count", "consumed_candidate", "new_candidate_rounds"} else
                        0 if case == "remaining_candidate" else "changed")
        rewrite_remaining_proof(output, proof_path, proof)
    before = preserved_files(output)
    with pytest.raises(ValueError):
        launch_remaining(output, authorization, tmp_path)
    assert len(launches) == 2
    assert not jobs._directory(output, jobs.REMAINING_RECOVERY_NAME).exists()
    assert_preserved(output, before)


@pytest.mark.parametrize("state,exit_code", [("running", None), ("unknown", None),
    ("succeeded", 0), ("stopped", -15), ("failed", 7)])
def test_remaining_candidate_is_not_a_generic_controller_restart(tmp_path, monkeypatch, state, exit_code):
    output, authorization, proof_path, launches = setup_remaining(tmp_path, monkeypatch)
    job_path = jobs._directory(output, jobs.PREFLIGHT_RECOVERY_NAME) / "job.json"
    controller = jobs._read_json(job_path)
    controller.update(state=state, exit_code=exit_code)
    jobs._write_json(job_path, controller)
    proof = jobs._read_json(proof_path)
    proof["failed_controller_job_sha256"] = sha(job_path)
    rewrite_remaining_proof(output, proof_path, proof)
    with pytest.raises(ValueError, match="known failed caption-rejection controller"):
        launch_remaining(output, authorization, tmp_path)
    assert len(launches) == 2


@pytest.mark.parametrize("alive", ["supervisor", "child_group"])
def test_remaining_candidate_requires_rejected_controller_process_group_to_exit(tmp_path, monkeypatch, alive):
    output, authorization, _, launches = setup_remaining(tmp_path, monkeypatch)
    if alive == "supervisor":
        monkeypatch.setattr(jobs, "_identity", lambda pid: "still-alive" if pid == 66666 else None)
    else:
        monkeypatch.setattr(jobs, "_group_running", lambda pid: pid == 77777)
    with pytest.raises(ValueError, match="still running"):
        launch_remaining(output, authorization, tmp_path)
    assert len(launches) == 2


@pytest.mark.parametrize("change", ["controller_job", "controller_log", "controller_lock", "proof", "baseline"])
def test_remaining_candidate_supervisor_rechecks_proof_before_worker(tmp_path, monkeypatch, change):
    output, authorization, proof_path, launches = setup_remaining(tmp_path, monkeypatch)
    job = launch_remaining(output, authorization, tmp_path)
    controller_directory = jobs._directory(output, jobs.PREFLIGHT_RECOVERY_NAME)
    proof = jobs._read_json(proof_path)
    path = {"controller_job": controller_directory / "job.json", "controller_log": controller_directory / "job.log",
            "controller_lock": controller_directory / "run.lock", "proof": proof_path,
            "baseline": Path(proof["baseline_state_path"])}[change]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        jobs._supervise(output, job["job_id"], jobs.REMAINING_RECOVERY_NAME)
    assert len(launches) == 3
    assert jobs.status(output, recovery_name=jobs.REMAINING_RECOVERY_NAME)["state"] == "failed"
    assert not (jobs._directory(output, jobs.REMAINING_RECOVERY_NAME) / "run.lock").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux detached recovery integration")
def test_synthetic_recovery_outlives_submitter_and_stops_owned_group(tmp_path):
    output, authorization = original_task(tmp_path)
    # Synthetic original PIDs must not accidentally identify real processes.
    original_path = output / ".omni-server/job.json"
    original = jobs._read_json(original_path)
    original.pop("supervisor_pid")
    original.pop("child_pid")
    jobs._write_json(original_path, original)
    record = jobs._read_json(authorization)
    record["original_job_sha256"] = sha(original_path)
    jobs._write_json(authorization, record)
    state = jobs._read_json(output / "library_state.json")
    state["artifacts"]["server_output_capacity_recovery"][0]["sha256"] = json_sha(record)
    jobs._write_json(output / "library_state.json", state)
    before = preserved_files(output)
    descendant = ("import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  "print('descendant-ready',os.getpid(),flush=True); time.sleep(30)")
    worker = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-u','-c',{descendant!r}]); time.sleep(30)")
    command = [sys.executable, "-u", "-c", worker]
    submitter = ("from pathlib import Path; from omni_story.library.server_jobs import start_recovery; "
                 f"start_recovery({command!r},Path({str(output)!r}),Path({str(tmp_path)!r}),"
                 f"authorization_path=Path({str(authorization)!r}),authorization_sha256={sha(authorization)!r})")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(Path(jobs.__file__).parents[2]), environment.get("PYTHONPATH", "")])
    caller = subprocess.run([sys.executable, "-c", submitter], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=10)
    assert caller.returncode == 0, caller.stderr
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = jobs.status(output, recovery_name=jobs.RECOVERY_NAME)
            log = jobs.logs(output, recovery_name=jobs.RECOVERY_NAME)
            if job["state"] == "running" and "descendant-ready" in log:
                break
            time.sleep(.05)
        else:
            pytest.fail(f"synthetic recovery did not start: {job}")
        descendant_pid = int(next(line for line in log.splitlines() if line.startswith("descendant-ready ")).split()[1])
        assert job["supervisor_alive"]
        assert os.getsid(job["supervisor_pid"]) == job["supervisor_pid"]
        assert os.getpgid(descendant_pid) == job["child_pid"]
        jobs.stop(output, recovery_name=jobs.RECOVERY_NAME)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            final = jobs.status(output, recovery_name=jobs.RECOVERY_NAME)
            if final["state"] in jobs.TERMINAL_STATES:
                break
            time.sleep(.05)
        assert final["state"] == "stopped"
        assert jobs._identity(job["child_pid"]) is None
        assert jobs._identity(descendant_pid) is None
        assert_preserved(output, before)
    finally:
        jobs.stop(output, recovery_name=jobs.RECOVERY_NAME)
