"""Historical rough -> actual-video review -> generic skill, driven by progress.

The caller owns credentials, model transport and launch control. This adapter
keeps one injected MCP/state ledger, preserves the rough result, and never
automatically resumes a failed or interrupted chain.
"""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from . import contracts, pipeline
from .media import inventory_sources, sha256_file
from .opencode_provider import PROVIDER, bound_media_scope
from .render import render_library_video
from .resources import historical_selected_review_v1 as selected_review
from .resources import original_rough_v1 as historical
from .state import LibraryState, LibraryStopped, json_sha, write_json, scope_fingerprint
from .audio_usage import task_usage, usage_bindings
from .story_context import build_rough_context, core_missing, synthesize_draft, validate_story_context
from .story_finecut import PREFIX as FINE_PREFIX, execute_finecut, FPS, WIDTH, HEIGHT
from .visual_story_trial import proxy, write_once

POLICY = 'reference_rough_skill_v1'
REVIEW_FIELDS = ('theme_status', 'editing_status', 'continuity_status')


def _review_prompt(template, context):
    """Clarify the current JSON contract without changing archived instructions."""
    creative_audio = context.get('creative_audio') is True
    prompt = template({**context, 'review_output_contract': contracts.review_output_contract(
        require_core_meaning=creative_audio)})
    if creative_audio:
        prompt += ('\n必须输出missing_core_meaning数组：实际画面与计划audio_draft组合仍缺失的参考核心含义。'
            '核心完整时为[]，小瑕疵只列limitations。audio_draft尚未合成、未听审，'
            '可以判断文稿表达内容，但不得声称真实声音已通过。')
    return prompt


class ProgressRoughPrompts:
    """Keep verified historical instructions while removing fixed total caps."""

    def __getattr__(self, name):
        return getattr(historical, name)

    @staticmethod
    def _plan(template, context):
        context = {**context, 'render_capabilities': {key: value for key, value in
            context.get('render_capabilities', {}).items() if key not in {'max_duration_s', 'max_segments'}}}
        prompt = template(context).replace(
            '最多32个segments，总成片时长sum((source_out_s-source_in_s)/speed)不得超过180秒。',
            '总成片时长和segments数量不设固定上限；由参考表达需要、实际证据和有效进展决定。') + (
            '\n执行合同：window_id、source_id和role_id必须逐字复制已观察记录中的ID，'
            '不能缩写、改前缀或引用示例ID。每个segment的完整原片区间和role_ids必须同时受到'
            '同一条usable_range支持：source_in_s >= source_start_s + local_in_s，'
            'source_out_s <= source_start_s + local_out_s，role_ids是该条usable_range角色的子集。'
            '不能把多条usable_ranges合并成一段跨过未支持的空隙；若想使用多条范围，'
            '由你自行选择拆成多个segments、缩短或重选，程序不会代你改切点。'
            '角色还须独立满足事件证据约束：segment.role_ids中的每个角色，必须出现在与所选'
            '区间有时间重叠的events.role_ids内；usable_range的角色标签不能代替该约束。'
            '环境或结果镜头只列实际重叠事件支持的角色，没有人物证据时不得借角色标签补造人物。'
            'focus_role_bindings及每个segment仍须引用对应窗口已确认的角色。')
        if context.get('creative_audio') is True:
            prompt = prompt.replace('人物身份与状态、因果联系需要画面证据。',
                '可见外形、动作和物件需要画面证据；新角色身份、动机、对白及叙事关系可以原创。')
            prompt = prompt.replace('所有段落要能组合出连贯人物路线，不能独立挑最高相关片段。角色状态冲突需要显式处理。',
                '所有段落共同完成参考表达；可以是人物故事、情绪意象或无人物的视觉过程。')
            prompt = prompt.replace('至少一个选入片段包含绑定焦点；',
                '需要焦点人物时至少一个选入片段包含绑定焦点；无焦点时focus_role_id=null、focus_role_bindings=[]；')
            prompt += '\n按strategy决定旁白、对白或无语音，不为旧示例字段强造主角或原片因果。'
        return prompt

    def plan_prompt(self, context):
        return self._plan(historical.plan_prompt, context)

    def review_prompt(self, context):
        return _review_prompt(historical.review_prompt, context)

    def search_prompt(self, context):
        context = {key: value for key, value in context.items() if key != 'remaining_window_budget'}
        instruction = '按实际缺项提出下一批连续窗口；观察总数不设固定上限，不重复已确认证据；不要换参考。'
        if 'reusable_visual_candidates' in context and context.get('instruction'):
            instruction = context['instruction'] + '\n' + instruction
        context['instruction'] = instruction
        batch = context.get('max_windows_this_round', 8)
        batching = (f'每批不超过{batch}个、' if batch is not None else '按接口容量分批、')
        prompt = historical.search_prompt(context).replace(
            '一次请求不超过12个连续窗口，每窗口不超过90秒，区间必须在catalog实际时长内。',
            f'连续窗口按工具容量分批提交，{batching}每窗口不超过90秒；'
            '观察总数不设固定上限，区间必须在catalog实际时长内。没有新证据可查时windows可为[]并说明缺项。')
        prompt += ('\n先围绕reference.theme及intended_takeaway找画面，不以还原素材电影剧情为目标。'
            '所有coarse_index均为已收到的来源绑定观察；附图各页标出source_id和原片范围，'
            '历史unavailable记录只表示不能重发的旧范围，不是附图的来源。'
            '身份、打斗胜负或剧情因果只在能帮助表达参考主旨时才值得补看；'
            '若没有可贡献该主旨的画面，windows返回[]并说明具体缺项，不转去梳理无关剧情。')
        if context.get('creative_audio') is True:
            prompt += ('\n本次将在粗剪阶段创作新旁白或对白，镜头和配音要共同表达参考主旨。'
                '不要求复述原片台词或原片故事；选择能承载新叙事的真实画面，仍不能编造其可见事实。')
        return prompt

    def for_round(self, round_no):
        if type(round_no) is not int or round_no < 0:
            raise ValueError('clean_chain:nonnegative_round_required')
        phase = historical.for_round(min(round_no, 1))
        return SimpleNamespace(fine_prompt=phase.fine_prompt,
                               review_prompt=lambda context: _review_prompt(phase.review_prompt, context),
                               plan_prompt=lambda context: self._plan(phase.plan_prompt, context))

    def select_prompt(self, candidates):
        return historical.select_prompt(candidates).replace('比较两个实际成片', '比较这些实际成片')

    def bound_select_prompt(self, candidates, attached_media):
        return self.select_prompt(candidates) + (
            '\n附带视频只属于attached_media标明的版本；不得把其中画面归入其他round。'
            '其他版本只依据其自身已保存的blind/review比较，不借附带视频补造事实。'
            '候选actual_render给出实际视频SHA和时长；选择理由必须明确依据的版本。'
            '\nattached_media=' + json.dumps(attached_media, ensure_ascii=False))


ROUGH_PROMPTS = ProgressRoughPrompts()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _same_file(path, expected, reason):
    if not Path(path).is_file() or sha256_file(path) != expected:
        raise LibraryStopped('clean_chain:' + reason)


def _received_reply(parent, saved, stage):
    for call in reversed(saved['calls']):
        if call['name'] not in {stage, stage + '_repair'} or call['status'] != 'received':
            continue
        folder = parent / 'calls' / call['id']
        if not (folder / 'parsed.json').is_file():
            continue
        request, response = _read(folder / 'request.json'), _read(folder / 'response.json')
        value = _read(folder / 'parsed.json')
        text = '\n'.join(row['text'] for row in response['result']['content'] if row.get('type') == 'text')
        if (json_sha(request) != call['request_sha256'] or json_sha(response) != call['response_sha256'] or
                contracts.parse_model_json(text) != value):
            raise LibraryStopped('clean_chain:rough_received_artifact_changed')
        if call['name'] == stage + '_repair':
            original = next((row for row in saved['calls'] if row['id'] == call.get('repair_of') and
                             row['name'] == stage and row['status'] == 'received'), None)
            if original is None:
                raise LibraryStopped('clean_chain:rough_received_repair_unbound')
            original_request = _read(parent / 'calls' / original['id'] / 'request.json')
            if (json_sha(original_request) != original['request_sha256'] or
                    any(request.get(key) != original_request.get(key) for key in
                        ('tool', 'media_sha256', 'observation_scope'))):
                raise LibraryStopped('clean_chain:rough_received_repair_changed')
        return request, value
    raise LibraryStopped('clean_chain:rough_received_artifact_unbound')


def _received_artifact(parent, saved, stage, filename):
    _, value = _received_reply(parent, saved, stage)
    if _read(parent / filename) != value:
        raise LibraryStopped('clean_chain:rough_received_artifact_changed')
    return value


def _continued_fine_observations(parent, saved, old_input, source, reference, seen=None):
    """Carry exact received facts forward, independently of a new planning prompt."""
    parent = Path(parent).resolve(strict=True)
    seen = set() if seen is None else seen
    if parent in seen:
        raise LibraryStopped('clean_chain:inherited_skill_observation_cycle')
    seen.add(parent)
    for key, current in (('parent', source), ('reference', reference)):
        if any(old_input[key].get(field) != current.get(field) for field in
               ('sha256', 'duration_s', 'video_stream_index', 'audio_stream_index')):
            raise LibraryStopped('clean_chain:inherited_skill_source_changed')
    base = parent / 'skill_finecut'
    observed = _read(base / 'observed.json')
    inherited = None
    reuse = saved.get('artifacts', {}).get('clean_chain_fine_observation_reuse', [])
    if reuse and not any(call['name'] == FINE_PREFIX + 'observe' and call['status'] == 'received'
                         for call in saved['calls']):
        receipt = _read(reuse[-1]['path'])
        if json_sha(receipt) != reuse[-1]['sha256']:
            raise LibraryStopped('clean_chain:inherited_skill_reuse_record_changed')
        origin = Path(receipt['parent_task']).resolve(strict=True)
        ledger = origin / 'library_state.json'
        _same_file(ledger, receipt['parent_ledger_sha256'], 'inherited_skill_origin_ledger_changed')
        origin_saved = _read(ledger)
        if (any(call['status'] != 'received' for call in origin_saved['calls']) or
                not any((origin / name).is_file() for name in ('chain_result.json', 'chain_failure.json'))):
            raise LibraryStopped('clean_chain:inherited_skill_origin_not_settled')
        for path, digest in receipt['files'].items():
            _same_file(path, digest, 'inherited_skill_origin_observation_changed')
        if str(origin / 'skill_finecut/observed.json') not in receipt['files']:
            raise LibraryStopped('clean_chain:inherited_skill_origin_observation_unbound')
        origin_input = _read(origin / 'skill_finecut/input.json')
        if origin_input['reference_context_sha256'] != old_input['reference_context_sha256']:
            raise LibraryStopped('clean_chain:inherited_skill_context_changed')
        inherited = _continued_fine_observations(origin, origin_saved, origin_input, source, reference, seen)
        if observed != inherited['observed']:
            raise LibraryStopped('clean_chain:inherited_skill_observation_changed')
    results_path = base / 'inspection_results.json'
    if results_path.is_file():
        inspections = _read(results_path)
    else:
        # A later failed inspection must not discard already received facts.
        # Only reconstruct complete continuous observations with no frame work;
        # raw invalid replies are retained, never promoted to valid observations.
        inspections = list(inherited['inspections']) if inherited else []
        for index, query in enumerate(observed.get('inspect', [])[len(inspections):], len(inspections)):
            stage = f'{FINE_PREFIX}inspect_{index}'
            if not any(call['status'] == 'received' and call['name'] in {stage, stage + '_repair'} and
                       (parent / 'calls' / call['id'] / 'parsed.json').is_file() for call in saved['calls']):
                break
            _, facts = _received_reply(parent, saved, stage)
            if facts.get('frame_ranges') or any(call['name'].startswith(f'{FINE_PREFIX}detail_{index}_')
                                              for call in saved['calls']):
                break
            inspections.append(dict(request=query, request_sha256=json_sha(query),
                continuous=facts, frame_sheets=[]))
    if inherited and inspections[:len(inherited['inspections'])] != inherited['inspections']:
        raise LibraryStopped('clean_chain:inherited_skill_observation_changed')
    sources = {'parent': source, 'reference': reference}

    def received(stage, expected, target, start, end, kind='continuous_window', media_sha=None):
        request, value = _received_reply(parent, saved, FINE_PREFIX + stage)
        scope = dict(kind=kind, source_sha256=sources[target]['sha256'],
                     source_start_s=start, source_end_s=end)
        if (value != expected or request['tool'] != ('analyze_image' if kind == 'sparse_contact_sheet' else 'analyze_video') or
                scope_fingerprint(bound_media_scope(request)) != scope_fingerprint(scope) or
                media_sha is not None and request['media_sha256'] != media_sha):
            raise LibraryStopped('clean_chain:inherited_skill_observation_changed')

    if inherited is None:
        received('observe', observed, 'parent', 0, source['duration_s'])
    if (not isinstance(inspections, list) or len(inspections) > len(observed.get('inspect', [])) or
            [row['request'] for row in inspections] != observed.get('inspect', [])[:len(inspections)]):
        raise LibraryStopped('clean_chain:inherited_skill_inspections_changed')
    for index, row in enumerate(inspections):
        query = row['request']
        if row['request_sha256'] != json_sha(query):
            raise LibraryStopped('clean_chain:inherited_skill_inspection_request_changed')
        if inherited and index < len(inherited['inspections']):
            continue  # Its original request/response/source were checked recursively.
        received(f'inspect_{index}', row['continuous'], query['target'], query['start_s'], query['end_s'])
        details = {call['name'].removesuffix('_repair') for call in saved['calls'] if
                   call['status'] == 'received' and call['name'].startswith(f'{FINE_PREFIX}detail_{index}_')}
        if details != {f'{FINE_PREFIX}detail_{index}_{page}' for page in range(len(row['frame_sheets']))}:
            raise LibraryStopped('clean_chain:inherited_skill_frame_pages_changed')
        for page, sheet in enumerate(row['frame_sheets']):
            manifest_path = Path(sheet['grid_manifest'])
            _same_file(manifest_path, manifest_path.with_name('manifest.sha256').read_text().strip(),
                       'inherited_skill_grid_manifest_changed')
            manifest = _read(manifest_path)
            if manifest['source']['sha256'] != sources[query['target']]['sha256']:
                raise LibraryStopped('clean_chain:inherited_skill_grid_source_changed')
            received(f'detail_{index}_{page}', sheet['facts'], query['target'],
                manifest['request']['start_s'], manifest['request']['end_s'], 'sparse_contact_sheet',
                manifest['grid']['sha256'])
    return dict(observed=observed, inspections=inspections,
                source_parent_task=inherited['source_parent_task'] if inherited else str(parent))


def _continued_fine_feedback(seed, parent, source, source_parent_task=None):
    _same_file(seed['path'], seed['sha256'], 'finecut_feedback_file_changed')
    feedback = _read(seed['path'])
    if feedback.get('parent_source_sha256') != source['sha256']:
        raise LibraryStopped('clean_chain:finecut_feedback_parent_changed')
    candidate = feedback['candidate']
    roots = [parent, *([Path(source_parent_task)] if source_parent_task is not None else [])]
    if not any(Path(candidate['path']).resolve(strict=True).is_relative_to(root) for root in roots):
        raise LibraryStopped('clean_chain:finecut_feedback_candidate_outside_parent')
    _same_file(candidate['path'], candidate['sha256'], 'finecut_feedback_candidate_changed')
    for manifest in feedback['manifests']:
        _same_file(manifest['path'], manifest['sha256'], 'finecut_feedback_manifest_changed')
    return feedback


def _continued_rough(parent, output, reference, library, configuration, factory, registry_path):
    """Explicit forward evaluation of settled real roughs; never rerun their edits."""
    parent = Path(parent).resolve(strict=True)
    baseline = configuration.get('parent_baseline', {})
    ledger_path = parent / 'library_state.json'
    if (Path(baseline.get('path', '')).resolve() != ledger_path or
            not output.is_relative_to(parent / 'evaluations')):
        raise LibraryStopped('clean_chain:rough_continuation_requires_direct_parent')
    _same_file(ledger_path, baseline.get('sha256'), 'rough_parent_ledger_changed')
    saved = _read(ledger_path)
    if (saved['input_lock']['configuration'].get('workflow') != POLICY or
            any(call['status'] != 'received' for call in saved['calls']) or
            not ((parent / 'chain_result.json').exists() or (parent / 'chain_failure.json').exists())):
        raise LibraryStopped('clean_chain:settled_known_rough_parent_required')
    sources = inventory_sources(library, output / 'catalog')
    ref = inventory_sources(reference, output / 'reference_catalog')['sources'][0]
    lock = saved['input_lock']
    if (ref['sha256'] != lock['reference_sha256'] or
            [{'source_id': row['source_id'], 'sha256': row['sha256']} for row in sources['sources']] != lock['library_sources']):
        raise LibraryStopped('clean_chain:rough_parent_media_changed')
    write_once(output / 'reference_reading.json', _read(parent / 'reference_reading.json'))
    handoff = _read(parent / 'rough_handoff.json')
    bindings = saved['artifacts'].get('clean_chain_rough_handoff', [])
    recorded = []
    for row in bindings:
        value = _read(row['path'])
        if json_sha(value) != row['sha256']:
            raise LibraryStopped('clean_chain:rough_parent_handoff_record_changed')
        recorded.append(value)
    if {'path': str(parent / 'rough_handoff.json'), 'sha256': json_sha(handoff)} not in recorded:
        raise LibraryStopped('clean_chain:rough_parent_handoff_unbound')
    rough = _read(parent / 'result.json')
    if (rough['final_video'] != handoff['rough_video_path'] or
            rough['final_sha256'] != handoff['rough_source_sha256'] or
            rough['selected_round'] != handoff['selected_round'] or
            rough['review'] != handoff['original_rough_review']):
        raise LibraryStopped('clean_chain:rough_parent_result_unbound')
    selected_stage = f"selected_review_v2_{handoff['selected_round']}"
    technical_handoff = (rough.get('review_kind') == 'not_reviewed_until_audio_delivery' and
                         configuration.get('creative_audio') is True)
    if technical_handoff:
        if _read(parent / 'rough_execution_check.json') != handoff['rough_review']:
            raise LibraryStopped('clean_chain:rough_execution_check_changed')
    elif _received_artifact(parent, saved, selected_stage, selected_stage + '.json') != handoff['rough_review']:
        raise LibraryStopped('clean_chain:rough_parent_review_unbound')
    _same_file(rough['final_video'], rough['final_sha256'], 'rough_parent_render_changed')
    state = LibraryState(output, {'reference_sha256': ref['sha256'],
        'library_sources': lock['library_sources'], 'configuration': configuration},
        max_requests=None, registry_path=registry_path)
    mcp = factory(state)
    state.set_artifact('clean_chain_rough_continuation', dict(parent_task=str(parent),
        parent_ledger_sha256=baseline['sha256'], original_result_sha256=sha256_file(parent / 'result.json'),
        original_handoff_sha256=sha256_file(parent / 'rough_handoff.json'), no_rough_render_repeated=True))
    inherited = not any(handoff['rough_review'][field] == 'fail' for field in REVIEW_FIELDS)
    if not inherited:
        candidates = []
        folders = [folder for folder in parent.glob('render_*') if folder.name.removeprefix('render_').isdigit()]
        for folder in sorted(folders, key=lambda item: int(item.name.removeprefix('render_'))):
            if not (folder / 'render_result.json').is_file():
                continue
            round_no = int(folder.name.removeprefix('render_'))
            actual = _read(folder / 'render_result.json')
            _same_file(actual['rendered_path'], actual['sha256'], 'rough_candidate_changed')
            _received_artifact(parent, saved, f'plan_{round_no}', f'plan_{round_no}.json')
            candidates.append(dict(round=round_no, blind=_received_artifact(parent, saved, f'blind_{round_no}', f'blind_reading_{round_no}.json'),
                review=_received_artifact(parent, saved, f'review_{round_no}', f'review_{round_no}.json'),
                actual_render={'sha256': actual['sha256'], 'duration_s': actual['measured_duration_s']}, render=actual))
        if not candidates:
            raise LibraryStopped('clean_chain:no_actual_rough_candidates')
        attached = candidates[-1]
        source = inventory_sources(attached['render']['rendered_path'], output / 'selection_catalog')['sources'][0]
        media = proxy(source, 0, source['duration_s'], output / 'chain_cache', label='clean_rough_selection')
        def validate_choice(value):
            if (type(value.get('selected_round')) is not int or
                    value['selected_round'] not in {row['round'] for row in candidates} or
                    not isinstance(value.get('reason'), str) or not value['reason'].strip()):
                raise ValueError('invalid_render_selection')
        choice = mcp.call('select_render', ROUGH_PROMPTS.bound_select_prompt(
            [{key: value for key, value in row.items() if key != 'render'} for row in candidates],
            {'round': attached['round'], 'sha256': source['sha256'],
             'source_start_s': 0, 'source_end_s': source['duration_s']}), media['path'], validate_choice)
        write_once(output / 'render_selection.json', choice)
        selected = next(row for row in candidates if row['round'] == choice['selected_round'])
        rough = {**rough, 'selected_round': selected['round'], 'final_video': selected['render']['rendered_path'],
            'final_sha256': selected['render']['sha256'], 'review': selected['review']}
        handoff = None
    selected = rough['selected_round']
    for name in (f'plan_{selected}.json', f'blind_reading_{selected}.json'):
        if (parent / name).is_file():
            write_once(output / name, _read(parent / name))
    write_once(output / f'render_{selected}/render_result.json', _read(parent / f'render_{selected}/render_result.json'))
    write_once(output / 'result.json', rough)
    return rough, handoff


def _selected_review(state, mcp, rough, parent, reference, context, output):
    selected = rough['selected_round']
    if type(selected) is not int or selected < 0:
        raise LibraryStopped('clean_chain:selected_round_invalid')
    render = _read(output / f'render_{selected}/render_result.json')
    if (rough['final_sha256'] != parent['sha256'] or render['sha256'] != parent['sha256'] or
            render['measured_duration_s'] != parent['duration_s']):
        raise LibraryStopped('clean_chain:selected_actual_render_changed')
    stage = f'selected_review_v2_{selected}'
    path = output / (stage + '.json')
    state._reload()
    if path.exists() or any(call['name'] in {stage, stage + '_repair'} for call in state.data['calls']):
        raise LibraryStopped('clean_chain:selected_review_already_used')
    actual = proxy(parent, 0, parent['duration_s'], output / 'chain_cache', label='clean_selected_rough')
    review_context = dict(reference=context['reference'], actual_render_sha256=parent['sha256'],
        blind_reading=_read(output / f'blind_reading_{selected}.json'),
        plan=_read(output / f'plan_{selected}.json'), provenance=render['provenance'],
        reference_protocol_limit=context['evidence_limit'],
        audio_review_limit='The normal-speed visual proxy physically omits audio and covers the complete '
            'rough timeline; native cloud frame sampling and audio craft remain unverified.')
    if context.get('creative_audio') is True:
        review_context.update(creative_audio=True, audio_draft_status='planned_text_only_not_synthesized_or_heard')
    review = mcp.call(stage, _review_prompt(selected_review.review_prompt, review_context), actual['path'],
                      lambda value: contracts.validate_review(value, reference['sha256'],
                          require_core_meaning=context.get('creative_audio') is True))
    write_once(path, review)
    state._reload()
    call = next(call for call in reversed(state.data['calls'])
                if call['name'] in {stage, stage + '_repair'} and call['status'] == 'received')
    return review, path, call['id']


def execute(reference, library, output, *, model_factory, provider_config=None,
            reference_seed=None, registry_path=None, asr=True, asr_model_dir=None, rough_task=None,
            search_recovery_proof=None):
    """Generate roughs while editing progresses, review the selection, refine it.

    A received reference seed is optional and is never tied to a historical
    call ID. Partial rough reviews may enter the skill. Failed reviews stop by
    default; explicit functional tests continue with a false joint gate. Only a fully settled,
    hash-verified result can be reused without invoking models or rendering.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    configuration = {**PROVIDER, **(provider_config or {}), 'workflow': POLICY, 'asr': asr}
    creative_audio = configuration.get('creative_audio') is True
    stop_after_rough = configuration.get('stop_after_rough') is True
    for field in ('max_rounds', 'max_renders', 'max_fine'):
        configuration.pop(field, None)
    identity = dict(reference=str(Path(reference).resolve()), library=str(Path(library).resolve()),
        configuration=configuration, reference_seed_sha256=json_sha(reference_seed),
        asr_model_dir=str(Path(asr_model_dir).resolve()) if asr_model_dir is not None else None)
    if rough_task is not None:
        identity['rough_task'] = str(Path(rough_task).resolve(strict=True))
    done = output / 'chain_result.json'
    if done.exists():
        saved = _read(done)
        if saved['input'] != identity:
            raise LibraryStopped('clean_chain:cached_inputs_changed')
        cached_audio = saved.get('finecut', {}).get('creative_audio', {}) or {}
        if creative_audio and saved.get('content_delivery_ready', cached_audio.get('content_delivery_ready', False)):
            visual = saved.get('final_visual_review', {})
            if (visual.get('status') != 'pass' or
                    visual.get('final_sha256') != saved.get('final_sha256')):
                raise LibraryStopped('clean_chain:cached_final_requires_visual_review')
        for path, digest in saved['cache_bindings'].items():
            _same_file(path, digest, 'cached_artifact_changed')
        return saved
    failure = output / 'chain_failure.json'
    if search_recovery_proof is not None:
        from .search_recovery import claim
        failure = claim(output, search_recovery_proof)
    elif failure.exists():
        raise LibraryStopped('clean_chain:terminal_failure_no_automatic_restart')
    if search_recovery_proof is None:
        try:
            with (output / 'chain_started').open('x', encoding='utf-8') as handle:
                handle.write(POLICY)
        except FileExistsError as error:
            raise LibraryStopped('clean_chain:interrupted_no_automatic_restart') from error
    active = {}
    try:
        if not callable(model_factory) or type(asr) is not bool:
            raise ValueError('clean_chain:model_factory_and_boolean_asr_required')
        if configuration.get('finecut_feedback_seed') and rough_task is None:
            raise LibraryStopped('clean_chain:finecut_feedback_requires_rough_continuation')
        if (configuration['provider'] != PROVIDER['provider'] or
                configuration['progress_policy'] != PROVIDER['progress_policy']):
            raise ValueError('clean_chain:finite_official_provider_required')
        quality_policy = configuration.get('quality_policy', 'stop_on_failed_rough')
        if quality_policy not in {'stop_on_failed_rough', 'functional_test_keep_negative_reviews'}:
            raise ValueError('clean_chain:unknown_quality_policy')
        if reference_seed is not None and (not isinstance(reference_seed, dict) or
                not isinstance(reference_seed.get('full_response'), dict) or
                not isinstance(reference_seed['full_response'].get('reference'), dict) or
                not isinstance(reference_seed.get('evidence_limit'), str) or not reference_seed['evidence_limit']):
            raise ValueError('clean_chain:received_reference_seed_required')
        if asr_model_dir is not None:
            asr_model_dir = Path(asr_model_dir).resolve(strict=True)
            if not asr_model_dir.is_dir():
                raise ValueError('clean_chain:asr_model_directory_required')
        def factory(state):
            if active:
                raise LibraryStopped('clean_chain:one_ledger_required')
            active['state'] = state
            active['mcp'] = model_factory(state)
            return active['mcp']
        inherited_handoff = None
        if (rough_task is None and search_recovery_proof is not None and
                _read(search_recovery_proof).get('policy') == 'human_authorized_tts_audio_resume_v1'):
            # Admission already bound the finished rough and its exact ledger.
            # Resume at audio, without rerunning planning or rewriting the rough.
            saved = _read(output/'library_state.json')
            factory(LibraryState(output, saved['input_lock'],
                                 max_requests=saved['max_requests'], registry_path=registry_path))
            rough = _read(output/'result.json')
        elif rough_task is None:
            rough = pipeline.execute(reference, library, output, span_s=600, frames=18, max_fine=None,
                max_requests=None, asr=asr, editing_v2=False, semantic_audit=False, active_finecut=False,
                failure_report_name=('failure_tts_audio_resume_v1.json'
                    if failure.name=='tts_audio_resume_v1.chain_failure.json' else 'failure_caption_reflow_repair_v1.json'
                    if failure.name=='caption_reflow_repair_v1.chain_failure.json' else 'failure_plan_binding_repair_v1.json'
                    if failure.name=='plan_binding_repair_v1.chain_failure.json' else 'failure_plan_cast_transport_v1.json'
                    if failure.name=='plan_cast_transport_v1.chain_failure.json'
                    else 'failure_plan_cast_correction_v1.json' if failure.name=='plan_cast_correction_v1.chain_failure.json'
                    else 'failure_schema_resume_v1.json' if search_recovery_proof is not None else 'failure.json'),
                model_factory=factory, provider_config=configuration, registry_path=registry_path,
                reference_seed=reference_seed, prompt_module=ROUGH_PROMPTS,
                render_fn=partial(render_library_video,caption_reflow=True, burn_captions=False,
                                  fps=FPS, width=WIDTH, height=HEIGHT)
                    if creative_audio else historical.render_library_video,
                asr_model_dir=asr_model_dir)
        else:
            rough, inherited_handoff = _continued_rough(rough_task, output, reference, library,
                configuration, factory, registry_path)
        state, mcp = active['state'], active['mcp']
        _same_file(rough['final_video'], rough['final_sha256'], 'rough_bytes_changed')
        original_result_sha = sha256_file(output / 'result.json')
        parent = inventory_sources(rough['final_video'], output / 'selected_rough_catalog')['sources'][0]
        ref = _read(output / 'reference_catalog/inventory.json')['sources'][0]
        reading = _read(output / 'reference_reading.json')
        context = dict(reference=reading, evidence_limit=(reference_seed['evidence_limit'] if reference_seed
            else rough.get('evidence_limit', 'Received reference observation; native sampling and audio craft remain unverified.')))
        if creative_audio:
            context['creative_audio'] = True
        if reference_seed and reference_seed.get('source_call_id'):
            context['source_call_id'] = reference_seed['source_call_id']
        if creative_audio and rough.get('status') == 'rough_planned':
            review = rough['review']
            review_path = output / 'rough_execution_check.json'
            review_call = None
            write_once(review_path, review)
        elif rough.get('status') == 'needs_material_reselection':
            review = rough['review']
            review_path = Path(rough['selected_review_path'])
            review_call = next(call['id'] for call in reversed(state.data['calls'])
                if call['name'] in {review_path.stem, review_path.stem + '_repair'} and call['status'] == 'received')
        elif inherited_handoff is None:
            review, review_path, review_call = _selected_review(state, mcp, rough, parent, ref, context, output)
        else:
            review = inherited_handoff['rough_review']
            review_path = Path(inherited_handoff['selected_review_path'])
            review_call = inherited_handoff['rough_review_call_id']
            if _read(review_path) != review:
                raise LibraryStopped('clean_chain:inherited_review_changed')
            contracts.validate_review(review, ref['sha256'], require_core_meaning=creative_audio)
        story_path = None
        if creative_audio and not core_missing(review) and not any(review[field] == 'fail' for field in REVIEW_FIELDS):
            if inherited_handoff and inherited_handoff.get('story_context_path'):
                story_path = Path(inherited_handoff['story_context_path'])
                _same_file(story_path, inherited_handoff['story_context_sha256'], 'rough_story_context_changed')
                story = _read(story_path)
                validate_story_context(story, reference_sha=ref['sha256'], video_sha=parent['sha256'])
                for line in story['received_tts']:
                    _same_file(line['audio_path'], line['audio_sha256'], 'rough_tts_changed')
            else:
                selected = rough['selected_round']
                story = build_rough_context(reading, _read(output / f'plan_{selected}.json'),
                    _read(output / f'render_{selected}/render_result.json'),
                    windows=_read(output / 'watched_windows.json') if (output / 'watched_windows.json').exists() else None)
                audio_seed = configuration.get('received_audio_seed')
                if audio_seed:
                    _same_file(audio_seed['path'], audio_seed['sha256'], 'received_audio_seed_changed')
                    prior_audio = _read(audio_seed['path'])
                    validate_story_context(prior_audio, reference_sha=ref['sha256'])
                    story.update(received_tts=prior_audio['received_tts'],
                                 tts_cache_dir=prior_audio['tts_cache_dir'])
                story = synthesize_draft(story, output / 'rough_audio/tts')
                if story['timing_problems'] and story.get('source_visual_policy') == 'silent_subtitle_masked':
                    from .visual_audio import revise_draft_timing
                    media = proxy(parent, 0, parent['duration_s'], output / 'chain_cache', label='rough_audio_timing')
                    story = revise_draft_timing(story, mcp, media['path'], output / 'rough_audio',
                        stage='rough_audio_timing_1', scope={key: media[key] for key in
                            ('kind', 'source_sha256', 'source_start_s', 'source_end_s')})
                    story = synthesize_draft(story, output / 'rough_audio/tts')
                if not story['timing_problems']:
                    from .audio_render import mix_speech
                    from .visual_audio import music_source
                    received = {line['id']: line for line in story['received_tts']}
                    preview = mix_speech(parent['path'], [
                        {**line, 'audio_path': received[line['id']]['audio_path']}
                        for line in story['audio_draft']], output / 'rough_audio/preview.mp4',
                        music_path=music_source(ref['path'], story, configuration.get('audio_music_bed')))
                    story['rough_audio_preview'] = dict(path=preview['path'], sha256=preview['sha256'],
                        receipt_path=preview['receipt_path'], receipt_sha256=sha256_file(preview['receipt_path']),
                        status='technical_preview_not_quality_verdict')
                story_path = output / 'rough_audio/story_context.json'
                write_once(story_path, story)
            context['story_context'] = story
        handoff = inherited_handoff or dict(policy=POLICY, rough_video_path=parent['path'], rough_source_sha256=parent['sha256'],
            rough_duration_s=parent['duration_s'], selected_round=rough['selected_round'],
            rough_review_call_id=review_call, rough_review=review,
            original_rough_review=rough['review'], selected_review_path=str(review_path),
            method_provenance={**historical.provenance(), 'runtime_adaptation':
                'progress_driven_without_fixed_total_duration_segments_windows_rounds_or_renders'},
            selected_review_provenance=selected_review.provenance())
        if story_path is not None and not inherited_handoff:
            handoff = {**handoff, 'story_context_path': str(story_path),
                       'story_context_sha256': sha256_file(story_path)}
        handoff_path = output / 'rough_handoff.json'
        write_once(handoff_path, handoff)
        state.set_artifact('clean_chain_rough_handoff', dict(path=str(handoff_path), sha256=json_sha(handoff)))
        result = dict(policy=POLICY, input=identity, rough=rough, rough_review=review,
                      rough_video=parent['path'], rough_sha256=parent['sha256'], rough_duration_s=parent['duration_s'])
        if core_missing(review):
            result.update(status='needs_material_reselection', candidate_video=parent['path'],
                          candidate_sha256=parent['sha256'],
                          missing_core_meaning=review.get('missing_core_meaning', []), joint_quality_gate=False)
        elif (any(review[field] == 'fail' for field in REVIEW_FIELDS) and
                (quality_policy != 'functional_test_keep_negative_reviews' or stop_after_rough)):
            result.update(status='rough_review_failed_candidate', final_video=parent['path'],
                          final_sha256=parent['sha256'], joint_quality_gate=False)
        elif stop_after_rough:
            result['joint_quality_gate'] = False
            if creative_audio:
                if story_path is None:
                    raise LibraryStopped('clean_chain:accepted_rough_audio_context_required')
                result.update(story_context_path=str(story_path), story_context_sha256=sha256_file(story_path),
                              timing_problems=story['timing_problems'])
                preview = story.get('rough_audio_preview')
                if preview:
                    result.update(status='rough_audio_ready', rough_audio_video=preview['path'],
                        rough_audio_sha256=preview['sha256'], final_video=preview['path'], final_sha256=preview['sha256'])
                    from .visual_audio import review_final_video, content_ready
                    actual_review = review_final_video(preview['path'], output / 'rough_audio', story)
                    ready = content_ready(actual_review, story)
                    result.update(content_review=actual_review, content_delivery_ready=ready)
                    if not ready:
                        result['status'] = ('needs_material_reselection' if actual_review['repair_at'] == 'selection'
                                            else 'needs_audio_revision')
                else:
                    result.update(status='needs_audio_revision', candidate_video=parent['path'],
                                  candidate_sha256=parent['sha256'])
            else:
                result.update(status='rough_ready', final_video=parent['path'], final_sha256=parent['sha256'])
        else:
            context.update(prior_rough_review=review, actual_rough_handoff=handoff)
            fine_options = {}
            if inherited_handoff and (Path(rough_task) / 'skill_finecut/input.json').is_file():
                prior = Path(rough_task).resolve(strict=True)
                old_input = _read(prior / 'skill_finecut/input.json')
                if json_sha(context) != old_input['reference_context_sha256']:
                    raise LibraryStopped('clean_chain:inherited_skill_context_changed')
                observation_files = [prior / 'skill_finecut' / name for name in ('observed.json', 'inspection_results.json')]
                if any(path.exists() for path in observation_files):
                    if not observation_files[0].is_file():
                        raise LibraryStopped('clean_chain:inherited_skill_observations_incomplete')
                    observation_files = [path for path in observation_files if path.is_file()]
                    ledger = prior / 'library_state.json'
                    _same_file(ledger, configuration['parent_baseline']['sha256'], 'rough_parent_ledger_changed')
                    fine_options['received_observations'] = _continued_fine_observations(
                        prior, _read(ledger), old_input, parent, ref)
                    state.set_artifact('clean_chain_fine_observation_reuse', dict(parent_task=str(prior),
                        parent_ledger_sha256=sha256_file(ledger),
                        files={str(path):sha256_file(path) for path in observation_files}, new_vision_post=False))
            if configuration.get('finecut_feedback_seed'):
                if not inherited_handoff:
                    raise LibraryStopped('clean_chain:finecut_feedback_requires_inherited_rough')
                fine_options['planning_feedback'] = _continued_fine_feedback(
                    configuration['finecut_feedback_seed'], Path(rough_task).resolve(strict=True), parent,
                    fine_options.get('received_observations', {}).get('source_parent_task'))
            fine = execute_finecut(state, mcp, context, parent, ref, output / 'skill_finecut', **fine_options)
            if fine.get('status') in {'needs_material_reselection', 'needs_audio_revision'}:
                candidate = fine.get('candidate_video', parent['path'])
                result.update(status=fine['status'], finecut=fine,
                    candidate_video=candidate, candidate_sha256=sha256_file(candidate),
                    missing_core_meaning=fine.get('missing_core_meaning', []), joint_quality_gate=False)
            else:
                deferred = (creative_audio and rough.get('status') == 'rough_planned' and
                    rough.get('review_kind') == 'not_reviewed_until_audio_delivery' and
                    all(review[field] == 'unverifiable' for field in REVIEW_FIELDS) and
                    not review.get('evidence') and not review.get('revision_requests') and not core_missing(review))
                audio = fine.get('creative_audio', {}) or {}
                final_av_ready = (audio.get('status') == 'delivered' and
                    audio.get('content_delivery_ready') is True and
                    audio.get('joint_quality_gate') is True and
                    audio.get('technical_delivery_verified') is True)
                result.update(status='rough_to_skill_completed', finecut=fine, final_video=fine['final_video'],
                    final_sha256=fine['final_sha256'], joint_quality_gate=((all(review[field] == 'pass'
                        for field in REVIEW_FIELDS) or (deferred and final_av_ready)) and
                        fine.get('joint_quality_gate') is True))
        _same_file(output / 'result.json', original_result_sha, 'rough_result_changed')
        settled_video = result.get('final_video') or result['candidate_video']
        settled_sha = result.get('final_sha256') or result['candidate_sha256']
        _same_file(settled_video, settled_sha, 'settled_bytes_changed')
        subtitles = None
        final_audio = result.get('finecut', {}).get('creative_audio', {}) or {}
        final_story = (story if stop_after_rough and story_path is not None else
                       final_audio.get('performed_story_context'))
        ready = result.get('content_delivery_ready', final_audio.get('content_delivery_ready', False))
        if (creative_audio and configuration.get('final_subtitles', True) and ready
                and final_story and final_story.get('audio_draft')):
            from .audio_render import subtitle_final_video
            subtitles = subtitle_final_video(settled_video, final_story['audio_draft'],
                                             output / 'final_subtitled.mp4')
            result.update(unsubtitled_video=settled_video, unsubtitled_sha256=settled_sha,
                          final_video=subtitles['path'], final_sha256=subtitles['sha256'],
                          subtitles=subtitles)
            settled_video, settled_sha = subtitles['path'], subtitles['sha256']
        if creative_audio and ready:
            from .final_visual_review import review_final_layout
            visual = review_final_layout(settled_video, output/'final_visual_review', mcp)
            result['final_visual_review'] = visual
            if visual['status'] != 'pass':
                result.update(status='needs_visual_revision', joint_quality_gate=False,
                              content_delivery_ready=False)
        state._reload()
        result['usage'] = task_usage(state, output / 'skill_finecut/creative_audio')
        result['cache_bindings'] = {str(path): sha256_file(path) for path in
            (output / 'result.json', handoff_path, review_path, Path(parent['path']),
             Path(ref['path']), Path(settled_video))}
        result['cache_bindings'].update(usage_bindings(result['usage']))
        if result.get('final_visual_review'):
            visual = result['final_visual_review']
            for path in (Path(visual['receipt_path']), Path(visual['reviewed_proxy'])):
                result['cache_bindings'][str(path)] = sha256_file(path)
            result['cache_bindings'].update(visual['evidence_sha256'])
        if subtitles:
            for name in ('receipt_path', 'subtitle_srt_path', 'subtitle_ass_path'):
                path = subtitles[name]
                result['cache_bindings'][path] = sha256_file(path)
            result['cache_bindings'][result['unsubtitled_video']] = result['unsubtitled_sha256']
        if configuration.get('finecut_feedback_seed'):
            seed = configuration['finecut_feedback_seed']
            result['cache_bindings'][seed['path']] = seed['sha256']
        if story_path is not None:
            result['cache_bindings'][str(story_path)] = sha256_file(story_path)
            for line in story['received_tts']:
                result['cache_bindings'][line['audio_path']] = line['audio_sha256']
            if story.get('rough_audio_preview'):
                preview = story['rough_audio_preview']
                result['cache_bindings'].update({preview['path']: preview['sha256'],
                    preview['receipt_path']: preview['receipt_sha256']})
                for stem in _read(preview['receipt_path'])['audio_artifacts'].values():
                    result['cache_bindings'][stem['path']] = stem['sha256']
            actual_review_path = output / 'rough_audio/review_v0.json'
            if actual_review_path.exists():
                result['cache_bindings'][str(actual_review_path)] = sha256_file(actual_review_path)
            for receipt in (output / 'rough_audio').glob('actual_av_proxy_v0*'):
                result['cache_bindings'][str(receipt)] = sha256_file(receipt)
            script_path, selection_path = output / 'rough_audio/script.md', output / 'rough_audio/selection.md'
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text('\n\n'.join(
                f"{line['start_s']:.2f}–{line['end_s']:.2f}s · {line.get('mode', 'narration')} · "
                f"{(line.get('speaker') or {}).get('role_id', '旁白')} · {line['voice']}\n\n{line['text']}"
                for line in story['audio_draft']) or '本片无语音稿。', encoding='utf-8')
            selection_path.write_text('\n\n'.join(
                f"{row['id']} · {row['start_s']:.2f}–{row['end_s']:.2f}s\n\n{row['purpose']}"
                for row in story['segments']), encoding='utf-8')
            result.update(script_path=str(script_path), selection_path=str(selection_path))
            result['cache_bindings'].update({str(path): sha256_file(path) for path in (script_path, selection_path)})
        write_once(done, result)
        return result
    except Exception as error:
        state = active.get('state')
        write_once(failure, dict(policy=POLICY, error=str(error), type=type(error).__name__,
            usage=task_usage(state, output / 'skill_finecut/creative_audio') if state else None,
            automatic_restart=False))
        raise
    finally:
        from .search_recovery import mark_settled
        mark_settled(output)
