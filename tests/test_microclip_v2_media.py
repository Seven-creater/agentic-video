from fractions import Fraction
from pathlib import Path

import av
from PIL import Image, ImageDraw
import pytest

from omni_story.library.microclip_frames import frame_catalog, verify_grid
from omni_story.library.microclip_v2_media import anchor_grid, dense_pages


def make_video(path, timestamps):
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=30)
        stream.width, stream.height, stream.pix_fmt = 96, 64, 'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
        stream.options = {'x264-params': 'bframes=2:b-adapt=0', 'crf': '18'}
        for index, timestamp in enumerate(timestamps):
            image = Image.new('RGB', (96, 64), (20, 20, 20))
            ImageDraw.Draw(image).rectangle((index % 60, 10, index % 60 + 20, 50),
                                             fill=(200, 60, 20))
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


def test_anchor_present_with_consecutive_true_pts_neighbours(vfr, tmp_path):
    rows = frame_catalog(vfr)['frames']
    result = anchor_grid(vfr, {'start_s': 0, 'end_s': .533}, rows[4]['frame_id'],
                         tmp_path / 'anchor')
    assert [r['frame_id'] for r in result['frames']] == [r['frame_id'] for r in rows[2:8]]
    assert result['request']['exclude_frame_ids'] == []
    assert [r['source_time_s'] for r in result['frames']] == pytest.approx(
        [.08, .113, .17, .22, .28, .35])
    assert result['grid']['crop_box'] is None
    for row in result['frames']:
        with Image.open(row['png_path']) as image:
            assert image.size == (96, 64)
    assert verify_grid(result) == result


@pytest.mark.parametrize('anchor_index,expected_indices', [(0, list(range(6))),
                                                          (9, list(range(4, 10)))])
def test_anchor_at_source_edge_shifts_without_omitting_anchor(vfr, tmp_path,
                                                             anchor_index, expected_indices):
    rows = frame_catalog(vfr)['frames']
    result = anchor_grid(vfr, {'start_s': 0, 'end_s': .533}, rows[anchor_index]['frame_id'],
                         tmp_path / 'anchor')
    assert [r['decode_frame_index'] for r in result['frames']] == expected_indices


def test_anchor_stays_inside_slot_and_rejects_outside_id(vfr, tmp_path):
    rows = frame_catalog(vfr)['frames']
    slot = {'start_s': .17, 'end_s': .35}
    result = anchor_grid(vfr, slot, rows[5]['frame_id'], tmp_path / 'anchor')
    assert [r['decode_frame_index'] for r in result['frames']] == [4, 5, 6]
    with pytest.raises(ValueError, match='unknown_anchor_frame_id'):
        anchor_grid(vfr, slot, rows[7]['frame_id'], tmp_path / 'other')


def test_dense_short_vfr_shows_every_frame_and_validates_immutable_cache(vfr, tmp_path):
    output = tmp_path / 'dense'
    result = dense_pages(vfr, .033, .533, output)
    rows = [r for p in result['pages'] for r in p['frames']]
    assert [r['decode_frame_index'] for r in rows] == list(range(1, 10))
    assert result['coverage']['all_decoded_frames_shown'] is True
    assert result['coverage']['max_sample_gap_s'] == pytest.approx(.09)
    assert result['coverage']['target_gap_met'] is True
    assert len(result['pages']) == 2
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in output.rglob('*') if p.is_file()}
    assert dense_pages(vfr, .033, .533, output) == result
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in output.rglob('*') if p.is_file()} == before
    png = Path(result['pages'][0]['frames'][0]['png_path'])
    png.write_bytes(png.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='frame_png_sha256'):
        dense_pages(vfr, .033, .533, output)


def test_dense_longer_clip_meets_quarter_second_gap_when_budget_permits(tmp_path):
    source = make_video(tmp_path / 'long.mkv', [5000 + round(i * 1000 / 30) for i in range(390)])
    catalog = frame_catalog(source)
    result = dense_pages(source, 0, catalog['source']['source_end_s'], tmp_path / 'dense')
    coverage = result['coverage']
    rows = [r for p in result['pages'] for r in p['frames']]
    assert coverage['target_gap_met'] is True
    assert coverage['max_sample_gap_s'] <= .25 + 1e-9
    assert coverage['all_decoded_frames_shown'] is False
    assert coverage['selected_frame_count'] <= 60
    assert len(result['pages']) <= 10
    assert rows[0]['frame_id'] == catalog['frames'][0]['frame_id']
    assert rows[-1]['frame_id'] == catalog['frames'][-1]['frame_id']
    assert all(1 <= len(page['frames']) <= 6 for page in result['pages'])
    assert all(page['request']['exclude_frame_ids'] == [] for page in result['pages'])
    assert all(row['source_sha256'] == coverage['source_sha256'] for row in rows)


def test_page_cap_widens_gap_with_explicit_limitation(tmp_path):
    source = make_video(tmp_path / 'long.mkv', [5000 + round(i * 1000 / 30) for i in range(450)])
    catalog = frame_catalog(source)
    result = dense_pages(source, 0, catalog['source']['source_end_s'], tmp_path / 'dense', max_pages=2)
    coverage = result['coverage']
    assert coverage['selected_frame_count'] == 12
    assert coverage['page_count'] == 2
    assert coverage['target_gap_met'] is False
    assert coverage['max_sample_gap_s'] > .25
    assert any('page limit' in text for text in coverage['limitations'])
    assert coverage['first_frame_id'] == catalog['frames'][0]['frame_id']
    assert coverage['last_frame_id'] == catalog['frames'][-1]['frame_id']


def test_source_vfr_gap_is_reported_even_when_all_frames_are_shown(tmp_path):
    source = make_video(tmp_path / 'sparse.mkv', [5000, 5033, 5080, 6000])
    catalog = frame_catalog(source)
    result = dense_pages(source, 0, catalog['source']['source_end_s'], tmp_path / 'dense')
    coverage = result['coverage']
    assert coverage['all_decoded_frames_shown'] is True
    assert coverage['target_gap_met'] is False
    assert coverage['max_sample_gap_s'] == pytest.approx(.92)
    assert any('decoded source' in text for text in coverage['limitations'])
    assert not any('page limit' in text for text in coverage['limitations'])


def test_ffprobe_six_decimal_duration_rounding_keeps_real_frame_end(tmp_path):
    source = tmp_path / 'exact_rate.mp4'
    with av.open(str(source), 'w') as container:
        stream = container.add_stream('libx264', rate=30)
        stream.width, stream.height, stream.pix_fmt = 96, 64, 'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1, 30)
        for index in range(50):
            frame = av.VideoFrame.from_image(Image.new('RGB', (96, 64), (index, 40, 80)))
            frame.pts, frame.time_base = index, Fraction(1, 30)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    catalog = frame_catalog(source)
    actual_end = catalog['source']['source_end_s']
    assert actual_end == pytest.approx(50 / 30)
    rounded_end = float(f'{actual_end:.6f}')
    assert rounded_end == 1.666667 and rounded_end > actual_end
    result = dense_pages(source, 0, rounded_end, tmp_path / 'dense')
    assert result['coverage']['end_s'] == rounded_end
    assert result['coverage']['last_observed_frame_end_s'] == actual_end
    assert result['coverage']['selected_frame_count'] == 50
    assert all(page['request']['end_s'] <= actual_end for page in result['pages'])
    with pytest.raises(ValueError, match='interval_past_source_end'):
        dense_pages(source, 0, actual_end + 2e-6, tmp_path / 'outside')


@pytest.mark.parametrize('max_pages', [0, 11, True, 1.5])
def test_invalid_page_limit_rejected(vfr, tmp_path, max_pages):
    with pytest.raises(ValueError, match='max_pages'):
        dense_pages(vfr, 0, .5, tmp_path / 'dense', max_pages=max_pages)


@pytest.mark.parametrize('start,end', [(-1, .5), (0, 1), (True, .5), (.35, .35)])
def test_invalid_range_rejected(vfr, tmp_path, start, end):
    with pytest.raises(ValueError):
        dense_pages(vfr, start, end, tmp_path / 'dense')
