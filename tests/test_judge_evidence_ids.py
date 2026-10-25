import pytest
from omni_story.evaluation import reference_exam as exam


def reading():
    return dict(theme='身份落差制造压迫感',intended_takeaway='问哪个道上的施压',reference_sha256='a'*64)


def test_unit_cited_only_under_evidence_is_joined_not_called_invented():
    units=exam.candidate_units(reading())
    assert [u['id'] for u in units]==['u000','u001']
    raw=dict(items=[dict(gold_id='g000',gold_validity='valid',relation='matched',candidate_ids=['u001'],
                         reason='u000与u001都写了施压',evidence=['u000','u001'])],inference_validity='supported')
    case=dict(labels=dict(elements=['利用身份落差制造压迫感']),reference=dict(duration_s=10))
    compiled=exam.compile_element_ids(raw,reading(),1,require_inference=True)
    assert compiled['elements'][0]['candidate_ids']==['u001','u000']
    assert compiled['elements'][0]['quote']==units[1]['text']
    assert exam.validate_elements(compiled,case,reading(),judge_backend='glm_text')['full_match_rate']==1


def test_missing_with_checked_units_only_under_evidence_is_valid():
    raw=dict(items=[dict(gold_id='g000',gold_validity='valid',relation='missing',candidate_ids=[],
                         reason='查过u000未提及',evidence=['u000'])])
    case=dict(labels=dict(elements=['渴望重来']),reference=dict(duration_s=10))
    compiled=exam.compile_element_ids(raw,reading(),1)
    assert exam.validate_elements(compiled,case,reading(),judge_backend='glm_text')['counts']['missing']==1


def test_unknown_evidence_id_still_fails_validation():
    raw=dict(items=[dict(gold_id='g000',gold_validity='valid',relation='matched',candidate_ids=['u000'],
                         reason='引用不存在的段落',evidence=['u000','u099'])])
    case=dict(labels=dict(elements=['利用身份落差制造压迫感']),reference=dict(duration_s=10))
    compiled=exam.compile_element_ids(raw,reading(),1)
    with pytest.raises(ValueError,match='invented_candidate_quote'):
        exam.validate_elements(compiled,case,reading(),judge_backend='glm_text')


def test_judge_supplied_quote_is_still_checked_against_its_own_ids():
    raw=dict(items=[dict(gold_id='g000',gold_validity='valid',relation='matched',candidate_ids=['u001'],
                         quote='候选没写的话',reason='编造',evidence=['u000'])])
    with pytest.raises(ValueError,match='invented_candidate_quote'):
        exam.compile_element_ids(raw,reading(),1)
