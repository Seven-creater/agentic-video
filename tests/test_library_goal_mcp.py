"""Goal transport isolation with real Node and synthetic local replies only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import omni_story.library as library_package


GUARD = Path(library_package.__file__).resolve().parent / 'mcp_guard.mjs'
PATTERN = r'^(?:active_([5-9]|[1-9][0-9]+)_(?:draft|finecut|blind|economy|review)|semantic_(?:slice|claims)_([5-9]|[1-9][0-9]+)_[a-f0-9]{16})(?:_repair)?$'


@pytest.mark.skipif(not shutil.which('node'), reason='Node guard requires Node.js')
@pytest.mark.parametrize('case', ['valid', 'small_base', 'old_unknown', 'later_round', 'semantic', 'repair', 'past80',
    'wrong_round', 'wrong_stage', 'old_call', 'received', 'tampered', 'finite_grant',
    'invalid_baseline', 'altered_header', 'bad_count', 'non_current_call',
    'other_pending', 'other_unknown', 'repeat_job', 'repeat_body',
    'local', 'local_repair', 'local_wrong_parent', 'local_changed_card', 'local_wrong_scope'])
def test_goal_guard_uses_bound_grant_current_submission_and_no_replay(tmp_path, case):
    package = tmp_path / 'package'
    undici = package / 'node_modules/undici'
    undici.mkdir(parents=True)
    (undici / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
    (undici / 'index.js').write_text('export class Agent {}', encoding='utf-8')
    root = tmp_path / 'task'
    root.mkdir()
    grant = {'policy': 'goal_feedback_extension_v1', 'task_id': 'synthetic-task',
        'base_request_limit': 80, 'baseline_request_count': 2, 'additional_requests': None,
        'effective_request_limit': None, 'allowed_stage_pattern': PATTERN,
        'request_limit_policy': 'progress_guard_no_numeric_request_cap_v1'}
    if case == 'finite_grant':
        grant['effective_request_limit'] = 10000
    if case == 'invalid_baseline':
        grant['baseline_request_count'] = -1
    if case == 'old_call':
        grant['baseline_request_count'] = 3
    stage = {'later_round': 'active_12_draft', 'semantic': 'semantic_slice_5_' + 'a' * 16,
        'repair': 'active_5_draft_repair', 'wrong_round': 'active_4_draft',
        'wrong_stage': 'active_5_selection'}.get(case, 'active_5_draft')
    if case.startswith('local'):
        stage='active_8_trim_'+ 'a'*16 + ('_repair' if case=='local_repair' else '')
    calls = [{'id': 'old1'}, {'id': 'old2'}, {'id': 'new3', 'name': stage,
        'status': 'received' if case == 'received' else 'submitted'}]
    if case == 'old_unknown':
        calls[1]['status'] = 'uncertain'
    if case in {'other_pending', 'other_unknown'}:
        calls.insert(2, {'id': 'different3', 'name': 'active_5_draft',
            'status': 'submitted' if case == 'other_pending' else 'uncertain'})
    if case == 'non_current_call':
        calls.append({'id': 'new4', 'name': 'active_5_finecut', 'status': 'received'})
    state = {'task_id': 'synthetic-task', 'max_requests': 80,
        'request_count': len(calls), 'calls': calls}
    if case.startswith('local'):
        from omni_story.library.state import json_sha
        card=root/'local.md'; card.write_text('generic local trimming 方法',encoding='utf-8')
        grant['input_lock_sha256']='locked'
        local={'policy':'local_counterfactual_trim_v1','round':8,'activation_baseline_requests':2,
            'input_lock_sha256':'locked','knowledge_path':str(card),
            'knowledge_sha256':hashlib.sha256(card.read_bytes()).hexdigest(),
            'additional_stage_pattern':r'^active_8_trim_[a-f0-9]{16}(?:_repair)?$',
            'one_local_proposal_per_parent':True,'repairs_per_stage':1,'new_unique_windows':0,
            'parent_inputs':[{'stage':'active_8_trim_'+('b' if case=='local_wrong_parent' else 'a')*16}]}
        if case=='local_wrong_scope':local['new_unique_windows']=1
        policy=root/'local.json'; policy.write_text(json.dumps(local,ensure_ascii=False),encoding='utf-8')
        state['artifacts']={'goal_research_local_8':[{'path':str(policy),'sha256':json_sha(local)}]}
        if case=='local_changed_card':card.write_text('changed',encoding='utf-8')
    if case == 'small_base':
        grant['base_request_limit'] = state['max_requests'] = 8
    if case == 'altered_header':
        grant['base_request_limit'] = state['max_requests'] = 10000
    if case == 'bad_count':
        state['request_count'] += 1
    authorization = root / 'authorization.json'
    authorization.write_text(json.dumps(grant), encoding='utf-8')
    authorization_sha = hashlib.sha256(authorization.read_bytes()).hexdigest()
    if case == 'tampered':
        authorization.write_text(json.dumps({**grant, 'additional_requests': 100}), encoding='utf-8')
    (root / 'library_state.json').write_text(json.dumps(state), encoding='utf-8')
    (root / 'mcp_current.json').write_text('{"job_id":"new3"}', encoding='utf-8')
    if case == 'past80':
        (root / 'mcp_http.jsonl').write_text('\n'.join(json.dumps({
            'type': 'request', 'hash': str(i), 'job_id': str(i)}) for i in range(80)) + '\n', encoding='utf-8')
    script = """
globalThis.fetch = async () => new Response('{"ok":true}', {status:200});
await import(process.argv[1]);
try {
  await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {method:'POST',body:'{"fixture":1}'});
  if (process.argv[2] === 'repeat_job') {
    await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {method:'POST',body:'{"fixture":2}'});
  }
  if (process.argv[2] === 'repeat_body') {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const file = path.join(process.env.OMNI_LIBRARY_MCP_ROOT, 'library_state.json');
    const state = JSON.parse(fs.readFileSync(file, 'utf8'));
    state.calls.push({id:'new4',name:'active_5_finecut',status:'submitted'});
    state.calls[2].status='received'; state.request_count=state.calls.length;
    fs.writeFileSync(file,JSON.stringify(state));
    fs.writeFileSync(path.join(process.env.OMNI_LIBRARY_MCP_ROOT,'mcp_current.json'),'{"job_id":"new4"}');
    await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {method:'POST',body:'{"fixture":1}'});
  }
  console.log('accepted');
} catch (error) { console.log(String(error.message)); }
"""
    env = {**os.environ, 'OMNI_LIBRARY_MCP_ROOT': str(root),
        'OMNI_LIBRARY_MCP_PACKAGE_ROOT': str(package), 'OMNI_LIBRARY_MAX_REQUESTS': '10000',
        'OMNI_LIBRARY_REQUEST_LIMIT_POLICY': grant['request_limit_policy'],
        'OMNI_LIBRARY_EXTENSION_AUTH_FILE': str(authorization),
        'OMNI_LIBRARY_EXTENSION_AUTH_SHA256': authorization_sha}
    result = subprocess.run(['node', '--input-type=module', '-e', script, GUARD.as_uri(), case],
        env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    accepted = {'valid', 'small_base', 'old_unknown', 'later_round', 'semantic', 'repair', 'past80','local','local_repair'}
    if case in accepted:
        assert result.stdout.strip() == 'accepted'
    elif case == 'tampered':
        assert 'authorization_modified' in result.stdout
    elif case in {'repeat_job', 'repeat_body'}:
        assert 'retry_or_budget_blocked' in result.stdout
    else:
        assert 'goal_budget_or_stage_blocked' in result.stdout
    journal = root / 'mcp_http.jsonl'
    requests = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()
        if json.loads(line)['type'] == 'request'] if journal.exists() else []
    assert len(requests) == (81 if case == 'past80' else 1 if case in accepted | {'repeat_job', 'repeat_body'} else 0)


def test_goal_launcher_prefers_full_goal_validation_and_clears_inherited_cap(tmp_path, monkeypatch):
    from omni_story.library import extension_budget, goal_budget, mcp_launch
    output = tmp_path / 'task'
    output.mkdir()
    authorization = output / 'goal.json'
    authorization.write_text('{"policy":"synthetic-bound-goal"}', encoding='utf-8')
    artifacts = {extension_budget.AUTHORIZATION: [{}], goal_budget.AUTHORIZATION: [{'path': str(authorization)}]}
    (output / 'library_state.json').write_text(json.dumps({'artifacts': artifacts}), encoding='utf-8')
    state = SimpleNamespace(data={'artifacts': artifacts})
    checked = []
    monkeypatch.setattr(goal_budget, 'stage_state', lambda directory: state)
    def validate(value):
        checked.append(value)
        return {'request_limit_policy': extension_budget.REQUEST_LIMIT_POLICY}
    monkeypatch.setattr(goal_budget, 'get_authorization', validate)
    monkeypatch.setattr(extension_budget, 'stage_state', lambda directory: pytest.fail('old writer must not be used'))
    monkeypatch.setenv('Z_AI_API_KEY', 'synthetic-test-credential')
    monkeypatch.setenv('OMNI_LIBRARY_MAX_REQUESTS', '1000000')
    monkeypatch.setenv('OMNI_LIBRARY_EXTENSION_AUTH_FILE', 'untrusted-old-path')
    monkeypatch.setattr('sys.argv', ['mcp_launch', '--output', str(output), '--package-root', str(tmp_path / 'package')])
    captured = {}
    monkeypatch.setattr(mcp_launch.subprocess, 'call', lambda arguments, env: captured.update(env) or 0)
    assert mcp_launch.main() == 0
    assert checked == [state]
    assert 'OMNI_LIBRARY_MAX_REQUESTS' not in captured
    assert captured['OMNI_LIBRARY_EXTENSION_AUTH_FILE'] == str(authorization)
    assert captured['OMNI_LIBRARY_EXTENSION_AUTH_SHA256'] == hashlib.sha256(authorization.read_bytes()).hexdigest()
    assert captured['OMNI_LIBRARY_REQUEST_LIMIT_POLICY'] == extension_budget.REQUEST_LIMIT_POLICY


def test_invalid_goal_launcher_does_not_read_credentials_or_clear_stop(tmp_path, monkeypatch):
    from omni_story.library import goal_budget, mcp_launch
    from omni_story.library.state import LibraryStopped
    output = tmp_path / 'task'
    output.mkdir()
    (output / 'library_state.json').write_text(json.dumps({'artifacts': {goal_budget.AUTHORIZATION: [{}]}}), encoding='utf-8')
    stop = output / 'mcp_stop'
    stop.write_text('preserve stop', encoding='utf-8')
    monkeypatch.delenv('Z_AI_API_KEY', raising=False)
    def reject(directory):
        raise LibraryStopped('goal:invalid_grant')
    monkeypatch.setattr(goal_budget, 'stage_state', reject)
    monkeypatch.setattr(mcp_launch.getpass, 'getpass', lambda *args: pytest.fail('must validate before hidden prompt'))
    monkeypatch.setattr(mcp_launch.subprocess, 'call', lambda *args, **kw: pytest.fail('no MCP launch'))
    monkeypatch.setattr('sys.argv', ['mcp_launch', '--output', str(output), '--package-root', str(tmp_path / 'package')])
    with pytest.raises(LibraryStopped, match='invalid_grant'):
        mcp_launch.main()
    assert stop.read_text(encoding='utf-8') == 'preserve stop'
