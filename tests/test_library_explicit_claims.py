"""Forward prompt/feedback are explicit without changing existing truth gates."""
from copy import deepcopy
import json

import pytest

from omni_story.library import explicit_claims, semantic_audit as audit, semantic_prompts
from omni_story.library.state import json_sha


def inputs():
    observation = {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": "segment_a",
        "source_sha256": "a" * 64, "source_in_s": 100, "source_out_s": 103,
        "characters": [{"character_id": "person", "appearance": "Observed clothing."}],
        "evidence": [
            {"evidence_id": "action", "kind": "visual_action", "description": "Visible action.",
             "local_start_s": 0, "local_end_s": 1, "character_ids": ["person"], "basis_evidence_ids": []},
            {"evidence_id": "result", "kind": "visual_outcome", "description": "Visible change.",
             "local_start_s": 2, "local_end_s": 3, "character_ids": ["person"], "basis_evidence_ids": []},
            {"evidence_id": "words", "kind": "visible_text", "description": "On-screen words.",
             "local_start_s": 1, "local_end_s": 2, "character_ids": [], "basis_evidence_ids": []},
            {"evidence_id": "guess", "kind": "inference", "description": "Possible intention.",
             "local_start_s": 0, "local_end_s": 1, "character_ids": ["person"], "basis_evidence_ids": ["action"]}],
        "uncertainties": []}
    claims = [{"claim_id": "claim_action", "kind": "visual_action", "description": "An action happens."},
              {"claim_id": "claim_result", "kind": "visual_outcome", "description": "A change occurs."},
              {"claim_id": "claim_identity", "kind": "identity", "description": "Participant identity."}]
    hypotheses = [{"role_id": "role", "appearance": "A candidate identity, not truth."}]
    return observation, claims, hypotheses


def test_comparison_fingerprint_ignores_id_aliases_but_retains_range_and_claim_content():
    observation,claims,hypotheses=inputs()
    fingerprint=explicit_claims.comparison_fingerprint(observation,claims,hypotheses)
    observation['segment_id']='new_stage_alias'
    for i,e in enumerate(observation['evidence']):e['evidence_id']='aliased_'+str(i)
    for c in claims:c['claim_id']='new_'+c['claim_id']
    hypotheses[0]['role_id']='new_role_alias'
    assert explicit_claims.comparison_fingerprint(observation,claims,hypotheses)==fingerprint
    observation['source_out_s']+=.25
    assert explicit_claims.comparison_fingerprint(observation,claims,hypotheses)!=fingerprint


def reply(observation, claims):
    return {"protocol": audit.SEMANTIC_PROTOCOL, "segment_id": observation["segment_id"],
        "observation_sha256": json_sha(observation), "claim_checks": [
            {"claim_id": claim["claim_id"], "status": "supported",
             "evidence_ids": ["result" if claim["kind"] == "visual_outcome" else "action"],
             "reason": "The independent fact is directly relevant.", "limitations": []} for claim in claims],
        "uncertainties": []}


def test_prompt_one_complete_template_has_all_actual_ids_fields_and_bound_values():
    observation, claims, hypotheses = inputs()
    before = deepcopy((observation, claims, hypotheses))
    prompt = explicit_claims.prompt(observation, claims, hypotheses)
    head, data = prompt.split("\n输入事实与待核说法：\n")
    template = json.loads(head.split("唯一输出形状：\n", 1)[1])
    assert set(template) == {"protocol", "segment_id", "observation_sha256", "claim_checks", "uncertainties"}
    assert template["protocol"] == audit.SEMANTIC_PROTOCOL
    assert template["segment_id"] == observation["segment_id"]
    assert template["observation_sha256"] == json_sha(observation)
    assert [row["claim_id"] for row in template["claim_checks"]] == [c["claim_id"] for c in claims]
    assert all(set(row) == {"claim_id", "status", "evidence_ids", "reason", "limitations"} for row in template["claim_checks"])
    assert all(row["status"] in audit.CLAIM_STATUSES for row in template["claim_checks"])
    assert template["uncertainties"] == []
    assert "supported也必须显式" in prompt and "即使没有其它不确定性也写[]" in prompt
    assert json.loads(data) == dict(observation=observation, required_claims=claims, role_hypotheses=hypotheses)
    assert (observation, claims, hypotheses) == before


def test_valid_reply_returns_original_under_the_original_strict_validator():
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    before = deepcopy((value, observation, claims))
    assert explicit_claims.diagnostics(value, observation, claims) == []
    assert explicit_claims.validate(value, observation, claims) is value
    assert audit.validate_segment_claim_check(value, observation, claims) is value
    assert (value, observation, claims) == before
    assert "quality_pass" not in value


def test_102_style_omissions_are_all_in_one_feedback_without_filling_103_uncertainty():
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    for row in value["claim_checks"]:
        del row["limitations"]
    del value["uncertainties"]
    before = deepcopy(value)
    errors = explicit_claims.diagnostics(value, observation, claims)
    assert {(e["path"], e["code"]) for e in errors} == {
        ("$.uncertainties", "missing"), ("$.claim_checks[0].limitations", "missing"),
        ("$.claim_checks[1].limitations", "missing"), ("$.claim_checks[2].limitations", "missing")}
    with pytest.raises(ValueError) as failure:
        explicit_claims.validate(value, observation, claims)
    original, collected = str(failure.value).split("; explicit_claim_mechanical_diagnostics=", 1)
    assert original == "semantic/check/limitations:list_required"
    assert json.loads(collected) == errors
    assert value == before and "uncertainties" not in value


def test_103_style_missing_uncertainties_still_fails_instead_of_defaulting_empty():
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    del value["uncertainties"]
    with pytest.raises(ValueError, match="semantic/check/uncertainties:list_required"):
        explicit_claims.validate(value, observation, claims)
    assert "uncertainties" not in value


@pytest.mark.parametrize("mutation,expected", [
    (lambda v: v.update(protocol="new_protocol"), ("$.protocol", "binding")),
    (lambda v: v.update(segment_id="other_segment"), ("$.segment_id", "binding")),
    (lambda v: v.update(observation_sha256="b" * 64), ("$.observation_sha256", "binding")),
    (lambda v: v.pop("protocol"), ("$.protocol", "missing")),
    (lambda v: v["claim_checks"][0].update(status="supported|partial"), ("$.claim_checks[0].status", "enum")),
    (lambda v: v["claim_checks"][0].update(status=[]), ("$.claim_checks[0].status", "enum")),
    (lambda v: v["claim_checks"][0].update(reason=" "), ("$.claim_checks[0].reason", "text")),
    (lambda v: v["claim_checks"][0].update(limitations="none"), ("$.claim_checks[0].limitations", "type")),
    (lambda v: v.update(uncertainties="none"), ("$.uncertainties", "type")),
    (lambda v: v["claim_checks"][0].update(evidence_ids=["missing"]), ("$.claim_checks[0].evidence_ids", "duplicate_or_unknown_ref")),
    (lambda v: v["claim_checks"][0].update(evidence_ids=["action", "action"]), ("$.claim_checks[0].evidence_ids", "duplicate_or_unknown_ref")),
    (lambda v: v["claim_checks"][0].update(evidence_ids=[]), ("$.claim_checks[0].evidence_ids", "count")),
    (lambda v: v["claim_checks"][0].update(claim_id="../unsafe"), ("$.claim_checks[0].claim_id", "id")),
])
def test_binding_types_enums_refs_and_counts_aggregate_and_preserve_original_rejection(mutation, expected):
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    mutation(value)
    before = deepcopy(value)
    assert expected in {(e["path"], e["code"]) for e in explicit_claims.diagnostics(value, observation, claims)}
    with pytest.raises((ValueError, TypeError, KeyError)):
        audit.validate_segment_claim_check(value, observation, claims)
    with pytest.raises(ValueError):
        explicit_claims.validate(value, observation, claims)
    assert value == before


def test_duplicate_unknown_and_missing_claims_all_report_without_skipping_row_fields():
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    value["claim_checks"].pop()
    value["claim_checks"].append({"claim_id": "claim_action", "status": "pass"})
    value["claim_checks"].append({"claim_id": "unknown", "status": "supported"})
    errors = explicit_claims.diagnostics(value, observation, claims)
    actual = {(e["path"], e["code"]) for e in errors}
    assert ("$.claim_checks[2].claim_id", "duplicate_or_unknown_id") in actual
    assert ("$.claim_checks[2].status", "enum") in actual
    assert ("$.claim_checks[2].limitations", "missing") in actual
    assert ("$.claim_checks[3].claim_id", "duplicate_or_unknown_id") in actual
    assert ("$.claim_checks", "coverage") in actual


@pytest.mark.parametrize("value", [None, [], "raw", {}, {"claim_checks": None},
    {"claim_checks": [None, {"claim_id": [], "status": {}, "evidence_ids": [{}], "limitations": [None]}]}])
def test_malformed_reply_never_crashes_the_diagnostic_or_gets_normalized(value):
    observation, claims, _ = inputs()
    before = deepcopy(value)
    errors = explicit_claims.diagnostics(value, observation, claims)
    assert errors and all(set(e) == {"path", "code", "expected", "actual"} for e in errors)
    with pytest.raises(ValueError, match="explicit_claim_mechanical_diagnostics"):
        explicit_claims.validate(value, observation, claims)
    assert value == before


@pytest.mark.parametrize("status", ["partial", "unsupported", "unverifiable"])
def test_non_supported_requires_real_limitations_and_partial_needs_evidence(status):
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    row = value["claim_checks"][0]
    row["status"] = status
    errors = explicit_claims.diagnostics(value, observation, claims)
    assert any(e["path"] == "$.claim_checks[0].limitations" and e["code"] == "count" for e in errors)
    row["limitations"] = ["Actual source lacks complete support."]
    if status != "partial":
        row["evidence_ids"] = []
    assert explicit_claims.validate(value, observation, claims) is value
    assert row["status"] == status


@pytest.mark.parametrize("evidence,claim_index,original_error", [
    (["guess"], 0, "inference_or_text_cannot_prove_visible_action"),
    (["words"], 0, "inference_or_text_cannot_prove_visible_action"),
    (["action"], 1, "visual_outcome_requires_outcome_typed_evidence"),
    (["words"], 2, "inference_or_text_cannot_prove_visible_action"),
])
def test_mechanically_complete_reply_cannot_weaken_typed_evidence_semantic_gate(evidence, claim_index, original_error):
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    value["claim_checks"][claim_index]["evidence_ids"] = evidence
    assert explicit_claims.diagnostics(value, observation, claims) == []
    with pytest.raises(ValueError, match=original_error):
        explicit_claims.validate(value, observation, claims)
    assert value["claim_checks"][claim_index]["status"] == "supported"


def test_identity_without_observed_character_is_still_strictly_rejected():
    observation, claims, _ = inputs()
    observation["evidence"][0]["character_ids"] = []
    value = reply(observation, claims)
    assert explicit_claims.diagnostics(value, observation, claims) == []
    with pytest.raises(ValueError, match="identity_needs_visible_character"):
        explicit_claims.validate(value, observation, claims)


def test_diagnostics_actual_and_expected_are_not_mutable_response_aliases():
    observation, claims, _ = inputs()
    value = reply(observation, claims)
    value["claim_checks"][0]["evidence_ids"] = ["foreign"]
    errors = explicit_claims.diagnostics(value, observation, claims)
    err = next(e for e in errors if e["code"] == "duplicate_or_unknown_ref")
    err["actual"].append("changed")
    err["expected"].append("made_up")
    assert value["claim_checks"][0]["evidence_ids"] == ["foreign"]
    assert [e["evidence_id"] for e in observation["evidence"]] == ["action", "result", "words", "guess"]


def test_prompt_rejects_duplicate_required_ids_instead_of_hiding_them_in_a_set():
    observation, claims, hypotheses = inputs()
    claims.append(deepcopy(claims[0]))
    with pytest.raises(ValueError, match="duplicate_id"):
        explicit_claims.prompt(observation, claims, hypotheses)


def test_empty_claim_set_is_explicit_and_does_not_invent_a_claim():
    observation, _, hypotheses = inputs()
    prompt = explicit_claims.prompt(observation, [], hypotheses)
    template = json.loads(prompt.split("唯一输出形状：\n", 1)[1].split("\n输入事实与待核说法：\n", 1)[0])
    assert template["claim_checks"] == []
    assert explicit_claims.validate(template, observation, []) is template


def test_old_input_parser_recovers_actual_request_data_without_reply_normalization():
    observation, claims, hypotheses = inputs()
    old_prompt = semantic_prompts.slice_claim_prompt(observation, claims, hypotheses)
    before = deepcopy((observation, claims, hypotheses))
    parsed = explicit_claims.old_claim_input(old_prompt)
    assert parsed == before
    parsed[0]["evidence"][0]["description"] = "Changed returned object."
    assert (observation, claims, hypotheses) == before
    assert explicit_claims.old_claim_input(explicit_claims.prompt(observation, claims, hypotheses)) == before


def test_old_input_parser_handles_marker_text_inside_json_strings_and_real_repairs():
    observation, claims, hypotheses = inputs()
    observation["evidence"][0]["description"] += "\nrequired_claims： [] \nrole_hypotheses： {}"
    old_prompt = semantic_prompts.slice_claim_prompt(observation, claims, hypotheses)
    repair = old_prompt + "\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。" + json.dumps({
        "validation_error": "Missing fields.", "previous_response": '{"claim_checks":[]}'}, ensure_ascii=False)
    assert explicit_claims.old_claim_input(old_prompt) == (observation, claims, hypotheses)
    assert explicit_claims.old_claim_input(repair) == (observation, claims, hypotheses)


@pytest.mark.parametrize("mutation", [
    lambda p: p.replace("\nobservation：", "\nobservation:", 1),
    lambda p: p.replace("\nrequired_claims：", "\nother_claims：", 1),
    lambda p: p.replace("\nrole_hypotheses：", "\nrole_hypotheses： malformed ", 1),
    lambda p: p[:-1],
    lambda p: p + " extra unrelated text",
    lambda p: p.replace("\nrequired_claims：", " intervening text\nrequired_claims：", 1),
])
def test_old_input_parser_rejects_truncated_or_ambiguous_input_blocks(mutation):
    observation, claims, hypotheses = inputs()
    old_prompt = semantic_prompts.slice_claim_prompt(observation, claims, hypotheses)
    assert explicit_claims.old_claim_input(mutation(old_prompt)) is None


@pytest.mark.parametrize("raw", [None, [], {}, "", '{"claim_checks":[]}'])
def test_old_input_parser_does_not_interpret_responses_as_request_inputs(raw):
    assert explicit_claims.old_claim_input(raw) is None


def test_cosmetic_prompt_or_stage_changes_do_not_change_key_comparison_inputs():
    observation, claims, hypotheses = inputs()
    prompt = semantic_prompts.slice_claim_prompt(observation, claims, hypotheses)
    first = explicit_claims.old_claim_input(prompt)
    second = explicit_claims.old_claim_input("New stage name or generic policy.\n" + prompt)
    assert json_sha(first) == json_sha(second)
    changed_claims = deepcopy(claims)
    changed_claims[0]["description"] = "A genuinely different action claim."
    changed = explicit_claims.old_claim_input(semantic_prompts.slice_claim_prompt(observation, changed_claims, hypotheses))
    assert json_sha(changed) != json_sha(first)


@pytest.mark.parametrize('formatter', [explicit_claims.prompt, explicit_claims.forward_prompt])
def test_explicit_request_and_sole_repair_keep_same_comparison_input(formatter):
    observation, claims, hypotheses = inputs()
    request = formatter(observation, claims, hypotheses)
    repair = request + '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。' + json.dumps({
        'validation_error': 'Missing field', 'previous_response': 'not an observation'}, ensure_ascii=False)
    assert explicit_claims.old_claim_input(request) == (observation, claims, hypotheses)
    assert explicit_claims.old_claim_input(repair) == (observation, claims, hypotheses)
    assert explicit_claims.old_claim_input(request + ' unrelated tail') is None
    assert explicit_claims.old_claim_input(request[:-1]) is None


def test_forward_prompt_corrects_field_count_without_changing_historical_prompt():
    data = inputs()
    old = explicit_claims.prompt(*data)
    new = explicit_claims.forward_prompt(*data)
    assert '全部六个字段' in old and '全部五个字段' in new
    assert new == old.replace('全部六个字段', '全部五个字段')
    assert explicit_claims.comparison_fingerprint(*explicit_claims.old_claim_input(old)) == \
        explicit_claims.comparison_fingerprint(*explicit_claims.old_claim_input(new))
