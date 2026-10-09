"""Bound interval correction through native records only; no model or network."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library.opencode_provider import PROVIDER
from omni_story.library.prompts import POLICY_VERSION
from omni_story.library.state import LibraryState, json_sha, write_json
from test_library_opencode_guard import run_guard


KEY = 'server_interval_resume'
ALIAS = 'semantic_interval_resume_v1'
STAGE = 'semantic_slice_0_7f3826a1e389c1bf'
MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Needs Node.js')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reply(value):
    return {'status': 'complete', 'finish_reason': 'stop',
            'result': {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}}


@pytest.fixture
def authorized(tmp_path):
    root = (tmp_path / 'task').resolve()
    config = {**PROVIDER, 'max_rounds': 2, 'max_renders': 2, 'max_fine': 16,
              'vision_generation': {'max_output_tokens': 32768}}
    state = LibraryState(root, {'configuration': config}, max_requests=None)
    proxy = root / 'proxy.mp4'
    proxy.write_bytes(b'synthetic immutable five-second proxy bytes')
    template = {'provider': PROVIDER['provider'], 'policy_version': POLICY_VERSION,
                'tool': 'analyze_video', 'arguments': {'video_source': str(proxy), 'prompt': ''},
                'media_sha256': sha(proxy), 'observation_scope': {'kind': 'continuous_window',
                'source_sha256': 'a' * 64, 'source_start_s': 2020.0, 'source_end_s': 2025.0}}
    parent = None
    for index in range(29):
        request = deepcopy(template)
        request['arguments']['prompt'] = (f'old immutable prompt {index}' if index != 28 else
                                         original['arguments']['prompt'] + MARKER + 'old generic feedback')
        name = STAGE if index == 27 else STAGE + '_repair' if index == 28 else f'overview_{index:08x}'
        call, folder = state.begin_call(name, request, repair_of=parent if index == 28 else None)
        value = {'old': index} if index < 27 else {'observed_duration_s': 5.0, 'evidence': [
            {'evidence_id': 'e1', 'local_start_s': 0, 'local_end_s': 1},
            {'evidence_id': 'e2', 'local_start_s': 2, 'local_end_s': 2}]}
        response = reply(value)
        state.complete_call(call, response)
        if index < 27:
            write_json(folder / 'parsed.json', value)
        else:
            write_json(folder / 'protocol_failure.json', {'error': 'semantic/evidence:outside_observed_slice',
                'attempt': index - 27, 'model_text': response['result']['content'][0]['text']})
        if index == 27:
            original, parent = request, call
    state.set_artifact('old_navigation', {'immutable': True})
    state._reload()
    baseline = root / 'artifacts/baseline_state.json'
    baseline.write_bytes(state.path.read_bytes())
    journal = b'{"fixture":"original HTTP prefix"}\n'
    (root / 'mcp_http.jsonl').write_bytes(journal)
    runtime = tmp_path / 'runtime.py'
    runtime.write_bytes(b'# verified deployed source bytes\n')
    expected = deepcopy(original)
    expected['arguments']['prompt'] += '\n通用区间诊断：0 <= start < end <= observed_duration_s。'
    protected = [{'path': str(path), 'sha256': sha(path)} for path in (root / 'calls').rglob('*')
                 if path.is_file()]
    proof = {'policy': 'opencode_known_interval_resume_v1', 'output': str(root),
             'task_id': state.data['task_id'], 'input_lock': state.data['input_lock'],
             'baseline_request_count': 29, 'baseline_state_path': str(baseline),
             'baseline_state_sha256': sha(baseline), 'stage': STAGE, 'alias': ALIAS,
             'expected_request': expected, 'protected_files': protected,
             'runtime_files': [{'path': str(runtime.resolve()), 'sha256': sha(runtime)}],
             'http_prefix_bytes': len(journal), 'http_prefix_sha256': hashlib.sha256(journal).hexdigest(),
             'user_instruction': '那接着后续剪辑', 'new_rounds': 0, 'max_rounds': 2,
             'max_renders': 2, 'max_fine': 16, 'no_unknown_replay': True,
             'alias_original_and_one_repair_only': True}
    proof_path = state.set_artifact(KEY, proof)
    return state, proof_path, proof, proxy, runtime


def seal(state, proof_path, proof):
    write_json(proof_path, proof)
    state._reload()
    state.data['artifacts'][KEY][0]['sha256'] = json_sha(proof)
    write_json(state.path, state.data)


def append(state, request, name=ALIAS, *, parent=None):
    call, _ = state.begin_call(name, request, repair_of=parent)
    state._reload()
    return deepcopy(state.data), call


def body(request, proxy):
    return {'model': 'glm-5.3-flash', 'stream': False, 'max_tokens': 32768,
            'messages': [{'role': 'user', 'content': [
                {'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,' +
                    base64.b64encode(proxy.read_bytes()).decode()}},
                {'type': 'text', 'text': request['arguments']['prompt']}]}]}


def test_exact_alias_and_only_repair_keep_received_history(authorized):
    state, _, proof, proxy, _ = authorized
    request = proof['expected_request']
    data, call = append(state, request)
    assert run_guard(state.output, data, {'job_id': call['id']}, body(request, proxy)) == 'Infinity'
    state.complete_call(call, reply({'fixture': 'new received but invalid interval'}))
    repair = deepcopy(request)
    repair['arguments']['prompt'] += MARKER + '{"validation_error":"zero_duration"}'
    data, corrected = append(state, repair, ALIAS + '_repair', parent=call)
    assert run_guard(state.output, data, {'job_id': corrected['id']}, body(repair, proxy)) == 'Infinity'
    assert data['request_count'] == 31
    assert read(proof['baseline_state_path'])['calls'] == data['calls'][:29]


@pytest.mark.parametrize('case', [
    'proof_bytes', 'proof_scope', 'baseline_bytes', 'old_call', 'old_artifact', 'old_file',
    'runtime_file', 'HTTP_prefix', 'short_HTTP_prefix', 'media', 'prompt', 'scope', 'same_old_prompt',
    'expected_media_binding', 'duplicate_authorization', 'unknown_history', 'new_round', 'old_stage',
    'unregistered_alias', 'second_alias', 'later_alias',
])
def test_tampering_cannot_grant_an_unbound_post(authorized, case):
    state, proof_path, proof, proxy, runtime = authorized
    request = deepcopy(proof['expected_request'])
    stage = ALIAS
    if case == 'proof_bytes':
        proof['user_instruction'] = 'changed'
        write_json(proof_path, proof)
    elif case == 'proof_scope':
        proof['max_renders'] = 3
        seal(state, proof_path, proof)
    elif case == 'baseline_bytes':
        Path(proof['baseline_state_path']).write_bytes(b'{}')
    elif case == 'old_file':
        Path(proof['protected_files'][0]['path']).write_bytes(b'{}')
    elif case == 'runtime_file':
        runtime.write_bytes(b'changed deployed source')
    elif case == 'HTTP_prefix':
        (state.output / 'mcp_http.jsonl').write_bytes(b'changed' * 20)
    elif case == 'short_HTTP_prefix':
        (state.output / 'mcp_http.jsonl').write_bytes(b'{}')
    elif case == 'media':
        proxy.write_bytes(b'changed media')
    elif case == 'prompt':
        request['arguments']['prompt'] += 'manual added instruction'
    elif case == 'scope':
        request['observation_scope']['source_end_s'] = 2026.0
    elif case == 'same_old_prompt':
        proof['expected_request']['arguments']['prompt'] = 'old immutable prompt 27'
        seal(state, proof_path, proof)
    elif case == 'expected_media_binding':
        proof['expected_request']['observation_scope']['source_end_s'] = 2026.0
        request = deepcopy(proof['expected_request'])
        seal(state, proof_path, proof)
    elif case == 'old_stage':
        stage = STAGE
    elif case == 'new_round':
        stage = 'plan_2'
    elif case in {'second_alias', 'later_alias'}:
        data, prior = append(state, request)
        state.complete_call(prior, reply({'fixture': 'first alias received'}))
        request['arguments']['prompt'] += 'duplicate or renamed attempt'
        stage = ALIAS if case == 'second_alias' else ALIAS + '_retry'
    data, call = append(state, request, stage)
    if case == 'old_call':
        data['calls'][0]['usage']['total_tokens'] = 42
    elif case == 'old_artifact':
        data['artifacts']['old_navigation'] = []
    elif case == 'duplicate_authorization':
        data['artifacts'][KEY] *= 2
    elif case == 'unknown_history':
        data['calls'][0]['status'] = 'uncertain'
    elif case == 'unregistered_alias':
        data['artifacts'].pop(KEY)
    result = run_guard(state.output, data, {'job_id': call['id']}, body(request, proxy))
    assert result != 'Infinity', (case, result)


def test_HTTP_suffix_append_and_original_finite_stages_stay_available(authorized):
    state, _, proof, proxy, _ = authorized
    with (state.output / 'mcp_http.jsonl').open('ab') as journal:
        journal.write(b'{"fixture":"new response is append-only"}\n')
    request = proof['expected_request']
    _, first = append(state, request)
    state.complete_call(first, reply({'fixture': 'corrected interval received'}))
    later = deepcopy(request)
    later['arguments']['prompt'] = 'unpaid native semantic comparison'
    data, call = append(state, later, 'semantic_claims_0_7f3826a1e389c1bf')
    assert run_guard(state.output, data, {'job_id': call['id']}, body(later, proxy)) == 'Infinity'


def test_second_repair_cannot_reuse_alias_parent(authorized):
    state, _, proof, proxy, _ = authorized
    request = proof['expected_request']
    _, parent = append(state, request)
    state.complete_call(parent, reply({'fixture': 'first alias received'}))
    repair = deepcopy(request)
    repair['arguments']['prompt'] += MARKER + '{"validation_error":"fixture"}'
    _, first = append(state, repair, ALIAS + '_repair', parent=parent)
    state.complete_call(first, reply({'fixture': 'only repair received'}))
    repair['arguments']['prompt'] += 'another repair is forbidden'
    data, current = append(state, repair, ALIAS + '_repair')
    data['calls'][-1]['repair_of'] = parent['id']
    assert run_guard(state.output, data, {'job_id': current['id']}, body(repair, proxy)) != 'Infinity'


@pytest.mark.parametrize('case', ['request', 'response'])
def test_received_alias_records_remain_bound_before_later_stages(authorized, case):
    state, _, proof, proxy, _ = authorized
    request = deepcopy(proof['expected_request'])
    _, accepted = append(state, request)
    state.complete_call(accepted, reply({'fixture': 'received interval correction'}))
    later = deepcopy(request)
    later['arguments']['prompt'] = 'native later claim check'
    data, current = append(state, later, 'semantic_claims_0_7f3826a1e389c1bf')
    folder = state.output / 'calls' / accepted['id']
    if case == 'request':
        request['arguments']['prompt'] += 'changed history'
        write_json(folder / 'request.json', request)
        data['calls'][29]['request_sha256'] = json_sha(request)
    else:
        write_json(folder / 'response.json', reply({'fixture': 'changed received reply'}))
    assert run_guard(state.output, data, {'job_id': current['id']}, body(later, proxy)) != 'Infinity'
