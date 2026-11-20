from pathlib import Path
import json,sys,subprocess
from PIL import Image,ImageDraw
from omni_story.library.dialogue_mouth_tracks import candidates
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes
from omni_story.library.state import write_json

creative,job=map(Path,sys.argv[1:3])
out=job/'alternate';out.mkdir(exist_ok=True)
source=json.loads((creative/'watched_windows.json').read_text())[0]
clips={}
for k,origin,length in [('A1',21842.73333333333,510),('A2',21859.73333333333,510)]:
 f=out/(k+'.mp4')
 if not f.exists():
  subprocess.run(['ffmpeg','-v','error','-y','-threads','1','-ss',str(origin),'-i',source['source_path'],'-an',
          '-vf','fps=30,scale=1280:720,setsar=1','-frames:v',str(length),'-threads','2','-c:v','libx264','-crf','16',f],check=True)
 frames=sampled_frames(f,step=1)
 changes=[0,*major_picture_changes(frames),len(frames)]
 rows=[]
 for a,b in zip(changes,changes[1:]):
  if b-a<6:continue
  part=candidates(frames[a:b],30)
  print(k,a,b,[(start+a,end+a) for start,end,_ in part],flush=True)
  for start,end,points in part:
   p={n+a:list(map(float,v)) for n,v in points.items()}
   rows.append(dict(start=a+start,end=a+end,points=p,cut=[a,b]))
 def make_images(data, suffix):
  for page in range(0,len(data),6):
   board=Image.new('RGB',(1000,140*min(len(data)-page,6)),'#22262a');pen=ImageDraw.Draw(board)
   for i,(tag,ids,points) in enumerate(data[page:page+6]):
    for j,n in enumerate(ids):
     im=Image.fromarray(frames[n])
     if points:
      x,y,w,h,_=points[n];ImageDraw.Draw(im).rectangle((x-w/2,y-h/2,x+w/2,y+h/2),outline='#00ffff',width=3)
     board.paste(im.resize((196,110)),(200*j,140*i+21));pen.text((200*j+3,140*i+4),f'{tag} F{n}',fill='white')
   board.save(out/(k+'_'+suffix+str(page//6)+'.jpg'),quality=90)
 make_images([(f'{k}.{i}',[a+(b-a-1)*j//4 for j in range(5)],{}) for i,(a,b) in enumerate(zip(changes,changes[1:]))],'shots')
 make_images([(f'{k}.{i}',[m for m in [sorted(t['points'])[j*(len(t['points'])-1)//4] for j in range(5)]],t['points']) for i,t in enumerate(rows)],'tracks')
 clips[k]=dict(frames=len(frames),changes=changes,tracks=rows,source_start_s=origin,source_sha256=source['source_sha256'])
 del frames
write_json(out/'scan.json',clips)
