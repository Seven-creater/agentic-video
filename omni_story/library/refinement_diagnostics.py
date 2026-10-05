"""Read-only local format diagnostics; never normalize a reply or grant a pass."""
from copy import deepcopy
from math import isfinite


def diagnostics(value, draft):
    """Collect independent mechanical errors without source/context assumptions."""
    errors = []
    def add(path, code, expected, actual):
        errors.append(dict(path=path, code=code, expected=deepcopy(expected), actual=deepcopy(actual)))
    def blocked(path, dependency):
        add(path, "blocked", "valid " + dependency, None)
    def enum(row, key, choices, path):
        if not isinstance(row.get(key), str) or row[key] not in choices:
            add(path + "." + key, "enum", sorted(choices), row.get(key))
    def texts(row, keys, path):
        for key in keys:
            if not isinstance(row.get(key), str) or not row[key].strip():
                add(path + "." + key, "text", "nonempty string", row.get(key))
    def number(raw, path, low=0, high=None, positive=False):
        try:
            finite = type(raw) in (int, float) and isfinite(raw)
        except OverflowError:
            finite = False
        if not finite or raw < low or (positive and raw <= 0) or (high is not None and raw > high):
            add(path, "number", dict(min=low, max=high, positive=positive, finite=True), raw)
            return None
        return raw
    def table(raw, key, path, nonempty=True):
        if not isinstance(raw, list):
            add(path, "type", "array", raw)
            return None
        if nonempty and not raw:
            add(path, "count", "at least one row", 0)
        found = {}
        for i, row in enumerate(raw):
            p = f"{path}[{i}]"
            if not isinstance(row, dict):
                add(p, "type", "object", row)
                continue
            ident = row.get(key)
            if not isinstance(ident, str) or not ident.strip():
                add(p + "." + key, "id", "nonempty string", ident)
            elif ident in found:
                add(p + "." + key, "duplicate_id", "unique ID", ident)
            else:
                found[ident] = (row, p)
        return found
    def refs(raw, allowed, path, nonempty=False):
        if not isinstance(raw, list) or any(not isinstance(x, str) or not x.strip() for x in raw):
            add(path, "refs", "array of string IDs", raw)
            return set()
        if allowed is None:
            blocked(path, "referenced IDs")
            return set()
        if len(set(raw)) != len(raw) or not set(raw) <= set(allowed) or (nonempty and not raw):
            add(path, "refs", dict(allowed=sorted(allowed), nonempty=nonempty, unique=True), raw)
        return set(raw)
    def coverage(found, expected, path):
        if found is None or expected is None:
            blocked(path, "row IDs")
        elif set(found) != set(expected):
            add(path, "coverage", sorted(expected), sorted(found))

    if not isinstance(value, dict):
        add("$", "type", "object", value)
        blocked("$.fields", "root object")
        return errors
    candidate = next(((value[k], "$." + k) for k in ("draft", "original_draft") if isinstance(value.get(k), dict)), (None, None))
    fields = {}
    for key, kind in (("plan", dict), ("decisions", list), ("obligation_coverage", list), ("draft_dispositions", list), ("duration", dict), ("timing_checks", list), ("transition_checks", list)):
        raw, path = value.get(key), "$." + key
        if not isinstance(raw, kind):
            add(path, "missing" if key not in value else "type", "object" if kind is dict else "array", raw)
            nested = candidate[0] if key == "plan" else candidate[0].get(key) if candidate[0] is not None else None
            if isinstance(nested, kind):
                raw, path = nested, candidate[1] + ("" if key == "plan" else "." + key)
                add("$." + key, "wrong_location", "$." + key, path)
            else:
                raw = None
        fields[key] = (raw, path)
    plan, pp = fields["plan"]
    segments = table(plan.get("segments"), "segment_id", pp + ".segments") if plan is not None else None
    slots = table(plan.get("slots"), "slot_id", pp + ".slots") if plan is not None else None
    originals = table(draft.get("segments"), "segment_id", "original_draft.segments") if isinstance(draft, dict) else None
    obligations = table(draft.get("slots"), "slot_id", "original_draft.slots") if isinstance(draft, dict) else None
    methods = table(draft.get("editing_bindings", []), "method_id", "original_draft.editing_bindings", False) if isinstance(draft, dict) else None
    if plan is None:
        blocked("$.plan.segments", "plan object")
    else:
        enum(plan, "audio_mode", {"reference", "source", "mix", "silent"}, pp)
    transforms, claims = {}, {}
    for sid, (segment, path) in (segments or {}).items():
        refs([segment.get("slot_id")], slots, path + ".slot_id")
        enum(segment, "look", {"none", "grayscale"}, path)
        enum(segment, "framing", {"fit", "crop"}, path)
        start, end = (number(segment.get(k), path + "." + k) for k in ("source_in_s", "source_out_s"))
        speed = number(segment.get("speed", 1), path + ".speed", .5, 2)
        hold = number(segment.get("freeze_tail_s", 0), path + ".freeze_tail_s", 0, 10)
        if None not in (start, end, speed, hold):
            if start >= end:
                add(path, "source_interval", "source_in_s < source_out_s", [start, end])
            else:
                transforms[sid] = (start, end, speed, hold)
        claims[sid] = table(segment.get("visual_claims", []), "claim_id", path + ".visual_claims", False)
        for claim, cp in (claims[sid] or {}).values():
            enum(claim, "kind", {"visual_action", "visual_outcome", "identity"}, cp)
            texts(claim, ("description",), cp)
    if segments is not None and len(segments) > 32:
        add(pp + ".segments", "count", "at most 32; allocation checked separately", len(segments))
    for slot_id, (slot, path) in (slots or {}).items():
        refs(slot.get("segment_ids"), segments, path + ".segment_ids", True)
        if segments is not None and isinstance(slot.get("segment_ids"), list):
            owned = sorted(sid for sid, (segment, _) in segments.items() if segment.get("slot_id") == slot_id)
            if slot["segment_ids"] != owned and sorted(x for x in slot["segment_ids"] if isinstance(x, str)) != owned:
                add(path + ".segment_ids", "slot_assignment", owned, slot["segment_ids"])
        if obligations is not None and slot_id in obligations and slot.get("intended_takeaway") != obligations[slot_id][0].get("intended_takeaway"):
            add(path + ".intended_takeaway", "original_obligation_changed", obligations[slot_id][0].get("intended_takeaway"), slot.get("intended_takeaway"))
    for key, idkey, expected in (("decisions", "segment_id", segments), ("obligation_coverage", "slot_id", obligations), ("draft_dispositions", "segment_id", originals), ("timing_checks", "segment_id", segments)):
        raw, path = fields[key]
        found = table(raw, idkey, path)
        coverage(found, expected, path)
        for i, row in enumerate(raw if isinstance(raw, list) else []):
            if not isinstance(row, dict):
                continue
            ident, rp = row.get(idkey) if isinstance(row.get(idkey), str) else None, f"{path}[{i}]"
            if key == "decisions":
                enum(row, "origin", {"general_optimization", "reference_transfer"}, rp)
                texts(row, ("new_information", "in_out_reason", "speed_reason", "hold_reason"), rp)
                chosen = refs(row.get("reference_method_ids"), methods, rp + ".reference_method_ids", row.get("origin") == "reference_transfer")
                if row.get("origin") == "general_optimization" and chosen:
                    add(rp + ".reference_method_ids", "optimization_method_refs", [], sorted(chosen))
            elif key == "obligation_coverage":
                enum(row, "status", {"preserved", "unresolved"}, rp)
                refs(row.get("segment_ids"), segments, rp + ".segment_ids", row.get("status") == "preserved")
                texts(row, ("reason",), rp)
            elif key == "draft_dispositions":
                enum(row, "decision", {"retained", "replaced", "removed"}, rp)
                chosen = refs(row.get("replacement_segment_ids"), segments, rp + ".replacement_segment_ids", row.get("decision") == "replaced")
                texts(row, ("reason",), rp)
                if row.get("decision") == "removed" and (chosen or segments is not None and ident in segments):
                    add(rp, "removed_still_selected", "no selected segment or replacements", ident)
                if row.get("decision") == "retained" and segments is not None and originals is not None and ident in originals:
                    keys = ("source_id", "window_id", "source_in_s", "source_out_s")
                    if ident not in segments or any(originals[ident][0].get(k) != segments[ident][0].get(k) for k in keys):
                        add(rp, "retained_source_changed", "unchanged source/window/in/out", ident)
            else:
                enum(row, "purpose", {"setup", "action", "result", "reaction", "identity", "context"}, rp)
                texts(row, ("reason",), rp)
                minimum = number(row.get("min_readable_s"), rp + ".min_readable_s", positive=True)
                intervals = row.get("essential_source_intervals")
                if not isinstance(intervals, list) or not intervals:
                    add(rp + ".essential_source_intervals", "type", "nonempty array", intervals)
                    continue
                covered, spans, complete = set(), [], True
                for i, interval in enumerate(intervals):
                    ip = f"{rp}.essential_source_intervals[{i}]"
                    if not isinstance(interval, dict):
                        add(ip, "type", "object", interval); complete = False; continue
                    texts(interval, ("visible_information",), ip)
                    covered |= refs(interval.get("claim_ids"), claims.get(ident), ip + ".claim_ids")
                    left, right = (number(interval.get(k), ip + "." + k) for k in ("source_start_s", "source_end_s"))
                    limit = number(interval.get("min_readable_s"), ip + ".min_readable_s", positive=True)
                    if ident not in transforms or left is None or right is None:
                        blocked(ip + ".exposure", "valid selected/source interval"); complete = False; continue
                    start, end, speed, hold = transforms[ident]
                    if not start <= left < right <= end:
                        add(ip, "interval_outside_source", [start, end], [left, right]); complete = False; continue
                    spans.append((left, right))
                    exposure = (right - left) / speed + (hold if right == end else 0)
                    if limit is not None and exposure + 1e-9 < limit:
                        add(ip, "interval_exposure", {"minimum_s": limit}, {"exposure_s": exposure})
                coverage({cid: None for cid in covered}, claims.get(ident), rp + ".claim_ids")
                if complete and spans and ident in transforms and minimum is not None:
                    start, end, speed, hold = transforms[ident]
                    union, last = 0, None
                    for left, right in sorted(spans):
                        union += max(0, right - last) if last is not None and left <= last else right - left
                        last = max(last, right) if last is not None else right
                    exposure = union / speed + (hold if any(right == end for _, right in spans) else 0)
                    if exposure + 1e-9 < minimum:
                        add(rp, "union_exposure", {"minimum_s": minimum}, {"exposure_s": exposure})
                elif not complete or ident not in transforms:
                    blocked(rp + ".union_exposure", "all valid core intervals and selected transform")
    duration, dp = fields["duration"]
    if duration is not None:
        target = number(duration.get("target_s"), dp + ".target_s", high=180, positive=True)
        reported = number(duration.get("total_s"), dp + ".total_s", high=180, positive=True)
        reason = duration.get("over_target_reason")
        if not isinstance(reason, str):
            add(dp + ".over_target_reason", "type", "string", reason)
        if segments is None or len(transforms) != len(segments):
            blocked(dp + ".total_s", "all final transforms")
        else:
            total = sum((end - start) / speed + hold for start, end, speed, hold in transforms.values())
            if total > 180:
                add(dp + ".total_s", "duration_cap", "at most 180 seconds", total)
            if reported is not None and abs(total - reported) > .05:
                add(dp + ".total_s", "duration_math", total, reported)
            if target is not None and total > target + .05:
                texts(duration, ("over_target_reason",), dp)
    raw, tp = fields["transition_checks"]
    expected = list(zip(segments, list(segments)[1:])) if segments is not None else None
    found = set()
    if isinstance(raw, list):
        for i, row in enumerate(raw):
            path = f"{tp}[{i}]"
            if not isinstance(row, dict):
                add(path, "type", "object", row); continue
            enum(row, "status", {"planned", "unresolved"}, path)
            texts(row, ("relation", "reason"), path)
            pair = (row.get("from_segment_id"), row.get("to_segment_id"))
            if any(not isinstance(x, str) for x in pair) or pair in found or expected is not None and pair not in expected:
                add(path, "adjacency", expected, list(pair))
            elif all(isinstance(x, str) for x in pair):
                found.add(pair)
    if expected is None:
        blocked(tp, "final segment order")
    elif found != set(expected):
        add(tp, "coverage", [list(p) for p in expected], [list(p) for p in sorted(found)])
    return errors
