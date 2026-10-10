"""Supported OpenCode CLI driving the official GLM vision MCP, one job at a time.

The agent invokes a bound MCP job; its summary never replaces the vision reply.
Only the official server sends vision requests. Unknown submissions are not retried.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .pipeline import CodexMCP, _captured_reply, _http_evidence, _outcome_unknown, _read, _usage_for
from .contracts import parse_model_json
from .state import LibraryStopped, json_sha, scope_fingerprint, write_json
from .media import probe_media, sha256_file

PROVIDER = {
    'provider': 'official_vision_mcp_in_opencode',
    'transport': 'opencode_cli_official_mcp_v1',
    'progress_policy': 'finite_stages_no_retry_v1',
    'vision_model': 'glm-5.3-flash',
    'agent_model': 'zhipuai-coding-plan/glm-5.3-flash',
}

OBSERVATION_STAGE = re.compile(r'(?:overview_[a-f0-9]{8}|(?:zoom|fine)_[a-f0-9]{16}|coarse_zoom_search|search_\d+|chain_e2e_v1_fine_(?:observe|inspect_\d+|detail_\d+_\d+))')


def bound_media_scope(request):
    """Bind the actual saved asset to its original source range, without inference."""
    args = request['arguments']
    media = Path(args.get('image_source') or args['video_source']).resolve(strict=True)
    if sha256_file(media) != request['media_sha256']:
        raise LibraryStopped('parent_observation_media_changed')
    scope = request.get('observation_scope')
    lineage_path = media.parent / 'lineage.json'
    if lineage_path.is_file():
        lineage = _read(lineage_path)
        derived = {key: lineage[key] for key in
            ('kind', 'source_sha256', 'source_start_s', 'source_end_s')}
        if (lineage['sha256'] != request['media_sha256'] or
                Path(lineage['path']).resolve() != media or
                any(lineage.get('spec', {}).get(key, value) != value for key, value in derived.items())):
            raise LibraryStopped('parent_observation_lineage_changed')
        if scope is not None and scope_fingerprint(scope) != scope_fingerprint(derived):
            raise LibraryStopped('parent_observation_scope_changed')
        scope = derived
    elif scope is None and request['tool'] == 'analyze_video':
        scope = dict(kind='complete_file', source_sha256=request['media_sha256'], source_start_s=0,
            source_end_s=probe_media(media)['duration_s'])
    scope_fingerprint(scope)
    return scope


def _matching_request(request, scope):
    args = request['arguments']
    argument = 'image_source' if request['tool'] == 'analyze_image' else 'video_source'
    return {**request, 'arguments': {key: value for key, value in args.items() if key != argument},
            'observation_scope': scope}


def _storage_preflight(output, media_bytes, max_tokens):
    # Native base64/journal copies, bounded response copies, and agent/log headroom.
    required = media_bytes * 4 + max_tokens * 32 + 64 * 1024 * 1024
    free = shutil.disk_usage(output).free
    if free < required:
        raise LibraryStopped(f'opencode_storage_preflight_insufficient:required={required}:free={free}')


def _diagnostic_error(reply):
    """Keep a bounded official-MCP error without treating it as model content."""
    reply = reply if isinstance(reply, dict) else {}
    detail = reply.get('error')
    if not isinstance(detail, str) or not detail.strip():
        result = reply.get('result')
        content = result.get('content', []) if isinstance(result, dict) else []
        if not isinstance(content, list):
            content = []
        detail = '\n'.join(row['text'] for row in content if isinstance(row, dict) and
                           row.get('type') == 'text' and isinstance(row.get('text'), str))
    secret = os.environ.get('Z_AI_API_KEY')
    if secret:
        detail = detail.replace(secret, '[REDACTED]')
    return detail[:4000] if detail.strip() else 'opencode_no_original_vision_reply'


def opencode_configuration(package_root, output):
    """No key in config: OpenCode resolves it from the child environment."""
    return {
        '$schema': 'https://opencode.ai/config.json',
        'enabled_providers': ['zhipuai-coding-plan'],
        'model': PROVIDER['agent_model'], 'small_model': PROVIDER['agent_model'],
        'autoupdate': False, 'share': 'disabled',
        'provider': {'zhipuai-coding-plan': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'Zhipu AI Coding Plan',
            'options': {'baseURL': 'https://open.bigmodel.cn/api/coding/paas/v4',
                        'apiKey': '{env:Z_AI_API_KEY}'},
            'models': {'glm-5.3-flash': {'name': 'GLM-5.3-Flash',
                'limit': {'context': 200000, 'output': 8192}}},
        }},
        'permission': {'*': 'deny', 'omni_execute': 'allow'},
        'agent': {'vision-job': {
            'description': 'Execute one immutable official GLM vision job.',
            'mode': 'primary', 'steps': 2,
            'prompt': 'Call omni_execute exactly once with {}. Do not interpret, rewrite, '
                      'or retry the job. Once the tool finishes, respond with DONE.',
            'permission': {'*': 'deny', 'omni_execute': 'allow'},
        }},
        'mcp': {'omni': {'type': 'local', 'enabled': True, 'timeout': 1260000,
            'command': ['node', str(Path(__file__).with_name('opencode_mcp.mjs')),
                        str(Path(output).resolve()), str(Path(package_root).resolve())]}},
    }


class OpenCodeMCP(CodexMCP):
    provider = PROVIDER['provider']

    def __init__(self, state, *, package_root, executable='opencode', exclusions=None, timeout_s=1320):
        if state.input_lock['configuration'] | PROVIDER != state.input_lock['configuration']:
            raise LibraryStopped('opencode_provider_input_lock_mismatch')
        self.state, self.output, self.timeout_s = state, state.output, timeout_s
        self.package_root = Path(package_root).resolve(strict=True)
        if not (self.package_root / 'node_modules/@z_ai/mcp-server/build/index.js').is_file():
            raise LibraryStopped('official_vision_mcp_package_missing')
        self.executable = shutil.which(str(executable))
        if not self.executable:
            raise LibraryStopped('opencode_executable_missing')
        if not os.environ.get('Z_AI_API_KEY'):
            raise LibraryStopped('GLM_key_missing_run_omni_server_configure')
        self.exclusions = list(exclusions or [])
        self.parent_baseline = (state.input_lock['configuration'].get('parent_baseline')
            if state.input_lock['configuration'].get('workflow') == 'reference_rough_skill_v1' else None)
        if self.parent_baseline:
            self.exclusions.extend(row for row in self.parent_baseline.get('unknown_inputs', [])
                if row not in self.exclusions)
        self.queue = self.output / 'mcp_queue'
        self.queue.mkdir(exist_ok=True)
        self.recover_received()

    def call(self, name, prompt, media, validator, *, image=False, scope=None):
        if self.parent_baseline and OBSERVATION_STAGE.fullmatch(name):
            self.state._reload()
            if not any(call['name'] == name for call in self.state.data['calls']):
                media = Path(media).resolve(strict=True)
                if media.stat().st_size >= 8_000_000:
                    raise ValueError('official_mcp_media_limit_8mb')
                request = dict(tool='analyze_image' if image else 'analyze_video',
                    arguments={'image_source' if image else 'video_source': str(media), 'prompt': prompt},
                    media_sha256=sha256_file(media), provider=self.provider,
                    policy_version=self.state.data['policy_version'])
                if scope is not None:
                    request['observation_scope'] = scope
                current_scope = bound_media_scope(request)
                self._exclude(request, current_scope)
                value = self._parent_observation(name, request, current_scope, validator)
                if value is not None:
                    return value
        return super().call(name, prompt, media, validator, image=image, scope=scope)

    def _exclude(self, request, scope):
        for blocked in self.exclusions:
            old = blocked['scope']
            if (request['media_sha256'] == blocked['media_sha256'] or
                    scope.get('source_sha256') == old.get('source_sha256') and
                    scope.get('source_start_s') == old.get('source_start_s') and
                    scope.get('source_end_s') == old.get('source_end_s')):
                raise LibraryStopped('historical_unknown_observation_no_replay:' + blocked['call_id'])

    def _parent_observation(self, name, request, scope, validator):
        for ledger in self.parent_baseline['linked_ledgers']:
            path = Path(ledger['path'])
            if sha256_file(path) != ledger['sha256']:
                raise LibraryStopped('parent_observation_ledger_changed')
            saved = _read(path)
            original = next((call for call in saved['calls'] if call.get('name') == name and
                call['status'] == 'received' and not call.get('repair_of')), None)
            if original is None:
                continue
            folder = path.parent / 'calls' / original['id']
            old_request = _read(folder / 'request.json')
            if json_sha(old_request) != original['request_sha256']:
                raise LibraryStopped('parent_observation_record_changed')
            if (any(old_request.get(key) != request.get(key) for key in
                    ('tool', 'provider', 'policy_version', 'media_sha256')) or
                    old_request['arguments']['prompt'] != request['arguments']['prompt']):
                continue
            old_reply = _read(folder / 'response.json')
            if json_sha(old_reply) != original['response_sha256']:
                raise LibraryStopped('parent_observation_record_changed')
            if _matching_request(old_request, bound_media_scope(old_request)) != _matching_request(request, scope):
                continue
            selected = original
            text = '\n'.join(row['text'] for row in old_reply['result']['content'] if row.get('type') == 'text')
            derived = False
            if not (folder / 'parsed.json').is_file():
                # A forward contract may accept the original complete response.
                # Revalidate its untouched bytes; never edit the parent's parse/failure.
                try:
                    value = parse_model_json(text)
                    validator(value)
                except (ValueError, TypeError, KeyError):
                    pass
                else:
                    derived = True
            if not (folder / 'parsed.json').is_file() and not derived:
                repairs = [call for call in saved['calls'] if call.get('repair_of') == original['id']]
                if len(repairs) != 1 or repairs[0]['status'] != 'received' or repairs[0]['name'] != name + '_repair':
                    continue
                selected = repairs[0]
                folder = path.parent / 'calls' / selected['id']
                repair, old_reply = _read(folder / 'request.json'), _read(folder / 'response.json')
                if json_sha(repair) != selected['request_sha256'] or json_sha(old_reply) != selected['response_sha256']:
                    raise LibraryStopped('parent_observation_record_changed')
                restored = {**repair, 'arguments': {**repair['arguments'], 'prompt': old_request['arguments']['prompt']}}
                if (not repair['arguments']['prompt'].startswith(old_request['arguments']['prompt'] +
                        '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。') or
                        _matching_request(restored, bound_media_scope(repair)) != _matching_request(old_request, scope)):
                    raise LibraryStopped('parent_observation_repair_binding_changed')
            if not (folder / 'parsed.json').is_file() and not derived:
                continue
            text = '\n'.join(row['text'] for row in old_reply['result']['content'] if row.get('type') == 'text')
            value = parse_model_json(text) if derived else _read(folder / 'parsed.json')
            if value != parse_model_json(text):
                raise LibraryStopped('parent_observation_parsed_changed')
            try:
                validator(value)
            except (ValueError, TypeError, KeyError):
                continue
            receipt = dict(source_task_id=saved['task_id'], source_ledger_path=str(path),
                source_ledger_sha256=ledger['sha256'], source_original_call_id=original['id'],
                source_call_id=selected['id'], source_request_sha256=selected['request_sha256'],
                source_response_sha256=selected['response_sha256'], source_parsed_sha256=json_sha(value),
                matching_original_request_sha256=original['request_sha256'],
                current_stage=name, current_request_sha256=json_sha(request), current_scope=scope,
                media_sha256=request['media_sha256'], new_vision_post=False)
            if derived:
                receipt.update(parsed_origin='original_received_response_revalidated', value=value)
            self.state._reload()
            existing = next((entry for entry in self.state.data['artifacts'].get('parent_observation_reuse', [])
                if entry['sha256'] == json_sha(receipt)), None)
            if existing and _read(existing['path']) != receipt:
                raise LibraryStopped('parent_observation_reuse_receipt_changed')
            if not existing:
                self.state.set_artifact('parent_observation_reuse', receipt)
            return value
        return None

    def _submit(self, name, request, *, repair_of=None):
        digest = json_sha(request)
        self.state._reload()
        previous = next((c for c in self.state.data['calls'] if c['request_sha256'] == digest), None)
        if previous:
            if previous['status'] != 'received':
                raise LibraryStopped('recorded_request_not_received_no_replay:' + previous['id'])
            folder = self.output / 'calls' / previous['id']
            saved, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
            if json_sha(saved) != digest or json_sha(reply) != previous['response_sha256']:
                raise LibraryStopped('recorded_model_request_or_reply_modified:' + previous['id'])
            return previous, reply
        clean = self.state.input_lock['configuration'].get('workflow') == 'reference_rough_skill_v1'
        scope = (bound_media_scope(request) if clean and self.exclusions else request.get('observation_scope', {}))
        self._exclude(request, scope)
        if clean:
            _storage_preflight(self.output, Path(next(value for key, value in request['arguments'].items()
                if key in {'image_source', 'video_source'})).stat().st_size,
                self.state.input_lock['configuration'].get('vision_generation', {}).get('max_output_tokens', 16384))
        call, _ = self.state.begin_call(name, request, repair_of=repair_of)
        job = {'job_id': call['id'], 'tool': request['tool'], 'arguments': request['arguments']}
        write_json(self.queue / (call['id'] + '.request.json'), job)
        write_json(self.output / 'mcp_current.json', {'job_id': call['id']})
        folder = self.output / 'calls' / call['id']
        work = folder / 'agent'
        work.mkdir()
        config = opencode_configuration(self.package_root, self.output)
        write_json(work / 'opencode.json', config)
        env = {k: v for k, v in os.environ.items() if not k.startswith('OMNI_LIBRARY_')}
        env.update(OPENCODE_CONFIG_CONTENT=json.dumps(config),
                   OMNI_LIBRARY_OPENCODE_JOB=call['id'], PYTHONUTF8='1')
        log = work / 'events.jsonl'
        try:
            with log.open('wb') as stream:
                process = subprocess.Popen([self.executable, 'run', '--pure', '--format', 'json',
                    '--agent', 'vision-job', '--model', PROVIDER['agent_model'],
                    '--title', call['id'], 'Execute the bound vision job using omni_execute once.'],
                    cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
                try:
                    returncode = process.wait(timeout=self.timeout_s)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                    returncode = -1
        except OSError as error:
            self.state.fail_call(call, 'opencode_launch_failed:' + str(error), uncertain=False)
            raise LibraryStopped('opencode_launch_failed') from error
        finally:
            if log.exists():
                log.write_bytes(log.read_bytes().replace(os.environ['Z_AI_API_KEY'].encode(), b'[REDACTED]'))
        entries = _http_evidence(self.output, call['id'])
        _, captured = _captured_reply(entries)
        reply_path = self.queue / (call['id'] + '.response.json')
        reply = captured or (_read(reply_path) if reply_path.exists() else None)
        write_json(work / 'exit.json', {'returncode': returncode,
            'vision_usage_scope': 'mcp_http.jsonl', 'agent_usage_scope': 'events.jsonl'})
        result = reply.get('result') if isinstance(reply, dict) else None
        if reply and reply.get('status') == 'complete' and isinstance(result, dict) and not result.get('isError'):
            self.state.complete_call(call, reply, usage=_usage_for(self.output, call['id']))
            return call, reply
        unknown = _outcome_unknown(entries)
        self.state.fail_call(call, _diagnostic_error(reply), uncertain=unknown)
        raise LibraryStopped('opencode_vision_job_failed_no_retry:' + call['id'])
