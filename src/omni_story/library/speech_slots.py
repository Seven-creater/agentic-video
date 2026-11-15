"""CPU speech candidates, then picture-only confirmation of visible mouths."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
import subprocess

from .content_ranges import content_range_for
from .media import _run, validate_window, verify_source
from .state import json_sha, write_json


MAX_S = 6
PAUSE_S = .25
JOIN_GAP_S = .3
STATIC_MARGIN_S = .25


def _cache_path(source, start, end, cache, kind):
    spec = dict(policy='speech-slots-v1', source_sha256=source['sha256'],
        source_start_s=float(start), source_end_s=float(end),
        audio_stream_index=source.get('audio_stream_index'), video_stream_index=source.get('video_stream_index', 0))
    folder = Path(cache) / ('speech_slots_' + json_sha(spec)[:20])
    folder.mkdir(parents=True, exist_ok=True)
    return folder / kind


def detect_speech(source, start, end, cache):
    """Return VAD intervals in seconds relative to the extracted audio."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    path = verify_source(source)
    start, end = validate_window(source, start, end)
    audio = _cache_path(source, start, end, cache, 'audio.wav')
    if not audio.exists():
        partial = audio.with_suffix('.part.wav')
        _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.6f}', '-i', str(path),
            '-t', f'{end-start:.6f}', '-map', f"0:{source['audio_stream_index']}", '-vn',
            '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(partial)])
        partial.replace(audio)
    rows = get_speech_timestamps(decode_audio(str(audio), sampling_rate=16000),
        vad_options=VadOptions(min_silence_duration_ms=250, speech_pad_ms=30))
    return [[r['start'] / 16000, r['end'] / 16000] for r in rows]


def detect_cuts(source, start, end, cache):
    """Return shot cuts in seconds relative to the selected source range."""
    path = verify_source(source)
    start, end = validate_window(source, start, end)
    saved = _cache_path(source, start, end, cache, 'cuts.json')
    if saved.exists():
        return json.loads(saved.read_text(encoding='utf-8'))
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'info', '-threads', '1',
        '-filter_threads', '1', '-ss', f'{start:.6f}', '-t', f'{end-start:.6f}', '-i', str(path),
        '-map', f"0:{source.get('video_stream_index', 0)}", '-an', '-sn', '-dn',
        '-vf', "setpts=PTS-STARTPTS,select='gt(scene,0.3)',showinfo",
        '-fps_mode', 'passthrough', '-f', 'null', '-'], capture_output=True, timeout=900)
    log = result.stderr.decode('utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(log[-4000:])
    cuts = sorted({round(float(t), 6) for t in re.findall(r'\bpts_time:([\d.eE+\-]+)', log)
                   if 0 < float(t) < end-start})
    write_json(saved, cuts)
    return cuts


def _merge(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def _pieces(spans):
    start, end = spans[0][0], spans[-1][1]
    if end-start <= MAX_S:
        return [(start, end, [[a[1], b[0]] for a, b in zip(spans, spans[1:])
                             if round(b[0]-a[1], 6) >= PAUSE_S])]
    if len(spans) > 1:
        split = max(range(1, len(spans)), key=lambda i: (round(spans[i][0]-spans[i-1][1], 6), -i))
        return _pieces(spans[:split]) + _pieces(spans[split:])
    count = math.ceil((end-start) / MAX_S)
    edges = [round(start + (end-start)*i/count, 6) for i in range(count+1)]
    return [(a, b, []) for a, b in zip(edges, edges[1:])]


def build_candidates(scene, spans, cuts=(), *, origin='vad'):
    """Inputs and output use source seconds; nearby VAD spans retain pauses."""
    allowed = scene['allowed_ranges'][0]
    low, high = allowed['source_start_s'], allowed['source_end_s']
    spans = _merge([[round(max(low, a), 6), round(min(high, b), 6)] for a, b in spans
                    if max(low, a) < min(high, b)])
    edges = [low, *sorted({c for c in cuts if low < c < high}), high]
    candidates = []
    for left, right in zip(edges, edges[1:]):
        parts = [[max(left, a), min(right, b)] for a, b in spans
                 if min(right, b) > max(left, a)]
        groups = []
        for part in parts:
            if groups and round(part[0]-groups[-1][-1][1], 6) <= JOIN_GAP_S:
                groups[-1].append(part)
            else:
                groups.append([part])
        for group in groups:
            for start, end, pauses in _pieces(group):
                candidates.append(dict(allowed, source_start_s=start, source_end_s=end,
                    pauses=pauses, origin=origin,
                    candidate_id=f'{scene["scene_id"]}_candidate_{len(candidates)+1}'))
    visible = [rng for role in sorted(scene['role_bindings'])
               for rng in scene.get('picture', {}).get('roles', {}).get(role, {}).get('visible_ranges', [])]
    candidates.sort(key=lambda c: (
        -any(a < c['source_end_s'] and c['source_start_s'] < b for a, b in visible),
        -(c['source_end_s']-c['source_start_s']), c['source_start_s'], c['candidate_id']))
    # Reserve evidence for each role before long utterances fill the 12-clip
    # review batch. A 0.3 s supporting-role mouth must not lose to 12 long leads.
    selected = []
    for role in sorted(scene['role_bindings']):
        observed = scene.get('picture', {}).get('roles', {}).get(role, {})
        ranges = observed.get('speech_ranges') or observed.get('visible_ranges', [])
        overlap = lambda c: sum(max(0, min(b, c['source_end_s'])-max(a, c['source_start_s'])) for a, b in ranges)
        best = max(candidates, key=overlap, default=None)
        if best is not None and overlap(best) > 0 and best not in selected:
            selected.append(best)
    selected += [c for c in candidates if c not in selected][:12-len(selected)]
    return sorted(selected, key=lambda c: (c['source_start_s'], c['source_end_s'], c['candidate_id']))


def scene_candidates(scene, source, cache, *, vad=detect_speech, cut_detector=detect_cuts):
    allowed = scene['allowed_ranges'][0]
    low, high = content_range_for(source) or (0, source['duration_s'])
    start, end = max(low, allowed['source_start_s']), min(high, allowed['source_end_s'])
    if end <= start:
        return []
    bounded = dict(scene, allowed_ranges=[dict(allowed, source_start_s=start, source_end_s=end)])
    if source.get('audio_stream_index') is None:
        spans = []
        for role in sorted(scene['role_bindings']):
            rows = scene.get('speech_ranges', {}).get(role)
            if rows is None:
                rows = scene.get('picture', {}).get('roles', {}).get(role, {}).get('speech_ranges', [])
            for row in rows:
                if isinstance(row, dict):
                    if row['source_sha256'] == source['sha256']:
                        spans.append([row['source_start_s'], row['source_end_s']])
                else:
                    spans.append(row)
        origin = 'check'
    else:
        spans = [[round(start+a, 6), round(start+b, 6)] for a, b in vad(source, start, end, cache)]
        origin = 'vad'
    if not spans:
        return []
    cuts = [round(start+t, 6) for t in cut_detector(source, start, end, cache)]
    return build_candidates(bounded, spans, cuts, origin=origin)


def _note(row, message):
    if not isinstance(row.get('normalised'), list):
        row['normalised'] = []
    if message not in row['normalised']:
        row['normalised'].append(message)


def validate_slots(value, candidates, entries, roles):
    from .scene_route import _clip_to_source, _fail, _number

    rows = value.get('slots') if isinstance(value, dict) else None
    expected = {c['candidate_id']: c for c in candidates}
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        _fail('scene_slots', 'slots须为对象数组', list(expected))
    by_id = {}
    names = {''.join(role.split()).casefold(): role for role in [*sorted(roles), 'unknown', 'none']}
    synonyms = {'是': True, '否': False, 'yes': True, 'no': False, 'true': True, 'false': False}
    for row in rows:
        ident = row.get('candidate_id')
        if not isinstance(ident, str) or ident not in expected:
            _fail('scene_slots', '未知candidate_id', list(expected))
        if ident in by_id:
            _note(by_id[ident], '重复candidate_id，保留首行')
            continue
        by_id[ident] = row
        if 'normalised' in row and not isinstance(row['normalised'], list):
            _note(row, 'normalised规范为数组')
        for key in ('mouth_visible', 'clear', 'loopable'):
            raw = row.get(key)
            if type(raw) is not bool:
                row[key] = synonyms.get(raw.strip().casefold(), False) if isinstance(raw, str) else False
                _note(row, f'{key}={raw!r}规范为{row[key]}')
        speaker = row.get('speaker')
        canonical = names.get(''.join(speaker.split()).casefold()) if isinstance(speaker, str) else None
        if canonical != speaker or canonical is None:
            _note(row, f'speaker={speaker!r}规范为{canonical or "unknown"}')
        row['speaker'] = canonical or 'unknown'
        gender = row.get('gender')
        row['gender'] = gender.strip().casefold() if isinstance(gender, str) else 'unclear'
        if row['gender'] not in ('male', 'female', 'unclear'):
            row['gender'] = 'unclear'
        if row['speaker'] in ('none', 'unknown'):
            if row.get('mouth_start_s') is not None or row.get('mouth_end_s') is not None:
                _note(row, 'none/unknown的动嘴区间置空')
            row['mouth_start_s'] = row['mouth_end_s'] = None
            continue
        rng = [row.get('mouth_start_s'), row.get('mouth_end_s')]
        if not all(_number(t) for t in rng) or rng[0] >= rng[1]:
            row['mouth_start_s'] = row['mouth_end_s'] = None
            _note(row, '动嘴区间无效，未确认')
            continue
        c = expected[ident]
        start, end = c['source_start_s'], c['source_end_s']
        # Near-boundary source times must not be mistaken for joined-clip seconds.
        if not (start-.5 <= rng[0] < rng[1] <= end+.5):
            rng = _clip_to_source(rng, [e for e in entries if e['candidate_id'] == ident], [[start, end]], row)
        if not (start-.5 <= rng[0] < rng[1] <= end+.5):
            row['mouth_start_s'] = row['mouth_end_s'] = None
            _note(row, '动嘴区间不在候选附近，未确认')
            continue
        clamped = [max(start, rng[0]), min(end, rng[1])]
        if clamped != rng:
            _note(row, f'动嘴区间{rng}夹到候选{clamped}')
        row['mouth_start_s'], row['mouth_end_s'] = clamped
    for ident in expected:
        if ident not in by_id:
            by_id[ident] = dict(candidate_id=ident, speaker='none', gender='unclear', mouth_visible=False, clear=False, loopable=False,
                mouth_start_s=None, mouth_end_s=None, reason='missing_candidate', normalised=['缺少候选，未确认'])
    value['slots'] = [by_id[ident] for ident in expected]
    return value['slots']


def shares_character(left, right):
    appearance = left.get('appearance')
    return bool(appearance and appearance == right.get('appearance')) or (
        left['window_id'] == right['window_id'] and left['role_id'] == right['role_id'])


def confirmed_slots(scene, candidates, rows):
    slots, unconfirmed = [], []
    by_id = {r['candidate_id']: r for r in rows}
    for c in candidates:
        row = by_id[c['candidate_id']]
        start, end = row['mouth_start_s'], row['mouth_end_s']
        reason = ('missing_candidate' if row.get('reason') == 'missing_candidate' else
                  'speaker_unknown' if row['speaker'] in ('none', 'unknown') else
                  'mouth_not_visible' if not row['mouth_visible'] else
                  'unclear' if not row['clear'] else
                  'invalid_mouth_range' if start is None or end is None or start >= end else None)
        if reason:
            unconfirmed.append({**c, **row, 'reason': reason})
            continue
        slots.append(dict(slot_id='speech_' + c['candidate_id'],
            role_id=row['speaker'], gender=row['gender'], window_id=c['window_id'], source_sha256=c['source_sha256'],
            source_start_s=start, source_end_s=end, pauses=[[max(start, a), min(end, b)] for a, b in c['pauses']
                if round(min(end, b)-max(start, a), 6) >= PAUSE_S],
            mouth_visible=True, loopable=False, origin=c['origin'], candidate_id=c['candidate_id']))
    bind_slot_roles(scene, slots)
    return slots, unconfirmed


def bind_slot_roles(scene, slots):
    stories = {binding['role_id']: story for story, binding in scene['role_bindings'].items()}
    for slot in slots:
        slot.pop('role', None)
        if slot['role_id'] in stories:
            slot['role'] = stories[slot['role_id']]


def slots_prompt(entries, hints):
    return ('只看画面，逐候选确认谁有可供配音的连续嘴形运动；不要求原片正在说话，原声不能作为证据。'
        '须看到相邻画面嘴形变化，咀嚼等连续动嘴也可用；闭嘴静止、保持张嘴或静止微笑不可用。'
        'characters列出本窗口角色的label和appearance外观，按物种、衣服、帽子认人。'
        'speaker填实际动嘴者的label，无人动嘴填none，无法辨认填unknown。不要按电影人物名或窗口字母猜身份。'
        'gender填该说话人看上去的性别male/female/unclear。'
        'mouth_visible表示说话人的嘴/脸清楚可见；clear表示判断明确。'
        'loopable仅在固定机位、固定视角、人物留在原地时为true；表情、点头、原地肢体语言都允许。'
        '人物走动换位置、跳跃、切镜、视角变化或镜头移动均为false；时长不是循环资格条件。'
        'mouth_start_s/mouth_end_s填可见说话人的嘴实际运动的原片起止秒；none/unknown时填null。'
        'entries映射短片秒与原片秒，动嘴区间须在对应候选内。只返回JSON '
        '{"slots":[{"candidate_id":"候选ID","speaker":"none","gender":"unclear","mouth_visible":false,'
        '"mouth_start_s":null,"mouth_end_s":null,"clear":false,"loopable":false}]}。\n' +
        json.dumps(dict(entries=entries, characters=[dict(label=label, appearance=r['appearance'])
            for label, r in hints.items()]), ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def edge_sheet(source, slot, cache, *, subtitle_masks=None):
    """One subtitle-free sheet, sampling the whole slot at 6 fps (at most 36 cells)."""
    from PIL import Image, ImageDraw, ImageFont

    spec = dict(policy='speech-edges-v3', source=source['sha256'], slot=slot, subtitle_masks=subtitle_masks)
    folder = Path(cache) / ('speech_edges_' + json_sha(spec)[:20])
    folder.mkdir(parents=True, exist_ok=True)
    sheet = folder / 'edges.jpg'
    metadata = folder / 'cells.json'
    def lineage():
        from .media import sha256_file
        write_json(folder/'lineage.json', dict(kind='sparse_contact_sheet', source_sha256=source['sha256'],
            source_start_s=slot['source_start_s'], source_end_s=slot['source_end_s'],
            path=str(sheet), sha256=sha256_file(sheet)))
    if sheet.exists() and metadata.exists():
        lineage()
        return sheet, json.loads(metadata.read_text())
    path = verify_source(source)
    start, end = slot['source_start_s'], slot['source_end_s']
    count = math.ceil((end-start)*6-1e-6)
    samples = list(range(count)) if count <= 36 else [round(i*(count-1)/35) for i in range(36)]
    crop_bottom = min((m['y'] for m in (subtitle_masks or [])
                       if m['start_s'] < end and start < m['end_s']), default=.78)
    filters = ['fps=6:start_time=0:round=up']
    if count > 36:
        filters.append("select='" + '+'.join(f'eq(n,{i})' for i in samples) + "'")
    filters.append('scale=480:-1')
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start:.6f}', '-t', f'{end-start:.6f}',
          '-i', str(path), '-map', f"0:{source.get('video_stream_index', 0)}", '-an',
          '-vf', ','.join(filters), '-fps_mode', 'vfr', '-frames:v', str(len(samples)), str(folder/'%02d.png')])
    try:
        font = ImageFont.truetype('DejaVuSans.ttf', 32)
    except OSError:
        font = ImageFont.load_default(size=32)
    cells, pictures = [], []
    for i, frame in enumerate(sorted(folder.glob('*.png'))[:len(samples)]):
        cells.append(dict(cell=i+1, time_s=round(start+samples[i]/6, 6)))
        with Image.open(frame) as img:
            pictures.append(img.convert('RGB'))
    height, label_height = pictures[0].height, 44
    canvas = Image.new('RGB', (480*4, (height+label_height)*math.ceil(len(pictures)/4)), 'black')
    draw = ImageDraw.Draw(canvas)
    for i, frame in enumerate(pictures):
        x, y = (i % 4)*480, (i // 4)*(height+label_height)
        draw.text((x+8, y+3), str(i+1), font=font, fill='yellow')
        canvas.paste(frame, (x, y+label_height))
        draw.rectangle((x, y+label_height+round(height*crop_bottom), x+479, y+label_height+height-1), fill='black')
    canvas.save(sheet, quality=90)
    entry = dict(slot_id=slot['slot_id'], cells=cells)
    write_json(metadata, entry)
    lineage()
    return sheet, entry


def static_bounds(value, start, end):
    """GLM's source-time interval, with a fixed tail allowance."""
    span = value.get('static_range')
    if not (isinstance(span, list) and len(span) == 2 and
            all(type(t) in (int, float) and math.isfinite(t) for t in span) and span[0] < span[1]):
        return None
    low, high = max(start, span[0]), min(end, span[1])
    # Keep short confirmed intervals too; a fixed 0.25 s cut must not erase them.
    high -= min(STATIC_MARGIN_S, max(0, high-low)/4)
    return [round(low, 6), round(high, 6)] if low < high else None


def known_static_speakers(registry_path):
    """Cross-template retrieval hints; every selected window is reviewed again."""
    registry = Path(registry_path)
    if not registry.is_file():
        return {}
    runs = json.loads(registry.read_text())
    found = {}
    for output in sorted({r.get('output') for r in runs.values() if isinstance(r, dict)} - {None}):
        report = Path(output)/'speech_slots.json'
        if not report.is_file():
            continue
        try:
            scenes = json.loads(report.read_text()).get('scenes', [])
        except (OSError, json.JSONDecodeError):
            continue  # A still-writing report supplies no reusable hint yet.
        for scene in scenes:
            if not scene.get('edge_reviews'):
                continue
            for slot in scene.get('speech_slots', []):
                if not (slot.get('mouth_visible') is True and slot.get('loopable') is True and slot.get('role_id')):
                    continue
                safe = static_bounds(slot, slot['source_start_s'], slot['source_end_s'])
                loop = slot.get('loop_range')
                if not (safe and loop and safe[0]-1e-6 <= loop[0] < loop[1] <= safe[1]+1e-6):
                    continue
                roles = found.setdefault(slot['source_sha256'], {}).setdefault(slot['window_id'], [])
                if slot['role_id'] not in roles:
                    roles.append(slot['role_id'])
    return found


def validate_edges(value, entry, *, require_motion=False):
    from .scene_route import _fail, _number

    if not isinstance(value, dict) or not isinstance(value.get('talking'), list):
        _fail('scene_edges', '须为含talking数组的对象', [entry['slot_id']])
    if 'static_ranges' in value:
        spans = value['static_ranges']
        if not isinstance(spans, list) or any(not (isinstance(r, list) and len(r) == 2 and
                all(_number(t) for t in r) and r[0] < r[1]) for r in spans):
            _fail('scene_edges', 'static_ranges须为原片起止秒数组，无静态段填[]', [entry['slot_id']])
    synonyms = {'是': True, '否': False, 'yes': True, 'no': False, 'true': True, 'false': False}
    def boolean(raw, label):
        if type(raw) is bool:
            return raw
        result = synonyms.get(raw.strip().casefold(), False) if isinstance(raw, str) else False
        _note(value, f'{label}={raw!r}规范为{result}')
        return result
    value['speaker_ok'] = boolean(value.get('speaker_ok'), 'speaker_ok')
    talking = [boolean(raw, f'talking[{i}]') for i, raw in enumerate(value['talking'])]
    count = len(entry['cells'])
    if len(talking) != count:
        _note(value, f'talking长度{len(talking)}规范为{count}（不足补false，超出截断）')
    value['talking'] = (talking + [False]*max(0, count-len(talking)))[:count]
    if require_motion or 'fixed_view' in value or 'in_place' in value:
        for field in ('fixed_view', 'in_place'):
            flags = value.get(field)
            if not isinstance(flags, list) or len(flags) != max(0, count-1) or any(type(f) is not bool for f in flags):
                _fail('scene_edges', f'{field}须按相邻格对填写{max(0, count-1)}个布尔值', entry)
        confirmed = []
        for i, (fixed, placed) in enumerate(zip(value['fixed_view'], value['in_place'])):
            if not (fixed and placed):
                continue
            low, high = entry['cells'][i]['time_s'], entry['cells'][i+1]['time_s']
            if confirmed and confirmed[-1][1] == low:
                confirmed[-1][1] = high
            else:
                confirmed.append([low, high])
        claimed = value.get('static_ranges', [value['static_range']] if value.get('static_range') else [])
        value.setdefault('claimed_static_ranges', deepcopy(claimed))
        value['static_ranges'] = [[max(a, low), min(b, high)] for a, b in claimed
            for low, high in confirmed if max(a, low) < min(b, high)]
    return value


def trim_edges(slot, entry, reply):
    row = validate_edges(reply, entry)
    runs = []
    for i, talking in enumerate(row['talking']):
        if not talking:
            continue
        if runs and i-runs[-1][1] <= 2:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    cells = entry['cells']
    before = [slot['source_start_s'], slot['source_end_s']]
    after = None
    if runs:
        first, last = max(runs, key=lambda r: cells[r[1]]['time_s']-cells[r[0]]['time_s'])
        after = [round(max(before[0], cells[first]['time_s']), 6),
                 round(min(before[1], cells[last]['time_s']+1/6), 6)]
    reason = ('edge_speaker_mismatch' if not row['speaker_ok'] else
              'edge_unconfirmed' if after is None else None)
    reviews = [dict(slot_id=slot['slot_id'], before=before, after=after, dropped=bool(reason), reply=row)]
    if reason:
        return [], reviews, [dict(slot, reason=reason)]
    spans = row.get('static_ranges', [row.get('static_range')])
    kept = []
    for span in spans:
        for first, last in runs:
            start, end = max(before[0], cells[first]['time_s']), min(before[1], cells[last]['time_s']+1/6)
            loop = static_bounds(dict(static_range=span), start, end)
            if loop:
                kept.append(dict(slot, slot_id=slot['slot_id'] + (f'_static_{len(kept)+1}' if kept else ''),
                    source_start_s=max(start, span[0]), source_end_s=min(end, span[1]),
                    static_range=span, loop_range=loop, loopable=True, pauses=[]))
    if kept:
        return kept, reviews, []
    start, end = after
    return [dict(slot, source_start_s=start, source_end_s=end, static_range=None,
        loop_range=None, loopable=False, pauses=[[max(start, a), min(end, b)] for a, b in slot['pauses']
            if round(min(end, b)-max(start, a), 6) >= PAUSE_S])], reviews, []


def review_edges(route, slots, hints, index, *, subtitle_masks=None):
    kept, reviews, dropped = [], [], []
    appearances = {r['role_id']: r['appearance'] for r in hints.values()}
    for slot in slots:
        sheet, entry = edge_sheet(route.sources[slot['source_sha256']], slot, route.cache,
                                  subtitle_masks=subtitle_masks)
        prompt = ('图中只有一个片段，格子按编号顺序覆盖全段。目标角色外观见appearance。'
            '先逐对相邻格判断机位和站位，再判断嘴动；不能因为是同一人、同一地点、同一镜头就答静态。'
            'fixed_view和in_place各填格子数减1个布尔值，第i项比较第i格与第i+1格。'
            'fixed_view只在背景构图、尺度、视角固定时为true：比较窗框、墙角、栏杆等背景参照；'
            '背景平移、放大、缩小或视差变化就是摇移推拉/变焦，哪怕人物始终居中也填false；切镜或无法判断也填false。'
            'in_place只在画面所有人物留在原地时为true；看身体落点，不把原地转头、表情、手势算成走动；'
            '人物迈步、向前靠近换站位、离开位置或穿过前景填false。'
            '逐格判断该角色的嘴/喙是否有可供配音的连续运动：须看相邻格嘴形变化，不要求原片正在说话；'
            '咀嚼等连续动嘴也可用；闭嘴静止、保持张嘴或静止微笑为false。严禁使用任何字幕或文字判断。'
            'speaker_ok表示说话者符合目标外观。talking按格号逐一填布尔值。'
            'static_ranges与talking分开判断：静态指固定机位、固定视角、人物留在原地，不是嘴不动。'
            '持续动嘴、张闭嘴交替、表情、点头、原地肢体语言都允许，不能据此缩短或否定静态区间；'
            '不要寻找闭嘴不动的片段，talking用于确认动嘴，static_ranges用于确认机位和站位。'
            'static_ranges列出全段所有最大的连续静态区间，每项为[原片开始秒,原片结束秒]，没有则填[]。'
            '每个区间从头到尾只能有一个固定视角，可以有多个人物，但所有人都须留在原地；'
            '切镜、远近景切换、推拉摇移、变焦、角度变化、人物走路跑动换位置、转身背离或前景穿行都必须断开。'
            '同一个人继续说话但视角变了，也必须拆成两个区间；变化前后的区间不能合并。'
            '逐格比较背景构图、人物大小与位置，标全各段，不要只选最长段。'
            '按cells的time_s标出真正头尾，不要自行加安全余量。只返回JSON '
            '{"fixed_view":[false],"in_place":[true],"talking":[true,false],"speaker_ok":true,'
            '"static_ranges":[],"note":"指出有运动的格对及背景参照、各段静态头尾依据"}。\n' +
            json.dumps(dict(entry, appearance=appearances[slot['role_id']]), ensure_ascii=False))
        def validate(value):
            validate_edges(value, entry, require_motion=True)
        number = route.edge_calls.get(index, 0) + 1
        name = f'material_scene_edges_{index}_{number}'
        reply = route._call(name, prompt, validate, sheet, image=True)
        route.edge_calls[index] = number
        good, changes, bad = trim_edges(slot, entry, reply)
        changes[0]['static_checks'] = [{k: trimmed[k] for k in
            ('slot_id', 'static_range', 'loop_range', 'loopable')} for trimmed in good]
        if good:
            changes[0]['static_check'] = changes[0]['static_checks'][0]
        kept.extend(good)
        dropped.extend(bad)
        reviews.append(dict(stage=name, image=str(sheet), entries=[entry], changes=changes))
    return kept, reviews, dropped


def reaction_candidates(scene):
    candidates = []
    for allowed in scene['allowed_ranges']:
        ranges = [[allowed['source_start_s'], allowed['source_end_s']]]
        for slot in scene['speech_slots']:
            if slot['window_id'] == allowed['window_id']:
                x, y = slot['source_start_s'], slot['source_end_s']
                ranges = [(a, b) for low, high in ranges for a, b in
                          ((low, min(high, x)), (max(low, y), high)) if a < b]
        visible = _merge([span for role, binding in scene['role_bindings'].items()
            if binding['window_id'] == allowed['window_id']
            for span in scene['picture'].get('roles', {}).get(role, {}).get('visible_ranges', [])])
        for low, high in ranges:
            for i in range(math.floor((high-low)/1.5+1e-6)):
                start, end = round(low+i*1.5, 6), round(low+(i+1)*1.5, 6)
                overlap = sum(max(0, min(end, b)-max(start, a)) for a, b in visible)
                candidate = dict(allowed, source_start_s=start, source_end_s=end,
                    candidate_id=f'{scene["scene_id"]}_reaction_{len(candidates)+1}')
                candidates.append((overlap, candidate))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]['source_start_s']))
    return [c for _, c in candidates[:8]]


def validate_reactions(value):
    from .scene_route import _fail

    if not isinstance(value, dict) or not isinstance(value.get('listeners'), list):
        _fail('scene_reactions', '须为含listeners数组的对象', [])
    if type(value.get('camera_still')) is not bool:
        raw = value.get('camera_still')
        value['camera_still'] = isinstance(raw, str) and raw.strip().casefold() in ('true', 'yes', '是')
        _note(value, f'camera_still={raw!r}规范为{value["camera_still"]}')
    return value


def review_reactions(route, scene, hints, index, *, subtitle_masks=None):
    candidates = reaction_candidates(scene)
    bound = {label: role for label, hint in hints.items() for role, binding in scene['role_bindings'].items()
             if hint['role_id'] == binding['role_id']}
    shots, reviews = [], []
    for candidate in candidates:
        sheet, entry = edge_sheet(route.sources[candidate['source_sha256']],
            dict(candidate, slot_id=candidate['candidate_id']), route.cache, subtitle_masks=subtitle_masks)
        prompt = ('图中只有一个片段，格子按编号顺序覆盖全段。characters列出本场景绑定角色的label和appearance外观，'
            '按物种、衣服、帽子认人，与说话槽位使用相同label。只看画面，严禁使用任何字幕或文字判断。'
            'static_range填最长一段可重复的相对静态画面的[原片开始秒,原片结束秒]，没有则填null。'
            '按cells的time_s标出真正头尾，不要自行加安全余量。区间内只有一个固定视角，不能切镜、变焦、推拉摇移或换角度；'
            '可以有多个人物，所有人都须留在原地，允许表情和原地肢体语言，静态不是嘴不动；'
            '任何人走路、跑开、换位置、转身背离、前景运动或切换镜头都必须截掉，不能跨过变化合并。'
            'listeners只填该静态区间内清楚可见且没有说话的角色label，嘴/喙须闭合或静止；'
            'camera_still表示所选区间内镜头静止。'
            '只返回JSON {"listeners":["c1"],"camera_still":true,"static_range":null,"note":"头尾依据"}。\n' +
            json.dumps(dict(entry, characters=[dict(label=label, appearance=hints[label]['appearance'])
                for label in bound]), ensure_ascii=False))
        number = route.reaction_calls.get(index, 0)+1
        name = f'material_scene_reactions_{index}_{number}'
        reply = route._call(name, prompt, validate_reactions, sheet, image=True)
        route.reaction_calls[index] = number
        reviews.append(dict(stage=name, candidate=candidate, image=str(sheet), entries=[entry], reply=deepcopy(reply)))
        loop = static_bounds(reply, candidate['source_start_s'], candidate['source_end_s'])
        if reply['camera_still'] and loop and round(loop[1]-loop[0], 6) >= .5:
            shots.extend(dict(role=role, window_id=candidate['window_id'],
                source_start_s=loop[0], source_end_s=loop[1],
                static_range=reply['static_range'], loop_range=loop, candidate_id=candidate['candidate_id']) for label, role in bound.items() if label in reply['listeners'])
    return shots, reviews


def build_scene_slots(route, scene, index, *, reactions=True):
    if getattr(route, 'joint_cut', False) and scene['reference_lines']:
        from .scene_coverage import build_scene_slots as build_coverage
        return build_coverage(route, scene, index)
    if not scene['reference_lines']:
        scene.update(speech_slots=[], unconfirmed_slots=[], reaction_shots=[])
        return dict(scene_id=scene['scene_id'], candidates=[], replies=[], speech_slots=[],
                    unconfirmed_slots=[], reaction_shots=[], reaction_reviews=[], reason='scene_without_lines_skipped')
    # Window evidence survives story-role rebinding without another model call.
    key = json_sha({k: scene[k] for k in ('scene_id', 'allowed_ranges', 'reference_lines')})
    if key not in route.slot_reports:
        allowed = scene['allowed_ranges'][0]
        source = route.sources[allowed['source_sha256']]
        candidates = scene_candidates(scene, source, route.cache, vad=route.vad, cut_detector=route.cut_detector)
        choice = route.choices[scene['scene_id']]
        records = route._candidate(choice)['records']
        from .scene_route import window_roles
        hints = {f'c{i}': role for i, role in enumerate(
            window_roles(route._candidate(choice), route.cast).values(), 1)}
        masks = (route.store.get(records[0]['observation_id'])['window'].get('source_subtitle_masks')
                 if route.store is not None else None)
        batches, duration = [], 0
        for c in candidates:
            clip = route.clip_maker(source, c['source_start_s'], c['source_end_s'], route.cache,
                pad_s=0, subtitle_masks=masks)
            # Encoded clips and the 10 fps join can round a nominal 12 s batch upward.
            seconds = math.ceil(clip['duration_s']*10) / 10
            if not batches or round(duration+seconds, 6) > 12:
                batches.append([])
                duration = 0
            batches[-1].append((c, clip))
            duration += seconds
        replies, rows = [], []
        for packed in batches:
            batch = [c for c, _ in packed]
            entries, clips, offset = [], [], 0
            for c, clip in packed:
                clips.append(clip)
                seconds = (math.floor(clip['duration_s']*10+.5) / 10
                           if len(packed) > 1 else clip['duration_s'])
                end = round(offset+seconds, 6)
                entries.append(dict(c, clip_start_s=offset, clip_end_s=end, clip_sha256=clip['sha256']))
                offset = end
            def validate(value):
                validate_slots(value, batch, entries, hints)
            number = route.slot_calls.get(index, 0) + 1
            name = f'material_scene_slots_{index}_{number}'
            reply = route._call(name, slots_prompt(entries, hints), validate, route.joiner(clips, route.cache))
            route.slot_calls[index] = number
            rows.extend(dict(row, speaker=hints[row['speaker']]['role_id'] if row['speaker'] in hints else row['speaker'])
                        for row in validate_slots(reply, batch, entries, hints))
            replies.append(dict(stage=name, entries=entries, reply=deepcopy(reply)))
        slots, unconfirmed = confirmed_slots(scene, candidates, rows)
        slots, edges, dropped = review_edges(route, slots, hints, index, subtitle_masks=masks)
        from . import scene_grounding
        grounding = scene_grounding.review(route, scene, slots, hints, index, masks)
        route.slot_reports[key] = dict(scene_id=scene['scene_id'], candidates=candidates, replies=replies,
            speech_slots=slots, unconfirmed_slots=unconfirmed + dropped, edge_reviews=edges, grounding=grounding)
    report = deepcopy(route.slot_reports[key])
    from . import scene_grounding
    selected, conversation = scene_grounding.select(scene, report['speech_slots'], report['grounding'])
    report['speech_slots'] = selected
    scene['conversation'] = conversation
    check = route.checks[(scene['scene_id'], scene['window_ids'][0])]
    check.update(actors=report['grounding']['actors'], single_location=bool(conversation),
                 scene=conversation['description'] if conversation else '未找到可确认的同场对话镜头')
    scene['picture'] = deepcopy(check)
    bind_slot_roles(scene, report['speech_slots'])
    scene.update(speech_slots=report['speech_slots'], unconfirmed_slots=report['unconfirmed_slots'])
    if reactions:
        if 'reaction_reviews' not in route.slot_reports[key]:
            from .scene_route import window_roles
            candidate = route._candidate(route.choices[scene['scene_id']])
            hints = {f'c{i}': role for i, role in enumerate(window_roles(candidate, route.cast).values(), 1)}
            masks = (route.store.get(candidate['records'][0]['observation_id'])['window'].get('source_subtitle_masks')
                     if route.store is not None else None)
            shots, reviews = review_reactions(route, scene, hints, index, subtitle_masks=masks)
            route.slot_reports[key].update(reaction_shots=shots, reaction_reviews=reviews)
        report.update({k: deepcopy(route.slot_reports[key][k]) for k in ('reaction_shots', 'reaction_reviews')})
        scene['reaction_shots'] = report['reaction_shots']
    return report


def build_speech_slots(route, lock, *, reactions=True):
    reports = [build_scene_slots(route, scene, index, reactions=reactions)
               for index, scene in enumerate(lock['scenes'], 1)]
    result = dict(scenes=reports)
    write_json(Path(route.state.output) / 'speech_slots.json', result)
    route.state.set_artifact('speech_slots', result)
    return result
