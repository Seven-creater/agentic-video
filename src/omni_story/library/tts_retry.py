"""Archived replacements under explicit human or narrow task-wide authorization.

The old outcome stays uncertain in its original-byte archive. A fresh received
receipt proves the replacement audio; the transport itself never retries.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from .media import sha256_file
from .state import file_lock, write_json

POLICY = 'human_authorized_tts_retry_once_v1'
SSE_POLICY = 'human_authorized_native_sse_validation_once_v1'
AUTO_POLICY = 'automatic_zero_byte_header_timeout_v1'


def _read(path):
    return json.loads(Path(path).read_text())


def _project(attempt, folder, lineage):
    result = deepcopy(attempt)
    result.pop('reused', None)
    result['request_count'] = lineage.get('request_count_total', 2)
    result['authorized_retry_once'] = lineage
    for key in ('source_audio_path', 'audio_path'):
        result[key] = str(folder / Path(result[key]).name)
    normalization = result.get('audio_normalization')
    if normalization:
        for key in ('raw_download_path', 'normalized_audio_path'):
            normalization[key] = str(folder / Path(normalization[key]).name)
    return result


def validate_replacement(path, saved=None):
    """Bind both attempts, exact human scope, actual receipt and audio bytes."""
    from .tts import TTSStopped
    path = Path(path).resolve()
    saved = saved or _read(path)
    if saved.get('reused_from') is not None:
        from .tts import _validate_reused
        source_path, source = _validate_reused(path, saved)
        for key, digest in (('source_audio_path', 'source_audio_sha256'), ('audio_path', 'audio_sha256')):
            if sha256_file(saved[key]) != saved[digest]:
                raise TTSStopped('tts_retry_audio_changed')
        return validate_replacement(source_path, source)
    lineage = saved.get('authorized_retry_once', {})
    if lineage.get('policy') == AUTO_POLICY:
        return _validate_automatic_replacement(path, saved, lineage)
    if lineage.get('policy') == SSE_POLICY:
        return _validate_sse_replacement(path, saved, lineage)
    if lineage.get('policy') != POLICY or saved.get('request_count') != 2:
        raise TTSStopped('tts_retry_invalid_lineage')
    for name in ('original', 'authorization', 'attempt'):
        record = lineage[name]
        if sha256_file(record['path']) != record['sha256']:
            raise TTSStopped('tts_retry_evidence_changed:' + name)
    original = _read(lineage['original']['path'])
    authorization = _read(lineage['authorization']['path'])
    attempt = _read(lineage['attempt']['path'])
    if (authorization.get('policy') != POLICY or authorization.get('max_additional_requests') != 1
            or authorization.get('original_ledger_path') != str(path)
            or authorization.get('original_ledger_sha256') != lineage['original']['sha256']
            or authorization.get('allow_possible_duplicate_charge') is not True
            or authorization.get('user_answer') != '允许这一句重调一次'
            or original.get('status') != 'uncertain' or original.get('request_count') != 1
            or attempt.get('status') != 'received' or attempt.get('audio_verified') is not True
            or attempt.get('request_count') != 1
            or any(original.get(k) != attempt.get(k) for k in
                   ('binding', 'request_sha256', 'line_id', 'text', 'voice', 'model'))
            or saved != _project(attempt, path.parent, lineage)):
        raise TTSStopped('tts_retry_scope_or_actual_receipt_changed')
    for record in (attempt, saved):
        for file_key, digest_key in (('source_audio_path', 'source_audio_sha256'), ('audio_path', 'audio_sha256')):
            if sha256_file(record[file_key]) != record[digest_key]:
                raise TTSStopped('tts_retry_audio_changed')
    return original, authorization, attempt


def _validate_sse_replacement(path, saved, lineage):
    """Use the actually received third attempt; both old outcomes stay archived."""
    from .tts import TTSStopped, _probe_audio
    folder = path.parent
    expected = {'original': folder/'original_uncertain.json',
                'previous': folder/'authorized_retry'/folder.name/'state.json',
                'authorization': folder/'human_sse_validation_authorization.json',
                'claim': folder/'authorized_sse_validation_once.claim.json',
                'attempt': folder/'authorized_sse_validation'/folder.name/'state.json'}
    for name, target in expected.items():
        record = lineage.get(name, {})
        if record.get('path') != str(target) or sha256_file(target) != record.get('sha256'):
            raise TTSStopped('tts_replacement_evidence_changed:' + name)
    old, previous, auth, claim, attempt = [_read(expected[k]) for k in expected]
    pins = auth.get('protected_prior_receipts', {})
    if not {str(path), str(expected['original']), str(expected['previous']),
            str(folder/'authorized_retry_once.claim.json')} <= set(pins):
        raise TTSStopped('tts_replacement_prior_pins_required')
    for name, digest in pins.items():
        source = expected['original'] if name == str(path) else name
        if sha256_file(source) != digest:
            raise TTSStopped('tts_replacement_prior_file_changed:' + name)
    if (saved.get('request_count') != 3 or lineage.get('request_count_total') != 3
            or auth.get('policy') != SSE_POLICY or auth.get('user_answer') != '允许用新版仅验证一次'
            or auth.get('max_additional_requests') != 1 or auth.get('expected_total_attempts_this_utterance') != 3
            or auth.get('allow_possible_duplicate_charge') is not True
            or auth.get('original_ledger_path') != str(path)
            or auth.get('original_ledger_sha256') != lineage['original']['sha256']
            or claim.get('policy') != SSE_POLICY or claim.get('authorization_path') != str(expected['authorization'])
            or claim.get('authorization_sha256') != lineage['authorization']['sha256']
            or attempt.get('status') != 'received' or attempt.get('audio_verified') is not True
            or attempt.get('request_count') != 1
            or any(row.get('status') != 'uncertain' or row.get('request_count') != 1 for row in (old, previous))
            or any(row.get(k) != attempt.get(k) for row in (old, previous)
                   for k in ('binding', 'request_sha256', 'line_id', 'text', 'voice', 'model'))
            or auth.get('request_sha256') != attempt.get('request_sha256')
            or saved != _project(attempt, folder, lineage)):
        raise TTSStopped('tts_replacement_scope_or_receipt_changed')
    for row in (attempt, saved):
        for key, digest in [('source_audio_path','source_audio_sha256'), ('audio_path','audio_sha256')]:
            if sha256_file(row[key]) != row[digest]:
                raise TTSStopped('tts_replacement_audio_changed')
        if _probe_audio(row['audio_path']) != row['probe']:
            raise TTSStopped('tts_replacement_pcm_changed')
    return old, auth, attempt


def adopt_received_sse(original_path):
    """No network: bind the reviewed successful validation into the existing cache."""
    original_path = Path(original_path).resolve(strict=True)
    folder = original_path.parent
    with file_lock(folder/'.lock'):
        paths = dict(original=folder/'original_uncertain.json',
            previous=folder/'authorized_retry'/folder.name/'state.json',
            authorization=folder/'human_sse_validation_authorization.json',
            claim=folder/'authorized_sse_validation_once.claim.json',
            attempt=folder/'authorized_sse_validation'/folder.name/'state.json')
        lineage = dict(policy=SSE_POLICY, request_count_total=3,
                       **{k:dict(path=str(p),sha256=sha256_file(p)) for k,p in paths.items()})
        attempt = _read(paths['attempt'])
        result = _project(attempt, folder, lineage)
        old = _read(original_path)
        if old != _read(paths['original']):
            validate_replacement(original_path, old)
            return old
        for key in ('source_audio_path', 'audio_path'):
            destination = Path(result[key])
            if not destination.exists():
                shutil.copyfile(attempt[key], destination)
        _validate_sse_replacement(original_path, result, lineage)
        write_json(original_path, result)
        return result


def retry_once(original_path, authorization_path, *, timeout_s=300, transport=None):
    """Send at most one extra POST, even after a crash or another timeout."""
    from .tts import TTSStopped
    original_path = Path(original_path).resolve(strict=True)
    authorization_path = Path(authorization_path).resolve(strict=True)
    folder = original_path.parent
    with file_lock(folder / '.lock'):
        authorization, original = _read(authorization_path), _read(original_path)
        if (authorization.get('policy') != POLICY or authorization.get('max_additional_requests') != 1
                or authorization.get('original_ledger_path') != str(original_path)
                or authorization.get('original_ledger_sha256') != sha256_file(original_path)
                or authorization.get('allow_possible_duplicate_charge') is not True
                or authorization.get('user_answer') != '允许这一句重调一次'
                or original.get('status') != 'uncertain' or original.get('request_count') != 1
                or original.get('audio_verified') is not False):
            raise TTSStopped('tts_retry_specific_human_authorization_required')
        return _send_replacement(original_path, authorization_path, original,
                                 timeout_s=timeout_s, transport=transport, policy=POLICY)


def _send_replacement(original_path, authorization_path, original, *, timeout_s, transport, policy):
    """Caller holds the original line lock and has validated its authorization."""
    from .tts import TTSStopped, synthesize_line
    folder = original_path.parent
    attempt_dir = folder / 'authorized_retry'
    if attempt_dir.exists():
        raise TTSStopped('tts_retry_attempt_path_already_exists')
    claim = folder / 'authorized_retry_once.claim.json'
    with claim.open('x') as handle:
        json.dump(dict(policy=policy, authorization_path=str(authorization_path),
            authorization_sha256=sha256_file(authorization_path),
            original_sha256=sha256_file(original_path), claimed_at=datetime.now(timezone.utc).isoformat()), handle)
        handle.flush()
        os.fsync(handle.fileno())
    archive = folder / 'original_uncertain.json'
    with archive.open('xb') as handle:
        handle.write(original_path.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != 'nt':
        directory_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    # The exclusive claim is durable before sending. The ordinary TTS
    # write-before-send ledger independently protects the one new attempt.
    synthesize_line(original['text'], original['voice'], attempt_dir,
        line_id=original['line_id'], model=original['model'], timeout_s=timeout_s, transport=transport,
        linked_ledgers=())
    attempt_path = attempt_dir / original['line_id'] / 'state.json'
    attempt = _read(attempt_path)
    lineage = dict(policy=policy, original=dict(path=str(archive), sha256=sha256_file(archive)),
        authorization=dict(path=str(authorization_path), sha256=sha256_file(authorization_path)),
        attempt=dict(path=str(attempt_path), sha256=sha256_file(attempt_path)))
    result = _project(attempt, folder, lineage)
    for key in ('source_audio_path', 'audio_path'):
        destination = Path(result[key])
        if destination.exists():
            if sha256_file(destination) != sha256_file(attempt[key]):
                raise TTSStopped('tts_retry_existing_audio_conflict')
        else:
            shutil.copyfile(attempt[key], destination)
    validate_replacement(original_path, result)
    write_json(original_path, result)
    return result


def zero_byte_header_timeout(original):
    evidence = original.get('transport_evidence', {})
    return (original.get('status') == 'uncertain' and original.get('request_count') == 1
        and original.get('audio_verified') is False and original.get('error_type') == 'APITransportError'
        and evidence.get('transport') == 'native_https_sse_v1'
        and evidence.get('phase') == 'headers' and 'http_status' in evidence
        and evidence['http_status'] is None and evidence.get('failure') == 'idle_timeout'
        and type(evidence.get('response_bytes')) is int and evidence['response_bytes'] == 0
        and type(evidence.get('events')) is int and evidence['events'] == 0)


def task_output_for(output):
    """The chain owns one quota shared by rough and fine audio directories."""
    paths = [Path(output).resolve(), *Path(output).resolve().parents]
    for path in paths:
        if (path / 'chain_started').is_file():
            return path
    for path in paths:
        # The fine-audio child is part of the enclosing chain, even without a
        # chain_started marker. This locates ownership without reading or
        # searching any ancestor's paid-request contents.
        if path.name == 'creative_audio' and path.parent.name == 'skill_finecut':
            owner = path.parent.parent
            if (owner / 'library_state.json').is_file():
                return owner
    for path in paths:
        if (path / 'library_state.json').is_file():
            return path
    return None


def automatic_retry_once(original_path, task_output, *, timeout_s=120, transport=None):
    from .tts import TTSUnknown
    original_path = Path(original_path).resolve(strict=True)
    folder, task_output = original_path.parent, Path(task_output).resolve()
    ledger = task_output / 'tts_automatic_resend.json'
    with file_lock(task_output / '.tts_automatic_resend.lock'):
        with file_lock(folder / '.lock'):
            original = _read(original_path)
            if ledger.exists() or not zero_byte_header_timeout(original):
                raise TTSUnknown('tts_request_outcome_unknown_no_replay')
            authorization_path = folder / 'automatic_resend_authorization.json'
            authorization = dict(kind=AUTO_POLICY, max_additional_requests=1,
                allow_possible_duplicate_charge=True, original_ledger_path=str(original_path),
                original_ledger_sha256=sha256_file(original_path),
                signature=original['transport_evidence'], task_ledger_path=str(ledger))
            write_json(authorization_path, authorization)
            # Durable task-wide claim before the common archive/send machinery.
            write_json(ledger, dict(kind=AUTO_POLICY, additional_requests_claimed=1,
                authorization=dict(path=str(authorization_path), sha256=sha256_file(authorization_path)),
                original=dict(path=str(folder/'original_uncertain.json'), sha256=sha256_file(original_path)),
                attempt_path=str(folder/'authorized_retry'/folder.name/'state.json')))
            if os.name != 'nt':
                directory_fd = os.open(task_output, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            try:
                return _send_replacement(original_path, authorization_path, original,
                    timeout_s=timeout_s, transport=transport, policy=AUTO_POLICY)
            except Exception as error:
                failure = TTSUnknown(str(error))
                failure.tts_attempts = [str(folder/'original_uncertain.json'),
                    str(folder/'authorized_retry'/folder.name/'state.json')]
                raise failure from error


def _validate_automatic_replacement(path, saved, lineage):
    from .tts import TTSStopped
    folder = path.parent
    expected = dict(original=folder/'original_uncertain.json',
        authorization=folder/'automatic_resend_authorization.json',
        attempt=folder/'authorized_retry'/folder.name/'state.json')
    for name, target in expected.items():
        record = lineage.get(name, {})
        if record.get('path') != str(target) or sha256_file(target) != record.get('sha256'):
            raise TTSStopped('tts_retry_evidence_changed:' + name)
    original, authorization, attempt = [_read(expected[key]) for key in expected]
    ledger_path = Path(authorization['task_ledger_path'])
    ledger = _read(ledger_path)
    if (authorization.get('kind') != AUTO_POLICY or authorization.get('max_additional_requests') != 1
            or authorization.get('allow_possible_duplicate_charge') is not True
            or authorization.get('original_ledger_path') != str(path)
            or authorization.get('original_ledger_sha256') != lineage['original']['sha256']
            or authorization.get('signature') != original.get('transport_evidence')
            or not zero_byte_header_timeout(original)
            or ledger_path.name != 'tts_automatic_resend.json'
            or ledger.get('kind') != AUTO_POLICY or ledger.get('additional_requests_claimed') != 1
            or ledger.get('original') != lineage['original']
            or ledger.get('authorization') != lineage['authorization']
            or ledger.get('attempt_path') != str(expected['attempt'])
            or saved.get('request_count') != 2
            or attempt.get('status') != 'received' or attempt.get('audio_verified') is not True
            or attempt.get('request_count') != 1
            or any(original.get(k) != attempt.get(k) for k in
                   ('binding', 'request_sha256', 'line_id', 'text', 'voice', 'model'))
            or saved != _project(attempt, folder, lineage)):
        raise TTSStopped('tts_retry_scope_or_actual_receipt_changed')
    for record in (attempt, saved):
        for key, digest in (('source_audio_path', 'source_audio_sha256'), ('audio_path', 'audio_sha256')):
            if sha256_file(record[key]) != record[digest]:
                raise TTSStopped('tts_retry_audio_changed')
    return original, authorization, attempt
