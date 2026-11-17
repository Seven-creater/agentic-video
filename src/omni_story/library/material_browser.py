"""Full-catalog GLM browsing with source-bound, subtitle-blind visual evidence.

This is retrieval only. Creative possibilities never become observed facts, and
neither similarity rank nor an old selection can hide catalog entries.
"""
import math
import json
import subprocess
import re
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from .state import LibraryStopped, ProviderOutputCut, ProviderRefused, ReplyUnusable, json_sha, write_json


def observation_unusable(exc):
    """Stop hardening 11-17: this one observation cannot be had (provider refusal or every bounded
    repair failed). Observation callers that can do without it degrade conservatively."""
    text=str(exc)
    return (isinstance(exc,(ProviderRefused,ReplyUnusable)) or
            text.startswith(('provider_request_rejected:','provider_content_rejected:','model_protocol_repair_exhausted:')))
from .autonomous_material import dump

POLICY = 'full_catalog_visual_browser_v1'
CONTINUITY_POLICY = 'narrative_scene_intervals_v3'
SUBTITLE_BAND_POLICY = 'fixed_source_subtitle_band_v1'
# Question-first coarse screening (user 11-16): every promising window is glanced at
# with the reference question in mind, then the best ones from distinct parts of the
# source are watched in full before any comparison.
COARSE_FRAMES = (.12, .37, .62, .87)
COARSE_PER_SHEET = 6
COARSE_SHORTLIST = 8            # scenes/segments, not windows: one dialogue often spans 2-3 windows
COARSE_REGION_S = 180.0         # segments closer than this are treated as the same part of the source
COARSE_SEGMENT_WINDOWS = 3      # watch at most this many contiguous windows of one segment
COARSE_WATCH_LIMIT = 16         # total windows watched by the screen
COARSE_ADJACENT_S = 1.0         # a gap up to this still continues the previous window


def continues(prev, nxt):
    """nxt picks up where prev leaves off: catalog windows overlap (17 s long, a new one
    every 15 s), so a continuation starts inside prev or within a second after it."""
    return (prev['source_sha256']==nxt['source_sha256'] and
            float(prev['start_s'])<float(nxt['start_s'])<=float(prev['end_s'])+COARSE_ADJACENT_S)
COARSE_NEIGHBOR_HOPS = 2        # glance outward from a good window this many neighbours per side
# Library windows are fixed 17s slices; a conversation can continue past one.
# A chosen group touching a slice edge gets the adjacent slice reviewed with it.
EDGE_TOUCH_S = 2.0
EDGE_EXTENSION_LIMIT = 4


def source_subtitle_band(source_sha256):
    """The measured burned-in subtitle area of one source video, if recorded.

    Files live outside the published library snapshot, in OMNI_SUBTITLE_BAND_DIR,
    named <source_sha256>.json. Absent file: per-window localization only.
    """
    import os
    from pathlib import Path
    folder=os.environ.get('OMNI_SUBTITLE_BAND_DIR')
    path=Path(folder)/f'{source_sha256}.json' if folder else None
    if path is None or not path.is_file():return None
    value=json.loads(path.read_text())
    region=value.get('region',{})
    if (value.get('policy')!=SUBTITLE_BAND_POLICY or value.get('source_sha256')!=source_sha256
            or any(type(region.get(k)) not in (int,float) for k in ('x','y','w','h'))
            or not (0<=region['x']<region['x']+region['w']<=1 and 0<=region['y']<region['y']+region['h']<=1)):
        raise LibraryStopped('source_subtitle_band_invalid:'+source_sha256)
    from .media import sha256_file
    return dict(policy=SUBTITLE_BAND_POLICY,path=str(path),sha256=sha256_file(path),
        region={k:region[k] for k in ('x','y','w','h')})


def continuity_prompt(mapping):
    """Neutral scene grouping request; shared by production and footage regression."""
    return ('独立复看这份无声、原对白字幕已遮挡的视频。下面时间表标出每个窗口在拼接片中的位置，以及它在原片中的起止秒。'
        '原片中不相接的两个窗口之间有未提供的画面，不能归为同一组。'
        '你不知道参考剧情、编剧设想或之前的选材结论，只按实际画面划分可连续剪辑的场景组。'
        '场景组是同一地点、时间连续、围绕同一段互动的一场戏，剪辑时可在组内任意取镜。'
        '只有看见以下变化才换组：①地点改变或时间跳跃（到了另一处场所、转场、明显换日）；'
        '②参与互动的人物整体换成另一批；③互动目标或交流焦点改变，另起了一件事。'
        '同一场戏内部的镜头变化不是换组：全景、近景、特写、只拍手或物件的插入镜头、反应镜头、正反打、'
        '背景变色或放射线等情绪特效、单个人物进出画面，都归入它所属的场景组。'
        '防止过度切分：服务于同一互动的短节拍合并为一组，不要按镜头逐个分组。'
        '时间相邻、服装相似、同为白色小动物，不代表同一人物或同一地点；注意头部、耳朵、尾巴、衣饰等区别。'
        '确实无法确认是否同一地点或同一组人物时才拆分，并在limitations说明。不能编造角色职业或交易。'
        '一个窗口内也可能有换场、片头、片尾，segments只列该组可用部分，排除标题卡与不相干内容。'
        'segments的start_s/end_s用各窗口本地秒数，不用拼接片秒数；只作候选范围，不是最终剪辑表。'
        '每个范围必须满足0<=start_s<end_s<=该窗口media_duration_s。'
        '瞬间观察若无法确认持续区间，只写issues，不编造时长；没有可用区间允许groups为空。'
        '说明实际看见的交流、展示、递物等，不将微笑、张嘴等同说话。遮挡造成的身份或动作不明必须说明。'
        '返回JSON {"groups":[{"segments":[{"window_id":"...","start_s":0,"end_s":1}],'
        '"setting":"可见地点","participants":"按外形区分人物，不分配老板顾客",'
        '"evidence":"可见互动及连续依据","limitations":"看不清或不能确认的内容"}],"issues":[]}。\n'+dump(mapping))


def continuity_request_key(ids):
    return json_sha(dict(policy=CONTINUITY_POLICY,window_ids=ids))[:16]


def normalize_overview_selection(value, allowed):
    """Preserve explicit model choices while separating IDs from explanations.

    No fuzzy matching, invented IDs, extra selections or changed ordering.
    The raw provider response remains immutable; this is its validated view.
    """
    if not isinstance(value,dict) or not isinstance(value.get('notes'),str):
        raise ValueError('visual browsing notes required')
    aliases=dict(allowed) if isinstance(allowed,dict) else {key:key for key in allowed}
    aliases.update({key:key for key in list(aliases.values())})
    original=value.get('worth_watching')
    if not isinstance(original,list):raise ValueError('only actual overview IDs may be proposed')
    choices=[]
    for item in original:
        if isinstance(item,str) and item in aliases:
            selected=aliases[item]
        elif isinstance(item,dict):
            explicit=[item[k] for k in ('id','window_id','label') if k in item]
            if (not explicit or any(not isinstance(x,str) or x not in aliases for x in explicit)
                    or len({aliases[x] for x in explicit})!=1):
                raise ValueError('only actual overview IDs may be proposed')
            selected=aliases[explicit[0]]
        elif isinstance(item,str):
            match=re.fullmatch(r'([A-Za-z0-9_]+)\s*[（(].*[）)]',item,flags=re.DOTALL)
            if match is None or match[1] not in aliases:
                raise ValueError('only actual overview IDs may be proposed')
            selected=aliases[match[1]]
        else:
            raise ValueError('only actual overview IDs may be proposed')
        choices.append(selected)
    if original!=choices:
        value.setdefault('normalization',[]).append(dict(policy='explicit_overview_id_representation_v2',
            original_items=deepcopy(original),normalized_ids=list(choices)))
        value['worth_watching']=choices
    return value


def pages(rows, max_chars=48000, max_rows=120):
    """Bound request size, with no truncation or omission of catalog entries."""
    result, page = [], []
    for row in rows:
        if page and (len(page)>=max_rows or len(dump(page+[row]))>max_chars):
            result.append(page);page=[]
        page.append(row)
    if page:result.append(page)
    return result


def validate_scan(value, ids):
    if not isinstance(value,dict):raise ValueError('catalog verdict object required')
    groups=[value.get(k) for k in ('promising','possible','unlikely')]
    if any(not isinstance(g,list) or any(not isinstance(x,str) for x in g) for g in groups):
        raise ValueError('classify every window ID in exactly one group')
    flattened=[x for g in groups for x in g]
    if len(flattened)!=len(ids) or set(flattened)!=set(ids):
        raise ValueError('catalog coverage: '+dump(dict(missing=sorted(set(ids)-set(flattened)),
            unknown=sorted(set(flattened)-set(ids)),duplicates=sorted({w for w in flattened if flattened.count(w)>1}))))
    if not isinstance(value.get('page_summary'),str) or not value['page_summary'].strip():
        raise ValueError('explain this page and alternative possibilities')
    return value


TIMED_TEXT_TIME_KEYS=('second','time_s','start_s','end_s','time','s')
TIMED_TEXT_TEXT_KEYS=('desc','description','text','fact','content')
# A list position is already the index; numbering keys carry nothing else (21 f2c03a82:
# the goal_fit column's fact_index led replies to number their facts, 25 of 38 repaired).
TIMED_TEXT_INDEX_KEYS=('index','idx','id','fact_index')


def timed_text(item):
    """Losslessly flatten {"second":0,"desc":"..."} into one string; anything else is None."""
    if not isinstance(item,dict) or not set(item)<=set(TIMED_TEXT_TIME_KEYS+TIMED_TEXT_TEXT_KEYS+TIMED_TEXT_INDEX_KEYS):return None
    texts=[k for k in TIMED_TEXT_TEXT_KEYS if k in item]
    times=[k for k in TIMED_TEXT_TIME_KEYS if k in item]
    if len(texts)!=1 or not isinstance(item[texts[0]],str) or not item[texts[0]].strip():return None
    if any(type(item[k]) is not int for k in TIMED_TEXT_INDEX_KEYS if k in item):return None
    parts=[]
    for k in times:
        v=item[k]
        if type(v) in (int,float) and math.isfinite(v):parts.append(f'{v:g}')
        elif isinstance(v,str) and v.strip():parts.append(v.strip().rstrip('秒s').strip())
        else:return None
    when='-'.join(parts)
    return (f'{when}秒：' if when else '')+item[texts[0]].strip()


def validate_coarse(value, labels):
    """Every label on the sheet answered once; nothing invented, nothing skipped."""
    rows=value.get('rows') if isinstance(value,dict) else None
    if not isinstance(rows,list):raise ValueError('rows must list one verdict per sheet label')
    got=[r.get('label') if isinstance(r,dict) else None for r in rows]
    missing=[l for l in labels if l not in got];extra=[g for g in got if g not in labels]
    repeated=sorted({g for g in got if got.count(g)>1 and g in labels})
    if missing or extra or repeated:
        raise ValueError(f'每个图中编号恰好回答一次：缺少{missing}，多出{extra}，重复{repeated}；只用编号{list(labels)}')
    for r in rows:
        if r.get('fit') not in ('strong','possible','no'):
            raise ValueError(f'{r["label"]}: fit只能是strong/possible/no')
        if not isinstance(r.get('reason'),str) or not r['reason'].strip():
            raise ValueError(f'{r["label"]}: reason须写图中依据')
        if r['fit']!='no' and (not isinstance(r.get('can_carry'),str) or not r['can_carry'].strip()):
            raise ValueError(f'{r["label"]}: strong/possible须写can_carry（能承载goal里的哪种关系或节拍）')
        if r['fit']!='no' and (type(r.get('score')) is not int or not 1<=r['score']<=5):
            raise ValueError(f'{r["label"]}: strong/possible须给score整数1-5（5=最适合goal），用于在同档候选里排序')
    return value


def validate_goal_fit(value, duration):
    """The question column must point at seconds of this clip and at a listed fact."""
    rows=value.get('goal_fit',[]) if isinstance(value,dict) else None
    if not isinstance(rows,list):raise ValueError('goal_fit must be an array (empty when nothing fits)')
    facts=value.get('visible_facts',[])
    for i,r in enumerate(rows):
        if not isinstance(r,dict) or not isinstance(r.get('need'),str) or not r['need'].strip():
            raise ValueError(f'goal_fit[{i}] needs need (which relation or beat)')
        a,b=r.get('start_s'),r.get('end_s')
        if type(a) not in (int,float) or type(b) not in (int,float) or not 0<=a<=b<=duration+.5:
            raise ValueError(f'goal_fit[{i}]: start_s/end_s须在本片0-{duration:.1f}秒内且start不晚于end（单一瞬间可相等）')
        k=r.get('fact_index')
        if type(k) is not int or not 0<=k<len(facts):
            raise ValueError(f'goal_fit[{i}]: fact_index须是visible_facts的下标0-{len(facts)-1}，引用支撑它的实际事实')
    return value


def validate_visual(value):
    if not isinstance(value,dict):raise ValueError('visual observation object required')
    for key in ('visible_facts','interaction','uncertainties','occlusion_limits'):
        rows=value.get(key)
        if not isinstance(rows,list):
            raise ValueError(f'{key} must be a JSON array of plain strings')
        flat=[]
        for i,v in enumerate(rows):
            text=v if isinstance(v,str) else timed_text(v)
            if text is None:
                raise ValueError(f'{key}[{i}] must be a plain string; write the time inside the text, '
                    'for example "0-2秒：两人在摊位前对视", not an object')
            flat.append(text)
        if flat!=rows:
            value.setdefault('normalized_fields',[]).append(key)
            value[key]=flat
    if type(value.get('readable_dialogue_text')) is not bool:
        raise ValueError('explicitly check remaining readable dialogue text')
    return value


def local_continuity_range(segment, mapping):
    """Convert an unambiguous montage range; never guess an ambiguous time domain."""
    a,b=segment.get('start_s'),segment.get('end_s')
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)):
        raise ValueError('finite local seconds required')
    if b<=a:
        raise ValueError(f'continuity end_s must be greater than start_s; received {a}..{b}')
    duration=mapping['media_duration_s']
    if 0<=a<b<=duration+.05:return None
    offset=mapping['start_s']
    if offset<=a<b<=mapping['end_s']+.05:
        original=dict(segment)
        segment.update(start_s=round(a-offset,6),end_s=round(b-offset,6))
        return dict(original=original,local=dict(segment),reason='outside local domain; uniquely fits mapped montage interval')
    raise ValueError(f'continuity local range outside clip: received {a}..{b}; '
                     f'local bounds 0..{duration}, montage bounds {offset}..{mapping["end_s"]}')


def validate_continuity_groups(value, mapping):
    """A point observation has no usable duration; never invent one for it."""
    groups=value.get('groups') if isinstance(value,dict) else None
    if not isinstance(groups,list):raise ValueError('continuity groups required, empty is allowed')
    if not isinstance(value.get('issues'),list):raise ValueError('record changes and uncertainties')
    by_id={m['window_id']:m for m in mapping}
    retained=[];excluded=[]
    for i,group in enumerate(groups):
        if not isinstance(group,dict):raise ValueError(f'groups[{i}] must be an object')
        missing=[k for k in ('setting','participants','evidence','limitations')
                 if not isinstance(group.get(k),str) or not group[k].strip()]
        if missing:
            raise ValueError(f'groups[{i}] lacks non-empty string fields {missing}. Every group needs its own '
                'setting, participants, evidence and limitations; write limitations="无明显限制" when none. '
                'issues is only for the whole video and does not replace a group field.')
        ranges=group.get('segments')
        if not isinstance(ranges,list) or not ranges:raise ValueError('actual usable local ranges required')
        usable=[]
        for j,r in enumerate(ranges):
            if not isinstance(r,dict) or r.get('window_id') not in by_id:
                raise ValueError(f'groups[{i}].segments[{j}]: unknown continuity window')
            bounds=by_id[r['window_id']];a,b=r.get('start_s'),r.get('end_s')
            if (type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a)
                    and a==b and (0<=a<=bounds['media_duration_s']+.05
                        or bounds['start_s']<=a<=bounds['end_s']+.05)):
                excluded.append(dict(group_index=i,segment_index=j,segment=deepcopy(r),
                    reason='point observation has zero usable duration; no interval inferred'))
                continue
            try:conversion=local_continuity_range(r,bounds)
            except ValueError as exc:
                raise ValueError(f'groups[{i}].segments[{j}] {r["window_id"]}: {exc}') from exc
            if conversion:value.setdefault('coordinate_normalizations',[]).append(conversion)
            usable.append(r)
        # Concatenated windows that do not meet in the source hide footage between
        # them; one scene group cannot jump across that unseen gap.
        used=sorted({r['window_id'] for r in usable},key=lambda w:by_id[w]['start_s'])
        for left,right in zip(used,used[1:]):
            a,b=by_id[left],by_id[right]
            if 'source_start_s' in a and 'source_start_s' in b and (a.get('source_sha256')!=b.get('source_sha256')
                    or b['source_start_s']>a['source_end_s']+1.0):
                raise ValueError(f'groups[{i}] joins {left} and {right}, which are not adjacent in the source '
                    f'(source {a["source_end_s"]}s -> {b["source_start_s"]}s); unseen footage lies between them. '
                    'Split them into separate groups.')
        if usable:retained.append(dict(group,segments=usable))
    value['groups']=retained
    if excluded:
        value.setdefault('excluded_unusable_segments',[]).extend(excluded)
        value['issues'].append('Zero-duration point observations excluded from usable scene ranges; no duration inferred.')
    return value


def continuity_transport(video, expected_duration):
    """Fit the complete silent montage into the vision transport, without cuts."""
    from .media import _run,probe_media
    measured=probe_media(video)
    if abs(measured['duration_s']-expected_duration)>.5:
        raise LibraryStopped('browser_continuity_proxy_mapping_or_size_invalid')
    if video.stat().st_size<8_000_000:return video,measured
    video_stream=next(s for s in measured['streams'] if s.get('codec_type')=='video')
    width=min(640,video_stream['width'])//2*2
    bitrate=max(1,math.floor(7_000_000*8/expected_duration*.9))
    target=video.with_name('continuity_transport_v1.mp4')
    _run(['ffmpeg','-v','error','-nostdin','-y','-i',str(video),'-map','0:v:0',
        '-vf',f'scale={width}:-2,fps=8','-c:v','libx264','-preset','veryfast',
        '-b:v',str(bitrate),'-maxrate',str(bitrate),'-bufsize',str(bitrate*2),
        '-an','-sn','-dn','-map_metadata','-1',str(target)])
    measured=probe_media(target)
    if target.stat().st_size>=8_000_000 or abs(measured['duration_s']-expected_duration)>.5:
        raise LibraryStopped('browser_continuity_transport_still_over_limit_or_time_changed')
    return target,measured


class MaterialBrowser:
    def __init__(self, discovery):
        self.d=discovery
        self.root=discovery.root/'browser'
        self.root.mkdir(parents=True,exist_ok=True)
        self.catalog=[]
        self.visual={}
        self.proxies={}
        self.trace=[]
        self.scene_groups={}
        self.audited_sets=set()
        self.unwatchable={}
        comparison=self.root/'comparison_progress.json'
        self.comparison_progress=json.loads(comparison.read_text()) if comparison.exists() else dict(rounds=[],active=False)


    def stage(self, kind, suffix):
        return f'material_scene_browser_{kind}_{suffix}'

    def goal(self):
        return dict(user_brief=self.d.route.config.get('material_brief'),
            dialogue_functions=[dict(relationship=s.get('interaction'),progression=s.get('template'))
                                for s in self.d.route.scenes])

    def window_allowed(self, wid):
        window=self.d.load_window(wid)
        return bool(self.d.route._allowed_record(dict(observation_id=self.d.records[wid][0]['observation_id'],
            source_sha256=window['source_sha256'],source_start_s=window['source_start_s'],source_end_s=window['source_end_s'])))

    def catalog_rows(self):
        rows=[];sources={}
        for wid,records in self.d.records.items():
            if not self.window_allowed(wid):
                # proxy() watches the whole window; a window crossing an excluded range
                # (titles, credits, stylized inserts) can never be watched, so never list it.
                continue
            stored=self.d.route.store.get(records[0]['observation_id'])
            observed=stored.get('received_observation',stored['window'].get('observation',{}))
            sha=records[0]['source_sha256'];sources.setdefault(sha,f'source_{len(sources)+1}')
            rows.append(dict(window_id=wid,source_sha256=sha,source_label=sources[sha],
                start_s=min(r['source_start_s'] for r in records),end_s=max(r['source_end_s'] for r in records),
                summary=next((r['window_summary'] for r in records if r.get('window_summary')),''),
                conditional_uses=observed.get('affordances',[])))
        return sorted(rows,key=lambda r:(r['source_sha256'],r['start_s'],r['window_id']))

    def scan(self):
        self.catalog=self.catalog_rows()
        if not self.catalog:raise LibraryStopped('material_browser_no_eligible_windows')
        self.by_id={r['window_id']:r for r in self.catalog}
        # Every summary and every use remains represented. Verbose conditions
        # are available through details; they need not be reprinted on each
        # navigation page. Original hashes stay in the bound catalog artifact.
        compact=[dict(window_id=r['window_id'],source=r['source_label'],start_s=r['start_s'],end_s=r['end_s'],
            summary=r['summary'],conditional_uses=[dict(expression=u.get('expression'),
                possible_uses=u.get('possible_uses',[])) for u in r['conditional_uses']]) for r in self.catalog]
        seeds=[];covered=set()
        if hasattr(self.d,'state'):
            from .pipeline import _material_ledgers
            from .media import sha256_file
            from pathlib import Path
            ledgers=[]
            for folder,ledger in _material_ledgers(self.d.state):
                if folder==self.d.state.output:continue
                ledgers.append((folder,ledger))
                refs=ledger.get('artifacts',{}).get('browser_catalog_page_ledgers',[])
                if refs:
                    artifact=json.loads(Path(refs[-1]['path']).read_text())
                    if json_sha(artifact)!=refs[-1]['sha256']:raise LibraryStopped('browser_page_ledger_list_changed')
                    for path in artifact:
                        file=Path(path)
                        if not file.exists():continue  # a reused seed has no new page ledger
                        if not file.resolve().is_relative_to(folder.resolve()):raise LibraryStopped('browser_page_ledger_outside_parent')
                        child=json.loads(file.read_text())
                        if any(c['status'] not in {'received','failed_known'} for c in child['calls']):
                            raise LibraryStopped('browser_parent_page_outcome_unknown_no_replay')
                        ledgers.append((file.parent,child))
            for folder,ledger in ledgers:
                for call in reversed(ledger['calls']):
                    import re
                    if call['status']!='received' or not re.fullmatch(r'material_scene_browser_scan_[0-9]+(?:_repair2?)?',call['name']):continue
                    request=json.loads((folder/'calls'/call['id']/'request.json').read_text())
                    if json_sha(request)!=call['request_sha256']:raise LibraryStopped('browser_seed_request_changed')
                    prompt=request['arguments']['prompt'].split('\n上次输出未通过本地协议校验。')[0]
                    data=json.loads(prompt.split('\n',1)[1]);page=data['catalog']
                    old_goal=data['goal']
                    if 'scenes' in old_goal:
                        old_goal=dict(user_brief=old_goal['user_brief'],dialogue_functions=[
                            dict(relationship=s.get('interaction'),progression=s.get('template')) for s in old_goal['scenes']])
                    if old_goal!=self.goal():continue
                    if any(r['window_id'] not in self.by_id or r['window_id'] in covered for r in page):continue
                    # Exact original rows stay in the seed request. Compact
                    # rows compare their represented fields against today's
                    # source-bound catalog, never merely their window IDs.
                    def matching(r):
                        now=self.by_id[r['window_id']]
                        if any(r[k]!=now[k] for k in ('start_s','end_s','summary')):return False
                        if 'source_sha256' in r:return r['source_sha256']==now['source_sha256'] and r['conditional_uses']==now['conditional_uses']
                        current=next(c for c in compact if c['window_id']==r['window_id'])
                        return r==current
                    if not all(matching(r) for r in page):continue
                    response=json.loads((folder/'calls'/call['id']/'response.json').read_text())
                    if json_sha(response)!=call['response_sha256']:raise LibraryStopped('browser_seed_response_changed')
                    from .pipeline import _parse_model_reply
                    raw='\n'.join(r['text'] for r in response['result']['content'] if r.get('type')=='text')
                    expected=[r['window_id'] for r in page]
                    try:
                        answer=_parse_model_reply(call['name'],raw,lambda v:validate_scan(v,expected))
                        validate_scan(answer,expected)
                    except (ValueError,TypeError,KeyError):
                        # A known received reply can preserve its unambiguous
                        # classifications. Ask only about omitted/conflicting
                        # IDs; never silently call missing entries unsuitable.
                        try:partial=_parse_model_reply(call['name'],raw,lambda v:v)
                        except (ValueError,TypeError,KeyError):continue
                        if not isinstance(partial,dict) or any(not isinstance(partial.get(k),list)
                                or any(not isinstance(w,str) for w in partial[k]) for k in ('promising','possible','unlikely')):continue
                        all_ids=[w for k in ('promising','possible','unlikely') for w in partial[k]]
                        kept={k:[w for w in partial[k] if w in expected and all_ids.count(w)==1]
                              for k in ('promising','possible','unlikely')}
                        retained={w for g in kept.values() for w in g}
                        missing=[r for r in page if r['window_id'] not in retained]
                        if not missing:continue
                        fix_prompt=('补充已收到但不完整的目录分类。这里只判断本次catalog里的条目；旧有效分类会保留。'
                            '同样按promising/possible/unlikely分类，每个ID一次；只作检索线索，不编剧。'
                            '返回JSON {"promising":[],"possible":[],"unlikely":[],"page_summary":"本批简述"}。\n'+
                            dump(dict(goal=self.goal(),catalog=missing)))
                        fixed=self.d.call(self.stage('scan_missing',json_sha(dict(page=page,missing=missing))[:12]),
                            fix_prompt,lambda v:validate_scan(v,[r['window_id'] for r in missing]))
                        answer={k:kept[k]+fixed[k] for k in kept}
                        answer['page_summary']=str(partial.get('page_summary',''))+' / 补漏：'+fixed['page_summary']
                        validate_scan(answer,expected)
                    seeds.append((page,answer,dict(ledger=str(folder/'library_state.json'),
                        ledger_sha256=sha256_file(folder/'library_state.json'),call_id=call['id'],
                        request_sha256=call['request_sha256'],response_sha256=call['response_sha256'])))
                    covered.update(r['window_id'] for r in page)
                if len(covered)==len(self.catalog):break
        self.catalog_pages=[s[0] for s in seeds]+pages([r for r in compact if r['window_id'] not in covered],max_chars=24000,max_rows=70)
        write_json(self.root/'classification_seeds.json',[s[2] for s in seeds])
        goal=self.goal()
        self.scans=[None]*len(self.catalog_pages)
        isolated=self.d.glm.provider=='official_vision_mcp_in_opencode' if hasattr(self.d,'glm') else False
        def process(item):
            i,page=item
            if i<=len(seeds):return i,seeds[i-1][1]
            prompt=('通读这一页全部素材目录，为二创寻找可合理改编的连续场景。'
                '不要求原道具、原职业、原台词或精确动作。看人物交流、反应和关系的改编可能。'
                'conditional_uses是有条件的旧联想，不是画面事实，也不限制新的用途。'
                '这里只粗分导航潜力，不推演整套剧本、不分配演员、不计算台词或嘴动秒数。'
                '摘要未提到不代表原片没有；有潜力或不确定的列possible，后续可以看片。'
                '每个window_id恰好归入一组，不能省略。promising明显值得看；possible可能可用；unlikely暂不看。'
                '返回JSON {"promising":[],"possible":[],"unlikely":[],"page_summary":"潜力及替代方向"}。\n'+
                dump(dict(goal=goal,page=i,total_pages=len(self.catalog_pages),catalog=page)))
            validate=lambda v,ids=[r['window_id'] for r in page]:validate_scan(v,ids)
            if isolated:
                from .state import LibraryState
                from .opencode_provider import OpenCodeMCP
                from .pipeline import _material_call
                lock=deepcopy(self.d.state.input_lock)
                lock['configuration']['browser_catalog_page']=dict(
                    parent=str(self.d.state.output),page=i,catalog_sha256=json_sha(self.catalog))
                folder=self.root/'pages'/f'page_{i:03d}'
                state=LibraryState(folder,lock,max_requests=None,registry_path=self.d.state.registry_path)
                glm=OpenCodeMCP(state,package_root=self.d.glm.package_root,
                    executable=self.d.glm.executable,exclusions=self.d.glm.exclusions,
                    timeout_s=self.d.glm.timeout_s)
                validate.repair_limit=2
                try:return i,_material_call(state,glm,self.stage('scan',str(i)),prompt,validate)
                except (LibraryStopped,ValueError) as exc:
                    if not observation_unusable(exc):raise
                    return i,self.unscanned_page(i,page,exc)
            try:return i,self.d.call(self.stage('scan',str(i)),prompt,validate)
            except (LibraryStopped,ValueError) as exc:
                if not observation_unusable(exc):raise
                return i,self.unscanned_page(i,page,exc)

        # Isolated ledgers preserve the serial paid-request invariant inside
        # each worker. Join every submitted page before propagating failure.
        ledgers=[str(self.root/'pages'/f'page_{i:03d}'/'library_state.json')
                 for i in range(1,len(self.catalog_pages)+1)] if isolated else []
        self.d.state.set_artifact('browser_catalog_page_ledgers',ledgers) if isolated else None
        pool=ThreadPoolExecutor(max_workers=3 if isolated else 1)
        futures=[pool.submit(process,item) for item in enumerate(self.catalog_pages,1)]
        errors=[]
        from concurrent.futures import as_completed
        for future in as_completed(futures):
            try:i,answer=future.result()
            except Exception as exc:
                errors.append(exc);continue
            self.scans[i-1]=answer
            write_json(self.root/'coverage.json',dict(policy=POLICY,catalog_sha256=json_sha(self.catalog),
                total_windows=len(self.catalog),total_pages=len(self.catalog_pages),
                page_ledgers=ledgers,completed_pages=sum(s is not None for s in self.scans),
                classified_window_ids=[w for s in self.scans if s is not None
                    for k in ('promising','possible','unlikely') for w in s[k]],
                complete=all(s is not None for s in self.scans)))
        pool.shutdown(wait=True)
        if isolated:
            page_calls=[]
            for path in ledgers:
                from pathlib import Path
                file=Path(path)
                if file.exists():
                    page_calls.extend(dict(ledger=path,**c) for c in json.loads(file.read_text())['calls'])
            write_json(self.root/'page_requests.json',dict(calls=page_calls,requests=len(page_calls),
                uncertain_calls=[c['id'] for c in page_calls if c['status'] in {'submitted','uncertain'}]))
        if errors:raise errors[0]
        write_json(self.root/'catalog.json',self.catalog)
        write_json(self.root/'catalog_verdicts.json',self.scans)
        self.d.seen.update(self.by_id)

    def proxy(self, wid):
        if wid in self.proxies:return self.proxies[wid]
        from .material_confirm import confirm_clip
        from .audio_contracts import validate_regions
        window=self.d.load_window(wid)
        source=self.d.route.sources[window['source_sha256']]
        start,end=window['source_start_s'],window['source_end_s']
        if not self.window_allowed(wid):
            raise LibraryStopped('material_browser_full_window_crosses_excluded_range:'+wid)
        if not hasattr(self,'parent_proxy_caches'):
            from .pipeline import _material_ledgers
            self.parent_proxy_caches=([folder/'autonomous_material/browser' for folder,_ in _material_ledgers(self.d.state)
                if folder!=self.d.state.output] if hasattr(self.d,'state') else [])
        raw=confirm_clip(source,start,end,self.root/'localization_media',pad_s=0,
            reuse_caches=[p/'localization_media' for p in self.parent_proxy_caches])
        prompt=('视频没有声音。只定位原对白字幕，中文和日文均须覆盖，不听写文字，不解释剧情。'
            '为每个字幕位置给稳定合并框并留少量余量，时间是本片本地秒；不遮招牌等场景文字。'
            '若遮挡字幕也遮住脸、手或物件，在limitations如实说明。'
            '返回JSON {"masks":[{"start_s":0,"end_s":1,"x":0.1,"y":0.8,"w":0.8,"h":0.15,'
            '"color":"#000000","reason":"原对白字幕"}],"protected_text":[],"limitations":[]}。')
        regions=self.d.call(self.stage('masks',wid),prompt,
            lambda v:validate_regions(v,end-start),raw['path'])
        # A union over each detected position protects against uncertain subtitle
        # onset/offset. The lost image region is explicitly reported, never filled.
        masks=[dict(r,start_s=start,end_s=end,color='#000000') for r in regions['masks']]
        proxy=confirm_clip(source,start,end,self.root/'blind_media',pad_s=0,subtitle_masks=masks,
            reuse_caches=[p/'blind_media' for p in self.parent_proxy_caches])
        bound=dict(proxy=proxy,localization=regions,window_id=wid,source_masks=masks,
            source_sha256=source['sha256'],subtitle_band=None,
            limitations=['Black subtitle regions remove original image content; hidden actions cannot be verified.'])
        write_json(self.root/(wid+'_media.json'),bound)
        self.proxies[wid]=bound
        return bound

    def banded_proxy(self, wid, band):
        """Per-window masks plus the source's fixed subtitle band, for a window whose
        muted view still showed readable dialogue. The band hides more picture, so it
        is used only where the narrower masks were seen to leak."""
        from .material_confirm import confirm_clip
        bound=self.proxy(wid)
        source=self.d.route.sources[bound['source_sha256']]
        start,end=bound['proxy']['source_start_s'],bound['proxy']['source_end_s']
        masks=[*bound['source_masks'],dict(band['region'],start_s=start,end_s=end,color='#000000',reason='固定源片字幕带')]
        proxy=confirm_clip(source,start,end,self.root/'blind_media',pad_s=0,subtitle_masks=masks,
            reuse_caches=[p/'blind_media' for p in self.parent_proxy_caches])
        return dict(bound,proxy=proxy,source_masks=masks,subtitle_band=band)

    PROVIDER_REFUSALS=('provider_request_rejected:','provider_content_rejected:')

    def prior_refusal(self, wid):
        """A definite provider refusal already recorded for this window (any ancestor run)."""
        if not hasattr(self.d,'state'):return None
        from .pipeline import _material_ledgers
        stages={self.stage(k,wid) for k in ('masks','blind','blindband')}
        for folder,ledger in _material_ledgers(self.d.state):
            for call in ledger.get('calls',[]):
                base=re.sub(r'_repair2?$','',call['name'])
                evidence=folder/'calls'/call['id']/'agent'/'provider_rejection.json'
                if base in stages and call['status']=='failed_known' and evidence.exists():
                    return dict(json.loads(evidence.read_text()),call_id=call['id'],stage=base)
        return None

    def unscanned_page(self, number, page, exc):
        """A catalog page whose classification cannot be had: every entry stays 'possible' (the
        prompt's own rule for uncertain entries), a navigation hint only, never a fact."""
        ids=[r['window_id'] for r in page]
        if hasattr(self.d,'state'):self.d.state.set_artifact('browser_catalog_page_unusable',dict(page=number,window_ids=ids,error=str(exc)))
        return dict(promising=[],possible=ids,unlikely=[],page_summary='本页目录分类不可用，全部按possible保留，需看片确认')

    def watch(self, wid):
        """Watch one window; a window the provider definitely refuses is skipped, not fatal.

        21 (fd3c176cbfb7): one candidate window got HTTP 400 code 1210 while 8 others with the
        same encoding passed; the whole search stopped. One refused library window is one
        fewer candidate, recorded and shown to the model, never resent."""
        if wid in self.visual:return self.visual[wid]
        if wid in self.unwatchable:return dict(window_id=wid,provider_refused=self.unwatchable[wid])
        refusal=self.prior_refusal(wid)
        if refusal is None:
            try:return self._watch(wid)
            except (LibraryStopped,ValueError) as exc:
                # Stop hardening 11-17: a window whose silent watch exhausted its repairs is as
                # unusable as a refused one; it is one fewer candidate, never a stop.
                if not observation_unusable(exc):raise
                refusal=dict(error=str(exc))
        self.unwatchable[wid]=refusal
        write_json(self.root/'unwatchable.json',self.unwatchable)
        if hasattr(self.d,'state'):self.d.state.set_artifact('browser_window_provider_refused',dict(window_id=wid,**refusal))
        return dict(window_id=wid,provider_refused=refusal)

    def drop_cut_material(self, cut):
        """Provider moderation cut the decision while describing some watched material.

        Same answer as a refused window (INC-20261116-09): the windows of the candidate being
        written at the cut become provider_refused and leave the comparison; the next turn asks
        again without them. Nothing about the cut is guessed: a partial text whose last
        window list is missing or unfinished does not name the material, so the stop stands."""
        lists=list(re.finditer(r'"window_ids"\s*:\s*\[([^\]]*)\]',cut.partial))
        if not lists or lists[-1].start()!=cut.partial.rfind('"window_ids"'):raise cut
        wids=[w for w in re.findall(r'"([^"]+)"',lists[-1].group(1)) if w in self.by_id]
        if not wids:raise cut
        refusal=dict(error='provider_output_cut',stage=cut.stage,call_id=cut.call_id)
        for wid in wids:
            self.visual.pop(wid,None)
            self.unwatchable[wid]=refusal
        for key,group in list(self.scene_groups.items()):
            if {r['window_id'] for r in group.get('segments',[])}&set(wids):del self.scene_groups[key]
        if getattr(self,'coarse',None):
            self.coarse['segments']=[g for g in self.coarse['segments'] if not set(g['window_ids'])&set(wids)]
            self.coarse['verdicts']={k:v for k,v in self.coarse['verdicts'].items() if k not in wids}
        write_json(self.root/'unwatchable.json',self.unwatchable)
        if hasattr(self.d,'state'):self.d.state.set_artifact('browser_output_provider_cut',dict(window_ids=wids,**refusal))
        self.trace.append(dict(action=dict(action='provider_output_cut'),result=dict(window_ids=wids)))

    def _watch(self, wid):
        bound=self.proxy(wid)
        duration=bound['proxy']['source_end_s']-bound['proxy']['source_start_s']
        prompt=('观察这段无声且原对白字幕被黑块遮住的动画，分两栏回答。'
            '第一栏客观事实：仅描述实际看见的人物、动作、交互方向、空间位置、反应和前后变化；职业身份不明就不猜。'
            '遮挡区看不见的动作不能补造；嘴张开、微笑不自动等于讲话。第一栏不得为迎合下面的需求而改写或省略。'
            '第二栏goal_fit带题作答：goal是参考要演的人物关系与对话递进，逐条判断本片段哪几秒经重新配音剪辑后能承载它，'
            '写need（承载哪种关系或节拍）、start_s/end_s（本片相对秒）、fact_index（支撑它的visible_facts下标）、reason；'
            '承载不了就不列，宁缺不编。'
            '如还有能辨认的对白字幕，readable_dialogue_text=true，不引用其内容。'
            '返回JSON {"visible_facts":[],"interaction":[],"uncertainties":[],"occlusion_limits":[],'
            '"goal_fit":[{"need":"","start_s":0,"end_s":1,"fact_index":0,"reason":""}],"readable_dialogue_text":false}。\n'+
            dump(dict(goal=self.goal(),duration_s=round(duration,3))))
        def valid(v):
            return validate_goal_fit(validate_visual(v),duration)
        observed=self.d.call(self.stage('blind',wid),prompt,valid,bound['proxy']['path'])
        fallback=None
        band=source_subtitle_band(bound['source_sha256']) if observed['readable_dialogue_text'] else None
        if band:
            first=observed
            bound=self.banded_proxy(wid,band)
            observed=self.d.call(self.stage('blindband',wid),prompt,valid,bound['proxy']['path'])
            # Later steps (continuity, casting views, final mosaic) use the view
            # that was actually verified, so the band follows only this window.
            self.proxies[wid]=bound
            write_json(self.root/(wid+'_media.json'),bound)
            fallback=dict(reason='readable dialogue text remained after per-window masks',
                first_observation=first,band=band)
        self.visual[wid]=dict(window_id=wid,observation=observed,
            evidence_sha256=bound['proxy']['sha256'],subtitle_blind=not observed['readable_dialogue_text'],
            **({'subtitle_band_fallback':fallback} if fallback else {}))
        write_json(self.root/(wid+'_visual.json'),self.visual[wid])
        return self.visual[wid]

    def coarse_frames(self, wid, folder):
        """Four frames of one window straight from the source, the fixed subtitle band
        blacked out: a glance needs no per-window localization call."""
        from .media import verify_source
        window=self.d.load_window(wid)
        source=self.d.route.sources[window['source_sha256']]
        band=source_subtitle_band(window['source_sha256'])
        start,end=window['source_start_s'],window['source_end_s']
        vf='scale=240:-2'
        if band:
            r=band['region']
            vf=(f"drawbox=x=iw*{r['x']}:y=ih*{r['y']}:w=iw*{r['w']}:h=ih*{r['h']}:color=black:t=fill,"+vf)
        frames=[]
        for k,ratio in enumerate(COARSE_FRAMES):
            local=(end-start)*ratio;out=folder/f'{wid}_{k}.jpg'
            if not out.exists():
                subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-ss',f'{start+local:.3f}','-i',str(verify_source(source)),
                    '-frames:v','1','-vf',vf,'-threads','1',str(out)],check=True,capture_output=True,timeout=60)
            frames.append((out,local))
        return frames,band

    def coarse_sheet(self, chunk, number, folder):
        from PIL import Image, ImageDraw
        from .material_confirm import _label_font
        labels={chr(65+i):wid for i,wid in enumerate(chunk)}
        sheet=Image.new('RGB',(4*240,160*len(chunk)),'white');draw=ImageDraw.Draw(sheet)
        bands=set()
        for row,(label,wid) in enumerate(labels.items()):
            frames,band=self.coarse_frames(wid,folder)
            bands.add(bool(band))
            for col,(frame,local) in enumerate(frames):
                with Image.open(frame) as picture:sheet.paste(picture.convert('RGB'),(col*240,row*160))
                draw.text((col*240+4,row*160+137),f'{label}  +{local:.1f}s',fill='black',font=_label_font(18))
        path=folder/f'sheet_{number}.jpg';sheet.save(path,quality=85)
        prompt=('带题粗看：先读goal了解参考要演的人物关系与对话递进，再看图。'
            '图中每一行是一段约15-17秒的连续片段，从左到右是按时间顺序的4帧，原对白字幕区域已涂黑。'
            '判断这段画面经重新配音和剪辑后，能否承载goal里的某种人物关系或对话递进；不要求原道具、原职业或原剧情。'
            '缩略图不能确认嘴动和完整动作，只作粗略判断：strong=人物与互动关系明显可用；'
            'possible=有可能，需要看视频确认；no=看不出可用（单人、标题卡、无关动作等）。'
            'strong和possible另给score整数1-5，5=最适合goal，用于同档排序。'
            '每个编号都必须回答一次，只用图中编号。'
            '返回JSON {"rows":[{"label":"A","fit":"strong","score":4,"can_carry":"可承载的关系或节拍","reason":"图中依据"}]}。\n'+
            dump(dict(goal=self.goal(),labels=list(labels))))
        try:
            answer=self.d.call(self.stage('coarse',str(number)),prompt,
                lambda v,labels=labels:validate_coarse(v,list(labels)),str(path),image=True)
        except (LibraryStopped,ValueError) as exc:
            if not observation_unusable(exc):raise
            # Unscored, not judged: these windows are not shortlisted from this sheet but stay in
            # the catalog for the director to open.
            answer=dict(rows=[dict(label=l,fit='no',score=0,can_carry='',reason='粗看结果不可用（'+str(exc)[:120]+'），未评分')
                for l in labels],unusable=str(exc))
            if hasattr(self.d,'state'):self.d.state.set_artifact('browser_coarse_sheet_unusable',dict(sheet=number,
                window_ids=list(labels.values()) if isinstance(labels,dict) else list(labels),error=str(exc)))
        return labels,answer,all(bands)

    def coarse_neighbors(self, wid):
        """Catalog windows that continue this one in the source (one each side, if any)."""
        row=self.by_id[wid]
        after=[o for o in self.catalog if continues(row,o)]
        before=[o for o in self.catalog if continues(o,row)]
        out=[]
        if after:out.append(min(after,key=lambda o:float(o['start_s']))['window_id'])
        if before:out.append(max(before,key=lambda o:float(o['start_s']))['window_id'])
        return out

    def coarse_screen(self):
        """Glance at every promising window with the question in mind, then at the
        neighbours of the good ones so whole scenes can be judged, and watch the best
        scene segments from distinct parts of the source before any comparison."""
        order={r['window_id']:i for i,r in enumerate(self.catalog)}
        promising=sorted({w for s in self.scans for w in s.get('promising',[]) if w in order},key=order.get)
        folder=self.root/'coarse';folder.mkdir(exist_ok=True)
        verdicts={};sheets=[];number=0
        def glance(ids,reason):
            nonlocal number
            for n in range(0,len(ids),COARSE_PER_SHEET):
                number+=1
                labels,answer,banded=self.coarse_sheet(ids[n:n+COARSE_PER_SHEET],number,folder)
                for r in answer['rows']:
                    verdicts[labels[r['label']]]=dict(window_id=labels[r['label']],fit=r['fit'],score=r.get('score',0),
                        can_carry=r.get('can_carry',''),reason=r['reason'],sheet=number,via=reason)
                sheets.append(dict(sheet=number,labels=labels,fixed_band=banded,via=reason))
        glance(promising,'catalog_promising')
        good=lambda w:verdicts[w]['fit']=='strong' or verdicts[w].get('score',0)>=4
        frontier=[w for w in promising if good(w)]
        for hop in range(1,COARSE_NEIGHBOR_HOPS+1):
            fresh=sorted({n for w in frontier for n in self.coarse_neighbors(w) if n not in verdicts},key=order.get)
            if not fresh:break
            glance(fresh,f'neighbor_hop_{hop}')
            frontier=[w for w in fresh if verdicts[w]['fit']!='no']
        segments=self.coarse_segments(verdicts,order)
        chosen,budget=[],COARSE_WATCH_LIMIT
        for seg in segments:
            if len(chosen)>=COARSE_SHORTLIST or budget<=0:break
            if any(seg['source_sha256']==c['source_sha256'] and
                   seg['start_s']-COARSE_REGION_S<c['end_s'] and c['start_s']<seg['end_s']+COARSE_REGION_S for c in chosen):continue
            seg=dict(seg,watch_ids=seg['watch_ids'][:budget]);budget-=len(seg['watch_ids'])
            chosen.append(seg)
        self.coarse=dict(policy='question_first_coarse_v3',promising=len(promising),sheets=sheets,
            verdicts=verdicts,segments=chosen,shortlist=[w for c in chosen for w in c['watch_ids']])
        write_json(self.root/'coarse.json',self.coarse)
        for seg in chosen:
            for wid in seg['watch_ids']:self.watch(wid)
            # Adjacent windows are only a guess of one scene; an independent
            # continuity review decides whether they really are.
            if len(seg['watch_ids'])>1:self.audit_continuity(seg['watch_ids'])
        return self.coarse

    def coarse_segments(self, verdicts, order):
        """Runs of touching windows that the glance found usable, best first.

        A dialogue scene often spans two or three 15-17 s windows; ranking single
        windows would watch only one slice of a scene, or spend picks on one scene."""
        rank={'strong':0,'possible':1}
        usable=sorted((w for w,v in verdicts.items() if v['fit'] in rank),
            key=lambda w:(self.by_id[w]['source_sha256'],float(self.by_id[w]['start_s'])))
        runs=[]
        for wid in usable:
            row=self.by_id[wid];last=runs[-1][-1] if runs else None
            if last and continues(self.by_id[last],row):
                runs[-1].append(wid)
            else:runs.append([wid])
        out=[]
        for run in runs:
            best=min(rank[verdicts[w]['fit']] for w in run)
            # Keep a contiguous slice around the strongest window when the run is long.
            anchor=min(range(len(run)),key=lambda i:(rank[verdicts[run[i]]['fit']],-verdicts[run[i]].get('score',0),i))
            lo=max(0,min(anchor-COARSE_SEGMENT_WINDOWS//2,len(run)-COARSE_SEGMENT_WINDOWS))
            out.append(dict(window_ids=run,watch_ids=run[lo:lo+COARSE_SEGMENT_WINDOWS],fit=['strong','possible'][best],
                strong=sum(verdicts[w]['fit']=='strong' for w in run),source_sha256=self.by_id[run[0]]['source_sha256'],
                start_s=float(self.by_id[run[0]]['start_s']),end_s=float(self.by_id[run[-1]]['end_s']),
                can_carry=[verdicts[w]['can_carry'] for w in run]))
        # Rank by the glance itself; ties broken by a position-neutral hash, never by catalog
        # order (21 d9bca80a: order ties put all eight picks in the first 1.7 h of 6.1 h).
        for g in out:g['score']=max(verdicts[w].get('score',0) for w in g['window_ids'])
        return sorted(out,key=lambda g:(['strong','possible'].index(g['fit']),-g['score'],-g['strong'],json_sha(g['window_ids'])))

    def overview(self, ids, turn):
        """Visual browsing can discover things missing from the textual summary."""
        from PIL import Image, ImageDraw
        from .material_confirm import _label_font
        folder=self.root/f'overview_{turn}'
        folder.mkdir(exist_ok=True)
        sheet=Image.new('RGB',(960,200*len(ids)),'white')
        mapping=[]
        labels={f'R{i+1}':wid for i,wid in enumerate(ids)}
        for row,wid in enumerate(ids):
            proxy=self.proxy(wid)['proxy']
            duration=proxy['source_end_s']-proxy['source_start_s']
            for col,ratio in enumerate((.15,.5,.85)):
                t=duration*ratio;frame=folder/f'{row}_{col}.jpg'
                subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(t),'-i',proxy['path'],
                    '-frames:v','1','-threads','1',str(frame)],check=True,capture_output=True,timeout=45)
                with Image.open(frame) as original:
                    picture=original.convert('RGB');picture.thumbnail((320,175))
                    sheet.paste(picture,(col*320,row*200))
                label=f'R{row+1}'
                ImageDraw.Draw(sheet).text((col*320+4,row*200+177),f'{label} +{t:.1f}s',fill='black',font=_label_font(20))
                mapping.append(dict(label=label,window_id=wid,local_s=t,media_sha256=proxy['sha256']))
        path=folder/'overview.jpg';sheet.save(path,quality=85)
        write_json(folder/'mapping.json',mapping)
        def valid(v):
            return normalize_overview_selection(v,labels)
        try:
            return self.d.call(self.stage('overview',str(turn)),
                '按图中编号看缩略图。只提议值得看动态视频的片段，不凭静态图确认嘴动、动作或完整对话。'
                'worth_watching只填图中短编号字符串，解释放notes；编号不是场景名称或判断依据。'
                '返回JSON {"worth_watching":[],"notes":"画面依据及不确定性"}。允许编号：'+dump(list(labels)),valid,str(path),image=True)
        except (LibraryStopped,ValueError) as exc:
            if not observation_unusable(exc):raise
            if hasattr(self.d,'state'):self.d.state.set_artifact('browser_overview_unusable',dict(turn=turn,window_ids=ids,error=str(exc)))
            return dict(worth_watching=[],notes='缩略图总览不可用（'+str(exc)[:120]+'），本轮没有提议；可直接watch或查看目录。')

    def audit_continuity(self, ids):
        """A second observer groups actual footage without the target or proposal."""
        from .media import _run,probe_media,sha256_file
        ids=[w for w in ids if w not in self.unwatchable]
        if not ids:return None
        ids=sorted(ids,key=lambda w:self.by_id[w]['start_s'])
        key=continuity_request_key(ids)
        folder=self.root/('continuity_'+key);folder.mkdir(exist_ok=True)
        mapping=[];offset=0;clips=[]
        for wid in ids:
            proxy=self.proxy(wid)['proxy'];clips.append(proxy['path'])
            duration=proxy['media_duration_s']
            mapping.append(dict(window_id=wid,start_s=offset,end_s=offset+duration,
                media_duration_s=duration,media_sha256=proxy['sha256'],
                source_sha256=self.by_id[wid]['source_sha256'],
                source_start_s=proxy['source_start_s'],source_end_s=proxy['source_end_s']))
            offset+=duration
        listing=folder/'concat.txt'
        listing.write_text(''.join("file '"+p.replace("'","'\\''")+"'\n" for p in clips))
        video=folder/'continuity.mp4'
        _run(['ffmpeg','-v','error','-nostdin','-y','-f','concat','-safe','0','-i',str(listing),
            '-map','0:v:0','-c:v','copy','-an','-sn','-dn','-map_metadata','-1',str(video)])
        video,measured=continuity_transport(video,offset)
        if any(s.get('codec_type')=='audio' for s in measured['streams']):
            raise LibraryStopped('browser_continuity_proxy_must_be_silent')
        def valid(value):
            return validate_continuity_groups(value,mapping)
        prompt=continuity_prompt(mapping)
        try:result=self.d.call(self.stage('continuity',key),prompt,valid,str(video))
        except (LibraryStopped,ValueError) as exc:
            if not observation_unusable(exc):raise
            # Continuity not established: no group is invented, so these windows cannot form a
            # candidate; the director is told and chooses among reviewed groups.
            self.continuity_failed=getattr(self,'continuity_failed',set())|set(ids)
            self.audited_sets.add(frozenset(ids))
            if hasattr(self.d,'state'):self.d.state.set_artifact('browser_continuity_unusable',dict(window_ids=ids,error=str(exc)))
            return None
        bound=dict(policy=CONTINUITY_POLICY,mapping=mapping,media_sha256=sha256_file(video),result=result)
        write_json(folder/'audit.json',bound)
        for group in result['groups']:
            group_id='cg_'+json_sha(dict(media_sha256=bound['media_sha256'],group=group))[:16]
            self.scene_groups[group_id]=dict(scene_group_id=group_id,**group)
        self.audited_sets.add(frozenset(ids))
        write_json(self.root/'scene_groups.json',list(self.scene_groups.values()))
        return bound

    def validate_finish(self, value):
        scenes=value.get('candidates')
        if not isinstance(scenes,list) or not scenes:raise ValueError('provide ranked actual scene candidates')
        seen_sets=set()
        for i,scene in enumerate(scenes):
            ids=scene.get('window_ids') if isinstance(scene,dict) else None
            if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)):
                raise ValueError(f'candidates[{i}].window_ids must be a non-empty list of distinct window IDs')
            unwatched=[w for w in ids if w not in self.visual]
            readable=[w for w in ids if w in self.visual and not self.visual[w]['subtitle_blind']]
            if unwatched or readable:
                raise ValueError(f'candidates[{i}] uses windows without a usable subtitle-blind observation: '
                    f'not yet watched {unwatched}, still showing readable dialogue text {readable}. '
                    'Watch unwatched windows first, or drop these windows; readable-text windows cannot be candidates.')
            key=frozenset(ids)
            if key in seen_sets:raise ValueError(f'candidates[{i}] duplicates an earlier candidate')
            seen_sets.add(key)
            if len({self.by_id[w]['source_sha256'] for w in ids})!=1:
                raise ValueError(f'candidates[{i}] mixes source videos; one continuous candidate must come from one source')
            if scene.get('scene_group_id') or any(set(ids)<=audited for audited in self.audited_sets):
                failed=sorted(set(ids)&getattr(self,'continuity_failed',set()))
                if failed and scene.get('scene_group_id') not in self.scene_groups:
                    raise ValueError(f'candidates[{i}] uses windows {failed} whose continuity review could not be completed, '
                        'so they are not an established scene; drop this candidate and choose among independent_scene_groups')
                group=self.scene_groups.get(scene.get('scene_group_id'))
                if group is None or set(ids)!=self.usable_group_windows(group):
                    options=[dict(scene_group_id=gid,window_ids=sorted(self.usable_group_windows(g)))
                             for gid,g in self.scene_groups.items()
                             if set(ids)&{r['window_id'] for r in g['segments']} and self.usable_group_windows(g)]
                    raise ValueError(f'candidates[{i}] scene_group_id={scene.get("scene_group_id")!r} with window_ids={ids} '
                        'does not equal the usable windows of one independently observed scene group. Use one of these '
                        'exactly (its scene_group_id and these window_ids; windows still showing readable dialogue are '
                        'already left out), never merge groups: '+dump(options))
            for k in ('visual_alone','editing_can_add','dubbing_can_add','limitations','continuity_evidence'):
                if not isinstance(scene.get(k),str) or not scene[k].strip():
                    raise ValueError(f'candidates[{i}] needs a non-empty string field {k}')
        if len(scenes)<2 and value.get('only_one_candidate_reason','').strip()=='':
            raise ValueError('compare alternatives or explain why only one usable scene was found')
        if not isinstance(value.get('selection_reason'),str) or not value['selection_reason'].strip():
            raise ValueError('explain actual candidate comparison')
        return value

    def usable_group_windows(self, group):
        """A scene group's windows that may stand in a candidate: watched, without readable dialogue.

        02 (36747a0228fc): one window of a three-window group still showed readable dialogue;
        "use the whole group" and "no readable windows" could not both hold, and every reply
        alternated between the two until repairs ran out."""
        return {r['window_id'] for r in group['segments']
                if r['window_id'] in self.visual and self.visual[r['window_id']].get('subtitle_blind')}

    def grounded_candidate(self, scene):
        group=self.scene_groups[scene['scene_group_id']]
        scene['continuity_audit']=group
        # The coordinator proposes adaptations; it cannot rewrite the neutral
        # observer's facts while producing the final navigation summary.
        scene['visual_alone']='；'.join(group[k] for k in ('setting','participants','evidence','limitations'))
        scene['continuity_evidence']=group['evidence']
        scene['usable_segments']=[dict(window_id=r['window_id'],
            source_start_s=self.proxies[r['window_id']]['proxy']['source_start_s']+r['start_s'],
            source_end_s=min(self.proxies[r['window_id']]['proxy']['source_end_s'],
                self.proxies[r['window_id']]['proxy']['source_start_s']+r['end_s']))
            for r in group['segments'] if r['window_id'] in scene['window_ids']]
        intervals=sorted((r['source_start_s'],r['source_end_s']) for r in scene['usable_segments'])
        merged=[]
        for a,b in intervals:
            if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
            else:merged.append([a,b])
        scene['unique_source_duration_s']=round(sum(b-a for a,b in merged),6)
        scene['limitations']=group['limitations']+'；'+scene['limitations']
        return scene

    def adjacent_window(self, wid, side):
        """The eligible catalog slice that continues this one in the same source."""
        row=self.by_id[wid]
        rows=[r for r in self.catalog if r['source_sha256']==row['source_sha256'] and r['window_id']!=wid]
        if side=='after':
            rows=[r for r in rows if row['start_s']<r['start_s']<=row['end_s']+1.0]
            return min(rows,key=lambda r:r['start_s'],default=None)
        rows=[r for r in rows if row['start_s']-1.0<=r['end_s']<row['end_s'] and r['start_s']<row['start_s']]
        return max(rows,key=lambda r:r['start_s'],default=None)

    def edge_extensions(self, candidates):
        """Scene groups cut by a slice edge, paired with the slice that may continue them.

        Only the window list is decided here; whether the footage really is the
        same scene is left to a new independent continuity review.
        """
        # The budget belongs to one grouping definition; reviews made under an
        # older definition produced different groups and do not spend it.
        done=[e for e in self.comparison_progress.setdefault('edge_extensions',[])
              if e.get('continuity_policy')==CONTINUITY_POLICY]
        budget=EDGE_EXTENSION_LIMIT-len(done)
        # A recorded extension is replayed on resume: its watch and review reuse the
        # received replies, so the later decision sees the same groups and it costs
        # no new budget. Only an extension never made before spends budget.
        recorded={frozenset(e['window_ids']) for e in done}
        out=[];fresh=0
        # Budget goes to the coordinator's first choice; a re-ranked list gets its new leader extended.
        for scene in candidates[:1]:
            group=self.scene_groups.get(scene.get('scene_group_id'))
            if group is None:continue
            windows=sorted({r['window_id'] for r in group['segments']},key=lambda w:self.by_id[w]['start_s'])
            for side,wid in (('after',windows[-1]),('before',windows[0])):
                local=[r for r in group['segments'] if r['window_id']==wid]
                duration=self.proxies[wid]['proxy']['media_duration_s'] if wid in self.proxies else \
                    self.by_id[wid]['end_s']-self.by_id[wid]['start_s']
                touches=(max(r['end_s'] for r in local)>=duration-EDGE_TOUCH_S if side=='after'
                    else min(r['start_s'] for r in local)<=EDGE_TOUCH_S)
                neighbor=self.adjacent_window(wid,side) if touches else None
                if neighbor is None or neighbor['window_id'] in windows:continue
                ids=sorted(windows+[neighbor['window_id']],key=lambda w:self.by_id[w]['start_s'])
                key=frozenset(ids)
                if key in self.audited_sets or any(key==frozenset(e['window_ids']) for e in out):continue
                replay=key in recorded
                if not replay and fresh>=budget:continue
                fresh+=0 if replay else 1
                out.append(dict(scene_group_id=group['scene_group_id'],side=side,
                    added_window_id=neighbor['window_id'],window_ids=ids,continuity_policy=CONTINUITY_POLICY,
                    **({'replay':True} if replay else {})))
        return out

    def restore_comparison_progress(self):
        fingerprint=json_sha(self.goal())
        if self.comparison_progress.get('goal_sha256',fingerprint)!=fingerprint:
            raise LibraryStopped('comparison_goal_changed')
        self.comparison_progress['goal_sha256']=fingerprint
        write_json(self.root/'comparison_progress.json',self.comparison_progress)

    def run(self, feedback=None):
        if not self.catalog:
            self.scan()
        # INC-20261116-06: a resume replays navigation turn by turn from the ledger.
        # Preloading the ancestor's final watched set and comparison rounds made the
        # first decision differ from every recorded one, so selection, cast, script
        # and voices were all asked again. Watches still reuse exact received replies.
        self.restore_comparison_progress()
        if feedback is None and not getattr(self,'coarse',None):self.coarse_screen()
        watched_before_followup=set(self.visual)
        followup_refusals=[0]
        turn=getattr(self,'navigation_turn',0)
        while True:
            turn+=1
            self.navigation_turn=turn
            progress=self.comparison_progress
            pending=next((r for r in progress['rounds'] if not r.get('completed')),None)
            if pending:
                for wid in pending['window_ids']:self.watch(wid)
                self.audit_continuity(pending['window_ids'])
                pending['completed']=True
                write_json(self.root/'comparison_progress.json',progress)
            force_watch=progress['active'] and len(progress['rounds'])<2
            fresh_ids=[r['window_id'] for r in self.catalog if r['window_id'] not in self.visual and r['window_id'] not in self.unwatchable]
            if not fresh_ids:force_watch=False
            context=dict(goal=self.goal(),
                pages=[dict(page=i+1,**s) for i,s in enumerate(self.scans)],
                watched=list(self.visual.values()),independent_scene_groups=list(self.scene_groups.values()),history=self.trace[-5:],
                **(dict(provider_refused_windows=sorted(self.unwatchable)) if self.unwatchable else {}),
                **(dict(coarse_screen=dict(segments_watched=[{k:g[k] for k in ('window_ids','watch_ids','fit','can_carry')}
                        for g in self.coarse['segments']],
                    candidates=[v for v in self.coarse['verdicts'].values() if v['fit']!='no'])) if getattr(self,'coarse',None) else {}),
                **(dict(followup_needs=feedback) if feedback else {}))
            prompt=(('已带题粗看全部promising窗口（coarse_screen.candidates是粗看结论），按相邻窗口合成场景片段，'
                '并已实际watch了其中来自不同区域的片段（coarse_screen.segments_watched，多窗口片段已做连续性复核）；'
                'watched里的goal_fit是带题精看的作答。先比较这些不同场景，必要时再watch其他粗看为strong或possible的窗口，'
                '再按适用程度排序finish。' if getattr(self,'coarse',None) else '')+
                '你负责自主浏览全库找二创素材。已通读全部目录，不受关键词或相似度排序限制。'
                '目录和旧用途只是线索，先看不同候选的画面，再实际watch视频和前后窗口，比较连续场景。'
                '目录中的无音频通常指建库代理，不能据此断言原片没有对白；旧目录解释不能当作独立看片事实。'
                '优先静音画面就能承载参考的互动和情绪走向，但这只是加分项；普通交谈可通过剪辑配音成立。'
                '无需精确商品、职业、动作，不要求静音画面复演完整参考。不得把配音才能表达的意思说成已看见。'
                '比较候选时说明双方互动、人物辨认、可用视角与反应、对白长度和重复风险，按实际观察说明取舍；不是按精确道具匹配。每轮JSON动作：{"action":"catalog_page","page":1}浏览任意页完整目录；'
                '{"action":"details","window_ids":[]}查看任意窗口全部原观察与条件性用途；'
                '{"action":"overview","window_ids":[]}看最多6个窗口的遮字幕缩略图；'
                '{"action":"watch","window_ids":[]}独立静音看最多3个窗口；'
                '{"action":"neighbors","window_id":"...","before_s":45,"after_s":45}查看前后目录；'
                '最后返回{"action":"finish","candidates":[{"window_ids":[],"scene_group_id":"独立复看的组ID，尚无复看时暂省略",'
                '"visual_alone":"画面单独表达什么",'
                '"editing_can_add":"剪辑能补什么","dubbing_can_add":"配音能补什么","limitations":"明确限制",'
                '"continuity_evidence":"连续场景的实际依据"}],"selection_reason":"按适用程度排序并比较不同场景",'
                '"only_one_candidate_reason":"仅一组时如实解释"}。候选必须已watch且无可读对白字幕。'
                '相邻时间不等于同场，须看连续性。每个候选内部只能属于一个independent_scene_group，不能跨组拼接成虚假连续场景；'
                'candidates列表可以并列多个不同场景组，逐组比较排序。独立分组本身不是只能返回一个候选的理由；'
                '只写该组实际可见的事实和人物区别，不能把别组动作套过来，具体职业与动机留给后续编剧。'
                '继续探索必须带来新目录或画面证据。'
                '若有provider_refused_windows，是模型接口明确拒绝分析的窗口，不能再watch或列入候选，改看其他窗口。\n'+dump(context))
            if not self.visual:
                prompt=('当前watched为空：你尚未实际看过任何候选视频。当前阶段禁止finish，禁止只凭目录提交结论。'
                    '先返回watch、overview、details、catalog_page或neighbors中的一个工具动作。\n'+prompt)
            if force_watch:
                prompt=('当前仅一组候选尚未完成实际比较。本轮必须返回watch，选择最多3个尚未看过、最有希望形成另一场戏的窗口。'
                        '不要重复现有片段；先看新的场景，后续按实际观察排序或说明淘汰原因。剩余补看轮数：'
                        +str(2-len(progress['rounds']))+'。\n'+prompt)
            def valid(v):
                if not isinstance(v,dict):raise ValueError('navigation action required')
                action=v.get('action')
                if force_watch and (action!='watch' or not isinstance(v.get('window_ids'),list)
                        or any(w not in fresh_ids for w in v['window_ids'])):
                    seen=[w for w in (v.get('window_ids') or []) if isinstance(w,str) and w not in fresh_ids]
                    raise ValueError('this round must be {"action":"watch","window_ids":[...]} with 1-3 catalog windows not yet watched; '
                        f'received action={action!r}; already watched or unknown: {seen}')
                if action=='finish' and feedback and fresh_ids and not set(self.visual)-watched_before_followup:
                    followup_refusals[0]+=1
                    if followup_refusals[0]>1:
                        # Asked once to look further, the director again judged that no unwatched window can
                        # fill the gap: keep the compared candidates (user 11-17: not a stop); casting then
                        # adapts to them or moves to another candidate.
                        self.d.state.set_artifact('browser_followup_kept_current',dict(reason=str(feedback.get('reason'))[:500],
                            refusals=followup_refusals[0],watched=len(self.visual)))
                        return self.validate_finish(v)
                    raise ValueError('这是后续阶段提出的补充请求，缺口见followup_needs：'+str(feedback.get('reason'))[:300]+
                        '。finish前必须先watch至少1个尚未看过、最可能补上该缺口的窗口（可先catalog_page、details或overview找线索），'
                        '不能把已看候选原样交回；看过后如实比较并说明是否补上缺口。')
                if action=='finish':return self.validate_finish(v)
                if action=='catalog_page':
                    if type(v.get('page')) is not int or not 1<=v['page']<=len(self.catalog_pages):raise ValueError('unknown page')
                elif action=='neighbors':
                    if v.get('window_id') not in self.by_id:raise ValueError('unknown window')
                    if any(type(v.get(k)) not in (int,float) or not math.isfinite(v[k]) or not 0<=v[k]<=120
                           for k in ('before_s','after_s')):raise ValueError('technical neighbor span must be finite, 0..120 seconds')
                elif action in {'details','overview','watch'}:
                    ids=v.get('window_ids');limit=6 if action=='overview' else 3 if action=='watch' else 120
                    if not isinstance(ids,list) or not 1<=len(ids)<=limit or any(w not in self.by_id for w in ids):
                        unknown=[w for w in (ids if isinstance(ids,list) else []) if w not in self.by_id]
                        raise ValueError(f'{action} needs 1-{limit} catalog window IDs; received '
                            f'{len(ids) if isinstance(ids,list) else type(ids).__name__}, unknown IDs {unknown}')
                else:raise ValueError('unknown browsing action')
                return v
            # A received reply that fails a newly enforced rule gets one bounded
            # protocol correction on resume instead of stopping the task.
            valid.revalidated_protocol_repair=True
            try:action=self.d.call(self.stage('decide',str(turn)),prompt,valid)
            except ProviderOutputCut as cut:
                self.drop_cut_material(cut)
                continue
            name=action['action']
            if name=='finish':
                if len(action['candidates'])<2 and not progress['active']:
                    progress['active']=True
                    write_json(self.root/'comparison_progress.json',progress)
                    if fresh_ids:continue
                action['comparison_audit']=dict(
                    supplemented_rounds=len(progress['rounds']),watched_count=len(self.visual),
                    status='compared' if len(action['candidates'])>1 else 'limited_comparison',
                    reason=action.get('only_one_candidate_reason') or action['selection_reason'])
                unaudited=[s['window_ids'] for s in action['candidates']
                    if not s.get('scene_group_id') and frozenset(s['window_ids']) not in self.audited_sets]
                if unaudited:
                    for ids in unaudited:self.audit_continuity(ids)
                    self.trace.append(dict(action=dict(action='independent_continuity_review'),
                        result=list(self.scene_groups.values())))
                    write_json(self.root/'navigation.json',self.trace)
                    continue
                extensions=self.edge_extensions(action['candidates'])
                if extensions:
                    for ext in extensions:
                        self.watch(ext['added_window_id'])
                        self.audit_continuity(ext['window_ids'])
                        if not ext.get('replay'):
                            progress['edge_extensions'].append(ext)
                            write_json(self.root/'comparison_progress.json',progress)
                    self.trace.append(dict(action=dict(action='scene_edge_continuation',
                        note='候选场景组到达17秒窗口边界，程序已静音观看相邻窗口并独立复核是否同场延续；'
                             '按新的independent_scene_groups重新比较，不同组仍不能拼接。',
                        extensions=extensions),result=list(self.scene_groups.values())))
                    write_json(self.root/'navigation.json',self.trace)
                    continue
                for scene in action['candidates']:
                    self.grounded_candidate(scene)
                write_json(self.root/'candidates.json',dict(policy=POLICY,**action))
                return action
            if name=='catalog_page':result=self.catalog_pages[action['page']-1]
            elif name=='details':result=[self.d.load_window(w)['observation'] for w in action['window_ids']]
            elif name=='overview':result=self.overview(action['window_ids'],turn)
            elif name=='watch':
                if force_watch:
                    pending=dict(window_ids=action['window_ids'],completed=False)
                    progress['rounds'].append(pending)
                    write_json(self.root/'comparison_progress.json',progress)
                result=[self.watch(w) for w in action['window_ids']]
                if force_watch:
                    self.audit_continuity(action['window_ids'])
                    pending['completed']=True
                    write_json(self.root/'comparison_progress.json',progress)
            else:
                c=self.by_id[action['window_id']]
                result=[r for r in self.catalog if r['source_sha256']==c['source_sha256']
                    and r['end_s']>c['start_s']-action['before_s'] and r['start_s']<c['end_s']+action['after_s']]
            entry=dict(action=action,result=result)
            if self.trace and json_sha(self.trace[-1])==json_sha(entry):
                raise LibraryStopped('material_browser_repeated_navigation_without_new_evidence')
            self.trace.append(entry)
            write_json(self.root/'navigation.json',self.trace)
