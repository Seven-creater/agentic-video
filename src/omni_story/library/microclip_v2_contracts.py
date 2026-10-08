"""Mechanical contracts for evidence-led local refinement, never plot answers.

Blocked/limited semantic judgments are valid replies. The execution layer must
check those judgments before rendering; a validator is not an editing review.
"""
import math


CLAIM_KEYS = ('intended_takeaway', 'entry_state', 'exit_state',
              'link_to_previous', 'link_to_next')
EXPOSURE_TOLERANCE_S = .05


def _require(ok, message):
    if not ok:
        raise ValueError('microclip_v2:' + message)


def _text(value, name):
    _require(isinstance(value, str) and bool(value.strip()), name)


def _strings(value, name):
    _require(isinstance(value, list) and all(isinstance(s, str) for s in value), name)


def _number(value, name):
    _require(type(value) in (int, float) and math.isfinite(value), name)


def _rows(value, key, minimum, maximum):
    _require(isinstance(value, dict), key + '_object')
    result = value.get(key)
    _require(isinstance(result, list) and minimum <= len(result) <= maximum
             and all(isinstance(r, dict) for r in result), key + '_rows')
    return result


def _ids(value, allowed, name, *, empty=False):
    _require(isinstance(value, list) and (empty or bool(value))
             and all(isinstance(i, str) and i in allowed for i in value)
             and len(value) == len(set(value)), name)
    return set(value)


def _exact(rows, field, expected, name):
    actual = [r.get(field) for r in rows]
    _require(all(isinstance(i, str) for i in actual) and len(actual) == len(set(actual))
             and set(actual) == set(expected), name)


def _semantic(value, statuses):
    _require(isinstance(value, dict), 'semantic_object')
    _require(value.get('status') in statuses, 'status')
    _strings(value.get('blocking_questions'), 'blocking_questions')
    _strings(value.get('limitations'), 'limitations')


def _range(row, catalog, slot=None):
    a, b = row.get('start_frame_id'), row.get('end_frame_id')
    _require(isinstance(a, str) and isinstance(b, str) and a in catalog and b in catalog,
             'observed_range_ids')
    first, last = catalog[a], catalog[b]
    start, end = first['source_time_s'], last.get('frame_end_s')
    _number(start, 'source_time_s')
    _number(end, 'real_frame_end_s')
    _require(start <= last['source_time_s'] < end, 'ordered_frame_range')
    _require(first['source_sha256'] == last['source_sha256'], 'same_range_source')
    if slot:
        _require(slot['start_s'] <= start < end <= slot['end_s'] + .000001, 'range_in_slot')
    return start, end


def validate_frames(value, shown):
    rows = _rows(value, 'frames', 1, len(shown))
    expected = {f['frame_id'] for f in shown}
    _exact(rows, 'frame_id', expected, 'all_shown_frames_once')
    for row in rows:
        _text(row.get('visible_action_or_state'), 'visible_action_or_state')
        _require(isinstance(row.get('visible_text'), str), 'visible_text')
    _text(value.get('visible_event'), 'visible_event')
    _strings(value.get('missing_information'), 'missing_information')
    _strings(value.get('limitations'), 'limitations')
    return value


def validate_motion(value, duration):
    _require(isinstance(value, dict), 'motion_object')
    _text(value.get('visible_meaning'), 'visible_meaning')
    _require(value.get('observation_status') in {'complete', 'limited'}, 'observation_status')
    _strings(value.get('limitations'), 'limitations')
    events = _rows(value, 'events', 1, 100)
    for event in events:
        a, b = event.get('start_s'), event.get('end_s')
        _number(a, 'event_start_s')
        _number(b, 'event_end_s')
        _require(0 <= a < b <= duration + .05, 'local_event_time')
        _text(event.get('visible_content'), 'visible_content')
        _require(isinstance(event.get('text_evidence'), str), 'text_evidence')
    return value


def validate_intent(value, slot, catalog):
    _semantic(value, {'ready', 'blocked'})
    claims = {key: slot[key] for key in CLAIM_KEYS
              if isinstance(slot.get(key), str) and slot[key].strip()}
    checks = _rows(value, 'original_claim_checks', len(claims), len(claims))
    _exact(checks, 'claim_id', claims, 'all_original_claims_once')
    for check in checks:
        _require(check.get('original_text') == claims[check['claim_id']], 'original_claim_text_unchanged')
        _require(check.get('verdict') in {'supported', 'partial', 'unsupported'}, 'claim_verdict')
        _text(check.get('reason'), 'claim_reason')
        _ids(check.get('evidence_frame_ids'), catalog, 'claim_observed_evidence',
             empty=check['verdict'] == 'unsupported')
    obligations = _rows(value, 'obligations', 1, 8)
    ids = [row.get('obligation_id') for row in obligations]
    _require(all(isinstance(i, str) and i.strip() for i in ids)
             and len(ids) == len(set(ids)), 'unique_obligation_ids')
    claim_coverage = set()
    for obligation in obligations:
        _text(obligation.get('description'), 'obligation_description')
        _require(obligation.get('support') in {'visual', 'text', 'mixed'}, 'obligation_support')
        _ids(obligation.get('evidence_frame_ids'), catalog, 'obligation_observed_evidence')
        claim_coverage |= _ids(obligation.get('original_claim_ids'), claims,
                               'obligation_original_claim_ids', empty=True)
    _require(claim_coverage == set(claims), 'all_original_claims_need_obligations')
    regions = _rows(value, 'search_regions', 1, 6)
    _require([r.get('region_id') for r in regions] == [f'r{i}' for i in range(len(regions))],
             'sequential_region_ids')
    covered = set()
    for region in regions:
        _range(region, catalog, slot)
        _text(region.get('question'), 'specific_search_question')
        covered |= _ids(region.get('obligation_ids'), ids, 'region_obligation_ids')
    _require(covered == set(ids), 'all_obligations_need_search_regions')
    return value


def validate_anchors(value, catalog, intent):
    _semantic(value, {'ready', 'blocked'})
    _strings(value.get('resolved_conflicts'), 'resolved_conflicts')
    events = _rows(value, 'events', 1, 6)
    _require([e.get('event_id') for e in events] == [f'e{i}' for i in range(len(events))],
             'sequential_event_ids')
    wanted = {row['obligation_id'] for row in intent['obligations']}
    covered, previous = set(), None
    for event in events:
        start, end = _range(event, catalog)
        _require(previous is None or start >= previous - .000001, 'ordered_nonoverlapping_events')
        previous = end
        _text(event.get('reason'), 'event_reason')
        covered |= _ids(event.get('obligation_ids'), wanted, 'event_obligation_ids')
    _require(covered == wanted, 'all_obligations_need_event_anchors')
    return value


def validate_edge(value, shown, anchor_id):
    validate_frames(value, shown)
    _semantic(value, {'confirmed', 'blocked'})
    _require(value.get('anchor_frame_id') == anchor_id
             and anchor_id in {f['frame_id'] for f in shown}, 'anchor_present_in_shown_frames')
    confirmed = value.get('confirmed_frame_id')
    _require((isinstance(confirmed, str) and confirmed in {f['frame_id'] for f in shown})
             or (confirmed is None and value['status'] == 'blocked'), 'confirmed_observed_boundary')
    _text(value.get('reason'), 'edge_reason')
    return value


def validate_plan(source, slot, value, frames, boundary_ids, intent):
    _require(isinstance(value, dict), 'plan_object')
    _text(value.get('preserved_visible_meaning'), 'preserved_visible_meaning')
    _strings(value.get('omitted_content'), 'omitted_content')
    _strings(value.get('limitations'), 'limitations')
    shots = _rows(value, 'shots', 1, 12)
    wanted = {row['obligation_id'] for row in intent['obligations']}
    segments, shot_obligations, previous = [], [], slot['start_s']
    for shot in shots:
        start, end = _range(shot, frames, slot)
        _require(start >= previous - .000001, 'ordered_nonoverlapping_shots')
        previous = end
        a, b = shot['start_frame_id'], shot['end_frame_id']
        _require(a in boundary_ids and b in boundary_ids, 'neighbor_confirmed_boundaries_required')
        _require(frames[a]['source_sha256'] == source['sha256'], 'frame_source_sha')
        speed, hold = shot.get('speed'), shot.get('hold_s')
        _number(speed, 'speed')
        _number(hold, 'hold_s')
        _require(.5 <= speed <= 2 and 0 <= hold <= 3, 'retiming_limits')
        _require(round((end - start) / speed * 30) >= 1, 'at_least_one_output_motion_frame')
        _text(shot.get('reason'), 'shot_reason')
        _text(shot.get('visible_change'), 'visible_change')
        shot_obligations.append(_ids(shot.get('obligation_ids'), wanted, 'shot_obligation_ids'))
        segments.append({'source_id': source['source_id'], 'slot_id': slot['slot_id'],
                         'window_id': slot['slot_id'], 'source_in_s': start, 'source_out_s': end,
                         'speed': speed, 'freeze_tail_s': hold, 'look': 'none', 'framing': 'fit'})
    coverage = _rows(value, 'obligation_coverage', len(wanted), len(wanted))
    _exact(coverage, 'obligation_id', wanted, 'all_obligations_need_plan_coverage')
    for row in coverage:
        indices = row.get('shot_indices')
        _require(isinstance(indices, list) and bool(indices)
                 and all(type(i) is int and 0 <= i < len(shots) for i in indices)
                 and len(indices) == len(set(indices)), 'coverage_shot_indices')
        _require(set(indices) == {i for i, ids in enumerate(shot_obligations) if row['obligation_id'] in ids},
                 'coverage_matches_shot_obligations')
        evidence = _ids(row.get('source_evidence_frame_ids'), frames, 'coverage_observed_evidence')
        used, exposure = set(), 0.0
        for index in indices:
            segment, shot = segments[index], shots[index]
            inside = {ident for ident in evidence if
                      frames[ident]['source_sha256'] == source['sha256']
                      and segment['source_in_s'] <= frames[ident]['source_time_s']
                      and frames[ident].get('frame_end_s') is not None
                      and frames[ident]['frame_end_s'] <= segment['source_out_s'] + .000001}
            _require(bool(inside), 'each_assigned_shot_needs_source_evidence')
            used |= inside
            # This is allocated screen time, not proof of continuous visibility
            # or a measured human reading threshold. Separate reviews test both.
            exposure += (segment['source_out_s'] - segment['source_in_s']) / segment['speed']
            if shot['end_frame_id'] in inside:
                exposure += segment['freeze_tail_s']
        _require(used == evidence, 'evidence_inside_assigned_shots_and_source')
        estimate, needed = row.get('exposure_s'), row.get('necessary_exposure_s')
        _number(estimate, 'exposure_s')
        _number(needed, 'necessary_exposure_s')
        _require(abs(estimate - exposure) <= EXPOSURE_TOLERANCE_S, 'exposure_matches_executable_allocation')
        _require(0 < needed <= exposure + EXPOSURE_TOLERANCE_S, 'necessary_exposure_fits_allocation')
        _text(row.get('reason'), 'coverage_reason')
    return {'fps': 30, 'width': 720, 'height': 1280, 'audio_mode': 'source', 'segments': segments}


def validate_source_check(value, intent):
    _semantic(value, {'ready', 'blocked'})
    wanted = {row['obligation_id'] for row in intent['obligations']}
    checks = _rows(value, 'obligation_checks', len(wanted), len(wanted))
    _exact(checks, 'obligation_id', wanted, 'all_source_obligations_once')
    for check in checks:
        _require(check.get('verdict') in {'supported', 'partial', 'unsupported'}, 'source_verdict')
        _text(check.get('reason'), 'source_check_reason')
    return value


def validate_blind_page(value, shown):
    validate_frames(value, shown)
    _require(value.get('observation_status') in {'complete', 'limited'}, 'observation_status')
    return value


def validate_review(value, intent, duration):
    _require(isinstance(value, dict), 'review_object')
    for key in ('status', 'key_moment_selection', 'economy', 'readability'):
        _require(value.get(key) in {'pass', 'partial', 'fail'}, key)
    _text(value.get('reason'), 'review_reason')
    _strings(value.get('limitations'), 'limitations')
    wanted = {row['obligation_id'] for row in intent['obligations']}
    checks = _rows(value, 'obligation_checks', len(wanted), len(wanted))
    _exact(checks, 'obligation_id', wanted, 'all_review_obligations_once')
    for check in checks:
        _require(check.get('verdict') in {'pass', 'partial', 'fail'}, 'review_obligation_verdict')
        _text(check.get('reason'), 'review_obligation_reason')
        a, b = check.get('output_start_s'), check.get('output_end_s')
        if a is None and b is None:
            _require(check['verdict'] == 'fail', 'missing_output_evidence_only_for_fail')
        else:
            _number(a, 'output_start_s')
            _number(b, 'output_end_s')
            _require(0 <= a < b <= duration + .05, 'actual_output_evidence_time')
    return value
