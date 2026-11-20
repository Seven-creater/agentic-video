"""Find repeated contour openings in drawn footage. Identify the actor and action on the full shot."""

from concurrent.futures import ThreadPoolExecutor
from itertools import groupby

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .dialogue_mouth_detail import sampled_frames
from .state import ProviderRefused, ReplyUnusable, write_json


def contours(frame):
    """Propose ink features without needing a character name or model-supplied coordinates."""
    ink = frame.max(axis=2) < 110
    labels, _ = ndimage.label(ink, np.ones((3, 3), dtype=bool))
    result = []
    for number, box in enumerate(ndimage.find_objects(labels), 1):
        y, x = box
        w, h = x.stop - x.start, y.stop - y.start
        if not (8 <= w <= 125 and 4 <= h <= 95 and 0.72 <= w / h <= 6.5):
            continue
        part = labels[box] == number
        fill = part.mean()
        if not 0.10 <= fill <= 0.95:
            continue
        holes = ndimage.binary_fill_holes(part) & ~part
        area = int(holes.sum())
        result.append(((x.start + x.stop) / 2, (y.start + y.stop) / 2, w, h, area))
    return result


def associate(active, found):
    distances = []
    for i, track in enumerate(active):
        x, y, w, h, _ = track[-1][1]
        for j, (cx, cy, cw, ch, _) in enumerate(found):
            if max(w, cw) > min(w, cw) * 1.65 or abs(y - cy) > max(5, w * 0.65):
                continue
            dist = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if dist <= max(6, w * 0.80):
                distances.append((dist + abs(w - cw) * 0.33 + abs(h - ch) * 0.1, i, j))
    links = {}
    taken = set()
    for _, i, j in sorted(distances):
        if i not in links and j not in taken:
            links[i] = j
            taken.add(j)
    return links, taken


def cycles(track, fps):
    # A held pose cannot carry unlimited dialogue. Keep only short, complete witnessed cycles.
    states = []
    for opened, group in groupby(track, key=lambda sample: sample[1][4] >= 8):
        run = list(group)
        if len(run) >= 2:
            states.append((opened, run[0][0], run[-1][0]))
    spans = []
    for left, mid, right in zip(states, states[1:], states[2:]):
        if left[0] != right[0] or left[0] == mid[0]:
            continue
        if (
            right[1] - left[2] > fps * 0.70
            or mid[1] - left[2] > 2
            or right[1] - mid[2] > 2
        ):
            continue
        lo = max(left[1], left[2] - max(1, fps // 8))
        hi = min(right[2], right[1] + max(1, fps // 8)) + 1
        if spans and spans[-1][1] >= lo:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    return spans


def candidates(frames, fps):
    active = []
    finished = []
    for n, frame in enumerate(frames):
        found = contours(frame)
        links, taken = associate(active, found)
        next_tracks = []
        for i, track in enumerate(active):
            if i in links:
                track.append((n, found[links[i]]))
                next_tracks.append(track)
            elif len(track) >= 6:
                finished.append(track)
        next_tracks.extend([[(n, f)] for j, f in enumerate(found) if j not in taken])
        active = next_tracks
    result = []
    for track in finished + active:
        for start, end in cycles(track, fps):
            points = {n: f for n, f in track if start <= n < end}
            result.append((start, end, points))
    return sorted(result, key=lambda row: (row[0], row[2][row[0]][0]))


def face_support(frame, feature):
    # Look for two similarly sized, roughly level ink features above and on either side.
    # With a profile, heavy occlusion or indistinct eyes the result is intentionally unknown.
    x, y, width, _, _ = feature
    labels, _ = ndimage.label(frame.max(axis=2) < 110, np.ones((3, 3), bool))
    sides = [[], []]
    for yy, xx in ndimage.find_objects(labels):
        cx = (xx.start + xx.stop) / 2 - x
        cy = y - (yy.start + yy.stop) / 2
        w, h = xx.stop - xx.start, yy.stop - yy.start
        if (
            0.48 * width < abs(cx) < 2.65 * width
            and 0.24 * width < cy < 1.85 * width
            and 0.2 * width < w < 1.7 * width
            and 0.07 * width < h < 1.5 * width
        ):
            sides[int(cx > 0)].append((abs(cx), cy, w, h))
    return any(
        abs(a[0] - b[0]) < width * 0.48
        and abs(a[1] - b[1]) < width * 0.30
        and min(a[2], b[2]) >= 0.64 * max(a[2], b[2])
        and min(a[3], b[3]) >= 0.48 * max(a[3], b[3])
        for a in sides[0]
        for b in sides[1]
    )


def witness_frames(points):
    runs = [list(g) for _, g in groupby(points, key=lambda n: points[n][4] >= 8)]
    frames = [g[len(g) // 2] for g in runs if len(g) >= 2]
    if len(frames) > 5:
        frames = [frames[j] for j in np.linspace(0, len(frames) - 1, 5, dtype=int)]
    return frames


def sheet(frames, points, dest):
    # One candidate per image. Keep ears, forehead and both hands in the actor view.
    image = Image.new("RGB", (960, 580), "#182127")
    ns = witness_frames(points)
    middle = ns[len(ns) // 2]
    x, y, w, h, _ = points[middle]
    im = Image.fromarray(frames[middle])
    full = im.copy()
    ImageDraw.Draw(full).rectangle(
        (x - w / 2 - 3, y - h / 2 - 3, x + w / 2 + 3, y + h / 2 + 3),
        outline="#00beff",
        width=2,
    )
    image.paste(full.resize((480, 270)), (0, 0))
    half = max(60, min(185, w * 4.8))
    face = im.crop(
        (
            round(x - half),
            round(y - 1.5 * half),
            round(x + half),
            round(y + 0.58 * half),
        )
    )
    image.paste(face.resize((300, 300)), (555, 0))
    draw = ImageDraw.Draw(image)
    draw.text((10, 275), "Whole shot / blue box = tracked feature", fill="white")
    draw.text((555, 303), "Actor around the tracked mouth", fill="white")
    for i, n in enumerate(ns):
        x, y, w, h, _ = points[n]
        half = max(17, min(70, 1.35 * w))
        crop = Image.fromarray(frames[n]).crop(
            (round(x - half), round(y - half), round(x + half), round(y + half))
        )
        col = i * (960 // len(ns))
        size = min(188, 960 // len(ns) - 4)
        image.paste(crop.resize((size, size)), (col, 359))
        draw.text((col + 4, 332), f"F{n}", fill="white")
    image.save(dest, quality=94)


def check_contact(case, out, key, first, frames, points, ask):
    ns = witness_frames(points)
    panel = Image.new("RGB", (225 * len(ns), 240), "#20272b")
    draw = ImageDraw.Draw(panel)
    for i, n in enumerate(ns):
        x, y, w, h, _ = points[n]
        half = max(19, round(1.05 * w))
        crop = Image.fromarray(frames[n]).crop(
            (round(x - half), round(y - half), round(x + half), round(y + half))
        )
        panel.paste(crop.resize((218, 218)), (i * 225, 20))
        draw.text((i * 225 + 2, 2), f"F{n}", fill="white")
    dest = out / f"{key}_contact_{first}.jpg"
    panel.save(dest, quality=96)
    prompt = (
        "这是一张局部实帧图。每格正中心是同一处轮廓。逐格看，中心是什么部位？有没有外物实际进入或盖住嘴？"
        "口内的粉红舌头和唇线是身体的一部分。请指出物体颜色及发生接触的F编号；"
        "若看到完整唇线和空隙，可填clear；不能确定是什么填unknown。"
        '返回 {"feature":"mouth或other或unknown","frames":[{"f":整数,"contact":"food/cup/other/clear/unknown"}],"why":"看得到的接触或间隙"}。'
    )

    def valid(v):
        if (
            v["feature"] not in ("mouth", "other", "unknown")
            or [a["f"] for a in v["frames"]] != ns
            or any(
                a["contact"] not in ("food", "cup", "other", "clear", "unknown")
                for a in v["frames"]
            )
            or not v["why"]
        ):
            raise ValueError("按图片帧号逐一填写中心部位和接触情况 " + str(ns))
        return v

    return ask(case, out, f"contact_{key}_{first}", prompt, dest, valid)


def observe(case, bundle, out, ask):
    out.mkdir(parents=True, exist_ok=True)

    def scan(key):
        frames = sampled_frames(bundle / case["clips"][key]["path"], step=1)
        if len(frames) != case["clips"][key]["frames"]:
            raise ValueError("Decoded frame count differs from selected source " + key)
        rows = candidates(frames, case["fps"])
        slots = []
        observations = []
        for first, (a, b, points) in enumerate(rows):
            ns = witness_frames(points)
            if sum(face_support(frames[n], points[n]) for n in ns) * 2 <= len(ns):
                observations.append(
                    dict(
                        start=a,
                        end=b,
                        action="unverified",
                        why="缺少可定位嘴的双侧五官",
                    )
                )
                continue
            path = out / f"{key}_face_{first}.jpg"
            sheet(frames, points, path)
            q = (
                "图中只有一个跟踪目标。左上蓝框是位置；右上为以蓝框为中心的角色大头照，先按头顶、耳朵、颜色认人。"
                "下方依次是这处的真实帧。请写目标角色头顶是什么形状和颜色、嘴边的手上有没有可辨认的食物或杯子。"
                "食物实际挨嘴或进入嘴才算eating，杯子遮挡算drinking。伸手、比划、举牌单独不能作为吃喝的证据。"
                "轮廓属于眼睛、字幕或物件填feature；看不清填unknown。"
                "嘴形反复变化且看清未被挡，填talking。"
                '返回 {"actor":"角色编号或none","action":"talking/eating/drinking/hidden/feature/unknown","why":"头顶特征和手嘴动作"}。'
                + "可用演员和外观 "
                + str(case["actors"])
            )

            def valid(r):
                if (
                    r["action"]
                    not in (
                        "talking",
                        "eating",
                        "drinking",
                        "hidden",
                        "feature",
                        "unknown",
                    )
                    or r["actor"] not in [*case["actors"], "none"]
                    or not r["why"]
                    or (r["action"] == "talking" and r["actor"] == "none")
                ):
                    raise ValueError("补全角色、动作和真正看到的依据")
                return r

            try:
                answer = ask(case, out, f"face_{key}_{first}", q, path, valid)
            except (ReplyUnusable, ProviderRefused) as exc:
                observations.append(
                    dict(start=a, end=b, action="unverified", why=str(exc))
                )
                continue
            if answer["actor"] != "none" and answer["action"] in (
                "drinking",
                "eating",
                "hidden",
                "feature",
            ):
                try:
                    contact = check_contact(case, out, key, first, frames, points, ask)
                except (ReplyUnusable, ProviderRefused) as exc:
                    contact = {"feature": "unknown", "why": str(exc)}
                answer = dict(answer, context_action=answer["action"], contact=contact)
                # Override a supposed prop only if every observed state has clear mouth contours
                # and the wider shot did identify this actor. Uncertain checks never release a slot.
                if (
                    answer["actor"] != "none"
                    and contact["feature"] == "mouth"
                    and all(s["contact"] == "clear" for s in contact["frames"])
                ):
                    answer["action"] = "talking"
                    answer["why"] = contact["why"]
            observations.append(dict(answer, start=a, end=b))
            if answer["action"] == "talking":
                slots.append(
                    dict(
                        actor=answer["actor"],
                        start=a,
                        end=b,
                        evidence=answer["why"],
                        track_frames=list(points),
                        center=[[v[0] / 640, v[1] / 360] for v in points.values()],
                        widths=[v[2] / 640 for v in points.values()],
                    )
                )
        return key, {
            "observed": "按原画轮廓逐帧追踪，再看实帧分辨角色和动作",
            "slots": slots,
            "source_sha256": case["clips"][key]["sha256"],
            "method": "contour_cycle",
            "features": observations,
        }

    checked = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for key, result in pool.map(scan, case["clips"]):
            checked[key] = result
            write_json(out / "checked.partial.json", checked)
    write_json(out / "checked.json", checked)
    return checked


def mouth_region(point, width, crop):
    """Return the mouth's comparison region if it fits in the crop."""
    x, y = point
    left, top, right, bottom = crop
    dx = max(6 / 640, width * 0.60)
    dy = dx * 640 / 360
    if not (left <= x - dx < x + dx <= right and top <= y - dy < y + dy <= bottom):
        return None
    return (x - dx, y - dy, x + dx, y + dy)


def matching_pose(source, picture, point, width, crop):
    """Verify that the pixels around the tracked mouth survived the final crop and subtitles."""
    box = mouth_region(point, width, crop)
    if box is None:
        return None
    left, top, right, bottom = crop
    output_box = (
        (box[0] - left) / (right - left),
        (box[1] - top) / (bottom - top),
        (box[2] - left) / (right - left),
        (box[3] - top) / (bottom - top),
    )

    def patch(frame, normalized):
        # Resample both pictures onto the same local coordinates before comparing ink.
        box = tuple(v * dim for v, dim in zip(normalized, (640, 360, 640, 360)))
        return np.array(
            Image.fromarray(frame).resize((48, 48), Image.Resampling.BILINEAR, box)
        )

    original = patch(source, box)
    rendered = patch(picture, output_box)
    ink = original.max(axis=2) < 110
    output_ink = rendered.max(axis=2) < 110
    union = (ink | output_ink).sum()
    if not union or (ink & output_ink).sum() / union < 0.72:
        return None
    # Read the final image. Only the component nearest the tracked centre can supply the mouth opening.
    labels, _ = ndimage.label(output_ink, np.ones((3, 3), bool))
    parts = []
    for i, region in enumerate(ndimage.find_objects(labels), 1):
        yy, xx = region
        dist = ((xx.start + xx.stop) / 2 - 24) ** 2 + (
            (yy.start + yy.stop) / 2 - 24
        ) ** 2
        if (
            dist < 9**2
            and 0 < xx.start
            and xx.stop < 48
            and 0 < yy.start
            and yy.stop < 48
        ):
            part = labels[region] == i
            # Map hole size in the output patch back to the 640px measurement scale.
            area = (ndimage.binary_fill_holes(part) & ~part).sum() * (
                (box[2] - box[0]) * 640 / 48
            ) ** 2
            parts.append((dist, area))
    return float(min(parts)[1]) if parts else None


def rendered_cycles(case, bundle, film, cuts, observed):
    tracks = rendered_mouths_by_actor(case, bundle, film, cuts, observed)
    return {
        line["id"]: tracks[line["id"]].get(line["actor"], []) for line in case["lines"]
    }


def rendered_mouths_by_actor(case, bundle, film, cuts, observed):
    """Return independently witnessed mouth cycles for every actor in the rendered shot."""
    output = sampled_frames(film, step=1)
    if len(output) != case["total"]:
        raise ValueError("Incomplete rendered video")
    seen = {line["id"]: {} for line in case["lines"]}
    for key, clip in case["clips"].items():
        if not observed[key]["slots"]:
            continue
        source = sampled_frames(bundle / clip["path"], step=1)
        for i, cut in enumerate(cuts):
            if cut["clip"] != key:
                continue
            end = cuts[i + 1]["at"] if i + 1 < len(cuts) else case["total"]
            for line in case["lines"]:
                for slot in observed[key]["slots"]:
                    start = max(
                        line["at"], cut["at"], cut["at"] + slot["start"] - cut["in"]
                    )
                    stop = min(line["end"], end, cut["at"] + slot["end"] - cut["in"])
                    track = []
                    for n in range(start, stop):
                        f = cut["in"] + n - cut["at"]
                        j = f - slot["start"]
                        area = matching_pose(
                            source[f],
                            output[n],
                            slot["center"][j],
                            slot["widths"][j],
                            cut.get("crop", [0, 0, 1, 1]),
                        )
                        if area is None:
                            track.append(None)
                        else:
                            track.append((n, (0, 0, 0, 0, area)))
                    for valid, group in groupby(track, key=lambda v: v is not None):
                        if valid:
                            for a, b in cycles(list(group), case["fps"]):
                                seen[line["id"]].setdefault(
                                    slot["actor"], set()
                                ).update(range(a, b))
    return {
        lid: {actor: sorted(frames) for actor, frames in actors.items()}
        for lid, actors in seen.items()
    }
