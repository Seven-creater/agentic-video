"""Synthetic assembly-binding tests; no actual run, paid model or new movie."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import slot_finecut as sf, slot_finecut_budget as budget
from omni_story.library.slot_finecut_assembly_binding import POLICY, request_bound_assembly
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_slot_finecut_execute import prepared

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Synthetic parent fixture requires FFmpeg')


@pytest.fixture
def bound_fixture(prepared):
    output, preparation, methods, _ = prepared
    parent = next(p for p in preparation['parents'] if p['round'] == 3)
    execution = output / 'synthetic_execution'
    folder = execution / 'render_3'
    knowledge = output / 'SLOT_FINECUT.md'
    knowledge.write_text('Synthetic generic editing knowledge.', encoding='utf-8')
    record = {'baseline_request_count': 2, 'preparation_id': preparation['preparation_id'],
              'preparation_path': preparation['preparation_path'], 'execution_directory': str(execution),
              'reference_methods_path': str(output / 'editing_reference_v2.json'),
              'knowledge_path': str(knowledge), 'unknown_inputs': [], 'max_slices_per_parent': 32}
    fields = {'entry_state': 'Synthetic state', 'exit_state': 'Synthetic outcome',
              'link_to_previous': 'Synthetic link', 'link_to_next': 'Synthetic link'}
    split = parent['provenance'][0]['output_out_s']
    outline = {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'], 'slots': [
        {'slot_id': 's1', 'start_s': 0, 'end_s': split, 'intended_takeaway': 'Synthetic opening', **fields},
        {'slot_id': 's2', 'start_s': split, 'end_s': parent['duration_s'],
         'intended_takeaway': 'Synthetic “title” outcome', **fields}], 'limitations': [], 'uncertainties': []}
    proposals, segments, slots, bindings, timing = [], [], [], [], []
    for index, original in enumerate(parent['provenance']):
        slot = outline['slots'][index]
        operation = {'parent_segment_index': index, **{k: original.get(k, 0) for k in
            ('source_in_s', 'source_out_s', 'speed', 'freeze_tail_s')}, 'reason': 'Synthetic visibility.',
            'evidence_ids': ['parent_e1'], 'essential_intervals': [{'source_start_s': original['source_in_s'],
                'source_end_s': original['source_out_s'], 'min_readable_s': .1, 'information': 'Synthetic geometry',
                'evidence_ids': ['parent_e1'], 'continues_in_tail_frame': False}]}
        proposals.append({'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
            'slot_id': slot['slot_id'], 'candidates': [{'candidate_id': 'c1', 'operations': [operation],
                'meaning_status': 'preserved', 'rationale': 'Synthetic proposal.', 'limitations': []}]})
        ident, claim = f'seg_{index}', f'claim_{index}'
        segments.append({'segment_id': ident, 'slot_id': slot['slot_id'], **{k: original.get(k, 0) for k in
            ('source_id', 'window_id', 'source_in_s', 'source_out_s', 'speed', 'freeze_tail_s', 'role_ids')},
            'framing': 'fit', 'look': 'none', 'visual_claims': [{'claim_id': claim,
                'kind': 'visual_action', 'description': 'Synthetic geometric subject remains visible.'}]})
        slots.append({'slot_id': slot['slot_id'], 'intended_takeaway': slot['intended_takeaway'].replace('“', '').replace('”', ''),
                      'segment_ids': [ident]})
        bindings.append({'segment_id': ident, 'slot_id': slot['slot_id'], 'candidate_id': 'c1', 'operation_index': 0})
        timing.append({'segment_id': ident, 'essential_claims': [{'essential_interval_index': 0, 'claim_ids': [claim]}]})
    plan = deepcopy(parent['original_plan'])
    plan.update(slots=slots, segments=segments)
    plan['editing_bindings'][0]['segment_ids'] = [s['segment_id'] for s in segments]
    body = {'plan': plan, 'selections': [{'slot_id': s['slot_id'], 'candidate_id': 'c1'} for s in slots],
            'segment_bindings': bindings, 'timing_checks': timing, 'transition_checks': [{
                'from_segment_id': 'seg_0', 'to_segment_id': 'seg_1', 'relation': 'Synthetic link.', 'status': 'planned'}],
            'limitations': []}
    write_json(folder / 'outline.json', outline)
    write_json(folder / 'proposals.json', proposals)
    catalog = json.loads((output / 'catalog/inventory.json').read_text(encoding='utf-8'))
    prompt = sf.assembly_prompt(parent, outline, proposals, catalog, parent['allowed_windows'],
                               preparation['reference'], methods, knowledge.read_text(encoding='utf-8'))
    prompt += '\n' + json.dumps({'mechanical_no_replay_source_scopes': []})
    media = output / 'proxy/window.mp4'
    media.parent.mkdir()
    media.write_bytes(b'Synthetic proxy bytes for strict request SHA binding only.')
    media_sha = budget.sha256_file(media)
    scope = {'kind': 'continuous_window', 'source_sha256': parent['sha256'],
             'source_start_s': 0.0, 'source_end_s': parent['duration_s']}
    write_json(media.parent / 'lineage.json', {**scope, 'sha256': media_sha, 'source_offset_s': 0,
        'media_duration_s': parent['duration_s'], 'time_mapping': 'source_time_s = source_offset_s + proxy_time_s'})
    data = json.loads((output / 'library_state.json').read_text(encoding='utf-8'))
    files = []
    for attempt in (0, 1):
        ident = 'synthetic_assembly_' + str(attempt)
        request = {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video', 'media_sha256': media_sha,
            'observation_scope': scope, 'arguments': {'video_source': str(media), 'prompt': prompt if attempt == 0 else 'Saved sole repair.'}}
        raw = json.dumps(body, ensure_ascii=False)
        response = {'result': {'content': [{'type': 'text', 'text': raw if attempt else raw[:-1]}]}}
        failure = {'attempt': attempt, 'error': 'slot_finecut:assembly_parent_changed' if attempt else "Expecting ',' delimiter"}
        for name, value in (('request.json', request), ('response.json', response), ('protocol_failure.json', failure)):
            p = output / 'calls' / ident / name
            write_json(p, value)
            files.append({'path': str(p), 'sha256': budget.sha256_file(p)})
        data['calls'].append({'id': ident, 'name': 'sf_3_assemble' + ('_repair' if attempt else ''),
            'status': 'received', 'request_sha256': json_sha(request), 'response_sha256': json_sha(response),
            'repair_of': 'synthetic_assembly_0' if attempt else None})
    data['request_count'] = len(data['calls'])
    state = SimpleNamespace(output=output, data=data, authorization=record, assert_protected=lambda: record)
    def artifact(name, value):
        p = output / (name + '.json')
        write_json(p, value)
        data['artifacts'][name] = [{'path': str(p), 'sha256': json_sha(value)}]
    state.set_artifact = artifact
    stop = {'policy': budget.PARENT_STOP_POLICY, 'preparation_id': record['preparation_id'], 'parent_round': 3,
        'independent_parent_round': 0, 'request_count': len(data['calls']), 'original_call_id': 'synthetic_assembly_0',
        'repair_call_id': 'synthetic_assembly_1', 'error': 'model_protocol_repair_exhausted:sf_3_assemble', 'files': files}
    artifact('sf_parent_protocol_stop_3_point_navigation_resume', stop)
    result_path = folder / 'result_point_navigation_resume.json'
    write_json(result_path, {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
        'status': 'stopped_protocol_failure', 'stop_receipt': stop, 'completed_files': []})
    artifact('sf_result_3_point_navigation_resume', {'result_path': str(result_path), 'result_sha256': budget.sha256_file(result_path)})
    write_json(output / 'library_state.json', data)
    return state, record, body, parent, outline, proposals, catalog, preparation['reference'], methods


def register(fixture):
    state, record, *_ = fixture
    policy = budget.record_request_bound_assembly(state)
    write_json(state.output / 'library_state.json', state.data)
    return policy


def invoke(state, record, *, function='requestBoundAssembly', options=None):
    write_json(state.output / 'library_state.json', state.data)
    module = Path('omni_story/library/mcp_slot_finecut_guard.mjs').resolve().as_uri()
    code = """const fs=await import('node:fs');const guard=await import(process.argv[1]);
const state=JSON.parse(fs.readFileSync(process.argv[2]+'/library_state.json','utf8'));
try{process.stdout.write(JSON.stringify(guard[process.argv[4]](process.argv[2],state,JSON.parse(process.argv[3]),JSON.parse(process.argv[5]))));}
catch(e){process.stderr.write(e.message);process.exitCode=3;}"""
    return subprocess.run(['node', '--input-type=module', '-e', code, module, str(state.output), json.dumps(record),
                           function, json.dumps(options or {})], text=True, capture_output=True, timeout=30)


def test_whole_known_body_only_gets_two_program_fields_and_old_bytes_stay(bound_fixture):
    state, record, body, *_ = bound_fixture
    before = {p: p.read_bytes() for p in state.output.rglob('*') if p.is_file()}
    policy = register(bound_fixture)
    assert {k: v for k, v in policy['body'].items() if k not in policy['program_field_additions']} == body
    assert set(policy['program_field_additions']) == {'baseline_id', 'parent_sha256'}
    assert policy['original_contract_status'] == 'failed' and not policy['old_parsed_created']
    assert policy['body']['plan']['slots'][1]['intended_takeaway'] == 'Synthetic title outcome'
    assert all(p.read_bytes() == raw for p, raw in before.items() if p.name != 'library_state.json')
    assert budget.record_request_bound_assembly(state) == policy
    assert not any((state.output / 'calls' / ident / 'parsed.json').exists() for ident in ('synthetic_assembly_0', 'synthetic_assembly_1'))
    assert budget.SlotFinecutState._received(state, {}, 'sf_3_assemble') == policy['body']


def test_default_target_contract_is_still_strict(bound_fixture):
    _, _, body, parent, outline, proposals, catalog, reference, methods = bound_fixture
    bound = {**body, 'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256']}
    with pytest.raises(ValueError, match='original_slot_obligation_changed'):
        sf.validate_assembly(bound, parent, outline, proposals, catalog, parent['allowed_windows'], reference, methods)
    assert sf.validate_assembly(bound, parent, outline, proposals, catalog, parent['allowed_windows'], reference, methods,
                               allow_typographic_target_variants=True) == bound


@pytest.mark.parametrize('tamper', ['body', 'target_word', 'addition', 'chosen', 'next', 'failure', 'parsed', 'context'])
def test_python_and_node_reject_tampering_even_with_policy_rehash(bound_fixture, tamper):
    state, record, *_ = bound_fixture
    policy = register(bound_fixture)
    if tamper == 'body':
        policy['body']['plan']['segments'][0]['speed'] = .75
    elif tamper == 'target_word':
        policy['body']['plan']['slots'][1]['intended_takeaway'] += ' rewritten'
    elif tamper == 'addition':
        policy['body']['parent_sha256'] = 'f' * 64
    elif tamper == 'chosen':
        policy['chosen_call_id'] = 'synthetic_assembly_0'
    elif tamper == 'next':
        policy['next_stage'] = 'semantic_slice_21_' + 'f' * 16
    elif tamper == 'failure':
        p = state.output / 'calls/synthetic_assembly_0/protocol_failure.json'
        p.write_bytes(p.read_bytes() + b' ')
    elif tamper == 'parsed':
        write_json(state.output / 'calls/synthetic_assembly_1/parsed.json', {})
    else:
        p = state.output / 'synthetic_execution/render_3/proposals.json'
        value = json.loads(p.read_text(encoding='utf-8'))
        value[0]['candidates'][0]['operations'][0]['speed'] = .75
        write_json(p, value)
    state.set_artifact(POLICY, policy)
    with pytest.raises((ValueError, LibraryStopped)):
        request_bound_assembly(state.output, state.data, record)
    if shutil.which('node'):
        checked = invoke(state, record)
        assert checked.returncode != 0 and 'request_bound_assembly_' in checked.stderr


def test_pending_registration_blocks_and_different_source_stage_can_follow(bound_fixture):
    state, record, *_ = bound_fixture
    state.data['calls'].append({'id': 'pending_other', 'name': 'sf_0_blind', 'status': 'submitted'})
    with pytest.raises(LibraryStopped, match='new_or_pending_outcome_unknown'):
        budget.record_request_bound_assembly(state)
    state.data['calls'].pop()
    policy = register(bound_fixture)
    state.data['calls'].append({'id': 'independent_source', 'name': policy['next_stage'], 'status': 'submitted'})
    assert request_bound_assembly(state.output, state.data, record) == policy
    assert budget._parent_stops(state.output, state.data, record)[3]['repair_call_id'] == policy['repair_call_id']
    if shutil.which('node'):
        checked = invoke(state, record, function='parentProtocolStops', options={'boundAssembly': policy})
        assert checked.returncode == 0, checked.stderr


def test_third_assembly_is_blocked_and_bound_result_never_lifts_new_stop(bound_fixture):
    state, record, *_ = bound_fixture
    policy = register(bound_fixture)
    request = json.loads((state.output / 'calls/synthetic_assembly_0/request.json').read_text(encoding='utf-8'))
    with pytest.raises(LibraryStopped, match='no_third_assembly'):
        budget.SlotFinecutState._check_input(state, 'sf_3_assemble', request, record)
    state.data['calls'].append({'id': 'forbidden', 'name': 'sf_3_review', 'status': 'submitted'})
    with pytest.raises(LibraryStopped, match='resume_stage'):
        request_bound_assembly(state.output, state.data, record)
    state.data['calls'].pop()
    for attempt in (0, 1):
        ident = f'source_failed_{attempt}'
        response = {'result': {'content': [{'type': 'text', 'text': '{}'}]}}
        name = policy['next_stage'] + ('_repair' if attempt else '')
        state.data['calls'].append({'id': ident, 'name': name, 'status': 'received', 'request_sha256': json_sha(request),
            'response_sha256': json_sha(response), 'repair_of': 'source_failed_0' if attempt else None})
        for filename, value in (('request.json', request), ('response.json', response), ('protocol_failure.json', {'attempt': attempt})):
            write_json(state.output / 'calls' / ident / filename, value)
    stop = budget.SlotFinecutState.freeze_parent_protocol_failure(state, 3, 'model_protocol_repair_exhausted:' + policy['next_stage'])
    assert state.data['artifacts'].get('sf_parent_protocol_stop_3_assembly_bound_resume')
    assert budget._parent_stops(state.output, state.data, record)[3] == stop
    state.data['calls'].append({'id': 'forbidden_after_stop', 'name': 'sf_3_blind', 'status': 'submitted'})
    with pytest.raises(LibraryStopped, match='stopped_parent_cannot_repeat'):
        budget._parent_stops(state.output, state.data, record)
    if shutil.which('node'):
        checked = invoke(state, record, function='parentProtocolStops', options={'boundAssembly': policy})
        assert checked.returncode != 0 and 'stopped_parent_cannot_repeat' in checked.stderr


@pytest.mark.skipif(not shutil.which('node'), reason='Node mirror unavailable')
def test_node_mirror_preserves_python_numeric_sha_conventions(bound_fixture):
    state, record, body, *_ = bound_fixture
    policy = register(bound_fixture)
    checked = invoke(state, record)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout) == policy
    assert json.loads(checked.stdout)['model_body_sha256'] == json_sha(body)
