"""Temporary file proofs only; no real protected movie or model is accessed."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from omni_story.library import slot_finecut_file_proofs as proofs
from omni_story.library.state import LibraryStopped


@pytest.fixture(autouse=True)
def cold_cache():
    proofs.reset()
    yield
    proofs.reset()


@pytest.fixture
def counted(monkeypatch):
    calls = []
    def full(path):
        calls.append(Path(path))
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    monkeypatch.setattr(proofs, "_full_sha256", full)
    return calls


def test_unchanged_resolved_path_reuses_one_full_read(tmp_path, counted):
    path = tmp_path / "evidence.json"
    path.write_bytes(b"original evidence")
    first = proofs.verified_sha256(path)
    assert proofs.verified_sha256(path.parent / "." / path.name) == first
    assert counted == [path.resolve()]


@pytest.mark.parametrize("change", ["size", "mtime", "same_size_bytes", "replace"])
def test_changed_size_time_or_file_identity_requires_a_new_full_read(tmp_path, counted, change):
    path = tmp_path / "evidence.bin"
    path.write_bytes(b"abcd")
    old = proofs.verified_sha256(path)
    if change == "size":
        path.write_bytes(b"longer contents")
    elif change == "mtime":
        stamp = path.stat()
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 10_000_000))
    elif change == "same_size_bytes":
        stamp = path.stat()
        path.write_bytes(b"wxyz")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 10_000_000))
    else:
        replacement = tmp_path / "replacement.bin"
        replacement.write_bytes(b"wxyz")
        os.replace(replacement, path)
    actual = proofs.verified_sha256(path)
    assert actual == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(counted) == 2
    assert (actual == old) is (change == "mtime")


def test_force_invalidate_and_reset_each_require_an_actual_read(tmp_path, counted):
    path = tmp_path / "source.bin"
    path.write_bytes(b"original")
    digest = proofs.verified_sha256(path)
    assert proofs.verified_sha256(path, force=True) == digest
    proofs.invalidate(path)
    assert proofs.verified_sha256(path) == digest
    proofs.reset()
    assert proofs.verified_sha256(path) == digest
    assert len(counted) == 4


def test_force_audit_does_not_treat_unchanged_metadata_as_fresh_byte_evidence(tmp_path, counted, monkeypatch):
    path = tmp_path / "source.bin"
    path.write_bytes(b"abcd")
    frozen = proofs._stamp(path)
    monkeypatch.setattr(proofs, "_stamp", lambda current: frozen)
    old = proofs.verified_sha256(path)
    path.write_bytes(b"wxyz")
    # Explicitly model metadata-preserving alteration: an in-process cache hit
    # is an earlier proof, while the required final forced audit reads new bytes.
    assert proofs.verified_sha256(path) == old
    fresh = proofs.verified_sha256(path, force=True)
    assert fresh != old and fresh == hashlib.sha256(b"wxyz").hexdigest()
    assert len(counted) == 2


def test_file_changing_during_hash_is_rejected_and_never_cached(tmp_path, monkeypatch):
    path = tmp_path / "source.bin"
    path.write_bytes(b"original")
    calls = []
    def changing(current):
        calls.append(current)
        digest = hashlib.sha256(current.read_bytes()).hexdigest()
        current.write_bytes(b"different and longer")
        return digest
    monkeypatch.setattr(proofs, "_full_sha256", changing)
    with pytest.raises(LibraryStopped, match="file_changed_while_hashing"):
        proofs.verified_sha256(path)
    monkeypatch.setattr(proofs, "_full_sha256", lambda current: calls.append(current) or hashlib.sha256(current.read_bytes()).hexdigest())
    assert proofs.verified_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(calls) == 2


def test_new_process_has_no_cached_sha_proof(tmp_path, counted):
    path = tmp_path / "evidence.bin"
    path.write_bytes(b"cold process proof")
    digest = proofs.verified_sha256(path)
    assert len(counted) == 1
    script = """
import sys
from omni_story.library import slot_finecut_file_proofs as proofs
original = proofs._full_sha256
calls = []
def counted(path):
    calls.append(path)
    return original(path)
proofs._full_sha256 = counted
print(proofs.verified_sha256(sys.argv[1]))
print(proofs.verified_sha256(sys.argv[1]))
print(len(calls))
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [digest, digest, "1"]


def test_cache_is_scoped_to_two_consumers_without_changing_media_hash_function():
    from omni_story.library import media, slot_finecut_baselines, slot_finecut_budget
    assert slot_finecut_baselines.sha256_file is proofs.verified_sha256
    assert slot_finecut_budget.sha256_file is proofs.verified_sha256
    assert media.sha256_file is not proofs.verified_sha256
