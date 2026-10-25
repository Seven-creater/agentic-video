import pytest
from omni_story.evaluation.multi_reference import normalize_prefix_answer


def test_forecast_spread_over_own_keys_is_collected():
    answer=dict(prediction_type='价值或结局推测',confidence='medium',prediction_summary='当众清算第三者',
                predicted_development=[dict(phase='斥骂升级',speculation='点明伤害')],
                value_ending_prediction=dict(stance='批判',expected_ending_value='谴责破坏家庭'),
                fact_basis=dict(facts_from_prefix=['闯入宿舍']),uncertainties=['后续未知'])
    out=normalize_prefix_answer(answer)
    assert '当众清算第三者' in out['prediction'] and '谴责破坏家庭' in out['prediction']
    assert '闯入宿舍' not in out['prediction'] and out['uncertainties']==['后续未知']


@pytest.mark.parametrize('answer',[dict(uncertainties=['x']),dict(prediction=''),[]])
def test_answer_without_any_forecast_text_still_fails(answer):
    with pytest.raises(ValueError,match='prefix_answer_invalid'):
        normalize_prefix_answer(answer)
