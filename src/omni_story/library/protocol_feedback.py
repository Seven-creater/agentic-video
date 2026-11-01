"""Bounded mechanical feedback for rejected model replies; never repairs decisions."""
from copy import deepcopy
import json
import math
import re

from . import contracts
from .audio_contracts import VOICES


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _objects(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _strings(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


class _Feedback:
    def __init__(self):
        self.items = []

    def add(self, path, value, expected, hint):
        if len(self.items) < 30 and not any(item['path'] == path for item in self.items):
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            self.items.append(dict(path=path, value=rendered[:200], expected=expected, hint=hint))

    def text(self, value, path):
        if not isinstance(value, str) or not value.strip():
            self.add(path, value, 'non-empty string', '填写非空文字，保留已有事实，不补造证据。')

    def enum(self, value, path, choices):
        if not isinstance(value, str) or value not in choices:
            self.add(path, value, list(choices), '使用允许的一个枚举值。')

    def array(self, value, path, *, nonempty=False):
        if not isinstance(value, list) or nonempty and not value:
            self.add(path, value, 'non-empty array' if nonempty else 'array', '补齐数组及必需项。')
        return value if isinstance(value, list) else []

    def object(self, value, path):
        if not isinstance(value, dict):
            self.add(path, value, 'object', '填写包含必填键的JSON对象。')
            return False
        return True

    def interval(self, row, path, duration):
        start, end = row.get('start_s'), row.get('end_s')
        if (not all(_finite(value) for value in (start, end)) or not 0 <= start < end or
                _finite(duration) and end > duration):
            self.add(path, [start, end], f'0 <= start_s < end_s <= {duration}',
                     '使用有限数值时间；标注规范化后区间仍须有效。bool不是数值。')


def _reference(value, context, feedback):
    value = deepcopy(value)
    duration = context.get('duration_s')
    if _finite(duration):
        try:
            contracts.normalize_reference(value, duration)
        except (ValueError, TypeError, OverflowError):
            feedback.add('$.normalization', value.get('normalization'), 'array', '规范化记录必须是数组。')
    sha = context.get('reference_sha')
    if sha is not None and value.get('reference_sha256') != sha:
        feedback.add('$.reference_sha256', value.get('reference_sha256'), sha, '逐字复制输入参考SHA。')
    for key in ('theme', 'intended_takeaway'):
        feedback.text(value.get(key), '$.' + key)
    strategy = value.get('strategy', {})
    if feedback.object(strategy, '$.strategy'):
        for key, choices, default in (
                ('speech_mode', ['narration', 'dialogue', 'mixed', 'none'], 'narration'),
                ('adaptation_mode', ['reinterpret', 'preserve'], 'preserve'),
                ('timing_driver', ['speech', 'music', 'action'], 'speech')):
            feedback.enum(strategy.get(key, default), '$.strategy.' + key, choices)
    for key, fields in (('visible_evidence', ('observed_fact', 'supports')),
                        ('editing_methods', ('method', 'function', 'visual_evidence'))):
        path = '$.' + key
        for index, row in enumerate(feedback.array(value.get(key), path, nonempty=True)):
            item_path = f'{path}[{index}]'
            if feedback.object(row, item_path):
                feedback.interval(row, item_path, duration)
                for field in fields:
                    feedback.text(row.get(field), item_path + '.' + field)
    for index, item in enumerate(feedback.array(value.get('uncertainties'), '$.uncertainties')):
        feedback.text(item, f'$.uncertainties[{index}]')
    required = context.get('require_value_fields', False)
    contract = contracts.creative_reference_contract()
    if required or 'viewer_takeaway' in value:
        feedback.text(value.get('viewer_takeaway'), '$.viewer_takeaway')
    if required or 'value_judgment' in value:
        judgment = value.get('value_judgment')
        path = '$.value_judgment'
        if feedback.object(judgment, path):
            for key in ('core_message', 'stance', 'what_is_upheld', 'what_is_rejected'):
                feedback.text(judgment.get(key), path + '.' + key)
            for index, row in enumerate(feedback.array(judgment.get('evidence'), path + '.evidence', nonempty=True)):
                item_path = f'{path}.evidence[{index}]'
                if feedback.object(row, item_path):
                    feedback.interval(row, item_path, duration)
                    feedback.enum(row.get('basis'), item_path + '.basis', contract['evidence_bases'])
                    feedback.text(row.get('note'), item_path + '.note')
    if required or 'cultural_context' in value:
        for index, row in enumerate(feedback.array(value.get('cultural_context'), '$.cultural_context')):
            path = f'$.cultural_context[{index}]'
            if feedback.object(row, path):
                for key in ('claim', 'supports'):
                    feedback.text(row.get(key), path + '.' + key)
                feedback.enum(row.get('kind'), path + '.kind', contract['cultural_kinds'])
                feedback.enum(row.get('confidence'), path + '.confidence', contract['confidence_values'])
    for parent, path in ((strategy, '$.strategy'), (value, '$')):
        visual = parent.get('visual_form') if isinstance(parent, dict) else None
        if isinstance(visual, dict) and isinstance(visual.get('evidence'), list):
            for index, row in enumerate(visual['evidence']):
                if isinstance(row, dict) and ('start_s' in row or 'end_s' in row):
                    feedback.interval(row, f'{path}.visual_form.evidence[{index}]', duration)


def _plan_bindings(value, windows, feedback):
    def check(validate, replacements=()):
        try:
            validate()
        except ValueError as error:
            violation = getattr(error, 'violation', None)
            if isinstance(violation, dict):
                path = violation['path']
                for old, new in replacements:
                    path = path.replace(old, new, 1)
                feedback.add(path, violation.get('value'), violation['expected'], violation['repair_hint'])

    cast = value.get('cast')
    # The whole list catches structural/duplicate errors; isolated bindings expose
    # later independent evidence errors hidden by the validator's first rejection.
    check(lambda: contracts.validate_task_cast(cast, windows or None))
    for i, character in enumerate(cast if isinstance(cast, list) else []):
        if not isinstance(character, dict):
            continue
        bindings = character.get('bindings')
        for j, binding in enumerate(bindings if isinstance(bindings, list) else []):
            check(lambda: contracts.validate_task_cast([dict(character, bindings=[binding])], windows or None),
                  [('$.cast[0]', f'$.cast[{i}]'), ('.bindings[0]', f'.bindings[{j}]')])
    if 'focus_role_bindings' in value:
        focus, bindings = value.get('focus_role_id'), value['focus_role_bindings']
        check(lambda: contracts.validate_focus_role_bindings(bindings, windows, focus))
        for i, binding in enumerate(bindings if isinstance(bindings, list) else []):
            check(lambda: contracts.validate_focus_role_bindings([binding], windows, focus),
                  [('$.focus_role_bindings[0]', f'$.focus_role_bindings[{i}]')])


def _plan(value, context, feedback):
    segments = feedback.array(value.get('segments'), '$.segments', nonempty=True)
    by_id = {row['segment_id']: row for row in _objects(segments) if isinstance(row.get('segment_id'), str)}
    order = {ident: index for index, ident in enumerate(by_id)}
    windows = context.get('windows', {})
    windows = {row['window_id']: row for row in _objects(windows)
               if isinstance(row.get('window_id'), str)} if isinstance(windows, list) else windows
    windows = windows if isinstance(windows, dict) else {}
    _plan_bindings(value, windows, feedback)
    triples = contracts.plan_field_contract(windows=windows)['cast']['bindable_triples']
    dialogue = [line for line in _objects(value.get('audio_draft')) if line.get('mode') == 'dialogue']
    identity_context = 'windows' in context and (not windows or any(
        isinstance(window.get('observation'), dict) for window in windows.values()))
    if identity_context and dialogue and not triples:
        hint = '当前没有可绑定的已确认身份（bindable_triples为空）；请改用旁白，或把对白挂到有已确认说话者的窗口，不能发明身份。'
        # Replace a generic binding hint already emitted for the first rejection.
        for item in feedback.items:
            if item['path'].startswith('$.cast') or item['path'] == '$.audio_draft':
                item['hint'] = hint
        feedback.add('$.audio_draft', dialogue, dict(bindable_triples=[]), hint)
    use_slots = 'slots' in value or any('slot_id' in row for row in _objects(segments))
    slots = feedback.array(value.get('slots'), '$.slots', nonempty=True) if use_slots else []
    from .plan_normalisation import storyboard_cell_carriers
    carriers = storyboard_cell_carriers(value)
    assignments = {ident: [] for ident in by_id}
    for index, slot in enumerate(slots):
        if not feedback.object(slot, f'$.slots[{index}]'):
            continue
        path = f'$.slots[{index}].segment_ids'
        ident, selected = slot.get('slot_id'), slot.get('segment_ids')
        owned = [key for key, segment in by_id.items() if segment.get('slot_id') == ident]
        if isinstance(selected, list) and (not selected or any(
                isinstance(ref, str) and ref in by_id and (by_id[ref].get('slot_id') != ident or
                    sum(_strings(other.get('segment_ids')).count(ref) for other in _objects(slots)) > 1)
                for ref in selected)):
            cells = slot.get('storyboard_cell_ids', [])
            details = dict(slot_id=ident, storyboard_cell_ids=cells, owned_segment_ids=owned,
                           cell_carriers={cell: carriers.get(cell, []) for cell in _strings(cells)})
            feedback.add(path, selected, details,
                '空或重复slot的归属与承载情况：' + json.dumps(details, ensure_ascii=False) +
                '。segment_ids须与segment.slot_id双向一致；仅当全部格已由有片段的其他slot承载且无其他引用时可删除空slot；'
                '未承载格仍须真实画面，不能补造片段。')
        selected = feedback.array(slot.get('segment_ids'), path, nonempty=True)
        for ident in selected:
            if not isinstance(ident, str) or ident not in by_id:
                feedback.add(path, ident, list(by_id), '引用本计划中存在的segment_id。')
            else:
                assignments[ident].append(slot.get('slot_id'))
    for index, segment in enumerate(segments):
        path = f'$.segments[{index}]'
        if not feedback.object(segment, path):
            continue
        ident = segment.get('segment_id')
        if use_slots and isinstance(ident, str) and assignments.get(ident) != [segment.get('slot_id')]:
            feedback.add(path + '.slot_id', segment.get('slot_id'), assignments.get(ident),
                         f'{ident}必须恰好属于一个slot，slot.segment_ids与segment.slot_id须双向一致。')
        window_id = segment.get('window_id')
        window = windows.get(window_id) if isinstance(window_id, str) else None
        start, end = segment.get('source_in_s'), segment.get('source_out_s')
        if window is not None:
            low, high = window.get('source_start_s'), window.get('source_end_s')
            in_window = (all(_finite(item) for item in (start, end, low, high)) and
                         low - 0.001 <= start < end <= high + 0.001)
            catalog = context.get('catalog', {})
            sources = {row['source_id']: row for row in catalog.get('sources', [])}
            choices = contracts.plan_source_range_contract(window, sources.get(window.get('source_id')))
            if not in_window or (isinstance(window.get('observation'), dict) and
                    not contracts.matching_plan_source_ranges(segment, choices)):
                hint = (contracts.plan_field_contract()['selected_range']['repair_hint'] +
                    f'窗口[{low}, {high}]秒；allowed_source_ranges是原片绝对秒数，不要把全计划改成local时间。')
                offset = window.get('source_offset_s')
                if (all(_finite(v) for v in (start, end, low, high, offset)) and low > start
                        and 0 <= start < end <= high - low + .001):
                    hint += ('source_in_s/source_out_s必须填写原片绝对秒数 = source_offset_s + local time（窗口局部时间），'
                        f'本窗口source_offset_s={offset}秒；allowed_source_ranges（绝对秒数）=' +
                        json.dumps(choices['allowed_source_ranges'], ensure_ascii=False) + '。')
                    # Also clarify the validator's first rejection, which may
                    # already occupy this segment's individual endpoint path.
                    for item in feedback.items:
                        if (item['path'] in (path, path + '.source_in_s', path + '.source_out_s')
                                and isinstance(item['expected'], dict)
                                and 'allowed_source_ranges' in item['expected']):
                            item['hint'] = hint
                feedback.add(path + '.source_in_s/source_out_s', [start, end], choices, hint)
            if isinstance(window.get('observation'), dict):
                try:
                    # Confirmed identity is independent of a bad time field. Check
                    # event overlap only after the selected source interval is valid.
                    contracts.validate_plan_segment_roles(segment, window, path, check_overlap=in_window)
                except ValueError as error:
                    violation = error.violation
                    feedback.add(violation['path'], violation['value'], violation['expected'], violation['repair_hint'])
        elif windows:
            feedback.add(path + '.window_id', window_id, list(windows), '使用已观察窗口ID。')
        speed, freeze = segment.get('speed', 1), segment.get('freeze_tail_s', 0)
        if (all(_finite(item) for item in (start, end, speed, freeze)) and speed > 0 and start < end):
            duration = (end - start) / speed + freeze
            caption = segment.get('caption')
            if isinstance(caption, dict):
                feedback.interval(caption, path + '.caption', duration)
                fps = value.get('fps')
                if type(fps) is int and 1 <= fps <= 60:
                    a, b = caption.get('start_s'), caption.get('end_s')
                    quantized = (round((end - start) / speed * fps) + round(freeze * fps)) / fps
                    if all(_finite(item) for item in (a, b)) and (
                            b > quantized + 1e-9 or round(b * fps) <= round(a * fps)):
                        feedback.add(path + '.caption', [a, b], f'non-empty frame interval within [0, {quantized}]',
                                     '字幕须位于按fps量化后的镜头时长内，且至少占一帧。')
    reference = context.get('reference', {})
    reference = reference.get('reference', reference) if isinstance(reference, dict) else {}
    strategy = reference.get('strategy', {})
    strategy = strategy if isinstance(strategy, dict) else {}
    speech_mode = context.get('speech_mode', strategy.get('speech_mode', 'narration'))
    draft = value.get('audio_draft')
    if context.get('require_audio_draft', True) or 'audio_draft' in value:
        if not isinstance(draft, list) or speech_mode != 'none' and not draft:
            contract = contracts.plan_field_contract(speech_mode)['audio_draft']
            feedback.add('$.audio_draft', draft, contract, contract['repair_hint'])
        elif speech_mode == 'none' and draft:
            feedback.add('$.audio_draft', draft, '[]', 'speech_mode=none时不添加语音。')
    entities = {}
    for character in _objects(value.get('cast')):
        for binding in _objects(character.get('bindings')):
            if all(isinstance(binding.get(key), str) for key in ('window_id', 'role_id')):
                entities[(binding['window_id'], binding['role_id'])] = character.get('entity_id')
    # As in validate_dialogue_speakers, observed identities take precedence over
    # an unsupported cast claim. A bad binding cannot invent voice equivalence.
    for window in windows.values():
        for role in _objects(window.get('observation', {}).get('roles')):
            if isinstance(role.get('role_id'), str) and isinstance(role.get('entity_id'), str):
                entities[(window.get('window_id'), role['role_id'])] = role['entity_id']
    seen, voices = set(), {}
    for index, line in enumerate(draft if isinstance(draft, list) else []):
        path = f'$.audio_draft[{index}]'
        if not feedback.object(line, path):
            continue
        ident = line.get('id')
        if (not isinstance(ident, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', ident) is None or ident in seen):
            feedback.add(path + '.id', ident, 'unique safe string ID', '每条音频使用唯一id。')
        else:
            seen.add(ident)
        text = line.get('text')
        feedback.text(text, path + '.text')
        if isinstance(text, str) and len(text) > 600:
            feedback.add(path + '.text', text, 'at most 600 characters', f'{ident}超过600字，请拆成连贯的多条。')
        feedback.enum(line.get('voice'), path + '.voice', VOICES)
        feedback.enum(line.get('mode'), path + '.mode', ['narration', 'dialogue'])
        if context.get('creative_style') in {'picture_first', 'picture_aware'}:
            feedback.enum(line.get('picture_relation'), path + '.picture_relation', list(contracts.PICTURE_RELATIONS))
            if line.get('picture_relation') == 'sync_speech' and line.get('mode') != 'dialogue':
                feedback.add(path + '.mode', line.get('mode'), 'dialogue',
                             'sync_speech必须是说话人在本句镜头中可见且开口的dialogue。')
        if 'audio_relation' in line and not isinstance(line['audio_relation'], list):
            contract = contracts.plan_field_contract(speech_mode)['audio_relation']
            feedback.add(path + '.audio_relation', line['audio_relation'], contract, contract['repair_hint'])
        selected = feedback.array(line.get('segment_ids'), path + '.segment_ids', nonempty=True)
        positions = [order[item] for item in selected if isinstance(item, str) and item in order]
        valid_selection = (bool(positions) and len(positions) == len(selected) and
                           positions == list(range(positions[0], positions[-1] + 1)))
        if selected and not valid_selection:
            feedback.add(path + '.segment_ids', selected, list(by_id),
                         f'{ident}须引用存在、无重复且按EDL顺序连续排列的segment_ids。')
        if line.get('mode', 'narration') != 'dialogue':
            continue
        speaker = line.get('speaker')
        if not feedback.object(speaker, path + '.speaker'):
            continue
        for key in ('window_id', 'role_id'):
            feedback.text(speaker.get(key), path + '.speaker.' + key)
        if not all(isinstance(speaker.get(key), str) for key in ('window_id', 'role_id')):
            continue
        appearance = (speaker['window_id'], speaker['role_id'])
        identity = entities.get(appearance)
        identity_key = identity if isinstance(identity, str) and identity else appearance
        character = next((row for row in _objects(value.get('cast')) if row.get('cast_id') == line.get('cast_id')), {})
        expected_voice = character.get('voice', voices.get(identity_key, line.get('voice')))
        if line.get('voice') != expected_voice:
            violation = contracts.dialogue_voice_error(index, line, expected_voice).violation
            feedback.add(violation['path'], violation['value'], violation['expected'], violation['repair_hint'])
        voices.setdefault(identity_key, expected_voice)

        visible = [key for key, row in by_id.items() if
            (row.get('window_id') == appearance[0] and appearance[1] in _strings(row.get('role_ids')) or
             isinstance(identity, str) and (identity in _strings(row.get('entity_ids')) or
             any(isinstance(row.get('window_id'), str) and entities.get((row['window_id'], role)) == identity
                 for role in _strings(row.get('role_ids')))))]
        anchor = line.get('identity_anchor_segment_id')
        anchored = isinstance(anchor, str) and anchor in visible and positions and order[anchor] <= positions[0]
        if not any(item in visible for item in selected) and not anchored:
            feedback.add(path + '.speaker', speaker, {'visible_segment_ids': visible},
                         f'{ident}说话者{speaker["role_id"]}实际出现在{visible}；本句镜头中不可见。'
                         '可改为narration旁白、换用这些镜头，或用identity_anchor_segment_id指向当前或更早已展示该人的镜头。')
        if anchor is not None and not anchored:
            feedback.add(path + '.identity_anchor_segment_id', anchor, visible,
                         f'{ident}的身份锚点必须在本句开始前或当前镜头建立，且能看到同一说话者。')


def _times(value, duration, feedback, path='$', *, scope=None, source_facts=False, limit=None):
    if limit is not None and len(feedback.items) >= limit:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + '.' + key
            if key.endswith('_s') and type(child) in (int, float):
                low, high = 0, duration
                if isinstance(scope, dict) and (key in {'source_start_s', 'source_end_s', 'source_in_s', 'source_out_s'} or source_facts and
                        key in {'start_s', 'end_s', 'time_s'}):
                    low, high = scope.get('source_start_s', 0), scope.get('source_end_s', duration)
                if (not _finite(child) or _finite(low) and child < low - 1e-6 or
                        _finite(high) and child > high + 1e-6):
                    if limit is not None and len(feedback.items) >= limit:
                        return
                    feedback.add(child_path, child,
                        f'{low} ≤ x ≤ {high}' if _finite(high) else f'finite number, {low} ≤ x',
                        '使用该字段时间域内的有限秒数，不要向上取整；源时间遵守observation_scope，不与短片本地时长比较。')
            _times(child, duration, feedback, child_path, scope=scope, source_facts=source_facts, limit=limit)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _times(child, duration, feedback, f'{path}[{index}]', scope=scope, source_facts=source_facts, limit=limit)


def diagnose(stage, value, context):
    """Collect up to 30 violations after validation fails, without changing the reply."""
    feedback = _Feedback()
    rejection = context.get('validation_error', stage + ':protocol_validation_failed')
    error = str(rejection)
    violation = context.get('violation', getattr(rejection, 'violation', None))
    if isinstance(violation, dict) and violation.get('offending_groups'):
        feedback.items.extend(dict(row, hint=row['repair_hint']) for row in violation['offending_groups'])
    elif isinstance(violation, dict) and violation.get('offending_lines'):
        # Window feedback must include every line, without the generic 30-row or
        # 200-character truncation. The validator produced these exact numbers.
        feedback.items.extend(dict(row, hint=row['repair_hint']) for row in violation['offending_lines'])
    elif isinstance(violation, dict):
        feedback.add(violation['path'], violation.get('value'), violation['expected'],
                     violation.get('hint') or violation.get('repair_hint') or
                     '按字段的允许范围修正；以实际媒体时长为准，可带小数，不要向上取整。')
    if isinstance(violation, dict) and violation.get('valid_choices'):
        for item in feedback.items:
            item['valid_choices'] = deepcopy(violation['valid_choices'])
    source_facts = re.search(r'_fine_(?:inspect|detail)_', stage) is not None
    _times(value, context.get('media_duration_s', context.get('duration_s')), feedback,
        scope=context.get('observation_scope'), source_facts=source_facts, limit=len(feedback.items) + 5)
    kind = context.get('stage', stage)
    if not isinstance(value, dict):
        feedback.add('$', value, error + ': one complete JSON object', '只返回一个完整JSON对象，补齐必填结构。')
    elif kind == 'reference' or (error.startswith('reference') and not kind.startswith('plan')):
        _reference(value, context, feedback)
    elif (kind.startswith('plan') or error.startswith('plan')) and (
            set(value) != {'audio_draft'} or kind.startswith('plan') or
            re.search(r'(?:^|_)(?:plan|revise)(?:_\d+)?(?:_repair\d*)?$', stage)):
        # A draft timing repair replies with audio_draft only; it has no segments of its own to scan.
        # A plan stage that wrongly replied with audio_draft only still gets the full missing-field scan.
        if not (stage.startswith('chain_e2e_v1_fine_') and isinstance(violation, dict) and violation.get('hint')):
            _plan(value, context, feedback)
    if not feedback.items:
        feedback.add('$', value, error + ': valid local protocol fields, IDs and time domains',
                     '按validation_error核对必填字段、类型、枚举、ID及时间域；保留实际证据，不补造画面。')
    return feedback.items
