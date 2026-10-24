import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import prefix_policy as policy, reference_cli as cli
from omni_story.evaluation import reference_core as core, reference_exam as exam, reference_protocol as p
from omni_story.library.state import json_sha


def labels():
    return dict(elements=['已经选择行动', '愿意承担代价', '坚持自己的立场'],
        core_evidence=[dict(element_index=2, start_packet_s=8),
            dict(element_index=0, start_packet_s=2), dict(element_index=1, start_packet_s=7.5)])


def manifest(count=1):
    cases=[]
    for i in range(count):
        asset=lambda role:dict(path=f't_{i}{role}',sha256=json_sha(dict(media=f't_{i}{role}')),
            asset_id=f't_{i}{role}',duration_s=20)
        cases.append(dict(id=f't_{i}',name=f'{i:02d}_模板{i}',reference=asset('r'),query=asset('q'),
            labels=dict(labels(),provenance='model_assisted_unconfirmed',same_mechanism=True,
                prefix_information='implied',story_paragraphs=['居民终于迈出了第一步', '仍然没有行动'],
                story_target_indices=[0],bgm_question=None)))
    return dict(protocol_version='v4',prefix_policy='before_last_element',schema_version=p.SCHEMA,
        protocol_sha256='frozen',experiment_id='test',methods=p.METHODS,cases=cases,
        human_review_cases=[c['id'] for c in cases[:6]],story_seed=7,bgm_measurements=[],
        production=dict(configuration={},production_files={}),receipts='/memory/receipts')


@pytest.fixture
def memory(monkeypatch):
    files={}
    def put(path,value):files[str(path)]=copy.deepcopy(value)
    def read(path):return copy.deepcopy(files[str(path)])
    def sha(path):return json_sha(files[str(path)])
    monkeypatch.setattr(Path,'exists',lambda path:str(path) in files)
    monkeypatch.setattr(Path,'mkdir',lambda *args,**kwargs:None)
    monkeypatch.setattr(Path,'write_text',lambda path,text,**kwargs:put(path,text))
    monkeypatch.setattr(p,'read',read)
    for module in (p,cli,core,exam):
        monkeypatch.setattr(module,'write_json',put)
        monkeypatch.setattr(module,'sha256_file',sha)
        monkeypatch.setattr(module,'file_lock',lambda *args,**kwargs:nullcontext())
    monkeypatch.setattr(cli,'code_identity',lambda:'code')
    monkeypatch.setattr(core.subprocess,'run',lambda *args,**kwargs:pytest.fail('Unexpected subprocess'))
    monkeypatch.setattr(exam,'review_audio_video',lambda *args,**kwargs:pytest.fail('Unexpected API call'))
    monkeypatch.setattr(exam,'ask_probe',lambda *args,**kwargs:pytest.fail('Unexpected probe'))
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR','/memory/registry')
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE',raising=False)
    return files,put,read,sha


def pipeline(monkeypatch,memory,value,*,second_status='missing',inference='plausible'):
    from omni_story.evaluation import production_subject
    files,put,read,sha=memory
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    monkeypatch.setattr(core,'verify_subject',lambda saved:None)
    monkeypatch.setattr(production_subject,'verify_subject',lambda saved:None)
    seen=dict(prefixes=[],reviews=[],subjects=[])
    def subject(case,protocol,folder,**kwargs):
        seen['subjects'].append((case,protocol,kwargs))
        saved=dict(understanding=dict(theme='候选主旨'),understanding_sha256='reading',manual_correction=False,
            binding=dict(reference_sha256=case['reference']['sha256'],configuration=protocol['production']['configuration']))
        put(folder/'subject/subject.json',saved)
        return dict(saved,evidence_path=str(folder/'subject/subject.json'))
    def prefix(asset,cut,folder):
        seen['prefixes'].append(cut)
        path=folder/'prefix.mp4'
        put(path,dict(cut=cut,source=asset['sha256']))
        put(folder/'prefix.json',dict(binding=dict(source_sha256=asset['sha256'],end_s=cut)))
        return path
    def review(assets,roles,prompt,data,directory,**kwargs):
        seen['reviews'].append((directory.name,prompt,copy.deepcopy(data)))
        prefix=directory.name.startswith('forecast@')
        rubric=data.get('gold_rubric',[dict(id='g000'),dict(id='g001')])
        items=[]
        for i,gold in enumerate(rubric):
            status=second_status if prefix and i else 'matched'
            items.append(dict(gold_id=gold['id'],gold_validity='disputed' if status=='label_disputed' else 'valid',
                relation='missing' if status=='label_disputed' else status,
                candidate_ids=['u000'] if status=='matched' else [],reason='比较实际主旨',
                evidence=[dict(start_s=8,end_s=9,observation='坚持选择')]))
        raw=dict(items=items,limitations=[])
        if prefix and inference is not None:raw['inference_validity']=inference
        state=directory/'receipt/state.json'
        put(state,dict(status='received'))
        put(state.with_name('response.json'),dict(http_status=200,
            body_text=json.dumps(dict(choices=[dict(message=dict(content=json.dumps(raw)))]))))
        return raw,dict(path=str(state),state_sha256=sha(state),response_sha256=sha(state.with_name('response.json')))
    def probe(question,folder,**kwargs):
        case_id=folder.parent.name
        answer=dict(paragraph_ids=sorted(p.story_gold(value,case_id)))
        put(folder/'answer.json',dict(answer=answer))
        return answer
    monkeypatch.setattr(cli,'subject',subject)
    monkeypatch.setattr(p,'prefix',prefix)
    monkeypatch.setattr(exam,'paid_review',review)
    monkeypatch.setattr(exam,'ask_probe',probe)
    monkeypatch.setattr(exam,'verify_probe',lambda saved:None)
    return seen


def test_before_last_element_uses_last_start_and_hides_boundary():
    assert policy.before_last_element(labels(),20)==dict(name='before_last_element',cut_s=7.5,
        hidden_element_indices=[1,2],visible_fraction=.375)


def test_hidden_indices_are_sorted_and_deduplicated():
    value=labels()
    value['core_evidence'] += [dict(element_index=2,start_packet_s=8),dict(element_index=0,start_packet_s=7.8)]
    assert policy.before_last_element(value,20)['hidden_element_indices']==[0,1,2]


@pytest.mark.parametrize('evidence',[None,[],[{}],[None],['bad'],
    [dict(element_index=0,start_packet_s=t) for t in (None,'8',True,-1,20,30,float('nan'),float('inf'))],
    [dict(element_index=i,start_packet_s=8) for i in (None,'0',True,-1,3)]])
def test_missing_or_invalid_evidence_times_are_not_applicable(evidence):
    value=dict(elements=['要素']*3)
    if evidence is not None:value['core_evidence']=evidence
    assert policy.before_last_element(value,20)==dict(name='before_last_element',cut_s=None,
        skipped_reason='no_core_evidence_times')


def test_minimum_visible_time_custom_margin_and_rounding():
    value=dict(elements=['要素'],core_evidence=[dict(element_index=0,start_packet_s=1.8)])
    assert policy.before_last_element(value,10)==dict(name='before_last_element',cut_s=1.5,
        hidden_element_indices=[0],visible_fraction=.15)
    value['core_evidence'][0]['start_packet_s']=8.12345
    assert policy.before_last_element(value,10,margin_s=.25,min_visible_s=2)['cut_s']==7.873


@pytest.mark.parametrize('duration,start',[(1.5,1.4),(1,0.5),(.001,.0005)])
def test_cut_never_reaches_duration(duration,start):
    value=dict(elements=['要素'],core_evidence=[dict(element_index=0,start_packet_s=start)])
    assert policy.before_last_element(value,duration)==dict(name='before_last_element',cut_s=None,
        skipped_reason='duration_too_short')


def test_one_condition_and_empty_hidden_gold_is_skipped():
    value=manifest();case=value['cases'][0]
    points=[point for task,point in p.task_points(value,case) if task=='forecast']
    assert len(points)==1 and points[0]['condition']=='forecast@before_last_element'
    case['labels']['core_evidence']=[dict(element_index=0,start_packet_s=.5)]
    point=next(point for task,point in p.task_points(value,case) if task=='forecast')
    assert point['cut_s']==1.5 and point['skipped_reason']=='no_hidden_elements'
    assert core.prefix_task(case,value,Path('/memory/case'),Path('/memory/review'),point=point)==dict(
        status='not_applicable',skipped_reason='no_hidden_elements')


def test_default_v4_and_legacy_policies_keep_existing_conditions():
    value=manifest();case=value['cases'][0]
    value.pop('prefix_policy')
    case['labels']['theme_reveal_s']=8
    assert [point['condition'] for task,point in p.task_points(value,case) if task=='forecast']==[
        'forecast@before_reveal','forecast@after_reveal']
    case['labels'].pop('theme_reveal_s')
    assert next(point for task,point in p.task_points(value,case) if task=='forecast')['condition']=='forecast@legacy_fixed'
    value.update(protocol_version='v3',prefix_policy='before_last_element')
    assert list(p.task_points(value,case))==[(task,None) for task in p.TASKS]


def test_validate_and_freeze_run_policy_for_every_case_without_relabeling(monkeypatch,memory):
    files,put,read,sha=memory
    value=manifest(10);value.pop('protocol_sha256')
    value['cases'][0]['labels']['core_evidence']=[]
    value['cases'][1]['labels']['theme_reveal_s']='unused invalid reveal'
    for case in value['cases']:
        for asset in (case['reference'],case['query']):put(asset['path'],dict(media=asset['path']))
    seen=[];original=policy.before_last_element
    def check(labels,duration):
        point=original(labels,duration);seen.append(point)
        return point
    monkeypatch.setattr(policy,'before_last_element',check)
    frozen=p.freeze(value,Path('/memory/manifest.json'))
    assert len(seen)==10 and seen[0]['skipped_reason']=='no_core_evidence_times'
    assert frozen['prefix_policy']=='before_last_element'
    assert p.load('/memory/manifest.json',media=False)==frozen
    assert len(seen)==20


@pytest.mark.parametrize('selected',[None,'before_last_element'])
def test_prepare_persists_only_explicit_policy(monkeypatch,memory,selected):
    files,put,read,sha=memory
    value=manifest(10)
    put('inventory',dict(templates=[dict(folder=c['name'],assets=[c['reference'],c['query']]) for c in value['cases']],
        bgm_measurements=[]))
    put('runtime',{})
    monkeypatch.setattr(exam,'prepare_labels',lambda *args:dict(provenance='model_assisted_unconfirmed'))
    monkeypatch.setattr(p,'freeze',lambda value,path:read(path) if str(path) in files else (put(path,value) or value))
    monkeypatch.setattr(cli,'cards',lambda *args:None)
    cli.prepare(SimpleNamespace(inventory='inventory',curation=None,output=Path('/memory/output'),
        receipts=Path('/memory/receipts'),runtime='runtime',experiment_id='test',protocol_version='v4',
        bgm_measurements=None,story_seed=7,asr_profile=None,prefix_policy=selected))
    saved=read('/memory/output/manifest.json')
    assert saved.get('prefix_policy')==selected
    assert ('prefix_policy' in saved)==(selected is not None)


def test_cli_exposes_prefix_policy_option(monkeypatch):
    seen=[]
    monkeypatch.setattr(cli,'prepare',lambda args:seen.append(args.prefix_policy))
    cli.main(['prepare','--output','/memory/output','--protocol-version','v4','--prefix-policy','before_last_element'])
    assert seen==['before_last_element']


def test_review_cards_use_current_cut_hidden_gold_and_skip_reason(memory):
    files,put,read,sha=memory
    value=manifest(2)
    value['cases'][0]['labels']['prefix_cut_s']=100
    value['cases'][1]['labels']['core_evidence']=[]
    cli.cards(value,Path('/memory/output'))
    text=files['/memory/output/human-review-cards.md']
    assert '只看本参考前7.5秒；被藏要素：愿意承担代价；坚持自己的立场' in text
    assert '前缀题不适用：no_core_evidence_times' in text and '前100秒' not in text


def test_v4_hidden_gold_single_condition_metrics_verification_and_report(monkeypatch,memory):
    files,put,read,sha=memory
    value=manifest(3)
    value['cases'][1]['reference']['duration_s']=10
    value['cases'][1]['labels']['core_evidence']=[dict(element_index=0,start_packet_s=1),
        dict(element_index=1,start_packet_s=8.5),dict(element_index=2,start_packet_s=9)]
    value['cases'][2]['labels']['core_evidence']=[]
    seen=pipeline(monkeypatch,memory,value)
    root=Path('/memory/output')
    for case in value['cases']:cli.task(SimpleNamespace(manifest='memory',case=case['id'],output=root))
    core.music_tasks(value,root)
    cli.report(SimpleNamespace(manifest='memory',output=root,human_reviews=None))
    result=read(root/'report.json')
    forecasts=[r for r in result['rows'] if r['task']=='forecast']
    assert len(forecasts)==3 and {r['condition'] for r in forecasts}=={'forecast@before_last_element'}
    assert seen['prefixes']==[7.5,8.5]
    assert forecasts[2]['status']=='not_applicable' and forecasts[2]['skipped_reason']=='no_core_evidence_times'
    for row in forecasts[:2]:
        assert row['metrics']['hidden_element_indices']==[1,2]
        assert row['metrics']['full_match_rate']==.5
        assert row['metrics']['cut_s']==row['prefix_cut_s']
        assert row['metrics']['inference_validity']=='plausible'
        assert sum(row['metrics']['counts'].values())==2
    for stage,prompt,data in seen['reviews']:
        if not stage.startswith('forecast@'):continue
        assert data['gold_rubric']==[dict(id='g000',text=labels()['elements'][1]),dict(id='g001',text=labels()['elements'][2])]
        assert data['theme_context']==labels()['elements'] and '仅供判断价值方向，不计分' in prompt
    assert result['report_scope']=='single_video' and '按单条视频报告，不代表模板级结论' in result['note']
    assert result['groups']['forecast']['means_scored']['visible_fraction']==pytest.approx(.6125)
    assert [r['cut_s'] for r in result['groups']['forecast']['cut_s']]==[7.5,8.5,None]
    text=files[str(root/'report.md')]
    assert '按单条视频报告' in text.splitlines()[0] and '不代表模板级结论' in text
    assert '可见比例均值' in text and '各题cut_s' in text
    row=forecasts[0];case=value['cases'][0]
    for key,bad in [('hidden_element_indices',[0]),('visible_fraction',.9),('cut_s',8),('full_match_rate',1)]:
        changed=copy.deepcopy(row);changed['metrics'][key]=bad
        with pytest.raises(ValueError,match='core_report_metrics_changed'):
            core.verify_row(changed,case,value,root/'cases'/case['id'])


def test_disputed_hidden_elements_remain_in_denominator(monkeypatch,memory):
    files,put,read,sha=memory
    value=manifest();pipeline(monkeypatch,memory,value,second_status='label_disputed')
    root=Path('/memory/output')
    cli.task(SimpleNamespace(manifest='memory',case='t_0',output=root))
    row=read(root/'cases/t_0/forecast@before_last_element.json')
    assert row['status']=='scored_provisional' and row['metrics']['full_match_rate']==.5
    assert row['metrics']['counts']['label_disputed']==1
    core.verify_row(row,value['cases'][0],value,root/'cases/t_0')


@pytest.mark.parametrize('inference',[None,'invalid'])
def test_new_strategy_still_requires_inference_validity(monkeypatch,memory,inference):
    files,put,read,sha=memory
    value=manifest();pipeline(monkeypatch,memory,value,inference=inference)
    cli.task(SimpleNamespace(manifest='memory',case='t_0',output=Path('/memory/output')))
    row=read('/memory/output/cases/t_0/forecast@before_last_element.json')
    assert row['status']=='failed' and 'reference_exam_invalid_received_reply' in row['error']


@pytest.mark.parametrize('same',[None,False,1,'true'])
@pytest.mark.parametrize('included',[False,True])
def test_unverified_cross_video_bgm_never_enters_runtime(monkeypatch,memory,same,included):
    files,put,read,sha=memory
    value=manifest();value['cases'][0]['labels'].pop('same_mechanism')
    if same is not None:value['cases'][0]['labels']['same_mechanism']=same
    if included:value['bgm_measurements']=[dict(case_id='t_0',query_id=f'q{i}',duration_s=3,
        music_rms_db=-20,speech_words=0) for i in range(2)]
    monkeypatch.setattr(core,'_full_subject',lambda *args:pytest.fail('Unverified cross-video subject'))
    core.music_tasks(value,Path('/memory/output'))
    assert read('/memory/output/cases/t_0/bgm.json')['status']=='not_applicable_cross_video_unverified'


def test_bgm_gallery_and_queries_only_use_verified_cases(monkeypatch,memory):
    files,put,read,sha=memory
    value=manifest(2);value['cases'][0]['labels']['same_mechanism']=False
    value['bgm_measurements']=[dict(case_id=c['id'],query_id=f'q{i}',duration_s=3,
        music_rms_db=-20,speech_words=0) for c in value['cases'] for i in range(2)]
    value['production']['music_candidate']=dict(root='frozen',files={})
    monkeypatch.setattr(core,'_full_subject',lambda case,*args:dict(case_id=case['id'],subject_path='subject',understanding_sha256='reading'))
    monkeypatch.setattr(core,'verify_tree',lambda *args:None)
    calls=[]
    def run(command,**kwargs):
        job=read(command[-1]);calls.append(job)
        put(Path(job['output'])/'answer.json',dict(status='no_match',candidates=[]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(core.subprocess,'run',run)
    put('/memory/output/cases/t_1/elements.json',dict(metrics=dict(full_match_rate=1)))
    core.music_tasks(value,Path('/memory/output'))
    assert len(calls)==1 and [c['case_id'] for c in calls[0]['gallery']]==['t_1']
    assert calls[0]['query']==value['cases'][1]['query']
    assert read('/memory/output/cases/t_1/bgm.json')['status']=='scored_provisional'


def test_report_verification_rejects_unverified_cross_video_score():
    with pytest.raises(ValueError,match='core_music_cross_video_unverified'):
        core.verify_row(dict(task='bgm'),dict(labels={}),dict(protocol_version='v4'),Path('/memory/output'))
