"""Actual source-audio sample checks, separate from visible performance labels."""
import json
from copy import deepcopy
from pathlib import Path
from .character_voice import VoiceUnavailable
from .state import LibraryStopped,json_sha,write_json
from .media import sha256_file

POLICY='independent_source_voice_audition_v4'


class SourceVoiceProtocolError(VoiceUnavailable):
    """An unusable reply is not evidence that the actor has no original voice."""


def aborted_generation(received):
    """The provider aborted its own answer: a definite failure with nothing received.

    02 (396c35b6cf4d): Qwen returned HTTP 400 "InternalError.Algo.InvalidParameter: Model
    output became abnormal while generating a JSON response ... Please retry". Unlike an
    unknown outcome, asking again (as a new request) cannot duplicate any received answer."""
    return received.get('http_status')==400 and 'InternalError.Algo' in str(received.get('provider_error') or '')


def normalize_sample_actors(rows,appearance):
    result=deepcopy(rows);changes=[]
    if (isinstance(result,list) and len(result)==1 and isinstance(result[0],dict)
            and isinstance(appearance,str) and appearance!='target' and result[0].get('actor')==appearance):
        result[0]['actor']='target'
        changes.append(dict(field='voices[0].actor',original=appearance,normalized='target',
            reason='exact complete appearance of the sole requested target'))
    return result,changes


def validate_audio_groups(value,clips):
    rows=value.get('samples') if isinstance(value,dict) else None
    if (not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows)
            or len(rows)!=len(clips) or {r.get('clip_id') for r in rows}!={r['clip_id'] for r in clips}):
        raise ValueError('Return every requested clip_id exactly once: '+json.dumps([r['clip_id'] for r in clips]))
    for row in rows:
        negative=(row.get('has_voice') is False and row.get('voice_group') is None
            and row.get('heard_text') in (None,''))
        if (type(row.get('has_voice')) is not bool or
                row.get('voice_group') is not None and not isinstance(row['voice_group'],str) or
                not negative and (not isinstance(row.get('evidence'),str) or not row['evidence'].strip())):
            raise ValueError('Voice rows need typed flags and actual evidence; an explicitly unvoiced row may have empty text/evidence')
    return value


def checked_review(story,media,folder,prompt,api,validator):
    error=None
    for attempt in range(2):
        current=prompt if error is None else prompt+'\n上次结果已收到，但结构不合规：'+error+'。重新实际核听并严格返回全部原编号，不猜人物、不补未听到的人声。'
        received=review(story,media,folder,current,api)
        if aborted_generation(received):
            error='接口在生成回答时中止（HTTP 400），没有收到任何结果';continue
        if received.get('http_status')!=200:
            raise LibraryStopped('source_voice_review_transport_failed:'+str(received.get('http_status')))
        value=received.get('parsed')
        try:
            validator(value)
            return value
        except (ValueError,TypeError,KeyError) as exc:error=str(exc)
    raise SourceVoiceProtocolError('Source voice reply protocol unresolved: '+str(error))


def review(story,media,folder,prompt,api):
    from .audio_review import review_audio_video
    from .pipeline import _material_ledgers
    fingerprint=sha256_file(media)
    for parent,_ in _material_ledgers(story.state):
        for file in (parent/'autonomous_material/character_voices').glob('*/audition_v2/*/request.json'):
            request=json.loads(file.read_text())
            if (request.get('video_sha256')!=fingerprint or request.get('prompt')!=prompt or
                    request.get('configuration')!=api.cfg):continue
            state=json.loads((file.parent/'state.json').read_text())
            if state['status']!='received':raise LibraryStopped('prior_voice_audition_outcome_unknown_no_replay')
            result=review_audio_video(request['video_path'],file.parent,prompt=prompt,api=api)
            story.state.set_artifact('source_voice_audition_reuse',dict(path=str(file.parent),
                request_sha256=result['request_sha256'],response_sha256=result['response_sha256']))
            return result
    return review_audio_video(media,folder/json_sha(dict(prompt=prompt,video_sha256=fingerprint))[:16],prompt=prompt,api=api)


def find_and_audition(story,voice,media,appearance,duration,*,actor_id=None,source_binding=None):
    from omni_story.api import QwenAPI
    api=QwenAPI();api.cfg['reference_fps_requested']=6.0
    folder=voice.folder/'audition_v2'
    prompt=('只听实际原动画声音、看真实发声人物，为target提取原声音色候选。不要编剧，不分配老板顾客。'
        '人物外观不是发声证据；目标必须真实发声，排除其他人、音乐和静音。没有足够声音就少列或空数组，绝不凑时长。'
        '不要求原声是完整句子，人物独有的短语气词也可列，但必须确实听到其人声并确认归属。'
        '时间是视频顶部LOCAL从0开始的秒数。最多列8个最干净的区间，每个0.3到15秒，不能重叠。'
        '每段evidence必须写实际听到的词或语气音及归属依据，不能只写动作。'
        '只返回JSON对象 {"voices":[{"actor":"target","samples":[{"start_s":0,"end_s":1,"evidence":"实际声音和归属依据"}]}]}。\n'+
        json.dumps(dict(target_appearance=appearance,duration_s=duration),ensure_ascii=False))
    error=None
    for attempt in range(2):
        current=prompt if error is None else prompt+'\n上次已收到，但格式不合规：'+error+'。重新听看后严格返回规定对象；不要复述格式说明。'
        received=review(story,media,folder,current,api)
        answer=received.get('parsed')
        try:
            if aborted_generation(received):raise ValueError('接口在生成回答时中止（HTTP 400），没有收到任何结果')
            if received.get('http_status')!=200:raise LibraryStopped('source_voice_sample_transport_failed:'+str(received.get('http_status')))
            if not isinstance(answer,dict):raise VoiceUnavailable('voices object required')
            raw_rows=answer.get('voices')
            rows,normalizations=normalize_sample_actors(raw_rows,appearance)
            if not isinstance(rows,list):raise VoiceUnavailable('voices array required')
            if not rows:
                spans=[]
                break
            # The enrollment budget applies after listening, never to proposals:
            # an early noisy interval must not hide a later clean utterance.
            spans=voice.check_samples(rows,['target'],duration,minimum_s=0,stop_after_s=None)['target']
            break
        except (VoiceUnavailable,ValueError,TypeError) as exc:
            error=str(exc)
    else:raise SourceVoiceProtocolError('Invalid received source sample format: '+str(error))
    if not spans:raise VoiceUnavailable('No audible target samples found')
    candidates=[dict(sample_id='s'+str(i+1),start_s=a,end_s=b) for i,(a,b) in enumerate(spans)]
    prompt=('独立核听候选原声样本。候选时间不表示正确，未提供前次解释或故事角色。'
        '必须实际听到人声，再结合连续画面确认声音属于target。目标静止、拿物、居中、被问到均不是发声证据；'
        '遮嘴人物需有明确连续声线和身体配合的归属证据，不能只因看见他就认定。'
        '按每个候选准确核听：has_voice是否真正有人声，is_target_voice是否确为目标，isolated是否无其他人重叠。'
        'heard_text写真实词句或具体语气音，不能照抄画面动作；evidence写实际听感和归属证据。'
        '不确定就false。all_accepted_same_voice只核对三个标记均true的候选，其音色是否确实同一个人。'
        '严格返回JSON {"samples":[{"sample_id":"s1","has_voice":true,"is_target_voice":true,"isolated":true,'
        '"heard_text":"实际听到什么","evidence":"声线与归属依据"}],"all_accepted_same_voice":true}。\n'+
        json.dumps(dict(target_appearance=appearance,candidates=candidates),ensure_ascii=False))
    ids={r['sample_id'] for r in candidates}
    def validate_audition(answer):
        checked=answer.get('samples') if isinstance(answer,dict) else None
        if (not isinstance(checked,list) or any(not isinstance(r,dict) for r in checked) or
                len(checked)!=len(ids) or {r.get('sample_id') for r in checked}!=ids or
                type(answer.get('all_accepted_same_voice')) is not bool or
                any(any(type(r.get(k)) is not bool for k in ('has_voice','is_target_voice','isolated'))
                    or r.get('has_voice') and (not isinstance(r.get('evidence'),str) or not r['evidence'].strip()
                        or not r.get('heard_text')) for r in checked)):
            raise ValueError('Return complete typed source audition evidence for sample_ids '+json.dumps(sorted(ids)))
        return answer
    answer=checked_review(story,media,folder,prompt,api,validate_audition)
    checked=answer['samples']
    by_id={r['sample_id']:r for r in checked}
    accepted=[dict(start_s=c['start_s'],end_s=c['end_s'],evidence=by_id[c['sample_id']]['evidence'])
        for c in candidates if all(by_id[c['sample_id']][k] for k in ('has_voice','is_target_voice','isolated'))]
    if not accepted:raise VoiceUnavailable('Insufficient isolated original voice for target')
    # Remove pictures and all proposed identity before checking acoustic identity.
    # Whole-scene AV interpretation may otherwise mistake a listener for a speaker.
    from .dialogue_identity_review import labelled_audio_batch
    clips=[dict(clip_id='s'+str(i+1),start_s=r['start_s'],end_s=r['end_s']) for i,r in enumerate(accepted)]
    black,mapping=labelled_audio_batch(media,voice.folder/'audition_sound_only',clips)
    prompt=('只听编号样本的实际声音，没有人物、故事、台词或预期说话人。不要根据语义猜人物。'
        '按音色、共振和发音习惯将同一发声者归为同一voice_group；不同发声者分组，情绪和音高变化本身不是换人。'
        'has_voice只在确实有人声时为true；无法确定归组用voice_group=null。'
        'heard_text逐字记录原语言实际声音或具体语气音，不翻译、不补写未听到的字。'
        '严格返回JSON {"samples":[{"clip_id":"s1","has_voice":true,"voice_group":"g1",'
        '"heard_text":"原语实际声音","evidence":"实际音色听感依据"}]}。\n'+json.dumps(dict(clips=mapping),ensure_ascii=False))
    audio=checked_review(story,black,folder,prompt,api,lambda v:validate_audio_groups(v,clips))
    rows_audio=audio['samples']
    groups={r['clip_id']:r['voice_group'] for r in rows_audio if r['has_voice'] and r.get('heard_text') and r.get('voice_group')}
    def select(grouping):
        anchors=set();saw_anchor=False
        if source_binding is not None and actor_id is not None:
            for clip in clips:
                a,b=[source_binding['start_s']+clip[k] for k in ('start_s','end_s')]
                # A reaction close-up can carry somebody else's offscreen voice.
                # Only independently verified speaking anchors ownership.
                if any(s['source_sha256']==source_binding['source_sha256'] and s.get('speaking')==[actor_id] and
                        s.get('speaking_verification',{}).get('basis') in {'mouth_motion','synchronous_gesture'} and
                        min(b,s['source_end_s'])-max(a,s['source_start_s'])>=.3 for s in story.shots):
                    saw_anchor=True
                    if clip['clip_id'] in grouping:anchors.add(grouping[clip['clip_id']])
        choices=anchors if saw_anchor else set(grouping.values())
        chosen=next(iter(choices)) if len(choices)==1 else None
        picked=[r for r,clip in zip(accepted,clips) if chosen is not None and grouping.get(clip['clip_id'])==chosen]
        return picked,anchors

    picked,anchors=select(groups)
    result=dict(policy=POLICY,candidates=candidates,candidate_normalizations=normalizations,review=answer,blind_audio=audio,
        anchored_groups=sorted(anchors),accepted=picked)
    write_json(voice.folder/'audition_result.json',result)
    recovery=None
    try:
        spans=voice.check_samples([dict(actor='target',samples=picked)],['target'],duration)
    except VoiceUnavailable:
        # Whole-batch listening can put every utterance under the first label.
        # If AV and blind listening disagree about audible intervals, resolve
        # local pairs with heard exemplars instead of discarding the performer.
        if len(groups)==len(clips):raise
        from .dialogue_identity_review import review_voice_pairs
        def listen(batch,batch_folder,*,prompt):
            return review(story,batch,folder,prompt,api)
        recovery=review_voice_pairs(media,voice.folder/'audition_sound_pairs',clips,
            {c['clip_id']:c['clip_id'] for c in clips},listen,max_workers=1)
        groups={r['line_id']:r['voice_id'] for r in recovery['observations'] if r.get('voice_id') and r.get('heard_text')}
        picked,anchors=select(groups)
        result.update(blind_audio_pair_recovery=recovery,anchored_groups=sorted(anchors),accepted=picked)
        write_json(voice.folder/'audition_result.json',result)
        spans=voice.check_samples([dict(actor='target',samples=picked)],['target'],duration)
    return spans,dict(policy=POLICY,candidate_observation=rows,candidate_observation_raw=raw_rows,
        candidate_normalizations=normalizations,independent_audition=answer,
        blind_audio=audio,blind_audio_pair_recovery=recovery)
