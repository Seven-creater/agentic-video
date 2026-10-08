"""Synthetic future permissions only; no real Goal, MCP, or render is run."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from omni_story.library import slot_finecut_budget as budget, slot_finecut_baselines
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


def request(number, *, start=10, sha="b" * 64):
    return {"arguments": {"prompt": "synthetic " + str(number)}, "media_sha256": f"{number:064x}",
            "observation_scope": {"kind": "continuous_window", "source_sha256": sha,
                                  "source_start_s": start, "source_end_s": start + 1}}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    state = LibraryState(tmp_path / "run", {"reference_sha256": "a" * 64,
                         "library_sources": [{"source_id": "film", "sha256": "b" * 64}]})
    calls = []
    for index in range(1, 132):
        ident = "glm_004_coarse_978d5360_01" if index == 4 else "glm_131_active_10_draft" if index == 131 else f"old_{index}"
        original = request(index, start=100 + index)
        call = {"id": ident, "name": "old_" + str(index), "request_sha256": json_sha(original),
                "repair_of": None, "status": "uncertain" if index in (4, 131) else "received", "usage": {}}
        write_json(state.output / "calls" / ident / "request.json", original)
        if call["status"] == "received":
            response = {"result": {"content": [{"type": "text", "text": "{}"}]}}
            write_json(state.output / "calls" / ident / "response.json", response)
            write_json(state.output / "calls" / ident / "parsed.json", {})
            call["response_sha256"] = json_sha(response)
        calls.append(call)
    state.data.update(calls=calls, request_count=len(calls))
    state._save()
    write_json(state.output / "editing_reference_v2.json", {"methods": []})
    preparation_path = state.output / "artifacts/prepared.json"
    prep = {"preparation_id": "slot_finecut_preparation_" + "c" * 64, "output": str(state.output), "task_id": state.data["task_id"],
            "input_lock_sha256": json_sha(state.data["input_lock"]), "baseline_rounds": [0, 3], "protected_files": []}
    write_json(preparation_path, prep)
    monkeypatch.setattr(slot_finecut_baselines, "load_preparation", lambda path: json.loads(Path(path).read_text(encoding="utf-8")))
    return state, preparation_path


def activated(prepared):
    original, preparation_path = prepared
    grant = budget.authorize(original.output, preparation_path, "Synthetic explicit future comparison.")
    return budget.SlotFinecutState(original.output), grant


def parallel_activated(prepared):
    state, grant = activated(prepared)
    original, folder = state.begin_call("sf_0_outline", request(201))
    state.complete_call(original, {"result": {"content": [{"type": "text", "text": "broken"}]}})
    write_json(folder / "protocol_failure.json", {"attempt": 0, "error": "fixture root contract"})
    raw = request(202)
    raw["media_sha256"] = request(201)["media_sha256"]
    repair, folder = state.begin_call("sf_0_outline_repair", raw, repair_of=original)
    state.complete_call(repair, {"result": {"content": [{"type": "text", "text": "broken repair"}]}})
    write_json(folder / "protocol_failure.json", {"attempt": 1, "error": "fixture repair root contract"})
    policy = budget.authorize_parallel_execution(state, "Synthetic explicit two parent parallel test with corrected prompt.",
                                                "ROOT_OUTPUT_INSTRUCTION synthetic v2")
    return state, grant, policy


def received(state, name, number, *, value=None, repair_of=None, start=10):
    raw = request(number, start=start)
    if repair_of:
        ident = repair_of["id"] if isinstance(repair_of, dict) else repair_of
        original = json.loads((state.output / "calls" / ident / "request.json").read_text(encoding="utf-8"))
        raw.update(media_sha256=original["media_sha256"], observation_scope=original["observation_scope"])
    call, folder = state.begin_call(name, raw, repair_of=repair_of)
    value = {} if value is None else value
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}})
    write_json(folder / "parsed.json", value)
    return call


def assembled(state, *, parent=0, assembly=None):
    prefix = f"sf_{parent}_"
    received(state, prefix + "outline", 201 + parent)
    received(state, prefix + "facts_" + "a" * 16, 211 + parent)
    received(state, prefix + "proposal_" + "a" * 16, 221 + parent)
    received(state, prefix + "assemble", 231 + parent, value=assembly)


def test_source_feedback_stage_requires_separate_permission(prepared):
    state, grant = activated(prepared)
    assembled(state, parent=3)
    before = deepcopy(state.data['calls'])
    with pytest.raises(LibraryStopped, match='source_feedback_not_authorized'):
        state.begin_call(budget.SOURCE_FEEDBACK_STAGE, request(901))
    assert state.data['calls'] == before
    assert grant['allowed_stage_pattern'] == budget.ALLOWED_STAGE_PATTERN


def test_authorization_is_explicit_idempotent_and_preserves_original_budget(prepared):
    original, path = prepared
    baseline = deepcopy(original.data["calls"])
    with pytest.raises(LibraryStopped, match="instruction_required"):
        budget.authorize(original.output, path, "")
    assert not original.data["artifacts"].get(budget.ARTIFACT)
    state, grant = activated(prepared)
    assert grant["baseline_request_count"] == 131
    assert grant["base_request_limit"] == state.data["max_requests"] == 80
    assert state.max_requests == float("inf") and state.usage()["max_requests"] is None
    assert state.data["calls"] == baseline
    assert Path(grant["authorization_path"]).parent.name == "sf_" + "c" * 12
    assert grant["execution_directory"] == str(budget.execution_folder(state.output, grant["preparation_id"]))
    assert "authorization_path" not in json.loads(Path(grant["authorization_path"]).read_text(encoding="utf-8"))
    before = state.path.read_bytes()
    assert budget.authorize(state.output, path, "Same authorization") == grant
    assert state.path.read_bytes() == before


def test_missing_permission_or_sibling_directory_cannot_activate(prepared, tmp_path):
    original, path = prepared
    with pytest.raises(LibraryStopped, match="one_authorization_required"):
        budget.SlotFinecutState(original.output)
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    write_json(sibling / "library_state.json", original.data)
    with pytest.raises(LibraryStopped, match="preparation_outside_original_run"):
        budget.authorize(sibling, path, "Explicit future request")


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_new_pending_or_unknown_blocks_all_further_work(prepared, status):
    state, _ = activated(prepared)
    call, _ = state.begin_call("sf_0_outline", request(201))
    if status != "submitted":
        state.fail_call(call, "synthetic lost outcome", uncertain=status == "uncertain")
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        state.begin_call("sf_3_outline", request(202))
    assert state.data["calls"][3]["status"] == state.data["calls"][130]["status"] == "uncertain"


@pytest.mark.parametrize("change", ["media", "scope", "digest"])
def test_unknown_request_scope_and_media_cannot_be_renamed_or_reencoded(prepared, change):
    state, grant = activated(prepared)
    old = grant["unknown_inputs"][0]
    new = request(201)
    if change == "media":
        new["media_sha256"] = old["media_sha256"]
    elif change == "scope":
        new["observation_scope"] = {**old["scope"], "source_start_s": float(old["scope"]["source_start_s"])}
    else:
        new = json.loads((state.output / "calls" / old["call_id"] / "request.json").read_text(encoding="utf-8"))
    with pytest.raises(LibraryStopped, match="unknown_input_replay_forbidden"):
        state.begin_call("sf_0_outline", new)
    assert state.data["request_count"] == 131


def test_valid_independent_stage_is_uncapped_but_duplicates_and_second_repairs_stop(prepared):
    state, _ = activated(prepared)
    call, _ = state.begin_call("sf_0_outline", request(201))
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": "{}"}]}})
    repair = received(state, "sf_0_outline_repair", 202, repair_of=call)
    assert state.data["request_count"] == 133 and state.data["max_requests"] == 80
    with pytest.raises(LibraryStopped):
        state.begin_call("sf_0_outline_repair", request(203), repair_of=call)
    with pytest.raises(LibraryStopped):
        state.begin_call("sf_0_outline_repair_repair", request(204), repair_of=repair)
    with pytest.raises(LibraryStopped):
        state.begin_call("sf_0_outline", request(205))
    received(state, "sf_0_facts_" + "a" * 16, 206)


@pytest.mark.parametrize("name", ["sf_0_assemble", "sf_3_review", "semantic_slice_20_" + "a" * 16,
                                   "semantic_claims_21_" + "a" * 16, "active_12_draft", "sf_1_outline"])
def test_unpaid_predecessors_and_other_route_names_are_blocked(prepared, name):
    state, _ = activated(prepared)
    with pytest.raises(LibraryStopped):
        state.begin_call(name, request(201))
    assert state.data["request_count"] == 131


def test_exhausted_observation_lineage_stays_blocked_across_ids(prepared):
    original, _ = prepared
    first = {"id": "glm_069_fixture", "name": "semantic_slice_3_" + "a" * 16, "status": "received",
             "repair_of": None, "usage": {}}
    repair = {"id": "glm_070_fixture", "name": first["name"] + "_repair", "status": "received",
              "repair_of": first["id"], "usage": {}}
    for row, index in ((first, 69), (repair, 70)):
        raw = request(400 + index, start=50)
        response = {"result": {"content": [{"type": "text", "text": "broken"}]}}
        row.update(request_sha256=json_sha(raw), response_sha256=json_sha(response))
        write_json(original.output / "calls" / row["id"] / "request.json", raw)
        write_json(original.output / "calls" / row["id"] / "response.json", response)
        original.data["calls"][index-1] = row
    original._save()
    state, _ = activated(prepared)
    assembled(state)
    with pytest.raises(LibraryStopped, match="known_exhausted_slice_lineage"):
        state.begin_call("semantic_slice_20_" + "b" * 16, request(301, start=50))


def test_protected_files_prefix_and_knowledge_cannot_change_on_resume(prepared):
    state, grant = activated(prepared)
    snapshot = deepcopy(state.data)
    state.data["calls"][3]["status"] = "received"
    state._save()
    with pytest.raises(LibraryStopped, match="original_calls_changed"):
        state.assert_protected()
    write_json(state.path, snapshot)
    path = Path(grant["knowledge_path"])
    original = path.read_bytes()
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(LibraryStopped, match="knowledge_snapshot_changed"):
        state.assert_protected()
    path.write_bytes(original)
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        state.fail_call(state.data["calls"][0], "no rewrite")
    with pytest.raises(LibraryStopped, match="historical_or_unknown_artifact_read_only"):
        state.set_artifact(budget.ARTIFACT, {})


def test_known_protocol_failure_does_not_allow_new_alias_stage_loop(prepared):
    state, _ = activated(prepared)
    call, _ = state.begin_call("sf_0_outline", request(201))
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": "broken"}]}})
    repaired_request = request(202)
    repaired_request["media_sha256"] = request(201)["media_sha256"]
    call, _ = state.begin_call("sf_0_outline_repair", repaired_request, repair_of=call)
    state.complete_call(call, {"result": {"content": [{"type": "text", "text": "broken"}]}})
    with pytest.raises(LibraryStopped, match="previous_stage_protocol_failure_no_new_loop"):
        state.begin_call("sf_3_outline", request(203))


def test_source_manifest_required_before_any_render_claim(prepared):
    state, _ = activated(prepared)
    assembled(state)
    with pytest.raises(LibraryStopped, match="source_manifest_required"):
        state.claim_render(0)
    assert not state.data["artifacts"].get("sf_0_render_claim")


def test_all_source_scopes_are_checked_before_any_exact_request(prepared):
    state, grant = activated(prepared)
    original = grant["unknown_inputs"][0]["scope"]
    plan = {"segments": [{"source_id": "film", "source_in_s": 10, "source_out_s": 11},
                         {"source_id": "film", "source_in_s": original["source_start_s"],
                          "source_out_s": original["source_end_s"]}]}
    before = state.path.read_bytes()
    with pytest.raises(LibraryStopped, match="selected_source_scope_blocked_no_replacement"):
        state.assert_source_inputs(plan, {"sources": [{"source_id": "film", "sha256": original["source_sha256"]}]})
    assert state.path.read_bytes() == before
    assert budget.blocked_source_scopes(state)[:2] == grant["unknown_inputs"]


@pytest.mark.parametrize("mutation", ["none", "missing_timing", "timing_blocker", "observation", "check"])
def test_render_claim_binds_actual_source_replies_and_readability(prepared, mutation):
    from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL
    state, grant = activated(prepared)
    segment = {"segment_id": "seg_1", "source_id": "film", "source_in_s": 10, "source_out_s": 11}
    assembly = {"plan": {"segments": [segment]}}
    assembled(state, assembly=assembly)
    observation = {**segment, "source_sha256": "b" * 64}
    check = {"segment_id": "seg_1", "claim_checks": []}
    key = json_sha({"segment": "seg_1", "sha": "b" * 64, "in": 10, "out": 11})[:16]
    received(state, "semantic_slice_20_" + key, 241, value=observation)
    received(state, "semantic_claims_20_" + key, 242, value=check)
    folder = Path(grant["authorization_path"]).parent / "render_0"
    write_json(folder / "assembly.json", assembly)
    manifest = {"protocol": SEMANTIC_PROTOCOL, "plan_sha256": json_sha(assembly["plan"]),
                "observations": [observation], "segment_checks": [check], "required_claims": [],
                "timing_counterevidence": []}
    if mutation == "missing_timing":
        del manifest["timing_counterevidence"]
    elif mutation == "timing_blocker":
        manifest["timing_counterevidence"] = [{"exposure_shortfall": 1}]
    elif mutation == "observation":
        manifest["observations"] = [{**observation, "unrecorded_fact": "invented"}]
    elif mutation == "check":
        manifest["segment_checks"] = [{**check, "unrecorded_verdict": "pass"}]
    write_json(folder / "source_manifest.json", manifest)
    if mutation != "none":
        with pytest.raises(LibraryStopped):
            state.claim_render(0)
        assert not state.data["artifacts"].get("sf_0_render_claim")
        return
    directory = state.claim_render(0)
    assert directory == folder / "render"
    directory.mkdir()
    before = state.path.read_bytes()
    assert state.claim_render(0) == directory
    assert state.path.read_bytes() == before
    assert len(state.data["artifacts"]["sf_0_render_claim"]) == 1


@pytest.mark.parametrize("method", ["reconcile_received", "reclassify_uncertain"])
def test_historical_transport_recovery_cannot_rewrite_admitted_unknowns(prepared, method):
    state, _ = activated(prepared)
    before = state.path.read_bytes()
    unknown = state.data["calls"][130]
    folder = state.output / "calls" / unknown["id"]
    original_files = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    with pytest.raises(LibraryStopped, match="historical_call_read_only"):
        if method == "reconcile_received":
            state.reconcile_received(unknown, {"result": {"content": [{"type": "text", "text": "{}"}]}},
                evidence={"status": 200, "job_id": unknown["id"], "body": '{"choices":[{"message":{"content":"{}"}}]}'})
        else:
            state.reclassify_uncertain(unknown, evidence={"job_id": unknown["id"], "status": "unknown"})
    assert state.path.read_bytes() == before
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == original_files


def test_adapter_cannot_enable_a_different_independent_policy(prepared):
    state, _ = activated(prepared)
    before = state.path.read_bytes()
    with pytest.raises(LibraryStopped, match="continuation_policy_is_bound_use_separate_authorization"):
        state.enable_independent_continuation()
    assert state.path.read_bytes() == before


def test_short_execution_directory_retains_full_identity_and_old_empty_directory(prepared):
    original, preparation_path = prepared
    preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
    old_directory = original.output / "artifacts" / budget.POLICY / preparation["preparation_id"]
    old_directory.mkdir(parents=True)
    state, grant = activated(prepared)
    assert old_directory.is_dir() and list(old_directory.iterdir()) == []
    assert grant["preparation_id"] == preparation["preparation_id"]
    assert Path(grant["execution_directory"]) == old_directory.parent / ("sf_" + "c" * 12)
    entry = state.data["artifacts"][budget.ARTIFACT][0]
    canonical = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
    canonical["execution_directory"] = str(old_directory)
    write_json(entry["path"], canonical)
    entry["sha256"] = json_sha(canonical)
    state._save()
    with pytest.raises(LibraryStopped, match="authorization_preparation_directory_changed"):
        budget.load_authorization(state.output)


def test_parallel_authorization_preserves_known_failures_and_is_idempotent(prepared):
    state, grant, policy = parallel_activated(prepared)
    before = state.path.read_bytes()
    assert policy["baseline_request_count"] == 133 and policy["max_concurrent_parents"] == 2
    assert budget.authorize_parallel_execution(state, "Same authorized intent", policy["prompt_policy"]) == policy
    assert state.path.read_bytes() == before
    assert state.data["calls"][:131] == json.loads(Path(grant["baseline_calls_path"]).read_text(encoding="utf-8"))
    for row in policy["protected_failure_files"]:
        assert budget.sha256_file(row["path"]) == row["sha256"]
    with pytest.raises(LibraryStopped, match="parallel_prompt_policy_changed"):
        budget.authorize_parallel_execution(state, "New text cannot rewrite approval", "different policy")


def test_parallel_two_parents_may_be_submitted_and_finish_in_reverse_order(prepared):
    state, _, _ = parallel_activated(prepared)
    other = budget.SlotFinecutState(state.output)
    call0, _ = state.begin_call(budget.REPLACEMENT_OUTLINE, request(301))
    call3, _ = other.begin_call("sf_3_outline", request(302))
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        state.begin_call("sf_0_facts_" + "a" * 16, request(303))
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        other.begin_call("sf_3_facts_" + "b" * 16, request(304))
    other.complete_call(call3, {"synthetic": "parent3 first"})
    state.complete_call(call0, {"synthetic": "parent0 second"})
    assert state.usage()["requests"] == 135
    assert [c["status"] for c in state.data["calls"][-2:]] == ["received", "received"]


@pytest.mark.parametrize("status", ["uncertain", "failed_known"])
def test_parallel_new_unknown_stops_all_new_calls_but_pending_reply_is_preserved(prepared, status):
    state, _, _ = parallel_activated(prepared)
    call0, _ = state.begin_call(budget.REPLACEMENT_OUTLINE, request(301))
    call3, _ = state.begin_call("sf_3_outline", request(302))
    state.fail_call(call0, "synthetic transport stop", uncertain=status == "uncertain")
    with pytest.raises(LibraryStopped, match="new_or_pending_outcome_unknown"):
        state.begin_call("sf_3_outline_repair", request(303), repair_of=call3)
    state.complete_call(call3, {"synthetic": "already submitted response retained"})
    assert state.data["calls"][-2]["status"] == status
    assert state.data["calls"][-1]["status"] == "received"


def test_parallel_sole_repair_can_follow_other_parent_call_but_requires_own_last_stage(prepared):
    state, _, _ = parallel_activated(prepared)
    original, _ = state.begin_call(budget.REPLACEMENT_OUTLINE, request(301))
    state.complete_call(original, {"synthetic": "format failure"})
    other, _ = state.begin_call("sf_3_outline", request(302))
    repair = received(state, budget.REPLACEMENT_OUTLINE + "_repair", 303, repair_of=original)
    received(state, "sf_0_facts_" + "a" * 16, 304)
    with pytest.raises(LibraryStopped):
        state.begin_call(budget.REPLACEMENT_OUTLINE + "_repair", request(305), repair_of=original)
    state.complete_call(other, {"synthetic": "independent reply"})
    assert state.data["calls"][-2]["repair_of"] == original["id"]
    assert repair["name"] == budget.REPLACEMENT_OUTLINE + "_repair"


def test_parallel_ledger_mutex_prevents_lost_calls_or_completions(prepared):
    state, _, _ = parallel_activated(prepared)
    states = [budget.SlotFinecutState(state.output), budget.SlotFinecutState(state.output)]
    def run(index):
        name = budget.REPLACEMENT_OUTLINE if index == 0 else "sf_3_outline"
        return states[index].begin_call(name, request(301 + index))[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = list(pool.map(run, (0, 1)))
        list(pool.map(lambda item: states[item[0]].complete_call(item[1], {"synthetic": item[0]}), enumerate(calls)))
    state.assert_protected()
    assert state.data["request_count"] == 135
    assert len({c["id"] for c in state.data["calls"]}) == 135
    assert all(c["status"] == "received" for c in state.data["calls"][-2:])


def test_parallel_corrected_outline_requires_forward_permission(prepared):
    state, _ = activated(prepared)
    with pytest.raises(LibraryStopped, match="replacement_outline_not_authorized"):
        state.begin_call(budget.REPLACEMENT_OUTLINE, request(301))
    assert state.data["request_count"] == 131


def test_parallel_failed_v2_is_frozen_and_other_parent_can_continue(prepared):
    state, _, _ = parallel_activated(prepared)
    original, folder = state.begin_call(budget.REPLACEMENT_OUTLINE, request(301))
    state.complete_call(original, {"synthetic": "v2 original failure"})
    write_json(folder / "protocol_failure.json", {"attempt": 0})
    other, other_folder = state.begin_call("sf_3_outline", request(302))
    raw = request(303)
    raw["media_sha256"] = request(301)["media_sha256"]
    repair, folder = state.begin_call(budget.REPLACEMENT_OUTLINE + "_repair", raw, repair_of=original)
    state.complete_call(repair, {"synthetic": "v2 repair failure"})
    write_json(folder / "protocol_failure.json", {"attempt": 1})
    receipt = state.freeze_parent_protocol_failure(0, "model_protocol_repair_exhausted:" + budget.REPLACEMENT_OUTLINE)
    assert receipt["original_call_id"] == original["id"]
    state.complete_call(other, {"result": {"content": [{"type": "text", "text": "{}"}]}})
    write_json(other_folder / "parsed.json", {})
    with pytest.raises(LibraryStopped, match="stopped_parent_cannot_repeat"):
        state.begin_call("sf_0_facts_" + "a" * 16, request(304))
    received(state, "sf_3_facts_" + "b" * 16, 305)


@pytest.mark.parametrize("target", ["baseline", "failed_file", "old_parsed", "policy"])
def test_parallel_old_failures_and_forward_authorization_are_immutable(prepared, target):
    state, _, policy = parallel_activated(prepared)
    if target == "baseline":
        state.data["calls"][-1]["usage"] = {"invented": 1}
        state._save()
    elif target == "failed_file":
        path = Path(policy["protected_failure_files"][-1]["path"])
        path.write_bytes(path.read_bytes() + b" ")
    elif target == "old_parsed":
        write_json(state.output / "calls" / policy["failed_call_ids"][0] / "parsed.json", {})
    else:
        entry = state.data["artifacts"][budget.PARALLEL_POLICY][0]
        modified = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
        modified["max_concurrent_parents"] = 3
        write_json(entry["path"], modified)
    with pytest.raises(LibraryStopped):
        state.assert_protected()
