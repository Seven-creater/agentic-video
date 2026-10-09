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
    state = {'task_id': 'original', 'calls': [], 'request_count': 36,
             'input_lock': {'configuration': {'prior_requests': 274}}}
    write_json(parent / 'library_state.json', state)
    child = {**state, 'task_id': 'restored', 'request_count': 71}
    write_json(parent / 'artifacts/original_method/library_state.json', child)
    baseline = cli.parent_baseline(parent, parent / 'evaluations/clean-chain')
    assert baseline['request_count'] == 107
    assert baseline['prior_requests'] == 274
    assert len(baseline['linked_ledgers']) == 2
    with pytest.raises(LibraryStopped, match='inside_parent_evaluations'):
        cli.parent_baseline(parent, home / 'sibling-reset')
