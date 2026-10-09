"""One explicit, same-task playable rough-to-fine server test; no restart loop."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys

from . import server_chain_authorization as authorization, server_jobs
from . import visual_story_trial as evidence
from .media import inventory_sources, sha256_file
from .opencode_provider import OpenCodeMCP
from .render import render_library_video
from .server_cli import _home, credential, load_history, settings
from .state import LibraryState, LibraryStopped, write_json
from .story_finecut import execute_finecut

MODULE = 'omni_story.library.server_chain_e2e'


class ChainMCP(OpenCodeMCP):
    def _submit(self, name, request, *, repair_of=None):
        proof = authorization.load(self.output)
        if proof is None or not authorization.allowed_stage(name):
            raise LibraryStopped('one_chain_stage_not_authorized')
        return super()._submit(name, request, repair_of=repair_of)


def rough_review_check(value, *, target=False, duration=None):
    evidence.review_check(value)
    if not isinstance(value.get('facts'), list) or not isinstance(value.get('limitations'), list):
        raise ValueError('rough_review_facts_and_limitations_lists_required')
    if target and type(value.get('ready_for_finecut')) is not bool:
        raise ValueError('rough_review_ready_for_finecut_boolean_required')
    if duration is not None:
        for fact in value['facts']:
            if not isinstance(fact, dict) or not isinstance(fact.get('description'), str) or not fact['description'].strip():
                raise ValueError('rough_visible_fact_description_required')
            start, end = fact.get('start_s'), fact.get('end_s')
            if (type(start) not in (int, float) or type(end) not in (int, float) or
                    not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration):
                raise ValueError('rough_visible_fact_output_time_out_of_range')


def ready_for_finecut(blind, review):
    # The target-aware answer cannot erase an independent picture-only failure.
    return (blind['status'] != 'fail' and review['status'] != 'fail' and
            review['ready_for_finecut'] and bool(blind['facts']))


def execute_chain(output, *, home, model_factory=None):
    output, home = Path(output).resolve(strict=True), Path(home).resolve(strict=True)
    proof = authorization.load(output)
    if proof is None:
        raise LibraryStopped('one_chain_authorization_missing')
    base = Path(proof['execution_directory'])
    base.mkdir(parents=True, exist_ok=True)
    result_path = base / 'result.json'
    if result_path.exists():
        saved = evidence.read(result_path)
        if sha256_file(saved['final_video']) != saved['final_sha256']:
            raise LibraryStopped('one_chain_delivery_changed')
        return saved
    if (output / authorization.FAILURE_REPORT).exists():
        raise LibraryStopped('one_chain_terminal_failure_no_automatic_restart')
    config = settings(home)
    history = load_history(config)
    if not history or history['reference_seed']['reference_sha256'] != proof['reference']['sha256']:
        raise LibraryStopped('one_chain_received_reference_missing')
    state = LibraryState(output, proof['input_lock'], max_requests=None,
                         registry_path=home / 'shared/server_library_runs.json')
    # The old stop marker is frozen in the authorization before this removal.
    (output / 'mcp_stop').unlink(missing_ok=True)
    mcp = None
    def progress(stage, **details):
        state._reload()
        row = dict(stage=stage, requests=state.data['request_count'], **details)
        write_json(base / 'progress.json', row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        mcp = model_factory(state) if model_factory else ChainMCP(state,
            package_root=config['mcp_package_root'], executable=config['opencode_executable'],
            exclusions=history['unknown_inputs'])
        reference_context = {
            'reference': history['reference_seed']['full_response']['reference'],
            'editing_reference': history['reference_seed']['full_response']['editing_reference'],
            'evidence_limit': history['reference_seed']['evidence_limit'],
            'source_call_id': history['reference_seed']['source_call_id']}
        evidence.write_once(base / 'reference_received_cache.json', reference_context)
        progress('rendering_actual_rough')
        # Original GLM147s draft is the rough cut, never the rejected152s refinement.
        rough = render_library_video(proof['catalog']['sources'], proof['rough_plan'],
                                     Path(proof['rough_directory']), fps=30, width=1280, height=720)
        state.set_artifact('chain_e2e_v1_rough_render', rough)
        parent = inventory_sources(rough['rendered_path'], base / 'rough_catalog')['sources'][0]
        roi = evidence.write_once(base / 'rough_layout.json', evidence.layout(parent))
        blind_media = evidence.proxy(parent, 0, parent['duration_s'], base,
                                      roi=roi['picture_crop'], label='rough_picture_only')
        scope = dict(kind='continuous_window', source_sha256=parent['sha256'],
                     source_start_s=0, source_end_s=parent['duration_s'])
        schema = ('只输出JSON {"status":"pass|partial|fail","apparent_story":"实际可见顺序",'
                  '"facts":[{"start_s":输出秒,"end_s":输出秒,"description":"可见事实"}],'
                  '"problems":["缺项"],"limitations":["观察局限"]')
        progress('rough_picture_blind_review')
        blind = mcp.call('chain_e2e_v1_rough_blind',
            '独立静音画面盲读。未提供目标故事、电影简介或剪辑计划。只依据实际画面描述人物、行动、'
            '结果和前后关系；不要借电影常识补全。裁切仅尝试排除外围字幕，画内文字依赖必须记录。'
            '粗剪不要求短；检查画面能否组成可理解的内容。不得声称穷尽观看。' + schema + '}',
            Path(blind_media['path']), lambda value: rough_review_check(value, duration=parent['duration_s']), scope=scope)
        evidence.write_once(base / 'rough_blind.json', blind)
        actual = evidence.proxy(parent, 0, parent['duration_s'], base,
                                roi=roi['crop'], label='rough_actual')
        progress('rough_content_and_continuity_review')
        context = dict(reference=reference_context, independent_blind=blind)
        review = mcp.call('chain_e2e_v1_rough_review',
            '审核这部实际粗剪。粗剪不限定时长；判定人物身份、行动与结果是否前后呼应、是否能表达参考'
            '主旨并可进入精剪。粗剪可以冗长；根本缺少可见因果或人物混乱则不可进入。不能用目标文本'
            '替代盲读事实，发现冲突要保留。' + schema + ',"ready_for_finecut":true或false}\n' +
            json.dumps(context, ensure_ascii=False), Path(actual['path']),
            lambda value: rough_review_check(value, target=True, duration=parent['duration_s']), scope=scope)
        evidence.write_once(base / 'rough_review.json', review)
        result = dict(policy=authorization.POLICY, rough_video=rough['rendered_path'],
                      rough_sha256=rough['sha256'], rough_duration_s=rough['duration_s'],
                      rough_blind=blind, rough_review=review, reference_evidence_limit=reference_context['evidence_limit'])
        if not ready_for_finecut(blind, review):
            result.update(status='rough_content_gate_not_established', final_video=rough['rendered_path'],
                          final_sha256=rough['sha256'], joint_quality_gate=False)
        else:
            progress('finecut_actual_rough_video')
            fine = execute_finecut(state, mcp, reference_context, parent, proof['reference'], base / 'fine')
            result.update(status='rough_to_fine_completed', finecut=fine,
                          final_video=fine['final_video'], final_sha256=fine['final_sha256'],
                          joint_quality_gate=(blind['status'] == review['status'] == 'pass' and
                                              fine.get('joint_quality_gate') is True))
        state._reload()
        result['usage'] = {'requests': state.data['request_count'],
                           'new_requests': state.data['request_count'] - proof['baseline_request_count']}
        evidence.write_once(result_path, result)
        progress('settled', final_video=result['final_video'], joint_quality_gate=result['joint_quality_gate'])
        return result
    except Exception as error:
        state._reload()
        evidence.write_once(output / authorization.FAILURE_REPORT,
            dict(policy=authorization.POLICY, error=str(error), type=type(error).__name__,
                 requests=state.data['request_count'], rough_video=str(base / 'rough/final.mp4'),
                 automatic_restart=False))
        progress('stopped_on_error', error=str(error))
        raise
    finally:
        (output / 'mcp_stop').write_text('one_chain_e2e_settled', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=_home())
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('start', '_run', 'status', 'logs', 'stop'):
        sub = commands.add_parser(name)
        sub.add_argument('--output', type=Path, required=True)
        if name == 'start':
            sub.add_argument('--user-instruction', required=True)
        if name == 'logs':
            sub.add_argument('--lines', type=int, default=80)
    args = parser.parse_args(argv)
    if args.command == 'start':
        config = settings(args.home)
        load_history(config)
        credential(args.home)
        authorization.register(args.output, args.user_instruction,
                               registry_path=args.home / 'shared/server_library_runs.json')
        state = evidence.read(args.output / 'library_state.json')
        original = state['artifacts']['server_output_capacity_recovery'][0]
        path = Path(original['path'])
        command = [sys.executable, '-m', MODULE, '--home', str(args.home.resolve()),
                   '_run', '--output', str(args.output.resolve())]
        value = server_jobs.start_recovery(command, args.output, Path(config['project_root']),
            authorization_path=path, authorization_sha256=sha256_file(path), recovery_name=authorization.NAME)
    elif args.command == '_run':
        os.environ['Z_AI_API_KEY'] = credential(args.home)
        try:
            value = execute_chain(args.output, home=args.home)
        finally:
            os.environ.pop('Z_AI_API_KEY', None)
    elif args.command == 'logs':
        print(server_jobs.logs(args.output, args.lines, recovery_name=authorization.NAME), end='')
        return 0
    else:
        value = getattr(server_jobs, args.command)(args.output, recovery_name=authorization.NAME)
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
