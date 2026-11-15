"""Plan required visual action, search it, and fit original character voices."""
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import subprocess

from .continuous_dialogue import ContinuousDialogue, dialogue_turns, fit_continuous_frames
from .joint_dialogue import FPS, place_audio
from .state import LibraryStopped, write_json
from .autonomous_material import dump


POLICY = 'action_dialogue_v1'


def validate_intent(plan, turns, required_functions=()):
    if not isinstance(plan, dict) or not isinstance(plan.get('setting'), str) or not plan['setting'].strip():
        raise ValueError('Describe the visual setting and participant relationship')
    beats, queries = plan.get('beats'), plan.get('queries')
    by_turn = {t['turn_id']: t for t in turns}
    if not isinstance(beats, list) or not 2 <= len(beats) <= 4:
        raise ValueError('Identify 2–4 indispensable visible events by dramatic function')
    if required_functions and len(beats) != len(required_functions):
        raise ValueError(f'Use exactly {len(required_functions)} beats, one for each required visual function')
    beat_ids = set()
    for beat in beats:
        if (not isinstance(beat, dict) or not isinstance(beat.get('id'), str) or not beat['id']
                or beat['id'] in beat_ids or beat.get('turn_id') not in by_turn
                or any(not isinstance(beat.get(k), str) or not beat[k].strip()
                       for k in ('action','function','equivalents'))):
            raise ValueError('Each beat requires a unique ID, a real reference turn, an action, and an equivalent')
        if len(beat['action']) > 55:
            raise ValueError('Each action must describe one concise visible event, not a sequence of literal details')
        beat_ids.add(beat['id'])
    order = {t['turn_id']:i for i,t in enumerate(turns)}
    if [order[b['turn_id']] for b in beats] != sorted(order[b['turn_id']] for b in beats):
        raise ValueError('Keep key events in their reference order')
    covered = set()
    if not isinstance(queries, list) or not 3 <= len(queries) <= 8:
        raise ValueError('Supply 3–8 concrete visual searches across all beats')
    for query in queries:
        if (not isinstance(query, dict) or not isinstance(query.get('text'), str)
                or not query['text'].strip() or query.get('beat') not in beat_ids):
            raise ValueError('Each search must name a beat and concrete visible clues')
        covered.add(query['beat'])
    if covered != beat_ids:
        raise ValueError('Search for every indispensable event')
    return plan


def validate_edit(plan, intent, turns, duration):
    if not isinstance(plan, dict) or plan.get('decision') not in {'use','skip'} or not plan.get('evidence'):
        raise ValueError('Return a decision with evidence')
    if plan['decision'] == 'skip':
        return plan
    refs = {t['turn_id']: t['role'] for t in turns}
    order = {t['turn_id']: i for i,t in enumerate(turns)}
    beats = {b['id']:b for b in intent['beats']}
    rows, cast = plan.get('lines'), plan.get('cast')
    if not isinstance(rows, list) or not 2 <= len(rows) <= 14 or not isinstance(cast, dict):
        raise ValueError('A usable edit needs a cast and concise dialogue')
    last_end, last_turn = 0., -1
    anchored, actors, covered, attributable = set(), {}, [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid dialogue row')
        tid = row.get('turn_id')
        a,b = row.get('start_s'),row.get('end_s')
        if (type(a) not in (float,int) or type(b) not in (float,int)
                or not math.isfinite(a) or not math.isfinite(b)
                or not last_end <= a < b <= duration+.001 or b-a < .42):
            raise ValueError('Use ordered, disjoint source intervals')
        if not isinstance(row.get('acting'),str) or not row['acting'].strip():
            raise ValueError('Each row needs an observed action')
        observed = row.get('visible_speaker')
        visual = row.get('view')=='visual'
        role = None
        if visual:
            if tid is not None or observed is not None or row.get('text') or row.get('actor') is not None:
                raise ValueError('A visual insert has null actor, turn and speaker, and empty text')
        else:
            if not isinstance(tid,str) or tid not in refs or order[tid]<=last_turn:
                raise ValueError('Keep spoken reference turns in order')
            role = refs[tid]
            actor = row.get('actor',role)
            if (not isinstance(actor,str) or not isinstance(cast.get(actor),str) or not cast[actor].strip()
                    or (role in actors and actors[role]!=actor)):
                raise ValueError('Bind a stable supplied actor ID to each spoken reference role')
            actors[role] = actor
            if not isinstance(row.get('text'),str) or not row['text'].strip():
                raise ValueError('Spoken rows need concise words')
            if row.get('view')=='speaker' and observed==actor:
                anchored.add(role)
            elif row.get('view')!='reaction' or observed is not None or role not in anchored:
                raise ValueError('A speaker must be visible and correct; reaction requires an established voice and no visible speech')
            last_turn = order[tid]
        ids = row.get('beats')
        if not isinstance(ids,list) or any(not isinstance(k,str) or k not in beats for k in ids):
            raise ValueError('Use only known action beat IDs')
        for k in ids:
            if not covered or covered[-1] != k:
                covered.append(k)
            if visual or refs[beats[k]['turn_id']] == role:
                attributable.add(k)
        last_end = b
    if (covered != list(beats) or attributable != set(beats)
            or anchored != set(actors) or len(actors)<2
            or len(set(actors.values()))!=len(actors)
            or len({cast[x] for x in actors.values()})!=len(actors)):
        raise ValueError('The edit must show all key actions in order and establish every used actor')
    return plan


def assign_reference_turns(lines, facts_cast, turns):
    """Resolve reference IDs in code while preserving the actor and turn order."""
    next_ref = 0
    for row in lines:
        if row.get('view') == 'visual':
            row.update(turn_id=None,actor=None,speech=None,text='',visible_speaker=None)
            continue
        role = facts_cast[row['actor']]['role']
        while next_ref < len(turns) and turns[next_ref]['role'] != role:
            next_ref += 1
        if next_ref == len(turns):
            raise ValueError('Use fewer spoken rows and keep the reference roles in order')
        row['turn_id'] = turns[next_ref]['turn_id']
        next_ref += 1


class ActionDialogue(ContinuousDialogue):
    def director_brief(self):
        raw = self.route.config.get('material_brief') or ''
        if raw.startswith('{'):
            brief = json.loads(raw)
            functions = brief.get('required_visual_functions',[])
            if (not isinstance(functions,list) or not 2<=len(functions)<=4
                    or any(not isinstance(x,str) or not x.strip() for x in functions)):
                raise LibraryStopped('action_dialogue:invalid_required_visual_functions')
            return brief
        return dict(request=raw,required_visual_functions=[])

    def earlier_observations(self):
        checkpoint = self.route.config.get('resume_from',{}).get('failed_task')
        visited = set()
        while checkpoint and checkpoint not in visited:
            visited.add(checkpoint)
            root = Path(checkpoint)
            evidence = root/'autonomous_material/action_rejections.json'
            if evidence.is_file():
                visual = [r for r in json.loads(evidence.read_text())
                          if r.get('stage','visual') == 'visual'
                          and not r.get('reason','').startswith('Insufficient isolated original voice')]
                if visual:
                    return visual
            state = json.loads((root/'library_state.json').read_text())
            checkpoint = state['input_lock']['configuration'].get('resume_from',{}).get('failed_task')
        return []

    def find_intent(self, turns):
        brief = self.director_brief()
        functions = brief['required_visual_functions']
        reference_turns = [dict(turn_id=t['turn_id'],role=t['role'],
                                functions=[r.get('function') for r in t['lines']]) for t in turns]
        if not functions:
            for row,turn in zip(reference_turns,turns):
                row['text'] = '，'.join(r['text'] for r in turn['lines'])
        prompt = (
            'director_brief是用户本次明确要的画面重点及改编范围，优先级高于原作细节。'
            'brief只要求某段互动时，只拆这段；其它情节不能自动升为必拍。'
            '若required_visual_functions非空，严格一项只产出一个beat，按该顺序。'
            '不能把表演举例、后续冲突、具体姿势、道具、额外承诺或数额写进必需行为。'
            '你给不同画风、不同角色和物件的素材找表演。先从参考确定贯穿整场的主要关系和冲突，'
            '再提炼2至4个决定因果的可见事件。画面必须演出关系、互动如何变化以及另一方的反应。'
            'action写可迁移的具体行为，例如一方出示物件、另一方查看后指向异常、前者当场回应。'
            '“正在聊天”不够。“同一把工具、同一个商品或相同打斗方式”也不属于必要条件。'
            '原片道具、精确操作、原句、暴力表现和次要人物只在无法替代其叙事功能时才是必要信息。'
            '可省去重复台词、无关引子及插曲式收尾。每项保留原来的角色关系，关联一个实际参考turn_id。'
            'action每项只写一个可见互动，不超过55字。'
            'equivalents写两种不同物件或环境下能真正演出同作用的行为，不换说法重复原片动作。'
            '检索词用素材可能描述的动作、手持物、反应、设施和站位。去掉原片专名、人物物种、'
            '具体商品、原对白和影片里才有的装置。总共3至8条，每项至少1条；同一动作可用不同生活场景找。'
            '返回JSON {"setting":"画面要让观众看懂的关系与地点功能","beats":['
            '{"id":"b1","turn_id":"t1","action":"不绑定原片道具的可见行为","function":"为何重要",'
            '"equivalents":"两个不同物件或环境的同功能画面"}],"queries":[{"beat":"b1","text":"检索词"}]}。\n'
            +dump(dict(director_brief=brief,reference={k:self.route.reference.get(k) for k in ('theme','intended_takeaway')},
                       turns=reference_turns,earlier_observations=self.earlier_observations()[-4:]))
        )
        def check(value):
            return validate_intent(value,turns,functions)
        plan = self.call('material_scene_discovery_decide_1',prompt,check)
        if functions:
            plan = dict(plan,required_visual_functions=functions)
        write_json(self.root/'visual_intent.json',plan)
        return plan

    def search_actions(self, intent):
        hits_by_window = defaultdict(dict)
        for query in intent['queries']:
            hits = self.route.index.search(dict(need_id=query['beat'],query_text=query['text']),
                                           channels=['bm25'],top_k=140,final_k=140)['hits']
            seen = set()
            for hit in hits:
                wid = hit['record']['window_id']
                if wid not in self.records or wid in seen:
                    continue
                seen.add(wid)
                rank = len(seen)
                beat = query['beat']
                best = hits_by_window[wid].get(beat)
                if best is None or rank < best[0]:
                    hits_by_window[wid][beat] = (rank,hit['record_id'])
                if rank >= 24:
                    break
        def rank_key(wid):
            ranks = hits_by_window[wid].values()
            return (-sum(1/(2+r) for r,_ in ranks),wid)
        ordered = sorted(hits_by_window,key=rank_key)
        # Give each beat's best results a seat, including rare actions.
        lead = [w for beat in intent['beats'] for w in
                sorted((w for w in ordered if beat['id'] in hits_by_window[w]),
                       key=lambda w:hits_by_window[w][beat['id']][0])[:3]]
        return [dict(self.card(w,[i for _,i in hits_by_window[w].values()]),
                     action_queries=hits_by_window[w]) for w in dict.fromkeys(lead+ordered)]

    def candidate_clip(self, card):
        from .subtitle_library import prepare_visible_window
        wid = card['window_id']
        first = self.records[wid][0]
        source = self.route.sources[first['source_sha256']]
        start,end = first['window_source_start_s'],first['window_source_end_s']
        def allowed(a,b):
            return self.route._allowed_record(dict(first,source_start_s=a,source_end_s=b))
        if not allowed(start,end):
            start,end = card['source_start_s'],card['source_end_s']
        # Keep the surrounding reaction within a short viewing request.
        candidates = [(start-4,end+16),(start-8,end+8),(start,end+8),(start-8,end),(start,end)]
        spans = [(a,b) for a,b in candidates if 0<=a<b<=source['duration_s'] and b-a<=42 and allowed(a,b)]
        if not spans:
            raise ValueError('No permitted source range')
        start,end = spans[0]
        members = [dict(window_id=w) for w,rows in self.records.items()
                   if rows[0]['source_sha256']==source['sha256']
                   and rows[0]['window_source_start_s']<end and rows[0]['window_source_end_s']>start]
        clip = prepare_visible_window(source,start,end,self.root/'action_media',wid)
        return dict(windows=members),clip,source

    def ground_edit(self, voice, source, clip, turns, intent):
        from .audio_review import review_audio_video
        from .character_voice import MODEL, VoiceUnavailable
        from .media import sha256_file

        media,binding = voice.source_media(source,clip)
        offset = clip['source_start_s']-binding['start_s']
        duration = binding['end_s']-binding['start_s']
        roles = list(dict.fromkeys(t['role'] for t in turns))
        facts_prompt = (
            '逐段听实际原声并看画面，顶部LOCAL是相对秒数。先根据双方站位、物件谁递给谁、谁发问、谁回应，'
            '独立辨认参与者和对应故事角色。镜头中的人物经常只是听者，不能当作画外发声者。'
            '以能听到的原语句、前后声线和可见口部动作确认说话人；少量非语言发声不等于说出完整对白。'
            '逐条列实际听清的短句或独立发声，各只记发声的起止秒数，不能包括长停顿或混声。'
            'actor用简短ID，角色填给定故事角色，若只是旁观者填null；visible只有能实际看到此人讲话时才填true。'
            '没把握的声音不要列。此处只观察，不改写对白或决定剪辑。'
            '返回JSON {"actors":[{"id":"a","appearance":"可辨外观","role":"给定角色或null","evidence":"实物交接和行动依据"}],'
            '"utterances":[{"id":"u1","actor":"a","start_s":0,"end_s":1,"heard":"实际原声","visible":true,"evidence":"声音和画面的身份依据"}]}。\n'
            +dump(dict(roles=roles,visual_functions=intent.get('required_visual_functions') or intent['beats']))
        )
        approved_path = os.environ.get('OMNI_ACTION_REVIEWED_SOURCE')
        approved = None
        reuse = {}
        if approved_path:
            record = json.loads(Path(approved_path).read_text())
            if record.get('binding')==dict(binding,media_sha256=sha256_file(media)):
                approved = record
                for actor,spec in record.get('verified_voice',{}).items():
                    path = Path(spec['receipt'])
                    receipt = json.loads(path.read_text())
                    if (sha256_file(path)!=spec['receipt_sha256'] or receipt['status']!='received'
                            or receipt['model']!=MODEL or receipt['seconds']<3
                            or sha256_file(spec['sample'])!=receipt['sample_sha256']
                            or not receipt.get('voice')):
                        raise VoiceUnavailable('Reviewed voice provenance could not be confirmed')
                    reuse[actor] = receipt['voice']
                write_json(voice.folder/'reviewed_source_binding.json',dict(
                    path=approved_path,sha256=sha256_file(approved_path),binding=record['binding']))
        factual = approved or review_audio_video(media,voice.folder/'source_facts',prompt=facts_prompt).get('parsed')
        if not isinstance(factual,dict):
            raise VoiceUnavailable('Original audio observation is missing')
        facts_cast, utterances = {}, {}
        for actor in factual.get('actors') or []:
            if (isinstance(actor,dict) and isinstance(actor.get('id'),str)
                    and actor.get('role') in roles and actor.get('appearance') and actor.get('evidence')):
                facts_cast[actor['id']] = actor
        for row in factual.get('utterances') or []:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str):
                continue
            a,b = row.get('start_s'),row.get('end_s')
            if (row.get('actor') in facts_cast and type(a) in (float,int) and type(b) in (float,int)
                    and 0<=a<b<=duration+.01 and .3<=b-a<=15 and row.get('heard') and row.get('evidence')):
                utterances[row['id']] = row
        if len({facts_cast[r['actor']]['role'] for r in utterances.values()})<2:
            raise VoiceUnavailable('Original source does not confirm two speaking roles')
        candidates_by_actor = defaultdict(list)
        for spoken in sorted(utterances.values(),key=lambda r:r['start_s']):
            if (spoken.get('visible') is True and spoken['end_s']-spoken['start_s']>=.8
                    and offset<=spoken['start_s']<=offset+clip['duration_s']):
                candidates_by_actor[spoken['actor']].append(spoken)
        i = 0
        for actor,candidates in candidates_by_actor.items():
            if approved and any(r['id']==facts_cast[actor].get('anchor_id') for r in candidates):
                continue
            for spoken in candidates[:3]:
                i += 1
                a,b = max(0,spoken['start_s']-.65),min(duration,spoken['end_s']+.65)
                focus = voice.folder/f'anchor_{i}.mp4'
                if not focus.exists():
                    part = focus.with_suffix('.part.mp4')
                    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-ss',str(a),
                        '-i',str(media),'-t',str(b-a),'-c:v','libx264','-preset','ultrafast','-threads','1',
                        '-c:a','aac',str(part)],check=True,capture_output=True,timeout=30)
                    part.replace(focus)
                focused = review_audio_video(focus,voice.folder/f'anchor_review_{i}',prompt=(
                    '只听看这几秒。比较每位人物嘴的开合，谁说了实际听到的完整句？'
                    '不能因拿物、做动作或居中就当作说话者。准确辨认实际开口者的外观。'
                    'confirmed仅表示画面确实显示在发声，不是纯画外音或字幕提示。'
                    '返回JSON {"confirmed":true,"appearance":"可辨外观","evidence":"原声和口部变化",'
                    '"same_utterance":true}。\n'
                    +dump(dict(heard=spoken['heard']))
                )).get('parsed')
                if (isinstance(focused,dict) and all(focused.get(k) is True for k in
                        ('confirmed','same_utterance'))
                        and focused.get('appearance') and focused.get('evidence')):
                    facts_cast[actor] = dict(facts_cast[actor],appearance=focused['appearance'],
                                             anchor_id=spoken['id'],anchor_evidence=focused['evidence'])
                    break
        facts_cast = {a:v for a,v in facts_cast.items() if v.get('anchor_id')}
        utterances = {k:v for k,v in utterances.items() if v['actor'] in facts_cast}
        if len({v['role'] for v in facts_cast.values()})<2:
            raise VoiceUnavailable('Original source lacks two confirmed on-screen speech anchors')
        write_json(voice.folder/'grounded_speakers.json',facts_cast)
        prompt = (
            '给一处已确认有关键动作的原片做简短配音剪辑，顶部LOCAL是原片秒数。'
            '已核验的演员和原声见actors、utterances。不能改这些演员的故事角色或把听者当发声者。'
            'appearance是短片确认的发声者，evidence可能描述同一方实际做动作的另一位。'
            '双方各自的同行者可以替同侧开口，但不能将对面的参与者当成本方发声者；'
            '按整场实际站位和物件交接核对，若短片确认的两位演员不在实际互动的两边，就skip。'
            '剪辑按实际互动的因果取顺序、不重叠的短片段。只取edit_range_s内，区间至少0.42秒。'
            '允许跳过空白、保留无声商品或动作特写；选货、交付、破损细节和回应有必要时就留下。'
            '总共4至7段，短对白只保留必要的两三句，台词每秒约4个汉字，避免重复。'
            '至少两位演员各有一句，首次发声用actors内已复核的anchor_id原句。'
            '只有已复核的anchor_id可以标speaker，其他对白只能用确实无人开口的同场reaction镜头。'
            '对白必须引用一个同演员的utterance ID（speech），start_s对准其实际开口，约0.5秒内。'
            '标了sample_only的原声只作音色样本，不能用来安排对白。'
            '程序会给台词分配参考ID，不要输出turn_id。短句按实物和动作改写，省去其它情节。'
            '画外音只能用已建立的声音加同场无人开口的镜头。'
            '关键动作可以由相邻特写、说话和对方回应合演，均可标同一beats。'
            '已有复刻音色的演员直接复用；其余演员从voices选累计至少3秒的本人原声id，不能包含别人。'
            '只输出JSON {"decision":"use或skip","evidence":"实际动作证据",'
            '"lines":[{"actor":"演员ID或null","speech":"u1或null","start_s":0,"end_s":2,'
            '"text":"简短配音；无声留空","view":"speaker、reaction或visual","acting":"实际可见动作","beats":["b1"]}],'
            '"voices":{"a":["u1","u2"]}}。\n'
            +dump(dict(intent=intent,edit_range_s=[offset,offset+clip['duration_s']],
                       actors=list(facts_cast.values()),utterances=list(utterances.values()),voice_available=list(reuse),
                       turns=[dict(turn_id=t['turn_id'],role=t['role'],text='，'.join(r['text'] for r in t['lines']))
                              for t in turns]))
        )
        for attempt in range(2):
            saved_review = approved.get('received_proposal') if approved and attempt==0 else None
            if saved_review:
                from .audio_review import _result
                saved_dir = Path(saved_review['path'])
                response = saved_dir/'response.json'
                saved_state = json.loads((saved_dir/'state.json').read_text())
                request = json.loads((saved_dir/'request.json').read_text())
                if (saved_state['status']!='received' or sha256_file(response)!=saved_review['sha256']
                        or saved_state['response_sha256']!=saved_review['sha256']
                        or request['video_sha256']!=sha256_file(media) or request['prompt']!=prompt):
                    raise VoiceUnavailable('Previously reviewed plan does not match this exact video and prompt')
                review = _result(json.loads(response.read_text()),saved_state,cached=True)
                write_json(voice.folder/'source_proposal_reuse.json',saved_review)
            else:
                review = review_audio_video(media,voice.folder/f'source_review_{attempt+1}',prompt=prompt)
            value = review.get('parsed')
            try:
                if not isinstance(value,dict):
                    raise VoiceUnavailable('Original audio review did not return an edit')
                if value.get('decision')=='skip':
                    raise VoiceUnavailable(str(value.get('evidence') or 'Original audio did not support this scene'))
                rows = value.get('lines')
                if not isinstance(rows,list) or any(not isinstance(r,dict) or
                        type(r.get('start_s')) not in (float,int) or type(r.get('end_s')) not in (float,int)
                        for r in rows):
                    raise VoiceUnavailable('Invalid source-clock edit')
                rows = sorted(rows,key=lambda r:(r['start_s'],r['end_s']))
                if approved:
                    for witness in approved.get('required_visual_evidence',[]):
                        if not any(min(r['end_s'],witness['end_s'])-max(r['start_s'],witness['start_s'])
                                   >= .75*(witness['end_s']-witness['start_s']) for r in rows):
                            rows.append(dict(witness,view='visual'))
                    rows.sort(key=lambda r:(r['start_s'],r['end_s']))
                cast = {}
                for row in rows:
                    if row.get('view')=='visual':
                        continue
                    if row.get('view')=='reaction' and row.get('actor') is None and row.get('speech') is None:
                        matches = [u for u in utterances.values() if u['actor'] in cast
                                   and u.get('visible') is False and not u.get('sample_only')
                                   and abs(u['start_s']-row['start_s'])<=.25
                                   and row['end_s']<=u['end_s']+.25]
                        if len(matches)==1:
                            row.update(actor=matches[0]['actor'],speech=matches[0]['id'])
                    actor = row.get('actor')
                    spoken = utterances.get(row.get('speech'))
                    if (not spoken or spoken.get('sample_only') or spoken['actor']!=actor
                            or abs(row['start_s']-spoken['start_s'])>.51
                            or (row.get('view')=='speaker' and facts_cast[actor].get('anchor_id')!=spoken['id'])
                            or (actor not in cast and facts_cast[actor].get('anchor_id')!=spoken['id'])):
                        raise VoiceUnavailable('Match each line to the observed speaker and onset; use the verified anchor for the first line')
                    cast[actor] = facts_cast[actor]['appearance']
                assign_reference_turns(rows,facts_cast,turns)
                edit = dict(value,cast=cast,lines=[
                    dict(r,start_s=round(r['start_s']-offset,6),end_s=round(r['end_s']-offset,6),
                         visible_speaker=r.get('actor') if r.get('view')=='speaker' else None) for r in rows])
                validate_edit(edit,intent,turns,clip['duration_s'])
                ids_by_actor = value.get('voices')
                needing_voice = set(cast)-set(reuse)
                if approved:
                    ids_by_actor = {a:approved['samples'][a] for a in needing_voice}
                if not isinstance(ids_by_actor,dict) or set(ids_by_actor)-set(reuse)!=needing_voice:
                    raise VoiceUnavailable('Voice samples must cover just the speaking actors')
                voice_rows = []
                for actor,ids in ids_by_actor.items():
                    if actor in reuse:
                        continue
                    if (not isinstance(ids,list) or any(not isinstance(i,str) or i not in utterances
                            or utterances[i]['actor']!=actor for i in ids)):
                        raise VoiceUnavailable('Use only the observed utterances of this voice')
                    voice_rows.append(dict(actor=actor,samples=[dict(utterances[i],
                        evidence=utterances[i]['heard']+'｜'+utterances[i]['evidence']) for i in ids]))
                samples = voice.check_samples(voice_rows,needing_voice,duration)
                return edit,media,binding,voice_rows,samples,reuse
            except (ValueError,KeyError,TypeError) as exc:
                if attempt==1:
                    raise VoiceUnavailable(str(exc)) from exc
                prompt += '\n上次答案未通过。按已核验原声修正：'+str(exc)
                if value is not None:
                    prompt += '\n上次答案：'+dump(value)

    def run(self):
        from .character_voice import CharacterVoices, VoiceUnavailable

        turns = dialogue_turns(self.route.lines)
        intent = self.find_intent(turns)
        cards = self.search_actions(intent)
        prior = [dict(self.card(r['window_id']),earlier_observation=r['reason'])
                 for r in self.earlier_observations() if r.get('window_id') in self.records]
        cards = list({c['window_id']:c for c in cards+prior}.values())
        prior_ids = {c['window_id'] for c in prior}
        cards.sort(key=lambda c:c['window_id'] not in prior_ids)
        write_json(self.root/'action_search.json',cards)
        failures = []
        seen = set()
        by_turn = {t['turn_id']:t for t in turns}
        for round_no in range(1,4):
            page = [c for c in cards if c['window_id'] not in seen][:22]
            if not page:
                break
            by_id = {c['window_id']:c for c in page}
            prompt = ('你决定实际看片顺序。优先最可能在同一互动里演出全部关键动作、双方反应和因果的候选。'
                      '同类地点或同框只是线索。索引描述不是看见的证据。'
                      '从本页选1至3个最可能的窗口。若线索偏了，可另给最多3个新的检索词，下一轮试。'
                      '返回JSON {"ids":["窗口ID"],"reason":"为什么优先看","new_queries":[{"beat":"原事件ID","text":"新检索词"}]}。\n'
                      +dump(dict(intent=intent,candidates=page,previous=failures[-6:])))
            def rank(value):
                ids = value.get('ids') if isinstance(value,dict) else None
                if (not isinstance(ids,list) or not 1<=len(ids)<=3 or len(set(ids))!=len(ids)
                        or any(w not in by_id for w in ids) or not value.get('reason')):
                    raise ValueError('Choose 1–3 supplied window IDs with a reason')
                extra = value.get('new_queries',[])
                if (not isinstance(extra,list) or len(extra)>3 or any(not isinstance(q,dict)
                        or q.get('beat') not in {b['id'] for b in intent['beats']}
                        or not isinstance(q.get('text'),str) or not q['text'].strip() for q in extra)):
                    raise ValueError('New queries must be short searches for existing visual events')
                return value
            ranking = self.call(f'material_scene_discovery_decide_{round_no+1}',prompt,rank)
            write_json(self.root/f'action_rank_{round_no}.json',ranking)
            for wid in ranking['ids']:
                seen.add(wid)
                try:
                    candidate,clip,source = self.candidate_clip(by_id[wid])
                except ValueError as exc:
                    failures.append(dict(window_id=wid,reason=str(exc)))
                    continue
                prompt = (
                    '看片检验必需动作。视频无原声，不能声称听见对白。LOCAL为本段秒数。'
                    '逐项确认intent中的可见行为及其戏剧作用。同作用物件可换。'
                    'director_brief的required_visual_functions界定要核实的功能，intent的动作写法只是可行表现，'
                    '确认功能在画面中成立即可；不要追加具体姿势、道具、口头承诺、争执后果为门槛。'
                    '纯聊天、相同表情或原字幕提到，都不能替代所需动作。'
                    '任一关键动作缺失、镜头跨标题或演员身份不稳定时，返回'
                    '{"decision":"skip","evidence":"具体缺失动作或时点"}。'
                    '可用时，按实际画面简写台词，保留关键因果，可省重复和无关前后缀。'
                    '台词里的具体动作或物件必须与现场画面一致，不能只用嘴交代核心动作。'
                    '每句至少留出能自然讲完的原片时长，通常每秒4至5个中文字，不能靠挪用其他角色画面凑时长。'
                    '按原片时间、参考对白顺序取不重叠区间，允许跳过片内空白。'
                    '起点贴着正确演员实际开口时刻；先做动作再开口时，不能提早配音。'
                    '首次发声要看到正确演员实际讲话；反应镜头只允许已建立的声音，且画面无人开口。'
                    '货架、价签、商品细节或无人说话的实际动作可单独用visual镜头，保留留白。'
                    'visual行必须turn_id=null、actor=null、visible_speaker=null、text为空，仍可填它实际呈现的beats。'
                    'cast使用任意简短演员ID（a、b等）标注外观。每条发言的actor是这个ID，'
                    '程序通过turn_id自动推导故事角色，别用原角色名或造别名。visible_speaker填同一个演员ID。'
                    'beats填这个区间实际演出的关键动作ID，每项一次并按顺序。'
                    '输出JSON {"decision":"use","evidence":"可见动作如何形成因果","cast":{"a":"可辨外观"},'
                    '"lines":[{"turn_id":"给定参考ID或null","actor":"a或null","start_s":0,"end_s":2,"text":"适配对白",'
                    '"view":"speaker、reaction或visual","visible_speaker":"a或null","acting":"可见动作证据","beats":["b1"]}]}。\n'
                    +dump(dict(director_brief=self.director_brief(),intent=intent,
                               turns=[dict(turn_id=t['turn_id'],role=t['role'],text='，'.join(r['text'] for r in t['lines']))
                                      for t in turns],duration_s=clip['duration_s']))
                )
                def check(value):
                    return validate_edit(value,intent,turns,clip['duration_s'])
                attempt = len(seen)
                try:
                    answer = self.call(f'material_scene_discovery_observe_{attempt}',prompt,check,clip['path'])
                except ValueError as exc:
                    if not str(exc).startswith('model_protocol_repair_exhausted:'):
                        raise
                    failures.append(dict(window_id=wid,reason=str(exc)))
                    write_json(self.root/'action_rejections.json',failures)
                    continue
                write_json(self.root/f'action_observed_{attempt}.json',dict(clip=clip,result=answer))
                if answer['decision']=='skip':
                    failures.append(dict(window_id=wid,reason=answer['evidence']))
                    write_json(self.root/'action_rejections.json',failures)
                    continue
                try:
                    voice = CharacterVoices(self.root/f'voices_{attempt}')
                    answer,media,binding,voice_rows,samples,reuse = self.ground_edit(voice,source,clip,turns,intent)
                except VoiceUnavailable as exc:
                    failures.append(dict(window_id=wid,stage='voice',reason=str(exc)))
                    write_json(self.root/'action_rejections.json',failures)
                    continue
                spoken = [r for r in answer['lines'] if r['view']!='visual']
                chosen = [by_turn[r['turn_id']] for r in spoken]
                actor_roles = {r['actor']:t['role'] for t,r in zip(chosen,spoken)}
                cast = {role:answer['cast'][actor] for actor,role in actor_roles.items()}
                # Reuse source masks and actor binding, then keep only the exact
                # observed intervals. Adjacent dialogue is not stretched into gaps.
                edit = dict(answer,turns=spoken,cast=cast)
                self.bind(candidate,clip,edit,chosen)
                self.edit_rows = []
                for row in answer['lines']:
                    a = math.ceil((clip['source_start_s']+row['start_s'])*FPS-1e-6)
                    b = math.floor((clip['source_start_s']+row['end_s'])*FPS+1e-6)
                    self.edit_rows.append(dict(row,turn_id=row.get('turn_id'),source_start_s=a/FPS,source_frames=b-a))
                edit_by_turn = {r['turn_id']:r for r in self.edit_rows if r['turn_id'] is not None}
                for line in self.lines:
                    row = edit_by_turn[line['line_id']]
                    line.update(source_start_s=row['source_start_s'],source_frames=row['source_frames'],action_beats=row['beats'])
                try:
                    self.voices,self.samples = voice.prepare(media,binding,cast,self.lines,voice_rows,
                                                            {actor_roles[k]:v for k,v in samples.items()},
                                                            {actor_roles[k]:v for k,v in reuse.items() if k in actor_roles})
                    self.shorten_overlong_lines(voice)
                    speech_lengths = {r['line_id']:r['voiced_s'] for r in self.lines}
                    self.frame_budget = fit_continuous_frames(
                        [r['source_frames'] for r in self.edit_rows],
                        [speech_lengths.get(r['turn_id'],0.) for r in self.edit_rows],self.route.edit_max_s)
                except (VoiceUnavailable,ValueError) as exc:
                    failures.append(dict(window_id=wid,reason=str(exc)))
                    write_json(self.root/'action_rejections.json',failures)
                    continue
                self.locked.update(editing_policy=POLICY,visual_intent=intent)
                retained = {rid for line in self.lines for rid in line['reference_line_ids']}
                self.locked['scenes'][0]['reference_lines'] = [r for r in self.route.lines if r['line_id'] in retained]
                write_json(self.root/'action_selection.json',dict(clip=clip,answer=dict(answer,cast=cast),failures=failures))
                self.state.set_artifact('material_lock',self.locked)
                write_json(Path(self.state.output)/'material_lock.json',self.locked)
                return self.selected_windows,self.locked
            if ranking.get('new_queries'):
                refreshed = self.search_actions(dict(intent,queries=ranking['new_queries']))
                cards = list({c['window_id']:c for c in refreshed+cards}.values())
                write_json(self.root/f'action_search_expanded_{round_no}.json',refreshed)
        raise LibraryStopped('action_dialogue:no_verified_action_and_voices:'+dump(failures[-8:]))

    def shorten_overlong_lines(self, voice):
        for line in self.lines:
            limit = math.floor(line['source_frames']/.55)/FPS-.11
            if line['voiced_s'] <= limit:
                continue
            original = line['text']
            count = sum(c.isalnum() for c in original)
            target = max(1, min(count-1, math.floor(count*limit/line['voiced_s']*.85)))
            def check(value):
                text = value.get('text') if isinstance(value,dict) else None
                if not isinstance(text,str) or not 1<=sum(c.isalnum() for c in text)<=target:
                    raise ValueError('Return the short spoken line within the requested character limit')
                return value
            reply = self.call('dialogue_rewrite',
                '复刻后的语音超出已核准的同一说话人画面。把台词缩到max_chars以内。'
                '保留谁在问或回答、与实物有关的关键信息。删客套、重复和语气词，不改说话者和动作。'
                '只返回JSON {"text":"短对白"}。\n'+dump(dict(text=original,role=line['role'],
                    visible_action=line['observed_action'],max_chars=target)),check)
            line.update(original_text=original,text=reply['text'])
            self.samples[line['line_id']] = voice.render_line(line,self.voices[line['role']])

    def prepare(self,cache,output):
        from .dialogue_cut import _Timeline

        scene = self.locked['scenes'][0]
        timeline = _Timeline(self.selected_windows)
        window = self.selected_windows[0]['window_id']
        lines = {r['line_id']:r for r in self.lines}
        anchored = set()
        for edit,frames in zip(self.edit_rows,self.frame_budget):
            line = lines.get(edit['turn_id'])
            role = line['role'] if line else scene['roles'][0]
            if line:
                line['visual_anchor'] = role not in anchored and edit['view']=='speaker'
                if line['visual_anchor']:
                    anchored.add(role)
            start = edit['source_start_s']
            row = timeline.append(scene,dict(window_id=window,role=role),start,frames,
                                  edit['view'],edit['turn_id'])
            row.update(source_out_s=start+edit['source_frames']/FPS,
                       speed=edit['source_frames']/frames,observed_action=edit['acting'],
                       action_beats=edit['beats'])
            if line is None:
                row.update(role=None,role_ids=[])
        audio,captions = place_audio(self.lines,timeline.rows,self.samples,cache,POLICY)
        script = dict(lines=self.lines,facts=[dict(line_id=r['line_id'],text=r['text'])
                     for r in scene['reference_lines']])
        write_json(Path(output)/'dialogue_script.json',script)
        return self.locked,self.selected_windows,self.voices,script,timeline,self.lines,audio,captions


def review_final_actions(video, folder, intent, rows):
    """Verify actual final frames at the retimed story beats."""
    from .audio_review import review_audio_video
    from .media import sha256_file
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    proxy = folder/'action_review_proxy.mp4'
    binding_file = folder/'action_proxy_binding.json'
    digest = sha256_file(video)
    if binding_file.exists():
        saved = json.loads(binding_file.read_text())
        if saved != dict(source_sha256=digest,proxy_sha256=sha256_file(proxy)):
            raise LibraryStopped('action_review:proxy_binding_changed')
    else:
        temporary = proxy.with_suffix('.part.mp4')
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-i',str(video),'-map','0:v:0','-map','0:a:0',
            '-filter_threads','1','-vf','scale=640:-2,fps=12','-c:v','libx264','-preset','veryfast',
            '-crf','25','-threads','1','-c:a','aac','-b:a','96k',str(temporary)],check=True,capture_output=True,timeout=120)
        temporary.replace(proxy)
        write_json(binding_file,dict(source_sha256=digest,proxy_sha256=sha256_file(proxy)))
    windows = defaultdict(list)
    for row in rows:
        for bid in row.get('action_beats',[]):
            windows[bid].append((row['start_s'],row['end_s']))
    beats = [dict(b,intervals_s=windows[b['id']]) for b in intent['beats']]
    required_functions = intent.get('required_visual_functions') or []
    observation_dir = folder/'action_observation'
    if required_functions:
        if len(required_functions)!=len(beats):
            raise LibraryStopped('action_review:invalid_visual_function_binding')
        # Explicit user requirements were paired with beats in order at planning
        # time. The planner's concrete staging is only a search example.
        beats = [dict(id=beat['id'],required_visual_function=function,intervals_s=beat['intervals_s'])
                 for beat,function in zip(beats,required_functions)]
        observation_dir = folder/'action_observation_functions'
    prompt = (
        '独立核对成片的关键画面。指定秒数内确实能看到的动作才可判成立。'
        '台词、字幕和推测不能证明发生了动作。可换物件，必须实际完成相同的可见交互或因果。'
        '如有required_visual_function，按这一项的实际可见功能核对，用画面中观察到的替代动作举证。'
        '每项如实写看到什么，缺失、看不清、仅对白提及都填false。'
        '返回JSON {"beats":[{"id":"原样填写","visible":true,"evidence":"实际可见的动作或缺失"}]}。\n'
        +dump(dict(setting=intent['setting'],beats=beats,
                   required_visual_functions=required_functions))
    )
    review = review_audio_video(proxy,observation_dir,prompt=prompt)
    parsed = review.get('parsed')
    observed = parsed.get('beats') if isinstance(parsed,dict) else None
    passed = (isinstance(observed,list) and len(observed)==len(beats)
              and all(isinstance(r,dict) and r.get('visible') is True
                      and isinstance(r.get('evidence'),str) and r['evidence'].strip() for r in observed)
              and {r.get('id') for r in observed}==set(windows))
    result = dict(passed=passed,video_sha256=digest,observed=observed,required=beats)
    write_json(folder/'action_verification.json',result)
    return result
