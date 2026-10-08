"""Synthetic append-only GLM skill-trial protections; no model or real run I/O."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import visual_story_trial_state as trial
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "synthetic_task"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    reference = artifacts / "reference.mp4"
    reference.write_bytes(b"synthetic reference source")
    parent = artifacts / "parent77.mp4"
    parent.write_bytes(b"synthetic independently bound 77 second parent")
    knowledge = artifacts / "SKILL.md"
    knowledge.write_text("Only generic editing instructions.", encoding="utf-8")
    marker = artifacts / "old_evidence.json"
    write_json(marker, {"old": "immutable evidence"})
    journal = root / "mcp_http.jsonl"
    journal.write_bytes(b'{"historical":"captured"}\n')
    ref_sha = sha256_file(reference)
    unknown_scopes = {
        4: {"kind": "sparse_contact_sheet", "source_sha256": "4" * 64,
            "source_start_s": 600.0, "source_end_s": 1200.0},
        131: {"kind": "continuous_window", "source_sha256": ref_sha,
              "source_start_s": 0.0, "source_end_s": 21.933333},
        166: {"kind": "continuous_window", "source_sha256": "6" * 64,
              "source_start_s": 0.0, "source_end_s": 34.0},
    }
    calls, unknown, protected = [], [], []
    for index in range(1, 251):
        ident = f"glm_{index:03d}_old_{index}"
        request = {"synthetic_old_request": index}
        record = {"id": ident, "name": f"old_{index}", "status": "received",
                  "repair_of": None, "usage": {}}
        if index in unknown_scopes:
            media = artifacts / f"old_input_{index}.bin"
            media.write_bytes(f"old unknown media {index}".encode())
            field = "image_source" if index == 4 else "video_source"
            request = {"tool": "analyze_image" if index == 4 else "analyze_video",
                       "arguments": {field: str(media), "prompt": "old frozen request"},
                       "media_sha256": sha256_file(media), "observation_scope": unknown_scopes[index]}
            record["status"] = "uncertain"
            unknown.append({"call_id": ident, "request_sha256": json_sha(request),
                            "media_sha256": request["media_sha256"], "scope": request["observation_scope"]})
        folder = root / "calls" / ident
        # Only unknown inputs and one representative old reply need physical
        # files; all 250 immutable call records remain in the ledger prefix.
        if index in unknown_scopes or index == 1:
            write_json(folder / "request.json", request)
            protected.append({"path": str(folder / "request.json"), "sha256": sha256_file(folder / "request.json")})
        record["request_sha256"] = json_sha(request)
        if record["status"] == "received":
            response = {"old_response": index}
            if index == 1:
                write_json(folder / "response.json", response)
                protected.append({"path": str(folder / "response.json"), "sha256": sha256_file(folder / "response.json")})
            record["response_sha256"] = json_sha(response)
        calls.append(record)
    baseline = {"task_id": "synthetic-task", "policy_version": "reference_library_glm_mcp_v1",
                "max_requests": 80, "request_count": 250, "calls": calls,
                "input_lock": {"reference_sha256": ref_sha, "library_sources": [], "configuration": {}},
                "continuation_policy": "independent_media_no_unknown_replay_v1",
                "artifacts": {"old_evidence": [{"path": str(marker), "sha256": json_sha(read(marker))}]}}
    snapshot = artifacts / "baseline_state.json"
    write_json(snapshot, baseline)
    execution = artifacts / trial.POLICY
    execution.mkdir()
    grant = {"policy": trial.POLICY, "task_id": baseline["task_id"],
             "input_lock_sha256": json_sha(baseline["input_lock"]),
             "baseline_request_count": 250, "baseline_state_path": str(snapshot),
             "baseline_state_sha256": sha256_file(snapshot), "prefix_calls_sha256": json_sha(calls),
             "execution_directory": str(execution), "unknown_inputs": unknown,
             "protected_files": [*protected, {"path": str(marker), "sha256": sha256_file(marker)}],
             "knowledge_files": [{"path": str(knowledge), "sha256": sha256_file(knowledge)}],
             "journal_prefixes": [{"path": str(journal), "bytes": journal.stat().st_size,
                                    "sha256": sha256_file(journal)}],
             "reference": {"path": str(reference), "sha256": ref_sha, "duration_s": 21.933333},
             "parent": {"path": str(parent), "sha256": sha256_file(parent), "duration_s": 77.366667},
             "max_concurrency": 1, "max_renders": 2, "repairs_per_stage": 1,
             "numeric_total_request_limit": None, "teacher_answers_forbidden": True}
    auth_path = artifacts / "authorization.json"
    write_json(auth_path, grant)
    data = deepcopy(baseline)
    data["artifacts"][trial.POLICY] = [{"path": str(auth_path), "sha256": json_sha(grant)}]
    write_json(root / "library_state.json", data)
    return SimpleNamespace(root=root, grant=grant, snapshot=snapshot, baseline=baseline,
                           parent=parent, reference=reference, knowledge=knowledge, marker=marker, journal=journal)


def register(f, state, name="vss_observe", *, scope=None, media=None, prompt="generic skill task", image=False):
    media = media or f.parent
    scope = scope or {"kind": "continuous_window", "source_sha256": f.grant["parent"]["sha256"],
                      "source_start_s": 0.0, "source_end_s": 77.366667}
    tool, field = ("analyze_image", "image_source") if image else ("analyze_video", "video_source")
    request = {"provider": "official_vision_mcp_in_codex", "tool": tool,
               "arguments": {field: str(media), "prompt": prompt},
               "media_sha256": sha256_file(media), "observation_scope": deepcopy(scope)}
    descriptor = {"stage": name.removesuffix("_repair"), "tool": tool, "media_path": str(media),
                  "media_sha256": request["media_sha256"], "scope": deepcopy(scope), "purpose": "synthetic evidence"}
    state.set_artifact("vss_input_" + name.removesuffix("_repair"), descriptor)
    return request


def complete(state, call, value=None):
    value = {"observed": "synthetic fact"} if value is None else value
    response = {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
    state.complete_call(call, response)
    write_json(state.output / "calls" / call["id"] / "parsed.json", value)
    return response


def test_independent_parent_appends_global_count_preserving_250_history(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    req = register(f, state)
    call, folder = state.begin_call("vss_observe", req)
    assert call["id"] == "glm_251_vss_observe"
    assert folder.is_dir()
    complete(state, call)
    auth = trial.get_auth(f.root, force=True)
    data = read(state.path)
    assert auth["baseline_request_count"] == 250
    assert data["request_count"] == 251 and data["max_requests"] == 80
    assert data["calls"][:250] == f.baseline["calls"]
    assert data["artifacts"]["old_evidence"] == f.baseline["artifacts"]["old_evidence"]
    assert state.usage()["max_requests"] == float("inf")


@pytest.mark.parametrize("kind", ["continuous_window", "complete_file", "sparse_contact_sheet"])
def test_full_reference_unknown_reencoding_or_kind_change_is_blocked(prepared, kind):
    f = prepared
    state = trial.VisualStoryState(f.root)
    media = f.root / "new_reference_encoding.bin"
    media.write_bytes(b"different encoding of same lost full reference")
    scope = {"kind": kind, "source_sha256": f.grant["reference"]["sha256"],
             "source_start_s": 0.0, "source_end_s": 21.933333}
    req = register(f, state, scope=scope, media=media, image=kind == "sparse_contact_sheet")
    with pytest.raises(LibraryStopped, match="unknown_input_no_replay|full_reference_unknown"):
        state.begin_call("vss_observe", req)
    assert read(state.path)["request_count"] == 250


def test_unknown_old_media_bytes_rejected_with_new_prompt_and_new_scope(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    unknown = f.grant["unknown_inputs"][0]
    old = read(f.root / "calls" / unknown["call_id"] / "request.json")
    req = register(f, state, media=Path(old["arguments"]["image_source"]), prompt="new words")
    with pytest.raises(LibraryStopped, match="unknown_input_no_replay"):
        state.begin_call("vss_observe", req)


@pytest.mark.parametrize("kind", ["continuous_window", "complete_file"])
@pytest.mark.parametrize("rounding", [0.0, 0.0000005])
def test_unknown_nonreference_continuous_kind_and_endpoint_rounding_blocked(prepared, kind, rounding):
    f = prepared
    state = trial.VisualStoryState(f.root)
    media = f.root / "new_encoding_of_unknown34.bin"
    media.write_bytes(b"new bytes cannot replay unknown 166")
    scope = deepcopy(f.grant["unknown_inputs"][2]["scope"])
    scope.update(kind=kind, source_start_s=rounding, source_end_s=34.0 + rounding)
    req = register(f, state, scope=scope, media=media)
    with pytest.raises(LibraryStopped, match="unknown_input_no_replay"):
        state.begin_call("vss_observe", req)
    assert read(state.path)["request_count"] == 250


@pytest.mark.parametrize("mutation", ["scope", "hash", "path", "tool", "media_bytes", "descriptor_hash"])
def test_descriptor_and_actual_media_binding_reject_changes(prepared, mutation):
    f = prepared
    state = trial.VisualStoryState(f.root)
    req = register(f, state)
    if mutation == "scope":
        req["observation_scope"]["source_end_s"] = 70.0
    elif mutation == "hash":
        req["media_sha256"] = "a" * 64
    elif mutation == "path":
        other = f.root / "same_bytes_other_path.mp4"
        other.write_bytes(f.parent.read_bytes())
        req["arguments"]["video_source"] = str(other)
    elif mutation == "tool":
        req["tool"] = "analyze_image"
    elif mutation == "media_bytes":
        f.parent.write_bytes(b"changed actual input")
    else:
        data = read(state.path)
        data["artifacts"]["vss_input_vss_observe"][0]["sha256"] = "f" * 64
        write_json(state.path, data)
    with pytest.raises(LibraryStopped, match="registered_input_changed|actual_media_changed"):
        state.begin_call("vss_observe", req)
    assert read(state.path)["request_count"] == 250


def test_one_repair_bound_to_original_and_no_third_paid_attempt(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    req = register(f, state)
    original, _ = state.begin_call("vss_observe", req)
    complete(state, original)
    repaired = deepcopy(req)
    repaired["arguments"]["prompt"] = "one format repair"
    with pytest.raises(LibraryStopped, match="repair_parent_required"):
        state.begin_call("vss_observe_repair", repaired)
    repair, _ = state.begin_call("vss_observe_repair", repaired, repair_of=original)
    complete(state, repair)
    with pytest.raises(LibraryStopped, match="stage_already_recorded_reuse_cache"):
        state.begin_call("vss_observe_repair", {**repaired, "extra": "third attempt"}, repair_of=original)
    assert read(state.path)["request_count"] == 252
    assert read(state.path)["calls"][-1]["repair_of"] == original["id"]


def test_repair_cannot_switch_input_or_original_stage_become_repair(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    req = register(f, state)
    original, _ = state.begin_call("vss_observe", req)
    complete(state, original)
    changed = deepcopy(req)
    changed["observation_scope"]["source_end_s"] = 50.0
    with pytest.raises(LibraryStopped, match="registered_input_changed"):
        state.begin_call("vss_observe_repair", changed, repair_of=original)
    other = register(f, state, "vss_plan", prompt="next original stage")
    with pytest.raises(LibraryStopped, match="original_cannot_be_repair"):
        state.begin_call("vss_plan", other, repair_of=original)


@pytest.mark.parametrize("status", ["submitted", "uncertain"])
def test_new_pending_or_unknown_stops_all_new_submission(prepared, status):
    f = prepared
    state = trial.VisualStoryState(f.root)
    first, _ = state.begin_call("vss_observe", register(f, state))
    if status == "uncertain":
        state.fail_call(first, "synthetic transport outcome unknown")
    req = register(f, state, "vss_inspect_0", prompt="independent next region")
    with pytest.raises(LibraryStopped, match="new_unknown_or_pending_no_replay"):
        state.begin_call("vss_inspect_0", req)
    assert read(state.path)["request_count"] == 251


@pytest.mark.parametrize("operation", ["complete", "fail", "reconcile", "reclassify"])
def test_historical_call_mutation_entrypoints_are_read_only(prepared, operation):
    f = prepared
    state = trial.VisualStoryState(f.root)
    old = state.data["calls"][130]
    before = state.path.read_bytes()
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        if operation == "complete":
            state.complete_call(old, {})
        elif operation == "fail":
            state.fail_call(old, "cannot relabel old history")
        elif operation == "reconcile":
            state.reconcile_received(old, {}, evidence={})
        else:
            state.reclassify_uncertain(old, evidence=[])
    assert state.path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["old_call", "old_artifact", "old_bytes", "knowledge", "journal"])
def test_history_prefix_and_protected_bytes_cannot_change(prepared, mutation):
    f = prepared
    state = trial.VisualStoryState(f.root)
    if mutation in {"old_call", "old_artifact"}:
        data = read(state.path)
        if mutation == "old_call":
            data["calls"][0]["name"] = "changed old call"
        else:
            data["artifacts"]["old_evidence"] = []
        write_json(state.path, data)
    elif mutation == "old_bytes":
        f.marker.write_bytes(b"modified evidence")
    elif mutation == "knowledge":
        f.knowledge.write_bytes(b"movie answers injected")
    else:
        f.journal.write_bytes(b"overwritten old journal")
    with pytest.raises(LibraryStopped, match="historical_ledger_changed|historical_artifact_changed|protected_bytes_changed|journal"):
        trial.get_auth(f.root, force=True)


def test_append_journal_allowed_and_existing_artifacts_immutable(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    with f.journal.open("ab") as handle:
        handle.write(b'{"new":"legitimate appended event"}\n')
    trial.get_auth(f.root, force=True)
    payload = {"actual": "bound immutable artifact"}
    artifact = state.set_artifact("vss_result_note", payload)
    before = state.path.read_bytes()
    assert state.set_artifact("vss_result_note", payload) == artifact
    assert state.path.read_bytes() == before
    with pytest.raises(LibraryStopped, match="immutable_trial_artifact_changed"):
        state.set_artifact("vss_result_note", {"actual": "rewrite"})
    with pytest.raises(LibraryStopped, match="new_artifacts_only"):
        state.set_artifact("old_evidence", payload)


def test_artifact_cache_record_hash_is_checked(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    payload = {"actual": "immutable"}
    state.set_artifact("vss_result_note", payload)
    data = read(state.path)
    data["artifacts"]["vss_result_note"][0]["sha256"] = "a" * 64
    write_json(state.path, data)
    with pytest.raises(LibraryStopped, match="immutable_trial_artifact_changed"):
        state.set_artifact("vss_result_note", payload)


@pytest.mark.parametrize("mutation", ["request", "response", "parsed"])
def test_received_cache_bytes_are_immutable(prepared, mutation):
    f = prepared
    state = trial.VisualStoryState(f.root)
    call, folder = state.begin_call("vss_observe", register(f, state))
    complete(state, call)
    path = folder / (mutation + ".json")
    value = read(path)
    if mutation == "request":
        value["arguments"]["prompt"] = "modified prompt"
    elif mutation == "response":
        value["result"]["content"][0]["text"] = '{"observed":"rewritten fact"}'
    else:
        value["observed"] = "rewritten parsed cache"
    write_json(path, value)
    with pytest.raises(LibraryStopped, match="new_request_changed|new_response_changed|new_parsed_cache_changed"):
        trial.get_auth(f.root, force=True)


def test_same_stage_changed_prompt_or_finished_trial_cannot_restart(prepared):
    f = prepared
    state = trial.VisualStoryState(f.root)
    req = register(f, state)
    call, _ = state.begin_call("vss_observe", req)
    complete(state, call)
    changed = deepcopy(req)
    changed["arguments"]["prompt"] = "same stage again is not progress"
    with pytest.raises(LibraryStopped, match="stage_already_recorded_reuse_cache"):
        state.begin_call("vss_observe", changed)
    req = register(f, state, "vss_plan", prompt="next stage")
    write_json(Path(f.grant["execution_directory"]) / "result.json", {"status": "stopped"})
    with pytest.raises(LibraryStopped, match="trial_finished_no_new_stage"):
        state.begin_call("vss_plan", req)
    assert read(state.path)["request_count"] == 251
