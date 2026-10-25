from copy import deepcopy
import json
import re

import pytest

from omni_story.library.reference_template import (
    single_reading_as_template, template_prompt, validate_template,
)


IDS = ["r0", "r1", "r2"]


def reading():
    return {"viewer_takeaway": "珍惜愿意陪伴自己的人",
        "theme": "用行动表达珍惜", "intended_takeaway": "珍惜彼此的陪伴",
        "value_judgment": {"core_message": "珍惜身边的人", "stance": "抒情",
            "what_is_upheld": "陪伴", "what_is_rejected": "无",
            "evidence": [{"basis": "visible", "note": "两人相视微笑"}]},
        "visible_evidence": [{"observed_fact": "两人相视微笑"}],
        "editing_methods": [{"method": "停留"}],
        "cultural_context": [{"claim": "相聚承载思念", "kind": "social_norm",
            "confidence": "low", "supports": "珍惜陪伴"}], "uncertainties": ["关系尚未确认"]}


def template():
    return {"template_viewer_takeaway": "珍惜愿意陪伴自己的人",
        "template_theme": "用行动表达珍惜", "template_takeaway": "珍惜彼此的陪伴",
        "value_judgment": {"core_message": "珍惜身边的人", "stance": "抒情",
            "what_is_upheld": "陪伴", "what_is_rejected": "无"},
        "common_elements": [{"text": "行动传达关心", "support": ["r0", "r1"], "counter": ["r2"]}],
        "variable_parts": [{"text": "送伞", "seen_in": ["r1"]}],
        "cultural_context": [{"claim": "相聚承载思念", "confidence": "low", "seen_in": ["r0"]}],
        "disagreements": [{"text": "是否应当主动维系关系", "readings": ["r0", "r2"]}],
        "uncertainties": ["关系尚未确认"]}


def assign(value, path, replacement, *, delete=False):
    parts = path.split(".")
    parent = value
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    if delete:
        del parent[parts[-1]]
    else:
        parent[parts[-1]] = replacement


def rejects(value, field, *, reading_ids=IDS):
    with pytest.raises(ValueError, match="^" + re.escape("reference_template:" + field) + "$"):
        validate_template(value, reading_ids=reading_ids)


@pytest.mark.parametrize("ids", [["r0", "r1"], ["first", "second", "third"]])
def test_prompt_preserves_readings_and_has_a_valid_complete_json_example(ids):
    readings = [reading() for _ in ids]
    readings[-1]["theme"] = "相反立场：陪伴也可能形成束缚"
    before = deepcopy(readings)
    prompt = template_prompt(readings, reading_ids=ids)
    data, example = prompt.split("给定readings：\n", 1)[1].split(
        "\n以下仅为JSON格式示例，不能照抄为汇总结论。输出：\n", 1)
    assert json.loads(data) == {"readings": [{"reading_id": rid, "reading": item}
        for rid, item in zip(ids, readings)]}
    value = json.loads(example)
    assert validate_template(value, reading_ids=ids) is value
    assert set(value) == set(template())
    assert readings == before
    for instruction in ("只依据给定 readings", "不引入新观察", "不提供视频", "区分可替换情节",
            "人物、场景、道具、情节归入variable_parts", "多条共同支持", "至少2个不同reading_id",
            "两条参考时允许恰为2个", "反对该要素的来源写入counter", "不强行统一", "写入disagreements",
            "仅沿用reading中已标注的cultural_context", "可合并但保留confidence", "不得提高置信度",
            "confidence不同时分条保留", "保留原有未确认事项", "严格JSON", "所有字符串非空"):
        assert instruction in prompt


@pytest.mark.parametrize("readings,ids,field", [
    ([], [], "reading_ids"), ([{}], ["r0"], "reading_ids"),
    ([{}, {}], ["r0", "r0"], "reading_ids"),
    ([{}, {}], ["r0", " "], "reading_ids"),
    ([{}, {}], ["r0", []], "reading_ids"),
    ([{}], ["r0", "r1"], "readings"),
    ([{}, None], ["r0", "r1"], "readings"),
    (None, ["r0", "r1"], "readings")])
def test_prompt_rejects_unbound_inputs(readings, ids, field):
    with pytest.raises(ValueError, match="^reference_template:" + field + "$"):
        template_prompt(readings, reading_ids=ids)


def test_validation_preserves_value_and_accepts_two_supporters_with_counterevidence():
    value = template()
    before = deepcopy(value)
    assert validate_template(value, reading_ids=IDS) is value
    assert value == before
    value["common_elements"][0]["counter"] = []
    value["disagreements"] = []
    assert validate_template(value, reading_ids=IDS[:2]) is value


@pytest.mark.parametrize("path,field", [
    ("common_elements.0.support", "common_elements.support"),
    ("common_elements.0.counter", "common_elements.counter"),
    ("variable_parts.0.seen_in", "variable_parts.seen_in"),
    ("cultural_context.0.seen_in", "cultural_context.seen_in"),
    ("disagreements.0.readings", "disagreements.readings")])
@pytest.mark.parametrize("invalid", [["r0", "unknown"], ["r0", "r0"], ["r0", {}], "r0", None])
def test_ids_must_be_known_distinct_strings_in_lists(path, field, invalid):
    value = template()
    assign(value, path, invalid)
    rejects(value, field)


@pytest.mark.parametrize("support", [[], ["r0"], ["r0", "r0"], ["r1", "r1", "r1"]])
def test_each_common_element_needs_two_different_supporters(support):
    value = template()
    value["common_elements"].append({"text": "另一共同点", "support": support, "counter": []})
    rejects(value, "common_elements.support")


@pytest.mark.parametrize("single", [None, False])
def test_common_elements_cannot_be_empty_for_multiple_references(single):
    value = template()
    value["common_elements"] = []
    if single is not None:
        value["single_reference"] = single
    rejects(value, "common_elements")


@pytest.mark.parametrize("path,field", [
    ("template_theme", "template_theme"), ("template_takeaway", "template_takeaway"),
    ("value_judgment.core_message", "value_judgment.core_message"),
    ("value_judgment.stance", "value_judgment.stance"),
    ("value_judgment.what_is_upheld", "value_judgment.what_is_upheld"),
    ("value_judgment.what_is_rejected", "value_judgment.what_is_rejected"),
    ("common_elements.0.text", "common_elements.text"),
    ("variable_parts.0.text", "variable_parts.text"),
    ("cultural_context.0.claim", "cultural_context.claim"),
    ("cultural_context.0.confidence", "cultural_context.confidence"),
    ("disagreements.0.text", "disagreements.text")])
@pytest.mark.parametrize("invalid", [None, "", "  ", 0, [], {}])
def test_strings_are_required_and_nonempty(path, field, invalid):
    value = template()
    assign(value, path, invalid)
    rejects(value, field)


@pytest.mark.parametrize("path,field,invalid", [
    ("value_judgment.stance", "value_judgment.stance", "praise"),
    ("value_judgment.stance", "value_judgment.stance", "赞同|批判"),
    ("cultural_context.0.confidence", "cultural_context.confidence", "certain"),
    ("cultural_context.0.confidence", "cultural_context.confidence", "HIGH")])
def test_enums_are_strict(path, field, invalid):
    value = template()
    assign(value, path, invalid)
    rejects(value, field)


@pytest.mark.parametrize("stance", ["赞同", "批判", "讽刺", "抒情", "中立", "其他"])
@pytest.mark.parametrize("confidence", ["high", "medium", "low"])
def test_all_documented_enum_values_are_accepted(stance, confidence):
    value = template()
    value["value_judgment"]["stance"] = stance
    value["cultural_context"][0]["confidence"] = confidence
    assert validate_template(value, reading_ids=IDS) is value


@pytest.mark.parametrize("field", list(template()))
def test_top_level_fields_are_required(field):
    value = template()
    del value[field]
    rejects(value, field)


@pytest.mark.parametrize("field", ["common_elements", "variable_parts", "cultural_context", "disagreements"])
@pytest.mark.parametrize("invalid", [None, {}, "text", [None], [[]]])
def test_collections_require_lists_of_objects(field, invalid):
    value = template()
    value[field] = invalid
    rejects(value, field)


@pytest.mark.parametrize("invalid", [None, {}, "text", [None], [""], ["  "], [{}]])
def test_uncertainties_require_a_list_of_nonempty_strings(invalid):
    value = template()
    value["uncertainties"] = invalid
    rejects(value, "uncertainties")


@pytest.mark.parametrize("invalid", [None, [], "text", 0])
def test_template_requires_an_object(invalid):
    rejects(invalid, "value")


@pytest.mark.parametrize("invalid", [None, [], {}, "r0", ["r0", "r0"], [" "], [[]]])
def test_reading_ids_require_a_nonempty_list_of_unique_nonempty_strings(invalid):
    rejects(template(), "reading_ids", reading_ids=invalid)


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", [], {}])
def test_single_reference_flag_must_be_boolean(invalid):
    value = template()
    value["single_reference"] = invalid
    rejects(value, "single_reference")


def test_single_reading_maps_meaning_culture_and_uncertainties_without_aliasing():
    source = reading()
    before = deepcopy(source)
    value = single_reading_as_template(source)
    assert validate_template(value, reading_ids=["r0"]) is value
    assert set(value) == set(template()) | {"single_reference"}
    assert value["single_reference"] is True and value["common_elements"] == []
    assert value["template_theme"] == source["theme"]
    assert value["template_takeaway"] == source["intended_takeaway"]
    assert value["value_judgment"] == source["value_judgment"]
    assert value["uncertainties"] == source["uncertainties"]
    assert value["cultural_context"] == [{"claim": "相聚承载思念", "confidence": "low", "seen_in": ["r0"]}]
    value["value_judgment"]["evidence"][0]["note"] = "改动"
    value["cultural_context"][0]["claim"] = "改动"
    value["uncertainties"].append("新疑问")
    assert source == before


def test_single_reference_relaxes_only_the_common_elements_minimum():
    value = single_reading_as_template(reading())
    value["common_elements"] = [{"text": "单条观察", "support": ["r0"], "counter": []}]
    rejects(value, "common_elements.support", reading_ids=["r0"])
    value["common_elements"] = []
    value["cultural_context"][0]["seen_in"] = ["unknown"]
    rejects(value, "cultural_context.seen_in", reading_ids=["r0"])


def test_single_reading_accepts_absent_optional_lists():
    source = reading()
    del source["cultural_context"], source["uncertainties"]
    value = single_reading_as_template(source)
    assert value["cultural_context"] == value["uncertainties"] == []
    assert validate_template(value, reading_ids=["r0"]) is value


def test_stance_with_explanation_matches_reference_contract():
    from omni_story.library import reference_template as t
    reading = dict(theme='主旨', intended_takeaway='理解', value_judgment=dict(core_message='揭露伪装',
        stance='批判：揭露贪腐者的自我辩白', what_is_upheld='诚实', what_is_rejected='伪装', evidence=[]),
        cultural_context=[], uncertainties=[])
    value = t.single_reading_as_template(reading)
    assert t.validate_template(value, reading_ids=['r0']) is value or t.validate_template(value, reading_ids=['r0'])
    import pytest
    value['value_judgment']['stance'] = '随便写'
    with pytest.raises(ValueError):
        t.validate_template(value, reading_ids=['r0'])
