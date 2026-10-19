"""Server-side hot-reference acquisition. Keep paid responses; never retry unknowns.

The old douyin-capture library supplies the acquisition pattern (raw responses,
ranked candidates and atomic CDN downloads). Current Wellbyte creator endpoints
add the missing multi-author participation evidence. Public data only.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from itertools import zip_longest
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from ..library.media import probe_media, select_audio_stream, sha256_file
from ..library.state import LibraryStopped, file_lock, json_sha, write_json


BASE_URL = 'https://api.wellbyte.net'
DEFAULT_MAX_VIDEO_BYTES = 256 * 1024 ** 2
DEFAULT_MIN_FREE_BYTES = 2 * 1024 ** 3
MAX_CAPTURE_ATTEMPTS = 3


class CaptureAPIError(LibraryStopped):
    def __init__(self, status, code):
        self.http_status, self.api_code = status, code
        super().__init__(f'capture_api_failed:http={status}:code={code}')


class CandidateUnavailable(LibraryStopped):
    """A received source explicitly says this individual public item is gone."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _key(home):
    value = os.environ.get('WELLBYTE_API_KEY')
    credential = Path(home) / 'credentials.json'
    if not value and credential.exists():
        value = json.loads(credential.read_text(encoding='utf-8')).get('WELLBYTE_API_KEY')
    env = Path(home) / 'wellbyte.env'
    if not value and env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('WELLBYTE_API_KEY='):
                value = line.split('=', 1)[1].strip().strip('\"\'')
    if not value:
        raise LibraryStopped('server_WELLBYTE_API_KEY_missing')
    return value


class Wellbyte:
    """One journal entry before each billable call, raw bytes before parsing."""

    def __init__(self, home, output):
        self.key = _key(home)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.ledger = self.output / 'capture_calls.json'
        self.calls = json.loads(self.ledger.read_text(encoding='utf-8')) if self.ledger.exists() else []
        if any(c['status'] in {'submitted', 'uncertain'} for c in self.calls):
            raise LibraryStopped('capture_request_outcome_unknown_no_replay')

    def call(self, name, endpoint, params, *, method='GET', retry_failed=False):
        if any(c['status'] in {'submitted', 'uncertain'} for c in self.calls):
            raise LibraryStopped('capture_request_outcome_unknown_no_replay')
        attempts = [c for c in self.calls if c['name'] == name]
        existing = attempts[-1] if attempts else None
        if retry_failed and existing is None:
            raise LibraryStopped('capture_failed_retry_requires_existing_receipt')
        if existing:
            if existing['endpoint'] != endpoint or existing['params'] != params or existing['method'] != method:
                raise LibraryStopped('capture_received_request_changed')
            path = Path(existing['response_path'])
            if sha256_file(path) != existing['response_sha256']:
                raise LibraryStopped('capture_previous_request_not_reusable')
            if existing['status'] == 'failed' and not (
                    retry_failed and existing.get('http_status') == 502 and
                    len(attempts) < MAX_CAPTURE_ATTEMPTS):
                raise CaptureAPIError(existing.get('http_status'), existing.get('api_code'))
            if existing['status'] not in {'received', 'failed'}:
                raise LibraryStopped('capture_previous_request_not_reusable')
            if existing['status'] == 'received':
                return json.loads(path.read_bytes())
        row = dict(name=name, endpoint=endpoint, params=params, method=method,
                   status='submitted', submitted_at=_now())
        if existing:
            # Explicit operator recovery only; preserve the failed receipt and bytes.
            row.update(attempt=len(attempts) + 1,
                       retry_of_response_sha256=existing['response_sha256'])
        self.calls.append(row)
        write_json(self.ledger, self.calls)
        data = json.dumps(params).encode() if method == 'POST' else None
        url = BASE_URL + endpoint
        if method == 'GET':
            url += '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=data, method=method,
            headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                     'User-Agent': 'Mozilla/5.0'})
        try:
            try:
                response = urllib.request.urlopen(request, timeout=90)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw, status = response.read(), response.status
                response_headers = {key: response.headers.get(key) for key in
                    ('X-Request-Id', 'CF-Ray', 'Server', 'Content-Type') if response.headers.get(key)}
        except Exception as error:
            row.update(status='uncertain', error_type=type(error).__name__,
                       error_detail=str(error).replace(self.key, '[REDACTED]'),
                       error_reason_type=type(getattr(error, 'reason', None)).__name__,
                       finished_at=_now())
            write_json(self.ledger, self.calls)
            raise LibraryStopped('capture_network_result_unknown_no_replay') from None
        path = self.output / (name + (f'.attempt-{len(attempts) + 1}' if existing else '') + '.response.json')
        path.write_bytes(raw)
        row.update(http_status=status, response_headers=response_headers, response_path=str(path),
                   response_sha256=sha256_file(path), finished_at=_now())
        try:
            payload = json.loads(raw)
        except ValueError:
            row['status'] = 'failed' if status != 200 else 'uncertain'
            write_json(self.ledger, self.calls)
            raise LibraryStopped(f'capture_non_json_response:http={status}:no_replay') from None
        row.update(status='received' if status == 200 and payload.get('code') == 0 else 'failed',
                   api_code=payload.get('code'), meta=payload.get('meta', {}))
        write_json(self.ledger, self.calls)
        if row['status'] != 'received':
            raise CaptureAPIError(status, payload.get('code'))
        return payload


def video_record(payload):
    """Normalize only fields present in the received detail; never infer authors."""
    detail = payload.get('data', {}).get('aweme_detail') or {}
    video, author, music = (detail.get(k) or {} for k in ('video', 'author', 'music'))
    urls = []
    for kind in ('play_addr_h264', 'play_addr', 'download_addr'):
        urls.extend((video.get(kind) or {}).get('url_list') or [])
    return dict(id=str(detail.get('aweme_id') or ''),
        author_id=str(author.get('uid') or ''), author=author.get('nickname'),
        title=detail.get('desc') or '', published_at=detail.get('create_time') or 0,
        duration_s=(video.get('duration') or 0) / 1000,
        is_video=not detail.get('images') and detail.get('aweme_type') not in {68, 101, 150},
        likes=(detail.get('statistics') or {}).get('digg_count') or 0,
        music_id=str(music.get('id_str') or music.get('id') or ''),
        challenges=[dict(id=c.get('cid'), title=c.get('cha_name')) for c in detail.get('cha_list') or []],
        urls=list(dict.fromkeys(u for u in urls if u.startswith('https://'))))


def _download(record, root, *, max_video_bytes=DEFAULT_MAX_VIDEO_BYTES,
              min_free_bytes=DEFAULT_MIN_FREE_BYTES):
    if type(max_video_bytes) is not int or max_video_bytes < 1 or type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError('capture_invalid_download_limits')
    target = root / record['id'] / 'video.mp4'
    receipt = target.with_suffix('.receipt.json')
    if receipt.exists():
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if saved['id'] != record['id'] or sha256_file(target) != saved['sha256']:
            raise LibraryStopped('capture_cached_video_changed')
        return saved
    target.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for url in record['urls']:
        part = None
        try:
            if shutil.disk_usage(target.parent).free <= min_free_bytes:
                raise LibraryStopped('capture_download_disk_space_insufficient')
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.douyin.com/'})
            with urllib.request.urlopen(request, timeout=90) as response:
                length = response.headers.get('Content-Length')
                length = int(length) if length is not None else None
                if length is not None and not 0 <= length <= max_video_bytes:
                    raise ValueError('capture_video_exceeds_byte_limit')
                if shutil.disk_usage(target.parent).free < min_free_bytes + (length if length is not None else max_video_bytes):
                    raise LibraryStopped('capture_download_disk_space_insufficient')
                received = 0
                with tempfile.NamedTemporaryFile(mode='wb', prefix='video-', suffix='.mp4.part',
                                                  dir=target.parent, delete=False) as handle:
                    part = Path(handle.name)
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if received > max_video_bytes:
                            raise ValueError('capture_video_exceeds_byte_limit')
                        if shutil.disk_usage(target.parent).free < min_free_bytes + len(chunk):
                            raise LibraryStopped('capture_download_disk_space_insufficient')
                        handle.write(chunk)
                if length is not None and received != length:
                    raise ValueError('capture_video_content_length_mismatch')
            measured = probe_media(part)
            part.replace(target)
            saved = dict(id=record['id'], path=str(target), sha256=sha256_file(target),
                         duration_s=measured['duration_s'], downloaded_at=_now())
            write_json(receipt, saved)
            return saved
        except LibraryStopped:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            errors.append(str(error) if str(error).startswith('capture_video_') else type(error).__name__)
        finally:
            if part is not None:
                part.unlink(missing_ok=True)
    raise LibraryStopped('capture_CDN_download_failed:' + record['id'] + ':' + ','.join(errors))


def _library_candidates(visual_library, title):
    from ..library.visual_library import VisualLibrary
    library = VisualLibrary(visual_library)
    cards = library.search(title, limit=12)
    # A small stratified palette shows the available worlds even on zero hits.
    groups = {}
    for card in library.search('', limit=10000):
        groups.setdefault(card['source_sha256'], []).append(card)
    for group in groups.values():
        cards.extend(group[::max(1, len(group)//3)][:3])
    unique = {c['observation_id']:c for c in cards}
    return [{k:c[k] for k in ('observation_id','source_sha256','source_start_s','source_end_s',
                             'summary','affordances','limitations','uncertainties')} for c in unique.values()]


def _observed_library_candidates(visual_library, review, previous):
    """Recall from received actions/anchors without turning uses into facts."""
    from ..library.visual_library import VisualLibrary
    library = VisualLibrary(visual_library)
    representative = review['representative_id']
    evidence = next(row for row in review['member_evidence'] if row['id'] == representative)
    queries = review['representative_brief']['adaptation_anchors'][:4] + [
        event['visible'] for event in evidence['events'][:8]]
    actions = ' '.join(event['visible'] for member in review['member_evidence']
                       for event in member['events'])
    # Concrete preparation words often have no literal counterpart in another
    # setting. These are recall aliases only; actual AV still decides fitness.
    if any(word in actions for word in ('烤馕', '揉面', '面团', '烹饪', '做饭', '烤饼')):
        queries = ['烹饪', '揉面', '面馆', '顾客'] + queries
    found = {}
    for query in queries:
        for card in library.search(query, limit=3):
            if (card['match_strength'] == 'weak_single_character' or not any(
                    row['kind'] == 'visual_fact' and any(len(term) > 1 for term in row['matched_terms'])
                    for row in card['reasons'])):
                continue
            found.setdefault(card['observation_id'], card)
    old_ids = {row['observation_id'] for row in previous}
    novel = [row for key,row in found.items() if key not in old_ids][:8]
    if not novel:
        return None
    fields = ('observation_id','source_sha256','source_start_s','source_end_s',
              'summary','affordances','limitations','uncertainties')
    return [{k:row[k] for k in fields} for row in novel] + previous[:24-len(novel)]


def _library_recall_admission(visual_library, review, previous, media):
    """Pin one context to this immutable negative, across later capture runs."""
    ledger = Path(review['ledger_path'])
    pins = dict(policy='one_visible_action_library_recall_v1',
        original_ledger_path=str(ledger), original_ledger_sha256=review['ledger_sha256'],
        original_response_sha256=review['response_sha256'],
        source_bindings=[{k:row[k] for k in ('id','sha256')} for row in media],
        max_new_assessments=1)
    if (sha256_file(ledger) != pins['original_ledger_sha256'] or
            sha256_file(ledger.parent/'response.json') != pins['original_response_sha256']):
        raise LibraryStopped('capture_library_recall_original_binding_changed')
    claim = ledger.parent.parent/'library_recall_once_v1.json'
    if claim.exists():
        saved = json.loads(claim.read_text())
        if (any(saved.get(key) != value for key,value in pins.items()) or
                json_sha(saved['candidates']) != saved['candidates_sha256']):
            raise LibraryStopped('capture_library_recall_once_binding_changed')
        # Reuse only this already admitted request, even if newer observations
        # would produce another palette. Its own ledger forbids unknown replay.
        return saved['candidates'], saved
    recalled = _observed_library_candidates(visual_library, review, previous)
    if recalled is None:
        return None, None
    saved = dict(pins, candidates=recalled, candidates_sha256=json_sha(recalled),
        previous_observation_ids=[r['observation_id'] for r in previous],
        recalled_observation_ids=[r['observation_id'] for r in recalled])
    # The caller owns the shared capture lock; this durable claim precedes any
    # new model request and remains after negative, invalid, or unknown results.
    write_json(claim, saved)
    return recalled, saved


def _comparison(media, folder):
    """Keep complete, normal-speed pictures and actual sound in one small proxy."""
    binding = [{k:item[k] for k in ('id', 'sha256')} for item in media]
    manifest = folder / 'comparison_sources.json'
    if manifest.exists():
        if json.loads(manifest.read_text()) != binding:
            raise LibraryStopped('capture_comparison_sources_changed')
    elif any(folder.glob('*.mp4')):
        raise LibraryStopped('capture_comparison_source_binding_missing')
    else:
        write_json(manifest, binding)
    clips, boundaries, offset = [], [], 0
    for item in media:
        if sha256_file(item['path']) != item['sha256']:
            raise LibraryStopped('capture_cached_video_changed')
        source = probe_media(item['path'])
        audio = select_audio_stream(source['streams'])
        clip = folder / (item['id'] + '.mp4')
        if not clip.exists():
            partial = clip.with_suffix('.part.mp4')
            command = ['ffmpeg', '-v', 'error', '-nostdin', '-i', item['path']]
            if audio is None:
                command += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            command += ['-map', '0:v:0', '-map', f'0:{audio}' if audio is not None else '1:a:0',
                '-vf', 'scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2,fps=25',
                '-af', 'apad,aresample=48000', '-t', str(source['duration_s']),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '28',
                '-c:a', 'aac', '-b:a', '64k', '-ar', '48000', '-ac', '2', '-y', str(partial)]
            subprocess.run(command, check=True)
            partial.replace(clip)
        duration = probe_media(clip)['duration_s']
        boundaries.append(dict(id=item['id'], start_s=offset, end_s=offset + duration,
            member_duration_s=source['duration_s'], audio_present=audio is not None,
            audio_evidence='original_audio_reencoded' if audio is not None else 'missing_original_audio_technical_silence'))
        offset += duration
        clips.append(clip)
    proxy = folder / 'comparison.mp4'
    if not proxy.exists():
        listing = folder / 'concat.txt'
        listing.write_text(''.join(f"file '{p.name}'\n" for p in clips), encoding='utf-8')
        partial = proxy.with_suffix('.part.mp4')
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'concat', '-safe', '0',
                        '-i', str(listing), '-c', 'copy', '-y', str(partial)], check=True)
        partial.replace(proxy)
    return proxy, boundaries


def _legacy_template_folder(root, trend_id, library_candidates):
    admission = 'cross_scene_remix_av_v1'
    suffix = '_av_v1_' + json_sha(dict(candidates=library_candidates, admission=admission))[:12]
    return root / ('template_' + str(trend_id) + suffix)


def _template_folder(root, trend_id, library_candidates, media):
    folder = _legacy_template_folder(root, trend_id, library_candidates)
    state = folder / 'audio_review/state.json'
    if state.exists() and json.loads(state.read_text())['status'] in {'submitted', 'uncertain'}:
        raise LibraryStopped('capture_existing_unknown_stops_branch:' + str(state))
    # Every new comparison binds the full source hashes, including for a stable
    # video ID whose bytes changed. Legacy proxies are never trusted as inputs.
    return folder.with_name(folder.name + '_members_' + json_sha(
        [{k:item[k] for k in ('id', 'sha256')} for item in media])[:12])


def _verify_template(home, root, trend, records, media, *, library_candidates=None,
                     correct_missing_limitations=True):
    """One cached actual AV comparison; common music/topic is insufficient."""
    from ..library import server_cli
    from ..library.audio_review import review_audio_video

    folder = _template_folder(root, trend['sentence_id'], library_candidates, media)
    folder.mkdir(exist_ok=True)
    settings = server_cli.settings(home)
    history = server_cli.load_history(settings)
    if any(item['sha256'] in {old['media_sha256'], old['scope']['source_sha256']}
           for item in media for old in (history or {}).get('unknown_inputs', [])):
        raise LibraryStopped('capture_member_historical_unknown_no_replay')
    proxy, boundaries = _comparison(media, folder)
    proxy_sha = sha256_file(proxy)
    if any(proxy_sha in {old['media_sha256'], old['scope']['source_sha256']}
           for old in (history or {}).get('unknown_inputs', [])):
        raise LibraryStopped('capture_comparison_historical_unknown_no_replay')
    key = os.environ.get('DASHSCOPE_API_KEY')
    if not key:
        credential = json.loads((Path(home) / 'credentials.json').read_text(encoding='utf-8'))
        os.environ['DASHSCOPE_API_KEY'] = credential.get('DASHSCOPE_API_KEY') or ''
    try:
        prompt = ('听看这些不同作者的实际完整视频，判断是否多人拍同一种可复刻玩法/段子/叙事结构。'
            '仅相同音乐、宽泛话题或标签不算同款；不要按标题臆测画面。'
            '媒体依次拼接、正常速度、完整画面缩小，原音轨重编码保留；audio_present=false的成员'
            '只有技术静音，原声音缺失，不能把该静音当作原作没有声音的证据。'
            '必须逐成员先描述真实可见动作和实际可听声音，再抽象共同表达；'
            '舞蹈动作、歌词字幕、文字故事、生活蒙太奇不是同一玩法，不能仅凭一句相同歌词混为一谈。'
            'same_template=true至少三位实际作者具有有证据支持的同一可复刻机制；成员只能选本次实际视频。'
            '每个member_evidence含id、shared_mechanism及events数组；每个event含start_s/end_s、'
            'visible、audible、template_function；时间必须为该成员本地秒，而非拼接全局秒，'
            '不能超出member_duration_s。听不清明确记录，不猜歌词/对白。'
            '选一个实际能作为电影素材重新创作的代表；representative_brief含member_id、'
            'core_expression、visual_form、audio_form、audible_words字符串数组、rhythm_role、'
            'adaptation_anchors字符串数组及uncertainties字符串数组。说明对白/歌词/音乐各自作用，'
            '实际可听词与听不清分开，节奏作用引用本地时间；不能只输出泛化主旨。'
            '没有共同玩法输出false，representative_id和representative_brief可为null，不强行通过。\n' +
            json.dumps(dict(trend={k:trend.get(k) for k in ('sentence','sentence_id','hot_score','video_count')},
                members=[{k:r[k] for k in ('id','author_id','title')} for r in records],
                actual_video_timeline=boundaries), ensure_ascii=False))
        if library_candidates is not None:
            prompt += ('\n同时输出library_adaptable:boolean,library_reason:string。'
                '根据以下已收视觉记录判断能否从现有素材创作相同主旨/笑点机制和声画分工的新内容，'
                '这是跨场景重新创作，不是逐物复刻：可以更换人物、时代、场景、对白和物件用途，'
                '不必复述原电影，但不能捏造可见动作。先抽象期待—行动—反差/结果及角色关系，'
                '再判断画面与新对白/字幕能否共同表达；实拍与动画、现代与古风的差别本身不构成拒绝理由。'
                '记录的affordances是条件性用途，不是画面事实；这些只是检索样本，样本缺项不能证明全库没有，'
                '应记录具体待检索缺项。只有笑点不可替换地依赖特定实物文字或行为、且候选没有其视觉基础时才拒绝，'
                '例如物件上的字面双关不能只靠无关角色表情冒充；普通对视、出场、被关注或关系反差'
                '则允许在其他场景中重新组织。不可适配时library_adaptable=false，明确不可替代的缺项，'
                '保留same_template的独立结论，自动考虑其他热点。true的library_reason必须说明可行改编，'
                '不可同时写无法表达或无法改编；不能为通过而虚构素材动作。'
                '\n现有素材候选：' + json.dumps(library_candidates, ensure_ascii=False))
        prompt += ('\n只输出一个JSON对象，不加Markdown，不拆成多对象：'
            '{"same_template":false,"shared_pattern":"","member_ids":[],"representative_id":null,'
            '"member_evidence":[],"representative_brief":null,"limitations":[],"library_adaptable":false,'
            '"library_reason":""}，自行填写实际判断。')
        def validate(value):
            ids = [r['id'] for r in records]
            members = value.get('member_ids')
            if (type(value.get('same_template')) is not bool or not isinstance(members, list) or
                    any(i not in ids for i in members) or len(set(members)) != len(members) or
                    not isinstance(value.get('shared_pattern'), str)):
                raise ValueError('hot_template_invalid_response')
            if value['same_template'] and (len(members) < 3 or value.get('representative_id') not in members):
                raise ValueError('hot_template_requires_three_actual_members')
            evidence = value.get('member_evidence')
            if not isinstance(evidence, list):
                raise ValueError('hot_template_member_evidence_required')
            seen = set()
            durations = {row['id']:row['member_duration_s'] for row in boundaries}
            for row in evidence:
                member = row.get('id')
                if member not in ids or member in seen or not isinstance(row.get('shared_mechanism'), str):
                    raise ValueError('hot_template_member_evidence_invalid')
                seen.add(member)
                if not isinstance(row.get('events'), list) or not row['events']:
                    raise ValueError('hot_template_timed_evidence_required')
                for event in row['events']:
                    start, end = event.get('start_s'), event.get('end_s')
                    if (type(start) not in (int, float) or type(end) not in (int, float) or
                            not 0 <= start < end <= durations[member] + .05 or
                            not all(isinstance(event.get(k), str) and event[k].strip()
                                    for k in ('visible', 'audible', 'template_function'))):
                        raise ValueError('hot_template_member_local_time_or_observation_invalid')
            if value['same_template']:
                brief = value.get('representative_brief')
                if (not set(members).issubset(seen) or not isinstance(brief, dict) or
                        brief.get('member_id') != value['representative_id'] or
                        not all(isinstance(brief.get(k), str) and brief[k].strip() for k in
                            ('core_expression', 'visual_form', 'audio_form', 'rhythm_role')) or
                        not all(isinstance(brief.get(k), list) and all(isinstance(s, str) for s in brief[k])
                            for k in ('audible_words', 'adaptation_anchors', 'uncertainties'))):
                    raise ValueError('hot_template_representative_actual_av_brief_required')
            if library_candidates is not None and (type(value.get('library_adaptable')) is not bool or
                    not isinstance(value.get('library_reason'), str)):
                raise ValueError('hot_template_library_admission_required')
            # A single corrective assessment is eligible only after every
            # substantive field has passed, and only for this absent field.
            if 'limitations' not in value:
                raise ValueError('hot_template_missing_limitations_field')
            if not isinstance(value['limitations'], list):
                raise ValueError('hot_template_invalid_limitations_field')
        review_folder = folder / 'audio_review'
        legacy = _legacy_template_folder(root, trend['sentence_id'], library_candidates)
        old_state, old_request, old_proxy = (legacy/'audio_review/state.json',
            legacy/'audio_review/request.json', legacy/'comparison.mp4')
        # A legacy response is reusable only after independently regenerating
        # the comparison from the source-bound inputs and matching exact bytes
        # and exact prompt. Keep the original request path and ledger intact.
        if (old_state.exists() and old_request.exists() and old_proxy.exists() and
                json.loads(old_state.read_text()).get('status') == 'received' and
                sha256_file(old_proxy) == proxy_sha and
                json.loads(old_request.read_text()).get('prompt') == prompt):
            proxy, review_folder = old_proxy, legacy/'audio_review'
        correction = None
        for attempt in range(2):
            received = review_audio_video(proxy, review_folder, prompt=prompt,
                                          allow_temporary_upload=True)
            state_path = review_folder / 'state.json'
            result = received.get('parsed')
            try:
                if received.get('parse_error') or received.get('http_status') != 200 or not isinstance(result, dict):
                    raise ValueError('hot_template_received_reply_invalid')
                validate(result)
            except (ValueError, TypeError, AttributeError) as error:
                failure = dict(error=str(error), ledger_path=str(state_path),
                    ledger_sha256=sha256_file(state_path), http_status=received.get('http_status'),
                    parse_error=received.get('parse_error'), response_sha256=received.get('response_sha256'),
                    evidence_limit='Known received AV reply rejected; original reply remains unchanged.')
                failure_path = review_folder.parent / 'template_protocol_failed.json'
                if not failure_path.exists():
                    write_json(failure_path, failure)
                else:
                    original_failure = json.loads(failure_path.read_text())
                    pins = ('ledger_path', 'ledger_sha256', 'http_status', 'parse_error', 'response_sha256')
                    if any(original_failure.get(k) != failure[k] for k in pins):
                        raise LibraryStopped('capture_protocol_original_failure_binding_changed')
                if (attempt == 0 and correct_missing_limitations and
                        str(error) == 'hot_template_missing_limitations_field'):
                    # This is a new, explicitly linked semantic request, never
                    # a replay of submitted/uncertain or of a negative verdict.
                    original_state = json.loads(state_path.read_text())
                    if original_state.get('status') != 'received':
                        raise LibraryStopped('capture_protocol_correction_requires_received_original')
                    repair = folder.with_name(folder.name + '_protocol_correction_v1')
                    repair.mkdir(exist_ok=True)
                    correction_path = repair / 'correction_binding.json'
                    correction = dict(policy='one_missing_limitations_correction_v1',
                        original_ledger_path=str(state_path), original_ledger_sha256=sha256_file(state_path),
                        original_response_sha256=received['response_sha256'],
                        original_failure_path=str(failure_path), original_failure_sha256=sha256_file(failure_path),
                        comparison_sha256=proxy_sha, reason=str(error), max_corrective_requests=1)
                    if correction_path.exists():
                        if json.loads(correction_path.read_text()) != correction:
                            raise LibraryStopped('capture_protocol_correction_binding_changed')
                    else:
                        write_json(correction_path, correction)
                    prompt += ('\n格式纠正审核：上次已收到的回复遗漏了必填的limitations数组，因此未被采纳。'
                        '请重新完整听看本次实际视频，独立判断同款和素材适配，不能为通过而更改判断。'
                        '必须输出limitations字段：写出实际限制；确实没有限制时输出[]。'
                        '所有成员、本地时间、事实证据及其他必填字段的要求保持不变。'
                        '\n原回复绑定：' + json.dumps(correction, ensure_ascii=False))
                    review_folder = repair / 'audio_review'
                    continue
                if received.get('http_status') in {401, 403, 429}:
                    raise LibraryStopped('capture_template_AV_auth_or_quota_failure') from None
                raise ValueError('capture_template_received_invalid') from None
            break
        review_path = folder / 'template_review.json'
        if review_path.exists() and json.loads(review_path.read_text(encoding='utf-8')) != result:
            raise LibraryStopped('capture_template_received_result_changed')
        write_json(review_path, result)
        return dict(result, ledger_path=str(state_path), ledger_sha256=sha256_file(state_path),
            comparison_sha256=proxy_sha, actual_video_timeline=boundaries,
            review_status=received['status'], parse_error=received.get('parse_error'),
            http_status=received.get('http_status'), response_sha256=received['response_sha256'],
            **({'protocol_correction': correction} if correction else {}))
    finally:
        if key is None:
            os.environ.pop('DASHSCOPE_API_KEY', None)
        else:
            os.environ['DASHSCOPE_API_KEY'] = key


def capture_reference(home, output, *, max_candidates=10, freshness_hours=72, visual_library=None,
                      exclude_trend_ids=(), exclude_reference_sha256s=(),
                      max_video_bytes=DEFAULT_MAX_VIDEO_BYTES, min_free_bytes=DEFAULT_MIN_FREE_BYTES):
    """Select another supported trend without changing received model choices."""
    if type(max_video_bytes) is not int or max_video_bytes < 1 or type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError('capture_invalid_download_limits')
    excluded_trends = {str(value) for value in exclude_trend_ids}
    excluded_references = set(exclude_reference_sha256s)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(home) / 'shared/data/hot_references'
    with file_lock(cache / '.capture.lock'):
        # A new clock hour does not make an earlier unknown result safe to bypass.
        for path in [*cache.glob('*/capture_calls.json'), *cache.glob('*/template_*/model/library_state.json'),
                     *cache.glob('*/template_*/audio_review/state.json')]:
            value = json.loads(path.read_text(encoding='utf-8'))
            calls = value if isinstance(value, list) else value.get('calls', [value])
            if any(c['status'] in {'submitted', 'uncertain'} for c in calls):
                raise LibraryStopped('capture_existing_unknown_stops_branch:' + str(path))
        now = datetime.now(timezone.utc)
        # Reuse this hour's paid discovery and bytes, including a just-received probe.
        roots = []
        for ledger in cache.glob('*/capture_calls.json'):
            calls = json.loads(ledger.read_text(encoding='utf-8'))
            board = next((c for c in calls if c['name'] == 'hot_challenges' and c['status'] == 'received'), None)
            if board and 0 <= (now - datetime.fromisoformat(board['finished_at'])).total_seconds() < 3600:
                roots.append(ledger.parent)
        root = max(roots, key=lambda p:p.name) if roots else cache / now.strftime('%Y%m%d_%H')
        api = Wellbyte(home, root)
        return _capture_from_api(home, output, api, root, now,
            max_candidates=max_candidates, freshness_hours=freshness_hours,
            visual_library=visual_library, excluded_trends=excluded_trends,
            excluded_references=excluded_references, max_video_bytes=max_video_bytes,
            min_free_bytes=min_free_bytes)


def _capture_from_api(home, output, api, root, now, *, max_candidates, freshness_hours,
                      visual_library, excluded_trends, excluded_references,
                      max_video_bytes, min_free_bytes):
    """Shared strict admission for actual, journaled source data from either provider."""
    payload = api.call('hot_challenges', '/v1/douyin/billboard/fetch_hot_challenge_list', {'page':1, 'page_size':20})
    data = payload['data']
    stamp = datetime.strptime(str(data['last_update_time']), '%Y%m%d%H%M%S').replace(tzinfo=timezone(timedelta(hours=8)))
    if not 0 <= (now - stamp).total_seconds() <= freshness_hours * 3600:
        raise LibraryStopped('capture_hot_board_stale')
    examined = []
    trends = list(data.get('objs', []))
    main_ids = {str(row['sentence_id']) for row in trends}
    extra = getattr(api, 'extra_trends', None)
    if extra is not None:
        trends.extend(row for row in extra(now, freshness_hours=freshness_hours)
                      if str(row['sentence_id']) not in main_ids)
    if visual_library:
        from ..library.visual_library import VisualLibrary
        library = VisualLibrary(visual_library)
        ranked = []
        for trend in trends:
            hits = [c['score'] for c in library.search(trend['sentence'], limit=3)
                    if c['match_strength'] != 'weak_single_character']
            # Cheap recall preference only; actual same-template/fit still needs video evidence.
            score = sum(hits) / max(len(hits), 1)
            ranked.append((score, trend))
        ranked.sort(key=lambda row:(-row[0], row[1].get('rank', 100000)))
        trends = [r for _,r in ranked]
    # Give both real discovery sources bounded recall under the same total cap.
    # Each stream retains its board/library ranking; no extra model attempts.
    if extra is not None:
        main = [r for r in trends if str(r['sentence_id']) in main_ids]
        additional = [r for r in trends if str(r['sentence_id']) not in main_ids]
        trends = [row for pair in zip_longest(main, additional) for row in pair if row is not None]
    if visual_library:
        scores = {str(r['sentence_id']): score for score, r in ranked}
        write_json(output / 'hot_candidate_order.json', [dict(trend_id=r['sentence_id'],
            title=r['sentence'], rank=r.get('rank'), library_recall_score=scores[str(r['sentence_id'])],
            candidate_discovery=r.get('candidate_discovery')) for r in trends])
    examined.extend(dict(trend_id=str(trend['sentence_id']), reason='batch_trend_already_selected')
                    for trend in trends if str(trend['sentence_id']) in excluded_trends)
    trends = [trend for trend in trends if str(trend['sentence_id']) not in excluded_trends]
    write_json(output / 'candidate_reviews.json', examined)
    for trend in trends[:max_candidates]:
        tid = str(trend['sentence_id'])
        try:
            prepare = getattr(api, 'prepare_trend', None)
            if prepare is not None:
                prepare(trend, freshness_hours=freshness_hours)
            related = api.call('related_' + tid, '/v1/douyin/creator/fetch_creator_material_center_related',
                dict(query_id=tid, billboard_type=2, limit=20, offset=0))
        except CandidateUnavailable as error:
            examined.append(dict(trend_id=tid, reason=str(error)))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        records, authors, nonvideos = [], set(), 0
        for row in related['data'].get('item_list', []):
            aid = str(row.get('item_id') or row.get('aweme_id') or '')
            if not aid.isdecimal():
                continue
            try:
                detail = api.call('video_' + aid, '/v1/douyin/app/v3/fetch_one_video', {'aweme_id':aid})
            except CaptureAPIError as error:
                if error.api_code not in {40001, 40401, 40402, 42201}:
                    raise
                continue  # Known unavailable/bad individual public item; never retry it.
            record = video_record(detail)
            if not record['is_video']:
                nonvideos += 1
                if nonvideos == 3 and not records:
                    break  # This sampled trend is photo-led, not our video input.
                continue
            age = now.timestamp() - record['published_at']
            if (record['id'] != aid or not record['author_id'] or record['author_id'] in authors or
                    not record['urls'] or not 0 <= age <= freshness_hours * 3600):
                continue
            authors.add(record['author_id'])
            records.append(record)
            if len(records) == 3:
                break
        if len(records) < 3:
            examined.append(dict(trend_id=tid, reason='fewer_than_three_recent_video_authors'))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        required = related['data'].get('required_member_id')
        if required is not None and required not in {r['id'] for r in records}:
            examined.append(dict(trend_id=tid, reason='public_hot_representative_not_recent_video'))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        try:
            media = [_download(r, root / 'videos', max_video_bytes=max_video_bytes,
                               min_free_bytes=min_free_bytes) for r in records]
        except LibraryStopped as error:
            if not str(error).startswith('capture_CDN_download_failed:'):
                raise
            examined.append(dict(trend_id=tid, reason=str(error)))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        if all(item['sha256'] in excluded_references for item in media):
            examined.append(dict(trend_id=tid, reason='batch_reference_media_already_selected'))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        candidates = _library_candidates(visual_library, trend['sentence']) if visual_library else None
        try:
            review = _verify_template(home, root, trend, records, media, library_candidates=candidates)
        except ValueError as error:
            if str(error) not in {'model_protocol_repair_exhausted:reference', 'capture_template_received_invalid',
                                  'capture_template_media_exceeds_inline_limit'}:
                raise
            examined.append(dict(trend_id=tid, reason=str(error)))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        examined.append(dict(trend_id=tid, review=review))
        write_json(output / 'candidate_reviews.json', examined)
        if visual_library and review['same_template'] and review.get('library_adaptable') is False:
            recalled, followup = _library_recall_admission(visual_library, review, candidates, media)
            if recalled is not None:
                # The prior negative remains evidence. A single new assessment
                # is permitted only with newly recalled received observations.
                write_json(output / ('library_recall_' + tid + '.json'), followup)
                try:
                    review = _verify_template(home, root, trend, records, media,
                        library_candidates=recalled, correct_missing_limitations=False)
                except ValueError as error:
                    if str(error) != 'capture_template_received_invalid':
                        raise
                    examined.append(dict(trend_id=tid, reason=str(error), library_recall=followup))
                    write_json(output / 'candidate_reviews.json', examined)
                    continue
                claim_path = Path(followup['original_ledger_path']).parent.parent/'library_recall_once_v1.json'
                review = dict(review, library_recall=dict(
                    admission_path=str(claim_path), admission_sha256=sha256_file(claim_path),
                    original_ledger_path=followup['original_ledger_path'],
                    original_ledger_sha256=followup['original_ledger_sha256'],
                    original_response_sha256=followup['original_response_sha256'],
                    candidates_sha256=followup['candidates_sha256'], max_new_assessments=1))
                examined.append(dict(trend_id=tid, review=review, library_recall=followup))
                write_json(output / 'candidate_reviews.json', examined)
        if not review['same_template'] or review.get('library_adaptable') is False:
            continue
        selected = next(m for m in media if m['id'] == review['representative_id'])
        if selected['sha256'] in excluded_references:
            examined.append(dict(trend_id=tid, reason='batch_reference_already_selected',
                                 representative_id=selected['id'], sha256=selected['sha256']))
            write_json(output / 'candidate_reviews.json', examined)
            continue
        record = next(r for r in records if r['id'] == selected['id'])
        snapshot = output / 'api_calls.json'
        write_json(snapshot, api.calls)
        evidence = dict(board_updated_at=stamp.isoformat(), captured_at=_now(),
            trend={k:trend.get(k) for k in ('sentence','sentence_id','rank','hot_score','video_count')},
            authors=[{k:r[k] for k in ('id','author_id','title','published_at','likes')} for r in records],
            template_review=review, api_ledger_path=str(snapshot), api_ledger_sha256=sha256_file(snapshot),
            shared_api_ledger_path=str(api.ledger),
            limitation='Three recent independent examples and model visual comparison; not exhaustive platform statistics or human verification.')
        evidence.update(getattr(api, 'source_evidence', {}))
        if trend.get('candidate_discovery') is not None:
            evidence['candidate_discovery'] = trend['candidate_discovery']
        result = dict(path=selected['path'], sha256=selected['sha256'], evidence=evidence,
                      source_url='https://www.douyin.com/video/' + record['id'])
        write_json(output / 'capture_receipt.json', result)
        return result
    raise LibraryStopped('capture_no_distinct_supported_same_template_candidate' if
                         excluded_trends or excluded_references else 'capture_no_supported_same_template_candidate')
