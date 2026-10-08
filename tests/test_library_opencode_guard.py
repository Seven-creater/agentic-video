"""New OpenCode guard against local records only; no MCP process or model calls."""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import omni_story.library as library_package
from omni_story.library.prompts import POLICY_VERSION
from omni_story.library.state import LibraryState, json_sha, write_json


GUARD = Path(library_package.__file__).resolve().parent / 'mcp_opencode_guard.mjs'
PROVIDER = 'official_vision_mcp_in_opencode'
CONFIG = {'provider': PROVIDER, 'transport': 'opencode_cli_official_mcp_v1',
          'progress_policy': 'finite_stages_no_retry_v1', 'vision_model': 'glm-5.3-flash',
          'agent_model': 'zhipuai-coding-plan/glm-5.3-flash'}
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Needs Node.js')


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, image=False, stage='reference', prior=0):
    root = tmp_path / 'task'
    state = LibraryState(root, {'configuration': CONFIG}, max_requests=None)
    media = root / ('fixture.png' if image else 'fixture.mp4')
    media.write_bytes(b'local guard byte fixture only')
    for index in range(prior):
        # Actual finite pipeline names: two rounds, up to 32 slice observations
        # and 32 claim comparisons each. This crosses the legacy 80-call ceiling.
        round_no, position = divmod(index, 64)
        category = 'slice' if position < 32 else 'claims'
        name = f'semantic_{category}_{round_no}_{position % 32:016x}'
        old, _ = state.begin_call(name, {'synthetic_received_history': index})
        state.complete_call(old, reply({'record': index}))
    argument = 'image_source' if image else 'video_source'
    request = {'tool': 'analyze_image' if image else 'analyze_video',
               'arguments': {argument: str(media.resolve()), 'prompt': '原样保留\nJSON与画面'},
               'media_sha256': file_sha(media), 'provider': PROVIDER, 'policy_version': POLICY_VERSION,
               # Float spellings test Python json_sha vs JS JSON.stringify.
               'observation_scope': {'kind': 'continuous_window', 'source_sha256': 'a' * 64,
                                     'source_start_s': 0.0, 'source_end_s': 1e-05}}
    call, _ = state.begin_call(stage, request)
    state._reload()
    data = deepcopy(state.data)
    type_ = 'image_url' if image else 'video_url'
    mime = 'image/png' if image else 'video/mp4'
    body = {'model': CONFIG['vision_model'], 'stream': False, 'messages': [{
        'role': 'user', 'content': [
            {'type': type_, type_: {'url': f'data:{mime};base64,' + base64.b64encode(media.read_bytes()).decode()}},
            {'type': 'text', 'text': request['arguments']['prompt']}]}]}
    if image:
        body['messages'].insert(0, {'role': 'system', 'content': 'Official image analysis system prompt fixture.'})
    return root, media, data, request, {'job_id': call['id']}, body


def reply(value):
    return {'status': 'complete', 'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}


def run_guard(root, state, job, body):
    write_json(root / 'library_state.json', state)
    write_json(root / 'test_payload.json', {'job': job, 'body': body})
    script = """
import fs from 'node:fs';
const {opencodeRequestLimit} = await import(process.argv[1]);
const root = process.argv[2];
const {job, body} = JSON.parse(fs.readFileSync(root + '/test_payload.json', 'utf8'));
try {
  const value = opencodeRequestLimit(root, job, body);
  console.log(value === Infinity ? 'Infinity' : String(value));
} catch (error) { console.log(error.message); }
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script, GUARD.as_uri(), str(root)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize('image,prior', [(False, 0), (True, 0), (False, 81)])
def test_explicit_new_lane_accepts_exact_official_body_with_no_numeric_ceiling(tmp_path, image, prior):
    root, _, state, _, job, body = fixture(tmp_path, image=image, prior=prior)
    assert state['request_count'] == prior + 1
    assert state['max_requests'] is None
    assert run_guard(root, state, job, body) == 'Infinity'


@pytest.mark.parametrize('provider', ['official_vision_mcp_in_codex', 'other_provider', None])
def test_existing_provider_is_not_granted_opencode_permission(tmp_path, provider):
    root, _, state, _, job, body = fixture(tmp_path)
    state['input_lock']['configuration']['provider'] = provider
    state['max_requests'] = 80
    assert run_guard(root, state, job, body) == 'null'


@pytest.mark.parametrize('case', [
    'policy', 'transport', 'vision_model', 'agent_model', 'numeric_limit', 'call_count',
    'current_job', 'previous_submitted', 'previous_unknown', 'previous_known_failure',
    'duplicate_id', 'duplicate_stage', 'unsupported_stage', 'later_round', 'repair_without_parent',
    'original_has_repair_parent', 'request_digest', 'request_provider', 'request_tool', 'request_policy',
    'local_media', 'remote_source', 'extra_argument', 'empty_prompt', 'native_model', 'native_stream',
    'native_prompt', 'native_extra_text', 'native_media', 'native_remote_media', 'native_base64',
    'native_type', 'native_extra_message', 'native_system_in_video',
])
def test_modified_or_unsettled_work_stops_before_a_post(tmp_path, case):
    root, media, state, request, job, body = fixture(tmp_path)
    call = state['calls'][-1]
    config = state['input_lock']['configuration']
    if case in {'policy', 'transport', 'vision_model', 'agent_model'}:
        field = 'progress_policy' if case == 'policy' else case
        config[field] = 'changed'
    elif case == 'numeric_limit':
        state['max_requests'] = 80
    elif case == 'call_count':
        state['request_count'] += 1
    elif case == 'current_job':
        job['job_id'] = 'different'
    elif case.startswith('previous_'):
        status = {'previous_submitted': 'submitted', 'previous_unknown': 'uncertain',
                  'previous_known_failure': 'failed_known'}[case]
        state['calls'].insert(0, {'id': 'prior', 'name': 'search_0', 'status': status})
        state['request_count'] += 1
    elif case in {'duplicate_id', 'duplicate_stage'}:
        previous = {**call, 'status': 'received', 'id': call['id'] if case == 'duplicate_id' else 'prior'}
        state['calls'].insert(0, previous)
        state['request_count'] += 1
    elif case in {'unsupported_stage', 'later_round', 'repair_without_parent'}:
        call['name'] = {'unsupported_stage': 'active_11_draft', 'later_round': 'plan_2',
                        'repair_without_parent': 'reference_repair'}[case]
    elif case == 'original_has_repair_parent':
        call['repair_of'] = 'not_an_original'
    elif case.startswith('request_'):
        field = {'request_provider': 'provider', 'request_tool': 'tool', 'request_policy': 'policy_version'}.get(case)
        if field:
            request[field] = 'changed'
            call['request_sha256'] = json_sha(request)
        else:
            request['arguments']['prompt'] += 'changed'
    elif case == 'local_media':
        media.write_bytes(b'changed media')
    elif case in {'remote_source', 'extra_argument', 'empty_prompt'}:
        if case == 'remote_source':
            request['arguments']['video_source'] = 'https://example.invalid/fixture.mp4'
        elif case == 'extra_argument':
            request['arguments']['other'] = 'changed'
        else:
            request['arguments']['prompt'] = ' '
        call['request_sha256'] = json_sha(request)
    elif case == 'native_model':
        body['model'] = 'changed'
    elif case == 'native_stream':
        body['stream'] = True
    elif case == 'native_prompt':
        body['messages'][0]['content'][1]['text'] += '\nAgent-added instruction'
    elif case == 'native_extra_text':
        body['messages'][0]['content'].append({'type': 'text', 'text': 'added'})
    elif case in {'native_media', 'native_remote_media', 'native_base64'}:
        body['messages'][0]['content'][0]['video_url']['url'] = {
            'native_media': 'data:video/mp4;base64,Y2hhbmdlZA==',
            'native_remote_media': 'https://example.invalid/fixture.mp4',
            'native_base64': 'data:video/mp4;base64,YR=='}[case]
    elif case == 'native_type':
        body['messages'][0]['content'][0]['type'] = 'image_url'
    elif case in {'native_extra_message', 'native_system_in_video'}:
        body['messages'].insert(0, {'role': 'system' if case == 'native_system_in_video' else 'assistant',
                                    'content': 'added'})
    write_json(root / 'calls' / call['id'] / 'request.json', request)
    assert run_guard(root, state, job, body).startswith('library_mcp_opencode_')


@pytest.mark.parametrize('case', ['valid', 'second_repair', 'parent_not_received', 'parent_is_repair',
                                 'changed_media', 'changed_scope', 'changed_parent_request',
                                 'changed_parent_response', 'missing_repair_marker'])
def test_format_repair_is_bound_to_one_received_original(tmp_path, case):
    root, _, state, request, _, body = fixture(tmp_path)
    parent = state['calls'][-1]
    parent['status'] = 'received'
    response = reply({'invalid': True})
    parent['response_sha256'] = json_sha(response)
    write_json(root / 'calls' / parent['id'] / 'response.json', response)
    repair_request = deepcopy(request)
    repair_request['arguments']['prompt'] += (
        '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
        '{"validation_error":"fixture","previous_response":"fixture"}')
    repair = {'id': 'glm_002_reference_repair', 'name': 'reference_repair', 'status': 'submitted',
              'repair_of': parent['id'], 'request_sha256': json_sha(repair_request)}
    state['calls'].append(repair)
    state['request_count'] = 2
    if case == 'second_repair':
        state['calls'].insert(1, {**repair, 'id': 'first_repair', 'status': 'received'})
        state['request_count'] += 1
    elif case == 'parent_not_received':
        parent['status'] = 'uncertain'
    elif case == 'parent_is_repair':
        parent['repair_of'] = 'older'
    elif case == 'changed_media':
        repair_request['media_sha256'] = 'b' * 64
    elif case == 'changed_scope':
        repair_request['observation_scope']['source_end_s'] = 2.0
    elif case == 'changed_parent_request':
        request['arguments']['prompt'] = 'altered original'
        write_json(root / 'calls' / parent['id'] / 'request.json', request)
    elif case == 'changed_parent_response':
        write_json(root / 'calls' / parent['id'] / 'response.json', reply({'altered': True}))
    elif case == 'missing_repair_marker':
        repair_request['arguments']['prompt'] = request['arguments']['prompt'] + '\ncreative retry'
    repair['request_sha256'] = json_sha(repair_request)
    write_json(root / 'calls' / repair['id'] / 'request.json', repair_request)
    body['messages'][0]['content'][1]['text'] = repair_request['arguments']['prompt']
    output = run_guard(root, state, {'job_id': repair['id']}, body)
    assert (output == 'Infinity') is (case == 'valid'), output
