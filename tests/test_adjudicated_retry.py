import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import production_subject as subject, reference_cli as cli
from omni_story.evaluation.adjudicate import read_adjudications
from omni_story.library.state import LibraryStopped, json_sha, write_json


def forbidden(*args,**kwargs):
    pytest.fail('No extra production attempt is allowed')


def snapshot(path):
    return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.fixture
def run(tmp_path,monkeypatch):
    from omni_story.library import pipeline
    root=tmp_path/'release';root.mkdir()
    monkeypatch.setattr(pipeline,'__file__',str(root/'src/omni_story/library/pipeline.py'))
    monkeypatch.setattr(pipeline,'execute',forbidden)
    media=tmp_path/'reference';media.write_bytes(b'minimal reference')
    original=tmp_path/'original/production_stage'
    write_json(original/'library_state.json',dict(input_lock=dict(reference_sha256=subject.digest(media)),
        calls=[dict(id='glm_001_reference',status='submitted')]))
    registry=tmp_path/'registry.json';registry.write_bytes(b'{"frozen": "original"}\n')
    return dict(production_root=str(root),production_files={},reference=str(media),
        reference_sha256=subject.digest(media),library=str(media),configuration={},asr=False,
        asr_model_dir=None,output=str(tmp_path/'output'),registry_path=str(registry),
        subject_cache_root=str(tmp_path/'cache'),case_id='t1',allow_unregistered_reference=True,
        existing_reference_outputs={'t1':str(original)},external_budget_gate=['budget-gate'])


def original(run):
    return Path(run['existing_reference_outputs']['t1'])


def decide(run,call_id='glm_001_reference',**overrides):
    row=dict(call_id=call_id,decision='abandoned_by_user',decided_by='user',
        user_note='明确放弃原调用',decided_at='2026-10-24T00:00:00+00:00')
    row.update(overrides)
    with (original(run)/'adjudications.jsonl').open('a',encoding='utf-8') as handle:
        handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    return row


def retry_stage(run,attempt='glm_001_reference__retry1'):
    return Path(run['output'])/('production_stage_'+attempt)


def metadata(run):
    return json.loads((Path(run['output'])/'reuse.json').read_text())


def collected(run,stage):
    value=dict(theme='行动带来改变')
    request=dict(arguments=dict(video_source=run['reference']),media_sha256=run['reference_sha256'])
    response=dict(result=value);folder=stage/'calls/glm_001_reference';artifacts={}
    for name,data in (('request.json',request),('response.json',response),('parsed.json',value)):
        path=folder/name;write_json(path,data);artifacts[str(path)]=subject.digest(path)
    saved=dict(status='reference_collected',manual_correction=False,call_id='glm_001_reference',
        binding={k:run[k] for k in ('reference','reference_sha256','production_root',
            'production_files','configuration','asr_model_dir')},
        understanding=value,understanding_sha256=json_sha(value),artifacts=artifacts,
        request_sha256=json_sha(request),response_sha256=json_sha(response),
        media_path=run['reference'],media_sha256=run['reference_sha256'])
    write_json(stage/'subject.json',saved)
    return saved


def producer(run,monkeypatch,events,*,registry_lookup=False,unknown=False):
    from omni_story.library import pipeline
    old=original(run)
    class State:
        def __init__(self,output):
            self.output=Path(output);self.path=self.output/'library_state.json'
            self.input_lock=dict(reference_sha256=run['reference_sha256']);self.data=dict(calls=[])
        def begin_call(self,name,request,**kwargs):
            events.append('begin')
            folder=self.output/'calls/glm_001_reference';write_json(folder/'request.json',request)
            call=dict(id='glm_001_reference',name=name,status='submitted',request_sha256=json_sha(request))
            self.data['calls'].append(call);write_json(self.path,self.data)
            return call,folder
        def _reload(self):self.data=json.loads(self.path.read_text())
    class Delegate:
        exclusions=[]
        def __init__(self,state):self.state=state
        def call(self,name,prompt,media,validator,**kwargs):
            request=dict(arguments=dict(video_source=str(media)),media_sha256=subject.digest(media))
            call,folder=self.state.begin_call(name,request)
            if unknown:raise RuntimeError('request_outcome_unknown_no_replay')
            value=dict(theme='行动带来改变');response=dict(result=value)
            write_json(folder/'response.json',response);write_json(folder/'parsed.json',value)
            call.update(status='received',response_sha256=json_sha(response));write_json(self.state.path,self.state.data)
            events.append('received')
            return value
    def execute(reference,library,output,*,registry_path,model_factory,**kwargs):
        if registry_lookup and Path(registry_path)==Path(run['registry_path']):
            events.append('registry_lookup')
            raise LibraryStopped('same_inputs_already_registered_use_original_output:'+str(old))
        stage=retry_stage(run)
        assert Path(output)==stage and not stage.is_relative_to(old)
        assert Path(registry_path)==Path(run['registry_path']).parent/'adjudicated_retries/glm_001_reference__retry1.registry.json'
        assert not Path(registry_path).exists()
        write_json(Path(registry_path),dict(retry=dict(output=str(output))))
        events.append('production')
        model_factory(State(output)).call('reference','original production prompt',reference,lambda value:None)
    def gate(command,**kwargs):
        assert command[0]=='budget-gate' and command[-2:]==['--provider','glm_coding_plan']
        assert events[-1]=='production'
        request=dict(arguments=dict(video_source=run['reference']),media_sha256=run['reference_sha256'])
        assert command[command.index('--key')+1]==json_sha(dict(
            input_lock=dict(reference_sha256=run['reference_sha256']),request=request,
            attempt_id='glm_001_reference__retry1'))
        assert command[command.index('--evidence')+1]==str(retry_stage(run)/'library_state.json')
        events.append('budget')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline,'execute',execute)
    monkeypatch.setattr(subject.subprocess,'run',gate)
    return Delegate


@pytest.mark.parametrize('status',['submitted','uncertain'])
@pytest.mark.parametrize('source',['manifest','registry'])
def test_unadjudicated_calls_still_block(run,monkeypatch,status,source):
    ledger=original(run)/'library_state.json';state=json.loads(ledger.read_text())
    state['calls'][0]['status']=status;write_json(ledger,state)
    before=snapshot(original(run));registry=Path(run['registry_path']).read_bytes()
    job=dict(run)
    if source=='registry':
        from omni_story.library import pipeline
        job['existing_reference_outputs']=[]
        def execute(*args,**kwargs):
            raise LibraryStopped('same_inputs_already_registered_use_original_output:'+str(original(run)))
        monkeypatch.setattr(pipeline,'execute',execute)
    with pytest.raises(RuntimeError) as error:
        subject.capture_reference(job,provider_factory=forbidden)
    assert cli.failure(error.value)['status']=='blocked_unknown'
    assert snapshot(original(run))==before and Path(run['registry_path']).read_bytes()==registry
    assert not Path(run['registry_path']).with_name('adjudicated_retries').exists()


@pytest.mark.parametrize('source',['manifest','registry','failure','subject_outputs'])
@pytest.mark.parametrize('cache',[True,False])
def test_adjudicated_call_produces_once_with_independent_registry(run,monkeypatch,source,cache):
    row=decide(run);before=snapshot(original(run));registry=Path(run['registry_path']).read_bytes()
    events=[];delegate=producer(run,monkeypatch,events,registry_lookup=source=='registry')
    job=dict(run,reuse_only=True)
    if source!='manifest':job['existing_reference_outputs']=[]
    if source=='registry':job['reuse_only']=False
    elif source=='failure':job['subject_failures']={'t1':dict(status='blocked_unknown',original_output=str(original(run)))}
    elif source=='subject_outputs':job['subject_outputs']={'t1':str(original(run))}
    if not cache:job.pop('subject_cache_root')
    result=subject.capture_reference(job,provider_factory=delegate)
    expected=(['registry_lookup'] if source=='registry' else [])+['production','budget','begin','received']
    assert events==expected
    for saved in (result,metadata(job),json.loads((retry_stage(run)/'subject.json').read_text())):
        assert saved['retry_of']==str(original(run))
        assert saved['attempt_id']=='glm_001_reference__retry1' and saved['adjudication']==row
    subject.verify_subject(result)
    assert subject.capture_reference(job,provider_factory=forbidden)==result and events==expected
    assert snapshot(original(run))==before and Path(run['registry_path']).read_bytes()==registry
    reservations=Path(run['registry_path']).parent/'adjudicated_retries/.attempts/adjudications.jsonl'
    assert len([r for r in read_adjudications(reservations.with_name('ledger.json')) if 'attempt_id' in r])==1


def test_complete_retry_result_is_verified_and_reused(run):
    row=decide(run);saved=collected(run,retry_stage(run));before=snapshot(original(run))
    result=subject.capture_reference(run,provider_factory=forbidden)
    assert result==dict(saved,retry_of=str(original(run)),attempt_id='glm_001_reference__retry1',adjudication=row)
    assert snapshot(original(run))==before


def test_cached_retry_cannot_bypass_its_pending_ledger(run,monkeypatch):
    decide(run);events=[];delegate=producer(run,monkeypatch,events)
    subject.capture_reference(run,provider_factory=delegate)
    write_json(retry_stage(run)/'library_state.json',dict(calls=[dict(id='lost',status='uncertain')]))
    with pytest.raises(RuntimeError,match='subject_retry_unknown_no_replay'):
        subject.capture_reference(run,provider_factory=forbidden)
    assert metadata(run)['status']=='blocked_unknown'
    assert events==['production','budget','begin','received']


@pytest.mark.parametrize('status',['submitted','uncertain'])
@pytest.mark.parametrize('ledger_name',['library_state.json','state.json'])
def test_retry_pending_always_blocks_even_with_its_own_adjudication(run,status,ledger_name):
    decide(run);stage=retry_stage(run)
    write_json(stage/ledger_name,dict(calls=[dict(id='retry-call',status=status)]))
    (stage/'adjudications.jsonl').write_text(json.dumps(dict(
        call_id='retry-call',decision='abandoned_by_user',decided_by='user'))+'\n')
    before=snapshot(original(run));retry_before=snapshot(stage)
    for _ in range(2):
        with pytest.raises(RuntimeError) as error:
            subject.capture_reference(run,provider_factory=forbidden)
        assert cli.failure(error.value)['status']=='blocked_unknown'
        assert metadata(run)['status']=='blocked_unknown'
    assert snapshot(stage)==retry_before and snapshot(original(run))==before
    assert not (Path(run['output'])/'production_stage_glm_001_reference__retry2').exists()


def test_retry_that_becomes_unknown_is_never_submitted_again(run,monkeypatch,tmp_path):
    decide(run);events=[];delegate=producer(run,monkeypatch,events,unknown=True)
    for job in (run,run,dict(run,output=str(tmp_path/'different-output'))):
        with pytest.raises(RuntimeError) as error:
            subject.capture_reference(job,provider_factory=delegate)
        assert cli.failure(error.value)['status']=='blocked_unknown'
    assert events==['production','budget','begin']


def test_all_original_pending_calls_require_user_decisions(run):
    path=original(run)/'library_state.json';state=json.loads(path.read_text())
    state['calls'].append(dict(id='glm_002_reference',status='uncertain'));write_json(path,state)
    decide(run)
    with pytest.raises(RuntimeError,match='subject_original_unknown_no_replay'):
        subject.capture_reference(run,provider_factory=forbidden)
    assert subject._registry_pending(original(run)) is True
    decide(run,'glm_002_reference')
    assert subject._registry_pending(original(run)) is False


def test_all_adjudicated_calls_retry_using_first_call_and_keep_failure_summary(run,monkeypatch):
    path=original(run)/'library_state.json';state=json.loads(path.read_text())
    state['calls'].append(dict(id='glm_002_reference',status='uncertain'));write_json(path,state)
    row=decide(run);decide(run,'glm_002_reference')
    job=dict(run,subject_failures={'t1':dict(status='blocked_unknown',calls=state['calls'])})
    before=snapshot(original(run));events=[];delegate=producer(run,monkeypatch,events)
    result=subject.capture_reference(job,provider_factory=delegate)
    assert result['attempt_id']=='glm_001_reference__retry1' and result['adjudication']==row
    assert snapshot(original(run))==before and events==['production','budget','begin','received']


def test_rejected_budget_gate_prevents_retry_submission(run,monkeypatch):
    decide(run);events=[];delegate=producer(run,monkeypatch,events)
    monkeypatch.setattr(subject.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1,stderr='budget exhausted'))
    with pytest.raises(RuntimeError) as error:
        subject.capture_reference(run,provider_factory=delegate)
    assert cli.failure(error.value)['status']=='blocked_budget' and events==['production']
    assert not Path(run['subject_cache_root']).exists()
    assert not (retry_stage(run)/'calls').exists()


def test_retry_without_external_budget_gate_is_rejected(run,monkeypatch):
    decide(run);events=[];producer(run,monkeypatch,events)
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE',raising=False)
    job={k:v for k,v in run.items() if k!='external_budget_gate'}
    with pytest.raises(ValueError,match='old_production_requires_external_budget_gate'):
        subject.capture_reference(job)
    assert events==['production'] and not (retry_stage(run)/'calls').exists()


def test_interrupted_attempt_without_ledger_is_not_started_again(run,monkeypatch):
    from omni_story.library import pipeline
    decide(run);attempts=[]
    def execute(reference,library,output,**kwargs):
        attempts.append(output)
        raise ValueError('interrupted_before_ledger')
    monkeypatch.setattr(pipeline,'execute',execute)
    with pytest.raises(ValueError,match='interrupted_before_ledger'):
        subject.capture_reference(run,provider_factory=forbidden)
    with pytest.raises(RuntimeError,match='subject_retry_unknown_no_replay'):
        subject.capture_reference(run,provider_factory=forbidden)
    assert attempts==[retry_stage(run)]


def test_non_user_abandonment_does_not_allow_retry(run):
    decide(run,decided_by='assistant')
    with pytest.raises(RuntimeError,match='subject_original_unknown_no_replay'):
        subject.capture_reference(run,provider_factory=forbidden)
    assert subject._registry_pending(original(run)) is True


def test_adjudication_can_load_beside_script_with_frozen_imports(monkeypatch):
    monkeypatch.setattr(subject,'__package__','')
    assert subject._adjudicate().is_blocked(dict(id='call',status='submitted'),[]) is True


def test_retry_lock_has_no_evaluation_package_import():
    import inspect
    from omni_story.evaluation import production_subject
    source = inspect.getsource(production_subject)
    assert 'from omni_story.evaluation.adjudicate import' not in source
    with production_subject._own_lock(__import__('tempfile').mkdtemp() + '/x.lock'):
        pass
