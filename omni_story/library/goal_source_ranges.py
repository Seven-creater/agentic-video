"""Mechanical navigation for recorded ranges, without choosing movie cuts."""
from __future__ import annotations

from copy import deepcopy
import json

from .state import json_sha

POLICY = "goal_source_range_diagnostics_v1"
INSTRUCTION = (
    "EDL source_in_s/source_out_s are absolute seconds in the source file; observation local times "
    "are relative to that window's source_start_s. derived_usable_ranges lists every original "
    "usable range after offset addition, with unchanged allowed role_ids and event indices. "
    "Each continuous selected slice must fit ONE listed range AND its role_ids must be a subset "
    "of that SAME row. Do not join ranges across gaps or pool role sets from an entire window. "
    "Rows are fallible recorded model observations, not proof that an action happens in every "
    "short subrange. Exact facts and claims are checked later. Exhausted exact inputs remain "
    "unavailable for a third observation; changing segment IDs does not unlock them. Choose cuts yourself."
)


def range_table(windows):
    rows = []
    for window in windows:
        observation = window["observation"]
        for index, usable in enumerate(observation["usable_ranges"]):
            rows.append({
                "window_id": window["window_id"], "source_id": window["source_id"],
                "source_sha256": window["source_sha256"],
                "observation_sha256": json_sha(observation), "usable_index": index,
                "source_in_s": window["source_start_s"] + usable["local_in_s"],
                "source_out_s": window["source_start_s"] + usable["local_out_s"],
                "role_ids": deepcopy(usable["role_ids"]),
                "event_indices": deepcopy(usable["event_indices"]),
            })
    return rows


def plan_diagnostics(plan, windows, exhausted):
    """Report all range/role and exhausted-input blockers; never propose fixes."""
    if not isinstance(plan, dict) or not isinstance(plan.get("segments"), list):
        return []
    lookup = {w["window_id"]: w for w in windows}
    table = range_table(windows)
    blockers = []
    for segment in plan["segments"]:
        if not isinstance(segment, dict):
            continue
        window = lookup.get(segment.get("window_id"))
        start, end = segment.get("source_in_s"), segment.get("source_out_s")
        roles = segment.get("role_ids")
        if not window or type(start) not in (float, int) or type(end) not in (float, int) \
                or not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            continue  # The original structural contract reports malformed fields.
        candidates = [r for r in table if r["window_id"] == window["window_id"]]
        contained = [r for r in candidates if r["source_in_s"] - .001 <= start < end <= r["source_out_s"] + .001]
        accepted = [r for r in contained if set(roles) <= set(r["role_ids"])]
        if not accepted:
            blockers.append({"error": "plan:range_not_supported_by_fine_observation",
                "segment_id": segment.get("segment_id"), "window_id": window["window_id"],
                "selected_source_in_s": start, "selected_source_out_s": end,
                "selected_role_ids": deepcopy(roles),
                "cause": "roles_not_allowed_in_containing_range" if contained else "no_single_usable_range_contains_slice",
                "all_recorded_ranges_for_window": candidates})
        scope = {"kind": "continuous_window", "source_sha256": window["source_sha256"],
                 "source_start_s": start, "source_end_s": end}
        for entry in exhausted:
            if entry["scope"] == scope:
                blockers.append({"error": "goal:known_exhausted_slice_input_no_third_observation_select_new_evidence",
                    "segment_id": segment.get("segment_id"), "scope": scope,
                    "original_call": entry["original_call"], "repair_call": entry["repair_call"],
                    "no_replacement_cut_supplied": True})
    return blockers


def enforce_plan_ranges(plan, windows, exhausted):
    blockers = plan_diagnostics(plan, windows, exhausted)
    if blockers:
        raise ValueError(POLICY + ":" + json.dumps({"mechanical_blockers": blockers}, ensure_ascii=False))
