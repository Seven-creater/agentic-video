#!/usr/bin/env python3
"""Evaluate material retrieval channel sets against human labels (plan C phase 1).

Sheet mode: for every need run the channel sets (baseline VisualLibrary.search,
bm25, text, image, bm25+text, bm25+text+image), pool the top-5 window-level
candidates per set (dedupe by window+time), render a contact sheet per pooled
candidate from shot-cache keyframes and write labels_template.json with
per-set ranks and a null label.

Score mode (--score labels.json): Success@5, MRR@10 and per-set unique-good
counts from the filled sheet.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from omni_story.library import material_embed, material_index
from omni_story.library.state import write_json

LOGGER = logging.getLogger('eval_material_retrieval')
SET_NAMES = ('baseline', 'bm25', 'text', 'image', 'bm25+text', 'bm25+text+image')
TILE_W, TILE_H = 320, 180


def _ascii(value):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value or '')).strip('_') or 'x'


def _baseline_queries(path, needs):
    """Baseline query per need: {need_id: query} JSON, or the whole file as one query."""
    raw = Path(path).read_text(encoding='utf-8')
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(key): str(value) for key, value in parsed.items()}
        if isinstance(parsed, list):
            return {need['need_id']: str(value) for need, value in zip(needs, parsed)}
    except json.JSONDecodeError:
        pass
    single = ' '.join(raw.split())
    return {need['need_id']: single for need in needs}


def _set_channels(set_name):
    return [] if set_name == 'baseline' else set_name.split('+')


def run_set(index, need, set_name, *, text_embedder=None, clip=None, baseline_library=None,
            baseline_query='', cast=None, final_k=50):
    """Ranked window candidates for one need and one channel set."""
    if set_name == 'baseline':
        if baseline_library is None:
            return None, 'baseline_library_not_provided'
        cards = baseline_library.search(baseline_query or '', limit=30)
        rows = [material_index.window_candidate(
            {'window_id': card['window_id'], 'source_sha256': card['source_sha256'],
             'film': index.meta.get('film_by_sha', {}).get(card['source_sha256']),
             'source_start_s': card['source_start_s'], 'source_end_s': card['source_end_s'],
             'record_id': card['observation_id']}) for card in cards]
        return rows, None
    result = index.search(need, channels=_set_channels(set_name), text_embedder=text_embedder,
                          clip=clip, final_k=final_k, top_windows=0, cast=cast)
    missing = set(_set_channels(set_name)) - set(result['channels_used'])
    if missing:
        return None, 'channels_unavailable:' + ','.join(sorted(missing))
    return [material_index.window_candidate(hit['record'], hit['matched_shots'])
            for hit in result['hits']], None


def _shots_for_span(index, film, span, matched_shots):
    """Matched shots first; otherwise shots overlapping the span, evenly spaced."""
    if matched_shots:
        return list(matched_shots)
    overlapping = sorted((shot for shot in index.image_shots
                          if shot.get('film') == film and
                          max(shot['shot_start'], span[0]) < min(shot['shot_end'], span[1])),
                         key=lambda shot: shot['shot_start'])
    if len(overlapping) <= 4:
        return overlapping
    positions = sorted({int(round(value)) for value in np.linspace(0, len(overlapping) - 1, 4)})
    return [overlapping[position] for position in positions]


def _tile(image_path, caption):
    from PIL import Image, ImageDraw, ImageFont

    canvas = Image.new('RGB', (TILE_W, TILE_H + 18), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default()
    except Exception:  # pragma: no cover - font always present in Pillow
        font = None
    tile = Image.new('RGB', (TILE_W, TILE_H), (60, 60, 60))
    if image_path and Path(image_path).is_file():
        with Image.open(image_path) as frame:
            frame = frame.convert('RGB')
            frame.thumbnail((TILE_W, TILE_H))
            tile.paste(frame, ((TILE_W - frame.width) // 2, (TILE_H - frame.height) // 2))
    else:
        draw_tile = ImageDraw.Draw(tile)
        draw_tile.text((8, TILE_H // 2), 'no keyframe', fill=(200, 200, 200))
    canvas.paste(tile, (0, 0))
    draw.text((4, TILE_H + 2), caption[:78], fill=(230, 230, 230), font=font)
    return canvas


def write_contact_sheet(index, need, candidate, out_path):
    """Grid of up to four keyframes for one pooled candidate."""
    from PIL import Image

    shots = _shots_for_span(index, candidate.get('film'), candidate['span'],
                            candidate.get('matched_shots') or [])
    sets_text = ' '.join(sorted(name + '#' + str(rank) for name, rank in candidate['sets'].items()))
    header = ' '.join([_ascii(need['need_id']), candidate['candidate_id'],
                       _ascii(candidate['window_id']), 'film' + str(candidate.get('film')),
                       format(candidate['span'][0], '.1f') + '-' + format(candidate['span'][1], '.1f'),
                       sets_text])
    tiles = [_tile(shot.get('keyframe'), 'shot n=' + str(shot.get('n', '?')) + ' ' +
                   format(shot.get('shot_start', 0), '.1f') + '-' + format(shot.get('shot_end', 0), '.1f'))
             for shot in shots[:4]] or [_tile('', 'no shots for span')]
    sheet = Image.new('RGB', (TILE_W * len(tiles), (TILE_H + 18) + 24), (16, 16, 16))
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(sheet)
    draw.text((4, TILE_H + 20), header[:190], fill=(240, 240, 240), font=ImageFont.load_default())
    for position, tile in enumerate(tiles):
        sheet.paste(tile, (position * TILE_W, 0))
    sheet.save(out_path, 'JPEG', quality=85)


def sheet_mode(args):
    from omni_story.library.film_cast import load_film_cast
    from omni_story.library.visual_library import VisualLibrary

    index = material_index.MaterialIndex.load(args.index)
    needs = json.loads(Path(args.needs).read_text(encoding='utf-8'))
    if not isinstance(needs, list):
        raise SystemExit('needs.json must be a list of need objects')
    out = Path(args.out)
    (out / 'sheets').mkdir(parents=True, exist_ok=True)

    text_embedder, _ = material_embed.try_text_embedder(
        args.text_model or index.meta.get('model_dirs', {}).get('text') or '', threads=args.threads)
    clip, _ = material_embed.try_clip(
        args.clip_model or index.meta.get('model_dirs', {}).get('image') or '', threads=args.threads)

    baseline_library, baseline_queries, cast = None, {}, None
    if args.baseline_library:
        baseline_library = VisualLibrary(args.baseline_library)
        if args.baseline_query_file:
            baseline_queries = _baseline_queries(args.baseline_query_file, needs)
    library_root = (index.meta.get('manifest') or {}).get('library_root')
    if library_root and Path(library_root, 'cast.json').is_file():
        try:
            cast = load_film_cast(library_root)
        except Exception as error:  # best effort: name canonicalisation only
            LOGGER.warning('eval_material_retrieval:cast_unavailable:%s', error)

    template, report_rows, available_sets = [], [], set()
    for need in needs:
        set_rankings, unavailable = {}, {}
        for set_name in SET_NAMES:
            rows, reason = run_set(index, need, set_name, text_embedder=text_embedder, clip=clip,
                                   baseline_library=baseline_library, cast=cast,
                                   baseline_query=baseline_queries.get(need.get('need_id'), ''))
            if reason:
                unavailable[set_name] = reason
            else:
                set_rankings[set_name] = rows
                available_sets.add(set_name)
        candidates = material_index.pool_candidates(set_rankings)
        for candidate in candidates:
            sheet_name = _ascii(need.get('need_id')) + '_' + candidate['candidate_id'] + '.jpg'
            write_contact_sheet(index, need, candidate, out / 'sheets' / sheet_name)
            template.append({'need_id': need.get('need_id'), 'beat': need.get('beat', ''),
                             'candidate_id': candidate['candidate_id'], 'window_id': candidate['window_id'],
                             'source_sha256': candidate['source_sha256'], 'film': candidate.get('film'),
                             'span': list(candidate['span']), 'sets': candidate['sets'],
                             'record_ids': candidate['record_ids'], 'sheet': 'sheets/' + sheet_name,
                             'label': None, 'notes': ''})
        report_rows.append({'need_id': need.get('need_id'), 'pooled': len(candidates),
                            'unavailable': unavailable})
        LOGGER.info('eval_material_retrieval:need:%s pooled=%d unavailable=%s',
                    need.get('need_id'), len(candidates), sorted(unavailable))
    write_json(out / 'labels_template.json',
               {'schema_version': material_index.SCHEMA_VERSION, 'sets': sorted(available_sets),
                'needs': template})
    write_json(out / 'sheet_report.json', {'rows': report_rows})
    print(json.dumps({'out': str(out), 'candidates': len(template),
                      'needs': len(report_rows)}, ensure_ascii=False))


def score_mode(args):
    payload = json.loads(Path(args.score).read_text(encoding='utf-8'))
    rows = payload['needs'] if isinstance(payload, dict) else payload
    labels_path = Path(args.score)
    out = Path(args.out) if args.out else labels_path.parent
    ranked_by_need, candidate_sets = {}, {}
    for row in rows:
        need_id, candidate_id = row['need_id'], row['candidate_id']
        candidate_sets.setdefault((need_id, candidate_id), set()).update(row.get('sets', {}))
        for set_name, rank in row.get('sets', {}).items():
            ranked_by_need.setdefault(need_id, {}).setdefault(set_name, []).append((rank, candidate_id))
    for ranking in ranked_by_need.values():
        for set_name, entries in ranking.items():
            ranking[set_name] = [candidate_id for _, candidate_id in sorted(entries)]
    metrics = material_index.compute_metrics(rows, ranked_by_need, candidate_sets)
    write_json(out / 'retrieval_metrics.json', metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--index', help='index directory from build_material_index.py')
    parser.add_argument('--needs', help='JSON list of need objects')
    parser.add_argument('--out', help='output directory for sheets/labels/metrics')
    parser.add_argument('--baseline-library', default=None, help='library root for the baseline set')
    parser.add_argument('--baseline-query-file', default=None,
                        help='recorded long query: {need_id: query} JSON or plain text')
    parser.add_argument('--text-model', default=None, help='override the build-time text model dir')
    parser.add_argument('--clip-model', default=None, help='override the build-time clip model dir')
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--score', default=None, help='labels.json to score (second mode)')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    if args.score:
        score_mode(args)
        return 0
    missing = [name for name, value in (('--index', args.index), ('--needs', args.needs),
                                        ('--out', args.out)) if not value]
    if missing:
        raise SystemExit('sheet mode requires ' + ' '.join(missing))
    sheet_mode(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
