"""Build the fixed two-line pilot inputs in an isolated working directory."""

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

from omni_story.library.dialogue_mouth_bundle import voice_frames
from omni_story.library.media import sha256_file


def ff(*args):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-threads",
            "1",
            *map(str, args),
        ],
        check=True,
    )


def read(p):
    return json.loads(p.read_text())


def interval(r):
    return (r["source_start_s"], r["source_end_s"])


def overlaps(lo, hi, intervals):
    return any(lo < b and a < hi for a, b in intervals)


def stable(value):
    return hashlib.sha256(("mouth-pilot-1/" + value).encode()).hexdigest()


def prepare(casefile, old_scan, fixture, out):
    data = json.loads(fixture.read_text())
    case = json.loads(casefile.read_text())
    config = Path(case["configuration_path"])
    cfg = json.loads(config.read_text())["input_lock"]["configuration"]
    index = Path(cfg["material_index_root"])
    lib = Path(cfg["visual_library_root"])
    scenes = json.loads((lib.parents[2] / "scene_pick/scenes.json").read_text())[
        "scenes"
    ]
    master_id = next(iter(case["clips"].values()))["source_sha256"]
    source = next(
        s
        for s in json.loads((config.parent / "catalog/inventory.json").read_text())[
            "sources"
        ]
        if s["sha256"] == master_id
    )
    if sha256_file(source["path"]) != master_id:
        raise ValueError("Published source and video hash differ")
    media = out / "media"
    media.mkdir(parents=True, exist_ok=True)

    lines = []
    for line in data["lines"]:
        wav = media / (line["id"] + ".wav")
        ff("-i", line["wav"], "-ar", 48000, "-ac", 1, "-c:a", "pcm_s16le", wav)
        raw = subprocess.check_output(
            ["ffmpeg", "-loglevel", "error", "-i", str(wav), "-f", "s16le", "-"]
        )
        samples = np.frombuffer(raw, "<i2")
        frames = math.ceil(len(samples) / 1600)
        lines.append(
            {
                "id": line["id"],
                "text": line["text"],
                "role": line["role"],
                "actor": line["actor"],
                "appearance": line["appearance"],
                "frames": frames,
                "voice_frames": voice_frames(samples, 0, frames),
                "wav": wav.name,
            }
        )
    roles = {line["actor"]: line for line in lines}
    warm = {}
    exclude = []

    for folder in (old_scan, old_scan / "extra"):
        for actor, rr in read(folder / "index_shortlist.json").items():
            for r in rr:
                warm[r["record_id"]] = (actor, r)
                exclude.append(interval(r))
    for clip in case["clips"].values():
        lo = clip["source_start_s"]
        exclude.append((lo, lo + clip["frames"] / 30))
    exclude.extend(data["development_ranges"])

    excluded_scenes = {
        s["scene_id"]
        for s in scenes
        if overlaps(s["source_start_s"], s["source_end_s"], exclude)
    }
    catalog = []
    for record_text in (index / "records.jsonl").read_text().splitlines():
        r = json.loads(record_text)
        if r.get("kind") == "event" and r["source_sha256"] == master_id:
            catalog.append(r)

    result = []
    for fold in ("warm", "cold"):
        used = set()
        for actor, line in roles.items():
            nick = data["aliases"][actor]
            rows = (
                [r for a, r in warm.values() if a == actor]
                if fold == "warm"
                else [
                    r
                    for r in catalog
                    if r["action"].startswith(nick)
                    and any(
                        t in r["action"] + r["speech"]
                        for t in (
                            "交谈",
                            "开合",
                            "对话",
                            "说话",
                            "回应",
                            "张口",
                            "张嘴",
                        )
                    )
                ]
            )
            nr = 0
            for r in sorted(rows, key=lambda r: stable(r["record_id"])):
                lo, hi = interval(r)
                start = math.ceil(lo * 30)
                length = line["frames"]
                if (start + length) / 30 > hi:
                    continue
                scene = next(
                    (
                        s
                        for s in scenes
                        if s["source_start_s"] <= start / 30
                        and (start + length) / 30 <= s["source_end_s"]
                    ),
                    None,
                )
                if (
                    not scene
                    or scene.get("duplicate_of")
                    or scene["scene_id"] in used
                    or (fold == "cold" and scene["scene_id"] in excluded_scenes)
                ):
                    continue
                key = stable("public/" + r["record_id"])[:8]
                dest = media / (key + ".mp4")
                ff(
                    "-ss",
                    f"{start / 30:.9f}",
                    "-i",
                    source["path"],
                    "-an",
                    "-vf",
                    "fps=30,scale=960:540,setsar=1",
                    "-frames:v",
                    length,
                    "-threads",
                    1,
                    "-c:v",
                    "libx264",
                    "-crf",
                    16,
                    dest,
                )
                result.append(
                    {
                        "id": key,
                        "fold": fold,
                        "line": line["id"],
                        "expected_actor": actor,
                        "source_sha256": master_id,
                        "scene": scene["scene_id"],
                        "source_frame": start,
                        "frames": length,
                        "sha256": sha256_file(dest),
                        "file": dest.name,
                        "description": r["action"] + "。" + r["place"],
                        "record": r["record_id"],
                    }
                )
                used.add(scene["scene_id"])
                nr += 1
                if nr == 10:
                    break
            if nr != 10:
                raise ValueError((fold, actor, nr))
    (out / "pilot.json").write_text(
        json.dumps(
            {
                "fps": 30,
                "lines": lines,
                "clips": result,
                "heldout_excluded_scenes": sorted(excluded_scenes),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        "ready",
        len(result),
        len(excluded_scenes),
        [(line["frames"], len(line["voice_frames"])) for line in lines],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("casefile", "old_scan", "fixture", "out"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    prepare(args.casefile, args.old_scan, args.fixture, args.out)
