"""Synthetic append-only authorization; no real video, model, or run mutation."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import independent_slot_recovery as recovery
from omni_story.library.media import sha256_file
from omni_story.library.state import json_sha, write_json


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / 'same_existing_run'
    root.mkdir()
    parent_file = root / 'render_3/final.mp4'
    parent_file.parent.mkdir()
    parent_file.write_bytes(b'Synthetic parent bytes; not a real movie.')
    parent_sha = sha256_file(parent_file)
    parent = {'round': 3, 'baseline_id': 'render_3', 'sha256': parent_sha, 'duration_s': 34, 'path': str(parent_file)}
    preparation = root / 'artifacts/preparation.json'
    write_json(preparation, {'parents': [parent]})
    local_file = root / 'media_cache/local/window.mp4'
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b'Synthetic cropped parent bytes, distinct from whole video.')
    local_sha = sha256_file(local_file)
    local_scope = {'kind': 'continuous_window', 'source_sha256': parent_sha, 'source_start_s': 27.0, 'source_end_s': 34.0}
    lineage = {**local_scope, 'path': str(local_file), 'sha256': local_sha, 'source_offset_s': 27,
               'media_duration_s': 7, 'time_mapping': 'source_time_s = source_offset_s + proxy_time_s',
               'spec': {**local_scope, 'fps': 30}}
    write_json(local_file.parent / 'lineage.json', lineage)
    data = {'task_id': 'synthetic_fixed_task', 'max_requests': 80, 'request_count': 166,
            'input_lock': {'reference_sha256': 'a' * 64}, 'calls': [], 'artifacts': {}}
    unknown_specs = {3: (recovery.FROZEN_IDS[0], {'kind': 'sparse_contact_sheet', 'source_sha256': 'b' * 64,
                                                'source_start_s': 600, 'source_end_s': 1200}),
                     130: (recovery.FROZEN_IDS[1], {'kind': 'continuous_window', 'source_sha256': 'c' * 64,
                                                  'source_start_s': 0, 'source_end_s': 21.933333}),
                     165: (recovery.LOST_CALL, {**local_scope, 'source_start_s': 0, 'source_end_s': 34})}
    for index in range(166):
        ident = f'known_fixture_{index + 1}'
        name = 'sf_0_fixture'
        request = {'fixture': index}
        status = 'received'
        if index in unknown_specs:
            ident, scope = unknown_specs[index]
            name = 'sf_3_source_feedback_replan_v1' if index == 165 else ident
            status = 'uncertain'
            request = {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video',
                       'media_sha256': str(index % 10) * 64, 'observation_scope': scope,
                       'arguments': {'video_source': 'Synthetic lost input', 'prompt': 'Original immutable request'}}
            if index == 3:
                # Actual 004 predates explicit observation_scope. Its original
                # request must remain untouched; exclusions use saved lineage.
                legacy = root / 'media_cache/legacy/sheet.jpg'
                legacy.parent.mkdir(parents=True)
                legacy.write_bytes(b'Uncertain synthetic navigation image')
                write_json(legacy.parent / 'lineage.json', scope)
                request.pop('observation_scope')
                request['arguments'] = {'image_source': str(legacy), 'prompt': 'Original immutable request'}
        elif index == 156:
            ident, name = 'known_local_fact', 'sf_3_facts_' + 'd' * 16
            request = {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video',
                       'media_sha256': local_sha, 'observation_scope': local_scope,
                       'arguments': {'video_source': str(local_file), 'prompt': 'Existing known local navigation'}}
        row = {'id': ident, 'name': name, 'status': status, 'request_sha256': json_sha(request),
               'submitted_at': 'synthetic_original_time', 'repair_of': None, 'usage': {}}
        write_json(root / 'calls' / ident / 'request.json', request)
        if status == 'received':
            response = {'fixture_known_response': index}
            write_json(root / 'calls' / ident / 'response.json', response)
            row['response_sha256'] = json_sha(response)
        data['calls'].append(row)
    old = deepcopy(data['calls'][-1])
    old['status'] = 'submitted'
    data['calls'][-1]['reconciled_at'] = 'synthetic_known_uncertainty_time'
    observation = {'call_id': recovery.LOST_CALL, 'previous_call': old, 'response_captured': False,
                   'no_replay': True, 'request_count_before': 166, 'new_requests': 0, 'new_renders': 0}
    receipt = root / 'artifacts/unknown_observation.json'
    write_json(receipt, observation)
    data['artifacts'][recovery.OBSERVATION_ARTIFACT] = [{'path': str(receipt), 'sha256': json_sha(observation), 'at': 'synthetic'}]
    write_json(root / 'calls' / recovery.LOST_CALL / 'uncertain_reclassification.json',
               {'previous_record': old, 'http_evidence': [{'observation_artifact': data['artifacts'][recovery.OBSERVATION_ARTIFACT][0]}]})
    original = json.loads((root / 'calls' / recovery.LOST_CALL / 'request.json').read_text(encoding='utf-8'))
    write_json(root / 'mcp_queue' / (recovery.LOST_CALL + '.request.json'),
               {'job_id': recovery.LOST_CALL, 'tool': original['tool'], 'arguments': original['arguments']})
    write_json(root / 'mcp_queue' / (recovery.LOST_CALL + '.started.json'), {'job_id': recovery.LOST_CALL, 'at': 'synthetic'})
    grant = {'task_id': data['task_id'], 'preparation_id': 'synthetic_fixed_preparation',
             'original_output': str(root), 'input_lock_sha256': json_sha(data['input_lock']),
             'max_slices_per_parent': 32, 'renders_per_parent': 1, 'repairs_per_stage': 1,
             'new_unique_windows': 0, 'additional_requests': None, 'effective_request_limit': None,
             'base_request_limit': 80, 'preparation_path': str(preparation)}
    write_json(root / 'library_state.json', data)
    state = SimpleNamespace(output=root, data=data, assert_protected=lambda: grant)
    def set_artifact(name, payload):
        path = root / 'artifacts' / (name + '.json')
        write_json(path, payload)
        data['artifacts'][name] = [{'path': str(path), 'sha256': json_sha(payload)}]
        write_json(root / 'library_state.json', data)
    state.set_artifact = set_artifact
    return state, grant


def register(fixture):
    state, grant = fixture
    return recovery.record_independent_slot_recovery(state, 'Explicit synthetic continuation of both existing parents.')


def local_request(policy):
    local = policy['local_replan_input']
    return {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video', 'media_sha256': local['media_sha256'],
            'observation_scope': deepcopy(local['observation_scope']),
            'arguments': {'video_source': local['path'], 'prompt': 'New local planning task, no lost reply'}}


def append(state, name, status='submitted', repair_of=None):
    state.data['calls'].append({'id': 'synthetic_new_' + str(len(state.data['calls']) + 1), 'name': name,
                              'status': status, 'repair_of': repair_of, 'request_sha256': 'f' * 64})
    state.data['request_count'] += 1
    write_json(state.output / 'library_state.json', state.data)


def test_registration_freezes_old_unknowns_and_preserves_every_old_byte(fixture):
    state, grant = fixture
    calls = deepcopy(state.data['calls'])
    old_files = {p: p.read_bytes() for p in state.output.rglob('*') if p.is_file() and p.name != 'library_state.json'}
    policy = register(fixture)
    assert state.data['calls'] == calls
    assert state.data['request_count'] == 166 and state.data['max_requests'] == 80
    assert all(p.read_bytes() == raw for p, raw in old_files.items())
    assert policy['effective_request_limit'] is None and policy['renders_per_parent'] == 1
    assert policy['max_slices_per_parent'] == 32 and policy['new_unique_windows'] == 0
    assert recovery.read_validate(state.output, state.data, grant) == policy
    assert register(fixture) == policy


@pytest.mark.parametrize('change', ['extra_unknown', 'submitted', 'received_166', 'different_id', 'budget_reset'])
def test_activation_does_not_admit_another_uncertainty_or_reset_ledger(fixture, change):
    state, _ = fixture
    if change == 'extra_unknown':
        state.data['calls'][5]['status'] = 'uncertain'
    elif change == 'submitted':
        state.data['calls'][5]['status'] = 'submitted'
    elif change == 'received_166':
        state.data['calls'][-1]['status'] = 'received'
    elif change == 'different_id':
        state.data['calls'][-1]['id'] = 'another_unknown'
    else:
        state.data['request_count'] = 0
    with pytest.raises(ValueError, match='unexpected_unknown|activation_baseline'):
        register(fixture)


def test_two_different_parent_jobs_may_be_pending_but_one_parent_cannot_duplicate(fixture):
    state, grant = fixture
    policy = register(fixture)
    assert recovery.check_next_stage(policy, state.data, 0, 'sf_0_assemble')
    assert recovery.check_next_stage(policy, state.data, 3, recovery.LOCAL_STAGE)
    append(state, 'sf_0_assemble')
    append(state, recovery.LOCAL_STAGE)
    assert recovery.read_validate(state.output, state.data, grant) == policy
    append(state, recovery.LOCAL_STAGE + '_repair', repair_of=state.data['calls'][-1]['id'])
    with pytest.raises(ValueError, match='one_pending_per_parent'):
        recovery.read_validate(state.output, state.data, grant)


@pytest.mark.parametrize('status', ['uncertain', 'failed_known'])
def test_a_new_unknown_stops_both_lanes_without_admitting_it(fixture, status):
    state, grant = fixture
    policy = register(fixture)
    append(state, 'sf_0_assemble', status=status)
    assert not recovery.admissible_status(policy, state.data['calls'][-1])
    with pytest.raises(ValueError, match='new_outcome_unknown'):
        recovery.read_validate(state.output, state.data, grant)


@pytest.mark.parametrize('index', range(3))
def test_unknown_request_media_or_scope_cannot_be_reencoded_or_reprompted(fixture, index):
    policy = register(fixture)
    request = local_request(policy)
    old = policy['unknown_inputs'][index]
    request['media_sha256'] = old['media_sha256']
    with pytest.raises(ValueError, match='unknown_input_replay_forbidden'):
        recovery.check_input(policy, recovery.LOCAL_STAGE, request)
    request = local_request(policy)
    request['observation_scope'] = old['scope']
    with pytest.raises(ValueError, match='unknown_input_replay_forbidden'):
        recovery.check_input(policy, recovery.LOCAL_STAGE, request)


@pytest.mark.parametrize('change', ['range', 'path', 'media'])
def test_local_replan_only_uses_bound_existing_27_34_crop(fixture, change):
    policy = register(fixture)
    request = local_request(policy)
    assert recovery.check_input(policy, recovery.LOCAL_STAGE, request)
    if change == 'range':
        request['observation_scope']['source_start_s'] = 26.9
    elif change == 'path':
        request['arguments']['video_source'] = str(Path(request['arguments']['video_source']).with_name('another_encoding.mp4'))
    else:
        request['media_sha256'] = 'e' * 64
    with pytest.raises(ValueError, match='local_replan_input_changed'):
        recovery.check_input(policy, recovery.LOCAL_STAGE, request)


@pytest.mark.parametrize('name', ['sf_3_source_feedback_replan_v1_repair', 'sf_3_assemble',
                                'semantic_slice_21_' + 'e' * 16, 'sf_0_blind', 'active_11_draft'])
def test_first_stage_cannot_retry_166_or_start_unrelated_work(fixture, name):
    state, _ = fixture
    policy = register(fixture)
    with pytest.raises(ValueError, match='stage_not_authorized|first_independent_stage_changed'):
        recovery.check_next_stage(policy, state.data, 0 if name.startswith('sf_0') else 3, name)


def test_local_stage_gets_only_its_sole_known_format_repair(fixture):
    state, grant = fixture
    policy = register(fixture)
    append(state, recovery.LOCAL_STAGE, status='received')
    original = state.data['calls'][-1]
    append(state, recovery.LOCAL_STAGE + '_repair', status='received', repair_of=original['id'])
    assert recovery.read_validate(state.output, state.data, grant) == policy
    append(state, recovery.LOCAL_STAGE, status='received')
    with pytest.raises(ValueError, match='local_stage_or_repair_repeated'):
        recovery.read_validate(state.output, state.data, grant)


def test_frozen_prefix_old_failures_and_unknown_reclassification_are_immutable(fixture):
    state, grant = fixture
    policy = register(fixture)
    state.data['calls'][-1]['status'] = 'received'
    with pytest.raises(ValueError, match='policy_or_prefix_changed'):
        recovery.read_validate(state.output, state.data, grant)
    state.data['calls'][-1]['status'] = 'uncertain'
    path = Path(policy['transport_binding']['reclassification_path'])
    original = path.read_bytes()
    path.write_bytes(original + b' ')
    with pytest.raises(ValueError, match='bound_input_changed'):
        recovery.read_validate(state.output, state.data, grant)
    path.write_bytes(original)
    assert recovery.read_validate(state.output, state.data, grant) == policy


@pytest.mark.skipif(not shutil.which('node'), reason='Matching Node guard requires Node.js')
def test_node_guard_matches_activation_parallel_no_replay_and_changed_history(fixture):
    state, grant = fixture
    policy = register(fixture)
    module = Path('omni_story/library/mcp_independent_slot_recovery.mjs').resolve().as_uri()
    script = r'''const fs=await import('node:fs'),crypto=await import('node:crypto');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));class N{constructor(s){this.s=s;}}
function fileDigest(p,select=v=>v){const v=JSON.parse(fs.readFileSync(p,'utf8'),(k,v,c)=>typeof v==='number'?new N(c.source):v);
function serialize(v){if(v instanceof N)return v.s;if(Array.isArray(v))return '['+v.map(serialize).join(',')+']';
if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+serialize(v[k])).join(',')+'}';return JSON.stringify(v);}
return crypto.createHash('sha256').update(serialize(select(v))).digest('hex');}
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const scope=v=>JSON.stringify([v.kind,v.source_sha256,v.source_start_s,v.source_end_s]);
try{const m=await import(process.argv[1]),state=read(process.argv[2]+'/library_state.json');
const p=m.validateIndependentSlotRecovery(process.argv[2],state,JSON.parse(process.argv[3]),{read,fileDigest,hash,scope});
if(process.argv[4])m.checkIndependentSlotRecoveryInput(p,{name:m.LOCAL_REPLAN_STAGE},JSON.parse(process.argv[4]),{scope});
process.stdout.write(p.policy);}catch(e){process.stderr.write(e.message);process.exitCode=3;}'''
    def invoke(request=None):
        args = ['node', '--input-type=module', '-e', script, module, str(state.output), json.dumps(grant)]
        if request is not None:
            args.append(json.dumps(request))
        return subprocess.run(args, capture_output=True, text=True, timeout=30)
    result = invoke(local_request(policy))
    assert result.returncode == 0, result.stderr
    assert result.stdout == recovery.POLICY
    request = local_request(policy)
    request['observation_scope'] = policy['unknown_inputs'][-1]['scope']
    result = invoke(request)
    assert result.returncode == 3 and 'unknown_input_replay_forbidden' in result.stderr
    append(state, 'sf_0_assemble')
    append(state, recovery.LOCAL_STAGE)
    assert invoke().returncode == 0
    state.data['calls'][-1]['status'] = 'uncertain'
    write_json(state.output / 'library_state.json', state.data)
    result = invoke()
    assert result.returncode == 3 and 'new_outcome_unknown' in result.stderr
