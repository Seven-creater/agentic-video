"""Scan source clips, check identity from the real frames, then publish the reusable facts."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw
from source_memory import POLICY, SourceMemory

from omni_story.library.dialogue_mouth_detail import sampled_frames
from omni_story.library.dialogue_mouth_edit import ask
from omni_story.library.dialogue_mouth_tracks import candidates, sheet, witness_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


def scan(pilot, out, database=None):
    data = json.loads(pilot.read_text())
    out.mkdir(parents=True, exist_ok=True)
    for row in data["clips"]:
        if sha256_file(pilot.parent / "media" / row["file"]) != row["sha256"]:
            raise ValueError("Source proxy changed: " + row["id"])
    bank = (
        SourceMemory(database, read_only=True)
        if database and database.is_file()
        else None
    )
    pending = [
        r for r in data["clips"] if bank is None or bank.recall(r, data["fps"]) is None
    ]
    if bank:
        bank.db.close()
    plan = {r["id"]: list(SourceMemory.key(r, data["fps"])) for r in pending}
    receipt = out / "scan_plan.json"
    if receipt.exists() and json.loads(receipt.read_text()) != plan:
        raise ValueError("Use a fresh observation directory for a different scan plan")
    write_json(receipt, plan)
    tracks = []
    for clip in sorted(pending, key=lambda row: row["id"]):
        media = pilot.parent / "media" / clip["file"]
        frames = sampled_frames(media, step=1)
        if len(frames) != clip["frames"]:
            raise ValueError("Source duration changed: " + clip["id"])
        for start, end, points in candidates(frames, data["fps"]):
            if len(witness_frames(points)) < 2:
                continue
            uid = f"T{len(tracks):03d}"
            sheet(frames, points, out / (uid + ".jpg"))
            tracks.append(
                {
                    "id": uid,
                    "clip": clip["id"],
                    "start": start,
                    "end": end,
                    "centers": {
                        int(f): [float(v) for v in point] for f, point in points.items()
                    },
                }
            )
    for i in range(0, len(tracks), 4):
        group = tracks[i : i + 4]
        poster = Image.new("RGB", (1280, 420 * ((len(group) + 1) // 2)), "#202020")
        pen = ImageDraw.Draw(poster)
        for k, row in enumerate(group):
            x, y = k % 2 * 640, k // 2 * 420
            pen.text((x + 2, y + 2), row["id"], fill="white")
            with Image.open(out / (row["id"] + ".jpg")) as tile:
                poster.paste(tile.resize((640, 387)), (x, y + 18))
        poster.save(out / f"group_{i // 4}.jpg", quality=92)
    write_json(out / "tracks.json", tracks)
    print(
        "new clips",
        len(pending),
        "cached",
        len(data["clips"]) - len(pending),
        "tracks",
        len(tracks),
        flush=True,
    )


def label(case, out, number, actors):
    tracks = json.loads((out / "tracks.json").read_text())
    ids = [r["id"] for r in tracks[number * 4 : number * 4 + 4]]
    if not ids:
        raise ValueError("Empty batch")
    prompt = (
        "每格是一处轮廓轨迹。有全景定位框、角色头部特写和连续采样。只判断下方放大的目标是否真是嘴，"
        "是否看得到开合。歪头、眼睛、汗滴、食物咀嚼、手上东西和张静止的嘴均不能当说话嘴。"
        "若该处真是说话嘴，按本人的头顶颜色耳朵认演员；其他角色填other_actor，看不清填unknown。"
        '返回 {"rows":[{"id":"T编号","kind":"mouth/other/unknown","actor":"编号或other_actor或unknown","why":"位置、外形及开合依据"}]}。'
        "逐格填写，顺序为"
        + str(ids)
        + "。演员外观："
        + json.dumps(actors, ensure_ascii=False)
    )

    def validate(reply):
        rows = reply["rows"]
        if [r["id"] for r in rows] != ids or any(
            r["kind"] not in ("mouth", "other", "unknown")
            or r["actor"] not in {*actors, "other_actor", "unknown"}
            or not r.get("why")
            for r in rows
        ):
            raise ValueError("逐格按顺序核对编号、角色和证据")
        return reply

    result = ask(
        case,
        out / f"batch_{number}",
        f"memory_observe_{number}",
        prompt,
        out / f"group_{number}.jpg",
        validate,
    )
    write_json(out / f"labels_{number}.json", result["rows"])


def publish(pilot, out, database):
    data = json.loads(pilot.read_text())
    tracks = json.loads((out / "tracks.json").read_text())
    plan = json.loads((out / "scan_plan.json").read_text())
    answers = {}
    for number in range((len(tracks) + 3) // 4):
        rows = json.loads((out / f"labels_{number}.json").read_text())
        answers.update({r["id"]: r for r in rows})
    if set(answers) != {r["id"] for r in tracks}:
        raise ValueError("Incomplete source review")
    bank = SourceMemory(database)
    notes = {}
    hits = 0
    for clip in data["clips"]:
        hits += bank.recall(clip, data["fps"]) is not None
        facts = []
        for t in tracks:
            if t["clip"] == clip["id"]:
                answer = answers[t["id"]]
                facts.append(
                    {
                        "start": clip["source_frame"] + t["start"],
                        "end": clip["source_frame"] + t["end"],
                        "kind": answer["kind"],
                        "actor": answer["actor"],
                        "why": answer["why"],
                    }
                )
        if clip["id"] in plan:
            if plan[clip["id"]] != list(SourceMemory.key(clip, data["fps"])):
                raise ValueError("Pilot and observation provenance differ")
            bank.remember(clip, data["fps"], facts)
        elif bank.recall(clip, data["fps"]) is None:
            raise ValueError("A skipped source is missing from this bank")
        notes[clip["id"]] = bank.describe(clip, data["fps"])
    bank.db.close()
    write_json(out / "notes.json", notes)
    print(POLICY, "previously indexed", hits, "of", len(notes))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("scan")
    s.add_argument("pilot", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--database", type=Path)
    s = sub.add_parser("label")
    s.add_argument("case", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("number", type=int)
    s.add_argument("actors", type=Path)
    s = sub.add_parser("publish")
    s.add_argument("pilot", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("database", type=Path)
    a = p.parse_args()
    if a.action == "scan":
        scan(a.pilot, a.out, a.database)
    elif a.action == "label":
        label(
            json.loads(a.case.read_text()),
            a.out,
            a.number,
            json.loads(a.actors.read_text()),
        )
    else:
        publish(a.pilot, a.out, a.database)
