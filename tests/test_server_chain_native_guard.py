"""Native one-chain guard against recorded synthetic bytes; no model or network."""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import server_chain_authorization as chain
from omni_story.library import opencode_provider
from omni_story.library.prompts import POLICY_VERSION
from omni_story.library.state import LibraryState, json_sha, write_json
from test_server_chain_authorization import stopped_chain, original, rejected
from test_server_chain_authorization import register as register_real_proof, append_call as append_real_call

GUARD = Path(chain.__file__).with_name('mcp_opencode_guard.mjs')
LOADER = Path(chain.__file__).with_suffix('.mjs')
REPAIR_MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
PREFIX = 'chain_e2e_v1_'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def node(code, *args):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js is required for the native local guard')
    result = subprocess.run([executable, '--input-type=module', '-e', code, *map(str, args)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
                            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def response(value):
    return {'status': 'complete', 'finish_reason': 'stop',
            'result': {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}}


@pytest.fixture
def authorized(tmp_path):
    """Persist the same cross-language proof shape as the Python registrar.

    Movie bytes and the rough EDL are intentionally synthetic. Python's real
    register/contract/controller checks have their own tests; this fixture tests
    read-only native binding without running a codec or accessing credentials.
    """
    output = (tmp_path / 'task').resolve()
    configuration = {**opencode_provider.PROVIDER, 'max_rounds': 2, 'max_renders': 2, 'max_fine': 16,
                     'vision_generation': {'max_output_tokens': 32768}}
    state = LibraryState(output, {'configuration': configuration}, max_requests=None)
    movie = tmp_path / 'movie.mp4'
    movie.write_bytes(b'synthetic immutable movie bytes')
    reference = tmp_path / 'reference.mp4'
    reference.write_bytes(b'synthetic immutable reference bytes')
    sources = []
    for index, path in enumerate([movie, reference]):
        stat = path.stat()
        sources.append({'source_id': 'source_' + str(index), 'path': str(path.resolve()), 'sha256': digest(path),
                        'duration_s': 21.933333 if index else 300.0, 'video_stream_index': 0,
                        'audio_stream_index': None, 'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
    state.data['input_lock'].update(library_sources=[{'source_id': sources[0]['source_id'],
        'sha256': sources[0]['sha256']}], reference_sha256=sources[1]['sha256'])
    write_json(state.path, state.data)
    media = output / 'native-fixture.mp4'
    media.write_bytes(b'synthetic proxy bytes, never sent to a model')
    rough = {'fixture': 'unmodified paid GLM draft', 'duration_s': 147.0}
    old_request = {'provider': opencode_provider.PROVIDER['provider'], 'policy_version': POLICY_VERSION,
                   'tool': 'analyze_video', 'arguments': {'video_source': str(media), 'prompt': 'fixture'},
                   'media_sha256': digest(media), 'observation_scope': {'kind': 'continuous_window',
                   'source_sha256': 'a' * 64, 'source_start_s': 42.0, 'source_end_s': 49.0}}
    previous = None
    for index in range(35):
        name = ('plan_1' if index == 30 else 'plan_1_repair' if index == 31 else
                'semantic_slice_1_0123456789abcdef' if index == 33 else
                'semantic_slice_1_0123456789abcdef_repair' if index == 34 else f'overview_{index:08x}')
        request = deepcopy(old_request)
        request['arguments']['prompt'] = f'old immutable prompt {index}'
        call, _ = state.begin_call(name, request, repair_of=previous if index in {31, 34} else None)
        reply = response(rough if index == 31 else {'old': index})
        state.complete_call(call, reply)
        write_json(output / 'calls' / call['id'] / 'parsed.json', rough if index == 31 else {'old': index})
        previous = call
    write_json(output / 'catalog/inventory.json', {'sources': [sources[0]]})
    write_json(output / 'reference_catalog/inventory.json', {'sources': [sources[1]]})
    windows = [{'window_id': f'window_{index}'} for index in range(12)]
    write_json(output / 'watched_windows.json', windows)
    write_json(output / 'draft_plan_1.json', rough)
    write_json(output / 'old_failure.json', {'error': 'original known failure stays intact'})
    (output / 'mcp_http.jsonl').write_bytes(b'{"fixture":"old immutable HTTP bytes"}\n')
    state._reload()
    baseline = output / 'artifacts/server_one_chain_e2e_baseline_v1.json'
    protected = [{'path': str(path), 'sha256': digest(path)} for path in output.rglob('*')
                 if path.is_file() and path.name not in {'library_state.json', '.state.lock', 'mcp_http.jsonl'}]
    old_files = sorted(str(path) for path in (output / 'calls').rglob('*') if path.is_file())
    baseline.parent.mkdir(parents=True, exist_ok=True)
    baseline.write_bytes(state.path.read_bytes())
    paid = state.data['calls'][31]
    journal = (output / 'mcp_http.jsonl').read_bytes()
    proof = {'policy': chain.POLICY, 'recovery_name': chain.NAME, 'stage_prefix': PREFIX,
        'output': str(output), 'task_id': state.data['task_id'], 'input_lock': state.data['input_lock'],
        'user_instruction': '直接到服务器端到端测试一次；🐼保留原记录。', 'baseline_request_count': 35,
        'baseline_state_path': str(baseline), 'baseline_state_sha256': digest(baseline),
        'protected_files': protected, 'old_call_files': old_files, 'old_mutable_runtime_files': [],
        'http_prefix_bytes': len(journal), 'http_prefix_sha256': hashlib.sha256(journal).hexdigest(),
        'failed_controller_job_id': 'synthetic-dead-controller', 'render_grant': {'rough': 1, 'fine': 2, 'total': 3},
        'new_candidate_rounds': 0, 'max_fine_actual_revisions': 1, 'repairs_per_stage': 1,
        'no_unknown_replay': True, 'goal_resumed': False, 'old_wrapper_consumed': False,
        'execution_directory': str(output / 'artifacts' / chain.NAME),
        'rough_directory': str(output / 'artifacts' / chain.NAME / 'rough'),
        'fine_target_duration_s': sources[1]['duration_s'], 'fine_target_tolerance_s': 2.0,
        'source_integrity_policy': 'actual_SHA_at_registration_then_immutable_catalog_and_stat_on_load',
        'semantic_joint_policy': 'actual_rough_content_and_character_echo_then_picture_only_fine_review',
        'rough_edl_is_original_model_plan': True, 'old_152_second_refinement_is_not_fine_input': True,
        'original_failures_preserved': True, 'catalog': {'sources': [sources[0]]}, 'reference': sources[1],
        'windows': windows, 'source_files': [{key: source[key] for key in
                                           ['path', 'sha256', 'size_bytes', 'mtime_ns']} for source in sources],
        'rough_plan': rough, 'rough_plan_sha256': json_sha(rough), 'paid_rough_plan_call_id': paid['id'],
        'paid_rough_plan_request_sha256': paid['request_sha256'],
        'paid_rough_plan_response_sha256': paid['response_sha256']}
    proof_path = state.set_artifact(chain.KEY, proof)
    # New proxies are appended after the frozen history snapshot.
    proxy = output / 'new-proxy.mp4'
    proxy.write_bytes(b'new independently bound rough proxy bytes')
    return state, proof_path, proxy


def seal(state, proof_path, proof):
    write_json(proof_path, proof)
    state._reload()
    state.data['artifacts'][chain.KEY][0]['sha256'] = json_sha(proof)
    write_json(state.path, state.data)


def append(state, proxy, name, *, repair_of=None):
    if repair_of:
        request = read(state.output / 'calls' / repair_of['id'] / 'request.json')
        request['arguments']['prompt'] += REPAIR_MARKER + '{"validation_error":"fixture"}'
    else:
        request = {'provider': opencode_provider.PROVIDER['provider'], 'policy_version': POLICY_VERSION,
            'tool': 'analyze_video', 'arguments': {'video_source': str(proxy), 'prompt': '原样保留 ' + name},
            'media_sha256': digest(proxy), 'observation_scope': {'kind': 'complete_file',
                'source_sha256': 'b' * 64, 'source_start_s': 0.0, 'source_end_s': 147.0}}
    call, _ = state.begin_call(name, request, repair_of=repair_of)
    return call, request


def native_body(request, proxy):
    return {'model': 'glm-5.3-flash', 'stream': False, 'max_tokens': 32768,
            'messages': [{'role': 'user', 'content': [
                {'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,' +
                                                 base64.b64encode(proxy.read_bytes()).decode()}},
                {'type': 'text', 'text': request['arguments']['prompt']}]}]}


def settle(state, call, value):
    state.complete_call(call, response(value))
    write_json(state.output / 'calls' / call['id'] / 'parsed.json', value)


def run_guard(state, call, body):
    payload = state.output / 'native_payload.json'
    write_json(payload, {'job': {'job_id': call['id']}, 'body': body})
    return node("import fs from 'node:fs';const m=await import(process.argv[1]);"
                "const p=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));"
                "try{console.log(String(m.opencodeRequestLimit(process.argv[2],p.job,p.body)))}"
                "catch(e){console.log(e.message)}", GUARD.as_uri(), state.output, payload)


def test_native_guard_accepts_registered_chain_and_exact_large_nanosecond_stats(authorized):
    state, proof_path, proxy = authorized
    assert read(proof_path)['source_files'][0]['mtime_ns'] > 2 ** 53
    call, request = append(state, proxy, PREFIX + 'rough_blind')
    assert run_guard(state, call, native_body(request, proxy)) == 'Infinity'


def test_native_consumer_accepts_actual_python_registered_proof_and_accepted_handoff(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    path = register_real_proof(fixture)
    append_real_call(fixture, 'rough_blind')
    call, folder = append_real_call(fixture, 'rough_review', status='submitted')
    request = read(folder / 'request.json')
    proxy = Path(request['arguments']['video_source'])
    assert chain.load(state.output) == read(path)
    assert run_guard(state, call, native_body(request, proxy)) == 'Infinity'


def test_official_startup_logging_keeps_registered_history_guard_valid(stopped_chain):
    fixture, state = stopped_chain, stopped_chain['state']
    shared = state.output / 'mcp_server.log'
    original = b'Original shared MCP diagnostics remain immutable.\n'
    shared.write_bytes(original)
    proof_path = register_real_proof(fixture)
    proof = read(proof_path)
    assert any(row['path'] == str(shared) for row in proof['protected_files'])
    call, folder = append_real_call(fixture, 'rough_blind', status='submitted')
    module = GUARD.with_name('opencode_mcp.mjs')
    script = ('const m=await import(process.argv[1]);'
              "m.appendOfficialStderr(process.argv[2],process.argv[3],'New official startup diagnostic\\n');"
              "console.log('logged')")
    assert node(script, module.as_uri(), state.output, call['id']) == 'logged'
    assert shared.read_bytes() == original
    assert (folder / 'agent/mcp_server.log').exists()
    request = read(folder / 'request.json')
    assert chain.load(state.output) == proof
    assert run_guard(state, call, native_body(request, Path(request['arguments']['video_source']))) == 'Infinity'


@pytest.mark.parametrize('case', ['missing', 'modified'])
def test_predecessor_requires_accepted_recorded_model_json(authorized, case):
    state, _, proxy = authorized
    parent, _ = append(state, proxy, PREFIX + 'rough_blind')
    settle(state, parent, {'known': 'picture-only rough evidence'})
    parsed = state.output / 'calls' / parent['id'] / 'parsed.json'
    if case == 'missing':
        parsed.unlink()
    else:
        write_json(parsed, {'known': 'invented replacement narrative'})
    call, request = append(state, proxy, PREFIX + 'rough_review')
    assert run_guard(state, call, native_body(request, proxy)) != 'Infinity'


def test_node_helper_decodes_non_ascii_under_non_utf8_platform_default(monkeypatch):
    monkeypatch.setattr(subprocess, '_text_encoding', lambda: 'cp1252')
    assert json.loads(node('console.log(JSON.stringify({value:"中文🐼"}))')) == {'value': '中文🐼'}


@pytest.mark.parametrize('stage', ['fine_inspect_4', 'fine_detail_0_6', 'fine_detail_4_0', 'fine_blind_r2',
                                  'fine_review_r_r', 'fine_plan_2', 'fine_anything', 'rough_plan', 'rough_revise'])
def test_stage_allowlist_has_no_arbitrary_prefix_expansion(stage):
    assert node("const m=await import(process.argv[1]);console.log(m.chainStage(process.argv[2]))",
                LOADER.as_uri(), PREFIX + stage) == 'false'


@pytest.mark.parametrize('case', ['no_proof', 'legacy_stage', 'scope', 'grant', 'reference_target', 'input_lock',
                                 'baseline', 'old_call', 'old_file', 'old_HTTP', 'extra_old_call_file',
                                 'source_size', 'source_mtime', 'duplicate_proof', 'wrapper_consumed',
                                 'terminal_media_replay', 'terminal_scope_replay', 'native_prompt', 'native_media',
                                 'native_capacity', 'missing_dependency'])
def test_unbound_or_modified_chain_stops_before_native_post(authorized, case):
    state, proof_path, proxy = authorized
    proof = read(proof_path)
    stage = PREFIX + 'rough_blind'
    if case == 'legacy_stage':
        stage = 'review_1'
    elif case == 'missing_dependency':
        stage = PREFIX + 'fine_blind_r'
    call, request = append(state, proxy, stage)
    body = native_body(request, proxy)
    state._reload()
    if case == 'no_proof':
        state.data['artifacts'].pop(chain.KEY)
    elif case == 'scope':
        proof['new_candidate_rounds'] = 1
    elif case == 'grant':
        proof['render_grant']['fine'] = 3
    elif case == 'reference_target':
        proof['fine_target_duration_s'] = 150.0
    elif case == 'input_lock':
        state.data['input_lock']['configuration']['max_rounds'] = 3
    elif case == 'baseline':
        Path(proof['baseline_state_path']).write_bytes(b'{}')
    elif case == 'old_call':
        state.data['calls'][0]['name'] = 'changed_old_stage'
    elif case == 'old_file':
        (state.output / 'old_failure.json').write_bytes(b'changed preserved failure')
    elif case == 'old_HTTP':
        (state.output / 'mcp_http.jsonl').write_bytes(b'changed old journal')
    elif case == 'extra_old_call_file':
        folder = state.output / 'calls' / state.data['calls'][0]['id']
        (folder / 'added_parsed.json').write_bytes(b'{}')
    elif case in {'source_size', 'source_mtime'}:
        source = Path(proof['source_files'][0]['path'])
        stat = source.stat()
        if case == 'source_size':
            source.write_bytes(b'changed size')
        else:
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 100_000))
    elif case == 'duplicate_proof':
        state.data['artifacts'][chain.KEY].append(state.data['artifacts'][chain.KEY][0])
    elif case == 'wrapper_consumed':
        state.data['artifacts'][chain.WRAPPER_KEY] = [{'path': 'unused', 'sha256': 'a' * 64}]
    elif case in {'terminal_media_replay', 'terminal_scope_replay'}:
        terminal = read(state.output / 'calls' / state.data['calls'][34]['id'] / 'request.json')
        if case == 'terminal_media_replay':
            request['media_sha256'] = terminal['media_sha256']
        else:
            request['observation_scope'] = terminal['observation_scope']
        call['request_sha256'] = json_sha(request)
        state.data['calls'][-1] = call
        write_json(state.output / 'calls' / call['id'] / 'request.json', request)
    elif case == 'native_prompt':
        body['messages'][0]['content'][1]['text'] += ' agent modified this'
    elif case == 'native_media':
        body['messages'][0]['content'][0]['video_url']['url'] = 'data:video/mp4;base64,Y2hhbmdlZA=='
    elif case == 'native_capacity':
        body['max_tokens'] = 16384
    write_json(state.path, state.data)
    if case in {'scope', 'grant', 'reference_target'}:
        seal(state, proof_path, proof)
    assert run_guard(state, call, body) != 'Infinity'


@pytest.mark.parametrize('case', ['valid', 'second_repair', 'changed_scope', 'changed_prompt', 'old_unknown'])
def test_one_chain_format_repair_is_same_input_and_only_once(authorized, case):
    state, _, proxy = authorized
    original, _ = append(state, proxy, PREFIX + 'rough_blind')
    state.complete_call(original, response({'invalid': True}))
    repair, request = append(state, proxy, original['name'] + '_repair', repair_of=original)
    body = native_body(request, proxy)
    state._reload()
    if case == 'second_repair':
        state.data['calls'].insert(-1, {**repair, 'id': 'synthetic_first_repair', 'status': 'received'})
        state.data['request_count'] += 1
    elif case in {'changed_scope', 'changed_prompt'}:
        if case == 'changed_scope':
            request['observation_scope']['source_end_s'] = 146.0
        else:
            request['arguments']['prompt'] = 'fresh creative request disguised as repair'
            body = native_body(request, proxy)
        repair['request_sha256'] = json_sha(request)
        state.data['calls'][-1] = repair
        write_json(state.output / 'calls' / repair['id'] / 'request.json', request)
    elif case == 'old_unknown':
        state.data['calls'][0]['status'] = 'uncertain'
    write_json(state.path, state.data)
    result = run_guard(state, repair, body)
    assert (result == 'Infinity') is (case == 'valid'), result


def test_all_finite_stages_with_sole_repairs_cross_80_without_enabling_another_round(authorized):
    state, _, proxy = authorized
    stages = ['rough_blind', 'rough_review', 'fine_observe']
    for index in range(4):
        stages.append(f'fine_inspect_{index}')
        stages.extend(f'fine_detail_{index}_{page}' for page in range(6))
    stages += ['fine_plan', 'fine_blind', 'fine_review', 'fine_revise', 'fine_blind_r', 'fine_review_r']
    for stage in stages:
        call, _ = append(state, proxy, PREFIX + stage)
        settle(state, call, {'known': stage})
        repair, request = append(state, proxy, call['name'] + '_repair', repair_of=call)
        if stage != stages[-1]:
            settle(state, repair, {'known_repair': stage})
    assert state.usage()['requests'] == 109
    assert run_guard(state, repair, native_body(request, proxy)) == 'Infinity'
    settle(state, repair, {'known_repair': stages[-1]})
    later, request = append(state, proxy, PREFIX + 'fine_plan_2')
    assert run_guard(state, later, native_body(request, proxy)) != 'Infinity'
