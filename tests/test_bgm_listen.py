import copy
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from omni_story.evaluation import bgm_listen as b, reference_exam as exam
from omni_story.library import audio_review as review
from omni_story.library.state import json_sha


def music(label='中性音乐'):
    return dict(style=label, instruments=['钢琴'], tempo='慢', mood='平静', recognizable_song=None, notes='器乐')


def heard(identity):
    return music('音色' + hashlib.sha256(identity.encode()).hexdigest()[:8])


@pytest.fixture(autouse=True)
def memory(monkeypatch):
    files = {}; commands = []; sends = []; glm_sends = []; root = Path('/memory/job')
    def put(path, value):
        files[str(path)] = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
    def sha(path):
        return hashlib.sha256(files[str(path)]).hexdigest()
    for name in ('read_text', 'read_bytes', 'write_text', 'write_bytes', 'exists', 'is_file', 'mkdir', 'resolve', 'stat', 'glob'):
        original = getattr(Path, name)
        def route(path, *args, _name=name, _original=original, **kwargs):
            if not str(path).startswith('/memory/'):
                return _original(path, *args, **kwargs)
            key = str(path)
            if _name == 'read_text':return files[key].decode('utf-8')
            if _name == 'read_bytes':return files[key]
            if _name in ('write_text', 'write_bytes'):
                files[key] = args[0].encode('utf-8') if _name == 'write_text' else args[0]
                return len(args[0])
            if _name in ('exists', 'is_file'):return key in files
            if _name == 'resolve':return path
            if _name == 'mkdir':return None
            if _name == 'stat':return SimpleNamespace(st_size=len(files[key]), st_mtime_ns=1)
            if _name == 'glob':
                return [Path(key) for key in files if Path(key).name == 'library_state.json' and Path(key).parent.parent == path]
        monkeypatch.setattr(Path, name, route)
    for module in (b, review, exam):
        monkeypatch.setattr(module, 'sha256_file', sha)
        monkeypatch.setattr(module, 'write_json', put)
        monkeypatch.setattr(module, 'file_lock', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(review, 'probe_media', lambda path: dict(duration_s=12, streams=[dict(index=0, codec_type='video'), dict(index=1, codec_type='audio')]))
    monkeypatch.setattr('omni_story.library.resource_guard.before_request', lambda: None)
    monkeypatch.setattr(review, 'QwenAPI', lambda *args, **kwargs: pytest.fail('Unexpected Qwen construction'))
    monkeypatch.setattr(b.subprocess, 'run', lambda *args, **kwargs: pytest.fail('Unexpected subprocess'))
    monkeypatch.setattr(b, '_duration', lambda audio: 75.0)
    def run(args):
        commands.append(args)
        if args[0] == '/memory/demucs':
            folder = Path(args[args.index('-o') + 1]) / 'htdemucs/audio/no_vocals.wav'
            files[str(folder)] = b'no vocals:' + files[args[-1]]
        elif args[-1].endswith('.wav'):
            files[args[-1]] = b'pcm:' + files[args[args.index('-i') + 1]]
        else:
            files[args[-1]] = json.dumps(args).encode()
        return ''
    monkeypatch.setattr(b, '_run', run)
    class API:
        cfg = dict(model='fake-qwen', base_url='https://example.test', temperature=0)
        key = 'synthetic-key'
        result = None
        active = False
        def request(self, prompt, *, media):
            assert not self.active
            self.active = True
            spec = next(json.loads(data)['spec'] for name, data in files.items() if name.endswith('/clip.json') and json.loads(data)['path'] == str(media))
            folder = b._listen_dir(root, spec)
            assert b._read(folder / 'state.json')['status'] == 'submitted'
            assert (folder / 'request.json').exists()
            sends.append(str(media))
            try:
                if isinstance(self.result, BaseException):raise self.result
                raw = heard(spec['id']) if self.result is None else self.result
                return dict(http_status=200, body_text=json.dumps(dict(choices=[dict(message=dict(content=json.dumps(raw, ensure_ascii=False)))])))
            finally:
                self.active = False
    class GLM:
        answer = dict(template_id='t0', confidence=.8, reason='音乐相符')
        failure = None
        def ask(self, model, prompt, frames, **limits):
            assert not frames
            question = json.loads(prompt.split('\n', 1)[1])
            directory = b._match_dir(root, question)
            assert (directory / 'attempt.json').exists()
            glm_sends.append(question)
            if self.failure:raise self.failure
            folder = Path('/memory/receipts') / ('r' + str(len(glm_sends)))
            text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer, ensure_ascii=False)
            response = dict(http_status=200, body_text=json.dumps(dict(content=[dict(type='text', text=text)], stop_reason='end_turn')))
            put(folder / 'calls/c0/response.json', response)
            payload = dict(messages=[dict(role='user', content=[dict(type='text', text=prompt)])],
                           max_tokens=limits['max_tokens'], thinking=dict(budget_tokens=limits['thinking_budget']))
            put(folder / 'library_state.json', dict(input_lock=dict(request=dict(payload=payload)),
                calls=[dict(id='c0', status='received', response_sha256=json_sha(response))]))
            return text
    glm = GLM()
    monkeypatch.setitem(sys.modules, 'hunter', SimpleNamespace(glm=glm))
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE', '/memory/budget')
    monkeypatch.setenv('HUNTER_RECEIPTS_DIR', '/memory/receipts')
    manifest = dict(schema=b.SCHEMA, seed=20261025, clip_s=12, demucs='/memory/demucs',
                    templates=[dict(id=f't{i}', name=f'{i+1:02d}_模板{i}', profile_video=f'/memory/profiles/{i}.mp4',
                                    understanding_path=f'/memory/understanding/{i}.json') for i in range(10)],
                    queries=[dict(id=f'q{i}', template_id=f't{i}', video=f'/memory/secret_answer_t{i}.mp4') for i in range(3)])
    for row in manifest['templates']:
        put(row['profile_video'], row['id'].encode())
        put(row['understanding_path'], dict(template_viewer_takeaway='态度', template_theme='主旨', secret='不可传输'))
    for row in manifest['queries']:
        put(row['video'], row['id'].encode())
    put(root / 'manifest.json', manifest)
    return SimpleNamespace(files=files, put=put, root=root, manifest=manifest, commands=commands,
                           api=API(), sends=sends, glm=glm, glm_sends=glm_sends)


def ready(memory):
    b.prepare(memory.root)
    b.listen(memory.root, api=memory.api)
    return memory


def test_manifest_accepts_all_candidates_and_queries(memory):
    assert b.validate_manifest(memory.manifest) is memory.manifest


@pytest.mark.parametrize('change,error', [
    (lambda m: m['templates'].pop(), '10_templates'),
    (lambda m: m['templates'][1].update(id='t0'), 'template_identity'),
    (lambda m: m['templates'][0].update(id='none'), 'template_identity'),
    (lambda m: m['queries'][0].update(template_id='unknown'), 'query_template'),
    (lambda m: m['queries'][1].update(id='q0'), 'query_identity'),
    (lambda m: m['templates'][0].update(profile_video='relative.mp4'), 'absolute_paths'),
    (lambda m: m['templates'][0].update(understanding_path='relative.json'), 'absolute_paths'),
    (lambda m: m['queries'][0].update(video='relative.mp4'), 'absolute_paths'),
    (lambda m: m.update(demucs='demucs'), 'absolute_paths'),
    (lambda m: m.update(fingerprint_rows='relative.jsonl'), 'absolute_paths'),
    (lambda m: m.update(seed=True), 'seed_or_clip'),
    (lambda m: m.update(clip_s=float('nan')), 'seed_or_clip'),
    (lambda m: m.update(clip_s=0), 'seed_or_clip'),
    (lambda m: m.update(queries=[]), 'queries_required'),
])
def test_manifest_rejects_bad_inputs(memory, change, error):
    change(memory.manifest)
    with pytest.raises(ValueError, match=error):b.validate_manifest(memory.manifest)


def test_prepare_black_video_hashes_and_unchanged_input_reuse(memory):
    result = b.prepare(memory.root)
    renders = [cmd for cmd in memory.commands if cmd[-1].endswith('.mp4')]
    assert len(result['items']) == len(renders) == 13
    for item, cmd in zip(result['items'], renders):
        inputs = [cmd[i+1] for i, arg in enumerate(cmd) if arg == '-i']
        assert inputs[0] == 'color=c=black:s=320x180:r=10'
        assert inputs[1].endswith('/no_vocals.wav')
        assert all(not source.endswith('.mp4') for source in inputs)
        assert [cmd[i+1] for i, arg in enumerate(cmd) if arg == '-map'] == ['0:v:0', '1:a:0']
        assert cmd[cmd.index('-map_metadata')+1] == '-1'
        assert item['sha256'] == b.sha256_file(item['path'])
        assert item['duration_s'] == (60 if item['spec']['role'] == 'profile' else 12)
        assert 0 <= item['start_s'] <= 63
    separators = [cmd for cmd in memory.commands if cmd[0] == '/memory/demucs']
    assert len(separators) == 13 and all(cmd[1:3] == ['--two-stems', 'vocals'] for cmd in separators)
    count = len(memory.commands)
    assert b.prepare(memory.root) == result
    assert len(memory.commands) == count
    memory.put(memory.manifest['queries'][0]['video'], b'changed source')
    changed = b.prepare(memory.root)
    assert len(memory.commands) == count + 3
    assert changed['items'][10]['sha256'] != result['items'][10]['sha256']


def test_prepare_offsets_are_seeded_and_short_audio_is_whole(memory, monkeypatch):
    first = b.prepare(memory.root)
    query = first['items'][10]
    del memory.files[str(Path(query['path']).parent / 'clip.json')]
    assert b.prepare(memory.root)['items'][10]['start_s'] == query['start_s']
    memory.manifest['seed'] += 1
    memory.put(memory.root / 'manifest.json', memory.manifest)
    assert b.prepare(memory.root)['items'][10]['start_s'] != query['start_s']
    memory.manifest['clip_s'] = 100
    memory.put(memory.root / 'manifest.json', memory.manifest)
    monkeypatch.setattr(b, '_duration', lambda audio: 5)
    for item in b.prepare(memory.root)['items'][10:]:
        assert item['start_s'] == 0 and item['duration_s'] == 5


def test_prepare_corrupt_clip_blocks_reuse(memory):
    item = b.prepare(memory.root)['items'][0]
    memory.put(item['path'], b'changed')
    with pytest.raises(ValueError, match='artifact_missing_or_changed'):b.prepare(memory.root)


def test_listen_serial_write_before_send_and_received_cache(memory):
    b.prepare(memory.root)
    first = b.listen(memory.root, api=memory.api)
    assert len(memory.sends) == 13
    assert [row['id'] for row in first] == [f't{i}' for i in range(10)] + [f'q{i}' for i in range(3)]
    assert all(row['status'] == 'received' for row in first)
    assert b.listen(memory.root, api=memory.api) == first
    assert len(memory.sends) == 13
    assert '不要猜测视频内容或模板' in b.LISTEN_PROMPT and '不要转写人声' in b.LISTEN_PROMPT


@pytest.mark.parametrize('interruption', [TimeoutError('unknown'), KeyboardInterrupt()])
def test_listen_unknown_is_not_replayed_or_continued(memory, interruption):
    b.prepare(memory.root); memory.api.result = interruption
    with pytest.raises((RuntimeError, KeyboardInterrupt)):b.listen(memory.root, api=memory.api)
    memory.api.result = None
    with pytest.raises(RuntimeError, match='unknown_no_replay'):b.listen(memory.root, api=memory.api)
    assert len(memory.sends) == 1


def test_listen_preflights_later_unknown_before_sending(memory):
    item = b.prepare(memory.root)['items'][-1]
    memory.put(b._listen_dir(memory.root, item['spec']) / 'state.json', dict(status='submitted'))
    with pytest.raises(RuntimeError, match='unknown_no_replay'):b.listen(memory.root, api=memory.api)
    assert not memory.sends


def test_received_invalid_music_is_preserved_without_retry(memory):
    b.prepare(memory.root); memory.api.result = dict(style='incomplete')
    for _ in range(2):
        assert all(row['status'] == 'received_invalid' for row in b.listen(memory.root, api=memory.api))
    assert len(memory.sends) == 13
    with pytest.raises(ValueError, match='description_invalid_no_replay'):b.match(memory.root)
    assert not memory.glm_sends


def test_changed_audio_binding_does_not_send_again(memory):
    ready(memory)
    memory.put(memory.manifest['queries'][0]['video'], b'new source')
    b.prepare(memory.root)
    with pytest.raises(ValueError, match='binding_changed_no_replay'):b.listen(memory.root, api=memory.api)
    assert len(memory.sends) == 13


def test_match_blind_shuffled_question_and_glm_receipt_reuse(memory):
    ready(memory)
    questions = b._questions(memory.root, memory.manifest)
    order = [row['template_id'] for row in questions[0][1]['candidates']]
    assert order != [row['id'] for row in memory.manifest['templates']]
    assert order == [row['template_id'] for row in b._questions(memory.root, memory.manifest)[0][1]['candidates']]
    changed = copy.deepcopy(memory.manifest)
    changed['queries'][0]['template_id'] = 't9'
    assert b._questions(memory.root, changed) == [(dict(q, template_id='t9') if q['id'] == 'q0' else q, question) for q, question in questions]
    text = json.dumps(questions[0][1], ensure_ascii=False)
    assert '/memory/' not in text and 'secret_answer' not in text and '不可传输' not in text
    assert 'q0' not in text and 'answer' not in questions[0][1] and len(order) == 10
    first = b.match(memory.root)
    assert all(row['status'] == 'valid' for row in first)
    assert b.match(memory.root) == first and len(memory.glm_sends) == 3
    assert all('/match/q_' in path for path in memory.files if path.endswith('/answer.json'))


@pytest.mark.parametrize('answer', [dict(template_id='not_a_template', confidence=.5, reason='x'),
    dict(template_id='t0', confidence=2, reason='x'), dict(template_id='t0', confidence=True, reason='x'),
    dict(template_id='t0', confidence=float('nan'), reason='x'),
    dict(template_id='t0', confidence=float('inf'), reason='x'),
    dict(template_id='t0', confidence=.5, reason=None), dict(template_id=['t0'], confidence=.5, reason='x'), 'not JSON'])
def test_invalid_glm_response_counts_as_invalid_without_retry(memory, answer):
    ready(memory); memory.glm.answer = answer
    for _ in range(2):
        assert all(row['status'] == 'invalid' for row in b.match(memory.root))
    assert len(memory.glm_sends) == 3
    result = b.report(memory.root)
    assert result['invalid'] == 3 and result['top1_accuracy'] == result['none_rate'] == 0


def test_match_unknown_stops_without_retry(memory):
    ready(memory); memory.glm.failure = TimeoutError('unknown')
    with pytest.raises(TimeoutError):b.match(memory.root)
    memory.glm.failure = None
    with pytest.raises(RuntimeError, match='not_received_no_replay'):b.match(memory.root)
    assert len(memory.glm_sends) == 1


def test_report_scores_all_queries_and_attaches_same_query_baseline(memory):
    ready(memory)
    questions = b._questions(memory.root, memory.manifest)
    for (query, question), choice in zip(questions, ['t0', 'none', 'invalid_id']):
        memory.glm.answer = dict(template_id=choice, confidence=.5, reason='听感')
        exam.ask_probe(question, b._match_dir(memory.root, question), allow_invalid=True)
    baseline = dict(query_id='q0', choice='t0', similarity=.9)
    memory.manifest['fingerprint_rows'] = '/memory/fingerprints.jsonl'
    memory.put(memory.root / 'manifest.json', memory.manifest)
    memory.put('/memory/fingerprints.jsonl', ('\n' + json.dumps(baseline) + '\n').encode())
    result = b.report(memory.root)
    assert result['total'] == 3 and result['valid'] == 2 and result['invalid'] == 1
    assert result['top1_accuracy'] == result['none_rate'] == 1/3
    assert [row['fingerprint_baseline'] for row in result['rows']] == [baseline, None, None]
    assert result['rows'][0]['profile_music'] == heard('t0')
    assert result['rows'][0]['query_music'] == heard('q0')
    assert result == b._read(memory.root / 'report.json')
    assert '33.33%' in (memory.root / 'report.md').read_text()
    assert len(memory.sends) == 13 and len(memory.glm_sends) == 3


def test_report_without_fingerprints_uses_null_and_verifies_receipts(memory):
    ready(memory); b.match(memory.root)
    assert all(row['fingerprint_baseline'] is None for row in b.report(memory.root)['rows'])
    response = next(path for path in memory.files if '/receipts/' in path and path.endswith('/response.json'))
    memory.put(response, dict(tampered=True))
    with pytest.raises(ValueError, match='receipt_changed'):b.report(memory.root)


def test_modified_qwen_receipt_cannot_feed_match(memory):
    ready(memory)
    response = next(path for path in memory.files if '/listen/' in path and path.endswith('/response.json'))
    memory.put(response, dict(tampered=True))
    with pytest.raises(ValueError, match='artifact_missing_or_changed'):b.match(memory.root)
    assert not memory.glm_sends


@pytest.mark.parametrize('format', ['array', 'rows', 'jsonl'])
def test_fingerprint_rows_use_query_identity_or_video(memory, format):
    row = dict(video='memory/secret_answer_t0.mp4', result='baseline')
    memory.manifest['fingerprint_rows'] = '/memory/fingerprints'
    value = [row] if format == 'array' else dict(rows=[row])
    memory.put('/memory/fingerprints', (json.dumps(row) + '\n\n' + json.dumps(dict(query_id='unrelated'))).encode()
               if format == 'jsonl' else value)
    rows = b._fingerprints(memory.manifest)
    assert b._baseline(rows, memory.manifest['queries'][0]) == row
    assert b._baseline(rows, memory.manifest['queries'][1]) is None
    with pytest.raises(ValueError, match='query_ambiguous'):
        b._baseline([row, row], memory.manifest['queries'][0])


def test_description_whitelists_fields_and_recognized_song(memory):
    raw = dict(music(), leaked_answer='t0', recognizable_song=dict(title='歌曲', confidence=.9, source='private'))
    cleaned = b._description(raw)
    assert 'leaked_answer' not in cleaned and cleaned['recognizable_song'] == dict(title='歌曲', confidence=.9)
    raw['recognizable_song']['confidence'] = 'certain'
    with pytest.raises(ValueError, match='description_invalid'):b._description(raw)


@pytest.mark.parametrize('command', ['prepare', 'listen', 'match', 'report'])
def test_cli_dispatches_job(memory, monkeypatch, capsys, command):
    seen = []
    monkeypatch.setattr(b, command, lambda job, **kw: seen.append(job) or dict(ok=True))
    assert b.main([command, '--job', str(memory.root)]) == 0
    assert seen == [memory.root] and json.loads(capsys.readouterr().out) == dict(ok=True)


def test_listen_limit_is_passed_from_cli(memory, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(b, 'listen', lambda job, limit=None: seen.append(limit) or dict(ok=True))
    assert b.main(['listen', '--job', str(memory.root), '--limit', '1']) == 0 and seen == [1]


def test_profile_segments_make_evenly_spaced_profile_clips(tmp_path, monkeypatch):
    video = tmp_path / 'v.mp4'
    monkeypatch.setattr(b, 'sha256_file', lambda path: 'a' * 64)
    manifest = dict(demucs='/d', seed=1, clip_s=12, profile_segments=3, profile_segment_s=20,
                    templates=[dict(id='t1', profile_video=str(video))], queries=[])
    assets = b._assets(manifest)
    assert [a[1] for a in assets] == ['t1#s0', 't1#s1', 't1#s2']
    spec = b._spec(manifest, *assets[2])
    assert spec['segment'] == 2 and spec['segments'] == 3 and spec['segment_s'] == 20


def test_melody_prompt_and_extra_fields():
    assert b._listen_prompt(dict(listen_prompt='melody_v2')) == b.LISTEN_PROMPT_V2
    assert b._listen_prompt({}) == b.LISTEN_PROMPT
    raw = dict(style='Trap', instruments=['808'], tempo='中速', mood='紧张', recognizable_song=None, notes='',
               melody='三音下行循环', signature_sounds=['口哨'], bpm_estimate=140)
    out = b._description(raw)
    assert out['melody'] == '三音下行循环' and out['bpm_estimate'] == 140


@pytest.mark.parametrize('bad', [dict(profile_segments=1, profile_segment_s=20), dict(profile_segments=3),
                                 dict(listen_prompt='v9')])
def test_invalid_segment_or_prompt_settings_rejected(memory, bad):
    value = json.loads((memory.root / 'manifest.json').read_text()) if (memory.root / 'manifest.json').exists() else None
    if value is None:
        pytest.skip('fixture has no manifest file')
    with pytest.raises(ValueError):
        b.validate_manifest(dict(value, **bad))
