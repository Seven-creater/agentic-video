"""Explicit skip-only recovery of one synthetic unknown request; no network."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from omni_story.library import visual_story_trial_state as trial
from omni_story.library.state import LibraryStopped, json_sha, write_json
from omni_story.library.media import sha256_file
from test_visual_story_trial_state import prepared, register, complete, read


@pytest.fixture
def stopped265(prepared):
    f=prepared
    state=trial.VisualStoryState(f.root)
    data=read(state.path)
    stages=['vss_observe','vss_inspect_0','vss_detail_0_0','vss_detail_0_1','vss_detail_0_2',
            'vss_inspect_1','vss_detail_1_0','vss_detail_1_1','vss_detail_1_2',
            'vss_inspect_2','vss_detail_2_0','vss_detail_2_1','vss_detail_2_2',
            'vss_inspect_3','vss_detail_3_0']
    for index,name in enumerate(stages):
        media=f.root/'artifacts'/f'new_media_{index}.bin'
        media.write_bytes(f'synthetic distinct media {index}'.encode())
        scope={'kind':'sparse_contact_sheet','source_sha256':f.grant['parent']['sha256'],
               'source_start_s':54.0 if index==14 else float(index),
               'source_end_s':55.5 if index==14 else float(index+1)}
        request={'provider':'official_vision_mcp_in_codex','tool':'analyze_image',
                 'arguments':{'image_source':str(media),'prompt':name},
                 'media_sha256':sha256_file(media),'observation_scope':scope}
        descriptor={'stage':name,'tool':'analyze_image','media_path':str(media),
                    'media_sha256':request['media_sha256'],'scope':scope,'purpose':'synthetic evidence'}
        descriptor_path=f.root/'artifacts'/f'vss_input_{name}.json'
        write_json(descriptor_path,descriptor)
        data['artifacts']['vss_input_'+name]=[{'path':str(descriptor_path),'sha256':json_sha(descriptor)}]
        count=251+index
        call={'id':f'glm_{count:03d}_{name}','name':name,'status':'received',
              'request_sha256':json_sha(request),'usage':{},'repair_of':None}
        folder=f.root/'calls'/call['id']
        write_json(folder/'request.json',request)
        if index==14:
            call.update(status='uncertain',error='synthetic ECONNRESET with no captured HTTP response')
            write_json(folder/'failure.json',{'uncertain':True,'error':call['error']})
        else:
            value={'observed':f'synthetic frame {index}'}
            response={'result':{'content':[{'type':'text','text':json.dumps(value)}]}}
            write_json(folder/'response.json',response)
            write_json(folder/'parsed.json',value)
            call['response_sha256']=json_sha(response)
        data['calls'].append(call)
    data['request_count']=265
    write_json(state.path,data)
    trial.get_auth(f.root,force=True)
    execution=Path(f.grant['execution_directory'])
    write_json(execution/'progress.json',{'controller':'stopped'})
    write_json(execution/'stopped.json',{'immutable':'prior unknown stop'})
    write_json(execution/'controller_turn_interrupt.json',{'interrupt':'after stop'})
    f.stopped_state=state
    f.lost_request=request
    f.lost_folder=folder
    f.execution=execution
    return f


def resume(f):
    return trial.record_unknown_resume(f.root,'那就继续：跳过未知265，不重放，继续原试验。')


def next_request(f,state,name='vss_detail_3_1',*,scope=None,media=None):
    if media is None:
        media=f.root/'artifacts'/f'{name}.bin'
        media.write_bytes(f'new independent media {name}'.encode())
    scope=scope or {'kind':'sparse_contact_sheet','source_sha256':f.grant['parent']['sha256'],
                    'source_start_s':55.5,'source_end_s':57.0}
    return register(f,state,name,scope=scope,media=media,prompt=name,image=True)


def test_explicit_resume_freezes265_and_appends_only_266(stopped265):
    f=stopped265
    baseline=(f.root/'library_state.json').read_bytes()
    old=read(f.root/'library_state.json')
    proof=resume(f)
    assert Path(proof['baseline_state_path']).read_bytes()==baseline
    assert proof['baseline_request_count']==265 and proof['new_renders']==0
    assert proof['skip_stages']==['vss_detail_3_0'] and proof['goal_resumed'] is False
    assert proof['lost_scope']==f.lost_request['observation_scope']
    current_bytes=(f.root/'library_state.json').read_bytes()
    assert resume(f)==proof and (f.root/'library_state.json').read_bytes()==current_bytes
    state=trial.VisualStoryState(f.root)
    call,_=state.begin_call('vss_detail_3_1',next_request(f,state))
    assert call['id']=='glm_266_vss_detail_3_1'
    complete(state,call)
    auth=trial.get_auth(f.root,force=True)
    new=read(state.path)
    assert auth['max_renders']==2 and auth['unknown_resume']['new_renders']==0
    assert new['calls'][:265]==old['calls'] and new['calls'][264]['status']=='uncertain'
    assert new['max_requests']==80 and new['request_count']==266
    assert not(f.lost_folder/'response.json').exists() and not(f.lost_folder/'parsed.json').exists()


def test_unknown265_blocks_without_explicit_resume(stopped265):
    f=stopped265
    state=trial.VisualStoryState(f.root)
    request=next_request(f,state)
    with pytest.raises(LibraryStopped,match='new_unknown_or_pending_no_replay'):
        state.begin_call('vss_detail_3_1',request)
    assert read(state.path)['request_count']==265


@pytest.mark.parametrize('kind',['sparse_contact_sheet','continuous_window','complete_file'])
@pytest.mark.parametrize('rounding',[0.0,0.0000005])
def test_lost_page_reencoding_kind_disguise_and_rounding_are_rejected(stopped265,kind,rounding):
    f=stopped265
    resume(f)
    state=trial.VisualStoryState(f.root)
    scope=deepcopy(f.lost_request['observation_scope'])
    scope.update(kind=kind,source_start_s=54.0+rounding,source_end_s=55.5+rounding)
    request=next_request(f,state,scope=scope)
    with pytest.raises(LibraryStopped,match='lost_265_input_no_replay'):
        state.begin_call('vss_detail_3_1',request)
    assert read(state.path)['request_count']==265


def test_lost_media_cannot_be_reused_with_changed_scope_and_prompt(stopped265):
    f=stopped265
    resume(f)
    state=trial.VisualStoryState(f.root)
    media=Path(f.lost_request['arguments']['image_source'])
    request=next_request(f,state,media=media)
    with pytest.raises(LibraryStopped,match='lost_265_input_no_replay'):
        state.begin_call('vss_detail_3_1',request)


def test_lost_original_and_its_repair_are_skipped_even_with_independent_media(stopped265):
    f=stopped265
    resume(f)
    state=trial.VisualStoryState(f.root)
    with pytest.raises(LibraryStopped,match='stage_already_recorded_reuse_cache|lost_265_input_no_replay'):
        state.begin_call('vss_detail_3_0',f.lost_request)
    request=deepcopy(f.lost_request)
    request['arguments']['prompt']='must not repair an unknown response'
    with pytest.raises(LibraryStopped,match='lost_265_input_no_replay'):
        state.begin_call('vss_detail_3_0_repair',request,repair_of=state.data['calls'][264])


@pytest.mark.parametrize('status',['submitted','uncertain'])
def test_only265_is_admitted_new_pending_or_unknown_still_stops(stopped265,status):
    f=stopped265
    resume(f)
    state=trial.VisualStoryState(f.root)
    call,_=state.begin_call('vss_detail_3_1',next_request(f,state))
    if status=='uncertain':
        state.fail_call(call,'synthetic subsequent unknown')
    request=next_request(f,state,'vss_detail_3_2',scope={
        'kind':'sparse_contact_sheet','source_sha256':f.grant['parent']['sha256'],
        'source_start_s':57.0,'source_end_s':58.0})
    with pytest.raises(LibraryStopped,match='new_unknown_or_pending_no_replay'):
        state.begin_call('vss_detail_3_2',request)


@pytest.mark.parametrize('mutation',['call','artifact','request_bytes','response_bytes','parsed_bytes',
                                    'call_file_addition','stopped','interrupt','snapshot','journal'])
def test_resume_265_prefix_and_old_files_are_immutable(stopped265,mutation):
    f=stopped265
    proof=resume(f)
    path=f.root/'library_state.json'
    if mutation in {'call','artifact'}:
        data=read(path)
        if mutation=='call':
            data['calls'][263]['usage']={'changed':1}
        else:
            data['artifacts']['vss_input_vss_inspect_3']=[]
        write_json(path,data)
    elif mutation in {'request_bytes','response_bytes','parsed_bytes','call_file_addition'}:
        folder=f.root/'calls'/'glm_264_vss_inspect_3'
        if mutation=='call_file_addition':
            write_json(folder/'invented.json',{'new':'not allowed inside old call'})
        else:
            target=folder/(mutation.removesuffix('_bytes')+'.json')
            target.write_bytes(target.read_bytes()+b'\n')
    elif mutation=='stopped':
        write_json(f.execution/'stopped.json',{'rewritten':'stop'})
    elif mutation=='interrupt':
        write_json(f.execution/'controller_turn_interrupt.json',{'rewritten':'interrupt'})
    elif mutation=='snapshot':
        target=Path(proof['baseline_state_path'])
        target.write_bytes(target.read_bytes()+b'\n')
    else:
        with f.journal.open('r+b') as stream:
            stream.seek(0)
            stream.write(b'X')
    with pytest.raises(LibraryStopped,match='unknown_resume_.*changed|historical_journal_prefix_changed'):
        trial.get_auth(f.root,force=True)


def test_controller_progress_and_appended_journal_remain_mutable(stopped265):
    f=stopped265
    proof=resume(f)
    progress=f.execution/'progress.json'
    assert str(progress) not in {row['path'] for row in proof['protected_files']}
    write_json(progress,{'controller':'resumed cached observations'})
    with f.journal.open('ab') as stream:
        stream.write(b'{"new":"append only evidence"}\n')
    trial.get_auth(f.root,force=True)


@pytest.mark.parametrize('mutation',['scope','skip','renders','instruction','lost_id'])
def test_resume_artifact_scope_or_permission_cannot_be_rewritten(stopped265,mutation):
    f=stopped265
    proof=resume(f)
    data=read(f.root/'library_state.json')
    artifact=Path(proof['artifact_path'])
    changed=read(artifact)
    if mutation=='scope':
        changed['lost_scope']['source_end_s']=57.0
    elif mutation=='skip':
        changed['skip_stages']=[]
    elif mutation=='renders':
        changed['new_renders']=1
    elif mutation=='instruction':
        changed['user_instruction']=''
    else:
        changed['lost_call_id']='glm_264_vss_inspect_3'
    write_json(artifact,changed)
    # Even changing the ledger's artifact digest cannot relax the scope.
    data['artifacts'][trial.RESUME_POLICY][0]['sha256']=json_sha(changed)
    write_json(f.root/'library_state.json',data)
    with pytest.raises(LibraryStopped,match='unknown_resume_scope_changed|unknown_resume_lost_input_changed'):
        trial.get_auth(f.root,force=True)


def test_resume_cannot_relax_base_independent_continuation(stopped265):
    f=stopped265
    data=read(f.root/'library_state.json')
    data['continuation_policy']='different_policy'
    write_json(f.root/'library_state.json',data)
    with pytest.raises(LibraryStopped,match='base_independent_continuation_required'):
        resume(f)


def test_no_blank_instruction_no_finished_trial_resume(stopped265):
    f=stopped265
    with pytest.raises(LibraryStopped,match='resume_instruction_required'):
        trial.record_unknown_resume(f.root,' ')
    write_json(f.execution/'result.json',{'finished':'existing result'})
    with pytest.raises(LibraryStopped,match='trial_finished_no_resume'):
        resume(f)


@pytest.mark.parametrize('operation',['complete','fail','reconcile','reclassify'])
def test_preserved_unknown265_cannot_be_reclassified_as_received(stopped265,operation):
    f=stopped265
    resume(f)
    state=trial.VisualStoryState(f.root)
    lost=state.data['calls'][264]
    before=state.path.read_bytes()
    with pytest.raises(LibraryStopped,match='unknown_resume_prefix_call_read_only'):
        if operation=='complete':
            state.complete_call(lost,{'new':'fabricated reply'})
        elif operation=='fail':
            state.fail_call(lost,'relabel unknown')
        elif operation=='reconcile':
            state.reconcile_received(lost,{},evidence={})
        else:
            state.reclassify_uncertain(lost,evidence=[])
    assert state.path.read_bytes()==before
    assert trial.get_auth(f.root,force=True)['unknown_resume']['lost_call_id']==lost['id']
