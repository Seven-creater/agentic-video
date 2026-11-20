"""Reproduce the dialogue baseline from an archived outcome and its matching film."""

import argparse
from itertools import groupby
import json
from pathlib import Path

from omni_story.library.media import sha256_file


def frame_ranges(frames):
    """Return half-open ranges on the rendered film timeline."""
    ranges = []
    for _, group in groupby(enumerate(frames), key=lambda pair: pair[1] - pair[0]):
        run = [frame for _, frame in group]
        ranges.append([run[0], run[-1] + 1])
    return ranges


def replay(report, film):
    render = report["render"]
    digest = sha256_file(film)
    if digest != render["video_sha256"]:
        raise ValueError("The film SHA-256 does not match the render in this outcome")

    score = report["score"]
    lines = []
    for line in score["lines"]:
        frames = sorted(line["confirmed_at"])
        voiced = line["voiced"]
        if (len(set(frames)) != len(frames) or len(frames) != line["approved"]
                or len(frames) > voiced or any(not 0 <= f < render["frames"] for f in frames)):
            raise ValueError(f"Inconsistent confirmed frames for line {line['id']}")
        competing = {f for actor_frames in line["competing_mouths"].values() for f in actor_frames}
        if len(competing) != line["competing_mouth_frames"] or competing.intersection(frames):
            raise ValueError(f"Inconsistent competing mouths for line {line['id']}")
        lines.append({
            "id": line["id"], "actor": line["actor"], "voiced": voiced,
            "confirmed": len(frames), "confirmed_render_ranges": frame_ranges(frames),
            "competing": len(competing),
        })

    totals = {
        "total": len(lines),
        "passed": sum(line["voiced"] > 0 and line["confirmed"] == line["voiced"] for line in lines),
        "voiced_frames": sum(line["voiced"] for line in lines),
        "confirmed_frames": sum(line["confirmed"] for line in lines),
        "competing_mouth_frames": sum(line["competing"] for line in lines),
    }
    if any(value != score[key] for key, value in totals.items()):
        raise ValueError("The per-line measurements do not match the saved totals")
    return {"render_sha256": digest, "summary": totals, "lines": lines}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outcome", type=Path)
    parser.add_argument("film", type=Path)
    args = parser.parse_args()
    try:
        result = replay(json.loads(args.outcome.read_text(encoding="utf-8")), args.film)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
