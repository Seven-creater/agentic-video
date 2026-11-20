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


def test_portrait_verdict_requires_foreground_and_two_observed_states():
    from portrait_observe import verdict

    holes = [0, 0, 12, 12, 0, 0]
    track = {
        "id": "T",
        "centers": {str(i): [1, 1, 8, 4, area] for i, area in enumerate(holes)},
    }
    row = {
        "id": "T",
        "actor": "C001",
        "layer": "scene",
        "feature": "mouth",
        "motion": "speech",
        "open_frame": 3,
        "closed_frame": 1,
        "why": "visible",
    }
    assert verdict(row, track, ["C001"])["actor"] == "C001"
    for change in (
        {"layer": "inset"},
        {"feature": "eye"},
        {"closed_frame": 3},
        {"closed_frame": None},
        {"motion": "held"},
        {"actor": "unknown"},
    ):
        assert verdict(row | change, track, ["C001"])["kind"] == "unknown"
    with pytest.raises(ValueError, match="F numbers"):
        verdict(row | {"open_frame": 123}, track, ["C001"])


def test_shortlist_expands_only_when_current_speaker_lacks_evidence():
    from select_reviewed import consider

    rows = [{"id": k, "source_frame": 100} for k in ("a", "b", "c")]
    line = {"actor": "X", "voice_frames": [1, 2, 3, 4]}
    target = {"actor": "X", "kind": "mouth", "start": 100, "end": 105}
    competitor = target | {"actor": "Y", "end": 104}
    facts = {"a": [target, competitor], "b": [], "c": [target]}
    result = consider(rows, line, lambda c: facts[c["id"]], 2)
    assert (
        result["expanded"]
        and result["scores"] == {"a": 1, "b": 0, "c": 4}
        and result["supported"] == ["c"]
    )
    result = consider(rows[::-1], line, lambda c: facts[c["id"]], 1)
    assert not result["expanded"] and list(result["scores"]) == ["c"]


def test_cannot_reuse_mouth_observations_across_cast_cards(tmp_path):
    clip = {
        "source_sha256": "source",
        "source_frame": 2,
        "frames": 3,
        "sha256": "proxy",
    }
    path = tmp_path / "bank.sqlite"
    bank = SourceMemory(path, policy="cast-A")
    bank.remember(clip, 30, [])
    bank.db.close()
    for version in ("cast-A", "cast-B"):
        other = SourceMemory(path, policy=version, read_only=True)
        assert (other.recall(clip, 30) == []) == (version == "cast-A")
        other.db.close()


def test_supplement_queries_only_unresolved_speaker_and_its_own_action():
    from prepare_pilot import subject_speech_rank, unresolved_lines

    a, b = {"id": "a"}, {"id": "b"}
    prev = {
        "lines": [a, b],
        "clips": [{"id": "1", "line": "a"}, {"id": "2", "line": "b"}],
    }
    reply = {"choices": ["1", "2"], "needs_more_material": ["b"]}
    assert unresolved_lines([a, b], prev, reply) == [b]
    with pytest.raises(ValueError):
        unresolved_lines([a, b], prev, reply | {"choices": ["2", "1"]})
    assert subject_speech_rank("角色_x皱眉；角色_y开口说话", "角色_x") == 2
    assert subject_speech_rank("角色_x开口回应，身旁另一位转头", "角色_x") == 0
    assert subject_speech_rank("角色_x张嘴吃东西", "角色_x") == 2


def test_portrait_publication_with_no_tracks_reuses_only_the_matching_cache(tmp_path):
    from PIL import Image
    from portrait_observe import policy, publish

    from omni_story.library.state import write_json

    roster = tmp_path / "roster"
    write_json(roster / "actors.json", ["C001"])
    Image.new("RGB", (8, 8), "white").save(roster / "cast.png")
    scan = tmp_path / "empty"
    write_json(scan / "tracks.json", [])
    write_json(scan / "scan_plan.json", {})
    clip = {
        "id": "s",
        "source_sha256": "master",
        "source_frame": 1,
        "frames": 2,
        "sha256": "view",
    }
    pilot = tmp_path / "pilot.json"
    write_json(pilot, {"fps": 30, "clips": [clip]})
    db = tmp_path / "cache.sqlite"
    bank = SourceMemory(db, policy=policy(roster))
    bank.remember(clip, 30, [])
    bank.db.close()
    publish(pilot, scan, roster, scan, db)
    Image.new("RGB", (8, 8), "black").save(roster / "cast.png")
    with pytest.raises(ValueError, match="skipped source"):
        publish(pilot, scan, roster, scan, db)


def test_continuation_keeps_exact_encoded_proxy_on_resume(tmp_path):
    from prepare_pilot import reusable_proxy

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"first encoding")
    row = {
        "source_sha256": "master",
        "source_frame": 30,
        "frames": 40,
        "file": video.name,
        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
    }
    assert reusable_proxy(row, video, "master", 30, 40)
    assert not reusable_proxy(row, video, "master", 31, 40)
    video.write_bytes(b"changed encoding")
    assert not reusable_proxy(row, video, "master", 30, 40)
