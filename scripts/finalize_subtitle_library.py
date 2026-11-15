#!/usr/bin/env python3
"""Publish a fixed collection snapshot while observing, or after the full run."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from omni_story.library.state import file_lock, write_json
from omni_story.library.media import sha256_file
from omni_story.library.visual_library import VisualLibrary
from omni_story.library.pipeline import _received_library_window


def read(path):
    return json.loads(Path(path).read_text())


def snapshot_library(root):
    """Copy small immutable annotations, never the movie or uploaded proxies."""
    version = time.strftime('%Y%m%d_%H%M%S', time.gmtime())+'_'+uuid.uuid4().hex[:8]
    publication = root/'publications'/version
    target = publication/'library'
    (target/'observations').mkdir(parents=True)
    with file_lock(root/'.write.lock', timeout_s=30):
        for directory in ('observations', 'revisions'):
            if (root/directory).is_dir():
                shutil.copytree(root/directory, target/directory, dirs_exist_ok=True)
        if (root/'review_exclusions.json').is_file():
            shutil.copy2(root/'review_exclusions.json', target/'review_exclusions.json')
    return version, publication, target


def unobserved_ranges(windows, source):
    intervals = sorted((w['source_start_s'], w['source_end_s']) for w in windows
                       if w['source_sha256'] == source['sha256'])
    gaps, end = [], 0
    for a,b in intervals:
        if a > end:
            gaps.append((end,a))
        end = max(end,b)
    if end < source['duration_s']:
        gaps.append((end,source['duration_s']))
    return gaps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--run', type=Path, help='Defaults to this collection\'s active build.')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--partial', action='store_true',
        help='Publish received samples now with BM25; full vector indexing follows when the build finishes.')
    parser.add_argument('--no-activate', action='store_true', help='Prepare and verify a candidate before switching production.')
    args = parser.parse_args()
    root, home = args.root, args.home
    run = args.run or Path(read(root/'active_build.json')['run'])
    build_root = root
    status = root/('preview_publication_status.json' if args.partial else 'publication_status.json')
    write_json(status, dict(status='waiting_for_observations', updated_at=time.time()))
    while not args.partial:
        result = run/'result.json'
        worker = read(run/'worker.json') if (run/'worker.json').exists() else {}
        if result.exists() and worker.get('status') == 'completed':
            break
        write_json(status, dict(status='observation_stopped' if worker.get('status') == 'stopped'
            else 'waiting_for_observations', updated_at=time.time(), worker_status=worker.get('status')))
        time.sleep(30)
    write_json(status, dict(status='building_retrieval_assets', updated_at=time.time()))
    snapshot_id, publication, root = snapshot_library(build_root)
    library = VisualLibrary(root)
    library.rebuild()  # Recompute all derived cards with the final projection policy.
    catalog = read(run/'source_catalog/inventory.json')
    build_result = read(run/'result.json') if (run/'result.json').is_file() else {}
    if args.partial and (run/'control.json').is_file():
        build_result['excluded_ranges'] = [dict(**row['scope'],reason=row['reason'])
            for row in read(run/'control.json').get('excluded_ranges',[])]
    records = [read(path) for path in sorted(library.records.glob('*.json'))]
    source_map = {s['sha256']: s for s in catalog['sources']}
    verification = []
    write_json(status, dict(status='checking_reuse_dependencies',updated_at=time.time(),total=len(records)))
    for record in records:
        try:
            _received_library_window(record,root,
                source=source_map[record['window']['source_sha256']],normalize=True)
        except Exception as error:
            verification.append(dict(observation_id=record['observation_id'],status='failed',
                error_type=type(error).__name__,error=str(error)[:1000]))
            write_json(root/'publication_verification.json',dict(status='failed',records=verification))
            raise
        verification.append(dict(observation_id=record['observation_id'],status='reusable'))
        if len(verification)%50 == 0:
            write_json(status,dict(status='checking_reuse_dependencies',updated_at=time.time(),
                checked=len(verification),total=len(records)))
    write_json(root/'publication_verification.json',dict(status='passed',records=verification,
        scope='media_and_received_evidence_reuse_not_semantic_accuracy',updated_at=time.time()))
    entities, names, characters = library.entities(), {}, []
    for entity in entities:
        approved = [ref for ref in entity['exemplars'] if ref.get('reference_check', {}).get('approved') is True]
        if not approved:
            continue
        ident = entity['entity_id']
        label = '角色_' + hashlib.sha256(ident.encode()).hexdigest()[:10]
        names[ident] = dict(name=label, status='named', name_kind='visual_identity_label_not_original_name',
            appearance=approved[-1]['reference_check']['visible_appearance'],
            evidence=approved[-1]['reference_check']['evidence'])
        characters.append(dict(name=label, aliases=[], films=[4]))
    if not characters:
        raise RuntimeError('no_confirmed_visual_character_anchors')
    write_json(root/'cast.json', dict(schema_version='collection_cast_v2', collection='chiikawa',
        characters=characters, stylized_ranges={}, identity_policy='verified_visual_appearance_labels'))
    write_json(root/'entity_names.json', dict(entities=names))
    reviewed_exclusions = library.quality_excluded_ids()
    windows = [library.effective_window(record) for record in records
               if record['observation_id'] not in reviewed_exclusions]
    windows.sort(key=lambda w: (w['source_sha256'], w['source_start_s']))
    content = {}
    for source in catalog['sources']:
        exclusions = []
        # Model title/credit guesses remain on retrieval cards. Hard exclusions
        # here are reserved for actual observation gaps and quarantined inputs.
        content[source['sha256']] = dict(content_start_s=0,content_end_s=source['duration_s'],
            verified_by='observation_coverage_only_requires_selected_scene_review',
            allow_internal_exclusions=True,excluded=exclusions)
        for a,b in unobserved_ranges(windows, source):
            exclusions.append(dict(start_s=a,end_s=b,kind='unobserved',
                reason='not_available_as_observed_material'))
        for rejected in build_result.get('excluded_ranges', []):
            if rejected.get('source_sha256') == source['sha256']:
                exclusions.append(dict(start_s=rejected['source_start_s'],end_s=rejected['source_end_s'],
                    kind='unobserved',reason=rejected['reason']))
    write_json(root/'content_ranges.json', dict(schema_version='content_ranges_v2', sources=content))
    # A few representative usable intervals per observation; no full second copy.
    frame_root = root/'retrieval_frames/film_4'
    frame_root.mkdir(parents=True, exist_ok=True)
    frames = []
    for window in ([] if args.partial else windows):
        spans = window['observation'].get('usable_ranges', [])
        from omni_story.library.subtitle_library import rejected_visual_evidence
        rejected_ranges = rejected_visual_evidence(window)[2]
        available = [i for i in range(len(spans)) if i not in rejected_ranges]
        indices = sorted({available[0],available[len(available)//2],available[-1]}) if available else []
        for index in indices:
            span = spans[index]
            a,b = span['local_in_s'],span['local_out_s']
            point=(a+b)/2
            image=frame_root/(window['window_id']+f'_{index}.jpg')
            if not image.exists():
                subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','2','-ss',str(point),
                    '-i',window['path'],'-vf','crop=iw:ih-32:0:32,scale=480:-2',
                    '-frames:v','1',str(image)],check=True,capture_output=True)
            frames.append(dict(n=len(frames)+1,timestamp=window['source_start_s']+point,
                shot_start=window['source_start_s']+a,shot_end=window['source_start_s']+b,
                keyframe=str(image),interval_kind='model_usable_range_not_native_shot'))
    source=catalog['sources'][0]
    write_json(frame_root/'shots.json', dict(source_sha256=source['sha256'],
        info=dict(film=4,title='Chiikawa',duration=source['duration_s']),frames=frames))
    index=root/'material_index'
    command = [sys.executable,str(Path(__file__).with_name('build_material_index.py')),
        '--library',str(root),'--shot-cache',str(root/'retrieval_frames'),'--out',str(index),
        '--entity-names',str(root/'entity_names.json'),'--film-by-sha',json.dumps({source['sha256']:4}),
        '--threads','2']
    if not args.partial:
        models=read(home/'shared/data/material_index_v1/index/manifest.json')['model_dirs']
        command += ['--text-model',models['text'],'--clip-model',models['image']]
    subprocess.run(command,check=True)
    manifest=read(index/'manifest.json')
    catalog_path = publication/'source_catalog.json'
    write_json(catalog_path,catalog)
    state = 'ready_partial' if args.partial else 'ready_with_gaps' if build_result.get('excluded_ranges') or reviewed_exclusions else 'ready'
    files = [root/'cast.json', root/'entity_names.json', root/'content_ranges.json', catalog_path,
             *(p for p in index.iterdir() if p.is_file())]
    profile_path = publication/'production_profile.json'
    write_json(profile_path, dict(schema_version='material_profile_v1',status=state,
        snapshot_id=snapshot_id,collection='chiikawa',
        source_sha256s=sorted(source_map),source_catalog=str(catalog_path),
        review_exclusions_path=str(build_root/'review_exclusions.json'),
        files={str(p):sha256_file(p) for p in files},channels=manifest['channels'],
        observations=len(windows),library=str(home/'shared/data/videos_library_02'),
        visual_library_root=str(root),material_index_root=str(index),material_search='library',
        material_flow='scene_v1',creative_audio=True,creative_style='picture_aware',
        source_visual_policy='silent_subtitle_visible_v1',source_subtitle_treatment='local_translucent_mosaic',
        release=str(Path(__file__).resolve().parents[1])))
    from omni_story.library.material_profile import load
    load(profile_path)  # Verify the complete candidate before switching either pointer.
    pointer = dict(schema_version='material_profile_pointer_v1',profile_path=str(profile_path),
                   profile_sha256=sha256_file(profile_path))
    if not args.no_activate:
        write_json(build_root/'production_profile.json',pointer)
        write_json(home/'shared/data/active_material_profile.json',pointer)
    write_json(status, dict(status=state,profile_path=str(profile_path),snapshot_id=snapshot_id,
        excluded_ranges=build_result.get('excluded_ranges',[]),updated_at=time.time(),observations=len(windows),
        reviewed_excluded_observation_ids=sorted(reviewed_exclusions),
        visual_identities=len(characters),retrieval=manifest['counts'],channels=manifest['channels']))


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        if '--root' in sys.argv:
            root = Path(sys.argv[sys.argv.index('--root')+1])
            write_json(root/('preview_publication_status.json' if '--partial' in sys.argv else 'publication_status.json'), dict(status='failed',
                error_type=type(error).__name__,error=str(error)[:1000],updated_at=time.time()))
        raise
