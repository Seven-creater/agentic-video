"""Audio-only background music comparison, bound to original reference returns.

Music identifies possible references; their actual understanding is retrieved,
never replaced with a hand-written template meaning.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess

from .audio_review import review_audio
from .media import probe_media, sha256_file
from .state import file_lock, json_sha, write_json

POLICY='background_music_template_match_v1'
PROMPT='''只听实际音频比较背景音乐；没有视频画面，也没有模板名称或标准答案。
包内是多个独立样本，位置见mapping。比较旋律、伴奏、节奏、音色，忽略对白内容、说话人和配音梗。
相同对白不能证明相同BGM；变调、变速和改编应说明不确定性。没有足够音乐证据时不匹配。
spoken_speech_present指剧情对白或口播，和背景歌曲里的演唱分开判断。
逐样本记录music_audible、spoken_speech_present和可复核的音乐特征。
groups按背景乐同曲分组，每个sample_id恰好出现一次；不能确认者独立成组并说明。
若提供query_id，query_matches仅返回与query背景乐相同的其他样本ID，允许多解或空列表。
不得根据对白或模板常识猜关联主题。不转写对白，不推断价值观。
music_only_ranges仅列确实没有可辨识对白的背景音乐区间；使用样本内部秒数，不确定就空列表。
只输出JSON：{"samples":[{"sample_id":"m000","music_audible":true,"spoken_speech_present":true,
"musical_evidence":"实际伴奏旋律或节奏特征","music_only_ranges":[{"start_s":0,"end_s":1}],"limitations":[]}],
"groups":[{"sample_ids":["m000"],"reason":"音乐依据","confidence":"high|medium|low"}],
"query_matches":[],"limitations":[]}。'''


def music_query_clip(asset,start_s,end_s,output):
    """Extract real sound with lineage; this operation does not remove speech."""
    folder=Path(output).resolve();folder.mkdir(parents=True,exist_ok=True)
    source=Path(asset['path']).resolve();measured=probe_media(source)
    if (sha256_file(source)!=asset['sha256'] or type(start_s) not in (int,float) or
            type(end_s) not in (int,float) or not 0<=start_s<end_s<=measured['duration_s']):
        raise ValueError('music_query_source_or_range_invalid')
    binding=dict(source_path=str(source),source_sha256=asset['sha256'],start_s=start_s,end_s=end_s,
        policy='actual_audio_window_not_speech_separation_v1')
    target=folder/'query.wav';record=folder/'lineage.json'
    with file_lock(folder/'.clip.lock',wait=True):
        if record.exists():
            saved=json.loads(record.read_text())
            if saved['binding']!=binding or sha256_file(target)!=saved['sha256']:
                raise ValueError('music_query_clip_changed_no_replay')
        else:
            if target.exists():raise ValueError('music_query_orphan_clip')
            temporary=folder/'query.part.wav'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-i',str(source),
                '-map','0:a:0','-vn','-af',f'atrim=start={start_s}:end={end_s},asetpts=N/SR/TB',
                '-ar','24000','-ac','1','-c:a','pcm_s16le',str(temporary)],check=True,capture_output=True,timeout=120)
            duration=probe_media(temporary,require_video=False)['duration_s']
            if abs(duration-(end_s-start_s))>.05:raise ValueError('music_query_crop_duration_invalid')
            subprocess.run(['ffmpeg','-v','error','-i',str(temporary),'-f','null','-'],check=True,capture_output=True,timeout=120)
            if sha256_file(source)!=asset['sha256']:raise ValueError('music_query_source_changed')
            temporary.replace(target);write_json(record,dict(binding=binding,sha256=sha256_file(target)))
    return dict(path=str(target),sha256=sha256_file(target),origin_path=str(source),origin_sha256=asset['sha256'],
        lineage_path=str(record),lineage_sha256=sha256_file(record))


def _verify_asset(asset):
    if sha256_file(asset['path'])!=asset['sha256']:raise ValueError('music_source_changed')
    if asset.get('origin_sha256'):
        lineage=json.loads(Path(asset['lineage_path']).read_text())
        if (sha256_file(asset['origin_path'])!=asset['origin_sha256'] or
                sha256_file(asset['lineage_path'])!=asset['lineage_sha256'] or
                lineage['binding']['source_sha256']!=asset['origin_sha256'] or lineage['sha256']!=asset['sha256']):
            raise ValueError('music_source_lineage_changed')


def audio_packet(assets,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    binding=[dict(path=str(Path(a['path']).resolve()),sha256=a['sha256']) for a in assets]
    if not binding:raise ValueError('music_packet_empty')
    for item in binding:
        if sha256_file(item['path'])!=item['sha256']:raise ValueError('music_source_changed')
    target=folder/'audio.mp3';receipt=folder/'packet.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved['binding']!=binding or sha256_file(target)!=saved['sha256']:
            raise ValueError('music_packet_changed')
        return target,saved
    mapping=[];segments=[];offset=0
    for index,item in enumerate(binding):
        source=probe_media(item['path'],require_video=False)
        if not any(s['codec_type']=='audio' for s in source['streams']):
            raise ValueError('music_source_has_no_audio')
        if offset+source['duration_s']>900:raise ValueError('music_packet_over_900_seconds')
        segment=folder/f'audio_{index:03d}.mp3'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-i',item['path'],
            '-map','0:a:0','-vn','-c:a','libmp3lame','-b:a','64k','-ac','1','-ar','24000',str(segment)],
            check=True,capture_output=True,timeout=600)
        duration=probe_media(segment,require_video=False)['duration_s']
        mapping.append(dict(sample_id=f'm{index:03d}',packet_start_s=offset,packet_end_s=offset+duration,
            source_duration_s=source['duration_s'],source_sha256=item['sha256']))
        offset+=duration;segments.append(segment)
    listing=folder/'segments.txt';listing.write_text(''.join("file '"+p.name+"'\n" for p in segments))
    temporary=folder/'audio.part.mp3'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','concat','-safe','1','-i',str(listing),
        '-c','copy',str(temporary)],check=True,capture_output=True,timeout=120)
    if 4*((temporary.stat().st_size+2)//3)>=10_000_000:raise ValueError('music_packet_inline_limit')
    temporary.replace(target)
    saved=dict(policy=POLICY,binding=binding,mapping=mapping,sha256=sha256_file(target),
        audio_scope='original_mixed_audio_no_images_not_a_separated_music_stem')
    write_json(receipt,saved)
    for path in segments:path.unlink()
    return target,saved


def validate_music_observations(raw,mapping,query_id=None):
    """Preserve the received reply; never crop from unchecked model clocks."""
    ids={r['sample_id'] for r in mapping}
    samples=raw.get('samples',[])
    groups=raw.get('groups',[])
    grouped=[x for g in groups for x in g.get('sample_ids',[])]
    if (len(samples)!=len(ids) or {r['sample_id'] for r in samples}!=ids or
            len(grouped)!=len(ids) or set(grouped)!=ids or any(not g.get('sample_ids') for g in groups)):
        raise ValueError('music_sample_identity_invalid')
    matches=raw.get('query_matches',[])
    if not isinstance(matches,list) or len(set(matches))!=len(matches) or any(x not in ids or x==query_id for x in matches):
        raise ValueError('music_match_identity_invalid')
    expected=next((set(g['sample_ids'])-{query_id} for g in groups if query_id in g['sample_ids']),set())
    if set(matches)!=expected:raise ValueError('music_matches_disagree_with_groups')
    durations={r['sample_id']:r['source_duration_s'] for r in mapping}
    valid={};invalid=[]
    for row in samples:
        sample_id=row['sample_id'];valid[sample_id]=[]
        if type(row.get('music_audible')) is not bool or type(row.get('spoken_speech_present')) is not bool:
            raise ValueError('music_observation_boolean_required')
        ranges=row.get('music_only_ranges',[])
        if not isinstance(ranges,list):raise ValueError('music_ranges_list_required')
        for index,window in enumerate(ranges):
            start,end=(window.get('start_s'),window.get('end_s')) if isinstance(window,dict) else (None,None)
            if (type(start) not in (int,float) or type(end) not in (int,float) or
                    not math.isfinite(start) or not math.isfinite(end) or
                    not 0<=start<end<=durations[sample_id] or not row['music_audible']):
                invalid.append(dict(sample_id=sample_id,range_index=index,reason='invalid_source_local_music_range'))
            else:valid[sample_id].append(dict(start_s=start,end_s=end))
    return dict(validated_music_only_ranges=valid,rejected_music_only_ranges=invalid,
        pure_bgm_query_verified=False,note='Range bounds only; a cropped query still requires audio-only speech/music verification.')


def compare_music(assets,output,receipts,*,query_index=None,api=None):
    """Listen once; uncertain calls remain blocked even under a different folder."""
    root=Path(receipts);root.mkdir(parents=True,exist_ok=True)
    sources={s for a in assets for s in (a['sha256'],a.get('origin_sha256')) if s}
    identity=dict(policy=POLICY,source_sha256s=sorted(sources),query_index=query_index,prompt_sha256=json_sha(PROMPT))
    key=json_sha(dict(identity=identity,ordered_sources=[a['sha256'] for a in assets]))
    operation=root/key
    with file_lock(root/'.registry.lock',wait=True):
        registry_path=root/'registry.json'
        registry=json.loads(registry_path.read_text()) if registry_path.exists() else {}
        for old_key,old in registry.items():
            state=root/old_key/'receipt/state.json'
            if sources.intersection(old['source_sha256s']) and state.exists() and json.loads(state.read_text())['status'] in {'submitted','uncertain'}:
                raise RuntimeError('music_unknown_source_no_replay')
        registry[key]=identity;write_json(registry_path,registry)
        audio,packet=audio_packet(assets,operation/'media')
        public=[{k:v for k,v in row.items() if k!='source_sha256'} for row in packet['mapping']]
        query_id=None if query_index is None else f'm{query_index:03d}'
        if query_index is not None and not 0<=query_index<len(assets):raise ValueError('music_query_outside_packet')
        for asset in assets:_verify_asset(asset)
        result=review_audio(audio,operation/'receipt',prompt=PROMPT+'\n'+json.dumps(dict(mapping=public,query_id=query_id)),api=api)
        for asset in assets:_verify_asset(asset)
        if result['http_status']!=200 or not isinstance(result['parsed'],dict):raise ValueError('music_received_reply_invalid')
        raw=result['parsed'];validation=validate_music_observations(raw,packet['mapping'],query_id)
        evidence=dict(policy=POLICY,identity=identity,packet=packet,observations=raw,validation=validation,
            receipt_path=str(operation/'receipt/state.json'),receipt_sha256=sha256_file(operation/'receipt/state.json'),
            response_sha256=sha256_file(operation/'receipt/response.json'),
            note='Background music comparison of mixed source audio; not proof of a clean BGM stem.')
        write_json(Path(output)/'music-comparison.json',evidence)
        return evidence


def register_reference(asset,understanding,output,*,production_version):
    """Index received subject content; no theme labels or template names are injected."""
    if sha256_file(asset['path'])!=asset['sha256']:raise ValueError('music_index_source_changed')
    value=dict(policy=POLICY,asset=asset,understanding=understanding,understanding_sha256=json_sha(understanding),
        production_version=production_version,entry_id=json_sha(dict(source=asset['sha256'],understanding=understanding,version=production_version)))
    write_json(Path(output)/'reference-music-index.json',value)
    return value


def match_reference_music(gallery,query,output,receipts,*,api=None):
    for entry in gallery:
        if entry['understanding_sha256']!=json_sha(entry['understanding']):raise ValueError('music_index_understanding_changed')
        if entry['asset']['sha256'] in {query['sha256'],query.get('origin_sha256')}:
            raise ValueError('music_query_must_be_held_out')
    comparison=compare_music([*[e['asset'] for e in gallery],query],output,receipts,query_index=len(gallery),api=api)
    query_row=next(x for x in comparison['observations']['samples'] if x['sample_id']==f'm{len(gallery):03d}')
    if query_row.get('spoken_speech_present') is not False or query_row.get('music_audible') is not True:
        return dict(status='ineligible_query_not_music_only',comparison=comparison,candidates=[])
    ids=comparison['observations']['query_matches'];by_id={f'm{i:03d}':e for i,e in enumerate(gallery)}
    return dict(status=('shared_music_candidates' if len(ids)>1 else 'matched') if ids else 'no_match',comparison=comparison,candidates=[dict(entry_id=by_id[i]['entry_id'],
        original_understanding=by_id[i]['understanding'],production_version=by_id[i]['production_version']) for i in ids])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--receipts',type=Path,required=True)
    args=p.parse_args();manifest=json.loads(args.manifest.read_text())
    result=compare_music(manifest['assets'],args.output,args.receipts,query_index=manifest.get('query_index'))
    print(json.dumps(dict(status='received',samples=len(result['observations']['samples']),groups=len(result['observations']['groups']))))


if __name__=='__main__':main()
