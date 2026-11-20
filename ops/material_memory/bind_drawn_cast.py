"""Bind on-screen performers to the library using the selected source times and clean pictures."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw
from render_drawn_takes import decode

from omni_story.library import scene_memory, scene_pick
from omni_story.library.dialogue_mouth_edit import ask
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


def source_photo(source, frame):
    arr = decode(source, frame, frame + 1, 320, 180)
    return Image.fromarray(arr[0])


def bind(manifest, run, out):
    case = json.loads(manifest.read_text())
    cfg_path = run / "library_state.json"
    cfg = json.loads(cfg_path.read_text())["input_lock"]["configuration"]
    catalog = scene_pick.load(cfg["visual_library_root"])
    digest = case["assets"]["source"]["sha256"]
    source = Path(case["assets"]["source"]["path"])
    if (
        not catalog
        or digest != catalog["source_sha256"]
        or sha256_file(source) != digest
    ):
        raise ValueError("The registered catalog and witnessed source must match")
    by_entity = {
        actor["entity_id"]: code for code, actor in catalog["characters"].items()
    }
    events = []
    with (Path(cfg["material_index_root"]) / "records.jsonl").open() as rows:
        for row in rows:
            record = json.loads(row)
            if record["kind"] == "event" and record["source_sha256"] == digest:
                events.append(record)
    by_role = defaultdict(list)
    for role, person in case["decision"]["cast"].items():
        by_role[role] = [k for k in person["takes"] if k in case["takes"]]
    out.mkdir(parents=True, exist_ok=True)
    roles = {}
    for index, (role, takes) in enumerate(by_role.items()):
        if not takes:
            raise ValueError("Empty cast evidence")
        intervals = {}
        for take in takes:
            t = case["takes"][take]["open"] / 30
            intervals[take] = sorted(
                {
                    by_entity[e]
                    for row in events
                    if row["source_start_s"] <= t < row["source_end_s"]
                    for e in row["entity_ids"]
                    if e in by_entity
                }
            )
        codes = sorted({c for options in intervals.values() for c in options})
        pictures = [(take, case["takes"][take]["open"]) for take in takes]
        if not codes:
            roles[role] = "unknown"
            continue
        examples = []
        for code in codes:
            key = catalog["characters"][code]["entity_id"]
            solo = [row for row in events if set(row["entity_ids"]) == {key}]
            if not solo:
                continue
            # The registration window is preferred. A long isolated view comes next.
            best = max(
                solo,
                key=lambda row: (
                    row["window_id"] in key,
                    row["source_end_s"] - row["source_start_s"],
                ),
            )
            examples.append(
                (code, round(15 * (best["source_start_s"] + best["source_end_s"])))
            )
        if not examples:
            roles[role] = "unknown"
            continue
        gallery = pictures + examples
        board = Image.new("RGB", (3 * 320, 202 * ((len(gallery) + 2) // 3)), "white")
        pen = ImageDraw.Draw(board)
        for n, (label, frame) in enumerate(gallery):
            x, y = n % 3 * 320, n // 3 * 202
            pen.text((x + 4, y + 2), label, fill="black")
            board.paste(source_photo(source, frame), (x, y + 17))
        image = out / f"role_{index}.jpg"
        board.save(image, quality=93)
        offered = {c: catalog["characters"][c]["appearance"] for c, _ in examples}
        prompt = (
            "左上开始、编号不是C开头的每张图是此次同一位说话演员的实际出场原画。"
            "下方C编号是素材库该演员单独出场的原片参照。根据完整头部、耳、颜色、鼻和尾巴在照片间辨认。"
            "画面内另有听众时，选多张图中反复出现的同一个说话人。库编号在过去某场漏标或错标很常见，"
            "跨场不能用编号交集替代外观核对。若两编号外观确实相同，才用实际嘴动原帧的登记消歧。"
            '若没有一致的参照或无法区分，选unknown。返回 { "code":"C编号或unknown", "why":"对照的具体外观与不同项" }。'
            + json.dumps(
                {"role": role, "catalog": offered, "events_at_actual_mouth": intervals},
                ensure_ascii=False,
            )
        )

        def validate(row, offered=offered):
            if row["code"] not in {*offered, "unknown"} or not row["why"]:
                raise ValueError("Use one of the pictured codes, or unknown")
            return row

        result = ask(
            {"configuration_path": str(cfg_path)},
            out,
            f"native_cast_{index}",
            prompt,
            image,
            validate,
        )
        write_json(
            out / f"role_{index}.json",
            {
                "role": role,
                "options": offered,
                "source_intervals": intervals,
                "result": result,
            },
        )
        roles[role] = result["code"]
    if len({v for v in roles.values() if v != "unknown"}) != sum(
        v != "unknown" for v in roles.values()
    ):
        raise ValueError("Distinct performers were assigned the same library actor")
    return roles


def bind_manifest(manifest, roles):
    case = json.loads(manifest.read_text())
    case["role_cast"] = {
        name: code if code != "unknown" else name for name, code in roles.items()
    }
    assigned = {}
    for role, person in case["decision"]["cast"].items():
        for key in person["takes"]:
            if key not in case["takes"]:
                continue
            if key in assigned and assigned[key] != role:
                raise ValueError("Two roles claim the same source performance")
            assigned[key] = role
            case["takes"][key]["actor"] = case["role_cast"][role]
    return case


def publish(manifest, run, verified_roles):
    """Store only the original witnessed interval, never the retimed duration of a finished line."""
    from hashlib import sha256

    case = json.loads(manifest.read_text())
    cfg = json.loads((run / "library_state.json").read_text())["input_lock"][
        "configuration"
    ]
    catalog = scene_pick.load(cfg["visual_library_root"])
    source = Path(case["assets"]["source"]["path"])
    digest = case["assets"]["source"]["sha256"]
    if sha256_file(source) != digest or catalog["source_sha256"] != digest:
        raise ValueError("Source changed since verification")
    entries = defaultdict(list)
    for role, person in case["decision"]["cast"].items():
        code = verified_roles.get(role)
        if code not in catalog["characters"]:
            continue
        for k in person["takes"]:
            if k not in case["takes"]:
                continue
            take = case["takes"][k]
            first, end = take["clip_bounds"]
            begin, stop = take["witness_bounds"]
            entries[(first, end)].append(
                {"actor": code, "start": begin - first, "end": stop - first}
            )
    clips = []
    for (a, b), slots in entries.items():
        view = decode(source, a, b, 640, 360)
        clips.append((digest, sha256(view).hexdigest(), a, b - a, slots))
    scene_memory.publish(
        catalog, clips, 30, "native_contour_with_cast_cards_v1/reviewed"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Save confirmed source observations for subsequent scene selection",
    )
    options = parser.parse_args()
    roles = bind(options.manifest, options.run, options.output)
    write_json(options.output / "roles.json", roles)
    write_json(
        options.output / "bound_manifest.json", bind_manifest(options.manifest, roles)
    )
    if options.publish:
        publish(options.manifest, options.run, roles)
