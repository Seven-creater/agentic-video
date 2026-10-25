import pytest
from omni_story.evaluation import reference_exam as exam
from omni_story.evaluation import reference_protocol as p

FORM='用模仿和反差的方式演绎经典深情告白'


def case():
    return dict(id='t_x',name='24_大话西游告白',labels=dict(elements=['后悔','痛苦','重来','一万年']))


def test_added_element_gets_its_own_group_and_keeps_originals():
    c=case()
    p.apply_confirmations([c],dict(added_elements={'24_大话西游告白':[dict(text=FORM,group='form')]}))
    assert c['labels']['elements']==['后悔','痛苦','重来','一万年',FORM]
    assert c['labels']['element_groups']==['content']*4+['form']


@pytest.mark.parametrize('bad',[dict(text='',group='form'),dict(text=FORM,group='content'),dict(text=FORM)])
def test_invalid_added_element_is_rejected(bad):
    with pytest.raises(ValueError,match='added_element_invalid'):
        p.apply_confirmations([case()],dict(added_elements={'24_大话西游告白':[bad]}))


def test_other_cases_are_untouched():
    c=dict(case(),name='01_不烧心')
    p.apply_confirmations([c],dict(added_elements={'24_大话西游告白':[dict(text=FORM,group='form')]}))
    assert 'element_groups' not in c['labels'] and len(c['labels']['elements'])==4


def test_groups_are_scored_separately_and_headline_is_content():
    reading=dict(theme='深情告白的搞笑模仿',reference_sha256='a'*64)
    c=case();p.apply_confirmations([c],dict(added_elements={'24_大话西游告白':[dict(text=FORM,group='form')]}))
    c['reference']=dict(duration_s=10)
    items=[dict(gold_id=f'g{i:03d}',gold_validity='valid',relation=r,candidate_ids=[] if r=='missing' else ['u000'],
                reason='r',evidence=[] if r=='missing' else ['u000'])
           for i,r in enumerate(['matched','partial','missing','partial','matched'])]
    compiled=exam.compile_element_ids(dict(items=items),reading,5)
    result=exam.validate_elements(compiled,c,reading,judge_backend='glm_text')
    assert result['full_match_rate']==.25 and result['group_scores']['form']['full_match_rate']==1
    assert result['group_scores']['content']['counts']['partial']==2


def test_no_groups_keeps_old_result_shape():
    reading=dict(theme='主旨',reference_sha256='a'*64)
    c=dict(labels=dict(elements=['主旨']),reference=dict(duration_s=10))
    raw=exam.compile_element_ids(dict(items=[dict(gold_id='g000',gold_validity='valid',relation='matched',
        candidate_ids=['u000'],reason='r',evidence=['u000'])]),reading,1)
    result=exam.validate_elements(raw,c,reading,judge_backend='glm_text')
    assert result['full_match_rate']==1 and 'group_scores' not in result


def test_prefix_gold_keeps_groups_aligned_with_hidden_elements():
    from omni_story.evaluation.reference_core import _prefix_gold
    c=case();p.apply_confirmations([c],dict(added_elements={'24_大话西游告白':[dict(text=FORM,group='form')]}))
    gold=_prefix_gold(c,dict(name='before_last_element',hidden_element_indices=[2,3]))
    assert gold['labels']['elements']==['重来','一万年'] and gold['labels']['element_groups']==['content','content']
    gold=_prefix_gold(c,dict(name='before_last_element',hidden_element_indices=[3,4]))
    assert gold['labels']['element_groups']==['content','form']
