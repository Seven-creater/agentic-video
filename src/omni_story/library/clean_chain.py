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
from .media import inventory_sources, prepare_window, sha256_file
from .opencode_provider import PROVIDER
from .resources import historical_selected_review_v1 as selected_review
from .resources import original_rough_v1 as historical
from .state import LibraryStopped, json_sha, write_json
from .story_finecut import execute_finecut
from .visual_story_trial import write_once

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
            '总成片时长和segments数量不设固定上限；由参考表达需要、实际证据和有效进展决定。')

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


ROUGH_PROMPTS = ProgressRoughPrompts()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _same_file(path, expected, reason):
    if not Path(path).is_file() or sha256_file(path) != expected:
        raise LibraryStopped('clean_chain:' + reason)


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
    actual = prepare_window(parent, 0, parent['duration_s'], output / 'chain_cache', fps=12)
    review_context = dict(reference=context['reference'], actual_render_sha256=parent['sha256'],
        blind_reading=_read(output / f'blind_reading_{selected}.json'),
        plan=_read(output / f'plan_{selected}.json'), provenance=render['provenance'],
        reference_protocol_limit=context['evidence_limit'],
        audio_review_limit='GLM vision MCP has not heard actual output audio; preserve limitation.')
    review = mcp.call(stage, selected_review.review_prompt(review_context), actual['path'],
                      lambda value: contracts.validate_review(value, reference['sha256']))
    write_once(path, review)
    state._reload()
    call = next(call for call in reversed(state.data['calls'])
                if call['name'] in {stage, stage + '_repair'} and call['status'] == 'received')
    return review, path, call['id']


def execute(reference, library, output, *, model_factory, provider_config=None,
            reference_seed=None, registry_path=None, asr=True, asr_model_dir=None):
    """Generate roughs while editing progresses, review the selection, refine it.

    A received reference seed is optional and is never tied to a historical
    call ID. Partial rough reviews may enter the skill, but a failed review
    remains a candidate delivery with a false joint gate. Only a fully settled,
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
        rough = pipeline.execute(reference, library, output, span_s=600, frames=18, max_fine=None,
            max_requests=None, asr=asr, editing_v2=False, semantic_audit=False, active_finecut=False,
            model_factory=factory, provider_config=configuration, registry_path=registry_path,
            reference_seed=reference_seed, prompt_module=ROUGH_PROMPTS,
            render_fn=historical.render_library_video, asr_model_dir=asr_model_dir)
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
        review, review_path, review_call = _selected_review(state, mcp, rough, parent, ref, context, output)
        handoff = dict(policy=POLICY, rough_video_path=parent['path'], rough_source_sha256=parent['sha256'],
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
        if any(review[field] == 'fail' for field in REVIEW_FIELDS):
            result.update(status='rough_review_failed_candidate', final_video=parent['path'],
                          final_sha256=parent['sha256'], joint_quality_gate=False)
        else:
            context.update(prior_rough_review=review, actual_rough_handoff=handoff)
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
