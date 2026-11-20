from collections import defaultdict
from itertools import groupby
from pathlib import Path
import json,sys
from PIL import Image,ImageDraw
from omni_story.library.dialogue_mouth_edit import ask,encoded,read,render,render_shot,score_output,validate_cuts
from omni_story.library.dialogue_mouth_tracks import cycles,matching_pose
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json

creative,job=map(Path,sys.argv[1:3])
b=job/'bundle'
scans=job/'scanned'
out=job/'locked_edit_film';out.mkdir(exist_ok=True)
prepared=read(b/'speech_ready.json'); lines=prepared['clock']
digest=read(creative/'watched_windows.json')[0]['source_sha256']
cast={l['role']:l['actor'] for l in lines}
identity={'a':cast['访客'],'b':cast['大爷']}
# Observed shot geometry specifies which visible actor a source can support, independent of dub line or offset.
# Mask just the burned-in subtitles, leaving the stall sign and the faces intact.
full=[0,0,1,1]
profiles=[
 ('D1','W1',49,133,[.10,.02,.84,.76],'b',(),5,'铠甲在摊后正面坐着'),
 ('D2','W2',301,419,[.435,.16,.795,.52],'b',(),3,'铠甲摊后，裁掉下沿访客和右侧另一铠甲'),
 ('V1','W1',137,193,[.265,.04,.905,.68],'a',(0,),5,'蓝尾访客正对摊主，近景'),
 ('V2','W1',199,268,[.08,.11,.60,.63],'a',(1,),3,'蓝尾访客站在摊前，中近景'),
 ('V3','W2',3,109,[.015,.045,.515,.545],'a',(0,),3,'蓝尾访客站在摊前，隔着桌子'),
 ('V4','W2',110,185,[.14,.11,.87,.84],'a',(),1,'蓝尾访客灰底大特写，惊讶'),
 ('V5','W2',185,298,[.32,.10,.88,.66],'a',(),-5,'蓝尾访客失落摇晃，适合挫败反应，嘴形随动作转头'),
 ('I1','W1',268,418,full,'',(),6,'两人之间有摊台，远景，交代地点'),
 ('I2','W2',298,419,full,'',(),1,'摊位与旁边的铠甲，蓝尾访客最后离开'),
 ('I3','W1',193,268,full,'',(),4,'过肩中景，两人保持当面对话站位'),
 ('I4','W2',3,110,full,'',(),4,'铠甲背影与访客，交代问答关系'),
 ('I5','W2',185,298,full,'',(),0,'访客冒汗失落，作为对白后的动作'),
]
duration={}
clips={};obs={};facts=read(scans/'locked_scan.json')
def receipt(kind,lo,hi,box):
    x,y,w,h=box
    return dict(start_s=lo/30,end_s=hi/30,x=x,y=y,w=w,h=h,opacity=.92,block_px=16)
masks={
    'W1':[receipt('subtitle',130,510,(.28,.845,.45,.13))],
    'W2':[receipt('subtitle',0,110,(.29,.845,.42,.13)),receipt('subtitle',185,418,(.385,.84,.24,.14))]
}
for k,origin in [('W1',1738),('W2',1753)]:
    target=b/(k+'.mp4')
    if not target.exists():target.symlink_to(scans/target.name)
    f=facts[k]
    clips[k]=dict(path=target.name,frames=f['frames'],sha256=sha256_file(target),
           source_start=origin*30,source_sha256=digest,group='scene_1',masks=masks[k])
    indices={index for _,source,_,_,_,actor,t,_,_ in profiles if source==k and actor=='a' for index in t}
    obs[k]=dict(method='contour_cycle',slots=[])
    for i in indices:
        track=f['tracks'][i]; pts=[(int(n),v) for n,v in track['points'].items()]
        obs[k]['slots'].append(dict(actor=identity['a'],start=track['start'],end=track['end'],track_frames=[n for n,_ in pts],
             center=[[p[0]/640,p[1]/360] for _,p in pts],widths=[p[2]/640 for _,p in pts],
             evidence='源画中蓝尾访客嘴部开合，已从眼睛与尾巴动图区分。',index=i))
case=dict(width=1280,height=720,fps=30,total=prepared['total'],dub='dub.m4a',subs='subs.ass',
          lines=lines,clips=clips,configuration_path=str(creative/'library_state.json'))
# Test the exact framing and mosaic on real pixels before choosing a dialogue take.
details={}
for name,k,lo,hi,crop,actor,tracks,rank,context in profiles:
    if not tracks:continue
    source=sampled_frames(b/clips[k]['path'],step=1)
    rendered=out/(name+'_evidence.mp4')
    if not rendered.exists():render_shot(case,b/clips[k]['path'],clips[k],lo,hi-lo,crop,rendered)
    picture=sampled_frames(rendered,step=1)
    assert len(picture)==hi-lo
    result=[]
    for i in tracks:
        for frame,p in facts[k]['tracks'][i]['points'].items():
            n=int(frame)
            if not lo<=n<hi:continue
            x,y,w,_,_=p
            area=matching_pose(source[n],picture[n-lo],(x/640,y/360),w/640,crop)
            if area is not None:result.append((n,(0,0,0,0,area)))
    details[name]=sorted(result)
    del source,picture

total=case['total']
duties=[(0,lines[0]['at'],None,'establish')]
for i,line in enumerate(lines):
    a,e=line['at'],line['end']
    nxt=lines[i+1]['at'] if i<len(lines)-1 else total
    voice=set(line['voice_frames'])
    parts=[a]
    while e-parts[-1]>52:
        start=parts[-1]
        options=range(start+22,start+46)
        mid=min(options,key=lambda n:(sum(t in voice for t in range(n-2,n+3)),abs(n-start-33)))
        parts.append(mid)
    # Small intervening pauses can stay on the same actor without losing the last word.
    extend=nxt if nxt-e<=21 else e
    for j,(lo,hi) in enumerate(zip(parts,[*parts[1:],extend])):
        duties.append((lo,hi,line,'talk'))
    if extend<nxt:
        duties.append((extend,nxt,None,'ending' if i==len(lines)-1 else 'reaction'))

all_options=[]
for idx,(at,end,line,kind) in enumerate(duties):
    length=end-at
    options=[]
    for name,k,lo,hi,crop,actor,tracks,rank,context in profiles:
        if not line and not idx and name!='I1':continue
        if line and identity.get(actor)!=line['actor']:continue
        if not line and actor:continue
        if not line and kind=='ending' and name not in ('I2','I5','I1'):continue
        if not line and kind=='reaction' and name=='I2':continue
        if line and name=='V5' and line['id']!='l8':continue
        for start in range(lo,hi-length+1):
            hit=set()
            if line:
                pts=[(n,p) for n,p in details.get(name,[]) if start<=n<start+min(end,line['end'])-at]
                for _,g in groupby(enumerate(pts),key=lambda z:z[1][0]-z[0]):
                    for pos,last in cycles([item for _,item in g],30):
                        hit.update(t+at-start for t in range(pos,last))
                hit&=set(line['voice_frames'])
            reward=len(hit)*4+rank
            if not line and kind=='ending':reward+=8 if name=='I2' else 0
            if not line and kind=='reaction':reward+=2 if name in ('I3','I4') else 0
            if line and line['id']=='l8' and name=='V5':reward+=5
            if line and line['id'] in ('l9','l10') and name=='D2':reward+=3
            a=clips[k]['source_start']+start
            options.append((reward,a,a+length,dict(clip=k,at=at,crop=crop,**{'in':start}),name,context,len(hit)))
    if not options:
        raise ValueError(f'No matching shot geometry for frames {at}-{end} and {line}')
    buckets=defaultdict(list)
    for option in sorted(options,key=lambda x:(-x[0],x[1])):
        slots=buckets[option[4]]
        if len(slots)<45 and not any(abs(r[1]-option[1])<=1 for r in slots):
            slots.append(option)
    all_options.append([o for v in buckets.values() for o in v])
    print('pool',idx,at,end,line['id'] if line else kind,[(k,len(v),v[0][-1]) for k,v in buckets.items()],flush=True)
# Exact source-frame constraints avoid the early choices crowding out later turns.
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix
import numpy as np

opt=[(i,o) for i,options in enumerate(all_options) for o in options]
physical=sorted({n for _,o in opt for n in range(o[1],o[2])})
row_for_frame={n:len(duties)+i for i,n in enumerate(physical)}
left=[];right=[]
for j,(i,o) in enumerate(opt):
    left.extend([i,*[row_for_frame[n] for n in range(o[1],o[2])]])
    right.extend([j]*(1+o[2]-o[1]))
matrix=coo_matrix((np.ones(len(left)),(left,right)),shape=(len(duties)+len(physical),len(opt))).tocsr()
constraints=[LinearConstraint(matrix,[1]*len(duties)+[0]*len(physical),1)]
layouts={}
for attempt in range(4):
    # Compare plans that differ in some actual camera choices.
    model=milp([-o[0]+.000001*(j%19) for j,(_,o) in enumerate(opt)],
               integrality=np.ones(len(opt)),bounds=Bounds(0,1),
               constraints=constraints,options={'time_limit':17,'mip_rel_gap':.015})
    print('assembly solve',attempt,model.message,flush=True)
    if model.x is None:break
    selected=[(i,o) for (i,o),x in zip(opt,model.x) if x>.5]
    if len(selected)!=len(duties):break
    selected.sort(key=lambda v:v[0])
    layouts[str(len(layouts)+1)]=[o for _,o in selected]
    selected_tags={i:o[4] for i,o in selected}
    same=np.array([int(selected_tags[i]==o[4]) for i,o in opt]).reshape(1,-1)
    constraints.append(LinearConstraint(same,0,len(duties)-2))
if not layouts:
    raise ValueError('No whole assembly can fit the original voices without replaying frames.')
summary={}
board=Image.new('RGB',(len(duties)*86,len(layouts)*75),'#252d32');draw=ImageDraw.Draw(board)
cached={k:sampled_frames(b/v['path'],step=1) for k,v in clips.items()}
for i,(key,options) in enumerate(layouts.items()):
    rows=[]
    for j,(option,(at,end,line,kind)) in enumerate(zip(options,duties)):
        _,start,stop,row,tag,details,hit=option
        n=row['in']+(end-at)//2
        img=Image.fromarray(cached[row['clip']][n])
        x,y,w,h=row['crop']
        img=img.crop((x*640,y*360,w*640,h*360))
        board.paste(img.resize((84,47)),(j*86,i*75+22))
        draw.text((j*86+2,i*75+3),f'{key} F{at} {tag}',fill='white')
        rows.append(dict(tag=tag,line=line['id'] if line else kind,at=at,end=end,source_in=row['in'],
                         description=details,verified_spoken_frames=hit))
    summary[key]=rows
del cached
pic=out/'assembly_board.jpg';board.save(pic,quality=94)
write_json(out/'measured_options.json',summary)
prompt=("剪《马冬梅》原台词，访客反复报一个名字，摊后大爷连续听错，访客失去耐心，末尾大爷回话。"
        "下图是同一场已选画面各区间经过真裁框和遮旧字幕预检的组接，每行一个方案。"
        "访客脸有少量确证开合。铠甲头盔嘴部没有足够可见变化，实事求是，优先正面单人留在他说完。"
        "对白时当前说话人的镜头优先，避免用走路画面或另一个人替说。确认前后站位，尽可能少碎切。"
        "给我选一行。只返回 JSON {\"choice\":\"1\",\"why\":\"按实际画面的取舍\",\"limitations\":[\"仍看不清的部分\"]}。"
        +encoded(dict(lines=[(l['id'],l['role'],l['text']) for l in lines],options=summary)))
def valid(v):
    if v.get('choice') not in layouts or not v.get('why'):
        raise ValueError('选择一行现有方案并说明依据')
    return v
reply=ask(case,out,'mah_locked_cut',prompt,pic,valid)
write_json(out/'decision.json',reply)
cuts=[]
for _,_,_,row,*_ in layouts[reply['choice']]:
    if cuts and cuts[-1]['clip']==row['clip'] and cuts[-1]['crop']==row['crop'] and cuts[-1]['in']+row['at']-cuts[-1]['at']==row['in']:continue
    cuts.append(row)
validate_cuts(cuts,case)
film=render(case,b,out,cuts,'madongmei')
result=score_output(case,b,out,film,cuts,None,obs,'madongmei')
frames=sampled_frames(film,step=1)
r=read(out/'madongmei_render.json')
write_json(out/'outcome.json',dict(score=result,decision=reply,render=r,picture_changes=major_picture_changes(frames),planned=summary[reply['choice']],cast=cast,
   source_actors=dict(visitor='白色蓝刺尾小兽',elder='灰色铠甲人'),source_sha256=digest,
   audio_sources=prepared['sources']))
print('FINAL',[(l['id'],l['approved'],l['voiced']) for l in result['lines']],flush=True)
