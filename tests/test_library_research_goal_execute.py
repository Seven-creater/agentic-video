"""Research-timing policy through production queues and synthetic FFmpeg media."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library import goal_budget, research_resume
from omni_story.library.goal_feedback_continuation import execute_goal_continuation
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import json_sha
from test_library_execute import _bridge, _read, inputs
from test_library_goal_feedback_continuation import prepare, answer

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Production media integration requires FFmpeg/FFprobe')


def _name(job):
    return job['job_id'].split('_', 2)[2].removesuffix('_repair')


def _with_timing(value):
    """Fixture decisions use their own final plan, never hand-picked real cuts."""
    value['timing_checks'] = []
    for segment in value['plan']['segments']:
        exposure = (segment['source_out_s'] - segment['source_in_s']) / segment['speed']
        value['timing_checks'].append({'segment_id': segment['segment_id'], 'purpose': 'action',
            'essential_source_intervals': [{'source_start_s': segment['source_in_s'],
                'source_end_s': segment['source_out_s'], 'visible_information': 'Synthetic visible geometry.',
                'claim_ids': [c['claim_id'] for c in segment['visual_claims']], 'min_readable_s': exposure}],
            'min_readable_s': exposure, 'reason': 'Fixture model-estimated exposure, not a human threshold.'})
    value['transition_checks'] = [{'from_segment_id': left['segment_id'], 'to_segment_id': right['segment_id'],
        'relation': 'Synthetic geometry is retained across disjoint views.', 'status': 'planned',
        'reason': 'Actual output still needs independent review.'}
        for left, right in zip(value['plan']['segments'], value['plan']['segments'][1:])]
    return value


def _research_answer(inputs, *, unsupported_kind=None, variant=False):
    base = answer(inputs, variant=variant)
    def response(job):
        name = _name(job)
        value = base(job)
        if name.startswith('active_') and name.endswith('_finecut'):
            assert 'timing_checks' in job['arguments']['prompt']
            assert 'evidence_timing_refinement_v1' in job['arguments']['prompt']
            if unsupported_kind == 'caption':
                segment = value['plan']['segments'][0]
                segment['caption'] = {'text': 'cue', 'start_s': 0, 'end_s': .2,
                    'position': 'bottom', 'font_size': 12,
                    'evidence': [{'window_id': segment['window_id'], 'event_indices': [0]}]}
            return _with_timing(value)
        if name.startswith('semantic_claims_') and unsupported_kind:
            prompt = job['arguments']['prompt']
            claims = json.loads(prompt.split('\nrequired_claims：', 1)[1].split('\nrole_hypotheses：', 1)[0])
            kinds = {c['claim_id']: c['kind'] for c in claims}
            for check in value['claim_checks']:
                if kinds[check['claim_id']] == unsupported_kind:
                    check.update(status='unsupported', evidence_ids=[],
                        reason='synthetic_source_counterevidence_' + unsupported_kind,
                        limitations=['Independent fixture comparison lacks this exact claim.'])
        if name.startswith('active_') and name.endswith('_review') and unsupported_kind:
            prompt = job['arguments']['prompt']
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
            unsupported = {c['claim_id'] for record in context['segment_checks']
                           for c in record['claim_checks'] if c['status'] != 'supported'}
            for check in value['fact_checks']:
                if check['claim_id'] in unsupported:
                    check.update(status='unsupported', evidence_refs=[], blind_evidence_ids=[],
                        limitations=['The output review cannot promote a failed source claim.'])
            value.update(theme_status='partial', editing_status='partial', visual_narrative_status='partial')
        return value
    return response


def _protected(policy):
    return {Path(row['path']): Path(row['path']).read_bytes() for row in policy['protected_files']}


def _all_bytes(output):
    return {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}


def _assert_historical(output, original_calls, protected):
    state = _read(output / 'library_state.json')
    assert state['calls'][:len(original_calls)] == original_calls
    assert state['max_requests'] == 80
    assert all(path.read_bytes() == content for path, content in protected.items())


def test_research_refinement_full_render_paid_context_and_zero_call_resume(inputs):
    reference, library, output = inputs
    policy = prepare(inputs)
    original = deepcopy(_read(output / 'library_state.json')['calls'])
    protected = _protected(policy)
    research = research_resume.enable(output, 'synthetic user requests evidence timing', first_round=5)
    with _bridge(output, _research_answer(inputs)) as jobs:
        result = execute_goal_continuation(reference, library, output)
        assert result['selected_round'] == 5 and result['new_renders'] == 1
        assert result['active_finecut_gate_passed'] is True
        assert result['human_quality_confirmation'] is False
        assert len(jobs) == 9
        assert probe_media(result['final_video'])['duration_s'] == pytest.approx(1.466667, abs=.05)
        assert sha256_file(result['final_video']) == result['final_sha256']
        refinement = _read(output / 'artifacts/goal_feedback_round_5/refinement.json')
        assert len(refinement['timing_checks']) == len(refinement['plan']['segments']) == 2
        assert len(refinement['transition_checks']) == 1
        live = _read(output / 'library_state.json')
        bound = live['artifacts']['goal_research_context_5'][0]
        context = _read(bound['path'])
        assert json_sha(context) == bound['sha256']
        assert context['knowledge_sha256'] == research['knowledge_sha256']
        finecut_job = next(j for j in jobs if _name(j) == 'active_5_finecut')
        request_context = json.JSONDecoder().raw_decode(
            finecut_job['arguments']['prompt'][finecut_job['arguments']['prompt'].index('{"policy":'):])[0]
        assert request_context['evidence']['research_refinement_evidence'] == context
        assert research['knowledge_sha256'] in finecut_job['arguments']['prompt']
        count = len(jobs)
        (output / 'mcp_ready.json').unlink()
        (output / 'mcp_stop').write_text('synthetic connection stopped', encoding='utf-8')
        before = _all_bytes(output)
        assert execute_goal_continuation(reference, library, output) == result
        assert len(jobs) == count
        assert _all_bytes(output) == before
    _assert_historical(output, original, protected)


def test_source_core_counterevidence_stops_before_render_and_reaches_next_model_draft(inputs):
    reference, library, output = inputs
    policy = prepare(inputs)
    original = deepcopy(_read(output / 'library_state.json')['calls'])
    protected = _protected(policy)
    research_resume.enable(output, 'synthetic evidence-first continuation', first_round=5)
    with _bridge(output, _research_answer(inputs, unsupported_kind='visual_action')) as jobs:
        stopped = execute_goal_continuation(reference, library, output)
        assert stopped['status'] == 'stopped_source_counterevidence'
        assert stopped['new_renders'] == 0 and stopped['actual_candidate'] is None
        assert stopped['model_goal_gate_passed'] is False
        assert stopped['details']['stage'] == 'independent_source_gate_before_render'
        assert stopped['details']['no_format_repair_for_semantic_counterevidence'] is True
        blockers = stopped['details']['blockers']
        assert {(b['segment_id'], b['claim_id'], b['status']) for b in blockers} == {
            ('segment_1', 'vc_stable', 'unsupported'), ('segment_2', 'vc_second', 'unsupported')}
        assert not (output / 'render_5').exists()
        assert len(jobs) == 6
        assert not any(_name(j).endswith(('_blind', '_economy', '_review')) for j in jobs)
        assert not any(j['job_id'].endswith('_repair') for j in jobs)
        before = _all_bytes(output)
        assert execute_goal_continuation(reference, library, output) == stopped
        assert len(jobs) == 6 and _all_bytes(output) == before
    round5_calls = deepcopy(_read(output / 'library_state.json')['calls'])
    round5_history = {p: p.read_bytes() for directory in (output / 'artifacts/goal_feedback_round_5',
                     output / 'semantic_audit/round_5') for p in directory.rglob('*') if p.is_file()}
    round5_history[output / 'result_goal_feedback_5.json'] = (output / 'result_goal_feedback_5.json').read_bytes()
    with _bridge(output, _research_answer(inputs, variant=True)) as jobs:
        next_result = execute_goal_continuation(reference, library, output, start_next=True)
        assert next_result['selected_round'] == 6
        draft = next(j for j in jobs if _name(j) == 'active_6_draft')
        assert 'synthetic_source_counterevidence_visual_action' in draft['arguments']['prompt']
        assert 'vc_stable' in draft['arguments']['prompt'] and 'unsupported' in draft['arguments']['prompt']
        # The unchanged second slice retains its failed claim check; cache
        # reuse cannot relabel it as supported and the new attempt also stops.
        assert next_result['status'] == 'stopped_source_counterevidence'
        assert {b['claim_id'] for b in next_result['details']['blockers']} == {'vc_second'}
        assert len(jobs) == 4
        assert not (output / 'render_6').exists()
    _assert_historical(output, round5_calls, round5_history)
    _assert_historical(output, original, protected)


@pytest.mark.parametrize('noncore', ['caption', 'role_presence'])
def test_noncore_source_failure_does_not_bypass_or_fail_core_gate(inputs, noncore):
    reference, library, output = inputs
    policy = prepare(inputs)
    original = deepcopy(_read(output / 'library_state.json')['calls'])
    protected = _protected(policy)
    research_resume.enable(output, 'synthetic timing with text distinction', first_round=5)
    with _bridge(output, _research_answer(inputs, unsupported_kind=noncore)) as jobs:
        result = execute_goal_continuation(reference, library, output)
        assert result['new_renders'] == 1 and (output / 'render_5/final.mp4').is_file()
        assert result['review_status'] == 'completed_protocol'
        assert result['active_finecut_gate_passed'] is False
        assert result['model_goal_gate_passed'] is False
        assert result['semantic_gate_passed'] is False
        assert len(jobs) == 9
        manifest = _read(result['semantic_evidence_path'])
        kinds = {c['claim_id']: c['kind'] for c in manifest['required_claims']}
        unsupported = [c for record in manifest['segment_checks'] for c in record['claim_checks'] if c['status'] == 'unsupported']
        assert unsupported and {kinds[c['claim_id']] for c in unsupported} == {noncore}
        assert all(c['status'] == 'supported' for record in manifest['segment_checks']
                   for c in record['claim_checks'] if kinds[c['claim_id']] in {'visual_action', 'visual_outcome', 'identity'})
        before = _all_bytes(output)
        assert execute_goal_continuation(reference, library, output) == result
        assert len(jobs) == 9 and _all_bytes(output) == before
    _assert_historical(output, original, protected)


def test_forward_research_from_round6_keeps_paid_round5_and_binds_received_source_context(inputs):
    reference, library, output = inputs
    policy = prepare(inputs)
    original = deepcopy(_read(output / 'library_state.json')['calls'])
    protected = _protected(policy)
    research = research_resume.enable(output, 'synthetic forward policy', first_round=6)
    with _bridge(output, answer(inputs)) as jobs:
        first = execute_goal_continuation(reference, library, output)
        assert len(jobs) == 9 and first['new_renders'] == 1
        assert not _read(output / 'library_state.json')['artifacts'].get('goal_research_context_5')
        assert 'timing_checks' not in _read(output / 'artifacts/goal_feedback_round_5/refinement.json')
    round5_calls = deepcopy(_read(output / 'library_state.json')['calls'])
    round5_bytes = {p: p.read_bytes() for directory in (output / 'artifacts/goal_feedback_round_5', output / 'render_5',
                   output / 'semantic_audit/round_5') for p in directory.rglob('*') if p.is_file()}
    with _bridge(output, _research_answer(inputs, variant=True)) as jobs:
        second = execute_goal_continuation(reference, library, output, start_next=True)
        assert second['selected_round'] == 6 and second['new_renders'] == 1
        assert second['active_finecut_gate_passed'] is True
        assert len(jobs) == 7
        state = goal_budget.stage_state(output)
        context_entry = state.data['artifacts']['goal_research_context_6'][0]
        context = _read(context_entry['path'])
        assert json_sha(context) == context_entry['sha256']
        assert context['knowledge_sha256'] == research['knowledge_sha256']
        assert len(context['source_observations']) == 2
        for bound in context['source_observations']:
            call = next(c for c in round5_calls if c['id'] == bound['call_id'])
            assert call['status'] == 'received' and call['name'].startswith('semantic_slice_5_')
            assert bound['response_sha256'] == call['response_sha256']
            parsed = _read(output / 'calls' / call['id'] / 'parsed.json')
            assert bound['original_observation'] == parsed
            assert bound['observation_sha256'] == json_sha(parsed)
        refinement_job = next(j for j in jobs if _name(j) == 'active_6_finecut')
        prompt = refinement_job['arguments']['prompt']
        request_context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"policy":'):])[0]
        assert request_context['evidence']['research_refinement_evidence'] == context
        before = _all_bytes(output)
        assert execute_goal_continuation(reference, library, output) == second
        assert len(jobs) == 7 and _all_bytes(output) == before
    _assert_historical(output, round5_calls, round5_bytes)
    _assert_historical(output, original, protected)
