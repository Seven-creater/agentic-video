import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import wave

import pytest

from omni_story.library import tts


class FakeTransport:
    def __init__(self, *, status=200, error=None, download_error=None, body=None):
        self.status, self.error, self.download_error = status, error, download_error
        self.body = body if body is not None else {
            'request_id': 'test-request', 'usage': {'characters': 6},
            'output': {'audio': {'id': 'audio-test', 'url':
                'http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/output.wav?Signature=secret-signature'}}}
        self.calls = 0
        self.downloads = 0

    def request(self, payload):
        self.calls += 1
        self.payload = payload
        if self.error:
            raise self.error
        return {'http_status': self.status, 'body_text': json.dumps(self.body)}

    def download(self, url, path):
        self.downloads += 1
        assert url.startswith('https://')
        if self.download_error:
            raise self.download_error
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\x00\x00' * 12000)


def call(tmp_path, client, **kwargs):
    return tts.synthesize_line('这份饭，踏实。', 'Ethan', tmp_path, line_id='line_01',
                               transport=client, **kwargs)


def state(tmp_path):
    return json.loads((tmp_path / 'line_01/state.json').read_text(encoding='utf-8'))


def test_received_wav_and_metadata_reuse_without_network_or_key(tmp_path, monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-api-secret')
    client = FakeTransport()
    result = call(tmp_path, client)
    assert result['status'] == 'received' and result['audio_verified']
    assert result['duration_s'] == .5 and result['probe']['frames'] == 12000
    assert result['usage'] == {'characters': 6} and result['request_id'] == 'test-request'
    assert not result['reused']
    monkeypatch.delenv('DASHSCOPE_API_KEY')
    reused = tts.synthesize_line('这份饭，踏实。', 'Ethan', tmp_path, line_id='line_01')
    assert reused['reused'] and reused['audio_sha256'] == result['audio_sha256']
    assert client.calls == client.downloads == 1
    serialized = (tmp_path / 'line_01/state.json').read_text(encoding='utf-8')
    assert 'fake-api-secret' not in serialized and 'secret-signature' not in serialized
    assert 'Signature=' not in serialized


@pytest.mark.parametrize('changed', [{'text': '这是另一句。'}, {'voice': 'Cherry'}])
def test_line_id_binds_text_and_voice(tmp_path, changed):
    client = FakeTransport()
    call(tmp_path, client)
    args = {'text': '这份饭，踏实。', 'voice': 'Ethan'} | changed
    with pytest.raises(tts.TTSStopped, match='binding_changed'):
        tts.synthesize_line(**args, output_dir=tmp_path, line_id='line_01', transport=client)
    assert client.calls == 1


@pytest.mark.parametrize('modify', ['overwrite', 'delete', 'probe', 'duration'])
def test_corrupted_received_artifact_stops_without_post(tmp_path, modify):
    client = FakeTransport()
    result = call(tmp_path, client)
    path = Path(result['audio_path'])
    if modify == 'overwrite':
        path.write_bytes(b'changed')
    elif modify == 'delete':
        path.unlink()
    else:
        saved = state(tmp_path)
        if modify == 'probe':
            saved['probe']['duration_s'] = 99
        else:
            saved['duration_s'] = 99
        tts.write_json(tmp_path / 'line_01/state.json', saved)
    with pytest.raises(tts.TTSStopped, match='mismatch_no_replay'):
        call(tmp_path, client)
    assert client.calls == 1


def test_unknown_transport_error_is_preserved_and_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-api-secret')
    client = FakeTransport(error=TimeoutError('fake-api-secret https://example.com/?Signature=secret'))
    with pytest.raises(tts.TTSUnknown):
        call(tmp_path, client)
    assert state(tmp_path)['status'] == 'uncertain'
    assert 'fake-api-secret' not in json.dumps(state(tmp_path))
    assert 'Signature=' not in json.dumps(state(tmp_path))
    with pytest.raises(tts.TTSUnknown, match='no_replay'):
        call(tmp_path, client)
    assert client.calls == 1


@pytest.mark.parametrize('status,error_type,stored_status', [
    (401, tts.TTSRejected, 'rejected'), (503, tts.TTSUnknown, 'uncertain')])
def test_provider_rejection_and_uncertainty_are_distinct(tmp_path, status, error_type, stored_status):
    client = FakeTransport(status=status, body={'request_id': 'error-id', 'code': 'error', 'message': 'failed'})
    with pytest.raises(error_type):
        call(tmp_path, client)
    assert state(tmp_path)['status'] == stored_status
    assert client.downloads == 0
    with pytest.raises(error_type):
        call(tmp_path, client)
    assert client.calls == 1


@pytest.mark.parametrize('problem', ['missing_url', 'download_failure', 'untrusted_url'])
def test_received_response_with_missing_artifact_preserves_usage_no_post_replay(tmp_path, problem):
    client = FakeTransport()
    if problem == 'missing_url':
        client.body['output']['audio'].pop('url')
    elif problem == 'untrusted_url':
        client.body['output']['audio']['url'] = 'https://untrusted.invalid/audio.wav'
    else:
        client.download_error = RuntimeError('https://example.com/?Signature=secret failed')
    with pytest.raises(tts.TTSStopped, match='artifact_unverified'):
        call(tmp_path, client)
    assert state(tmp_path)['status'] == 'received'
    assert state(tmp_path)['usage'] == {'characters': 6}
    assert not state(tmp_path)['audio_verified']
    with pytest.raises(tts.TTSStopped, match='no_replay'):
        call(tmp_path, client)
    assert client.calls == 1
    assert 'Signature=' not in json.dumps(state(tmp_path))


def test_write_before_send_and_nonblocking_lock_prevent_duplicate_post(tmp_path):
    class ConcurrentTransport(FakeTransport):
        def request(self, payload):
            assert state(tmp_path)['status'] == 'submitted'
            with pytest.raises(tts.LibraryStopped, match='locked'):
                call(tmp_path, self)
            return super().request(payload)
    client = ConcurrentTransport()
    call(tmp_path, client)
    assert client.calls == 1


@pytest.mark.parametrize('arguments', [
    {'text': ''}, {'text': '长' * 601}, {'voice': 'unverified'}, {'model': 'other-model'},
    {'line_id': '../unsafe'}, {'line_id': 'CON'}, {'timeout_s': float('inf')}])
def test_invalid_inputs_do_not_submit(tmp_path, arguments):
    client = FakeTransport()
    args = dict(text='你好', voice='Moon', output_dir=tmp_path, line_id='line_01', transport=client)
    args.update(arguments)
    with pytest.raises(ValueError):
        tts.synthesize_line(**args)
    assert client.calls == 0 and not list(tmp_path.iterdir())


def test_truncated_wav_rejected(tmp_path):
    client = FakeTransport()
    original_download = client.download
    def download(url, path):
        original_download(url, path)
        path.write_bytes(path.read_bytes()[:-100])
    client.download = download
    with pytest.raises(tts.TTSStopped, match='truncated_pcm_wav'):
        call(tmp_path, client)
    assert state(tmp_path)['status'] == 'received' and not state(tmp_path)['audio_verified']


def placeholder_transport():
    client = FakeTransport()
    original_download = client.download
    def download(url, path):
        original_download(url, path)
        data = bytearray(path.read_bytes())
        data[44:] = b'\x34\x12\xcc\xed' * 6000
        data[4:8] = struct.pack('<I', 0x7fffffbf)
        data[40:44] = struct.pack('<I', 0x7fffff9b)
        path.write_bytes(data)
    client.download = download
    return client


def test_qwen_placeholder_normalizes_lengths_with_raw_and_pcm_identity_receipt(tmp_path):
    client = placeholder_transport()
    result = call(tmp_path, client)
    assert result['duration_s'] == .5
    assert result['probe']['frames'] == result['probe']['header_frames'] == 12000
    assert not result['probe']['streaming_length_placeholder']
    raw_path, normalized_path = Path(result['source_audio_path']), Path(result['audio_path'])
    raw, normalized = raw_path.read_bytes(), normalized_path.read_bytes()
    assert raw_path.name == 'audio.wav' and normalized_path.name == 'audio.normalized.wav'
    assert struct.unpack('<I', raw[4:8])[0] == 0x7fffffbf
    assert struct.unpack('<I', raw[40:44])[0] == 0x7fffff9b
    assert struct.unpack('<I', normalized[4:8])[0] == len(normalized) - 8
    assert struct.unpack('<I', normalized[40:44])[0] == len(raw) - 44
    assert raw[:4] == normalized[:4] and raw[8:40] == normalized[8:40]
    assert raw[44:] == normalized[44:]
    receipt = result['audio_normalization']
    assert receipt['raw_download_path'] == str(raw_path)
    assert receipt['raw_source_sha256'] == result['source_audio_sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt['normalized_audio_path'] == str(normalized_path)
    assert receipt['normalized_sha256'] == result['audio_sha256'] == hashlib.sha256(normalized).hexdigest()
    assert receipt['actual_pcm_frames'] == 12000 and receipt['header_only']
    assert receipt['pcm_sha256_before'] == receipt['pcm_sha256_after'] == hashlib.sha256(raw[44:]).hexdigest()
    saved_state = (tmp_path / 'line_01/state.json').read_bytes()
    assert call(tmp_path, client)['reused'] and client.calls == 1
    assert raw_path.read_bytes() == raw and normalized_path.read_bytes() == normalized
    assert (tmp_path / 'line_01/state.json').read_bytes() == saved_state


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='Requires FFmpeg')
def test_normalized_qwen_wav_passes_real_ffmpeg_strict_decode_raw_failure_preserved(tmp_path):
    result = call(tmp_path, placeholder_transport())
    def decode(path):
        return subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-xerror',
                               '-i', str(path), '-f', 'null', '-'], capture_output=True)
    raw_path, normalized_path = Path(result['source_audio_path']), Path(result['audio_path'])
    raw_bytes = raw_path.read_bytes()
    assert decode(raw_path).returncode != 0
    decoded = decode(normalized_path)
    assert decoded.returncode == 0, decoded.stderr.decode(errors='replace')
    assert raw_path.read_bytes() == raw_bytes


def test_already_valid_wav_stays_byte_identical_without_normalized_asset(tmp_path):
    result = call(tmp_path, FakeTransport())
    path = Path(result['audio_path'])
    before = path.read_bytes()
    assert result['source_audio_path'] == str(path)
    assert 'audio_normalization' not in result
    assert not (path.parent / 'audio.normalized.wav').exists()
    target = tmp_path / 'separate_cache/normalized.wav'
    assert tts.normalize_received_wav(path, target) is None
    assert path.read_bytes() == before and not target.exists()


def test_original_received_placeholder_cache_is_reused_without_state_or_asset_changes(tmp_path):
    client = FakeTransport()
    result = call(tmp_path, client)
    path = Path(result['audio_path'])
    raw = bytearray(path.read_bytes())
    raw[4:8] = struct.pack('<I', 0x7fffffbf)
    raw[40:44] = struct.pack('<I', 0x7fffff9b)
    path.write_bytes(raw)
    original = state(tmp_path)
    original.pop('source_audio_sha256')  # Metadata written before header normalization existed.
    original.update(audio_sha256=hashlib.sha256(raw).hexdigest(), probe=tts._probe_audio(path))
    tts.write_json(tmp_path / 'line_01/state.json', original)
    before_state = (tmp_path / 'line_01/state.json').read_bytes()
    reused = call(tmp_path, client)
    assert reused['reused'] and reused['audio_path'] == str(path)
    assert reused['probe']['streaming_length_placeholder']
    assert path.read_bytes() == raw
    assert (tmp_path / 'line_01/state.json').read_bytes() == before_state
    assert not (path.parent / 'audio.normalized.wav').exists()
    assert client.calls == client.downloads == 1
    # A caller may explicitly normalize this received source into a new cache,
    # while the original cache and its raw source remain immutable.
    receipt = tts.normalize_received_wav(path, tmp_path / 'new_cache/audio.wav')
    assert receipt['raw_source_sha256'] == original['audio_sha256']
    assert path.read_bytes() == raw
    assert (tmp_path / 'line_01/state.json').read_bytes() == before_state


@pytest.mark.parametrize('modify', ['raw', 'receipt', 'normalized'])
def test_normalized_received_source_or_receipt_tampering_stops_without_replay(tmp_path, modify):
    client = placeholder_transport()
    result = call(tmp_path, client)
    if modify == 'raw':
        path = Path(result['source_audio_path'])
        path.write_bytes(path.read_bytes()[:-2])
    elif modify == 'normalized':
        path = Path(result['audio_path'])
        path.write_bytes(path.read_bytes()[:-2])
    else:
        saved = state(tmp_path)
        saved['audio_normalization']['pcm_sha256_after'] = 'changed'
        tts.write_json(tmp_path / 'line_01/state.json', saved)
    with pytest.raises(tts.TTSStopped, match='mismatch_no_replay'):
        call(tmp_path, client)
    assert client.calls == client.downloads == 1


@pytest.mark.parametrize('problem', ['partial_pcm_frame', 'unrecognized_placeholder'])
def test_incomplete_or_unknown_placeholder_is_unverified_without_repair_or_replay(tmp_path, problem):
    client = placeholder_transport()
    original_download = client.download
    def download(url, path):
        original_download(url, path)
        raw = bytearray(path.read_bytes())
        if problem == 'partial_pcm_frame':
            raw = raw[:-1]
        else:
            raw[40:44] = struct.pack('<I', 0x7fffff99)
        path.write_bytes(raw)
    client.download = download
    with pytest.raises(tts.TTSStopped, match='truncated_pcm_wav'):
        call(tmp_path, client)
    saved = state(tmp_path)
    path = tmp_path / 'line_01/audio.wav'
    assert saved['status'] == 'received' and not saved['audio_verified']
    assert saved['usage'] == {'characters': 6}
    assert saved['source_audio_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not (path.parent / 'audio.normalized.wav').exists()
    assert 'audio_normalization' not in saved
    with pytest.raises(tts.TTSStopped, match='no_replay'):
        call(tmp_path, client)
    assert client.calls == client.downloads == 1


def test_normalization_never_overwrites_source_or_existing_target(tmp_path):
    client = placeholder_transport()
    source = tmp_path / 'source.wav'
    client.download('https://example.invalid', source)
    raw = source.read_bytes()
    with pytest.raises(ValueError, match='target_exists_no_reset'):
        tts.normalize_received_wav(source, source)
    target = tmp_path / 'existing.wav'
    target.write_bytes(b'preserved')
    with pytest.raises(ValueError, match='target_exists_no_reset'):
        tts.normalize_received_wav(source, target)
    assert source.read_bytes() == raw and target.read_bytes() == b'preserved'


@pytest.mark.parametrize('offset,value', [(28, 1), (32, 1)])
def test_placeholder_with_invalid_pcm_format_fields_is_not_normalized(tmp_path, offset, value):
    client = placeholder_transport()
    source = tmp_path / 'source.wav'
    client.download('https://example.invalid', source)
    raw = bytearray(source.read_bytes())
    raw[offset:offset + (4 if offset == 28 else 2)] = struct.pack('<I' if offset == 28 else '<H', value)
    source.write_bytes(raw)
    target = tmp_path / 'normalized.wav'
    with pytest.raises(ValueError, match='invalid_pcm_wav'):
        tts.normalize_received_wav(source, target)
    assert not target.exists() and source.read_bytes() == raw


@pytest.mark.parametrize('status,error_type', [(401, tts.TTSRejected), (200, tts.TTSUnknown),
                                            (503, tts.TTSUnknown)])
def test_non_json_http_errors_preserve_known_vs_unknown_status(tmp_path, status, error_type):
    class HtmlTransport(FakeTransport):
        def request(self, payload):
            self.calls += 1
            return {'http_status': status, 'body_text': '<html>gateway error</html>'}
    client = HtmlTransport()
    with pytest.raises(error_type):
        call(tmp_path, client)
    with pytest.raises(error_type):
        call(tmp_path, client)
    assert client.calls == 1 and state(tmp_path)['http_status'] == status


def test_5xx_with_conflicting_client_error_body_remains_uncertain(tmp_path):
    client = FakeTransport(status=503, body={'status_code': 401, 'code': 'conflicting_status'})
    with pytest.raises(tts.TTSUnknown):
        call(tmp_path, client)
    assert state(tmp_path)['status'] == 'uncertain'


def test_received_success_with_malformed_usage_can_still_validate_audio(tmp_path):
    client = FakeTransport()
    client.body['usage'] = ['malformed-but-not-an-audio-failure']
    result = call(tmp_path, client)
    assert result['audio_verified'] and result['usage'] == {}


def test_mock_transport_credentials_and_bearer_errors_are_redacted(tmp_path, monkeypatch):
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    client = FakeTransport(error=RuntimeError('credential private-test-key; Bearer other-secret'))
    client.key = 'private-test-key'
    with pytest.raises(tts.TTSUnknown):
        call(tmp_path, client)
    saved = json.dumps(state(tmp_path))
    assert 'private-test-key' not in saved and 'other-secret' not in saved
