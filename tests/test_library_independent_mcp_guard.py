"""Real Node guard with synthetic bound records and a stub transport only."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

from omni_story.library.state import json_sha, write_json
from test_library_goal_mcp import GUARD, PATTERN


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(not shutil.which('node'), reason='Needs Node.js')
@pytest.mark.parametrize('case', ['prefact', 'repair', 'draft', 'missing_env', 'wrong_round',
    'reference', 'unknown_media', 'unknown_scope', 'wrong_prefact_scope', 'other_pending',
    'new_unknown', 'changed_prefix', 'changed_policy', 'changed_old_request', 'incomplete_facts',
    'early_review', 'prefact_after_draft'])
def test_only_independent_bound_round_can_cross_unknown(tmp_path, case):
    root = tmp_path / 'task'
    root.mkdir()
    package = tmp_path / 'package' / 'node_modules' / 'undici'
    package.mkdir(parents=True)
    (package / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
    (package / 'index.js').write_text('export class Agent {}', encoding='utf-8')
    input_lock = {'reference_sha256': 'b' * 64}
    grant = {'policy': 'goal_feedback_extension_v1', 'task_id': 'synthetic-task',
             'base_request_limit': 80, 'baseline_request_count': 93, 'additional_requests': None,
             'effective_request_limit': None, 'allowed_stage_pattern': PATTERN,
             'request_limit_policy': 'progress_guard_no_numeric_request_cap_v1',
             'input_lock_sha256': json_sha(input_lock)}
    calls = [{'id': f'old_{i}', 'name': 'historical', 'status': 'received'} for i in range(131)]
    exclusions = []
    for index, ident, kind, sha, start, end in [
        (3, 'glm_004_coarse_978d5360_01', 'sparse_contact_sheet', 'a' * 64, 600, 1200),
        (130, 'glm_131_active_10_draft', 'continuous_window', 'b' * 64, 0, 21.933333)]:
        request = {'tool': 'analyze_image', 'media_sha256': f'{index:064x}', 'observation_scope': {
            'kind': kind, 'source_sha256': sha, 'source_start_s': start, 'source_end_s': end}}
        write_json(root / 'calls' / ident / 'request.json', request)
        calls[index] = {'id': ident, 'name': 'active_10_draft' if index == 130 else 'old_coarse',
                        'status': 'uncertain', 'request_sha256': json_sha(request)}
        exclusions.append({'call_id': ident, 'request_sha256': json_sha(request),
                           'media_sha256': request['media_sha256'], 'scope': request['observation_scope']})
    state = {'task_id': 'synthetic-task', 'max_requests': 80, 'input_lock': input_lock,
             'calls': calls, 'request_count': len(calls), 'artifacts': {}}
    snapshot = root / 'artifacts' / 'baseline.json'
    write_json(snapshot, state)
    prefacts = [{'stage': f'semantic_slice_11_{i:016x}', 'source_sha256': 'a' * 64,
                 'segment': {'segment_id': f'seg_{i}', 'source_id': 'movie',
                             'source_in_s': 1300 + i, 'source_out_s': 1301 + i}} for i in range(6)]
    record = {'policy': 'independent_library_evidence_after_unknown_v1', 'round': 11,
              'task_id': state['task_id'], 'input_lock_sha256': grant['input_lock_sha256'],
              'reference_sha256': input_lock['reference_sha256'], 'baseline_request_count': 131,
              'activation_baseline_requests': 131, 'baseline_state_path': str(snapshot),
              'baseline_state_sha256': file_sha(snapshot), 'prefix_calls_sha256': json_sha(calls),
              'protected_files': [{'path': str(p), 'sha256': file_sha(p)} for p in (root / 'calls').glob('*/*')],
              'admitted_unknown_call_ids': [e['call_id'] for e in exclusions], 'unknown_inputs': exclusions,
              'prefacts': prefacts, 'planning_carrier_stage': prefacts[0]['stage'],
              'model_timeout_ms': 1200000, 'tool_timeout_ms': 1260000,
              'new_unique_windows': 0, 'new_renders': 1, 'repairs_per_stage': 1,
              'reference_media_forbidden': True, 'old_calls_unchanged': True,
              'no_numeric_request_ceiling': True, 'reference_is_known_cached_model_interpretation': True}
    policy = root / 'artifacts' / 'independent.json'
    write_json(policy, record)
    policy_sha = file_sha(policy)
    state['artifacts']['goal_research_independent_11'] = [{'path': str(policy), 'sha256': json_sha(record)}]
    image = root / 'sheet.jpg'
    image.write_bytes(b'synthetic library image, not reference')
    request = {'tool': 'analyze_image', 'arguments': {'image_source': str(image), 'prompt': 'synthetic'},
               'media_sha256': file_sha(image), 'observation_scope': {
                   'kind': 'continuous_window', 'source_sha256': 'a' * 64,
                   'source_start_s': 1300, 'source_end_s': 1301}}
    stage = prefacts[0]['stage']
    if case in {'draft', 'incomplete_facts'}:
        for i, p in enumerate(prefacts):
            ident = f'glm_{132 + i:03d}_{p["stage"]}'
            calls.append({'id': ident, 'name': p['stage'], 'status': 'received'})
            if case == 'draft' or i < 5:
                write_json(root / 'calls' / ident / 'parsed.json', {**p['segment'], 'source_sha256': p['source_sha256']})
        stage = 'active_11_draft'
    if case in {'repair', 'prefact_after_draft'}:
        original = 'active_11_draft' if case == 'prefact_after_draft' else stage
        calls.append({'id': 'known_parent', 'name': original, 'status': 'received'})
        if case == 'repair':
            stage += '_repair'
    if case == 'wrong_round':
        stage = 'active_12_draft'
    if case == 'early_review':
        stage = 'active_11_review'
    if case == 'reference':
        request['observation_scope']['source_sha256'] = 'b' * 64
    if case == 'unknown_media':
        request['media_sha256'] = exclusions[0]['media_sha256']
    if case == 'unknown_scope':
        request['observation_scope'] = deepcopy(exclusions[0]['scope'])
    if case == 'wrong_prefact_scope':
        request['observation_scope']['source_end_s'] += 1
    if case in {'other_pending', 'new_unknown'}:
        calls.append({'id': 'other_new', 'name': 'active_11_draft',
                      'status': 'submitted' if case == 'other_pending' else 'uncertain'})
    if case == 'changed_prefix':
        calls[130]['status'] = 'received'
    if case == 'changed_policy':
        write_json(policy, {**record, 'new_unique_windows': 1})
    if case == 'changed_old_request':
        write_json(root / 'calls' / exclusions[0]['call_id'] / 'request.json', {'changed': True})
    ident = f'glm_{len(calls) + 1:03d}_{stage}'
    calls.append({'id': ident, 'name': stage, 'status': 'submitted', 'request_sha256': json_sha(request)})
    if case == 'repair':
        calls[-1]['repair_of'] = 'known_parent'
    write_json(root / 'calls' / ident / 'request.json', request)
    state['request_count'] = len(calls)
    write_json(root / 'library_state.json', state)
    write_json(root / 'mcp_current.json', {'job_id': ident})
    authorization = root / 'authorization.json'
    write_json(authorization, grant)
    env = {**os.environ, 'OMNI_LIBRARY_MCP_ROOT': str(root),
           'OMNI_LIBRARY_MCP_PACKAGE_ROOT': str(package.parent.parent),
           'OMNI_LIBRARY_REQUEST_LIMIT_POLICY': grant['request_limit_policy'],
           'OMNI_LIBRARY_EXTENSION_AUTH_FILE': str(authorization),
           'OMNI_LIBRARY_EXTENSION_AUTH_SHA256': file_sha(authorization),
           'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE': str(policy),
           'OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256': policy_sha}
    if case == 'missing_env':
        env.pop('OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE')
        env.pop('OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256')
    script = """
globalThis.fetch = async () => new Response('{"ok":true}', {status:200});
try {
  await import(process.argv[1]);
  await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {method:'POST',body:'{"fixture":1}'});
  console.log('accepted');
} catch (error) { console.log(String(error.message)); }
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script, GUARD.as_uri()],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    accepted = case in {'prefact', 'repair', 'draft'}
    assert (result.stdout.strip() == 'accepted') is accepted, result.stdout
    journal = root / 'mcp_http.jsonl'
    requests = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()
                if json.loads(line)['type'] == 'request'] if journal.exists() else []
    assert len(requests) == int(accepted)
