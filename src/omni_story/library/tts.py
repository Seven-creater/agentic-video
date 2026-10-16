"""One paid Qwen TTS request per bound line; received WAVs are reusable by SHA.

API/voices: https://help.aliyun.com/zh/model-studio/qwen-tts-api
https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
Only the model and four voices already exercised by this project are enabled.
Durations come from decoded PCM frame counts; callers decide whether lines fit.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
import wave

from ..api import curl_json
from .state import LibraryStopped, file_lock, json_sha, write_json

MODEL = 'qwen3-tts-flash'
VERIFIED_VOICES = frozenset({'Ethan', 'Cherry', 'Serena', 'Moon'})
ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'


class TTSStopped(LibraryStopped):
    """A bound line cannot be submitted/replayed or its artifact is unverified."""


class TTSRejected(TTSStopped):
    """The provider explicitly rejected this request. No automatic retry."""


class TTSUnknown(TTSStopped):
    """Submission outcome is unknown. No automatic retry."""


def _safe_error(value, secrets=()):
    text = str(value)
    for key in (os.environ.get('DASHSCOPE_API_KEY', ''), *secrets):
        if key:
            text = text.replace(str(key), '[REDACTED]')
    text = re.sub(r'Bearer\s+[^\s"\'<>]+', 'Bearer [REDACTED]', text, flags=re.I)
    return re.sub(r'https?://[^\s"\'<>]+', '[URL_REDACTED]', text)[:1000]


def _download_url(url):
    if not isinstance(url, str) or any(ord(c) < 32 for c in url):
        raise ValueError('tts_invalid_audio_url')
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or
            parsed.port not in {None, 80, 443} or
            not (parsed.hostname or '').endswith('.aliyuncs.com')):
        raise ValueError('tts_untrusted_audio_download_host')
    return urlunsplit(('https', parsed.hostname, parsed.path, parsed.query, ''))


def _probe_audio(path):
    """Count actual PCM frames; Qwen outputs have a known streaming length header.

    The original 11 received Qwen WAVs use RIFF/data lengths 0x7fffffbf/0x7fffff9b.
    These are placeholders, not the duration. Ordinary WAV lengths remain strict.
    Curl must finish downloading before this is called; no headers are rewritten.
    """
    with wave.open(str(path), 'rb') as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        channels, width = audio.getnchannels(), audio.getsampwidth()
        if audio.getcomptype() != 'NONE' or not frames or not rate:
            raise ValueError('tts_invalid_pcm_wav')
        read_bytes = 0
        while chunk := audio.readframes(65536):
            read_bytes += len(chunk)
        placeholder = False
        if read_bytes != frames * channels * width:
            with Path(path).open('rb') as handle:
                header = handle.read(44)
            placeholder = (len(header) == 44 and header[:4] == b'RIFF' and
                           header[36:40] == b'data' and
                           struct.unpack('<I', header[4:8])[0] == 0x7fffffbf and
                           struct.unpack('<I', header[40:44])[0] == 0x7fffff9b)
            if not placeholder or not read_bytes or read_bytes % (channels * width):
                raise ValueError('tts_truncated_pcm_wav')
        actual_frames = read_bytes // (channels * width)
    return {'format_name': 'wav', 'codec_name': 'pcm', 'sample_rate': rate,
            'channels': channels, 'sample_width_bytes': width, 'frames': actual_frames,
            'header_frames': frames, 'streaming_length_placeholder': placeholder,
            'duration_s': actual_frames / rate}


def normalize_received_wav(source, target):
    """Repair only known Qwen length placeholders in a separate WAV.

    The completed download stays untouched. Return a byte/PCM receipt when a
    sibling was created, or None for an ordinary valid WAV. No target is reused
    or overwritten, and incomplete PCM cannot be repaired by changing a header.
    """
    source = Path(source).resolve(strict=True)
    probe = _probe_audio(source)
    if not probe['streaming_length_placeholder']:
        return None
    target = Path(target).resolve()
    if target == source or target.exists():
        raise ValueError('tts_normalization_target_exists_no_reset')
    raw = source.read_bytes()
    frame_bytes = probe['channels'] * probe['sample_width_bytes']
    if (raw[:4] != b'RIFF' or raw[8:16] != b'WAVEfmt ' or raw[36:40] != b'data' or
            struct.unpack('<I', raw[4:8])[0] != 0x7fffffbf or
            struct.unpack('<I', raw[40:44])[0] != 0x7fffff9b or
            struct.unpack('<IHHIIHH', raw[16:36]) !=
            (16, 1, probe['channels'], probe['sample_rate'],
             probe['sample_rate'] * frame_bytes, frame_bytes, probe['sample_width_bytes'] * 8)):
        raise ValueError('tts_invalid_pcm_wav')
    pcm = raw[44:]
    if len(pcm) != probe['frames'] * frame_bytes:
        raise ValueError('tts_truncated_pcm_wav')
    riff_length = len(raw) - 8 + len(pcm) % 2
    if riff_length > 0xffffffff:
        raise ValueError('tts_pcm_wav_too_large')
    normalized = bytearray(raw)
    normalized[4:8] = struct.pack('<I', riff_length)
    normalized[40:44] = struct.pack('<I', len(pcm))
    if len(pcm) % 2:
        normalized.append(0)  # RIFF padding is outside the unchanged PCM data.
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as handle:
        handle.write(normalized)
    after = target.read_bytes()
    normalized_probe = _probe_audio(target)
    pcm_before = hashlib.sha256(pcm).hexdigest()
    pcm_after = hashlib.sha256(after[44:44 + len(pcm)]).hexdigest()
    if (after != normalized or pcm_before != pcm_after or
            normalized_probe != dict(probe, header_frames=probe['frames'],
                                     streaming_length_placeholder=False)):
        raise ValueError('tts_normalization_changed_pcm')
    return {'schema_version': 'tts_wav_normalization_v1', 'header_only': True,
            'raw_download_path': str(source),
            'raw_source_sha256': hashlib.sha256(raw).hexdigest(),
            'normalized_audio_path': str(target),
            'normalized_sha256': hashlib.sha256(after).hexdigest(),
            'actual_pcm_frames': probe['frames'],
            'pcm_sha256_before': pcm_before, 'pcm_sha256_after': pcm_after}


class QwenTTS:
    """Direct native curl, with credentials and signed URLs passed only on stdin.

    Tests may pass an object exposing request(payload) and download(url, path).
    Neither operation retries. API credentials are read only from the environment.
    """

    def __init__(self, *, timeout_s=120):
        self.key = os.environ.get('DASHSCOPE_API_KEY', '')
        self.curl = shutil.which('curl.exe' if sys.platform == 'win32' else 'curl')
        if not self.key or not self.curl:
            raise ValueError('DASHSCOPE_API_KEY_missing' if not self.key else 'curl_missing')
        self.timeout_s = timeout_s

    def request(self, payload):
        return curl_json(self.curl, ENDPOINT, self.key, 'POST', payload,
                         timeout=self.timeout_s, noproxy='*')

    def download(self, url, path):
        url = _download_url(url)
        quoted = url.replace('\\', '\\\\').replace('"', '\\"')
        args = [self.curl, '--noproxy', '*', '--silent', '--show-error', '--fail',
                '--max-time', str(self.timeout_s), '-K', '-', '-o', str(path)]
        if sys.platform == 'win32':
            args.insert(1, '--ssl-revoke-best-effort')
        result = subprocess.run(args, input=('url = "' + quoted + '"\n').encode(),
                                capture_output=True, timeout=self.timeout_s + 5)
        if result.returncode:
            raise RuntimeError('tts_audio_download_failed:' + str(result.returncode))


def synthesize_line(text, voice, output_dir, *, line_id, model=MODEL,
                    transport=None, timeout_s=120):
    """Return a verified WAV record, or stop without replaying a paid request.

    Files live in output_dir/line_id/{state.json,audio.wav}; a known streaming
    header gets a separate audio.normalized.wav plus a PCM identity receipt.
    A matching received result is reused only after verifying its SHA and PCM
    metadata. All other persisted states stop; changing text/voice/model under
    the same ID also stops.
    Keys and temporary signed audio URLs are never included in persisted records.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 600:
        raise ValueError('tts_text_must_contain_1_to_600_characters')
    if model != MODEL or voice not in VERIFIED_VOICES:
        raise ValueError('tts_model_or_voice_not_verified')
    if (not isinstance(line_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', line_id) or
            line_id.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                               *(f'LPT{i}' for i in range(1, 10))}):
        raise ValueError('tts_unsafe_line_id')
    if type(timeout_s) not in {int, float} or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError('tts_timeout_must_be_positive')
    payload = {'model': model, 'input': {'text': text, 'voice': voice, 'language_type': 'Chinese'}}
    binding = {'line_id': line_id, 'endpoint': ENDPOINT, 'payload': payload}
    digest = json_sha(binding)
    folder = Path(output_dir).resolve() / line_id
    folder.mkdir(parents=True, exist_ok=True)
    state_path, audio_path = folder / 'state.json', folder / 'audio.wav'
    with file_lock(folder / '.lock'):
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                raise TTSStopped('tts_existing_state_unreadable_no_reset') from None
            if (state.get('request_sha256') != digest or state.get('binding') != binding or
                    any(state.get(k) != v for k, v in
                        {'line_id': line_id, 'text': text, 'voice': voice, 'model': model}.items())):
                raise TTSStopped('tts_line_binding_changed')
            if state.get('status') == 'rejected':
                raise TTSRejected('tts_request_already_rejected_no_replay')
            if state.get('status') in {'submitted', 'uncertain'}:
                raise TTSUnknown('tts_request_outcome_unknown_no_replay')
            if state.get('status') != 'received' or state.get('audio_verified') is not True:
                raise TTSStopped('tts_received_artifact_unverified_no_replay')
            normalization = state.get('audio_normalization')
            if normalization is not None:
                raw = audio_path.read_bytes() if audio_path.is_file() else b''
                raw_sha = hashlib.sha256(raw).hexdigest()
                normalized_path = folder / 'audio.normalized.wav'
                normalized = normalized_path.read_bytes() if normalized_path.is_file() else b''
                if (not isinstance(normalization, dict) or not raw or
                        normalization.get('schema_version') != 'tts_wav_normalization_v1' or
                        normalization.get('header_only') is not True or
                        state.get('source_audio_path') != str(audio_path) or
                        state.get('source_audio_sha256') != raw_sha or
                        normalization.get('raw_download_path') != str(audio_path) or
                        normalization.get('raw_source_sha256') != raw_sha or
                        normalization.get('normalized_audio_path') != str(normalized_path) or
                        normalization.get('normalized_sha256') != state.get('audio_sha256') or
                        normalization.get('actual_pcm_frames') != state.get('probe', {}).get('frames') or
                        normalization.get('pcm_sha256_before') != hashlib.sha256(raw[44:]).hexdigest() or
                        normalization.get('pcm_sha256_after') != hashlib.sha256(normalized[44:len(raw)]).hexdigest() or
                        normalization.get('pcm_sha256_after') != normalization.get('pcm_sha256_before')):
                    raise TTSStopped('tts_received_normalization_mismatch_no_replay')
                audio_path = normalized_path
            if (not audio_path.is_file() or state.get('audio_path') != str(audio_path) or
                    hashlib.sha256(audio_path.read_bytes()).hexdigest() != state.get('audio_sha256')):
                raise TTSStopped('tts_received_audio_sha_mismatch_no_replay')
            probe = _probe_audio(audio_path)
            if probe != state.get('probe') or probe['duration_s'] != state.get('duration_s'):
                raise TTSStopped('tts_received_audio_probe_mismatch_no_replay')
            return dict(state, reused=True)
        if audio_path.exists() or (folder / 'audio.normalized.wav').exists():
            raise TTSStopped('tts_audio_without_original_state_no_reset')
        # Local configuration errors occur before the write-before-send record.
        client = transport if transport is not None else QwenTTS(timeout_s=timeout_s)
        secrets = tuple(value for value in (getattr(client, 'key', None), getattr(client, 'token', None)) if value)
        from .resource_guard import before_request
        before_request()
        state = {'schema_version': 'tts_line_v1', 'line_id': line_id, 'text': text,
                 'voice': voice, 'model': model, 'binding': binding, 'request_sha256': digest,
                 'status': 'submitted', 'submitted_at': datetime.now(timezone.utc).isoformat(),
                 'request_count': 1, 'usage': {}, 'audio_verified': False}
        write_json(state_path, state)
        try:
            response = client.request(payload)
            http_status = response['http_status']
            if type(http_status) is not int or not 100 <= http_status <= 599:
                raise ValueError('tts_invalid_http_status')
            state['http_status'] = http_status
        except Exception as exc:
            state.update(status='uncertain', error=_safe_error(exc, secrets), error_type=type(exc).__name__)
            write_json(state_path, state)
            raise TTSUnknown('tts_request_outcome_unknown:' + state['error']) from None
        try:
            body = json.loads(response['body_text'])
            if not isinstance(body, dict):
                raise ValueError('tts_response_not_object')
        except (ValueError, KeyError, TypeError):
            # An explicit HTTP client rejection remains known even when an edge
            # gateway returns HTML. HTTP 200/5xx without a reply remain unknown.
            rejected = 400 <= http_status < 500
            state.update(status='rejected' if rejected else 'uncertain', code='tts_response_json_invalid')
            write_json(state_path, state)
            error = TTSRejected if rejected else TTSUnknown
            raise error('tts_provider_' + state['status'] + ':tts_response_json_invalid') from None
        state['request_id'] = _safe_error(body.get('request_id', ''), secrets)
        usage = body.get('usage')
        if isinstance(usage, dict):
            state['usage'] = {k: v for k, v in usage.items()
                              if k in {'characters', 'input_tokens', 'output_tokens'} and
                              type(v) in {int, float} and math.isfinite(v)}
        effective_status = body.get('status_code', http_status)
        if http_status != 200 or effective_status != 200:
            # 5xx can follow server-side processing; never classify it as no cost.
            rejected = (http_status < 500 and type(effective_status) is int and
                        400 <= effective_status < 500)
            state.update(status='rejected' if rejected else 'uncertain',
                         code=_safe_error(body.get('code', ''), secrets),
                         message=_safe_error(body.get('message', ''), secrets))
            write_json(state_path, state)
            error = TTSRejected if rejected else TTSUnknown
            raise error('tts_provider_' + state['status'] + ':' + state['code'])
        # Preserve received usage even if downloading/validating the output fails.
        state.update(status='received', received_at=datetime.now(timezone.utc).isoformat())
        write_json(state_path, state)
        try:
            audio = body.get('output', {}).get('audio', {})
            state['audio_id'] = _safe_error(audio.get('id', ''), secrets)
            url = _download_url(audio.get('url'))
            client.download(url, audio_path)
            state.update(source_audio_path=str(audio_path),
                         source_audio_sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest())
            write_json(state_path, state)
            normalization = normalize_received_wav(audio_path, folder / 'audio.normalized.wav')
            if normalization is not None:
                if normalization['raw_source_sha256'] != state['source_audio_sha256']:
                    raise ValueError('tts_downloaded_source_changed')
                state['audio_normalization'] = normalization
                audio_path = Path(normalization['normalized_audio_path'])
            probe = _probe_audio(audio_path)
            state.update(audio_verified=True, audio_path=str(audio_path),
                         audio_sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                         probe=probe, duration_s=probe['duration_s'])
        except Exception as exc:
            state.update(error=_safe_error(exc, secrets), error_type=type(exc).__name__)
            write_json(state_path, state)
            raise TTSStopped('tts_received_artifact_unverified:' + state['error']) from None
        write_json(state_path, state)
        return dict(state, reused=False)
