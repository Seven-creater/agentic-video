"""Restore the historically evidenced rough-generation method, then skill finecut."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import contracts, restoration_policy as policy, server_jobs
from .media import inventory_sources, prepare_window, sha256_file
from .opencode_provider import OpenCodeMCP, PROVIDER
from .resources import historical_selected_review_v1 as selected_review
from .restored_rough import run_rough
from .server_cli import _home, credential, load_history, settings, VISION_GENERATION
from .state import LibraryStopped, json_sha, write_json
from .story_finecut import execute_finecut
from .visual_story_trial import write_once

MODULE = 'omni_story.library.server_restored'


def control_output(lane):
    """Keep the original zero-call failed launch when its one CPU fix is carried."""
    correction = Path(lane) / 'controllers/bootstrap_fix_v1'
    return correction if (correction / '.omni-server/job.json').exists() else Path(lane)


class RestorationMCP(OpenCodeMCP):
    def _submit(self, name, request, *, repair_of=None):
        proof = policy.load(self.output)
        if proof is None or not policy.allowed_stage(name):
            raise LibraryStopped('restoration_stage_not_authorized')
        return super()._submit(name, request, repair_of=repair_of)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def usable_rough(result):
    review = result['review']
    return (review.get('continuity_status') == 'pass' and
            review.get('theme_status') in {'pass', 'partial'})


def _review_selected(state, mcp, rough, parent, reference, seed, lane):
    selected = rough['selected_round']
    stage = f'selected_review_v2_{selected}'
    path = lane / (stage + '.json')
    render = _read(lane / f'render_{selected}/render_result.json')
    if (type(selected) is not int or selected not in (0, 1) or
            rough['final_sha256'] != parent['sha256'] or render['sha256'] != parent['sha256'] or
            render['measured_duration_s'] != parent['duration_s']):
        raise LibraryStopped('restoration_selected_actual_render_changed')
    state._reload()
    calls = [call for call in state.data['calls'] if call['name'] in {stage, stage + '_repair'}]
    if Path(rough['selected_review_path']).resolve() == path:
        review = _read(path)
        if (review != rough['review'] or not calls or calls[-1]['status'] != 'received' or
                _read(lane / 'calls' / calls[-1]['id'] / 'parsed.json') != review or
                not _read(lane / 'calls' / calls[-1]['id'] / 'request.json')['arguments']['prompt'].startswith(
                    selected_review.review_prompt({})[:-2])):
            raise LibraryStopped('restoration_selected_review_cache_changed')
        contracts.validate_review(review, reference['sha256'])
    else:
        if (Path(rough['selected_review_path']).resolve() != lane / f'review_{selected}.json' or calls or path.exists()):
            raise LibraryStopped('restoration_selected_review_stage_already_used')
        context = dict(reference=seed['full_response']['reference'], actual_render_sha256=parent['sha256'],
            blind_reading=_read(lane / f'blind_reading_{selected}.json'),
            plan=_read(lane / f'plan_{selected}.json'), provenance=render['provenance'],
            audio_review_limit='GLM vision MCP has not heard actual output audio; preserve limitation.',
            reference_protocol_limit=seed['evidence_limit'])
        write_json(lane / 'restoration_progress.json', {'stage':'selected_review_actual_rough','selected_round':selected})
        media = prepare_window(parent, 0, parent['duration_s'], lane / 'cache', fps=12)
        review = mcp.call(stage, selected_review.review_prompt(context), media['path'],
                          lambda value: contracts.validate_review(value, reference['sha256']))
        write_once(path, review)
    reviewed = {**rough, 'review': review, 'selected_review_path': str(path)}
    write_once(lane / 'restored_reviewed_rough.json', reviewed)
    return reviewed


def execute(args):
    home, lane = args.home.resolve(strict=True), args.output.resolve(strict=True)
    configuration = settings(home)
    history = load_history(configuration)
    proof = policy.load(lane)
    if proof is None:
        raise LibraryStopped('restoration_authorization_required')
    delivery = lane / 'restoration_result.json'
    if delivery.exists():
        result = _read(delivery)
        if sha256_file(result['final_video']) != result['final_sha256']:
            raise LibraryStopped('restoration_final_changed')
        return result
    if (lane / 'restoration_failure.json').exists():
        raise LibraryStopped('restoration_known_terminal_no_automatic_restart')
    seed = proof['reference_seed']
    provider = {**PROVIDER, **policy.configuration(lane / 'authorization.json'),
                'vision_generation': dict(VISION_GENERATION)}
    active = {}
    def factory(state):
        active['state'] = state
        active['mcp'] = RestorationMCP(state, package_root=configuration['mcp_package_root'],
            executable=configuration['opencode_executable'], exclusions=history['unknown_inputs'])
        return active['mcp']
    try:
        rough = run_rough(args.reference, args.library, lane, reference_seed=seed,
            provider_config=provider, model_factory=factory,
            registry_path=home / 'shared/server_library_runs.json', asr_model_dir=args.asr_model_dir)
        state, mcp = active['state'], active['mcp']
        parent = inventory_sources(rough['final_video'], lane / 'selected_rough_catalog')['sources'][0]
        reference = _read(lane / 'reference_catalog/inventory.json')['sources'][0]
        rough = _review_selected(state, mcp, rough, parent, reference, seed, lane)
        state._reload()
        selected = rough['selected_round']
        review_stage = Path(rough['selected_review_path']).stem
        review_call = next(call for call in reversed(state.data['calls'])
                           if call['name'] in {review_stage, review_stage + '_repair'})
        handoff = dict(policy='actual_reviewed_rough_to_skill_v1',
            rough_video_path=parent['path'], rough_source_sha256=parent['sha256'],
            rough_duration_s=parent['duration_s'], selected_round=selected,
            rough_review_call_id=review_call['id'], rough_review=rough['review'],
            original_method='original_rough_v1', target_is_reference_duration=True)
        path = lane / 'rough_handoff.json'
        write_once(path, handoff)
        state.set_artifact('restored_rough_handoff', {'path': str(path), 'sha256': json_sha(handoff)})
        result = dict(policy=policy.POLICY, rough=rough, rough_video=parent['path'],
                      rough_sha256=parent['sha256'], rough_duration_s=parent['duration_s'])
        if not usable_rough(rough):
            result.update(status='rough_content_not_established', final_video=parent['path'],
                          final_sha256=parent['sha256'], joint_quality_gate=False)
        else:
            write_json(lane / 'restoration_progress.json', {'stage':'skill_finecut_actual_rough'})
            context = dict(reference=seed['full_response']['reference'],
                source_call_id=seed['source_call_id'], evidence_limit=seed['evidence_limit'],
                prior_rough_review=rough['review'], actual_rough_handoff=handoff)
            fine = execute_finecut(state, mcp, context, parent, reference, lane / 'skill_finecut')
            result.update(status='restored_rough_to_fine_completed', finecut=fine,
                final_video=fine['final_video'], final_sha256=fine['final_sha256'],
                joint_quality_gate=(rough['review']['theme_status'] == 'pass' and
                                    fine.get('joint_quality_gate') is True))
        state._reload()
        result['usage'] = policy.aggregate_usage(proof, state.data['request_count'])
        result['historical_method_restored'] = True
        result['same77_output_is_not_guaranteed'] = True
        write_once(delivery, result)
        write_json(lane / 'restoration_progress.json', {'stage':'settled','final_video':result['final_video']})
        return result
    except Exception as error:
        count = 0
        if active.get('state'):
            active['state']._reload(); count = active['state'].data['request_count']
        write_once(lane / 'restoration_failure.json', dict(error=str(error), type=type(error).__name__,
            usage=policy.aggregate_usage(proof, count), automatic_restart=False))
        raise
    finally:
        (lane / 'mcp_stop').write_text('restoration_settled', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=_home())
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('start', '_run', 'status', 'logs', 'stop'):
        command = subs.add_parser(name)
        command.add_argument('--output', type=Path, required=True)
        if name in ('start', '_run'):
            command.add_argument('--reference', type=Path, required=True)
            command.add_argument('--library', type=Path, required=True)
            command.add_argument('--asr-model-dir', type=Path, required=True)
        if name == 'start':
            command.add_argument('--parent-output', type=Path, required=True)
            command.add_argument('--reference-seed', type=Path, required=True)
            command.add_argument('--user-instruction', required=True)
    args = parser.parse_args(argv)
    if args.command == 'start':
        from .resources import original_rough_v1
        config = settings(args.home)
        history = load_history(config)
        credential(args.home)
        seed = _read(args.reference_seed)
        args.asr_model_dir.resolve(strict=True)
        policy.register(args.parent_output, args.output, args.user_instruction, seed,
                        history=history, method_provenance={**original_rough_v1.provenance(),
                            'selected_actual_video_review': selected_review.provenance()})
        command = [sys.executable, '-m', MODULE, '--home', str(args.home.resolve()), '_run',
            '--reference',str(args.reference.resolve(strict=True)), '--library',str(args.library.resolve(strict=True)),
            '--output',str(args.output.resolve()), '--asr-model-dir',str(args.asr_model_dir.resolve(strict=True))]
        # Registration creates a populated lane; the supervisor's mkdir is its launch-once lock.
        cwd = server_jobs._launch_arguments(command, Path(config['project_root']))
        result = server_jobs._launch(command, args.output.resolve(strict=True), cwd)
    elif args.command == '_run':
        os.environ['Z_AI_API_KEY'] = credential(args.home)
        try:
            result = execute(args)
        finally:
            os.environ.pop('Z_AI_API_KEY', None)
    elif args.command == 'logs':
        print(server_jobs.logs(control_output(args.output)), end=''); return 0
    else:
        result = getattr(server_jobs, args.command)(control_output(args.output))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
