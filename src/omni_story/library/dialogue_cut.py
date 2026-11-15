"""Slot dialogue, measured speech, one audiovisual review."""
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import re

import numpy as np

from . import audio_render, render, tts, visual_audio
from .scene_route import VOICE_GENDER, reference_line_role
from .state import LibraryStopped, json_sha, write_json

RATES = dict(Ethan=4.6, Moon=3.9, Kai=3.9, Cherry=4.3, Serena=3.9)
FPS = 30
SR = audio_render.SAMPLE_RATE


def _picture_range(slot):
    return slot.get('loop_range') or [slot['source_start_s'], slot['source_end_s']]


def _max_chars(slot, voice):
    if slot.get('loop_range'):
        return None
    start, end = _picture_range(slot)
    return math.floor((end-start)*RATES[voice])


def _script_slots(scene, voices):
    return [p for p in scene['speech_slots'] if p.get('role') in voices]


def voices_for(lock):
    voices = {}
    bindings = lock['cast_bindings']
    # Reuse a limited voice palette across separate conversations, never prefer
    # global uniqueness over distinguishing people who actually speak together.
    talk, neighbours = {}, {role: set() for role in bindings}
    for scene in lock['scenes']:
        active = {r['role'] for r in scene.get('reference_lines', []) if r.get('role') in bindings}
        if not active:
            active = {s['role'] for s in scene.get('speech_slots', []) if s.get('role') in bindings}
        for role in active:
            neighbours[role].update(active-{role})
        for slot in scene.get('speech_slots', []):
            if slot.get('role'):
                talk[slot['role']] = talk.get(slot['role'], 0) + slot['source_end_s'] - slot['source_start_s']
    while len(voices) < len(bindings):
        role = max((r for r in bindings if r not in voices), key=lambda r: (
            len({voices[n] for n in neighbours[r] if n in voices}), len(neighbours[r]), talk.get(r, 0)))
        gender = bindings[role].get('gender')
        choices = [v for v, g in VOICE_GENDER.items() if g == gender] or list(VOICE_GENDER)
        voices[role] = min(choices, key=lambda v: (
            sum(voices.get(n) == v for n in neighbours[role]), list(voices.values()).count(v)))
    return voices


def script_prompt(lock, reference, strategy, voices):
    scenes = [dict(scene_id=s['scene_id'], picture=s['picture'], roles=s['roles'],
        role_bindings={role: dict(binding, actor=s['picture'].get('actors', {}).get(binding.get('role_id'), {}).get('name', binding['actor']))
                       for role, binding in s['role_bindings'].items()}, conversation=s.get('conversation'),
        template=s.get('template'),
        reference_lines=[dict(line, role=reference_line_role(line, s['roles'])) for line in s['reference_lines']],
        slots=[dict(slot_id=p['slot_id'], role=p['role'], seconds=_picture_range(p)[1]-_picture_range(p)[0],
            loopable=p.get('loopable', False), conversation_id=p.get('conversation_id'),
            max_chars=_max_chars(p, voices[p['role']]))
            for p in sorted(_script_slots(s, voices), key=lambda p: not p.get('loopable', False))])
        for s in lock['scenes'] if s['reference_lines']]
    return ('按场景顺序写可直接配音的中文台词，保留参考结构、轮次、包袱；物件换成picture中可见的东西。'
        '只输出JSON {"reference_roles":{"场景":["逐条参考行的说话角色"]},"facts":["必须说清的具体事项"],'
        '"lines":[{"line_id":"l1","scene_id":"场景","slot_id":"槽位","role":"角色",'
        '"reference_line_ids":[1],"text":"台词","facts":[0]}]}，lines按播放顺序。'
        'reference_roles逐条抄写reference_lines中已经核对的role；旧数据未知时才根据原文问答判断，不能根据素材反推。'
        '已知role须原样保留，未知role从场景roles判断，不能根据素材时长、槽位多少或改写结果反推角色。'
        '每条改写台词列出它承接的reference_line_ids，只能承接同角色参考行；所有参考行都必须按原顺序有对应台词，'
        '可合并同角色相邻短句，不得删掉任何具体问题、论证、权利主张或结尾。'
        '每句role必须等于槽位role，槽位必须属于该scene；短句无需凑字数，一个字的应答也可保留。'
        'max_chars=null表示静态画面可无限循环，句长按参考内容决定；否则在可容纳字数内完整表达原句。'
        '未出现在槽位中的角色不能说话，但绝不能省略或转述他的台词；素材不足由选镜头阶段解决。'
        '保留template和每条参考行function：问题必须由提问者当场问，纠正由回答者当场说；'
        '禁止把现场问答改成“你刚才问”“第一回你说”等事后复述，除非参考本来就是复述。'
        '角色、物件、名字可适配素材，但同一对象须前后一致，重复、误听、递进及结尾反差都要实际演出来；'
        'role_bindings是最终画面演员与故事角色的对应，appearance是实际外观、actor是已复核身份；'
        'actor=null表示姓名未知，可使用与外观一致的称呼，不能借用其他在场人物的身份。'
        '自报姓名、物种、外貌和对方称呼都必须与绑定一致，不能把在场的其他人物当成说话者。'
        '不得用新增自我介绍替代招呼，不得把收尾吐槽合成别人的重复应答。'
        '内容具体：若宣布N个问题，后文须说清N个不同的具体问题。每项facts至少被一句引用。'
        'speech_mode不含旁白（narration或mixed）时禁止旁白。保留参考各角色的发言比例，参考中台词最多的角色仍须台词最多；'
        '原角色保留自己的具体内容，如投诉者保留投诉及其提出的各项具体问题；不得因槽位短或不可循环而转交其他角色。'
        '优先选loopable=true的固定视角，一句台词只用这个视角，可连续循环任意多次、多句继续复用。'
        '连续循环或隔镜头复用都只允许已审核静态区间，角色总台词量不受槽位数量限制。'
        'loopable=false的画面只能用一次，插入反应镜头也不能让它重新使用。'
        '不要为遮循环接缝插入其他视角或反应镜头，台词音频必须连续。'
        'visual_scenes_input中的无对白段落也须保留：额外输出visual_scenes数组，每项为'
        '{"scene_id":"场景","ranges":[[原片起秒,原片止秒]]}，按可见事件选择画面，'
        '只用allowed_ranges内的区间，不重复画面；每场总时长须等于target_s。无对白段落不编旁白。\n' + json.dumps(dict(
            reference={k: reference.get(k) for k in ('theme', 'intended_takeaway', 'strategy')},
            speech_mode=strategy['speech_mode'], scenes=scenes,
            visual_scenes_input=[dict(scene_id=s['scene_id'], template=s.get('template'), picture=s['picture'],
                allowed_ranges=s['allowed_ranges'], events=s.get('visual_events', []),
                target_s=min(s['end_s']-s['start_s'], sum(r['source_end_s']-r['source_start_s']
                    for r in s['allowed_ranges']))) for s in lock['scenes'] if not s['reference_lines']]), ensure_ascii=False))


def validate_script(value, lock, voices):
    slots = {(s['scene_id'], p['slot_id']): p for s in lock['scenes'] if s['reference_lines']
             for p in _script_slots(s, voices)}
    errors, covered = [], set()
    for line in value.get('lines', []):
        count = len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', line.get('text', '')))
        if count < 1:
            errors.append(f'{line.get("line_id")}: 台词须有实际发声内容')
        slot = slots.get((line.get('scene_id'), line.get('slot_id')))
        allowed = [p['slot_id'] for (sid, _), p in slots.items()
                   if sid == line.get('scene_id') and p['role'] == line.get('role')]
        if slot is None or slot['role'] != line.get('role'):
            errors.append(f'{line.get("line_id")}: scene={line.get("scene_id")}, role={line.get("role")}, '
                f'slot={line.get("slot_id")}不匹配；允许slot_ids={allowed}')
        else:
            maximum = _max_chars(slot, voices[slot['role']])
            if maximum is not None and count > maximum*1.1:
                errors.append(f'{line.get("line_id")}: chars={count}, max_chars={maximum}, '
                    f'允许上限={maximum*1.1:.1f}；允许slot_ids={allowed}')
        covered.update(i for i in line.get('facts', []) if type(i) is int)
    missing = sorted(set(range(len(value.get('facts', []))))-covered)
    if missing:
        errors.append(f'facts总数={len(value["facts"])}，未覆盖索引={missing}')
    for scene in lock['scenes']:
        if not scene['reference_lines']:
            rows = [s for s in value.get('visual_scenes', []) if s.get('scene_id') == scene['scene_id']]
            spans = rows[0].get('ranges', []) if len(rows) == 1 else []
            allowed = scene['allowed_ranges']
            valid = bool(spans) and all(isinstance(r, list) and len(r) == 2 and
                all(type(t) in (int, float) and math.isfinite(t) for t in r) and
                any(a['source_start_s'] <= r[0] < r[1] <= a['source_end_s'] for a in allowed) for r in spans)
            if not valid:
                errors.append(f'{scene["scene_id"]}: 无对白场景须有allowed_ranges内的画面')
            else:
                target = min(scene['end_s']-scene['start_s'], sum(a['source_end_s']-a['source_start_s'] for a in allowed))
                if abs(sum(b-a for a, b in spans)-target) > 1/FPS:
                    errors.append(f'{scene["scene_id"]}: 无对白场景画面总长须为{target:.6f}秒')
                ordered = sorted(spans)
                if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
                    errors.append(f'{scene["scene_id"]}: 无对白动作画面不得重复')
            continue
        sid = scene['scene_id']
        roles = value.get('reference_roles', {}).get(sid, [])
        if len(roles) != len(scene['reference_lines']) or any(r not in scene['roles'] for r in roles):
            errors.append(f'{sid}: reference_roles须逐条标全参考说话人，不能跳过未知角色')
            continue
        owners = {}
        for raw, role in zip(scene['reference_lines'], roles):
            known = reference_line_role(raw, scene['roles'])
            if known and known != role:
                errors.append(f'{sid}/{raw["line_id"]}: 参考说话人须为{known}')
            owners[raw['line_id']] = known or role
        lines = [line for line in value.get('lines', []) if line.get('scene_id') == sid]
        speaking = {p['role'] for (scene_id, _), p in slots.items() if scene_id == sid}
        missing_roles = set(owners.values()) - speaking
        if missing_roles:
            errors.append(f'{sid}: 缺少角色说话素材{sorted(missing_roles)}，须重新选窗，不能删句或转交台词')
        covered_refs = set()
        for line in lines:
            refs = line.get('reference_line_ids', [])
            if not refs or any(owners.get(i) != line.get('role') for i in refs):
                errors.append(f'{line.get("line_id")}: reference_line_ids须来自同场景同角色，不能转交台词')
            covered_refs.update(refs)
        if set(owners)-covered_refs:
            errors.append(f'{sid}: 遗漏参考行{sorted(set(owners)-covered_refs)}，须恢复原角色的内容')
        if scene.get('template'):
            order = []
            for line in lines:
                for ident in line.get('reference_line_ids', []):
                    if not order or order[-1] != ident:
                        order.append(ident)
            if order != list(owners):
                errors.append(f'{sid}: 参考轮次须保持{list(owners)}，当前{order}；只允许同角色连续原句合并')
        reference_counts = Counter(role for role in owners.values() if role in speaking)
        counts = Counter(line['role'] for line in lines)
        largest = max(reference_counts.values(), default=0)
        leaders = [role for role, count in reference_counts.items() if count == largest]
        if len(leaders) == 1 and counts[leaders[0]] < max(counts.values(), default=0):
            errors.append(f'{sid}: 参考主讲人{leaders[0]}仍须台词最多，当前{dict(counts)}')
    if errors:
        raise ValueError('dialogue_script: ' + '\n'.join(errors))
    return value


def _speech(line, voice, state, output):
    from .tts_retry import task_output_for
    key = 'utt_' + json_sha(dict(provider=tts.ENDPOINT, model=tts.MODEL, language='Chinese',
                                text=line['text'], voice=voice))[:20]
    ledgers = state.input_lock['configuration'].get('parent_baseline', {}).get('linked_ledgers', ())
    result = tts.synthesize_line(line['text'], voice, output/'dialogue_tts', line_id=key,
        task_output=task_output_for(output) or output, **(dict(linked_ledgers=ledgers) if ledgers else {}))
    samples = audio_render._samples(result['audio_path'])
    active = np.flatnonzero(np.max(np.abs(samples), axis=1) >= .01)
    lead, stop = (int(active[0]), int(active[-1])+1) if len(active) else (0, 0)
    line.update(voice=voice, tts_audio_path=result['audio_path'], voiced_s=(stop-lead)/SR, lead_s=lead/SR)
    return samples[lead:stop]


# 不烧心 g2: 1–6 frame slivers flashed on screen and broke the render's duration check.
MIN_FRAMES = 12
LOOP_MIN_FRAMES = 1  # Output frame granularity, not a minimum loop duration.


class _Timeline:
    def __init__(self, windows):
        self.windows = {w['window_id']: w for w in windows}
        self.rows, self.cursors = [], {}
        self.frames = 0

    def can_loop(self, slot):
        return bool(slot.get('loopable') and slot.get('loop_range'))

    def unused(self, slot, start, end):
        ranges = [[start, end]]
        source = self.windows[slot['window_id']]['source_id']
        for row in self.rows:
            if row['source_id'] == source:
                x, y = row['source_in_s'], row['source_out_s']
                ranges = [(a, b) for low, high in ranges for a, b in
                          ((low, min(high, x)), (max(low, y), high)) if a < b]
        return next(((a, b) for a, b in ranges if (b-a)*FPS >= MIN_FRAMES-1e-6), (end, end))

    def append(self, scene, slot, start, frames, kind, line_id=None):
        window = self.windows[slot['window_id']]
        row = dict(segment_id=f'seg_{len(self.rows)+1}', scene_id=scene['scene_id'], slot_id=slot.get('slot_id'),
            window_id=slot['window_id'], source_id=window['source_id'], source_in_s=start,
            source_out_s=start+frames/FPS, speed=1, look='none', framing='fit',
            role_ids=[scene['role_bindings'][slot['role']]['role_id']], role=slot['role'], kind=kind,
            line_id=line_id, start_s=self.frames/FPS, end_s=(self.frames+frames)/FPS,
            source_subtitle_masks=deepcopy(window.get('source_subtitle_masks', [])),
            source_subtitle_treatments=deepcopy(window.get('source_subtitle_treatments', [])),
            static_range=slot.get('static_range'), loop_range=slot.get('loop_range'))
        self.rows.append(row)
        self.frames += frames
        return row

    def take(self, scene, line, seconds):
        first = next(p for p in scene['speech_slots'] if p['slot_id'] == line['slot_id'])
        slots = [first]
        # User rule: a static one-view loop may be any length; only one-shot footage keeps a minimum.
        slots = [p for p in slots if self.can_loop(p) or
                 math.floor((p['source_end_s']-p['source_start_s'])*FPS+1e-6) >= FPS/2]
        saved = (len(self.rows), self.frames, self.cursors.copy())
        remaining = round(seconds*FPS)
        while remaining and slots:
            choices = []
            for slot in slots:
                loopable = self.can_loop(slot)
                key = (slot['source_sha256'], slot['source_start_s'], slot['source_end_s'])
                low, end = _picture_range(slot)
                start = max(low, self.cursors.get(key, low))
                frames = math.floor((end-start)*FPS+1e-6)
                wrapped = frames < (LOOP_MIN_FRAMES if loopable else MIN_FRAMES)
                if wrapped:
                    if not loopable:
                        continue
                    start = low
                if not loopable:
                    start, end = self.unused(slot, start, end)
                frames = math.floor((end-start)*FPS+1e-6)
                if frames < (LOOP_MIN_FRAMES if loopable else MIN_FRAMES):
                    continue
                choices.append((int(wrapped), slot, key, start, frames))
            if not choices:
                break
            _, slot, key, start, frames = min(choices, key=lambda choice: choice[0])
            frames = min(frames, remaining)
            leftover = remaining - frames
            if not self.can_loop(slot) and 0 < leftover < MIN_FRAMES and frames - (MIN_FRAMES - leftover) >= MIN_FRAMES:
                frames -= MIN_FRAMES - leftover
            elif not self.can_loop(slot) and frames < MIN_FRAMES:
                # A line's very last frames: overrun the voice a little rather than flash a sliver.
                frames = MIN_FRAMES
            self.append(scene, slot, start, frames, 'speech', line['line_id'])
            self.cursors[key] = start+frames/FPS
            remaining = max(0, remaining-frames)
        if remaining:
            count, self.frames, self.cursors = saved
            del self.rows[count:]
            return [], remaining/FPS
        return self.rows[saved[0]:], 0


def _visual_soundtrack(reference_source, scenes, timeline, cache):
    """Keep the reference soundtrack only under its explicitly non-dialogue scenes."""
    from .media import _run, verify_source
    if reference_source is None or reference_source.get('audio_stream_index') is None:
        return None
    visual = [s for s in scenes if not s['reference_lines']]
    if not visual:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    samples = np.zeros((round(timeline[-1]['end_s']*SR), 2), dtype=np.float32)
    for scene in visual:
        segments = [r for r in timeline if r['scene_id'] == scene['scene_id']]
        start, end = segments[0]['start_s'], segments[-1]['end_s']
        audio = cache / (scene['scene_id']+'_reference.wav')
        _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(scene['start_s']),
            '-i', str(verify_source(reference_source)), '-t', str(end-start),
            '-map', f"0:{reference_source['audio_stream_index']}", '-vn', '-ac', '2', '-ar', str(SR), str(audio)])
        part = audio_render._samples(audio)[:round((end-start)*SR)]
        offset = round(start*SR)
        samples[offset:offset+len(part)] = part
    path = cache/'visual_soundtrack.wav'
    audio_render._wav(path, samples)
    return path


def run(lock, windows, reference, strategy, sources, state, glm, cache, output, *, render_fn=None, reference_source=None, material=None):
    from .pipeline import _material_call
    output, cache = Path(output), Path(cache)/'dialogue_cut'
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    if lock.get('editing_policy') in {'self_directed_material_v1', 'continuous_dialogue_v1', 'action_dialogue_v1', 'reference_story_v1'}:
        if material is None or getattr(material, 'discovery', None) is None:
            raise LibraryStopped('autonomous_material_controller_missing')
        lock, windows, voices, script, timeline, lines, audio_lines, captions = material.discovery.prepare(cache, output)
        from .subtitle_library import refine_selected_subtitles
        if lock['editing_policy'] == 'self_directed_material_v1':
            refine_selected_subtitles(timeline.rows, windows, sources, glm, cache)
        return deliver(lock, reference, strategy, sources, state, cache, output, voices, script,
                       timeline.rows, lines, audio_lines, captions, [],
                       render_fn=render_fn, reference_source=reference_source)
    notes = deepcopy(lock.get('mismatches', []))
    lock = deepcopy(lock)
    by_window = {w['window_id']: w for w in windows}
    for scene in lock['scenes']:
        scene['visual_events'] = [dict(source_start_s=w['source_start_s']+e['local_start_s'],
            source_end_s=w['source_start_s']+e['local_end_s'], observed_fact=e['observed_fact'])
            for wid in scene['window_ids'] for w in [by_window[wid]] for e in w['observation']['events']]
    if lock.get('editing_policy') == 'joint_av_v1':
        from .joint_dialogue import prepare
        voices, script, timeline, lines, audio_lines, captions = prepare(
            lock, windows, reference, state, glm, cache, output)
    else:
        voices = voices_for(lock)
        for scene in lock['scenes']:
            if not scene['reference_lines']:
                continue
            if scene.get('picture', {}).get('single_location') is False:
                raise LibraryStopped(f'scene_route:multiple_locations_in_dialogue:{scene["scene_id"]}')
            required = {reference_line_role(line, scene['roles']) for line in scene['reference_lines']} - {None}
            available = {p.get('role') for p in _script_slots(scene, voices)}
            if required - available:
                raise LibraryStopped(f'scene_route:missing_speaking_roles:{scene["scene_id"]}:{sorted(required-available)}')
        def validate(value):
            return validate_script(value, lock, voices)
        validate.repair_limit = 1
        script = _material_call(state, glm, 'dialogue_script', script_prompt(lock, reference, strategy, voices), validate)
        write_json(output/'dialogue_script.json', script)
        timeline, lines = _Timeline(windows), []
        audio_lines, captions = [], []
        for scene in lock['scenes']:
            if not scene['reference_lines']:
                visual = next(s for s in script['visual_scenes'] if s['scene_id'] == scene['scene_id'])
                for start, end in visual['ranges']:
                    allowed = next(a for a in scene['allowed_ranges'] if a['source_start_s'] <= start < end <= a['source_end_s'])
                    role = next(iter(scene['role_bindings']))
                    slot = dict(allowed, slot_id='visual_'+scene['scene_id'], role=role)
                    timeline.append(scene, slot, start, math.floor((end-start)*FPS+1e-6), 'visual')
                continue
            for raw in script['lines']:
                if raw['scene_id'] != scene['scene_id']:
                    continue
                line = dict(raw, id=f'line_{len(lines)+1}')
                lines.append(line)
                slot = next(p for p in scene['speech_slots'] if p['slot_id'] == line['slot_id'])
                line.update(mode='dialogue', picture_relation='sync_speech', speaker=dict(window_id=slot['window_id'],
                            role_id=scene['role_bindings'][line['role']]['role_id']))
                samples = _speech(line, voices[line['role']], state, output)
                if not len(samples):
                    raise LibraryStopped(f'dialogue_silent_line:{line["line_id"]}')
                action = 'continuous'
                pieces, remaining = timeline.take(scene, line, line['voiced_s']+.12)
                if remaining:
                    available = line['voiced_s']+.12-remaining
                    target = max(1, math.floor(max(0, available-.12)*RATES[line['voice']]*.85))
                    def rewrite(value):
                        if not isinstance(value, dict) or not isinstance(value.get('text'), str) or not value['text'].strip():
                            raise ValueError('dialogue_rewrite: text须为非空字符串')
                        count = len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', value['text']))
                        if count < 1:
                            raise ValueError('dialogue_rewrite: 台词须有实际发声内容')
                        return value
                    rewrite.repair_limit = 1
                    revised = _material_call(state, glm, 'dialogue_rewrite',
                        '缩短这一句，保留具体事实、说话人及提问/回答/纠正等作用，不得改成复述；短句不凑字数，只输出JSON {"text":"台词"}。\n' + json.dumps(
                            dict(line=line, max_chars=target), ensure_ascii=False), rewrite)
                    line.update(original_text=line['text'], text=revised['text'])
                    samples = _speech(line, line['voice'], state, output)
                    pieces, remaining = timeline.take(scene, line, line['voiced_s']+.12) if len(samples) else ([], 0)
                    action = 'rewrite'
                    if remaining or not len(samples):
                        raise LibraryStopped(f'dialogue_unfitted_line:{line["line_id"]}:remaining_s={remaining}')
                line.update(fit_action=action, segment_ids=[p['segment_id'] for p in pieces],
                            start_s=pieces[0]['start_s'], end_s=pieces[-1]['end_s'])
                path = cache/(line['id']+'.wav')
                audio_render._wav(path, samples)
                end = line['start_s']+len(samples)/SR
                audio_lines.append(dict(id=line['id'], audio_path=str(path), start_s=line['start_s'], end_s=end))
                captions.append(dict(text=line['text'], start_s=line['start_s'], end_s=end))
    from .subtitle_library import refine_selected_subtitles
    refine_selected_subtitles(timeline.rows, windows, sources, glm, cache)
    return deliver(lock, reference, strategy, sources, state, cache, output, voices, script,
                   timeline.rows, lines, audio_lines, captions, notes,
                   render_fn=render_fn, reference_source=reference_source)


def deliver(lock, reference, strategy, sources, state, cache, output, voices, script, rows, lines,
            audio_lines, captions, notes, *, render_fn=None, reference_source=None, degradation=None,
            auto_review=True):
    """Render and mix one technically checked video, register it, then add review facts.

    The internal review only adds quality information: a review failure is
    recorded and never removes an already registered video. A degraded plan
    (degradation is not None) is never marked content_delivery_ready.
    """
    from .run_outcome import write_run_outcome
    schedule_verification=None
    if lock.get('editing_policy')=='reference_story_v1' and all('shot_options' in r for r in lines):
        from .schedule_audit import audit_schedule
        source_shots={s['slot_id']:s for scene in lock['scenes'] for s in scene.get('coverage_shots',[])}
        if all(r.get('slot_id') in source_shots for r in rows):
            actual=[dict(slot_id=r['slot_id'],line_id=r.get('line_id'),
                source_sha256=source_shots[r['slot_id']]['source_sha256'],
                first=round(r['source_in_s']*FPS),last=round(r['source_out_s']*FPS)) for r in rows]
            schedule_verification=audit_schedule(script,lines,actual,source_shots)
            write_json(output/'schedule_audit.json',schedule_verification)
    level = degradation['fallback_level'] if degradation else 0
    if not audio_lines and level < 3 and any(s.get('reference_lines') for s in lock['scenes']):
        # A reference with dialogue needs a new voice (only L3 may omit it). A reference
        # with no speech at all (31: its only ASR line was a credit) is a pure visual
        # piece: the joint script is legitimately empty and the reference soundtrack plays.
        raise LibraryStopped('scene_route:no_voiced_lines')
    plan = dict(reference_sha256=reference.get('reference_sha256'), audio_mode='silent', reference_audio=None,
        width=1280, height=720, fps=FPS, limitations=list(degradation['limitations']) if degradation else [],
        segments=rows,
        cast=[dict(cast_id=f'cast_{i}_{j}', story_role=role, entity_id=entity, voice=voices[role], bindings=bindings)
              for i, (role, bound) in enumerate(lock['cast_bindings'].items())
              for j, (entity, bindings) in enumerate(bound.get('entity_bindings', {}).items())],
        slots=[dict(slot_id=s['scene_id'], segment_ids=[p['segment_id'] for p in rows
                   if p['scene_id'] == s['scene_id']]) for s in lock['scenes']], audio_draft=lines)
    # Each level writes its own artifacts, so a later level never collides with
    # files an earlier, failed level left in the same task directory.
    suffix = f'_l{level}' if level else ''
    plan_name, render_dir = (('dialogue_plan.json', 'render_dialogue') if not level else
                             (f'degraded_plan{suffix}.json', f'render_degraded{suffix}'))
    write_json(output/plan_name, plan)
    rendered = (render_fn or render.render_library_video)(sources, plan, output/render_dir,
        fps=FPS, width=1280, height=720, max_duration_s=None, burn_captions=False)
    audio_lines.sort(key=lambda row: row['start_s'])
    captions.sort(key=lambda row: row['start_s'])
    soundtrack = _visual_soundtrack(reference_source, lock['scenes'], rows, cache)
    mixed = audio_render.mix_speech(rendered['rendered_path'], audio_lines, output/f'final_mixed{suffix}.mp4',
        **(dict(music_path=soundtrack) if soundtrack else {}))
    subtitles = dict(subtitle_mode='burned')
    final = None
    if captions:
        target = output/f'final_subtitled{suffix}.mp4'
        try:
            final = audio_render.subtitle_final_video(mixed['path'], captions, target)
        except Exception as exc:  # noqa: BLE001 - captions are an optional layer over a finished mix
            srt = target.with_suffix('.srt')
            subtitles = dict(subtitle_mode='srt_only', subtitle_error=f'{type(exc).__name__}:{str(exc)[:1000]}',
                             subtitle_srt_path=str(srt) if srt.is_file() else None)
    else:
        subtitles = dict(subtitle_mode='none')
    if final is None:
        final = mixed  # the checked mix is the delivery; no burned captions
    result = dict(status='delivered_with_review_notes', material_flow='scene_v1',
        final_video=final['path'], final_sha256=final['sha256'], voices=voices, facts=script['facts'], lines=lines,
        timeline=rows, audio_lines=audio_lines, review=None, notes=notes, artifact_ready=True,
        delivery_mode=degradation['delivery_mode'] if degradation else 'normal', fallback_level=level,
        degradation_reasons=list(degradation['reasons']) if degradation else [],
        degradation_path=degradation.get('path') if degradation else None,
        quality_status='review_pending', content_delivery_ready=False, joint_quality_gate=False,
        audio_measurement=mixed.get('audio_measurement'), usage=state.usage(),
        dialogue_result_path=str(output/'dialogue_result.json'), **subtitles)

    def register():
        write_json(output/'dialogue_result.json', result)
        write_json(output/'result.json', result)
        write_run_outcome(output, result)
    # The checked video is a delivery before any quality judgement runs.
    register()
    if not auto_review:
        # User policy 2026-11-15: no automatic final review for this lane. The
        # deterministic schedule audit still guards against a collapsed plan.
        ready = degradation is None
        result.update(status='delivered' if ready else 'delivered_with_review_notes', review=None,
            quality_status='auto_review_off', review_policy='user_waived_automatic_final_review_20261115',
            content_delivery_ready=ready, joint_quality_gate=False, usage=state.usage())
        if schedule_verification is not None:
            from .schedule_audit import apply_schedule_quality
            result['schedule_verification']=schedule_verification
            apply_schedule_quality(result,schedule_verification)
        register()
        return result
    context = dict(editing_policy=lock.get('editing_policy'), reference={**reference, 'strategy': strategy}, segments=rows, audio_draft=lines,
        scene_cast=[dict(scene_id=s['scene_id'], role_bindings=s['role_bindings'],
            scene=s['picture']['scene']) for s in lock['scenes']],
        reference_dialogue=[dict(scene_id=s['scene_id'], template=s.get('template'),
            turns=[{k: r.get(k) for k in ('line_id', 'role', 'text', 'function', 'must_preserve', 'verbatim_terms', 'visual_anchor')} for r in s['reference_lines']])
            for s in lock['scenes']])
    if lock.get('visible_actor_catalog'):
        context['visible_actor_catalog']=lock['visible_actor_catalog']
    if lock.get('reference_contract'):
        from .reference_story import required_scenes
        context['reference_contract'] = lock['reference_contract']
        context['reference_dialogue'] = [dict(scene_id=s['scene_id'],template=s['template'],
            turns=[r for r in lock['reference_contract']['lines'] if r['line_id'] in s['line_ids']])
            for s in required_scenes(lock['reference_contract'])]
    if lock.get('editing_policy') in {'action_dialogue_v1', 'reference_story_v1'}:
        if lock.get('visual_intent'):
            context['visual_intent'] = lock['visual_intent']
        sound_ends = {r['id']:r['end_s'] for r in audio_lines}
        context['audio_draft'] = [dict(r,end_s=sound_ends[r['id']]) for r in lines]
    if lock.get('editing_policy')=='reference_story_v1':
        # Actual synthesis IDs define only neutral reference samples. Story-role
        # assignments and expected target labels never reach the audio observer.
        by_voice={}
        for line in lines:
            key=line.get('voice',line['role'])
            if key not in by_voice or line.get('voiced_s',0)>by_voice[key].get('voiced_s',0):by_voice[key]=line
        context['voice_reference_line_ids']=[line['line_id'] for line in by_voice.values()]
    write_json(output/'dialogue_review_context.json',context)
    try:
        review = visual_audio.review_final_video(final['path'], output/'dialogue_review', context,
            **(dict(picture_source=rendered['rendered_path']) if (lock.get('editing_policy')=='reference_story_v1' or getattr(state,'input_lock',{}).get('configuration',{}).get('independent_final_visual_v1')) else {}))
        passed = visual_audio.content_ready(review, context)
        result.update(review=review, quality_status='passed' if passed else 'needs_improvement')
    except Exception as exc:  # noqa: BLE001 - review is advisory after registration
        passed = False
        result.update(quality_status='review_failed', review_error=f'{type(exc).__name__}:{str(exc)[:2000]}')
    if lock.get('editing_policy') == 'action_dialogue_v1':
        from .action_dialogue import review_final_actions
        try:
            action_check = review_final_actions(final['path'],output/'dialogue_review',lock['visual_intent'],rows)
        except Exception as exc:  # noqa: BLE001 - never mark an unverified render ready
            action_check = dict(passed=False,error=f'{type(exc).__name__}:{str(exc)[:1000]}')
        result['action_verification'] = action_check
        if not action_check['passed']:
            passed = False
            result['quality_status'] = 'needs_improvement'
    ready = passed and degradation is None
    result.update(status='delivered' if ready else 'delivered_with_review_notes', content_delivery_ready=ready,
                  joint_quality_gate=ready, usage=state.usage())
    if schedule_verification is not None:
        from .schedule_audit import apply_schedule_quality
        result['schedule_verification']=schedule_verification
        apply_schedule_quality(result,schedule_verification)
    register()
    return result
