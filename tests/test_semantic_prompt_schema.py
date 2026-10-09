"""Real forward templates obey unchanged fact contracts without story answers."""
from copy import deepcopy
import json

import pytest

from omni_story.library import semantic_audit as audit, semantic_prompts


SOURCE_SHA = "a" * 64
PROXY_SHA = "b" * 64
VIDEO_SHA = "c" * 64


def source_inputs(duration=4):
    segment = {"segment_id": "seg_fixture", "source_id": "source_fixture",
        "source_in_s": 10, "source_out_s": 10 + duration,
        "slot_id": "HIDDEN_SLOT_ANSWER", "caption": {"text": "HIDDEN_CAPTION_ANSWER"},
        "visual_claims": [{"description": "HIDDEN_DESIRED_ACTION"}]}
    source = {"source_id": segment["source_id"], "sha256": SOURCE_SHA,
              "title": "HIDDEN_FILM_KNOWLEDGE"}
    proxy = {"source_id": segment["source_id"], "source_sha256": SOURCE_SHA,
        "source_start_s": segment["source_in_s"], "source_end_s": segment["source_out_s"],
        "duration_s": duration, "sha256": PROXY_SHA, "goal": "HIDDEN_TARGET_THEME"}
    return segment, source, proxy


def source_template(duration=4):
    segment, source, proxy = source_inputs(duration)
    prompt = semantic_prompts.explicit_slice_observation_prompt(segment, source, proxy)
    value = json.loads(prompt.split("metadata：", 1)[1])
    return prompt, value, segment, source, proxy


def blind_template(duration=4):
    prompt = semantic_prompts.blind_prompt(duration, VIDEO_SHA)
    return prompt, json.loads(prompt.split("完整JSON模板：", 1)[1])


@pytest.mark.parametrize("duration", [.05, 1, 4])
def test_actual_forward_source_template_strictly_validates_without_answers(duration):
    segment, source, proxy = source_inputs(duration)
    before = deepcopy((segment, source, proxy))
    prompt = semantic_prompts.explicit_slice_observation_prompt(segment, source, proxy)
    value = json.loads(prompt.split("metadata：", 1)[1])
    assert audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy) is value
    assert (segment, source, proxy) == before
    assert set(value) == {"protocol", "segment_id", "source_id", "source_sha256", "source_in_s",
        "source_out_s", "proxy_sha256", "observed_duration_s", "characters", "evidence", "uncertainties"}
    assert "string[]" in prompt and '[{"text":"身份无法确认"}]' in prompt
    for sentinel in ("HIDDEN_SLOT_ANSWER", "HIDDEN_CAPTION_ANSWER", "HIDDEN_DESIRED_ACTION",
                     "HIDDEN_FILM_KNOWLEDGE", "HIDDEN_TARGET_THEME"):
        assert sentinel not in prompt


def test_forward_source_typed_inference_has_its_own_fields_and_direct_basis():
    prompt, value, segment, _, proxy = source_template()
    inference = {**deepcopy(value["evidence"][0]), "evidence_id": "e2", "kind": "inference",
                 "description": "仅据已记录画面提出的低置信度推断。", "basis_evidence_ids": ["e1"]}
    value["evidence"].append(inference)
    value["uncertainties"] = ["该推断不能确认人物的真实身份。"]
    assert audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy) is value
    assert "inference也必须完整包含上述七个字段" in prompt
    assert "不得用根inference/inferences正文替代" in prompt


@pytest.mark.parametrize("field", ["evidence_id", "local_start_s", "description", "character_ids",
                                    "basis_evidence_ids"])
def test_source_inference_missing_required_field_is_not_normalized(field):
    _, value, segment, _, proxy = source_template()
    inference = {**deepcopy(value["evidence"][0]), "evidence_id": "e2", "kind": "inference",
                 "basis_evidence_ids": ["e1"]}
    inference.pop(field)
    value["evidence"].append(inference)
    before = deepcopy(value)
    with pytest.raises(ValueError):
        audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy)
    assert value == before


@pytest.mark.parametrize("uncertainties", [[{"description": "未知"}], [{"text": "未知"}]])
def test_both_received_object_uncertainty_shapes_still_fail(uncertainties):
    _, value, segment, _, proxy = source_template()
    value["uncertainties"] = uncertainties
    before = deepcopy(value)
    with pytest.raises(ValueError, match="semantic/uncertainties:text_required"):
        audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy)
    assert value == before


@pytest.mark.parametrize("uncertainties", [None, "未知", {"text": "未知"}])
def test_source_uncertainties_must_be_a_list(uncertainties):
    _, value, segment, _, proxy = source_template()
    value["uncertainties"] = uncertainties
    with pytest.raises(ValueError, match="semantic/uncertainties:list_required"):
        audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy)


@pytest.mark.parametrize("wrapper", ["metadata", "observation", "data"])
def test_source_root_wrapper_still_fails_without_unwrapping(wrapper):
    _, value, segment, _, proxy = source_template()
    wrapped = {wrapper: value}
    before = deepcopy(wrapped)
    with pytest.raises(ValueError, match="semantic:protocol"):
        audit.validate_segment_observation(wrapped, segment, SOURCE_SHA, proxy)
    assert wrapped == before


def test_root_inference_cannot_replace_source_typed_evidence():
    _, value, segment, _, proxy = source_template()
    value["inference"] = value.pop("evidence")
    with pytest.raises(ValueError, match="semantic/evidence:list_required"):
        audit.validate_segment_observation(value, segment, SOURCE_SHA, proxy)


@pytest.mark.parametrize("duration", [.05, 1, 4])
def test_actual_blind_template_strictly_validates_and_contains_typed_shape(duration):
    prompt, value = blind_template(duration)
    assert audit.validate_visual_blind(value, duration, VIDEO_SHA) is value
    assert value["protocol"] == audit.SEMANTIC_PROTOCOL and value["video_sha256"] == VIDEO_SHA
    assert set(value["evidence"][0]) == {"evidence_id", "claim_id", "kind", "start_s", "end_s",
                                         "observed_fact", "basis_evidence_ids"}
    assert "main_characters和confusions都是string[]" in prompt
    assert "inference只能作为evidence中的kind" in prompt


def test_blind_typed_inference_validates_only_with_direct_evidence_basis():
    _, value = blind_template()
    inference = {**deepcopy(value["evidence"][0]), "evidence_id": "blind_e2", "claim_id": "blind_c2",
                 "kind": "inference", "basis_evidence_ids": ["blind_e1"]}
    value["evidence"].append(inference)
    assert audit.validate_visual_blind(value, 4, VIDEO_SHA) is value
    inference["basis_evidence_ids"] = []
    with pytest.raises(ValueError, match="semantic:inference_requires_direct_evidence"):
        audit.validate_visual_blind(value, 4, VIDEO_SHA)


@pytest.mark.parametrize("field", ["main_characters", "confusions"])
def test_blind_object_array_text_fields_still_fail(field):
    _, value = blind_template()
    value[field] = [{"text": "未知"}]
    with pytest.raises(ValueError, match="text_required"):
        audit.validate_visual_blind(value, 4, VIDEO_SHA)
