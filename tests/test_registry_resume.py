import json
from pathlib import Path

import pytest

from omni_story.evaluation import production_subject as subject, reference_core as core, reference_cli as cli
from omni_story.evaluation.subject_cache import SubjectCache
from omni_story.library.state import LibraryStopped, json_sha, write_json


def forbidden(*args,**kwargs):
    pytest.fail('No model request or extra production attempt is allowed')


@pytest.fixture
def run(tmp_path,monkeypatch):
    from omni_story.library import pipeline
    release=tmp_path/'release';release.mkdir()
    media=tmp_path/'media';media.write_bytes(b'minimal media')
    monkeypatch.setattr(pipeline,'__file__',str(release/'src/omni_story/library/pipeline.py'))
    monkeypatch.setattr(pipeline,'execute',forbidden)
    return dict(production_root=str(release),production_files={},reference=str(media),
        reference_sha256=subject.digest(media),output=str(tmp_path/'report/cases/t1/subject'),
        library=str(media),registry_path=str(tmp_path/'registry.json'),configuration={},
        asr=False,asr_model_dir=None,case_id='t1',condition='full',
        subject_cache_root=str(tmp_path/'cache'),allow_unregistered_reference=True)


def collected(run,output=None,stage=None):
    output=Path(output or run['output']);stage=Path(stage or output/'production_stage')
    value=dict(theme='付诸行动')
    request=dict(arguments=dict(video_source=run['reference']),media_sha256=run['reference_sha256'])
    response=dict(result=value);folder=stage/'calls/c0';artifacts={}
    for name,data in (('request.json',request),('response.json',response),('parsed.json',value)):
        path=folder/name;write_json(path,data);artifacts[str(path)]=subject.digest(path)
    saved=dict(status='reference_collected',manual_correction=False,call_id='c0',
        binding={k:run[k] for k in ('reference','reference_sha256','production_root',
            'production_files','configuration','asr_model_dir')},
        understanding=value,understanding_sha256=json_sha(value),artifacts=artifacts,
        request_sha256=json_sha(request),response_sha256=json_sha(response),
        media_path=run['reference'],media_sha256=run['reference_sha256'])
    write_json(output/'subject.json',saved)
    return saved


def metadata(run):
    return json.loads((Path(run['output'])/'reuse.json').read_text())


def cached(run,saved):
    cache=SubjectCache(run['subject_cache_root'])
    meta=json.loads(next(cache.root.glob('*/meta.json')).read_text())
    assert cache.get(meta['key_fields'])==saved['understanding']


def registry_stop(monkeypatch,original,resume=forbidden):
    from omni_story.library import pipeline
    error=LibraryStopped('same_inputs_already_registered_use_original_output:'+str(original))
    attempts=[]
    def execute(reference,library,output,**kwargs):
        attempts.append(Path(output))
        if len(attempts)==1:raise error
        assert Path(output)==original
        resume(output)
    monkeypatch.setattr(pipeline,'execute',execute)
    return error,attempts


@pytest.mark.parametrize('location',['stage','parent'])
def test_registered_complete_result_reused_and_cached(run,tmp_path,monkeypatch,location):
    original=tmp_path/'original/subject/production_stage'
    source=original if location=='stage' else original.parent
    saved=collected(run,source,stage=original)
    before={p:p.read_bytes() for p in [source/'subject.json',*(Path(p) for p in saved['artifacts'])]}
    _,attempts=registry_stop(monkeypatch,original)
    result=subject.capture_reference(run,provider_factory=forbidden)
    assert result==saved and len(attempts)==1
    assert metadata(run)['reused_from']==str(original)
    cached(run,saved)
    assert all(path.read_bytes()==raw for path,raw in before.items())


def test_cache_precedes_existing_outputs(run,tmp_path,monkeypatch):
    saved=collected(run)
    subject.capture_reference(run,provider_factory=forbidden)
    monkeypatch.setattr(subject,'_existing_subject',forbidden)
    job=dict(run,output=str(tmp_path/'other/subject'),existing_reference_outputs=['unread'])
    assert subject.capture_reference(job,provider_factory=forbidden)==saved


def test_invalid_existing_binding_diagnosed_before_registered_reuse(run,tmp_path,monkeypatch):
    invalid=tmp_path/'invalid/subject';wrong=collected(run,invalid)
    wrong['binding']['reference_sha256']='wrong';write_json(invalid/'subject.json',wrong)
    original=tmp_path/'original/subject/production_stage'
    saved=collected(run,original.parent,stage=original)
    _,attempts=registry_stop(monkeypatch,original)
    job=dict(run,existing_reference_outputs={'t1':str(invalid)})
    assert subject.capture_reference(job,provider_factory=forbidden)==saved and len(attempts)==1
    assert metadata(job)['reused_from']==str(original)
    assert any(d['reason']=='subject_reference_changed' for d in metadata(job)['reuse_diagnosis'])
    cached(job,saved)


@pytest.mark.parametrize('registry',['mapping','list'])
def test_existing_stage_reused_before_production(run,tmp_path,registry):
    original=tmp_path/'old/subject/production_stage'
    saved=collected(run,original.parent,stage=original)
    paths={'t1':str(original)} if registry=='mapping' else [str(original)]
    job=dict(run,existing_reference_outputs=paths,reuse_only=True)
    assert subject.capture_reference(job,provider_factory=forbidden)==saved
    assert metadata(job)['reused_from']==str(original)
    cached(job,saved)


def test_registered_received_reading_reused_without_subject_receipt(run,tmp_path,monkeypatch):
    original=tmp_path/'old/subject/production_stage'
    saved=collected(run,original.parent,stage=original)
    (original.parent/'subject.json').unlink()
    write_json(original/'reference_reading.json',dict(saved['understanding'],reference_sha256=run['reference_sha256']))
    write_json(original/'library_state.json',dict(input_lock=dict(reference_sha256=run['reference_sha256']),
        calls=[dict(id='c0',name='reference',status='received',request_sha256=saved['request_sha256'],
            response_sha256=saved['response_sha256'])]))
    _,attempts=registry_stop(monkeypatch,original)
    result=subject.capture_reference(run,provider_factory=forbidden)
    assert result['understanding']==saved['understanding'] and len(attempts)==1
    assert metadata(run)['reused_from']==str(original)
    cached(run,result)


@pytest.mark.parametrize('damage,reason',[
    ('binding','subject_reference_changed'),('understanding','subject_understanding_changed'),
    ('receipt','subject_original_receipt_changed')])
def test_failed_verification_records_reason(run,tmp_path,damage,reason):
    original=tmp_path/'old/subject';saved=collected(run,original)
    if damage=='binding':saved['binding']['reference_sha256']='wrong'
    elif damage=='understanding':saved['understanding']['theme']='改写'
    else:Path(next(iter(saved['artifacts']))).write_text('{}')
    write_json(original/'subject.json',saved)
    job=dict(run,existing_reference_outputs={'t1':str(original)},reuse_only=True)
    with pytest.raises(ValueError,match='subject_reuse_only_no_completed_result'):
        subject.capture_reference(job,provider_factory=forbidden)
    assert any(item['reason']==reason and item['source']==str(original/'subject.json')
        for item in metadata(job)['reuse_diagnosis'])
    assert not Path(run['subject_cache_root']).exists()


@pytest.mark.parametrize('statuses',[[],['received'],['failed_known'],['received','failed_known'],None])
@pytest.mark.parametrize('ledger_name',['library_state.json','state.json'])
def test_known_or_empty_ledger_resumes_original_once(run,tmp_path,monkeypatch,statuses,ledger_name):
    original=tmp_path/'old/subject/production_stage';original.mkdir(parents=True)
    ledger=original/ledger_name
    if statuses is not None:write_json(ledger,dict(calls=[dict(status=s) for s in statuses]))
    before=ledger.read_bytes() if ledger.exists() else None
    resumed=[]
    def resume(output):
        resumed.append(output)
        collected(run,stage=output)
        raise subject.ReferenceCollected()
    _,attempts=registry_stop(monkeypatch,original,resume)
    result=subject.capture_reference(run,provider_factory=forbidden)
    assert resumed==[original] and len(attempts)==2
    assert metadata(run)['resumed_in']==str(original)
    assert before is None or ledger.read_bytes()==before
    cached(run,result)


def test_absent_registered_directory_is_delegated_to_production_once(run,tmp_path,monkeypatch):
    original=tmp_path/'absent/production_stage';resumed=[]
    failure=LibraryStopped('registered_task_state_missing_do_not_reset_budget')
    def resume(output):
        resumed.append(output);raise failure
    _,attempts=registry_stop(monkeypatch,original,resume)
    with pytest.raises(LibraryStopped) as error:
        subject.capture_reference(run,provider_factory=forbidden)
    assert error.value is failure and resumed==[original] and len(attempts)==2
    assert metadata(run)['resumed_in']==str(original) and not original.exists()


@pytest.mark.parametrize('status',['submitted','uncertain'])
@pytest.mark.parametrize('ledger_name',['library_state.json','state.json'])
def test_unknown_ledger_blocks_without_resume(run,tmp_path,monkeypatch,status,ledger_name):
    original=tmp_path/'old/production_stage'
    ledger=original/ledger_name;write_json(ledger,dict(calls=[dict(status=status)]))
    before=ledger.read_bytes();_,attempts=registry_stop(monkeypatch,original)
    with pytest.raises(RuntimeError,match='subject_original_unknown_no_replay') as error:
        subject.capture_reference(run,provider_factory=forbidden)
    assert cli.failure(error.value)['status']=='blocked_unknown'
    assert metadata(run)['status']=='blocked_unknown' and len(attempts)==1
    assert 'resumed_in' not in metadata(run) and ledger.read_bytes()==before
    assert not Path(run['subject_cache_root']).exists()


@pytest.mark.parametrize('pending',[False,True])
def test_invalid_registered_result_does_not_resume(run,tmp_path,monkeypatch,pending):
    original=tmp_path/'old/subject/production_stage'
    saved=collected(run,original.parent,stage=original)
    Path(next(iter(saved['artifacts']))).write_text('{}')
    if pending:write_json(original/'state.json',dict(calls=[dict(status='uncertain')]))
    original_error,attempts=registry_stop(monkeypatch,original)
    with pytest.raises(RuntimeError) as error:
        subject.capture_reference(run,provider_factory=forbidden)
    assert len(attempts)==1
    if pending:assert cli.failure(error.value)['status']=='blocked_unknown'
    else:assert error.value is original_error
    assert any(d['reason']=='subject_original_receipt_changed' for d in metadata(run)['reuse_diagnosis'])
    assert not Path(run['subject_cache_root']).exists()


@pytest.mark.parametrize('ledger',[dict(calls=[dict(status='unexpected')]),dict(calls='invalid'),None])
def test_invalid_registry_ledger_preserves_original_error(run,tmp_path,monkeypatch,ledger):
    original=tmp_path/'old/production_stage';original.mkdir(parents=True)
    path=original/'library_state.json'
    if ledger is None:path.write_text('{')
    else:write_json(path,ledger)
    before=path.read_bytes();original_error,attempts=registry_stop(monkeypatch,original)
    with pytest.raises(LibraryStopped) as error:
        subject.capture_reference(run,provider_factory=forbidden)
    assert error.value is original_error and len(attempts)==1
    assert metadata(run)['reuse_diagnosis'] and path.read_bytes()==before


def test_resume_registry_error_is_not_retried(run,tmp_path,monkeypatch):
    original=tmp_path/'old/production_stage';original.mkdir(parents=True)
    again=LibraryStopped('same_inputs_already_registered_use_original_output:'+str(original))
    def resume(output):raise again
    _,attempts=registry_stop(monkeypatch,original,resume)
    with pytest.raises(LibraryStopped) as error:
        subject.capture_reference(run,provider_factory=forbidden)
    assert error.value is again and attempts.count(original)==1
    assert metadata(run)['resumed_in']==str(original)


@pytest.mark.parametrize('message',['unrelated_error',
    'same_inputs_already_registered_use_original_output:',
    'same_inputs_already_registered_use_original_output:relative/path'])
def test_unparseable_registry_error_preserved(run,monkeypatch,message):
    from omni_story.library import pipeline
    original_error=LibraryStopped(message);attempts=[]
    def execute(*args,**kwargs):
        attempts.append(args[2]);raise original_error
    monkeypatch.setattr(pipeline,'execute',execute)
    with pytest.raises(LibraryStopped) as error:
        subject.capture_reference(run,provider_factory=forbidden)
    assert error.value is original_error and len(attempts)==1


def test_legacy_registry_error_does_not_resume(run,tmp_path,monkeypatch):
    original=tmp_path/'old/production_stage';original.mkdir(parents=True)
    original_error,attempts=registry_stop(monkeypatch,original)
    job={k:v for k,v in run.items() if k!='subject_cache_root'}
    with pytest.raises(LibraryStopped) as error:
        subject.capture_reference(job,provider_factory=forbidden)
    assert error.value is original_error and len(attempts)==1
    assert not (Path(run['output'])/'reuse.json').exists()


def test_report_rows_include_subject_reuse_metadata(run,tmp_path):
    folder=Path(run['output']).parent;report=folder.parents[1]/'report.json'
    diagnosis=[dict(source='old/subject.json',reason='subject_received_binding_changed',error_type='ValueError')]
    write_json(Path(run['output'])/'reuse.json',dict(reused_from='registered/production_stage',reuse_diagnosis=diagnosis))
    prefix=folder/'prefix-condition/before_reveal/subject'
    write_json(prefix/'reuse.json',dict(resumed_in='prefix/production_stage'))
    rows=[dict(case_id='t1',template='01_模板',task='story',status='failed'),
        dict(case_id='t1',template='01_模板',task='forecast',condition='forecast@before_reveal',status='failed')]
    result=dict(protocol_sha256='frozen',rows=rows,subjects=[dict(case_id='t1',original_output=run['output'])],
        label_sources={},prefix_subset={},story_leakage={},
        groups={task:dict(planned=1,scored=0,means_scored={}) for task in ('elements','forecast','story','bgm')})
    manifest=dict(protocol_version='v4')
    write_json(report,result)
    core.report_text(manifest,result)
    saved=json.loads(report.read_text())
    assert saved['rows'][0]['reuse_diagnosis']==diagnosis
    assert saved['rows'][0]['reused_from']=='registered/production_stage'
    assert saved['rows'][1]['resumed_in']=='prefix/production_stage'
