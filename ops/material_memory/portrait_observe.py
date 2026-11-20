"""Attribute a tracked mouth using cast photos, the whole scene and numbered source frames."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from source_memory import POLICY, SourceMemory

from omni_story.library.dialogue_mouth_detail import sampled_frames
from omni_story.library.dialogue_mouth_edit import ask
from omni_story.library.dialogue_mouth_tracks import witness_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


def read(path):
    return json.loads(path.read_text())


def build_roster(manifest, media, out):
    """Build the actor card from reviewed crops of a separate development clip."""
    refs = read(manifest)
    if not refs or len({r["actor"] for r in refs}) != len(refs):
        raise ValueError("Expected one verified reference for each actor")
    out.mkdir(parents=True, exist_ok=True)
    board = Image.new("RGB", (230 * len(refs), 180), "#202830")
    pen = ImageDraw.Draw(board)
    for i, ref in enumerate(refs):
        path = media / ref["file"]
        if sha256_file(path) != ref["sha256"]:
            raise ValueError("Cast exemplar changed")
        frame = Image.fromarray(sampled_frames(path, step=1)[ref["frame"]])
        tile = ImageOps.contain(frame.crop(tuple(ref["box"])), (225, 155))
        x = i * 230
        board.paste(tile, (x + (230 - tile.width) // 2, 0))
        pen.text((x + 95, 159), ref["actor"], fill="white")
    board.save(out / "cast.png")
    write_json(out / "actors.json", [r["actor"] for r in refs])


def policy(roster):
    actors = read(roster / "actors.json")
    # Actor IDs as well as the labelled portraits determine the meaning of the facts.
    digest = hashlib.sha256(
        (json.dumps(actors) + sha256_file(roster / "cast.png")).encode()
    )
    return "portrait-context-mouth-v2/" + digest.hexdigest()


def witness_ids(track):
    return witness_frames(
        {
            int(k): v
            for k, v in sorted(track["centers"].items(), key=lambda kv: int(kv[0]))
        }
    )


def verdict(row, track, actors):
    if (
        row["id"] != track["id"]
        or row["actor"] not in [*actors, "unknown"]
        or row["layer"] not in ("scene", "inset", "graphic", "unknown")
        or row["feature"] not in ("mouth", "eye", "other", "unknown")
        or row["motion"] not in ("speech", "held", "food", "unknown")
        or not isinstance(row.get("why"), str)
        or not row["why"].strip()
    ):
        raise ValueError("Complete all observations for the tracked region")
    ids = witness_ids(track)
    for field in ("open_frame", "closed_frame"):
        n = row[field]
        if n is not None and (type(n) is not int or n not in ids):
            raise ValueError("Use the F numbers in that track only")
    verified = (
        row["layer"] == "scene"
        and row["feature"] == "mouth"
        and row["motion"] == "speech"
        and row["actor"] in actors
        and row["open_frame"] is not None
        and row["closed_frame"] is not None
        and row["open_frame"] != row["closed_frame"]
    )
    return {
        "id": row["id"],
        "kind": "mouth" if verified else "unknown",
        "actor": row["actor"] if verified else "unknown",
        "why": row["why"],
        "evidence": row,
    }


def binding(track, observations, roster_policy):
    metadata = [
        roster_policy,
        track,
        sha256_file(observations / (track["id"] + ".jpg")),
    ]
    return hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()


def observe(case, observations, roster, out, batch):
    actors = read(roster / "actors.json")
    tracks = read(observations / "tracks.json")[2 * batch : 2 * batch + 2]
    if not tracks:
        raise ValueError("No tracks in batch")
    out.mkdir(parents=True, exist_ok=True)
    with Image.open(roster / "cast.png") as cast:
        board = Image.new(
            "RGB",
            (max(1040, cast.width), cast.height + 20 + 610 * len(tracks)),
            "#182127",
        )
        board.paste(cast, (0, 0))
        top = cast.height + 20
    draw = ImageDraw.Draw(board)
    for i, track in enumerate(tracks):
        y = top + i * 610
        draw.text((10, y), track["id"] + "   TARGET", fill="white")
        with Image.open(observations / (track["id"] + ".jpg")) as image:
            board.paste(image, (0, y + 20))
    photo = out / f"portrait_{batch}.jpg"
    board.save(photo, quality=94)
    roster_policy = policy(roster)
    prompt = (
        "最上排是经过核实的本次目标演员的证件照。下面每张大图独立判断左上蓝框内的同一个部位。"
        "先看全景，辨明蓝框属于实际出场的人(scene)、泡泡或画中画(inset)、字幕图案(graphic)或unknown。"
        "再看局部判断中心是嘴、眼还是别的。对照证件照的头顶轮廓和颜色认人；看不清、没有对应演员一律unknown。"
        "最后逐格看底排真实帧，若看到说话嘴的一开一闭，抄下各自标出的F数字；只有张嘴不变、眨眼、进食、"
        "只有雨线经过都不算说话。看不清用null。每行说明目标部位和看到的变化。"
        '只回复JSON代码块 {"rows":[{"id":"T...","actor":"演员编号或unknown","layer":"scene/inset/graphic/unknown",'
        '"feature":"mouth/eye/other/unknown","motion":"speech/held/food/unknown","open_frame":null,"closed_frame":null,'
        '"why":"实际证据"}]}。按此顺序逐项填写：'
        + str([(t["id"], witness_ids(t)) for t in tracks])
    )

    def validate(answer):
        rows = answer["rows"]
        if len(rows) != len(tracks):
            raise ValueError("Return every track exactly once")
        for row, track in zip(rows, tracks):
            verdict(row, track, actors)
        return answer

    answer = ask(
        case,
        out / f"batch_{batch}",
        f"memory_portrait_{batch}",
        prompt,
        photo,
        validate,
    )
    write_json(
        out / f"binding_{batch}.json",
        [binding(t, observations, roster_policy) for t in tracks],
    )
    write_json(
        out / f"labels_{batch}.json",
        [verdict(r, t, actors) for r, t in zip(answer["rows"], tracks)],
    )


def publish(
    pilot, observations, roster, out, database, library_root=None, reviewed_tracks=None
):
    data = read(pilot)
    plan = read(observations / "scan_plan.json")
    expected = {
        c["id"]: list(SourceMemory.key(c, data["fps"], POLICY)) for c in data["clips"]
    }
    if any(expected.get(key) != signature for key, signature in plan.items()):
        raise ValueError("Detector scan and pilot sources differ")
    tracks = read(observations / "tracks.json")
    rows = [
        row
        for i in range((len(tracks) + 1) // 2)
        for row in read(out / f"labels_{i}.json")
    ]
    if [t["id"] for t in tracks] != [r["id"] for r in rows]:
        raise ValueError("Missing or reordered labels")
    roster_policy = policy(roster)
    for i in range((len(tracks) + 1) // 2):
        if read(out / f"binding_{i}.json") != [
            binding(t, observations, roster_policy) for t in tracks[2 * i : 2 * i + 2]
        ]:
            raise ValueError("Source evidence or cast card changed after observation")
    by_clip = {c["id"]: [] for c in data["clips"]}
    clips = {c["id"]: c for c in data["clips"]}
    for t, r in zip(tracks, rows):
        if t["clip"] not in plan:
            raise ValueError("Track belongs to a source outside the scan plan")
        c = clips[t["clip"]]
        by_clip[c["id"]].append(
            {
                **r,
                "start": c["source_frame"] + t["start"],
                "end": c["source_frame"] + t["end"],
            }
        )
    bank = SourceMemory(database, policy=roster_policy)
    try:
        for c in data["clips"]:
            if c["id"] in plan:
                bank.remember(c, data["fps"], by_clip[c["id"]])
            elif bank.recall(c, data["fps"]) is None:
                raise ValueError("A skipped source is missing from this cast bank")
    finally:
        bank.db.close()
    if library_root:
        from omni_story.library import scene_memory, scene_pick

        reviews = read(reviewed_tracks) if reviewed_tracks else None
        if reviews is not None and set(reviews) != {t["id"] for t in tracks}:
            raise ValueError("Archived review must refer to this set of tracks")
        entries = []
        for c in data["clips"]:
            if c["id"] in plan:
                slots = [
                    dict(
                        f,
                        start=f["start"] - c["source_frame"],
                        end=f["end"] - c["source_frame"],
                    )
                    for f in by_clip[c["id"]]
                    if f["kind"] == "mouth"
                    and (reviews is None or reviews[f["id"]] == f["actor"])
                ]
                entries.append(
                    (
                        c["source_sha256"],
                        c["sha256"],
                        c["source_frame"],
                        c["frames"],
                        slots,
                    )
                )
        scene_memory.publish(
            scene_pick.load(library_root),
            entries,
            data["fps"],
            roster_policy + ("/reviewed" if reviews is not None else ""),
        )
    write_json(out / "predictions.json", rows)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("roster")
    for name in ("manifest", "media", "out"):
        s.add_argument(name, type=Path)
    s = sub.add_parser("observe")
    for name in ("case", "observations", "roster", "out"):
        s.add_argument(name, type=Path)
    s.add_argument("batch", type=int)
    s = sub.add_parser("publish")
    for name in ("pilot", "observations", "roster", "out", "database"):
        s.add_argument(name, type=Path)
    s.add_argument(
        "--library-root",
        type=Path,
        help="Share cast-bound mouth leads with normal scene selection",
    )
    s.add_argument(
        "--reviewed-tracks",
        type=Path,
        help="Apply completed source corrections to shared scene hints",
    )
    a = p.parse_args()
    if a.action == "roster":
        build_roster(a.manifest, a.media, a.out)
    elif a.action == "observe":
        observe(read(a.case), a.observations, a.roster, a.out, a.batch)
    else:
        publish(
            a.pilot,
            a.observations,
            a.roster,
            a.out,
            a.database,
            a.library_root,
            a.reviewed_tracks,
        )
