#!/usr/bin/env python3
"""Print/persist per-film bars: PYTHONPATH=src python scripts/check_subtitle_bars.py --shot-cache ROOT [--film VIDEO ...] [--measure]."""
import argparse
import json
from pathlib import Path

from omni_story.library.media import sha256_file
from omni_story.library.state import write_json
from omni_story.library.subtitle_bar import film_subtitle_bar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shot-cache', type=Path, required=True)
    parser.add_argument('--film', action='append', type=Path, help='Repeat for relocated videos; default: all cached films')
    parser.add_argument('--measure', action='store_true', help='Measure full-width frame columns even for verified library encodes')
    parser.add_argument('--write-cache', action='store_true', help='Store in shots.json before a new task pins its hash')
    args = parser.parse_args()
    paths = list(args.shot_cache.glob('film_*/shots.json'))
    if not paths:
        parser.error('no film_*/shots.json in shot cache')
    overrides = {sha256_file(p): str(p.resolve()) for p in args.film or []}
    for path in sorted(paths):
        saved = json.loads(path.read_text(encoding='utf-8'))
        info = saved['info']
        sha = saved.get('source_sha256') or info.get('source_sha256')
        video = overrides.get(sha, info['path'])
        if sha is None:
            sha = sha256_file(video)
            video = overrides.get(sha, video)
        source = dict(path=video, sha256=sha)
        bar = film_subtitle_bar(source, info, measure=args.measure)
        if args.write_cache:
            info['subtitle_bar'] = bar
            # Explicit cache preparation, before the task pins shots.json's hash.
            write_json(path, saved)
        print(json.dumps(dict(film=info['film'], title=info['title'], **bar), ensure_ascii=False))


if __name__ == '__main__':
    main()
