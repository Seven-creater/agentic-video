"""Trace the proposed, independently approved and actually rendered picture."""

def audit_schedule(script, lines, allocation, shots):
    by_id={r['line_id']:r for r in lines}
    rows=[]
    approved_union=set()
    for raw in script['lines']:
        line=by_id[raw['line_id']]
        approved=[o['shot_id'] for o in line.get('shot_options',[])]
        approved_union.update(approved)
        used=list(dict.fromkeys(r['slot_id'] for r in allocation if r.get('line_id')==raw['line_id']))
        if any(i not in approved for i in used):raise ValueError('rendered_unapproved_shot:'+raw['line_id'])
        rows.append(dict(line_id=raw['line_id'],proposed=list(raw['shot_ids']),approved=approved,used=used,
            substitutions=[dict(shot_id=i,reason=next(o['reason'] for o in line['shot_options'] if o['shot_id']==i))
                           for i in used if i not in raw['shot_ids']]))
    used=set(r['slot_id'] for r in allocation)
    intervals={}
    total=0
    for r in allocation:
        a,b=r['first'],r['last'];total+=b-a
        intervals.setdefault(r['source_sha256'],[]).append((a,b))
    unique=0
    for spans in intervals.values():
        end=None
        for a,b in sorted(spans):
            unique+=max(0,b-max(a,end if end is not None else a));end=max(b,end if end is not None else b)
    # A single legitimate approved composition is allowed. Multiple source
    # IDs alone are diagnostic: the reviewer must explain an intentional hold.
    distinct_first={r['shot_options'][0]['shot_id'] for r in lines if r.get('shot_options')}
    return dict(policy='planned_to_rendered_v1',lines=rows,used_shot_count=len(used),
        approved_shot_count=len(approved_union),repeated_source_fraction=1-unique/total if total else 0,
        collapsed_plan=len(used)==1 and len(distinct_first)>1)


def bind_performance_permissions(lock, lines, rows, shots):
    """Carry existing independently authorized dubbing permissions to final review."""
    scenes={s['scene_id']:s for s in lock['scenes']}
    for row in rows:
        shot=shots[row['slot_id']]
        for key in ('dub_speaker_ids','concealed_dub_ids','stylized_dub_ids'):
            row[key]=list(shot.get(key,[]))
        row['performance_mode']=shot.get('performance_policy','strict')
    for line in lines:
        first=next(r for r in rows if r.get('line_id')==line['line_id'])
        actor=scenes[line['scene_id']]['role_bindings'][line['role']]['role_id']
        line['allows_occluded_intro']=bool(line.get('visual_anchor') and actor in first['concealed_dub_ids'])
        line['allows_stylized_intro']=bool(line.get('visual_anchor') and actor in first['stylized_dub_ids'])


def apply_schedule_quality(result,audit):
    """Keep a playable artifact when bounded scheduling repair is insufficient."""
    if audit.get('collapsed_plan'):
        result.update(status='delivered_with_review_notes',quality_status='needs_improvement',content_delivery_ready=False,joint_quality_gate=False)
        result.setdefault('notes',[]).append('planned_picture_variation_not_preserved')
    return result
