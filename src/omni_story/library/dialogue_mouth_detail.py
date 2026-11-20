"""Locate faces independently and review temporal mouth crops when whole frames leave speech unverified."""

import math
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

from omni_story.library.state import ProviderRefused, ReplyUnusable, write_json


def sampled_frames(path, step=3):
    w, h = 640, 360
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"select=not(mod(n\\,{step})),scale={w}:{h}",
            *(["-vsync", "0"] if step != 1 else []),
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)


def major_picture_changes(frames):
    """Find large full-picture changes for review, including cuts inside a source excerpt."""
    reduced = frames[:, ::5, ::5].astype(np.int16)
    difference = np.abs(reduced[1:] - reduced[:-1]).mean(axis=(1, 2, 3))
    return (np.flatnonzero(difference >= 40) + 1).tolist()


def crop_sheet(key, frames, rois, out):
    # Keep a full-frame context and a chronological array of enlarged crops for each actor.
    cols = 12
    w = 92
    h = 61
    rows = math.ceil(len(frames) / cols)
    width = w * cols
    sheet = Image.new(
        "RGB", (width, 224 + len(rois) * (26 + rows * (h + 18))), "#252525"
    )
    draw = ImageDraw.Draw(sheet)
    for j, i in enumerate([len(frames) // 6, len(frames) // 2, 5 * len(frames) // 6]):
        sheet.paste(Image.fromarray(frames[i]).resize((340, 191)), (j * 362, 0))
        draw.text((j * 362 + 2, 195), key + " F" + str(i * 3), fill="white")
    for r, roi in enumerate(rois):
        yy = 222 + r * (26 + rows * (h + 18))
        draw.text(
            (5, yy),
            roi["actor"] + "   chronological mouth detail    every 3 source frames",
            fill="white",
        )
        for j, frame in enumerate(frames):
            loc = [len(frames) // 6, len(frames) // 2, 5 * len(frames) // 6]
            x = np.interp(j, loc, [a[0] for a in roi["centers"]]) * 640
            y = np.interp(j, loc, [a[1] for a in roi["centers"]]) * 360
            # Keep enough context to see if the mouth is covered, or the output is centred on a hand.
            left = max(0, min(640 - 128, round(x) - 64))
            top = max(0, min(360 - 92, round(y) - 46))
            pic = Image.fromarray(frame[top : top + 92, left : left + 128]).resize(
                (w, h)
            )
            a = (j % cols) * w
            b = yy + 23 + (j // cols) * (h + 18)
            sheet.paste(pic, (a, b))
            draw.text((a + 1, b + h + 1), "F" + str(3 * j), fill="white")
    sheet.save(out, quality=96)


def witnessed_slots(case, out, key, frames, people, claims, ask):
    from PIL import ImageDraw

    validated = []
    for index, s in enumerate(claims):
        roi = next(p for p in people if p["actor"] == s["actor"])
        points = s["witness"]
        refs = [len(frames) // 6, len(frames) // 2, 5 * len(frames) // 6]
        # Keep the same context as the candidate crop. A tighter second crop can cut off the mouth.
        cols = 3
        sheet = Image.new(
            "RGB", (cols * 342, math.ceil(len(points) / cols) * 345), "#222"
        )
        dr = ImageDraw.Draw(sheet)
        for i, n in enumerate(points):
            j = n // 3
            x = round(np.interp(j, refs, [c[0] for c in roi["centers"]]) * 640)
            y = round(np.interp(j, refs, [c[1] for c in roi["centers"]]) * 360)
            x = max(0, min(480, x - 80))
            y = max(0, min(240, y - 60))
            im = Image.fromarray(frames[j])
            left = i % cols * 342
            top = i // cols * 345
            sheet.paste(im.resize((320, 180)), (left, top))
            crop = im.crop((x, y, x + 160, y + 120)).resize((184, 138))
            sheet.paste(crop, (left, top + 184))
            dr.text((left + 3, top + 325), "F" + str(n), fill="white")
        path = out / (key + "_witness_" + str(index) + ".jpg")
        sheet.save(path, quality=95)
        q = (
            f"每个F有同帧整幅画面及头部放大图，用上方整图确认角色和手持物。只看目标演员{s['actor']}，按阅读顺序标嘴形。open表示清楚张开的嘴，closed表示清楚收窄或闭合，"
            "blocked表示手、杯、食物或被切掉，unseen表示没有认出嘴。固定的张口笑脸每格都填open。"
            '持有物件且远离嘴时仍可辨嘴。只有实际吃喝造成该嘴形运动才填eating为true，手中有物本身不算。不要把杯口或眼睛认成嘴。返回{"poses":["open或closed或blocked或unseen",...],"eating":true或false,"reason":"实际看到了什么"}。'
            + "每个F必须返回一种，共"
            + str(len(points))
            + "格。"
        )

        def valid(v, points=points):
            if (
                len(v["poses"]) != len(points)
                or any(
                    p not in ("open", "closed", "blocked", "unseen") for p in v["poses"]
                )
                or type(v["eating"]) is not bool
                or not v["reason"]
            ):
                raise ValueError("逐格标注并说明是否在吃喝")
            return v

        v = ask(case, out, "mouth_pose_" + key + "_" + str(index), q, path, valid)
        s["poses"] = v
        if v["eating"]:
            continue
        poses = v["poses"]
        # A single opening, or three identical smiles, is not a speaking cycle. A long static gap stays unverified.
        spans = []
        for j in range(len(points) - 2):
            a, b, c = poses[j : j + 3]
            if (
                a in ("open", "closed")
                and b in ("open", "closed")
                and a == c
                and a != b
                and points[j + 1] - points[j] <= case["fps"] / 2
                and points[j + 2] - points[j + 1] <= case["fps"] / 2
            ):
                lo, hi = points[j], points[j + 2] + 1
                if spans and spans[-1][1] >= lo:
                    spans[-1][1] = max(spans[-1][1], hi)
                else:
                    spans.append([lo, hi])
        validated.extend(dict(s, start=a, end=b) for a, b in spans)
    return validated


def grid_locations(case, bundle, out, keys, ask):
    cols, rows = 8, 6

    def batch(subset):
        canvas = Image.new("RGB", (960, 300 * len(subset)), "#222")
        dr = ImageDraw.Draw(canvas)
        for i, key in enumerate(subset):
            fs = sampled_frames(bundle / case["clips"][key]["path"], step=3)
            im = Image.fromarray(fs[len(fs) // 2]).resize((480, 270))
            y = i * 300
            canvas.paste(im, (0, y))
            canvas.paste(im, (480, y))
            for row in range(rows):
                for col in range(cols):
                    x = 480 + col * 60
                    top = y + row * 45
                    dr.rectangle((x, top, x + 59, top + 44), outline="#b79857")
                    cell = chr(ord("A") + col) + str(row + 1)
                    dr.rectangle((x + 1, top + 1, x + 16, top + 11), fill="#242424")
                    dr.text((x + 1, top), cell, fill="white")
            dr.text((8, y + 274), key, fill="white")
        path = out / ("grid_" + subset[0] + ".jpg")
        canvas.save(path, quality=97)
        prompt = (
            "同一行左边是动画原图，右边是同一帧上的8列6行网格，每格左上写有编号。"
            "识别演员，给每个能看见嘴的演员，抄下右图中嘴正中所在的格号，如D3。请按图上格号选，不估归一化坐标。"
            "跨格时选最接近嘴中心的一格。一个素材一行，背对看不见嘴可跳过角色。"
            '返回{"clips":[{"clip":"素材编号","people":[{"actor":"角色编号","cell":"格号"}]}]}。'
            + "行编号"
            + str(subset)
            + "；演员"
            + str(case["actors"])
        )

        def valid(v):
            if [a["clip"] for a in v["clips"]] != subset:
                raise ValueError("按图顺序给每行")
            for a in v["clips"]:
                for actor in a["people"]:
                    cell = actor["cell"]
                    if (
                        actor["actor"] not in case["actors"]
                        or len(cell) != 2
                        or cell[0] not in "ABCDEFGH"
                        or cell[1] not in "123456"
                    ):
                        raise ValueError("嘴所在格号A1至H6和有效人物编号")
            return v

        reply = ask(case, out, "mouth_grid_" + subset[0], prompt, path, valid)
        result = {}
        for item in reply["clips"]:
            people = []
            for p in item["people"]:
                col = ord(p["cell"][0]) - ord("A")
                row = int(p["cell"][1]) - 1
                point = [(col + 0.5) / cols, (row + 0.5) / rows]
                people.append(
                    {"actor": p["actor"], "cell": p["cell"], "centers": [point] * 3}
                )
            result[item["clip"]] = people
        return result

    locations = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for batch_result in pool.map(
            batch, [keys[i : i + 3] for i in range(0, len(keys), 3)]
        ):
            locations.update(batch_result)
    return locations


def recover_slots(case, bundle, out, prior, ask):

    out.mkdir(parents=True, exist_ok=True)
    keys = list(case["clips"])
    checked = {}

    rois = grid_locations(case, bundle, out, keys, ask)
    write_json(out / "locations.json", rois)

    def check_clip(key):
        existing = prior[key]
        people = rois[key]
        if not people:
            return key, dict(existing, crop_review="mouth not located")
        frames = sampled_frames(bundle / case["clips"][key]["path"])
        sheet = out / (key + "_cropped.jpg")
        crop_sheet(key, frames, people, sheet)
        allowed = list(range(0, 3 * len(frames), 3))
        q = (
            "这是同一源素材的三张全景和嘴部放大图。下半部每组用角色编号标明，每三源帧取一格，"
            "要逐格查看同一张嘴，嘴位若定位错、端杯挡嘴、重复嚼食、静止笑脸都不可通过。"
            "当嘴部明显呈现开-合-开或合-开-合，抄下印在三个实际小图下的F编号。"
            "将该组有节奏张合的可用区间首尾也限定在看得到证据的区间。需要上下文判断是否进食。"
            '若整段仅一次从闭合变成张开并保持，无交替，不算。返回{"observed":"逐角色实况","slots":'
            '[{"actor":"角色编号","witness":[源帧整数,源帧整数,源帧整数],"evidence":"每个F画面具体嘴形和遮挡情况"}]}。'
            "按每次开合的转折列出尽量完整的F编号，相邻两个转折最大15帧，不能越过明显持续不动的一段。没有则slots填空。"
            + "可填的F编号"
            + str(allowed)
            + "，角色"
            + str([p["actor"] for p in people])
        )

        def valid(v):
            if not v["observed"] or not isinstance(v["slots"], list):
                raise ValueError("需实际观察")
            for s in v["slots"]:
                pts = s["witness"]
                if (
                    s["actor"] not in [p["actor"] for p in people]
                    or len(pts) < 3
                    or pts != sorted(set(pts))
                    or any(n not in allowed for n in pts)
                    or not s["evidence"]
                ):
                    raise ValueError(
                        "逐个抄写同一演员确有嘴形证据的小图帧号，无法确认填空"
                    )
            return v

        v = ask(case, out, "mouth_crops_" + key, q, sheet, valid)
        additions = witnessed_slots(case, out, key, frames, people, v["slots"], ask)
        return key, dict(
            existing, slots=[*existing["slots"], *additions], crop_review=v
        )

    def available_read(key):
        try:
            return check_clip(key)
        except (ReplyUnusable, ProviderRefused) as error:
            return key, dict(
                prior[key], crop_review={"status": "unverified", "reason": str(error)}
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        for key, result in pool.map(available_read, keys):
            checked[key] = result
            write_json(out / "checked.partial.json", checked)
    write_json(out / "checked.json", checked)
    return checked
