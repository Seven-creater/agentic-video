"""Refine two immutable existing edits locally, with model-owned microcuts.

Preparation and execution are separate. Execution needs a later explicit grant
and the session's official MCP; this module never resumes a paused Goal itself.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..contract import ids, require, rows, text
from . import contracts, semantic_audit as audit, semantic_prompts
from . import slot_finecut_contracts as local
from .active_finecut import economy_manifest, economy_prompt, validate_economy_review
from .editing import validate_method_review
from .media import inventory_sources, prepare_window as _prepare_window, sha256_file
from .pipeline import CodexMCP, _read, _window_context
from .render import compile_library_plan, render_library_video, validate_caption_layout
from .research_readability import source_counterevidence
from .semantic_pipeline import validate_plan_claims
from .slot_finecut_baselines import delivery_paths, load_preparation
from .state import LibraryStopped, json_sha, write_json

POLICY = 'slot_finecut_comparison_v1'
AUDIT_ROUNDS = {0: 20, 3: 21}
_MEDIA_MUTEX = threading.RLock()


def prepare_window(*args, **kwargs):
    # The existing cache writer has a fixed .part path per spec. Prevent two
    # lanes creating an identical proxy simultaneously; model calls stay parallel.
    with _MEDIA_MUTEX:
        return _prepare_window(*args, **kwargs)


def _call(glm, name, prompt, media, validator, **options):
    resolver = getattr(glm.state, 'derived_fact', None) if hasattr(glm, 'state') else None
    derived = resolver(name, media_sha256=sha256_file(media)) if resolver else None
    if derived is not None:
        validator(derived)
        return derived
    for method in ('derived_assembly', 'derived_proposal_retiming'):
        resolver = getattr(glm.state, method, None) if hasattr(glm, 'state') else None
        derived = resolver(name, media_sha256=sha256_file(media)) if resolver else None
        if derived is not None:
            validator(derived)
            return derived
    lineage = _read(Path(media).parent / 'lineage.json')
    options.setdefault('scope', {k: lineage[k] for k in
                                ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
    return glm.call(name, prompt, media, validator, **options)


def _save(path, value):
    """Do not rewrite completed stage evidence on resume."""
    path = Path(path)
    if path.exists():
        require(_read(path) == value, 'slot_finecut:recorded_stage_changed:' + path.name)
    else:
        write_json(path, value)
    return value


def _view(parent):
    return {key: parent[key] for key in ('baseline_id', 'sha256', 'duration_s',
                                        'provenance', 'output_mapping')}


def _choices(outline, proposals, selections):
    wanted = ids(outline['slots'], 'slot_id', 'slot_finecut/outline')
    require(ids(selections, 'slot_id', 'slot_finecut/selections') == wanted,
            'slot_finecut:selection_must_cover_all_slots')
    selected = {row['slot_id']: row['candidate_id'] for row in selections}
    by_slot = {row['slot_id']: row for row in proposals}
    result = []
    for slot in outline['slots']:
        candidates = by_slot[slot['slot_id']]['candidates']
        choice = next((c for c in candidates if c['candidate_id'] == selected[slot['slot_id']]), None)
        require(choice is not None, 'slot_finecut:unknown_selected_candidate')
        result.append((slot, choice))
    return result


def validate_assembly(value, parent, outline, proposals, catalog, windows, reference, methods,
                      *, allow_typographic_target_variants=False, retiming_operations=()):
    """Bind GLM's final EDL exactly to its local proposals and original meanings."""
    require(value.get('baseline_id') == parent['baseline_id'] and value.get('parent_sha256') == parent['sha256'],
            'slot_finecut:assembly_parent_changed')
    choices = _choices(outline, proposals, value.get('selections'))
    plan = value.get('plan')
    contracts.validate_plan(plan, catalog, windows, reference['sha256'], reference['duration_s'],
        reference_audio_stream_index=reference.get('audio_stream_index'), editing_reference=methods)
    compile_library_plan(catalog, plan, fps=plan['fps'], width=plan['width'], height=plan['height'])
    validate_caption_layout(plan, plan['width'], plan['height'])
    validate_plan_claims(plan, windows, 32)
    require([s['slot_id'] for s in plan['slots']] == [s['slot_id'] for s in outline['slots']],
            'slot_finecut:assembly_slot_order_changed')
    for slot, final in zip(outline['slots'], plan['slots'], strict=True):
        unchanged = final['intended_takeaway'] == slot['intended_takeaway']
        typographic = allow_typographic_target_variants and final['intended_takeaway'].translate(
            str.maketrans('', '', '“”')) == slot['intended_takeaway'].translate(str.maketrans('', '', '“”'))
        require(unchanged or typographic,
                'slot_finecut:original_slot_obligation_changed')
    expected = [(slot, candidate, index, op) for slot, candidate in choices
                for index, op in enumerate(candidate['operations'])]
    bindings = rows(value.get('segment_bindings'), 'slot_finecut/segment_bindings')
    require(len(bindings) == len(plan['segments']) == len(expected), 'slot_finecut:operation_count_changed')
    for binding, segment, (slot, candidate, index, operation) in zip(bindings, plan['segments'], expected, strict=True):
        require(binding == {'segment_id': segment['segment_id'], 'slot_id': slot['slot_id'],
                            'candidate_id': candidate['candidate_id'], 'operation_index': index},
                'slot_finecut:operation_binding_changed')
        original = parent['provenance'][operation['parent_segment_index']]
        require(segment['slot_id'] == slot['slot_id'] and all(segment[key] == original[key]
                for key in ('source_id', 'window_id')) and segment['role_ids'] == original['role_ids'],
                'slot_finecut:source_or_identity_changed')
        retimable = (slot['slot_id'], candidate['candidate_id'], index) in retiming_operations
        for key in ('source_in_s', 'source_out_s') + (() if retimable else ('speed', 'freeze_tail_s')):
            require(segment.get(key, 0) == operation[key], 'slot_finecut:proposed_operation_changed:' + key)
        if retimable:
            for essential in operation['essential_intervals']:
                tail = essential['continues_in_tail_frame']
                exposure = (essential['source_end_s']-essential['source_start_s']) / segment['speed'] + \
                    (segment.get('freeze_tail_s', 0) if tail else 0)
                require(exposure + local.EPSILON >= essential['min_readable_s'],
                        'slot_finecut:final_model_retiming_still_short')
    timing = rows(value.get('timing_checks'), 'slot_finecut/timing_checks')
    require([r.get('segment_id') for r in timing] == [s['segment_id'] for s in plan['segments']],
            'slot_finecut:timing_must_cover_final_segments')
    for check, segment, (_, _, _, operation) in zip(timing, plan['segments'], expected, strict=True):
        core = {c['claim_id'] for c in segment['visual_claims']}
        mappings = rows(check.get('essential_claims'), 'slot_finecut/essential_claims')
        require([r.get('essential_interval_index') for r in mappings] == list(range(len(operation['essential_intervals']))),
                'slot_finecut:all_essential_intervals_need_claims')
        covered = set()
        for row in mappings:
            links = row.get('claim_ids')
            require(isinstance(links, list) and links and len(set(links)) == len(links) and set(links) <= core,
                    'slot_finecut:essential_unknown_core_claim')
            covered.update(links)
        require(covered == core, 'slot_finecut:core_claim_missing_essential_timing')
    transitions = rows(value.get('transition_checks'), 'slot_finecut/transitions', nonempty=False)
    expected_pairs = [(a['segment_id'], b['segment_id']) for a, b in zip(plan['segments'], plan['segments'][1:])]
    require([(r.get('from_segment_id'), r.get('to_segment_id')) for r in transitions] == expected_pairs,
            'slot_finecut:adjacent_transitions_incomplete')
    for transition in transitions:
        text(transition.get('relation'), 'slot_finecut/transition_relation')
        require(transition.get('status') in {'planned', 'unresolved'}, 'slot_finecut:transition_not_quality_pass')
    for limitation in rows(value.get('limitations'), 'slot_finecut/limitations', nonempty=False):
        text(limitation, 'slot_finecut/limitation')
    return value


def assembly_prompt(parent, outline, proposals, catalog, windows, reference, methods, knowledge):
    instruction = (local.ROOT_OUTPUT_INSTRUCTION + '只返回一个JSON对象。把逐slot候选放回整片上下文，自主每slot选择一个候选。'
        '保持你本轮划分的slot顺序与intended_takeaway原文；每个已选operation对应一段最终EDL，'
        '按候选中的顺序执行，不额外拼接未提出的原片切点或速度。不要只选局部最漂亮而破坏前后呼应。'
        '允许整片或个别slot比原版更长，重要信息看清优先；不设置统一时长缩减比例。'
        '需返回完整标准plan，包含visual_claims及所有参考剪法editing_bindings；未验证手法标未知。'
        'source/window/role_ids沿用operation引用的parent_segment，不从影片常识补动作。'
        '分段恒速不等于平滑速度坡道，尾帧停留不等于新动作。'
        'plan必须是完整JSON对象，不是字符串。枚举字段从allowed_enum_values选一个值；模板值仅为合法示例。'
        'planned的editing_binding必须有非空segment_ids；unavailable/unverifiable必须有非空limitations。'
        '所有原片时间、速度、停留及曝光为number；ID列表为list，不能复制说明文字。\n' + knowledge + '\n')
    payload = {
        'parent': _view(parent), 'outline': outline, 'local_proposals': proposals,
        'catalog': {'sources': [{k: s[k] for k in ('source_id', 'sha256', 'duration_s', 'audio_stream_index')}
                               for s in catalog['sources']]},
        'watched_windows': [_window_context(w, include_speech=False) for w in windows],
        'reference': reference['cached_reading'], 'editing_reference': methods,
        'plan_fields': {'reference_sha256': reference['sha256'], 'focus_role_id': '全片焦点人物ID',
            'focus_role_bindings': [{'window_id': '已看窗口', 'role_id': '该窗口已确认角色ID', 'identity_evidence': '观测依据'}],
            'slots': [{'slot_id': '本轮ID', 'intended_takeaway': 'outline原文', 'segment_ids': ['最终段ID']}],
            'segments': [{'segment_id': '最终段ID', 'slot_id': '本轮ID', 'source_id': '父operation源',
                'window_id': '父operation窗口', 'source_in_s': '选中operation原值', 'source_out_s': '原值',
                'role_ids': ['父operation角色'], 'speed': '选中operation原值', 'freeze_tail_s': '原值',
                'framing': 'fit', 'look': 'none',
                'visual_claims': [{'claim_id': '全片唯一ID', 'kind': 'visual_action',
                                   'description': '待原片独立核验的画面主张'}]}],
            'editing_bindings': [{'method_id': 'methods中ID', 'status': 'planned',
                'segment_ids': ['最终段ID'], 'intended_relation': '表达关系', 'operation': '实际执行手法',
                'verification': '成片应看到什么', 'limitations': []}],
            'audio_mode': 'reference', 'source_gain_db': 0, 'reference_gain_db': 0,
            'reference_audio': {'stream_index': reference.get('audio_stream_index'), 'start_s': 0,
                                'end_s': reference['duration_s'], 'loop': True},
            'width': parent['render_input']['compiled']['width'], 'height': parent['render_input']['compiled']['height'],
            'fps': parent['render_input']['compiled']['fps'], 'limitations': []},
        'response_contract': {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
            'selections': [{'slot_id': '本轮slot ID', 'candidate_id': '该slot候选ID'}],
            'segment_bindings': [{'segment_id': '最终段ID', 'slot_id': '本轮slot ID',
                                  'candidate_id': '所选候选ID', 'operation_index': 0}],
            'timing_checks': [{'segment_id': '最终段ID', 'essential_claims': [
                {'essential_interval_index': 0, 'claim_ids': ['本段visual_claims ID']}]}],
            'transition_checks': [{'from_segment_id': '相邻前段', 'to_segment_id': '相邻后段',
                                   'relation': '实际画面应建立的关系', 'status': 'planned'}],
            'limitations': []}}
    payload['response_contract']['plan'] = payload.pop('plan_fields')
    payload['allowed_enum_values'] = {'framing': ['fit', 'crop'], 'look': ['none', 'grayscale'],
        'visual_claim_kind': ['visual_action', 'visual_outcome', 'identity'],
        'editing_binding_status': ['planned', 'unavailable', 'unverifiable'],
        'audio_mode': ['reference', 'source', 'mix', 'silent']}
    return instruction + json.dumps(payload, ensure_ascii=False)


def executable_identity(plan):
    return {'segments': [{k: s.get(k, {'freeze_tail_s': 0, 'caption': None,
                     'framing': 'fit', 'look': 'none'}.get(k)) for k in
                     ('source_id', 'window_id', 'source_in_s', 'source_out_s', 'speed',
                      'freeze_tail_s', 'caption', 'framing', 'look')} for s in plan['segments']],
            'output': {k: plan.get(k) for k in ('fps', 'width', 'height', 'audio_mode',
                      'source_gain_db', 'reference_gain_db', 'reference_audio')}}


def timing_counterevidence(assembly, outline, proposals, source_manifest, parent):
    """Check each proposed exposure against independent facts, never infer action."""
    choices = _choices(outline, proposals, assembly['selections'])
    operations = [op for _, candidate in choices for op in candidate['operations']]
    by_segment = {o['segment_id']: o for o in source_manifest['observations']}
    checks = {c['segment_id']: {r['claim_id']: r for r in c['claim_checks']}
              for c in source_manifest['segment_checks']}
    blockers = []
    fps = assembly['plan']['fps']
    for segment, operation, timing in zip(assembly['plan']['segments'], operations, assembly['timing_checks'], strict=True):
        observation = by_segment[segment['segment_id']]
        evidence = {e['evidence_id']: e for e in observation['evidence']}
        duration = (segment['source_out_s']-segment['source_in_s']) / segment['speed']
        quantized_motion = round(duration*fps)/fps
        for link in timing['essential_claims']:
            essential = operation['essential_intervals'][link['essential_interval_index']]
            start, end = essential['source_start_s'], essential['source_end_s']
            for cid in link['claim_ids']:
                claim = checks[segment['segment_id']][cid]
                intervals = []
                for eid in claim['evidence_ids']:
                    row = evidence[eid]
                    if row['kind'] not in audit.VISUAL_KINDS:
                        continue
                    a = max(start, segment['source_in_s']+row['local_start_s'])
                    b = min(end, segment['source_in_s']+row['local_end_s'])
                    if a < b:
                        intervals.append((a, b))
                # Different evidence can overlap; count the visible interval once.
                merged = []
                for a, b in sorted(intervals):
                    if merged and a <= merged[-1][1]:
                        merged[-1][1] = max(merged[-1][1], b)
                    else:
                        merged.append([a, b])
                exposure = sum(max(0, min(quantized_motion, (b-segment['source_in_s'])/segment['speed']) -
                    min(quantized_motion, (a-segment['source_in_s'])/segment['speed'])) for a, b in merged)
                tail = essential.get('continues_in_tail_frame', False)
                if tail and any(abs(b-segment['source_out_s']) < .001 for _, b in merged):
                    exposure += round(segment.get('freeze_tail_s', 0)*fps)/fps
                if claim['status'] != 'supported' or exposure + 1e-9 < essential['min_readable_s']:
                    blockers.append({'segment_id': segment['segment_id'], 'claim_id': cid,
                        'essential_interval_index': link['essential_interval_index'],
                        'independent_visual_exposure_s': exposure, 'model_min_readable_s': essential['min_readable_s'],
                        'reason': 'Independent visible evidence does not meet the model-estimated exposure proposal.'})
    return blockers


def observe_final_slices(glm, plan, catalog, windows, cache, folder, round_no):
    """Finish neutral source facts for ALL slices before assessing any claims."""
    sources = {s['source_id']: s for s in catalog['sources']}
    by_window = {w['window_id']: w for w in windows}
    facts, checks, required, pending = [], [], [], []
    for segment in plan['segments']:
        source = sources[segment['source_id']]
        proxy = prepare_window(source, segment['source_in_s'], segment['source_out_s'], cache, fps=30)
        key = json_sha({'segment': segment['segment_id'], 'sha': source['sha256'],
                        'in': segment['source_in_s'], 'out': segment['source_out_s']})[:16]
        observation = _call(glm, f'semantic_slice_{round_no}_{key}',
            semantic_prompts.explicit_slice_observation_prompt(segment, source, proxy), proxy['path'],
            lambda v, s=segment, src=source, p=proxy: audit.validate_segment_observation(v, s, src['sha256'], p),
            scope={k: proxy[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
        _save(folder / (key + '_observation.json'), observation)
        facts.append(observation)
        pending.append((segment, proxy, key, observation))
    for segment, proxy, key, observation in pending:
        claims = audit.segment_required_claims(plan, segment)
        roles = by_window[segment['window_id']]['observation']['roles']
        prompt = semantic_prompts.slice_claim_prompt(observation, claims, roles).replace(
            'supported|partial|unsupported|unverifiable', 'unverifiable') + \
            '\nstatus从supported、partial、unsupported、unverifiable选一个值，模板值仅示例；' \
            'supported需要有效证据ID列表；非supported必须有非空limitations。'
        check = _call(glm, f'semantic_claims_{round_no}_{key}', prompt, proxy['path'],
            lambda v, o=observation, c=claims: audit.validate_segment_claim_check(v, o, c))
        _save(folder / (key + '_claims.json'), check)
        checks.append(check)
        required.extend(claims)
    required.extend(audit.output_required_claims(plan))
    return {'protocol': audit.SEMANTIC_PROTOCOL, 'plan_sha256': json_sha(plan),
            'observations': facts, 'segment_checks': checks, 'required_claims': required,
            'comparison_mode': 'all_independent_facts_before_individual_comparisons',
            'limitations': ['Model observations and readability estimates remain fallible.']}


def _parent_source(parent, folder):
    return inventory_sources(parent['path'], folder)['sources'][0]


def record_forward_parent_navigation_contract(state, user_instruction):
    """Append a contract for unsubmitted navigation; never revalidate old facts."""
    state.assert_protected()
    key = local.FORWARD_NAVIGATION_POLICY['version']
    entries = state.data['artifacts'].get(key)
    if entries:
        _forward_parent_navigation(state, '__unsubmitted_navigation__')
        return _read(entries[0]['path'])
    require(bool(user_instruction.strip()), 'slot_finecut:navigation_instruction_required')
    require(all(c['status'] == 'received' for c in state.data['calls'][state.authorization['baseline_request_count']:]),
            'slot_finecut:navigation_activation_requires_settled_calls')
    value = {'policy': key, 'task_id': state.data['task_id'],
             'baseline_request_count': len(state.data['calls']), 'prefix_calls_sha256': json_sha(state.data['calls']),
             'contract': local.FORWARD_NAVIGATION_POLICY, 'contract_sha256': json_sha(local.FORWARD_NAVIGATION_POLICY),
             'knowledge_sha256': state.authorization['knowledge_sha256'], 'user_instruction': user_instruction,
             'protocol_limitations': ['Preliminary point observations have unknown duration and cannot prove action completion or exposure.',
                 'Request bindings are program metadata; model reports remain intact and fallible.',
                 'Exact source and actual-output evidence gates remain independent and unchanged.']}
    state.set_artifact(key, value)
    return value


def _forward_parent_navigation(state, stage):
    state._reload()
    key = local.FORWARD_NAVIGATION_POLICY['version']
    entries = state.data['artifacts'].get(key)
    if not entries:
        return False
    require(len(entries) == 1, 'slot_finecut:forward_navigation_one_policy')
    value = _read(entries[0]['path'])
    count = value['baseline_request_count']
    require(json_sha(value) == entries[0]['sha256'] and value['policy'] == key
            and value['task_id'] == state.data['task_id']
            and value['contract'] == local.FORWARD_NAVIGATION_POLICY
            and value['contract_sha256'] == json_sha(local.FORWARD_NAVIGATION_POLICY)
            and value['knowledge_sha256'] == state.authorization['knowledge_sha256']
            and type(count) is int and state.authorization['baseline_request_count'] <= count <= len(state.data['calls'])
            and json_sha(state.data['calls'][:count]) == value['prefix_calls_sha256'],
            'slot_finecut:forward_navigation_policy_changed')
    previous = next((i for i, c in enumerate(state.data['calls']) if c['name'] == stage), None)
    return previous is None or previous >= count


def _result_binding(state, parent):
    if state.data['artifacts'].get('sf_independent_slot_recovery_v1'):
        return 'result_independent_resume.json', f'sf_result_{parent["round"]}_independent_resume'
    if parent['round'] == 3 and state.data['artifacts'].get('sf_source_feedback_replan_v1_3'):
        return 'result_source_feedback_resume.json', 'sf_result_3_source_feedback_resume'
    if parent['round'] == 3 and state.data['artifacts'].get('sf_request_bound_assembly_v1_3'):
        return 'result_assembly_bound_resume.json', 'sf_result_3_assembly_bound_resume'
    if parent['round'] == 0 and state.data['artifacts'].get('sf_request_bound_proposal_retiming_v1_0'):
        return 'result_timing_bound_resume.json', 'sf_result_0_timing_bound_resume'
    if state.data['artifacts'].get(f'sf_parent_point_navigation_v1_{parent["round"]}'):
        return 'result_point_navigation_resume.json', f'sf_result_{parent["round"]}_point_navigation_resume'
    resumed = parent['round'] == 0 and state.data['artifacts'].get('sf_request_bound_parent_facts_v1')
    return ('result_metadata_bound_resume.json', 'sf_result_0_metadata_bound_resume') if resumed else \
        ('result.json', f'sf_result_{parent["round"]}')


def _run_parent(glm, state, preparation, parent, folder, methods, knowledge):
    result_name, result_key = _result_binding(state, parent)
    result_path = folder / result_name
    if result_path.exists():
        result = _read(result_path)
        state._reload()
        entries = state.data['artifacts'].get(result_key, [])
        require(len(entries) == 1, 'slot_finecut:completed_result_binding_missing')
        receipt = _read(entries[0]['path'])
        require(json_sha(receipt) == entries[0]['sha256'] and
                sha256_file(result_path) == receipt['result_sha256'] and
                result['baseline_id'] == parent['baseline_id'] and result['parent_sha256'] == parent['sha256'],
                'slot_finecut:completed_result_changed')
        for row in result['completed_files']:
            require(sha256_file(row['path']) == row['sha256'], 'slot_finecut:completed_output_changed')
        return result
    folder.mkdir(parents=True, exist_ok=True)
    reference = preparation['reference']
    catalog = _read(state.output / 'catalog/inventory.json')
    windows = parent['allowed_windows']
    cache = state.output / 'media_cache'
    source = _parent_source(parent, folder / 'parent_catalog')
    parent_media = prepare_window(source, 0, parent['duration_s'], cache, fps=30)
    round_no = parent['round']
    from .slot_finecut_budget import blocked_source_scopes
    blocked_navigation = blocked_source_scopes(state)
    state._reload()
    replacement = state.data['artifacts'].get('sf_parallel_execution_v1') and round_no == 0
    outline_stage = f'sf_{round_no}_outline_v2' if replacement else f'sf_{round_no}_outline'
    outline = _call(glm, outline_stage, local.slots_prompt(parent, reference['cached_reading']),
                       parent_media['path'], lambda v: local.validate_slots(v, parent))
    _save(folder / 'outline.json', outline)
    proposals = []
    for slot in outline['slots']:
        key = json_sha({'parent': parent['sha256'], 'slot': slot})[:16]
        proxy = prepare_window(source, slot['start_s'], slot['end_s'], cache, fps=30)
        stage = f'sf_{round_no}_facts_{key}'
        resolver = getattr(state, 'derived_parent_navigation', None)
        navigation = resolver(stage, media_sha256=sha256_file(proxy['path'])) if resolver else None
        if navigation is not None:
            local.validate_parent_navigation(navigation, parent, slot)
            facts = navigation['model_report']
            _save(folder / 'slots' / key / 'navigation.json', navigation)
        else:
            forward = _forward_parent_navigation(state, stage)
            if forward:
                lineage = _read(Path(proxy['path']).parent / 'lineage.json')
                scope = {k: lineage[k] for k in ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
                digest = sha256_file(proxy['path'])
                facts = _call(glm, stage, local.navigation_facts_prompt(parent, slot), proxy['path'],
                    lambda v, s=slot: local.parent_navigation(v, parent, s, media_sha256=digest,
                                                            observation_scope=scope))
                navigation = local.parent_navigation(facts, parent, slot, media_sha256=digest, observation_scope=scope)
                _save(folder / 'slots' / key / 'navigation.json', navigation)
            else:
                facts = _call(glm, stage, local.neutral_facts_prompt(parent, slot), proxy['path'],
                                lambda v, s=slot: local.validate_facts(v, parent, s))
                _save(folder / 'slots' / key / 'facts.json', facts)
        relevant_indices = [i for i, p in enumerate(parent['provenance'])
                            if p['output_in_s'] < slot['end_s'] and slot['start_s'] < p['output_out_s']]
        window_ids = {parent['provenance'][i]['window_id'] for i in relevant_indices}
        relevant_windows = [w for w in windows if w['window_id'] in window_ids]
        # Show actual neighboring OUTPUT context, while facts keep slot-local time.
        context_start, context_end = max(0, slot['start_s']-1), min(parent['duration_s'], slot['end_s']+1)
        context = prepare_window(source, context_start, context_end, cache, fps=30)
        prompt = local.proposal_prompt(_view(parent), slot, facts,
                                      [_window_context(w, include_speech=False) for w in relevant_windows],
                                      navigation=navigation)
        prompt += '\n' + json.dumps({'actual_context_media_parent_start_s': context_start,
            'actual_context_media_parent_end_s': context_end,
            'slot_media_local_start_s': slot['start_s']-context_start,
            'slot_media_local_end_s': slot['end_s']-context_start,
            'instruction': '提供片段含邻接输出画面；facts时间仍从slot本身0秒开始。知识卡为本次锁定版本。'}, ensure_ascii=False)
        prompt += '\n' + json.dumps({'mechanical_no_replay_source_scopes': blocked_navigation,
            'instruction': '这些原范围已因未知请求或耗尽格式修复禁止再观察；不是创作切点建议。'}, ensure_ascii=False)
        # Lock knowledge, rather than silently adopting a changed installed card.
        prompt = prompt.replace(local.HANDBOOK.read_text(encoding='utf-8'), knowledge)
        timing_entries = state.data['artifacts'].get('sf_request_bound_proposal_retiming_v1_0', [])
        timing_record = _read(timing_entries[0]['path']) if timing_entries and round_no == 0 else None
        timing_stage = timing_record is not None and timing_record['stage'] == f'sf_{round_no}_proposal_{key}'
        proposal = _call(glm, f'sf_{round_no}_proposal_{key}', prompt, context['path'],
            lambda v, s=slot, f=facts, n=navigation: local.validate_proposal(
                v, parent, s, f, relevant_windows, navigation=n, enforce_readability=not timing_stage))
        _save(folder / 'slots' / key / ('proposal_timing_navigation.json' if timing_stage else 'proposal.json'), proposal)
        proposals.append(proposal)
    _save(folder / 'proposals.json', proposals)
    retiming = tuple((r['slot_id'], r['candidate_id'], r['operation_index'])
                    for r in timing_record['retiming_operations']) if timing_record is not None else ()
    assembly_instructions = ''
    if retiming:
        timing_context = {k: timing_record[k] for k in
                          ('mechanical_shortfalls', 'retiming_operations', 'protocol_limitations')}
        assembly_instructions = '\n' + json.dumps({'request_bound_proposal_retiming': timing_context,
            'instruction': '只有retiming_operations列出的操作仍存在模型自定曝光短缺。原片in/out、顺序、角色和表达目标不改。'
                '你可以在最终plan中自行调整这些操作的speed或freeze_tail_s，以满足原essential_intervals的min_readable_s。'
                '程序不替你选速度或停留，不得减小原min_readable_s，不得把hold当作补出缺失动作。'
                '其它操作保持原提案。最终真实视觉曝光仍要独立源片及成片核验。'}, ensure_ascii=False)
    assembly_bound = round_no == 3 and bool(state.data['artifacts'].get('sf_request_bound_assembly_v1_3'))
    assembled = _call(glm, f'sf_{round_no}_assemble',
        assembly_prompt(parent, outline, proposals, catalog, windows, reference, methods, knowledge) + '\n' +
        json.dumps({'mechanical_no_replay_source_scopes': blocked_navigation}, ensure_ascii=False) + assembly_instructions,
        parent_media['path'], lambda v: validate_assembly(v, parent, outline, proposals, catalog, windows,
            reference, methods, allow_typographic_target_variants=assembly_bound, retiming_operations=retiming))
    feedback_entries = state.data['artifacts'].get('sf_source_feedback_replan_v1_3', []) if round_no == 3 else []
    if feedback_entries:
        from . import slot_source_feedback as feedback
        policy = feedback.read_validate(state.output, state.data, state.authorization)
        _save(folder / 'assembly_before_source_feedback.json', assembled)
        recovering = bool(state.data['artifacts'].get('sf_independent_slot_recovery_v1'))
        if recovering:
            from . import slot_local_replan as local_replan
            local_slot = outline['slots'][-1]
            local_media = prepare_window(source, local_slot['start_s'], local_slot['end_s'], cache, fps=30)
            local_replan.validate_input_scope({k: local_media[k] for k in
                ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}, policy, parent)
            replanned_local = _call(glm, 'sf_3_local_source_replan_v2',
                local_replan.prompt(policy, parent, catalog, windows, facts, navigation=navigation, knowledge=knowledge),
                local_media['path'], lambda v: local_replan.validate_replan(v, policy, parent, catalog, windows,
                    reference, methods, facts, navigation=navigation))
            _save(folder / 'local_source_replan.json', replanned_local)
            if replanned_local['status'] == 'unavailable':
                return _finish(state, folder, parent, status='stopped_local_replan_unavailable',
                    model_quality_gate_passed=False, limitations=replanned_local['limitations'])
            proposals = local_replan.revised_full_proposals(replanned_local, policy)
            assembled = local_replan.replacement_assembly(replanned_local, policy)
            _save(folder / 'proposals_after_local_replan.json', proposals)
        else:
            replanned = _call(glm, feedback.STAGE,
                feedback.prompt(policy, parent, catalog, windows, reference, methods, knowledge, facts, navigation=navigation),
                parent_media['path'], lambda v: feedback.validate_replan(v, policy, parent, catalog, windows,
                    reference, methods, facts, navigation=navigation))
            _save(folder / 'source_feedback.json', replanned)
            if replanned['status'] == 'unavailable':
                return _finish(state, folder, parent, status='stopped_source_feedback_unavailable',
                    model_quality_gate_passed=False, limitations=replanned['limitations'])
            replacements = {p['slot_id']: p for p in replanned['revised_proposals']}
            proposals = [replacements.get(p['slot_id'], p) for p in proposals]
            _save(folder / 'proposals_after_source_feedback.json', proposals)
            assembled = replanned['replacement_assembly']
    _save(folder / 'assembly.json', assembled)
    plan = assembled['plan']
    if executable_identity(plan) == executable_identity(parent['original_plan']):
        return _finish(state, folder, parent, status='stopped_no_executable_change', limitations=[
            'Model returned the same executable edit; no additional render or automatic loop.'])
    state.assert_source_inputs(plan, catalog)
    checked = observe_final_slices(glm, plan, catalog, windows, cache, folder / 'exact_sources', AUDIT_ROUNDS[round_no])
    # Stable parent-slot obligations survive local trimming into final output review.
    for slot in outline['slots']:
        checked['required_claims'].append({'claim_id': 'parent_slot_' + json_sha([parent['sha256'], slot])[:16],
            'kind': 'slot_takeaway', 'description': slot['intended_takeaway'], 'owner_id': slot['slot_id'],
            'origin': 'parent_slot_obligation', 'segment_ids': next(s['segment_ids'] for s in plan['slots']
                                                                 if s['slot_id'] == slot['slot_id'])})
    checked['timing_counterevidence'] = timing_counterevidence(assembled, outline, proposals, checked, parent)
    _save(folder / 'source_manifest.json', checked)
    blockers = source_counterevidence(checked) + checked['timing_counterevidence']
    if blockers:
        return _finish(state, folder, parent, status='stopped_source_counterevidence', source_blockers=blockers,
            limitations=['Independent source checks do not support required actions/results/identities; no render.'])
    state.assert_protected()
    render_folder = state.claim_render(parent['round'])
    rendered = render_library_video(catalog, plan, render_folder, reference_path=reference['path'],
        fps=plan['fps'], width=plan['width'], height=plan['height'])
    actual = inventory_sources(rendered['rendered_path'], folder / 'output_catalog')['sources'][0]
    output_media = prepare_window(actual, 0, rendered['measured_duration_s'], cache, fps=30)
    blind = _call(glm, f'sf_{round_no}_blind', semantic_prompts.blind_prompt(rendered['measured_duration_s'], rendered['sha256']),
        output_media['path'], lambda v: audit.validate_visual_blind(v, rendered['measured_duration_s'], rendered['sha256']))
    _save(folder / 'blind_reading.json', blind)
    manifest = economy_manifest(plan, rendered)
    economy = _call(glm, f'sf_{round_no}_economy', economy_prompt(manifest, blind), output_media['path'],
                       lambda v: validate_economy_review(v, manifest))
    _save(folder / 'economy_review.json', economy)
    context = {'reference': reference['cached_reading'], 'editing_reference': methods,
        'protocol': audit.SEMANTIC_PROTOCOL, 'video_sha256': rendered['sha256'],
        'actual_render_sha256': rendered['sha256'], 'output_duration_s': rendered['measured_duration_s'],
        'blind_reading': blind, 'source_observations': checked['observations'],
        'segment_checks': checked['segment_checks'], 'required_claims': checked['required_claims'],
        'provenance': rendered['provenance'], 'parent_slot_obligations': outline['slots'],
        'essential_timing_proposals': [{'segment_bindings': assembled['segment_bindings'],
            'timing_checks': assembled['timing_checks'], 'selected_local_proposals': proposals}],
        'audio_review_limit': 'Music rhythm was not heard or verified.',
        'reference_observation_limit': 'Cached reference model interpretation; no replay of unknown full-reference request.'}
    if parent['round'] == 0 and state.data['artifacts'].get('sf_request_bound_parent_facts_v1'):
        policy = _read(state.data['artifacts']['sf_request_bound_parent_facts_v1'][0]['path'])
        context['parent_fact_protocol_limitations'] = policy['protocol_limitations']
    point_key = f'sf_parent_point_navigation_v1_{parent["round"]}'
    if state.data['artifacts'].get(point_key):
        policy = _read(state.data['artifacts'][point_key][0]['path'])
        context['parent_navigation_protocol_limitations'] = policy['protocol_limitations']
    for key in ('sf_request_bound_assembly_v1_3', 'sf_request_bound_proposal_retiming_v1_0'):
        if state.data['artifacts'].get(key) and key.endswith('_' + str(round_no)):
            policy = _read(state.data['artifacts'][key][0]['path'])
            context[key + '_limitations'] = policy['protocol_limitations']
    if feedback_entries:
        context['source_feedback_protocol_limitations'] = _read(feedback_entries[0]['path'])['protocol_limitations']
    if state.data['artifacts'].get('sf_independent_slot_recovery_v1'):
        context['unknown_reply_limit'] = '004/131/166 remain unknown; this edit uses only received evidence and new bound inputs.'
    def validate_review(value):
        contracts.validate_review(value, reference['sha256'])
        validate_method_review(value, methods, rendered['measured_duration_s'])
        audit.validate_semantic_review(value, blind, checked['observations'], checked['required_claims'],
            rendered['measured_duration_s'], rendered['sha256'], segment_checks=checked['segment_checks'])
    review = _call(glm, f'sf_{round_no}_review', semantic_prompts.review_prompt(context), output_media['path'], validate_review)
    _save(folder / 'review.json', review)
    passed = audit.semantic_review_passes(review, blind, checked['segment_checks'],
        expected_segment_ids=[s['segment_id'] for s in plan['segments']]) and \
        economy['economy_status'] == economy['narrative_readability'] == 'pass' and \
        all(candidate['meaning_status'] == 'preserved' for _, candidate in _choices(outline, proposals, assembled['selections'])) and \
        all(row['status'] == 'planned' for row in assembled['transition_checks'])
    return _finish(state, folder, parent, status='model_checked_candidate' if passed else 'candidate_with_limitations',
        final_video=rendered['rendered_path'], final_sha256=rendered['sha256'],
        measured_duration_s=rendered['measured_duration_s'], model_quality_gate_passed=passed,
        limitations=review['limitations'] + economy['limitations'] + assembled['limitations'])


def _finish(state, folder, parent, **values):
    state._reload()
    result_name, result_key = _result_binding(state, parent)
    files = [p for p in folder.rglob('*') if p.is_file() and p.name != result_name]
    result = {'policy': POLICY, 'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
        'parent_video': parent['path'], 'parent_duration_s': parent['duration_s'], **values,
        'evidence_limit': 'Model reviews are not unfamiliar-viewer comprehension or human quality truth.',
        'completed_files': [{'path': str(p.resolve()), 'sha256': sha256_file(p)} for p in sorted(files)]}
    if result_name == 'result_metadata_bound_resume.json':
        policy = _read(state.data['artifacts']['sf_request_bound_parent_facts_v1'][0]['path'])
        result['parent_fact_protocol_limitations'] = policy['protocol_limitations']
    if result_name == 'result_point_navigation_resume.json':
        policy = _read(state.data['artifacts'][f'sf_parent_point_navigation_v1_{parent["round"]}'][0]['path'])
        result['parent_navigation_protocol_limitations'] = policy['protocol_limitations']
    for key in ('sf_request_bound_assembly_v1_3', 'sf_request_bound_proposal_retiming_v1_0'):
        if state.data['artifacts'].get(key) and key.endswith('_' + str(parent['round'])):
            policy = _read(state.data['artifacts'][key][0]['path'])
            result[key + '_limitations'] = policy['protocol_limitations']
    if parent['round'] == 3 and state.data['artifacts'].get('sf_source_feedback_replan_v1_3'):
        policy = _read(state.data['artifacts']['sf_source_feedback_replan_v1_3'][0]['path'])
        result['source_feedback_protocol_limitations'] = policy['protocol_limitations']
    if state.data['artifacts'].get('sf_independent_slot_recovery_v1'):
        result['independent_recovery_limit'] = 'Unknown 004/131/166 remain frozen and cannot establish editing quality.'
    _save(folder / result_name, result)
    state.set_artifact(result_key, {'result_path': str((folder / result_name).resolve()),
        'result_sha256': sha256_file(folder / result_name), 'baseline_id': parent['baseline_id'],
        'parent_sha256': parent['sha256']})
    return result


def execute(output):
    """Run only the separately authorized comparison; never create a grant here."""
    from .slot_finecut_budget import SlotFinecutState, load_authorization, execution_folder
    state = SlotFinecutState(output)
    grant = load_authorization(output)
    preparation = load_preparation(grant['preparation_path'])
    folder = execution_folder(state.output, preparation['preparation_id'])
    snapshot = folder / 'SLOT_FINECUT.md'
    require(sha256_file(snapshot) == grant['knowledge_sha256'], 'slot_finecut:knowledge_snapshot_changed')
    knowledge = snapshot.read_text(encoding='utf-8')
    methods = _read(grant['reference_methods_path'])
    require(sha256_file(grant['reference_methods_path']) == grant['reference_methods_sha256'],
            'slot_finecut:cached_reference_methods_changed')
    lanes = {}
    # Recover old replies before either lane can submit new work. Otherwise a
    # second constructor could complete the first owner's just-returned call.
    for parent in preparation['parents']:
        lane_state = SlotFinecutState(output)
        result_path = folder / parent['baseline_id'] / _result_binding(lane_state, parent)[0]
        lanes[parent['round']] = (lane_state, None if result_path.exists() else CodexMCP(lane_state))
    def run_parent(parent):
        # Each lane owns its mutable State view; ledger writes share a local lock.
        lane_state, glm = lanes[parent['round']]
        try:
            result = _run_parent(glm, lane_state, preparation, parent, folder / parent['baseline_id'], methods, knowledge)
        except (LibraryStopped, ValueError) as error:
            lane_state.assert_protected()
            if isinstance(error, ValueError) and str(error).startswith('model_protocol_repair_exhausted:'):
                receipt = lane_state.freeze_parent_protocol_failure(parent['round'], error)
                return _finish(lane_state, folder / parent['baseline_id'], parent,
                    status='stopped_protocol_failure', stop_receipt=receipt,
                    model_quality_gate_passed=False,
                    limitations=['Known original and sole repair failed their protocol; this parent stops without replay.'])
            diagnostic = {'policy': POLICY, 'baseline_id': parent['baseline_id'], 'error': str(error),
                          'request_count': len(lane_state.data['calls']), 'no_automatic_repeat': True}
            rendered_path = folder / parent['baseline_id'] / 'render/render_result.json'
            if rendered_path.exists():
                rendered = _read(rendered_path)
                require(sha256_file(rendered['rendered_path']) == rendered['sha256'], 'slot_finecut:failed_review_video_changed')
                diagnostic.update(final_video=rendered['rendered_path'], final_sha256=rendered['sha256'],
                                  review_status='incomplete_no_quality_pass')
                print(delivery_paths(rendered), flush=True)
            _save(folder / ('stop_' + json_sha(diagnostic)[:16] + '.json'), diagnostic)
            raise
        if result.get('final_video'):
            print(delivery_paths(result), flush=True)
        return result

    if state.data['artifacts'].get('sf_parallel_execution_v1'):
        results, errors = [], []
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='slot_parent') as pool:
            jobs = {pool.submit(run_parent, parent): parent for parent in preparation['parents']}
            for job in as_completed(jobs):
                try:
                    results.append(job.result())
                except (LibraryStopped, ValueError) as error:
                    errors.append({'baseline_id': jobs[job]['baseline_id'], 'error': str(error)})
        if errors:
            state.assert_protected()
            diagnostic = {'policy': POLICY, 'errors': errors, 'known_results': results,
                          'request_count': len(state.data['calls']), 'both_lanes_settled_before_return': True}
            _save(folder / ('parallel_stop_' + json_sha(diagnostic)[:16] + '.json'), diagnostic)
            raise LibraryStopped('slot_finecut:parallel_execution_stopped:' + json.dumps(errors, ensure_ascii=False))
        results.sort(key=lambda result: result['baseline_id'])
    else:
        results = [run_parent(parent) for parent in preparation['parents']]
    state.assert_protected()
    comparison = {'policy': POLICY, 'preparation_id': preparation['preparation_id'], 'results': results,
        'selection': None, 'comparison_limit': 'Two separately reviewed outputs; no invented side-by-side model viewing.',
        'requests_total': len(state.data['calls']), 'new_unique_library_windows': 0}
    recovery = any(state.data['artifacts'].get(key) for key in
        ('sf_request_bound_assembly_v1_3', 'sf_request_bound_proposal_retiming_v1_0', 'sf_source_feedback_replan_v1_3'))
    bindings = [_result_binding(state, parent)[1] for parent in preparation['parents']]
    path = folder / ('comparison_' + json_sha(bindings)[:16] + '.json' if recovery else 'comparison.json')
    if path.exists():
        # Later unrelated calls do not relabel the original comparison usage.
        previous = _read(path)
        require(previous['results'] == results, 'slot_finecut:completed_comparison_changed')
        return previous
    return _save(path, comparison)
