from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,json,math,subprocess
import numpy as np
from PIL import Image,ImageDraw
from omni_story.library.dialogue_mouth_tracks import candidates,face_support,witness_frames,cycles
from omni_story.library.dialogue_mouth_detail import major_picture_changes
from omni_story.library.media import sha256_file
from omni_story.library.dialogue_mouth_edit import read
from omni_story.library.selected_edit import ff

def scan(bundle,task):
    lock=read(bundle/'bundle.json')
    index=read(task/'index_shortlist.json')
    source=next(s for s in read(Path(lock['configuration_path']).parent/'catalog/inventory.json')['sources'] if s['sha256']==lock['clips']['M02']['source_sha256'])
    fps=lock['fps']
    q=task/'choices';q.mkdir(exist_ok=True)
    rows=[(f'{actor}T{i:02}',actor,r) for actor,rr in index.items() for i,r in enumerate(rr,1)]
    def process(item):
        key,actor,record=item
        source_start=round(record['source_start_s']*fps)
        n=min(240,round(record['source_end_s']*fps)-source_start)
        common=["-ss",f"{source_start/fps:.7f}","-i",source["path"],"-an"]
        raw=subprocess.check_output(["ffmpeg","-v","error",*common,"-vf","fps=30,scale=640:360",
            "-frames:v",str(n),"-threads","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"])
        frames=np.frombuffer(raw,np.uint8).reshape(-1,360,640,3)
        large=set(major_picture_changes(frames))
        tracks=[]
        for a,b,t in candidates(frames,fps):
            if b-a<12 or any(a<cut<b for cut in large):
                continue
            watched=witness_frames(t)
            if sum(face_support(frames[n], t[n]) for n in watched)*2<=len(watched):
                continue
            tracks.append(dict(start=a,end=b,points={n:list(v) for n,v in t.items()},
                               witnesses=watched))
        if not tracks:
            return None
        path=q/(key+'.mp4')
        ff(*common,"-vf","fps=30,scale=1280:720,setsar=1","-frames:v",len(frames),"-c:v","libx264",
           "-preset","veryfast","-crf",18,"-threads",2,path)
        pics=[]
        for i,t in enumerate(tracks):
            witnesses=t['witnesses']
            panel=Image.new('RGB',(920,194),'#263238')
            dr=ImageDraw.Draw(panel)
            for k,j in enumerate(witnesses):
                x=k*182
                pic=Image.fromarray(frames[j]);cx,cy,w,h,_=t['points'][j]
                d=ImageDraw.Draw(pic)
                d.rectangle([cx-w*.62,cy-h*.62,cx+w*.62,cy+h*.62],outline='#00eaff',width=2)
                panel.paste(pic.resize((179,102)),(x,18))
                crops=Image.fromarray(frames[j]).crop((round(cx-42),round(cy-26),round(cx+42),round(cy+26)))
                panel.paste(crops.resize((108,64)),(x,124))
                dr.text((x+2,1),f'{key}.{i} F{j}',fill='white')
            p=q/(f'{key}_{i}.jpg');panel.save(p,quality=91);pics.append(p.name)
        masks=record.get('subtitle_treatment')
        return key,dict(path=path.name,sha256=sha256_file(path),frames=len(frames),
                        expected_actor=actor,source_start=source_start,source_sha256=source["sha256"],
                        record_id=record['record_id'],action=record['action'],place=record['place'],
                        subtitle_treatment=masks,tracks=tracks,boards=pics)
    result={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for x in pool.map(process,rows):
            if x:
                key,info=x
                result[key]=info
                print(key,len(info['tracks']),[(t['start'],t['end']) for t in info['tracks']],flush=True)
                (task/'scan.json').write_text(json.dumps(result,ensure_ascii=False))
    (task/'scan.json').write_text(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('bundle',type=Path);p.add_argument('task',type=Path);a=p.parse_args();scan(a.bundle,a.task)
