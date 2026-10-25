"""User adjudication of unresolved calls. The original ledger is never rewritten."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

from ..library.state import json_sha


@contextmanager
def _lock(path):
    # Own blocking lock: frozen production state modules may shadow newer file_lock signatures.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

UNRESOLVED = frozenset({'submitted', 'uncertain'})


def _path(ledger_path):
    return Path(ledger_path).with_name('adjudications.jsonl')


def read_adjudications(ledger_path):
    path = _path(ledger_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def adjudicate(ledger_path, call_id, *, decision, user_note, decided_by):
    if decision != 'abandoned_by_user' or decided_by != 'user':
        raise ValueError('adjudication_requires_explicit_user_decision')
    ledger = json.loads(Path(ledger_path).read_text(encoding='utf-8'))
    call = next((c for c in ledger['calls'] if c['id'] == call_id), None)
    if call is None:
        raise ValueError('adjudication_call_unknown')
    if call['status'] not in UNRESOLVED | {'failed_known'}:
        raise ValueError('adjudication_call_not_unresolved')
    with _lock(_path(ledger_path).with_suffix('.lock')):
        if any(row['call_id'] == call_id for row in read_adjudications(ledger_path)):
            raise ValueError('adjudication_exists')
        row = dict(call_id=call_id, original_status=call['status'], original_record_sha256=json_sha(call),
            decision=decision, user_note=user_note, decided_by=decided_by,
            decided_at=datetime.now(timezone.utc).isoformat())
        with _path(ledger_path).open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    return row


def new_attempt_id(call_id, adjudications):
    if not any(row['call_id'] == call_id and row['decision'] == 'abandoned_by_user' for row in adjudications):
        raise ValueError('unknown_call_not_adjudicated_no_replay')
    used = {row['attempt_id'] for row in adjudications if row.get('attempt_id', '').startswith(f'{call_id}__retry')}
    number = 1
    while f'{call_id}__retry{number}' in used:
        number += 1
    return f'{call_id}__retry{number}'


def record_attempt(ledger_path, call_id):
    """Reserve the next retry id durably so two callers cannot reuse it."""
    with _lock(_path(ledger_path).with_suffix('.lock')):
        rows = read_adjudications(ledger_path)
        attempt = new_attempt_id(call_id, rows)
        with _path(ledger_path).open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(call_id=call_id, decision='abandoned_by_user',
                attempt_id=attempt, reserved_by='record_attempt'), sort_keys=True) + '\n')
    return attempt


def is_blocked(call, adjudications):
    if call['status'] not in UNRESOLVED:
        return False
    return not any(row['call_id'] == call['id'] and row['decision'] == 'abandoned_by_user' for row in adjudications)
