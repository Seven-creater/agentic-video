"""Real local media and fault recovery, never paid model calls."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import reliable as r
from omni_story.library.state import LibraryStopped, write_json


def run(args):
    p = subprocess.run(args,capture_output=True,timeout=120)
    assert p.returncode == 0,p.stderr.decode(errors='replace')


@pytest.fixture
def media(tmp_path,monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('real FFmpeg required')
    library = tmp_path / 'library'
    library.mkdir()
    source,reference = library / 'movie.mp4',tmp_path / 'reference.mp4'
    for path,color in ((source,'red'),(reference,'blue')):
        run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',f'color={color}:s=96x64:r=30:d=12',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=12',
            '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)])
    evidence = tmp_path / 'synthetic_evidence.json'
    write_json(evidence,dict(kind='synthetic_local_test_only'))
    visual = tmp_path / 'visual'
    visual.mkdir()
    def candidates(records,catalog,*a):
        s=catalog['sources'][0]
        return [dict(clip_id='clip_'+str(i),source_id=s['source_id'],source_sha256=s['sha256'],
            source_path=s['path'],window_id='window',source_in_s=i*4,source_out_s=(i+1)*4,
            summary='Synthetic fixture.',matched_terms=['动作'],source_subtitle_masks=[],
            evidence_path=str(evidence),evidence_sha256=r.sha256_file(evidence)) for i in range(3)],[]
    monkeypatch.setattr(r,'candidates_from_records',candidates)
    calls=[]
    class Model:
        def __init__(self,state): self.state,self.output=state,state.output
        def call(self,name,prompt,media,validator):
            calls.append(name)
            value = dict(theme='动作',queries=['动作'],speech_mode='none') if name=='reference' else dict(
                clip_ids=['clip_0','clip_1','clip_2'],speech=[])
            validator(value)
            return value
    return dict(reference=reference,library=library,visual_library=visual,output=tmp_path/'output',
        model_factory=Model,configuration=dict(r.PROVIDER,workflow=r.POLICY)),calls


def test_real_music_delivery_is_idempotent_without_quality_gate(media):
    args,calls=media
    result=r.execute(**args)
    assert result['engineering_status']=='completed'
    assert result['quality_status']=='unassessed' and result['joint_quality_gate'] is False
    assert result['technical']['technical_complete'] and result['technical']['sound']['audible']
    assert calls==['reference','plan']
    assert r.execute(**args)==result and calls==['reference','plan']


def test_received_protocol_failure_automatically_uses_related_candidates(media):
    args,calls=media
    original=args['model_factory']
    class BrokenPlan(original):
        def call(self,name,*a):
            if name=='plan':
                calls.append(name)
                raise LibraryStopped('reliable_one_protocol_repair_exhausted')
            return super().call(name,*a)
    args['model_factory']=BrokenPlan
    result=r.execute(**args)
    assert result['engineering_status']=='completed_degraded'
    assert result['technical']['technical_complete']
    assert 'plan:' in ' '.join(result['degraded_reasons'])


def test_candidates_keep_their_own_ids_and_event_only_summaries(tmp_path,monkeypatch):
    from test_visual_library import observed, SHA
    records=tmp_path/'observations';records.mkdir()
    for ident,start in (('first',0),('second',12)):
        record=observed(ident,start,start+10,fact='人物摆手')
        record['window']['observation']['usable_ranges']=[dict(local_in_s=0,local_out_s=8)]
        write_json(records/(ident+'.json'),record)
    monkeypatch.setattr(r,'_received_library_window',lambda record,*a,**k:record['window'])
    catalog=dict(sources=[dict(source_id='film_a',sha256=SHA,path='/fixture/actual_movie.mp4')])
    candidates,_=r.candidates_from_records(records,catalog,['人物摆手'],tmp_path)
    assert {c['clip_id'] for c in candidates}=={'first_0','second_0'}
    assert all(c['summary']=='人物摆手' for c in candidates)
    choice=dict(clip_ids=[c['clip_id'] for c in candidates],speech=[])
    assert r.compile_choice(choice,candidates,10)['duration_s']==10


def test_received_reuse_is_bound_and_does_not_replay_refusals(media,monkeypatch):
    args,_=media
    from omni_story.library.prompts import POLICY_VERSION
    source=args['reference'];sha=r.sha256_file(source)
    catalog=r.inventory_sources(source,args['output']/'old_catalog')
    proxy=r.prepare_window(catalog['sources'][0],0,12,args['output']/'old_proxy',max_bytes=3500000,include_audio=False)
    lineage=r.read(Path(proxy['path']).parent/'lineage.json')
    request=dict(tool='analyze_video',arguments=dict(video_source=proxy['path'],prompt=r.REFERENCE_PROMPT),
        media_sha256=r.sha256_file(proxy['path']),provider=r.PROVIDER['provider'],policy_version=POLICY_VERSION,
        observation_scope={k:lineage[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
    parent=args['output']/'parent';folder=parent/'calls/glm_001_reference';folder.mkdir(parents=True)
    value=dict(theme='动作',queries=['动作'],speech_mode='none')
    reply=dict(result=dict(content=[dict(type='text',text=json.dumps(value))]))
    write_json(folder/'request.json',request);write_json(folder/'response.json',reply)
    call=dict(id='glm_001_reference',name='reference',status='received',request_sha256=r.json_sha(request),
        response_sha256=r.json_sha(reply))
    ledger=parent/'library_state.json';write_json(ledger,dict(calls=[call]))
    from omni_story.library.state import LibraryState
    def model():
        state=LibraryState(args['output']/'new',dict(configuration=dict(received_sources=[
            dict(path=str(ledger),sha256=r.sha256_file(ledger))])))
        m=r.ReliableMCP.__new__(r.ReliableMCP);m.state=state
        return m
    m=model()
    assert m._received('reference',request,r.validate_reference)==value
    assert m.state.data['calls']==[]
    assert m.received_media('reference',r.REFERENCE_PROMPT,sha,12)==Path(proxy['path'])
    assert m._received('reference',dict(request,arguments=dict(request['arguments'],prompt='new prompt')),
        r.validate_reference) is None
    class Replay(r.ReliableMCP):
        def __init__(self,state):
            self.state,self.output=state,state.output
            self.exclusions=[];self.audio_new_work_exclusions=None
        def call(self,name,prompt,media,validator):
            if name=='plan':
                choice=dict(clip_ids=['clip_0','clip_1','clip_2'],speech=[]);validator(choice);return choice
            return super().call(name,prompt,media,validator)
        def _submit(self,*a,**k):pytest.fail('received reference must not send another paid request')
    args['configuration']['received_sources']=[dict(path=str(ledger),sha256=r.sha256_file(ledger))]
    args['output']=args['output']/'fresh_pipeline';args['model_factory']=Replay
    monkeypatch.setattr(r,'prepare_window',lambda *a,**k:pytest.fail('do not re-encode a received proxy'))
    result=r.execute(**args)
    assert result['technical']['technical_complete']
    assert r.read(args['output']/'reference_reading.json')['media_path']==proxy['path']
    new_state=r.read(args['output']/'model/library_state.json')
    assert new_state['calls']==[] and new_state['artifacts']['received_reuse']
    write_json(folder/'response.json',dict(result={}))
    with pytest.raises(LibraryStopped,match='response_changed'):
        m._received('reference',request,r.validate_reference)
    write_json(folder/'response.json',reply)
    call.update(status='failed_known',error='provider_http_400_code_1301:refused')
    write_json(ledger,dict(calls=[call]))
    m.state.input_lock['configuration']['received_sources'][0]['sha256']=r.sha256_file(ledger)
    with pytest.raises(LibraryStopped,match='reliable_provider_refused'):
        m._received('reference',request,r.validate_reference)
    with pytest.raises(LibraryStopped,match='reliable_provider_refused'):
        m.received_media('reference',r.REFERENCE_PROMPT,sha,12)
    call.update(name='plan_repair',status='submitted',error='unknown')
    write_json(ledger,dict(calls=[call]))
    m.state.input_lock['configuration']['received_sources'][0]['sha256']=r.sha256_file(ledger)
    with pytest.raises(LibraryStopped,match='not_received_no_replay'):
        m.received_media('reference','a changed prompt cannot bypass unknown scope',sha,12)
    class Refused:
        def call(self,*a):raise LibraryStopped('reliable_provider_refused')
    with pytest.raises(LibraryStopped,match='reliable_provider_refused'):
        r._call_or_fallback(Refused(),'reference','fixed prompt',source,r.validate_reference,value,[])


def test_interrupted_render_resumes_received_stages_without_model_calls(media,monkeypatch):
    args,calls=media
    original=r.render_library_video
    def interrupted(*a,**k):
        original(*a,**k)
        raise SystemExit('simulated worker loss after real render')
    monkeypatch.setattr(r,'render_library_video',interrupted)
    with pytest.raises(SystemExit): r.execute(**args)
    monkeypatch.setattr(r,'render_library_video',original)
    result=r.execute(**args)
    assert result['technical']['technical_complete'] and result['automatic_recoveries']==1
    assert calls==['reference','plan']


def test_unknown_voice_is_not_replayed_and_soundtrack_is_delivered(media,monkeypatch):
    from omni_story.library import tts
    args,calls=media
    original=args['model_factory']
    class Speech(original):
        def call(self,name,prompt,media,validator):
            if name=='reference':
                calls.append(name)
                result=dict(theme='动作',queries=['动作'],speech_mode='narration')
                validator(result)
                return result
            if name=='plan':
                calls.append(name)
                result=dict(clip_ids=['clip_0','clip_1','clip_2'],speech=[dict(clip_id='clip_0',text='开始行动')])
                validator(result)
                return result
            return super().call(name,prompt,media,validator)
    args['model_factory']=Speech
    requests=[]
    def lost(*a,**k):
        requests.append(1)
        raise tts.TTSUnknown('synthetic_unknown_no_replay')
    monkeypatch.setattr(tts,'synthesize_line',lost)
    result=r.execute(**args)
    assert result['engineering_status']=='completed_degraded'
    assert result['audio_mode']=='reference_original_soundtrack'
    assert r.execute(**args)==result and len(requests)==1


@pytest.mark.parametrize('corruption',['silence','canvas','black'])
def test_technical_gate_rejects_real_bad_files(media,corruption):
    args,_=media
    result=r.execute(**args)
    target=args['output']/('bad_'+corruption+'.mp4')
    if corruption=='black':
        run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=black:s=1280x720:r=30:d=12',
            '-i',result['final_video'],'-map','0:v:0','-map','1:a:0','-c:v','libx264','-c:a','copy','-t','12',str(target)])
    else:
        filters=['-af','volume=0','-c:v','copy'] if corruption=='silence' else ['-vf','scale=720:1280']
        run(['ffmpeg','-nostdin','-v','error','-i',result['final_video'],*filters,str(target)])
    plan=r.read(args['output']/'plan.json')['plan']
    with pytest.raises(LibraryStopped,match='technical_rejected'):
        r.verify_delivery(target,plan)


def test_checkpoint_changed_artifact_is_not_silently_rebuilt(tmp_path):
    p=tmp_path/'artifact'
    p.write_text('received')
    cp=r.Checkpoints(tmp_path,dict(fixed=1))
    cp.stage('received',lambda:(dict(path=str(p)),[p]))
    p.write_text('changed')
    with pytest.raises(LibraryStopped,match='checkpoint_artifact_changed'):
        cp.stage('received',lambda:pytest.fail('must not overwrite evidence'))


def test_unknown_call_uses_local_fallback_without_a_second_request(tmp_path):
    from omni_story.library.state import LibraryState
    state=LibraryState(tmp_path/'model',dict(reference_sha256='a'*64))
    class Lost:
        def __init__(self): self.state,self.output=state,state.output
        def call(self,*a):
            state.begin_call('reference',dict(payload='bound'))
            raise LibraryStopped('synthetic_response_lost')
    reasons=[]
    fallback=dict(theme='动作',queries=['动作'],speech_mode='none')
    assert r._call_or_fallback(Lost(),'reference','prompt',None,r.validate_reference,fallback,reasons)==fallback
    state._reload()
    assert state.data['calls'][0]['status']=='submitted' and len(state.data['calls'])==1
    assert reasons==['reference:unknown_isolated_no_replay']


def test_prior_budget_is_carried_and_cannot_be_changed(tmp_path):
    from omni_story.evaluation.budget import liability
    base=dict(experiment_id='old',requests=[dict(run_id='old/case/full')],
        cost_control=dict(experiment_root=str(tmp_path/'old'),per_operation_reserve_cny=1))
    original=tmp_path/'old_budget.json'
    write_json(original,base)
    current=dict(experiment_id='new',requests=[],cost_control=dict(experiment_root=str(tmp_path/'new'),
        per_operation_reserve_cny=1,prior_budget_file=str(original),prior_budget_sha256=r.sha256_file(original)))
    assert float(liability(current)['upper_cny'])==1
    write_json(original,dict(base,requests=[]))
    with pytest.raises(RuntimeError,match='prior_budget_changed'):liability(current)


def test_model_cannot_supply_an_unknown_clip_or_duplicate():
    for value in [dict(clip_ids=['invented'],speech=[]),dict(clip_ids=['real','real'],speech=[])]:
        with pytest.raises(ValueError):r.validate_choice(value,[dict(clip_id='real')])


def test_actual_voice_provenance_cannot_be_satisfied_by_background_music(media,monkeypatch):
    from omni_story.library import tts, audio_render
    args,calls=media
    wav=args['output'].parent/'voice.wav'
    run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
        'sine=frequency=1200:sample_rate=24000:duration=0.6','-c:a','pcm_s16le',str(wav)])
    original=args['model_factory']
    class Speech(original):
        def call(self,name,prompt,media,validator):
            calls.append(name)
            value=(dict(theme='动作',queries=['动作'],speech_mode='narration') if name=='reference' else
                dict(clip_ids=['clip_0','clip_1','clip_2'],speech=[dict(clip_id='clip_0',text='开始行动')]))
            validator(value)
            return value
    args['model_factory']=Speech
    monkeypatch.setattr(tts,'synthesize_line',lambda *a,**k: dict(
        audio_path=str(wav),audio_sha256=r.sha256_file(wav),duration_s=.6))
    result=r.execute(**args)
    assert result['technical']['checks']['speech_provenance'] is True
    wrong=audio_render.mix_speech(args['output']/'render/final.mp4',[],args['output']/'without_voice.mp4',
                                 music_path=args['reference'])
    audio=result['audio_provenance']
    with pytest.raises(LibraryStopped,match='technical_rejected'):
        r.verify_delivery(wrong['path'],r.read(args['output']/'plan.json')['plan'],
                          speech=audio['speech'],mix_receipt=audio['mix_receipt'])


def test_global_unknown_voice_blocks_different_output_without_sending(tmp_path,monkeypatch):
    from omni_story.library import tts
    old=tmp_path/'original'
    (old/'line').mkdir(parents=True)
    write_json(old/'line/state.json',dict(status='uncertain'))
    payload=dict(model=tts.MODEL,input=dict(text='开始行动',voice='Cherry',language_type='Chinese'))
    registry=tmp_path/'registry.json'
    write_json(registry,{r.json_sha(payload):dict(output=str(old),line_id='line')})
    monkeypatch.setattr(tts,'synthesize_line',lambda *a,**k: pytest.fail('unknown must not be replayed'))
    with pytest.raises(LibraryStopped,match='shared_unknown_voice_no_replay'):
        r.synthesize_registered('开始行动',tmp_path/'other','other_line',registry)


@pytest.mark.parametrize('fault',[None,'unknown','prompt','media','budget'])
def test_actual_native_reliable_guard_checks_payload_and_unknowns(tmp_path,fault):
    import base64
    if not shutil.which('node'):pytest.skip('actual Node guard required')
    media=tmp_path/'window.mp4'
    media.write_bytes(b'local bound native bytes')
    config=dict(r.PROVIDER,workflow=r.POLICY,vision_generation=dict(max_output_tokens=32768))
    request=dict(tool='analyze_video',arguments=dict(video_source=str(media),prompt='fixed prompt'),
        media_sha256=r.sha256_file(media),provider=config['provider'],policy_version='fixture')
    original=dict(id='glm_001_reference',name='reference',status='failed_known',request_sha256=r.json_sha(request))
    retry=dict(request,reliable_retry_of=original['request_sha256'],reliable_attempt=1)
    current=dict(id='glm_002_reference_retry1',name='reference_retry1',status='submitted',request_sha256=r.json_sha(retry))
    for c,req in ((original,request),(current,retry)):
        folder=tmp_path/'calls'/c['id'];folder.mkdir(parents=True)
        write_json(folder/'request.json',req)
    events=[dict(type='request',job_id=original['id'],seq=1),dict(type='response',seq=1,status=500)]
    (tmp_path/'mcp_http.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    state=dict(input_lock=dict(configuration=config),calls=[original,current],request_count=2,
               max_requests=20,policy_version='fixture')
    body=dict(model=config['vision_model'],stream=False,max_tokens=32768,messages=[dict(role='user',content=[
        dict(type='video_url',video_url=dict(url='data:video/mp4;base64,'+base64.b64encode(media.read_bytes()).decode())),
        dict(type='text',text='fixed prompt')])])
    if fault=='unknown': original['status']='uncertain'
    if fault=='prompt': body['messages'][0]['content'][1]['text']='changed'
    if fault=='media': body['messages'][0]['content'][0]['video_url']['url']='data:video/mp4;base64,b3RoZXI='
    if fault=='budget': state['max_requests']=80
    write_json(tmp_path/'library_state.json',state)
    module=(Path(r.__file__).with_name('mcp_opencode_guard.mjs')).as_uri()
    script=('import {opencodeRequestLimit} from '+json.dumps(module)+';try {console.log(opencodeRequestLimit('+
        json.dumps(str(tmp_path))+','+json.dumps(dict(job_id=current['id']))+','+json.dumps(body)+'))}'+
        'catch(e){console.log(e.message);process.exitCode=2}')
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=20)
    assert (result.returncode==0) is (fault is None),result.stdout+result.stderr


def test_clip_quantization_never_extends_beyond_received_range(media):
    args,_=media
    catalog=r.inventory_sources(args['library'],args['output']/'inventory')
    candidates,_=r.candidates_from_records(None,catalog,None,None)
    for c in candidates:c['source_out_s']+=.019
    plan=r.compile_choice(dict(clip_ids=[c['clip_id'] for c in candidates],speech=[]),candidates,10)
    original={c['clip_id']:c for c in candidates}
    assert all(s['source_out_s']<=original[s['clip_id']]['source_out_s'] for s in plan['segments'])


def test_durable_checkpoint_survives_actual_sigkill(tmp_path):
    import os,time,sys
    root=tmp_path/'worker';root.mkdir()
    script=('from omni_story.library.reliable import Checkpoints;import time;from pathlib import Path;'+
        'c=Checkpoints(Path('+repr(str(root))+'),{"fixed":1});'+
        'c.stage("received",lambda:({"kept":True},[]));c.stage("render",lambda:time.sleep(300))')
    process=subprocess.Popen([sys.executable,'-c',script])
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            p=root/'reliable_state.json'
            if p.exists() and 'render' in r.read(p)['stages']:break
            time.sleep(.05)
        else:pytest.fail('worker did not write its durable checkpoint')
        process.kill();process.wait(timeout=5)
        cp=r.Checkpoints(root,dict(fixed=1))
        assert cp.stage('received',lambda:pytest.fail('received stage cannot be redone'))==dict(kept=True)
        assert cp.stage('render',lambda:('recovered',[]))=='recovered' and cp.data['recoveries']==1
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_live_capture_binds_actual_primary_and_keeps_quality_unassessed(media,monkeypatch):
    from omni_story.discovery import reliable_capture as capture
    from datetime import datetime,timedelta,timezone
    args,_=media
    instant=datetime.now(timezone.utc)
    class Public:
        def __init__(self,home,root):
            root.mkdir(parents=True);self.ledger=root/'calls.json';write_json(self.ledger,[])
        def _board(self):
            return dict(data=dict(last_update_time=instant.astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d%H%M%S'),
                objs=[dict(sentence_id=str(i),sentence='动作'+str(i),rank=i) for i in (1,2)]))
        def prepare_trend(self,*a,**k):pass
        def _topic(self,tid):
            return [dict(aweme_id=tid,create_time=int(instant.timestamp())-60,
                author=dict(uid='author'),video=dict(duration=12000,play_addr=dict(url_list=['https://example.org/'+tid])))]
    monkeypatch.setattr(capture,'PublicSource',Public)
    def download(record,*a,**k):
        path=args['reference'] if record['id']=='1' else args['library']/'movie.mp4'
        return dict(path=str(path),sha256=r.sha256_file(path))
    monkeypatch.setattr(capture,'_download',download)
    a=capture.capture_reference(args['output'].parent,args['output']/'capture1')
    b=capture.capture_reference(args['output'].parent,args['output']/'capture2',exclude_trend_ids=['1'],
        exclude_reference_sha256s=[a['sha256']])
    assert a['sha256']!=b['sha256'] and a['evidence']['trend']['sentence_id']=='1'
    assert b['evidence']['trend']['sentence_id']=='2' and b['evidence']['quality_status']=='unassessed'


def test_small_successful_batch_cannot_claim_frozen20_live2_acceptance(tmp_path):
    from omni_story.library.reliable_cli import collect
    root=tmp_path/'batch';folder=root/'runs/one/reliable_v1';folder.mkdir(parents=True)
    final=folder/'final.mp4';final.write_bytes(b'fixture_bound_bytes')
    write_json(folder/'reliable_result.json',dict(final_video=str(final),final_sha256=r.sha256_file(final),
        engineering_status='completed',technical=dict(technical_complete=True),degraded_reasons=[],
        quality_status='unassessed',automatic_recoveries=0))
    write_json(root/'request_budget.json',dict(experiment_id='batch',requests=[],cost_control=dict(
        experiment_root=str(root),per_operation_reserve_cny=1)))
    report=collect(root,dict(cases=[dict(case_id='one',input_kind='reference')]),
                   dict(started_at=r.now(),code_sha256='fixed'))
    assert report['batch_complete'] is True and report['engineering_acceptance_pass'] is False


def test_real_module_refuses_unbounded_worker_before_paid_admission(tmp_path):
    import sys
    if not shutil.which('ffmpeg'):pytest.skip('FFmpeg runtime required')
    home=tmp_path/'home';(home/'shared').mkdir(parents=True)
    library=tmp_path/'frozen.json';write_json(library,{})
    source=tmp_path/'reference.mp4';source.write_bytes(b'bound preflight input; no paid calls')
    prior=tmp_path/'prior.json'
    write_json(prior,dict(experiment_id='prior',requests=[],max_model_calls=522,cost_control=dict(
        experiment_root=str(tmp_path/'old'),per_operation_reserve_cny=1)))
    root=home/'shared/jobs/actual_coordinator'
    manifest=tmp_path/'manifest.json'
    write_json(manifest,dict(experiment_id='actual_coordinator',execution_mode='reliable_v1',output_spec=r.SPEC,
        max_cost_cny=100,library=dict(manifest_path=str(library),manifest_sha256=r.sha256_file(library)),
        cases=[dict(case_id='one',input_kind='reference',reference=dict(path=str(source),sha256=r.sha256_file(source)))],
        prior_budget_file=str(prior),prior_budget_sha256=r.sha256_file(prior)))
    result=subprocess.run([sys.executable,'-m','omni_story.library.reliable_cli','batch','--home',str(home),
        '--root',str(root),'--manifest',str(manifest)],capture_output=True,text=True,timeout=45)
    assert result.returncode!=0,result.stdout+result.stderr
    assert 'reliable_hard_memory_requires_delegated_systemd_service' in result.stderr
    folder=root/'runs/one/reliable_v1'
    assert not folder.exists()
    assert not (root/'request_budget.json').exists()
