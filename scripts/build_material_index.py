#!/usr/bin/env python3
"""Build the offline event-level material index (plan C phase 1).

Reads library observations, the film cast, optional entity names and the shot
cache; writes records.jsonl, bm25.json, text/image vector files and a manifest.
Incremental: vector channels skip any id whose content hash already matches the
previous run, so a second build re-encodes nothing. Missing model files (or
missing runtimes) disable the affected channel with a logged reason; the build
itself always completes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from omni_story.library import material_embed, material_index
from omni_story.library.film_cast import load_film_cast
from omni_story.library.state import LibraryStopped, write_json
from omni_story.library.visual_library import VisualLibrary

LOGGER = logging.getLogger('build_material_index')


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _parse_film_by_sha(value):
    """--film-by-sha accepts inline JSON or @file.json; values are film numbers."""
    if not value:
        return None
    raw = Path(value[1:]).read_text(encoding='utf-8') if value.startswith('@') else value
    mapping = json.loads(raw)
    if not isinstance(mapping, dict) or not all(isinstance(film, int) for film in mapping.values()):
        raise ValueError('film-by-sha must map source_sha256 -> film number')
    return mapping


def _channel_file_shas(model_dir, names):
    if not model_dir:
        return None
    directory = Path(model_dir)
    shas = {}
    for name in names:
        path = directory / name
        if path.is_file():
            shas[name] = _file_sha256(path)
    return shas or None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', required=True, help='visual library root (observations/, cast.json)')
    parser.add_argument('--shot-cache', required=True, help='directory containing film_N/shots.json')
    parser.add_argument('--out', required=True, help='output index directory')
    parser.add_argument('--text-model', default=None, help='BGE-small-zh ONNX directory (text channel)')
    parser.add_argument('--clip-model', default=None, help='Chinese-CLIP ONNX directory (image channel)')
    parser.add_argument('--entity-names', default=None, help='optional {entity_id: {name, evidence}} JSON')
    parser.add_argument('--film-by-sha', default=None, help='explicit {"<sha256>": film} JSON or @file.json')
    parser.add_argument('--threads', type=int, default=2, help='onnxruntime intra_op_num_threads')
    parser.add_argument('--text-batch', type=int, default=32)
    parser.add_argument('--image-batch', type=int, default=8)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
    started = time.time()
    timings, out = {}, Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cast_path = Path(args.library) / 'cast.json'
    cast = None
    if cast_path.is_file():
        try:
            cast = load_film_cast(args.library)
        except LibraryStopped as error:
            raise SystemExit('cast.json invalid: ' + str(error))
    else:
        LOGGER.warning('build_material_index:cast_missing:%s', cast_path)

    entity_names = {}
    if args.entity_names:
        entity_names = json.loads(Path(args.entity_names).read_text(encoding='utf-8'))

    shot_caches = material_index.load_shot_caches(args.shot_cache)
    film_by_sha = material_index.resolve_film_by_sha(shot_caches, _parse_film_by_sha(args.film_by_sha))
    shots = [shot for cache in shot_caches for shot in cache['shots']]
    for shot in shots:
        shot['keyframe_sha256'] = _file_sha256(shot['keyframe']) if shot['keyframe'] else ''

    step = time.time()
    library = VisualLibrary(args.library)
    records = material_index.build_records(library, cast=cast, entity_names=entity_names,
                                           film_by_sha=film_by_sha)
    timings['records_s'] = round(time.time() - step, 3)
    with open(out / material_index.RECORDS_NAME, 'w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    LOGGER.info('build_material_index:records:%d (events=%d ranges=%d)', len(records),
                sum(1 for r in records if r['kind'] == 'event'),
                sum(1 for r in records if r['kind'] == 'range'))

    step = time.time()
    bm25 = material_index.build_bm25(records)
    bm25.save(out / material_index.BM25_NAME)
    timings['bm25_s'] = round(time.time() - step, 3)

    channels, model_shas = {'text': {'enabled': False, 'reason': 'no_model_dir'},
                            'image': {'enabled': False, 'reason': 'no_model_dir'}}, {}
    text_state = {'encoded_ids': [], 'reused': 0, 'dim': 0}
    if args.text_model:
        embedder, reason = material_embed.try_text_embedder(
            args.text_model, threads=args.threads, batch_size=args.text_batch)
        model_shas['text'] = _channel_file_shas(args.text_model, (*material_embed.TextEmbedder.MODEL_FILES,
                                                                  'tokenizer.json'))
        if embedder is None:
            channels['text']['reason'] = reason
        else:
            step = time.time()
            text_state = material_index.build_text_vectors(
                records, embedder, material_index.read_text_channel(out), batch_size=args.text_batch)
            material_index.write_text_channel(out, text_state)
            timings['text_s'] = round(time.time() - step, 3)
            channels['text'] = {'enabled': True, 'reason': '',
                                'encoded': len(text_state['encoded_ids']), 'reused': text_state['reused'],
                                'dim': text_state['dim']}

    image_state = {'encoded_ids': [], 'reused': 0, 'missing': 0, 'dim': 0}
    if shots:
        step = time.time()
        shot_records = material_index.map_shots_to_records(records, shots)
        timings['shot_map_s'] = round(time.time() - step, 3)
        vectors, encoded_ids = None, []
        if args.clip_model:
            clip, reason = material_embed.try_clip(
                args.clip_model, threads=args.threads, batch_size=args.image_batch)
            model_shas['image'] = _channel_file_shas(
                args.clip_model, (*material_embed.ImageTextCLIP.MODEL_FILES, 'tokenizer.json',
                                  'preprocessor_config.json'))
            if clip is None:
                channels['image']['reason'] = reason
            else:
                step = time.time()
                previous = material_index.read_image_channel(out)
                image_state = material_index.build_image_vectors(
                    shots, clip, previous, batch_size=args.image_batch)
                vectors, encoded_ids = image_state['vectors'], image_state['encoded_ids']
                timings['image_s'] = round(time.time() - step, 3)
                channels['image'] = {'enabled': True, 'reason': '',
                                     'encoded': len(encoded_ids), 'reused': image_state['reused'],
                                     'missing': image_state['missing'], 'dim': image_state['dim']}
        # Vector rows align with the encoded (usable) shot list; without vectors
        # the full shot metadata still lands in image_shots.json for eval sheets.
        meta_shots = image_state['shots'] if vectors is not None else shots
        material_index.write_image_channel(out, meta_shots, shot_records, vectors, encoded_ids)

    observation_files = sorted(Path(library.records).glob('*.json'))
    manifest = {
        'schema_version': material_index.SCHEMA_VERSION,
        'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'library_root': str(Path(args.library).resolve()),
        'shot_cache': str(Path(args.shot_cache).resolve()),
        'entity_revision_ids': library.entity_revision_ids(),
        'input_shas': {
            'observations': {path.name: _file_sha256(path) for path in observation_files},
            'cast': _file_sha256(cast_path) if cast_path.is_file() else None,
            'entity_names': _file_sha256(args.entity_names) if args.entity_names else None,
            'shot_caches': {cache['path']: _file_sha256(cache['path']) for cache in shot_caches},
        },
        'model_shas': model_shas,
        'model_dirs': {'text': args.text_model, 'image': args.clip_model},
        'film_by_sha': film_by_sha,
        'counts': {
            'records': len(records),
            'events': sum(1 for record in records if record['kind'] == 'event'),
            'ranges': sum(1 for record in records if record['kind'] == 'range'),
            'shots': len(shots),
            'shots_encoded': len(image_state.get('encoded_ids') or []),
            'text_encoded': len(text_state.get('encoded_ids') or []),
        },
        'channels': channels,
        'timings_s': timings,
        'total_s': round(time.time() - started, 3),
        'peak_rss_kb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'argv': sys.argv[1:],
    }
    write_json(out / material_index.MANIFEST_NAME, manifest)
    LOGGER.info('build_material_index:done total=%ss peak_rss=%skb records=%d shots=%d',
                manifest['total_s'], manifest['peak_rss_kb'], len(records), len(shots))
    print(json.dumps({'out': str(out), 'counts': manifest['counts'], 'channels': channels,
                      'total_s': manifest['total_s']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
