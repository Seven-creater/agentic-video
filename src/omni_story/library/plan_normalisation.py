"""Mechanical plan IDs derived from evidence, before the unchanged validators."""
from copy import deepcopy

from . import contracts
from .film_cast import canonical_name, normalise
from .line_windows import draft_line_window_details, segment_output_timeline

# Half a second is sustained reused footage, not a shared cut boundary or rounding.
MAX_SOURCE_OVERLAP_S = .5

PLAN_NORMALISATION_PROMPT = '''
程序在解析后、校验前按连续可用范围拆段并同步片段引用，确定性填写或纠正segments.source_id（及已有source_sha256）、
cast.bindings、entity_id、focus_role_bindings及对白的片段挂载；window_id决定来源，未知窗口仍须重选。
模型负责选择真实segments、故事角色与台词。这些机械字段可留空或近似填写，仍接受模型提供的值。
填写角色canonical_name或观察entity_id以明确身份；程序只使用已确认观察和qfs规范名/别名，不推测身份。
focus_role_id可引用cast_id、规范名/别名或观察entity_id；未提供可辨身份时保留有效焦点绑定作为身份依据。
对白挂错段时程序依次尝试最近的可见且容纳台词的入选段、当前或更早的身份锚、合同允许的旁白。
每次更改写入plan_normalisations:[{path,before,after,reason}]；无法满足的身份/区间/连贯性仍由原校验拒绝。
同源素材重叠严格大于0.5秒会作为可修复错误拒绝；当前schema没有刻意重复的豁免字段。
'''


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _names(row, film_cast):
    values = [row.get(key) for key in ('canonical_name', 'name', 'cast_id')]
    values += row.get('aliases', []) if isinstance(row.get('aliases'), list) else []
    return {normalise(canonical_name(value, film_cast)) for value in values
            if isinstance(value, str) and value.strip()}


def _set(plan, row, key, value, path, reason, *, remove=False):
    if (remove and key not in row) or (not remove and key in row and row[key] == value):
        return
    before = deepcopy(row.get(key))
    if remove:
        row.pop(key, None)
    else:
        row[key] = deepcopy(value)
    plan.setdefault('plan_normalisations', []).append(dict(
        path=path, before=before, after=deepcopy(value), reason=reason))


def _confirmed(windows):
    windows = windows.values() if isinstance(windows, dict) else windows or []
    return [(window, role) for window in _rows(list(windows)) if window.get('status') == 'watched'
            for role in _rows((window.get('observation') or {}).get('roles'))
            if role.get('identity_confirmed') is True and isinstance(role.get('role_id'), str)]


def _observed_identity(window, role, film_cast):
    names = _names(role, film_cast)
    for identity in _rows(window.get('qfs_identity')):
        if role.get('entity_id') and identity.get('entity_id') == role['entity_id']:
            names |= _names(identity, film_cast)
    return dict(entity_id=role.get('entity_id'), names=names)


def _same_identity(left, right):
    return (bool(left.get('entity_id')) and left['entity_id'] == right.get('entity_id') or
            bool(left['names'] & right['names']))


def _segment_sources(plan, windows):
    lookup = windows if isinstance(windows, dict) else {w['window_id']: w for w in _rows(windows)}
    for index, segment in enumerate(_rows(plan.get('segments'))):
        window_id = segment.get('window_id')
        window = lookup.get(window_id) if isinstance(window_id, str) else None
        if window is None:
            continue  # Unknown windows are creative selections for the validator.
        for key in ('source_id', 'source_sha256'):
            if key in window and (key == 'source_id' or key in segment):
                _set(plan, segment, key, window[key], f'$.segments[{index}].{key}', 'copy_selected_window_source')


def _reference_fields(value, parts, path='$'):
    if not parts:
        return
    key, *rest = parts
    if key == '*' and isinstance(value, list):
        for index, row in enumerate(value):
            yield from _reference_fields(row, rest, f'{path}[{index}]')
    elif isinstance(value, dict) and key in value:
        if rest:
            yield from _reference_fields(value[key], rest, path + '.' + key)
        else:
            yield value, key, path + '.' + key


def _split_references(plan, replacements, *, fps=None):
    for field in contracts.plan_field_contract()['segment_references']:
        for row, key, path in _reference_fields(plan, field['path']):
            value, policy = row[key], field['on_split']
            if policy == 'expand' and isinstance(value, list) and all(isinstance(old, str) for old in value):
                value = [new['segment_id'] for old in value
                         for new in replacements.get(old, [dict(segment_id=old)])]
            elif policy == 'duplicate_edges' and isinstance(value, list) and all(
                    isinstance(edge, dict) and isinstance(edge.get('segment_id'), str) for edge in value):
                value = [dict(edge, segment_id=new['segment_id'])
                         for edge in value for new in replacements.get(edge['segment_id'], [edge])]
            elif policy == 'first' and isinstance(value, str) and value in replacements:
                value = replacements[value][0]['segment_id']
            elif policy == 'offset' and isinstance(value, dict) and isinstance(value.get('segment_id'), str):
                pieces, offset = replacements.get(value['segment_id']), value.get('offset_s')
                if pieces and contracts.finite_number(offset) and offset >= 0:
                    try:
                        timeline = segment_output_timeline(pieces, fps=fps)
                    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                        continue  # Invalid creative timing remains the validator's job.
                    for piece in pieces:
                        timing = timeline[piece['segment_id']]
                        if timing['start_s'] <= offset < timing['end_s']:
                            value = dict(value, segment_id=piece['segment_id'], offset_s=offset - timing['start_s'])
                            break
            _set(plan, row, key, value, path, 'update_split_segment_reference')


def _split_captions(segment, pieces, window, *, fps=None):
    caption = segment.get('caption')
    if caption is None:
        return True
    speed = segment.get('speed', 1)
    hold = segment.get('freeze_tail_s', 0)
    if (not isinstance(caption, dict) or not all(contracts.finite_number(caption.get(k))
            for k in ('start_s', 'end_s')) or not contracts.finite_number(speed) or speed <= 0
            or not contracts.finite_number(hold) or hold < 0):
        return False
    duration = (segment['source_out_s'] - segment['source_in_s']) / speed + hold
    # Same tolerance as the caption validators: model decimals carry float dust.
    if not -1e-9 <= caption['start_s'] < caption['end_s'] <= duration + 1e-9:
        return False
    evidence_rows = caption.get('evidence', [])
    if not isinstance(evidence_rows, list) or any(not isinstance(e, dict) or not isinstance(
            e.get('event_indices'), list) for e in evidence_rows):
        return False
    # The caption validators bound each piece by its frame-quantized output
    # length; clamp to it so a split can never invent a caption rejection.
    quantized = type(fps) is int and 1 <= fps <= 60
    for piece in pieces:
        offset = (piece['source_in_s'] - segment['source_in_s']) / speed
        span = (piece['source_out_s'] - piece['source_in_s']) / speed
        duration = span + piece.get('freeze_tail_s', 0)
        if quantized:
            duration = min(duration, (round(span * fps) + round(piece.get('freeze_tail_s', 0) * fps)) / fps)
        start, end = max(caption['start_s'], offset), min(caption['end_s'], offset + duration)
        if start >= end or quantized and round(end * fps) <= round(start * fps):
            # Remainders shorter than one output frame carry no caption.
            piece.pop('caption', None)
            continue
        # Preserve the cited events; never choose different evidence for a caption.
        for evidence in evidence_rows:
            for index in evidence['event_indices']:
                events = (window.get('observation') or {}).get('events', [])
                if (type(index) is not int or not 0 <= index < len(events) or not (
                    events[index]['local_start_s'] < piece['source_out_s'] - window['source_start_s'] and
                    piece['source_in_s'] - window['source_start_s'] < events[index]['local_end_s'])):
                    return False
        piece['caption'] = dict(caption, start_s=start - offset, end_s=end - offset)
    return True


def _split_segments(plan, windows, *, catalog=None, fps=None, max_segments=None):
    lookup = windows if isinstance(windows, dict) else {w['window_id']: w for w in _rows(windows)}
    contract = contracts.plan_field_contract(windows=windows, catalog=catalog)['selected_range']
    choices = {r['window_id']: r for r in contract['windows']}
    reserved = {s.get('segment_id') for s in _rows(plan.get('segments')) if isinstance(s.get('segment_id'), str)}
    replacements, segments = {}, []
    for index, segment in enumerate(plan.get('segments', [])):
        if not isinstance(segment, dict):
            segments.append(segment)
            continue
        original_id, window_id = segment.get('segment_id'), segment.get('window_id')
        selection = choices.get(window_id) if isinstance(window_id, str) else None
        start, end = segment.get('source_in_s'), segment.get('source_out_s')
        if (not isinstance(original_id, str) or selection is None
                or not all(contracts.finite_number(v) for v in (start, end)) or start >= end
                or contracts.matching_plan_source_ranges(segment, selection)):
            segments.append(segment)
            continue
        # Each piece must pass both the original containment rule and the shared
        # confirmed-role / overlapping-event validator. Never widen the selection.
        # Work backwards over range boundaries so overlapping ranges cannot
        # make a greedy first cut hide another fully supported partition.
        paths = {end: []}
        boundaries = {start} | {r['source_out_s'] for r in selection['allowed_source_ranges']
                                if start < r['source_out_s'] < end}
        for cursor in sorted(boundaries, reverse=True):
            candidates = []
            for usable in selection['allowed_source_ranges']:
                if not usable['source_in_s'] - contract['tolerance_s'] <= cursor < usable['source_out_s']:
                    continue
                cut = end if end <= usable['source_out_s'] + contract['tolerance_s'] else usable['source_out_s']
                if cut not in paths:
                    continue
                piece = dict(segment, source_in_s=cursor, source_out_s=cut)
                if not contracts.matching_plan_source_ranges(piece, dict(selection, allowed_source_ranges=[usable])):
                    continue
                try:
                    contracts.validate_plan_segment_roles(piece, lookup[window_id], f'$.segments[{index}]')
                except ValueError:
                    continue
                candidates.append(piece)
            if candidates:
                piece = max(candidates, key=lambda s: s['source_out_s'])
                paths[cursor] = [piece] + paths[piece['source_out_s']]
        pieces = paths.get(start, [])
        if len(pieces) < 2:
            segments.append(segment)
            continue
        projected = len(segments) + len(pieces) + len(plan['segments']) - index - 1
        if isinstance(max_segments, int) and projected > max_segments:
            # A mechanical split must not push the plan past a render cap and
            # spend a repair on segment_count; leave the range to the model.
            segments.append(segment)
            continue
        for piece in pieces[:-1]:
            if 'freeze_tail_s' in piece:
                piece['freeze_tail_s'] = 0
        if not _split_captions(segment, pieces, lookup[window_id], fps=fps):
            segments.append(segment)
            continue
        suffix = 2
        for piece in pieces[1:]:
            while f'{original_id}_{suffix}' in reserved:
                suffix += 1
            piece['segment_id'] = f'{original_id}_{suffix}'
            reserved.add(piece['segment_id'])
        segments.extend(pieces)
        replacements[original_id] = pieces
        plan.setdefault('plan_normalisations', []).append(dict(path=f'$.segments[{index}]',
            before=deepcopy(segment), after=deepcopy(pieces), reason='split_contiguous_usable_ranges'))
    if replacements:
        plan['segments'] = segments
        _split_references(plan, replacements, fps=fps)


def _bindings(plan, windows, film_cast):
    used = {row.get('window_id') for row in _rows(plan.get('segments')) if isinstance(row.get('window_id'), str)}
    observed = _confirmed(windows)
    confirmed = {(w['window_id'], r['role_id']): r for w, r in observed}
    triples = contracts.plan_field_contract(windows=windows)['cast']['bindable_triples']
    draft, characters = _rows(plan.get('audio_draft')), _rows(plan.get('cast'))
    kept = []
    for index, character in enumerate(_rows(plan.get('cast'))):
        names = _names(character, film_cast)
        # Explicit appearances or the sole cast using this voice identify a
        # story role. Voice equality by itself never establishes an identity.
        unique_voice = character.get('voice') and sum(c.get('voice') == character['voice'] for c in characters) == 1
        speakers = [line['speaker'] for line in draft if isinstance(line.get('speaker'), dict) and (
            line.get('cast_id') == character.get('cast_id') or
            'cast_id' not in line and unique_voice and line.get('voice') == character['voice'])]
        declared = {(b.get('window_id'), b.get('role_id')) for b in
                    _rows(character.get('bindings')) + speakers
                    if isinstance(b.get('window_id'), str) and isinstance(b.get('role_id'), str)}
        identity_matches = []
        for window, role in observed:
            if not role.get('entity_id'):
                continue
            if _same_identity(dict(entity_id=character.get('entity_id'), names=names),
                              _observed_identity(window, role, film_cast)):
                identity_matches.append((window, role))
        matches = [pair for pair in identity_matches if pair[0]['window_id'] in used] or identity_matches
        # The cast validator requires one source entity per character. Ambiguity is
        # not authority to merge entities or select a different creative character.
        exact = [pair for pair in matches if pair[1]['entity_id'] == character.get('entity_id')]
        matches = exact or matches
        entities = {role['entity_id'] for _, role in matches}
        if not matches:
            candidates = [t for t in triples if (t['window_id'], t['role_id']) in declared]
            entities = {t['entity_id'] for t in candidates}
            # Every declared appearance must have confirmed evidence, including
            # lines with the same story role; otherwise the mapping is ambiguous.
            if declared and declared == {(t['window_id'], t['role_id']) for t in candidates} and len(entities) == 1:
                matches = [(w, r) for w, r in observed if r.get('entity_id') in entities]
            else:
                entities = set()
        bindings = []
        if len(entities) == 1:
            entity = matches[0][1]['entity_id']
            _set(plan, character, 'entity_id', entity, f'$.cast[{index}].entity_id', 'copy_confirmed_observed_entity')
            # One pure function of the confirmed entity, the EDL's windows and the
            # model's declared appearances, so a later pass reproduces the list
            # instead of drifting between the identity and appearance derivations.
            appearances = [(w, r) for w, r in observed if r.get('entity_id') == entity]
            in_scope = [pair for pair in appearances
                        if pair[0]['window_id'] in used or (pair[0]['window_id'], pair[1]['role_id']) in declared]
            bindings = [dict(window_id=w['window_id'], role_id=r['role_id']) for w, r in in_scope or appearances]
        _set(plan, character, 'bindings', bindings, f'$.cast[{index}].bindings', 'derive_confirmed_bindings_from_used_windows'
             if all(b['window_id'] in used for b in bindings) else 'derive_unique_confirmed_bindings')
        if not bindings and not any(line.get('cast_id') == character.get('cast_id') for line in draft):
            plan.setdefault('plan_normalisations', []).append(dict(path=f'$.cast[{index}]',
                before=deepcopy(character), after=None, reason='drop_unreferenced_unbound_cast'))
        else:
            kept.append(character)

    # A focus is one creative character, not whichever cast happens to be the
    # sole visible role in a window. Resolve it before collecting occurrences.
    focus = plan.get('focus_role_id')
    characters = _rows(plan.get('cast'))
    # A dropped unused cast can still name the intended visual focus. Keep that
    # identity declaration on subsequent passes rather than drifting to a survivor.
    removed = [c['before'] for c in _rows(plan.get('plan_normalisations'))
               if c.get('reason') == 'drop_unreferenced_unbound_cast' and isinstance(c.get('before'), dict)
               and not any(row.get('cast_id') == c['before'].get('cast_id') for row in characters)]
    named = [c for c in characters + removed if isinstance(focus, str) and (
        focus == c.get('entity_id') or normalise(canonical_name(focus, film_cast)) in _names(c, film_cast))]
    direct = dict(entity_id=focus, names=_names(dict(name=focus), film_cast))
    seeds = []
    for binding in _rows(plan.get('focus_role_bindings')):
        key = (binding.get('window_id'), binding.get('role_id'))
        if all(isinstance(v, str) for v in key) and key in confirmed:
            seeds.extend((w, r) for w, r in observed if (w['window_id'], r['role_id']) == key)
    target, local_only = None, False
    if len(named) == 1:
        target = dict(entity_id=named[0].get('entity_id'), names=_names(named[0], film_cast))
    elif not named and any(_same_identity(direct, _observed_identity(w, r, film_cast)) for w, r in observed):
        target = direct
    elif not named and seeds:
        identities = [_observed_identity(w, r, film_cast) for w, r in seeds]
        identities = [i for i in identities if i['entity_id'] or i['names']]
        if identities and all(_same_identity(identities[0], other) for other in identities):
            target = identities[0]
        # Distinct source-local entities do not disprove a model's existing
        # visual focus declaration. Keep those confirmed local bindings, but
        # never extrapolate them to additional windows without a shared identity.
        local_only = True
    elif not named and len(characters) == 1:
        target = dict(entity_id=characters[0].get('entity_id'), names=_names(characters[0], film_cast))
    bindings = []
    for window_id in sorted(used) if focus is not None else []:
        choices = [r for w, r in observed if w['window_id'] == window_id and (
            _same_identity(target, _observed_identity(w, r, film_cast)) if target else
            local_only and (w, r) in seeds)]
        if len(choices) == 1 and isinstance(choices[0].get('identity_evidence'), str) and choices[0]['identity_evidence'].strip():
            # validate_fine requires observation.identity_evidence itself. State
            # cannot replace missing evidence without weakening that contract.
            bindings.append(dict(window_id=window_id, role_id=choices[0]['role_id'],
                                 identity_evidence=choices[0]['identity_evidence']))
    _set(plan, plan, 'focus_role_bindings', bindings, '$.focus_role_bindings', 'confirmed_unique_focus_with_observed_evidence')
    if len(kept) != len(characters):
        plan['cast'] = kept


def speaker_segments(plan, windows=(), *, segments=None, cast=None):
    """Visibility uses the same local appearance/entity rules as the validator."""
    segments = _rows(plan.get('segments')) if segments is None else segments
    characters = _rows(plan.get('cast')) if cast is None else _rows(cast)
    entities = {(b.get('window_id'), b.get('role_id')): c.get('entity_id')
                for c in characters for b in _rows(c.get('bindings'))}
    entities.update({(w['window_id'], r['role_id']): r['entity_id'] for w, r in _confirmed(windows) if r.get('entity_id')})
    result = {}
    for line in _rows(plan.get('audio_draft')):
        speaker = line.get('speaker')
        if speaker is None:
            index = _rows(plan.get('audio_draft')).index(line)
            speaker = next((change.get('before') for change in reversed(_rows(plan.get('plan_normalisations')))
                if change.get('path') == f'$.audio_draft[{index}].speaker'), None)
        if not isinstance(speaker, dict):
            continue
        appearance = (speaker.get('window_id'), speaker.get('role_id'))
        if not all(isinstance(v, str) for v in appearance):
            continue
        identity = entities.get(appearance)
        result[line.get('id')] = [s.get('segment_id', s.get('id')) for s in segments if (
            s.get('window_id') == appearance[0] and appearance[1] in s.get('role_ids', []) or
            any(e.get('window_id') == appearance[0] and appearance[1] in e.get('role_ids', [])
                for e in _rows(s.get('visual_events'))) or identity is not None and (
            identity in s.get('entity_ids', []) or any(entities.get((s.get('window_id'), r)) == identity
                for r in s.get('role_ids', []))))]
    return result


def fine_visible_segments(plan, parent, *, fps=None):
    from .story_context import _map_events
    timeline = segment_output_timeline(plan['segments'], fps=fps)
    events = [event for s in parent['segments'] for event in s.get('visual_events', [])]
    result = []
    for s in plan['segments']:
        timing = timeline[s['segment_id']]
        mapped = _map_events(events, s, dict(output_in_s=timing['start_s'], output_out_s=timing['end_s']))
        result.append(dict(segment_id=s['segment_id'], visual_events=mapped,
                           entity_ids=list({entity for e in mapped for entity in e.get('entity_ids', [])})))
    return result


def normalise_storyboard_slots(plan, storyboard=None):
    """Repair redundant references without choosing footage or a carrying slot."""
    if 'plan_normalisations' in plan and not isinstance(plan['plan_normalisations'], list):
        raise contracts.numeric_error('plan:normalisations_array_required', '$.plan_normalisations',
                                      plan['plan_normalisations'], 'array')
    slots, segments = _rows(plan.get('slots')), _rows(plan.get('segments'))
    if not slots or not segments or len(slots) != len(plan['slots']) or len(segments) != len(plan['segments']):
        return
    if (all(isinstance(s.get('slot_id'), str) and s['slot_id'].strip() for s in slots)
            and len({s['slot_id'] for s in slots}) == len(slots)
            and all(isinstance(s.get('segment_id'), str) and s['segment_id'].strip() for s in segments)
            and len({s['segment_id'] for s in segments}) == len(segments)):
        expected = {s['slot_id']: [] for s in slots}
        segment_ids = {s['segment_id'] for s in segments}
        valid = all(isinstance(s.get('slot_id'), str) and s['slot_id'] in expected for s in segments)
        valid = valid and all(isinstance(s.get('segment_ids'), list) and all(
            isinstance(ref, str) and ref in segment_ids for ref in s['segment_ids']) for s in slots)
        if valid:
            for segment in segments:
                expected[segment['slot_id']].append(segment['segment_id'])
            if all(expected.values()) and any(
                    set(s['segment_ids']) != set(expected[s['slot_id']]) or
                    len(s['segment_ids']) != len(expected[s['slot_id']]) for s in slots):
                for index, slot in enumerate(slots):
                    _set(plan, slot, 'segment_ids', expected[slot['slot_id']],
                         f'$.slots[{index}].segment_ids', 'rebuild_slot_segment_ids_from_segment_slot_id')

    if not isinstance(storyboard, dict) or not isinstance(plan.get('storyboard_merges'), list):
        return
    cells = {c['id']: c for c in _rows(storyboard.get('storyboard'))
             if isinstance(c.get('id'), str) and contracts.finite_number(c.get('start_s'))}
    kept = []
    for merge_index, merge in enumerate(plan['storyboard_merges']):
        if not isinstance(merge, dict):
            kept.append(merge)
            continue
        cell_id, into = merge.get('cell_id'), merge.get('into_slot_id')
        targets = [(i, s) for i, s in enumerate(slots) if s.get('slot_id') == into]
        if (not isinstance(cell_id, str) or cell_id not in cells or cells[cell_id].get('core') is not True
                or not isinstance(into, str) or len(targets) != 1
                or not isinstance(merge.get('reason'), str) or not merge['reason'].strip()):
            kept.append(merge)
            continue
        index, slot = targets[0]
        ids_ = slot.get('storyboard_cell_ids', [])
        refs = slot.get('segment_ids')
        if (not isinstance(refs, list) or not any(s.get('segment_id') in refs for s in segments)
                or not isinstance(ids_, list) or any(not isinstance(c, str) or c not in cells for c in ids_)
                or len(ids_) != len(set(ids_))):
            kept.append(merge)
            continue
        carried = sorted(ids_ + ([] if cell_id in ids_ else [cell_id]), key=lambda c: cells[c]['start_s'])
        count = len(plan.get('plan_normalisations', []))
        _set(plan, slot, 'storyboard_cell_ids', carried,
             f'$.slots[{index}].storyboard_cell_ids', 'core_cell_carried_by_slot')
        if len(plan.get('plan_normalisations', [])) > count:
            plan['plan_normalisations'][-1]['model_reason'] = merge['reason']
        else:
            plan.setdefault('plan_normalisations', []).append(dict(
                path=f'$.storyboard_merges[{merge_index}]', before=deepcopy(merge), after=None,
                reason='core_cell_carried_by_slot', model_reason=merge['reason']))
    _set(plan, plan, 'storyboard_merges', kept, '$.storyboard_merges', 'remove_carried_core_cell_merges')


def normalise_plan(plan, windows=(), *, film_cast=None, speech_mode=None, fps=None,
                   received_tts=(), parent=None, allow_narration=None, allow_identity_anchor=True,
                   storyboard_beats=(), storyboard=None, catalog=None, max_segments=None):
    if not isinstance(plan, dict):
        return plan
    if 'plan_normalisations' in plan and not isinstance(plan['plan_normalisations'], list):
        raise contracts.numeric_error('plan:normalisations_array_required', '$.plan_normalisations',
                                      plan['plan_normalisations'], 'array')
    if storyboard_beats:
        beat_cells = {b.get('slot_id', b.get('id')): b.get('cell_ids',
            b.get('reference_evidence', {}).get('cell_ids', [])) for b in storyboard_beats}
        for index, slot in enumerate(_rows(plan.get('slots'))):
            cells = beat_cells.get(slot.get('slot_id')) if isinstance(slot.get('slot_id'), str) else None
            if 'storyboard_cell_ids' not in slot and cells:
                _set(plan, slot, 'storyboard_cell_ids', deepcopy(cells),
                     f'$.slots[{index}].storyboard_cell_ids', 'storyboard_beat_cell_ids')
    if storyboard_beats or storyboard is not None:
        normalise_storyboard_slots(plan, storyboard)
    film_cast = film_cast or dict(characters=[])
    if windows:
        _segment_sources(plan, windows)
        if isinstance(plan.get('segments'), list):
            _split_segments(plan, windows, catalog=catalog, fps=fps, max_segments=max_segments)
        _bindings(plan, windows, film_cast)
    segments = _rows(plan.get('segments'))
    if not segments:
        return plan
    # Malformed creative fields belong to the existing validators, not repair here.
    try:
        timeline = segment_output_timeline(segments, fps=fps)
        visible = fine_visible_segments(plan, parent, fps=fps) if parent else segments
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return plan
    draft = _rows(plan.get('audio_draft'))
    inherited = {line.get('id'): line for line in _rows((parent or {}).get('audio_draft'))}
    lookup = deepcopy(plan)
    for line in _rows(lookup.get('audio_draft')):
        original = inherited.get(line.get('id'), {})
        if line.get('mode') == original.get('mode') and line.get('voice') == original.get('voice'):
            for key in ('speaker', 'cast_id'):
                if key not in line and key in original:
                    line[key] = original[key]
    choices = speaker_segments(lookup, windows, segments=visible, cast=(parent or {}).get('cast', plan.get('cast')))
    order = {s['segment_id']: i for i, s in enumerate(segments)}
    for index, line in enumerate(draft):
        if line.get('mode') != 'dialogue':
            continue
        available = choices.get(line.get('id'), [])
        selected = line.get('segment_ids')
        if not isinstance(selected, list) or not selected or any(not isinstance(s, str) or s not in order for s in selected):
            continue
        anchor = line.get('identity_anchor_segment_id')
        if any(s in available for s in selected) or (anchor in available and order[anchor] <= order[selected[0]]):
            continue
        if not available:  # No confirmed alternative; never manufacture visibility.
            continue
        path = f'$.audio_draft[{index}]'
        candidates = sorted(available, key=lambda s: (min(abs(order[s] - order[t]) for t in selected), order[s]))
        moved = False
        for segment_id in candidates:
            trial = deepcopy(line)
            trial['segment_ids'] = [segment_id]
            for key in ('start_s', 'end_s', 'start_anchor', 'identity_anchor_segment_id'):
                trial.pop(key, None)
            trial_draft = [trial if other is line else other for other in draft]
            try:
                details = draft_line_window_details(trial_draft, segments, received_tts=received_tts, fps=fps)
            except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                break
            if not details or any(d['window_s'] < d['min_window_s'] - 1e-6 for d in details
                                  if d['id'] == line.get('id') or d['segment_ids'] == [segment_id]):
                continue
            _set(plan, line, 'segment_ids', [segment_id], path + '.segment_ids', 'nearest_visible_segment_fits_line_window')
            for key in ('start_s', 'end_s', 'start_anchor', 'identity_anchor_segment_id'):
                _set(plan, line, key, None, path + '.' + key, 'rederive_timing_after_segment_move', remove=True)
            if 'audio_relation' in line:
                _set(plan, line, 'audio_relation', [dict(segment_id=segment_id, relation='on_screen')],
                     path + '.audio_relation', 'relation_follows_visible_segment')
            moved = True
            break
        if moved:
            continue
        earlier = [s for s in candidates if order[s] <= order[selected[0]]]
        if allow_identity_anchor and earlier:
            _set(plan, line, 'identity_anchor_segment_id', earlier[0], path + '.identity_anchor_segment_id', 'nearest_established_visible_speaker')
            if 'audio_relation' in line:
                _set(plan, line, 'audio_relation', [dict(segment_id=s, relation='offscreen') for s in selected],
                     path + '.audio_relation', 'anchored_offscreen_dialogue')
        elif (allow_narration if allow_narration is not None else (
                speech_mode in {'mixed', 'narration'} or speech_mode == 'dialogue' and
                sum(other.get('mode') == 'dialogue' for other in draft) > 1)):
            _set(plan, line, 'mode', 'narration', path + '.mode', 'no_fitting_visible_window_or_earlier_anchor')
            _set(plan, line, 'speaker', None, path + '.speaker', 'narration_has_no_speaker')
            _set(plan, line, 'identity_anchor_segment_id', None, path + '.identity_anchor_segment_id', 'narration_needs_no_identity_anchor', remove=True)
            if 'audio_relation' in line:
                _set(plan, line, 'audio_relation', [dict(segment_id=s, relation='narration') for s in selected], path + '.audio_relation', 'narration_relation')
    return plan


def add_valid_choices(error, plan, windows=(), *, parent=None, fps=None, film_cast=None):
    """Enrich feedback without changing a validator's verdict or rule."""
    try:
        segments = fine_visible_segments(plan, parent, fps=fps) if parent else None
        choices = dict(bindable_triples=contracts.plan_field_contract(windows=windows)['cast'].get('bindable_triples', []),
            speaker_visible_segments=speaker_segments(plan, windows, segments=segments, cast=(parent or {}).get('cast', plan.get('cast'))))
        observed = _confirmed(windows)
        window_rows = list(windows.values()) if isinstance(windows, dict) else _rows(windows)
        choices['valid_window_ids'] = sorted(w['window_id'] for w in window_rows)
        choices['unknown_segment_window_ids'] = sorted({s['window_id'] for s in _rows(plan.get('segments'))
            if isinstance(s.get('window_id'), str) and s['window_id'] not in choices['valid_window_ids']})
        choices['bindable_rows'] = []
        choices['focus_binding_blockers'] = []
        for window, role in observed:
            identity = next((i for i in _rows(window.get('qfs_identity'))
                             if role.get('entity_id') and i.get('entity_id') == role['entity_id']), role)
            name = identity.get('canonical_name', identity.get('name'))
            if isinstance(name, str):
                name = canonical_name(name, film_cast or dict(characters=[]))
            if not isinstance(role.get('identity_evidence'), str) or not role['identity_evidence'].strip():
                choices['focus_binding_blockers'].append(dict(window_id=window['window_id'],
                    role_id=role['role_id'], reason='observation_identity_evidence_required'))
            else:
                choices['bindable_rows'].append(dict(window_id=window['window_id'], role_id=role['role_id'],
                                                    entity_id=role.get('entity_id'), name=name))
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return
    violation = getattr(error, 'violation', None)
    if isinstance(violation, dict):
        violation['valid_choices'] = choices
        expected = violation.get('expected')
        if isinstance(expected, dict):
            violation['expected'] = {**expected, **choices}
    else:
        error.violation = dict(path='$', value=None, expected=choices, valid_choices=choices, repair_hint='从已确认身份和可见片段中重新选择，不补造观察。')


def source_ranges(segment, parent=None):
    """Resolve selected parent seconds to original source seconds, without guessing.

    New contexts carry source_ranges (including frame-quantized motion and holds).
    Old contexts can recover complete events only; clipped events lack an exact
    original mapping and must not be stretched into invented source evidence.
    """
    if parent is None:
        if not all(k in segment for k in ('source_id', 'source_in_s', 'source_out_s')):
            return []  # Legacy context-only callers may have no source lineage.
        return [dict(source_id=segment['source_id'], source_in_s=segment['source_in_s'],
                     source_out_s=segment['source_out_s'], start_s=segment['source_in_s'], end_s=segment['source_out_s'])]
    result = []
    for row in parent.get('segments', []):
        mappings = row.get('source_ranges')
        if mappings is None:
            mappings = [e for e in row.get('visual_events', []) if e.get('complete_event') is True
                        and all(k in e for k in ('source_id', 'source_in_s', 'source_out_s'))]
        for mapping in mappings:
            start, end = max(segment['source_in_s'], mapping['start_s']), min(segment['source_out_s'], mapping['end_s'])
            if start >= end:
                continue
            scale = (mapping['source_out_s'] - mapping['source_in_s']) / (mapping['end_s'] - mapping['start_s'])
            result.append(dict(source_id=mapping['source_id'], start_s=start, end_s=end,
                source_in_s=mapping['source_in_s'] + (start - mapping['start_s']) * scale,
                source_out_s=mapping['source_in_s'] + (end - mapping['start_s']) * scale))
    return result


def _merge(ranges):
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def _subtract(start, end, occupied):
    cursor, result = start, []
    for low, high in _merge(occupied):
        if low > cursor:
            result.append([cursor, min(low, end)])
        cursor = max(cursor, high)
        if cursor >= end:
            break
    if cursor < end:
        result.append([cursor, end])
    return result


def allowed_range_table(windows=(), catalog=None, *, parent=None):
    if parent is not None:
        return [dict(source_id='chain_parent', window_id='full_parent_observed',
            source_in_s=row['start_s'], source_out_s=row['end_s']) for row in parent.get('segments', [])]
    sources = {s['source_id']: s for s in (catalog or {}).get('sources', [])}
    observed = windows.values() if isinstance(windows, dict) else windows
    return [dict(r, source_id=w['source_id'], window_id=w['window_id'])
            for w in observed if w.get('status') == 'watched'
            for r in contracts.plan_source_range_contract(w, sources.get(w.get('source_id')))['allowed_source_ranges']]


def validate_repeated_footage(plan, windows=(), catalog=None, *, parent=None):
    segments = _rows(plan.get('segments'))
    selected = [source_ranges(s, parent) + (source_ranges(s) if parent is not None else [])
                for s in segments]
    conflicts = []
    for i, ranges in enumerate(selected):
        for j in range(i):
            sources = {r['source_id'] for r in ranges}
            for source in sorted(sources):
                left = _merge([[r['source_in_s'], r['source_out_s']] for r in ranges if r['source_id'] == source])
                right = _merge([[r['source_in_s'], r['source_out_s']] for r in selected[j] if r['source_id'] == source])
                overlap = sum(max(0, min(b, d) - max(a, c)) for a, b in left for c, d in right)
                if overlap > MAX_SOURCE_OVERLAP_S + 1e-9:
                    conflicts.append(dict(segment_ids=[segments[j]['segment_id'], segments[i]['segment_id']],
                                          source_id=source, overlap_s=overlap))
    if not conflicts:
        return
    table = allowed_range_table(windows, catalog, parent=parent)
    occupied = {}
    for ranges in selected:
        for r in ranges:
            occupied.setdefault(r['source_id'], []).append([r['source_in_s'], r['source_out_s']])
    free = []
    for row in table:
        # Free choices are in the allowed table's input domain. For a parent
        # render, subtract equivalent original footage at every parent occurrence.
        if parent is None:
            cuts = occupied.get(row['source_id'], [])
        else:
            cuts = []
            for old in parent.get('segments', []):
                for m in old.get('source_ranges', old.get('visual_events', [])):
                    if not all(k in m for k in ('source_id', 'source_in_s', 'source_out_s', 'start_s', 'end_s')):
                        continue
                    span = m['source_out_s'] - m['source_in_s']
                    if span <= 0:
                        continue
                    for a, b in occupied.get(m['source_id'], []):
                        low, high = max(a, m['source_in_s']), min(b, m['source_out_s'])
                        if low < high:
                            scale = (m['end_s'] - m['start_s']) / span
                            cuts.append([m['start_s'] + (low - m['source_in_s']) * scale,
                                         m['start_s'] + (high - m['source_in_s']) * scale])
            cuts += [[s['source_in_s'], s['source_out_s']] for s in segments]
        for start, end in _subtract(row['source_in_s'], row['source_out_s'], cuts):
            free.append(dict(row, source_in_s=start, source_out_s=end))
    expected = dict(max_source_overlap_s=MAX_SOURCE_OVERLAP_S, conflicts=conflicts,
                    allowed_range_table=table, free_alternative_ranges=free)
    error = contracts.numeric_error('plan:repeated_source_footage', '$.segments', conflicts, expected)
    error.violation['repair_hint'] = '同源素材重叠超过0.5秒；删除冲突段或从free_alternative_ranges重选完整合法范围，不扩展观察。'
    raise error
