"""Bounded replanning plumbing with synthetic media; no real calls or run writes."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import slot_source_feedback as feedback
from omni_story.library.state import json_sha, write_json
from test_library_slot_finecut_execute import prepared
from test_library_slot_finecut_assembly_binding import bound_fixture, register


@pytest.fixture
def feedback_fixture(bound_fixture):
    state, grant, body, parent, outline, proposals, catalog, reference, methods = bound_fixture
    binding = register(bound_fixture)
    operation = proposals[-1]['candidates'][0]['operations'][0]
    source = next(s for s in catalog['sources'] if s['source_id'] == parent['provenance'][-1]['source_id'])
    scope = {'kind': 'continuous_window', 'source_sha256': source['sha256'],
             'source_start_s': operation['source_in_s'], 'source_end_s': operation['source_out_s']}
    for index in (0, 1):
        ident = 'synthetic_exhausted_' + str(index)
        request = {'provider': 'official_vision_mcp_in_codex', 'tool': 'analyze_video',
                   'media_sha256': 'c' * 64, 'observation_scope': scope, 'arguments': {'prompt': 'Synthetic source check'}}
        reply = {'result': {'content': [{'type': 'text', 'text': '{}'}]}}
        for name, value in (('request.json', request), ('response.json', reply),
                            ('protocol_failure.json', {'attempt': index, 'error': 'Synthetic known field failure'})):
            write_json(state.output / 'calls' / ident / name, value)
        state.data['calls'].append({'id': ident, 'name': 'semantic_slice_3_synthetic' + ('_repair' if index else ''),
            'status': 'received', 'repair_of': 'synthetic_exhausted_0' if index else None,
            'request_sha256': json_sha(request), 'response_sha256': json_sha(reply)})
    state.data['request_count'] = len(state.data['calls'])
    before = {p: p.read_bytes() for p in (state.output / 'calls').glob('*/*')}
    policy = feedback.record_source_feedback(state, 'Synthetic test authorization; no real model is called.')
    assert all(p.read_bytes() == raw for p, raw in before.items())
    write_json(state.output / 'library_state.json', state.data)
    return state, grant, policy, parent, catalog, reference, methods


def planned(fixture):
    _, _, policy, parent, catalog, reference, methods = fixture
    slot = policy['original_outline']['slots'][-1]
    facts = {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'], 'slot_id': slot['slot_id'],
        'slot_start_s': slot['start_s'], 'slot_end_s': slot['end_s'], 'time_domain': 'slot_local_output',
        'evidence': [{'evidence_id': 'parent_e1', 'start_s': 0, 'end_s': slot['end_s'] - slot['start_s'],
                      'observed_fact': 'Synthetic geometric subject remains visible.', 'kind': 'shape', 'basis': 'visual'}],
        'limitations': [], 'uncertainties': []}
    proposal = deepcopy(policy['original_proposals'][-1])
    candidate = proposal['candidates'][0]
    candidate['candidate_id'] = 'feedback_c1'
    operation = candidate['operations'][0]
    operation['source_in_s'] += .1
    operation['source_out_s'] -= .1
    operation['essential_intervals'][0].update(source_start_s=operation['source_in_s'], source_end_s=operation['source_out_s'])
    assembly = deepcopy(policy['original_assembly'])
    segment = assembly['plan']['segments'][-1]
    old_id = segment['segment_id']
    segment.update(segment_id='feedback_source', source_in_s=operation['source_in_s'], source_out_s=operation['source_out_s'])
    assembly['plan']['slots'][-1].update(segment_ids=['feedback_source'], intended_takeaway=slot['intended_takeaway'])
    assembly['selections'][-1]['candidate_id'] = candidate['candidate_id']
    assembly['segment_bindings'][-1].update(segment_id='feedback_source', candidate_id=candidate['candidate_id'])
    assembly['timing_checks'][-1]['segment_id'] = 'feedback_source'
    assembly['transition_checks'][-1]['to_segment_id'] = 'feedback_source'
    for binding in assembly['plan']['editing_bindings']:
        binding['segment_ids'] = ['feedback_source' if ident == old_id else ident for ident in binding['segment_ids']]
    value = {'status': 'planned', 'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
        'change_reason': 'Synthetic distinct source geometry for contract testing only.',
        'revised_proposals': [proposal], 'replacement_assembly': assembly, 'limitations': []}
    return value, facts


def validate(value, facts, fixture):
    _, _, policy, parent, catalog, reference, methods = fixture
    return feedback.validate_replan(value, policy, parent, catalog, parent['allowed_windows'], reference, methods, facts)


def test_one_planned_microcut_passes_real_contracts_and_preserves_original_body(feedback_fixture):
    value, facts = planned(feedback_fixture)
    before = deepcopy(feedback_fixture[2])
    assert validate(value, facts, feedback_fixture) is value
    assert feedback_fixture[2] == before
    proposals = feedback.revised_full_proposals(value, before)
    assert proposals[:-1] == before['original_proposals'][:-1]
    assert proposals[-1] == value['revised_proposals'][0]


@pytest.mark.parametrize('mutation,error', [
    ('full_range', 'proper_ordered_microcuts'), ('lower_min', 'minimum_changed'),
    ('information', 'minimum_changed'), ('same_essential', 'essential_interval'),
    ('unblocked_segment', 'unblocked_segments'), ('old_segment_id', 'new_source_segment_ids'),
    ('target_words', 'original_slot_obligation'), ('global_audio', 'unrelated_plan_settings'),
    ('old_candidate_id', 'new_candidate_id'), ('unblocked_method', 'unblocked_method_bindings')])
def test_replan_cannot_relax_evidence_or_change_unblocked_edit(feedback_fixture, mutation, error):
    value, facts = planned(feedback_fixture)
    operation = value['revised_proposals'][0]['candidates'][0]['operations'][0]
    original = feedback_fixture[2]['original_operation']
    assembly = value['replacement_assembly']
    if mutation == 'full_range':
        operation.update(source_in_s=original['source_in_s'], source_out_s=original['source_out_s'])
    elif mutation == 'lower_min':
        operation['essential_intervals'][0]['min_readable_s'] = .05
    elif mutation == 'information':
        operation['essential_intervals'][0]['information'] = 'Different required information'
    elif mutation == 'same_essential':
        operation['essential_intervals'] = deepcopy(original['essential_intervals'])
    elif mutation == 'unblocked_segment':
        assembly['plan']['segments'][0]['look'] = 'grayscale'
    elif mutation == 'old_segment_id':
        for row in [assembly['plan']['segments'][-1], assembly['segment_bindings'][-1], assembly['timing_checks'][-1]]:
            row['segment_id'] = 'seg_1'
        assembly['plan']['slots'][-1]['segment_ids'] = ['seg_1']
        assembly['transition_checks'][-1]['to_segment_id'] = 'seg_1'
        assembly['plan']['editing_bindings'][0]['segment_ids'][-1] = 'seg_1'
    elif mutation == 'target_words':
        assembly['plan']['slots'][-1]['intended_takeaway'] = 'Changed creative obligation'
    elif mutation == 'global_audio':
        assembly['plan']['source_gain_db'] = -1
    elif mutation == 'old_candidate_id':
        value['revised_proposals'][0]['candidates'][0]['candidate_id'] = 'c1'
    elif mutation == 'unblocked_method':
        assembly['plan']['editing_bindings'][0]['segment_ids'] = ['feedback_source']
    with pytest.raises(ValueError, match=error):
        validate(value, facts, feedback_fixture)


def test_unavailable_stops_without_plan(feedback_fixture):
    value, facts = planned(feedback_fixture)
    value.update(status='unavailable', revised_proposals=[], replacement_assembly=None, limitations=['Synthetic source unavailable.'])
    assert validate(value, facts, feedback_fixture) is value
    value['limitations'] = []
    with pytest.raises(ValueError, match='unavailable_must_stop'):
        validate(value, facts, feedback_fixture)


@pytest.mark.parametrize('removed_frames', [0.0001, 1])
def test_numeric_or_one_frame_trim_cannot_bypass_exhausted_source(feedback_fixture, removed_frames):
    value, facts = planned(feedback_fixture)
    old = feedback_fixture[2]['original_operation']
    fps = feedback_fixture[3]['render_input']['compiled']['fps']
    operation = value['revised_proposals'][0]['candidates'][0]['operations'][0]
    operation['source_in_s'] = old['source_in_s']
    operation['source_out_s'] = old['source_out_s'] - removed_frames / fps
    operation['essential_intervals'][0].update(source_start_s=operation['source_in_s'], source_end_s=operation['source_out_s'])
    value['replacement_assembly']['plan']['segments'][-1].update(
        source_in_s=operation['source_in_s'], source_out_s=operation['source_out_s'])
    with pytest.raises(ValueError, match='epsilon_or_one_frame_trim_is_not_progress'):
        validate(value, facts, feedback_fixture)


def test_policy_is_idempotent_and_preserves_prefix_and_protected_bytes(feedback_fixture):
    state, grant, policy, *_ = feedback_fixture
    assert feedback.record_source_feedback(state, 'Same authorization remains active') == policy
    protected = Path(policy['protected_files'][0]['path'])
    before = protected.read_bytes()
    protected.write_bytes(before + b' ')
    with pytest.raises(ValueError, match='history_changed|rewritten'):
        feedback.read_validate(state.output, state.data, grant)
    protected.write_bytes(before)
    state.data['calls'][0]['name'] = 'Changed historical call'
    with pytest.raises(ValueError, match='prefix_changed'):
        feedback.read_validate(state.output, state.data, grant)


def test_feedback_stage_cannot_repeat_original_or_request_third_repair(feedback_fixture):
    state, grant, policy, *_ = feedback_fixture
    state.data['calls'].append({'id': 'feedback_original', 'name': feedback.STAGE, 'status': 'received', 'repair_of': None})
    state.data['calls'].append({'id': 'feedback_repair', 'name': feedback.STAGE + '_repair', 'status': 'received', 'repair_of': 'feedback_original'})
    assert feedback.read_validate(state.output, state.data, grant) == policy
    state.data['calls'].append({'id': 'third_paid_attempt', 'name': feedback.STAGE, 'status': 'submitted', 'repair_of': None})
    with pytest.raises(ValueError, match='no_replanning_loop'):
        feedback.read_validate(state.output, state.data, grant)


@pytest.mark.parametrize('name', ['sf_3_assemble', 'semantic_slice_21_new', 'semantic_claims_21_new'])
def test_first_new_parent_stage_must_be_source_feedback(feedback_fixture, name):
    state, grant, _, *_ = feedback_fixture
    state.data['calls'].append({'id': 'wrong_first_stage', 'name': name, 'status': 'submitted', 'repair_of': None})
    with pytest.raises(ValueError, match='first_stage_changed'):
        feedback.read_validate(state.output, state.data, grant)


@pytest.mark.parametrize('field,value', [('old_failures_preserved', False), ('semantic_truth_established', True),
                                       ('user_instruction', ' ')])
def test_policy_checks_are_identical_even_if_artifact_digest_is_rebound(feedback_fixture, field, value):
    state, grant, policy, *_ = feedback_fixture
    row = state.data['artifacts'][feedback.POLICY][0]
    changed = {**policy, field: value}
    write_json(Path(row['path']), changed)
    row['sha256'] = json_sha(changed)
    with pytest.raises(ValueError, match='policy_changed|text_required'):
        feedback.read_validate(state.output, state.data, grant)


def test_pending_or_unknown_cannot_be_frozen_as_new_feedback_baseline(feedback_fixture):
    state, grant, policy, *_ = feedback_fixture
    state.data['calls'][-1]['status'] = 'uncertain'
    row = state.data['artifacts'][feedback.POLICY][0]
    changed = {**policy, 'prefix_calls_sha256': json_sha(state.data['calls'])}
    write_json(Path(row['path']), changed)
    row['sha256'] = json_sha(changed)
    with pytest.raises(ValueError, match='pending_or_unknown_blocks_activation'):
        feedback.read_validate(state.output, state.data, grant)


@pytest.mark.skipif(not shutil.which('node'), reason='Node guard requires Node.js')
def test_node_helper_accepts_same_bound_policy_and_rejects_changed_history(feedback_fixture):
    state, grant, _, *_ = feedback_fixture
    module = Path('omni_story/library/mcp_slot_source_feedback.mjs').resolve().as_uri()
    code = r'''const fs=await import('node:fs'),crypto=await import('node:crypto');
const read=p=>JSON.parse(fs.readFileSync(p,'utf8'));
class N{constructor(s){this.s=s;}}
function fileDigest(p,select=v=>v){const v=JSON.parse(fs.readFileSync(p,'utf8'),(k,v,c)=>typeof v==='number'?new N(c.source):v);
function serialize(v){if(v instanceof N)return v.s;if(Array.isArray(v))return '['+v.map(serialize).join(',')+']';
if(v&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+serialize(v[k])).join(',')+'}';return JSON.stringify(v);}
return crypto.createHash('sha256').update(serialize(select(v))).digest('hex');}
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const scope=v=>JSON.stringify([v.kind,v.source_sha256,v.source_start_s,v.source_end_s]);
try{const module=await import(process.argv[1]);const state=read(process.argv[2]+'/library_state.json');
const result=module.validateSourceFeedback(process.argv[2],state,JSON.parse(process.argv[3]),{read,fileDigest,hash,scope});
process.stdout.write(result.stage);}catch(e){process.stderr.write(e.message);process.exitCode=3;}'''
    def invoke():
        return subprocess.run(['node', '--input-type=module', '-e', code, module, str(state.output), json.dumps(grant)],
                              text=True, capture_output=True, timeout=30)
    result = invoke()
    assert result.returncode == 0, result.stderr
    assert result.stdout == feedback.STAGE
    state.data['calls'].append({'id': 'wrong_first_stage', 'name': 'semantic_slice_21_new', 'status': 'submitted'})
    write_json(state.output / 'library_state.json', state.data)
    result = invoke()
    assert result.returncode != 0 and 'first_stage_changed' in result.stderr
    state.data['calls'].pop()
    write_json(state.output / 'library_state.json', state.data)
    policy = _read(Path(state.data['artifacts'][feedback.POLICY][0]['path']))
    Path(policy['protected_files'][0]['path']).write_text('Changed synthetic protected file')
    result = invoke()
    assert result.returncode != 0 and 'history_changed' in result.stderr


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))
