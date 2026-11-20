"""Select one fixed-length shot per line, optionally reading source observations."""

import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

from omni_story.library.dialogue_mouth_detail import sampled_frames
from omni_story.library.dialogue_mouth_edit import ask
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


def view_sheet(rows, media, dest):
    sheet = Image.new("RGB", (900, 115 * max(1, len(rows))), "#202020")
    pen = ImageDraw.Draw(sheet)
    for i, row in enumerate(rows):
        frames = sampled_frames(media / row["file"], step=1)[::3]
        pen.text((4, 115 * i), row["id"], fill="white")
        for n in range(5):
            j = round(n * (len(frames) - 1) / 4)
            tile = Image.fromarray(frames[j]).resize((176, 96))
            sheet.paste(tile, (180 * n, 115 * i + 17))
    sheet.save(dest, quality=88)


def normalize_pick(reply, options, lines):
    """Accept a list of IDs or explicit per-line selections without guessing ownership."""
    watch, choices = reply.get("watch", []), reply.get("choices", [])
    if isinstance(choices, dict) and set(choices) == {line["id"] for line in lines}:
        choices = [choices[line["id"]] for line in lines]
    reason = reply.get("why")
    if (
        isinstance(choices, list)
        and choices
        and all(isinstance(c, dict) for c in choices)
    ):
        if [c.get("line") for c in choices] != [line["id"] for line in lines]:
            raise ValueError("选择中的台词编号或顺序不符")
        if not reason and all(c.get("why") for c in choices):
            reason = [c["why"] for c in choices]
        choices = [c.get("candidate") for c in choices]
    if not isinstance(watch, list) or not isinstance(choices, list):
        raise TypeError("watch与choices须为列表")
    if watch:
        if (
            not all(isinstance(k, str) for k in watch)
            or len(watch) > 4
            or len(set(watch)) != len(watch)
            or set(watch) - options.keys()
            or choices
        ):
            raise ValueError("最多看4个不重复候选，待看片时choices为空")
    elif len(choices) != len(lines) or any(
        not isinstance(k, str) or options.get(k, {}).get("line") != line["id"]
        for k, line in zip(choices, lines)
    ):
        raise ValueError("按台词顺序各选择一个属于该句的候选")
    return {"watch": watch, "choices": choices, "why": reason}


def run(pilot, folder, case, fold, database):
    data = json.loads(pilot.read_text())
    folder.mkdir(parents=True, exist_ok=True)
    media = pilot.parent / "media"
    rows = sorted(
        (r for r in data["clips"] if r["fold"] == fold), key=lambda r: r["id"]
    )
    for r in rows:
        if sha256_file(media / r["file"]) != r["sha256"]:
            raise ValueError("Source changed: " + r["id"])
    options = {r["id"]: r for r in rows}
    lines = [
        {k: row[k] for k in ("id", "role", "actor", "appearance", "text", "frames")}
        for row in data["lines"]
    ]
    facts = {}
    if database:
        from source_memory import SourceMemory

        bank = SourceMemory(database, read_only=True)
        try:
            facts = {r["id"]: bank.describe(r, data["fps"]) for r in rows}
        finally:
            bank.db.close()
    menu = [{k: r[k] for k in ("id", "line", "description")} for r in rows]
    for row in menu:
        if database:
            row["source_observation"] = facts[row["id"]]
    prompt = (
        "为下列两句原配音逐句各选择一个完整的镜头。同一句的候选等长，按句依次播。优先本人开口，"
        "让问话和接话清楚，再考虑动作情境和相邻镜头。目录文字是旧的粗描述，可能认错。"
        "若有源观察，其中帧号为镜头内帧，未核实表示未知。可请求一次看片，最多4个候选，"
        "已有足够证据也可直接选。两组可用预算相同。"
        '请在一个 JSON 代码块内回复，块外留空。看片格式 {"watch":["候选编号"],"choices":[],"why":"想查什么"}；'
        '定稿格式 {"watch":[],"choices":["首句编号","次句编号"],"why":"依据"}。每个选择必须来自相应台词。\n'
        + json.dumps({"lines": lines, "menu": menu}, ensure_ascii=False)
    )

    def validate(value):
        return normalize_pick(value, options, lines)

    poster = folder / "catalog.jpg"
    view_sheet([], media, poster)
    start = time.monotonic()
    first = validate(ask(case, folder, "memory_pick", prompt, poster, validate))
    reply = first
    watched = first["watch"]
    if watched:
        preview = folder / "requested.jpg"
        view_sheet([options[k] for k in watched], media, preview)

        def finish(v):
            v = validate(v)
            if v["watch"]:
                raise ValueError("看片机会已用完，填写choices")
            return v

        reply = finish(
            ask(
                case,
                folder,
                "memory_decide",
                prompt
                + "\n现在已经按顺序展示这些候选的时间序列："
                + str(watched)
                + "。请给最终选择。",
                preview,
                finish,
            )
        )
    result = {
        "choices": reply["choices"],
        "why": reply["why"],
        "watched": watched,
        "decision_rounds": 1 + bool(watched),
        "seconds": round(time.monotonic() - start, 2),
    }
    write_json(folder / "selection.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pilot", type=Path)
    p.add_argument("folder", type=Path)
    p.add_argument("case", type=Path)
    p.add_argument("fold", choices=["warm", "cold"])
    p.add_argument("--memory", type=Path)
    a = p.parse_args()
    run(a.pilot, a.folder, json.loads(a.case.read_text()), a.fold, a.memory)
