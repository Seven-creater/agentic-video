#!/usr/bin/env python3
"""Merge per-film entity naming runs into one entity_names.json for the material index.

A name is kept only when the entity's own library appearance text mentions one of
the character's species words (from a small data file), because crops are often
wide multi-character shots and the model then names the most prominent character
rather than the entity. Mismatches become status "conflict"; nothing is guessed.
"""
import argparse
import json
from pathlib import Path

from omni_story.library.visual_library import VisualLibrary


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def merge(runs, library, species):
    appearance = {}
    for entity in VisualLibrary(library).entities():
        texts = [str(row.get(key) or '') for row in entity['appearances'] for key in ('appearance', 'state')]
        appearance[entity['entity_id']] = ' '.join(texts)
    merged, counts = {}, {}
    for run in runs:
        for entity_id, row in _read(Path(run) / 'entity_names.json')['entities'].items():
            row = dict(row)
            if row.get('status') == 'named':
                words = species.get(row.get('name'), [])
                if words and not any(word in appearance.get(entity_id, '') for word in words):
                    row.update(status='conflict', conflict='appearance_species_mismatch',
                               species_words=words)
            previous = merged.get(entity_id)
            # Several runs may cover one entity (a stopped run's first film-2 grid):
            # a named answer wins over unclear/other; two different names conflict.
            if previous is None or previous.get('status') != 'named':
                merged[entity_id] = row
            elif row.get('status') == 'named' and row.get('name') != previous.get('name'):
                merged[entity_id] = dict(previous, status='conflict', conflict='runs_disagree',
                                         other_name=row.get('name'))
    for row in merged.values():
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {'policy': 'film_cast_identity_anchor_v1', 'species_check': True,
            'entities': merged, 'summary': counts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', required=True)
    parser.add_argument('--species', required=True, help='JSON {character name: [species words]}')
    parser.add_argument('--out', required=True)
    parser.add_argument('runs', nargs='+', help='entity naming output directories')
    args = parser.parse_args(argv)
    result = merge(args.runs, args.library, _read(args.species))
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(result['summary'], ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
