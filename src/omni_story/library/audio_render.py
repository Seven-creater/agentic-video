"""Local subtitle masking and bounded replacement speech, without paid calls.

FFmpeg filter reference: https://ffmpeg.org/ffmpeg-filters.html
The video timeline is retained. Speech that does not fit raises AudioTimingError;
callers must revise their plan rather than truncating audio or extending video.
"""
from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
import wave

import numpy as np

from .media import probe_media, sha256_file
from .state import json_sha, write_json
from .tts import _probe_audio

SAMPLE_RATE = 48000
RENDER_POLICY = 'audio-render-v2-zero-anchor-receipt-reuse'


def subtitle_final_video(video, lines, output):
    """Burn captions once from final speech times; retain the existing audio."""
    from PIL import ImageFont
    from .render import _caption_font
    source = Path(video).resolve(strict=True)
    info = _video_info(source)
    target = _output_path(source, output)
    cues = []
    previous_end = 0.0
    for line in lines:
        start, end = (_number(line[k], k) for k in ('start_s', 'end_s'))
        text = ' '.join(line['text'].split())
        if not text or not previous_end <= start < end <= info['duration_s'] + .001:
            raise ValueError('final_subtitle_invalid_speech_interval')
        previous_end = end
        # Phrase times are proportional within measured utterances, not ASR alignment.
        chunks = [text[i:i+36] for i in range(0, len(text), 36)]
        offset = 0
        for chunk in chunks:
            begin = start + (end-start) * offset / len(text)
            offset += len(chunk)
            finish = start + (end-start) * offset / len(text)
            cues.append((begin, finish, [chunk[i:i+18] for i in range(0, len(chunk), 18)]))
    if not cues:
        raise ValueError('final_subtitle_speech_required')

    def stamp(seconds, *, ass=False):
        scale = 100 if ass else 1000
        total = round(seconds*scale)
        whole, fraction = divmod(total, scale)
        minutes, sec = divmod(whole, 60)
        hours, minute = divmod(minutes, 60)
        return (f'{hours}:{minute:02}:{sec:02}.{fraction:02}' if ass else
                f'{hours:02}:{minute:02}:{sec:02},{fraction:03}')

    srt = target.with_suffix('.srt')
    srt.write_text('\n\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n' + '\n'.join(rows)
        for i, (a, b, rows) in enumerate(cues, 1)) + '\n', encoding='utf-8')
    font = _caption_font()
    size = max(18, round(info['width']*.044))
    family = ImageFont.truetype(str(font), size).getname()[0]
    ass = target.with_suffix('.ass')
    header = (f'[Script Info]\nScriptType: v4.00+\nPlayResX: {info["width"]}\n'
        f'PlayResY: {info["height"]}\nWrapStyle: 2\n\n[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Default,{family},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,36,36,{round(info["height"]*.065)},1\n\n'
        '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    events = []
    for a, b, rows in cues:
        # Do not interpret generated text as ASS overrides.
        text = '\\N'.join(row.replace('\\', '＼').replace('{', '｛').replace('}', '｝') for row in rows)
        events.append(f'Dialogue: 0,{stamp(a, ass=True)},{stamp(b, ass=True)},Default,,0,0,0,,{text}')
    ass.write_text(header+'\n'.join(events)+'\n', encoding='utf-8-sig')
    # A short cwd avoids Windows filter-path quoting and historic long-path problems.
    with tempfile.TemporaryDirectory(prefix='omni-subtitles-') as directory:
        shutil.copyfile(ass, Path(directory)/'subtitle.ass')
        shutil.copyfile(font, Path(directory)/font.name)
        proc = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(source),
            '-vf', 'ass=subtitle.ass:fontsdir=.', '-map', '0:v:0', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-fps_mode', 'passthrough',
            '-c:a', 'copy', '-movflags', '+faststart', str(target)], cwd=directory,
            capture_output=True, timeout=900)
        if proc.returncode:
            raise RuntimeError('final_subtitle_render_failed:'+proc.stderr.decode(errors='replace')[-2000:])
    return _receipt(target, info, source, 'final_subtitles',
        subtitle_srt_path=str(srt), subtitle_srt_sha256=sha256_file(srt),
        subtitle_ass_path=str(ass), subtitle_ass_sha256=sha256_file(ass),
        lines=lines, audio_stream_copied=True,
        timing='Measured utterance boundaries; proportional phrase splits, not word alignment.')


class AudioTimingError(ValueError):
    def __init__(self, line_id, actual_s, window_s):
        self.line_id, self.actual_s, self.window_s = line_id, actual_s, window_s
        self.delta_s = actual_s - window_s
        super().__init__(f'audio_line_does_not_fit:{line_id}:actual_s={actual_s:.6f}'
                         f':window_s={window_s:.6f}:delta_s={self.delta_s:.6f}')


def _run(args):
    result = subprocess.run(args, capture_output=True, timeout=900)
    if result.returncode:
        raise RuntimeError('audio_render_ffmpeg_failed:' + result.stderr.decode(errors='replace')[-4000:])
    return result


def _number(value, name):
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError('audio_render_invalid_' + name)
    return float(value)


def _video_info(path):
    probe = probe_media(path)
    stream = next(s for s in probe['streams'] if s['index'] == probe['video_stream_index'])
    if stream.get('duration_ts') is not None and stream.get('time_base'):
        duration = float(int(stream['duration_ts']) * Fraction(stream['time_base']))
    else:
        duration = float(stream.get('duration') or probe['duration_s'])
    fps = Fraction(stream.get('avg_frame_rate') or stream['r_frame_rate'])
    if not math.isfinite(duration) or duration <= 0 or fps <= 0:
        raise ValueError('audio_render_invalid_video_timeline')
    return {'width': int(stream['width']), 'height': int(stream['height']),
            'fps': str(fps), 'duration_s': duration, 'video_stream_index': stream['index'],
            'start_s': float(stream.get('start_time', 0)),
            'frame_count': stream.get('nb_frames'), 'probe': probe}


def _output_path(video, output, other_inputs=()):
    path = Path(output).resolve()
    inputs = [Path(video).resolve(), *(Path(p).resolve() for p in other_inputs)]
    if path in inputs:
        raise ValueError('audio_render_output_must_differ_from_inputs')
    if path.exists() or path.with_suffix(path.suffix + '.receipt.json').exists():
        raise ValueError('audio_render_output_already_exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _verify_video(output, original, *, stereo=False):
    # A zero return code from a full decode is required, not just a container probe.
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-err_detect', 'explode',
          '-i', str(output), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])
    current = _video_info(output)
    if any(current[key] != original[key] for key in ('width', 'height', 'fps')):
        raise ValueError('audio_render_video_geometry_or_fps_changed')
    if abs(current['duration_s'] - original['duration_s']) > .001:
        raise ValueError('audio_render_video_duration_changed')
    if abs(current['start_s']) > .0001:
        raise ValueError('audio_render_video_not_zero_anchored')
    if (original['frame_count'] is not None and current['frame_count'] is not None and
            current['frame_count'] != original['frame_count']):
        raise ValueError('audio_render_video_frame_count_changed')
    if stereo:
        audio = [s for s in current['probe']['streams'] if s.get('codec_type') == 'audio']
        if (len(audio) != 1 or int(audio[0].get('sample_rate', 0)) != SAMPLE_RATE or
                audio[0].get('channels') != 2):
            raise ValueError('audio_render_output_must_be_48k_stereo')
        if (abs(float(audio[0].get('start_time', 0))) > 1 / SAMPLE_RATE or
                abs(float(audio[0].get('duration', 0)) - original['duration_s']) > .001):
            raise ValueError('audio_render_output_audio_timeline_changed')
    return current


def _receipt(output, original, source, operation, **details):
    current = _verify_video(output, original, stereo=operation == 'mix_speech')
    path = output.with_suffix(output.suffix + '.receipt.json')
    record = {'policy': RENDER_POLICY, 'operation': operation, 'path': str(output), 'sha256': sha256_file(output),
              'duration_s': current['duration_s'], 'width': current['width'], 'height': current['height'],
              'fps': current['fps'], 'probe': current['probe'], 'fully_decoded': True,
              'source_video_path': str(source), 'source_video_sha256': sha256_file(source),
              'source_video_start_s': original['start_s'], 'output_video_start_s': current['start_s'],
              'receipt_path': str(path), **details}
    write_json(path, record)
    return record


def _cached_mix(target, original, binding):
    """Resume only a complete, unchanged delivery under the same render policy."""
    receipt_path = target.with_suffix(target.suffix + '.receipt.json')
    if not target.exists() and not receipt_path.exists():
        return None
    if not target.is_file() or not receipt_path.is_file():
        raise ValueError('audio_render_existing_output_without_complete_receipt')
    try:
        saved = json.loads(receipt_path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ValueError('audio_render_existing_receipt_unreadable') from None
    if (saved.get('policy') != RENDER_POLICY or saved.get('operation') != 'mix_speech' or
            saved.get('input_binding') != binding or saved.get('input_sha256') != json_sha(binding)):
        raise ValueError('audio_render_existing_mix_binding_changed')
    if saved.get('path') != str(target) or saved.get('sha256') != sha256_file(target):
        raise ValueError('audio_render_existing_output_changed')
    current = _verify_video(target, original, stereo=True)
    if saved.get('probe') != current['probe'] or saved.get('fully_decoded') is not True:
        raise ValueError('audio_render_existing_output_probe_changed')
    stems = saved.get('audio_artifacts', {})
    expected_names = {'voice', 'music_ducked', 'mix_raw', 'mix'}
    if set(stems) != expected_names:
        raise ValueError('audio_render_existing_audio_artifacts_incomplete')
    for name in expected_names:
        path = target.parent / (target.stem + '_audio') / (name + '.wav')
        item = stems[name]
        if (item.get('path') != str(path) or not path.is_file() or
                item.get('sha256') != sha256_file(path) or item.get('probe') != _probe_audio(path)):
            raise ValueError('audio_render_existing_audio_artifact_changed:' + name)
    return dict(saved, cache_hit=True)


def mask_video(video, masks, output):
    """Fill normalized rectangles during [start_s,end_s), retaining original audio.

    masks contain x,y,w,h,start_s,end_s,color (#RRGGBB). Pixel bounds round outward
    to cover the requested area. No masks means a stream copy of the selected video.
    """
    source = Path(video).resolve(strict=True)
    info = _video_info(source)
    normalized, filters = [], []
    for item in masks:
        x, y, w, h, start, end = (_number(item[k], k) for k in
                                 ('x', 'y', 'w', 'h', 'start_s', 'end_s'))
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and
                x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9):
            raise ValueError('audio_render_mask_geometry_out_of_bounds')
        if not 0 <= start < end <= info['duration_s'] + 1e-9:
            raise ValueError('audio_render_mask_time_out_of_bounds')
        color = item.get('color', '#000000')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
            raise ValueError('audio_render_mask_color_must_be_hex')
        left, top = math.floor(x * info['width']), math.floor(y * info['height'])
        right = min(info['width'], math.ceil((x + w) * info['width']))
        bottom = min(info['height'], math.ceil((y + h) * info['height']))
        filters.append(f"drawbox=x={left}:y={top}:w={right-left}:h={bottom-top}:"
                       f"color=0x{color[1:]}:t=fill:enable='gte(t,{start:.9f})*lt(t,{end:.9f})'")
        normalized.append({'x': x, 'y': y, 'w': w, 'h': h, 'start_s': start, 'end_s': end,
                           'color': color, 'pixel_rectangle': [left, top, right-left, bottom-top]})
    target = _output_path(source, output)
    source_sha = sha256_file(source)
    # Anchor the first picture frame, not the earliest source audio packet. AAC
    # priming can precede picture PTS and otherwise shift the visible timeline.
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-copyts',
            '-itsoffset', f"{-info['start_s']:.9f}", '-i', str(source),
            '-map', f"0:{info['video_stream_index']}", '-map', '0:a?']
    if filters:
        pix_fmt = 'yuv420p' if not (info['width'] % 2 or info['height'] % 2) else 'yuv444p'
        args += ['-vf', ','.join(filters), '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                 '-pix_fmt', pix_fmt, '-fps_mode', 'passthrough']
    else:
        args += ['-c:v', 'copy']
    args += ['-c:a', 'copy', '-map_metadata', '-1', '-avoid_negative_ts', 'disabled', str(target)]
    _run(args)
    if sha256_file(source) != source_sha:
        raise ValueError('audio_render_source_changed')
    return _receipt(target, info, source, 'mask_video', masks=normalized)


def _samples(path):
    data = _run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                 '-vn', '-ac', '2', '-ar', str(SAMPLE_RATE), '-f', 'f32le', '-']).stdout
    values = np.frombuffer(data, dtype='<f4')
    if not len(values) or len(values) % 2 or not np.isfinite(values).all():
        raise ValueError('audio_render_invalid_decoded_audio')
    return values.reshape(-1, 2).copy()


def _wav(path, samples):
    # Float input is retained in the calculation; PCM avoids format-dependent timing.
    pcm = (np.clip(samples, -1, 1) * 32767).round().astype('<i2')
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm.tobytes())


def mix_speech(video, lines, output, *, music_path=None):
    """Replace source audio with bounded PCM speech and optional pure music.

    Each line requires id,audio_path,start_s,end_s; end_s is the containing window,
    not a crop point. Windows must not overlap. Music loops if needed and is ducked
    around actual speech intervals; source video sound is never mapped into output.
    Retained voice.wav/music_ducked.wav/mix.wav allow independent audio checks.
    An existing candidate is reused only with its complete, SHA-verified receipt
    and stems, exactly matching these inputs and RENDER_POLICY; it is not replaced.
    """
    source = Path(video).resolve(strict=True)
    info = _video_info(source)
    prepared, ids = [], set()
    for line in lines:
        line_id = line['id']
        if not isinstance(line_id, str) or not line_id or line_id in ids:
            raise ValueError('audio_render_line_id_missing_or_duplicate')
        ids.add(line_id)
        start, end = (_number(line[k], k) for k in ('start_s', 'end_s'))
        if not 0 <= start < end <= info['duration_s'] + 1e-9:
            raise ValueError('audio_render_speech_window_out_of_bounds:' + line_id)
        audio = Path(line['audio_path']).resolve(strict=True)
        probe = _probe_audio(audio)
        if probe['duration_s'] > end - start + 1e-9:
            raise AudioTimingError(line_id, probe['duration_s'], end - start)
        prepared.append({'id': line_id, 'audio_path': str(audio), 'audio_sha256': sha256_file(audio),
                         'start_s': start, 'end_s': end, 'duration_s': probe['duration_s'],
                         'actual_end_s': start + probe['duration_s'], 'probe': probe})
    prepared.sort(key=lambda line: line['start_s'])
    if any(a['end_s'] > b['start_s'] + 1e-9 for a, b in zip(prepared, prepared[1:])):
        raise ValueError('audio_render_speech_windows_overlap')
    music = Path(music_path).resolve(strict=True) if music_path is not None else None
    source_sha = sha256_file(source)
    music_sha = sha256_file(music) if music else None
    binding = {'policy': RENDER_POLICY, 'video_path': str(source), 'video_sha256': source_sha,
               'video_timeline': {k: info[k] for k in ('width', 'height', 'fps', 'start_s', 'duration_s', 'frame_count')},
               'lines': [dict(line) for line in prepared],
               'music_path': str(music) if music else None, 'music_sha256': music_sha}
    cached = _cached_mix(Path(output).resolve(), info, binding)
    if cached is not None:
        return cached
    target = _output_path(source, output, [p['audio_path'] for p in prepared] + ([music] if music else []))
    audio_dir = target.parent / (target.stem + '_audio')
    if audio_dir.exists():
        raise ValueError('audio_render_audio_artifact_directory_already_exists')
    count = round(info['duration_s'] * SAMPLE_RATE)
    voice = np.zeros((count, 2), dtype=np.float32)
    duck = np.ones(count, dtype=np.float32)
    for line in prepared:
        samples = _samples(line['audio_path'])
        if sha256_file(line['audio_path']) != line['audio_sha256']:
            raise ValueError('audio_render_speech_source_changed:' + line['id'])
        start = round(line['start_s'] * SAMPLE_RATE)
        stop = start + len(samples)
        if len(samples) / SAMPLE_RATE > line['end_s'] - line['start_s'] + 1 / SAMPLE_RATE:
            raise AudioTimingError(line['id'], len(samples) / SAMPLE_RATE, line['end_s'] - line['start_s'])
        if stop > count:
            raise AudioTimingError(line['id'], len(samples) / SAMPLE_RATE,
                                   info['duration_s'] - line['start_s'])
        voice[start:stop] += samples
        line.update(decoded_sample_count=len(samples), actual_start_sample=start, actual_end_sample=stop,
                    decoded_duration_s=len(samples) / SAMPLE_RATE)
        attack, release = max(0, start - round(.06 * SAMPLE_RATE)), min(count, stop + round(.15 * SAMPLE_RATE))
        if start > attack:
            duck[attack:start] = np.minimum(duck[attack:start], np.linspace(1, .18, start-attack))
        duck[start:stop] = np.minimum(duck[start:stop], .18)
        if release > stop:
            duck[stop:release] = np.minimum(duck[stop:release], np.linspace(.18, 1, release-stop))
    if music:
        music_samples = _samples(music)
        if sha256_file(music) != music_sha:
            raise ValueError('audio_render_music_source_changed')
        accompaniment = np.tile(music_samples, (math.ceil(count / len(music_samples)), 1))[:count].copy()
        accompaniment *= (.22 * duck)[:, None]
    else:
        accompaniment = np.zeros_like(voice)
    mixed = voice + accompaniment
    peak = float(np.max(np.abs(mixed))) if count else 0
    pre_gain = min(1.0, .98 / peak) if peak else 1.0
    audio_dir.mkdir()
    voice_path, music_out = audio_dir / 'voice.wav', audio_dir / 'music_ducked.wav'
    raw_path, mix_path = audio_dir / 'mix_raw.wav', audio_dir / 'mix.wav'
    _wav(voice_path, voice)
    _wav(music_out, accompaniment)
    _wav(raw_path, mixed * pre_gain)
    normalization = _run(['ffmpeg', '-nostdin', '-v', 'info', '-n', '-i', str(raw_path),
                          '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json',
                          '-ar', str(SAMPLE_RATE), '-ac', '2', '-c:a', 'pcm_s16le', str(mix_path)])
    loudnorm_json = re.search(r'\{\s*"input_i".*?\}', normalization.stderr.decode(errors='replace'), re.S)
    normalized_probe = _probe_audio(mix_path)
    if normalized_probe['frames'] != count:
        raise ValueError('audio_render_normalization_changed_sample_count')
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-copyts',
          '-itsoffset', f"{-info['start_s']:.9f}", '-i', str(source), '-i', str(mix_path),
          '-map', f"0:{info['video_stream_index']}", '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
          '-b:a', '192k', '-ar', str(SAMPLE_RATE), '-ac', '2', '-map_metadata', '-1',
          '-avoid_negative_ts', 'disabled', str(target)])
    if sha256_file(source) != source_sha:
        raise ValueError('audio_render_source_changed')
    return _receipt(target, info, source, 'mix_speech', lines=prepared, source_audio_removed=True,
                    input_binding=binding, input_sha256=json_sha(binding), cache_hit=False,
                    sample_rate=SAMPLE_RATE, channels=2, speech_truncated=False,
                    music_path=str(music) if music else None, music_sha256=music_sha,
                    music_gain=.22, music_speech_duck_gain=.18, duck_attack_s=.06, duck_release_s=.15,
                    pre_normalization_gain=pre_gain, loudnorm=json.loads(loudnorm_json.group()) if loudnorm_json else None,
                    audio_artifacts={p.stem: {'path': str(p), 'sha256': sha256_file(p), 'probe': _probe_audio(p)}
                                     for p in (voice_path, music_out, raw_path, mix_path)})
