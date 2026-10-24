"""Independent Linux editing jobs through OpenCode and the official vision MCP."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import server_jobs
from .contracts import parse_model_json
from .media import sha256_file
from .opencode_provider import OpenCodeMCP, PROVIDER, bound_media_scope
from .state import LibraryStopped, json_sha, write_json


VISION_GENERATION = {
    'policy': 'opencode_vision_capacity_v2',
    'max_output_tokens': 32768,
    'model_timeout_ms': 1200000,
    'tool_timeout_ms': 1260000,
}


def _vision_generation(output):
    """Lock fresh jobs; preserve the complete configuration of existing jobs."""
    path = Path(output) / 'library_state.json'
    if path.exists():
        config = json.loads(path.read_text(encoding='utf-8'))['input_lock']['configuration']
        if 'vision_generation' not in config:
            return None
        generation = config['vision_generation']
    else:
        generation = VISION_GENERATION
    if (not isinstance(generation, dict) or generation != VISION_GENERATION or
            any(type(generation.get(key)) is not int for key in
                ('max_output_tokens', 'model_timeout_ms', 'tool_timeout_ms'))):
        raise LibraryStopped('server_vision_generation_invalid')
    return dict(generation)


def _home():
    return Path(os.environ.get('OMNI_SERVER_HOME', Path.home() / '.local/share/agentic-video')).resolve()


def settings(home):
    return json.loads((Path(home) / 'server.json').read_text(encoding='utf-8'))


def load_history(config):
    """Verify the transported records without resolving historical Windows paths."""
    if not config.get('history_file'):
        return None
    path = Path(config['history_file'])
    if sha256_file(path) != config['history_sha256']:
        raise LibraryStopped('server_history_manifest_changed')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value['policy'] != 'server_append_only_migration_v1' or
            len(value['historical_calls']) != value['baseline_requests'] or
            json_sha(value['historical_calls']) != value['baseline_calls_sha256']):
        raise LibraryStopped('server_history_baseline_changed')
    seed = value['reference_seed']
    call = next(c for c in value['historical_calls'] if c['id'] == seed['source_call_id'])
    if (call['status'] != 'received' or json_sha(seed) != value['reference_seed_sha256'] or
            json_sha(value['seed_request']) != seed['request_sha256'] or
            seed['request_sha256'] != call['request_sha256'] or
            json_sha(value['seed_response']) != seed['response_sha256'] or
            seed['response_sha256'] != call['response_sha256']):
        raise LibraryStopped('server_cached_reference_binding_changed')
    text = '\n'.join(c['text'] for c in value['seed_response']['result']['content'] if c.get('type') == 'text')
    if parse_model_json(text) != seed['full_response']:
        raise LibraryStopped('server_cached_reference_content_changed')
    unknown = [c['id'] for c in value['historical_calls'] if c['status'] == 'uncertain']
    if unknown != [c['call_id'] for c in value['unknown_inputs']]:
        raise LibraryStopped('server_history_unknown_exclusions_changed')
    return value


def credential(home):
    path = Path(home) / 'credentials.json'
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise LibraryStopped('server_credential_permissions_must_be_600')
    key = json.loads(path.read_text(encoding='utf-8'))['Z_AI_API_KEY']
    if not isinstance(key, str) or not key.strip():
        raise LibraryStopped('server_GLM_key_empty')
    return key


def reference_cache(path, reference):
    """Read a received model record; never synthesize an interpretation."""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    call, request, response = value['call'], value['request'], value['response']
    if (call['status'] != 'received' or json_sha(request) != call['request_sha256'] or
            json_sha(response) != call['response_sha256']):
        raise LibraryStopped('reference_cache_original_record_changed')
    text = '\n'.join(row['text'] for row in response['result']['content'] if row.get('type') == 'text')
    parsed = parse_model_json(text)
    reading = parsed.get('reference', parsed)
    if reading.get('reference_sha256') != sha256_file(reference):
        raise LibraryStopped('reference_cache_video_mismatch')
    return dict(source_call_id=call['id'], request_sha256=call['request_sha256'],
                response_sha256=call['response_sha256'], reference_sha256=reading['reference_sha256'],
                full_response={'reference': reading},
                evidence_limit='Reuse this received original model observation, including its errors and uncertainties; not human truth.')


def reference_context_file(path, reference):
    """Carry captured titles and received sound/picture observations with sources."""
    path = Path(path).resolve(strict=True)
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('sha256') != sha256_file(reference):
        raise LibraryStopped('reference_context_video_mismatch')
    evidence = value['evidence']
    review = evidence['template_review']
    if sha256_file(review['ledger_path']) != review['ledger_sha256']:
        raise LibraryStopped('reference_context_comparison_changed')
    titles = [{key: row[key] for key in ('id', 'author_id', 'title')}
              for row in evidence['authors'][:3]]
    result = dict(path=str(path), sha256=sha256_file(path), reference_sha256=value['sha256'],
                hot_title=evidence['trend']['sentence'], shared_pattern=review['shared_pattern'],
                author_titles=titles,
                limitations=[*review['limitations'], evidence['limitation']],
                comparison_ledger=dict(path=review['ledger_path'], sha256=review['ledger_sha256']))
    if review.get('representative_brief'):
        selected = review['representative_id']
        if review['representative_brief'].get('member_id') != selected:
            raise LibraryStopped('reference_context_representative_mismatch')
        result['received_av_observation'] = dict(
            representative_brief=review['representative_brief'],
            member_evidence=[row for row in review['member_evidence'] if row['id'] == selected],
            response_sha256=review['response_sha256'],
            evidence_limit='Received model listening/viewing, not independently verified truth; local times belong to this reference.')
    return result


def reference_strategy_file(path, reference):
    """Load an explicit user strategy for these exact reference bytes."""
    from .contracts import reference_strategy
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('reference_sha256') != sha256_file(reference):
        raise LibraryStopped('reference_strategy_video_mismatch')
    strategy = value.get('strategy')
    if not isinstance(strategy, dict) or not all(key in strategy for key in
            ('adaptation_mode', 'speech_mode', 'timing_driver')):
        raise ValueError('reference_strategy_complete_strategy_required')
    result = {'reference_strategy': reference_strategy(value, creative_audio=True)}
    if 'speech_form' in value:
        speech_form = value['speech_form']
        if not isinstance(speech_form, str) or not speech_form.strip():
            raise ValueError('reference_strategy_speech_form_text_required')
        result['reference_speech_form'] = speech_form
    if 'finecut_feedback_seed' in value:
        seed = value['finecut_feedback_seed']
        feedback = Path(seed['path']).resolve(strict=True)
        if not feedback.is_file() or sha256_file(feedback) != seed['sha256']:
            raise LibraryStopped('finecut_feedback_seed_changed')
        result['finecut_feedback_seed'] = dict(path=str(feedback), sha256=seed['sha256'])
    return result


def task_job(task):
    """The hot entry owns one job; its edit directory has no second worker."""
    task = Path(task).resolve(strict=True)
    if (task / '.omni-server/job.json').is_file():
        return server_jobs.status(task)
    if task.name == 'edit' and (task.parent / '.omni-server/job.json').is_file():
        job = server_jobs.status(task.parent)
        command = job.get('command', [])
        if ('omni_story.library.hot_pipeline' in command and '--output' in command and
                Path(command[command.index('--output') + 1]).resolve() == task.parent):
            return job
    raise LibraryStopped('task_job_owner_not_found')


def parent_baseline(parent, output, parallel_tasks=()):
    """Link an explicitly requested new evaluation without resetting old usage."""
    parent, output = Path(parent).resolve(strict=True), Path(output).resolve()
    if not output.is_relative_to(parent / 'evaluations'):
        raise LibraryStopped('new_evaluation_must_remain_inside_parent_evaluations')
    path = parent / 'library_state.json'
    state = json.loads(path.read_text(encoding='utf-8'))
    files = [path, *sorted((parent / 'artifacts').glob('*/library_state.json'))]
    audio_ledger = parent / 'skill_finecut/creative_audio/library_state.json'
    if audio_ledger.is_file():
        files.append(audio_ledger)
    counted = set(files)
    ledgers = []
    configuration = state['input_lock']['configuration']
    unknown = list(configuration.get('unknown_inputs', []))
    for item in files:
        saved = json.loads(item.read_text(encoding='utf-8'))
        saved_config = saved['input_lock']['configuration']
        inherited = list(saved_config.get('parent_baseline', {}).get('linked_ledgers', []))
        if saved_config.get('predecessor'):
            inherited.append(saved_config['predecessor'])
        for row in inherited:
            ancestor = Path(row['path']).resolve(strict=True)
            if sha256_file(ancestor) != row['sha256']:
                raise LibraryStopped('parent_ancestor_ledger_changed')
            if ancestor not in files:
                files.append(ancestor)
        if type(saved['request_count']) is not int or saved['request_count'] != len(saved['calls']):
            raise LibraryStopped('parent_call_count_changed')
        if any(row['status'] == 'submitted' for row in saved['calls']):
            raise LibraryStopped('parent_request_still_submitted')
        unknown.extend(saved['input_lock']['configuration'].get('unknown_inputs', []))
        ledgers.append(dict(path=str(item), sha256=sha256_file(item),
                            task_id=saved['task_id'], request_count=saved['request_count']))
        for call in saved['calls']:
            if call['status'] != 'uncertain':
                continue
            request_path = item.parent / 'calls' / call['id'] / 'request.json'
            request = json.loads(request_path.read_text(encoding='utf-8'))
            if json_sha(request) != call['request_sha256']:
                raise LibraryStopped('parent_unknown_request_changed')
            scope = bound_media_scope(request)
            media = Path(request['arguments'].get('image_source') or request['arguments']['video_source'])
            lineage = media.parent / 'lineage.json'
            unknown.append(dict(call_id=call['id'], source_task_id=saved['task_id'],
                source_ledger_path=str(item), request_sha256=call['request_sha256'],
                media_sha256=request['media_sha256'], scope=scope,
                lineage_path=str(lineage) if lineage.exists() else None,
                lineage_sha256=sha256_file(lineage) if lineage.exists() else None))
    baseline = dict(path=str(path), sha256=sha256_file(path), task_id=state['task_id'],
                linked_ledgers=ledgers, request_count=sum(row['request_count'] for row in ledgers
                    if Path(row['path']) in counted),
                unknown_inputs=list({json_sha(row): row for row in unknown}.values()),
                prior_requests=state['input_lock']['configuration'].get('prior_requests', 0))
    if parallel_tasks:
        # Siblings share one history; only their distinct new calls are added.
        residual = baseline['prior_requests'] - sum(row['request_count'] for row in ledgers
                                                   if Path(row['path']) not in counted)
        unique = {row['path']: row for row in ledgers}
        inherited = {row['path']: row['sha256'] for row in ledgers if Path(row['path']) not in counted}
        seen = {parent}
        for sibling in (parent, *parallel_tasks):
            sibling = Path(sibling).resolve(strict=True)
            worker_path = sibling / 'worker.json'
            if worker_path.is_file():
                settled = json.loads(worker_path.read_text(encoding='utf-8'))['status'] == 'completed'
            else:
                job = task_job(sibling)
                settled = (job['state'] in {'failed', 'succeeded', 'stopped'} and
                           not server_jobs._group_running(job.get('child_pid')))
            current = json.loads((sibling / 'library_state.json').read_text(encoding='utf-8'))
            if not settled or any(
                    call['status'] not in {'received', 'failed_known'} for call in current['calls']):
                raise LibraryStopped('parallel_library_not_completed_with_known_results')
            if sibling in seen:
                continue
            other = parent_baseline(sibling, sibling / 'evaluations/next')
            other_residual = other['prior_requests'] - (
                sum(row['request_count'] for row in other['linked_ledgers']) - other['request_count'])
            if residual < 0 or other_residual != residual or not any(
                    inherited.get(row['path']) == row['sha256'] for row in other['linked_ledgers']):
                raise LibraryStopped('parallel_library_requires_same_history')
            seen.add(sibling)
            for row in other['linked_ledgers']:
                # Independent continuations can have distinct ancestors; overlaps must agree.
                if row['path'] in unique and unique[row['path']]['sha256'] != row['sha256']:
                    raise LibraryStopped('parallel_library_history_changed')
                unique[row['path']] = row
            unknown.extend(other['unknown_inputs'])
        baseline['prior_requests'] = residual
        baseline['request_count'] = sum(row['request_count'] for row in unique.values())
        baseline['linked_ledgers'] = list(unique.values())
        baseline['parallel_tasks'] = sorted(map(str, seen))
        baseline['unknown_inputs'] = list({json_sha(row): row for row in unknown}.values())
    return baseline


def received_plan_config(parent):
    """Continue a settled plan from its original replies and visual records."""
    parent = Path(parent).resolve(strict=True)
    job = task_job(parent)
    if job['state'] not in {'failed', 'succeeded', 'stopped'} or server_jobs._group_running(job.get('child_pid')):
        raise LibraryStopped('reuse_plan_parent_not_settled')
    ledger = parent / 'library_state.json'
    saved = json.loads(ledger.read_text(encoding='utf-8'))
    if any(call['status'] not in {'received', 'failed_known'} for call in saved['calls']):
        raise LibraryStopped('reuse_plan_parent_results_not_known')
    plans = [call for call in saved['calls'] if call['status'] == 'received' and call['name'].startswith('plan_')]
    if not plans:
        raise LibraryStopped('reuse_plan_received_plan_required')
    binding = {'path': str(ledger), 'sha256': sha256_file(ledger)}
    windows = parent / 'watched_windows.json'
    return {'received_plan_seed': {'ledger': binding, 'call_id': plans[-1]['id']},
            'received_visual_seed': {'ledger': binding,
                'windows': {'path': str(windows), 'sha256': sha256_file(windows)}}}


def configure(home):
    """Store the secret once outside the checkout; prompt never echoes it."""
    settings(home)
    key = getpass.getpass('GLM Coding Plan API key (hidden): ').strip()
    if not key:
        raise ValueError('empty key was not saved')
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    temporary = home / 'credentials.json.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(temporary, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump({'Z_AI_API_KEY': key}, handle)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, home / 'credentials.json')
    print('GLM key saved privately. No model request was sent.', flush=True)


def doctor(home):
    config = settings(home)
    package = Path(config['mcp_package_root']) / 'node_modules/@z_ai/mcp-server/package.json'
    report = {'python': sys.version.split()[0],
              'tools': {tool: bool(shutil.which(config.get('opencode_executable', 'opencode') if tool == 'opencode' else tool))
                        for tool in ('opencode', 'node', 'ffmpeg', 'ffprobe')},
              'official_mcp': json.loads(package.read_text())['version'] if package.exists() else None,
              'credential_present': (Path(home) / 'credentials.json').exists(),
              'history_baseline_requests': (load_history(config) or {}).get('baseline_requests', 0),
              'model_requests_sent': 0}
    if report['credential_present']:
        credential(home)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(report['tools'].values()) and report['official_mcp'] and report['credential_present'] else 1


def _run(args):
    config = settings(args.home)
    history = load_history(config)
    key = credential(args.home)
    os.environ['Z_AI_API_KEY'] = key
    loaded_audio_key = False
    if getattr(args, 'creative_audio', False) and not os.environ.get('DASHSCOPE_API_KEY'):
        audio_key = json.loads((Path(args.home) / 'credentials.json').read_text(encoding='utf-8')).get('DASHSCOPE_API_KEY')
        if not isinstance(audio_key, str) or not audio_key.strip():
            raise LibraryStopped('server_audio_key_missing')
        os.environ['DASHSCOPE_API_KEY'] = audio_key
        loaded_audio_key = True
    reference = args.reference.resolve(strict=True)
    seed = (history['reference_seed'] if history and
            sha256_file(reference) == history['reference_seed']['reference_sha256'] else None)
    if args.reference_cache:
        seed = reference_cache(args.reference_cache, reference)
    provider_config = dict(PROVIDER)
    provider_config['workflow'] = 'reference_rough_skill_v1'
    if getattr(args, 'reference_context', None):
        provider_config['reference_context'] = reference_context_file(args.reference_context, reference)
    if getattr(args, 'reference_strategy', None):
        provider_config.update(reference_strategy_file(args.reference_strategy, reference))
    if getattr(args, 'stop_after_rough', False):
        provider_config['stop_after_rough'] = True
    if getattr(args, 'creative_audio', False):
        provider_config['creative_audio'] = True
        state_path = Path(args.output) / 'library_state.json'
        reasoning = (json.loads(state_path.read_text(encoding='utf-8'))['input_lock']['configuration']
            .get('vision_reasoning_effort') if state_path.exists() else 'low')
        if reasoning is not None:
            provider_config['vision_reasoning_effort'] = reasoning
        if args.audio_music_bed:
            provider_config['audio_music_bed'] = str(args.audio_music_bed.resolve(strict=True))
        if getattr(args, 'visual_library', None):
            provider_config['visual_library_root'] = str(args.visual_library.resolve(strict=True))
    if getattr(args, 'functional_test', False):
        provider_config['quality_policy'] = 'functional_test_keep_negative_reviews'
    generation = _vision_generation(args.output)
    if generation is not None:
        provider_config['vision_generation'] = generation
    if history:
        provider_config.update(history_sha256=config['history_sha256'],
                               prior_requests=history['baseline_requests'])
    if getattr(args, 'parallel_task', []) and not args.parent_task:
        raise LibraryStopped('parallel_task_requires_parent_task')
    baseline = parent_baseline(args.parent_task, args.output,
        getattr(args, 'parallel_task', [])) if args.parent_task else None
    rough_task = getattr(args, 'rough_task', None)
    if rough_task is not None:
        if args.parent_task is None or rough_task.resolve() != args.parent_task.resolve():
            raise LibraryStopped('rough_task_must_be_the_explicit_parent_task')
        job = task_job(rough_task)
        if job['state'] not in {'failed', 'succeeded', 'stopped'} or server_jobs._group_running(job.get('child_pid')):
            raise LibraryStopped('rough_task_worker_not_settled')
    if baseline:
        provider_config.update(parent_baseline=baseline,
            prior_requests=max(provider_config.get('prior_requests', 0), baseline['prior_requests']) + baseline['request_count'])
    if getattr(args, 'reuse_plan', False):
        if not baseline or not args.creative_audio or rough_task is not None:
            raise LibraryStopped('reuse_plan_requires_creative_parent_without_rough_task')
        provider_config.update(received_plan_config(args.parent_task))
    from .clean_chain import execute
    def factory(state):
        if history:
            state.set_artifact('server_history_migration', {
                'policy': history['policy'], 'baseline_task_id': history['baseline_task_id'],
                'baseline_requests': history['baseline_requests'],
                'manifest_sha256': config['history_sha256'],
                'prior_calls_are_not_refunded_or_replayed': True})
        return OpenCodeMCP(state, package_root=config['mcp_package_root'],
            executable=config['opencode_executable'], exclusions=[*(history or {}).get('unknown_inputs', []),
                *(baseline or {}).get('unknown_inputs', [])])
    try:
        result = execute(reference, args.library.resolve(strict=True), args.output.resolve(),
            asr=args.asr, asr_model_dir=args.asr_model_dir,
            model_factory=factory, provider_config=provider_config, reference_seed=seed,
            registry_path=Path(args.home) / 'shared/server_library_runs.json',
            **({'search_recovery_proof': args.search_recovery_proof}
               if getattr(args, 'search_recovery_proof', None) is not None else {}),
            **({'rough_task': rough_task} if rough_task is not None else {}))
        if baseline and any(sha256_file(row['path']) != row['sha256'] for row in baseline['linked_ledgers']):
            raise LibraryStopped('parent_ledger_changed_during_evaluation')
        cumulative = provider_config.get('prior_requests', 0) + result.get('usage', {}).get('requests', 0)
        print(json.dumps({'result_path': str(args.output.resolve() / 'chain_result.json'),
                          'lineage_cumulative_vision_requests': cumulative,
                          'agent_usage_recorded_separately': True}, ensure_ascii=False), flush=True)
    finally:
        os.environ.pop('Z_AI_API_KEY', None)
        if loaded_audio_key:
            os.environ.pop('DASHSCOPE_API_KEY', None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=_home())
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('configure', help='Save GLM key using hidden terminal input.')
    commands.add_parser('doctor', help='Check environment without model requests.')
    start = commands.add_parser('start', help='Start one detached editing task; no automatic new round.')
    worker = commands.add_parser('_run', help=argparse.SUPPRESS)
    for p in (start, worker):
        p.add_argument('--reference', type=Path, required=True)
        p.add_argument('--creative-audio', action='store_true',
                       help='Plan narration with the rough cut, reuse its TTS at finecut, then review actual audiovisual content.')
        p.add_argument('--stop-after-rough', action='store_true',
                       help='Deliver the rough cut and optional draft voice preview, then stop before finecut.')
        p.add_argument('--audio-music-bed', type=Path,
                       help='Optional verified pure music stem for creative audio; never a reference dialogue mix.')
        p.add_argument('--visual-library', type=Path,
                       help='Reuse the shared visual catalog before observing new footage; requires --creative-audio.')
        p.add_argument('--library', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--asr', action='store_true', help='Optional CPU ASR; downloads weights on first use.')
        p.add_argument('--asr-model-dir', type=Path, help='Use previously verified local ASR weights.')
        p.add_argument('--reference-cache', type=Path, help='Reuse a hash-bound received original reference call.')
        p.add_argument('--reference-context', type=Path,
                       help='Exact-video capture receipt with attributed hot title and received same-template context.')
        p.add_argument('--reference-strategy', type=Path,
                       help='Optional JSON user strategy and speech form, bound to reference_sha256.')
        if p is worker:
            p.add_argument('--search-recovery-proof', type=Path,
                           help=argparse.SUPPRESS)
        p.add_argument('--parent-task', type=Path, help='Bind a new evaluation under this existing task/evaluations.')
        p.add_argument('--reuse-plan', action='store_true',
                       help='Reuse the settled parent\'s received plan and visual records without selecting or planning again.')
        p.add_argument('--parallel-task', type=Path, action='append', default=[],
                       help='Also inherit a completed sibling library task sharing the same history; repeat for each sibling.')
        p.add_argument('--rough-task', type=Path, help='Explicitly continue settled actual roughs under --parent-task; no rough regeneration.')
        p.add_argument('--functional-test', action='store_true',
                       help='Exercise the full chain despite failed rough quality reviews; retain negative reviews and the false quality gate.')
    for name in ('status', 'logs', 'stop'):
        p = commands.add_parser(name)
        p.add_argument('--output', type=Path, required=True)
        if name == 'logs':
            p.add_argument('--lines', type=int, default=80)
    args = parser.parse_args(argv)
    if args.command == 'configure':
        configure(args.home)
    elif args.command == 'doctor':
        return doctor(args.home)
    elif args.command == 'start':
        config = settings(args.home)
        load_history(config)
        credential(args.home)
        reference, library = args.reference.resolve(strict=True), args.library.resolve(strict=True)
        if not reference.is_file() or not library.is_dir() or not any(library.iterdir()):
            parser.error('reference must be a file and library a nonempty directory')
        if args.visual_library is not None and not args.creative_audio:
            parser.error('--visual-library requires --creative-audio')
        if args.reference_strategy is not None:
            reference_strategy_file(args.reference_strategy, reference)
        if args.reference_context is not None:
            reference_context_file(args.reference_context, reference)
        if args.reuse_plan:
            if not args.parent_task or not args.creative_audio or args.rough_task is not None:
                parser.error('--reuse-plan requires --creative-audio and --parent-task, without --rough-task')
            received_plan_config(args.parent_task)
        if reference.name.endswith('.part') or any(p.name.endswith('.part') for p in library.iterdir()):
            parser.error('media upload is incomplete; wait for SHA verification and .part rename')
        command = [sys.executable, '-m', 'omni_story.library.server_cli', '--home', str(args.home.resolve()), '_run',
                   '--reference', str(reference), '--library', str(library), '--output', str(args.output.resolve())]
        if args.asr:
            command.append('--asr')
        if args.functional_test:
            command.append('--functional-test')
        if args.creative_audio:
            command += ['--creative-audio']
        if args.stop_after_rough:
            command.append('--stop-after-rough')
        if args.reuse_plan:
            command.append('--reuse-plan')
        for option in ('asr_model_dir', 'reference_cache', 'reference_context', 'reference_strategy', 'parent_task', 'rough_task', 'audio_music_bed', 'visual_library'):
            value = getattr(args, option)
            if value is not None:
                command.extend(['--' + option.replace('_', '-'), str(value.resolve(strict=True))])
        for value in args.parallel_task:
            command.extend(['--parallel-task', str(value.resolve(strict=True))])
        result = server_jobs.start(command, args.output, Path(config['project_root']))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == '_run':
        _run(args)
    elif args.command == 'logs':
        print(server_jobs.logs(args.output, args.lines), end='')
    else:
        print(json.dumps(getattr(server_jobs, args.command)(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
