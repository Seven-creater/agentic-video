"""Reject impossible speech windows; measured timing handles borderline ones."""

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


def line_window_prompt(lines=()):
    """Generate both prompt numbers and existing-line minima from the helper."""
    import json
    minima = {line['id']: min_line_window_s(line['text']) for line in lines
              if line.get('mode') != 'none' and line.get('text')}
    return (f'\n每句可用窗口end_s-start_s必须≥max({MIN_LINE_WINDOW_S:.1f}, '
        f'{SPOKEN_CHAR_SECONDS:.2f} × spoken_chars)秒。'
        'spoken_chars仅计CJK汉字、字母和数字，不计标点或空白。'
        '写出每句text时按此规则计算并给出min_window_s，窗口不足须缩短文本或扩大窗口；'
        '引用多个相邻镜头时按剪辑后的实际时间计算，考虑speed、freeze_tail_s、start_anchor及同窗口其他句子。'
        'mode=none或无文本的行免除此下限。这仅拦截不可能的窗口，临界窗口交给实测timing revision；仍须实测TTS。'
        '例外：上下文已有同text、同voice的received_tts回执时，窗口以该实际时长为准；'
        '不得自行估计、编造回执或把旧文本时长用于新文本。'
        '当前各句最小窗口秒数：' + json.dumps(minima, ensure_ascii=False))


def validate_line_window(line, window_s, path, *, received_tts=()):
    if line.get('mode') == 'none' or not line.get('text') or window_s is None:
        return
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
    if window_s < minimum - 1e-6:
        from .contracts import numeric_error
        error = numeric_error('audio_line_window_below_minimum', path, window_s, minimum)
        error.violation['repair_hint'] = '缩短文本或扩大窗口（shorten the text or widen the window）；按修改后的text重算min_window_s。'
        raise error


def draft_line_windows(draft, segments):
    """Resolve authored windows or the same proportional slot split as rough audio.

    Plan segments use source seconds/speed/freeze; rendered segments use output
    seconds. Minimal identity-only contract callers have no timing to measure.
    """
    by_id, cursor = {}, 0.0
    for segment in segments:
        if 'start_s' in segment and 'end_s' in segment:
            start, end = segment['start_s'], segment['end_s']
        elif 'source_in_s' in segment and 'source_out_s' in segment:
            start = cursor
            end = start + (segment['source_out_s'] - segment['source_in_s']) / segment.get('speed', 1) + segment.get('freeze_tail_s', 0)
        else:
            return {}
        by_id[segment.get('segment_id', segment.get('id'))] = dict(start_s=start, end_s=end)
        cursor = end
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
