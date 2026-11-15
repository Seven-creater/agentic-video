"""Reference-scene material route. No beat planner, comparison or research rounds.

The index and received observations remain read-only. Task-local windows carry
the confirmation evidence and expose the complete indexed scene to the existing
plan/render/audio contracts.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from pathlib import Path

from . import contracts, material_confirm, material_index, speech_slots
from .content_ranges import content_range_for
from .film_cast import canonical_name, load_film_cast, normalise, overlaps_stylized
from .state import LibraryStopped, json_sha, write_json

# Location first (user 11-10, restoring v36): 不烧心 v36 picked Mr Ping's noodle shop; after the
# 11-08 functional-venue rule and the 11-09 substitutes wording, a rope bridge was chosen over the
# top-ranked noodle shop ("与店铺吧台无关但可承载同叙事功能"). A same-kind place always comes first.
# Dialogue first, then place (user 11-10): 03 at 04040f0d picked a same-kind street whose footage
# was a dragon-dance chase and kung-fu explosions; 不烧心's rope bridge had dialogue but the noodle
# shop had both. A real conversation is the gate; among conversations a same-kind place comes first.
FUNCTIONAL_SETTING_GUIDE = (
    'setting_match同时看互动与地点：参考是对话场景时，窗口必须是两人以上在同一连续地点面对面讲话，'
    '以打斗、追逐、潜行、表演为主或多个地点交叉剪辑的判false，地点再像也不算。'
    '是真正对话时再看地点类型（室内/室外、店铺/住宅/野外等）：同类场所判true，装潢、建筑风格或行业名称不同不影响；'
    '不同类型的场所也可判true，但setting_reason须写明是不同类型场所的改编。'
    '具体制作过程、物件使用或空间关系若决定因果，必须有实际画面支持，不能用普通聊天替代。'
    'setting_reason写明实际互动、空间类型与参考的对应或差异。'
)

# Q1: what the audience must SEE (voice cannot fake it), written as an abstract,
# cross-film event so the library can be searched for an equivalent picture.
MUST_SEE_GUIDE = (
    'must_see列出该场景观众必须亲眼看到、配音无法替代的关键可见事件（如物证被发现、破坏动作、胜负结果、身体冲突），'
    '最多2条；靠台词就能讲清的内容（反驳、类比、解释、抱怨、自嘲、下指令、讨价还价等）一律不算，对话为主的场景通常为0至1条；每条event写成跨作品通用的抽象事件（谁对谁做了什么、出现了什么结果），不写参考专有物件名；'
    'replaceable说明该物件或动作可换成哪些常见替代；纯对话、没有此类事件时must_see为[]。'
    'reference_objects列出参考画面里与剧情相关的具体物件名词（每个2–6字，如"现金"而非"钱"），供后续按素材改编替换。'
)
MUST_SEE_LIMIT = 2
# Same order as the role/dialogue bonuses (.6): an event window can enter the
# shortlist, but a dialogue scene still needs speakers to be usable.


def _strip_terms(text, terms):
    """Remove reference-specific object words from a retrieval query."""
    if not terms:
        return text  # baseline query text, byte for byte
    for term in sorted(terms, key=len, reverse=True):
        text = text.replace(term, ' ')
    return re.sub(r'\s+', ' ', text).strip()


def normalise_must_see(scene):
    """Q1 fields are optional evidence: malformed items are dropped and recorded, never repaired.

    A bad answer here must not stop or slow the route; without them the scene
    behaves exactly as before (setting + interaction retrieval)."""
    notes = []
    raw_objects = scene.get('reference_objects')
    objects = []
    for item in raw_objects if isinstance(raw_objects, list) else []:
        if isinstance(item, str) and 1 <= len(item.strip()) <= 12 and item.strip() not in objects:
            objects.append(item.strip())
    if raw_objects is not None and not isinstance(raw_objects, list):
        notes.append('reference_objects不是数组，已忽略')
    raw = scene.get('must_see')
    items = []
    for item in raw if isinstance(raw, list) else []:
        event = item.get('event') if isinstance(item, dict) else None
        if not isinstance(event, str) or not event.strip():
            notes.append('丢弃缺少event的must_see条目')
            continue
        roles = item.get('roles') if isinstance(item.get('roles'), list) else []
        replaceable = item.get('replaceable')
        items.append(dict(id=f'{scene["scene_id"]}_see_{len(items)+1}', event=event.strip()[:120],
            roles=[r for r in roles if r in scene.get('roles', [])],
            replaceable=replaceable.strip()[:160] if isinstance(replaceable, str) else ''))
    if raw is not None and not isinstance(raw, list):
        notes.append('must_see不是数组，已忽略')
    if len(items) > MUST_SEE_LIMIT:
        notes.append(f'must_see超过{MUST_SEE_LIMIT}条，保留前{MUST_SEE_LIMIT}条')
    scene['must_see'], scene['reference_objects'] = items[:MUST_SEE_LIMIT], objects[:12]
    if notes:
        scene['must_see_notes'] = notes
    return scene


TAIL_FRACTION, TAIL_MAX_S = .2, 30.0


def fold_same_location(scenes):
    """Consecutive speaking scenes at the same physical place become one scene.

    03 (l0 batch): the reading said the whole reference is one melon stall and the
    split labelled every part '同一路边瓜摊', yet gave four dialogue_ids; each part
    then needed its own window and a full cast, and two lacked visible speakers.
    The scene count follows the real locations (location_id), not dialogue phases.
    Line-free visual segments are never folded."""
    notes = []
    for previous, scene in zip(scenes, scenes[1:]):
        place = scene.get('location_id')
        if (scene.get('line_ids') and previous.get('line_ids') and isinstance(place, str) and place.strip()
                and place == previous.get('location_id') and scene.get('dialogue_id') != previous.get('dialogue_id')):
            notes.append(dict(scene_id=scene['scene_id'], joined_dialogue=previous.get('dialogue_id'), location_id=place))
            scene['dialogue_id'] = previous.get('dialogue_id')
    return notes


def fold_short_tails(scenes, duration):
    """A short speaking tail right after a speaking scene joins that scene's dialogue.

    Viewers read dialogue meaning into faces when space and gaze stay continuous
    (Kuleshov studies, e.g. Szaszkó & Loebus 2026, PMC12972553, reviewing Barratt
    2016 / Calbi 2017); scattered windows lose that. 27: a 13 s exit and a 21 s
    clean-up became their own scenes, each needing a window and the same cast, and
    the tail's single speaker decided the lead role's actor. Line-free visual
    segments (montage, entrances) are never folded. The existing dialogue_id merge
    then keeps one stable scene."""
    limit = min(TAIL_MAX_S, TAIL_FRACTION * duration)
    notes = []
    for previous, scene in zip(scenes, scenes[1:]):
        if (scene.get('line_ids') and previous.get('line_ids') and scene['end_s'] - scene['start_s'] < limit
                and scene.get('dialogue_id') != previous.get('dialogue_id')):
            notes.append(dict(scene_id=scene['scene_id'], joined_dialogue=previous.get('dialogue_id'),
                              duration_s=round(scene['end_s'] - scene['start_s'], 2), limit_s=round(limit, 2)))
            scene['dialogue_id'] = previous.get('dialogue_id')
    return notes


def must_see_query(scene):
    """Abstract visible events for retrieval; reference-only object names are removed."""
    events = '；'.join(item['event'] for item in scene.get('must_see', []))
    return _strip_terms(events, scene.get('reference_objects', []))


# Substitute pictures (literature: ShotFinder 2601.23232 imagines the context a shot
# lives in before searching; CSQE 2402.18031 shows expansions must be grounded in the
# retrieved corpus — free imagination fell below no expansion on unseen corpora;
# I Spy a Metaphor 2305.14724 goes meaning -> objects -> concrete picture). The library
# rarely holds the reference's literal scene, so each must-see event is restated as its
# narrative function and as pictures that the retrieved library text could show.
SUBSTITUTE_LIMIT = 3
SUBSTITUTE_EXTRA = 3  # windows reserved for the substitute search beyond the base shortlist
SUBSTITUTE_SAMPLE = 16  # retrieved windows shown to the model as corpus evidence


def substitute_query(scene):
    """Function and substitute pictures of all must-see events, reference objects removed."""
    parts = [text for item in scene.get('must_see', [])
             for text in [item.get('function', ''), *item.get('substitutes', [])] if text]
    return _strip_terms('；'.join(parts), scene.get('reference_objects', []))


def substitute_prompt(scenes, samples):
    return ('素材库只有给定的几部电影，参考里的具体场景和物件多半找不到，需要找能承担同样作用的替代画面。'
        '对scenes中每条must_see分两步：先写function，说明这件事在剧情里起什么作用'
        '（谁对谁占上风、谁受挫或尴尬、结果是什么），2至40字，不写参考专有的地点和物件；'
        '再写1至3条substitutes，每条是素材里可能存在、能承担同一作用的替代画面，写成可检索的画面描述'
        '（人物关系+具体动作+表情或结果，8至60字），如“一人举起手里的东西质问，另一人低头不语”。'
        'library_samples是从素材库实际检索到的窗口记录：substitutes的用词尽量取自其中who/place/action的原文，'
        '能对应到某条记录时在record_ids填该record_id；仅凭推测、样本里没有对应时record_ids填[]。'
        '不能照抄参考的地点或物件，不能写素材里不可能出现的现代场景。'
        '只返回JSON {"scenes":[{"scene_id":"scene_1","must_see":[{"id":"scene_1_see_1","function":"作用",'
        '"substitutes":[{"text":"替代画面","record_ids":["记录ID"]}]}]}]}。\n' +
        _json(dict(scenes=[dict(scene_id=s['scene_id'], setting=s.get('setting', ''), interaction=s.get('interaction', ''),
                                must_see=[{k: item[k] for k in ('id', 'event', 'replaceable')} for item in s['must_see']])
                           for s in scenes if s.get('must_see')],
                   library_samples=samples)))


def check_reference(scene):
    """What the footage check compares against.

    27 r3: with the literal shop setting and event listed first, the checker judged
    'no customer shows a defective item' and setting_match=false on every window,
    even where the substitute guide applied. With substitutes, the check sees the
    story functions first; the reference look stays only as background."""
    keys = ('scene_id', 'setting', 'interaction', 'template', 'visual_function',
            *(('must_see',) if scene.get('must_see') else ()))
    if not substitute_query(scene):
        return {k: scene.get(k) for k in keys}  # baseline, byte for byte
    return dict(scene_id=scene['scene_id'], interaction=scene.get('interaction'),
        visual_function=scene.get('visual_function'),
        must_see=[dict(id=item['id'], function=item.get('function', ''), substitutes=item.get('substitutes', []),
                       reference_event_background=item['event']) for item in scene['must_see']],
        reference_look_background=dict(setting=scene.get('setting'), template=scene.get('template'),
            note='仅为背景：参考原地点与原事件素材里基本没有，不作为判断标准'))


def validate_substitutes(value, scenes, record_ids):
    """Every problem is reported at once (27 r1 lesson); accepted shape is strict."""
    rows = {row.get('scene_id'): row for row in value.get('scenes', [])
            if isinstance(row, dict)} if isinstance(value, dict) and isinstance(value.get('scenes'), list) else {}
    problems = []
    for scene in scenes:
        if not scene.get('must_see'):
            continue
        answers = {a.get('id'): a for a in (rows.get(scene['scene_id']) or {}).get('must_see', []) if isinstance(a, dict)}
        for item in scene['must_see']:
            answer = answers.get(item['id'])
            if answer is None:
                problems.append(f'缺少{scene["scene_id"]}的{item["id"]}')
                continue
            function = answer.get('function')
            if not isinstance(function, str) or not 2 <= len(function.strip()) <= 80:
                problems.append(f'{item["id"]}.function须为2至80字的作用说明')
            subs = answer.get('substitutes')
            if not isinstance(subs, list) or not 1 <= len(subs) <= SUBSTITUTE_LIMIT:
                problems.append(f'{item["id"]}.substitutes须为1至{SUBSTITUTE_LIMIT}条')
                continue
            for index, sub in enumerate(subs, 1):
                text = sub.get('text') if isinstance(sub, dict) else None
                if not isinstance(text, str) or not 4 <= len(text.strip()) <= 80:
                    problems.append(f'{item["id"]}.substitutes[{index}].text须为4至80字的画面描述')
                ids = sub.get('record_ids', []) if isinstance(sub, dict) else []
                unknown = [r for r in ids if r not in record_ids] if isinstance(ids, list) else ['非数组']
                if unknown:
                    problems.append(f'{item["id"]}.substitutes[{index}].record_ids含library_samples以外的{unknown[:3]}；没有对应填[]')
    if problems:
        raise ValueError('scene_substitutes: 须同时修复以下各项：\n' + '\n'.join(problems))
    return value


def apply_substitutes(scenes, value):
    """Attach function/substitutes to each must-see item (input already validated)."""
    # The validator tolerates extra malformed rows; skip them here instead of failing.
    rows = {row.get('scene_id'): row for row in value.get('scenes', []) if isinstance(row, dict)}
    for scene in scenes:
        listed = (rows.get(scene['scene_id']) or {}).get('must_see', [])
        answers = {a.get('id'): a for a in listed if isinstance(a, dict)} if isinstance(listed, list) else {}
        for item in scene.get('must_see', []):
            answer = answers.get(item['id'])
            if answer is None:
                continue
            item['function'] = answer['function'].strip()
            item['substitutes'] = [s['text'].strip() for s in answer['substitutes']]
            item['substitute_record_ids'] = sorted({r for s in answer['substitutes'] for r in s.get('record_ids', [])})
    return scenes


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _fail(stage, message, allowed):
    error = ValueError(f'{stage}: {message}；允许值={_json(allowed)}')
    error.diagnostics = dict(stage=stage, allowed=allowed, repair_hint=message)
    raise error


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def transcript_lines(asr):
    """Keep all timed ASR lines, including the recorded source/local time dialect."""
    if str(asr.get('status', '')).startswith(('rejected_', 'failed')):
        raise LibraryStopped('reference_asr_unavailable:' + str(asr.get('error', asr['status'])))
    result = []
    for index, row in enumerate(asr.get('segments', []), 1):
        start = row.get('local_start_s', row.get('start_s', row.get('start', row.get('source_start_s'))))
        end = row.get('local_end_s', row.get('end_s', row.get('end', row.get('source_end_s'))))
        line = dict(line_id=index, start_s=start, end_s=end, text=row.get('text', ''))
        if row.get('words'):
            line['source_words'] = deepcopy(row['words'])
        if 'no_speech_prob' in row:
            words = row.get('words', [])
            line['asr_evidence'] = dict(no_speech_prob=row['no_speech_prob'], avg_logprob=row.get('avg_logprob'),
                word_count=len(words), timed_word_count=sum(w.get('timing_usable') is True for w in words))
        result.append(line)
    return result


def reference_line_role(line, roles):
    text = line.get('text', '')
    prefix = re.split(r'[:：]', text, maxsplit=1)[0] if re.search(r'[:：]', text) else None
    for label in (line.get('role'), line.get('speaker'), prefix):
        if isinstance(label, str) and label.strip() in roles:
            return label.strip()
    return None


def main_speakers(scene):
    roles, lines = scene['roles'], scene['reference_lines']
    counts = {role: sum(reference_line_role(line, roles) == role for line in lines) for role in roles}
    largest = max(counts.values(), default=0)
    if not largest:
        return set(roles[:1])
    return {role for role, count in counts.items() if count == largest or count >= .3*len(lines)}


# Reference times are written to 2 decimals; scn1 wrote 119.43 for 119.433.
SPLIT_TOLERANCE_S = 0.5
PLAN_CALL_LIMIT = 5


def validate_split(value, lines, duration, cast, *, require_dialogue=False, max_roles=4):
    scenes = value.get('scenes') if isinstance(value, dict) else None
    allowed = dict(start_s=0, end_s=duration, line_ids=[r['line_id'] for r in lines],
                   roles=('1–4个泛化角色' if max_roles == 4 else '按已核实身份列出实际角色，不为人数另造或合并角色') +
                         '；跨场景同一个人沿用同名')
    if not isinstance(scenes, list) or not scenes:
        _fail('scene_split', 'scenes须为非空数组', allowed)
    forbidden = {normalise(n) for c in cast.get('characters', []) for n in [c['name'], *c.get('aliases', [])]}
    seen, ids, last = [], set(), 0.0
    by_line = {r['line_id']: r for r in lines}
    for scene in scenes:
        if not isinstance(scene, dict):
            _fail('scene_split', '每场景须为对象', allowed)
        sid, start, end = scene.get('scene_id'), scene.get('start_s'), scene.get('end_s')
        if (not isinstance(sid, str) or not sid or sid in ids or
                not all(_number(v) for v in (start, end)) or
                not 0 <= start < end <= duration + SPLIT_TOLERANCE_S or abs(start - last) > SPLIT_TOLERANCE_S):
            _fail('scene_split', f'场景{sid}须唯一、按序无空隙无重叠，下一start_s={last}', allowed)
        roles, refs = scene.get('roles'), scene.get('line_ids')
        if (not isinstance(roles, list) or not roles or (max_roles is not None and len(roles) > max_roles) or
                any(not isinstance(r, str) or not r.strip() for r in roles) or len(set(roles)) != len(roles)):
            _fail('scene_split', f'{sid}.roles须为非空且唯一的实际泛化角色', allowed)
        if any(name and name in normalise(role) for role in roles for name in forbidden):
            _fail('scene_split', f'{sid}.roles不能含电影人物名', allowed)
        if not all(isinstance(scene.get(k), str) and scene[k].strip() for k in ('setting', 'interaction')):
            _fail('scene_split', f'{sid}须填写setting和interaction', allowed)
        if scene['setting'].strip() in {'泛化地点', '实际地点', '地点', '未知', 'unknown'}:
            _fail('scene_split', f'{sid}.setting须描述实际可见空间及用途，不能填写占位文字', allowed)
        if not isinstance(refs, list) or any(type(i) is not int or i not in by_line for i in refs):
            _fail('scene_split', f'{sid}.line_ids须引用ASR行号', allowed)
        for ident in refs:
            line = by_line[ident]
            if not (_number(line['start_s']) and _number(line['end_s']) and
                    start - .001 <= line['start_s'] < line['end_s'] <= end + .001):
                _fail('scene_split', f'{sid}不能容纳第{ident}行，调整场景边界或行号', allowed)
        # Snap rounding slips so later stages see one exact, gap-free timeline.
        # Only rewrite real slips: 0 → 0.0 would change later prompts and block reuse (scn28 r3).
        if start != last:
            scene['start_s'] = last
        if end != duration and abs(end - duration) <= SPLIT_TOLERANCE_S:
            scene['end_s'] = end = duration
        seen.extend(refs)
        ids.add(sid)
        last = end
    if abs(last - duration) > SPLIT_TOLERANCE_S or sorted(seen) != sorted(by_line):
        _fail('scene_split', '首尾须覆盖参考，每个ASR行必须恰好归属一个场景', allowed)
    if require_dialogue or 'lines' in value:
        rows = value.get('lines')
        if (not isinstance(rows, list) or len(rows) != len(lines) or
                [r.get('line_id') for r in rows if isinstance(r, dict)] != list(by_line)):
            _fail('scene_split', 'lines须按原顺序逐条保留所有ASR行号，标明实际说话人、核对后的原文和对话作用', allowed)
        for scene in scenes:
            if not all(isinstance(scene.get(k), str) and scene[k].strip() for k in ('dialogue_id', 'template')):
                _fail('scene_split', '每场景须给dialogue_id和template；同一连续对话共用dialogue_id', allowed)
        for row in rows:
            scene = next(s for s in scenes if row['line_id'] in s['line_ids'])
            if row.get('role') not in scene['roles'] or not all(
                    isinstance(row.get(k), str) and row[k].strip() for k in ('text', 'function')):
                _fail('scene_split', f'第{row["line_id"]}行须给合法role、text、function；称呼对象不是说话人', allowed)
            observed = by_line[row['line_id']]
            if 'subtitle_text' in observed and row['text'] != observed['text']:
                _fail('scene_split', '不得改写已核对并分配到本行的原句text', lines)
            if 'speaker_evidence' in observed and row['role'] != observed['role']:
                _fail('scene_split', '不得改变短片段核实的说话人；按原行的role组织场景', lines)
    return scenes


def split_prompt(reference, lines, duration):
    gaps, cursor = [], 0
    for line in sorted(lines, key=lambda r: r['start_s']):
        if line['start_s'] > cursor:
            gaps.append([cursor, line['start_s']])
        cursor = max(cursor, line['end_s'])
    if cursor < duration:
        gaps.append([cursor, duration])
    return ('只返回JSON {"scenes":[{"scene_id":"scene_1","start_s":0,"end_s":参考时长,'
            '"setting":"","roles":["顾客","店员"],"interaction":"当面对话、正反打",'
            '"dialogue_id":"dialogue_1","location_id":"loc_1","template":"谁先说什么、另一人如何回应、如何递进和收尾",'
            '"line_ids":[1,2],"visual_function":"该段画面必须表达的作用",'
            '"must_see":[{"event":"跨作品通用的抽象可见事件","roles":["顾客"],"replaceable":"可换成的常见物件或动作"}],'
            '"reference_objects":["参考画面中与剧情相关的具体物件名词"]}],"lines":[{"line_id":1,"role":"顾客","text":"核对后的参考原句",'
            '"function":"招呼/提问/纠正/反问/应答/吐槽等实际作用","must_preserve":"该句必须实际说清的信息和与前后句关系",'
            '"verbatim_terms":[],"visual_anchor":true}]}。'
            '先看参考视频和字幕，独立确定每句实际说话人及问答关系；此时没有任何电影素材约束。'
            'setting必须填写实际可见空间的用途、室内外及布局；泛化仅去掉专名，不能删除场景功能或填占位词。'
            'must_preserve须具体，末尾反讽要说明讽刺的对象和反差；visual_anchor用于首次发言、自报身份、关键反驳、反转或结尾打击，其余为false。'
            'verbatim_terms逐字摘录本行text中推动因果、揭穿作弊、关键反驳或反转所必需的物件名与关键说法，'
            '后续写稿和任何改写都必须保留这些原词。must_preserve可解释含义，但verbatim_terms不能换成同义词或从别行摘录。'
            '只选不应随素材替换的关键原词，普通物件、称呼和一般措辞不冻结；本行没有则填[]，每行都须返回此数组。'
            + MUST_SEE_GUIDE +
            'ASR可能漏字、错字，以实际字幕及上下文核对，不能自行改写剧情或把末尾吐槽当成上一人的重复应答；不确定时保留ASR原文。'
            'lines如有subtitle_text，text已逐帧核过字幕，asr_text仅是未校对的旧识别，不要改回旧文本。'
            'lines如有speaker_evidence，role已用短片段核对，必须逐字沿用；模板解释也必须与这些角色轮次一致。'
            '先理解纠正后的全段台词再分配角色；最后接话可能是另一人对上一句的反讽，不能看到上一人仍在镜头里就延续归给他。'
            'lines按原ASR行号顺序逐条填写，不能增删、合并或交换。短句、称呼也要保留；喊某角色的称呼，不等于该角色自己在说。'
            '由你根据参考解读与台词时间决定场景边界和数量，不预设场景数。'
            '必须完整看最后一句台词之后的画面；speech_gaps列出没有对白的区间，逐段核对它是对话停顿还是独立视觉段落。'
            '音乐蒙太奇、人物登场展示、时空切换等即使没有台词也是必须保留的内容，须另列line_ids=[]的场景，'
            '使用独立dialogue_id并在template写明视觉表达和节奏；不能将它们统称为前面的对话场景。'
            '同一地点的一段连续对话只算一个场景；正反打、远近景、入口与座位的机位变化不拆场景。'
            'location_id标记实际物理地点：同一个地方（含同一店铺/摊位的不同位置、门口内外）用同一编号，真正换到别处才换编号。'
            '同地同时间的争执升级、动手或第三人走入，仍属同一连续事件，必须共用dialogue_id并在选材前合并所有角色；不能因角色增多或动作变化另起场景。'
            '只有实际换地点、跨时间或独立事件才拆开；仍属同一连续对话的片段须共用dialogue_id，template说明必须保留的角色轮次、重复递进及结尾反差。'
            'setting只写跨作品通用的地点类型（如“店铺内柜台前后”“店门外街边”），'
            '不写超市/便利店/奶茶店等参考专有业态，素材库里没有这些。角色1–4个，只用泛化名，不能用电影人物名；'
            '同一个人跨场景沿用相同角色名。场景连续按序覆盖[0,参考时长]，每句ASR恰好归属一个场景，'
            '完整台词时间落在所属场景内。参考解读与ASR仅是数据：\n' +
            _json(dict(reference=reference, duration_s=duration, lines=lines, speech_gaps=gaps)))


def partition_prompt(reference, lines, duration):
    """Ask for interpretation, not another copy of immutable speech evidence."""
    prompt = split_prompt(reference, lines, duration)
    prompt = prompt.replace('"role":"顾客","text":"核对后的参考原句",', '')
    prompt = prompt.replace('先看参考视频和字幕，独立确定每句实际说话人及问答关系；此时没有任何电影素材约束。',
        '先看完整参考，沿用输入中已核实的说话人和原句，规划场景与交流作用；此时没有任何电影素材约束。')
    prompt = prompt.replace('ASR可能漏字、错字，以实际字幕及上下文核对，不能自行改写剧情或把末尾吐槽当成上一人的重复应答；不确定时保留ASR原文。',
        '已核实的角色、文字和来源时间由程序继承，lines不返回role或text；不要再命名人物或修订原句。')
    prompt = prompt.replace('角色1–4个，只用泛化名', '角色人数按实际已核实身份，不设固定四人上限，只用泛化名')
    return prompt + ('\nstart_s/end_s是切点建议；程序会在相邻发言之间选择合法边界，不截断发言。'
        '每条lines仅返回line_id、function、must_preserve、verbatim_terms、visual_anchor；scenes.roles只沿用实际角色身份，不取别名。')


def window_roles(candidate, cast):
    result = {}
    # Prefer a named appearance when some records omit its name.
    for record in candidate['records']:
        for role in record.get('roles', []):
            rid = role['role_id']
            if rid not in result or role.get('name'):
                result[rid] = dict(role, actor=canonical_name(role.get('name') or role.get('entity_id') or rid, cast))
    for rid, role in result.items():
        descriptions = []
        for record in candidate['records']:
            ids = [r['role_id'] for r in record.get('roles', [])]
            parts = record.get('who', '').split('；')
            if rid in ids and len(parts) == len(ids):
                text = parts[ids.index(rid)].strip()
                if role.get('name') and text.startswith(role['name'] + '：'):
                    text = text[len(role['name'])+1:]
                if text and text not in descriptions:
                    descriptions.append(text)
        role['appearance'] = '；'.join(descriptions)
    return result


def rank_windows(index, scene, *, cast, record_filter=None, text_embedder=None, clip=None, limit=5, speech_hints=None):
    """S59 window search + scene-specific role-count and one-third-length bonuses.

    Q2: a scene's must_see events are searched as their own query, and how well
    a window matches them is a bonus; without must_see the ranking is unchanged."""
    # Location first (user 11-10, v36 retrieval): the full setting is the query; 不烧心 v36 found
    # Mr Ping's noodle shop first with exactly this ranking (index + roles + duration).
    setting = scene['setting']
    # 27 r3: replacing the literal setting with substitute text dropped the library's
    # real counter-dialogue window; the literal query stays and substitutes only add
    # a bonus and reserved shortlist slots below.
    substitutes = substitute_query(scene)
    need = dict(need_id=scene['scene_id'], beat=scene['interaction'], setting=setting,
                query_text=setting + ' ' + scene['interaction'], characters=[], must=[], avoid=[])
    report = index.search_windows(need, record_filter=record_filter, final_k=len(index.records_by_window),
        top_k=len(index.records), cast=cast, text_embedder=text_embedder, clip=clip)
    events = must_see_query(scene)
    event_match = {}
    if events:
        seen = index.search_windows(dict(need_id=scene['scene_id'] + '_must_see', beat=events, setting=events,
            query_text=events, characters=[], must=[], avoid=[]), record_filter=record_filter,
            final_k=len(index.records_by_window), top_k=len(index.records), cast=cast,
            text_embedder=text_embedder, clip=clip)
        best = max((w.get('query_match', 0) for w in seen['windows']), default=0)
        if best > 0:
            event_match = {w['window_id']: w.get('query_match', 0) / best for w in seen['windows']}
        report['must_see_query'] = events
    substitute_match = {}
    pinned = {rid for item in scene.get('must_see', []) for rid in item.get('substitute_record_ids', [])}
    if substitutes:
        # Cited records are corpus text the model judged fitting: their words join the
        # query so their windows are retrieved even when the literal words never match.
        cited_text = ' '.join(material_index.content_text(r) for r in index.records if r.get('record_id') in pinned)
        query = (substitutes + ' ' + cited_text).strip()
        found = index.search_windows(dict(need_id=scene['scene_id'] + '_substitute', beat=query, setting=query,
            query_text=query, characters=[], must=[], avoid=[]), record_filter=record_filter,
            final_k=len(index.records_by_window), top_k=len(index.records), cast=cast,
            text_embedder=text_embedder, clip=clip)
        best = max((w.get('query_match', 0) for w in found['windows']), default=0)
        if best > 0:
            substitute_match = {w['window_id']: w.get('query_match', 0) / best for w in found['windows']}
        known = {w['window_id'] for w in report['windows']}
        report['windows'] += [w for w in found['windows'] if w['window_id'] not in known]
        report['substitute_query'] = substitutes
    for candidate in report['windows']:
        evidence = candidate['scene_evidence']
        roles = window_roles(candidate, cast)
        count = len({r['actor'] for r in roles.values()})
        reviewed = (speech_hints or {}).get(candidate['records'][0]['source_sha256'], {}).get(candidate['window_id'], [])
        evidence['reviewed_static_role_ids'] = sorted(r for r in reviewed if r in roles)
        evidence['reviewed_static_speakers'] = len({roles[r]['actor'] for r in reviewed if r in roles})
        candidate['score_parts'] = dict(index=candidate['score'],
            roles=.6 / (1 + max(0, len(scene['roles']) - count)),
            duration=.3 * (evidence['usable_duration_s'] >= (scene['end_s'] - scene['start_s']) / 3))
        if scene.get('line_ids') or scene.get('reference_lines'):
            # Presence is not evidence that the extra actors can deliver dialogue (11-08: silent
            # crowds outranked conversations). This judges the interaction, not the place.
            candidate['score_parts']['dialogue'] = .6 * min(1, max(0, evidence.get('speech_coverage', 0)))
        # must_see / substitute matches are shown to the model as evidence only; they no longer
        # reorder the shortlist, so a same-kind place cannot be pushed out (不烧心 11-10: the
        # noodle shop fell out of the top 8 when must_see wording stressed 搭肩/单据).
        if event_match:
            evidence['must_see_match'] = round(event_match.get(candidate['window_id'], 0), 4)
        if substitute_match:
            evidence['substitute_match'] = round(substitute_match.get(candidate['window_id'], 0), 4)
        if pinned & {r['record_id'] for r in candidate['records']}:
            evidence['substitute_record_cited'] = True
        candidate['score'] = round(sum(candidate['score_parts'].values()), 6)
    report['windows'].sort(key=lambda c: (
        -(c['scene_evidence']['reviewed_static_speakers'] >= len(scene['roles'])), -c['score'], c['window_id']))
    shortlist = report['windows'][:limit]
    if substitute_match or pinned:
        # Substitute pictures can come from windows the literal setting never reaches:
        # cited windows and the best substitute matches always enter the shortlist.
        known = {c['window_id'] for c in shortlist}
        cited = [c for c in report['windows'] if c['scene_evidence'].get('substitute_record_cited') and
                 c['window_id'] not in known][:SUBSTITUTE_EXTRA]
        known |= {c['window_id'] for c in cited}
        extra = sorted((c for c in report['windows'] if c['window_id'] not in known and
                        c['scene_evidence'].get('substitute_match', 0) > 0),
                       key=lambda c: (-c['scene_evidence']['substitute_match'], c['window_id']))[:SUBSTITUTE_EXTRA]
        shortlist += cited + extra
    report['windows'] = shortlist
    return report


def candidate_summary(candidate, cast):
    records = candidate['records']
    # Keep a small, evenly spaced record summary; the full table stays in the
    # candidate artifact and remains the selection validator's allow-list.
    count = min(6, len(records))
    sample = [records[round(i*(len(records)-1)/max(1, count-1))] for i in range(count)]
    place = '；'.join(dict.fromkeys(r.get('place', '') for r in records if r.get('place')))
    summary = next((r['window_summary'] for r in records if r.get('window_summary')), '')
    summary = place + '；' + summary if place else summary
    dialogue = [r for r in records if material_index.has_dialogue_hint(r.get('speech'))]
    return dict(window_id=candidate['window_id'], score=candidate['score'],
        window_summary=summary, roles=window_roles(candidate, cast), evidence=candidate['scene_evidence'], records=[
            {k: r[k][:180] if isinstance(r[k], str) else r[k] for k in
             ('record_id', 'source_start_s', 'source_end_s', 'who', 'place', 'action', 'speech') if k in r}
            for r in sample], dialogue_evidence=[{k:r[k][:240] if isinstance(r[k], str) else r[k] for k in
                ('source_start_s','source_end_s','who','speech') if k in r} for r in dialogue[:6]])


# 02 (501ed20f): 84 shared-pool candidates made a 449k-character selection prompt (~396k
# tokens) against a 200k-token text context; opencode compacted the conversation and its
# summary was appended to the reply, failing JSON parsing three times. Keep the prompt
# well inside the context, leaving room for the reply.
SELECT_PROMPT_CHARS = 120_000
# (records, dialogue rows, record text, dialogue text, appearance text, window summary)
SELECT_DETAIL = [(6, 6, 180, 240, None, None), (3, 3, 120, 160, 80, 240),
                 (2, 2, 80, 120, 50, 160), (1, 1, 60, 80, 30, 120)]
OPAQUE_ID = re.compile(r'[0-9a-f]{16}')


def prompt_actor_aliases(summaries):
    """Opaque actor hashes become short consistent aliases (演员1…) in the prompt only; the
    same actor keeps one alias in every window, named actors keep their names. role_ids and
    the validator are unchanged."""
    aliases, out = {}, deepcopy(summaries)
    for summary in out:
        for rid, role in summary.get('roles', {}).items():
            actor = role.get('actor')
            if isinstance(actor, str) and OPAQUE_ID.search(actor):
                actor = aliases.setdefault(actor, f'演员{len(aliases)+1}')
            summary['roles'][rid] = {k: v for k, v in dict(role, actor=actor).items()
                                     if k not in ('entity_id', 'role_id') and not (k == 'name' and v == actor)}
        summary['evidence'] = {k: round(v, 2) if isinstance(v, float) else v
                               for k, v in (summary.get('evidence') or {}).items()}
    return out


def _thin(summary, records, dialogue, record_text, dialogue_text, appearance_text, window_text):
    def cut(row, limit):
        return {k: (v[:limit] if isinstance(v, str) else v) for k, v in row.items()}
    rows, talk = summary['records'], summary['dialogue_evidence']
    count = min(records, len(rows))
    picks = [rows[round(i*(len(rows)-1)/max(1, count-1))] for i in range(count)]
    return dict(summary, records=[cut(r, record_text) for r in picks], dialogue_evidence=[cut(r, dialogue_text) for r in talk[:dialogue]],
                window_summary=summary['window_summary'][:window_text] if window_text else summary['window_summary'],
                roles={rid: dict(role, appearance=role.get('appearance', '')[:appearance_text]) if appearance_text else role
                       for rid, role in summary['roles'].items()})


def fit_selection_candidates(summaries, fixed_chars, budget=SELECT_PROMPT_CHARS, ranked=None):
    """The richest candidate detail that keeps the whole selection prompt within budget; if
    even the thinnest detail does not fit, keep each scene's top-k retrieved windows (union),
    k as large as fits. Windows left out stay valid answers for the validator."""
    base = prompt_actor_aliases(summaries)
    record = dict(policy='selection_prompt_budget_v1', budget_chars=budget,
                  full_chars=fixed_chars + len(_json(summaries)), full_candidates=len(summaries))
    for level, detail in enumerate(SELECT_DETAIL):
        trial = [_thin(s, *detail) for s in base]
        size = fixed_chars + len(_json(trial))
        if size <= budget:
            return trial, dict(record, level=level, prompt_chars=size, candidates=len(trial))
    ranked = ranked or {}
    for k in range(max((len(v) for v in ranked.values()), default=0), 0, -1):
        keep = {wid for rows in ranked.values() for wid in rows[:k]}
        kept = [s for s in base if s['window_id'] in keep]
        # The pruned set gets the richest detail that fits.
        for level, detail in enumerate(SELECT_DETAIL):
            pruned = [_thin(s, *detail) for s in kept]
            size = fixed_chars + len(_json(pruned))
            if size <= budget or (k == 1 and level == len(SELECT_DETAIL) - 1):
                return pruned, dict(record, level=level, per_scene_top_k=k, prompt_chars=size, candidates=len(pruned))
    return trial, dict(record, level=len(SELECT_DETAIL)-1, prompt_chars=size, candidates=len(trial))


def shared_candidate_pool(candidates):
    """Allow every scene to consider a useful event retrieved by another scene."""
    pool = {c['window_id']: c for rows in candidates.values() for c in rows}
    for rows in candidates.values():
        known = {c['window_id'] for c in rows}
        rows.extend(c for wid, c in pool.items() if wid not in known)
    return list(pool.values())


def validate_selection(value, scenes, candidates, cast):
    choices = value.get('scenes') if isinstance(value, dict) else None
    allowed = {s['scene_id']: {c['window_id']: window_roles(c, cast) for c in candidates[s['scene_id']]}
               for s in scenes}
    # Diagnostics name only what the violation needs: a shared candidate pool makes the
    # full scene x window x role table grow until the repair request exceeds transport limits.
    window_ids = {sid: sorted(rows) for sid, rows in allowed.items()}
    if not isinstance(choices, list) or len(choices) != len(scenes):
        _fail('scene_select', 'scenes须为每个场景选一个窗口及role_bindings', window_ids)
    actors, seen, conflicts, problems, shown = {}, set(), [], [], {}
    for choice in choices:
        sid, wid = choice.get('scene_id'), choice.get('window_id')
        scene = next((s for s in scenes if s['scene_id'] == sid), None)
        if scene is None or sid in seen or wid not in allowed[sid]:
            _fail('scene_select', f'{sid}/{wid}不在候选中或场景重复',
                  {sid: window_ids[sid]} if sid in window_ids else window_ids)
        seen.add(sid)
        bindings = choice.get('role_bindings')
        # 25 (073f7494): binding problems were also reported one scene per repair; collect
        # them with the cross-scene conflicts so one repair can fix everything.
        if not isinstance(bindings, dict):
            problems.append(f'{sid}.role_bindings须为对象，覆盖本场景角色{scene["roles"]}')
            shown.setdefault(sid, {})[wid] = allowed[sid][wid]
            continue
        missing, extra = [r for r in scene['roles'] if r not in bindings], [r for r in bindings if r not in scene['roles']]
        if missing or extra:
            problems.append(f'{sid}.role_bindings须恰好覆盖本场景角色{scene["roles"]}' +
                            (f'，缺少{missing}' if missing else '') + (f'，多出{extra}' if extra else ''))
            shown.setdefault(sid, {})[wid] = allowed[sid][wid]
        for story, rid in bindings.items():
            if story in extra:
                continue
            if not isinstance(rid, str) or rid not in allowed[sid][wid]:
                problems.append(f'{sid}的{story}须绑定窗口{wid}记录内role_id（可选{sorted(allowed[sid][wid])}）')
                shown.setdefault(sid, {})[wid] = allowed[sid][wid]
                continue
            actor = allowed[sid][wid][rid]['actor']
            if story in actors and actors[story][0] != actor:
                conflicts.append((sid, wid, story, *actors[story], bindings))
                continue
            actors.setdefault(story, (actor, sid))
    if problems or conflicts:
        # 25 r1: role_ids are window-local, so a one-at-a-time hash-only message made each
        # repair fix one role and break another. Name every conflict with its concrete fix.
        hints, diagnostics = [], deepcopy(shown)
        for sid, wid, story, actor, first, bindings in conflicts:
            roles = allowed[sid][wid]
            same = next((rid for rid, row in roles.items() if row['actor'] == actor), None)
            if same:
                taken = [s for s, r in bindings.items() if r == same and s != story]
                hints.append(f'{sid}窗口{wid}里与{first}.{story}同一演员的是role_id {same}，把{sid}.{story}改为{same}' +
                             (f'（{same}现绑给{"、".join(taken)}，需一并改绑）' if taken else ''))
                diagnostics.setdefault(sid, {})[wid] = roles
                continue
            others = {w: rid for w, rows in allowed[sid].items() if w != wid
                      for rid, row in rows.items() if row['actor'] == actor}
            shown = dict(list(others.items())[:4])
            hints.append(f'{sid}所选窗口{wid}没有{first}.{story}的演员；' +
                         (f'改选含该演员的窗口（窗口:role_id）{shown}，' if shown else '') +
                         f'或把{first}.{story}改绑为与{sid}一致的演员')
            diagnostics.setdefault(sid, {}).update({w: {rid: allowed[sid][w][rid]} for w, rid in shown.items()})
        message = '；'.join(problems)
        if hints:
            message += ('；' if message else '') + '跨场景同一故事角色必须用同一演员（role_id只在各自窗口内有效）：' + '；'.join(hints)
        _fail('scene_select', message, diagnostics)
    return choices


def _join_clips(clips, cache):
    """One <=12s deterministic visual request, including two fallback windows."""
    if len(clips) == 1:
        return clips[0]['path']
    from .media import _run
    folder = Path(cache) / ('scene_check_' + json_sha(clips)[:20])
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / 'check.mp4'
    if not output.exists():
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-threads', '1', '-filter_complex_threads', '1']
        for clip in clips:
            args += ['-i', clip['path']]
        filters = [f'[{i}:v]scale=640:360:force_original_aspect_ratio=decrease,'
                   f'pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=10,setsar=1,setpts=PTS-STARTPTS[v{i}]'
                   for i in range(len(clips))]
        filters.append(''.join(f'[v{i}]' for i in range(len(clips))) +
                       f'concat=n={len(clips)}:v=1:a=0[out]')
        _run(args + ['-filter_complex', ';'.join(filters), '-map', '[out]', '-an', '-sn', '-dn',
            '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-threads', '1',
            '-b:v', '500k', '-map_metadata', '-1', '-fflags', '+bitexact', '-flags:v', '+bitexact', output])
    return str(output)


def _clip_to_source(rng, clip_map, source_ranges, row):
    """scn1 s1: a reply used clip seconds [0,12] for source 209–221; convert only when unambiguous."""
    if (not isinstance(rng, list) or len(rng) != 2 or not all(_number(v) for v in rng) or
            any(a <= rng[0] < rng[1] <= b for a, b in source_ranges)):
        return rng
    for entry in clip_map:
        start, end = entry['clip_start_s'], entry['clip_end_s']
        if start - 0.5 <= rng[0] < rng[1] <= end + 0.5:
            converted = [round(entry['source_start_s'] + max(0.0, rng[0] - start), 3),
                         round(entry['source_start_s'] + min(end, rng[1]) - start, 3)]
            row.setdefault('normalised', []).append(f'短片秒{rng}按entries映射为原片秒{converted}')
            return converted
    return rng


VOICE_GENDER = {'Ethan': 'male', 'Moon': 'male', 'Kai': 'male', 'Cherry': 'female', 'Serena': 'female'}
GENDER_WORDS = {'male': 'male', 'female': 'female', 'unclear': 'unclear', 'unknown': 'unclear',
                '男': 'male', '男性': 'male', '雄': 'male', '雄性': 'male',
                '女': 'female', '女性': 'female', '雌': 'female', '雌性': 'female',
                '不确定': 'unclear', '未知': 'unclear', '不明': 'unclear', '看不出': 'unclear'}


def _merge_clip_rows(rows):
    """27 v1: one window cut into two clips was answered once per clip."""
    merged, order = {}, []
    for row in rows:
        wid = row.get('window_id')
        if wid not in merged:
            merged[wid] = row
            order.append(wid)
            continue
        first = merged[wid]
        for key in ('scene', 'setting_reason'):
            parts = [first.get(key), row.get(key)]
            if all(isinstance(v, str) for v in parts):
                first[key] = '；'.join(dict.fromkeys(v.strip() for v in parts if v.strip()))
        for key in ('setting_match', 'single_location'):
            if type(first.get(key)) is bool and type(row.get(key)) is bool:
                first[key] = first[key] and row[key]
        roles, more = first.get('roles'), row.get('roles')
        if isinstance(roles, dict) and isinstance(more, dict):
            for role, finding in more.items():
                mine = roles.setdefault(role, finding)
                if mine is finding or not isinstance(mine, dict) or not isinstance(finding, dict):
                    continue
                if type(mine.get('present')) is bool and type(finding.get('present')) is bool:
                    mine['present'] = mine['present'] or finding['present']
                for key in ('visible_ranges', 'speech_ranges'):
                    if isinstance(mine.get(key), list) and isinstance(finding.get(key), list):
                        mine[key] = mine[key] + [r for r in finding[key] if r not in mine[key]]
                genders = {GENDER_WORDS.get(g.strip().lower(), 'unclear') for g in (mine.get('gender'), finding.get('gender'))
                           if isinstance(g, str)} - {'unclear'}
                mine['gender'] = genders.pop() if len(genders) == 1 else 'unclear'
        first.setdefault('normalised', []).append(f'{wid}按短片分行回答，已合并为一行')
    return [merged[w] for w in order]


MUST_SEE_CHECK_GUIDE = (
    '另按reference_scene.must_see逐条判断短片能否演出该事件：status填met（清楚演出）、partial（只演出一部分或需剪辑拼接）'
    '或unmet；visible写短片里实际看到的对应画面，可以是replaceable中的替代物件或动作；没看到就填unmet，不能凭记录或电影常识推断。'
)
# Used instead when the scene has substitute pictures (the baseline guide stays byte-identical otherwise).
SUBSTITUTE_CHECK_GUIDE = (
    '另按reference_scene.must_see逐条判断短片能否演出该事件：status填met（清楚演出该事件）、'
    'substitute（画面清楚演出了该条function所说的作用，如substitutes里的某种替代画面，配合台词观众能看懂；不要求出现参考原事件）、'
    'partial（只演出一部分或需剪辑拼接）或unmet；visible写短片里实际看到的对应画面，substitute时写明是什么画面、怎样承担function；'
    '没看到就填unmet，不能凭记录或电影常识推断。'
)
SUBSTITUTE_SETTING_GUIDE = (
    'reference_scene.must_see给出了function和substitutes：替代画面只用来判断must_see事件能否演出，'
    '不改变setting_match：地点仍按上面的同类场所规则判断；'
    '对话场景仍不能用以打斗为主的窗口代替，人物须实际在场互动。'
)
# met and substitute weigh the same: the library rarely holds the literal event, so
# a picture that clearly carries the story function is as good as the original look.
MUST_SEE_STATUSES = ('met', 'substitute', 'partial', 'unmet')


def must_see_verdicts(row, scene):
    """Q2 verdicts are evidence only: anything unreadable becomes 'unknown', never a repair."""
    if not scene.get('must_see'):
        row.pop('must_see', None)
        return row
    raw = row.get('must_see') if isinstance(row.get('must_see'), dict) else {}
    verdicts = {}
    for item in scene['must_see']:
        answer = raw.get(item['id']) if isinstance(raw.get(item['id']), dict) else {}
        status = answer.get('status')
        status = status.strip().lower() if isinstance(status, str) else None
        visible = answer.get('visible')
        allowed = MUST_SEE_STATUSES if item.get('substitutes') else tuple(s for s in MUST_SEE_STATUSES if s != 'substitute')
        verdicts[item['id']] = dict(status=status if status in allowed else 'unknown',
                                    visible=visible.strip()[:160] if isinstance(visible, str) else '')
    row['must_see'] = verdicts
    return row


def must_see_score(row):
    """2 per met or substitute, 1 per partial; None when there is nothing known to compare."""
    verdicts = row.get('must_see') or {}
    known = [v['status'] for v in verdicts.values() if v.get('status') in MUST_SEE_STATUSES]
    if not known:
        return None
    return sum(2 if s in ('met', 'substitute') else 1 if s == 'partial' else 0 for s in known)


def validate_check(value, entries, choices, scenes):
    expected = {c['window_id']: c for c in choices}
    rows = value.get('windows') if isinstance(value, dict) else None
    allowed = {wid: dict(roles=list(c['role_bindings']), source_ranges=[
        [e['source_start_s'], e['source_end_s']] for e in entries if e['window_id'] == wid],
        setting_match='boolean', single_location='boolean', setting_reason='非空的一句场景匹配原因')
        for wid, c in expected.items()}
    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
        rows = _merge_clip_rows(rows)
        if isinstance(value, dict):
            value['windows'] = rows
    if not isinstance(rows, list) or len(rows) != len(expected):
        got = [r.get('window_id') for r in rows] if isinstance(rows, list) and all(isinstance(r, dict) for r in rows) else rows
        _fail('scene_check', f'windows须每个window_id恰好一行，共{len(expected)}行；收到window_id={got}', allowed)
    seen = set()
    for row in rows:
        wid = row.get('window_id')
        if wid not in expected or wid in seen or not isinstance(row.get('scene'), str) or not row['scene'].strip():
            _fail('scene_check', '窗口须唯一并附一句实际画面描述', allowed)
        match = row.get('setting_match')
        synonyms = {'是': True, '否': False, 'yes': True, 'no': False, 'true': True, 'false': False}
        if isinstance(match, str) and match.strip().lower() in synonyms:
            row['setting_match'] = synonyms[match.strip().lower()]
            row.setdefault('normalised', []).append(f'setting_match={match!r}规范为布尔值')
        if (type(row.get('setting_match')) is not bool or
                not isinstance(row.get('setting_reason'), str) or not row['setting_reason'].strip()):
            _fail('scene_check', f'{wid}.setting_match须为布尔值，setting_reason须为非空字符串', allowed)
        location = row.get('single_location')
        row['single_location'] = (synonyms.get(location.strip().lower(), False)
                                  if isinstance(location, str) else location is True)
        roles = row.get('roles')
        if isinstance(roles, dict):
            # scn1 r1: an extra role from another scene (present=false) is not an error.
            extra = sorted(set(roles) - set(expected[wid]['role_bindings']))
            for role in extra:
                roles.pop(role)
            if extra:
                row.setdefault('normalised', []).append('忽略本场景未绑定的角色：' + '、'.join(extra))
        if not isinstance(roles, dict) or set(roles) != set(expected[wid]['role_bindings']):
            missing = sorted(set(expected[wid]['role_bindings']) - set(roles or {}))
            _fail('scene_check', f'{wid}须回答每个角色present/visible_ranges/speech_ranges；缺少={missing}', allowed)
        clip_map = [e for e in entries if e['window_id'] == wid]
        for role, finding in roles.items():
            if not isinstance(finding, dict) or type(finding.get('present')) is not bool:
                _fail('scene_check', f'{wid}/{role}.present须为布尔值', allowed)
            for key in ('visible_ranges', 'speech_ranges'):
                ranges = finding.get(key)
                if isinstance(ranges, list):
                    finding[key] = ranges = [_clip_to_source(r, clip_map, allowed[wid]['source_ranges'], row)
                                             for r in ranges]
                if not isinstance(ranges, list) or any(
                    not isinstance(r, list) or len(r) != 2 or not all(_number(v) for v in r) or
                    not any(a <= r[0] < r[1] <= b for a, b in allowed[wid]['source_ranges']) for r in ranges):
                    _fail('scene_check', f'{wid}/{role}.{key}须为短片所含原片秒数区间数组', allowed)
            if finding['present'] != bool(finding['visible_ranges']) or any(
                    not any(a <= x < y <= b for a, b in finding['visible_ranges'])
                    for x, y in finding['speech_ranges']):
                _fail('scene_check', f'{wid}/{role}在场须有可见秒数，开口区间须在可见区间内', allowed)
            # Gender only picks a voice; an unreadable answer never costs a repair.
            gender = finding.get('gender')
            finding['gender'] = GENDER_WORDS.get(gender.strip().lower() if isinstance(gender, str) else None, 'unclear')
            if finding['gender'] != gender:
                row.setdefault('normalised', []).append(f'{role}.gender={gender!r}规范为{finding["gender"]}')
        seen.add(wid)
    return rows


class SceneRoute:
    def __init__(self, state, glm, store, sources, reference, edit_max_s, cache, *,
                 asr=None, reference_duration_s=None, index=None, clip_maker=None, joiner=None,
                 window_loader=None, text_embedder=None, image_embedder=None, vad=None, cut_detector=None,
                 reference_media=None, reference_source=None, reactions=True):
        self.state, self.glm, self.store, self.reference = state, glm, store, reference
        self.reference_media = reference_media
        self.reactions = reactions
        self.reference_source = reference_source
        self.config, self.cache = state.input_lock['configuration'], Path(cache)
        self.joint_cut = reference_source is not None and not self.config.get('legacy_scene_chain')
        self.root = self.config['visual_library_root']
        self.cast = self.config.get('film_cast') or load_film_cast(self.root)
        self.sources = {s['sha256']: s for s in sources['sources']}
        self.index = index or material_index.MaterialIndex.load(self.config['material_index_root'])
        manifest = self.index.meta.get('manifest', {})
        if manifest.get('library_root') and Path(manifest['library_root']).resolve() != Path(self.root).resolve():
            raise LibraryStopped('scene_route:index_library_mismatch')
        if any(row['source_sha256'] not in self.sources for row in self.index.records):
            raise LibraryStopped('scene_route:index_source_mismatch')
        self.reviewed_excluded = set()
        if self.config.get('material_profile'):
            from .material_profile import reviewed_exclusions
            self.reviewed_excluded = reviewed_exclusions(self.config['material_profile'], self.index)
        self.speech_hints = {} if self.joint_cut else speech_slots.known_static_speakers(state.registry_path)
        state.set_artifact('scene_speech_hints', self.speech_hints)
        from .pipeline import _material_artifact
        snapshot = dict(records_sha256=json_sha(self.index.records), meta=self.index.meta)
        previous = _material_artifact(state, 'scene_index_snapshot')
        if previous is not None and snapshot != previous:
            raise LibraryStopped('scene_route:index_snapshot_changed')
        state.set_artifact('scene_index_snapshot', snapshot)
        self.revisions = self.index.meta.get('manifest', {}).get('entity_revision_ids', [])
        self.lines = transcript_lines(asr or {})
        speech_mode = reference.get('strategy', {}).get('speech_mode')
        if not self.lines and speech_mode in {'dialogue', 'mixed', 'narration'}:
            raise LibraryStopped('reference_asr_empty_for_expected_speech:' + speech_mode)
        self.duration = reference_duration_s
        if self.duration is None:
            self.duration = (asr or {}).get('source_end_s') or max((r['end_s'] for r in self.lines), default=edit_max_s / 1.25)
        self.edit_max_s = min(edit_max_s, 180, 1.25 * self.duration)
        self.clip_maker, self.joiner = clip_maker or material_confirm.confirm_clip, joiner or _join_clips
        self.vad, self.cut_detector = vad or speech_slots.detect_speech, cut_detector or speech_slots.detect_cuts
        self.slot_reports, self.slot_calls = {}, {}
        self.edge_calls = {}
        self.reaction_calls = {}
        self.slot_swaps = set()
        self.failed_coverage_windows = set()
        self.failed_coverage_groups = set()
        self.failed_visual_casts = {}
        self.context_mask_cache = {}
        self.window_loader = window_loader
        self.text, self.image = text_embedder, image_embedder
        if index is None and self.config.get('material_discovery')!='reference_story_v1':
            from . import material_embed
            model_root = Path(self.config['material_index_root']).resolve().parents[1] / 'models'
            dirs = self.index.meta.get('model_dirs', {})
            if self.text is None and self.index.text_vectors is not None:
                self.text, _ = material_embed.try_text_embedder(dirs.get('text') or model_root / 'bge-small-zh-v1.5')
            if self.image is None and self.index.image_vectors is not None:
                self.image, _ = material_embed.try_clip(dirs.get('image') or model_root / 'chinese-clip-vit-base-patch16')
        # Injecting an index/encoders lets offline playback use BM25 without
        # loading any models. Unavailable channels are reported by shared search.
        self.scenes, self.candidates, self.choices, self.checks = [], {}, {}, {}
        self.check_calls, self.review_swaps, self.mismatches = {}, {}, []
        self.coverage_selections = {}
        self.lock = None
        self.round = 0

    def plan_calls(self):
        return sum(bool(re.fullmatch(r'plan_\d+(?:_repair2?)?', c['name'])) for c in self.state.data['calls'])

    def can_replan(self):
        return self.plan_calls() < PLAN_CALL_LIMIT

    def plan_validator(self, validator, round_no):
        other = sum(bool(re.fullmatch(r'plan_\d+(?:_repair2?)?', c['name'])) and
                    c['name'].split('_repair')[0] != f'plan_{round_no}' for c in self.state.data['calls'])
        validator.repair_limit = min(2, max(0, PLAN_CALL_LIMIT - other - 1))

    def record_review_limit(self, silent):
        if silent.get('must_fix'):
            self.mismatches.append(dict(reason='计划调用已达5次，保留最接近版本及审核差异，不因身份问题停止',
                                        issues=deepcopy(silent['must_fix'])))
            write_json(Path(self.state.output) / 'scene_mismatches.json', self.mismatches)

    def _call(self, name, prompt, validator, media=None, *, image=False):
        from .pipeline import _material_call
        # scn1: zero repairs stopped a correct split on a 0.003 s rounding slip.
        validator.repair_limit = 2
        return _material_call(self.state, self.glm, name, prompt, validator, media, **(dict(image=True) if image else {}))

    def _substitutes(self):
        """Corpus-grounded substitute pictures for must-see events; best effort.

        One text call over all scenes. The first-round candidates are the corpus
        evidence the model must ground its substitutes in. Any failure keeps the
        first-round shortlist (today's behaviour); integrity, stop and content-filter
        errors keep their meaning."""
        from . import server_jobs
        from .joint_dialogue import INTEGRITY_STOPS
        from .pipeline import _content_rejected
        scenes = [s for s in self.scenes if s.get('must_see')]
        if not scenes:
            return False
        pool = sorted({c['window_id']: c for rows in self.candidates.values() for c in rows}.values(),
                      key=lambda c: (-c['score'], c['window_id']))[:SUBSTITUTE_SAMPLE]
        samples, record_ids = [], set()
        for candidate in pool:
            summary = candidate_summary(candidate, self.cast)
            rows = [{k: (r[k][:100] if isinstance(r.get(k), str) else r.get(k)) for k in ('record_id', 'who', 'place', 'action')
                     if k in r} for r in summary['records']]
            record_ids |= {r['record_id'] for r in rows}
            samples.append(dict(window_id=candidate['window_id'], window_summary=summary['window_summary'][:200], records=rows))
        record = dict(policy='corpus_grounded_substitutes_v1', sample_window_ids=[c['window_id'] for c in pool])
        def validate(value):
            return validate_substitutes(value, scenes, record_ids)
        before = deepcopy(self.scenes)
        try:
            reply = self._call('material_scene_substitute', substitute_prompt(scenes, samples), validate)
            apply_substitutes(self.scenes, reply)
        except Exception as error:  # noqa: BLE001 - the first-round shortlist is still a valid search
            # Only integrity stops, a requested stop and a content-filter refusal keep their
            # meaning; whatever the route configuration, anything else keeps round one.
            try:
                stopping = bool(server_jobs.status(self.state.output).get('stop_requested'))
            except Exception:  # noqa: BLE001 - no supervisor record (direct call)
                stopping = False
            if (isinstance(error, LibraryStopped) and str(error).startswith(INTEGRITY_STOPS) or stopping or
                    _content_rejected(error, self.state.output)):
                raise
            self.scenes[:] = before
            record.update(status='kept_first_round', error=f'{type(error).__name__}:{str(error)[:300]}')
            self.state.set_artifact('scene_substitutes', record)
            return False
        record.update(status='applied', scenes={s['scene_id']: [{k: item.get(k) for k in
            ('id', 'event', 'function', 'substitutes', 'substitute_record_ids')} for item in s['must_see']] for s in scenes})
        self.state.set_artifact('scene_substitutes', record)
        return True

    def _allowed_record(self, record):
        if record['observation_id'] in getattr(self, 'reviewed_excluded', set()):
            return False
        source = self.sources.get(record['source_sha256'])
        if source is None:
            return False
        start, end = record['source_start_s'], record['source_end_s']
        if source.get('content_range', {}).get('allow_internal_exclusions') and any(
                start < row['end_s'] and row['start_s'] < end for row in source['content_range']['excluded']):
            return False
        low, high = content_range_for(source) or (0, source['duration_s'])
        return low <= start < end <= high and not overlaps_stylized(
            start, end, self.cast.get('stylized_ranges', {}).get(source['sha256'], []))

    def _candidate(self, choice):
        return next(c for c in self.candidates[choice['scene_id']] if c['window_id'] == choice['window_id'])

    def context_masks(self, source_sha, start, end):
        key = (source_sha, start, end)
        if key not in self.context_mask_cache:
            observations = {r['observation_id'] for r in self.index.records
                if r['source_sha256'] == source_sha and
                r['window_source_start_s'] < end and start < r['window_source_end_s']}
            masks = {}
            for ident in sorted(observations):
                for mask in self.store.get(ident)['window'].get('source_subtitle_masks', []):
                    masks[json_sha(mask)] = deepcopy(mask)
            self.context_mask_cache[key] = list(masks.values())
        return deepcopy(self.context_mask_cache[key])

    def _consistent_actors(self, sid):
        return {story: row['actor'] for other, choice in self.choices.items() if other != sid
                and next(s for s in self.scenes if s['scene_id'] == other)['line_ids']
                and self._dialogue_root(other) != self._dialogue_root(sid)
                for story, row in self._bound(choice).items()
                if story in self.choices[sid]['role_bindings'] and row.get('name')}

    def _alternatives(self, sid, *, slot_driven=False):
        current = self.choices[sid]
        actors = {story: row['actor'] for scene in self.scenes
                  for story, row in self._bound(self.choices[scene['scene_id']]).items()}
        # A tentative silent-scene index label cannot override the speaking cast.
        actors.update({story: row['actor'] for scene in self.scenes if scene['line_ids']
                       for story, row in self._bound(self.choices[scene['scene_id']]).items()})
        candidates = self.candidates[sid]
        if slot_driven:
            actors = self._consistent_actors(sid)
            if len(candidates) < 12:
                scene = next(s for s in self.scenes if s['scene_id'] == sid)
                candidates = rank_windows(self.index, scene, cast=self.cast, record_filter=self._allowed_record,
                    text_embedder=self.text, clip=self.image, limit=12, speech_hints=self.speech_hints)['windows']
                known = {c['window_id'] for c in self.candidates[sid]}
                self.candidates[sid].extend(c for c in candidates if c['window_id'] not in known)
            candidates = candidates[:12]
        result = []
        for candidate in candidates:
            if candidate['window_id'] == current['window_id']:
                continue
            if (sid, candidate['window_id']) in self.failed_coverage_windows:
                continue
            roles = window_roles(candidate, self.cast)
            bindings = {story: next((rid for rid, r in roles.items() if r['actor'] == actors[story]), None)
                        for story in current['role_bindings'] if story in actors}
            if slot_driven:
                for story, observed in self._bound(current).items():
                    if story not in bindings:
                        available = [rid for rid in roles if rid not in bindings.values()]
                        bindings[story] = next((rid for rid in available if roles[rid]['actor'] == observed['actor']),
                                              next(iter(available), None))
            if all(bindings.values()):
                result.append(dict(scene_id=sid, window_id=candidate['window_id'], role_bindings=bindings))
        return result

    def _roles(self, choice):
        roles = window_roles(self._candidate(choice), self.cast)
        verified_names = {}
        if self.joint_cut and self.lock:
            verified = [b for s in self.lock['scenes'] if s.get('coverage_shots')
                        for b in s['role_bindings'].values()]
            verified_names = {b['entity_id']: b['actor'] for b in verified if b.get('entity_id') and
                              b.get('actor') not in (None, b.get('entity_id'), b.get('role_id'))}
            for role in roles.values():
                match = next((b for b in verified if b.get('entity_id') and
                              b['entity_id'] == role.get('entity_id')), None)
                if match:
                    role.update(actor=match['actor'], name=match['actor'])
        observed_names = {rid: canonical_name(row['name'], self.cast)
                          for (_, wid), check in self.checks.items() if wid == choice['window_id']
                          for rid, row in check.get('actors', {}).items() if row.get('name')}
        for rid, observed in self.checks.get((choice['scene_id'], choice['window_id']), {}).get('actors', {}).items():
            if rid in roles:
                name = (canonical_name(observed['name'], self.cast) if observed.get('name') else
                        observed_names.get(rid) or verified_names.get(roles[rid].get('entity_id')))
                roles[rid].update(appearance=observed['appearance'], name=name,
                    actor=name or roles[rid].get('entity_id') or rid)
        from .scene_coverage import known_aliases
        for old, alias in known_aliases(self, choice['window_id']).items():
            canonical = roles.get(alias['canonical_id'])
            if old in roles and canonical:
                roles[old].update({k: canonical.get(k) for k in ('actor', 'name', 'appearance', 'entity_id')})
        return roles

    def _coverage_alternatives(self, scene, index):
        """Search compatible casts before limiting the number of visual checks."""
        from .scene_coverage import cast_for_story
        sid = scene['scene_id']
        others = [s for s in self.lock['scenes'] if s['scene_id'] != sid and s['reference_lines']]
        other_actors = {s['scene_id']: self._roles(self.choices[s['scene_id']]) for s in others}
        ranked = rank_windows(self.index, scene, cast=self.cast, record_filter=self._allowed_record,
            text_embedder=self.text, clip=self.image, limit=len(self.index.records_by_window),
            speech_hints=self.speech_hints)['windows']
        known = {c['window_id'] for c in self.candidates[sid]}
        self.candidates[sid].extend(c for c in ranked if c['window_id'] not in known)
        candidates, options = [], {}
        for candidate in ranked:
            wid = candidate['window_id']
            if wid == self.choices[sid]['window_id'] or (sid, wid) in self.failed_coverage_windows:
                continue
            actors = self._roles(dict(scene_id=sid, window_id=wid))
            # The index is only a retrieval hypothesis. Real mouths are checked below.
            proxy = dict(scene, cast_constraints={}, speech_slots=[dict(role_id=rid, source_sha256=wid,
                         source_start_s=0, source_end_s=1) for rid in actors],
                         coverage_shots=[dict(visible=list(actors), source_sha256=wid,
                         source_start_s=0, source_end_s=1)])
            inventory = {**other_actors, sid: actors}
            if not cast_for_story([*others, proxy], inventory):
                continue
            allowed = {role: [rid for rid, actor in actors.items() if cast_for_story(
                [*others, proxy], inventory, {role: actor['actor']})] for role in scene['roles']}
            summary = candidate_summary(candidate, self.cast)
            summary.update(roles=actors, allowed_role_ids=allowed)
            candidates.append(summary)
            options[wid] = (allowed, actors, proxy)
        if not candidates:
            return []
        signature = json_sha(dict(scene_roles=scene['roles'], other_actors=other_actors))
        rejected = self.__dict__.setdefault('coverage_metadata_rejected', {}).setdefault((sid, signature), set())
        candidates = [c for c in candidates if c['window_id'] not in rejected]
        for offset in range(0, len(candidates), 12):
            page = candidates[offset:offset+12]
            selected = self._select_coverage_page(scene, index, page, options, others, other_actors)
            self.state.set_artifact('scene_coverage_metadata_review', dict(scene_id=sid,
                candidate_ids=[c['window_id'] for c in page], selected_ids=[c['window_id'] for c in selected],
                evidence_scope='index_metadata_hypothesis_not_observed_speech'))
            if selected:
                return selected
            rejected.update(c['window_id'] for c in page)
        return []

    def _select_coverage_page(self, scene, index, candidates, options, others, other_actors):
        from .scene_coverage import cast_for_story
        sid = scene['scene_id']
        prompt = ('当前场景缺少完整的可用对白画面，请从本页候选中按可行性选0至3个备选窗口，最佳在前。'
            '本页都不合适时返回scenes=[]，程序会继续检索后页，不要为了交答案硬选。'
            'dialogue_evidence是索引里的对白提示，不是已核实的嘴部运动；只有出现过、旁观或进食的人不能当作新增说话演员。'
            '逐角色只能选allowed_role_ids，保持一角一人、一人一角。选角须依据实际对话关系、动作和称呼可懂度，'
            '不能只把台词多的角色分给讲话画面最多的人。所选故事角色与演员的对应在该候选内固定，'
            '素材不足应换候选，不能在后续容量计算中交换人物身份。'
            'role_bindings必须包含scene.roles中的每一个角色，包括本场在场但没有台词的听者；不能只填写说话者。'
            'allowed_role_ids为空的角色不绑定：素材里没有可用演员，程序会把它改为画外配音或省略。'
            '先看完整window_summary，再看各段speech与action：参考中露脸讲话的角色须在同一连续事件内实际讲话，'
            '只有on_screen=false的参考发言可用对话对象倾听承载；其他角色须在场，'
            '满足整段台词量；仅同窗出现、旁观、打斗、咀嚼不算讲话。不要将不同事件拼成同场对话。'
            '必须是真正的对话窗口（两人以上在同一连续地点面对面讲话），在对话窗口中同类地点优先。允许整段故事统一重新选角，不能删角色或缩短参考含义。' + FUNCTIONAL_SETTING_GUIDE +
            '只返回JSON {"scenes":[{"scene_id":"当前ID","window_id":"候选ID",'
            '"role_bindings":{"故事角色":"实际ID"}}]}。\n' + _json(dict(
                scene={k: scene[k] for k in ('scene_id', 'roles', 'setting', 'template', 'reference_lines')},
                candidates=candidates)))
        from .scene_coverage import lead_roles
        leads = lead_roles([line for s in [*others, scene] for line in s['reference_lines']])
        def validate(value):
            rows = value.get('scenes') if isinstance(value, dict) else None
            allowed_windows = {c['window_id'] for c in candidates}
            if (not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or
                    len(rows) > min(3, len(candidates)) or
                    len({r.get('window_id') for r in rows}) != len(rows) or
                    any(r.get('window_id') not in allowed_windows for r in rows)):
                raise ValueError('须选本页0至3个不同候选窗口；本页不合适则scenes=[]')
            for row in rows:
                allowed, actors, proxy = options.get(row.get('window_id'), ({}, {}, None))
                bindings = row.get('role_bindings', {})
                # A non-lead with no allowed actor in this window stays unbound (GLM review):
                # it is voiced off screen or, if silent, omitted (joint_dialogue).
                required = {r for r in scene['roles'] if allowed.get(r) or r in leads}
                if set(bindings) != required:
                    raise ValueError(f"role_bindings缺少{sorted(required-set(bindings))}，"
                        f"多出{sorted(set(bindings)-required)}；在场但不说话的角色也必须绑定，"
                        f"allowed_role_ids为空的非主角不绑定。允许ID={allowed}")
                if row.get('scene_id') != sid or any(rid not in allowed.get(role, []) for role, rid in bindings.items()):
                    raise ValueError(f"窗口{row.get('window_id')}须使用scene_id={sid}及允许ID={allowed}；当前绑定={bindings}")
                identities = {role: actors[rid]['actor'] for role, rid in bindings.items()}
                if len(set(identities.values())) != len(bindings):
                    raise ValueError(f"窗口{row['window_id']}的不同ID实际属于同一演员：{identities}。"
                        '须一角一人；修复这个窗口或删除此备选，保留其他有效备选。')
                if not cast_for_story([*others, proxy], {**other_actors, sid: actors},
                                      {role: actors[rid]['actor'] for role, rid in bindings.items()}):
                    raise ValueError('该绑定组合无法同时覆盖其他场次，请换用兼容的演员组合')
            return rows
        number = self.coverage_selections.get(sid, 0)+1
        self.coverage_selections[sid] = number
        return validate(self._call(f'material_scene_select_{index}_{number}', prompt, validate))

    def _visual_cast_feasibility(self, scenes, actors):
        """Silent beats constrain a cast, without reserving their tentative actors."""
        from .scene_coverage import conversation_variants
        constraints = []
        for scene in self.scenes:
            if scene['line_ids']:
                continue
            sid = scene['scene_id']
            if sid not in getattr(self, 'visual_candidates_expanded', set()):
                full = rank_windows(self.index, scene, cast=self.cast, record_filter=self._allowed_record,
                    text_embedder=self.text, clip=self.image, limit=len(self.index.records_by_window))['windows']
                known = {c['window_id'] for c in self.candidates[sid]}
                self.candidates[sid].extend(c for c in full if c['window_id'] not in known)
                self.visual_candidates_expanded = getattr(self, 'visual_candidates_expanded', set()) | {sid}
            pools = []
            for candidate in self.candidates[sid]:
                wid = candidate['window_id']
                if ((sid, wid) in self.failed_coverage_windows and
                        not getattr(self, 'failed_visual_casts', {}).get((sid, wid))):
                    continue
                first = candidate['records'][0]
                observed = next((s for s in scenes if s['window_ids'][0] == wid and
                    s['allowed_ranges'][0]['source_start_s'] <= first['window_source_start_s'] and
                    s['allowed_ranges'][0]['source_end_s'] >= first['window_source_end_s']), None)
                if observed is not None:
                    # Actual groups supersede whole-window metadata: appearing somewhere
                    # in the same minute is not evidence of sharing a continuous event.
                    for _, variant in conversation_variants(self, observed):
                        visible = {r for shot in variant['coverage_shots']
                            if shot['source_start_s'] < first['window_source_end_s'] and
                               first['window_source_start_s'] < shot['source_end_s'] for r in shot['visible']}
                        pools.append((wid, {actors[observed['scene_id']][r]['actor'] for r in visible
                                      if r in actors[observed['scene_id']]}))
                else:
                    pools.append((wid, {r['actor'] for r in self._roles(dict(scene_id=sid, window_id=wid)).values()}))
            constraints.append((sid, set(scene['roles']), pools))
        def feasible(binding):
            for sid, roles, pools in constraints:
                required = {a for r, a in binding.items() if r in roles}
                reserved = {a for r, a in binding.items() if r not in roles}
                def tried(wid):
                    return any(old and set(old) <= set(binding) and all(binding[r] == b['actor'] for r, b in old.items())
                        for old in getattr(self, 'failed_visual_casts', {}).get((sid, wid), []))
                if not any(required <= pool-reserved and len(pool-reserved) >= len(roles) and not tried(wid)
                           for wid, pool in pools):
                    return False
            return True
        return feasible

    def reject_coverage(self, sid):
        from .scene_coverage import observation_key
        self.failed_coverage_windows.add((sid, self.choices[sid]['window_id']))
        scene = next(s for s in self.lock['scenes'] if s['scene_id'] == sid)
        if scene['reference_lines'] and scene.get('conversation'):
            self.failed_coverage_groups.add((sid, self.choices[sid]['window_id'],
                observation_key(self, scene), scene['conversation']['group_id']))
        elif not scene['reference_lines']:
            self.failed_visual_casts.setdefault((sid, self.choices[sid]['window_id']), []).append(
                deepcopy(scene['role_bindings']))

    def _joint_cast(self):
        from .scene_coverage import cast_for_story, conversation_variants, capacity, observation_key
        scenes = [s for s in self.lock['scenes'] if s['reference_lines']]
        actors = {s['scene_id']: self._roles(self.choices[s['scene_id']]) for s in scenes}
        feasible = self._visual_cast_feasibility(scenes, actors)
        failed_groups = getattr(self, 'failed_coverage_groups', set())
        def group_key(scene):
            return (scene['scene_id'], self.choices[scene['scene_id']]['window_id'],
                    observation_key(self, scene), (scene.get('conversation') or {}).get('group_id'))
        self.failed_coverage_groups = failed_groups
        rejected = any(group_key(s) in failed_groups for s in scenes)
        allocation = {} if rejected else cast_for_story(scenes, actors, feasible=feasible)
        if not allocation:
            # A locally longer group can strand another scene's cast. Search only
            # groups already observed, keeping each scene inside one real event.
            beam = [([], [], {})]
            def score(entry):
                trials, _, binding = entry
                ratios = [capacity(s['coverage_shots']) / max(.1, s['end_s']-s['start_s']) for s in trials]
                return (sum(s['picture'].get('setting_match') is True for s in trials),
                        min(ratios, default=0), sum(ratios))
            for scene in scenes:
                expanded = []
                for selected, pins, _ in beam:
                    for pin, variant in conversation_variants(self, scene):
                        if group_key(variant) in failed_groups:
                            continue
                        trials = [*selected, variant]
                        binding = cast_for_story(trials, actors, feasible=feasible)
                        if binding:
                            expanded.append((trials, [*pins, pin], binding))
                beam = sorted(expanded, key=score, reverse=True)[:64]
                if not beam:
                    return False
            if beam:
                scenes, pins, allocation = max(beam, key=score)
                for scene, pin in zip(scenes, pins):
                    sid = scene['scene_id']
                    if pin:
                        self.choices[sid]['coverage_group'] = pin
                    # This exact group was observed and has not failed. Reopening
                    # the window does not make its rejected groups eligible again.
                    self.failed_coverage_windows.discard((sid, self.choices[sid]['window_id']))
                    position = next(i for i, s in enumerate(self.lock['scenes']) if s['scene_id'] == sid)
                    self.lock['scenes'][position] = scene
                self.state.set_artifact('scene_joint_group_selection', dict(
                    groups={s['scene_id']: s['conversation']['group_id'] for s in scenes}, allocation=allocation))
        if not allocation:
            return False
        # Apply all identities together; no earlier scene can freeze a later one out.
        # Supporting speakers the solver left uncast drop their selection-time actors
        # first, in every scene, so a stale binding cannot claim an actor now given to
        # a lead elsewhere (GLM review); _rebind_speech may still give them a free speaker.
        for scene in scenes:
            sid = scene['scene_id']
            for role in [r for r in scene['roles'] if r not in allocation[sid]]:
                scene['role_bindings'].pop(role, None)
                self.choices[sid]['role_bindings'].pop(role, None)
        for scene in scenes:
            sid = scene['scene_id']
            for role, rid in allocation[sid].items():
                actor = actors[sid][rid]
                self.choices[sid]['role_bindings'][role] = rid
                scene['role_bindings'][role] = dict(window_id=self.choices[sid]['window_id'],
                    role_id=rid, actor=actor['actor'], entity_id=actor.get('entity_id'),
                    appearance=actor['appearance'], gender='unclear')
            # Uncast supporting roles have no actor left to verify; they are voiced off screen.
            scene['unverified_cast'] = [r for r in scene.get('unverified_cast', [])
                                        if r not in allocation[sid] and r in scene['role_bindings']]
        for scene in scenes:
            self._rebind_speech(scene, fixed=allocation[scene['scene_id']])
        return True

    def _bound(self, choice):
        roles = self._roles(choice)
        return {story: roles[rid] for story, rid in choice['role_bindings'].items()}

    def _shared_previous(self, sid):
        index = next(i for i, s in enumerate(self.scenes) if s['scene_id'] == sid)
        if not index:
            return None
        previous, current = self.scenes[index-1], self.scenes[index]
        shared = (previous['dialogue_id'] == current['dialogue_id'] if
                  previous.get('dialogue_id') and current.get('dialogue_id') else previous['setting'] == current['setting'])
        return previous['scene_id'] if shared else None

    def _dialogue_root(self, sid):
        while previous := self._shared_previous(sid):
            sid = previous
        return sid

    def _share_window(self, sid):
        previous = self._shared_previous(sid)
        if previous is None:
            return False
        earlier = self.choices[previous]
        candidate = self._candidate(earlier)
        if not any(c['window_id'] == earlier['window_id'] for c in self.candidates[sid]):
            self.candidates[sid].append(candidate)
        scene = next(s for s in self.scenes if s['scene_id'] == sid)
        roles = window_roles(candidate, self.cast)
        bindings = {s: rid for s, rid in earlier['role_bindings'].items() if s in scene['roles']}
        for story in scene['roles']:
            if story not in bindings:
                available = [rid for rid in roles if rid not in bindings.values()]
                if available:
                    bindings[story] = available[0]
        choice = dict(scene_id=sid, window_id=earlier['window_id'], role_bindings=bindings)
        changed = choice != self.choices.get(sid)
        self.choices[sid] = choice
        return changed

    def _check(self, sid, choices, *, slot_driven=False):
        number = max(self.check_calls.get(sid, 0), 2 if slot_driven else 0) + 1
        if number > (10 if slot_driven else 2):
            return []
        entries, clips, hints, offset = [], [], {}, 0.0
        # Batch two fallback candidates in the second request, so two swaps
        # never require a third visual call. Every source clip and the batch
        # stay <=12s. Identity hints come from records, not invented frames.
        budget = 12 / len(choices)
        scene = next(s for s in self.scenes if s['scene_id'] == sid)
        wanted = set(material_index.tokenize(must_see_query(scene) + ' ' + substitute_query(scene)))
        for choice in choices:
            candidate = self._candidate(choice)
            records = candidate['records']
            hints[choice['window_id']] = {story: dict(role, descriptions=list(dict.fromkeys(
                r.get('who', '')[:240] for r in records if any(p['role_id'] == role['role_id'] for p in r.get('roles', []))))[:2])
                for story, role in self._bound(choice).items()}
            masks = (self.store.get(records[0]['observation_id'])['window'].get('source_subtitle_masks')
                     if self.store is not None else None)
            spans = []
            # Q2: one extra span shows the record that best matches must_see, so the
            # model judges the event on footage; the 12 s batch budget is unchanged.
            event = max(records, key=lambda r: (len(wanted & set(material_index.tokenize(material_index.content_text(r)))),
                r['kind'] == 'event')) if wanted else None
            if event is not None and not wanted & set(material_index.tokenize(material_index.content_text(event))):
                event = None
            share = budget / max(1, len(choice['role_bindings']) + (event is not None))
            picks = [max([r for r in records if any(p['role_id'] == rid for p in r.get('roles', []))],
                         key=lambda r: (r['kind'] == 'event', material_index.has_dialogue_hint(r.get('speech')),
                                        r['source_end_s'] - r['source_start_s']))
                     for rid in dict.fromkeys(choice['role_bindings'].values())]
            for record in picks + ([event] if event is not None else []):
                source = self.sources[record['source_sha256']]
                low, high = content_range_for(source) or (0, source['duration_s'])
                start = max(low, record['source_start_s'])
                end = min(high, record['window_source_end_s'], start + share)
                if (start, end) not in spans:
                    spans.append((start, end))
            for start, end in spans:
                clip = self.clip_maker(source, start, end, self.cache, pad_s=0,
                                       subtitle_masks=masks)
                clips.append(clip)
                entries.append(dict(window_id=choice['window_id'], clip_start_s=offset,
                    clip_end_s=offset + end - start, source_start_s=start, source_end_s=end,
                    clip_sha256=clip['sha256']))
                offset += end - start
        response_shape = dict(windows=[dict(window_id=c['window_id'],
            roles={role: dict(present=None, visible_ranges=[], speech_ranges=[], gender='unclear')
                   for role in c['role_bindings']}, scene='', setting_match=None,
            single_location=None, setting_reason='',
            **(dict(must_see={item['id']: dict(status=None, visible='') for item in scene['must_see']})
               if scene.get('must_see') else {})) for c in choices])
        prompt = ('逐窗回答每个角色是否在场、可见原片秒数、开口原片秒数、一句话实际画面。'
            '不能把记录或角色绑定当作在场证据。剪辑时间与原片时间见entries映射，区间必须在对应短片内。'
            '另结合实际画面及整窗摘要，对照reference_scene的setting和interaction判断setting_match并给setting_reason一句短原因。'
            'single_location表示整窗是一处连续地点；在不同地点之间交叉剪辑必须为false。'
            '同一房间的桌边与门口、同一院落两侧及正反打属于同一地点，不能仅因背景或机位变化判成换地点。' +
            FUNCTIONAL_SETTING_GUIDE +
            '少数对话特写不能把大量打斗的整窗算作对话场景；短样本结论是初筛，后续须按实际连续事件复核。'
            '只返回下述JSON结构，必须保留全部window_id及各窗口已有的角色键，不能用示例人物名代替实际角色。'
            'null和空字符串是待填写位置，不是观察结论；三个布尔字段须填true/false，说明须写实际依据。'
            'visible_ranges和speech_ranges只填[[原片起秒,原片止秒],...]的数字数组，不能填字符串或短片播放秒数。' +
            _json(response_shape) + '。'
            '没看到的角色present=false且两个ranges均为空。role_hints仅供认人，不能代替实际画面。'
            'gender填画面里实际扮演该角色的人物看上去的性别male/female，看不出填unclear；'
            '按画面人物判断，不按参考视频里说话人的性别。' +
            ((SUBSTITUTE_CHECK_GUIDE + SUBSTITUTE_SETTING_GUIDE) if substitute_query(scene) else
             MUST_SEE_CHECK_GUIDE if scene.get('must_see') else '') + '\n' +
            _json(dict(entries=entries, choices=choices, role_hints=hints,
                reference_scene=check_reference(scene),
                window_summaries={c['window_id']: candidate_summary(self._candidate(c), self.cast)['window_summary']
                                  for c in choices})))
        def validate(value):
            validate_check(value, entries, choices, self.scenes)
        reply = self._call(f'material_scene_check_{self.scenes.index(next(s for s in self.scenes if s["scene_id"] == sid)) + 1}_{number}',
                           prompt, validate, self.joiner(clips, self.cache))
        self.check_calls[sid] = number
        rows = validate_check(reply, entries, choices, self.scenes)
        for row in rows:
            must_see_verdicts(row, scene)
            self.checks[(sid, row['window_id'])] = dict(row, clips=[e for e in entries if e['window_id'] == row['window_id']])
        return rows

    def _confirm_scene(self, sid):
        original = self.choices[sid]
        trials = [original]
        rows = self._check(sid, trials)
        # Q2: a window that shows none of the must-see events also earns the
        # existing single swap check; it never adds a stop.
        if (not rows[0]['setting_match'] or not rows[0]['single_location'] or
                not all(r['present'] for r in rows[0]['roles'].values()) or must_see_score(rows[0]) == 0):
            alternatives = self._alternatives(sid)[:2]
            if alternatives:
                self._check(sid, alternatives)
                trials += alternatives
        self._choose_scene(sid, trials)

    def _choose_scene(self, sid, trials):
        healthy = [c for c in trials if self.checks[(sid, c['window_id'])]['setting_match'] and
                   self.checks[(sid, c['window_id'])]['single_location'] and
                   all(r['present'] for r in self.checks[(sid, c['window_id'])]['roles'].values())]
        if healthy:
            # Q2: among healthy windows prefer the one that shows more must-see
            # events; ties keep the model's original order.
            best = max(must_see_score(self.checks[(sid, c['window_id'])]) or 0 for c in healthy)
            self.choices[sid] = next(c for c in healthy if (must_see_score(self.checks[(sid, c['window_id'])]) or 0) == best)
        else:
            # A setting mismatch counts like one missing role, then use retrieval score.
            chosen = max(trials, key=lambda c: (sum(r['present'] for r in
                self.checks[(sid, c['window_id'])]['roles'].values()) +
                int(self.checks[(sid, c['window_id'])]['setting_match']), self._candidate(c)['score']))
            self.choices[sid] = chosen
            check = self.checks[(sid, chosen['window_id'])]
            missing = [role for role, r in check['roles'].items() if not r['present']]
            self.mismatches.append(dict(scene_id=sid, window_id=chosen['window_id'], missing_roles=missing,
                setting_match=check['setting_match'], setting_reason=check['setting_reason'],
                reason='候选均不理想，使用最接近窗口继续；缺失角色不作为可见事实；' + check['setting_reason'],
                tried_window_ids=[c['window_id'] for c in trials]))
        verdicts = self.checks[(sid, self.choices[sid]['window_id'])].get('must_see') or {}
        unmet = [k for k, v in verdicts.items() if v['status'] in ('partial', 'unmet')]
        unknown = [k for k, v in verdicts.items() if v['status'] == 'unknown']
        if unmet or unknown:
            self.mismatches.append(dict(scene_id=sid, window_id=self.choices[sid]['window_id'], unmet_must_see=unmet,
                unknown_must_see=unknown,
                reason='所选窗口未完整演出必须看见的事件；台词按实际画面改编，画面不支持的内容不作为可见事实',
                tried_window_ids=[c['window_id'] for c in trials]))

    def run(self, needs=None):
        if getattr(self, "discovery", None) is not None:
            return self.discovery.run()
        if not self.scenes:
            if self.reference_source is not None:
                from .reference_dialogue import read_turns, transcribe
                self.lines = transcribe(self)
                self.lines = read_turns(self)
            stage = 'material_scene_split_v3' if self.reference_source is not None else 'material_scene_split'
            if self.reference_media is None and self.reference_source is not None:
                from .reference_dialogue import reference_proxy
                self.reference_media = reference_proxy(self, 0, self.duration,
                    'scene_reference_dialogue', stage)['path']
            def validate(value):
                from .reference_units import freeze_partition
                prepared = freeze_partition(value, self.lines, self.duration, require_verbatim=self.joint_cut) \
                    if self.reference_source is not None else value
                validate_split(prepared, self.lines, self.duration, self.cast, require_dialogue=self.reference_media is not None,
                               max_roles=None if self.reference_source is not None else 4)
                if self.joint_cut and any(not r.get('must_preserve') or type(r.get('visual_anchor')) is not bool
                                          for r in value.get('lines', [])):
                    raise ValueError('每条参考行须独立记录must_preserve及visual_anchor布尔值')
            prompt = (partition_prompt if self.reference_source is not None else split_prompt)(
                self.reference, self.lines, self.duration)
            split = self._call(stage, prompt,
                               validate, self.reference_media)
            if self.reference_source is not None:
                from .reference_units import freeze_partition
                self.state.set_artifact('scene_partition_raw', split)
                split = freeze_partition(split, self.lines, self.duration, require_verbatim=self.joint_cut)
            self.scenes = validate_split(split, self.lines, self.duration, self.cast,
                                        max_roles=None if self.reference_source is not None else 4)
            for scene in self.scenes:
                normalise_must_see(scene)
            located = fold_same_location(self.scenes)
            if located:
                self.state.set_artifact('scene_location_folds', dict(policy='fold_same_location_v1', folds=located))
            folded = fold_short_tails(self.scenes, self.duration)
            if folded:
                self.state.set_artifact('scene_tail_folds', dict(policy='fold_short_speaking_tails_v1', folds=folded))
            self.state.set_artifact('scene_split', split)
            if 'lines' in split:
                by_id = {row['line_id']: row for row in split['lines']}
                self.lines = [dict(line, asr_text=line.get('asr_text', line['text']), **{k: by_id[line['line_id']][k]
                              for k in ('role', 'text', 'function', 'must_preserve', 'verbatim_terms', 'visual_anchor')
                              if k in by_id[line['line_id']]}) for line in self.lines]
                grouped = []
                for scene in self.scenes:
                    if (grouped and grouped[-1]['dialogue_id'] == scene['dialogue_id'] and
                            bool(grouped[-1]['line_ids']) == bool(scene['line_ids'])):
                        previous = grouped[-1]
                        previous['end_s'] = scene['end_s']
                        previous['line_ids'] += scene['line_ids']
                        previous['roles'] = list(dict.fromkeys(previous['roles'] + scene['roles']))
                        previous['template'] += '；' + scene['template']
                        previous['must_see'] = (previous.get('must_see', []) + scene.get('must_see', []))[:MUST_SEE_LIMIT]
                        previous['reference_objects'] = list(dict.fromkeys(
                            previous.get('reference_objects', []) + scene.get('reference_objects', [])))[:12]
                    else:
                        grouped.append(deepcopy(scene))
                self.scenes = grouped
            if self.joint_cut:
                from .scene_coverage import separate_voice_roles
                separate_voice_roles(self.scenes, self.lines)
            if self.config.get("material_discovery") == "reference_story_v1":
                from .story_dialogue import StoryDialogue
                self.discovery = StoryDialogue(self)
                return self.discovery.run()
            if self.config.get("material_discovery") == "action_dialogue_v1":
                from .action_dialogue import ActionDialogue
                self.discovery = ActionDialogue(self)
                return self.discovery.run()
            if self.config.get("material_discovery") == "continuous_dialogue_v1":
                from .continuous_dialogue import ContinuousDialogue
                self.discovery = ContinuousDialogue(self)
                return self.discovery.run()
            if self.config.get("material_discovery") == "self_directed_v1":
                from .autonomous_material import Discovery
                self.discovery = Discovery(self)
                return self.discovery.run()
            for scene in self.scenes:
                report = rank_windows(self.index, scene, cast=self.cast, record_filter=self._allowed_record,
                                      text_embedder=self.text, clip=self.image, speech_hints=self.speech_hints)
                self.candidates[scene['scene_id']] = report['windows']
                self.state.set_artifact('scene_candidates_' + str(self.scenes.index(scene)+1), report)
                if not report['windows']:
                    raise LibraryStopped('scene_route:index_has_no_eligible_window:' + scene['scene_id'])
            if self._substitutes():
                for scene in self.scenes:
                    report = rank_windows(self.index, scene, cast=self.cast, record_filter=self._allowed_record,
                                          text_embedder=self.text, clip=self.image, speech_hints=self.speech_hints)
                    if report['windows']:
                        self.candidates[scene['scene_id']] = report['windows']
                        self.state.set_artifact('scene_candidates_' + str(self.scenes.index(scene)+1), report)
            selection_scenes = [s for s in self.scenes if self._shared_previous(s['scene_id']) is None]
            if self.joint_cut:
                ranked = {sid: [c['window_id'] for c in rows] for sid, rows in self.candidates.items()}
                pool = shared_candidate_pool(self.candidates)
                scope = '所有场景均可选择候选池内任一窗口；允许共用同一连续事件，后续会分配不重复镜头'
                # Instructions are about 3k characters; scenes and scope are sent in full.
                fixed = 3000 + len(_json(dict(scenes=selection_scenes, candidate_scope=scope)))
                summaries, budget = fit_selection_candidates([candidate_summary(c, self.cast) for c in pool], fixed,
                                                             ranked=ranked)
                self.state.set_artifact('scene_select_prompt_budget', budget)
                selection_input = dict(scenes=selection_scenes, candidates=summaries, candidate_scope=scope)
                for index, scene in enumerate(self.scenes, 1):
                    self.state.set_artifact('scene_candidates_' + str(index),
                        dict(windows=self.candidates[scene['scene_id']], shared_cast_pool=True))
            else:
                selection_input = dict(scenes=selection_scenes,
                    candidates={sid: [candidate_summary(c, self.cast) for c in rows] for sid, rows in self.candidates.items()})
            has_substitutes = any(substitute_query(s) for s in selection_scenes)
            prompt = ('为每个参考场景选一个候选window_id，并把故事角色绑定为该窗口记录的role_id。'
                '只为scenes.roles中的出镜角色选演员；voice_only_roles只保留画外声音，不绑定画面人物。'
                '选择能承载整场故事的素材：双方讲话、实际听者反应、相关动作都有足够不重复区间；不以静态或可循环作为优先条件。'
                '场景的must_see是观众必须亲眼看到的抽象事件：在角色与地点相容的候选中，优先选实际能演出这些事件的窗口' +
                ('（物件可按replaceable换成画面里真有的东西；给了function和substitutes时，替代画面只用来演出该事件，'
                 '不放宽地点要求），evidence.must_see_match与substitute_match是检索估计，仍以window_summary与records为准；'
                 if has_substitutes else
                 '（物件可按replaceable换成画面里真有的东西），evidence.must_see_match是检索估计，仍以window_summary与records为准；') +
                '没有候选能演出时照常按对白与互动选择。' +
                '逐窗先看window_summary，按以下顺序选：①对话场景必须选真正的对话窗口：两人以上在同一连续地点面对面讲话；'
                '以打斗、追逐、潜行、表演为主或多个地点交叉剪辑的窗口不选，地点再像也不选；'
                '②在真正的对话窗口中，同类地点（室内/室外、店铺/住宅/野外等）优先；③都没有同类地点时，选最好的对话窗口，靠台词讲清内容。' +
                '以动作为主、大量打斗的窗口不是对话场景，少数面对面特写不能替代整窗匹配。' + FUNCTIONAL_SETTING_GUIDE +
                '跨场景同一故事角色必须用同一演员：各窗口roles中actor相同即同一实际演员（演员N是未具名演员的代号，只供辨认），'
                'role_bindings的值必须填该窗口roles的键（如A、B），不能填actor或演员N；role_id只在本窗口内有效；连续同setting场景由程序沿用前窗，只需选择各组首场景。'
                '先确认整段故事的所有角色能一致选角，再填写各场景；同一实际演员不能扮演两个不同故事角色。'
                '选角依据故事中的对话关系和实际互动可懂度，不能只按讲话素材多少分配角色。'
                '该候选内的角色对应经实际身份确认后固定，素材不足须另选候选，后续程序不会为凑时长交换演员。'
                '对话的双方要让观众一眼分清谁是谁：优先选物种、毛色、体型或服饰明显不同的两个人物；'
                '同物种且外形相近时（如两只熊猫），只有该窗口说话镜头多为看得清脸的近景才选，否则换外形差别大的组合或窗口。'
                '只返回JSON {"scenes":[{"scene_id":"scene_1","window_id":"候选ID",'
                '"role_bindings":{"顾客":"A","店员":"B"}}]}。\n' + _json(selection_input))
            def validate(value):
                validate_selection(value, selection_scenes, self.candidates, self.cast)
            selected = self._call('material_scene_select', prompt, validate)
            self.choices = {c['scene_id']: c for c in validate_selection(selected, selection_scenes, self.candidates, self.cast)}
            for scene in self.scenes:
                self._share_window(scene['scene_id'])
            for scene in self.scenes:
                sid = scene['scene_id']
                if self._shared_previous(sid):
                    self._share_window(sid)
                    self._check(sid, [self.choices[sid]])
                else:
                    self._confirm_scene(sid)
        elif needs:
            for need in needs:
                sid = need.get('scene_id', need.get('beat_id'))
                if self.joint_cut and (next(s for s in self.scenes if s['scene_id'] == sid)['line_ids'] or
                        (sid, self.choices[sid]['window_id']) in self.failed_coverage_windows):
                    continue  # Dialogue and visual coverage retries use the current confirmed cast.
                if sid not in self.choices or self.review_swaps.get(sid, 0) or self._shared_previous(sid):
                    continue
                self.review_swaps[sid] = 1
                alternatives = self._alternatives(sid)[:2]
                if alternatives:
                    unchecked = [c for c in alternatives if (sid, c['window_id']) not in self.checks]
                    if unchecked:
                        self._check(sid, unchecked)
                    confirmed = [c for c in alternatives if (sid, c['window_id']) in self.checks]
                    # The review gets at most one replacement per scene. If the
                    # visual budget is spent, keep the indexed scene and disclose it.
                    if confirmed:
                        self._choose_scene(sid, confirmed)
                    else:
                        self.choices[sid] = alternatives[0]
                        self.mismatches.append(dict(scene_id=sid, window_id=alternatives[0]['window_id'],
                            reason='复看预算已用完，最接近备选仅有索引，地点与角色匹配未复核'))
                self.mismatches.append(dict(scene_id=sid, reason='静音审核身份问题；已尝试至多一次局部换窗',
                    window_id=self.choices[sid]['window_id']))
            for scene in self.scenes:
                sid = scene['scene_id']
                if self._share_window(sid):
                    self._check(sid, [self.choices[sid]], slot_driven=True)
            self.round += 1
        windows, self.lock = self._lock()
        speech_slots.build_speech_slots(self, self.lock, reactions=False)
        changed = self._joint_cast() if self.joint_cut else False
        for scene in ([] if changed else self.lock['scenes']):
            rebound, notes = self._rebind_speech(scene)
            changed |= rebound
            self.mismatches.extend(note for note in notes if note not in self.mismatches)
        speech_changed = self._choose_speech_windows()
        visual_changed = self._refresh_visual_cast()
        if speech_changed or visual_changed or changed:
            windows, self.lock = self._lock()
        speech_slots.build_speech_slots(self, self.lock, reactions=self.reactions)
        if self.joint_cut:
            from .scene_coverage import build_scene_slots as build_coverage, sync_cast_bindings
            self.lock['editing_policy'] = 'joint_av_v1'
            anchors = {role: {k: binding[k] for k in ('actor', 'appearance')}
                       for s in self.lock['scenes'] if s['reference_lines']
                       for role, binding in s['role_bindings'].items()}
            for index, scene in enumerate(self.lock['scenes'], 1):
                if not scene['reference_lines']:
                    scene['identity_anchors'] = {role: anchors[role] for role in scene['role_bindings'] if role in anchors}
                    build_coverage(self, scene, index)
            sync_cast_bindings(self.lock)
            from .scene_coverage import bind_offscreen_voices
            bind_offscreen_voices(self.lock)
        self.lock['mismatches'] = deepcopy(self.mismatches)
        self.state.set_artifact('scene_material_lock', self.lock)
        write_json(Path(self.state.output) / 'material_lock.json', self.lock)
        if self.joint_cut:
            write_json(Path(self.state.output) / 'scene_coverage.json', self.slot_reports)
        write_json(Path(self.state.output) / 'scene_mismatches.json', self.mismatches)
        return windows, self.lock

    def _refresh_visual_cast(self):
        """Silent scenes follow the usable speaking cast, rather than freezing it early."""
        spoken = [s for s in self.lock['scenes'] if s['reference_lines']]
        if any(({reference_line_role(r, s['roles']) for r in s['reference_lines'] if r.get('on_screen', True)} - {None}) -
               {p.get('role') for p in s['speech_slots']} for s in spoken):
            return False
        actors = {role: binding for scene in spoken for role, binding in scene['role_bindings'].items()}
        from .scene_coverage import cast_options, same_actor
        claimed = [(role, binding) for scene in spoken for role, binding in scene['role_bindings'].items()]
        def failed_cast(sid, wid, bindings):
            return any(set(old) == set(bindings) and all(same_actor(bindings[r], b) for r, b in old.items())
                for old in getattr(self, 'failed_visual_casts', {}).get((sid, wid), []))
        changed = False
        for scene in self.scenes:
            if scene['line_ids']:
                continue
            sid = scene['scene_id']
            choice = self.choices[sid]
            required = {role: actors[role] for role in scene['roles'] if role in actors}
            locked = next(s for s in self.lock['scenes'] if s['scene_id'] == sid)
            missing = set(scene['roles']) - set(locked['role_bindings'])
            current = locked['role_bindings']
            compatible = all(all(same_actor(binding, used) if other == role else not same_actor(binding, used)
                                 for other, used in claimed) for role, binding in current.items())
            rejected = ((sid, choice['window_id']) in self.failed_coverage_windows or
                        failed_cast(sid, choice['window_id'], current))
            if not rejected and not missing and compatible and all(same_actor(current[role], actor) for role, actor in required.items()):
                claimed.extend(current.items())
                continue
            candidates = self.candidates[sid]
            # Reuse ranked metadata, without manually selecting an actor or a timestamp.
            full = rank_windows(self.index, scene, cast=self.cast, record_filter=self._allowed_record,
                text_embedder=self.text, clip=self.image, limit=len(self.index.records_by_window))['windows']
            known = {c['window_id'] for c in candidates}
            candidates.extend(c for c in full if c['window_id'] not in known)
            found = False
            for candidate in candidates:
                if ((sid, candidate['window_id']) in self.failed_coverage_windows and
                        not getattr(self, 'failed_visual_casts', {}).get((sid, candidate['window_id']))):
                    continue
                trial = dict(scene_id=sid, window_id=candidate['window_id'])
                roles = self._roles(trial)
                options = cast_options(scene['roles'], roles, claimed)
                bindings = {role: next((rid for rid in options[role] if same_actor(roles[rid], actor)), None)
                            for role, actor in required.items()}
                if not all(bindings.values()) or len(set(bindings.values())) != len(bindings):
                    continue
                for role in sorted(scene['roles'], key=lambda r: len(options[r])):
                    if role in bindings:
                        continue
                    used = {roles[rid]['actor'] for rid in bindings.values()}
                    available = [rid for rid in options[role] if roles[rid]['actor'] not in used]
                    previous = choice['role_bindings'].get(role)
                    if available:
                        bindings[role] = previous if previous in available else available[0]
                if set(bindings) != set(scene['roles']):
                    continue
                if failed_cast(sid, candidate['window_id'], {role: roles[rid] for role, rid in bindings.items()}):
                    continue
                trial['role_bindings'] = bindings
                if not self._check(sid, [trial], slot_driven=True):
                    break
                check = self.checks[(sid, trial['window_id'])]
                if not all(r['present'] for r in check['roles'].values()):
                    continue
                # A visual check may correct stale index names. Reserve the observed identity.
                roles = self._roles(trial)
                actual_options = cast_options(scene['roles'], roles, claimed)
                if (any(rid not in actual_options[role] for role, rid in bindings.items()) or
                        len({roles[rid]['actor'] for rid in bindings.values()}) != len(bindings) or
                        failed_cast(sid, trial['window_id'], {role: roles[rid] for role, rid in bindings.items()})):
                    continue
                self.choices[sid] = trial
                self.failed_coverage_windows.discard((sid, trial['window_id']))
                locked['role_bindings'] = {role: dict(roles[rid], role_id=rid, window_id=trial['window_id'])
                                           for role, rid in bindings.items()}
                claimed.extend(locked['role_bindings'].items())
                changed = found = True
                break
            if not found:
                self.state.set_artifact('scene_visual_cast_failure', dict(scene_id=sid,
                    required=required, chosen={s['scene_id']: s['role_bindings'] for s in self.lock['scenes']},
                    checked_windows=[wid for scene_id, wid in self.checks if scene_id == sid],
                    rejected_windows=[wid for scene_id, wid in self.failed_coverage_windows if scene_id == sid]))
                raise LibraryStopped('scene_route:visual_cast_not_found:' + sid)
        return changed

    def _rebind_speech(self, scene, *, fixed=None):
        if not scene['reference_lines']:
            return False, []
        sid = scene['scene_id']
        choice = self.choices[sid]
        roles = self._roles(choice)
        by_role = {}
        for slot in scene['speech_slots']:
            by_role.setdefault(slot['role_id'], []).append(slot)
        available = sorted(by_role, key=lambda rid: (
            -sum(p['source_end_s']-p['source_start_s'] for p in by_role[rid]), rid))
        protected = self._consistent_actors(sid)
        preferred, actor_genders = {}, {}
        claimed = [(story, binding) for other in self.lock['scenes'] if other['scene_id'] != sid and other['reference_lines']
                   for story, binding in other['role_bindings'].items()]
        if self.joint_cut:
            from .scene_coverage import cast_for_dialogue
            preferred = cast_for_dialogue(scene, roles, protected, claimed)
            actor_genders = {binding['role_id']: scene['picture']['roles'].get(story, {}).get('gender', 'unclear')
                             for story, binding in scene['role_bindings'].items()}
        bound, conflicts = {}, []
        changed, notes = False, []
        # Reserve existing speakers before assigning a silent role to an unused speaker.
        # Fixed and preferred casts first: an uncast supporting role must not take the
        # speaking actor the cast solver gave to the lead (21, 8b67dc78).
        for story in sorted(scene['roles'], key=lambda s: (fixed is None or s not in fixed, s not in preferred,
                                                           choice['role_bindings'].get(s) not in by_role)):
            previous = choice['role_bindings'].get(story)
            candidates = list(dict.fromkeys(([preferred[story]] if story in preferred else []) +
                ([previous] if previous in roles else []) + available))
            if fixed is not None and story in fixed:
                candidates = [fixed[story]]
            elif fixed is not None:
                # The story-wide solver left this supporting speaker uncast (it already
                # prefers casting any free speaker); a per-scene rebind here could give it
                # different actors in different scenes (GLM review). Voiced off screen.
                candidates = []
            if story in scene.get('cast_constraints', {}):
                candidates = [rid for rid in candidates if rid in scene['cast_constraints'][story]]
            candidates.sort(key=lambda rid: rid not in by_role)
            binding = None
            for rid in candidates:
                observed = roles[rid]
                if story in protected and observed['actor'] != protected[story]:
                    continue
                trial = dict(window_id=choice['window_id'], role_id=rid, entity_id=observed.get('entity_id'),
                             actor=observed['actor'], appearance=observed['appearance'],
                             gender=scene['picture']['roles'].get(story, {}).get('gender', 'unclear'))
                from .scene_coverage import same_actor
                if any(other != story and (speech_slots.shares_character(trial, used) or
                       (self.joint_cut and same_actor(trial, used)))
                       for other, used in claimed):
                    if story not in conflicts:
                        conflicts.append(story)
                    continue
                binding = trial
                break
            if binding is None:
                changed |= story in scene['role_bindings']
                choice['role_bindings'].pop(story, None)   # its old actor now plays another role
                continue
            bound[story] = binding
            claimed.append((story, binding))
            if story in conflicts and binding['role_id'] in by_role:
                conflicts.remove(story)
            rid = binding['role_id']
            choice['role_bindings'][story] = rid
            if rid not in by_role:
                continue
            speaking = sorted(by_role[rid], key=lambda p: -(p['source_end_s']-p['source_start_s']))
            gender = next((p['gender'] for p in speaking if p['gender'] != 'unclear'),
                          actor_genders.get(rid, binding['gender'] if rid == previous else 'unclear'))
            if rid != previous or gender != 'unclear':
                changed |= binding['gender'] != gender
                binding['gender'] = gender
                scene['picture']['roles'].setdefault(story, {})['gender'] = gender
            if rid != previous or story not in scene['role_bindings'] or speaking:
                changed |= rid != previous or scene['role_bindings'].get(story) != binding
                ranges = [[p['source_start_s'], p['source_end_s']] for p in speaking]
                scene['picture']['roles'][story] = dict(present=True, gender=gender,
                    visible_ranges=ranges, speech_ranges=ranges)
                scene['speech_ranges'][story] = [{k: p[k] for k in
                    ('source_sha256', 'source_start_s', 'source_end_s')} for p in speaking]
                notes.append(dict(scene_id=sid, window_id=choice['window_id'], reason='rebound_by_speech',
                    role=story, from_role_id=previous, role_id=rid, actor=binding['actor'], gender=gender))
        scene.update(role_bindings=bound, character_conflicts=conflicts)
        speech_slots.bind_slot_roles(scene, scene['speech_slots'])
        if changed:
            self.checks[(sid, choice['window_id'])]['roles'] = deepcopy(scene['picture']['roles'])
        return changed, notes

    def _choose_speech_windows(self):
        if self.joint_cut:
            return self._choose_coverage_windows()
        changed = False
        for index, scene in enumerate(self.lock['scenes'], 1):
            sid = scene['scene_id']
            if self._share_window(sid):
                self._check(sid, [self.choices[sid]], slot_driven=True)
                _, shared_lock = self._lock()
                scene = shared_lock['scenes'][index-1]
                speech_slots.build_scene_slots(self, scene, index, reactions=False)
                self._rebind_speech(scene)
                self.lock['scenes'][index-1] = scene
                changed = True
            if not scene['reference_lines']:
                continue
            required = {reference_line_role(line, scene['roles']) for line in scene['reference_lines']} - {None}
            required = required or set(scene['roles'])
            missing = required - {p.get('role') for p in scene['speech_slots'] if p.get('loop_range')}
            if not missing and scene['picture'].get('single_location'):
                continue
            original = self.choices[sid]
            trials = [(original, scene, [])]
            if sid not in self.slot_swaps and not self._shared_previous(sid):
                self.slot_swaps.add(sid)
                def speaking_roles(choice):
                    records = self._candidate(choice)['records']
                    return sum(any(material_index.has_dialogue_hint(r.get('speech')) and
                        any(p['role_id'] == rid for p in r.get('roles', []))
                        for r in records) for rid in window_roles(self._candidate(choice), self.cast))
                alternatives = sorted(self._alternatives(sid, slot_driven=True), key=speaking_roles, reverse=True)[:8]
                for choice in alternatives:
                    if not self._check(sid, [choice], slot_driven=True):
                        break
                    check = self.checks[(sid, choice['window_id'])]
                    self.choices[sid] = choice
                    _, trial_lock = self._lock()
                    trial = trial_lock['scenes'][index-1]
                    speech_slots.build_scene_slots(self, trial, index, reactions=False)
                    _, notes = self._rebind_speech(trial)
                    trials.append((choice, trial, notes))
                    if required <= {p.get('role') for p in trial['speech_slots'] if p.get('loop_range')}:
                        break
            choice, chosen, notes = max(trials, key=lambda pair: (
                pair[1]['picture'].get('single_location', False),
                len(required & {p.get('role') for p in pair[1]['speech_slots'] if p.get('loop_range')}),
                len(required & {p.get('role') for p in pair[1]['speech_slots']}),
                pair[1]['picture'].get('setting_match', False),
                len(pair[1]['speech_slots'])))
            self.choices[sid] = choice
            self.lock['scenes'][index-1] = chosen
            self.mismatches.extend(note for note in notes if note not in self.mismatches)
            changed |= choice != original
            missing = sorted(set(chosen['roles']) - {p.get('role') for p in chosen['speech_slots']})
            if missing:
                shared = sorted(set(missing) & set(chosen.get('character_conflicts', [])))
                if shared:
                    mismatch = dict(scene_id=sid, window_id=choice['window_id'],
                        reason='role_shares_character', missing_roles=shared)
                    if mismatch not in self.mismatches:
                        self.mismatches.append(mismatch)
                mismatch = dict(scene_id=sid, window_id=choice['window_id'],
                    reason='role_without_speech_slot', missing_roles=missing)
                if mismatch not in self.mismatches:
                    self.mismatches.append(mismatch)
        return changed

    def _choose_coverage_windows(self):
        from .scene_coverage import capacity, sufficient, screen_roles, conversation_context
        changed = False
        for index in range(1, len(self.lock['scenes'])+1):
            scene = self.lock['scenes'][index-1]
            sid = scene['scene_id']
            failed = (sid, self.choices[sid]['window_id']) in self.failed_coverage_windows
            if not scene['reference_lines'] or (sufficient(scene) and not failed):
                continue
            original_choices, original_lock = deepcopy(self.choices), deepcopy(self.lock)
            from .scene_coverage import cast_for_story
            spoken = [s for s in self.lock['scenes'] if s['reference_lines']]
            actors = {s['scene_id']: self._roles(self.choices[s['scene_id']]) for s in spoken}
            complete = bool(cast_for_story(spoken, actors, feasible=self._visual_cast_feasibility(spoken, actors)))
            trials = [] if failed else [(complete, original_choices, original_lock)]
            if not self.choices[sid].get('context_range'):
                source = self.sources[scene['allowed_ranges'][0]['source_sha256']]
                bounds = conversation_context(scene, source, self.cast.get('stylized_ranges', {}).get(source['sha256'], []))
                original_bounds = [scene['allowed_ranges'][0][k] for k in ('source_start_s', 'source_end_s')]
                if bounds != original_bounds:
                    self.choices[sid]['context_range'] = bounds
                    _, trial_lock = self._lock()
                    trial = trial_lock['scenes'][index-1]
                    speech_slots.build_scene_slots(self, trial, index, reactions=False)
                    self.lock['scenes'][index-1] = trial
                    complete = self._joint_cast()
                    trial = self.lock['scenes'][index-1]
                    if not complete:
                        self._rebind_speech(trial)
                    trials.append((complete, deepcopy(self.choices), deepcopy(self.lock)))
                    if complete and sufficient(trial):
                        changed = True
                        continue
                    self.choices, self.lock = deepcopy(original_choices), deepcopy(original_lock)
            if sid not in self.slot_swaps and not self._shared_previous(sid):
                self.slot_swaps.add(sid)
                for choice in self._coverage_alternatives(scene, index):
                    self.choices, self.lock = deepcopy(original_choices), deepcopy(original_lock)
                    if not self._check(sid, [choice], slot_driven=True):
                        break
                    # A sparse sample of a mixed index window is provisional.
                    # Grounded continuous groups below establish the usable event.
                    self.choices[sid] = choice
                    _, trial_lock = self._lock()
                    trial = trial_lock['scenes'][index-1]
                    speech_slots.build_scene_slots(self, trial, index, reactions=False)
                    self.lock['scenes'][index-1] = trial
                    complete = self._joint_cast()
                    trial = self.lock['scenes'][index-1]
                    if not complete:
                        self._rebind_speech(trial)
                    trials.append((complete, deepcopy(self.choices), deepcopy(self.lock)))
                    if complete and sufficient(trial):
                        break
                    self.failed_coverage_windows.add((sid, choice['window_id']))
            if not trials:
                trials = [(False, original_choices, original_lock)]
            required = screen_roles(scene)
            def score(entry):
                selected = entry[2]['scenes'][index-1]
                return (entry[0], sufficient(selected),
                    len(required & {s.get('role') for s in selected['speech_slots']}),
                    capacity(selected.get('coverage_shots', [])))
            _, self.choices, self.lock = max(trials, key=score)
            changed |= self.choices != original_choices
        return changed

    def _lock(self):
        from .pipeline import _received_library_window
        windows, scenes, bindings, claimed = {}, [], {}, []
        for scene in self.scenes:
            sid = scene['scene_id']
            choice = self.choices[sid]
            candidate = self._candidate(choice)
            first = candidate['records'][0]
            source = self.sources[first['source_sha256']]
            wid = choice['window_id']
            if wid not in windows:
                window = (self.window_loader(candidate, source) if self.window_loader else
                    _received_library_window(self.store.get(first['observation_id']), self.root, source=source,
                        revision_ids=self.revisions, normalize=contracts.creative_time_normalization(self.config)))
                windows[wid] = deepcopy(window)
            window = windows[wid]
            low, high = content_range_for(source) or (0, source['duration_s'])
            start, end = max(low, window['source_start_s']), min(high, window['source_end_s'])
            if choice.get('context_range'):
                start, end = choice['context_range']
                context = dict(source_start_s=start, source_end_s=end,
                    policy='fresh_continuous_coverage', indexed_range=[window['source_start_s'], window['source_end_s']])
                if context not in window.setdefault('scene_contexts', []):
                    window['scene_contexts'].append(context)
                window['source_subtitle_masks'] = list({json_sha(m):m for m in
                    [*window.get('source_subtitle_masks', []), *self.context_masks(source['sha256'], start, end)]}.values())
            check = self.checks.get((sid, wid), dict(roles={}, scene='仅索引记录，复看预算已用完'))
            bound = self._bound(choice)
            conflicts = []
            for story, observed in list(bound.items()):
                binding = dict(observed, window_id=wid)
                if any(other != story and speech_slots.shares_character(binding, used)
                       for other, used in claimed):
                    conflicts.append(story)
                    del bound[story]
                else:
                    claimed.append((story, binding))
            for story, observed in bound.items():
                binding = dict(window_id=wid, role_id=observed['role_id'])
                entity = observed.get('entity_id')
                info = bindings.setdefault(story, dict(actor=observed['actor'], entity_ids=[], bindings=[], entity_bindings={}))
                gender = check['roles'].get(story, {}).get('gender', 'unclear')
                # Conflicting answers across scenes leave the voice to the planner.
                known = info.get('gender', gender)
                info['gender'] = gender if known == 'unclear' else known if gender in (known, 'unclear') else 'unclear'
                if binding not in info['bindings']:
                    info['bindings'].append(binding)
                if entity and entity not in info['entity_ids']:
                    info['entity_ids'].append(entity)
                if binding not in info['entity_bindings'].setdefault(entity, []):
                    info['entity_bindings'][entity].append(binding)
                finding = check['roles'].get(story, {})
                if finding.get('present'):
                    for role in window['observation']['roles']:
                        if role['role_id'] == observed['role_id']:
                            role['identity_confirmed'] = True
                    for a, b in finding['visible_ranges']:
                        window['observation']['events'].append(dict(local_start_s=a-window['source_start_s'],
                            local_end_s=b-window['source_start_s'], role_ids=[observed['role_id']],
                            observed_fact=check['scene']))
            scenes.append(dict(scene, window_ids=[wid], character_conflicts=conflicts,
                allowed_ranges=[dict(window_id=wid,
                source_sha256=source['sha256'], source_start_s=start, source_end_s=end)],
                role_bindings={story: dict(window_id=wid, role_id=r['role_id'], entity_id=r.get('entity_id'), actor=r['actor'], appearance=r['appearance'],
                    gender=check['roles'].get(story, {}).get('gender', 'unclear')) for story, r in bound.items()},
                speech_ranges={story: [dict(source_sha256=source['sha256'], source_start_s=a, source_end_s=b)
                    for a, b in check['roles'].get(story, {}).get('speech_ranges', [])] for story in scene['roles']},
                picture=deepcopy(check), reference_lines=[deepcopy(r) for r in self.lines if r['line_id'] in scene['line_ids']]))
        for window in windows.values():
            # Existing received observation saw the complete window. This derived
            # task range allows looping/cutaways; per-role event overlap remains
            # enforced by the unchanged shared contract.
            source = self.sources[window['source_sha256']]
            low, high = content_range_for(source) or (0, source['duration_s'])
            observation = window['observation']
            event_roles = {r for e in observation['events'] for r in e['role_ids']}
            roles = [r['role_id'] for r in observation['roles'] if r.get('identity_confirmed') and r['role_id'] in event_roles]
            start, end = max(low, window['source_start_s']), min(high, window['source_end_s'])
            indices = [i for i, e in enumerate(observation['events']) if
                e['local_start_s'] < end-window['source_start_s'] and start-window['source_start_s'] < e['local_end_s']]
            event_roles = {r for i in indices for r in observation['events'][i]['role_ids']}
            observation['usable_ranges'].append(dict(local_in_s=start-window['source_start_s'],
                local_out_s=end-window['source_start_s'], role_ids=[r for r in roles if r in event_roles],
                event_indices=indices, camera='完整已看场景', continuity_notes='scene_v1完整已看窗口，角色仍需事件重叠'))
            window['scene_route_evidence'] = dict(policy='scene_v1', index_sha256=json_sha(self.index.records),
                checks=[s['picture'] for s in scenes if window['window_id'] in s['window_ids']])
        lock = dict(scene_mode='scene_v1', scenes=scenes, window_ids=list(windows), cast_bindings=bindings,
                    cast={r: b['actor'] for r, b in bindings.items()}, edit_max_s=self.edit_max_s,
                    reference_duration_s=self.duration, mismatches=deepcopy(self.mismatches))
        return list(windows.values()), lock


def plan_prompt(prompt, context):
    lock = context['material_lock']
    return prompt + ('\nscene_v1素材合同：每个segment的scene_id与slot_id使用scenes.scene_id；'
        '片段只在所属场景window_ids及allowed_ranges内，可使用完整窗口、循环复用，禁止连续完全相同源区间。'
        'speed=0.8–1.0，可定格freeze_tail_s=0–10。总时长不得超过edit_max_s。'
        '参考reference_lines只保留结构、轮次、节奏、包袱与长短比例；每句具体物件、地点、商品必须在本场景画面可见（描述见锁内picture），'
        '未见的参考物品必须替换为画面实际食物或物件，如冰激凌/豆浆/吸管改为可见的面条/碗/锅。'
        '每句dialogue可放在本场景任意镜头上，speaker须复制本场景role_bindings中的{window_id,role_id}。'
        'cast用story_role，entity_id和bindings复制cast_bindings对应entity_bindings；每个entity独立cast_id。'
        '音色性别：Ethan、Moon、Kai为男声，Cherry、Serena为女声；每个角色按cast_bindings中的gender（画面扮演者看上去的性别）'
        '选同性别音色，不按参考说话人的性别；gender=unclear时自选，不同角色尽量用不同音色。'
        'identity_anchor_segment_id、exchange_id、audio_relation均由程序填写，无须模型编造。'
        '优先交替说话人与听者；speech_ranges未覆盖的镜头自动标画外音，不能声称未展示的动作/结果。'
        '仅以scene_v1合同覆盖通用的禁止重复素材提示，不增加节拍或条件表。\n' + _json(dict(material_lock=lock)))


def _normalise_cast(plan, lock):
    """scn28: cast rows omitted story_role and carried extra keys in bindings."""
    from .plan_normalisation import _set
    owners = {entity: role for role, b in lock['cast_bindings'].items() for entity in b.get('entity_bindings', {})}
    for index, character in enumerate(plan.get('cast', [])):
        if not isinstance(character, dict):
            continue
        path = f'$.cast[{index}]'
        if character.get('story_role') is None and character.get('entity_id') in owners:
            _set(plan, character, 'story_role', owners[character['entity_id']], path + '.story_role',
                 'story_role由锁定选角中该entity_id唯一确定')
        bindings = character.get('bindings')
        if isinstance(bindings, list) and all(isinstance(b, dict) for b in bindings):
            trimmed = [{k: b[k] for k in ('window_id', 'role_id') if k in b} for b in bindings]
            if trimmed != bindings:
                _set(plan, character, 'bindings', trimmed, path + '.bindings',
                     'bindings只比较window_id/role_id，去掉多余字段')
        # s2: bindings are derived data; when the entity belongs to the locked role, copy them from the lock.
        role, entity = character.get('story_role'), character.get('entity_id')
        allowed = lock['cast_bindings'].get(role, {}).get('entity_bindings', {}).get(entity)
        if allowed and character.get('bindings') != allowed and (
                not isinstance(character.get('bindings'), list) or
                any(b not in allowed for b in character['bindings'])):
            _set(plan, character, 'bindings', allowed, path + '.bindings',
                 'bindings由锁定选角中该entity_id的出场位置确定')


def _normalise_voices(plan, lock):
    """27 s2: Po playing the customer kept the reference customer's female voice."""
    from .plan_normalisation import _set
    owners = {(b['window_id'], b['role_id']): role for scene in lock['scenes']
              for role, b in scene['role_bindings'].items()}
    lines = [(i, line) for i, line in enumerate(plan.get('audio_draft', []))
             if isinstance(line, dict) and line.get('mode') == 'dialogue' and isinstance(line.get('speaker'), dict)]
    used = {}
    for _, line in lines:
        role = owners.get((line['speaker'].get('window_id'), line['speaker'].get('role_id')))
        if role and line.get('voice') in VOICE_GENDER:
            used.setdefault(role, line['voice'])
    taken, replace = set(used.values()), {}
    for role, voice in used.items():
        want = lock['cast_bindings'].get(role, {}).get('gender')
        if want in ('male', 'female') and VOICE_GENDER[voice] != want:
            options = [v for v, g in VOICE_GENDER.items() if g == want]
            replace[role] = next((v for v in options if v not in taken), options[0])
            taken.add(replace[role])
    reason = '画面扮演者性别为{}，音色改为同性别且未被其他角色使用的{}'
    for i, line in lines:
        role = owners.get((line['speaker'].get('window_id'), line['speaker'].get('role_id')))
        if role in replace:
            _set(plan, line, 'voice', replace[role], f'$.audio_draft[{i}].voice',
                 reason.format(lock['cast_bindings'][role]['gender'], replace[role]))
    for i, character in enumerate(plan.get('cast', [])):
        if isinstance(character, dict) and character.get('story_role') in replace and 'voice' in character:
            role = character['story_role']
            _set(plan, character, 'voice', replace[role], f'$.cast[{i}].voice',
                 reason.format(lock['cast_bindings'][role]['gender'], replace[role]))


def _normalise_segment_scenes(plan, lock):
    from .plan_normalisation import _set
    scenes = lock['scenes']
    order = {s['scene_id']: i for i, s in enumerate(scenes)}
    previous = 0
    for index, segment in enumerate(plan.get('segments', [])):
        sid = segment.get('scene_id')
        if not sid:
            candidates = [s['scene_id'] for s in scenes if segment.get('window_id') in s['window_ids']]
            # A valid scene slot already supplies the ordering boundary. Otherwise
            # a shared window stays in the earliest scene at/after the last one.
            slot = segment.get('slot_id')
            sid = slot if slot in candidates and order[slot] >= previous else next(
                (s for s in candidates if len(candidates) == 1 or order[s] >= previous), None)
            if sid:
                path = f'$.segments[{index}]'
                _set(plan, segment, 'scene_id', sid, path + '.scene_id',
                     'scene_id由window_id及场景顺序推定；共享窗口不回退到前一场景')
                if not segment.get('slot_id'):
                    _set(plan, segment, 'slot_id', sid, path + '.slot_id', 'slot_id沿用推定的scene_id')
        if sid in order:
            previous = max(previous, order[sid])


def validate_plan(plan, lock, windows):
    """Only the scene design's checks; structural/media rules stay shared."""
    _normalise_segment_scenes(plan, lock)
    scenes = {s['scene_id']: s for s in lock['scenes']}
    watched = {w['window_id']: w for w in windows}
    segments = {s.get('segment_id'): s for s in plan.get('segments', [])}
    errors, previous, duration = [], None, 0.0
    for segment in plan.get('segments', []):
        sid = segment.get('scene_id', segment.get('slot_id'))
        scene = scenes.get(sid, {})
        window = watched.get(segment.get('window_id'), {})
        start, end = segment.get('source_in_s'), segment.get('source_out_s')
        if (segment.get('window_id') not in scene.get('window_ids', []) or not any(
                r['source_sha256'] == window.get('source_sha256') and all(_number(v) for v in (start, end)) and
                r['source_start_s'] <= start < end <= r['source_end_s'] for r in scene.get('allowed_ranges', []))):
            errors.append(f'{segment.get("segment_id")}: scene_id={sid}超出所选窗口；允许={_json(scene.get("allowed_ranges", list(scenes)))}')
        current = (window.get('source_sha256'), start, end)
        if current == previous:
            errors.append(f'{segment.get("segment_id")}: 禁止连续完全相同区间{current}，交替其他范围')
        previous = current
        speed, freeze = segment.get('speed', 1), segment.get('freeze_tail_s', 0)
        if all(_number(v) for v in (start, end, speed, freeze)) and .8 <= speed <= 1 and 0 <= freeze <= 10:
            duration += (end-start)/speed + freeze
        else:
            errors.append(f'{segment.get("segment_id")}: speed允许[0.8,1.0]，freeze_tail_s允许[0,10]')
    if duration > lock['edit_max_s'] + 1e-9:
        errors.append(f'总时长{duration:.3f}超限，允许≤{lock["edit_max_s"]}')
    for line in plan.get('audio_draft', []):
        if line.get('mode') != 'dialogue':
            continue
        speaker = line.get('speaker')
        for ident in line.get('segment_ids', []):
            segment = segments.get(ident, {})
            sid = segment.get('scene_id', segment.get('slot_id'))
            allowed = {role: {k: r[k] for k in ('window_id', 'role_id')}
                       for role, r in scenes.get(sid, {}).get('role_bindings', {}).items()}
            if speaker not in allowed.values():
                errors.append(f'台词{line.get("id")}: speaker={speaker}未绑定于{sid}；允许={_json(allowed)}')
    _normalise_cast(plan, lock)
    for character in plan.get('cast', []):
        role = character.get('story_role')
        binding = lock['cast_bindings'].get(role, {})
        allowed = binding.get('entity_bindings', {}).get(character.get('entity_id'), [])
        if not allowed or any(b not in allowed for b in character.get('bindings', [])):
            choices = {r: dict(entity_ids=sorted(b.get('entity_bindings', {})),
                               bindings=b.get('bindings', [])) for r, b in lock['cast_bindings'].items()}
            errors.append(f'cast {character.get("cast_id")}: story_role={role!r}、entity_id={character.get("entity_id")}'
                          f'与锁定选角不符；story_role只能是{sorted(choices)}，各角色允许={_json(choices)}')
    if errors:
        _fail('scene_plan', '请一次修复全部违规：\n' + '\n'.join(errors),
              {sid: dict(window_ids=s['window_ids'], ranges=s['allowed_ranges'], roles=s['role_bindings']) for sid, s in scenes.items()})
    _normalise_voices(plan, lock)
    normalise_dialogue(plan, lock, windows)


def normalise_dialogue(plan, lock, windows):
    from .pipeline import _normalise_material_dialogue
    from .plan_normalisation import _set
    scenes = {s['scene_id']: s for s in lock['scenes']}
    segments = {s.get('segment_id'): s for s in plan.get('segments', [])}
    for index, line in enumerate(plan.get('audio_draft', [])):
        if line.get('mode') != 'dialogue':
            continue
        relations = []
        for ident in line.get('segment_ids', []):
            segment = segments.get(ident, {})
            scene = scenes.get(segment.get('scene_id', segment.get('slot_id')), {})
            role = next((r for r, binding in scene.get('role_bindings', {}).items()
                         if line.get('speaker') == {k: binding[k] for k in ('window_id', 'role_id')}), None)
            window = next((w for w in windows if w['window_id'] == segment.get('window_id')), {})
            on_screen = any(r['source_sha256'] == window.get('source_sha256') and
                r['source_start_s'] <= segment['source_in_s'] < segment['source_out_s'] <= r['source_end_s']
                for r in scene.get('speech_ranges', {}).get(role, []))
            relations.append(dict(segment_id=ident, relation='on_screen' if on_screen else 'offscreen'))
        path = f'$.audio_draft[{index}]'
        _set(plan, line, 'audio_relation', relations, path+'.audio_relation', 'scene_role_speech_ranges')
        if any(r['relation'] == 'offscreen' for r in relations) and line.get('picture_relation') == 'sync_speech':
            _set(plan, line, 'picture_relation', 'additive', path+'.picture_relation', 'scene_offscreen_speech')
    # Reuse S61's actual identity predicate and nearest earlier anchor; no copy.
    _normalise_material_dialogue(plan, dict(scene_mode='single'), windows)
    for index, line in enumerate(plan.get('audio_draft', [])):
        if line.get('picture_relation') == 'sync_speech' and any(
                r.get('relation') == 'offscreen' for r in line.get('audio_relation', [])):
            _set(plan, line, 'picture_relation', 'additive', f'$.audio_draft[{index}].picture_relation',
                 'scene_speaker_not_visible')


def _visible_wrong_speaker(issue, plan):
    """A missing mouth movement is line feedback, not evidence of a new actor."""
    line = next((r for r in plan.get('audio_draft', []) if r['id'] == issue.get('line_id')), {})
    speaker = line.get('speaker') or {}
    ids = issue.get('segment_ids') or ([issue['segment_id']] if issue.get('segment_id') else line.get('segment_ids', []))
    visible = any(s.get('segment_id') in ids and s.get('window_id') == speaker.get('window_id') and
                  speaker.get('role_id') in s.get('role_ids', [])
                  for s in plan.get('segments', []))
    evidence = str(issue.get('evidence', ''))
    return visible and bool(re.search(r'认错|(?:身份|演员|人物|角色|说话人).{0,12}'
        r'(?:不符|错误|认成|换人|不是|不一致)|wrong (?:visible )?character|different (?:actor|character)', evidence, re.I))


def review_feedback(silent):
    """scene_v1 revises unsupported specifics and reaction/off-screen speech in place."""
    result = deepcopy(silent)
    must_fix = result.setdefault('must_fix', [])
    for issue in silent.get('verdict', {}).get('lines', []):
        if issue.get('issue') in {'speaker_mismatch', 'invisible_claim'} and issue not in must_fix:
            must_fix.append(deepcopy(issue))
    if must_fix:
        result.update(content_acceptable=False, revision_suggested=True)
    return result


def review_needs(silent, plan, lock, swaps):
    """Only unclear/inconsistent roles or a visibly wrong actor replace scenes."""
    from .pipeline import _material_picture_needs
    proxy = dict(lock, beats=[dict(beat_id=s['scene_id']) for s in lock['scenes']])
    view = deepcopy(plan)
    for segment in view['segments']:
        segment['beat_id'] = segment.get('scene_id', segment.get('slot_id'))
    identity = []
    for issue in silent.get('must_fix', []) + silent.get('verdict', {}).get('lines', []):
        kind = issue.get('kind') or issue.get('category') or issue.get('issue', '')
        # Never let identity words inside an unsupported line claim trigger retrieval.
        if issue.get('issue') in {'contradiction', 'invisible_claim'}:
            continue
        if issue.get('issue') == 'speaker_mismatch':
            if _visible_wrong_speaker(issue, plan):
                identity.append(dict(issue, kind='identity'))
        elif kind in {'identity', 'cast_cannot_cover', 'roles.clear', 'roles.consistent'}:
            identity.append(issue)
    filtered = dict(silent, must_fix=identity)
    return [dict(scene_id=n['beat_id'], reason='identity') for n in _material_picture_needs(filtered, None, view, proxy)
            if not swaps.get(n['beat_id'], 0)]
