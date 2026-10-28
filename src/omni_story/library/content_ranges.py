"""Pinned feature-content bounds, shared by model prompts and range validation."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .state import LibraryStopped


def content_range_for(source):
    """No bounds on historical inputs; rendered parents use output time."""
    if source.get('time_domain') == 'output':
        return None
    record = source.get('content_range')
    return [record['content_start_s'], record['content_end_s']] if record else None


def check_content_range(source, start, end, path, *, fields=('start_s', 'end_s')):
    bounds = content_range_for(source)
    if bounds is None:
        return
    for field, value in zip(fields, (start, end)):
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and bounds[0] <= value <= bounds[1]
        except OverflowError:
            valid = False
        if not valid:
            error = ValueError('visual_library:range_outside_content')
            error.violation = dict(path=path + ('.' + field if field else ''), value=value, expected=bounds,
                repair_hint='Choose a range wholly inside the recorded content range; logos, title cards and end credits are forbidden.')
            raise error


def load_content_ranges(root, catalog):
    path = Path(root) / 'content_ranges.json'
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema_version') != 'content_ranges_v1':
            raise ValueError('schema_version')
        records = data.get('sources')
        if not isinstance(records, dict):
            raise ValueError('sources')
        durations = {s['sha256']: s['duration_s'] for s in catalog['sources']}
        for sha, duration in durations.items():
            if sha not in records:
                raise ValueError('missing_source:' + sha)
            row = records[sha]
            if not isinstance(row, dict):
                raise ValueError('source:' + sha)
            start, end = row.get('content_start_s'), row.get('content_end_s')
            def valid(a, b):
                return (all(type(v) in (int, float) and math.isfinite(v) for v in (a, b))
                        and 0 <= a < b <= duration)
            if not valid(start, end):
                raise ValueError('bounds:' + sha)
            if not isinstance(row.get('verified_by'), str) or not row['verified_by'].strip():
                raise ValueError('verified_by:' + sha)
            if not isinstance(row.get('excluded'), list):
                raise ValueError('excluded:' + sha)
            for excluded in row['excluded']:
                if (not isinstance(excluded, dict) or not valid(excluded.get('start_s'), excluded.get('end_s'))
                        or excluded.get('kind') not in {'logo', 'title_card', 'end_credits'}
                        or max(start, excluded['start_s']) < min(end, excluded['end_s'])):
                    raise ValueError('excluded_bounds:' + sha)
        if set(records) != set(durations):
            raise ValueError('unknown_source')
        return dict(data, file_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        raise LibraryStopped('visual_library:content_ranges_missing_or_invalid:' + detail) from None


def recorded_content_ranges(output, root, catalog, snapshot=None):
    state = Path(output) / 'library_state.json'
    if state.exists():
        # Never consult today's library bounds when replaying/resuming old work.
        return json.loads(state.read_text())['input_lock']['configuration'].get('content_ranges')
    loaded = load_content_ranges(root, catalog)
    if snapshot is not None and loaded != snapshot:
        raise LibraryStopped('visual_library:content_ranges_missing_or_invalid:snapshot_changed')
    return loaded


def bind_content_ranges(catalog, snapshot):
    if snapshot:
        for source in catalog['sources']:
            source['content_range'] = deepcopy(snapshot['sources'][source['sha256']])
    return catalog


def content_range_prompt(sources):
    if isinstance(sources, dict):
        sources = sources.get('sources', [sources])
    rows = [dict(source_id=s.get('source_id'), content_range_s=content_range_for(s))
            for s in sources if content_range_for(s) is not None]
    return ('\n素材正文范围（原片秒数）：' + json.dumps(rows, ensure_ascii=False) +
            '。所有选择区间必须完全位于content_range_s内；禁止片头厂标/logos、标题卡/title cards、片尾字幕/end credits。\n') if rows else ''


def offer_content_windows(cards, catalog):
    sources = {s['sha256']: s for s in catalog['sources']}
    result = []
    for original in cards:
        source = sources[original['source_sha256']]
        bounds = content_range_for(source)
        if bounds is None:
            result.append(original)
            continue
        start = max(bounds[0], original['source_start_s'])
        end = min(bounds[1], original['source_end_s'])
        if start >= end:
            continue
        card = deepcopy(original)
        card.update(content_range=deepcopy(source['content_range']), usable_source_range_s=[start, end])
        result.append(card)
    return result
