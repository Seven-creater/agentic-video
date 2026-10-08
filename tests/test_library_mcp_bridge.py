"""Bridge startup against SDK stubs; no official server or network is started."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

BRIDGE = Path(__file__).resolve().parents[1] / 'omni_story/library/mcp_bridge.mjs'
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Bridge startup requires Node.js')


def run_bridge(tmp_path, max_tokens=None):
    root = tmp_path / 'task'
    root.mkdir()
    # Immediately exit after initialization. Stub transport never creates a
    # process, and stub client treats a tool call as a test failure.
    (root / 'mcp_stop').touch()
    sdk = tmp_path / 'package/node_modules/@modelcontextprotocol/sdk'
    client = sdk / 'dist/esm/client'
    client.mkdir(parents=True)
    (sdk / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
    (client / 'stdio.js').write_text('''
import fs from 'node:fs';
export class StdioClientTransport {
  constructor(options) {
    fs.writeFileSync(process.env.BRIDGE_TEST_CAPTURE, JSON.stringify({
      max_tokens: options.env.Z_AI_VISION_MODEL_MAX_TOKENS,
      timeout: options.env.Z_AI_TIMEOUT,
      retries: options.env.Z_AI_RETRY_COUNT,
      model: options.env.Z_AI_VISION_MODEL,
      mode: options.env.Z_AI_MODE
    }));
  }
}
''', encoding='utf-8')
    (client / 'index.js').write_text('''
export class Client {
  async connect() {}
  async listTools() { return {tools: [{name: 'analyze_video'}]}; }
  async callTool() { throw new Error('unexpected_model_call_in_startup_test'); }
  async close() {}
}
''', encoding='utf-8')
    capture = tmp_path / 'captured.json'
    env = {**os.environ, 'Z_AI_API_KEY': 'synthetic-test-secret', 'BRIDGE_TEST_CAPTURE': str(capture)}
    env.pop('Z_AI_VISION_MODEL_MAX_TOKENS', None)
    env.pop('OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY', None)
    if max_tokens is not None:
        env['Z_AI_VISION_MODEL_MAX_TOKENS'] = max_tokens
    result = subprocess.run(['node', str(BRIDGE), str(root), str(tmp_path / 'package')],
                            env=env, capture_output=True, text=True, timeout=15)
    return root, capture, result


@pytest.mark.parametrize('configured,expected', [(None, 131072), ('131072', 131072),
                                              ('65536', 65536), ('16384', 16384), ('1', 1)])
def test_bridge_passes_default_or_explicit_output_limit_and_records_actual_timeout(tmp_path, configured, expected):
    root, capture, result = run_bridge(tmp_path, configured)
    assert result.returncode == 0, result.stderr
    assert 'official_vision_mcp_connected' in result.stdout
    captured = json.loads(capture.read_text(encoding='utf-8'))
    assert captured == {'max_tokens': str(expected), 'timeout': '600000', 'retries': '0',
                        'model': 'glm-5.3-flash', 'mode': 'ZHIPU'}
    ready = json.loads((root / 'mcp_ready.json').read_text(encoding='utf-8'))
    assert ready['max_output_tokens'] == expected
    assert ready['model_timeout_ms'] == 600000
    assert ready['tool_timeout_ms'] == 660000
    assert ready['model'] == 'glm-5.3-flash'
    assert ready['max_concurrent_parents'] == 1
    assert 'synthetic-test-secret' not in (root / 'mcp_ready.json').read_text(encoding='utf-8')
    assert list((root / 'mcp_queue').iterdir()) == []


@pytest.mark.parametrize('invalid', ['0', '-1', '131073', '1.5', '1e5', 'Infinity', 'NaN', '',
                                   ' 16384 ', '016384', '999999999999999999999999'])
def test_invalid_output_limit_stops_before_connect_or_job_file_creation(tmp_path, invalid):
    root, capture, result = run_bridge(tmp_path, invalid)
    assert result.returncode != 0
    assert 'Z_AI_VISION_MODEL_MAX_TOKENS_must_be_integer_1_to_131072' in result.stderr
    assert not capture.exists()
    assert not (root / 'mcp_queue').exists()
    assert not (root / 'mcp_ready.json').exists()
    assert 'synthetic-test-secret' not in result.stdout + result.stderr
