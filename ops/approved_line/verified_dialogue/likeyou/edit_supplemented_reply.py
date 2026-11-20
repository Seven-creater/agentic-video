from pathlib import Path
import argparse
from omni_story.library.dialogue_mouth_edit import ask, encoded, read, render, score_output, validate_cuts
from omni_story.library.dialogue_mouth_detail import sampled_frames
from omni_story.library.dialogue_mouth_tracks import candidates, face_support, witness_frames
from omni_story.library.media import sha256_file
from omni_story.library.selected_edit import ff, concat
from omni_story.library.state import write_json


def link(source, dest):
    if not dest.exists():
        dest.symlink_to(source)


def run(bundle, pair, task):
    base = read(bundle/"bundle.json")
    clock = read(pair/"comparison.json")
    staged = read(task/"targets.json")
    prior = read(pair/"qa_pair_render.json")
    lines = clock["lines"]
    needed = lines[0]["end"]-lines[0]["at"]
    fps = base["fps"]
    shortlist = ("R2","R3","R7")
    checks = {}
    pieces = []
    for clip in shortlist:
        raw = task/(clip+".mp4")
        samples = sampled_frames(raw,step=1)
        usable = []
        for a,b,points in candidates(samples,fps):
            landmarks = witness_frames(points)
            if b-a>=needed and sum(face_support(samples[n],points[n]) for n in landmarks)*2>len(landmarks):
                usable.append({
                    "actor": lines[0]["actor"], "start": a, "end": b, "witness": landmarks,
                    "evidence": "源片逐帧检测到连续口部开合，嘴上方两侧脸部特征均支持",
                    "track_frames": list(points),
                    "center": [[v[0]/640,v[1]/360] for v in points.values()],
                    "widths": [v[2]/640 for v in points.values()],
                })
        checks[clip]=usable
        dest=task/(clip+"_read.mp4")
        ff("-i",raw,"-an","-vf",
           f"scale=832:468,pad=iw:ih+30:0:30,drawtext=text='{clip} SOURCE F%{{n}}':x=5:y=2:fontsize=22:fontcolor=white",
           "-c:v","libx264","-preset","veryfast","-crf",23,"-threads",2,dest)
        pieces.append(dest)
    movie=concat(task,pieces,"listen_candidates")
    summary={k:dict(frames=staged[k]["frames"],visible_mouth=[{"start":s["start"],"end":s["end"],"witness":s["witness"]} for s in checks[k]]) for k in shortlist}
    write_json(task/"candidate_mouths.json",checks)
    prompt=(
        "只补一刀：黄色长耳兔说‘立刻有。’共19帧，下一刀白色圆耳追问已锁定。"
        "候选视频上方有素材号和源帧；请自己定19帧入点和裁切，不要改句长和配音。"
        "全程嘴无食物遮挡、清楚开合，避免把相邻蓝头角色或它的嘴剪进来。"
        "visible_mouth是口部轮廓已检测到开合的源范围，仍需你看实际动作，眼睛变化/旧字幕/吃饭都不算。"
        "有确认范围的镜头优先用，固定笑脸不算说话。crop宽高归一化跨度必须相同，整幅可以省略。"
        '返回一个JSON对象 {"clip":"R编号","in":源入帧,"why":"真实看到的开合、入点及取舍"}。'
        +encoded(summary)
    )
    known={k:v for k,v in staged.items() if k in shortlist}
    frozen_white=prior["cuts"][1]
    clips={k:dict(v,sha256=sha256_file(task/v["path"]),masks=[
        # The visible Japanese/Chinese titles occupy the bottom centre of these archive excerpts.
        {"x":0.28,"y":0.80,"w":0.44,"h":0.20,"opacity":0.90,"block_px":15,
         "start_s":0,"end_s":v["frames"]/fps}
    ]) for k,v in known.items()}
    white_key=frozen_white["clip"]
    clips[white_key]=base["clips"][white_key]
    for f in ("pair_dub.m4a","pair.ass"):
        link(pair/f,task/f)
    link(bundle/base["clips"][white_key]["path"],task/base["clips"][white_key]["path"])
    case=dict(base,clips=clips,total=clock["source_window"][1]-clock["source_window"][0],
              lines=lines,dub="pair_dub.m4a",subs="pair.ass",
              blocks=[{"at":0,"end":lines[-1]["end"],"clips":list(clips)}])
    def material(v):
        return {k:v[k] for k in ("clip","in","crop") if k in v}
    def validate(v):
        k=v["clip"]
        if k not in shortlist or type(v["in"]) is not int or not v["why"]:
            raise ValueError("使用候选和整数源帧，写看到的动作")
        if not any(s["start"] <= v["in"] and v["in"]+needed <= s["end"] for s in checks[k]):
            raise ValueError("19帧必须留在已检测到开合的源范围，源出点不含")
        validate_cuts([dict(material(v),at=0),frozen_white],case)
        return v
    chosen=ask(case,task,"glmsel_rabbit_replacement",prompt,movie,validate)
    write_json(task/"selection.json",chosen)
    cuts=[dict(material(chosen),at=0),frozen_white]
    result=render(case,task,task,cuts,"supplemented_pair")
    old_evidence={key:{"method":"contour_cycle","slots":slots} for key,slots in checks.items()}
    old_evidence[white_key]={"method":"contour_cycle","slots":[]}
    score=score_output(case,task,task,result,cuts,None,old_evidence,"supplemented_pair")
    write_json(task/"recheck.json",{
        "fixed_white_cut":frozen_white,"cuts":cuts,"mouth_scoring":score,"auto_candidate_notes":summary,
        "source_record":known[chosen["clip"]],"selected_media_sha256":sha256_file(task/clips[chosen["clip"]]["path"]),
        "selected":chosen,"rubric":"兔子只用嘴动候选，白圆耳沿用上一版；裁框和遮罩应在实际画面复看。"
    })


if __name__=="__main__":
    p=argparse.ArgumentParser()
    for k in ("bundle","pair","task"):p.add_argument(k,type=Path)
    a=p.parse_args()
    run(a.bundle,a.pair,a.task)
