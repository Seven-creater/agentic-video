"""Structural evidence checks for the reference-first movie-library route.

Validation proves provenance and time-domain consistency, not story quality.
Coarse timestamps are search hints; only continuous, watched windows support EDLs.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from ..contract import require, text, number as _number, rows, ids, refs, forbid_keys

_UNSPECIFIED_AUDIO_STREAM = object()


def creative_time_normalization(configuration):
    return (configuration.get('provider') == 'official_vision_mcp_in_opencode'
            and configuration.get('creative_audio') is True)


def time_normalization_enabled(state):
    return (getattr(state, 'normalize_observation_times', False) is True or
            creative_time_normalization(getattr(state, 'input_lock', {}).get('configuration', {})))


def normalized_view(data, validator):
    """Return a deterministic validated view without changing the supplied record."""
    value = deepcopy(data)
    validator(value)
    return value


def observation_content(value):
    """Normalization history is audit metadata, absent in some legacy records."""
    return {key: item for key, item in value.items() if key != 'normalization'} if isinstance(value, dict) else value


def same_normalized(left, right):
    if observation_content(left) != observation_content(right):
        return False
    return (not isinstance(left, dict) or not left.get('normalization') or not right.get('normalization')
            or left['normalization'] == right['normalization'])


def same_observation(left, right, validator):
    return same_normalized(normalized_view(left, validator), normalized_view(right, validator))


def recorded_view(raw, saved, validator, *, changed, incompatible='recorded_reply_incompatible_with_current_contract'):
    """Check both views, including when today's contract rejects the old reply."""
    from .state import LibraryStopped
    views, errors = [], []
    for original in (raw, saved):
        value = deepcopy(original)
        try:
            validator(value)
        except (ValueError, TypeError, KeyError) as error:
            errors.append(error)
        views.append(value)
    if not same_normalized(*views):
        raise LibraryStopped(changed)
    if errors:
        raise LibraryStopped(incompatible + ':' + str(errors[0])) from errors[0]
    return views[0]


def numeric_error(reason, path, value, expected):
    error = ValueError(reason)
    if type(value) is float and not math.isfinite(value):
        value = repr(value)
    error.violation = dict(path=path, value=value, expected=expected)
    return error


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def number(value, path, minimum=0):
    try:
        return _number(value, path, minimum)
    except (ValueError, OverflowError) as error:
        error.violation = numeric_error(str(error), path, value, f'finite number, {minimum} ≤ x').violation
        raise


def interval_bounds(start, end, lower, upper, path, reason, *, tolerance=0,
                    fields=('start_s', 'end_s'), points=False):
    for field, value in zip(fields, (start, end)):
        if (not finite_number(value) or
                not lower <= value <= upper + tolerance):
            raise numeric_error(reason, path + '.' + field, value, f'{lower} ≤ x ≤ {upper}')
    if not (start <= end if points else start < end):
        raise numeric_error(reason, path, [start, end],
                            f'{lower} ≤ {fields[0]} {"≤" if points else "<"} {fields[1]} ≤ {upper}')


def normalize_intervals(data, parent, key, duration, *, fields=('start_s', 'end_s'), path=None, offset=0):
    """Clamp local observations, optionally expressed through a trusted source offset."""
    if not isinstance(parent, dict) or not isinstance(parent.get(key), list):
        return {}
    path = path or '$.' + key
    kept, indices = [], {}
    for index, row in enumerate(parent[key]):
        if isinstance(row, dict) and any(field in row for field in fields):
            start, end = (row.get(field) for field in fields)
            if all(finite_number(value) for value in (start, end)):
                start, end = (max(offset, min(offset + duration, value)) for value in (start, end))
                item_path = f'{path}[{index}]'
                if start >= end:
                    rows(data.setdefault('normalization', []), 'reference/normalization', nonempty=False).append(
                        dict(path=item_path, original=deepcopy(row), dropped=True))
                    continue
                for field, normalized in zip(fields, (start, end)):
                    if row[field] != normalized:
                        rows(data.setdefault('normalization', []), 'reference/normalization', nonempty=False).append(
                            dict(path=item_path + '.' + field, original=row[field], normalized=normalized))
                        row[field] = normalized
        indices[index] = len(kept)
        kept.append(row)
    parent[key][:] = kept
    return indices


class PlanEvidenceError(ValueError):
    """Keep the original verdict string and attach deterministic source evidence."""
    def __init__(self, reason, diagnostics, *, violation=None):
        super().__init__(reason)
        self.diagnostics = deepcopy(diagnostics)
        if isinstance(violation, dict):
            self.violation = deepcopy(violation)


class ReviewContractError(ValueError):
    """Retain a review rejection and describe its required list element types."""
    def __init__(self, reason, diagnostics):
        super().__init__(reason)
        self.diagnostics = deepcopy(diagnostics)


def review_output_contract(*, require_core_meaning=False):
    """Describe the existing review protocol without supplying observations."""
    contract = {'policy': 'review_output_contract_v1',
        'reference_sha256': {'type': 'string', 'must_match': 'input reference SHA'},
        'status_fields': {name: {'type': 'string',
            'allowed': ['pass', 'partial', 'fail', 'unverifiable']}
            for name in ('theme_status', 'editing_status', 'continuity_status')},
        'list_fields': {name: {'expected': ('non-empty array' if name == 'evidence' else 'array') +
            ' of non-empty strings', 'type': 'array', 'min_items': 1 if name == 'evidence' else 0,
            'item_type': 'non-empty string'} for name in ('evidence', 'limitations', 'revision_requests')},
        'format_example': {'evidence': ['<原有时间证据>：<原有画面事实及判断>'],
            'limitations': ['<原有限制或负面评价>'], 'revision_requests': ['<原有修改要求>']},
        'repair_instruction': '这些列表的每一项必须是非空字符串，不能是对象或嵌套数组。'
            '只修复结构和类型，保留原时间证据、事实、状态、限制和负评，不补造事实。'
            'evidence不得清空；limitations和revision_requests仅在原本没有相应内容时允许空数组。'
            '示例仅为格式占位，不是可复制的画面证据或答案。'}
    if require_core_meaning:
        contract['missing_core_meaning'] = {'type': 'array', 'item_type': 'non-empty string',
            'required': True, 'meaning': '参考核心含义仍缺失的具体项；仅小瑕疵用limitations记录，核心完整时为[]。'}
    return contract


def _review_list_diagnostics(data, failed_field):
    def actual(value):
        if value is None:
            return {'type': 'null'}
        if isinstance(value, str):
            return {'type': 'string', 'non_empty': bool(value.strip())}
        if isinstance(value, dict):
            return {'type': 'object', 'keys': [key if isinstance(key, str) else
                '<' + type(key).__name__ + ' key>' for key in value]}
        if isinstance(value, list):
            return {'type': 'array', 'length': len(value)}
        if isinstance(value, bool):
            return {'type': 'boolean'}
        if type(value) in (int, float):
            return {'type': 'number', 'finite': type(value) is int or math.isfinite(value)}
        return {'type': type(value).__name__}

    contract = review_output_contract()
    fields = []
    failure = None
    for name in ('evidence', 'limitations', 'revision_requests'):
        value = data.get(name)
        field = {'path': '$.' + name,
            'expected': contract['list_fields'][name]['expected'],
            'present': name in data, 'actual': actual(value), 'items': []}
        if isinstance(value, list):
            field['items'] = [{'path': f'$.{name}[{index}]', 'expected': 'non-empty string',
                'actual': actual(item), 'valid': isinstance(item, str) and bool(item.strip())}
                for index, item in enumerate(value)]
        if name == failed_field:
            invalid = next((item for item in field['items'] if not item['valid']), None)
            failure = invalid or {key: field[key] for key in ('path', 'expected', 'actual')}
        fields.append(field)
    return {'policy': 'review_list_type_diagnostics_v1',
        'error_path': failure['path'], 'expected': failure['expected'], 'actual': failure['actual'],
        'review_list_fields': fields, 'output_contract': contract}


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
            'observed_entities': [{key: role.get(key) for key in
                ('role_id', 'entity_id', 'identity_confirmed', 'identity_status', 'state')}
                for role in observation['roles']],
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
            overlapping_roles = set()
            if (window is not None and isinstance(roles, list)
                    and all(isinstance(role, str) for role in roles)
                    and all(type(value) in (int, float) and math.isfinite(value) for value in (start, end))):
                offset = window['source_start_s']
                matching = [index for index, usable in enumerate(window['observation']['usable_ranges'])
                    if offset + usable['local_in_s'] - 0.001 <= start < end
                    and end <= offset + usable['local_out_s'] + 0.001
                    and set(roles) <= set(usable['role_ids'])]
                overlapping_roles = {role for event in window['observation']['events']
                    if event['local_start_s'] < end - offset and start - offset < event['local_end_s']
                    for role in event['role_ids']}
            selections.append({'segment_id': segment.get('segment_id'), 'window_id': window_id,
                'source_id': segment.get('source_id'), 'selected_source_interval_s': [start, end],
                'role_ids': roles, 'known_window_id': window is not None,
                'matching_single_usable_range_indices': matching,
                'overlapping_event_role_ids': sorted(overlapping_roles),
                'missing_overlapping_event_role_ids': [role for role in roles if role not in overlapping_roles]
                    if isinstance(roles, list) and all(isinstance(role, str) for role in roles) else None})
    return deepcopy(json_safe({'policy': 'all_plan_execution_evidence_v1',
        'focus_bindings': bindings, 'selected_segments': selections, 'watched_window_table': table,
        'cast_contract': 'Each cast binding must use the exact entity_id of its confirmed observed role. '
            'Different entity IDs cannot be merged by appearance or a new invented ID; cast is optional, '
            'but every dialogue speaker must still be bound to a visible received role.',
        'audio_relation_contract': {'shape': [{'segment_id': 'a segment_id in this line',
            'relation': 'on_screen/offscreen/reaction/cutaway/narration'}],
            'coverage': 'When present, list every segment_id of this audio line; a string is invalid.'},
        'limit': 'Mechanical IDs/ranges/roles only; does not establish narrative or identity quality. '
                 'An empty matching list permits no automatic replacement, split or range expansion.'}))


def parse_model_json(value: str | dict) -> dict:
    if isinstance(value, dict):
        return value
    require(isinstance(value, str), "model_response:text_or_object_required")
    value = value.strip()
    if "```" in value:
        # Formatting prose around one explicitly fenced JSON object should
        # not require another paid observation. Keep the original reply intact.
        match = re.search(r"```(?:json)?\s*\n([\s\S]+?)\n```", value)
        require(value.count("```") == 2, "model_response:single_json_object_required")
        require(match is not None, "model_response:single_json_object_required")
        value = match.group(1)
    result = json.loads(value)
    require(isinstance(result, dict), "model_response:object_required")
    return result


def _object(value, path):
    require(isinstance(value, dict), path + ":object_required")
    return value


def _interval(start, end, duration, path, *, json_path=None, fields=('start_s', 'end_s')):
    try:
        start = number(start, path + "/start")
        end = number(end, path + "/end")
    except ValueError as error:
        error.violation['path'] = (json_path or path) + '.' + fields[0 if error.violation['path'].endswith('/start') else 1]
        error.violation['expected'] = f'0 ≤ x ≤ {duration}'
        raise
    interval_bounds(start, end, 0, duration, json_path or path, path + ":invalid_or_out_of_bounds",
                    tolerance=.001, fields=fields)
    return start, end


def normalize_reference(data, duration):
    """Clamp annotation ranges only; retain invalid numbers for strict validation."""
    groups = [(data, 'visible_evidence', '$.visible_evidence'),
              (data, 'editing_methods', '$.editing_methods'),
              (data.get('value_judgment'), 'evidence', '$.value_judgment.evidence')]
    strategy = data.get('strategy')
    for parent, path in ((strategy, '$.strategy'), (data, '$')):
        visual = parent.get('visual_form') if isinstance(parent, dict) else None
        groups.append((visual, 'evidence', path + '.visual_form.evidence'))
    for parent, key, path in groups:
        if not isinstance(parent, dict) or not isinstance(parent.get(key), list):
            continue
        kept = []
        for index, row in enumerate(parent[key]):
            if (not isinstance(row, dict) or
                    not any(field in row for field in ('start_s', 'end_s'))):
                kept.append(row)
                continue
            start, end = row.get('start_s'), row.get('end_s')
            if not all(type(value) in (int, float) and math.isfinite(value) for value in (start, end)):
                kept.append(row)
                continue
            start, end = max(0, start), min(duration, end)
            item_path = f'{path}[{index}]'
            if start >= end:
                changes = rows(data.setdefault('normalization', []), 'reference/normalization', nonempty=False)
                changes.append({'path': item_path, 'original': deepcopy(row), 'dropped': True})
                continue
            for field, normalized in (('start_s', start), ('end_s', end)):
                if row[field] != normalized:
                    changes = rows(data.setdefault('normalization', []), 'reference/normalization', nonempty=False)
                    changes.append({'path': item_path + '.' + field,
                                    'original': row[field], 'normalized': normalized})
                    row[field] = normalized
            kept.append(row)
        parent[key][:] = kept
    return data


def _annotation_interval(row, duration, path, json_path=None):
    for field in ('start_s', 'end_s'):
        try:
            number(row.get(field), path + ('/start' if field == 'start_s' else '/end'))
        except ValueError as error:
            error.violation.update(path=(json_path or path) + '.' + field, expected=f'0 ≤ x ≤ {duration}')
            raise
    interval_bounds(row['start_s'], row['end_s'], 0, duration, json_path or path,
                    path + ':invalid_or_out_of_bounds')


def _annotation_rows(data, value, path, json_path):
    changes = data.get('normalization')
    if value == [] and isinstance(changes, list) and any(item.get('dropped') and
            isinstance(item.get('path'), str) and item['path'].startswith(json_path + '[')
            for item in changes if isinstance(item, dict)):
        raise ValueError(path + ':list_required:empty_after_normalization')
    return rows(value, path)


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


def reference_strategy(reference, *, creative_audio=False):
    """Read optional creative choices while retaining old reference files."""
    reference = reference.get('reference', reference) if isinstance(reference, dict) else {}
    strategy = reference.get('strategy', {})
    _object(strategy, 'reference/strategy')
    defaults = {'adaptation_mode': 'reinterpret' if creative_audio else 'preserve',
                'speech_mode': 'narration', 'timing_driver': 'speech'}
    allowed = {'adaptation_mode': {'reinterpret', 'preserve'},
               'speech_mode': {'narration', 'dialogue', 'mixed', 'none'},
               'timing_driver': {'speech', 'music', 'action'}}
    for key, choices in allowed.items():
        value = strategy.get(key, defaults[key])
        require(isinstance(value, str) and value in choices, 'reference:invalid_' + key)
    return {**defaults, **{key: strategy[key] for key in allowed if key in strategy}}


def creative_reference_contract():
    return {'required_fields': ['viewer_takeaway', 'value_judgment', 'cultural_context'],
        'stance_values': ['赞同', '批判', '讽刺', '抒情', '中立', '其他'],
        'evidence_bases': ['visible', 'asr', 'cultural'],
        'cultural_kinds': ['work_or_character', 'meme', 'pun', 'social_norm', 'other'],
        'confidence_values': ['high', 'medium', 'low'],
        'format_example': {
            'viewer_takeaway': '普通观众对视频内容和主旨的一两句话理解',
            'value_judgment': {'core_message': '一句话：视频真正表达的观点、价值、态度或情感，不写剪辑方法',
                'stance': '抒情', 'what_is_upheld': '视频肯定或珍视什么；情感类写情感对象',
                'what_is_rejected': '视频否定或嘲讽什么；没有则写无',
                'evidence': [{'start_s': 0, 'end_s': 1, 'basis': 'visible', 'note': '支持这一态度的实际画面'}]},
            'cultural_context': [{'claim': '理解作品、角色、梗、谐音或社会语境所用的外部文化常识',
                'kind': 'other', 'confidence': 'medium', 'supports': '它支撑主旨的哪部分'}]},
        'repair_instruction': '参考理解的原有字段和语义全部保留；修复时必须补齐viewer_takeaway（非空字符串）、value_judgment和cultural_context。'
            '先写viewer_takeaway，从已有内容概括观众理解的主旨，其余字段须与它一致，不得互相矛盾。'
            '没有使用外部文化常识时cultural_context为[]，不得编造画面、台词或时间证据。'}


def validate_reference(data, reference_sha, duration_s, *, require_value_fields=False):
    """Old reference files may omit newer fields; only fresh readings must carry them."""
    _object(data, "reference")
    normalize_reference(data, duration_s)
    require(data.get("reference_sha256") == reference_sha, "reference:sha_changed")
    text(data.get("theme"), "reference/theme")
    text(data.get("intended_takeaway"), "reference/intended_takeaway")
    reference_strategy(data)
    for index, evidence in enumerate(_annotation_rows(data, data.get("visible_evidence"),
            "reference/visible_evidence", '$.visible_evidence')):
        _object(evidence, "reference/evidence")
        _annotation_interval(evidence, duration_s, "reference/evidence", f"$.visible_evidence[{index}]")
        text(evidence.get("observed_fact"), "reference/observed_fact")
        text(evidence.get("supports"), "reference/supports")
    for index, method in enumerate(_annotation_rows(data, data.get("editing_methods"),
            "reference/editing_methods", '$.editing_methods')):
        _object(method, "reference/editing_method")
        text(method.get("method"), "reference/method")
        text(method.get("function"), "reference/function")
        text(method.get("visual_evidence"), "reference/visual_evidence")
        _annotation_interval(method, duration_s, "reference/editing_method", f"$.editing_methods[{index}]")
    _strings(data.get("uncertainties"), "reference/uncertainties")
    contract = creative_reference_contract()
    if require_value_fields or "value_judgment" in data:
        path = "reference/value_judgment"
        judgment = _object(data.get("value_judgment"), path)
        for key in ("core_message", "stance", "what_is_upheld", "what_is_rejected"):
            text(judgment.get(key), path + "/" + key)
        # Stance is informational only: a free label such as "幽默" is kept under "其他" instead of failing.
        pattern = "(?:" + "|".join(contract['stance_values']) + r")(?:[：:].+|（.+）|\(.+\))?"
        if re.fullmatch(pattern, judgment["stance"].strip(), re.S) is None:
            judgment["stance"] = "其他：" + judgment["stance"].strip()
        for index, evidence in enumerate(_annotation_rows(data, judgment.get("evidence"),
                path + "/evidence", '$.value_judgment.evidence')):
            _object(evidence, path + "/evidence")
            _annotation_interval(evidence, duration_s, path + "/evidence", f"$.value_judgment.evidence[{index}]")
            require(evidence.get("basis") in contract['evidence_bases'], path + "/evidence/basis:invalid_value")
            text(evidence.get("note"), path + "/evidence/note")
    if require_value_fields or "cultural_context" in data:
        path = "reference/cultural_context"
        for context in rows(data.get("cultural_context"), path, nonempty=False):
            _object(context, path)
            for key in ("claim", "kind", "confidence", "supports"):
                text(context.get(key), path + "/" + key)
            require(context["kind"] in contract['cultural_kinds'], path + "/kind:invalid_value")
            require(context["confidence"] in contract['confidence_values'], path + "/confidence:invalid_value")
    if require_value_fields or "viewer_takeaway" in data:
        text(data.get("viewer_takeaway"), "reference/viewer_takeaway")
    strategy = data.get('strategy', {})
    for parent, path in ((strategy, 'reference/strategy'), (data, 'reference')):
        visual = _object(parent, path).get('visual_form')
        if isinstance(visual, dict) and isinstance(visual.get('evidence'), list):
            for index, evidence in enumerate(visual['evidence']):
                if isinstance(evidence, dict) and any(key in evidence for key in ('start_s', 'end_s')):
                    _annotation_interval(evidence, duration_s, path + '/visual_form/evidence',
                                         '$.' + ('strategy.' if parent is strategy else '') + f'visual_form.evidence[{index}]')
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
        if not start <= timestamp <= end:
            raise numeric_error('coarse:timestamp_outside_coverage', 'coarse/events/timestamp_s',
                                event['timestamp_s'], f'{start} ≤ x ≤ {end}')
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
        if end - start > max_window_s:
            raise numeric_error('search:window_too_long', 'search/window/duration_s', end - start,
                                f'0 < duration_s ≤ {max_window_s}')
        text(request.get("question"), "search/question")
        _strings(request.get("role_ids"), "search/role_ids")
    return data


def normalize_fine(data, duration):
    """Keep event indices attached to the same surviving observed events."""
    event_indices = normalize_intervals(data, data, 'events', duration,
        fields=('local_start_s', 'local_end_s'))
    normalize_intervals(data, data, 'usable_ranges', duration, fields=('local_in_s', 'local_out_s'))
    for key in ('usable_ranges', 'affordances'):
        for index, item in enumerate(data.get(key, []) if isinstance(data.get(key), list) else []):
            if isinstance(item, dict) and isinstance(item.get('event_indices'), list):
                selected = item['event_indices']
                # Affordances were never type-checked before normalization; free-form indices pass untouched.
                if key == 'affordances' and not all(type(entry) is int for entry in selected):
                    continue
                require(all(type(entry) is int and entry in event_indices for entry in selected),
                        'fine:unknown_event_index')
                remapped = [event_indices[entry] for entry in selected]
                if remapped != selected:
                    rows(data.setdefault('normalization', []), 'reference/normalization', nonempty=False).append(
                        dict(path=f'$.{key}[{index}].event_indices', original=selected[:], normalized=remapped))
                    item['event_indices'] = remapped


def validate_fine(data, window, *, normalize=False):
    _object(data, "fine")
    _object(window, "window")
    require(data.get("window_id") == window.get("window_id"), "fine:window_id_mismatch")
    require(data.get("source_id") == window.get("source_id"), "fine:source_id_mismatch")
    start = number(window.get("source_start_s"), "window/source_start_s")
    end = number(window.get("source_end_s"), "window/source_end_s")
    require(start < end, "window:invalid_range")
    duration = end - start
    if normalize:
        normalize_fine(data, duration)
    role_ids = ids(data.get("roles"), "role_id", "fine/roles", nonempty=False)
    confirmed = set()
    for role in data["roles"]:
        require(type(role.get("identity_confirmed")) is bool, "fine:identity_confirmation_required")
        text(role.get("state"), "fine/role_state")
        text(role.get("identity_evidence"), "fine/identity_evidence")
        if role.get('entity_id') is not None:
            entity = role['entity_id']
            text(window.get('source_sha256'), 'fine/entity_source_sha256')
            prefix = window['source_sha256'] + ':'
            require(isinstance(entity, str) and entity.startswith(prefix) and
                    re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', entity[len(prefix):]) is not None,
                    'fine:entity_source_namespace_required')
        if role["identity_confirmed"]:
            confirmed.add(role["role_id"])
    events = rows(data.get("events"), "fine/events", nonempty=False)
    for index, event in enumerate(events):
        _object(event, "fine/event")
        _interval(event.get("local_start_s"), event.get("local_end_s"), duration, "fine/event",
                  json_path=f"$.events[{index}]", fields=("local_start_s", "local_end_s"))
        text(event.get("observed_fact"), "fine/observed_fact")
        refs(event.get("role_ids"), role_ids, "fine/event_roles")
    for index, usable in enumerate(rows(data.get("usable_ranges"), "fine/usable_ranges", nonempty=False)):
        _object(usable, "fine/usable_range")
        usable_start, usable_end = _interval(usable.get("local_in_s"), usable.get("local_out_s"), duration,
                                            "fine/usable", json_path=f"$.usable_ranges[{index}]",
                                            fields=("local_in_s", "local_out_s"))
        refs(usable.get("role_ids"), confirmed, "fine/usable_confirmed_roles")
        indices = rows(usable.get("event_indices"), "fine/event_indices", nonempty=False)
        if not indices:
            # A watched environment/detail shot may carry no actor event.
            # Its own camera description supports selection, not an invented action.
            require(not usable['role_ids'] and isinstance(usable.get('camera'), str)
                    and bool(usable['camera'].strip()), 'fine:empty_events_require_visual_only_range')
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


def validate_task_cast(cast, windows=None):
    """Associate a task character with observed appearances of one source entity."""
    if cast is None:
        return {}
    ids(cast, 'cast_id', 'plan/cast', nonempty=False)
    result, appearances, entities = {}, set(), set()
    if isinstance(windows, list):
        windows = {window['window_id']: window for window in windows}
    from .audio_contracts import VOICES
    for character in cast:
        entity = character.get('entity_id')
        text(entity, 'plan/cast/entity_id')
        require(entity not in entities, 'plan:duplicate_cast_entity')
        entities.add(entity)
        if 'voice' in character:
            require(character['voice'] in VOICES, 'plan:cast_unknown_voice')
        for binding in rows(character.get('bindings'), 'plan/cast/bindings'):
            _object(binding, 'plan/cast/binding')
            for key in ('window_id', 'role_id'):
                text(binding.get(key), 'plan/cast/binding/' + key)
            identity = (binding['window_id'], binding['role_id'])
            require(identity not in appearances, 'plan:duplicate_cast_appearance')
            appearances.add(identity)
            if windows is not None:
                window = windows.get(identity[0], {})
                require(window.get('status') == 'watched', 'plan:cast_window_not_watched')
                observation = validate_fine(window.get('observation'), window)
                role = next((row for row in observation['roles'] if row['role_id'] == identity[1]), {})
                require(role.get('identity_confirmed') is True and role.get('entity_id') == entity,
                        'plan:cast_entity_not_supported_by_observation')
        result[character['cast_id']] = character
    return result


def validate_plan(data, catalog, windows, reference_sha, reference_duration_s=None, *,
                  reference_audio_stream_index=_UNSPECIFIED_AUDIO_STREAM, editing_reference=None,
                  max_duration_s=180, max_segments=32, require_audio_draft=False,
                  reference=None, allow_unfocused=False):
    _object(data, "plan")
    require(data.get("reference_sha256") == reference_sha, "plan:reference_sha_changed")
    sources = _sources(catalog)
    focus = data.get("focus_role_id")
    if not allow_unfocused or focus is not None:
        text(focus, "plan/focus_role_id")
    if isinstance(windows, list):
        ids(windows, "window_id", "windows", nonempty=False)
        windows = {window["window_id"]: window for window in windows}
    _object(windows, "windows")
    validate_task_cast(data.get('cast'), windows)
    focus_bindings = {}
    bindings = data.get("focus_role_bindings", [] if allow_unfocused else None)
    require(focus is not None or not bindings, "plan:focus_bindings_require_focus_role")
    for binding in rows(bindings, "plan/focus_role_bindings", nonempty=focus is not None):
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
        if 'coverage' in slot:
            coverage = _object(slot['coverage'], 'plan/slot/coverage')
            for key in ('visual_goal', 'audio_goal'):
                text(coverage.get(key), 'plan/slot/coverage/' + key)
            _strings(coverage.get('gaps', []), 'plan/slot/coverage/gaps')
        refs(slot.get("segment_ids"), segment_ids, "plan/slot_segments", nonempty=True)
        assigned.extend(slot["segment_ids"])
    require(len(assigned) == len(set(assigned)) and set(assigned) == segment_ids,
            "plan:segments_must_belong_to_exactly_one_slot")
    focal_seen = False
    segment_durations = []
    for segment_index, segment in enumerate(data["segments"]):
        if 'shot_role' in segment:
            text(segment['shot_role'], 'plan/segment/shot_role')
        if 'reason' in segment:
            text(segment['reason'], 'plan/segment/reason')
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
                               sources[source_id]["duration_s"], "plan/segment",
                               json_path=f"$.segments[{segment_index}]", fields=("source_in_s", "source_out_s"))
        interval_bounds(start, end, max(0, window['source_start_s'] - .001), window['source_end_s'],
                        f'$.segments[{segment_index}]', 'plan:segment_outside_watched_window',
                        tolerance=.001, fields=('source_in_s', 'source_out_s'))
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
        if not 0.5 <= speed <= 2:
            raise numeric_error('plan:speed_out_of_range', f'$.segments[{segment_index}].speed',
                                segment['speed'], '0.5 ≤ x ≤ 2')
        freeze_tail = number(segment.get("freeze_tail_s", 0), "plan/freeze_tail_s")
        if freeze_tail > 10:
            raise numeric_error('plan:freeze_tail_exceeds_10_seconds', f'$.segments[{segment_index}].freeze_tail_s',
                                segment['freeze_tail_s'], '0 ≤ x ≤ 10')
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
                                                   segment_duration, "plan/caption",
                                                   json_path=f"$.segments[{segment_index}].caption")
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
    if focus is not None:
        require(focal_seen, "plan:focus_role_not_confirmed_in_selected_footage")
    require(data.get("audio_mode") in {"reference", "source", "mix", "silent"}, "plan:audio_mode")
    for key in ("source_gain_db", "reference_gain_db"):
        value = data.get(key, 0) if data["audio_mode"] == "silent" else data.get(key)
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
    if require_audio_draft or 'audio_draft' in data:
        speech_mode = (reference_strategy(reference, creative_audio=require_audio_draft)['speech_mode']
                       if reference is not None else None)
        validate_audio_draft(data.get('audio_draft'), data['segments'], speech_mode=speech_mode,
                             cast=data.get('cast'))
        if data.get('cast') is not None:
            validate_dialogue_speakers(data['audio_draft'], data['segments'],
                                       cast=data['cast'], windows=windows)
    return data


def validate_audio_draft(draft, segments, *, speech_mode=None, cast=None):
    """Bind the global script to the same rough edit; timing is resolved later."""
    from .audio_contracts import VOICES
    ids(draft, 'id', 'plan/audio_draft', nonempty=speech_mode != 'none')
    if speech_mode == 'none':
        require(not draft, 'plan:audio_draft_must_be_empty_without_speech')
    order = {row['segment_id']: index for index, row in enumerate(segments)}
    by_id = {row['segment_id']: row for row in segments}
    characters = validate_task_cast(cast)
    for line in draft:
        text(line.get('text'), 'plan/audio_draft/text')
        require(len(line['text']) <= 600, 'plan:audio_draft_text_exceeds_600_characters')
        require(line.get('voice') in VOICES, 'plan:audio_draft_unknown_voice')
        require(line.get('mode', 'narration') in {'narration', 'dialogue'}, 'plan:audio_draft_mode')
        refs(line.get('segment_ids'), set(order), 'plan/audio_draft/segments', nonempty=True)
        positions = [order[segment_id] for segment_id in line['segment_ids']]
        require(positions == list(range(positions[0], positions[-1] + 1)),
                'plan:audio_draft_segments_must_be_contiguous_in_edl_order')
        if 'cast_id' in line:
            require(line['cast_id'] in characters, 'plan:audio_draft_unknown_cast')
            voice = characters[line['cast_id']].get('voice', line['voice'])
            require(line['voice'] == voice, 'plan:dialogue_speaker_voice_changed')
        if 'audio_relation' in line:
            relations = rows(line['audio_relation'], 'plan/audio_draft/audio_relation')
            for relation in relations:
                _object(relation, 'plan/audio_draft/audio_relation/edge')
            related = [relation.get('segment_id') for relation in relations]
            refs(related, set(line['segment_ids']), 'plan/audio_draft/audio_relation/segments')
            require(set(related) == set(line['segment_ids']), 'plan:audio_relation_missing_segment')
            require(all(isinstance(relation.get('relation'), str) and relation['relation'] in {'on_screen', 'offscreen', 'reaction',
                    'cutaway', 'narration'} for relation in relations), 'plan:unknown_audio_relation')
        if 'identity_anchor_segment_id' in line:
            anchor = line['identity_anchor_segment_id']
            require(anchor in order and order[anchor] <= positions[0], 'plan:dialogue_identity_anchor_not_established')
        if 'start_anchor' in line:
            anchor = _object(line['start_anchor'], 'plan/audio_draft/start_anchor')
            require(anchor.get('segment_id') in line['segment_ids'], 'plan:start_anchor_outside_line_segments')
            offset = number(anchor.get('offset_s'), 'plan/audio_draft/start_anchor/offset_s')
            row = by_id[anchor['segment_id']]
            if 'start_s' in row and 'end_s' in row:
                duration = row['end_s'] - row['start_s']
            elif 'source_in_s' in row and 'source_out_s' in row:
                duration = (row['source_out_s'] - row['source_in_s']) / row.get('speed', 1) + row.get('freeze_tail_s', 0)
            else:
                duration = None
            if duration is not None:
                if not offset < duration:
                    raise numeric_error('plan:start_anchor_outside_segment_duration',
                        'plan/audio_draft/start_anchor/offset_s', anchor['offset_s'], f'0 ≤ x < {duration}')
    return draft


def validate_dialogue_speakers(draft, segments, *, cast=None, windows=None):
    """Keep a character's voice through reaction shots and later appearances."""
    by_id = {row.get('segment_id', row.get('id')): row for row in segments}
    order = {segment_id: index for index, segment_id in enumerate(by_id)}
    characters = validate_task_cast(cast, windows)
    if isinstance(windows, list):
        windows = {window['window_id']: window for window in windows}
    entities = {(binding['window_id'], binding['role_id']): character['entity_id']
                for character in characters.values() for binding in character['bindings']}
    for window in (windows or {}).values():
        entities.update({(window['window_id'], role['role_id']): role['entity_id']
                         for role in window.get('observation', {}).get('roles', []) if role.get('entity_id')})
    voices = {}
    for line in draft:
        if line.get('mode', 'narration') != 'dialogue':
            continue
        speaker = _object(line.get('speaker'), 'plan/audio_draft/speaker')
        for key in ('window_id', 'role_id'):
            text(speaker.get(key), 'plan/audio_draft/speaker/' + key)
        appearance = (speaker['window_id'], speaker['role_id'])
        identity = entities.get(appearance, appearance)
        if line.get('cast_id') is not None:
            character = characters.get(line['cast_id'], {})
            require(any((binding['window_id'], binding['role_id']) == appearance
                        for binding in character.get('bindings', [])), 'plan:dialogue_speaker_cast_mismatch')
            require(character.get('voice', line.get('voice')) == line.get('voice'),
                    'plan:dialogue_speaker_voice_changed')
        def visible(row):
            return (row.get('window_id') == appearance[0] and appearance[1] in row.get('role_ids', []) or
                    isinstance(identity, str) and (identity in row.get('entity_ids', []) or
                    any(entities.get((row.get('window_id'), role_id)) == identity
                        for role_id in row.get('role_ids', []))))
        selected = [by_id.get(segment_id, {}) for segment_id in line.get('segment_ids', [])]
        anchor = line.get('identity_anchor_segment_id')
        anchored = (anchor in by_id and visible(by_id[anchor]) and
                    line.get('segment_ids') and order[anchor] <= order[line['segment_ids'][0]])
        require(any(visible(row) for row in selected) or anchored,
                'plan:dialogue_speaker_not_in_selected_segments')
        if anchor is not None:
            require(anchored, 'plan:dialogue_identity_anchor_not_established')
        for relation in line.get('audio_relation', []):
            if relation.get('relation') == 'on_screen':
                require(visible(by_id.get(relation.get('segment_id'), {})),
                        'plan:on_screen_speaker_not_visible')
        require(identity not in voices or voices[identity] == line.get('voice'),
                'plan:dialogue_speaker_voice_changed')
        voices[identity] = line.get('voice')
    return draft


def validate_blind_reading(data, duration_s, *, normalize=False):
    _object(data, "blind")
    for key in ("observed_story", "apparent_theme"):
        text(data.get(key), "blind/" + key)
    _strings(data.get("main_characters"), "blind/main_characters")
    if normalize:
        normalize_intervals(data, data, 'evidence', duration_s)
        _annotation_rows(data, data.get('evidence'), 'blind/evidence', '$.evidence')
    for index, evidence in enumerate(rows(data.get('evidence'), 'blind/evidence')):
        _object(evidence, "blind/evidence")
        _interval(evidence.get("start_s"), evidence.get("end_s"), duration_s, "blind/evidence",
                  json_path=f"$.evidence[{index}]")
        text(evidence.get("observed_fact"), "blind/observed_fact")
    _strings(data.get("confusions"), "blind/confusions")
    return data


def validate_review(data, reference_sha, *, require_core_meaning=False):
    _object(data, "review")
    require(data.get("reference_sha256") == reference_sha, "review:reference_sha_changed")
    for key in ("theme_status", "editing_status", "continuity_status"):
        require(data.get(key) in {"pass", "partial", "fail", "unverifiable"}, "review:" + key)
    for key in ("evidence", "limitations", "revision_requests"):
        try:
            _strings(data.get(key), "review/" + key, nonempty=key == "evidence")
        except ValueError as error:
            raise ReviewContractError(str(error), _review_list_diagnostics(data, key)) from error
    if require_core_meaning or 'missing_core_meaning' in data:
        _strings(data.get('missing_core_meaning'), 'review/missing_core_meaning')
    return data
