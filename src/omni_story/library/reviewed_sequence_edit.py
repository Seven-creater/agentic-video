"""Render a dialogue film directly from a GLM-reviewed sequence and measured character speech."""

import argparse
import json
import math
import os
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np

from .audio_render import SAMPLE_RATE, _wav
from .character_voice import CharacterVoices
from .media import sha256_file
from .opencode_provider import OpenCodeMCP
from .pipeline import _material_call
from .reference_dialogue import review_exchange_speakers
from .selected_edit import (
    FPS,
    incoming_voice_guides,
    insert_role_guides,
    missed_incoming_voices,
    missing_insert_roles,
    read,
    render,
    timed_script_visibility,
)
from .server_cli import _home, credential, settings
from .state import LibraryState, json_sha, write_json

SUBTITLE_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,40,&H00FFFFFF,&H00FFFFFF,&H00181818,&H70000000,-1,0,0,0,100,100,0,0,1,2,0,2,40,40,37,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_subtitles(out, lines):
    def stamp(s):
        cs = round(s * 100)
        sec, cs = divmod(cs, 100)
        mins, sec = divmod(sec, 60)
        hr, mins = divmod(mins, 60)
        return f"{hr}:{mins:02}:{sec:02}.{cs:02}"

    header = SUBTITLE_HEADER
    (out / "final_subtitled.ass").write_text(
        header
        + "".join(
            "Dialogue: 0,"
            + stamp(l["start_s"])
            + ","
            + stamp(l["end_s"])
            + ",Default,,0,0,0,,"
            + l["text"].replace("{", "｛").replace("}", "｝").replace("\\", "＼")
            + "\n"
            for l in lines
        ),
        encoding="utf-8-sig",
    )


def edit(root, reference, *, reviewed_cache=None, speaker_mouth=False):
    creative = Path(read(root / "fast_run.json")["creative_output"])
    b = creative / "autonomous_material/browser"
    plan = read(b / "scene_pick.json")["plan"]
    out = creative / "evaluations" / ("dialogue_mouth_trial" if speaker_mouth else "reviewed_sequence_edit")
    out.mkdir(parents=True, exist_ok=True)
    if sha256_file(reference) != read(creative / "original_reference_contract.json")["reference_sha256"]:
        raise ValueError("Reference hash does not match the saved run")
    units = [u for s in plan["scene_sequence"] for u in s.get("picture_units", [s])]
    if not units or not all(u.get("performance_review", {}).get("ok") for u in units):
        raise ValueError("A passed visual review is required for every selected unit")
    reviews = [r for r in b.glob("sequence_review_*/review.json") if not read(r).get("problems")]

    def exact_review(path):
        report = read(path)
        if [item["reference"] for item in report["scenes"]] != [u["reference"] for u in units]:
            return False
        allowed = {
            (
                u["reference"],
                v["window_id"],
                round(v["source_start_s"], 3),
                round(v["source_end_s"], 3),
            )
            for u in units
            for v in u["usable_segments"]
        }
        actual = {
            (
                v["reference"],
                v["window_id"],
                round(v["source_start_s"], 3),
                round(v["source_end_s"], 3),
            )
            for v in report["clips"].values()
        }
        return bool(actual) and actual == allowed

    review = next(
        (r for r in sorted(reviews, key=lambda p: p.stat().st_mtime, reverse=True) if exact_review(r)),
        None,
    )
    if review is None:
        raise ValueError("No passed review matches the actual selected intervals")
    comparison = review.parent / "sequence.mp4"
    segments = read(review)["clips"]
    source_hash = {s["window_id"]: s["source_sha256"] for u in units for s in u["usable_segments"]}
    catalog = {
        item["sha256"]: item["path"] for item in read(creative / "catalog/inventory.json")["sources"]
    }
    shots = {}
    sources = {}
    for key, s in segments.items():
        wid = s["window_id"]
        digest = source_hash[wid]
        shots[key] = dict(s, source_sha256=digest)
        sources[digest] = str(catalog[digest])
    for digest, path in sources.items():
        if sha256_file(path) != digest:
            raise ValueError("Reviewed footage changed: " + path)
    anchor = read(creative / "library_state.json")["artifacts"]["reference_turns_verified"]
    timed = {l["line_id"]: l for l in read(Path(anchor[-1]["path"]))["lines"]}
    voices = read(creative / "autonomous_material/character_voices/cast_voices.json")
    script = read(creative / "dialogue_script.json")["lines"]
    lock = read(creative / "material_lock.json")
    voice_folder = Path(read(root / "fast_run.json")["preview_output"]) / "character_voices"
    home = _home()
    server = settings(home)
    os.environ["Z_AI_API_KEY"] = credential(home)
    os.environ["DASHSCOPE_API_KEY"] = read(home / "credentials.json")["DASHSCOPE_API_KEY"]
    src = read(creative / "original_reference_contract.json")
    ref_source = {"path": str(reference), "sha256": src["reference_sha256"]}
    roster = read(Path(anchor[-1]["path"]))["characters"]
    evidence = read(creative / "library_state.json")["artifacts"].get("reference_turn_window_evidence")
    if evidence:
        artifact = evidence[-1]
        stored = read(Path(artifact["path"]))
        observed = {
            row["line_id"]: stored[str(row["line_id"])]
            for row in timed.values()
            if str(row["line_id"]) in stored
        }
        # Earlier ledgers hashed numeric keys before JSON converted them to strings.
        if len(observed) != len(stored) or artifact["sha256"] not in {
            json_sha(stored),
            json_sha(observed),
        }:
            raise ValueError("Reference dialogue evidence changed")
    else:
        cache = out if reviewed_cache is None else reviewed_cache.resolve(strict=True)
        if reviewed_cache is not None:
            if cache.parent != (creative / "evaluations").resolve():
                raise ValueError("The dialogue cache must belong to this run")
            previous = read(cache / "reference_review/library_state.json")
            if not previous["calls"] or any(
                c["status"] in {"submitted", "uncertain"} for c in previous["calls"]
            ):
                raise ValueError("The previous dialogue check has no settled outcome")
        state0 = LibraryState(
            cache / "reference_review",
            {
                "configuration": read(creative / "library_state.json")["input_lock"]["configuration"],
                "reference_sha256": ref_source["sha256"],
                "mode": "line_windows",
            },
            max_requests=None,
        )
        glm0 = OpenCodeMCP(
            state0, package_root=server["mcp_package_root"], executable=server["opencode_executable"]
        )

        def read_window(name, prompt, validate, media, *, image=False):
            return _material_call(state0, glm0, name, prompt, validate, media, image=image)

        observed = review_exchange_speakers(ref_source, list(timed.values()), roster, cache, read_window)
    write_json(out / "turn_evidence.json", observed)
    updates = []
    for row in script:
        proof = [observed.get(i, {}) for i in row["reference_line_ids"]]
        identified = {p["speaker"] for p in proof if p.get("speaker")}
        if len(identified) == 1 and all(p.get("speaker") for p in proof):
            role = next(iter(identified))
            listeners = list(dict.fromkeys(t for p in proof for t in p["listeners"] if t != role))
            updates.append(
                {
                    "line_id": row["line_id"],
                    "previous": row["role"],
                    "observed": role,
                    "reference_ids": row["reference_line_ids"],
                }
            )
            row.update(role=role, addressee_roles=listeners, on_screen=True)
    write_json(out / "script_role_evidence.json", {"changes": updates, "script": script})
    service = CharacterVoices(out / "character_voices")
    service.reuse_folders = [voice_folder]
    if reviewed_cache is not None:
        service.reuse_folders.append(reviewed_cache / "character_voices")
    lines = []
    clock = 0
    bounds = {}
    pieces = []
    last_ref = 0
    audit = []
    for src in script:
        sid = src["scene_id"]
        role = src["role"]
        if role not in voices:
            raise ValueError("Missing verified voice for " + role)
        refids = src["reference_line_ids"]
        target = timed[refids[0]]["start_s"]
        gapframes = round(max(0, target - last_ref) * FPS)
        if sid not in bounds:
            bounds[sid] = [clock, clock]
        if gapframes:
            pieces.append(np.zeros((gapframes * (SAMPLE_RATE // FPS), 2), dtype=np.float32))
            clock += gapframes
        raw = dict(src)
        sample = service.render_line(raw, voices[role])
        speech_frames = math.ceil(len(sample) * FPS / SAMPLE_RATE)
        framesamples = speech_frames * (SAMPLE_RATE // FPS)
        pieces.append(np.pad(sample, ((0, framesamples - len(sample)), (0, 0))))
        lines.append(dict(src, start_s=clock / FPS, end_s=clock / FPS + len(sample) / SAMPLE_RATE))
        audit.append(
            {
                "line_id": src["line_id"],
                "voice": voices[role],
                "text": src["text"],
                "audio_sha256": sha256_file(raw["tts_audio_path"]),
                "seconds": raw["voiced_s"],
            }
        )
        clock += speech_frames
        bounds[sid][1] = clock
        last_ref = timed[refids[-1]]["end_s"]
    ref_end = max(s["end_s"] for s in lock["scenes"])
    pad = round(max(0, ref_end - last_ref) * FPS)
    if pad:
        pieces.append(np.zeros((pad * (SAMPLE_RATE // FPS), 2), dtype=np.float32))
    total = clock + pad
    bounds[script[-1]["scene_id"]][1] = total
    master = out / "dubbed_master.wav"
    _wav(master, np.vstack(pieces))
    audio_media = out / "dubbed_master.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(master),
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(audio_media),
        ],
        check=True,
    )
    write_json(out / "used_speech.json", audit)
    order = {u["reference"]: i for i, u in enumerate(units)}
    skill = Path(__file__).parent / "resources/edit-selected-footage/SKILL.md"
    footage = {k: {"unit": r["reference"], "duration": r["duration"]} for k, r in segments.items()}
    scene_keys = defaultdict(list)
    actors = {}
    for parent in plan["scene_sequence"]:
        sid = parent["reference"]
        actors[sid] = plan["cast_hints"]
        for unit in parent.get("picture_units", [parent]):
            report = unit["performance_review"]
            for key in report["clips"]:
                shots[key]["visible"] = sorted(
                    {p["actor"] for p in report["people"] if p["clip"] == key}
                )
                shots[key]["scene"] = sid
                scene_keys[sid].append(key)
    guides = insert_role_guides(plan["scene_sequence"], shots, scene_keys, actors)
    spoken = timed_script_visibility(lines, script, lock["scenes"])
    incoming = incoming_voice_guides(plan["scene_sequence"], shots, scene_keys, actors, spoken)

    def validate(v):
        cuts = v["cuts"]
        seen = set()
        prior = -1
        issues = []
        if not cuts or cuts[0]["at"] != 0:
            raise ValueError("成片从0秒开始")
        for i, c in enumerate(cuts):
            key = c["shot"]
            if key not in shots:
                raise ValueError("需要S标签 " + str(sorted(shots)))
            s = shots[key]
            c["scene"] = s["scene"]
            t = round(c["at"] * FPS)
            end = round(cuts[i + 1]["at"] * FPS) if i + 1 < len(cuts) else total
            idx = order[s["reference"]]
            seen.add(idx)
            lo, hi = bounds[c["scene"]]
            if t < lo or end > hi:
                issues.append(
                    f"第{i + 1}刀跨越已核对的参考声轨场界：{c['scene']}只允许成片第{lo}到{hi}帧"
                )
            if not (0 <= t < end <= total and idx >= prior):
                issues.append(f"第{i + 1}刀的顺序或时间错误")
            if not (0 <= c["offset"] < s["duration"] - 1 / FPS and 0.75 <= c["speed"] <= 1.2):
                issues.append(f"第{i + 1}刀的素材入点或速度越界")
            if not (0 <= c["center"] <= 1 and c["size"] in ("full", "medium", "close")):
                issues.append(f"第{i + 1}刀的景别或构图无效")
            if (end - t) / FPS - (s["duration"] - c["offset"]) / c["speed"] > 0.35:
                issues.append(f"第{i + 1}刀 {key} 余下的素材不够长，请换刀或提前切")
            c["tail"] = "hold"
            prior = idx
        if seen != set(order.values()):
            issues.append("须按序包括全部已核验画面段")
        missing = missing_insert_roles(guides, cuts, shots, total)
        if missing:
            issues.append("插镜的实际留用秒数缺少人物：" + json.dumps(missing, ensure_ascii=False))
        late = missed_incoming_voices(incoming, cuts)
        if late:
            issues.append("新同伴开始当面说话时要看清他：" + json.dumps(late, ensure_ascii=False))
        if issues:
            raise ValueError("；".join(issues))
        return v

    # Lock only the measurable audio boundaries. The model chooses every picture and its share within them.
    blocks = []
    for parent in plan["scene_sequence"]:
        sid = parent["reference"]
        lo, hi = bounds[sid]
        group = []
        for unit in parent.get("picture_units", [parent]):
            anchors = [
                round(g["at"] * FPS)
                for g in incoming
                if g["scene"] == sid
                and g["role"] in unit.get("roles", [])
                and unit.get("kind") == "exchange"
            ]
            if group and anchors and lo < min(anchors) < hi:
                blocks.append({"scene": sid, "first": lo, "last": min(anchors), "units": group})
                lo = min(anchors)
                group = []
            group.append(unit["reference"])
        blocks.append({"scene": sid, "first": lo, "last": hi, "units": group})
    for b in blocks:
        b["usable"] = [k for k, r in segments.items() if r["reference"] in b["units"]]
        b["first_shot_options"] = next(
            (
                [c["shot"] for c in g["choices"]]
                for g in incoming
                if round(g["at"] * FPS) == b["first"] and g["scene"] == b["scene"]
            ),
            b["usable"],
        )
    if speaker_mouth:
        from .dialogue_mouth_bundle import prepare
        from .dialogue_mouth_edit import run_speaker_trial
        write_subtitles(out, lines)
        bundle = prepare(creative, out, reference, review, plan, shots, sources, lines, audio_media,
                         out / "final_subtitled.ass", total, blocks)
        return run_speaker_trial(bundle, out)
    state = LibraryState(
        out,
        {
            "configuration": read(creative / "library_state.json")["input_lock"]["configuration"],
            "material": sha256_file(creative / "autonomous_material/browser/scene_pick.json"),
            "audio": sha256_file(audio_media),
            "mode": "dubbed_screening_measured_blocks_v1",
        },
        max_requests=None,
        registry_path=out.parent / "sequence_edit_runs.json",
    )
    glm = OpenCodeMCP(
        state,
        package_root=server["mcp_package_root"],
        executable=server["opencode_executable"],
    )

    def ask(name, prompt, validate, media):
        f = out / (name + ".json")
        v = _material_call(
            state,
            glm,
            "material_scene_reviewed_sequence_" + name,
            prompt,
            validate,
            str(media),
        )
        write_json(f, v)
        return v

    forms = [
        {
            "block": i + 1,
            "usable": b["usable"],
            "first_shot_options": b["first_shot_options"],
            "seconds": (b["last"] - b["first"]) / FPS,
            "units": b["units"],
        }
        for i, b in enumerate(blocks)
    ]

    def from_blocks(v):
        chosen = v.get("blocks")
        if not isinstance(chosen, list) or len(chosen) != len(blocks):
            raise ValueError("按顺序逐区块提供所有结果：" + str(forms))
        cuts = []
        for i, (b, assembly) in enumerate(zip(blocks, chosen)):
            takes = assembly.get("cuts", [])
            if (
                not takes
                or takes[0].get("shot") not in b["first_shot_options"]
                or any(t.get("shot") not in b["usable"] for t in takes)
            ):
                raise ValueError(
                    "区块只能使用本块镜头，区块首镜遵守first_shot_options：" + str(forms[i])
                )
            if list(dict.fromkeys(segments[t["shot"]]["reference"] for t in takes)) != b["units"]:
                raise ValueError("按次序保留每个画面段：" + str(forms[i]))
            capacities = []
            weights = []
            for t in takes:
                key = t["shot"]
                offset = t.get("offset", 0)
                rate = t.get("speed", 1)
                w = t.get("weight")
                if (
                    not all(type(v) in (int, float) and math.isfinite(v) for v in (offset, rate, w))
                    or not 0 <= offset < segments[key]["duration"]
                    or not 0.75 <= rate <= 1.2
                    or w <= 0
                ):
                    raise ValueError("offset、speed、weight须在允许范围")
                capacities.append(math.floor((segments[key]["duration"] - offset) / rate * FPS))
                weights.append(w)
            n = b["last"] - b["first"]
            if sum(capacities) < n or min(capacities) < 1 or len(takes) > n:
                raise ValueError(
                    "这组画面的总可播时间不够，增加另一个镜头或调整入点和速度。区块 " + str(forms[i])
                )
            # Weighted fair allocation in integer frames, respecting the actual playable tail of each take.
            count = [1] * len(takes)
            for frame in range(n - len(takes)):
                k = max(
                    (j for j, cap in enumerate(capacities) if count[j] < cap),
                    key=lambda j: weights[j] / (count[j] + 1),
                )
                count[k] += 1
            at = b["first"]
            for take, frames in zip(takes, count):
                cuts.append(
                    dict(
                        take,
                        at=at / FPS,
                        center=take.get("center", 0.5),
                        size=take.get("size", "full"),
                        offset=take.get("offset", 0),
                        speed=take.get("speed", 1),
                    )
                )
                at += frames
        return validate({"blocks": chosen, "cuts": cuts})

    block_prompt = (
        "独立剪一条动画角色配音改编片，lines表中的台词和起止秒数是已经实录的配音。看视频，对照REF与S标签，按表依次给每个区块选画面、顺序、入点和相对播放时长。"
        "每个区块只能用表内usable的镜头，第一刀从本块first_shot_options中挑。不要把镜头跨块调换。"
        "首句问话和接话按实录lines中的role辨认谁先开口。镜头优先保留说话者及耳朵、头饰等识别特征完整可见。按核验结果保留每段所需角色和画面顺序。如有换人，按配音第一次开口及画内外状态安排交接。"
        "只填权重weight控制节奏（越大占时越多），程序按音频实际边界分配帧，不用自己算全片绝对秒数。"
        "offset默认0，speed默认1。如不足，可在本块多选一刀；size为full/medium/close，center为0到1。"
        '返回 {"blocks":[{"cuts":[{"shot":"本块的S标签","weight":2,"offset":0,"speed":1,"size":"full","center":0.5}]}]}。'
        + "只返回JSON。"
        + json.dumps(
            {
                "blocks": forms,
                "shots": {k: dict(**footage[k], visible=shots[k]["visible"]) for k in shots},
                "roles": plan["cast_hints"],
                "lines": lines,
                "insert_roles": guides,
            },
            ensure_ascii=False,
        )
        + "\n"
        + skill.read_text().split("需要对照真实切点")[0]
    )
    v = from_blocks(ask("timeline", block_prompt, from_blocks, comparison))
    write_json(out / "resolved_timeline.json", v)

    write_subtitles(out, lines)
    final = render(out, out, {}, str(audio_media), shots, sources, v, total, "role_dub")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"],
        check=True,
    )
    write_json(
        out / "cut_result.json",
        {
            "video": str(final),
            "sha256": sha256_file(final),
            "frames": total,
            "cuts": len(v["cuts"]),
            "lines": len(lines),
            "decision": sha256_file(out / "timeline.json"),
            "audio": "received_character_dub",
            "audio_sources": audit,
            "review": str(review),
            "turns": observed,
            "script_roles": updates,
        },
    )
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run",
        type=Path,
        help="A completed selection with a locked dialogue script and voice cast",
    )
    parser.add_argument("reference", type=Path, help="Original reference video belonging to the run")
    parser.add_argument(
        "--reviewed-cache",
        type=Path,
        help="Reuse a settled reference-dialogue review and verified speech from this run",
    )
    parser.add_argument("--speaker-mouth", action="store_true", help="Run the active-speaker editing experiment on the locked dialogue and footage")
    args = parser.parse_args()
    print(edit(args.run, args.reference, reviewed_cache=args.reviewed_cache, speaker_mouth=args.speaker_mouth))


if __name__ == "__main__":
    main()
