"""Optional local CPU ASR; text evidence retains original movie timestamps."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .media import _cache_folder, _run, _write_json, sha256_file, validate_window, verify_source

ASR_POLICY = "library-asr-v1"
TIME_VALIDATION_POLICY = "asr-time-validation-v2"


def _raw_segment(segment) -> dict:
    """Keep the recognizer's complete segment and word values before validation."""
    result = {field: getattr(segment, field) for field in
              ("id", "seek", "start", "end", "text", "tokens", "avg_logprob", "compression_ratio",
               "no_speech_prob", "temperature") if hasattr(segment, field)}
    result["words"] = [{"start": float(word.start), "end": float(word.end), "word": word.word,
                        "probability": float(word.probability)} for word in segment.words or []]
    return result


def _bounded_interval(raw_start: float, raw_end: float, duration: float) -> tuple[float, float]:
    """Allow recorded boundary rounding, but emit only usable in-window intervals."""
    if not all(math.isfinite(value) for value in (raw_start, raw_end)):
        raise ValueError("asr_timestamp_not_finite")
    if raw_start < -0.05 or raw_end > duration + 0.25 or raw_end <= raw_start:
        raise ValueError("asr_timestamp_out_of_window")
    bounded_start, bounded_end = max(0.0, raw_start), min(duration, raw_end)
    if bounded_end <= bounded_start:
        raise ValueError("asr_timestamp_has_no_in_window_duration")
    return bounded_start, bounded_end


def _validate_cached_times(result: dict, start: float, end: float) -> None:
    """Validate existing evidence without rewriting a completed transcript."""
    if result["source_start_s"] != start or result["source_end_s"] != end or result["source_offset_s"] != start:
        raise ValueError("asr_cache_window_mismatch")
    duration = end - start
    for segment in result["segments"]:
        local_start, local_end = float(segment["local_start_s"]), float(segment["local_end_s"])
        source_start, source_end = float(segment["source_start_s"]), float(segment["source_end_s"])
        if not all(math.isfinite(t) for t in (local_start, local_end, source_start, source_end)):
            raise ValueError("asr_cache_timestamp_not_finite")
        for field in ("raw_asr_local_start_s", "raw_asr_local_end_s"):
            if field in segment and not math.isfinite(float(segment[field])):
                raise ValueError("asr_cache_raw_timestamp_not_finite")
        if local_start < 0 or local_end > duration or local_end <= local_start:
            raise ValueError("asr_cache_timestamp_out_of_window")
        if abs(source_start - start - local_start) > 1e-6 or abs(source_end - start - local_end) > 1e-6:
            raise ValueError("asr_cache_source_time_mapping_invalid")
        for word in segment.get("words", []):
            for field in ("raw_asr_local_start_s", "raw_asr_local_end_s"):
                if field in word and not math.isfinite(float(word[field])):
                    raise ValueError("asr_cache_raw_word_timestamp_not_finite")
            if word.get("timing_usable") is False:
                if word.get("alignment_issue") not in {"zero_duration", "clipped_to_empty"}:
                    raise ValueError("asr_cache_unknown_word_alignment_issue")
                if "source_start_s" in word or "source_end_s" in word:
                    raise ValueError("asr_cache_unusable_word_has_source_times")
                raw_start, raw_end = float(word["raw_asr_local_start_s"]), float(word["raw_asr_local_end_s"])
                if min(raw_start, raw_end) < -0.05 or max(raw_start, raw_end) > duration + 0.25 or raw_end < raw_start:
                    raise ValueError("asr_cache_unusable_word_out_of_window")
                if raw_start < local_start - 0.05 or raw_end > local_end + 0.05:
                    raise ValueError("asr_cache_unusable_word_out_of_segment")
                continue
            word_start, word_end = float(word["source_start_s"]), float(word["source_end_s"])
            if not all(math.isfinite(t) for t in (word_start, word_end)):
                raise ValueError("asr_cache_word_timestamp_not_finite")
            if word_start < source_start - 1e-6 or word_end > source_end + 1e-6 or word_end <= word_start:
                raise ValueError("asr_cache_word_timestamp_out_of_segment")


def load_asr_model(model_size: str = "small", *, device: str = "cpu", compute_type: str = "int8"):
    from faster_whisper import WhisperModel
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe_window(source: dict, start_s: float, end_s: float, cache_dir: str | Path, *,
                      model_size: str = "small", language: str | None = None, model=None) -> dict:
    """Transcribe selected audio using VAD; never interpret speech as visible identity."""
    path = verify_source(source)
    start, end = validate_window(source, start_s, end_s)
    audio_index = source.get("audio_stream_index")
    if audio_index is None:
        raise ValueError("asr_source_has_no_selected_audio")
    spec = {"policy": ASR_POLICY, "source_sha256": source["sha256"], "source_start_s": start,
            "source_end_s": end, "audio_stream_index": audio_index, "model_size": model_size,
            "language": language, "device": "cpu", "compute_type": "int8", "vad_filter": True}
    folder = _cache_folder(cache_dir, spec)
    output = folder / "transcript.json"
    if output.is_file():
        saved = json.loads(output.read_text(encoding="utf-8"))
        if saved.get("spec") != spec:
            raise ValueError("asr_cache_spec_mismatch")
        if sha256_file(saved["audio_path"]) != saved["audio_sha256"]:
            raise ValueError("asr_audio_cache_modified")
        if "raw_response_path" in saved and sha256_file(saved["raw_response_path"]) != saved["raw_response_sha256"]:
            raise ValueError("asr_raw_cache_modified")
        _validate_cached_times(saved, start, end)
        return saved
    audio = folder / "audio.wav"
    partial = folder / "audio.part.wav"
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}", "-i", str(path),
          "-t", f"{end - start:.6f}", "-map", f"0:{audio_index}", "-vn", "-ac", "1", "-ar", "16000",
          "-c:a", "pcm_s16le", str(partial)])
    partial.replace(audio)
    active_model = model if model is not None else load_asr_model(model_size)
    rows, info = active_model.transcribe(str(audio), language=language, vad_filter=True,
                                         word_timestamps=True, beam_size=5)
    rows = list(rows)
    raw_path = folder / f"raw_asr_{len(list(folder.glob('raw_asr_*.json'))) + 1:03d}.json"
    _write_json(raw_path, {"spec": spec, "audio_sha256": sha256_file(audio),
                          "language": info.language, "language_probability": float(info.language_probability),
                          "segments": [_raw_segment(segment) for segment in rows]})
    segments = []
    for segment in rows:
        raw_start, raw_end = float(segment.start), float(segment.end)
        local_start, local_end = _bounded_interval(raw_start, raw_end, end - start)
        words = []
        for word in segment.words or []:
            raw_word_start, raw_word_end = float(word.start), float(word.end)
            if not all(math.isfinite(t) for t in (raw_word_start, raw_word_end)):
                raise ValueError("asr_timestamp_not_finite")
            if min(raw_word_start, raw_word_end) < -0.05 or max(raw_word_start, raw_word_end) > end - start + 0.25:
                raise ValueError("asr_timestamp_out_of_window")
            if raw_word_end < raw_word_start:
                raise ValueError("asr_timestamp_reversed")
            if raw_word_start < local_start - 0.05 or raw_word_end > local_end + 0.05:
                raise ValueError("asr_word_timestamp_out_of_segment")
            word_start, word_end = max(local_start, raw_word_start), min(local_end, raw_word_end)
            saved_word = {"text": word.word, "probability": float(word.probability),
                          "raw_asr_local_start_s": raw_word_start, "raw_asr_local_end_s": raw_word_end,
                          "boundary_clamped": word_start != raw_word_start or word_end != raw_word_end}
            if word_end <= word_start:
                saved_word.update(timing_usable=False, alignment_issue=
                                  "zero_duration" if raw_word_end == raw_word_start else "clipped_to_empty")
            else:
                saved_word.update(timing_usable=True, source_start_s=start + word_start, source_end_s=start + word_end)
            words.append(saved_word)
        segments.append({"segment_id": f"{source['source_id']}_{start + local_start:.3f}",
                         "source_start_s": start + local_start, "source_end_s": start + local_end,
                         "local_start_s": local_start, "local_end_s": local_end, "text": segment.text,
                         "raw_asr_local_start_s": raw_start, "raw_asr_local_end_s": raw_end,
                         "boundary_clamped": local_start != raw_start or local_end != raw_end,
                         "avg_logprob": float(segment.avg_logprob),
                         "no_speech_prob": float(segment.no_speech_prob), "words": words})
    verify_source(source)
    result = {"spec": spec, "source_id": source["source_id"], "source_sha256": source["sha256"],
              "source_start_s": start, "source_end_s": end, "source_offset_s": start,
              "audio_path": str(audio), "audio_sha256": sha256_file(audio),
              "audio_stream_index": audio_index, "language": info.language,
              "language_probability": float(info.language_probability), "segments": segments,
              "raw_response_path": str(raw_path), "raw_response_sha256": sha256_file(raw_path),
              "time_validation": {"policy": TIME_VALIDATION_POLICY,
                                  "allowed_start_rounding_s": 0.05, "allowed_end_rounding_s": 0.25,
                                  "allowed_word_segment_rounding_s": 0.05,
                                  "unaligned_words_have_no_source_times": True,
                                  "raw_times_are_not_edit_ranges": True},
              "evidence_kind": "automatic_transcription_unverified",
              "evidence_limit": "ASR text is not music analysis, speaker identity, or verified visible action."}
    _validate_cached_times(result, start, end)
    _write_json(output, result)
    return result
