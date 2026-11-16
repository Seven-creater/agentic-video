"""GLM-directed search/inspection; immutable observations, finite edit contracts.

This lane reads a published index and writes only into the creative task. The
model chooses pictures and meanings; code owns identity keys and frame counts.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

from .state import LibraryStopped, json_sha, write_json
from .material_plan import POLICY, FPS, validate_cast, allocate, validate_allocation, capacity_preflight
from .material_evidence import VisualIdentityRegistry, overlap_conflicts, exclude_conflicts


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def normalize_observation_fields(value):
    """Resolve redundant field names/visible targets without inventing evidence."""
    if not isinstance(value,dict):return value
    value=deepcopy(value)
    actors=value.get('confirmed_actors')
    for actor in actors if isinstance(actors,list) else []:
        if isinstance(actor,dict) and not actor.get('evidence') and isinstance(actor.get('identity_hint'),str):
            actor['evidence']=actor['identity_hint']
    segments=value.get('segments')
    for row in segments if isinstance(segments,list) else []:
        if not isinstance(row,dict) or not isinstance(row.get('visible'),list) or not isinstance(row.get('addressee_ids'),list):continue
        events=row.get('events')
        for event in events if isinstance(events,list) else []:
            if not isinstance(event,dict) or not isinstance(event.get('addressee_ids'),list):continue
            # The model already recorded this interaction and the actual target
            # in the frame. Off-screen targets still need independent evidence.
            for actor in event['addressee_ids']:
                if actor in row['visible'] and actor not in row['addressee_ids']:
                    row['addressee_ids'].append(actor)
    return value


def navigation_history(history):
    """Keep search results, but never duplicate the watched evidence payload."""
    rows=[]
    for entry in history[-5:]:
        result=entry['result']
        if entry['action']['action']=='inspect':
            result=dict(inspected_windows=[o['window_id'] for o in result['inspections']])
        rows.append(dict(turn=entry['turn'],action=entry['action'],result=result))
    return rows


class Discovery:
    def __init__(self, route):
        self.route, self.state, self.glm = route, route.state, route.glm
        self.root = Path(self.state.output)/'autonomous_material'
        self.root.mkdir(parents=True, exist_ok=True)
        self.records = {wid: [r for r in rows if route._allowed_record(r)]
                        for wid, rows in route.index.records_by_window.items()}
        self.records = {wid: rows for wid, rows in self.records.items() if rows}
        self.history, self.seen, self.observations, self.windows, self.actors = [], set(), {}, {}, {}
        self.cast, self.selection = None, None
        self.budget = min(64, max(24, math.ceil(route.duration/3)+3*len(self.roles)))
        self.max_rounds = self.budget+12
        self.shots = []
        self.raw_shots, self.conflicts = [], []
        self.identities = VisualIdentityRegistry(self.root,self.call)
        self.feedback = []
        self.allow_static_reuse=False

    @property
    def roles(self):
        return list(dict.fromkeys(r for s in self.route.scenes for r in s['roles']))

    def call(self, name, prompt, validate, media=None, *, image=False):
        from .pipeline import _material_call
        validate.repair_limit = 2
        return _material_call(self.state, self.glm, name, prompt, validate, media, image=image)

    def search(self, need):
        return self.route.index.search(need, channels=['bm25'], top_k=200, final_k=200)

    def validate_selection(self, value):
        """Additional route constraints are checked before the cast is locked."""

    def search_scene_context(self, scene):
        return dict(scene,reference_lines=[{k:r.get(k) for k in
            ('line_id','role','text','function','on_screen','addressee_roles')}
            for r in self.route.lines if r['line_id'] in scene['line_ids']])

    def search_reference_context(self):
        return {k:self.route.reference.get(k) for k in ('theme','intended_takeaway')}

    def identity_window(self, window, proxy, source):
        return window

    def inspection_proxy(self, window, source):
        from .material_confirm import confirm_clip
        return confirm_clip(source,window['source_start_s'],window['source_end_s'],
                            self.root/'media',pad_s=0,subtitle_masks=None)

    def inspection_instructions(self):
        return '只观察原动画，不知道新剧本和选角。原字幕可帮助理解原场景，但不能证明动作、嘴动或身份。'

    def verified_inspection(self, window, proxy, shots):
        return shots

    def creative_search_rules(self):
        return ''

    def search_target_duration(self):
        return self.route.duration

    def decision_evidence(self):
        return {}

    def normalize_identities(self, shots):
        result=deepcopy(shots)
        resolve=self.identities.resolve
        for shot in result:
            for key in ('visible','speaking','clear_face','addressee_ids','listeners'):
                shot[key]=list(dict.fromkeys(resolve(a) for a in shot.get(key,[])))
            shot['local_actor_bindings']={resolve(a):r for a,r in shot['local_actor_bindings'].items()}
            for event in shot['events']:
                for key in ('subject_ids','addressee_ids'):
                    event[key]=list(dict.fromkeys(resolve(a) for a in event[key]))
        return result

    def reconcile_actors(self):
        actors={}
        for old,info in self.actors.items():
            aid=self.identities.resolve(old)
            if aid not in actors:actors[aid]=dict(info,actor_id=aid,bindings=list(info['bindings']))
            else:actors[aid]['bindings'].extend(b for b in info['bindings'] if b not in actors[aid]['bindings'])
        self.actors=actors
        if self.cast:
            self.cast={role:self.identities.resolve(a) for role,a in self.cast.items()}
            validate_cast(self.cast,self.roles,self.actors)

    def card(self, wid, matched_ids=()):
        rows = self.records[wid]
        events=[r for r in rows if r['kind']=='event']
        priority={ident:i for i,ident in enumerate(matched_ids)}
        events.sort(key=lambda r:priority.get(r['record_id'],len(priority)))
        return dict(window_id=wid, source_start_s=min(r['source_start_s'] for r in rows),
            source_end_s=max(r['source_end_s'] for r in rows),
            summary=next((r['window_summary'] for r in rows if r.get('window_summary')), ''),
            preview_only=True,event_count=len(events),
            events=[{k:r.get(k) for k in ('source_start_s','source_end_s','who','action','expression','place','speech')}
                    for r in events[:3]])

    def load_window(self, wid):
        from .pipeline import _received_library_window
        if wid not in self.windows:
            row = self.records[wid][0]
            self.windows[wid] = deepcopy(_received_library_window(self.route.store.get(row['observation_id']),
                self.route.root, source=self.route.sources[row['source_sha256']],
                revision_ids=self.route.revisions, normalize=True))
        return self.windows[wid]

    def inspect(self, wid):
        from .material_confirm import confirm_clip
        from .scene_coverage import detect_shots
        window = self.load_window(wid)
        source = self.route.sources[window['source_sha256']]
        start, end = window['source_start_s'], window['source_end_s']
        proxy = self.inspection_proxy(window,source)
        cuts = detect_shots(source, start, end, self.root/'cuts')
        bounds = [0, *cuts, end-start]
        cut_rows = [dict(cut_id=f'c{i+1}', start_s=round(a,6), end_s=round(b,6))
                    for i,(a,b) in enumerate(zip(bounds,bounds[1:])) if b-a>=1/FPS]
        identity_window=self.identity_window(window,proxy,source)
        identities=self.identities.bind(identity_window,protected=(self.cast or {}).values())
        self.reconcile_actors()
        actor_to_local={aid:row['role_id'] for aid,row in identities.items()}
        actor_kinds={aid:row['entity_kind'] for aid,row in identities.items()}
        hints=[dict(actor_id=aid,appearance=row['appearance'],identity_hint=row['evidence'])
               for aid,row in identities.items()]
        prompt = (self.inspection_instructions()+
            '输入cuts是程序检测的真实镜头边界；逐一观察，可在cut内细分连续状态，不能跨cut。'
            'actor_hints是索引身份假设，必须据画面核实；外观不符则不确认，绝不把人物硬套进ID。'
            'speech仅在确见说话表演时列actor_id：动画可用一次清晰的张嘴/闭嘴变化配合说话姿态，'
            '不要求真人连续口型；静止张嘴、惊讶、进食咀嚼不是讲话证据。不确定用空数组并记录原因。'
            '区间应完整支持填写的事实，人物进出、嘴动或对象改变则细分。clear_face列看得清嘴部的角色。'
            'events必须结构化记动作的subject_ids与实际被对着说话/交互的addressee_ids，无对象时为空。'
            '每个有可见人物的story段至少列一条实际动作或静态姿势event，不能只在action写事实却把events留空。'
            '对象不在当前镜头时，只能引用同一连续对话中已建立的对象，并用addressee_evidence说明；不确定就不填对象。'
            '不要猜职业或故事角色。标题、过场、片尾也记录kind，不能当剧情镜头。'
            '返回JSON {"confirmed_actors":[{"actor_id":"...","appearance":"实际可见外观","evidence":"身份依据"}],'
            '"segments":[{"cut_id":"c1","start_s":0,"end_s":1,"kind":"story/title/transition/credits/uncertain",'
            '"visible":[],"speaking":[],"clear_face":[],"speech_evidence":"嘴部实际变化或无",'
            '"addressee_ids":[],"addressee_evidence":"对象的实际依据或空串","place":"可辨认地点","action":"客观动作",'
            '"events":[{"action":"具体动作","subject_ids":[],"addressee_ids":[]}]}],"uncertainties":[]}。'
            '所有秒数是本片段相对时间。只列实际核实的actor_id，不创造ID。'
            '每个segment的cut_id恰好是输入cuts中的一个id（如c5），不能写c1-c6这样的区间；多个cut各写一个segment，可省略重复论述但不能合并。'
            'visible/speaking/clear_face/addressee_ids与events里的ID只能取自本回复confirmed_actors；'
            '画面里另有看得清但无法核实为actor_hints的人物时，不要写进这些数组或自造标签，在action里客观描述并在uncertainties说明。\n'+dump(dict(
                duration_s=end-start, cuts=cut_rows, actor_hints=hints)))
        if self.allow_static_reuse:
            prompt += ('\n每个segment另给loopable布尔值和loopable_reason。仅人物原地讲话/倾听、姿态基本不变、'
                '没有物件状态变化的区间可为true，供新对白重复使用。走动、进出、拿放或使用物品、'
                '攻击、镜头移动等不可逆动作必须false；不确定也为false。')
        cut_map = {r['cut_id']:r for r in cut_rows}
        def validate(value):
            normalized=normalize_observation_fields(value)
            if isinstance(value,dict):
                value.clear();value.update(normalized)
            if not isinstance(value,dict) or not isinstance(value.get('segments'),list) or not value['segments']:
                raise ValueError('observation requires segments')
            confirmed = value.get('confirmed_actors', [])
            if not isinstance(confirmed,list) or any(not isinstance(p,dict) for p in confirmed):
                raise ValueError('confirmed_actors must be an array of observed people')
            aids = [p.get('actor_id') for p in confirmed]
            if len(aids)!=len(set(aids)) or any(a not in actor_to_local for a in aids):
                raise ValueError('unknown/duplicate confirmed actor')
            if any(not p.get('appearance') or not p.get('evidence') for p in confirmed):
                raise ValueError('confirmed actor needs actual appearance evidence')
            ranges = []
            for row in value['segments']:
                cut = cut_map.get(row.get('cut_id'))
                a,b = row.get('start_s'),row.get('end_s')
                if cut is None:
                    raise ValueError(f'unknown cut_id {row.get("cut_id")!r}: use exactly one input cut id per segment '
                        f'(c1..c{len(cut_rows)}), never a range; write one segment for each cut')
                if not all(type(x) in (int,float) and math.isfinite(x) for x in (a,b)):
                    raise ValueError(f'{row["cut_id"]}: start_s/end_s must be numbers inside the cut {dump(cut)}')
                if not cut['start_s']-.001 <= a < b <= cut['end_s']+.001:
                    raise ValueError('segment outside detected cut: '+dump(cut))
                if any(a < y-.001 and x < b-.001 for x,y in ranges):
                    raise ValueError('observation segments overlap')
                ranges.append((a,b))
                for key in ('visible','speaking','clear_face','addressee_ids'):
                    if not isinstance(row.get(key),list):
                        raise ValueError(f'{row["cut_id"]}: {key} must be an array of confirmed actor IDs')
                    unknown=[x for x in row[key] if x not in aids]
                    if unknown:
                        raise ValueError(f'segment contains unconfirmed actor: {key}; {row["cut_id"]} lists {unknown} '
                            f'but only confirmed_actors {aids} may appear. Remove them from {key}; describe an '
                            'unverified visible person in action and uncertainties instead of inventing a label')
                if not set(row['speaking']+row['clear_face']).issubset(row['visible']):
                    raise ValueError('speech/face actor is not visible')
                if row['speaking'] and not row.get('speech_evidence'):
                    raise ValueError('speaking requires visual evidence')
                if row.get('kind') not in {'story','title','transition','credits','uncertain'}:
                    raise ValueError('invalid content kind')
                if self.allow_static_reuse and (type(row.get('loopable')) is not bool or not row.get('loopable_reason')):
                    raise ValueError('State whether each segment is safely reusable in place and give visible evidence')
                if not isinstance(row.get('events'),list):raise ValueError('events required')
                if row['kind']=='story' and row['visible'] and not row['events']:
                    raise ValueError('visible story action/pose must be recorded as a structured event')
                if set(row['addressee_ids'])-set(row['visible']) and not row.get('addressee_evidence'):
                    raise ValueError('offscreen addressee needs evidence from the continuous dialogue')
                for event in row['events']:
                    if not event.get('action'):raise ValueError('event action missing')
                    for key in ('subject_ids','addressee_ids'):
                        allowed=row['visible'] if key=='subject_ids' else row['addressee_ids']
                        if not isinstance(event.get(key),list) or any(x not in allowed for x in event[key]):
                            raise ValueError(f'{row["cut_id"]}: event {key}={event.get(key)} must be among observed IDs {allowed}; do not invent a visible subject or off-screen target')
            return value
        # Number every observation call: re-inspecting a window for a new candidate
        # does not grow self.observations, so its size cannot name the next call.
        self.observe_calls=getattr(self,'observe_calls',0)+1
        observed = self.call(f'material_scene_discovery_observe_{self.observe_calls}',prompt,validate,proxy['path'])
        for actor in observed['confirmed_actors']:
            aid=actor['actor_id']
            info=self.actors.setdefault(aid,dict(actor_id=aid,appearance=actor['appearance'],entity_kind=actor_kinds[aid],bindings=[]))
            info['bindings'].append(dict(window_id=wid,role_id=actor_to_local[aid],
                library_entity_ids=identities[aid]['entity_ids'],local_role_ids=identities[aid]['local_role_ids']))
        selected=[]
        for n,row in enumerate(observed['segments']):
            if row['kind']!='story':continue
            a=math.ceil((start+row['start_s'])*FPS-1e-6)/FPS
            b=math.floor((start+row['end_s'])*FPS+1e-6)/FPS
            if b-a<.3:continue
            slot='shot_'+json_sha([wid,n,POLICY])[:12]
            shot=dict(row,slot_id=slot,window_id=wid,source_sha256=source['sha256'],
                source_start_s=a,source_end_s=b,listeners=[p for p in row['visible'] if p not in row['speaking']],
                events=[dict(e,event_id=slot+'_event_'+str(i+1)) for i,e in enumerate(row['events'])],
                loopable=bool(self.allow_static_reuse and row.get('loopable')),local_actor_bindings=actor_to_local)
            selected.append(shot)
        selected=self.verified_inspection(window,proxy,selected)
        previous=self.normalize_identities(self.raw_shots)
        selected=self.normalize_identities(selected)
        conflicts=overlap_conflicts(previous,selected)
        self.conflicts.extend(conflicts)
        self.raw_shots=previous+selected
        self.shots=exclude_conflicts(self.raw_shots,self.conflicts)
        if conflicts:
            self.feedback.append(dict(problem='重叠源帧的看片结果存在人物/讲话/脸部可见性冲突，冲突区间已隔离，不能拿来排片。',
                intervals=[{k:c[k] for k in ('start_s','end_s','fields')} for c in conflicts]))
        write_json(self.root/'observation_conflicts.json',self.conflicts)
        for prior_id,prior in self.observations.items():
            prior['shots']=[s for s in self.shots if s['window_id']==prior_id]
            write_json(self.root/(prior_id+'.json'),prior)
        selected=[s for s in self.shots if s['window_id']==wid]
        self.observations[wid]=dict(window_id=wid,proxy=proxy,observed=observed,shots=selected)
        write_json(self.root/(wid+'.json'),self.observations[wid])
        return dict(window_id=wid,confirmed_actors=observed['confirmed_actors'],shots=selected,
                    uncertainties=observed.get('uncertainties',[]))

    def explore(self, feedback=None):
        if feedback:self.feedback.append(feedback)
        rules=('你是二创视频选材导演。先思考参考真正需要观众看见什么，再自己检索、看片、选角。'
            '地点/物件可合理替换，保留因果、角色关系、问答和包袱。不得以字幕推断嘴动。'
            '参考中的职业与原话并不是动画索引词。先把内容转成可见的互动功能、动作和情绪，再查询；'
            '例如询问、拒绝、解释、赞同、担忧、回应可以由不同日常场景表达，不能把所有查询都写成同一职业场所的近义词。'
            '每轮只决定下一步工具。候选卡只是简短索引预览，先实际inspect有希望的片段，勿在看片前推演完整排片。'
            '首次search至少覆盖两个维度：interaction/action/reaction/setting；每条query说明dimension和need。'
            '初选不足只代表当前候选不足，必须换词/换画面功能扩展检索；不能推断整个库缺素材。'
            '优先找到同一组演员在一个连贯场景中的对话与反应；可用有明确作用的插入镜头，'
            '不要每句换无关地点，不要把原片表演硬说成新故事动作。需要更多连贯画面可neighbors。'
            'search返回粗索引，必须inspect后才可选用。所有演员ID在不同窗口保持同一身份，'
            'cast_locked非空后不能换角。新观察的events是可声明故事动作的唯一依据。'
            '非loopable镜头只能播放一次；容量按真实配音算，不能宣称够了就绕过校验，不能重复背影凑时长。'
            '原本露脸对白的角色在锁角前必须找到该演员实际讲话的镜头；静态面甲、吃东西或单纯背影不能建立露脸发言身份。'
            '每轮返回一个JSON动作：'
            '{"action":"search","visual_needs":["需要的可见信息"],"queries":[{"text":"自拟检索词",'
            '"dimension":"interaction/action/reaction/setting四选一","need":"具体画面作用"}],"reason":"为什么找"}（最多4条query），'
            '或{"action":"neighbors","window_id":"已见ID","radius_s":45,"reason":"为什么找相邻"}，'
            '或{"action":"inspect","window_ids":["最多3个已见但未看片ID"],"reason":"看片目的"}，'
            '或{"action":"finish","cast":{"故事角色":"actor_id"},"scene_windows":{"scene_id":["实际看片的window_id"]},'
            '"continuity_plan":"主场景与少量插入镜头怎么连接","reason":"哪些画面满足哪些要求"}。'
            '不足时继续找；feedback是程序/独立审核发现的真实缺口，先补缺口。'
            '预算用完可{"action":"insufficient","reason":"具体缺什么"}，不能伪造完成。')
        rules += self.creative_search_rules()
        while self.max_rounds is None or len(self.history)<self.max_rounds:
            turn=len(self.history)+1
            # Compact past search cards, retain all watched evidence and current gaps.
            summary=navigation_history(self.history)
            context=dict(reference=self.search_reference_context(),
                scenes=[self.search_scene_context(s) for s in self.route.scenes],
                target_duration_s=self.search_target_duration(),cast_locked=self.cast,
                actors={a:{k:v for k,v in info.items() if k!='entity_kind'} for a,info in self.actors.items()},
                watched_shots=self.shots,history=summary,feedback=self.feedback[-3:],
                indexed_windows=len(self.records),candidate_windows_seen=len(self.seen),
                inspected=len(self.observations),inspection_budget=self.budget,round=turn,round_budget=self.max_rounds)
            context.update(self.decision_evidence())
            def validate(value):
                if not isinstance(value,dict):raise ValueError('action must be an object')
                action=value.get('action')
                if action=='search':
                    qs=value.get('queries',[])
                    if (not 1<=len(qs)<=4 or any(not isinstance(q,dict) or not isinstance(q.get('text'),str) or not q['text'].strip()
                        or q.get('dimension') not in {'interaction','action','reaction','setting'} or not q.get('need') for q in qs)):
                        raise ValueError('queries need text, dimension and concrete visual need')
                    if turn==1 and len({q['dimension'] for q in qs})<2:
                        raise ValueError('initial search must explore at least two visual dimensions, not only the literal setting')
                elif action=='neighbors':
                    if value.get('window_id') not in self.seen:raise ValueError('unknown neighbor window')
                elif action=='inspect':
                    ids=value.get('window_ids',[])
                    if (not 1<=len(ids)<=3 or len(set(ids))!=len(ids) or any(w not in self.seen or w in self.observations for w in ids)
                        or len(self.observations)+len(ids)>self.budget):raise ValueError('invalid inspection IDs or exhausted budget')
                elif action=='finish':
                    validate_cast(value.get('cast'),self.roles,self.actors)
                    if self.cast is not None and value['cast']!=self.cast:raise ValueError('cast locked; search for these actors')
                    mapping=value.get('scene_windows',{})
                    if set(mapping)!={s['scene_id'] for s in self.route.scenes}:raise ValueError('all scenes require windows')
                    for ids in mapping.values():
                        if not ids or any(w not in self.observations for w in ids):raise ValueError('scene needs actually inspected windows')
                    selected={w for ids in mapping.values() for w in ids}
                    for role,aid in value['cast'].items():
                        visible_voice=any(r['role']==role and r.get('on_screen') is not False for r in self.route.lines)
                        if visible_voice and not any(s['window_id'] in selected and set(s['speaking'])=={aid} for s in self.shots):
                            raise ValueError('cannot lock a visible dialogue role without observed speech: '+role+'; inspect/search another candidate before finishing')
                    if not value.get('continuity_plan'):raise ValueError('continuity plan required')
                    self.validate_selection(value)
                elif action!='insufficient':raise ValueError('unknown action')
                return value
            action=self.call(f'material_scene_discovery_decide_{turn}',rules+'\n'+dump(context),validate)
            result={}
            if action['action']=='search':
                searches=[]
                for q in action['queries']:
                    search=self.search(dict(need_id=f'discovery_{turn}',query_text=q['text']))
                    hits=search['hits']
                    ids=[]
                    for hit in hits:
                        w=hit['record']['window_id']
                        if w in self.records and w not in self.seen and w not in ids:ids.append(w)
                        if len(ids)>=6:break
                    self.seen.update(ids);searches.append(dict(query=q['text'],dimension=q['dimension'],need=q['need'],
                        channels_used=search.get('channels_used'),channels_skipped=search.get('channels_skipped'),
                        candidates=[self.card(w,[h['record_id'] for h in hits if h['record']['window_id']==w]) for w in ids]))
                result=dict(searches=searches)
            elif action['action']=='neighbors':
                wid=action['window_id'];span=self.card(wid);sha=self.records[wid][0]['source_sha256']
                radius=min(120,max(1,float(action.get('radius_s',45))))
                ids=[w for w in self.records if w not in self.seen and self.records[w][0]['source_sha256']==sha
                     and self.card(w)['source_start_s']<span['source_end_s']+radius
                     and self.card(w)['source_end_s']>span['source_start_s']-radius]
                ids.sort(key=lambda w:abs(self.card(w)['source_start_s']-span['source_start_s']))
                ids=ids[:10];self.seen.update(ids);result=dict(candidates=[self.card(w) for w in ids])
            elif action['action']=='inspect':
                result=dict(inspections=[self.inspect(w) for w in action['window_ids']])
            repeated_empty=(action['action'] in {'search','neighbors'} and
                not any(s.get('candidates') for s in result.get('searches',[])) and not result.get('candidates') and
                any(old['action']==action and old['result']==result for old in self.history))
            self.history.append(dict(turn=turn,action=action,result=result))
            write_json(self.root/'search_history.json',self.history)
            if repeated_empty:raise LibraryStopped('autonomous_material_repeated_search_without_new_evidence')
            if action['action']=='finish':
                self.cast=action['cast'];self.selection=action
                return
            if action['action']=='insufficient':
                raise LibraryStopped('autonomous_material_insufficient:'+str(action.get('reason')))
        raise LibraryStopped('autonomous_material_search_budget_exhausted')

    def lock(self):
        scenes=[];bindings={}
        for role,aid in self.cast.items():
            actor=self.actors[aid]
            bindings[role]=dict(actor=aid,gender='unclear',entity_ids=[aid],
                bindings=actor['bindings'],entity_bindings={aid:actor['bindings']})
        windows={}
        for scene in self.route.scenes:
            ids=self.selection['scene_windows'][scene['scene_id']]
            shots=[deepcopy(s) for s in self.shots if s['window_id'] in ids]
            roles={}
            for role in scene['roles']:
                aid=self.cast[role];actor=self.actors[aid]
                observed=next((b for b in actor['bindings'] if b['window_id'] in ids),None)
                if observed is None:raise ValueError('scene lacks locked actor: '+role)
                # Global IDs in planning; local IDs are restored in the render rows.
                roles[role]=dict(window_id=observed['window_id'],role_id=aid,entity_id=aid,
                    actor=aid,appearance=actor['appearance'],gender='unclear')
            for wid in ids:windows[wid]=self.windows[wid]
            scenes.append(dict(deepcopy(scene),window_ids=ids,coverage_shots=shots,
                role_bindings=roles,reference_lines=[deepcopy(r) for r in self.route.lines if r['line_id'] in scene['line_ids']],
                allowed_ranges=[dict(window_id=w,source_sha256=self.windows[w]['source_sha256'],
                    source_start_s=self.windows[w]['source_start_s'],source_end_s=self.windows[w]['source_end_s']) for w in ids],
                picture=dict(scene=self.selection['continuity_plan'],continuity_plan=self.selection['continuity_plan']),speech_slots=[],speech_ranges={}))
        lock=dict(scene_mode='scene_v1',editing_policy=POLICY,scenes=scenes,window_ids=list(windows),
                  cast_bindings=bindings,cast=deepcopy(self.cast),edit_max_s=self.route.edit_max_s,
                  reference_duration_s=self.route.duration,mismatches=[])
        return list(windows.values()),lock

    def run(self):
        if self.selection is None:self.explore()
        windows,lock=self.lock()
        self.state.set_artifact('material_lock',lock)
        write_json(Path(self.state.output)/'material_lock.json',lock)
        return windows,lock

    def write_script(self,lock):
        specs=[]
        for scene in lock['scenes']:
            groups=[]
            for ref in scene['reference_lines']:
                if (groups and groups[-1][-1]['role']==ref['role']
                        and ref['start_s']-groups[-1][-1]['end_s']<=.6):
                    groups[-1].append(ref)
                else:
                    groups.append([ref])
            for i,group in enumerate(groups):
                following=groups[i+1][0]['start_s'] if i+1<len(groups) else scene['end_s']
                specs.append(dict(line_id='d'+str(len(specs)+1),scene_id=scene['scene_id'],role=group[0]['role'],
                    reference_line_ids=[r['line_id'] for r in group],original_text='，'.join(r['text'] for r in group),
                    function=[r.get('function') for r in group],must_preserve=[r.get('must_preserve') for r in group],
                    verbatim_terms=list(dict.fromkeys(t for r in group for t in (r.get('verbatim_terms') or []))),
                    reference_offscreen=all(r.get('on_screen') is False for r in group),visual_anchor=False,
                    addressee_roles=list(dict.fromkeys(t for r in group for t in r.get('addressee_roles',[]))),
                    pause_after_s=min(.4,max(.08,following-group[-1]['end_s']))))
        prompt=('根据已核实参考与实际画面改编配音。保持每句说话人、信息、问答关系、反差，不删句、不换角、不编新的剧情因果。'
            '关键原词verbatim_terms逐字保留，其他物件可换成镜头中同作用的物件；原话已经合适就保留。'
            '说话自然简练，篇幅不超过原句，不把对话改成旁白。尚未发生的动作不能写成眼前已经发生。'
            '只返回JSON {"lines":[{"line_id":"给定ID","text":"新配音","visual_reason":"画面如何承接"}]}，顺序/数量与specs一致。\n'+
            dump(dict(specs=specs,cast=self.cast,actors=self.actors,shots=self.shots,continuity=self.selection['continuity_plan'])))
        def validate(v):
            rows=v.get('lines',[]) if isinstance(v,dict) else []
            if [r.get('line_id') for r in rows]!=[r['line_id'] for r in specs]:raise ValueError('script must preserve every line in order')
            for spec,row in zip(specs,rows):
                if not isinstance(row.get('text'),str) or not row['text'].strip() or not row.get('visual_reason'):raise ValueError('empty line')
                terms=spec.get('verbatim_terms') or []
                if any(isinstance(t,str) and t not in row['text'] for t in terms):raise ValueError('missing frozen key terms')
            return v
        answer=self.call('material_scene_script_1_1',prompt,validate)
        return dict(lines=[dict(spec,**{k:r[k] for k in ('text','visual_reason')}) for spec,r in zip(specs,answer['lines'])],
                    facts=[dict(line_id=r['line_id'],text=r['original_text'],requirement=r['must_preserve'],
                                verbatim_terms=r['verbatim_terms']) for r in specs],visual_scenes=[])

    def prepare(self,cache,output):
        from .dialogue_cut import _speech, voices_for, _Timeline
        from .joint_dialogue import place_audio
        windows,lock=self.lock();voices=voices_for(lock)
        script=self.write_script(lock)
        lines=[];samples_by_id={}
        for raw in script['lines']:
            line=dict(raw,id='line_'+str(len(lines)+1),mode='dialogue',picture_relation='planned_dialogue')
            binding=lock['scenes'][next(i for i,s in enumerate(lock['scenes']) if s['scene_id']==line['scene_id'])]['role_bindings'][line['role']]
            line['speaker']={k:binding[k] for k in ('window_id','role_id')}
            samples=_speech(line,voices[line['role']],self.state,output)
            if not len(samples):raise LibraryStopped('dialogue_silent_line:'+line['line_id'])
            lines.append(line);samples_by_id[line['line_id']]=samples
        write_json(Path(output)/'dialogue_script.json',script)
        # Same speech is kept while the director finds more footage. A short
        # pool never authorizes truncating audio or unbounded source replay.
        allocation=None;last_error=None
        for attempt in range(1,7):
            windows,lock=self.lock()
            shots={s['slot_id']:s for scene in lock['scenes'] for s in scene['coverage_shots']}
            scenes={s['scene_id']:s for s in lock['scenes']}
            preflights=[]
            while True:
                preflight=capacity_preflight(lines,list(shots.values()),self.cast,max_seconds=lock['edit_max_s'],
                    scene_shots={sid:{s['slot_id'] for s in scene['coverage_shots']} for sid,scene in scenes.items()})
                preflights.append(preflight)
                write_json(self.root/f'capacity_preflight_{attempt}.json',preflights)
                if preflight['feasible']:break
                self.explore(dict(problem='画面物理容量不足，排片不能解决，必须检索补充。保持演员绑定。',
                    capacity={k:v for k,v in preflight.items() if k!='options'},
                    measured_lines=[{k:r.get(k) for k in ('line_id','role','text','voiced_s','pause_after_s','addressee_roles')} for r in lines]))
                windows,lock=self.lock()
                shots={s['slot_id']:s for scene in lock['scenes'] for s in scene['coverage_shots']}
                scenes={s['scene_id']:s for s in lock['scenes']}
            request=dict(lines=[{k:r.get(k) for k in ('line_id','scene_id','role','text','voiced_s','pause_after_s','addressee_roles','visual_reason','reference_offscreen')} for r in lines],
                cast=self.cast,actors=self.actors,shots=list(shots.values()),
                continuity=self.selection['continuity_plan'],previous_error=last_error,
                max_duration_s=lock['edit_max_s'],eligible_options=preflight['options'])
            prompt=('逐句审核画面语义，不做时间轴排片。对eligible_options里的每个镜头，判断实际人物、动作和地点能否承接本句。'
                '列出所有语义成立的候选，按优先级排序；不必替程序挑一个唯一镜头或手算容量。'
                '真实配音时长、共享源帧、角色绑定和不重复使用均由程序硬性校验；程序已确认整个候选池有可行解。'
                'eligible_options是程序已筛好的身份/对象正确的可选镜头和用途；只从对应列表选。整个候选池已通过实际时长可行性检查，'
                '你只判断语义与剪辑连贯性，程序做帧分配。每句可列全部语义合适的候选，reason每项不超过30字。'
                '每个角色首次露脸发言必须有最多1秒可见说话锚点（短句按实长），其余可剪到同场听者或有意义的插入镜头。'
                'speaker只能选该演员确实开口的区间；listener须是另一位已绑定演员且无人在说话；'
                'cutaway不能出现别人讲话或配音者清楚闭嘴的脸。嘴动区间太短就补找。'
                '每个用途必须明确claims：把画面解读成哪个故事角色做什么/对谁做，就引用该shot的event_id、part=subject或addressee、role。'
                '例如说“顾客在吃东西”，subject的实际actor_id必须等于顾客绑定；绝不能因旁白就把店员的吃东西镜头说成顾客。'
                '只是中性氛围、纯物件或普通倾听时claims可空，不能省略与剧情有关的动作声明来绕过。'
                '若你声称对着某故事角色说话，addressee_role也必须填；实际看不出对象时不要伪称。'
                '镜头应连贯，避免跨地点乱跳、标题、动作重置；actual observations优先于索引和你的旧计划。'
                '只返回JSON {"lines":[{"line_id":"给定ID","reason":"本句画面作用",'
                '"uses":[{"shot_id":"实际ID","mode":"speaker/listener/cutaway","reason":"实际动作与本句的联系",'
                '"claims":[{"role":"故事角色","event_id":"实际事件ID","part":"subject/addressee"}],"addressee_role":null}]}]}。\n'+dump(request))
            def shape(v):
                if not isinstance(v,dict) or not isinstance(v.get('lines'),list):raise ValueError('lines required')
                return v
            choice=self.call(f'material_scene_schedule_1_{attempt}',prompt,shape)
            write_json(self.root/f'schedule_choice_{attempt}.json',choice)
            try:
                for line,row in zip(lines,choice['lines']):
                    allowed={s['slot_id'] for s in scenes[line['scene_id']]['coverage_shots']}
                    if any(u.get('shot_id') not in allowed for u in row.get('uses',[])):raise ValueError('shot belongs to a different scene')
                allocation,items=allocate(choice,lines,list(shots.values()),self.cast,max_seconds=lock['edit_max_s'])
                self.review_uses(choice,lines,shots,attempt)
                break
            except ValueError as error:
                last_error=str(error)
                write_json(self.root/f'schedule_rejected_{attempt}.json',dict(error=last_error,measured_lines=request['lines']))
                if attempt>=6:raise LibraryStopped('autonomous_schedule_not_grounded:'+last_error) from error
                # Physical gaps were resolved before calling the planner. Only
                # semantic incompatibility can now require another search.
                if last_error.startswith('semantic use conflicts') and attempt>1:
                    self.explore(dict(problem=last_error,measured_lines=request['lines'],cast_locked=self.cast))
                elif 'footage' in last_error:
                    last_error += '；完整eligible_options池已通过容量检查，请扩充受限台词的相容候选，勿只选一个短片段。'
        if allocation is None:raise LibraryStopped('autonomous_no_valid_allocation')
        timeline=_Timeline(windows)
        line_by_id={r['line_id']:r for r in lines}
        for piece in allocation:
            shot=shots[piece['slot_id']];scene=scenes[piece['scene_id']];voice=piece['voice_role']
            visible=[r for r,a in self.cast.items() if a in shot['visible'] and r in scene['role_bindings']]
            role=voice if voice in visible else visible[0] if visible else voice
            kind='speech' if self.cast[voice] in shot['speaking'] else 'reaction'
            row=timeline.append(scene,dict(shot,role=role),piece['first']/FPS,piece['last']-piece['first'],kind,piece['line_id'])
            row.update(role_ids=[shot['local_actor_bindings'][a] for a in shot['visible']],
                visible_roles=visible,speaking_role_ids=[shot['local_actor_bindings'][a] for a in shot['speaking']],
                observed_action=shot['action'],planned_use=piece['reason'],actor_ids=shot['visible'],
                speaking_actor_ids=shot['speaking'])
        anchored=set()
        for line in lines:
            line['visual_anchor']=line['role'] not in anchored and not line['reference_offscreen']
            if line['visual_anchor']:anchored.add(line['role'])
        audio_lines,captions=place_audio(lines,timeline.rows,samples_by_id,cache,POLICY)
        checked=validate_allocation(allocation,items,shots)
        checked.update(measured_speech_s=sum(r['voiced_s'] for r in lines),source_reuse_s=0,
                       cast=self.cast,lines=len(lines))
        write_json(Path(output)/'material_hard_validation.json',checked)
        write_json(Path(output)/'joint_schedule.json',dict(policy=POLICY,items=items,allocation=allocation))
        write_json(Path(output)/'material_lock.json',lock);write_json(Path(output)/'watched_windows.json',windows)
        self.state.set_artifact('material_lock',lock)
        return lock,windows,voices,script,timeline,lines,audio_lines,captions

    def review_uses(self,choice,lines,shots,attempt):
        # Independent semantic check cannot override program identity/time checks.
        prompt=('你审核二创的画面用途，只依据实际观察事实，不能因导演已选就同意。逐一核查：'
            '画面中的动作主体、说话对象与锁定cast是否一致；claims是否漏掉导演声称的关键动作/对象；'
            '台词的动作结果是否被相反画面误导；主对话的地点/人物/道具关系是否连贯，少量功能明确插入可接受。'
            '不要求原字幕和新台词相同，不要求真人逐音素口型；动画清楚开合配说话姿态可成立。'
            '返回JSON {"passed":true,"issues":["具体line_id/shot_id及矛盾，没问题为空"]}，有实质矛盾必须false。\n'+
            dump(dict(cast=self.cast,actors=self.actors,lines=[{k:r.get(k) for k in ('line_id','role','text','addressee_roles')} for r in lines],
                planned=choice,observed_shots=[shots[i] for i in sorted({u['shot_id'] for r in choice['lines'] for u in r['uses']})])))
        def valid(v):
            if not isinstance(v,dict) or type(v.get('passed')) is not bool or not isinstance(v.get('issues'),list):raise ValueError('invalid semantic review')
            return v
        review=self.call(f'material_scene_identity_1_{attempt}',prompt,valid)
        write_json(self.root/f'semantic_review_{attempt}.json',review)
        if not review['passed'] or review['issues']:raise ValueError('semantic use conflicts: '+dump(review['issues']))
