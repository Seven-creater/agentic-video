from copy import deepcopy

import pytest

from omni_story.library import clean_chain, contracts, prompts


SHA = 'a' * 64
DURATION = 2


def legacy_reference():
    return {'reference_sha256': SHA, 'theme': '用行动表达珍惜', 'intended_takeaway': '珍惜彼此的陪伴',
        'visible_evidence': [{'start_s': 0, 'end_s': 1, 'observed_fact': '两人相视微笑', 'supports': '彼此关心'}],
        'editing_methods': [{'method': '停留', 'function': '让观众感受陪伴',
            'visual_evidence': '两人同框', 'start_s': 0, 'end_s': 1}], 'uncertainties': []}


def fresh_reference():
    value = legacy_reference()
    value.update(value_judgment={'core_message': '珍惜彼此的陪伴', 'stance': '抒情',
        'what_is_upheld': '相伴的人', 'what_is_rejected': '无',
        'evidence': [{'start_s': 0, 'end_s': 1, 'basis': 'visible', 'note': '微笑传达亲近'}]},
        cultural_context=[])
    return value


@pytest.mark.parametrize('mutate', [lambda v: None, lambda v: v.pop('value_judgment'),
    lambda v: v.pop('cultural_context')])
def test_readings_missing_value_fields_pass_by_default(mutate):
    value = fresh_reference()
    mutate(value)
    before = deepcopy(value)
    assert contracts.validate_reference(value, SHA, DURATION) is value
    assert value == before


def test_legacy_reading_still_supports_editing_reference_validation():
    editing = {'reference_sha256': SHA, 'methods': [{'method_id': 'method_0',
        'reference_method_index': 0, 'form': '停留', 'function': '感受陪伴',
        'verification_rule': '同框画面持续可见', 'source_start_s': 0, 'source_end_s': 1,
        'evidence_type': 'model_estimate', 'requires_audio': False,
        'material_requirements': ['两人同框'], 'uncertainties': []}], 'uncertainties': []}
    assert contracts.validate_editing_reference(editing, SHA, DURATION, legacy_reference()) is editing


@pytest.mark.parametrize('field', ['value_judgment', 'cultural_context'])
def test_strict_mode_rejects_missing_fields(field):
    reading = fresh_reference()
    reading.pop(field)
    with pytest.raises(ValueError, match='^reference/' + field + ':'):
        contracts.validate_reference(reading, SHA, DURATION, require_value_fields=True)


def test_strict_mode_rejects_a_fully_legacy_reading():
    with pytest.raises(ValueError, match='^reference/value_judgment:'):
        contracts.validate_reference(legacy_reference(), SHA, DURATION, require_value_fields=True)


@pytest.mark.parametrize('corrupt', [
    lambda v: v['value_judgment'].update(core_message=''),
    lambda v: v['value_judgment'].update(evidence=[]),
    lambda v: v['value_judgment']['evidence'][0].update(basis='culture'),
    lambda v: v['value_judgment']['evidence'][0].update(end_s=float('inf')),
    lambda v: v.update(cultural_context=[{'claim': '表白谐音的文化解释', 'kind': 'character',
        'confidence': 'high', 'supports': '支持情感解释'}]),
    lambda v: v.update(cultural_context=[{'claim': '表白谐音的文化解释', 'kind': 'pun',
        'confidence': 'certain', 'supports': '支持情感解释'}])])
@pytest.mark.parametrize('require_value_fields', [False, True])
def test_present_but_invalid_fields_fail_in_both_modes(corrupt, require_value_fields):
    value = fresh_reference()
    corrupt(value)
    with pytest.raises(ValueError):
        contracts.validate_reference(value, SHA, DURATION, require_value_fields=require_value_fields)


def test_value_evidence_shares_the_visible_evidence_normalization():
    for delta in (0.0001, 0.0005, 1.5):
        value = fresh_reference()
        value['visible_evidence'][0]['end_s'] = DURATION + delta
        value['value_judgment']['evidence'][0]['end_s'] = DURATION + delta
        assert contracts.validate_reference(value, SHA, DURATION) is value
        assert value['visible_evidence'][0]['end_s'] == DURATION
        assert value['value_judgment']['evidence'][0]['end_s'] == DURATION
        assert len(value['normalization']) == 2


@pytest.mark.parametrize('template', [prompts.reference_prompt, clean_chain.ROUGH_PROMPTS.reference_prompt])
def test_prompt_requires_at_least_one_value_evidence(template):
    assert 'value_judgment.evidence至少一条' in template(SHA, DURATION)
