"""Reference-preserving dialogue, published-library discovery and source voices.

The v36 joint writer/scheduler owns the edit. Search supplies observed scenes;
source voice samples supply timbre only, never the new script or its line count.
"""
from copy import deepcopy
from pathlib import Path
import json
import re

from .autonomous_material import Discovery, dump
from . import joint_dialogue, reference_story
from .character_voice import CharacterVoices, VoiceUnavailable
from .state import LibraryStopped, json_sha, write_json

POLICY = 'reference_story_v1'


def validate_optional_roles(value, lines):
    from .scene_coverage import lead_roles
    roles={r['role'] for r in lines}
    if (not isinstance(value,list) or any(not isinstance(r,str) for r in value) or
            len(value)!=len(set(value)) or not set(value)<=roles or set(value)&lead_roles(lines) or
            len(roles-set(value))<min(2,len(roles))):
        raise ValueError('Only secondary characters may be optional; keep the leads and the central dialogue participants')
    return value


def dialogue_capacity_evidence(shots, windows):
    """Use the actual scheduler's eligibility, including the other actor listening."""
    from itertools import combinations
    from .scene_coverage import capacity
    actors=sorted({a for s in shots for a in s['visible']})
    result=[]
    for pair in combinations(actors,2):
        members=[]
        for wid,w in windows.items():
            visible={a for s in shots if s['window_id']==wid for a in s['visible']}
            if set(pair)<=visible:members.append(w)
        groups=[]
        for window in sorted(members,key=lambda w:(w['source_sha256'],w['source_start_s'])):
            if (groups and groups[-1]['source']==window['source_sha256'] and
                    window['source_start_s']<=groups[-1]['end_s']+1/30):
                groups[-1]['windows'].append(window['window_id'])
                groups[-1]['end_s']=max(groups[-1]['end_s'],window['source_end_s'])
            else:
                groups.append(dict(source=window['source_sha256'],end_s=window['source_end_s'],windows=[window['window_id']]))
        for group in groups:
            pool=[s for s in shots if s['window_id'] in group['windows']]
            scene=dict(role_bindings={a:dict(role_id=a) for a in pair})
            rows=[]
            for actor in pair:
                usable=[s for s in pool if joint_dialogue.eligible(scene,s,actor)]
                speaking=[s for s in pool if s['speaking']==[actor]]
                loops=[s['slot_id'] for s in usable if joint_dialogue.reusable(s)]
                rows.append(dict(actor_id=actor,observed_speech_s=round(capacity(speaking),2),
                    speech_or_listener_unique_s=round(capacity(usable),2),
                    reusable_dialogue_shots=loops,
                    reusable_listener_shots=[s['slot_id'] for s in usable if not s['speaking'] and joint_dialogue.reusable(s)]))
            result.append(dict(actor_ids=list(pair),window_ids=group['windows'],actors=rows))
    return result



def footage_budgets(lock, contract):
    """Per scene: distinct footage, speech ceiling, and roles whose words exceed it (user 11-17)."""
    from .dialogue_cut import voices_for
    voices=voices_for(lock) if lock.get('cast_bindings') else {}
    may_omit=reference_story.dialogue_adaptation(contract)
    rows=[]
    for scene in lock['scenes']:
        if not scene.get('reference_lines'):continue
        rows.append(dict(scene_id=scene['scene_id'],**joint_dialogue.footage_budget(scene),
            max_chars=joint_dialogue.char_limits(scene,voices,may_omit=may_omit),
            over_budget=joint_dialogue.fit_problems(scene,voices) if may_omit else []))
    return rows


def check_footage_omission(plan, lock, contract, budgets):
    """Validate the plan's footage-driven omissions; return the omitted reference line IDs."""
    omitted=plan.get('omit_reference_line_ids',[])
    if omitted in (None,''):omitted=[]
    if not isinstance(omitted,list) or any(type(i) not in (int,str) for i in omitted):
        raise ValueError('omit_reference_line_ids must be an array of reference line_id values')
    if not omitted:
        over=[p for b in budgets for p in b['over_budget']]
        if over:
            raise ValueError('画面装不下这些角色的原句（字数远超max_chars），须在omit_reference_line_ids省去次要参考句：'+dump(over))
        return []
    if not reference_story.dialogue_adaptation(contract):
        raise ValueError('this story keeps every reference line; omit_reference_line_ids must be []')
    if not isinstance(plan.get('omission_reason'),str) or not plan['omission_reason'].strip():
        raise ValueError('omission_reason must explain which dialogue functions are dropped and why the main line holds')
    from .dialogue_cut import voices_for
    voices=voices_for(lock) if lock.get('cast_bindings') else {}
    known={r['line_id'] for s in lock['scenes'] for r in s.get('reference_lines',[])}
    unknown=sorted(set(omitted)-known)
    if unknown:raise ValueError(f'omit_reference_line_ids contains unknown or already omitted line_ids {unknown}')
    problems=[]
    for scene in lock['scenes']:
        refs=scene.get('reference_lines') or []
        if not refs:continue
        ends={refs[0]['line_id'],refs[-1]['line_id']}&set(omitted)
        if ends:problems.append(f'{scene["scene_id"]}不能省第一句和最后一句{sorted(ends)}')
        kept=[r for r in refs if r['line_id'] not in omitted]
        lost=sorted({r['role'] for r in refs}-{r['role'] for r in kept})
        if lost:problems.append(f'{scene["scene_id"]}的角色{lost}一句都没留，每个角色至少留一句')
        rows=joint_dialogue.fit_problems(scene,voices,kept={r['line_id'] for r in kept})
        totals=[r for r in rows if r['kind']=='scene_total']
        if totals:
            row=min(totals,key=lambda r:r['target_scene_need'])
            problems.append(f'{row["scene_id"]}保留的内容共需约{row["scene_need"]}字的配音空间'
                f'（每个角色取原句{int(row["min_keep"]*100)}%与每轮{row["min_line_chars"]}字中的较大者：{dump(row["role_needs"])}），'
                f'不重复画面只有{row["unique_footage_s"]}秒，最多容纳约{row["target_scene_need"]}字；'
                '请再省去次要句，优先省篇幅最大角色的次要句和打断对话的零碎轮次，使所需空间不超过这个数')
        for row in rows:
            if row['kind']=='role_footage':
                problems.append(f'{row["scene_id"]}的{row["role"]}能出镜的画面太少，最多承载{row["max_chars"]}字'
                    f'（现保留{row["turns"]}轮、原句{row["reference_chars"]}字，每轮至少{row["min_line_chars"]}字）；'
                    f'需省去该角色的次要句，使其保留不超过{row["target_role_turns"]}轮、原句不超过{row["target_role_chars"]}字')
    if problems:raise ValueError('；'.join(problems))
    return omitted


def apply_footage_omission(story, lock, omitted, reason):
    """Record footage-driven omissions in the contract, the route and this lock (user 11-17)."""
    previous=list(story.contract.get('optional_reference_line_ids',[]))
    story.contract=reference_story.with_dialogue_scope(story.contract,previous+list(omitted),
        (story.contract.get('scope_rationale','')+' / 按画面长度压缩：'+reason).strip(' /'))
    write_json(Path(story.state.output)/'reference_contract.json',story.contract)
    story.route.lines=[r for r in story.route.lines if r['line_id'] not in omitted]
    for scene in [*story.route.scenes,*lock['scenes']]:
        scene['line_ids']=[i for i in scene.get('line_ids',[]) if i not in omitted]
        if 'reference_lines' in scene:
            scene['reference_lines']=[r for r in scene['reference_lines'] if r['line_id'] not in omitted]
    if 'reference_contract' in lock:lock['reference_contract']=story.contract
    story.state.set_artifact('footage_budget_omission',dict(omitted_line_ids=list(omitted),reason=reason))


def unheard_parts(context, failures):
    """The parts of a widened voice context outside the widest context already answered.

    Contexts grow around the scene, each containing the previous one; only an answered
    ('voice') attempt counts as heard."""
    low,high=context
    heard=[tuple(f['context']) for f in failures if f.get('kind')=='voice'
           and f['context'][0]>=low and f['context'][1]<=high]
    if not heard:return []
    inner_low,inner_high=max(heard,key=lambda c:c[1]-c[0])
    return [p for p in ((low,inner_low),(inner_high,high)) if p[1]-p[0]>=1.0]


PRESET_POLICY='preset_voice_fallback_v1'
PRESET_BY_GENDER={'female':('Cherry','Serena'),'male':('Ethan','Moon','Kai')}


def preset_genders(story, lock, pending):
    """One text question for all roles that need a preset voice: which voice gender fits each.

    The role's reference lines and its on-screen performer decide; an unusable answer falls
    back to alternating genders (presets still never repeat within a film)."""
    roles=[r for r,_,_ in pending]
    appearance={}
    for scene in lock['scenes']:
        for role,b in scene['role_bindings'].items():
            if role in roles and b.get('appearance'):appearance.setdefault(role,b['appearance'])
    rows=[dict(role=r,appearance=appearance.get(r),lines=[x['text'] for x in story.route.lines if x['role']==r][:8]) for r in roles]
    prompt=('为下列故事角色选择配音声音的性别。依据参考台词里的称呼、身份与语气，以及画面扮演者的外观；'
        '只能填male或female，拿不准时按台词身份判断。只返回JSON {"genders":{"角色":"female"},"evidence":"简要依据"}。\n'+
        json.dumps(dict(roles=rows),ensure_ascii=False))
    def valid(v):
        g=v.get('genders') if isinstance(v,dict) else None
        if not isinstance(g,dict) or set(g)!=set(roles) or any(x not in PRESET_BY_GENDER for x in g.values()):
            raise ValueError('genders须恰好覆盖'+json.dumps(roles,ensure_ascii=False)+'，值只能是male或female')
        return v
    try:
        answer=story.call('material_scene_discovery_voice_gender_'+json_sha(dict(rows=rows))[:12],prompt,valid)
        return answer['genders']
    except (LibraryStopped,ValueError) as exc:
        from .material_browser import observation_unusable
        if not observation_unusable(exc):raise
        story.state.set_artifact('preset_gender_unusable',dict(roles=roles,error=str(exc)))
        return {}


def preset_voice(story, service, voice_bindings, role, actor_id, failures, gender):
    """Give a role a verified preset voice when its original voice cannot be cloned (user 11-17).

    Roles in one film never share a preset; the binding records a distinct policy so reviews
    can tell it from a cloned original timbre."""
    used=set(service.voices.values())
    if gender not in PRESET_BY_GENDER:
        # Unknown gender: alternate, taking the gender whose presets are used least so far.
        count={g:sum(v in used for v in vs) for g,vs in PRESET_BY_GENDER.items()}
        first=min(('female','male'),key=lambda g:(count[g],g))
    else:first=gender
    order=tuple(v for g in (first,'male' if first=='female' else 'female') for v in PRESET_BY_GENDER[g])
    voice=next((v for v in order if v not in used),order[0])
    service.voices[role]=voice
    voice_bindings[role]=dict(review_policy=PRESET_POLICY,actor_id=actor_id,voice_id=voice,
        gender=gender or 'unclear',original_voice_failures=failures)
    story.state.set_artifact('preset_voice_fallback',dict(role=role,actor_id=actor_id,voice=voice,
        gender=gender or 'unclear',failures=failures))

class StoryDialogue(Discovery):
    def __init__(self, route):
        super().__init__(route)
        self.contract = reference_story.make_contract(
            dict(route.reference,reference_sha256=route.reference_source['sha256']),route.scenes,route.lines,
            visual_policy='continuous_dialogue_adaptation_v1',creative_brief=route.config.get('material_brief'))
        write_json(Path(self.state.output)/'original_reference_contract.json', self.contract)
        self.voice_service = None
        self.requirements_planned = False
        self.inspections_restored = False
        self.budget=len(self.records)
        self.max_rounds=None
        self.identities.verify_matches=True
        self.allow_static_reuse=True
        self.seed_windows=list(dict.fromkeys(w for w in re.findall(r'vl_[a-f0-9]{16}',route.config.get('material_brief') or '')
                                            if w in self.records))
        self.seen.update(self.seed_windows)
        from .material_browser import MaterialBrowser
        self.browser=MaterialBrowser(self)
        self.browser_result=None

    def identity_window(self, window, proxy, source):
        from .creative_portraits import observe_portraits
        return observe_portraits(window,proxy,source,self.root,self.call)

    def restore_inspections(self):
        """Reconstruct watched evidence through the normal authenticated call cache.

        Prior cast, queries, scripts and conclusions are not inherited. Window
        IDs only tell us which original source observations can be reused.
        """
        import json
        from .pipeline import _material_ledgers
        if self.inspections_restored:return
        self.inspections_restored=True
        for folder, _ledger in _material_ledgers(self.state):
            if folder.resolve()==Path(self.state.output).resolve():continue
            previous=folder/'autonomous_material'
            files=sorted((previous/'identities').glob('assessment_*.json'),
                         key=lambda f:int(f.stem.rsplit('_',1)[1]))
            if not files:continue
            prior_contract=folder/'reference_contract.json'
            same_brief=(prior_contract.is_file() and getattr(self,'contract',{}).get('creative_brief')==
                        json.loads(prior_contract.read_text()).get('creative_brief'))
            ids=[]
            for file in files:
                wid=json.loads(file.read_text()).get('window_id')
                if not same_brief and getattr(self,'seed_windows',[]) and wid not in self.seed_windows:
                    continue  # user supplied a new scene lead; old paid evidence remains reusable on demand
                if wid in self.records and wid not in ids and (previous/(wid+'.json')).is_file():
                    ids.append(wid)
            for wid in ids:
                self.seen.add(wid)
                # inspect revalidates source/exemplars and uses only exactly
                # matching, hash-verified received model calls from ancestry.
                self.inspect(wid)
            write_json(self.root/'restored_inspections.json',dict(parent=str(folder),window_ids=ids))
            break

    def run(self):
        candidates=self.retrieve()
        self.use_candidate(0)
        return super().run()

    def use_candidate(self, rank):
        from .scene_casting import candidate_ranges
        candidate=self.browser_result['candidates'][rank]
        if getattr(self,'active_candidate',None)==candidate and self.shots:return
        self.active_candidate=candidate
        self.active_ranges=candidate_ranges(self.active_candidate)
        self.raw_shots=[];self.shots=[];self.conflicts=[]
        for wid in self.active_ranges:self.inspect(wid)
        self.seen=set(self.active_ranges)
        write_json(self.root/'active_candidate.json',dict(candidate=self.active_candidate,ranges=self.active_ranges))

    def verified_inspection(self, window, proxy, shots):
        from .scene_casting import scope_shots,verify_source_speakers,apply_performance_policy
        shots=scope_shots(shots,self.active_ranges)
        return apply_performance_policy(scope_shots(verify_source_speakers(self,window,proxy,shots),self.active_ranges),
            self.route.config.get('dialogue_performance_policy','strict'))

    def explore(self, feedback=None):
        from .scene_casting import direct_cast
        return direct_cast(self,feedback)

    def retrieve(self):
        """Retrieval-only entry: no cast assignment, script, TTS or final render."""
        if any(not row.get('role') for row in self.route.lines):
            raise LibraryStopped('retrieval_requires_role_annotated_reference_not_raw_asr')
        if not self.requirements_planned:
            original={s['scene_id']:s for s in self.route.scenes if s['line_ids']}
            prompt=('先提炼参考的对话关系和戏剧结构，再找一个大致合适、稳定连贯的实际对话场景。'
                '用户要围绕找到的场景创作合理剧本，不要求精确复刻原片动作和道具。'
                '这里只概括谁向谁提问、双方关系、怎样质疑或回应、怎样递进和形成包袱；每个描述字段不超过240字。'
                '不要把原片毁物、打斗、逃跑、特定商品或职业变成素材必须出现的动作清单。'
                '中性说话、略带微笑、倾听、惊讶等日常表演都可由新对白合理重释，不要求表情逐句匹配。'
                '优先同一地点同一组人物的连续交谈；选好场景后由编剧根据画面改写具体台词。'
                '可以省掉与主要双方问答、核心冲突无关的插曲式配角，不为这种配角强找第三个会讲话的人。'
                'optional_roles列可省去的原角色，不能删主要双方或承担核心反转的人物；omission_reason解释为何不伤主线。'
                '没有可省角色就用空数组并说明。剩余人物承担的全部原台词作用仍须通过合理改写保留。'
                '只返回JSON {"optional_roles":[],"omission_reason":"为何只保留这些主要参与者",'
                '"scenes":[{"scene_id":"原有对话场景ID","relationship":"保留角色间关系",'
                '"dramatic_progression":"问答递进与包袱结构，不含必须拍出的原片动作",'
                '"setting_type":"宽泛可替换的对话场所类型"}]}。仅列有台词的原场景，顺序不变。\n'+
                dump(dict(reference_contract=self.contract,user_brief=self.route.config.get('material_brief'))))
            def valid(value):
                rows=value.get('scenes',[]) if isinstance(value,dict) else []
                if [r.get('scene_id') for r in rows]!=list(original):
                    raise ValueError('Preserve the original dialogue scenes in order')
                if any(not isinstance(row.get(k),str) or not row[k].strip() or len(row[k])>240
                       for row in rows for k in ('relationship','dramatic_progression','setting_type')):
                    raise ValueError('Briefly describe relationships and dialogue structure')
                validate_optional_roles(value.get('optional_roles'),self.route.lines)
                if not value.get('omission_reason'):raise ValueError('Explain whether secondary omissions preserve the main conflict')
                return value
            planned=self.call('material_scene_discovery_decide_dialogue_requirements',prompt,valid)
            write_json(self.root/'dialogue_requirements.json',planned)
            optional=[r['line_id'] for r in self.route.lines if r['role'] in planned['optional_roles']]
            self.contract=reference_story.with_dialogue_scope(self.contract,optional,planned['omission_reason'])
            write_json(Path(self.state.output)/'reference_contract.json',self.contract)
            self.route.lines=[r for r in self.route.lines if r['line_id'] not in optional]
            self.route.scenes=list(original.values())
            for row in planned['scenes']:
                scene=original[row['scene_id']]
                roles=list(dict.fromkeys(r['role'] for r in self.route.lines if r['line_id'] in scene['line_ids']))
                scene.update(roles=roles,line_ids=[i for i in scene['line_ids'] if i not in optional],
                    voice_only_roles=[r for r in scene.get('voice_only_roles',[]) if r in roles],
                    setting=row['setting_type'],interaction=row['relationship'],
                    template=row['dramatic_progression'],search_plot=row['dramatic_progression'],
                    visual_function='稳定的连续对话和听者反应',must_see=[],required_actions='同一场景中的交谈与回应',
                    visual_policy='continuous_dialogue_adaptation_v1')
            self.route.scenes=[s for s in self.route.scenes if s['line_ids']]
            self.requirements_planned=True
        if self.browser_result is None:
            self.browser_result=self.browser.run()
            import os
            if os.environ.get('OMNI_STOP_AFTER_BROWSER')=='1':
                # Material-selection experiment: stop before casting, script or voices are paid for.
                raise LibraryStopped('stopped_after_browser_selection')
        return self.browser_result

    def inspection_proxy(self, window, source):
        return self.browser.proxy(window['window_id'])['proxy']

    def inspection_instructions(self):
        return ('只观察静音且原对白字幕被黑块隐藏的画面，不知道新剧本和选角。'
                '不能根据原剧情或文字补充动作，黑块遮住的嘴、手、物件必须记录看不清。')

    def creative_search_rules(self):
        return ('用户的选材标准是大致合适的连续对话，不是逐一还原参考动作。'
            '优先实际看片和扩展一个有希望的对话场景，别因为索引没写质疑/愤怒就否定普通交谈画面。'
            '同场人物讲话、倾听和中性反应足够供后续编剧合理改写；不必找到原片商品、工具、动作或同样情绪。'
            '检查人物身份与对话方向即可，剧情和具体台词由写稿阶段围绕所选场景创作。'
            'target_duration_s只是按保留文字估算的对白长度，最终以改写后真实TTS为准，不填满原片总长。'
            '每位角色先有一处本人讲话画面来建立身份；其余对白可以切到同场另一人的静默倾听/反应。'
            '不能把每句都算成说话者本人必须全程露脸的时长缺口。dialogue_capacity是程序按真实排片规则算出的容量。'
            'reusable_listener_shots也能承接该演员的对白，可按需回切；并非只有本人嘴动画面才能重复使用。'
            '若已找到连贯组合、双方有讲话依据且讲话或听者反应容量足够，就finish交给编剧与调度，'
            '不要为凑满每人全程露脸的秒数继续检索。具体台词与镜头是否相容仍由后续独立审核核对。'
            'loopable=true的原地讲话或原地反应可由调度器重复使用，不以原始唯一秒数否定足够的对话镜头。')

    def search_target_duration(self):
        return round(sum(joint_dialogue._chars(r['text']) for r in self.route.lines)/5,1)

    def decision_evidence(self):
        return dict(dialogue_capacity=dialogue_capacity_evidence(self.shots,self.windows),
                    independently_compared_scene_candidates=self.browser_result)

    def search_reference_context(self):
        return dict(policy='Find a coherent continuous conversation; adapt the reference dialogue to it, without exact action matching.',
            user_brief=self.route.config.get('material_brief'),suggested_candidates=[self.card(w) for w in self.seed_windows])

    def search_scene_context(self, scene):
        lines=[r for r in self.contract['lines'] if r['line_id'] in scene['line_ids']]
        return dict(scene_id=scene['scene_id'],roles=scene['roles'],line_ids=scene['line_ids'],
            start_s=scene['start_s'],end_s=scene['end_s'],dialogue_id=scene.get('dialogue_id'),
            setting=scene['setting'],interaction=scene['interaction'],template=scene['search_plot'],
            required_actions=scene['required_actions'],
            speech_requirements=[dict(role=role,
                original_utterance_count=sum(r['role']==role for r in lines),
                total_text_character_count=sum(joint_dialogue._chars(r['text']) for r in lines if r['role']==role),
                estimated_minimum_speech_seconds=round(sum(joint_dialogue._chars(r['text']) for r in lines if r['role']==role)/6,1))
                for role in dict.fromkeys(r['role'] for r in lines)])

    def search(self, need):
        return self.route.index.search(need, channels=['bm25', 'text', 'image'],
            text_embedder=self.route.text, clip=self.route.image, top_k=200, final_k=200)

    def validate_selection(self, value):
        from .scene_coverage import capacity
        # Catch an obviously tiny pool before enrolling voices or writing a script.
        for scene in self.route.scenes:
            ids = value['scene_windows'][scene['scene_id']]
            if hasattr(self,'active_ranges') and (not ids or not set(ids)<=set(self.active_ranges)):
                raise ValueError('只能选择独立复核通过的当前连续场景窗口')
            shots = [s for s in self.shots if s['window_id'] in ids]
            for role in scene['roles']:
                actor = value['cast'][role]
                if not any(actor in s['visible'] for s in shots):
                    raise ValueError(f'{scene["scene_id"]}:所选画面尚未确认{role}出镜；'
                                     '继续检索同一演员，不能用别人的动作代替')
                if hasattr(self,'active_ranges') and not any(joint_dialogue.speaker_view(s,actor) for s in shots):
                    raise ValueError(f'{role}:尚无本人讲话或独立确认遮嘴可配音画面，不能为凑时长借用别人')
            # The scheduler must open each on-screen speaker on a shot that identifies
            # them; check it here, before voices and script, so the gap triggers
            # same-scene expansion instead of a late schedule failure.
            bound = {value['cast'][r] for r in scene['roles']}
            for role in scene['roles']:
                lines = [r for r in self.route.lines if r['line_id'] in scene['line_ids'] and r['role'] == role]
                if not lines or all(r.get('on_screen') is False for r in lines):
                    continue
                actor = value['cast'][role]
                if hasattr(self,'active_ranges') and not any(joint_dialogue.anchor_view(s,actor,bound) for s in shots):
                    raise ValueError(f'{role}:所选窗口中没有能单独辨认{actor}的镜头（他本人原片说话，画面里没有其他有台词的角色，'
                                     '或focus_anchor_shots核对过的同框焦点镜头），'
                                     '排片无法让观众认出这个新声音。返回expand补看同场相邻画面，或换其他已看过的候选；不要重复本方案')
            refs = [r for r in self.route.lines if r['line_id'] in scene['line_ids']]
            demand = sum(joint_dialogue._chars(r['text']) for r in refs)/6
            if capacity(shots) < demand and not any(joint_dialogue.reusable(s) for s in shots):
                raise ValueError(f'{scene["scene_id"]}:完整参考约需至少{demand:.1f}秒对白画面；'
                                 '当前实际看片不足，继续找同场相邻窗口，不能删剧情')
            for role in {r['role'] for r in refs}:
                actor = value['cast'].get(role)
                if actor is None:
                    continue  # genuinely off-screen roles are bound by the scene adapter
                usable = [s for s in shots if joint_dialogue.speaker_view(s,actor) or
                          (not s['speaking'] and actor not in s['visible'] and s['visible'])]
                needed = sum(joint_dialogue._chars(r['text']) for r in refs if r['role'] == role)/6
                if capacity(usable) < needed and not any(joint_dialogue.reusable(s) for s in usable):
                    raise ValueError(f'{role}:约需{needed:.1f}秒本人讲话或同场听者画面；'
                                     '请扩大连续场景，不锁定不足的演员组合')

    def lock(self):
        from .scene_coverage import bind_offscreen_voices
        windows, lock = super().lock()
        lock.update(editing_policy=POLICY, reference_contract=deepcopy(self.contract))
        visible={a for scene in lock['scenes'] for shot in scene.get('coverage_shots',[]) for a in shot['visible']}
        lock['visible_actor_catalog']=[dict(actor_id=a,appearance=self.actors[a]['appearance'])
            for a in sorted(visible) if a in self.actors]

        if hasattr(self,'active_candidate'):
            lock['retrieved_scene']=deepcopy(self.active_candidate)
        introduced = set()
        for scene in lock['scenes']:
            scene['introduced_roles'] = sorted(introduced)
            scene['conversation'] = dict(group_id=scene.get('dialogue_id', scene['scene_id']))
            scene['speech_slots'] = [dict(shot, role=role, role_id=binding['role_id'])
                for role, binding in scene['role_bindings'].items()
                for shot in scene['coverage_shots'] if joint_dialogue.speaker_view(shot,binding['role_id'])]
            for shot in scene['coverage_shots']:
                shot.setdefault('objects', [])
                shot.setdefault('entry', shot['action'])
                shot.setdefault('exit', shot['action'])
            introduced.update(s['role'] for s in scene['speech_slots'])
            # The full original words stay immutable in reference_contract. Literal
            # prop names may adapt; independent semantic review checks their function.
            scene['reference_objects'] = []
            for row in scene['reference_lines']:
                row['verbatim_terms'] = []
        bind_offscreen_voices(lock)
        return windows, lock

    def plan_adaptation(self, lock):
        if getattr(self,'adaptation_plan',None) is None:
            expected=[r['line_id'] for r in reference_story.required_lines(self.contract)]
            budgets=footage_budgets(lock,self.contract)
            def valid(v):
                if not isinstance(v,dict) or not v.get('premise') or not isinstance(v.get('substitutions'),list):
                    raise ValueError('grounded premise and consistent substitution plan required')
                omitted=check_footage_omission(v,lock,self.contract,budgets)
                kept=[i for i in expected if i not in omitted]
                rows=v.get('beats')
                if (not isinstance(rows,list) or [r.get('reference_line_id') for r in rows]!=kept
                        or any(not r.get('dialogue_function') or not r.get('adapted_content') for r in rows)):
                    raise ValueError('Plan exactly these kept reference_line_id values in order: '+dump(kept))
                return v
            prompt=('根据已锁定演员与真实连续场景，先设计改编方案，再写台词。'
                '原参考用于保留问答、质疑、反驳、承诺、递进与包袱，不强制原物件或原动作。'
                '围绕实际物品和角色互动建立合理的新话题，不用原话硬塞画面，不把未发生的动作说成已发生。'
                'substitutions解释前后一致的道具/事实替换及其对话作用。'
                'beats逐条写原句的抽象dialogue_function和本场景可成立的adapted_content（是内容计划，不是最终台词）。'
                'beats必须且只能按required_reference_line_ids逐条输出；reference_contract保留完整原始证据，'
                '其中optional_reference_line_ids已获准省略，不得重新加回beats或引入对应配角。'
                '不改变cast、不换场、不删主线。'
                'footage_budget是各场不重复画面秒数和对白上限（同一画面最多重复2倍，用户要求不反复出现同一画面）；'
                'over_budget列出画面装不下的情况。有over_budget时，在omit_reference_line_ids列出省去的次要参考句，'
                '只保住主要问答递进、核心冲突和结局：kind=scene_total时省到整场所需空间不超过target_scene_need，'
                '优先省篇幅最大角色的次要句和零碎短句；kind=role_footage时省该角色自己的句子到target_role_turns轮、target_role_chars字以内；'
                '每轮台词要留出至少min_line_chars字的空间，不能靠把每句压成一两个字来凑；'
                '不能省每场第一句和最后一句，每个角色至少留一句；omission_reason说明省去了哪些作用、为何主线仍成立。'
                '没有over_budget时omit_reference_line_ids为空数组。beats只写保留的句子。'
                '返回JSON {"omit_reference_line_ids":[],"omission_reason":"","premise":"新场景故事前提",'
                '"substitutions":[{"original":"原作元素","adapted":"新场景元素","function":"相同对话作用"}],'
                '"beats":[{"reference_line_id":1,"dialogue_function":"抽象作用","adapted_content":"改编内容"}]}。\n'+
                dump(dict(reference_contract=self.contract,required_reference_line_ids=expected,footage_budget=budgets,
                    retrieved_scene=getattr(self,'active_candidate',None),
                    scenes=[dict(scene_id=s['scene_id'],cast=s['role_bindings'],shots=s['coverage_shots']) for s in lock['scenes']])))
            plan=self.call('material_scene_discovery_decide_adaptation_plan',prompt,valid)
            self.adaptation_plan=plan
            write_json(self.root/'adaptation_plan.json',plan)
            if plan.get('omit_reference_line_ids'):
                apply_footage_omission(self,lock,plan['omit_reference_line_ids'],plan['omission_reason'])
        for scene in lock['scenes']:
            scene['adaptation_plan']=dict(self.adaptation_plan,beats=[r for r in self.adaptation_plan['beats']
                if r['reference_line_id'] in scene['line_ids']])
        return lock

    def review_script(self, script, lock, number):
        seen, previous_missing, revision = set(), None, 0
        while True:
            reference_story.check_coverage(script, self.contract)
            fingerprint=json_sha(script)
            if fingerprint in seen:
                raise LibraryStopped('reference_story_revision_no_change')
            seen.add(fingerprint)
            instruction = ('独立审核待配音剧本与已看片证据。编号覆盖只代表格式，'
                           '你须判断每条原义是否真实保留。不要替作者改写审核标准。')
            prompt = instruction + reference_story.review_instructions(self.contract) + '\n' + dump(dict(
                script=script, scenes=[dict(scene_id=s['scene_id'], role_bindings=s['role_bindings'],
                    shots=s['coverage_shots']) for s in lock['scenes']]))
            def valid(value):
                result = reference_story.check_review(value, self.contract)
                if any(i['kind']=='incomplete_review' for i in result['issues']):
                    raise ValueError('Return complete reference_checks and story_checks with actual evidence')
                return value
            name=f'{number}_{revision}'
            answer = self.call('material_scene_script_story_review_'+name, prompt, valid)
            checked = reference_story.check_review(answer, self.contract)
            write_json(self.root/f'script_story_review_{name}.json', dict(answer=answer, **checked))
            if checked['passed']:
                return script
            missing=len(checked['issues'])
            if previous_missing is not None and missing>=previous_missing:
                raise LibraryStopped('reference_story_revision_no_semantic_progress:'+dump(checked['issues']))
            previous_missing=missing
            def validate_revision(value):
                joint_dialogue.validate_script(value,lock)
                reference_story.check_coverage(value,self.contract)
                return value
            revised=self.call('material_scene_script_story_revision_'+name,
                '修复独立审稿发现的剧情缺失。逐条依据完整参考恢复原意、问答和结尾，保留已成立的台词。'
                '只能修改剧本，不改变参考、演员、镜头事实。不要填入画面无法承担的动作；'
                '保留所有line_id、reference_line_ids和scene_id。返回完整lines与visual_scenes JSON。\n'+
                dump(dict(reference_contract=self.contract,script=script,issues=checked['issues'],
                          scenes=lock['scenes'])),validate_revision)
            if 'facts' in script:
                revised['facts']=script['facts']
            script=revised
            revision+=1

    def source_voices(self, lock):
        from .source_voice_review import find_and_audition,SourceVoiceProtocolError,POLICY as VOICE_POLICY
        from .pipeline import _material_artifact
        from .media import sha256_file
        import json
        service = CharacterVoices(self.root/'character_voices')
        service.voices = {}
        prior=_material_artifact(self.state,'grounded_source_voices') or {}
        voice_bindings={}
        service.reuse_folders=prior.get('speech_folders',([prior['speech_folder']] if prior.get('speech_folder') else []))
        pending=[]  # roles whose original timbre cannot be had -> preset voice (user 11-17)
        for role in dict.fromkeys(r['role'] for r in reference_story.required_lines(self.contract)):
            bindings = [s['role_bindings'][role] for s in lock['scenes'] if role in s['role_bindings']]
            binding = next((b for b in bindings if not b.get('voice_only')), None)
            kept=(prior.get('roles') or {}).get(role)
            if kept and kept.get('review_policy')==PRESET_POLICY and kept.get('actor_id')==(binding or {}).get('role_id'):
                # A resume keeps the preset chosen before; auditioning again would be paid twice.
                service.voices[role]=kept['voice_id'];voice_bindings[role]=kept
                continue
            if binding is None:
                pending.append((role,None,[dict(error='voice-only role: no on-screen source actor')]))
                continue
            aid = binding['role_id']
            old=next((v for v in prior.get('roles',{}).values() if v['actor_id']==aid),None)
            if old and old.get('review_policy')==VOICE_POLICY and old['actor_id']==aid and old['source_sha256'] in self.route.sources:
                receipt=json.loads(Path(old['enrollment_path']).read_text())
                if (receipt['status']!='received' or receipt['voice']!=old['voice_id'] or
                        sha256_file(old['sample_path'])!=receipt['binding']['sample_sha256'] or
                        sha256_file(old['evidence_path'])!=old['evidence_sha256']):
                    raise VoiceUnavailable('Prior verified voice evidence changed')
                service.voices[role]=old['voice_id'];voice_bindings[role]=old
                continue
            spoken = sorted((s for s in self.shots if joint_dialogue.speaker_view(s,aid)),
                            key=lambda s:-(s['source_end_s']-s['source_start_s']))
            if not spoken:
                pending.append((role,aid,[dict(error='No observed source speech')]))
                continue
            failures = []
            source = self.route.sources[spoken[0]['source_sha256']]
            ranges=[r for values in self.active_ranges.values() for r in values]
            scene_low,scene_high=min(a for a,b in ranges),max(b for a,b in ranges)
            contexts=[(scene_low,scene_high)]
            # Larger source-only contexts are never added to the creative shot
            # pool. Limit each AV request to 120 seconds for the 10MB/6000-token
            # review transport, while retaining actual target-voice checks.
            width=scene_high-scene_low
            while width<120:
                width=min(120,width*2)
                center=(scene_low+scene_high)/2
                pair=(max(0,center-width/2),min(source['duration_s'],center+width/2))
                if pair not in contexts:contexts.append(pair)
            for attempt,(low,high) in enumerate(contexts,1):
                if not self.route._allowed_record(dict(observation_id='',source_sha256=source['sha256'],
                        source_start_s=low, source_end_s=high)):
                    continue
                voice = CharacterVoices(service.folder/(json_sha(dict(role=role,actor_id=aid))[:10]+f'_{attempt}'))
                media, source_binding = voice.source_media(source, dict(source_start_s=low,source_end_s=high),pad_s=0)
                duration = source_binding['end_s']-source_binding['start_s']
                try:
                    spans,answer=find_and_audition(self,voice,media,binding['appearance'],duration,
                        actor_id=aid,source_binding=source_binding)
                except VoiceUnavailable as error:
                    failures.append(dict(attempt=attempt,error=str(error),context=[low,high],
                        kind='protocol' if isinstance(error,SourceVoiceProtocolError) else 'voice'))
                    parts=unheard_parts((low,high),failures)
                    if failures[-1]['kind']=='protocol' and parts:
                        # 02 (d82361fc0bee): Qwen aborted the same widened 120 s clip twice while
                        # its inner 66 s had already been heard. Listen to the new outer parts
                        # alone; the widened clip's failure is then replaced by their answers.
                        failures[-1]['kind']='protocol_replaced_by_parts'
                        failures[-1]['parts']=[list(p) for p in parts]
                        contexts.extend(p for p in parts if p not in contexts)
                    continue
                write_json(voice.folder/'voice_evidence.json',dict(role=role,actor=aid,
                    appearance=binding['appearance'],source=source_binding,observations=answer,
                    used=spans,failures=failures))
                service.voices[role] = voice.enroll(role, media, spans['target'])
                sample=voice.folder/('sample_'+json_sha(role)[:12]+'.wav')
                voice_bindings[role]=dict(review_policy=VOICE_POLICY,actor_id=aid,source_sha256=source['sha256'],voice_id=service.voices[role],
                    sample_path=str(sample),enrollment_path=str(voice.enrollment_path),
                    evidence_path=str(voice.folder/'voice_evidence.json'),evidence_sha256=sha256_file(voice.folder/'voice_evidence.json'))
                self.state.set_artifact('grounded_source_voices',dict(roles=voice_bindings,speech_folder=str(service.folder),
                    speech_folders=list(dict.fromkeys([str(service.folder),*service.reuse_folders]))))
                break
            if role not in service.voices:
                # Stop hardening 11-17 (user: too little original voice -> preset voice, not a recast
                # and not a stop). Unusable audition evidence counts the same: nothing was verified.
                pending.append((role,aid,failures))
        if pending:
            genders=preset_genders(self,lock,pending)
            for role,aid,failures in pending:
                preset_voice(self,service,voice_bindings,role,aid,failures,genders.get(role))
        write_json(service.folder/'cast_voices.json',service.voices)
        self.state.set_artifact('grounded_source_voices',dict(roles=voice_bindings,speech_folder=str(service.folder),
            speech_folders=list(dict.fromkeys([str(service.folder),*service.reuse_folders]))))
        # Silent cast members have no synthesized voice; the render cast still
        # includes their visual identity without borrowing somebody else's timbre.
        service.voices.update({role:None for role in lock['cast_bindings'] if role not in service.voices})
        return service

    def prepare(self, cache, output):
        from .subtitle_library import refine_selected_subtitles
        while True:
            windows, lock = self.lock()
            self.plan_adaptation(lock)
            if self.voice_service is None:
                self.voice_service = self.source_voices(lock)
            before = len(self.shots)
            try:
                voices, script, timeline, lines, audio, captions = joint_dialogue.prepare(
                    lock, windows, self.route.reference, self.state, self.glm, cache, Path(output),
                    voice_service=self.voice_service, script_review=self.review_script)
            except joint_dialogue.CoverageError as error:
                self.explore(dict(problem=str(error),instruction='保留完整剧情和演员，补充同场真实讲话与反应画面。'))
                if len(self.shots)<=before:
                    raise LibraryStopped('reference_story_no_new_coverage') from error
                continue
            # The joint scheduler uses global actor IDs; rendering binds source-local IDs.
            shots={s['slot_id']:s for s in self.shots}
            for row in timeline.rows:
                shot=shots[row['slot_id']] if row.get('slot_id') in shots else next(
                    s for s in self.shots if s['window_id']==row['window_id'] and
                    s['source_start_s']<=row['source_in_s']+.001 and
                    s['source_end_s']>=row['source_out_s']-.001)
                row['role_ids']=[shot['local_actor_bindings'][a] for a in shot['visible']]
                row['speaking_role_ids']=[shot['local_actor_bindings'][a] for a in shot['speaking']]
                row['actor_ids']=shot['visible']
                row['speaking_actor_ids']=shot['speaking']
            from .schedule_audit import bind_performance_permissions
            from .dialogue_finish import remove_micro_tails,verified_masks
            bind_performance_permissions(lock,lines,timeline.rows,shots)
            technical_finish=remove_micro_tails(timeline.rows,lines)
            technical_finish['subtitle_masks']=verified_masks(timeline.rows,self.browser.proxies,self.browser.visual)
            write_json(Path(output)/'technical_finish.json',technical_finish)
            if technical_finish['subtitle_masks'] is None:
                refine_selected_subtitles(timeline.rows,windows,dict(sources=list(self.route.sources.values())),self.glm,cache)
                # Preserve dialogue regions already checked by subtitle-blind viewing
                # as a safety margin around the more precise final mosaic treatment.
                for row in timeline.rows:
                    for mask in self.browser.proxies[row['window_id']]['source_masks']:
                        if mask['start_s']<row['source_out_s'] and row['source_in_s']<mask['end_s']:
                            row.setdefault('source_subtitle_treatments',[]).append(dict(mask,mode='mosaic',opacity=.92,
                                block_px=18,refinement_stage='retrieval_verified_dialogue_region'))
            from .scene_casting import audit_line_bindings
            binding_audit=audit_line_bindings(lock,lines,timeline.rows,self.shots,voices)
            write_json(Path(output)/'line_binding_audit.json',binding_audit)
            if not binding_audit['passed']:raise LibraryStopped('scheduled_actor_voice_binding_conflict')
            reference_story.check_coverage(script,self.contract)
            reference_story.check_timeline(timeline.rows,lines,self.contract)
            write_json(Path(output)/'material_lock.json',lock)
            self.state.set_artifact('material_lock',lock)
            return lock,windows,voices,script,timeline,lines,audio,captions
