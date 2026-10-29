"""Real unknown-skim evidence through the Python ledger and actual JS fetch guard."""
from copy import deepcopy
import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from omni_story.library import contracts, opencode_provider, question_first as qfs
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json
from test_creative_server_retry import run_guard


FIXTURES = Path(__file__).parent / 'fixtures/e2e_qfs_unknown_20261030'
BLOCKED = 'library_mcp_opencode_current_job_or_previous_outcome_invalid'
REPAIR = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'


def evidence(name):
    return qfs.read_json(FIXTURES / (name + '.json'))


def unknown_skim(tmp_path, monkeypatch):
    config = dict(opencode_provider.PROVIDER, workflow='reference_rough_skill_v1', vision_platform='ZHIPU')
    state = LibraryState(tmp_path / 'task', dict(configuration=config), max_requests=None,
                         registry_path=tmp_path / 'registry.json')
    package = tmp_path / 'package'
    undici = package / 'node_modules/undici'
    undici.mkdir(parents=True)
    (undici / 'package.json').write_text('{"type":"module"}')
    (undici / 'index.js').write_text('export class Agent {} export class ProxyAgent {}')
    lost = evidence('unknown_calls')[0]
    original = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(qfs, 'http_post', original)
    direct = qfs.DirectTransport(state, 'local-fixture-key')
    job = dict(stage='skim', prompt='local synthetic contact sheet request',
               sheet=dict(tiles=[]), beat_ids=['b1'])
    with pytest.raises(qfs.UnknownQFSCall) as caught:
        direct.call(lost['name'], job)
    call = state.data['calls'][0]
    assert str(caught.value) == lost['error'].rsplit(':', 1)[0] + ':' + call['id']
    assert call['status'] == 'uncertain'
    original.assert_called_once()
    no_resend = Mock(side_effect=AssertionError('unknown request was resent'))
    monkeypatch.setattr(qfs, 'http_post', no_resend)
    return state, package, direct, job, no_resend


def submit(state, name, prompt, *, repair_of=None):
    media = state.output / 'local.jpg'
    media.write_bytes(b'local guard fixture, no real pixels')
    request = dict(provider=opencode_provider.PROVIDER['provider'], policy_version=state.data['policy_version'],
        tool='analyze_image', media_sha256=qfs.sha256_file(media),
        arguments=dict(image_source=str(media), prompt=prompt))
    call, _ = state.begin_call(name, request, repair_of=repair_of)
    write_json(state.output / 'mcp_current.json', dict(job_id=call['id']))
    body = dict(model='glm-5.3-flash', stream=False, messages=[dict(role='system', content='Fixture'),
        dict(role='user', content=[dict(type='image_url', image_url=dict(url='data:image/jpeg;base64,' +
            base64.b64encode(media.read_bytes()).decode())), dict(type='text', text=prompt)])])
    return call, body


def test_evidence_unknown_skim_allows_fine_repair_and_plan_without_replay(tmp_path, monkeypatch):
    state, package, direct, job, no_resend = unknown_skim(tmp_path, monkeypatch)
    lost = deepcopy(state.data['calls'][0])
    reply = evidence('fine_response')
    value = json.loads(reply['result']['content'][0]['text'])
    contracts.validate_fine(deepcopy(value), dict(window_id=value['window_id'], source_id=value['source_id'],
        source_start_s=0, source_end_s=8))
    # Actual guard blocks this before the fix, although the Python ledger admits it.
    stage = value['window_id'].replace('window_', 'fine_')
    call, body = submit(state, stage, 'Bound continuous-window observation')
    assert run_guard(state, package, body, [200])['results'] == [200]
    state.complete_call(call, reply)
    # Received-only format repairs and subsequent planning must also tolerate the lost sheet.
    repair, body = submit(state, stage + '_repair', body['messages'][-1]['content'][-1]['text'] + REPAIR,
                         repair_of=call)
    assert run_guard(state, package, body, [200])['results'] == [200]
    state.complete_call(repair, reply)
    plan, body = submit(state, 'plan_0', 'Plan using received watched windows only')
    assert run_guard(state, package, body, [200, 200])['results'] == [200, 'library_mcp_retry_or_budget_blocked']
    state.complete_call(plan, dict(status='complete'))
    events = [json.loads(line) for line in (state.output / 'mcp_http.jsonl').read_text().splitlines()]
    assert [e['job_id'] for e in events if e['type'] == 'request'] == [call['id'], repair['id'], plan['id']]
    assert not any('retry_permit' in e['type'] for e in events)
    with pytest.raises(qfs.UnknownQFSCall):
        direct.call(lost['name'], job)
    with pytest.raises(LibraryStopped, match='request_already_recorded_reuse_existing_result'):
        state.begin_call(lost['name'], qfs.read_json(state.output / 'calls' / lost['id'] / 'request.json'))
    state._reload()
    assert state.data['calls'][0] == lost
    assert state.usage()['uncertain_calls'] == [lost['id']]
    assert state.data['request_count'] == 4
    no_resend.assert_not_called()
    assert evidence('run_outcome')['error'] == 'no_watched_window_available_for_planning'


def test_evidence_continuation_permission_requires_exact_recorded_qfs_and_creative_mode(tmp_path, monkeypatch):
    state, package, _, _, no_resend = unknown_skim(tmp_path, monkeypatch)
    _, body = submit(state, 'fine_' + 'b' * 16, 'Independent watched-window request')
    # Admission is the positive control for all the narrowly scoped negative checks below.
    baseline = deepcopy(state.data)
    assert run_guard(state, package, body, [200])['results'] == [200]
    path = state.output / 'calls' / baseline['calls'][0]['id'] / 'request.json'
    request = qfs.read_json(path)
    blocked = []
    for status in ['submitted', 'failed_known']:
        changed = deepcopy(baseline)
        changed['calls'][0]['status'] = status
        blocked.append((changed, request, BLOCKED))
    for workflow in ['visual_library_v1', 'visual_audio_v1']:
        changed = deepcopy(baseline)
        changed['input_lock']['configuration']['workflow'] = workflow
        blocked.append((changed, request, BLOCKED))
    for field, value in [('stage', 'other'), ('provider', opencode_provider.PROVIDER['provider']),
                         ('endpoint', 'https://example.invalid'), ('payload', dict(model='other'))]:
        changed, modified = deepcopy(baseline), dict(request, **{field: value})
        changed['calls'][0]['request_sha256'] = json_sha(modified)
        blocked.append((changed, modified, BLOCKED))
    changed = deepcopy(baseline)
    changed['calls'][0]['name'] = 'fine_' + 'c' * 16
    blocked.append((changed, request, BLOCKED))
    blocked.append((baseline, dict(request, stage='tampered'), 'library_mcp_opencode_recorded_request_changed'))
    for changed, modified, expected in blocked:
        write_json(state.path, changed)
        write_json(path, modified)
        result = run_guard(state, package, body, [200])
        assert result['sends'] == 0 and result['results'] == [expected], (changed, modified, result)
    no_resend.assert_not_called()
