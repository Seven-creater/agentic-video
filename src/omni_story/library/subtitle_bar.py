"""Source-bound, deterministic bottom letterbox masks for question-first.

The three verified entries come from the library's received watched_windows
records (full_tpl_kuangbiao_fish_20261026_v1), not a title/duration guess.
Other encodes must be measured from their own pixels once per film.
Measurement assumes a stable, horizontal black bar. Sparse captions/logos are
outliers; overlays reaching >=25% of columns, changing aspect ratios, or mostly
dark picture edges need manual verification. The robust column IQR must be at
most 2% of frame height, and the bottom bar at least 5%.
"""
from copy import deepcopy
import math
from pathlib import Path
import subprocess

from .media import probe_media
from .state import write_json


VERIFIED_LIBRARY_BARS = dict.fromkeys((
    '24675c65978d5360586f02edcab4735708640027b2f6dff654bda1e5a5041060',
    '5f9ca95e536641933924044c8711c18eea22ed81d312969bf2c57351dda66ad0',
    'cf2fec1024243517c1811dce0bba3c1790af887823ab93ec543627606323b64e',
), (1920, 1080, 950))

BLACK_PIXEL_THRESHOLD = 24
MIN_BOTTOM_BAR_FRACTION = 0.05
MAX_PICTURE_BOTTOM_IQR_FRACTION = 0.02
COLUMN_MEASUREMENT_METHOD = 'ffmpeg_gray_column_median_v2'


def _column_picture_bottoms(frames):
    """Last nonblack pixel + 1 per column, across all sampled grayscale frames.

    Taking the temporal maximum avoids moving into the picture on dark frames;
    unlike a spatial maximum it leaves corner logos as a minority of columns.
    """
    import numpy as np
    bright = frames.max(axis=0) > BLACK_PIXEL_THRESHOLD
    return np.where(bright, np.arange(1, frames.shape[1] + 1)[:, None], 0).max(axis=0)


def _picture_bottom_stats(columns, height):
    """Ignore <25% contaminated columns, reject broadly inconsistent edges."""
    import numpy as np
    q25, median, q75 = (float(v) for v in np.percentile(columns, [25, 50, 75]))
    spread = q75 - q25
    if spread > height * MAX_PICTURE_BOTTOM_IQR_FRACTION:
        raise ValueError('question_first:subtitle_bar_column_spread_too_large')
    bottom = math.ceil(median)
    if bottom <= 0 or height - bottom < height * MIN_BOTTOM_BAR_FRACTION:
        raise ValueError('question_first:subtitle_bar_below_five_percent_or_no_picture')
    return bottom, dict(column_p25_px=q25, column_median_px=median,
                        column_p75_px=q75, column_iqr_px=spread)


def validate_subtitle_bar(bar, source_sha256):
    if not isinstance(bar, dict) or bar.get('source_sha256') != source_sha256:
        raise ValueError('question_first:subtitle_bar_source_mismatch')
    values = [bar.get(k) for k in ('native_width', 'native_height', 'picture_bottom_px')]
    if (any(type(v) is not int for v in values) or
            not (values[0] > 0 and 0 < values[2] < values[1]) or not bar.get('measurement')):
        raise ValueError('question_first:invalid_subtitle_bar_measurement')
    bottom, height = values[2], values[1]
    if height - bottom < height * MIN_BOTTOM_BAR_FRACTION:
        raise ValueError('question_first:subtitle_bar_below_five_percent_or_no_picture')
    expected = dict(x=0, y=bottom / height, w=1, h=(height - bottom) / height, color='#000000')
    if bar.get('mask') != expected:
        raise ValueError('question_first:subtitle_bar_must_cover_black_bar_only')
    return bar


def film_subtitle_bar(source, info=None, *, measure=False):
    """Reuse a verified SHA-bound value, otherwise measure full-width columns.

    Sample twelve native frames at each of nine content timestamps. For each
    column take the deepest nonblack pixel over time, then the column median.
    Corner overlays occupying <25% of width cannot move this edge or its IQR.
    Wider overlays, variable aspect ratios and dark edges require verification;
    a large IQR or a bottom bar thinner than 5% raises ValueError.
    """
    sha = source['sha256']
    if not measure and sha in VERIFIED_LIBRARY_BARS:
        width, height, bottom = VERIFIED_LIBRARY_BARS[sha]
        measurement = dict(method='verified_library_native_frames',
            evidence='full_tpl_kuangbiao_fish_20261026_v1/watched_windows.json',
            note='subtitles in bottom black bar; mask begins below picture at native row 950')
    else:
        if not measure and info and info.get('subtitle_bar') is not None:
            cached = validate_subtitle_bar(info['subtitle_bar'], sha)
            if cached['measurement'].get('method') == COLUMN_MEASUREMENT_METHOD:
                return deepcopy(cached)
        import numpy as np
        metadata = probe_media(source['path'])
        stream = next(s for s in metadata['streams'] if s['index'] ==
                      source.get('video_stream_index', metadata['video_stream_index']))
        width, height = stream['width'], stream['height']
        bounds = (info or {}).get('bounds')
        if bounds is None:
            from .content_ranges import content_range_for
            bounds = content_range_for(source)
        if bounds is None:
            raise ValueError('question_first:subtitle_bar_content_range_required')
        lo, hi = bounds
        samples, column_bottoms = [], np.zeros(width, dtype=int)
        for i in range(1, 10):
            timestamp = lo + (hi - lo) * i / 10
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-ss', str(timestamp),
                '-noautorotate', '-i', source['path'], '-map', f"0:{stream['index']}", '-an',
                '-frames:v', '12', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
            result = subprocess.run(command, capture_output=True, timeout=60, check=True)
            if not result.stdout or len(result.stdout) % (width * height):
                raise ValueError('question_first:subtitle_bar_invalid_raw_frames')
            frames = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, height, width)
            columns = _column_picture_bottoms(frames)
            column_bottoms = np.maximum(column_bottoms, columns)
            samples.append(dict(timestamp_s=timestamp, frame_count=len(frames),
                                column_median_px=float(np.median(columns))))
        bottom, stats = _picture_bottom_stats(column_bottoms, height)
        measurement = dict(method=COLUMN_MEASUREMENT_METHOD, black_pixel_threshold=BLACK_PIXEL_THRESHOLD,
            aggregation='temporal maximum per column, then median across full native width',
            max_column_iqr_fraction=MAX_PICTURE_BOTTOM_IQR_FRACTION,
            min_bottom_bar_fraction=MIN_BOTTOM_BAR_FRACTION, samples=samples, **stats)
    bar = dict(source_sha256=sha, native_width=width, native_height=height,
        picture_bottom_px=bottom, measurement=measurement,
        mask=dict(x=0, y=bottom / height, w=1, h=(height - bottom) / height, color='#000000'))
    return validate_subtitle_bar(bar, sha)


def validate_question_first_masks(masks, bar, start_s, end_s):
    """Exactly one black, full-width bar for the complete source window."""
    validate_subtitle_bar(bar, bar['source_sha256'])
    expected = dict(bar['mask'], start_s=start_s, end_s=end_s)
    if (not isinstance(masks, list) or len(masks) != 1 or not isinstance(masks[0], dict) or
            any(masks[0].get(k) != v for k, v in expected.items()) or
            not all(type(masks[0].get(k)) in (int, float) and math.isfinite(masks[0][k])
                    for k in ('x', 'y', 'w', 'h', 'start_s', 'end_s'))):
        raise ValueError('question_first:subtitle_mask_must_cover_full_black_bar_and_window')
    return masks


def question_first_masks(source, start_s, end_s, film_info_dir):
    """Persist measurement with film info; never send a mask query to a model."""
    path = Path(film_info_dir) / (source['sha256'] + '.json')
    info = source
    if path.is_file():
        import json
        info = dict(source, subtitle_bar=json.loads(path.read_text(encoding='utf-8')))
    bar = film_subtitle_bar(source, info)
    if not path.is_file() or info.get('subtitle_bar') != bar:
        write_json(path, bar)
    source['subtitle_bar'] = deepcopy(bar)
    masks = [dict(bar['mask'], start_s=start_s, end_s=end_s,
                  reason=bar['measurement']['method'])]
    return validate_question_first_masks(masks, bar, start_s, end_s)
