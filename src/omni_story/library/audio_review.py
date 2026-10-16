"""Source-bound, write-before-send listening and picture review of an actual video.

The video must carry audio. Received replies are evidence, not a quality pass;
the caller validates their schema and claims. An unknown request never replays.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from omni_story.api import QwenAPI

from .media import probe_media, sha256_file
from .state import file_lock, json_sha, write_json

POLICY = "actual-audio-video-review-v1"
INLINE_BASE64_LIMIT = 10_000_000
_SECRET_FIELDS = {"key", "api_key", "token", "auth_token", "authorization", "password",
                  "secret", "access_token", "refresh_token", "credential", "credentials"}
_DATA_URI = re.compile(r"data:[^\s\"'<>]*?;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)


def _text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _safe(value, secrets):
    """Keep response evidence without credentials, data URIs or signed URLs."""
    if isinstance(value, dict):
        return {key: _safe(item, secrets) for key, item in value.items()
                if str(key).lower() not in _SECRET_FIELDS
                and not str(key).lower().endswith(("_key", "_token", "_secret", "_password"))}
    if isinstance(value, list):
        return [_safe(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    for secret in secrets:
        value = value.replace(secret, "[REDACTED]")
    value = _DATA_URI.sub("[REDACTED_MEDIA]", value)
    value = re.sub(r"Bearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", value, flags=re.IGNORECASE)
    def safe_url(match):
        try:
            parsed = urlsplit(match.group())
        except ValueError:
            return "[REDACTED_URL]"
        return "[REDACTED_URL]" if parsed.query or parsed.fragment or parsed.username else match.group()
    return _URL.sub(safe_url, value)


def _safe_body(body, secrets):
    # Parse before redaction so JSON escaping cannot conceal a data URI/URL.
    try:
        value = json.loads(body)
    except (ValueError, TypeError):
        return _safe(body, secrets)
    cleaned = _safe(value, secrets)
    return body if cleaned == value else json.dumps(cleaned, ensure_ascii=False, allow_nan=False)


def _configuration(api, secrets):
    cfg = dict(api.cfg)
    endpoint = getattr(api, "endpoint", None)
    if endpoint is None:
        endpoint = str(cfg.get("base_url", "")).rstrip("/") + "/chat/completions"
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("audio_review:credential_free_https_endpoint_required")
    if not isinstance(cfg.get("model"), str) or not cfg["model"]:
        raise ValueError("audio_review:configured_model_required")
    return {"configuration": _safe(cfg, secrets), "endpoint": _safe(endpoint, secrets),
            "request_options": {"tokens": 6000, "media_kind": "video_url",
                                "modalities": ["text"], "embedded_audio_required": True}}


def _result(response, state, *, cached):
    body, reply, parsed, usage, error = {}, None, None, {}, None
    try:
        body = json.loads(response["body_text"])
        if not isinstance(body, dict):
            raise ValueError
        usage = body.get("usage", {})
        reply = body["choices"][0]["message"]["content"]
        if not isinstance(reply, str):
            raise ValueError
        parsed = json.loads(reply)
        if not isinstance(parsed, dict):
            parsed = None
            error = "reply_json_object_required"
    except (ValueError, KeyError, IndexError, TypeError):
        error = "response_or_reply_json_invalid"
    if response.get("http_status") != 200:
        error = "http_status_not_200"
    return {"status": "received", "parsed": parsed, "reply": reply, "usage": usage,
            "http_status": response.get("http_status"), "parse_error": error,
            "video_sha256": state["video_sha256"], "request_sha256": state["request_sha256"],
            "response_sha256": state["response_sha256"], "cache_hit": cached}


def _verify_segment_binding(binding, path, video_sha):
    """Check the source/lineage pins before send and after receiving evidence."""
    try:
        source, lineage = Path(binding['source_path']), Path(binding['lineage_path'])
        start, end = binding['source_start_s'], binding['source_end_s']
        saved = json.loads(lineage.read_text(encoding='utf-8'))
        spec = saved['spec']
        valid = (source.is_absolute() and lineage.is_absolute() and
            sha256_file(source) == binding['source_sha256'] and
            sha256_file(lineage) == binding['lineage_sha256'] and
            saved['path'] == str(path) and saved['sha256'] == video_sha == binding['clip_sha256'] and
            spec['source_path'] == str(source) and spec['source_sha256'] == binding['source_sha256'] and
            type(start) in (int, float) and type(end) in (int, float) and
            math.isfinite(start) and math.isfinite(end) and 0 <= start < end and
            spec['source_start_s'] == start and spec['source_end_s'] == end and
            saved['video_first_pts_s'] == binding['clip_video_first_pts_s'] == 0 and
            saved['audio_first_pts_s'] == binding['clip_audio_first_pts_s'] == 0 and
            saved['fully_decoded'] is True and
            spec['time_mapping'] == binding['time_mapping'] == 'source_time_s = source_start_s + clip_time_s')
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError('audio_review:segment_source_or_lineage_binding_changed_no_replay')


def review_audio_video(video, output_dir, *, prompt, api=None, source_binding=None) -> dict:
    """Review actual embedded sound and video once per bound output directory.

    Oversized inputs require the caller to prepare an audio-preserving proxy;
    this function has no arbitrary duration limit and never silently mutes media.
    ``parsed`` may be None for a received invalid reply. ``reply`` preserves the
    original model content apart from credential/media/URL security redactions.
    """
    path = Path(video).resolve(strict=True)
    if not path.is_file():
        raise ValueError("audio_review:video_file_required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("audio_review:prompt_required")
    before = path.stat()
    encoded_size = 4 * ((before.st_size + 2) // 3)
    if encoded_size >= INLINE_BASE64_LIMIT:
        raise ValueError("audio_review:inline_base64_exceeds_10mb_prepare_audio_preserving_proxy")
    measured = probe_media(path)
    audio_indices = [row["index"] for row in measured["streams"] if row.get("codec_type") == "audio"]
    if not audio_indices:
        raise ValueError("audio_review:actual_video_has_no_audio_stream")
    video_sha = sha256_file(path)
    if source_binding is not None:
        _verify_segment_binding(source_binding, path, video_sha)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("audio_review:video_changed_during_probe")
    runner = api if api is not None else QwenAPI()
    secrets = tuple(str(value) for value in (getattr(runner, "key", None),
                                            getattr(runner, "token", None)) if value)
    request = {"policy": POLICY, "video_path": str(path), "video_sha256": video_sha,
               "size_bytes": before.st_size, "base64_size_bytes": encoded_size,
               "duration_s": measured["duration_s"], "audio_stream_indices": audio_indices,
               "prompt": _safe(prompt, secrets), "prompt_sha256": _text_sha(prompt),
               **_configuration(runner, secrets)}
    if source_binding is not None:
        # Segment lineage belongs in the paid request fingerprint, not its prompt.
        request["source_binding"] = source_binding
    fingerprint = json_sha(request)
    folder = Path(output_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    state_path, request_path, response_path = (folder / name for name in
                                              ("state.json", "request.json", "response.json"))
    with file_lock(folder / ".audio_review.lock"):
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (state.get("request_sha256") != fingerprint or not request_path.is_file()
                    or json_sha(json.loads(request_path.read_text(encoding="utf-8"))) != fingerprint):
                raise ValueError("audio_review:request_binding_changed_no_replay")
            if state.get("status") in {"submitted", "uncertain"}:
                raise RuntimeError("audio_review:request_outcome_unknown_no_replay")
            if state.get("status") != "received":
                raise ValueError("audio_review:invalid_saved_status_no_replay")
            if not response_path.is_file() or sha256_file(response_path) != state.get("response_sha256"):
                raise ValueError("audio_review:received_response_missing_or_modified")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if (_text_sha(response["body_text"]) != state.get("body_sha256")
                    or state.get("video_sha256") != video_sha):
                raise ValueError("audio_review:received_response_binding_invalid")
            if state.get("source_changed_during_request"):
                raise ValueError("audio_review:video_changed_during_request_no_replay")
            return _result(response, state, cached=True)
        if request_path.exists() or response_path.exists():
            raise RuntimeError("audio_review:orphan_request_artifacts_no_replay")
        if sha256_file(path) != video_sha:
            raise ValueError("audio_review:video_changed_before_submission")
        from .resource_guard import before_request
        before_request()
        write_json(request_path, request)
        state = {"policy": POLICY, "status": "submitted", "request_sha256": fingerprint,
                 "video_sha256": video_sha, "submitted_at": _timestamp()}
        write_json(state_path, state)
        try:
            response = runner.request(prompt, media=path)
        except Exception as exc:
            state.update(status="uncertain", error="request_outcome_unknown_no_replay",
                         error_type=type(exc).__name__, failed_at=_timestamp())
            write_json(state_path, state)
            raise RuntimeError("audio_review:request_outcome_unknown_no_replay") from None
        # Only store response fields from the hosted contract, never its payload.
        saved_response = {"http_status": response.get("http_status"),
                          "body_text": _safe_body(response["body_text"], secrets),
                          "original_body_sha256": _text_sha(response["body_text"]),
                          "elapsed_s": response.get("elapsed_s"),
                          "transport_json_instruction_added": response.get("transport_json_instruction_added")}
        write_json(response_path, saved_response)
        state.update(status="received", received_at=_timestamp(),
                     response_sha256=sha256_file(response_path),
                     body_sha256=_text_sha(saved_response["body_text"]))
        state["source_changed_during_request"] = sha256_file(path) != video_sha
        if source_binding is not None:
            try:
                _verify_segment_binding(source_binding, path, video_sha)
            except ValueError:
                state['source_changed_during_request'] = True
        write_json(state_path, state)
        if state["source_changed_during_request"]:
            raise ValueError("audio_review:video_changed_during_request_no_replay")
        return _result(saved_response, state, cached=False)


def review_audio_video_segmented(video, output_dir, *, prompt, timeline=None,
                                 api=None, max_segment_s=6.0) -> dict:
    """Blind actual-media review of contiguous, zero-PTS technical segments.

    ``prompt`` contains review instructions only, with no whole-film timing,
    transcript or authored lines. The wrapper adds each clip's measured local
    duration and PCM voice bands. Received invalid replies remain evidence;
    unknown requests stop and never replay. There is no total request limit.
    """
    from .audio_segments import review_segments
    return review_segments(video, output_dir, prompt=prompt, timeline=timeline,
                           api=api, max_segment_s=max_segment_s)
