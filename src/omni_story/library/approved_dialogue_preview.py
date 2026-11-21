"""Deliver one reviewed dialogue workflow for both new drafts and recuts."""
import argparse
from pathlib import Path
import runpy
import fcntl

from .media import sha256_file
from .selected_edit import read
from .state import write_json, LibraryState
from .reviewed_sequence_edit import edit


def reference_path(creative):
    expected = read(creative / "original_reference_contract.json")["reference_sha256"]
    for parent in (creative, *creative.parents):
        plan = parent / "plan.json"
        if plan.is_file():
            job = read(plan)
            if job.get("reference_sha256") == expected and job.get("reference"):
                reference = Path(job["reference"])
                if sha256_file(reference) != expected:
                    raise ValueError("Original reference changed")
                return reference
    raise ValueError("Could not locate the locked reference in the launch plans")


def deliver(creative, out, *, reuse_only=False, previous=None):
    script = read(creative / "dialogue_script.json")
    reference = reference_path(creative)
    # Silent and voice-over-only films have no visible dialogue to verify.
    if not any(row.get("on_screen", True) for row in script["lines"]):
        runpy.run_module("omni_story.library.scheduled_preview", run_name="__main__")
        return
    config = read(creative / "library_state.json")["input_lock"]["configuration"]
    inputs = {p: sha256_file(creative / p) for p in ("material_lock.json", "dialogue_script.json",
              "watched_windows.json", "autonomous_material/character_voices/cast_voices.json")}
    lock = dict(configuration=config, draft=inputs, reference=sha256_file(reference),
                workflow="verified_dialogue", output=str(out))
    LibraryState(out, lock, registry_path=out.parent / "approved_dialogue_jobs.json", max_requests=None)
    work = out
    voices = [creative / "evaluations/fast_preview/character_voices"]
    if previous:
        info = read(previous)
        if any(c["status"] not in ("received", "failed_known") for c in info["calls"]):
            raise ValueError("Previous request outcome unresolved")
        if info["input_lock"].get("workflow") == "verified_dialogue":
            prior = previous.parent.resolve(strict=True)
            if prior.parent != out.parent or info["input_lock"]["draft"] != inputs:
                raise ValueError("Previous edit belongs to a different locked draft")
            work = Path(read(prior / "edit_work.json")["work"])
            if work.resolve().parent != out.parent:
                raise ValueError("Previous edit workspace belongs to another draft")
        voices.insert(0, previous.parent / "character_voices")
    write_json(out / "edit_work.json", dict(work=str(work)))
    with (work / ".approved_edit.lock").open("a") as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = edit(None, reference, creative=creative, destination=work, speaker_mouth=True,
                      reuse_only=reuse_only, voice_folders=voices)
        evidence = read(work / "speaker/outcome.json")
        film = Path(evidence["video"])
        render = read(work / "speaker" / (evidence["selected"] + "_render.json"))
        if sha256_file(film) != render["video_sha256"]:
            raise ValueError("Exported film and review do not match")
        case = read(work / "bundle/bundle.json")
        lines = [dict(row, line_id=row["id"], start_s=row["at"]/case["fps"],
                      end_s=row["end"]/case["fps"]) for row in case["lines"]]
        write_json(out / "dialogue_result.json", dict(
            final_video=str(film), final_sha256=render["video_sha256"], artifact_ready=True,
            content_delivery_ready=evidence["accepted"], quality_status="passed" if evidence["accepted"] else "needs_review",
            audio_lines=lines, score=evidence["score"], review=str(work / "speaker/outcome.json")))
        write_json(out / "preview_progress.json", dict(stage="completed", result=str(result)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("creative", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--reuse-speech-only", action="store_true")
    p.add_argument("--resume-ledger", type=Path)
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        deliver(args.creative.resolve(), out, reuse_only=args.reuse_speech_only, previous=args.resume_ledger)
    except Exception as exc:
        write_json(out / "preview_failure.json", {"error": f"{type(exc).__name__}: {exc}"})
        raise
