"""Read-only cached proxy navigation, not film interpretation or cut selection."""
from copy import deepcopy
from pathlib import Path
import pytest

from omni_story.library import source_cut_navigation as navigation
from omni_story.library.media import sha256_file
from omni_story.library.shot_timeline import _identity, SHOT_TIMELINE_VERSION
from omni_story.library.state import write_json, json_sha


def cached_window(output, suffix, offset):
    proxy = output / suffix / 'window.mp4'
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b'synthetic cached proxy ' + suffix.encode())
    digest = sha256_file(proxy)
    w = {'window_id': 'window_' + suffix, 'path': str(proxy.resolve()), 'sha256': digest,
        'kind': 'continuous_window', 'source_id': 'original_movie', 'source_sha256': 'a' * 64,
        'source_start_s': offset, 'source_end_s': offset + 6, 'source_offset_s': offset,
        'mapping_tolerance_s': .1, 'spec': {'fps': 12}, 'observation': {'synthetic': suffix}}
    write_json(proxy.parent / 'lineage.json', w)
    source = {'source_id': 'src_' + digest[:16], 'path': str(proxy.resolve()), 'sha256': digest,
              'source_sha256': digest, 'video_stream_index': 0}
    write_json(output / 'media_cache' / ('shot_catalog_' + suffix) / 'inventory.json', {'sources': [source]})
    spec = {'threshold_percent': 3, 'native_resolution': [720, 406], 'version': SHOT_TIMELINE_VERSION,
            'time_base': '1/12288', 'video_stream_index': 0}
    identity = _identity(spec)
    folder = output / 'media_cache/shot_timelines' / identity[:20]
    # Source identity belongs in spec as in production; each proxy has its own cache.
    spec.update(source_sha256=digest, source_path=str(proxy.resolve()), source_id=source['source_id'])
    identity = _identity(spec)
    folder = output / 'media_cache/shot_timelines' / identity[:20]
    folder.mkdir(parents=True)
    raw = folder / 'scdet_metadata.txt'
    raw.write_text('synthetic original detector record', encoding='utf-8')
    record = {'version': SHOT_TIMELINE_VERSION, 'spec': spec, 'input_identity': identity,
        'source_sha256': digest, 'source_id': source['source_id'], 'source_path': str(proxy.resolve()),
        'video_stream_index': 0,
        'native_time_base': '1/12288', 'score_summary': {'frame_count': 72},
        'terminal_frame_duration_estimate_s': 1 / 12, 'source_start_s': 0, 'source_end_s': 6,
        'cuts': [{'source_time_s': 1.25, 'score_percent': 9}, {'source_time_s': 4.5, 'score_percent': 4}],
        'raw_metadata_path': str(raw.resolve()), 'raw_metadata_sha256': sha256_file(raw)}
    record['record_sha256'] = _identity(record)
    write_json(folder / 'timeline.json', record)
    return w, folder / 'timeline.json'


def test_all_windows_candidates_and_original_order_preserved_without_writes(tmp_path):
    w1, _ = cached_window(tmp_path, 'first', 100)
    w2, _ = cached_window(tmp_path, 'second', 250)
    windows = [w2, w1]
    untouched = deepcopy(windows)
    files = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    value = navigation.build(tmp_path, windows, {'reference_sha256': 'b' * 64}, 7)
    assert value['window_ids'] == ['window_second', 'window_first']
    assert value['records'][0]['candidate_table'] == [[251.25, 9], [254.5, 4]]
    assert value['records'][1]['candidate_table'] == [[101.25, 9], [104.5, 4]]
    assert value['new_model_calls'] == value['new_unique_windows'] == 0
    assert windows == untouched
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == files
    view = navigation.prompt_view(value)
    assert view['snapshot_sha256'] == json_sha(value)
    assert 'protected_files' not in view['records'][0]
    assert view['records'][0]['candidate_table'] == value['records'][0]['candidate_table']


@pytest.mark.parametrize('change', ['proxy', 'raw', 'manifest', 'lineage'])
def test_changed_source_or_detector_history_rejected_without_regeneration(tmp_path, change):
    w, path = cached_window(tmp_path, 'first', 100)
    if change == 'proxy':
        Path(w['path']).write_bytes(b'changed proxy')
    elif change == 'raw':
        (path.parent / 'scdet_metadata.txt').write_text('changed raw', encoding='utf-8')
    elif change == 'manifest':
        import json
        data = json.loads(path.read_text(encoding='utf-8'))
        data['cuts'][0]['source_time_s'] = 2
        write_json(path, data)
    else:
        write_json(Path(w['path']).parent / 'lineage.json', {**w, 'source_offset_s': 99})
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='cut_navigation:'):
        navigation.build(tmp_path, [w], {}, 7)
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before
