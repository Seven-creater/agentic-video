"""One detached Linux job, with durable state and no automatic restart or retry.

The command is an explicit argv supplied by the caller. This module chooses no
model provider and stores no environment variables. A supervisor runs once and
handles a token-bound stop marker; control clients never signal stored PIDs.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .state import json_sha


TERMINAL_STATES = {"succeeded", "failed", "stopped"}
MODULE = "omni_story.library.server_jobs"
RECOVERY_NAME = "output_capacity_v1"
RECOVERY_POLICY = "opencode_known_output_starvation_recovery_v1"
PREFLIGHT_RECOVERY_NAME = "output_capacity_v1_preflight_fix"
PREFLIGHT_POLICY = "opencode_capacity_catalog_preflight_fix_v1"
REMAINING_RECOVERY_NAME = "remaining_candidate_v1"
REMAINING_POLICY = "opencode_remaining_initial_candidate_feedback_v1"
RECOVERY_NAMES = (RECOVERY_NAME, PREFLIGHT_RECOVERY_NAME, REMAINING_RECOVERY_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _directory(output, recovery_name=None):
    directory = Path(output).resolve() / ".omni-server"
    if recovery_name is not None:
        if recovery_name not in RECOVERY_NAMES:
            raise ValueError("unsupported recovery name")
        directory = directory / "recoveries" / recovery_name
        if directory.resolve() != directory:
            raise ValueError("recovery control directory must remain inside original output")
    return directory


def _write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(pid):
    """Linux process birth time, avoiding a reused PID in status reports."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def _launch_arguments(command, cwd):
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command):
        raise ValueError("command must be a nonempty argv list without empty or NUL arguments")
    cwd = Path(cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("cwd must be a directory")
    if not sys.platform.startswith("linux"):
        raise RuntimeError("detached server jobs currently require Linux")
    return cwd


def start(command: list[str], output: Path, cwd: Path) -> dict:
    """Start exactly one detached job in a new or empty output directory."""
    cwd = _launch_arguments(command, cwd)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output must be a new or empty directory; jobs are never restarted")
    return _launch(command, output, cwd)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preflight_binding(output, state, authorization, authorization_path, authorization_sha256):
    """Carry over the unused capacity remedy after the one known CPU failure."""
    entries = state.get("artifacts", {}).get("server_output_capacity_preflight_fix", [])
    if len(entries) != 1:
        raise ValueError("preflight fix requires exactly one registered proof")
    proof_path = Path(entries[0]["path"]).resolve(strict=True)
    if not proof_path.is_relative_to(output):
        raise ValueError("preflight fix proof must remain inside original output")
    proof = _read_json(proof_path)
    if entries[0]["sha256"] != json_sha(proof):
        raise ValueError("preflight fix registered proof changed")
    count = proof.get("baseline_request_count")
    if (proof.get("policy") != PREFLIGHT_POLICY or
            proof.get("recovery_name") != PREFLIGHT_RECOVERY_NAME or
            proof.get("task_id") != state.get("task_id") or
            proof.get("task_id") != authorization.get("task_id") or
            proof.get("input_lock") != state.get("input_lock") or
            proof.get("input_lock") != authorization.get("input_lock") or
            type(count) is not int or count != state["request_count"] or
            count != authorization.get("baseline_request_count") or
            proof.get("original_capacity_authorization_sha256") != authorization_sha256 or
            type(proof.get("new_model_requests_authorized")) is not int or
            proof["new_model_requests_authorized"] != 0):
        raise ValueError("preflight fix scope or unused request baseline changed")
    first_directory = _directory(output, RECOVERY_NAME)
    first_path = first_directory / "job.json"
    log_path = first_directory / "job.log"
    lock_path = first_directory / "run.lock"
    if (Path(proof["first_recovery_job_path"]).resolve(strict=True) != first_path or
            Path(proof["first_recovery_log_path"]).resolve(strict=True) != log_path or
            lock_path.resolve(strict=True) != lock_path or
            proof["first_recovery_job_sha256"] != _sha(first_path) or
            proof["first_recovery_log_sha256"] != _sha(log_path)):
        raise ValueError("preflight fix first recovery records changed")
    first = _read_json(first_path)
    first_binding = first.get("recovery", {})
    if (first.get("job_id") != proof.get("first_recovery_job_id") or
            first.get("state") != "failed" or first.get("exit_code") != 1 or
            first_binding.get("name") != RECOVERY_NAME or
            first_binding.get("authorization_path") != str(authorization_path) or
            first_binding.get("authorization_sha256") != authorization_sha256 or
            lock_path.read_text(encoding="utf-8").strip() != first["job_id"]):
        raise ValueError("preflight fix requires the known first failed capacity job")
    if ((first.get("supervisor_pid") and _identity(first["supervisor_pid"]) is not None) or
            (first.get("child_pid") and _group_running(first["child_pid"]))):
        raise ValueError("first capacity supervisor or child group is still running")
    if "LibraryStopped: library_file_set_changed" not in log_path.read_text(encoding="utf-8", errors="replace"):
        raise ValueError("preflight fix requires the known catalog CPU failure")
    return {"path": str(proof_path), "sha256": _sha(proof_path),
            "first_recovery_job_path": str(first_path), "first_recovery_job_sha256": _sha(first_path),
            "first_recovery_log_path": str(log_path), "first_recovery_log_sha256": _sha(log_path),
            "first_recovery_run_lock_sha256": _sha(lock_path)}


def _remaining_candidate_binding(output, state, authorization, authorization_path, authorization_sha256):
    """Use the remaining initial candidate after the bound caption-evidence rejection."""
    entries = state.get("artifacts", {}).get("server_remaining_candidate_feedback", [])
    if len(entries) != 1:
        raise ValueError("remaining candidate requires exactly one registered proof")
    proof_path = Path(entries[0]["path"]).resolve(strict=True)
    if not proof_path.is_relative_to(output):
        raise ValueError("remaining candidate proof must remain inside original output")
    proof = _read_json(proof_path)
    if entries[0]["sha256"] != json_sha(proof):
        raise ValueError("remaining candidate registered proof changed")
    count = proof.get("baseline_request_count")
    initial_count = authorization.get("baseline_request_count")
    config = state["input_lock"].get("configuration", {})
    if (proof.get("policy") != REMAINING_POLICY or
            proof.get("task_id") != state.get("task_id") or
            proof.get("task_id") != authorization.get("task_id") or
            proof.get("input_lock") != state["input_lock"] or
            proof.get("input_lock") != authorization.get("input_lock") or
            type(count) is not int or count != state["request_count"] or
            type(initial_count) is not int or count != initial_count + 2 or
            any(type(proof.get(key)) is not int or proof[key] != expected for key, expected in
                (("consumed_candidate", 0), ("remaining_candidate", 1), ("new_candidate_rounds", 0))) or
            config.get("max_rounds") != 2 or config.get("max_renders") != 2 or
            proof.get("no_unknown_replay") is not True or
            proof.get("original_capacity_authorization_sha256") != authorization_sha256):
        raise ValueError("remaining candidate scope or initial candidate allowance changed")
    baseline_path = Path(proof["baseline_state_path"]).resolve(strict=True)
    initial_path = Path(authorization["baseline_state_path"]).resolve(strict=True)
    if (not baseline_path.is_relative_to(output) or not initial_path.is_relative_to(output) or
            _sha(baseline_path) != proof["baseline_state_sha256"] or
            _sha(initial_path) != authorization["baseline_state_sha256"]):
        raise ValueError("remaining candidate baseline bytes changed")
    baseline, initial = _read_json(baseline_path), _read_json(initial_path)
    if (baseline.get("task_id") != state["task_id"] or initial.get("task_id") != state["task_id"] or
            baseline.get("input_lock") != state["input_lock"] or initial.get("input_lock") != state["input_lock"] or
            baseline.get("request_count") != count or initial.get("request_count") != initial_count or
            baseline.get("calls") != state["calls"] or
            initial.get("calls") != state["calls"][:initial_count] or
            any(call.get("name", "").startswith("plan_1") for call in state["calls"]) or
            (output / "result.json").exists() or any(output.glob("render_[01]"))):
        raise ValueError("remaining candidate call prefix or zero-render baseline changed")
    original, failed = state["calls"][-2:]
    if (original.get("name") != "plan_0_capacity_v2" or original.get("repair_of") is not None or
            failed.get("name") != "plan_0_capacity_v2_repair" or failed.get("repair_of") != original.get("id") or
            failed.get("id") != proof.get("failed_plan_call_id") or
            failed.get("request_sha256") != proof.get("request_sha256") or
            failed.get("response_sha256") != proof.get("response_sha256")):
        raise ValueError("remaining candidate requires the failed capacity original and sole repair")
    call_directory = (output / "calls" / failed["id"]).resolve(strict=True)
    if not call_directory.is_relative_to(output):
        raise ValueError("remaining candidate failed call must remain inside original output")
    if (json_sha(_read_json(call_directory / "request.json")) != proof["request_sha256"] or
            json_sha(_read_json(call_directory / "response.json")) != proof["response_sha256"] or
            _sha(call_directory / "protocol_failure.json") != proof["failed_plan_protocol_failure_sha256"] or
            _read_json(call_directory / "protocol_failure.json").get("error") !=
            "plan:caption_event_outside_selected_range"):
        raise ValueError("remaining candidate known caption rejection records changed")
    controller_directory = _directory(output, PREFLIGHT_RECOVERY_NAME)
    job_path, log_path = controller_directory / "job.json", controller_directory / "job.log"
    lock_path = controller_directory / "run.lock"
    failure_path = output / "failure_capacity_recovery_v1.json"
    if (Path(proof["failed_controller_job_path"]).resolve(strict=True) != job_path or
            Path(proof["failed_controller_log_path"]).resolve(strict=True) != log_path or
            Path(proof["failure_report_path"]).resolve(strict=True) != failure_path or
            lock_path.resolve(strict=True) != lock_path or
            _sha(job_path) != proof["failed_controller_job_sha256"] or
            _sha(log_path) != proof["failed_controller_log_sha256"] or
            _sha(failure_path) != proof["failure_report_sha256"] or
            _read_json(failure_path).get("error") != "model_protocol_repair_exhausted:plan_0_capacity_v2"):
        raise ValueError("remaining candidate controller or failure records changed")
    controller = _read_json(job_path)
    previous_binding = controller.get("recovery", {})
    if (controller.get("job_id") != proof.get("failed_controller_job_id") or
            controller.get("state") != "failed" or controller.get("exit_code") != 1 or
            previous_binding.get("name") != PREFLIGHT_RECOVERY_NAME or
            previous_binding.get("authorization_path") != str(authorization_path) or
            previous_binding.get("authorization_sha256") != authorization_sha256 or
            lock_path.read_text(encoding="utf-8").strip() != controller["job_id"]):
        raise ValueError("remaining candidate requires the known failed caption-rejection controller")
    if ((controller.get("supervisor_pid") and _identity(controller["supervisor_pid"]) is not None) or
            (controller.get("child_pid") and _group_running(controller["child_pid"]))):
        raise ValueError("caption-rejection supervisor or child group is still running")
    return {"path": str(proof_path), "sha256": _sha(proof_path),
            "baseline_state_sha256": _sha(baseline_path),
            "failed_controller_job_sha256": _sha(job_path), "failed_controller_log_sha256": _sha(log_path),
            "failed_controller_run_lock_sha256": _sha(lock_path), "failure_report_sha256": _sha(failure_path)}


def _recovery_binding(output, authorization_path, authorization_sha256, recovery_name):
    """Bind a fixed, settled continuation to the original capacity authorization."""
    _directory(output, recovery_name)  # Reject arbitrary control directories.
    authorization_path = Path(authorization_path).resolve(strict=True)
    if _sha(authorization_path) != authorization_sha256:
        raise ValueError("recovery authorization bytes changed")
    authorization = _read_json(authorization_path)
    original_directory = _directory(output)
    original_path = original_directory / "job.json"
    original = _read_json(original_path)
    if (authorization.get("policy") != RECOVERY_POLICY or
            authorization.get("recovery_name") != RECOVERY_NAME or
            authorization.get("output") != str(output) or
            authorization.get("original_job_id") != original.get("job_id") or
            authorization.get("original_job_sha256") != _sha(original_path)):
        raise ValueError("recovery authorization does not bind the original job")
    if original.get("state") != "failed" or original.get("exit_code") != 1:
        raise ValueError("capacity recovery requires the original known failed job")
    original_lock = original_directory / "run.lock"
    if original_lock.read_text(encoding="utf-8").strip() != original["job_id"]:
        raise ValueError("original supervisor run lock changed")
    if ((original.get("supervisor_pid") and _identity(original["supervisor_pid"]) is not None) or
            (original.get("child_pid") and _group_running(original["child_pid"]))):
        raise ValueError("original supervisor or child group is still running")
    state_path = output / "library_state.json"
    state = _read_json(state_path)
    calls = state.get("calls")
    if (not isinstance(calls, list) or not calls or
            state.get("request_count") != len(calls) or
            any(call.get("status") != "received" for call in calls)):
        raise ValueError("capacity recovery requires all original calls received")
    registered = state.get("artifacts", {}).get("server_output_capacity_recovery", [])
    if not any(Path(entry["path"]).resolve() == authorization_path and
               entry["sha256"] == json_sha(authorization) for entry in registered):
        raise ValueError("recovery authorization is not registered in original state")
    failure_path = output / "failure.json"
    _read_json(failure_path)  # A recorded known failure is required, never inferred.
    binding = {"name": recovery_name, "authorization_path": str(authorization_path),
            "authorization_sha256": authorization_sha256, "original_job_id": original["job_id"],
            "original_job_sha256": _sha(original_path), "original_state_sha256": _sha(state_path),
            "original_run_lock_sha256": _sha(original_lock),
            "original_failure_sha256": _sha(failure_path)}
    if recovery_name == PREFLIGHT_RECOVERY_NAME:
        binding["preflight_fix"] = _preflight_binding(output, state, authorization,
                                                     authorization_path, authorization_sha256)
    elif recovery_name == REMAINING_RECOVERY_NAME:
        binding["remaining_candidate"] = _remaining_candidate_binding(output, state, authorization,
                                                                       authorization_path, authorization_sha256)
    return binding


def start_recovery(command: list[str], output: Path, cwd: Path, *,
                   authorization_path: Path, authorization_sha256: str,
                   recovery_name: str = RECOVERY_NAME) -> dict:
    """Launch one fixed, explicitly registered continuation control.

    The caller validates the authorization's editing/provider semantics. This
    layer checks its registration and task binding, then creates an independent
    control directory without altering the original supervisor or its run lock.
    """
    cwd = _launch_arguments(command, cwd)
    output = Path(output).resolve(strict=True)
    binding = _recovery_binding(output, authorization_path, authorization_sha256, recovery_name)
    directory = _directory(output, recovery_name)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    return _launch(command, output, cwd, binding)


def _launch(command, output, cwd, recovery=None):
    recovery_name = recovery["name"] if recovery else None
    directory = _directory(output, recovery_name)
    # mkdir is the submission lock: a concurrent start cannot take this task.
    directory.mkdir(mode=0o700)
    job = {"schema_version": 1, "job_id": uuid.uuid4().hex, "state": "starting",
           "created_at": _now(), "command": command, "cwd": str(cwd),
           "output": str(output), "automatic_restart": False}
    if recovery:
        job["recovery"] = recovery
    _write_json(directory / "job.json", job)
    try:
        with (directory / "job.log").open("ab", buffering=0) as log:
            supervisor_command = [sys.executable, "-m", MODULE, "_supervise", "--output", str(output), "--job-id", job["job_id"]]
            if recovery_name:
                supervisor_command += ["--recovery-name", recovery_name]
            supervisor = subprocess.Popen(
                supervisor_command,
                cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, close_fds=True)
    except Exception as error:
        job.update(state="failed", finished_at=_now(), error=f"supervisor launch failed: {error}")
        _write_json(directory / "job.json", job)
        raise
    # Separate file prevents parent/supervisor job.json write races. Failure here
    # cannot be mislabeled a failed launch: the supervisor already exists.
    _write_json(directory / "launch.json", {"job_id": job["job_id"], "pid": supervisor.pid,
                                           "identity": _identity(supervisor.pid)})
    return status(output, recovery_name=recovery_name)


def status(output: Path, *, recovery_name=None) -> dict:
    """Read recorded state; a lost supervisor is unknown, never auto-resumed."""
    directory = _directory(output, recovery_name)
    job = _read_json(directory / "job.json")
    if job["state"] not in TERMINAL_STATES:
        launch = _read_json(directory / "launch.json") if (directory / "launch.json").exists() else {}
        pid = job.get("supervisor_pid", launch.get("pid"))
        identity = job.get("supervisor_identity", launch.get("identity"))
        alive = bool(pid and identity and _identity(pid) == identity)
        job["supervisor_alive"] = alive
        if pid and not alive:
            job["recorded_state"] = job["state"]
            job["state"] = "unknown"
    job["stop_requested"] = _stop_requested(directory, job["job_id"])
    return job


def logs(output: Path, lines: int = 100, *, recovery_name=None) -> str:
    if lines < 1:
        raise ValueError("lines must be positive")
    directory = _directory(output, recovery_name)
    _read_json(directory / "job.json")  # Require an actual task, not an arbitrary log path.
    with (directory / "job.log").open(encoding="utf-8", errors="replace") as handle:
        return "".join(deque(handle, maxlen=lines))


def stop(output: Path, *, recovery_name=None) -> dict:
    """Request the supervisor to stop its own child; never signal saved PIDs."""
    job = status(output, recovery_name=recovery_name)
    if job["state"] not in TERMINAL_STATES:
        _write_json(_directory(output, recovery_name) / "stop.json", {"job_id": job["job_id"], "requested_at": _now()})
    return status(output, recovery_name=recovery_name)


def _stop_requested(directory, job_id):
    try:
        return _read_json(directory / "stop.json").get("job_id") == job_id
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _stop_child(child):
    if child.returncode is not None:
        return child.returncode
    # Keep the leader unreaped until all group members are gone or SIGKILL has
    # been sent. Its reserved PID also prevents reuse of this process-group ID.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while _group_running(child.pid):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if _group_running(child.pid):
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return child.wait()


def _group_running(group_id):
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return True  # Conservatively retain the full grace period.
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group_id and fields[0] != "Z":
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def _leader_exited(child):
    return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _supervise(output, job_id, recovery_name=None):
    output = Path(output).resolve()
    directory = _directory(output, recovery_name)
    job = _read_json(directory / "job.json")
    if job["job_id"] != job_id or job["state"] != "starting":
        raise ValueError("job token or initial state does not match")
    if recovery_name:
        binding = job.get("recovery", {})
        try:
            actual = _recovery_binding(output, binding["authorization_path"],
                                       binding["authorization_sha256"], recovery_name)
            if actual != binding:
                raise ValueError("original recovery baseline changed before execution")
        except (OSError, ValueError, KeyError) as error:
            job.update(state="failed", finished_at=_now(), error=f"recovery preflight failed: {error}")
            _write_json(directory / "job.json", job)
            raise
    # This durable, never removed marker forbids a second child even after a crash.
    with (directory / "run.lock").open("x", encoding="utf-8") as handle:
        handle.write(job_id + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    child = None
    interrupted = False

    def request_stop(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous_handlers = {signum: signal.signal(signum, request_stop) for signum in (signal.SIGTERM, signal.SIGINT)}
    if hasattr(signal, "SIGCHLD"):
        # An inherited SIG_IGN would let Linux reap the leader automatically,
        # defeating the reserved-PID guarantee used while stopping its group.
        previous_handlers[signal.SIGCHLD] = signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    try:
        job.update(supervisor_pid=os.getpid(), supervisor_identity=_identity(os.getpid()))
        if _stop_requested(directory, job_id):
            job.update(state="stopped", finished_at=_now(), exit_code=None)
        else:
            _write_json(directory / "job.json", job)
            child = subprocess.Popen(job["command"], cwd=job["cwd"], stdin=subprocess.DEVNULL,
                                     start_new_session=True, close_fds=True)
            job.update(state="running", started_at=_now(), child_pid=child.pid)
            _write_json(directory / "job.json", job)
            while True:
                if interrupted or _stop_requested(directory, job_id):
                    code = _stop_child(child)
                    job.update(state="stopped", finished_at=_now(), exit_code=code)
                    break
                if _leader_exited(child):
                    code = _stop_child(child)  # Clean up any descendants before reaping.
                    job.update(state="succeeded" if code == 0 else "failed", finished_at=_now(), exit_code=code)
                    break
                time.sleep(0.2)
    except Exception as error:
        if child is not None and child.returncode is None:
            _stop_child(child)
        job.update(state="failed", finished_at=_now(), error=f"{type(error).__name__}: {error}")
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    _write_json(directory / "job.json", job)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    start_parser = commands.add_parser("start", help="run an explicit command once, detached on Linux")
    start_parser.add_argument("--output", type=Path, required=True)
    start_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    start_parser.add_argument("command", nargs=argparse.REMAINDER)
    for action in ("status", "logs", "stop", "_supervise"):
        subparser = commands.add_parser(action)
        subparser.add_argument("--output", type=Path, required=True)
        subparser.add_argument("--recovery-name", choices=RECOVERY_NAMES)
        if action == "logs":
            subparser.add_argument("--lines", type=int, default=100)
        if action == "_supervise":
            subparser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "_supervise":
            return _supervise(args.output, args.job_id, args.recovery_name)
        if args.action == "start":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            result = start(command, args.output, args.cwd)
        elif args.action == "logs":
            print(logs(args.output, args.lines, recovery_name=args.recovery_name), end="")
            return 0
        else:
            result = {"status": status, "stop": stop}[args.action](args.output, recovery_name=args.recovery_name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
