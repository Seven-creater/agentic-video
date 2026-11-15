"""S4 画面比较与关键条件复看（写台词之前）——方案_找素材新流程_最终版_20261103.

两件事，都发生在任何台词写下之前：
1. 比较：把 2–3 套"选角＋节拍用画面"候选的关键帧拼成一张宫格，让模型判断
   哪套最能"演出"这个故事（人物、地点、每个节拍的表演基调；画面要贡献
   行动/反应/对抗/让步/结果/反差，而不是一排说话的头像）。
2. 复看：对选中方案里**必须满足的条件**截几秒无声短片逐条判定，判定只有
   三态：satisfied / not_satisfied / insufficient。短片没清楚展示就是
   insufficient，绝不当作满足（复看只能降低风险，不能保证一定对）。

本模块不写素材记录本身；复看确认的事实只进独立的 verified_facts 附加区
（append-only），绝不改写旧观测。所有模型调用由外部注入
（vision_model(name, prompt, media_path, validator, image) -> dict）。
"""
from __future__ import annotations

from copy import deepcopy

from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import logging
import math
import re
import subprocess
import threading
import unicodedata
from pathlib import Path

from .media import sha256_file
from .state import json_sha

LOGGER = logging.getLogger('material_confirm')

POLICY = 'material_confirm_v1'

# 宫格比较：一张图，宽 ≤ 2048px，JPEG ≤ 2MB，每选项最多 12 格、每节拍 1–3 格。
MIN_COMPARE_OPTIONS, MAX_COMPARE_OPTIONS = 2, 3
MAX_TILES_PER_BEAT = 3
MAX_SHEET_WIDTH = 2048
MAX_SHEET_BYTES = 2_000_000
SHEET_QUALITY_LADDER = (88, 78, 68, 56)

# 复看短片：MCP video_analysis 上限 8MB；宽 ≤ 640，fps 在 8–12 之间。
CLIP_FPS_LADDER = (12, 10, 8)
CLIP_MAX_LONG_EDGE = 640
CLIP_DEFAULT_PAD_S = 1.0
CLIP_DEFAULT_MAX_BYTES = 8_000_000
CHECK_DEFAULT_MAX_CONDITIONS = 4
CHECK_DEFAULT_MAX_CLIP_S = 12.0

# 批次合并还须满足条件数/片长上限，任何两段的间隔都不能超过 20s。
MERGE_GAP_S = 20.0

FIT_STATES = ('good', 'weak', 'unsuitable')
VERDICTS = ('satisfied', 'not_satisfied', 'insufficient')
FACT_ORIGIN = 'model_reobserved'
_TIME_TOLERANCE_S = 0.5

# 标签字体：PIL 内置字体不含 CJK，按平台找中文字体；都找不到时退化为
# ASCII 标注（optA · beat2），宫格依旧可读、依旧确定。
_CJK_FONT_CANDIDATES = (
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/Supplemental/Songti.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/simhei.ttf',
)
_FONT_CACHE: dict = {}
_CJK_FONT_PATH: str | None = None


def normalise_condition(text) -> str:
    """条件文本的比较键：NFKC、去掉全部空白、casefold；复用事实必须键相同。

    模型复述条件时中英文都可能多出空格，空白差异不应造成"未知条件"。
    """
    if not isinstance(text, str):
        return ''
    return ''.join(unicodedata.normalize('NFKC', text).split()).casefold()


def _label_font(size: int):
    from PIL import ImageFont

    global _CJK_FONT_PATH
    key = ('label', size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if _CJK_FONT_PATH is None:
        for candidate in _CJK_FONT_CANDIDATES:
            if not Path(candidate).is_file():
                continue
            try:
                ImageFont.truetype(candidate, size)
            except OSError:
                continue
            _CJK_FONT_PATH = candidate
            break
    font = None
    if _CJK_FONT_PATH:
        try:
            font = ImageFont.truetype(_CJK_FONT_PATH, size)
        except OSError:
            font = None
    if font is None:
        font = ImageFont.load_default(size=size)
    _FONT_CACHE[key] = font
    return font


def _has_cjk_font() -> bool:
    _label_font(16)
    return _CJK_FONT_PATH is not None


# ---------------------------------------------------------------------------
# 镜头关键帧缓存（只读共享缓存 film_N/shots.json，绝不重建）


def _resolve_keyframe(root: Path, film_dir: Path, value):
    if not value:
        return None
    media = Path(value)
    if media.is_absolute():
        return media if media.is_file() else None
    for base in (film_dir, root):
        if (base / media).is_file():
            return base / media
    return None


def _load_shot_frames(shot_cache):
    """{film: [shot...]}，按 shot_start 排序；关键帧缺失的镜头跳过并计数。"""
    root = Path(shot_cache)
    films, missing = {}, 0
    for path in sorted(root.glob('film_*/shots.json')):
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f'comparison:shot_cache_unreadable:{path}:{error}') from error
        info = saved.get('info') or {}
        film = info.get('film')
        if film is None:
            film = int(path.parent.name.split('_')[-1])
        shots = []
        for row in saved.get('frames') or []:
            try:
                start, end = float(row['shot_start']), float(row['shot_end'])
            except (KeyError, TypeError, ValueError):
                continue
            keyframe = _resolve_keyframe(root, path.parent, row.get('keyframe'))
            if keyframe is None:
                missing += 1
                continue
            shots.append({'film': int(film), 'n': int(row.get('n', 0)),
                          'source_sha256': info.get('source_sha256'),
                          'timestamp_s': float(row.get('timestamp', (start + end) / 2)),
                          'shot_start_s': start, 'shot_end_s': end, 'keyframe': str(keyframe)})
        if not shots:
            continue
        shots.sort(key=lambda shot: (shot['shot_start_s'], shot['n']))
        films.setdefault(int(film), shots)
    if not films:
        raise ValueError(f'comparison:shot_cache_empty:{root}')
    if missing:
        LOGGER.warning('material_confirm:keyframes_missing:%d', missing)
    return films


def _validated_options(options):
    if not isinstance(options, list) or not MIN_COMPARE_OPTIONS <= len(options) <= MAX_COMPARE_OPTIONS:
        raise ValueError(f'comparison:needs_{MIN_COMPARE_OPTIONS}_to_{MAX_COMPARE_OPTIONS}_options')
    seen_ids = set()
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            raise ValueError(f'comparison:option[{index}]_not_object')
        option_id = option.get('option_id')
        if not isinstance(option_id, str) or not option_id:
            raise ValueError(f'comparison:option[{index}].option_id_required')
        if option_id in seen_ids:
            raise ValueError(f'comparison:duplicate_option_id:{option_id}')
        seen_ids.add(option_id)
        beats = option.get('beats')
        if not isinstance(beats, list) or not beats:
            raise ValueError(f'comparison:{option_id}:beats_required')
        seen_beats = set()
        for position, beat in enumerate(beats):
            if not isinstance(beat, dict):
                raise ValueError(f'comparison:{option_id}:beat[{position}]_not_object')
            beat_id = beat.get('beat_id')
            if not isinstance(beat_id, str) or not beat_id:
                raise ValueError(f'comparison:{option_id}:beat[{position}].beat_id_required')
            if beat_id in seen_beats:
                raise ValueError(f'comparison:{option_id}:duplicate_beat_id:{beat_id}')
            seen_beats.add(beat_id)
            ranges = beat.get('ranges')
            if not isinstance(ranges, list) or not ranges:
                raise ValueError(f'comparison:{option_id}:{beat_id}:ranges_required')
            for rng in ranges:
                _validated_range(rng, f'{option_id}:{beat_id}')
    return options


def _validated_range(rng, context):
    if not isinstance(rng, dict):
        raise ValueError(f'comparison:{context}:range_not_object')
    try:
        film = int(rng['film'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'comparison:{context}:range.film_required') from error
    start, end = rng.get('source_start_s'), rng.get('source_end_s')
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)):
        raise ValueError(f'comparison:{context}:range.times_finite_required')
    if float(start) < 0 or float(end) < float(start):
        raise ValueError(f'comparison:{context}:range.times_invalid')
    return film, float(start), float(end)


def _tile_budget(num_beats: int, max_tiles: int, minimums=None):
    """每节拍先保留一格；按区间数优先补覆盖，再补至每节拍最多 3 格。"""
    if num_beats > max_tiles:
        raise ValueError(f'comparison:beats_exceed_tile_budget:{num_beats}>{max_tiles}')
    counts = list(minimums or [1] * num_beats)
    quotas = [1] * num_beats
    remaining = max_tiles - sum(quotas)
    order = sorted(range(num_beats), key=lambda index: (-counts[index], index))
    for targets in (counts, [MAX_TILES_PER_BEAT] * num_beats):
        for slot in order:
            extra = min(remaining, max(0, min(targets[slot], MAX_TILES_PER_BEAT) - quotas[slot]))
            quotas[slot] += extra
            remaining -= extra
    return quotas


def _extract_comparison_frame(source, timestamp_s, target):
    """输入端快速 seek，准确解码到区间采样点；只输出缩小的单帧。"""
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{timestamp_s:.6f}',
                    '-i', str(source['path']), '-map', f"0:{source.get('video_stream_index', 0)}",
                    '-frames:v', '1', '-vf',
                    'scale=640:640:force_original_aspect_ratio=decrease',
                    '-q:v', '3', str(target)], check=True, capture_output=True, timeout=30)


def _comparison_frame(source, timestamp_s, cache, extractor):
    from PIL import Image

    key = json_sha({'source_sha256': source['sha256'], 'timestamp_s': timestamp_s})[:24]
    target = Path(cache) / f'frame_{key}.jpg'
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix('.part.jpg')
        extractor(source, timestamp_s, partial)
        with Image.open(partial) as picture:
            picture.verify()
        partial.replace(target)
    return str(target)


def _beat_frames(beat, films, limit, sources, cache, extractor):
    """超配额先合并近邻区间、选最长代表；先取中点，再补 start+1s。"""
    limit = min(MAX_TILES_PER_BEAT, max(1, int(limit)))
    ranges = [dict(rng) for rng in beat['ranges']]
    if len(ranges) > limit:
        merged = []
        for rng in sorted(ranges, key=lambda row: (int(row['film']), row['source_start_s'],
                                                   row['source_end_s'])):
            if (merged and int(merged[-1]['film']) == int(rng['film'])
                    and rng['source_start_s'] <= merged[-1]['source_end_s'] + 2):
                merged[-1]['source_end_s'] = max(merged[-1]['source_end_s'], rng['source_end_s'])
            else:
                merged.append(rng)
        ranges = sorted(merged, key=lambda row: (-(row['source_end_s'] - row['source_start_s']),
                                                 row['source_start_s'], int(row['film']),
                                                 row['source_end_s']))[:limit]
        ranges.sort(key=lambda row: (row['source_start_s'], int(row['film']), row['source_end_s']))
    candidates, seen = [], set()
    samples = [(rng, (rng['source_start_s'] + rng['source_end_s']) / 2)
               for rng in ranges]
    samples.extend((rng, rng['source_start_s'] + 1.0) for rng in ranges
                   if rng['source_end_s'] - rng['source_start_s'] > 4)
    for rng, timestamp in samples:
        if len(candidates) >= limit:
            break
        film = int(rng['film'])
        shots = films.get(film, [])
        start, end = float(rng['source_start_s']), float(rng['source_end_s'])
        nearby = sorted(shots, key=lambda shot: (abs(shot['timestamp_s'] - timestamp), shot['n']))
        short = [shot for shot in nearby
                 if start - 1 <= shot['shot_start_s'] <= shot['shot_end_s'] <= end + 1
                 and shot['shot_end_s'] - shot['shot_start_s'] <= 8
                 and start - 1 <= shot['timestamp_s'] <= end + 1]
        if short:
            frame = dict(short[0], frame_origin='shot_cache', approximate=False)
        else:
            source = sources.get(rng.get('source_sha256'))
            timestamp = round(timestamp, 6)
            try:
                if source is None:
                    raise ValueError('source_unavailable')
                path = _comparison_frame(source, timestamp, cache, extractor)
                frame = dict(film=film, n=None, timestamp_s=timestamp, keyframe=path,
                             shot_start_s=start, shot_end_s=end,
                             frame_origin='source', approximate=False,
                             source_sha256=source['sha256'])
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                if not nearby:
                    raise ValueError(f'comparison:no_frame_for_range:{film}:{start}-{end}') from error
                LOGGER.warning('material_confirm:approximate_frame:%s:%s:%s', film, timestamp, error)
                frame = dict(nearby[0], frame_origin='shot_cache', approximate=True)
        key = (film, frame['timestamp_s'], frame['keyframe'])
        if key not in seen:
            seen.add(key)
            candidates.append(frame)
    return candidates


def _fit_text_size(draw, text, font_size, max_width, minimum=10):
    while font_size > minimum and draw.textlength(text, font=_label_font(font_size)) > max_width:
        font_size -= 1
    return max(minimum, font_size)


def _paste_tile(canvas, draw, shot, column, row_y, tile_w, tile_h, caption_h, caption):
    from PIL import Image

    margin = 4
    x = 8 + column * tile_w
    with Image.open(shot['keyframe']) as picture:
        source = picture.convert('RGB')
    scale = min(tile_w / source.width, tile_h / source.height)
    size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
    board = Image.new('RGB', (tile_w, tile_h), 'black')
    board.paste(source.resize(size, Image.Resampling.LANCZOS),
                ((tile_w - size[0]) // 2, (tile_h - size[1]) // 2))
    canvas.paste(board, (x, row_y + caption_h))
    draw.rectangle((x, row_y, x + tile_w - 1, row_y + caption_h - 1), fill=(24, 24, 24))
    size = _fit_text_size(draw, caption, max(13, tile_w // 12), tile_w - 2 * margin)
    draw.text((x + margin, row_y + (caption_h - size) // 2), caption,
              font=_label_font(size), fill='white')


def comparison_sheet(options, shot_cache, out_dir, *, sources=None, frame_extractor=None,
                     max_tiles_per_option=12):
    """把 2–3 套候选的节拍关键帧拼成一张带标注的宫格（确定性、可复用）。"""
    from PIL import Image, ImageDraw

    options = _validated_options(options)
    films = _load_shot_frames(shot_cache)
    max_tiles = int(max_tiles_per_option)
    if max_tiles < 1:
        raise ValueError('comparison:max_tiles_per_option_too_small')
    tile_plans, beat_coverage = [], []
    for option in options:
        budgets = _tile_budget(len(option['beats']), max_tiles,
                               [len(beat['ranges']) for beat in option['beats']])
        plan, coverage = [], []
        for beat, budget in zip(option['beats'], budgets):
            frames = _beat_frames(beat, films, budget, sources or {}, Path(out_dir) / 'frames',
                                  frame_extractor or _extract_comparison_frame)
            covered = {'beat_id': beat['beat_id'], 'shown_ranges': [], 'omitted_ranges': []}
            for rng in beat['ranges']:
                film, start, end = _validated_range(rng, beat['beat_id'])
                shown = any(frame['film'] == film and start <= frame['timestamp_s'] <= end
                            for frame in frames)
                covered['shown_ranges' if shown else 'omitted_ranges'].append(
                    dict(film=film, source_start_s=start, source_end_s=end))
            plan.append(frames)
            coverage.append(covered)
        tile_plans.append(plan)
        beat_coverage.append(coverage)
    beat_ids = [[beat['beat_id'] for beat in option['beats']] for option in options]
    row_grid = all(ids == beat_ids[0] for ids in beat_ids)
    layout = 'rows_by_beat' if row_grid else 'stacked_by_option'
    cols_per_option = [max(1, max((len(rows) for rows in plan), default=1)) for plan in tile_plans]
    columns = sum(cols_per_option) if row_grid else max(cols_per_option)
    tile_w = max(160, min(360, (MAX_SHEET_WIDTH - 16) // columns))
    tile_h = tile_w * 9 // 16
    caption_h, header_h = 30, 46
    cjk = _has_cjk_font()

    def option_label(index):
        return f'选项{chr(ord("A") + index)}' if cjk else f'opt{chr(ord("A") + index)}'

    def beat_label(number):
        return f'节拍{number}' if cjk else f'beat{number}'

    def caption(option_index, beat_number, shot):
        source = f"片{shot['film']} {shot['timestamp_s']:.1f}s" if cjk else f"f{shot['film']} {shot['timestamp_s']:.1f}s"
        approximate = (' · 近似帧' if cjk else ' · approx') if shot['approximate'] else ''
        covered = beat_coverage[option_index][beat_number - 1]
        counts = ''
        if covered['omitted_ranges']:
            shown = len(covered['shown_ranges'])
            total = shown + len(covered['omitted_ranges'])
            counts = f' · 共{total}段·示{shown}段' if cjk else f' · ranges {shown}/{total}'
        return f'{option_label(option_index)} · {beat_label(beat_number)}{counts} · {source}{approximate}'

    headers = 1 if row_grid else len(options)
    body_rows = len(beat_ids[0]) if row_grid else sum(len(ids) for ids in beat_ids)
    canvas = Image.new('RGB', (columns * tile_w + 16,
                               headers * header_h + body_rows * (tile_h + caption_h) + 8), 'black')
    draw = ImageDraw.Draw(canvas)
    tiles = []

    def cast_summary(option):
        casting = option.get('casting') or {}
        if isinstance(casting, dict):
            text = '、'.join(f'{key}={value}' for key, value in casting.items())
        else:
            text = str(casting)
        return text[:80] + ('…' if len(text) > 80 else '')

    def draw_header(text, x, width):
        draw.rectangle((x, 8, x + width - 1, 8 + header_h - 1), fill=(40, 40, 40))
        size = _fit_text_size(draw, text, 26, width - 12)
        draw.text((x + 6, 8 + (header_h - size) // 2), text, font=_label_font(size), fill='white')

    if row_grid:
        offset = 0
        for index, option in enumerate(options):
            width = cols_per_option[index] * tile_w
            draw_header(f'{option_label(index)} · {cast_summary(option)}', 8 + offset * tile_w, width)
            offset += cols_per_option[index]
        for beat_index in range(len(beat_ids[0])):
            row_y = 8 + header_h + beat_index * (tile_h + caption_h)
            base_column = 0
            for option_index, plan in enumerate(tile_plans):
                position = 0
                for shot in plan[beat_index]:
                    column = base_column + position
                    text = caption(option_index, beat_index + 1, shot)
                    _paste_tile(canvas, draw, shot, column, row_y, tile_w, tile_h, caption_h, text)
                    tiles.append(_tile_record(options[option_index], option_index, option_label(option_index),
                                              beat_index + 1, beat_label(beat_index + 1), shot, column, text))
                    position += 1
                base_column += cols_per_option[option_index]
    else:
        row_cursor = 8
        for option_index, option in enumerate(options):
            draw_header(f'{option_label(option_index)} · {cast_summary(option)}', 8, columns * tile_w)
            row_cursor += header_h
            for beat_index, shot_rows in enumerate(tile_plans[option_index]):
                row_y = row_cursor
                for position, shot in enumerate(shot_rows):
                    text = caption(option_index, beat_index + 1, shot)
                    _paste_tile(canvas, draw, shot, position, row_y, tile_w, tile_h, caption_h, text)
                    tiles.append(_tile_record(option, option_index, option_label(option_index),
                                              beat_index + 1, beat_label(beat_index + 1), shot,
                                              position, text))
                row_cursor += tile_h + caption_h

    for tile in tiles:
        covered = beat_coverage[tile['option_index']][tile['beat_number'] - 1]
        tile.update(shown_ranges=covered['shown_ranges'], omitted_ranges=covered['omitted_ranges'])

    if max(canvas.size) > MAX_SHEET_WIDTH:
        ratio = MAX_SHEET_WIDTH / max(canvas.size)
        canvas = canvas.resize((round(canvas.width * ratio), round(canvas.height * ratio)),
                               Image.Resampling.LANCZOS)
    payload = None
    for _round in range(4):
        for quality in SHEET_QUALITY_LADDER:
            buffer = io.BytesIO()
            canvas.save(buffer, 'JPEG', quality=quality)
            if buffer.tell() <= MAX_SHEET_BYTES:
                payload = buffer.getvalue()
                break
        if payload is not None:
            break
        canvas = canvas.resize((max(64, int(canvas.width * .8)), max(64, int(canvas.height * .8))),
                               Image.Resampling.LANCZOS)
    if payload is None:
        raise ValueError('comparison:sheet_exceeds_size_budget')

    spec = {'policy': POLICY, 'kind': 'comparison_sheet', 'layout': layout,
            'cjk_labels': cjk, 'max_tiles_per_option': max_tiles,
            'options': [{'option_id': option['option_id'],
                         'beats': [{**covered,
                                    'ranges': [dict(rng) for rng in beat['ranges']]}
                                   for beat, covered in zip(option['beats'], beat_coverage[index])]}
                        for index, option in enumerate(options)],
            'tiles': [{key: tile[key] for key in
                       ('option_id', 'beat_id', 'film', 'shot_n', 'timestamp_s',
                        'frame_origin', 'approximate', 'source_sha256')} for tile in tiles]}
    key = json_sha(spec)[:16]
    out = Path(out_dir)
    target, sidecar = out / f'comparison_{key}.jpg', out / f'comparison_{key}.json'
    result = {'policy': POLICY, 'kind': 'comparison_sheet', 'path': str(target),
              'sha256': hashlib.sha256(payload).hexdigest(),
              'width': canvas.width, 'height': canvas.height,
              'size_bytes': len(payload), 'layout': layout, 'cjk_labels': cjk,
              'options': [{'option_id': option['option_id'],
                           'label': option_label(index), 'beats': beat_coverage[index]}
                          for index, option in enumerate(options)],
              'tiles': tiles, 'spec': spec}
    if not (target.is_file() and sidecar.is_file()
            and json.loads(sidecar.read_text(encoding='utf-8')).get('sha256') == result['sha256']):
        out.mkdir(parents=True, exist_ok=True)
        partial = out / f'comparison_{key}.part.jpg'
        partial.write_bytes(payload)
        partial.replace(target)
        (out / f'comparison_{key}.part.json').write_text(
            json.dumps({'spec': spec, 'sha256': result['sha256']}, ensure_ascii=False), encoding='utf-8')
        (out / f'comparison_{key}.part.json').replace(sidecar)
    else:
        saved = json.loads(sidecar.read_text(encoding='utf-8'))
        result['sha256'] = saved['sha256']
    return result


def _tile_record(option, option_index, option_label, beat_number, beat_label, shot, column, label):
    return {'label': label,
            'option_id': option['option_id'], 'option_label': option_label, 'option_index': option_index,
            'beat_id': option['beats'][beat_number - 1]['beat_id'], 'beat_label': beat_label,
            'beat_number': beat_number, 'film': shot['film'], 'shot_n': shot['n'],
            'timestamp_s': shot['timestamp_s'], 'shot_start_s': shot['shot_start_s'],
            'shot_end_s': shot['shot_end_s'], 'keyframe': shot['keyframe'], 'column': column,
            'frame_origin': shot['frame_origin'], 'approximate': shot['approximate'],
            'source_sha256': shot.get('source_sha256')}


# ---------------------------------------------------------------------------
# 比较：prompt + validator


def _option_digest(option):
    beats = []
    for position, beat in enumerate(option['beats'], 1):
        ranges = '; '.join(f"片{rng.get('film')} {float(rng['source_start_s']):.1f}-{float(rng['source_end_s']):.1f}s"
                           for rng in beat['ranges'])
        checks = ' / '.join(str(row.get('condition')) for row in (beat.get('checks') or []))
        row = f"    节拍{position}（beat_id={beat['beat_id']}）区间: {ranges}"
        if beat.get('speaking_role'):
            row += f"；说话角色: {beat['speaking_role']}"
        if checks:
            row += f"；必须条件: {checks}"
        beats.append(row)
    casting = option.get('casting') or {}
    cast = '、'.join(f'{key}={value}' for key, value in casting.items()) if isinstance(casting, dict) else str(casting)
    return f"  选角: {cast or '未提供'}\n" + '\n'.join(beats)


def compare_prompt(options, skeleton_summary, *, tiles=None):
    """宫格比较的提问：哪套方案最能演出这个故事；每套每节拍给 fit 与一句话依据。"""
    options = _validated_options(options)
    skeleton = skeleton_summary if isinstance(skeleton_summary, str) else json.dumps(
        skeleton_summary, ensure_ascii=False)
    lines = [
        '你在为一条新短片从既有电影素材里挑实现方案。同一故事有 2–3 套"选角＋节拍用画面"候选，',
        '每套候选各节拍的关键帧已拼在一张宫格里，每格上方标注了 选项X · 节拍Y 以及来源影片和时间。',
        '',
        '【故事骨架】', skeleton, '',
    ]
    for index, option in enumerate(options):
        letter = chr(ord('A') + index)
        lines.append(f'【选项{letter}】option_id={option["option_id"]}')
        lines.append(_option_digest(option))
    if tiles:
        lines.append('')
        lines.append('【宫格标注索引】')
        for tile in tiles:
            origin = '原片取帧' if tile.get('frame_origin') == 'source' else f"镜头{tile['shot_n']}"
            lines.append(f"  {tile['label']}（{origin}，{tile['shot_start_s']:.1f}-{tile['shot_end_s']:.1f}s）")
        coverage = {(tile.get('option_id'), tile.get('beat_id')): tile for tile in tiles
                    if 'omitted_ranges' in tile}
        if coverage:
            lines += ['', '【区间抽样覆盖】', '示k段表示有关键帧落入原始区间，仍非完整展示；合并仅用于抽样。']
            for option in options:
                for beat in option['beats']:
                    covered = coverage.get((option['option_id'], beat['beat_id']))
                    if covered is None:
                        continue
                    omitted = '; '.join(
                        f"片{rng['film']} {rng['source_start_s']:.1f}-{rng['source_end_s']:.1f}s"
                        for rng in covered['omitted_ranges'])
                    lines.append(f"  option_id={option['option_id']} beat_id={beat['beat_id']}："
                                 f"共{len(beat['ranges'])}段·示{len(covered['shown_ranges'])}段；"
                                 f"未展示区间：{omitted or '无'}")
    lines += [
        '',
        '【判断要求】',
        '1. best_option_id：哪套方案最能"演出"这个故事——人物组合立得住、地点连贯、每个节拍的表演',
        '   基调与叙事作用匹配。只比较候选之内的方案，不要发明新方案。',
        '2. 画面本身要贡献 行动/反应/对抗/让步/结果/反差 中的至少一种；一排只会说话的头像不算演出。',
        '3. 每套方案每个节拍都要给 fit：good=画面能演出该节拍；weak=勉强或证据不足，可能要靠台词补；',
        '   unsuitable=画面不合适（比如只有说话头像、动作做不出来、场景对不上）。',
        '4. note 用一句话写你在关键帧里实际看到了什么；不要编造画面里没有的东西。',
        '5. 未展示区间属于未知，不能因未展示判定其中人物/动作/条件缺失；关键帧证据不足或看不清时',
        '   fit=weak，note 写“证据不足（未展示/看不清）”；只有已展示画面明确不合适时才用 unsuitable。',
        '',
        '【输出】只输出 JSON，不要多余文字：',
        '{"best_option_id": "<候选 option_id 之一>", "options": [',
        '  {"option_id": "...", "beats": [{"beat_id": "...", "fit": "good|weak|unsuitable", "note": "一句话"}]}]}',
        '每套候选都要出现，beats 必须覆盖该候选的全部 beat_id。',
    ]
    return '\n'.join(lines)


def validate_compare(value, options):
    """一次性收集全部问题再抛出：best_option_id、每套每节拍的 fit/note 都要齐。"""
    expected = {option['option_id']: {beat['beat_id'] for beat in option['beats']} for option in options}
    if not isinstance(value, dict):
        raise ValueError('compare_invalid:value_not_object')
    problems = []
    best = value.get('best_option_id')
    if not isinstance(best, str) or best not in expected:
        problems.append(f'best_option_id_must_be_one_of:{sorted(expected)}')
    rows = value.get('options')
    if not isinstance(rows, list):
        raise ValueError('compare_invalid:options_must_be_list')
    seen = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f'options[{index}]:not_object')
            continue
        option_id = row.get('option_id')
        if option_id not in expected:
            problems.append(f'options[{index}]:option_id_unknown:{option_id}')
            continue
        if option_id in seen:
            problems.append(f'options[{index}]:duplicate_option:{option_id}')
        seen.append(option_id)
        beats = row.get('beats')
        if not isinstance(beats, list):
            problems.append(f'{option_id}:beats_must_be_list')
            continue
        covered = []
        for position, beat in enumerate(beats):
            if not isinstance(beat, dict):
                problems.append(f'{option_id}:beats[{position}]:not_object')
                continue
            beat_id = beat.get('beat_id')
            if beat_id not in expected[option_id]:
                problems.append(f'{option_id}:beat_unknown:{beat_id}')
                continue
            if beat_id in covered:
                problems.append(f'{option_id}:beat_duplicate:{beat_id}')
            covered.append(beat_id)
            fit = beat.get('fit')
            if fit not in FIT_STATES:
                problems.append(f'{option_id}:{beat_id}:fit_invalid:{fit}')
            note = beat.get('note')
            if not isinstance(note, str) or not note.strip():
                problems.append(f'{option_id}:{beat_id}:note_required')
        for beat_id in sorted(expected[option_id] - set(covered)):
            problems.append(f'{option_id}:beat_missing:{beat_id}')
    for option_id in sorted(set(expected) - set(seen)):
        problems.append(f'options_missing:{option_id}')
    if problems:
        raise ValueError('compare_invalid:' + ';'.join(problems))
    return value


# ---------------------------------------------------------------------------
# 复看短片：独立的确定性编码，不改变其他阶段的通用代理编码。


def confirm_clip(source, start_s, end_s, cache, *, pad_s=CLIP_DEFAULT_PAD_S,
                 max_bytes=CLIP_DEFAULT_MAX_BYTES, subtitle_masks=None,reuse_caches=()):
    """按 [start−pad, end+pad]（夹在源片范围内）切一段无声短片供条件复看。"""
    from .media import (_cache_folder, _cached, _run, _subtitle_filters, _write_json,
                        probe_media, validate_window, verify_source)

    duration = float(source['duration_s'])
    start = max(0.0, float(start_s) - float(pad_s))
    end = min(duration, float(end_s) + float(pad_s))
    if not end > start:
        raise ValueError(f'confirm_clip:empty_range_after_padding:{start:.3f}-{end:.3f}s')
    path = verify_source(source)
    start, end = validate_window(source, start, end)
    max_bytes = min(int(max_bytes), CLIP_DEFAULT_MAX_BYTES)
    if max_bytes <= 0:
        raise ValueError('proxy_options_invalid')
    failure = None
    for fps in CLIP_FPS_LADDER:
        bitrate = min(1_200_000, math.floor(max_bytes * 8 * 0.80 / (end - start)))
        if bitrate < 40_000:
            failure = 'proxy_range_too_long_for_size_budget'
            continue
        scale = (f"scale=w='if(gte(iw,ih),min({CLIP_MAX_LONG_EDGE},iw),-2)':"
                 f"h='if(gte(iw,ih),-2,min({CLIP_MAX_LONG_EDGE},ih))',fps={fps}")
        filters = ','.join([*_subtitle_filters(source, subtitle_masks, offset_s=start), scale])
        input_args = ['-nostdin', '-v', 'error', '-y', '-threads', '1',
                      '-filter_threads', '1', '-filter_complex_threads', '1',
                      '-fflags', '+bitexact', '-ss', f'{start:.6f}']
        for attempt in range(3):
            output_args = ['-t', f'{end - start:.6f}', '-map', f"0:{source.get('video_stream_index', 0)}",
                '-vf', filters, '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                '-threads', '1', '-fflags', '+bitexact', '-flags:v', '+bitexact', '-flags:a', '+bitexact',
                '-g', str(fps * 2), '-keyint_min', str(fps * 2), '-sc_threshold', '0', '-bf', '0',
                '-x264-params', 'threads=1:lookahead-threads=1:sliced-threads=0',
                '-b:v', str(bitrate), '-maxrate', str(bitrate), '-bufsize', str(bitrate * 2),
                '-an', '-sn', '-dn', '-map_metadata', '-1', '-map_metadata:s:v', '-1', '-map_chapters', '-1',
                '-metadata', 'creation_time=', '-metadata:s:v:0', 'creation_time=',
                '-movflags', '+faststart']
            # Paths and wall-clock data are excluded; include the actual bitrate
            # attempt so equal specs mean equal source, range and encoder settings.
            spec = dict(policy='material-confirm-clip-v2', kind='continuous_window',
                source_sha256=source['sha256'], source_start_s=start, source_end_s=end,
                video_stream_index=source.get('video_stream_index', 0), include_audio=False,
                subtitle_masks=subtitle_masks, max_bytes=max_bytes,
                max_long_edge=CLIP_MAX_LONG_EDGE, fps=fps,
                input_args=input_args, output_args=output_args)
            folder = _cache_folder(cache, spec)
            window = _cached(folder, spec)
            if window is not None:
                return dict(window, kind='condition_recheck_clip')
            # Closed parent caches remain immutable. Exact encoder/source/mask
            # specs plus byte verification permit reuse without re-encoding.
            for previous in reuse_caches:
                old_folder=Path(previous).resolve()/folder.name
                window=_cached(old_folder,spec)
                if window is None:continue
                if window.get('source_id')!=source['source_id'] or window.get('source_path')!=str(path):continue
                _write_json(folder/'reuse.json',dict(lineage_path=str(old_folder/'lineage.json'),
                    lineage_sha256=sha256_file(old_folder/'lineage.json'),media_sha256=window['sha256']))
                return dict(window,kind='condition_recheck_clip')
            partial, target = folder / 'window.part.mp4', folder / 'window.mp4'
            _run(['ffmpeg', *input_args, '-i', str(path), *output_args, str(partial)])
            if partial.stat().st_size >= max_bytes:
                bitrate = max(40_000, int(bitrate * 0.65))
                failure = 'proxy_exceeds_size_budget'
                continue
            measured = probe_media(partial)
            if abs(measured['duration_s'] - (end - start)) > max(0.20, 2 / fps):
                raise ValueError('proxy_duration_mapping_invalid')
            if any(s.get('codec_type') == 'audio' for s in measured['streams']):
                raise ValueError('proxy_audio_stream_lost')
            verify_source(source)
            partial.replace(target)
            window = dict(spec=spec, spec_sha256=json_sha(spec), kind='continuous_window',
                path=str(target), sha256=sha256_file(target), source_id=source['source_id'],
                source_path=str(path), source_sha256=source['sha256'], source_start_s=start,
                source_end_s=end, source_offset_s=start, media_duration_s=measured['duration_s'],
                duration_s=measured['duration_s'], size_bytes=target.stat().st_size,
                audio_stream_index=None, audio_present=False, metadata=measured,
                time_mapping='source_time_s = source_offset_s + proxy_time_s',
                mapping_tolerance_s=max(0.10, 1 / fps),
                source_visual_policy='silent_subtitle_masked' if subtitle_masks is not None else 'silent',
                source_subtitle_masks=subtitle_masks, pad_s=float(pad_s), fps_used=fps,
                actual_range_s=[start, end],
                local_to_source='source_time_s = source_start_s + clip_local_time_s')
            _write_json(folder / 'lineage.json', window)
            return dict(window, kind='condition_recheck_clip')
    raise ValueError(f'confirm_clip:exceeds_size_budget:{int(max_bytes)}B:'
                     f'{start:.3f}-{end:.3f}s:last={failure}')


def local_to_source_time(clip, local_s):
    """短片本地秒 → 原片秒（夹在短片时长内，避免越界证据外推）。"""
    duration = float(clip['duration_s'])
    return float(clip['source_start_s']) + max(0.0, min(duration, float(local_s)))


def check_evidence_source_times(clip, evidence, verdict, start_s, end_s):
    """证据与核查区间取交集，至少保留 min(0.5s, 原证据时长)；区间内点证据有效。"""
    result = dict(verdict=verdict, evidence_source_times=[], evidence_source_times_raw=[])
    pair = (evidence.get('local_start_s'), evidence.get('local_end_s'))
    if pair[0] is None:
        return result
    raw_start, raw_end = [local_to_source_time(clip, value) for value in pair]
    result['evidence_source_times_raw'] = [[raw_start, raw_end]]
    start, end = max(float(start_s), raw_start), min(float(end_s), raw_end)
    if start > end:
        note = 'evidence_outside_range:证据完全位于核查区间外的 clip padding 中'
    elif end - start < min(0.5, raw_end - raw_start):
        note = 'evidence_overlap_too_short:证据与核查区间的交集时长不足'
    else:
        result['evidence_source_times'] = [[start, end]]
        return result
    result.update(verdict='insufficient', evidence_time_note=note)
    return result


# ---------------------------------------------------------------------------
# 条件复看：prompt + validator


def check_prompt(beat, conditions, clip_range):
    """条件复看的提问：一段无声短片，逐条给三态判定和片内证据时间。"""
    beats = beat if isinstance(beat, list) else [beat]
    start = float(clip_range['source_start_s'])
    end = float(clip_range['source_end_s'])
    duration = float(clip_range.get('duration_s', end - start))
    lines = [
        f'你会观看一段几秒钟的无声短片：覆盖原片时间 {start:.2f}–{end:.2f}s，本地时间 0–{duration:.2f}s',
        f'（本地时间 + {start:.2f} = 原片时间）。短片没有声音，请只依据画面判断。',
        '',
        '【节拍背景】',
    ]
    for row in beats:
        piece = f"  beat_id={row.get('beat_id')}"
        if row.get('range'):
            rng = row['range']
            piece += f"（本unit原片区间 {rng['source_start_s']}–{rng['source_end_s']}s）"
        if row.get('speaking_role'):
            piece += f"（说话角色: {row['speaking_role']}）"
        lines.append(piece)
    lines.append('')
    lines.append('【复看条件：身份为硬条件，其余为画面偏好或说话方式依据】')
    for index, row in enumerate(conditions, 1):
        text = row['condition'] if isinstance(row, dict) else str(row)
        beat_id = row.get('beat_id') if isinstance(row, dict) else None
        why = row.get('why') if isinstance(row, dict) else None
        piece = f'  c{index}. [{beat_id}] {text}'
        if why:
            piece += f'（说明：{why}）'
        lines.append(piece)
    lines += [
        '',
        '【判定规则】',
        '- satisfied：短片清楚展示了该条件成立。给出证据时间和一句话 observed。',
        '- not_satisfied：短片清楚展示了条件不成立（相反情况看得很清楚）。同样给证据。',
        '- insufficient：短片没有清楚展示（太短、被遮挡、太模糊、时机不在片内）。这时证据时间',
        '  留 null，observed 写为什么看不清。绝不能猜，更不能把 insufficient 当作满足。',
        f'- evidence.local_start_s / local_end_s 必须在 0–{duration:.2f} 之间且 start ≤ end；',
        '  observed 只写你实际看到的内容，不要编造。',
        '',
        '每个上述beat_id是一个区间unit，另写一句scene：片段中实际可见的人物、动作、相互关系和结果，',
        '只写看到的，≤80字；按每个unit自身区间描述，不把相邻区间的动作归给它。',
        '【输出】只输出 JSON，不要多余文字；id 填上面条件前的编号（c1、c2……），condition 可省略：',
        '{"conditions": [{"id": "c1", "verdict": "satisfied|not_satisfied|insufficient",',
        '  "evidence": {"local_start_s": 0.0, "local_end_s": 0.0, "observed": "一句话"}}],',
        ' "scenes": [{"unit": "上面的beat_id", "text": "实际画面一句话"}]}',
        '每个条件编号和每个unit的scene都必须且只能出现一次；没有条件时conditions写[]，仍须输出scenes。',
    ]
    return '\n'.join(lines)


def _check_times(pair, duration):
    start, end = pair
    for value in (start, end):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return 'evidence_times_must_be_numbers'
    if start < -_TIME_TOLERANCE_S or end > duration + _TIME_TOLERANCE_S or end < start:
        return f'evidence_outside_clip:{start:.2f}-{end:.2f}s_of_{duration:.2f}s'
    return None


def validate_check(value, conditions, clip_duration_s, units=None):
    """一次性收集全部问题：缺条件、未知条件、非法 verdict、片外证据、空 observed。"""
    duration = float(clip_duration_s)
    if not isinstance(value, dict):
        raise ValueError('check_invalid:value_not_object')
    # Conditions are answered by their prompt id (c1, c2...): texts repeat across ranges of one
    # beat and models trim the shown "[beat]" prefix, so exact-text echo failed (mat6).
    requested, by_text = {}, {}
    for index, row in enumerate(conditions, 1):
        text = row['condition'] if isinstance(row, dict) else str(row)
        key = f'c{index}'
        requested[key] = {'beat_id': row.get('beat_id') if isinstance(row, dict) else None, 'condition': text}
        by_text.setdefault(normalise_condition(text), []).append(key)
    rows = value.get('conditions')
    if not isinstance(rows, list):
        raise ValueError('check_invalid:conditions_must_be_list')
    problems, seen = [], set()
    for index, row in enumerate(rows):
        tag = f'conditions[{index}]'
        if not isinstance(row, dict):
            problems.append(f'{tag}:not_object')
            continue
        key = str(row.get('id') or '').strip().lower()
        if key not in requested:
            text = row.get('condition')
            matches = []
            if isinstance(text, str) and text.strip():
                # Exact text first; then without a copied "c1. [b1:0]" prefix or "（说明：…）" suffix.
                stripped = re.sub(r'^\s*(?:c\d+[.、:：]?\s*)?(?:\[[^\]]*\]\s*)?', '', text)
                stripped = re.split(r'（说明：| —— ', stripped)[0]
                for candidate in (text, stripped):
                    known = by_text.get(normalise_condition(candidate), [])
                    # Prefer an unanswered id; a text answered already is reported as a duplicate.
                    matches = [k for k in known if k not in seen] or known[:1]
                    if matches:
                        break
            if len(matches) != 1:
                problems.append(f'{tag}:unknown_condition_id:{row.get("id") or str(text)[:40]}'
                                f' (use one of {", ".join(sorted(requested, key=lambda k: int(k[1:])))})')
                continue
            key = matches[0]
        row['id'], row['condition'] = key, requested[key]['condition']
        if key in seen:
            problems.append(f'{tag}:duplicate_condition_id:{key}')
            # 重复行照样检查其余字段，问题一次收齐。
        else:
            seen.add(key)
        verdict = row.get('verdict')
        if isinstance(verdict, str):
            verdict = verdict.strip().lower()
        if verdict not in VERDICTS:
            problems.append(f'{tag}:verdict_invalid:{row.get("verdict")}')
            verdict = None
        else:
            row['verdict'] = verdict
        evidence = row.get('evidence')
        if not isinstance(evidence, dict):
            problems.append(f'{tag}:evidence_object_required')
            continue
        observed = evidence.get('observed')
        if not isinstance(observed, str) or not observed.strip():
            problems.append(f'{tag}:observed_required')
        pair = (evidence.get('local_start_s'), evidence.get('local_end_s'))
        if verdict is not None:
            if verdict == 'insufficient' and all(value is None for value in pair):
                continue
            problem = _check_times(pair, duration)
            if problem:
                problems.append(f'{tag}:{problem}')
    for key, item in requested.items():
        if key not in seen:
            problems.append(f'condition_missing:{key} [{item["beat_id"]}] {item["condition"][:40]}')
    expected_units = set(units if units is not None else
                         (row['beat_id'] for row in requested.values() if row['beat_id']))
    scenes = value.get('scenes')
    scene_units = set()
    if not isinstance(scenes, list):
        problems.append('scenes_required:请补scenes=[{"unit":"区间beat_id","text":"实看画面，≤80字"}]')
        scenes = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            problems.append(f'scenes[{index}]:object_required')
            continue
        unit = scene.get('unit')
        if not isinstance(unit, str) or unit not in expected_units:
            problems.append(f'scenes[{index}]:unknown_unit:{unit}; use {sorted(expected_units)}')
            continue
        if unit in scene_units:
            problems.append(f'scene_duplicate:{unit}')
        scene_units.add(unit)
        text = scene.get('text')
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 80:
            problems.append(f'scene_text_required:{unit}:请写实际可见画面，1–80字')
    for unit in sorted(expected_units - scene_units):
        problems.append(f'scene_missing:{unit}:请补该unit的scenes条目，text只写实看画面，≤80字')
    if problems:
        raise ValueError('check_invalid:' + ';'.join(problems))
    return value


def _salvage_check_reply(text, conditions, error):
    """Recover scalar fields by condition id, without guessing missing values or braces."""
    text = text.strip()
    if not text.startswith('{'):
        raise error
    fields = {'id', 'verdict', 'local_start_s', 'local_end_s', 'observed'}
    scene_fields = {'unit', 'text'}
    members, condition_lists, scene_lists = [], 0, 0
    decoder, offset = json.JSONDecoder(), 0
    while offset < len(text):
        if text[offset] != '"':
            if text[offset].isspace() or text[offset] in '{}[],:':
                offset += 1
            else:
                try:
                    _, offset = decoder.raw_decode(text, offset)
                except json.JSONDecodeError:
                    raise error from None
            continue
        try:
            key, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            raise error from None
        # Consume complete JSON strings so quoted field names inside observed
        # remain evidence text, including escaped quotes and Unicode escapes.
        tail = text[offset:].lstrip()
        if not tail.startswith(':'):
            continue
        start = len(text) - len(tail) + 1
        while start < len(text) and text[start].isspace():
            start += 1
        if key in ('conditions', 'scenes'):
            condition_lists += key == 'conditions'
            scene_lists += key == 'scenes'
            if text[start:start + 1] != '[':
                raise error
        if key not in fields | scene_fields:
            continue
        try:
            value, offset = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            raise error from None
        if offset < len(text) and not (text[offset].isspace() or text[offset] in ',}]'):
            raise error
        if key in {'id', 'verdict', 'observed'} | scene_fields:
            if not isinstance(value, str):
                raise error
        elif value is not None and type(value) not in (int, float):
            raise error
        members.append((key, value))
    requested = {f'c{index}' for index in range(1, len(conditions) + 1)}
    blocks, scene_blocks, seen = [], [], set()
    for key, value in members:
        if key == 'unit':
            scene_blocks.append({key: value})
        elif key == 'text':
            if not scene_blocks or key in scene_blocks[-1]:
                raise error
            scene_blocks[-1][key] = value
        elif key == 'id':
            if value not in requested or value in seen:
                raise error
            seen.add(value)
            blocks.append({key: value})
        elif not blocks or key in blocks[-1]:
            raise error
        else:
            blocks[-1][key] = value
    if (condition_lists != 1 or scene_lists != 1 or seen != requested
            or any(set(row) != fields for row in blocks)
            or any(set(row) != scene_fields for row in scene_blocks)):
        raise error
    return dict(conditions=[dict(id=row['id'], verdict=row['verdict'], evidence={
        key: row[key] for key in ('local_start_s', 'local_end_s', 'observed')}) for row in blocks],
        scenes=scene_blocks, normalization=[dict(kind='check_reply_salvaged', error=str(error))])


class CheckReplyValidator:
    """S4 validator with an opt-in parser for otherwise unambiguous broken JSON."""

    def __init__(self, conditions, clip_duration_s, units=None):
        self.conditions, self.clip_duration_s = conditions, clip_duration_s
        self.units = units

    def __call__(self, value):
        return validate_check(value, self.conditions, self.clip_duration_s, self.units)

    def parse_reply(self, text, *, normalisations=None):
        from . import contracts

        try:
            return contracts.parse_model_json(text, normalisations=normalisations)
        except json.JSONDecodeError as error:
            value = _salvage_check_reply(text, self.conditions, error)
            try:
                self(value)
            except (ValueError, TypeError, KeyError):
                raise error from None
        if normalisations is not None:
            normalisations.extend(value['normalization'])
        return value


def recheck_outcome(findings):
    """汇总三态判定：satisfied > not_satisfied > insufficient；insufficient 永不算满足。

    同一 (beat_id, condition) 出现在多次调用时，任一次 satisfied 即算满足；
    satisfied 与 not_satisfied 同时出现记为 conflict（证据互相矛盾，不能当确认）。
    """
    order = {'insufficient': 0, 'not_satisfied': 1, 'satisfied': 2}
    states, verdicts, reasons = {}, {}, {}
    for row in findings or []:
        key = (row.get('beat_id'), normalise_condition(row.get('condition')))
        verdict = row.get('verdict')
        if verdict not in VERDICTS:
            continue
        if key not in states:
            states[key] = {'beat_id': row.get('beat_id'), 'condition': row.get('condition'),
                           'verdict': verdict, 'conflict': False, 'reasons': []}
            verdicts[key] = set()
        if order[verdict] > order[states[key]['verdict']]:
            states[key]['verdict'] = verdict
        verdicts[key].add(verdict)
        if {'satisfied', 'not_satisfied'} <= verdicts[key]:
            states[key]['conflict'] = True
        reason = row.get('reason')
        if reason and reason not in states[key]['reasons']:
            states[key]['reasons'].append(reason)
    conditions = list(states.values())
    ready = bool(conditions) and all(
        row['verdict'] == 'satisfied' and not row['conflict'] for row in conditions)
    return {'policy': POLICY, 'conditions': conditions, 'ready': ready,
            'counts': {verdict: sum(1 for row in conditions if row['verdict'] == verdict)
                       for verdict in VERDICTS}}


# ---------------------------------------------------------------------------
# 批次规划：合并相邻区间，压住调用次数


def _validated_candidates(locked_candidates):
    if not isinstance(locked_candidates, list):
        raise ValueError('plan:locked_candidates_must_be_list')
    beats = []
    for index, beat in enumerate(locked_candidates):
        if not isinstance(beat, dict):
            raise ValueError(f'plan:candidate[{index}]_not_object')
        beat_id = beat.get('beat_id')
        if not isinstance(beat_id, str) or not beat_id:
            raise ValueError(f'plan:candidate[{index}].beat_id_required')
        ranges = beat.get('ranges')
        if not isinstance(ranges, list) or not ranges:
            raise ValueError(f'plan:{beat_id}:ranges_required')
        for rng in ranges:
            _validated_range(rng, f'plan:{beat_id}')
        checks = []
        for row in beat.get('checks') or []:
            if isinstance(row, dict) and isinstance(row.get('condition'), str) and row['condition'].strip():
                kind = row.get('kind') or ('speech' if row.get('why') == '对白说话条件' else 'must')
                checks.append({'condition': row['condition'].strip(), 'kind': kind,
                               'why': str(row.get('why') or '')})
            else:
                raise ValueError(f'plan:{beat_id}:check_condition_required')
        beats.append({'beat_id': beat_id, 'ranges': ranges, 'checks': checks,
                      'scene_required': beat.get('scene_required', False)})
    return beats


def _check_priority(check):
    return {'identity': 0, 'speech': 1, 'must': 2}.get(check.get('kind'), 3)


def single_scene_candidates(option, *, max_range_s=10.0):
    """Review the selected scene once, splitting long ranges instead of sampling.

    plan_checks' default 1 s padding keeps these clips within 12 seconds.
    Every role is checked in every viewed range; identity is aggregated with
    existential semantics by the scene lock, while mouth evidence stays local.
    """
    ranges = {}
    for beat in option['beats']:
        for rng in beat['ranges']:
            key = (rng['source_sha256'], rng['source_start_s'], rng['source_end_s'])
            if key not in ranges:
                ranges[key] = deepcopy(rng)
            else:
                ranges[key]['record_ids'] = sorted(set(ranges[key]['record_ids'] + rng['record_ids']))
    checks = deepcopy(option['scene_checks']) + [dict(condition=actor + '在所选区间可见且持续动嘴说话',
        kind='speech', story_role=role, why='对白说话条件') for role, actor in option['casting'].items()]
    candidates = []
    for rng in sorted(ranges.values(), key=lambda r: (r['source_sha256'], r['source_start_s'], r['source_end_s'])):
        count = max(1, math.ceil((rng['source_end_s'] - rng['source_start_s']) / max_range_s))
        width = (rng['source_end_s'] - rng['source_start_s']) / count
        for number in range(count):
            part = dict(rng, source_start_s=rng['source_start_s'] + number * width,
                        source_end_s=(rng['source_end_s'] if number == count - 1 else
                                      rng['source_start_s'] + (number + 1) * width))
            candidates.append(dict(beat_id='scene_' + str(len(candidates) + 1), ranges=[part],
                checks=deepcopy(checks), must_conditions=deepcopy(option['scene_checks']),
                record_ids=part['record_ids'], dialogue=False, scene_required=True))
    return candidates


def plan_checks(locked_candidates, *, max_calls,
                max_conditions_per_call=CHECK_DEFAULT_MAX_CONDITIONS,
                max_clip_s=CHECK_DEFAULT_MAX_CLIP_S, pad_s=CLIP_DEFAULT_PAD_S):
    """先为每个scene_required区间保留一次调用，再查身份/说话/画面偏好。

    scene覆盖是预算下限；额外条件超出预算则以 insufficient/budget 返回。
    长区间只复看中间一小段，未展示的条件仍须判 insufficient。
    """
    beats = _validated_candidates(locked_candidates)
    limit = int(max_calls)
    requested_limit = limit
    if limit < 0:
        raise ValueError('plan:max_calls_negative')
    condition_limit = int(max_conditions_per_call)
    max_clip_s, pad_s = float(max_clip_s), float(pad_s)
    window_s = max_clip_s - 2 * pad_s
    if condition_limit < 1:
        raise ValueError('plan:max_conditions_per_call_too_small')
    if not all(math.isfinite(v) for v in (max_clip_s, pad_s)) or pad_s < 0 or window_s <= 0:
        raise ValueError('plan:clip_budget_invalid')
    units = []
    for beat in beats:
        if not beat['checks'] and not beat['scene_required']:
            continue
        entries = {}
        for check in beat['checks']:
            entries.setdefault((beat['beat_id'], normalise_condition(check['condition'])),
                               {'beat_id': beat['beat_id'], **check})
        for rng in beat['ranges']:
            film = int(rng['film'])
            start, end = float(rng['source_start_s']), float(rng['source_end_s'])
            if end - start > window_s:
                middle = (start + end) / 2
                start, end = middle - window_s / 2, middle + window_s / 2
            units.append({'key': (str(rng.get('source_sha256') or ''), film), 'film': film,
                          'source_sha256': rng.get('source_sha256'),
                          'start': start, 'end': end,
                          'beat_id': beat['beat_id'], 'scene_required': beat['scene_required'],
                          'conditions': dict(entries)})
    units.sort(key=lambda unit: (unit['key'], unit['start'], unit['end']))
    merged = []
    for unit in units:
        if merged and merged[-1]['key'] == unit['key'] \
                and unit['start'] - min(row['end'] for row in merged[-1]['units']) <= MERGE_GAP_S \
                and max(merged[-1]['end'], unit['end']) - merged[-1]['start'] <= window_s:
            target = merged[-1]
            target['end'] = max(target['end'], unit['end'])
            target['conditions'].update(unit['conditions'])
            target['units'].append(unit)
        else:
            merged.append({'key': unit['key'], 'film': unit['film'],
                           'source_sha256': unit['source_sha256'], 'start': unit['start'],
                           'end': unit['end'], 'conditions': dict(unit['conditions']), 'units': [unit]})
    batches = []
    for group in merged:
        ordered = sorted(group['conditions'], key=lambda key: _check_priority(group['conditions'][key]))
        for offset in range(0, max(1, len(ordered)), condition_limit):
            keys = ordered[offset:offset + condition_limit]
            scene_first = offset == 0 and any(u['scene_required'] for u in group['units'])
            related = [unit for unit in group['units'] if scene_first or any(key in unit['conditions'] for key in keys)]
            batches.append(dict(group, conditions={key: group['conditions'][key] for key in keys},
                                start=min(unit['start'] for unit in related),
                                end=max(unit['end'] for unit in related), units=len(related),
                                beats=sorted({u['beat_id'] for u in related}), scene_first=scene_first,
                                mandatory=scene_first or any(group['conditions'][key]['kind'] == 'identity'
                                                             for key in keys)))
    minimum_scene_calls = sum(group['scene_first'] for group in batches)
    minimum_calls = sum(group['mandatory'] for group in batches)
    # Scene/identity coverage is mandatory; the requested budget limits extra checks.
    limit = max(limit, minimum_calls)
    # Stable sort keeps source/time order within each priority level.
    batches.sort(key=lambda group: -1 if group['scene_first'] else
                 min((_check_priority(row) for row in group['conditions'].values()), default=3))
    kept, dropped = batches[:limit], batches[limit:]
    covered = set()
    for group in kept:
        covered.update(group['conditions'])
    calls = [{'call_id': f'chk_{index + 1:02d}', 'film': group['film'],
              'source_sha256': group['source_sha256'],
              'source_start_s': group['start'], 'source_end_s': group['end'],
              'beats': group['beats'],
              'conditions': list(group['conditions'].values()),
              'merged_ranges': group['units']} for index, group in enumerate(kept)]
    deferred = []
    for group in dropped:
        for key in group['conditions']:
            if key not in covered:
                row = dict(group['conditions'][key])
                deferred.append({**row, 'verdict': 'insufficient', 'reason': 'budget'})
                covered.add(key)
    total = len({key for group in merged for key in group['conditions']})
    return {'policy': POLICY, 'kind': 'condition_recheck_plan', 'max_calls': limit,
            'requested_max_calls': requested_limit, 'minimum_scene_calls': minimum_scene_calls,
            'minimum_required_calls': minimum_calls,
            'max_conditions_per_call': condition_limit, 'max_clip_s': max_clip_s, 'pad_s': pad_s,
            'merge_gap_s': MERGE_GAP_S, 'expected_calls': len(calls), 'calls': calls,
            'deferred': deferred, 'total_conditions': total,
            'dropped_groups': len(dropped)}


# ---------------------------------------------------------------------------
# 事实写回区：append-only JSONL，只加不改


_APPEND_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


def _path_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _APPEND_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


def _require_sha(source_sha256):
    text = source_sha256 if isinstance(source_sha256, str) else ''
    if len(text) != 64 or any(char not in '0123456789abcdef' for char in text):
        raise ValueError(f'fact_store:source_sha256_invalid:{source_sha256!r}')
    return text


def _finite_range(start_s, end_s):
    start, end = float(start_s), float(end_s)
    if not all(math.isfinite(value) for value in (start, end)) or end < start:
        raise ValueError(f'fact_store:range_invalid:{start}-{end}')
    return start, end


def _normalise_evidence_times(value):
    if value in (None, []):
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError('fact_store:evidence_source_times_invalid')
    pairs = value if all(isinstance(row, list) and len(row) == 2 for row in value) else [value]
    result = []
    for pair in pairs:
        if len(pair) != 2 or not all(isinstance(item, (int, float)) and math.isfinite(item)
                                     for item in pair):
            raise ValueError('fact_store:evidence_source_times_invalid')
        result.append([float(pair[0]), float(pair[1])])
    return result


class FactStore:
    """verified_facts/<source_sha256>.jsonl：复看确认的事实，逐行追加，永不改写。

    事实只在"归一化条件完全相同且查询区间完全落在已核查区间内"时才可复用。
    """

    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'verified_facts'

    def path_for(self, source_sha256) -> Path:
        return self.directory / f'{_require_sha(source_sha256)}.jsonl'

    def append(self, record) -> dict:
        if not isinstance(record, dict):
            raise ValueError('fact_store:record_not_object')
        entry = dict(record)
        sha = _require_sha(entry.get('source_sha256'))
        entry['source_sha256'] = sha
        entry['source_start_s'], entry['source_end_s'] = _finite_range(
            entry.get('source_start_s'), entry.get('source_end_s'))
        condition = entry.get('condition')
        if not isinstance(condition, str) or not condition.strip():
            raise ValueError('fact_store:condition_required')
        if entry.get('verdict') not in VERDICTS:
            raise ValueError(f'fact_store:verdict_invalid:{entry.get("verdict")}')
        if not isinstance(entry.get('observed'), str) or not entry['observed'].strip():
            raise ValueError('fact_store:observed_required')
        entry['evidence_source_times'] = _normalise_evidence_times(entry.get('evidence_source_times'))
        for field in ('call_id', 'clip_sha256'):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError(f'fact_store:{field}_required')
        entry.setdefault('created_at', datetime.now(timezone.utc).isoformat())
        entry.setdefault('origin', FACT_ORIGIN)
        path = self.path_for(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with _path_lock(path):
            with path.open('a', encoding='utf-8') as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.write(line + '\n')
                    handle.flush()
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return entry

    def records(self, source_sha256) -> list:
        path = self.path_for(source_sha256)
        if not path.is_file():
            return []
        rows = []
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f'fact_store:corrupt_line:{path}:{number}:{error}') from error
        return rows

    def lookup(self, source_sha256, start_s, end_s) -> list:
        """与查询区间重叠的事实（不分条件、不分判定，供人工/程序排查）。"""
        start, end = _finite_range(start_s, end_s)
        return [row for row in self.records(source_sha256)
                if float(row['source_start_s']) < end + 1e-9
                and float(row['source_end_s']) > start - 1e-9]

    def reusable(self, source_sha256, condition, start_s, end_s) -> list:
        """可直接复用的事实：条件归一化相同，且查询区间完全在已核查区间内。"""
        start, end = _finite_range(start_s, end_s)
        needle = normalise_condition(condition)
        if not needle:
            return []
        return [row for row in self.lookup(source_sha256, start_s, end_s)
                if normalise_condition(row.get('condition')) == needle
                and float(row['source_start_s']) - 1e-6 <= start
                and end <= float(row['source_end_s']) + 1e-6
                and (needle != '__scene__' or (abs(float(row['source_start_s']) - start) <= 1e-6
                     and abs(float(row['source_end_s']) - end) <= 1e-6))]


# ---------------------------------------------------------------------------
# 组装（模型调用由外部注入；这里只做编排，不改素材记录）


def compare_options(options, skeleton_summary, shot_cache, out_dir, vision_model, *,
                    sources=None, frame_extractor=None, max_tiles_per_option=12):
    """S4 第一步：宫格比较。一张图、一次视觉调用比较全部候选。"""
    sheet = comparison_sheet(options, shot_cache, out_dir,
                             sources=sources, frame_extractor=frame_extractor,
                             max_tiles_per_option=max_tiles_per_option)
    prompt = compare_prompt(options, skeleton_summary, tiles=sheet['tiles'])
    verdict = vision_model('material_s4_compare', prompt, sheet['path'],
                           lambda value: validate_compare(value, options), image=True)
    return {'sheet': sheet, 'verdict': verdict}


def recheck_plan(plan, sources, cache_dir, vision_model, fact_store, *,
                 pad_s=CLIP_DEFAULT_PAD_S, max_bytes=CLIP_DEFAULT_MAX_BYTES,
                 subtitle_masks=None, call_name='material_s4_check'):
    """S4 第二步：按计划截短片、逐条判定、映射回原片时间并写入事实区。"""
    findings = [dict(row) for row in plan.get('deferred', [])]
    executed = []
    for index, call in enumerate(plan.get('calls', [])):
        name = f"{call_name}_{index + 1:02d}"
        source = sources.get(call.get('source_sha256'))
        if source is None:
            findings.extend({**row, 'verdict': 'insufficient', 'reason': 'source_unavailable'}
                            for row in call['conditions'])
            executed.append({'call_id': name, 'status': 'source_unavailable',
                             'conditions': len(call['conditions'])})
            continue
        clip = confirm_clip(source, call['source_start_s'], call['source_end_s'], cache_dir,
                            pad_s=min(pad_s, plan.get('pad_s', pad_s)),
                            max_bytes=max_bytes, subtitle_masks=subtitle_masks)
        conditions = call['conditions']
        beats = [{'beat_id': beat_id} for beat_id in call['beats']]
        prompt = check_prompt(beats, conditions, clip)
        value = vision_model(name, prompt, clip['path'],
                             CheckReplyValidator(conditions, clip['duration_s'], call['beats']),
                             image=False)
        by_id = {f'c{number}': row for number, row in enumerate(conditions, 1)}
        for row in value['conditions']:
            evidence = row.get('evidence') or {}
            mapped = check_evidence_source_times(clip, evidence, row['verdict'],
                                                 call['source_start_s'], call['source_end_s'])
            finding = {'beat_id': by_id[row['id']]['beat_id'],
                       'condition': row['condition'], **mapped,
                       'observed': evidence.get('observed'),
                       'reason': FACT_ORIGIN, 'call_id': name}
            findings.append(finding)
            if fact_store is not None:
                fact_store.append({'source_sha256': clip['source_sha256'],
                                   'source_start_s': call['source_start_s'],
                                   'source_end_s': call['source_end_s'],
                                   'condition': row['condition'], **mapped,
                                   'observed': evidence.get('observed'),
                                   'call_id': name,
                                   'clip_sha256': clip['sha256'],
                                   'beat_id': finding['beat_id']})
        for scene in value['scenes']:
            if fact_store is not None:
                fact_store.append(dict(source_sha256=clip['source_sha256'],
                    source_start_s=call['source_start_s'], source_end_s=call['source_end_s'],
                    condition='__scene__', verdict='satisfied', observed=scene['text'],
                    evidence_source_times=[[call['source_start_s'], call['source_end_s']]],
                    call_id=name, clip_sha256=clip['sha256'], beat_id=scene['unit']))
        executed.append({'call_id': name, 'status': 'checked',
                         'clip': {key: clip[key] for key in
                                  ('path', 'sha256', 'source_start_s', 'source_end_s',
                                   'duration_s', 'size_bytes', 'fps_used')},
                         'conditions': len(conditions)})
    return {'policy': POLICY, 'calls': executed, 'findings': findings,
            'outcome': recheck_outcome(findings)}
