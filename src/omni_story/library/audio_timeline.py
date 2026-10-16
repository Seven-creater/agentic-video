"""Media-derived voice-energy timing for a blind audiovisual review.

Only a completed, source-bound render receipt and its retained PCM stems are
accepted. Authored line times, identifiers and text never determine the bands.
"""
from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import struct
import wave

import numpy as np

from .audio_render import RENDER_POLICY
from .media import probe_media, sha256_file
from .state import json_sha

POLICY = 'actual_pcm_voice_bands_v1'
SAMPLE_RATE = 48000
WINDOW_S = .01
ENERGY_THRESHOLD = .0015
JOIN_GAP_S = .4


def _fail(reason):
    raise ValueError('audio_timeline_' + reason)


def _bound_file(path, digest, name):
    if not isinstance(path, str) or not Path(path).is_absolute():
        _fail(name + '_path_invalid')
    try:
        current = Path(path).resolve(strict=True)
        if not current.is_file() or sha256_file(current) != digest:
            _fail(name + '_changed')
    except OSError:
        _fail(name + '_missing')
    return current


def _pcm(path):
    """Require a complete ordinary RIFF WAV, including all declared chunks."""
    raw = path.read_bytes()
    if (len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE' or
            struct.unpack('<I', raw[4:8])[0] + 8 != len(raw)):
        _fail('stem_wav_incomplete')
    offset, counts, data_size = 12, {}, None
    while offset < len(raw):
        if offset + 8 > len(raw):
            _fail('stem_wav_incomplete')
        name, size = raw[offset:offset + 4], struct.unpack('<I', raw[offset + 4:offset + 8])[0]
        offset += 8 + size + size % 2
        if offset > len(raw):
            _fail('stem_wav_incomplete')
        counts[name] = counts.get(name, 0) + 1
        if name == b'data':
            data_size = size
    if counts.get(b'fmt ') != 1 or counts.get(b'data') != 1:
        _fail('stem_wav_chunks_invalid')
    try:
        with wave.open(str(path), 'rb') as audio:
            frames = audio.getnframes()
            if (audio.getcomptype() != 'NONE' or audio.getframerate() != SAMPLE_RATE or
                    audio.getnchannels() != 2 or audio.getsampwidth() != 2 or frames <= 0):
                _fail('stem_must_be_48k_stereo_pcm16')
            data = audio.readframes(frames)
    except (wave.Error, EOFError):
        _fail('stem_wav_invalid')
    if len(data) != frames * 4 or data_size != frames * 4:
        _fail('stem_wav_incomplete')
    probe = {'format_name': 'wav', 'codec_name': 'pcm', 'sample_rate': SAMPLE_RATE,
             'channels': 2, 'sample_width_bytes': 2, 'frames': frames,
             'header_frames': frames, 'streaming_length_placeholder': False,
             'duration_s': frames / SAMPLE_RATE}
    return np.frombuffer(data, dtype='<i2').reshape(-1, 2), probe


def _energy_bands(pcm):
    step, join = round(WINDOW_S * SAMPLE_RATE), round(JOIN_GAP_S * SAMPLE_RATE)
    ranges = []
    for start in range(0, len(pcm), step):
        stop = min(start + step, len(pcm))
        # Convert before abs/squaring: signed PCM -32768 must not overflow.
        values = pcm[start:stop].astype(np.float64) / 32768
        peak = float(np.max(np.abs(values)))
        rms = float(np.max(np.sqrt(np.mean(values ** 2, axis=0))))
        if peak <= ENERGY_THRESHOLD or rms <= ENERGY_THRESHOLD:
            continue
        if ranges and start - ranges[-1][1] <= join:
            ranges[-1][1] = stop
        else:
            ranges.append([start, stop])
    return [{'id': f'voice_band_{index:04d}', 'start_s': start / SAMPLE_RATE,
             'end_s': stop / SAMPLE_RATE}
            for index, (start, stop) in enumerate(ranges, 1)]


def actual_voice_bands(candidate_path) -> dict:
    """Anchor actual voice activity at candidate time zero, in 10 ms bins.

    Speech-internal energy gaps of at most 400 ms are joined. This is an energy
    observation of the render's isolated voice stem, not ASR or a lip-sync test.
    No transcript, script, plan or authored line boundary is accepted as input.
    """
    try:
        candidate = Path(candidate_path).resolve(strict=True)
        receipt_path = candidate.with_suffix(candidate.suffix + '.receipt.json')
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        _fail('receipt_unreadable')
    if not isinstance(receipt, dict):
        _fail('receipt_invalid')
    if (receipt.get('policy') != RENDER_POLICY or receipt.get('operation') != 'mix_speech' or
            receipt.get('fully_decoded') is not True or receipt.get('source_audio_removed') is not True or
            receipt.get('speech_truncated') is not False):
        _fail('receipt_render_contract_invalid')
    bound = []
    output = _bound_file(receipt.get('path'), receipt.get('sha256'), 'candidate')
    if output != candidate:
        _fail('candidate_path_mismatch')
    bound.append((output, receipt['sha256']))
    probe = probe_media(candidate)
    if probe != receipt.get('probe'):
        _fail('candidate_probe_changed')
    video = next(row for row in probe['streams'] if row['index'] == probe['video_stream_index'])
    audio = [row for row in probe['streams'] if row.get('codec_type') == 'audio']
    duration = (float(int(video['duration_ts']) * Fraction(video['time_base']))
                if video.get('duration_ts') is not None and video.get('time_base')
                else float(video.get('duration') or probe['duration_s']))
    if (not math.isfinite(duration) or duration <= 0 or receipt.get('duration_s') != duration or
            receipt.get('output_video_start_s') != 0 or abs(float(video.get('start_time', 0))) > .0001 or
            len(audio) != 1 or int(audio[0].get('sample_rate', 0)) != SAMPLE_RATE or
            audio[0].get('channels') != 2 or abs(float(audio[0].get('start_time', 0))) > 1 / SAMPLE_RATE or
            abs(float(audio[0].get('duration', 0)) - duration) > .001):
        _fail('candidate_timeline_invalid')
    binding = receipt.get('input_binding')
    if (not isinstance(binding, dict) or binding.get('policy') != RENDER_POLICY or
            receipt.get('input_sha256') != json_sha(binding) or
            binding.get('video_path') != receipt.get('source_video_path') or
            binding.get('video_sha256') != receipt.get('source_video_sha256')):
        _fail('source_binding_invalid')
    source = _bound_file(binding.get('video_path'), binding.get('video_sha256'), 'source_video')
    bound.append((source, binding['video_sha256']))
    lines = binding.get('lines')
    if not isinstance(lines, list) or not lines:
        _fail('voice_source_binding_missing')
    for line in lines:
        if not isinstance(line, dict):
            _fail('voice_source_binding_invalid')
        source = _bound_file(line.get('audio_path'), line.get('audio_sha256'), 'voice_source')
        bound.append((source, line['audio_sha256']))
    if binding.get('music_path') is not None:
        source = _bound_file(binding['music_path'], binding.get('music_sha256'), 'music_source')
        bound.append((source, binding['music_sha256']))
    artifacts = receipt.get('audio_artifacts')
    if not isinstance(artifacts, dict) or set(artifacts) != {'voice', 'music_ducked', 'mix_raw', 'mix'}:
        _fail('stems_incomplete')
    voice_pcm, voice_path, voice_sha = None, None, None
    for name, item in artifacts.items():
        if not isinstance(item, dict):
            _fail('stem_receipt_invalid')
        stem = _bound_file(item.get('path'), item.get('sha256'), 'stem_' + name)
        if stem != candidate.parent / (candidate.stem + '_audio') / (name + '.wav'):
            _fail('stem_path_mismatch')
        pcm, measured = _pcm(stem)
        if measured != item.get('probe') or measured['frames'] != round(duration * SAMPLE_RATE):
            _fail('stem_probe_or_duration_changed')
        bound.append((stem, item['sha256']))
        if name == 'voice':
            voice_pcm, voice_path, voice_sha = pcm, stem, item['sha256']
    bands = _energy_bands(voice_pcm)
    if not bands:
        _fail('actual_voice_energy_empty')
    if any(sha256_file(path) != digest for path, digest in bound):
        _fail('bound_file_changed_during_observation')
    return {'policy': POLICY, 'duration_s': len(voice_pcm) / SAMPLE_RATE,
            'candidate_sha256': receipt['sha256'], 'voice_stem_path': str(voice_path),
            'voice_stem_sha256': voice_sha, 'bands': bands,
            'scope_notes': [
                'Times are actual isolated voice PCM samples relative to candidate time zero; '
                '10 ms bins require peak and per-channel RMS above 0.0015 full scale.',
                'Energy gaps at most 0.4 s are joined; leading/trailing silence and longer gaps remain outside bands.',
                'These bands cannot establish speech text, voice quality, visual match or exact phoneme lip-sync. '
                'The independent reviewer must still hear and see the actual candidate media.',
            ]}
