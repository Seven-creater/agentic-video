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
    state = {"request_count": 2, "calls": [{"id": "one", "status": "received"},
                                             {"id": "two", "status": "received"}],
             "input_lock": {"preserve": True}, "artifacts": {}}
    authorization = {"policy": jobs.RECOVERY_POLICY, "recovery_name": jobs.RECOVERY_NAME,
                     "output": str(output), "original_job_id": job["job_id"],
                     "original_job_sha256": sha(original / "job.json")}
    authorization_path = output / "artifacts/server_output_capacity_recovery_001.json"
    authorization_path.parent.mkdir()
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
