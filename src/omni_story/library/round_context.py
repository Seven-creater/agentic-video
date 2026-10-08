"""Pure forward prompt projection; evidence stays verbatim, old plans stay archived."""
from copy import deepcopy
import json
import re

from ..contract import require
from .state import json_sha

POLICY = "evidence_first_context_projection_v1"


def _without_failure_text(value):
    if isinstance(value, dict):
        return {key: _without_failure_text(item) for key, item in value.items() if key != "model_text"}
    if isinstance(value, list):
        return [_without_failure_text(item) for item in value]
    return deepcopy(value)


def _fact_reference(value, bank):
    sha = json_sha(value)
    return {"observation_sha256": sha, "lookup": "research_refinement_evidence.source_observations",
            "fallible_model_evidence": True} if sha in bank else None


def _cache_record(value, bank):
    """Retain claim checks and provenance, reference exact duplicated observations."""
    require(isinstance(value, dict), "context:cached_record_object_required")
    result = {key: deepcopy(value[key]) for key in
              ("target_stage", "call_id", "segment", "plan_sha256", "claims", "hypotheses") if key in value}
    result["archived_record_sha256"] = json_sha(value)
    for key in ("value", "observation"):
        if key not in value:
            continue
        ref = _fact_reference(value[key], bank)
        if ref is not None:
            result[key + "_reference"] = ref
        else:
            result[key] = deepcopy(value[key])
    return result


def _feedback(value, bank):
    require(isinstance(value, dict), "context:feedback_object_required")
    result = {key: deepcopy(item) for key, item in value.items() if key != "records"}
    records = value.get("records", [])
    require(isinstance(records, list), "context:feedback_records_array_required")
    result["records"] = []
    for record in records:
        require(isinstance(record, dict), "context:feedback_record_object_required")
        packed = {key: deepcopy(item) for key, item in record.items()
                  if key not in {"model_value", "protocol_failure", "bound_record", "actual_technical_failure"}}
        stage = record.get("stage", "")
        if "model_value" in record:
            model = record["model_value"]
            if re.fullmatch(r"active_[0-9]+_(?:draft|finecut)(?:_repair)?", stage):
                packed["archived_model_value_sha256"] = json_sha(model)
                packed["body_omission"] = "Previous creative plan body omitted; its slots are not new-round obligations."
            elif re.fullmatch(r"active_[0-9]+_trim_[a-f0-9]{16}(?:_repair)?", stage) and isinstance(model, dict):
                packed["archived_model_value_sha256"] = json_sha(model)
                packed["local_proposal_feedback"] = {key: deepcopy(model[key]) for key in
                    ("parent_segment_id", "obligation_status", "limitations", "uncertainties") if key in model}
                packed["evidence_role"] = "Fallible creative proposal warnings, not independent source facts."
            else:
                ref = _fact_reference(model, bank)
                if ref is not None:
                    packed["model_value_reference"] = ref
                else:
                    packed["model_value"] = deepcopy(model)
        if "protocol_failure" in record:
            packed["protocol_failure"] = _without_failure_text(record["protocol_failure"])
            packed["raw_failure_body_archived"] = True
        if "actual_technical_failure" in record:
            packed["actual_technical_failure"] = _without_failure_text(record["actual_technical_failure"])
        if "bound_record" in record:
            packed["bound_record"] = _cache_record(record["bound_record"], bank)
        result["records"].append(packed)
    return result


def project(context, source_observations):
    """Project a newly unpaid round's shared draft/refinement input without I/O.

    The caller must bind and validate all supplied observations before activation.
    This projection neither grants an execution policy nor establishes video truth.
    """
    require(isinstance(context, dict), "context:object_required")
    for key in ("reference", "editing_reference", "catalog", "render_capabilities"):
        require(isinstance(context.get(key), dict), "context:" + key + "_required")
    windows = context.get("watched_windows")
    require(isinstance(windows, list) and bool(windows), "context:complete_windows_required")
    require(isinstance(source_observations, list), "context:bound_source_observations_required")
    facts, bank, exact_records = [], set(), set()
    for row in source_observations:
        require(isinstance(row, dict) and isinstance(row.get("original_observation"), dict),
                "context:typed_source_record_required")
        observation = row["original_observation"]
        require(isinstance(observation.get("evidence"), list)
                and row.get("observation_sha256") == json_sha(observation), "context:observation_hash_changed")
        sha = json_sha(row)
        if sha not in exact_records:
            facts.append(deepcopy(row))
            exact_records.add(sha)
        bank.add(row["observation_sha256"])
    result = deepcopy(context)
    if "actual_feedback" in context:
        result["actual_feedback"] = _feedback(context["actual_feedback"], bank)
    existing = context.get("research_refinement_evidence", {})
    require(isinstance(existing, dict), "context:research_evidence_object_required")
    require(all(json_sha(row) in exact_records for row in existing.get("source_observations", [])),
            "context:previous_bound_fact_omitted")
    result["research_refinement_evidence"] = {key: deepcopy(item) for key, item in existing.items()
                                              if key != "source_observations"}
    result["research_refinement_evidence"]["source_observations"] = facts
    result["context_projection"] = {
        "policy": POLICY, "input_context_sha256": json_sha(context),
        "source_records_sha256": json_sha(source_observations),
        "source_observation_count": len(facts), "watched_window_count": len(windows),
        "evidence_limit": "All observations and comparisons remain fallible model evidence; no new pass is inferred.",
        "instruction": "The fixed reference defines the intended meaning. Reconstruct slots and character routes from "
            "available evidence when prior actions are unsupported. Previous drafts, their durations, slots and claims "
            "are historical attempts, not obligations of this new draft. Do not replace the reference's takeaway. "
            "This new round's refinement must account for its own new model draft's obligations. "
            "Read independent exact-source facts before broad labels; conflicting accounts stay explicit and fallible. "
            "Do not infer missing actions or choose unobserved ranges. Actual source and output gates remain strict."}
    return result


def size_report(before, after):
    """Exact JSON size only; no unknown GLM tokenizer or visual-token estimate."""
    old, new = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                for value in (before, after))
    old_bytes, new_bytes = len(old.encode("utf-8")), len(new.encode("utf-8"))
    return {"before_chars": len(old), "after_chars": len(new), "removed_chars": len(old) - len(new),
            "before_utf8_bytes": old_bytes, "after_utf8_bytes": new_bytes,
            "removed_utf8_bytes": old_bytes - new_bytes,
            "utf8_reduction_fraction": (old_bytes - new_bytes) / old_bytes if old_bytes else None,
            "glm_token_count": None, "token_limit": "GLM tokenizer unavailable; JSON bytes do not establish token savings."}
