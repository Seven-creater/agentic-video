"""Render a GLM cut from observed drawings, the original subtitle timing and locked audio."""

import argparse
import json
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from native_poses import frame_crop, frame_sequence, openings
from PIL import Image

from omni_story.library.media import sha256_file
from omni_story.library.selected_edit import FPS, concat, ff, read
from omni_story.library.state import write_json


def decode(source, start, end, width, height):
    data = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(start / FPS),
            "-i",
            str(source),
            "-an",
            "-vf",
            f"fps={FPS},scale={width}:{height}",
            "-frames:v",
            str(end - start),
            "-threads",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, height, width, 3)


def compose(picture, crop, boxes):
    im = Image.fromarray(picture)
    width, height = im.size
    for left, top, right, bottom in boxes:
        if not 0 <= left < right <= 1 or not 0 <= top < bottom <= 1:
            raise ValueError("Invalid source subtitle rectangle")
        box = (
            int(left * width),
            int(top * height),
            min(width, int(np.ceil(right * width))),
            min(height, int(np.ceil(bottom * height))),
        )
        pixels = im.crop(box)
        blocks = pixels.resize(
            (max(1, pixels.width // 18), max(1, pixels.height // 18)),
            Image.Resampling.BOX,
        )
        mosaic = blocks.resize(pixels.size, Image.Resampling.NEAREST)
        im.paste(Image.blend(pixels, mosaic, 0.92), box)
    left, top, span = crop
    view = (
        round(left * width),
        round(top * height),
        round((left + span) * width),
        round((top + span) * height),
    )
    return im.crop(view).resize((1280, 720), Image.Resampling.LANCZOS)


def encode_cut(path, pictures):
    args = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        "1280x720",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-an",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-crf",
        "16",
        "-preset",
        "veryfast",
        "-threads",
        "2",
        str(path),
    ]
    with subprocess.Popen(args, stdin=subprocess.PIPE) as proc:
        try:
            for picture in pictures:
                proc.stdin.write(picture)
        finally:
            proc.stdin.close()
        if proc.wait():
            raise ValueError("Could not encode cut")


def stream_hash(path):
    return (
        subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
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


def locked_take_options(case, lines, lock):
    """Limit each line to visible performances inside that scene's locked source shots."""
    scenes = {s["scene_id"]: s for s in lock["scenes"]}
    options = {}
    for line in lines:
        scene = scenes[line["scene_id"]]
        actor = scene["role_bindings"][line["role"]]["role_id"]
        if case["role_cast"].get(line["role"]) != actor:
            raise ValueError("Manifest actor differs from the material lock: " + line["role"])
        allowed = []
        for key, take in case["takes"].items():
            first, end = take["clip_bounds"]
            if take["actor"] == actor and any(
                shot["source_sha256"] == case["assets"]["source"]["sha256"]
                and actor in shot["visible"]
                and shot["source_start_s"] * FPS - 1e-7 <= first < end <= shot["source_end_s"] * FPS + 1e-7
                for shot in scene["coverage_shots"]
            ):
                allowed.append(key)
        if not allowed:
            raise ValueError("No performance inside the material lock for " + line["line_id"])
        options[line["line_id"]] = allowed
    return options


def choose(case, run, out):
    """Give the server editor actual unmarked frames, the cast and the locked lines."""
    from PIL import ImageDraw

    from omni_story.library.dialogue_mouth_edit import ask

    source = Path(case["assets"]["source"]["path"])
    roles = case["role_cast"]
    result_path = Path(case["assets"]["result"]["path"]).resolve(strict=True)
    if not result_path.is_relative_to(run.resolve(strict=True)):
        raise ValueError("The locked result must belong to this creative run")
    clock = read(result_path)
    options = locked_take_options(case, clock["lines"], read(result_path.parent / "material_lock.json"))
    usable = {key for keys in options.values() for key in keys}
    performance = {key: take for key, take in case["takes"].items() if key in usable}
    board = Image.new("RGB", (640, 201 * len(performance)), "white")
    pen = ImageDraw.Draw(board)
    for i, (key, take) in enumerate(performance.items()):
        pen.text(
            (2, 201 * i + 2), f"{key}   {take['actor']}   CLOSED / OPEN", fill="black"
        )
        first, last = take["clip_bounds"]
        poses = decode(source, first, last, 320, 180)
        for j, state in enumerate(("closed", "open")):
            board.paste(
                Image.fromarray(poses[take[state] - first]), (320 * j, i * 201 + 17)
            )
    photo = out / "performances.jpg"
    board.save(photo, quality=92)
    script = [
        {k: l[k] for k in ("line_id", "role", "text", "start_s", "end_s")}
        for l in clock["lines"]
    ]
    prompt = (
        "根据下面已定稿的配音和图中的原画，选每句话的表演。每一行是一段候选表演的闭口和张口实帧。"
        "只有正在说话的演员可承接这句；对手接话才反打。注意上句与下句情绪、环境和角度能否衔接。"
        "镜头数量、顺序和时长占比自定，景别选full、medium或close。"
        '返回 {"lines":[{"id":"句号","shots":[{"take":"编号","share":1,"framing":"medium"}]}],"reason":"剪辑取舍"}。'
        + json.dumps({"cast": roles, "lines": script, "allowed_takes_per_line": options}, ensure_ascii=False)
    )

    def validate(reply):
        if [l["id"] for l in reply["lines"]] != [l["line_id"] for l in script]:
            raise ValueError("Return each original line once in order")
        for line, choice in zip(script, reply["lines"]):
            if not choice["shots"]:
                raise ValueError("Each line needs a picture")
            for shot in choice["shots"]:
                if (
                    shot["take"] not in options[line["line_id"]]
                    or shot["framing"] not in {"full", "medium", "close"}
                    or not 0 < float(shot["share"]) < float("inf")
                ):
                    raise ValueError(
                        "Choose a take of the current speaker and a positive duration"
                    )
        return reply

    result = ask(
        {"configuration_path": str(run / "library_state.json")},
        out,
        "choose_native_performances",
        prompt,
        photo,
        validate,
    )
    cast = {
        role: {"takes": [key for key, t in performance.items() if t["actor"] == actor]}
        for role, actor in roles.items()
    }
    return {**result, "cast": cast}


def render(manifest, out, *, choose_for_run=None):
    case = read(manifest)
    out.mkdir(parents=True, exist_ok=True)
    paths = {name: Path(entry["path"]) for name, entry in case["assets"].items()}
    for name, path in paths.items():
        if sha256_file(path) != case["assets"][name]["sha256"]:
            raise ValueError(f"Locked input changed: {name}")
    if choose_for_run is not None:
        case["decision"] = choose(case, choose_for_run, out)
        write_json(out / "selected_manifest.json", case)
    clock = read(paths["result"])
    movie = Path(clock["final_video"])
    if sha256_file(movie) != clock["final_sha256"]:
        raise ValueError("Original film or audio master changed")
    voice = np.frombuffer(
        subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(paths["voice"]),
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
    lines = clock["lines"]
    plan = case["decision"]["lines"]
    if not lines or [x["line_id"] for x in lines] != [x["id"] for x in plan]:
        raise ValueError("Edit must cover every locked line in order")
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_streams",
                "-of",
                "json",
                str(movie),
            ]
        )
    )["streams"][0]
    if info["r_frame_rate"] != f"{FPS}/1" or round(lines[0]["start_s"] * FPS) != 0:
        raise ValueError("Expected a 30 fps dialogue opening on frame zero")
    total = int(info["nb_frames"])
    masks = case["caption_boxes"]
    source_hash = case["assets"]["source"]["sha256"]

    @lru_cache(maxsize=1)
    def samples(start, end):
        return decode(paths["source"], start, end, 640, 360)

    cuts = []
    files = []
    for i, (line, decision) in enumerate(zip(lines, plan)):
        at = round(line["start_s"] * FPS)
        end = round(lines[i + 1]["start_s"] * FPS) if i + 1 < len(lines) else total
        start = at
        shots = decision["shots"]
        if not shots or any(float(s["share"]) <= 0 for s in shots):
            raise ValueError("Each line needs positive shot durations")
        weight = sum(s["share"] for s in shots)
        for j, shot in enumerate(shots):
            stop = at + round(
                (end - at) * sum(s["share"] for s in shots[: j + 1]) / weight
            )
            take = case["takes"][shot["take"]]
            if take["actor"] != case["role_cast"][line["role"]] or stop <= start:
                raise ValueError("Cut changes the locked actor or has no duration")
            origin, limit = take["clip_bounds"]
            a, b = take["witness_bounds"]
            closed, opened = take["closed"], take["open"]
            if (
                not 0
                <= origin
                <= a
                <= min(closed, opened)
                < max(closed, opened)
                < b
                <= limit
            ):
                raise ValueError(
                    "Mouth evidence falls outside the selected performance"
                )
            arr = samples(origin, limit)
            target = openings(voice, start, stop - start)
            path, shapes = frame_sequence(
                arr[a - origin : b - origin],
                closed - a,
                opened - a,
                take["mouth"],
                target,
            )
            absolute = [a + n for n in path]
            if shot["framing"] not in {"full", "medium", "close"}:
                raise ValueError("Unknown framing")
            crop = frame_crop(take["mouth"], shot["framing"])
            needed = sorted(set(absolute))
            # Begin at the original sample origin to retain identical fps rounding at the source.
            native = decode(paths["source"], origin, needed[-1] + 1, 1280, 720)
            frames = {
                n: compose(native[n - origin], crop, masks[str(n)]).tobytes()
                for n in needed
            }
            del native
            dest = out / f"cut_{len(cuts):03}.mp4"
            encode_cut(dest, (frames[n] for n in absolute))
            del frames
            files.append(dest)
            cuts.append(
                {
                    "line": line["line_id"],
                    "role": line["role"],
                    "actor": take["actor"],
                    "take": shot["take"],
                    "scene": take["scene"],
                    "clip": take["clip"],
                    "framing": shot["framing"],
                    "crop": [*crop[:2], crop[2], crop[2]],
                    "at": start,
                    "end": stop,
                    "absolute_source_frames": absolute,
                    "observed_closed": closed,
                    "observed_open": opened,
                    "source_openness": shapes,
                    "target_open": target.astype(int).tolist(),
                    "source_digest": source_hash,
                }
            )
            start = stop
    picture = concat(out, files, "assembled")
    dest = out / "full.mp4"
    ff(
        "-i",
        picture,
        "-i",
        movie,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        "ass='" + str(paths["subtitles"]) + "'",
        "-c:v",
        "libx264",
        "-threads",
        2,
        "-preset",
        "veryfast",
        "-crf",
        17,
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        dest,
    )
    count = int(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_frames",
                "-of",
                "csv=p=0",
                str(dest),
            ]
        )
    )
    audio = stream_hash(dest)
    if count != total or audio != stream_hash(movie):
        raise ValueError("Exported duration or original audio changed")
    ff("-xerror", "-i", dest, "-f", "null", "-")
    write_json(
        out / "receipt.json",
        {
            "movie": str(dest),
            "film_sha": sha256_file(dest),
            "frames": count,
            "fps": FPS,
            "source": str(paths["source"]),
            "source_sha": source_hash,
            "audio": audio,
            "same_audio": True,
            "old_sub": case["assets"]["subtitles"]["sha256"],
            "lines": len(lines),
            "cuts": cuts,
        },
    )
    return dest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--choose-for-run",
        type=Path,
        help="Have GLM select takes for the locked cast in this creative run",
    )
    args = parser.parse_args()
    print(
        render(args.manifest, args.output, choose_for_run=args.choose_for_run),
        flush=True,
    )
