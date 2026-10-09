"""Original-method restoration policy tests use local files, never model calls."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import restoration_policy as policy, server_jobs
from omni_story.library.media import sha256_file
from omni_story.library.opencode_provider import PROVIDER
from omni_story.library.state import LibraryState, LibraryStopped, json_sha, write_json


def reply(value):
    return {'status': 'complete', 'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}


@pytest.fixture
def trial(tmp_path, monkeypatch):
    monkeypatch.setattr(server_jobs, '_identity', lambda pid: None)
    monkeypatch.setattr(server_jobs, '_group_running', lambda pid: False)
    parent = tmp_path / 'original_server_task'
    parent.mkdir()
    reference_sha = 'a' * 64
    calls = []
    for index in range(36):
        name = 'chain_e2e_v1_rough_blind' if index == 35 else f'old_{index}'
        request = {'old': index}
        call = {'id': f'glm_{index+1:03d}_{name}', 'name': name,
            'status': 'failed_known' if index == 35 else 'received', 'request_sha256': json_sha(request),
            'repair_of': None}
        directory = parent / 'calls' / call['id']
        write_json(directory / 'request.json', request)
        if index == 35:
            call['error'] = 'opencode_no_original_vision_reply'
            write_json(directory / 'failure.json', {'error': call['error'], 'uncertain': False})
            write_json(parent / 'mcp_queue' / (call['id'] + '.response.json'),
                {'status': 'error', 'result': {'isError': True, 'content': [{
                    'type': 'text', 'text': 'Error: Unexpected error: Video analysis failed: Network error: server_chain_authorization_old_file_changed'}]}})
        else:
            response = reply({'old': index})
            call['response_sha256'] = json_sha(response)
            write_json(directory / 'response.json', response)
        calls.append(call)
    write_json(parent / 'library_state.json', {'task_id': 'parent-task', 'request_count': 36,
        'calls': calls, 'input_lock': {'reference_sha256': reference_sha}, 'max_requests': None})
    write_json(parent / 'reference_catalog/inventory.json', {'sources': [{'sha256': reference_sha, 'duration_s': 21.933333}]})
    controller = parent / '.omni-server/recoveries/one_chain_e2e_v1'
    write_json(controller / 'job.json', {'state': 'failed', 'exit_code': 1,
        'supervisor_pid': 12345, 'child_pid': 12346})
    (controller / 'run.lock').write_text('prior-control', encoding='utf-8')
    write_json(parent / 'failure_one_chain_e2e_v1.json', {'error': 'old_file_changed'})
    (parent / 'mcp_http.jsonl').write_text(json.dumps({'job_id': calls[0]['id'], 'type': 'response'}) + '\n', encoding='utf-8')
    first_request, first_reply = {'media_sha256': reference_sha}, reply({'reference_sha256': reference_sha, 'theme': 'original interpretation'})
    first = {'id': 'glm_001_reference', 'status': 'received', 'request_sha256': json_sha(first_request),
             'response_sha256': json_sha(first_reply)}
    historical = [first] + [{'id': f'glm_{index:03d}_historic', 'status': 'uncertain' if index in (4, 131, 166, 265) else 'received'}
        for index in range(2, 275)]
    unknown = [{'call_id': row['id'], 'request_sha256': 'c' * 64, 'media_sha256': 'b' * 64,
        'scope': {'kind': 'continuous_window', 'source_sha256': 'd' * 64,
                  'source_start_s': 0.0, 'source_end_s': 15.0}}
        for row in historical if row['status'] == 'uncertain']
    history = {'baseline_requests': 274, 'historical_calls': historical,
        'baseline_calls_sha256': json_sha(historical), 'unknown_inputs': unknown}
    seed = {'source_call_id': first['id'], 'request_sha256': first['request_sha256'],
        'response_sha256': first['response_sha256'], 'reference_sha256': reference_sha,
        'full_response': {'reference': {'reference_sha256': reference_sha, 'theme': 'original interpretation'}},
        'seed_request': first_request, 'seed_response': first_reply, 'evidence_limit': 'Historical limitations preserved.'}
    lane = parent / 'artifacts' / policy.NAME
    return parent, lane, seed, history


def registered(trial):
    parent, lane, seed, history = trial
    path = policy.register(parent, lane, '那你恢复', seed, history=history)
    state = LibraryState(lane, {'reference_sha256': seed['reference_sha256'],
        'configuration': {**PROVIDER, **policy.configuration(path)}}, max_requests=None,
        registry_path=parent / 'restoration_registry.json')
    return state, path


def append(state, name='search_0', *, media_sha='e' * 64, scope=None, parent=None, complete=True):
    request = {'tool': 'analyze_image', 'arguments': {'image_source': str(state.output / 'local.png'), 'prompt': name},
               'media_sha256': media_sha, 'provider': PROVIDER['provider'], 'policy_version': state.data['policy_version']}
    if scope is not None:
        request['observation_scope'] = scope
    if parent:
        request = json.loads((state.output / 'calls' / parent['id'] / 'request.json').read_text())
        request['arguments']['prompt'] += policy.REPAIR_MARKER + 'fix syntax only'
    call, folder = state.begin_call(name, request, repair_of=parent)
    if complete:
        state.complete_call(call, reply({'fact': 'synthetic'}))
        write_json(folder / 'parsed.json', {'fact': 'synthetic'})
    return call


def node_load(lane):
    if not shutil.which('node'):
        pytest.skip('Needs Node.js')
    module = Path(policy.__file__).with_suffix('.mjs')
    script = """
const {restorationAuthorization}=await import(process.argv[1]);
try {console.log(restorationAuthorization(process.argv[2]) ? 'accepted' : 'null');}
catch(error){console.log(error.message);}
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script, module.as_uri(), str(lane)],
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_register_preserves_parent_and_keeps_aggregate_accounting(trial):
    parent = trial[0]
    original = sha256_file(parent / 'library_state.json')
    state, _ = registered(trial)
    append(state)
    proof = policy.load(state.output)
    assert sha256_file(parent / 'library_state.json') == original
    assert policy.aggregate_usage(proof, state.usage()['requests'])['lineage_cumulative_vision_requests'] == 311
    assert node_load(state.output) == 'accepted'


def test_registration_is_once_and_original_reference_is_bound(trial):
    registered(trial)
    with pytest.raises(LibraryStopped, match='lane_already_exists'):
        policy.register(trial[0], trial[1], '那你恢复', trial[2], history=trial[3])


def test_original_001_cannot_be_replaced_by_060_or_modified_interpretation(trial):
    seed = deepcopy(trial[2]); seed['full_response']['reference']['theme'] = 'different theme'
    with pytest.raises(LibraryStopped, match='original_001_seed_changed'):
        policy.register(trial[0], trial[1], '那你恢复', seed, history=trial[3])


def test_known_prepost_failure_cannot_hide_a_real_post(trial):
    parent = trial[0]
    last = json.loads((parent / 'library_state.json').read_text())['calls'][-1]
    with (parent / 'mcp_http.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'job_id': last['id'], 'type': 'request'}) + '\n')
    with pytest.raises(LibraryStopped, match='old_36_has_HTTP_event'):
        registered(trial)


def test_old_failure_or_request_cannot_be_overwritten(trial):
    state, _ = registered(trial)
    append(state)
    (trial[0] / 'failure_one_chain_e2e_v1.json').write_text('{}', encoding='utf-8')
    with pytest.raises(LibraryStopped, match='bound_history_changed'):
        policy.load(state.output)
    assert 'bound_history_changed' in node_load(state.output)


@pytest.mark.parametrize('variant', ['media', 'lineage', 'whole_reference'])
def test_lost_inputs_and_whole_reference_cannot_be_reencoded_and_replayed(trial, variant):
    state, _ = registered(trial)
    sha = {'media': 'b' * 64, 'lineage': 'e' * 64, 'whole_reference': 'a' * 64}[variant]
    scope = {'kind': 'continuous_window', 'source_sha256': 'd' * 64,
             'source_start_s': 0, 'source_end_s': 15} if variant == 'lineage' else None
    append(state, media_sha=sha, scope=scope)
    with pytest.raises(LibraryStopped, match='no_replay|whole_reference_POST_forbidden'):
        policy.load(state.output)
    assert 'no_replay' in node_load(state.output) or 'whole_reference_POST_forbidden' in node_load(state.output)


@pytest.mark.parametrize('stage', ['reference', 'editing_reference_v2', 'plan_2', 'finecut_0',
    'chain_e2e_v1_fine_inspect_4', 'chain_e2e_v1_fine_detail_0_6'])
def test_finite_stages_exclude_fresh_reference_wrong_old_finecut_and_extra_round(trial, stage):
    state, _ = registered(trial)
    append(state, stage)
    with pytest.raises(LibraryStopped, match='stage_not_permitted'):
        policy.load(state.output)
    assert 'stage_not_permitted' in node_load(state.output)


def test_finecut_requires_a_reviewed_actual_rough_video(trial):
    state, _ = registered(trial)
    append(state, 'chain_e2e_v1_fine_observe')
    with pytest.raises(LibraryStopped, match='actual_rough_handoff_required'):
        policy.load(state.output)
    assert 'actual_rough_handoff_required' in node_load(state.output)


def test_finecut_uses_bound_actual_rough_handoff(trial):
    state, _ = registered(trial)
    call = append(state, 'review_0')
    video = state.output / 'render_0/final.mp4'
    video.parent.mkdir(); video.write_bytes(b'Synthetic rough media, never decoded.')
    handoff = {'rough_video_path': str(video), 'rough_source_sha256': sha256_file(video),
        'selected_round': 0, 'rough_review_call_id': call['id']}
    path = state.output / 'rough_handoff.json'; write_json(path, handoff)
    state.set_artifact('restored_rough_handoff', {'path': str(path), 'sha256': json_sha(handoff)})
    append(state, 'chain_e2e_v1_fine_observe')
    assert policy.load(state.output) is not None
    assert node_load(state.output) == 'accepted'
    video.write_bytes(b'Changed rough file')
    with pytest.raises(LibraryStopped, match='rough_video_changed'):
        policy.load(state.output)


def test_one_format_repair_is_kept_with_original_and_not_a_new_round(trial):
    state, _ = registered(trial)
    first = append(state)
    append(state, 'search_0_repair', parent=first)
    assert policy.load(state.output) is not None
    assert node_load(state.output) == 'accepted'


@pytest.mark.parametrize('range_,accepted', [((3.0, 6.0), True), ((0.0, 21.933333), False), ((2.0, 10.0), False)])
def test_reference_partial_window_is_bounded_and_whole_scope_is_never_replayed(trial, range_, accepted):
    state, _ = registered(trial)
    start, end = range_
    append(state, scope={'kind': 'continuous_window', 'source_sha256': 'a' * 64,
                        'source_start_s': start, 'source_end_s': end})
    if accepted:
        assert policy.load(state.output) is not None
        assert node_load(state.output) == 'accepted'
    else:
        with pytest.raises(LibraryStopped, match='whole_reference_POST_forbidden|reference_local_scope_invalid'):
            policy.load(state.output)
        assert 'reference_' in node_load(state.output)
