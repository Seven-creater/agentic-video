"""Opt-in visual evidence contracts, not an automatic semantic truth oracle.

Observe exact selected slices without the plan first, then compare immutable
observations with all planned claims. These helpers perform no I/O, model call,
render, or historical-state mutation. Legacy protocols never call them.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from ..contract import forbid_keys, ids, number, refs, require, rows, text
from .state import json_sha

SEMANTIC_PROTOCOL = "visual_narrative_primary_v2"
EVIDENCE_KINDS = {"visual_action", "visual_outcome", "visible_text", "inference"}
CLAIM_STATUSES = {"supported", "partial", "unsupported", "unverifiable"}
REVIEW_STATUSES = {"pass", "partial", "fail", "unverifiable"}
VISUAL_KINDS = {"visual_action", "visual_outcome"}


def _object(value, path):
    require(isinstance(value, dict), path + ":object_required")
    return value


def _sha(value, path):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
            path + ":sha256_required")


def _strings(value, path):
    for value in rows(value, path, nonempty=False):
        text(value, path)


def _interval(start, end, duration, path):
    start, end = number(start, path), number(end, path)
    require(start < end <= duration + 0.001, path + ":outside_observed_slice")


def _interval_diagnostics(evidence, duration, start_field, end_field):
    """Describe every rejected interval without inventing or changing evidence."""
    def finite_time(value):
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False

    def safe(value):
        if type(value) is float and not math.isfinite(value):
            return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
        if type(value) is int and not finite_time(value):
            return "<int:outside_finite_time_range>"
        if value is None or type(value) in (str, int, float, bool):
            return value
        return "<" + type(value).__name__ + ">"

    invalid = []
    for index, row in enumerate(evidence):
        start, end = row.get(start_field), row.get(end_field)
        try:
            _interval(start, end, duration, "semantic/evidence")
        except (ValueError, OverflowError):
            if not all(finite_time(value) for value in (start, end)):
                problem = "invalid_time_value"
            elif min(start, end) < 0:
                problem = "negative_time"
            elif start == end:
                problem = "zero_duration"
            elif start > end:
                problem = "reversed_interval"
            else:
                problem = "outside_observed_slice"
            invalid.append({"evidence_id": row["evidence_id"], "row_index": index,
                            "start_s": safe(start), "end_s": safe(end), "problem": problem})
    return {"observed_duration_s": duration, "start_field": start_field, "end_field": end_field,
            "required_relation": "0 <= start < end <= observed_duration_s",
            "end_tolerance_s": 0.001, "invalid_intervals": invalid}


def _claim(kind, description, *, segment_id=None, owner_id=None):
    text(description, "semantic/required_claim/description")
    seed = json.dumps([kind, description, segment_id, owner_id], ensure_ascii=False)
    return {"claim_id": "claim_" + hashlib.sha256(seed.encode()).hexdigest()[:16],
            "kind": kind, "description": description,
            "segment_id": segment_id, "owner_id": owner_id}


def segment_required_claims(plan, segment):
    """Per-slice claims only; a slice need not prove an entire montage or slot.

    New visual_claims rows are {claim_id,kind,description}; kind is visual_action,
    visual_outcome or identity. Existing captions, role presence and focus
    identity remain represented even when visual_claims are absent.
    """
    segment_id = segment["segment_id"]
    result = [_claim("role_presence", role, segment_id=segment_id)
              for role in segment.get("role_ids", [])]
    for binding in plan.get("focus_role_bindings", []):
        if binding["window_id"] == segment["window_id"] and binding["role_id"] in segment.get("role_ids", []):
            result.append(_claim("identity", binding["identity_evidence"], segment_id=segment_id))
    if segment.get("caption"):
        result.append(_claim("caption", segment["caption"]["text"], segment_id=segment_id))
    for claim in rows(segment.get("visual_claims", []), "semantic/visual_claims", nonempty=False):
        _object(claim, "semantic/visual_claim")
        require(claim.get("kind") in {"visual_action", "visual_outcome", "identity"},
                "semantic:visual_claim_kind")
        text(claim.get("description"), "semantic/visual_claim/description")
        result.append({"claim_id": claim.get("claim_id"), "kind": claim["kind"],
                       "description": claim["description"], "segment_id": segment_id})
    ids(result, "claim_id", "semantic/required_claims", nonempty=False)
    return result


def output_required_claims(plan):
    """Keep every slot and every method relation/operation/check at output scope."""
    result = [_claim("slot_takeaway", slot["intended_takeaway"], owner_id=slot["slot_id"])
              for slot in plan["slots"]]
    for binding in plan.get("editing_bindings", []):
        for field in ("intended_relation", "operation", "verification"):
            result.append(_claim("editing_" + field, binding[field], owner_id=binding["method_id"]))
    ids(result, "claim_id", "semantic/output_claims")
    return result


def _validate_evidence(evidence, duration, *, local, characters=None, claim_ids=False):
    known = ids(evidence, "evidence_id", "semantic/evidence")
    if claim_ids:
        ids(evidence, "claim_id", "semantic/blind_claims")
    by_id = {row["evidence_id"]: row for row in evidence}
    for row in evidence:
        require(row.get("kind") in EVIDENCE_KINDS, "semantic:unknown_evidence_kind")
        a, b = ("local_start_s", "local_end_s") if local else ("start_s", "end_s")
        try:
            _interval(row.get(a), row.get(b), duration, "semantic/evidence")
        except ValueError as exc:
            exc.diagnostics = _interval_diagnostics(evidence, duration, a, b)
            raise
        text(row.get("description" if local else "observed_fact"), "semantic/evidence/fact")
        refs(row.get("basis_evidence_ids"), known - {row["evidence_id"]}, "semantic/evidence/basis")
        if characters is not None:
            refs(row.get("character_ids"), characters, "semantic/evidence/characters")
        basis = row["basis_evidence_ids"]
        if row["kind"] == "inference":
            require(bool(basis) and all(by_id[x]["kind"] != "inference" for x in basis),
                    "semantic:inference_requires_direct_evidence")
        else:
            require(not basis, "semantic:direct_fact_cannot_depend_on_inference")
    return by_id


def validate_segment_observation(data, segment, source_sha256, proxy):
    """Pure source-slice reading. Never supply plan/theme/claims to this call.

    evidence uses local_start_s/local_end_s, description, character_ids and
    basis_evidence_ids. characters uses character_id and observed appearance.
    The supplied trusted proxy must bind exactly this selected source range,
    rather than the containing 90-second watched window.
    """
    _object(data, "semantic/observation")
    _sha(source_sha256, "semantic/source")
    _sha(proxy.get("sha256"), "semantic/proxy")
    require(data.get("protocol") == SEMANTIC_PROTOCOL, "semantic:protocol")
    for key, expected in (("segment_id", segment["segment_id"]), ("source_id", segment["source_id"]),
                          ("source_sha256", source_sha256), ("proxy_sha256", proxy["sha256"]),
                          ("source_in_s", segment["source_in_s"]), ("source_out_s", segment["source_out_s"])):
        require(data.get(key) == expected, "semantic:observation_binding_changed:" + key)
    require(proxy.get("source_id") == segment["source_id"] and proxy.get("source_sha256") == source_sha256,
            "semantic:proxy_source_changed")
    require(proxy.get("source_start_s") == segment["source_in_s"]
            and proxy.get("source_end_s") == segment["source_out_s"], "semantic:exact_slice_proxy_required")
    duration = number(proxy.get("duration_s"), "semantic/proxy/duration", minimum=0.001)
    actual = number(data.get("observed_duration_s"), "semantic/observed_duration", minimum=0.001)
    require(abs(actual - duration) <= 0.001, "semantic:observation_duration_changed")
    require(abs(duration - (segment["source_out_s"] - segment["source_in_s"])) <= 0.25,
            "semantic:proxy_duration_not_source_slice")
    characters = ids(data.get("characters"), "character_id", "semantic/characters", nonempty=False)
    for character in data["characters"]:
        text(character.get("appearance"), "semantic/character/appearance")
    _validate_evidence(data.get("evidence"), duration, local=True, characters=characters)
    _strings(data.get("uncertainties"), "semantic/uncertainties")
    forbid_keys(data, {"plan", "reference", "theme", "intended_takeaway", "required_claims", "claim_checks"})
    return data


def _checks(checks, claims, evidence_lookup):
    required = ids(claims, "claim_id", "semantic/required_claims", nonempty=False)
    found = ids(checks, "claim_id", "semantic/claim_checks", nonempty=False)
    require(found == required, "semantic:required_claims_must_be_checked_exactly_once")
    by_id = {claim["claim_id"]: claim for claim in claims}
    for check in checks:
        require(check.get("status") in CLAIM_STATUSES, "semantic:claim_status")
        text(check.get("reason"), "semantic/check/reason")
        _strings(check.get("limitations"), "semantic/check/limitations")
        evidence = evidence_lookup(check)
        if check["status"] in {"supported", "partial"}:
            require(bool(evidence), "semantic:claim_needs_observed_evidence")
        if check["status"] == "supported":
            kind = by_id[check["claim_id"]]["kind"]
            if kind == "visible_text":
                allowed = {"visible_text"}
            elif kind == "visual_outcome":
                allowed = {"visual_outcome"}
            elif kind == "caption":
                allowed = VISUAL_KINDS | {"visible_text"}
            else:
                allowed = VISUAL_KINDS
            diagnostic = ("semantic:visual_outcome_requires_outcome_typed_evidence"
                          if kind == "visual_outcome" and any(row["kind"] in VISUAL_KINDS for row in evidence)
                          else "semantic:inference_or_text_cannot_prove_visible_action")
            require(any(row["kind"] in allowed for row in evidence), diagnostic)
            if kind in {"identity", "role_presence"}:
                require(any(row.get("character_ids") for row in evidence), "semantic:identity_needs_visible_character")
        else:
            require(bool(check["limitations"]), "semantic:non_supported_claim_needs_limitation")
    return checks


def validate_segment_claim_check(data, observation, required_claims):
    """Assess claims only after, and hash-bound to, the immutable observation."""
    _object(data, "semantic/claim_check")
    require(data.get("protocol") == SEMANTIC_PROTOCOL, "semantic:protocol")
    require(data.get("segment_id") == observation["segment_id"], "semantic:claim_check_segment_changed")
    require(data.get("observation_sha256") == json_sha(observation), "semantic:claim_check_observation_changed")
    by_id = {row["evidence_id"]: row for row in observation["evidence"]}
    def evidence_lookup(check):
        refs(check.get("evidence_ids"), set(by_id), "semantic/check/evidence")
        return [by_id[x] for x in check["evidence_ids"]]
    _checks(data.get("claim_checks"), required_claims, evidence_lookup)
    _strings(data.get("uncertainties"), "semantic/check/uncertainties")
    return data


def validate_visual_blind(data, duration_s, video_sha256):
    """Silent output reading with typed action/text/inference/outcome evidence."""
    _object(data, "semantic/blind")
    _sha(video_sha256, "semantic/output_video")
    require(data.get("protocol") == SEMANTIC_PROTOCOL, "semantic:protocol")
    require(data.get("video_sha256") == video_sha256, "semantic:blind_video_changed")
    for field in ("observed_story", "apparent_theme"):
        text(data.get(field), "semantic/blind/" + field)
    _strings(data.get("main_characters"), "semantic/blind/characters")
    _strings(data.get("confusions"), "semantic/blind/confusions")
    require(data.get("text_dependency") in {"none", "assists", "essential", "unverifiable"},
            "semantic:blind_text_dependency")
    _validate_evidence(data.get("evidence"), duration_s, local=False, claim_ids=True)
    return data


def _source_statuses(segment_checks):
    combined = []
    for record in segment_checks:
        _object(record, "semantic/source_check")
        combined.extend(rows(record.get("claim_checks"), "semantic/source_check/claims", nonempty=False))
    ids(combined, "claim_id", "semantic/source_check/claims", nonempty=False)
    for check in combined:
        require(check.get("status") in CLAIM_STATUSES, "semantic:source_claim_status")
    return {c["claim_id"]: c["status"] for c in combined}


def validate_semantic_review(data, blind, segment_observations, required_claims, duration_s, video_sha256,
                             *, segment_checks=()):
    """Claim-ID cross-checking; unresolved contradictions block primary pass.

    fact_checks covers required_claims and every blind evidence claim_id exactly
    once. Each check has evidence_refs [{segment_id,evidence_id}] and
    blind_evidence_ids. contradictions has contradiction_id, claim_ids, status
    unresolved/resolved, reason, resolution and the same evidence reference
    fields. A resolved contradiction needs actual direct visual evidence. Its
    optional rejected_claim_ids may retire erroneous blind claims, never plan
    requirements; their original records and unsupported verdicts are retained.
    Supplied independent segment_checks are hash-bound to the observations;
    whole-output interpretation cannot promote their non-supported slice claims.
    """
    _object(data, "semantic/review")
    validate_visual_blind(blind, duration_s, video_sha256)
    require(data.get("protocol") == SEMANTIC_PROTOCOL, "semantic:protocol")
    require(data.get("video_sha256") == video_sha256, "semantic:review_video_changed")
    for field in ("theme_status", "editing_status", "continuity_status", "visual_narrative_status"):
        require(data.get(field) in REVIEW_STATUSES, "semantic:review_status:" + field)
    observations = list(segment_observations.values()) if isinstance(segment_observations, dict) else segment_observations
    ids(observations, "segment_id", "semantic/review/observations", nonempty=False)
    if segment_checks:
        source_segments = ids(list(segment_checks), "segment_id", "semantic/review/source_checks")
        require(source_segments == {o["segment_id"] for o in observations},
                "semantic:source_checks_must_cover_observed_segments")
        by_segment = {o["segment_id"]: o for o in observations}
        for record in segment_checks:
            require(record.get("observation_sha256") == json_sha(by_segment[record["segment_id"]]),
                    "semantic:review_source_check_observation_changed")
    source_evidence = {(o["segment_id"], e["evidence_id"]): e for o in observations for e in o["evidence"]}
    blind_evidence = {e["evidence_id"]: e for e in blind["evidence"]}
    def evidence_lookup(check):
        seen, evidence = set(), []
        for ref in rows(check.get("evidence_refs"), "semantic/review/evidence_refs", nonempty=False):
            _object(ref, "semantic/review/ref")
            key = (ref.get("segment_id"), ref.get("evidence_id"))
            require(key in source_evidence and key not in seen, "semantic:unknown_or_duplicate_source_evidence")
            seen.add(key)
            evidence.append(source_evidence[key])
        refs(check.get("blind_evidence_ids"), set(blind_evidence), "semantic/review/blind_evidence")
        return evidence + [blind_evidence[x] for x in check["blind_evidence_ids"]]
    claims = list(required_claims) + [{"claim_id": e["claim_id"], "kind": e["kind"],
        "description": e["observed_fact"]} for e in blind["evidence"]]
    _checks(data.get("fact_checks"), claims, evidence_lookup)
    source_statuses = _source_statuses(segment_checks)
    for check in data["fact_checks"]:
        require(not (check["claim_id"] in source_statuses and check["status"] == "supported"
                     and source_statuses[check["claim_id"]] != "supported"),
                "semantic:whole_review_cannot_promote_unsupported_slice_claim")
    known_claims = {c["claim_id"] for c in claims}
    blind_claims = {e["claim_id"] for e in blind["evidence"]}
    by_check = {c["claim_id"]: c for c in data["fact_checks"]}
    contradictions = data.get("contradictions")
    ids(contradictions, "contradiction_id", "semantic/contradictions", nonempty=False)
    for contradiction in contradictions:
        refs(contradiction.get("claim_ids"), known_claims, "semantic/contradiction/claims", nonempty=True)
        require(len(contradiction["claim_ids"]) >= 2, "semantic:contradiction_needs_two_claims")
        text(contradiction.get("reason"), "semantic/contradiction/reason")
        require(contradiction.get("status") in {"resolved", "unresolved"}, "semantic:contradiction_status")
        refs(contradiction.get("rejected_claim_ids", []), blind_claims & set(contradiction["claim_ids"]),
             "semantic/contradiction/rejected_blind_claims")
        evidence = evidence_lookup(contradiction)
        if contradiction["status"] == "resolved":
            text(contradiction.get("resolution"), "semantic/contradiction/resolution")
            require(any(e["kind"] in VISUAL_KINDS for e in evidence), "semantic:resolution_needs_direct_visual_evidence")
            require(all(by_check[x]["status"] in {"unsupported", "unverifiable"}
                        for x in contradiction.get("rejected_claim_ids", [])),
                    "semantic:rejected_blind_claim_cannot_be_supported")
        else:
            require(contradiction.get("resolution") is None, "semantic:unresolved_resolution_must_be_null")
            require(not contradiction.get("rejected_claim_ids"), "semantic:unresolved_cannot_retire_claims")
    if data["visual_narrative_status"] == "pass" or all(data[k] == "pass" for k in
            ("theme_status", "editing_status", "continuity_status")):
        require(not any(c["status"] == "unresolved" for c in contradictions), "semantic:unresolved_contradiction_blocks_pass")
        require(_remaining_claims_supported(data, blind), "semantic:unsupported_fact_blocks_pass")
        require(not blind["confusions"] and blind["text_dependency"] in {"none", "assists"},
                "semantic:visual_comprehension_not_established")
    return data


def _remaining_claims_supported(review, blind):
    blind_ids = {e["claim_id"] for e in blind["evidence"]}
    retired = {claim for c in review.get("contradictions", []) if c["status"] == "resolved"
               for claim in c.get("rejected_claim_ids", [])} & blind_ids
    return all(c["status"] == "supported" or (c["claim_id"] in retired
               and c["status"] in {"unsupported", "unverifiable"}) for c in review["fact_checks"])


def semantic_review_passes(review, blind, segment_checks=(), *, expected_segment_ids=None):
    """Final gate after validation; rendering success alone never meets it."""
    return (all(review.get(k) == "pass" for k in
            ("theme_status", "editing_status", "continuity_status", "visual_narrative_status"))
        and not any(c["status"] == "unresolved" for c in review.get("contradictions", []))
        and bool(review.get("fact_checks")) and _remaining_claims_supported(review, blind)
        and not review.get("revision_requests")
        and not blind.get("confusions") and blind.get("text_dependency") in {"none", "assists"}
        and bool(segment_checks)
        and len({r["segment_id"] for r in segment_checks}) == len(segment_checks)
        and (expected_segment_ids is None
             or {r["segment_id"] for r in segment_checks} == set(expected_segment_ids))
        and all(c["status"] == "supported" for record in segment_checks for c in record["claim_checks"]))


def validate_semantic_selection(choice, candidates):
    """Compare verified evidence IDs, retaining limitations on partial choices.

    Candidates are {round,review,blind,segment_checks,expected_segment_ids}; choice contains
    selected_round, reason, evidence_claim_ids, limitations. No model truth is
    created here and this helper never grants an additional render.
    """
    _object(choice, "semantic/selection")
    by_round = {c["round"]: c for c in candidates}
    require(len(by_round) == len(candidates), "semantic:duplicate_candidate_round")
    require(type(choice.get("selected_round")) is int and choice["selected_round"] in by_round,
            "semantic:unknown_selected_round")
    text(choice.get("reason"), "semantic/selection/reason")
    selected = by_round[choice["selected_round"]]
    supported = {c["claim_id"] for c in selected["review"]["fact_checks"] if c["status"] == "supported"}
    unverified = {claim for claim, status in _source_statuses(selected.get("segment_checks", ())).items()
                  if status != "supported"}
    for contradiction in selected["review"].get("contradictions", []):
        if contradiction["status"] == "unresolved":
            unverified.update(contradiction["claim_ids"])
        unverified.update(contradiction.get("rejected_claim_ids", []))
    supported -= unverified
    refs(choice.get("evidence_claim_ids"), supported, "semantic/selection/verified_claims", nonempty=True)
    _strings(choice.get("limitations"), "semantic/selection/limitations")
    if not semantic_review_passes(selected["review"], selected["blind"], selected.get("segment_checks", ()),
                                  expected_segment_ids=selected.get("expected_segment_ids")):
        require(bool(choice["limitations"]), "semantic:limited_candidate_selection_needs_limitations")
    return choice


def validate_caption_temporal_evidence(plan, windows):
    """New-protocol synchronized evidence only; no summary/foreshadow bypass.

    Caption output-local times map to source_in + local * speed. Evidence must
    overlap the source played while the text is shown, not merely another part
    of the selected range. Frozen tails map to the final real source frame.
    """
    if isinstance(windows, list):
        windows = {w["window_id"]: w for w in windows}
    for segment in plan["segments"]:
        caption = segment.get("caption")
        if not caption:
            continue
        require(caption.get("evidence_mode", "synchronous") == "synchronous",
                "semantic:caption_summary_or_foreshadow_not_supported")
        window = windows[segment["window_id"]]
        speed = number(segment["speed"], "semantic/caption/speed", minimum=0.001)
        frame_s = speed / plan["fps"]
        last_frame_start = max(segment["source_in_s"], segment["source_out_s"] - frame_s)
        start = min(segment["source_in_s"] + caption["start_s"] * speed, last_frame_start)
        end = min(segment["source_in_s"] + caption["end_s"] * speed, segment["source_out_s"])
        for binding in caption["evidence"]:
            require(binding["window_id"] == segment["window_id"], "semantic:caption_evidence_window_changed")
            for index in binding["event_indices"]:
                event = window["observation"]["events"][index]
                a = window["source_start_s"] + event["local_start_s"]
                b = window["source_start_s"] + event["local_end_s"]
                require(a < end and start < b, "semantic:caption_evidence_not_visible_during_display")
    return plan
