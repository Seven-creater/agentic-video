"""Observe the delivered AV independently, then compare identities in code.

The observer receives neutral appearance IDs and line intervals, never the cast's
story roles, generated script, reference answer, or editing plan. Its observations
are retained even when a later holistic reviewer says the result is acceptable.
"""
import json
from itertools import combinations
from pathlib import Path
import subprocess
import re
import wave

from .media import sha256_file
from .state import LibraryStopped, write_json

POLICY = 'blind_dialogue_identity_v1'


def identity_contract(context):
    scenes = {s['scene_id']: s['role_bindings'] for s in context.get('scene_cast', [])}
    lines = [r for r in context.get('audio_draft', []) if r.get('mode') != 'narration']
    people, keys = {}, {}
    for sid, bindings in scenes.items():
        for role, binding in bindings.items():
            if binding.get('voice_only'):
                keys[sid, role] = None
                continue
            identity = binding.get('actor') or binding.get('entity_id') or (sid, binding['role_id'])
            key = json.dumps(identity, ensure_ascii=False)
            people.setdefault(key, set()).add(binding['appearance'])
            keys[sid, role] = key
    # Observe silent bystanders as distinct people, without assigning story roles.
    for actor in context.get('visible_actor_catalog',[]):
        key=json.dumps(actor['actor_id'],ensure_ascii=False)
        people.setdefault(key,set()).add(actor['appearance'])
    ids = {key: f'p{i+1}' for i, key in enumerate(sorted(people))}
    catalog = [dict(person_id=ids[key], appearances=sorted(people[key])) for key in sorted(people)]
    expected = [dict(line_id=r['line_id'], scene_id=r['scene_id'], role=r['role'],
        start_s=r['start_s'], end_s=r['end_s'],
        person_id=ids.get(keys[r['scene_id'], r['role']]),
        visual_anchor=bool(r.get('visual_anchor')),allows_occluded_intro=bool(r.get('allows_occluded_intro')),allows_stylized_intro=bool(r.get('allows_stylized_intro')),
        reference_offscreen=bool(r.get('reference_offscreen')),
        voice_only=bool(scenes[r['scene_id']][r['role']].get('voice_only'))) for r in lines]
    return catalog, expected


def observation_prompt(catalog, expected):
    return ('只观察实际视频和音轨，不评价创作是否成功。没有提供剧情答案、预期台词或角色分配。'
        '人物表仅帮助识别外观；不要从外观文字中的职业或原剧情推断新配音身份。'
        '按intervals逐句听写heard_text，为实际听到的不同声音分配稳定voice_id（v1、v2等）。'
        'speaker_id表示这句声音实际属于哪个画面人物（person_id），不是当前画面里的听者。'
        'visible_speaker_ids只列这段中确实看见开口讲话的人；原字幕、新字幕、站位或台词含义都不能证明嘴在动。'
        'basis用visible_speech、established_voice、offscreen或unclear。'
        '动画出现明确张闭嘴变化并配合讲话姿态可算说话，不要求真人逐音素口型；静态张嘴、咀嚼和惊讶不能算。'
        'visible_speech须实际看见声音所属人物讲话；established_voice用于已建立声音归属的反应镜头，'
        'anchor_line_id须指向本片中同一声音和人物有直接讲话证据的句子，可在本句前或后。'
        '只听到画外声音且全片没有可见身份依据时用offscreen、speaker_id=null；不确定用unclear，不猜测。'
        'visible_speaker_ids不能为了配合speaker_id漏掉其他实际讲话者。'
        'evidence简述实际听到和看见的依据。记录身份不一致或无法核验之处，不用剪辑惯例解释矛盾。'
        '只返回JSON {"observations":[{"line_id":"对应ID","heard_text":"实际声音",'
        '"voice_id":"v1","speaker_id":"p1或null","visible_speaker_ids":["p1"],'
        '"basis":"visible_speech","anchor_line_id":null,"evidence":"声画证据"}],'
        '"contradictions":[],"limitations":[]}。所有interval必须恰好出现一次。\n' +
        json.dumps(dict(people=catalog, intervals=[{k: r[k] for k in ('line_id', 'start_s', 'end_s')}
                                                  for r in expected]), ensure_ascii=False))


def verify_observations(observed, catalog, expected):
    """No model-supplied success flag can bypass the identity/voice checks."""
    rows = observed.get('observations') if isinstance(observed, dict) else None
    ids = {p['person_id'] for p in catalog}
    wanted = {r['line_id'] for r in expected}
    complete = (isinstance(rows, list) and len(rows) == len(expected) and
        all(isinstance(r, dict) and isinstance(r.get('line_id'), str) for r in rows) and
        {r['line_id'] for r in rows} == wanted)
    if not complete:
        return dict(policy=POLICY, passed=False, complete=False, checks=[], issues=['incomplete_observations'])
    by_id = {r['line_id']: r for r in rows}
    checks, voices, roles = [], {}, {}
    for line in expected:
        row, errors = by_id[line['line_id']], []
        person, visible = row.get('speaker_id'), row.get('visible_speaker_ids')
        voice, basis = row.get('voice_id'), row.get('basis')
        valid = ((voice is None or isinstance(voice, str) and bool(voice.strip())) and
            (person is None or isinstance(person, str) and person in ids) and
            isinstance(visible, list) and all(isinstance(p, str) and p in ids for p in visible) and
            basis in ('visible_speech', 'occluded_intro', 'stylized_intro', 'established_voice', 'offscreen', 'unclear') and
            isinstance(row.get('heard_text'),str) and
            isinstance(row.get('evidence'),str) and bool(row['evidence'].strip()))
        if not valid:
            errors.append('invalid_observation_fields')
        else:
            if voice is None:
                errors.append('voice_identity_unverifiable')
            if not row['heard_text'].strip():
                errors.append('speech_not_transcribed')
            if person != line['person_id']:
                errors.append('speaker_identity_unverifiable' if person is None else 'speaker_identity_mismatch')
            if basis == 'unclear':
                errors.append('speaker_identity_unverifiable')
            if set(visible) - {line['person_id']}:
                errors.append('different_person_visibly_speaking')
            if basis in {'occluded_intro','stylized_intro'}:
                opening=row.get('opening_visible_actor_ids',[])
                covered=row.get('opening_concealed_mouth_ids',[]) if basis=='occluded_intro' else row.get('opening_stylized_actor_ids',[])
                permission=line.get('allows_occluded_intro') if basis=='occluded_intro' else line.get('allows_stylized_intro')
                if (not permission or person is None or opening!=[person] or person not in covered or visible):
                    errors.append('occluded_introduction_unverified')
            elif line['visual_anchor'] or basis == 'visible_speech':
                if basis != 'visible_speech' or person is None or person not in visible:
                    errors.append('visible_anchor_missing')
            if basis == 'established_voice':
                anchor_id = row.get('anchor_line_id')
                anchor = by_id.get(anchor_id, {}) if isinstance(anchor_id, str) else {}
                if (anchor_id == line['line_id'] or anchor.get('basis') not in {'visible_speech','occluded_intro','stylized_intro'} or
                        anchor.get('voice_id') != voice or person is None or
                        anchor.get('speaker_id') != person or person not in (anchor.get('visible_speaker_ids') or anchor.get('opening_concealed_mouth_ids') or anchor.get('opening_stylized_actor_ids') or [])):
                    errors.append('voice_anchor_unverified')
            if basis == 'offscreen' and not (line['reference_offscreen'] or line['voice_only']):
                errors.append('unexpected_offscreen_voice')
            if basis == 'offscreen' and not line['voice_only']:
                errors.append('on_screen_identity_not_established')
            # Compare the heard voices as well as the reported appearance IDs. A
            # silent role swap cannot pass by changing only one of these labels.
            if voice is not None:
                voices.setdefault(voice, set()).add(line['role'])
                roles.setdefault(line['role'], set()).add(voice)
        checks.append(dict(line_id=line['line_id'], expected_person_id=line['person_id'],
            observed_person_id=person, voice_id=voice, passed=not errors, issues=errors))
    for check, line in zip(checks, expected):
        voice = check['voice_id']
        if isinstance(voice, str) and (len(voices.get(voice, ())) > 1 or len(roles.get(line['role'], ())) > 1):
            check['issues'].append('voice_role_inconsistent')
            check['passed'] = False
    contradictions = observed.get('contradictions')
    issues = []
    if not isinstance(contradictions, list) or any(not isinstance(x, str) for x in contradictions):
        issues.append('contradictions_missing_or_invalid')
    elif contradictions:
        issues.append('observer_reported_contradiction')
    return dict(policy=POLICY, passed=all(c['passed'] for c in checks) and not issues,
                complete=True, checks=checks, issues=issues)


def identity_media(media, folder):
    """Retain the original sound or picture packets, removing the other modality."""
    folder.mkdir(parents=True, exist_ok=True)
    audio_only = folder/'sound_on_black.mp4'
    picture_only = folder/'picture_muted.mp4'
    receipt = folder/'identity_media.json'
    source_sha = sha256_file(media)
    outputs = (audio_only, picture_only)
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved != dict(source_sha256=source_sha, outputs={p.name:sha256_file(p) for p in outputs}):
            raise LibraryStopped('dialogue_identity_media_binding_changed')
        return outputs
    inputs = ['-i', str(media)]
    commands = [
        ['-f','lavfi','-i','color=c=black:s=160x90:r=6',*inputs,
         '-map','0:v:0','-map','1:a:0','-c:v','libx264','-threads','1','-c:a','copy'],
        [*inputs,'-f','lavfi','-i','anullsrc=r=48000:cl=mono',
         '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac'],
    ]
    for path, command, stream in zip(outputs, commands, ('a:0','v:0')):
        part = path.with_suffix('.part.mp4')
        subprocess.run(['ffmpeg','-v','error','-nostdin','-y',*command,'-shortest',str(part)],
                       check=True,capture_output=True,timeout=90)
        def packet_hash(file):
            return subprocess.check_output(['ffmpeg','-v','error','-nostdin','-i',str(file),
                '-map','0:'+stream,'-c','copy','-f','hash','-'],timeout=30)
        if packet_hash(media)!=packet_hash(part):
            raise LibraryStopped('dialogue_identity_proxy_changed_source_packets:'+stream)
        part.replace(path)
    if sha256_file(media) != source_sha:
        raise LibraryStopped('dialogue_identity_media_changed')
    write_json(receipt,dict(source_sha256=source_sha,outputs={p.name:sha256_file(p) for p in outputs}))
    return outputs


def combine_identity_observations(sound, picture, catalog, expected):
    """Link heard voice groups to independently observed on-screen utterances."""
    wanted = {line['line_id'] for line in expected}
    people = {person['person_id'] for person in catalog}

    def by_line(value):
        rows = value.get('observations') if isinstance(value,dict) else None
        if not isinstance(rows,list) or len(rows)!=len(wanted) or any(
                not isinstance(row,dict) or not isinstance(row.get('line_id'),str) for row in rows):
            raise LibraryStopped('dialogue_identity_missing_modality_observations')
        mapping = {row['line_id']:row for row in rows}
        if set(mapping)!=wanted:
            raise LibraryStopped('dialogue_identity_incomplete_modality_observations')
        return mapping

    heard, seen = by_line(sound), by_line(picture)
    anchors = {}
    observations = []
    for line in expected:
        key = line['line_id']
        voice, vision = heard[key], seen[key]
        voice_id = voice.get('voice_id')
        visible = vision.get('visible_speaker_ids')
        visibility = vision.get('visibility')
        if (not isinstance(visible,list) or any(not isinstance(p,str) or p not in people for p in visible)
                or visibility not in {'speaker','none','unclear'}
                or (visibility=='speaker')!=bool(visible)
                or not isinstance(vision.get('evidence'),str) or not vision['evidence'].strip()
                or not isinstance(voice.get('evidence'),str) or not voice['evidence'].strip()
                or (voice_id is not None and (not isinstance(voice_id,str) or not voice_id.strip()))):
            raise LibraryStopped('dialogue_identity_invalid_modality_observation')
        for field in ('opening_visible_actor_ids','opening_concealed_mouth_ids','opening_stylized_actor_ids'):
            if field in vision and (not isinstance(vision[field],list) or
                    any(not isinstance(p,str) or p not in people for p in vision[field])):
                raise LibraryStopped('dialogue_identity_invalid_opening_observation')
        person = visible[0] if len(visible)==1 and voice_id is not None else None
        basis = 'visible_speech' if person else 'offscreen' if visibility=='none' and voice_id else 'unclear'
        if person:
            anchors.setdefault(voice_id,{}).setdefault(person,key)
        observations.append(dict(line_id=key,heard_text=voice.get('heard_text'),voice_id=voice_id,
            speaker_id=person,visible_speaker_ids=visible,basis=basis,anchor_line_id=None,
            opening_visible_actor_ids=vision.get('opening_visible_actor_ids',[]),
            opening_concealed_mouth_ids=vision.get('opening_concealed_mouth_ids',[]),
            opening_stylized_actor_ids=vision.get('opening_stylized_actor_ids',[]),
            evidence=voice['evidence']+' / '+vision['evidence']))
    first_voices=set()
    for row in observations:
        voice=row['voice_id'];opening=row['opening_visible_actor_ids'];covered=row['opening_concealed_mouth_ids']
        stylized=row['opening_stylized_actor_ids']
        if (voice is not None and voice not in first_voices and row['basis']=='offscreen' and
                len(opening)==1 and opening[0] in covered+stylized and not row['visible_speaker_ids']):
            person=opening[0]
            row.update(speaker_id=person,basis='occluded_intro' if person in covered else 'stylized_intro')
            anchors.setdefault(voice,{})[person]=row['line_id']
        first_voices.add(voice)
    for row in observations:
        choices = anchors.get(row['voice_id'],{})
        if row['basis']=='offscreen' and len(choices)==1:
            person, anchor = next(iter(choices.items()))
            row.update(speaker_id=person,anchor_line_id=anchor,basis='established_voice')
    return dict(observations=observations,contradictions=[],limitations=[])


def group_pairwise_voices(value, clip_to_line):
    """Require comparisons that agree on one partition of the heard voices."""
    clips = value.get('clips') if isinstance(value,dict) else None
    pairs = value.get('pairs') if isinstance(value,dict) else None
    if (not isinstance(clips,list) or len(clips)!=len(clip_to_line)
            or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in clips)
            or {r['id'] for r in clips}!=set(clip_to_line) or not isinstance(pairs,list)):
        raise LibraryStopped('dialogue_identity_invalid_audio_comparisons')
    wanted = {frozenset(pair) for pair in combinations(clip_to_line,2)}
    observations = {}
    groups = {key:{key} for key in clip_to_line}
    for pair in pairs:
        if (not isinstance(pair,dict) or any(not isinstance(pair.get(k),str) for k in ('a','b','evidence'))
                or not pair['evidence'].strip() or pair.get('verdict') not in ('same','different','uncertain')):
            raise LibraryStopped('dialogue_identity_invalid_audio_comparison')
        a,b = pair['a'],pair['b']
        key = frozenset((a,b))
        if key not in wanted or key in observations:
            raise LibraryStopped('dialogue_identity_duplicate_or_unknown_audio_comparison')
        observations[key] = pair
        if pair['verdict']=='same':
            combined = groups[a] | groups[b]
            for clip_id in combined:
                groups[clip_id] = combined
    if set(observations)!=wanted:
        raise LibraryStopped('dialogue_identity_incomplete_audio_comparisons')
    if any(groups[p['a']]==groups[p['b']] and p['verdict']=='different' for p in pairs):
        raise LibraryStopped('dialogue_identity_inconsistent_audio_comparisons')
    unresolved = set()
    for pair in pairs:
        a,b = pair['a'],pair['b']
        if (groups[a]!=groups[b] and pair['verdict']=='uncertain'
                and not any(observations[frozenset((x,y))]['verdict']=='different'
                            for x in groups[a] for y in groups[b])):
            unresolved.update(groups[a] | groups[b])
    return dict(observations=[dict(line_id=clip_to_line[r['id']],heard_text=r.get('heard_text'),
        voice_id=None if r['id'] in unresolved else min(groups[r['id']]),
        evidence=' / '.join(p['evidence'] for p in pairs if r['id'] in (p['a'],p['b'])) or 'Single utterance heard')
        for r in clips])


def labelled_audio_batch(media, folder, clips, *, gap_s=.4, label_lead_s=0):
    """Give each utterance an unambiguous neutral label and a silence boundary.

    The model sees only arbitrary IDs, never expected text, cast or voice IDs.
    Cutting decoded PCM avoids AAC seek boundaries leaking adjacent speakers.
    The receipt records every exact source-sample interval and output interval.
    """
    folder.mkdir(parents=True, exist_ok=True)
    policy='neutral_labelled_pcm_segments_v1' if gap_s==.4 and label_lead_s==0 else 'neutral_labelled_pcm_segments_v2'
    receipt=folder/'audio_mapping.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        rate=saved['sample_rate']
        expected=[(r['clip_id'],round(r['start_s']*rate),round(r['end_s']*rate)) for r in clips]
        actual=[(r['clip_id'],r['source_start_sample'],r['source_end_sample']) for r in saved['clips']]
        video=folder/'labelled_audio.mp4'
        if (saved['policy']!=policy or saved.get('gap_s',.4)!=gap_s or saved.get('label_lead_s',0)!=label_lead_s or expected!=actual
                or saved['source_sha256']!=sha256_file(media) or saved['output_sha256']!=sha256_file(video)
                or saved['pcm_sha256']!=sha256_file(folder/'labelled_audio.wav')):
            raise LibraryStopped('dialogue_identity_labelled_audio_binding_changed')
        return video,[{k:r[k] for k in ('clip_id','start_s','end_s')} for r in saved['clips']]
    decoded = folder/'source_audio.wav'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-i',str(media),
                    '-vn','-c:a','pcm_s16le',str(decoded)],check=True,capture_output=True,timeout=90)
    output_audio = folder/'labelled_audio.wav'
    mapping, cursor = [], 0
    with wave.open(str(decoded),'rb') as source, wave.open(str(output_audio),'wb') as target:
        target.setparams(source.getparams())
        rate = source.getframerate()
        frame_bytes = source.getnchannels()*source.getsampwidth()
        gap = round(rate*gap_s)
        for clip in clips:
            key = clip['clip_id']
            if not re.fullmatch(r's[0-9]+',key):
                raise LibraryStopped('dialogue_identity_invalid_neutral_clip_id')
            start, end = [round(clip[k]*rate) for k in ('start_s','end_s')]
            if not 0 <= start < end <= source.getnframes():
                raise LibraryStopped('dialogue_identity_audio_interval_out_of_bounds')
            target.writeframes(b'\0'*gap*frame_bytes)
            cursor += gap
            source.setpos(start)
            target.writeframes(source.readframes(end-start))
            mapping.append(dict(clip_id=key,start_s=cursor/rate,end_s=(cursor+end-start)/rate,
                                source_start_sample=start,source_end_sample=end))
            cursor += end-start
        target.writeframes(b'\0'*gap*frame_bytes)
        cursor += gap
    if label_lead_s:
        filters = ','.join("drawtext=text='%s':fontcolor=white:fontsize=64:x=(w-tw)/2:y=(h-th)/2:enable='gte(t,%.9f)*lt(t,%.9f)'" %
            (row['clip_id'],max(0,row['start_s']-min(label_lead_s,gap_s)),row['end_s']) for row in mapping)
    else:
        filters = ','.join("drawtext=text='%s':fontcolor=white:fontsize=64:x=(w-tw)/2:y=(h-th)/2:enable='between(t,%.9f,%.9f)'" %
            (row['clip_id'],row['start_s'],row['end_s']) for row in mapping)
    video = folder/'labelled_audio.mp4'
    subprocess.run(['ffmpeg','-v','error','-nostdin','-n','-f','lavfi','-i','color=c=black:s=640x360:r=6',
        '-i',str(output_audio),'-vf',filters,'-map','0:v','-map','1:a','-t',str(cursor/rate),
        '-c:v','libx264','-threads','1','-c:a','aac','-b:a','192k',str(video)],
        check=True,capture_output=True,timeout=90)
    write_json(folder/'audio_mapping.json',dict(policy=policy,gap_s=gap_s,label_lead_s=label_lead_s,
        source_sha256=sha256_file(media),sample_rate=rate,clips=mapping,
        output_sha256=sha256_file(video),pcm_sha256=sha256_file(output_audio)))
    decoded.unlink()
    return video, [{k:row[k] for k in ('clip_id','start_s','end_s')} for row in mapping]


def _compare_voice_pair(media, folder, by_id, pair, reviewer):
    prefix = (
        '只有两个音频片段，先s1、后s2，中间有静音。分别听写第一段和第二段。'
        '只凭人声的音色、共振、发音习惯，判断它们是不是同一个发声者。'
        '音高、语气、台词不同本身不能判成不同人。无法听清、太短或不能确定时用uncertain。'
        '不知道角色、配音参数或期望分组，不能按台词含义、性别印象或影视知识猜谁在说话。'
        '只输出JSON {"first_text":"第一段听到的台词","second_text":"第二段听到的台词",'
        '"verdict":"same|different|uncertain","evidence":"实际音色听感依据"}。\n')

    a,b=pair
    batch_folder=folder/f'sound_pair_{a}_{b}'
    selected=[dict(by_id[key],clip_id=f's{i}') for i,key in enumerate(pair,1)]
    batch_media, selected = labelled_audio_batch(media,batch_folder/'media',selected)
    received = reviewer(batch_media,batch_folder,prompt=prefix+json.dumps(dict(clips=selected)))
    if received.get('http_status')!=200 or received.get('parse_error'):
        raise LibraryStopped('dialogue_identity_audio_pair_invalid_received')
    value = received.get('parsed')
    if (not isinstance(value,dict) or value.get('verdict') not in {'same','different','uncertain'}
            or any(not isinstance(value.get(k),str) for k in ('first_text','second_text','evidence'))
            or not value['evidence'].strip()
            or (value['verdict']!='uncertain' and any(not value[k].strip() for k in ('first_text','second_text')))):
        raise LibraryStopped('dialogue_identity_audio_pair_incomplete')
    if value['verdict']=='uncertain' and (not value['first_text'].strip() or not value['second_text'].strip()):
        repair_folder=batch_folder/'boundary_repair'
        raw_clips=[dict(by_id[key],clip_id=f's{i}') for i,key in enumerate(pair,1)]
        alternate,mapping=labelled_audio_batch(media,repair_folder/'media',raw_clips,gap_s=2.0,label_lead_s=2.0)
        repaired=reviewer(alternate,repair_folder,prompt=prefix+
            '两段样本之间有2秒静音，编号在对应声音开始前已显示。必须按给定时间区间分别听写，'
            '即使前后同声也不能合并成first_text或把第二段漏掉。不能确认仍返回uncertain。\n'+
            json.dumps(dict(clips=mapping)))
        candidate=repaired.get('parsed')
        if (repaired.get('http_status')!=200 or repaired.get('parse_error') or not isinstance(candidate,dict)
                or candidate.get('verdict') not in {'same','different','uncertain'}
                or any(not isinstance(candidate.get(k),str) for k in ('first_text','second_text','evidence'))
                or not candidate['evidence'].strip()
                or (candidate['verdict']!='uncertain' and any(not candidate[k].strip() for k in ('first_text','second_text')))):
            raise LibraryStopped('dialogue_identity_audio_boundary_repair_invalid_received')
        candidate=dict(candidate,boundary_repair=dict(policy='explicit_audio_boundaries_v2',original=value))
        value=candidate
    result=dict(a=a,b=b,**value)
    write_json(batch_folder/'comparison.json',result)
    return result



def review_voice_pairs(media, folder, clips, clip_to_line, reviewer, *, max_workers=3):
    """Discover voice groups from two-utterance comparisons with heard exemplars.

    Every assigned utterance is directly compared with its group's exemplar.
    There is no expected speaker count or cast mapping in this observation.
    """
    from concurrent.futures import ThreadPoolExecutor
    by_id = {r['clip_id']:r for r in clips}
    def compare(pair):
        return _compare_voice_pair(media,folder,by_id,pair,reviewer)

    remaining=list(clip_to_line)
    groups,transcripts,comparisons={},{},[]
    while remaining:
        seed,*candidates=remaining
        groups[seed]=seed
        pairs=[(seed,key) for key in candidates]
        if len(clip_to_line)==1:pairs=[(seed,seed)]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            answers=list(pool.map(compare,pairs))
        remaining=[]
        for answer in answers:
            a,b=answer['a'],answer['b']
            comparisons.append(answer)
            transcripts.setdefault(a,answer['first_text'])
            transcripts.setdefault(b,answer['second_text'])
            if answer['verdict']=='same':groups[b]=seed
            elif b!=seed:remaining.append(b)
    unresolved=set()
    for pair in comparisons:
        if pair['verdict']=='uncertain':
            unresolved.update((groups[pair['a']],groups[pair['b']]))
    observed=dict(observations=[dict(line_id=clip_to_line[key],heard_text=transcripts.get(key),
        voice_id=None if groups[key] in unresolved else groups[key],
        evidence=' / '.join(p['evidence'] for p in comparisons if key in (p['a'],p['b'])))
        for key in clip_to_line])
    write_json(folder/'audio_pair_observations.json',dict(policy='blind_exemplar_pairs_v1',
        comparisons=comparisons,groups=groups,observed=observed))
    return observed


def review_voice_reference_pairs(media,folder,clips,clip_to_line,reference_line_ids,reviewer,*,max_workers=3):
    """Compare two actual clips at a time; never multiplex target identities.

    References and targets carry only neutral IDs. Every target is checked
    against every reference, and ambiguous/contradictory matches stay unknown.
    """
    from concurrent.futures import ThreadPoolExecutor
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    by_line={line:key for key,line in clip_to_line.items()};by_id={r['clip_id']:r for r in clips}
    if not reference_line_ids or len(reference_line_ids)!=len(set(reference_line_ids)) or any(k not in by_line for k in reference_line_ids):
        raise LibraryStopped('voice_reference_ids_invalid')
    refs=sorted([by_line[k] for k in reference_line_ids],key=lambda k:-(by_id[k]['end_s']-by_id[k]['start_s']))
    ref_pairs=list(combinations(refs,2)) or [(refs[0],refs[0])]
    pairs=ref_pairs+[(ref,key) for key in clip_to_line if key not in refs for ref in refs]
    answers={}
    for offset in range(0,len(pairs),max_workers):
        batch=pairs[offset:offset+max_workers]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            rows=list(pool.map(lambda pair:_compare_voice_pair(media,folder,by_id,pair,reviewer),batch))
        answers.update({(r['a'],r['b']):r for r in rows})
    groups={key:key for key in refs};uncertain=set();texts={}
    def find(key):
        while groups[key]!=key:key=groups[key]
        return key
    for pair in ref_pairs:
        row=answers[pair];a,b=pair
        texts.setdefault(a,row['first_text']);texts.setdefault(b,row['second_text'])
        if row['verdict']=='same':groups[find(b)]=find(a)
        elif row['verdict']=='uncertain':uncertain.update((a,b))
    if any(answers[pair]['verdict']=='different' and find(pair[0])==find(pair[1]) for pair in ref_pairs):
        raise LibraryStopped('voice_reference_partition_contradiction')
    bad={find(key) for key in uncertain};observations=[]
    for key,line in clip_to_line.items():
        if key in refs:
            group=find(key) if find(key) not in bad and texts.get(key,'').strip() else None
            text=texts.get(key,'');evidence=' / '.join(answers[p]['evidence'] for p in ref_pairs if key in p)
            readings=[text]
        else:
            rows=[answers[ref,key] for ref in refs]
            same={find(ref) for ref,row in zip(refs,rows) if row['verdict']=='same'}
            different={find(ref) for ref,row in zip(refs,rows) if row['verdict']=='different'}
            group=next(iter(same)) if (len(same)==1 and not same.intersection(bad|different)
                and all(r['verdict']!='uncertain' for r in rows)) else None
            readings=[r['second_text'] for r in rows]
            text=next((r['second_text'] for r in rows if r['verdict']=='same' and r['second_text'].strip()),
                      next((t for t in readings if t.strip()),''))
            evidence=' / '.join(r['evidence'] for r in rows)
        observations.append(dict(line_id=line,heard_text=text,voice_id=group,evidence=evidence,
            transcript_observations=readings))
    result=dict(observations=observations)
    write_json(folder/'voice_reference_pair_observations.json',dict(policy='blind_reference_pairs_v2',
        reference_line_ids=reference_line_ids,comparisons=list(answers.values()),groups=groups,observed=result))
    return result


def review_voice_references(media, folder, clips, clip_to_line, reference_line_ids, reviewer):
    """Blind enrol/verify comparison: neutral references, no expected target labels."""
    from .contracts import parse_model_json
    by_line={line:key for key,line in clip_to_line.items()};by_id={r['clip_id']:r for r in clips}
    refs=[by_line[line] for line in reference_line_ids]
    if not refs or len(refs)!=len(set(refs)):raise LibraryStopped('voice_reference_ids_invalid')
    groups={key:key for key in refs};uncertain=set();comparisons=[];heard={}
    def find(key):
        while groups[key]!=key:key=groups[key]
        return key
    targets=list(clip_to_line)
    for offset in range(0,len(targets),4):
        target_ids=targets[offset:offset+4]
        source_ids=refs+target_ids
        selected=[dict(by_id[key],clip_id=f's{i+1}') for i,key in enumerate(source_ids)]
        batch=folder/f'voice_refs_{offset//4+1}'
        video,mapping=labelled_audio_batch(media,batch/'media',selected)
        ref_labels={f's{i+1}':key for i,key in enumerate(refs)}
        target_labels={f's{i+1+len(refs)}':key for i,key in enumerate(target_ids)}
        prompt=('声音样本已用中性编号逐段显示，段间有静音；没有人物、故事角色、预期归属或台词答案。'
            'reference_clips是声音参照，target_clips是待比对的实际发声。先听每段，不按台词含义猜人。'
            '根据音色、共振、发音习惯比较是否同一个发声者，音高或情绪变化本身不能判换人。'
            '对每个target逐一比较所有reference，same/different/uncertain；太短、听不清或不能确定必须uncertain。'
            'heard_text听写目标实际声音；没有依据不能为了完整分组硬填same。'+
            ('还要比较所有不同reference两两是否同声，不能预设每个reference是不同人。' if offset==0 else '')+
            '返回JSON {"targets":[{"clip_id":"目标编号","heard_text":"实际声音",'
            '"matches":[{"reference_clip_id":"参照编号","verdict":"same|different|uncertain","evidence":"声学依据"}]}],'
            '"reference_pairs":[{"a":"参照编号","b":"参照编号","verdict":"different","evidence":"声学依据"}]}。\n'+
            json.dumps(dict(reference_clips=list(ref_labels),target_clips=list(target_labels),clips=mapping),ensure_ascii=False))
        response=reviewer(video,batch/'review',prompt=prompt)
        if response.get('http_status')!=200:raise LibraryStopped('voice_reference_review_not_received')
        value=response.get('parsed') or parse_model_json(response['reply'])
        rows=value.get('targets')
        if (not isinstance(rows,list) or len(rows)!=len(target_labels) or
                {r.get('clip_id') for r in rows}!=set(target_labels)):
            raise LibraryStopped('voice_reference_targets_incomplete')
        if offset==0:
            pairs=value.get('reference_pairs')
            wanted={frozenset(pair) for pair in combinations(ref_labels,2)};seen=set()
            if not isinstance(pairs,list):raise LibraryStopped('voice_reference_pairs_incomplete')
            for pair in pairs:
                key=frozenset((pair.get('a'),pair.get('b')))
                if key not in wanted or key in seen or pair.get('verdict') not in {'same','different','uncertain'} or not pair.get('evidence'):
                    raise LibraryStopped('voice_reference_pairs_invalid')
                seen.add(key)
                a,b=ref_labels[pair['a']],ref_labels[pair['b']]
                if pair['verdict']=='same':groups[find(b)]=find(a)
                elif pair['verdict']=='uncertain':uncertain.update((a,b))
                comparisons.append(dict(a=a,b=b,**{k:pair[k] for k in ('verdict','evidence')}))
            if seen!=wanted:raise LibraryStopped('voice_reference_pairs_incomplete')
            if any(p['verdict']=='different' and find(p['a'])==find(p['b']) for p in comparisons):
                raise LibraryStopped('voice_reference_partition_contradiction')
        for row in rows:
            matches=row.get('matches')
            if (not isinstance(matches,list) or len(matches)!=len(ref_labels) or
                    {m.get('reference_clip_id') for m in matches}!=set(ref_labels) or
                    any(m.get('verdict') not in {'same','different','uncertain'} or not m.get('evidence') for m in matches)
                    or not isinstance(row.get('heard_text'),str)):
                raise LibraryStopped('voice_reference_matches_incomplete')
            same={find(ref_labels[m['reference_clip_id']]) for m in matches if m['verdict']=='same'}
            bad={find(key) for key in uncertain}
            group=next(iter(same)) if len(same)==1 and not same.intersection(bad) and row['heard_text'].strip() else None
            heard[target_labels[row['clip_id']]]=dict(heard_text=row['heard_text'],voice_id=group,
                evidence=' / '.join(m['evidence'] for m in matches))
        write_json(batch/'comparison.json',value)
    result=dict(observations=[dict(line_id=clip_to_line[key],**heard[key]) for key in clip_to_line])
    write_json(folder/'voice_reference_observations.json',dict(reference_line_ids=reference_line_ids,
        comparisons=comparisons,groups=groups,observed=result))
    return result


def review_separate_identities(media, folder, catalog, expected, reviewer, reference_line_ids=None):
    audio_only, picture_only = identity_media(media, folder)
    intervals = [{k:row[k] for k in ('line_id','start_s','end_s')} for row in expected]
    clip_to_line = {f's{i}':row['line_id'] for i,row in enumerate(intervals,1)}
    clips = [dict(clip_id=key,start_s=row['start_s'],end_s=row['end_s'])
             for key,row in zip(clip_to_line,intervals)]
    picture_prompt = (
        '这是静音画面。逐句时间段仅观察确实有讲话口部动作的人，不推测谁在画外发声。'
        '用人物外观区分个体。字幕、画面中心的人、物品持有者和固定牙齿都不是发声动作。'
        '只有可见讲话动作才填speaker并列出观察到的人物ID；只有道具、确实静默或无人出镜填none；'
        '已明确看到头部，但真实嘴被面具或固定非生理造型遮住时，visible_speaker_ids=[]、visibility=none；'
        'none只表示没有观察到可见嘴动，不断言是否在发声。人物身份看不清或暴露的嘴部活动无法判断才填unclear；'
        '逐句另记录开头0.8秒（更短就取全句）的opening_visible_actor_ids：完整头部或身体清楚可辨的人；'
        '与已建立动画人物外观明确一致的角色头像/气泡可登记为该人物的图像呈现；商品上不确定的脸、单独手部不算。'
        'opening_concealed_mouth_ids只列其中真实嘴被面具、头盔或固定非生理面部造型遮住的人；'
        '普通清楚可见的闭嘴、微笑不算遮嘴。opening_stylized_actor_ids记录其中清楚呈现的动画/绘画人物形象，真人不能列入；'
        '这是表现媒介记录，不是嘴动证据。三个字段均只描述画面，不猜声音属于谁。'
        '看不清、无法判断、讲话者不在人物表填unclear。'
        '仅输出JSON {"observations":[{"line_id":"原ID","visibility":"speaker|none|unclear",'
        '"visible_speaker_ids":[],"opening_visible_actor_ids":[],"opening_concealed_mouth_ids":[],"opening_stylized_actor_ids":[],"evidence":"实际画面动作"}]}。每个ID一次。\n'
        +json.dumps(dict(people=catalog,intervals=intervals),ensure_ascii=False)
    )
    heard = (review_voice_reference_pairs(audio_only,folder/'reference_pairs',clips,clip_to_line,reference_line_ids,reviewer)
             if reference_line_ids else review_voice_pairs(audio_only,folder,clips,clip_to_line,reviewer))
    picture = reviewer(picture_only,folder/'picture',prompt=picture_prompt)
    if picture.get('http_status')!=200 or picture.get('parse_error'):
        raise LibraryStopped('dialogue_identity_modality_observation_invalid_received')
    seen = picture.get('parsed')
    if isinstance(seen,dict) and isinstance(seen.get('observations'),list):
        unclear = {r.get('line_id') for r in seen['observations']
                   if isinstance(r,dict) and r.get('visibility')=='unclear'}
        if unclear and not reference_line_ids:
            followup = reviewer(media,folder/'picture_timing',prompt=(
                '复核实际视频中的发声动作。音轨仅供定位发声起止，禁止据音色、台词或字幕推断身份。'
                '逐段只报确实可见的发声人。如果嘴被遮住或动画口型简化，'
                '可用与发声节拍同步、清楚区别于旁听的头部或身体动作确认。静止牙齿或居中都不算。'
                '如果开头确实看见发声，随后切到静默听者，报告前者。证据写实际动作和可见时间。'
                '只输出JSON {"observations":[{"line_id":"原ID","visibility":"speaker|none|unclear",'
                '"visible_speaker_ids":[],"evidence":"实际动作及时间"}]}。\n'
                +json.dumps(dict(people=catalog,intervals=[r for r in intervals if r['line_id'] in unclear]),
                            ensure_ascii=False)
            ))
            if followup.get('http_status')!=200 or followup.get('parse_error'):
                raise LibraryStopped('dialogue_identity_picture_timing_observation_invalid_received')
            followup_value = followup.get('parsed')
            updates = followup_value.get('observations') if isinstance(followup_value,dict) else None
            if (not isinstance(updates,list) or len(updates)!=len(unclear)
                    or any(not isinstance(r,dict) or not isinstance(r.get('line_id'),str) for r in updates)
                    or {r['line_id'] for r in updates}!=unclear):
                raise LibraryStopped('dialogue_identity_picture_timing_observation_incomplete')
            updates_by_line = {r['line_id']:r for r in updates}
            seen = dict(observations=[updates_by_line.get(r['line_id'],r) for r in seen['observations']])
    modalities = dict(sound=heard,picture=seen)
    write_json(folder/'modality_observations.json',modalities)
    return combine_identity_observations(**modalities,catalog=catalog,expected=expected)


def review_identities(media, folder, context, reviewer):
    catalog, expected = identity_contract(context)
    if not expected:
        return None
    if context.get('editing_policy') in {'action_dialogue_v1', 'reference_story_v1'}:
        observed = review_separate_identities(Path(media),folder,catalog,expected,reviewer,
            reference_line_ids=context.get('voice_reference_line_ids'))
    else:
        prompt = observation_prompt(catalog, expected)
        received = reviewer(media, folder, prompt=prompt)
        if received.get('http_status') != 200 or received.get('parse_error'):
            raise LibraryStopped('dialogue_identity_observation_invalid_received')
        observed = received.get('parsed')
    result = verify_observations(observed, catalog, expected)
    # The expected mapping is stored AFTER the blind request for audit, never sent
    # as feedback to the observer. Failed observations are not retried to get a pass.
    write_json(folder/'identity_comparison.json', dict(result=result, expected=expected,
                                                     people=catalog, observed=observed))
    return result
