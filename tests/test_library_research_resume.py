"""Forward policy tests on synthetic ledgers; no paid requests or real-run writes."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from omni_story.library import goal_budget as goal, research_resume as research
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_goal_budget import setup, register
from test_library_extension_budget import request
from test_library_execute import _read


def test_policy_is_forward_only_and_idempotent_with_old_history_intact(tmp_path):
    state = setup(tmp_path)
    old = deepcopy(state.data['calls'])
    frozen = {Path(row['path']): Path(row['path']).read_bytes()
              for row in goal.get_authorization(state)['protected_files']}
    record = research.enable(state.output, 'synthetic explicit research then Goal resume')
    before = state.path.read_bytes()
    assert research.enable(state.output, 'second resume does not rewrite permission') == record
    assert state.path.read_bytes() == before
    state = goal.stage_state(state.output)
    assert state.data['calls'] == old
    assert research.load(state, 5) is None
    assert research.load(state, 6) == record
    assert all(path.read_bytes() == content for path, content in frozen.items())
    with pytest.raises(ValueError, match='activation_round_changed'):
        research.enable(state.output, 'cannot move policy retrospectively', first_round=5)


@pytest.mark.parametrize('first_round', [True, 0, 4, 6.0])
def test_invalid_first_round_makes_no_strategy_files(tmp_path, first_round):
    state = setup(tmp_path)
    with pytest.raises(ValueError, match='invalid_first_round'):
        research.enable(state.output, 'synthetic explicit resume', first_round=first_round)
    assert not (state.output / 'artifacts/evidence_timing_research_v1').exists()


def test_paid_refinement_cannot_receive_a_new_prompt_policy(tmp_path):
    state = setup(tmp_path)
    register(state, 5)
    call, folder = state.begin_call('active_5_draft', request(100))
    state.complete_call(call, {'result': {'content': [{'type': 'text', 'text': json.dumps({'known_draft': True})}]}})
    write_json(folder / 'parsed.json', {'known_draft': True})
    call, _ = state.begin_call('active_5_finecut', request(101))
    state.complete_call(call, {'known_finecut': True})
    with pytest.raises(ValueError, match='cannot_change_paid_refinement'):
        research.enable(state.output, 'late strategy cannot reinterpret paid plan', first_round=5)
    assert not (state.output / 'artifacts/evidence_timing_research_v1').exists()


def test_unknown_new_call_blocks_activation_without_files(tmp_path):
    state = setup(tmp_path)
    register(state)
    call, _ = state.begin_call('active_5_draft', request(100))
    state.fail_call(call, 'synthetic lost response', uncertain=True)
    with pytest.raises((ValueError, LibraryStopped)):
        research.enable(state.output, 'synthetic explicit resume')
    assert not (state.output / 'artifacts/evidence_timing_research_v1').exists()


def test_knowledge_and_context_are_hash_bound_without_new_requests(tmp_path):
    state = setup(tmp_path)
    record = research.enable(state.output, 'synthetic explicit resume')
    state = goal.stage_state(state.output)
    context = research.refinement_context(state, record, 6)
    assert context['source_observations'] == []
    assert research.refinement_context(state, record, 6) == context
    assert state.data['request_count'] == record['activation_baseline_requests']
    with pytest.raises(LibraryStopped, match='goal_stage_artifact_immutable'):
        state.set_artifact('goal_research_context_6', {**context, 'round': 7})
    Path(record['knowledge_path']).write_text('changed knowledge', encoding='utf-8')
    with pytest.raises((ValueError, LibraryStopped)):
        goal.get_authorization(state)


def test_context_tamper_is_detected_by_full_goal_validation(tmp_path):
    state = setup(tmp_path)
    record = research.enable(state.output, 'synthetic explicit resume')
    state = goal.stage_state(state.output)
    research.refinement_context(state, record, 6)
    path = Path(state.data['artifacts']['goal_research_context_6'][0]['path'])
    write_json(path, {'invented_source_observations': True})
    with pytest.raises(LibraryStopped):
        goal.get_authorization(state)
