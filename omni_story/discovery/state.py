"""Persistent budgets and write-before-send records, independent of story budgets."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import uuid

from ..pipeline import json_sha, write

LIMITS = {"candidates": 20, "reviews": 3, "steps": 80, "qwen_calls": 100,
          "omni_calls": 8, "active_seconds": 1800}


class DiscoveryStopped(RuntimeError):
    pass


@contextmanager
def session_lock(output):
    """OS lock prevents concurrent CLI invocations from losing counters or double-sending."""
    path = Path(output).resolve()
    path.mkdir(parents=True, exist_ok=True)
    handle = (path / ".session.lock").open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DiscoveryStopped("discovery_session_already_running") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


class State:
    def __init__(self, output, config):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.path = self.output / "discovery_state.json"
        self.config = config
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if self.data["config"] != config or self.data["limits"] != LIMITS:
                raise DiscoveryStopped("discovery_config_changed_use_original_configuration")
        else:
            self.data = {"schema_version": "douyin_discovery_v1", "session_id": str(uuid.uuid4()),
                         "config": config, "limits": dict(LIMITS), "status": "created",
                         "steps": 0, "qwen_calls": 0, "omni_calls": 0,
                         "active_seconds": 0, "candidates": {}, "searches": [], "calls": []}
            self.save()
        self._started = time.monotonic()
        self._paused = False
        self.run_error = None
        for call in self.data["calls"]:
            if call["status"] == "submitted":
                raise DiscoveryStopped("paid_request_outcome_unknown:" + call["id"])

    def save(self):
        write(self.path, self.data)

    def checkpoint(self):
        now = time.monotonic()
        if not self._paused:
            self.data["active_seconds"] += now - self._started
        self._started = now
        self.save()

    def check_time(self):
        self.checkpoint()
        if self.run_error:
            raise DiscoveryStopped(self.run_error)
        if self.data["active_seconds"] >= LIMITS["active_seconds"]:
            raise DiscoveryStopped("active_time_budget_exhausted")

    def remaining_seconds(self):
        elapsed = 0 if self._paused else time.monotonic() - self._started
        return max(0, LIMITS["active_seconds"] - self.data["active_seconds"] - elapsed)

    @contextmanager
    def human_pause(self, reason):
        self.checkpoint()
        self._paused = True
        self.data.update(status="awaiting_login", pause_reason=reason)
        self.save()
        try:
            yield
        finally:
            self._paused = False
            self._started = time.monotonic()
            self.data.update(status="running")
            self.data.pop("pause_reason", None)
            self.save()

    @contextmanager
    def suspend_time(self):
        """The screenplay has its own budget; its elapsed time is not browsing time."""
        self.checkpoint()
        self._paused = True
        try:
            yield
        finally:
            self._paused = False
            self._started = time.monotonic()

    def step(self):
        self.check_time()
        if self.data["steps"] >= LIMITS["steps"]:
            raise DiscoveryStopped("browser_step_budget_exhausted")
        self.data["steps"] += 1
        self.save()

    def begin_call(self, kind, name, request):
        self.check_time()
        if any(c["status"] == "submitted" for c in self.data["calls"]):
            raise DiscoveryStopped("paid_request_outcome_unknown_no_replay")
        key = kind + "_calls"
        if self.data[key] >= LIMITS[key]:
            raise DiscoveryStopped(kind + "_call_budget_exhausted")
        self.data[key] += 1
        call_id = f"{kind}_{self.data[key]:03d}_{name}"
        folder = self.output / "calls" / call_id
        folder.mkdir(parents=True)
        write(folder / "request.json", request)
        call = {"id": call_id, "kind": kind, "name": name, "status": "submitted",
                "request_sha256": json_sha(request), "usage": {}}
        self.data["calls"].append(call)
        self.save()  # must happen before POST; a crash here never permits an automatic replay
        return call, folder

    def finish_call(self, call, folder, response, *, status="received", usage=None):
        write(folder / "response.json", response)
        call.update(status=status, response_sha256=json_sha(response), usage=usage or {})
        if status == "rejected":
            self.run_error = call["kind"] + "_HTTP_rejected_check_recorded_response"
        self.checkpoint()

    def candidate(self, candidate):
        aid = candidate["aweme_id"]
        if not isinstance(aid, str) or not aid.isascii() or not aid.isdecimal():
            raise ValueError("invalid_aweme_id")
        if aid in self.data["candidates"]:
            return False
        if len(self.data["candidates"]) >= LIMITS["candidates"]:
            raise DiscoveryStopped("candidate_budget_exhausted")
        self.data["candidates"][aid] = candidate
        self.save()
        return True

    @property
    def reviewed(self):
        return [row for row in self.data["candidates"].values() if row.get("review")]

    def result(self, **result):
        self.checkpoint()
        result.update(session_id=self.data["session_id"],
                      candidate_count=len(self.data["candidates"]), reviewed_count=len(self.reviewed),
                      usage={kind: {"calls": self.data[kind + "_calls"],
                         "prompt_tokens": sum(c["usage"].get("prompt_tokens", 0) or 0
                                              for c in self.data["calls"] if c["kind"] == kind),
                         "completion_tokens": sum(c["usage"].get("completion_tokens", 0) or 0
                                                  for c in self.data["calls"] if c["kind"] == kind)}
                             for kind in ("qwen", "omni")},
                      image_video_generation=False)
        self.data["status"] = result["status"]
        self.save()
        write(self.output / "result.json", result)
        return result
