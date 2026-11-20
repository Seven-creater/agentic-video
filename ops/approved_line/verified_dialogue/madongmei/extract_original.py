
from pathlib import Path
import json,sys,subprocess
from PIL import Image,ImageDraw
import numpy as np
c,job=map(Path,sys.argv[1:]); out=job/'scanned';out.mkdir(parents=True,exist_ok=True)
w=json.loads((c/'watched_windows.json').read_text())
source=w[0]['source_path']
board=Image.new("RGB",(1200,138*5),"#232b30");pen=ImageDraw.Draw(board)
for j,start in enumerate((1738,1753)):
 f=out/f'W{j+1}.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-threads','1','-ss',str(start),'-i',source,'-an','-vf','fps=30,scale=1280:720,setsar=1','-t','17','-threads','2','-c:v','libx264','-crf','16',f],check=True)
 for t in range(15):
  raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t+.4),'-i',f,'-frames:v','1','-vf','scale=200:112','-pix_fmt','rgb24','-f','rawvideo','-'])
  i=15*j+t; x=200*(i%6); y=138*(i//6)
  board.paste(Image.frombytes('RGB',(200,112),raw),(x,y+22))
  pen.text((x+3,y+4),f'W{j+1} +{t}.4  ({start+t}.4)',fill='white')
board.save(out/'source_grid.jpg',quality=92)
