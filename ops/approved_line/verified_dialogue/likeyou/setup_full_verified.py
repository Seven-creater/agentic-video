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

def setup(b,previous,qa,rabbit_dir,task):
    base=read(b/'bundle.json'); case=dict(base)
    old=read(previous/'draft_render.json')
    choice=read(qa/'outcome.json')
    if choice['scores']['passed']!=2:
        raise ValueError("Both restored opening exchange lines must pass before assembly")
    prepared=read(rabbit_dir/'final_recheck.json')
    facts=read(task/'scan.json')
    facts.update(read(task/'extra'/'scan.json'))
    out=task/'full_verified'
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
    case["clips"] = clips
    return case, {k: observations.get(k,{"method":"contour_cycle","slots":[]}) for k in clips}, out, crops
