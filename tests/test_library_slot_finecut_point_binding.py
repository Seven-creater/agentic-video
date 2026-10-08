"""Synthetic point navigation preserves fallible model reports and old stops."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import slot_finecut_budget as budget
from omni_story.library.slot_finecut_contracts import neutral_facts_prompt
from omni_story.library.state import LibraryStopped, json_sha, write_json


def fixture(tmp_path, parent):
    output = tmp_path / f"point_{parent}"
    output.mkdir()
    start, end = (29, 41) if parent == 0 else (16, 27)
    selected_parent = {"round": parent, "baseline_id": f"render_{parent}", "sha256": "c" * 64, "duration_s": end}
    slot = {"slot_id": "s3", "start_s": start, "end_s": end,
            **{k: "synthetic model slot" for k in ("intended_takeaway", "entry_state", "exit_state", "link_to_previous", "link_to_next")}}
    outline = {"baseline_id": f"render_{parent}", "parent_sha256": "c" * 64,
               "slots": [{**slot, "slot_id": "s0", "start_s": 0, "end_s": start}, slot], "limitations": [], "uncertainties": []}
    folder = output / "execution" / f"render_{parent}"
    write_json(folder / "outline.json", outline)
    preparation_path = output / "preparation.json"
    write_json(preparation_path, {"parents": [selected_parent]})
    media = output / "proxy/window.mp4"
    media.parent.mkdir()
    media.write_bytes(b"Synthetic byte fixture, not movie footage")
    media_sha = budget.sha256_file(media)
    scope = {"kind": "continuous_window", "source_sha256": "c" * 64, "source_start_s": start, "source_end_s": end}
    write_json(media.parent / "lineage.json", {**scope, "sha256": media_sha, "source_offset_s": start,
               "media_duration_s": end - start, "time_mapping": "source_time_s = source_offset_s + proxy_time_s"})
    original_body = {"baseline_id": f"render_{parent}", "parent_sha256": "c" * 64, "slot_id": "s3",
                     "slot_start_s": start, "slot_end_s": end, "time_domain": "slot_local_output",
                     "evidence": [{"evidence_id": "e1", "start_s": 0, "end_s": 1,
                                   "observed_fact": "Synthetic model observation", "kind": "state", "basis": "visual"},
                                  {"evidence_id": "e2", "start_s": 2, "end_s": 2,
                                   "observed_fact": "Synthetic model point", "kind": "state", "basis": "visible_text"}],
                     "limitations": ["Model evidence is fallible."], "uncertainties": ["Point duration unknown."]}
    repair_body = deepcopy(original_body)
    if parent == 3:
        repair_body.update(slot_start_s=0, slot_end_s=end-start)
    else:
        repair_body["evidence"][1]["end_s"] = 3
        del repair_body["limitations"], repair_body["uncertainties"]
    stage = budget.POINT_NAVIGATION_STAGES[parent]
    calls, files = [], []
    errors = ("slot_finecut/fact:outside_bound_interval",
              "slot_finecut:fact_time_binding_changed" if parent == 3 else "slot_finecut/fact:outside_bound_interval")
    for attempt, body in enumerate((original_body, repair_body)):
        name = stage + ("_repair" if attempt else "")
        ident = f"synthetic_{parent}_{attempt}"
        request = {"provider": "official_vision_mcp_in_codex", "tool": "analyze_video", "media_sha256": media_sha,
                   "observation_scope": scope, "arguments": {"video_source": str(media),
                   "prompt": neutral_facts_prompt(selected_parent, slot) if attempt == 0 else "saved sole repair"}}
        response = {"result": {"content": [{"type": "text", "text": json.dumps(body)}]}}
        call = {"id": ident, "name": name, "status": "received", "request_sha256": json_sha(request),
                "response_sha256": json_sha(response), "repair_of": calls[0]["id"] if attempt else None}
        for filename, value in (("request.json", request), ("response.json", response),
                                ("protocol_failure.json", {"attempt": attempt, "error": errors[attempt]})):
            path = output / "calls" / ident / filename
            write_json(path, value)
            files.append({"path": str(path), "sha256": budget.sha256_file(path)})
        calls.append(call)
    stop = {"policy": budget.PARENT_STOP_POLICY, "preparation_id": "synthetic_prep", "parent_round": parent,
            "independent_parent_round": 3 if parent == 0 else 0, "request_count": 2,
            "original_call_id": calls[0]["id"], "repair_call_id": calls[1]["id"],
            "error": "model_protocol_repair_exhausted:" + stage, "files": files}
    stop_path = output / "stop.json"
    write_json(stop_path, stop)
    result_path = folder / ("result_metadata_bound_resume.json" if parent == 0 else "result.json")
    write_json(result_path, {"status": "stopped_protocol_failure", "stop_receipt": stop,
                            "baseline_id": selected_parent["baseline_id"], "parent_sha256": selected_parent["sha256"]})
    result_receipt = {"result_path": str(result_path), "result_sha256": budget.sha256_file(result_path)}
    result_receipt_path = output / "result_receipt.json"
    write_json(result_receipt_path, result_receipt)
    stop_key = "sf_parent_protocol_stop_0_metadata_bound_resume" if parent == 0 else "sf_parent_protocol_stop_3"
    data = {"task_id": "synthetic_task", "request_count": 2, "calls": calls, "artifacts": {
        stop_key: [{"path": str(stop_path), "sha256": json_sha(stop)}],
        ("sf_result_0_metadata_bound_resume" if parent == 0 else "sf_result_3"):
            [{"path": str(result_receipt_path), "sha256": json_sha(result_receipt)}]}}
    if parent == 0:
        old_stop_path = output / "old_141_stop.json"
        write_json(old_stop_path, {"historical_stop": "preserve bytes"})
        data["artifacts"]["sf_parent_protocol_stop_0"] = [{"path": str(old_stop_path), "sha256": json_sha({"historical_stop": "preserve bytes"})}]
        old_result_path = folder / "result.json"
        write_json(old_result_path, {"status": "stopped_protocol_failure", "historical_call": 141})
        old_result_receipt = {"result_path": str(old_result_path), "result_sha256": budget.sha256_file(old_result_path)}
        old_result_receipt_path = output / "old_141_result_receipt.json"
        write_json(old_result_receipt_path, old_result_receipt)
        data["artifacts"]["sf_result_0"] = [{"path": str(old_result_receipt_path), "sha256": json_sha(old_result_receipt)}]
        write_json(folder.parent / "parallel_stop_fixture.json", {"request_count": 153,
                   "errors": [{"baseline_id": "render_0", "error": "slot_finecut:parent_stop_error_changed"}]})
    record = {"baseline_request_count": 0, "preparation_path": str(preparation_path),
              "execution_directory": str(folder.parent), "preparation_id": "synthetic_prep", "unknown_inputs": []}
    state = SimpleNamespace(output=output, data=data, authorization=record, assert_protected=lambda: record)
    def artifact(name, value):
        path = output / (name + ".json")
        write_json(path, value)
        data["artifacts"][name] = [{"path": str(path), "sha256": json_sha(value)}]
    state.set_artifact = artifact
    return state, record, stage, original_body, repair_body


@pytest.mark.parametrize("parent", [0, 3])
def test_chosen_whole_reply_and_point_times_are_preserved_without_old_pass(tmp_path, parent):
    state, record, stage, original, repair = fixture(tmp_path, parent)
    before = {p: p.read_bytes() for p in state.output.rglob("*.json")}
    policy = budget.record_parent_point_navigation(state, parent, stage)
    chosen = original if parent == 0 else repair
    assert policy["envelope"]["model_report"] == chosen
    assert policy["original_contract_status"] == "failed" and policy["retroactive_fact_pass"] is False
    point = policy["envelope"]["temporal_support"][1]
    assert point["start_s"] == point["end_s"] == 2
    assert point["duration_unknown"] and point["cannot_prove_completed_action"] and not point["exposure_proof"]
    assert all(p.read_bytes() == content for p, content in before.items())
    assert not list(state.output.rglob("parsed.json"))
    if parent == 0:
        assert "uncertainties" in chosen and "uncertainties" not in repair
        assert any(row["field"] == "uncertainties" and row["repair_present"] is False
                   for row in policy["envelope"]["unresolved_model_disagreements"])
    assert budget.SlotFinecutState.derived_parent_navigation(state, stage, media_sha256=policy["media_sha256"]) == policy["envelope"]
    assert budget.SlotFinecutState._received(state, {}, stage) == policy["envelope"]
    with pytest.raises(LibraryStopped, match="current_media_changed"):
        budget.SlotFinecutState.derived_parent_navigation(state, stage, media_sha256="f" * 64)
    with pytest.raises(LibraryStopped, match="no_third_observation"):
        budget.SlotFinecutState._check_input(state, f"sf_{parent}_facts_" + "f" * 16,
            {"media_sha256": policy["media_sha256"], "observation_scope": policy["observation_scope"]}, record)


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_point_navigation_cannot_activate_with_pending_or_unknown(tmp_path, status):
    state, _, stage, _, _ = fixture(tmp_path, 3)
    state.data["calls"].append({"name": "sf_0_outline_v2", "status": status})
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        budget.record_parent_point_navigation(state, 3, stage)


def test_point_changes_and_nonmatching_scope_are_rejected(tmp_path):
    state, record, stage, _, _ = fixture(tmp_path, 3)
    with pytest.raises(LibraryStopped, match="scope_invalid"):
        budget.record_parent_point_navigation(state, 3, "sf_3_facts_" + "f" * 16)
    policy = budget.record_parent_point_navigation(state, 3, stage)
    policy["envelope"]["temporal_support"][1]["end_s"] = 2.01
    state.set_artifact(f"{budget.POINT_NAVIGATION_POLICY}_3", policy)
    with pytest.raises(LibraryStopped, match="body_or_evidence_changed"):
        budget._parent_point_navigation(state.output, state.data, record, 3)


def test_point_binding_requires_same_failed_outcome_in_separate_result(tmp_path):
    state, _, stage, _, _ = fixture(tmp_path, 0)
    receipt_entry = state.data["artifacts"]["sf_result_0_metadata_bound_resume"][0]
    receipt = json.loads(Path(receipt_entry["path"]).read_text(encoding="utf-8"))
    result_path = Path(receipt["result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["stop_receipt"]["repair_call_id"] = "old141"
    write_json(result_path, result)
    receipt["result_sha256"] = budget.sha256_file(result_path)
    write_json(receipt_entry["path"], receipt)
    receipt_entry["sha256"] = json_sha(receipt)
    with pytest.raises(LibraryStopped, match="point_navigation_stop_changed"):
        budget.record_parent_point_navigation(state, 0, stage)


def test_resume_allows_same_proposal_and_terminal_point_stop_blocks_more_work(tmp_path):
    state, record, stage, _, _ = fixture(tmp_path, 3)
    policy = budget.record_parent_point_navigation(state, 3, stage)
    before_stop = Path(state.data["artifacts"]["sf_parent_protocol_stop_3"][0]["path"]).read_bytes()
    original_id = "new_proposal_original"
    for attempt in (0, 1):
        name = policy["next_stage"] + ("_repair" if attempt else "")
        ident = original_id if attempt == 0 else "new_proposal_repair"
        request, response = {"synthetic": attempt}, {"result": {"content": [{"type": "text", "text": "broken"}]}}
        call = {"id": ident, "name": name, "status": "received", "repair_of": original_id if attempt else None,
                "request_sha256": json_sha(request), "response_sha256": json_sha(response)}
        for filename, value in (("request.json", request), ("response.json", response), ("protocol_failure.json", {"attempt": attempt})):
            write_json(state.output / "calls" / ident / filename, value)
        state.data["calls"].append(call)
    receipt = budget.SlotFinecutState.freeze_parent_protocol_failure(state, 3, "model_protocol_repair_exhausted:" + policy["next_stage"])
    assert state.data["artifacts"].get("sf_parent_protocol_stop_3_point_navigation_resume")
    assert Path(state.data["artifacts"]["sf_parent_protocol_stop_3"][0]["path"]).read_bytes() == before_stop
    assert budget._parent_stops(state.output, state.data, record)[3] == receipt
    state.data["calls"].append({"name": "sf_3_facts_" + "f" * 16, "status": "submitted"})
    with pytest.raises(LibraryStopped, match="stopped_parent_cannot_repeat"):
        budget._parent_stops(state.output, state.data, record)
