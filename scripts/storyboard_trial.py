#!/usr/bin/env python3
"""Independent storyboard trials; no pipeline state and no HTTP replay."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import subprocess
import urllib.error

from omni_story.library import question_first as qfs
from omni_story.library.storyboard import (
    StoryboardViolation, asr_rows, storyboard_prompt, storyboard_repair_prompt, validate_storyboard,
)


TRANSCODE_ABOVE_BYTES = 7_500_000
MAX_SEND_BYTES = 8 * 1024 ** 2


def build_request(prompt, video_bytes, key, *, mime="video/mp4"):
    content = [dict(type="video_url", video_url=dict(
        url=f"data:{mime};base64," + base64.b64encode(video_bytes).decode("ascii"))),
        dict(type="text", text=qfs.redact(prompt, key))]
    return dict(endpoint=qfs.ENDPOINT, headers={"Authorization": "Bearer " + key,
        "Content-Type": "application/json", "X-Title": "4.5V MCP Local"},
        payload=dict(model="glm-5.3-flash", thinking=dict(type="disabled"), temperature=.2,
                     max_tokens=8192, messages=[dict(role="user", content=content)]))


def request_record(request, key):
    def strip_media(value):
        if isinstance(value, dict):
            return {k: strip_media(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip_media(v) for v in value]
        if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
            prefix, encoded = value.split(";base64,", 1)
            media = base64.b64decode(encoded, validate=True)
            return dict(mime=prefix[5:], sha256=hashlib.sha256(media).hexdigest(), size_bytes=len(media))
        return value
    return qfs.redact(strip_media(request), key)


def validate_output_dir(jobs, out):
    jobs, out = [Path(job).resolve() for job in jobs], Path(out).resolve()
    if len({job.name for job in jobs}) != len(jobs):
        raise ValueError("任务目录名必须互不相同，避免输出覆盖。")
    for target in [out, *(out / job.name for job in jobs)]:
        target = target.resolve()
        if any(target == job or job in target.parents for job in jobs):
            raise ValueError("--out 及每个任务的输出目录不得位于任何 job 目录内。")
    return jobs, out


def prepare_video(path, folder):
    if path.stat().st_size <= TRANSCODE_ABOVE_BYTES:
        return path
    compressed = folder / "reference_480p.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a?", "-vf", "scale=-2:480", "-c:v", "libx264",
        "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(compressed)],
        check=True, capture_output=True, timeout=900)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type", "-of", "json", str(compressed)],
        check=True, capture_output=True, timeout=120)
    metadata = json.loads(probe.stdout)
    duration = float(metadata["format"]["duration"])
    if (not math.isfinite(duration) or duration <= 0 or not any(
            row.get("codec_type") == "video" for row in metadata["streams"])):
        raise ValueError("转码结果不是有效视频。")
    if compressed.stat().st_size > MAX_SEND_BYTES:
        raise ValueError("480p/crf30 转码后仍超过 8 MB，停止该任务，不发送。")
    return compressed


def storyboard_markdown(storyboard, segments, *, failure=None):
    def text(value):
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")
    lines = ["# 参考分镜故事", "", "| 格 | 时间 | 人物 | 动作 | 台词意思 | 对应ASR原文 | 作用 | 核心 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    originals = {row["id"]: row["text"] for row in asr_rows(segments)} if storyboard else {}
    for cell in storyboard["storyboard"] if storyboard else []:
        line = cell["line"]
        meaning = f"{line['speaker'] or '未知'}：{line['intent']}" if line else "—"
        original = "；".join(f"{sid}：{originals[sid]}" for sid in line["ref_asr_ids"]) if line else "—"
        values = [cell["id"], f"{cell['start_s']:.2f}–{cell['end_s']:.2f}s", "、".join(cell["roles"]),
                  cell["action"], meaning, original, cell["story_role"], "是" if cell["core"] else "否"]
        lines.append("| " + " | ".join(text(value) for value in values) + " |")
    if storyboard:
        lines += ["", f"节奏：{text(storyboard['pace'])}", f"对话轮次：{storyboard['dialogue_turns']}"]
        if storyboard["ignorable_asr"]:
            lines += ["", "可忽略 ASR：" + "；".join(
                text(f"{row['id']}（{', '.join(row['ignorable_reasons'])}）：{row['text']}")
                for row in storyboard["ignorable_asr"])]
    else:
        lines += ["", f"本任务未产出通过校验的分镜：{text(failure)}。详见 failure.json。"]
    return "\n".join(lines) + "\n"


def run_job(job, folder, max_repairs, key):
    folder.mkdir(parents=True, exist_ok=False)
    summary = dict(job=job.name, duration=None, asr_lines=None, cells=None, core_cells=None,
                   dialogue_turns=None, attempts=0, status="failed")
    segments, failure = [], None

    def save(name, value):
        (folder / name).write_text(json.dumps(qfs.redact(value, key), ensure_ascii=False,
                                            indent=2, allow_nan=False) + "\n", encoding="utf-8")

    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        reading = read(job / "reference_reading.json")
        segments = read(job / "reference_asr.json")["segments"]
        source = read(job / "reference_catalog" / "inventory.json")["sources"][0]
        duration = source["duration_s"]
        initial_prompt = storyboard_prompt(reading, segments, duration)
        summary.update(duration=duration, asr_lines=len(segments))
        if not key.strip():
            raise StoryboardViolation("trial:missing_api_key", dict(repair_hint="设置环境变量 Z_AI_API_KEY 后使用新的输出目录。"))
        video = Path(source["path"])
        if not video.is_absolute():
            video = job / video
        video = prepare_video(video, folder)
        video_bytes = video.read_bytes()
        mime = mimetypes.guess_type(video.name)[0] or "video/mp4"
        prompt = initial_prompt
        for attempt in range(1, max_repairs + 2):
            request = build_request(prompt, video_bytes, key, mime=mime)
            save(f"request_{attempt}.json", request_record(request, key))
            summary["attempts"] = attempt
            try:
                response = qfs.redact(qfs.http_post(request["endpoint"], request["headers"], request["payload"]), key)
            except Exception as error:
                # Exception bodies and headers may echo credentials. Never record them.
                failure = dict(code="transport_error", error_type=type(error).__name__,
                               outcome_unknown=True, automatic_resend=False)
                if isinstance(error, urllib.error.HTTPError):
                    failure["http_status"] = error.code
                save(f"response_{attempt}.json", failure)
                break
            save(f"response_{attempt}.json", response)
            previous_reply = ""
            try:
                try:
                    previous_reply = response["choices"][0]["message"]["content"]
                    data = qfs.parse_json_object(previous_reply)
                except (ValueError, KeyError, IndexError, TypeError):
                    raise StoryboardViolation("storyboard:invalid_json", dict(
                        repair_hint="返回含 storyboard、dialogue_turns、pace 的完整 JSON 对象。")) from None
                storyboard = validate_storyboard(data, duration_s=duration, asr_segments=segments)
            except StoryboardViolation as violation:
                failure = dict(code=violation.code, feedback=violation.feedback)
                if attempt <= max_repairs:
                    prompt = initial_prompt + f"\n修复轮次：{attempt}/{max_repairs}\n" + storyboard_repair_prompt(previous_reply, violation)
                    continue
                break
            save("storyboard.json", storyboard)
            (folder / "storyboard.md").write_text(qfs.redact(storyboard_markdown(storyboard, segments), key), encoding="utf-8")
            summary.update(cells=len(storyboard["storyboard"]), core_cells=sum(cell["core"] for cell in storyboard["storyboard"]),
                           dialogue_turns=storyboard["dialogue_turns"], status="ok")
            return summary
    except Exception as error:
        failure = dict(code="trial_error", error_type=type(error).__name__)
        if isinstance(error, StoryboardViolation):
            failure.update(code=error.code, feedback=error.feedback)
    save("failure.json", dict(failure or {}, attempts=summary["attempts"]))
    (folder / "storyboard.md").write_text(qfs.redact(storyboard_markdown(None, [],
        failure=failure["code"]), key), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立参考分镜试跑；仅校验失败才请求修复，网络错误绝不重发。")
    parser.add_argument("jobs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-repairs", type=int, default=2)
    args = parser.parse_args(argv)
    if args.max_repairs < 0:
        parser.error("--max-repairs 不能小于 0。")
    try:
        jobs, out = validate_output_dir(args.jobs, args.out)
        if any((out / job.name).exists() for job in jobs):
            raise ValueError("任务输出目录已存在；请使用新的 --out，避免覆盖或重发历史请求。")
    except ValueError as error:
        parser.error(str(error))
    out.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("Z_AI_API_KEY", "")
    with ThreadPoolExecutor(max_workers=min(5, len(jobs))) as pool:
        summaries = list(pool.map(lambda job: run_job(job, out / job.name, args.max_repairs, key), jobs))
    (out / "summary.json").write_text(json.dumps(qfs.redact(summaries, key), ensure_ascii=False,
        indent=2, allow_nan=False) + "\n", encoding="utf-8")
    columns = ("job", "duration", "asr_lines", "cells", "core_cells", "dialogue_turns", "attempts", "status")
    print(" | ".join(columns))
    for row in summaries:
        print(qfs.redact(" | ".join(str(row[column]) for column in columns), key))
    return 0 if all(row["status"] == "ok" for row in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
