"""Ground dialogue editing in visible actions, and compare repairs on the exported timeline."""

from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

from .dialogue_mouth_detail import major_picture_changes, sampled_frames
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
            if original in seen and not case["clips"][key].get("loopable"):
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
    from .dialogue_mouth_edit import frame_at, scene_blocks

    frames = sampled_frames(film, step=1)
    if len(frames) != case["total"]:
        raise ValueError("Incomplete review export")
    blocks = scene_blocks(case)
    editorial_cuts = {row["at"] for row in cuts}
    possible_internal_cuts = [
        n for n in major_picture_changes(frames) if n not in editorial_cuts
    ]

    def inspect(block):
        selected = [
            l
            for l in case["lines"]
            if l.get("on_screen", True)
            and block["at"] <= min(l["voice_frames"], default=l["at"]) < block["end"]
        ]
        if not selected:
            return {"issues": [], "unknown": [], "ending": ""}
        return review_scene(
            case,
            out,
            frames,
            cuts,
            block,
            selected,
            tag,
            brief,
            ask,
            frame_at,
            possible_internal_cuts,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(inspect, blocks))
    checked = {
        "issues": [i for r in results for i in r["issues"]],
        "unknown": [u for r in results for u in r["unknown"]],
        "ending": next((r["ending"] for r in reversed(results) if r.get("ending")), ""),
        "reviews": results,
        "possible_internal_cuts": possible_internal_cuts,
    }
    write_json(out / (tag + "_scene_review.json"), checked)
    return checked


def review_scene(
    case,
    out,
    frames,
    cuts,
    block,
    selected,
    tag,
    brief,
    ask,
    frame_at,
    possible_internal_cuts=(),
):
    from .dialogue_mouth_edit import encoded

    # Show readable frame labels on stills rather than relying on the provider's video sampler.
    watched = {}
    for line in selected:
        a, b = line["at"], line["end"]
        onset = min(line["voice_frames"], default=a)
        watched[line["id"]] = sorted(
            {
                max(block["at"], onset - 1),
                onset,
                min(b - 1, onset + case["fps"] // 5),
                (a + b) // 2,
                b - 1,
            }
        )
        # Compare both sides of a source discontinuity while the same voice is speaking.
        inner = [n for n in possible_internal_cuts if onset < n < b]
        if inner:
            watched[line["id"]] = sorted(
                set(watched[line["id"]]) | {n + d for n in inner[:2] for d in (-1, 0)}
            )
    # A new cut can sit between the regular samples; always show it as evidence of the action.
    if block["end"] == case["total"]:
        for line in selected:
            boundaries = [c["at"] for c in cuts if line["at"] < c["at"] < line["end"]]
            regular = watched[line["id"]]
            watched[line["id"]] = sorted(set(regular) | set(boundaries[:3]))
    last = min(selected[-1]["end"], block["end"] - 1)
    tail = sorted({last, (last + block["end"] - 1) // 2, block["end"] - 1})
    rows = [*watched.items(), ("END", tail)]
    width, height = 224, 126
    panel = Image.new(
        "RGB",
        (max(len(ns) for _, ns in rows) * width, len(rows) * (height + 19)),
        "#20242a",
    )
    draw = ImageDraw.Draw(panel)
    for i, (key, ns) in enumerate(rows):
        y = i * (height + 19)
        for j, n in enumerate(ns):
            x = j * width
            panel.paste(Image.fromarray(frames[n]).resize((width, height)), (x, y + 19))
            draw.text((x + 3, y + 2), f"{key}  F{n}", fill="white")
    media = out / (tag + f"_scene_{block['at']}_frames.jpg")
    panel.save(media, quality=94)
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
        for l in case["lines"]
        if l["id"] in watched
    ]
    prompt = (
        "这是真实成片的逐句截图，各行按播放顺序排列。每格标句号和成片帧 F，END是最后一句说完以后。"
        "看换人开口和句中谁在画面里，动作有没有重来。先写结尾行能看见的实际行动。"
        "碰到台词要观众看实物、听答案或等反应，请逐格确认线索在该句发声当下或之后的合理时刻兑现。"
        "上一格已经展示关键动作、台词开口马上切去另一个动作，按payoff_missing检查。"
        "只看截图，可见一排物品和观众能看清正在递出的那一件不是同一种画面。如果画面缺动作，只按截图作证。"
        "只抄画面格的F后面的整数；比如 F174 填 174。每项line填现有句号。"
        "actor_missing代表开口时本人明显在画外，incompatible_action代表说者当时实际在吃喝等，"
        "action_reset代表同场同一个动作明显重来，payoff_missing代表台词指向的关键动作或结果看不到。"
        "有充分实帧证据才填issues。unknown只填句号，原因写在结尾说明。"
        '仅返回JSON {"issues":[{"line":"l1","f":174,"kind":"actor_missing","why":"能看见谁、谁不在"}],'
        '"unknown":[],"ending":"最后一行实际交代了什么"}。'
        + "演员 "
        + encoded(case["actors"])
        + " 台词 "
        + encoded(clock)
        + " 各行照片帧号 "
        + encoded(dict(rows))
    )
    if any(block["at"] < n < block["end"] for n in possible_internal_cuts):
        prompt += "有些相邻帧在剪辑单同一刀内仍突然换画面。按每句的实际发声对比前后帧，确认是否切给了另一个演员。"
    if block["end"] == case["total"]:
        source_context = []
        for n in sorted(set(tail) | set(watched[clock[-1]["id"]])):
            key, src, _ = frame_at(cuts, n)
            labels = brief[key]["actions"]
            nearest = min(
                labels, key=lambda moment: abs(moment["f"] - src), default=None
            )
            if nearest and abs(nearest["f"] - src) <= case["fps"] // 5:
                source_context.append(
                    {"f": n, "action_in_source_nearby": nearest["what"]}
                )
        prompt += (
            " 最后一行台词与结尾的源帧观察附在下面，可以用来核对人和物，但裁切后请以截图为准。"
            "按照上一格、开口、结尾的顺序比。把准备去拿东西、已经举到对方面前、对方看到以后有反应分开。"
            "没有看清对方接过物件，就别把面对物件写成真的接过了。"
            "额外输出 closing，回答最后一句有没有明确承诺/要求观众接下来看到的动作或结果，"
            "以及发声当下到结尾具体哪格兑现。fulfilled要指出发生的动作；"
            "东西本来就在手上、动作只在开口前发生、人物只是面对东西，均不能自动当作结果。"
            '格式 "closing":{"state":"fulfilled/missing/unknown/not_applicable","f":成片实帧号,"why":"实际起因和结果"}。'
            + encoded(source_context)
        )
    lines = {l["id"]: l for l in clock}

    def validate(reply):
        if (
            not isinstance(reply["issues"], list)
            or not set(reply["unknown"]) <= lines.keys()
            or not reply["ending"]
        ):
            raise ValueError("缺少复看字段")
        for item in reply["issues"]:
            n = item["f"]
            if (
                item["line"] not in lines
                or type(n) is not int
                or not 0 <= n < case["total"]
                or n
                not in (
                    set(watched[item["line"]])
                    | (set(tail) if item["line"] == clock[-1]["id"] else set())
                )
                or item["kind"]
                not in (
                    "actor_missing",
                    "incompatible_action",
                    "action_reset",
                    "payoff_missing",
                )
                or not item["why"]
            ):
                raise ValueError("每个问题需要有效的句号和成片实帧号")
            line = lines[item["line"]]
            if item["kind"] == "actor_missing" and (
                not line["on_screen"] or not line["onset"] <= n < line["end"]
            ):
                raise ValueError(
                    "发声时说者不在画面的证据必须在该句发声区间，旁白可画外"
                )
        if block["end"] == case["total"]:
            conclusion = reply.get("closing", {})
            if (
                conclusion.get("state")
                not in ("fulfilled", "missing", "unknown", "not_applicable")
                or conclusion.get("f") not in set(tail) | set(watched[clock[-1]["id"]])
                or not conclusion.get("why")
            ):
                raise ValueError("closing需给状态、结尾截图上的整数帧号及原因")
        return reply

    try:
        checked = ask(
            case, out, tag + f"_scene_{block['at']}_review", prompt, media, validate
        )
    except (LibraryStopped, ProviderRefused, ReplyUnusable) as exc:
        checked = {"issues": [], "unknown": list(lines), "unavailable": str(exc)}
    closing = checked.get("closing", {})
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
    contradicted = []
    retained = []
    for issue in checked["issues"]:
        key, n, crop = frame_at(cuts, issue["f"])
        actor = lines[issue["line"]]["actor"]
        exact = dict(brief[key]["faces"]).get(n, [])
        if issue["kind"] == "actor_missing" and actor in exact and crop == [0, 0, 1, 1]:
            contradicted.append(issue)
        else:
            retained.append(issue)
    return dict(checked, issues=retained, contradicted=contradicted, block=block["at"])


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


def choose_exported_edit(case, out, draft, candidate, patches, ask):
    """Show both actual exports at identical frames inside each proposed change."""
    import subprocess
    from PIL import Image, ImageDraw

    indices = sorted({n for patch in patches for n in
                      (patch["at"], (patch["at"] + patch["end"] - 1) // 2, patch["end"] - 1)})
    selector = "+".join(f"eq(n\\,{n})" for n in indices)
    board = Image.new("RGB", (640, 108 * len(indices)), "white")
    labels = ImageDraw.Draw(board)
    for col, movie in enumerate((draft, candidate)):
        thumb = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(movie), "-an", "-vf",
            "select=" + selector + ",scale=160:90", "-vsync", "0", "-threads", "1", "-pix_fmt",
            "rgb24", "-f", "rawvideo", "-"])
        if len(thumb) != len(indices) * 160 * 90 * 3:
            raise ValueError("Incomplete side-by-side export")
        for i, n in enumerate(indices):
            block = 160 * 90 * 3
            frame = Image.frombytes("RGB", (160, 90), thumb[i*block:(i+1)*block])
            x = col * 320
            board.paste(frame, (x, i*108))
            labels.text((x, i*108+90), ("A" if col == 0 else "B") + f"  {n}", fill="black")
    path = out / "verified_options.jpg"
    board = board.crop((0, 0, 480, board.height))
    board.save(path, quality=83)
    def validate(v):
        if v.get("pick") not in ("A", "B") or not v.get("why"):
            raise ValueError("Select a shown export and explain the visible difference")
        return v
    answer = ask(case, out, "choose_verified_export",
                 '这是相同帧号的两条实片，左A是原剪，右B是经逐句核过的候选。按谁在说、谁在回、'
                 '动作和情绪是否连贯选一条。返回 {"pick":"A或B","why":"画面依据"}。台词：'
                 + str([l["text"] for l in case["lines"]]), path, validate)
    write_json(out / "verified_export_choice.json", answer)
    return answer


def finish_scene(case, bundle, out, cuts, film, observed, brief, ask):
    from .dialogue_mouth_edit import (
        encoded,
        expand_loops,
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

        story = review_story(case, out, media, plan, tag, ask=ask, brief=brief)
        timeline = timeline_findings(case, plan, blocks, brief)
        score = score_output(
            case, bundle, out, media, plan, None, observed, tag, timeline
        )
        known = {
            (item["line"], item["actor"], n)
            for item in timeline
            if item["kind"] == "other_mouth_moving"
            for n in range(item["first"], item["end"])
        }
        for line in score["lines"]:
            for actor, positions in line["competing_mouths"].items():
                extra = [n for n in positions if (line["id"], actor, n) not in known]
                for _, group in groupby(
                    enumerate(extra), key=lambda entry: entry[1] - entry[0]
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
        return {"timeline": timeline, "story": story, "score": score}

    before = check(film, cuts, "draft")
    selected = "draft"
    result = before
    reason = "暂用首稿"
    faults = before["timeline"] + before["story"]["issues"]
    by_id = {l["id"]: l for l in case["lines"]}
    reported = {item["line"] for item in faults}
    for measured in before["score"]["lines"]:
        line = by_id[measured["id"]]
        if measured["approved"] < measured["voiced"] and line["id"] not in reported:
            usable = any(slot["actor"] == line["actor"] for b in blocks
                         if b["at"] < line["end"] and line["at"] < b["end"]
                         for key in b["clips"] for slot in observed[key]["slots"])
            if usable:
                faults.append(dict(line=line["id"], kind="speaker_mouth_gap",
                                   first=line["at"], end=line["end"]))
    if faults:
        lines = by_id
        affected = [
            b
            for b in blocks
            if any(
                b["at"] < lines[f["line"]]["end"] and b["end"] > lines[f["line"]]["at"]
                for f in faults
            )
        ]
        affected_lines = {f["line"] for f in faults}
        protected = [(l["at"], l["end"]) for l in case["lines"] if l["id"] not in affected_lines]
        is_missing_payoff = any(f["kind"] == "payoff_missing" for f in faults)
        prompt = (
            "在已选原素材和固定配音内，最多做一次局部改剪。先顾说话人开口的镜头和重要动作的先后。"
            "有错就改对应几秒，可把相邻停顿一起剪，使动作接顺。"
            "同一句内只允许这句说话人明显动嘴；画面里另一个人在动嘴时，优先裁掉那张嘴并保住说话人的脸。"
            "如果裁不掉就换画面。缺可用嘴动仍可修走入画面、吃喝、动作倒放等。"
            "已出现过的举动不要从相同源帧重新播放，程序会在渲染前核对源窗口的重叠；loopable里的原地静态镜头除外。"
            "听者反应、递出的物件等关键信息留到能看懂的时刻。"
            "每刀的时长是下一刀at减本刀at，最后一刀改用区间end。时长必须不超过素材总帧数减in；loopable素材可以超过，程序从in起循环重播。"
            "一刀时长放不下就把切点移到足以容纳该素材的位置，或在经过核实的范围内补另一刀；"
            "缩短补丁也可以，剩下沿用首稿。返回前逐刀用下面的总帧数核算。"
            '返回 {"patches":[{"at":成片起帧,"end":成片止帧,"cuts":[{"at":成片帧,"clip":"素材编号","in":源帧}]}],'
            '"reason":"改了哪些动作及效果"}。无解填空补原因。区间止帧不含。'
            + (
                "这次缺少台词要求的实际动作或反应。先确定最后该看到的动作，再倒推前面的画面。"
                "可把太早出现的源动作整段移到应出现的位置，补丁允许覆盖句前停顿和句后收尾。"
                "避开把该物件一直留在原说话人手上却没有给对方看的候选。结尾目标未兑现就不要提交只改发声过程中几帧的补丁。"
                if is_missing_payoff
                else ""
            )
            + encoded(
                {
                    "allowed": affected,
                    "source_total_frames": {
                        k: v["frames"] for k, v in case["clips"].items()
                    },
                    "loopable": sorted(
                        k for k, v in case["clips"].items() if v.get("loopable")
                    ),
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
                if any(lo < b and a < hi for lo, hi in protected):
                    raise ValueError("候选覆盖了已通过的台词，只改有问题的台词和相邻停顿")
                rows = expand_loops(patch["cuts"], case, total=b)
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
                    [dict(c, at=c["at"] - a) for c in rows],
                    dict(case, total=b - a),
                )
                ranges.append({"id": str(i), "at": a, "end": b})
                replacements[str(i)] = rows
                previous_end = b
            timeline = splice(cuts, replacements, dict(case, lines=ranges))
            validate_scenes(timeline, case)
            return timeline

        def validate(reply):
            if not isinstance(reply["patches"], list) or not reply["reason"]:
                raise ValueError("需要补丁列表和取舍")
            proposed = apply(reply["patches"])
            known = timeline_frames(before)
            failures = [
                issue
                for issue in timeline_findings(case, proposed, blocks, brief)
                if issue["kind"]
                in ("replayed_picture", "blocked_speaker", "other_mouth_moving")
                and any(
                    (issue["kind"], issue["line"], n) not in known
                    for n in range(issue["first"], issue["end"])
                )
            ]
            if failures:
                for issue in failures:
                    key, n, _ = frame_at(proposed, issue["first"])
                    issue.update(clip=key, source_first=n)
                raise ValueError(
                    "候选引入已确认吃喝遮挡或同场重播。可以改原先占用素材的相邻停顿，以移动动作；"
                    "提交前避开下面的成片和源帧冲突 " + encoded(failures)
                )

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
    if selected != "draft":
        from .dialogue_mouth_edit import read
        winning = read(out / (selected + "_render.json"))["cuts"]
        changed = [n for n in range(case["total"]) if frame_at(cuts, n) != frame_at(winning, n)]
        from itertools import groupby
        intervals = []
        for _, group in groupby(enumerate(changed), key=lambda item: item[1] - item[0]):
            seq = [n for _, n in group]
            intervals.append({"at": seq[0], "end": seq[-1] + 1})
        if intervals:
            answer = choose_exported_edit(case, out, film, out / (selected + ".mp4"), intervals, ask)
            if answer["pick"] == "A":
                selected, result, reason = "draft", before, answer["why"]
    accepted = (
        not result["timeline"]
        and not result["story"]["issues"]
        and not result["story"]["unknown"]
        and result["score"]["passed"] == result["score"]["total"]
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
        return "保留首稿，候选增加了重复源帧、口部遮挡或另一演员动嘴"
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
    if after["score"]["confirmed_frames"] > before["score"]["confirmed_frames"]:
        prior = {l["id"]: set(l["confirmed_at"]) for l in before["score"]["lines"]}
        if all(
            prior[l["id"]] <= set(l["confirmed_at"]) for l in after["score"]["lines"]
        ):
            return None
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
