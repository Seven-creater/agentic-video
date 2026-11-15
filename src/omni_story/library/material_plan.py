"""Ground a director's choices in observed identities and finite source frames."""
from __future__ import annotations

import math
from .joint_dialogue import FPS, CoverageError, _fits, schedule

POLICY = 'self_directed_material_v1'


def validate_cast(cast, roles, actors):
    if not isinstance(cast, dict) or set(cast) != set(roles):
        raise ValueError('cast must bind every story role exactly once')
    if any(a not in actors for a in cast.values()):
        raise ValueError('cast contains an unobserved actor')
    if any(actors[a].get('entity_kind', 'individual') != 'individual' for a in cast.values()):
        raise ValueError('a story role cannot be cast to an indistinguishable group')
    if len(set(cast.values())) != len(cast):
        raise ValueError('different story roles cannot share an actor')
    return cast


def validate_use(use, shot, cast, role):
    """A story role is never inferred from a window-local A/B label or prose."""
    mode = use.get('mode')
    if mode not in {'speaker', 'listener', 'cutaway', 'visual'}:
        raise ValueError('unknown shot use mode')
    visible, speaking = set(shot['visible']), set(shot['speaking'])
    actor = cast.get(role)
    if mode == 'speaker' and (speaking != {actor} or actor not in visible):
        raise ValueError('speaker actor lacks observed speech evidence')
    if mode in {'listener', 'cutaway', 'visual'} and speaking:
        raise ValueError('silent insert contains a different speaking actor')
    if mode == 'listener' and (actor in visible or not visible.intersection(set(cast.values())-{actor})):
        raise ValueError('listener must show a different bound actor')
    # A clear closed mouth is not a speaking performance. A first-person voice
    # over one's own hands/back may still be a cutaway if that is observed.
    if mode == 'cutaway' and actor in shot.get('clear_face', []):
        raise ValueError('cutaway shows the voice actor clearly not speaking')
    events = {e['event_id']: e for e in shot.get('events', [])}
    claims = use.get('claims')
    if not isinstance(claims, list):
        raise ValueError('every use must explicitly declare its depicted role/action claims')
    for claim in claims:
        event = events.get(claim.get('event_id'))
        if event is None or claim.get('part') not in {'subject', 'addressee'} or claim.get('role') not in cast:
            raise ValueError('depiction references an unknown event, participant or story role')
        if cast[claim['role']] not in event.get(claim['part']+'_ids', []):
            raise ValueError('depicted subject/addressee conflicts with the locked actor')
    if use.get('addressee_role') is not None:
        target = cast.get(use['addressee_role'])
        if target is None or target not in shot.get('addressee_ids', []):
            raise ValueError('claimed dialogue addressee is not the observed actor')
    if not isinstance(use.get('reason'), str) or not use['reason'].strip():
        raise ValueError('shot use needs a factual reason')
    return use


def allocate(plan, lines, shots, cast, *, max_seconds):
    """Use measured TTS lengths; shared source atoms count once across windows."""
    by_id = {s['slot_id']: s for s in shots}
    if len(by_id) != len(shots):
        raise ValueError('duplicate shot identity')
    uses = plan.get('lines', [])
    if [r.get('line_id') for r in uses] != [r['line_id'] for r in lines]:
        raise ValueError('schedule must cover every line exactly once in order')
    items, anchored = [], set()
    for line, row in zip(lines, uses):
        role = line.get('role')
        if role not in cast:
            raise ValueError('line role is not cast')
        options = row.get('uses', [])
        if not options or len({u.get('shot_id') for u in options}) != len(options):
            raise ValueError('line needs unique observed shot options')
        for use in options:
            shot = by_id.get(use.get('shot_id'))
            if shot is None:
                raise ValueError('unobserved shot id')
            try:
                validate_use(use, shot, cast, role)
            except ValueError as error:
                raise ValueError(line['line_id']+'/'+shot['slot_id']+': '+str(error)) from error
            targets = {cast[r] for r in line.get('addressee_roles', []) if r in cast}
            if (use['mode'] == 'speaker' and targets and shot.get('addressee_ids')
                    and not set(shot['addressee_ids']).issubset(targets)):
                raise ValueError('observed dialogue addressee conflicts with reference role relation')
        frames = math.ceil((line['voiced_s']+line.get('pause_after_s',0))*FPS - 1e-9)
        if frames < 1:
            raise ValueError('empty measured speech')
        # Establish every visible voice with a genuine speaking shot. The rest
        # of a long utterance may continue over approved listeners/cutaways.
        anchor = role not in anchored and not line.get('reference_offscreen', False)
        speech = [u['shot_id'] for u in options if u['mode'] == 'speaker']
        prefix = min(frames, FPS) if anchor else 0
        if anchor and not speech:
            raise ValueError('first visible voice has no speaking anchor: '+role)
        base = dict(line_id=line['line_id'], scene_id=line['scene_id'], role=role,
                    kind='speech', reason=row.get('reason') or line.get('visual_reason', 'approved observed shot'),
                    shot_reasons={u['shot_id']: u['reason'] for u in options})
        if prefix:
            items.append(dict(base, item_id=line['line_id']+'_anchor', frames=prefix, shot_ids=speech))
            anchored.add(role)
        if frames-prefix:
            items.append(dict(base, item_id=line['line_id'], frames=frames-prefix,
                              shot_ids=[u['shot_id'] for u in options], speaking_ids=speech))
    if sum(i['frames'] for i in items) > math.floor(max_seconds*FPS):
        raise ValueError('measured complete speech exceeds the task duration limit')
    if not _fits(items, by_id):
        raise ValueError('unique eligible footage cannot hold measured complete speech; search more material')
    try:
        rows = schedule(items, by_id)
    except CoverageError as error:
        raise ValueError("finite footage scheduling failed: "+str(error)) from error
    validate_allocation(rows, items, by_id)
    return rows, items


def validate_allocation(rows, items, shots):
    expected = {i['item_id']: i for i in items}
    totals = {k: 0 for k in expected}
    used = {}
    for row in rows:
        item, shot = expected.get(row['item_id']), shots.get(row['slot_id'])
        if item is None or shot is None or row['slot_id'] not in item['shot_ids']:
            raise ValueError('allocation escaped approved shot choices')
        a, b = row['first'], row['last']
        low = math.ceil(shot['source_start_s']*FPS-1e-6)
        high = math.floor(shot['source_end_s']*FPS+1e-6)
        if not all(type(n) is int for n in (a,b)) or not low <= a < b <= high:
            raise ValueError('allocation outside actual source frames')
        source = shot['source_sha256']
        if row.get('source_sha256') != source:
            raise ValueError('allocation source identity changed')
        if any(a < y and x < b for x,y in used.setdefault(source, [])):
            raise ValueError('source frames reused through overlapping windows')
        used[source].append((a,b))
        totals[row['item_id']] += b-a
    if totals != {k:i['frames'] for k,i in expected.items()}:
        raise ValueError('allocation does not cover measured audio exactly')
    return dict(policy=POLICY, passed=True, frames=sum(totals.values()), unique_frames=sum(totals.values()))


def capacity_preflight(lines, shots, cast, *, max_seconds, scene_shots=None):
    """An upper bound before asking the model for meaning-aware choices.

    If even ALL identity-compatible options cannot hold the real voice, no
    reasoning or rearrangement can fix that. Return the deficit to retrieval.
    """
    from .scene_coverage import capacity
    plan = dict(lines=[])
    for line in lines:
        options = []
        for shot in shots:
            if scene_shots is not None and shot['slot_id'] not in scene_shots[line['scene_id']]:
                continue
            for mode in ('speaker', 'listener', 'cutaway'):
                use = dict(shot_id=shot['slot_id'], mode=mode, claims=[], reason='physical eligibility upper bound')
                try:
                    validate_use(use, shot, cast, line['role'])
                except ValueError:
                    continue
                targets = {cast[r] for r in line.get('addressee_roles', []) if r in cast}
                if mode == 'speaker' and targets and shot.get('addressee_ids') and not set(shot['addressee_ids']).issubset(targets):
                    continue
                options.append(use)
                break
        plan['lines'].append(dict(line_id=line['line_id'], uses=options))
    report = dict(required_s=sum(l['voiced_s']+l.get('pause_after_s', 0) for l in lines),
                  unique_pool_s=capacity(shots), by_role=[], options=plan['lines'])
    for role in cast:
        ids = {u['shot_id'] for l,q in zip(lines,plan['lines']) if l['role']==role for u in q['uses']}
        required = sum(l['voiced_s']+l.get('pause_after_s',0) for l in lines if l['role']==role)
        available = capacity([s for s in shots if s['slot_id'] in ids])
        report['by_role'].append(dict(role=role, required_s=required, eligible_s=available,
                                      deficit_s=max(0, required-available)))
    try:
        allocate(plan, lines, shots, cast, max_seconds=max_seconds)
        report['feasible'] = True
    except ValueError as error:
        report.update(feasible=False, error=str(error))
    return report
