"""Store full-frame observations on a source timeline and query an exact verified rendition."""

import json
import sqlite3
from pathlib import Path

POLICY = "full-frame-contour-mouth-v1"


class SourceMemory:
    def __init__(self, database, *, read_only=False):
        if read_only:
            self.db = sqlite3.connect(
                Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=30
            )
            return
        self.db = sqlite3.connect(database, timeout=30)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                policy TEXT, source TEXT, fps INTEGER, start INTEGER, length INTEGER,
                view_sha TEXT, facts TEXT NOT NULL,
                PRIMARY KEY(policy, source, fps, start, length, view_sha)
            )""")

    @staticmethod
    def key(clip, fps):
        return (
            POLICY,
            clip["source_sha256"],
            fps,
            clip["source_frame"],
            clip["frames"],
            clip["sha256"],
        )

    def recall(self, clip, fps):
        result = self.db.execute(
            """
            SELECT facts FROM observations WHERE
            (policy, source, fps, start, length, view_sha) = (?, ?, ?, ?, ?, ?)""",
            self.key(clip, fps),
        ).fetchone()
        return None if result is None else json.loads(result[0])

    def remember(self, clip, fps, observations):
        for fact in observations:
            if not (
                clip["source_frame"]
                <= fact["start"]
                < fact["end"]
                <= clip["source_frame"] + clip["frames"]
            ):
                raise ValueError("Observation outside its source interval")
        raw = json.dumps(observations, ensure_ascii=False, sort_keys=True)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*self.key(clip, fps), raw),
            )
            if self.recall(clip, fps) != observations:
                raise ValueError(
                    "Conflicting evidence for the same source rendition and policy"
                )

    def describe(self, clip, fps):
        facts = self.recall(clip, fps)
        if facts is None:
            return {"status": "未观察"}
        ranges = {}
        for fact in facts:
            if fact["kind"] == "mouth" and fact["actor"] != "unknown":
                ranges.setdefault(fact["actor"], []).append(
                    [
                        fact["start"] - clip["source_frame"],
                        fact["end"] - clip["source_frame"],
                    ]
                )
        return {"status": "已检查；未列出的口型未知", "visible_cycles": ranges}
