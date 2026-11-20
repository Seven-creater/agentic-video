from pathlib import Path
from itertools import groupby
from collections import defaultdict
import json,sys
from omni_story.library.dialogue_mouth_tracks import matching_pose,cycles
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes

b,task=map(Path,sys.argv[1:3])
out=task/'full_refined'
case=json.loads((b/'bundle.json').read_text())
r=json.loads((out/'outcome.json').read_text())
line=case['lines'][0]
voice=set(line['voice_frames'])
facts=json.loads((task/'scan.json').read_text())
facts.update(json.loads((task/'extra'/'scan.json').read_text()))
bounds={
 'C001P03':([1],[0,.205,.49,.695]),
 'C001P09':([1,2],[.52,.38,.99,.85]),
 'C001T14':([0],[.30,.36,.68,.74]),
 'C001P01':([0],[.444,.322,.972,.850])
}
def options():
 result={}
 for k,(ids,crop) in bounds.items():
    record=facts[k];path=record['path']
    visual=sampled_frames(out/'mouth_previews'/(k+'.mp4'),step=1)
    source=sampled_frames(out/path,step=1)
    switches=set(major_picture_changes(visual))
    tracked=[]
    for i in ids:
      points=record['tracks'][i]['points']
      vals=[]
      for f,v in points.items():
         f=int(f)
         x,y,w,_,_=v
         area=matching_pose(source[f],visual[f],(x/640,y/360),w/640,crop)
         if area is not None:
            vals.append((f,(0,0,0,0,area)))
      tracked.append(vals)
    key=record.get('source_sha256') or record['sha256']
    origin=record.get('source_start',round(record.get('source_start_s',0)*30))
    forbidden=[(origin+21,origin+35)] if k=='C001T14' else []
    starts=sorted({f for t in tracked for f,_ in t})
    result[k]=defaultdict(list)
    for size in range(12,45):
     for start in range(max(0,min(starts)-4),max(starts)+1):
        if start+size>len(visual) or switches.intersection(range(start+1,start+size)):continue
        span=(origin+start,origin+start+size)
        if any(max(a,span[0])<min(b,span[1]) for a,b in forbidden):continue
        good=set()
        for track in tracked:
           p=[(f,v) for f,v in track if start<=f<start+size]
           for _,g in groupby(enumerate(p),key=lambda z:z[1][0]-z[0]):
              for a,b in cycles([v for _,v in g],case['fps']):
                 good.update(range(a-start,b-start))
        if len(good)>=10:result[k][size].append((start,key,span,good))
    print(k,'verified tracks',[(t[0][0],t[-1][0]) for t in tracked],flush=True)
 return result

def read_choice(p):
 r=json.loads(p.read_text())
 return (r['render']['cuts'],r['score']['lines'][0]['approved'])

if __name__=='__main__':
 opts=options()
 # Allow an extra shot of the same actor when a naturally long voice run has no full witness.
 # Prefer silent boundaries and the minimum number of cuts after proving coverage.
 beams={0:[(0,[],{})]}
 for at in range(94):
    if at not in beams:continue
    for score,cuts,used in sorted(beams.pop(at),key=lambda t:t[0],reverse=True)[:75]:
      for end in range(at+12,min(94,at+44)+1):
        if (end<94 and 94-end<12) or len(cuts)>=5:continue
        attempt=[]
        for k in bounds:
          if cuts and cuts[-1]['clip']==k:continue
          for start,d,span,confirmed in opts[k][end-at]:
            confirmed=sorted(set(at+f for f in confirmed)&voice)
            if not confirmed or any(max(a,span[0])<min(b,span[1]) for a,b in used.get(d,[])):continue
            next_cut={'at':at,'in':start,'clip':k,'crop':bounds[k][1],'supported':confirmed}
            v=score+10*len(confirmed)-(2 if at else 0)-(2 if at and at in voice and at-1 in voice else 0)
            if k=='C001T14':v-=1
            attempt.append((v,cuts+[next_cut],dict(used,**{d:used.get(d,[])+[span]})))
        if attempt:
           beams[end]=sorted(beams.get(end,[])+sorted(attempt,key=lambda t:t[0],reverse=True)[:4],key=lambda t:t[0],reverse=True)[:200]
 final=[]
 for v,cuts,_ in beams.get(94,[]):
    shape=tuple((r['clip'],r['at']//5) for r in cuts)
    if any(tuple((r['clip'],r['at']//5) for r in x['cuts'])==shape for x in final):continue
    final.append(dict(cuts=cuts,score=sum(len(r['supported']) for r in cuts)))
    if len(final)==5:break
 (task/'opening_options.json').write_text(json.dumps(final))
 for o in final:print(o['score'],[(r['at'],r['clip'],r['in'],len(r['supported'])) for r in o['cuts']],flush=True)
