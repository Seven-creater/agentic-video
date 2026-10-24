import copy
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import reference_cli as cli, reference_core as core
from omni_story.evaluation import reference_exam as exam, reference_protocol as p
from omni_story.library.state import json_sha

SUBJECT=cli.subject


def manifest():
    asset=lambda role:dict(path=role,sha256=role,asset_id=role,duration_s=20)
    case=dict(id='t_0',name='01_模板',reference=asset('r'),query=asset('q'),labels=dict(
        elements=['主动行动','承担代价'],provenance='human_confirmed',same_mechanism=True,
        core_evidence=[dict(element_index=0,start_packet_s=2),dict(element_index=1,start_packet_s=8)],
        story_paragraphs=['居民主动迈步','居民选择等待'],story_target_indices=[0],bgm_question=None,
        prefix_information='implied'))
    return dict(protocol_version='v4',judge_backend='glm_text',prefix_policy='before_last_element',
        scoring_rule='只评明确表达的主旨',protocol_sha256='current',experiment_id='test',cases=[case],
        production=dict(configuration={},production_files={}),receipts='/memory/receipts',
        human_review_cases=['t_0'],story_seed=3,bgm_measurements=[])


def judgment(count=1,*,inference=False):
    raw=dict(items=[dict(gold_id=f'g{i:03d}',gold_validity='valid',relation='matched',
        candidate_ids=['u000'],reason='候选明确表达',evidence=['u000']) for i in range(count)],limitations=[])
    if inference:raw['inference_validity']='plausible'
    return raw


@pytest.fixture
def memory(monkeypatch):
    files={}
    def put(path,value):files[str(path)]=copy.deepcopy(value)
    def read(path):return copy.deepcopy(files[str(path)])
    def sha(path):return json_sha(files[str(path)])
    monkeypatch.setattr(Path,'exists',lambda path:str(path) in files)
    monkeypatch.setattr(Path,'mkdir',lambda *args,**kwargs:None)
    monkeypatch.setattr(Path,'write_text',lambda path,text,**kwargs:put(path,text))
    monkeypatch.setattr(Path,'glob',lambda path,pattern:[Path(key) for key in files
        if Path(key).name=='library_state.json' and Path(key).parent.parent==path])
    monkeypatch.setattr(p,'read',read)
    for module in (p,cli,core,exam):
        monkeypatch.setattr(module,'write_json',put)
        monkeypatch.setattr(module,'sha256_file',sha)
        monkeypatch.setattr(module,'file_lock',lambda *args,**kwargs:nullcontext())
    monkeypatch.setattr(cli,'code_identity',lambda:'code')
    monkeypatch.setattr(cli.subprocess,'run',lambda *args,**kwargs:pytest.fail('Unexpected subprocess'))
    monkeypatch.setattr(exam,'review_audio_video',lambda *args,**kwargs:pytest.fail('Unexpected video request'))
    monkeypatch.setattr(exam,'paid_review',lambda *args,**kwargs:pytest.fail('Unexpected Qwen review'))
    monkeypatch.setattr(cli,'subject',lambda *args,**kwargs:pytest.fail('Unexpected subject request'))
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR','/memory/registry')
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE','/memory/budget')
    return files,put,read,sha


def save_probe(memory,directory,raw,*,raw_text=None,stop_reason='end_turn',identity=None):
    files,put,read,sha=memory
    text=json.dumps(raw,ensure_ascii=False) if raw_text is None else raw_text
    response=dict(http_status=200,body_text=json.dumps(dict(content=[dict(type='text',text=text)],stop_reason=stop_reason)))
    path=directory/'response.json';put(path,response)
    saved=dict(identity=identity or {},answer=raw,allow_invalid=True,raw_text=text,stop_reason=stop_reason,
        receipt=dict(path=str(path),sha256=sha(path),response_sha256=json_sha(response)))
    put(directory/'answer.json',saved)
    return raw


def old_report(memory,value,*,source='/memory/old/report.json',status='scored_provisional'):
    files,put,read,sha=memory
    row=dict(case_id='t_0',task='elements',status=status,protocol_sha256='old',
        metrics=dict(full_match_rate=1,counts=dict(matched=2,partial=0,missing=0,contradicted=0)))
    put(source,dict(rows=[row]));put(Path(source).with_name('manifest.json'),value)
    return row


def test_reuse_requires_identical_elements_and_preserves_original(memory):
    files,put,read,sha=memory
    value=manifest();value['reuse_rows_from']=['/memory/old/report.json']
    original=old_report(memory,value)
    row=cli.reuse_elements(value['cases'][0],value)
    assert row==dict(original,reused_from=value['reuse_rows_from'][0],
        reused_label_sha256=json_sha(value['cases'][0]['labels']['elements']),judge_backend='qwen_video')
    assert read(value['reuse_rows_from'][0])['rows']==[original]
    value['cases'][0]['labels']['elements'][0]='改写了主旨'
    assert cli.reuse_elements(value['cases'][0],value) is None


@pytest.mark.parametrize('change',[
    dict(status='failed'),dict(case_id='t_other'),dict(task='story')])
def test_reuse_skips_ineligible_rows_and_tries_next_report(memory,change):
    files,put,read,sha=memory
    value=manifest();value['reuse_rows_from']=['/memory/first/report.json','/memory/next/report.json']
    first=old_report(memory,value,source=value['reuse_rows_from'][0]);first.update(change)
    put(value['reuse_rows_from'][0],dict(rows=[first]))
    old_report(memory,value,source=value['reuse_rows_from'][1])
    assert cli.reuse_elements(value['cases'][0],value)['reused_from']==value['reuse_rows_from'][1]


def test_reused_elements_need_no_subject_or_probe_and_are_verified(memory,monkeypatch):
    files,put,read,sha=memory
    value=manifest();value['reuse_rows_from']=['/memory/old/report.json'];old_report(memory,value)
    root=Path('/memory/out');case=value['cases'][0]
    for name,point in p.task_points(value,case):
        if name!='elements':put(p.row_path(root/'cases/t_0',name,point),dict(status='already_saved'))
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    monkeypatch.setattr(exam,'ask_probe',lambda *args,**kwargs:pytest.fail('Unexpected probe'))
    cli.task(SimpleNamespace(manifest='memory',case='t_0',output=root))
    row=read(root/'cases/t_0/elements.json')
    assert row['status']=='scored_provisional' and row['reused_from']=='/memory/old/report.json'
    cli.verify_scored_row(row,case,value,root/'cases/t_0')
    row['metrics']['full_match_rate']=0
    with pytest.raises(ValueError,match='reused_row_changed'):cli.verify_scored_row(row,case,value,root/'cases/t_0')
    row=read(root/'cases/t_0/elements.json');case['labels']['elements'][0]='改写'
    with pytest.raises(ValueError,match='reused_row_changed'):cli.verify_scored_row(row,case,value,root/'cases/t_0')


def test_text_judge_passes_only_text_and_saves_glm_receipt(memory,monkeypatch):
    calls=[]
    def probe(question,directory,**kwargs):
        calls.append((question,kwargs))
        return save_probe(memory,directory,judgment())
    monkeypatch.setattr(exam,'ask_probe',probe)
    data=dict(candidate_units=[dict(id='u000',text='主动行动',path='/secret/video.mp4')],
        gold_rubric=[dict(id='g000',text='主动行动')],scoring_rule='比较主旨',
        video='/secret/video.mp4',frames=['frame.png'],mapping=[dict(asset_id='r')])
    raw,receipt=exam.judge_text('elements',data,Path('/memory/judge'))
    question,kwargs=calls[0]
    assert kwargs==dict(allow_invalid=True)
    assert question['candidate_units']==[dict(id='u000',text='主动行动')]
    assert question['gold_rubric']==data['gold_rubric'] and question['scoring_rule']=='比较主旨'
    assert not {'video','frames','mapping'}&question.keys() and '/secret' not in json.dumps(question)
    assert receipt['judge_backend']=='glm_text' and exam.received_judgment(receipt)==raw


def test_text_evidence_is_bound_to_real_candidate_sentences():
    case=manifest()['cases'][0];case['labels']['elements']=['主动行动']
    reading=dict(theme='主动行动')
    raw=exam.compile_element_ids(judgment(),reading,1)
    assert exam.validate_elements(raw,case,reading,judge_backend='glm_text')['full_match_rate']==1
    with pytest.raises((ValueError,TypeError)):exam.validate_elements(raw,case,reading)
    for key,value in [('evidence',['u999']),('evidence',['g000']),('evidence',[]),
            ('candidate_ids',['u999']),('candidate_ids',[]),('quote','主动'),('quotes',['编造的候选句'])]:
        broken=copy.deepcopy(raw);broken['elements'][0][key]=value
        with pytest.raises(ValueError,match='invented_candidate_quote'):
            exam.validate_elements(broken,case,reading,judge_backend='glm_text')
    broken=judgment();broken['items'][0]['candidate_ids']=['u999']
    with pytest.raises(ValueError,match='candidate_unit_identity_invalid'):exam.compile_element_ids(broken,reading,1)


@pytest.mark.parametrize('broken', ['bad json', 'missing_evidence', 'missing_inference'])
def test_text_repair_uses_same_probe_channel_once(memory,monkeypatch,broken):
    files,put,read,sha=memory;calls=[]
    def probe(question,directory,**kwargs):
        calls.append(question)
        raw=judgment(inference=True)
        if len(calls)==1:
            if broken=='bad json':return save_probe(memory,directory,None,raw_text='not json')
            if broken=='missing_evidence':raw['items'][0].pop('evidence')
            else:raw.pop('inference_validity')
        return save_probe(memory,directory,raw)
    monkeypatch.setattr(exam,'ask_probe',probe)
    data=dict(candidate_units=[dict(id='u000',text='候选原句')],gold_rubric=[dict(id='g000',text='要素')],scoring_rule='规则')
    raw,receipt=exam.checked_review([],{},'unused',data,Path('/memory/judge'),Path('/memory/out'),
        count=1,require_inference=True,judge_backend='glm_text')
    assert raw==judgment(inference=True) and [c['prompt_kind'] for c in calls]==['forecast','repair']
    assert '不改变任何已有判断' in calls[1]['instruction']
    assert exam.received_judgment(receipt)==raw
    record=read('/memory/out/repair.json')['judge']
    assert record['calls']==1 and record['defects_after']==[]
    original=read(receipt['path']);original['raw_text']='tampered';put(receipt['path'],original)
    with pytest.raises(ValueError,match='text_receipt_changed'):exam.received_judgment(receipt)


def test_unknown_text_calls_never_enter_repair(memory,monkeypatch):
    files,put,read,sha=memory;calls=[]
    def unknown(*args,**kwargs):calls.append(1);raise RuntimeError('uncertain_no_replay')
    monkeypatch.setattr(exam,'judge_text',unknown)
    with pytest.raises(RuntimeError,match='uncertain_no_replay'):
        exam.checked_review([],{},'unused',{},Path('/memory/judge'),Path('/memory/out'),count=1,judge_backend='glm_text')
    assert calls==[1] and '/memory/out/repair.json' not in files


@pytest.mark.parametrize('truncated',[True,False])
def test_text_repairs_are_bounded_and_empty_truncations_are_not_repaired(memory,monkeypatch,truncated):
    files,put,read,sha=memory;calls=[]
    def probe(question,directory,**kwargs):
        calls.append(question)
        return save_probe(memory,directory,None,raw_text='' if truncated else '{}',
            stop_reason='max_tokens' if truncated else 'end_turn')
    monkeypatch.setattr(exam,'ask_probe',probe)
    data=dict(candidate_units=[],gold_rubric=[dict(id='g000',text='要素')],scoring_rule='规则')
    with pytest.raises(ValueError,match='empty_body_truncated' if truncated else 'items_missing'):
        exam.checked_review([],{},'unused',data,Path('/memory/judge'),Path('/memory/out'),count=1,judge_backend='glm_text')
    assert len(calls)==(1 if truncated else 2)


def test_glm_probe_caches_received_invalid_json_without_replaying(memory,monkeypatch):
    files,put,read,sha=memory;calls=[]
    def ask(model,prompt,frames,**kwargs):
        calls.append((model,frames,kwargs))
        directory=Path('/memory/registry/request/calls/c0')
        response=dict(http_status=200,body_text=json.dumps(dict(content=[dict(type='text',text=' invalid\n')],stop_reason='end_turn')))
        put(directory/'response.json',response)
        put('/memory/registry/request/library_state.json',dict(input_lock=dict(request=dict(payload=dict(max_tokens=24000,thinking=dict(type='enabled',budget_tokens=8000),
            messages=[dict(role='user',content=[dict(type='text',text=prompt)])]))),
            calls=[dict(id='c0',status='received',response_sha256=json_sha(response))]))
        return 'invalid'
    monkeypatch.setitem(sys.modules,'hunter',SimpleNamespace(glm=SimpleNamespace(ask=ask)))
    for _ in range(2):
        assert exam.ask_probe(dict(question='文字'),Path('/memory/probe'),allow_invalid=True) is None
    assert calls==[('glm-5.3-flash',[],dict(max_tokens=24000,thinking_budget=8000))]
    assert read('/memory/probe/answer.json')['raw_text']==' invalid\n'


@pytest.mark.parametrize('text,condition,reuse_only',[(True,'full',True),(True,'prefix@7.5',False),(False,'full',False)])
def test_full_understanding_reuses_existing_outputs_and_cache(memory,monkeypatch,text,condition,reuse_only):
    files,put,read,sha=memory;value=manifest();case=value['cases'][0]
    value['production'].update(existing_reference_outputs=['/memory/old/reference.json'],reuse_only=True)
    if not text:value.pop('judge_backend')
    root=Path('/memory/subject');jobs=[]
    def run(command,**kwargs):
        job=read(command[-2]);jobs.append(job)
        put(Path(job['output'])/'subject.json',dict(understanding=dict(theme='已缓存')))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.subprocess,'run',run)
    SUBJECT(case,value,root,condition=condition,cache_root=Path('/memory/cache'))
    assert jobs[0]['existing_reference_outputs']==['/memory/old/reference.json']
    assert jobs[0]['subject_cache_root']=='/memory/cache' and jobs[0]['condition']==condition
    assert bool(jobs[0].get('reuse_only'))==reuse_only


def pipeline(memory,monkeypatch,value):
    from omni_story.evaluation import production_subject
    files,put,read,sha=memory;seen=dict(subjects=[],questions=[])
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    monkeypatch.setattr(core,'verify_subject',lambda saved:None)
    monkeypatch.setattr(production_subject,'verify_subject',lambda saved:None)
    def subject(case,protocol,folder,**kwargs):
        seen['subjects'].append(kwargs)
        saved=dict(understanding=dict(theme='主动行动并承担代价'),understanding_sha256='reading',manual_correction=False,
            binding=dict(reference_sha256=case['reference']['sha256'],configuration=protocol['production']['configuration']))
        put(folder/'subject/subject.json',saved)
        return dict(saved,evidence_path=str(folder/'subject/subject.json'))
    def prefix(asset,cut,folder):
        put(folder/'prefix.mp4',dict(cut=cut));put(folder/'prefix.json',dict(binding=dict(source_sha256=asset['sha256'],end_s=cut)))
        return folder/'prefix.mp4'
    def probe(question,directory,**kwargs):
        assert not kwargs.get('frames')
        seen['questions'].append(question)
        raw=judgment(len(question['gold_rubric']),inference=question['prompt_kind']=='forecast') if 'prompt_kind' in question else dict(
            paragraph_ids=sorted(p.story_gold(value,'t_0')))
        return save_probe(memory,directory,raw)
    monkeypatch.setattr(cli,'subject',subject);monkeypatch.setattr(p,'prefix',prefix);monkeypatch.setattr(exam,'ask_probe',probe)
    return seen


def test_elements_prefix_story_and_report_use_text_backend(memory,monkeypatch):
    files,put,read,sha=memory;value=manifest();seen=pipeline(memory,monkeypatch,value)
    value['reuse_rows_from']=['/memory/old/report.json'];old_report(memory,value)
    value['cases'][0]['labels']['elements'][0]='改写主旨'
    root=Path('/memory/out');cli.task(SimpleNamespace(manifest='memory',case='t_0',output=root))
    for name in ('elements','forecast@before_last_element','story'):
        row=read(root/'cases/t_0'/f'{name}.json')
        assert row['status']=='scored_provisional' and row['judge_backend']=='glm_text' and 'reused_from' not in row
    prefix=next(q for q in seen['questions'] if q.get('prompt_kind')=='forecast')
    assert prefix['gold_rubric']==[dict(id='g000',text='承担代价')]
    assert prefix['scoring_rule']==value['scoring_rule'] and len(seen['subjects'])==2
    assert seen['subjects'][0]['cache_root']==root/'subject_cache'
    assert seen['subjects'][1]['condition']=='prefix@7.5'
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE')
    cli.report(SimpleNamespace(manifest='memory',output=root,human_reviews=None))
    result=read(root/'report.json')
    assert result['groups']['elements']['scored']==1 and result['groups']['forecast']['scored']==1
    assert all('judge_backend' in row for row in result['rows'])
    assert '评审与被测同属 GLM 家族，标签为人工确认' in result['note']
    assert '评审与被测同属 GLM 家族，标签为人工确认' in files[str(root/'report.md')]


def test_fingerprint_dispatch_and_report_verification(memory,monkeypatch):
    files,put,read,sha=memory;value=manifest();value.update(bgm_mode='fingerprint',bgm_screen_rows='/memory/screen.json')
    calls=[];expected=dict(status='scored_provisional',metrics=dict(music_template_hit=1,unique_template_hit=1,candidate_count=1))
    def evaluate(protocol,**kwargs):calls.append((protocol,kwargs));return dict(t_0=expected)
    monkeypatch.setitem(sys.modules,'omni_story.evaluation.bgm_fingerprint',SimpleNamespace(evaluate=evaluate))
    monkeypatch.setattr(core,'_full_subject',lambda *args:pytest.fail('Unexpected gallery'))
    root=Path('/memory/out');core.music_tasks(value,root);core.music_tasks(value,root)
    assert len(calls)==1 and calls[0]==(value,dict(screen_rows_path=value['bgm_screen_rows']))
    row=read(root/'cases/t_0/bgm.json')
    assert row['metrics']==expected['metrics'] and row['judge_backend']=='fingerprint'
    core.verify_row(row,value['cases'][0],value,root/'cases/t_0')
    row['metrics']['candidate_count']=2
    with pytest.raises(ValueError,match='fingerprint_result_changed'):core.verify_row(row,value['cases'][0],value,root/'cases/t_0')


def test_fingerprint_missing_module_has_clear_error(memory,monkeypatch):
    value=manifest();value.update(bgm_mode='fingerprint',bgm_screen_rows='/memory/screen.json')
    def missing(*args,**kwargs):raise ImportError('not merged')
    monkeypatch.setattr(p.importlib,'import_module',missing)
    with pytest.raises(RuntimeError,match='reference_eval_v4_bgm_fingerprint_unavailable'):
        core.music_tasks(value,Path('/memory/out'))


@pytest.mark.parametrize('version,backend',[(None,'glm_text'),('v3','glm_text'),('v4',None),('v4','qwen')])
def test_default_behavior_does_not_enable_reuse_or_text_judge(memory,version,backend):
    value=manifest();value.update(protocol_version=version,judge_backend=backend,reuse_rows_from=['unreadable'])
    assert not exam.text_judge(value) and cli.reuse_elements(value['cases'][0],value) is None


def test_text_judge_separates_different_questions_in_shared_root(tmp_path, monkeypatch):
    from omni_story.evaluation import reference_exam as exam
    seen = []
    def fake_probe(question, directory, **kw):
        seen.append(Path(directory))
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / 'answer.json').write_text(__import__('json').dumps(dict(raw_text='{}')))
        return {}
    monkeypatch.setattr(exam, 'ask_probe', fake_probe)
    base = dict(candidate_units=[dict(id='u000', text='a')], gold_rubric=[dict(id='g000', text='x')], scoring_rule='r')
    exam.judge_text('elements', base, tmp_path / 'elements_ids_v1')
    exam.judge_text('elements', dict(base, candidate_units=[dict(id='u000', text='b')]), tmp_path / 'elements_ids_v1')
    exam.judge_text('elements', base, tmp_path / 'elements_ids_v1')
    assert seen[0] != seen[1] and seen[0] == seen[2] and seen[0].parent == tmp_path / 'elements_ids_v1'


def test_probe_ignores_older_identical_messages_sent_with_other_limits(memory,monkeypatch):
    files,put,read,sha=memory
    def state(name,max_tokens,budget,prompt):
        response=dict(http_status=200,body_text=json.dumps(dict(content=[dict(type='text',text='{"ok":true}')],stop_reason='end_turn')))
        put(f'/memory/registry/{name}/calls/c0/response.json',response)
        put(f'/memory/registry/{name}/library_state.json',dict(input_lock=dict(request=dict(payload=dict(max_tokens=max_tokens,
            thinking=dict(type='enabled',budget_tokens=budget),messages=[dict(role='user',content=[dict(type='text',text=prompt)])]))),
            calls=[dict(id='c0',status='received',response_sha256=json_sha(response))]))
    def ask(model,prompt,frames,**kwargs):
        state('old',8192,1200,prompt);state('new',kwargs['max_tokens'],kwargs['thinking_budget'],prompt)
        return '{"ok":true}'
    monkeypatch.setitem(sys.modules,'hunter',SimpleNamespace(glm=SimpleNamespace(ask=ask)))
    assert exam.ask_probe(dict(q=1),Path('/memory/probe2'))==dict(ok=True)
    assert read('/memory/probe2/answer.json')['receipt']['path'].startswith('/memory/registry/new/')
