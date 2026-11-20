from pathlib import Path
import json,sys
from itertools import groupby
from PIL import Image,ImageDraw
from omni_story.library.dialogue_mouth_tracks import candidates,cycles
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes
from omni_story.library.state import write_json

creative,job=map(Path,sys.argv[1:3])
out=job/'scanned'
clips={}
for k in ['W1','W2']:
 frames=sampled_frames(out/(k+'.mp4'),step=1)
 changes=[0,*major_picture_changes(frames),len(frames)]
 rows=[]
 for i,(a,b) in enumerate(zip(changes,changes[1:])):
  if b-a<6:continue
  part=candidates(frames[a:b],30)
  print(k,a,b,[(start+a,end+a) for start,end,_ in part],flush=True)
  for start,end,points in part:
   p={n+a:list(map(float,v)) for n,v in points.items()}
   rows.append(dict(start=a+start,end=a+end,points=p,cut=[a,b]))
 board=Image.new('RGB',(1000,140*max(1,len(rows))),'#22262a'); pen=ImageDraw.Draw(board)
 for i,t in enumerate(rows):
  points=t['points']; opt=list(points)
  fids=[opt[1],opt[len(opt)//4],opt[len(opt)//2],opt[len(opt)*3//4],opt[-2]]
  for j,n in enumerate(fids):
   im=Image.fromarray(frames[n]);pen2=ImageDraw.Draw(im)
   x,y,w,h,_=points[n]; pen2.rectangle((x-w/2,y-h/2,x+w/2,y+h/2),outline='#00ffff',width=3)
   board.paste(im.resize((196,110)),(200*j,140*i+21));pen.text((200*j+3,140*i+4),f'{k}.{i} F{n} x{int(x)} y{int(y)}',fill='white')
 board.save(out/(k+'_tracks.jpg'),quality=90)
 clips[k]=dict(frames=len(frames),changes=changes,tracks=rows)
 del frames
write_json(out/'locked_scan.json',clips)
