"""Actual media/queue plumbing, with synthetic decisions rather than GLM quality."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library.active_finecut import POLICY, enable_policy, plan_budget, prepare_existing_task
from omni_story.library.media import inventory_sources, probe_media, sha256_file
from omni_story.library.pipeline import execute
from omni_story.library.state import LibraryState, LibraryStopped
from test_library_execute import _bridge, _read, _semantic_fixture_responses, inputs

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Real FFmpeg/FFprobe integration requires both tools')


def _responses(reference, library, *, redundant=False, malformed=False, missing_obligation=False):
    base = _semantic_fixture_responses(reference, library)
    def answer(job):
        name = job['job_id'].split('_', 2)[2]
        prompt = job['arguments']['prompt']
        if name.startswith('finecut_'):
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"policy":'):])[0]
            draft = context['draft']
            final = deepcopy(draft)
            first = final['segments'][0]
            first.pop('caption')
            first.pop('freeze_tail_s')
            second = deepcopy(first)
            first.update(source_in_s=1.5, source_out_s=2, speed=2)
            second.update(segment_id='segment_2', source_in_s=3, source_out_s=3.5, speed=.5, freeze_tail_s=.2,
                          visual_claims=[{'claim_id':'vc_second','kind':'visual_action','description':'square stays visible'}])
            final['segments'].append(second)
            final['slots'][0]['segment_ids'].append('segment_2')
            if missing_obligation:
                final['slots'][0].update(slot_id='restructured', intended_takeaway='new fixture formulation')
                for segment in final['segments']:
                    segment['slot_id'] = 'restructured'
            final['editing_bindings'][0].update(segment_ids=['segment_1','segment_2'],
                operation='two disjoint source slices; fast first, slow second with actual tail hold',
                verification='actual timing and visible geometry, not creative quality')
            value = {'plan':final, 'decisions':[
                {'segment_id':s['segment_id'], 'new_information':'synthetic visible state',
                 'in_out_reason':'fixture selected interval', 'speed_reason':'synthetic retiming test',
                 'hold_reason':'retain actual output tail or no hold', 'origin':'general_optimization',
                 'reference_method_ids':[]} for s in final['segments']],
                'obligation_coverage':[{'slot_id':draft['slots'][0]['slot_id'], 'segment_ids':['segment_1','segment_2'],
                    'status':'preserved','reason':'synthetic obligation, not a narrative judgment'}],
                'draft_dispositions':[{'segment_id':'segment_1','decision':'replaced',
                    'replacement_segment_ids':['segment_1','segment_2'],'reason':'shorter disjoint fixture cuts'}],
                'duration':{'target_s':1.45,'total_s':1.45,'over_target_reason':''}}
            if malformed and name == 'finecut_0':
                value['decisions'].pop()
            return value
        if name.startswith('economy_'):
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"actual_output":'):])[0]
            actual = context['actual_output']
            return {'video_sha256':actual['sha256'], 'economy_status':'partial' if redundant else 'pass',
                'narrative_readability':'pass','segment_checks':[
                    {'segment_id':s['segment_id'], 'status':'redundant' if redundant else 'necessary',
                     'output_evidence':[{'start_s':s['output_in_s'],'end_s':s['output_out_s'],
                                        'observed_fact':'fixture visible colored geometry'}],
                     'reason':'synthetic independent economy judgment'} for s in actual['provenance']],
                'limitations':['fixture replies do not establish creative quality']}
        value = base(job)
        if name.startswith('review_0'):
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
            duration = context['output_duration_s']
            value['method_checks'][0]['output_evidence'] = [{'start_s':max(0,duration-.2),'end_s':duration,
                'observed_fact':'actual tail remains visible'}]
            for check in value['fact_checks']:
                if check['claim_id'] == 'vc_second':
                    check['evidence_refs'][0]['segment_id'] = 'segment_2'
            if missing_obligation:
                original_ids = {c['claim_id'] for c in context['required_claims'] if c.get('origin') == 'draft_obligation'}
                assert original_ids
                for check in value['fact_checks']:
                    if check['claim_id'] in original_ids:
                        check.update(status='unsupported', evidence_refs=[],
                                     limitations=['original information absent despite new slot claim'])
                value.update(theme_status='partial', visual_narrative_status='partial')
        return value
    return answer


@pytest.mark.parametrize('redundant,malformed,missing_obligation', [
    (False,False,False),(True,False,False),(False,True,False),(False,False,True)])
def test_active_refinement_real_disjoint_cuts_independent_review_and_resume(inputs, redundant, malformed, missing_obligation):
    reference, library, output = inputs
    def run(flag):
        return execute(reference,library,output,span_s=3,frames=2,max_fine=1,max_requests=34,
                       asr=False,active_finecut=flag)
    with _bridge(output,_responses(reference,library,redundant=redundant,malformed=malformed,
                                  missing_obligation=missing_obligation)) as jobs:
        result = run(True)
        count = len(jobs)
        assert count == result['usage']['requests'] == (18 if malformed else 17)
        assert result['active_finecut_protocol'] == POLICY
        assert result['active_finecut_gate_passed'] is (not (redundant or missing_obligation))
        assert result['status'] == ('library_candidate_with_limitations' if redundant or missing_obligation else 'model_checked_library_candidate')
        draft, final = _read(output/'draft_plan_0.json'), _read(output/'plan_0.json')
        assert len(draft['segments']) == 1 and len(final['segments']) == 2
        actual = _read(output/'render_0/render_result.json')
        assert [(s['source_in_s'],s['source_out_s'],s['speed']) for s in actual['provenance']] == [(1.5,2,2),(3,3.5,.5)]
        assert actual['provenance'][-1]['freeze_tail_s'] == .2
        assert probe_media(result['final_video'])['duration_s'] == pytest.approx(1.466667,abs=.05)
        manifest = _read(result['semantic_evidence_path'])
        observations = manifest['observations']
        assert {(s['source_in_s'],s['source_out_s']) for s in observations} == {(1.5,2),(3,3.5)}
        original_claim = next(c for c in manifest['required_claims'] if c.get('origin') == 'draft_obligation')
        assert original_claim['description'] == draft['slots'][0]['intended_takeaway']
        assert original_claim['segment_ids'] == ['segment_1','segment_2']
        if missing_obligation:
            assert next(c for c in result['review']['fact_checks'] if c['claim_id'] == original_claim['claim_id'])['status'] == 'unsupported'
        economy_job = next(j for j in jobs if j['job_id'].endswith('_economy_0'))
        economy_prompt = economy_job['arguments']['prompt']
        economy_context = json.JSONDecoder().raw_decode(economy_prompt[economy_prompt.index('{"actual_output":'):])[0]
        assert 'fixture selected interval' not in economy_prompt
        assert not {'reference','draft','plan'} & set(economy_context)
        blind_job = next(j for j in jobs if j['job_id'].endswith('_blind_0'))
        assert 'slot_1' not in blind_job['arguments']['prompt']
        state = _read(output/'library_state.json')
        budget = _read(state['artifacts']['active_finecut_plan_budget_0'][0]['path'])
        assert budget['reserved_fixed_requests'] == 12 and budget['reserved_per_segment'] == 4
        before = {p:p.read_bytes() for p in output.rglob('*') if p.is_file() and p.name != 'current_status.json'}
        assert run(False) == result
        assert len(jobs) == count
        assert all(p.read_bytes() == content for p, content in before.items())
    assert len(list(output.glob('render_*/final.mp4'))) == 1
    assert sha256_file(result['final_video']) == result['final_sha256']


def test_active_budget_allocations_survive_lower_remaining_budget(tmp_path):
    state = LibraryState(tmp_path/'task', {'reference_sha256':'a'*64},max_requests=34)
    maximum = plan_budget(state,0)
    assert maximum == 5
    state.begin_call('known_pending', {'test':'synthetic accounting only'})
    assert plan_budget(state,0) == maximum
    state2 = LibraryState(tmp_path/'other', {'reference_sha256':'b'*64},max_requests=15)
    with pytest.raises(LibraryStopped,match='insufficient_budget'):
        plan_budget(state2,0)


def test_preflight_is_cpu_only_idempotent_and_does_not_authorize_old_task(inputs):
    reference, library, output = inputs
    source = inventory_sources(reference,output/'reference_catalog')['sources'][0]
    sources = inventory_sources(library,output/'catalog')['sources']
    lock = {'reference_sha256':source['sha256'], 'library_sources':[
        {k:s[k] for k in ('source_id','sha256')} for s in sources]}
    state = LibraryState(output,lock,max_requests=34)
    call, _ = state.begin_call('plan_0', {'synthetic':'accounting fixture only'})
    state.complete_call(call, {'status':'complete','result':{'content':[]}})
    (output/'render_0').mkdir()
    old = output/'render_0/final.mp4'
    old.write_bytes(b'prior render fixture, not playable media')
    before_calls = deepcopy(state.data['calls'])
    proposal = prepare_existing_task(output,reference=reference,library=library)
    assert proposal['new_requests'] == proposal['new_renders'] == 0
    assert proposal['additional_render_authorized'] is False
    assert proposal['proposed_max_additional_requests'] == sum(proposal['reservation'].values()) == 44
    assert prepare_existing_task(output,reference=reference,library=library) == proposal
    assert state.usage()['requests'] == 1 and state.max_requests == 34
    assert state.data['calls'] == before_calls
    assert old.read_bytes() == b'prior render fixture, not playable media'
    assert len(state.data['artifacts']['active_finecut_preflight']) == 1
    assert not state.data['artifacts'].get('active_finecut_policy')
    with pytest.raises(LibraryStopped,match='cannot_reinterpret_paid_plans'):
        enable_policy(state,True)
