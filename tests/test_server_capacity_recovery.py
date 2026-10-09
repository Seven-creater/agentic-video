"""Known output-starvation recovery, using local records and stub transports only."""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import opencode_provider, pipeline, server_capacity_recovery as recovery, server_jobs
from omni_story.library.media import sha256_file
from omni_story.library.prompts import POLICY_VERSION
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


JS_LOADER = Path(recovery.__file__).with_suffix('.mjs')
JS_GUARD = Path(recovery.__file__).with_name('mcp_opencode_guard.mjs')
JS_FETCH_GUARD = Path(recovery.__file__).with_name('mcp_guard.mjs')
JS_TIMEOUTS = Path(recovery.__file__).with_name('mcp_timeouts.mjs')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def snapshot(output):
    return {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}


def unchanged(output, before, *, except_state=False):
    assert all((output / p).read_bytes() == raw for p, raw in before.items()
               if not except_state or p != Path('library_state.json'))


def response(text='', *, finish='length'):
    return {'status': 'complete', 'finish_reason': finish,
            'result': {'content': [{'type': 'text', 'text': text}]}}


def append_http(output, ident, sequence, text='', *, finish='length', tokens=16384):
    body = {'choices': [{'message': {'content': text}, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 31, 'completion_tokens': tokens,
                      'completion_tokens_details': {'reasoning_tokens': tokens}}}
    entries = [{'type': 'request', 'job_id': ident, 'seq': sequence,
                'hash': hashlib.sha256(ident.encode()).hexdigest()},
               {'type': 'response', 'job_id': ident, 'seq': sequence,
                'status': 200, 'body': json.dumps(body)}]
    with (output / 'mcp_http.jsonl').open('a', encoding='utf-8') as stream:
        for entry in entries:
            stream.write(json.dumps(entry) + '\n')


@pytest.fixture
def original(tmp_path, monkeypatch):
    output = tmp_path / 'original'
    config = {**opencode_provider.PROVIDER, 'max_rounds': 2, 'max_renders': 2, 'max_fine': 16}
    state = LibraryState(output, {'configuration': config}, max_requests=None)
    media = output / 'planning.png'
    media.write_bytes(b'synthetic contact sheet bytes, never sent to a model')
    scope = {'kind': 'sparse_contact_sheet', 'source_sha256': 'a' * 64,
             'source_start_s': 0.0, 'source_end_s': 1e-05}
    request = {'tool': 'analyze_image', 'arguments': {'image_source': str(media), 'prompt': '原始规划\n完整JSON'},
               'media_sha256': sha256_file(media), 'provider': opencode_provider.PROVIDER['provider'],
               'policy_version': POLICY_VERSION, 'observation_scope': scope}
    prior, _ = state.begin_call('search_0', {**request, 'arguments': {**request['arguments'], 'prompt': 'old search'}})
    state.complete_call(prior, response('{"old":true}', finish='stop'))
    write_json(output / 'calls' / prior['id'] / 'parsed.json', {'old': True})
    state.set_artifact('old_audit', {'evidence_limit': 'old report remains original'})
    parent = None
    for position, name in enumerate([recovery.STAGE, recovery.STAGE + '_repair'], 1):
        job_request = deepcopy(request)
        if parent:
            job_request['arguments']['prompt'] += '\nsole old format repair'
        call, _ = state.begin_call(name, job_request, repair_of=parent)
        state.complete_call(call, response(), usage={'completion_tokens': 16384})
        write_json(output / 'calls' / call['id'] / 'protocol_failure.json',
                   {'error': 'empty JSON', 'attempt': position - 1, 'model_text': ''})
        append_http(output, call['id'], position)
        parent = call
    state._reload()
    write_json(output / 'failure.json', {'error': 'model_protocol_repair_exhausted:plan_0'})
    write_json(output / '.omni-server/job.json',
               {'job_id': 'old-job-token', 'state': 'failed', 'exit_code': 1,
                'supervisor_pid': 11111, 'child_pid': 22222, 'command': ['old-worker']})
    (output / '.omni-server/run.lock').write_bytes(b'old-job-token\n')
    (output / '.omni-server/job.log').write_bytes(b'old known failure\n')
    write_json(output / 'catalog/inventory.json', {'sources': [{'sha256': 'a' * 64}]})
    write_json(output / 'reference_catalog/inventory.json', {'sources': [{'sha256': 'b' * 64}]})
    write_json(output / 'watched_windows.json', {'completed': ['window-one']})
    user_path = tmp_path / 'user_authorization.json'
    write_json(user_path, {'policy': 'server_edit_test_autonomous_engineering_fixes_v1',
                          'task_output': str(output), 'no_unknown_replay': True,
                          'no_automatic_new_round': True, 'candidate_limit_unchanged': 2})
    monkeypatch.setattr(server_jobs, '_identity', lambda pid: None)
    monkeypatch.setattr(server_jobs, '_group_running', lambda pid: False)
    return state, media, request, user_path


def register(original):
    state, _, _, user_path = original
    return recovery.register(state.output, user_path, sha256_file(user_path))


def node(code, *args, env=None):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node.js is required for local guard tests')
    result = subprocess.run([executable, '--input-type=module', '-e', code, *map(str, args)],
                            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def js_load(output):
    return node("const m=await import(process.argv[1]);try{const x=m.capacityRecovery(process.argv[2]);"
                "console.log(x ? JSON.stringify(x.authorization) : 'null')}catch(e){console.log(e.message)}",
                JS_LOADER.as_uri(), output)


def alias_call(state, request, *, name=recovery.ALIAS, repair_of=None):
    request = deepcopy(request)
    request['arguments']['prompt'] += recovery.SUFFIX
    call, _ = state.begin_call(name, request, repair_of=repair_of)
    return call, request


def native_body(request, media):
    return {'model': 'glm-5.3-flash', 'stream': False, 'max_tokens': 32768,
            'messages': [{'role': 'system', 'content': 'Synthetic official image system prompt'},
                         {'role': 'user', 'content': [
                             {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                              base64.b64encode(media.read_bytes()).decode()}},
                             {'type': 'text', 'text': request['arguments']['prompt']}]}]}


def js_guard(output, call, body):
    write_json(output / 'synthetic_payload.json', {'job': {'job_id': call['id']}, 'body': body})
    return node("import fs from 'node:fs';const m=await import(process.argv[1]);"
                "const root=process.argv[2],p=JSON.parse(fs.readFileSync(root+'/synthetic_payload.json'));"
                "try{console.log(String(m.opencodeRequestLimit(root,p.job,p.body)))}"
                "catch(e){console.log(e.message)}", JS_GUARD.as_uri(), output)


def test_registration_preserves_every_original_record_and_input_lock(original):
    state, _, _, _ = original
    before = snapshot(state.output)
    old = deepcopy(state.data)
    assert recovery.load(state.output) is None
    assert js_load(state.output) == 'null'
    path = register(original)
    auth = recovery.load(state.output)
    assert read(path) == auth == json.loads(js_load(state.output))
    assert Path(auth['baseline_state_path']).read_bytes() == before[Path('library_state.json')]
    state._reload()
    assert state.data['calls'] == old['calls']
    assert state.data['input_lock'] == old['input_lock']
    assert 'vision_generation' not in state.input_lock['configuration']
    assert state.usage()['requests'] == 3 and state.max_requests is None
    assert auth['generation']['max_output_tokens'] == 32768
    assert [p['usage']['completion_tokens'] for p in auth['HTTP_known_output_starvation']] == [16384, 16384]
    unchanged(state.output, before, except_state=True)
    registered = snapshot(state.output)
    with pytest.raises(LibraryStopped, match='already_registered'):
        register(original)
    unchanged(state.output, registered)


@pytest.mark.parametrize('case', ['submitted', 'uncertain', 'failed_known', 'wrong_failure',
                                 'render', 'result', 'live_supervisor', 'live_group',
                                 'not_length', 'not_empty', 'not_http200', 'wrong_tokens',
                                 'unmatched_seq', 'duplicate_post', 'wrong_user_digest',
                                 'wrong_repair_parent', 'original_is_repair'])
def test_registration_requires_stopped_known_empty_original_and_sole_repair(original, monkeypatch, case):
    state, _, _, user = original
    if case in {'submitted', 'uncertain', 'failed_known', 'wrong_repair_parent', 'original_is_repair'}:
        data = read(state.path)
        if case == 'wrong_repair_parent':
            data['calls'][-1]['repair_of'] = data['calls'][0]['id']
        elif case == 'original_is_repair':
            data['calls'][-2]['repair_of'] = data['calls'][0]['id']
        else:
            data['calls'][-1]['status'] = case
        write_json(state.path, data)
    elif case == 'wrong_failure':
        write_json(state.output / 'failure.json', {'error': 'different failure'})
    elif case in {'render', 'result'}:
        (state.output / ('render_0' if case == 'render' else 'result.json')).mkdir()
    elif case.startswith('live_'):
        monkeypatch.setattr(server_jobs, '_identity' if case == 'live_supervisor' else '_group_running',
                            lambda pid: True)
    elif case in {'not_length', 'not_empty'}:
        path = state.output / 'calls' / state.data['calls'][-1]['id'] / 'response.json'
        write_json(path, response('content' if case == 'not_empty' else '',
                                  finish='stop' if case == 'not_length' else 'length'))
    elif case != 'wrong_user_digest':
        path = state.output / 'mcp_http.jsonl'
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        if case == 'duplicate_post':
            entries.append(entries[-2])
        elif case == 'unmatched_seq':
            entries[-1]['seq'] += 1
        elif case == 'not_http200':
            entries[-1]['status'] = 500
        else:
            body = json.loads(entries[-1]['body'])
            body['usage']['completion_tokens'] = 32768
            entries[-1]['body'] = json.dumps(body)
        path.write_text(''.join(json.dumps(e) + '\n' for e in entries), encoding='utf-8')
    before = snapshot(state.output)
    with pytest.raises(LibraryStopped):
        recovery.register(state.output, user, '0' * 64 if case == 'wrong_user_digest' else sha256_file(user))
    unchanged(state.output, before)
    assert not (state.output / 'artifacts/server_output_capacity_baseline_v1.json').exists()


@pytest.mark.parametrize('case', ['authorization', 'baseline', 'call_prefix', 'count',
                                 'old_artifact', 'request', 'response', 'protocol_failure',
                                 'failure', 'job', 'run_lock', 'catalog', 'reference_catalog',
                                 'user_authorization', 'http_prefix'])
def test_python_and_js_reject_changed_baseline_before_new_work(original, case):
    state, _, _, user = original
    auth_path = register(original)
    auth = recovery.load(state.output)
    if case in {'call_prefix', 'count', 'old_artifact'}:
        data = read(state.path)
        if case == 'call_prefix':
            data['calls'][0]['usage']['completion_tokens'] = 999
        elif case == 'count':
            data['request_count'] += 1
        else:
            data['artifacts']['old_audit'][0]['sha256'] = '0' * 64
        write_json(state.path, data)
    else:
        path = {'authorization': auth_path, 'baseline': Path(auth['baseline_state_path']),
                'request': state.output / 'calls' / state.data['calls'][-1]['id'] / 'request.json',
                'response': state.output / 'calls' / state.data['calls'][-1]['id'] / 'response.json',
                'protocol_failure': state.output / 'calls' / state.data['calls'][-1]['id'] / 'protocol_failure.json',
                'failure': state.output / 'failure.json', 'job': state.output / '.omni-server/job.json',
                'run_lock': state.output / '.omni-server/run.lock',
                'catalog': state.output / 'catalog/inventory.json',
                'reference_catalog': state.output / 'reference_catalog/inventory.json',
                'user_authorization': user, 'http_prefix': state.output / 'mcp_http.jsonl'}[case]
        if case == 'authorization':
            value = read(path)
            value['policy'] = 'changed-policy'
            write_json(path, value)
        elif case == 'http_prefix':
            path.write_bytes(path.read_bytes().replace(b'16384', b'16385', 1))
        else:
            path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(LibraryStopped, match='server_capacity_recovery_'):
        recovery.load(state.output)
    assert js_load(state.output).startswith('server_capacity_recovery_')


def test_appended_http_audits_and_completed_windows_do_not_rewrite_frozen_prefix(original):
    state, _, _, _ = original
    register(original)
    auth = recovery.load(state.output)
    old_http = (state.output / 'mcp_http.jsonl').read_bytes()
    state.set_artifact('old_audit', {'new_appended_audit': True})
    write_json(state.output / 'watched_windows.json', {'completed': ['window-one', 'round-two-window']})
    append_http(state.output, 'glm_004_fine_0000000000000000', 3, '{"valid":true}', finish='stop', tokens=3)
    assert (state.output / 'mcp_http.jsonl').read_bytes()[:len(old_http)] == old_http
    assert recovery.load(state.output) == auth
    assert json.loads(js_load(state.output)) == auth


@pytest.mark.parametrize('names', [[recovery.ALIAS], [recovery.ALIAS, recovery.ALIAS + '_repair'],
                                 [recovery.ALIAS, recovery.ALIAS],
                                 [recovery.ALIAS + '_extra'],
                                 [recovery.ALIAS, recovery.ALIAS + '_repair', recovery.ALIAS + '_third']])
def test_alias_names_have_one_original_and_one_repair_only(original, names):
    state, _, _, _ = original
    register(original)
    data = read(state.path)
    for position, name in enumerate(names):
        data['calls'].append({'id': 'alias-' + str(position), 'name': name, 'status': 'received'})
    data['request_count'] = len(data['calls'])
    write_json(state.path, data)
    if names in [[recovery.ALIAS], [recovery.ALIAS, recovery.ALIAS + '_repair']]:
        assert recovery.load(state.output)
        assert json.loads(js_load(state.output))['alias'] == recovery.ALIAS
    else:
        with pytest.raises(LibraryStopped, match='alias_limit_changed'):
            recovery.load(state.output)
        assert js_load(state.output).endswith('alias_limit_changed')


@pytest.mark.parametrize('case', ['exact', 'changed_media', 'different_path', 'wrong_tool'])
def test_capacity_client_uses_original_prompt_scope_and_media_only(original, monkeypatch, case):
    state, media, request, _ = original
    register(original)
    calls = []
    monkeypatch.setattr(opencode_provider.OpenCodeMCP, 'call',
                        lambda self, *args, **kwargs: calls.append((args, kwargs)) or {'valid': True})
    client = object.__new__(recovery.CapacityMCP)
    client.output = state.output
    selected = media
    if case == 'changed_media':
        media.write_bytes(b'changed bytes')
    elif case == 'different_path':
        selected = media.with_name('different.png')
        selected.write_bytes(media.read_bytes())
    if case == 'exact':
        assert client.call('plan_0', 'new prompt must not replace original', selected,
                           lambda value: None, image=True, scope={'changed': True}) == {'valid': True}
        args, kwargs = calls[0]
        assert args[:3] == (recovery.ALIAS, request['arguments']['prompt'] + recovery.SUFFIX, media)
        assert kwargs == {'image': True, 'scope': request['observation_scope']}
    else:
        with pytest.raises(LibraryStopped, match='planning_media_changed'):
            client.call('plan_0', 'ignored', selected, lambda value: None, image=case != 'wrong_tool')
        assert not calls


@pytest.mark.parametrize('case', ['exact', 'prompt', 'scope', 'capacity', 'native_prompt', 'native_media',
                                 'unsupported_alias', 'later_round', 'original_paid_again'])
def test_js_guard_accepts_only_bound_alias_with_native_capacity(original, case):
    state, media, request, _ = original
    register(original)
    call, selected = alias_call(state, request)
    body = native_body(selected, media)
    if case in {'prompt', 'scope'}:
        selected['arguments']['prompt'] += 'extra' if case == 'prompt' else ''
        if case == 'scope':
            selected['observation_scope']['source_end_s'] = 9.0
        write_json(state.output / 'calls' / call['id'] / 'request.json', selected)
        state._reload()
        state.data['calls'][-1]['request_sha256'] = json_sha(selected)
        write_json(state.path, state.data)
        body = native_body(selected, media)
    elif case == 'capacity':
        body['max_tokens'] = 16384
    elif case == 'native_prompt':
        body['messages'][-1]['content'][1]['text'] += 'extra'
    elif case == 'native_media':
        body['messages'][-1]['content'][0]['image_url']['url'] = 'data:image/png;base64,Y2hhbmdlZA=='
    elif case in {'unsupported_alias', 'later_round', 'original_paid_again'}:
        state._reload()
        state.data['calls'][-1]['name'] = {'unsupported_alias': recovery.ALIAS + '_extra',
                                        'later_round': 'plan_2', 'original_paid_again': 'plan_0'}[case]
        write_json(state.path, state.data)
    result = js_guard(state.output, call, body)
    assert (result == 'Infinity') is (case == 'exact'), result
    if case == 'exact':
        profile = node("const m=await import(process.argv[1]);"
                       "console.log(JSON.stringify(m.connectionTimeouts(process.argv[2],{})))",
                       JS_TIMEOUTS.as_uri(), state.output)
        assert json.loads(profile) == {'modelTimeoutMs': 1200000, 'toolTimeoutMs': 1260000,
                                       'policy': 'opencode_vision_capacity_v2'}


@pytest.mark.parametrize('case', ['exact', 'changed_media', 'second_repair', 'missing_marker'])
def test_js_guard_sole_repair_is_bound_to_capacity_original(original, case):
    state, media, request, _ = original
    register(original)
    parent, selected = alias_call(state, request)
    state.complete_call(parent, response('invalid JSON', finish='stop'))
    repair_request = deepcopy(selected)
    repair_request['arguments']['prompt'] += (
        '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
        '{"validation_error":"synthetic"}')
    if case == 'changed_media':
        repair_request['observation_scope']['source_end_s'] = 2.0
    elif case == 'missing_marker':
        repair_request['arguments']['prompt'] = selected['arguments']['prompt'] + '\ncreative retry'
    call, _ = state.begin_call(recovery.ALIAS + '_repair', repair_request, repair_of=parent)
    if case == 'second_repair':
        state._reload()
        state.data['calls'].insert(-1, {**state.data['calls'][-1], 'id': 'earlier-repair', 'status': 'received'})
        state.data['request_count'] += 1
        write_json(state.path, state.data)
    result = js_guard(state.output, call, native_body(repair_request, media))
    assert (result == 'Infinity') is (case == 'exact'), result


def test_fetch_guard_never_resubmits_alias_even_after_guard_restart(original, tmp_path):
    state, media, request, _ = original
    register(original)
    call, selected = alias_call(state, request)
    write_json(state.output / 'mcp_current.json', {'job_id': call['id']})
    write_json(state.output / 'synthetic_native.json', native_body(selected, media))
    stub = tmp_path / 'package/node_modules/undici'
    stub.mkdir(parents=True)
    (stub / 'package.json').write_text('{"type":"module"}', encoding='utf-8')
    (stub / 'index.js').write_text('export class Agent {}', encoding='utf-8')
    env = {k: value for k, value in os.environ.items() if not k.startswith('OMNI_LIBRARY_')}
    env.update(OMNI_LIBRARY_MCP_ROOT=str(state.output), OMNI_LIBRARY_MCP_PACKAGE_ROOT=str(stub.parent.parent))
    code = """import fs from 'node:fs';let posts=0;
      globalThis.fetch=async()=>{posts++;return new Response('{"synthetic":true}',{status:200});};
      await import(process.argv[1]);
      const body=fs.readFileSync(process.argv[2]+'/synthetic_native.json','utf8');
      for(let i=0;i<2;i++){try{await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions',
        {method:'POST',body});console.log('accepted')}catch(e){console.log(e.message)}}
      console.log('transport_posts='+posts);
    """
    before = (state.output / 'mcp_http.jsonl').read_bytes()
    assert node(code, JS_FETCH_GUARD.as_uri(), state.output, env=env).splitlines() == [
        'accepted', 'library_mcp_retry_or_budget_blocked', 'transport_posts=1']
    after = (state.output / 'mcp_http.jsonl').read_bytes()
    assert after[:len(before)] == before
    assert node(code, JS_FETCH_GUARD.as_uri(), state.output, env=env).splitlines() == [
        'library_mcp_retry_or_budget_blocked', 'library_mcp_retry_or_budget_blocked', 'transport_posts=0']
    assert (state.output / 'mcp_http.jsonl').read_bytes() == after


def capacity_client(original, tmp_path, monkeypatch, answers):
    state, _, _, _ = original
    package = tmp_path / 'official-package'
    index = package / 'node_modules/@z_ai/mcp-server/build/index.js'
    index.parent.mkdir(parents=True)
    index.write_text('// Local synthetic fixture, never executed.', encoding='utf-8')
    monkeypatch.setenv('Z_AI_API_KEY', 'synthetic-no-network')
    monkeypatch.setattr(opencode_provider.shutil, 'which', lambda executable: '/fake/opencode')
    launches = []

    class Process:
        def __init__(self, command, **kwargs):
            self.kwargs = kwargs
            self.finished = False
            launches.append((command, kwargs))
            assert len(launches) <= len(answers), 'Unexpected additional OpenCode invocation'

        def wait(self, timeout=None):
            if not self.finished:
                self.finished = True
                job = self.kwargs['env']['OMNI_LIBRARY_OPENCODE_JOB']
                text = answers[len(launches) - 1]
                if text is None:
                    with (state.output / 'mcp_http.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps({'type': 'request', 'job_id': job, 'seq': 3,
                                                 'hash': 'synthetic-unknown'}) + '\n')
                else:
                    append_http(state.output, job, len(launches) + 2, text,
                                finish='stop' if text else 'length', tokens=3 if text else 32768)
            return 0

    monkeypatch.setattr(opencode_provider.subprocess, 'Popen', Process)
    return recovery.CapacityMCP(state, package_root=package), launches


def validator(value):
    if value.get('valid') is not True:
        raise ValueError('valid_boolean_required')


@pytest.mark.parametrize('answers', [['{"valid":true}'], ['', '{"valid":true}'], ['', '']])
def test_real_adapter_preserves_old_pair_and_uses_only_capacity_original_and_sole_repair(
        original, tmp_path, monkeypatch, answers):
    state, media, request, _ = original
    old = deepcopy(state.data)
    protected = snapshot(state.output)
    register(original)
    client, launches = capacity_client(original, tmp_path, monkeypatch, answers)
    if answers[-1]:
        assert client.call('plan_0', 'current prompt is deliberately different', media,
                           validator, image=True) == {'valid': True}
        assert client.call('plan_0', 'another later prompt', media, validator, image=True) == {'valid': True}
    else:
        for _ in range(2):
            with pytest.raises(ValueError, match='model_protocol_repair_exhausted:plan_0_capacity_v2'):
                client.call('plan_0', 'ignored', media, validator, image=True)
    state._reload()
    assert len(launches) == len(answers)
    assert state.data['calls'][:3] == old['calls']
    assert state.data['request_count'] == 3 + len(answers)
    aliases = state.data['calls'][3:]
    assert [call['name'] for call in aliases] == [recovery.ALIAS, recovery.ALIAS + '_repair'][:len(answers)]
    first = read(state.output / 'calls' / aliases[0]['id'] / 'request.json')
    assert first == {**request, 'arguments': {**request['arguments'],
                                            'prompt': request['arguments']['prompt'] + recovery.SUFFIX}}
    if len(answers) == 2:
        assert aliases[1]['repair_of'] == aliases[0]['id']
    for relative, raw in protected.items():
        if relative not in {Path('library_state.json'), Path('mcp_http.jsonl')}:
            assert (state.output / relative).read_bytes() == raw
    assert (state.output / 'mcp_http.jsonl').read_bytes().startswith(protected[Path('mcp_http.jsonl')])
    assert recovery.load(state.output)


def test_unknown_capacity_alias_preserves_count_and_cannot_be_replayed(original, tmp_path, monkeypatch):
    state, media, _, _ = original
    register(original)
    client, launches = capacity_client(original, tmp_path, monkeypatch, [None])
    with pytest.raises(LibraryStopped, match='opencode_vision_job_failed_no_retry'):
        client.call('plan_0', 'ignored', media, validator, image=True)
    state._reload()
    assert state.data['calls'][-1]['status'] == 'uncertain'
    before = state.data['request_count']
    restarted = recovery.CapacityMCP(state, package_root=client.package_root)
    with pytest.raises(LibraryStopped, match='recorded_request_not_received_no_replay'):
        restarted.call('plan_0', 'different prompt does not authorize replay', media, validator, image=True)
    assert len(launches) == 1 and state.usage()['requests'] == before == 4
    assert not any(c['name'] == recovery.ALIAS + '_repair' for c in state.data['calls'])


@pytest.mark.parametrize('filename', ['../failure.json', 'failure_capacity_recovery_v2.json', 'arbitrary.json'])
def test_unregistered_failure_filename_stops_before_creating_a_task(tmp_path, filename):
    output = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='unsupported_failure_report_name'):
        pipeline.execute(tmp_path / 'missing-reference', tmp_path / 'missing-library', output,
                         failure_report_name=filename)
    assert not output.exists()


def test_recovery_failure_report_preserves_original_failure_bytes(tmp_path, monkeypatch):
    media = tmp_path / 'synthetic.mp4'
    media.write_bytes(b'No video codec or network is used by this test.')
    source = {'source_id': 'synthetic', 'path': str(media), 'sha256': sha256_file(media),
              'duration_s': 2.0, 'audio_stream_index': None}
    monkeypatch.setattr(pipeline, '_catalog', lambda *args: {'sources': [source]})
    output = tmp_path / 'original'
    output.mkdir()
    original_failure = b'{"error":"old exhausted pair"}\n'
    (output / 'failure.json').write_bytes(original_failure)
    calls = []

    class Model:
        def call(self, *args, **kwargs):
            calls.append(args[0])
            raise RuntimeError('synthetic new capacity failure')

    with pytest.raises(RuntimeError, match='synthetic new capacity failure'):
        pipeline.execute(media, media, output, asr=False, model_factory=lambda state: Model(),
                         failure_report_name='failure_capacity_recovery_v1.json')
    assert calls == ['reference']
    assert (output / 'failure.json').read_bytes() == original_failure
    report = read(output / 'failure_capacity_recovery_v1.json')
    assert report['error'] == 'synthetic new capacity failure'
    assert report['no_automatic_paid_replay'] is True and report['usage']['requests'] == 0
