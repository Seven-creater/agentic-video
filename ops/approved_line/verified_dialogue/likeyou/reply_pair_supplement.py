from pathlib import Path
import json,subprocess,sys
from omni_story.library.dialogue_mouth_edit import read
from omni_story.library.selected_edit import ff,concat
bundle,task=map(Path,sys.argv[1:3])
case=read(bundle/'bundle.json')
creative=Path(case['configuration_path']).parent
inventory=read(creative/'catalog/inventory.json')['sources']
digest=case['clips']['M02']['source_sha256']
src=next(v for v in inventory if v['sha256']==digest)
fps=case['fps']
config=read(Path(case['configuration_path']))['input_lock']['configuration']
index=Path(config['material_index_root'])/'records.jsonl'
ids=('vl_6a6380cb04a32e47:e001','vl_4b0d5d69228cb0ef:e001','vl_87086e96596027c7:e000',
     'vl_aa6864a264c34dc6:e004','vl_6b36d8f81290f455:e002','vl_543f3e3f15616aea:e003',
     'vl_a2703ba02078341c:e004')
records={}
with index.open() as f:
    for line in f:
        item=json.loads(line)
        if item['record_id'] in ids and item['source_sha256']==digest: records[item['record_id']]=item
task.mkdir(exist_ok=True,parents=True)
meta={}
for i,name in enumerate(ids,1):
    r=records[name]
    key=f"R{i}"
    start,end=[round(r[f'source_{k}_s']*fps) for k in ('start','end')]
    n=end-start
    out=task/(key+'.mp4')
    ff("-ss",f"{start/fps:.6f}","-i",src['path'],"-an","-vf",
       f"fps={fps},scale=1280:720,setsar=1","-frames:v",n,
       "-c:v","libx264","-preset","veryfast","-crf",18,"-threads",2,out)
    meta[key]={"frames":n,"source_start_s":start/fps,"source_sha256":digest,"path":out.name,
               "record_id":name,"index_action":r['action'],"index_speech":r['speech']}
(task/'targets.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
