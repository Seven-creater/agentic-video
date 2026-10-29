"""Reject impossible speech windows; measured timing handles borderline ones."""
from contextlib import contextmanager
from contextvars import ContextVar

_deferred_windows = ContextVar('deferred_plan_line_windows', default=None)

MIN_LINE_WINDOW_S = 0.7
SPOKEN_CHAR_SECONDS = 0.18


def min_line_window_s(text):
    """Hard floor max(.7, .18*n), not a typical TTS duration estimate.

    Server audit: 190 unique received lines, seconds/char p05 about .16-.24
    and p50 about .25; shortest 1-4 char lines .72s, 5-8 char lines .96s.
    Floors max(.7, a+b*n) falsely rejected 175/190 for .35+.30*n,
    78 for .3+.22*n, 41 for .3+.20*n, 28 for .2+.20*n, and 10 for .20*n.
    Use .18*n without padding to block only impossible windows; the .7s
    short-line floor still rejects "摔惨了。" in .45s. Exact text/voice
    receipts override this floor in validate_line_window in either direction.
    """
    spoken_chars = sum(c.isalnum() for c in text)
    return max(MIN_LINE_WINDOW_S, SPOKEN_CHAR_SECONDS * spoken_chars)


def draft_window_derivation():
    return ('窗口推导：按EDL顺序累计各段输出时长(source_out_s-source_in_s)/speed+freeze_tail_s'
        '（已有输出start_s/end_s时直接使用），将segment_ids完全相同且顺序相同的句子按草稿顺序分组，'
        '从首段起点到末段终点逐句分配，先令cursor=max(cursor,起声锚点所在段起点+offset_s)，'
        '若本句同时给出start_s/end_s则直接采用，否则本句获得(末段终点-cursor)×len(text)/'
        'sum(本句及组内后续句的len(text))秒（len含标点和空白，末句取全部剩余时间），并把cursor推进到本句终点。')


def line_window_prompt(lines=(), *, segments=(), received_tts=()):
    """Generate both prompt numbers and existing-line minima from the helper."""
    import json
    minima = {line['id']: required_line_window_s(line, received_tts=received_tts) for line in lines
              if line.get('mode') != 'none' and line.get('text')}
    return (f'\n每句可用窗口end_s-start_s必须≥max({MIN_LINE_WINDOW_S:.1f}, '
        f'{SPOKEN_CHAR_SECONDS:.2f} × spoken_chars)秒。'
        'spoken_chars仅计CJK汉字、字母和数字，不计标点或空白。'
        '写出每句text时按此规则计算并给出min_window_s，窗口不足须缩短文本或扩大窗口；'
        '引用多个相邻镜头时按剪辑后的实际时间计算，考虑speed、freeze_tail_s、start_anchor及同窗口其他句子。'
        'mode=none或无文本的行免除此下限。这仅拦截不可能的窗口，临界窗口交给实测timing revision；仍须实测TTS。'
        '例外：上下文已有同text、同voice的received_tts回执时，窗口以该实际时长为准；'
        '不得自行估计、编造回执或把旧文本时长用于新文本。'
        '当前各句最小窗口秒数：' + json.dumps(minima, ensure_ascii=False) + '\n' + draft_window_derivation() +
        '\naudio_draft每句已允许同时填写start_s/end_s，使用剪辑后输出时间轴上的绝对秒数，'
        '须落在所挂首段起点至末段终点内；不填写则采用上述推导，不是每句独享所挂镜头的全部时长。'
        '回复前按拟提交的segments和全部audio_draft重算；缩短一句也会改变同组其他句的分配。'
        '\n演算示例（仅演算，不是台词）：' + json.dumps(draft_line_window_details([
            dict(id='example_a', text='这是一条示例句。', segment_ids=['example']),
            dict(id='example_b', text='另一条示例句。', segment_ids=['example'])],
            [dict(segment_id='example', source_in_s=0, source_out_s=2, speed=1)]), ensure_ascii=False) +
        ('\n当前草稿窗口明细：' + json.dumps(draft_line_window_details(lines, segments,
            received_tts=received_tts), ensure_ascii=False) if segments else ''))


def validate_line_window(line, window_s, path, *, received_tts=()):
    if line.get('mode') == 'none' or not line.get('text') or window_s is None:
        return
    minimum = required_line_window_s(line, received_tts=received_tts)
    if window_s < minimum - 1e-6:
        from .contracts import numeric_error
        error = numeric_error('audio_line_window_below_minimum', path, window_s, minimum)
        error.violation['repair_hint'] = '缩短文本或扩大窗口（shorten the text or widen the window）；按修改后的text重算min_window_s。'
        raise error


def required_line_window_s(line, *, received_tts=()):
    minimum = min_line_window_s(line['text'])
    # An exact received waveform outranks a statistical estimate in either
    # direction. These receipts belong to the pinned input, never the reply.
    import math
    measured = [row.get('actual_duration_s') for row in received_tts
                if row.get('text') == line['text'] and row.get('voice') == line.get('voice')]
    measured = [duration for duration in measured if type(duration) in (int, float)
                and math.isfinite(duration) and duration > 0]
    if measured:
        minimum = max(measured)
    return minimum


def segment_output_timeline(segments):
    by_id, cursor = {}, 0.0
    for segment in segments:
        if 'start_s' in segment and 'end_s' in segment:
            start, end = segment['start_s'], segment['end_s']
        elif 'source_in_s' in segment and 'source_out_s' in segment:
            start = cursor
            end = start + (segment['source_out_s'] - segment['source_in_s']) / segment.get('speed', 1) + segment.get('freeze_tail_s', 0)
        else:
            return {}
        by_id[segment.get('segment_id', segment.get('id'))] = dict(start_s=start, end_s=end,
                                                                output_duration_s=end - start)
        cursor = end
    return by_id


def draft_line_windows(draft, segments):
    """Resolve authored windows or the same proportional slot split as rough audio.

    Plan segments use source seconds/speed/freeze; rendered segments use output
    seconds. Minimal identity-only contract callers have no timing to measure.
    """
    by_id = segment_output_timeline(segments)
    groups, result = {}, {}
    for line in draft:
        groups.setdefault(tuple(line['segment_ids']), []).append(line)
    for selected, lines in groups.items():
        if not selected or any(s not in by_id for s in selected):
            continue
        cursor, end = by_id[selected[0]]['start_s'], by_id[selected[-1]]['end_s']
        for index, line in enumerate(lines):
            anchor = line.get('start_anchor')
            if anchor:
                cursor = max(cursor, by_id[anchor['segment_id']]['start_s'] + anchor['offset_s'])
            if 'start_s' in line and 'end_s' in line:
                start, finish = line['start_s'], line['end_s']
            else:
                start = cursor
                remaining = sum(len(item.get('text', '')) for item in lines[index:])
                finish = (end if index == len(lines) - 1 or not remaining else
                          cursor + (end - cursor) * len(line.get('text', '')) / remaining)
            result[line['id']] = (start, finish)
            cursor = finish
    return result


def draft_line_window_details(draft, segments, *, received_tts=()):
    """Use the actual resolver for both preflight prompt numbers and rejections."""
    timeline = segment_output_timeline(segments)
    windows = draft_line_windows(draft, segments)
    details = []
    for index, line in enumerate(draft):
        if line['id'] not in windows or line.get('mode') == 'none' or not line.get('text'):
            continue
        start, end = windows[line['id']]
        details.append(dict(index=index, id=line['id'], path=f'$.audio_draft[{index}]', text=line['text'],
            spoken_chars=sum(c.isalnum() for c in line['text']), allocation_chars=len(line['text']),
            start_s=start, end_s=end, window_s=end - start,
            min_window_s=required_line_window_s(line, received_tts=received_tts),
            segment_ids=line['segment_ids'], segments=[dict(segment_id=ident, **timeline[ident])
                for ident in line['segment_ids']], derivation=draft_window_derivation()))
    return details


@contextmanager
def defer_plan_line_windows():
    """Locally collect this one rule while rechecking an exhausted reply in full."""
    warnings = []
    token = _deferred_windows.set(warnings)
    try:
        yield warnings
    finally:
        _deferred_windows.reset(token)


def validate_draft_line_windows(draft, segments, *, received_tts=()):
    offenders = []
    for row in draft_line_window_details(draft, segments, received_tts=received_tts):
        if row['window_s'] < row['min_window_s'] - 1e-6:
            offenders.append(dict(row, code='audio_line_window_below_minimum', value=row['window_s'],
                expected=row['min_window_s'], repair_hint='改挂到更长的连续片段、在已看可用范围内延长所挂片段、'
                '把本句移到更早或更晚的片段，或同时填写段落输出时间轴内的start_s/end_s显式窗口，'
                '或缩短文本（shorten the text or widen the window）；按全部句子重新推导，不能只修改min_window_s。'))
    if not offenders:
        return
    deferred = _deferred_windows.get()
    if deferred is not None:
        for row in offenders:
            if not any(item['path'] == row['path'] for item in deferred):
                deferred.append(row)
        return
    from .contracts import numeric_error
    first = offenders[0]
    error = numeric_error(first['code'], first['path'], first['value'], first['expected'])
    error.violation.update(repair_hint=first['repair_hint'], offending_lines=offenders)
    error.diagnostics = dict(line_window_warnings=offenders, derivation=draft_window_derivation())
    raise error
