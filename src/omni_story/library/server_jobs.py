"""One detached Linux job, with durable state and no automatic restart or retry.

The command is an explicit argv supplied by the caller. This module chooses no
model provider and stores no environment variables. A supervisor runs once and
handles a token-bound stop marker; control clients never signal stored PIDs.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


TERMINAL_STATES = {"succeeded", "failed", "stopped"}
MODULE = "omni_story.library.server_jobs"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _directory(output):
    return Path(output).resolve() / ".omni-server"


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


def start(command: list[str], output: Path, cwd: Path) -> dict:
    """Start exactly one detached job in a new or empty output directory."""
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command):
        raise ValueError("command must be a nonempty argv list without empty or NUL arguments")
    cwd = Path(cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("cwd must be a directory")
    if not sys.platform.startswith("linux"):
        raise RuntimeError("detached server jobs currently require Linux")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("output must be a new or empty directory; jobs are never restarted")
    directory = _directory(output)
    # mkdir is the submission lock: a concurrent start cannot take this task.
    directory.mkdir(mode=0o700)
    job = {"schema_version": 1, "job_id": uuid.uuid4().hex, "state": "starting",
           "created_at": _now(), "command": command, "cwd": str(cwd),
           "output": str(output), "automatic_restart": False}
    _write_json(directory / "job.json", job)
    try:
        with (directory / "job.log").open("ab", buffering=0) as log:
            supervisor = subprocess.Popen(
                [sys.executable, "-m", MODULE, "_supervise", "--output", str(output), "--job-id", job["job_id"]],
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
    return status(output)


def status(output: Path) -> dict:
    """Read recorded state; a lost supervisor is unknown, never auto-resumed."""
    directory = _directory(output)
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


def logs(output: Path, lines: int = 100) -> str:
    if lines < 1:
        raise ValueError("lines must be positive")
    directory = _directory(output)
    _read_json(directory / "job.json")  # Require an actual task, not an arbitrary log path.
    with (directory / "job.log").open(encoding="utf-8", errors="replace") as handle:
        return "".join(deque(handle, maxlen=lines))


def stop(output: Path) -> dict:
    """Request the supervisor to stop its own child; never signal saved PIDs."""
    job = status(output)
    if job["state"] not in TERMINAL_STATES:
        _write_json(_directory(output) / "stop.json", {"job_id": job["job_id"], "requested_at": _now()})
    return status(output)


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


def _supervise(output, job_id):
    directory = _directory(output)
    job = _read_json(directory / "job.json")
    if job["job_id"] != job_id or job["state"] != "starting":
        raise ValueError("job token or initial state does not match")
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
        if action == "logs":
            subparser.add_argument("--lines", type=int, default=100)
        if action == "_supervise":
            subparser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "_supervise":
            return _supervise(args.output, args.job_id)
        if args.action == "start":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            result = start(command, args.output, args.cwd)
        elif args.action == "logs":
            print(logs(args.output, args.lines), end="")
            return 0
        else:
            result = {"status": status, "stop": stop}[args.action](args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
