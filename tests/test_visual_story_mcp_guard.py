"""New skill-trial dispatch proofs using local files only, never a model POST."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from omni_story.library.state import json_sha, write_json

GUARD = Path(__file__).resolve().parents[1] / "omni_story/library/mcp_visual_story_guard.mjs"
POLICY = "visual_story_skill_trial_v1"


def byte_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, replay_scope=False, replay_kind=False):
    root = tmp_path / "task"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    old_request = {"tool": "analyze_video", "arguments": {
        "video_source": str(artifacts / "old.mp4"), "prompt": "old unknown"},
        "media_sha256": "a" * 64, "observation_scope": {
            "kind": "continuous_window", "source_sha256": "1" * 64,
            "source_start_s": 0.0, "source_end_s": 21.933333}}
    calls = [{"id": f"glm_{i:03d}_old", "name": f"old_{i}", "status": "received"}
             for i in range(1, 251)]
    old_id = "glm_166_old"
    calls[165].update(status="uncertain", request_sha256=json_sha(old_request))
    write_json(root / "calls" / old_id / "request.json", old_request)
    old_queue = root / "mcp_queue" / (old_id + ".request.json")
    write_json(old_queue, {"job_id": old_id, "tool": old_request["tool"], "arguments": old_request["arguments"]})
    (old_queue.parent / (old_id + ".started.json")).write_text("immutable started", encoding="utf-8")
    old_marker = artifacts / "old.txt"
    old_marker.write_text("protected old evidence", encoding="utf-8")
    knowledge = artifacts / "SKILL.md"
    knowledge.write_text("generic editing knowledge", encoding="utf-8")
    baseline = {"task_id": "synthetic", "policy_version": "old", "max_requests": 80,
                "request_count": 250, "calls": calls, "input_lock": {"fixed": True},
                "artifacts": {"old": [{"path": str(old_marker)}]}}
    baseline_path = artifacts / "baseline.json"
    write_json(baseline_path, baseline)
    policy = {"policy": POLICY, "task_id": "synthetic", "input_lock_sha256": json_sha(baseline["input_lock"]),
              "baseline_request_count": 250, "baseline_state_path": str(baseline_path),
              "baseline_state_sha256": byte_sha(baseline_path), "prefix_calls_sha256": json_sha(calls),
              "execution_directory": str(artifacts / POLICY), "unknown_inputs": [{
                  "call_id": old_id, "request_sha256": json_sha(old_request),
                  "media_sha256": old_request["media_sha256"], "scope": old_request["observation_scope"]}],
              "protected_files": [{"path": str(old_marker), "sha256": byte_sha(old_marker)}],
              "journal_prefixes": [], "knowledge_files": [{"path": str(knowledge), "sha256": byte_sha(knowledge)}],
              "max_concurrency": 1, "max_renders": 2, "repairs_per_stage": 1,
              "numeric_total_request_limit": None, "teacher_answers_forbidden": True,
              "user_instruction": "let GLM try the generic skill"}
    auth = artifacts / "authorization.json"
    write_json(auth, policy)
    media = artifacts / "new.mp4"
    media.write_bytes(b"different locally bound media; no model sees this synthetic test")
    new_scope = dict(old_request["observation_scope"]) if replay_scope else {
        "kind": "continuous_window", "source_sha256": "2" * 64,
        "source_start_s": 0.0, "source_end_s": 77.366667}
    if replay_kind:
        new_scope["kind"] = "complete_file"
        new_scope["source_end_s"] += 0.0000003
    request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video",
               "arguments": {"video_source": str(media), "prompt": "generic skill"},
               "media_sha256": byte_sha(media), "observation_scope": new_scope}
    new_id = "glm_251_vss_observe"
    write_json(root / "calls" / new_id / "request.json", request)
    write_json(root / "mcp_queue" / (new_id + ".request.json"), {
        "job_id": new_id, "tool": request["tool"], "arguments": request["arguments"]})
    descriptor = artifacts / "input.json"
    write_json(descriptor, {"stage": "vss_observe", "tool": request["tool"], "media_path": str(media),
                            "media_sha256": byte_sha(media), "scope": new_scope, "purpose": "navigation"})
    state = json.loads(json.dumps(baseline))
    state["calls"].append({"id": new_id, "name": "vss_observe", "status": "submitted",
                           "request_sha256": json_sha(request), "repair_of": None})
    state["request_count"] = 251
    state["artifacts"][POLICY] = [{"path": str(auth), "sha256": json_sha(policy)}]
    state["artifacts"]["vss_input_vss_observe"] = [{"path": str(descriptor), "sha256": json_sha(json.loads(descriptor.read_text()))}]
    write_json(root / "library_state.json", state)
    env = dict(os.environ, OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE=str(auth),
               OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256=byte_sha(auth))
    return SimpleNamespace(root=root, state=state, env=env, media=media, old_queue=old_queue,
                           new_id=new_id, descriptor=descriptor, knowledge=knowledge)


def run_node(f, body):
    code = f"import * as g from {json.dumps(GUARD.as_uri())};\n" + body
    return subprocess.run(["node", "--input-type=module", "-e", code], env=f.env,
                          capture_output=True, text=True, timeout=20)


@pytest.mark.skipif(not shutil.which("node"), reason="Guard checks require Node.js")
def test_new_bound_job_dispatch_and_old_frozen_job_preserve_bytes(tmp_path):
    f = fixture(tmp_path)
    original = f.old_queue.read_bytes()
    r = run_node(f, f"""
const root={json.dumps(str(f.root))};
if(g.visualStoryRequestLimit(root,{{job_id:{json.dumps(f.new_id)}}})!==Infinity)throw Error('not authorized');
const skip=g.visualStoryFrozenJob(root);
if(!skip({json.dumps(str(f.old_queue))}))throw Error('old queue not frozen');
if(skip({json.dumps(str(f.root / 'mcp_queue' / (f.new_id + '.request.json')))}))throw Error('new queue frozen');
console.log('local guard proofs passed');
""")
    assert r.returncode == 0, r.stderr
    assert f.old_queue.read_bytes() == original
    assert not f.old_queue.with_name(f.old_queue.name.replace(".request", ".response")).exists()


@pytest.mark.skipif(not shutil.which("node"), reason="Guard checks require Node.js")
@pytest.mark.parametrize("mutation,expected", [
    ("unknown_scope", "unknown_input_replay"), ("unknown_alias", "unknown_input_replay"),
    ("media", "bound_input_required"),
    ("knowledge", "protected_bytes_changed"), ("prefix", "policy_or_prefix_changed"),
    ("descriptor", "bound_input_required")])
def test_new_guard_rejects_replay_and_binding_changes(tmp_path, mutation, expected):
    f = fixture(tmp_path, replay_scope=mutation in {"unknown_scope", "unknown_alias"}, replay_kind=mutation == "unknown_alias")
    if mutation == "media":
        f.media.write_bytes(b"changed")
    if mutation == "knowledge":
        f.knowledge.write_text("changed generic skill", encoding="utf-8")
    if mutation == "prefix":
        f.state["calls"][0]["name"] = "changed_old_call"
        write_json(f.root / "library_state.json", f.state)
    if mutation == "descriptor":
        d = json.loads(f.descriptor.read_text())
        d["scope"]["source_end_s"] = 20.0
        write_json(f.descriptor, d)
        f.state["artifacts"]["vss_input_vss_observe"][0]["sha256"] = json_sha(d)
        write_json(f.root / "library_state.json", f.state)
    r = run_node(f, f"g.visualStoryRequestLimit({json.dumps(str(f.root))},{{job_id:{json.dumps(f.new_id)}}});")
    assert r.returncode != 0
    assert expected in r.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Guard checks require Node.js")
def test_single_lane_native_guard_remembers_parallel_post_hashes(tmp_path):
    f = fixture(tmp_path)
    package = tmp_path / "package"
    undici = package / "node_modules/undici"
    undici.mkdir(parents=True)
    (undici / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (undici / "index.js").write_text("export class Agent { constructor() {} }", encoding="utf-8")
    body = '{"model":"glm-5.3-flash","messages":["synthetic native body"]}'
    old_post = {"type": "request", "seq": 1, "job_id": "glm_166_old",
                "hash": hashlib.sha256(body.encode()).hexdigest()}
    journal = f.root / "mcp_http_sf_3.jsonl"
    journal.write_text(json.dumps(old_post) + "\n", encoding="utf-8")
    original = journal.read_bytes()
    write_json(f.root / "mcp_current.json", {"job_id": f.new_id})
    f.env["OMNI_LIBRARY_MCP_ROOT"] = str(f.root)
    f.env["OMNI_LIBRARY_MCP_PACKAGE_ROOT"] = str(package)
    native_guard = GUARD.with_name("mcp_guard.mjs")
    code = f"""
let actualFetches=0;
globalThis.fetch=async()=>{{actualFetches++;return new Response('{{}}');}};
await import({json.dumps(native_guard.as_uri())});
try {{
  await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions',{{method:'POST',body:{json.dumps(body)}}});
  throw Error('duplicate incorrectly allowed');
}} catch(error) {{
  if(!String(error).includes('library_mcp_retry_or_budget_blocked'))throw error;
}}
if(actualFetches!==0)throw Error('duplicate reached mocked HTTP');
console.log('parallel historical hash blocked without network');
"""
    checked = subprocess.run(["node", "--input-type=module", "-e", code], env=f.env,
                             capture_output=True, text=True, timeout=20)
    assert checked.returncode == 0, checked.stderr
    assert journal.read_bytes() == original
    assert not (f.root / "mcp_http.jsonl").exists()


@pytest.mark.parametrize("reject", [False, True])
def test_launcher_prefers_skill_trial_without_enabling_frozen_microclip(tmp_path, monkeypatch, reject):
    from omni_story.library import mcp_launch, microclip_v2_state
    root = tmp_path / "task"
    write_json(root / "library_state.json", {"artifacts": {POLICY: [{}], "microclip_slot_finecut_v2": [{}]}})
    marker = root / "mcp_stop"
    marker.write_text("previous stop", encoding="utf-8")
    module = ModuleType("omni_story.library.visual_story_trial_state")
    def state(output):
        if reject:
            raise ValueError("synthetic_invalid_skill_trial")
        return SimpleNamespace()
    module.VisualStoryState = state
    module.get_auth = lambda output: {"authorization_path": "new-auth", "authorization_sha256": "new-sha"}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(microclip_v2_state, "SlotMicroclipState", lambda output: pytest.fail("old microclip resumed"))
    monkeypatch.setattr(sys, "argv", ["mcp_launch", "--output", str(root), "--package-root", str(tmp_path / "package")])
    monkeypatch.setenv("Z_AI_API_KEY", "synthetic-secret")
    for key in ("OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE", "OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256",
                "OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE", "OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256"):
        monkeypatch.setenv(key, "poisoned-inherited")
    launches = []
    monkeypatch.setattr(mcp_launch.subprocess, "call", lambda command, env: launches.append((command, env)) or 17)
    if reject:
        with pytest.raises(ValueError, match="synthetic_invalid_skill_trial"):
            mcp_launch.main()
        assert not launches and marker.read_text() == "previous stop"
    else:
        assert mcp_launch.main() == 17 and not marker.exists()
        command, env = launches[0]
        assert env["OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE"] == "new-auth"
        assert env["OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256"] == "new-sha"
        assert env["OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY"] == "1"
        assert "OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE" not in env
        assert "OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256" not in env
        assert "OMNI_LIBRARY_MAX_REQUESTS" not in env
        assert "synthetic-secret" not in " ".join(command)
