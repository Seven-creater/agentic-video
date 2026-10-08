"""All recorded caption-time conflicts; no replacement evidence or edit choices."""
from __future__ import annotations

import math

POLICY = "goal_caption_time_diagnostics_v1"


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def diagnostics(plan, windows):
    """Mirror strict overlap checks without mutating or completing model fields.

    Missing or malformed data remains the original structural validator's job.
    Ranges describe the recorded event, not independently confirmed footage.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("segments"), list):
        return []
    if isinstance(windows, list):
        windows = {w["window_id"]: w for w in windows
                   if isinstance(w, dict) and isinstance(w.get("window_id"), str)}
    if not isinstance(windows, dict):
        return []
    fps = plan.get("fps")
    if type(fps) is not int or not 1 <= fps <= 60:
        return []
    conflicts = []
    for segment in plan["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("window_id"), str) \
                or not isinstance(segment.get("segment_id"), str):
            continue
        caption = segment.get("caption")
        window = windows.get(segment["window_id"])
        if not isinstance(caption, dict) or not isinstance(window, dict):
            continue
        observation = window.get("observation")
        if not isinstance(observation, dict) or not isinstance(observation.get("events"), list):
            continue
        start, end, speed = (segment.get(k) for k in ("source_in_s", "source_out_s", "speed"))
        a, b = caption.get("start_s"), caption.get("end_s")
        offset = window.get("source_start_s")
        if not all(_finite(v) for v in (start, end, speed, a, b, offset)) \
                or not (0 <= start < end and speed > 0 and 0 <= a < b):
            continue
        # Identical to semantic_audit.validate_caption_temporal_evidence:
        # a frozen tail exposes the final real source frame, not later footage.
        last_frame_start = max(start, end - speed / fps)
        display_start = min(start + a * speed, last_frame_start)
        display_end = min(start + b * speed, end)
        bindings = caption.get("evidence")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict) or binding.get("window_id") != segment["window_id"] \
                    or not isinstance(binding.get("event_indices"), list):
                continue
            for index in binding["event_indices"]:
                events = observation["events"]
                if type(index) is not int or not 0 <= index < len(events) or not isinstance(events[index], dict):
                    continue
                event = events[index]
                local_a, local_b = event.get("local_start_s"), event.get("local_end_s")
                if not _finite(local_a) or not _finite(local_b) or not 0 <= local_a < local_b:
                    continue
                event_start, event_end = offset + local_a, offset + local_b
                errors = []
                if not (event_start < end and start < event_end):
                    errors.append("plan:caption_event_outside_selected_range")
                if not (event_start < display_end and display_start < event_end):
                    errors.append("semantic:caption_evidence_not_visible_during_display")
                if errors:
                    conflicts.append({"segment_id": segment["segment_id"],
                        "window_id": segment["window_id"], "event_index": index,
                        "selected_source_range": [start, end],
                        "caption_output_local_range": [a, b],
                        "caption_source_display_range": [display_start, display_end],
                        "recorded_event_source_range": [event_start, event_end],
                        "errors": errors})
    return conflicts
