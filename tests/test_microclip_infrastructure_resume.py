"""Synthetic append-only recovery of the one known local publication failure."""
import json
from pathlib import Path

import pytest

from test_library_microclip_state import registered, old_prepared, image_input, complete, node, read
from omni_story.library import microclip_state as mc
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json


@pytest.fixture
def cpu_stopped(registered):
    root, auth = registered
    state = mc.MicroclipState(root)
    for index in range(2):
        name, request = image_input(state, f"mc_observe_{index}", index * 2.0, 15.0 if index == 0 else 4.0)
        entries = state.data["artifacts"]["mc_input_" + name]
        descriptor = read(entries[0]["path"])
        manifest_path = Path(descriptor["lineage_path"])
        manifest = read(manifest_path)
        base = manifest["frames"][0]
        manifest["frames"] = [dict(base, frame_id=f"synthetic_{index}_{j}", source_time_s=index * 2.0 + j * .1,
            frame_end_s=index * 2.0 + (j + 1) * .1, pts=index * 50 + j * 2.5,
            pts_time_s=index * 2.0 + j * .1) for j in range(6)]
        write_json(manifest_path, manifest)
        manifest_path.with_name("manifest.sha256").write_text(sha256_file(manifest_path), encoding="ascii")
        descriptor["lineage_sha256"] = sha256_file(manifest_path)
        # The synthetic descriptor has not been used by a call yet. Keep a fresh
        # self-consistent state before binding its actual request.
        write_json(entries[0]["path"], descriptor)
        from omni_story.library.state import json_sha
        data = read(root / "library_state.json")
        data["artifacts"]["mc_input_" + name][0]["sha256"] = json_sha(descriptor)
        write_json(root / "library_state.json", data)
        call, _ = state.begin_call(name, request)
        frames = manifest["frames"]
        value = {"frames": [{"frame_id": row["frame_id"], "visible_action_or_state": "Synthetic geometry",
                              "visible_text": ""} for row in frames], "visible_event": "Visible synthetic change",
            "missing_information": ["Inspect the local change"], "limitations": ["Synthetic fixture"],
            "next_observation": {"action": "zoom", "start_frame_id": frames[1]["frame_id"],
                "end_frame_id": frames[4]["frame_id"], "question": "Which geometric state changed?",
                "reason": "Inspect more real frames"}}
        complete(state, call, value)
    directory = Path(auth["execution_directory"])
    temporary = directory / "frames" / "view_2" / "._grid_abcdef123456_12345678"
    temporary.mkdir(parents=True)
    (temporary / "grid.png").write_bytes(b"preserved forensic partial")
    destination = temporary.with_name("grid_abcdef12345678901234")
    error = f"[WinError 5] Access is denied: {str(temporary)!r} -> {str(destination)!r}"
    result = {"status": "stopped", "error": error, "model_quality_gate_passed": False,
        "final_video": None, "final_sha256": None, "measured_duration_s": None,
        "observed_frames": 12, "observation_rounds": 2}
    path = state.finish(result)
    old = {f: f.read_bytes() for f in directory.rglob("*") if f.is_file()}
    old.update({root / "artifacts" / "mc_result_001.json": (root / "artifacts" / "mc_result_001.json").read_bytes()})
    return root, path, old


def test_exact_cpu_stop_resumes_once_without_new_render_grant(cpu_stopped):
    root, stopped_path, old = cpu_stopped
    state = mc.MicroclipState(root)
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call("mc_observe_2", {})
    auth = mc.record_infrastructure_resume(root, "Continue the original unused local experiment after its known CPU publication failure.")
    resume = auth["infrastructure_resume"]
    assert resume["baseline_request_count"] == 209 and resume["new_renders"] == 0
    assert resume["reuse_original_unused_render"] is True
    assert mc.record_infrastructure_resume(root, "Same authorization") == auth
    assert node(root, auth, "console.log(g.loadMicroclip(root,env).resume.next_stage)").stdout.strip() == "mc_observe_2"
    state = mc.MicroclipState(root)
    with pytest.raises(LibraryStopped, match="resume_next_stage_required"):
        state.begin_call("mc_motion", {})
    call, _ = state.begin_call(*image_input(state, "mc_observe_2", 2.12, 2.4))
    request = read(root / "calls" / call["id"] / "request.json")
    queued = {"job_id": call["id"], "tool": request["tool"], "arguments": request["arguments"]}
    write_json(root / "mcp_queue" / (call["id"] + ".request.json"), queued)
    result = node(root, auth, f"console.log(g.microclipRequestLimit(root,{{job_id:{json.dumps(call['id'])}}},env))")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Infinity"
    complete(state, call, {"synthetic": "known received observation"})
    recovered = state.finish({"status": "stopped", "model_quality_gate_passed": False,
                              "error": "Synthetic subsequent finite stop"})
    assert recovered.name == "result_recovered.json" and recovered != stopped_path
    assert state.data["artifacts"].get("mc_recovered_result")
    assert all(path.read_bytes() == value for path, value in old.items())
    assert node(root, auth, "console.log(g.loadMicroclip(root,env).state.calls.length)").stdout.strip() == "210"
    with pytest.raises(LibraryStopped, match="finished_or_repeated_stage"):
        state.begin_call("mc_observe_3", {})
    with pytest.raises(LibraryStopped, match="finished_experiment_read_only"):
        state.claim_render({})


@pytest.mark.parametrize("change", ["partial", "old_result", "prefix", "grant"])
def test_resume_bound_history_and_grant_are_checked_by_python_and_node(cpu_stopped, change):
    root, stopped_path, old = cpu_stopped
    auth = mc.record_infrastructure_resume(root, "Resume this known CPU failure.")
    if change == "partial":
        next(p for p in old if p.name == "grid.png").write_bytes(b"changed forensic partial")
    elif change == "old_result":
        stopped_path.write_text("{}", encoding="utf-8")
    elif change == "prefix":
        data = read(root / "library_state.json")
        data["calls"][208]["usage"] = {"completion_tokens": 99}
        write_json(root / "library_state.json", data)
    else:
        data = read(root / "library_state.json")
        entry = data["artifacts"][mc.RESUME_ARTIFACT][0]
        value = read(entry["path"])
        value["new_renders"] = 1
        write_json(entry["path"], value)
        from omni_story.library.state import json_sha
        entry["sha256"] = json_sha(value)
        write_json(root / "library_state.json", data)
    with pytest.raises((LibraryStopped, KeyError, ValueError)):
        mc.get_auth(root, force=True)
    assert node(root, auth, "g.loadMicroclip(root,env)").returncode != 0


def test_resume_cannot_register_after_a_new_stage_or_grant_use(cpu_stopped):
    root, _, _ = cpu_stopped
    state = mc.MicroclipState(root)
    image_input(state, "mc_observe_2", 2.1, 2.4)
    with pytest.raises(LibraryStopped, match="unused_pre_render_grant_required"):
        mc.record_infrastructure_resume(root, "Cannot expand this recovery.")


def test_recovered_result_bytes_remain_bound(cpu_stopped):
    root, _, _ = cpu_stopped
    auth = mc.record_infrastructure_resume(root, "Resume this known CPU failure.")
    state = mc.MicroclipState(root)
    recovered = state.finish({"status": "stopped", "model_quality_gate_passed": False})
    recovered.write_text("{}", encoding="utf-8")
    with pytest.raises(LibraryStopped, match="completed_bytes_changed"):
        mc.get_auth(root, force=True)
    assert node(root, auth, "g.loadMicroclip(root,env)").returncode != 0
