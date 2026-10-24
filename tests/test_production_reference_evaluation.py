import copy
import json
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from omni_story.evaluation import production_subject as subject
from omni_story.evaluation import reference_protocol as protocol, reference_exam as exam
from omni_story.library.state import json_sha, write_json, LibraryStopped
from omni_story.library.media import sha256_file


def test_observer_runs_original_request_and_stops_before_downstream(tmp_path, monkeypatch):
    from omni_story.library import pipeline, visual_story_trial, clean_chain
    from omni_story.library.opencode_provider import PROVIDER
    monkeypatch.delenv('OMNI_EVAL_BUDGET_FILE', raising=False)
    media = tmp_path/'original.mp4';media.write_bytes(b'original input fixture')
    sha=sha256_file(media)
    source=dict(source_id='reference',sha256=sha,path=str(media),duration_s=12,audio_stream_index=None)
    monkeypatch.setattr(pipeline,'_catalog',lambda *a,**kw:dict(sources=[source]))
    monkeypatch.setattr(visual_story_trial,'proxy',lambda *a,**kw:dict(path=str(media)))
    config=dict(PROVIDER,workflow='reference_rough_skill_v1',creative_audio=True)
    value=dict(reference_sha256=sha,theme='表面强势与实际弱小的反差',intended_takeaway='挑衅后被揭穿',
        visible_evidence=[dict(start_s=1,end_s=2,observed_fact='角色挑衅',supports='冲突开端')],
        editing_methods=[dict(method='正反打',function='对峙',visual_evidence='两人交替',start_s=1,end_s=3)],
        uncertainties=[],strategy=dict(adaptation_mode='reinterpret',speech_mode='none',timing_driver='action'),
        speech_form='音频未知',visual_form=dict(description='挑衅后反应',evidence=[]))
    requests=[]
    class Delegate:
        exclusions=[]
        def __init__(self,state):self.state=state
        def call(self,name,prompt,media,validator,**kwargs):
            assert name=='reference'
            validator(value)
            request=dict(tool='analyze_video',arguments=dict(prompt=prompt,video_source=str(media)),
                         media_sha256=sha256_file(media),provider=PROVIDER['provider'],**kwargs)
            requests.append(request)
            call,folder=self.state.begin_call(name,request)
            self.state.complete_call(call,dict(result=dict(content=[dict(type='text',text=json.dumps(value))])))
            write_json(folder/'parsed.json',value)
            return value
    class AfterReference(BaseException):pass
    def stop(*args,**kw):raise AfterReference()
    monkeypatch.setattr(pipeline,'_adaptive_coarse',stop)
    with pytest.raises(AfterReference):
        pipeline.execute(media,media,tmp_path/'ordinary',model_factory=Delegate,provider_config=config,
            asr=False,max_requests=None,max_fine=None,prompt_module=clean_chain.ROUGH_PROMPTS)
    run=dict(production_root=str(Path(pipeline.__file__).parents[3]), production_files={},
        reference=str(media),reference_sha256=sha,output=str(tmp_path/'evaluation'),configuration=config,
        library=str(media),asr=False,asr_model_dir=None,registry_path=str(tmp_path/'registry.json'))
    result=subject.capture_reference(run,provider_factory=Delegate)
    assert requests[0]==requests[1]
    assert result['understanding']==value and result['status']=='reference_collected'
    assert not (tmp_path/'evaluation/production_stage/result.json').exists()
    assert not (tmp_path/'evaluation/production_stage/failure.json').exists()
    assert len(list((tmp_path/'evaluation/production_stage/calls').iterdir()))==1
    def forbidden(*args):pytest.fail('Received component must be reused')
    assert subject.capture_reference(run,provider_factory=forbidden)==result
    reuse_only=dict(run,output=str(tmp_path/'missing'),reuse_only=True)
    with pytest.raises(ValueError,match='reuse_only_no_completed_result'):
        subject.capture_reference(reuse_only,provider_factory=forbidden)
    parsed=next(Path(p) for p in result['artifacts'] if p.endswith('/parsed.json'))
    parsed.write_text('{}')
    with pytest.raises(ValueError,match='receipt_changed'):
        subject.capture_reference(run,provider_factory=forbidden)


def test_reference_freeze_rejects_changed_production_source(tmp_path):
    path=tmp_path/'module.py';path.write_text('original')
    files={'module.py':sha256_file(path)}
    subject.verify_tree(tmp_path,files)
    path.write_text('changed')
    with pytest.raises(ValueError,match='freeze_changed'):
        subject.verify_tree(tmp_path,files)


def test_external_native_budget_reservation_is_atomic_and_idempotent(tmp_path,monkeypatch):
    from omni_story.evaluation.budget import reserve_request
    path=tmp_path/'budget.json'
    write_json(path,dict(experiment_id='test',requests=[],deadline_unix=time.time()+60,
        generation_call_limit=1,max_model_calls=1,per_run_max_model_calls=1))
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE',str(path))
    monkeypatch.setenv('OMNI_EVAL_RUN_ID','test/case/method')
    first=reserve_request(reservation_key='original_native_request',audit=dict(timing='pre_submission'))
    assert reserve_request(reservation_key='original_native_request')==first
    assert len(json.loads(path.read_text())['requests'])==1
    with pytest.raises(RuntimeError,match='budget'):
        reserve_request(reservation_key='another_request')
    value=json.loads(path.read_text());value['stop_reason']='user_stop';write_json(path,value)
    with pytest.raises(RuntimeError,match='stopped'):
        reserve_request(reservation_key='original_native_request')


def test_isolated_asr_preserves_original_segment_and_word_values():
    from omni_story.evaluation.isolated_asr import restore_segments
    from omni_story.library.asr import _raw_segment
    raw=dict(id=1,seek=0,start=1.2,end=2.4,text='测试对白',tokens=[1,2],avg_logprob=-.1,
        compression_ratio=1.0,no_speech_prob=.01,temperature=0,
        words=[dict(start=1.2,end=2.4,word='测试对白',probability=.95)])
    assert _raw_segment(restore_segments([raw])[0])==raw


def test_known_encoding_padding_is_trimmed_but_bad_bgm_clock_is_rejected():
    bound=dict(packet_start_s=43.466667,packet_end_s=90.533334,source_duration_s=47.033333)
    value=dict(start_packet_s=43.467,end_packet_s=90.533,stage='阶段',ambiguous=False)
    assert exam.normalize_bgm_question(value,bound)['end_s']==47.033333
    value['start_packet_s']=43.46
    with pytest.raises(ValueError,match='range_invalid'):
        exam.normalize_bgm_question(value,bound)
    value.update(start_packet_s=90.52,end_packet_s=90.53)
    with pytest.raises(ValueError,match='padding_only'):
        exam.normalize_bgm_question(value,bound)


def test_bad_story_and_music_labels_do_not_discard_valid_other_questions(monkeypatch,tmp_path):
    case=dict(reference=dict(asset_id='ref',duration_s=10),query=dict(asset_id='query',duration_s=30))
    raw=dict(same_mechanism=True,elements=['挑衅','揭穿'],cut_packet_s=15,
             future_events=['揭穿'],story_paragraphs=['这是长故事。'*20]*8,
             bgm_question=dict(start_packet_s=0,end_packet_s=2,stage='阶段',ambiguous=False))
    receipt=dict(packet=dict(mapping=[dict(asset_id='query',packet_start_s=10,packet_end_s=40,source_duration_s=30)]))
    monkeypatch.setattr(exam,'paid_review',lambda *a,**kw:(raw,receipt))
    labels=exam.prepare_labels(case,tmp_path)
    assert labels['elements']==['挑衅','揭穿'] and labels['cut_s']==5
    assert 'story_preparation_error' in labels and 'bgm_preparation_error' in labels
    assert 'forecast_preparation_error' not in labels


def test_core_labels_cut_reference_and_do_not_require_query_event_predictions(monkeypatch,tmp_path):
    case=dict(reference=dict(asset_id='r',duration_s=10),query=dict(asset_id='q',duration_s=12),
        evaluation_target='core_meaning_v2',curation=dict(theme_hypothesis='行动胜于犹豫'))
    raw=dict(reference_core_supported=True,theme_summary='行动带来改变',same_mechanism=True,
        elements=['行动优于反复犹豫'],reference_cut_packet_s=3,prefix_information='implied',
        prefix_acceptable_themes=['行动需要勇气'],story_paragraphs=['面对犹豫终于迈步。'*15]*8,story_target_indices=[2],bgm_question=None)
    packet=dict(mapping=[dict(asset_id='r',packet_start_s=0,packet_end_s=10,source_duration_s=10),
                         dict(asset_id='q',packet_start_s=10,packet_end_s=22,source_duration_s=12)])
    monkeypatch.setattr(exam,'paid_review',lambda *a,**kw:(raw,dict(packet=packet)))
    labels=exam.prepare_labels(case,tmp_path)
    assert labels['prefix_cut_s']==3 and 'forecast_preparation_error' not in labels
    assert labels['future_events']==[] and labels['elements']==['行动优于反复犹豫']
    question=protocol.public_question(case,'story',dict(theme='行动'),[dict(id='p000',text='长故事')])
    assert '核心主旨' in question['question'] and 'theme_hypothesis' not in json.dumps(question)


def test_exam_question_does_not_leak_hidden_answers():
    case=dict(labels=dict(future_events=['SECRET_SUFFIX'],story_target_indices=[6],cut_s=7),
              query=dict(path='SECRET_FULL_VIDEO.mp4'))
    candidate={'theme':'公开的原理解'}
    for task in ['forecast','story']:
        public=protocol.public_question(case,task,candidate,[dict(id='p001',text='长故事正文')])
        encoded=json.dumps(public)
        assert all(secret not in encoded for secret in ['SECRET_SUFFIX','SECRET_FULL_VIDEO','story_target_indices','cut_s'])
        assert public['understanding']==candidate
    with pytest.raises(ValueError,match='fake_audio'):
        protocol.public_question(case,'bgm',candidate,[])


def test_element_judge_requires_candidate_quote_not_just_video_content():
    case=dict(labels=dict(elements=['挑衅','揭穿']),reference=dict(duration_s=10))
    reading=dict(theme='猫扮演对峙人物，最后揭穿身份')
    evidence=[dict(start_s=1,end_s=2,observation='同一参考的实际画面')]
    raw=dict(elements=[dict(index=0,status='partial',quote='猫扮演对峙人物',reason='引用原输出',evidence=evidence),
                       dict(index=1,status='matched',quote='最后揭穿身份',reason='引用原输出',evidence=evidence)])
    assert exam.validate_elements(raw,case,reading)['full_match_rate']==.5
    raw['elements'][0]['quote']='候选没有写出的真人打牌'
    with pytest.raises(ValueError,match='invented_candidate_quote'):
        exam.validate_elements(raw,case,reading)


def test_judge_selects_candidate_ids_and_does_not_blame_candidate_for_bad_labels():
    reading=dict(theme='两人之间亲密而融洽',reference_sha256='a'*64)
    units=exam.candidate_units(reading)
    assert len(units)==1 and units[0]['text']==reading['theme']
    evidence=[dict(start_s=0,end_s=1,observation='亲密靠肩')]
    raw=dict(items=[dict(gold_id='g000',gold_validity='disputed',relation='contradicted',candidate_ids=[],reason='原标签疏离与视频矛盾',evidence=evidence),
                    dict(gold_id='g001',gold_validity='valid',relation='matched',candidate_ids=['u000'],reason='候选原句明确表达',evidence=evidence)])
    case=dict(labels=dict(elements=['疏离','亲密']),reference=dict(duration_s=10))
    compiled=exam.compile_element_ids(raw,reading,2)
    result=exam.validate_elements(compiled,case,reading)
    assert result['full_match_rate']==1 and result['label_verification_coverage']==.5
    assert result['counts']['label_disputed']==1 and compiled['elements'][1]['quote']==reading['theme']
    with pytest.raises(ValueError,match='prefix_inference_status_required'):
        exam.compile_element_ids(raw,reading,2,require_inference=True)
    raw['inference_validity']='plausible'
    assert exam.compile_element_ids(raw,reading,2,require_inference=True)['inference_validity']=='plausible'
    raw['items'][1]['candidate_ids']=['g001']
    with pytest.raises(ValueError,match='candidate_unit_identity'):
        exam.compile_element_ids(raw,reading,2)


def test_forecast_missing_events_remain_in_denominator():
    case=dict(labels=dict(cut_s=3,future_events=['挑衅','揭穿','反应']),query=dict(duration_s=12))
    answer=dict(events=['揭穿','反应'])
    raw=dict(matches=[dict(prediction_index=0,gold_index=1,start_s=5,end_s=6),
                      dict(prediction_index=1,gold_index=2,start_s=9,end_s=10)])
    assert exam.forecast_metrics(raw,answer,case)['event_f1']==pytest.approx(.8)
    assert exam.forecast_metrics(raw,answer,case)['order_accuracy']==.5
    raw['matches'].append(raw['matches'][0])
    with pytest.raises(ValueError,match='duplicate_event_match'):
        exam.forecast_metrics(raw,answer,case)


def test_forecast_ids_distinguish_candidate_and_gold_and_clip_only_real_hidden_evidence():
    case=dict(labels=dict(cut_s=2.000333,future_events=['被围攻']),
              query=dict(asset_id='q',duration_s=12.5))
    answer=dict(events=['继续挑衅','被围攻'])
    packet=dict(mapping=[dict(asset_id='q',packet_start_s=0,packet_end_s=12.533333)])
    raw=dict(matches=[dict(prediction_id='p001',gold_id='g000',start_s=2,end_s=12.53,observation='实际被围攻')])
    compiled=exam.compile_forecast_ids(raw,answer,case,packet)
    assert exam.compile_forecast_ids(raw['matches'],answer,case,packet)==compiled
    from omni_story.library.audio_review import _result
    body={'choices':[{'message':{'content':json.dumps(raw['matches'])}}]}
    response=dict(http_status=200,body_text=json.dumps(body))
    received=_result(response,dict(video_sha256='v',request_sha256='q',response_sha256='r'),cached=True)
    assert received['parsed'] is None
    assert exam.parse_received_review(received,exam.FORECAST_IDS_PROMPT)==raw['matches']
    assert compiled['matches'][0]==dict(prediction_index=1,gold_index=0,start_s=2.000333,end_s=12.5,observation='实际被围攻')
    assert exam.forecast_metrics(compiled,answer,case)['recall']==1
    raw['matches'][0]['prediction_id']='g000'
    with pytest.raises(ValueError,match='event_identity_invalid'):
        exam.compile_forecast_ids(raw,answer,case,packet)
    raw['matches'][0].update(prediction_id='p001',start_s=0,end_s=1)
    with pytest.raises(ValueError,match='no_hidden_evidence'):
        exam.compile_forecast_ids(raw,answer,case,packet)


def test_unknown_paid_source_cannot_be_resubmitted_elsewhere(tmp_path,monkeypatch):
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path))
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE',str(tmp_path/'budget.json'))
    old=tmp_path/'old';write_json(old/'receipt/state.json',dict(status='uncertain'))
    write_json(tmp_path/'production_exam_media_registry.json',
        {'old':dict(directory=str(old),sources=['a'*64],prompt_sha256='old')})
    with pytest.raises(RuntimeError,match='unknown_source_no_replay'):
        exam.paid_review([dict(sha256='a'*64,asset_id='source')],{},'new prompt',{},tmp_path/'different')


def test_story_requires_original_quotes_and_correct_answer_ranges():
    case=dict(id='t1',labels=dict(story_paragraphs=['普通生活','隐藏事实被揭穿','仅同名无反差'],story_target_indices=[1]))
    manifest=dict(cases=[case])
    assert exam.story_metrics(dict(paragraph_ids=['p001'],quotations=['隐藏事实']),manifest,case)['hit_at_1']==1
    assert exam.story_metrics(dict(paragraph_ids=['p002'],quotations=['仅同名']),manifest,case)['hit_at_1']==0
    with pytest.raises(ValueError,match='quote'):
        exam.story_metrics(dict(paragraph_ids=['p001'],quotations=['编造原文']),manifest,case)
    answer=dict(paragraph_ids=['p001'],quotations=['模型改写的原文'])
    assert exam.story_metrics(answer,manifest,case,compile_quotes=True)['hit_at_1']==1
    assert exam.compiled_story_excerpt(answer,manifest)==[dict(paragraph_id='p001',text='隐藏事实被揭穿')]


def test_real_prefix_physically_truncates_video_and_audio(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('server FFmpeg integration')
    source=tmp_path/'full.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=15:d=4',
        '-f','lavfi','-i','sine=frequency=440:duration=4','-threads','1','-c:v','libx264','-c:a','aac',str(source)],check=True)
    asset=dict(path=str(source),sha256=sha256_file(source))
    output=protocol.prefix(asset,1,tmp_path/'prefix')
    from omni_story.library.media import probe_media
    measured=probe_media(output)
    assert measured['duration_s']<=1.1
    assert any(s['codec_type']=='audio' for s in measured['streams'])
    assert protocol.prefix(asset,1,tmp_path/'prefix')==output
    with pytest.raises(ValueError,match='prefix_changed'):
        protocol.prefix(asset,2,tmp_path/'prefix')


def test_core_prefix_does_not_receive_the_full_understanding_or_gold(monkeypatch,tmp_path):
    from omni_story.evaluation import reference_core as core,reference_cli as cli
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path/'receipts'))
    (tmp_path/'receipts').mkdir()
    prefix=tmp_path/'cropped.mp4';prefix.write_bytes(b'prefix-only-media')
    monkeypatch.setattr(protocol,'prefix',lambda *a:prefix)
    case=dict(id='case',evaluation_target='core_meaning_v2',reference=dict(path='full.mp4',sha256='original',asset_id='r',duration_s=20),
        labels=dict(prefix_cut_s=5,prefix_information='ambiguous',prefix_acceptable_themes=['SECRET_GOLD'],elements=['SECRET_GOLD']))
    runtime=dict(configuration=dict(creative_audio=True))
    def collect(selected,manifest,folder):
        assert selected['reference']['path']==str(prefix) and selected['id']=='case_prefix'
        assert manifest['production']['configuration']['reference_context']==core.PREFIX_CONTEXT
        assert 'SECRET_GOLD' not in json.dumps(manifest['production'])
        return dict(manual_correction=False,understanding=dict(theme='有可能需要勇气'),
            evidence_path=str(tmp_path/'subject.json'),understanding_sha256='reading')
    monkeypatch.setattr(cli,'subject',collect)
    raw=dict(items=[dict(gold_id='g000',gold_validity='valid',relation='partial',candidate_ids=['u000'],
        reason='合理推测',evidence=[dict(start_s=1,end_s=2,observation='前缀含犹豫')])],inference_validity='plausible')
    monkeypatch.setattr(exam,'paid_review',lambda *a,**kw:(raw,{}))
    # A real lineage record is required even when transports are mocked.
    folder=tmp_path/'case';write_json(folder/'prefix-media/prefix.json',dict(binding=dict(source_sha256='original',end_s=5),sha256=sha256_file(prefix)))
    result=core.prefix_task(case,dict(production=runtime),folder,tmp_path/'grading')
    assert result['subject_condition']=='reference_prefix_only'
    assert result['metrics']['prefix_information']=='ambiguous'
    assert result['metrics']['inference_validity']=='plausible'
    with pytest.raises(ValueError,match='no_full_answer'):
        protocol.public_question(case,'forecast',dict(theme='SECRET_FULL_ANSWER'),[])


def test_core_prefix_cannot_crop_an_original_unknown_call(monkeypatch,tmp_path):
    from omni_story.evaluation import reference_core as core
    write_json(tmp_path/'subject-status.json',dict(status='blocked_unknown'))
    case=dict(labels=dict(prefix_cut_s=5),reference=dict(sha256='unknown'))
    monkeypatch.setattr(protocol,'prefix',lambda *a:pytest.fail('Must stop before cropping or calling'))
    with pytest.raises(RuntimeError,match='original_source_unknown_no_replay'):
        core.prefix_task(case,{},tmp_path,tmp_path)


def test_prefix_regrade_does_not_collect_an_unavailable_original(monkeypatch,tmp_path):
    from omni_story.evaluation import reference_core as core
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR',str(tmp_path/'receipts'))
    monkeypatch.setattr(protocol,'prefix',lambda *a:pytest.fail('Reuse-only must not create a new subject'))
    case=dict(id='t1',labels={},reference=dict(sha256='original'))
    result=core.prefix_task(case,dict(production=dict(reuse_only=True)),tmp_path/'new',tmp_path/'grading')
    assert result['status']=='blocked_subject' and 'no request sent' in result['reason']


def test_shared_music_does_not_claim_unique_or_correct_meaning():
    from omni_story.evaluation.reference_core import music_metrics
    value=music_metrics(dict(candidates=[dict(case_id='a'),dict(case_id='b')]),'a',1)
    assert value['music_template_hit']==1 and value['unique_template_hit']==0
    assert value['matched_original_core_full_match_rate'] is None


def test_music_regrade_keeps_canonical_query_and_received_artifacts(monkeypatch,tmp_path):
    from omni_story.evaluation import reference_core as core
    output=tmp_path/'new-version';canonical=tmp_path/'original-music'
    case=dict(id='t1',query=dict(path='held-out',duration_s=12),labels=dict(same_mechanism=True))
    own=dict(case_id='t1',subject_path='original-subject',understanding_sha256='original-sha')
    monkeypatch.setattr(core,'_full_subject',lambda *a:own)
    monkeypatch.setattr(core,'verify_tree',lambda *a:None)
    write_json(output/'cases/t1/elements.json',dict(metrics=dict(full_match_rate=1)))
    seen=[]
    def run(command,**kwargs):
        job=json.loads(Path(command[-1]).read_text());seen.append(job)
        assert job['output']==str(canonical)
        write_json(canonical/'answer.json',dict(status='no_match',candidates=[]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(core.subprocess,'run',run)
    manifest=dict(cases=[case],protocol_sha256='new-version',receipts=str(tmp_path/'receipts'),
        production=dict(music_candidate=dict(root='frozen',files={}),music_outputs=dict(t1=str(canonical))))
    core.music_tasks(manifest,output)
    result=json.loads((output/'cases/t1/bgm.json').read_text())
    assert len(seen)==1 and result['status']=='scored_provisional'
    assert result['answer_path']==str(canonical/'answer.json')
    assert not (output/'cases/t1/music-subject').exists()


def test_core_story_scope_isolates_unrelated_templates_and_rejects_unseen_ids():
    a=dict(id='a',evaluation_target='core_meaning_v2',labels=dict(story_paragraphs=['SECRET_OTHER_TEMPLATE'],story_target_indices=[0]))
    b=dict(id='b',evaluation_target='core_meaning_v2',labels=dict(story_paragraphs=['犹豫以后迈步','继续拖延'],story_target_indices=[0]))
    manifest=dict(cases=[a,b])
    visible=protocol.story_inputs(manifest,'b')
    assert visible==[dict(id='p001',text='犹豫以后迈步'),dict(id='p002',text='继续拖延')]
    assert 'SECRET_OTHER_TEMPLATE' not in json.dumps(visible)
    assert exam.story_metrics(dict(paragraph_ids=['p001']),manifest,b,compile_quotes=True)['hit_at_1']==1
    with pytest.raises(ValueError,match='identity_invalid'):
        exam.story_metrics(dict(paragraph_ids=['p000']),manifest,b,compile_quotes=True)


def test_bound_mcp_adapter_dispatches_once_without_an_agent_or_network(tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node runtime required')
    sdk=tmp_path/'package/node_modules/@modelcontextprotocol/sdk/dist/esm/client';sdk.mkdir(parents=True)
    (sdk/'package.json').write_text('{"type":"module"}')
    (sdk/'index.js').write_text('''import fs from 'node:fs';
export class Client {
 async connect(){} async listTools(){return {tools:[{name:'execute'}]};}
 async callTool(value){fs.appendFileSync(process.env.DISPATCH_LOG,JSON.stringify(value)+'\\n');return {isError:false};}
 async close(){}
}''')
    (sdk/'stdio.js').write_text('export class StdioClientTransport {constructor(config){this.config=config;}}')
    record=tmp_path/'dispatch.jsonl'
    import os
    config=dict(mcp=dict(omni=dict(command=['node',str(tmp_path/'opencode_mcp.mjs'),str(tmp_path/'out'),str(tmp_path/'package')],timeout=1000)))
    env=dict(os.environ,OPENCODE_CONFIG_CONTENT=json.dumps(config),OMNI_LIBRARY_OPENCODE_JOB='glm_001_reference',DISPATCH_LOG=str(record))
    runner=Path(__file__).parents[1]/'src/omni_story/evaluation/bound_mcp_runner.mjs'
    completed=subprocess.run([node,str(runner),'run','--agent','vision-job'],env=env,text=True,capture_output=True,timeout=10)
    assert completed.returncode==0,completed.stderr
    assert record.read_text().splitlines()==[json.dumps(dict(name='execute',arguments={}),separators=(',',':'))]
    assert json.loads(completed.stdout)['agent_model_called'] is False


def test_isolated_asr_parent_does_not_import_recognizer_and_preserves_arguments(monkeypatch,tmp_path):
    import sys,builtins
    from omni_story.evaluation import isolated_asr
    real_import=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name=='faster_whisper':pytest.fail('Recognizer dependencies must load only in the child')
        return real_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    monkeypatch.setitem(sys.modules,'faster_whisper',None)
    isolated_asr.install(tmp_path/'cache','/frozen/production')
    audio=tmp_path/'audio.wav';audio.write_bytes(b'actual local fixture')
    captured=[]
    def child(command,**kwargs):
        job=json.loads(Path(command[-2]).read_text());captured.append(job)
        write_json(Path(command[-1]),dict(job=job,segments=[],info=dict(language='zh',language_probability=1)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(isolated_asr.subprocess,'run',child)
    model=sys.modules['faster_whisper'].WhisperModel('frozen-model',device='cpu',compute_type='int8',cpu_threads=2)
    rows,info=model.transcribe(str(audio),word_timestamps=True,vad_filter=True)
    assert list(rows)==[] and info.language=='zh'
    assert captured[0]['constructor_kwargs']==dict(device='cpu',compute_type='int8',cpu_threads=2)
    assert captured[0]['transcribe_kwargs']==dict(word_timestamps=True,vad_filter=True)
