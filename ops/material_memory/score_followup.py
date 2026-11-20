"""Render a continuation preview with earlier shots and audio preserved for resolved lines."""

import argparse
import tempfile
from pathlib import Path

from score_pair import read, run

from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


def assemble(base, original, supplement, revised, out):
    first, extra = (read(p / "pilot.json") for p in (base, supplement))
    picks, replacements = read(original), read(revised)
    pending = set(picks["needs_more_material"])
    extra_lines = extra["lines"]
    if (
        not pending
        or pending != {line["id"] for line in extra_lines}
        or len(replacements["choices"]) != len(extra_lines)
        or len(picks["choices"]) != len(first["lines"])
        or first["fps"] != extra["fps"]
    ):
        raise ValueError("Continuation must cover the unresolved lines only")
    extra_ids = {line["id"]: k for line, k in zip(extra_lines, replacements["choices"])}
    base_lines = {l["id"]: l for l in first["lines"]}
    for line in extra_lines:
        earlier = base_lines[line["id"]]
        if earlier != line or sha256_file(
            base / "media" / earlier["wav"]
        ) != sha256_file(supplement / "media" / line["wav"]):
            raise ValueError("Replacement changed the audio, speaker, text or timing")
    original_scenes = set(first["heldout_excluded_scenes"]) | {
        c["scene"] for c in first["clips"]
    }
    if original_scenes.intersection(c["scene"] for c in extra["clips"]):
        raise ValueError("Supplement overlaps a previous scene")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as root:
        tmp = Path(root)
        media = tmp / "media"
        media.mkdir()
        selected = []
        clips = []
        tracks = []
        labels = {}
        origins = {}
        for line, k in zip(first["lines"], picks["choices"]):
            lid = line["id"]
            selected.append(extra_ids.get(lid, k))
            origins[lid] = "supplement" if lid in pending else "initial"
            (media / line["wav"]).symlink_to((base / "media" / line["wav"]).resolve())
        for tag, src, data in (
            ("initial", base, first),
            ("supplement", supplement, extra),
        ):
            ids = {
                k
                for line, k in zip(first["lines"], selected)
                if origins[line["id"]] == tag
            }
            for c in data["clips"]:
                if c["id"] in ids:
                    clips.append(c)
                    (media / c["file"]).symlink_to(
                        (src / "media" / c["file"]).resolve()
                    )
            gold = read(src / "independent_tracks.json")
            for t in read(src / "observations/tracks.json"):
                if t["clip"] in ids:
                    tid = tag + "_" + t["id"]
                    tracks.append(dict(t, id=tid))
                    labels[tid] = gold[t["id"]]
        write_json(
            tmp / "pilot.json",
            {"fps": first["fps"], "lines": first["lines"], "clips": clips},
        )
        write_json(tmp / "tracks.json", tracks)
        write_json(tmp / "blind.json", labels)
        unresolved = replacements["needs_more_material"]
        write_json(
            out / "selection.json",
            {
                "choices": selected,
                "origin": origins,
                "needs_more_material": unresolved,
                "qualified": not unresolved,
            },
        )
        run(
            tmp / "pilot.json",
            out / "selection.json",
            tmp / "tracks.json",
            tmp / "blind.json",
            out,
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for k in ("base", "original", "supplement", "revised", "out"):
        p.add_argument(k, type=Path)
    a = p.parse_args()
    assemble(a.base, a.original, a.supplement, a.revised, a.out)
