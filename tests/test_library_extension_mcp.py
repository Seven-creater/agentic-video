"""Real Node guard with a stub transport: no model/network request is sent."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

GUARD = Path(__file__).resolve().parents[1] / 'omni_story/library/mcp_guard.mjs'
PATTERN = r'^(?:active_4_(?:draft|finecut|blind|economy|review)|semantic_(?:slice|claims)_4_[a-f0-9]{16})(?:_repair)?$'


@pytest.mark.skipif(not shutil.which('node'), reason='Node guard integration requires Node.js')
@pytest.mark.parametrize('case', ['valid', 'wrong_stage', 'finite_grant', 'tampered', 'env_only', 'repeat_job', 'past80'])
def test_guard_extension_requires_bound_authorization_and_blocks_duplicate_job(tmp_path, case):
    package = tmp_path / 'package'
    undici = package / 'node_modules/undici'
    undici.mkdir(parents=True)
    (undici/'package.json').write_text('{"type":"module"}',encoding='utf-8')
    (undici/'index.js').write_text('export class Agent {}',encoding='utf-8')
    root = tmp_path / 'task'
    root.mkdir()
    grant = {'policy':'active_finecut_extension_v2','task_id':'synthetic-task',
             'base_request_limit':80,'baseline_request_count':2,'additional_requests':None,
             'effective_request_limit':None,'allowed_stage_pattern':PATTERN,
             'request_limit_policy':'progress_guard_no_numeric_request_cap_v1'}
    if case == 'finite_grant':
        grant['effective_request_limit'] = 1000
    authorization = root/'authorization.json'
    authorization.write_text(json.dumps(grant),encoding='utf-8')
    authorization_sha = hashlib.sha256(authorization.read_bytes()).hexdigest()
    if case == 'tampered':
        authorization.write_text(json.dumps({**grant,'additional_requests':100}),encoding='utf-8')
    stage = 'plan_0' if case == 'wrong_stage' else 'active_4_draft'
    (root/'library_state.json').write_text(json.dumps({'task_id':'synthetic-task','max_requests':80,
        'request_count':3,'calls':[{'id':'old1'},{'id':'old2'},{'id':'new3','name':stage,'status':'submitted'}]}),encoding='utf-8')
    (root/'mcp_current.json').write_text('{"job_id":"new3"}',encoding='utf-8')
    if case in {'env_only', 'past80'}:
        (root/'mcp_http.jsonl').write_text('\n'.join(json.dumps({'type':'request','hash':str(i),'job_id':str(i)})
            for i in range(80))+'\n',encoding='utf-8')
    script = """
globalThis.fetch = async () => new Response('{"ok":true}', {status:200});
await import(process.argv[1]);
try {
  await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {method:'POST',body:'{"fixture":1}'});
  if (process.argv[2] === 'repeat_job') {
    await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {method:'POST',body:'{"fixture":2}'});
  }
  console.log('accepted');
} catch (error) { console.log(String(error.message)); }
"""
    env = {**os.environ,'OMNI_LIBRARY_MCP_ROOT':str(root),'OMNI_LIBRARY_MCP_PACKAGE_ROOT':str(package),
           'OMNI_LIBRARY_MAX_REQUESTS':'1000', 'OMNI_LIBRARY_REQUEST_LIMIT_POLICY':grant['request_limit_policy']}
    if case != 'env_only':
        env.update(OMNI_LIBRARY_EXTENSION_AUTH_FILE=str(authorization),OMNI_LIBRARY_EXTENSION_AUTH_SHA256=authorization_sha)
    else:
        env.pop('OMNI_LIBRARY_EXTENSION_AUTH_FILE',None)
        env.pop('OMNI_LIBRARY_EXTENSION_AUTH_SHA256',None)
    result = subprocess.run(['node','--input-type=module','-e',script,GUARD.as_uri(),case],
                            env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    if case in {'valid','past80'}:
        assert result.stdout.strip() == 'accepted'
    elif case == 'tampered':
        assert 'authorization_modified' in result.stdout
    elif case in {'env_only','repeat_job'}:
        assert 'retry_or_budget_blocked' in result.stdout
    else:
        assert 'budget_or_stage_blocked' in result.stdout
    journal = root/'mcp_http.jsonl'
    requests = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()
                if json.loads(line)['type']=='request'] if journal.exists() else []
    assert len(requests) == (81 if case == 'past80' else 80 if case == 'env_only' else 1 if case in {'valid','repeat_job'} else 0)


def test_launcher_uses_progress_policy_without_a_fake_large_request_cap(tmp_path, monkeypatch):
    from omni_story.library import extension_budget, mcp_launch
    from types import SimpleNamespace
    output = tmp_path/'task'
    output.mkdir()
    authorization = output/'authorization.json'
    authorization.write_text('{"policy":"test-bound-authorization"}',encoding='utf-8')
    (output/'library_state.json').write_text(json.dumps({'artifacts':{extension_budget.AUTHORIZATION:[{}]}}),encoding='utf-8')
    state = SimpleNamespace(data={'artifacts':{extension_budget.AUTHORIZATION:[{'path':str(authorization)}]}})
    monkeypatch.setattr(extension_budget,'stage_state',lambda directory:state)
    monkeypatch.setattr(extension_budget,'get_authorization',lambda value:{
        'request_limit_policy':'progress_guard_no_numeric_request_cap_v1','effective_request_limit':None})
    monkeypatch.setenv('Z_AI_API_KEY','synthetic-test-credential')
    monkeypatch.setenv('OMNI_LIBRARY_MAX_REQUESTS','1000000')
    monkeypatch.setattr('sys.argv',['mcp_launch','--output',str(output),'--package-root',str(tmp_path/'package')])
    captured = {}
    def invoke(arguments,env):
        captured.update(env)
        return 0
    monkeypatch.setattr(mcp_launch.subprocess,'call',invoke)
    assert mcp_launch.main() == 0
    assert 'OMNI_LIBRARY_MAX_REQUESTS' not in captured
    assert captured['OMNI_LIBRARY_REQUEST_LIMIT_POLICY'] == 'progress_guard_no_numeric_request_cap_v1'
    assert captured['OMNI_LIBRARY_EXTENSION_AUTH_SHA256'] == hashlib.sha256(authorization.read_bytes()).hexdigest()
