"""OpenCode adapter with simulated processes and original HTTP evidence only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import opencode_provider as provider
from omni_story.library.media import sha256_file
from omni_story.library.prompts import POLICY_VERSION
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


SECRET = 'synthetic-private-test-key'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    output = tmp_path / 'run'
    state = LibraryState(output, {'configuration': deepcopy(provider.PROVIDER)}, max_requests=None)
    package = tmp_path / 'official-package'
    index = package / 'node_modules/@z_ai/mcp-server/build/index.js'
    index.parent.mkdir(parents=True)
    index.write_text('// Not executed by these tests.', encoding='utf-8')
    media = tmp_path / 'fixture.mp4'
    media.write_bytes(b'local queue fixture only')
    monkeypatch.setenv('Z_AI_API_KEY', SECRET)
    monkeypatch.setattr(provider.shutil, 'which', lambda executable: '/fake/opencode')
    return state, package, media


def request(media, prompt='fixed prompt', *, scope=None):
    result = {'tool': 'analyze_video', 'arguments': {'video_source': str(media.resolve()), 'prompt': prompt},
              'media_sha256': sha256_file(media), 'provider': provider.PROVIDER['provider'],
              'policy_version': POLICY_VERSION}
    if scope is not None:
        result['observation_scope'] = scope
    return result


def reply(text):
    return {'status': 'complete', 'result': {'content': [{'type': 'text', 'text': text}]}}


def validator(value):
    if value.get('valid') is not True:
        raise ValueError('valid_boolean_required')


def journal(output, job_id, *, text=None, status=200, unknown=False, request_only=False):
    sequence = len((output / 'mcp_http.jsonl').read_text().splitlines()) + 1 if (output / 'mcp_http.jsonl').exists() else 1
    entries = [{'type': 'request', 'seq': sequence, 'job_id': job_id, 'hash': 'synthetic-body-hash'}]
    if not request_only:
        if unknown:
            entries.append({'type': 'unknown_result', 'seq': sequence, 'job_id': job_id,
                            'error': 'synthetic connection lost'})
        else:
            body = {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 11, 'completion_tokens': 3}} if status == 200 else {
                        'error': {'code': '1234', 'message': 'synthetic known HTTP failure'}}
            entries.append({'type': 'response', 'seq': sequence, 'job_id': job_id,
                            'status': status, 'body': json.dumps(body)})
    with (output / 'mcp_http.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(''.join(json.dumps(entry) + '\n' for entry in entries))


def fake_process(monkeypatch, responses):
    launches = []

    class Process:
        def __init__(self, command, **kwargs):
            launches.append({'command': command, **kwargs})
            self.kwargs = kwargs
            self.finished = False
            assert len(launches) <= len(responses), 'Unexpected additional OpenCode invocation'

        def wait(self, timeout=None):
            if not self.finished:
                self.finished = True
                responses[len(launches) - 1](self.kwargs)
            return 0

    monkeypatch.setattr(provider.subprocess, 'Popen', Process)
    return launches


def process_response(state, *, text='{"valid":true}', status=200, unknown=False,
                     request_only=False, queue_reply=None):
    def finish(kwargs):
        job_id = kwargs['env']['OMNI_LIBRARY_OPENCODE_JOB']
        # Agent summaries deliberately contradict the original vision reply.
        kwargs['stdout'].write((json.dumps({'type': 'text', 'text': '{"valid":false}',
                                           'synthetic_secret': SECRET}) + '\n').encode())
        journal(state.output, job_id, text=text, status=status, unknown=unknown, request_only=request_only)
        if queue_reply is not None:
            write_json(state.output / 'mcp_queue' / (job_id + '.response.json'), queue_reply)
    return finish


def parent_cache_case(setup, *, stage='fine_1234567812345678', repair=False, unknown=False):
    from omni_story.library.server_cli import parent_baseline
    parent, package, media = setup
    scope = dict(kind='continuous_window', source_sha256='a' * 64, source_start_s=1, source_end_s=4)
    write_json(media.parent / 'lineage.json', {**scope, 'spec': scope,
        'path': str(media.resolve()), 'sha256': sha256_file(media)})
    original, _ = parent.begin_call(stage, request(media))
    value = {'valid': True, 'evidence': 'original synthetic observation only'}
    selected = original
    if unknown:
        parent.fail_call(original, 'original result unknown', uncertain=True)
    else:
        parent.complete_call(original, reply(json.dumps({'valid': False} if repair else value)))
        if repair:
            text = 'fixed prompt\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'
            selected, _ = parent.begin_call(stage + '_repair', request(media, text), repair_of=original)
            parent.complete_call(selected, reply(json.dumps(value)))
        write_json(parent.output / 'calls' / selected['id'] / 'parsed.json', value)
    output = parent.output / 'evaluations/new'
    baseline = parent_baseline(parent.output, output)
    state = LibraryState(output, {'configuration': {**provider.PROVIDER,
        'workflow': 'reference_rough_skill_v1', 'parent_baseline': baseline,
        'prior_requests': baseline['prior_requests'] + baseline['request_count']}}, max_requests=None)
    relocated = output / 'media/fixture.mp4'; relocated.parent.mkdir(); relocated.write_bytes(media.read_bytes())
    write_json(relocated.parent / 'lineage.json', {**scope, 'spec': scope,
        'path': str(relocated.resolve()), 'sha256': sha256_file(relocated)})
    parent._reload()
    selected = next(call for call in parent.data['calls'] if call['id'] == selected['id'])
    return parent, state, package, relocated, value, selected, baseline


@pytest.mark.parametrize('stage', ['overview_12345678', 'zoom_1234567812345678',
    'fine_1234567812345678', 'coarse_zoom_search', 'search_0'])
def test_clean_parent_observations_reuse_only_bound_original_model_json_without_a_post(setup, monkeypatch, stage):
    parent, state, package, media, value, selected, baseline = parent_cache_case(setup, stage=stage)
    before = {str(path): path.read_bytes() for path in parent.output.rglob('*.json')
              if not path.is_relative_to(state.output)}
    launches = fake_process(monkeypatch, [])
    client = provider.OpenCodeMCP(state, package_root=package)
    assert client.call(stage, 'fixed prompt', media, validator) == value
    assert client.call(stage, 'fixed prompt', media, validator) == value
    assert launches == [] and state.usage()['requests'] == 0
    assert state.input_lock['configuration']['prior_requests'] == baseline['request_count']
    receipts = state.data['artifacts']['parent_observation_reuse']
    assert len(receipts) == 1
    receipt = json.loads(Path(receipts[0]['path']).read_text(encoding='utf-8'))
    assert receipt['source_task_id'] == parent.data['task_id']
    assert receipt['source_call_id'] == selected['id']
    assert receipt['source_request_sha256'] == selected['request_sha256']
    assert receipt['source_response_sha256'] == selected['response_sha256']
    assert receipt['new_vision_post'] is False
    assert {path: Path(path).read_bytes() for path in before} == before


def test_clean_parent_sole_repair_is_bound_to_the_matching_original_observation(setup, monkeypatch):
    parent, state, package, media, value, selected, _ = parent_cache_case(setup, repair=True)
    launches = fake_process(monkeypatch, [])
    assert provider.OpenCodeMCP(state, package_root=package).call(
        'fine_1234567812345678', 'fixed prompt', media, validator) == value
    receipt = json.loads(Path(state.data['artifacts']['parent_observation_reuse'][0]['path']).read_text())
    assert receipt['source_original_call_id'] == parent.data['calls'][0]['id']
    assert receipt['source_call_id'] == selected['id'] == parent.data['calls'][1]['id']
    assert receipt['matching_original_request_sha256'] == parent.data['calls'][0]['request_sha256']
    assert launches == [] and state.usage()['requests'] == 0


@pytest.mark.parametrize('mismatch', ['prompt', 'media', 'scope', 'validator', 'plan', 'review'])
def test_clean_parent_cache_does_not_accept_changed_inputs_contracts_or_plan_review(setup, monkeypatch, mismatch):
    stage = {'plan': 'plan_0', 'review': 'review_0'}.get(mismatch, 'fine_1234567812345678')
    _, state, package, media, _, _, _ = parent_cache_case(setup, stage=stage)
    prompt = 'changed prompt' if mismatch == 'prompt' else 'fixed prompt'
    if mismatch in {'media', 'scope'}:
        mapping = json.loads((media.parent / 'lineage.json').read_text())
        if mismatch == 'media':
            media.write_bytes(b'new observation bytes')
            mapping['sha256'] = sha256_file(media)
        else:
            mapping['source_start_s'] = mapping['spec']['source_start_s'] = 5
            mapping['source_end_s'] = mapping['spec']['source_end_s'] = 8
        write_json(media.parent / 'lineage.json', mapping)
    def current_validator(value):
        validator(value)
        if mismatch == 'validator' and value.get('new_evidence') is not True:
            raise ValueError('new_evidence_required')
    launches = fake_process(monkeypatch, [process_response(state, text='{"valid":true,"new_evidence":true}')])
    value = provider.OpenCodeMCP(state, package_root=package).call(stage, prompt, media, current_validator)
    assert value['new_evidence'] is True
    assert len(launches) == 1 and state.usage()['requests'] == 1
    assert not state.data['artifacts'].get('parent_observation_reuse')


@pytest.mark.parametrize('target', ['ledger', 'request', 'response', 'parsed'])
def test_clean_parent_cache_rejects_tampering_before_any_agent_or_post(setup, monkeypatch, target):
    parent, state, package, media, _, selected, _ = parent_cache_case(setup)
    path = parent.path if target == 'ledger' else parent.output / 'calls' / selected['id'] / (target + '.json')
    write_json(path, {'changed': True})
    launches = fake_process(monkeypatch, [])
    with pytest.raises(LibraryStopped, match='parent_observation_'):
        provider.OpenCodeMCP(state, package_root=package).call('fine_1234567812345678', 'fixed prompt', media, validator)
    assert launches == [] and state.usage()['requests'] == 0


def test_clean_parent_unknown_is_excluded_without_reusing_or_changing_its_record(setup, monkeypatch):
    parent, state, package, media, _, _, baseline = parent_cache_case(setup, unknown=True)
    before = parent.path.read_bytes()
    launches = fake_process(monkeypatch, [])
    client = provider.OpenCodeMCP(state, package_root=package)
    with pytest.raises(LibraryStopped, match='historical_unknown_observation_no_replay'):
        client.call('fine_1234567812345678', 'fixed prompt', media, validator)
    assert launches == [] and state.usage()['requests'] == 0
    assert parent.path.read_bytes() == before and parent.data['calls'][0]['status'] == 'uncertain'


def test_clean_storage_preflight_stops_before_registering_or_launching_a_paid_job(setup, monkeypatch):
    state, package, media = setup
    state.input_lock['configuration']['workflow'] = 'reference_rough_skill_v1'
    launches = fake_process(monkeypatch, [])
    monkeypatch.setattr(provider.shutil, 'disk_usage', lambda path: shutil._ntuple_diskusage(100, 100, 0))
    with pytest.raises(LibraryStopped, match='storage_preflight_insufficient'):
        provider.OpenCodeMCP(state, package_root=package).call('reference', 'fixed prompt', media, validator)
    assert launches == [] and state.usage()['requests'] == 0
    assert not list((state.output / 'mcp_queue').glob('*.request.json'))


@pytest.mark.parametrize('field', list(provider.PROVIDER))
def test_provider_configuration_is_locked_before_process_launch(setup, field):
    state, package, _ = setup
    state.input_lock['configuration'][field] = 'changed'
    with pytest.raises(LibraryStopped, match='input_lock_mismatch'):
        provider.OpenCodeMCP(state, package_root=package)
    assert state.usage()['requests'] == 0


def test_config_uses_only_coding_endpoint_and_environment_secret(setup):
    state, package, _ = setup
    config = provider.opencode_configuration(package, state.output)
    serialized = json.dumps(config)
    assert config['enabled_providers'] == ['zhipuai-coding-plan']
    options = config['provider']['zhipuai-coding-plan']['options']
    assert options == {'baseURL': 'https://open.bigmodel.cn/api/coding/paas/v4',
                       'apiKey': '{env:Z_AI_API_KEY}'}
    assert 'https://open.bigmodel.cn/api/paas/v4' not in serialized
    assert SECRET not in serialized
    assert config['permission'] == {'*': 'deny', 'omni_execute': 'allow'}
    assert config['agent']['vision-job']['permission'] == config['permission']
    assert config['mcp']['omni']['command'][0] == 'node'


def test_original_http_reply_wins_over_agent_summary_and_queue_rewrite(setup, monkeypatch):
    state, package, media = setup
    monkeypatch.setenv('OMNI_LIBRARY_EXTENSION_AUTH_FILE', 'must-not-be-inherited')
    launches = fake_process(monkeypatch, [process_response(state, queue_reply=reply('{"valid":false}'))])
    client = provider.OpenCodeMCP(state, package_root=package)
    assert client.call('reference', 'fixed prompt', media, validator) == {'valid': True}
    assert len(launches) == 1
    launch = launches[0]
    assert 'OMNI_LIBRARY_EXTENSION_AUTH_FILE' not in launch['env']
    assert launch['env']['Z_AI_API_KEY'] == SECRET
    assert launch['command'][1] == 'run'
    assert state.usage()['requests'] == 1
    assert state.usage()['prompt_tokens'] == 11
    call = state.data['calls'][0]
    saved = json.loads((state.output / 'calls' / call['id'] / 'response.json').read_text())
    assert saved['result']['content'][0]['text'] == '{"valid":true}'
    events = state.output / 'calls' / call['id'] / 'agent/events.jsonl'
    assert SECRET not in events.read_text()
    assert '[REDACTED]' in events.read_text()


@pytest.mark.parametrize('status', [401, 429, 500])
def test_known_http_failure_is_preserved_and_never_retried(setup, monkeypatch, status):
    state, package, media = setup
    launches = fake_process(monkeypatch, [process_response(state, status=status,
                                                         queue_reply={'status': 'error', 'error': 'known HTTP error'})])
    client = provider.OpenCodeMCP(state, package_root=package)
    with pytest.raises(LibraryStopped, match='failed_no_retry'):
        client.call('reference', 'fixed prompt', media, validator)
    assert state.data['calls'][0]['status'] == 'failed_known'
    with pytest.raises(LibraryStopped, match='not_received_no_replay'):
        client.call('reference', 'fixed prompt', media, validator)
    assert len(launches) == 1
    assert state.usage()['requests'] == 1


@pytest.mark.parametrize('request_only', [False, True])
def test_unknown_post_keeps_count_and_blocks_replay_after_restart(setup, monkeypatch, request_only):
    state, package, media = setup
    launches = fake_process(monkeypatch, [process_response(state, unknown=not request_only,
                                                         request_only=request_only)])
    with pytest.raises(LibraryStopped, match='failed_no_retry'):
        provider.OpenCodeMCP(state, package_root=package).call('reference', 'fixed prompt', media, validator)
    assert state.data['calls'][0]['status'] == 'uncertain'
    restarted = LibraryState(state.output, state.input_lock, max_requests=None)
    client = provider.OpenCodeMCP(restarted, package_root=package)
    with pytest.raises(LibraryStopped, match='not_received_no_replay'):
        client.call('reference', 'fixed prompt', media, validator)
    with pytest.raises(LibraryStopped, match='outcome_unknown'):
        client.call('search_0', 'changed prompt', media, validator)
    assert len(launches) == 1
    assert restarted.usage()['requests'] == 1


@pytest.mark.parametrize('failed_first', [False, True])
def test_captured_reply_is_recovered_without_launching_an_agent(setup, monkeypatch, failed_first):
    state, package, media = setup
    call, _ = state.begin_call('reference', request(media))
    if failed_first:
        state.fail_call(call, 'lost reply', uncertain=True)
    journal(state.output, call['id'], text='{"valid":true}')
    launches = fake_process(monkeypatch, [])
    client = provider.OpenCodeMCP(state, package_root=package)
    assert client.call('reference', 'fixed prompt', media, validator) == {'valid': True}
    assert state.data['calls'][0]['status'] == 'received'
    assert state.usage()['requests'] == 1
    assert launches == []
    assert not list((state.output / 'mcp_queue').glob('*.request.json'))


def test_received_response_cache_is_reused_and_tampering_is_rejected(setup, monkeypatch):
    state, package, media = setup
    launches = fake_process(monkeypatch, [process_response(state)])
    client = provider.OpenCodeMCP(state, package_root=package)
    assert client.call('reference', 'fixed prompt', media, validator) == {'valid': True}
    assert provider.OpenCodeMCP(state, package_root=package).call(
        'reference', 'fixed prompt', media, validator) == {'valid': True}
    assert len(launches) == 1
    call = state.data['calls'][0]
    write_json(state.output / 'calls' / call['id'] / 'response.json', reply('{"valid":false}'))
    with pytest.raises(LibraryStopped, match='request_or_reply_modified'):
        provider.OpenCodeMCP(state, package_root=package).call('reference', 'fixed prompt', media, validator)


@pytest.mark.parametrize('repair_succeeds', [False, True])
def test_format_repair_is_unique_and_resumes_from_both_original_replies(setup, monkeypatch, repair_succeeds):
    state, package, media = setup
    launches = fake_process(monkeypatch, [process_response(state, text='{"valid":false}'),
                                         process_response(state, text='{"valid":true}' if repair_succeeds else 'not JSON')])
    client = provider.OpenCodeMCP(state, package_root=package)
    if repair_succeeds:
        assert client.call('reference', 'fixed prompt', media, validator) == {'valid': True}
    else:
        with pytest.raises(ValueError, match='repair_exhausted'):
            client.call('reference', 'fixed prompt', media, validator)
    assert len(launches) == 2
    assert state.usage()['requests'] == 2
    assert state.data['calls'][1]['repair_of'] == state.data['calls'][0]['id']
    protected = {path: path.read_bytes() for path in (state.output / 'calls').glob('*/*.json')}
    restarted = provider.OpenCodeMCP(state, package_root=package)
    if repair_succeeds:
        assert restarted.call('reference', 'fixed prompt', media, validator) == {'valid': True}
    else:
        with pytest.raises(ValueError, match='repair_exhausted'):
            restarted.call('reference', 'fixed prompt', media, validator)
    assert len(launches) == 2
    assert state.usage()['requests'] == 2
    assert all(path.read_bytes() == content for path, content in protected.items())


@pytest.mark.parametrize('case', ['same_media', 'same_scope_new_encoding', 'different_scope'])
def test_baseline_unknown_exclusions_survive_provider_and_encoding_change(setup, monkeypatch, case):
    state, package, media = setup
    scope = {'kind': 'continuous_window', 'source_sha256': 'a' * 64,
             'source_start_s': 20.0, 'source_end_s': 21.0}
    exclusion = {'call_id': 'historical_unknown_131', 'media_sha256': sha256_file(media), 'scope': deepcopy(scope)}
    if case != 'same_media':
        exclusion['media_sha256'] = 'b' * 64
    if case == 'different_scope':
        exclusion['scope']['source_start_s'] = 19.0
    launches = fake_process(monkeypatch, [process_response(state)] if case == 'different_scope' else [])
    client = provider.OpenCodeMCP(state, package_root=package, exclusions=[exclusion])
    if case == 'different_scope':
        assert client.call('reference', 'fixed prompt', media, validator, scope=scope) == {'valid': True}
        assert len(launches) == 1
    else:
        with pytest.raises(LibraryStopped, match='historical_unknown_observation_no_replay'):
            client.call('reference', 'fixed prompt', media, validator, scope=scope)
        assert len(launches) == 0
        assert state.usage()['requests'] == 0


def test_launch_failure_records_known_failure_without_assuming_a_paid_reply(setup, monkeypatch):
    state, package, media = setup
    def fail(*args, **kwargs):
        raise OSError('synthetic launch failure')
    monkeypatch.setattr(provider.subprocess, 'Popen', fail)
    with pytest.raises(LibraryStopped, match='opencode_launch_failed'):
        provider.OpenCodeMCP(state, package_root=package).call('reference', 'fixed prompt', media, validator)
    assert state.data['calls'][0]['status'] == 'failed_known'
    assert state.usage()['requests'] == 1
    assert not (state.output / 'mcp_http.jsonl').exists()


def test_agent_summary_without_original_tool_reply_cannot_become_vision_evidence(setup, monkeypatch):
    state, package, media = setup
    def finish(kwargs):
        kwargs['stdout'].write(b'{"type":"text","text":"{\\"valid\\":true}"}\n')
    launches = fake_process(monkeypatch, [finish])
    with pytest.raises(LibraryStopped, match='failed_no_retry'):
        provider.OpenCodeMCP(state, package_root=package).call('reference', 'fixed prompt', media, validator)
    assert state.data['calls'][0]['status'] == 'failed_known'
    assert len(launches) == 1


def test_agent_timeout_with_a_submitted_post_preserves_unknown_outcome(setup, monkeypatch):
    state, package, media = setup
    events = []
    class Process:
        def __init__(self, command, **kwargs):
            events.append('launch')
            journal(state.output, kwargs['env']['OMNI_LIBRARY_OPENCODE_JOB'], request_only=True)

        def wait(self, timeout=None):
            if 'terminate' not in events:
                raise subprocess.TimeoutExpired('synthetic-opencode', timeout)
            events.append('reaped')
            return 0

        def terminate(self):
            events.append('terminate')

    monkeypatch.setattr(provider.subprocess, 'Popen', Process)
    with pytest.raises(LibraryStopped, match='failed_no_retry'):
        provider.OpenCodeMCP(state, package_root=package, timeout_s=1).call(
            'reference', 'fixed prompt', media, validator)
    assert events == ['launch', 'terminate', 'reaped']
    assert state.data['calls'][0]['status'] == 'uncertain'
    assert state.usage()['requests'] == 1
    call = state.data['calls'][0]
    exit_record = json.loads((state.output / 'calls' / call['id'] / 'agent/exit.json').read_text())
    assert exit_record['returncode'] == -1


@pytest.mark.parametrize('case', ['key', 'package', 'executable'])
def test_missing_runtime_requirements_fail_before_registering_a_model_attempt(setup, monkeypatch, case):
    state, package, _ = setup
    if case == 'key':
        monkeypatch.delenv('Z_AI_API_KEY')
    elif case == 'package':
        (package / 'node_modules/@z_ai/mcp-server/build/index.js').unlink()
    else:
        monkeypatch.setattr(provider.shutil, 'which', lambda executable: None)
    with pytest.raises(LibraryStopped, match={'key': 'key_missing', 'package': 'package_missing',
                                            'executable': 'executable_missing'}[case]):
        provider.OpenCodeMCP(state, package_root=package)
    assert state.usage()['requests'] == 0


def node(code, *args):
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Needs Node.js for the local diagnostic-log helper')
    completed = subprocess.run([executable, '--input-type=module', '-e', code, *map(str, args)],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
                              timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def test_official_startup_stderr_is_per_job_and_preserves_frozen_shared_log(tmp_path):
    root = tmp_path / 'original-task'
    root.mkdir()
    original = root / 'mcp_server.log'
    original.write_bytes(b'Frozen original official MCP startup log.\r\n')
    before = original.read_bytes()
    module = Path(provider.__file__).with_name('opencode_mcp.mjs')
    script = (
        "const m=await import(process.argv[1]);const root=process.argv[2];"
        "const a=m.appendOfficialStderr(root,'glm_036_chain_e2e_v1_rough_blind',"
        "'中文启动诊断🐼 synthetic-private-test-key\\n','synthetic-private-test-key');"
        "m.appendOfficialStderr(root,'glm_036_chain_e2e_v1_rough_blind','second startup line\\n');"
        "const b=m.appendOfficialStderr(root,'glm_037_chain_e2e_v1_rough_review','next job\\n');"
        "console.log(JSON.stringify({a,b}))")
    paths = json.loads(node(script, module.as_uri(), root))
    assert original.read_bytes() == before
    first = root / 'calls/glm_036_chain_e2e_v1_rough_blind/agent/mcp_server.log'
    second = root / 'calls/glm_037_chain_e2e_v1_rough_review/agent/mcp_server.log'
    assert Path(paths['a']) == first and Path(paths['b']) == second
    assert first.read_text(encoding='utf-8') == '中文启动诊断🐼 [REDACTED]\nsecond startup line\n'
    assert second.read_text(encoding='utf-8') == 'next job\n'
    assert not (root / 'mcp_http.jsonl').exists()
    assert not (root / 'mcp_queue').exists()


@pytest.mark.parametrize('case', ['real', 'normalized', 'missing', 'not_module'])
def test_official_mcp_entrypoint_detection_is_import_safe_and_uses_real_paths(tmp_path, case):
    module = Path(provider.__file__).with_name('opencode_mcp.mjs')
    candidates = {'real': str(module),
                  'normalized': str(module.parent / '..' / module.parent.name / module.name),
                  'missing': str(tmp_path / 'missing.mjs'), 'not_module': str(module.with_name('mcp_timeouts.mjs'))}
    script = ("const m=await import(process.argv[1]);console.log(m.isEntrypoint(process.argv[2]));"
              "if(m.isEntrypoint(undefined)) throw new Error('missing entry accepted')")
    assert node(script, module.as_uri(), candidates[case]) == ('true' if case in {'real', 'normalized'} else 'false')
    assert list(tmp_path.iterdir()) == []


def test_official_mcp_entrypoint_follows_deployment_symlink_without_starting_mcp(tmp_path):
    module = Path(provider.__file__).with_name('opencode_mcp.mjs')
    alias = tmp_path / 'current-opencode-mcp.mjs'
    try:
        alias.symlink_to(module)
    except OSError as error:
        pytest.skip('Creating a symlink is unavailable on this host: ' + str(error))
    script = "const m=await import(process.argv[1]);console.log(m.isEntrypoint(process.argv[2]))"
    assert node(script, module.as_uri(), alias) == 'true'
    assert list(tmp_path.iterdir()) == [alias]


@pytest.mark.parametrize('job_id', ['../mcp_server', 'glm_036_../old', 'reference', ''])
def test_official_stderr_rejects_unsafe_job_without_touching_history(tmp_path, job_id):
    module = Path(provider.__file__).with_name('opencode_mcp.mjs')
    original = tmp_path / 'mcp_server.log'
    original.write_bytes(b'old unchanged log')
    script = ("const m=await import(process.argv[1]);try{m.appendOfficialStderr(process.argv[2],"
              "process.argv[3],'forbidden append');console.log('unexpected')}catch(e){console.log(e.message)}")
    assert node(script, module.as_uri(), tmp_path, job_id) == 'bound_job_missing'
    assert original.read_bytes() == b'old unchanged log'
    assert not (tmp_path / 'calls').exists()


@pytest.mark.parametrize('status', ['error', 'complete'])
def test_nested_official_prepost_error_is_preserved_known_and_never_model_content(setup, monkeypatch, status):
    state, package, media = setup
    detail = ('Error: Unexpected error: Video analysis failed: Network error: '
              'server_chain_authorization_old_file_changed')
    def finish(kwargs):
        job_id = kwargs['env']['OMNI_LIBRARY_OPENCODE_JOB']
        write_json(state.output / 'mcp_queue' / (job_id + '.response.json'),
                   {'status': status, 'result': {'isError': True,
                    'content': [{'type': 'text', 'text': detail + ' ' + SECRET}]}})
    launches = fake_process(monkeypatch, [finish])
    client = provider.OpenCodeMCP(state, package_root=package)
    with pytest.raises(LibraryStopped, match='failed_no_retry'):
        client.call('reference', 'fixed prompt', media, validator)
    state._reload()
    call = state.data['calls'][0]
    assert call['status'] == 'failed_known'
    assert call['error'] == detail + ' [REDACTED]'
    assert not (state.output / 'calls' / call['id'] / 'response.json').exists()
    assert not (state.output / 'mcp_http.jsonl').exists()
    with pytest.raises(LibraryStopped, match='not_received_no_replay'):
        client.call('reference', 'fixed prompt', media, validator)
    assert state.usage()['requests'] == len(launches) == 1
    assert not any(row['name'].endswith('_repair') for row in state.data['calls'])


def test_official_error_detail_is_bounded_and_redacted_before_truncation(monkeypatch):
    monkeypatch.setenv('Z_AI_API_KEY', SECRET)
    message = 'prefix ' + SECRET + ' ' + 'x' * 6000
    detail = provider._diagnostic_error({'status': 'error', 'error': message})
    assert len(detail) == 4000 and SECRET not in detail and '[REDACTED]' in detail
