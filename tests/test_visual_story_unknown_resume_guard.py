"""The 265 continuation is append-only; all dispatches here are local mocks."""
import ast
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library.state import json_sha, write_json
from test_visual_story_mcp_guard import byte_sha, fixture, run_node

RESUME = "vss_unknown_265_resume_v1"
LOST = "glm_265_vss_detail_3_0"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Guard checks require Node.js")


def append_call(f, number, name, *, status="received", observation=None, media=None):
    ident = f"glm_{number:03d}_{name}"
    media = media or f.root / "artifacts" / f"media_{number}.bin"
    if not media.exists():
        media.write_bytes(f"independent synthetic media {number}".encode())
    observation = observation or {"kind": "sparse_contact_sheet", "source_sha256": "2" * 64,
                                  "source_start_s": float(number), "source_end_s": float(number + 1)}
    request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_image",
               "arguments": {"image_source": str(media), "prompt": f"generic local query {number}"},
               "media_sha256": byte_sha(media), "observation_scope": observation}
    folder = f.root / "calls" / ident
    write_json(folder / "request.json", request)
    queue = f.root / "mcp_queue" / f"{ident}.request.json"
    write_json(queue, {"job_id": ident, "tool": request["tool"], "arguments": request["arguments"]})
    call = {"id": ident, "name": name, "status": status, "request_sha256": json_sha(request), "repair_of": None}
    if status == "received":
        response = {"result": {"content": [{"type": "text", "text": '{"facts":[]}'}]}}
        write_json(folder / "response.json", response)
        call["response_sha256"] = json_sha(response)
    if status != "submitted":
        write_json(queue.with_name(f"{ident}.started.json"), {"job_id": ident, "submitted_at": "frozen"})
        write_json(queue.with_name(f"{ident}.response.json"), {
            "status": "unknown" if status == "uncertain" else "complete", "original": True})
    descriptor = f.root / "artifacts" / f"input_{number}.json"
    payload = {"stage": name.removesuffix("_repair"), "tool": request["tool"], "media_path": str(media),
               "media_sha256": byte_sha(media), "scope": observation, "purpose": "model-selected local evidence"}
    write_json(descriptor, payload)
    f.state["artifacts"]["vss_input_" + payload["stage"]] = [{"path": str(descriptor), "sha256": json_sha(payload)}]
    f.state["calls"].append(call)
    f.state["request_count"] += 1
    write_json(f.root / "library_state.json", f.state)
    return ident, request


def resume_fixture(tmp_path, *, with_resume=True, replay_kind=False, replay_media=False):
    f = fixture(tmp_path)
    f.state["continuation_policy"] = "independent_media_no_unknown_replay_v1"
    first = f.state["calls"][-1]
    response = {"result": {"content": [{"type": "text", "text": '{"facts":[]}'}]}}
    write_json(f.root / "calls" / first["id"] / "response.json", response)
    first.update(status="received", response_sha256=json_sha(response))
    for number in range(252, 265):
        append_call(f, number, f"vss_inspect_{number - 252}")
    lost_scope = {"kind": "sparse_contact_sheet", "source_sha256": "2" * 64,
                  "source_start_s": 54.0, "source_end_s": 55.5}
    _, lost_request = append_call(f, 265, "vss_detail_3_0", status="uncertain", observation=lost_scope)
    journal = f.root / "mcp_http.jsonl"
    journal.write_bytes(b'{"type":"unknown_result","job_id":"glm_265_vss_detail_3_0","seq":190}\n')
    snapshot = f.root / "artifacts" / "baseline265.json"
    write_json(snapshot, f.state)
    original_auth = Path(f.env["OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE"])
    protected = [p for p in f.root.rglob("*") if p.is_file() and p != f.root / "library_state.json" and p != journal]
    resume = {"policy": RESUME, "task_id": f.state["task_id"],
              "authorization_sha256": json_sha(json.loads(original_auth.read_text())),
              "input_lock_sha256": json_sha(f.state["input_lock"]),
              "baseline_request_count": 265, "baseline_state_path": str(snapshot),
              "baseline_state_sha256": byte_sha(snapshot), "prefix_calls_sha256": json_sha(f.state["calls"]),
              "baseline_call_files": {c["id"]: sorted(str(p.relative_to(f.root / "calls" / c["id"]))
                                                      for p in (f.root / "calls" / c["id"]).rglob("*") if p.is_file())
                                      for c in f.state["calls"]},
              "protected_files": [{"path": str(p), "sha256": byte_sha(p)} for p in protected],
              "journal_prefixes": [{"path": str(journal), "bytes": journal.stat().st_size, "sha256": byte_sha(journal)}],
              "lost_call_id": LOST, "lost_request_sha256": json_sha(lost_request),
              "lost_media_sha256": lost_request["media_sha256"], "lost_scope": lost_scope,
              "skip_stages": ["vss_detail_3_0"], "new_renders": 0,
              "goal_resumed": False, "teacher_answers_forbidden": True,
              "progress_policy": "skip_one_lost_page_continue_original_trial_no_POST_replay_no_new_round",
              "user_instruction": "continue with independent neighbouring evidence"}
    f.resume_file = f.root / "artifacts" / "resume.json"
    f.resume = resume
    if with_resume:
        write_json(f.resume_file, resume)
        f.state["artifacts"][RESUME] = [{"path": str(f.resume_file), "sha256": json_sha(resume)}]
    observation = {"kind": "sparse_contact_sheet", "source_sha256": "2" * 64,
                   "source_start_s": 55.5, "source_end_s": 57.0}
    if replay_kind:
        observation = dict(lost_scope, kind="continuous_window", source_end_s=55.5000003)
    media = Path(lost_request["arguments"]["image_source"]) if replay_media else None
    f.new_id, _ = append_call(f, 266, "vss_detail_3_1", status="submitted", observation=observation, media=media)
    f.lost_queue = f.root / "mcp_queue" / f"{LOST}.request.json"
    f.journal = journal
    f.snapshot = snapshot
    return f


def save_resume(f):
    write_json(f.resume_file, f.resume)
    f.state["artifacts"][RESUME][0]["sha256"] = json_sha(f.resume)
    write_json(f.root / "library_state.json", f.state)


def dispatch(f, ident=None):
    return run_node(f, f"g.visualStoryRequestLimit({json.dumps(str(f.root))},{{job_id:{json.dumps(ident or f.new_id)}}});")


def test_resume_fixture_fields_match_actual_python_producer(tmp_path):
    from omni_story.library.visual_story_trial_state import record_unknown_resume
    f = resume_fixture(tmp_path)
    tree = ast.parse(inspect.getsource(record_unknown_resume))
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "resume" for target in node.targets))
    assert isinstance(assignment.value, ast.Call) and assignment.value.func.id == "dict"
    assert set(f.resume) == {keyword.arg for keyword in assignment.value.keywords}
    assert f.resume["goal_resumed"] is False and f.resume["teacher_answers_forbidden"] is True
    assert f.resume["new_renders"] == 0
    assert not {"original_authorization_sha256", "reuse_original_unused_renders", "baseline_artifacts"} & set(f.resume)


@pytest.mark.skipif(not os.environ.get("OMNI_VSS_READONLY_PREFLIGHT_ROOT"),
                    reason="Actual registered artifact preflight is explicit opt-in and read-only")
def test_actual_registered_grant_readonly_producer_consumer_preflight():
    from test_visual_story_mcp_guard import GUARD
    root = Path(os.environ["OMNI_VSS_READONLY_PREFLIGHT_ROOT"]).resolve(strict=True)
    state_path = root / "library_state.json"
    state = json.loads(state_path.read_text("utf-8"))
    auth = Path(state["artifacts"]["visual_story_skill_trial_v1"][0]["path"])
    resume = Path(state["artifacts"][RESUME][0]["path"])
    grant = json.loads(resume.read_text("utf-8"))
    assert grant["authorization_sha256"] == state["artifacts"]["visual_story_skill_trial_v1"][0]["sha256"]
    queue = root / "mcp_queue" / (LOST + ".request.json")
    files = [state_path, auth, resume, queue, queue.with_name(LOST + ".started.json"),
             queue.with_name(LOST + ".response.json")]
    before = {p: byte_sha(p) for p in files}
    # The preflight process has no secret and imports no launcher, SDK or model
    # tool. A direct accidental fetch is also forbidden in the child process.
    env = {k: v for k, v in os.environ.items()
           if k not in {"Z_AI_API_KEY", "API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
    env.update(OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE=str(auth),
               OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256=byte_sha(auth))
    code = ("globalThis.fetch=()=>{throw Error('read_only_preflight_network_forbidden')};"
            f" const g=await import({json.dumps(GUARD.as_uri())}); const root={json.dumps(str(root))};"
            " const c=g.visualStoryConfiguration(root);"
            " if(!c||c.maxConcurrency!==1)throw Error('configuration invalid');"
            f" if(!g.visualStoryFrozenJob(root)({json.dumps(str(queue))}))throw Error('265 not frozen');")
    result = subprocess.run(["node", "--input-type=module", "-e", code], env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    assert before == {p: byte_sha(p) for p in files}


def test_valid_resume_authorizes_only_independent_new_page_and_freezes_all_old_queues(tmp_path):
    f = resume_fixture(tmp_path)
    protected_bytes = {Path(row["path"]): Path(row["path"]).read_bytes() for row in f.resume["protected_files"]}
    with f.journal.open("ab") as stream:
        stream.write(b'{"type":"new_local_mock"}\n')
    r = run_node(f, f"""
const root={json.dumps(str(f.root))};
if(g.visualStoryRequestLimit(root,{{job_id:{json.dumps(f.new_id)}}})!==Infinity)throw Error('new page denied');
const skip=g.visualStoryFrozenJob(root);
for (const id of ['glm_166_old','glm_251_vss_observe',{json.dumps(LOST)}]) {{
  if(!skip(root+'/mcp_queue/'+id+'.request.json'))throw Error('history not frozen:'+id);
}}
if(skip(root+'/mcp_queue/'+{json.dumps(f.new_id)}+'.request.json'))throw Error('new page frozen');
""")
    assert r.returncode == 0, r.stderr
    assert all(p.read_bytes() == raw for p, raw in protected_bytes.items())
    rejected = dispatch(f, LOST)
    assert rejected.returncode != 0 and "unsettled_predecessor_or_job" in rejected.stderr


@pytest.mark.parametrize("mutation,expected", [
    ("missing_resume", "unsettled_predecessor_or_job"),
    ("resume_sha", "resume_authorization_changed"),
    ("original_sha", "resume_scope_or_prefix_changed"),
    ("prefix_sha", "resume_scope_or_prefix_changed"),
    ("lost_call_mutation", "resume_scope_or_prefix_changed"),
    ("snapshot", "resume_baseline_modified"),
    ("artifact", "resume_historical_artifact_changed"),
    ("protected_unknown_response", "resume_protected_bytes_changed"),
    ("protected_started", "resume_protected_bytes_changed"),
    ("journal", "resume_journal_prefix_changed"),
    ("lost_scope", "resume_lost_input_changed"),
    ("extra_render", "resume_scope_or_prefix_changed"),
    ("skipped_stage", "resume_scope_or_prefix_changed"),
    ("input_lock", "resume_scope_or_prefix_changed"),
    ("goal_resumed", "resume_scope_or_prefix_changed"),
    ("teacher_answers", "resume_scope_or_prefix_changed"),
    ("progress_policy", "resume_scope_or_prefix_changed"),
    ("kind_alias", "unknown_input_replay"),
    ("same_media", "unknown_input_replay"),
])
def test_resume_proofs_reject_mutations_and_lost_input_aliases(tmp_path, mutation, expected):
    f = resume_fixture(tmp_path, with_resume=mutation != "missing_resume", replay_kind=mutation == "kind_alias",
                       replay_media=mutation == "same_media")
    if mutation == "resume_sha":
        f.state["artifacts"][RESUME][0]["sha256"] = "0" * 64
        write_json(f.root / "library_state.json", f.state)
    elif mutation in {"original_sha", "prefix_sha", "lost_scope", "extra_render", "skipped_stage",
                      "input_lock", "goal_resumed", "teacher_answers", "progress_policy"}:
        if mutation == "original_sha":
            f.resume["authorization_sha256"] = "0" * 64
        elif mutation == "prefix_sha":
            f.resume["prefix_calls_sha256"] = "0" * 64
        elif mutation == "lost_scope":
            f.resume["lost_scope"]["kind"] = "continuous_window"
        elif mutation == "extra_render":
            f.resume["new_renders"] = 1
        elif mutation == "input_lock":
            f.resume["input_lock_sha256"] = "0" * 64
        elif mutation == "goal_resumed":
            f.resume["goal_resumed"] = True
        elif mutation == "teacher_answers":
            f.resume["teacher_answers_forbidden"] = False
        elif mutation == "progress_policy":
            f.resume["progress_policy"] = "unbounded_new_rounds"
        else:
            f.resume["skip_stages"].append("vss_inspect_0")
        save_resume(f)
    elif mutation == "lost_call_mutation":
        f.state["calls"][264]["status"] = "failed_known"
        write_json(f.root / "library_state.json", f.state)
    elif mutation == "snapshot":
        with f.snapshot.open("ab") as stream:
            stream.write(b"\n")
    elif mutation == "artifact":
        f.state["artifacts"]["vss_input_vss_inspect_0"][0]["sha256"] = "0" * 64
        write_json(f.root / "library_state.json", f.state)
    elif mutation in {"protected_unknown_response", "protected_started"}:
        suffix = ".response.json" if mutation == "protected_unknown_response" else ".started.json"
        (f.lost_queue.parent / (LOST + suffix)).write_bytes(b"altered history")
    elif mutation == "journal":
        raw = f.journal.read_bytes()
        f.journal.write_bytes(b"!" + raw[1:])
    r = dispatch(f)
    assert r.returncode != 0, r.stdout
    assert expected in r.stderr


@pytest.mark.parametrize("status,expected", [("uncertain", "unsettled_predecessor_or_job"),
                                          ("submitted", "single_lane_required"),
                                          ("failed_known", "unsettled_predecessor_or_job")])
def test_only_original_lost265_is_exempt_from_unsettled_predecessor_rule(tmp_path, status, expected):
    f = resume_fixture(tmp_path)
    f.state["calls"][-1]["status"] = status
    f.new_id, _ = append_call(f, 267, "vss_plan", status="submitted")
    r = dispatch(f)
    assert r.returncode != 0 and expected in r.stderr


def test_frozen_queue_callback_rejects_modified_lost_queue_binding(tmp_path):
    f = resume_fixture(tmp_path)
    queue = json.loads(f.lost_queue.read_text())
    queue["arguments"]["prompt"] = "changed historical question"
    write_json(f.lost_queue, queue)
    # This bypasses only the mocked file-proof row to exercise the queue binding
    # check itself. A real protected queue-byte mutation is rejected even sooner.
    row = next(row for row in f.resume["protected_files"] if row["path"] == str(f.lost_queue))
    row["sha256"] = byte_sha(f.lost_queue)
    save_resume(f)
    r = run_node(f, f"g.visualStoryFrozenJob({json.dumps(str(f.root))})({json.dumps(str(f.lost_queue))});")
    assert r.returncode != 0 and "frozen_queue_binding_changed" in r.stderr
