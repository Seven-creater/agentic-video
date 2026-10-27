"""Strict contracts for picture-grounded audio; never rewrite model proposals.

These checks establish structural and time/actor bindings. They do not prove
that a visual description is true, or that generated speech matches the picture;
those require independent picture and listening reviews.
"""

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


def validate_regions(value, duration_s):
    """Validate normalized timed masks; clamp only tolerated boundary noise."""
    _duration(duration_s)
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
        _object(row, (*fields, 'segment_ids') if story_context else fields, name)
        _unique_id(row, line_ids, name)
        _text(row['text'], name + '.text')
        _require(len(row['text']) <= 600, name + '.tts_text_limit')
        _text(row['visual_basis'], name + '.visual_basis')
        _require(row['voice'] in VOICES, name + '.voice')
        _require(row['mode'] in ('dialogue', 'narration'), name + '.mode')
        _window(row, duration_s, name)
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
