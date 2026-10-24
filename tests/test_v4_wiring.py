import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.evaluation import reference_cli as cli, reference_core as core
from omni_story.evaluation import reference_exam as exam, reference_protocol as p
from omni_story.library.media import sha256_file
from omni_story.library.state import json_sha, write_json


def manifest(count=2):
    cases=[]
    for index in range(count):
        case_id=f't_{index}'
        cases.append(dict(id=case_id,name=f'{index:02d}_模板{index}',evaluation_target='core_meaning_v2',
            reference=dict(path=case_id+'r',sha256=case_id+'r',asset_id=case_id+'r',duration_s=20),
            query=dict(path=case_id+'q',sha256=case_id+'q',asset_id=case_id+'q',duration_s=20),
            labels=dict(provenance='human_confirmed' if index else 'model_assisted_unconfirmed',
                elements=['勇于迈步'],same_mechanism=True,story_paragraphs=[f'第{index}位居民付诸行动','仍旧等待'],
                story_target_indices=[0],theme_summary='行动带来改变',core_evidence=[],theme_reveal_s=8,
                prefix_cut_s=4,prefix_information='implied',prefix_acceptable_themes=['勇于迈步'],bgm_question=None)))
    return dict(protocol_version='v4',schema_version=p.SCHEMA,protocol_sha256='frozen',experiment_id='test',
        story_seed=17,methods=p.METHODS,cases=cases,human_review_cases=[c['id'] for c in cases[:6]],
        production=dict(configuration={},production_files={}),receipts='unused',bgm_measurements=[])


def policy(monkeypatch,points=None):
    seen=[]
    def validate(labels,duration):
        seen.append((labels,duration))
        if not 0<labels['theme_reveal_s']<duration:raise ValueError('invalid_reveal')
    module=SimpleNamespace(validate_reveal=validate,cut_points=lambda duration,reveal:copy.deepcopy(points or [
        dict(name='before_reveal',cut_s=reveal/2),dict(name='after_reveal',cut_s=reveal+1)]))
    monkeypatch.setitem(sys.modules,'omni_story.evaluation.prefix_policy',module)
    return seen


def judgment(inference=False):
    raw=dict(items=[dict(gold_id='g000',gold_validity='valid',relation='matched',candidate_ids=['u000'],
        reason='对应主旨',evidence=[dict(start_s=0,end_s=1,observation='迈步')])],limitations=[])
    if inference:raw['inference_validity']='plausible'
    return raw


def receipt(directory,raw,**extra):
    state=directory/'receipt/state.json'
    write_json(state,dict(status='received'))
    write_json(state.with_name('response.json'),dict(http_status=200,
        body_text=json.dumps(dict(choices=[dict(message=dict(content=json.dumps(raw,ensure_ascii=False)))]))))
    return dict(path=str(state),state_sha256=sha256_file(state),
        response_sha256=sha256_file(state.with_name('response.json')),**extra)


def test_freeze_audits_v4_and_validates_each_reveal(monkeypatch,tmp_path):
    seen=policy(monkeypatch)
    value=manifest(10)
    monkeypatch.setattr(p,'sha256_file',str)
    value.pop('protocol_sha256')
    frozen=p.freeze(value,tmp_path/'manifest.json')
    assert len(seen)==10 and frozen['protocol_sha256']==json_sha(value)
    value['cases'][0]['labels']['story_paragraphs'][0]='模板0被直接写进答案'
    with pytest.raises(ValueError,match='reference_eval_v4_story_keyword_separable'):
        p.freeze(value,tmp_path/'leaked.json')
    assert not (tmp_path/'leaked.json').exists()
    value['cases'][0]['labels']['story_paragraphs'][0]='居民付诸行动'
    value['cases'][0]['labels']['theme_reveal_s']=30
    with pytest.raises(ValueError,match='invalid_reveal'):p.validate(value,media=False)


def test_mixed_story_shared_gold_and_set_metrics():
    value=manifest();a,b=value['cases']
    assert p.story_inputs(value,a['id'])==p.story_inputs(value,b['id'])==p.story_text(value)
    assert len(p.story_text(value))==4
    gold=p.story_gold(value,a['id'])
    result=exam.story_metrics(dict(paragraph_ids=list(gold)),value,a,compile_quotes=True)
    assert result==dict(precision=1,recall=1,f1=1,exact=True)
    assert 'hit_at_1' not in result
    assert exam.story_metrics(dict(paragraph_ids=[]),value,a)==dict(precision=None,recall=0,f1=None,exact=False)
    other=p.story_gold(value,b['id'])
    assert exam.story_metrics(dict(paragraph_ids=list(gold|other)),value,a,compile_quotes=True)['precision']==.5
    b['labels']['story_preparation_error']='no story'
    assert len(p.story_text(value))==2 and p.story_gold(value,b['id'])==set()
    with pytest.raises(ValueError,match='identity_invalid'):
        exam.story_metrics(dict(paragraph_ids=['unknown']),value,a,compile_quotes=True)


def test_missing_parallel_modules_have_clear_errors(monkeypatch):
    def missing(*args,**kwargs):raise ImportError('not merged')
    monkeypatch.setattr(p.importlib,'import_module',missing)
    for name in ('prefix_policy','bgm_measure'):
        with pytest.raises(RuntimeError,match='reference_eval_v4_'+name+'_unavailable'):p.v4_module(name)


def test_prepare_measures_bgm_and_freezes_eligibility(monkeypatch,tmp_path):
    assets=lambda index:[dict(path=f'{index}{role}.mp4',sha256=f'{index}{role}',duration_s=10) for role in 'rq']
    inventory=dict(templates=[dict(folder=f'{i:02d}_模板{i}',assets=assets(i)) for i in range(10)],bgm_items=[dict(query_id='q')])
    rows=[dict(case_id='t_missing',query_id='q',duration_s=3,music_rms_db=-20,speech_words=0)]
    seen=[]
    def measure(items):seen.append(items);return rows
    monkeypatch.setitem(sys.modules,'omni_story.evaluation.bgm_measure',SimpleNamespace(measure_all=measure))
    monkeypatch.setattr(p,'read',lambda path:inventory if str(path)=='inventory' else {})
    monkeypatch.setattr(exam,'prepare_labels',lambda *args:dict(provenance='model_assisted_unconfirmed'))
    def freeze(value,path):
        assert value['bgm_measurements']==rows
        assert value['bgm_eligibility']['templates']['t_missing']['status']=='excluded_at_design'
        assert value['protocol_version']=='v4' and value['asr_profile'] is None
        return dict(value,protocol_sha256='frozen')
    monkeypatch.setattr(p,'freeze',freeze)
    monkeypatch.setattr(cli,'cards',lambda *args:None)
    args=SimpleNamespace(inventory='inventory',curation=None,output=tmp_path,receipts=tmp_path/'receipts',
        runtime='runtime',experiment_id='test',protocol_version='v4',bgm_measurements=None,story_seed=17,asr_profile=None)
    cli.prepare(args)
    assert seen==[inventory['bgm_items']]
    inventory['bgm_measurements']=rows
    cli.prepare(SimpleNamespace(**dict(vars(args),output=tmp_path/'premeasured')))
    assert len(seen)==1


def fake_pipeline(monkeypatch,tmp_path,value,*,repair=False):
    value['receipts']=str(tmp_path/'receipts')
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path/'registry'))
    policy(monkeypatch)
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE',raising=False)
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    seen=dict(subjects=[],reviews=[],questions=[],prefixes=[])
    def subject(case,protocol,folder,**kwargs):
        seen['subjects'].append((case,protocol,kwargs))
        saved=dict(status='reference_collected',understanding=dict(theme=case['id']),manual_correction=False,
            understanding_sha256=json_sha(dict(theme=case['id'])),
            binding=dict(reference_sha256=case['reference']['sha256'],configuration=protocol['production']['configuration']))
        path=folder/'subject/subject.json';write_json(path,saved)
        return dict(saved,evidence_path=str(path))
    monkeypatch.setattr(cli,'subject',subject)
    from omni_story.evaluation import production_subject
    def verify(saved):assert saved['understanding_sha256']==json_sha(saved['understanding'])
    monkeypatch.setattr(production_subject,'verify_subject',verify)
    monkeypatch.setattr(core,'verify_subject',verify)
    def prefix(asset,cut,folder):
        seen['prefixes'].append(cut)
        path=folder/'prefix.mp4';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(str(cut).encode())
        write_json(folder/'prefix.json',dict(binding=dict(source_sha256=asset['sha256'],end_s=cut)))
        return path
    monkeypatch.setattr(p,'prefix',prefix)
    def review(assets,roles,prompt,data,directory,**kwargs):
        seen['reviews'].append((directory.name,prompt,data,kwargs))
        raw=judgment('forecast@' in directory.name)
        if repair and not directory.name.endswith('_repair1'):raw['items'][0].pop('evidence')
        return raw,receipt(directory,raw)
    monkeypatch.setattr(exam,'paid_review',review)
    def probe(question,folder,**kwargs):
        seen['questions'].append(question)
        assert question['story']==p.story_text(value)
        answer=dict(paragraph_ids=sorted(p.story_gold(value,question['understanding']['theme'])))
        write_json(folder/'answer.json',dict(answer=answer))
        return answer
    monkeypatch.setattr(exam,'ask_probe',probe)
    monkeypatch.setattr(exam,'verify_probe',lambda saved:None)
    return seen


def test_v4_four_tasks_conditions_repairs_and_report(monkeypatch,tmp_path):
    value=manifest();seen=fake_pipeline(monkeypatch,tmp_path,value,repair=True)
    for case in value['cases']:
        cli.task(SimpleNamespace(manifest='memory',case=case['id'],output=tmp_path))
    core.music_tasks(value,tmp_path)
    cli.report(SimpleNamespace(manifest='memory',output=tmp_path,human_reviews=None))
    result=json.loads((tmp_path/'report.json').read_text())
    assert len(result['rows'])==10 and result['groups']['forecast']['planned']==4
    assert result['groups']['story']['means_scored']==dict(precision=1,recall=1,f1=1,exact=1)
    assert result['prefix_subset']['groups']['implied']['scored']==4
    assert result['story_leakage']['keyword_separable']==0
    assert result['label_sources']=={'human_confirmed':dict(planned=5,scored=4),
        'model_assisted_unconfirmed':dict(planned=5,scored=4)}
    assert result['groups']['forecast']['label_sources']['human_confirmed']['element_counts']['matched']==2
    forecasts=[row for row in result['rows'] if row['task']=='forecast']
    assert {row['condition'] for row in forecasts}=={'forecast@before_reveal','forecast@after_reveal'}
    assert {row['prefix_cut_s'] for row in forecasts}=={4,9}
    assert seen['prefixes']==[4,9,4,9]
    for case,protocol,kwargs in seen['subjects']:
        assert kwargs['cache_root']==tmp_path/'subject_cache'
        if 'condition' in kwargs:
            assert kwargs['condition'] in {'prefix@4.0','prefix@9'}
            assert protocol['production']['configuration']['reference_context']==core.PREFIX_CONTEXT
    for case in value['cases']:
        records=json.loads((tmp_path/'cases'/case['id']/'repair.json').read_text())
        assert set(records)=={'elements_ids_v1','forecast@before_reveal','forecast@after_reveal'}
        assert all(r['calls']==1 and r['defects_after']==[] for r in records.values())
    assert len([r for r in seen['reviews'] if r[0].endswith('_repair1')])==6
    assert all(r['status']=='not_applicable_at_design' for r in result['rows'] if r['task']=='bgm')
    assert 'hit_at_1' not in (tmp_path/'report.md').read_text()


def test_skipped_prefix_and_excluded_bgm_never_enter_runtime(monkeypatch,tmp_path):
    value=manifest(1);seen=fake_pipeline(monkeypatch,tmp_path,value)
    policy(monkeypatch,[dict(name='before_reveal',cut_s=4),
        dict(name='after_reveal',cut_s=21,skipped_reason='outside_video')])
    cli.task(SimpleNamespace(manifest='memory',case='t_0',output=tmp_path))
    row=json.loads((tmp_path/'cases/t_0/forecast@after_reveal.json').read_text())
    assert row['status']=='not_applicable' and row['skipped_reason']=='outside_video'
    assert len(seen['subjects'])==2 and seen['prefixes']==[4]
    value['bgm_measurements']=[dict(case_id='t_0',query_id='q',duration_s=3,music_rms_db=-20,speech_words=1)]
    monkeypatch.setattr(core,'_full_subject',lambda *args:pytest.fail('excluded BGM entered runtime'))
    core.music_tasks(value,tmp_path)
    assert json.loads((tmp_path/'cases/t_0/bgm.json').read_text())['status']=='not_applicable_at_design'


def test_repair_is_once_and_truncated_empty_reply_is_not_resent(monkeypatch,tmp_path):
    calls=[]
    def broken(*args,**kwargs):
        calls.append(args[4].name)
        return {},dict(raw_text='{}')
    monkeypatch.setattr(exam,'paid_review',broken)
    with pytest.raises(ValueError,match='items_missing'):
        exam.checked_review([],{},'prompt',{},tmp_path/'elements',tmp_path,count=1)
    assert calls==['elements','elements_repair1']
    assert json.loads((tmp_path/'repair.json').read_text())['elements']['calls']==1
    calls.clear()
    def truncated(*args,**kwargs):
        calls.append(1)
        return None,dict(raw_text='',stop_reason='max_tokens')
    monkeypatch.setattr(exam,'paid_review',truncated)
    with pytest.raises(ValueError,match='empty_body_truncated'):
        exam.checked_review([],{},'prompt',{},tmp_path/'forecast',tmp_path,count=1,require_inference=True)
    assert calls==[1]
    def unknown(*args,**kwargs):raise RuntimeError('uncertain_no_replay')
    monkeypatch.setattr(exam,'paid_review',unknown)
    with pytest.raises(RuntimeError,match='uncertain'):
        exam.checked_review([],{},'prompt',{},tmp_path/'unknown',tmp_path,count=1)
    assert 'unknown' not in json.loads((tmp_path/'repair.json').read_text())


def test_received_bad_json_uses_paid_repair_channel_and_preserves_receipts(monkeypatch,tmp_path):
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path/'registry'))
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE',str(tmp_path/'budget'))
    media=tmp_path/'packet';media.write_bytes(b'packet')
    monkeypatch.setattr(exam,'_packet',lambda *args,**kwargs:(media,dict(mapping=[],sha256='packet')))
    calls=[]
    def review(media,folder,**kwargs):
        calls.append((folder.parent.name,kwargs['prompt']))
        text='bad json' if len(calls)==1 else '```json\n'+json.dumps(judgment())+'\n```'
        write_json(folder/'state.json',dict(status='received'))
        write_json(folder/'response.json',dict(http_status=200,
            body_text=json.dumps(dict(choices=[dict(message=dict(content=text),finish_reason='stop')]))))
        return dict(parsed=None,http_status=200,reply=text)
    monkeypatch.setattr(exam,'review_audio_video',review)
    raw,saved=exam.checked_review([dict(asset_id='r',sha256='media')],{},'original',{},tmp_path/'elements',tmp_path,count=1)
    assert raw==judgment() and [name for name,prompt in calls]==['elements','elements_repair1']
    assert 'bad json' in calls[1][1] and '不改变任何已有判断' in calls[1][1]
    assert exam.received_judgment(saved)==judgment()
    record=json.loads((tmp_path/'repair.json').read_text())['elements']
    Path(record['original_receipt']['path']).write_text('{}')
    with pytest.raises((ValueError,KeyError)):exam.received_judgment(saved)


def test_bgm_included_template_enters_runtime_only_after_two_clean_queries(monkeypatch,tmp_path):
    value=manifest();value['production']['music_candidate']=dict(root='frozen',files={})
    value['bgm_measurements']=[dict(case_id='t_0',query_id=f'q{i}',duration_s=3,music_rms_db=-20,speech_words=0) for i in range(2)]
    monkeypatch.setattr(core,'_full_subject',lambda case,*args:dict(case_id=case['id'],subject_path='subject',understanding_sha256='reading'))
    monkeypatch.setattr(core,'verify_tree',lambda *args:None)
    calls=[]
    def run(command,**kwargs):
        job=json.loads(Path(command[-1]).read_text());calls.append(job)
        write_json(Path(job['output'])/'answer.json',dict(status='no_match',candidates=[]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(core.subprocess,'run',run)
    write_json(tmp_path/'cases/t_0/elements.json',dict(metrics=dict(full_match_rate=1)))
    core.music_tasks(value,tmp_path)
    assert len(calls)==1 and calls[0]['query']==value['cases'][0]['query']
    assert json.loads((tmp_path/'cases/t_0/bgm.json').read_text())['status']=='scored_provisional'
    assert json.loads((tmp_path/'cases/t_1/bgm.json').read_text())['status']=='not_applicable_at_design'


def test_prefix_registry_keeps_unknown_blocked_until_adjudicated(monkeypatch,tmp_path):
    from omni_story.evaluation import adjudicate
    value=manifest(1);seen=fake_pipeline(monkeypatch,tmp_path,value)
    ledger=tmp_path/'old/production_stage/library_state.json'
    write_json(ledger,dict(calls=[dict(id='old_prefix',status='uncertain')]))
    write_json(tmp_path/'registry/core_prefix_registry.json',dict(old=dict(
        source_sha256=value['cases'][0]['reference']['sha256'],output=str(tmp_path/'old'))))
    args=SimpleNamespace(manifest='memory',case='t_0',output=tmp_path)
    cli.task(args)
    paths=[tmp_path/'cases/t_0'/f'forecast@{name}.json' for name in ('before_reveal','after_reveal')]
    assert all(json.loads(path.read_text())['status']=='blocked_unknown' for path in paths)
    assert seen['prefixes']==[]
    adjudicate.adjudicate(ledger,'old_prefix',decision='abandoned_by_user',decided_by='user',user_note='cancel')
    for path in paths:path.unlink()
    cli.task(args)
    assert all(json.loads(path.read_text())['status']=='scored_provisional' for path in paths)
    assert seen['prefixes']==[4,9]


def test_adjudicated_unknown_calls_pass_wiring_without_mutating_ledger(monkeypatch,tmp_path):
    from omni_story.evaluation import adjudicate
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path))
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE',str(tmp_path/'budget'))
    old=tmp_path/'old';state=old/'receipt/state.json'
    write_json(state,dict(id='call',status='uncertain'))
    original=state.read_bytes()
    write_json(tmp_path/'production_exam_media_registry.json',dict(old=dict(directory=str(old),sources=['media'])))
    with pytest.raises(RuntimeError,match='unknown_source_no_replay'):
        exam.paid_review([dict(asset_id='r',sha256='media')],{},'prompt',{},tmp_path/'new')
    state.with_name('adjudications.jsonl').write_text(json.dumps(dict(call_id='call',decision='abandoned_by_user'))+'\n')
    assert not p.blocked(state)
    media=tmp_path/'packet';media.write_bytes(b'packet')
    monkeypatch.setattr(exam,'_packet',lambda *args,**kwargs:(media,dict(mapping=[],sha256='packet')))
    def review(media,folder,**kwargs):
        write_json(folder/'state.json',dict(status='received'))
        write_json(folder/'response.json',{})
        return dict(parsed={},http_status=200)
    monkeypatch.setattr(exam,'review_audio_video',review)
    assert exam.paid_review([dict(asset_id='r',sha256='media')],{},'prompt',{},tmp_path/'new')[0]=={}
    assert state.read_bytes()==original
    ledger=tmp_path/'subject/production_stage/library_state.json'
    write_json(ledger,dict(calls=[dict(id='subject',status='submitted')]))
    assert p.blocked(ledger)
    adjudicate.adjudicate(ledger,'subject',decision='abandoned_by_user',decided_by='user',user_note='cancel')
    assert not p.blocked(ledger)


@pytest.mark.parametrize('profile',[None,dict(runner=['local-runner'],memory_max_bytes=2*1024**3,lock_path='asr.lock')])
def test_subject_cache_and_asr_profile_bootstrap(monkeypatch,tmp_path,profile):
    from omni_story.evaluation import production_subject,isolated_asr
    from omni_story.library import pipeline
    media=tmp_path/'media';media.write_bytes(b'media')
    release=tmp_path/'release';release.mkdir()
    monkeypatch.setattr(pipeline,'__file__',str(release/'src/omni_story/library/pipeline.py'))
    monkeypatch.setattr(production_subject,'verify_tree',lambda *args:None)
    monkeypatch.setattr(production_subject,'verify_subject',lambda saved:None)
    produced=[];installed=[]
    def capture(run,**kwargs):
        produced.append(run['condition'])
        saved=dict(status='reference_collected',understanding=dict(theme='行动'),understanding_sha256=json_sha(dict(theme='行动')),
            artifacts={},manual_correction=False,binding={k:run[k] for k in
                ('reference','reference_sha256','production_root','production_files','configuration','asr_model_dir')})
        write_json(Path(run['output'])/'subject.json',saved)
        return saved
    monkeypatch.setattr(production_subject,'_capture_reference',capture)
    monkeypatch.setattr(isolated_asr,'install',lambda root,production_root,**kwargs:installed.append(kwargs['profile']))
    monkeypatch.setitem(sys.modules,'production_subject',production_subject)
    monkeypatch.setitem(sys.modules,'isolated_asr',isolated_asr)
    def run(command,**kwargs):
        with monkeypatch.context() as context:
            context.setattr(sys,'argv',['-c',*command[3:]])
            context.setattr(sys,'path',list(sys.path))
            exec(command[2],{})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli.subprocess,'run',run)
    value=manifest(1);value['asr_profile']=profile
    value['production'].update(production_root=str(release),asr=False,asr_model_dir=None)
    case=dict(value['cases'][0],reference=dict(path=str(media),sha256=sha256_file(media)))
    root=tmp_path/'subject_cache'
    first=cli.subject(case,value,tmp_path/'a',cache_root=root)
    second=cli.subject(case,value,tmp_path/'b',cache_root=root)
    cli.subject(case,value,tmp_path/'prefix',condition='prefix@4',cache_root=root)
    assert first['understanding']==second['understanding']
    assert produced==['full','prefix@4'] and installed==[profile,profile,profile]
    assert len(list(root.glob('*/meta.json')))==2


def test_legacy_story_and_report_output_stay_unchanged(monkeypatch,tmp_path):
    value=manifest(1);value.pop('protocol_version');value['cases'][0]['labels']['provenance']='model_assisted_unconfirmed'
    assert p.story_inputs(value,'t_0')==[dict(id='p000',text='第0位居民付诸行动'),dict(id='p001',text='仍旧等待')]
    assert exam.story_metrics(dict(paragraph_ids=['p000']),value,value['cases'][0],compile_quotes=True)==dict(
        hit_at_1=1,exact_range=1,paragraph_precision=1,paragraph_recall=1,raw_quotation_exact=0)
    monkeypatch.setattr(p,'load',lambda *args,**kwargs:value)
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE',raising=False)
    cli.report(SimpleNamespace(manifest='memory',output=tmp_path,human_reviews=None))
    result=json.loads((tmp_path/'report.json').read_text())
    assert len(result['rows'])==4 and result['groups']['forecast']['planned']==10
    assert not {'protocol_version','prefix_subset','story_leakage','label_sources'}&result.keys()
    assert all(row['status']=='pending' and 'condition' not in row for row in result['rows'])


def test_legacy_unknown_checkpoint_stays_blocked_without_adjudication(tmp_path):
    original=tmp_path/'subject'
    write_json(original/'production_stage/library_state.json',dict(calls=[dict(id='old',status='received')]))
    write_json(tmp_path/'subject-status.json',dict(status='blocked_unknown',original_output=str(original)))
    with pytest.raises(RuntimeError,match='original_source_unknown_no_replay'):
        core.prefix_task(dict(labels={}),{},tmp_path,tmp_path/'receipts')
