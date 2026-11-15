"""Bind one published collection, index and source catalog to a creative job."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .media import sha256_file
from .state import LibraryStopped, json_sha


def default_path(home):
    value = os.environ.get('OMNI_MATERIAL_PROFILE')
    path = Path(value) if value else Path(home)/'shared/data/active_material_profile.json'
    return path if path.is_file() else None


def load(path):
    """Resolve a publication pointer once; children receive the immutable path."""
    path = Path(path).resolve(strict=True)
    value = json.loads(path.read_text())
    if value.get('schema_version') == 'material_profile_pointer_v1':
        target = Path(value['profile_path']).resolve(strict=True)
        if sha256_file(target) != value['profile_sha256']:
            raise LibraryStopped('material_profile:pointer_changed')
        path, value = target, json.loads(target.read_text())
    if (value.get('schema_version') != 'material_profile_v1' or
            value.get('status') not in {'ready', 'ready_with_gaps', 'ready_partial'}):
        raise LibraryStopped('material_profile:not_published')
    for name in ('library', 'visual_library_root', 'material_index_root'):
        if not Path(value[name]).is_dir():
            raise LibraryStopped('material_profile:directory_missing:' + name)
    visual, index = Path(value['visual_library_root']), Path(value['material_index_root'])
    required = {str(p) for p in [visual/'cast.json', visual/'content_ranges.json',
        index/'manifest.json', index/'records.jsonl', index/'bm25.json', Path(value['source_catalog'])]}
    if not required <= set(value['files']):
        raise LibraryStopped('material_profile:required_file_binding_missing')
    for name, digest in value['files'].items():
        if not Path(name).is_file() or sha256_file(name) != digest:
            raise LibraryStopped('material_profile:published_file_changed:' + name)
    manifest = json.loads((Path(value['material_index_root'])/'manifest.json').read_text())
    if (Path(manifest['library_root']).resolve() != Path(value['visual_library_root']).resolve()
            or set(manifest['film_by_sha']) != set(value['source_sha256s'])
            or manifest['counts']['records'] < 1):
        raise LibraryStopped('material_profile:index_collection_mismatch')
    binding = {k: value[k] for k in ('snapshot_id', 'collection', 'source_sha256s',
        'source_visual_policy', 'review_exclusions_path')}
    binding.update(path=str(path), sha256=sha256_file(path))
    return value, binding


def apply_cli(args):
    path = getattr(args, 'material_profile', None)
    # Resumed work keeps the profile originally recorded in its input lock.
    prior = getattr(args, 'resume_from', None) or getattr(args, 'output', None)
    state = Path(prior)/'library_state.json' if prior else None
    pinned = None
    if state and state.is_file():
        pinned = json.loads(state.read_text())['input_lock']['configuration'].get('material_profile')
        if pinned and path is None:
            path = pinned['path']
    path = path or default_path(args.home)
    if path is None:
        return
    profile, binding = load(path)
    if pinned and binding != pinned:
        raise LibraryStopped('material_profile:resume_snapshot_changed')
    if state and state.is_file() and not pinned:
        raise LibraryStopped('material_profile:cannot_mix_historical_task_with_new_collection')
    for argument, field in (('library', 'library'), ('visual_library', 'visual_library_root'),
                            ('material_index', 'material_index_root')):
        previous = getattr(args, argument, None)
        target = Path(profile[field]).resolve()
        if previous is not None and Path(previous).resolve() != target:
            raise LibraryStopped('material_profile:conflicting_' + argument)
        setattr(args, argument, target)
    if (getattr(args, 'material_flow', None) not in (None, 'scene_v1') or
            getattr(args, 'material_search', 'library') != 'library' or
            getattr(args, 'creative_style', 'default') not in ('default', 'picture_aware')):
        raise LibraryStopped('material_profile:requires_scene_v1_picture_aware_library_search')
    args.creative_audio, args.creative_style, args.material_flow = True, 'picture_aware', 'scene_v1'
    args.material_profile, args._material_profile = Path(binding['path']), binding


def validate_catalog(binding, catalog):
    profile, checked = load(binding['path'])
    if checked != binding or set(binding['source_sha256s']) != {s['sha256'] for s in catalog['sources']}:
        raise LibraryStopped('material_profile:source_catalog_changed')
    return profile


def reviewed_exclusions(binding, index):
    """Later human exclusions also apply to an already published sample index."""
    path = Path(binding['review_exclusions_path'])
    if not path.is_file():
        return set()
    data = json.loads(path.read_text())
    if data.get('schema_version') != 'review_exclusions_v1':
        raise LibraryStopped('material_profile:review_exclusions_invalid')
    root = Path(index.meta['manifest']['library_root'])
    from .visual_library import VisualLibrary
    library = VisualLibrary(root)
    indexed = {r['observation_id'] for r in index.records}
    excluded = set()
    for row in data['observations']:
        ident = row['observation_id']
        if ident in indexed:
            record = library.get(ident)
            if json_sha(record) != row['record_sha256']:
                raise LibraryStopped('material_profile:review_binding_changed:' + ident)
            excluded.add(ident)
    return excluded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--query')
    parser.add_argument('--limit', type=int, default=5)
    args = parser.parse_args(argv)
    profile, binding = load(args.profile)
    result = dict(profile=binding, status=profile['status'], channels=profile['channels'])
    if args.query:
        from .material_index import MaterialIndex
        index = MaterialIndex.load(profile['material_index_root'])
        excluded = reviewed_exclusions(binding, index)
        if excluded:
            index = MaterialIndex([r for r in index.records if r['observation_id'] not in excluded], meta=index.meta)
        # This diagnostic endpoint makes no model calls and explicitly reports its channel.
        result['retrieval_channel'] = 'bm25'
        result['results'] = index.search(dict(need_id='query', query_text=args.query),
            channels=['bm25'], final_k=args.limit, top_windows=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
