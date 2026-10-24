import json

import pytest

from omni_story.evaluation.adjudicate import (adjudicate, is_blocked, new_attempt_id,
    read_adjudications, record_attempt)


def ledger(tmp_path, *statuses):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(dict(calls=[dict(id=f'c{i}', status=s) for i, s in enumerate(statuses)])))
    return path


def decide(path, call_id='c0', **overrides):
    return adjudicate(path, call_id, **{**dict(decision='abandoned_by_user', user_note='n', decided_by='user'), **overrides})


@pytest.mark.parametrize('status', ['submitted', 'uncertain'])
def test_unresolved_calls_can_be_adjudicated(tmp_path, status):
    path = ledger(tmp_path, status)
    row = decide(path)
    assert row['original_status'] == status and len(row['original_record_sha256']) == 64
    assert read_adjudications(path) == [row]


@pytest.mark.parametrize('status', ['received', 'failed_known'])
def test_resolved_calls_are_refused(tmp_path, status):
    with pytest.raises(ValueError, match='adjudication_call_not_unresolved'):
        decide(ledger(tmp_path, status))


def test_unknown_call_and_non_user_decisions_are_refused(tmp_path):
    path = ledger(tmp_path, 'uncertain')
    with pytest.raises(ValueError, match='adjudication_call_unknown'):
        decide(path, 'missing')
    for bad in (dict(decided_by='assistant'), dict(decision='received')):
        with pytest.raises(ValueError, match='explicit_user_decision'):
            decide(path, **bad)
    assert read_adjudications(path) == []


def test_original_ledger_bytes_are_unchanged(tmp_path):
    path = ledger(tmp_path, 'uncertain')
    before = path.read_bytes()
    decide(path)
    assert path.read_bytes() == before


def test_duplicate_adjudication_is_refused(tmp_path):
    path = ledger(tmp_path, 'uncertain')
    decide(path)
    with pytest.raises(ValueError, match='adjudication_exists'):
        decide(path)


def test_retry_ids_increase_and_require_adjudication(tmp_path):
    path = ledger(tmp_path, 'uncertain')
    with pytest.raises(ValueError, match='unknown_call_not_adjudicated_no_replay'):
        new_attempt_id('c0', read_adjudications(path))
    decide(path)
    assert new_attempt_id('c0', read_adjudications(path)) == 'c0__retry1'
    assert record_attempt(path, 'c0') == 'c0__retry1'
    assert record_attempt(path, 'c0') == 'c0__retry2'
    assert new_attempt_id('c0', read_adjudications(path)) == 'c0__retry3'


def test_is_blocked_only_until_adjudicated(tmp_path):
    path = ledger(tmp_path, 'uncertain', 'received')
    calls = json.loads(path.read_text())['calls']
    assert is_blocked(calls[0], read_adjudications(path)) is True
    assert is_blocked(calls[1], read_adjudications(path)) is False
    decide(path)
    assert is_blocked(calls[0], read_adjudications(path)) is False
