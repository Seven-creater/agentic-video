"""Forward evidence-first activation and cache tests using temporary files only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import goal_budget as goal, research_resume as research
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_goal_budget import setup
from test_library_round_context import fixture as context_fixture


class FakeState:
    def __init__(self, output):
        self.output = output.resolve()
        self.data = {"input_lock": {"reference_sha256": "a" * 64},
                     "calls": [], "request_count": 0, "artifacts": {}}
        self.writes = 0

    def set_artifact(self, name, value):
        assert not self.data["artifacts"].get(name), "test artifact is immutable"
        path = self.output / "artifacts" / (name + "_001.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, value)
        self.data["artifacts"][name] = [{"path": str(path), "sha256": json_sha(value)}]
        self.writes += 1


@pytest.fixture
def fake(tmp_path, monkeypatch):
    state = FakeState(tmp_path)
    state.set_artifact("goal_result_8", {"status": "stopped_source_counterevidence"})
    monkeypatch.setattr(goal, "stage_state", lambda output: state)
    monkeypatch.setattr(goal, "get_authorization", lambda state: {"baseline_request_count": 1})
    state.data["calls"] = [{"id": "old_004", "name": "coarse_old", "status": "uncertain"}]
    state.data["request_count"] = 1
    return state


def activate(state):
    return research.enable_preplanning(state.output, "Synthetic forward evidence-first research.")


def replace_record(state, name, value, *, update_sha=True):
    entry = state.data["artifacts"][name][0]
    write_json(entry["path"], value)
    if update_sha:
        entry["sha256"] = json_sha(value)


def add_fact(state, monkeypatch, records, index):
    _, examples = context_fixture()
    item = deepcopy(examples[index % 2])
    item["call_id"] = "known_fact_" + str(index)
    item["original_observation"]["segment_id"] = "seg_" + str(index)
    item["observation_sha256"] = json_sha(item["original_observation"])
    call = {"id": item["call_id"], "name": "semantic_slice_8_" + str(index), "status": "received",
            "request_sha256": item["request_sha256"], "response_sha256": item["response_sha256"]}
    state.data["calls"].append(call)
    state.data["request_count"] = len(state.data["calls"])
    folder = state.output / "calls" / call["id"]
    folder.mkdir(parents=True)
    write_json(folder / "parsed.json", item["original_observation"])
    records[call["id"]] = ({"media_sha256": item["media_sha256"]}, item["original_observation"])
    monkeypatch.setattr(goal, "_call_value", lambda output, call: records[call["id"]])
    return item


def shared_context():
    value, _ = context_fixture()
    del value["research_refinement_evidence"]
    return value


def test_activation_preserves_old_unknown_calls_and_is_idempotent_even_after_paid_round(fake):
    old = deepcopy(fake.data["calls"])
    record = activate(fake)
    assert research.load_preplanning(fake, 8) is None
    assert research.load_preplanning(fake, 9) == record
    assert research.load_preplanning(fake, 10) == record
    assert fake.data["calls"] == old
    fake.data["calls"].append({"name": "active_9_draft", "status": "received"})
    writes = fake.writes
    assert activate(fake) == record
    assert fake.writes == writes
    assert fake.data["calls"][0]["status"] == "uncertain"


@pytest.mark.parametrize("name", ["active_9_draft", "active_9_finecut_repair",
                                  "semantic_slice_9_deadbeef", "semantic_claims_9_deadbeef"])
def test_new_strategy_cannot_reinterpret_any_paid_ninth_round(fake, name):
    fake.data["calls"].append({"name": name, "status": "received"})
    before = deepcopy(fake.data)
    with pytest.raises(ValueError, match="cannot_change_paid_round"):
        activate(fake)
    assert fake.data == before
    assert not (fake.output / "artifacts" / research.PREPLANNING_POLICY).exists()


@pytest.mark.parametrize("status", ["submitted", "uncertain", "failed_known"])
def test_new_unsettled_call_blocks_activation_without_creating_files(fake, status):
    fake.data["calls"].append({"name": "active_8_finecut", "status": status})
    with pytest.raises(ValueError, match="unsettled_new_call"):
        activate(fake)
    assert not (fake.output / "artifacts" / research.PREPLANNING_POLICY).exists()


def test_previous_round_must_be_finished_before_activation(fake):
    del fake.data["artifacts"]["goal_result_8"]
    with pytest.raises(ValueError, match="previous_round_not_finished"):
        activate(fake)
    assert not (fake.output / "artifacts" / research.PREPLANNING_POLICY).exists()


@pytest.mark.parametrize("first_round", [8, 10, True])
def test_activation_requires_the_explicit_forward_ninth_round(fake, first_round):
    with pytest.raises(ValueError, match="explicit_forward_instruction_required"):
        research.enable_preplanning(fake.output, "Synthetic instruction.", first_round=first_round)
    assert not fake.data["artifacts"].get(research.PREPLANNING_ARTIFACT)


def test_strategy_hash_and_locked_inputs_are_checked(fake):
    record = activate(fake)
    replace_record(fake, research.PREPLANNING_ARTIFACT, {**record, "user_instruction": "Changed."}, update_sha=False)
    with pytest.raises(ValueError, match="strategy_changed"):
        research.load_preplanning(fake, 9)
    replace_record(fake, research.PREPLANNING_ARTIFACT, record)
    fake.data["input_lock"]["reference_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="strategy_changed"):
        research.load_preplanning(fake, 9)


@pytest.mark.parametrize("flag", ["typed_source_facts_before_draft", "explicit_claim_checks",
    "original_observations_unchanged", "no_new_unique_fine_windows",
    "no_numeric_request_ceiling", "no_old_reply_normalization"])
def test_scope_flags_cannot_be_disabled_even_with_a_matching_record_hash(fake, flag):
    record = activate(fake)
    replace_record(fake, research.PREPLANNING_ARTIFACT, {**record, flag: False})
    with pytest.raises(ValueError, match="scope_changed"):
        research.load_preplanning(fake, 9)


def test_knowledge_snapshot_is_hash_bound_and_must_stay_inside_the_run(fake, tmp_path):
    record = activate(fake)
    card = Path(record["knowledge_path"])
    original = card.read_bytes()
    card.write_bytes(b"changed generic knowledge")
    with pytest.raises(ValueError, match="knowledge_changed"):
        research.load_preplanning(fake, 9)
    card.write_bytes(original)
    outside = tmp_path.parent / (tmp_path.name + "_external_card.md")
    outside.write_bytes(original)
    try:
        replace_record(fake, research.PREPLANNING_ARTIFACT, {**record, "knowledge_path": str(outside)})
        with pytest.raises(ValueError, match="knowledge_changed"):
            research.load_preplanning(fake, 9)
    finally:
        outside.unlink()


def test_shared_context_preserves_all_windows_facts_and_blockers_and_freezes_resume(fake, monkeypatch):
    strategy = activate(fake)
    records = {}
    facts = [add_fact(fake, monkeypatch, records, i) for i in range(2)]
    original = shared_context()
    before = deepcopy(original)
    research_policy = {"knowledge_sha256": "e" * 64}
    result = research.preplanning_context(fake, strategy, research_policy, 9, original)
    assert result["watched_windows"] == original["watched_windows"]
    assert len(result["watched_windows"]) == 16
    for key in ("reference", "editing_reference", "catalog", "source_cut_navigation",
                "source_range_navigation", "known_exhausted_slice_inputs", "craft_supplement"):
        assert result[key] == original[key]
    assert result["research_refinement_evidence"]["source_observations"] == facts
    assert result["actual_feedback"]["previous_goal_result"] == original["actual_feedback"]["previous_goal_result"]
    assert "model_value" not in result["actual_feedback"]["records"][0]
    assert "not obligations" in result["context_projection"]["instruction"]
    assert original == before
    writes = fake.writes
    add_fact(fake, monkeypatch, records, 2)  # Later received evidence belongs to the next draft, not this paid input.
    assert research.preplanning_context(fake, strategy, research_policy, 9, original) == result
    assert fake.writes == writes
    assert len(research.bound_source_facts(fake)) == 3
    saved = fake.data["artifacts"]["goal_research_projection_9"][0]
    assert Path(saved["path"]).is_file()
    assert fake.data["calls"][0]["status"] == "uncertain"


def test_repeated_projection_cannot_change_input_feedback_or_hide_fact_binding_changes(fake, monkeypatch):
    strategy = activate(fake)
    records = {}
    fact = add_fact(fake, monkeypatch, records, 0)
    original = shared_context()
    policy = {"knowledge_sha256": "e" * 64}
    research.preplanning_context(fake, strategy, policy, 9, original)
    changed = deepcopy(original)
    changed["actual_feedback"]["previous_goal_result"]["status"] = "fabricated_pass"
    with pytest.raises(ValueError, match="frozen_context_changed"):
        research.preplanning_context(fake, strategy, policy, 9, changed)
    records[fact["call_id"]][1]["uncertainties"] = []
    with pytest.raises(ValueError, match="original_binding_changed"):
        research.preplanning_context(fake, strategy, policy, 9, original)


def test_unparsed_failed_replies_never_gain_defaults_parsed_files_or_source_facts(fake, monkeypatch):
    strategy = activate(fake)
    old_bytes = {}
    for index in (69, 70):
        ident = "old_" + str(index)
        fake.data["calls"].append({"id": ident, "name": "semantic_slice_3_deadbeef", "status": "received"})
        folder = fake.output / "calls" / ident
        folder.mkdir(parents=True)
        path = folder / "protocol_failure.json"
        path.write_bytes(b'{"error":"uncertainties_required","model_text":"known failed reply"}\r\n')
        old_bytes[path] = path.read_bytes()
    monkeypatch.setattr(goal, "_call_value", lambda *args: pytest.fail("Unparsed failures are not typed facts."))
    result = research.preplanning_context(fake, strategy, {"knowledge_sha256": "e" * 64}, 9, shared_context())
    assert result["research_refinement_evidence"]["source_observations"] == []
    assert all(path.read_bytes() == content for path, content in old_bytes.items())
    assert not list((fake.output / "calls").rglob("parsed.json"))


def test_full_goal_validator_binds_strategy_projection_and_preplanning_context(tmp_path):
    state = setup(tmp_path)
    research_policy = research.enable(state.output, "Synthetic timing research.")
    state = goal.stage_state(state.output)
    state.set_artifact("goal_result_8", {"completed_files": []})
    strategy = research.enable_preplanning(state.output, "Synthetic ninth-round evidence strategy.")
    state = goal.stage_state(state.output)
    original = shared_context()
    projected = research.preplanning_context(state, strategy, research_policy, 9, original)
    goal.get_authorization(state)
    assert projected["research_refinement_evidence"]["source_observations"] == []
    assert state.data["request_count"] == strategy["activation_baseline_requests"]
    entry = state.data["artifacts"]["goal_research_projection_9"][0]
    value = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
    value["source_context_sha256"] = "f" * 64
    write_json(entry["path"], value)
    entry["sha256"] = json_sha(value)
    write_json(state.path, state.data)
    with pytest.raises(LibraryStopped, match="preplanning_source_context_changed"):
        goal.get_authorization(state)
