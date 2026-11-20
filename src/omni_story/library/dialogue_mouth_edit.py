"""Experimental active-speaker edit of locked footage and dubbing, using a prepared media bundle."""

import argparse
import json
import math
import os
import re
from pathlib import Path

from omni_story.library.media import sha256_file
from omni_story.library.opencode_provider import OpenCodeMCP
from omni_story.library.pipeline import _material_call
from omni_story.library.server_cli import _home, credential, settings
from omni_story.library.state import LibraryState, json_sha, write_json


def read(path):
    return json.loads(path.read_text())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ask(case, folder, name, prompt, media, validate):
    name = name.lower()
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name):
        raise ValueError("invalid scene stage name")
    cfg = read(Path(case["configuration_path"]))["input_lock"]["configuration"]
    home = _home()
    server = settings(home)
    os.environ["Z_AI_API_KEY"] = credential(home)
    lock = {
        "configuration": cfg,
        "stage": name,
        "media_sha256": sha256_file(media),
        "prompt": prompt,
    }
    job = folder / "requests" / (name + "_" + json_sha(lock)[:16])
    job.mkdir(parents=True, exist_ok=True)
    state = LibraryState(
        job, lock, max_requests=None, registry_path=job.parent / "registry.json"
    )
    provider = OpenCodeMCP(
        state,
        package_root=server["mcp_package_root"],
        executable=server["opencode_executable"],
    )
    return _material_call(
        state,
        provider,
        "material_scene_" + name,
        prompt,
        validate,
        str(media),
        image=media.suffix == ".jpg",
    )


def checked_span(start, end, maximum):
    return type(start) is int and type(end) is int and 0 <= start < end <= maximum


def inventory(case, bundle, out):
    records = {}
    for index, group in enumerate(case["groups"]):
        lengths = {k: case["clips"][k]["frames"] for k in group["clips"]}

        def validate(reply, lengths=lengths):
            items = reply["clips"]
            if {s["clip"] for s in items} != set(lengths) or len(items) != len(lengths):
                raise ValueError("每个素材要单独记录一次 " + encoded(lengths))
            for item in items:
                if not isinstance(item["slots"], list) or not item.get("observed"):
                    raise ValueError("实际观察和slots数组必填，无嘴动填空数组")
                for slot in item["slots"]:
                    if (
                        slot["actor"] not in case["actors"]
                        or not checked_span(
                            slot["start"], slot["end"], lengths[item["clip"]]
                        )
                        or not slot.get("evidence")
                    ):
                        raise ValueError(
                            "复看角色、源帧区间和嘴形证据 " + encoded(item)
                        )
            return reply

        prompt = (
            "静音逐段观察动画原画。素材标签旁是从零开始的源帧号。按素材找能给一个人物配上当面对话的连续开合嘴区间。"
            "逐个角色盯住相对鼻眼的位置，区分嘴形变化与转头、进食、眨眼、走动、固定笑脸或持续张嘴。"
            "看不清或只能看到一个嘴形时将slots留空。请在observed写出原因、同框情况以及哪些角色可见。"
            "只有确有连续发音式张合才填slots；写哪两种嘴形发生交替，首尾具体帧。源出点不含末帧。"
            '返回 {"clips":[{"clip":"素材编号","observed":"实际看见","slots":[{"actor":"编号","start":0,"end":1,"evidence":"嘴形如何变"}]}]}。'
            + "角色外观"
            + encoded(case["actors"])
            + "。各素材帧数"
            + encoded(lengths)
        )
        answer = ask(
            case,
            out,
            "mouth_source_" + str(index),
            prompt,
            bundle / group["media"],
            validate,
        )
        for item in answer["clips"]:
            records[item["clip"]] = item
        write_json(out / "source_read.partial.json", records)
    return records


def weighted_cuts(groups, case):
    if len(groups) != len(case["blocks"]):
        raise ValueError("每个区块要有一组镜头")
    cuts = []
    for group, block in zip(groups, case["blocks"]):
        rows = group["cuts"]
        if not rows or any(c["clip"] not in block["clips"] for c in rows):
            raise ValueError("区块素材不匹配 " + encoded(block))
        weights = [r["weight"] for r in rows]
        if any(
            not isinstance(w, (float, int)) or not math.isfinite(w) or w <= 0
            for w in weights
        ):
            raise ValueError("时长权重必须是正数")
        for row in rows:
            if not checked_span(
                row["in"], row["in"] + 1, case["clips"][row["clip"]]["frames"]
            ):
                raise ValueError("源入点越界 " + encoded(row))
        caps = [case["clips"][r["clip"]]["frames"] - r["in"] for r in rows]
        n = block["end"] - block["at"]
        if len(rows) > n or sum(caps) < n:
            raise ValueError(
                "本区块可播放帧数不足，需增加镜头或调整入点 " + encoded(block)
            )
        count = [1] * len(rows)
        for _ in range(n - len(rows)):
            j = max(
                (i for i in range(len(rows)) if count[i] < caps[i]),
                key=lambda i: weights[i] / (count[i] + 1),
            )
            count[j] += 1
        at = block["at"]
        for row, frames in zip(rows, count):
            cuts.append(dict(row, at=at))
            at += frames
    return cuts


def validate_cuts(cuts, case):
    if not cuts or cuts[0]["at"] != 0:
        raise ValueError("第一刀从成片第0帧开始")
    for i, row in enumerate(cuts):
        end = cuts[i + 1]["at"] if i + 1 < len(cuts) else case["total"]
        key = row["clip"]
        at = row["at"]
        start = row["in"]
        if key not in case["clips"] or not checked_span(at, end, case["total"]):
            raise ValueError("素材编号或成片入点错误 " + encoded(row))
        if not checked_span(start, start + end - at, case["clips"][key]["frames"]):
            raise ValueError(
                f"第{i + 1}刀需要{end - at}帧，{key}从{start}帧起实际只剩{case['clips'][key]['frames'] - start}帧。用其他切点或源入点，不能定格补足。"
            )
        box = row.get("crop", [0, 0, 1, 1])
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in box)
            or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
            or min(box[2] - box[0], box[3] - box[1]) < 0.1
            or abs((box[2] - box[0]) - (box[3] - box[1])) > 0.012
        ):
            raise ValueError(
                "crop用原画归一化边界[左,上,右,下]，宽度与高度的归一化跨度相同，以保持画幅"
            )
    return cuts


def scene_blocks(case):
    blocks = case.get("blocks")
    if (
        blocks is None
        and len({clip.get("group") for clip in case["clips"].values()}) == 1
    ):
        return [{"at": 0, "end": case["total"], "clips": list(case["clips"])}]
    if not blocks:
        raise ValueError("多场对白缺少配音对应的场界，请由已锁工程重新准备素材包")
    cursor = 0
    for b in blocks:
        if (
            b["at"] != cursor
            or not checked_span(cursor, b["end"], case["total"])
            or not b["clips"]
            or not set(b["clips"]) <= case["clips"].keys()
        ):
            raise ValueError("参考场界必须按序覆盖整条配音并指向已选素材")
        cursor = b["end"]
    if cursor != case["total"]:
        raise ValueError("参考场界未覆盖片尾")
    return blocks


def validate_scenes(cuts, case):
    for i, cut in enumerate(cuts):
        end = cuts[i + 1]["at"] if i + 1 < len(cuts) else case["total"]
        for block in scene_blocks(case):
            if (
                cut["at"] < block["end"]
                and end > block["at"]
                and cut["clip"] not in block["clips"]
            ):
                raise ValueError(
                    f"第{i + 1}刀跨到别场，第{block['at']}至{block['end']}帧只可用 {block['clips']}"
                )


def public_slots(observed):
    return {
        key: [
            {k: slot[k] for k in ("actor", "start", "end", "evidence")}
            for slot in item["slots"]
        ]
        for key, item in observed.items()
    }


def render_shot(case, source, clip, start, length, crop, dest):
    from omni_story.library.render import mosaic_layers
    from omni_story.library.selected_edit import ff

    w = case["width"]
    h = case["height"]
    cw = round((crop[2] - crop[0]) * w / 2) * 2
    ch = round((crop[3] - crop[1]) * h / 2) * 2
    x = min(w - cw, round(crop[0] * w / 2) * 2)
    y = min(h - ch, round(crop[1] * h / 2) * 2)
    graph = [
        f"[0:v]trim=start_frame={start}:end_frame={start + length},setpts=PTS-STARTPTS[mosaic0]"
    ]
    masks = clip["masks"]
    graph.extend(mosaic_layers(masks, w, h, start / case["fps"]))
    graph.append(
        f"[mosaic{len(masks)}]crop={cw}:{ch}:{x}:{y},scale={w}:{h},setsar=1[video]"
    )
    ff(
        "-i",
        source,
        "-filter_complex_threads",
        1,
        "-filter_complex",
        ";".join(graph),
        "-map",
        "[video]",
        "-an",
        "-frames:v",
        length,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-threads",
        2,
        "-crf",
        17,
        dest,
    )


def render(case, bundle, out, cuts, name):
    import subprocess

    from omni_story.library.selected_edit import concat, ff

    paths = []
    for i, row in enumerate(cuts):
        clip = case["clips"][row["clip"]]
        end = cuts[i + 1]["at"] if i + 1 < len(cuts) else case["total"]
        dest = out / (f"{name}_{i:03}.mp4")
        render_shot(
            case,
            bundle / clip["path"],
            clip,
            row["in"],
            end - row["at"],
            row.get("crop", [0, 0, 1, 1]),
            dest,
        )
        paths.append(dest)
    movie = concat(out, paths, name + "_picture")
    dest = out / (name + ".mp4")
    ff(
        "-i",
        movie,
        "-i",
        bundle / case["dub"],
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-vf",
        "ass='" + str(bundle / case["subs"]) + "'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-threads",
        2,
        "-crf",
        18,
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        dest,
    )
    info = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", dest]
        )
    )["streams"]
    if (
        int(next(s for s in info if s["codec_type"] == "video")["nb_frames"])
        != case["total"]
    ):
        raise ValueError("导出视频帧数错误")

    def audio_hash(path):
        return (
            subprocess.check_output(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    path,
                    "-map",
                    "0:a:0",
                    "-c",
                    "copy",
                    "-f",
                    "hash",
                    "-",
                ]
            )
            .decode()
            .strip()
        )

    if audio_hash(dest) != audio_hash(bundle / case["dub"]):
        raise ValueError("导出的配音已改变")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", dest, "-f", "null", "-"], check=True
    )
    write_json(
        out / (name + "_render.json"),
        {
            "video_sha256": sha256_file(dest),
            "audio": audio_hash(dest),
            "frames": case["total"],
            "cuts": cuts,
        },
    )
    return dest


def public_clock(case):
    return [{k: v for k, v in l.items() if k != "voice_frames"} for l in case["lines"]]


def run_edit(case, bundle, out, arm, evidence_path=None):
    for clip in case["clips"].values():
        if sha256_file(bundle / clip["path"]) != clip["sha256"]:
            raise ValueError("锁定的源文件已改变")
    source_read = None
    story_sources = None
    if arm == "speaker":
        from .dialogue_scene_review import (
            available_footage,
            inspect_sources,
            source_brief,
        )

        evidence_path = evidence_path or out.parent / "recover" / "checked.json"
        source_read = load_verified_sources(case, evidence_path)
        write_json(
            out / "source_evidence_receipt.json",
            {"path": str(evidence_path), "sha256": sha256_file(evidence_path)},
        )
        context_path = out / "scene_sources.json"
        source_context = read(context_path) if context_path.is_file() else {}
        if set(source_context) != set(case["clips"]) or any(
            row.get("source_sha256") != case["clips"][key]["sha256"]
            for key, row in source_context.items()
        ):
            source_context = inspect_sources(case, bundle, out, ask)
        story_sources = source_brief(case, source_read, source_context)
        write_json(
            out / "line_footage.json",
            available_footage(case, scene_blocks(case), story_sources),
        )
    prompt = (
        "看片包包含参考片REF、固定配音DUB及全部M编号素材。按参考的搭档关系剪出连贯的对白，"
        "谁开口尽量给谁单人近景，看清其嘴确实在说。交接处说完再切，停顿可以留听者反应。自行决定镜头。"
        "按固定录音和节目总长剪整条。以下时钟均为帧，出点不含末帧。素材不足仍交能做出的最好试剪，"
        "把无法满足的句号和原因写在gaps数组。实际只支持原速直切和固定裁框，不能拉长、定格或变速。"
        "每刀clip用素材编号、in用源起帧、crop可省略，给出时用归一化[左,上,右,下]，宽高跨度相同。"
        + "每秒帧数"
        + str(case["fps"])
        + "，总帧数"
        + str(case["total"])
        + "。"
        + case["context"]
        + "。演员"
        + encoded(case["actors"])
        + "。台词"
        + encoded(public_clock(case))
        + "。源长度"
        + encoded({k: v["frames"] for k, v in case["clips"].items()})
    )
    if arm == "weighted":
        prompt += (
            '只在各区块内选镜，每刀填相对时长weight，程序按权重及源可用时长分配各刀实际帧数。返回{"blocks":[{"cuts":[{"clip":"M编号","in":0,"weight":1}]}],"gaps":[]}。区块'
            + encoded(case["blocks"])
        )
    else:
        prompt += '直接定每刀的绝对成片起帧at，从0开始。相邻at的差不能超过源素材剩余帧数。返回{"cuts":[{"at":0,"clip":"M编号","in":0}],"gaps":[]}。'
    if story_sources is not None:
        prompt += (
            "每刀所用素材须对应准用范围 "
            + encoded(scene_blocks(case))
            + "。先安排一场戏的起因、对方接话和最后的结果。台词指向物件或动作时，把实际动作留到听见这句话的时候，"
            "话说完再交给对方的反应。同一个人连续说几句，可以让镜头一直演下去。动作应有推进，禁止从同一源区间反复重播。"
            "同一句发声时，画面只可有这句对应演员的嘴在明显开合。另一人同框可以听，吃东西或嘴动须裁掉或换镜。"
            "开口看说者，嘴看不清时仍优先能认出本人、无冲突动作的画面。脸、动作和嘴动证据按源帧号如下。"
            "faces是采样图点位，空表示未认出；mouth为核实开合；blocked为已知进食、喝水或遮住。"
            + encoded(story_sources)
        )

    def validate(row):
        cuts = weighted_cuts(row["blocks"], case) if arm == "weighted" else row["cuts"]
        validate_cuts(cuts, case)
        if arm == "speaker":
            validate_scenes(cuts, case)
        return row

    plan = ask(case, out, arm + "_edit", prompt, bundle / case["media"], validate)
    cuts = weighted_cuts(plan["blocks"], case) if arm == "weighted" else plan["cuts"]
    write_json(out / "decision.json", plan)
    film = render(case, bundle, out, cuts, "draft")
    if story_sources is not None:
        from .dialogue_scene_review import finish_scene

        finish_scene(case, bundle, out, cuts, film, source_read, story_sources, ask)
    else:
        audit_and_repair(case, bundle, out, cuts, film, source_read)


def frame_strip(path, first, end, dest):
    """A still grid preserves visible source frame numbers through provider video sampling."""
    import subprocess

    from PIL import Image, ImageDraw

    count = min(20, end - first)
    numbers = sorted(
        {first + round(i * (end - first - 1) / max(1, count - 1)) for i in range(count)}
    )
    expr = "+".join("eq(n\\," + str(n) + ")" for n in numbers)
    w, h = 336, 189
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"select={expr},scale={w}:{h}",
            "-vsync",
            "0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-threads",
            "1",
            "pipe:1",
        ]
    )
    if len(raw) != len(numbers) * w * h * 3:
        raise ValueError("解码的取样帧数不符")
    cols = 4
    grid = Image.new(
        "RGB", (w * cols, (h + 17) * math.ceil(len(numbers) / cols)), "#252525"
    )
    draw = ImageDraw.Draw(grid)
    for i, n in enumerate(numbers):
        size = w * h * 3
        x = i % cols * w
        y = i // cols * (h + 17)
        grid.paste(
            Image.frombytes("RGB", (w, h), raw[i * size : (i + 1) * size]), (x, y)
        )
        draw.text((x + 3, y + h + 1), str(n), fill="white")
    grid.save(dest, quality=93)
    return numbers


def proof_reel(source, sheet, dest):
    from omni_story.library.selected_edit import ff

    # Slow continuous footage plus a labelled contact sheet avoids relying on provider sampling cadence.
    graph = (
        "[0:v]setpts=2*PTS,scale=960:540,pad=960:720:0:90,setsar=1,fps=15[a];"
        "[1:v]scale=960:720:force_original_aspect_ratio=decrease,pad=960:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=15[b];"
        "[a][b]concat=n=2:v=1:a=0[video]"
    )
    ff(
        "-i",
        source,
        "-loop",
        1,
        "-t",
        3,
        "-i",
        sheet,
        "-filter_complex_threads",
        1,
        "-filter_complex",
        graph,
        "-map",
        "[video]",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-threads",
        2,
        dest,
    )


def closeup(path, slot, case, dest):
    import subprocess

    import numpy as np
    from PIL import Image

    w, h = 640, 360
    start, end = slot["start"], slot["end"]
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"trim=start_frame={start}:end_frame={end},scale={w}:{h}",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    pts = slot["witness"]
    centers = slot["center"]
    s = w * h * 3
    if len(raw) != (end - start) * s:
        raise ValueError("嘴部核验源帧不足")
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-s",
            "960x360",
            "-r",
            str(case["fps"] / 3),
            "-pix_fmt",
            "rgb24",
            "-i",
            "pipe:0",
            "-threads",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        stdin=subprocess.PIPE,
    )
    with proc:
        for i, n in enumerate(range(start, end)):
            im = Image.frombytes("RGB", (w, h), raw[i * s : (i + 1) * s])
            x = np.interp(n, pts, [q[0] for q in centers]) * w
            y = np.interp(n, pts, [q[1] for q in centers]) * h
            left = round(max(0, min(w - 92, x - 46)))
            top = round(max(0, min(h - 104, y - 52)))
            view = Image.new("RGB", (960, 360))
            view.paste(im, (0, 0))
            view.paste(
                im.crop((left, top, left + 92, top + 104)).resize((320, 360)), (640, 0)
            )
            proc.stdin.write(view.tobytes())
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError("嘴部放大视频生成失败")


def source_frames(case, bundle, out):
    first = read(out.parent / "inventory" / "source_read.json")
    from concurrent.futures import ThreadPoolExecutor

    checked = {}

    def inspect_clip(item):
        key, clip = item
        sheet = out / (key + ".jpg")
        nums = frame_strip(bundle / clip["path"], 0, clip["frames"], sheet)
        prompt = (
            "同一条动画镜头的真实画面，按从左到右、从上到下播放，每格下方是此素材的源帧。"
            "先观察每个人相对鼻眼的嘴形。看着脸移动、大张口固定、转头、塞食物、嘴含杯子、咀嚼、闭唇微笑都不能当作讲话。"
            "只有亲眼认出同一张嘴在至少三格发生发音式张合交替（例如窄、开、再窄），才能报可用区间。"
            "给出witness数组，三个或更多格子的确切帧号，另给center数组，每个证据帧各一对嘴中心的原图归一化坐标[x,y]。证据中逐个写出其嘴形和有没有遮嘴物体。"
            "附件先慢放一个原片段，再展示同一素材按帧号排的格子。找不到足够证据，slots返回空。无需猜故事。列出真正可见的演员和限制。"
            '返回{"observed":"人物和观察","slots":[{"actor":"编号","start":整数,"end":整数,"witness":[帧号,帧号,帧号],"center":[[x,y],[x,y],[x,y]],"evidence":"说明"}]}。'
            + "素材"
            + key
            + "，出点不含，帧数"
            + str(clip["frames"])
            + "，格子帧号"
            + encoded(nums)
            + "，角色"
            + encoded(case["actors"])
        )

        def validate(v):
            if not isinstance(v["slots"], list) or not v["observed"]:
                raise ValueError("需要observed和slots数组")
            for s in v["slots"]:
                pts = s["witness"]
                if (
                    s["actor"] not in case["actors"]
                    or not checked_span(s["start"], s["end"], clip["frames"])
                    or not isinstance(s.get("center"), list)
                    or len(s["center"]) != len(pts)
                    or any(
                        not isinstance(q, list)
                        or len(q) != 2
                        or not all(
                            isinstance(z, (float, int)) and 0 <= z <= 1 for z in q
                        )
                        for q in s["center"]
                    )
                    or not isinstance(pts, list)
                    or len(pts) < 3
                    or pts != sorted(set(pts))
                    or any(n not in nums or not s["start"] <= n < s["end"] for n in pts)
                    or not s["evidence"]
                ):
                    raise ValueError(
                        "证据帧须出现在格子和源区间内，并按播放顺序列至少3个"
                    )
            return v

        media = out / (key + "_proof.mp4")
        proof_reel(bundle / clip["path"], sheet, media)
        obs = ask(case, out, "mouth_strip_" + key, prompt, media, validate)
        slots = []
        for i, slot in enumerate(obs["slots"]):
            detail = out / (key + "_mouth_" + str(i) + ".mp4")
            closeup(bundle / clip["path"], slot, case, detail)
            q = (
                "视频左侧是整幅动画，右侧放大了待核验的嘴，时间顺序与源片一致。"
                "先确认右侧确实可以辨认该角色的嘴，再逐次看至少一次开、合、再开或合、开、再合。"
                "运动、喝水吃饭、闭眼、大笑、摆头或一直同一个嘴形不合格，无法确认也不合格。"
                '返回{"confirmed":true或false,"actor":"编号或none","evidence":"逐次观察及干扰因素"}。'
                + "演员对照"
                + encoded(case["actors"])
                + "，待核验角色"
                + slot["actor"]
            )

            def check(v):
                if (
                    type(v["confirmed"]) is not bool
                    or v["actor"] not in [*case["actors"], "none"]
                    or not v["evidence"]
                ):
                    raise ValueError("缺实际嘴形与角色判断")
                return v

            answer = ask(
                case, out, "mouth_detail_" + key + "_" + str(i), q, detail, check
            )
            slot["detail_check"] = answer
            if answer["confirmed"] and answer["actor"] == slot["actor"]:
                slots.append(slot)
        return key, {
            "observed": obs["observed"],
            "slots": slots,
            "first_view": first[key],
            "strip_view": obs,
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        for key, result in pool.map(inspect_clip, case["clips"].items()):
            checked[key] = result
            write_json(out / "checked.partial.json", checked)
    write_json(out / "checked.json", checked)


def review_render(case, bundle, out, film, cuts, tag):
    from omni_story.library.selected_edit import concat, ff

    reviewed = []
    # One separate labelled excerpt per line keeps the model on the right cut and the actual dub.
    target_lines = [l for l in case["lines"] if l.get("on_screen", True)]
    for seq in range(0, len(target_lines), 3):
        group = target_lines[seq : seq + 3]
        parts = []
        for line in group:
            a, b = line["at"], line["end"]
            p = out / (tag + "_" + line["id"] + ".mp4")
            vf = (
                f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,scale=960:540,pad=iw:ih+32:0:32,"
                + f"drawtext=text='{line['id']}  output frame %{{eif\\:n+{a}\\:d}}':x=8:y=3:fontsize=23:fontcolor=white"
            )
            ff(
                "-i",
                film,
                "-vf",
                vf,
                "-af",
                f"atrim=start={a / case['fps']}:end={b / case['fps']},asetpts=PTS-STARTPTS",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-threads",
                2,
                "-crf",
                18,
                "-c:a",
                "aac",
                p,
            )
            parts.append(p)
        reel = concat(out, parts, tag + "_batch_" + str(seq))
        clock = [{"id": l["id"], "at": l["at"], "end": l["end"]} for l in group]
        prompt = (
            "审真实成片的短片。每句左上有句号和成片帧号。独立观察最显眼角色、嘴是否清楚、谁真的有发音式嘴形变化，"
            "分清咀嚼、露齿不动、端杯、定格、转头。返回每句连续区间spans，start、end是成片帧，出点不含。"
            "actor填写你看到的有证据的说话者，或none。status取talking,still,eating,hidden,uncertain。"
            "others布尔值表示画面还看得见其他演员。每段用evidence写亲眼看见的变化，无法核实写uncertain。"
            '返回{"lines":[{"id":"句号","spans":[{"start":整数,"end":整数,"actor":"编号或none","status":"枚举","others":true,"evidence":"嘴形变化"}]}]}。'
            "一个句子内实况相同可一段，变化时分段。每句必须逐帧覆盖。[角色]"
            + encoded(case["actors"])
            + "[时钟]"
            + encoded(clock)
        )

        def validate(v, group=group, clock=clock):
            if [l["id"] for l in v["lines"]] != [l["id"] for l in group]:
                raise ValueError("按顺序写所有句号 " + encoded(clock))
            for item, l in zip(v["lines"], group):
                prev = l["at"]
                for s in item["spans"]:
                    if (
                        s["start"] != prev
                        or not checked_span(s["start"], s["end"], l["end"])
                        or s["actor"] not in [*case["actors"], "none"]
                        or not s["evidence"]
                        or type(s["others"]) is not bool
                        or s["status"]
                        not in ("talking", "still", "eating", "hidden", "uncertain")
                    ):
                        raise ValueError("检查帧范围、actor、status和others " + l["id"])
                    prev = s["end"]
                if prev != l["end"]:
                    raise ValueError("完整覆盖句子 " + encoded(l["id"]))
            return v

        reviewed += ask(case, out, tag + "_review_" + str(seq), prompt, reel, validate)[
            "lines"
        ]
        write_json(out / (tag + "_review.partial.json"), reviewed)
    return reviewed


def load_verified_sources(case, path):
    observed = read(path)
    if set(observed) != set(case["clips"]) or any(
        item.get("source_sha256", case["clips"][key]["sha256"])
        != case["clips"][key]["sha256"]
        for key, item in observed.items()
    ):
        raise ValueError("嘴动证据与这批素材不匹配")
    location_file = path.parent / "locations.json"
    if not location_file.exists():
        return observed
    positions = read(location_file)
    for key, item in observed.items():
        count = (case["clips"][key]["frames"] + 2) // 3
        anchors = [3 * (count // 6), 3 * (count // 2), 3 * (5 * count // 6)]
        people = {r["actor"]: r["centers"] for r in positions.get(key, [])}
        for slot in item["slots"]:
            if not slot.get("center") and slot["actor"] in people:
                slot["track_frames"] = anchors
                slot["center"] = people[slot["actor"]]
    return observed


def mouth_survives_crop(case, cut, slot, frame):
    import numpy as np

    track = slot.get("track_frames", slot.get("witness", []))
    coords = slot.get("center", [])
    if not coords or len(coords) != len(track):
        return False
    x = float(np.interp(frame, track, [p[0] for p in coords]))
    y = float(np.interp(frame, track, [p[1] for p in coords]))
    crop = cut.get("crop", [0, 0, 1, 1])
    left, top, right, bottom = crop
    widths = slot.get("widths", [])
    if len(widths) == len(track):
        from omni_story.library.dialogue_mouth_tracks import mouth_region

        width = float(np.interp(frame, track, widths))
        if mouth_region((x, y), width, crop) is None:
            return False
    elif not (left < x < right and top < y < bottom):
        return False
    for mask in case["clips"][cut["clip"]].get("masks", []):
        if (
            mask["start_s"] <= frame / case["fps"] < mask["end_s"]
            and mask["x"] <= x <= mask["x"] + mask["w"]
            and mask["y"] <= y <= mask["y"] + mask["h"]
        ):
            return False
    return True


def score(case, cuts, views, observed, rendered=None, competing=None):
    results = []
    all_approved = all_voiced = all_others = 0
    target_lines = [l for l in case["lines"] if l.get("on_screen", True)]
    if views is None:
        views = [{"id": l["id"], "spans": None} for l in target_lines]
    if [l["id"] for l in target_lines] != [v["id"] for v in views]:
        raise ValueError("Rendered review does not cover all on-screen lines")
    for line, v in zip(target_lines, views):
        good = []
        other = []
        unknown = []
        missing = []
        voiced_frames = set(line["voice_frames"])
        other_mouths = {
            actor: sorted(voiced_frames & set(frames))
            for actor, frames in (competing or {}).get(line["id"], {}).items()
            if voiced_frames.intersection(frames)
        }
        conflicting_frames = {n for frames in other_mouths.values() for n in frames}
        verified_mouth = set(rendered[line["id"]]) if rendered is not None else None
        for n in line["voice_frames"]:
            c = next(c for c in reversed(cuts) if c["at"] <= n)
            frame = c["in"] + n - c["at"]
            slot = any(
                s["actor"] == line["actor"]
                and s["start"] <= frame < s["end"]
                and mouth_survives_crop(case, c, s, frame)
                for s in observed[c["clip"]]["slots"]
            )
            s = next((s for s in v["spans"] or () if s["start"] <= n < s["end"]), None)
            visible_cycle = (
                n in verified_mouth
                if verified_mouth is not None
                else s and s["status"] == "talking" and s["actor"] == line["actor"]
            )
            if slot and visible_cycle and n not in conflicting_frames:
                good.append(n)
            if not slot:
                missing.append(n)
            if s is None or s["status"] == "uncertain":
                unknown.append(n)
            if s and s["others"]:
                other.append(n)
        total = len(line["voice_frames"])
        all_voiced += total
        all_approved += len(good)
        all_others += len(other)
        results.append(
            {
                "id": line["id"],
                "actor": line["actor"],
                "text": line["text"],
                "voiced": total,
                "approved": len(good),
                "confirmed_at": good,
                "competing_mouths": other_mouths,
                "competing_mouth_frames": len(conflicting_frames),
                "passed": total > 0 and len(good) == total,
                "other_visible": len(other) if v["spans"] is not None else None,
                "unknown": len(unknown) if v["spans"] is not None else None,
                "without_source_support": len(missing),
                "observations": v["spans"],
            }
        )
    return {
        "lines": results,
        "passed": sum(l["passed"] for l in results),
        "total": len(results),
        "voiced_frames": all_voiced,
        "confirmed_frames": all_approved,
        "competing_mouth_frames": sum(l["competing_mouth_frames"] for l in results),
        "other_visible_frames": all_others
        if all(v["spans"] is not None for v in views)
        else None,
    }


def frame_at(cuts, n):
    row = next(r for r in reversed(cuts) if r["at"] <= n)
    return row["clip"], row["in"] + n - row["at"], row.get("crop", [0, 0, 1, 1])


def splice(cuts, patches, case):
    """Only the named frame ranges may change. Carry the original source offset across both joins."""
    by_id = {l["id"]: l for l in case["lines"]}
    output = []
    old = None
    for n in range(case["total"]):
        seq = next(
            (
                cs
                for key, cs in patches.items()
                if by_id[key]["at"] <= n < by_id[key]["end"]
            ),
            cuts,
        )
        key, src, crop = frame_at(seq, n)
        if old != (key, src - 1, crop):
            output.append({"at": n, "clip": key, "in": src, "crop": crop})
        old = (key, src, crop)
    return validate_cuts(output, case)


def repair_dialogue(case, bundle, out, cuts, views, cert, score0):
    avail = {key: s["slots"] for key, s in cert.items() if s["slots"]}
    blocks = scene_blocks(case)
    clock = {l["id"]: l for l in case["lines"]}
    weak = {
        l["id"]
        for l in score0["lines"]
        if l["voiced"] > l["approved"]
        and any(
            s["actor"] == l["actor"]
            for b in blocks
            if b["at"] < clock[l["id"]]["end"] and b["end"] > clock[l["id"]]["at"]
            for key in b["clips"]
            for s in avail.get(key, [])
        )
    }
    if not weak:
        return {
            "selected": "draft",
            "reason": "现有素材没有可用于失败句说话人的已确认嘴动区间，或所有句已通过",
        }
    failed = [v for v in score0["lines"] if v["id"] in weak]
    prompt = (
        "只考虑下面有已验证源素材的失败句。旧剪辑表、导出实帧的复看、已核验源区间都在下方。"
        '仅在能实质改善说话人嘴动时修改。返回{"patches":[{"id":"句号","cuts":[{"at":成片绝对帧,"clip":"编号","in":源帧}]}],"reason":"说明"}。'
        "每项整句替换，从该句at开始，最后一刀到该句end为止。可自定刀数及归一化crop，或者返回空数组。"
        "不改其余时间。录像里REF、DUB和M素材是原始节目资料。"
        + encoded(
            {
                "current": cuts,
                "review": failed,
                "slots": public_slots(cert),
                "scene_blocks": blocks,
                "lines": [l for l in public_clock(case) if l["id"] in weak],
                "context": case["context"],
                "actors": case["actors"],
            }
        )
    )

    def validate(v):
        patches = {}
        for patch in v["patches"]:
            key = patch["id"]
            cs = patch["cuts"]
            if (
                key not in weak
                or key in patches
                or not cs
                or cs[0]["at"] != clock[key]["at"]
            ):
                raise ValueError("仅可完整替换指定句子")
            at, end = clock[key]["at"], clock[key]["end"]
            local = [dict(c, at=c["at"] - at) for c in cs]
            validate_cuts(local, dict(case, total=end - at))
            patches[key] = cs
        validate_scenes(splice(cuts, patches, case), case)
        if not v.get("reason"):
            raise ValueError("说明取舍")
        return v

    reply = ask(
        case, out, "speaker_local_recut", prompt, bundle / case["media"], validate
    )
    write_json(out / "repair_decision.json", reply)
    patches = {p["id"]: p["cuts"] for p in reply["patches"]}
    if not patches:
        return {"selected": "draft", "reason": reply["reason"]}
    alt = splice(cuts, patches, case)
    if all(frame_at(cuts, n) == frame_at(alt, n) for n in range(case["total"])):
        return {"selected": "draft", "reason": "源帧及构图完全相同"}
    alternative = render(case, bundle, out, alt, "revision")
    changed = [l for l in case["lines"] if l["id"] in patches]
    new_views = review_render(
        dict(case, lines=changed), bundle, out, alternative, alt, "revision"
    )
    updated = {v["id"]: v for v in new_views}
    merged = [updated.get(v["id"], v) for v in views]
    score1 = score_output(case, bundle, out, alternative, alt, merged, cert, "revision")
    write_json(out / "revision_views.json", merged)
    write_json(out / "revision_score.json", score1)
    regression = any(
        set(a["confirmed_at"]) - set(b["confirmed_at"])
        for a, b in zip(score0["lines"], score1["lines"])
    )
    if not regression and score1["confirmed_frames"] > score0["confirmed_frames"]:
        return {
            "selected": "revision",
            "reason": "已确认嘴动覆盖增加，原通过的发声帧全部保留",
        }
    return {"selected": "draft", "reason": "逐帧比较未证实改善或已确认说话画面减少"}


def score_output(case, bundle, out, film, cuts, views, observed, tag, timeline=()):
    rendered = None
    competing = {line["id"]: {} for line in case["lines"]}
    if all(item.get("method") == "contour_cycle" for item in observed.values()):
        from omni_story.library.dialogue_mouth_tracks import rendered_mouths_by_actor

        tracks = rendered_mouths_by_actor(case, bundle, film, cuts, observed)
        rendered = {}
        for line in case["lines"]:
            lid = line["id"]
            rendered[lid] = tracks[lid].get(line["actor"], [])
            competing[lid] = {
                actor: set(frames)
                for actor, frames in tracks[lid].items()
                if actor != line["actor"]
            }
        write_json(out / (tag + "_visible_cycles.json"), rendered)
    for issue in timeline:
        if issue["kind"] == "other_mouth_moving":
            competing[issue["line"]].setdefault(issue["actor"], set()).update(
                range(issue["first"], issue["end"])
            )
    return score(case, cuts, views, observed, rendered, competing)


def audit_and_repair(case, bundle, out, cuts, film, source_read):
    views = review_render(case, bundle, out, film, cuts, "draft")
    write_json(out / "draft_views.json", views)
    if source_read is not None:
        observed = source_read
        metrics = score_output(case, bundle, out, film, cuts, views, observed, "draft")
        write_json(out / "draft_score.json", metrics)
        result = repair_dialogue(case, bundle, out, cuts, views, observed, metrics)
        final = read(out / (result["selected"] + "_score.json"))
        write_json(
            out / "outcome.json",
            dict(
                **result,
                accepted=final["passed"] == final["total"],
                score=final,
                video=str(out / (result["selected"] + ".mp4")),
            ),
        )


def recover(case, bundle, out):
    from omni_story.library.dialogue_mouth_detail import recover_slots

    prior_path = out.parent / "source_frames" / "checked.json"
    prior = (
        read(prior_path)
        if prior_path.is_file()
        else {k: {"observed": "", "slots": []} for k in case["clips"]}
    )
    return recover_slots(case, bundle, out, prior, ask)


def run_speaker_trial(bundle, out):
    case = read(bundle / "bundle.json")
    editing = out / "speaker"
    editing.mkdir(parents=True, exist_ok=True)
    check_bundle(case, bundle)
    from omni_story.library.dialogue_mouth_tracks import observe

    scene_blocks(case)
    observe(case, bundle, out / "tracks", ask)
    run_edit(case, bundle, editing, "speaker", out / "tracks" / "checked.json")
    outcome = read(editing / "outcome.json")
    return (
        editing / (outcome["selected"] + ".mp4")
        if outcome["accepted"]
        else editing / "outcome.json"
    )


def check_bundle(case, bundle):
    for name, expected in case["hashes"].items():
        if sha256_file(bundle / name) != expected:
            raise ValueError("media changed " + name)
    for key, clip in case["clips"].items():
        if (
            not re.fullmatch(r"[a-zA-Z0-9]+", key)
            or sha256_file(bundle / clip["path"]) != clip["sha256"]
        ):
            raise ValueError("invalid clip or source changed " + key)
    if not case["lines"] or not any(l.get("on_screen", True) for l in case["lines"]):
        raise ValueError("No on-screen dialogue to evaluate")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "stage",
        choices=[
            "inventory",
            "source_frames",
            "recover",
            "weighted",
            "free",
            "speaker",
            "run",
        ],
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="Frozen verified source report used by the speaker arm",
    )
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    case = read(bundle / "bundle.json")
    check_bundle(case, bundle)
    if args.stage == "run":
        print(run_speaker_trial(bundle, out))
        return
    if args.stage == "inventory":
        write_json(out / "source_read.json", inventory(case, bundle, out))
    elif args.stage == "source_frames":
        source_frames(case, bundle, out)
    elif args.stage == "recover":
        recover(case, bundle, out)
    else:
        run_edit(
            case,
            bundle,
            out,
            args.stage,
            args.evidence,
        )


if __name__ == "__main__":
    main()
