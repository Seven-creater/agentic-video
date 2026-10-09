"""Historical rough-cut instructions, without historical creative decisions.

The runtime templates come from actual received-call requests. The archived
git files provide separate source provenance; the renderer is executed as-is.
This restores instructions, not a promise of the same model choices or duration.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .archived_render import compile_library_plan, render_library_video


POLICY_VERSION = "reference_library_glm_mcp_v1"
RESOURCE_POLICY = "historical_rough_prompt_templates_v1"
_ROOT = Path(__file__).parent
_TEMPLATES_SHA = "5771bf7ecf2b10478cdee4067b0c601fb8c6b6c2a55d90ffd4ec6f0e830159ac"


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def provenance() -> dict:
    """Verify packaged historical bytes before constructing new requests."""
    raw = (_ROOT / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if manifest.get("policy") != RESOURCE_POLICY:
        raise ValueError("historical_rough_resource_policy_changed")
    template_sha = _sha((_ROOT / "templates.json").read_bytes())
    if template_sha != _TEMPLATES_SHA or template_sha != manifest["templates_sha256"]:
        raise ValueError("historical_rough_templates_changed")
    for filename, origin in manifest["archives"].items():
        if _sha((_ROOT / filename).read_bytes()) != origin["sha256"]:
            raise ValueError("historical_rough_archived_source_changed:" + filename)
    return {
        "policy": RESOURCE_POLICY,
        "manifest_sha256": _sha(raw),
        "templates_sha256": template_sha,
        "archives": manifest["archives"],
        "historical_request_origins": manifest["origins"],
        "historical_context_and_model_answers_included": False,
        "fixed_77_second_target": False,
        "limits": manifest["not_reproduced"],
    }


provenance()
_TEMPLATES = json.loads((_ROOT / "templates.json").read_text(encoding="utf-8"))
BASE = _TEMPLATES["base"]


def reference_prompt(reference_sha: str, duration_s: float) -> str:
    return (_TEMPLATES["reference"].replace("__REFERENCE_SHA__", reference_sha)
            .replace("__DURATION_S__", str(duration_s)))


def coarse_prompt(source_id: str, coverage_s: list, frame_times: list, context: dict) -> str:
    return _TEMPLATES["coarse"] + json.dumps(
        {"source_id": source_id, "coverage_s": coverage_s,
         "frame_times": frame_times, "context": context}, ensure_ascii=False
    ) + _TEMPLATES["coarse_suffix"]


def zoom_search_prompt(context: dict, *, max_regions: int = 4, span_s: float = 600) -> str:
    # Original range-navigation prompt had no worked region choices.
    prefix = (_TEMPLATES["zoom_search"].replace("__MAX_REGIONS__", str(max_regions))
              .replace("__SPAN_S__", str(span_s)))
    return prefix + json.dumps(context, ensure_ascii=False)


def search_prompt(context: dict) -> str:
    # Historical text says 12; context and validator still enforce round cap 8.
    return _TEMPLATES["search"] + json.dumps(context, ensure_ascii=False)


def fine_prompt(window: dict, context: dict) -> str:
    return _TEMPLATES["fine"] + json.dumps(
        {"window": window, "context": context}, ensure_ascii=False)


def plan_prompt(context: dict) -> str:
    stream = context.get("reference_audio_stream_index")
    has_audio = type(stream) is int and stream >= 0
    example = {
        "start_s": 0,
        "end_s": min(10, context.get("reference_duration_s", 10)),
        "stream_index": stream,
        "loop": False,
    } if has_audio else None
    prefix = (_TEMPLATES["plan"]
              .replace("__AUDIO_MODE__", json.dumps("reference" if has_audio else "source"))
              .replace("__AUDIO_EXAMPLE__", json.dumps(example, ensure_ascii=False)))
    return prefix + json.dumps(context, ensure_ascii=False)


def blind_prompt(duration_s: float) -> str:
    return _TEMPLATES["blind"].replace("__DURATION_S__", str(duration_s))


def review_prompt(context: dict) -> str:
    return _TEMPLATES["review"] + json.dumps(context, ensure_ascii=False)


def select_prompt(candidates: list) -> str:
    return _TEMPLATES["select"] + json.dumps(candidates, ensure_ascii=False)
