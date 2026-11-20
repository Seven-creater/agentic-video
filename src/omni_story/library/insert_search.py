"""Find disjoint reaction shots for an insert without discarding verified dialogue."""
import io
import json
import subprocess


def candidates(windows, ids, cast, used, hashes, per_actor=8):
    """Give each required actor a visually varied menu of their observed story moments."""
    by_actor = {actor: [] for actor in cast if actor}
    for source in dict.fromkeys(ids):
        for window in windows[source]:
            wid = window['window']
            for moment in window['moments']:
                visible = set(moment['actors'])
                roles = visible.intersection(by_actor)
                if not roles:
                    continue
                spans = [tuple(moment['at'])]
                origin = window['origin']
                for old in used:
                    if old['source_sha256'] != hashes[wid]:
                        continue
                    a, b = old['source_start_s'] - origin, old['source_end_s'] - origin
                    spans = [(x,y) for lo,hi in spans for x,y in ((lo,min(hi,a)),(max(lo,b),hi)) if y-x >= .5]
                for lo,hi in spans:
                    if hi-lo < .5:
                        continue
                    shot = dict(source=source, window=wid, start_s=lo, end_s=hi, actors=sorted(visible),
                                action=moment['action'])
                    for actor in roles:
                        priority = (len(visible-set(by_actor)), len(visible), moment['actor'] != actor, -(hi-lo))
                        by_actor[actor].append((priority,shot))
    menu = []
    for shots in by_actor.values():
        shots.sort(key=lambda item: item[0])
        seen, rest = set(), []
        chosen = []
        for _,shot in shots:
            if shot['source'] in seen:
                rest.append(shot)
            else:
                chosen.append(shot)
                seen.add(shot['source'])
        for shot in (chosen+rest)[:per_actor]:
            if shot not in menu:
                menu.append(shot)
    return {f'V{i+1}': shot for i,shot in enumerate(menu)}


def visual_menu(browser, shots, destination):
    from PIL import Image, ImageDraw, ImageOps
    # Three frames per option reveal entrances, turns and changes of partner.
    cell_w, cell_h, per_row = 144, 99, 2
    board = Image.new('RGB',(cell_w*3*per_row,cell_h*((len(shots)+per_row-1)//per_row)),'#202020')
    draw = ImageDraw.Draw(board)
    for i,(label,shot) in enumerate(shots.items()):
        wid = shot['window']
        window = browser.d.route.store.get(browser.d.records[wid][0]['observation_id'])['window']
        x = (i%per_row)*3*cell_w
        y = (i//per_row)*cell_h
        for j,fraction in enumerate((.18,.50,.82)):
            t = window['source_start_s'] + shot['start_s'] + (shot['end_s']-shot['start_s'])*fraction
            data = subprocess.check_output(['ffmpeg','-nostdin','-v','error','-threads','1','-ss',str(t),
                '-i',window['source_path'],'-frames:v','1','-vf',f'scale={cell_w}:-2','-f','image2pipe',
                '-vcodec','mjpeg','-threads','1','pipe:1'])
            with Image.open(io.BytesIO(data)) as frame:
                board.paste(ImageOps.contain(frame.convert('RGB'),(cell_w,80)), (x+j*cell_w,y))
        draw.text((x+4,y+81),label,fill='white')
    board.save(destination, quality=89)


def replace(browser, rows, refs, bad, hints, pool, windows, options, index, failure):
    from .multiscene_pick import ground, compose
    from .state import json_sha
    used = [seg for r in rows if r['reference'] not in bad for seg in r['usable_segments']]
    fixed = {}
    for unit_index,ref in enumerate(refs,1):
        sid = ref['scene_id']
        if sid not in bad:
            continue
        allowed = options[sid] + [r['source'] for r in rows if isinstance(r['source'],str)]
        hashes = {w['window']:browser.by_id[w['window']]['source_sha256'] for s in allowed for w in windows[s]}
        rejected = [seg for r in rows if r['reference'] == sid for seg in r['usable_segments']]
        shots = candidates(windows,allowed,[hints[r] for r in ref['roles']],used+rejected,hashes)
        if not shots:
            raise failure
        image = browser.root/f'insert_options_{index}_{sid.replace("/", "_")}.jpg'
        visual_menu(browser,shots,image)
        def validate(value):
            if value.get('action') == 'search':
                return value
            takes = value.get('takes')
            if not isinstance(takes,list) or not takes or any(not isinstance(t,dict) for t in takes):
                raise ValueError('takes须按剪辑顺序逐项填标签和秒数')
            intervals = []
            for take in takes:
                shot = shots.get(take.get('clip'))
                a, b = take.get('start_s'), take.get('end_s')
                if not shot or type(a) not in (float,int) or type(b) not in (float,int) or not (
                        shot['start_s'] <= a < b <= shot['end_s'] and b-a >= .3):
                    raise ValueError('选表中标签及对应起止秒数内的有效区间')
                origin = next(w['origin'] for w in windows[shot['source']] if w['window']==shot['window'])
                source_hash = hashes[shot['window']]
                lo, hi = origin+a, origin+b
                if any(h == source_hash and min(hi,y)-max(lo,x) > .0001 for h,x,y in intervals):
                    raise ValueError('插镜取段须使用互不重叠的实际源帧')
                intervals.append((source_hash,lo,hi))
            if any(not isinstance(value.get(k),str) or not value[k].strip() for k in ('entry','exit','transition')):
                raise ValueError('填写可见的开头、结尾和衔接')
            return value
        prompt = ('已核验的主要表演保留。只为这个插镜选新画面。每个标签给出同一素材秒段的早、中、晚三帧。'
                  '按实际图区分角色和伙伴，近景反应、分镜回忆、物品动作均按本段叙事功能取舍，改编道具无需与真人原片一样。'
                  '对照给定角色表，该离场的人不得抢先进入。可用不同标签先后拍不同角色，避免多余角色、片头和标题。'
                  '查表使用窗口本地秒数，挑最能看懂的部分。无合适组合返回 {"action":"search"}。'
                  '返回 {"takes":[{"clip":"标签","start_s":开始秒,"end_s":结束秒}],"entry":"可见开头","exit":"结尾","transition":"衔接"}。\n'
                  + json.dumps(dict(reference=ref,cast=hints,previous_review=failure.report['scenes'],options=shots),ensure_ascii=False))
        choice = browser.d.call(f'material_scene_sequence_insert_library_{index}_{unit_index}',prompt,validate,str(image),image=True)
        if choice.get('action') == 'search':
            raise failure
        spans, sources = [], []
        for take in choice['takes']:
            shot = shots[take['clip']]
            wid, source = shot['window'],shot['source']
            origin = next(w['origin'] for w in windows[source] if w['window']==wid)
            ranges = {wid:[(origin+take['start_s'],origin+take['end_s'])]}
            part = ground(browser,dict(pool[source],window_ids=[wid],takes=ranges))
            spans.extend(part['usable_segments'])
            sources.append(source)
        new = dict(reference=sid, source=list(dict.fromkeys(sources)), usable_segments=spans,
                   scene_group_id='cg_'+json_sha(spans)[:16],
                   window_ids=list(dict.fromkeys(s['window_id'] for s in spans)),insert_from_verified_clips=choice['takes'],
                   entry=choice['entry'],exit=choice['exit'],transition=choice['transition'])
        if len(new['source']) == 1:
            new['source'] = new['source'][0]
        fixed[sid] = new
        used.extend(spans)
    result = [fixed.get(r['reference'],r) for r in rows]
    compose(result)
    return result
