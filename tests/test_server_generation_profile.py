"""Generation settings use persisted configuration; all MCP clients are fake."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import pipeline, server_cli
from omni_story.library.state import LibraryStopped


PROFILE = dict(server_cli.VISION_GENERATION)
TIMEOUTS = Path(server_cli.__file__).with_name('mcp_timeouts.mjs')
BRIDGE = Path(server_cli.__file__).with_name('opencode_mcp.mjs')


def _state(root, profile=None, *, provider='official_vision_mcp_in_opencode'):
    config = {'provider': provider}
    if profile is not None:
        config['vision_generation'] = profile
    path = root / 'library_state.json'
    path.write_text(json.dumps({'input_lock': {'configuration': config}}), encoding='utf-8')
    return path


def _node(code, *args, env=None):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js is required for synthetic MCP tests')
    return subprocess.run([executable, '--input-type=module', '-e', code, *map(str, args)],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def test_fresh_profile_ignores_runtime_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('Z_AI_VISION_MODEL_MAX_TOKENS', '999999')
    monkeypatch.setenv('Z_AI_TIMEOUT', '1')
    result = server_cli._vision_generation(tmp_path)
    assert result == PROFILE
    result['max_output_tokens'] = 1
    assert server_cli._vision_generation(tmp_path) == PROFILE
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('profile', [None, PROFILE])
def test_existing_lock_is_read_only(tmp_path, profile):
    path = _state(tmp_path, profile)
    original = path.read_bytes()
    assert server_cli._vision_generation(tmp_path) == profile
    assert path.read_bytes() == original


@pytest.mark.parametrize('key,value', [
    ('policy', 'different-policy'), ('max_output_tokens', True),
    ('max_output_tokens', 32768.0), ('max_output_tokens', 131073),
    ('model_timeout_ms', 0), ('tool_timeout_ms', 1260001), ('extra', 1),
])
def test_python_rejects_invalid_locked_profile(tmp_path, key, value):
    _state(tmp_path, {**PROFILE, key: value})
    with pytest.raises(LibraryStopped, match='server_vision_generation_invalid'):
        server_cli._vision_generation(tmp_path)


@pytest.mark.parametrize('existing', [False, True])
def test_worker_adds_profile_only_for_fresh_task(tmp_path, monkeypatch, existing):
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'synthetic reference')
    library = tmp_path / 'library'
    library.mkdir()
    output = tmp_path / 'output'
    output.mkdir()
    if existing:
        _state(output)
    monkeypatch.setattr(server_cli, 'settings', lambda home: {})
    monkeypatch.setattr(server_cli, 'load_history', lambda config: None)
    monkeypatch.setattr(server_cli, 'credential', lambda home: 'synthetic-no-network')
    monkeypatch.delenv('Z_AI_API_KEY', raising=False)
    observed = {}
    def execute(*args, **kwargs):
        observed.update(kwargs['provider_config'])
        return {'usage': {'requests': 0}}
    monkeypatch.setattr(pipeline, 'execute', execute)
    server_cli._run(SimpleNamespace(home=tmp_path, reference=reference,
                                   library=library, output=output, asr=False))
    assert observed.get('vision_generation') == (None if existing else PROFILE)


@pytest.mark.parametrize('profile,expected', [
    (None, {'modelTimeoutMs': 600000, 'toolTimeoutMs': 660000, 'policy': None}),
    (PROFILE, {'modelTimeoutMs': 1200000, 'toolTimeoutMs': 1260000,
               'policy': 'opencode_vision_capacity_v2'}),
])
def test_dispatcher_deadline_uses_locked_profile(tmp_path, profile, expected):
    path = _state(tmp_path, profile)
    original = path.read_bytes()
    result = _node("const m=await import(process.argv[1]);"
                   "console.log(JSON.stringify(m.connectionTimeouts(process.argv[2],{})));",
                   TIMEOUTS.as_uri(), tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert path.read_bytes() == original


@pytest.mark.parametrize('profile,provider', [
    ({**PROFILE, 'max_output_tokens': False}, 'official_vision_mcp_in_opencode'),
    ({**PROFILE, 'model_timeout_ms': 1200001}, 'official_vision_mcp_in_opencode'),
    ({**PROFILE, 'tool_timeout_ms': 0}, 'official_vision_mcp_in_opencode'),
    (PROFILE, 'official_vision_mcp_in_codex'),
])
def test_dispatcher_rejects_invalid_or_other_provider_profile(tmp_path, profile, provider):
    _state(tmp_path, profile, provider=provider)
    result = _node("const m=await import(process.argv[1]);m.connectionTimeouts(process.argv[2],{});",
                   TIMEOUTS.as_uri(), tmp_path)
    assert result.returncode != 0
    assert 'server_vision_generation_' in result.stderr


def _fake_sdk(package):
    root = package / 'node_modules/@modelcontextprotocol/sdk'
    root.mkdir(parents=True)
    (root / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
    modules = {
        'types.js': "export const ListToolsRequestSchema='list',CallToolRequestSchema='call';",
        'server/stdio.js': 'export class StdioServerTransport {}',
        'server/index.js': """export class Server {
          handlers=new Map(); setRequestHandler(key,fn){this.handlers.set(key,fn);}
          async connect(){await this.handlers.get('call')({params:{name:'execute',arguments:{}}});}
          async close(){}
        }""",
        'client/stdio.js': """export class StdioClientTransport {
          constructor(options){this.options=options;}
        }""",
        'client/index.js': """import fs from 'node:fs';import path from 'node:path';
        export class Client {
          async connect(transport){this.transport=transport;}
          async callTool(request,schema,options){
            const e=this.transport.options.env;
            fs.writeFileSync(path.join(e.OMNI_LIBRARY_MCP_ROOT,'synthetic_launch.json'),
              JSON.stringify({max_tokens:e.Z_AI_VISION_MODEL_MAX_TOKENS,
                model_timeout:e.Z_AI_TIMEOUT,retries:e.Z_AI_RETRY_COUNT,
                tool_timeout:options.timeout}));
            return {content:[{type:'text',text:'synthetic only'}]};
          }
          async close(){}
        }""",
    }
    for name, content in modules.items():
        target = root / 'dist/esm' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')


@pytest.mark.parametrize('profile,max_tokens,model_timeout', [
    (None, '16384', '600000'), (PROFILE, '32768', '1200000'),
])
def test_bridge_passes_locked_native_environment_without_starting_a_model(
        tmp_path, profile, max_tokens, model_timeout):
    _state(tmp_path, profile)
    queue = tmp_path / 'mcp_queue'
    queue.mkdir()
    job = 'glm_001_plan_0'
    (queue / (job + '.request.json')).write_text(json.dumps(
        {'job_id': job, 'tool': 'analyze_image', 'arguments': {}}), encoding='utf-8')
    package = tmp_path / 'fake-package'
    _fake_sdk(package)
    env = {**os.environ, 'OMNI_LIBRARY_OPENCODE_JOB': job,
           'Z_AI_API_KEY': 'synthetic-no-network',
           'Z_AI_VISION_MODEL_MAX_TOKENS': '1', 'Z_AI_TIMEOUT': '1'}
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js is required for synthetic MCP tests')
    # Exercise the real CLI entrypoint; importing helpers deliberately does not launch MCP.
    result = subprocess.run([executable, str(BRIDGE), str(tmp_path), str(package)],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / 'synthetic_launch.json').read_text()) == {
        'max_tokens': max_tokens, 'model_timeout': model_timeout,
        'retries': '0', 'tool_timeout': 1260000}
    assert json.loads((queue / (job + '.response.json')).read_text())['status'] == 'complete'
