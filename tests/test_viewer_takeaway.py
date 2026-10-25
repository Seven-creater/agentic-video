from copy import deepcopy
import json

import pytest

from omni_story.library import clean_chain, contracts, prompts
from omni_story.library.reference_template import (
    single_reading_as_template, template_prompt, validate_template,
)


SHA = 'a' * 64
DURATION = 2
IDS = ['r0', 'r1']


def reading():
    return {'viewer_takeaway': '珍惜愿意陪伴自己的人', 'reference_sha256': SHA,
        'theme': '用行动表达珍惜', 'intended_takeaway': '珍惜彼此的陪伴',
        'visible_evidence': [{'start_s': 0, 'end_s': 0.25, 'observed_fact': '两人相视微笑',
            'supports': '彼此关心'}],
        'editing_methods': [{'method': '停留', 'function': '感受陪伴', 'visual_evidence': '两人同框',
            'start_s': 0, 'end_s': 0.25}], 'uncertainties': [],
        'value_judgment': {'core_message': '珍惜彼此的陪伴', 'stance': '抒情',
            'what_is_upheld': '相伴的人', 'what_is_rejected': '无',
            'evidence': [{'start_s': 0, 'end_s': 0.25, 'basis': 'visible', 'note': '微笑传达亲近'}]},
        'cultural_context': []}


def template():
    return {'template_viewer_takeaway': '珍惜愿意陪伴自己的人', 'template_theme': '用行动表达珍惜',
        'template_takeaway': '珍惜彼此的陪伴',
        'value_judgment': {'core_message': '珍惜彼此的陪伴', 'stance': '抒情',
            'what_is_upheld': '相伴的人', 'what_is_rejected': '无'},
        'common_elements': [{'text': '行动传达关心', 'support': IDS[:], 'counter': []}],
        'variable_parts': [], 'cultural_context': [], 'disagreements': [], 'uncertainties': []}


@pytest.mark.parametrize('make_prompt', [prompts.reference_prompt, clean_chain.ROUGH_PROMPTS.reference_prompt])
@pytest.mark.parametrize('duration', [0.25, DURATION])
def test_reference_prompt_starts_with_viewer_question_and_has_valid_json(make_prompt, duration):
    prompt = make_prompt(SHA, duration)
    assert prompt.index('普通观众') < prompt.index('你是参考驱动电影素材库剪辑系统')
    for rule in ('viewer_takeaway', '一两句话', '这个视频想让观众明白什么', '只说内容和主旨',
            '不讲剪辑手法', '不需要列证据', '可结合常识', '影视作品、网络梗和社会常识',
            '唯一允许推断的字段', '先写viewer_takeaway，再写其他字段',
            'theme、intended_takeaway、value_judgment等应与它一致', '不得互相矛盾',
            '其余字段的事实/推测区分规则保持不变', '严格区分可见事实、ASR与外部常识',
            'value_judgment.evidence至少一条'):
        assert rule in prompt
    value = json.loads(prompt.split('输出：\n', 1)[1])
    assert next(iter(value)) == 'viewer_takeaway'
    assert contracts.validate_reference(value, SHA, duration, require_value_fields=True) is value


def test_reference_contract_and_repair_require_viewer_takeaway():
    contract = json.loads(json.dumps(contracts.creative_reference_contract(), ensure_ascii=False))
    assert contract['required_fields'] == ['viewer_takeaway', 'value_judgment', 'cultural_context']
    assert next(iter(contract['format_example'])) == 'viewer_takeaway'
    value = {**reading(), **contract['format_example']}
    assert contracts.validate_reference(value, SHA, DURATION, require_value_fields=True) is value
    repair = contract['repair_instruction']
    assert '补齐viewer_takeaway（非空字符串）' in repair
    assert '先写viewer_takeaway' in repair
    assert '不得编造画面、台词或时间证据' in repair
    assert repair in prompts.reference_prompt(SHA, DURATION)


def test_fresh_reference_requires_viewer_takeaway():
    value = reading()
    del value['viewer_takeaway']
    with pytest.raises(ValueError, match='^reference/viewer_takeaway:text_required$'):
        contracts.validate_reference(value, SHA, DURATION, require_value_fields=True)


@pytest.mark.parametrize('legacy_value_fields', [False, True])
def test_legacy_reference_allows_absent_viewer_takeaway_without_mutation(legacy_value_fields):
    value = reading()
    del value['viewer_takeaway']
    if legacy_value_fields:
        del value['value_judgment'], value['cultural_context']
    before = deepcopy(value)
    assert contracts.validate_reference(value, SHA, DURATION) is value
    assert value == before


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('invalid', [None, '', ' \n\t', 0, False, [], {}])
def test_present_viewer_takeaway_must_be_nonempty_text(strict, invalid):
    value = reading()
    value['viewer_takeaway'] = invalid
    with pytest.raises(ValueError, match='^reference/viewer_takeaway:text_required$'):
        contracts.validate_reference(value, SHA, DURATION, require_value_fields=strict)


@pytest.mark.parametrize('strict', [False, True])
def test_valid_viewer_takeaway_preserves_the_reading(strict):
    value = reading()
    before = deepcopy(value)
    assert contracts.validate_reference(value, SHA, DURATION, require_value_fields=strict) is value
    assert value == before


def test_template_prompt_summarizes_viewer_takeaways_and_has_valid_json():
    readings = [reading(), reading()]
    readings[1]['viewer_takeaway'] = '陪伴需要用行动表达'
    before = deepcopy(readings)
    prompt = template_prompt(readings, reading_ids=IDS)
    for rule in ('先读各条reading的viewer_takeaway', '普通观众', '一两句话', '共同想让观众明白什么',
            'template_viewer_takeaway', '不讲剪辑手法', '不需要列证据', '不得互相矛盾',
            '只依据给定 readings', '不引入新观察', '不强行统一'):
        assert rule in prompt
    data, example = prompt.split('给定readings：\n', 1)[1].split(
        '\n以下仅为JSON格式示例，不能照抄为汇总结论。输出：\n', 1)
    assert json.loads(data) == {'readings': [{'reading_id': rid, 'reading': item}
        for rid, item in zip(IDS, readings)]}
    value = json.loads(example)
    assert next(iter(value)) == 'template_viewer_takeaway'
    assert validate_template(value, reading_ids=IDS) is value
    assert readings == before


def test_template_requires_viewer_takeaway():
    value = template()
    del value['template_viewer_takeaway']
    with pytest.raises(ValueError, match='^reference_template:template_viewer_takeaway$'):
        validate_template(value, reading_ids=IDS)


@pytest.mark.parametrize('invalid', [None, '', ' \n\t', 0, False, [], {}])
def test_template_viewer_takeaway_must_be_nonempty_text(invalid):
    value = template()
    value['template_viewer_takeaway'] = invalid
    with pytest.raises(ValueError, match='^reference_template:template_viewer_takeaway$'):
        validate_template(value, reading_ids=IDS)


def test_valid_template_preserves_viewer_takeaway():
    value = template()
    before = deepcopy(value)
    assert validate_template(value, reading_ids=IDS) is value
    assert value == before


@pytest.mark.parametrize('has_viewer_takeaway', [False, True])
def test_single_reading_maps_viewer_takeaway_or_falls_back_to_theme(has_viewer_takeaway):
    value = reading()
    if not has_viewer_takeaway:
        del value['viewer_takeaway']
    before = deepcopy(value)
    result = single_reading_as_template(value)
    assert result['template_viewer_takeaway'] == value.get('viewer_takeaway', value['theme'])
    assert result['template_theme'] == value['theme']
    assert result['template_takeaway'] == value['intended_takeaway']
    assert validate_template(result, reading_ids=['r0']) is result
    assert value == before


@pytest.mark.parametrize('invalid', [None, '', ' \n\t', 0, False, [], {}])
def test_single_reading_does_not_replace_invalid_viewer_takeaway_with_theme(invalid):
    value = reading()
    value['viewer_takeaway'] = invalid
    with pytest.raises(ValueError, match='^reference_template:template_viewer_takeaway$'):
        single_reading_as_template(value)
