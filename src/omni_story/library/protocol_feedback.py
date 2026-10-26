"""Bounded mechanical feedback for rejected model replies; never repairs decisions."""
from copy import deepcopy
import json
import math
import re

from . import contracts
from .audio_contracts import VOICES


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _objects(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _strings(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


class _Feedback:
    def __init__(self):
        self.items = []

    def add(self, path, value, expected, hint):
        if len(self.items) < 30:
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
        except (ValueError, TypeError):
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


def _plan(value, context, feedback):
    segments = feedback.array(value.get('segments'), '$.segments', nonempty=True)
    by_id = {row['segment_id']: row for row in _objects(segments) if isinstance(row.get('segment_id'), str)}
    order = {ident: index for index, ident in enumerate(by_id)}
    windows = context.get('windows', {})
    windows = {row['window_id']: row for row in _objects(windows)
               if isinstance(row.get('window_id'), str)} if isinstance(windows, list) else windows
    windows = windows if isinstance(windows, dict) else {}
    slots = feedback.array(value.get('slots'), '$.slots', nonempty=True)
    assignments = {ident: [] for ident in by_id}
    for index, slot in enumerate(slots):
        if not feedback.object(slot, f'$.slots[{index}]'):
            continue
        path = f'$.slots[{index}].segment_ids'
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
        if isinstance(ident, str) and assignments.get(ident) != [segment.get('slot_id')]:
            feedback.add(path + '.slot_id', segment.get('slot_id'), assignments.get(ident),
                         f'{ident}必须恰好属于一个slot，slot.segment_ids与segment.slot_id须双向一致。')
        window_id = segment.get('window_id')
        window = windows.get(window_id) if isinstance(window_id, str) else None
        start, end = segment.get('source_in_s'), segment.get('source_out_s')
        if window is not None:
            low, high = window.get('source_start_s'), window.get('source_end_s')
            if (not all(_finite(item) for item in (start, end, low, high)) or
                    not low - 0.001 <= start < end <= high + 0.001):
                feedback.add(path + '.source_in_s/source_out_s', [start, end],
                             f'{low} <= source_in_s < source_out_s <= {high}',
                             f'{ident}只能选择窗口{window_id}内的时间；允许范围[{low}, {high}]秒，不能自动扩展窗口。')
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
            feedback.add('$.audio_draft', draft, 'array' if speech_mode == 'none' else 'non-empty array',
                         'audio_mode=silent只表示不用原声；新配音仍须输出audio_draft，每项含id、text、voice、mode、segment_ids。')
        elif speech_mode == 'none' and draft:
            feedback.add('$.audio_draft', draft, '[]', 'speech_mode=none时不添加语音。')
    entities = {}
    for window in windows.values():
        for role in _objects(window.get('observation', {}).get('roles')):
            if isinstance(role.get('role_id'), str) and isinstance(role.get('entity_id'), str):
                entities[(window.get('window_id'), role['role_id'])] = role['entity_id']
    for character in _objects(value.get('cast')):
        for binding in _objects(character.get('bindings')):
            if all(isinstance(binding.get(key), str) for key in ('window_id', 'role_id')):
                entities[(binding['window_id'], binding['role_id'])] = character.get('entity_id')
    seen = set()
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


def diagnose(stage, value, context):
    """Collect up to 30 violations after validation fails, without changing the reply."""
    feedback = _Feedback()
    error = str(context.get('validation_error', stage + ':protocol_validation_failed'))
    kind = context.get('stage', stage)
    if not isinstance(value, dict):
        feedback.add(error, value, 'one complete JSON object', '只返回一个完整JSON对象，补齐必填结构。')
    elif kind == 'reference' or error.startswith('reference'):
        _reference(value, context, feedback)
    elif kind.startswith('plan') or error.startswith('plan'):
        _plan(value, context, feedback)
    if not feedback.items:
        feedback.add(error, value, 'valid local protocol fields, IDs and time domains',
                     '按validation_error核对必填字段、类型、枚举、ID及时间域；保留实际证据，不补造画面。')
    return feedback.items
