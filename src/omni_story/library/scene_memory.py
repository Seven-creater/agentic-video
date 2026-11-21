"""Source-mouth observations for editing locked footage. Final cut verification stays on the rendered film."""

import json
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def database():
    override = os.environ.get("OMNI_MATERIAL_MEMORY_DB")
    if override:
        return Path(override)
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state / "omni_story" / "research-material-memory" / "scene_memory.sqlite"


def publish(data, clips, fps, method):
    """Each item contains the master, exact view digest, source start, length and clip-local slots."""
    if not data or not data.get("source_sha256"):
        return
    actors = data.get("characters", {})
    rows = []
    for master, view, start, length, slots in clips:
        if master != data["source_sha256"]:
            continue
        spans = set()
        for slot in slots:
            entity = actors.get(slot["actor"], {}).get("entity_id")
            a, b = slot["start"], slot["end"]
            if entity and 0 <= a < b <= length:
                spans.add((entity, start + a, start + b))
        rows.append(
            (master, view, start, length, fps, method, json.dumps(sorted(spans)))
        )
    if not rows:
        return
    path = database()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS scene_mouth (
            source TEXT, view_sha TEXT, start INTEGER, length INTEGER, fps INTEGER, method TEXT, spans TEXT,
            PRIMARY KEY(source, view_sha, start, length, fps, method))""")
        db.executemany(
            "INSERT OR REPLACE INTO scene_mouth VALUES (?,?,?,?,?,?,?)", rows
        )


def publish_dialogue(case, checked):
    from .scene_pick import load

    if not case.get("configuration_path"):
        return
    config = json.loads(Path(case["configuration_path"]).read_text())["input_lock"][
        "configuration"
    ]
    data = load(config.get("visual_library_root"))
    clips = []
    for key, observation in checked.items():
        c = case["clips"][key]
        if (
            observation.get("method") == "contour_cycle"
            and observation["source_sha256"] == c["sha256"]
        ):
            clips.append(
                (
                    c["source_sha256"],
                    c["sha256"],
                    round(c["source_start_s"] * case["fps"]),
                    c["frames"],
                    observation["slots"],
                )
            )
    publish(data, clips, case["fps"], "dialogue_contour_v1")


def source_spans(data):
    path = database()
    if not path.is_file() or not data.get("source_sha256"):
        return []
    codes = {
        actor["entity_id"]: code
        for code, actor in data["characters"].items()
        if actor.get("entity_id")
    }
    result = set()
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    ) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scene_mouth'"
        ).fetchone():
            return []
        for fps, raw in db.execute(
            """SELECT fps,spans FROM scene_mouth s WHERE source=? AND NOT EXISTS (
                 SELECT 1 FROM scene_mouth r WHERE
                 (r.source,r.view_sha,r.start,r.length,r.fps,r.method)=
                 (s.source,s.view_sha,s.start,s.length,s.fps,s.method || '/reviewed'))""",
            (data["source_sha256"],),
        ):
            for entity, a, b in json.loads(raw):
                if entity in codes:
                    result.add((a / fps, b / fps, codes[entity]))
    return sorted(result)


def locked_hints(library_root, clips, speakers, fps, snapshot):
    """Map master observations to local frames of selected clips and their bound speakers."""
    from .scene_pick import load

    from .state import json_sha, write_json

    scope = json_sha({"library": str(library_root), "fps": fps, "clips": clips,
                      "speakers": {key: sorted(ids) for key, ids in speakers.items()}})
    if snapshot.is_file():
        saved = json.loads(snapshot.read_text())
        if saved.get("scope") == scope:
            return saved["hints"]
    data = load(library_root)
    spans = source_spans(data) if data else []
    hints = {}
    for key, clip in clips.items():
        if not spans or clip.get("source_sha256") != data["source_sha256"]:
            continue
        start = clip["source_start_s"]
        frames = clip.get("frames")
        if frames is None:
            frames = math.floor((clip["source_end_s"] - start) * fps + 1e-7)
        allowed = set(speakers.get(key, ()))
        if "visible" in clip:
            allowed.intersection_update(clip["visible"])
        hits = []
        for a, b, actor in spans:
            if actor not in allowed:
                continue
            first = max(0, math.ceil((a - start) * fps - 1e-7))
            end = min(frames, math.floor((b - start) * fps + 1e-7))
            if first < end:
                hits.append(dict(actor=actor, start=first, end=end))
        if hits:
            hints[key] = hits
    write_json(snapshot, {"scope": scope, "hints": hints})
    return hints
