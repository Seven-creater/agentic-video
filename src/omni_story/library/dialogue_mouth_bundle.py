"""Prepare the speaker-edit experiment from the selected media and the locked voice master."""

import math
import shutil
import subprocess
from collections import defaultdict

import numpy as np

from .dialogue_finish import verified_masks
from .media import sha256_file
from .selected_edit import FPS, concat, ff, mask_source_matches, read
from .state import write_json


def voice_frames(samples, at, end):
    # The input is isolated speech with silence. Count 1/30-second windows above the noise floor.
    width = 48000 // FPS
    portion = samples[at * width : end * width]
    if end <= at or not len(portion):
        return []
    signal = (
        np.pad(portion, (0, (end - at) * width - len(portion)))
        .reshape(-1, width)
        .astype(float)
    )
    rms = np.sqrt(np.mean(signal**2, axis=1))
    return (np.flatnonzero(rms >= max(55.0, float(rms.max()) * 0.035)) + at).tolist()


def prepare(
    creative,
    out,
    reference,
    review_path,
    plan,
    shots,
    sources,
    lines,
    master,
    subs,
    total,
    blocks,
):
    folder = out / "bundle"
    folder.mkdir(exist_ok=True)
    audio = folder / "dub.m4a"
    shutil.copyfile(master, audio)
    shutil.copyfile(subs, folder / "subs.ass")
    samples = np.frombuffer(
        subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                audio,
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-f",
                "s16le",
                "-",
            ]
        ),
        dtype="<i2",
    )
    cast = plan["cast_hints"]
    review = read(review_path)
    actors = defaultdict(list)
    for scene in review["scenes"]:
        for person in scene["people"]:
            if (
                person.get("evidence")
                and person["evidence"] not in actors[person["actor"]]
            ):
                actors[person["actor"]].append(person["evidence"])
    clock = []
    for line in lines:
        at = round(line["start_s"] * FPS)
        end = min(total, math.ceil(line["end_s"] * FPS - 1e-7))
        clock.append(
            {
                "id": line["line_id"],
                "actor": cast[line["role"]],
                "listeners": [
                    cast[role]
                    for role in line.get("addressee_roles", [])
                    if role in cast
                ],
                "text": line["text"],
                "at": at,
                "end": end,
                "on_screen": line.get("on_screen", True),
                "voice_frames": voice_frames(samples, at, end),
            }
        )
    clips = {}
    by_scene = defaultdict(list)
    browser = creative / "autonomous_material/browser"
    previews = []

    def preview(name, args, duration, has_audio):
        dest = folder / (name + "_preview.mp4")
        vf = (
            f"scale=768:432:force_original_aspect_ratio=decrease,pad=768:432:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={FPS},pad=iw:ih+32:0:32,drawtext=text='{name}   frame %{{n}}':x=8:y=4:fontsize=20:fontcolor=white"
        )
        sound = [] if has_audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        ff(
            *args,
            *sound,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0" if has_audio else "1:a:0",
            "-vf",
            vf,
            "-t",
            duration,
            "-threads",
            2,
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            24,
            "-c:a",
            "aac",
            "-ar",
            48000,
            "-ac",
            2,
            dest,
        )
        previews.append(dest)

    from .render import _probe

    ref_info = _probe(str(reference), "ffprobe")
    preview(
        "REF",
        ["-i", reference],
        ref_info["format"]["duration"],
        any(s["codec_type"] == "audio" for s in ref_info["streams"]),
    )
    # The dub gets its own screen. The editing model never receives a previous cut's pictures.
    dub_preview = folder / "wave.mp4"
    ff(
        "-i",
        audio,
        "-filter_complex",
        f"[0:a]showwaves=s=768x432:rate={FPS}:mode=line,setsar=1[v]",
        "-map",
        "[v]",
        "-map",
        "0:a",
        "-threads",
        2,
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-c:a",
        "copy",
        dub_preview,
    )
    preview("DUB", ["-i", dub_preview], total / FPS, True)
    for i, (label, shot) in enumerate(shots.items(), 1):
        key = f"M{i:02}"
        wid = shot["window_id"]
        digest = shot["source_sha256"]
        original = sources[digest]
        bound = read(browser / (wid + "_media.json"))
        observed = read(browser / (wid + "_visual.json"))
        a = math.ceil(shot["source_start_s"] * FPS - 1e-7) / FPS
        count = math.floor((shot["source_end_s"] - a) * FPS + 1e-7)
        row = {
            "window_id": wid,
            "source_id": bound["proxy"]["source_id"],
            "source_in_s": a,
            "source_out_s": a + count / FPS,
        }
        if (
            count < 1
            or not mask_source_matches(bound, digest)
            or not verified_masks([row], {wid: bound}, {wid: observed})
        ):
            raise ValueError(
                "Selected range or verified subtitle treatments unavailable " + label
            )
        video = folder / (key + ".mp4")
        stream = next(
            s
            for s in _probe(original, "ffprobe")["streams"]
            if s["codec_type"] == "video"
        )
        scale = min(1280 / stream["width"], 720 / stream["height"])
        w = min(1280, max(2, round(stream["width"] * scale / 2) * 2))
        h = min(720, max(2, round(stream["height"] * scale / 2) * 2))
        x = (1280 - w) // 2
        y = (720 - h) // 2
        ff(
            "-ss",
            a,
            "-i",
            original,
            "-an",
            "-vf",
            f"fps={FPS},scale={w}:{h},pad=1280:720:{x}:{y},setsar=1",
            "-frames:v",
            count,
            "-threads",
            2,
            "-c:v",
            "libx264",
            "-crf",
            15,
            video,
        )
        masks = [
            dict(
                m,
                start_s=m["start_s"] - a,
                end_s=m["end_s"] - a,
                x=(x + m["x"] * w) / 1280,
                y=(y + m["y"] * h) / 720,
                w=m["w"] * w / 1280,
                h=m["h"] * h / 720,
            )
            for m in row["source_subtitle_treatments"]
        ]
        clips[key] = {
            "path": video.name,
            "frames": count,
            "sha256": sha256_file(video),
            "group": shot["reference"],
            "source": label,
            "visible": shot["visible"],
            "source_sha256": digest,
            "source_start_s": a,
            "masks": masks,
        }
        by_scene[shot["reference"]].append(key)
        preview(key, ["-i", video], count / FPS, False)
    reel = concat(folder, previews, "inputs")
    if reel.stat().st_size >= 7_850_000:
        smaller = folder / "inputs_compact.mp4"
        ff(
            "-i",
            reel,
            "-c:v",
            "libx264",
            "-crf",
            30,
            "-threads",
            2,
            "-c:a",
            "copy",
            smaller,
        )
        reel = smaller
    groups = []
    for i, keys in enumerate(by_scene.values()):
        marked = [folder / (k + "_preview.mp4") for k in keys]
        group = concat(folder, marked, "scan_" + str(i))
        groups.append({"clips": keys, "media": group.name})
    context = [
        {
            "scene": s["reference"],
            "cast": [(p["role"], p["actor"]) for p in s["people"]],
        }
        for s in review["scenes"]
    ]
    files = [audio, folder / "subs.ass", reel, *[folder / g["media"] for g in groups]]
    by_label = {v["source"]: k for k, v in clips.items()}
    case = {
        "blocks": [
            {
                "at": b["first"],
                "end": b["last"],
                "clips": [by_label[k] for k in b["usable"]],
            }
            for b in blocks
        ],
        "fps": FPS,
        "total": total,
        "width": 1280,
        "height": 720,
        "media": reel.name,
        "dub": audio.name,
        "subs": "subs.ass",
        "clips": clips,
        "groups": groups,
        "actors": dict(actors),
        "context": "参考片依次出场的人物 " + str(context),
        "lines": clock,
        "configuration_path": str(creative / "library_state.json"),
        "hashes": {p.name: sha256_file(p) for p in files},
    }
    write_json(folder / "bundle.json", case)
    return folder
