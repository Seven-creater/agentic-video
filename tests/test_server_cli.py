"""Server command wiring and migration integrity; no network or real keys."""
from copy import deepcopy
import json

import pytest

from omni_story.library import server_cli as cli
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, json_sha, write_json


@pytest.fixture
def home(tmp_path):
    write_json(tmp_path / 'server.json', {'project_root': str(tmp_path),
        'mcp_package_root': str(tmp_path / 'mcp'), 'opencode_executable': '/fake/opencode'})
    write_json(tmp_path / 'credentials.json', {'Z_AI_API_KEY': 'synthetic-key'})
    (tmp_path / 'credentials.json').chmod(0o600)
    return tmp_path


def test_start_uses_actual_module_and_does_not_put_key_in_command(home, monkeypatch, capsys):
    ref = home / 'ref.mp4'; ref.write_bytes(b'fixture')
    library = home / 'videos'; library.mkdir(); (library / 'movie.mp4').write_bytes(b'fixture')
    seen = []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda argv, output, cwd: seen.append(argv) or {'state': 'starting'})
    assert cli.main(['--home', str(home), 'start', '--reference', str(ref), '--library', str(library),
                     '--output', str(home / 'new-run')]) == 0
    assert seen[0][1:3] == ['-m', 'omni_story.library.server_cli']
    assert 'synthetic-key' not in repr(seen)
    assert 'synthetic-key' not in capsys.readouterr().out


def test_start_forwards_explicit_functional_test_to_detached_worker(home, monkeypatch):
    ref = home / 'ref.mp4'; ref.write_bytes(b'fixture')
    library = home / 'videos'; library.mkdir(); (library / 'movie.mp4').write_bytes(b'fixture')
    seen = []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda argv, output, cwd: seen.append(argv) or {'state': 'starting'})

    assert cli.main(['--home', str(home), 'start', '--reference', str(ref), '--library', str(library),
                     '--output', str(home / 'new-run'), '--functional-test']) == 0

    assert seen[0].count('--functional-test') == 1
    assert '--rough-task' not in seen[0]


def test_reference_strategy_is_forwarded_and_loaded_without_changing_reference_seed(home, monkeypatch):
    from omni_story.library import clean_chain
    ref = home / 'ref.mp4'; ref.write_bytes(b'fixture')
    library = home / 'videos'; library.mkdir(); (library / 'movie.mp4').write_bytes(b'fixture')
    override = home / 'strategy.json'
    strategy = dict(adaptation_mode='reinterpret', speech_mode='mixed', timing_driver='speech')
    speech_form = '角色视角对话为主，可用简短旁白连接。'
    write_json(override, dict(reference_sha256=sha256_file(ref), strategy=strategy, speech_form=speech_form))
    parent = home / 'parent'
    call = dict(id='glm_002_plan_0_repair', name='plan_0_repair', status='received')
    write_json(parent / 'library_state.json', dict(task_id='parent', request_count=1, calls=[call],
        input_lock=dict(configuration=dict(prior_requests=7))))
    write_json(parent / 'watched_windows.json', [])
    write_json(parent / '.omni-server/job.json', {'state': 'failed'})
    commands, executed = [], []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda argv, *args:
                        commands.append(argv) or {'state': 'starting'})
    monkeypatch.setattr(clean_chain, 'execute', lambda *args, **options:
                        executed.append(options) or {'usage': {'requests': 0}})
    monkeypatch.setattr(cli.server_jobs, 'status', lambda output: {'state': 'failed'})
    monkeypatch.setattr(cli.server_jobs, '_group_running', lambda pid: False)
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'synthetic-audio-key')
    options = ['--reference', str(ref), '--library', str(library),
               '--output', str(parent / 'evaluations/new-run'), '--parent-task', str(parent),
               '--reuse-plan', '--creative-audio', '--reference-strategy', str(override)]
    assert cli.main(['--home', str(home), 'start', *options]) == 0
    assert commands[0][commands[0].index('--reference-strategy') + 1] == str(override.resolve())
    assert '--reuse-plan' in commands[0]
    assert cli.main(['--home', str(home), '_run', *options]) == 0
    assert executed[0]['provider_config']['reference_strategy'] == strategy
    assert executed[0]['provider_config']['reference_speech_form'] == speech_form
    assert executed[0]['reference_seed'] is None
    config = executed[0]['provider_config']
    assert config['received_plan_seed']['call_id'] == call['id']
    assert config['received_plan_seed']['ledger']['sha256'] == sha256_file(parent / 'library_state.json')
    assert config['received_visual_seed']['windows']['sha256'] == sha256_file(parent / 'watched_windows.json')
    assert config['prior_requests'] == 8
    write_json(parent / 'library_state.json', dict(calls=[{**call, 'status': 'uncertain'}]))
    with pytest.raises(LibraryStopped, match='reuse_plan_parent_results_not_known'):
        cli.received_plan_config(parent)


def test_reference_strategy_rejects_other_video_and_invalid_mode_before_start(home, monkeypatch):
    ref = home / 'ref.mp4'; ref.write_bytes(b'fixture')
    library = home / 'videos'; library.mkdir(); (library / 'movie.mp4').write_bytes(b'fixture')
    override = home / 'strategy.json'
    strategy = dict(adaptation_mode='reinterpret', speech_mode='mixed', timing_driver='speech')
    write_json(override, dict(reference_sha256='0' * 64, strategy=strategy))
    monkeypatch.setattr(cli.server_jobs, 'start', lambda *args: pytest.fail('invalid override was dispatched'))
    options = ['--home', str(home), 'start', '--reference', str(ref), '--library', str(library),
               '--output', str(home / 'new-run'), '--reference-strategy', str(override)]
    with pytest.raises(LibraryStopped, match='reference_strategy_video_mismatch'):
        cli.main(options)
    write_json(override, dict(reference_sha256=sha256_file(ref), strategy={**strategy, 'speech_mode': 'invalid'}))
    with pytest.raises(ValueError, match='invalid_speech_mode'):
        cli.main(options)


@pytest.mark.parametrize('functional_test', [False, True])
def test_worker_locks_functional_test_policy_without_reusing_a_rough(home, monkeypatch, functional_test):
    from omni_story.library import clean_chain

    ref = home / 'ref.mp4'; ref.write_bytes(b'fixture')
    library = home / 'videos'; library.mkdir(); (library / 'movie.mp4').write_bytes(b'fixture')
    seen = []

    def execute(reference, movie_library, output, **options):
        assert reference == ref.resolve() and movie_library == library.resolve()
        seen.append(options)
        return {'usage': {'requests': 0}}

    monkeypatch.setattr(clean_chain, 'execute', execute)
    arguments = ['--home', str(home), '_run', '--reference', str(ref), '--library', str(library),
                 '--output', str(home / 'new-run')]
    if functional_test:
        arguments.append('--functional-test')

    assert cli.main(arguments) == 0

    assert (seen[0]['provider_config'].get('quality_policy') ==
            'functional_test_keep_negative_reviews') is functional_test
    assert 'rough_task' not in seen[0]
    assert seen[0]['reference_seed'] is None


@pytest.mark.parametrize('partial_reference', [True, False])
def test_partial_media_blocks_start_before_a_job(home, monkeypatch, partial_reference):
    ref = home / ('ref.mp4.part' if partial_reference else 'ref.mp4'); ref.write_bytes(b'fixture')
    library = home / 'videos'; library.mkdir()
    (library / ('movie.mp4' if partial_reference else 'movie.mp4.part')).write_bytes(b'fixture')
    monkeypatch.setattr(cli.server_jobs, 'start', lambda *args: pytest.fail('partial media submitted'))
    with pytest.raises(SystemExit):
        cli.main(['--home', str(home), 'start', '--reference', str(ref), '--library', str(library),
                  '--output', str(home / 'new-run')])


def history_fixture(home):
    content = {'reference': {'theme': 'fixture'}, 'editing_reference': {'methods': []}}
    request = {'arguments': {'prompt': 'synthetic'}}
    response = {'result': {'content': [{'type': 'text', 'text': json.dumps(content)}]}}
    call = {'id': 'glm_060_fixture', 'status': 'received', 'request_sha256': json_sha(request),
            'response_sha256': json_sha(response)}
    unknown = {'id': 'glm_131_fixture', 'status': 'uncertain'}
    seed = {'source_call_id': call['id'], 'request_sha256': call['request_sha256'],
            'response_sha256': call['response_sha256'], 'full_response': content}
    record = {'policy': 'server_append_only_migration_v1', 'baseline_requests': 2,
              'historical_calls': [call, unknown], 'baseline_calls_sha256': json_sha([call, unknown]),
              'reference_seed': seed, 'reference_seed_sha256': json_sha(seed),
              'seed_request': request, 'seed_response': response,
              'unknown_inputs': [{'call_id': unknown['id']}]}
    path = home / 'history.json'; write_json(path, record)
    config = {'history_file': str(path), 'history_sha256': sha256_file(path)}
    return config, record


def test_history_is_verified_without_resolving_old_windows_paths(home):
    config, record = history_fixture(home)
    assert cli.load_history(config) == record


@pytest.mark.parametrize('field', ['historical_calls', 'reference_seed', 'seed_response', 'unknown_inputs'])
def test_migration_cannot_change_history_or_seed_even_with_new_outer_hash(home, field):
    config, record = history_fixture(home)
    value = deepcopy(record)
    if field == 'historical_calls': value[field][0]['status'] = 'uncertain'
    elif field == 'reference_seed': value[field]['full_response']['reference']['theme'] = 'changed'
    elif field == 'seed_response': value[field]['result']['content'][0]['text'] = '{}'
    else: value[field] = []
    write_json(config['history_file'], value)
    config['history_sha256'] = sha256_file(config['history_file'])
    with pytest.raises(LibraryStopped): cli.load_history(config)


def test_configure_saves_key_privately_without_sending_it(home, monkeypatch, capsys):
    monkeypatch.setattr(cli.getpass, 'getpass', lambda _: 'another-synthetic-key')
    cli.configure(home)
    assert cli.credential(home) == 'another-synthetic-key'
    assert 'another-synthetic-key' not in capsys.readouterr().out


def test_received_reference_cache_is_checked_against_raw_response_and_video(home):
    ref = home / 'reference.mp4'; ref.write_bytes(b'fixture')
    reading = {'reference_sha256': sha256_file(ref), 'theme': 'fixture'}
    request = {'arguments': {'prompt': 'original model reference observation'}}
    response = {'result': {'content': [{'type': 'text', 'text': json.dumps(reading)}]}}
    call = {'id': 'glm_001_reference', 'status': 'received', 'request_sha256': json_sha(request),
            'response_sha256': json_sha(response)}
    cache = home / 'cache.json'; write_json(cache, {'call': call, 'request': request, 'response': response})
    seed = cli.reference_cache(cache, ref)
    assert seed['full_response'] == {'reference': reading}
    response['result']['content'][0]['text'] = '{}'
    write_json(cache, {'call': call, 'request': request, 'response': response})
    with pytest.raises(LibraryStopped, match='original_record_changed'):
        cli.reference_cache(cache, ref)


def test_parent_evaluation_preserves_all_prior_live_ledgers(home):
    parent = home / 'parent'; parent.mkdir()
    state = {'task_id': 'original', 'calls': [{'status': 'received'} for _ in range(36)], 'request_count': 36,
             'input_lock': {'configuration': {'prior_requests': 274}}}
    write_json(parent / 'library_state.json', state)
    child = {**state, 'task_id': 'restored', 'calls': [{'status': 'received'} for _ in range(71)], 'request_count': 71}
    write_json(parent / 'artifacts/original_method/library_state.json', child)
    baseline = cli.parent_baseline(parent, parent / 'evaluations/clean-chain')
    assert baseline['request_count'] == 107
    assert baseline['prior_requests'] == 274
    assert len(baseline['linked_ledgers']) == 2
    with pytest.raises(LibraryStopped, match='inside_parent_evaluations'):
        cli.parent_baseline(parent, home / 'sibling-reset')


def test_nested_parent_inherits_unknown_scope_and_counts_ancestors_once(home):
    ancestor = home / 'ancestor'; ancestor.mkdir()
    original = {'task_id': 'historical', 'calls': [{'status': 'received'} for _ in range(107)],
        'request_count': 107, 'input_lock': {'configuration': {'prior_requests': 274}}}
    write_json(ancestor / 'library_state.json', original)
    inherited = {'path': str(ancestor / 'library_state.json'),
        'sha256': sha256_file(ancestor / 'library_state.json'), 'task_id': 'historical', 'request_count': 107}
    parent = ancestor / 'evaluations/failed'; parent.mkdir(parents=True)
    media = parent / 'media/window.mp4'; media.parent.mkdir(); media.write_bytes(b'original unknown bytes')
    scope = dict(kind='continuous_window', source_sha256='a' * 64, source_start_s=100, source_end_s=110)
    write_json(media.parent / 'lineage.json', {**scope, 'spec': scope,
        'path': str(media), 'sha256': sha256_file(media)})
    request = {'tool': 'analyze_video', 'arguments': {'video_source': str(media), 'prompt': 'original only'},
        'media_sha256': sha256_file(media)}
    lost = {'id': 'glm_016_fine_1234567812345678', 'name': 'fine_1234567812345678',
        'status': 'uncertain', 'request_sha256': json_sha(request)}
    write_json(parent / 'calls' / lost['id'] / 'request.json', request)
    state = {'task_id': 'failed', 'calls': [{'status': 'received'} for _ in range(15)] + [lost],
        'request_count': 16, 'input_lock': {'configuration': {'prior_requests': 381,
            'parent_baseline': {'linked_ledgers': [inherited]}}}}
    write_json(parent / 'library_state.json', state)
    before = (parent / 'library_state.json').read_bytes()
    baseline = cli.parent_baseline(parent, parent / 'evaluations/new')
    assert baseline['prior_requests'] + baseline['request_count'] == 397
    assert len(baseline['linked_ledgers']) == 2
    assert baseline['unknown_inputs'][0]['scope'] == scope
    assert baseline['unknown_inputs'][0]['media_sha256'] == sha256_file(media)
    assert baseline['unknown_inputs'][0]['lineage_sha256'] == sha256_file(media.parent / 'lineage.json')
    assert (parent / 'library_state.json').read_bytes() == before
    write_json(ancestor / 'library_state.json', {**original, 'request_count': 0})
    with pytest.raises(LibraryStopped, match='ancestor_ledger_changed'):
        cli.parent_baseline(parent, parent / 'evaluations/another')


def test_parent_baseline_links_audio_child_vision_but_excludes_tts_and_av_services(home):
    parent = home / 'parent'
    state = dict(task_id='parent', request_count=3, calls=[dict(status='received')] * 3,
                 input_lock=dict(configuration=dict(prior_requests=20)))
    write_json(parent / 'library_state.json', state)
    root = parent / 'skill_finecut/creative_audio'
    write_json(root / 'library_state.json', {**state, 'task_id': 'audio', 'request_count': 4,
                                           'calls': [dict(status='received')] * 4})
    write_json(root / 'tts/utt/state.json', dict(status='received', request_count=1))
    write_json(root / 'av_review_v0/state.json', dict(status='received', request_count=1))

    baseline = cli.parent_baseline(parent, parent / 'evaluations/next')

    assert baseline['request_count'] == 7 and baseline['prior_requests'] == 20
    assert len(baseline['linked_ledgers']) == 2
    assert {row['task_id'] for row in baseline['linked_ledgers']} == {'parent', 'audio'}


@pytest.mark.parametrize('target', ['request', 'media', 'lineage'])
def test_parent_unknown_missing_scope_requires_authentic_media_mapping(home, target):
    parent = home / 'parent'; parent.mkdir()
    media = parent / 'proxy/window.mp4'; media.parent.mkdir(); media.write_bytes(b'actual unknown proxy')
    scope = dict(kind='continuous_window', source_sha256='a' * 64, source_start_s=1, source_end_s=4)
    mapping = {**scope, 'spec': scope, 'path': str(media), 'sha256': sha256_file(media)}
    write_json(media.parent / 'lineage.json', mapping)
    request = {'tool': 'analyze_video', 'arguments': {'video_source': str(media), 'prompt': 'original'},
        'media_sha256': sha256_file(media)}
    call = {'id': 'glm_001_lost', 'status': 'uncertain', 'request_sha256': json_sha(request)}
    write_json(parent / 'calls/glm_001_lost/request.json', request)
    write_json(parent / 'library_state.json', {'task_id': 'parent', 'calls': [call], 'request_count': 1,
        'input_lock': {'configuration': {}}})
    if target == 'request':
        write_json(parent / 'calls/glm_001_lost/request.json', {**request, 'media_sha256': 'b' * 64})
    elif target == 'media':
        media.write_bytes(b'changed')
    else:
        write_json(media.parent / 'lineage.json', {**mapping, 'source_end_s': 5})
    with pytest.raises(LibraryStopped, match='parent_unknown_request_changed|parent_observation_'):
        cli.parent_baseline(parent, parent / 'evaluations/new')


def test_parallel_library_counts_common_history_once(home):
    ancestor = home / 'ancestor/library_state.json'
    write_json(ancestor, dict(task_id='history', request_count=57,
        calls=[dict(status='received')] * 57, input_lock=dict(configuration={})))
    inherited = dict(path=str(ancestor.resolve()), sha256=sha256_file(ancestor))
    tasks = [home / f'film_{i}' for i in range(3)]
    unknown = dict(call_id='historical_unknown', media_sha256='a' * 64)
    for i, (task, count) in enumerate(zip(tasks, (15, 37, 12))):
        write_json(task / 'worker.json', dict(status='completed'))
        write_json(task / 'library_state.json', dict(task_id=f'film_{i}', request_count=count,
            calls=[dict(status='received')] * count, input_lock=dict(configuration=dict(
                prior_requests=766, parent_baseline=dict(linked_ledgers=[inherited]),
                unknown_inputs=[unknown]))))
    baseline = cli.parent_baseline(tasks[0], tasks[0] / 'evaluations/delivery',
                                   [tasks[1], tasks[2], tasks[1]])
    assert baseline['prior_requests'] + baseline['request_count'] == 830
    assert len(baseline['linked_ledgers']) == 4
    continued = tasks[2] / 'evaluations/continued'
    write_json(continued / 'worker.json', dict(status='completed'))
    write_json(continued / 'library_state.json', dict(task_id='continued', request_count=2,
        calls=[dict(status='received')] * 2, input_lock=dict(configuration=dict(prior_requests=778,
            predecessor=dict(path=str((tasks[2] / 'library_state.json').resolve()),
                sha256=sha256_file(tasks[2] / 'library_state.json'))))))
    resumed = cli.parent_baseline(tasks[0], tasks[0] / 'evaluations/delivery', [tasks[1], continued])
    assert resumed['prior_requests'] + resumed['request_count'] == 832
    assert len(resumed['linked_ledgers']) == 5

    main_continued = tasks[0] / 'evaluations/continued'
    write_json(main_continued / 'worker.json', dict(status='completed'))
    write_json(main_continued / 'library_state.json', dict(task_id='main_continued', request_count=23,
        calls=[dict(status='received')] * 23, input_lock=dict(configuration=dict(prior_requests=781,
            predecessor=dict(path=str((tasks[0] / 'library_state.json').resolve()),
                             sha256=sha256_file(tasks[0] / 'library_state.json'))))))
    third_continued = continued / 'evaluations/full'
    write_json(third_continued / 'worker.json', dict(status='completed'))
    write_json(third_continued / 'library_state.json', dict(task_id='third_continued', request_count=23,
        calls=[dict(status='received')] * 23, input_lock=dict(configuration=dict(prior_requests=780,
            predecessor=dict(path=str((continued / 'library_state.json').resolve()),
                             sha256=sha256_file(continued / 'library_state.json'))))))
    joined = cli.parent_baseline(main_continued, main_continued / 'evaluations/delivery',
                                 [tasks[1], third_continued])
    assert joined['prior_requests'] == 709
    assert joined['request_count'] == 169 and len(joined['linked_ledgers']) == 7
    assert joined['prior_requests'] + joined['request_count'] == 878
    assert joined['unknown_inputs'] == [unknown]

    other_path = tasks[1] / 'library_state.json'
    original = json.loads(other_path.read_text())
    different_residual = deepcopy(original)
    different_residual['input_lock']['configuration']['prior_requests'] += 1
    write_json(other_path, different_residual)
    with pytest.raises(LibraryStopped, match='parallel_library_requires_same_history'):
        cli.parent_baseline(main_continued, main_continued / 'evaluations/delivery', [tasks[1], third_continued])
    conflicting_sha = deepcopy(original)
    conflicting_sha['input_lock']['configuration']['parent_baseline']['linked_ledgers'][0]['sha256'] = '0' * 64
    write_json(other_path, conflicting_sha)
    with pytest.raises(LibraryStopped, match='ancestor_ledger_changed'):
        cli.parent_baseline(main_continued, main_continued / 'evaluations/delivery', [tasks[1], third_continued])
    write_json(other_path, original)
    write_json(tasks[2] / 'worker.json', dict(status='running'))
    with pytest.raises(LibraryStopped, match='parallel_library_not_completed'):
        cli.parent_baseline(tasks[0], tasks[0] / 'evaluations/delivery', tasks[1:])


def test_parent_baseline_skips_unknown_free_qfs_calls(home):
    # Real stop (801d00e): the parent had an unknown qfs_close call (no 'arguments'),
    # and parent_baseline crashed with KeyError: 'arguments' before the child started.
    parent = home / 'qfs_parent'; parent.mkdir()
    request = {'provider': 'qfs_direct_https_v1', 'stage': 'question_first',
               'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
               'payload': {'model': 'glm-5.3-flash', 'messages': []}}
    lost = {'id': 'glm_168_qfs_close_25', 'name': 'qfs_close_25', 'status': 'uncertain',
            'request_sha256': json_sha(request)}
    write_json(parent / 'calls' / lost['id'] / 'request.json', request)
    write_json(parent / 'library_state.json', {'task_id': 'qfs_parent', 'calls': [lost],
        'request_count': 1, 'input_lock': {'configuration': {'prior_requests': 0}}})
    baseline = cli.parent_baseline(parent, parent / 'evaluations/next')
    assert baseline['unknown_inputs'] == []
