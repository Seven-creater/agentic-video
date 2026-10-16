"""Source-bound local audiovisual reviews; no script or authored line inputs."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess

from .audio_timeline import actual_voice_bands
from .media import probe_media, sha256_file
from .state import file_lock, json_sha, write_json
from .visual_audio import review_check

POLICY = 'actual-audio-video-segments-v2-scoped-evidence'
SAMPLE_RATE = 48000
SCOPE_NOTE_TARGETS = frozenset({'phoneme_viseme_lipsync', 'original_actor_voice_identity'})


def _run(args):
    result = subprocess.run(args, capture_output=True, timeout=900)
    if result.returncode:
        raise ValueError('audio_segments_media_failed:' + result.stderr.decode(errors='replace')[-2000:])
    return result.stdout


def _frames(path, selector='v:0'):
    value = json.loads(_run(['ffprobe', '-v', 'error', '-select_streams', selector,
        '-show_frames', '-show_streams', '-show_entries',
        'stream=time_base:frame=best_effort_timestamp', '-of', 'json', str(path)]))
    time_base = Fraction(value['streams'][0]['time_base'])
    pts = [float(int(row['best_effort_timestamp'])*time_base) for row in value['frames']]
    if (not pts or pts[0] != 0 or
            any(not math.isfinite(x) for x in pts) or any(b <= a for a, b in zip(pts, pts[1:]))):
        raise ValueError('audio_segments_nonzero_or_invalid_video_pts')
    return pts


def _intervals(pts, duration, bands, maximum):
    """Prefer a late PCM silence boundary; otherwise split at a real frame PTS."""
    remaining = [*pts[1:], duration]
    start, windows = 0.0, []
    while start < duration - .000001:
        candidates = [t for t in remaining if start < t <= start + maximum + .000001]
        if not candidates:
            raise ValueError('audio_segments_frame_longer_than_technical_window')
        end = candidates[-1]
        if end < duration:
            quiet = [t for t in candidates if t - start >= maximum * .75 and
                     not any(b['start_s'] < t < b['end_s'] for b in bands)]
            if quiet:
                end = quiet[-1]
        windows.append((start, end))
        start = end
    return windows


def _local_bands(bands, start, end):
    return [dict(id=b['id'], start_s=max(start, b['start_s']) - start,
                 end_s=min(end, b['end_s']) - start)
            for b in bands if b['start_s'] < end and b['end_s'] > start]


def _lines(bands):
    return [dict(id=b['id'], start_s=b['start_s'], actual_duration_s=b['end_s']-b['start_s'])
            for b in bands]


def _check_source(source, digest):
    if sha256_file(source) != digest:
        raise ValueError('audio_segments_source_changed_no_replay')


def _clip(source, source_sha, probe, pts, folder, index, start, end):
    directory = folder / 'media' / f'segment_{index:04d}'
    directory.mkdir(parents=True, exist_ok=True)
    target, record = directory / 'video.mp4', directory / 'lineage.json'
    video = next(row for row in probe['streams'] if row['index'] == probe['video_stream_index'])
    audio = [row for row in probe['streams'] if row.get('codec_type') == 'audio']
    first_frame = sum(t < start-.000001 for t in pts)
    end_frame = sum(t < end-.000001 for t in pts)
    spec = dict(policy=POLICY, source_path=str(source), source_sha256=source_sha,
        source_start_s=start, source_end_s=end, video_stream_index=video['index'],
        source_first_frame_index=first_frame, source_end_frame_index_exclusive=end_frame,
        audio_stream_index=audio[0]['index'], audio_start_sample=round(start*SAMPLE_RATE),
        audio_end_sample=round(end*SAMPLE_RATE), time_mapping='source_time_s = source_start_s + clip_time_s',
        video_encoder='libx264_crf18_native_geometry_pts', audio_encoder='aac_320k_48k_stereo')
    _check_source(source, source_sha)
    if record.exists():
        saved = json.loads(record.read_text(encoding='utf-8'))
        if (saved.get('spec') != spec or not target.is_file() or saved.get('path') != str(target) or
                sha256_file(target) != saved.get('sha256') or saved.get('fully_decoded') is not True):
            raise ValueError('audio_segments_clip_binding_changed_no_replay')
        return saved
    if target.exists():
        raise ValueError('audio_segments_orphan_clip_no_replay')
    vf = f"[0:{video['index']}]trim=start_frame={first_frame}:end_frame={end_frame},setpts=PTS-STARTPTS[v]"
    af = (f"[0:{audio[0]['index']}]atrim=start_sample={spec['audio_start_sample']}:"
          f"end_sample={spec['audio_end_sample']},asetpts=N/SR/TB[a]")
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(source), '-filter_complex', vf+';'+af,
        '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-pix_fmt', video.get('pix_fmt', 'yuv420p'), '-bf', '0', '-fps_mode', 'passthrough',
        '-video_track_timescale', str(Fraction(video['time_base']).denominator),
        '-c:a', 'aac', '-b:a', '320k', '-ar', str(SAMPLE_RATE), '-ac', '2',
        '-map_metadata', '-1', '-movflags', '+faststart', str(target)])
    measured = probe_media(target)
    output_video = next(row for row in measured['streams'] if row['index'] == measured['video_stream_index'])
    output_audio = [row for row in measured['streams'] if row.get('codec_type') == 'audio']
    actual_pts = _frames(target)
    audio_pts = _frames(target, 'a:0')
    expected_pts = [t-start for t in pts if start-.000001 <= t < end-.000001]
    output_duration = float(int(output_video['duration_ts']) * Fraction(output_video['time_base']))
    if (len(actual_pts) != len(expected_pts) or
            any(abs(a-b) > .000002 for a, b in zip(actual_pts, expected_pts)) or
            abs(output_duration-(end-start)) > .001 or
            any(output_video[k] != video[k] for k in ('width', 'height')) or
            len(output_audio) != 1 or int(output_audio[0].get('sample_rate', 0)) != SAMPLE_RATE or
            output_audio[0].get('channels') != 2 or
            abs(float(output_audio[0].get('start_time', -1))) > 1/SAMPLE_RATE or
            abs(float(output_audio[0].get('duration', 0))-(end-start)) > .001):
        raise ValueError('audio_segments_actual_clip_timeline_invalid')
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
          '-i', str(target), '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    _check_source(source, source_sha)
    saved = dict(spec=spec, path=str(target), sha256=sha256_file(target),
        duration_s=end-start, probe=measured, decoded_frame_count=len(actual_pts),
        video_first_pts_s=actual_pts[0], audio_first_pts_s=audio_pts[0],
        audio_present=True, fully_decoded=True)
    write_json(record, saved)
    return saved


def _prompt(instructions, duration, bands):
    return instructions + '\n' + json.dumps(dict(clip_duration_s=duration,
        actual_pcm_voice_bands_local=bands), ensure_ascii=False) + (
        '\n这是单独的实际音视频片段；时间从0秒开始，所有时间只能使用本片段局部时间。'
        '每个区间严格满足0<=start_s<end_s<=clip_duration_s。'
        '整段结束时间必须原样使用上面的clip_duration_s，禁止四舍五入到比实际时长更大的数；'
        '例如时长5.066666666666666不能写成5.07。结束时刻不明确须partial，不能编造或越界。'
        '只有上面来自真实绑定语音PCM的能量区间，不提供预期对白、作者脚本或人物关系。'
        '逐句实际听词句和音色，逐段实际看画面；seen必须覆盖整段，包括无语音时段。'
        'seen只能写实际可见像素，evidence_source固定为visible_pixels；'
        '画外人物嘴部不可见，不能从声音推断其嘴部开合并计为seen。音乐也不是画面观察。'
        'heard和seen必须覆盖每个实际发声区间，不能仅凭大致印象猜时间或补写听不清的话。'
        '若区间边界切入了半句话，只记录本片段真实听到的部分，勿补全未听到内容；无法核实则partial。'
        'limitations只记录本次范围内的真实听不清、语音不自然、说话角色/动作不明或未解决错配。'
        '精确逐音素唇同步和未知原演员声音身份不在本次范围；若需要注明，仅写scope_notes中'
        '的phoneme_viseme_lipsync或original_actor_voice_identity，不写入limitations，不据此宣称已通过该项。'
        'scope_notes不可装入任何范围内未核实项；正常静音或未听到音乐本身不是限制。'
        '实际听到的背景音乐或未听到声音写soundscape，evidence_source固定heard_audio；'
        '“未听到音乐”只是听觉观察，不能据此声称实际音轨没有任何音乐信号。'
        '只输出JSON对象 {"status":"pass或partial或fail",'
        '"heard":[{"start_s":0,"end_s":0,"text":"实际听到","voice_description":"音色"}],'
        '"seen":[{"start_s":0,"end_s":0,"description":"实际画面","evidence_source":"visible_pixels"}],'
        '"soundscape":[{"start_s":0,"end_s":0,"description":"实际听到的音乐/环境声或未听到声音",'
        '"evidence_source":"heard_audio"}],'
        '"issues":[{"start_s":0,"end_s":0,"description":"真实问题"}],'
        '"limitations":[],"scope_notes":[]}。'
        '有问题、遗漏或未核实项不能pass；没有实际语音时heard可为空。')


def _missing_picture(rows, duration):
    cursor, end = min(.05, duration/4), duration-min(.05, duration/4)
    for row in sorted(rows, key=lambda row: row['start_s']):
        if row['end_s'] <= cursor:
            continue
        if row['start_s'] > cursor+.000001:
            return True
        cursor = max(cursor, row['end_s'])
    return cursor < end-.000001


def _scope_notes(value):
    notes = value.get('scope_notes', [])
    if (not isinstance(notes, list) or
            any(not isinstance(note, str) or note not in SCOPE_NOTE_TARGETS for note in notes) or
            len(set(notes)) != len(notes)):
        raise ValueError('audio_review_scope_notes_declared_unmeasured_targets_required')
    return notes


def _evidence_row(row, duration, kind):
    if not isinstance(row, dict) or row.get('evidence_source') != kind:
        raise ValueError('audio_review_'+kind+'_evidence_required')
    # Soundscape uses the same strict actual interval/description contract as
    # an issue, while retaining its observational rather than adverse meaning.
    review_check(dict(status='partial', issues=[row], limitations=[]), duration)


def _normalized(value, duration, bands):
    """Keep only schema-valid actual local rows; retain bad replies separately."""
    valid = dict(status='partial', heard=[], seen=[], soundscape=[], issues=[], limitations=[], scope_notes=[])
    error = None
    try:
        json_sha(value)  # Received non-finite JSON numbers cannot enter a result.
        # A voice-free clip may honestly have no heard rows. Do not fabricate one
        # just to satisfy the whole-film pass-requires-heard rule.
        checked = deepcopy(value)
        if isinstance(checked, dict):
            _scope_notes(checked)
            for row in checked.get('seen', []) if isinstance(checked.get('seen'), list) else []:
                _evidence_row(row, duration, 'visible_pixels')
            if not isinstance(checked.get('soundscape', []), list):
                raise ValueError('audio_review_soundscape_list_required')
            for row in checked.get('soundscape', []):
                _evidence_row(row, duration, 'heard_audio')
        if isinstance(checked, dict) and not bands and checked.get('status') == 'pass':
            review_check(checked, duration)
            checked['status'] = 'partial'
        review_check(checked, duration, audiovisual=True, lines=_lines(bands))
        if bands:
            coverage = dict(checked, status='pass', issues=[], limitations=[])
            review_check(coverage, duration, audiovisual=True, lines=_lines(bands))
        if _missing_picture(checked['seen'], duration):
            raise ValueError('audio_review_missing_full_picture_coverage')
    except (ValueError, TypeError, KeyError) as exc:
        error = str(exc)
    if isinstance(value, dict):
        for key in ('heard', 'seen', 'issues'):
            for row in value.get(key, []) if isinstance(value.get(key), list) else []:
                test = dict(status='partial', heard=[], seen=[], issues=[], limitations=[])
                test[key] = [row]
                try:
                    json_sha(row)
                    review_check(test, duration, audiovisual=True)
                    if key == 'seen':
                        _evidence_row(row, duration, 'visible_pixels')
                except (ValueError, TypeError, KeyError):
                    continue
                valid[key].append(deepcopy(row))
        valid['limitations'] = [row for row in value.get('limitations', []) if isinstance(row, str)] \
            if isinstance(value.get('limitations'), list) else []
        try:
            valid['scope_notes'] = list(_scope_notes(value))
        except ValueError:
            pass
        for row in value.get('soundscape', []) if isinstance(value.get('soundscape', []), list) else []:
            try:
                json_sha(row)
                _evidence_row(row, duration, 'heard_audio')
            except (ValueError, TypeError, KeyError):
                continue
            valid['soundscape'].append(deepcopy(row))
    if error:
        if isinstance(value, dict) and value.get('status') == 'fail':
            valid['status'] = 'fail'
        valid['issues'].append(dict(start_s=0, end_s=duration,
            description='Unresolved actual segment review: '+error, review_protocol_issue=True))
        valid['limitations'].append('Received segment reply does not establish complete valid observation: '+error)
    else:
        valid['status'] = value['status']
    return valid, error


def _immutable_json(path, value, reason):
    if path.exists():
        if json_sha(json.loads(path.read_text(encoding='utf-8'))) != json_sha(value):
            raise ValueError('audio_segments_'+reason+'_changed_no_replay')
    else:
        write_json(path, value)


def review_segments(video, output_dir, *, prompt, timeline=None, api=None, max_segment_s=6.0):
    from .audio_review import QwenAPI, review_audio_video
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('audio_segments_instruction_prompt_required')
    if type(max_segment_s) not in (int, float) or not math.isfinite(max_segment_s) or max_segment_s <= 0:
        raise ValueError('audio_segments_technical_window_invalid')
    source = Path(video).resolve(strict=True)
    observed = actual_voice_bands(source)
    if timeline is not None and json_sha(timeline) != json_sha(observed):
        raise ValueError('audio_segments_timeline_not_actual_source_bound_pcm')
    duration, source_sha = observed['duration_s'], observed['candidate_sha256']
    probe, pts = probe_media(source), _frames(source)
    folder = Path(output_dir).resolve()
    # A top-level service ledger would charge-count a request that never happened.
    if (folder/'state.json').exists() or (folder/'request.json').exists():
        raise ValueError('audio_segments_existing_whole_review_no_replay')
    folder.mkdir(parents=True, exist_ok=True)
    runner = api if api is not None else QwenAPI()
    with file_lock(folder/'.audio_segments.lock'):
        windows = _intervals(pts, duration, observed['bands'], float(max_segment_s))
        chunks = []
        for index, (start, end) in enumerate(windows, 1):
            clip = _clip(source, source_sha, probe, pts, folder, index, start, end)
            lineage = Path(clip['path']).with_name('lineage.json')
            local = _local_bands(observed['bands'], start, end)
            chunks.append(dict(segment_id=f'segment_{index:04d}', source_start_s=start,
                source_end_s=end, local_voice_bands=local, clip=clip,
                lineage_path=str(lineage), lineage_sha256=sha256_file(lineage),
                service_directory=str(folder.with_name(folder.name+f'_segment_{index:04d}'))))
        manifest = dict(policy=POLICY, source_path=str(source), source_sha256=source_sha,
            duration_s=duration, timeline=observed, timeline_sha256=json_sha(observed),
            instruction_prompt_sha256=json_sha(prompt), technical_max_segment_s=float(max_segment_s),
            full_candidate_contiguous_coverage=True, segments=chunks)
        _immutable_json(folder/'manifest.json', manifest, 'manifest_binding')
        merged = dict(status='pass', heard=[], seen=[], soundscape=[], issues=[], limitations=[], scope_notes=[])
        evidence, usage, cached = [], {}, True
        for chunk in chunks:
            _check_source(source, source_sha)
            start, end, clip = chunk['source_start_s'], chunk['source_end_s'], chunk['clip']
            binding = dict(source_path=str(source), source_sha256=source_sha,
                source_start_s=start, source_end_s=end, clip_sha256=clip['sha256'],
                lineage_path=chunk['lineage_path'], lineage_sha256=chunk['lineage_sha256'],
                time_mapping=clip['spec']['time_mapping'], clip_video_first_pts_s=clip['video_first_pts_s'],
                clip_audio_first_pts_s=clip['audio_first_pts_s'])
            result = review_audio_video(clip['path'], chunk['service_directory'],
                prompt=_prompt(prompt, end-start, chunk['local_voice_bands']), api=runner, source_binding=binding)
            _check_source(source, source_sha)
            value, error = _normalized(result['parsed'] if not result['parse_error'] else None,
                                       end-start, chunk['local_voice_bands'])
            if result['parse_error']:
                error = result['parse_error']
                value['limitations'].append('Received transport/reply parse error: '+error)
            cached &= result['cache_hit']
            if value['status'] == 'fail':
                merged['status'] = 'fail'
            elif value['status'] != 'pass' and merged['status'] == 'pass':
                merged['status'] = 'partial'
            provenance = dict(segment_id=chunk['segment_id'], source_sha256=source_sha,
                source_start_s=start, source_end_s=end, clip_sha256=clip['sha256'],
                request_sha256=result['request_sha256'], response_sha256=result['response_sha256'],
                response_path=str(Path(chunk['service_directory'])/'response.json'))
            for key in ('heard', 'seen', 'soundscape', 'issues'):
                for row in value[key]:
                    row['local_start_s'], row['local_end_s'] = row['start_s'], row['end_s']
                    row['start_s'], row['end_s'] = min(end, start+row['start_s']), min(end, start+row['end_s'])
                    row['provenance'] = provenance
                    merged[key].append(row)
            merged['limitations'].extend(chunk['segment_id']+': '+text for text in value['limitations'])
            merged['scope_notes'] = sorted(set(merged['scope_notes']) | set(value['scope_notes']))
            evidence.append(dict(provenance, http_status=result['http_status'], parse_error=result['parse_error'],
                validation_error=error, model_status=((result['parsed'] or {}).get('status')
                    if (result['parsed'] or {}).get('status') in ('pass', 'partial', 'fail') else None),
                accepted_status=value['status'], scope_notes=value['scope_notes']))
            for key, number in result['usage'].items() if isinstance(result['usage'], dict) else []:
                if type(number) in (int, float) and math.isfinite(number) and number >= 0:
                    usage[key] = usage.get(key, 0)+number
        # Recheck the global bands across clip boundaries; a received pass is not
        # promoted when either heard or seen omits an actual PCM voice interval.
        try:
            review_check(merged, duration, audiovisual=True, lines=_lines(observed['bands']))
        except ValueError as exc:
            if merged['status'] != 'fail':
                merged['status'] = 'partial'
            merged['issues'].append(dict(start_s=0, end_s=duration,
                description='Unresolved global review: '+str(exc), review_protocol_issue=True))
            merged['limitations'].append('Merged observations did not establish complete actual voice-band coverage: '+str(exc))
        review_check(merged, duration, audiovisual=True, lines=_lines(observed['bands']))
        _check_source(source, source_sha)
        saved = dict(status='received', parsed=merged, usage=usage, http_status=200, parse_error=None,
            video_sha256=source_sha, request_sha256=json_sha(manifest), segments=evidence,
            manifest_path=str(folder/'manifest.json'), manifest_sha256=sha256_file(folder/'manifest.json'))
        _immutable_json(folder/'result.json', saved, 'received_result')
        return dict(saved, response_sha256=sha256_file(folder/'result.json'), cache_hit=cached)
