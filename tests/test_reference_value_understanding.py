from copy import deepcopy
import json

import pytest

from omni_story.library import clean_chain, contracts, prompts


SHA = 'a' * 64
DURATION = 2


def reference():
    return {'viewer_takeaway': '珍惜愿意陪伴自己的人',
        'reference_sha256': SHA, 'theme': '用行动表达珍惜', 'intended_takeaway': '珍惜彼此的陪伴',
        'visible_evidence': [{'start_s': 0, 'end_s': 1, 'observed_fact': '两人相视微笑', 'supports': '彼此关心'}],
        'editing_methods': [{'method': '停留', 'function': '让观众感受陪伴',
            'visual_evidence': '两人同框', 'start_s': 0, 'end_s': 1}], 'uncertainties': [],
        'value_judgment': {'core_message': '珍惜彼此的陪伴', 'stance': '抒情',
            'what_is_upheld': '相伴的人', 'what_is_rejected': '无',
            'evidence': [{'start_s': 0, 'end_s': 1, 'basis': 'visible', 'note': '微笑传达亲近'}]},
        'cultural_context': []}


def test_contract_example_is_json_and_valid_with_original_fields():
    contract = json.loads(json.dumps(contracts.creative_reference_contract(), ensure_ascii=False))
    assert contract['required_fields'] == ['viewer_takeaway', 'value_judgment', 'cultural_context']
    value = {**reference(), **contract['format_example']}
    before = deepcopy(value)
    assert contracts.validate_reference(value, SHA, DURATION) is value
    assert value == before


@pytest.mark.parametrize('duration', [0.25, DURATION])
@pytest.mark.parametrize('template', [prompts.reference_prompt, clean_chain.ROUGH_PROMPTS.reference_prompt])
def test_reference_prompt_has_a_valid_complete_json_example(template, duration):
    prompt = template(SHA, duration)
    value = json.loads(prompt.split('输出：\n', 1)[1])
    assert contracts.validate_reference(value, SHA, duration) is value
    assert set(reference()) <= set(value)
    assert value['theme'] == '主旨' and value['intended_takeaway'] == '观众应该理解什么'
    assert value['editing_methods'][0]['method'] == '剪法'


@pytest.mark.parametrize('template', [prompts.reference_prompt, clean_chain.ROUGH_PROMPTS.reference_prompt])
def test_reference_and_repair_prompts_allow_labeled_cultural_knowledge(template):
    prompt = template(SHA, DURATION)
    for instruction in ('表达了什么价值/态度', 'theme继续保留可迁移主旨', 'editing_methods继续写剪法',
            '三者不要互相替代', '不要求所有视频都讲道德', '情感、讽刺、幽默也算',
            '参考理解允许使用', '作品/角色背景、梗、谐音、社会语境', '严格区分可见事实、ASR与外部常识',
            '必须逐条写进cultural_context并标明confidence', '不得冒充画面或台词事实',
            '没有使用外部文化常识时cultural_context为[]', 'cultural引用cultural_context中的常识',
            '未提供ASR或音频测量时，不声称听到了台词、音乐或节拍',
            contracts.creative_reference_contract()['repair_instruction']):
        assert instruction in prompt
    assert clean_chain.ROUGH_PROMPTS.reference_prompt(SHA, DURATION) == prompts.reference_prompt(SHA, DURATION)


@pytest.mark.parametrize('field', ['value_judgment', 'cultural_context'])
def test_new_fields_are_required(field):
    value = reference()
    del value[field]
    with pytest.raises(ValueError, match='^reference/' + field + ':'):
        contracts.validate_reference(value, SHA, DURATION, require_value_fields=True)


@pytest.mark.parametrize('stance', ['赞同', '批判', '讽刺', '抒情', '中立', '其他',
    '赞同：肯定陪伴', '讽刺（嘲讽冷漠）', '其他(幽默)'])
def test_valid_stances_and_optional_explanations(stance):
    value = reference()
    value['value_judgment']['stance'] = stance
    assert contracts.validate_reference(value, SHA, DURATION) is value


@pytest.mark.parametrize('basis,kind,confidence', [('visible', 'work_or_character', 'high'),
    ('asr', 'meme', 'medium'), ('cultural', 'pun', 'low'), ('cultural', 'social_norm', 'high'),
    ('cultural', 'other', 'medium')])
def test_labeled_evidence_and_cultural_enums(basis, kind, confidence):
    value = reference()
    value['cultural_context'] = [{'claim': '表白谐音的文化解释', 'kind': kind,
        'confidence': confidence, 'supports': '支持珍惜对方的情感解释'}]
    value['value_judgment']['evidence'] = [{'start_s': 0, 'end_s': DURATION,
        'basis': basis, 'note': '参考中触发该解释的区间；外部知识不等同于实测台词'}]
    assert contracts.validate_reference(value, SHA, DURATION) is value


def test_cultural_context_can_be_empty_and_downstream_fields_are_preserved():
    value = reference()
    value.update(strategy={'adaptation_mode': 'reinterpret', 'speech_mode': 'none', 'timing_driver': 'action'},
        speech_form='无语音', visual_form={'description': '两人同框'}, extra={'received': True})
    before = deepcopy(value)
    assert contracts.validate_reference(value, SHA, DURATION) is value
    assert value == before and value['cultural_context'] == []
    assert contracts.reference_strategy(value) == value['strategy']


def test_editing_reference_still_accepts_the_reading_without_rewriting_it():
    reading = reference()
    before = deepcopy(reading)
    editing = {'reference_sha256': SHA, 'methods': [{'method_id': 'method_0',
        'reference_method_index': 0, 'form': '停留', 'function': '感受陪伴',
        'verification_rule': '同框画面持续可见', 'source_start_s': 0, 'source_end_s': 1,
        'evidence_type': 'model_estimate', 'requires_audio': False,
        'material_requirements': ['两人同框'], 'uncertainties': []}], 'uncertainties': []}
    assert contracts.validate_editing_reference(editing, SHA, DURATION, reading) is editing
    assert reading == before


@pytest.mark.parametrize('field', ['core_message', 'stance', 'what_is_upheld', 'what_is_rejected'])
@pytest.mark.parametrize('invalid', [None, '', '  ', 0, [], {}])
def test_value_strings_are_required_and_nonempty(field, invalid):
    value = reference()
    value['value_judgment'][field] = invalid
    with pytest.raises(ValueError, match='^reference/value_judgment/' + field + ':text_required$'):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('field', ['claim', 'kind', 'confidence', 'supports'])
@pytest.mark.parametrize('invalid', [None, '', '  ', 0, [], {}])
def test_cultural_strings_are_required_and_nonempty(field, invalid):
    value = reference()
    value['cultural_context'] = deepcopy(contracts.creative_reference_contract()['format_example']['cultural_context'])
    value['cultural_context'][0][field] = invalid
    with pytest.raises(ValueError, match='^reference/cultural_context/' + field + ':text_required$'):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('collection,field,invalid', [
    ('cultural_context', 'kind', 'character'),
    ('cultural_context', 'confidence', 'certain')])
def test_invalid_enums(collection, field, invalid):
    value = {**reference(), **deepcopy(contracts.creative_reference_contract()['format_example'])}
    item = value[collection][0] if collection == 'cultural_context' else value[collection]
    item[field] = invalid
    with pytest.raises(ValueError, match='^reference/' + collection + '/' + field + ':invalid_value$'):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('stance', ['幽默：展现谐音梗带来的社交趣味', 'praise', '赞同|批判', '讽刺但赞同'])
def test_free_stance_is_kept_under_other(stance):
    value = reference()
    value['value_judgment']['stance'] = stance
    contracts.validate_reference(value, SHA, DURATION)
    assert value['value_judgment']['stance'] == '其他：' + stance


@pytest.mark.parametrize('basis', [None, '', 'audio', 'culture', [], {}])
def test_invalid_evidence_basis(basis):
    value = reference()
    value['value_judgment']['evidence'][0]['basis'] = basis
    with pytest.raises(ValueError, match='^reference/value_judgment/evidence/basis:invalid_value$'):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('start,end', [(-1, 1), (0, 3), (0, DURATION + 0.01), (1, 1), (2, 1), (False, 1),
    (0, True), (None, 1), (0, '1'), (float('nan'), 1), (0, float('inf'))])
def test_invalid_value_evidence_times(start, end):
    value = reference()
    value['value_judgment']['evidence'][0].update(start_s=start, end_s=end)
    with pytest.raises(ValueError, match='^reference/value_judgment/evidence'):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('field,invalid', [('value_judgment', None), ('value_judgment', []),
    ('cultural_context', None), ('cultural_context', {}), ('cultural_context', [None])])
def test_invalid_top_level_structures(field, invalid):
    value = reference()
    value[field] = invalid
    with pytest.raises(ValueError, match='^reference/' + field):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('evidence', [None, [], {}, [None], [{'start_s': 0, 'end_s': 1, 'basis': 'visible'}],
    [{'start_s': 0, 'end_s': 1, 'basis': 'visible', 'note': '  '}]])
def test_value_evidence_requires_objects_and_nonempty_notes(evidence):
    value = reference()
    value['value_judgment']['evidence'] = evidence
    with pytest.raises(ValueError, match='^reference/value_judgment/evidence'):
        contracts.validate_reference(value, SHA, DURATION)


@pytest.mark.parametrize('field,invalid,error', [('reference_sha256', 'changed', 'reference:sha_changed'),
    ('theme', '', 'reference/theme:text_required'), ('intended_takeaway', None, 'reference/intended_takeaway:text_required'),
    ('strategy', {'speech_mode': 'invalid'}, 'reference:invalid_speech_mode'),
    ('visible_evidence', [], 'reference/visible_evidence:list_required'),
    ('visible_evidence', [{'start_s': 0, 'end_s': 3}], 'reference/evidence:invalid_or_out_of_bounds'),
    ('visible_evidence', [{'start_s': 0, 'end_s': 1, 'observed_fact': ''}], 'reference/observed_fact:text_required'),
    ('visible_evidence', [{'start_s': 0, 'end_s': 1, 'observed_fact': '画面'}], 'reference/supports:text_required'),
    ('editing_methods', [], 'reference/editing_methods:list_required'),
    ('editing_methods', [{'method': ''}], 'reference/method:text_required'),
    ('uncertainties', [''], 'reference/uncertainties:text_required')])
def test_original_validation_errors_remain_unchanged(field, invalid, error):
    value = reference()
    value[field] = invalid
    with pytest.raises(ValueError) as caught:
        contracts.validate_reference(value, SHA, DURATION)
    assert str(caught.value) == error


def test_original_evidence_time_tolerance_is_unchanged():
    value = reference()
    value['visible_evidence'][0]['end_s'] = DURATION + 0.0001
    value['editing_methods'][0]['end_s'] = DURATION + 0.0001
    assert contracts.validate_reference(value, SHA, DURATION) is value
