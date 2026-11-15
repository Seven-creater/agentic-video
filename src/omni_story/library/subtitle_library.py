"""Subtitle-visible library inputs; source dialogue never becomes visual evidence."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from .media import probe_media, sha256_file, verify_source
from .state import LibraryStopped, json_sha, write_json

POLICY = 'silent_subtitle_visible_v1'
PROTOCOL = 'short_shot_grounded_v3'
PROMPT_VERSION = 'independent_visible_window_v5'


def visible(configuration):
    return configuration.get('source_visual_policy') == POLICY


def scene_cuts(source, root):
    """Cheap cut proposals, not asserted episode boundaries or exact frame cuts."""
    folder = Path(root) / 'navigation' / source['sha256'][:16]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'timeline.json'
    if target.exists():
        saved = json.loads(target.read_text())
        if saved['source_sha256'] != source['sha256']:
            raise LibraryStopped('subtitle_library:cut_source_changed')
        return saved['cut_times_s']
    raw = folder / 'scene_candidates.txt'
    with raw.open('w') as handle:
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '2', '-skip_frame', 'nokey',
            '-i', str(verify_source(source)), '-an', '-sn', '-dn', '-vf',
            "fps=2,scale=320:-2,select='gt(scene,0.25)',metadata=mode=print:file=-",
            '-fps_mode', 'vfr', '-f', 'null', '-'], stdout=handle, check=True)
    cuts = sorted(set(float(x) for x in re.findall(r'pts_time:([0-9.]+)', raw.read_text())))
    write_json(target, dict(source_sha256=source['sha256'], cut_times_s=cuts,
        method='ffmpeg_keyframe_scene_0.25_at_2fps_320px', precision='source_keyframe_limited',
        limitations=['Coarse keyframe cut proposals only; episode/title/story boundaries are separately observed.']))
    return cuts


def prepare_visible_window(source, start, end, root, window_id):
    """Encode the actual upload once, with a clock outside the original picture."""
    spec = dict(policy=POLICY, kind='continuous_window', source_sha256=source['sha256'],
        source_start_s=start, source_end_s=end, fps=12, picture_width=1280,
        clock_band_px=32, max_bytes=7_800_000, crf=24)
    folder = Path(root) / json_sha(spec)[:20]
    folder.mkdir(parents=True, exist_ok=True)
    lineage = folder / 'lineage.json'
    if lineage.exists():
        saved = json.loads(lineage.read_text())
        if saved['spec'] != spec or sha256_file(saved['path']) != saved['sha256']:
            raise LibraryStopped('subtitle_library:proxy_changed')
        return saved
    if shutil.disk_usage(folder).free < 5 * 2**30:
        raise LibraryStopped('subtitle_library:disk_below_5gib')
    path = verify_source(source)
    target = folder / 'window.mp4'
    partial = folder / 'window.part.mp4'
    vf = ("scale=1280:-2,fps=12,pad=iw:ih+32:0:32:black,"
          r"drawtext=text='LOCAL %{pts\:hms}':x=8:y=4:fontsize=20:fontcolor=white")
    base = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '2',
        '-ss', str(start), '-i', str(path), '-t', str(end-start), '-map', '0:v:0',
        '-an', '-sn', '-dn', '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast',
        '-threads', '2', '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
    subprocess.run([*base, '-crf', '24', str(partial)], check=True, capture_output=True)
    if partial.stat().st_size >= spec['max_bytes']:
        # Keep readable geometry; cap bytes without making a second permanent copy.
        bitrate = int(spec['max_bytes'] * 8 * .86 / (end-start))
        subprocess.run([*base, '-b:v', str(bitrate), '-maxrate', str(bitrate),
            '-bufsize', str(bitrate*2), str(partial)], check=True, capture_output=True)
    if partial.stat().st_size >= 8_000_000:
        raise LibraryStopped('subtitle_library:proxy_over_upload_limit')
    measured = probe_media(partial)
    if abs(measured['duration_s']-(end-start)) > .2:
        raise LibraryStopped('subtitle_library:proxy_duration_changed')
    partial.replace(target)
    result = dict(spec=spec, kind='continuous_window', window_id=window_id,
        source_id=source['source_id'], source_path=str(path), source_sha256=source['sha256'],
        source_start_s=start, source_end_s=end, source_offset_s=start,
        duration_s=end-start, media_duration_s=measured['duration_s'],
        path=str(target), sha256=sha256_file(target), size_bytes=target.stat().st_size,
        metadata=measured, audio_stream_index=None, audio_present=False,
        source_visual_policy=POLICY, source_subtitle_masks=[], reference_board=[],
        time_mapping='source_time_s = source_offset_s + proxy_time_s', mapping_tolerance_s=1/12,
        observation_geometry='1280px original picture below a separate 32px clock band; boxes exclude clock band')
    write_json(lineage, result)
    return result


def prompt(window, bank, previous=None):
    from .visual_indexer import observation_prompt
    base = observation_prompt(window, bank)
    base = base.replace('纯视觉素材库', '画面事实与原字幕分开记录的素材库').replace(
        '仅依据实际画面，忽略原字幕，不使用电影常识、原对白或熟悉的剧情补身份和动机。',
        '允许阅读原字幕帮助理解，但 summary/scene/events/roles/usable_ranges 只记画面依据；不得把台词提及的动作当作已经看见。')
    base = base.replace('CURRENT', 'LOCAL')
    base = base.replace('记录完整的开始—变化—结果、听者反应和可用停顿，',
        '记录可见人物、物件、表情和状态变化；不补写未显示的动作前后过程，')
    base = base.replace('{"window_id":'+json.dumps(window['window_id'])+',"source_id":'+
                        json.dumps(window['source_id'])+',', '{', 1)
    base = base.replace('"before_state":"动作前","after_state":"动作后","complete_action_visible":true,', '')
    instructions = """以下是同一份观察的必要字段，必须随其他字段一起输出：
视频顶部32像素黑条是计时；所有人物框/字幕框坐标仅对应黑条下方原始画面，归一化x/y/w/h。
原字幕不遮挡，音频已去除。不要声称听到原声。模糊文字不补写，speaker_role_id不明确时null。
标题卡/片尾的装饰人物不算剧情说话人；story_segments区分story/title/credits/transition/uncertain。
逐句字幕独立记在source_text，不放入events.observed_fact。context解释必须标visual/source_text/mixed来源。
source_text.text只逐字抄写当前画面能辨认的文字，中文和日文分条；不翻译、不拼接包装与字幕、不补成完整句。
例如画面只写“动物饼干”，不能补成“加了动物饼干可可”。读不清的部分用空串或标记不可辨，不能猜。
本次是模型转录、尚无独立OCR核对，source_text.uncertain始终true；字幕只能提供线索，不证明动作已发生。
嘴张开不自动等于说话；speech_fit区分嘴可见、疑似说话、反应、表情、看不清。
吃东西、咀嚼、张嘴惊讶不等于讲话；看不见完整动作前后就complete_action_visible=false。
外形描述只写可见颜色、轮廓和图案；没有衣物轮廓时不要把毛色图案解释成帽子或服装。
有字幕仍可用，成片会局部半透明马赛克后加新配音字幕，不因此删除合适片段。
usable_ranges不可跨越明显片头/片尾/不同集；仅给剧情可剪区间。真实分集不确定就不编集号。
当前视频已包含相邻画面的短前文，独立观察本次实际视频，不用前一段文字剧情续写当前片段。
source_text.kind仅用dialogue/title/credits/decoration/in_scene/uncertain；box看不清可null。
即使读不清字幕内容，也要记录可见对白文字的大致区域与出现时间；不把稀疏观看当作无字幕证明。
source_text若只确认出现的一刻，允许local_start_s=local_end_s并time_precision=point；不要臆造持续时间。
events/usable_ranges必须为正长度。所有自己填写的时间不得超过当前duration_s；story_segments的时间由程序附加。
原片音频未提供，summary及events不得声称存在背景音乐、听到叫声或台词；张嘴只描述可见表情。
story_segments、source_text、context没有证据时用空数组，不能省略。
window_id和source_id由程序依据实际上传媒体附加，不在输出中填写。
这是检索导航记录，描述应简洁：summary不超过100字，单条observed_fact约30字；不要在多个字段重复复述。
不要求为每个动作补写起因、过程完整性或结局；未显示的部分不写。affordances仅给1至2个有画面依据的条件性用途。
"""
    extra = dict(story_segments=[dict(local_start_s=0,local_end_s=2,kind='story',
        summary='画面摘要',episode_label=None)],source_text=[dict(local_start_s=0,local_end_s=2,
        kind='dialogue',text='实际可辨文字或空串',box=[.1,.8,.8,.15],speaker_role_id=None,
        uncertain=True,overlap_risk='与脸/手/物件的位置关系',time_precision='model_estimate')],
        context=[dict(interpretation='原作讨论内容或推断',support='source_text',uncertain=True)],
        subtitle_localization=dict(precision='model_estimate',requires_selected_clip_refinement=True))
    source_shots = window.get('source_shots', [])
    if source_shots:
        instructions += (
            'source_shots是程序从原片检测的候选切镜区间，不是剧情分类。story_segments只填shot_id/kind/summary/episode_label。'
            '不要输出story_segments的local_start_s/local_end_s，程序会按shot_id绑定已提供的时间。'
            '切镜检测可能漏切或多切，区间内混有剧情和标题、或看不清时kind=uncertain并说明；不能一概认作transition。'
            'events及usable_ranges仍记录实际看见的变化，依据顶部LOCAL时钟；不把相邻镜头内容提前或延后。'
            '所有story_segments只是粗分类，正式选片还需重新观察。\n')
        extra['story_segments'] = [dict(shot_id=source_shots[0]['shot_id'],kind='uncertain',
            summary='简短分类依据；看不清就说明',episode_label=None)]
    ending = '"identity_merges":[],"uncertainties":[],"limitations":[]}'
    if ending not in base:
        raise ValueError('subtitle_library:base_schema_changed')
    base = base.replace(ending, ending[:-1]+','+json.dumps(extra,ensure_ascii=False)[1:])
    base = base.replace('仅返回一个JSON', instructions+'\n仅返回一个JSON', 1)
    return base+'\n附加上下文（不是输出结构）：'+json.dumps(
        {'source_shots': source_shots,
         'prompt_version': PROMPT_VERSION},ensure_ascii=False)


def bind_program_metadata(value, window):
    """Attach request-owned IDs/times only when omitted; never retime model claims."""
    for key in ('window_id','source_id'):
        if key not in value:
            value[key] = window[key]
            value.setdefault('technical_adjustments', []).append(dict(
                kind='id_from_bound_uploaded_window',field=key,value=window[key]))
    shots = {s['shot_id']: s for s in window.get('source_shots', [])}
    for row in value.get('story_segments', []):
        if not isinstance(row,dict) or 'local_start_s' in row or 'local_end_s' in row:
            continue
        shot = shots.get(row.get('shot_id'))
        if shot is not None:
            row.update(local_start_s=shot['local_start_s'],local_end_s=shot['local_end_s'])
            value.setdefault('technical_adjustments', []).append(dict(
                kind='navigation_bounds_from_supplied_shot_id',**shot))


def supplied_context(prompt_text, window):
    """Recover the exact original reference IDs for a received-only continuation."""
    decoder, matches = json.JSONDecoder(), []
    for match in re.finditer(r'\{', prompt_text):
        try:
            row,_ = decoder.raw_decode(prompt_text,match.start())
        except ValueError:
            continue
        if (isinstance(row,dict) and row.get('window_id') == window['window_id']
            and row.get('source_id') == window['source_id'] and isinstance(row.get('known_entities'),list)
            and row.get('duration_s') == window['duration_s']):
            matches.append(row)
    if len(matches) != 1:
        raise LibraryStopped('subtitle_library:received_reference_context_not_unique')
    return matches[0]


def validate_extra(value, window):
    from .contracts import numeric_error
    duration = window['source_end_s'] - window['source_start_s']
    for key, kinds in (('story_segments', {'story', 'title', 'credits', 'transition', 'uncertain'}),
                       ('source_text', {'dialogue', 'title', 'credits', 'decoration', 'in_scene', 'uncertain'})):
        if not isinstance(value.get(key), list):
            raise ValueError('subtitle_library:' + key + '_array_required')
        for index, row in enumerate(value[key]):
            if not isinstance(row, dict):
                raise ValueError('subtitle_library:'+key+'_object_required')
            a, b = row.get('local_start_s'), row.get('local_end_s')
            if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b))
                    or not 0 <= a <= b <= duration + .001 or (key != 'source_text' and a == b)
                    or row.get('kind') not in kinds):
                raise numeric_error('subtitle_library:' + key + '_invalid_interval_or_kind',f'$.{key}[{index}]',
                    dict(local_start_s=a,local_end_s=b,kind=row.get('kind')),
                    dict(duration_s=duration,allowed_kinds=sorted(kinds),point_allowed=key=='source_text'))
            if key == 'source_text':
                box = row.get('box')
                if box is not None and (not isinstance(box, list) or len(box) != 4 or
                    any(type(x) not in (int, float) or not math.isfinite(x) for x in box) or
                    not (0 <= box[0] < 1 and 0 <= box[1] < 1 and 0 < box[2] <= 1-box[0]+1e-6
                         and 0 < box[3] <= 1-box[1]+1e-6)):
                    raise ValueError('subtitle_library:text_box_invalid')
                if not isinstance(row.get('text'), str):
                    raise ValueError('subtitle_library:text_string_required')
    if not isinstance(value.get('context'), list):
        raise ValueError('subtitle_library:context_array_required')
    for row in value['context']:
        if row.get('support') not in {'visual', 'source_text', 'mixed'}:
            raise ValueError('subtitle_library:context_provenance_required')
    # Historical supplied times remain unverified model hints. Program shot
    # bounds are stored separately; a transcription mismatch is not corrected
    # by silently moving the model's described action to another moment.


def text_hints(rows):
    """A confident model reply is still not an independently checked transcript."""
    return [dict(row, model_reported_uncertain=row.get('uncertain'), uncertain=True,
                 verification='unverified_model_transcription') for row in rows]


def rejected_visual_evidence(window):
    """Received negative image checks cannot become positive retrieval evidence."""
    roles = {ref['role_id'] for ref in window.get('entity_exemplars', [])
        if ref.get('reference_check', {}).get('approved') is False
        and (ref.get('reference_check', {}).get('evidence') or {}).get('status') == 'received'}
    observation = window['observation']
    events = {i for i,row in enumerate(observation.get('events', [])) if roles & set(row.get('role_ids', []))}
    ranges = {i for i,row in enumerate(observation.get('usable_ranges', []))
        if roles & set(row.get('role_ids', [])) or events & set(row.get('event_indices', []))}
    return roles,events,ranges


def story_hints(window):
    """Do not interpret a model's own group ordinals as supplied shot IDs."""
    shots = window.get('source_shots', [])
    rows = window['observation'].get('story_segments', [])
    if not shots or [r.get('shot_id') for r in rows] != [s['shot_id'] for s in shots]:
        return []
    if any(abs(row[key]-shot[key]) > .001 for row,shot in zip(rows,shots)
           for key in ('local_start_s','local_end_s')):
        return []
    return rows


def padded_box(x, y, w, h):
    # The inspected bilingual sample leaked its lower Japanese row. Margins
    # are local to each selected box; they do not cover a fixed full-width band.
    left, top = max(0, x-.012), max(0, y-.015)
    return dict(x=left, y=top, w=min(1, x+w+.012)-left, h=min(1, y+h+.03)-top)


def treatments(window):
    """Conservative model-estimated proposals; retain provenance for refinement."""
    result = []
    for row in window['observation'].get('source_text', []):
        if row.get('kind') != 'dialogue' or not row.get('box') or row['local_start_s'] == row['local_end_s']:
            continue
        x, y, w, h = row['box']
        x0, y0 = max(0, x-.008), max(0, y-.008)
        result.append(dict(x=x0, y=y0, w=min(1, x+w+.008)-x0, h=min(1, y+h+.008)-y0,
            start_s=window['source_start_s']+row['local_start_s'],
            end_s=window['source_start_s']+row['local_end_s'], mode='mosaic', opacity=.92,
            block_px=18, precision='model_estimate', requires_selected_clip_refinement=True))
    return result


def refine_selected_subtitles(rows, windows, sources, model, cache):
    """Inspect only the source intervals actually used by a new redubbed edit."""
    from .audio_contracts import validate_regions
    by_window = {w['window_id']: w for w in windows}
    source_map = {s['source_id']: s for s in sources['sources']}
    selected = [row for row in rows if by_window[row['window_id']].get('source_visual_policy') == POLICY]
    spans = {}
    for row in selected:
        spans.setdefault(row['source_id'], []).append(row.get('loop_range') or [row['source_in_s'], row['source_out_s']])
    all_regions = {}
    for source_id, intervals in spans.items():
        merged = []
        for a, b in sorted(intervals):
            if merged and a <= merged[-1][1]+.001:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a,b])
        source = source_map[source_id]
        regions = []
        for low, high in merged:
            count = math.ceil((high-low)/12)
            for i in range(count):
                start, end = low+(high-low)*i/count, low+(high-low)*(i+1)/count
                key = json_sha([source['sha256'], start, end, 'selected_subtitle_v2_bilingual_margin'])[:16]
                media = prepare_visible_window(source, start, end, Path(cache)/'subtitle_refinement', 'subtitle_'+key)
                duration = end-start
                instruction = (
                    '只定位本次成片选中的短片里需要替换的原对白字幕。视频静音、原字幕保留。'
                    '顶部32像素黑条是计时，坐标为下方原画面归一化x/y/w/h，时间是本次片段本地秒。'
                    '双语两行都要覆盖；同一镜头的固定字幕区域使用稳定合并框，覆盖出现和消失边界并留少量余量。'
                    '不遮人物/物件、场景招牌、标题或装饰；只输出有视觉依据的字幕区域。字幕与主体重叠时写limitations。'
                    '看不清原文仍可定位区域；没有字幕就masks=[]。不把字幕内容改成动作事实。'
                    f'时长{duration:.6f}秒。返回JSON：'
                    '{"masks":[{"x":0.1,"y":0.8,"w":0.8,"h":0.15,"start_s":0,"end_s":1,'
                    '"color":"#000000","reason":"实际字幕区域"}],"protected_text":[],"limitations":[]}。')
                answer = model.call('subtitle_regions_'+key, instruction, media['path'],
                    lambda value: validate_regions(value, duration),
                    scope={k: media[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
                for mask in answer['masks']:
                    regions.append(dict(**padded_box(mask['x'],mask['y'],mask['w'],mask['h']),
                        start_s=start+mask['start_s'],end_s=start+mask['end_s'],mode='mosaic',
                        opacity=.92,block_px=18,precision='selected_continuous_clip_model_estimate',
                        requires_selected_clip_refinement=False,refinement_stage='subtitle_regions_'+key))
                write_json(Path(cache)/'subtitle_refinement'/('result_'+key+'.json'),
                    dict(source_sha256=source['sha256'],source_start_s=start,source_end_s=end,
                         result=answer,precision='model_estimate_not_native_frame_OCR'))
        all_regions[source_id] = regions
    for row in selected:
        low, high = row.get('loop_range') or [row['source_in_s'],row['source_out_s']]
        row['source_subtitle_treatments'] = [r for r in all_regions[row['source_id']]
                                           if r['start_s'] < high and low < r['end_s']]
        row['source_subtitle_masks'] = []
