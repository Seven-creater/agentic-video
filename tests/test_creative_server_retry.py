"""Exercise known server retries with the actual guard and a local transport."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import opencode_provider, pipeline
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryState, LibraryStopped, write_json


CREATIVE = 'reference_rough_skill_v1'
LIBRARY = 'visual_library_v1'
PROVIDER = opencode_provider.PROVIDER['provider']
BLOCKED = 'library_mcp_retry_or_budget_blocked'
SUCCESS = dict(choices=[dict(finish_reason='stop', message=dict(content='{"accepted":true}'))],
               usage=dict(total_tokens=7))


def fixture(tmp_path, workflow=CREATIVE, provider=PROVIDER):
    package = tmp_path / 'package'
    undici = package / 'node_modules/undici'
    undici.mkdir(parents=True)
    (undici / 'package.json').write_text('{"type":"module"}')
    (undici / 'index.js').write_text('export class Agent {} export class ProxyAgent {}')
    root = tmp_path / 'task'
    root.mkdir()
    media = root / 'image.jpg'
    media.write_bytes(b'bound local image bytes')
    source = dict(path=str(media), sha256=sha256_file(media))
    config = dict(opencode_provider.PROVIDER, provider=provider, workflow=workflow,
                  vision_platform='ZHIPU')
    state = LibraryState(root, dict(configuration=config, video=source, reference=source),
                         max_requests=None if provider == PROVIDER else 80,
                         registry_path=tmp_path / 'registry.json')
    request = dict(provider=provider, policy_version=state.data['policy_version'],
        tool='analyze_image', media_sha256=source['sha256'],
        arguments=dict(image_source=str(media), prompt='Bound finecut review'))
    stage = {CREATIVE: 'chain_e2e_v1_fine_review', LIBRARY: 'fine_' + 'a' * 16,
             'visual_audio_v1': 'audio_observe'}.get(workflow, 'reference')
    call, _ = state.begin_call(stage, request)
    write_json(root / 'mcp_current.json', dict(job_id=call['id']))
    write_json(root / 'rough_handoff.json', dict(rough_video_path=str(media), rough_source_sha256=source['sha256']))
    write_json(root / 'result.json', dict(final_video=str(media), final_sha256=source['sha256']))
    body = dict(model=config['vision_model'], stream=False, messages=[dict(role='system', content='Fixture'),
        dict(role='user', content=[dict(type='image_url', image_url=dict(url='data:image/jpeg;base64,' +
            base64.b64encode(media.read_bytes()).decode())), dict(type='text', text=request['arguments']['prompt'])])])
    return state, package, body


def run_guard(state, package, body, outcomes, *, limit=80):
    if not shutil.which('node'):
        pytest.skip('Node needed for actual fetch guard')
    module = (Path(pipeline.__file__).parent / 'mcp_guard.mjs').as_uri()
    code = '''const outcomes=OUTCOMES, results=[], delays=[], timeline=[];let sends=0;
globalThis.fetch=async()=>{
  const outcome=outcomes[sends++];timeline.push('send');
  if(typeof outcome==='number')return new Response(JSON.stringify(outcome===200?SUCCESS:
    {error:{code:'1234',message:'Internal network failure, please try again later.'}}),{status:outcome});
  const error=new Error('fetch failed');
  if(outcome==='unreadable')return {status:500,clone:()=>({text:async()=>{throw error;}})};
  if(outcome!=='no_status')error.cause={code:outcome};
  throw error;
};
globalThis.setTimeout=(fn,ms)=>{delays.push(ms);timeline.push('wait');fn();};
await import(MODULE);
for(let i=0;i<outcomes.length;i++){
  try{const response=await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions',
    {method:'POST',body:JSON.stringify(BODY)});results.push(response.status);}
  catch(error){results.push(error.message);}
}
console.log(JSON.stringify({sends,results,delays,timeline}));'''
    for key, value in dict(OUTCOMES=outcomes, SUCCESS=SUCCESS, MODULE=module, BODY=body).items():
        code = code.replace(key, json.dumps(value))
    env = {key: value for key, value in os.environ.items() if not key.startswith('OMNI_LIBRARY_')}
    env.update(OMNI_LIBRARY_MCP_ROOT=str(state.output), OMNI_LIBRARY_MCP_PACKAGE_ROOT=str(package),
               OMNI_LIBRARY_NETWORK_RETRY_MAX='2', OMNI_LIBRARY_MAX_REQUESTS=str(limit))
    result = subprocess.run(['node', '--input-type=module', '-e', code],
                            capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip())


def journal(state):
    return [json.loads(line) for line in (state.output / 'mcp_http.jsonl').read_text().splitlines()]


@pytest.mark.parametrize('workflow', [CREATIVE, LIBRARY])
@pytest.mark.parametrize('status', [500, 502, 503, 504])
def test_known_server_error_waits_once_then_succeeds(tmp_path, workflow, status):
    state, package, body = fixture(tmp_path, workflow)
    value = run_guard(state, package, body, [status, 200, 200])
    assert value == dict(sends=2, results=[status, 200, BLOCKED], delays=[60000],
                         timeline=['send', 'wait', 'send'])
    events = journal(state)
    assert [row['type'] for row in events] == ['request', 'response', 'known_server_retry_permit', 'request', 'response']
    first, _, permit, second, _ = events
    assert first['hash'] == second['hash'] == permit['hash']
    assert first['body'] == second['body'] == body
    assert first['job_id'] == second['job_id'] == permit['job_id']
    assert (permit['attempt'], permit['of_max'], permit['previous_seq']) == (2, 1, first['seq'])
    state._reload()
    assert state.data['request_count'] == len(state.data['calls']) == 1


@pytest.mark.parametrize('workflow', [CREATIVE, LIBRARY])
def test_second_500_exhausts_retry_even_after_restart(tmp_path, workflow):
    state, package, body = fixture(tmp_path, workflow)
    value = run_guard(state, package, body, [500, 500, 200])
    assert value['sends'] == 2 and value['results'] == [500, 500, BLOCKED]
    assert value['delays'] == [60000]
    value = run_guard(state, package, body, [200])
    assert value['sends'] == 0 and value['results'] == [BLOCKED] and value['delays'] == []
    assert sum(row['type'] == 'known_server_retry_permit' for row in journal(state)) == 1


@pytest.mark.parametrize('workflow', [CREATIVE, LIBRARY])
def test_network_error_on_retry_never_allows_another_send(tmp_path, workflow):
    state, package, body = fixture(tmp_path, workflow)
    value = run_guard(state, package, body, [500, 'ECONNRESET', 200])
    assert value['sends'] == 2 and value['results'] == [500, 'fetch failed', BLOCKED]
    assert value['delays'] == [60000]
    assert not any(row['type'] == 'network_retry_permit' for row in journal(state))


@pytest.mark.parametrize('workflow', [CREATIVE, LIBRARY])
def test_known_error_does_not_permit_a_changed_body(tmp_path, workflow):
    state, package, body = fixture(tmp_path, workflow)
    assert run_guard(state, package, body, [500])['results'] == [500]
    body['messages'][0]['content'] = 'Changed native system prompt'
    value = run_guard(state, package, body, [200])
    assert value['sends'] == 0 and value['results'] == [BLOCKED] and value['delays'] == []


@pytest.mark.parametrize('workflow', [CREATIVE, LIBRARY])
@pytest.mark.parametrize('outcome', ['ECONNRESET', 'ETIMEDOUT', 'AbortError', 'no_status', 'unreadable'])
def test_unknown_outcome_never_retries(tmp_path, workflow, outcome):
    state, package, body = fixture(tmp_path, workflow)
    value = run_guard(state, package, body, [outcome, 200])
    assert value['sends'] == 1 and value['results'] == ['fetch failed', BLOCKED] and value['delays'] == []
    assert [row['type'] for row in journal(state)] == ['request', 'unknown_result']


@pytest.mark.parametrize('workflow', [CREATIVE, LIBRARY])
@pytest.mark.parametrize('status', [400, 401, 403, 408, 429, 501])
def test_other_http_errors_never_retry(tmp_path, workflow, status):
    state, package, body = fixture(tmp_path, workflow)
    value = run_guard(state, package, body, [status, 200])
    assert value['sends'] == 1 and value['results'] == [status, BLOCKED] and value['delays'] == []
    assert [row['type'] for row in journal(state)] == ['request', 'response']


@pytest.mark.parametrize('workflow,provider', [('visual_audio_v1', PROVIDER), ('legacy', PROVIDER),
    (CREATIVE + '_other', PROVIDER), (CREATIVE, 'official_vision_mcp_in_codex')])
def test_other_workflows_and_providers_keep_server_retries_blocked(tmp_path, workflow, provider):
    state, package, body = fixture(tmp_path, workflow, provider)
    value = run_guard(state, package, body, [500, 200])
    assert value['sends'] == 1 and value['results'] == [500, BLOCKED] and value['delays'] == []


def test_known_server_retry_survives_restart_without_resetting_budget(tmp_path):
    state, package, body = fixture(tmp_path, LIBRARY, 'official_vision_mcp_in_codex')
    assert run_guard(state, package, body, [500], limit=2)['results'] == [500]
    value = run_guard(state, package, body, [200], limit=1)
    assert value['sends'] == 0 and value['results'] == [BLOCKED] and value['delays'] == []
    value = run_guard(state, package, body, [200], limit=2)
    assert value['sends'] == 1 and value['results'] == [200] and value['delays'] == [60000]
    write_json(state.output / 'mcp_current.json', dict(job_id='new_job'))
    body['messages'][0]['content'] = 'Another request'
    value = run_guard(state, package, body, [200], limit=2)
    assert value['sends'] == 0 and value['results'] == [BLOCKED]
    assert sum(row['type'] == 'request' for row in journal(state)) == 2


def test_creative_retry_ledger_survives_restart(tmp_path):
    state, package, body = fixture(tmp_path)
    assert run_guard(state, package, body, [500])['results'] == [500]
    value = run_guard(state, package, body, [200, 200])
    assert value['sends'] == 1 and value['results'] == [200, BLOCKED] and value['delays'] == [60000]


def test_python_recovers_successful_retry_as_one_received_call(tmp_path):
    state, package, body = fixture(tmp_path)
    run_guard(state, package, body, [500, 200])
    call = state.data['calls'][0]
    entries = pipeline._http_evidence(state.output, call['id'])
    assert not pipeline._outcome_unknown(entries)
    mcp = opencode_provider.OpenCodeMCP.__new__(opencode_provider.OpenCodeMCP)
    mcp.state, mcp.output = state, state.output
    mcp.queue = state.output / 'mcp_queue'
    mcp.queue.mkdir()
    mcp.recover_received()
    received = state.data['calls'][0]
    assert received['status'] == 'received' and received['usage'] == SUCCESS['usage']
    assert state.data['request_count'] == len(state.data['calls']) == 1
    assert not (state.output / 'calls' / call['id'] / 'failure.json').exists()
    request = pipeline._read(state.output / 'calls' / call['id'] / 'request.json')
    reused, reply = mcp._submit(call['name'], request)
    assert reused == received and reply['status'] == 'complete'
    assert sum(row['type'] == 'request' for row in journal(state)) == 2


@pytest.mark.parametrize('statuses', [[500, 200], [500, 500]])
def test_python_submit_classifies_final_retry_result(tmp_path, monkeypatch, statuses):
    state, _, _ = fixture(tmp_path)
    first = state.data['calls'][0]
    state.complete_call(first, dict(status='complete', result=dict(content=[])))
    request = pipeline._read(state.output / 'calls' / first['id'] / 'request.json')
    request['arguments']['prompt'] = 'Next finecut review'
    mcp = opencode_provider.OpenCodeMCP.__new__(opencode_provider.OpenCodeMCP)
    mcp.state, mcp.output = state, state.output
    mcp.package_root, mcp.executable, mcp.timeout_s = tmp_path, 'local-fixture', 10
    mcp.parent_baseline, mcp.audio_new_work_exclusions, mcp.exclusions = None, None, []
    mcp.queue = state.output / 'mcp_queue'
    mcp.queue.mkdir()
    monkeypatch.setenv('Z_AI_API_KEY', 'local-fixture-no-api')

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self, **kwargs):
            call = state.data['calls'][-1]
            records = []
            for seq, status in enumerate(statuses, 1):
                records.extend([dict(type='request', seq=seq, job_id=call['id']),
                    dict(type='response', seq=seq, job_id=call['id'], status=status,
                         body=json.dumps(SUCCESS if status == 200 else dict(error='known HTTP 500')))])
            (state.output / 'mcp_http.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
            return 0

    monkeypatch.setattr(opencode_provider.subprocess, 'Popen', Process)
    if statuses[-1] == 200:
        _, reply = mcp._submit('chain_e2e_v1_fine_review_r', request)
        assert reply['status'] == 'complete'
        expected = 'received'
    else:
        with pytest.raises(LibraryStopped, match='opencode_vision_job_failed_no_retry:'):
            mcp._submit('chain_e2e_v1_fine_review_r', request)
        expected = 'failed_known'
    state._reload()
    assert state.data['calls'][-1]['status'] == expected
    assert state.data['request_count'] == len(state.data['calls']) == 2
