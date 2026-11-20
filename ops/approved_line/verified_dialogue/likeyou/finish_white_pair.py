from pathlib import Path
import sys
from omni_story.library.dialogue_mouth_edit import ask,encoded,read,render,score_output,validate_cuts
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes
from omni_story.library.dialogue_mouth_tracks import cycles
from omni_story.library.selected_edit import ff,concat
from omni_story.library.state import write_json

base,first,rabbit_dir,task=map(Path,sys.argv[1:])
seed=read(base/'bundle.json')
clock=read(first/'comparison.json')
prev=read(rabbit_dir/'final_recheck.json')
facts=read(task/'scan.json')
facts.update(read(task/'extra'/'scan.json'))
lines=clock['lines']
frames=lines[1]['end']-lines[1]['at']
target=lines[1]['actor']
out=task/'finished_pair'
out.mkdir(exist_ok=True)
choices={'C001T14':([.30,.36,.68,.74],[0]),'C001P09':([.52,.40,.98,.86],[1,2])}
def link(src,dest):
    if not dest.exists():
        dest.symlink_to(src)

def slots(track,key,actor):
    points=list(track['points'].values())
    return dict(actor=actor,start=track['start'],end=track['end'],
        evidence='实帧看见该演员连续嘴部开合，已核对同框人和道具',
        track_frames=list(range(track['start'],track['end'])),
        center=[[p[0]/640,p[1]/360] for p in points],widths=[p[2]/640 for p in points])

options={}
observed={}
video=[]
for key,(crop,indices) in choices.items():
    row=facts[key]
    catalog = task/'extra' if 'T' in key else task
    link(catalog/'choices'/row['path'],out/row['path'])
    opt=[]
    passed=[]
    for i in indices:
        track=row['tracks'][i]
        points={int(k):v for k,v in track['points'].items()}
        left,top,right,bottom=crop
        inside={}
        for n,p in points.items():
            px=max(6,p[2]*.60)
            if left*640 < p[0]-px < p[0]+px < right*640 and top*360 < p[1]-px < p[1]+px < bottom*360:
                inside[n]=p
        starts=[]
        for t in range(track['start'],track['end']-frames+1):
            if not all(n in inside for n in range(t,t+frames)):
                continue
            rs=cycles([(n,v) for n,v in inside.items() if t<=n<t+frames],seed['fps'])
            good={n-t+lines[1]['at'] for a,b in rs for n in range(a,b)}
            if set(lines[1]['voice_frames'])<=good:
                starts.append(t)
        if starts:
            opt.extend(starts)
            passed.append(slots(track,key,target))
    if not opt:continue
    piece=out/(key+'_reads.mp4')
    ff('-i',out/row['path'],'-an','-vf',
       f"scale=832:468,pad=iw:ih+30:0:30,drawtext=text='{key} SOURCE F%{{n}}':x=5:y=2:fontsize=21:fontcolor=white",
       '-c:v','libx264','-preset','veryfast','-crf',23,'-threads',2,piece)
    video.append(piece)
    options[key]={"allowed_starts":opt,"solo_crop":crop,"length":row['frames'],"actor":target}
    observed[key]={"method":"contour_cycle","slots":passed}
if not options:
    raise ValueError("No verified window has 13 complete frames")
pack=concat(out,video,'white_candidates')
old=prev['cuts'][0]
rabbit=old['clip']
src=task.parent/'reply_pair_supplement'
for name in ('pair_dub.m4a','pair.ass'):
    link(first/name,out/name)
link(rabbit_dir/(rabbit+'.mp4'),out/(rabbit+'.mp4'))
clip_data={key:dict(facts[key],masks=[]) for key in options}
clip_data[rabbit]=dict(prev['source_record'],sha256=prev['selected_media_sha256'],masks=[prev['mask']])
case=dict(seed,clips=clip_data,total=32,lines=lines,dub='pair_dub.m4a',subs='pair.ass')
prompt=(
    "只换第二句13帧。第一句长耳兔答‘立刻有。’已经核验，不能动。"
    "第二句是白色圆耳C001问‘立刻有？’，保留一个画面从头说到尾。"
    "候选原画有编号和源帧，已经对过包含完整嘴部开合的可用入点。"
    "你自己选最像听见意外回答后的追问的镜头和入点，避免进食或带进其他角色的嘴。"
    "每段对应单人范围已看过，渲染用solo_crop。"
    '请只输出{"clip":"实际编号","in":源帧整数,"why":"实际看到的嘴形和追问表情"}。'
    +encoded(options)
)
def chosen(v):
    key=v['clip']
    if key not in options or type(v['in']) is not int or v['in'] not in options[key]['allowed_starts'] or not v['why']:
        raise ValueError("选一个有本句用法的允许入点")
    return v
v=ask(case,out,'glmsel_white_requestion',prompt,pack,chosen)
clip=v['clip']
cuts=[old,{"clip":clip,"in":v['in'],"at":lines[1]['at'],"crop":options[clip]['solo_crop']}]
validate_cuts(cuts,case)
write_json(out/'decision.json',v)
film=render(case,out,out,cuts,'pair_both')
observed[rabbit]={"method":"contour_cycle","slots":read(rabbit_dir/'candidate_mouths.json')[rabbit]}
score=score_output(case,out,out,film,cuts,None,observed,'pair_both')
data={'solid_options':options,'decision':v,'cuts':cuts,'source_records':{k:clip_data[k]['record_id'] for k in (rabbit,clip)},
      'scores':score,'render':read(out/'pair_both_render.json'),
      'picture_changes':major_picture_changes(sampled_frames(film,step=1))}
assert data['render']['audio']==prev['render']['audio']
write_json(out/'outcome.json',data)
print(data['decision'],[(l['id'],l['approved'],l['voiced']) for l in score['lines']],flush=True)
