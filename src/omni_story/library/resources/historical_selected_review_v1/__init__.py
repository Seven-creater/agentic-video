"""The generic historical043 selected actual-video review, without its answer.

This resource only constructs a prompt. The caller supplies the current
reference, actual render, blind reading, plan and execution provenance, and
remains responsible for authorization, model dispatch and review validation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


RESOURCE_POLICY = "historical_selected_review_v1"
_ROOT = Path(__file__).parent
_MANIFEST_SHA = "4fab77603ff0c151f4e341e2aa38886784100792304169b390025adaabf4555d"
_TEMPLATE_SHA = "2acbf827ddfa126ca31def2fbcb59257bd149ab8f1f1fb2d2c446c3ba9ce4ac7"


def provenance() -> dict:
    """Verify the independently bound template and its historical origin."""
    manifest_raw = (_ROOT / "manifest.json").read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest() != _MANIFEST_SHA:
        raise ValueError("historical_selected_review_manifest_changed")
    manifest = json.loads(manifest_raw)
    if manifest["policy"] != RESOURCE_POLICY:
        raise ValueError("historical_selected_review_policy_changed")
    prefix = (_ROOT / manifest["template_file"]).read_bytes()
    template_sha = hashlib.sha256(prefix).hexdigest()
    if template_sha != _TEMPLATE_SHA or template_sha != manifest["template_sha256"]:
        raise ValueError("historical_selected_review_template_changed")
    return {**manifest, "manifest_sha256": _MANIFEST_SHA}


def review_prompt(context: dict) -> str:
    """Append only caller-supplied actual-video context to the exact043 prefix."""
    proof = provenance()
    prefix = (_ROOT / proof["template_file"]).read_text(encoding="utf-8")
    return prefix + json.dumps(context, ensure_ascii=False)
