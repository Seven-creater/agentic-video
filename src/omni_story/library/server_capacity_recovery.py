"""One registered recovery of a known empty, output-limited OpenCode plan.

This is an engineering correction in the original task, not a format retry,
unknown replay, budget reset, or grant of another editing round.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .media import sha256_file
from .opencode_provider import OpenCodeMCP, PROVIDER
from .pipeline import _http_evidence
from .server_cli import VISION_GENERATION, credential, load_history, settings
from .state import LibraryState, LibraryStopped, json_sha

POLICY = 'opencode_known_output_starvation_recovery_v1'
KEY = 'server_output_capacity_recovery'
NAME = 'output_capacity_v1'
STAGE = 'plan_0'
ALIAS = 'plan_0_capacity_v2'
MODULE = 'omni_story.library.server_capacity_recovery'
SUFFIX = ('\n本次是已记录的生成容量修正：前两次请求均返回已知的空内容及 length，'
          '现在官方生成上限为32768。请简洁思考，优先完整输出要求的JSON；保留全部必需字段、'
          '来源绑定和画面证据，勿重复复述上下文。不得编造素材或修改创作目标。')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _require(condition, reason):
    if not condition:
        raise LibraryStopped('server_capacity_recovery_' + reason)


def _bound_file(output, relative):
    path = (output / relative).resolve(strict=True)
    _require(path.is_relative_to(output), 'file_outside_task')
    return {'path': str(path), 'sha256': sha256_file(path)}


def load(output):
    """Verify the immutable authorization and the entire old call prefix."""
    output = Path(output).resolve(strict=True)
    state = _read(output / 'library_state.json')
    entries = state.get('artifacts', {}).get(KEY, [])
    if not entries:
        return None
    _require(len(entries) == 1, 'duplicate_authorization')
    entry = entries[0]
    auth = _read(entry['path'])
    _require(json_sha(auth) == entry['sha256'], 'authorization_changed')
    _require(Path(entry['path']).resolve().is_relative_to(output), 'authorization_outside_task')
    _require(auth['policy'] == POLICY and auth['stage'] == STAGE and auth['alias'] == ALIAS and
             auth['recovery_name'] == NAME and auth['output'] == str(output) and
             auth['generation'] == VISION_GENERATION and auth['input_lock'] == state['input_lock'] and
             auth['task_id'] == state['task_id'] and state['max_requests'] is None,
             'scope_changed')
    _require(sha256_file(auth['baseline_state_path']) == auth['baseline_state_sha256'], 'baseline_changed')
    old = _read(auth['baseline_state_path'])
    count = auth['baseline_request_count']
    _require(type(count) is int and count == len(old['calls']) == old['request_count'] and
             old['task_id'] == state['task_id'] and old['input_lock'] == state['input_lock'] and
             old['max_requests'] is None and old['calls'] == state['calls'][:count] and
             all(c['status'] == 'received' for c in old['calls']), 'history_changed')
    _require(state['request_count'] == len(state['calls']), 'count_changed')
    _require(all(state.get('artifacts', {}).get(key, [])[:len(entries)] == entries
                 for key, entries in old.get('artifacts', {}).items()), 'old_artifacts_changed')
    _require(auth['prompt_suffix'] == SUFFIX and auth['new_rounds'] == 0 and
             auth['candidate_limit_unchanged'] == 2 and auth['fine_window_limit_unchanged'] == 16 and
             auth['no_unknown_replay'] is True and auth['alias_original_and_one_repair_only'] is True,
             'recovery_limits_changed')
    with (output / 'mcp_http.jsonl').open('rb') as journal:
        prefix = journal.read(auth['http_prefix_bytes'])
    _require(hashlib.sha256(prefix).hexdigest() == auth['http_prefix_sha256'], 'old_HTTP_journal_changed')
    for item in auth['protected_files']:
        _require(Path(item['path']).resolve().is_relative_to(output) and
                 sha256_file(item['path']) == item['sha256'], 'original_file_changed')
    _require(sha256_file(auth['user_authorization_path']) == auth['user_authorization_sha256'],
             'user_authorization_changed')
    original = old['calls'][-2:]
    _require(len(original) == 2 and [c['name'] for c in original] == [STAGE, STAGE + '_repair'] and
             original[0]['repair_of'] is None and original[1]['repair_of'] == original[0]['id'],
             'original_pair_changed')
    for call in original:
        request = _read(output / 'calls' / call['id'] / 'request.json')
        reply = _read(output / 'calls' / call['id'] / 'response.json')
        _require(json_sha(request) == call['request_sha256'] and json_sha(reply) == call['response_sha256'] and
                 reply.get('finish_reason') == 'length' and
                 not ''.join(c.get('text', '') for c in reply['result']['content']), 'not_known_empty_length')
    # The unchanged official package is given a larger native output capacity.
    # An authorized original and its sole repair are the only new alias names.
    aliases = [c for c in state['calls'][count:] if c['name'].startswith(ALIAS)]
    _require(len(aliases) <= 2 and len({c['name'] for c in aliases}) == len(aliases) and
             all(c['name'] in {ALIAS, ALIAS + '_repair'} for c in aliases), 'alias_limit_changed')
    return auth


def register(output, user_authorization_path, user_authorization_sha256, *, registry_path=None):
    """Register once, only after two actual HTTP200 empty length replies."""
    from . import server_jobs
    output = Path(output).resolve(strict=True)
    _require(load(output) is None, 'already_registered')
    data = _read(output / 'library_state.json')
    config = data['input_lock']['configuration']
    _require(config | PROVIDER == config and 'vision_generation' not in config and
             config['max_rounds'] == config['max_renders'] == 2 and config['max_fine'] == 16 and
             data['max_requests'] is None and data['request_count'] == len(data['calls']) and
             all(c['status'] == 'received' for c in data['calls']), 'unsettled_or_wrong_task')
    job_path = output / '.omni-server/job.json'
    job = server_jobs.status(output)
    _require(job['state'] == 'failed' and job.get('exit_code') == 1 and
             not server_jobs._identity(job.get('supervisor_pid', 0)) and
             not server_jobs._group_running(job.get('child_pid', 0)), 'original_job_not_stopped')
    _require(_read(output / 'failure.json')['error'] == 'model_protocol_repair_exhausted:' + STAGE and
             not (output / 'result.json').exists() and not list(output.glob('render_[01]')),
             'wrong_failure_or_render_exists')
    _require(len(data['calls']) >= 2 and [c['name'] for c in data['calls'][-2:]] ==
             [STAGE, STAGE + '_repair'] and data['calls'][-2]['repair_of'] is None and
             data['calls'][-1]['repair_of'] == data['calls'][-2]['id'] and
             sum(c['name'] == STAGE for c in data['calls']) == 1 and
             sum(c['name'] == STAGE + '_repair' for c in data['calls']) == 1,
             'wrong_failed_stage')
    proofs = []
    for call in data['calls'][-2:]:
        reply = _read(output / 'calls' / call['id'] / 'response.json')
        _require(reply.get('finish_reason') == 'length' and
                 not ''.join(c.get('text', '') for c in reply['result']['content']), 'not_known_empty_length')
        evidence = _http_evidence(output, call['id'])
        posts = [e for e in evidence if e.get('type') == 'request']
        responses = [e for e in evidence if e.get('type') == 'response']
        _require(len(posts) == len(responses) == 1 and responses[0]['status'] == 200 and
                 posts[0]['seq'] == responses[0]['seq'], 'HTTP_pair_not_known')
        body = json.loads(responses[0]['body'])
        choice = body['choices'][0]
        _require(choice['finish_reason'] == 'length' and not choice['message'].get('content') and
                 body['usage']['completion_tokens'] == 16384, 'not_output_starvation')
        proofs.append({'call_id': call['id'], 'seq': posts[0]['seq'], 'status': 200,
                       'response_body_sha256': hashlib.sha256(responses[0]['body'].encode()).hexdigest(),
                       'usage': body['usage']})
    user_path = Path(user_authorization_path).resolve(strict=True)
    _require(sha256_file(user_path) == user_authorization_sha256, 'user_authorization_changed')
    user = _read(user_path)
    _require(user['policy'] == 'server_edit_test_autonomous_engineering_fixes_v1' and
             user['task_output'] == str(output) and user['no_unknown_replay'] is True and
             user['no_automatic_new_round'] is True and user['candidate_limit_unchanged'] == 2,
             'user_authorization_scope_changed')
    protected = [_bound_file(output, relative) for relative in
                 ['failure.json', '.omni-server/job.json', '.omni-server/run.lock',
                  'catalog/inventory.json', 'reference_catalog/inventory.json']]
    for call in data['calls']:
        for filename in ('request.json', 'response.json', 'parsed.json', 'protocol_failure.json'):
            relative = Path('calls') / call['id'] / filename
            if (output / relative).exists():
                protected.append(_bound_file(output, relative))
        request = _read(output / 'calls' / call['id'] / 'request.json')
        _require(json_sha(request) == call['request_sha256'] and
                 json_sha(_read(output / 'calls' / call['id'] / 'response.json')) == call['response_sha256'],
                 'original_call_changed')
    state = LibraryState(output, data['input_lock'], max_requests=None, registry_path=registry_path)
    baseline = output / 'artifacts/server_output_capacity_baseline_v1.json'
    _require(not baseline.exists(), 'baseline_already_exists')
    baseline.write_bytes((output / 'library_state.json').read_bytes())
    baseline.chmod(0o600)
    http_prefix = (output / 'mcp_http.jsonl').read_bytes()
    auth = {'policy': POLICY, 'task_id': data['task_id'], 'input_lock': data['input_lock'],
            'output': str(output), 'baseline_state_path': str(baseline),
            'baseline_state_sha256': sha256_file(baseline), 'baseline_request_count': data['request_count'],
            'stage': STAGE, 'alias': ALIAS, 'prompt_suffix': SUFFIX, 'generation': VISION_GENERATION,
            'original_job_id': job['job_id'], 'original_job_sha256': sha256_file(job_path),
            'recovery_name': NAME, 'user_authorization_path': str(user_path),
            'user_authorization_sha256': user_authorization_sha256, 'protected_files': protected,
            'HTTP_known_output_starvation': proofs, 'new_rounds': 0,
            'http_prefix_bytes': len(http_prefix), 'http_prefix_sha256': hashlib.sha256(http_prefix).hexdigest(),
            # Initial-state audit value; ordinary second-round observations may
            # extend watched_windows. Old parsed call files remain protected.
            'completed_windows_sha256': sha256_file(output / 'watched_windows.json'),
            'candidate_limit_unchanged': 2, 'fine_window_limit_unchanged': 16,
            'no_unknown_replay': True, 'alias_original_and_one_repair_only': True}
    path = state.set_artifact(KEY, auth)
    load(output)
    return path


class CapacityMCP(OpenCodeMCP):
    """Route the proven known failed plan through its single registered alias."""
    def call(self, name, prompt, media, validator, *, image=False, scope=None):
        auth = load(self.output)
        _require(auth is not None, 'authorization_missing')
        if name == auth['stage']:
            old = _read(auth['baseline_state_path'])['calls'][-2]
            original = _read(self.output / 'calls' / old['id'] / 'request.json')
            argument = 'image_source' if image else 'video_source'
            _require(original['tool'] == ('analyze_image' if image else 'analyze_video') and
                     Path(original['arguments'][argument]) == Path(media).resolve() and
                     original['media_sha256'] == sha256_file(media), 'planning_media_changed')
            return super().call(auth['alias'], original['arguments']['prompt'] + auth['prompt_suffix'],
                                media, validator, image=image, scope=original.get('observation_scope'))
        return super().call(name, prompt, media, validator, image=image, scope=scope)


def run(home, output):
    auth = load(output)
    _require(auth is not None, 'authorization_missing')
    config = settings(home)
    history = load_history(config)
    original_config = auth['input_lock']['configuration']
    _require(config['history_sha256'] == original_config['history_sha256'], 'history_manifest_changed')
    command = _read(Path(output) / '.omni-server/job.json')['command']
    reference = Path(command[command.index('--reference') + 1])
    library = Path(command[command.index('--library') + 1])
    seed = history['reference_seed']
    _require(json_sha(seed) == original_config['reference_seed_sha256'], 'reference_seed_changed')
    os.environ['Z_AI_API_KEY'] = credential(home)
    try:
        from .pipeline import execute
        return execute(reference, library, output, span_s=original_config['span_s'],
            frames=original_config['frames'], max_fine=original_config['max_fine'],
            asr=original_config['asr'], max_requests=None, active_finecut=True,
            model_factory=lambda state: CapacityMCP(state, package_root=config['mcp_package_root'],
                executable=config['opencode_executable'], exclusions=history['unknown_inputs']),
            provider_config=original_config, reference_seed=seed,
            registry_path=Path(home) / 'shared/server_library_runs.json',
            failure_report_name='failure_capacity_recovery_v1.json')
    finally:
        os.environ.pop('Z_AI_API_KEY', None)


def register_catalog_preflight_fix(output, *, registry_path):
    """Carry the unused registered remedy past one proved CPU-only failure."""
    from . import server_jobs
    from .pipeline import _catalog
    output = Path(output).resolve(strict=True)
    auth = load(output)
    _require(auth is not None, 'authorization_missing')
    state = _read(output / 'library_state.json')
    key = 'server_output_capacity_preflight_fix'
    _require(not state['artifacts'].get(key), 'preflight_fix_already_registered')
    _require(state['request_count'] == auth['baseline_request_count'] and
             all(c['status'] == 'received' for c in state['calls']), 'preflight_fix_cannot_repeat_paid_work')
    directory = output / '.omni-server/recoveries' / NAME
    job_path, log_path = directory / 'job.json', directory / 'job.log'
    job = server_jobs.status(output, recovery_name=NAME)
    _require(job['state'] == 'failed' and job.get('exit_code') == 1 and
             not server_jobs._identity(job.get('supervisor_pid', 0)) and
             not server_jobs._group_running(job.get('child_pid', 0)), 'preflight_job_not_stopped')
    _require('LibraryStopped: library_file_set_changed' in log_path.read_text(encoding='utf-8') and
             not (output / 'failure_capacity_recovery_v1.json').exists(), 'not_catalog_preflight_failure')
    # Confirm the corrected inventory comparison and unchanged source stat before
    # appending a launch correction. This never calls a model or rewrites catalogs.
    original = _read(output / '.omni-server/job.json')['command']
    library = Path(original[original.index('--library') + 1])
    _catalog(library, output / 'catalog')
    _catalog(Path(original[original.index('--reference') + 1]), output / 'reference_catalog')
    entry = state['artifacts'][KEY][0]
    proof = {'policy': server_jobs.PREFLIGHT_POLICY,
             'recovery_name': server_jobs.PREFLIGHT_RECOVERY_NAME,
             'task_id': state['task_id'], 'input_lock': state['input_lock'],
             'baseline_request_count': state['request_count'],
             'first_recovery_job_path': str(job_path), 'first_recovery_job_sha256': sha256_file(job_path),
             'first_recovery_job_id': job['job_id'], 'first_recovery_log_path': str(log_path),
             'first_recovery_log_sha256': sha256_file(log_path),
             'original_capacity_authorization_sha256': sha256_file(entry['path']),
             'new_model_requests_authorized': 0,
             'reason': 'Correct inventory/resume extension mismatch before any new paid call; '
                       'only the unchanged, unused capacity alias authorization carries forward.'}
    record = LibraryState(output, state['input_lock'], max_requests=None,
        registry_path=registry_path)
    return record.set_artifact(key, proof)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start')
    start.add_argument('--user-authorization', type=Path, required=True)
    start.add_argument('--user-authorization-sha256', required=True)
    commands.add_parser('resume-catalog', help='Continue the unused remedy after its bound CPU catalog failure.')
    commands.add_parser('_run')
    for name in ('status', 'logs', 'stop'):
        commands.add_parser(name)
    args = parser.parse_args(argv)
    from . import server_jobs
    if args.command in {'start', 'resume-catalog'}:
        if args.command == 'start':
            path = register(args.output, args.user_authorization, args.user_authorization_sha256,
                            registry_path=args.home / 'shared/server_library_runs.json')
            recovery_name = NAME
        else:
            register_catalog_preflight_fix(args.output, registry_path=args.home / 'shared/server_library_runs.json')
            path = Path(_read(args.output / 'library_state.json')['artifacts'][KEY][0]['path'])
            recovery_name = server_jobs.PREFLIGHT_RECOVERY_NAME
        command = [sys.executable, '-m', MODULE, '--home', str(args.home.resolve()),
                   '--output', str(args.output.resolve()), '_run']
        result = server_jobs.start_recovery(command, args.output,
            Path(settings(args.home)['project_root']), authorization_path=path,
            authorization_sha256=sha256_file(path), recovery_name=recovery_name)
    elif args.command == '_run':
        result = run(args.home, args.output)
    elif args.command == 'logs':
        name = server_jobs.PREFLIGHT_RECOVERY_NAME if _read(args.output / 'library_state.json')['artifacts'].get(
            'server_output_capacity_preflight_fix') else NAME
        print(server_jobs.logs(args.output, recovery_name=name), end='')
        return 0
    else:
        name = server_jobs.PREFLIGHT_RECOVERY_NAME if _read(args.output / 'library_state.json')['artifacts'].get(
            'server_output_capacity_preflight_fix') else NAME
        result = getattr(server_jobs, args.command)(args.output, recovery_name=name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
