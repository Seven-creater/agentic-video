"""Bounded local rename recovery; no model request or real ledger is used."""
import json

import pytest

from omni_story.library import state


def windows_error(code):
    error = PermissionError('synthetic Windows replace failure')
    error.winerror = code
    return error


@pytest.mark.parametrize('code', [5, 32, 33])
def test_transient_windows_replace_preserves_old_file_then_commits_same_json(tmp_path, monkeypatch, code):
    target = tmp_path / 'state.json'
    old = b'{"historical":"untouched"}'
    target.write_bytes(old)
    value = {'new': ['完整内容', 1.0], 'valid': True}
    original_replace = state.os.replace
    attempts, delays = [], []
    def replace(temporary, destination):
        assert target.read_bytes() == old
        assert json.loads(temporary.read_text(encoding='utf-8')) == value
        attempts.append(temporary)
        if len(attempts) < 3:
            raise windows_error(code)
        original_replace(temporary, destination)
    monkeypatch.setattr(state.os, 'replace', replace)
    monkeypatch.setattr(state.time, 'sleep', delays.append)
    state.write_json(target, value)
    assert len(set(attempts)) == 1 and len(attempts) == 3
    assert delays == [.02, .04]
    assert json.loads(target.read_text(encoding='utf-8')) == value
    assert not list(tmp_path.glob('*.tmp'))


def test_persistent_windows_replace_rethrows_last_real_error_and_keeps_complete_temp(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    old = b'{"historical":"untouched"}'
    target.write_bytes(old)
    attempts, delays, errors = [], [], []
    def replace(temporary, destination):
        assert target.read_bytes() == old
        attempts.append(temporary)
        error = windows_error(5)
        errors.append(error)
        raise error
    monkeypatch.setattr(state.os, 'replace', replace)
    monkeypatch.setattr(state.time, 'sleep', delays.append)
    with pytest.raises(PermissionError) as caught:
        state.write_json(target, {'pending': 'exact valid JSON'})
    assert caught.value is errors[-1]
    assert len(attempts) == 6 and len(set(attempts)) == 1
    assert delays == [.02, .04, .08, .16, .32] and sum(delays) < 1
    assert target.read_bytes() == old
    assert list(tmp_path.glob('*.tmp')) == [attempts[0]]
    assert json.loads(attempts[0].read_text(encoding='utf-8')) == {'pending': 'exact valid JSON'}


@pytest.mark.parametrize('error', [OSError('ordinary filesystem failure'), windows_error(3)])
def test_other_replace_errors_are_not_retried_or_swallowed(tmp_path, monkeypatch, error):
    target = tmp_path / 'state.json'
    old = b'{"historical":"untouched"}'
    target.write_bytes(old)
    attempts = []
    def replace(temporary, destination):
        attempts.append(temporary)
        raise error
    monkeypatch.setattr(state.os, 'replace', replace)
    monkeypatch.setattr(state.time, 'sleep', lambda seconds: pytest.fail('unexpected filesystem retry'))
    with pytest.raises(OSError) as caught:
        state.write_json(target, {'saved': True})
    assert caught.value is error and len(attempts) == 1
    assert target.read_bytes() == old
    assert json.loads(attempts[0].read_text()) == {'saved': True}


def test_serialization_error_cannot_replace_old_file_or_leave_partial_json(tmp_path, monkeypatch):
    target = tmp_path / 'state.json'
    old = b'{"historical":"untouched"}'
    target.write_bytes(old)
    monkeypatch.setattr(state.os, 'replace', lambda *args: pytest.fail('invalid JSON cannot be committed'))
    with pytest.raises(ValueError):
        state.write_json(target, {'invalid': float('nan')})
    assert target.read_bytes() == old
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(state.os.name != 'nt', reason='Windows path-length regression')
def test_windows_long_target_still_replaces_old_json_atomically(tmp_path, monkeypatch):
    filename = 'a' * 64 + '.json'
    padding = 245 - len(str(tmp_path / 'cached_reply_validation' / filename)) - 1
    assert padding > 0, 'Test directory must leave room for the Windows path boundary'
    target = tmp_path / ('x' * padding) / 'cached_reply_validation' / filename
    legacy_temporary = target.with_name(target.name + '.' + 'b' * 32 + '.tmp')
    assert len(str(target)) <= 250 and len(str(legacy_temporary)) > 260
    target.parent.mkdir(parents=True)
    old = b'{"historical":"untouched"}'
    target.write_bytes(old)
    value = {'new': ['完整内容', 1.0], 'valid': True}
    original_replace = state.os.replace
    replacements = []
    def replace(temporary, destination):
        assert target.read_bytes() == old
        assert temporary.parent == target.parent and destination == target
        assert json.loads(temporary.read_text(encoding='utf-8')) == value
        replacements.append(temporary)
        original_replace(temporary, destination)
    monkeypatch.setattr(state.os, 'replace', replace)
    state.write_json(target, value)
    assert len(replacements) == 1
    assert json.loads(target.read_text(encoding='utf-8')) == value
    assert not list(target.parent.glob('*.tmp'))
