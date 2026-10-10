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


def parent_baseline(parent, output):
    """Link an explicitly requested new evaluation without resetting old usage."""
    parent, output = Path(parent).resolve(strict=True), Path(output).resolve()
    if not output.is_relative_to(parent / 'evaluations'):
        raise LibraryStopped('new_evaluation_must_remain_inside_parent_evaluations')
    path = parent / 'library_state.json'
    state = json.loads(path.read_text(encoding='utf-8'))
    files = [path, *sorted((parent / 'artifacts').glob('*/library_state.json'))]
    ledgers = []
    inherited = state['input_lock']['configuration'].get('parent_baseline', {}).get('linked_ledgers', [])
    for row in inherited:
        item = Path(row['path']).resolve(strict=True)
        if sha256_file(item) != row['sha256']:
            raise LibraryStopped('parent_ancestor_ledger_changed')
        if item not in files:
            files.append(item)
    counted = {path, *sorted((parent / 'artifacts').glob('*/library_state.json'))}
    unknown = []
    for item in files:
        saved = json.loads(item.read_text(encoding='utf-8'))
        if type(saved['request_count']) is not int or saved['request_count'] != len(saved['calls']):
            raise LibraryStopped('parent_call_count_changed')
        if any(row['status'] == 'submitted' for row in saved['calls']):
            raise LibraryStopped('parent_request_still_submitted')
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
    return dict(path=str(path), sha256=sha256_file(path), task_id=state['task_id'],
                linked_ledgers=ledgers, request_count=sum(row['request_count'] for row in ledgers
                    if Path(row['path']) in counted), unknown_inputs=unknown,
                prior_requests=state['input_lock']['configuration'].get('prior_requests', 0))


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
    reference = args.reference.resolve(strict=True)
    seed = (history['reference_seed'] if history and
            sha256_file(reference) == history['reference_seed']['reference_sha256'] else None)
    if args.reference_cache:
        seed = reference_cache(args.reference_cache, reference)
    provider_config = dict(PROVIDER)
    provider_config['workflow'] = 'reference_rough_skill_v1'
    generation = _vision_generation(args.output)
    if generation is not None:
        provider_config['vision_generation'] = generation
    if history:
        provider_config.update(history_sha256=config['history_sha256'],
                               prior_requests=history['baseline_requests'])
    baseline = parent_baseline(args.parent_task, args.output) if args.parent_task else None
    if baseline:
        provider_config.update(parent_baseline=baseline,
            prior_requests=max(provider_config.get('prior_requests', 0), baseline['prior_requests']) + baseline['request_count'])
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
            registry_path=Path(args.home) / 'shared/server_library_runs.json')
        if baseline and any(sha256_file(row['path']) != row['sha256'] for row in baseline['linked_ledgers']):
            raise LibraryStopped('parent_ledger_changed_during_evaluation')
        cumulative = provider_config.get('prior_requests', 0) + result.get('usage', {}).get('requests', 0)
        print(json.dumps({'result_path': str(args.output.resolve() / 'chain_result.json'),
                          'lineage_cumulative_vision_requests': cumulative,
                          'agent_usage_recorded_separately': True}, ensure_ascii=False), flush=True)
    finally:
        os.environ.pop('Z_AI_API_KEY', None)


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
        p.add_argument('--library', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--asr', action='store_true', help='Optional CPU ASR; downloads weights on first use.')
        p.add_argument('--asr-model-dir', type=Path, help='Use previously verified local ASR weights.')
        p.add_argument('--reference-cache', type=Path, help='Reuse a hash-bound received original reference call.')
        p.add_argument('--parent-task', type=Path, help='Bind a new evaluation under this existing task/evaluations.')
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
        if reference.name.endswith('.part') or any(p.name.endswith('.part') for p in library.iterdir()):
            parser.error('media upload is incomplete; wait for SHA verification and .part rename')
        command = [sys.executable, '-m', 'omni_story.library.server_cli', '--home', str(args.home.resolve()), '_run',
                   '--reference', str(reference), '--library', str(library), '--output', str(args.output.resolve())]
        if args.asr:
            command.append('--asr')
        for option in ('asr_model_dir', 'reference_cache', 'parent_task'):
            value = getattr(args, option)
            if value is not None:
                command.extend(['--' + option.replace('_', '-'), str(value.resolve(strict=True))])
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
