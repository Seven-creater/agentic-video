from copy import deepcopy
from pathlib import Path

import pytest

from omni_story.library import dialogue_identity_review as identity
from omni_story.library import scene_coverage as coverage


def context():
    return dict(reference=dict(theme='theme', intended_takeaway='goal', strategy=dict(
        speech_mode='dialogue', adaptation_mode='reinterpret', timing_driver='speech')),
        editing_policy='joint_av_v1', segments=[], scene_cast=[dict(scene_id='s', role_bindings={
            'questioner': dict(role_id='x', actor='person-a', appearance='striped coat'),
            'responder': dict(role_id='y', actor='person-b', appearance='round glasses')})],
        audio_draft=[dict(line_id='l1', scene_id='s', role='questioner', start_s=0, end_s=2,
                         text='SECRET SCRIPT', visual_anchor=True),
                     dict(line_id='l2', scene_id='s', role='responder', start_s=2, end_s=5,
                         text='SECRET ANSWER', visual_anchor=True),
                     dict(line_id='l3', scene_id='s', role='questioner', start_s=5, end_s=8)])


def observation():
    return dict(observations=[
        dict(line_id='l1', heard_text='question', voice_id='v1', speaker_id='p1',
             visible_speaker_ids=['p1'], basis='visible_speech', anchor_line_id=None, evidence='coat speaks'),
        dict(line_id='l2', heard_text='answer', voice_id='v2', speaker_id='p2',
             visible_speaker_ids=['p2'], basis='visible_speech', anchor_line_id=None, evidence='glasses speak'),
        dict(line_id='l3', heard_text='follow up', voice_id='v1', speaker_id='p1',
             visible_speaker_ids=[], basis='established_voice', anchor_line_id='l1', evidence='same voice, quiet listener')],
        contradictions=[], limitations=[])


def verify(value, ctx=None):
    return identity.verify_observations(value, *identity.identity_contract(ctx or context()))


def test_observer_cannot_see_role_assignment_script_or_plan():
    ctx = context()
    ctx['segments'] = [dict(planned_use='SECRET PLAN', visible_roles=['responder'])]
    prompt = identity.observation_prompt(*identity.identity_contract(ctx))
    for hidden in ['SECRET', 'questioner', 'responder', 'role_bindings', 'planned_use', 'visual_anchor']:
        assert hidden not in prompt
    assert 'striped coat' in prompt
    assert 'start_s' in prompt


def test_valid_speaker_and_established_voice_over_listener_pass():
    assert verify(observation())['passed']


def test_silent_visible_bystander_gets_own_identity_without_story_role():
    ctx=context();ctx['visible_actor_catalog']=[dict(actor_id='person-c',appearance='plain coat, round ears')]
    people,expected=identity.identity_contract(ctx)
    assert len(people)==3 and people[-1]['appearances']==['plain coat, round ears']
    assert {r['person_id'] for r in expected}=={'p1','p2'}
    prompt=identity.observation_prompt(people,expected)
    assert 'plain coat, round ears' in prompt and 'questioner' not in prompt


def test_later_conflicting_mouth_cannot_rebind_first_concealed_voice_or_hide_conflict():
    ctx=context();ctx['audio_draft'][0]['allows_occluded_intro']=True
    ctx['audio_draft'][2]['role']='questioner'
    catalog,expected=identity.identity_contract(ctx)
    sound=dict(observations=[dict(line_id=f'l{i}',voice_id=f'v{v}',heard_text='words',evidence='timbre')
        for i,v in enumerate((1,2,1),1)])
    picture=dict(observations=[
        dict(line_id='l1',visibility='none',visible_speaker_ids=[],opening_visible_actor_ids=['p1'],opening_concealed_mouth_ids=['p1'],evidence='only p1 concealed face'),
        dict(line_id='l2',visibility='speaker',visible_speaker_ids=['p2'],evidence='p2 mouth'),
        dict(line_id='l3',visibility='speaker',visible_speaker_ids=['p2'],evidence='p2 mouth under first voice')])
    observed=identity.combine_identity_observations(sound,picture,catalog,expected)
    assert observed['observations'][0]['speaker_id']=='p1'
    result=identity.verify_observations(observed,catalog,expected)
    assert result['checks'][0]['passed'] and not result['checks'][2]['passed']
    assert not result['passed']


def test_concealed_face_intro_is_separate_from_visible_lips_and_cannot_rebind_a_known_voice():
    ctx=context();ctx['audio_draft'][1]['allows_occluded_intro']=True
    catalog,expected=identity.identity_contract(ctx)
    sound=dict(observations=[dict(line_id=f'l{i}',voice_id=f'v{v}',heard_text='actual words',evidence='timbre')
        for i,v in enumerate((1,2,1),1)])
    picture=dict(observations=[
        dict(line_id='l1',visibility='speaker',visible_speaker_ids=['p1'],opening_visible_actor_ids=['p1'],opening_concealed_mouth_ids=[],evidence='p1 mouth moves'),
        dict(line_id='l2',visibility='none',visible_speaker_ids=[],opening_visible_actor_ids=['p2'],opening_concealed_mouth_ids=['p2'],evidence='only p2 head visible, real mouth concealed'),
        dict(line_id='l3',visibility='none',visible_speaker_ids=[],opening_visible_actor_ids=['p2'],opening_concealed_mouth_ids=['p2'],evidence='p2 silently listens')])
    result=identity.combine_identity_observations(sound,picture,catalog,expected)
    assert result['observations'][1]['basis']=='occluded_intro'
    assert result['observations'][1]['visible_speaker_ids']==[]
    assert result['observations'][2]['speaker_id']=='p1'
    assert identity.verify_observations(result,catalog,expected)['passed']
    expected[1]['allows_occluded_intro']=False
    assert not identity.verify_observations(result,catalog,expected)['passed']
    expected[1]['allows_occluded_intro']=True
    picture['observations'][1]['opening_visible_actor_ids']=['p1','p2']
    result=identity.combine_identity_observations(sound,picture,catalog,expected)
    assert not identity.verify_observations(result,catalog,expected)['passed']


def test_known_audio_voice_does_not_hide_an_unclear_unoccluded_picture():
    catalog,expected=identity.identity_contract(context())
    sound=dict(observations=[dict(line_id=f'l{i}',voice_id=f'v{v}',heard_text='words',evidence='timbre')
        for i,v in enumerate((1,2,1),1)])
    picture=dict(observations=[dict(line_id='l1',visibility='speaker',visible_speaker_ids=['p1'],evidence='mouth moves'),
        dict(line_id='l2',visibility='speaker',visible_speaker_ids=['p2'],evidence='mouth moves'),
        dict(line_id='l3',visibility='unclear',visible_speaker_ids=[],evidence='other person mouth is blurry')])
    observed=identity.combine_identity_observations(sound,picture,catalog,expected)
    assert observed['observations'][2]['basis']=='unclear'
    assert not identity.verify_observations(observed,catalog,expected)['passed']


def test_stylized_intro_requires_explicit_animation_permission_and_one_identifiable_person():
    ctx=context();ctx['audio_draft'][1]['allows_stylized_intro']=True
    catalog,expected=identity.identity_contract(ctx)
    sound=dict(observations=[dict(line_id=f'l{i}',voice_id=f'v{v}',heard_text='words',evidence='heard timbre')
        for i,v in enumerate((1,2,1),1)])
    picture=dict(observations=[dict(line_id='l1',visibility='speaker',visible_speaker_ids=['p1'],evidence='mouth motion'),
        dict(line_id='l2',visibility='none',visible_speaker_ids=[],opening_visible_actor_ids=['p2'],
            opening_stylized_actor_ids=['p2'],evidence='one clear animated character'),
        dict(line_id='l3',visibility='none',visible_speaker_ids=[],evidence='silent listener')])
    observed=identity.combine_identity_observations(sound,picture,catalog,expected)
    assert observed['observations'][1]['basis']=='stylized_intro'
    assert observed['observations'][1]['visible_speaker_ids']==[]
    assert identity.verify_observations(observed,catalog,expected)['passed']
    expected[1]['allows_stylized_intro']=False
    assert not identity.verify_observations(observed,catalog,expected)['passed']


def test_audio_group_links_an_offscreen_line_to_the_visible_speaker():
    catalog, expected = identity.identity_contract(context())
    sound = dict(observations=[dict(line_id=f'l{i}',voice_id=f'v{voice}',
                                   heard_text='words',evidence='timbre')
                               for i,voice in enumerate((1,2,1),1)])
    picture = dict(observations=[
        dict(line_id='l1',visibility='speaker',visible_speaker_ids=['p1'],evidence='coat speaks'),
        dict(line_id='l2',visibility='speaker',visible_speaker_ids=['p2'],evidence='glasses speak'),
        dict(line_id='l3',visibility='none',visible_speaker_ids=[],evidence='object close-up'),
    ])
    def review():
        observed = identity.combine_identity_observations(sound,picture,catalog,expected)
        return identity.verify_observations(observed,catalog,expected)

    assert review()['passed']
    sound['observations'][2]['voice_id'] = 'v3'
    assert not review()['passed']
    sound['observations'][2]['voice_id'] = 'v1'
    picture['observations'][2].update(visibility='speaker',visible_speaker_ids=['p2'])
    assert not review()['passed']


def test_pairwise_audio_requires_a_consistent_speaker_partition():
    ids = {'s1':'l1','s2':'l2','s3':'l3'}
    sound = dict(clips=[dict(id=key,heard_text='words') for key in ids],pairs=[
        dict(a='s1',b='s2',verdict='same',evidence='same timbre'),
        dict(a='s1',b='s3',verdict='different',evidence='distinct timbre'),
        dict(a='s2',b='s3',verdict='uncertain',evidence='short audio'),
    ])
    result = identity.group_pairwise_voices(sound,ids)
    assert [r['voice_id'] for r in result['observations']] == ['s1','s1','s3']
    sound['pairs'][2]['verdict'] = 'same'
    with pytest.raises(identity.LibraryStopped,match='inconsistent_audio_comparisons'):
        identity.group_pairwise_voices(sound,ids)


def test_voice_review_compares_every_line_to_heard_exemplars_without_expected_cast(tmp_path,monkeypatch):
    import json
    from threading import Lock
    ids={f's{i}':f'line{i}' for i in range(18)}
    clips=[dict(clip_id=k,start_s=i,end_s=i+1) for i,k in enumerate(ids)]
    requested=[]
    mutex=Lock()
    monkeypatch.setattr(identity,'labelled_audio_batch',lambda media,folder,clips,**transport:(media,clips))
    def reviewer(media,folder,*,prompt):
        payload=json.loads(prompt.split('\n',1)[1])
        assert [c['clip_id'] for c in payload['clips']]==['s1','s2']
        a,b=folder.name.split('_')[-2:]
        with mutex:requested.append(frozenset((a,b)))
        return dict(http_status=200,parsed=dict(first_text='first words',second_text='second words',
            verdict='same' if int(a[1:])%3==int(b[1:])%3 else 'different',evidence='heard resonance'))
    result=identity.review_voice_pairs(tmp_path/'audio.mp4',tmp_path,clips,ids,reviewer)
    assert len(requested)==len(set(requested))==33
    assert set().union(*requested)==set(ids)
    assert len({r['voice_id'] for r in result['observations']})==3
    def incomplete(*args,**kwargs):
        response=reviewer(*args,**kwargs)
        response['parsed'].pop('verdict')
        return response
    with pytest.raises(identity.LibraryStopped,match='pair_incomplete'):
        identity.review_voice_pairs(tmp_path/'audio.mp4',tmp_path,clips,ids,incomplete)


def test_labelled_audio_uses_exact_samples_and_remaps_nonconsecutive_clip_ids(tmp_path,monkeypatch):
    import json
    import struct
    import wave
    source=tmp_path/'original.mp4'
    source.write_bytes(b'source-bound')
    rate=1000
    samples=[100]*rate+[200]*rate+[300]*rate
    def ffmpeg(command,**kwargs):
        path=Path(command[-1])
        if path.suffix=='.wav':
            with wave.open(str(path),'wb') as audio:
                audio.setparams((1,2,rate,0,'NONE','not compressed'))
                audio.writeframes(struct.pack('<3000h',*samples))
        else:
            assert "text='s17'" in command[command.index('-vf')+1]
            path.write_bytes(b'encoded-labelled-video')
    monkeypatch.setattr(identity.subprocess,'run',ffmpeg)
    movie,clips=identity.labelled_audio_batch(source,tmp_path/'batch',[
        dict(clip_id='s2',start_s=1,end_s=2),dict(clip_id='s17',start_s=2,end_s=3)])
    assert clips==[dict(clip_id='s2',start_s=.4,end_s=1.4),dict(clip_id='s17',start_s=1.8,end_s=2.8)]
    with wave.open(str(movie.parent/'labelled_audio.wav'),'rb') as audio:
        actual=struct.unpack('<3200h',audio.readframes(audio.getnframes()))
    assert actual==tuple([0]*400+[200]*1000+[0]*400+[300]*1000+[0]*400)
    saved=json.loads((movie.parent/'audio_mapping.json').read_text())
    assert saved['clips'][1]['source_start_sample']==2000
    again=identity.labelled_audio_batch(source,tmp_path/'batch',[
        dict(clip_id='s2',start_s=1,end_s=2),dict(clip_id='s17',start_s=2,end_s=3)])
    assert again==(movie,clips)
    with pytest.raises(identity.LibraryStopped,match='binding_changed'):
        identity.labelled_audio_batch(source,tmp_path/'batch',[
            dict(clip_id='s2',start_s=0,end_s=1),dict(clip_id='s17',start_s=2,end_s=3)])


def test_uncertain_unheard_pair_is_retained_as_unverified_not_a_schema_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(identity,'labelled_audio_batch',lambda media,folder,clips,**transport:(media,clips))
    def reviewer(*args,**kwargs):
        return dict(http_status=200,parsed=dict(first_text='heard first',second_text='',
            verdict='uncertain',evidence='could not separate the second short utterance'))
    result=identity.review_voice_pairs(tmp_path/'audio.mp4',tmp_path,
        [dict(clip_id=f's{i}',start_s=i,end_s=i+1) for i in (1,2)],{'s1':'l1','s2':'l2'},reviewer)
    assert [r['voice_id'] for r in result['observations']]==[None,None]
    assert result['observations'][1]['heard_text']==''


@pytest.mark.parametrize('change', ['swap', 'unknown', 'no_anchor', 'different_mouth', 'same_voice',
                                  'missing', 'duplicate', 'contradiction', 'invalid_id'])
def test_false_positive_patterns_are_rejected(change):
    value = observation()
    rows = value['observations']
    if change == 'swap':
        rows[0]['speaker_id'], rows[1]['speaker_id'] = 'p2', 'p1'
    elif change == 'unknown':
        rows[0].update(basis='unclear', speaker_id=None)
    elif change == 'no_anchor':
        rows[2]['anchor_line_id'] = 'l3'
    elif change == 'different_mouth':
        rows[2]['visible_speaker_ids'] = ['p2']
    elif change == 'same_voice':
        rows[1]['voice_id'] = 'v1'
    elif change == 'missing':
        rows.pop()
    elif change == 'duplicate':
        rows[2] = deepcopy(rows[0])
    elif change == 'contradiction':
        value['contradictions'] = ['appearance and sound disagree']
    else:
        rows[0]['speaker_id'] = ['p1']
    value['passed'] = True  # A model's overall approval has no authority here.
    assert not verify(value)['passed']


def test_voice_only_supporting_character_is_not_forced_on_screen():
    ctx = context()
    ctx['scene_cast'][0]['role_bindings']['questioner']['voice_only'] = True
    for line in ctx['audio_draft']:
        if line['role'] == 'questioner':
            line.update(reference_offscreen=True, visual_anchor=False)
    value = observation()
    # Only one visible person remains in the neutral roster, so its ID is p1.
    value['observations'][1].update(speaker_id='p1', visible_speaker_ids=['p1'])
    for row in (value['observations'][0], value['observations'][2]):
        row.update(speaker_id=None, visible_speaker_ids=[], basis='offscreen', anchor_line_id=None)
    assert verify(value, ctx)['passed']


def test_overall_approval_cannot_override_blind_observation(tmp_path, monkeypatch):
    from omni_story.library import visual_audio
    monkeypatch.setattr(visual_audio, 'probe_media', lambda p: dict(duration_s=8))
    media = tmp_path/'actual.mp4'
    media.write_bytes(b'test media')
    blind = observation()
    blind['observations'][0]['speaker_id'] = 'p2'
    calls = []
    def reviewer(media, folder, *, prompt):
        calls.append(prompt)
        if folder.name.startswith('av_identity_'):
            return dict(http_status=200, parsed=blind)
        return dict(http_status=200, parsed=dict(apparent_story='fine', theme_alignment='matches',
            speech_clear=True, repair_at='none', visual_notes=['fine'], missing_core_meaning=[],
            issues=[], limitations=[], dialogue_execution_ok=True, speech_checks=[
                dict(line_id=f'l{i}', matched=True, evidence='approved') for i in range(1, 4)]))
    result = visual_audio.review_final_video(media, tmp_path/'review', context(), av_reviewer=reviewer)
    assert len(calls) == 2
    assert result['repair_at'] == 'selection'
    assert not result['dialogue_execution_ok']
    assert not visual_audio.content_ready(result, context())


def test_identity_transport_failure_keeps_independent_story_audit_and_blocks_pass(tmp_path,monkeypatch):
    from omni_story.library import visual_audio
    monkeypatch.setattr(visual_audio,'probe_media',lambda p:dict(duration_s=8))
    monkeypatch.setattr(identity,'review_identities',lambda *a:(_ for _ in ()).throw(
        identity.LibraryStopped('dialogue_identity_audio_batch_incomplete')))
    media=tmp_path/'actual.mp4'
    media.write_bytes(b'test media')
    def reviewer(media,folder,*,prompt):
        return dict(http_status=200,parsed=dict(apparent_story='heard story',theme_alignment='matches',
            speech_clear=True,repair_at='none',visual_notes=['observed picture'],missing_core_meaning=[],
            issues=[],limitations=[],dialogue_execution_ok=True,speech_checks=[
                dict(line_id=f'l{i}',matched=True,evidence='actual speech') for i in range(1,4)]))
    result=visual_audio.review_final_video(media,tmp_path/'review',context(),av_reviewer=reviewer)
    assert result['apparent_story']=='heard story'
    assert not result['identity_verification']['complete']
    assert result['picture_audio_coherence']=='unverifiable'
    assert not visual_audio.content_ready(result,context())


@pytest.mark.parametrize('long_role,short_role', [('interviewer', 'guest'), ('child', 'parent'), ('甲', '乙')])
def test_capacity_cannot_swap_selected_narrative_identities(long_role, short_role):
    def shot(rid, seconds):
        return dict(role_id=rid, source_sha256='video', source_start_s=0, source_end_s=seconds)
    scene = dict(scene_id='s', roles=[long_role, short_role], reference_lines=[
        dict(role=long_role, text='话'*100), dict(role=short_role, text='话'*30)],
        speech_slots=[shot('A', 3), shot('B', 20)],
        role_bindings={long_role: dict(role_id='A'), short_role: dict(role_id='B')},
        cast_constraints={long_role: ['A'], short_role: ['B']})
    actors = {'A': dict(actor='person-a'), 'B': dict(actor='person-b')}
    assert coverage.cast_for_dialogue(scene, actors, {}) == {long_role: 'A', short_role: 'B'}
    assert coverage.cast_for_story([scene], {'s': actors}) == {'s': {long_role: 'A', short_role: 'B'}}
    scene['cast_constraints'][long_role] = []
    assert coverage.cast_for_story([scene], {'s': actors}) == {}


def test_shared_identity_uses_scene_local_ids_without_changing_actor():
    def scene(sid, rid):
        return dict(scene_id=sid, roles=['lead'], reference_lines=[dict(role='lead', text='dialogue')],
            speech_slots=[dict(role_id=rid, source_sha256=sid, source_start_s=0, source_end_s=4)],
            cast_constraints={'lead': [rid]})
    assert coverage.cast_for_story([scene('s1', 'A'), scene('s2', 'X')], {
        's1': {'A': dict(actor='same actor')}, 's2': {'X': dict(actor='same actor')}}) == {
            's1': {'lead': 'A'}, 's2': {'lead': 'X'}}
