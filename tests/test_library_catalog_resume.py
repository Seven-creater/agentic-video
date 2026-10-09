"""Inventory and resume share media filtering; source verification stays strict."""
import json
import os
from pathlib import Path

import pytest

from omni_story.library import media, pipeline
from omni_story.library.state import LibraryStopped


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    library = tmp_path / 'library'
    library.mkdir()
    movies = [library / 'one.mp4', library / 'two.MKV']
    for index, path in enumerate(movies):
        path.write_bytes(('synthetic video ' + str(index)).encode())
    (library / '.upload.lock').write_bytes(b'owned upload control\n')
    (library / 'upload.json').write_bytes(b'{"status":"SHA-verified"}\n')
    (library / 'directory.mp4').mkdir()
    probes = []

    def probe(path):
        probes.append(Path(path))
        return {'duration_s': 2.0, 'video_stream_index': 0,
                'streams': [{'index': 0, 'codec_type': 'video'}],
                'format': {'duration': '2.0'}}

    monkeypatch.setattr(media, 'probe_media', probe)
    output = tmp_path / 'task/catalog'
    result = pipeline._catalog(library, output)
    catalog_path = output / 'inventory.json'
    input_path = output.parent / 'library_state.json'
    input_lock = {'reference_sha256': 'a' * 64,
                  'library_sources': [{'source_id': source['source_id'], 'sha256': source['sha256']}
                                      for source in result['sources']]}
    input_path.write_text(json.dumps({'input_lock': input_lock, 'calls': [], 'request_count': 0}),
                          encoding='utf-8')
    assert set(probes) == set(movies)
    assert {source['path'] for source in result['sources']} == {str(path) for path in movies}
    return library, movies, output, result, catalog_path.read_bytes(), input_path.read_bytes(), probes


def assert_records_unchanged(catalog):
    _, _, output, _, original_catalog, original_input, _ = catalog
    assert (output / 'inventory.json').read_bytes() == original_catalog
    assert (output.parent / 'library_state.json').read_bytes() == original_input


@pytest.mark.parametrize('change', ['unchanged', 'new_lock', 'new_json', 'changed_lock',
                                  'changed_json', 'deleted_lock', 'deleted_json', 'media_directory'])
def test_non_media_sidecars_and_directories_are_ignored_on_resume(catalog, change):
    library, movies, output, original, _, _, probes = catalog
    if change == 'new_lock':
        (library / 'one.mp4.upload.lock').write_bytes(b'new sidecar')
    elif change == 'new_json':
        (library / 'one.mp4.json').write_bytes(b'{"new":"metadata"}')
    elif change in {'changed_lock', 'changed_json'}:
        (library / ('.upload.lock' if change == 'changed_lock' else 'upload.json')).write_bytes(b'changed sidecar')
    elif change in {'deleted_lock', 'deleted_json'}:
        (library / ('.upload.lock' if change == 'deleted_lock' else 'upload.json')).unlink()
    elif change == 'media_directory':
        (library / 'new-film.mkv').mkdir()
    movie_bytes = [path.read_bytes() for path in movies]
    assert pipeline._catalog(library, output) == original
    assert len(probes) == 2  # Cached resume verifies source stat without reinventory/probe.
    assert [path.read_bytes() for path in movies] == movie_bytes
    assert_records_unchanged(catalog)


def test_same_name_same_bytes_changed_mtime_is_rejected(catalog):
    library, movies, output, _, _, _, _ = catalog
    movie = movies[0]
    original_bytes = movie.read_bytes()
    old = movie.stat()
    os.utime(movie, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
    assert movie.stat().st_mtime_ns != old.st_mtime_ns
    with pytest.raises(ValueError, match='source_changed_since_inventory'):
        pipeline._catalog(library, output)
    assert movie.read_bytes() == original_bytes
    assert_records_unchanged(catalog)


@pytest.mark.parametrize('extension', sorted(media.MEDIA_EXTENSIONS))
def test_new_real_media_extension_is_rejected_without_rewriting_catalog(catalog, extension):
    library, _, output, _, _, _, probes = catalog
    (library / ('new-film' + extension.upper())).write_bytes(b'new genuine media candidate')
    with pytest.raises(LibraryStopped, match='library_file_set_changed'):
        pipeline._catalog(library, output)
    assert len(probes) == 2
    assert_records_unchanged(catalog)


def test_deleted_real_media_is_rejected_without_rewriting_catalog(catalog):
    library, movies, output, _, _, _, _ = catalog
    movies[0].unlink()
    with pytest.raises(LibraryStopped, match='library_file_set_changed'):
        pipeline._catalog(library, output)
    assert_records_unchanged(catalog)


def test_single_media_file_resume_retains_its_locked_inventory(catalog):
    _, movies, output, _, _, _, probes = catalog
    single = output.parent / 'reference_catalog'
    original = pipeline._catalog(movies[0], single)
    before = (single / 'inventory.json').read_bytes()
    assert pipeline._catalog(movies[0], single) == original
    assert (single / 'inventory.json').read_bytes() == before
    assert len(probes) == 3
    assert_records_unchanged(catalog)
