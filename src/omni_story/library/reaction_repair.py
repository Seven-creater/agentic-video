"""Move a disputed off-screen line onto verified object detail in the same event."""
from copy import deepcopy
import math

from omni_story.library.joint_dialogue import FPS


def use_object_insert(result, certified_segments):
    """Use the existing speech and source ranges; repair at most one reaction."""
    rows, lines = deepcopy(result['timeline']), deepcopy(result['lines'])
    audio = {r['id']:dict(r) for r in result['audio_lines']}
    checks = {r['line_id']:r for r in result['review']['identity_verification']['checks']}
    by_line = {r['line_id']:r for r in lines}
    repaired = None
    for i,row in enumerate(rows):
        check = checks.get(row['line_id'], {})
        if (row['kind']!='reaction' or not
                {'different_person_visibly_speaking','speaker_identity_mismatch'}.intersection(check.get('issues',[]))):
            continue
        if i==0:
            continue
        insert = rows[i-1]
        beats = set(row['action_beats'])
        if (insert['kind']!='visual' or insert['segment_id'] not in certified_segments
                or insert['scene_id']!=row['scene_id'] or not beats
                or not beats.issubset(insert['action_beats'])):
            continue
        line = by_line[row['line_id']]
        source_seconds = insert['source_out_s']-insert['source_in_s']
        frames = max(round((insert['end_s']-insert['start_s'])*FPS),
                     math.ceil((line['voiced_s']+.11)*FPS))
        if source_seconds/(frames/FPS)<.55:
            continue
        insert.update(kind='reaction',line_id=row['line_id'],role=row['role'],role_ids=row['role_ids'],
                      speed=source_seconds/(frames/FPS),end_s=insert['start_s']+frames/FPS)
        repaired = dict(line_id=row['line_id'],replaced_segment=row['segment_id'],
                        object_segment=insert['segment_id'])
        rows.pop(i)
        break
    if repaired is None:
        raise ValueError('No disputed reaction can move to a verified adjacent object insert')
    frames = 0
    captions = []
    for row in rows:
        count = round((row['end_s']-row['start_s'])*FPS)
        row.update(start_s=frames/FPS,end_s=(frames+count)/FPS)
        frames += count
        line = by_line.get(row['line_id'])
        if line is None:
            continue
        line.update(start_s=row['start_s'],end_s=row['end_s'],segment_ids=[row['segment_id']],
                    observed_action=row['observed_action'])
        cue = audio[line['id']]
        cue.update(start_s=row['start_s'],end_s=row['start_s']+line['voiced_s'])
        text = line['text'] if line['line_id']!=repaired['line_id'] else f"{line['role']}：{line['text']}"
        captions.append(dict(text=text,start_s=cue['start_s'],end_s=cue['end_s']))
    return rows,lines,list(audio.values()),captions,repaired
