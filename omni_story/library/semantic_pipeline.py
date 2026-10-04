"""Exact-slice auditing orchestration; no extra render or budget authorization."""
from __future__ import annotations

import json
from pathlib import Path

from . import semantic_audit as audit, semantic_prompts
from .media import prepare_window
from .state import LibraryStopped, json_sha, write_json


def enable_policy(state, requested):
    record = state.data['artifacts'].get('semantic_execution_policy')
    if isinstance(record, list):
        entry = record[-1] if record else None
        record = json.loads(Path(entry['path']).read_text(encoding='utf-8')) if entry else None
        if entry and json_sha(record) != entry['sha256']:
            raise LibraryStopped('recorded_semantic_execution_policy_modified')
    if requested and not record:
        if any(c['name'].startswith('plan_') for c in state.data['calls']):
            raise LibraryStopped('semantic_audit_cannot_reinterpret_existing_plans_or_reset_budgets')
        record = {'policy': audit.SEMANTIC_PROTOCOL, 'input_and_hard_budgets_unchanged': True,
                  'observation_before_intended_claims': True, 'all_selected_slices_audited': True,
                  'old_prompts_responses_and_verdicts_unchanged': True,
                  'model_evidence_is_not_human_truth': True}
        state.set_artifact('semantic_execution_policy', record)
    if record and record.get('policy') != audit.SEMANTIC_PROTOCOL:
        raise LibraryStopped('unsupported_recorded_semantic_execution_policy')
    return bool(record)


def plan_budget(state, round_no):
    # Persist the allocation so a lower remaining budget on resume cannot alter
    # a previously submitted prompt/digest or erase already charged work.
    name = f'semantic_plan_budget_{round_no}'
    saved = state.data['artifacts'].get(name)
    if isinstance(saved, list) and saved:
        record = saved[-1]
        value = json.loads(Path(record['path']).read_text(encoding='utf-8'))
        if json_sha(value) != record['sha256']:
            raise LibraryStopped('recorded_semantic_plan_budget_modified')
        return value['max_segments']
    if isinstance(saved, dict):
        return saved['max_segments']
    remaining = state.usage()['max_requests'] - state.usage()['requests']
    # Plan, blind, review, final selection: up to two calls each. Each slice
    # has two separate calls, each with the existing one-repair hard boundary.
    maximum = min(32, (remaining - 8) // 4)
    if maximum < 1:
        raise LibraryStopped('insufficient_budget_for_all_selected_slice_audits')
    state.set_artifact(name, {'max_segments': maximum, 'remaining_at_allocation': remaining,
        'reserved_per_segment': 4, 'reserved_plan_blind_review_selection': 8})
    return maximum


def fine_window_budget(state, round_no, max_fine, watched_count, remaining):
    name = f'semantic_search_budget_{round_no}'
    saved = state.data['artifacts'].get(name, [])
    if saved:
        record = saved[-1]
        value = json.loads(Path(record['path']).read_text(encoding='utf-8'))
        if json_sha(value) != record['sha256']:
            raise LibraryStopped('recorded_semantic_search_budget_modified')
        return value['window_cap']
    maximum = min(8, max_fine-watched_count, max(0, (remaining-14)//2))
    state.set_artifact(name, {'window_cap': maximum, 'remaining_at_allocation': remaining,
                             'reserved_search': 2, 'reserved_single_slice_and_output': 12})
    return maximum


def record_budget_reservation(state, value):
    saved = state.data['artifacts'].get('budget_reservation', [])
    if saved:
        previous = json.loads(Path(saved[-1]['path']).read_text(encoding='utf-8'))
        if previous == value:
            return
    state.set_artifact('budget_reservation', value)


def validate_plan_claims(plan, windows, maximum):
    if len(plan['segments']) > maximum:
        raise ValueError('semantic:plan_exceeds_reserved_slice_audit_budget')
    all_claims = []
    for segment in plan['segments']:
        if not segment.get('visual_claims'):
            raise ValueError('semantic:each_slice_needs_observable_visual_claim')
        all_claims.extend(audit.segment_required_claims(plan, segment))
    claim_ids = [row['claim_id'] for row in all_claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError('semantic:duplicate_claim_id_across_slices')
    audit.validate_caption_temporal_evidence(plan, windows)


def observe_selected_slices(glm, plan, source_map, windows, cache, output, round_no):
    folder = Path(output) / 'semantic_audit' / f'round_{round_no}'
    observations, checks, required = [], [], []
    by_window = {w['window_id']: w for w in windows}
    for segment in plan['segments']:
        source = source_map[segment['source_id']]
        # Existing validate_plan already requires completed usable fine ranges.
        # This descendant extraction never grants a new watched range.
        proxy = prepare_window(source, segment['source_in_s'], segment['source_out_s'], cache, fps=30)
        key = json_sha({'segment': segment['segment_id'], 'sha': source['sha256'],
                        'in': segment['source_in_s'], 'out': segment['source_out_s']})[:16]
        observation = glm.call(f'semantic_slice_{round_no}_{key}',
            semantic_prompts.slice_observation_prompt(segment, source, proxy), proxy['path'],
            lambda v: audit.validate_segment_observation(v, segment, source['sha256'], proxy),
            scope={k: proxy[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
        write_json(folder / f'{key}_observation.json', observation)
        claims = audit.segment_required_claims(plan, segment)
        hypotheses = by_window[segment['window_id']]['observation']['roles']
        checked = glm.call(f'semantic_claims_{round_no}_{key}',
            semantic_prompts.slice_claim_prompt(observation, claims, hypotheses), proxy['path'],
            lambda v: audit.validate_segment_claim_check(v, observation, claims))
        write_json(folder / f'{key}_claims.json', checked)
        observations.append(observation)
        checks.append(checked)
        required.extend(claims)
    required.extend(audit.output_required_claims(plan))
    manifest = {'protocol': audit.SEMANTIC_PROTOCOL, 'plan_sha256': json_sha(plan),
                'observations': observations, 'segment_checks': checks, 'required_claims': required,
                'limitations': ['Independent model observations remain fallible; no human quality verdict.']}
    write_json(folder / 'manifest.json', manifest)
    return manifest
