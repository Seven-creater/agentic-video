import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import multi_reference as multi, reference_cli as cli
from omni_story.evaluation import reference_core as core, reference_exam as exam
from omni_story.library.state import json_sha
from test_multi_reference import manifest, memory, pipeline


def runtime(monkeypatch,memory,value,**kwargs):
    seen=pipeline(monkeypatch,memory,value,**kwargs);seen['events']=[]
    original_subject=cli.subject;original_probe=exam.ask_probe
    def subject(case,protocol,folder,**options):
        seen['events'].append(('subject',case['reference']['asset_id']))
        return original_subject(case,protocol,folder,**options)
    def probe(question,directory,**options):
        kind=question.get('prompt_kind');seen['events'].append(('probe',kind))
        if kind!='other_full_plus_prefix':return original_probe(question,directory,**options)
        seen['questions'].append(copy.deepcopy(question))
        answer=dict(prediction='选择主动行动并承担代价',uncertainties=[])
        raw=json.dumps(answer,ensure_ascii=False)
        response=dict(http_status=200,body_text=json.dumps(dict(
            content=[dict(type='text',text=raw)],stop_reason='end_turn')))
        response_path=directory/'response.json';memory.put(response_path,response)
        memory.put(directory/'answer.json',dict(identity=dict(question=question),answer=answer,
            raw_text=raw,stop_reason='end_turn',receipt=dict(path=str(response_path),
                sha256=memory.sha(response_path),response_sha256=json_sha(response))))
        return answer
    monkeypatch.setattr(cli,'subject',subject);monkeypatch.setattr(exam,'ask_probe',probe)
    return seen


def run(value,root=Path('/memory/output')):
    cli.task(SimpleNamespace(manifest='memory',case='t_0',output=root))
    return root/'cases/t_0'


def prefix_only(memory,value,root=Path('/memory/output')):
    folder=root/'cases/t_0'
    for task in ('elements','story'):
        memory.put(folder/(task+'.json'),dict(case_id='t_0',task=task,status='not_applicable',
            **multi.row_metadata(value['cases'][0],value,folder)))


@pytest.mark.parametrize('k',[1,3])
def test_exactly_k_other_full_readings_precede_prefix(monkeypatch,memory,k):
    value=manifest(k);seen=runtime(monkeypatch,memory,value);prefix_only(memory,value)
    folder=run(value)
    full=[case['reference']['asset_id'] for case,_,options in seen['subjects'] if options.get('condition','full')=='full']
    assert full==[f'a{i}' for i in range(1,k+1)]
    first_prefix=next(i for i,event in enumerate(seen['events']) if event==('subject','a0_before_last_element'))
    assert all(seen['events'].index(('subject',asset))<first_prefix for asset in full)
    summaries=[json.loads(q['instruction']) for q in seen['questions'] if q.get('prompt_kind')=='template']
    if k==1:
        assert not summaries and not seen['prompts']
        assert seen['single'] and all(s['theme'].endswith('a1') for s in seen['single'])
    else:
        assert [s['reading_ids'] for s in summaries]==[['a1','a2','a3']]
        assert 'a0' not in json.dumps(summaries)
        assert seen['events'].index(('probe','template'))<first_prefix
    question=next(q for q in seen['questions'] if q.get('prompt_kind')=='other_full_plus_prefix')
    assert question['prefix_understanding']['theme'].endswith('a0_before_last_element')
    assert 'gold_rubric' not in question and 'core_evidence' not in question
    judges=[q for q in seen['questions'] if q.get('prompt_kind')=='forecast']
    assert len(judges)==2 and all(q['gold_rubric']==[dict(id='g000',text='承担代价')] for q in judges)
    row=memory.read(folder/'forecast@other_full_plus_prefix.json')
    control=memory.read(folder/'forecast@before_last_element.json')
    assert row['status']==control['status']=='scored_provisional'
    assert control['forecast_group']=='control'
    assert row['support_asset_ids']==row['other_support_asset_ids']==full
    assert row['template_path']==row['support_template_path']==str(folder/'other-full-template/template.json')
    assert row['metrics']['hidden_element_indices']==[1]
    assert row['metrics']['inference_validity']=='plausible' and row['metrics']['full_match_rate']==1
    assert not (folder/'forecast@support_plus_prefix.json').exists()
    assert not (folder/'template/template-record.json').exists()
    for item in (row,control):cli.verify_scored_row(item,value['cases'][0],value,folder)
    before=copy.deepcopy(seen);run(value);assert seen==before


@pytest.mark.parametrize('k,available',[(1,1),(3,1),(3,2),(3,3)])
def test_not_enough_other_references_skips_only_new_condition(monkeypatch,memory,k,available):
    value=manifest(k);value['cases'][0]['support_assets']=value['cases'][0]['support_assets'][:available]
    seen=runtime(monkeypatch,memory,value);prefix_only(memory,value)
    multi.validate_manifest(value,media=False)
    folder=run(value)
    row=memory.read(folder/'forecast@other_full_plus_prefix.json')
    assert row['status']=='not_applicable' and row['reason']==row['skipped_reason']=='not_enough_other_references'
    assert memory.read(folder/'forecast@before_last_element.json')['status']=='scored_provisional'
    assert not any(options.get('condition','full')=='full' for _,_,options in seen['subjects'])
    assert not any(q.get('prompt_kind') in ('template','other_full_plus_prefix') for q in seen['questions'])


def test_short_reference_list_still_records_prefix_rows(monkeypatch,memory):
    value=manifest(3);value['cases'][0]['support_assets']=value['cases'][0]['support_assets'][:1]
    seen=runtime(monkeypatch,memory,value);multi.validate_manifest(value,media=False);folder=run(value)
    assert memory.read(folder/'forecast@other_full_plus_prefix.json')['reason']=='not_enough_other_references'
    assert memory.read(folder/'forecast@before_last_element.json')['status']=='scored_provisional'
    assert memory.read(folder/'elements.json')['reason']=='not_enough_references'
    assert memory.read(folder/'story.json')['reason']=='not_enough_references'
    assert all(options.get('condition','full')!='full' for _,_,options in seen['subjects'])


@pytest.mark.parametrize('k',[1,3])
def test_insufficient_others_keeps_full_template_tasks_and_report(monkeypatch,memory,k):
    value=manifest(k);value['cases'][0]['support_assets']=value['cases'][0]['support_assets'][:k]
    seen=runtime(monkeypatch,memory,value);multi.validate_manifest(value,media=False);folder=run(value)
    for name in ('elements','story','forecast@before_last_element'):
        row=memory.read(folder/(name+'.json'))
        assert row['status']=='scored_provisional'
        assert row['support_asset_ids']==[f'a{i}' for i in range(k)]
        cli.verify_scored_row(row,value['cases'][0],value,folder)
    assert memory.read(folder/'template/template-record.json')['binding']['reading_ids']==[f'a{i}' for i in range(k)]
    row=memory.read(folder/'forecast@other_full_plus_prefix.json')
    assert row['status']=='not_applicable' and row['reason']=='not_enough_other_references'
    assert not any(q.get('prompt_kind')=='other_full_plus_prefix' for q in seen['questions'])
    core.music_tasks(value,folder.parents[1])
    cli.report(SimpleNamespace(manifest='memory',output=folder.parents[1],human_reviews=None))
    conditions=memory.read(folder.parents[1]/'report.json')['groups']['forecast']['conditions']
    assert conditions['forecast@before_last_element']['scored']==1
    assert conditions['forecast@other_full_plus_prefix']['statuses']==dict(not_applicable=1)
    assert conditions['forecast@other_full_plus_prefix']['means_scored']=={}


@pytest.mark.parametrize('k',[1,3])
@pytest.mark.parametrize('status',['blocked_unknown','failed'])
def test_bad_extra_reference_keeps_original_template_tasks(monkeypatch,memory,k,status):
    value=manifest(k);asset_id=f'a{k}'
    seen=runtime(monkeypatch,memory,value,
        **({'blocked':asset_id} if status=='blocked_unknown' else {'failed':asset_id}))
    folder=run(value)
    for name in ('elements','story','forecast@before_last_element'):
        row=memory.read(folder/(name+'.json'))
        assert row['status']=='scored_provisional'
        cli.verify_scored_row(row,value['cases'][0],value,folder)
    row=memory.read(folder/'forecast@other_full_plus_prefix.json')
    assert row['status']==status and row['support_errors'][0]['asset_id']==asset_id
    full=[c['reference']['asset_id'] for c,_,options in seen['subjects'] if options.get('condition','full')=='full']
    assert full==[f'a{i}' for i in range(k+1)]
    assert not any(q.get('prompt_kind')=='other_full_plus_prefix' for q in seen['questions'])
    before=copy.deepcopy(seen);run(value);assert seen==before


@pytest.mark.parametrize('k',[1,3])
def test_other_summary_rejects_target_before_request(monkeypatch,memory,k):
    value=manifest(k);seen=runtime(monkeypatch,memory,value);case=value['cases'][0]
    folder=Path('/memory/output/cases/t_0')
    sources,error=multi.collect_support(case,value,folder)
    assert error is None
    with pytest.raises(ValueError,match='template_sources_changed'):
        multi.summarize(case,value,folder,sources,other_only=True)
    assert not seen['questions'] and not seen['prompts']


@pytest.mark.parametrize('change',['duplicate_id','duplicate_sha','query','invalid'])
def test_last_other_reference_is_validated(memory,change):
    value=manifest(3);case=value['cases'][0]
    asset=case['support_assets'][3]
    if change=='duplicate_id':asset['asset_id']=case['reference']['asset_id']
    elif change=='duplicate_sha':asset['sha256']=case['reference']['sha256']
    elif change=='query':asset['sha256']=case['query']['sha256']
    else:asset['duration_s']=0
    with pytest.raises(ValueError):multi.validate_manifest(value,media=False)


@pytest.mark.parametrize('k',[0,2,5,True,'3',None])
def test_only_one_and_three_are_allowed(k):
    with pytest.raises(ValueError,match='support_k_invalid'):
        multi.validate_manifest(manifest(k) if k is not None else dict(manifest(),support_k=None),media=False)


@pytest.mark.parametrize('inference',[None,'invalid'])
def test_new_forecast_requires_inference_validity(monkeypatch,memory,inference):
    value=manifest(1);runtime(monkeypatch,memory,value,inference=inference);prefix_only(memory,value)
    folder=run(value)
    row=memory.read(folder/'forecast@other_full_plus_prefix.json')
    assert row['status']=='failed' and 'invalid_received_reply' in row['error']


def test_forecast_report_separates_control_and_other_means(monkeypatch,memory):
    value=manifest(3);runtime(monkeypatch,memory,value);folder=run(value)
    core.music_tasks(value,folder.parents[1])
    cli.report(SimpleNamespace(manifest='memory',output=folder.parents[1],human_reviews=None))
    result=memory.read(folder.parents[1]/'report.json')
    groups=result['support_k_groups']['3']['forecast']['conditions']
    assert set(groups)=={'forecast@before_last_element','forecast@other_full_plus_prefix'}
    assert groups==result['groups']['forecast']['conditions']
    assert result['groups']['forecast']['primary_condition']=='forecast@other_full_plus_prefix'
    for condition,group in groups.items():
        assert group['scored']==group['planned']==1 and group['means_scored']['full_match_rate']==1
        assert group['forecast_group']==('control' if condition.endswith('before_last_element') else 'other_full_plus_prefix')
    text=memory.files[str(folder.parents[1]/'report.md')]
    assert 'forecast@before_last_element (control)' in text and 'forecast@other_full_plus_prefix' in text
    rows=[dict(support_k=3,task='forecast',condition=condition,status=status,metrics=dict(full_match_rate=rate))
        for condition,status,rate in [('forecast@before_last_element','scored_provisional',.25),
            ('forecast@other_full_plus_prefix','scored_provisional',.75),
            ('forecast@other_full_plus_prefix','failed',99)]]
    groups=multi.summarize_report(dict(rows=rows))['3']['forecast']['conditions']
    assert groups['forecast@before_last_element']['means_scored']['full_match_rate']==.25
    assert groups['forecast@other_full_plus_prefix']['scored']==1
    assert groups['forecast@other_full_plus_prefix']['means_scored']['full_match_rate']==.75


@pytest.mark.parametrize('k',[1,3])
def test_rerun_reuses_old_successes_and_runs_new_condition(monkeypatch,memory,k):
    value=manifest(k);runtime(monkeypatch,memory,value)
    old_root=Path('/memory/old');run(value,old_root)
    core.music_tasks(value,old_root)
    cli.report(SimpleNamespace(manifest='memory',output=old_root,human_reviews=None))
    old_report=memory.read(old_root/'report.json')
    old_report['rows']=[r for r in old_report['rows'] if r.get('condition')!='forecast@other_full_plus_prefix']
    old_report['rows'].insert(2,dict(case_id='t_0',task='forecast',condition='forecast@support_plus_prefix',
        status='scored_provisional',prefix_cut_s=7.5))
    for row in old_report['rows']:row.pop('forecast_group',None)
    memory.put(old_root/'report.json',old_report);memory.put(old_root/'manifest.json',value)
    original={path:copy.deepcopy(saved) for path,saved in memory.files.items() if path.startswith(str(old_root)+'/')}
    current=dict(value,protocol_sha256='rerun',receipts='/memory/rerun-receipts',rerun_failed_from=[str(old_root/'report.json')])
    seen=runtime(monkeypatch,memory,current);folder=run(current)
    for task,condition in [('elements',None),('story',None),('forecast','forecast@before_last_element')]:
        row=memory.read(folder/((condition or task)+'.json'))
        source=next(r for r in old_report['rows'] if r['task']==task and r.get('condition')==condition)
        assert row==dict(source,reused_from_report=str(old_root/'report.json'))
        cli.verify_scored_row(row,current['cases'][0],current,folder)
    assert not any(case['reference']['asset_id']=='a0' for case,_,_ in seen['subjects'])
    kinds=[q.get('prompt_kind') for q in seen['questions']]
    assert 'elements' not in kinds and not any('story' in q for q in seen['questions'])
    assert kinds.count('forecast')==kinds.count('other_full_plus_prefix')==1
    assert not (folder/'template/template-record.json').exists()
    row=memory.read(folder/'forecast@other_full_plus_prefix.json')
    assert row['status']=='scored_provisional' and 'reused_from_report' not in row
    cli.verify_scored_row(row,current['cases'][0],current,folder)
    core.music_tasks(current,folder.parents[1])
    cli.report(SimpleNamespace(manifest='memory',output=folder.parents[1],human_reviews=None))
    result=memory.read(folder.parents[1]/'report.json')
    assert result['groups']['forecast']['conditions']['forecast@before_last_element']['forecast_group']=='control'
    assert all(memory.files[path]==saved for path,saved in original.items())
    assert not (folder/'forecast@support_plus_prefix.json').exists()


@pytest.mark.parametrize('k',[1,3])
def test_rerun_with_insufficient_others_sends_no_requests(monkeypatch,memory,k):
    value=manifest(k);value['cases'][0]['support_assets']=value['cases'][0]['support_assets'][:k]
    runtime(monkeypatch,memory,value);old_root=Path('/memory/old');run(value,old_root)
    core.music_tasks(value,old_root)
    cli.report(SimpleNamespace(manifest='memory',output=old_root,human_reviews=None))
    old=memory.read(old_root/'report.json')
    old['rows']=[row for row in old['rows'] if row.get('condition')!='forecast@other_full_plus_prefix']
    memory.put(old_root/'report.json',old);memory.put(old_root/'manifest.json',value)
    current=dict(value,protocol_sha256='rerun',rerun_failed_from=[str(old_root/'report.json')])
    runtime(monkeypatch,memory,current)
    monkeypatch.setattr(cli,'subject',lambda *args,**kwargs:pytest.fail('Unexpected subject request'))
    monkeypatch.setattr(exam,'ask_probe',lambda *args,**kwargs:pytest.fail('Unexpected text request'))
    folder=run(current)
    assert memory.read(folder/'forecast@other_full_plus_prefix.json')['reason']=='not_enough_other_references'
