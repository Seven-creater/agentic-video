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
