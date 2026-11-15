"""L1 degraded delivery: keep every received utterance when finite normal coverage cannot.

Used only after the normal joint schedule has stopped trying (the reselection
returned the same pool, or its budget is spent). It makes no generation-model
or TTS request (the final review after registration is the only model call): the attempt's own script, voices and verified speech samples are
reused, and pictures come from the attempt's coverage shots. Unused footage is
spent first; then shots are reused in preference order so the picture lasts as
long as the real speech. Every reuse is recorded in the plan and the result.
"""
from collections import Counter
from copy import deepcopy
import math

from .joint_dialogue import FPS, eligible, place_audio, speaker_anchors, to_timeline, unused
from .state import json_sha, write_json

POLICY = 'degraded_dialogue_reuse_v1'
LEVEL = 1
MODE = 'dialogue_reuse'


def _speech_candidates(scene, line, anchor):
    """Preference-ordered (shot_id, tier); never a shot where someone else talks unless nothing else exists."""
    shots = scene['coverage_shots']
    rid = scene['role_bindings'][line['role']]['role_id']
    approved = [o['shot_id'] for o in line.get('shot_options', [])]
    by_id = {s['slot_id']: s for s in shots}
    speaker = [s['slot_id'] for s in shots if s['speaking'] == [rid]]
    tiers = [
        ('approved_speaker', [i for i in approved if i in speaker]),
        ('speaker', speaker),
        ('approved_reaction', [i for i in approved if i in by_id]),
        ('written', [i for i in line.get('shot_ids', []) if i in by_id and eligible(scene, by_id[i], line['role'])]),
        ('listener', [s['slot_id'] for s in shots if eligible(scene, s, line['role'])]),
        ('silent_bound', [s['slot_id'] for s in shots if eligible(scene, s)]),
        ('silent_any', [s['slot_id'] for s in shots if not s['speaking']]),
        ('other_speaker', [s['slot_id'] for s in shots]),
    ]
    if not anchor:
        # Ordinary lines keep the reviewer's ranking ahead of unreviewed speaker shots.
        tiers[1], tiers[2] = tiers[2], tiers[1]
    seen, ordered = set(), []
    for tier, ids in tiers:
        for ident in ids:
            if ident not in seen:
                seen.add(ident)
                ordered.append((ident, tier))
        if ordered and tier in ('listener', 'silent_bound', 'silent_any'):
            break  # Do not mix in weaker pictures when acceptable ones exist.
    return ordered


def _fill(candidates, shots, frames, used, *, reuse, min_frames):
    """Unused footage first, then whole-shot reuse; a final short piece may overrun the voice."""
    pieces, remaining = [], frames
    for ident, tier in candidates:
        shot = shots[ident]
        whole = round(shot['source_end_s']*FPS)-round(shot['source_start_s']*FPS)
        for first, last in unused(shot, used+pieces):
            if remaining <= 0:
                break
            if last-first < min(min_frames, whole):
                continue  # a leftover sliver of a longer shot would only flash
            length = min(last-first, max(remaining, min_frames))
            pieces.append(dict(slot_id=ident, source_sha256=shot['source_sha256'], first=first,
                               last=first+length, tier=tier, reused=False))
            remaining -= length
        if remaining <= 0:
            break
    while reuse and remaining > 0:
        before = remaining
        for ident, tier in candidates:
            if remaining <= 0:
                break
            shot = shots[ident]
            first, last = round(shot['source_start_s']*FPS), round(shot['source_end_s']*FPS)
            if last <= first:
                continue
            length = min(last-first, max(remaining, min_frames))
            pieces.append(dict(slot_id=ident, source_sha256=shot['source_sha256'], first=first,
                               last=first+length, tier=tier, reused=True))
            remaining -= length
        if remaining == before:
            break
    return pieces, max(0, remaining)


def _gaps(scene, rows):
    refs = {r['line_id']: r for r in scene['reference_lines']}
    gaps = [max(0, refs[rows[0]['reference_line_ids'][0]]['start_s']-scene['start_s'])]
    for i, row in enumerate(rows):
        end = refs[row['reference_line_ids'][-1]]['end_s']
        following = refs[rows[i+1]['reference_line_ids'][0]]['start_s'] if i+1 < len(rows) else scene['end_s']
        gaps.append(max(0, following-end))
    return [math.floor(g*FPS) for g in gaps]


def plan(lock, script, lines):
    """Deterministic allocation over the attempt's coverage shots; returns (allocation, report)."""
    from .dialogue_cut import MIN_FRAMES
    from .scene_coverage import capacity
    shots = {s['slot_id']: s for scene in lock['scenes'] for s in scene['coverage_shots']}
    allocation, used, report = [], [], []

    def add(scene, pieces, *, item_id, kind, line_id=None, role=None, reason):
        for piece in pieces:
            used.append(piece)
            allocation.append(dict(slot_id=piece['slot_id'], first=piece['first'], last=piece['last'],
                source_sha256=piece['source_sha256'], scene_id=scene['scene_id'], item_id=item_id, kind=kind,
                line_id=line_id, voice_role=role, reason=reason,
                degraded=dict(policy=POLICY, tier=piece['tier'], reused=piece['reused'])))

    for scene in lock['scenes']:
        sid = scene['scene_id']
        rows = [r for r in lines if r['scene_id'] == sid]
        target = round((scene['end_s']-scene['start_s'])*FPS)
        if not rows:
            visual = next((v for v in script.get('visual_scenes', []) if v['scene_id'] == sid), {})
            hints = [i for i in visual.get('shot_ids', []) if i in shots]
            candidates = ([(i, 'written') for i in hints] or
                          [(s['slot_id'], 'silent_bound') for s in scene['coverage_shots'] if eligible(scene, s)] or
                          [(s['slot_id'], 'silent_any') for s in scene['coverage_shots']])
            want = min(target, math.floor(capacity([shots[i] for i, _ in candidates])*FPS))
            pieces, short = _fill(candidates, shots, want, used, reuse=True, min_frames=MIN_FRAMES)
            add(scene, pieces, item_id=sid+'_visual', kind='visual',
                reason=visual.get('action_reason') or 'degraded: bound roles in this scene')
            report.append(dict(scene_id=sid, kind='visual', reference_s=target/FPS,
                planned_s=sum(p['last']-p['first'] for p in pieces)/FPS, unfilled_s=short/FPS))
            continue
        refs = {r['line_id']: r for r in scene['reference_lines']}
        anchors = speaker_anchors(scene, refs, rows)
        pauses = [(s['slot_id'], 'silent_bound') for s in scene['coverage_shots'] if eligible(scene, s)]
        gaps = _gaps(scene, rows)
        for i, row in enumerate(rows):
            if gaps[i] >= MIN_FRAMES and pauses:
                # Pauses are pacing, not speech: only unused footage, never a repeat.
                pieces, _ = _fill(pauses, shots, gaps[i], used, reuse=False, min_frames=MIN_FRAMES)
                add(scene, pieces, item_id=f'{sid}_pause_{i}', kind='reaction',
                    reason='degraded: reference pause from unused footage')
            candidates = _speech_candidates(scene, row, anchors[row['line_id']])
            frames = math.ceil(row['voiced_s']*FPS)
            pieces, short = _fill(candidates, shots, frames, used, reuse=True, min_frames=MIN_FRAMES)
            if short:
                raise ValueError(f'degraded_speech_unfilled:{row["line_id"]}:{short}')
            add(scene, pieces, item_id=row['line_id'], kind='speech', line_id=row['line_id'], role=row['role'],
                reason=row.get('visual_reason') or 'degraded: complete received speech')
            tiers = Counter(p['tier'] for p in pieces)
            report.append(dict(scene_id=sid, kind='speech', line_id=row['line_id'], role=row['role'],
                voiced_s=row['voiced_s'], picture_s=sum(p['last']-p['first'] for p in pieces)/FPS,
                reused_s=sum(p['last']-p['first'] for p in pieces if p['reused'])/FPS,
                speaker_anchor=anchors[row['line_id']], tiers=dict(tiers),
                semantic_review=bool(row.get('shot_options'))))
        if gaps[-1] >= MIN_FRAMES and pauses:
            pieces, _ = _fill(pauses, shots, gaps[-1], used, reuse=False, min_frames=MIN_FRAMES)
            add(scene, pieces, item_id=sid+'_pause_end', kind='reaction',
                reason='degraded: reference closing pause from unused footage')
    return allocation, report


def run(exc, trigger, reference, strategy, sources, state, output, *, render_fn=None, reference_source=None):
    """Deliver an L1 degraded video from a CoverageError's prepared attempt, or None when L1 cannot apply."""
    from .dialogue_cut import deliver
    prepared = getattr(exc, 'prepared', None)
    if not prepared:
        return None  # No script or speech exists for this attempt; L1 has nothing to keep.
    lock, windows = prepared['lock'], prepared['windows']
    lines = [deepcopy(line) for line in prepared['lines']]
    allocation, report = plan(lock, prepared['script'], lines)
    timeline = to_timeline(allocation, lock, windows)
    audio_lines, captions = place_audio(lines, timeline.rows, prepared['samples'], prepared['cache'], 'degraded_reuse')
    speech = [r for r in report if r['kind'] == 'speech']
    reused = sum(r['reused_s'] for r in speech)
    weak = sorted({t for r in speech for t in r['tiers'] if t in ('silent_bound', 'silent_any', 'other_speaker')})
    limitations = [f'{POLICY}: normal finite schedule stopped ({trigger}); complete received speech kept.',
                   f'{reused:.2f}s of speech picture reuses already shown shots.']
    if weak:
        limitations.append('Some speech plays over pictures without the speaker talking: ' + ', '.join(weak))
    if any(not r['semantic_review'] for r in speech):
        limitations.append('Some lines use structurally eligible shots without a semantic shot review.')
    # A reselection may already have rewritten the window list; the record must
    # name the windows this video actually uses.
    write_json(output/'watched_windows.json', windows)
    degradation = dict(policy=POLICY, fallback_level=LEVEL, delivery_mode=MODE,
        reasons=sorted({exc.reason, trigger}), trigger=trigger, error=str(exc)[:4000],
        windows_source='failed_attempt', window_ids=[w['window_id'] for w in windows],
        failed_scenes=exc.scene_ids, generation_model_requests_added=0, tts_requests_added=0,
        review_after_registration=True,
        speech_s=sum(r['voiced_s'] for r in speech), reused_speech_picture_s=reused,
        picture_s=timeline.frames/FPS, weak_picture_tiers=weak, limitations=limitations, items=report)
    path = output/'degraded_delivery.json'
    write_json(path, degradation)
    state.set_artifact('degraded_delivery', dict(path=str(path), sha256=json_sha(degradation),
        policy=POLICY, fallback_level=LEVEL, reasons=degradation['reasons']))
    degradation['path'] = str(path)
    notes = deepcopy(lock.get('mismatches', []))
    return deliver(lock, reference, strategy, sources, state, prepared['cache'], output, prepared['voices'],
                   prepared['script'], timeline.rows, lines, audio_lines, captions, notes,
                   render_fn=render_fn, reference_source=reference_source, degradation=degradation)
