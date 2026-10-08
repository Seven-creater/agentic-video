"""Process-local reuse of previously read file bytes under unchanged stat proofs.

No proof survives process restart. This is an execution optimization, not a final
historical byte audit; final delivery must hash protected files with force=True.
"""
from __future__ import annotations

from pathlib import Path
from stat import S_ISREG
from threading import RLock

from .media import sha256_file as _full_sha256
from .state import LibraryStopped

POLICY = "slot_finecut_process_local_stat_sha_cache_v1"
_cache = {}
_lock = RLock()


def _stamp(path):
    value = path.stat()
    if not S_ISREG(value.st_mode):
        raise LibraryStopped("slot_finecut:file_proof_requires_regular_file:" + str(path))
    return (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino, value.st_dev)


def verified_sha256(path, *, force=False):
    """Read a full SHA once per unchanged path/stat, or on every forced audit."""
    if type(force) is not bool:
        raise ValueError("slot_finecut:file_proof_force_must_be_boolean")
    path = Path(path).resolve(strict=True)
    with _lock:
        before = _stamp(path)
        previous = _cache.get(path)
        if not force and previous is not None and previous[0] == before:
            if _stamp(path) != before:
                _cache.pop(path, None)
                raise LibraryStopped("slot_finecut:file_changed_during_cached_proof:" + str(path))
            return previous[1]
        _cache.pop(path, None)
        digest = _full_sha256(path)
        after = _stamp(path)
        if before != after:
            raise LibraryStopped("slot_finecut:file_changed_while_hashing:" + str(path))
        _cache[path] = (after, digest)
        return digest


def invalidate(path):
    """Discard one process-local proof without touching the file."""
    path = Path(path).resolve()
    with _lock:
        _cache.pop(path, None)


def reset():
    """Discard all process-local proofs; the next checks read full bytes again."""
    with _lock:
        _cache.clear()
