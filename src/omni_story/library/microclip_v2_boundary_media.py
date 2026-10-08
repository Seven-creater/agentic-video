"""All real presentation frames in a model-selected local boundary envelope.

The historical six-frame grid schema is unchanged. Extra anchor comparison
pages are identified separately and never labelled as contiguous observations.
"""
from __future__ import annotations

import math

from .microclip_v2_boundary_contracts import MAX_OBSERVATION_S
from .microclip_v2_media import _catalog, _require, _rows_grid


MAX_PAGES = 4
IMAGES_PER_PAGE = 6


def boundary_pages(source_path, slot, start_s, end_s,
                   anchor_frame_id, output_dir, *, observed_frame_ids=()):
    """Return <=4 immutable six-frame grids without temporal subsampling.

    Numeric envelope endpoints request observation, never invent edit frames.
    All in-range frames must fit; capacity overflow stops rather than silently
    dropping intermediate frames. Cloud frame comprehension remains unknown.
    """
    _require(isinstance(slot, dict), 'boundary_slot')
    _require(type(start_s) in (int, float) and type(end_s) in (int, float)
             and math.isfinite(start_s) and math.isfinite(end_s)
             and slot['start_s'] <= start_s < end_s <= slot['end_s'] + 1e-6,
             'boundary_envelope_inside_slot')
    _require(end_s - start_s <= MAX_OBSERVATION_S + 1e-6, 'boundary_observation_capacity')
    catalog, slot_rows = _catalog(source_path, slot.get('start_s'), slot.get('end_s'))
    rows_by_id = {row['frame_id']: row for row in slot_rows}
    _require(all(isinstance(ident, str) and ident in rows_by_id
                 for ident in (anchor_frame_id,)),
             'boundary_known_slot_frame_ids')
    _require(not isinstance(observed_frame_ids, (str, bytes)), 'boundary_observed_ids')
    observed = set(observed_frame_ids)
    _require(all(isinstance(ident, str) for ident in observed), 'boundary_observed_ids')
    continuous = [row for row in slot_rows if start_s <= row['source_time_s'] < end_s]
    _require(bool(continuous), 'boundary_no_source_frame')
    a, b = continuous[0], continuous[-1]
    start, end = a['source_time_s'], min(end_s, b['frame_end_s']) if b['frame_end_s'] is not None else None
    _require(end is not None and start < end <= slot['end_s'] + 1e-6,
             'boundary_real_endpoint')
    _require([row['decode_frame_index'] for row in continuous]
             == list(range(a['decode_frame_index'], b['decode_frame_index'] + 1)),
             'boundary_contiguous_decoder_frames')
    continuous_ids = [row['frame_id'] for row in continuous]
    new_ids = [ident for ident in continuous_ids if ident not in observed]
    _require(bool(new_ids), 'boundary_no_new_decoder_frame')
    extra_anchor = anchor_frame_id not in set(continuous_ids)
    page_count = (len(continuous) + IMAGES_PER_PAGE - 1) // IMAGES_PER_PAGE + int(extra_anchor)
    _require(page_count <= MAX_PAGES, 'boundary_all_frames_must_fit_page_capacity')
    pages = []
    bindings = []
    if extra_anchor:
        page = _rows_grid(source_path, [rows_by_id[anchor_frame_id]], slot['end_s'], output_dir)
        pages.append(page)
        bindings.append({'page_index': 0, 'role': 'noncontinuous_original_anchor_comparison',
                         'continuous_frame_ids': [],
                         'supplemental_anchor_frame_ids': [anchor_frame_id]})
    for index in range(0, len(continuous), IMAGES_PER_PAGE):
        group = continuous[index:index + IMAGES_PER_PAGE]
        page = _rows_grid(source_path, group, end, output_dir)
        pages.append(page)
        bindings.append({'page_index': len(pages) - 1, 'role': 'continuous_source_frames',
                         'continuous_frame_ids': [row['frame_id'] for row in group],
                         'supplemental_anchor_frame_ids': []})
    # _rows_grid already verifies source decode, PNG pixels and grid bytes.
    shown = [row for page in pages for row in page['frames']]
    _require(len({row['frame_id'] for row in shown}) == len(shown), 'boundary_shown_frame_unique')
    _require(anchor_frame_id in {row['frame_id'] for row in shown}, 'boundary_anchor_present')
    _require(all(row['source_sha256'] == catalog['source']['sha256'] for row in shown),
             'boundary_same_real_source')
    return {
        'pages': pages,
        'catalog_frames': shown,
        'new_frame_ids': new_ids,
        'continuous_source_range': {
            'source_sha256': catalog['source']['sha256'],
            'start_s': start, 'end_s': end, 'end_exclusive': True,
            'requested_start_s': float(start_s), 'requested_end_s': float(end_s),
            'last_frame_end_s': b['frame_end_s'],
            'frame_ids': continuous_ids, 'frame_count': len(continuous),
            'all_decoded_frames_shown': True,
        },
        'anchor_in_continuous_range': not extra_anchor,
        'page_bindings': bindings,
        'limitations': ['Complete local decoder-frame images do not establish cloud '
                        'inspection of every frame or continuous-motion understanding.'],
    }
