"""Render a frozen two-line selection and verify it against separate source-track labels."""

import argparse
import json
import tempfile
import wave
from pathlib import Path

from omni_story.library.dialogue_mouth_edit import render, score_output
from omni_story.library.media import sha256_file
from omni_story.library.reviewed_sequence_edit import write_subtitles
from omni_story.library.selected_edit import ff
from omni_story.library.state import write_json


def read(path):
    return json.loads(path.read_text())


def run(pilot, selection, tracks, blind_labels, root):
    data = read(pilot)
    fps = data["fps"]
    root.mkdir(parents=True, exist_ok=True)
    ids = read(selection)["choices"]
    by_id = {c["id"]: c for c in data["clips"]}
    crops = [0, 0, 1, 1]
    lines, cuts, clips = [], [], {}
    pcm = bytearray()
    at = 0
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        bundle = Path(tmp)
        if len(ids) != len(data["lines"]):
            raise ValueError("Missing line selection")
        for key, line in zip(ids, data["lines"]):
            clip = by_id[key]
            media = pilot.parent / "media" / clip["file"]
            if (
                line["id"] != clip["line"]
                or sha256_file(media) != clip["sha256"]
                or clip["frames"] != line["frames"]
            ):
                raise ValueError("Source does not match the fixed line")
            (bundle / clip["file"]).symlink_to(media.resolve())
            with wave.open(str(pilot.parent / "media" / line["wav"]), "rb") as audio:
                if (
                    audio.getframerate(),
                    audio.getnchannels(),
                    audio.getsampwidth(),
                ) != (48000, 1, 2):
                    raise ValueError("Pilot requires mono 48 kHz PCM16")
                sound = audio.readframes(audio.getnframes())
            pcm.extend(sound.ljust(line["frames"] * (48000 // fps) * 2, b"\0"))
            end = at + line["frames"]
            lines.append(
                dict(
                    line,
                    at=at,
                    end=end,
                    start_s=at / fps,
                    end_s=end / fps,
                    voice_frames=[at + n for n in line["voice_frames"]],
                )
            )
            cuts.append(dict(clip=key, at=at, **{"in": 0}, crop=crops))
            # The source has burned-in dialogue near the bottom. Use the same region in every arm.
            mask = {
                "x": 0.18,
                "y": 0.80,
                "w": 0.64,
                "h": 0.17,
                "start_s": 0,
                "end_s": line["frames"] / fps,
                "opacity": 0.90,
                "block_px": 15,
            }
            clips[key] = {
                "path": clip["file"],
                "masks": [mask],
                "frames": clip["frames"],
            }
            at = end
        with wave.open(str(bundle / "dub.wav"), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        ff("-i", bundle / "dub.wav", "-c:a", "aac", "-b:a", "160k", bundle / "dub.m4a")
        write_subtitles(bundle, lines)
        case = {
            "fps": fps,
            "width": 960,
            "height": 540,
            "total": at,
            "dub": "dub.m4a",
            "subs": "final_subtitled.ass",
            "clips": clips,
            "lines": lines,
        }
        gold = read(blind_labels)
        observed = {key: {"method": "contour_cycle", "slots": []} for key in ids}
        for t in read(tracks):
            key = t["clip"]
            actor = gold[t["id"]]
            if key not in observed or actor in ("other", "uncertain"):
                continue
            ns = list(range(t["start"], t["end"]))
            pts = [t["centers"][str(n)] for n in ns]
            observed[key]["slots"].append(
                {
                    "actor": actor,
                    "start": ns[0],
                    "end": ns[-1] + 1,
                    "track_frames": ns,
                    "center": [[p[0] / 640, p[1] / 360] for p in pts],
                    "widths": [p[2] / 640 for p in pts],
                }
            )
        film = render(case, bundle, root, cuts, "pair")
        score = score_output(case, bundle, root, film, cuts, None, observed, "pair")
        write_json(root / "score.json", score)
        print("scored", score["confirmed_frames"], score["voiced_frames"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("pilot", "selection", "tracks", "blind_labels", "output"):
        parser.add_argument(arg, type=Path)
    a = parser.parse_args()
    run(a.pilot, a.selection, a.tracks, a.blind_labels, a.output)
