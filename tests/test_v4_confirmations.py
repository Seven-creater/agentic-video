import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import reference_cli as cli
from omni_story.evaluation import reference_core as core, reference_exam as exam, reference_protocol as p
from omni_story.library.state import json_sha


def labels(elements):
    return dict(elements=elements, provenance='model_assisted_unconfirmed', same_mechanism=True,
        core_evidence=[dict(element_index=1, start_packet_s=8)], prefix_information='implied',
        story_paragraphs=['居民终于迈出了第一步', '仍然没有行动'], story_target_indices=[0], bgm_question=None)


def manifest(count=2, *, confirmed=False):
    cases=[]
    for i in range(count):
        asset=lambda role:dict(path=f't_{i}{role}',sha256=json_sha(dict(media=f't_{i}{role}')),
            asset_id=f't_{i}{role}',duration_s=20)
        cases.append(dict(id=f't_{i}',name=f'{i:02d}_模板{i}',reference=asset('r'),query=asset('q'),
            labels=dict(labels(['具体情节A','抽象要素B'] if i else ['具体情节A','抽象要素B','具体情节C']),
                **(dict(element_label_source='human_confirmed' if i else 'model_assisted_unconfirmed',
                    cut_point_source='model_assisted_unconfirmed',same_video_source='human_confirmed' if i else 'model_assisted_unconfirmed')
                    if confirmed else {}))))
    value=dict(protocol_version='v4',prefix_policy='before_last_element',schema_version=p.SCHEMA,
        protocol_sha256='frozen',experiment_id='test',methods=p.METHODS,cases=cases,
        human_review_cases=[c['id'] for c in cases[:6]],story_seed=7,bgm_measurements=[],
        production=dict(configuration={},production_files={}),receipts='/memory/receipts')
    if confirmed:value.update(confirmations_sha256='confirmation-sha',scoring_rule='主旨要素按抽象层级判定')
    return value


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


def pipeline(monkeypatch,memory,value):
    from omni_story.evaluation import production_subject
    files,put,read,sha=memory
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    monkeypatch.setattr(core,'verify_subject',lambda saved:None)
    monkeypatch.setattr(production_subject,'verify_subject',lambda saved:None)
    def subject(case,protocol,folder,**kwargs):
        saved=dict(understanding=dict(theme='候选主旨'),understanding_sha256='reading',manual_correction=False,
            binding=dict(reference_sha256=case['reference']['sha256'],configuration=protocol['production']['configuration']))
        put(folder/'subject/subject.json',saved)
        return dict(saved,evidence_path=str(folder/'subject/subject.json'))
    def prefix(asset,cut,folder):
        put(folder/'prefix.mp4',dict(cut=cut,source=asset['sha256']))
        put(folder/'prefix.json',dict(binding=dict(source_sha256=asset['sha256'],end_s=cut)))
        return folder/'prefix.mp4'
    def review(assets,roles,prompt,data,directory,**kwargs):
        is_prefix=directory.name.startswith('forecast@')
        items=[dict(gold_id=gold['id'],gold_validity='valid',relation='matched',candidate_ids=['u000'],
                reason='比较实际主旨',evidence=[dict(start_s=8,end_s=9,observation='坚持选择')])
            for gold in data.get('gold_rubric',[dict(id='g000'),dict(id='g001')])]
        raw=dict(items=items,limitations=[])
        if is_prefix:raw['inference_validity']='plausible'
        state=directory/'receipt/state.json'
        put(state,dict(status='received'))
        put(state.with_name('response.json'),dict(http_status=200,
            body_text=json.dumps(dict(choices=[dict(message=dict(content=json.dumps(raw)))]))))
        return raw,dict(path=str(state),state_sha256=sha(state),response_sha256=sha(state.with_name('response.json')))
    def probe(question,folder,**kwargs):
        answer=dict(paragraph_ids=sorted(p.story_gold(value,folder.parent.name)))
        put(folder/'answer.json',dict(answer=answer))
        return answer
    monkeypatch.setattr(cli,'subject',subject)
    monkeypatch.setattr(p,'prefix',prefix)
    monkeypatch.setattr(exam,'paid_review',review)
    monkeypatch.setattr(exam,'ask_probe',probe)
    monkeypatch.setattr(exam,'verify_probe',lambda saved:None)


def inventory(value):
    return dict(templates=[dict(folder=c['name'],assets=[c['reference'],c['query']]) for c in value['cases']],
        bgm_measurements=[])


def prepare_args(output,confirmations=None):
    return SimpleNamespace(inventory='inventory',curation=None,output=output,
        receipts=Path('/memory/receipts'),runtime='runtime',experiment_id='test',protocol_version='v4',
        bgm_measurements=None,story_seed=7,asr_profile=None,confirmations=confirmations)


def drafted():
    return copy.deepcopy(dict(provenance='model_assisted_unconfirmed',same_mechanism=True,
        elements=['具体情节A','抽象要素B','具体情节C'],story_paragraphs=['居民终于迈出了第一步','仍然没有行动'],
        story_target_indices=[0],bgm_question=None))


def test_load_confirmations_requires_user_signature(tmp_path):
    path=tmp_path/'confirmations_v4.json'
    path.write_text(json.dumps(dict(confirmed_by='model',rule='r',cases={},abstract_rewrites={}),ensure_ascii=False))
    with pytest.raises(ValueError,match='reference_eval_v4_confirmations_not_user'):p.load_confirmations(path)
    path.write_text(json.dumps(dict(confirmed_by='user',rule='按抽象层级',cases={},abstract_rewrites={}),ensure_ascii=False))
    assert p.load_confirmations(path)['rule']=='按抽象层级'


def test_apply_confirmations_rewrites_elements_and_records_sources():
    case=dict(id='t_1',name='29_见面',labels=dict(provenance='model_assisted_unconfirmed',
        elements=['具体见面情节','维系关系']))
    p.apply_confirmations([case],dict(confirmed_by='user',rule='r',
        cases={'t_1':dict(name='29_见面',elements='confirmed',cut_point='是',same_video='pending')},
        abstract_rewrites={'29_见面':{'1':'日常小事升华为隐喻'}}))
    q=case['labels']
    assert q['elements']==['具体见面情节','日常小事升华为隐喻']
    assert q['original_elements']==['具体见面情节','维系关系']
    assert q['element_rewrites']=={1:'日常小事升华为隐喻'}
    assert q['element_label_source']=='human_confirmed'
    assert q['cut_point_source']=='human_confirmed' and q['same_video_source']=='model_assisted_unconfirmed'
    assert q['provenance']=='model_assisted_unconfirmed'


def test_apply_confirmations_marks_pending_and_unlisted_cases_model_assisted():
    cases=[dict(id='t_1',name='01_不烧心',labels=dict(provenance='model_assisted_unconfirmed',elements=['要素'])),
        dict(id='t_2',name='17_狂飙吃鱼',labels=dict(provenance='model_assisted_unconfirmed',elements=['要素','另一要素']))]
    p.apply_confirmations(cases,dict(confirmed_by='user',rule='r',
        cases={'t_2':dict(name='17_狂飙吃鱼',elements='pending',cut_point='pending',same_video='是')},
        abstract_rewrites={'01_不烧心':{'0':'抽象要素'}}))
    assert cases[0]['labels']['elements']==['抽象要素']
    assert cases[0]['labels']['element_label_source']==cases[0]['labels']['cut_point_source']=='model_assisted_unconfirmed'
    assert cases[1]['labels']['element_label_source']==cases[1]['labels']['cut_point_source']=='model_assisted_unconfirmed'
    assert cases[1]['labels']['same_video_source']=='human_confirmed'


def test_apply_confirmations_rejects_rewrite_index_outside_elements():
    case=dict(id='t_1',name='29_见面',labels=dict(provenance='model_assisted_unconfirmed',elements=['唯一要素']))
    with pytest.raises(ValueError,match='reference_eval_v4_rewrite_index_invalid'):
        p.apply_confirmations([case],dict(confirmed_by='user',rule='r',cases={},
            abstract_rewrites={'29_见面':{'1':'越界'}}))


def test_cli_exposes_confirmations_option(monkeypatch):
    seen=[]
    monkeypatch.setattr(cli,'prepare',lambda args:seen.append(args.confirmations))
    cli.main(['prepare','--output','/memory/output','--protocol-version','v4',
        '--confirmations','/memory/review/confirmations_v4.json'])
    assert seen==[Path('/memory/review/confirmations_v4.json')]


def test_prepare_applies_user_confirmations_and_keeps_model_draft(monkeypatch,memory):
    files,put,read,sha=memory
    value=manifest(10)
    put('inventory',inventory(value));put('runtime',{})
    monkeypatch.setattr(exam,'prepare_labels',lambda *args:drafted())
    monkeypatch.setattr(p,'freeze',lambda value,path:read(path) if str(path) in files else (put(path,value) or value))
    monkeypatch.setattr(cli,'cards',lambda *args:None)
    cli.prepare(prepare_args(Path('/memory/plain')))
    plain=read('/memory/plain/manifest.json')
    assert 'scoring_rule' not in plain and 'confirmations_sha256' not in plain
    assert all('element_label_source' not in c['labels'] for c in plain['cases'])
    ids={c['name']:'t_'+json_sha(c['name'])[:16] for c in value['cases']}
    first,second=value['cases'][0]['name'],value['cases'][1]['name']
    confirmations=dict(confirmed_by='user',rule='主旨要素按抽象层级判定',
        cases={ids[first]:dict(name=first,elements='confirmed',cut_point='pending',same_video='是'),
            ids[second]:dict(name=second,elements='pending',cut_point='confirmed',same_video='pending')},
        abstract_rewrites={first:{'2':'换成抽象表述'},second:{'0':'利益冲突导致悲剧'}},
        abstract_rewrites_status='confirmed_by_user_2026-10-24')
    put('/memory/confirmations.json',confirmations)
    cli.prepare(prepare_args(Path('/memory/confirmed'),Path('/memory/confirmations.json')))
    saved=read('/memory/confirmed/manifest.json')
    assert saved['scoring_rule']=='主旨要素按抽象层级判定'
    assert saved['confirmations_sha256']==sha('/memory/confirmations.json')
    labels={c['id']:c['labels'] for c in saved['cases']}
    q1,q2=labels[ids[first]],labels[ids[second]]
    assert q1['elements']==['具体情节A','抽象要素B','换成抽象表述']
    assert q1['original_elements']==['具体情节A','抽象要素B','具体情节C']
    assert q1['element_rewrites']=={2:'换成抽象表述'}
    assert q1['element_label_source']=='human_confirmed'
    assert q1['cut_point_source']=='model_assisted_unconfirmed' and q1['same_video_source']=='human_confirmed'
    assert q2['elements']==['利益冲突导致悲剧','抽象要素B','具体情节C']
    assert q2['element_rewrites']=={0:'利益冲突导致悲剧'}
    assert q2['element_label_source']=='model_assisted_unconfirmed' and q2['cut_point_source']=='human_confirmed'
    assert q1['provenance']==q2['provenance']=='model_assisted_unconfirmed'
    draft=read(f'/memory/confirmed/preparation/{ids[first]}/labels.json')['labels']
    assert draft['elements']==['具体情节A','抽象要素B','具体情节C'] and 'element_label_source' not in draft


@pytest.mark.parametrize('rewrites,expected',[
    ({},'reference_eval_v4_confirmations_not_user'),
    ({'5':'越界'},'reference_eval_v4_rewrite_index_invalid')])
def test_prepare_rejects_unsigned_or_out_of_range_confirmations_before_freeze(monkeypatch,memory,rewrites,expected):
    files,put,read,sha=memory
    value=manifest(10)
    put('inventory',inventory(value));put('runtime',{})
    monkeypatch.setattr(exam,'prepare_labels',lambda *args:drafted())
    put('/memory/confirmations.json',dict(confirmed_by='model' if not rewrites else 'user',rule='r',
        cases={},abstract_rewrites={value['cases'][0]['name']:rewrites} if rewrites else {}))
    with pytest.raises(ValueError,match=expected):
        cli.prepare(prepare_args(Path('/memory/bad'),Path('/memory/confirmations.json')))
    assert '/memory/bad/manifest.json' not in files


def test_report_splits_elements_by_element_label_source_and_states_rule(monkeypatch,memory):
    files,put,read,sha=memory
    value=manifest(confirmed=True)
    pipeline(monkeypatch,memory,value)
    root=Path('/memory/output')
    for case in value['cases']:cli.task(SimpleNamespace(manifest='memory',case=case['id'],output=root))
    core.music_tasks(value,root)
    cli.report(SimpleNamespace(manifest='memory',output=root,human_reviews=None))
    result=read(root/'report.json')
    assert result['scoring_rule']=='主旨要素按抽象层级判定' and result['confirmations_sha256']=='confirmation-sha'
    split=result['groups']['elements']['element_label_sources']
    assert split['human_confirmed']==dict(planned=1,scored=1,
        means_scored=dict(full_match_rate=1.0,full_match_rate_all_planned=1.0,label_verification_coverage=1.0))
    assert split['model_assisted_unconfirmed']==dict(planned=1,scored=1,
        means_scored=dict(full_match_rate=1.0,full_match_rate_all_planned=1.0,label_verification_coverage=1.0))
    elements={r['case_id']:r['element_label_source'] for r in result['rows'] if r['task']=='elements'}
    assert elements=={'t_0':'model_assisted_unconfirmed','t_1':'human_confirmed'}
    text=files[str(root/'report.md')]
    assert '人工确认评分规则：主旨要素按抽象层级判定' in text and 'confirmation-sha' in text
    value.pop('confirmations_sha256');value.pop('scoring_rule')
    for case in value['cases']:case['labels'].pop('element_label_source',None)
    cli.report(SimpleNamespace(manifest='memory',output=Path('/memory/plain'),human_reviews=None))
    plain=read('/memory/plain/report.json')
    assert 'element_label_sources' not in plain['groups']['elements'] and 'scoring_rule' not in plain
    assert '人工确认评分规则' not in files['/memory/plain/report.md']
