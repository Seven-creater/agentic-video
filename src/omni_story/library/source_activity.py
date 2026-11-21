"""Follow a witnessed activity in continuous source footage and locate its uses in the edit."""
import json
import math
import re
import subprocess

from .edit_review import cut_spans
from .render import _probe, _source_duration


class BoundaryUnclear(ValueError):
    """The source did not provide a verified endpoint for an observed activity."""


def source_window(shot, cut, frames, fps):
    """Same trim, speed, loop/hold limits as the picture renderer, in half-open source frames."""
    first = math.ceil((shot['source_start_s'] + cut['offset']) * fps - 1e-6)
    limit = math.floor(shot['source_end_s'] * fps + 1e-6)
    # A held tail or a loop does not introduce any further source material.
    length = min(limit - first, max(1, math.ceil(frames * cut['speed'] - 1e-6)))
    return first, first + length


def trace_activity(out, stage, issue, cut, shot, sources, fps, ask, ff):
    """Track one actor through source edits. An open page edge continues into the next page."""
    from PIL import Image, ImageDraw

    digest = shot['source_sha256']
    source = sources[digest]
    limit = math.ceil(_source_duration(_probe(source, 'ffprobe')) * fps)
    span = issue['interval']
    first, last = source_window(shot, cut, span['end'] - span['start'], fps)
    pivot = (first + last) // 2
    size = 16 * fps
    initial = max(0, min(pivot - 5*fps, limit-size))
    evidence = []

    def read_page(start, witness, *, check_end=True):
        end = min(limit,start+size)
        name = f'{stage}_sequence_{start}'
        video = out/(name+'.mp4')
        ff('-ss',start/fps,'-i',source,'-t',(end-start)/fps,'-an','-vf',f'fps={fps},scale=640:-2',
           '-threads',2,'-c:v','libx264','-crf',20,video)
        detector = subprocess.run(['ffmpeg','-v','info','-i',str(video),'-an','-vf',
            "scale=192:108,select='gt(scene,0.32)',showinfo",'-f','null','-'],
            check=True,capture_output=True,text=True)
        changes = sorted({start+round(float(t)*fps) for t in re.findall(r'pts_time:([0-9.]+)',detector.stderr)})
        edges = [start,*(n for n in changes if start<n<end),end]
        rows = []
        for left,right in zip(edges,edges[1:]):
            for a in range(left,right,3*fps):
                rows.append((a,min(right,a+3*fps)))
        anchor = next(i for i,(a,b) in enumerate(rows,1) if a<=witness<b)
        # Four ordered frames of each continuous interval reveal whether the actor or only the camera moves.
        samples = [[a+round((b-a-1)*phase) for phase in (.03,.34,.66,.96)] for a,b in rows]
        indices = sorted({n for row in samples for n in row})
        select = '+'.join(f'eq(n,{n-start})' for n in indices)
        width,height = 256,144
        raw = subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-an','-vf',
            f"select='{select}',scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",'-fps_mode','passthrough','-threads','1','-pix_fmt','rgb24','-f','rawvideo','-'])
        stride = width*height*3
        if len(raw)!=stride*len(indices):
            raise BoundaryUnclear('source page did not decode all required witness frames')
        pictures = {n:Image.frombytes('RGB',(width,height),raw[i*stride:(i+1)*stride]) for i,n in enumerate(indices)}
        sheet = Image.new('RGB',(width*4,(height+18)*len(rows)),'#202428')
        draw = ImageDraw.Draw(sheet)
        for i,row in enumerate(samples):
            for j,n in enumerate(row):
                x,y = j*width,i*(height+18)
                sheet.paste(pictures[n],(x,y+18))
                draw.text((x+4,y+2),f'R{i+1}   {(n-start)/fps:.2f}s',fill='white')
        board = out/(name+'.jpg')
        sheet.save(board,quality=94)
        prompt = ('从基点所在行沿原片前后追同一当事人的同一次离场或活动。每行是同一连续片段的四帧，'
            '从左到右再从上到下。先看身体、脚与帐篷/地面的相对位置，再判断是否仍在位移。'
            '近景裁掉腿仍可在走；停稳面对另一个人时这一次行走已经结束，手里还拿着东西也可以站定。'
            '途中切到对方的反应，按前后行判断；后来再次出发属于下一次。'
            '给与基点相连的起始行 first、末行 last（整数，从1开始），分别写前一状态、动作延续的可见证据、'
            '之后首次新状态。证据不足填 status=unclear。begin_s/end_s 仅在动作于行内开始或结束时填页内秒数，'
            '恰在换镜处或跨页时省略。返回 {"status":"seen","first":1,"last":2,"before":"...",'
            '"during":"...","after":"..."}。')
        bounds = {i+1:[round((a-start)/fps,3),round((b-start)/fps,3)] for i,(a,b) in enumerate(rows,0)}
        prompt += json.dumps(dict(anchor_row=anchor,anchor_second=(witness-start)/fps,rows=bounds,observed=issue['seen']),ensure_ascii=False)

        def validate(v):
            if v.get('status')=='unclear' and (v.get('during') or v.get('reason')):
                return v
            a,b = v.get('first'),v.get('last')
            if v.get('status')!='seen' or type(a) is not int or type(b) is not int or not 1<=a<=anchor<=b<=len(rows):
                raise ValueError('give a contiguous range of visible row numbers containing the anchor')
            if not all(isinstance(v.get(k),str) and v[k].strip() for k in ('before','during','after')):
                raise ValueError('describe the preceding state, continued action and subsequent state')
            for key,i in (('begin_s',a),('end_s',b)):
                n = v.get(key)
                if n is not None and (type(n) not in (int,float) or not bounds[i][0]-.001<=n<=bounds[i][1]+.001):
                    raise ValueError('optional seconds must fall inside the row named for that endpoint')
            first_frame,last_frame = limits(v)
            if not first_frame<=witness<last_frame:
                raise ValueError('the witnessed source frame must fall inside the activity')
            return v

        def limits(v):
            a,b = rows[v['first']-1][0],rows[v['last']-1][1]
            if v.get('begin_s') is not None:
                a = max(a,start+round(v['begin_s']*fps))
            if v.get('end_s') is not None:
                b = min(b,start+round(v['end_s']*fps))
            return a,b

        reply = ask(name,prompt,validate,board)
        if reply['status']=='unclear':
            reply = ask(name+'_video',prompt+'附件换为同一段连续视频，按同一页的行号与秒数表复核。',validate,video)
        evidence.append(dict(start=start,end=end,rows=rows,observation=reply))
        if reply['status']!='seen':
            raise BoundaryUnclear(reply.get('during') or reply.get('reason'))
        left,right = limits(reply)
        if right < end and check_end:
            # A crop can remove all evidence of the feet without ending the movement.
            # Verify the first positive change of state on uninterrupted video before closing a trace.
            marked = out/(name+'_labeled.mp4')
            labels = [f"drawtext=text=R{i}:x=8:y=8:fontsize=26:box=1:boxcolor=black:fontcolor=white:"
                      f"enable='gte(t,{(a-start)/fps:.6f})*lt(t,{(b-start)/fps:.6f})'"
                      for i,(a,b) in enumerate(rows,1)]
            ff('-i',video,'-an','-vf',','.join(labels),'-c:v','libx264','-threads',2,'-crf',21,marked)
            def check_stop(v):
                if v.get('status')=='continues' and v.get('evidence'):
                    return v
                i=v.get('stopped_row')
                if (v.get('status')!='ended' or type(i) is not int or not anchor<=i<=len(rows)
                        or v.get('proof') not in ('fixed_position','new_interaction','scene_ends')
                        or not v.get('evidence')):
                    raise ValueError('give the first row with positive evidence of a stop, or continues')
                t=v.get('at_s')
                if t is not None and (type(t) not in (int,float) or not bounds[i][0]-.001<=t<=bounds[i][1]+.001):
                    raise ValueError('at_s must be inside the stopped row')
                if stop_frame(v)<=witness:
                    raise ValueError('the stopped state must start after the witnessed ongoing action')
                return v
            def stop_frame(v):
                if v.get('at_s') is None:
                    return rows[v['stopped_row']-1][0]
                return start+round(v['at_s']*fps)
            stop=ask(name+'_stop',
                '独立看这一段连续视频，标签 R 为区间号。追踪基点人物实际动作直至首次可确认的停止。'
                '镜头裁掉腿、切正脸/侧脸、冒汗、思想泡泡、只拍对方，都不能单独证明本人的走路已经结束。'
                '顺着相同衣物和身体位置看头肩起伏、后方帐篷边或竖杆是否掠过。人物仍拿着行李但已经站定面对对方，是停止的正证据。'
                '找最早能看到本人相对固定地物站定、或和另一角色展开新互动的行，也可选明确切到全新场景。'
                '紧跟基点继续看，别跳过第一次站定。若停止发生在一行中间，at_s填页内秒数；刚换镜即确认则省略。'
                '如果到末尾仍未能确认停止，写 continues 并说明末尾画面。'
                '返回 {"status":"ended","stopped_row":整数,"proof":"fixed_position/new_interaction/scene_ends",'
                '"evidence":"描述仍在动的近景，及首次停稳时人与相对地物或对手的实际关系"}。'
                +json.dumps(dict(anchor_row=anchor,rows=bounds,observed=issue['seen']),ensure_ascii=False),
                check_stop,marked)
            right=end if stop['status']=='continues' else stop_frame(stop)
            evidence[-1]['end_verification']=stop
        return left,right

    left,right = read_page(initial,pivot)
    lo,hi = initial,min(limit,initial+size)
    while left==lo and lo>0:
        new_start = max(0,lo-size+2*fps)
        left,_ = read_page(new_start,min(limit-1,lo+fps),check_end=False)
        lo = new_start
    while right==hi and hi<limit:
        new_start = hi-2*fps
        _,right = read_page(new_start,hi-fps)
        hi = min(limit,new_start+size)
    return dict(source_sha256=digest,first=left,end=right,scene=cut['scene'],evidence=evidence,
                seed_cut=issue['cut'],seen=issue['seen'])


def activity_hits(cuts, shots, total, fps, traces):
    hits = {}
    if not traces:
        return hits
    for span, cut in zip(cut_spans(cuts,total,fps), cuts):
        shot = shots[cut['shot']]
        first, end = source_window(shot,cut,span['end']-span['start'],fps)
        for trace in traces:
            if shot['source_sha256'] != trace['source_sha256'] or first >= trace['end'] or end <= trace['first']:
                continue
            hits.setdefault(span['id'],[]).append(dict(
                trace=trace.get('id',trace['seed_cut']), source_sha256=shot['source_sha256'],
                first=max(first,trace['first']), end=min(end,trace['end'])))
    return hits


def review_activity_uses(out, stage, picture, cuts, shots, total, fps, lines, reference, traces, ask, ff):
    """Search the whole timeline, then distinguish intended uses of the same source action."""
    hits = activity_hits(cuts,shots,total,fps,traces)
    spans = cut_spans(cuts,total,fps)
    issues = []
    # Only review time windows containing a source match. Preserve neighboring speech and real cuts.
    for page in sorted({span['start']//(24*fps) for span in spans if span['id'] in hits}):
        targets = {s['id']:s for s in spans if s['id'] in hits and s['start']//(24*fps)==page}
        start = max(0,min(s['start'] for s in targets.values())-fps)
        end = min(total,max(s['end'] for s in targets.values())+fps)
        labels = [f"drawtext=text={k}:x=8:y=8:fontsize=24:fontcolor=white:box=1:boxcolor=black:"
                  f"enable='gte(t,{(s['start']-start)/fps:.6f})*lt(t,{(s['end']-start)/fps:.6f})'" for k,s in targets.items()]
        name = f'{stage}_{page}'; media = out/(name+'.mp4')
        ff('-ss',start/fps,'-i',picture,'-t',(end-start)/fps,'-vf',','.join(['scale=768:432','fps=15',*labels]),
           '-threads',2,'-c:v','libx264','-crf',23,'-c:a','aac','-b:a','96k',media)
        context = {k:dict(scene=s['scene'], source_actions=[t['seen'] for t in traces
                        if any(h['trace']==t.get('id',t['seed_cut']) for h in hits[k])],
                       lines=[(l['role'],l['text']) for l in lines if
                              l['start_s']*fps<s['end'] and l['end_s']*fps>s['start']]) for k,s in targets.items()}

        def validate(v):
            rows = v.get('cuts')
            if not isinstance(rows,dict) or set(rows)!=set(targets):
                raise ValueError('give one judgment for each visible target cut '+str(list(targets)))
            if any(not isinstance(row,dict) or row.get('fit') not in ('conflicts','fits','unclear') or not row.get('why') for row in rows.values()):
                raise ValueError('state conflicts/fits/unclear with visible or narrative evidence for each cut')
            return v
        response = ask(name,'以下刀号的实际源帧和已查清的同一段原片动作重叠。近景裁掉腿或物件也可能在延续该动作。'
            '结合每刀实际画面、前后配音、人物与场景，是否让人误以为当事人已经离场或正在做另一件事？'
            '来源相同不代表一定剪错：若本段确实交代同行、离场、回忆或转场，有充分依据填 fits。'
            '同一场已确认这段源动作违背剧情，不能因拍成大头、气泡或腿在画外，就当作普通反应。'
            '本场另有明确的离场、同行、回忆等叙事转折才能例外。若确有转折，在对应刀另填'
            'story_change 为交代转折的台词或分场意图原文（逐字），并在 why 解释如何交代。'
            '仍在同一互动则填 conflicts。看不清填 unclear。'
            '返回 {"cuts":{"刀号":{"fit":"conflicts/fits/unclear","why":"依据"}}}。'
            +json.dumps(dict(cuts=context,scenes=reference),ensure_ascii=False),validate,media)
        for k,row in response['cuts'].items():
            # A close-up cannot invalidate witnessed source provenance. Only a documented story change
            # can authorize reusing the same action within the scene where it was already rejected.
            match_ids={h['trace'] for h in hits[k]}
            same_scene=any(t['scene']==targets[k]['scene'] and t.get('id',t['seed_cut']) in match_ids for t in traces)
            story_text=[text for _,text in context[k]['lines']]
            for scene in reference:
                if scene.get('scene_id')==targets[k]['scene']:
                    story_text.extend(str(scene.get(field) or '') for field in ('interaction','visual_function','must_see'))
            quote=row.get('story_change')
            supported_change=isinstance(quote,str) and bool(quote.strip()) and any(quote in t for t in story_text)
            if same_scene and not supported_change:
                issues.append(dict(cut=k,kind='action_conflict',seen='本场再次使用已确认冲突的同一次源动作，剧情未交代新的行动。',
                                   local_review=row,interval=targets[k],source_overlap=hits[k]))
            elif row['fit']!='fits':
                issues.append(dict(cut=k,kind='action_conflict' if row['fit']=='conflicts' else 'action_unclear',
                                   seen=row['why'],interval=targets[k],source_overlap=hits[k]))
    return issues


def extend_action_review(out, stage, issues, picture, cuts, shots, sources, total, fps, lines, reference, ask, ff, *, known=()):
    """Persist discoveries across repairs, even if the original wide shot has already gone."""
    traces = list(known)
    pending = []
    for issue in issues:
        if issue['kind'] != 'action_conflict':
            continue
        i = int(issue['cut']) - 1
        cut = cuts[i]
        try:
            trace = trace_activity(out,f"{stage}_cut_{issue['cut']}",issue,cut,shots[cut['shot']],sources,fps,ask,ff)
        except BoundaryUnclear as exc:
            pending.append(dict(cut=issue['cut'],seen=issue['seen'],reason=str(exc)))
            continue
        trace['id'] = f"{stage}:{issue['cut']}"
        traces.append(trace)
    confirmed = {(v['cut'],v['kind']):v for v in issues}
    if traces:
        matches = review_activity_uses(out,stage+'_uses',picture,cuts,shots,total,fps,lines,reference,traces,ask,ff)
        confirmed.update({(v['cut'],v['kind']):v for v in matches})
    return list(confirmed.values()),traces,pending


def excluded_activity_ranges(out, seeds, shots, sources, scene_keys, fps, ask, ff):
    """A planner may exclude an activity for one scene while retaining other uses of that footage."""
    traces = []
    for key,reason in seeds.items():
        scene = next(s for s,keys in scene_keys.items() if key in keys)
        shot = shots[key]
        frames = max(1,round((shot['source_end_s']-shot['source_start_s'])*fps))
        item = dict(cut=key,seen=reason,interval=dict(start=0,end=frames))
        cut = dict(scene=scene,offset=0,speed=1)
        trace = trace_activity(out,'planned_activity_'+key,item,cut,shot,sources,fps,ask,ff)
        traces.append(trace)
    return traces,shot_activity_ranges(traces,shots,scene_keys,fps,scene_only=True)


def shot_activity_ranges(traces, shots, scene_keys, fps, *, scene_only=False):
    ranges = {}
    for trace in traces:
        for key in (scene_keys[trace['scene']] if scene_only else shots):
            shot = shots[key]
            if shot['source_sha256'] != trace['source_sha256']:
                continue
            start = math.ceil(shot['source_start_s']*fps-1e-6)
            end = math.floor(shot['source_end_s']*fps+1e-6)
            a,b = max(start,trace['first']), min(end,trace['end'])
            if a<b:
                ranges.setdefault(key,[]).append([a,b])
    return ranges
