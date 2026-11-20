from pathlib import Path
from collections import defaultdict
from PIL import Image, ImageDraw
from omni_story.library.dialogue_mouth_edit import ask, encoded, frame_at, read, render, score_output, validate_cuts
from omni_story.library.dialogue_mouth_detail import sampled_frames, major_picture_changes
from omni_story.library.state import write_json
from setup_full_verified import setup
import sys

b,previous,qa,rabbit_dir,task=map(Path,sys.argv[1:])
case,obs,out,crops=setup(b,previous,qa,rabbit_dir,task)
refined=read(task/'full_refined'/'outcome.json')
locked=read(task/'full_locked'/'outcome.json')
proof={0:[],241:[]}
scorebook={}
def opening_candidates():
    for opt in read(task/'opening_options.json'):
        yield [{k:v for k,v in r.items() if k!='supported'} for r in opt['cuts']]
    yield [r for r in refined['render']['cuts'] if r['at']<94]

def blue_candidates():
    candidates=read(task/'full_locked'/'candidate_assemblies.json')
    for row in candidates.values():
        yield [dict(at=r['at'],clip=r['clip'],crop=crops[r['clip']],**{'in':r['source_in']})
                for r in row['sequence'] if r['at'] in (241,289)]
    yield [r for r in refined['render']['cuts'] if r['at']==241]

def verify(lid, start, end, candidates, floor):
    original=next(l for l in case['lines'] if l['id']==lid)
    event=next(line for line in (out/case['subs']).read_text().splitlines()
               if line.startswith('Dialogue:') and line.endswith(','+original['text']))
    header=(out/case['subs']).read_text().split('Dialogue:')[0]
    subname=f'{lid}_check.ass'
    event=event.split(',',3)
    timed=end-start
    centiseconds=round(timed*100/30)
    deadline=f'0:00:{centiseconds//100:02}.{centiseconds%100:02}'
    (out/subname).write_text(header+','.join([event[0],'0:00:00.00',deadline,event[3]])+'\n')
    local_line=dict(original,at=0,end=timed,voice_frames=[n-start for n in original['voice_frames']])
    trial=dict(case,total=timed,lines=[local_line],subs=subname)
    good=[]
    for i,cuts in enumerate(candidates,1):
        local=[dict(r,at=r['at']-start) for r in cuts]
        only={r['clip'] for r in local}
        test=dict(trial,clips={k:case['clips'][k] for k in only})
        name=f'{lid}_pick{i}'
        validate_cuts(local,test)
        film=render(test,out,out,local,name)
        report=score_output(test,out,out,film,local,None,{k:obs[k] for k in only},name)['lines'][0]
        count=report['approved']
        scorebook[name]=dict(report=report,cuts=cuts)
        print(name,count,flush=True)
        if count>=floor and not report['competing_mouth_frames']:
            good.append((count,cuts,film))
    return sorted(good,key=lambda x:-x[0])
for key,start,end,opts,floor in [('l1',0,94,opening_candidates(),69),('l7',241,319,blue_candidates(),61)]:
    proof[start]=verify(key,start,end,opts,floor)
    if not proof[start]:raise ValueError(f'No verified option preserved baseline in {key}')
write_json(out/'verified_alternatives.json',scorebook)
group=[]
preset={}
for at,choices in proof.items():
    best=choices[0][0]
    for i,(count,cuts,film) in enumerate(choices,1):
        if count < best-1:continue
        name=f'{at}_{i}'
        preset[name]={'spoken_frames_verified':count,'cuts':cuts}
        group.append((name,at,cuts,film))
board=Image.new('RGB',(960,112*len(group)),'#1b2329')
pen=ImageDraw.Draw(board)
for i,(name,start,cuts,film) in enumerate(group):
    imgs=sampled_frames(film)
    pen.text((4,i*112+2),f'{name} confirmed={preset[name]["spoken_frames_verified"]}',fill='white')
    for j,n in enumerate(round((len(imgs)-1)*j/5) for j in range(6)):
        board.paste(Image.fromarray(imgs[n]).resize((156,88)),(160*j,i*112+19))
    del imgs
gallery=out/'verified_options.jpg'
board.save(gallery,quality=91)
reply=ask(case,out,'glmsel_proven_dialogue','''正在复剪《立刻有》整片，只选开场和蓝头解释。每一行是真正裁切、遮盖旧字幕、叠新字幕后导出的视频抽帧。帧数越多越好，优先顺畅可读，说话时只有一张嘴动，避免叼食物、张嘴吃或者无关动作。开场0到94帧是白圆耳邀请对方吃饼干，241到319帧是蓝头解释。两段分别在相同开头帧编号的选项中选择一行。已通过的中间问答及其他情节不要修改。返回{"opening":"0_1","explanation":"241_1","why":"两段画面上的取舍"}。'''+encoded(preset),gallery,
          lambda v:v if v['opening'] in preset and v['opening'].startswith('0_') and v['explanation'] in preset and v['explanation'].startswith('241_') and v['why'] else (_ for _ in ()).throw(ValueError('不属于各自时段的有效选项')))
write_json(out/'decision.json',reply)
cuts=preset[reply['opening']]['cuts']+[r for r in locked['render']['cuts'] if 94<=r['at']<241]+preset[reply['explanation']]['cuts']+[r for r in locked['render']['cuts'] if r['at']>=319]
validate_cuts(cuts,case)
film=render(case,out,out,cuts,'single_full')
result=score_output(case,out,out,film,cuts,None,obs,'single_full')
report=read(out/'single_full_render.json')
write_json(out/'outcome.json',dict(decision=reply,render=report,score=result,
           actual_changed_frames=major_picture_changes(sampled_frames(film,step=1)),
           alternate_preview_scores={k:v['spoken_frames_verified'] for k,v in preset.items()}))
print('RESULT',[(l['id'],l['approved'],l['voiced']) for l in result['lines']],flush=True)
