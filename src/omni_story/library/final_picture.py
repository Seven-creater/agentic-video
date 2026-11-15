"""Caption-free, silent picture facts, observed without the intended story."""
import json
import math
from pathlib import Path
import subprocess

from .media import probe_media, sha256_file
from .state import LibraryStopped, write_json


def observe_picture(path, root, reviewer):
    path, root = Path(path), Path(root)
    root.mkdir(parents=True, exist_ok=True)
    duration=probe_media(path)['duration_s']
    proxy=root/'silent_picture.mp4'; receipt=root/'picture_binding.json'
    spec=dict(source_sha256=sha256_file(path),policy='caption_free_silent_picture_v1')
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved['spec']!=spec or sha256_file(proxy)!=saved['sha256']:
            raise LibraryStopped('final_picture_changed')
    else:
        temporary=root/'silent_picture.part.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-n','-i',str(path),
            '-f','lavfi','-i','anullsrc=r=16000:cl=mono','-map','0:v:0','-map','1:a:0',
            '-t',str(duration),'-vf','scale=640:-2,fps=4','-c:v','libx264','-preset','fast',
            '-crf','25','-c:a','aac','-b:a','16k','-movflags','+faststart',str(temporary)],check=True)
        if abs(probe_media(temporary)['duration_s']-duration)>.26:
            raise LibraryStopped('final_picture_clock_changed')
        temporary.replace(proxy)
        write_json(receipt,dict(spec=spec,sha256=sha256_file(proxy)))
    received=reviewer(proxy,root/'observation',prompt=(
        '独立观看实际剪辑后的无声画面。音轨是人工静音，无参考、角色职业或剧本。'
        '只记录实际可见的人物外观、交互方向、物件、动作、机位和衔接。'
        '马赛克中的物件、原字幕和看不清的动作一律不可推测；不要猜买卖等职业或因果。'
        f'按有变化的连续区间记录，最多24项，不必逐帧。时间为本片秒，实际片长{duration:.6f}秒，不要超出。'
        '返回JSON {"facts":[{"start_s":0,"end_s":1,"observation":"可见事实"}],'
        '"limitations":["无法观察的事项"]}。'))
    value=received.get('parsed')
    if received.get('http_status')!=200 or received.get('parse_error') or not isinstance(value,dict):
        raise LibraryStopped('final_picture_observation_invalid')
    facts=value.get('facts')
    if (not isinstance(facts,list) or not 1<=len(facts)<=24 or
        any(not isinstance(r,dict) or not isinstance(r.get('observation'),str) or not r['observation'].strip()
            or any(type(r.get(k)) not in (int,float) or not math.isfinite(r[k]) for k in ('start_s','end_s'))
            or not (0<=r['start_s']<min(r['end_s'],duration) and r['end_s']<=duration+1) for r in facts)):
        raise LibraryStopped('final_picture_facts_invalid')
    return dict(policy='caption_free_silent_picture_v1',source_sha256=spec['source_sha256'],
        facts=[dict(r,fact_id=f'picture_{i+1}',reported_end_s=r['end_s'],end_s=min(r['end_s'],duration))
            for i,r in enumerate(facts)],
        limitations=value.get('limitations',[]))


def ground_visual_notes(verdict, picture):
    """The AV judge selects observed facts; it cannot write new picture facts."""
    selected=verdict.get('visual_notes'); facts={r['fact_id']:r for r in picture['facts']}
    passed=(isinstance(selected,list) and bool(selected) and
        all(isinstance(key,str) and key in facts for key in selected))
    verdict['picture_grounding']=dict(passed=passed,observation=picture,
        selected_fact_ids=selected,policy='observed_picture_facts_only_v1')
    if passed:
        verdict['visual_notes']=[facts[key]['observation'] for key in selected]
    else:
        verdict.update(picture_audio_coherence='unverifiable',repair_at='selection')
        verdict.setdefault('issues',[]).append('终审返回未被独立静音画面支持的视觉事实，不能确认声画一致')
