"""Compare attributed mouth tracks to a separate, case-blinded review before reusing a rule."""

import argparse
import json
from pathlib import Path


def audit(tracks, predictions, reviews):
    ids = {t["id"] for t in tracks}
    if (
        len(tracks) != len(ids)
        or set(reviews) != ids
        or {r["id"] for r in predictions} != ids
        or len(predictions) != len(ids)
    ):
        raise ValueError(
            "Detector, observations and independent review must cover the same tracks once"
        )
    matched, contradicted, unresolved = [], [], []
    for row in predictions:
        if row["kind"] != "mouth" or row["actor"] in ("unknown", "other_actor"):
            continue
        uid = row["id"]
        if reviews[uid] == row["actor"]:
            matched.append(uid)
        elif reviews[uid] == "uncertain":
            unresolved.append(uid)
        else:
            contradicted.append(uid)
    return {
        "tracks": len(tracks),
        "attributed": len(matched) + len(contradicted) + len(unresolved),
        "matched": matched,
        "contradicted": contradicted,
        "unresolved": unresolved,
        "finding": "contradicted_source_claims"
        if contradicted
        else "no_contradiction_in_reviewed_claims",
    }


def candidate_evidence(pilot, tracks, reviews):
    """Count source frames with an independently attributed cycle during the fixed audio."""
    by_clip = {}
    for track in tracks:
        actor = reviews[track["id"]]
        frames = by_clip.setdefault((track["clip"], actor), set())
        frames.update(range(track["start"], track["end"]))
    lines = {line["id"]: line for line in pilot["lines"]}
    groups = {}
    for clip in pilot["clips"]:
        line = lines[clip["line"]]
        cycles = by_clip.get((clip["id"], line["actor"]), set())
        overlap = len(cycles.intersection(line["voice_frames"]))
        group = groups.setdefault(clip["fold"], {}).setdefault(line["id"], {})
        group[clip["id"]] = overlap
    return groups


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("observations", type=Path)
    p.add_argument("blind_labels", type=Path)
    p.add_argument(
        "--pilot", type=Path, help="Also report source coverage per candidate"
    )
    a = p.parse_args()
    tracks = json.loads((a.observations / "tracks.json").read_text())
    rows = [
        r
        for n in range((len(tracks) + 3) // 4)
        for r in json.loads((a.observations / f"labels_{n}.json").read_text())
    ]
    reviews = json.loads(a.blind_labels.read_text())
    report = audit(tracks, rows, reviews)
    if a.pilot:
        report["source_voiced_overlap"] = candidate_evidence(
            json.loads(a.pilot.read_text()), tracks, reviews
        )
    print(json.dumps(report, indent=2))
