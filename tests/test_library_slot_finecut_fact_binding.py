"""Pure synthetic request-bound metadata; no real state, MCP or movie is used."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import slot_finecut_budget as budget
from omni_story.library.slot_finecut_contracts import neutral_facts_prompt
from omni_story.library.state import LibraryStopped, json_sha, write_json


def fixture(tmp_path, *, start=0, domain=None, kind="inference", basis="visible_text", zero_interval=False):
    output = tmp_path / "synthetic"
    output.mkdir()
    parent = {"round": 0, "baseline_id": "render_0", "sha256": "c" * 64, "duration_s": start + 2}
    slot = {"slot_id": "s1", "start_s": start, "end_s": start + 2,
            **{k: "synthetic model slot" for k in ("intended_takeaway", "entry_state", "exit_state", "link_to_previous", "link_to_next")}}
    slots = [slot] if start == 0 else [{**slot, "slot_id": "previous", "start_s": 0, "end_s": start}, slot]
    folder = output / "execution/render_0"
    outline = {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
               "slots": slots, "limitations": [], "uncertainties": []}
    write_json(folder / "outline.json", outline)
    preparation_path = output / "preparation.json"
    write_json(preparation_path, {"parents": [parent]})
    media = output / "proxy/window.mp4"
    media.parent.mkdir()
    media.write_bytes(b"synthetic media; deliberately not a real film")
    scope = {"kind": "continuous_window", "source_sha256": parent["sha256"], "source_start_s": start, "source_end_s": start + 2}
    media_sha = budget.sha256_file(media)
    lineage = {**scope, "sha256": media_sha, "source_offset_s": start, "media_duration_s": 2,
               "time_mapping": "source_time_s = source_offset_s + proxy_time_s"}
    write_json(media.parent / "lineage.json", lineage)
    body = {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"], "slot_id": slot["slot_id"],
            "slot_start_s": start, "slot_end_s": start + 2, "evidence": [{"evidence_id": "e1", "start_s": 0,
                "end_s": 0 if zero_interval else 1.0, "observed_fact": "Synthetic displayed text, not proven visible action.",
                "kind": kind, "basis": basis}], "limitations": [], "uncertainties": []}
    if domain is not None:
        body["time_domain"] = domain
    stage = "sf_0_facts_" + json_sha({"parent": parent["sha256"], "slot": slot})[:16]
    calls = []
    protected = []
    for attempt in (0, 1):
        name = stage + ("_repair" if attempt else "")
        ident = "synthetic_" + name
        request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video", "media_sha256": media_sha,
                   "observation_scope": scope, "arguments": {"video_source": str(media),
                   "prompt": neutral_facts_prompt(parent, slot) if attempt == 0 else "Sole saved format repair"}}
        response = {"result": {"content": [{"type": "text", "text": json.dumps(body)}]}}
        call = {"id": ident, "name": name, "status": "received", "request_sha256": json_sha(request),
                "response_sha256": json_sha(response), "repair_of": calls[0]["id"] if attempt else None}
        call_folder = output / "calls" / ident
        for file, value in (("request.json", request), ("response.json", response),
                            ("protocol_failure.json", {"attempt": attempt, "error": "slot_finecut:fact_time_binding_changed"})):
            write_json(call_folder / file, value)
            protected.append({"path": str(call_folder / file), "sha256": budget.sha256_file(call_folder / file)})
        calls.append(call)
    stop = {"policy": budget.PARENT_STOP_POLICY, "preparation_id": "synthetic_prep", "parent_round": 0,
            "independent_parent_round": 3, "request_count": 2, "original_call_id": calls[0]["id"], "repair_call_id": calls[1]["id"],
            "error": "model_protocol_repair_exhausted:" + stage, "files": protected}
    stop_path = output / "stop.json"
    write_json(stop_path, stop)
    result_path = folder / "result.json"
    write_json(result_path, {"status": "stopped_protocol_failure"})
    result_receipt = {"result_path": str(result_path), "result_sha256": budget.sha256_file(result_path)}
    result_receipt_path = output / "result_receipt.json"
    write_json(result_receipt_path, result_receipt)
    data = {"task_id": "synthetic_task", "calls": calls, "request_count": 2, "artifacts": {
        "sf_parent_protocol_stop_0": [{"path": str(stop_path), "sha256": json_sha(stop)}],
        "sf_result_0": [{"path": str(result_receipt_path), "sha256": json_sha(result_receipt)}]}}
    record = {"baseline_request_count": 0, "preparation_path": str(preparation_path),
              "execution_directory": str(folder.parent), "preparation_id": "synthetic_prep"}
    state = SimpleNamespace(output=output, data=data, authorization=record, assert_protected=lambda: record)
    def artifact(name, value):
        path = output / (name + ".json")
        write_json(path, value)
        data["artifacts"][name] = [{"path": str(path), "sha256": json_sha(value)}]
    state.set_artifact = artifact
    return state, record, stage, body


def test_only_program_domain_is_added_and_old_failure_bytes_remain(tmp_path):
    state, record, stage, body = fixture(tmp_path)
    before = {p: p.read_bytes() for p in state.output.rglob("*.json")}
    policy = budget.record_request_bound_parent_facts(state, stage=stage)
    assert policy["body"] == {**body, "time_domain": "slot_local_output"}
    assert policy["body"]["evidence"][0]["kind"] == "inference"
    assert policy["original_contract_status"] == "failed"
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not list(state.output.rglob("parsed.json"))
    assert budget.record_request_bound_parent_facts(state, stage=stage) == policy
    value = budget.SlotFinecutState.derived_fact(state, stage, media_sha256=policy["media_sha256"])
    assert value == policy["body"]
    assert budget.SlotFinecutState._received(state, {}, stage) == value
    with pytest.raises(LibraryStopped, match="current_media_changed"):
        budget.SlotFinecutState.derived_fact(state, stage, media_sha256="f" * 64)
    data = deepcopy(state.data)
    data["calls"].append({"name": policy["next_stage"], "status": "submitted"})
    assert 0 in budget._parent_stops(state.output, data, record)
    data["calls"][-1]["name"] = "sf_0_facts_" + "f" * 16
    with pytest.raises(LibraryStopped, match="stopped_parent_cannot_repeat"):
        budget._parent_stops(state.output, data, record)


@pytest.mark.parametrize("changes", [{"domain": "parent_output"}, {"start": 1}, {"basis": "visual"}, {"zero_interval": True}])
def test_wrong_domain_nonzero_slot_visual_inference_and_invalid_times_are_rejected(tmp_path, changes):
    state, _, stage, _ = fixture(tmp_path, **changes)
    with pytest.raises(Exception):
        budget.record_request_bound_parent_facts(state, stage=stage)
    assert budget.REQUEST_BOUND_FACT_POLICY not in state.data["artifacts"]
    assert not list(state.output.rglob("parsed.json"))


def test_derived_evidence_cannot_be_changed_even_with_recomputed_artifact_hash(tmp_path):
    state, record, stage, _ = fixture(tmp_path)
    policy = budget.record_request_bound_parent_facts(state, stage=stage)
    policy["body"]["evidence"][0]["end_s"] = 1.5
    state.set_artifact(budget.REQUEST_BOUND_FACT_POLICY, policy)
    with pytest.raises(LibraryStopped, match="body_or_evidence_changed"):
        budget._request_bound_parent_facts(state.output, state.data, record)


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_pending_or_unknown_never_activates_metadata_resume(tmp_path, status):
    state, _, stage, _ = fixture(tmp_path)
    state.data["calls"].append({"name": "sf_3_outline", "status": status})
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        budget.record_request_bound_parent_facts(state, stage=stage)
    assert budget.REQUEST_BOUND_FACT_POLICY not in state.data["artifacts"]


def test_metadata_binding_never_allows_third_observation_under_a_new_stage(tmp_path):
    state, record, stage, _ = fixture(tmp_path)
    policy = budget.record_request_bound_parent_facts(state, stage=stage)
    record["unknown_inputs"] = []
    request = {"media_sha256": policy["media_sha256"], "observation_scope": policy["observation_scope"]}
    with pytest.raises(LibraryStopped, match="no_third_observation"):
        budget.SlotFinecutState._check_input(state, "sf_0_facts_" + "f" * 16, request, record)


@pytest.mark.skipif(not shutil.which("node"), reason="Guard mirror requires Node")
@pytest.mark.parametrize("tamper", [False, True])
def test_guard_mirror_revalidates_derived_body_and_nonvisual_taxonomy(tmp_path, tamper):
    state, record, stage, _ = fixture(tmp_path)
    policy = budget.record_request_bound_parent_facts(state, stage=stage)
    if tamper:
        policy["body"]["evidence"][0]["basis"] = "visual"
        state.set_artifact(budget.REQUEST_BOUND_FACT_POLICY, policy)
    write_json(state.output / "library_state.json", state.data)
    module = Path("omni_story/library/mcp_slot_finecut_guard.mjs").resolve().as_uri()
    code = """const fs=await import('node:fs'); const {requestBoundParentFacts}=await import(process.argv[1]);
try { const r=requestBoundParentFacts(process.argv[2],JSON.parse(fs.readFileSync(process.argv[2]+'/library_state.json','utf8')),JSON.parse(process.argv[3]));
process.stdout.write(r.body.time_domain); } catch(e) {process.stderr.write(String(e.message));process.exitCode=3;}"""
    checked = subprocess.run(["node", "--input-type=module", "-e", code, module, str(state.output), json.dumps(record)],
                             text=True, capture_output=True, timeout=30)
    if tamper:
        assert checked.returncode != 0 and "body_changed" in checked.stderr
    else:
        assert checked.returncode == 0, checked.stderr
        assert checked.stdout == "slot_local_output"
