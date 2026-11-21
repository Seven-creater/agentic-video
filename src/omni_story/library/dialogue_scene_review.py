"""Ground dialogue editing in visible actions, and compare repairs on the exported timeline."""

from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

from .dialogue_mouth_detail import sampled_frames
from .state import LibraryStopped, ProviderRefused, ReplyUnusable, write_json


def inspect_sources(case, bundle, out, ask):
    """Read source context once, including faces when the mouth tracker has no positive slot."""
    out.mkdir(parents=True, exist_ok=True)
    keys = list(case["clips"])

    def inspect(batch):
        width, height = 240, 135
        panel = Image.new("RGB", (4 * width, len(batch) * 2 * (height + 20)), "#20242a")
        draw = ImageDraw.Draw(panel)
        sample_numbers = {}
        for i, key in enumerate(batch):
            frames = sampled_frames(bundle / case["clips"][key]["path"], step=1)
            if len(frames) != case["clips"][key]["frames"]:
                raise ValueError("Invalid decoded frame count for " + key)
            samples = sorted({round(j * (len(frames) - 1) / 7) for j in range(8)})
            sample_numbers[key] = samples
            for j, n in enumerate(samples):
                x, y = j % 4 * width, (2 * i + j // 4) * (height + 20)
                panel.paste(
                    Image.fromarray(frames[n]).resize((width, height)), (x, y + 20)
                )
                draw.text((x + 4, y + 3), f"{key}  F{n}", fill="white")
        media = out / (batch[0] + "_source_context.jpg")
        panel.save(media, quality=92)
        prompt = (
            "每个素材按源帧顺序展示。逐格看每个演员的脸是否可辨。背对、走出画外、完全遮住的脸不可填。"
            "只抄图上的素材号和帧号。给出可剪进故事的实际动作，例如入画、进食、亮出单张物品、对方看懂后的反应。"
            "动作要写清楚是谁做、道具此时在哪。仅靠这些图无法判断的细节写看不清。"
            "每个标出的帧都填可辨的演员编号，空数组代表这张图没有认出脸。"
            '返回 {"clips":[{"clip":"M01","faces":[[源帧整数,["人物编号"]]],'
            '"actions":[{"f":源帧整数,"what":"实际看到的动作"}]}]}。按顺序 Keys='
            + str(sample_numbers)
            + "。人物="
            + str(case["actors"])
        )

        def validate(reply):
            if [item["clip"] for item in reply["clips"]] != batch:
                raise ValueError("逐段返回素材")
            for item in reply["clips"]:
                samples = sample_numbers[item["clip"]]
                if [row[0] for row in item["faces"]] != samples:
                    raise ValueError("每个标出的源帧需要一项")
                if any(
                    not isinstance(ids, list) or not set(ids) <= case["actors"].keys()
                    for _, ids in item["faces"]
                ):
                    raise ValueError("脸使用已锁演员编号")
                if any(a["f"] not in samples or not a["what"] for a in item["actions"]):
                    raise ValueError("动作要有图上见证帧")
            return reply

        try:
            return ask(case, out, "scene_sources_" + batch[0], prompt, media, validate)[
                "clips"
            ]
        except (LibraryStopped, ProviderRefused, ReplyUnusable) as exc:
            return [
                dict(clip=key, faces=[], actions=[], unavailable=str(exc))
                for key in batch
            ]

    batches = [keys[i : i + 3] for i in range(0, len(keys), 3)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = {
            item["clip"]: item for rows in pool.map(inspect, batches) for item in rows
        }
    for key, record in result.items():
        record["source_sha256"] = case["clips"][key]["sha256"]
    write_json(out / "scene_sources.json", result)
    return result


def source_brief(case, observed, context):
    return {
        key: {
            "cast": clip.get("visible", []),
            "faces": context[key]["faces"],
            "actions": context[key]["actions"],
            "mouth": [
                {k: slot[k] for k in ("actor", "start", "end", "evidence")}
                for slot in observed[key]["slots"]
            ],
            "blocked": [
                {k: row[k] for k in ("actor", "start", "end", "action")}
                for row in observed[key].get("features", [])
                if row.get("actor")
                and row.get("action") in ("eating", "drinking", "hidden")
            ],
        }
        for key, clip in case["clips"].items()
    }


def source_frame(clip, source_index, fps):
    return (
        clip.get("source_sha256") or clip["sha256"],
        round(clip.get("source_start_s", 0) * fps) + source_index,
    )


def timeline_findings(case, cuts, blocks, brief):
    """Compare actual source frames, even when a physical interval has multiple clip aliases."""
    from itertools import groupby

    from .dialogue_mouth_edit import frame_at

    findings = []
    seen_by_scene = {}
    for line in case["lines"]:
        if not line.get("on_screen", True):
            continue
        repeats = []
        blocked = []
        other_eating = {}
        voiced = set(line["voice_frames"])
        for frame in range(line["at"], line["end"]):
            block = next(b for b in blocks if b["at"] <= frame < b["end"])
            seen = seen_by_scene.setdefault(block["at"], set())
            key, n, crop = frame_at(cuts, frame)
            original = source_frame(case["clips"][key], n, case["fps"])
            if original in seen:
                repeats.append(frame)
            seen.add(original)
            if frame in voiced and any(
                s["actor"] == line["actor"] and s["start"] <= n < s["end"]
                for s in brief[key]["blocked"]
            ):
                blocked.append(frame)
            if frame in voiced:
                # Historical observations have no mouth coordinates. A crop can hide
                # the conflicting mouth, but the crop alone cannot certify that it did.
                is_full = crop == [0, 0, 1, 1] and not case["clips"][key].get("masks")
                kind = "other_mouth_moving" if is_full else "other_mouth_unverified"
                for segment in brief[key]["blocked"]:
                    if (
                        segment["actor"] != line["actor"]
                        and segment.get("action") == "eating"
                        and segment["start"] <= n < segment["end"]
                    ):
                        other_eating.setdefault((segment["actor"], kind), []).append(
                            frame
                        )
        for (actor, kind), frames in other_eating.items():
            for _, group in groupby(
                enumerate(frames), key=lambda item: item[1] - item[0]
            ):
                run = [n for _, n in group]
                findings.append(
                    dict(
                        line=line["id"],
                        kind=kind,
                        actor=actor,
                        first=run[0],
                        end=run[-1] + 1,
                        why="源实帧已看到另一演员张嘴进食，完整画幅保留了该动作"
                        if kind == "other_mouth_moving"
                        else "源画面已确认另一人进食，需核实裁框或遮挡是否确实挡住那张嘴",
                    )
                )
        onset = min(line["voice_frames"], default=line["at"])
        key, src, _ = frame_at(cuts, onset)
        facts = brief[key]["faces"]
        left = next(
            (
                ids
                for f, ids in reversed(facts)
                if f <= src and src - f <= case["fps"] // 4
            ),
            [],
        )
        right = next((ids for f, ids in facts if src < f <= src + case["fps"] // 4), [])
        if left and right and line["actor"] not in left and line["actor"] not in right:
            findings.append(
                dict(
                    line=line["id"],
                    kind="onset_face_unconfirmed",
                    first=onset,
                    end=onset + 1,
                    why="开口附近两张源画面只认出了其他演员，请核对具体入画帧",
                )
            )
        for indices, kind in (
            (repeats, "replayed_picture"),
            (blocked, "blocked_speaker"),
        ):
            for _, group in groupby(enumerate(indices), key=lambda t: t[1] - t[0]):
                run = [n for _, n in group]
                if kind == "replayed_picture" and len(run) < max(3, case["fps"] // 3):
                    continue
                findings.append(
                    dict(
                        line=line["id"],
                        kind=kind,
                        first=run[0],
                        end=run[-1] + 1,
                        why="同一场已播放过同一源帧"
                        if kind == "replayed_picture"
                        else "源实帧已确认进食、喝水或嘴被遮挡",
                    )
                )
    return findings


def available_footage(case, blocks, brief):
    result = {}
    for line in case["lines"]:
        if not line.get("on_screen", True):
            continue
        keys = {
            k
            for b in blocks
            if b["at"] < line["end"] and line["at"] < b["end"]
            for k in b["clips"]
        }
        face = any(line["actor"] in ids for k in keys for _, ids in brief[k]["faces"])
        voiced = len(line["voice_frames"])
        slots = {
            source_frame(case["clips"][k], f, case["fps"])
            for k in keys
            for s in brief[k]["mouth"]
            if s["actor"] == line["actor"]
            for f in range(s["start"], s["end"])
        }
        result[line["id"]] = dict(
            face=face, distinct_mouth_frames=len(slots), voiced=voiced
        )
    return result


def review_story(case, out, film, cuts, tag, brief, ask):
    from .dialogue_mouth_edit import scene_blocks

    from .render import _probe

    streams = _probe(film, "ffprobe")["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    if int(video["nb_frames"]) != case["total"]:
        raise ValueError("Incomplete review export")
    blocks = scene_blocks(case)

    def inspect(block):
        selected = [
            l
            for l in case["lines"]
            if l.get("on_screen", True)
            and block["at"] <= min(l["voice_frames"], default=l["at"]) < block["end"]
        ]
        if not selected:
            return {"issues": [], "unknown": [], "ending": ""}
        return review_scene(case, out, film, block, selected, tag, ask)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(inspect, blocks))
    checked = {
        "issues": [i for r in results for i in r["issues"]],
        "unknown": [u for r in results for u in r["unknown"]],
        "ending": next((r["ending"] for r in reversed(results) if r.get("ending")), ""),
        "reviews": results,
    }
    write_json(out / (tag + "_scene_review.json"), checked)
    return checked


def review_scene(case, out, film, block, selected, tag, ask):
    from .dialogue_mouth_edit import encoded
    from .selected_edit import ff

    start, end, fps = block["at"], block["end"], case["fps"]
    media = out / f"{tag}_scene_{start}_review.mp4"
    # Retain the original pace and soundtrack. The provider caps an attachment at 8 MB.
    rate = max(32, min(900, int(54_000 * fps / (end - start)) - 48))
    vf = (f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,scale=640:360,"
          f"pad=iw:ih+24:0:24,drawtext=text='F%{{eif\\:n+{start}\\:d}}':x=5:y=2:fontsize=18:fontcolor=white")
    ff("-i", film, "-vf", vf, "-af", f"atrim=start={start/fps}:end={end/fps},asetpts=PTS-STARTPTS",
       "-c:v", "libx264", "-threads", 2, "-b:v", f"{rate}k", "-maxrate", f"{rate}k",
       "-bufsize", f"{rate}k", "-c:a", "aac", "-b:a", "48k", media)
    clock = [
        dict(
            id=l["id"],
            actor=l["actor"],
            text=l["text"],
            at=l["at"],
            end=l["end"],
            onset=min(l["voice_frames"], default=l["at"]),
            on_screen=l.get("on_screen", True),
        )
        for l in selected
    ]
    for line in clock:
        line["seconds_in_clip"] = [round((line[k] - start) / fps, 3) for k in ("at", "end")]
    prompt = (
        "带原配音连看这一段，判断观众能否认清谁在说、发生了什么。左上 F 是成片帧号。"
        "说话人已经交代清楚，声音延续到听者、物件或全景都是正常剪法。"
        "speaker_confusion 请指出实际争抢台词的说话动作，或交接时让人认错的声画线索。"
        "action_reset 只报同一动作可见地重演，说明两次各做了什么。变景别、情绪突变可以是剪辑意图。"
        "incompatible_action 是动作和剧情明显冲突，payoff_missing 是剧情承诺给观众看的结果缺失。"
        "只报能指出实际影响的问题。无法判断剧情或归属的句子填 unknown。"
        'issues 只填确有影响的问题，顺畅时填空数组。每条给台词 id 与本段播放秒数 t。'
        '返回 {"issues":[{"line":"台词id","t":片段内秒数,"kind":"问题类型","why":"实际依据和影响"}],'
        '"unknown":[],"ending":"最后看到什么"}。演员和台词：' + encoded(case["actors"]) + encoded(clock)
    )
    if block["end"] == case["total"]:
        prompt += " 结尾若承诺展示某个结果却缺了，就在 issues 引用结尾帧；纯对白可以靠台词收住。"
    lines = {l["id"]: l for l in clock}

    def validate(reply):
        if (
            not isinstance(reply["issues"], list)
            or not set(reply["unknown"]) <= lines.keys()
            or not reply["ending"]
        ):
            raise ValueError("缺少复看字段")
        for item in reply["issues"]:
            lid = item["line"]
            if lid not in lines:
                matches = [l["id"] for l in clock if l["text"] == lid]
                if len(matches) == 1:
                    item["line"] = matches[0]
            if "t" in item:
                t = item["t"]
                if type(t) not in (int, float) or not 0 <= t < (end - start) / fps:
                    raise ValueError("t 须为片段内的播放秒数")
                item["f"] = start + int(t * fps + 1e-8)
            n = item.get("f")
            if (
                item["line"] not in lines
                or type(n) is not int
                or not start <= n < end
                or not lines[item["line"]]["at"] <= n < (
                    end if item["line"] == clock[-1]["id"] else lines[item["line"]]["end"]
                )
                or item["kind"]
                not in (
                    "speaker_confusion",
                    "incompatible_action",
                    "action_reset",
                    "payoff_missing",
                )
                or not item["why"]
            ):
                raise ValueError("每个问题需要有效的句号和该句对应的成片帧号")
            line = lines[item["line"]]
            if item["kind"] == "speaker_confusion" and (
                not line["on_screen"] or not line["onset"] <= n < line["end"]
            ):
                raise ValueError(
                    "说话人混淆的证据须对应这句对白及其发声区间"
                )
        # Older review replies sometimes place an ending defect in a separate field.
        conclusion = reply.get("closing") or {}
        if conclusion.get("state") == "missing" and (
            type(conclusion.get("f")) is not int
            or not max(start, clock[-1]["at"]) <= conclusion["f"] < end
            or not conclusion.get("why")
        ):
            raise ValueError("结尾缺失要指出片内帧和画面依据")
        return reply

    try:
        checked = ask(
            case, out, tag + f"_scene_{block['at']}_review", prompt, media, validate
        )
    except (LibraryStopped, ProviderRefused, ReplyUnusable) as exc:
        checked = {"issues": [], "unknown": list(lines), "unavailable": str(exc)}
    closing = checked.get("closing") or {}
    if closing.get("state") == "missing" and not any(
        i["line"] == clock[-1]["id"] and i["kind"] == "payoff_missing"
        for i in checked["issues"]
    ):
        checked["issues"].append(
            {
                "line": clock[-1]["id"],
                "f": closing["f"],
                "kind": "payoff_missing",
                "why": closing["why"],
            }
        )
    if closing.get("state") == "unknown" and clock[-1]["id"] not in checked["unknown"]:
        checked["unknown"].append(clock[-1]["id"])
    return dict(checked, block=block["at"])


def introduced_story_issues(before, after, previous, candidate, witnessed=None):
    """A model report about unchanged picture is not a newly introduced defect."""
    from .dialogue_mouth_edit import frame_at

    prior = {
        (i["kind"], i["line"], frame_at(previous, i["f"])[:2]) for i in before["issues"]
    }
    previous_payoffs = {
        i["line"] for i in before["issues"] if i["kind"] == "payoff_missing"
    }
    return [
        i
        for i in after["issues"]
        if (
            frame_at(previous, i["f"]) != frame_at(candidate, i["f"])
            if witnessed is None
            else i["f"] in witnessed
        )
        and (i["kind"], i["line"], frame_at(candidate, i["f"])[:2]) not in prior
        and not (i["kind"] == "payoff_missing" and i["line"] in previous_payoffs)
    ]


def finish_scene(case, bundle, out, cuts, film, observed, brief, ask):
    from .dialogue_mouth_edit import (
        encoded,
        frame_at,
        render,
        scene_blocks,
        score_output,
        splice,
        validate_cuts,
        validate_scenes,
    )

    blocks = scene_blocks(case)
    available = available_footage(case, blocks, brief)

    def check(media, plan, tag):
        from itertools import groupby

        source_checks = timeline_findings(case, plan, blocks, brief)
        story = review_story(case, out, media, plan, tag, brief, ask)
        score = score_output(case, bundle, out, media, plan, None, observed, tag)
        timeline = []
        for line in score["lines"]:
            for actor, positions in line["competing_mouths"].items():
                for _, group in groupby(
                    enumerate(positions), key=lambda entry: entry[1] - entry[0]
                ):
                    frames = [n for _, n in group]
                    timeline.append(
                        dict(
                            line=line["id"],
                            kind="other_mouth_moving",
                            actor=actor,
                            first=frames[0],
                            end=frames[-1] + 1,
                            why="实际导出仍显示另一位演员的嘴形反复变化",
                        )
                    )
        write_json(out / (tag + "_score.json"), score)
        return {"timeline": timeline, "story": story, "score": score, "source_checks": source_checks}

    before = check(film, cuts, "draft")
    selected = "draft"
    result = before
    reason = "暂用首稿"
    faults = before["timeline"] + before["story"]["issues"]
    if faults:
        lines = {l["id"]: l for l in case["lines"]}
        affected = [
            b
            for b in blocks
            if any(
                b["at"] < lines[f["line"]]["end"] and b["end"] > lines[f["line"]]["at"]
                for f in faults
            )
        ]
        prompt = (
            "只在本场准用素材和原配音内修复下面有证据的问题，自定景别和剪点。保留其余有效表演。"
            "每刀长度须放得进源片。无合适的改法可返回空数组。"
            '返回 {"patches":[{"at":成片起帧,"end":成片止帧,"cuts":[{"at":成片帧,"clip":"编号","in":源帧}]}],'
            '"reason":"改动和效果"}。止帧不含。'
            + encoded(
                {
                    "allowed": affected,
                    "source_total_frames": {
                        k: v["frames"] for k, v in case["clips"].items()
                    },
                    "current": cuts,
                    "issues": faults,
                    "clock": [
                        {k: v for k, v in l.items() if k != "voice_frames"}
                        for l in case["lines"]
                    ],
                    "sources": {
                        k: brief[k]
                        for k in sorted({k for b in affected for k in b["clips"]})
                    },
                }
            )
        )

        def apply(patches):
            previous_end = 0
            ranges = []
            replacements = {}
            for i, patch in enumerate(patches):
                a, b = patch["at"], patch["end"]
                if (
                    type(a) is not int
                    or type(b) is not int
                    or not previous_end <= a < b <= case["total"]
                    or not any(s["at"] <= a < b <= s["end"] for s in affected)
                ):
                    raise ValueError("按顺序修改受影响的场，区间不得重叠或越场")
                rows = patch["cuts"]
                deficits = []
                for j, row in enumerate(rows):
                    if (
                        row["clip"] not in case["clips"]
                        or type(row["in"]) is not int
                        or type(row["at"]) is not int
                    ):
                        raise ValueError("素材编号、成片帧和源帧需符合输入")
                    stop = rows[j + 1]["at"] if j + 1 < len(rows) else b
                    available_frames = case["clips"][row["clip"]]["frames"] - row["in"]
                    if stop - row["at"] > available_frames:
                        deficits.append(
                            f"{row['clip']} 从源帧{row['in']}最多放{available_frames}帧，成片区间{row['at']}-{stop}要求{stop - row['at']}帧"
                        )
                if deficits:
                    raise ValueError("、".join(deficits))
                validate_cuts(
                    [dict(c, at=c["at"] - a) for c in patch["cuts"]],
                    dict(case, total=b - a),
                )
                ranges.append({"id": str(i), "at": a, "end": b})
                replacements[str(i)] = patch["cuts"]
                previous_end = b
            timeline = splice(cuts, replacements, dict(case, lines=ranges))
            validate_scenes(timeline, case)
            return timeline

        def validate(reply):
            if not isinstance(reply["patches"], list) or not reply["reason"]:
                raise ValueError("需要补丁列表和取舍")
            apply(reply["patches"])
            return reply

        try:
            decision = ask(
                case,
                out,
                "dialogue_scene_recut",
                prompt,
                bundle / case["media"],
                validate,
            )
        except (LibraryStopped, ProviderRefused, ReplyUnusable) as exc:
            decision = {
                "patches": [],
                "reason": "局部改剪未返回可执行时间线 " + str(exc),
            }
        write_json(out / "repair_decision.json", decision)
        reason = decision["reason"]
        if decision["patches"]:
            candidate = apply(decision["patches"])
            if any(
                frame_at(cuts, n) != frame_at(candidate, n)
                for n in range(case["total"])
            ):
                alternative = render(case, bundle, out, candidate, "revision")
                after = check(alternative, candidate, "revision")
                witnessed = changed_witnesses(
                    film,
                    alternative,
                    [i["f"] for r in (before, after) for i in r["story"]["issues"]],
                    case["fps"],
                )
                write_json(out / "recut_changed_witnesses.json", sorted(witnessed))
                reason = compare_reviews(before, after, cuts, candidate, witnessed)
                if reason is None:
                    selected, result, reason = (
                        "revision",
                        after,
                        "确认减少问题且无新增问题",
                    )
                elif len(affected) > 1:
                    # Keep independent repairs in other scenes if the first review found a real regression.
                    rejected_frames = {
                        n
                        for _, _, n in timeline_frames(after) - timeline_frames(before)
                    }
                    rejected_frames.update(
                        i["f"]
                        for i in introduced_story_issues(
                            before["story"], after["story"], cuts, candidate, witnessed
                        )
                    )
                    rejected_frames.update(
                        lines[l]["at"]
                        for l in set(after["story"]["unknown"])
                        - set(before["story"]["unknown"])
                    )
                    bad_blocks = [
                        b
                        for b in affected
                        if any(b["at"] <= n < b["end"] for n in rejected_frames)
                    ]
                    kept = [
                        p
                        for p in decision["patches"]
                        if not any(b["at"] <= p["at"] < b["end"] for b in bad_blocks)
                    ]
                    if kept and len(kept) < len(decision["patches"]):
                        partial = apply(kept)
                        partial_film = render(case, bundle, out, partial, "partial")
                        partial_check = check(partial_film, partial, "partial")
                        partial_witnessed = changed_witnesses(
                            film,
                            partial_film,
                            [
                                i["f"]
                                for r in (before, partial_check)
                                for i in r["story"]["issues"]
                            ],
                            case["fps"],
                        )
                        write_json(
                            out / "partial_changed_witnesses.json",
                            sorted(partial_witnessed),
                        )
                        if (
                            compare_reviews(
                                before, partial_check, cuts, partial, partial_witnessed
                            )
                            is None
                        ):
                            selected, result, reason = (
                                "partial",
                                partial_check,
                                "保留独立改善的场，退回带来新增问题的场",
                            )
    accepted = (
        not result["timeline"]
        and not result["story"]["issues"]
        and not result["story"]["unknown"]
    )
    write_json(
        out / "outcome.json",
        dict(
            selected=selected,
            reason=reason,
            accepted=accepted,
            score=result["score"],
            footage=available,
            timeline=result["timeline"],
            source_checks=result["source_checks"],
            story=result["story"],
            video=str(out / (selected + ".mp4")),
        ),
    )


def timeline_frames(result):
    return {
        (i["kind"], i["line"], n)
        for i in result["timeline"]
        for n in range(i["first"], i["end"])
    }


def compare_reviews(before, after, cuts, candidate, witnessed=None):
    if timeline_frames(after) - timeline_frames(before):
        return "保留首稿，候选新增已核实的错人动嘴"
    if after["story"].get("unavailable") or set(after["story"]["unknown"]) - set(
        before["story"]["unknown"]
    ):
        return "保留首稿，改动缺少完整复看"
    if introduced_story_issues(
        before["story"], after["story"], cuts, candidate, witnessed
    ):
        return "保留首稿，改动后的画面出现新的动作或叙事问题"
    old = {
        (i["kind"], i["line"])
        for i in before["story"]["issues"]
        if witnessed is None or i["f"] in witnessed
    }
    new = {(i["kind"], i["line"]) for i in after["story"]["issues"]}
    if timeline_frames(before) - timeline_frames(after) or old - new:
        return None
    if any(kind == "payoff_missing" for kind, _ in old & new):
        return "保留首稿，关键动作还没有复核通过"
    return "保留首稿，复看未证实改动有改善"


def changed_witnesses(before, after, witnesses, fps):
    """Compare the two exports around each cited frame at identical output times."""
    import subprocess

    import numpy as np

    if not witnesses:
        return set()
    offsets = (-max(1, fps // 6), 0, max(1, fps // 6))
    indices = sorted({max(0, f + d) for f in witnesses for d in offsets})
    selector = "+".join(f"eq(n\\,{n})" for n in indices)

    def read_thumbnails(path):
        raw = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-an",
                "-vf",
                "select=" + selector + ",scale=192:108",
                "-fps_mode",
                "passthrough",
                "-threads",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ]
        )
        return (
            np.frombuffer(raw, dtype=np.uint8).reshape(-1, 108, 192, 3).astype(np.int16)
        )

    left, right = read_thumbnails(before), read_thumbnails(after)
    if left.shape != right.shape:
        raise ValueError("Review exports contain different frame counts")

    def differences(x, y):
        # Ignore low-amplitude H.264 noise in unchanged regions.
        return float((np.abs(x - y) > 12).mean())

    changed = {n for n, x, y in zip(indices, left, right) if differences(x, y) > 0.015}
    return {f for f in witnesses if any(max(0, f + d) in changed for d in offsets)}
