"""Observe finite shots before choosing a complete conversation."""
from copy import deepcopy
import json
import math
from itertools import combinations, permutations
import re
from pathlib import Path

from .state import json_sha, write_json

FPS = 30
POLICY = 'joint_av_v1'


CUT_MERGE_S = .2


def detect_shots(source, start, end, cache):
    """Native-frame content changes, with no minimum shot duration.

    03 (fc6699b6): the adaptive detector alone missed 2 of 19 real cuts in a 50 s window
    (checked against ffmpeg scdet), so a 'shot' could hold two camera angles and a trimmed
    piece showed someone else (user 11-11). The union with a plain content detector is
    used; cuts closer than CUT_MERGE_S are one cut. An extra split only shortens a shot."""
    import av
    from scenedetect.detectors import AdaptiveDetector, ContentDetector
    from .speech_slots import _cache_path
    from .media import verify_source
    saved = _cache_path(source, start, end, cache, 'adaptive_content_cuts_v2.json')
    if saved.exists():
        return json.loads(saved.read_text())
    detectors = [AdaptiveDetector(min_scene_len=1), ContentDetector(min_scene_len=1)]
    times, indices = [], []
    with av.open(str(verify_source(source))) as video:
        stream = video.streams[source.get('video_stream_index', 0)]
        video.seek(int(start/float(stream.time_base)), stream=stream)
        for frame in video.decode(stream):
            if frame.time is None or frame.time < start:
                continue
            if frame.time >= end:
                break
            times.append(float(frame.time))
            height = max(2, round(frame.height*320/frame.width/2)*2)
            picture = frame.reformat(width=320, height=height, format='bgr24').to_ndarray()
            for detector in detectors:
                indices.extend(detector.process_frame(len(times)-1, picture))
    cuts = []
    for cut in sorted({round(times[i]-start, 6) for i in indices if 0 <= i < len(times) and start < times[i] < end}):
        if not cuts or cut - cuts[-1] >= CUT_MERGE_S:
            cuts.append(cut)
    write_json(saved, cuts)
    return cuts


def capacity(shots):
    from .speech_slots import _merge
    sources = {s['source_sha256'] for s in shots}
    return sum(b-a for source in sources for a, b in _merge([
        [s['source_start_s'], s['source_end_s']] for s in shots if s['source_sha256'] == source]))


def conversation_context(scene, source, excluded=()):
    """Bound one fresh look around an index slice; index edges are not scene cuts."""
    from .content_ranges import content_range_for
    start, end = (scene['allowed_ranges'][0][k] for k in ('source_start_s', 'source_end_s'))
    low, high = content_range_for(source) or (0, source['duration_s'])
    if source.get('content_range', {}).get('allow_internal_exclusions'):
        excluded = [*excluded, *source['content_range']['excluded']]
    for row in excluded:
        left, right = row['start_s'], row['end_s']
        if right <= start:
            low = max(low, right)
        elif left >= end:
            high = min(high, left)
    margin = min(end-start, scene['end_s']-scene['start_s'])
    return [math.ceil(max(low, start-margin)*FPS)/FPS, math.floor(min(high, end+margin)*FPS)/FPS]


def same_actor(left, right):
    if left.get('actor') and left['actor'] == right.get('actor'):
        return True
    # A resolved name may replace an unnamed index token. Contradictory actual
    # names must not be joined just because the old index reused an entity ID.
    unnamed = any(not b.get('actor') or b['actor'] in (b.get('entity_id'), b.get('role_id'))
                  for b in (left, right))
    return bool(unnamed and left.get('entity_id') and left['entity_id'] == right.get('entity_id'))


def screen_roles(scene):
    return {r['role'] for r in scene['reference_lines'] if r.get('on_screen', True)}


MINOR_ROLE_LINES, MINOR_ROLE_SHARE = 2, .1


def voice_minor_roles(lines):
    """A role with at most two lines and under 10% of the dialogue is voiced off screen.

    Every on-screen speaker needs its own speaking footage in the chosen window; a
    passer-by with one shout made 03's stall scene require four speaking actors.
    Off-screen voices are ordinary in dubbing practice (arXiv 2212.12137) and the
    line still lands over its addressee's reaction. Main roles are unchanged."""
    count = {}
    for r in lines:
        count.setdefault(r['role'], []).append(len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', r.get('text', ''))))
    total = sum(sum(v) for v in count.values())
    if not total or len(count) <= 2:
        return []  # no text to weigh, or a two-person dialogue: everyone keeps a face
    leads = sorted(count, key=lambda role: (-sum(count[role]), -len(count[role]), role))[:2]
    minor = sorted(role for role, sizes in count.items() if role not in leads and
                   len(sizes) <= MINOR_ROLE_LINES and sum(sizes) <= MINOR_ROLE_SHARE * total)
    for r in lines:
        if r['role'] in minor and r.get('on_screen') is not False:
            r.update(on_screen=False, offscreen_reason='minor_role_voiced_offscreen')
    return minor


def separate_voice_roles(scenes, lines):
    """A consistently unseen voice does not consume a physical actor in retrieval."""
    voice_minor_roles(lines)
    spoken = {r['role'] for r in lines}
    seen = {r['role'] for r in lines if r.get('on_screen') is not False}
    seen.update(r for s in scenes if not s['line_ids'] for r in s['roles'])
    for scene in scenes:
        unseen = [r for r in scene['roles'] if r in spoken-seen]
        visual = [r for r in scene['roles'] if r not in unseen]
        if unseen and visual:
            scene.update(voice_only_roles=unseen, roles=visual)


def bind_offscreen_voices(lock):
    """Restore dialogue identities after visual casting, without inventing a visible person."""
    for scene in lock['scenes']:
        for role in scene.get('voice_only_roles', []):
            ident = 'voice_' + json_sha(role)[:12]
            scene['role_bindings'][role] = dict(window_id=scene['window_ids'][0], role_id=ident,
                actor=role, entity_id=ident, appearance='参考仅画外发言，未绑定出镜演员',
                gender='unclear', voice_only=True)
            if role not in scene['roles']:
                scene['roles'] = [*scene['roles'], role]
            lock['cast_bindings'][role] = dict(actor=role, gender='unclear', voice_only=True,
                bindings=[], entity_ids=[], entity_bindings={})
            lock['cast'][role] = role


def cast_options(roles, actors, claimed):
    """Keep shared identities and reserve actors already playing other roles."""
    return {role: [rid for rid, actor in actors.items()
                   if all(same_actor(actor, used) if other == role else not same_actor(actor, used)
                          for other, used in claimed)] for role in roles}


LEAD_SHARE = .2


def lead_roles(lines):
    """Leads: the biggest speaker, plus anyone with at least LEAD_SHARE of the characters.
    21: the second-biggest of five speakers had 61 of 564 characters (11%)."""
    chars = {}
    for line in lines:
        if line.get('role'):
            chars[line['role']] = chars.get(line['role'], 0) + len(
                re.findall(r'[\u3400-\u9fffA-Za-z0-9]', line.get('text') or ''))
    total = sum(chars.values())
    ranked = sorted(chars, key=lambda role: (-chars[role], role))
    return set(ranked[:1]) | {role for role in ranked if total and chars[role] >= LEAD_SHARE * total}


def cast_for_dialogue(scene, actors, protected, claimed=()):
    """Choose the cast before writing, using all roles' finite speaking demand.

    21 (8b67dc78): six speaking roles, two speaking actors. Requiring every role to
    get its own speaker left no permutation at all, so the selection's guess (the
    80% lead on a silent actor) stood. Supporting speakers may stay uncast here and
    are then voiced off screen (joint_dialogue.voice_unfilmed_roles); leads may not."""
    demand = {role: sum(len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', r['text']))
                       for r in scene['reference_lines'] if r.get('role') == role and r.get('on_screen', True))
              for role in scene['roles']}
    needed = [r for r in scene['roles'] if demand[r]]
    leads = lead_roles(scene['reference_lines'])
    available = {s['role_id'] for s in scene['speech_slots']}
    seconds = {rid: capacity([s for s in scene['speech_slots'] if s['role_id'] == rid]) for rid in available}
    choices = []
    options = cast_options(needed, actors, claimed)
    for role, ids in scene.get('cast_constraints', {}).items():
        if role in options:
            options[role] = [rid for rid in options[role] if rid in ids]
    for size in range(min(len(needed), len(available)), 0, -1):
        for cast in combinations(needed, size):
            if any(role in leads for role in needed if role not in cast):
                continue
            for ids in permutations(sorted(available), size):
                if any(rid not in options[role] for role, rid in zip(cast, ids)):
                    continue
                if any(role in protected and actors[rid]['actor'] != protected[role] for role, rid in zip(cast, ids)):
                    continue
                if len({actors[rid]['actor'] for rid in ids}) != len(ids):
                    continue
                pairs = list(zip(cast, ids))
                ratios = [seconds[rid]/demand[role] for role, rid in pairs] + [0] * (len(needed) - size)
                retained = sum(scene['role_bindings'].get(role, {}).get('role_id') == rid for role, rid in pairs)
                carried = carried_seconds(lambda _: [(seconds[rid], demand[role]) for role, rid in pairs], None)
                choices.append(((carried, min(ratios, default=0), sum(ratios), size, retained), dict(pairs)))
    return max(choices, key=lambda row: row[0])[1] if choices else {}


def carried_seconds(evidence_rows, binding):
    """Seconds of reference dialogue that can be voiced over the bound actor's own
    speaking footage: sum over (scene, role) of min(available, needed).

    27 r2: maximising only the worst ratio let a 21 s tail scene (one speaker)
    give the 318-character lead role to an actor with 3 s of speech, because any
    other choice left the tail at ratio 0. Total carried speech comes first; the
    worst ratio only breaks ties."""
    from .dialogue_cut import RATES
    cps = sum(RATES.values()) / len(RATES)
    return sum(min(seconds, count / cps) for seconds, count in evidence_rows(binding))


def cast_for_story(scenes, actors, fixed=None, *, feasible=None):
    """Choose shared actors from every scene's speaking inventory before locking any scene.

    A role that is not a lead may stay uncast (None) when the footage has fewer usable
    actors than roles: a supporting speaker is voiced off screen later, a silent extra
    is omitted. See cast_for_dialogue."""
    domains, evidence, introduced = {}, [], set()
    leads = lead_roles([line for scene in scenes for line in scene['reference_lines']])
    spoken = set()
    for scene in scenes:
        sid = scene['scene_id']
        demand = {r: sum(len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', line['text']))
                         for line in scene['reference_lines'] if line['role'] == r and line.get('on_screen', True))
                  for r in scene['roles']}
        for role, count in demand.items():
            available = {}
            for rid, actor in actors[sid].items():
                if role in scene.get('cast_constraints', {}) and rid not in scene['cast_constraints'][role]:
                    continue
                shots = ([s for s in scene['speech_slots'] if s['role_id'] == rid] if count else
                         [s for s in scene.get('coverage_shots', []) if rid in s['visible']])
                seconds = capacity(shots)
                # A role already introduced on screen in an earlier scene may be voiced
                # over listeners later, so an actor without speech here stays a candidate
                # at 0 s and the carried-speech score decides (27 r2). A role's first
                # speaking scene still needs its actor's own speech.
                floor = -1 if role in introduced else 0
                if seconds > available.get(actor['actor'], (floor, None))[0]:
                    available[actor['actor']] = (seconds, rid)
            domains[role] = domains.get(role, set(available)) & set(available)
            evidence.append((sid, role, max(1, count), available))
            if count:
                spoken.add(role)
        introduced |= {line['role'] for line in scene['reference_lines'] if line.get('on_screen', True)}
    for role, actor in (fixed or {}).items():
        if role in domains:
            domains[role] &= {actor}
    # Supporting speakers and silent extras may stay uncast; leads and fixed roles may not.
    # Silent extras without visible footage are omitted later (joint_dialogue.omit_unfilmed_silent_roles).
    optional = set(domains) - leads - set(fixed or {})
    if not domains or any(not ids for role, ids in domains.items() if role not in optional):
        return {}
    def rows(binding):
        return [(available[binding[role]][0] if binding[role] is not None else 0, count)
                for _, role, count, available in evidence if role in binding]
    def score(binding):
        ratios = [seconds/count for seconds, count in rows(binding)]
        cast = sum(actor is not None for actor in binding.values())
        return carried_seconds(rows, binding), min(ratios, default=0), sum(ratios), cast
    def cast_only(binding):
        return {role: actor for role, actor in binding.items() if actor is not None}
    beam = [{}]
    for role in sorted(domains, key=lambda r: (len(domains[r]), r)):
        options = sorted(domains[role]) + ([None] if role in optional else [])
        expanded = [dict(binding, **{role: actor}) for binding in beam for actor in options
                    if actor is None or actor not in binding.values()]
        beam = sorted(expanded, key=score, reverse=True)[:64]
        if not beam:
            return {}
    beam = [binding for binding in beam if feasible is None or feasible(cast_only(binding))]
    if not beam:
        return {}
    chosen = max(beam, key=score)
    result = {s['scene_id']: {} for s in scenes}
    for sid, role, _, available in evidence:
        if chosen[role] is not None:
            result[sid][role] = available[chosen[role]][1]
    return result


def sufficient(scene):
    from .joint_dialogue import eligible
    required = screen_roles(scene)
    speakers = {s.get('role') for s in scene.get('speech_slots', [])}
    return (required <= speakers and bool(scene.get('conversation')) and
            scene.get('picture', {}).get('setting_match') is not False and
            capacity(scene.get('coverage_shots', [])) >= .85*(scene['end_s']-scene['start_s']) and
            all(capacity([shot for shot in scene.get('coverage_shots', []) if eligible(scene, shot, role)]) >=
                .85*sum(max(0, r.get('end_s', 0)-r.get('start_s', 0)) for r in scene['reference_lines'] if r['role']==role)
                for role in required if role in scene.get('role_bindings', {})))


def observed_cast(bindings, actors, cast):
    """Resolve stale index IDs by the identity observed in the actual footage."""
    from .film_cast import canonical_name, character_for
    names = {rid: canonical_name(row['name'], cast) for rid, row in actors.items()
             if row.get('name') and str(row['name']).strip().lower() not in {'null', 'none'}}
    result = {}
    for role, binding in bindings.items():
        target = canonical_name(binding['actor'], cast)
        matches = [rid for rid, name in names.items() if name == target]
        current = binding['role_id']
        if matches:
            result[role] = current if current in matches else matches[0]
        elif current in actors and current not in names and not character_for(target, cast):
            result[role] = current  # An unnamed extra retains its observed local identity.
        else:
            result[role] = None
    return result


def resolve_cast(route, scene, report, index):
    """Match unfamiliar names by previously observed appearance, not world knowledge."""
    resolved = observed_cast(scene['role_bindings'], report['actors'], route.cast)
    # Cross-scene identity is established by the verified speaking footage.
    # A local index may have both a stale name and a stale appearance.
    for role in scene.get('identity_anchors', {}):
        resolved[role] = None
    missing = [role for role, rid in resolved.items() if rid is None]
    if not missing:
        return resolved
    targets = {role: scene.get('identity_anchors', {}).get(role,
        {k: scene['role_bindings'][role][k] for k in ('actor', 'appearance')}) for role in missing}
    key = json_sha(targets)
    saved = report.setdefault('identity_resolutions', {})
    if key not in saved:
        evidence = {rid: row['appearance'] for rid, row in report['actors'].items()}
        prompt = ('仅匹配已经逐帧观察过的人物外观，不推测镜头内容。targets是先前已确认的演员外观，'
            'observed是这段实际素材中各ID的外观。姓名不是证据，比较体型、物种、脸部、服装及配饰。'
            '只有外观一致且可唯一匹配才返回role_id；人物不在场或无法确定返回null，不能选最接近的替代人物。'
            '不同故事角色不能占用同一ID；reserved中的ID已属于其他角色。'
            '只返回JSON {"bindings":{"故事角色":{"role_id":null,"reason":"实际外观依据"}}}。\n' +
            json.dumps(dict(targets=targets, observed=evidence,
                reserved=[rid for rid in resolved.values() if rid is not None]), ensure_ascii=False))
        def validate(value):
            rows = value.get('bindings', {})
            if set(rows) != set(missing):
                raise ValueError('bindings须逐一回答所有targets角色')
            used = {rid for rid in resolved.values() if rid is not None}
            for row in rows.values():
                rid = row.get('role_id')
                if not row.get('reason') or (rid is not None and (rid not in evidence or rid in used)):
                    raise ValueError('须有实际外观依据，且选未占用的实际ID或null')
                if rid is not None:
                    used.add(rid)
        if not hasattr(route, 'identity_calls'):
            route.identity_calls = {}
        number = route.identity_calls.get(index, 0)+1
        saved[key] = route._call(f'material_scene_identity_{index}_{number}', prompt, validate)['bindings']
        route.identity_calls[index] = number
    resolved.update({role: row['role_id'] for role, row in saved[key].items()})
    return resolved


def sync_cast_bindings(lock):
    """Keep render metadata consistent with corrected, scene-local visual IDs."""
    for role, info in lock['cast_bindings'].items():
        info.update(bindings=[], entity_bindings={})
        for scene in lock['scenes']:
            if role not in scene['role_bindings']:
                continue
            actual = scene['role_bindings'][role]
            binding = {k: actual[k] for k in ('window_id', 'role_id')}
            if binding not in info['bindings']:
                info['bindings'].append(binding)
            rows = info['entity_bindings'].setdefault(actual.get('entity_id'), [])
            if binding not in rows:
                rows.append(binding)


def visible_objects(row):
    """Q3 evidence: optional object nouns; anything malformed is simply ignored."""
    raw = row.get('objects')
    return list(dict.fromkeys(o.strip() for o in raw if isinstance(o, str) and 0 < len(o.strip()) <= 12))[:6] \
        if isinstance(raw, list) else []


def visible_activity(row):
    """Discard contradictory activity labels without adding visual evidence."""
    row = deepcopy(row)
    row['speaking'] = [r for r in row['speaking'] if r in row['visible']]
    row['listeners'] = [r for r in row['listeners'] if r in row['visible'] and r not in row['speaking']]
    return row


def grounded_shots(shots, grounding, known_ids, prior_aliases=None):
    """Apply visual identity corrections to every fact, preserving source intervals."""
    actors = grounding.get('actors', {})
    if not actors or set(actors)-set(known_ids) or any(
            not isinstance(a, dict) or not a.get('appearance') for a in actors.values()):
        raise ValueError('actors须使用已提供ID，逐一记录实际人物外观；重复身份只保留一个ID')
    aliases = grounding.get('aliases')
    prior_aliases = prior_aliases or {}
    if not isinstance(aliases, dict) or any(old in actors or not isinstance(a, dict) or not a.get('reason') or
            (a.get('canonical_id') != prior_aliases[old]['canonical_id'] if old in prior_aliases else
             old not in known_ids or a.get('canonical_id') not in actors)
            for old, a in aliases.items()):
        raise ValueError('aliases须把有图像证据的重复ID指向唯一实际人物，并给出依据；无重复则填{}')
    rows = grounding.get('shots', [])
    if not isinstance(rows, list) or [r.get('slot_id') for r in rows] != [s['slot_id'] for s in shots]:
        raise ValueError('shots须按顺序逐一核对所有原片段，不增删镜头')
    result = []
    for shot, row in zip(shots, rows):
        row = deepcopy(row)
        for key in ('visible', 'speaking', 'listeners'):
            ids = row.get(key)
            if not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids)-set(actors):
                raise ValueError(f'{key}只能使用核实后actors中的唯一身份')
        # A visible speaking person cannot safely serve as a silent reaction.
        # Conservatively discard contradictory labels, without adding evidence.
        row = visible_activity(row)
        if len(row['speaking']) > len(shot['speaking']):
            # Stills can correct identity, not establish additional mouth motion.
            # Retain only an unambiguous correspondence to the video evidence;
            # never choose one of several asserted speakers arbitrarily.
            mapping = dict(prior_aliases, **aliases)
            observed = {mapping.get(r, {}).get('canonical_id', r) for r in shot['speaking']}
            row['speaking'] = [r for r in row['speaking'] if r in observed]
            if len(row['speaking']) > len(shot['speaking']):
                row['speaking'] = []
        fields = ('place', 'action', 'entry', 'exit', 'identity_evidence')
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in fields):
            raise ValueError('逐镜头填写校正后的动作、起止状态与实际人物证据，描述中的ID也须同步校正')
        result.append(dict(shot, **{k: row[k] for k in ('visible', 'speaking', 'listeners', *fields)},
                           role_id=next(iter(row['speaking'] or row['visible']), None)))
    return result


def known_aliases(route, window_id):
    """An established source identity also applies to overlapping scene observations."""
    return {old: deepcopy(alias) for (_, wid), check in route.checks.items() if wid == window_id
            for old, alias in check.get('actor_aliases', {}).items()}


def observe(route, scene, index, *, full_window=False):
    """Every supplied second is watched; chunk boundaries are transport limits, not shots."""
    from .scene_grounding import contact_sheet

    allowed = scene['allowed_ranges'][0]
    candidate = route._candidate(route.choices[scene['scene_id']])
    subtitle_visible = any(row.get('source_visual_policy') == 'silent_subtitle_visible_v1'
                           for row in candidate['records'])
    aliases = known_aliases(route, scene['window_ids'][0])
    hints = {rid: actor for rid, actor in route._roles(route.choices[scene['scene_id']]).items() if rid not in aliases}
    source = route.sources[allowed['source_sha256']]
    masks = (route.store.get(candidate['records'][0]['observation_id'])['window'].get('source_subtitle_masks')
             if route.store is not None else None)
    shots, receipts = [], []
    low, high = allowed['source_start_s'], allowed['source_end_s']
    context = route.choices[scene['scene_id']].get('context_range')
    if context:
        masks = route.context_masks(source['sha256'], low, high)
    if not scene['reference_lines'] and not full_window:
        # Short visual beats use a complete indexed event plus context. A whole
        # dialogue still needs its entire window to establish finite capacity.
        bound = {b['role_id'] for b in scene['role_bindings'].values()}
        events = [r for r in candidate['records'] if r.get('kind') == 'event']
        if events:
            event = max(events, key=lambda r: (len(bound & {p['role_id'] for p in r.get('roles', [])}),
                        r['source_end_s']-r['source_start_s']))
            budget = min(high-low, max(12, 1.5*(scene['end_s']-scene['start_s'])))
            center = (event['source_start_s']+event['source_end_s'])/2
            low = max(low, min(center-budget/2, high-budget))
            high = low+budget
    cuts = [low, *[low+t for t in detect_shots(source, low, high, route.cache)], high]
    count = math.ceil((high-low)/11.8)
    for batch in range(count):
        start, end = low+(high-low)*batch/count, low+(high-low)*(batch+1)/count
        boundaries = {f'cut_{i}': [max(a, start)-start, min(b, end)-start]
                      for i, (a, b) in enumerate(zip(cuts, cuts[1:]), 1) if max(a, start) < min(b, end)}
        clip = route.clip_maker(source, start, end, route.cache, pad_s=0, subtitle_masks=masks)
        prompt = ('按播放顺序完整观察短片。source_shots是程序从原视频检测的切镜区间，时间为本短片秒数。'
            '逐一回答每个shot_id，不能合并、增删或自行估计时间边界。只报告这个区间实际看见的事实。'
            '同一ID的外观必须始终一致；按体型、服装和配饰区分同物种角色。角色提示仅为定位信息，实际看不清则不猜。'
            'visible列清晰可辨角色ID；speaking只列嘴有连续讲话运动的人，吃东西/咀嚼/惊讶张嘴不算讲话；'
            'listeners只列实际在听同场对方讲话且无讲话口型的人。未知人物不猜ID。'
            '主动表演、展示物品、离开、舞动或摆弄他人身体不属于静默倾听；即使同场也不能放入listeners。'
            '自然走动、转身、点头都保留，如实描述。'
            'loopable：固定机位、固定视角、讲话人留在原地，只有嘴部、表情、点头等原地小动作时为true；'
            '走动、跑、转身离开、切换人物、镜头推拉摇移或动作有明显起止时为false；不确定填false。'
            '说明地点、面对谁、实际动作及起止姿态，无法判断的关系如实标注。'
            'objects列该镜头里清楚可见、人物在使用或展示的物件名词（最多6个，看不清不写，可为[]）。'
            '只返回JSON {"shots":[{"shot_id":"cut_1","visible":["A"],"speaking":[],"listeners":["A"],'
            '"place":"具体可见空间","action":"实际事件","entry":"起始姿态","exit":"结束姿态","objects":[],"loopable":false}]}。\n' +
            json.dumps(dict(duration_s=end-start, source_shots=boundaries, characters=hints), ensure_ascii=False))
        if subtitle_visible:
            prompt = prompt.replace('"loopable":false}', '"loopable":false,"content_kind":"story"}',1)
            prompt += ('\n本素材保留原字幕，但原字幕不能证明画面动作或讲话。'
                '每个shot额外给content_kind：story/title/credits/transition/uncertain。'
                '独立核对标题边框、作品名和结束卡；标题/片尾中的装饰角色不算剧情演员。'
                '字幕组水印、场内招牌或普通对白字幕本身不等于片头片尾。'
                '无法区分剧情与标题时标uncertain；非story段的visible/speaking/listeners填空数组。')
        def validate(value):
            rows = value.get('shots')
            if not isinstance(rows, list) or not rows:
                raise ValueError('shots须为按时间排列的非空数组')
            if [r.get('shot_id') for r in rows] != list(boundaries):
                raise ValueError(f'按顺序逐一标注实际镜头ID：{list(boundaries)}')
            for row in rows:
                if subtitle_visible and row.get('content_kind') not in {'story','title','credits','transition','uncertain'}:
                    raise ValueError('每个shot须给content_kind: story/title/credits/transition/uncertain')
                for key in ('visible', 'speaking', 'listeners'):
                    if not isinstance(row.get(key), list) or set(row[key])-set(hints):
                        raise ValueError(f'{key}只能列实际角色ID：{list(hints)}')
                if any(not row.get(k) for k in ('place', 'action', 'entry', 'exit')):
                    raise ValueError('每段须填写实际地点、动作和起止状态')
        number = route.slot_calls.get(index, 0)+1
        reply = route._call(f'material_scene_coverage_{index}_{number}', prompt, validate, clip['path'])
        route.slot_calls[index] = number
        receipts.append(dict(start_s=start, end_s=end, clip=clip, reply=reply))
        for row in reply['shots']:
            if subtitle_visible and row['content_kind'] != 'story':
                continue
            row = visible_activity(row)
            left, right = boundaries[row['shot_id']]
            a = math.ceil((start+left)*FPS-1e-6)/FPS
            b = math.floor(min(end, start+right)*FPS+1e-6)/FPS
            if b <= a or not row['visible']:
                continue
            shots.append(dict(allowed, **{k: row[k] for k in ('visible', 'speaking', 'listeners', 'place', 'action', 'entry', 'exit')},
                objects=visible_objects(row),
                source_start_s=a, source_end_s=b, detected_shot_id=row['shot_id'],
                slot_id=f'{scene["scene_id"]}_shot_{len(shots)+1}',
                role_id=next(iter(row['speaking'] or row['visible'])), gender='unclear',
                # Static talking footage may replay (user rule 11-05; limited-animation practice).
                # Only an explicit true counts; anything else stays a single play.
                loopable=row.get('loopable') is True))
    if not shots:
        return dict(shots=[], groups=[], actors={}, receipts=receipts)
    sheet, entries = contact_sheet(route, shots, masks)
    identity_context = dict(characters=hints, **(dict(confirmed_identity_aliases=aliases) if aliases else {}))
    identity_instruction = ''
    if not scene['reference_lines']:
        identity_context.update(story_cast={role: scene.get('identity_anchors', {}).get(role,
            {k: binding[k] for k in ('actor', 'appearance')})
            for role, binding in scene['role_bindings'].items()})
        identity_instruction = ('story_cast是已选演员的实际外观，逐一与图片中的人物匹配，不能沿用索引的角色编号对应关系。'
            '名字必须与图片外观一致；若名字与体型、物种、服饰矛盾，以图像为准。不能把旧编号上的名字复制给另一种外观的人物。')
    from .scene_route import FUNCTIONAL_SETTING_GUIDE
    assess_setting = bool(context or getattr(route, 'reference_source', None) is not None)
    context_instruction = ('逐组对照reference_scene的叙事作用、地点功能与互动，'
        '输出setting_match布尔值及setting_reason；只把可合理承载该场戏的实际空间与互动判为true。'
        + FUNCTIONAL_SETTING_GUIDE +
        '相邻时间不是同一场的证据，不得把换地点、无关训练或其他事件并入原对话。'
        if assess_setting else '')
    prompt = ('图片每行对应一个实际片段的头中尾，observations来自完整短片的逐段观察。'
        '旧characters和observations的身份ID都是待核对的定位提示，不是人物真值。'
        '先跨镜头核实实际人物，尤其检查远景/近景、正面/背面、不同光线下的同一人是否被拆成多个ID。'
        '同一实际人物只保留一个稳定ID；确有图片证据的重复编号放入aliases，不能因为编号不同就认定为两个人。'
        '同物种但脸部、服饰或体型不同者必须分开；不能为了凑故事角色合并或拆分身份。姓名不确定填null。'
        '逐个返回校正后的shots：同步修正visible、speaking、listeners及action/entry/exit里说话者和对话对象的ID。'
        '不能只修改actors中的姓名却保留旧镜头身份。动作和嘴部运动仍以完整短片观察为准，静帧不能凭空增加讲话。'
        'identity_evidence写该镜头人物脸部、服饰、体型及跨镜头对应的实际依据；不确定的人物不猜。'
        '只使用characters提供的ID，合并时优先保留外观已明确对应的ID；actors只列核实后的唯一人物。'
        '再把片段分成可以剪成同一场连续事件的groups，分组描述也须使用校正后的身份。'
        'group代表整场连续事件，不是一个动作段或一组正反打。持续在同一空间的同一组人物对话须合为一组，'
        '期间插入相关物件特写、听者反应、远近景、人物靠近或改变姿态，都不另分组。'
        '输出前核对：若两个组的description描述的是同一场对话延续，必须合并。'
        '同组可以包括正反打、听者反应和相关动作，但须是同一地点时段、同一组相互交流的人。'
        '庭院/桥、室内/室外、不同对话对象须分组；不能因同电影或同一个人物就合并。'
        '每个片段恰好属于一组。不要为了凑够时长或人数合组。'
        '只返回JSON {"actors":{"A":{"name":null,"appearance":"实际外观"}},'
        '"aliases":{"旧重复ID":{"canonical_id":"A","reason":"跨镜头图像依据"}},'
        '"shots":[{"slot_id":"片段ID","visible":["A"],"speaking":[],"listeners":["A"],'
        '"place":"实际地点","action":"校正后的实际动作与对话对象","entry":"起始姿态","exit":"结束姿态",'
        '"identity_evidence":"实际人物外观证据"}],'
        '"groups":[{"group_id":"g1","slot_ids":["片段ID"],"description":"实际共同地点人物与互动依据"' +
        (',"setting_match":true,"setting_reason":"与reference_scene必要叙事功能及互动的对应或缺失"' if assess_setting else '') + '}]}。\n' +
            identity_instruction + ('confirmed_identity_aliases是此前已用这段原片核实的统一编号，须继续使用规范ID，不能把旧编号重新拆成另一个人物。' if aliases else '') +
            context_instruction + json.dumps(dict(shots=entries, observations=shots,
                **identity_context, **(dict(reference_scene={k:scene.get(k) for k in ('setting','interaction','template','visual_function')})
                                      if assess_setting else {})), ensure_ascii=False))
    def validate_groups(value):
        grounded_shots(shots, value, hints, aliases)
        ids = [i for g in value.get('groups', []) for i in g.get('slot_ids', [])]
        if sorted(ids) != sorted(s['slot_id'] for s in shots):
            raise ValueError('每个片段必须恰好属于一组')
        if any(not g.get('description') for g in value['groups']):
            raise ValueError('须记录实际共同空间和互动依据')
        if assess_setting and any(type(g.get('setting_match')) is not bool or not g.get('setting_reason') for g in value['groups']):
            raise ValueError('邻近原片每组须独立给setting_match布尔值和setting_reason，不能沿用索引窗口的结论')
    number = route.slot_calls.get(index, 0)+1
    grounding = route._call(f'material_scene_grounding_{index}_{number}', prompt, validate_groups, sheet, image=True)
    route.slot_calls[index] = number
    checked = verify_speakers(route, grounded_shots(shots, grounding, hints, aliases), grounding.get('actors', {}), index, masks)
    # Verification can exclude validly annotated shots. Keep every downstream
    # group inside the retained inventory before casting or scoping its ids.
    retained = {s['slot_id'] for s in checked}
    groups = [dict(g, slot_ids=[i for i in g['slot_ids'] if i in retained]) for g in grounding['groups']]
    return dict(grounding, shots=checked, groups=[g for g in groups if g['slot_ids']],
                raw_shots=shots, receipts=receipts)


VERIFY_BATCH = 8


def verify_speakers(route, shots, actors, index, masks):
    """Ask one narrow question per talking shot: who is visibly speaking here?

    03 (fc6699b6): 6 of 24 checked dialogue frames showed the wrong speaker. The broad shot
    annotation labelled two look-alike pandas the wrong way round and called an off-screen
    voice over rabbits 'B speaking'. A focused check on the shot's own frames is applied
    before casting: a named speaker replaces the label; 'none' confirms no visible speaker.
    An unclear speaker (crowds, backs, far shots) cannot establish silent footage. Unclear shots
    are excluded before casting, so neither dialogue nor reuse can treat them as listeners."""
    from .scene_grounding import contact_sheet
    ids = sorted(actors)
    talking = [s for s in shots if s['speaking']]
    for k in range(0, len(talking), VERIFY_BATCH):
        batch = talking[k:k+VERIFY_BATCH]
        sheet, entries = contact_sheet(route, batch, masks)
        prompt = ('图片每行是一个镜头的头、中、尾三帧，Shot编号与shots顺序一致。只核对一件事：'
            '每个镜头里，观众能不能清楚看到是哪个人物在开口说话。人物按actors的实际外观区分，'
            '同物种按脸部、服饰、体型区分，不能只看物种。observed_speaking是之前的视频标注，只作参考，'
            '可能把画外说话或长得像的人认错。说话者在画面外、画面里只有听者或旁人时speaker填"none"；'
            '人多的远景、背影、太小或无法确定是谁在说时填"unclear"；能确定时填该人物ID。'
            'visible列画面中清楚可见的人物ID。只返回JSON {"shots":[{"slot_id":"片段ID","speaker":"A",'
            '"visible":["A","B"],"evidence":"一句话依据"}]}。\n' +
            json.dumps(dict(actors={i: (actors[i] or {}).get('appearance') for i in ids},
                shots=[dict(shot=e['shot'], slot_id=s['slot_id'], observed_speaking=s['speaking'],
                            observed_visible=s['visible'], action=s['action']) for e, s in zip(entries, batch)]),
                ensure_ascii=False))
        def validate(value, batch=batch):
            rows = value.get('shots') if isinstance(value, dict) else None
            by_id = {r.get('slot_id'): r for r in rows or [] if isinstance(r, dict)}
            missing = [s['slot_id'] for s in batch if s['slot_id'] not in by_id]
            if not isinstance(rows, list) or missing:
                raise ValueError(f'须逐个回答每个镜头，缺少{missing}')
            bad = [i for i, r in by_id.items() if r.get('speaker') not in (*ids, 'none', 'unclear')]
            if bad:
                raise ValueError(f'speaker只能是{ids}、none或unclear：{bad}')
            for row in by_id.values():
                row['visible'] = [i for i in row.get('visible') or [] if i in ids] if isinstance(row.get('visible'), list) else None
            return value
        number = route.slot_calls.get(index, 0)+1
        reply = route._call(f'material_scene_speakers_{index}_{number}', prompt, validate, sheet, image=True)
        route.slot_calls[index] = number
        verdicts = {r['slot_id']: r for r in reply['shots']}
        for shot in batch:
            verdict = verdicts[shot['slot_id']]
            shot['speaker_check'] = dict(observed=list(shot['speaking']), speaker=verdict['speaker'],
                                         evidence=verdict.get('evidence', ''))
            if verdict['speaker'] == 'unclear':
                continue  # Unknown mouth motion is not evidence of silent listening.
            speaker = verdict['speaker'] if verdict['speaker'] in ids else None
            if verdict.get('visible') is not None:   # [] means no cast member is visible
                shot['visible'] = list(verdict['visible'])
            if speaker and speaker not in shot['visible']:
                shot['visible'] = [*shot['visible'], speaker]
            # A visible person wrongly labelled as the speaker was in the exchange: now a listener.
            heard = [*shot['listeners'], *shot['speaking']]
            shot['speaking'] = [speaker] if speaker else []
            shot['listeners'] = [r for r in dict.fromkeys(heard) if r in shot['visible'] and r != speaker]
            shot['role_id'] = next(iter(shot['speaking'] or shot['visible']), shot.get('role_id'))
    return [s for s in shots if s['visible'] and (s.get('speaker_check') or {}).get('speaker') != 'unclear']


def observation_key(route, scene, *, full_window=False):
    # Story scene IDs do not change the people or actions in identical source frames.
    # A short visual sample cannot stand in for a full dialogue-window observation.
    return json_sha(dict(policy=POLICY, observation='grounded_identities_v5_speaker_exclusions', boundaries='adaptive_content_cuts_v2',
        setting_policy='functional_scene_fit_v1',
        scope='full' if scene['reference_lines'] or full_window else 'visual_event', allowed=scene['allowed_ranges'],
        **(dict(context_setting={k:scene.get(k) for k in ('setting','interaction','template')})
           if route.choices[scene['scene_id']].get('context_range') else {})))


def conversation_variants(route, scene):
    """Reuse observed groups; never combine unrelated events to increase capacity."""
    key = observation_key(route, scene)
    report = route.slot_reports.get(key)
    if not report:
        return [(None, deepcopy(scene))]
    by_id = {s['slot_id']: s for s in report['shots']}
    variants = []
    for group in report['groups']:
        trial = deepcopy(scene)
        shots = [dict(by_id[i], conversation_id=group['group_id']) for i in group['slot_ids']]
        trial.update(coverage_shots=shots,
            speech_slots=[dict(s, role_id=s['speaking'][0]) for s in shots if len(s['speaking']) == 1],
            conversation=deepcopy(group))
        trial['picture'].update(single_location=True, scene=group['description'])
        if 'setting_match' in group:
            trial['picture'].update(setting_match=group['setting_match'], setting_reason=group['setting_reason'])
        variants.append((dict(observation_key=key, group_id=group['group_id']), trial))
    return variants or [(None, deepcopy(scene))]


def scope_shot_ids(report, observation):
    """Keep reused observations distinct from another range's local shot numbers."""
    report = deepcopy(report)
    mapping = {s['slot_id']: 'shot_' + json_sha([observation, s['slot_id']])[:20]
               for s in report['shots']}
    for shot in report['shots']:
        shot['observation_slot_id'] = shot['slot_id']
        shot['slot_id'] = mapping[shot['slot_id']]
    for group in report['groups']:
        group['slot_ids'] = [mapping[i] for i in group['slot_ids']]
    return report


def observed_visual_report(route, scene):
    """Use a richer observed event instead of stale metadata for a silent beat."""
    allowed = scene['allowed_ranges'][0]
    candidates = []
    for report in route.slot_reports.values():
        # Only whole, already observed shots inside this window are reusable.
        shots = [s for s in report['shots'] if
                 s.get('window_id') == scene['window_ids'][0] and
                 s['source_sha256'] == allowed['source_sha256'] and
                 allowed['source_start_s'] <= s['source_start_s'] < s['source_end_s'] <= allowed['source_end_s']]
        if not shots:
            continue
        by_id = {s['slot_id']: s for s in shots}
        groups = [dict(g, slot_ids=[i for i in g['slot_ids'] if i in by_id]) for g in report['groups']]
        groups = [g for g in groups if g['slot_ids']]
        aliases = report.get('aliases', {})
        bound = {aliases.get(b['role_id'], {}).get('canonical_id', b['role_id'])
                 for b in scene['role_bindings'].values()}
        if not groups or not bound:
            continue
        covered = max(len(bound & {r for i in g['slot_ids'] for r in by_id[i]['visible']}) for g in groups)
        if covered != len(bound):
            continue
        # A dialogue's reference setting is not the silent beat's setting verdict.
        for group in groups:
            group.pop('setting_match', None)
            group.pop('setting_reason', None)
        candidates.append((capacity(shots), dict(deepcopy(report), shots=deepcopy(shots), groups=groups)))
    return max(candidates, key=lambda row: row[0])[1] if candidates else None


def shared_observation_path(route, scene, key):
    """Where runs share the observation of the same footage, rules and reference scene.

    The observation_key fixes the footage, rules and scope; the reference scene is added
    because each group's setting_match is judged against it."""
    folder = (getattr(route, 'config', None) or {}).get('scene_observation_cache')
    if not folder:
        return None
    shared = json_sha(dict(observation_key=key, reference_scene={k: scene.get(k) for k in
                           ('setting', 'interaction', 'template', 'visual_function')}))
    return Path(folder) / f'{shared}.json'


def build_scene_slots(route, scene, index):
    from .speech_slots import bind_slot_roles
    key = observation_key(route, scene)
    shared = shared_observation_path(route, scene, key)
    if key not in route.slot_reports and shared is not None and shared.is_file():
        saved = json.loads(shared.read_text())
        route.slot_reports[key] = saved['report']
        route.state.set_artifact('scene_observation_reused', dict(policy='shared_scene_observation_v1',
            scene_id=scene['scene_id'], observation_key=key, path=str(shared), origin=saved.get('origin')))
    full_key = observation_key(route, scene, full_window=True)
    if not scene['reference_lines']:
        observed = observed_visual_report(route, scene)
        if observed is not None:
            route.slot_reports[key] = observed
    if key not in route.slot_reports and full_key in route.slot_reports:
        route.slot_reports[key] = deepcopy(route.slot_reports[full_key])
    if key not in route.slot_reports:
        report = observe(route, scene, index)
        if not scene['reference_lines']:
            bound = set(resolve_cast(route, scene, report, index).values())
            by_id = {s['slot_id']: s for s in report['shots']}
            if not any(bound <= {r for i in g['slot_ids'] for r in by_id[i]['visible']}
                       for g in report.get('groups', [])):
                # A metadata hit does not establish that every actor is in the
                # sampled event. Inspect the whole candidate before replacing it.
                report = observe(route, scene, index, full_window=True)
        route.slot_reports[key] = scope_shot_ids(report, key)
        if shared is not None:
            shared.parent.mkdir(parents=True, exist_ok=True)
            write_json(shared, dict(report=route.slot_reports[key], origin=str(route.state.output)))
    report = deepcopy(route.slot_reports[key])
    check = route.checks.setdefault((scene['scene_id'], scene['window_ids'][0]), deepcopy(scene['picture']))
    check['actors'] = deepcopy(report['actors'])
    check['actor_aliases'] = dict(known_aliases(route, scene['window_ids'][0]), **deepcopy(report.get('aliases', {})))
    resolved = resolve_cast(route, scene, report, index)
    route.slot_reports[key]['identity_resolutions'] = deepcopy(report.get('identity_resolutions', {}))
    scene['unverified_cast'] = [role for role, rid in resolved.items() if rid is None]
    for role, rid in resolved.items():
        if rid is not None:
            scene['role_bindings'][role].update(role_id=rid, appearance=report['actors'][rid]['appearance'])
            route.choices[scene['scene_id']]['role_bindings'][role] = rid
            from .film_cast import character_for
            target = scene['role_bindings'][role]['actor']
            if character_for(target, route.cast):
                check['actors'][rid]['name'] = target
    # Selection assigns narrative roles; observation only resolves the same person's
    # local ID. Footage capacity may choose another window, never another identity.
    # An unresolved identity has an empty domain, not permission to pick a lookalike.
    scene['cast_constraints'] = {role: [resolved[role]] if resolved.get(role) is not None else []
                                 for role in scene['roles']}
    scene['cast_policy'] = 'selection_identity_v1'
    by_id = {s['slot_id']: s for s in report['shots']}
    required = len(screen_roles(scene)) if scene['reference_lines'] else len(scene['roles'])
    def score(group):
        shots = [by_id[i] for i in group['slot_ids']]
        if not scene['reference_lines']:
            visible = {r for s in shots for r in s['visible']}
            bound = {r['role_id'] for r in scene['role_bindings'].values()}
            return group.get('setting_match', True), len(visible & bound), capacity(shots)
        speakers = {r for s in shots for r in s['speaking']}
        return group.get('setting_match', True), min(required, len(speakers)), capacity(shots)
    pinned = route.choices[scene['scene_id']].get('coverage_group', {})
    group = next((g for g in report['groups'] if pinned.get('observation_key') == key and
                  pinned.get('group_id') == g['group_id']), None)
    group = group or max(report['groups'], key=score, default=None)
    selected = [dict(by_id[i], conversation_id=group['group_id']) for i in group['slot_ids']] if group else []
    slots = [dict(s, role_id=s['speaking'][0]) for s in selected if len(s['speaking']) == 1]
    bind_slot_roles(scene, slots)
    scene.update(coverage_shots=selected, speech_slots=slots, reaction_shots=[], unconfirmed_slots=[], conversation=group)
    check.update(single_location=bool(group),
                 scene=group['description'] if group else '未找到实际连续事件')
    if group and 'setting_match' in group:
        check.update(setting_match=group['setting_match'], setting_reason=group['setting_reason'])
    scene['picture'] = deepcopy(check)
    report.update(scene_id=scene['scene_id'], speech_slots=slots, coverage_shots=selected,
                  capacity_s=capacity(selected), sufficient=sufficient(scene))
    return report
