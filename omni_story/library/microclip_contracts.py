"""Small frame-grounded editing protocol; no human footage decisions."""
import math


def _require(ok, message):
    if not ok:
        raise ValueError('microclip:' + message)


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), name)


def _strings(value, name):
    _require(isinstance(value, list) and all(isinstance(s, str) for s in value), name)


def validate_observation(value, shown, catalog, *, first=False):
    _require(isinstance(value, dict), 'observation_object')
    rows = value.get('frames')
    expected = {f['frame_id'] for f in shown}
    _require(isinstance(rows, list) and len(rows) == len(expected)
             and {r.get('frame_id') for r in rows if isinstance(r, dict)} == expected, 'all_shown_frames_once')
    for row in rows:
        _text(row.get('visible_action_or_state'), 'visible_action_or_state')
        _require(isinstance(row.get('visible_text'), str), 'visible_text')
    _text(value.get('visible_event'), 'visible_event')
    _strings(value.get('missing_information'), 'missing_information')
    _strings(value.get('limitations'), 'limitations')
    next_view = value.get('next_observation')
    _require(isinstance(next_view, dict) and next_view.get('action') in {'zoom', 'ready', 'stop'}, 'next_action')
    _text(next_view.get('reason'), 'next_reason')
    if first:
        _require(next_view['action'] in {'zoom', 'stop'}, 'first_view_requires_fine_observation')
    if next_view['action'] == 'zoom':
        a, b = next_view.get('start_frame_id'), next_view.get('end_frame_id')
        _require(a in catalog and b in catalog and catalog[a]['source_time_s'] < catalog[b]['frame_end_s'], 'zoom_frame_bounds')
        _text(next_view.get('question'), 'specific_observation_question')
    return value


def validate_motion(value, duration):
    _require(isinstance(value, dict), 'motion_object')
    _text(value.get('visible_meaning'), 'visible_meaning')
    _strings(value.get('limitations'), 'limitations')
    events = value.get('events')
    _require(isinstance(events, list) and bool(events), 'visible_events')
    for event in events:
        _require(isinstance(event, dict), 'event_object')
        a, b = event.get('start_s'), event.get('end_s')
        _require(type(a) in (int, float) and type(b) in (int, float)
                 and math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= duration + .05, 'local_event_time')
        _text(event.get('visible_content'), 'visible_content')
        _require(isinstance(event.get('text_evidence'), str), 'text_evidence')
    return value


def validate_anchors(value, frames):
    _require(isinstance(value, dict), 'anchors_object')
    ids = [value.get(k + '_frame_id') for k in ('start', 'peak', 'end')]
    _require(all(i in frames for i in ids), 'observed_anchor_ids')
    times = [frames[i]['source_time_s'] for i in ids]
    _require(times[0] < times[1] < times[2], 'three_ordered_anchor_instants')
    _text(value.get('reason'), 'anchor_reason')
    _text(value.get('expected_visible_change'), 'expected_visible_change')
    _strings(value.get('limitations'), 'limitations')
    return value


def validate_plan(source, slot, value, frames, boundary_ids):
    plan = compile_plan(source, slot, value, frames)
    _require(all(s[k] in boundary_ids for s in value['shots'] for k in ('start_frame_id', 'end_frame_id')),
             'boundaries_require_observed_neighbor_frames')
    return plan


def compile_plan(source, slot, value, frames):
    _require(isinstance(value, dict), 'plan_object')
    _text(value.get('preserved_visible_meaning'), 'preserved_visible_meaning')
    _strings(value.get('omitted_content'), 'omitted_content')
    _strings(value.get('limitations'), 'limitations')
    shots = value.get('shots')
    _require(isinstance(shots, list) and 1 <= len(shots) <= 8, 'one_to_eight_shots')
    segments, previous = [], slot['start_s']
    for shot in shots:
        _require(isinstance(shot, dict), 'shot_object')
        a, b = shot.get('start_frame_id'), shot.get('end_frame_id')
        _require(a in frames and b in frames, 'observed_frame_ids_required')
        first, last = frames[a], frames[b]
        _require(first['source_sha256'] == last['source_sha256'] == source['sha256'], 'frame_source_sha')
        start, end = first['source_time_s'], last.get('frame_end_s')
        _require(type(end) in (int, float) and slot['start_s'] <= start < end <= slot['end_s'] + .000001
                 and start >= previous - .000001, 'ordered_source_range_and_real_end')
        speed, hold = shot.get('speed'), shot.get('hold_s')
        _require(type(speed) in (int, float) and math.isfinite(speed) and .5 <= speed <= 2, 'speed')
        _require(round((end - start) / speed * 30) >= 1, 'at_least_one_output_motion_frame')
        _require(type(hold) in (int, float) and math.isfinite(hold) and 0 <= hold <= 3, 'hold_s')
        _text(shot.get('reason'), 'shot_reason')
        _text(shot.get('visible_change'), 'visible_change')
        segments.append({'source_id': source['source_id'], 'slot_id': slot['slot_id'], 'window_id': slot['slot_id'],
                         'source_in_s': start, 'source_out_s': end, 'speed': speed,
                         'freeze_tail_s': hold, 'look': 'none', 'framing': 'fit'})
        previous = end
    return {'fps': 30, 'width': 720, 'height': 1280, 'audio_mode': 'source', 'segments': segments}


def validate_review(value):
    _require(isinstance(value, dict), 'review_object')
    for key in ('status', 'key_moment_selection', 'economy', 'readability'):
        _require(value.get(key) in {'pass', 'partial', 'fail'}, key)
    _text(value.get('reason'), 'review_reason')
    _strings(value.get('limitations'), 'limitations')
    return value
