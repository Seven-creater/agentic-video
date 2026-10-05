"""Serialization snapshots, never normalization of a failed creative response."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from omni_story.library import active_finecut, flat_refinement as flat, goal_budget as goal
from omni_story.library.state import LibraryStopped
from test_library_goal_budget import setup


def test_forward_format_policy_keeps_old_calls_and_resumes_idempotently(tmp_path):
    state = setup(tmp_path)
    old = deepcopy(state.data['calls'])
    record = flat.enable(state.output, 'synthetic explicit resumed Goal')
    before = state.path.read_bytes()
    assert flat.enable(state.output, 'do not rewrite permission') == record
    assert state.path.read_bytes() == before
    state = goal.stage_state(state.output)
    assert state.data['calls'] == old
    assert flat.load(state, 6) is None
    assert flat.load(state, 7) == record
    Path(record['knowledge_path']).write_text('changed serialization rule', encoding='utf-8')
    with pytest.raises(LibraryStopped):
        goal.get_authorization(state)


@pytest.mark.parametrize('round_no', [True, 6, 7.0])
def test_invalid_activation_round_has_no_knowledge_files(tmp_path, round_no):
    state = setup(tmp_path)
    with pytest.raises(ValueError, match='invalid_first_round'):
        flat.enable(state.output, 'synthetic explicit resumed Goal', first_round=round_no)
    assert not (state.output / 'artifacts' / flat.POLICY).exists()


def test_prompt_has_one_output_shape_and_preserves_all_input_evidence(tmp_path):
    state = setup(tmp_path)
    record = flat.enable(state.output, 'synthetic explicit resumed Goal')
    state = goal.stage_state(state.output)
    view = SimpleNamespace(data={'artifacts': {
        active_finecut.POLICY_ARTIFACT: state.data['artifacts'][goal.AUTHORIZATION]}})
    draft = {'segments': [{'segment_id': 'original_s'}], 'slots': [{'slot_id': 'original_slot'}]}
    evidence = {'watched_windows': [{'source_sha256': 'a' * 64, 'usable_ranges': [[2, 3]]}],
        'known_exhausted_slice_inputs': [{'original_call': 'old_unknown'}], 'reference_methods': ['m_1']}
    unchanged = deepcopy((draft, evidence))
    prompt = flat.prompt(view, draft, evidence, 'generic research knowledge only', record)
    input_start = prompt.index('{"original_draft":')
    observed = json.JSONDecoder().raw_decode(prompt[input_start:])[0]
    assert observed == {'original_draft': draft, 'observations': evidence}
    output_shape = json.loads(prompt[prompt.rindex('\n') + 1:])
    assert list(output_shape) == ['plan', 'decisions', 'obligation_coverage', 'draft_dispositions',
                                  'duration', 'timing_checks', 'transition_checks']
    assert 'purpose="action" is valid' in prompt
    assert 'purpose="action/result" is invalid' in prompt
    assert (draft, evidence) == unchanged
