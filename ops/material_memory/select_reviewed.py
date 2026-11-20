"""Expand the candidate pool when a line lacks mouth evidence, then select from actual pictures."""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw
from portrait_observe import policy, read
from select_arm import normalize_pick, view_sheet
from source_memory import SourceMemory

from omni_story.library.dialogue_mouth_detail import sampled_frames
from omni_story.library.dialogue_mouth_edit import ask, encoded
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


def evidence(facts, line):
    voiced = set(line["voice_frames"])
    target, others = set(), set()
    for fact in facts or []:
        if fact["kind"] != "mouth":
            continue
        dst = target if fact["actor"] == line["actor"] else others
        dst.update(range(fact["start"], fact["end"]))
    return len((voiced & target) - others)


def consider(rows, line, read_facts, initial):
    if not rows or initial < 1 or not line["voice_frames"]:
        raise ValueError(
            "Expected candidates, positive review budget and voiced frames"
        )
    scores = {}

    def inspect(batch):
        for c in batch:
            facts = read_facts(c) or []
            local = [
                dict(
                    f,
                    start=f["start"] - c["source_frame"],
                    end=f["end"] - c["source_frame"],
                )
                for f in facts
            ]
            scores[c["id"]] = evidence(local, line)

    inspect(rows[:initial])
    needed = (len(line["voice_frames"]) + 1) // 2
    expanded = max(scores.values()) < needed and len(rows) > initial
    if expanded:
        inspect(rows[initial:])
    ranked = sorted(scores, key=lambda k: -scores[k])
    supported = [key for key in ranked if scores[key] >= needed]
    return {
        "initial": [c["id"] for c in rows[:initial]],
        "expanded": expanded,
        "scores": scores,
        "required": needed,
        "supported": supported,
    }


def reviewers_choice(case, out, line, pool, trace, blank):
    count = min(3, len(pool))
    prompt = (
        "离线库尚不能核实这句有一半发声帧是目标演员在开口。请从扩展后的目录挑最值得用真实连续帧复看的候选。"
        "本人出镜、嘴可见、有说话动作优先，优先排除进食、闪回和走过。"
        f'只回JSON代码块 {{"watch":["ID"]}}，必须列{count}个不同编号。'
        + encoded(
            {
                "line": {k: line[k] for k in ("actor", "appearance", "text")},
                "menu": [
                    {
                        "id": r["id"],
                        "description": r["description"],
                        "supported_frames": trace["scores"][r["id"]],
                    }
                    for r in pool
                ],
            }
        )
    )
    options = {r["id"] for r in pool}

    def check(v):
        keys = v["watch"]
        if (
            not isinstance(keys, list)
            or len(keys) != count
            or not all(isinstance(k, str) for k in keys)
            or len(set(keys)) != count
            or not set(keys) <= options
        ):
            raise ValueError("Give the requested number of distinct candidate IDs")
        return v

    tag = "research_watch_" + str(out.name)
    return check(ask(case, out, tag, prompt, blank, check))["watch"]


def inspect_picture(case, out, clip, media, card, line):
    """Send exactly one candidate. Bind the response to the supplied source, outside the model."""
    frames = sampled_frames(media / clip["file"], step=1)
    numbers = sorted({round(i * (len(frames) - 1) / 8) for i in range(9)})
    board = Image.new("RGB", (960, 800), "#202028")
    with Image.open(card) as actors:
        board.paste(actors, (0, 0))
    pen = ImageDraw.Draw(board)
    for i, n in enumerate(numbers):
        x, y = (i % 3) * 320, 185 + (i // 3) * 200
        pen.text((x + 4, y), f"{clip['id']}   F{n}", fill="white")
        board.paste(Image.fromarray(frames[n]).resize((320, 180)), (x, y + 16))
    out.mkdir(parents=True, exist_ok=True)
    photo = out / "source.jpg"
    board.save(photo, quality=94)
    prompt = (
        "下面九格都是同一镜头不同时间，第一排上方是演员对照图。目标是"
        + line["actor"]
        + "，"
        "找这个人物本人真实嘴部可见的一开一闭，留意别人说话、进食、遮挡、回忆小图、用手擦眼。"
        '只回JSON代码块 {"status":"visible/uncertain/other","open_frame":null,"closed_frame":null,"why":"本人头型、在做什么及嘴部变化"}。'
        "visible须给图上两个不同的有效帧编号；只见静态张嘴填uncertain。"
    )

    def validate(r):
        if r["status"] not in ("visible", "uncertain", "other") or not r["why"]:
            raise ValueError("Report the target performer and action")
        if r["status"] == "visible":
            a, b = r["open_frame"], r["closed_frame"]
            if (
                type(a) is not int
                or type(b) is not int
                or a not in numbers
                or b not in numbers
                or a == b
            ):
                raise ValueError(
                    "Give distinct visible open and closed frames for this video"
                )
        return r

    return validate(ask(case, out, "memory_single_picture", prompt, photo, validate))


def run(pilot, roster, database, case, out, fold, initial, single_review=False):
    data = read(pilot)
    if not {line["actor"] for line in data["lines"]} <= set(
        read(roster / "actors.json")
    ):
        raise ValueError("Verified cast card is missing a performer from the script")
    media = pilot.parent / "media"
    out.mkdir(parents=True, exist_ok=True)
    rows = [r for r in data["clips"] if r["fold"] == fold]
    options = {r["id"]: r for r in rows}
    if len(options) != len(rows) or any(
        sha256_file(media / r["file"]) != r["sha256"] for r in rows
    ):
        raise ValueError("Duplicate or changed candidate")
    # The provider's image endpoint uses JPG inputs.
    with Image.open(roster / "cast.png") as im:
        blank = out / "roster.jpg"
        im.convert("RGB").save(blank, quality=94)
    bank = SourceMemory(database, read_only=True, policy=policy(roster))
    traces = {}
    watched = []
    try:
        for i, line in enumerate(data["lines"]):
            group = [c for c in rows if c["line"] == line["id"]]

            def recall(c):
                found = bank.recall(c, data["fps"])
                if found is None:
                    raise ValueError("Incomplete observations for this cast or source")
                return found

            trace = consider(group, line, recall, initial)
            if trace["supported"]:
                finalists = trace["supported"][:3]
            else:
                pool = [r for r in group if r["id"] in trace["scores"]]
                finalists = reviewers_choice(
                    case, out / str(i), line, pool, trace, blank
                )
                best = max(trace["scores"], key=trace["scores"].get)
                if trace["scores"][best] > 0 and best not in finalists:
                    finalists[-1] = best
            if single_review:
                trace["single_reviews"] = {
                    k: inspect_picture(
                        case, out / ("check_" + k), options[k], media, blank, line
                    )
                    for k in finalists
                }
                visible = [
                    k
                    for k in finalists
                    if trace["single_reviews"][k]["status"] == "visible"
                ]
                if visible:
                    finalists = visible
            trace["watched"] = list(finalists)
            traces[line["id"]] = trace
            watched.extend(finalists)
    finally:
        bank.db.close()
    write_json(out / "review_trace.json", traces)
    preview = out / "watched.jpg"
    view_sheet([options[k] for k in watched], media, preview)
    with Image.open(preview) as scenes, Image.open(blank) as card:
        board = Image.new(
            "RGB",
            (max(scenes.width, card.width), scenes.height + card.height),
            "#202020",
        )
        board.paste(card, (0, 0))
        board.paste(scenes, (0, card.height))
    board.save(preview, quality=93)
    menu = [
        {
            "line": line["id"],
            "actor": line["actor"],
            "text": line["text"],
            "voiced": len(line["voice_frames"]),
            "pictures": [
                {"id": k, "observed": traces[line["id"]]["scores"][k]}
                for k in traces[line["id"]]["watched"]
            ],
        }
        for line in data["lines"]
    ]
    prompt = (
        "顶排是演员对照图。其后每一排是真实候选的五个连续时刻。为每句选一排画面，确认是说这句台词的演员本人出镜开口，考虑与前后镜头衔接。"
        "observed是此前核实的发声帧数，0表示未知。如果全部都看不清，只选试看片并在why说明。请勿把睁眼、回忆气泡或另一个人的嘴当作本人。"
        '只回JSON代码块 {"choices":["按台词顺序的ID"],"why":"每句实际看到的变化或不足"}。'
        + encoded(menu)
    )
    available = {k: options[k] for k in watched}

    def check(answer):
        result = normalize_pick(answer, available, data["lines"])
        if result["watch"] or not result["why"]:
            raise ValueError("Select a reviewed picture and explain")
        return result

    if single_review:
        prompt += (
            " 每个镜头已单独查过，编号由程序绑定到该文件。请结合逐段观察："
            + encoded(
                {
                    k: {
                        uid: r
                        for uid, r in trace["single_reviews"].items()
                        if uid in available
                    }
                    for k, trace in traces.items()
                }
            )
        )
    choice = check(ask(case, out, "memory_reviewed_pick", prompt, preview, check))
    pending = [
        line["id"]
        for line, k in zip(data["lines"], choice["choices"])
        if traces[line["id"]]["scores"][k] < traces[line["id"]]["required"]
        or (
            single_review
            and traces[line["id"]]["single_reviews"][k]["status"] != "visible"
        )
    ]
    write_json(
        out / "selection.json",
        {
            "choices": choice["choices"],
            "why": choice["why"],
            "watched": watched,
            "needs_more_material": pending,
            "qualified": not pending,
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("pilot", "roster", "database", "case", "out"):
        p.add_argument(name, type=Path)
    p.add_argument("--fold", choices=["warm", "cold"], default="cold")
    p.add_argument("--initial", type=int, default=4)
    p.add_argument(
        "--single-review",
        action="store_true",
        help="Verify each finalist separately before choosing a replacement",
    )
    a = p.parse_args()
    run(
        a.pilot,
        a.roster,
        a.database,
        read(a.case),
        a.out,
        a.fold,
        a.initial,
        a.single_review,
    )
