"""Explicit, forward-only research strategy for the resumed editing Goal."""
from pathlib import Path

from ..contract import require
from .state import json_sha
from .media import sha256_file
from .pipeline import _read

ARTIFACT = 'goal_research_6'
POLICY = 'evidence_timing_refinement_v1'
KNOWLEDGE = Path(__file__).with_name('craft_knowledge') / 'RESEARCH_TIMING.md'


def enable(output, instruction, first_round=6):
    from .goal_budget import stage_state, get_authorization
    state = stage_state(output)
    require(type(first_round) is int and first_round >= 5, 'research:invalid_first_round')
    if state.data['artifacts'].get(ARTIFACT):
        record = load(state, first_round)
        require(record is not None and record['first_round'] == first_round,
                'research:activation_round_changed')
        return record
    require(isinstance(instruction, str) and instruction.strip(), 'research:explicit_resume_instruction_required')
    require(not any(c['status'] in {'submitted', 'uncertain'}
                    for c in state.data['calls'][get_authorization(state)['baseline_request_count']:]),
            'research:unsettled_new_call')
    require(not any(c['name'].startswith(f'active_{first_round}_finecut') for c in state.data['calls']),
            'research:cannot_change_paid_refinement')
    folder = state.output / 'artifacts' / 'evidence_timing_research_v1'
    folder.mkdir(exist_ok=False)
    path = folder / 'RESEARCH_TIMING.md'
    path.write_bytes(KNOWLEDGE.read_bytes())
    record = {'policy': POLICY, 'first_round': first_round,
        'applies_from': f'active_{first_round}_finecut',
        'activation_baseline_requests': state.data['request_count'],
        'user_resume_instruction': instruction,
        'input_lock_sha256': json_sha(state.data['input_lock']),
        'knowledge_path': str(path), 'knowledge_sha256': sha256_file(path),
        'source_gate_before_render': True, 'model_estimated_times_not_human_truth': True,
        'paid_draft_and_old_knowledge_unchanged': True,
        'no_new_unique_fine_windows': True, 'no_numeric_request_ceiling': True,
        'no_manual_movie_cuts': True}
    state.set_artifact(ARTIFACT, record)
    return load(state, first_round)


def load(state, round_no):
    records = state.data['artifacts'].get(ARTIFACT, [])
    if not records:
        return None
    require(len(records) == 1, 'research:multiple_strategy_records')
    record = _read(records[0]['path'])
    require(json_sha(record) == records[0]['sha256'] and record['policy'] == POLICY,
            'research:strategy_record_changed')
    require(record['input_lock_sha256'] == json_sha(state.data['input_lock']), 'research:inputs_changed')
    path = Path(record['knowledge_path']).resolve(strict=True)
    require(path.is_relative_to(state.output) and sha256_file(path) == record['knowledge_sha256'],
            'research:knowledge_changed')
    return record if round_no >= record['first_round'] else None


def bound_source_facts(state):
    """Known typed model observations only, never the human frame audit or plot."""
    from .goal_budget import _call_value
    values = []
    seen = set()
    for call in state.data['calls']:
        if call['status'] != 'received' or not call['name'].startswith('semantic_slice_'):
            continue
        if not (state.output / 'calls' / call['id'] / 'parsed.json').is_file():
            continue
        request, value = _call_value(state.output, call)
        if not isinstance(value, dict) or not isinstance(value.get('evidence'), list):
            continue
        key = json_sha(value)
        if key in seen:
            continue
        seen.add(key)
        values.append({'call_id': call['id'], 'request_sha256': call['request_sha256'],
            'response_sha256': call['response_sha256'], 'observation_sha256': key,
            'media_sha256': request['media_sha256'],
            'original_observation': value, 'fallible_independent_model_observation': True})
    return values


def refinement_context(state, policy, round_no):
    name = f'goal_research_context_{round_no}'
    saved = state.data['artifacts'].get(name, [])
    if saved:
        require(len(saved) == 1, 'research:multiple_context_snapshots')
        value = _read(saved[0]['path'])
        require(json_sha(value) == saved[0]['sha256'], 'research:context_changed')
        validate_context(state, value, policy, round_no)
        return value
    value = {'policy': POLICY, 'round': round_no,
        'knowledge_sha256': policy['knowledge_sha256'],
        'source_observations': bound_source_facts(state),
        'instruction': 'These independent exact-source descriptions may contradict broad window labels. '
            'Keep contradictions explicit. Do not add film knowledge or infer actions between observed instants. '
            'Use this temporal evidence to choose essential moments, not entire source-event blocks. '
            'An original model observation can be wrong; new selected cuts receive independent checks.'}
    state.set_artifact(name, value)
    return value


def validate_context(state, value, policy, round_no):
    """Bind cached context to its strategy and unchanged original source replies."""
    from .goal_budget import _call_value
    require(value.get('policy') == POLICY and value.get('round') == round_no
            and value.get('knowledge_sha256') == policy['knowledge_sha256'],
            'research:context_strategy_binding_changed')
    calls = {call['id']: call for call in state.data['calls']}
    observations = value.get('source_observations')
    require(isinstance(observations, list), 'research:source_context_required')
    seen = set()
    for item in observations:
        call = calls.get(item.get('call_id'))
        require(call is not None and call['status'] == 'received'
                and call['name'].startswith('semantic_slice_'), 'research:source_context_call_missing')
        request, original = _call_value(state.output, call)
        require(item == {'call_id': call['id'], 'request_sha256': call['request_sha256'],
                'response_sha256': call['response_sha256'], 'observation_sha256': json_sha(original),
                'media_sha256': request['media_sha256'], 'original_observation': original,
                'fallible_independent_model_observation': True}
                and isinstance(original, dict) and isinstance(original.get('evidence'), list),
                'research:source_context_original_binding_changed')
        require(item['observation_sha256'] not in seen, 'research:duplicate_source_context')
        seen.add(item['observation_sha256'])
