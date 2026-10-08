"""Node timeout changes require a frozen independent-input strategy."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import omni_story.library as library_package
from omni_story.library import independent_source_resume as independent
from omni_story.library.media import sha256_file
from omni_story.library.state import json_sha, write_json
from test_library_independent_source_resume import recorded

pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Needs Node')


def run(root, env):
    module = (Path(library_package.__file__).resolve().parent / 'mcp_timeouts.mjs').as_uri()
    script = 'const {connectionTimeouts}=await import(process.argv[1]); process.stdout.write(JSON.stringify(connectionTimeouts(process.argv[2],JSON.parse(process.argv[3]))));'
    return subprocess.run(['node', '--input-type=module', '-e', script, module, str(root), json.dumps(env)],
                          text=True, capture_output=True, timeout=30)


def test_default_deadlines_remain_unchanged(tmp_path):
    r = run(tmp_path, {})
    assert r.returncode == 0
    assert json.loads(r.stdout) == {'modelTimeoutMs': 600000, 'toolTimeoutMs': 660000, 'policy': None}


def test_hash_bound_forward_policy_changes_only_new_connection(recorded):
    independent.enable(recorded.output, 'Synthetic forward work')
    entry = recorded.data['artifacts'][independent.ARTIFACT][0]
    write_json(recorded.output / 'library_state.json', recorded.data)
    env = {'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE': entry['path'],
           'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256': sha256_file(entry['path'])}
    r = run(recorded.output, env)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {'modelTimeoutMs': 1200000, 'toolTimeoutMs': 1260000, 'policy': independent.POLICY}
    # No original request or outcome is changed by reading connection settings.
    recorded.data['calls'][-1]['status'] = 'received'
    write_json(recorded.output / 'library_state.json', recorded.data)
    r = run(recorded.output, env)
    assert r.returncode != 0 and 'history_changed' in r.stderr


def test_free_env_or_changed_timeout_is_rejected(recorded):
    independent.enable(recorded.output, 'Synthetic work')
    entry = recorded.data['artifacts'][independent.ARTIFACT][0]
    write_json(recorded.output / 'library_state.json', recorded.data)
    r = run(recorded.output, {'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE': entry['path']})
    assert r.returncode != 0 and 'authorization_changed' in r.stderr
    value = json.loads(Path(entry['path']).read_text(encoding='utf-8'))
    value['model_timeout_ms'] = 1800000
    write_json(entry['path'], value)
    entry['sha256'] = json_sha(value)
    write_json(recorded.output / 'library_state.json', recorded.data)
    env = {'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE': entry['path'],
           'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256': sha256_file(entry['path'])}
    r = run(recorded.output, env)
    assert r.returncode != 0 and 'scope_changed' in r.stderr
