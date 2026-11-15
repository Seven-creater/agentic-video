from types import SimpleNamespace
import pytest
from omni_story.library.character_voice import CharacterVoices,VoiceUnavailable
from omni_story.library import source_voice_review as review
from omni_story.library import dialogue_identity_review as identity


@pytest.mark.parametrize('anchor_mode',['verified','listener','conflicting'])
def test_reaction_closeup_cannot_override_verified_covisible_speaker(tmp_path,monkeypatch,anchor_mode):
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network')
    monkeypatch.setattr(identity,'labelled_audio_batch',lambda *args:('black.mp4',[]))
    intervals=[(0,2),(3,5),(6,8)]
    answers=iter([
        dict(voices=[dict(actor='target',samples=[dict(start_s=a,end_s=b,evidence='candidate') for a,b in intervals])]),
        dict(samples=[dict(sample_id='s'+str(i),has_voice=True,is_target_voice=True,isolated=True,
            heard_text='heard',evidence='AV proposed target') for i in range(1,4)],all_accepted_same_voice=True),
        dict(samples=[dict(clip_id='s'+str(i),has_voice=True,voice_group=group,
            heard_text='heard',evidence='independent acoustic grouping')
            for i,group in enumerate(['g1','g2','g1'],1)])])
    monkeypatch.setattr(review,'review',lambda *args:dict(http_status=200,parsed=next(answers)))
    shots=[dict(source_sha256='source',source_start_s=100,source_end_s=102,
        visible=['target','other'],speaking=['target'] if anchor_mode!='listener' else ['other'],
        speaking_verification=dict(basis='synchronous_gesture')),
        dict(source_sha256='source',source_start_s=103,source_end_s=105,
        visible=['target'],speaking=['target'] if anchor_mode=='conflicting' else [],
        speaking_verification=dict(basis='mouth_motion' if anchor_mode=='conflicting' else 'none'))]
    args=(SimpleNamespace(shots=shots),CharacterVoices(tmp_path),'source.mp4','neutral appearance',10)
    kwargs=dict(actor_id='target',source_binding=dict(source_sha256='source',start_s=100))
    if anchor_mode=='verified':
        spans,_=review.find_and_audition(*args,**kwargs)
        assert spans=={'target':[(0,2),(6,8)]}
    else:
        with pytest.raises(VoiceUnavailable,match='Insufficient isolated'):
            review.find_and_audition(*args,**kwargs)


@pytest.mark.parametrize('pair_verdict',['same','different'])
def test_missing_batch_intervals_are_reheard_in_local_pairs(tmp_path,monkeypatch,pair_verdict):
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network')
    monkeypatch.setattr(identity,'labelled_audio_batch',lambda *args:('black.mp4',[]))
    intervals=[(0,2),(3,5),(6,8)]
    answers=iter([
        dict(voices=[dict(actor='target',samples=[dict(start_s=a,end_s=b,evidence='candidate') for a,b in intervals])]),
        dict(samples=[dict(sample_id='s'+str(i),has_voice=True,is_target_voice=True,isolated=True,
            heard_text='heard',evidence='AV proposed target') for i in range(1,4)],all_accepted_same_voice=True),
        dict(samples=[dict(clip_id='s'+str(i),has_voice=i==1,voice_group='g1' if i==1 else None,
            heard_text='entire batch under first ID' if i==1 else '',evidence='batch observation') for i in range(1,4)]),
        dict(first_text='first',second_text='second',verdict='same',evidence='same acoustic speaker'),
        dict(first_text='first',second_text='third',verdict=pair_verdict,evidence='independent local pair')])
    prompts=[]
    def received(*args):
        prompts.append(args[3]);return dict(http_status=200,parsed=next(answers))
    monkeypatch.setattr(review,'review',received)
    shots=[dict(source_sha256='source',source_start_s=a+100,source_end_s=b+100,
        visible=['target','other'],speaking=['target'],speaking_verification=dict(basis='mouth_motion'))
        for a,b in (intervals[0],intervals[2])]
    args=(SimpleNamespace(shots=shots),CharacterVoices(tmp_path),'source.mp4','neutral appearance',10)
    kwargs=dict(actor_id='target',source_binding=dict(source_sha256='source',start_s=100))
    if pair_verdict=='same':
        spans,evidence=review.find_and_audition(*args,**kwargs)
        assert spans=={'target':intervals}
        assert len(evidence['blind_audio_pair_recovery']['observations'])==3
    else:
        with pytest.raises(VoiceUnavailable,match='Insufficient isolated'):
            review.find_and_audition(*args,**kwargs)
    assert len(prompts)==5
