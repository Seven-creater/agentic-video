from types import SimpleNamespace
import json
import pytest
from omni_story.library.autonomous_material import Discovery
from omni_story.library.story_dialogue import StoryDialogue
from omni_story.library import reference_story


def test_provisional_lock_does_not_mutate_planner_cast():
    story=Discovery.__new__(Discovery)
    story.cast={'participant':'actor_a'}
    story.actors={'actor_a':dict(bindings=[dict(window_id='window')],appearance='neutral')}
    story.windows={'window':dict(window_id='window',source_sha256='sha',source_start_s=0,source_end_s=10)}
    story.selection=dict(scene_windows={'scene':['window']},continuity_plan='continuous')
    story.shots=[]
    story.route=SimpleNamespace(scenes=[dict(scene_id='scene',roles=['participant'],line_ids=['line'])],
        lines=[dict(line_id='line',role='participant')],duration=10,edit_max_s=10)
    _,lock=story.lock()
    lock['cast']['offscreen']='voice_only_identity'
    assert story.cast=={'participant':'actor_a'}
    _,again=story.lock()
    assert again['cast']==story.cast


def test_model_omitted_role_is_removed_from_every_active_role_list(tmp_path):
    story=StoryDialogue.__new__(StoryDialogue)
    lines=[dict(line_id='a',role='sender',text='main dialogue'*20),
        dict(line_id='b',role='receiver',text='main answer'*20),
        dict(line_id='c',role='cameo',text='hello')]
    scenes=[dict(scene_id='scene',line_ids=['a','b','c'],roles=['sender','receiver'],voice_only_roles=['cameo'])]
    story.route=SimpleNamespace(lines=lines,scenes=scenes,config={})
    story.contract=reference_story.make_contract({},scenes,lines,visual_policy='continuous_dialogue_adaptation_v1')
    story.requirements_planned=False;story.browser_result={'candidates':[]}
    story.root=tmp_path/'material';story.root.mkdir();story.state=SimpleNamespace(output=tmp_path)
    answer=dict(optional_roles=['cameo'],omission_reason='secondary greeting is unrelated to main dialogue',
        scenes=[dict(scene_id='scene',relationship='sender addresses receiver',dramatic_progression='question and answer',setting_type='conversation')])
    story.call=lambda name,prompt,validator:validator(answer)
    story.retrieve()
    scene=story.route.scenes[0]
    assert scene['roles']==['sender','receiver'] and scene['voice_only_roles']==[]
    assert scene['line_ids']==['a','b']
    assert [r['line_id'] for r in reference_story.required_lines(story.contract)]==['a','b']
    assert [r['line_id'] for r in story.contract['lines']]==['a','b','c']


def test_planner_gets_explicit_active_ids_and_keeps_immutable_original_evidence(tmp_path):
    story=StoryDialogue.__new__(StoryDialogue)
    lines=[dict(line_id=key,role='lead' if key!='secondary' else 'cameo',text='words')
        for key in ['primary_a','secondary','primary_b']]
    scenes=[dict(scene_id='scene',line_ids=[r['line_id'] for r in lines])]
    original=reference_story.make_contract({},scenes,lines,visual_policy='continuous_dialogue_adaptation_v1')
    story.contract=reference_story.with_dialogue_scope(original,['secondary'],'unrelated greeting')
    story.root=tmp_path
    lock=dict(scenes=[dict(scene_id='scene',line_ids=['primary_a','primary_b'],role_bindings={},coverage_shots=[])])
    def received(name,prompt,validator):
        context=json.loads(prompt.split('\n',1)[1])
        assert context['required_reference_line_ids']==['primary_a','primary_b']
        assert len(context['reference_contract']['lines'])==3
        value=dict(premise='same group talks',substitutions=[],beats=[dict(reference_line_id=k,
            dialogue_function='function',adapted_content='adaptation') for k in ['primary_a','secondary','primary_b']])
        with pytest.raises(ValueError,match='primary_a.*primary_b'):validator(value)
        value['beats']=[b for b in value['beats'] if b['reference_line_id']!='secondary']
        return validator(value)
    story.call=received
    story.plan_adaptation(lock)
    assert [r['reference_line_id'] for r in lock['scenes'][0]['adaptation_plan']['beats']]==['primary_a','primary_b']
