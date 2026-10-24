import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from omni_story.evaluation import isolated_asr, production_subject as subject, reference_cli as cli
from omni_story.evaluation.subject_cache import SubjectCache
from omni_story.library.state import json_sha


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False))
    return path


def forbidden(*args,**kwargs):
    pytest.fail('production must not run')


def collected(run):
    value=dict(theme='付诸行动')
    request=dict(arguments=dict(video_source=run['reference']),media_sha256=run['reference_sha256'])
    response=dict(result=value)
    folder=Path(run['output'])/'production_stage/calls/c0'
    artifacts={}
    for name,data in (('request.json',request),('response.json',response),('parsed.json',value)):
        path=write(folder/name,data);artifacts[str(path)]=subject.digest(path)
    saved=dict(status='reference_collected',manual_correction=False,call_id='c0',
        binding={k:run[k] for k in ('reference','reference_sha256','production_root',
            'production_files','configuration','asr_model_dir')},
        understanding=value,understanding_sha256=json_sha(value),artifacts=artifacts,
        request_sha256=json_sha(request),response_sha256=json_sha(response),
        media_path=run['reference'],media_sha256=run['reference_sha256'])
    write(Path(run['output'])/'subject.json',saved)
    return saved


@pytest.fixture
def run(tmp_path,monkeypatch):
    from omni_story.library import pipeline
    root=tmp_path/'release';root.mkdir()
    monkeypatch.setattr(pipeline,'__file__',str(root/'src/omni_story/library/pipeline.py'))
    monkeypatch.setattr(pipeline,'execute',forbidden)
    media=tmp_path/'media';media.write_bytes(b'minimal media')
    return dict(production_root=str(root),production_files={},reference=str(media),
        reference_sha256=subject.digest(media),output=str(tmp_path/'new/subject'),
        configuration={},asr=False,asr_model_dir=None,case_id='t1',condition='full',
        subject_cache_root=str(tmp_path/'cache'),allow_unregistered_reference=True,reuse_only=True)


def test_isolated_asr_with_legacy_release_on_sys_path(tmp_path):
    release=tmp_path/'release/src'
    library=release/'omni_story/library';library.mkdir(parents=True)
    (release/'omni_story/__init__.py').write_text('')
    (library/'__init__.py').write_text('')
    (library/'state.py').write_text('''import json
from contextlib import contextmanager
@contextmanager
def file_lock(path):
    raise AssertionError('legacy lock must not be used')
    yield
def write_json(path,value):
    path.write_text(json.dumps(value))
''')
    (library/'asr.py').write_text('def _raw_segment(row):return row\n')
    (release/'faster_whisper.py').write_text('''from types import SimpleNamespace
class WhisperModel:
    def __init__(self,*args,**kwargs):pass
    def transcribe(self,*args,**kwargs):
        return [dict(text='原始台词',words=[])],SimpleNamespace(language='zh',language_probability=1)
''')
    audio=tmp_path/'audio';audio.write_bytes(b'audio')
    runner=tmp_path/'runner.py'
    runner.write_text('''import fcntl,os,subprocess,sys
with open(os.environ['ASR_LOCK'],'a+b') as handle:
    try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:pass
    else:raise AssertionError('ASR ran without exclusive lock')
sys.exit(subprocess.run(sys.argv[1:]).returncode)
''')
    profile=dict(runner=[sys.executable,str(runner)],memory_max_bytes=2*1024**3,
        lock_path=str(tmp_path/'locks/asr.lock'))
    script='''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from omni_story.library import state
sys.path.insert(0,sys.argv[2])
import isolated_asr
profile=json.loads(sys.argv[5])
isolated_asr.install(sys.argv[3],Path(sys.argv[1]).parent,profile=profile)
from faster_whisper import WhisperModel
rows,info=WhisperModel('frozen').transcribe(sys.argv[4],word_timestamps=True)
assert [row.text for row in rows]==['原始台词'] and info.language=='zh'
with isolated_asr.file_lock(profile['lock_path']):pass
'''
    result=subprocess.run([sys.executable,'-c',script,str(release),
        str(Path(isolated_asr.__file__).parent),str(tmp_path/'cache'),str(audio),json.dumps(profile)],
        env=dict(os.environ,ASR_LOCK=profile['lock_path']),text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert json.loads(next((tmp_path/'cache').glob('*.profile.json')).read_text())['returncode']==0


def test_local_lock_waits_and_releases_on_error(tmp_path,monkeypatch):
    events=[]
    def flock(fd,operation):
        events.append(operation)
    monkeypatch.setattr(isolated_asr.fcntl,'flock',flock)
    with pytest.raises(RuntimeError,match='child failure'):
        with isolated_asr.file_lock(tmp_path/'locks/asr.lock'):
            events.append('child');raise RuntimeError('child failure')
    assert events==[fcntl.LOCK_EX,'child',fcntl.LOCK_UN]


@pytest.mark.parametrize('registry_type',['mapping','list','file'])
def test_registered_subject_falls_back_and_populates_cache(run,tmp_path,registry_type):
    original=tmp_path/'original/subject'
    saved=collected(dict(run,output=str(original)))
    path=str(original/'subject.json' if registry_type=='file' else original)
    registry={'t1':path} if registry_type=='mapping' else [path]
    job=dict(run,existing_reference_outputs=registry)
    result=subject.capture_reference(job,provider_factory=forbidden)
    assert result==saved
    cache=SubjectCache(run['subject_cache_root'])
    meta=json.loads(next(cache.root.glob('*/meta.json')).read_text())
    assert cache.get(meta['key_fields'])==saved['understanding']
    assert set(saved['artifacts'])<=set(meta['receipts'])
    again=dict(job,output=str(tmp_path/'again/subject'),existing_reference_outputs={'t1':str(tmp_path/'missing')})
    assert subject.capture_reference(again,provider_factory=forbidden)==saved


def test_registered_legacy_reading_still_populates_cache(run,tmp_path):
    original=tmp_path/'original/subject'
    saved=collected(dict(run,output=str(original)))
    reading=write(original/'production_stage/reference_reading.json',
        dict(saved['understanding'],reference_sha256=run['reference_sha256']))
    write(reading.parent/'library_state.json',dict(input_lock=dict(reference_sha256=run['reference_sha256']),
        calls=[dict(id='c0',name='reference',status='received',request_sha256=saved['request_sha256'],
            response_sha256=saved['response_sha256'])]))
    result=subject.capture_reference(dict(run,existing_reference_outputs=[str(reading)]),provider_factory=forbidden)
    assert result['understanding']==saved['understanding']
    assert next(Path(run['subject_cache_root']).glob('*/meta.json')).exists()
    subject.verify_subject(result)


@pytest.mark.parametrize('damage',['missing','receipt','understanding','media','release'])
def test_registered_invalid_subject_never_produces(run,tmp_path,damage):
    original=tmp_path/'original/subject'
    saved=collected(dict(run,output=str(original)))
    if damage=='missing':(original/'subject.json').unlink()
    elif damage=='receipt':Path(next(iter(saved['artifacts']))).write_text('{}')
    elif damage=='understanding':
        write(original/'subject.json',dict(saved,understanding=dict(theme='改写')))
    elif damage=='media':
        write(original/'subject.json',dict(saved,binding=dict(saved['binding'],reference_sha256='different')))
    else:
        write(original/'subject.json',dict(saved,binding=dict(saved['binding'],production_files={'other':'hash'})))
    with pytest.raises(ValueError,match='^subject_reuse_only_no_completed_result$'):
        subject.capture_reference(dict(run,existing_reference_outputs={'t1':str(original)}),provider_factory=forbidden)
    assert not Path(run['subject_cache_root']).exists()


@pytest.mark.parametrize('status',['submitted','uncertain'])
@pytest.mark.parametrize('history',['registered','subject_outputs','current','failure'])
def test_unresolved_original_is_blocked_without_production(run,tmp_path,status,history):
    original=tmp_path/'old/subject'
    job=dict(run)
    if history=='registered':job['existing_reference_outputs']={'t1':str(original)}
    elif history=='subject_outputs':job['subject_outputs']={'t1':str(original)}
    elif history=='failure':job['subject_failures']={'t1':dict(status='blocked_unknown',original_output=str(original))}
    else:original=Path(run['output'])
    ledger=write(original/'production_stage/library_state.json',
        dict(calls=[dict(id='c0',name='reference',status=status)]))
    before=ledger.read_bytes()
    with pytest.raises(RuntimeError,match='subject_original_unknown_no_replay') as error:
        subject.capture_reference(job,provider_factory=forbidden)
    assert cli.failure(error.value)['status']=='blocked_unknown'
    assert ledger.read_bytes()==before
    assert not Path(run['subject_cache_root']).exists()


def test_unresolved_original_remains_blocked_in_task_rows(run,tmp_path,monkeypatch):
    original=tmp_path/'old/subject'
    write(original/'production_stage/library_state.json',dict(calls=[dict(id='c0',status='submitted')]))
    case=dict(id='t1',reference=dict(path=run['reference'],sha256=run['reference_sha256']),
        labels=dict(elements=['付诸行动']))
    manifest=dict(protocol_version='v4',judge_backend='glm_text',protocol_sha256='frozen',cases=[case],
        receipts=str(tmp_path/'receipts'),production=dict(run,existing_reference_outputs={'t1':str(original)}))
    point=dict(name='before_last_element',condition='forecast@before_last_element',cut_s=1)
    monkeypatch.setattr(cli.p,'load',lambda path:manifest)
    monkeypatch.setattr(cli.p,'task_points',lambda *args:[('story',None),('forecast',point)])
    monkeypatch.setattr(cli.p,'prefix',forbidden)
    def child(command,**kwargs):
        job=json.loads(Path(command[-2]).read_text())
        try:subject.capture_reference(job,provider_factory=forbidden)
        except RuntimeError as error:return SimpleNamespace(returncode=1,stderr=str(error))
        pytest.fail('unresolved original must block')
    monkeypatch.setattr(cli.subprocess,'run',child)
    cli.task(SimpleNamespace(manifest='memory',case='t1',output=tmp_path/'report'))
    folder=tmp_path/'report/cases/t1'
    for name in ('subject-status','story','forecast@before_last_element'):
        assert json.loads((folder/(name+'.json')).read_text())['status']=='blocked_unknown'


@pytest.mark.parametrize('failure',[dict(status='blocked_unknown'),dict(status='submitted'),
    dict(status='uncertain'),dict(status='failed',calls=[dict(status='submitted')])])
def test_manifest_unresolved_call_cannot_replay_without_original_directory(run,failure):
    with pytest.raises(RuntimeError,match='subject_original_unknown_no_replay'):
        subject.capture_reference(dict(run,subject_failures={'t1':failure}),provider_factory=forbidden)


def producer(run,monkeypatch,events):
    from omni_story.library import pipeline
    value=dict(theme='付诸行动')
    class State:
        def __init__(self,output):
            self.output=Path(output);self.path=self.output/'library_state.json'
            self.input_lock=dict(reference_sha256=run['reference_sha256']);self.data=dict(calls=[])
        def begin_call(self,name,request,**kwargs):
            events.append('begin')
            folder=self.output/'calls/c0'
            write(folder/'request.json',request)
            call=dict(id='c0',name=name,status='submitted',request_sha256=json_sha(request))
            self.data['calls'].append(call)
            write(self.path,self.data)
            return call,folder
        def _reload(self):pass
    class Delegate:
        exclusions=[]
        def __init__(self,state):self.state=state
        def call(self,name,prompt,media,validator,**kwargs):
            request=dict(arguments=dict(video_source=str(media)),media_sha256=subject.digest(media))
            call,folder=self.state.begin_call(name,request)
            response=dict(result=value)
            write(folder/'response.json',response);write(folder/'parsed.json',value)
            call.update(status='received',response_sha256=json_sha(response));write(self.state.path,self.state.data)
            events.append('received')
            return value
    def execute(reference,library,output,*,model_factory,**kwargs):
        model_factory(State(output)).call('reference','original prompt',reference,lambda value:None)
    monkeypatch.setattr(pipeline,'execute',execute)
    return Delegate


@pytest.mark.parametrize('registered_elsewhere',[False,True])
def test_unregistered_case_produces_once_through_budget_gate(run,tmp_path,monkeypatch,registered_elsewhere):
    events=[];delegate=producer(run,monkeypatch,events)
    job=dict(run,library=run['reference'],registry_path=str(tmp_path/'registry'),external_budget_gate=['fake-budget-gate'])
    if registered_elsewhere:job['existing_reference_outputs']={'different_case':str(tmp_path/'other/subject')}
    def gate(command,**kwargs):
        assert command[0]=='fake-budget-gate'
        assert command[-2:]==['--provider','glm_coding_plan']
        assert events==[];events.append('budget')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subject.subprocess,'run',gate)
    first=subject.capture_reference(job,provider_factory=delegate)
    assert events==['budget','begin','received']
    second=subject.capture_reference(dict(job,output=str(tmp_path/'again/subject')),provider_factory=forbidden)
    assert second==first and events==['budget','begin','received']
    subject.verify_subject(first)


def test_rejected_budget_gate_prevents_call_and_cache(run,tmp_path,monkeypatch):
    events=[];delegate=producer(run,monkeypatch,events)
    monkeypatch.setattr(subject.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1,stderr='budget exhausted'))
    job=dict(run,library=run['reference'],registry_path=str(tmp_path/'registry'),external_budget_gate=['fake-budget-gate'])
    with pytest.raises(RuntimeError,match='subject_budget_gate_failed') as error:
        subject.capture_reference(job,provider_factory=delegate)
    assert cli.failure(error.value)['status']=='blocked_budget'
    assert events==[] and not Path(run['subject_cache_root']).exists()


def test_new_production_requires_budget_gate(run,tmp_path,monkeypatch):
    producer(run,monkeypatch,[])
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE',raising=False)
    with pytest.raises(ValueError,match='old_production_requires_external_budget_gate'):
        subject.capture_reference(dict(run,library=run['reference'],registry_path=str(tmp_path/'registry')))


def test_legacy_reuse_only_miss_still_blocks(run):
    job=dict(run,allow_unregistered_reference=False)
    with pytest.raises(ValueError,match='^subject_reuse_only_no_completed_result$'):
        subject.capture_reference(job,provider_factory=forbidden)


@pytest.mark.parametrize('protocol,condition',[('v4','full'),('v4','prefix@3'),('v3','full')])
def test_only_v4_full_enables_unregistered_production(tmp_path,monkeypatch,protocol,condition):
    folder=tmp_path/'cases/t1';jobs=[]
    manifest=dict(protocol_version=protocol,judge_backend='glm_text',
        production=dict(existing_reference_outputs={'t1':'original/subject'},reuse_only=True))
    case=dict(id='t1',reference=dict(path='media',sha256='sha'))
    def child(command,**kwargs):
        path=command[-2] if protocol=='v4' else command[-1]
        job=json.loads(Path(path).read_text());jobs.append(job)
        write(Path(job['output'])/'subject.json',dict(status='reference_collected'))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.subprocess,'run',child)
    cli.subject(case,manifest,folder,condition=condition,cache_root=tmp_path/'cache')
    assert bool(jobs[0].get('allow_unregistered_reference'))==(protocol=='v4' and condition=='full')
    assert jobs[0]['existing_reference_outputs']==manifest['production']['existing_reference_outputs']
    if protocol=='v3':assert 'case_id' not in jobs[0] and 'subject_cache_root' not in jobs[0]
