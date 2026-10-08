"""Forward-only scoped evidence contracts, not a semantic truth oracle.

No I/O, calls or historical mutation. Window identity is context rather than a
short-slice obligation. Original observation kinds remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from ..contract import ids, number, refs, require, rows, text
from .state import json_sha

PROTOCOL = "scoped_source_evidence_v1"
SOURCE_KINDS = {"visual_action", "visual_state", "visual_outcome", "visible_text"}
DIRECT_VISUAL_KINDS = {"visual_action", "visual_state", "visual_outcome"}
OUTPUT_OPERATION_KINDS = {"speed", "tail_hold"}
STATUSES = {"supported", "partial", "unsupported", "unverifiable"}
UNKNOWN_RELATIONS = {"unknown", "not_observed", "insufficient_scope"}
RELATIONS = UNKNOWN_RELATIONS | {"appearance_matches_context", "direct_action",
    "observed_state", "observed_result", "observed_transition", "text_reading", "contradicted"}


def _object(value, path):
    require(isinstance(value, dict), path + ":object_required")
    return value


def _strings(value, path):
    for value in rows(value, path, nonempty=False):
        text(value, path)


def _sha(value, path):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
            path + ":sha256_required")


def _claim(kind, description, *, segment_id=None, owner_id=None, **fields):
    text(description, "scoped/claim/description")
    seed = {"kind": kind, "description": description, "segment_id": segment_id,
            "owner_id": owner_id, **fields}
    return {"claim_id": "scoped_" + json_sha(seed)[:16], **seed}


def window_identity_context(plan, segment):
    """Copy window bindings as context; never promote them to slice claims."""
    return deepcopy([binding for binding in plan.get("focus_role_bindings", [])
        if binding["window_id"] == segment["window_id"]
        and binding["role_id"] in segment.get("role_ids", [])])


def scoped_segment_claims(plan, segment):
    """Only current-slice role presence and explicit new atomic source_claims.

    Old visual_claims are never automatically converted. Planned captions,
    speed, holds and window-wide identity narratives are not source claims.
    """
    segment_id = segment["segment_id"]
    roles = segment.get("role_ids", [])
    refs(roles, set(roles), "scoped/role_ids")
    result = [_claim("role_presence", role, segment_id=segment_id,
                     scope="source_slice", role_id=role) for role in roles]
    for claim in rows(segment.get("source_claims"), "scoped/source_claims", nonempty=False):
        _object(claim, "scoped/source_claim")
        require(claim.get("kind") in SOURCE_KINDS, "scoped:source_claim_kind")
        text(claim.get("description"), "scoped/source_claim/description")
        require(not {"speed", "freeze_tail_s", "expected_value", "output_operation"} & set(claim),
                "scoped:output_operation_cannot_be_source_claim")
        require(claim.get("scope", "source_slice") == "source_slice", "scoped:source_claim_scope")
        result.append({"claim_id": claim.get("claim_id"), "kind": claim["kind"],
                       "description": claim["description"], "segment_id": segment_id,
                       "scope": "source_slice"})
    ids(result, "claim_id", "scoped/source_claims", nonempty=False)
    return result


def scoped_output_claims(plan, original_slots):
    """Keep every bound original takeaway verbatim and operations output-only."""
    original_ids = ids(original_slots, "slot_id", "scoped/original_slots")
    current_ids = ids(plan.get("slots"), "slot_id", "scoped/slots")
    require(original_ids <= current_ids, "scoped:original_slot_obligation_removed")
    current = {slot["slot_id"]: slot for slot in plan["slots"]}
    result = []
    for slot in original_slots:
        text(slot.get("intended_takeaway"), "scoped/original_takeaway")
        require(current[slot["slot_id"]].get("intended_takeaway") == slot["intended_takeaway"],
                "scoped:original_slot_obligation_changed:" + slot["slot_id"])
        result.append(_claim("slot_takeaway", slot["intended_takeaway"],
                             owner_id=slot["slot_id"], scope="actual_output"))
    for segment in rows(plan.get("segments"), "scoped/segments"):
        speed = number(segment.get("speed"), "scoped/speed", minimum=0.001)
        hold = number(segment.get("freeze_tail_s", 0), "scoped/tail_hold")
        operations = rows(segment.get("output_operation_claims"),
                          "scoped/output_operation_claims", nonempty=False)
        seen = set()
        for claim in operations:
            _object(claim, "scoped/output_operation_claim")
            kind = claim.get("kind")
            require(kind in OUTPUT_OPERATION_KINDS and kind not in seen,
                    "scoped:unknown_or_duplicate_output_operation")
            seen.add(kind)
            expected = speed if kind == "speed" else hold
            value = number(claim.get("expected_value"), "scoped/operation/expected_value")
            require(value == expected, "scoped:output_operation_value_changed:" + kind)
            text(claim.get("description"), "scoped/operation/description")
            result.append({"claim_id": claim.get("claim_id"), "kind": "output_" + kind,
                           "description": claim["description"], "segment_id": segment["segment_id"],
                           "expected_value": claim["expected_value"], "scope": "actual_output"})
        required = ({"speed"} if speed != 1 else set()) | ({"tail_hold"} if hold > 0 else set())
        require(required <= seen, "scoped:transformed_operation_missing_actual_output_claim")
    for binding in plan.get("editing_bindings", []):
        for field in ("intended_relation", "operation", "verification"):
            result.append(_claim("editing_" + field, binding[field],
                                 owner_id=binding["method_id"], scope="actual_output"))
    ids(result, "claim_id", "scoped/output_claims")
    return result


def validate_scoped_plan_claims(plan, original_slots):
    """Validate the explicit split without modifying any plan or observation."""
    claims = [claim for segment in plan["segments"] for claim in scoped_segment_claims(plan, segment)]
    claims += scoped_output_claims(plan, original_slots)
    ids(claims, "claim_id", "scoped/all_claims")
    return plan


def _quoted_result(basis, evidence_by_id, allowed_ids, path):
    evidence_id = basis.get("result_evidence_id")
    require(evidence_id in allowed_ids, path + ":result_evidence_must_be_cited_direct_visual")
    quote = basis.get("result_quote")
    text(quote, path + "/result_quote")
    fact = evidence_by_id[evidence_id].get("description", evidence_by_id[evidence_id].get("observed_fact"))
    require(isinstance(fact, str) and quote in fact, path + ":result_quote_not_in_observation")


def _supported(check, claim, evidence, evidence_by_id, characters, path):
    kind, relation, basis = claim["kind"], check["semantic_relation"], check["basis"]
    direct = [row for row in evidence if row.get("kind") in DIRECT_VISUAL_KINDS]
    if kind == "visible_text":
        require(relation == "text_reading" and any(row.get("kind") == "visible_text" for row in evidence),
                path + ":visible_text_needs_text_reading_evidence")
        return
    require(bool(direct), path + ":visible_claim_needs_direct_visual_evidence")
    if kind == "role_presence":
        require(relation == "appearance_matches_context", path + ":role_presence_relation_required")
        require(basis.get("role_id") == claim["role_id"], path + ":stable_role_id_changed")
        refs(basis.get("character_ids"), characters, path + "/visible_characters", nonempty=True)
        visible = {character for row in direct for character in row.get("character_ids", [])}
        require(set(basis["character_ids"]) <= visible, path + ":role_presence_needs_cited_visible_character")
    elif kind == "visual_action":
        require(relation == "direct_action" and any(row.get("kind") == "visual_action" for row in direct),
                path + ":action_needs_direct_action_relation")
    elif kind == "visual_state":
        # Explicit relation permits neutral old action-kind state records.
        require(relation == "observed_state", path + ":state_needs_observed_state_relation")
    elif kind == "visual_outcome":
        direct_ids = {row["evidence_id"] for row in direct}
        if relation == "observed_result":
            require(basis.get("mode") == "explicit_result_state",
                    path + ":outcome_requires_explicit_result_state_basis")
            _quoted_result(basis, evidence_by_id, direct_ids, path)
        elif relation == "observed_transition":
            before, after = basis.get("before_evidence_ids"), basis.get("after_evidence_ids")
            refs(before, direct_ids, path + "/before", nonempty=True)
            refs(after, direct_ids, path + "/after", nonempty=True)
            require(not set(before) & set(after), path + ":transition_needs_distinct_before_after")
            before_start = min(evidence_by_id[x]["local_start_s"] for x in before)
            after_start = max(evidence_by_id[x]["local_start_s"] for x in after)
            require(before_start <= after_start, path + ":transition_time_order_reversed")
            text(basis.get("change"), path + "/change")
            _quoted_result(basis, evidence_by_id, set(after), path)
        else:
            require(False, path + ":outcome_needs_observed_result_or_transition;observed_kinds="
                    + ",".join(sorted({row.get("kind", "unknown") for row in evidence})))


def validate_scoped_comparison(data, observation, required_claims, *, target_segment_id=None):
    """Unknown coverage is not support or proof of absence; retain limitations."""
    _object(data, "scoped/comparison")
    require(data.get("protocol") == PROTOCOL, "scoped:protocol")
    segment_id = observation["segment_id"] if target_segment_id is None else target_segment_id
    require(data.get("segment_id") == segment_id, "scoped:segment_changed")
    require(data.get("observation_sha256") == json_sha(observation), "scoped:observation_changed")
    claims = ids(required_claims, "claim_id", "scoped/required_claims", nonempty=False)
    by_claim = {claim["claim_id"]: claim for claim in required_claims}
    require(all(claim.get("scope") == "source_slice" and claim.get("kind") in SOURCE_KINDS | {"role_presence"}
                and claim.get("segment_id") == segment_id for claim in required_claims),
            "scoped:only_current_source_slice_claims_allowed")
    checks = rows(data.get("claim_checks"), "scoped/claim_checks", nonempty=False)
    require(ids(checks, "claim_id", "scoped/claim_checks", nonempty=False) == claims,
            "scoped:claims_must_be_checked_exactly_once")
    known = ids(observation.get("evidence"), "evidence_id", "scoped/observation/evidence", nonempty=False)
    evidence_by_id = {row["evidence_id"]: row for row in observation["evidence"]}
    characters = ids(observation.get("characters"), "character_id", "scoped/characters", nonempty=False)
    for check in checks:
        path = "scoped/check[" + check["claim_id"] + "]"
        require(check.get("status") in STATUSES, path + ":status")
        require(check.get("semantic_relation") in RELATIONS, path + ":semantic_relation_required")
        basis = _object(check.get("basis"), path + "/basis")
        text(basis.get("explanation"), path + "/basis/explanation")
        text(check.get("reason"), path + "/reason")
        _strings(check.get("limitations"), path + "/limitations")
        refs(check.get("evidence_ids"), known, path + "/evidence")
        evidence = [evidence_by_id[x] for x in check["evidence_ids"]]
        if check["semantic_relation"] in UNKNOWN_RELATIONS:
            require(check["status"] in {"partial", "unverifiable"},
                    path + ":unknown_cannot_be_supported_or_counterevidence")
        if check["status"] in {"supported", "partial"}:
            require(bool(evidence), path + ":observed_evidence_required")
        if check["status"] == "supported":
            _supported(check, by_claim[check["claim_id"]], evidence, evidence_by_id, characters, path)
        else:
            require(bool(check["limitations"]), path + ":non_supported_needs_limitations")
            if check["status"] == "unsupported":
                counter_kinds = ({"visible_text"} if by_claim[check["claim_id"]]["kind"] == "visible_text"
                                 else DIRECT_VISUAL_KINDS)
                require(check["semantic_relation"] == "contradicted"
                        and any(row.get("kind") in counter_kinds for row in evidence),
                        path + ":unsupported_needs_direct_counterevidence")
    _strings(data.get("uncertainties"), "scoped/uncertainties")
    return data


def _observation_envelopes(observations):
    ids(observations, "segment_id", "scoped/observation_envelopes")
    for row in observations:
        _object(row.get("observation"), "scoped/observation_envelope/body")
        require(row.get("observation_sha256") == json_sha(row["observation"]),
                "scoped:observation_envelope_hash_changed")
    return {row["segment_id"]: row for row in observations}


def validate_batch_comparison(value, observations, claims, *, plan_sha256=None):
    """Cover all bound target segments exactly once without renaming old facts."""
    _object(value, "scoped/batch")
    require(value.get("protocol") == PROTOCOL, "scoped:protocol")
    _sha(value.get("plan_sha256"), "scoped/batch/plan")
    if plan_sha256 is not None:
        require(value.get("plan_sha256") == plan_sha256, "scoped:plan_changed")
    by_segment = _observation_envelopes(observations)
    require(isinstance(claims, dict) and set(claims) == set(by_segment), "scoped:claim_segments_changed")
    ids([claim for segment_claims in claims.values() for claim in segment_claims],
        "claim_id", "scoped/batch/all_claims", nonempty=False)
    comparisons = rows(value.get("segments"), "scoped/batch/segments")
    require(ids(comparisons, "segment_id", "scoped/batch/segments") == set(by_segment),
            "scoped:all_segments_must_be_checked_exactly_once")
    for record in comparisons:
        segment_id = record["segment_id"]
        validate_scoped_comparison({"protocol": value["protocol"], **record}, by_segment[segment_id]["observation"],
                                  claims[segment_id], target_segment_id=segment_id)
    _strings(value.get("limitations"), "scoped/batch/limitations")
    return value


def comparison_contract(required_claims):
    """Generic prompt schema; contains no film answers or proposed source cuts."""
    return {"protocol": PROTOCOL, "semantic_relation_values": sorted(RELATIONS),
        "status_values": sorted(STATUSES), "required_claims": deepcopy(required_claims),
        "claim_check_fields": {"claim_id": "required claim ID", "status": "unverifiable",
            "evidence_ids": [], "semantic_relation": "unknown",
            "basis": {"explanation": "Explain the relation to the immutable observation."},
            "reason": "Explain the judgment.", "limitations": ["State unresolved evidence."]},
        "supported_role_basis": {"role_id": "unchanged stable role ID",
            "character_ids": ["visible observation character ID"], "explanation": "Visible appearance mapping."},
        "supported_result_basis": {"mode": "explicit_result_state", "result_evidence_id": "cited direct evidence ID",
            "result_quote": "Exact observed wording that records the result state.", "explanation": "Why it is the asserted result."},
        "supported_transition_basis": {"before_evidence_ids": ["direct evidence ID"],
            "after_evidence_ids": ["different direct evidence ID"], "change": "Observed state change.",
            "result_evidence_id": "after evidence ID", "result_quote": "Exact observed result wording.",
            "explanation": "Why the cited before/after facts establish the result."},
        "scope_rules": ["Window identity is context; only current-slice role presence is checked.",
            "A visual_state may use an old visual_action record only with observed_state and an explicit basis.",
            "Actions do not automatically prove outcomes; use an explicit result state or an observed transition.",
            "Speed, holds, added captions and all other output operations are checked on the actual output.",
            "Unknown/omitted/out-of-scope observations cannot establish support or direct counterevidence."]}


def comparison_prompt(observations, claims, window_context, *, plan_sha256):
    """Immutable facts first, then source-only atomic checks for every segment."""
    by_segment = _observation_envelopes(observations)
    _sha(plan_sha256, "scoped/prompt/plan")
    require(set(claims) == set(by_segment), "scoped:claim_segments_changed")
    payload = {"immutable_observations": deepcopy(observations),
        "window_identity_context_only": deepcopy(window_context),
        "contracts": {segment_id: comparison_contract(claims[segment_id]) for segment_id in by_segment},
        "response_contract": {"protocol": PROTOCOL, "plan_sha256": plan_sha256,
            "segments": [{"segment_id": segment_id,
                "observation_sha256": by_segment[segment_id]["observation_sha256"],
                "claim_checks": [comparison_contract([])["claim_check_fields"]], "uncertainties": []}
                for segment_id in by_segment], "limitations": []}}
    return ("只输出一个完整JSON对象。逐段核验全部source_slice主张，一项一次，不补造观察。"
        "窗口级身份仅是上下文，不要求短片段证明整窗口经历。保留原观察kind；"
        "state/result可引用旧visual_action但必须说明语义关系与具体依据，动作相关性不自动证明结果。"
        "observed_result须引用记录结果状态的原文，observed_transition须不同的前后直接证据。"
        "unknown/not_observed/insufficient_scope只能partial或unverifiable；直接反证才能unsupported。"
        "速度、停帧、新增字幕效果等输出操作不在原片代理上判定。每段覆盖所有claim_id；"
        "没有足够证据如实保留limitations，不为通过合同改写事实。\n"
        + json.dumps(payload, ensure_ascii=False))
