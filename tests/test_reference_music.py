import json
import shutil
import subprocess

import pytest

from omni_story import api
from omni_story.library import audio_review


def test_native_audio_payload_has_no_video_or_images(tmp_path,monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','synthetic-key')
    monkeypatch.setattr(api.shutil,'which',lambda name:'/synthetic/curl')
    sent=[]
    def request(*args,**kwargs):
        sent.append(args[4]);return dict(http_status=200,body_text='{}')
    monkeypatch.setattr(api,'curl_json',request)
    path=tmp_path/'sound.mp3';path.write_bytes(b'synthetic-audio')
    runner=api.QwenAPI();runner.request('Return JSON.',audio=path)
    content=sent[0]['messages'][0]['content']
    assert [r['type'] for r in content]==['text','input_audio']
    assert content[1]['input_audio']['format']=='mp3'
    assert content[1]['input_audio']['data'].startswith('data:audio/mpeg;base64,')
    with pytest.raises(ValueError,match='one_media_kind'):
        runner.request('Return JSON.',audio=path,media=path)
    assert len(sent)==1


def test_audio_receipt_reuses_received_and_never_replays_unknown(tmp_path,monkeypatch):
    path=tmp_path/'sound.mp3';path.write_bytes(b'synthetic-audio')
    monkeypatch.setattr(audio_review,'probe_media',lambda p,**kw:dict(duration_s=3,streams=[dict(index=0,codec_type='audio')]))
    class Runner:
        key='synthetic-key';endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
        cfg=dict(model='qwen3.8-omni-flash');calls=0;fail=False
        def request(self,prompt,*,audio):
            assert audio==path.resolve();self.calls+=1
            if self.fail:raise TimeoutError('synthetic-key')
            body={'choices':[{'message':{'content':'{"heard_music":true}'}}]}
            return dict(http_status=200,body_text=json.dumps(body))
    runner=Runner();out=tmp_path/'received'
    first=audio_review.review_audio(path,out,prompt='Return JSON.',api=runner)
    assert audio_review.review_audio(path,out,prompt='Return JSON.',api=runner)['cache_hit']
    assert runner.calls==1 and first['parsed']['heard_music']
    request=json.loads((out/'request.json').read_text())
    assert request['policy']==audio_review.AUDIO_POLICY
    assert request['input_kind']=='audio_only' and request['request_options']['media_kind']=='input_audio'
    runner.fail=True
    for _ in range(2):
        with pytest.raises(RuntimeError,match='unknown_no_replay'):
            audio_review.review_audio(path,tmp_path/'unknown',prompt='Return JSON.',api=runner)
    assert runner.calls==2


def test_music_matching_retrieves_original_meaning_and_rejects_dialogue_shortcut(tmp_path,monkeypatch):
    from omni_story.library import reference_music as music
    source=tmp_path/'reference.mp4';source.write_bytes(b'original-source')
    reading=dict(theme='坚持原则',intended_takeaway='不为小利违背良心')
    index=music.register_reference(dict(path=str(source),sha256=music.sha256_file(source)),reading,tmp_path/'index',production_version='frozen-fixture')
    observed={'samples':[dict(sample_id='m001',spoken_speech_present=False,music_audible=True)],'query_matches':['m000']}
    monkeypatch.setattr(music,'compare_music',lambda *a,**kw:dict(observations=observed))
    query=dict(sha256='held-out-sha')
    result=music.match_reference_music([index],query,tmp_path/'match',tmp_path/'receipts')
    assert result['candidates'][0]['original_understanding']==reading
    observed['samples'][0]['spoken_speech_present']=True
    assert music.match_reference_music([index],query,tmp_path/'match',tmp_path/'receipts')['status']=='ineligible_query_not_music_only'
    with pytest.raises(ValueError,match='must_be_held_out'):
        music.match_reference_music([index],index['asset'],tmp_path/'same',tmp_path/'receipts')


def test_received_global_clocks_are_not_used_as_source_local_music_windows():
    from omni_story.library import reference_music as music
    mapping=[dict(sample_id='m000',source_duration_s=8),dict(sample_id='m001',source_duration_s=5)]
    raw=dict(samples=[dict(sample_id='m000',music_audible=True,spoken_speech_present=True,
        music_only_ranges=[dict(start_s=6,end_s=8)]),
        dict(sample_id='m001',music_audible=True,spoken_speech_present=False,
        music_only_ranges=[dict(start_s=8,end_s=10),dict(start_s=True,end_s=2),dict(start_s=0,end_s=3)])],
        groups=[dict(sample_ids=['m000','m001'])],query_matches=['m000'])
    result=music.validate_music_observations(raw,mapping,'m001')
    assert len(result['rejected_music_only_ranges'])==2
    assert result['validated_music_only_ranges']['m001']==[dict(start_s=0,end_s=3)]
    assert raw['samples'][1]['music_only_ranges'][0]==dict(start_s=8,end_s=10)
    raw['query_matches']=[]
    with pytest.raises(ValueError,match='disagree_with_groups'):
        music.validate_music_observations(raw,mapping,'m001')


def test_real_audio_only_packet_preserves_sound_without_picture(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('server FFmpeg integration')
    from omni_story.library import reference_music as music
    source=tmp_path/'source.mp3'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','libmp3lame',str(source)],check=True)
    with pytest.raises(ValueError,match='no_video'):
        music.probe_media(source)
    target,packet=music.audio_packet([dict(path=str(source),sha256=music.sha256_file(source))],tmp_path/'packet')
    measured=music.probe_media(target,require_video=False)
    assert measured['video_stream_index'] is None
    assert any(s['codec_type']=='audio' for s in measured['streams'])
    assert packet['audio_scope']=='original_mixed_audio_no_images_not_a_separated_music_stem'


def test_received_audio_pricing_preserves_other_unknown_reservations(tmp_path):
    from omni_story.evaluation.budget import liability
    from omni_story.library.state import write_json
    root=tmp_path/'runs/case/audio'
    write_json(root/'received/state.json',dict(policy=audio_review.AUDIO_POLICY,status='received'))
    write_json(root/'received/request.json',dict(configuration=dict(model='qwen3.8-omni-flash'),
        endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'))
    write_json(root/'received/response.json',dict(http_status=200,
        body_text=json.dumps(dict(usage=dict(prompt_tokens=4183,completion_tokens=1235)))))
    write_json(root/'unknown/state.json',dict(policy=audio_review.AUDIO_POLICY,status='uncertain'))
    budget=dict(experiment_id='fixture',requests=[dict(run_id='fixture/case/audio')]*2,
        cost_control=dict(per_operation_reserve_cny=1,experiment_root=str(tmp_path)))
    result=liability(budget)
    assert result['upper_cny']=='1.006681'
    assert result['received_priced_operations']==1 and result['unresolved_reserved_operations']==1
