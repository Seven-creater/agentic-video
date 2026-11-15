"""L2/L3 fallback: deliver a labelled storyboard when the scene route cannot finish.

Runs after any failure of the joint scene route that L1 (degraded_cut) could not
absorb. It makes no generation-model request (the final review after
registration is the only model call). Text comes from the reference's own timed
transcript (not adapted); pictures come from a local lexical search of the visual
library, one cut per line. L2 voices every line with one narrator voice; when
any synthesis fails it stops sending and falls to L3 (captions only). A
reference without usable transcript keeps its original audio, labelled as such.
"""
from copy import deepcopy
import math
from pathlib import Path

from .state import json_sha, write_json

POLICY = 'fallback_storyboard_v1'
FPS = 30


def _error_code(error):
    text = str(error)
    head = text.split(':', 1)[0].strip()
    return head if head and ' ' not in head and len(head) <= 80 else type(error).__name__


def reference_lines(asr):
    """Timed, non-empty reference lines in order; [] when no usable transcript exists."""
    from .scene_route import transcript_lines
    from .state import LibraryStopped
    try:
        rows = transcript_lines(asr or {})
    except LibraryStopped:
        return []
    lines, previous = [], 0.0
    for row in sorted(rows, key=lambda r: r['start_s']):
        text = ' '.join(str(row.get('text', '')).split())
        start, end = max(float(row['start_s']), previous), float(row['end_s'])
        if text and end > start:
            lines.append(dict(line_id=row['line_id'], text=text, start_s=start, end_s=end))
            previous = end
    return lines


def drop_non_speech(lines, non_speech):
    """Remove ASR lines the verified turn review classified as non-speech (credits,
    misheard music). Without that review (failure before it) every line is kept."""
    texts = {''.join(str(r.get('text', '')).split()) for r in non_speech or [] if r.get('text')}
    kept = []
    for line in lines:
        text = ''.join(line['text'].split())
        covering = ''.join(''.join(str(r.get('text', '')).split()) for r in non_speech or []
                           if r.get('start_s', 0) < line['end_s'] and line['start_s'] < r.get('end_s', 0))
        if text not in texts and covering != text:
            kept.append(line)
    return kept


def timing(lines, voiced, duration):
    """Speech keeps the reference rhythm and is never cut: a long voice pushes later lines.
    A line without a received voice keeps its reference duration as a caption."""
    voiced = voiced or {}
    placed, cursor = [], 0.0
    for line in lines:
        start = max(line['start_s'], cursor)
        end = start + voiced.get(line['line_id'], line['end_s'] - line['start_s'])
        placed.append(dict(line, at_s=start, until_s=end, voiced=line['line_id'] in voiced))
        cursor = end
    tail = max(0.0, duration - lines[-1]['end_s']) if lines else duration
    return placed, cursor + tail


def beats(placed, total_s, min_frames):
    """Cut boundaries at each line start; slivers merge into the previous beat."""
    total = math.ceil(total_s*FPS - 1e-9)
    marks = sorted({0, *(min(total, round(p['at_s']*FPS)) for p in placed), total})
    spans = []
    for a, b in zip(marks, marks[1:]):
        if spans and b-a < min_frames:
            spans[-1][1] = b
        elif b > a:
            spans.append([a, b])
    if len(spans) > 1 and spans[0][1]-spans[0][0] < min_frames:
        spans[1][0] = spans[0][0]
        spans.pop(0)
    return [b-a for a, b in spans]


def pictures(cards, frames_per_beat, min_frames):
    """One new card per beat; a card shorter than its beat continues into the next. Reuse is recorded."""
    cards = [c for c in cards if round(c['source_end_s']*FPS) > round(c['source_start_s']*FPS)]
    if not cards:
        raise ValueError('fallback_no_library_pictures')
    pieces, uses, index = [], {}, 0
    for beat, frames in enumerate(frames_per_beat):
        remaining = frames
        while remaining > 0:
            card = cards[index % len(cards)]
            index += 1
            first, last = round(card['source_start_s']*FPS), round(card['source_end_s']*FPS)
            length = min(last-first, remaining)
            leftover = remaining-length
            if 0 < leftover < min_frames and length-(min_frames-leftover) >= min_frames:
                length -= min_frames-leftover  # leave the next card a visible piece, not a flash
            uses[card['observation_id']] = uses.get(card['observation_id'], 0)+1
            pieces.append(dict(card=card, beat=beat, first=first, frames=length,
                               reused=uses[card['observation_id']] > 1))
            remaining -= length
    return pieces


def _library_cards(config, sources, reference, lines, limit=80):
    from .visual_library import VisualLibrary
    store = VisualLibrary(config['visual_library_root'], content_sources=sources)
    by_sha = {s['sha256']: s for s in sources['sources']}
    query = ' '.join([str(reference.get(k) or '') for k in ('theme', 'intended_takeaway')] +
                     [line['text'] for line in lines])
    cards = (store.search(query, source_sha256s=list(by_sha), limit=limit) or
             store.search('', source_sha256s=list(by_sha), limit=limit))
    usable = []
    for card in cards:
        source = by_sha.get(card['source_sha256'])
        if source is None or not 0 <= card['source_start_s'] < card['source_end_s'] <= source['duration_s']:
            continue
        try:
            masks = store.get(card['observation_id'])['window'].get('source_subtitle_masks', [])
        except (OSError, ValueError, KeyError):
            continue
        usable.append(dict(observation_id=card['observation_id'], source_id=source['source_id'],
            source_sha256=card['source_sha256'], source_start_s=card['source_start_s'],
            source_end_s=card['source_end_s'], source_subtitle_masks=deepcopy(masks or []),
            summary=card.get('summary')))
    return usable, query


def run(error, reference, strategy, ref, asr, sources, state, cache, output, *, render_fn=None, cards=None):
    """Deliver an L2/L3 fallback video for this failed attempt; raises when even that is impossible."""
    from . import audio_render
    from .dialogue_cut import MIN_FRAMES, RATES, _speech, deliver
    output, cache = Path(output), Path(cache)/'fallback_cut'
    cache.mkdir(parents=True, exist_ok=True)
    lines = reference_lines(asr)
    try:
        from .pipeline import _material_artifact
        non_speech = _material_artifact(state, 'reference_non_speech')
    except Exception:  # noqa: BLE001 - a fallback never stops on optional evidence
        non_speech = None
    lines = drop_non_speech(lines, non_speech)
    query = None
    if cards is None:
        cards, query = _library_cards(state.input_lock['configuration'], sources, reference, lines)
    voice = next(iter(RATES))
    samples, tts_error = {}, None
    for line in lines:
        # After the first failure no new paid request is sent; every voice already
        # received is still played, the remaining lines become captions.
        try:
            got = _speech(dict(line, id=f'narration_{line["line_id"]}'), voice, state, output)
        except Exception as exc:  # noqa: BLE001 - any synthesis failure stops new requests
            tts_error = f'{type(exc).__name__}:{str(exc)[:500]}'
            break
        if not len(got):
            tts_error = f'silent_synthesis:{line["line_id"]}'
            break
        samples[line['line_id']] = got
    level = 2 if samples else 3
    voiced = {k: len(v)/audio_render.SAMPLE_RATE for k, v in samples.items()}
    unvoiced = [line['line_id'] for line in lines if line['line_id'] not in samples]
    placed, total = timing(lines, voiced, ref['duration_s'])
    chosen = pictures(cards, beats(placed, total, MIN_FRAMES), MIN_FRAMES)
    rows, cursor = [], 0
    for piece in chosen:
        card = piece['card']
        rows.append(dict(segment_id=f'seg_{len(rows)+1}', scene_id='fallback', slot_id=None,
            window_id=card['observation_id'], source_id=card['source_id'],
            source_in_s=piece['first']/FPS, source_out_s=(piece['first']+piece['frames'])/FPS,
            speed=1, look='none', framing='fit', role_ids=[], role=None, kind='storyboard', line_id=None,
            start_s=cursor/FPS, end_s=(cursor+piece['frames'])/FPS,
            source_subtitle_masks=deepcopy(card['source_subtitle_masks']),
            static_range=None, loop_range=None,
            degraded=dict(policy=POLICY, reused=piece['reused'], picture_source='visual_library_lexical_search')))
        cursor += piece['frames']
    audio_lines, captions, script_lines = [], [], []
    for row in placed:
        caption_end = min(row['until_s'], cursor/FPS)
        if caption_end > row['at_s']:
            captions.append(dict(text=row['text'], start_s=row['at_s'], end_s=caption_end))
        if row['voiced']:
            path = cache/f'narration_{row["line_id"]}.wav'
            audio_render._wav(path, samples[row['line_id']])
            audio_lines.append(dict(id=f'narration_{row["line_id"]}', audio_path=str(path),
                                    start_s=row['at_s'], end_s=row['until_s']))
        script_lines.append(dict(line_id=f'narration_{row["line_id"]}', id=f'narration_{row["line_id"]}',
            reference_line_ids=[row['line_id']], text=row['text'], role='旁白', mode='narration',
            voice=voice if row['voiced'] else None, start_s=row['at_s'], end_s=row['until_s'],
            picture_relation='storyboard_not_lip_sync'))
    audio_source = ('tts_narration_of_reference_text' if level == 2 else
                    'none_subtitles_only' if lines else
                    'reference_original_audio_may_contain_voices' if ref.get('audio_stream_index') is not None else
                    'none')
    reused = sum(p['frames'] for p in chosen if p['reused'])/FPS
    limitations = [f'{POLICY} L{level}: normal scene route failed ({_error_code(error)}); no generation-model request was made (final review runs after registration).',
                   'Pictures come from a local lexical library search, one cut per line; they are not matched to speakers.',
                   f'{reused:.2f}s of pictures reuse an already shown library window.']
    if lines:
        limitations.append('Text is the reference transcript itself, not adapted to the library story.')
    if unvoiced and lines:
        limitations.append(f'{len(unvoiced)} of {len(lines)} lines have no new voice and are captions only'
                           + (f' (synthesis stopped: {tts_error})' if tts_error else '.'))
    if not lines:
        limitations.append('No usable reference transcript; audio is ' + audio_source + '.')
    degradation = dict(policy=POLICY, fallback_level=level,
        delivery_mode='narration_storyboard' if level == 2 else 'subtitle_storyboard' if lines else 'picture_storyboard',
        reasons=sorted({_error_code(error), 'scene_route_failed'}), error=f'{type(error).__name__}:{str(error)[:4000]}',
        text_source='reference_transcript_unadapted' if lines else 'none', audio_source=audio_source,
        tts_error=tts_error, voiced_lines=len(samples), unvoiced_line_ids=unvoiced, reference_lines=len(lines), narrator_voice=voice if level == 2 else None,
        picture_source='visual_library_lexical_search', library_query=query, cards_used=len({p['card']['observation_id'] for p in chosen}),
        reused_picture_s=reused, picture_s=cursor/FPS, reference_s=ref['duration_s'],
        generation_model_requests_added=0, review_after_registration=True, limitations=limitations)
    path = output/f'fallback_l{level}.json'
    write_json(path, degradation)
    state.set_artifact('fallback_delivery', dict(path=str(path), sha256=json_sha(degradation),
        policy=POLICY, fallback_level=level, reasons=degradation['reasons']))
    degradation['path'] = str(path)
    lock = dict(cast_bindings={}, editing_policy=POLICY, scenes=[dict(scene_id='fallback', start_s=0,
        end_s=ref['duration_s'], role_bindings={}, picture=dict(scene=None), template=None,
        reference_lines=[dict(line_id=r['line_id'], role=None, text=r['text']) for r in placed])])
    return deliver(lock, reference, strategy, sources, state, cache, output, {'旁白': voice} if level == 2 else {},
                   dict(facts=[]), rows, script_lines, audio_lines, captions, [],
                   render_fn=render_fn, reference_source=ref if not lines else None, degradation=degradation)
