#!/usr/bin/env python3
"""Audit static coverage, fixed-view loops, delivered lines and final.mp4 decoding.

Old boolean-only reviews cannot certify a repeat. This checks review coverage,
not pixel motion: a wrong GLM interval still needs visual review. Set FFMPEG or
pass --ffmpeg if ffmpeg is not on PATH.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

from omni_story.library.speech_slots import static_bounds, trim_edges
from omni_story.library.dialogue_cut import FPS


def audit_timeline(rows, report=None):
    slots = {(scene['scene_id'], slot['slot_id']): slot
             for scene in (report or {}).get('scenes', []) for slot in scene.get('speech_slots', [])}
    issues, seen, repeats = [], {}, 0
    for row in rows:
        start, end = row['source_in_s'], row['source_out_s']
        previous = seen.setdefault(row['source_id'], [])
        repeated = any(min(end, b)-max(start, a) > 1e-6 for a, b in previous)
        previous.append((start, end))
        repeats += repeated
        receipt = slots.get((row['scene_id'], row.get('slot_id')), row)
        loop = receipt.get('loop_range')
        if not repeated and row['kind'] != 'reaction' and not loop:
            continue
        safe = static_bounds(receipt, receipt.get('source_start_s', -math.inf),
                             receipt.get('source_end_s', math.inf))
        reason = None
        if safe is None or not loop:
            reason = 'missing_static_range'
        elif not (safe[0]-1e-6 <= loop[0] < loop[1] <= safe[1]+1e-6 and
                  loop[0]-1e-6 <= start < end <= loop[1]+1e-6):
            reason = 'outside_trimmed_static_range'
        if reason:
            issues.append(dict(segment_id=row['segment_id'], final=[row['start_s'], row['end_s']],
                film=[start, end], kind=row['kind'], repeated=repeated, reason=reason))
    for line_id in dict.fromkeys(row.get('line_id') for row in rows):
        line = [r for r in rows if r.get('line_id') == line_id]
        if line_id and any(r.get('loop_range') for r in line) and len({
                (r['window_id'], r.get('slot_id'), tuple(r.get('loop_range') or [])) for r in line}) > 1:
            issues.append(dict(segment_id=line_id, final=[line[0]['start_s'], line[-1]['end_s']],
                film=None, kind='line', reason='loop_line_view_change'))
    return issues, repeats


def missed_static(report):
    missed = []
    for scene in (report or {}).get('scenes', []):
        for review in scene.get('edge_reviews', []):
            for change in review['changes']:
                slot = dict(slot_id=change['slot_id'], source_start_s=change['before'][0],
                    source_end_s=change['before'][1], pauses=[])
                entry = next(e for e in review['entries'] if e['slot_id'] == slot['slot_id'])
                kept, _, _ = trim_edges(slot, entry, deepcopy(change['reply']))
                for candidate in kept:
                    loop = candidate.get('loop_range')
                    if loop and not any(p.get('loop_range') and
                            min(loop[1], p['loop_range'][1])-max(loop[0], p['loop_range'][0]) > 1e-6
                            for p in scene['speech_slots']):
                        missed.append(dict(scene_id=scene['scene_id'], slot_id=slot['slot_id'], film=loop))
    return missed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--ffmpeg', default=os.environ.get('FFMPEG') or shutil.which('ffmpeg'))
    args = parser.parse_args()
    result = json.loads((args.run_dir/'dialogue_result.json').read_text())
    path = args.run_dir/'speech_slots.json'
    report = json.loads(path.read_text()) if path.exists() else None
    issues, repeats = audit_timeline(result['timeline'], report)
    for line in result.get('lines', []):
        if line.get('fit_action', '').startswith('drop'):
            issues.append(dict(segment_id=line['line_id'], final=None, film=None,
                kind='line', reason=line['fit_action']))
    for issue in issues:
        final = '-'.join(f'{t:.3f}' for t in issue['final']) if issue['final'] else 'missing'
        film = '-'.join(f'{t:.3f}' for t in issue['film']) if issue['film'] else 'multiple/none'
        print(f"{issue['segment_id']} final={final} film={film} "
              f"{issue['kind']} {issue['reason']}")
    missed = missed_static(report)
    for row in missed:
        print(f"missed_static {row['scene_id']}/{row['slot_id']} film={row['film']}")
    print('roles: scripted=' + str(dict(Counter(line['role'] for line in result.get('lines', [])))) +
          ' delivered=' + str(dict(Counter(line['role'] for line in result.get('lines', []) if 'start_s' in line))))
    if not args.ffmpeg:
        parser.error('ffmpeg required: set FFMPEG or pass --ffmpeg')
    video = subprocess.run([args.ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(args.run_dir/'final.mp4'),
        '-map', '0:v:0', '-an', '-progress', 'pipe:1', '-f', 'null', '-'], capture_output=True, text=True)
    progress = dict(line.split('=', 1) for line in video.stdout.splitlines() if '=' in line)
    frames = int(progress.get('frame', 0))
    # ffmpeg progress ends at the last frame's PTS, rounded to microseconds.
    # scene_v1 is fixed FPS; compare decoded frames with the planned frame count.
    duration = frames/FPS
    coverage = frames >= round(result['timeline'][-1]['end_s']*FPS)
    audio_covered = all(line['end_s'] <= result['timeline'][-1]['end_s']+1/FPS
                        for line in result.get('audio_lines', []))
    video_ok = video.returncode == 0 and frames > 0 and coverage and audio_covered
    if not video_ok:
        print(f'video_coverage_failed: {video.stderr.strip()} duration={duration:.3f} audio_covered={audio_covered}')
    counts = Counter(issue['reason'] for issue in issues)
    status = 'PASS' if not issues and video_ok else 'FAIL'
    print(f'{status} {args.run_dir.name}: rows={len(result["timeline"])} repeated_rows={repeats} '
          f'violations={len(issues)} missed_static={len(missed)} video_frames={frames} video_s={duration:.3f} '
          f'reasons={dict(counts)}')
    return int(status == 'FAIL')


if __name__ == '__main__':
    raise SystemExit(main())
