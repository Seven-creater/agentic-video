from pathlib import Path
from collections import defaultdict
import argparse,math
from itertools import groupby
from PIL import Image,ImageDraw
from omni_story.library.dialogue_mouth_edit import ask,encoded,read,render,render_shot,score_output,validate_cuts
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes
from omni_story.library.dialogue_mouth_tracks import cycles, matching_pose
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json

def run(b,previous,qa,rabbit_dir,task):
    base=read(b/'bundle.json'); case=dict(base)
    old=read(previous/'draft_render.json')
    choice=read(qa/'outcome.json')
    if choice['scores']['passed']!=2:
        raise ValueError("Both restored opening exchange lines must pass before assembly")
    prepared=read(rabbit_dir/'final_recheck.json')
    facts=read(task/'scan.json')
    facts.update(read(task/'extra'/'scan.json'))
    out=task/'full_locked'
    out.mkdir(exist_ok=True)
    motions=read(task/'original_motions.json')
    observations={k:v for k,v in motions.items()}
    clips={k:dict(v) for k,v in base['clips'].items()}
    crops={
      'M03':[.28,.10,.72,.54],
      'M06':[470/1280,168/720,1270/1280,618/720],
      'M09':[0,0,1,1],
    }
    visuals={
      # These rectangles isolate the actor, independent of which frames GLM chooses.
      'C001P03':('C001',[1],[.00,.205,.49,.695],'草地日景，白圆耳面向搭档'),
      'C001P01':('C001',[0],[.444,.322,.972,.850],'晴天草地，白圆耳在右'),
      'C001P09':('C001',[1,2],[.52,.38,.99,.85],'户外，白圆耳在右，可裁掉蓝头'),
      'C001T14':('C001',[0],[.30,.36,.68,.74],'绿色草地，白圆耳单人'),
      'C003P06':('C003',[0],[.43,.24,.81,.62],'秋色草地，裁近黄色长耳，旁边角色出画'),
      'C003P01':('C003',[0],[.38,.30,.76,.68],'户外叶伞下长耳兔单人，雨天色偏冷'),
      'C007P04':('C007',[0,1],[.63,.45,.99,.81],'草地、蓝头近景'),
      'C007P09':('C007',[0,1],[0,0,1,1],'绿树前蓝头单人'),
      'C007P13':('C007',[0,1],[.36,.26,.77,.67],'蓝头和纸本，草地'),
      'C007P03':('C007',[0],[0,0,1,1],'草地、蓝头单人，尽量不要带手中蓝杈'),
    }
    descriptions={k:t[3] for k,t in visuals.items()}
    descriptions.update(M03='原场蓝头与白圆耳背影',M06='原场蓝头手持一排卡片',M09='原场草地白圆耳回忆',M04='原场蓝头从地面拿牌',M05='原场举起单张牌和白圆耳表情',M11='原场白圆耳在桌对面')
    pockets={}
    def link(src,path):
        if not path.exists():path.symlink_to(src)
    for n in (base['dub'],base['subs']):
        link(b/n,out/n)
    for k,v in clips.items():
        link(b/v['path'],out/v['path'])
    for k,(a,indices,crop,_) in visuals.items():
        data=facts[k]
        clips[k]=dict(data,masks=[])
        origin=task/'extra' if 'T' in k else task
        link(origin/'choices'/data['path'],out/data['path'])
        seen=[]
        pockets[k]=[]
        crops[k]=crop
        for i in indices:
            t=data['tracks'][i]
            ps={int(n):v for n,v in t['points'].items()}
            seen.append(dict(actor=a,start=t['start'],end=t['end'],track_frames=list(ps),
                             center=[[v[0]/640,v[1]/360] for v in ps.values()],
                             widths=[v[2]/640 for v in ps.values()],evidence='核对过身份的嘴形开合。'))
            pockets[k].append((a,ps))
    # Parse tracks of previously verified frozen clips.
        observations[k]={"method":"contour_cycle","slots":seen}
    for k in ['M03','M06','M09']:
        ps=sampled_frames(b/clips[k]['path'],step=1)
        from omni_story.library.dialogue_mouth_tracks import candidates
        detected=candidates(ps,30)
        pockets[k]=[]
        for ref in observations[k]['slots']:
            for a,end,p in detected:
                if (a,end)==(ref['start'],ref['end']):
                    pockets[k].append((ref['actor'],p))
                    break
    for cut in choice['cuts']:
        key=cut['clip']
        if key=='R7':
            r=prepared['source_record']
            clips[key]=dict(r,sha256=prepared['selected_media_sha256'],masks=[prepared['mask']])
            link(rabbit_dir/r['path'],out/r['path'])
            observations[key]={"method":"contour_cycle","slots":read(rabbit_dir/'candidate_mouths.json')[key]}
        elif key not in clips:
            raise ValueError("White pair selected a source outside the audit")
    def area_mask(v,mask):
        return dict(x=mask[0],y=mask[1],w=mask[2]-mask[0],h=mask[3]-mask[1],block_px=15,opacity=.91,start_s=0,end_s=v['frames']/30)
    # Confirm actual title bands on archived source boards; keep them away from the mouth.
    for k,box in {
        'C001P03':(.20,.84,.72,.995),'C001P09':(.26,.84,.70,.995),'C001T14':(.28,.84,.68,.995),
        'C003P01':(.39,.86,.67,.995),'C003P06':(.23,.79,.70,.995),
        'C007P04':(.25,.86,.72,.995),'C007P09':(.28,.84,.69,.995),
        'C007P13':(.20,.85,.75,.995),'C007P03':(.29,.84,.72,.995)
    }.items():
        clips[k]['masks']=[area_mask(clips[k],box)] if k!='C001T14' else []
    line={l['id']:l for l in base['lines']}
    # Keep actual speech handoffs. Split long lines only at measured gaps in the original voice.
    duties=[
        (0,33,'l1'),(33,60,'l1'),(60,94,'l1'),(94,113,'fixed'),(113,127,'fixed'),
        (127,149,'l4'),(149,174,'pause_table'),(174,193,'l5'),
        (193,241,'l6'),(241,289,'l7'),(289,319,'l7'),(319,340,'l8'),(340,380,'l8'),
        (380,421,'gather'),(421,434,'l9'),(434,455,'show_card'),(455,500,'l9'),(500,545,'closing'),
    ]
    speech_keys={
       'l1':('C001P03','C001P09','C001T14','C001P01'),
       'l4':('C003P06','C003P01'),
       'l5':('C003P06','C003P01'),
       'l6':('M09','C001T14','C001P03','C001P09'),
       'l7':('C007P09','C007P03','C007P04','M03'),
       'l8':('C007P04','C007P09','C007P03','M03','C007P13'),
       'l9':('M06','C007P13','C007P04','M03'),
    }
    def rawkey(k,n):
        v=clips[k]
        return (v.get('source_sha256') or v['sha256'],v.get('source_start',round(v.get('source_start_s',0)*30))+n)
    baseline = {r['at']: r for r in read(task/'full_refined'/'outcome.json')['planned']}
    areas = {}
    previews = out / 'mouth_previews'
    previews.mkdir(exist_ok=True)
    # Same crop, mask, scale and encoder as export. Compare the actual pixels.
    for k in sorted({k for lid in ('l6','l7') for k in speech_keys[lid]}):
        media = out / clips[k]['path']
        box = crops[k]
        picture = previews / (k + '.mp4')
        render_shot(case, media, clips[k], 0, clips[k]['frames'], box, picture)
        src = sampled_frames(media, step=1)
        dst = sampled_frames(picture, step=1)
        if len(src) != len(dst):
            raise ValueError(f'Incomplete picture scan for {k}')
        areas[k] = []
        for actor, track in pockets[k]:
            entries = []
            for j, value in track.items():
                x, y, w, _, _ = value
                area = matching_pose(src[j], dst[j], (x / 640, y / 360), w / 640, box)
                if area is not None:
                    entries.append((j,(0,0,0,0,area)))
            areas[k].append((actor,entries))
        del src,dst
        print(k, [len(entries) for _,entries in areas[k]], flush=True)
    options=[]
    for i,(a,e,lid) in enumerate(duties):
        options.append([])
        def add(k,n,box,quality=0,matched=0):
            if n<0 or n+e-a>clips[k]['frames']:
                return
            opt=dict(clip=k,at=a,score=matched,rank=quality,crop=box,**{'in':n})
            if opt not in options[i]:options[i].append(opt)
        if lid not in ('l6','l7'):
            opt=baseline[a]
            add(opt['clip'],opt['in'],opt['crop'],matched=opt['score'])
            continue
        if lid=='fixed':
            c=choice['cuts'][0 if a==94 else 1]
            add(c['clip'],c['in'],c.get('crop',[0,0,1,1]),matched=len(line['l2' if a==94 else 'l3']['voice_frames']))
            continue
        if lid=='pause_table':
            for n in (73,76,79,83):
                add('M11',n,[0,0,1,1],3)
            continue
        if lid in ('gather','show_card','closing'):
            k,starts={'gather':('M04',range(0,24,5)),'show_card':('M05',range(0,26,5)),
                      'closing':('M05',range(40,52,4))}[lid]
            for n in starts:
                add(k,n,[0,0,1,1],5)
            continue
        for k in speech_keys[lid]:
            box=crops[k];target=line[lid]['actor'];voices=set(line[lid]['voice_frames']) & set(range(a,e))
            if k=='C007P09' and a==289:pref=9
            elif k=='M09' and lid=='l6':pref=3
            elif k=='C007P13' and lid=='l9':pref=4
            elif k=='C007P09' and lid in ('l7','l8'):pref=5
            elif k=='C003P01':pref=-7
            elif k in ('C001T14','C001P03'):pref=3
            else:pref=0
            scores=[]
            for n in range(clips[k]['frames']-(e-a)+1):
                hit=set()
                for actor, entries in areas[k]:
                    if actor != target:
                        continue
                    start = n + max(0, line[lid]['at'] - a)
                    end = n + min(e, line[lid]['end']) - a
                    inside=[(j,v) for j,v in entries if start<=j<end]
                    for _, group in groupby(enumerate(inside), key=lambda v: v[1][0]-v[0]):
                        for x,y in cycles([v for _,v in group],30):
                            hit.update(range(a+x-n,a+y-n))
                count=len(hit & voices)
                scores.append((count,n))
            for count,n in sorted(scores, key=lambda m:(-m[0],m[1]))[:33]:
                add(k,n,box,pref+count*3,matched=count)
    write_json(out/'preview_options.json',[
        {'at':a,'line':lid,'top_per_source':{
            k:max((o for o in options[i] if o['clip']==k),key=lambda o:o['score'])
            for k in dict.fromkeys(o['clip'] for o in options[i])
        }} for i,(a,e,lid) in enumerate(duties)
    ])
    pools=[]
    beams=[(0,[],{})]
    for i,(a,e,lid) in enumerate(duties):
        after=[]
        for value,seq,used in beams:
            for option in options[i]:
                k=option['clip'];n=option['in']
                d,start=rawkey(k,n);span=(start,start+e-a)
                prev_intervals=used.get(d,[])
                if any(max(x,span[0])<min(y,span[1]) for x,y in prev_intervals):
                    continue
                continuity=0
                if seq and seq[-1]['clip']==k:
                    last=seq[-1]
                    continuity=8 if last['in']+a-last['at']==n else -4
                # Keep the known 13-frame offcut available even before it is appended.
                future=[]
                for j in (3,4):
                    if i<j:
                        f=options[j][0]
                        fk,fi=f['clip'],f['in']
                        fs,ft=rawkey(fk,fi)
                        if d==fs:future.append((ft,ft+duties[j][1]-duties[j][0]))
                if any(max(x,span[0])<min(y,span[1]) for x,y in future):
                    continue
                new=dict(used);new[d]=[*prev_intervals,span]
                after.append((value+option['rank']+continuity,[*seq,option],new))
        if not after:
            raise ValueError(f"No non-overlapping source sequence at {a} ({lid})")
        # Preserve choices using different source shots so later slots can find unused footage.
        after.sort(key=lambda v:-v[0])
        buckets=defaultdict(int)
        beams=[]
        for j in after:
            key=tuple(v['clip'] for v in j[1])
            if buckets[key]<7:
                beams.append(j);buckets[key]+=1
            if len(beams)>=350:break
    layouts=[]
    # Disallow adjacent repeats recutting the same shot in reverse. A contiguous
    # match is a single shot, and is merged in the time line below.
    for val,seq,_ in beams:
        if any(left['clip']==right['clip'] and left['in']+right['at']-left['at']!=right['in']
               for left,right in zip(seq,seq[1:])):
            continue
        shape=tuple(r['clip'] for r in seq)
        if any(tuple(r['clip'] for r in s)==shape for s in layouts):
            continue
        if layouts and sum(r['score'] for r in seq) < sum(r['score'] for r in layouts[0])-4:
            continue
        layouts.append(seq)
        if len(layouts)>=4:break
    if not layouts:
        raise ValueError('No eligible layout after continuity checks')
    preset={}
    for i,layout in enumerate(layouts,1):
        preset[str(i)]={"predicted_frames":sum(o['score'] for o in layout),
                         "sequence":[{"at":o['at'],"clip":o['clip'],"source_in":o['in'],"picture":descriptions.get(o['clip'],o['clip'])} for o in layout]}
    sampled={}
    board=Image.new("RGB",(len(duties)*95,len(layouts)*86), "#20272b")
    pen=ImageDraw.Draw(board)
    for i,seq in enumerate(layouts):
        for j,(r,(_,end,_)) in enumerate(zip(seq,duties)):
            k=r['clip']
            if k not in sampled:sampled[k]=sampled_frames(out/clips[k]['path'],step=1)
            n=r['in']+(end-r['at'])//2
            im=Image.fromarray(sampled[k][n]);x,y,w,h=r['crop']
            im=im.crop((int(x*640),int(y*360),int(w*640),int(h*360)))
            board.paste(im.resize((92,52)),(95*j,i*86+19))
            pen.text((95*j+2,i*86+2),f'{i+1} #{j+1} F{r["at"]}',fill='white')
    del sampled
    shot_sheet=out/'storyboards.jpg'
    board.save(shot_sheet,quality=95)
    report=out/'candidate_assemblies.json'
    write_json(report,preset)
    case['clips']=clips
    prompt=(
        "正在剪《立刻有》9句，沿用原配音时钟。镜头中对话时只给当前演员的嘴，禁止回放同一段动作。"
        "这张图是程序从看过的素材和真实发声时段筛出来的连续组接方案。每行一种顺序，每格有序号和成片开始帧。"
        "只重剪起点为 193、241、289 帧的镜头，其余保持复剪版。看三处构图和背景的衔接，蓝头解释时不要来回重播，"
        "末尾留给牌和白圆耳的反应。已经按真实口型、裁遮和配音的句界估计有效嘴动。优先覆盖发声帧，蓝头在289附近的停顿才允许同一句换视角。选其中一行剪辑组接。"
        '仅返回 {"assembly":"1","why":"你亲眼看到的剪辑取舍","gaps":["仍需核对的动作或背景"]}。'
        +encoded({"script":[{"actor":l["actor"],"text":l["text"]} for l in base["lines"]],"candidate":preset})
    )
    def valid(v):
        if v["assembly"] not in preset or not v["why"]:
            raise ValueError("选择一条现有组接")
        return v
    decision=ask(case,out,'glmsel_single_full',prompt,shot_sheet,valid)
    write_json(out/'decision.json',decision)
    selected=layouts[int(decision['assembly'])-1]
    cuts=[]
    for r in selected:
        row={k:v for k,v in r.items() if k not in ('rank','score')}
        if cuts and row['clip']==cuts[-1]['clip'] and row['crop']==cuts[-1]['crop'] and row['in']==cuts[-1]['in']+row['at']-cuts[-1]['at']:
            continue
        cuts.append(row)
    validate_cuts(cuts,case)
    film=render(case,out,out,cuts,'single_full')
    obs={k:observations.get(k,{"method":"contour_cycle","slots":[]}) for k in clips}
    score=score_output(case,out,out,film,cuts,None,obs,'single_full')
    report=read(out/'single_full_render.json')
    write_json(out/'outcome.json',dict(decision=decision,render=report,score=score,planned=selected,
                                     fixed_exchange=choice['cuts'],actual_changed_frames=major_picture_changes(sampled_frames(film,step=1))))
    print([(l['id'],l['approved'],l['voiced']) for l in score['lines']],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('bundle','previous','qa','rabbit','task'):p.add_argument(n,type=Path)
    a=p.parse_args();run(a.bundle,a.previous,a.qa,a.rabbit,a.task)
