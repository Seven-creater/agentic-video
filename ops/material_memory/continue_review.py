"""Continue a reviewed selection on one more pool of fresh scenes, only for unresolved lines."""

import argparse
from pathlib import Path

from observe_sources import scan
from portrait_observe import observe, publish, read
from prepare_pilot import prepare, unresolved_lines
from select_reviewed import run
from source_memory import SourceMemory

from omni_story.library.state import write_json


def continue_review(
    case, history, fixture, pilot, selection, roster, work, evaluation, limit
):
    first, picked = read(pilot), read(selection)
    plan = {
        line["id"]: {"clip": k, "pool": "initial"}
        for line, k in zip(first["lines"], picked["choices"], strict=True)
    }
    pending = picked["needs_more_material"]
    if pending:
        unresolved_lines(read(fixture)["lines"], first, picked)
        prepare(
            case,
            history,
            fixture,
            work,
            after=pilot,
            per_line=limit,
            continuation=selection,
        )
        manifest = work / "pilot.json"
        data = read(manifest)
        observations = work / "observations"
        # Reuse only the complete detector scan of exactly these source renditions.
        if not (observations / "tracks.json").exists():
            scan(manifest, observations)
        expected = {
            r["id"]: list(SourceMemory.key(r, data["fps"])) for r in data["clips"]
        }
        if read(observations / "scan_plan.json") != expected:
            raise ValueError(
                "Use a fresh continuation directory for a different candidate pool"
            )
        tracks = read(observations / "tracks.json")
        output = evaluation / "observed"
        for i in range((len(tracks) + 1) // 2):
            observe(read(case), observations, roster, output, i)
        database = work / "supplement.sqlite"
        publish(manifest, observations, roster, output, database)
        run(
            manifest,
            roster,
            database,
            read(case),
            evaluation / "selection",
            "cold",
            4,
            single_review=True,
        )
        replacement = read(evaluation / "selection/selection.json")
        for line, k in zip(data["lines"], replacement["choices"], strict=True):
            plan[line["id"]] = {"clip": k, "pool": "supplement"}
        pending = replacement["needs_more_material"]
    write_json(
        work / "edit.json",
        {"lines": plan, "needs_more_material": pending, "qualified": not pending},
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "case",
        "history",
        "fixture",
        "pilot",
        "selection",
        "roster",
        "work",
        "evaluation",
    ):
        parser.add_argument(name, type=Path)
    parser.add_argument("--limit", type=int, default=12)
    a = parser.parse_args()
    continue_review(
        a.case,
        a.history,
        a.fixture,
        a.pilot,
        a.selection,
        a.roster,
        a.work,
        a.evaluation,
        a.limit,
    )
