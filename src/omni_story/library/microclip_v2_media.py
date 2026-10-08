"""PTS-bound local evidence for a whole slot and its individual boundaries.

These helpers only extract CPU image evidence. Image coverage is not a claim
that a model watched continuous motion or that an audience understood an edit.
Existing grid manifests remain immutable and retain full, uncropped frames.
"""
from __future__ import annotations

import math

from .microclip_frames import _select, extract_grid, frame_catalog, verify_grid


def _require(ok, label):
    if not ok:
        raise ValueError('microclip_v2_media:' + label)


def _interval(start_s, end_s):
    _require(type(start_s) in (int, float) and type(end_s) in (int, float)
             and math.isfinite(start_s) and math.isfinite(end_s)
             and 0 <= start_s < end_s, 'interval')


def _catalog(source_path, start_s, end_s):
    _interval(start_s, end_s)
    catalog = frame_catalog(source_path)
    source_end = catalog['source']['source_end_s']
    # FFprobe duration strings commonly round to six decimal places. This
    # tolerance accepts only that metadata rounding; all extracted frames and
    # page endpoints still bind to the decoder's actual PTS/frame_end.
    _require(source_end is None or end_s <= source_end + 1e-6,
             'interval_past_source_end')
    rows = [r for r in catalog['frames'] if start_s <= r['source_time_s'] < end_s]
    _require(bool(rows), 'no_frames_in_interval')
    return catalog, rows


def _rows_grid(source_path, rows, end_s, output_dir):
    """Restrict the interval to these consecutive rows, then select all of it."""
    last = rows[-1]
    end = min(end_s, last['frame_end_s']) if last['frame_end_s'] is not None else min(
        end_s, math.nextafter(last['source_time_s'], math.inf))
    result = extract_grid(source_path, rows[0]['source_time_s'], end,
                          output_dir, count=len(rows), exclude_frame_ids=())
    _require([r['frame_id'] for r in result['frames']] == [r['frame_id'] for r in rows],
             'consecutive_frame_selection')
    return verify_grid(result, source_path=source_path)


def anchor_grid(source_path, slot, anchor_frame_id, output_dir):
    """Show an actual candidate frame together with up to five decoder neighbours.

    Six presentation frames normally cover anchor-2 through anchor+3. At a slot
    edge the window shifts to retain six where available. Previously seen frames
    are intentionally included for direct visual comparison.
    """
    _require(isinstance(slot, dict), 'slot')
    _, rows = _catalog(source_path, slot.get('start_s'), slot.get('end_s'))
    _require(isinstance(anchor_frame_id, str), 'anchor_frame_id')
    index = next((i for i, row in enumerate(rows) if row['frame_id'] == anchor_frame_id), None)
    _require(index is not None, 'unknown_anchor_frame_id')
    start = min(max(0, index - 2), max(0, len(rows) - 6))
    chosen = rows[start:start + 6]
    result = _rows_grid(source_path, chosen, slot['end_s'], output_dir)
    _require(anchor_frame_id in {r['frame_id'] for r in result['frames']}, 'anchor_omitted')
    return result


def _gap(rows):
    return max((right['source_time_s'] - left['source_time_s']
                for left, right in zip(rows, rows[1:])), default=0.0)


def _page_plan(rows, start_s, end_s, max_pages, target_gap_s):
    """Choose chronological interval pages using the existing grid selector."""
    # Preview uses precisely the same true-PTS selection as extraction and cache
    # validation; no frame index is converted to time using an assumed FPS.
    span = rows[-1]['source_time_s'] - rows[0]['source_time_s']
    capacity = max_pages * 6
    initial = min(capacity, max(2, math.ceil(span / target_gap_s) + 1))
    for sample_count in range(initial, capacity + 1):
        plan, selected = [], []
        targets, _ = _select(rows, start_s, end_s, sample_count, set())
        for index in range(0, len(targets), 6):
            group = targets[index:index + 6]
            start = group[0]['source_time_s']
            # The final real frame is included; the next decoded row is not.
            end = min(end_s, math.nextafter(group[-1]['source_time_s'], math.inf))
            page_rows, _ = _select(rows, start, end, len(group), set())
            plan.append((start, end, len(group), page_rows))
            selected.extend(page_rows)
        if _gap(selected) <= target_gap_s + 1e-9:
            break
    return plan


def dense_pages(source_path, start_s, end_s, output_dir, *, max_pages=10):
    """Extract chronological full-frame pages and report actual temporal coverage.

    At most six images appear on each page. All frames are shown when they fit
    within ``max_pages * 6``. Longer intervals target gaps of at most 0.25 seconds;
    decoder timing and the page limit can prevent that, which is reported rather
    than relabelled as complete observation. First and last in-range frames always
    remain represented. The return value is ``{'pages': [...], 'coverage': {...}}``.
    """
    _require(type(max_pages) is int and 1 <= max_pages <= 10, 'max_pages')
    catalog, rows = _catalog(source_path, start_s, end_s)
    capacity, target_gap_s = max_pages * 6, .25
    pages = []
    if len(rows) <= capacity:
        for index in range(0, len(rows), 6):
            pages.append(_rows_grid(source_path, rows[index:index + 6], end_s, output_dir))
    else:
        for start, end, count, expected in _page_plan(rows, start_s, end_s, max_pages, target_gap_s):
            result = extract_grid(source_path, start, end, output_dir, count=count,
                                  exclude_frame_ids=())
            _require([r['frame_id'] for r in result['frames']]
                     == [r['frame_id'] for r in expected], 'dense_page_selection')
            pages.append(verify_grid(result, source_path=source_path))
    selected = [row for page in pages for row in page['frames']]
    _require(1 <= len(pages) <= max_pages and len(selected) <= capacity, 'page_limit')
    _require(selected[0]['frame_id'] == rows[0]['frame_id']
             and selected[-1]['frame_id'] == rows[-1]['frame_id'], 'interval_endpoint_omitted')
    _require(all(start_s <= row['source_time_s'] < end_s for row in selected), 'frame_outside_interval')
    _require(all(left['decode_frame_index'] < right['decode_frame_index']
                 for left, right in zip(selected, selected[1:])), 'page_order')
    max_gap = _gap(selected)
    source_max_gap = _gap(rows)
    all_shown = len(selected) == len(rows)
    target_met = max_gap <= target_gap_s + 1e-9
    limitations = ['PTS-bound still-image pages do not establish continuous-motion '
                   'observation or real-time audience readability.']
    if source_max_gap > target_gap_s + 1e-9:
        limitations.append('The decoded source itself contains presentation-frame gaps '
                           'longer than the target sampling interval.')
    if not target_met and not all_shown:
        limitations.append('The page limit requires wider actual sample gaps; '
                           'unshown intermediate frames remain unobserved.')
    final_frame_end = rows[-1]['frame_end_s']
    coverage = {
        'schema': 'microclip_dense_coverage_v1',
        'source_path': catalog['source']['path'], 'source_sha256': catalog['source']['sha256'],
        'start_s': float(start_s), 'end_s': float(end_s), 'end_exclusive': True,
        'first_frame_id': selected[0]['frame_id'], 'last_frame_id': selected[-1]['frame_id'],
        'first_observed_time_s': selected[0]['source_time_s'],
        'last_observed_time_s': selected[-1]['source_time_s'],
        'last_observed_frame_end_s': final_frame_end,
        'leading_unrepresented_time_s': max(0.0, rows[0]['source_time_s'] - start_s),
        'trailing_unrepresented_time_s': (max(0.0, end_s - final_frame_end)
                                           if final_frame_end is not None else None),
        'in_range_decoded_frame_count': len(rows), 'selected_frame_count': len(selected),
        'page_count': len(pages), 'max_pages': max_pages, 'images_per_page_limit': 6,
        'target_gap_s': target_gap_s, 'max_sample_gap_s': max_gap,
        'max_source_frame_gap_s': source_max_gap, 'target_gap_met': target_met,
        'all_decoded_frames_shown': all_shown,
        'page_bindings': [{'manifest_path': page['manifest_path'],
                           'request_sha256': page['request_sha256'],
                           'grid_sha256': page['grid']['sha256'],
                           'frame_ids': [row['frame_id'] for row in page['frames']]}
                          for page in pages],
        'limitations': limitations,
    }
    return {'pages': pages, 'coverage': coverage}
