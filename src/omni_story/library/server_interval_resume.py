"""Continue one received interval failure in its original task and ledger."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

from . import contracts, semantic_audit, semantic_prompts, server_jobs
from .media import sha256_file
from .opencode_provider import OpenCodeMCP
from .pipeline import _http_evidence, execute
from .server_cli import credential, load_history, settings
from .state import LibraryState, LibraryStopped, json_sha

KEY = 'server_interval_resume'
POLICY = 'opencode_known_interval_resume_v1'
ALIAS = 'semantic_interval_resume_v1'
CONTROLLER = 'controllers/interval_resume_v1'
USER_INSTRUCTION = '那接着后续剪辑'
FAILURE = 'failure_interval_resume_v1.json'
MODULE = 'omni_story.library.server_interval_resume'
MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
SCHEMA_KEY = 'server_schema_resume'
SCHEMA_POLICY = 'opencode_known_schema_resume_v1'
SCHEMA_ALIAS = 'semantic_schema_resume_v1'
SCHEMA_CONTROLLER = 'controllers/schema_resume_v1'
SCHEMA_FAILURE = 'failure_schema_resume_v1.json'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _require(ok, reason):
    if not ok:
        raise LibraryStopped('server_interval_resume_' + reason)


def _text(reply):
    return '\n'.join(row['text'] for row in reply['result']['content'] if row.get('type') == 'text')


def _record(path):
    return {'path': str(Path(path).resolve(strict=True)), 'sha256': sha256_file(path)}


def _stopped(output, controller=None):
    directory = Path(output) / controller / '.omni-server' if controller else Path(output) / '.omni-server'
    job = _read(directory / 'job.json')
    _require(job['state'] == 'failed' and job['exit_code'] == 1 and
             not server_jobs._identity(job.get('supervisor_pid', 0)) and
             not server_jobs._group_running(job.get('child_pid', 0)), 'original_controller_not_stopped')
    _require((directory / 'run.lock').read_text().strip() == job['job_id'], 'original_run_lock_changed')
    return directory


def _failed_pair(output, data, *, schema=False):
    original, repair = data['calls'][-2:]
    stage = original['name']
    _require(stage.startswith('semantic_slice_') and not original.get('repair_of') and
             repair['name'] == stage + '_repair' and repair['repair_of'] == original['id'], 'wrong_failed_pair')
    request = _read(output / 'calls' / original['id'] / 'request.json')
    proxy = _read(Path(request['arguments']['video_source']).parent / 'lineage.json')
    _require(sha256_file(proxy['path']) == request['media_sha256'] == proxy['sha256'], 'failed_proxy_changed')
    segment = next(s for s in _read(output / 'plan_0.json')['segments']
                   if s['segment_id'] == contracts.parse_model_json(_text(
                       _read(output / 'calls' / original['id'] / 'response.json')))['segment_id'])
    diagnostics = []
    for attempt, call in enumerate((original, repair)):
        folder = output / 'calls' / call['id']
        saved, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
        _require(json_sha(saved) == call['request_sha256'] and json_sha(reply) == call['response_sha256'] and
                 reply['status'] == 'complete' and reply['finish_reason'] == 'stop' and
                 saved['media_sha256'] == proxy['sha256'] and
                 saved.get('observation_scope') == request.get('observation_scope') and
                 not (folder / 'parsed.json').exists(), 'failed_reply_changed')
        failure = _read(folder / 'protocol_failure.json')
        expected_error = ('semantic/uncertainties:list_required' if attempt else
                          'semantic:direct_fact_cannot_depend_on_inference') if schema else \
                         'semantic/evidence:outside_observed_slice'
        _require(failure['error'] == expected_error and
                 failure['attempt'] == attempt and failure['model_text'] == _text(reply), 'old_failure_changed')
        events = _http_evidence(output, call['id'])
        posts = [e for e in events if e.get('type') == 'request']
        replies = [e for e in events if e.get('type') == 'response']
        _require(len(posts) == len(replies) == 1 and replies[0]['status'] == 200 and
                 posts[0]['seq'] == replies[0]['seq'] and
                 json.loads(replies[0]['body'])['choices'][0]['message']['content'] == _text(reply),
                 'HTTP_outcome_not_known')
        try:
            validator = semantic_audit.validate_segment_observation_with_diagnostics if schema else \
                        semantic_audit.validate_segment_observation
            validator(contracts.parse_model_json(_text(reply)), segment, proxy['source_sha256'], proxy)
        except ValueError as error:
            if schema:
                _require(str(error) == expected_error and 'uncertainties' not in
                         contracts.parse_model_json(_text(reply)), 'not_known_missing_field_failure')
            else:
                _require(str(error) == expected_error and
                         all(row['problem'] == 'zero_duration' for row in error.diagnostics['invalid_intervals']),
                         'not_known_zero_duration_failure')
            diagnostics.append(error.diagnostics)
        else:
            _require(False, 'reply_did_not_fail')
    corrected = deepcopy(request)
    suffix = ('\n这是同一任务已明确授权的一次字段纠正。重新观察当前视频，完整填写所有字段；'
              '不确定性由实际画面独立判断，不得因缺字段就假设没有不确定性。\n' +
              semantic_prompts.explicit_slice_observation_prompt(segment,
                  {'source_id': segment['source_id'], 'sha256': proxy['source_sha256']}, proxy)) if schema else \
             ('\n这是同一任务已明确授权的一次区间纠正。重新依据当前视频确认区间，'
              '不能给点时间随意补时长；无法确认的动作如实记入uncertainties。')
    corrected['arguments']['prompt'] += suffix + MARKER + json.dumps({
            'validation_error': 'semantic/uncertainties:list_required' if schema else
                                'semantic/evidence:outside_observed_slice',
            'previous_response': _text(_read(output / 'calls' / repair['id'] / 'response.json')),
            'validation_diagnostics': diagnostics[-1]}, ensure_ascii=False)
    return stage, corrected


def load(output, *, schema=False):
    output = Path(output).resolve(strict=True)
    data = _read(output / 'library_state.json')
    key, policy, alias, count, instruction = (SCHEMA_KEY, SCHEMA_POLICY, SCHEMA_ALIAS, 39, '继续') if schema else \
                                           (KEY, POLICY, ALIAS, 29, USER_INSTRUCTION)
    parent = load(output) if schema else None
    entries = data['artifacts'].get(key, [])
    _require(len(entries) == 1, 'exactly_one_authorization_required')
    entry = entries[0]
    proof = _read(entry['path'])
    _require(json_sha(proof) == entry['sha256'] and proof['policy'] == policy and
             proof['output'] == str(output) and proof['task_id'] == data['task_id'] and
             proof['input_lock'] == data['input_lock'] and proof['alias'] == alias and
             proof['user_instruction'] == instruction and proof['new_rounds'] == 0 and
             proof['max_rounds'] == proof['max_renders'] == 2 and proof['max_fine'] == 16 and
             proof['no_unknown_replay'] is True and proof['alias_original_and_one_repair_only'] is True,
             'authorization_changed')
    if schema:
        _require(proof.get('parent_authorization_sha256') == json_sha(parent) and
                 proof['stage'] == 'semantic_slice_0_f8a4f183a2d3df76', 'parent_authorization_changed')
    _require(data['request_count'] == len(data['calls']), 'call_count_changed')
    baseline = _read(proof['baseline_state_path'])
    _require(sha256_file(proof['baseline_state_path']) == proof['baseline_state_sha256'] and
             baseline['request_count'] == len(baseline['calls']) == proof['baseline_request_count'] == count and
             baseline['calls'] == data['calls'][:count] and baseline['max_requests'] is data['max_requests'] is None and
             baseline['input_lock'] == data['input_lock'] and baseline['task_id'] == data['task_id'], 'prefix_changed')
    _require(all(call['status'] == 'received' for call in baseline['calls']), 'unknown_history')
    _require(all(entries == data['artifacts'].get(key, [])[:len(entries)]
                 for key, entries in baseline['artifacts'].items()), 'old_artifacts_changed')
    for item in proof['protected_files'] + proof['runtime_files']:
        _require(sha256_file(item['path']) == item['sha256'], 'protected_file_changed')
    with (output / 'mcp_http.jsonl').open('rb') as handle:
        from hashlib import sha256
        prefix = handle.read(proof['http_prefix_bytes'])
    _require(len(prefix) == proof['http_prefix_bytes'] and
             sha256(prefix).hexdigest() == proof['http_prefix_sha256'], 'HTTP_prefix_changed')
    aliases = [c for c in data['calls'][count:] if c['name'].startswith(alias)]
    _require(len(aliases) <= 2 and len({c['name'] for c in aliases}) == len(aliases) and
             all(c['name'] in {alias, alias + '_repair'} for c in aliases), 'correction_limit_changed')
    return proof


def register(output, registry, *, schema=False):
    output = Path(output).resolve(strict=True)
    data = _read(output / 'library_state.json')
    key, policy, alias, count, instruction, folder_name = (SCHEMA_KEY, SCHEMA_POLICY, SCHEMA_ALIAS, 39,
        '继续', 'schema_resume_v1') if schema else (KEY, POLICY, ALIAS, 29, USER_INSTRUCTION, 'interval_resume_v1')
    if data['artifacts'].get(key):
        return load(output, schema=schema)
    parent = load(output) if schema else None
    control = _stopped(output, CONTROLLER if schema else None)
    config = data['input_lock']['configuration']
    _require(data['request_count'] == len(data['calls']) == count and data['max_requests'] is None and
             all(c['status'] == 'received' for c in data['calls']) and
             config['max_rounds'] == config['max_renders'] == 2 and config['max_fine'] == 16,
             'original_scope_changed')
    _require(not (output / 'result.json').exists() and not any(output.glob('render_[0-9]*')), 'already_rendered')
    stage, request = _failed_pair(output, data, schema=schema)
    if schema:
        _require(stage == 'semantic_slice_0_f8a4f183a2d3df76', 'unexpected_schema_stage')
    state = LibraryState(output, data['input_lock'], max_requests=None, registry_path=registry)
    folder = output / 'artifacts' / folder_name
    folder.mkdir(parents=True, exist_ok=False)
    baseline = folder / 'baseline_state.json'
    baseline.write_bytes((output / 'library_state.json').read_bytes())
    paths = [p for p in (output / 'calls').rglob('*') if p.is_file()]
    paths += [p for p in control.iterdir() if p.is_file()]
    paths += [output / name for name in ('failure.json', 'plan_0.json', 'draft_plan_0.json', 'finecut_0.json')]
    if schema:
        paths.append(output / FAILURE)
    paths += [Path(e['path']) for entries in data['artifacts'].values() for e in entries]
    http = (output / 'mcp_http.jsonl').read_bytes()
    from hashlib import sha256
    package = Path(__file__).parent
    proof = {'policy': policy, 'output': str(output), 'task_id': data['task_id'], 'input_lock': data['input_lock'],
             'baseline_request_count': count, 'baseline_state_path': str(baseline),
             'baseline_state_sha256': sha256_file(baseline), 'stage': stage, 'alias': alias,
             'expected_request': request, 'protected_files': [_record(p) for p in sorted(set(paths))],
             'runtime_files': [_record(package / name) for name in (
                 'server_interval_resume.py', 'server_interval_resume.mjs', 'mcp_opencode_guard.mjs',
                 'semantic_audit.py', 'semantic_prompts.py', 'pipeline.py', 'opencode_provider.py')],
             'http_prefix_bytes': len(http), 'http_prefix_sha256': sha256(http).hexdigest(),
             'user_instruction': instruction, 'new_rounds': 0, 'max_rounds': 2, 'max_renders': 2,
             'max_fine': 16, 'no_unknown_replay': True, 'alias_original_and_one_repair_only': True}
    if schema:
        proof['parent_authorization_sha256'] = json_sha(parent)
    state.set_artifact(key, proof)
    return load(output, schema=schema)


class IntervalResumeMCP(OpenCodeMCP):
    def call(self, name, prompt, media, validator, **options):
        proof = next((p for p in getattr(self, 'resume_proofs', [self.resume_proof]) if name == p['stage']), None)
        if proof:
            request = proof['expected_request']
            _require(sha256_file(media) == request['media_sha256'] and
                     options.get('scope') == request.get('observation_scope'), 'correction_input_changed')
            name, prompt = proof['alias'], request['arguments']['prompt']
        return super().call(name, prompt, media, validator, **options)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start', '_run', 'status', 'stop'))
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--schema-correction', action='store_true')
    args = parser.parse_args(argv)
    output = args.output.resolve(strict=True)
    controller = output / (SCHEMA_CONTROLLER if args.schema_correction else CONTROLLER)
    config = settings(args.home)
    if args.command in {'status', 'stop'}:
        print(json.dumps(getattr(server_jobs, args.command)(controller)))
        return
    if args.command == 'start':
        load_history(config)
        credential(args.home)
        register(output, args.home / 'shared/server_library_runs.json', **({'schema': True} if args.schema_correction else {}))
        command = [sys.executable, '-m', MODULE, '_run', '--home', str(args.home), '--output', str(output)]
        if args.schema_correction:
            command.append('--schema-correction')
        print(json.dumps(server_jobs.start(command, controller, Path(config['project_root'])), ensure_ascii=False))
        return
    proof = load(output, schema=args.schema_correction)
    credential_value = credential(args.home)
    history = load_history(config)
    os.environ['Z_AI_API_KEY'] = credential_value
    input_config = proof['input_lock']['configuration']
    inventory = _read(output / 'catalog/inventory.json')
    reference = _read(output / 'reference_catalog/inventory.json')['sources'][0]['path']
    library = str(Path(inventory['sources'][0]['path']).parent)
    def factory(state):
        client = IntervalResumeMCP(state, package_root=config['mcp_package_root'],
            executable=config['opencode_executable'], exclusions=(history or {}).get('unknown_inputs'))
        client.resume_proof = proof
        if args.schema_correction:
            client.resume_proofs = [load(output), proof]
        return client
    try:
        execute(reference, library, output, asr=input_config['asr'], max_requests=None,
                model_factory=factory, provider_config=input_config, active_finecut=True,
                registry_path=args.home / 'shared/server_library_runs.json',
                failure_report_name=SCHEMA_FAILURE if args.schema_correction else FAILURE,
                **({'slice_observation_prompt': semantic_prompts.explicit_slice_observation_prompt,
                    'slice_observation_validator': semantic_audit.validate_segment_observation_with_diagnostics}
                   if args.schema_correction else {}))
        load(output, schema=args.schema_correction)
    finally:
        os.environ.pop('Z_AI_API_KEY', None)


if __name__ == '__main__':
    main()
