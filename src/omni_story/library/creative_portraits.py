"""Observe task-local actor portraits from video, independent of index crops."""
from copy import deepcopy
import math
from pathlib import Path

from .state import LibraryStopped, ProviderRefused, json_sha, write_json
from .media import sha256_file, verify_source


def validate_portraits(value, duration):
    roles=value.get('roles') if isinstance(value,dict) else None
    if not isinstance(roles,list):raise ValueError('roles must list the actual visible characters')
    ids=[]
    for role in roles:
        if not isinstance(role,dict):raise ValueError('portrait must be an object')
        rid=role.get('role_id');t=role.get('time_s');box=role.get('bbox')
        if not isinstance(rid,str) or not rid.strip() or rid in ids:raise ValueError('unique local role_id required')
        ids.append(rid)
        if type(t) not in (int,float) or not math.isfinite(t) or not 0<=t<duration:
            raise ValueError('portrait time must be inside the actual clip')
        if (not isinstance(box,list) or len(box)!=4 or
                any(type(v) not in (int,float) or not math.isfinite(v) for v in box) or
                not 0<=box[0]<box[2]<=1 or not 0<=box[1]<box[3]<=1):
            raise ValueError('bbox must be normalized [left,top,right,bottom] for one actual character')
        if not role.get('appearance') or not role.get('evidence'):
            raise ValueError('stable appearance and visible portrait evidence required')
    return value


def observe_portraits(window, proxy, source, root, call):
    """The library stays immutable; fresh evidence augments this creative task only."""
    from PIL import Image
    from .material_confirm import _comparison_frame, _extract_comparison_frame
    duration=window['source_end_s']-window['source_start_s']
    prompt=(
        '观察整段真实动画，为本次创作登记人物。不知道新剧本，不分配故事角色。'
        '不能依赖旧素材库的裁图或人物编号；要列出视频中每一个外观清楚的人物，包括只在部分镜头出现的配角。'
        '同一人物在本片段只用一个local role_id，如P1、P2；表情、汗滴、泪水、嘴部状态、手持食物或临时道具'
        '变化都不产生新身份。以头部/身体形状、耳朵、固定花纹与稳定配色描述appearance。'
        '每人选一个看得最清楚的实际时刻，给出只框该人的bbox；优先正面头部和身体，别框到旁边的人，'
        '不要选切镜瞬间、纯色块、字幕或遮挡位置。只有始终无法辨认的身影才记入uncertainties。'
        'time_s为本视频从0开始的相对秒；bbox为0至1归一化[left,top,right,bottom]。'
        '不根据字幕猜人名。只返回JSON {"roles":[{"role_id":"P1","time_s":1.0,'
        '"bbox":[0.1,0.1,0.5,0.9],"appearance":"稳定可见外观","evidence":"此时截图可辨认的依据"}],'
        '"uncertainties":[]}。视频时长：'+str(duration)+'秒。')
    try:
        answer=call('material_scene_discovery_portraits_'+window['window_id'],prompt,
                    lambda v:validate_portraits(v,duration),proxy['path'])
    except (LibraryStopped,ValueError) as exc:
        if isinstance(exc, ProviderRefused) and exc.reason == 'provider_content_rejected':
            raise
        from .material_browser import observation_unusable
        if not observation_unusable(exc):raise
        # Stop hardening 11-17: no person is registered from this window (it then gives no shot).
        answer=dict(roles=[],uncertainties=['人物登记不可用：'+str(exc)[:160]],unusable=str(exc)[:300])
    folder=Path(root)/'portraits'/window['window_id'];folder.mkdir(parents=True,exist_ok=True)
    verify_source(source)
    exemplars=[];roles=[]
    for row in answer['roles']:
        timestamp=window['source_start_s']+row['time_s']
        frame=_comparison_frame(source,timestamp,folder,_extract_comparison_frame)
        with Image.open(frame) as picture:
            width,height=picture.size
            a,b,c,d=row['bbox']
            box=(math.floor(a*width),math.floor(b*height),math.ceil(c*width),math.ceil(d*height))
            target=folder/('actor_'+json_sha(row)[:16]+'.jpg')
            picture.crop(box).convert('RGB').save(target,quality=94)
        entity='creative_'+json_sha([window['source_sha256'],window['window_id'],row['role_id']])[:20]
        roles.append(dict(role_id=row['role_id'],entity_id=entity,entity_kind='individual',
                          appearance=row['appearance']))
        exemplars.append(dict(role_id=row['role_id'],path=str(target),sha256=sha256_file(target),
                              source_time_s=timestamp,portrait_evidence=row['evidence']))
    # Existing observation/events retain their original IDs and hashes. Only the
    # identity registry receives the task-local role/exemplar view.
    view=deepcopy(window)
    view['observation']=dict(view['observation'],roles=roles)
    view['entity_exemplars']=exemplars
    write_json(folder/'evidence.json',dict(window_id=window['window_id'],source_sha256=source['sha256'],
        original_roles=window['observation']['roles'],original_exemplars=window.get('entity_exemplars',[]),
        observation=answer,roles=roles,entity_exemplars=exemplars))
    return view
