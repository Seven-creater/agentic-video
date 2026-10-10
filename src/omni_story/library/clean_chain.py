"""Historical rough -> actual-video review -> generic skill, driven by progress.

The caller owns credentials, model transport and launch control. This adapter
keeps one injected MCP/state ledger, preserves the rough result, and never
automatically resumes a failed or interrupted chain.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from . import contracts, pipeline
from .media import inventory_sources, sha256_file
from .opencode_provider import PROVIDER
from .resources import historical_selected_review_v1 as selected_review
from .resources import original_rough_v1 as historical
from .state import LibraryState, LibraryStopped, json_sha, write_json
from .story_finecut import execute_finecut
from .visual_story_trial import proxy, write_once

POLICY = 'reference_rough_skill_v1'
REVIEW_FIELDS = ('theme_status', 'editing_status', 'continuity_status')


class ProgressRoughPrompts:
    """Keep verified historical instructions while removing fixed total caps."""

    def __getattr__(self, name):
        return getattr(historical, name)

    @staticmethod
    def _plan(template, context):
        context = {**context, 'render_capabilities': {key: value for key, value in
            context.get('render_capabilities', {}).items() if key not in {'max_duration_s', 'max_segments'}}}
        return template(context).replace(
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

    def plan_prompt(self, context):
        return self._plan(historical.plan_prompt, context)

    def search_prompt(self, context):
        context = {key: value for key, value in context.items() if key != 'remaining_window_budget'}
        context['instruction'] = '按实际缺项提出下一批连续窗口；观察总数不设固定上限，不重复已确认证据；不要换参考。'
        batch = context.get('max_windows_this_round', 8)
        batching = (f'每批不超过{batch}个、' if batch is not None else '按接口容量分批、')
        return historical.search_prompt(context).replace(
            '一次请求不超过12个连续窗口，每窗口不超过90秒，区间必须在catalog实际时长内。',
            f'连续窗口按工具容量分批提交，{batching}每窗口不超过90秒；'
            '观察总数不设固定上限，区间必须在catalog实际时长内。没有新证据可查时windows可为[]并说明缺项。')

    def for_round(self, round_no):
        if type(round_no) is not int or round_no < 0:
            raise ValueError('clean_chain:nonnegative_round_required')
        phase = historical.for_round(min(round_no, 1))
        return SimpleNamespace(fine_prompt=phase.fine_prompt, review_prompt=phase.review_prompt,
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


def _received_artifact(parent, saved, stage, filename):
    value = _read(parent / filename)
    for call in reversed(saved['calls']):
        if call['name'] not in {stage, stage + '_repair'} or call['status'] != 'received':
            continue
        folder = parent / 'calls' / call['id']
        if not (folder / 'parsed.json').is_file():
            continue
        request, response = _read(folder / 'request.json'), _read(folder / 'response.json')
        text = '\n'.join(row['text'] for row in response['result']['content'] if row.get('type') == 'text')
        if (json_sha(request) != call['request_sha256'] or json_sha(response) != call['response_sha256'] or
                _read(folder / 'parsed.json') != value or contracts.parse_model_json(text) != value):
            raise LibraryStopped('clean_chain:rough_received_artifact_changed')
        return value
    raise LibraryStopped('clean_chain:rough_received_artifact_unbound')


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
    if _received_artifact(parent, saved, selected_stage, selected_stage + '.json') != handoff['rough_review']:
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
    review = mcp.call(stage, selected_review.review_prompt(review_context), actual['path'],
                      lambda value: contracts.validate_review(value, reference['sha256']))
    write_once(path, review)
    state._reload()
    call = next(call for call in reversed(state.data['calls'])
                if call['name'] in {stage, stage + '_repair'} and call['status'] == 'received')
    return review, path, call['id']


def execute(reference, library, output, *, model_factory, provider_config=None,
            reference_seed=None, registry_path=None, asr=True, asr_model_dir=None, rough_task=None):
    """Generate roughs while editing progresses, review the selection, refine it.

    A received reference seed is optional and is never tied to a historical
    call ID. Partial rough reviews may enter the skill. Failed reviews stop by
    default; explicit functional tests continue with a false joint gate. Only a fully settled,
    hash-verified result can be reused without invoking models or rendering.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    configuration = {**PROVIDER, **(provider_config or {}), 'workflow': POLICY, 'asr': asr}
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
        for path, digest in saved['cache_bindings'].items():
            _same_file(path, digest, 'cached_artifact_changed')
        return saved
    failure = output / 'chain_failure.json'
    if failure.exists():
        raise LibraryStopped('clean_chain:terminal_failure_no_automatic_restart')
    try:
        with (output / 'chain_started').open('x', encoding='utf-8') as handle:
            handle.write(POLICY)
    except FileExistsError as error:
        raise LibraryStopped('clean_chain:interrupted_no_automatic_restart') from error
    active = {}
    try:
        if not callable(model_factory) or type(asr) is not bool:
            raise ValueError('clean_chain:model_factory_and_boolean_asr_required')
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
        if rough_task is None:
            rough = pipeline.execute(reference, library, output, span_s=600, frames=18, max_fine=None,
                max_requests=None, asr=asr, editing_v2=False, semantic_audit=False, active_finecut=False,
                model_factory=factory, provider_config=configuration, registry_path=registry_path,
                reference_seed=reference_seed, prompt_module=ROUGH_PROMPTS,
                render_fn=historical.render_library_video, asr_model_dir=asr_model_dir)
        else:
            rough, inherited_handoff = _continued_rough(rough_task, output, reference, library,
                configuration, factory, registry_path)
        state, mcp = active['state'], active['mcp']
        _same_file(rough['final_video'], rough['final_sha256'], 'rough_bytes_changed')
        original_result_sha = sha256_file(output / 'result.json')
        parent = inventory_sources(rough['final_video'], output / 'selected_rough_catalog')['sources'][0]
        ref = _read(output / 'reference_catalog/inventory.json')['sources'][0]
        reading = reference_seed['full_response']['reference'] if reference_seed else _read(output / 'reference_reading.json')
        context = dict(reference=reading, evidence_limit=(reference_seed['evidence_limit'] if reference_seed
            else rough.get('evidence_limit', 'Received reference observation; native sampling and audio craft remain unverified.')))
        if reference_seed and reference_seed.get('source_call_id'):
            context['source_call_id'] = reference_seed['source_call_id']
        if inherited_handoff is None:
            review, review_path, review_call = _selected_review(state, mcp, rough, parent, ref, context, output)
        else:
            review = inherited_handoff['rough_review']
            review_path = Path(inherited_handoff['selected_review_path'])
            review_call = inherited_handoff['rough_review_call_id']
            if _read(review_path) != review:
                raise LibraryStopped('clean_chain:inherited_review_changed')
            contracts.validate_review(review, ref['sha256'])
        handoff = inherited_handoff or dict(policy=POLICY, rough_video_path=parent['path'], rough_source_sha256=parent['sha256'],
            rough_duration_s=parent['duration_s'], selected_round=rough['selected_round'],
            rough_review_call_id=review_call, rough_review=review,
            original_rough_review=rough['review'], selected_review_path=str(review_path),
            method_provenance={**historical.provenance(), 'runtime_adaptation':
                'progress_driven_without_fixed_total_duration_segments_windows_rounds_or_renders'},
            selected_review_provenance=selected_review.provenance())
        handoff_path = output / 'rough_handoff.json'
        write_once(handoff_path, handoff)
        state.set_artifact('clean_chain_rough_handoff', dict(path=str(handoff_path), sha256=json_sha(handoff)))
        result = dict(policy=POLICY, input=identity, rough=rough, rough_review=review,
                      rough_video=parent['path'], rough_sha256=parent['sha256'], rough_duration_s=parent['duration_s'])
        if (any(review[field] == 'fail' for field in REVIEW_FIELDS) and
                quality_policy != 'functional_test_keep_negative_reviews'):
            result.update(status='rough_review_failed_candidate', final_video=parent['path'],
                          final_sha256=parent['sha256'], joint_quality_gate=False)
        else:
            context.update(prior_rough_review=review, actual_rough_handoff=handoff)
            if inherited_handoff and (Path(rough_task) / 'skill_finecut/input.json').is_file():
                old_input = _read(Path(rough_task) / 'skill_finecut/input.json')
                if json_sha(context) != old_input['reference_context_sha256']:
                    raise LibraryStopped('clean_chain:inherited_skill_context_changed')
            fine = execute_finecut(state, mcp, context, parent, ref, output / 'skill_finecut')
            result.update(status='rough_to_skill_completed', finecut=fine, final_video=fine['final_video'],
                final_sha256=fine['final_sha256'], joint_quality_gate=(all(review[field] == 'pass'
                    for field in REVIEW_FIELDS) and fine.get('joint_quality_gate') is True))
        _same_file(output / 'result.json', original_result_sha, 'rough_result_changed')
        _same_file(result['final_video'], result['final_sha256'], 'final_bytes_changed')
        state._reload()
        result['usage'] = state.usage()
        result['cache_bindings'] = {str(path): sha256_file(path) for path in
            (output / 'result.json', handoff_path, review_path, Path(parent['path']),
             Path(ref['path']), Path(result['final_video']))}
        write_once(done, result)
        return result
    except Exception as error:
        state = active.get('state')
        write_once(failure, dict(policy=POLICY, error=str(error), type=type(error).__name__,
            usage=state.usage() if state else None, automatic_restart=False))
        raise
    finally:
        (output / 'mcp_stop').write_text('reference_rough_skill_settled', encoding='utf-8')
