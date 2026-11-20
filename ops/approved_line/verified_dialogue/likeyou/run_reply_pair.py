from pathlib import Path
from itertools import pairwise
import argparse
import numpy as np
from omni_story.library.dialogue_mouth_edit import (
    ask, check_bundle, encoded, frame_at, load_verified_sources, read, render,
    score_output, validate_cuts,
)
from omni_story.library.dialogue_mouth_detail import sampled_frames
from omni_story.library.dialogue_scene_review import source_brief, timeline_findings
from omni_story.library.state import write_json
from omni_story.library.selected_edit import ff


def clock_stamp(frame, fps):
    cs = round(100 * frame / fps)
    secs, cs = divmod(cs, 100)
    mins, secs = divmod(secs, 60)
    return f"0:{mins:02}:{secs:02}.{cs:02}"


def major_picture_changes(pictures):
    # Triage only large changes affecting most of the frame, then inspect the actual frames.
    small = pictures[:, ::5, ::5].astype(np.int16)
    delta = np.abs(small[1:] - small[:-1]).mean(axis=(1, 2, 3))
    return (np.flatnonzero(delta >= 40) + 1).tolist()


def run(bundle, previous, evidence, output):
    output.mkdir(parents=True, exist_ok=True)
    original = read(bundle/"bundle.json")
    check_bundle(original,bundle)
    selected = original["lines"][1:3]
    start, end = selected[0]["at"], selected[-1]["end"]
    if len(selected)!=2 or selected[0]["end"] != selected[1]["at"]:
        raise ValueError("expected two adjacent on-screen lines")
    scene = next(b for b in original["blocks"] if b["at"] <= start < end <= b["end"])
    clips={k:original["clips"][k] for k in scene["clips"]}
    lines=[]
    for line in selected:
        lines.append(dict(line,at=line["at"]-start,end=line["end"]-start,
                          voice_frames=[i-start for i in line["voice_frames"]]))
    fps=original["fps"]
    media=output/"pair_sources.mp4"
    ff("-i",bundle/"scan_S1.mp4","-vf","scale=832:-2","-an","-c:v","libx264",
       "-crf",24,"-threads",2,media)
    chunk=output/"pair_dub.m4a"
    audio_filter=(
        f"aresample=48000,atrim=start_sample={start*48000//fps}:end_sample={end*48000//fps},"
        "asetpts=PTS-STARTPTS"
    )
    ff("-i",bundle/original["dub"],"-map","0:a:0","-af",audio_filter,"-vn","-c:a","aac","-b:a","192k",chunk)
    subs=output/"pair.ass"
    raw=(bundle/original["subs"]).read_text(encoding="utf-8-sig")
    header=raw.split("Dialogue:",1)[0]
    text=[]
    for line in lines:
        a,b=[clock_stamp(line[k],fps) for k in ("at","end")]
        text.append(f"Dialogue: 0,{a},{b},Default,,0,0,0,,{line['text']}")
    subs.write_text(header+"\n".join(text)+"\n",encoding="utf-8-sig")
    for clip in clips.values():
        link=output/clip["path"]
        if not link.exists():
            link.symlink_to(bundle/clip["path"])
    case=dict(original,total=end-start,lines=lines,clips=clips,dub=chunk.name,subs=subs.name,
              blocks=[{"at":0,"end":end-start,"clips":list(clips)}])
    originals=load_verified_sources(original,evidence)
    observed={k:originals[k] for k in clips}
    context=read(previous/"scene_sources.json")
    brief=source_brief(case,observed,context)
    discontinuities={k:major_picture_changes(sampled_frames(output/v["path"],step=1)) for k,v in clips.items()}
    targets=[{k:v for k,v in line.items() if k!="voice_frames"} for line in lines]
    prompt=(
        "你要剪一个极短的对话，只剪下方相邻的两句。视频里按素材编号和源帧逐段标了原画。"
        "按每句指定演员，第一句整个说完才能切第二句。每句只用一个连续画面，必须从头到尾看清本人，"
        "不让另一张嘴抢对白。用合适的裁框只留下说话者，裁不掉第二张会动的嘴就换素材。不要用吃东西代替说话。"
        "在没有充足的嘴动素材时，交最接近的试剪并在gap说明看见什么和还缺什么。"
        "素材内有突然换镜的可能位置，所用整句要在同一侧。每句in为源入帧，长度等于该句end减at。"
        "crop是归一化[左,上,右,下]，宽度和高度的归一化比例跨度相同，整个画幅可省略这个字段。"
        '只返回一个JSON对象 {"lines":[{"id":"l2","clip":"编号","in":源帧整数,"gap":"说明或空字符串"},'
        '{"id":"l3","clip":"编号","in":源帧整数,"gap":"说明或空字符串"}]}。'
        +encoded({"lines":targets,"actors":case["actors"],"evidence":brief,
                  "lengths":{k:v["frames"] for k,v in clips.items()},"possible_internal_cuts":discontinuities})
    )
    def cuts_for(reply):
        rows=reply["lines"]
        if [row["id"] for row in rows]!=[l["id"] for l in lines]:
            raise ValueError("必须按上面顺序给两句，第一句兔子，第二句白圆耳")
        cuts=[]
        for row,line in zip(rows,lines):
            cut={k:row[k] for k in ("clip","in")}
            if not isinstance(row["gap"],str):
                raise ValueError("缺口填说明文字或空字符串")
            if "crop" in row:cut["crop"]=row["crop"]
            cuts.append(dict(cut,at=line["at"]))
        validate_cuts(cuts,case)
        for cut,line in zip(cuts,lines):
            source_stop=cut["in"]+line["end"]-line["at"]
            internal=[n for n in discontinuities[cut["clip"]] if cut["in"]<n<source_stop]
            if internal:
                raise ValueError(f"这句 {line['id']} 跨过了素材 {cut['clip']} 的大幅换画面帧 {internal}，改入点或换镜头")
        return cuts
    def valid(reply):
        cuts_for(reply)
        return reply
    result=ask(case,output,"glmsel_reply_pair",prompt,media,valid)
    cuts=cuts_for(result)
    write_json(output/"decision.json",result)
    filmed=render(case,output,output,cuts,"qa_pair")
    original_source=previous/"draft.mp4"
    ff("-i",original_source,"-i",chunk,"-map","0:v:0","-map","1:a:0","-vf",
       f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS",
       "-c:v","libx264","-c:a","copy","-threads",2,output/"old_pair.mp4")
    issues=timeline_findings(case,cuts,case["blocks"],brief)
    score=score_output(case,output,output,filmed,cuts,None,observed,"qa_pair",issues)
    old_cuts=[]
    source_cuts=read(previous/"draft_render.json")["cuts"]
    frame_map=[frame_at(source_cuts,n) for n in range(start,end)]
    for n,(key,f,crop) in enumerate(frame_map):
        if n==0 or frame_map[n-1]!=(key,f-1,crop):
            old_cuts.append(dict(clip=key,crop=crop,at=n,**{"in":f}))
    old_issues=timeline_findings(case,old_cuts,case["blocks"],brief)
    baseline=score_output(case,output,output,output/"old_pair.mp4",old_cuts,None,observed,"old_pair",old_issues)
    def jumps(output_file,cuts):
        return [{"at":n,"same_edit_shot":frame_at(cuts,n)[:1]==frame_at(cuts,n-1)[:1]
                 and frame_at(cuts,n)[1]==frame_at(cuts,n-1)[1]+1}
                for n in major_picture_changes(sampled_frames(output_file,step=1))]
    write_json(output/"comparison.json",{
        "source_window":[start,end],"fps":fps,"lines":lines,"input_sha256":{k:v["sha256"] for k,v in clips.items()},
        "cuts":cuts,"old_cuts":old_cuts,"before":baseline,"after":score,
        "new_issues":issues,"old_issues":old_issues,"source_large_changes":discontinuities,
        "old_picture_jumps":jumps(output/"old_pair.mp4",old_cuts),
        "new_picture_jumps":jumps(filmed,cuts),
    })

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for k in ("bundle","previous","evidence","output"):p.add_argument(k,type=Path)
    args=p.parse_args()
    run(args.bundle,args.previous,args.evidence,args.output)
