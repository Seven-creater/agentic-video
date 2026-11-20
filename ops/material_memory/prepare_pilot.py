"""Build the fixed two-line pilot inputs in an isolated working directory."""

import argparse
import hashlib
import json
import math
import re
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


def subject_speech_rank(action, alias):
    """Rank a speaking action attached to the target ahead of speech elsewhere in the event."""
    if not action.startswith(alias):
        return 2
    own = re.split(r"[；;，,。→]|角色_", action[len(alias) :], maxsplit=1)[0]
    if any(word in own for word in ("吃", "咀嚼", "饮", "喝", "捂嘴")):
        return 2
    if any(
        word in own
        for word in (
            "说话",
            "开合",
            "回应",
            "开口",
            "发言",
            "交谈",
            "对话",
            "讲话",
            "喊",
            "应答",
            "呼叫",
            "叫喊",
        )
    ):
        return 0
    return (
        1
        if any(word in own for word in ("张口", "张嘴", "嘴动", "嘴部", "朗读", "吟唱"))
        else 2
    )


def reusable_proxy(old, dest, master_id, start, length):
    return (
        old.get("source_sha256") == master_id
        and old.get("source_frame") == start
        and old.get("frames") == length
        and old.get("file") == dest.name
        and dest.is_file()
        and sha256_file(dest) == old.get("sha256")
    )


def unresolved_lines(fixture_lines, previous, selection):
    pending = set(selection["needs_more_material"])
    previous_clips = {c["id"]: c for c in previous["clips"]}
    previous_lines = previous["lines"]
    if (
        not pending
        or len(selection["choices"]) != len(previous_lines)
        or any(
            previous_clips.get(k, {}).get("line") != line["id"]
            for line, k in zip(previous_lines, selection["choices"])
        )
    ):
        raise ValueError("Continuation and previous selection do not match")
    result = [line for line in fixture_lines if line["id"] in pending]
    if {line["id"] for line in result} != pending or not pending <= {
        line["id"] for line in previous_lines
    }:
        raise ValueError("Unresolved line is absent from the fixture or previous pilot")
    return result


def prepare(
    casefile, old_scan, fixture, out, after=None, per_line=10, continuation=None
):
    data = read(fixture)
    if per_line < 1:
        raise ValueError("At least one candidate per line is required")
    if continuation:
        if not after:
            raise ValueError("Continuation requires the previous pilot")
        data["lines"] = unresolved_lines(data["lines"], read(after), read(continuation))
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

    receipt = out / "pilot.json"
    prior = read(receipt) if receipt.is_file() else {}
    prior_clips = (
        {c["id"]: c for c in prior.get("clips", [])} if prior.get("fps") == 30 else {}
    )
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
    if after:
        previous = read(after)
        if {clip["source_sha256"] for clip in previous["clips"]} != {master_id}:
            raise ValueError("Cannot compare scene IDs across different source videos")
        excluded_scenes.update(previous["heldout_excluded_scenes"])
        excluded_scenes.update(clip["scene"] for clip in previous["clips"])
    catalog = []
    for record_text in (index / "records.jsonl").read_text().splitlines():
        r = json.loads(record_text)
        if r.get("kind") == "event" and r["source_sha256"] == master_id:
            catalog.append(r)

    result = []
    for fold in ("cold",) if after else ("warm", "cold"):
        used = set()
        for line in lines:
            actor = line["actor"]
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
            if continuation:
                # The old directory mentions multiple performers in the same record.
                rows = [r for r in catalog if r["action"].startswith(nick)]

            def order(row, nick=nick):
                rank = subject_speech_rank(row["action"], nick) if continuation else 0
                return rank, stable(row["record_id"])

            for r in sorted(rows, key=order):
                lo, hi = interval(r)
                start = math.ceil(lo * 30)
                length = line["frames"]
                if continuation:
                    start = max(start, math.floor(((lo + hi) * 30 - length) / 2))
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
                if not reusable_proxy(
                    prior_clips.get(key, {}), dest, master_id, start, length
                ):
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
                if continuation:
                    result[-1]["subject_speech_rank"] = subject_speech_rank(
                        r["action"], nick
                    )
                used.add(scene["scene_id"])
                nr += 1
                if nr == per_line:
                    break
            if nr != per_line:
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
    parser.add_argument(
        "--after", type=Path, help="Only sample fresh scenes outside this earlier pilot"
    )
    parser.add_argument("--per-line", type=int, default=10)
    parser.add_argument(
        "--continue-selection",
        type=Path,
        help="Retrieve new scenes for unresolved lines only",
    )
    args = parser.parse_args()
    prepare(
        args.casefile,
        args.old_scan,
        args.fixture,
        args.out,
        args.after,
        args.per_line,
        args.continue_selection,
    )
