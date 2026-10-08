"""Bounded, write-before-send records for supported-agent MCP orchestration.

This module does not send requests or embed credentials. An external supported
agent records a request here before invoking the official MCP tool.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from .prompts import POLICY_VERSION


class LibraryStopped(RuntimeError):
    pass


def json_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def scope_fingerprint(scope):
    if not isinstance(scope, dict):
        raise LibraryStopped('independent_request_requires_bound_media_scope')
    kind, sha = scope.get('kind'), scope.get('source_sha256')
    start, end = scope.get('source_start_s'), scope.get('source_end_s')
    if (kind not in {'sparse_contact_sheet','continuous_window','complete_file'} or
            not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{64}',sha) or
            type(start) not in (int,float) or type(end) not in (int,float) or
            not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end):
        raise LibraryStopped('independent_request_invalid_media_scope')
    return kind,sha,float(start),float(end)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A long target basename must not push the same-directory temp over Windows' path limit.
    temporary = path.with_name(uuid.uuid4().hex + ".tmp")
    serialized = False
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        serialized = True
        # Windows readers/antivirus can briefly prevent replacing an otherwise
        # valid ledger. Retry only the filesystem rename, never the paid request.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except OSError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(.02 * (2 ** attempt))
    finally:
        # Keep a fully written file if replace ultimately fails, so its exact
        # bytes and the original error remain available for recovery.
        if not serialized and temporary.exists():
            temporary.unlink()


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
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
            raise LibraryStopped("library_state_is_locked") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


class LibraryState:
    """One immutable input/policy task, with at most 80 external model calls.

The registry prevents restarting identical inputs in a sibling output directory
to reset the budget. Pass a shared registry_path if runs use different parents.
Restarting a pending/uncertain call permits inspection, but no further submission.
"""

    def __init__(self, output, input_lock, *, max_requests=80, registry_path=None):
        if type(max_requests) is not int or not 1 <= max_requests <= 80:
            raise ValueError("max_requests_must_be_1_to_80")
        self.output = Path(output).resolve()
        self.path = self.output / "library_state.json"
        self.lock_path = self.output / ".state.lock"
        self.output.mkdir(parents=True, exist_ok=True)
        # Copy through JSON so caller mutation cannot change the locked inputs.
        self.input_lock = json.loads(json.dumps(input_lock, allow_nan=False))
        self.max_requests = max_requests
        self.registry_path = Path(registry_path or self.output.parent / ".library_runs.json").resolve()
        task_key = json_sha({"inputs": self.input_lock, "policy": POLICY_VERSION})
        with file_lock(self.registry_path.with_suffix(".lock")):
            registry = json.loads(self.registry_path.read_text(encoding="utf-8")) if self.registry_path.exists() else {}
            registered = registry.get(task_key)
            if registered and Path(registered["output"]).resolve() != self.output:
                raise LibraryStopped("same_inputs_already_registered_use_original_output:" + registered["output"])
            with file_lock(self.lock_path):
                if self.path.exists():
                    self._reload()
                    if (self.data["input_lock"] != self.input_lock or
                            self.data["policy_version"] != POLICY_VERSION or
                            self.data["max_requests"] != max_requests):
                        raise LibraryStopped("locked_inputs_policy_or_budget_changed")
                elif registered:
                    raise LibraryStopped("registered_task_state_missing_do_not_reset_budget")
                else:
                    self.data = {"schema_version": "library_state_v1", "policy_version": POLICY_VERSION,
                                 "task_id": str(uuid.uuid4()), "created_at": _timestamp(),
                                 "input_lock": self.input_lock, "max_requests": max_requests,
                                 "request_count": 0, "calls": [], "artifacts": {}}
                    self._save()
                registry[task_key] = {"output": str(self.output), "task_id": self.data["task_id"]}
                write_json(self.registry_path, registry)

    def _reload(self):
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self):
        write_json(self.path, self.data)

    def _call(self, call):
        call_id = call["id"] if isinstance(call, dict) else call
        for record in self.data["calls"]:
            if record["id"] == call_id:
                return record
        raise LibraryStopped("unknown_call_id")

    def begin_call(self, name, request, *, repair_of=None):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("unsafe_call_name")
        digest = json_sha(request)
        with file_lock(self.lock_path):
            self._reload()
            if any(call["status"] == "submitted" for call in self.data["calls"]):
                raise LibraryStopped("request_outcome_unknown_no_replay")
            uncertain = [call for call in self.data['calls'] if call['status'] == 'uncertain']
            if uncertain:
                if self.data.get('continuation_policy') != 'independent_media_no_unknown_replay_v1':
                    raise LibraryStopped('request_outcome_unknown_no_replay')
                media_sha = request.get('media_sha256')
                scope = request.get('observation_scope')
                if not isinstance(media_sha, str) or not media_sha or not isinstance(scope, dict):
                    raise LibraryStopped('independent_request_requires_bound_media_scope')
                new_fingerprint = scope_fingerprint(scope)
                for lost in uncertain:
                    old = json.loads((self.output/'calls'/lost['id']/'request.json').read_text(encoding='utf-8'))
                    if json_sha(old) != lost['request_sha256']:
                        raise LibraryStopped('unknown_original_request_modified')
                    old_scope = old.get('observation_scope')
                    if old_scope is None:
                        media_path = old.get('arguments',{}).get('image_source') or old.get('arguments',{}).get('video_source')
                        lineage_path = Path(media_path).parent/'lineage.json' if media_path else None
                        if lineage_path and lineage_path.exists():
                            lineage = json.loads(lineage_path.read_text(encoding='utf-8'))
                            old_scope = {k:lineage[k] for k in ('kind','source_sha256','source_start_s','source_end_s')}
                    if (old.get('media_sha256') == media_sha or lost['request_sha256'] == digest or
                            old_scope is None or scope_fingerprint(old_scope) == new_fingerprint):
                        raise LibraryStopped('unknown_media_must_not_be_resubmitted')
            if self.data["request_count"] >= self.max_requests:
                raise LibraryStopped("model_request_budget_exhausted")
            if any(call["request_sha256"] == digest for call in self.data["calls"]):
                raise LibraryStopped("request_already_recorded_reuse_existing_result")
            repair_id = None
            if repair_of is not None:
                previous = self._call(repair_of)
                repair_id = previous["id"]
                if previous["status"] != "received" or previous.get("repair_of"):
                    raise LibraryStopped("format_repair_requires_received_original_response")
                if any(call.get("repair_of") == repair_id for call in self.data["calls"]):
                    raise LibraryStopped("format_repair_limit_exhausted")
            count = self.data["request_count"] + 1
            call_id = f"glm_{count:03d}_{name}"
            folder = self.output / "calls" / call_id
            folder.mkdir(parents=True, exist_ok=False)
            write_json(folder / "request.json", request)
            record = {"id": call_id, "name": name, "status": "submitted", "submitted_at": _timestamp(),
                      "request_sha256": digest, "usage": {}, "repair_of": repair_id}
            self.data["request_count"] = count
            self.data["calls"].append(record)
            self._save()  # External MCP is invoked only after this returns.
            return record, folder

    def complete_call(self, call, response, *, usage=None):
        with file_lock(self.lock_path):
            self._reload()
            record = self._call(call)
            if record["status"] != "submitted":
                raise LibraryStopped("call_not_submitted_cannot_overwrite_history")
            write_json(self.output / "calls" / record["id"] / "response.json", response)
            record.update(status="received", completed_at=_timestamp(), response_sha256=json_sha(response),
                          usage=usage or {})
            self._save()

    def fail_call(self, call, error, *, uncertain=True):
        with file_lock(self.lock_path):
            self._reload()
            record = self._call(call)
            if record["status"] != "submitted":
                raise LibraryStopped("call_not_submitted_cannot_overwrite_history")
            record.update(status="uncertain" if uncertain else "failed_known", error=str(error),
                          completed_at=_timestamp())
            write_json(self.output / "calls" / record["id"] / "failure.json",
                       {"error": str(error), "uncertain": bool(uncertain)})
            self._save()

    def reconcile_received(self, call, response, *, evidence, usage=None):
        """Append new transport evidence; preserve earlier failure classifications.

        Only a captured HTTP 200 model reply can resolve a transport failure.
        The model content may still be empty/invalid and requires normal validation.
        """
        if evidence.get('status') != 200 or not evidence.get('body'):
            raise LibraryStopped('reconciliation_requires_captured_http_200')
        body = json.loads(evidence['body'])
        if not isinstance(body.get('choices'), list) or not body['choices']:
            raise LibraryStopped('reconciliation_requires_model_reply')
        with file_lock(self.lock_path):
            self._reload()
            record = self._call(call)
            if record['status'] not in {'submitted', 'uncertain', 'failed_known'}:
                raise LibraryStopped('reconciliation_already_received')
            if evidence.get('job_id') != record['id']:
                raise LibraryStopped('reconciliation_job_mismatch')
            folder = self.output / 'calls' / record['id']
            write_json(folder / 'transport_reconciliation.json',
                       {'previous_record': dict(record), 'evidence': evidence,
                        'reason': 'captured_original_HTTP_reply_no_POST_replay'})
            write_json(folder / 'response.json', response)
            record.update(status='received', completed_at=_timestamp(),
                          response_sha256=json_sha(response), usage=usage or body.get('usage', {}),
                          reconciled_from_http=True)
            self._save()

    def reclassify_uncertain(self, call, *, evidence):
        """A transport error may later be proved to have an unknown paid outcome."""
        with file_lock(self.lock_path):
            self._reload()
            record = self._call(call)
            if record['status'] not in {'failed_known', 'submitted'}:
                raise LibraryStopped('cannot_reclassify_call_status')
            write_json(self.output / 'calls' / record['id'] / 'uncertain_reclassification.json',
                       {'previous_record':dict(record), 'http_evidence':evidence,
                        'reason':'submitted_HTTP_without_known_response_no_replay'})
            record.update(status='uncertain', reconciled_at=_timestamp())
            self._save()

    def enable_independent_continuation(self):
        """Append a policy allowing bound new inputs, without replaying unknowns."""
        self._reload()
        policy = 'independent_media_no_unknown_replay_v1'
        if self.data.get('continuation_policy') == policy:
            return
        self.set_artifact('continuation_policy', {'policy':policy,
            'reason':'Continue authorized independent work without replaying lost paid submissions.',
            'preserved':['input_lock','original_policy_version','all_calls','request_count','max_requests'],
            'unknown_media_or_scope_blocked':True,'submitted_blocks_all_new_work':True})
        with file_lock(self.lock_path):
            self._reload()
            self.data['continuation_policy'] = policy
            self._save()

    def set_artifact(self, name, payload):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
            raise ValueError("unsafe_artifact_name")
        with file_lock(self.lock_path):
            self._reload()
            history = self.data["artifacts"].setdefault(name, [])
            path = self.output / "artifacts" / f"{name}_{len(history) + 1:03d}.json"
            write_json(path, payload)
            history.append({"path": str(path), "sha256": json_sha(payload), "at": _timestamp()})
            self._save()
            return path

    def usage(self):
        self._reload()
        return {"requests": self.data["request_count"], "max_requests": self.max_requests,
                "token_usage_scope": "captured_responses_only_unknown_charges_not_zero",
                "prompt_tokens": sum(call["usage"].get("prompt_tokens", 0) or 0 for call in self.data["calls"]),
                "completion_tokens": sum(call["usage"].get("completion_tokens", 0) or 0 for call in self.data["calls"]),
                "uncertain_calls": [call["id"] for call in self.data["calls"]
                                    if call["status"] in {"submitted", "uncertain"}]}
