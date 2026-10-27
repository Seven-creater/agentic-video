import json
from pathlib import Path
import struct
from types import SimpleNamespace
import wave

import pytest

from omni_story.library import tts
from omni_story.library.media import sha256_file
from omni_story.library.state import json_sha, write_json


TEXT = '这份饭，踏实。'
VOICE = 'Ethan'
LINE_ID = 'utt_test'


class Transport:
    def __init__(self):
        self.calls = self.downloads = 0
        self.placeholder = False
        self.error = None

    def request(self, payload):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(http_status=200, body_text=json.dumps(dict(request_id='test-request',
            usage=dict(characters=len(payload['input']['text'])), output=dict(audio=dict(
                id='test-audio', url='https://result.aliyuncs.com/audio.wav')))))

    def download(self, url, path):
        self.downloads += 1
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\x34\x12' * 12000)
        if self.placeholder:
            raw = bytearray(path.read_bytes())
            raw[4:8] = struct.pack('<I', 0x7fffffbf)
            raw[40:44] = struct.pack('<I', 0x7fffff9b)
            path.write_bytes(raw)


@pytest.fixture
def client(monkeypatch):
    client = Transport()
    monkeypatch.setattr(tts, 'QwenTTS', lambda **kwargs: client)
    return client


def ledger(root, rows=None):
    configuration = {} if rows is None else dict(parent_baseline=dict(linked_ledgers=rows))
    path = root / 'library_state.json'
    write_json(path, dict(input_lock=dict(configuration=configuration)))
    return dict(path=str(path), sha256=sha256_file(path))


def call(cache, text=TEXT, voice=VOICE, line_id=LINE_ID, *, linked_ledgers=()):
    return tts.synthesize_line(text, voice, cache, line_id=line_id, linked_ledgers=linked_ledgers)


def lineage(tmp_path, client, cache='rough_audio/tts', line_id=LINE_ID):
    ancestor = tmp_path / 'ancestor'
    row = ledger(ancestor)
    received = call(ancestor / cache, line_id=line_id)
    current = ancestor / 'evaluations/current'
    ledger(current, [row])
    client.calls = client.downloads = 0
    return ancestor, current, row, received


def file_pins(root):
    return {str(path): sha256_file(path) for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('cache', ['rough_audio/tts', 'skill_finecut/creative_audio/tts',
                                 'skill_finecut/revision/creative_audio/tts'])
def test_received_ancestor_is_copied_without_requests_or_ancestor_writes(tmp_path, client, cache):
    ancestor, current, row, received = lineage(tmp_path, client, cache)
    pins = file_pins(ancestor / cache)
    cache_dir = current / 'rough_audio/tts'
    result = call(cache_dir, linked_ledgers=[row])
    state_path = cache_dir / LINE_ID / 'state.json'
    saved = json.loads(state_path.read_text())
    source = Path(received['audio_path']).parent / 'state.json'
    assert result['reused'] and result['status'] == 'received' and result['audio_verified']
    assert saved['audio_path'] == str(cache_dir / LINE_ID / 'audio.wav')
    assert Path(saved['audio_path']).read_bytes() == Path(received['audio_path']).read_bytes()
    assert saved['reused_from'] == dict(path=str(source), sha256=sha256_file(source),
        ledger_path=row['path'], ledger_sha256=row['sha256'])
    assert saved['binding'] == received['binding']
    assert saved['request_sha256'] == received['request_sha256']
    assert call(cache_dir)['reused']
    assert file_pins(ancestor / cache) == pins
    assert client.calls == client.downloads == 0


@pytest.mark.parametrize('matching', [True, False])
def test_changed_ancestor_ledger_stops_before_reuse_or_new_request(tmp_path, client, matching):
    _, current, row, _ = lineage(tmp_path, client)
    path = Path(row['path'])
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(tts.TTSStopped, match='ancestor_ledger_changed'):
        call(current / 'rough_audio/tts', TEXT if matching else '不同的文本。', linked_ledgers=[row])
    assert client.calls == client.downloads == 0


@pytest.mark.parametrize('status', ['submitted', 'uncertain'])
@pytest.mark.parametrize('other_received', [False, True])
def test_unknown_ancestor_blocks_even_when_another_match_was_received(tmp_path, client, status,
                                                                    other_received):
    ancestor, current, row, received = lineage(tmp_path, client)
    if other_received:
        call(ancestor / 'skill_finecut/creative_audio/tts')
        client.calls = client.downloads = 0
    source = Path(received['audio_path']).parent / 'state.json'
    saved = json.loads(source.read_text())
    write_json(source, dict(saved, status=status, audio_verified=False))
    with pytest.raises(tts.TTSUnknown, match='outcome_unknown_no_replay'):
        call(current / 'rough_audio/tts', linked_ledgers=[row])
    assert client.calls == client.downloads == 0
    assert not (current / 'rough_audio/tts' / LINE_ID / 'state.json').exists()


@pytest.mark.parametrize('changed', ['text', 'voice', 'endpoint', 'model', 'line_id'])
def test_different_binding_synthesizes_normally(tmp_path, client, monkeypatch, changed):
    _, current, row, _ = lineage(tmp_path, client)
    options = {}
    if changed == 'text':
        options['text'] = '不同的文本。'
    elif changed == 'voice':
        options['voice'] = 'Moon'
    elif changed == 'line_id':
        options['line_id'] = 'utt_other'
    elif changed == 'endpoint':
        monkeypatch.setattr(tts, 'ENDPOINT', tts.ENDPOINT + '/other')
    else:
        # Keep the current supported model; only the ancestor binding differs.
        source = current.parents[1] / 'rough_audio/tts' / LINE_ID / 'state.json'
        saved = json.loads(source.read_text())
        saved['model'] = saved['binding']['payload']['model'] = 'older-model'
        saved['request_sha256'] = json_sha(saved['binding'])
        write_json(source, saved)
    result = call(current / 'rough_audio/tts', linked_ledgers=[row], **options)
    assert not result['reused'] and result['audio_verified']
    assert 'reused_from' not in result
    assert client.calls == client.downloads == 1


@pytest.mark.parametrize('with_task_ledger', [False, True])
def test_no_ancestor_retains_local_reuse_and_unknown_protection(tmp_path, client, with_task_ledger):
    if with_task_ledger:
        ledger(tmp_path)
    cache = tmp_path / 'rough_audio/tts'
    assert not call(cache)['reused']
    assert call(cache)['reused']
    source = cache / LINE_ID / 'state.json'
    write_json(source, dict(json.loads(source.read_text()), status='uncertain'))
    with pytest.raises(tts.TTSUnknown, match='no_replay'):
        call(cache)
    assert client.calls == client.downloads == 1


def test_current_task_without_baseline_does_not_inherit_enclosing_task_baseline(tmp_path, client):
    ancestor, current, row, _ = lineage(tmp_path, client)
    ledger(ancestor, [row])
    ledger(current)
    assert not call(current / 'rough_audio/tts')['reused']
    assert client.calls == client.downloads == 1


@pytest.mark.parametrize('contents', [None, '{', '{}', '{"input_lock": null}',
                                    '{"input_lock": {"configuration": {}}}'])
@pytest.mark.parametrize('layout', ['rough_audio/tts', 'unrelated/external_cache'])
def test_no_baseline_never_reads_task_ledgers_or_searches_ancestors(tmp_path, client, monkeypatch,
                                                                contents, layout):
    if contents is not None:
        (tmp_path / 'library_state.json').write_text(contents)
    read_text, read_bytes = Path.read_text, Path.read_bytes
    def guarded_read_text(path, *args, **kwargs):
        assert path.name != 'library_state.json', 'task ledger must not be read'
        return read_text(path, *args, **kwargs)
    def guarded_read_bytes(path, *args, **kwargs):
        assert path.name != 'library_state.json', 'task ledger must not be read'
        return read_bytes(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', guarded_read_text)
    monkeypatch.setattr(Path, 'read_bytes', guarded_read_bytes)
    monkeypatch.setattr(tts, '_reuse_ancestor', lambda *args: pytest.fail('no baseline to search'))
    cache = tmp_path / layout
    assert not call(cache)['reused']
    assert call(cache)['reused']
    source = cache / LINE_ID / 'state.json'
    write_json(source, dict(json.loads(source.read_text()), status='uncertain'))
    with pytest.raises(tts.TTSUnknown, match='outcome_unknown_no_replay'):
        call(cache)
    assert client.calls == client.downloads == 1


def test_rejected_ancestor_keeps_no_automatic_replay_policy(tmp_path, client):
    _, current, row, received = lineage(tmp_path, client)
    source = Path(received['audio_path']).parent / 'state.json'
    write_json(source, dict(json.loads(source.read_text()), status='rejected', audio_verified=False))
    with pytest.raises(tts.TTSRejected, match='already_rejected_no_replay'):
        call(current / 'rough_audio/tts', linked_ledgers=[row])
    assert client.calls == client.downloads == 0


@pytest.mark.parametrize('normalized', [False, True])
@pytest.mark.parametrize('problem', ['audio_sha', 'probe', 'unverified', 'request_sha', 'normalization'])
def test_invalid_ancestor_artifact_cannot_be_reused_or_resent(tmp_path, client, normalized, problem):
    client.placeholder = normalized
    _, current, row, received = lineage(tmp_path, client)
    source = Path(received['audio_path']).parent / 'state.json'
    saved = json.loads(source.read_text())
    if problem == 'audio_sha':
        path = Path(received['audio_path'])
        path.write_bytes(path.read_bytes()[:-2])
    elif problem == 'probe':
        saved['probe']['frames'] += 1
    elif problem == 'unverified':
        saved['audio_verified'] = False
    elif problem == 'request_sha':
        saved['request_sha256'] = 'changed'
    else:
        saved.setdefault('audio_normalization', {})['pcm_sha256_after'] = 'changed'
    write_json(source, saved)
    with pytest.raises(tts.TTSStopped):
        call(current / 'rough_audio/tts', linked_ledgers=[row])
    assert client.calls == client.downloads == 0


def test_normalized_wav_and_raw_receipt_are_copied_and_validated(tmp_path, client):
    client.placeholder = True
    ancestor, current, row, received = lineage(tmp_path, client)
    pins = file_pins(ancestor / 'rough_audio/tts')
    cache = current / 'rough_audio/tts'
    result = call(cache, linked_ledgers=[row])
    assert Path(result['audio_path']).name == 'audio.normalized.wav'
    receipt = result['audio_normalization']
    assert receipt['raw_download_path'] == result['source_audio_path']
    assert receipt['normalized_audio_path'] == result['audio_path']
    for key in ('audio_path', 'source_audio_path'):
        assert Path(result[key]).read_bytes() == Path(received[key]).read_bytes()
    assert call(cache)['reused']
    assert file_pins(ancestor / 'rough_audio/tts') == pins
    assert client.calls == client.downloads == 0


@pytest.mark.parametrize('modified', ['ledger', 'state', 'copied_audio', 'copied_receipt'])
def test_reused_cache_keeps_validating_provenance_and_local_bytes(tmp_path, client, modified):
    _, current, row, _ = lineage(tmp_path, client)
    cache = current / 'rough_audio/tts'
    result = call(cache, linked_ledgers=[row])
    if modified == 'ledger':
        path = Path(row['path'])
    elif modified == 'state':
        path = Path(result['reused_from']['path'])
    elif modified == 'copied_audio':
        path = Path(result['audio_path'])
    else:
        path = cache / LINE_ID / 'state.json'
        saved = json.loads(path.read_text())
        saved['request_id'] = 'changed'
        write_json(path, saved)
    if modified != 'copied_receipt':
        path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(tts.TTSStopped):
        call(cache)
    assert client.calls == client.downloads == 0


def test_all_linked_ledgers_are_checked_and_grandparent_cache_is_found(tmp_path, client):
    ancestor, current, original, _ = lineage(tmp_path, client)
    middle = ancestor / 'evaluations/middle'
    row = ledger(middle, [original])
    ledger(current, [row, original])
    assert call(current / 'rough_audio/tts', linked_ledgers=[row, original])['reused']
    assert client.calls == client.downloads == 0


def test_later_changed_ledger_cannot_be_hidden_by_earlier_received_match(tmp_path, client):
    ancestor, current, original, _ = lineage(tmp_path, client)
    other = ledger(ancestor / 'artifacts/rough')
    ledger(current, [original, other])
    Path(other['path']).write_text('{}')
    with pytest.raises(tts.TTSStopped, match='ancestor_ledger_changed'):
        call(current / 'rough_audio/tts', linked_ledgers=[original, other])
    assert client.calls == client.downloads == 0


def test_unknown_other_binding_does_not_prevent_new_text(tmp_path, client):
    _, current, row, received = lineage(tmp_path, client)
    source = Path(received['audio_path']).parent / 'state.json'
    write_json(source, dict(json.loads(source.read_text()), status='uncertain', audio_verified=False))
    result = call(current / 'rough_audio/tts', text='不同的文本。', linked_ledgers=[row])
    assert not result['reused'] and client.calls == client.downloads == 1


def test_pcm_is_probed_even_when_ancestor_audio_sha_was_updated(tmp_path, client):
    _, current, row, received = lineage(tmp_path, client)
    audio = Path(received['audio_path'])
    audio.write_bytes(audio.read_bytes()[:-2])
    source = audio.parent / 'state.json'
    write_json(source, dict(json.loads(source.read_text()), audio_sha256=sha256_file(audio)))
    with pytest.raises(ValueError, match='tts_truncated_pcm_wav'):
        call(current / 'rough_audio/tts', linked_ledgers=[row])
    assert client.calls == client.downloads == 0


def test_reused_results_can_be_reused_by_another_generation(tmp_path, client):
    _, current, row, _ = lineage(tmp_path, client)
    result = call(current / 'rough_audio/tts', linked_ledgers=[row])
    grandchild = current / 'evaluations/next'
    parent = dict(path=str(current / 'library_state.json'),
                  sha256=sha256_file(current / 'library_state.json'))
    ledger(grandchild, [parent])
    inherited = call(grandchild / 'rough_audio/tts', linked_ledgers=[parent])
    assert inherited['reused'] and inherited['audio_sha256'] == result['audio_sha256']
    assert client.calls == client.downloads == 0


@pytest.mark.parametrize('caller', ['draft', 'visual_audio'])
@pytest.mark.parametrize('with_baseline', [False, True])
def test_creative_callers_pass_explicit_task_baseline(tmp_path, client, monkeypatch, caller,
                                                   with_baseline):
    key = 'utt_' + json_sha(dict(provider=tts.ENDPOINT, model=tts.MODEL, language='Chinese',
                                text=TEXT, voice=VOICE))[:20]
    _, current, row, received = lineage(tmp_path, client, line_id=key)
    cache = current / 'skill_finecut/creative_audio/tts'
    cache.parent.mkdir(parents=True)
    (cache.parent / 'library_state.json').write_text('{')
    rows = [row] if with_baseline else ()
    line = dict(id='line', text=TEXT, voice=VOICE, segment_ids=['segment'], start_s=0, end_s=1)
    if caller == 'draft':
        from omni_story.library import story_context
        monkeypatch.setattr(story_context, 'validate_story_context', lambda context: context)
        context = dict(audio_draft=[line], segments=[dict(id='segment', start_s=0, end_s=1)])
        result = story_context.synthesize_draft(context, cache, linked_ledgers=rows)['received_tts'][0]
    else:
        from omni_story.library.visual_audio import VisualAudio
        audio = object.__new__(VisualAudio)
        audio.story_context, audio.tts, audio.tts_cache_dir = None, None, cache
        configuration = dict(parent_baseline=dict(linked_ledgers=rows)) if with_baseline else {}
        audio.state = SimpleNamespace(input_lock=dict(configuration=configuration))
        result = audio.synthesize(dict(lines=[line]))[0][0]
    assert result['audio_sha256'] == received['audio_sha256']
    assert Path(result['audio_path']).is_relative_to(cache)
    assert client.calls == client.downloads == (0 if with_baseline else 1)


@pytest.mark.parametrize('count', [1, 3])
@pytest.mark.parametrize('cache_name', ['rough_audio/tts', 'skill_finecut/creative_audio/tts'])
def test_reused_lines_have_zero_task_charges_and_do_not_double_count_lineage(tmp_path, client,
                                                                         count, cache_name):
    from omni_story.library.audio_usage import _service_usage, task_usage, usage_bindings
    ancestor, current = tmp_path / 'ancestor', tmp_path / 'child'
    row = ledger(ancestor)
    originals = [call(ancestor / cache_name, text=TEXT + str(i), line_id='utt_' + str(i))
                 for i in range(count)]
    ledger(current, [row])
    client.calls = client.downloads = 0
    copied = [call(current / cache_name, text=TEXT + str(i), line_id='utt_' + str(i),
                   linked_ledgers=[row]) for i in range(count)]
    def state(root):
        return SimpleNamespace(output=root, input_lock=dict(configuration={},
            tts_cache_dir=str(root / cache_name)),
            usage=lambda: dict(requests=0, prompt_tokens=0, completion_tokens=0,
                               max_requests=None, uncertain_calls=[]))
    original_usage = task_usage(state(ancestor))['audio_services']['tts']
    task_report = task_usage(state(current))
    child_usage = task_report['audio_services']['tts']
    assert child_usage['requests'] == 0 and child_usage['reused'] == count
    assert child_usage['statuses'] == {} and child_usage['captured_usage'] == {}
    assert child_usage['uncertain_calls'] == child_usage['prior_authorized_uncertain_attempts'] == []
    expected_usage = dict(characters=sum(len(original['text']) for original in originals))
    assert original_usage['requests'] == count and original_usage['reused'] == 0
    assert original_usage['statuses'] == dict(received=count)
    assert original_usage['captured_usage'] == expected_usage
    assert original_usage['requests'] + child_usage['requests'] == count
    paths = [Path(item['audio_path']).parent / 'state.json' for item in [*originals, *copied]]
    combined = _service_usage(paths)
    assert combined['requests'] == count and combined['reused'] == count
    assert combined['captured_usage'] == expected_usage and combined['statuses'] == dict(received=count)
    bindings = usage_bindings(task_report)
    for original, inherited, audit in zip(originals, copied, child_usage['ledgers']):
        for key in ('request_count', 'request_id', 'usage', 'submitted_at', 'received_at'):
            assert inherited[key] == original[key]
        assert audit['usage'] == original['usage']
        assert audit['reused_from'] == inherited['reused_from']
        path = Path(inherited['audio_path']).parent / 'state.json'
        assert bindings[str(path)] == sha256_file(path)
    assert client.calls == client.downloads == 0


def test_mixed_reuse_and_new_synthesis_counts_only_current_requests(tmp_path, client):
    from omni_story.library.audio_usage import _service_usage
    _, current, row, _ = lineage(tmp_path, client)
    cache = current / 'rough_audio/tts'
    reused = call(cache, linked_ledgers=[row])
    new = call(cache, text='本任务新句。', line_id='utt_new', linked_ledgers=[row])
    assert call(cache, text='本任务新句。', line_id='utt_new')['reused']
    paths = [Path(item['audio_path']).parent / 'state.json' for item in (reused, new)]
    usage = _service_usage(paths)
    assert usage['requests'] == usage['reused'] == 1
    assert usage['statuses'] == dict(received=1)
    assert usage['captured_usage'] == dict(characters=len('本任务新句。'))
    assert client.calls == client.downloads == 1


def test_authorized_retry_receipt_remains_verifiable_after_reuse(tmp_path, client):
    from omni_story.library.audio_usage import _service_usage
    from omni_story.library.tts_retry import POLICY, retry_once, validate_replacement
    ancestor = tmp_path / 'ancestor'
    row = ledger(ancestor)
    cache = ancestor / 'rough_audio/tts'
    client.error = TimeoutError('test timeout')
    with pytest.raises(tts.TTSUnknown):
        call(cache)
    source = cache / LINE_ID / 'state.json'
    authorization = source.parent / 'authorization.json'
    write_json(authorization, dict(policy=POLICY, original_ledger_path=str(source),
        original_ledger_sha256=sha256_file(source), max_additional_requests=1,
        allow_possible_duplicate_charge=True, user_answer='允许这一句重调一次'))
    client.error = None
    retry_once(source, authorization, transport=client)
    current = ancestor / 'evaluations/current'
    ledger(current, [row])
    client.calls = client.downloads = 0
    pins = file_pins(cache)
    result = call(current / 'rough_audio/tts', linked_ledgers=[row])
    assert result['request_count'] == 2 and result['reused']
    copied = Path(result['audio_path']).parent / 'state.json'
    validate_replacement(copied)
    usage = _service_usage([copied])
    assert usage['requests'] == 0 and usage['reused'] == 1
    assert usage['captured_usage'] == {} and usage['statuses'] == {}
    assert usage['prior_authorized_uncertain_attempts'] == []
    assert usage['ledgers'][0]['retry_evidence'] == result['authorized_retry_once']
    combined = _service_usage([source, copied])
    assert combined['requests'] == 2 and combined['reused'] == 1
    assert combined['statuses'] == dict(received=1, prior_authorized_uncertain=1)
    assert combined['captured_usage'] == dict(characters=len(TEXT))
    assert call(current / 'rough_audio/tts')['reused']
    assert file_pins(cache) == pins and client.calls == client.downloads == 0
    Path(result['audio_path']).write_bytes(b'changed')
    with pytest.raises(tts.TTSStopped, match='audio_sha_mismatch'):
        _service_usage([copied])
