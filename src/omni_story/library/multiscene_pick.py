"""Choose and ground an ordered set of scenes before the shared cast is locked."""
import json

from . import scene_pick
from .state import LibraryStopped, json_sha, write_json


def observed_moments(observed, codes, lo, hi):
    local={r['role_id']:codes[r['entity_id']] for r in observed.get('roles',[])
           if r.get('identity_confirmed') and r.get('entity_id') in codes}
    segments=observed.get('story_segments',[])
    moments=[]
    for event in observed.get('events',[]):
        a,b=event.get('local_start_s'),event.get('local_end_s')
        if type(a) not in (int,float) or type(b) not in (int,float):continue
        a,b=max(a,lo),min(b,hi)
        if b-a<.3:continue
        mid=(a+b)/2
        if any(seg.get('kind') in ('title','credits','transition','ad','graphic') and
               seg['local_start_s']<=mid<seg['local_end_s'] for seg in segments):continue
        visible=sorted({local[r] for r in event.get('role_ids',[]) if r in local})
        if visible:
            moments.append(dict(at=[round(a,3),round(b,3)],actors=visible,
                actor=local.get(event.get('actor_id')),recipient=local.get(event.get('recipient_id')),
                action=event.get('observed_fact') or event.get('action','')))
    return moments


def supports_performance(rows,ref,cast):
    moments=[m for w in rows for m in w.get('moments',[])]
    if not moments:
        return {cast[r] for r in ref['roles']}-{None}<={a for w in rows for a in w['actors']}
    if not {cast[r] for r in ref['roles']}-{None}<={a for m in moments for a in m['actors']}:
        return False
    pairs={frozenset((m['actor'],m['recipient'])) for m in moments if m['actor'] and m['recipient']}
    targets={frozenset((cast[t['role']],cast[other])) for t in ref.get('turns',[])
             if t.get('on_screen') is not False and t.get('role') in cast and cast.get(t['role'])
             for other in t.get('addressee_roles',[]) if other in ref['roles'] and cast.get(other)}
    return all(pair in pairs for pair in targets)


def window_options(browser, data, selected):
    """Window clocks and published identity clues for text selection; never imply co-presence."""
    codes = {info.get('entity_id'): code for code, info in data.get('characters', {}).items() if info.get('entity_id')}
    result = {}
    for sid in sorted(selected):
        scene = data['scenes'][sid]
        rows = []
        for wid in scene['window_ids']:
            if wid not in browser.by_id:
                continue
            stored = browser.d.route.store.get(browser.d.records[wid][0]['observation_id'])
            window = stored['window']
            origin = window['source_start_s']
            start = max(origin, scene['source_start_s'])
            end = min(window['source_end_s'], scene['source_end_s'])
            if end - start < .3:
                continue
            observed = stored.get('received_observation') or window.get('observation', {})
            actors = sorted({codes[r['entity_id']] for r in observed.get('roles', [])
                             if r.get('identity_confirmed') and r.get('entity_id') in codes})
            rows.append(dict(window=wid, origin=origin, usable=[round(start-origin, 6),round(end-origin, 6)],
                             actors=actors,moments=observed_moments(observed,codes,start-origin,end-origin),
                             description=observed.get('summary') or browser.by_id[wid]['summary']))
        result[sid] = rows
    return result


def visual_cast_options(browser, data, refs, options, windows):
    """Show actual library portraits so duplicate-looking catalog IDs cannot play different roles."""
    from pathlib import Path
    import hashlib
    from PIL import Image, ImageOps, ImageDraw
    if not data.get('folder'):
        return None
    actors=scene_pick.appearances(data,{sid for ids in options.values() for sid in ids})
    catalog=data.get('characters',{})
    entities={catalog[c]['entity_id']:c for c in actors if catalog.get(c,{}).get('entity_id')}
    portraits={}
    root=Path(data['folder']).parent
    # Prefer a character's own identity exemplar; fall back to one of the shortlisted windows.
    original=[entity.rsplit(':',1)[-1].rsplit('_',1)[0] for entity in entities]
    shortlisted=[w['window'] for candidates in windows.values() for w in candidates]
    for wid in dict.fromkeys(original+shortlisted):
        path=root/'observations'/(hashlib.sha256(wid.encode()).hexdigest()+'.json')
        if not path.is_file():continue
        obs=json.loads(path.read_text())
        for item in obs['window'].get('entity_exemplars',[]):
            code=entities.get(item.get('entity_id'))
            if not code or code in portraits or not item.get('path'):continue
            photo=Path(item['path'])
            if photo.is_file() and hashlib.sha256(photo.read_bytes()).hexdigest()==item.get('sha256'):
                portraits[code]=photo
    if len(portraits)<2:return None
    codes=sorted(portraits)
    cols=min(7,len(codes));width,height=144,138
    board=Image.new('RGB',(cols*width,((len(codes)+cols-1)//cols)*height),'white')
    draw=ImageDraw.Draw(board)
    for i,code in enumerate(codes):
        with Image.open(portraits[code]) as image:
            tile=ImageOps.contain(image.convert('RGB'),(width-4,height-22))
        x=i%cols*width;y=i//cols*height
        board.paste(tile,(x+(width-tile.width)//2,y));draw.text((x+5,y+height-19),code,fill='black')
    photo=browser.root/'scene_cast_portraits.jpg';board.save(photo,quality=92)
    roster={role for ref in refs for role in ref['roles']}
    memberships={sid:sorted({code for w in rows for code in w['actors']}) for sid,rows in windows.items()}
    # Enumerate only assignments that have timed visual evidence in every reference unit.
    # Join progressively through each scene to avoid building a Cartesian product of the cast.
    from itertools import permutations
    partial=[({},0)]
    for ref in refs:
        from math import perm
        if sum(perm(len(memberships[sid]),len(ref['roles'])) for sid in options[ref['scene_id']])>25000:
            partial=[]
            break
        counts={}
        for sid in options[ref['scene_id']]:
            ws=windows[sid]
            for assignment in permutations(memberships[sid],len(ref['roles'])):
                trial=dict(zip(ref['roles'],assignment))
                if supports_performance(ws,ref,trial):counts[assignment]=counts.get(assignment,0)+1
        next_plans={}
        for proposal,score in partial:
            for assignment,n in counts.items():
                trial=dict(zip(ref['roles'],assignment))
                if any(r in proposal and proposal[r]!=a for r,a in trial.items()):continue
                combined={**proposal,**trial}
                if len(set(combined.values()))!=len(combined):continue
                key=tuple(sorted(combined.items()))
                next_plans[key]=max(next_plans.get(key,0),score+n)
        partial=[(dict(k),score) for k,score in sorted(next_plans.items(),key=lambda kv:(-kv[1],kv[0]))[:150]]
        if not partial:break
    # Include alternatives with different common performers, not just the highest-frequency actor.
    preferred=[cast for cast,_ in partial]
    def valid(v):
        aliases=v.get('lookalikes');plans=v.get('casts')
        if (not isinstance(aliases,list) or any(not isinstance(g,list) or len(g)<2
                or any(not isinstance(c,str) or c not in codes for c in g) for g in aliases)):
            raise ValueError('lookalikes填外观无法区分的编号组，每组至少两个图上编号')
        if isinstance(plans,dict):
            plans=v['casts']=[plans]
        if preferred and isinstance(plans,list):
            plans=[preferred[entry-1] if type(entry) is int and 1<=entry<=len(preferred) else entry for entry in plans]
        if not isinstance(plans,list) or not plans:raise ValueError('casts须为分配数组，每组填写故事角色到照片编号。可选：'+str(codes))
        valid_plans=[]
        errors=[]
        for plan in plans:
            if isinstance(plan,dict) and len(plan)==1 and isinstance(next(iter(plan.values())),dict):
                plan=next(iter(plan.values()))
            if not isinstance(plan,dict) or set(plan)!=roster:
                errors.append('方案要给全每个参考角色');continue
            ids=[v for v in plan.values() if v is not None]
            if any(not isinstance(i,str) or i not in actors for i in ids) or len(ids)!=len(set(ids)):
                errors.append('角色只填图上的演员编号或null，当前为 '+str(ids));continue
            if any(len(set(g)&set(ids))>1 for g in aliases):
                errors.append('同一方案的演员外观无法区分 '+str(ids));continue
            missing=[ref['scene_id'] for ref in refs if not any(
                supports_performance(windows[sid],ref,plan) for sid in options[ref['scene_id']])]
            if missing:
                errors.append('候选登记演员不足 '+str(missing)+' '+str(ids));continue
            if plan not in valid_plans:valid_plans.append(plan)
        if not valid_plans:raise ValueError('；'.join(errors)+'。可用演员编号 '+str(codes))
        v['casts']=valid_plans
        return v
    support=[dict(roles=r['roles'],available_sets=[list(group) for group in sorted({
                tuple(memberships[sid]) for sid in options[r['scene_id']]})]) for r in refs]
    prompt=('只返回JSON。你在给改编片选择演员，返回照片左下角的演员编号，禁止写场景号或素材号。'
        '参考人物名只代表故事身份，外观完全可以换，优先让观众一眼区分各演员。'
        '这是当前候选库演员的原始登记照片，编号代表记录。同一个外形可能登记多次。先按实际外观把普通观众难以区分的编号记入lookalikes，'
        '特别比较头部轮廓、耳形、同一图案或衣物，姿势、光线和不同叫法不足以分成两个人。'
        '再按参考各段谁和谁说话、谁替换谁，优先选外观差别一眼可见的演员，跨场同一人固定，用同一编号。'
        '每种方案每段至少有一场候选涵盖该段的已登记演员。给一至三组可行方案，优先外观最清楚的一组，'
        '实在无法从登记确认的角色填null，后面按真实选段看片。返回 {"lookalikes":[["编号甲","编号乙"]],"casts":[{"参考角色":"候选编号或null"}]}。\n'
        +json.dumps(dict(allowed_actor_codes=codes,appearance=actors,available_actor_groups_per_unit=support),ensure_ascii=False))
    valid.revalidated_protocol_repair=True
    if preferred:
        prompt=('只返回JSON。看所有登记人物的实际照片，找出普通观众分不清的相似编号lookalikes。'
                '从给定候选分配中选1至3个方案序号填casts。每个方案已由程序验过有实际时间段内的人物交流记录。'
                '不同参考角色选视觉差别清晰的演员，跨场同一角色固定。候选人物是改编演员，参考名字和外观无需模仿。'
                '有的登记图其实是同一人的正面、侧面、远近变化，或被错误描述为戴不同头饰；把这些放在同一相似组。'
                '返回 {"lookalikes":[["编号","编号"]],"casts":[方案序号]}。只填编号和序号。\n'
                +json.dumps(dict(choices={i:c for i,c in enumerate(preferred,1)},appearances=actors),ensure_ascii=False))
    result=valid(browser.d.call('material_scene_sequence_cast_looks',prompt,valid,str(photo),image=True))
    write_json(browser.root/'scene_cast_looks.json',result)
    return result


def proposed_ranges(row, windows):
    available = {w['window']: w for w in windows}
    takes = row.get('takes')
    if not isinstance(takes, list) or not takes:
        raise ValueError(row['source']+' 须给出可用的实际窗口和秒数 takes')
    result = {}
    for take in takes:
        if not isinstance(take,dict) or not isinstance(take.get('window'),str):
            raise ValueError(row['source']+' 的每条take需给window、start_s、end_s')
        w = available.get(take.get('window'))
        a, b = take.get('start_s'), take.get('end_s')
        if not w or type(a) not in (int,float) or type(b) not in (int,float) or not (
                w['usable'][0]-.0005 <= a < b <= w['usable'][1]+.0005 and b-a >= .3):
            raise ValueError(row['source']+' 的takes须在对应窗口本地usable区间内，至少0.3秒：'+str(take))
        result.setdefault(w['window'], []).append((w['origin']+max(a,w['usable'][0]),w['origin']+min(b,w['usable'][1])))
    return result


def performance_units(refs):
    """A cutaway may borrow a different source than the dialogue it interrupts."""
    units=[]
    for ref in refs:
        beats=ref.get('picture_beats',[])
        if not (any(b.get('kind')=='exchange' for b in beats) and any(b.get('kind')=='insert' for b in beats)):
            units.append(ref)
            continue
        groups=[]
        for beat in beats:
            roles=[r for r in ref['roles'] if r in beat['roles']]
            if groups and groups[-1][0]==beat['kind'] and set(groups[-1][1])==set(roles):
                groups[-1][2].append(beat)
            else:
                groups.append((beat['kind'],roles,[beat]))
        covered={role for _,roles,_ in groups for role in roles}
        # Keep unpictured narrators in the reference; they do not become an invented visual partner.
        for i,(kind,roles,items) in enumerate(groups,1):
            turns=[t for t in ref.get('turns',[]) if kind=='exchange' and t['role'] in roles
                   and set(t.get('addressee_roles',[]))<=set(roles)]
            units.append(dict(ref,scene_id=f"{ref['scene_id']}.p{i}",parent=ref['scene_id'],kind=kind,
                roles=roles,people=len(roles),turns=turns,has_dialogue=bool(turns),picture_beats=items,
                visual_function='；'.join(b['action'] for b in items),required='',
                voice_only=[r for r in ref['roles'] if r not in covered]))
    return units


def group_units(plan, refs):
    """Restore original scene IDs for casting and preserve each checked beat and its ranges."""
    rows=plan['scene_sequence']
    merged=[]
    for row,ref in zip(rows,refs):
        parent=ref.get('parent')
        if not parent:
            merged.append(row)
            continue
        if not merged or merged[-1]['reference']!=parent:
            merged.append(dict(reference=parent,source=[],entry=row['entry'],exit=row['exit'],
                transition=row['transition'],window_ids=[],usable_segments=[],picture_units=[]))
        group=merged[-1]
        group['source'].append(row['source'])
        group['exit']=row['exit']
        group['window_ids']=list(dict.fromkeys(group['window_ids']+row['window_ids']))
        group['usable_segments'].extend(row['usable_segments'])
        group['picture_units'].append(dict(row,kind=ref['kind'],roles=ref['roles']))
    plan['scene_sequence']=merged


class PictureMismatch(ValueError):
    def __init__(self, problems, report, clips, video):
        super().__init__('实际留用画面不符合参考：'+'；'.join(problems))
        self.report,self.clips,self.video=report,clips,video


def reserve_insert_segments(rows, fixes, clips):
    """Reserve GLM-selected cutaway frames, removing them from their original scene."""
    from copy import deepcopy
    result=deepcopy(rows);reserved={};replacements={}
    for fix in fixes:
        sid=fix['reference'];spans=[];sources=[]
        for take in fix['takes']:
            label=take['clip'];clip=clips[label]
            owner=next(r for r in rows if r['reference']==clip['reference'])
            segment=next(s for s in owner['usable_segments'] if s['window_id']==clip['window_id']
                         and abs(s['source_start_s']-clip['source_start_s'])<.001)
            a,b=(clip['source_start_s']+take[k] for k in ('start_s','end_s'))
            occupied=reserved.setdefault(segment['source_sha256'],[])
            if any(min(b,y)>max(a,x)+.0001 for x,y in occupied):raise ValueError('插镜所选秒数不能互相重放')
            occupied.append((a,b))
            spans.append(dict(segment,source_start_s=a,source_end_s=b))
            if owner['source'] not in sources:sources.append(owner['source'])
        replacements[sid]=(fix,spans,sources)
    for row in result:
        sid=row['reference']
        row.pop('performance_review',None)
        if sid in replacements:
            fix,spans,sources=replacements[sid]
            row.update(source=sources[0] if len(sources)==1 else sources,
                       **{k:fix[k] for k in ('entry','exit','transition')},
                       insert_from_verified_clips=fix['takes'])
            row.pop('takes',None)
        else:
            spans=[]
            for old in row['usable_segments']:
                pieces=[(old['source_start_s'],old['source_end_s'])]
                for a,b in reserved.get(old['source_sha256'],[]):
                    pieces=[(x,y) for lo,hi in pieces for x,y in ((lo,min(hi,a)),(max(lo,b),hi)) if y-x>=.3]
                spans.extend(dict(old,source_start_s=x,source_end_s=y) for x,y in pieces)
        if not spans:raise ValueError('须保留主场表演的画面：'+sid)
        row.update(usable_segments=spans,window_ids=list(dict.fromkeys(s['window_id'] for s in spans)),
                   scene_group_id='cg_'+json_sha(spans)[:16])
    return result


def repair_inserts(browser, rows, refs, index, failure, hints, appearances, pool, windows, options):
    reports={s['reference']:s for s in failure.report['scenes']}
    bad={r['scene_id'] for r in refs if check_performances(
        {'scenes':[reports[r['scene_id']]]},[r],failure.clips,hints)}
    if not bad or any(r.get('kind')!='insert' for r in refs if r['scene_id'] in bad):raise failure
    good={r['scene_id']:r for r in refs if r['scene_id'] not in bad}
    menu={}
    for ref in refs:
        sid=ref['scene_id']
        if sid not in bad:continue
        clip_labels=[label for label,clip in failure.clips.items() if clip['reference'] in good and
                 set(good[clip['reference']]['roles']).intersection(ref['roles'])]
        if not clip_labels:raise failure
        menu[sid]=clip_labels
    def valid(v):
        if v.get('action')=='search':return v
        fixes=v.get('inserts')
        if v.get('action')!='reserve' or not isinstance(fixes,list) or [f.get('reference') for f in fixes]!=[
                r['scene_id'] for r in refs if r['scene_id'] in bad]:
            raise ValueError('只给待修插镜各一条，按参考顺序填inserts；无合适帧填search')
        for f in fixes:
            if not f.get('takes') or not all(isinstance(f.get(k),str) and f[k].strip() for k in ('entry','exit','transition')):
                raise ValueError('插镜需要takes、entry、exit、transition')
            for t in f['takes']:
                label=t.get('clip');a,b=t.get('start_s'),t.get('end_s')
                if label not in menu[f['reference']] or type(a) not in (int,float) or type(b) not in (int,float) or not (
                        0<=a<b<=failure.clips[label]['duration']+.00001 and b-a>=.3):
                    raise ValueError('选本段允许的标签及该标签时钟内的秒数，至少0.3秒：'+str(t))
        reserve_insert_segments(rows,fixes,failure.clips)
        return v
    brief=[dict(reference=r['scene_id'],roles=r['roles'],beats=r.get('picture_beats',[])) for r in refs if r['scene_id'] in bad]
    prompt=('参考原片REF和所选素材S交替。主要对白的人物已核实，只有以下插镜未通过。'
            '直接看S标签的实际画面，从已通过的表演中为回忆或反应另留少量画面。'
            '可先后取不同人的反应，没有必要同框。所选秒数将从原来的主场中扣除，给主场留足可辨认的说话和听者表演。'
            '若这些帧无法表达参考插镜，返回 {"action":"search"}。有合适的返回 '
            '{"action":"reserve","inserts":[{"reference":"待修ID","entry":"实际画面","exit":"实际画面","transition":"如何接",'
            '"takes":[{"clip":"允许的S标签","start_s":标签内开始秒,"end_s":标签内结束秒}]}]}。'
            '请独立挑选。'+json.dumps(dict(required=brief,available=menu,clocks=failure.clips,cast=hints),ensure_ascii=False))
    answer=browser.d.call('material_scene_sequence_insert_pick_'+str(index),prompt,valid,str(failure.video))
    if answer['action']=='reserve':
        corrected=reserve_insert_segments(rows,answer['inserts'],failure.clips)
    else:
        from . import insert_search
        corrected=insert_search.replace(browser,rows,refs,bad,hints,pool,windows,options,index,failure)
    plan=compose(corrected);plan['cast_hints']=hints
    plan['transition_review']=review_sequence(browser,corrected,refs,str(index)+'_insert',hints,appearances)
    return plan


def select(browser, data, feedback):
    route=browser.d.route
    originals=scene_pick.references(route)
    refs=performance_units(originals)
    pool=data['scenes']
    if not getattr(browser,'sequence_options',None):
        browser.catalog=browser.catalog_rows()
        browser.by_id={w['window_id']:w for w in browser.catalog}
        browser.d.seen.update(browser.by_id)
        eligible=lambda sid:any(w in browser.by_id for w in pool[sid]['window_ids'])
        candidates=[sid for sid in sorted(pool) if eligible(sid)]
        if not candidates:raise LibraryStopped('no_watchable_scenes_for_sequence')
        pages=scene_pick.pages(data,candidates)
        # Read each library page once for the entire reference, one shortlist per scene.
        parallel=browser.isolated_page_call()
        def one(item):
            index,ids=item
            prompt=('按参考顺序为每一段分别挑选候选。对白段看人数、面对面位置和地点功能。'
                    '无对白段看角色、动作开始和结果。注意前后段共同的人物。每段最多四个本页编号。'
                    '返回 {"options":{"参考场景ID":["素材场景ID"]}}。参考：'+json.dumps(refs,ensure_ascii=False)
                    +'\n'+scene_pick.catalog_text(data,ids))
            def valid(v):
                options=v['options']
                if set(options)!={r['scene_id'] for r in refs}:raise ValueError('one list per reference scene')
                for row in options.values():
                    if not isinstance(row,list) or not 1<=len(row)<=min(4,len(ids)) or len(set(row))!=len(row) or set(row)-set(ids):
                        raise ValueError('choose distinct IDs from this page')
                return v
            stage='material_scene_sequence_page_'+str(index+1)
            value=parallel(index,stage,prompt,valid) if parallel else browser.d.call(stage,prompt,valid)
            return value['options']
        if parallel:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=3) as workers: opts=list(workers.map(one,enumerate(pages)))
        else:opts=list(map(one,enumerate(pages)))
        browser.sequence_options={r['scene_id']:[s for page in opts for s in page[r['scene_id']]] for r in refs}
        browser.sequence_attempts=[]
        browser.sequence_parts={}
    options=browser.sequence_options
    if not feedback and getattr(browser,'scene_pick_result',None):return browser.scene_pick_result
    shortlisted=set(s for group in options.values() for s in group)
    windows=window_options(browser,data,shortlisted)
    roles={role for ref in refs for role in ref['roles']}
    prompt=('从各自候选中，按参考顺序选能演出每段交流或插镜的窗口。不同段可以用同场不同时间，各段不能重复使用相同画面。给每个故事角色分配不同演员，'
            '名单是窗口级线索，后面会用实际画面复核。写每段开头和结尾可见的状态、为何能从上一段接过来，'
            '地点变化要让观众看懂，不能把上一段已完成的动作重做。若先前组合失败，改选有效组合。'
            'cast_hints填所有故事角色。按外观与窗口的文字选具体takes，优先实际有说者和听者的几秒，可取多个窗口提供反应和说话镜头。回忆可分镜出人，无须多人同框。不同角色不能因为跨场就都留下。编号未知填null，交给画面核验。'
            '返回 {"cast_hints":{"故事角色":"C编号或null"},"scenes":[{"reference":"参考场景ID","source":"素材场景ID","takes":[{"window":"窗口ID","start_s":窗口内秒,"end_s":窗口内秒}],"entry":"开头可见状态",'
            '"exit":"结尾可见状态","transition":"和上段的衔接，首段写建立关系"}]}。\n'+
            json.dumps(dict(reference=refs,options=options,windows={sid:[{k:v for k,v in w.items() if k!='moments'} for w in ws] for sid,ws in windows.items()},appearances=scene_pick.appearances(data,shortlisted),previous=browser.sequence_attempts,feedback=feedback),ensure_ascii=False))
    def with_cast_choices(prompt,looks):
        head,body=prompt.split('\n',1)
        payload,end=json.JSONDecoder().raw_decode(body)
        choices={}
        allowed_roles={}

        for ref in refs:
            choices[ref['scene_id']]=[]
            for sid in options[ref['scene_id']]:
                possible=[cast for cast in looks['casts'] if supports_performance(windows[sid],ref,cast)]
                if not possible:continue
                choices[ref['scene_id']].append(sid)
                allowed_roles.setdefault(sid,set()).update(c[r] for c in possible for r in ref['roles'] if c[r])
        used={sid for ids in choices.values() for sid in ids}
        detailed={}
        for sid in sorted(used):
            detailed[sid]=[dict(w,moments=[m for m in w.get('moments',[]) if allowed_roles[sid].intersection(m['actors'])])
                           for w in windows[sid]]
        payload.update(options=choices,windows=detailed,visual_cast_options=looks)
        return head+'\n'+json.dumps(payload,ensure_ascii=False)+body[end:]
    if getattr(browser,'sequence_cast_looks',None):
        prompt=with_cast_choices(prompt,browser.sequence_cast_looks)
    def valid(v):
        rows=v['scenes']
        if [r['reference'] for r in rows]!=[r['scene_id'] for r in refs]:raise ValueError('preserve every reference scene in order')
        identity=[(r['source'],r.get('takes')) for r in rows]
        if identity in browser.sequence_attempts:raise ValueError('select a different source interval for the failed sequence')
        for r in rows:
            if r['source'] not in options[r['reference']] or not all(str(r.get(k,'')).strip() for k in ('entry','exit','transition')):
                raise ValueError('source and visible transition required for '+r['reference'])
            proposed_ranges(r,windows[r['source']])
        hints=v.get('cast_hints',{})
        if not isinstance(hints,dict) or set(hints)!=roles:raise ValueError('map every reference role '+str(sorted(roles)))
        assigned=[a for a in hints.values() if a is not None]
        if len(assigned)!=len(set(assigned)):raise ValueError('different recurring roles cannot share one actor')
        looks=getattr(browser,'sequence_cast_looks',None)
        if looks and (any(len(set(g)&set(assigned))>1 for g in looks['lookalikes']) or hints not in looks['casts']):
            raise ValueError('图库实拍外观比较后，请沿用可区分且有候选的cast方案：'+str(looks['casts']))
        for role,actor in hints.items():
            if actor is not None and any(actor not in pool[r['source']].get('characters',[])
                                       for ref,r in zip(refs,rows) if role in ref['roles']):
                raise ValueError('registered actor not present in every scene of '+role)
        if looks:
            for ref,row in zip(refs,rows):
                selected=proposed_ranges(row,windows[row['source']])
                scoped=[]
                for w in windows[row['source']]:
                    ranges=selected.get(w['window'])
                    if not ranges:continue
                    scoped.append(dict(w,moments=[m for m in w['moments'] if any(
                        min(b,w['origin']+m['at'][1])-max(a,w['origin']+m['at'][0])>=.3 for a,b in ranges)]))
                if not any(w['moments'] for w in scoped) and any(w['moments'] for w in windows[row['source']]):
                    raise ValueError(ref['scene_id']+' 的所选秒数没有保留已登记的人物动作，按moments选。')
                if not supports_performance(scoped,ref,hints):
                    raise ValueError(ref['scene_id']+' 的所选秒数缺少所需人物/实际双方互动。按moments的actor、recipient及时间选。')
        return v
    for _ in range(3):
        idx=len(browser.sequence_attempts)+1
        choice=browser.d.call('material_scene_sequence_rank_'+str(idx),prompt,valid)
        rows=choice['scenes'];browser.sequence_attempts.append([(r['source'],r['takes']) for r in rows])
        try:
            for r in rows:
                sid=r['source']
                takes=proposed_ranges(r,windows[sid])
                key=json_sha([sid,takes])
                if key not in browser.sequence_parts:
                    browser.sequence_parts[key]=ground(browser,dict(pool[sid],window_ids=list(takes),takes=takes))
                r.update(browser.sequence_parts[key])
            plan=compose(rows)
            plan['cast_hints']=choice.get('cast_hints',{})
            looks_by_code=scene_pick.appearances(data,shortlisted)
            try:
                plan['transition_review']=review_sequence(browser,rows,refs,idx,choice['cast_hints'],looks_by_code)
            except PictureMismatch as mismatch:
                plan=repair_inserts(browser,rows,refs,idx,mismatch,choice['cast_hints'],looks_by_code,pool,windows,options)
        except ValueError as exc:
            if not getattr(browser,'sequence_cast_looks',None) and '实际留用画面' in str(exc):
                browser.sequence_cast_looks=visual_cast_options(browser,data,refs,options,windows)
            looks=getattr(browser,'sequence_cast_looks',None)
            if looks:
                prompt=with_cast_choices(prompt,looks)+'\n选其中一个外观可辨方案，在其覆盖的窗口中挑选有互动的秒数。'
            prompt+='\n组合无法落到已核验的不同源片段：'+str(exc)+'。已尝试'+str(browser.sequence_attempts)
            continue
        group_units(plan,refs)
        result=dict(action='finish',candidates=[plan],selection_reason='逐参考场选材后按顺序联合选角和复核')
        write_json(browser.root/'scene_pick.json',dict(options=options,attempts=browser.sequence_attempts,plan=plan))
        write_json(browser.root/'candidates.json',result)
        browser.scene_pick_result=result
        return result
    raise LibraryStopped('no_grounded_multiscene_sequence:'+str(browser.sequence_attempts))


def ground(browser,scene):
    ids=[w for w in scene['window_ids'] if w in browser.by_id]
    for wid in ids:
        if wid not in browser.visual:browser.watch(wid)
    audit=browser.audit_continuity(ids)
    options=[]
    for g in (audit or {}).get('result',{}).get('groups',[]):
        gid='cg_'+json_sha(dict(media_sha256=audit['media_sha256'],group=g))[:16]
        part=browser.grounded_candidate(dict(scene_group_id=gid,limitations='',window_ids=list(browser.usable_group_windows(g))))
        spans=[]
        for seg in part['usable_segments']:
            a=max(seg['source_start_s'],scene['source_start_s']);b=min(seg['source_end_s'],scene['source_end_s'])
            for lower,upper in scene['takes'].get(seg['window_id'],[]):
                start,end=max(a,lower),min(b,upper)
                if end-start>=.3:spans.append(dict(seg,source_sha256=browser.by_id[seg['window_id']]['source_sha256'],source_start_s=start,source_end_s=end))
        if spans:options.append((sum(s['source_end_s']-s['source_start_s'] for s in spans),gid,spans))
    if not options:raise ValueError('no independently observed footage for '+scene['scene_id'])
    _,gid,spans=max(options,key=lambda o:o[0])
    return dict(scene_group_id=gid,usable_segments=spans,window_ids=list(dict.fromkeys(s['window_id'] for s in spans)))


def compose(rows):
    used=[];segments=[]
    for r in rows:
        for s in r['usable_segments']:
            if any(s['source_sha256']==t['source_sha256'] and min(s['source_end_s'],t['source_end_s'])>
                   max(s['source_start_s'],t['source_start_s'])+.001 for t in used):
                raise ValueError('source time reused across scenes: '+r['reference'])
        used.extend(r['usable_segments']);segments.extend(r['usable_segments'])
    return dict(scene_group_id='seq_'+json_sha(rows)[:16],scene_sequence=rows,usable_segments=segments,
                window_ids=list(dict.fromkeys(s['window_id'] for s in segments)),limitations='各段只能使用本段核验的源区间')


def check_performances(report, refs, clips, hints):
    """Reject mismatched partners even if the model calls both transitions smooth."""
    by_scene = report['scenes']
    if [s.get('reference') for s in by_scene] != [r['scene_id'] for r in refs]:
        raise ValueError('review every actual scene in reference order')
    identities, owners, problems = {}, {}, []
    for scene, ref in zip(by_scene, refs):
        sid = ref['scene_id']
        if scene.get('ok') is not True:
            problems.append(sid+' '+str(scene.get('reason','实际画面不足')))
            continue
        def located(row):
            clip = clips.get(row.get('clip'))
            t = row.get('at_s')
            return (clip and clip['reference']==sid and type(t) in (int,float)
                    and 0 <= t < clip['duration'] and bool(str(row.get('evidence') or '').strip()))
        cast = {}
        for person in scene.get('people', []):
            role, actor = person.get('role'), person.get('actor')
            if role not in ref['roles'] or not isinstance(actor,str) or not actor.strip() or not located(person):
                problems.append(sid+' 人物证据未落在本场保留画面 '+str(role))
                continue
            if cast.get(role,actor)!=actor or ((hints.get(role) or identities.get(role,actor))!=actor
                                or owners.get(actor,role)!=role):
                problems.append(sid+' 演员与跨场绑定冲突 '+role+' '+actor)
            cast[role]=actor
            identities[role]=actor
            owners[actor]=role
        missing=set(ref['roles'])-set(cast)
        if missing:problems.append(sid+' 缺人物画面 '+str(sorted(missing)))
        expected={(t['role'],other) for t in ref.get('turns',[]) for other in t.get('addressee_roles',[])
                  if t.get('on_screen') is not False and other in ref['roles'] and other!=t['role']}
        seen={(e.get('speaker'),e.get('listener')) for e in scene.get('exchanges',[]) if located(e)}
        absent={(a,b) for a,b in expected if (cast.get(a),cast.get(b)) not in seen}
        if absent:problems.append(sid+' 缺少说者到听者的实际交流 '+str(sorted(absent)))
    return problems


def review_sequence(browser, rows, refs, index, hints, appearances):
    """Show every retained interval and bind observations to the on-screen clip clock."""
    import subprocess
    folder=browser.root/('sequence_review_'+str(index));folder.mkdir(exist_ok=True)
    files, clips = [], {}
    def encode(source, start, duration, label):
        clip=folder/(label+'.mp4')
        overlay="drawtext=text='"+label+" %{pts\\:hms}':box=1:boxcolor=black:fontcolor=white:fontsize=14:x=2:y=2"
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(start),'-i',str(source),
            '-t',str(duration),'-an','-vf','fps=10,scale=512:288,setsar=1,setpts=PTS-STARTPTS,'+overlay,
            '-c:v','libx264','-crf','27','-threads','1','-pix_fmt','yuv420p',str(clip)],check=True)
        files.append(clip)
    reference=getattr(getattr(browser.d,'route',None),'reference_media',None)
    last_ref=None
    for i,(row,ref) in enumerate(zip(rows,refs),1):
        parent=ref.get('parent',ref['scene_id'])
        if reference and last_ref!=parent and ref.get('start_s') is not None and ref.get('end_s') is not None:
            encode(reference,ref['start_s'],ref['end_s']-ref['start_s'],f'REF{i}')
        last_ref=parent
        spans=row['usable_segments'] if row.get('insert_from_verified_clips') else sorted(row['usable_segments'],key=lambda x:x['source_start_s'])
        for j,s in enumerate(spans,1):
            proxy=browser.proxies[s['window_id']]['proxy']
            a,b=s['source_start_s'],s['source_end_s']
            label=f'S{i}W{j}'
            encode(proxy['path'],a-proxy['source_start_s'],b-a,label)
            clips[label]=dict(reference=row['reference'],duration=round(b-a,6),window_id=s['window_id'],source_start_s=a,source_end_s=b)
    listing=folder/'parts.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in files))
    output=folder/'sequence.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','1','-i',str(listing),'-c','copy',str(output)],check=True)
    def valid(v):
        if not isinstance(v.get('scenes'),list) or not isinstance(v.get('joins'),list):
            raise ValueError('return scenes and joins')
        for scene in v['scenes']:
            if not isinstance(scene,dict) or type(scene.get('ok')) is not bool:
                raise ValueError('each scene needs a boolean ok')
            if scene['ok'] and any(not isinstance(scene.get(k),list) or
                    not all(isinstance(item,dict) for item in scene[k]) for k in ('people','exchanges')):
                raise ValueError('通过的场需提供people与exchanges数组，数组元素为证据对象')
        if [c['to'] for c in v['joins']]!=[r['scene_id'] for r in refs[1:]] or any(
                type(c.get('ok')) is not bool or not c.get('evidence') for c in v['joins']):
            raise ValueError('review every join in order with actual evidence')
        return v
    contracts=[]
    previous=set()
    previous_dialogue=set()
    for ref in refs:
        roles=set(ref['roles'])
        contracts.append(dict(reference=ref['scene_id'],roles=ref['roles'],turns=ref.get('turns',[]),picture_beats=ref.get('picture_beats',[]),
                              keep=sorted(previous & roles),enter=sorted(roles-previous),leave=sorted(previous-roles)))
        if ref.get('has_dialogue'):
            contracts[-1]['conversation_change']=dict(keep=sorted(previous_dialogue & roles),
                enter=sorted(roles-previous_dialogue),leave=sorted(previous_dialogue-roles))
            previous_dialogue=roles
        previous=roles
    prompt=('视频是改编所选的动画素材与参考对照。REF为该段参考原片，后面的S标签才是本段实际保留的画面。按原片实际信息流向检查谁交流，不用台词文字替画面作证。左上角时钟从零开始。没有参考片的真人、服饰、地点或道具不能作为否决理由。'
        '按实际画面辨认演员。已知编号用编号，未知演员自取全片一致的ID。不同角色须不同演员，同一角色再次登场须同一演员。'
        '检查本段谁向谁说、谁听、相邻段谁留下、谁换进来。两个人刚好都出现不能说明有交流；可以用正反打、朝向、指示和反应建立关系。'
        '画外音不要求说者动嘴，回忆按picture_beats核对先后，不要求所有人同框。角色对不上、实际无法辨认或缺少关键互动就写ok=false，并指出哪段缺什么。'
        'people每个需要的角色一条，exchanges写能辨认的说者actor与听者actor的方向，每种实际方向一条。at_s是所报clip左上显示的本段秒数。'
        '返回 {"scenes":[{"reference":"参考ID","ok":true,"reason":"可见依据或缺口","people":[{"role":"参考角色","actor":"演员ID","clip":"标签","at_s":1,"evidence":"外观及动作"}],'
        '"exchanges":[{"speaker":"演员ID","listener":"演员ID","clip":"标签","at_s":1,"evidence":"可见交流"}]}],"joins":[{"to":"下一段参考ID","ok":true,"evidence":"两段具体交接"}]}。\n'
        +json.dumps(dict(reference=contracts,cast=hints,appearance={c:appearances.get(c,'') for c in hints.values() if c},clips=clips),ensure_ascii=False))
    report=browser.d.call('material_scene_sequence_join_'+str(index),prompt,valid,str(output))
    problems=check_performances(report,refs,clips,hints)
    problems.extend(str(c['evidence']) for c in report['joins'] if not c['ok'])
    write_json(folder/'review.json',dict(report,clips=clips,problems=problems))
    if problems:raise PictureMismatch(problems,report,clips,output)
    for scene,row in zip(report['scenes'],rows):
        row['performance_review']=dict(scene, clips={k:v for k,v in clips.items() if v['reference']==row['reference']})
    return report['joins']
