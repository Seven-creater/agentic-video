"""Source-grounded scene handoff and independent active-speaker evidence."""
from copy import deepcopy
import json
import math

from .state import LibraryStopped,json_sha,write_json


def candidate_ranges(candidate):
    """Assign each source frame once, retaining the model's chosen scene only."""
    result={};covered=[]
    for row in sorted(candidate['usable_segments'],key=lambda r:(r['source_start_s'],r['source_end_s'])):
        pieces=[(row['source_start_s'],row['source_end_s'])]
        for x,y in covered:
            pieces=[(a,b) for start,end in pieces for a,b in
                ((start,min(end,x)),(max(start,y),end)) if b-a>=1/30]
        for a,b in pieces:result.setdefault(row['window_id'],[]).append((a,b))
        covered.extend(pieces)
    return result


def scope_shots(shots,ranges):
    result=[]
    for shot in shots:
        for low,high in ranges.get(shot['window_id'],[]):
            a=math.ceil(max(low,shot['source_start_s'])*30-1e-6)/30
            b=math.floor(min(high,shot['source_end_s'])*30+1e-6)/30
            if b-a<.3:continue
            row=deepcopy(shot)
            row.update(source_start_s=a,source_end_s=b)
            if (a,b)!=(shot['source_start_s'],shot['source_end_s']):
                row['slot_id']='shot_'+json_sha([shot['slot_id'],a,b])[:12]
                row['events']=[dict(e,event_id=row['slot_id']+'_event_'+str(i+1)) for i,e in enumerate(row['events'])]
            result.append(row)
    return result


def apply_performance_policy(shots,policy):
    """Creative animation dubbing is permission, never a source-speaking fact."""
    result=deepcopy(shots)
    for shot in result:
        concealed=shot.get('dub_speaker_ids',[])
        shot['concealed_dub_ids']=list(concealed)
        stylized=([a for a in shot['visible'] if a not in concealed]
            if policy=='stylized_animation_v1' and not shot['speaking'] else [])
        shot['stylized_dub_ids']=stylized
        shot['dub_speaker_ids']=list(dict.fromkeys(concealed+stylized))
        shot['performance_policy']=policy
    return result


def validate_source_turns(value,actors,duration):
    rows=value.get('turns') if isinstance(value,dict) else None
    if not isinstance(rows,list):raise ValueError('source turns array required; empty is allowed')
    previous=0
    for row in rows:
        a,b=row.get('start_s'),row.get('end_s')
        if any(type(t) not in (float,int) or not math.isfinite(t) for t in (a,b)) or not previous-.02<=a<b<=duration+.02:
            raise ValueError('source turns must be ordered, disjoint local intervals')
        previous=b
        visible=row.get('visible');active=row.get('speaking')
        if (not isinstance(visible,list) or not set(visible)<=set(actors) or
                not isinstance(active,list) or not set(active)<=set(visible) or len(active)>1):
            raise ValueError('source speaker must be one actually visible known actor')
        if row.get('basis') not in {'mouth_motion','synchronous_gesture','none','uncertain'}:
            raise ValueError('source speaking basis required')
        if not isinstance(row.get('evidence'),str) or not row['evidence'].strip():raise ValueError('source evidence required')
        if active and row['basis'] not in {'mouth_motion','synchronous_gesture'}:
            raise ValueError('uncertain or silent observations cannot label a speaker')
    return value


def normalize_source_turns(value, actors=None):
    """An audio-only hypothesis cannot become visible speaking or a silent listener."""
    result=deepcopy(value)
    aliases={}
    for ident,appearance in (actors or {}).items():
        if isinstance(appearance,str) and list(actors.values()).count(appearance)==1 and appearance not in actors:
            aliases[appearance]=ident
    for turn in result.get('turns',[]) if isinstance(result,dict) else []:
        if not isinstance(turn,dict):continue
        for field in ('visible','speaking'):
            values=turn.get(field)
            if isinstance(values,list):
                turn[field]=[aliases.get(v,v) if isinstance(v,str) else v for v in values]
    known=lambda values:isinstance(values,list) and all(isinstance(a,str) and a in (actors or {}) for a in values)
    for turn in result.get('turns',[]) if isinstance(result,dict) else []:
        # A non-visual basis such as "off_screen_voice" is hearing, not seeing:
        # keep the stated basis and let the speaker become a heard hypothesis.
        if (isinstance(turn,dict) and isinstance(turn.get('basis'),str) and
                turn['basis'] not in {'mouth_motion','synchronous_gesture','none','uncertain'} and
                known(turn.get('visible')) and known(turn.get('speaking'))):
            turn['source_basis_original']=turn['basis']
            turn['basis']='uncertain'
    for turn in result.get('turns',[]) if isinstance(result,dict) else []:
        if isinstance(turn,dict) and turn.get('basis') in {'none','uncertain'} and turn.get('speaking'):
            turn['heard_actor_hypotheses']=turn['speaking']
            turn['speaking']=[]
            turn['basis']='uncertain'
        elif isinstance(turn,dict) and turn.get('basis') in {'mouth_motion','synchronous_gesture'}:
            visible,active=turn.get('visible'),turn.get('speaking')
            if known(visible) and known(active) and (len(active)>1 or not set(active)<=set(visible)):
                turn['source_claim_conflict']=dict(claimed_speaking=list(active),
                    reported_visible=list(visible),claimed_basis=turn['basis'],
                    reason='speaker not visually established or not uniquely attributed')
                turn['heard_actor_hypotheses']=list(active)
                turn['speaking']=[]
                turn['basis']='uncertain'
    return result


def speech_disputes(shots,turns,start):
    result=[]
    for shot in shots:
        for turn in turns:
            a=max(shot['source_start_s']-start,turn['start_s']);b=min(shot['source_end_s']-start,turn['end_s'])
            if b-a<.3:continue
            unresolved_audio=bool(turn.get('heard_actor_hypotheses')) and turn['basis']=='uncertain'
            if unresolved_audio or set(shot['speaking'])!=set(turn['speaking']) or not set(turn['speaking'])<=set(shot['visible']):
                result.append(dict(span_id='d'+str(len(result)+1),start_s=a,end_s=b))
    return result


def inspect_disputed_mouths(story,window,proxy,people,spans):
    """Dense muted frames resolve a modality disagreement without its proposed answer."""
    from PIL import Image,ImageDraw
    from .media import _run,sha256_file
    base=story.root/'source_speakers'/window['window_id']/'silent_disputes';base.mkdir(parents=True,exist_ok=True)
    batches=[];page=[];count=0
    for span in spans:
        n=max(3,math.ceil((span['end_s']-span['start_s'])*4))
        if page and count+n>48:batches.append(page);page=[];count=0
        page.append((span,n));count+=n
    if page:batches.append(page)
    results={}
    for batch_no,batch in enumerate(batches,1):
        folder=base/json_sha(dict(proxy_sha256=proxy['sha256'],people=people,spans=batch,sampling_fps=4))[:16]
        folder.mkdir(exist_ok=True)
        frames=[]
        for span,n in batch:
            for i in range(n):
                t=span['start_s']+(span['end_s']-span['start_s'])*(i+.5)/n
                target=folder/f'{span["span_id"]}_{i}.jpg'
                _run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(t),'-i',proxy['path'],
                    '-frames:v','1','-vf','scale=320:-2','-threads','1',str(target)])
                frames.append((span['span_id'],t,target))
        board=Image.new('RGB',(1280,200*math.ceil(len(frames)/4)),'#eeeeee');draw=ImageDraw.Draw(board)
        for i,(key,t,path) in enumerate(frames):
            x,y=(i%4)*320,(i//4)*200
            with Image.open(path) as picture:board.paste(picture.convert('RGB'),(x,y))
            draw.text((x+4,y+181),f'{key} +{t:.3f}s',fill='black')
        image=folder/f'frames_{batch_no}.jpg';temporary=image.with_name(image.stem+'.part.jpg');board.save(temporary,quality=92)
        if image.exists():
            if sha256_file(image)!=sha256_file(temporary):raise LibraryStopped('dense_frame_media_changed_no_overwrite')
            temporary.unlink()
        else:temporary.replace(image)
        wanted={s['span_id'] for s,_ in batch}
        def valid(v):
            rows=v.get('observations') if isinstance(v,dict) else None
            if not isinstance(rows,list) or len(rows)!=len(wanted) or {r.get('span_id') for r in rows}!=wanted:
                raise ValueError('observe each dense-frame interval once')
            bounds={span['span_id']:span for span,_ in batch}
            for row in rows:
                parts=row.get('segments');span=bounds[row['span_id']]
                if not isinstance(parts,list) or not parts:raise ValueError('split changes into actual time segments')
                cursor=span['start_s']
                for r in parts:
                    a,b=r.get('start_s'),r.get('end_s')
                    if (any(type(t) not in (int,float) or not math.isfinite(t) for t in (a,b))
                            or abs(a-cursor)>.05 or not a<b<=span['end_s']+.05):raise ValueError('cover the exact span with disjoint video-local seconds')
                    cursor=b;visible=r.get('visible')
                    if not isinstance(visible,list) or not set(visible)<=set(people):raise ValueError('known visible IDs required')
                    if any(not isinstance(r.get(k),list) or not set(r[k])<=set(visible)
                           for k in ('speaking','gesture_candidates','concealed_mouth_ids')):
                        raise ValueError('only actually visible people may show mouth, body or concealed-face evidence')
                    if set(r['speaking'])&set(r['concealed_mouth_ids']):raise ValueError('concealed mouths are not observed lip motion')
                    if type(r.get('uncertain')) is not bool or not r.get('evidence'):raise ValueError('explicit uncertainty and frame evidence required')
                if abs(cursor-span['end_s'])>.05:raise ValueError('do not omit the end of a disputed span')
            return v
        prompt=('只比较图中连续静音画面。没有声音、新台词、故事角色或前次判断。每格标注区间ID与实际相对秒。'
            '按相邻帧辨认嘴部变化，固定牙齿、静态张嘴、微笑和物品图案不是讲话运动。'
            '按外观表的耳形、头部花纹与稳定配色区分人物，同色不代表同一人。'
            '每个span细分为segments：人物、嘴动或动作改变就分段，不把后半段才开始的嘴动标到整个区间。'
            '每个span必须完整覆盖输入起止；时间沿用格子标注的视频本地秒，不从span重新归零，不把第一格采样时刻当区间起点。'
            'speaking只列有明确可见讲话嘴动的人，动画允许一次清晰张闭配合讲话姿态；单纯转身或遮挡造成的形变不算。'
            'gesture_candidates列有可见头部/躯干动作的人，不表示已确认讲话。单独手部无法确认归属时不列人物。'
            'concealed_mouth_ids只列头部身份清楚但真实嘴部被面具、头盔或固定非生理面部造型遮住的人。'
            '这只表示可考虑后期配音，绝不表示原片已发生嘴动；普通清晰可见的闭嘴、微笑不能列入此类。'
            '无声不能判断职业、动机或音色，不依据字幕；不确定就标uncertain。'
            '返回JSON {"observations":[{"span_id":"d1","segments":[{"start_s":0,"end_s":1,"visible":[],"speaking":[],"gesture_candidates":[],"concealed_mouth_ids":[],"uncertain":false,"evidence":"本小段实际帧与动作依据"}]}]}。\n'+json.dumps(dict(people=people,spans=[s for s,_ in batch]),ensure_ascii=False))
        from .pipeline import _material_ledgers
        import re
        base_stage=f'material_scene_discovery_speechcheck_{window["window_id"]}_{batch_no}'
        stage=base_stage+'_'+json_sha(dict(prompt=prompt,media_sha256=sha256_file(image)))[:12]
        # Reuse exact received legacy stage aliases; every new input gets its
        # own stage and immutable image path instead of replacing paid evidence.
        found=False
        for parent,ledger in _material_ledgers(story.state):
            for call in reversed(ledger['calls']):
                if call['name']!=base_stage and not call['name'].startswith(base_stage+'_'):continue
                if call['status'] not in {'received','failed_known'}:raise LibraryStopped('speechcheck_prior_outcome_unknown_no_replay')
                if call['status']!='received':continue
                request=json.loads((parent/'calls'/call['id']/'request.json').read_text())
                if json_sha(request)!=call['request_sha256']:raise LibraryStopped('speechcheck_prior_request_changed')
                if request['arguments']['prompt'].split('\n上次输出未通过本地协议校验。')[0]==prompt and request.get('media_sha256')==sha256_file(image):
                    stage=re.sub(r'_repair2?$','',call['name']);found=True;break
            if found:break
        answer=story.call(stage,prompt,valid,str(image),image=True)
        results.update({r['span_id']:r for r in answer['observations']})
        write_json(folder/f'review_{batch_no}.json',dict(media_sha256=sha256_file(image),spans=[s for s,_ in batch],answer=answer))
    return results


def verify_source_speakers(story,window,proxy,shots):
    """Read original sound with masked pictures, without script, roles or old labels."""
    from .audio_review import review_audio_video
    from omni_story.api import QwenAPI
    from .media import _run,sha256_file,verify_source
    source=story.route.sources[window['source_sha256']]
    folder=story.root/'source_speakers'/window['window_id'];folder.mkdir(parents=True,exist_ok=True)
    media=folder/'source_av.mp4'
    spec=dict(source_sha256=source['sha256'],picture_sha256=proxy['sha256'],
        start_s=window['source_start_s'],end_s=window['source_end_s'])
    receipt=folder/'binding.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved!=dict(spec,media_sha256=sha256_file(media)):raise LibraryStopped('source_speaker_media_changed')
    else:
        _run(['ffmpeg','-v','error','-nostdin','-y','-i',proxy['path'],'-ss',str(spec['start_s']),
            '-i',str(verify_source(source)),'-t',str(spec['end_s']-spec['start_s']),
            '-map','0:v:0','-map',f"1:{source['audio_stream_index']}",'-c:v','copy','-c:a','aac',
            '-b:a','96k','-ac','2','-map_metadata','-1',str(media)])
        write_json(receipt,dict(spec,media_sha256=sha256_file(media)))
    people={a:story.actors[a]['appearance'] for shot in shots for a in shot['visible']}
    prompt=('只听原动画声音并看实际动作，核对谁在发声。没有新剧本、故事角色或之前的讲话标签。'
        '人物外观表只是待核对的身份，字幕已隐藏。每段只列看得清的人物visible。'
        '实际嘴部张闭与人声同步才用mouth_motion；面具/头盔遮嘴时，只有明确随声音同步、区别于旁听的'
        '身体/头部动作才能用synchronous_gesture。静止牙齿、居中、持有物品、被问到都不证明讲话。'
        '只听到画外声、不能确认归属、多人重叠或无人讲话，speaking用[]，basis用none或uncertain。'
        '按真实发声变化细分时间，不将一个窗口或整镜头都算同一个人在说。完整记录静默、倾听段为none，'
        '无法判断的段为uncertain；尽量覆盖整段视频，不只摘讲话片段。不要猜职业、老板顾客。'
        '返回JSON {"turns":[{"start_s":0,"end_s":1,"visible":[],"speaking":[],"basis":"none",'
        '"evidence":"实际声音和动作依据"}],"limitations":[]}。时间是本片从0开始的秒数。\n'+
        json.dumps(dict(people=people,duration_s=spec['end_s']-spec['start_s']),ensure_ascii=False))
    from .pipeline import _material_ledgers
    source_api=QwenAPI();source_api.cfg['reference_fps_requested']=6.0
    received=None
    for parent,_ in list(_material_ledgers(story.state))[1:]:
        old=parent/'autonomous_material/source_speakers'/window['window_id']
        if not (old/'binding.json').exists() or not (old/'review/state.json').exists():continue
        binding=json.loads((old/'binding.json').read_text())
        if {k:binding.get(k) for k in spec}!=spec:continue
        old_media=old/'source_av.mp4'
        if sha256_file(old_media)!=binding['media_sha256']:raise LibraryStopped('prior_source_speaker_media_changed')
        state=json.loads((old/'review/state.json').read_text())
        if state.get('status') not in {'received','rejected'}:raise LibraryStopped('prior_source_speaker_outcome_unknown_no_replay')
        request=json.loads((old/'review/request.json').read_text()) if (old/'review/request.json').exists() else {}
        if request.get('prompt')!=prompt or request.get('configuration',{}).get('reference_fps_requested')!=6.0:continue
        received=review_audio_video(old_media,old/'review',prompt=prompt,api=source_api)
        story.state.set_artifact('source_speaker_reuse',dict(window_id=window['window_id'],path=str(old),binding=binding))
        break
    if received is None:received=review_audio_video(media,folder/'review',prompt=prompt,api=source_api)
    if received.get('http_status')!=200:
        raise LibraryStopped('source_speaker_invalid_received_reply')
    from .contracts import parse_model_json
    observed=validate_source_turns(normalize_source_turns(received.get('parsed') or parse_model_json(received['reply']),people),
        people,spec['end_s']-spec['start_s'])
    disputes=speech_disputes(shots,observed['turns'],spec['start_s'])
    resolved=inspect_disputed_mouths(story,window,proxy,people,disputes) if disputes else {}
    verified=[]
    for shot in shots:
        for i,turn in enumerate(observed['turns']):
            a=max(shot['source_start_s'],spec['start_s']+turn['start_s'])
            b=min(shot['source_end_s'],spec['start_s']+turn['end_s'])
            if b-a<.3:continue
            dispute=next((d for d in disputes if abs(d['start_s']-(a-spec['start_s']))<.001
                and abs(d['end_s']-(b-spec['start_s']))<.001),None)
            parts=resolved[dispute['span_id']]['segments'] if dispute else [dict(start_s=a-spec['start_s'],end_s=b-spec['start_s'],
                visible=shot['visible'],speaking=turn['speaking'],gesture_candidates=[],concealed_mouth_ids=[],
                uncertain=turn['basis']=='uncertain',evidence=turn['evidence'])]
            for j,part in enumerate(parts):
                if part['uncertain']:continue
                low=max(a,spec['start_s']+part['start_s']);high=min(b,spec['start_s']+part['end_s'])
                if high-low<.3:continue
                visible=part['visible'];speaking=part['speaking'];basis=turn['basis']
                if dispute:
                    audible=bool(turn['speaking'] or turn.get('heard_actor_hypotheses'))
                    if speaking and audible:basis='mouth_motion'
                    elif (basis=='synchronous_gesture' and audible and turn['speaking'] and
                            set(turn['speaking'])<=set(part['gesture_candidates'])):
                        speaking=turn['speaking']
                    elif not speaking:basis='none'
                    else:continue
                row=deepcopy(shot);evidence=part['evidence']
                row.update(source_start_s=low,source_end_s=high,slot_id='shot_'+json_sha([shot['slot_id'],i,j,low,high])[:12],
                    visible=visible,speaking=speaking,listeners=[p for p in visible if p not in speaking],
                    dub_speaker_ids=part['concealed_mouth_ids'] if not speaking else [],
                    clear_face=[p for p in shot['clear_face'] if p in visible],speech_evidence=evidence,
                    speaking_verification=dict(basis=basis,evidence=evidence,media_sha256=sha256_file(media),
                        dispute_id=dispute['span_id'] if dispute else None))
                if dispute and set(speaking)!=set(shot['speaking']):
                    # Superseded visual speech claims stay in the received reply,
                    # not in the canonical action/events sent to the writer.
                    row.update(action=evidence,addressee_ids=[],addressee_evidence='not established in this recheck',
                        events=[dict(action=evidence,subject_ids=speaking or part['gesture_candidates'],addressee_ids=[])])
                row['events']=[dict(e,event_id=row['slot_id']+'_event_'+str(k+1))
                    for k,e in enumerate(row['events']) if set(e['subject_ids'])<=set(visible)]
                verified.append(row)
    write_json(folder/'verified.json',dict(observed=observed,disputes=disputes,resolved=resolved,shots=verified))
    return verified


def review_cast_pictures(story,cast,validator):
    """Check spatial relationships against actual muted frames, not a proposal's prose."""
    from PIL import Image,ImageDraw
    from .media import _run,sha256_file
    shots=sorted(story.shots,key=lambda s:(s['source_sha256'],s['source_start_s']))
    binding=dict(cast=cast,frames=[dict(window_id=s['window_id'],
        proxy_sha256=story.observations[s['window_id']]['proxy']['sha256'],
        source_s=(s['source_start_s']+s['source_end_s'])/2) for s in shots])
    folder=story.root/'cast_pictures'/json_sha(binding)[:16];folder.mkdir(parents=True,exist_ok=True)
    board=Image.new('RGB',(1280,200*math.ceil(len(shots)/4)),'#eeeeee');draw=ImageDraw.Draw(board)
    for i,shot in enumerate(shots):
        proxy=story.observations[shot['window_id']]['proxy']
        window=story.windows[shot['window_id']]
        source_s=binding['frames'][i]['source_s'];image=folder/f'frame_{i+1}.jpg'
        _run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(source_s-window['source_start_s']),
            '-i',proxy['path'],'-frames:v','1','-vf','scale=320:-2','-threads','1',str(image)])
        x,y=(i%4)*320,(i//4)*200
        with Image.open(image) as picture:board.paste(picture.convert('RGB'),(x,y))
        draw.text((x+4,y+181),f'{i+1} source {source_s:.3f}s',fill='black')
    media=folder/'scene.jpg';temporary=folder/'scene.part.jpg';board.save(temporary,quality=92)
    if media.exists():
        if sha256_file(media)!=sha256_file(temporary):raise LibraryStopped('cast_frame_media_changed_no_overwrite')
        temporary.unlink()
    else:temporary.replace(media)
    prompt=('直接检查静音、已遮字幕的实际画面，独立判断这组新故事角色是否能由画面关系合理承担。'
        'cast只是待审分配，不是答案；没有提供上次的角色解释，不能自行补造摊位内外或交接动作。'
        '这是后期配音创作的角色适配，不是考证原动画人物的真实职业或原剧情。'
        '允许用普通交谈、静态表演和反应承载新对白；职业、商品、问价或争执可由剧本建立。'
        '缺少原故事的场所、道具、交易或特定动作，本身不是否决理由；只检查所提人物关系是否可合理改编、是否明显违背实际动作方向。'
        '人物拿着、举着商品既可能是买家查看也可能是卖家展示，不能单凭持物就认定职业。'
        '结合桌子前后、人物朝向、物件交接和连续反应核对；不把画面外的动作当成已看到。'
        '允许合理改编原职业，不要求知道原动画剧情，但若改编明显违背所见关系就supported=false。'
        'supported=true表示可承担该新故事角色，不表示画面已经证明其原职业；人物身份无法确认或关系明显矛盾则false，并具体说明。'
        '每项必须严格按cast中的role与actor_id对应检查，不能审另一个人或自行调换映射。必须逐角色返回JSON '
        '{"checks":[{"role":"故事角色","actor_id":"cast中对应的演员ID","supported":true,"evidence":"实际图号、站位和动作依据或矛盾"}]}。\n'+
        json.dumps(dict(cast=cast,actors={a:story.actors[a]['appearance'] for a in story.actors}),ensure_ascii=False))
    def identity_valid(value):
        checked=validator(value)
        if any(row.get('actor_id')!=cast.get(row['role']) for row in checked['checks']):
            raise ValueError('Check each role against its exact cast actor_id; do not swap the proposed people')
        return checked
    stage='material_scene_discovery_castscene_'+json_sha(dict(binding=binding,media_sha256=sha256_file(media),prompt=prompt))[:16]
    answer=story.call(stage,prompt,identity_valid,str(media),image=True)
    write_json(folder/'review.json',dict(binding=binding,media_sha256=sha256_file(media),review=answer))
    return answer


def direct_cast(story,feedback=None):
    """GLM chooses and checks story roles after neutral source observation."""
    from .material_plan import validate_cast
    if feedback:story.feedback.append(feedback)
    prior=set();voice_shortages={}
    while True:
        story.cast_round=getattr(story,'cast_round',0)+1
        context=dict(reference_roles=[dict(role=role,utterances=[dict(text=r['text'],function=r.get('function'),
            addressee_roles=r.get('addressee_roles')) for r in story.route.lines if r['role']==role]) for role in story.roles],
            active_scene=story.active_candidate,actors=story.actors,shots=story.shots,locked_cast=story.cast,
            alternatives=[dict(rank=i,**c) for i,c in enumerate(story.browser_result['candidates'])],feedback=story.feedback[-3:],
            scene_ids=[s['scene_id'] for s in story.route.scenes])
        context['verified_speech_actors']=sorted({a for s in story.shots if s.get('speaking_verification',{}).get('basis')
            in {'mouth_motion','synchronous_gesture'} for a in s['speaking']})
        context['dubbing_view_actors']=sorted({a for s in story.shots for a in s.get('dub_speaker_ids',[])})
        if voice_shortages:context['source_voice_shortages']=voice_shortages
        prompt=('为已实际看片的同一连续场景分配故事角色，不再用关键词重新检索。先理解参考双方的关系与对话作用，'
            '再按实际人物的动作方向、场景位置和相互关系合理选角；不能只按讲话秒数把角色颠倒。'
            'source speaking_verification是独立原声动作核对，不能把另一人正在说话写成听者。'
            '有对白角色需有verified_speech_actors或dubbing_view_actors中的本人画面。dub_speaker_ids是后期配音用途，不是原片嘴动事实。'
            'stylized_animation_v1表示用户认可动画简化口型的稳定对话，允许角色清楚出镜的普通静态表演配音；不能因此谎称原片在开口。'
            '原声音色会另用真实原声按人物独立核验。不能因遮嘴看不见嘴动就倒换人物职业。locked_cast为空表示尚未锁角，允许比较并更换已看过的候选场景；锁角后才保持演员与连续场景。'
            '可以适配原片人物的职业和剧情，但须解释新故事关系如何从这组画面建立，不能声称未发生的动作。'
            'locked_cast非空后不可换角。普通对话和反应可以配音改编，不要求精确商品和动作。'
            '若本组无法成立，可在锁角前返回{"action":"candidate","rank":候选索引,"reason":"具体缺口"}；'
            '若确需更多同场画面返回{"action":"expand","reason":"实际缺什么"}。'
            '完成返回{"action":"finish","cast":{"故事角色":"actor_id"},'
            '"scene_windows":{"场景ID":["本组已核对的窗口ID"]},"role_evidence":{"故事角色":"动作位置和关系的实际依据"},'
            '"continuity_plan":"如何保持人物与场景稳定","reason":"分配依据"}。\n'+
            json.dumps(context,ensure_ascii=False))
        def decision_key(v):
            return json_sha(dict(cast=v['cast'],scene_windows=v['scene_windows'])) if v['action']=='finish' else json_sha(v)
        def valid(v):
            if not isinstance(v,dict):raise ValueError('cast action required')
            if v.get('action')=='candidate':
                if story.cast is not None or type(v.get('rank')) is not int or not 0<=v['rank']<len(story.browser_result['candidates']):raise ValueError('cannot change locked cast or unknown candidate')
                if story.browser_result['candidates'][v['rank']]['scene_group_id']==story.active_candidate['scene_group_id']:
                    raise ValueError('active scene is already inspected; choose finish, a genuinely different candidate, or expand with a concrete gap')
            elif v.get('action')=='finish':
                validate_cast(v.get('cast'),story.roles,story.actors)
                if set(v['cast'].values())&set(voice_shortages):
                    raise ValueError('A proposed actor has no verified original voice in the reviewed source contexts; choose another suitable same-scene performer or expand with concrete evidence')
                if story.cast is not None and v['cast']!=story.cast:raise ValueError('story cast is locked')
                if set(v.get('scene_windows',{}))!={s['scene_id'] for s in story.route.scenes}:raise ValueError('all dialogue scenes require windows')
                if set(v.get('role_evidence',{}))!=set(story.roles) or any(not v['role_evidence'][r] for r in story.roles):raise ValueError('each role needs grounded relationship evidence')
                if not v.get('continuity_plan'):raise ValueError('continuity explanation required')
                story.validate_selection(v)
            elif v.get('action')!='expand':raise ValueError('unknown cast action')
            if decision_key(v) in prior:
                raise ValueError('This cast/window decision was already checked and rejected without new evidence. Address its feedback: choose a different viable decision/candidate or expand for concrete missing evidence; do not repeat the same cast and windows with a new explanation.')
            return v
        action=story.call('material_scene_discovery_decide_cast_'+str(story.cast_round),prompt,valid)
        prior.add(decision_key(action))
        if action['action']=='candidate':
            before=deepcopy(story.active_ranges)
            story.use_candidate(action['rank'])
            if story.active_ranges!=before:voice_shortages={}
            continue
        if action['action']=='expand':
            before=len(story.browser.visual)
            previous=story.active_candidate['scene_group_id']
            story.browser_result=story.browser.run(feedback=dict(reason=action.get('reason'),locked_cast=story.cast,
                actors=story.actors,source_voice_shortages=voice_shortages,
                current_scene_group_id=previous,
                selection_constraints=('锁角后只扩展同一组人物。' if story.cast is not None else
                    '尚未锁角。本组原声不足时可改选其他已独立看过的场景，不必重复检索；不将暂选场景当作用户指定。')))
            selected=story.browser_result['candidates'][0]['scene_group_id']
            if len(story.browser.visual)<=before and selected==previous:
                raise LibraryStopped('cast_expansion_no_new_video_evidence')
            story.use_candidate(0)
            if selected!=previous:voice_shortages={}
            continue
        def review_valid(v):
            rows=v.get('checks') if isinstance(v,dict) else None
            if (not isinstance(rows,list) or len(rows)!=len(story.roles) or {r.get('role') for r in rows}!=set(story.roles)
                    or any(type(r.get('supported')) is not bool or not r.get('evidence') for r in rows)):
                raise ValueError('independently check each story role')
            return v
        reviewed=review_cast_pictures(story,action['cast'],review_valid)
        write_json(story.root/f'cast_review_{story.cast_round}.json',dict(proposal=action,review=reviewed))
        if not all(r['supported'] for r in reviewed['checks']):
            story.feedback.append(dict(problem='角色关系证据不足',cast=action['cast'],scene_windows=action['scene_windows'],review=reviewed));continue
        story.cast=action['cast'];story.selection=action
        if hasattr(story,'source_voices') and story.voice_service is None:
            from .character_voice import VoiceUnavailable
            try:
                _,provisional=story.lock()
                story.voice_service=story.source_voices(provisional)
            except VoiceUnavailable as exc:
                # Audition is a casting prerequisite, before script/timing lock.
                # A service/unknown outcome is not evidence that an actor is unfit.
                actor=getattr(exc,'actor_id',None)
                if actor is None:raise
                voice_shortages[actor]=dict(status='insufficient_verified_original_voice',evidence=str(exc))
                story.state.set_artifact('source_voice_cast_shortage',dict(scene=story.active_candidate['scene_group_id'],
                    actor_id=actor,evidence=str(exc)))
                story.feedback.append(dict(problem='所提人物无法提供足够已核验原声音色，尚未最终锁角',
                    actor_id=actor,evidence=str(exc),instruction='尚未锁角：优先核对同场可用原声；若同场不能满足，可换其他已看片的连续场景。不得颠倒关系凑音色或借用其他人的音色。'))
                story.cast=None;story.selection=None
                continue
        write_json(story.root/'cast_lock.json',dict(policy='source_grounded_cast_v1',proposal=action,review=reviewed))
        return


def audit_line_bindings(lock,lines,rows,shots,voices):
    """Validate actual scheduled actor/voice links after all timing changes."""
    by_scene={s['scene_id']:s for s in lock['scenes']};by_shot={s['slot_id']:s for s in shots}
    checks=[]
    for line in lines:
        actor=by_scene[line['scene_id']]['role_bindings'][line['role']]['role_id']
        parts=[r for r in rows if r.get('line_id')==line['line_id']];errors=[]
        if line.get('voice')!=voices.get(line['role']):errors.append('voice_id_does_not_match_locked_role')
        if not parts:errors.append('no_picture_for_utterance')
        visible_speakers=set()
        for part in parts:
            shot=by_shot.get(part.get('slot_id'))
            if shot is None:errors.append('unknown_scheduled_shot');continue
            visible_speakers.update(shot['speaking'])
            if set(shot['speaking'])-{actor}:errors.append('another_actor_speaking_under_this_voice')
            if part.get('voice_role',line['role'])!=line['role']:errors.append('scheduled_voice_role_changed')
        checks.append(dict(line_id=line['line_id'],role=line['role'],actor_id=actor,voice_id=line.get('voice'),
            visible_speaking_actor_ids=sorted(visible_speakers),segment_ids=[r.get('segment_id') for r in parts],
            passed=not errors,issues=sorted(set(errors))))
    return dict(policy='locked_actor_voice_timeline_v1',passed=all(c['passed'] for c in checks),checks=checks)
