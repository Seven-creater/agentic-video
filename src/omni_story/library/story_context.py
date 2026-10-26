"""One reference goal and model-owned script, carried through actual edit times."""
from copy import deepcopy
from pathlib import Path
import math
import json

from .media import sha256_file
from .state import LibraryStopped, json_sha


def core_missing(review):
    missing = review.get('missing_core_meaning', [])
    if not isinstance(missing, list) or any(not isinstance(s, str) or not s.strip() for s in missing):
        raise ValueError('story_context:missing_core_meaning_string_array')
    return bool(missing) or review.get('next_action') == 'reselect_material'


def validate_story_context(context, reference_sha=None, video_sha=None):
    if not isinstance(context, dict):
        raise LibraryStopped('story_context:required')
    reference = context.get('reference', {})
    if any(not isinstance(reference.get(k), str) or not reference[k].strip()
           for k in ('reference_sha256', 'theme', 'intended_takeaway')):
        raise LibraryStopped('story_context:reference_goal_required')
    if reference_sha is not None and reference['reference_sha256'] != reference_sha:
        raise LibraryStopped('story_context:reference_changed')
    if video_sha is not None and context.get('video_sha256') != video_sha:
        raise LibraryStopped('story_context:edited_video_changed')
    from .contracts import reference_strategy, validate_audio_draft, validate_dialogue_speakers
    strategy = reference_strategy(reference, creative_audio=True)
    duration = context.get('duration_s')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('story_context:actual_duration_required')
    segments = context.get('segments', [])
    ids, last = set(), 0.0
    for row in segments:
        start, end = row['start_s'], row['end_s']
        if (not isinstance(row.get('id'), str) or not row['id'] or row['id'] in ids or
                any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (start, end)) or
                not 0 <= start < end <= duration + 1e-6 or start < last - 1e-6):
            raise ValueError('story_context:invalid_output_segment')
        if not isinstance(row.get('purpose'), str) or not row['purpose'].strip():
            raise ValueError('story_context:segment_purpose_required')
        for key in ('visible_facts', 'source_text_context', 'unsupported_claims'):
            if not isinstance(row.get(key, []), list) or any(not isinstance(s, str) for s in row.get(key, [])):
                raise ValueError('story_context:segment_text_arrays_required')
        for event in row.get('visual_events', []):
            begin, finish = event.get('start_s'), event.get('end_s')
            if (any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
                    for t in (begin, finish)) or not start - 1e-6 <= begin < finish <= end + 1e-6
                    or not isinstance(event.get('observed_fact'), str) or not event['observed_fact'].strip()):
                raise ValueError('story_context:invalid_visual_event')
        ids.add(row['id'])
        last = end
    if not segments:
        raise ValueError('story_context:actual_segments_required')
    validate_audio_draft(context.get('audio_draft'), [dict(row, segment_id=row['id']) for row in segments],
                         speech_mode=strategy['speech_mode'], cast=context.get('cast'))
    if context.get('cast') is not None:
        validate_dialogue_speakers(context['audio_draft'], segments, cast=context['cast'])
    by_id = {row['id']: row for row in segments}
    for line in context['audio_draft']:
        windows = [by_id[s] for s in line['segment_ids']]
        start, end = line['start_s'], line['end_s']
        if not windows[0]['start_s'] - 1e-6 <= start < end <= windows[-1]['end_s'] + 1e-6:
            raise ValueError('story_context:draft_outside_real_segments')
    return context


def _map_events(events, segment, actual):
    """Keep only surviving event ranges, in the current output time domain."""
    selected_start, selected_end = segment['source_in_s'], segment['source_out_s']
    speed = segment.get('speed', 1)
    output_start = actual.get('output_in_s', actual.get('output_start_s'))
    output_end = actual.get('output_out_s', actual.get('output_end_s'))
    result = []
    for event in events:
        start, end = max(selected_start, event['start_s']), min(selected_end, event['end_s'])
        if start >= end:
            continue
        begin = output_start + (start - selected_start) / speed
        finish = min(output_end, output_start + (end - selected_start) / speed)
        if begin >= finish:
            continue
        result.append({**event, 'start_s': begin, 'end_s': finish,
            'complete_event': event.get('complete_event', True) and
                start <= event['start_s'] + 1e-6 and end >= event['end_s'] - 1e-6})
    return result


def _window_events(window):
    offset = window['source_start_s']
    entities = {role['role_id']: role['entity_id'] for role in window['observation']['roles']
                if role.get('entity_id')}
    return [dict(window_id=window['window_id'], source_id=window['source_id'], event_index=index,
        source_in_s=offset + event['local_start_s'], source_out_s=offset + event['local_end_s'],
        start_s=offset + event['local_start_s'], end_s=offset + event['local_end_s'],
        observed_fact=event['observed_fact'], role_ids=list(event['role_ids']),
        entity_ids=[entities[role_id] for role_id in event['role_ids'] if role_id in entities], complete_event=True)
        for index, event in enumerate(window['observation']['events'])]


def _anchor_start(line, segments):
    anchor = line.get('start_anchor')
    return (segments[anchor['segment_id']]['start_s'] + anchor['offset_s']) if anchor else None


def _actual_slots(plan, segments, parent):
    """Carry model-owned paragraph tasks through the actual selected ranges."""
    parent_segments = {row['id']: row for row in (parent or {}).get('segments', [])}
    parent_slots = {row['slot_id']: row for row in (parent or {}).get('slots', [])}
    for row in parent_segments.values():
        if row.get('slot_id') and row['slot_id'] not in parent_slots:
            parent_slots[row['slot_id']] = {key: deepcopy(row[key]) for key in
                ('slot_id', 'intended_takeaway', 'coverage') if key in row}
    result = {}
    for requested, actual in zip(plan['segments'], segments):
        explicit = [slot for slot in plan.get('slots', []) if
            slot['slot_id'] == requested.get('slot_id') or requested['segment_id'] in slot.get('segment_ids', [])]
        contributions = []
        for slot in explicit:
            contributions.append((slot, actual['start_s'], actual['end_s']))
        if not explicit:
            for slot in parent_slots.values():
                ranges = slot.get('ranges')
                if ranges is None:
                    ranges = [row for row in parent_segments.values() if row['id'] in slot.get('segment_ids', []) or
                        row.get('slot_id') == slot['slot_id'] or slot['slot_id'] in row.get('slot_ids', [])]
                for source_range in ranges:
                    start = max(requested['source_in_s'], source_range['start_s'])
                    end = min(requested['source_out_s'], source_range['end_s'])
                    if start >= end:
                        continue
                    begin = actual['start_s'] + (start - requested['source_in_s']) / requested.get('speed', 1)
                    finish = (actual['end_s'] if end >= requested['source_out_s'] - 1e-6 else
                        min(actual['end_s'], actual['start_s'] + (end - requested['source_in_s']) / requested.get('speed', 1)))
                    if begin < finish:
                        contributions.append((slot, begin, finish))
        slot_ids = []
        for slot, start, end in contributions:
            slot_id = slot['slot_id']
            if slot_id not in result:
                inherited = parent_slots.get(slot_id, {}) if explicit else {}
                result[slot_id] = {key: deepcopy(value) for key, value in {**inherited, **slot}.items()
                    if key not in ('segment_ids', 'start_s', 'end_s', 'ranges')}
                result[slot_id].update(segment_ids=[], ranges=[])
            task = result[slot_id]
            if actual['id'] not in task['segment_ids']:
                task['segment_ids'].append(actual['id'])
            ranges = task['ranges']
            if ranges and ranges[-1]['segment_id'] == actual['id'] and start <= ranges[-1]['end_s'] + 1e-6:
                ranges[-1]['end_s'] = max(ranges[-1]['end_s'], end)
            else:
                ranges.append(dict(segment_id=actual['id'], start_s=start, end_s=end))
            if slot_id not in slot_ids:
                slot_ids.append(slot_id)
        if len(slot_ids) == 1:
            actual['slot_id'] = slot_ids[0]
            if 'coverage' in result[slot_ids[0]]:
                actual['coverage'] = deepcopy(result[slot_ids[0]]['coverage'])
        elif slot_ids:
            actual.pop('coverage', None)
            actual['slot_ids'] = slot_ids
    for slot in result.values():
        slot.update(start_s=min(row['start_s'] for row in slot['ranges']),
                    end_s=max(row['end_s'] for row in slot['ranges']))
    return list(result.values())


def _build(reference, plan, render, parent=None, windows=None, inherited_speaker_ids=()):
    reference = deepcopy(reference.get('reference', reference))
    from .contracts import reference_strategy, validate_audio_draft
    strategy = reference_strategy(reference, creative_audio=True)
    supplied_windows = windows is not None
    windows = {w['window_id']: w for w in windows} if isinstance(windows, list) else windows or {}
    duration = render.get('measured_duration_s', render.get('duration_s'))
    rows = render.get('provenance', render.get('segments', []))
    if len(rows) != len(plan['segments']):
        raise ValueError('story_context:render_segment_mapping_missing')
    slots = {s['slot_id']: s for s in plan.get('slots', [])}
    cast = plan.get('cast', parent.get('cast') if parent else None)
    segments = []
    for index, (segment, actual) in enumerate(zip(plan['segments'], rows)):
        if actual.get('segment_id', segment['segment_id']) != segment['segment_id'] or actual.get('segment_index', index) != index:
            raise ValueError('story_context:render_segment_identity_changed')
        start = actual.get('output_in_s', actual.get('output_start_s'))
        end = actual.get('output_out_s', actual.get('output_end_s'))
        slot = slots.get(segment.get('slot_id'), {})
        purpose = segment.get('reason') or slot.get('intended_takeaway') or segment.get('key_change')
        inherited = []
        if parent:
            inherited = [s for s in parent['segments'] if
                         s['start_s'] < segment['source_out_s'] and segment['source_in_s'] < s['end_s']]
        window = windows.get(segment.get('window_id'))
        if not parent and supplied_windows:
            if (window is None or window.get('status') != 'watched' or
                    window.get('source_id') != segment.get('source_id')):
                raise ValueError('story_context:selected_window_evidence_missing')
            from .contracts import validate_fine
            validate_fine(window.get('observation'), window)
            if not (window['source_start_s'] - 1e-6 <= segment['source_in_s'] <
                    segment['source_out_s'] <= window['source_end_s'] + 1e-6):
                raise ValueError('story_context:selected_range_outside_observation')
        events = ([event for row in inherited for event in row.get('visual_events', [])] if parent else
                  _window_events(window) if window and window.get('status') == 'watched' else [])
        events = _map_events(events, segment, actual) if events else []
        source_text = ([] if strategy['adaptation_mode'] == 'reinterpret' else
            list(segment.get('source_text_context', [])) +
            [text for s in inherited for text in s.get('source_text_context', [])])
        segments.append(dict(id=segment['segment_id'], start_s=start, end_s=end,
                             purpose=purpose, visible_facts=list(dict.fromkeys([
                                 event['observed_fact'] for event in events if event['complete_event']])),
                             visual_events=events,
                             source_text_context=list(dict.fromkeys(source_text)),
                             unsupported_claims=list(dict.fromkeys([
                                 *segment.get('unsupported_claims', []),
                                 *(text for s in inherited for text in s.get('unsupported_claims', []))]))))
        row = segments[-1]
        row['entity_ids'] = list(dict.fromkeys(entity for event in events for entity in event.get('entity_ids', [])))
        if window:
            row.update(window_id=window['window_id'], role_ids=list(segment.get('role_ids', [])))
        for key in ('shot_role', 'reason'):
            if key in segment:
                row[key] = deepcopy(segment[key])
            elif len(inherited) == 1 and key in inherited[0]:
                row[key] = deepcopy(inherited[0][key])
        if 'coverage' in slot:
            row['coverage'] = deepcopy(slot['coverage'])
        elif len(inherited) == 1 and 'coverage' in inherited[0]:
            row['coverage'] = deepcopy(inherited[0]['coverage'])
    actual_slots = _actual_slots(plan, segments, parent)
    validate_audio_draft(plan.get('audio_draft'), plan['segments'], speech_mode=strategy['speech_mode'], cast=cast)
    drafts = deepcopy(plan['audio_draft'])
    by_id = {s['id']: s for s in segments}
    # Multiple lines sharing a slot divide its real window; this is a draft estimate,
    # never a substitute for the subsequent measured waveform duration.
    groups = {}
    for line in drafts:
        line.setdefault('mode', 'narration')
        groups.setdefault(tuple(line['segment_ids']), []).append(line)
    for segment_ids, lines in groups.items():
        start, end = by_id[segment_ids[0]]['start_s'], by_id[segment_ids[-1]]['end_s']
        cursor = start
        for i, line in enumerate(lines):
            anchor = _anchor_start(line, by_id)
            cursor = max(cursor, anchor) if anchor is not None else cursor
            remaining = sum(len(item['text']) for item in lines[i:])
            finish = end if i == len(lines) - 1 else cursor + (end - cursor) * len(line['text']) / remaining
            line.update(start_s=cursor, end_s=finish)
            cursor = finish
    context = dict(reference=reference, video_sha256=render['sha256'], duration_s=duration,
                   segments=segments, audio_draft=drafts, audio_timing='estimated')
    if actual_slots:
        context['slots'] = actual_slots
    if cast is not None:
        context['cast'] = deepcopy(cast)
    if parent:
        for key in ('received_tts', 'tts_cache_dir'):
            if key in parent:
                context[key] = deepcopy(parent[key])
        if parent.get('source_visual_policy') == 'silent_subtitle_masked':
            context['source_visual_policy'] = parent['source_visual_policy']
    elif windows:
        selected_windows = [windows.get(row.get('window_id'), {}) for row in plan['segments']]
        if all(window.get('source_visual_policy') == 'silent_subtitle_masked' and
               isinstance(window.get('source_subtitle_masks'), list) for window in selected_windows):
            context['source_visual_policy'] = 'silent_subtitle_masked'
    for line in drafts:
        if (line['id'] not in inherited_speaker_ids or line.get('mode') != 'dialogue' or
                'identity_anchor_segment_id' in line or
                any(edge.get('relation') == 'on_screen' for edge in line.get('audio_relation', []))):
            continue
        speaker = line['speaker']
        entities = {character['entity_id'] for character in cast or [] if any(
            binding['window_id'] == speaker['window_id'] and binding['role_id'] == speaker['role_id']
            for binding in character['bindings'])}
        if len(entities) != 1:
            continue
        entity = next(iter(entities))
        if any(entity in by_id[segment_id]['entity_ids'] for segment_id in line['segment_ids']):
            continue
        earlier = [row for row in segments if row['end_s'] <= by_id[line['segment_ids'][0]]['start_s'] + 1e-6
                   and entity in row['entity_ids']]
        if earlier:
            line['identity_anchor_segment_id'] = earlier[-1]['id']
    return validate_story_context(context)


def build_rough_context(reference, plan, render, *, windows=None):
    return _build(reference, plan, render, windows=windows)


def build_fine_context(reference, parent_context, plan, render):
    validate_story_context(parent_context)
    plan = deepcopy(plan)
    previous = {line['id']: line for line in parent_context['audio_draft']}
    inherited_speaker_ids = set()
    for line in plan['audio_draft']:
        original = previous.get(line['id'])
        if (original is not None and line.get('voice') == original.get('voice') and
                line.get('mode') == original.get('mode')):
            for key in ('speaker', 'cast_id'):
                if key not in line and key in original:
                    line[key] = deepcopy(original[key])
                    if key == 'speaker':
                        inherited_speaker_ids.add(line['id'])
    return _build(reference, plan, render, parent=parent_context, inherited_speaker_ids=inherited_speaker_ids)


def synthesize_draft(context, tts_dir, tts=None, *, linked_ledgers=()):
    """One rough voice draft; unchanged received lines remain reusable at finecut."""
    validate_story_context(context)
    from .tts import ENDPOINT, MODEL, synthesize_line
    for saved in Path(tts_dir).glob('*/state.json'):
        if json.loads(saved.read_text(encoding='utf-8')).get('status') in {'submitted', 'uncertain'}:
            raise LibraryStopped('rough_tts_request_outcome_unknown_no_replay')
    result = deepcopy(context)
    result['audio_timing'] = 'revised' if context.get('audio_timing') == 'revised' else 'measured'
    result['tts_cache_dir'] = str(Path(tts_dir).resolve())
    result['received_tts'], result['timing_problems'] = [], []
    saved_lines = {(row['text'], row['voice']): row for row in context.get('received_tts', [])}
    for line in context['audio_draft']:
        saved = saved_lines.get((line['text'], line['voice']))
        if saved:
            if (not Path(saved['audio_path']).is_file() or
                    sha256_file(saved['audio_path']) != saved['audio_sha256']):
                raise LibraryStopped('rough_received_tts_changed_no_replay')
            audio = {**saved, 'duration_s': saved['actual_duration_s']}
        else:
            key = 'utt_' + json_sha(dict(provider=ENDPOINT, model=MODEL, language='Chinese',
                                        text=line['text'], voice=line['voice']))[:20]
            options = dict(linked_ledgers=linked_ledgers) if linked_ledgers and tts is None else {}
            audio = (tts or synthesize_line)(line['text'], line['voice'], tts_dir, line_id=key, **options)
        if (isinstance(audio['duration_s'], bool) or not isinstance(audio['duration_s'], (int, float)) or
                not math.isfinite(audio['duration_s']) or audio['duration_s'] <= 0):
            raise ValueError('story_context:received_tts_duration_required')
        result['received_tts'].append(dict(id=line['id'], text=line['text'], voice=line['voice'],
            audio_path=audio['audio_path'], audio_sha256=audio['audio_sha256'],
            actual_duration_s=audio['duration_s']))
    by_id = {row['id']: row for row in context['segments']}
    cursor = 0.0
    for line, audio in zip(result['audio_draft'], result['received_tts']):
        first, last = by_id[line['segment_ids'][0]], by_id[line['segment_ids'][-1]]
        bounded = context.get('audio_timing') == 'revised'
        anchor = _anchor_start(line, by_id)
        intended_start = anchor if anchor is not None else line['start_s'] if bounded else first['start_s']
        start = max(intended_start, cursor)
        limit = line['end_s'] if bounded else last['end_s']
        finish = start + audio['actual_duration_s']
        if finish > limit + 1e-6 or anchor is not None and cursor > anchor + 1e-6:
            result['timing_problems'].append(dict(line_id=line['id'],
                actual_duration_s=audio['actual_duration_s'], available_s=max(0, limit - start),
                reason='start_anchor_overlap' if anchor is not None and cursor > anchor + 1e-6 else 'tts_exceeds_window'))
        else:
            line.update(start_s=start, end_s=finish)
        cursor = finish
    return result
