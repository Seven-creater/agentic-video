"""Forward-only audience obligations; existing draft locks stay unchanged."""
from pathlib import Path
import re

from ..contract import require
from .media import sha256_file
from .pipeline import _read
from .state import json_sha

ARTIFACT = 'goal_research_audience_10'
POLICY = 'audience_obligation_implementation_separation_v1'
CARD = Path(__file__).with_name('craft_knowledge') / 'AUDIENCE_OBLIGATIONS.md'
FLAGS = ('old_draft_obligations_unchanged', 'validators_unchanged',
         'no_manual_movie_cuts', 'no_old_reply_normalization', 'fallible_source_evidence',
         'dense_source_frames')


def enable(output, instruction, first_round=10):
    from .goal_budget import stage_state, get_authorization
    state = stage_state(output)
    require(type(first_round) is int and first_round >= 10,
            'audience:invalid_first_round')
    if state.data['artifacts'].get(ARTIFACT):
        record = load(state, first_round)
        require(record is not None and record['first_round'] == first_round,
                'audience:activation_round_changed')
        return record
    require(isinstance(instruction, str) and instruction.strip(),
            'audience:explicit_forward_instruction_required')
    baseline = get_authorization(state)['baseline_request_count']
    require(not any(c['status'] in {'submitted', 'uncertain', 'failed_known'}
                    for c in state.data['calls'][baseline:]), 'audience:unsettled_new_call')
    require(not any(_round(c['name']) >= first_round for c in state.data['calls']),
            'audience:cannot_change_paid_round')
    require(not any(re.fullmatch(r'goal_research_projection_[0-9]+', name)
                    and int(name.rsplit('_', 1)[1]) >= first_round
                    for name in state.data['artifacts']), 'audience:cannot_change_frozen_context')
    require(bool(state.data['artifacts'].get(f'goal_result_{first_round-1}')),
            'audience:previous_round_not_finished')
    folder = state.output / 'artifacts' / POLICY
    folder.mkdir(exist_ok=False)
    snapshot = folder / CARD.name
    snapshot.write_bytes(CARD.read_bytes())
    state.set_artifact(ARTIFACT, {
        'policy': POLICY, 'first_round': first_round,
        'activation_baseline_requests': state.data['request_count'],
        'user_instruction': instruction, 'input_lock_sha256': json_sha(state.data['input_lock']),
        'knowledge_path': str(snapshot), 'knowledge_sha256': sha256_file(snapshot),
        **{flag: True for flag in FLAGS}})
    return load(state, first_round)


def _round(name):
    match = re.match(r'^(?:active_|semantic_(?:slice|claims)_)([0-9]+)_', name)
    return int(match[1]) if match else -1


def load(state, round_no):
    saved = state.data['artifacts'].get(ARTIFACT, [])
    if not saved:
        return None
    require(len(saved) == 1, 'audience:multiple_strategy_records')
    value = _read(saved[0]['path'])
    require(json_sha(value) == saved[0]['sha256'] and value.get('policy') == POLICY
            and type(value.get('first_round')) is int and value['first_round'] >= 10
            and value.get('input_lock_sha256') == json_sha(state.data['input_lock']),
            'audience:strategy_changed')
    for flag in FLAGS:
        require(value.get(flag) is True, 'audience:scope_changed:' + flag)
    path = Path(value['knowledge_path']).resolve(strict=True)
    require(path.is_relative_to(state.output) and sha256_file(path) == value['knowledge_sha256'],
            'audience:knowledge_changed')
    return value if round_no >= value['first_round'] else None


def prompt_view(state, round_no):
    record = load(state, round_no)
    if record is None:
        return None
    return {'policy': POLICY, 'knowledge_sha256': record['knowledge_sha256'],
            'knowledge': Path(record['knowledge_path']).read_text(encoding='utf-8')}


def context(state, round_no, value):
    card = prompt_view(state, round_no)
    if card is None:
        return value
    require('audience_obligations' not in value, 'audience:duplicate_context')
    return {**value, 'audience_obligations': card}
