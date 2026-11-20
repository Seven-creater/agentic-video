from pathlib import Path
import sys
from omni_story.library.dialogue_mouth_edit import read,render,score_output,validate_cuts
from omni_story.library.dialogue_mouth_detail import sampled_frames,major_picture_changes
from omni_story.library.state import write_json
base,first,task=map(Path,sys.argv[1:4])
source=read(base/'bundle.json')
pair=read(first/'comparison.json')
prior=read(task/'recheck.json')
cuts=prior['cuts']
rabbit,white=[r['clip'] for r in cuts]
assert rabbit=='R7' and cuts[0]['in']==17 and cuts[1]==pair['cuts'][1]
clip=prior['source_record']
# R7 source frames 17–21 have no burned text. The two lines at F22–35
# occupy x270–360 and y307–356 at 640x360, below the lip contour.
mask={"x":270/640,"y":307/360,"w":90/640,"h":49/360,"opacity":.90,
      "block_px":15,"start_s":22/30,"end_s":36/30}
clips={rabbit:dict(clip,sha256=prior['selected_media_sha256'],masks=[mask]),white:source['clips'][white]}
case=dict(source,total=pair['source_window'][1]-pair['source_window'][0],clips=clips,
          lines=pair['lines'],dub='pair_dub.m4a',subs='pair.ass')
validate_cuts(cuts,case)
film=render(case,task,task,cuts,'supplemented_pair_clean')
observed={rabbit:{"method":"contour_cycle","slots":read(task/'candidate_mouths.json')[rabbit]},
          white:{"method":"contour_cycle","slots":[]}}
checked=score_output(case,task,task,film,cuts,None,observed,'supplemented_pair_clean')
jumps=major_picture_changes(sampled_frames(film,step=1))
ours=read(task/'supplemented_pair_clean_render.json')
assert ours['audio']==read(first/'qa_pair_render.json')['audio']
write_json(task/'final_recheck.json',dict(prior,final=checked,mask=mask,jumps=jumps,
                                         render=ours,draft_rabbit_scoring=prior['mouth_scoring']['lines'][0]))
print(jumps,[(l['id'],l['approved'],l['voiced']) for l in checked['lines']],ours['audio'])
