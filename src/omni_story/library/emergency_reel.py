"""L4 last-resort reel: a playable, honestly labelled video when every other level failed.

Depends only on ffmpeg/ffprobe, the reference file and the library's video files. No model,
TTS, ASR, index, visual library, registry or paid review. Pictures are evenly spaced clips of
the library films (the reference's own picture only when the library has none, labelled);
sound is the reference's own audio when decodable (labelled: original voices), else silence.
The caller decides policy (requested stops and proven content-filter refusals do not come here).
"""
import json
import math
import subprocess
from pathlib import Path

POLICY = 'emergency_reel_v1'
FPS, WIDTH, HEIGHT = 30, 1280, 720
CLIP_S = 4.0
MEDIA_EXTENSIONS = {'.mp4', '.mkv', '.mov', '.m4v', '.webm', '.avi'}


def _run(args, timeout=900):
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError('emergency_reel_ffmpeg_failed:' + proc.stderr.decode(errors='replace')[-1500:])
    return proc.stdout


def probe(path):
    """{'duration_s', 'video', 'audio_index'} or None when the file is not decodable media."""
    try:
        raw = _run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=index,codec_type',
                    '-of', 'json', str(path)], timeout=120)
        data = json.loads(raw)
        duration = float(data['format']['duration'])
    except Exception:  # noqa: BLE001 - an unreadable file is simply not a source
        return None
    streams = data.get('streams', [])
    return dict(duration_s=duration, video=any(s.get('codec_type') == 'video' for s in streams),
                audio_index=next((s['index'] for s in streams if s.get('codec_type') == 'audio'), None))


def plan_clips(films, duration_s):
    """Evenly spaced CLIP_S clips, round-robin over films, inside each film's 5%..95% span."""
    count = max(1, math.ceil(duration_s / CLIP_S))
    clips = []
    for i in range(count):
        film = films[i % len(films)]
        turn, turns = i // len(films), math.ceil(count / len(films))
        low, high = film['duration_s'] * .05, max(film['duration_s'] * .95 - CLIP_S, film['duration_s'] * .05)
        start = low + (high - low) * (turn + .5) / max(1, turns)
        length = min(CLIP_S, duration_s - i * CLIP_S)
        clips.append(dict(path=film['path'], start_s=round(start, 3), length_s=round(length, 3)))
    return clips


def _sha(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def profile_clips(profile_path, duration):
    """Even the last resort stays inside this collection's published usable ranges."""
    from .material_profile import load, reviewed_exclusions
    from .material_index import MaterialIndex
    from .content_ranges import check_content_range
    profile, binding = load(profile_path)
    catalog = json.loads(Path(profile['source_catalog']).read_text())
    sources = {s['sha256']: s for s in catalog['sources']}
    bounds = json.loads((Path(profile['visual_library_root'])/'content_ranges.json').read_text())['sources']
    for sha, source in sources.items():
        source['content_range'] = bounds[sha]
    index = MaterialIndex.load(profile['material_index_root'])
    excluded = reviewed_exclusions(binding,index)
    rows = []
    for row in index.records:
        if row['kind'] != 'range' or row['observation_id'] in excluded:
            continue
        a,b = row['source_start_s'],row['source_end_s']
        if b-a < CLIP_S+.002:
            continue
        try:
            check_content_range(sources[row['source_sha256']],a,b,'emergency')
        except ValueError:
            continue
        rows.append(row)
    if not rows:
        raise RuntimeError('emergency_reel:no_usable_published_ranges')
    clips = []
    for i in range(math.ceil(duration/CLIP_S)):
        row = rows[i % len(rows)]
        start = math.ceil(row['source_start_s']*1000)/1000
        clips.append(dict(path=sources[row['source_sha256']]['path'],start_s=start,
            length_s=min(CLIP_S,duration-i*CLIP_S)))
    return clips,binding


def produce(reference, library, output, *, error, write_outcome=True, material_profile=None):
    """Write final_l4*.mp4 plus the records downstream acceptance reads; return the result dict."""
    reference, output = Path(reference), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    work = output / 'emergency_l4'
    work.mkdir(exist_ok=True)
    reference_sha = _sha(reference)
    ref = probe(reference)
    duration = min(max(ref['duration_s'] if ref else 30.0, 2.0), 600.0)
    library = Path(library)
    candidates = sorted(p for p in (library.iterdir() if library.is_dir() else [library])
                        if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS and not p.name.endswith('.part'))
    films = []
    for path in candidates:
        info = probe(path)
        if info and info['video'] and info['duration_s'] > 2 * CLIP_S:
            films.append(dict(path=str(path), duration_s=info['duration_s']))
    picture_source = 'library_films'
    if not films and not material_profile:
        if not (ref and ref['video']):
            raise RuntimeError('emergency_reel_no_decodable_picture_source')
        films, picture_source = [dict(path=str(reference), duration_s=ref['duration_s'])], 'reference_frames_labelled'
    binding = None
    if material_profile:
        clips,binding = profile_clips(material_profile,duration)
        picture_source = 'published_collection_ranges'
    else:
        clips = plan_clips(films, duration)
    parts = []
    scale = (f'scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,'
             f'pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p')
    for i, clip in enumerate(clips):
        part = work / f'clip_{i:04d}.mp4'
        if not part.is_file():
            _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(clip['start_s']), '-i', clip['path'],
                  '-t', str(clip['length_s']), '-an', '-vf', scale, '-c:v', 'libx264', '-preset', 'veryfast',
                  '-crf', '26', str(part)])
        parts.append(part)
    (work / 'concat.txt').write_text(''.join(f"file '{p.name}'\n" for p in parts), encoding='utf-8')
    picture = work / 'picture.mp4'
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(work / 'concat.txt'),
          '-c', 'copy', str(picture)])
    audio_source = 'reference_original_audio_may_contain_voices' if ref and ref['audio_index'] is not None else 'silence'
    number = 1
    final = output / 'final_l4.mp4'
    while final.exists():
        number += 1
        final = output / f'final_l4_{number}.mp4'
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(picture)]
    if audio_source == 'silence':
        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-map', '0:v:0', '-map', '1:a:0']
    else:
        args += ['-i', str(reference), '-map', '0:v:0', '-map', f'1:{ref["audio_index"]}']
    args += ['-t', str(duration), '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2',
             '-movflags', '+faststart', str(final)]
    try:
        _run(args)
    except RuntimeError:
        if audio_source == 'silence':
            raise
        final.unlink(missing_ok=True)  # undecodable reference audio: fall back to silence
        audio_source = 'silence'
        _run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(picture), '-f', 'lavfi', '-i',
              'anullsrc=r=48000:cl=stereo', '-map', '0:v:0', '-map', '1:a:0', '-t', str(duration), '-c:v', 'copy',
              '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(final)])
    checked = probe(final)
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(final), '-f', 'null', '-'])  # whole-file decode check
    if not checked or not checked['video'] or checked['duration_s'] <= 0:
        raise RuntimeError('emergency_reel_output_not_playable')
    code = str(error).split(':', 1)[0].strip()[:80] or type(error).__name__
    record = dict(policy=POLICY, fallback_level=4, delivery_mode='emergency_reel', reasons=[code, 'all_levels_failed'],
        error=f'{type(error).__name__}:{str(error)[:4000]}' if isinstance(error, BaseException) else str(error)[:4000],
        picture_source=picture_source, audio_source=audio_source, films=[f['path'] for f in films], clips=len(clips),
        reference_s=ref['duration_s'] if ref else None, picture_s=checked['duration_s'],
        generation_model_requests_added=0, review='skipped_no_paid_request',
        limitations=['Last-resort reel: library clips unrelated to the reference story, no adaptation, no new voice.',
                     f'Audio is {audio_source}.'])
    if binding:
        record['material_profile'] = binding
    (output / 'emergency_l4.json').write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    reading = output / 'reference_reading.json'
    if not reading.is_file():
        reading.write_text(json.dumps(dict(reference_sha256=reference_sha, source=POLICY), ensure_ascii=False), encoding='utf-8')
    result = dict(status='delivered_with_review_notes', material_flow='scene_v1', final_video=str(final),
        final_sha256=_sha(final), voices={}, facts=[], lines=[], audio_lines=[], notes=[], review=None,
        timeline=[dict(segment_id='seg_1', scene_id='emergency', start_s=0, end_s=checked['duration_s'])],
        artifact_ready=True, delivery_mode='emergency_reel', fallback_level=4, degradation_reasons=record['reasons'],
        degradation_path=str(output / 'emergency_l4.json'), quality_status='review_skipped', content_delivery_ready=False,
        joint_quality_gate=False, subtitle_mode='none', usage=dict(requests=0),
        dialogue_result_path=str(output / 'dialogue_result.json'))
    for name in ('dialogue_result.json', 'result.json'):
        (output / name).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    if write_outcome:
        from .run_outcome import write_run_outcome
        write_run_outcome(output, result)
    return result


def delivered_result(output):
    """An already registered, still intact delivery in this task directory (no new work)."""
    path = Path(output) / 'dialogue_result.json'
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        video = Path(result['final_video'])
        if result.get('artifact_ready') or result.get('status', '').startswith('delivered'):
            if video.is_file() and _sha(video) == result.get('final_sha256'):
                return result
    except Exception:  # noqa: BLE001 - no intact delivery
        return None
    return None
