from fractions import Fraction
import hashlib
import json

import av
from PIL import Image, ImageDraw
import pytest

from omni_story.library.microclip_frames import (
    extract_grid, extract_neighborhood, frame_catalog, verify_grid,
)


def make_video(path, timestamps=None, *, color=200):
    timestamps = timestamps or [5000 + i * 33 for i in range(70)]
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=30)
        stream.width, stream.height, stream.pix_fmt = 192, 96, 'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
        stream.options = {'x264-params': 'bframes=2:b-adapt=0', 'crf': '18'}
        for index, timestamp in enumerate(timestamps):
            image = Image.new('RGB', (192, 96), (20, 20, 20))
            ImageDraw.Draw(image).rectangle((index % 100, 10, index % 100 + 30, 80),
                                             fill=(color, 60, 20))
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = timestamp, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture
def vfr(tmp_path):
    return make_video(tmp_path / 'vfr.mkv', [5000, 5033, 5080, 5113, 5170,
                                           5220, 5280, 5350, 5410, 5500])


def test_real_nonzero_pts_and_bframe_presentation_order(vfr):
    with av.open(str(vfr)) as container:
        packet_pts = [p.pts for p in container.demux(video=0) if p.size]
    assert packet_pts != sorted(packet_pts)  # B packets really reorder, not just configured.
    catalog = frame_catalog(vfr)
    rows = catalog['frames']
    assert [r['pts'] for r in rows] == sorted(packet_pts)
    assert [r['decode_frame_index'] for r in rows] == list(range(10))
    assert catalog['source']['timeline_origin_s'] == 5
    assert [r['source_time_s'] for r in rows] == pytest.approx([0, .033, .08, .113, .17,
                                                            .22, .28, .35, .41, .5])
    assert rows[1]['frame_end_s'] == .08
    assert rows[1]['frame_end_basis'] == 'next_presentation_pts'
    assert rows[0]['pts_time_s'] == 5
    assert rows[-1]['frame_end_s'] == pytest.approx(.533)


def test_requested_seconds_are_distinct_from_real_pts_and_end_is_exclusive(vfr, tmp_path):
    result = extract_grid(vfr, .04, .35, tmp_path / 'grids', count=6, max_cell_width=160)
    assert result['sampling']['end_exclusive'] is True
    assert len(result['frames']) == 5  # .08,.113,.17,.22,.28 only; never duplicate to fill six.
    assert all(.04 <= r['source_time_s'] < .35 for r in result['frames'])
    assert any(r['requested_time_s'] != r['source_time_s'] for r in result['frames'])
    assert result['grid']['crop_box'] is None
    assert result['grid']['resize_only'] is True
    assert result['source']['width'] == 192
    for row in result['frames']:
        with Image.open(row['png_path']) as image:
            assert image.size == (192, 96)  # Full image, even when presentation is smaller.
    assert verify_grid(result) == result


def test_full_png_pixels_match_actual_decoder_and_grid_cache_is_immutable(vfr, tmp_path):
    result = extract_grid(vfr, 0, .5, tmp_path / 'grids')
    files = list((tmp_path / 'grids').rglob('*'))
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.is_file()}
    decoded = {}
    with av.open(str(vfr)) as container:
        for index, frame in enumerate(container.decode(video=0)):
            decoded[index] = frame.to_image().convert('RGB').tobytes()
    for row in result['frames']:
        with Image.open(row['png_path']) as image:
            assert image.convert('RGB').tobytes() == decoded[row['decode_frame_index']]
        assert row['decoded_rgb_sha256'] == hashlib.sha256(decoded[row['decode_frame_index']]).hexdigest()
    assert extract_grid(vfr, 0, .5, tmp_path / 'grids') == result
    after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.is_file()}
    assert after == before


def test_wrong_source_and_png_corruption_are_rejected(vfr, tmp_path):
    result = extract_grid(vfr, 0, .5, tmp_path / 'grids')
    other = make_video(tmp_path / 'other.mkv', color=30)
    with pytest.raises(ValueError, match='source_path'):
        verify_grid(result, source_path=other)
    source_bytes = vfr.read_bytes()
    vfr.write_bytes(source_bytes + b'changed')
    with pytest.raises(ValueError, match='source_sha256'):
        verify_grid(result)
    vfr.write_bytes(source_bytes)
    png = tmp_path / 'grids' / ('grid_' + result['request_sha256'][:20]) / (result['frames'][0]['frame_id'] + '.png')
    png.write_bytes(png.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='frame_png_sha256'):
        extract_grid(vfr, 0, .5, tmp_path / 'grids')


def test_timestamp_edit_cannot_be_hidden_by_rehashing_manifest(vfr, tmp_path):
    result = extract_grid(vfr, 0, .5, tmp_path / 'grids')
    manifest_path = tmp_path / 'grids' / ('grid_' + result['request_sha256'][:20]) / 'manifest.json'
    result['frames'][0]['source_time_s'] = .002
    raw = json.dumps(result).encode()
    manifest_path.write_bytes(raw)
    manifest_path.with_name('manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='frame_pts_mapping'):
        verify_grid(manifest_path)


def _rehash_manifest(result):
    from pathlib import Path
    path = Path(result['manifest_path'])
    raw = json.dumps(result).encode()
    path.write_bytes(raw)
    path.with_name('manifest.sha256').write_text(hashlib.sha256(raw).hexdigest())
    return path


def test_rehashed_substitute_png_still_must_match_real_source_pixels(vfr, tmp_path):
    result = extract_grid(vfr, 0, .5, tmp_path / 'grids')
    row = result['frames'][0]
    substitute = Image.new('RGB', (row['width'], row['height']), 'red')
    substitute.save(row['png_path'])
    from pathlib import Path
    row['png_sha256'] = hashlib.sha256(Path(row['png_path']).read_bytes()).hexdigest()
    row['decoded_rgb_sha256'] = hashlib.sha256(substitute.tobytes()).hexdigest()
    with pytest.raises(ValueError, match='decoded_source_pixels'):
        verify_grid(_rehash_manifest(result))


def test_rehashed_wrong_grid_still_must_present_actual_full_frames(vfr, tmp_path):
    result = extract_grid(vfr, 0, .5, tmp_path / 'grids')
    from pathlib import Path
    grid_path = Path(result['grid']['path'])
    with Image.open(grid_path) as current:
        changed = current.convert('RGB')
    ImageDraw.Draw(changed).rectangle((0, 46, 20, 66), fill='red')
    changed.save(grid_path)
    result['grid']['sha256'] = hashlib.sha256(grid_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='grid_frame_pixels'):
        verify_grid(_rehash_manifest(result))


def test_model_selected_neighborhood_uses_new_frames_and_narrows(tmp_path):
    source = make_video(tmp_path / 'longer.mkv')
    previous = extract_grid(source, 0, 2, tmp_path / 'grids')
    focal = previous['frames'][2]
    new = extract_neighborhood(previous, focal['frame_id'], tmp_path / 'grids', radius_s=.3)
    assert len(new['frames']) == 6
    assert {r['frame_id'] for r in new['frames']}.isdisjoint(r['frame_id'] for r in previous['frames'])
    assert new['request']['end_s'] - new['request']['start_s'] < 2
    assert new['request']['start_s'] == pytest.approx(focal['source_time_s'] - .3)
    assert verify_grid(new) == new
    with pytest.raises(ValueError, match='unknown_frame_id'):
        extract_neighborhood(previous, 'invented', tmp_path / 'grids')


@pytest.mark.parametrize('start,end,count', [(-1, 1, 6), (1, 1, 6), (0, float('nan'), 6),
                                           (True, 1, 6), (0, .5, 7), (0, .5, True)])
def test_invalid_bounds_and_count(vfr, tmp_path, start, end, count):
    with pytest.raises(ValueError):
        extract_grid(vfr, start, end, tmp_path / 'grids', count=count)


def test_interval_past_source_and_exhausted_observation_rejected(vfr, tmp_path):
    with pytest.raises(ValueError, match='interval_past_source_end'):
        extract_grid(vfr, 0, 1, tmp_path / 'grids')
    first = extract_grid(vfr, 0, .04, tmp_path / 'grids')
    with pytest.raises(ValueError, match='no_new_frames_in_interval'):
        extract_grid(vfr, 0, .04, tmp_path / 'grids',
                     exclude_frame_ids=[r['frame_id'] for r in first['frames']])


def test_publish_retries_only_bounded_windows_sharing_errors(tmp_path, monkeypatch):
    from omni_story.library import microclip_frames as mf
    source, destination = tmp_path / 'pending', tmp_path / 'complete'
    source.mkdir()
    (source / 'evidence').write_bytes(b'unchanged')
    original, attempts = mf.os.rename, []
    def rename(a, b):
        attempts.append((a, b))
        if len(attempts) < 3:
            error = PermissionError('synthetic transient sharing interruption')
            error.winerror = 5
            raise error
        return original(a, b)
    monkeypatch.setattr(mf.os, 'rename', rename)
    monkeypatch.setattr(mf.time, 'sleep', lambda _: None)
    assert mf._publish_grid(source, destination) is True
    assert len(attempts) == 3 and (destination / 'evidence').read_bytes() == b'unchanged'


def test_publish_persistent_error_keeps_forensic_partial(tmp_path, monkeypatch):
    from omni_story.library import microclip_frames as mf
    source, destination = tmp_path / 'pending', tmp_path / 'complete'
    source.mkdir()
    (source / 'evidence').write_bytes(b'preserved')
    attempts = []
    def rename(a, b):
        attempts.append((a, b))
        error = PermissionError('synthetic persistent access error')
        error.winerror = 5
        raise error
    monkeypatch.setattr(mf.os, 'rename', rename)
    monkeypatch.setattr(mf.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError): mf._publish_grid(source, destination)
    assert len(attempts) == 6 and not destination.exists()
    assert (source / 'evidence').read_bytes() == b'preserved'


def test_publish_competing_directory_is_never_overwritten(tmp_path):
    from omni_story.library.microclip_frames import _publish_grid
    source, destination = tmp_path / 'pending', tmp_path / 'complete'
    source.mkdir()
    destination.mkdir()
    (destination / 'evidence').write_bytes(b'existing')
    assert _publish_grid(source, destination) is False
    assert source.exists() and (destination / 'evidence').read_bytes() == b'existing'
