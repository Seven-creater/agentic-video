"""Offline QFS storyboard integration: synthetic media and recorded HTTP substitutes."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.error
from unittest.mock import Mock

import pytest

from omni_story.library import question_first as qfs
from omni_story.library import storyboard as sb
from omni_story.library.state import LibraryState, LibraryStopped, json_sha

MEDIA_COMMAND = qfs.media_command


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(qfs, 'http_post', Mock(side_effect=AssertionError('no network')))
    monkeypatch.setattr(qfs, 'media_command', Mock(side_effect=AssertionError('no ffmpeg')))


def cell(n, start, end, speakers=(), *, roles=('A', 'B'), core=False):
    return dict(id=f's{n}', start_s=start, end_s=end, roles=list(roles),
        action=f'动作{n}', story_role='对峙', basis='visible+asr', core=core,
        lines=[dict(speaker=s, intent=f'{n}-{i}发言', ref_asr_ids=[f'asr{n}_{i}'])
               for i, s in enumerate(speakers)])


def board(cells=None):
    return dict(storyboard=cells if cells is not None else [cell(1, 0, 4, ['A'], core=True)],
                pace='问答后收尾', dialogue_turns=1)


def asr_for(data):
    return [dict(segment_id=sid, source_start_s=c['start_s'] + .1,
                 source_end_s=c['end_s'] - .1, text=line['intent'])
            for c in data['storyboard'] for line in c['lines'] for sid in line['ref_asr_ids']]


@pytest.fixture
def film_cast():
    return qfs.read_json(Path(__file__).parents[1] / 'data/visual_library/three_films_v2/cast.json')


@pytest.fixture
def state(tmp_path, film_cast):
    return LibraryState(tmp_path / 'task', dict(reference_sha256='f' * 64,
        configuration=dict(film_cast=film_cast, reference_storyboard='v1')), max_requests=80)


def envelope(data):
    return dict(choices=[dict(message=dict(content=json.dumps(data, ensure_ascii=False)))])


def test_zhaodehan_six_cells_with_b_then_a_and_full_provenance():
    cells = [cell(1, 0, 6.1, core=True), cell(2, 6.1, 12, ['B', 'B']),
             cell(3, 12, 14.6, ['B', 'A']), cell(4, 14.6, 18.7, roles=['B']),
             cell(5, 18.7, 22.8, roles=['B']), cell(6, 22.8, 30.03, ['B', 'B', 'B'], roles=['B'], core=True)]
    original = board(cells)
    beats = qfs.storyboard_beats(original)
    # s2's B can share footage with s3's B; s3's A remains an independent beat.
    assert len(beats) == 6
    assert [b['speaker'] for b in beats] == [None, 'B', 'A', None, None, 'B']
    assert [b['id'] for b in beats] == [f'b{i}' for i in range(1, 7)]
    assert beats[1]['reference_evidence'] == dict(source='storyboard', cell_ids=['s2', 's3'],
        start_s=6.1, end_s=14.6, actions=['动作2', '动作3'],
        lines=cells[1]['lines'] + cells[2]['lines'][:1], story_roles=['对峙', '对峙'],
        basis=['visible+asr', 'visible+asr'])
    assert beats[2]['reference_evidence']['lines'] == cells[2]['lines'][1:]
    assert beats[1]['function'] == '动作2；2-0发言；2-1发言；对峙；动作3；3-0发言；对峙'
    assert beats[-1]['core'] and not beats[1]['core']
    assert original == board(cells)  # no input mutation


def test_madongmei_distinct_speakers_in_first_appearance_order():
    beats = qfs.storyboard_beats(board([cell(1, 0, 5, ['A', 'B', 'A'], core=True)]))
    assert [b['speaker'] for b in beats] == ['A', 'B']
    assert [line['intent'] for line in beats[0]['reference_evidence']['lines']] == ['1-0发言', '1-2发言']


@pytest.mark.parametrize('speakers', [[], [None, None]])
def test_silent_or_unknown_speaker_cell_is_one_action_beat(speakers):
    beats = qfs.storyboard_beats(board([cell(1, 0, 5, speakers, roles=[], core=True)]))
    assert len(beats) == 1 and beats[0]['speaker'] is None
    assert len(beats[0]['reference_evidence']['lines']) == len(speakers)


def test_adjacent_merge_uses_roles_set_and_core_union_and_sixteen_second_limit():
    cells = [cell(1, 0, 8, ['A']), cell(2, 8, 16, ['A'], roles=['B', 'A'], core=True),
             cell(3, 16, 18, ['A']), cell(4, 18, 20, ['A'], roles=['A'])]
    beats = qfs.storyboard_beats(board(cells))
    assert [b['reference_evidence']['cell_ids'] for b in beats] == [['s1', 's2'], ['s3'], ['s4']]
    assert beats[0]['core'] and beats[0]['reference_evidence']['end_s'] == 16
    cells[1]['end_s'] = 16.001
    assert len(qfs.storyboard_beats(board(cells[:2]))) == 2


@pytest.mark.parametrize('invalid_count', [0, 1, 2, 3])
def test_storyboard_repairs_and_deterministic_resume(state, tmp_path, monkeypatch, invalid_count):
    valid = board()
    invalid = deepcopy(valid)
    invalid['storyboard'][0]['core'] = False
    replies = [envelope(invalid)] * invalid_count + [envelope(valid)]
    http = Mock(side_effect=replies)
    monkeypatch.setattr(qfs, 'http_post', http)
    media = tmp_path / 'reference.mp4'
    media.write_bytes(b'reference with audio')
    args = ({'theme': '问答'}, asr_for(valid), 4, media)
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'test-key'))
    def invoke():
        if invalid_count == 3:
            with pytest.raises(LibraryStopped, match='^question_first:storyboard_invalid:storyboard:core_required$'):
                runner.build_storyboard(*args)
            return None
        return runner.build_storyboard(*args)
    result = invoke()
    expected_calls = min(invalid_count + 1, 3)
    names = ['qfs_storyboard', 'qfs_storyboard_repair1', 'qfs_storyboard_repair2'][:expected_calls]
    assert [c['name'] for c in state.data['calls']] == names
    assert http.call_count == expected_calls
    first_payload = http.call_args_list[0].args[2]
    assert first_payload['model'] == 'glm-5.3-flash'
    content = first_payload['messages'][0]['content']
    assert content[0] == dict(type='video_url', video_url=dict(url='data:video/mp4;base64,' +
        base64.b64encode(media.read_bytes()).decode()))
    assert content[-1]['text'] == sb.storyboard_prompt(*args[:3])
    for attempt, request in enumerate(http.call_args_list[1:], 1):
        violation = sb.StoryboardViolation('storyboard:core_required', {})
        try:
            sb.validate_storyboard(invalid, duration_s=4, asr_segments=args[1])
        except sb.StoryboardViolation as error:
            violation = error
        assert request.args[2]['messages'][0]['content'][-1]['text'] == sb.storyboard_repair_prompt(
            json.dumps(invalid, ensure_ascii=False), violation) + f'\n本次为第 {attempt} 次修复（最多 2 次）。'
    if result:
        assert result['normalisations'] == [] and result['asr_rejected'] == []
        assert qfs.read_json(state.output / 'storyboard.json') == result
        marker = state.data['artifacts']['reference_storyboard'][0]
        binding = qfs.read_json(marker['path'])
        assert json_sha(binding) == marker['sha256']
        assert binding['sha256'] == qfs.sha256_file(state.output / 'storyboard.json')
    # Reconstruct state/transport as a process restart would; every receipt revalidates.
    resumed = LibraryState(state.output, state.input_lock)
    runner = qfs.QuestionFirstRunner(resumed, [], transport=qfs.DirectTransport(resumed))
    monkeypatch.setattr(qfs, 'http_post', Mock(side_effect=AssertionError('resent')))
    assert invoke() == result
    assert len(resumed.data['calls']) == expected_calls
    qfs.http_post.assert_not_called()


def test_invalid_json_is_repairable(state, tmp_path, monkeypatch):
    media = tmp_path / 'ref.mp4'; media.write_bytes(b'av')
    valid = board()
    http = Mock(side_effect=[dict(choices=[dict(message=dict(content='{bad json'))]), envelope(valid)])
    monkeypatch.setattr(qfs, 'http_post', http)
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'key'))
    runner.build_storyboard({}, asr_for(valid), 4, media)
    assert [c['name'] for c in state.data['calls']] == ['qfs_storyboard', 'qfs_storyboard_repair1']
    assert '{bad json' in http.call_args.args[2]['messages'][0]['content'][-1]['text']


def test_nan_in_unvalidated_storyboard_field_is_repairable_not_fatal(state, tmp_path, monkeypatch):
    """json.loads accepts NaN; write_json(allow_nan=False) would crash while recording."""
    media = tmp_path / 'ref.mp4'; media.write_bytes(b'av')
    valid = board()
    nan_reply = dict(valid, junk=float('nan'))
    http = Mock(side_effect=[envelope(nan_reply), envelope(valid)])
    monkeypatch.setattr(qfs, 'http_post', http)
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'key'))
    result = runner.build_storyboard({}, asr_for(valid), 4, media)
    assert result == sb.validate_storyboard(valid, duration_s=4, asr_segments=asr_for(valid))
    assert [c['name'] for c in state.data['calls']] == ['qfs_storyboard', 'qfs_storyboard_repair1']
    assert 'NaN' in http.call_args.args[2]['messages'][0]['content'][-1]['text']
    assert 'junk' not in qfs.read_json(state.output / 'storyboard.json')


@pytest.mark.parametrize('failed_attempt', [0, 1, 2])
def test_http_error_stops_without_repair_or_resend(state, tmp_path, monkeypatch, failed_attempt):
    valid = board()
    invalid = deepcopy(valid); invalid['storyboard'][0]['core'] = False
    http = Mock(side_effect=[envelope(invalid)] * failed_attempt +
        [urllib.error.HTTPError('https://invalid', 503, 'unavailable', {}, None)])
    monkeypatch.setattr(qfs, 'http_post', http)
    media = tmp_path / 'ref.mp4'; media.write_bytes(b'av')
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'key'))
    for _ in range(2):
        with pytest.raises(qfs.UnknownQFSCall):
            runner.build_storyboard({}, asr_for(valid), 4, media)
    assert http.call_count == failed_attempt + 1
    assert state.data['calls'][-1]['status'] == 'uncertain'


def test_child_reuses_hash_bound_parent_storyboard_without_transport(state, tmp_path):
    valid = sb.validate_storyboard(board(), duration_s=4, asr_segments=asr_for(board()))
    qfs.record_storyboard(state, valid)
    baseline = dict(path=str(state.path), sha256=qfs.sha256_file(state.path))
    child = LibraryState(state.output / 'evaluations' / 'child', dict(reference_sha256='f' * 64,
        configuration=dict(reference_storyboard='v1', parent_baseline=baseline)))
    transport = Mock()
    runner = qfs.QuestionFirstRunner(child, [], transport=transport)
    assert runner.build_storyboard({}, [], 4, 'unused.mp4') == valid
    transport.call.assert_not_called()
    assert qfs.read_json(child.output / 'storyboard.json') == valid
    (state.output / 'storyboard.json').write_text('{}')
    with pytest.raises(LibraryStopped, match='recorded_storyboard_changed'):
        runner.build_storyboard({}, [], 4, 'unused.mp4')


def test_media_transcodes_once_with_audio_and_atomic_output(tmp_path, monkeypatch):
    original = tmp_path / 'large.mp4'
    with original.open('wb') as stream:
        stream.truncate(7_500_001)
    commands = []
    def generate(args):
        commands.append(args)
        Path(args[-1]).write_bytes(b'compressed av')
    monkeypatch.setattr(qfs, 'media_command', generate)
    monkeypatch.setattr(qfs, 'valid_media', lambda p: Path(p).is_file() and Path(p).stat().st_size == 13)
    expected = tmp_path / 'question_first_storyboard/reference_480p.mp4'
    assert qfs.storyboard_media(original, tmp_path) == expected
    assert qfs.storyboard_media(original, tmp_path) == expected
    assert len(commands) == 1 and commands[0][-1] != expected
    assert '-an' not in commands[0] and '0:a?' in commands[0]
    assert commands[0][commands[0].index('-vf') + 1] == 'scale=-2:480'
    assert commands[0][commands[0].index('-crf') + 1] == 30
    assert list(expected.parent.iterdir()) == [expected]


def test_media_over_eight_mib_stops_before_send(tmp_path, monkeypatch):
    original = tmp_path / 'large.mp4'
    with original.open('wb') as stream:
        stream.truncate(9 * 1024 ** 2)
    def generate(args):
        with Path(args[-1]).open('wb') as stream:
            stream.truncate(8 * 1024 ** 2 + 1)
    monkeypatch.setattr(qfs, 'media_command', generate)
    monkeypatch.setattr(qfs, 'valid_media', lambda p: Path(p).is_file())
    with pytest.raises(LibraryStopped, match='storyboard_media_exceeds_8_mib'):
        qfs.storyboard_media(original, tmp_path)
    qfs.http_post.assert_not_called()


def test_ffmpeg_helper_disables_stdin(monkeypatch):
    process = Mock(return_value=Mock(stdout=b''))
    monkeypatch.setattr(subprocess, 'run', process)
    MEDIA_COMMAND(['-i', 'reference.mp4', 'output.mp4'])
    assert '-nostdin' in process.call_args.args[0]


def question_reply(beats):
    return dict(casting={'A': [dict(name='大龙', aliases=[], films=[1])]}, beats=[dict(b,
        must_see=dict(who_relation='说话者可见', action_class='说话', shot_need='脸可见', mouth_open_needed=False),
        dub_can_carry=['重配台词'], equivalents=[], roles=['A']) for b in beats])


def test_run_legacy_request_is_byte_identical_and_storyboard_prompt_is_separate(state):
    reference = dict(visible_evidence=[dict(supports='旧节拍', start_s=0, end_s=4)])
    class Transport:
        def __init__(self):
            self.jobs = []
        def call(self, name, job):
            self.jobs.append((name, deepcopy(job)))
            return qfs.validate_reply(question_reply(job['beats']), job)
    transport = Transport()
    runner = qfs.QuestionFirstRunner(state, [], transport=transport)
    report = runner.run(reference)
    expected = qfs.QUESTIONS + json.dumps(dict(reference=reference, beats=qfs.reference_beats(reference),
        film_cast=state.input_lock['configuration']['film_cast']['characters'], target_style='3d'), ensure_ascii=False)
    assert transport.jobs[0][1]['prompt'].encode() == expected.encode()
    # Captured from HEAD 6c940af, before storyboard integration.
    assert hashlib.sha256(expected.encode()).hexdigest() == '1e79c9c3968a33022c48c89bb62c77d2f0ffa9f3c82a20c03ae95cf3870a66b4'
    assert 'storyboard_merges' not in report
    runner.run(reference, board())
    job = transport.jobs[-1][1]
    assert job['beats'] == qfs.storyboard_beats(board())
    assert '节拍已由 visible_evidence' not in job['prompt']
    assert all(text in job['prompt'] for text in ['reference_storyboard', '逐字复制', '不得增加、删除、合并',
        '带 speaker', '最少必须看见该说话者', 'TTS', 'mouth_open_needed'])
    assert all(b['speaker'] == 'A' and b['core'] for b in qfs.read_json(runner.out / 'report.json')['beats'])


def merge_report(eligible, *, core=(), cell_count=None):
    cells = [cell(i + 1, i * 4, i * 4 + 4, ['A'], core=i + 1 in core) for i in range(len(eligible))]
    # Build each beat separately to isolate gap absorption from same-speaker coalescing.
    beats = []
    for c, has_footage in zip(cells, eligible):
        b = qfs.storyboard_beats(board([c]))[0]
        b.update(id=f'b{len(beats) + 1}', must_see=dict(who_relation='一人可见', mouth_open_needed=False),
                 dub_can_carry=['原意图'], candidates=[])
        if has_footage:
            b['candidates'] = [dict(source_id='src', film=1, film_in_s=10, film_out_s=12,
                verdict='usable', style='3d', confidence='high')]
        beats.append(b)
    return dict(beats=beats, cast_anchors={}, target_style='3d', storyboard_cell_count=cell_count or len(cells))


CATALOG = dict(sources=[dict(source_id='src', duration_s=100, sha256='a' * 64)])


@pytest.mark.parametrize('eligible,core,expected,gaps', [
    ([True, False, True], (), [('b2', 'b1')], []),
    ([False, True, True], (), [('b1', 'b2')], []),
    ([True, False, True], (2,), [], ['b2']),
    ([False, False, False], (), [], ['b1', 'b2', 'b3']),
    ([True, False, False, False, False], (), [('b2', 'b1'), ('b3', 'b1'),
        ('b4', None), ('b5', None)], ['b4', 'b5']),
])
def test_noncore_gap_merges_and_slot_intents(eligible, core, expected, gaps):
    report = merge_report(eligible, core=core)
    search = qfs.to_search_result(report, CATALOG, max_windows=None)
    assert [(m['beat_id'], m['into']) for m in report['storyboard_merges']] == expected
    assert [g['beat_id'] for g in search['question_first_gaps']] == gaps
    absorbed = set()
    for record in report['storyboard_merges']:
        if record['into'] is None:
            assert record['reason'] == 'merge_cap_reached'
            continue
        absorbed.add(record['beat_id'])
        slot = next(s for s in search['slots'] if s['slot_id'] == record['into'])
        origin = next(b for b in report['beats'] if b['id'] == record['beat_id'])
        assert record['reason'] == 'no_eligible_footage_non_core'
        assert set(record['cell_ids']) <= set(slot['absorbed_cell_ids']) <= set(slot['cell_ids'])
        assert origin['reference_evidence']['lines'][0]['intent'] in slot['coverage']['audio_goal']
        assert '原意图' in slot['coverage']['audio_goal']
    assert not absorbed & {s['slot_id'] for s in search['slots']}
    before = deepcopy(report)
    assert qfs.to_search_result(report, CATALOG, max_windows=None) == search
    assert report == before


def test_nearest_previous_beats_following_and_partial_or_excluded_neighbours_do_not_count():
    report = merge_report([True, False, False, True], core=(2,))
    qfs.to_search_result(report, CATALOG, max_windows=None)
    assert report['storyboard_merges'][0]['into'] == 'b1'
    report = merge_report([True, False, True])
    report['beats'][0]['candidates'][0]['verdict'] = 'partial'
    report['beats'][2]['candidates'][0]['style'] = '2d_stylized'
    qfs.to_search_result(report, CATALOG, max_windows=None)
    assert report['storyboard_merges'] == []
    assert len(report['question_first_gaps']) == 3


def test_cap_counts_source_cells_not_beat_count():
    report = merge_report([True, False, False, False, True], cell_count=5)
    report['beats'][1]['reference_evidence']['cell_ids'] = ['s2', 's3', 's4']
    qfs.to_search_result(report, CATALOG, max_windows=None)
    assert report['storyboard_merges'][0]['reason'] == 'merge_cap_reached'
    assert report['question_first_gaps'][0]['beat_id'] == 'b2'


@pytest.mark.parametrize('old_cache,with_lock', [(False, False), (True, False), (True, True)])
def test_config_gate_and_unchanged_legacy_lock(tmp_path, monkeypatch, film_cast, old_cache, with_lock):
    from omni_story.library import film_cast as cast_module
    monkeypatch.setattr(cast_module, 'recorded_film_cast', lambda *a: film_cast)
    cache = tmp_path / 'shots.json'; cache.write_text('{}')
    monkeypatch.setattr(qfs, 'load_shot_cache', lambda *a: [dict(_cache_path=str(cache), frames=[], sheets=[])])
    signature = [dict(path=str(cache), sha256=qfs.sha256_file(cache), media=[])]
    config = dict(material_search='question_first', creative_audio=True, shot_cache=str(tmp_path),
                  content_ranges=dict(sources={}))
    if old_cache:
        config['question_first_cache'] = signature
    if with_lock:
        legacy = dict(config, film_cast=film_cast, target_style='3d')
        locked = LibraryState(tmp_path, dict(configuration=deepcopy(legacy)))
        # Preflight may reconstruct config without the cache pin; the old lock wins.
        config.pop('question_first_cache')
        config['reference_storyboard'] = 'v1'
    qfs.configure(config, dict(sources=[]), tmp_path)
    assert config.get('reference_storyboard') == (None if old_cache else 'v1')
    if old_cache:
        assert 'reference_storyboard' not in config
    else:
        assert config['reference_storyboard'] == 'v1'
    if with_lock:
        assert config == legacy
        LibraryState(tmp_path, dict(configuration=config))  # immutable lock still opens
        assert locked.input_lock == dict(configuration=config)


def test_pipeline_pins_storyboard_and_carries_it_and_merges_into_reading(tmp_path, monkeypatch, film_cast):
    from omni_story.library import pipeline, prompts
    reference = tmp_path / 'reference.mp4'; reference.write_bytes(b'reference av')
    ref = dict(source_id='ref', path=str(reference), sha256=qfs.sha256_file(reference),
               duration_s=4, audio_stream_index=None)
    source = dict(ref, source_id='src', sha256='a' * 64)
    monkeypatch.setattr(pipeline, '_catalog', lambda path, output: deepcopy(
        dict(sources=[ref] if path == reference else [source])))
    monkeypatch.setattr(pipeline, 'prepare_window', lambda *a, **k: dict(path=str(reference)))
    monkeypatch.setattr(pipeline, 'create_contact_sheet', lambda *a, **k: dict(path=str(reference)))
    def configure(config, catalog, output):
        config.update(reference_storyboard='v1', film_cast=film_cast)
        return []
    monkeypatch.setattr(qfs, 'configure', configure)
    reading = json.loads(prompts.reference_prompt(ref['sha256'], 4).split('输出：\n', 1)[1])
    reading['strategy'] = dict(adaptation_mode='reinterpret', speech_mode='none', timing_driver='action')
    data = board([cell(1, 0, 4, core=True)])
    def respond(url, headers, payload):
        prompt = payload['messages'][0]['content'][-1]['text']
        if prompt.startswith('完整观看'):
            return envelope(data)
        return envelope(question_reply(qfs.storyboard_beats(data)))
    monkeypatch.setattr(qfs, 'http_post', Mock(side_effect=respond))
    monkeypatch.setenv('Z_AI_API_KEY', 'fixture')
    class Model:
        def __init__(self, state):
            self.state = state
        def call(self, name, prompt, media, validator, **kwargs):
            assert name == 'reference'
            value = deepcopy(reading)
            validator(value)
            return value
    output = tmp_path / 'task'
    with pytest.raises(LibraryStopped, match='^question_first:material_gap$'):
        pipeline.execute(reference, tmp_path, output, asr=False, model_factory=Model,
            provider_config=dict(material_search='question_first', creative_audio=True))
    saved = qfs.read_json(output / 'reference_reading.json')
    assert saved['reference_storyboard'] == qfs.read_json(output / 'storyboard.json')
    assert saved['storyboard_merges'] == []
    assert saved['visible_evidence'] == reading['visible_evidence']
    assert saved['question_first_gaps'][0]['beat_id'] == 'b1'
    ledger = qfs.read_json(output / 'library_state.json')
    assert 'reference_storyboard' in ledger['artifacts']
    assert [c['name'] for c in ledger['calls']] == ['qfs_storyboard', 'qfs_questions']


def test_unattributed_line_rides_with_first_speaker_of_mixed_cell():
    from omni_story.library.question_first import storyboard_beats
    cell = dict(id='s1', start_s=0, end_s=5, roles=['A', 'B'], action='两人对视', story_role='铺垫',
                basis='asr', core=True, lines=[
                    dict(speaker='A', intent='问', ref_asr_ids=['a']),
                    dict(speaker=None, intent='画外音', ref_asr_ids=['n']),
                    dict(speaker='B', intent='答', ref_asr_ids=['b'])])
    beats = storyboard_beats(dict(storyboard=[cell]))
    assert [b['speaker'] for b in beats] == ['A', 'B']
    assert [l['intent'] for l in beats[0]['reference_evidence']['lines']] == ['问', '画外音']
    assert '画外音' in beats[0]['function']
