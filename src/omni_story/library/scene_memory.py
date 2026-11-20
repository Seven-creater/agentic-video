"""Source-mouth observations for scene retrieval. Final cut verification stays on the rendered film."""

import json
import os
import sqlite3
from bisect import bisect_left
from contextlib import closing
from pathlib import Path

from .state import write_json


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


def apply(data, snapshot):
    """Use the same hints throughout this selection, including local retries."""
    if snapshot.exists():
        notes = json.loads(snapshot.read_text())
    else:
        spans = source_spans(data)
        starts = [a for a, _, _ in spans]
        notes = {}
        for sid, scene in data["scenes"].items():
            if not spans:
                break
            lo, hi = scene["source_start_s"], scene["source_end_s"]
            best = {}
            for a, b, code in spans[bisect_left(starts, lo) : bisect_left(starts, hi)]:
                if b <= hi and (
                    code not in best or b - a > best[code][1] - best[code][0]
                ):
                    best[code] = (a, b)
            if best:
                notes[sid] = best
        write_json(snapshot, notes)
    for sid, mouths in notes.items():
        if sid in data["scenes"]:
            summary = "、".join(
                f"{c} {a:.2f}–{b:.2f}" for c, (a, b) in sorted(mouths.items())
            )
            data["scenes"][sid]["record"] += (
                " 过往看片的动嘴线索（原片秒，选中后复看）：" + summary
            )
