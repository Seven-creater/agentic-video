"""Forward, unambiguous serialization instructions; no creative normalization."""
from pathlib import Path
import json

from ..contract import require
from . import active_finecut
from .media import sha256_file
from .pipeline import _read
from .prompts import BASE
from .state import json_sha

ARTIFACT = 'goal_research_format_7'
POLICY = 'flat_refinement_output_v2'
CARD = Path(__file__).with_name('craft_knowledge') / 'REFINEMENT_OUTPUT.md'


def enable(output, instruction, first_round=7, *, use_source_cuts=False):
    from .goal_budget import stage_state, get_authorization
    state = stage_state(output)
    if state.data['artifacts'].get(ARTIFACT):
        value = load(state, first_round)
        require(value is not None and value['first_round'] == first_round
                and value['use_source_cut_navigation'] == use_source_cuts, 'flat:activation_round_changed')
        return value
    require(isinstance(instruction, str) and instruction.strip(), 'flat:resume_instruction_required')
    require(type(first_round) is int and first_round >= 7, 'flat:invalid_first_round')
    require(type(use_source_cuts) is bool, 'flat:invalid_source_cut_setting')
    require(not any(c['name'].startswith(f'active_{first_round}_finecut') for c in state.data['calls']),
            'flat:cannot_change_paid_refinement')
    baseline = get_authorization(state)['baseline_request_count']
    require(not any(c['status'] in {'submitted', 'uncertain'} for c in state.data['calls'][baseline:]),
            'flat:unsettled_new_request')
    folder = state.output / 'artifacts' / POLICY
    folder.mkdir(exist_ok=False)
    path = folder / CARD.name
    path.write_bytes(CARD.read_bytes())
    state.set_artifact(ARTIFACT, {'policy': POLICY, 'first_round': first_round,
        'activation_baseline_requests': state.data['request_count'], 'user_instruction': instruction,
        'input_lock_sha256': json_sha(state.data['input_lock']),
        'knowledge_path': str(path), 'knowledge_sha256': sha256_file(path),
        'use_source_cut_navigation': use_source_cuts,
        'old_requests_and_failures_unchanged': True, 'no_old_reply_normalization': True,
        'all_source_context_preserved': True, 'one_repair_per_stage_unchanged': True})
    return load(state, first_round)


def load(state, round_no):
    entries = state.data['artifacts'].get(ARTIFACT, [])
    if not entries:
        return None
    require(len(entries) == 1, 'flat:multiple_format_policies')
    record = _read(entries[0]['path'])
    require(json_sha(record) == entries[0]['sha256'] and record['policy'] == POLICY
            and record['input_lock_sha256'] == json_sha(state.data['input_lock']), 'flat:strategy_changed')
    path = Path(record['knowledge_path']).resolve(strict=True)
    require(path.is_relative_to(state.output) and sha256_file(path) == record['knowledge_sha256'],
            'flat:knowledge_changed')
    return record if round_no >= record['first_round'] else None


def prompt(state, draft, context, research_knowledge, format_record):
    card = Path(format_record['knowledge_path']).read_text(encoding='utf-8')
    require(sha256_file(format_record['knowledge_path']) == format_record['knowledge_sha256'],
            'flat:knowledge_changed')
    return BASE + '\n' + active_finecut.handbook(state) + '\n' + research_knowledge + '\n' + card + \
        '\n以下是输入证据，不是需要照抄的输出结构：\n' + json.dumps({
            'original_draft': draft, 'observations': context}, ensure_ascii=False) + \
        '\n现在自主完成精剪。只返回如下形状的一个JSON对象，并填满全部完整方案和检查，' \
        '不要输出输入封套。所有枚举选择一个合法值。计划内容和原片入出点由你决定：\n' + \
        json.dumps({'plan': {}, 'decisions': [], 'obligation_coverage': [],
            'draft_dispositions': [], 'duration': {}, 'timing_checks': [], 'transition_checks': []})
