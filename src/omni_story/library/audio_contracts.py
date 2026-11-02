"""Strict contracts for picture-grounded audio; never rewrite model proposals.

These checks establish structural and time/actor bindings. They do not prove
that a visual description is true, or that generated speech matches the picture;
those require independent picture and listening reviews.
"""

from copy import deepcopy
import json
import math
import re
import unicodedata

from .contracts import normalize_intervals, interval_bounds, numeric_error


VOICES = ('Ethan', 'Cherry', 'Serena', 'Moon')


def _require(ok, message):
    if not ok:
        raise ValueError('audio_contracts:' + message)


def _object(value, fields, name):
    _require(isinstance(value, dict), name + '.object')
    _require(set(value) == set(fields), name + '.fields')


def _text(value, name, *, nonempty=True):
    _require(isinstance(value, str), name + '.string')
    if nonempty:
        _require(bool(value.strip()), name + '.nonempty')
    _require(not any(unicodedata.category(c) == 'Cc' for c in value),
             name + '.control_character')


def _strings(value, name):
    _require(isinstance(value, list), name + '.list')
    for i, item in enumerate(value):
        _text(item, f'{name}[{i}]', nonempty=False)


def _finite(value, name):
    if type(value) not in (int, float):
        raise numeric_error(name + '.number', name, value, 'finite number')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise numeric_error(name + '.finite', name, value, 'finite number')


def _duration(duration_s):
    _finite(duration_s, 'duration_s')
    _require(duration_s > 0, 'duration_s.positive')


def _window(row, duration_s, name):
    start, end = row['start_s'], row['end_s']
    try:
        _finite(start, name + '.start_s')
        _finite(end, name + '.end_s')
    except ValueError as error:
        error.violation['expected'] = f'0 ≤ x ≤ {duration_s}'
        raise
    interval_bounds(start, end, 0, duration_s, name, name + '.time_range')


def _references(value, known_ids, name, *, nonempty=False):
    _require(isinstance(value, list), name + '.list')
    if nonempty:
        _require(bool(value), name + '.nonempty')
    seen = set()
    for i, item in enumerate(value):
        _text(item, f'{name}[{i}]')
        _require(item in known_ids, name + '.unknown_id')
        _require(item not in seen, name + '.duplicate_id')
        seen.add(item)


def _unique_id(row, seen, name):
    _text(row['id'], name + '.id')
    _require(row['id'] not in seen, name + '.duplicate_id')
    seen.add(row['id'])


def _covers(windows, start, end):
    cursor = start
    for window in sorted(windows, key=lambda item: item['start_s']):
        if window['end_s'] <= cursor:
            continue
        if window['start_s'] > cursor + 1e-6:
            break
        cursor = max(cursor, window['end_s'])
    return cursor >= end - 1e-6


_RECTANGLE_RULE = ('x,y,w,h must be ratios in 0..1 of the watched frame; '
                   'x+w ≤ 1, y+h ≤ 1')
_REGIONS_REPAIR_HINT = ('按括号内规则只修复该条mask列出的字段；坐标是观看画面内的归一化比例，'
                        '像素值须除以观看画面的宽/高；时间写成非空区间；颜色用#RRGGBB；其余内容保留原值。')
_MASK_FIELDS = ('x', 'y', 'w', 'h', 'start_s', 'end_s', 'color', 'reason')
_MAX_MASK_FEEDBACK = 30


def _slip_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _render(value):
    return f'{value:g}' if isinstance(value, (int, float)) else str(value)


def _rectangle_statement(row):
    """Every failed ratio condition the numeric coordinates can show, with numbers.

    A non-numeric coordinate is reported elsewhere; the conditions of the
    numeric ones are still checked, so one round reports both.
    """
    x, y, w, h = (row.get(key) if _slip_number(row.get(key)) else None for key in ('x', 'y', 'w', 'h'))
    checks = ((x is not None and x < -1e-6, lambda: f'x={x:g} < 0'),
              (y is not None and y < -1e-6, lambda: f'y={y:g} < 0'),
              (w is not None and w <= 0, lambda: f'w={w:g} ≤ 0'),
              (h is not None and h <= 0, lambda: f'h={h:g} ≤ 0'),
              (x is not None and x > 1+1e-6, lambda: f'x={x:g} > 1'),
              (y is not None and y > 1+1e-6, lambda: f'y={y:g} > 1'),
              (w is not None and w > 1+1e-6, lambda: f'w={w:g} > 1'),
              (h is not None and h > 1+1e-6, lambda: f'h={h:g} > 1'),
              (None not in (x, w) and x + w > 1+1e-6, lambda: f'x+w={x+w:g} > 1'),
              (None not in (y, h) and y + h > 1+1e-6, lambda: f'y+h={y+h:g} > 1'))
    failed = [text() for bad, text in checks if bad]
    return ', '.join(failed) if failed else None


def _widenable_point(start, end, duration_s):
    """A sampled instant inside the range covers the second it was seen at."""
    return start == end and -1e-6 <= start <= duration_s + 1e-6


def _region_problems(value, duration_s):
    """Every regions violation in the strict validator's check order.

    A widenable point sample is kept as a deferred time problem: it is widened
    when nothing else is wrong, and reported as a violation when the reply is
    rejected, so one repair round sees every mask's concrete problem.
    """
    problems = []
    if set(value) != {'masks', 'protected_text', 'limitations'}:
        problems.append(dict(name='audio_contracts:regions.fields', path='$',
            value=sorted(map(str, value)), kind='structure',
            expected='exactly the fields masks, protected_text, limitations'))
    for key in ('protected_text', 'limitations'):
        items = value.get(key)
        if not isinstance(items, list):
            problems.append(dict(name=f'audio_contracts:regions.{key}.list', path='$.' + key,
                value=items, kind='structure', expected='array of strings'))
        else:
            for i, item in enumerate(items):
                try:
                    _text(item, f'regions.{key}[{i}]', nonempty=False)
                except ValueError as error:
                    problems.append(dict(name=str(error), path=f'$.{key}[{i}]', value=item,
                        kind='structure', expected='text without control characters'))
    masks = value.get('masks')
    if not isinstance(masks, list):
        problems.append(dict(name='audio_contracts:regions.masks.list', path='$.masks',
                             value=masks, kind='structure', expected='array of mask objects'))
        return problems
    time_rule = f'0 ≤ start_s < end_s ≤ {duration_s}'
    for i, row in enumerate(masks):
        name, path = f'regions.masks[{i}]', f'$.masks[{i}]'
        if not isinstance(row, dict) or set(row) != set(_MASK_FIELDS):
            problems.append(dict(name='audio_contracts:' + name +
                ('.object' if not isinstance(row, dict) else '.fields'),
                path=path, value=row, kind='structure',
                expected='object with exactly the fields ' + ', '.join(_MASK_FIELDS)))
            if not isinstance(row, dict):
                continue
        for key in ('x', 'y', 'w', 'h'):
            try:
                _finite(row.get(key), name + '.' + key)
            except ValueError as error:
                problems.append(dict(name=str(error), path=path, value={key: row.get(key)},
                    kind='number', expected=f'{key}={json.dumps(row.get(key), ensure_ascii=False)} (need finite number)'))
        statement = _rectangle_statement(row)
        if statement is not None:
            problems.append(dict(name='audio_contracts:' + name + '.normalized_rectangle',
                path=path, value={key: row.get(key) for key in ('x', 'y', 'w', 'h')},
                kind='rectangle', expected=f'{statement} ({_RECTANGLE_RULE})'))
        start, end = row.get('start_s'), row.get('end_s')
        times_broken = False
        for key, item in (('start_s', start), ('end_s', end)):
            try:
                _finite(item, name + '.' + key)
            except ValueError as error:
                problems.append(dict(name=str(error), path=path, value={key: item},
                    kind='number', expected=f'{key}={json.dumps(item, ensure_ascii=False)} (need finite number)'))
                times_broken = True
        if not times_broken:
            statement = f'start_s={_render(start)} end_s={_render(end)} ({time_rule})'
            if _widenable_point(start, end, duration_s):
                problems.append(dict(name=name + '.time_range', path=path,
                    value=[start, end], kind='time', deferred=True, expected=statement))
            elif not (-1e-6 <= start <= duration_s + 1e-6 and -1e-6 <= end <= duration_s + 1e-6
                      and max(0, start) < min(duration_s, end)):
                problems.append(dict(name=name + '.time_range', path=path,
                    value=[start, end], kind='time', expected=statement))
        color = row.get('color')
        if not (isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color) is not None):
            shown = json.dumps(color, ensure_ascii=False) if isinstance(color, str) else str(color)
            problems.append(dict(name='audio_contracts:' + name + '.hex_color', path=path,
                value=color, kind='color', expected=f'color {shown} (need #RRGGBB)'))
        try:
            _text(row.get('reason'), name + '.reason')
        except ValueError as error:
            problems.append(dict(name=str(error), path=path, value=row.get('reason'),
                kind='reason', expected='non-empty text without control characters'))
    return problems


def _offending_masks(problems):
    """One concrete row per offending path, all of that mask's problems joined."""
    rows = []
    for problem in problems:
        value = problem['value'] if isinstance(problem['value'], dict) else {problem['kind']: problem['value']}
        for row in rows:
            if row['path'] == problem['path']:
                row['expected'] += '; ' + problem['expected']
                row['value'] = {**row['value'], **value}
                break
        else:
            if len(rows) >= _MAX_MASK_FEEDBACK:
                continue
            rows.append(dict(path=problem['path'], value=value,
                             expected=problem['expected'], repair_hint=_REGIONS_REPAIR_HINT))
    return rows


def _strict_regions(value, duration_s):
    """The original regions contract; clamps only tolerated boundary noise."""
    _object(value, ('masks', 'protected_text', 'limitations'), 'regions')
    _strings(value['protected_text'], 'regions.protected_text')
    _strings(value['limitations'], 'regions.limitations')
    _require(isinstance(value['masks'], list), 'regions.masks.list')
    for i, row in enumerate(value['masks']):
        name = f'regions.masks[{i}]'
        _object(row, ('x', 'y', 'w', 'h', 'start_s', 'end_s', 'color', 'reason'), name)
        for key in ('x', 'y', 'w', 'h'):
            _finite(row[key], name + '.' + key)
        _require(-1e-6 <= row['x'] <= 1+1e-6 and -1e-6 <= row['y'] <= 1+1e-6
                 and 0 < row['w'] <= 1+1e-6 and 0 < row['h'] <= 1+1e-6
                 and row['x'] + row['w'] <= 1+1e-6
                 and row['y'] + row['h'] <= 1+1e-6, name + '.normalized_rectangle')
        _window(row, duration_s, name)
        row['w'], row['h'] = min(1, row['w']), min(1, row['h'])
        if row['x'] < 0 or row['x'] + row['w'] > 1:
            row['x'] = max(0, 1-row['w']) if row['x'] >= 0 else 0
        if row['y'] < 0 or row['y'] + row['h'] > 1:
            row['y'] = max(0, 1-row['h']) if row['y'] >= 0 else 0
        row['start_s'], row['end_s'] = max(0, row['start_s']), min(duration_s, row['end_s'])
        interval_bounds(row['start_s'], row['end_s'], 0, duration_s, name, name + '.time_range')
        _require(isinstance(row['color'], str)
                 and re.fullmatch(r'#[0-9a-fA-F]{6}', row['color']) is not None,
                 name + '.hex_color')
        _text(row['reason'], name + '.reason')
    return value


def _widen_points(value, duration_s):
    """Widen whole-second point samples in place; return how many were widened.

    The model samples whole seconds, so a subtitle reported at start_s ==
    end_s == t covers [t, t+1] (the last second when t is the range end).
    """
    masks = value.get('masks') if isinstance(value, dict) else None
    widened = 0
    for row in masks if isinstance(masks, list) else ():
        if not isinstance(row, dict):
            continue
        start, end = row.get('start_s'), row.get('end_s')
        if _slip_number(start) and _slip_number(end) and _widenable_point(start, end, duration_s):
            if start >= duration_s:
                row['start_s'] = max(0, duration_s - 1)
            else:
                row['end_s'] = min(start + 1, duration_s)
            widened += 1
    return widened


def validate_regions(value, duration_s, *, normalisations=None):
    """Validate normalized timed masks; clamp only tolerated boundary noise.

    Pass/fail and the raised error are the original strict contract's. Point
    samples are widened on a copy that replaces the reply only when the whole
    copy passes; a rejected reply is untouched, and its error carries every
    mask problem so one repair round can fix them all. Pixel-like coordinates
    are never rescaled: the model's pixel space need not be the watched file's
    (a 720x406 window came back with y=902).
    """
    _duration(duration_s)
    _require(isinstance(value, dict), 'regions.object')
    candidate = deepcopy(value)
    widened = _widen_points(candidate, duration_s)
    try:
        _strict_regions(candidate, duration_s)
    except ValueError:
        try:
            _strict_regions(deepcopy(value), duration_s)
        except ValueError as error:
            problems = _region_problems(value, duration_s)
            if problems:
                error.violation = dict(path=problems[0]['path'], value=problems[0]['value'],
                                       expected=problems[0]['expected'],
                                       offending_masks=_offending_masks(problems))
            raise
        raise
    value.clear()
    value.update(candidate)
    if normalisations is not None:
        normalisations[:] = ([dict(kind='point_time_widened', mask_count=widened, widened_s=1)]
                             if widened else [])
    return value


def validate_observation(value, duration_s, *, normalize=False):
    """Validate visual events and their explicit speaker attribution."""
    _duration(duration_s)
    _object(value, ('actors', 'events', 'limitations'), 'observation')
    _strings(value['limitations'], 'observation.limitations')
    _require(isinstance(value['actors'], list), 'observation.actors.list')
    actor_ids = set()
    for i, row in enumerate(value['actors']):
        name = f'observation.actors[{i}]'
        _object(row, ('id', 'appearance'), name)
        _unique_id(row, actor_ids, name)
        _text(row['appearance'], name + '.appearance')
    _require(isinstance(value['events'], list), 'observation.events.list')
    if normalize:
        normalize_intervals(value, value, 'events', duration_s)
    event_ids = set()
    for i, row in enumerate(value['events']):
        name = f'observation.events[{i}]'
        _object(row, ('id', 'start_s', 'end_s', 'visible', 'actor_ids',
                      'speech_mode', 'speech_actor_id'), name)
        _unique_id(row, event_ids, name)
        _window(row, duration_s, name)
        _text(row['visible'], name + '.visible')
        _references(row['actor_ids'], actor_ids, name + '.actor_ids')
        _require(row['speech_mode'] in ('dialogue', 'narration', 'none'),
                 name + '.speech_mode')
        if row['speech_mode'] == 'dialogue':
            _text(row['speech_actor_id'], name + '.speech_actor_id')
            _require(row['speech_actor_id'] in actor_ids,
                     name + '.speech_actor_id.unknown_id')
            _require(row['speech_actor_id'] in row['actor_ids'],
                     name + '.speech_actor_id.not_visible_actor')
        else:
            _require(row['speech_actor_id'] is None, name + '.non_dialogue_speaker')
    return value


def validate_plan(value, observation, duration_s, *, story_context=None):
    """Bind dialogue to its speaker event, and narration to continuous windows."""
    validate_observation(observation, duration_s)
    if story_context:
        from .story_context import validate_story_context
        validate_story_context(story_context)
        _require(math.isclose(story_context['duration_s'], duration_s, rel_tol=0, abs_tol=.075),
                 'story_context.duration_s')
    _object(value, ('lines', 'limitations'), 'plan')
    _strings(value['limitations'], 'plan.limitations')
    _require(isinstance(value['lines'], list), 'plan.lines.list')
    if not value['lines']:
        from .contracts import reference_strategy
        _require(bool(story_context) and reference_strategy(story_context['reference'],
            creative_audio=True)['speech_mode'] == 'none', 'plan.lines.nonempty')
    actor_ids = {row['id'] for row in observation['actors']}
    events = {row['id']: row for row in observation['events']}
    segments = {row['id']: row for row in story_context['segments']} if story_context else {}
    line_ids = set()
    actor_voices = {}
    windows = []
    for i, row in enumerate(value['lines']):
        name = f'plan.lines[{i}]'
        fields = ('id', 'text', 'voice', 'speaker_id', 'mode', 'start_s',
                  'end_s', 'event_ids', 'visual_basis')
        if 'min_window_s' in row:
            fields = (*fields, 'min_window_s')
        _object(row, (*fields, 'segment_ids') if story_context else fields, name)
        _unique_id(row, line_ids, name)
        _text(row['text'], name + '.text')
        _require(len(row['text']) <= 600, name + '.tts_text_limit')
        _text(row['visual_basis'], name + '.visual_basis')
        _require(row['voice'] in VOICES, name + '.voice')
        _require(row['mode'] in ('dialogue', 'narration'), name + '.mode')
        _window(row, duration_s, name)
        from .line_windows import validate_line_window
        validate_line_window(row, row['end_s'] - row['start_s'], f'$.lines[{i}]',
                             received_tts=(story_context or {}).get('received_tts', []))
        _references(row['event_ids'], events, name + '.event_ids',
                    nonempty=row['mode'] == 'dialogue' or not story_context)
        if story_context:
            _references(row['segment_ids'], segments, name + '.segment_ids', nonempty=True)
            _require(_covers([segments[segment_id] for segment_id in row['segment_ids']],
                             row['start_s'], row['end_s']), name + '.segment_window')
        if row['mode'] == 'dialogue':
            _text(row['speaker_id'], name + '.speaker_id')
            _require(row['speaker_id'] in actor_ids, name + '.speaker_id.unknown_id')
            previous_voice = actor_voices.setdefault(row['speaker_id'],row['voice'])
            _require(previous_voice == row['voice'], name + '.speaker_voice_changed')
        else:
            _require(row['speaker_id'] is None, name + '.narration_speaker')
        if row['mode'] == 'dialogue':
            _require(any(events[event_id]['start_s'] <= row['start_s']
                         and row['end_s'] <= events[event_id]['end_s']
                         for event_id in row['event_ids']), name + '.event_window')
        else:
            windows_for_line = [events[event_id] for event_id in row['event_ids']]
            windows_for_line.extend(segments[segment_id] for segment_id in row.get('segment_ids', []))
            _require(_covers(windows_for_line, row['start_s'], row['end_s']), name + '.event_window')
        for event_id in row['event_ids']:
            event = events[event_id]
            if row['mode'] == 'dialogue':
                _require(event['speech_mode'] == 'dialogue', name + '.event_not_dialogue')
                _require(row['speaker_id'] in event['actor_ids']
                         and event['speech_actor_id'] == row['speaker_id'],
                         name + '.event_speaker_mismatch')
        windows.append((row['start_s'], row['end_s']))
    previous_end = 0
    for start, end in sorted(windows):
        _require(start >= previous_end - 1e-6, 'plan.lines.overlap')
        previous_end = end
    return value
