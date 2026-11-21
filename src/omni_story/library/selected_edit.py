"""Three-stage GLM picture edit over a delivered, immutable dialogue master."""
import argparse
import json
import math
import os
import re
from pathlib import Path
import subprocess

from omni_story.library.dialogue_finish import verified_masks
from omni_story.library.media import sha256_file
from omni_story.library.edit_review import adjudicate_repairs, audit_render, apply_cut_replacements
from omni_story.library.render import _probe, mosaic_layers
from omni_story.library.opencode_provider import OpenCodeMCP
from omni_story.library.pipeline import _material_call
from omni_story.library.server_cli import _home, credential, settings
from omni_story.library.state import LibraryState, LibraryStopped, write_json

FPS = 30


def read(path):
    return json.loads(path.read_text())


def ff(*args):
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-filter_threads', '1', *map(str, args)], check=True)


def make_reel(out, shots, sources):
    files = []
    for key, shot in shots.items():
        path = out / (key + '.mp4')
        label = f"drawtext=text={key}:fontsize=32:fontcolor=white:box=1:boxcolor=black:x=8:y=8"
        ff('-ss', shot['source_start_s'], '-i', sources[shot['source_sha256']], '-t',
           shot['source_end_s']-shot['source_start_s'], '-an', '-vf',
           'fps=12,scale=640:360,'+label, '-c:v','libx264','-threads',2,path)
        files.append(path)
    return concat(out, files, 'selected_reel')


def concat(out, files, name):
    listing = out / (name+'.ffconcat')
    listing.write_text(''.join("file '"+p.name+"'\n" for p in files))
    dest = out / (name+'.mp4')
    ff('-f','concat','-safe',1,'-i',listing,'-c','copy',dest)
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('output', type=Path, help='New run directory under baseline/evaluations; reuse it to resume.')
    parser.add_argument('--skill', type=Path, default=Path(__file__).with_name('resources')/'edit-selected-footage'/'SKILL.md')
    parser.add_argument('--independent-brief', help='Start from the locked audio and sources without showing any previous cut. Supply the editorial brief here.')
    parser.add_argument('--automatic-review',action='store_true',help='Find local picture defects on the rendered film and independently check the model repairs.')
    parser.add_argument('--dialogue-handoffs', action='store_true', help='Cut to the incoming speaker at a change of voice. Shots within a turn are free.')
    parser.add_argument('--edit-notes',type=Path,help='Optional JSON with notes keyed by existing line IDs')
    parser.add_argument('--reuse-source-read',type=Path,help='Prior run with verified independent source observations')
    args=parser.parse_args()
    if args.independent_brief and (args.edit_notes or args.reuse_source_read):
        parser.error('independent editing starts with fresh footage observations and the brief only')
    parent,out=args.baseline.resolve(),args.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    result=read(parent/'result.json')
    lock=read(parent/'material_lock.json')
    original=Path(result['final_video'])
    if sha256_file(original)!=result['final_sha256']:
        raise ValueError('baseline changed')
    inventory=next(p/'catalog/inventory.json' for p in (parent,*parent.parents) if (p/'catalog/inventory.json').is_file())
    sources={s['sha256']:s['path'] for s in read(inventory)['sources']}
    shots={}; scene_keys={}; cast={}
    for scene in lock['scenes']:
        sid=scene['scene_id']
        scene_keys[sid]=[]
        cast[sid]={r:{k:b.get(k) for k in ('role_id','appearance')} for r,b in scene['role_bindings'].items()}
        for shot in scene['coverage_shots']:
            key='S'+str(len(shots)+1);shots[key]=shot;scene_keys[sid].append(key)
    for digest in {s['source_sha256'] for s in shots.values()}:
        if sha256_file(sources[digest])!=digest:raise ValueError('locked source changed: '+digest)
    source_info={k:dict(scene=next(s for s,keys in scene_keys.items() if k in keys),
                        seconds=round(s['source_end_s']-s['source_start_s'],3),action=s['action'],
                        actors=s['visible'],speaking=s['speaking'],dub=s.get('dub_speaker_ids',[]),
                        repeatable=s['loopable']) for k,s in shots.items()}
    lines=[{k:l[k] for k in ('scene_id','line_id','role','text','start_s','end_s')} for l in result['lines']]
    targets=list(lines)
    spoken_scenes={l['scene_id'] for l in lines}
    for sid in scene_keys:
        if sid in spoken_scenes:continue
        rows=[r for r in result['timeline'] if r['scene_id']==sid]
        if rows:
            targets.append(dict(line_id='visual_'+sid,scene_id=sid,role=None,text='无对白场景',
                                start_s=rows[0]['start_s'],end_s=rows[-1]['end_s']))
    targets.sort(key=lambda r:r['start_s'])
    notes=read(args.edit_notes) if args.edit_notes else {}
    if not isinstance(notes,dict) or any(k not in {l['line_id'] for l in targets} or not isinstance(v,str) or not v.strip() for k,v in notes.items()):
        raise ValueError('edit notes must name existing targets')
    skill=args.skill.read_text().split('需要对照真实切点')[0]
    config=read(parent/'library_state.json')['input_lock']['configuration']
    from .scene_memory import locked_hints
    speakers = {
        key: {cast[sid][line['role']]['role_id'] for line in lines
              if line['scene_id'] == sid and line['role'] in cast[sid]}
        for sid, keys in scene_keys.items() for key in keys
    }
    for key, spans in locked_hints(config.get('visual_library_root'), shots, speakers, FPS, out/'locked_mouth_hints.json').items():
        source_info[key]['过往动嘴线索（片内帧，出点不含，须看片核实）'] = spans

    home=_home(); server=settings(home); os.environ['Z_AI_API_KEY']=credential(home)
    state=LibraryState(out,dict(configuration=config,baseline=result['final_sha256'],
            edit_policy='automatic_paired_picture_review_v2' if args.automatic_review else 'visual_evidence_and_blind_review_v1',
            **({'dialogue_handoffs':True} if args.dialogue_handoffs else {}),
            **({'independent_brief':args.independent_brief} if args.independent_brief else {}),
            **({'edit_notes':notes} if notes else {}),
            material=sha256_file(parent/'material_lock.json'),skill=sha256_file(args.skill)),
            max_requests=None,registry_path=out.parent/'selected_edit_runs.json')
    glm=OpenCodeMCP(state,package_root=server['mcp_package_root'],executable=server['opencode_executable'])
    def ask(stage,prompt,validate,asset):
        cache=out/(stage+'.json')
        receipt=out/(stage+'_binding.json')
        from hashlib import sha256
        binding=dict(prompt=sha256(prompt.encode()).hexdigest(),media=sha256_file(asset))
        if cache.exists() and receipt.exists():
            if read(receipt)!=dict(**binding,response=sha256_file(cache)):raise ValueError('stage input or response changed: '+stage)
            cached=read(cache); validate(cached); return cached
        if stage=='source_read' and args.reuse_source_read:
            prior=args.reuse_source_read.resolve()
            response=prior/cache.name; record=read(prior/receipt.name)
            if record!=dict(**binding,response=sha256_file(response)):
                raise ValueError('prior source observations differ in image, prompt, or receipt')
            value=validate(read(response))
            write_json(cache,value)
            write_json(receipt,dict(**binding,response=sha256_file(cache)))
            write_json(out/'source_read_reused.json',dict(source=str(response),sha256=record['response']))
            return value
        recovered=None
        if stage in ('plan','review','final_review','refine'):
            recovered=received_timeline(out,stage,prompt,binding['media'],validate)
        if recovered is not None:
            value,call_id=recovered
            write_json(out/(stage+'_replay.json'),dict(call_id=call_id,validated=True))
        else:
            value=_material_call(state,glm,'material_scene_selected_edit_'+stage,prompt,validate,str(asset),image=asset.suffix.lower() in ('.jpg','.png'))
        write_json(cache,value)
        write_json(receipt,dict(**binding,response=sha256_file(cache)))
        return value
    reel=out/'selected_reel.mp4'
    if not reel.exists():make_reel(out,shots,sources)
    reference=[{k:s.get(k) for k in ('scene_id','interaction','visual_function','must_see','picture')} for s in lock['scenes']]
    contract=json.dumps(dict(actors=cast,sources=source_info,locked_audio=lines,
        **({'visual_targets':[l for l in targets if l['role'] is None]} if len(targets)>len(lines) else {}),
        reference=reference),ensure_ascii=False)
    permitted=allowed_shots(shots,scene_keys,cast)
    board=shot_board(out,shots)
    observed=ask('source_read','这是编号镜头的开头、中段和末段实际画面。逐镜独立描述肉眼能确认的景别、主体、手部、姿势、表情和背景图形。'
        '尤其注意和相邻画面差异很大的近景、放射线、汗滴或突然的姿势；哪些像强烈反应，哪些只是平静等待？'
        '没有剧本，不能杜撰事件。返回 {"shots":{"编号":"具体看到什么，静帧不明的动作如实说明"}}。编号为'
        +json.dumps(list(shots)),lambda v:check_observation(v,shots),board)
    blocked = dialogue_walkaways(shots, permitted, lines) if args.dialogue_handoffs else {}
    if blocked:
        blocked.update(review_movement_context(out,shots,sources,permitted,blocked,ask))
    permitted = continuity_options(permitted, blocked)
    usable={k for keys in permitted.values() for k in keys}
    scoped=concat(out,[out/(k+'.mp4') for k in shots if k in usable],'permitted_reel')
    observation={k:v for k,v in observed['shots'].items() if k in usable}
    material=json.loads(contract)
    if args.independent_brief and not args.automatic_review:
        material.pop('reference',None)
    if args.independent_brief:
        material['editorial_brief']=args.independent_brief
    material['sources']={k:v for k,v in source_info.items() if k in usable}
    material['per_line_options']={row['line_id']:[k for k in permitted[row['scene_id']] if not row['role'] or
        not set(shots[k]['speaking'])-{cast[row['scene_id']][row['role']]['role_id']}] for row in targets}
    if args.dialogue_handoffs:
        material['handoffs'] = dialogue_handoffs(lines)
    if notes:material['editorial_feedback']=notes
    contract=json.dumps(material,ensure_ascii=False)
    def beat_check(v):
        motion=v.get('source_motion',{}) if args.automatic_review else {}
        continuity_options(permitted,motion)
        check_beats(v,targets,shots,continuity_options(permitted,{**v.get('exclude',{}),**motion}),cast)
        missing=set(notes)-{b['line'] for b in v['anchors']}
        if missing:raise ValueError('include all editorial review targets: '+str(sorted(missing)))
        return v
    beats=ask('beats_scoped','本次只展示允许使用的镜头。现在看这些镜头的连续动作。结合独立读图与演员许可，为剧本选少量真正需要靠画面看懂的关键变化。'
              '指着东西说话优先能看见伸手指物的镜头；重复一句需要看出语气或景别的变化；把显著的变脸、冲击背景等留给真正触发它的那一刻。'
              '要覆盖引发反应的提问与真正爆发的回答，强表情优先留给爆发的那一方；区分同一句重复两遍的远近。不要逐句占用镜头。若揭示或结尾只有台词能交代，必须保留该条记录，缺少动作或物证的关键句明确填null；可以用反应交代态度，但不能冒充道具出现。'
              '对同一场仍在原地发生的对话，核对问、答和中间插镜。若某整段动作明显属于另一时刻且本场无处可用，在exclude按镜头编号写实际可见的违和原因；正常走动或符合剧情的离场仍可使用。无须排除时给空对象。'
              '返回 {"exclude":{},"anchors":[{"line":"line_id","shot":"S编号或null","observation":"实际可见证据或缺项",'
              '"reserve":false}]}。同一句只列一次。reserve=true表示这个镜头不能再指定给其他句。正常出镜镜头可共用。'
              +'专门保留只用于不可提前消耗的反应或揭示；不要把能重复承担普通发言的角色正脸专供开头。'
              +('先根据台词顺序明确谁询问、谁应答、哪一句开始针对对方发怒。只标出有必要靠画面表达的转折。'
                '每条非空锚点只能从该行per_line_options里选。强反应归给实际爆发的人说出那句话时。'
                '保留普通问句的可用镜头，不要提前消耗强反应。' if args.dialogue_handoffs else '')
              +('先按参考表中每场的意图判断是留在原地交谈、边走边谈还是主动离场。仅当走动会破坏本场时，'
                '把实际看出走路或跟拍的编号和可见依据写在source_motion对象中，这些镜头应全场排除。'
                '动作符合剧情时保留，source_motion填空。不要猜范围。' if args.automatic_review else '')
              +'这次逐句反馈，所有指定句都要回应并写入anchors：'+json.dumps(notes,ensure_ascii=False)+'。允许编号'+json.dumps(permitted,ensure_ascii=False)+'独立读图'+json.dumps(observation,ensure_ascii=False)+contract,
              beat_check,scoped)
    if args.automatic_review and beats.get('source_motion'):
        motion=beats['source_motion']
        blocked.update(motion)
        blocked.update(review_movement_context(out,shots,sources,permitted,blocked,ask))
        # Source continuity evidence takes precedence over a provisional anchor.
        for anchor in beats['anchors']:
            if anchor['shot'] in blocked:
                anchor.update(shot=None,reserve=False,observation=blocked[anchor['shot']])
    run_edit(out,parent,result,original,shots,sources,lines,scene_keys,skill,contract+json.dumps(observation,ensure_ascii=False),beats,ask,targets=targets,notes=notes,independent_brief=args.independent_brief,turn_locked=args.dialogue_handoffs,blocked=blocked,automatic_reference=reference if args.automatic_review else None)


def shot_board(out,shots):
    import io
    from PIL import Image,ImageDraw
    # Three phases keep subsecond reactions legible even when the video service samples sparsely.
    cols=2
    sheet=Image.new('RGB',(cols*600,((len(shots)+cols-1)//cols)*138),'#17212b')
    draw=ImageDraw.Draw(sheet)
    for i,(key,shot) in enumerate(shots.items()):
        start=(i%cols)*600;top=(i//cols)*138
        draw.text((start+3,top+2),key,fill='white')
        duration=shot['source_end_s']-shot['source_start_s']
        for j,fraction in enumerate((.12,.5,.85)):
            when=max(0,min(duration*fraction,duration-.12))
            image=subprocess.check_output(['ffmpeg','-v','error','-i',str(out/(key+'.mp4')),'-ss',str(when),
                '-frames:v','1','-vf','scale=200:112','-vcodec','mjpeg','-f','image2pipe','pipe:1'])
            sheet.paste(Image.open(io.BytesIO(image)),(start+200*j,top+20))
    dest=out/'shot_board.jpg';sheet.save(dest,quality=91)
    return dest


def check_observation(value,shots):
    rows=value.get('shots',{})
    if not isinstance(rows,dict) or set(rows)!=set(shots) or any(not isinstance(t,str) or not t.strip() for t in rows.values()):
        raise ValueError('describe all labelled shots using these keys: '+str(list(shots)))
    return value


def check_beats(value,lines,shots,permitted,cast):
    anchors=value.get('anchors')
    if not isinstance(anchors,list) or not anchors:raise ValueError('give the decisive beats')
    by_line={l['line_id']:l for l in lines}
    seen=set(); assignments={}
    for beat in anchors:
        lid=beat.get('line')
        if lid not in by_line or lid in seen:raise ValueError('give each selected line ID once: '+str(lid))
        seen.add(lid);key=beat.get('shot'); line=by_line[lid];sid=line['scene_id']
        if not beat.get('observation') or (key is not None and key not in permitted[sid]):
            raise ValueError(f'{lid}: {key} cannot serve as a verified picture in {sid}; use one of {permitted[sid]} or null with an explanation')
        if type(beat.get('reserve')) is not bool:raise ValueError('reserve must be true or false')
        if key:
            assignments.setdefault(key,[]).append(beat)
            if line.get('role') and set(shots[key]['speaking'])-{cast[sid][line['role']]['role_id']}:
                raise ValueError(lid+' cannot use another speaking performer: '+key)
    for key,group in assignments.items():
        if len(group)>1 and any(b['reserve'] for b in group):
            raise ValueError(key+' cannot be reserved AND reused. Remove competing assignments or mark all as shared.')
    return value


def received_timeline(out,stage,prompt,media_digest,validate):
    from omni_story.library.contracts import parse_model_json
    from omni_story.library.state import json_sha
    calls=read(out/'library_state.json')['calls']
    key='material_scene_selected_edit_'+stage
    chain=[c for c in calls if c['name'] in (key,key+'_repair',key+'_repair2')]
    if not chain or any(c['status']!='received' for c in chain):return None
    folder=out/'calls'/chain[-1]['id']
    if not (folder/'protocol_failure.json').exists():return None
    request=read(out/'calls'/chain[0]['id']/'request.json')
    if json_sha(request)!=chain[0]['request_sha256'] or request['arguments']['prompt']!=prompt or request['media_sha256']!=media_digest:
        raise ValueError('received timeline request does not match the current input')
    response=read(folder/'response.json')
    if json_sha(response)!=chain[-1]['response_sha256']:raise ValueError('received timeline receipt changed')
    raw='\n'.join(item['text'] for item in response['result']['content'] if item['type']=='text')
    return validate(parse_model_json(raw)),chain[-1]['id']


def allowed_shots(shots,scene_keys,cast):
    permitted={}
    for sid,keys in scene_keys.items():
        bound={b['role_id'] for b in cast[sid].values()}
        permitted[sid] = []
        for key in keys:
            shot = shots[key]
            if not bound or bound.intersection(shot['visible']):
                permitted[sid].append(key)
            elif not shot['visible'] and not shot['speaking'] and all(
                isinstance(e, dict) and isinstance(e.get('subject_ids'), list)
                and set(e['subject_ids']).union(e.get('addressee_ids') or []) <= bound
                for e in (shot.get('events') or [])
            ):
                permitted[sid].append(key)
    return permitted


def continuity_options(permitted, excluded):
    available = {key for keys in permitted.values() for key in keys}
    if not isinstance(excluded, dict) or set(excluded) - available or any(
            not isinstance(reason, str) or not reason.strip() for reason in excluded.values()):
        raise ValueError('each excluded shot needs a valid source ID and a visible continuity reason')
    remaining = {scene: [key for key in keys if key not in excluded] for scene, keys in permitted.items()}
    if any(not keys for keys in remaining.values()):
        raise ValueError('no usable picture left for a locked scene')
    return remaining


def dialogue_walkaways(shots, permitted, lines):
    # The source action is observed independently of this edit. Match leaving
    # the conversation location, not a bystander approaching the stall.
    dialogue_scenes={l['scene_id'] for l in lines if l['role']}
    walks=r'(?:摊位间|摊位之间|市集(?:里|中|内)|集市(?:里|中|内)).{0,12}(?:走动|行走|走过)|(?:走出|离开|走离).{0,8}摊位'
    return {key: shots[key]['action'] for scene, keys in permitted.items() if scene in dialogue_scenes
            for key in keys if re.search(walks, shots[key].get('action', ''))}


def movement_context_candidates(shots, permitted, blocked):
    """Find nearby shots that may be the reverse or close-up of the same walk."""
    candidates={}
    for keys in permitted.values():
        walks=sorted((k for k in keys if k in blocked), key=lambda k:shots[k]['source_start_s'])
        for key in walks:
            origin=shots[key]
            end=origin['source_end_s']
            for other in sorted((k for k in keys if k not in blocked),key=lambda k:shots[k]['source_start_s']):
                shot=shots[other]
                if (shot['source_sha256']==origin['source_sha256'] and
                        set(origin['visible']).intersection(shot['visible']) and
                        origin['source_end_s']-.10 <= shot['source_start_s'] <= min(end+.50,origin['source_end_s']+3)):
                    candidates.setdefault(key,[]).append(other)
                    end=max(end,shot['source_end_s'])
    return candidates


def review_movement_context(out, shots, sources, permitted, blocked, ask):
    import io
    from PIL import Image, ImageDraw

    result={}
    for origin, keys in movement_context_candidates(shots,permitted,blocked).items():
        walk=shots[origin]
        first=walk['source_start_s']
        last=max(shots[k]['source_end_s'] for k in keys)
        proxy=out/('movement_'+origin+'.mp4')
        ff('-ss',first,'-i',sources[walk['source_sha256']],'-t',last-first,'-an',
           '-vf','scale=768:432,fps=15','-threads',2,'-c:v','libx264','-crf',22,proxy)
        slots={k:[round(shots[k][f]-first,3) for f in ('source_start_s','source_end_s')] for k in [origin,*keys]}
        board=Image.new('RGB',(4*256,len(slots)*162),'#17212b')
        draw=ImageDraw.Draw(board)
        for i,(label,(a,b)) in enumerate(slots.items()):
            for j,fraction in enumerate((.05,.33,.66,.90)):
                t=min(max(a,b-.09),a+(b-a)*fraction)
                data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(proxy),
                    '-frames:v','1','-vf','scale=256:144','-c:v','mjpeg','-f','image2pipe','pipe:1'])
                board.paste(Image.open(io.BytesIO(data)),(j*256,i*162))
                draw.text((j*256+4,i*162+145),f'{label}  {t:.2f}s',fill='white')
        board_path=out/('movement_'+origin+'.jpg')
        board.save(board_path,quality=92)
        def validate(v):
            rows=v.get('shots',{})
            if not isinstance(rows,dict) or set(rows)!=set(keys) or any(
                    type(r.get('same_walk')) is not bool or not r.get('evidence') for r in rows.values()):
                raise ValueError('give movement evidence for each: '+str(keys))
            return v
        try:
            verdict=ask('movement_context_'+origin,
                        '每行是同一来源连续原画里的前后取帧，按行拼接。第一行是已确定的角色走动；之后的短景逐段确认是否仍在同一段走路，'
                        '包括把腿裁掉的跟拍特写。看角色的持续起伏、背景相对移动、切前动作、是否落脚停稳重新在摊前。'
                        '不能单凭相邻镜号下结论。仅按实际画面判断。'
                        '返回 {"shots":{"后续镜号":{"same_walk":true或false,"evidence":"显示继续走动或停稳的画面依据"}}}。'
                        '分段（从连续素材零秒起算）：'+json.dumps(slots),validate,board_path)
        except LibraryStopped as error:
            message=str(error)
            prefix='opencode_vision_job_failed_no_retry:'
            if not message.startswith(prefix):
                raise
            failure=out/'calls'/message[len(prefix):]/'failure.json'
            if not failure.is_file() or read(failure)!={'error':'opencode_no_original_vision_reply','uncertain':False}:
                raise
            # Strict stand-in-place scenes must never silently admit an
            # unverified reverse angle when the model supplied no tool result.
            reason='相邻同一角色的走路近景尚未独立确认已停稳'
            result.update(dict.fromkeys(keys,reason))
            write_json(out/('movement_'+origin+'_guard.json'),dict(call=message[len(prefix):],excluded=keys,reason=reason))
            continue
        for k,row in verdict['shots'].items():
            if row['same_walk']:
                result[k]=row['evidence']
    return result


def dialogue_handoffs(lines):
    return [{'line': b['line_id'], 'scene': b['scene_id'], 'from': a['role'],
             'to': b['role'], 'frame': round(b['start_s']*FPS)}
            for a, b in zip(lines, lines[1:]) if a['scene_id']==b['scene_id'] and a['role']!=b['role']]


def speaker_picture_errors(cuts, shots, actors, lines, total):
    at_frame = {round(cut['at'] * FPS): cut for cut in cuts}
    failures = []
    for turn in dialogue_handoffs(lines):
        cut = at_frame.get(turn['frame'])
        if cut is None:
            failures.append([turn['line'], turn['frame'], 'cut on the incoming voice opening'])
        elif actors[turn['scene']][turn['to']] not in shots[cut['shot']]['visible']:
            failures.append([turn['line'], cut['shot'], 'show the incoming speaker at handoff'])
    return failures


def handoff_focus_edits(cuts, shots, actors, lines, permitted):
    """Find cuts where a registered listener dominates the incoming speaker."""
    by_frame={round(c['at']*FPS):c for c in cuts}
    edits=[]
    for turn in dialogue_handoffs(lines):
        shot=by_frame[turn['frame']]['shot']
        actor=actors[turn['scene']][turn['to']]
        alternatives=[key for key in permitted[turn['scene']]
                      if actor in shots[key]['visible'] and
                      not (set(shots[key]['speaking'])-{actor}) and
                      (actor in shots[key].get('clear_face',[]) or shots[key]['visible']==[actor])]
        if alternatives and shot not in alternatives:
            edits.append(dict(turn, shot=shot, alternatives=alternatives))
    return edits


def anchor_errors(cuts, anchors, lines, total):
    by_line={l['line_id']:l for l in lines}
    errors=[]
    for beat in anchors:
        key=beat['shot']
        if not key:continue
        line=by_line[beat['line']];a=round(line['start_s']*FPS);b=round(line['end_s']*FPS)
        spans=[(round(c['at']*FPS),round(cuts[i+1]['at']*FPS) if i+1<len(cuts) else total)
               for i,c in enumerate(cuts) if c['shot']==key]
        if not any(min(b,y)-max(a,x)>=min(6,b-a) for x,y in spans):
            errors.append(f"{beat['line']} needs {key} during {a/FPS:.2f}–{b/FPS:.2f}s")
        if beat['reserve'] and any(y<=a or x>=b for x,y in spans):
            errors.append(f"{key} reserved for {beat['line']}; use another shot elsewhere")
    return errors


def refined_anchors(anchors, checks, cuts, lines, total):
    """Use the revised picture for a weak beat without releasing reserved reactions."""
    weak = {row['line'] for row in checks if row['status'] == 'weak'}
    by_id = {line['line_id']: line for line in lines}
    revised = []
    for anchor in anchors:
        if anchor['line'] in weak and anchor['shot'] and not anchor['reserve']:
            line = by_id[anchor['line']]
            start, end = (round(line[k] * FPS) for k in ('start_s', 'end_s'))
            overlaps = []
            for i, cut in enumerate(cuts):
                stop = round(cuts[i+1]['at'] * FPS) if i+1 < len(cuts) else total
                overlap = min(end, stop) - max(start, round(cut['at'] * FPS))
                if overlap > 0:
                    overlaps.append((overlap, cut))
            if overlaps:
                cut = max(overlaps, key=lambda item: item[0])[1]
                if cut['shot'] != anchor['shot']:
                    anchor = dict(anchor, shot=cut['shot'], original_shot=anchor['shot'],
                                  observation=cut.get('reason') or '检查更换镜头后能否看懂这句')
        revised.append(anchor)
    return revised


def picture_evidence(out, name, picture, lines, anchors, ask, notes):
    import io
    from PIL import Image, ImageDraw

    by_id = {line['line_id']: line for line in lines}
    evidence = {}
    # Limit each page so the model can read a face even in a long multi-scene cut.
    for page_start in range(0, len(anchors), 6):
        page = anchors[page_start:page_start + 6]
        sheet = Image.new('RGB', (960, len(page) * 202), '#17212b')
        draw = ImageDraw.Draw(sheet)
        claims = {}
        for row, anchor in enumerate(page):
            lid = anchor['line']
            line = by_id[lid]
            start, end = (round(line[k] * FPS) for k in ('start_s', 'end_s'))
            for col, phase in enumerate((.15, .5, .85)):
                frame = min(end-1, start + int((end-start) * phase))
                data = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(frame/FPS),
                    '-i', str(picture), '-frames:v', '1', '-vf', 'scale=320:180',
                    '-c:v', 'mjpeg', '-f', 'image2pipe', 'pipe:1'])
                sheet.paste(Image.open(io.BytesIO(data)), (col*320, row*202))
                draw.text((col*320+3, row*202+181), f'{row+1}   {frame/FPS:.2f}s', fill='white')
            claims[str(row+1)] = notes.get(lid) or {'本句': line.get('text'), '剪辑主张': anchor['observation']}
        stage = name + '_frames_' + str(page_start)
        image = out / (stage + '.jpg')
        sheet.save(image, quality=92)
        def check(v):
            rows = v.get('rows', {})
            if set(rows) != set(claims) or any(
                r.get('status') not in ('clear','unclear') or not r.get('seen') for r in rows.values()):
                raise ValueError('describe the visible evidence and give clear/unclear for each row')
            return v
        vote = ask(stage, '每行是同一时段三个无声、无台词字幕的实际导出画面。以下是待核实的说法。'
                   '只凭本行画面检查主体和动作，人物只有部分头部、认不出发问者、或无法确认所述变化就判unclear。'
                   '看不见的道具不要补全。返回 {"rows":{"1":{"status":"clear或unclear","seen":"图中具体依据"}}}。'
                   + json.dumps(claims, ensure_ascii=False), check, image)
        for row, anchor in enumerate(page):
            evidence[anchor['line']] = vote['rows'][str(row+1)]
    return evidence


def reconcile_picture_review(review, evidence):
    checks = []
    for row in review['checks']:
        proof = evidence[row['line']]
        if row['status'] == 'visible' and proof['status'] == 'unclear':
            row = dict(row, voiced_status=row['status'], voiced_reason=row['reason'],
                       status='weak', reason=proof['seen'])
        checks.append(row)
    return dict(review, checks=checks, frame_evidence=evidence)


def review_rows(review, requested, available):
    rows = review.get('checks', [])
    by_id = {row['line']: row for row in rows}
    if len(by_id) != len(rows) or not set(requested) <= by_id.keys() or set(by_id) - available:
        raise ValueError('missing, repeated, or unknown line; required ' + str(requested))
    if len(rows) > len(requested):
        review['all_checks'] = rows
    review['checks'] = [by_id[line] for line in requested]
    return review['checks']


def blind_review(out,name,picture,lines,anchors,ask):
    spans={a['line']:next((round(l['start_s'],2),round(l['end_s'],2)) for l in lines if l['line_id']==a['line']) for a in anchors}
    proxy=out/(name+'_silent.mp4')
    ff('-i',picture,'-an','-vf','scale=768:432,fps=15','-threads',2,'-c:v','libx264','-crf',24,proxy)
    def validate(v):
        obs=v.get('observations',[])
        if not isinstance(obs,list) or [r.get('id') for r in obs]!=list(spans) or any(not r.get('seen') for r in obs):
            raise ValueError('describe each requested interval in order')
        return v
    return ask(name,'仅按画面记录各时段人物位置、动作、物件、朝向、能否读出表情变化。看不清就写不清。'
               '避免推测未出现的事件。返回 {"observations":[{"id":"时段编号","seen":"看到的具体现象"}]}。'
               +json.dumps(spans,ensure_ascii=False),validate,proxy)


def insert_role_guides(sequence, shots, scene_keys, actors):
    """Map each already-verified insert participant to playable intervals in the current cut pool."""
    guides=[]
    for part in sequence:
        sid=part['reference']
        for unit in part.get('picture_units',[]):
            if unit.get('kind')!='insert':
                continue
            roles={role:[] for role in unit['roles']}
            for key in scene_keys.get(sid,[]):
                shot=shots[key]
                for span in unit['usable_segments']:
                    if shot['window_id']!=span['window_id'] or shot['source_sha256']!=span['source_sha256']:
                        continue
                    lo=max(shot['source_start_s'],span['source_start_s'])-shot['source_start_s']
                    hi=min(shot['source_end_s'],span['source_end_s'])-shot['source_start_s']
                    if hi-lo<1/FPS:
                        continue
                    for role in roles:
                        if actors[sid].get(role) in shot['visible']:
                            roles[role].append(dict(shot=key,start=round(lo,5),end=round(hi,5)))
            if roles:
                guides.append(dict(scene=sid,unit=unit['reference'],roles=roles))
    return guides


def missing_insert_roles(guides, cuts, shots, total):
    played={}
    for i,cut in enumerate(cuts):
        key=cut['shot']
        end=round(cuts[i+1]['at']*FPS) if i+1<len(cuts) else total
        span=(end-round(cut['at']*FPS))/FPS*cut['speed']
        limit=shots[key]['source_end_s']-shots[key]['source_start_s']
        played.setdefault((cut['scene'],key),[]).append((cut['offset'],min(limit,cut['offset']+span)))
    missing=[]
    for guide in guides:
        for role,choices in guide['roles'].items():
            present=any(min(end,choice['end'])-max(start,choice['start'])>=min(.2,choice['end']-choice['start'])-.00001
                        for choice in choices for start,end in played.get((guide['scene'],choice['shot']),[]))
            if not present:
                missing.append([guide['unit'],role,'插镜中的人物没有出现在实际播放区间',choices])
    return missing


def timed_script_visibility(lines, script, scenes):
    """Join dubbed line IDs to the first reference utterance they actually adapt."""
    refs={(s['scene_id'],r['line_id']):r for s in scenes for r in s.get('reference_lines',[])}
    adapted={(r['scene_id'],r['line_id']):r for r in script}
    result=[]
    for line in lines:
        sid,lid=line['scene_id'],line['line_id']
        ids=adapted.get((sid,lid),{}).get('reference_line_ids') or [lid]
        first=next((refs[sid,r] for r in ids if (sid,r) in refs and
                    refs[sid,r].get('role',line['role'])==line['role']),{})
        result.append(dict(line,on_screen=first.get('on_screen')))
    return result


def incoming_voice_guides(sequence, shots, scene_keys, actors, spoken):
    """Locate the first on-screen words of a new partner after a verified insert."""
    previous=set()
    guides=[]
    for part in sequence:
        sid=part['reference']
        units=part.get('picture_units') or [part]
        after_insert=False
        for unit in units:
            if unit.get('kind')=='insert':
                after_insert=True
                continue
            seen=unit.get('performance_review',{}).get('people',[])
            roles=set(unit.get('roles') or (p['role'] for p in seen))
            if previous and after_insert:
                for role in sorted(roles-previous):
                    line=next((l for l in spoken if l.get('scene_id')==sid and l['role']==role),None)
                    if not line or line.get('on_screen') is not True:
                        continue
                    staged=dict(reference=sid,picture_units=[dict(unit,kind='insert',roles=[role])])
                    choices=insert_role_guides([staged],shots,scene_keys,actors)[0]['roles'][role]
                    if choices:
                        guides.append(dict(scene=sid,role=role,at=line['start_s'],choices=choices))
            previous=roles
            after_insert=False
    return guides


def missed_incoming_voices(guides,cuts):
    missed=[]
    for guide in guides:
        frame=round(guide['at']*FPS)
        cut=next((c for c in reversed(cuts) if round(c['at']*FPS)<=frame),None)
        if not cut:
            missed.append(guide)
            continue
        offset=cut['offset']+(frame-round(cut['at']*FPS))/FPS*cut['speed']
        if cut['scene']!=guide['scene'] or not any(c['shot']==cut['shot'] and c['start']-.0001<=offset<c['end']+.0001
                                                    for c in guide['choices']):
            missed.append(guide)
    return missed


def run_edit(out,parent,result,original,shots,sources,lines,scene_keys,skill,contract,beats,ask,*,targets,notes,independent_brief=None,turn_locked=False,blocked=None,automatic_reference=None):
    total=round(result['timeline'][-1]['end_s']*FPS)
    lock=read(parent/'material_lock.json')
    cast=lock['scenes']
    actors={s['scene_id']:{r:b['role_id'] for r,b in s['role_bindings'].items()} for s in cast}
    sequence=lock.get('retrieved_scene',{}).get('scene_sequence',[])
    insert_guides=insert_role_guides(sequence,shots,scene_keys,actors)
    script_lines=read(parent/'dialogue_script.json')['lines'] if sequence else []
    spoken=timed_script_visibility(lines,script_lines,cast)
    incoming_guides=incoming_voice_guides(sequence,shots,scene_keys,actors,spoken)
    boundaries=[]
    for row in result['timeline']:
        sid=row['scene_id']
        a,b=round(row['start_s']*FPS),round(row['end_s']*FPS)
        if boundaries and sid==boundaries[-1][0]:boundaries[-1][2]=b
        else:boundaries.append([sid,a,b])
    if [s for s,_,_ in boundaries]!=list(scene_keys):
        raise ValueError('baseline timeline does not represent every locked scene in order')
    def validate(v):
        cuts=v.get('cuts')
        if not isinstance(cuts,list) or not cuts:
            raise ValueError('give cuts in time order')
        last=-1
        openings={l['line_id']:l['start_s'] for l in lines}
        for c in cuts:
            if turn_locked and c.get('at_line') in openings:
                c['at']=openings[c['at_line']]
            for field in ('at','offset','speed','center'):
                try: number=float(c[field])
                except (TypeError,ValueError,KeyError):raise ValueError('expected a number for '+field+' in '+str(c))
                if not math.isfinite(number):raise ValueError('nonfinite '+field)
                c[field]=number
            frame=round(c['at']*FPS)
            key=c['shot']; shot=shots.get(key)
            if not last<frame<total or (last<0 and frame!=0) or not shot:
                raise ValueError('invalid cut start or shot: '+str(c))
            last=frame
            sid=c['scene']
            if key not in scene_keys[sid] or not 0<=c['offset']<shot['source_end_s']-shot['source_start_s']-1/FPS:
                raise ValueError('wrong scene or offset in '+str(c))
            # A blank fallback leaves playback at normal length; if the source ends
            # before the cut, the renderer already holds its final frame.
            if c.get('tail') in ('',None):c['tail']='hold'
            if not 0.5<=c['speed']<=1.5 or c['tail'] not in ('hold','loop') or (c['tail']=='loop' and not shot['loopable']):
                raise ValueError('invalid playback speed/tail '+key)
            if 'center_y' in c:
                c['center_y']=float(c['center_y'])
                if not 0<=c['center_y']<=1:raise ValueError('vertical focus out of range')
            if c['size'] not in ('full','medium','close') or not 0<=c['center']<=1:
                raise ValueError('invalid framing '+key)
            role=c['shows_speaker']
            permitted=set(shot['speaking']+shot.get('dub_speaker_ids',[])+shot.get('concealed_dub_ids',[])+shot.get('stylized_dub_ids',[]))
            if role and actors[sid].get(role) not in permitted:
                raise ValueError('cannot depict '+str(role)+' speaking in '+key+'; use verified performer, insert or listening view')
        for i,c in enumerate(cuts):
            start=round(c['at']*FPS);end=round(cuts[i+1]['at']*FPS) if i+1<len(cuts) else total
            overlap={sid for sid,a,b in boundaries if a<end and b>start}
            if overlap-{c['scene']}:raise ValueError('cannot use a different scene during these lines: '+str(c))
        return v
    if independent_brief or automatic_reference is not None:
        proxy=out/'soundtrack_only.mp4'
        if not proxy.exists():
            ff('-f','lavfi','-i','color=c=black:s=640x360:r=12','-i',original,
               '-map','0:v:0','-map','1:a:0','-t',total/FPS,'-c:v','libx264','-threads',2,
               '-c:a','copy',proxy)
        media_context='附件是只有黑画面的原配音。根据原声和之前看到的源镜头自己决定剪点。'
    else:
        proxy=out/'original_proxy.mp4'
        if not proxy.exists():
            ff('-i',original,'-vf','scale=640:360,fps=12','-c:v','libx264','-crf',26,'-threads',2,'-c:a','aac','-b:a','80k',proxy)
        media_context='附上原来的成片，可据此判断锁定的声音节奏。'
    form=('返回JSON {"cuts":[{"at":0,"scene":"场景ID","shot":"S编号","offset":0,"speed":1,'
          '"size":"full","center":0.5,"tail":"hold","shows_speaker":"","reason":"此刀的目的"}]}。'
          'at为成片绝对秒数，从0递增，下一刀开始即上一刀结束。offset是镜头内相对秒数。'
          'speed 0.5到1.5，默认1。size取full/medium/close，center为主体横向位置0至1。'
          'tail表示源区间不够长时的手段：hold停住末帧；loop只允许repeatable=true的镜头。尽量用切镜或自然时长，避免长定格。'
          'shows_speaker仅在画面明确让登记演员开口时写其角色名，物件、听者或全景填空字符串。保持身份与视线。')
    permitted=allowed_shots(shots,scene_keys,{sc['scene_id']:sc['role_bindings'] for sc in cast})
    excluded={**(blocked or {}), **beats.get('exclude',{})}
    permitted=continuity_options(permitted,excluded)
    def cast_problems(cuts):
        problems=[]
        for i,c in enumerate(cuts):
            key=c['shot']; sid=c['scene']; a=round(c['at']*FPS)
            b=round(cuts[i+1]['at']*FPS) if i+1<len(cuts) else total
            if key not in permitted[sid]:
                problems.append([c['at'],key,excluded.get(key,'unbound performer')])
            spoken=set(shots[key]['speaking'])
            for l in lines:
                if l['scene_id']!=sid or b<=round(l['start_s']*FPS) or a>=round(l['end_s']*FPS):continue
                if spoken-{actors[sid][l['role']]}:
                    problems.append([c['at'],key,'different actor mouths '+l['line_id']])
        return problems
    def cast_check(v, anchors=None):
        validate(v)
        problems=missing_insert_roles(insert_guides,v['cuts'],shots,total)+cast_problems(v['cuts'])+anchor_errors(v['cuts'],beats['anchors'] if anchors is None else anchors,targets,total)
        problems += missed_incoming_voices(incoming_guides,v['cuts'])
        if turn_locked:
            problems += speaker_picture_errors(v['cuts'],shots,actors,lines,total)
        if problems:raise ValueError('timeline conflicts: '+str(problems)+'; available shots '+str(permitted))
        return v
    role_table={sid:{k:dict(visible=[r for r,a in actors[sid].items() if a in shots[k]['visible']],
        original_speaker=[r for r,a in actors[sid].items() if a in shots[k]['speaking']],
        dubbable=[r for r,a in actors[sid].items() if a in shots[k]['speaking']+shots[k].get('dub_speaker_ids',[])])
        for k in keys} for sid,keys in permitted.items()}
    rules=('只能使用表中的镜头。original_speaker有值时只许配该角色的台词，跨句要核对。'
           'shows_speaker只能取dubbable中的角色或空字符串。表中角色名已经按登记身份换算。'+json.dumps(role_table,ensure_ascii=False))
    if incoming_guides:
        rules+='新同伴首次在画内开口时，须已经回到能看清他的对白画面。按配音时钟自行选镜：'+json.dumps(incoming_guides,ensure_ascii=False)
    if insert_guides:
        rules+='原片插镜中的每个角色都需要在实际播放区间保留。按镜头内秒数自行选段及排序：'+json.dumps(insert_guides,ensure_ascii=False)
    if turn_locked:
        rules += ('本次指定在换人开口帧交接给接话者。换人点用at_line引用句号；交接后自行决定镜头和景别。'
                  '需要交接的帧：'+json.dumps(dialogue_handoffs(lines),ensure_ascii=False))
    plan=ask('plan',skill+'\n第二步。'+media_context+'你负责重新剪画面。'
             '保持原音轨、演员和每句顺序、起止不变，按当前全部候选自由剪，不必沿用旧镜头组合。每段锁定边界（场景ID、起止帧，30 fps）'+json.dumps(boundaries)+'。'
             +
             '必须兑现看片时的anchors；reserve=true的表情只给那一句；重复话可改变构图让观众看清谁在施压；长段配音可在符合说话者与场景要求时跨镜。避免只换相位重放同一动作。只用有已绑定演员或空镜道具的允许镜头 '+json.dumps(permitted,ensure_ascii=False)+'。每一刀先核对锁定的演员ID，只有演员ID确实登记在该镜头的speaking或dub内才可填shows_speaker。成片总秒数'+str(total/FPS)+'。'+rules+form+contract+'\n看片笔记'+json.dumps(beats,ensure_ascii=False),cast_check,proxy)
    rough=render(out,parent,result,original,shots,sources,plan,total,'rough')
    def review_check(v):
        if not v.get('retelling') or not isinstance(v.get('problems'),list):raise ValueError('give retelling and problems')
        return v
    small=out/'review_proxy.mp4'
    ff('-i',rough,'-vf','scale=768:432,fps=15','-threads',2,'-c:v','libx264','-crf',24,'-c:a','aac','-b:a','96k',small)
    def review_export(name,picture,voiced,anchors):
        blind=blind_review(out,name+'_blind',picture,targets,anchors,ask)
        def compare(v):
            review_check(v)
            rows=review_rows(v,[a['line'] for a in anchors],{l['line_id'] for l in targets})
            if any(r.get('status') not in ('visible','audio_only','weak') or not r.get('reason') for r in rows):
                raise ValueError('state visual support for each anchor')
            for check,anchor in zip(rows,anchors):
                if anchor['shot'] is None and check['status']=='visible':
                    raise ValueError('previously identified missing evidence for '+anchor['line']+'; review the actual silent observations')
            return v
        voiced_review=ask(name,'对照以下独立静音记录与现在有声成片，核对每句需要看到的事是否真能看见。'
                   '静音记录未出现的道具不能凭台词宣称看见。人物同框不足以证明人物做了什么。'
                   '可行的画面调整记入problems；素材确定缺失单列audio_only。'
                   '返回 {"retelling":"简述","checks":[{"line":"编号","status":"visible/audio_only/weak","reason":"依据"}],'
                   '"problems":[{"at":0,"observed":"可修的具体问题"}]}。'
                   +json.dumps(dict(blind=blind,anchors=anchors,script=lines),ensure_ascii=False),compare,voiced)
        evidence=picture_evidence(out,name,picture,targets,anchors,ask,notes)
        combined=reconcile_picture_review(voiced_review,evidence)
        write_json(out/(name+'_reconciled.json'),combined)
        return combined
    active_anchors=beats['anchors']
    review=review_export('review',out/'rough_picture.mp4',small,active_anchors)
    chosen=plan; final=rough
    if automatic_reference is None and (review['problems'] or any(r['status']=='weak' for r in review['checks'])):
        def fix_check(v):
            if v.get('keep') is not True:
                validate(v)
                revised=refined_anchors(active_anchors,review['checks'],v['cuts'],targets,total)
                cast_check(v,revised)
            elif not str(v.get('reason','')).strip(): raise ValueError('explain why the cut should stay')
            return v
        fix=ask('refine','只对粗剪反馈作一次局部调整。保留没有问题的切点和已锁定的对白、演员。'
                '若锁定素材确实无法解决，返回 {"keep":true,"reason":"..."}。能改就返回完整剪辑表，另附reason说明实际改的部位。'
                +'标成weak且未专用的镜头可以换，保留该句原意，按实际导出向观众交代清楚；专用反应仍须保留。复看须按导出后的实际构图修正。多人镜头若只看得到头顶就不能说清谁在问。需要重构图时，可为该刀增写center_y，'
                '与center分别是所要留住的主体在原始画面中的纵、横位置（0到1）；主体偏下就把center_y向下移。'
                '按镜头里可见的脸或身形来定中心，尽量留住动作，不要把围观者放大当成发问人。'
                +rules+form+contract+'\n原来的关键句安排'+json.dumps(beats,ensure_ascii=False)+'\n原剪辑表'+json.dumps(plan,ensure_ascii=False)+'\n实际看片反馈'+json.dumps(review,ensure_ascii=False),fix_check,small)
        if fix.get('keep') is not True:
            active_anchors=refined_anchors(active_anchors,review['checks'],fix['cuts'],targets,total)
            chosen=fix;final=render(out,parent,result,original,shots,sources,chosen,total,'refined')
    cast_check(chosen,active_anchors)
    focus_edits=handoff_focus_edits(chosen['cuts'],shots,actors,lines,permitted) if turn_locked else []
    review_name='final_review'
    if focus_edits:
        flagged={e['line']:e for e in focus_edits}
        selected={round(c['at']*FPS):c for c in chosen['cuts']}
        def check_focus(v):
            replacements=v.get('replacements',[])
            by_line={item['line']:item for item in replacements}
            if set(by_line)!=set(flagged) or len(by_line)!=len(replacements):
                raise ValueError('replace each of these handoffs once: '+str(list(flagged)))
            revised=dict(selected)
            for lid,item in by_line.items():
                row=flagged[lid]
                if item['shot'] not in row['alternatives']:
                    raise ValueError(lid+' requires one of '+str(row['alternatives']))
                revised[row['frame']]=dict(item, scene=row['scene'], at=row['frame']/FPS)
            fixed={'cuts':[revised[f] for f in sorted(revised)]}
            anchors=refined_anchors(active_anchors,[dict(line=l,status='weak') for l in flagged],fixed['cuts'],targets,total)
            cast_check(fixed,anchors)
            if handoff_focus_edits(fixed['cuts'],shots,actors,lines,permitted):
                raise ValueError('incoming performer must be the clear subject at handoffs')
            v['cuts']=fixed['cuts']
            v['anchors']=anchors
            return v
        candidate=out/'handoff_candidate.mp4'
        ff('-i',final,'-vf','scale=768:432,fps=15','-threads',2,'-c:v','libx264','-crf',24,'-c:a','aac','-b:a','96k',candidate)
        response=ask('handoff_focus','复查你自己的成片。以下开口点虽换了镜头，主体仍是别人。只重选这些镜头；原配音、切点及其余画面照旧。'
                     '每行可从其alternatives挑选登记为清晰主体的真实镜头。用开口就看得清的入点和构图，尽量错开重复动作。'
                     '返回 {"replacements":[{"line":"句号","shot":"镜号","offset":0,"speed":1,"size":"medium",'
                     '"center":0.5,"center_y":0.6,"tail":"hold","shows_speaker":"","reason":"可以确认的画面"}]}。'
                     +json.dumps(focus_edits,ensure_ascii=False)+rules+contract,check_focus,candidate)
        chosen={'cuts':response['cuts']}
        active_anchors=response['anchors']
        final=render(out,parent,result,original,shots,sources,chosen,total,'focused')
        review_name='handoff_review'
    if final!=rough:
        final_proxy=out/(review_name+'_proxy.mp4')
        ff('-i',final,'-vf','scale=768:432,fps=15','-threads',2,'-c:v','libx264','-crf',24,'-c:a','aac','-b:a','96k',final_proxy)
        review=review_export(review_name,out/(final.stem+'_picture.mp4'),final_proxy,active_anchors)
    anchor_review_media=sha256_file(final)
    automatic=None
    if automatic_reference is not None:
        before=audit_render(out,'picture_audit',final,chosen['cuts'],
                            lines,total,FPS,automatic_reference,ask,ff)
        actionable={r['cut'] for r in before if r['kind']!='missing_material'}
        automatic=dict(before=before,remaining=before,accepted=False)
        if actionable:
            affected={key:chosen['cuts'][int(key)-1] for key in sorted(actionable,key=int)}
            def revise(value):
                fixed=apply_cut_replacements(chosen['cuts'],actionable,value)
                changed=[int(key)-1 for key in value['replacements']]
                weak=[dict(line=l['line_id'],status='weak') for l in targets if any(
                    chosen['cuts'][i]['at']<l['end_s'] and
                    (chosen['cuts'][i+1]['at'] if i+1<len(chosen['cuts']) else total/FPS)>l['start_s']
                    for i in changed)]
                anchors=refined_anchors(active_anchors,weak,fixed,targets,total)
                cast_check({'cuts':fixed},anchors)
                if turn_locked and handoff_focus_edits(fixed,shots,actors,lines,permitted):
                    raise ValueError('repair cannot remove focus from the incoming actor')
                value['cuts']=fixed
                value['anchors']=anchors
                return value
            feedback=ask('targeted_picture_revision',
                         '根据对实际导出片的独立复查，只改报出的刀号。保留演员、原声、片长、场景边界及全部原有切点。'
                         '可换同场许可素材、调整入点、速度或构图。缺少支持素材时说明原因并置于deferred。'
                         '不要更改其他刀或杜撰道具。普通可用镜头可重复使用。'
                         '返回 {"replacements":{"刀号":{"shot":"S编号","offset":0,"speed":1,"size":"full",'
                         '"center":0.5,"center_y":0.5,"tail":"hold","shows_speaker":"","reason":"实际改法"}},'
                         '"deferred":{"无法修的刀号":"原因"}}。'
                         +json.dumps(dict(issues=before,cuts=affected),ensure_ascii=False)+rules+contract,
                         revise,final_proxy if final!=rough else small)
            automatic['deferred']=feedback['deferred']
            if feedback['replacements']:
                candidate=render(out,parent,result,original,shots,sources,feedback,total,'automatic_revision')
                after=audit_render(out,'picture_recheck',candidate,feedback['cuts'],
                                   lines,total,FPS,automatic_reference,ask,ff)
                automatic['candidate']=after
                evidence=adjudicate_repairs(out,out/(final.stem+'_picture.mp4'),
                    out/'automatic_revision_picture.mp4',chosen['cuts'],feedback['cuts'],
                    before,after,total,FPS,ask,ff)
                automatic['paired_evidence']=evidence
                if evidence['accepted']:
                    chosen=feedback
                    final=candidate
                    active_anchors=feedback['anchors']
                    automatic.update(accepted=True,remaining=evidence['remaining'])
        automatic['status']='needs_review' if any(r['kind']!='missing_material' for r in automatic['remaining']) else 'no_open_picture_issues'
        write_json(out/'automatic_picture_review.json',automatic)
    import hashlib
    def audio_hash(path):
        audio=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','adts','pipe:1'])
        return hashlib.sha256(audio).hexdigest()
    if audio_hash(final)!=audio_hash(original):raise ValueError('original audio packets changed')
    write_json(out/'selected_edit_result.json',dict(final_video=str(final),sha256=sha256_file(final),baseline=sha256_file(original),
               audio_packets_sha256=audio_hash(final),source_lock_sha256=sha256_file(parent/'material_lock.json'),
               dialogue_sha256=sha256_file(parent/'dialogue_script.json'),subtitle_sha256=sha256_file(parent/'final_subtitled.ass'),
               **({'automatic_picture_review':automatic} if automatic is not None else {}),
               shots=len(shots),frames=total,lines=len(lines),subtitle_style='verified_translucent_mosaic',
               blocked_sources=blocked or {},focus_rechecks=focus_edits,dialogue_turns=dialogue_handoffs(lines) if turn_locked else [],anchor_review=review['checks'],anchor_review_video_sha256=anchor_review_media,final_anchors=active_anchors,
               visual_gaps=[r['line'] for r in review['checks'] if r['status']!='visible'],cuts=chosen['cuts'],shot_ids={k:v['slot_id'] for k,v in shots.items()}))


def mask_source_matches(bound, source_sha256):
    # Early persisted manifests only wrote the digest inside the proxy.
    proxy_sha256=bound['proxy'].get('source_sha256')
    return proxy_sha256==source_sha256 and bound.get('source_sha256',proxy_sha256)==source_sha256


def subtitle_edge_padding(treatment):
    # Include the outlined glyph edges just outside the detected rectangle.
    x,y=max(0,treatment['x']-.006),max(0,treatment['y']-.018)
    right=min(1,treatment['x']+treatment['w']+.006)
    bottom=min(1,treatment['y']+treatment['h']+.018)
    return dict(treatment,x=x,y=y,w=right-x,h=bottom-y,block_px=max(24,treatment['block_px']))


def render(out,parent,result,original,shots,sources,plan,total,name):
    browser=next(p/'autonomous_material/browser' for p in parent.parents if (p/'autonomous_material/browser').is_dir())
    windows={shots[c['shot']]['window_id'] for c in plan['cuts']}
    bounds={w:read(browser/(w+'_media.json')) for w in windows}
    observations={w:read(browser/(w+'_visual.json')) for w in windows}
    dimensions={}
    clips=[]
    for i,c in enumerate(plan['cuts']):
        end=round(plan['cuts'][i+1]['at']*FPS) if i+1<len(plan['cuts']) else total
        frames=end-round(c['at']*FPS)
        s=shots[c['shot']]
        start=math.ceil((s['source_start_s']+c['offset'])*FPS-1e-6)/FPS
        length=math.floor((s['source_end_s']-start)*FPS+1e-6)
        if length<1:raise ValueError('empty source interval')
        wid=s['window_id']
        bound=bounds[wid]
        if not mask_source_matches(bound,s['source_sha256']):
            raise ValueError('source does not match verified subtitle mask '+wid)
        row=dict(window_id=wid,source_id=bound['proxy']['source_id'],source_in_s=start,source_out_s=start+length/FPS)
        if not verified_masks([row],bounds,observations):
            raise ValueError('missing verified subtitle mask '+wid)
        source=sources[s['source_sha256']]
        filters=['fps=30','trim=end_frame='+str(length),'setpts=PTS-STARTPTS']
        graph=[]
        treatments=[subtitle_edge_padding(mask) for mask in row['source_subtitle_treatments']]
        if treatments:
            if source not in dimensions:
                native=next(item for item in _probe(source,'ffprobe')['streams'] if item['codec_type']=='video')
                dimensions[source]=(native['width'],native['height'])
            graph.append('[0:v]'+','.join(filters)+'[mosaic0]')
            graph.extend(mosaic_layers(treatments,*dimensions[source],start))
            filters=[]
        scale={'full':1,'medium':.86,'close':.72}[c['size']]
        x=max(0,min(1-scale,c['center']-scale/2))
        y=max(0,min(1-scale,c.get('center_y',.5)-scale/2))
        filters+=['crop=trunc(iw*%f/2)*2:trunc(ih*%f/2)*2:iw*%f:ih*%f'%(scale,scale,x,y),
                  'scale=1280:720','setsar=1']
        if c['tail']=='loop':filters.append('loop=loop=-1:size='+str(length)+':start=0')
        filters+=['setpts=N/(30*%f*TB)'%c['speed'],'fps=30','tpad=stop_mode=clone:stop_duration='+str(frames/FPS+1)]
        dest=out/(name+'_cut_%03d.mp4'%i)
        filtering=(['-filter_complex_threads',1,'-filter_complex',
                    ';'.join([*graph,f'[mosaic{len(treatments)}]'+','.join(filters)+'[picture]']),
                    '-map','[picture]'] if graph else ['-vf',','.join(filters)])
        ff('-threads',2,'-ss',start,'-i',source,'-an',*filtering,
           '-frames:v',frames,'-c:v','libx264','-preset','veryfast','-crf',17,'-pix_fmt','yuv420p','-threads',2,dest)
        clips.append(dest)
    picture=concat(out,clips,name+'_picture')
    dest=out/(name+'.mp4')
    ff('-i',picture,'-i',original,'-map','0:v','-map','1:a:0','-vf',"ass='"+str(parent/'final_subtitled.ass')+"'",
       '-c:v','libx264','-preset','veryfast','-threads',2,'-crf',17,'-c:a','copy','-movflags','+faststart',dest)
    count=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=nb_frames',
                                   '-of','csv=p=0',str(dest)],text=True)
    if int(count.strip())!=total:raise ValueError('render length changed: '+count)
    return dest


if __name__=='__main__':
    main()
