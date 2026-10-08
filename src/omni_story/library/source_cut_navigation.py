"""All cached watched-proxy cut candidates, without semantic or creative choices."""
from pathlib import Path

from ..contract import require
from .media import sha256_file
from .pipeline import _read
from .shot_timeline import _identity, SHOT_TIMELINE_VERSION
from .state import json_sha

POLICY = 'all_watched_proxy_cut_navigation_v1'


def build(output, windows, input_lock, round_no):
    output = Path(output).resolve(strict=True)
    timelines = [(p, _read(p)) for p in (output / 'media_cache/shot_timelines').rglob('timeline.json')]
    records = []
    for w in windows:
        catalog_path = output / 'media_cache' / ('shot_catalog_' + w['window_id'].removeprefix('window_')) / 'inventory.json'
        source = _read(catalog_path)['sources'][0]
        proxy_path = Path(w['path']).resolve(strict=True)
        require(source['source_id'] == 'src_' + w['sha256'][:16]
                and Path(source['path']).resolve(strict=True) == proxy_path
                and source['sha256'] == w['sha256'] == sha256_file(proxy_path), 'cut_navigation:proxy_changed')
        matches = [(p, t) for p, t in timelines if t.get('source_sha256') == w['sha256']
                   and t.get('source_id') == source['source_id']
                   and Path(t['source_path']).resolve() == proxy_path
                   and t['spec'].get('threshold_percent') == 3]
        require(len(matches) == 1, 'cut_navigation:unique_existing_timeline_required')
        path, timeline = matches[0]
        require(all(timeline['spec'].get(k) == timeline.get(k)
                    for k in ('source_id', 'source_sha256', 'video_stream_index'))
                and timeline['source_id'] == source['source_id']
                and timeline['source_sha256'] == source['sha256']
                and timeline['video_stream_index'] == source['video_stream_index']
                and Path(timeline['spec']['source_path']).resolve(strict=True) == proxy_path
                and timeline['spec']['time_base'] == timeline['native_time_base'],
                'cut_navigation:detector_source_binding_changed')
        require(timeline['version'] == SHOT_TIMELINE_VERSION
                and timeline['input_identity'] == _identity(timeline['spec'])
                and path.parent.name == timeline['input_identity'][:20]
                and timeline['record_sha256'] == _identity({k: v for k, v in timeline.items() if k != 'record_sha256'}),
                'cut_navigation:detector_cache_changed')
        raw_path = Path(timeline['raw_metadata_path']).resolve(strict=True)
        require(raw_path == (path.parent / 'scdet_metadata.txt').resolve(strict=True)
                and sha256_file(raw_path) == timeline['raw_metadata_sha256'], 'cut_navigation:raw_detector_changed')
        lineage_path = proxy_path.parent / 'lineage.json'
        lineage = _read(lineage_path)
        require(all(lineage.get(k) == w.get(k) for k in
                    ('kind', 'source_sha256', 'source_start_s', 'source_end_s', 'source_offset_s')),
                'cut_navigation:proxy_lineage_changed')
        offset = w['source_offset_s']
        records.append({'window_id': w['window_id'], 'source_id': w['source_id'],
            'source_sha256': w['source_sha256'], 'observation_sha256': json_sha(w['observation']),
            'watched_source_range': [w['source_start_s'], w['source_end_s']],
            'source_offset_s': offset, 'mapping_tolerance_s': w['mapping_tolerance_s'],
            'proxy_sha256': w['sha256'], 'proxy_fps': lineage['spec']['fps'],
            'proxy_time_base': timeline['native_time_base'],
            'proxy_frame_count': timeline['score_summary']['frame_count'],
            'proxy_resolution': timeline['spec']['native_resolution'],
            'proxy_frame_duration_estimate_s': timeline['terminal_frame_duration_estimate_s'],
            'proxy_source_range': [timeline['source_start_s'], timeline['source_end_s']],
            'candidate_table_columns': ['source_time_s_estimate', 'score_percent'],
            'candidate_table': [[offset + c['source_time_s'], c['score_percent']] for c in timeline['cuts']],
            'candidate_status': 'scene_change_candidate_not_semantic_shot',
            'all_candidate_count': len(timeline['cuts']),
            'mechanical_intervals': 'Consecutive candidates with proxy endpoints define threshold-based intervals; not semantic shots.',
            'protected_files': [{'path': str(p), 'sha256': sha256_file(p)}
                for p in (catalog_path, path, raw_path, proxy_path, lineage_path)]})
    return {'policy': POLICY, 'round': round_no, 'input_lock_sha256': json_sha(input_lock),
        'window_ids': [w['window_id'] for w in windows], 'records': records,
        'all_windows_and_candidates_preserved': True, 'new_model_calls': 0, 'new_unique_windows': 0,
        'instruction': 'Use these ALL-window visual-change candidates to navigate possible microcuts. '
            'They are proxy-time estimates, not semantic actions, recommended slices, native film frame precision or verified results. '
            'You choose source in/out points and required information. Keep usable-range/role constraints. '
            'Independent exact-source and actual-output checks still follow.'}


def context(state, windows, round_no):
    name = f'goal_research_source_cuts_{round_no}'
    saved = state.data['artifacts'].get(name, [])
    if saved:
        require(len(saved) == 1, 'cut_navigation:multiple_snapshots')
        value = _read(saved[0]['path'])
        require(json_sha(value) == saved[0]['sha256'], 'cut_navigation:snapshot_changed')
    else:
        value = build(state.output, windows, state.data['input_lock'], round_no)
        state.set_artifact(name, value)
    require(value['policy'] == POLICY and value['round'] == round_no
            and value['input_lock_sha256'] == json_sha(state.data['input_lock'])
            and value['window_ids'] == [w['window_id'] for w in windows]
            and len(value['records']) == len(windows)
            and all(r['observation_sha256'] == json_sha(w['observation'])
                    and r['source_id'] == w['source_id'] and r['source_sha256'] == w['source_sha256']
                    and r['watched_source_range'] == [w['source_start_s'], w['source_end_s']]
                    and r['source_offset_s'] == w['source_offset_s']
                    and r['proxy_sha256'] == w['sha256']
                    and r['mapping_tolerance_s'] == w['mapping_tolerance_s']
                    for r, w in zip(value['records'], windows)),
            'cut_navigation:watched_window_binding_changed')
    return value


def prompt_view(value):
    """Keep every candidate, omit file-proof bookkeeping from the model's input."""
    return {'policy': value['policy'], 'snapshot_sha256': json_sha(value),
        'window_ids': value['window_ids'], 'instruction': value['instruction'],
        'records': [{k: v for k, v in record.items() if k != 'protected_files'}
                    for record in value['records']]}
