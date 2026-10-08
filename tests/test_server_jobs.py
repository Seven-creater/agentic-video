"""Detached-job safety checks use synthetic children and temporary directories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from omni_story.library import server_jobs as jobs


def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.sys, "platform", "linux")
    monkeypatch.setattr(jobs, "_identity", lambda pid: "synthetic-process-birth")
    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Supervisor", (), {"pid": 12345})()

    monkeypatch.setattr(jobs.subprocess, "Popen", spawn)
    output = tmp_path / "task"
    job = jobs.start(["synthetic-worker", "argument with spaces"], output, tmp_path)
    return output, job, calls


def test_start_detaches_and_preserves_explicit_argv(tmp_path, monkeypatch):
    output, job, calls = prepared(tmp_path, monkeypatch)
    argv, options = calls[0]
    assert argv[:3] == [sys.executable, "-m", jobs.MODULE]
    assert options["start_new_session"] is True and options["close_fds"] is True
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] is options["stderr"] and options["stdout"].name.endswith("job.log")
    assert job["command"] == ["synthetic-worker", "argument with spaces"]
    assert job["state"] == "starting" and job["automatic_restart"] is False
    assert jobs._read_json(output / ".omni-server/launch.json")["pid"] == 12345
    with pytest.raises(ValueError, match="never restarted"):
        jobs.start(["different-command"], output, tmp_path)
    assert len(calls) == 1


def test_nonempty_output_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.sys, "platform", "linux")
    history = tmp_path / "original.json"
    history.write_bytes(b'{"uncertain":"preserve"}')
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="empty directory"):
        jobs.start(["worker"], tmp_path, tmp_path)
    assert history.read_bytes() == b'{"uncertain":"preserve"}'
    assert not (tmp_path / ".omni-server").exists()


@pytest.mark.parametrize("command", [[], "shell command", [""], ["worker", "\x00"], [1]])
def test_invalid_command_is_rejected_before_writing(tmp_path, command):
    with pytest.raises(ValueError, match="argv"):
        jobs.start(command, tmp_path / "task", tmp_path)
    assert not (tmp_path / "task").exists()


def test_linux_requirement_is_checked_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="Linux"):
        jobs.start(["worker"], tmp_path / "task", tmp_path)
    assert not (tmp_path / "task").exists()


def test_atomic_json_failure_preserves_record(tmp_path, monkeypatch):
    target = tmp_path / "job.json"
    target.write_bytes(b'{"state":"running"}')

    def fail_replace(source, destination):
        assert jobs._read_json(source) == {"state": "stopped"}
        raise OSError("synthetic filesystem failure")

    monkeypatch.setattr(jobs.os, "replace", fail_replace)
    with pytest.raises(OSError, match="filesystem failure"):
        jobs._write_json(target, {"state": "stopped"})
    assert target.read_bytes() == b'{"state":"running"}'
    assert not list(tmp_path.glob(".*.tmp"))


def test_launch_failure_is_durable_and_cannot_be_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.sys, "platform", "linux")

    def fail_spawn(*args, **kwargs):
        raise OSError("synthetic process launch failure")

    monkeypatch.setattr(jobs.subprocess, "Popen", fail_spawn)
    output = tmp_path / "task"
    with pytest.raises(OSError, match="process launch failure"):
        jobs.start(["worker"], output, tmp_path)
    assert jobs.status(output)["state"] == "failed"
    with pytest.raises(ValueError, match="never restarted"):
        jobs.start(["worker"], output, tmp_path)


def test_registration_failure_does_not_claim_spawned_supervisor_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: calls.append(a) or type("Supervisor", (), {"pid": 12345})())
    original_write = jobs._write_json

    def write(path, value):
        if path.name == "launch.json":
            raise OSError("synthetic registration failure")
        original_write(path, value)

    monkeypatch.setattr(jobs, "_write_json", write)
    output = tmp_path / "task"
    with pytest.raises(OSError, match="registration failure"):
        jobs.start(["worker"], output, tmp_path)
    assert len(calls) == 1
    assert jobs._read_json(output / ".omni-server/job.json")["state"] == "starting"
    with pytest.raises(ValueError, match="never restarted"):
        jobs.start(["worker"], output, tmp_path)


def test_missing_supervisor_is_unknown_and_stop_never_signals_pid(tmp_path, monkeypatch):
    output, job, calls = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "_identity", lambda pid: "different-process-birth")
    monkeypatch.setattr(jobs.os, "kill", lambda *args: pytest.fail("must not signal a saved PID"))
    observed = jobs.stop(output)
    assert observed["state"] == "unknown" and observed["recorded_state"] == "starting"
    assert observed["stop_requested"] is True
    assert jobs._read_json(output / ".omni-server/job.json")["state"] == "starting"
    assert jobs._read_json(output / ".omni-server/stop.json")["job_id"] == job["job_id"]
    assert len(calls) == 1


@pytest.mark.parametrize("exit_code, expected", [(0, "succeeded"), (7, "failed")])
def test_supervisor_runs_once_and_records_actual_child_result(tmp_path, monkeypatch, exit_code, expected):
    output, job, _ = prepared(tmp_path, monkeypatch)
    starts = []

    class Child:
        pid = 54321
        returncode = None

        def wait(self, timeout=None):
            return exit_code

        def poll(self):
            return exit_code

    def spawn(argv, **options):
        starts.append((argv, options))
        return Child()

    monkeypatch.setattr(jobs.subprocess, "Popen", spawn)
    monkeypatch.setattr(jobs, "_leader_exited", lambda child: True)
    monkeypatch.setattr(jobs, "_group_running", lambda group_id: False)
    monkeypatch.setattr(jobs.os, "killpg", lambda *args: None, raising=False)
    jobs._supervise(output, job["job_id"])
    final = jobs.status(output)
    assert final["state"] == expected and final["exit_code"] == exit_code
    assert len(starts) == 1 and starts[0][0] == job["command"]
    assert starts[0][1]["start_new_session"] is True
    with pytest.raises(ValueError, match="initial state"):
        jobs._supervise(output, job["job_id"])
    assert len(starts) == 1
    jobs.stop(output)
    assert not (output / ".omni-server/stop.json").exists()


def test_stop_before_execution_creates_no_child(tmp_path, monkeypatch):
    output, job, _ = prepared(tmp_path, monkeypatch)
    jobs.stop(output)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("stopped before execution"))
    jobs._supervise(output, job["job_id"])
    assert jobs.status(output)["state"] == "stopped"


def test_stop_during_execution_is_handled_by_owning_supervisor(tmp_path, monkeypatch):
    output, job, _ = prepared(tmp_path, monkeypatch)
    starts, stopped = [], []

    class Child:
        pid = 54321

    child = Child()

    def still_running(value):
        jobs.stop(output)
        return False

    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: starts.append(a) or child)
    monkeypatch.setattr(jobs, "_stop_child", lambda value: stopped.append(value) or -15)
    monkeypatch.setattr(jobs, "_leader_exited", still_running)
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: None)
    jobs._supervise(output, job["job_id"])
    assert jobs.status(output)["state"] == "stopped"
    assert jobs.status(output)["exit_code"] == -15
    assert len(starts) == 1 and stopped == [child]


def test_child_group_stop_escalates_after_bounded_grace_period(monkeypatch):
    signals, waits = [], []
    monkeypatch.setattr(jobs.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: signals.append((pid, sig)), raising=False)
    monkeypatch.setattr(jobs, "_group_running", lambda group_id: True)
    times = iter([0, 5])
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(times))

    class Child:
        pid = 54321
        returncode = None

        def wait(self, timeout=None):
            waits.append(timeout)
            return -9

    assert jobs._stop_child(Child()) == -9
    assert signals == [(54321, jobs.signal.SIGTERM), (54321, 9)]
    assert waits == [None]


def test_child_is_not_reaped_before_killing_a_term_ignoring_descendant(monkeypatch):
    events = []
    monkeypatch.setattr(jobs.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(jobs.os, "killpg", lambda pid, sig: events.append(("signal", sig)), raising=False)
    # Direct child is already a zombie; a same-group descendant remains alive.
    monkeypatch.setattr(jobs, "_group_running", lambda group_id: True)
    times = iter([0, 5])
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(times))

    class Child:
        pid = 54321
        returncode = None

        def wait(self):
            events.append(("reap", self.pid))
            return -15

    assert jobs._stop_child(Child()) == -15
    assert events == [("signal", jobs.signal.SIGTERM), ("signal", 9), ("reap", 54321)]


def test_already_reaped_child_cannot_signal_a_reused_process_group(monkeypatch):
    monkeypatch.setattr(jobs.os, "killpg", lambda *args: pytest.fail("PID may already be reused"), raising=False)
    child = type("Child", (), {"pid": 54321, "returncode": 0})()
    assert jobs._stop_child(child) == 0


def test_child_launch_failure_is_recorded_without_another_attempt(tmp_path, monkeypatch):
    output, job, _ = prepared(tmp_path, monkeypatch)
    starts = []

    def fail_spawn(*args, **kwargs):
        starts.append(args)
        raise OSError("synthetic child launch failure")

    monkeypatch.setattr(jobs.subprocess, "Popen", fail_spawn)
    jobs._supervise(output, job["job_id"])
    final = jobs.status(output)
    assert final["state"] == "failed" and "child launch failure" in final["error"]
    assert len(starts) == 1
    with pytest.raises(ValueError, match="initial state"):
        jobs._supervise(output, job["job_id"])


def test_durable_run_lock_blocks_replay_after_crash(tmp_path, monkeypatch):
    output, job, _ = prepared(tmp_path, monkeypatch)
    (output / ".omni-server/run.lock").write_text(job["job_id"])
    before = (output / ".omni-server/job.json").read_bytes()
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: pytest.fail("must not replay child"))
    with pytest.raises(FileExistsError):
        jobs._supervise(output, job["job_id"])
    assert (output / ".omni-server/job.json").read_bytes() == before


def test_stop_marker_for_another_job_is_ignored(tmp_path, monkeypatch):
    output, job, _ = prepared(tmp_path, monkeypatch)
    jobs._write_json(output / ".omni-server/stop.json", {"job_id": "unrelated"})
    assert jobs.status(output)["stop_requested"] is False


def test_logs_tail_decodes_bad_bytes_and_cli_reads_state(tmp_path, monkeypatch, capsys):
    output, job, _ = prepared(tmp_path, monkeypatch)
    (output / ".omni-server/job.log").write_bytes(b"first\nsecond\n\xffthird\n")
    assert jobs.logs(output, 2) == "second\n\ufffdthird\n"
    with pytest.raises(ValueError, match="positive"):
        jobs.logs(output, 0)
    assert jobs.main(["status", "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == job["job_id"]


def _wait_for(output, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = jobs.status(output)
        if predicate(value):
            return value
        time.sleep(0.05)
    pytest.fail(f"job did not settle: {jobs.status(output)}")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux detached-process integration")
def test_detached_job_outlives_submitter_and_stops_its_child_group(tmp_path):
    output = tmp_path / "detached"
    descendant = ("import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                  "print('descendant-ready', os.getpid(), flush=True); time.sleep(60)")
    worker = ("import os,subprocess,sys,time; "
              f"subprocess.Popen([sys.executable, '-u', '-c', {descendant!r}]); "
              "print('worker-ready', os.getpid(), flush=True); time.sleep(60)")
    command = [sys.executable, "-u", "-c", worker]
    submitter = (
        "from pathlib import Path; from omni_story.library.server_jobs import start; "
        f"start({command!r}, Path({str(output)!r}), Path({str(tmp_path)!r}))"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(Path(jobs.__file__).parents[2]), environment.get("PYTHONPATH", "")])
    caller = subprocess.run([sys.executable, "-c", submitter], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, timeout=10)
    assert caller.returncode == 0, caller.stderr
    try:
        running = _wait_for(output, lambda job: job["state"] == "running" and "descendant-ready" in jobs.logs(output))
        descendant_pid = int(next(line for line in jobs.logs(output).splitlines() if line.startswith("descendant-ready ")).split()[1])
        assert running["supervisor_pid"] != os.getpid()
        assert os.getsid(running["supervisor_pid"]) == running["supervisor_pid"]
        assert os.getsid(running["child_pid"]) == running["child_pid"]
        assert os.getpgid(descendant_pid) == running["child_pid"]
        assert running["supervisor_alive"] is True
        jobs.stop(output)
        stopped = _wait_for(output, lambda job: job["state"] in jobs.TERMINAL_STATES)
        assert stopped["state"] == "stopped"
        assert jobs._identity(running["child_pid"]) is None
        assert jobs._identity(descendant_pid) is None
        assert "worker-ready" in jobs.logs(output)
    finally:
        jobs.stop(output)
