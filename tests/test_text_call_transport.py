"""Offline text transport boundaries; no agent or model is launched."""
import errno
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import clean_chain, pipeline
from omni_story.library.state import LibraryStopped
from test_protocol_feedback import harness


@pytest.fixture
def model(tmp_path, monkeypatch):
    model, _, _ = harness(tmp_path, monkeypatch, [], provider=clean_chain.PROVIDER['provider'])
    model.queue = model.output / 'mcp_queue'
    model.queue.mkdir()
    model.executable, model.package_root, model.timeout_s = '/fake/opencode', tmp_path, 1
    return model


def emit_reply(kwargs):
    kwargs['stdout'].write(b'{"type":"text","part":{"text":"{\\"ok\\":true}"}}\n')
    return SimpleNamespace(returncode=0)


@pytest.mark.parametrize('prompt', ['a' * 100_000, 'a' * 100_001, '汉' * 70_000])
def test_prompt_argv_byte_bound_and_config_file_transport(model, monkeypatch, prompt):
    sent = []
    def run(argv, **kwargs):
        assert all(len(arg.encode('utf-8')) <= pipeline.TEXT_ARGV_MAX_BYTES for arg in argv)
        if len(prompt.encode('utf-8')) > pipeline.TEXT_ARGV_MAX_BYTES:
            assert sum(len(arg.encode('utf-8')) for arg in argv) < 1000
            assert 'OPENCODE_CONFIG_CONTENT' not in kwargs['env']
            assert kwargs['env']['OPENCODE_CONFIG'] == str(Path(kwargs['cwd']) / 'opencode.json')
            config = json.loads((Path(kwargs['cwd']) / 'opencode.json').read_text())
            assert config['agent']['silent-check']['prompt'].endswith('\n' + prompt)
        else:
            assert argv[-1] == prompt
            config = json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT'])
        assert config['mcp'] == {} and config['permission'] == {'*': 'deny'}
        sent.append(argv)
        return emit_reply(kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
    assert model.call_text('silent_review_0', prompt, lambda value: None) == {'ok': True}
    assert model.call_text('silent_review_0', 'ignored on replay', lambda value: None) == {'ok': True}
    assert len(sent) == 1
    call = model.state.data['calls'][0]
    request = json.loads((model.output / 'calls' / call['id'] / 'request.json').read_text())
    assert request['arguments']['prompt'] == prompt


@pytest.mark.parametrize('number', [errno.E2BIG, errno.ENOENT, errno.EACCES])
def test_spawn_oserror_is_failed_known_and_never_replayed(model, monkeypatch, number):
    sent = []
    def spawn(*args, **kwargs):
        sent.append(args)
        raise OSError(number, 'simulated spawn failure')
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(LibraryStopped, match='text_check_launch_failed:'):
        model.call_text('silent_review_0', 'prompt', lambda value: None)
    call = model.state.data['calls'][0]
    assert call['status'] == 'failed_known'
    failure = json.loads((model.output / 'calls' / call['id'] / 'failure.json').read_text())
    assert failure['uncertain'] is False
    with pytest.raises(LibraryStopped, match='recorded_request_not_received_no_replay:'):
        model.call_text('silent_review_0', 'prompt', lambda value: None)
    assert len(sent) == 1


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'read_oserror'])
def test_post_spawn_failures_stay_uncertain(model, monkeypatch, failure):
    def run(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args[0], 1)
        if failure == 'exit':
            raise subprocess.CalledProcessError(1, args[0])
        return emit_reply(kwargs)
    monkeypatch.setattr(subprocess, 'run', run)
    read_text = Path.read_text
    def read(path, *args, **kwargs):
        if path.name == 'agent_events.jsonl':
            raise OSError(errno.EIO, 'simulated post-spawn read failure')
        return read_text(path, *args, **kwargs)
    if failure == 'read_oserror':
        monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(LibraryStopped, match='text_check_outcome_unknown_no_replay:'):
        model.call_text('silent_review_0', 'prompt', lambda value: None)
    assert model.state.data['calls'][0]['status'] == 'uncertain'


def test_oserror_inside_running_child_stays_uncertain(model, monkeypatch):
    class Process:
        pid = 123
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def communicate(self, *args, **kwargs):
            raise OSError(errno.EIO, 'simulated failure after child creation')
        def kill(self):
            pass
    monkeypatch.setattr(subprocess, 'Popen', Process)
    with pytest.raises(LibraryStopped, match='text_check_outcome_unknown_no_replay:'):
        model.call_text('silent_review_0', 'prompt', lambda value: None)
    assert model.state.data['calls'][0]['status'] == 'uncertain'
