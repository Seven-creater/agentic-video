import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import multi_reference as multi, reference_cli as cli
from omni_story.evaluation import reference_core as core, reference_exam as exam, reference_protocol as p
from omni_story.library.state import json_sha


def manifest(k=3):
    assets=[dict(asset_id=f'a{i}',sha256=f'sha{i}',path=f'/memory/a{i}.mp4',duration_s=20) for i in range(6)]
    case=dict(id='t_0',name='01_模板',reference=assets[0],query=dict(assets[0],asset_id='q',sha256='query'),
        support_assets=assets,labels=dict(elements=['主动行动','承担代价'],provenance='human_confirmed',
            same_mechanism=True,prefix_information='implied',
            core_evidence=[dict(element_index=0,start_packet_s=2),dict(element_index=1,start_packet_s=8)],
            story_paragraphs=['居民决定主动迈出第一步','居民仍留在原地等待'],story_target_indices=[0],bgm_question=None))
    value=dict(protocol_version='v4',judge_backend='glm_text',prefix_policy='before_last_element',
        scoring_rule='只评明确表达的主旨',protocol_sha256='frozen',experiment_id='memory',cases=[case],
        production=dict(production_root='/memory/production',configuration={},production_files={}),
        receipts='/memory/receipts',human_review_cases=['t_0'],story_seed=7,bgm_measurements=[])
    if k is not None:value['support_k']=k
    return value


@pytest.fixture
def memory(monkeypatch):
    files={};cached={};puts=[]
    def put(path,value):files[str(path)]=copy.deepcopy(value)
    def read(path):return copy.deepcopy(files[str(path)])
    def sha(path):return json_sha(files[str(path)])
    monkeypatch.setattr(Path,'exists',lambda path:str(path) in files)
    monkeypatch.setattr(Path,'mkdir',lambda *args,**kwargs:None)
    monkeypatch.setattr(Path,'write_text',lambda path,text,**kwargs:put(path,text))
    monkeypatch.setattr(p,'read',read)
    for module in (multi,p,cli,core,exam):
        monkeypatch.setattr(module,'write_json',put)
        monkeypatch.setattr(module,'sha256_file',sha)
        monkeypatch.setattr(module,'file_lock',lambda *args,**kwargs:nullcontext())
    class Cache:
        def __init__(self,root):self.root=root
        def get(self,fields):return copy.deepcopy(cached.get(json_sha(fields)))
        def put(self,fields,value,receipts):
            assert receipts and all(str(path) in files for path in receipts)
            puts.append(copy.deepcopy(fields));cached[json_sha(fields)]=copy.deepcopy(value)
    monkeypatch.setattr(multi,'SubjectCache',Cache)
    from omni_story.evaluation import production_subject
    for module in (multi,core,production_subject):monkeypatch.setattr(module,'verify_subject',lambda saved:None)
    monkeypatch.setattr(cli,'code_identity',lambda:'code')
    monkeypatch.setattr(cli.subprocess,'run',lambda *args,**kwargs:pytest.fail('Unexpected subprocess'))
    monkeypatch.setattr(exam,'paid_review',lambda *args,**kwargs:pytest.fail('Unexpected video request'))
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR','/memory/registry')
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE',raising=False)
    return SimpleNamespace(files=files,put=put,read=read,sha=sha,cached=cached,puts=puts)


def pipeline(monkeypatch,memory,value,*,invalid=0,blocked=None,failed=None,inference='plausible'):
    seen=dict(subjects=[],questions=[],single=[],prompts=[],validations=[])
    def prompt(readings,*,reading_ids):
        seen['prompts'].append(copy.deepcopy((readings,reading_ids)))
        return json.dumps(dict(readings=readings,reading_ids=reading_ids),ensure_ascii=False)
    def validate(raw,*,reading_ids):
        seen['validations'].append(copy.deepcopy(reading_ids))
        if not isinstance(raw,dict) or raw.get('reading_ids')!=reading_ids or 'theme' not in raw:
            raise ValueError('template_shape_invalid')
        return raw
    def single(reading):
        seen['single'].append(copy.deepcopy(reading))
        return dict(theme=reading['theme'])
    module=SimpleNamespace(template_prompt=prompt,validate_template=validate,single_reading_as_template=single)
    monkeypatch.setattr(multi,'production_module',lambda manifest:(module,'module-sha'))
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    def subject(case,protocol,folder,**kwargs):
        asset=case['reference'];seen['subjects'].append((case,folder,kwargs))
        if asset['asset_id']==blocked:raise RuntimeError('original_unknown_no_replay')
        if asset['asset_id']==failed:raise RuntimeError('decode_failed')
        saved=dict(status='reference_collected',understanding=dict(theme='主动行动，承担代价 '+asset['asset_id']),
            manual_correction=False,binding=dict(reference_sha256=asset['sha256'],configuration=protocol['production']['configuration']))
        saved['understanding_sha256']=json_sha(saved['understanding'])
        memory.put(folder/'subject/subject.json',saved)
        return dict(saved,evidence_path=str(folder/'subject/subject.json'))
    monkeypatch.setattr(cli,'subject',subject)
    def prefix(asset,cut,folder):
        media=folder/'prefix.mp4';memory.put(media,dict(cut=cut,source=asset['sha256']))
        memory.put(folder/'prefix.json',dict(binding=dict(source_sha256=asset['sha256'],end_s=cut)))
        return media
    monkeypatch.setattr(p,'prefix',prefix)
    def save(question,directory,raw):
        text=json.dumps(raw,ensure_ascii=False)
        response=dict(http_status=200,body_text=json.dumps(dict(content=[dict(type='text',text=text)],stop_reason='end_turn')))
        response_path=directory/'response.json';memory.put(response_path,response)
        memory.put(directory/'answer.json',dict(identity=dict(question=question),answer=raw,allow_invalid=True,
            raw_text=text,stop_reason='end_turn',receipt=dict(path=str(response_path),
                sha256=memory.sha(response_path),response_sha256=json_sha(response))))
        return copy.deepcopy(raw)
    def probe(question,directory,**kwargs):
        directory=Path(directory)
        if str(directory/'answer.json') in memory.files:
            saved=memory.read(directory/'answer.json')
            assert saved['identity']['question']==question
            exam.verify_probe(saved)
            return saved['answer']
        seen['questions'].append(copy.deepcopy(question));kind=question.get('prompt_kind')
        if kind=='template':
            data=json.loads(question['instruction'])
            raw=dict(theme='主动行动，承担代价',reading_ids=data['reading_ids'])
            if invalid:raw={}
        elif kind=='template_repair':
            original=memory.read(directory.with_name('summary')/'answer.json')
            ids=json.loads(original['identity']['question']['instruction'])['reading_ids']
            raw=dict(theme='主动行动，承担代价',reading_ids=ids) if invalid==1 else {}
        elif kind=='support_plus_prefix':raw=dict(prediction='选择主动行动并承担代价',uncertainties=[])
        elif kind in ('elements','forecast','repair'):
            candidate=next(u['id'] for u in question['candidate_units'] if '主动行动' in u['text'])
            raw=dict(items=[dict(gold_id=gold['id'],gold_validity='valid',relation='matched',candidate_ids=[candidate],
                reason='原句表达主旨',evidence=[candidate]) for gold in question['gold_rubric']],limitations=[])
            if kind=='forecast' and inference is not None:raw['inference_validity']=inference
        else:raw=dict(paragraph_ids=sorted(p.story_gold(value,'t_0')))
        return save(question,directory,raw)
    monkeypatch.setattr(exam,'ask_probe',probe)
    return seen


def run(value):
    root=Path('/memory/output')
    cli.task(SimpleNamespace(manifest='memory',case=value['cases'][0]['id'],output=root))
    return root/'cases'/value['cases'][0]['id']


def test_default_path_is_unchanged(monkeypatch,memory):
    value=manifest(None);seen=pipeline(monkeypatch,memory,value)
    monkeypatch.setattr(multi,'task',lambda *args:pytest.fail('Unexpected multi-reference task'))
    folder=run(value)
    assert [point['condition'] for task,point in p.task_points(value,value['cases'][0]) if task=='forecast']==['forecast@before_last_element']
    assert seen['subjects'][0][1]==folder
    assert not seen['single'] and not any(q.get('prompt_kind')=='template' for q in seen['questions'])
    assert all('support_k' not in memory.read(folder/name) for name in ('elements.json','story.json'))
    assert str(folder/'template/template.json') not in memory.files


def test_single_maps_reading_without_summary_request(monkeypatch,memory):
    value=manifest(1);seen=pipeline(monkeypatch,memory,value);folder=run(value)
    assert memory.read(folder/'template/template.json')==dict(theme='主动行动，承担代价 a0')
    assert not seen['prompts'] and not any(q.get('prompt_kind')=='template' for q in seen['questions'])
    subjects=[(c,d,k) for c,d,k in seen['subjects'] if k.get('condition','full')=='full']
    assert len(subjects)==1 and subjects[0][1]==folder/'support/a0'
    assert subjects[0][2]['cache_root']==Path('/memory/output/subject_cache')
    row=memory.read(folder/'forecast@support_plus_prefix.json')
    assert row['status']=='not_applicable' and row['skipped_reason']=='support_k_one'
    assert memory.puts[0]['media_sha256']==json_sha(dict(k=1,support_sha256s=['sha0']))
    for task,point in p.task_points(value,value['cases'][0]):
        if task=='bgm':continue
        row=memory.read(p.row_path(folder,task,point))
        assert row['support_k']==1 and row['support_asset_ids']==['a0']
        if row['status']=='scored_provisional':cli.verify_scored_row(row,value['cases'][0],value,folder)


@pytest.mark.parametrize('invalid',[0,1,2])
def test_three_summary_once_and_at_most_one_repair(monkeypatch,memory,invalid):
    value=manifest();seen=pipeline(monkeypatch,memory,value,invalid=invalid)
    folder=Path('/memory/output/cases/t_0')
    sources,error=multi.collect_support(value['cases'][0],value,folder)
    assert error is None
    if invalid==2:
        with pytest.raises(ValueError,match='shape_invalid'):multi.summarize(value['cases'][0],value,folder,sources)
        with pytest.raises(ValueError,match='shape_invalid'):multi.summarize(value['cases'][0],value,folder,sources)
    else:
        first=multi.summarize(value['cases'][0],value,folder,sources)
        assert first['template']['reading_ids']==['a0','a1','a2']
        assert multi.summarize(value['cases'][0],value,folder,sources)==first
        other=Path('/memory/another/cases/t_0')
        assert multi.summarize(value['cases'][0],value,other,sources)==first
        assert memory.read(other/'template/template.json')==first['template']
        assert len(memory.puts)==1
    kinds=[q['prompt_kind'] for q in seen['questions']]
    assert kinds.count('template')==1 and kinds.count('template_repair')==(invalid>0)
    assert all(c['reference']['asset_id'] in ('a0','a1','a2') for c,_,_ in seen['subjects'])
    if invalid:
        repair=memory.read(folder/'template/repair.json')
        assert repair['calls']==1 and bool(repair['defects_after'])==(invalid==2)


@pytest.mark.parametrize('status',['blocked_unknown','failed'])
@pytest.mark.parametrize('asset_id',['a0','a1','a2'])
def test_any_bad_support_blocks_all_template_tasks(monkeypatch,memory,status,asset_id):
    value=manifest();seen=pipeline(monkeypatch,memory,value,
        **({'blocked':asset_id} if status=='blocked_unknown' else {'failed':asset_id}))
    folder=run(value)
    assert len(seen['subjects'])==3 and not seen['questions']
    for task,point in p.task_points(value,value['cases'][0]):
        if task=='bgm':continue
        row=memory.read(p.row_path(folder,task,point))
        assert row['status']==status and row['support_errors'][0]['asset_id']==asset_id
        assert ('unknown_no_replay' if status=='blocked_unknown' else 'decode_failed') in row['error']
    run(value)
    assert len(seen['subjects'])==3 and not seen['questions']


@pytest.mark.parametrize('k',[3,5])
def test_support_forecast_omits_truncated_video_and_verifies_rows(monkeypatch,memory,k):
    value=manifest(k);seen=pipeline(monkeypatch,memory,value);folder=run(value)
    rows=[]
    for task,point in p.task_points(value,value['cases'][0]):
        if task=='bgm':continue
        row=memory.read(p.row_path(folder,task,point));rows.append(row)
        assert row['status']=='scored_provisional',row
        cli.verify_scored_row(row,value['cases'][0],value,folder)
    summaries=[json.loads(q['instruction']) for q in seen['questions'] if q.get('prompt_kind')=='template']
    assert [s['reading_ids'] for s in summaries]==[[f'a{i}' for i in range(k)],[f'a{i}' for i in range(1,k)]]
    assert 'a0' not in json.dumps(summaries[1])
    question=next(q for q in seen['questions'] if q.get('prompt_kind')=='support_plus_prefix')
    assert question['template']['reading_ids']==[f'a{i}' for i in range(1,k)]
    assert question['prefix_understanding']['theme'].endswith('a0_before_last_element')
    assert 'gold_rubric' not in question and 'core_evidence' not in question
    forecasts=[r for r in rows if r['task']=='forecast']
    assert all(r['metrics']['hidden_element_indices']==[1] and r['prefix_cut_s']==7.5 and
        r['metrics']['inference_validity']=='plausible' and r['metrics']['counts']['matched']==1 for r in forecasts)
    assert forecasts[0]['subject_path']==forecasts[1]['subject_path']
    story=next(q for q in seen['questions'] if 'story' in q)
    assert story['understanding']==memory.read(folder/'template/template.json')
    before=copy.deepcopy(seen);run(value)
    assert seen==before
    altered=copy.deepcopy(forecasts[1]);altered['answer']['prediction']='其他结局'
    with pytest.raises(ValueError,match='prefix_answer_changed'):
        cli.verify_scored_row(altered,value['cases'][0],value,folder)
    altered=copy.deepcopy(rows[0]);altered['support_asset_ids']=['a0']
    with pytest.raises(ValueError,match='row_binding_changed'):
        cli.verify_scored_row(altered,value['cases'][0],value,folder)


@pytest.mark.parametrize('inference',[None,'invalid'])
def test_forecast_still_requires_inference_validity(monkeypatch,memory,inference):
    value=manifest();pipeline(monkeypatch,memory,value,inference=inference);folder=run(value)
    for condition in ('before_last_element','support_plus_prefix'):
        row=memory.read(folder/('forecast@'+condition+'.json'))
        assert row['status']=='failed' and 'invalid_received_reply' in row['error']
        assert memory.read(folder/'repair.json')['forecast@'+condition]['calls']==1


def test_unknown_summary_is_not_replayed(monkeypatch,memory):
    value=manifest();seen=pipeline(monkeypatch,memory,value)
    calls=[]
    def unknown(question,directory,**kwargs):
        calls.append(question)
        raise RuntimeError('request_outcome_unknown_no_replay')
    monkeypatch.setattr(exam,'ask_probe',unknown)
    folder=run(value)
    assert memory.read(folder/'elements.json')['status']=='blocked_unknown'
    assert len(calls)==1
    run(value)
    assert len(calls)==1 and len(seen['subjects'])==3


@pytest.mark.parametrize('k',[None,3])
def test_subject_adapter_keeps_cache_and_allows_new_supports(monkeypatch,memory,k):
    value=manifest(k);value['production']['reuse_only']=True;calls=[]
    def completed(command,**kwargs):
        job=memory.read(command[3]);calls.append(job)
        memory.put(Path(job['output'])/'subject.json',dict(understanding={}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.subprocess,'run',completed)
    folder=Path('/memory/output/cases/t_0/support/a0')
    cli.subject(value['cases'][0],value,folder,cache_root=Path('/memory/cache'))
    assert calls[0]['subject_cache_root']=='/memory/cache' and calls[0]['condition']=='full'
    assert calls[0]['allow_unregistered_reference'] is True
    assert calls[0].get('reuse_only',False)==(k is None)
    assert calls[0]['output']==str(folder/'subject')


def test_template_and_subject_receipts_are_rechecked(monkeypatch,memory):
    value=manifest();pipeline(monkeypatch,memory,value);folder=run(value)
    row=memory.read(folder/'elements.json')
    path=folder/'template/template.json';saved=memory.read(path)
    memory.put(path,dict(theme='篡改'))
    with pytest.raises(ValueError,match='row_template_changed'):cli.verify_scored_row(row,value['cases'][0],value,folder)
    memory.put(path,saved)
    source=folder/'support/a1/subject/subject.json';reading=memory.read(source)
    reading['understanding']['theme']='篡改';memory.put(source,reading)
    with pytest.raises(ValueError,match='source_changed'):cli.verify_scored_row(row,value['cases'][0],value,folder)


@pytest.mark.parametrize('k',[0,2,True,'3',None])
def test_invalid_support_k_rejected(k):
    value=manifest();value['support_k']=k
    with pytest.raises(ValueError,match='support_k_invalid'):multi.validate_manifest(value,media=False)


def test_support_validation_and_selected_prefix_only(memory):
    value=manifest();case=value['cases'][0]
    case['support_assets'][3:]=[None]
    multi.validate_manifest(value,media=False)
    case['support_assets'][0]=dict(case['reference'],asset_id='wrong')
    with pytest.raises(ValueError,match='support_assets_invalid'):multi.validate_manifest(value,media=False)
    case['support_assets'][0]=case['reference']
    case['support_assets'][1]=dict(case['query'])
    with pytest.raises(ValueError,match='unseen_video_leakage'):multi.validate_manifest(value,media=False)


def test_imports_pinned_production_module_by_file_path(monkeypatch,memory):
    value=manifest();path=Path(value['production']['production_root'])/'src/omni_story/library/reference_template.py'
    memory.put(path,'production template source');seen=[];module=SimpleNamespace()
    def spec(name,target):
        seen.append((name,target))
        return SimpleNamespace(loader=SimpleNamespace(exec_module=lambda actual:seen.append(actual)))
    monkeypatch.setattr(multi.importlib.util,'spec_from_file_location',spec)
    monkeypatch.setattr(multi.importlib.util,'module_from_spec',lambda spec:module)
    assert multi.production_module(value)==(module,memory.sha(path))
    assert seen[0][1]==path and seen[0][0]!='omni_story.library.reference_template' and seen[1] is module


def test_reports_group_by_k_and_forecast_condition():
    rows=[]
    for k in (1,3,5):
        for task in ('elements','story','forecast'):
            rows.append(dict(support_k=k,task=task,status='scored_provisional',metrics=dict(score=k/5),
                **(dict(condition='forecast@before_last_element') if task=='forecast' else {})))
        rows.append(dict(support_k=k,task='forecast',condition='forecast@support_plus_prefix',
            status='not_applicable' if k==1 else 'scored_provisional',metrics=dict(score=1)))
    groups=multi.summarize_report(dict(rows=rows))
    assert set(groups)=={'1','3','5'}
    assert groups['3']['elements']['means_scored']==dict(score=.6)
    assert groups['1']['forecast']['planned']==2 and groups['1']['forecast']['scored']==1
    assert groups['1']['forecast']['conditions']['forecast@support_plus_prefix']['means_scored']=={}
    assert groups['5']['story']['means_scored']==dict(score=1)


def test_cli_report_contains_k_groups_and_metadata(monkeypatch,memory):
    value=manifest();pipeline(monkeypatch,memory,value);folder=run(value)
    core.music_tasks(value,Path('/memory/output'))
    cli.report(SimpleNamespace(manifest='memory',output=Path('/memory/output'),human_reviews=None))
    result=memory.read('/memory/output/report.json')
    assert result['report_scope']=='template' and set(result['support_k_groups'])=={'3'}
    assert all(r['support_k']==3 and r['support_asset_ids']==['a0','a1','a2'] and
        r['template_path']==str(folder/'template/template.json') for r in result['rows'])
    assert result['support_k_groups']['3']['forecast']['scored']==2
    assert 'forecast@support_plus_prefix' in memory.files['/memory/output/report.md']
    assert '按单条视频报告' not in memory.files['/memory/output/report.md']
    assert memory.read(folder/'bgm.json')['support_k']==3
