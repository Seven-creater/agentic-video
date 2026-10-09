"""Generic-skill transfer on an existing rough cut; no teacher creative answers."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import time

from .contracts import parse_model_json
from .media import probe_media, sha256_file
from .pipeline import CodexMCP, _captured_reply, _http_evidence, _outcome_unknown, _usage_for
from .render import compile_library_plan, render_library_video
from .state import LibraryStopped, json_sha, write_json
from .visual_story_trial_state import VisualStoryState, authorize, get_auth, read, require, BASELINE

class TrialMCP(CodexMCP):
    def recover_received(self):
        # Never alter the outcome of any historical request, even on startup.
        self.state._reload()
        for c in self.state.data['calls'][BASELINE:]:
            if c['status']!='submitted':
                continue
            entries=_http_evidence(self.output,c['id'])
            evidence,reply=_captured_reply(entries)
            if reply:
                self.state.reconcile_received(c,reply,evidence=evidence)
                continue
            path=self.queue/(c['id']+'.response.json')
            if path.exists():
                response=read(path)
                if response['status']=='complete':
                    self.state.complete_call(c,response,usage=_usage_for(self.output,c['id']))
                else:
                    self.state.fail_call(c,response.get('error','MCP_error'),
                        uncertain=response['status']=='unknown' or _outcome_unknown(entries))

def write_once(path,value):
    path=Path(path)
    if path.exists():
        require(read(path)==value,'CPU_artifact_changed:'+str(path))
    else:
        write_json(path,value)
    return value

def command(args):
    r=subprocess.run([str(x) for x in args],capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors='replace')[-3000:])
    return r.stdout

def layout(source):
    """Only spatial black margins; no movie-specific positions or temporal cuts."""
    import av
    import numpy as np
    with av.open(source['path']) as container:
        stream=container.streams.video[0]
        width,height=stream.width,stream.height
        samples=[];target=0
        for frame in container.decode(stream):
            t=float(frame.pts*frame.time_base)
            if t+1e-9 < target:
                continue
            rgb=frame.to_ndarray(format='rgb24')
            samples.append((rgb.max(axis=2)>24).mean(axis=1))
            target += max(1,source['duration_s']/18)
    row=np.max(samples,axis=0)
    content=np.where(row>.005)[0]
    start=max(0,int(content[0])-4)//2*2
    stop=min(height,int(content[-1])+5)//2*2
    dense=np.where(row>.2)[0]
    runs=[]
    for index in dense:
        if not runs or index != runs[-1][-1]+1:
            runs.append([int(index)])
        else:
            runs[-1].append(int(index))
    longest=max(runs,key=len)
    picture_top=longest[0]//2*2;picture_bottom=(longest[-1]+2)//2*2
    require(stop>start and picture_bottom-picture_top>height*.15,'spatial_ROI_not_reliable')
    return {'crop':[0,start,width,stop-start],
            'picture_crop':[0,picture_top,width,picture_bottom-picture_top],
            'method':'uniform navigation pixel black-margin union and largest dense-row run',
            'limitations':'Spatial framing only; not subtitle semantics, shots or editing answers.'}

def proxy(source,start,end,base,*,roi=None,label='window'):
    spec=dict(source_sha256=source['sha256'],source_start_s=float(start),source_end_s=float(end),
              roi=roi,fps=30,audio=False,label=label)
    folder=base/'media'/json_sha(spec)[:20]
    record=folder/'lineage.json'
    if record.exists():
        saved=read(record);require(saved['spec']==spec and sha256_file(saved['path'])==saved['sha256'],'proxy_cache_changed')
        return saved
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/'window.mp4'
    duration=end-start
    bitrate=min(1_000_000,int(7_400_000*8*.75/duration))
    filters=[]
    if roi:
        x,y,w,h=roi;filters.append(f'crop={w}:{h}:{x}:{y}')
    filters.extend(["scale=w='if(gte(iw,ih),min(720,iw),-2)':h='if(gte(iw,ih),-2,min(720,ih))'",'fps=30','setsar=1'])
    command(['ffmpeg','-nostdin','-y','-v','error','-ss',f'{start:.9f}','-i',source['path'],
             '-t',f'{duration:.9f}','-map',f"0:{source['video_stream_index']}",'-an',
             '-vf',','.join(filters),'-c:v','libx264','-preset','fast','-threads','2',
             '-b:v',bitrate,'-maxrate',bitrate,'-bufsize',bitrate*2,'-pix_fmt','yuv420p',
             '-movflags','+faststart',target])
    measured=probe_media(target)
    require(target.stat().st_size<8_000_000 and not any(s['codec_type']=='audio' for s in measured['streams']), 'proxy_size_or_audio')
    require(abs(measured['duration_s']-duration)<.075,'proxy_time_mapping')
    return write_once(record,dict(spec=spec,kind='continuous_window',path=str(target),sha256=sha256_file(target),
        source_sha256=source['sha256'],source_start_s=float(start),source_end_s=float(end),
        source_offset_s=float(start),time_mapping='source_s = local_s + source_offset_s',audio_present=False,
        metadata=measured))

def ref_cache(output,auth,base):
    """Use received GLM content, not teacher observations or an unknown replay."""
    state=read(output/'library_state.json')
    c=next(c for c in state['calls'] if c['id']=='glm_060_continuation_3_reference_repair')
    folder=output/'calls'/c['id'];request=read(folder/'request.json');response=read(folder/'response.json')
    require(c['status']=='received' and json_sha(request)==c['request_sha256'] and
            json_sha(response)==c['response_sha256'],'reference_received_cache_changed')
    text='\n'.join(x['text'] for x in response['result']['content'] if x.get('type')=='text')
    value=parse_model_json(text)
    require(value['reference']['reference_sha256']==auth['reference']['sha256'],'reference_sha_changed')
    record=dict(source_call_id=c['id'],request_sha256=c['request_sha256'],response_sha256=c['response_sha256'],
        status='known_model_reference_subobjects_with_historical_protocol_limit',
        limitation='原回复未通过完整覆盖合同；这些是GLM旧观察和估计，可能错漏，不是已核验真值。整段未知131不重放。',
        reference=value['reference'],editing_reference=value['editing_reference'])
    return write_once(base/'reference_received_cache.json',record)

def number(value,lo,hi):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise ValueError('finite_number_out_of_range')

def observe_check(value,auth):
    if not isinstance(value,dict):
        raise ValueError('model_root_object_required')
    if not isinstance(value.get('story'),list) or not value['story'] or not isinstance(value.get('facts'),list):
        raise ValueError('story_and_facts_lists_required')
    for s in value['story']:
        if not isinstance(s,dict):
            raise ValueError('story_row_object_required')
        if not all(isinstance(s.get(k),str) and s[k].strip() for k in ('id','contribution')):
            raise ValueError('story_id_contribution_required')
    inspect_check(value.get('inspect',[]),auth)

def inspect_check(items,auth):
    if not isinstance(items,list) or (len(items)>4 and auth.get('unbounded_inspections') is not True):
        raise ValueError('inspect_list_max_four')
    for r in items:
        if not isinstance(r,dict):
            raise ValueError('inspect_row_object_required')
        if r.get('target') not in {'parent','reference'} or not isinstance(r.get('question'),str) or not r['question'].strip():
            raise ValueError('inspect_target_question_required')
        source=auth[r['target']]
        number(r.get('start_s'),0,source['duration_s']);number(r.get('end_s'),0,source['duration_s'])
        step=r.get('step_s',.1);number(step,.033333333333,1)
        if not 0<r['end_s']-r['start_s']<=6 or (r['end_s']-r['start_s'])/step>36.001:
            raise ValueError('local_inspection_max6s_max36_requested_frames_shrink_range_or_raise_step')

def facts_check(value):
    if not isinstance(value,dict):
        raise ValueError('model_root_object_required')
    if not isinstance(value.get('facts'),list) or not isinstance(value.get('uncertainties'),list):
        raise ValueError('facts_uncertainties_lists_required')

def review_check(value):
    if not isinstance(value,dict):
        raise ValueError('model_root_object_required')
    if value.get('status') not in {'pass','partial','fail'} or not isinstance(value.get('problems'),list) or not isinstance(value.get('apparent_story'),str):
        raise ValueError('status_apparent_story_problems_required')

def plan_check(value,auth,source,story,*,max_segments=24,max_duration_s=180):
    if not isinstance(value,dict):
        raise ValueError('model_root_object_required')
    if max_segments is not None and (type(max_segments) is not int or max_segments<1):
        raise ValueError('invalid_max_segments')
    rows=value.get('segments')
    if not isinstance(rows,list) or not rows or (max_segments is not None and len(rows)>max_segments):
        raise ValueError('segments_nonempty_list_required' if max_segments is None else
                         'segments_1_to_'+str(max_segments))
    ids={s['id'] for s in story}
    for row in rows:
        if not isinstance(row,dict):
            raise ValueError('segment_row_object_required')
        if not isinstance(row.get('contribution_to'),list) or not row['contribution_to'] or not set(row['contribution_to'])<=ids:
            raise ValueError('segments_must_map_to_model_story_contributions')
        if row.get('source_id')!=source['source_id'] or row.get('window_id')!='full_parent_observed':
            raise ValueError('fixed_parent_source_and_observed_window_only')
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():
            raise ValueError('segment_reason_required')
        if row.get('caption') is not None:
            raise ValueError('no_new_explanatory_captions_original_text_only')
    compiled=compile_library_plan([source],{'segments':rows,'audio_mode':'silent'},fps=30,width=1280,height=720,
                                  max_duration_s=max_duration_s)
    if abs(compiled['duration_s']-auth['target_duration_s'])>auth['target_tolerance_s']:
        raise ValueError('target_close_to_reference_not_minimum_duration:'+str(compiled['duration_s']))
    if compiled['duration_s']>auth['reference']['duration_s']+1/30:
        raise ValueError('preserve_original_music_speed_output_must_fit_reference_audio')

def editing_fingerprint(plan,source,*,max_duration_s=180):
    compiled=compile_library_plan([source],{'segments':plan['segments'],'audio_mode':'silent'},
                                  fps=30,width=1280,height=720,max_duration_s=max_duration_s)
    # Rewording explanations/IDs alone does not change the executed editing.
    return json_sha(compiled['segments'])

class Trial:
    def __init__(self,output):
        self.output=Path(output).resolve();self.auth=get_auth(output)
        self.base=Path(self.auth['execution_directory']);self.state=VisualStoryState(output)
        self.mcp=TrialMCP(self.state,timeout_s=690)
        self.knowledge='\n\n'.join(Path(x['path']).read_text('utf-8') for x in self.auth['knowledge_files'])
        self.reference=ref_cache(self.output,self.auth,self.base)
        self.roi=write_once(self.base/'parent_layout.json',layout(self.auth['parent'])) if not (self.base/'parent_layout.json').exists() else read(self.base/'parent_layout.json')
        full=proxy(self.auth['parent'],0,self.auth['parent']['duration_s'],self.base,roi=self.roi['crop'],label='full_parent')
        self.full=full
        meta=full['metadata']
        self.render_source=dict(source_id='parent77',path=full['path'],sha256=full['sha256'],
                                duration_s=self.auth['parent']['duration_s'],audio_stream_index=None)
        self.inspections=[]
        self.selected_facts={}

    def status(self,stage,**details):
        self.state._reload();value=dict(stage=stage,cumulative_calls=self.state.data['request_count'],**details)
        write_json(self.base/'progress.json',value);print(json.dumps(value,ensure_ascii=False),flush=True)

    def call(self,name,prompt,media,scope,validator,*,image=False,purpose):
        path=Path(media).resolve(strict=True)
        self.state.set_artifact('vss_input_'+name,dict(stage=name,tool='analyze_image' if image else 'analyze_video',
            media_path=str(path),media_sha256=sha256_file(path),scope=scope,purpose=purpose))
        self.status(name)
        return self.mcp.call(name,prompt,path,validator,image=image,scope=scope)

    def source_scope(self,target,start,end,kind='continuous_window'):
        return dict(kind=kind,source_sha256=self.auth[target]['sha256'],source_start_s=start,source_end_s=end)

    def inspect(self,items):
        inspect_check(items,self.auth)
        from .microclip_frames import extract_grid, frame_catalog
        for row in items:
            key=json_sha(row)
            if any(x['request_sha256']==key for x in self.inspections):
                raise LibraryStopped('visual_story:repeated_inspection_no_new_evidence')
            index=len(self.inspections);target=row['target'];source=self.auth[target]
            start,end=row['start_s'],row['end_s'];step=row.get('step_s',.1)
            clip=proxy(source,start,end,self.base,roi=self.roi['crop'] if target=='parent' else None,label='model_local')
            scope=self.source_scope(target,start,end)
            p='独立观察这个真实连续片段，不知道最终剪辑意图。原时间=本地播放时间+'+str(start)+'。问题仅用于找缺项，不能当答案：'+row['question']+'\n只输出JSON {"facts":[{"start_s":原时间,"end_s":原时间,"description":"看见什么","basis":"picture或text或inference"}],"uncertainties":[]}。请区分画面、文字和推断，不假设完整动作已完成。'
            facts=self.call(f'vss_inspect_{index}',p,clip['path'],scope,facts_check,purpose='model_selected_continuous_local_observation')
            sheets=[];cursor=start;page=0
            # Each image has <=6 authentic frames. Inspection extent/precision is model owned.
            actual_end=frame_catalog(source['path'])['source']['source_end_s']
            while cursor<end-1e-8:
                stop=min(end,cursor+step*6,actual_end)
                if stop<=cursor:
                    break
                stage=f'vss_detail_{index}_{page}'
                resume=self.auth.get('unknown_resume')
                if resume and stage in resume['skip_stages']:
                    lost_scope=self.source_scope(target,cursor,stop,'sparse_contact_sheet')
                    require(lost_scope==resume['lost_scope'],'lost_page_scope_changed')
                    sheets.append(dict(status='unknown_not_replayed',call_id=resume['lost_call_id'],
                        source_scope=lost_scope,
                        limitations=['原请求已发出但没有捕获回复，此页没有可用模型事实；保留其它已完成观察。']))
                    cursor=stop;page+=1
                    continue
                grid=extract_grid(source['path'],cursor,stop,self.base/'frames'/f'inspect_{index}_{page}',count=min(6,max(1,math.ceil((stop-cursor)/step-1e-7))),max_cell_width=480)
                display=Path(grid['grid']['path'])
                if target=='parent':
                    # Reframe only display pixels; original source PTS/PNGs stay.
                    from PIL import Image, ImageDraw, ImageFont
                    x,y,w,h=self.roi['crop'];cw=480;ch=round(h*cw/w);label=30
                    display=display.with_name('reframed_grid.png')
                    if not display.exists():
                        sheet=Image.new('RGB',(cw*3,(ch+label)*math.ceil(len(grid['frames'])/3)),'white')
                        font=ImageFont.load_default(size=20)
                        for n,f in enumerate(grid['frames']):
                            with Image.open(f['png_path']) as im:
                                tile=im.convert('RGB').crop((x,y,x+w,y+h)).resize((cw,ch))
                            tx=(n%3)*cw;ty=(n//3)*(ch+label)
                            sheet.paste(tile,(tx,ty+label))
                            ImageDraw.Draw(sheet).text((tx+4,ty+4),f"source {f['source_time_s']:.6f}s",font=font,fill='black')
                        sheet.save(display)
                grid_scope=self.source_scope(target,cursor,stop,'sparse_contact_sheet')
                pp='观察这张真实源时间戳网格。它只表示抽样，不是连续播放。问题：'+row['question']+'。不要猜图间没展示的结果。只输出JSON {"facts":[{"time_s":真实图标时间,"description":"可见事实","basis":"picture或text或inference"}],"uncertainties":[]}。'
                f=self.call(stage,pp,display,grid_scope,facts_check,image=True,purpose='model_selected_real_PTS_local_frames')
                sheets.append(dict(grid_manifest=grid['manifest_path'],facts=f));cursor=stop;page+=1
            self.inspections.append(dict(request=row,request_sha256=key,continuous=facts,frame_sheets=sheets))
        write_once(self.base/'inspection_results.json',self.inspections)

    def plan_prompt(self,observed,*,revision=None):
        context=dict(reference=self.reference,observed=observed,local_observations=self.inspections,
                     source=dict(source_id='parent77',duration_s=self.render_source['duration_s']),
                     target_duration_s=self.auth['target_duration_s'],tolerance_s=self.auth['target_tolerance_s'],
                     max_duration_s=self.auth['reference']['duration_s']+1/30)
        if revision:
            context['actual_output_feedback']=revision
        return self.knowledge+'\n\n'+json.dumps(context,ensure_ascii=False)+'''\n制定一个完整可执行精剪表。自行选关键变化和边界；先删重复过程，再决定局部加速、慢放或停留，不必每种都用。约22秒，达到参考时长即可，重要信息要看清，不追求最短。不新增解释字幕，不从未看见画面补胜负或仪式。只能用parent77范围内的素材；真实源时间不等于输出时间；多个分散瞬间可共同完成一个story贡献。只输出JSON {"segments":[{"segment_id":"clip_1","source_id":"parent77","window_id":"full_parent_observed","source_in_s":0.0,"source_out_s":1.0,"speed":1.0,"freeze_tail_s":0.0,"look":"none","framing":"fit","contribution_to":["你原先story的ID"],"key_change":"可见关键变化","reason":"该操作改善什么","relation_to_next":"与下一片怎样呼应"}],"limitations":[]}。speed可0.5–2，尾帧停留0–2秒。输出总时长为各段(源时长/speed+停留)，须接近参考长度，镜头行数不等于原片镜头数。保持原先story表达贡献，最多24条。请直接完成选择，不只描述想怎么剪。'''

    def render(self,plan,index):
        p=deepcopy(plan);p.update(audio_mode='silent')
        write_once(self.base/f'plan_{index}.json',p)
        self.state.set_artifact(f'vss_render_{index}',dict(plan_sha256=json_sha(p),directory=str(self.base/f'render_{index}'),author='GLM'))
        self.status('rendering',version=index)
        return render_library_video([self.render_source],p,self.base/f'render_{index}',fps=30,width=1280,height=720)

    def blind_proxy(self,result):
        # Propagate the observed spatial image rectangle into the fit output.
        src=self.full['metadata'];v=next(s for s in src['streams'] if s['codec_type']=='video')
        scale=min(1280/v['width'],720/v['height'])
        pad=(720-round(v['height']*scale))/2
        _,parent_y,_,_=self.roi['crop'];_,pic_y,_,pic_h=self.roi['picture_crop']
        y=max(0,int(pad+(pic_y-parent_y)*scale)//2*2)
        h=min(720-y,int(pic_h*scale)//2*2)
        source=dict(path=result['rendered_path'],sha256=result['sha256'],video_stream_index=0,duration_s=result['duration_s'])
        return proxy(source,0,result['duration_s'],self.base,roi=[0,y,1280,h],label='picture_only_blind')

    def review(self,plan,result,index):
        blind=self.blind_proxy(result)
        scope=dict(kind='continuous_window',source_sha256=result['sha256'],source_start_s=0,source_end_s=result['duration_s'])
        suffix='' if index==0 else '_r'
        prompt='首次独立读这条实际无字幕、静音视频。没有提供目标、参考或剪辑计划，请只依据看到的画面，不借电影常识补剧情。只输出JSON {"apparent_story":"看见的故事顺序","facts":[{"start_s":输出时间,"end_s":输出时间,"description":"可见动作/状态变化"}],"problems":[{"start_s":输出时间,"end_s":输出时间,"description":"太快、难懂或重复的具体位置"}],"status":"pass或partial或fail","limitations":["观察受限之处"]}。不能把只看到一张画面说成动态看完，也不要凭音乐推断。'
        first=self.call('vss_blind'+suffix,prompt,blind['path'],scope,review_check,purpose='fresh_context_picture_only_actual_output_reading')
        context=dict(reference=self.reference,original_story=self.observed['story'],plan=plan,independent_picture_read=first,
                     source_observations=self.inspections,target_duration_s=self.auth['target_duration_s'])
        prompt=self.knowledge+'\n\n'+json.dumps(context,ensure_ascii=False)+'''\n审查实际视频与原表达要求、参考剪法作用、关键瞬间、信息可读性。不要把计划宣称当实际证据。先前无字幕盲读是独立事实，遇到冲突记录，不强行改成通过。本次只有已有77秒素材，原片缺少的结果不能靠变速补。音乐未核验可单独记未知，不因静音否定已有画面表达。只输出JSON {"apparent_story":"成片实际表达","status":"pass或partial或fail","problems":[{"start_s":输出秒,"end_s":输出秒,"description":"问题"}],"editing_methods_used":[{"operation":"手法","output_start_s":秒,"output_end_s":秒,"function":"实际作用"}],"next_action":"deliver或revise","revision_reason":"若改版，指出可修复的具体问题及所需真实证据；素材缺失不能靠反复剪修复","limitations":[]}。'''
        actual=proxy(dict(path=result['rendered_path'],sha256=result['sha256'],video_stream_index=0,duration_s=result['duration_s']),0,result['duration_s'],self.base,label='actual_output_review')
        reviewed=self.call('vss_review'+suffix,prompt,actual['path'],scope,review_check,purpose='actual_output_target_comparison_after_independent_read')
        return dict(blind=first,target=reviewed)

    def finish(self,selected,review):
        ref=self.auth['reference'];duration=selected['duration_s']
        require(duration<=ref['duration_s']+.075,'audio_source_shorter_than_video_no_silent_padding')
        target=self.base/'delivery'/'glm_skill_finecut.mp4';target.parent.mkdir(exist_ok=True)
        if not target.exists():
            command(['ffmpeg','-nostdin','-y','-v','error','-i',selected['rendered_path'],'-i',ref['path'],
                '-map','0:v:0','-map',f"1:{ref['audio_stream_index']}",'-c:v','copy','-af',
                f'atrim=duration={duration:.9f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.08,afade=t=out:st={duration-.5:.9f}:d=0.5',
                '-c:a','aac','-b:a','160k','-t',f'{duration:.9f}','-movflags','+faststart',target])
        metadata=probe_media(target);require(abs(metadata['duration_s']-duration)<.075,'delivery_duration')
        command(['ffmpeg','-nostdin','-v','error','-i',target,'-f','null','-'])
        self.state._reload();result=dict(policy='visual_story_skill_trial_v1',status='completed_with_model_review_limits',
            author='GLM-5.3-Flash',final_video=str(target),final_sha256=sha256_file(target),duration_s=duration,
            selected_render=selected,review=review,request_count=self.state.data['request_count'],
            new_requests=self.state.data['request_count']-BASELINE,goal_resumed=False,
            limitations=['模型自评和抽帧不等于陌生观众连续播放评测','音乐使用原参考音轨，未分离或验证卡点',
                         '完整参考旧不明请求未重放，本次起点是已收到的旧GLM参考分析'])
        get_auth(self.output,force=True)
        write_once(self.base/'result.json',result);self.state.set_artifact('vss_result',result)
        self.status('completed',video=str(target),duration_s=duration,model_status=review['target']['status'])
        (self.output/'mcp_stop').write_text('visual_story_trial_settled',encoding='utf-8')
        return result

    def run(self):
        done=self.base/'result.json'
        if done.exists():
            result=read(done);require(sha256_file(result['final_video'])==result['final_sha256'],'delivery_changed');return result
        prompt=self.knowledge+'\n\n'+json.dumps(dict(reference=self.reference,source_duration_s=self.auth['parent']['duration_s'],
             target_duration_s=self.auth['target_duration_s']),ensure_ascii=False)+'''\n当前视频是完整粗剪的正常速度静音代理，时间与父片同起点，只有外部黑边裁切，没有选择教师片段。先自主观察整片，拟定观众信息贡献段落，再决定哪些小范围值得精看；不要固定slot数量，不从电影知识补没出现的情节。只输出JSON {"story":[{"id":"beat_a","contribution":"本段新增什么信息","entry":"观众先知道什么","exit":"之后知道什么"}],"facts":[{"start_s":父片秒,"end_s":父片秒,"description":"看见什么","basis":"picture或text或inference"}],"inspect":[{"target":"parent或reference","start_s":秒,"end_s":秒,"step_s":0.1,"question":"要确认的具体缺项"}],"limitations":[]}。最多4个必要精看范围，每个不超过6秒且范围时长/step_s不超过36，可缩小范围提高精度。父片可用0至77.366667秒。参考已收到的GLM观察可能错漏，可以按缺项选其局部补看，禁止把整参考换编码重发。若整体足以规划可返回inspect空列表，但需要真实确认关键的短动态，而不是只写故事概括。'''
        self.observed=self.call('vss_observe',prompt,self.full['path'],self.source_scope('parent',0,self.auth['parent']['duration_s']),lambda v:observe_check(v,self.auth),purpose='whole_parent_normal_speed_observation_and_model_selected_local_queries')
        write_once(self.base/'observed.json',self.observed)
        self.inspect(self.observed.get('inspect',[]))
        validator=lambda v:plan_check(v,self.auth,self.render_source,self.observed['story'])
        plan=self.call('vss_plan',self.plan_prompt(self.observed),self.full['path'],self.source_scope('parent',0,self.auth['parent']['duration_s']),validator,purpose='model_owned_complete_EDL_from_skill_and_actual_observations')
        render=self.render(plan,0);review=self.review(plan,render,0)
        if review['target'].get('next_action')=='revise' and review['target'].get('revision_reason','').strip():
            revised=self.call('vss_revise',self.plan_prompt(self.observed,revision=dict(plan=plan,review=review)),self.full['path'],
                self.source_scope('parent',0,self.auth['parent']['duration_s']),validator,purpose='one_actual_output_evidence_driven_revision_not_another_round')
            if editing_fingerprint(revised,self.render_source)==editing_fingerprint(plan,self.render_source):
                self.state.set_artifact('vss_no_editing_progress_stop',dict(
                    reason='一次改版没有改变编辑表，不重复渲染或启动下一轮；交付已有实际候选及局限。',
                    revised_plan_sha256=json_sha(revised),original_plan_sha256=json_sha(plan)))
            else:
                second=self.render(revised,1);second_review=self.review(revised,second,1)
                # Keep both actual candidates; never label a regressed candidate selected pass.
                score={'pass':2,'partial':1,'fail':0}
                if score[second_review['target']['status']]>=score[review['target']['status']] and score[second_review['blind']['status']]>=score[review['blind']['status']]:
                    render,review=second,second_review
        return self.finish(render,review)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--authorize',action='store_true');p.add_argument('--user-instruction');p.add_argument('--run',action='store_true')
    p.add_argument('--resume-unknown',action='store_true',help='Append explicitly authorized no-replay continuation after the protected265 stop.')
    args=p.parse_args()
    if args.authorize:
        if not args.user_instruction:p.error('--user-instruction required')
        a=authorize(args.output,args.user_instruction);print(json.dumps({k:a[k] for k in ('policy','baseline_request_count','execution_directory')},ensure_ascii=False))
    if args.resume_unknown:
        if not args.user_instruction:p.error('--user-instruction required')
        from .visual_story_trial_state import record_unknown_resume
        a=record_unknown_resume(args.output,args.user_instruction)
        print(json.dumps({'resume_policy':a['policy'],
            'preserved_unknown':a['lost_call_id'],'new_renders':0},ensure_ascii=False))
    if args.run:
        base=Path(get_auth(args.output)['execution_directory'])
        try:
            print(json.dumps(Trial(args.output).run(),ensure_ascii=False),flush=True)
        except Exception as error:
            state=read(args.output/'library_state.json')
            stop=dict(error=str(error),request_count=state['request_count'],
                pending=[c['id'] for c in state['calls'][BASELINE:] if c['status']=='submitted'],no_automatic_replay=True)
            stop_path=base/'stopped.json'
            if stop_path.exists():
                stop_path=base/f"stopped_{state['request_count']}.json"
            if stop_path.exists() and read(stop_path)!=stop:
                stop_path=base/f"stopped_{state['request_count']}_{json_sha(stop)[:16]}.json"
            write_once(stop_path,stop)
            if not any(c['status']=='submitted' for c in state['calls'][BASELINE:]):
                (args.output/'mcp_stop').write_text('visual_story_trial_known_stop',encoding='utf-8')
            raise

if __name__=='__main__':main()
