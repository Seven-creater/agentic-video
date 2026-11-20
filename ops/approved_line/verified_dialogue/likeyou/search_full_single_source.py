from pathlib import Path
import sys,json,re
bundle,out=map(Path,sys.argv[1:3])
out.mkdir(parents=True,exist_ok=True)
case=json.loads((bundle/'bundle.json').read_text())
cfg=json.loads(Path(case['configuration_path']).read_text())['input_lock']['configuration']
root=Path(cfg['material_index_root'])
digest=case['clips']['M02']['source_sha256']
aliases={"C001":"角色_3ef4eeccae","C003":"角色_a938151335","C007":"角色_b4ed0efef4"}
strong=('口部开合','嘴部开合','嘴形开合','嘴型开合','张合','开合','主动说话','正在说话','连续说话','疑似说话','交谈','回应','自言自语','台词','对话')
indexed={k:[] for k in aliases}
for row in (root/'records.jsonl').open():
 r=json.loads(row)
 length=r['source_end_s']-r['source_start_s']
 if r['kind']!='event' or r['source_sha256']!=digest or length<.67:continue
 a=r['action']; sp=r['speech']; head=a.split('；')[0]; place=r['place']; cam=r['camera']
 for actor,nick in aliases.items():
  if not a.startswith(nick) or not any(k in a+sp for k in strong):continue
  if any(k in head for k in ('吃','饮','喝','咬','咀嚼','吞','哭','哈欠','尖叫','跑','打闹','狂笑')):
   continue
  points=0
  points+=5*any(k in head for k in ('说','开合','交谈','回应','张合','念'))
  points+=3*any(k in sp for k in ('反复开合','持续说话','明显开合','张合','清晰开合','清楚开合','开合'))
  points+=3*any(k in place for k in ('草地','草坪','户外','野餐','树林'))
  points+=2*any(k in cam or k in a for k in ('特写','近景','面部近景','单人'))
  points+=min(5,length)
  points-=5*any(k in sp for k in ('不宜配','不可配','无明确说话','并非说话','为进食'))
  points-=3*any(k in a for k in ('走动','跑向','跳跃','三人同框','吼叫','惊叫'))
  if not any(k in head for k in ('说','张嘴','开合','交谈','回应','张开','发声','张口')):
   points-=4
  indexed[actor].append((points,r))
selected={}
for actor,items in indexed.items():
 picks=[]
 for val,r in sorted(items,key=lambda x:(-x[0],x[1]['source_start_s'])):
  if val<3:break
  a=r['source_start_s'];b=r['source_end_s']
  if any(max(x['source_start_s']-2,a)<min(x['source_end_s']+2,b) for x in picks):
   continue
  picks.append(dict(r,rank=val))
  if len(picks)>=23:break
 selected[actor]=picks
(out/'index_shortlist.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2)+'\n')
for k,v in selected.items():
 print(k,len(v))
 for i,r in enumerate(v,1):
  print(i,r['source_start_s'],r['source_end_s'],r['rank'],r['action'][:80],r['place'][:32],r['speech'][:43])
