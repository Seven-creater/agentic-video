"""Supported OpenCode CLI driving the official GLM vision MCP, one job at a time.

The agent invokes a bound MCP job; its summary never replaces the vision reply.
Only the official server sends vision requests. Unknown submissions are not retried.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from .pipeline import CodexMCP, _captured_reply, _http_evidence, _outcome_unknown, _read, _usage_for
from .state import LibraryStopped, json_sha, write_json

PROVIDER = {
    'provider': 'official_vision_mcp_in_opencode',
    'transport': 'opencode_cli_official_mcp_v1',
    'progress_policy': 'finite_stages_no_retry_v1',
    'vision_model': 'glm-5.3-flash',
    'agent_model': 'zhipuai-coding-plan/glm-5.3-flash',
}


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
        self.exclusions = exclusions or []
        self.queue = self.output / 'mcp_queue'
        self.queue.mkdir(exist_ok=True)
        self.recover_received()

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
        scope = request.get('observation_scope', {})
        for blocked in self.exclusions:
            if (request['media_sha256'] == blocked['media_sha256'] or
                    scope.get('source_sha256') == blocked['scope'].get('source_sha256') and
                    scope.get('source_start_s') == blocked['scope'].get('source_start_s') and
                    scope.get('source_end_s') == blocked['scope'].get('source_end_s')):
                raise LibraryStopped('historical_unknown_observation_no_replay:' + blocked['call_id'])
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
