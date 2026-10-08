"""Standalone Node guard/launch tests; no MCP, HTTP, key or real run is used."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from omni_story.library.media import sha256_file
from omni_story.library.state import json_sha
from omni_story.library.slot_finecut_budget import ALLOWED_STAGE_PATTERN, POLICY

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Guard checks require Node")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def scope(sha="c" * 64, start=1, end=2, kind="continuous_window"):
    return {"kind": kind, "source_sha256": sha, "source_start_s": start, "source_end_s": end}


def request(media, observation, *, tool="analyze_image"):
    return {"provider": "official_vision_mcp_in_codex", "tool": tool,
            "arguments": {"image_source" if tool == "analyze_image" else "video_source": str(media),
                          "prompt": "Synthetic standalone guard input"},
            "media_sha256": sha256_file(media), "observation_scope": observation}


def invoke(root, env, job):
    module = Path("omni_story/library/mcp_slot_finecut_guard.mjs").resolve().as_uri()
    code = """const {slotRequestLimit}=await import(process.argv[1]);
delete process.env.OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE;
delete process.env.OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256;
Object.assign(process.env,JSON.parse(process.argv[4]));
try { const r=slotRequestLimit(process.argv[2],JSON.parse(process.argv[3]));
process.stdout.write(r===Infinity?'Infinity':JSON.stringify(r)); }
catch(e) { process.stderr.write(String(e.message)); process.exitCode=3; }"""
    return subprocess.run(["node", "--input-type=module", "-e", code, module, str(root),
                           json.dumps(job), json.dumps(env)], text=True, capture_output=True, timeout=30)


@pytest.fixture
def granted(tmp_path):
    root = tmp_path / "same_run"
    root.mkdir()
    old4, old131, new_media = [root / name for name in ("old4.jpg", "old131.mp4", "new.jpg")]
    for path, body in ((old4, b"old unknown sparse input"), (old131, b"old unknown reference input"),
                       (new_media, b"new independent synthetic image input")):
        path.write_bytes(body)
    original_inputs = [
        ("glm_004_coarse_978d5360_01", "coarse_978d5360_01", request(old4, scope("a" * 64, 600, 1200,
                                                                                 "sparse_contact_sheet"))),
        ("glm_131_active_10_draft", "active_10_draft", request(old131, scope("b" * 64, 0, 21),
                                                            tool="analyze_video"))]
    baseline = []
    for ident, name, body in original_inputs:
        write(root / "calls" / ident / "request.json", body)
        baseline.append({"id": ident, "name": name, "status": "uncertain",
                         "request_sha256": json_sha(body), "repair_of": None})
    baseline_path = root / "artifacts" / POLICY / "fixture_preparation" / "baseline_calls.json"
    write(baseline_path, baseline)
    preparation_path, methods_path, knowledge_path = [baseline_path.parent / name for name in (
        "preparation.json", "methods.json", "SLOT_FINECUT.md")]
    write(preparation_path, {"synthetic": "CPU fixture; no authorization executed"})
    write(methods_path, {"methods": []})
    knowledge_path.write_text("Generic synthetic editing knowledge", encoding="utf-8")
    protected = root / "old_metadata.json"
    write(protected, {"old": "immutable"})
    current = request(new_media, scope())
    ident = "glm_132_sf_0_outline"
    call = {"id": ident, "name": "sf_0_outline", "status": "submitted",
            "request_sha256": json_sha(current), "repair_of": None}
    write(root / "calls" / ident / "request.json", current)
    input_lock = {"reference_sha256": "b" * 64, "library_sources": [{"source_id": "fixture", "sha256": "c" * 64}]}
    grant = {"policy": POLICY, "task_id": "synthetic_task", "original_output": str(root),
             "request_limit_policy": "progress_guard_no_numeric_request_cap_v1", "base_request_limit": 80,
             "input_lock_sha256": json_sha(input_lock), "baseline_calls_path": str(baseline_path),
             "baseline_calls_sha256": sha256_file(baseline_path), "baseline_request_count": len(baseline),
             "prefix_calls_sha256": json_sha(baseline), "allowed_stage_pattern": ALLOWED_STAGE_PATTERN,
             "preparation_path": str(preparation_path), "preparation_sha256": sha256_file(preparation_path),
             "reference_methods_path": str(methods_path), "reference_methods_sha256": sha256_file(methods_path),
             "knowledge_path": str(knowledge_path), "knowledge_sha256": sha256_file(knowledge_path),
             "protected_files": [{"path": str(protected), "sha256": sha256_file(protected)}],
             "admitted_unknown_call_ids": [row["id"] for row in baseline],
             "unknown_inputs": [{"call_id": ident, "request_sha256": json_sha(body),
                                 "media_sha256": body["media_sha256"], "scope": body["observation_scope"]}
                                for ident, _, body in original_inputs],
             "exhausted_source_inputs": [{"scope": scope(start=5, end=7), "call_ids": ["fixture_exhausted"]}],
             "parent_rounds": [0, 3], "max_slices_per_parent": 32, "renders_per_parent": 1,
             "repairs_per_stage": 1, "new_unique_windows": 0, "automatic_round_loops": False,
             "validated_on_resume": True, "additional_requests": None, "effective_request_limit": None}
    authorization_path = baseline_path.parent / "authorization.json"
    write(authorization_path, grant)
    state = {"task_id": "synthetic_task", "max_requests": 80, "input_lock": input_lock,
             "calls": baseline + [call], "request_count": len(baseline) + 1,
             "artifacts": {POLICY: [{"path": str(authorization_path), "sha256": json_sha(grant)}]}}
    write(root / "library_state.json", state)
    return {"root": root, "state": state, "grant": grant, "call": call, "request": current,
            "job": {"job_id": call["id"], "tool": current["tool"], "arguments": current["arguments"]},
            "env": {"OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE": str(authorization_path),
                    "OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256": sha256_file(authorization_path)}}


def save_current(fixture):
    call = fixture["state"]["calls"][-1]
    fixture["request"]["tool"] = fixture["request"].get("tool", "analyze_image")
    call["request_sha256"] = json_sha(fixture["request"])
    write(fixture["root"] / "calls" / call["id"] / "request.json", fixture["request"])
    fixture["state"]["request_count"] = len(fixture["state"]["calls"])
    write(fixture["root"] / "library_state.json", fixture["state"])
    fixture["job"] = {"job_id": call["id"], "tool": fixture["request"]["tool"],
                      "arguments": fixture["request"]["arguments"]}


def result(fixture):
    return invoke(fixture["root"], fixture["env"], fixture["job"])


def save_grant(fixture):
    path = Path(fixture["env"]["OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE"])
    write(path, fixture["grant"])
    fixture["env"]["OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256"] = sha256_file(path)
    fixture["state"]["artifacts"][POLICY][0]["sha256"] = json_sha(fixture["grant"])
    write(fixture["root"] / "library_state.json", fixture["state"])


def test_no_slot_environment_returns_null(tmp_path):
    checked = invoke(tmp_path, {}, {"job_id": "does_not_read_missing_state"})
    assert checked.returncode == 0 and checked.stdout == "null"


def test_valid_bound_slot_grant_has_no_numeric_call_cap(granted):
    before = (granted["root"] / "library_state.json").read_bytes()
    checked = result(granted)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Infinity"
    assert (granted["root"] / "library_state.json").read_bytes() == before


@pytest.mark.parametrize("replay", ["scope", "media"])
def test_unknown_input_cannot_be_replayed_under_new_stage(granted, replay):
    old = granted["grant"]["unknown_inputs"][0]
    if replay == "scope":
        granted["request"]["observation_scope"] = deepcopy(old["scope"])
    else:
        granted["request"]["media_sha256"] = old["media_sha256"]
        granted["request"]["arguments"]["image_source"] = str(granted["root"] / "old4.jpg")
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "unknown_input_replay" in checked.stderr


@pytest.mark.parametrize("stage", ["semantic_slice_20_0123456789abcdef", "sf_0_facts_0123456789abcdef"])
def test_exhausted_source_scope_remains_blocked(granted, stage):
    granted["state"]["calls"][-1]["name"] = stage
    granted["request"]["observation_scope"] = deepcopy(granted["grant"]["exhausted_source_inputs"][0]["scope"])
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "exhausted" in checked.stderr


def test_legitimate_float_scope_preserves_python_canonical_request_digest(granted):
    granted["request"]["observation_scope"] = scope(start=1.0, end=2.0)
    save_current(granted)
    checked = result(granted)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Infinity"


def test_legitimate_grant_preserves_float_exhausted_scope_digest(granted):
    granted["grant"]["exhausted_source_inputs"][0]["scope"] = scope(start=5.0, end=7.0)
    save_grant(granted)
    checked = result(granted)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Infinity"


def test_unchanged_baseline_float_metadata_keeps_canonical_prefix_hash(granted):
    # Python-authored historical numbers must not lose their numeric lexemes.
    granted["state"]["calls"][0]["usage"] = {"synthetic_elapsed_seconds": 0.0}
    baseline = granted["state"]["calls"][:granted["grant"]["baseline_request_count"]]
    snapshot = Path(granted["grant"]["baseline_calls_path"])
    write(snapshot, baseline)
    granted["grant"]["baseline_calls_sha256"] = sha256_file(snapshot)
    granted["grant"]["prefix_calls_sha256"] = json_sha(baseline)
    save_grant(granted)
    checked = result(granted)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Infinity"


def test_unknown_original_float_scope_digest_remains_unchanged_and_valid(granted):
    """Construct a legitimate original numeric shape, rather than normalizing it."""
    original = granted["state"]["calls"][1]
    path = granted["root"] / "calls" / original["id"] / "request.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["observation_scope"] = scope("b" * 64, start=0.0, end=21.0)
    write(path, body)
    original["request_sha256"] = json_sha(body)
    # Authorization's normalized navigation copy and old raw request are separate.
    granted["grant"]["unknown_inputs"][1]["request_sha256"] = json_sha(body)
    baseline = granted["state"]["calls"][:granted["grant"]["baseline_request_count"]]
    snapshot = Path(granted["grant"]["baseline_calls_path"])
    write(snapshot, baseline)
    granted["grant"]["baseline_calls_sha256"] = sha256_file(snapshot)
    granted["grant"]["prefix_calls_sha256"] = json_sha(baseline)
    save_grant(granted)
    checked = result(granted)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Infinity"
    assert body["observation_scope"]["source_start_s"] == 0.0
    assert '"source_start_s": 0.0' in path.read_text(encoding="utf-8")


def test_negative_source_time_is_not_an_independent_valid_scope(granted):
    granted["request"]["observation_scope"] = scope(start=-1, end=1)
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "scope" in checked.stderr


@pytest.mark.parametrize("target", ["authorization", "prefix", "snapshot", "knowledge", "protected"])
def test_tampered_authorization_or_original_evidence_is_rejected(granted, target):
    if target == "prefix":
        granted["state"]["calls"][0]["usage"] = {"unexpected": 1}
        write(granted["root"] / "library_state.json", granted["state"])
    else:
        paths = {"authorization": granted["env"]["OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE"],
                 "snapshot": granted["grant"]["baseline_calls_path"],
                 "knowledge": granted["grant"]["knowledge_path"],
                 "protected": granted["grant"]["protected_files"][0]["path"]}
        path = Path(paths[target])
        path.write_bytes(path.read_bytes() + b" ")
    checked = result(granted)
    assert checked.returncode != 0 and "library_mcp_slot_" in checked.stderr


def test_foreign_stage_cannot_use_the_new_grant(granted):
    granted["state"]["calls"][-1]["name"] = "active_11_draft"
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "budget_or_stage_blocked" in checked.stderr


def test_duplicate_original_stage_is_rejected(granted):
    old = deepcopy(granted["state"]["calls"][-1])
    old.update(id="previous_sf_0_outline", status="received")
    granted["state"]["calls"].insert(-1, old)
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "stage_repetition" in checked.stderr


def add_repair(granted, *, second=False):
    parent = granted["state"]["calls"][-1]
    parent["status"] = "received"
    if second:
        first = deepcopy(parent)
        first.update(id="first_repair", name=parent["name"] + "_repair", repair_of=parent["id"])
        granted["state"]["calls"].append(first)
    current = deepcopy(parent)
    current.update(id="current_repair", name=parent["name"] + "_repair", repair_of=parent["id"], status="submitted")
    granted["state"]["calls"].append(current)
    save_current(granted)


def test_one_known_format_repair_is_allowed(granted):
    add_repair(granted)
    checked = result(granted)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "Infinity"


def test_second_repair_is_rejected(granted):
    add_repair(granted, second=True)
    checked = result(granted)
    assert checked.returncode != 0 and "repair_parent_invalid" in checked.stderr


def test_new_unsettled_call_cannot_be_ignored(granted):
    prior = deepcopy(granted["state"]["calls"][-1])
    prior.update(id="new_unknown", name="sf_3_outline", status="uncertain")
    granted["state"]["calls"].insert(-1, prior)
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "new_unsettled_call" in checked.stderr


def parallel_granted(granted):
    """Append synthetic new permission; preserve both known original failures."""
    granted["grant"]["preparation_id"] = "slot_finecut_preparation_" + "e" * 64
    save_grant(granted)
    original = granted["state"]["calls"][-1]
    original["status"] = "received"
    write(granted["root"] / "calls" / original["id"] / "response.json", {"broken": "original"})
    write(granted["root"] / "calls" / original["id"] / "protocol_failure.json", {"attempt": 0})
    repair = deepcopy(original)
    repair.update(id="glm_133_sf_0_outline_repair", name="sf_0_outline_repair", repair_of=original["id"])
    granted["state"]["calls"].append(repair)
    body = deepcopy(granted["request"])
    body["arguments"]["prompt"] = "sole old repair"
    repair["request_sha256"] = json_sha(body)
    write(granted["root"] / "calls" / repair["id"] / "request.json", body)
    write(granted["root"] / "calls" / repair["id"] / "response.json", {"broken": "repair"})
    write(granted["root"] / "calls" / repair["id"] / "protocol_failure.json", {"attempt": 1})
    failures = [{"path": str(granted["root"] / "calls" / c["id"] / name),
                 "sha256": sha256_file(granted["root"] / "calls" / c["id"] / name)}
                for c in (original, repair) for name in ("request.json", "response.json", "protocol_failure.json")]
    policy = {"policy": "sf_parallel_execution_v1", "task_id": granted["state"]["task_id"],
              "preparation_id": granted["grant"]["preparation_id"],
              "authorization_record_sha256": granted["state"]["artifacts"][POLICY][0]["sha256"],
              "baseline_request_count": len(granted["state"]["calls"]),
              "prefix_calls_sha256": json_sha(granted["state"]["calls"]),
              "max_concurrent_parents": 2, "parent_rounds": [0, 3], "replacement_outline_stage": "sf_0_outline_v2",
              "failed_call_ids": [original["id"], repair["id"]], "protected_failure_files": failures,
              "prompt_policy": "synthetic root output correction", "user_instruction": "Synthetic explicit parallel permission",
              "prompt_policy_sha256": hashlib.sha256(b"synthetic root output correction").hexdigest()}
    policy_path = granted["root"] / "artifacts/sf_parallel_execution_v1.json"
    write(policy_path, policy)
    granted["state"]["artifacts"]["sf_parallel_execution_v1"] = [{"path": str(policy_path), "sha256": json_sha(policy)}]
    granted["policy"] = policy
    new = deepcopy(original)
    new.update(id="glm_134_sf_0_outline_v2", name="sf_0_outline_v2", status="submitted", repair_of=None)
    granted["state"]["calls"].append(new)
    granted["request"]["arguments"]["prompt"] = "corrected root output shape"
    save_current(granted)
    return granted


def append_other_parent(granted, *, status="submitted"):
    call = {"id": "glm_135_sf_3_outline", "name": "sf_3_outline", "status": status, "repair_of": None}
    granted["state"]["calls"].append(call)
    granted["request"] = deepcopy(granted["request"])
    granted["request"]["arguments"]["prompt"] = "independent parent3"
    granted["request"]["observation_scope"] = scope(sha="d" * 64, start=0, end=4, kind="complete_file")
    save_current(granted)
    return call


def test_parallel_non_global_last_request_and_other_parent_pending_are_allowed(granted):
    parallel_granted(granted)
    first_job = deepcopy(granted["job"])
    append_other_parent(granted)
    for job in (first_job, granted["job"]):
        checked = invoke(granted["root"], granted["env"], job)
        assert checked.returncode == 0, checked.stderr
        assert checked.stdout == "Infinity"


@pytest.mark.parametrize("status", ["uncertain", "failed_known"])
def test_parallel_new_unknown_blocks_other_parent_guard(granted, status):
    parallel_granted(granted)
    first_job = deepcopy(granted["job"])
    append_other_parent(granted, status=status)
    checked = invoke(granted["root"], granted["env"], first_job)
    assert checked.returncode != 0 and "new_unsettled_call" in checked.stderr


def test_parallel_same_parent_second_pending_is_blocked(granted):
    parallel_granted(granted)
    previous = deepcopy(granted["state"]["calls"][-1])
    previous.update(id="glm_135_same_parent", name="sf_0_facts_" + "a" * 16)
    granted["state"]["calls"].append(previous)
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "new_unsettled_call" in checked.stderr


def test_parallel_more_than_two_or_duplicate_other_parent_pending_is_blocked(granted):
    parallel_granted(granted)
    first_job = deepcopy(granted["job"])
    other = append_other_parent(granted)
    duplicate = deepcopy(other)
    duplicate.update(id="glm_136_other_parent", name="sf_3_facts_" + "b" * 16)
    granted["state"]["calls"].append(duplicate)
    save_current(granted)
    checked = invoke(granted["root"], granted["env"], first_job)
    assert checked.returncode != 0 and "new_unsettled_call" in checked.stderr


@pytest.mark.parametrize("same_parent_between", [False, True])
def test_parallel_repair_requires_previous_own_parent_stage(granted, same_parent_between):
    parallel_granted(granted)
    original = granted["state"]["calls"][-1]
    original["status"] = "received"
    original_request = deepcopy(granted["request"])
    append_other_parent(granted)
    if same_parent_between:
        extra = deepcopy(original)
        extra.update(id="intervening_parent0", name="sf_0_facts_" + "a" * 16)
        granted["state"]["calls"].append(extra)
    current = deepcopy(original)
    current.update(id="current_v2_repair", name="sf_0_outline_v2_repair", repair_of=original["id"], status="submitted")
    granted["state"]["calls"].append(current)
    granted["request"] = original_request
    granted["request"]["arguments"]["prompt"] = "sole v2 repair"
    save_current(granted)
    checked = result(granted)
    if same_parent_between:
        assert checked.returncode != 0 and "repair_parent_invalid" in checked.stderr
    else:
        assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize("target", ["policy", "baseline", "failure", "parsed"])
def test_parallel_forward_permission_cannot_change_old_evidence(granted, target):
    parallel_granted(granted)
    policy = granted["policy"]
    if target == "policy":
        path = Path(granted["state"]["artifacts"]["sf_parallel_execution_v1"][0]["path"])
        path.write_bytes(path.read_bytes() + b" changed")
    elif target == "baseline":
        granted["state"]["calls"][granted["grant"]["baseline_request_count"]]["usage"] = {"invented": 1}
        write(granted["root"] / "library_state.json", granted["state"])
    elif target == "failure":
        path = Path(policy["protected_failure_files"][-1]["path"])
        path.write_bytes(path.read_bytes() + b" ")
    else:
        write(granted["root"] / "calls" / policy["failed_call_ids"][0] / "parsed.json", {})
    checked = result(granted)
    assert checked.returncode != 0


def test_v2_outline_without_forward_authorization_is_blocked(granted):
    granted["state"]["calls"][-1]["name"] = "sf_0_outline_v2"
    save_current(granted)
    checked = result(granted)
    assert checked.returncode != 0 and "budget_or_stage_blocked" in checked.stderr


def test_launcher_selects_slot_grant_and_clears_old_goal_environment(tmp_path, monkeypatch):
    """Mock process launch entirely; this does not authorize or start a bridge."""
    from omni_story.library import mcp_launch, slot_finecut_budget, goal_budget
    from omni_story.library.extension_budget import GOAL_AUTHORIZATION
    root = tmp_path / "launch_only"
    root.mkdir()
    write(root / "library_state.json", {"artifacts": {POLICY: [{}], GOAL_AUTHORIZATION: [{}]}})
    package = tmp_path / "unused_package"
    package.mkdir()
    env_names = ["OMNI_LIBRARY_EXTENSION_AUTH_FILE", "OMNI_LIBRARY_EXTENSION_AUTH_SHA256",
                 "OMNI_LIBRARY_REQUEST_LIMIT_POLICY", "OMNI_LIBRARY_MAX_REQUESTS",
                 "OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE", "OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256",
                 "OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE", "OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256"]
    for name in env_names:
        monkeypatch.setenv(name, "stale_synthetic_value")
    monkeypatch.setenv("Z_AI_API_KEY", "synthetic_test_key_never_sent")
    monkeypatch.setattr(sys, "argv", ["mcp_launch", "--output", str(root), "--package-root", str(package)])
    monkeypatch.setattr(slot_finecut_budget, "SlotFinecutState", lambda output: object())
    expected_path = str(root / "authorization_fixture.json")
    monkeypatch.setattr(slot_finecut_budget, "load_authorization", lambda output: {
        "authorization_path": expected_path, "authorization_sha256": "d" * 64})
    monkeypatch.setattr(goal_budget, "stage_state", lambda *args: pytest.fail("Old Goal branch was selected"))
    captured = {}

    def no_launch(command, *, env):
        captured.update(command=command, env=dict(env))
        return 0

    monkeypatch.setattr(mcp_launch.subprocess, "call", no_launch)
    assert mcp_launch.main() == 0
    assert captured["command"][0] == "node"
    assert captured["env"]["OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE"] == expected_path
    assert captured["env"]["OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256"] == "d" * 64
    assert all(name not in captured["env"] for name in env_names if "SLOT_FINECUT" not in name)
