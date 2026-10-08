from fractions import Fraction
from pathlib import Path

import av
from PIL import Image
import pytest

from omni_story.library.microclip_frames import frame_catalog, verify_grid
from omni_story.library.microclip_v2_boundary_media import boundary_pages
from omni_story.library.microclip_v2_media import anchor_grid


def make_video(path, *, rate=30, frames=120, cut_index=66):
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=rate)
        stream.width, stream.height, stream.pix_fmt = 96, 64, 'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1, rate)
        for index in range(frames):
            image = Image.new('RGB', (96, 64), (210, 20, 20) if index < cut_index else (20, 20, 210))
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = index, Fraction(1, rate)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture
def source(tmp_path):
    return make_video(tmp_path / 'synthetic.mp4')


def test_real_decoder_frames_expand_a_false_boundary_without_movie_answers(source, tmp_path):
    catalog = frame_catalog(source)
    rows = catalog['frames']
    slot = {'start_s': 0, 'end_s': 4}
    old = anchor_grid(source, slot, rows[55]['frame_id'], tmp_path / 'old')
    assert old['frames'][2]['source_time_s'] == pytest.approx(55 / 30)
    for row in old['frames']:
        with Image.open(row['png_path']) as image:
            r, _, b = image.convert('RGB').getpixel((30, 30))
            assert r > b
    result = boundary_pages(source, slot, 2, 2.5, rows[55]['frame_id'], tmp_path / 'expanded',
                            observed_frame_ids=[r['frame_id'] for r in old['frames']])
    assert result['continuous_source_range']['all_decoded_frames_shown'] is True
    assert result['continuous_source_range']['frame_ids'] == [r['frame_id'] for r in rows[60:75]]
    assert len(result['pages']) == 4
    assert result['anchor_in_continuous_range'] is False
    assert result['page_bindings'][0]['role'] == 'noncontinuous_original_anchor_comparison'
    assert result['page_bindings'][0]['continuous_frame_ids'] == []
    assert [r['decode_frame_index'] for r in result['catalog_frames']] == [55, *range(60, 75)]
    assert result['new_frame_ids'] == [r['frame_id'] for r in rows[60:75]]
    colors = []
    for page in result['pages']:
        assert 1 <= len(page['frames']) <= 6
        assert verify_grid(page) == page
        assert page['source']['sha256'] == catalog['source']['sha256']
        for row in page['frames']:
            assert row['pts_time_s'] == row['source_time_s']
            with Image.open(row['png_path']) as image:
                r, _, b = image.convert('RGB').getpixel((30, 30))
                colors.append(r > b)
    assert True in colors and False in colors


def test_anchor_inside_envelope_is_present_without_noncontinuous_extra_page(source, tmp_path):
    rows = frame_catalog(source)['frames']
    result = boundary_pages(source, {'start_s': 0, 'end_s': 4}, 1.8, 2.4,
                            rows[55]['frame_id'], tmp_path / 'pages', observed_frame_ids=[])
    assert result['anchor_in_continuous_range'] is True
    assert len(result['pages']) == 3
    assert len(result['new_frame_ids']) == 18
    assert all(binding['role'] == 'continuous_source_frames' for binding in result['page_bindings'])
    assert rows[55]['frame_id'] in {row['frame_id'] for row in result['catalog_frames']}


def test_cache_resume_changes_no_existing_bytes_and_detects_pixel_corruption(source, tmp_path):
    rows = frame_catalog(source)['frames']
    args = (source, {'start_s': 0, 'end_s': 4}, .1, .6, rows[0]['frame_id'], tmp_path / 'pages')
    result = boundary_pages(*args)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (tmp_path / 'pages').rglob('*') if p.is_file()}
    assert boundary_pages(*args) == result
    assert before == {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in (tmp_path / 'pages').rglob('*') if p.is_file()}
    png = Path(result['pages'][-1]['frames'][-1]['png_path'])
    png.write_bytes(png.read_bytes() + b'corruption')
    with pytest.raises(ValueError, match='frame_png_sha256'):
        boundary_pages(*args)


def test_known_frames_only_are_not_new_evidence(source, tmp_path):
    rows = frame_catalog(source)['frames']
    with pytest.raises(ValueError, match='boundary_no_new_decoder_frame'):
        boundary_pages(source, {'start_s': 0, 'end_s': 4}, .1, .6, rows[0]['frame_id'],
                       tmp_path / 'pages', observed_frame_ids=[row['frame_id'] for row in rows])
    assert not (tmp_path / 'pages').exists()


def test_high_fps_capacity_stops_instead_of_sampling_out_frames(tmp_path):
    source = make_video(tmp_path / 'high_fps.mp4', rate=120, frames=120)
    rows = frame_catalog(source)['frames']
    with pytest.raises(ValueError, match='boundary_all_frames_must_fit_page_capacity'):
        boundary_pages(source, {'start_s': 0, 'end_s': 1}, .1, .5, rows[0]['frame_id'],
                       tmp_path / 'pages')
    assert not (tmp_path / 'pages').exists()


def test_vfr_nonzero_absolute_pts_are_not_replaced_by_an_assumed_fps(tmp_path):
    source = tmp_path / 'vfr.mkv'
    with av.open(str(source), 'w') as container:
        stream = container.add_stream('libx264', rate=30)
        stream.width, stream.height, stream.pix_fmt = 96, 64, 'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
        for index, timestamp in enumerate([5000, 5033, 5080, 5113, 5170, 5220, 5280, 5350]):
            frame = av.VideoFrame.from_image(Image.new('RGB', (96, 64), (index * 20, 40, 80)))
            frame.pts, frame.time_base = timestamp, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    catalog = frame_catalog(source)
    rows = catalog['frames']
    result = boundary_pages(source, {'start_s': 0, 'end_s': catalog['source']['source_end_s']},
                            .05, .3, rows[0]['frame_id'], tmp_path / 'pages')
    observed = [r for r in result['catalog_frames'] if r['frame_id'] in
                result['continuous_source_range']['frame_ids']]
    assert [row['source_time_s'] for row in observed] == pytest.approx([.08, .113, .17, .22, .28])
    assert [row['pts_time_s'] for row in observed] == pytest.approx([5.08, 5.113, 5.17, 5.22, 5.28])
    assert result['continuous_source_range']['requested_start_s'] == .05
    assert result['continuous_source_range']['start_s'] == .08
    assert result['continuous_source_range']['last_frame_end_s'] == .35


@pytest.mark.parametrize('start,end,error', [
    (-.01, .1, 'boundary_envelope_inside_slot'),
    (.1, 4.1, 'boundary_envelope_inside_slot'),
    (.1, .701, 'boundary_observation_capacity'),
    (.1, .1, 'boundary_envelope_inside_slot'),
    (True, 1.1, 'boundary_envelope_inside_slot'),
    (.1, float('inf'), 'boundary_envelope_inside_slot'),
])
def test_invalid_envelope_is_rejected(source, tmp_path, start, end, error):
    rows = frame_catalog(source)['frames']
    with pytest.raises(ValueError, match=error):
        boundary_pages(source, {'start_s': 0, 'end_s': 4}, start, end,
                       rows[0]['frame_id'], tmp_path / 'pages')
