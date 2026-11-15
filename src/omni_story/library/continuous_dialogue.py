"""Find and redub one continuous conversation in the published material library."""
from collections import defaultdict
from copy import deepcopy
from itertools import combinations
import math
from pathlib import Path
import re

from .autonomous_material import Discovery, dump
from .joint_dialogue import FPS, place_audio
from .state import LibraryStopped, json_sha, write_json

POLICY = 'continuous_dialogue_v1'
_DIALOGUE = re.compile(r'问答|交谈|对话|开口|说话|讲话|提问|回答|听者|倾听|回应|嘴部.{0,5}开合')
_NO_DIALOGUE = re.compile(r'未见说话|未说话|无说话|无嘴部对白|唱歌|咀嚼')


def dialogue_evidence(records, role_count):
    """Rank by sustained interaction evidence. Index descriptions are only hints."""
    events = []
    pairs = set()
    for record in records:
        actors = frozenset(record.get('entity_ids') or ())
        if record['kind'] != 'event' or not 0 < len(actors) <= role_count+1:
            continue
        speech = record.get('speech') or ''
        action = record.get('action') or ''
        speaks = bool(_DIALOGUE.search(speech+' '+action) and not _NO_DIALOGUE.search(speech))
        events.append((record['source_start_s'], record['source_end_s'], actors, speaks))
        if speaks and len(actors) >= role_count:
            pairs.update(frozenset(c) for c in combinations(sorted(actors), role_count))
    best = (0., 0.)
    for pair in pairs:
        # Close-ups of a member of the group can continue a dialogue.
        spans = sorted((a,b) for a,b,actors,_ in events if actors <= pair)
        spoken = [(a,b) for a,b,actors,speaks in events if speaks and actors <= pair]
        runs = []
        for a,b in spans:
            if runs and a <= runs[-1][1]+1.0:
                runs[-1][1] = max(b,runs[-1][1])
            else:
                runs.append([a,b])
        for a,b in runs:
            merged = []
            for low,high in sorted((max(a,x), min(b,y)) for x,y in spoken if x<b and a<y):
                if merged and low <= merged[-1][1]:
                    merged[-1][1] = max(high,merged[-1][1])
                else:
                    merged.append([low,high])
            talking = sum(y-x for x,y in merged)
            best = max(best, (min(b-a, talking*2), talking))
    return best


def dialogue_turns(lines):
    turns = []
    for line in lines:
        if not turns or turns[-1]['role'] != line['role']:
            turns.append(dict(turn_id=f't{len(turns)+1}', role=line['role'], lines=[]))
        turns[-1]['lines'].append(line)
    return turns


def fit_continuous_frames(source_frames, speech_seconds, max_seconds):
    """Budget measured speech and in-scene pauses without dropping source frames."""
    minimum = [max(math.ceil(frames/1.3), math.ceil((seconds+.11)*FPS))
               for frames, seconds in zip(source_frames, speech_seconds)]
    if any(needed > frames/.55 for frames, needed in zip(source_frames, minimum)):
        raise ValueError('A turn needs more continuous footage for the measured speech')
    budget = math.floor(max_seconds*FPS+1e-6)
    if sum(minimum) > budget:
        raise ValueError('Measured speech and source movement exceed the edit limit')
    preferred = [max(original, needed) for original, needed in zip(source_frames, minimum)]
    if sum(preferred) <= budget:
        return preferred
    available = budget-sum(minimum)
    slack = [p-m for p,m in zip(preferred, minimum)]
    shares = [available*extra/sum(slack) for extra in slack]
    added = [math.floor(share) for share in shares]
    order = sorted(range(len(shares)), key=lambda i: -(shares[i]-added[i]))
    for i in order[:available-sum(added)]:
        added[i] += 1
    return [m+n for m,n in zip(minimum, added)]


def candidate_spans(windows, anchors, max_seconds):
    """Expand a matched window only through adjacent indexed windows of its source."""
    by_source = defaultdict(list)
    for window in windows.values():
        by_source[window['source_sha256']].append(window)
    for group in by_source.values():
        group.sort(key=lambda w: (w['start_s'], w['end_s']))
    spans = []
    for anchor in anchors:
        seed = windows[anchor]
        neighbors = by_source[seed['source_sha256']]
        left = right = next(i for i, w in enumerate(neighbors) if w['window_id'] == anchor)
        low, high = seed['start_s'], seed['end_s']
        while True:
            choices = []
            if left and neighbors[left-1]['end_s'] >= low - 1/FPS:
                choices.append((left-1, right))
            if right+1 < len(neighbors) and neighbors[right+1]['start_s'] <= high + 1/FPS:
                choices.append((left, right+1))
            choices = [(a, b) for a, b in choices
                       if max(high, neighbors[b]['end_s'])-min(low, neighbors[a]['start_s']) <= max_seconds]
            if not choices:
                break
            left, right = min(choices, key=lambda ab: abs(
                neighbors[ab[0]]['start_s'] + neighbors[ab[1]]['end_s'] - seed['start_s'] - seed['end_s']))
            low, high = min(low, neighbors[left]['start_s']), max(high, neighbors[right]['end_s'])
        if any(s['source_sha256'] == seed['source_sha256']
               and min(s['end_s'], high)-max(s['start_s'], low) > .65*min(high-low, s['end_s']-s['start_s'])
               for s in spans):
            continue
        spans.append(dict(candidate_id=f'c{len(spans)+1}', source_sha256=seed['source_sha256'],
                          start_s=low, end_s=high, windows=neighbors[left:right+1]))
    return spans


def validate_conversation(value, turns, duration_s):
    if not isinstance(value, dict) or value.get('decision') not in {'use', 'skip'}:
        raise ValueError('Return decision use or skip and the actual visual evidence')
    if not isinstance(value.get('evidence'), str) or not value['evidence'].strip():
        raise ValueError('Describe the actual location, interaction or reason to skip')
    if value['decision'] == 'skip':
        return value
    actors = value.get('cast')
    roles = {turn['role'] for turn in turns}
    if not isinstance(actors, dict) or set(actors) != roles or any(
            not isinstance(a, str) or not a.strip() for a in actors.values()):
        raise ValueError('Bind every speaking role to one observed appearance')
    if len(set(actors.values())) != len(roles):
        raise ValueError('Different speaking roles need distinct actual actors')
    rows = value.get('turns')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or [
            r.get('turn_id') for r in rows] != [t['turn_id'] for t in turns]:
        raise ValueError('Keep every original dialogue turn in order')
    anchored = set()
    previous = None
    for row, turn in zip(rows, turns):
        start, end = row.get('start_s'), row.get('end_s')
        if any(type(x) not in (float, int) or not math.isfinite(x) for x in (start, end)) or not (
                0 <= start < end <= duration_s + .001 and end-start >= .3):
            raise ValueError('Invalid interval for '+turn['turn_id'])
        if previous is not None and abs(start-previous) > 1/FPS+.001:
            raise ValueError('Intervals must touch and follow source order at '+turn['turn_id'])
        previous = end
        text = row.get('text')
        if not isinstance(text, str) or not text.strip() or not row.get('acting'):
            raise ValueError('Each turn needs new dialogue and observed action')
        if row.get('view') not in {'speaker', 'reaction'}:
            raise ValueError('Show the assigned speaker or their same-scene listener')
        if row['view'] == 'speaker':
            anchored.add(turn['role'])
        terms = {t for line in turn['lines'] for t in line.get('verbatim_terms', [])}
        if any(t not in text for t in terms):
            raise ValueError('Missing protected wording in '+turn['turn_id'])
    if anchored != roles:
        raise ValueError('Every role needs at least one actual speaking shot')
    return value


class ContinuousDialogue(Discovery):
    def candidates(self):
        windows = {}
        for wid, rows in self.records.items():
            row = rows[0]
            start, end = row['window_source_start_s'], row['window_source_end_s']
            if not self.route._allowed_record(dict(row, source_start_s=start, source_end_s=end)):
                # A boundary window may include an excluded lead-in or tail.
                # Its usable indexed range can still join an adjacent window.
                start = min(r['source_start_s'] for r in rows)
                end = max(r['source_end_s'] for r in rows)
                if not self.route._allowed_record(dict(row, source_start_s=start, source_end_s=end)):
                    continue
            windows[wid] = dict(window_id=wid, source_sha256=row['source_sha256'], start_s=start, end_s=end,
                                summary=row.get('window_summary', '')[:155])
        queries = ['两人同框 面对面 问答 交谈 说话 倾听 反应',
                   '同一场景 连续对话 看向对方 轮流 开口 双人 正反打',
                   '桌边 柜台 讨论 解释 提问 回答 手势']
        queries.extend(' '.join(str(scene.get(k, '')) for k in ('setting', 'interaction'))[:150]
                       for scene in self.route.scenes)
        hits_by_query = []
        for i, query in enumerate(queries):
            hits = self.route.index.search(dict(need_id=f'conversation_{i}', query_text=query),
                channels=['bm25'], top_k=140, final_k=140)['hits']
            hits_by_query.append(list(dict.fromkeys(h['record']['window_id'] for h in hits
                                                   if h['record']['window_id'] in windows)))
        anchors = list(dict.fromkeys(w for rank in range(35) for hits in hits_by_query for w in hits[rank:rank+1]))
        retrieved = set(anchors)
        anchors.extend(w for w in windows if w not in retrieved)
        spans = candidate_spans(windows, anchors, max(22, min(75, self.route.duration*1.6)))
        role_count = len({line['role'] for line in self.route.lines})
        eligible = []
        for span in spans:
            if span['end_s']-span['start_s'] < self.route.duration*.63 or not self.route._allowed_record(
                    dict(observation_id='', source_sha256=span['source_sha256'],
                         source_start_s=span['start_s'], source_end_s=span['end_s'])):
                continue
            records = [r for w in span['windows'] for r in self.records[w['window_id']]]
            continuity, talking = dialogue_evidence(records, role_count)
            span.update(dialogue_hint_s=round(continuity,1), speech_hint_s=round(talking,1))
            eligible.append(span)
        return sorted(eligible, key=lambda s: (-s['dialogue_hint_s'], -s['speech_hint_s']))

    def run(self):
        from .subtitle_library import prepare_visible_window
        from .scene_coverage import detect_shots
        from .dialogue_cut import _speech, voices_for, SR

        turns = dialogue_turns(self.route.lines)
        if len({t['role'] for t in turns}) < 2:
            raise LibraryStopped('continuous_dialogue:reference_needs_multiple_speakers')
        briefs = [dict(turn_id=t['turn_id'], role=t['role'], text='，'.join(l['text'] for l in t['lines']),
                       required=[l.get('must_preserve') for l in t['lines']],
                       terms=list(dict.fromkeys(x for l in t['lines'] for x in l.get('verbatim_terms', []))))
                  for t in turns]
        pool = self.candidates()
        failures = []
        seen = set()
        for search in range(1, 4):
            cards = [c for c in pool if c['candidate_id'] not in seen][:20]
            if not cards:
                break
            ids = {c['candidate_id']: c for c in cards}
            prompt = ('你为对白短片检索素材。优先找一处地点、同一组人物在相邻镜头实际问答的长段落，'
                      '地点和职业可迁移，重点是人物面对面互动、讲话与反应、时间足够。'
                      '候选卡是相邻原片窗口的粗索引，seconds包含前后文，dialogue_hint_s是有相同人物互动证据的时长提示。'
                      '优先选有足够长的实际问答和反应的片段。职业、物品、地点相似度不能替代对话连续性。'
                      '这是重新配音，原对白和新台词内容可以不同。排除换场、跳集、打斗、唱歌、独白。'
                      '选最值得实际看片的最多4段，按优先级返回'
                      '{"ranked":["c1"],"reason":"选择这些候选的依据"}。\n' +
                      dump(dict(turns=briefs, duration_s=self.route.duration, failures=failures[-5:],
                                candidates=[dict(id=c['candidate_id'], seconds=round(c['end_s']-c['start_s'],1),
                                                 dialogue_hint_s=c['dialogue_hint_s'],
                                                 summaries=[w['summary'] for w in c['windows']]) for c in cards])))
            def rank(value):
                order = value.get('ranked') if isinstance(value, dict) else None
                if not isinstance(order, list) or not 1 <= len(order) <= 4 or any(
                        not isinstance(i, str) or i not in ids for i in order) or len(set(order)) != len(order):
                    raise ValueError('Choose 1–4 unique supplied candidate IDs')
                return value
            chosen = self.call(f'material_scene_discovery_decide_{search}', prompt, rank)
            write_json(self.root/f'continuous_search_{search}.json', dict(cards=cards, ranking=chosen))
            for candidate_id in chosen['ranked']:
                seen.add(candidate_id)
                candidate = ids[candidate_id]
                source = self.route.sources[candidate['source_sha256']]
                start, end = candidate['start_s'], candidate['end_s']
                clip = prepare_visible_window(source, start, end, self.root/'continuous_media', candidate_id)
                cuts = detect_shots(source, start, end, self.root/'cuts')
                instruction = (
                    '这是用原画面重新创作配音。原片字幕、职业和原对白内容无需与新故事相同；'
                    'terms只约束你写的新配音text，不能因原片没出现这些词而跳过。'
                    '用同一场所的对话表演和听者反应承接新台词。顶端LOCAL时钟是本视频相对秒。'
                    '先确认同一场所、同一组人实际对话，选定范围内不能有标题、换地点、换人或回放。'
                    '可以在原有机位间自然切换。短暂听者反应允许；看到其他人物正在讲话不能当成听者。'
                    '每个故事角色至少一次有实际讲话表演的镜头；静止张嘴、唱歌、吃东西不算。'
                    '按turns顺序分配相接的区间，上句end_s等于下句start_s，中间没有遗漏或重叠。'
                    '保持每轮问答和必需信息，保护terms原词。台词口语简练，按可见表演长度控制在约每秒4至5字。'
                    '情节可用同场表情和问答承接，不能声称看见画面中没有的动作。'
                    '实际找不到连续段就返回{"decision":"skip","evidence":"具体缺口"}。找到时只返回JSON '
                    '{"decision":"use","evidence":"实际场景、同一组人物及连续对话的证据",'
                    '"cast":{"故事角色":"清晰可见且独特的实际外观"},'
                    '"turns":[{"turn_id":"t1","start_s":0,"end_s":3,"text":"新台词",'
                    '"view":"speaker或reaction","acting":"实际可见讲话者/听者动作与对象"}]}。\n' +
                    dump(dict(turns=briefs, duration_s=clip['duration_s'], cut_times_s=[round(x,3) for x in cuts],
                              target_duration_s=self.route.duration)))
                try:
                    answer = self.call(f'material_scene_discovery_observe_{len(seen)}', instruction,
                                       lambda value: validate_conversation(value, turns, clip['duration_s']), clip['path'])
                    if answer['decision'] == 'skip':
                        failures.append(dict(candidate_id=candidate_id, reason=answer['evidence']))
                        continue
                    self.bind(candidate, clip, answer, turns)
                    self.voices = voices_for(self.locked)
                    self.samples = {line['line_id']: _speech(line, self.voices[line['role']], self.state,
                                                           Path(self.state.output))
                                    for line in self.lines}
                    if any(not len(s) for s in self.samples.values()):
                        raise ValueError('The selected dialogue contains a silent synthesized turn')
                    self.frame_budget = fit_continuous_frames(
                        [line['source_frames'] for line in self.lines],
                        [len(self.samples[line['line_id']])/SR for line in self.lines],
                        self.route.edit_max_s)
                except ValueError as error:
                    failures.append(dict(candidate_id=candidate_id, reason=str(error)[:350]))
                    write_json(self.root/'continuous_rejections.json', failures)
                    continue
                write_json(self.root/'continuous_selection.json',
                           dict(candidate=candidate, clip=clip, model_choice=answer, rejected=failures))
                self.state.set_artifact('material_lock', self.locked)
                write_json(Path(self.state.output)/'material_lock.json', self.locked)
                return self.selected_windows, self.locked
        raise LibraryStopped('continuous_dialogue:no_confirmed_conversation:'+dump(failures[-5:]))

    def bind(self, candidate, clip, answer, turns):
        # All render intervals live in the verified continuous proxy, with
        # subtitle regions inherited from its overlapping indexed windows.
        scene = deepcopy(self.route.scenes[0])
        window = dict(clip, observation=dict(roles=[], events=[]))
        regions = {}
        for member in candidate['windows']:
            original = self.load_window(member['window_id'])
            for treatment in original.get('source_subtitle_treatments', []):
                regions[json_sha(treatment)] = treatment
        window['source_subtitle_treatments'] = list(regions.values())
        bindings = {}
        for i, (role, appearance) in enumerate(answer['cast'].items(), 1):
            actor = f'actor_{i}'
            binding = dict(window_id=window['window_id'], role_id=actor)
            bindings[role] = dict(binding, actor=actor, entity_id=actor, appearance=appearance, gender='unclear')
            window['observation']['roles'].append(dict(role_id=actor, appearance=appearance))
        scene.update(window_ids=[window['window_id']], roles=list(bindings), role_bindings=bindings,
                     reference_lines=deepcopy(self.route.lines), picture=dict(scene=answer['evidence']), speech_slots=[])
        self.selected_windows = [window]
        self.locked = dict(editing_policy=POLICY, scenes=[scene], edit_max_s=self.route.edit_max_s,
                           cast_bindings={role: dict(gender='unclear', entity_bindings={
                               b['actor']: [{k:b[k] for k in ('window_id', 'role_id')}]
                           }) for role, b in bindings.items()})
        previous = None
        self.lines = []
        for turn, row in zip(turns, answer['turns']):
            a = previous if previous is not None else math.ceil((clip['source_start_s']+row['start_s'])*FPS)
            b = math.floor((clip['source_start_s']+row['end_s'])*FPS+1e-6)
            if b <= a:
                raise ValueError('Continuous interval has no usable source frames')
            previous = b
            self.lines.append(dict(line_id=turn['turn_id'], id=turn['turn_id'], scene_id=scene['scene_id'],
                role=turn['role'], text=row['text'], mode='dialogue', picture_relation=row['view'],
                observed_action=row['acting'], source_start_s=a/FPS, source_frames=b-a,
                speaker={k: bindings[turn['role']][k] for k in ('window_id', 'role_id')},
                reference_line_ids=[l['line_id'] for l in turn['lines']],
                addressee_roles=list(dict.fromkeys(r for l in turn['lines'] for r in l.get('addressee_roles', [])))))

    def prepare(self, cache, output):
        from .dialogue_cut import _Timeline
        timeline = _Timeline(self.selected_windows)
        scene = self.locked['scenes'][0]
        anchored = set()
        for line, frames in zip(self.lines, self.frame_budget):
            role = line['role']
            line['visual_anchor'] = line['picture_relation'] == 'speaker' and role not in anchored
            if line['visual_anchor']:
                anchored.add(role)
            slot = dict(window_id=self.selected_windows[0]['window_id'], role=role)
            row = timeline.append(scene, slot, line['source_start_s'], frames, line['picture_relation'], line['line_id'])
            row.update(source_out_s=line['source_start_s']+line['source_frames']/FPS,
                       speed=line['source_frames']/frames, observed_action=line['observed_action'])
        audio, captions = place_audio(self.lines, timeline.rows, self.samples, cache, POLICY)
        script = dict(facts=[dict(line_id=l['line_id'], text=l['text'], requirement=l.get('must_preserve'))
                             for l in self.route.lines], lines=self.lines)
        write_json(Path(output)/'dialogue_script.json', script)
        return self.locked, self.selected_windows, self.voices, script, timeline, self.lines, audio, captions
