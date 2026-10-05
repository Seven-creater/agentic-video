"""Synthetic FFmpeg and the production queue; no GLM quality claims."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import pytest

from omni_story.library import goal_budget as goal
from omni_story.library.goal_feedback_continuation import execute_goal_continuation, substantive_edl
from omni_story.library.media import probe_media
from omni_story.library.state import LibraryStopped
from test_library_execute import _bridge, _read, inputs
from test_library_finecut_continuation import _setup
from test_library_active_finecut_execute import _responses

pytestmark=pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='Requires FFmpeg/FFprobe')


def answer(inputs,*,fail=None,variant=False,redundant=False):
    reference,library,output=inputs
    base=_responses(reference,library)
    def response(job):
        name=job['job_id'].split('_',2)[2]
        stem=name.removesuffix('_repair')
        if fail and stem==fail:
            return {'invalid':'known synthetic format failure'}
        phase=stem.rsplit('_',1)[1] if stem.startswith('active_') else None
        target={'draft':'plan_0','finecut':'finecut_0','blind':'blind_0','economy':'economy_0','review':'review_0'}.get(phase,name)
        value=base({**job,'job_id':'glm_000_'+target})
        if phase=='finecut' and variant:
            value['plan']['segments'][0]['source_out_s']=1.9
            value['duration']['total_s']=1.4
        if phase=='economy':
            prompt=job['arguments']['prompt']
            actual=json.JSONDecoder().raw_decode(prompt[prompt.index('{"actual_output":'):])[0]['actual_output']
            value['microcut_checks']=[{'segment_id':s['segment_id'],'status':'redundant' if redundant else 'necessary',
                'necessary_intervals':[{'start_s':s['output_in_s'],'end_s':s['output_out_s'],
                    'observed_fact':'synthetic actual square stays visible'}],
                'removable_intervals':[{'start_s':s['output_in_s'],'end_s':s['output_out_s'],
                    'observed_fact':'synthetic redundant span'}] if redundant else [],'reason':'independent synthetic instant check'}
                for s in actual['provenance']]
            if redundant: value['economy_status']='partial'
        if phase=='review':
            for check in value['fact_checks']:
                check['blind_evidence_ids']=['blind_visible']
        return value
    return response


def prepare(inputs):
    _setup(inputs)
    return goal.authorize(inputs[2],'synthetic user asks Goal completion; no paid models')


def test_actual_round5_cache_round6_exact_evidence_reuse_and_duplicate_stop(inputs):
    reference,library,output=inputs
    policy=prepare(inputs)
    goal.stage_state(output).set_artifact('goal_round_5_history_protection_v1', {'synthetic_protection_alias': True})
    old=deepcopy(_read(output/'library_state.json')['calls'])
    protected={Path(r['path']):Path(r['path']).read_bytes() for r in policy['protected_files']}
    with _bridge(output,answer(inputs)) as jobs:
        first=execute_goal_continuation(reference,library,output)
        assert first['selected_round']==5 and first['new_renders']==1
        assert first['active_finecut_gate_passed'] is True
        assert first['human_quality_confirmation'] is False
        assert probe_media(first['final_video'])['duration_s']==pytest.approx(1.466667,abs=.05)
        assert len(jobs)==9
        count=len(jobs)
        assert execute_goal_continuation(reference,library,output)==first and len(jobs)==count
        with pytest.raises(LibraryStopped,match='library_file_set_changed'):
            execute_goal_continuation(library/'a.mkv',library,output)
        assert len(jobs)==count
    with _bridge(output,answer(inputs,variant=True)) as jobs:
        second=execute_goal_continuation(reference,library,output,start_next=True)
        assert second['selected_round']==6 and second['active_finecut_gate_passed'] is True
        assert len(jobs)==7  # second unchanged source fact+claims reused, not paid again
        assert len([j for j in jobs if 'semantic_slice_6_' in j['job_id']])==1
        assert len([j for j in jobs if 'semantic_claims_6_' in j['job_id']])==1
    with _bridge(output,answer(inputs,variant=True)) as jobs:
        stopped=execute_goal_continuation(reference,library,output,start_next=True)
        assert stopped['status']=='stopped_no_new_edit' and stopped['new_renders']==0
        assert len(jobs)==2 and not (output/'render_7').exists()
        assert execute_goal_continuation(reference,library,output)==stopped and len(jobs)==2
        with pytest.raises(ValueError,match='no_progress_stop'):
            execute_goal_continuation(reference,library,output,start_next=True)
    live=_read(output/'library_state.json')
    assert live['calls'][:len(old)]==old and live['max_requests']==80
    assert all(path.read_bytes()==content for path,content in protected.items())


def test_known_prerender_failure_is_real_terminal_feedback_then_new_round(inputs):
    reference,library,output=inputs
    prepare(inputs)
    with _bridge(output,answer(inputs,fail='active_5_draft')) as jobs:
        result=execute_goal_continuation(reference,library,output)
        assert result['status']=='stopped_protocol_failure' and result['new_renders']==0
        assert len(jobs)==2 and not (output/'render_5').exists()
    with _bridge(output,answer(inputs,redundant=True)) as jobs:
        result=execute_goal_continuation(reference,library,output,start_next=True)
        assert result['selected_round']==6 and result['active_finecut_gate_passed'] is False
        draft=next(j for j in jobs if j['job_id'].endswith('active_6_draft'))
        assert 'protocol_failure' in draft['arguments']['prompt']
        assert 'actual_render_exists' in draft['arguments']['prompt']
        assert len(jobs)==9 and (output/'render_6/final.mp4').is_file()


def test_missing_goal_grant_before_files_or_queue(inputs):
    _setup(inputs)
    reference,library,output=inputs
    before={p:p.read_bytes() for p in output.rglob('*') if p.is_file()}
    with pytest.raises(LibraryStopped,match='authorization_required'):
        execute_goal_continuation(reference,library,output)
    assert {p:p.read_bytes() for p in output.rglob('*') if p.is_file()}==before
