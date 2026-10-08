"""CPU-only, immutable parent manifests for a later model-owned slot fine cut.

Preparation grants no model-call or render permission and never changes the run
ledger. Historical plans and observed windows remain fallible source evidence.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .media import probe_media
from .slot_finecut_file_proofs import verified_sha256 as sha256_file
from .state import json_sha

SCHEMA = "slot_finecut_preparation_v1"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _path(value, output):
    value = Path(value)
    return (value if value.is_absolute() else output / value).resolve(strict=True)


def _number(value, name, *, positive=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 or (positive and value == 0)):
        raise ValueError("slot_finecut_invalid_number:" + name)
    return float(value)


def _close(actual, expected, name, tolerance=1e-6):
    if not math.isclose(actual, expected, abs_tol=tolerance, rel_tol=0):
        raise ValueError("slot_finecut_mismatch:" + name)


def _timeline(compiled, result, sources, windows, output):
    """Retain actual rendered timing; holds are separate from moving source time."""
    rows = compiled["segments"]
    plan = compiled["plan"]
    if not rows or rows != result["provenance"] or len(rows) != len(plan["segments"]):
        raise ValueError("slot_finecut_provenance_mismatch")
    fps = _number(compiled["fps"], "fps", positive=True)
    _close(fps, _number(result["fps"], "result_fps", positive=True), "fps")
    cursor, frame_count, mappings = 0.0, 0, []
    for index, (row, segment) in enumerate(zip(rows, plan["segments"], strict=True)):
        source, window = sources[row["source_id"]], windows[row["window_id"]]
        if row["segment_index"] != index:
            raise ValueError("slot_finecut_segment_order_mismatch")
        for key in ("source_id", "window_id", "source_in_s", "source_out_s", "speed"):
            if row[key] != segment[key]:
                raise ValueError("slot_finecut_plan_provenance_mismatch:" + key)
        if (row["source_sha256"] != source["sha256"]
                or window["source_sha256"] != source["sha256"]
                or window["source_id"] != row["source_id"]
                or _path(row["source_path"], output) != _path(source["path"], output)):
            raise ValueError("slot_finecut_source_binding_mismatch")
        source_in = _number(row["source_in_s"], "source_in_s")
        source_out = _number(row["source_out_s"], "source_out_s", positive=True)
        speed = _number(row["speed"], "speed", positive=True)
        if not (window["source_start_s"] <= source_in < source_out
                <= window["source_end_s"] <= source["duration_s"]):
            raise ValueError("slot_finecut_source_range_outside_watched_window")
        start = _number(row["output_in_s"], "output_in_s")
        end = _number(row["output_out_s"], "output_out_s", positive=True)
        duration = _number(row["duration_s"], "duration_s", positive=True)
        _close(start, cursor, "output_timeline_gap_or_overlap")
        _close(end - start, duration, "segment_duration")
        frames = row["frames"]
        if type(frames) is not int or frames < 1:
            raise ValueError("slot_finecut_invalid_frames")
        _close(duration, frames / fps, "segment_frames")
        hold_frames = row.get("freeze_frames", 0)
        if type(hold_frames) is not int or not 0 <= hold_frames < frames:
            raise ValueError("slot_finecut_invalid_hold_frames")
        motion_frames = row.get("motion_frames", frames - hold_frames)
        if type(motion_frames) is not int or motion_frames + hold_frames != frames:
            raise ValueError("slot_finecut_motion_hold_frames_mismatch")
        motion_duration, hold_duration = motion_frames / fps, hold_frames / fps
        _close(row.get("motion_duration_s", motion_duration), motion_duration, "motion_duration")
        _close(row.get("freeze_tail_s", 0), hold_duration, "hold_duration")
        _close(motion_duration, (source_out - source_in) / speed, "retiming", 1 / fps + 1e-6)
        freeze = row.get("freeze_source")
        if hold_frames and (not isinstance(freeze, dict)
                or freeze.get("source_sha256") != source["sha256"]
                or freeze.get("source_in_s") != source_in
                or freeze.get("source_out_s") != source_out
                or freeze.get("selection") != "last_output_frame_of_trimmed_source_range_before_hold"):
            raise ValueError("slot_finecut_unbound_tail_hold")
        mappings.append({
            "segment_id": segment["segment_id"], "slot_id": segment["slot_id"],
            "source_id": row["source_id"], "window_id": row["window_id"],
            "source_sha256": source["sha256"], "source_in_s": source_in,
            "source_out_s": source_out, "speed": speed,
            "role_ids": list(segment.get("role_ids", [])),
            "output_in_s": start, "output_out_s": end,
            "motion_output_in_s": start, "motion_output_out_s": start + motion_duration,
            "hold_output_in_s": start + motion_duration if hold_frames else None,
            "hold_output_out_s": end if hold_frames else None,
            "hold_source": freeze if hold_frames else None,
        })
        cursor, frame_count = end, frame_count + frames
    _close(cursor, _number(compiled["duration_s"], "compiled_duration", positive=True), "total_duration")
    _close(cursor, _number(result["duration_s"], "result_duration", positive=True), "result_duration")
    if compiled["total_frames"] != frame_count:
        raise ValueError("slot_finecut_total_frames_mismatch")
    return mappings


def load_baselines(output, baseline_rounds=(0, 3)):
    """Read and verify existing movies only; never generate footage or a plan."""
    output = Path(output).resolve(strict=True)
    rounds = tuple(baseline_rounds)
    if (not rounds or any(type(item) is not int or item < 0 for item in rounds)
            or len(set(rounds)) != len(rounds)):
        raise ValueError("slot_finecut_invalid_baseline_rounds")
    state = _read(output / "library_state.json")
    locked_sources = {row["source_id"]: row["sha256"] for row in state["input_lock"]["library_sources"]}
    protected = {}

    def protect(path, expected=None):
        path = _path(path, output)
        key = str(path)
        if key not in protected:
            before = path.stat()
            digest = sha256_file(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("slot_finecut_file_changed_while_reading:" + key)
            protected[key] = {"path": key, "sha256": digest, "size_bytes": after.st_size}
        if expected is not None and protected[key]["sha256"] != expected:
            raise ValueError("slot_finecut_file_sha_mismatch:" + key)
        return path

    catalog_path = protect(output / "catalog" / "inventory.json")
    catalog = _read(catalog_path)
    sources = {row["source_id"]: row for row in catalog["sources"]}
    if len(sources) != len(catalog["sources"]) or set(sources) != set(locked_sources):
        raise ValueError("slot_finecut_catalog_input_lock_mismatch")
    for source in sources.values():
        source_path = protect(source["path"], locked_sources[source["source_id"]])
        if source["sha256"] != locked_sources[source["source_id"]]:
            raise ValueError("slot_finecut_catalog_source_sha_mismatch")
        _close(probe_media(source_path)["duration_s"], source["duration_s"], "source_duration", .1)
    watched_path = protect(output / "watched_windows.json")
    watched = _read(watched_path)
    windows = {row["window_id"]: row for row in watched if row.get("status") == "watched"}
    if len(windows) != len(watched) or not windows:
        raise ValueError("slot_finecut_requires_completed_watched_windows")
    for window in windows.values():
        source = sources[window["source_id"]]
        if (window["source_sha256"] != source["sha256"]
                or window.get("kind") != "continuous_window"
                or not 0 <= window["source_start_s"] < window["source_end_s"] <= source["duration_s"]
                or not isinstance(window.get("observation"), dict)
                or window["observation"].get("window_id") != window["window_id"]):
            raise ValueError("slot_finecut_watched_fact_binding_mismatch")
        protect(window["path"], window["sha256"])
    ref_catalog_path = protect(output / "reference_catalog" / "inventory.json")
    ref_sources = _read(ref_catalog_path)["sources"]
    if len(ref_sources) != 1 or ref_sources[0]["sha256"] != state["input_lock"]["reference_sha256"]:
        raise ValueError("slot_finecut_reference_input_lock_mismatch")
    reference = ref_sources[0]
    ref_path = protect(reference["path"], reference["sha256"])
    ref_metadata = probe_media(ref_path)
    ref_duration = ref_metadata["duration_s"]
    _close(ref_duration, reference["duration_s"], "reference_duration", .1)
    ref_audio_index = reference.get("audio_stream_index")
    if ref_audio_index is not None and (type(ref_audio_index) is not int or not any(
            row.get("index") == ref_audio_index and row.get("codec_type") == "audio"
            for row in ref_metadata["streams"])):
        raise ValueError("slot_finecut_reference_audio_stream_mismatch")
    reading_path = protect(output / "reference_reading.json")
    reading = _read(reading_path)
    if reading["reference_sha256"] != reference["sha256"]:
        raise ValueError("slot_finecut_reference_reading_sha_mismatch")
    methods_path = next((path for path in (
        output / "artifacts" / "editing_revision_v1" / "reference_methods.json",
        output / "editing_reference_v2.json") if path.is_file()), None)
    methods, methods_sha = None, None
    if methods_path is not None:
        methods_path = protect(methods_path)
        methods = _read(methods_path)
        methods_sha = protected[str(methods_path)]["sha256"]
        if methods.get("reference_sha256") != reference["sha256"]:
            raise ValueError("slot_finecut_reference_methods_sha_mismatch")
    parents = []
    plan_paths = list(output.glob("plan_*.json")) + list((output / "artifacts").rglob("plan.json"))
    for round_no in rounds:
        render_dir = output / f"render_{round_no}"
        result_path = protect(render_dir / "render_result.json")
        input_path = protect(render_dir / "render_input.json")
        result, frozen = _read(result_path), _read(input_path)
        compiled = frozen["compiled"]
        if (result["input_identity"] != json_sha(frozen)
                or compiled["plan"]["reference_sha256"] != reference["sha256"]
                or frozen["sources"] != result["sources"]):
            raise ValueError("slot_finecut_render_input_identity_mismatch")
        for source_id, row in frozen["sources"].items():
            source = sources[source_id]
            if row["sha256"] != source["sha256"] or _path(row["path"], output) != _path(source["path"], output):
                raise ValueError("slot_finecut_render_source_catalog_mismatch")
        movie = protect(result["rendered_path"], result["sha256"])
        if movie != (render_dir / "final.mp4").resolve(strict=True):
            raise ValueError("slot_finecut_parent_path_outside_expected_render")
        mapping = _timeline(compiled, result, sources, windows, output)
        duration = probe_media(movie)["duration_s"]
        _close(duration, result["duration_s"], "measured_parent_duration", 1 / compiled["fps"] + .01)
        _close(duration, result["measured_duration_s"], "recorded_measured_duration", .01)
        originals = [str(protect(path)) for path in plan_paths if _read(path) == compiled["plan"]]
        if not originals:
            raise ValueError("slot_finecut_original_model_plan_missing")
        calls = []
        for call in state.get("calls", []):
            parsed = output / "calls" / call["id"] / "parsed.json"
            if call.get("status") == "received" and parsed.is_file() and _read(parsed) == compiled["plan"]:
                evidence = {"call_id": call["id"]}
                for name in ("parsed", "request", "response"):
                    expected = call.get(name + "_sha256") if name != "parsed" else None
                    proof_path = protect(parsed.with_name(name + ".json"))
                    if expected is not None and json_sha(_read(proof_path)) != expected:
                        raise ValueError("slot_finecut_original_call_json_sha_mismatch")
                    evidence[name + "_path"] = str(proof_path)
                calls.append(evidence)
        parents.append({
            "baseline_id": f"render_{round_no}", "round": round_no, "sha256": result["sha256"],
            "path": str(movie), "duration_s": duration,
            "provenance": [{**row, "role_ids": list(segment.get("role_ids", []))}
                           for row, segment in zip(result["provenance"], compiled["plan"]["segments"], strict=True)],
            "output_mapping": mapping, "original_plan": compiled["plan"], "render_input": frozen,
            "original_plan_sources": originals, "original_model_calls": calls,
            "allowed_windows": watched, "render_result_path": str(result_path),
            "render_input_path": str(input_path),
        })
    return {
        "schema_version": SCHEMA, "output": str(output), "task_id": state["task_id"],
        "input_lock_sha256": json_sha(state["input_lock"]), "baseline_rounds": list(rounds),
        "reference": {"path": str(ref_path), "sha256": reference["sha256"],
                      "duration_s": ref_duration, "audio_stream_index": ref_audio_index,
                      "cached_reading": reading, "cached_methods": methods,
                      "cache_provenance": {"reading_path": str(reading_path),
                                           "inventory_path": str(ref_catalog_path),
                                           "methods_path": str(methods_path) if methods_path else None,
                                           "methods_sha256": methods_sha}},
        "parents": parents, "protected_files": sorted(protected.values(), key=lambda row: row["path"]),
        "execution_authorized": False,
        "limits": ["Preparation is CPU-only and does not resume the paused Goal.",
                   "Cached model facts and old plans are not independent quality verdicts."],
    }


def load_preparation(path):
    """Reject altered parents/evidence; the active call ledger itself may evolve."""
    path = Path(path).resolve(strict=True)
    record = _read(path)
    identity = record.pop("preparation_id", None)
    if (record.get("schema_version") != SCHEMA
            or identity != "slot_finecut_preparation_" + json_sha(record)
            or path.name != identity + ".json"):
        raise ValueError("slot_finecut_preparation_identity_mismatch")
    expected_path = Path(record["output"]).resolve(strict=True) / "artifacts" / SCHEMA / path.name
    if path != expected_path:
        raise ValueError("slot_finecut_preparation_outside_original_run")
    current = load_baselines(record["output"], record["baseline_rounds"])
    if current != record:
        raise ValueError("slot_finecut_preparation_evidence_changed")
    return {**record, "preparation_id": identity, "preparation_path": str(path)}


def prepare(output, baseline_rounds=(0, 3)):
    """Append one content-addressed same-run preparation, then reuse it exactly."""
    output = Path(output).resolve(strict=True)
    rounds = list(baseline_rounds)
    if (not rounds or any(type(item) is not int or item < 0 for item in rounds)
            or len(set(rounds)) != len(rounds)):
        raise ValueError("slot_finecut_invalid_baseline_rounds")
    directory = output / "artifacts" / SCHEMA
    for path in sorted(directory.glob("slot_finecut_preparation_*.json")):
        if _read(path).get("baseline_rounds") == rounds:
            return load_preparation(path)
    record = load_baselines(output, rounds)
    identity = "slot_finecut_preparation_" + json_sha(record)
    path = directory / (identity + ".json")
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves an existing preparation even under a race.
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({**record, "preparation_id": identity}, stream, ensure_ascii=False, indent=2, allow_nan=False)
    except FileExistsError:
        return load_preparation(path)
    return {**record, "preparation_id": identity, "preparation_path": str(path)}


def delivery_paths(result_or_path):
    """Include copyable absolute paths and folders alongside later media previews."""
    if isinstance(result_or_path, (str, Path)):
        path = Path(result_or_path).resolve(strict=True)
    else:
        raw = next((result_or_path[key] for key in ("path", "rendered_path", "final_video", "video_path")
                    if result_or_path.get(key)), None)
        if raw is None:
            raise ValueError("slot_finecut_delivery_video_path_missing")
        path = Path(raw).resolve(strict=True)
    return f"完整视频路径：{path}\n所在文件夹：{path.parent}"
