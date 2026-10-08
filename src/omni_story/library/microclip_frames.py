"""Local, source-bound presentation-frame evidence for a short video interval.

Decode indices count *decoder output frames*, in presentation order, not packets.
Seconds are relative to the video stream start; absolute PTS is also retained.
The source image is never cropped. Grids are labelled, resized presentations only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from fractions import Fraction
import uuid

import av
from PIL import Image, ImageDraw, ImageFont


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _require(ok, label):
    if not ok:
        raise ValueError('microclip_frames:' + label)


def _frame_id(sha256, index):
    return 'f_' + hashlib.sha256(f'{sha256}:{index}'.encode()).hexdigest()[:24]


def _decode(path, sha256):
    """Sequential decoding: no seeking or guessed fps-based timestamps."""
    with av.open(str(path)) as container:
        _require(bool(container.streams.video), 'no_video_stream')
        stream = container.streams.video[0]
        origin = (Fraction(stream.start_time) * stream.time_base
                  if stream.start_time is not None else None)
        previous = None
        for index, frame in enumerate(container.decode(stream)):
            _require(frame.pts is not None and frame.time_base is not None, 'missing_frame_pts')
            absolute = Fraction(frame.pts) * frame.time_base
            if origin is None:
                origin = absolute
            timestamp = absolute - origin
            _require(timestamp >= 0 and (previous is None or timestamp > previous),
                     'nonincreasing_presentation_pts')
            previous = timestamp
            duration = (Fraction(frame.duration) * frame.time_base
                        if frame.duration is not None and frame.duration > 0 else None)
            row = {'frame_id': _frame_id(sha256, index), 'source_sha256': sha256,
                   'decode_frame_index': index, 'pts': frame.pts,
                   'time_base': [frame.time_base.numerator, frame.time_base.denominator],
                   'pts_time_s': float(absolute), 'source_time_s': float(timestamp),
                   'source_time_fraction': str(timestamp),
                   'duration_s': float(duration) if duration is not None else None,
                   'width': frame.width, 'height': frame.height}
            source = {'path': str(Path(path).resolve()), 'sha256': sha256,
                      'video_stream_index': stream.index,
                      'timeline_origin_s': float(origin), 'timeline_origin_fraction': str(origin),
                      'timeline_origin_policy': ('video_stream_start_time' if stream.start_time is not None
                                                 else 'first_decoded_presentation_pts'),
                      'stream_start_time': stream.start_time,
                      'stream_time_base': [stream.time_base.numerator, stream.time_base.denominator],
                      'stream_duration_s': (float(stream.duration * stream.time_base)
                                            if stream.duration is not None else None),
                      'container_start_time_us': container.start_time,
                      'width': frame.width, 'height': frame.height}
            yield source, row, frame


def frame_catalog(source_path, *, start_s=0, end_s=None):
    """Return real frame IDs/PTS. Decodes the whole source once; holds no images."""
    path = Path(source_path).resolve()
    sha256 = _sha(path)
    source, rows = None, []
    for source, row, _ in _decode(path, sha256):
        rows.append(row)
    _require(bool(rows), 'empty_video')
    for current, following in zip(rows, rows[1:]):
        current['frame_end_s'] = following['source_time_s']
        current['frame_end_basis'] = 'next_presentation_pts'
    last = rows[-1]
    if last['duration_s'] is not None:
        last['frame_end_s'] = last['source_time_s'] + last['duration_s']
        last['frame_end_basis'] = 'decoded_frame_duration'
    elif (source['stream_duration_s'] is not None
          and source['stream_duration_s'] > last['source_time_s']):
        last['frame_end_s'] = source['stream_duration_s']
        last['frame_end_basis'] = 'declared_stream_duration'
    else:
        last['frame_end_s'] = None
        last['frame_end_basis'] = 'unknown'
    source['decoded_frame_count'] = len(rows)
    source['source_end_s'] = last['frame_end_s']
    _require(_sha(path) == sha256, 'source_changed_during_decode')
    return {'source': source, 'frames': [r for r in rows if r['source_time_s'] >= start_s
                                        and (end_s is None or r['source_time_s'] < end_s)]}


def _select(rows, start_s, end_s, count, excluded):
    available = [r for r in rows if start_s <= r['source_time_s'] < end_s
                 and r['frame_id'] not in excluded]
    _require(bool(available), 'no_new_frames_in_interval')
    # Last target is genuinely inside the exclusive endpoint, never a fabricated frame.
    targets = ([start_s] if count == 1 else
               [start_s + (math.nextafter(end_s, start_s) - start_s) * i / (count - 1)
                for i in range(count)])
    selected = []
    remaining = available.copy()
    for target in targets:
        if not remaining:
            break
        nearest = min(remaining, key=lambda r: (abs(r['source_time_s'] - target), r['decode_frame_index']))
        selected.append(dict(nearest, requested_time_s=target))
        remaining.remove(nearest)
    return sorted(selected, key=lambda r: r['decode_frame_index']), targets


def _grid_image(rows, images, max_cell_width):
    cells = []
    for row, full in zip(rows, images, strict=True):
        width = min(full.width, max_cell_width)
        height = max(1, round(full.height * width / full.width))
        shown = full.resize((width, height), Image.Resampling.LANCZOS)
        cell = Image.new('RGB', (max(160, width), height + 46), 'white')
        cell.paste(shown, ((cell.width - width) // 2, 46))
        draw = ImageDraw.Draw(cell)
        labels = [f"#{row['decode_frame_index']}  {row['source_time_s']:.6f}s", row['frame_id']]
        size = 16
        font = ImageFont.load_default(size=size)
        while size > 8 and max(draw.textbbox((0, 0), label, font=font)[2] for label in labels) > cell.width - 8:
            size -= 1
            font = ImageFont.load_default(size=size)
        for index, label in enumerate(labels):
            draw.text((4, 2 + index * 22), label, fill='black', font=font)
        cells.append(cell)
    cols = min(3, len(cells))
    cell_width, cell_height = max(c.width for c in cells), max(c.height for c in cells)
    grid = Image.new('RGB', (cell_width * cols, cell_height * math.ceil(len(cells) / cols)), '#dddddd')
    for index, cell in enumerate(cells):
        grid.paste(cell, ((index % cols) * cell_width, (index // cols) * cell_height))
    return grid, {'width': grid.width, 'height': grid.height, 'columns': cols,
            'rows': math.ceil(len(cells) / cols), 'cell_width': cell_width,
            'cell_height': cell_height, 'label_height': 46,
            'crop_box': None, 'resize_only': True, 'frame_id_label': 'full'}


def _write_grid(rows, images, path, max_cell_width):
    grid, description = _grid_image(rows, images, max_cell_width)
    grid.save(path, format='PNG')
    return description


def _publish_grid(temporary, final):
    """Publish once; tolerate bounded Windows file-sharing interruptions.

    A competing destination is verified by the caller. Never overwrite it or
    remove the unpublished directory, which may contain failure evidence.
    """
    for attempt in range(6):
        try:
            os.rename(temporary, final)
            return True
        except OSError as error:
            if final.exists():
                return False
            if getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(min(.05 * 2 ** attempt, .5))


def extract_grid(source_path, start_s, end_s, output_dir, *, count=6,
                 max_cell_width=640, exclude_frame_ids=()):
    """Save <=6 full RGB PNGs and a labelled grid with exact source provenance.

    Repeated inputs verify and reuse immutable files. Different inputs create a
    content-addressed child directory. ``exclude_frame_ids`` enables genuinely
    new observations during neighbourhood refinement, rather than repeated frames.
    """
    _require(type(start_s) in (int, float) and type(end_s) in (int, float)
             and math.isfinite(start_s) and math.isfinite(end_s) and 0 <= start_s < end_s, 'interval')
    _require(type(count) is int and 1 <= count <= 6, 'count')
    _require(type(max_cell_width) is int and max_cell_width >= 160, 'cell_width')
    _require(not isinstance(exclude_frame_ids, (str, bytes)), 'excluded_frame_ids')
    exclude_frame_ids = tuple(exclude_frame_ids)
    _require(all(isinstance(x, str) for x in exclude_frame_ids), 'excluded_frame_ids')
    path, output = Path(source_path).resolve(), Path(output_dir).resolve()
    request = {'source_path': str(path), 'source_sha256': _sha(path),
               'start_s': float(start_s), 'end_s': float(end_s), 'count': count,
               'max_cell_width': max_cell_width, 'exclude_frame_ids': sorted(set(exclude_frame_ids))}
    request_sha = hashlib.sha256(_json_bytes(request)).hexdigest()
    final = output / ('grid_' + request_sha[:20])
    manifest_path = final / 'manifest.json'
    if final.exists():
        result = verify_grid(manifest_path, source_path=path)
        _require(result['request'] == request, 'cache_request')
        return result
    catalog = frame_catalog(path)
    _require(catalog['source']['sha256'] == request['source_sha256'], 'source_changed_before_decode')
    source_end = catalog['source']['source_end_s']
    _require(source_end is None or end_s <= source_end + 1e-9, 'interval_past_source_end')
    selected, targets = _select(catalog['frames'], start_s, end_s, count,
                                set(request['exclude_frame_ids']))
    wanted = {r['decode_frame_index']: r for r in selected}
    images = []
    for _, row, frame in _decode(path, request['source_sha256']):
        if row['decode_frame_index'] in wanted:
            _require(row['pts'] == wanted[row['decode_frame_index']]['pts'], 'decode_pts_changed')
            images.append(frame.to_image().convert('RGB'))
        if row['decode_frame_index'] >= selected[-1]['decode_frame_index']:
            break
    _require(_sha(path) == request['source_sha256'], 'source_changed_during_extraction')
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / ('._grid_' + request_sha[:12] + '_' + uuid.uuid4().hex[:8])
    temporary.mkdir()
    for row, image in zip(selected, images, strict=True):
        name = row['frame_id'] + '.png'
        image.save(temporary / name, format='PNG')
        row.update({'png_path': str(final / name), 'png_sha256': _sha(temporary / name),
                    'decoded_rgb_sha256': hashlib.sha256(image.tobytes()).hexdigest()})
    presentation = _write_grid(selected, images, temporary / 'grid.png', max_cell_width)
    manifest = {'schema': 'microclip_grid_v1', 'request': request, 'request_sha256': request_sha,
                'source': catalog['source'], 'frames': selected,
                'sampling': {'requested_targets_s': targets, 'end_exclusive': True,
                             'method': 'nearest_distinct_pts_in_interval',
                             'returned_count': len(selected), 'requested_count': count},
                'grid': dict(presentation, path=str(final / 'grid.png'), sha256=_sha(temporary / 'grid.png')),
                'manifest_path': str(manifest_path)}
    raw = _json_bytes(manifest)
    (temporary / 'manifest.json').write_bytes(raw)
    (temporary / 'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest(), encoding='ascii')
    if not _publish_grid(temporary, final):
        # A concurrent writer won. Never overwrite its files or remove forensic partials.
        return verify_grid(manifest_path, source_path=path)
    return manifest


def verify_grid(manifest_or_path, *, source_path=None):
    """Recheck cache bytes, source SHA, exact decoded timestamps and RGB pixels."""
    supplied = manifest_or_path if isinstance(manifest_or_path, dict) else None
    path = Path(supplied['manifest_path'] if supplied is not None else manifest_or_path).resolve()
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == path.with_name('manifest.sha256').read_text('ascii').strip(),
             'manifest_sha256')
    manifest = json.loads(raw)
    _require(supplied is None or supplied == manifest, 'manifest_not_disk_record')
    _require(manifest.get('schema') == 'microclip_grid_v1' and manifest['manifest_path'] == str(path), 'schema_path')
    request = manifest['request']
    _require(hashlib.sha256(_json_bytes(request)).hexdigest() == manifest['request_sha256'], 'request_sha256')
    source = Path(source_path or manifest['source']['path']).resolve()
    _require(str(source) == request['source_path'] == manifest['source']['path'], 'source_path')
    _require(_sha(source) == manifest['source']['sha256'] == request['source_sha256'], 'source_sha256')
    rows = manifest['frames']
    _require(1 <= len(rows) <= request['count'] <= 6, 'frame_count')
    wanted = {r['decode_frame_index']: r for r in rows}
    _require(len(wanted) == len(rows), 'duplicate_frame')
    catalog = frame_catalog(source)
    _require(catalog['source'] == manifest['source'], 'source_metadata')
    expected, targets = _select(catalog['frames'], request['start_s'], request['end_s'], request['count'],
                                 set(request['exclude_frame_ids']))
    _require(targets == manifest['sampling']['requested_targets_s'], 'requested_targets')
    _require(len(expected) == len(rows), 'selection_count')
    images = []
    for expected_row, row in zip(expected, rows, strict=True):
        _require(all(row.get(k) == v for k, v in expected_row.items()), 'frame_pts_mapping')
        png = Path(row['png_path']).resolve()
        _require(png.parent == path.parent and png.name == row['frame_id'] + '.png', 'frame_path')
        _require(_sha(png) == row['png_sha256'], 'frame_png_sha256')
        with Image.open(png) as image:
            _require(image.size == (row['width'], row['height'])
                     and hashlib.sha256(image.convert('RGB').tobytes()).hexdigest() == row['decoded_rgb_sha256'],
                     'frame_rgb_sha256')
            images.append(image.convert('RGB').copy())
    for _, row, frame in _decode(source, request['source_sha256']):
        if row['decode_frame_index'] in wanted:
            rgb = frame.to_image().convert('RGB').tobytes()
            _require(hashlib.sha256(rgb).hexdigest() == wanted[row['decode_frame_index']]['decoded_rgb_sha256'],
                     'decoded_source_pixels')
        if row['decode_frame_index'] >= max(wanted):
            break
    grid = Path(manifest['grid']['path']).resolve()
    _require(grid.parent == path.parent and grid.name == 'grid.png', 'grid_path')
    _require(_sha(grid) == manifest['grid']['sha256'], 'grid_sha256')
    _require(manifest['grid']['crop_box'] is None and manifest['grid']['resize_only'] is True, 'presentation_crop')
    expected_grid, description = _grid_image(rows, images, request['max_cell_width'])
    _require(all(manifest['grid'].get(k) == v for k, v in description.items()), 'grid_geometry')
    with Image.open(grid) as shown:
        _require(shown.size == expected_grid.size and shown.convert('RGB').tobytes() == expected_grid.tobytes(),
                 'grid_frame_pixels')
    _require(_sha(source) == request['source_sha256'], 'source_changed_during_verification')
    return manifest


def extract_neighborhood(previous_manifest, frame_id, output_dir, *, radius_s=.5, count=6,
                         exclude_frame_ids=()):
    """Narrow a previous interval around a model-selected real frame ID.

    Excludes the previous grid plus supplied previously viewed IDs. Returning fewer
    than six is explicit when the interval contains fewer new source frames.
    """
    previous = verify_grid(previous_manifest)
    _require(type(radius_s) in (int, float) and math.isfinite(radius_s) and radius_s > 0, 'radius')
    center = next((r['source_time_s'] for r in previous['frames'] if r['frame_id'] == frame_id), None)
    _require(center is not None, 'unknown_frame_id')
    start = max(previous['request']['start_s'], center - radius_s)
    end = min(previous['request']['end_s'], center + radius_s)
    _require(end - start < previous['request']['end_s'] - previous['request']['start_s'], 'not_narrower')
    excluded = set(exclude_frame_ids) | {r['frame_id'] for r in previous['frames']}
    return extract_grid(previous['source']['path'], start, end, output_dir, count=count,
                        max_cell_width=previous['request']['max_cell_width'], exclude_frame_ids=excluded)
