import copy
import hashlib
import io
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import adjudicate, budget, multi_reference as multi
from omni_story.evaluation import production_subject as producer, reference_cli as cli
from omni_story.evaluation import reference_core as core, reference_exam as exam, reference_protocol as p
from omni_story.library.state import json_sha


@pytest.fixture
def memory(monkeypatch):
    files={}
    def put(path,value):files[str(path)]=copy.deepcopy(value)
    def content(path):
        value=files[str(path)]
        if isinstance(value,bytes):return value.decode()
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    def read(path):return json.loads(content(path))
    def sha(path):return hashlib.sha256(content(path).encode()).hexdigest()
    class Handle(io.StringIO):
        def __init__(self,path,mode):
            self.path=path;self.mode=mode
            super().__init__(content(path) if str(path) in files and 'w' not in mode else '')
            if 'a' in mode:self.seek(0,2)
        def close(self):
            if not self.closed and any(flag in self.mode for flag in ('a','w')):put(self.path,self.getvalue())
            super().close()
    monkeypatch.setattr(Path,'exists',lambda path:str(path) in files)
    monkeypatch.setattr(Path,'is_file',lambda path:str(path) in files)
    monkeypatch.setattr(Path,'resolve',lambda path,**kwargs:path)
    monkeypatch.setattr(Path,'mkdir',lambda *args,**kwargs:None)
    monkeypatch.setattr(Path,'read_text',lambda path,**kwargs:content(path))
    monkeypatch.setattr(Path,'read_bytes',lambda path:content(path).encode())
    monkeypatch.setattr(Path,'write_text',lambda path,text,**kwargs:put(path,text))
    monkeypatch.setattr(Path,'write_bytes',lambda path,value:put(path,value))
    monkeypatch.setattr(Path,'open',lambda path,mode='r',**kwargs:Handle(path,mode))
    monkeypatch.setattr(Path,'glob',lambda path,pattern:[Path(key) for key in files
        if Path(key).name=='library_state.json' and Path(key).parent.parent==path])
    monkeypatch.setattr(p,'read',read)
    for module in (cli,core,exam,multi,p):
        monkeypatch.setattr(module,'write_json',put)
        monkeypatch.setattr(module,'sha256_file',sha)
        monkeypatch.setattr(module,'file_lock',lambda *args,**kwargs:nullcontext())
    import omni_story.library.state as state
    monkeypatch.setattr(state,'write_json',put)
    monkeypatch.setattr(adjudicate,'_lock',lambda *args:nullcontext())
    monkeypatch.setattr(producer,'_own_lock',lambda *args:nullcontext())
    monkeypatch.setattr(producer,'digest',sha)
    for module in (producer,core,multi):monkeypatch.setattr(module,'verify_subject',lambda saved:None)
    monkeypatch.setattr(cli,'code_identity',lambda:'code')
    monkeypatch.setattr(cli.subprocess,'run',lambda *args,**kwargs:pytest.fail('Unexpected subprocess'))
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR','/memory/receipts')
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE','/memory/budget.json')
    return SimpleNamespace(files=files,put=put,read=read,sha=sha,content=content)


def save_probe(memory,directory,question,answer,*,text=None,stop='end_turn'):
    text=json.dumps(answer,ensure_ascii=False) if text is None else text
    response=dict(http_status=200,body_text=json.dumps(dict(content=[dict(type='text',text=text)],stop_reason=stop)))
    path=directory/'response.json';memory.put(path,response)
    memory.put(directory/'answer.json',dict(identity=dict(question=question),answer=answer,raw_text=text,
        stop_reason=stop,allow_invalid=True,receipt=dict(path=str(path),sha256=memory.sha(path),response_sha256=json_sha(response))))
    return answer


def glm_channel(monkeypatch,memory,replies,*,unknown=False,empty_error=False):
    calls=[];reservations=[]
    def reserve(**kwargs):
        reservations.append(kwargs)
        return dict(sequence=len(reservations),provider=kwargs['provider'])
    monkeypatch.setattr(budget,'reserve_request',reserve)
    def ask(model,prompt,frames,**limits):
        from omni_story.library.resource_guard import before_request
        before_request(provider='glm_coding_plan')
        calls.append(prompt)
        text,stop=replies[min(len(calls)-1,len(replies)-1)]
        directory=Path('/memory/receipts')/f'call{len(calls)}'
        response=dict(http_status=200,body_text=json.dumps(dict(content=[dict(type='text',text=text)],stop_reason=stop)))
        memory.put(directory/'calls/c0/response.json',response)
        memory.put(directory/'library_state.json',dict(input_lock=dict(request=dict(payload=dict(
            max_tokens=limits['max_tokens'],thinking=dict(budget_tokens=limits['thinking_budget']),
            messages=[dict(role='user',content=[dict(type='text',text=prompt)])]))),
            calls=[dict(id='c0',status='uncertain' if unknown else 'received',response_sha256=json_sha(response))]))
        if unknown:raise RuntimeError('request_outcome_unknown_no_replay')
        if empty_error and not text:raise ValueError('empty_body: GLM 返回空正文(stop_reason=max_tokens)')
        return text
    monkeypatch.setitem(sys.modules,'hunter',SimpleNamespace(glm=SimpleNamespace(ask=ask)))
    return calls,reservations


@pytest.mark.parametrize('first',['','{"paragraph_ids": ["p000"]','{"other": true}'])
def test_received_invalid_story_retries_once_and_preserves_original(memory,monkeypatch,first):
    calls,reservations=glm_channel(monkeypatch,memory,[(first,'end_turn'),('{"paragraph_ids":["p000"]}','end_turn')],empty_error=True)
    root=Path('/memory/story');question=dict(response_contract=dict(paragraph_ids=[]))
    assert exam.ask_probe(question,root)==dict(paragraph_ids=['p000'])
    original=copy.deepcopy(memory.files);assert len(calls)==2
    assert calls[1].endswith(exam.PROBE_RETRY_INSTRUCTION)
    assert len(reservations)==2 and all(row['provider']=='glm_coding_plan' for row in reservations)
    assert memory.read(root/'answer.json')['raw_text']==first
    assert memory.read(root/'retry1/answer.json')['identity']['retry_of']==memory.sha(root/'answer.json')
    assert exam.read_probe(root)['answer']==dict(paragraph_ids=['p000'])
    assert exam.ask_probe(question,root)==dict(paragraph_ids=['p000']) and len(calls)==2
    assert memory.files==original


def test_end_turn_empty_stays_truthful_and_never_retries_twice(memory,monkeypatch):
    calls,_=glm_channel(monkeypatch,memory,[('','end_turn')],empty_error=True)
    for _ in range(2):
        with pytest.raises(ValueError,match=r'正文为空\(stop_reason=end_turn\)') as error:
            exam.ask_probe({},Path('/memory/story'))
        assert '截断' not in cli.failure(error.value)['error']
    assert len(calls)==2 and '/memory/story/retry1/retry1/answer.json' not in memory.files


@pytest.mark.parametrize('text',['{"paragraph_ids": ["p000"]','{"other": true}'])
def test_invalid_json_or_missing_key_is_bounded_across_invocations(memory,monkeypatch,text):
    calls,_=glm_channel(monkeypatch,memory,[(text,'end_turn')])
    question=dict(response_contract=dict(paragraph_ids=[]))
    for _ in range(2):
        with pytest.raises(ValueError):exam.ask_probe(question,Path('/memory/story'))
    assert len(calls)==2


def test_max_tokens_empty_is_truncated_without_retry(memory,monkeypatch):
    calls,reservations=glm_channel(monkeypatch,memory,[('','max_tokens')],empty_error=True)
    for _ in range(2):
        with pytest.raises(ValueError,match=r'正文为空\(stop_reason=max_tokens\).*截断'):
            exam.ask_probe({},Path('/memory/story'))
    assert len(calls)==1 and len(reservations)==1
    assert memory.read('/memory/story/answer.json')['stop_reason']=='max_tokens'


def test_unknown_probe_never_enters_retry(memory,monkeypatch):
    calls,reservations=glm_channel(monkeypatch,memory,[('','end_turn')],unknown=True)
    with pytest.raises(RuntimeError,match='unknown_no_replay'):exam.ask_probe({},Path('/memory/story'))
    with pytest.raises(RuntimeError,match='not_received_no_replay'):exam.ask_probe({},Path('/memory/story'))
    assert len(calls)==1 and len(reservations)==1 and '/memory/story/retry1/attempt.json' not in memory.files


def test_unknown_retry_is_durably_blocked(memory,monkeypatch):
    calls,_=glm_channel(monkeypatch,memory,[('','end_turn')])
    root=Path('/memory/story')
    memory.put(root/'retry1/attempt.json',dict(started=True))
    for _ in range(2):
        with pytest.raises(RuntimeError,match='not_received_no_replay'):exam.ask_probe({},root)
    assert len(calls)==1


def judgment(*,invented=False,extra_quote=False):
    item=dict(gold_id='g000',gold_validity='valid',relation='matched',candidate_ids=['u000'],reason='明确表达',
        evidence=['编造的候选句'] if invented else ['u000'])
    if extra_quote:item['quote']='编造的候选句'
    return dict(items=[item],limitations=[])


@pytest.mark.parametrize('extra_quote',[False,True])
@pytest.mark.parametrize('still_invented',[False,True])
def test_invented_quotes_get_one_bounded_repair(memory,monkeypatch,extra_quote,still_invented):
    calls=[]
    def judge(kind,data,directory):
        calls.append((kind,copy.deepcopy(data)))
        broken=len(calls)==1 or still_invented
        raw=judgment(invented=broken and not extra_quote,extra_quote=broken and extra_quote)
        return raw,dict(raw_text=json.dumps(raw,ensure_ascii=False),stop_reason='end_turn')
    monkeypatch.setattr(exam,'judge_text',judge)
    data=dict(candidate_units=[dict(id='u000',text='选择行动并承担代价')],gold_rubric=[dict(id='g000',text='主动行动')])
    def run():return exam.checked_review([],{},'prompt',data,Path('/memory/judge'),Path('/memory/out'),count=1,judge_backend='glm_text')
    if still_invented:
        with pytest.raises(ValueError,match='invented_candidate_quote'):run()
    else:assert run()[0]==judgment()
    assert len(calls)==2 and calls[1][0]=='repair'
    assert '给定u编号候选句' in calls[1][1]['repair_prompt'] and '不改判断方向' in calls[1][1]['repair_prompt']
    record=memory.read('/memory/out/repair.json')['judge']
    assert record['calls']==1 and bool(record['defects_after'])==still_invented


@pytest.mark.parametrize('prediction',[
    dict(core_value_speculation=dict(basis='已见事实',ending=['合理推测','保留不确定性'])),
    ['已见事实',dict(ending_speculation='合理推测')],
    ['已见事实',' ',dict(ending_speculation='合理推测')],
])
def test_nested_prefix_prediction_keeps_order_and_raw(memory,monkeypatch,prediction):
    folder=Path('/memory/case');question={};raw=dict(prediction=prediction,uncertainties=[])
    memory.put(folder/'template/template-record.json',dict(sources=[{},{}]))
    monkeypatch.setattr(multi,'summarize',lambda *args,**kwargs:dict(template=dict(theme='主题'),template_sha256='sha'))
    monkeypatch.setattr(multi,'_prefix_question',lambda *args:question)
    monkeypatch.setattr(multi,'_probe',lambda q,d:(save_probe(memory,d,q,raw),{}))
    result,row=multi.prefix_reading({}, {},folder,dict(condition='forecast@support_plus_prefix',cut_s=2),{})
    assert result['prediction']=='\n'.join(exam.strings(prediction)) and result['prediction_raw']==prediction
    assert memory.read(row['answer_path'])['answer']==raw
    memory.put(folder/'support-template/template-record.json',dict(template=dict(theme='主题'),template_sha256='sha',sources=[dict(asset_sha256='s1')]))
    memory.put(folder/'support-template/template.json',dict(theme='主题'))
    monkeypatch.setattr(multi,'verify_template',lambda *args:None)
    monkeypatch.setattr(multi,'support_assets',lambda *args:[{},dict(sha256='s1')])
    assert multi.verify_prefix_answer(dict(row,condition='forecast@support_plus_prefix',prefix_cut_s=2),{}, {},folder,{})==result


@pytest.mark.parametrize('prediction',[{},[],dict(value=[' ',None,0]),' ',None])
def test_empty_prefix_predictions_are_rejected(prediction):
    with pytest.raises(ValueError,match='multi_reference_prefix_answer_invalid'):
        multi.normalize_prefix_answer(dict(prediction=prediction))


def failed_ledger(memory,status='failed_known'):
    path=Path('/memory/original/library_state.json')
    memory.put(path,dict(input_lock=dict(reference_sha256='media'),calls=[dict(id='c0',status=status)]))
    return path


def decide(path,call_id='c0'):
    return adjudicate.adjudicate(path,call_id,decision='abandoned_by_user',decided_by='user',user_note='明确放弃')


def test_failed_known_adjudication_keeps_ledger_bytes(memory):
    path=failed_ledger(memory);before=memory.content(path)
    row=decide(path)
    assert row['original_status']=='failed_known' and row['decision']=='abandoned_by_user'
    assert row['original_record_sha256']==json_sha(memory.read(path)['calls'][0])
    assert memory.content(path)==before


def test_failed_known_requires_all_decisions_and_retries_only_once(memory,monkeypatch):
    path=failed_ledger(memory);state=memory.read(path)
    state['calls'].append(dict(id='c1',status='failed_known'));memory.put(path,state)
    run=dict(output='/memory/new',registry_path='/memory/registry.json',reference_sha256='media')
    assert producer._guard_original_calls(run,[path.parent]) is None
    decide(path)
    assert producer._guard_original_calls(run,[path.parent]) is None
    decide(path,'c1');retry=producer._guard_original_calls(run,[path.parent])
    assert retry==(path,state['calls'][0])
    before=memory.content(path);calls=[]
    def capture(job,**kwargs):
        calls.append(copy.deepcopy(job));saved=dict(understanding=dict(theme='主旨'))
        memory.put(Path(kwargs['production_output'])/'subject.json',saved)
        return saved
    monkeypatch.setattr(producer,'_capture_reference',capture)
    monkeypatch.setattr(producer,'_existing_subject',lambda job,paths,diagnosis:(memory.read(Path(paths[0])/'subject.json'),str(paths[0]))
        if (Path(paths[0])/'subject.json').exists() else (None,None))
    first=producer._adjudicated_retry(run,*retry,None,[],{})
    assert producer._adjudicated_retry(run,*retry,None,[],{})==first and len(calls)==1
    assert first['attempt_id']=='c0__retry1' and calls[0]['output']=='/memory/new/production_stage_c0__retry1'
    assert memory.content(path)==before


def test_unadjudicated_failed_known_still_uses_original_guard(memory,monkeypatch):
    from omni_story.library.state import LibraryStopped
    path=failed_ledger(memory);calls=[]
    def capture(*args,**kwargs):
        calls.append(1)
        raise LibraryStopped('same_inputs_already_registered_use_original_output:'+str(path.parent))
    monkeypatch.setattr(producer,'_capture_reference',capture)
    with pytest.raises(LibraryStopped,match='already_registered'):
        producer.capture_reference(dict(output='/memory/new',reference_sha256='media'))
    assert calls==[1] and '/memory/registry/adjudicated_retries' not in memory.files


def test_failed_known_retry_with_unknown_outcome_is_not_repeated(memory,monkeypatch):
    path=failed_ledger(memory);decide(path);calls=[]
    run=dict(output='/memory/new',registry_path='/memory/registry.json',reference_sha256='media')
    retry=producer._guard_original_calls(run,[path.parent])
    monkeypatch.setattr(producer,'_existing_subject',lambda *args:(None,None))
    def unknown(*args,**kwargs):calls.append(1);raise RuntimeError('request_outcome_unknown_no_replay')
    monkeypatch.setattr(producer,'_capture_reference',unknown)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='no_replay'):producer._adjudicated_retry(run,*retry,None,[],{})
    assert calls==[1]


def manifest():
    asset=lambda role:dict(asset_id=role,sha256=role,path='/memory/'+role,duration_s=20)
    case=dict(id='t_0',name='01_模板',reference=asset('r'),query=asset('q'),labels=dict(
        elements=['主动行动'],core_evidence=[dict(element_index=0,start_packet_s=8)],prefix_information='implied',
        story_paragraphs=['居民主动行动','居民留在原地'],story_target_indices=[0],same_mechanism=True,
        provenance='human_confirmed',bgm_question=None))
    return dict(protocol_version='v4',judge_backend='glm_text',prefix_policy='before_last_element',story_seed=7,
        scoring_rule='只评明确表达的主旨',protocol_sha256='old',experiment_id='old',cases=[case],
        production=dict(configuration={},production_files={}),receipts='/memory/receipts',bgm_measurements=[])


def old_rows(memory,value,*,failed=None):
    case=value['cases'][0];folder=Path('/memory/old/cases/t_0');rows=[]
    for name,point in p.task_points(value,case):
        row=dict(case_id=case['id'],task=name,template=case['name'],human_confirmed=True,label_source='human_confirmed',
            protocol_sha256=value['protocol_sha256'],status='not_applicable_at_design',judge_backend='glm_text')
        if point:row.update(condition=point['condition'],prefix_cut_s=point['cut_s'])
        if name=='elements':
            reading=dict(theme='主动行动');subject_path=folder/'subject/subject.json'
            memory.put(subject_path,dict(understanding=reading,understanding_sha256=json_sha(reading),binding=dict(reference_sha256='r')))
            directory=folder/'judge';raw=judgment();save_probe(memory,directory,{},raw)
            compiled=exam.compile_element_ids(raw,reading,1)
            row.update(status='scored_provisional',subject_path=str(subject_path),subject_sha256=json_sha(reading),
                judgment=compiled,judgment_compiler='candidate_unit_ids_v1',
                receipt=dict(judge_backend='glm_text',path=str(directory/'answer.json'),sha256=memory.sha(directory/'answer.json')),
                metrics=exam.validate_elements(compiled,case,reading,judge_backend='glm_text'))
        if name==failed:row.update(status='failed',error='format_invalid')
        rows.append(row)
    memory.put('/memory/old/manifest.json',value);memory.put('/memory/old/report.json',dict(rows=rows))
    return rows


def test_rerun_reuses_success_and_not_applicable_without_requests(memory,monkeypatch):
    value=manifest();rows=old_rows(memory,value);current=dict(value,protocol_sha256='new',rerun_failed_from=['/memory/old/report.json'])
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:current)
    monkeypatch.setattr(cli,'subject',lambda *args,**kwargs:pytest.fail('Unexpected subject'))
    monkeypatch.setattr(exam,'ask_probe',lambda *args,**kwargs:pytest.fail('Unexpected request'))
    root=Path('/memory/new');cli.task(SimpleNamespace(manifest='memory',case='t_0',output=root))
    core.music_tasks(current,root)
    for original in rows:
        point=next(point for name,point in p.task_points(current,current['cases'][0])
            if name==original['task'] and (point.get('condition') if point else None)==original.get('condition'))
        row=memory.read(p.row_path(root/'cases/t_0',original['task'],point))
        assert row==dict(original,reused_from_report='/memory/old/report.json')
        cli._verify_reused_row(row,current['cases'][0],current)
    assert memory.read('/memory/old/report.json')==dict(rows=rows)
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE')
    current['human_review_cases']=['t_0']
    value['human_review_cases']=['t_0'];memory.put('/memory/old/manifest.json',value)
    cli.report(SimpleNamespace(manifest='memory',output=root,human_reviews=None))
    assert memory.read(root/'report.json')['rows']==[dict(row,reused_from_report='/memory/old/report.json') for row in rows]


def test_pending_music_can_read_reused_original_gallery(memory):
    value=manifest();old_rows(memory,value)
    current=dict(value,rerun_failed_from=['/memory/old/report.json']);root=Path('/memory/new')
    cli.reuse_failed_rows(current,root)
    source=Path('/memory/old/cases/t_0/subject/subject.json');saved=memory.read(source)
    saved['manual_correction']=False;memory.put(source,saved)
    memory.put(source.parent.parent/'subject-status.json',dict(status='collected',original_output=str(source.parent)))
    entry=core._full_subject(current['cases'][0],current,root/'cases/t_0')
    assert entry['subject_path']==str(source) and entry['understanding']==dict(theme='主动行动')


def test_subject_reuse_metadata_does_not_mutate_copied_rows(memory):
    path=Path('/memory/new/cases/t_0/subject')
    memory.put(path/'reuse.json',dict(resumed_in='/memory/original',reuse_diagnosis=['old']))
    row=dict(case_id='t_0',task='elements',reused_from_report='/memory/old/report.json')
    result=dict(subjects=[dict(case_id='t_0',original_output=str(path))],rows=[copy.deepcopy(row)])
    core._report_subject_reuse(result)
    assert result['rows']==[row]


def test_rerun_failed_rows_execute_normally(memory,monkeypatch):
    value=manifest();old_rows(memory,value,failed='story')
    current=dict(value,protocol_sha256='new',rerun_failed_from=['/memory/old/report.json']);calls=[]
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:current)
    def subject(case,manifest,folder,**kwargs):
        calls.append('subject')
        return dict(manual_correction=False,understanding=dict(theme='主动行动'),understanding_sha256='reading',evidence_path='/memory/reading')
    def probe(question,directory,**kwargs):
        calls.append('story')
        return dict(paragraph_ids=sorted(p.story_gold(current,'t_0')))
    monkeypatch.setattr(cli,'subject',subject);monkeypatch.setattr(exam,'ask_probe',probe)
    root=Path('/memory/new');cli.task(SimpleNamespace(manifest='memory',case='t_0',output=root))
    assert calls==['subject','story']
    row=memory.read(root/'cases/t_0/story.json')
    assert row['status']=='scored_provisional' and 'reused_from_report' not in row
    assert memory.read(root/'cases/t_0/elements.json')['reused_from_report']=='/memory/old/report.json'


@pytest.mark.parametrize('change',['sha','case','condition','support_k'])
def test_rerun_rejects_changed_evidence_or_identity(memory,change):
    value=manifest();rows=old_rows(memory,value);current=copy.deepcopy(value)
    current.update(protocol_sha256='new',rerun_failed_from=['/memory/old/report.json'])
    if change=='sha':memory.put('/memory/old/cases/t_0/judge/response.json',dict(tampered=True))
    elif change=='case':current['cases'][0]['labels']['elements']=['另一个主旨']
    elif change=='condition':
        rows[0]['condition']='elements@other';memory.put('/memory/old/report.json',dict(rows=rows))
    else:current['support_k']=1;current['cases'][0]['support_assets']=[current['cases'][0]['reference']]
    cli.reuse_failed_rows(current,Path('/memory/new'))
    assert '/memory/new/cases/t_0/elements.json' not in memory.files


def test_rerun_receipts_are_checked_again_after_copy(memory):
    value=manifest();old_rows(memory,value)
    current=dict(value,rerun_failed_from=['/memory/old/report.json']);root=Path('/memory/new')
    cli.reuse_failed_rows(current,root);row=memory.read(root/'cases/t_0/elements.json')
    memory.put('/memory/old/cases/t_0/judge/response.json',dict(tampered=True))
    with pytest.raises(ValueError,match='probe_receipt_changed'):
        cli.verify_scored_row(row,current['cases'][0],current,root/'cases/t_0')


def test_rerun_does_not_reuse_a_changed_forecast_cut(memory):
    value=manifest();rows=old_rows(memory,value)
    next(row for row in rows if row['task']=='forecast')['prefix_cut_s']=19
    memory.put('/memory/old/report.json',dict(rows=rows))
    current=dict(value,rerun_failed_from=['/memory/old/report.json']);root=Path('/memory/new')
    cli.reuse_failed_rows(current,root)
    assert (root/'cases/t_0/elements.json').exists()
    assert not (root/'cases/t_0/forecast@before_last_element.json').exists()
