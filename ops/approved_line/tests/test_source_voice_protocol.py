from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import source_voice_review as review
from omni_story.library.character_voice import CharacterVoices,VoiceUnavailable
from omni_story.library.state import LibraryStopped


def test_real_alias_and_late_clean_candidate_reach_independent_listening(tmp_path,monkeypatch):
    from omni_story.library import dialogue_identity_review as identity
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network')
    fixture=json.loads(Path(__file__).with_name('source_actor_alias_fixture.json').read_text())[0]
    raw=fixture['received'];before=deepcopy(raw);calls=[]
    expected=raw['voices'][0]['samples'][-1]
    monkeypatch.setattr(identity,'labelled_audio_batch',lambda *args:('black.mp4',[]))
    def received(*args):
        prompt=args[3];calls.append(prompt)
        if len(calls)==1:return dict(http_status=200,parsed=deepcopy(raw))
        if len(calls)==2:
            candidates=json.loads(prompt.split('\n')[-1])['candidates']
            assert len(candidates)==len(raw['voices'][0]['samples'])
            assert candidates[-1]['start_s']==expected['start_s']
            return dict(http_status=200,parsed=dict(samples=[dict(sample_id=c['sample_id'],
                has_voice=i==len(candidates)-1,is_target_voice=i==len(candidates)-1,
                isolated=True,heard_text='actual final utterance' if i==len(candidates)-1 else '',
                evidence='actual listening evidence') for i,c in enumerate(candidates)],all_accepted_same_voice=True))
        assert len(calls)==3
        return dict(http_status=200,parsed=dict(samples=[dict(clip_id='s1',has_voice=True,
            voice_group='g1',heard_text='actual final utterance',evidence='one acoustic speaker')]))
    monkeypatch.setattr(review,'review',received)
    spans,evidence=review.find_and_audition(SimpleNamespace(shots=[]),CharacterVoices(tmp_path),
        'source.mp4',fixture['appearance'],fixture['duration'])
    assert spans==dict(target=[(expected['start_s'],expected['end_s'])])
    assert evidence['candidate_normalizations'] and evidence['candidate_observation_raw']==raw['voices']
    assert raw==before


def test_enrollment_budget_is_retained_but_does_not_limit_candidate_validation(tmp_path,monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network')
    voice=CharacterVoices(tmp_path)
    rows=[dict(actor='target',samples=[dict(start_s=a,end_s=b,evidence='candidate')
        for a,b in ((0,5),(6,11),(12,16))])]
    assert voice.check_samples(rows,['target'],20)['target']==[(0,5),(6,11)]
    assert voice.check_samples(rows,['target'],20,minimum_s=0,stop_after_s=None)['target']==[(0,5),(6,11),(12,16)]


def test_unknown_and_partial_appearance_aliases_are_not_guessed(tmp_path,monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network')
    rows=[dict(actor='partial',samples=[])]
    normalized,changes=review.normalize_sample_actors(rows,'complete known appearance')
    assert normalized==rows and changes==[]
    with pytest.raises(VoiceUnavailable):CharacterVoices(tmp_path).check_samples(normalized,['target'],10)


def test_explicit_empty_proposals_are_negative_evidence_without_forced_repair(tmp_path,monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network');calls=[]
    monkeypatch.setattr(review,'review',lambda *a:(calls.append(a) or dict(http_status=200,parsed=dict(voices=[]))))
    with pytest.raises(VoiceUnavailable,match='No audible target samples') as caught:
        review.find_and_audition(SimpleNamespace(shots=[]),CharacterVoices(tmp_path),'source.mp4','appearance',10)
    assert not isinstance(caught.value,review.SourceVoiceProtocolError) and len(calls)==1


def test_real_negative_rows_can_have_empty_explanations_without_inventing_evidence():
    value=json.loads(Path(__file__).with_name('source_negative_audio_fixture.json').read_text());before=deepcopy(value)
    clips=[dict(clip_id=r['clip_id']) for r in value['samples']]
    assert review.validate_audio_groups(value,clips)==before
    assert value==before and any(not r['evidence'] for r in value['samples'] if not r['has_voice'])


def test_positive_voice_without_evidence_still_requires_bounded_protocol_repair(tmp_path,monkeypatch):
    clips=[dict(clip_id='s1')]
    bad=dict(samples=[dict(clip_id='s1',has_voice=True,voice_group='g1',heard_text='heard',evidence='')])
    good=deepcopy(bad);good['samples'][0]['evidence']='actual sound'
    replies=iter([bad,good]);prompts=[]
    monkeypatch.setattr(review,'review',lambda *a:(prompts.append(a[3]) or dict(http_status=200,parsed=next(replies))))
    assert review.checked_review(None,'input.mp4',tmp_path,'original prompt',None,
        lambda v:review.validate_audio_groups(v,clips))==good
    assert len(prompts)==2 and prompts[0]=='original prompt' and '结构不合规' in prompts[1]
    assert bad['samples'][0]['evidence']==''


def test_transport_failure_is_not_retried_or_reported_as_actor_shortage(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(review,'review',lambda *a:(calls.append(a) or dict(http_status=503,parsed=None)))
    with pytest.raises(LibraryStopped,match='transport_failed'):
        review.checked_review(None,'input.mp4',tmp_path,'prompt',None,lambda v:v)
    assert len(calls)==1


@pytest.mark.parametrize('protocol',[True,False])
def test_unresolved_protocol_does_not_disqualify_the_performer(tmp_path,monkeypatch,protocol):
    from omni_story.library import story_dialogue,pipeline,joint_dialogue,reference_story
    monkeypatch.setenv('DASHSCOPE_API_KEY','unit-test-no-network')
    monkeypatch.setattr(pipeline,'_material_artifact',lambda *a:{})
    monkeypatch.setattr(reference_story,'required_lines',lambda *a:[dict(role='r')])
    monkeypatch.setattr(joint_dialogue,'speaker_view',lambda *a:True)
    monkeypatch.setattr(CharacterVoices,'source_media',lambda self,source,clip,**kw:
        ('source.mp4',dict(source_sha256='source',start_s=clip['source_start_s'],end_s=clip['source_end_s'])))
    def reject(*args,**kwargs):
        raise (review.SourceVoiceProtocolError('invalid reply') if protocol else VoiceUnavailable('no usable speech'))
    monkeypatch.setattr(review,'find_and_audition',reject)
    story=SimpleNamespace(root=tmp_path,state=SimpleNamespace(),contract={},
        shots=[dict(source_sha256='source',source_start_s=10,source_end_s=17)],
        active_ranges={'w':[(10,17)]},route=SimpleNamespace(sources={'source':dict(sha256='source',duration_s=120)},
            _allowed_record=lambda row:True))
    lock=dict(scenes=[dict(role_bindings={'r':dict(role_id='a',appearance='known actor')})])
    with pytest.raises(LibraryStopped if protocol else VoiceUnavailable) as caught:
        story_dialogue.StoryDialogue.source_voices(story,lock)
    assert (not hasattr(caught.value,'actor_id')) if protocol else caught.value.actor_id=='a'
