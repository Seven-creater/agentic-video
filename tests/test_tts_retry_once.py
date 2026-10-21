import json
from pathlib import Path
import wave

import pytest

from omni_story.library import tts
from omni_story.library.audio_usage import _service_usage, usage_bindings
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json
from omni_story.library.tts_retry import POLICY, retry_once, validate_replacement


class Transport:
    def __init__(self, error=None):
        self.calls, self.error = 0, error

    def request(self, payload):
        self.calls += 1
        if self.error:
            raise self.error
        return {'http_status': 200, 'body_text': json.dumps({'request_id': 'new-actual-request',
            'usage': {'characters': 3}, 'output': {'audio': {
                'url': 'https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav', 'id': 'actual-audio'}}})}

    def download(self, url, path):
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(b'\0\0' * 12000)


def original(tmp_path):
    client = Transport(TimeoutError('timeout'))
    with pytest.raises(tts.TTSUnknown):
        tts.synthesize_line('先吃饭。', 'Ethan', tmp_path, line_id='utt_test', transport=client)
    path = tmp_path / 'utt_test/state.json'
    authorization = tmp_path / 'authorization.json'
    write_json(authorization, dict(policy=POLICY, original_ledger_path=str(path),
        original_ledger_sha256=sha256_file(path), max_additional_requests=1,
        allow_possible_duplicate_charge=True, user_answer='允许这一句重调一次'))
    return path, authorization


def test_one_real_replacement_preserves_old_bytes_and_reuses_without_post(tmp_path):
    path, authorization = original(tmp_path)
    old = path.read_bytes()
    client = Transport()
    result = retry_once(path, authorization, transport=client)
    assert client.calls == 1 and result['request_count'] == 2
    assert result['request_id'] == 'new-actual-request' and result['audio_verified']
    assert Path(result['authorized_retry_once']['original']['path']).read_bytes() == old
    validate_replacement(path)
    reused = tts.synthesize_line('先吃饭。', 'Ethan', tmp_path, line_id='utt_test', transport=client)
    assert reused['reused'] and client.calls == 1
    usage = _service_usage([path])
    assert usage['requests'] == 2 and usage['captured_usage'] == {'characters': 3}
    assert usage['prior_authorized_uncertain_attempts'][0]['status'] == 'uncertain'
    assert usage['prior_authorized_uncertain_attempts'][0]['possible_duplicate_charge_accepted']
    from omni_story.evaluation.collect import operation_usage
    operations = operation_usage(tmp_path, [{'scope': 'generation'}] * 2)
    assert operations['tts'] == operations['total'] == operations['reserved'] == 2
    bindings = usage_bindings({'audio_services': {'tts': usage}})
    for kind in ('original', 'authorization', 'attempt'):
        item = result['authorized_retry_once'][kind]
        assert bindings[item['path']] == item['sha256']
    with pytest.raises(tts.TTSStopped):
        retry_once(path, authorization, transport=client)
    assert client.calls == 1


def test_second_timeout_retains_both_uncertain_ledgers_and_never_retries(tmp_path):
    path, authorization = original(tmp_path)
    old = path.read_bytes()
    client = Transport(TimeoutError('timeout again'))
    with pytest.raises(tts.TTSUnknown):
        retry_once(path, authorization, transport=client)
    assert path.read_bytes() == old and client.calls == 1
    attempt = path.parent / 'authorized_retry/utt_test/state.json'
    assert json.loads(attempt.read_text())['status'] == 'uncertain'
    from omni_story.evaluation.collect import operation_usage
    assert operation_usage(tmp_path, [])['tts'] == 2
    with pytest.raises(tts.TTSStopped, match='attempt_path_already_exists'):
        retry_once(path, authorization, transport=client)
    assert client.calls == 1
    with pytest.raises(tts.TTSUnknown):
        tts.synthesize_line('先吃饭。', 'Ethan', tmp_path, line_id='utt_test', transport=client)


def test_existing_attempt_directory_cannot_be_adopted_or_sent(tmp_path):
    path, authorization = original(tmp_path)
    (path.parent / 'authorized_retry').mkdir()
    client = Transport()
    with pytest.raises(tts.TTSStopped, match='attempt_path_already_exists'):
        retry_once(path, authorization, transport=client)
    assert client.calls == 0


def test_retry_normalized_streaming_wav_remains_reusable(tmp_path):
    import struct
    path, authorization = original(tmp_path)
    client = Transport()
    download = client.download
    def placeholder(url, target):
        download(url, target)
        raw = bytearray(target.read_bytes())
        raw[4:8] = struct.pack('<I', 0x7fffffbf)
        raw[40:44] = struct.pack('<I', 0x7fffff9b)
        target.write_bytes(raw)
    client.download = placeholder
    result = retry_once(path, authorization, transport=client)
    assert result['audio_normalization']['header_only']
    cached = tts.synthesize_line('先吃饭。', 'Ethan', tmp_path, line_id='utt_test', transport=client)
    assert cached['reused'] and cached['audio_sha256'] == result['audio_sha256']
    assert client.calls == 1


@pytest.mark.parametrize('edit', [
    {'max_additional_requests': 2}, {'allow_possible_duplicate_charge': False},
    {'original_ledger_sha256': 'wrong'}, {'original_ledger_path': '/another/line'},
    {'user_answer': 'continue'}, {'policy': 'automatic_retry'},
])
def test_exact_authorization_required_without_any_post(tmp_path, edit):
    path, authorization = original(tmp_path)
    write_json(authorization, {**json.loads(authorization.read_text()), **edit})
    client = Transport()
    with pytest.raises(tts.TTSStopped):
        retry_once(path, authorization, transport=client)
    assert client.calls == 0


@pytest.mark.parametrize('kind', ['original', 'authorization', 'attempt'])
def test_tampered_retry_evidence_rejected_on_cache_and_usage(tmp_path, kind):
    path, authorization = original(tmp_path)
    client = Transport()
    result = retry_once(path, authorization, transport=client)
    evidence = Path(result['authorized_retry_once'][kind]['path'])
    evidence.write_text(evidence.read_text() + ' ')
    with pytest.raises(tts.TTSStopped, match='evidence_changed'):
        tts.synthesize_line('先吃饭。', 'Ethan', tmp_path, line_id='utt_test', transport=client)
    with pytest.raises(tts.TTSStopped, match='evidence_changed'):
        _service_usage([path])
    assert client.calls == 1
