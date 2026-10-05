"""One local model proposal, actual synthetic rendering and immutable resume."""
from copy import deepcopy
import json
import shutil
import pytest

from omni_story.library import local_trim, flat_refinement, research_resume
from omni_story.library.goal_feedback_continuation import execute_goal_continuation
from test_library_execute import inputs, _bridge, _read
from test_library_goal_feedback_continuation import prepare, answer
from test_library_research_goal_execute import _research_answer, _all_bytes

pytestmark=pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='Needs FFmpeg')


def test_local_trim_forward_queue_independent_checks_actual_render_and_resume(inputs):
    reference,library,output=inputs
    prepare(inputs)
    research_resume.enable(output,'synthetic resumed research',first_round=6)
    for r in (5,6):
        with _bridge(output,answer(inputs,fail=f'active_{r}_draft')):
            assert execute_goal_continuation(reference,library,output,start_next=r==6)['new_renders']==0
    flat_refinement.enable(output,'synthetic forward serialization')
    old_oracle=answer(inputs,fail='active_7_finecut')
    def legacy_reply(job):
        value=old_oracle(job)
        if job['job_id'].endswith('active_7_draft'):
            for s in value['segments']:s.pop('reason',None)
        return value
    with _bridge(output,legacy_reply):
        assert execute_goal_continuation(reference,library,output,start_next=True)['new_renders']==0
    old=deepcopy(_read(output/'library_state.json')['calls'])
    local_trim.enable(output,'synthetic local method, not a real model')
    oracle=_research_answer(inputs)
    def reply(job):
        stage=job['job_id'].split('_',2)[2]
        prompt=job['arguments']['prompt']
        if stage.startswith('active_8_trim_'):
            packet=json.JSONDecoder().raw_decode(prompt[prompt.index('{"parent_segment":'):])[0]
            parent=packet['parent_segment']
            cuts=[{'source_in_s':1.5,'source_out_s':2,'speed':2,'freeze_tail_s':0,
                'visible_information':'synthetic geometry','essential_interval':{'source_start_s':1.5,'source_end_s':2,'min_readable_s':.25},'reason':'first state'},
                {'source_in_s':3,'source_out_s':3.5,'speed':.5,'freeze_tail_s':.2,
                'visible_information':'synthetic geometry','essential_interval':{'source_start_s':3,'source_end_s':3.5,'min_readable_s':1},'reason':'second state'}]
            intervals=[(parent['source_in_s'],1.5,'omit'),(1.5,2,'keep'),(2,3,'omit'),(3,3.5,'keep'),(3.5,parent['source_out_s'],'omit')]
            return {'parent_segment_id':parent['segment_id'],'kept_slices':cuts,
                'deletion_checks':[{'source_start_s':a,'source_end_s':b,'decision':d,'information_lost':'fixture information','reason':'synthetic decision'}
                    for a,b,d in intervals if a<b],'obligation_status':'preserved','limitations':[],'uncertainties':[]}
        if stage.startswith('active_8_finecut'):
            inp=json.JSONDecoder().raw_decode(prompt[prompt.index('{"original_draft":'):])[0]
            assert len(inp['observations']['local_trim_proposals'])==1
            assert 'actual_feedback' not in inp['observations']
            shim=deepcopy(job); shim['arguments']['prompt']=json.dumps({'policy':'test only','draft':inp['original_draft'],
                'evidence':inp['observations'],'test_note':'timing_checks evidence_timing_refinement_v1'})
            return oracle(shim)
        if stage.startswith('semantic_claims_8_'):
            inp=json.JSONDecoder().raw_decode(prompt[prompt.index('{"observation":'):])[0]
            shim=deepcopy(job); shim['arguments']['prompt']='\nobservation：'+json.dumps(inp['observation'])+\
                '\nrequired_claims：'+json.dumps(inp['required_claims'])+'\nrole_hypotheses：'+json.dumps(inp['role_hypotheses'])
            return oracle(shim)
        return oracle(job)
    with _bridge(output,reply) as jobs:
        result=execute_goal_continuation(reference,library,output,start_next=True)
        assert result['selected_round']==8 and result['new_renders']==1 and result['active_finecut_gate_passed']
        assert not any('active_8_draft' in j['job_id'] for j in jobs)
        assert len([j for j in jobs if 'active_8_trim_' in j['job_id']])==1
        assert all('explicit_slice_claim_check_v1' not in j['arguments']['prompt'] or 'uncertainties' in j['arguments']['prompt'] for j in jobs)
        count=len(jobs); before=_all_bytes(output)
        assert execute_goal_continuation(reference,library,output)==result
        assert len(jobs)==count and _all_bytes(output)==before
    assert _read(output/'library_state.json')['calls'][:len(old)]==old
