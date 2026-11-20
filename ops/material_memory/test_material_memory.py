import hashlib
import json

import pytest
from audit_policy import audit
from observe_sources import scan
from select_arm import normalize_pick
from source_memory import SourceMemory


def test_source_provenance_and_unknown_are_preserved(tmp_path):
    bank = SourceMemory(tmp_path / "sources.sqlite")
    clip = {
        "source_sha256": "master",
        "source_frame": 100,
        "frames": 12,
        "sha256": "view",
    }
    fact = {
        "kind": "mouth",
        "actor": "C001",
        "start": 102,
        "end": 108,
        "why": "visible cycle",
    }
    bank.remember(clip, 30, [fact])
    bank.remember(clip, 30, [fact])
    with pytest.raises(ValueError, match="Conflicting"):
        bank.remember(clip, 30, [])
    assert bank.describe(clip, 30)["visible_cycles"] == {"C001": [[2, 8]]}
    for other in [
        dict(clip, source_frame=99),
        dict(clip, sha256="cropped"),
        dict(clip, source_sha256="other"),
    ]:
        assert bank.recall(other, 30) is None
    assert bank.recall(clip, 25) is None
    bank.db.close()


def test_requested_pictures_and_explicit_selections_are_preserved():
    opts = {"a": {"line": "one"}, "b": {"line": "two"}}
    lines = [{"id": "one"}, {"id": "two"}]
    assert normalize_pick({"watch": ["b"]}, opts, lines)["watch"] == ["b"]
    rows = [
        {"line": "one", "candidate": "a", "why": "opens"},
        {"line": "two", "candidate": "b", "why": "replies"},
    ]
    assert normalize_pick({"choices": rows}, opts, lines)["choices"] == ["a", "b"]
    assert normalize_pick({"choices": {"two": "b", "one": "a"}}, opts, lines)[
        "choices"
    ] == ["a", "b"]
    with pytest.raises(ValueError):
        normalize_pick({"choices": rows[::-1]}, opts, lines)


def test_uncertain_is_not_counted_as_an_error():
    ids = ["a", "b", "c"]
    predictions = [{"id": k, "kind": "mouth", "actor": "C001"} for k in ids]
    result = audit(
        [{"id": k} for k in ids],
        predictions,
        {"a": "C001", "b": "other", "c": "uncertain"},
    )
    assert (result["matched"], result["contradicted"], result["unresolved"]) == (
        ["a"],
        ["b"],
        ["c"],
    )


def test_cached_scan_checks_the_actual_video_without_decoding(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    proxy = media / "sample.mp4"
    proxy.write_bytes(b"original")
    row = {
        "id": "clip",
        "file": proxy.name,
        "source_sha256": "master",
        "source_frame": 10,
        "frames": 12,
        "sha256": hashlib.sha256(proxy.read_bytes()).hexdigest(),
    }
    db = tmp_path / "cache.sqlite"
    bank = SourceMemory(db)
    bank.remember(row, 30, [])
    bank.db.close()
    pilot = tmp_path / "pilot.json"
    pilot.write_text(json.dumps({"fps": 30, "clips": [row]}))
    out = tmp_path / "repeat"
    scan(pilot, out, db)
    assert json.loads((out / "scan_plan.json").read_text()) == {}
    proxy.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Source proxy changed"):
        scan(pilot, out, db)
