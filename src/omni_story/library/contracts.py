"""Structural evidence checks for the reference-first movie-library route.

Validation proves provenance and time-domain consistency, not story quality.
Coarse timestamps are search hints; only continuous, watched windows support EDLs.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from ..contract import require, text, number, rows, ids, refs, forbid_keys

_UNSPECIFIED_AUDIO_STREAM = object()


class PlanEvidenceError(ValueError):
    """Keep the original verdict string and attach deterministic source evidence."""
    def __init__(self, reason, diagnostics):
        super().__init__(reason)
        self.diagnostics = deepcopy(diagnostics)


def _plan_evidence_diagnostics(segment, window, start, end):
    offset = window['source_start_s']
    roles = segment['role_ids']
    return {'policy': 'plan_evidence_diagnostics_v1',
            'segment_id': segment['segment_id'], 'window_id': window['window_id'],
            'source_id': segment['source_id'], 'selected_source_interval_s': [start, end],
            'selected_local_interval_s': [start - offset, end - offset],
            'requested_role_ids': roles,
            'usable_ranges': [{'usable_range_index': index,
                'evidence': {key: usable[key] for key in ('local_in_s', 'local_out_s', 'event_indices',
                                                         'role_ids', 'continuity_notes')},
                'source_interval_s': [offset + usable['local_in_s'], offset + usable['local_out_s']],
                'contains_selected_source_interval':
                    offset + usable['local_in_s'] - 0.001 <= start and
                    end <= offset + usable['local_out_s'] + 0.001,
                'missing_requested_role_ids': [role for role in roles if role not in usable['role_ids']]}
                for index, usable in enumerate(window['observation']['usable_ranges'])]}


def plan_execution_diagnostics(data, windows):
    """Project every selection and known range; never repair or accept an EDL."""
    def json_safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return {'invalid_value': 'non_finite_number', 'representation': repr(value)}
        if isinstance(value, dict):
            return {key: json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [json_safe(item) for item in value]
        return value

    if isinstance(windows, dict):
        windows = list(windows.values())
    known = {window['window_id']: window for window in windows}
    table = []
    for window in windows:
        offset = window['source_start_s']
        observation = window['observation']
        table.append({'window_id': window['window_id'], 'source_id': window['source_id'],
            'source_start_s': offset, 'source_end_s': window['source_end_s'],
            'confirmed_role_ids': [role['role_id'] for role in observation['roles']
                                   if role['identity_confirmed']],
            'usable_ranges': [{'source_interval_s': [offset + usable['local_in_s'],
                                                     offset + usable['local_out_s']],
                               'local_interval_s': [usable['local_in_s'], usable['local_out_s']],
                               'role_ids': list(usable['role_ids'])}
                              for usable in observation['usable_ranges']]})
    selections = []
    bindings = []
    if isinstance(data, dict):
        for binding in data.get('focus_role_bindings', []) if isinstance(data.get('focus_role_bindings'), list) else []:
            if isinstance(binding, dict):
                window_id = binding.get('window_id')
                bindings.append({'window_id': window_id, 'role_id': binding.get('role_id'),
                    'known_window_id': isinstance(window_id, str) and window_id in known})
        for segment in data.get('segments', []) if isinstance(data.get('segments'), list) else []:
            if not isinstance(segment, dict):
                continue
            window_id = segment.get('window_id')
            window = known.get(window_id) if isinstance(window_id, str) else None
            start, end = segment.get('source_in_s'), segment.get('source_out_s')
            roles = segment.get('role_ids')
            matching = []
            if (window is not None and isinstance(roles, list)
                    and all(isinstance(role, str) for role in roles)
                    and all(type(value) in (int, float) and math.isfinite(value) for value in (start, end))):
                offset = window['source_start_s']
                matching = [index for index, usable in enumerate(window['observation']['usable_ranges'])
                    if offset + usable['local_in_s'] - 0.001 <= start < end
                    and end <= offset + usable['local_out_s'] + 0.001
                    and set(roles) <= set(usable['role_ids'])]
            selections.append({'segment_id': segment.get('segment_id'), 'window_id': window_id,
                'source_id': segment.get('source_id'), 'selected_source_interval_s': [start, end],
                'role_ids': roles, 'known_window_id': window is not None,
                'matching_single_usable_range_indices': matching})
    return deepcopy(json_safe({'policy': 'all_plan_execution_evidence_v1',
        'focus_bindings': bindings, 'selected_segments': selections, 'watched_window_table': table,
        'limit': 'Mechanical IDs/ranges/roles only; does not establish narrative or identity quality. '
                 'An empty matching list permits no automatic replacement, split or range expansion.'}))


def parse_model_json(value: str | dict) -> dict:
    if isinstance(value, dict):
        return value
    require(isinstance(value, str), "model_response:text_or_object_required")
    value = value.strip()
    if value.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n([\s\S]+?)\n```", value)
        require(match is not None, "model_response:single_json_object_required")
        value = match.group(1)
    result = json.loads(value)
    require(isinstance(result, dict), "model_response:object_required")
    return result


def _object(value, path):
    require(isinstance(value, dict), path + ":object_required")
    return value


def _interval(start, end, duration, path):
    start = number(start, path + "/start")
    end = number(end, path + "/end")
    require(start < end <= duration + 0.001, path + ":invalid_or_out_of_bounds")
    return start, end


def _strings(value, path, *, nonempty=False):
    for item in rows(value, path, nonempty=nonempty):
        text(item, path)


def _sources(catalog):
    _object(catalog, "catalog")
    source_ids = ids(catalog.get("sources"), "source_id", "catalog/sources")
    result = {}
    for source in catalog["sources"]:
        text(source.get("sha256"), "catalog/sha256")
        number(source.get("duration_s"), "catalog/duration_s", minimum=0.001)
        result[source["source_id"]] = source
    require(source_ids == set(result), "catalog:source_ids")
    return result


def validate_reference(data, reference_sha, duration_s):
    _object(data, "reference")
    require(data.get("reference_sha256") == reference_sha, "reference:sha_changed")
    text(data.get("theme"), "reference/theme")
    text(data.get("intended_takeaway"), "reference/intended_takeaway")
    for evidence in rows(data.get("visible_evidence"), "reference/visible_evidence"):
        _object(evidence, "reference/evidence")
        _interval(evidence.get("start_s"), evidence.get("end_s"), duration_s,
                  "reference/evidence")
        text(evidence.get("observed_fact"), "reference/observed_fact")
        text(evidence.get("supports"), "reference/supports")
    for method in rows(data.get("editing_methods"), "reference/editing_methods"):
        _object(method, "reference/editing_method")
        text(method.get("method"), "reference/method")
        text(method.get("function"), "reference/function")
        text(method.get("visual_evidence"), "reference/visual_evidence")
        _interval(method.get("start_s"), method.get("end_s"), duration_s,
                  "reference/editing_method")
    _strings(data.get("uncertainties"), "reference/uncertainties")
    return data


def validate_editing_reference(data, reference_sha, duration_s, reference_reading):
    """Validate an appended method specification without rewriting the reading.

    Method ranges are model estimates, not measured cut locations or proof of
    an editing technique. Every original method remains represented once.
    """
    _object(data, "editing_reference")
    validate_reference(reference_reading, reference_sha, duration_s)
    require(data.get("reference_sha256") == reference_sha, "editing_reference:sha_changed")
    for key in ("theme", "intended_takeaway"):
        if key in data:
            require(data[key] == reference_reading[key], "editing_reference:" + key + "_changed")
    ids(data.get("methods"), "method_id", "editing_reference/methods")
    original_methods = reference_reading["editing_methods"]
    original_indices = []
    for method in data["methods"]:
        index = method.get("reference_method_index")
        require(type(index) is int and 0 <= index < len(original_methods),
                "editing_reference:unknown_reference_method_index")
        original_indices.append(index)
        for key in ("form", "function", "verification_rule"):
            text(method.get(key), "editing_reference/" + key)
        _interval(method.get("source_start_s"), method.get("source_end_s"), duration_s,
                  "editing_reference/method")
        require(method.get("evidence_type") == "model_estimate",
                "editing_reference:model_estimate_required")
        require(type(method.get("requires_audio")) is bool,
                "editing_reference:requires_audio_boolean_required")
        _strings(method.get("material_requirements"), "editing_reference/material_requirements",
                 nonempty=True)
        _strings(method.get("uncertainties"), "editing_reference/method_uncertainties")
    require(len(original_indices) == len(original_methods)
            and set(original_indices) == set(range(len(original_methods))),
            "editing_reference:original_methods_must_be_covered_exactly_once")
    _strings(data.get("uncertainties"), "editing_reference/uncertainties")
    return data


def validate_coarse(data, catalog):
    _object(data, "coarse")
    sources = _sources(catalog)
    require(data.get("source_id") in sources, "coarse:unknown_source")
    coverage = rows(data.get("coverage_s"), "coarse/coverage_s")
    require(len(coverage) == 2, "coarse:coverage_pair_required")
    start, end = _interval(*coverage, sources[data["source_id"]]["duration_s"], "coarse/coverage")
    role_ids = ids(data.get("roles"), "role_id", "coarse/roles", nonempty=False)
    for role in data["roles"]:
        text(role.get("description"), "coarse/role_description")
    for event in rows(data.get("events"), "coarse/events", nonempty=False):
        _object(event, "coarse/event")
        timestamp = number(event.get("timestamp_s"), "coarse/timestamp_s")
        require(start <= timestamp <= end, "coarse:timestamp_outside_coverage")
        text(event.get("observed_fact"), "coarse/observed_fact")
        refs(event.get("role_ids"), role_ids, "coarse/event_roles")
    _strings(data.get("uncertainties"), "coarse/uncertainties")
    forbid_keys(data, {"source_in_s", "source_out_s", "local_in_s", "local_out_s"})
    return data


def validate_search(data, catalog, *, max_windows=12, max_window_s=90, allow_empty=False):
    _object(data, "search")
    sources = _sources(catalog)
    text(data.get("reason"), "search/reason")
    requests = rows(data.get("windows"), "search/windows", nonempty=not allow_empty)
    if max_windows is not None:
        require(len(requests) <= max_windows, "search:too_many_windows")
    for request in requests:
        _object(request, "search/window")
        require(request.get("source_id") in sources, "search:unknown_source")
        start, end = _interval(request.get("start_s"), request.get("end_s"),
                               sources[request["source_id"]]["duration_s"], "search/window")
        require(end - start <= max_window_s, "search:window_too_long")
        text(request.get("question"), "search/question")
        _strings(request.get("role_ids"), "search/role_ids")
    return data


def validate_fine(data, window):
    _object(data, "fine")
    _object(window, "window")
    require(data.get("window_id") == window.get("window_id"), "fine:window_id_mismatch")
    require(data.get("source_id") == window.get("source_id"), "fine:source_id_mismatch")
    start = number(window.get("source_start_s"), "window/source_start_s")
    end = number(window.get("source_end_s"), "window/source_end_s")
    require(start < end, "window:invalid_range")
    duration = end - start
    role_ids = ids(data.get("roles"), "role_id", "fine/roles", nonempty=False)
    confirmed = set()
    for role in data["roles"]:
        require(type(role.get("identity_confirmed")) is bool, "fine:identity_confirmation_required")
        text(role.get("state"), "fine/role_state")
        text(role.get("identity_evidence"), "fine/identity_evidence")
        if role["identity_confirmed"]:
            confirmed.add(role["role_id"])
    events = rows(data.get("events"), "fine/events", nonempty=False)
    for event in events:
        _object(event, "fine/event")
        _interval(event.get("local_start_s"), event.get("local_end_s"), duration, "fine/event")
        text(event.get("observed_fact"), "fine/observed_fact")
        refs(event.get("role_ids"), role_ids, "fine/event_roles")
    for usable in rows(data.get("usable_ranges"), "fine/usable_ranges", nonempty=False):
        _object(usable, "fine/usable_range")
        usable_start, usable_end = _interval(usable.get("local_in_s"), usable.get("local_out_s"), duration,
                                            "fine/usable")
        refs(usable.get("role_ids"), confirmed, "fine/usable_confirmed_roles")
        indices = rows(usable.get("event_indices"), "fine/event_indices")
        require(all(type(index) is int and 0 <= index < len(events) for index in indices),
                "fine:unknown_event_index")
        require(all(events[index]["local_start_s"] < usable_end
                    and usable_start < events[index]["local_end_s"] for index in indices),
                "fine:usable_range_event_does_not_overlap")
        event_roles = {role for index in indices for role in events[index]["role_ids"]}
        require(set(usable["role_ids"]) <= event_roles, "fine:usable_role_missing_event_evidence")
        text(usable.get("continuity_notes"), "fine/continuity_notes")
    _strings(data.get("uncertainties"), "fine/uncertainties")
    forbid_keys(data, {"source_in_s", "source_out_s", "timestamp_s"})
    return data


def validate_plan(data, catalog, windows, reference_sha, reference_duration_s=None, *,
                  reference_audio_stream_index=_UNSPECIFIED_AUDIO_STREAM, editing_reference=None,
                  max_duration_s=180, max_segments=32):
    _object(data, "plan")
    require(data.get("reference_sha256") == reference_sha, "plan:reference_sha_changed")
    sources = _sources(catalog)
    text(data.get("focus_role_id"), "plan/focus_role_id")
    if isinstance(windows, list):
        ids(windows, "window_id", "windows", nonempty=False)
        windows = {window["window_id"]: window for window in windows}
    _object(windows, "windows")
    focus_bindings = {}
    for binding in rows(data.get("focus_role_bindings"), "plan/focus_role_bindings"):
        _object(binding, "plan/focus_binding")
        window_id = binding.get("window_id")
        require(window_id in windows, "plan:focus_binding_unknown_window")
        require(window_id not in focus_bindings, "plan:duplicate_focus_binding_window")
        window = windows[window_id]
        require(window.get("status") == "watched", "plan:focus_binding_window_not_watched")
        require(window.get("source_id") in sources, "plan:focus_binding_unknown_source")
        require(window.get("source_sha256") == sources[window["source_id"]]["sha256"],
                "plan:focus_binding_source_sha_changed")
        observation = validate_fine(window.get("observation"), window)
        confirmed = {role["role_id"] for role in observation["roles"] if role["identity_confirmed"]}
        refs([binding.get("role_id")], confirmed, "plan/focus_binding_confirmed_role", nonempty=True)
        text(binding.get("identity_evidence"), "plan/focus_binding_identity_evidence")
        focus_bindings[window_id] = binding["role_id"]
    segment_ids = ids(data.get("segments"), "segment_id", "plan/segments")
    if max_segments is not None:
        require(len(segment_ids) <= max_segments, f"plan:segment_count_exceeds_{max_segments}")
    slot_ids = ids(data.get("slots"), "slot_id", "plan/slots")
    assigned = []
    for slot in data["slots"]:
        text(slot.get("intended_takeaway"), "plan/slot_takeaway")
        refs(slot.get("segment_ids"), segment_ids, "plan/slot_segments", nonempty=True)
        assigned.extend(slot["segment_ids"])
    require(len(assigned) == len(set(assigned)) and set(assigned) == segment_ids,
            "plan:segments_must_belong_to_exactly_one_slot")
    focal_seen = False
    segment_durations = []
    for segment in data["segments"]:
        require(segment.get("slot_id") in slot_ids, "plan:unknown_slot")
        require(segment["segment_id"] in next(slot["segment_ids"] for slot in data["slots"]
                                                if slot["slot_id"] == segment["slot_id"]),
                "plan:slot_assignment_mismatch")
        source_id, window_id = segment.get("source_id"), segment.get("window_id")
        require(source_id in sources, "plan:unknown_source")
        require(window_id in windows, "plan:unknown_window")
        window = windows[window_id]
        require(window.get("window_id") == window_id and window.get("source_id") == source_id,
                "plan:window_source_mismatch")
        require(window.get("source_sha256") == sources[source_id]["sha256"], "plan:source_sha_changed")
        require(window.get("status") == "watched", "plan:window_not_fine_watched")
        _interval(window.get("source_start_s"), window.get("source_end_s"),
                  sources[source_id]["duration_s"], "plan/window")
        observation = validate_fine(window.get("observation"), window)
        start, end = _interval(segment.get("source_in_s"), segment.get("source_out_s"),
                               sources[source_id]["duration_s"], "plan/segment")
        require(window["source_start_s"] - 0.001 <= start < end <= window["source_end_s"] + 0.001,
                "plan:segment_outside_watched_window")
        confirmed = {role["role_id"] for role in observation["roles"] if role["identity_confirmed"]}
        refs(segment.get("role_ids"), confirmed, "plan/confirmed_roles")
        acceptable = [usable for usable in observation["usable_ranges"]
                      if window["source_start_s"] + usable["local_in_s"] - 0.001 <= start
                      and end <= window["source_start_s"] + usable["local_out_s"] + 0.001
                      and set(segment["role_ids"]) <= set(usable["role_ids"])]
        if not acceptable:
            raise PlanEvidenceError("plan:range_not_supported_by_fine_observation",
                                    _plan_evidence_diagnostics(segment, window, start, end))
        local_start = start - window["source_start_s"]
        local_end = end - window["source_start_s"]
        overlapping_roles = {role for event in observation["events"]
                             if event["local_start_s"] < local_end and local_start < event["local_end_s"]
                             for role in event["role_ids"]}
        require(set(segment["role_ids"]) <= overlapping_roles,
                "plan:segment_role_missing_overlapping_event_evidence")
        focal_seen |= focus_bindings.get(window_id) in segment["role_ids"]
        speed = number(segment.get("speed"), "plan/speed")
        require(0.5 <= speed <= 2, "plan:speed_out_of_range")
        freeze_tail = number(segment.get("freeze_tail_s", 0), "plan/freeze_tail_s")
        require(freeze_tail <= 10, "plan:freeze_tail_exceeds_10_seconds")
        segment_duration = (end - start) / speed + freeze_tail
        segment_durations.append(segment_duration)
        if segment.get("caption") is not None:
            caption = _object(segment["caption"], "plan/caption")
            require(not set(caption) - {"text", "start_s", "end_s", "position", "font_size", "evidence"},
                    "plan:unsupported_caption_field")
            caption_text = caption.get("text")
            text(caption_text, "plan/caption/text")
            require(len(caption_text) <= 300, "plan:caption_text_exceeds_300_characters")
            require(all((ord(character) >= 32 and character != "\x7f") or character == "\n"
                        for character in caption_text),
                    "plan:caption_text_has_control_character")
            caption_start, caption_end = _interval(caption.get("start_s"), caption.get("end_s"),
                                                   segment_duration, "plan/caption")
            require(caption_end <= segment_duration + 1e-9, "plan:caption_exceeds_nominal_duration")
            caption_fps = data.get("fps")
            require(type(caption_fps) is int and 1 <= caption_fps <= 60,
                    "plan:fps_integer_1_to_60_required")
            output_duration = (round((end - start) / speed * caption_fps)
                               + round(freeze_tail * caption_fps)) / caption_fps
            require(caption_end <= output_duration + 1e-9, "plan:caption_exceeds_quantized_duration")
            require(round(caption_end * caption_fps) > round(caption_start * caption_fps),
                    "plan:caption_empty_quantized_interval")
            require(isinstance(caption.get("position"), str)
                    and caption["position"] in {"top", "center", "bottom"},
                    "plan:unsupported_caption_position")
            font_size = caption.get("font_size")
            require(type(font_size) is int and 12 <= font_size <= 120,
                    "plan:caption_font_size_integer_12_to_120_required")
            for evidence in rows(caption.get("evidence"), "plan/caption/evidence"):
                _object(evidence, "plan/caption/evidence")
                require(evidence.get("window_id") == window_id,
                        "plan:caption_evidence_window_mismatch")
                event_indices = rows(evidence.get("event_indices"), "plan/caption/event_indices")
                require(all(type(index) is int and 0 <= index < len(observation["events"])
                            for index in event_indices), "plan:caption_unknown_event_index")
                require(len(event_indices) == len(set(event_indices)),
                        "plan:caption_duplicate_event_index")
                if not all(observation["events"][index]["local_start_s"] < local_end
                           and local_start < observation["events"][index]["local_end_s"]
                           for index in event_indices):
                    diagnostic = _plan_evidence_diagnostics(segment, window, start, end)
                    diagnostic['caption_evidence'] = {'window_id': evidence['window_id'],
                                                      'event_indices': event_indices}
                    diagnostic['cited_events'] = [{'event_index': index,
                        'evidence': {key: observation['events'][index][key] for key in
                                     ('local_start_s', 'local_end_s', 'role_ids', 'observed_fact')},
                        'source_interval_s': [window['source_start_s'] + observation['events'][index]['local_start_s'],
                                              window['source_start_s'] + observation['events'][index]['local_end_s']],
                        'overlaps_selected_interval': observation['events'][index]['local_start_s'] < local_end and
                                                     local_start < observation['events'][index]['local_end_s']}
                        for index in event_indices]
                    raise PlanEvidenceError("plan:caption_event_outside_selected_range", diagnostic)
        require(segment.get("look") in {"none", "grayscale"}, "plan:unsupported_look")
        require(segment.get("framing") in {"fit", "crop"}, "plan:unsupported_framing")
        forbid_keys(segment, {"local_in_s", "local_out_s", "timestamp_s"})
    if max_duration_s is not None:
        require(math.fsum(segment_durations) <= max_duration_s,
                f"plan:duration_exceeds_{max_duration_s}_seconds")
    require(focal_seen, "plan:focus_role_not_confirmed_in_selected_footage")
    require(data.get("audio_mode") in {"reference", "source", "mix", "silent"}, "plan:audio_mode")
    for key in ("source_gain_db", "reference_gain_db"):
        value = data.get(key)
        require(type(value) in (int, float) and -60 <= value <= 12, "plan:" + key)
    if data["audio_mode"] in {"reference", "mix"}:
        if reference_audio_stream_index is not _UNSPECIFIED_AUDIO_STREAM:
            require(reference_audio_stream_index is not None, "plan:reference_has_no_audio")
            require(type(reference_audio_stream_index) is int and reference_audio_stream_index >= 0,
                    "plan:actual_reference_audio_stream_invalid")
        require(reference_duration_s is not None, "plan:reference_duration_required")
        audio = _object(data.get("reference_audio"), "plan/reference_audio")
        _interval(audio.get("start_s"), audio.get("end_s"), reference_duration_s, "plan/reference_audio")
        require(type(audio.get("stream_index")) is int and audio["stream_index"] >= 0,
                "plan:reference_audio_stream")
        if reference_audio_stream_index is not _UNSPECIFIED_AUDIO_STREAM:
            require(audio["stream_index"] == reference_audio_stream_index,
                    "plan:reference_audio_stream_mismatch")
        require(type(audio.get("loop")) is bool, "plan:reference_audio_loop")
    for key in ("width", "height"):
        value = data.get(key)
        require(type(value) is int and 2 <= value <= 3840 and value % 2 == 0, "plan:" + key)
    fps = data.get("fps")
    require(type(fps) is int and 1 <= fps <= 60, "plan:fps_integer_1_to_60_required")
    if editing_reference is not None or any(segment.get("caption") is not None
                                           or segment.get("freeze_tail_s", 0) > 0
                                           for segment in data["segments"]):
        output_frames = sum(round((segment["source_out_s"] - segment["source_in_s"])
                                  / segment["speed"] * fps) + round(segment.get("freeze_tail_s", 0) * fps)
                            for segment in data["segments"])
        if max_duration_s is not None:
            require(output_frames / fps <= max_duration_s,
                    f"plan:quantized_duration_exceeds_{max_duration_s}_seconds")
    _strings(data.get("limitations"), "plan/limitations")
    if editing_reference is not None:
        _object(editing_reference, "editing_reference")
        require(editing_reference.get("reference_sha256") == reference_sha,
                "plan:editing_reference_sha_changed")
        method_ids = ids(editing_reference.get("methods"), "method_id", "editing_reference/methods")
        binding_ids = ids(data.get("editing_bindings"), "method_id", "plan/editing_bindings")
        require(binding_ids == method_ids, "plan:editing_methods_must_be_bound_exactly_once")
        edl_order = {segment["segment_id"]: index for index, segment in enumerate(data["segments"])}
        for binding in data["editing_bindings"]:
            status = binding.get("status")
            require(isinstance(status, str) and status in {"planned", "unavailable", "unverifiable"},
                    "plan:editing_binding_status")
            refs(binding.get("segment_ids"), segment_ids, "plan/editing_binding_segments",
                 nonempty=status == "planned")
            positions = [edl_order[segment_id] for segment_id in binding["segment_ids"]]
            require(positions == sorted(positions), "plan:editing_binding_segments_not_in_edl_order")
            for key in ("intended_relation", "operation", "verification"):
                text(binding.get(key), "plan/editing_binding/" + key)
            _strings(binding.get("limitations"), "plan/editing_binding/limitations",
                     nonempty=status != "planned")
    return data


def validate_blind_reading(data, duration_s):
    _object(data, "blind")
    for key in ("observed_story", "apparent_theme"):
        text(data.get(key), "blind/" + key)
    _strings(data.get("main_characters"), "blind/main_characters")
    for evidence in rows(data.get("evidence"), "blind/evidence"):
        _object(evidence, "blind/evidence")
        _interval(evidence.get("start_s"), evidence.get("end_s"), duration_s, "blind/evidence")
        text(evidence.get("observed_fact"), "blind/observed_fact")
    _strings(data.get("confusions"), "blind/confusions")
    return data


def validate_review(data, reference_sha):
    _object(data, "review")
    require(data.get("reference_sha256") == reference_sha, "review:reference_sha_changed")
    for key in ("theme_status", "editing_status", "continuity_status"):
        require(data.get(key) in {"pass", "partial", "fail", "unverifiable"}, "review:" + key)
    _strings(data.get("evidence"), "review/evidence", nonempty=True)
    _strings(data.get("limitations"), "review/limitations")
    _strings(data.get("revision_requests"), "review/revision_requests")
    return data
