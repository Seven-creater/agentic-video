"""Mechanical dialogue finishing; never changes words, actors or source actions."""
from copy import deepcopy

def remove_micro_tails(rows, lines, *, fps=30, min_frames=12, minimum_speed_ratio=.8):
    """Absorb a sub-minimum shot tail by mildly retiming its preceding shot.

    Only the same utterance may be joined. Its opening, audio, total clock and
    source bounds remain unchanged. Whole short utterances are never removed.
    """
    kept, changes = [], []
    for row in rows:
        frames = round((row['end_s']-row['start_s'])*fps)
        previous = kept[-1] if kept else None
        if (previous and 0 < frames < min_frames and row.get('line_id') and
                row['line_id'] == previous.get('line_id') and
                row['scene_id'] == previous['scene_id'] and
                row.get('slot_id') != previous.get('slot_id') and
                abs(previous['end_s']-row['start_s']) < .5/fps and
                not previous.get('loop_range') and not previous.get('hold_s')):
            old_frames = round((previous['end_s']-previous['start_s'])*fps)
            ratio = old_frames/(old_frames+frames)
            speed = previous.get('speed', 1)*ratio
            if ratio >= minimum_speed_ratio and speed >= .5:
                changes.append(dict(removed=deepcopy(row), previous=deepcopy(previous), new_speed=speed))
                previous.update(end_s=row['end_s'], speed=speed)
                continue
        kept.append(row)
    rows[:] = kept
    for line in lines:
        line['segment_ids'] = [r['segment_id'] for r in rows if r.get('line_id') == line['line_id']]
    return dict(policy='same_utterance_micro_tail_retime_v1', changes=changes)


def verified_masks(rows, proxies, observations):
    """Use the exact masks whose muted view was independently subtitle-blind.

    Return None before changing anything if any row lacks that evidence. The
    caller then uses the existing subtitle refinement instead.
    """
    selected = []
    for row in rows:
        wid = row['window_id']; bound = proxies.get(wid, {}); proxy = bound.get('proxy', {})
        visual = observations.get(wid, {})
        if not (visual.get('subtitle_blind') is True and
                visual.get('evidence_sha256') == proxy.get('sha256') and proxy.get('sha256') and
                proxy.get('source_id') == row['source_id'] and
                proxy.get('source_start_s', float('inf')) <= row['source_in_s']+.001 and
                proxy.get('source_end_s', -1) >= row['source_out_s']-.001 and
                bound.get('source_masks') == proxy.get('spec', {}).get('subtitle_masks')):
            return None
        masks = []
        for mask in bound['source_masks']:
            if mask['start_s'] >= row['source_out_s'] or row['source_in_s'] >= mask['end_s']:
                continue
            candidate = dict(mask)
            # Merge overlapping rectangles with the same clock, eliminating
            # repeated pixelation without shrinking the verified coverage.
            while True:
                match = next((m for m in masks if m['start_s'] == candidate['start_s'] and
                    m['end_s'] == candidate['end_s'] and
                    m['x'] <= candidate['x']+candidate['w'] and candidate['x'] <= m['x']+m['w'] and
                    m['y'] <= candidate['y']+candidate['h'] and candidate['y'] <= m['y']+m['h']), None)
                if match is None:break
                x=min(match['x'],candidate['x']); y=min(match['y'],candidate['y'])
                candidate.update(x=x,y=y,w=max(match['x']+match['w'],candidate['x']+candidate['w'])-x,
                    h=max(match['y']+match['h'],candidate['y']+candidate['h'])-y)
                masks.remove(match)
            masks.append(candidate)
        selected.append([dict(m,mode='mosaic',opacity=.92,block_px=18,
            refinement_stage='retrieval_verified_dialogue_region') for m in masks])
    for row, masks in zip(rows, selected):
        row['source_subtitle_masks'] = []
        row['source_subtitle_treatments'] = masks
    return dict(policy='verified_subtitle_masks_only_v1', windows=sorted({r['window_id'] for r in rows}))
