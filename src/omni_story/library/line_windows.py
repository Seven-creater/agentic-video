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
    return ('窗口推导：按EDL顺序累计输出时间；有输出fps时每段帧数为'
        'round((source_out_s-source_in_s)/speed×fps)+round(freeze_tail_s×fps)，'
        '先分别取整运动与停帧的帧数，再累计整数帧数并除以fps得到段起止秒数；'
        '无fps的旧输入按(source_out_s-source_in_s)/speed+freeze_tail_s累计，'
        '已有实际输出start_s/end_s时直接使用。将segment_ids完全相同且顺序相同的句子按草稿顺序分组，'
        '从首段起点到末段终点逐句分配，先令cursor=max(cursor,起声锚点所在段起点+offset_s)，'
        '其他调用者若同时给出start_s/end_s则直接采用；计划阶段由程序移除显式时间。'
        '否则，若末段终点-cursor足够容纳本句及后续句的最小窗口总和，'
        '本句先获得required_line_window_s（同text/voice的received_tts实际时长优先，否则为下限），'
        '再获得剩余余量×len(text)/sum(本句及组内后续句的len(text))；'
        '若不足则退回原比例分配：(末段终点-cursor)×len(text)/sum(本句及后续句的len(text))。'
        'mode=none或无文本的行最小窗口为0；len含标点和空白，末句取全部剩余时间，cursor推进到本句终点。')


def line_window_prompt(lines=(), *, segments=(), received_tts=(), fps=None, program_arranged=False):
    """Generate both prompt numbers and existing-line minima from the helper."""
    import json
    minima = {line['id']: required_line_window_s(line, received_tts=received_tts) for line in lines
              if line.get('mode') != 'none' and line.get('text')}
    budgets = draft_line_window_groups(lines, segments, received_tts=received_tts, fps=fps)
    if program_arranged:
        budgets = [dict(group, lines=[{key: row[key] for key in ('id', 'text', 'min_window_s')}
                                     for row in group['lines']]) for group in budgets]
    return (f'\n每句可用窗口end_s-start_s必须≥max({MIN_LINE_WINDOW_S:.1f}, '
        f'{SPOKEN_CHAR_SECONDS:.2f} × spoken_chars)秒。'
        'spoken_chars仅计CJK汉字、字母和数字，不计标点或空白。'
        'min_window_s由程序计算，窗口不足须缩短文本或扩大所挂片段；'
        '程序按剪辑后的实际时间计入speed、freeze_tail_s、start_anchor及同窗口其他句子。'
        'mode=none或无文本的行免除此下限。这仅拦截不可能的窗口，临界窗口交给实测timing revision；仍须实测TTS。'
        '例外：上下文已有同text、同voice的received_tts回执时，窗口以该实际时长为准；'
        '不得自行估计、编造回执或把旧文本时长用于新文本。'
        '当前各句最小窗口秒数：' + json.dumps(minima, ensure_ascii=False) +
        ('\n计划阶段台词窗口由程序安排；模型只选择镜头、每句挂载的segment_ids和text，'
         '保留所需mode、speaker和voice，不填写或计算audio_draft的start_s/end_s。'
         '每组完全相同且有序的segment_ids共享所挂片段的总窗口；保持span_s≥demand_s。'
         '不足时缩短文本，或挂到更长、更多连续片段；source_in_s/source_out_s始终是素材时间域，'
         '不能填输出时间轴秒数。' if program_arranged else '') + '\n' + draft_window_derivation() +
        '\n当前分组窗口预算：' + json.dumps(budgets, ensure_ascii=False) +
        ('\n当前草稿窗口明细：' + json.dumps(draft_line_window_details(lines, segments,
            received_tts=received_tts, fps=fps), ensure_ascii=False) if segments and not program_arranged else ''))


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
    # An exact received waveform outranks a statistical estimate in either
    # direction. These receipts belong to the pinned input, never the reply.
    measured = received_line_duration_s(line, received_tts=received_tts)
    return measured if measured is not None else min_line_window_s(line['text'])


def received_line_duration_s(line, *, received_tts=()):
    """The measured part of the shared prompt/validator window contract."""
    import math
    measured = [row.get('actual_duration_s') for row in received_tts
                if row.get('text') == line['text'] and row.get('voice') == line.get('voice')]
    measured = [duration for duration in measured if type(duration) in (int, float)
                and math.isfinite(duration) and duration > 0]
    return max(measured) if measured else None


def segment_output_timeline(segments, *, fps=None):
    by_id, cursor, cursor_frames = {}, 0.0, 0
    for segment in segments:
        if fps is not None and 'source_in_s' in segment and 'source_out_s' in segment:
            from .render import output_frame_counts
            motion, hold = output_frame_counts(
                (segment['source_out_s'] - segment['source_in_s']) / segment.get('speed', 1),
                segment.get('freeze_tail_s', 0), fps)
            start, end = cursor_frames / fps, (cursor_frames + motion + hold) / fps
        elif 'start_s' in segment and 'end_s' in segment:
            start, end = segment['start_s'], segment['end_s']
        elif 'source_in_s' in segment and 'source_out_s' in segment:
            start = cursor
            end = start + (segment['source_out_s'] - segment['source_in_s']) / segment.get('speed', 1) + segment.get('freeze_tail_s', 0)
        else:
            return {}
        by_id[segment.get('segment_id', segment.get('id'))] = dict(start_s=start, end_s=end,
                                                                output_duration_s=end - start)
        cursor = end
        if fps is not None:
            cursor_frames = round(end * fps)
    return by_id


def draft_line_windows(draft, segments, *, received_tts=(), fps=None):
    """Resolve authored windows or reserve minima before sharing the spare time.

    Plan segments use the renderer's frame counts when fps is supplied; rendered
    segments use actual output seconds. Identity-only callers have no timing.
    """
    by_id = segment_output_timeline(segments, fps=fps)
    if not by_id:
        return {}
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
                minima = [_line_minimum(item, received_tts) for item in lines[index:]]
                span, demand = end - cursor, sum(minima)
                share = len(line.get('text', '')) / remaining if remaining else 0
                allocation = (minima[0] + max(0, span - demand) * share
                              if span >= demand - 1e-6 else span * share)
                finish = end if index == len(lines) - 1 or not remaining else cursor + allocation
            result[line['id']] = (start, finish)
            cursor = finish
    return result


def _line_minimum(line, received_tts):
    return (required_line_window_s(line, received_tts=received_tts)
            if line.get('mode') != 'none' and line.get('text') else 0)


def draft_line_window_details(draft, segments, *, received_tts=(), fps=None):
    """Use the actual resolver for both preflight prompt numbers and rejections."""
    timeline = segment_output_timeline(segments, fps=fps)
    windows = draft_line_windows(draft, segments, received_tts=received_tts, fps=fps)
    details = []
    for index, line in enumerate(draft):
        if line['id'] not in windows or line.get('mode') == 'none' or not line.get('text'):
            continue
        start, end = windows[line['id']]
        details.append(dict(index=index, id=line['id'], path=f'$.audio_draft[{index}]', text=line['text'],
            spoken_chars=sum(c.isalnum() for c in line['text']), allocation_chars=len(line['text']),
            start_s=start, end_s=end, window_s=end - start,
            allowed_start_s=timeline[line['segment_ids'][0]]['start_s'],
            allowed_end_s=timeline[line['segment_ids'][-1]]['end_s'],
            min_window_s=required_line_window_s(line, received_tts=received_tts),
            segment_ids=line['segment_ids'], segments=[dict(segment_id=ident, **timeline[ident])
                for ident in line['segment_ids']], derivation=draft_window_derivation()))
    return details


def line_window_extension_options(segments, windows=(), *, catalog=None):
    """Only ranges already supporting the selection can supply extra motion."""
    from . import contracts
    lookup = windows if isinstance(windows, dict) else {w['window_id']: w for w in windows}
    sources = {s['source_id']: s for s in (catalog or {}).get('sources', [])}
    result = {}
    for index, segment in enumerate(segments):
        window = lookup.get(segment.get('window_id'))
        if not window or window.get('status') != 'watched':
            continue
        speed = segment.get('speed', 1)
        if not contracts.finite_number(speed) or speed <= 0:
            continue
        choices = contracts.plan_source_range_contract(window, sources.get(window.get('source_id')))
        supported = contracts.matching_plan_source_ranges(segment, choices)
        if not supported:
            continue
        try:
            contracts.validate_plan_segment_roles(segment, window, f'$.segments[{index}]')
        except ValueError:
            continue
        start, end = segment['source_in_s'], segment['source_out_s']
        options = []
        for usable in supported:
            low, high = usable['source_in_s'], usable['source_out_s']
            # Do not consume the adjacent cut's footage when it continues this
            # source in source order, even if it uses another watched window.
            if index and segments[index - 1].get('source_id') == segment.get('source_id'):
                previous = segments[index - 1].get('source_out_s')
                if contracts.finite_number(previous) and previous <= start + .001:
                    low = max(low, previous)
            if index + 1 < len(segments) and segments[index + 1].get('source_id') == segment.get('source_id'):
                following = segments[index + 1].get('source_in_s')
                if contracts.finite_number(following) and following >= end - .001:
                    high = min(high, following)
            options.append(dict(usable_range_index=usable['usable_range_index'],
                source_in_s=low, source_out_s=high,
                extend_before_s=max(0, start - low) / speed,
                extend_after_s=max(0, high - end) / speed))
        result[segment['segment_id']] = options
    return result


def draft_line_window_groups(draft, segments, *, received_tts=(), fps=None, windows=(), catalog=None):
    """Shared group budgets for planning, normalisation and rejection feedback."""
    details = draft_line_window_details(draft, segments, received_tts=received_tts, fps=fps)
    grouped = {}
    for row in details:
        grouped.setdefault(tuple(row['segment_ids']), []).append(row)
    extensions = line_window_extension_options(segments, windows, catalog=catalog) if windows else {}
    result = []
    for selected, lines in grouped.items():
        span = lines[0]['allowed_end_s'] - lines[0]['allowed_start_s']
        demand = sum(row['min_window_s'] for row in lines)
        result.append(dict(segment_ids=list(selected), span_s=span, demand_s=demand,
            summed_min_window_s=demand, slack_s=max(0, span - demand), deficit_s=max(0, demand - span),
            lines=lines, extendable_seconds={ident: extensions[ident] for ident in selected if ident in extensions}))
    return result


@contextmanager
def defer_plan_line_windows():
    """Locally collect this one rule while rechecking an exhausted reply in full."""
    warnings = []
    token = _deferred_windows.set(warnings)
    try:
        yield warnings
    finally:
        _deferred_windows.reset(token)


def validate_draft_line_windows(draft, segments, *, received_tts=(), fps=None, windows=(), catalog=None):
    from .contracts import numeric_error
    details = draft_line_window_details(draft, segments, received_tts=received_tts, fps=fps)
    outside = [dict(row, code='plan:audio_line_window_outside_segments',
        value=dict(start_s=row['start_s'], end_s=row['end_s']),
        expected=f'{row["allowed_start_s"]} ≤ start_s < end_s ≤ {row["allowed_end_s"]}',
        repair_hint='窗口须落在所挂首段起点至末段终点内；调整start_anchor或所挂连续片段，'
            '计划阶段由程序重新安排台词窗口。')
        for row in details if not row['allowed_start_s'] - 1e-6 <= row['start_s'] < row['end_s'] <= row['allowed_end_s'] + 1e-6]
    # Containment is a structural invariant, never a statistical floor warning.
    if outside:
        first = outside[0]
        error = numeric_error(first['code'], first['path'], first['value'], first['expected'])
        error.violation.update(repair_hint=first['repair_hint'], offending_lines=outside)
        error.diagnostics = dict(line_window_errors=outside, derivation=draft_window_derivation())
        raise error
    offenders = []
    for group in draft_line_window_groups(draft, segments, received_tts=received_tts, fps=fps,
                                         windows=windows, catalog=catalog):
        if any(row['window_s'] < row['min_window_s'] - 1e-6 for row in group['lines']):
            import math
            deficit = group['deficit_s'] or sum(max(0, row['min_window_s'] - row['window_s'])
                                              for row in group['lines'])
            offenders.append(dict(group, code='audio_line_window_below_minimum',
                path=group['lines'][0]['path'], value=group['span_s'], expected=group['demand_s'],
                spoken_chars_to_cut=math.ceil(max(0, deficit - 1e-9) / SPOKEN_CHAR_SECONDS),
                repair_hint='计划阶段台词窗口由程序安排；改挂到更长或更多连续片段，'
                    '在同一条已看可用范围内延长片段，或缩短文本（shorten the text or widen the window）；'
                    '程序按整组重新计算min_window_s、demand_s和余量，不能只修改min_window_s。'))
    if not offenders:
        return
    deferred = _deferred_windows.get()
    if deferred is not None:
        for row in offenders:
            if not any(item['path'] == row['path'] for item in deferred):
                deferred.append(row)
        return
    first = offenders[0]
    error = numeric_error(first['code'], first['path'], first['value'], first['expected'])
    error.violation.update(first, offending_groups=offenders)
    error.diagnostics = dict(line_window_warnings=offenders, derivation=draft_window_derivation())
    raise error
