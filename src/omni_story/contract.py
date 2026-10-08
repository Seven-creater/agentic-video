"""Extracted structural checks; they do not prove semantic correctness."""
from __future__ import annotations
import math
import re

LEGACY_TIMES = {"start_s", "end_s", "estimated_duration_s", "target_duration_s"}
ACTUAL_TIMES = {"source_in_s", "source_out_s", "timeline_start_s", "timeline_end_s"}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def text(value: object, path: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), path + ":text_required")


def number(value: object, path: str, minimum: float = 0) -> float:
    require(type(value) in (int, float) and math.isfinite(value)
            and value >= minimum, path + ":finite_number_required")
    return float(value)


def rows(value: object, path: str, *, nonempty: bool = True) -> list:
    require(isinstance(value, list) and (bool(value) or not nonempty),
            path + ":list_required")
    return value


def ids(items: object, field: str, path: str, *, nonempty=True) -> set[str]:
    seen = set()
    for item in rows(items, path, nonempty=nonempty):
        require(isinstance(item, dict), path + ":object_required")
        text(item.get(field), path + "/" + field)
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item[field]) is not None,
                path + ":unsafe_id")
        require(item[field] not in seen, path + ":duplicate_id")
        seen.add(item[field])
    return seen


def refs(value: object, allowed: set[str], path: str, *, nonempty=False) -> None:
    values = rows(value, path, nonempty=nonempty)
    require(all(isinstance(v, str) for v in values), path + ":string_ids_required")
    require(len(values) == len(set(values)) and set(values) <= allowed,
            path + ":unknown_or_duplicate_ref")


def forbid_keys(value: object, forbidden: set[str]) -> None:
    if isinstance(value, dict):
        require(not set(value) & forbidden, "mixed_time_domains:" +
                ",".join(sorted(set(value) & forbidden)))
        for child in value.values():
            forbid_keys(child, forbidden)
    elif isinstance(value, list):
        for child in value:
            forbid_keys(child, forbidden)


def validate_assets(plan: dict, source: dict) -> dict[str, set[str]]:
    allowed = {}
    for collection, field in (("characters", "visible_identity"),
                              ("locations", "visible_description"),
                              ("props", "visible_description")):
        allowed[collection] = ids(plan.get(collection), "id", collection,
                                  nonempty=collection != "props")
        existing = {r["id"] for r in source[collection]}
        require(existing <= allowed[collection], collection + ":existing_id_removed")
        for item in plan[collection]:
            text(item.get(field), collection + "/" + field)
            if collection == "props":
                text(item.get("initial_state"), "props/initial_state")
    return allowed


def validate_outline(plan: dict, source: dict) -> None:
    require(plan.get("schema_version") == "story_plan_v2", "outline_schema")
    forbid_keys(plan, LEGACY_TIMES | ACTUAL_TIMES | {"generation_duration_s"})
    text(plan.get("title"), "title")
    text(plan.get("intended_takeaway"), "intended_takeaway")
    assets = validate_assets(plan, source)
    units = rows(plan.get("units"), "units")
    require(len(units) <= 8, "outline_unit_budget_exceeded")
    ids(units, "unit_id", "units")
    seen = set()
    for unit in units:
        require(re.fullmatch(r"U[0-9]+", unit["unit_id"]) is not None, "unit_id:U_number_required")
        refs(unit.get("caused_by"), seen, "caused_by")
        seen.add(unit["unit_id"])
        validate_asset_refs(unit, assets)
        for field in ("entry_state", "character_goal", "event_summary", "exit_state",
                      "intended_viewer_update"):
            text(unit.get(field), field)
        require(unit.get("elapsed_time_hint") is None or
                isinstance(unit["elapsed_time_hint"], str), "elapsed_time_hint:type")


def validate_asset_refs(item: dict, assets: dict) -> None:
    refs(item.get("character_ids"), assets["characters"], "character_ids", nonempty=True)
    refs(item.get("prop_ids"), assets["props"], "prop_ids")
    require(item.get("location_id") in assets["locations"], "location_id:unknown")


def validate_segment(segment: dict, planned: dict, plan: dict) -> None:
    require(segment.get("schema_version") == "story_segment_v2", "segment_schema")
    forbid_keys(segment, LEGACY_TIMES | ACTUAL_TIMES | {"generation_duration_s"})
    require(segment.get("unit_id") == planned["unit_id"], "unit_id:changed")
    require(segment.get("caused_by") == planned["caused_by"], "caused_by:changed")
    assets = {k: {r["id"] for r in plan[k]} for k in ("characters", "props", "locations")}
    validate_asset_refs(segment, assets)
    require(segment["location_id"] == planned["location_id"], "location_id:changed")
    for field in ("entry_state", "character_goal", "exit_state", "state_after",
                  "intended_viewer_update"):
        text(segment.get(field), field)
    for field in ("dialogue_or_voiceover", "on_screen_text", "elapsed_time_hint"):
        require(segment.get(field) is None or isinstance(segment[field], str), field + ":type")
    ids(segment.get("events"), "event_id", "events")
    for event in segment["events"]:
        require(event["event_id"].startswith(segment["unit_id"] + "_"), "event_id:unit_prefix")
        for field in ("before", "action", "after", "observable_evidence"):
            text(event.get(field), "events/" + field)
        require(type(event.get("essential")) is bool, "events/essential:boolean")
    for value in rows(segment.get("omittable_processes"), "omittable_processes", nonempty=False):
        text(value, "omittable_processes")



