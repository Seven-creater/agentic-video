"""Forward obligation card protections, using temporary synthetic runs only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import audience_obligations as audience, goal_budget as goal, research_resume as research
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_goal_budget import setup
from test_library_preplanning import FakeState, replace_record, shared_context


@pytest.fixture
def fake(tmp_path, monkeypatch):
    state = FakeState(tmp_path)
    state.set_artifact('goal_result_9', {'status': 'stopped_protocol_failure'})
    state.data['calls'] = [{'id': 'old_004', 'name': 'coarse_old', 'status': 'uncertain'}]
    state.data['request_count'] = 1
    monkeypatch.setattr(goal, 'stage_state', lambda output: state)
    monkeypatch.setattr(goal, 'get_authorization', lambda state: {'baseline_request_count': 1})
    return state


def activate(state, **kwargs):
    return audience.enable(state.output, 'Synthetic forward audience obligations.', **kwargs)


def test_activation_is_forward_and_idempotent_without_changing_old_calls(fake):
    old = deepcopy(fake.data['calls'])
    record = activate(fake)
    assert audience.load(fake, 9) is None
    assert audience.load(fake, 10) == audience.load(fake, 11) == record
    assert record['dense_source_frames'] is True
    fake.data['calls'].append({'name': 'active_10_draft', 'status': 'received'})
    writes = fake.writes
    assert activate(fake) == record and fake.writes == writes
    assert fake.data['calls'][:len(old)] == old


@pytest.mark.parametrize('first_round', [9, True, 10.0])
def test_round_must_be_an_integer_at_least_ten(fake, first_round):
    before = deepcopy(fake.data)
    with pytest.raises(ValueError, match='invalid_first_round'):
        activate(fake, first_round=first_round)
    assert fake.data == before


@pytest.mark.parametrize('name', ['active_10_draft', 'active_10_finecut_repair',
    'semantic_slice_10_deadbeef', 'semantic_claims_10_deadbeef', 'active_11_draft'])
def test_no_paid_target_or_later_round_can_gain_the_card(fake, name):
    fake.data['calls'].append({'name': name, 'status': 'received'})
    before = deepcopy(fake.data)
    with pytest.raises(ValueError, match='cannot_change_paid_round'):
        activate(fake)
    assert fake.data == before
    assert not (fake.output / 'artifacts' / audience.POLICY).exists()


@pytest.mark.parametrize('status', ['submitted', 'uncertain', 'failed_known'])
def test_new_unsettled_calls_block_activation_but_old_unknown_is_preserved(fake, status):
    fake.data['calls'].append({'name': 'active_9_finecut', 'status': status})
    with pytest.raises(ValueError, match='unsettled_new_call'):
        activate(fake)
    assert fake.data['calls'][0]['status'] == 'uncertain'
    assert not fake.data['artifacts'].get(audience.ARTIFACT)


def test_finished_previous_round_and_unfrozen_context_are_required(fake):
    previous = fake.data['artifacts'].pop('goal_result_9')
    with pytest.raises(ValueError, match='previous_round_not_finished'):
        activate(fake)
    fake.data['artifacts']['goal_result_9'] = previous
    fake.set_artifact('goal_research_projection_10', {'synthetic': 'already frozen'})
    with pytest.raises(ValueError, match='cannot_change_frozen_context'):
        activate(fake)
    assert not fake.data['artifacts'].get(audience.ARTIFACT)


@pytest.mark.parametrize('flag', audience.FLAGS)
def test_scope_cannot_be_disabled_even_with_a_matching_artifact_hash(fake, flag):
    record = activate(fake)
    replace_record(fake, audience.ARTIFACT, {**record, flag: False})
    with pytest.raises(ValueError, match='scope_changed'):
        audience.load(fake, 10)


def test_snapshot_record_and_input_lock_are_bound(fake):
    record = activate(fake)
    replace_record(fake, audience.ARTIFACT, {**record, 'user_instruction': 'Changed'}, update_sha=False)
    with pytest.raises(ValueError, match='strategy_changed'):
        audience.load(fake, 10)
    replace_record(fake, audience.ARTIFACT, record)
    fake.data['input_lock']['reference_sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='strategy_changed'):
        audience.load(fake, 10)


def test_snapshot_bytes_and_containment_are_bound(fake, tmp_path):
    record = activate(fake)
    path = Path(record['knowledge_path'])
    original = path.read_bytes()
    path.write_bytes(b'changed generic card')
    with pytest.raises(ValueError, match='knowledge_changed'):
        audience.load(fake, 10)
    path.write_bytes(original)
    outside = tmp_path.parent / (tmp_path.name + '_audience_external.md')
    outside.write_bytes(original)
    try:
        replace_record(fake, audience.ARTIFACT, {**record, 'knowledge_path': str(outside)})
        with pytest.raises(ValueError, match='knowledge_changed'):
            audience.load(fake, 10)
    finally:
        outside.unlink()


def test_card_enters_before_projection_freeze_and_old_ninth_context_is_unchanged(fake, monkeypatch):
    record = activate(fake)
    original = shared_context()
    before = deepcopy(original)
    assert audience.context(fake, 9, original) is original
    forward = audience.context(fake, 10, original)
    assert forward['audience_obligations']['knowledge_sha256'] == record['knowledge_sha256']
    assert original == before
    monkeypatch.setattr(research, 'bound_source_facts', lambda state: [])
    strategy = {'policy': research.PREPLANNING_POLICY, 'knowledge_path': record['knowledge_path'],
                'knowledge_sha256': record['knowledge_sha256']}
    policy = {'knowledge_sha256': 'e' * 64}
    projected = research.preplanning_context(fake, strategy, policy, 10, forward)
    assert projected['audience_obligations'] == forward['audience_obligations']
    writes = fake.writes
    assert research.preplanning_context(fake, strategy, policy, 10, forward) == projected
    assert fake.writes == writes
    with pytest.raises(ValueError, match='frozen_context_changed'):
        research.preplanning_context(fake, strategy, policy, 10, original)


def test_full_goal_validator_checks_activation_and_projected_card(tmp_path):
    state = setup(tmp_path)
    research_policy = research.enable(state.output, 'Synthetic timing research.')
    state = goal.stage_state(state.output)
    state.set_artifact('goal_result_8', {'completed_files': []})
    strategy = research.enable_preplanning(state.output, 'Synthetic ninth-round evidence strategy.')
    state = goal.stage_state(state.output)
    state.set_artifact('goal_result_9', {'completed_files': []})
    activate(state)
    state = goal.stage_state(state.output)
    original = audience.context(state, 10, shared_context())
    research.preplanning_context(state, strategy, research_policy, 10, original)
    goal.get_authorization(state)
    entry = state.data['artifacts']['goal_research_projection_10'][0]
    projection = json.loads(Path(entry['path']).read_text(encoding='utf-8'))
    del projection['projected_context']['audience_obligations']
    write_json(entry['path'], projection)
    entry['sha256'] = json_sha(projection)
    write_json(state.path, state.data)
    with pytest.raises(LibraryStopped, match='audience_projection_binding_changed'):
        goal.get_authorization(state)
