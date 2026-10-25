from omni_story.evaluation.exam_bank import story_levels
from omni_story.evaluation import reference_protocol as p

SRC={'p0':dict(case_id='a',index=0),'p1':dict(case_id='a',index=1),'p2':dict(case_id='a',index=2),
     'p3':dict(case_id='b',index=0),'p4':dict(case_id='b',index=1)}


def test_same_story_non_target_is_counted_apart_from_other_stories():
    r=story_levels(['p0','p1','p3'],None,SRC,'a',{1})
    assert r['story_hit']==1 and r['picked_target']==1 and r['picked_same_story_non_target']==1
    assert r['picked_other_story']==1 and r['same_story_share']==2/3 and r['story_basis']=='paragraph_ids'


def test_story_choice_uses_story_paragraph_ids_when_given():
    r=story_levels(['p3'],['p3','p4','p0'],SRC,'a',{1})
    assert r['story_hit']==0 and r['chosen_story_case_id']=='b' and r['story_basis']=='story_paragraph_ids'


def test_tie_prefers_nothing_but_counts():
    r=story_levels(['p0','p3'],None,SRC,'a',{0})
    assert r['story_hit']==1 and r['picked_other_story']==1


def test_one_story_first_prompt_only_when_flagged():
    case=dict(evaluation_target='core_meaning_v2')
    old=p.public_question(case,'story',{},[])
    new=p.public_question(dict(case,story_prompt='one_story_first_v1'),'story',{},[])
    assert 'story_paragraph_ids' not in old['response_contract']
    assert 'story_paragraph_ids' in new['response_contract'] and '第一步' in new['question']
