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
scans=job/'alternate'
out=job/'alternate_verified_film';out.mkdir(exist_ok=True)
prepared=read(b/'speech_ready.json'); lines=prepared['clock']
digest=read(creative/'watched_windows.json')[0]['source_sha256']
cast={l['role']:l['actor'] for l in lines}
identity={'a':cast['访客'],'b':cast['大爷']}
# Observed shot geometry specifies which visible actor a source can support, independent of dub line or offset.
# Mask just the burned-in subtitles, leaving the stall sign and the faces intact.
full=[0,0,1,1]
profiles=[
 ('B0','A1',0,67,[.21,.17,.79,.75],'a',(0,),0,'蓝蝴蝶结访客带耙站在草地上'),
 ('B1','A2',298,345,[.17,.08,.57,.48],'a',(2,),5,'访客在门口面对屋主人'),
 ('B2','A3',216,306,[0,.32,.57,.89],'a',(1,),7,'访客正脸，村屋前站定'),
 ('B3','A3',306,411,[.01,.12,.55,.66],'a',(2,),7,'访客面对门口询问，近景'),
 ('B4','A4',76,195,[.19,.39,.51,.71],'a',(0,),4,'屋门外访客，稍远单人构图'),
 ('B5','A4',353,417,[.05,.33,.72,1],'a',(1,),6,'访客略显慌张，准备告别'),
 ('R1','A2',247,347,[.28,.43,.76,.91],'b',(1,3),6,'白圆耳在门边回头，可以做误听与回问'),
 ('R2','A3',150,216,[.24,.22,.89,.87],'b',(),5,'屋门白圆耳单人近景，思索'),
 ('R3','A3',411,467,[.22,.26,.76,.80],'b',(3,),7,'圆耳站在自己家门口，有开合和瞪眼'),
 ('R4','A4',195,292,[.39,.32,.99,.92],'b',(),5,'白圆耳在屋门口，微笑后愣住'),
 ('R5','A3',9,143,[.05,.07,.58,.60],'b',(),4,'圆耳在门外驻足，露正脸，另一人背对'),
 ('I1','A3',0,144,full,'',(),9,'村屋门前两人相遇'),
 ('I2','A4',421,509,full,'',(),7,'访客转身离开圆顶屋'),
 ('I3','A4',75,195,full,'',(),4,'两人站在屋门口彼此相对'),
 ('I4','A2',72,219,full,'',(),3,'访客来到屋门外，圆耳面对访客'),
 ('I5','A3',216,306,full,'',(),4,'访客与白圆耳在相邻屋门前同框'),
 ('I6','A2',354,455,full,'',(),1,'门前远景，交代站位，避开字幕卡'),
 ('I7','A4',292,350,full,'',(),3,'白圆耳从门边探身回应'),
]
clips={};obs={};facts=read(scans/'scan.json')
facts.update(read(job/'alternate_extra'/'scan.json'))
def receipt(kind,lo,hi,box):
    x,y,w,h=box
    return dict(start_s=lo/30,end_s=hi/30,x=x,y=y,w=w,h=h,opacity=.92,block_px=16)
masks={
    'A1':[receipt('subtitle',8,510,(.25,.795,.50,.195))],
    'A2':[receipt('subtitle',60,450,(.25,.825,.48,.155))],
    'A3':[receipt('subtitle',12,411,(.25,.83,.50,.16))],
    'A4':[receipt('subtitle',37,410,(.24,.80,.53,.19))],
}
for k in facts:
    target=b/(k+'.mp4')
    if not target.exists():target.symlink_to((scans if k in ('A1','A2') else job/'alternate_extra')/target.name)
    f=facts[k]
    clips[k]=dict(path=target.name,frames=f['frames'],sha256=sha256_file(target),
           source_start=round(f['source_start_s']*30),source_sha256=digest,group='scene_1',masks=masks[k])
    obs[k]=dict(method='contour_cycle',slots=[])
    for _,source,_,_,_,actor,indices,_,_ in profiles:
        if source!=k:continue
        for i in indices:
            track=f['tracks'][i]; pts=[(int(n),v) for n,v in track['points'].items()]
            obs[k]['slots'].append(dict(actor=identity[actor],start=track['start'],end=track['end'],track_frames=[n for n,_ in pts],
                 center=[[p[0]/640,p[1]/360] for _,p in pts],widths=[p[2]/640 for _,p in pts],
                 evidence='实际全图里对应演员嘴形变化，排除眼睛、背面轮廓和字幕。',index=i))
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
        if not line and kind=='ending' and name!='I2':continue
        if not line and kind=='reaction' and name=='I2':continue
        if line and name=='B5' and line['id'] not in ('l2','l8'):continue
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
            if line and line['id']=='l8' and name=='B5':reward+=5
            if line and line['id'] in ('l9','l10') and name=='R3':reward+=3
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

def cuts_for(layout):
    cuts=[]
    for _,_,_,row,*_ in layout:
        if cuts and cuts[-1]['clip']==row['clip'] and cuts[-1]['crop']==row['crop'] and cuts[-1]['in']+row['at']-cuts[-1]['at']==row['in']:
            continue
        cuts.append(row)
    return cuts

# Test complete actual renders to account for new subtitles and the final encoding as well.
verified={}
chosen_files={}
for key,layout in layouts.items():
    cuts=cuts_for(layout)
    validate_cuts(cuts,case)
    name='check_'+key
    film=render(case,b,out,cuts,name)
    score=score_output(case,b,out,film,cuts,None,obs,name)
    verified[key]=score
    chosen_files[key]=(film,cuts)
    print('actual',key,score['confirmed_frames'],score['passed'],flush=True)
write_json(out/'verified_options.json',verified)
top=max(r['confirmed_frames'] for r in verified.values())
layouts={k:v for k,v in layouts.items() if verified[k]['confirmed_frames']>=top-4 and not verified[k]['competing_mouth_frames']}
if not layouts:
    raise ValueError('No highest scoring assembly is free from confirmed competing mouths')

summary={}
board=Image.new('RGB',(len(duties)*86,len(layouts)*82),'#252d32');draw=ImageDraw.Draw(board)
for i,(key,options) in enumerate(layouts.items()):
    rows=[]
    cached=sampled_frames(chosen_files[key][0],step=1)
    for j,(option,(at,end,line,kind)) in enumerate(zip(options,duties)):
        _,start,stop,row,tag,details,hit=option
        n=(at+end)//2
        img=Image.fromarray(cached[n])
        board.paste(img.resize((84,47)),(j*86,i*82+23))
        draw.text((j*86+2,i*82+3),f'{key} F{at} {tag}',fill='white')
        rows.append(dict(tag=tag,line=line['id'] if line else kind,at=at,end=end,
                         source_in=row['in'],description=details))
    summary[key]=dict(
        covered=verified[key]['confirmed_frames'],
        per_line=[(l['id'],l['approved'],l['voiced']) for l in verified[key]['lines']],
        timeline=rows)
    del cached
pic=out/'assembly_board.jpg';board.save(pic,quality=94)
write_json(out/'measured_options.json',summary)
prompt=("这是《马冬梅》屋门口替换场景的实际整片导出稿。蓝蝴蝶结是访客，白圆耳是大爷。对照最终画面、逐句已确认帧数和台词，选一行。"
        "保住人物和扭头、问完再交镜头，尽量少用与剧情无关的工具、走动、突然表情和前后站位变化。"
        "已经核实的总帧数和每句数据是成片实测。优先尽量多的发声句全过，其次节奏和故事可读性。"
        '只返回 JSON {"choice":"1","why":"按实际画面的取舍","limitations":["仍看不清的部分"]}。'
        +encoded(dict(lines=[(l['id'],l['role'],l['text']) for l in lines],options=summary)))
def valid(v):
    if v.get('choice') not in layouts or not v.get('why'):
        raise ValueError('选择一行已核实的方案并说明依据')
    return v
reply=ask(case,out,'mah_alternate_verified_cut',prompt,pic,valid)
write_json(out/'decision.json',reply)
film,cuts=chosen_files[reply['choice']]
result=verified[reply['choice']]
from shutil import copyfile
copyfile(film,out/'madongmei.mp4')
r=read(out/(film.stem+'_render.json'))
write_json(out/'outcome.json',dict(score=result,decision=reply,render=r,picture_changes=major_picture_changes(sampled_frames(film,step=1)),
   planned=summary[reply['choice']]['timeline'],cast=cast,
   source_actors=dict(visitor='蓝蝴蝶结小兽',elder='白色圆耳小兽'),source_scenes=['s451','s452'],source_sha256=digest,
   actual_alternative_scores={k:dict(confirmed=v['confirmed_frames'],passed=v['passed']) for k,v in verified.items()},
   audio_sources=prepared['sources']))
print('FINAL',[(l['id'],l['approved'],l['voiced']) for l in result['lines']],flush=True)
