"""Omni AV auditions, model-owned selection, and video-only screenplay handoff."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .. import pipeline
from . import prompts
from .media import verify_cached
from .state import DiscoveryStopped, LIMITS


def validate_review(value, aid, duration):
    if value.get("aweme_id") != aid or not isinstance(value.get("potentially_useful"), bool):
        raise ValueError("review_identity_or_usefulness_invalid")
    for key in ("story_transfer", "innovation_space", "asset_feasibility", "editing_learning"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError("review_field_invalid:" + key)
    for key in ("limitations", "uncertainties"):
        if not isinstance(value.get(key), list) or not all(isinstance(s, str) for s in value[key]):
            raise ValueError("review_list_invalid:" + key)
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("review_evidence_missing")
    for row in evidence:
        a, b = row["start_s"], row["end_s"]
        if (isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float))
                or not isinstance(b, (int, float)) or not math.isfinite(a + b)
                or not 0 <= a < b <= duration + 0.1):
            raise ValueError("review_evidence_outside_media")
        if not isinstance(row.get("observed"), str) or not row["observed"].strip() or row.get("modality") not in {"visual", "audio", "text", "mixed"}:
            raise ValueError("review_evidence_invalid")


def completion_value(body):
    """Use the same accepted text representations for live and recorded responses."""
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content)
    return json.loads(content)


def omni_call(state, runner, name, prompt, payload, validator, *, media=None):
    original = payload
    operation = pipeline.json_sha({"prompt": prompt, "payload": original,
                                  "media_sha256": pipeline.sha(media) if media else None, "config": runner.cfg})
    previous = []
    for call in state.data["calls"]:
        if call.get("name") != name or call["kind"] != "omni":
            continue
        folder = state.output / "calls" / call["id"]
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        if request.get("operation_sha256") != operation:
            raise DiscoveryStopped("cached_review_input_changed:" + name)
        if call["status"] != "received":
            raise DiscoveryStopped("previous_review_failed_no_replay:" + name)
        response = json.loads((folder / "response.json").read_text(encoding="utf-8"))
        if pipeline.json_sha(response) != call["response_sha256"]:
            raise DiscoveryStopped("cached_review_response_changed:" + name)
        body = json.loads(response["body_text"])
        try:
            value = completion_value(body)
            validator(value)
            return value  # confirmed response survives a crash before candidate-state save
        except (ValueError, KeyError, TypeError, IndexError):
            previous.append(body)
    if len(previous) >= 2:
        raise DiscoveryStopped("Omni_protocol_repair_exhausted:" + name)
    if previous:
        payload = {"original_input": original, "invalid_response": previous[-1],
                   "protocol_error": "cached_response_failed_schema"}
        prompt = "Repair JSON/schema only; preserve semantic content.\n" + prompt
        media = None
    for attempt in range(len(previous), 2):
        text = prompt + json.dumps(payload, ensure_ascii=False)
        request = {"text": text, "media_sha256": pipeline.sha(media) if media else None,
                   "model_config": runner.cfg, "format_repair": bool(attempt), "operation_sha256": operation}
        call, folder = state.begin_call("omni", name, request)
        try:
            response = runner.request(text, media=media, tokens=3500)
        except Exception as exc:
            pipeline.write(folder / "transport_failure.json", {"error_type": type(exc).__name__})
            raise DiscoveryStopped("paid_request_outcome_unknown:" + call["id"]) from exc
        state.finish_call(call, folder, response,
                          status="received" if 200 <= response["http_status"] < 300 else "rejected")
        if not 200 <= response["http_status"] < 300:
            raise DiscoveryStopped("Omni_HTTP_" + str(response["http_status"]))
        body = json.loads(response["body_text"])
        call["usage"] = body.get("usage") or {}
        state.save()
        try:
            value = completion_value(body)
            validator(value)
            pipeline.write(folder / "validation.json", {"status": "pass"})
            return value
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            pipeline.write(folder / "validation.json", {"status": "invalid_protocol", "error": str(exc)})
            if attempt:
                state.run_error = "Omni_protocol_repair_exhausted:" + name
                raise DiscoveryStopped("Omni_protocol_repair_exhausted:" + name) from exc
            payload = {"original_input": original, "invalid_response": body,
                       "protocol_error": str(exc)}
            prompt = "Repair JSON/schema only; preserve semantic content.\n" + prompt
            media = None  # never pay for another full AV reading to repair returned text


def audition(state, candidate, runner):
    if candidate.get("review"):
        verify_cached(candidate["media"])
        return candidate["review"]
    if len(state.reviewed) >= LIMITS["reviews"]:
        return None
    media = verify_cached(candidate["media"])
    path = Path(media["analysis_path"])
    value = omni_call(state, runner, "review_" + candidate["aweme_id"], prompts.ASSESS,
                      {"aweme_id": candidate["aweme_id"], "duration_s": media["duration_s"],
                       "audio_stream_present": media["audio_stream_present"]},
                      lambda v: validate_review(v, candidate["aweme_id"], media["duration_s"]), media=path)
    candidate["review"] = value
    state.save()
    return value


def select(state, runner):
    if state.data.get("selection"):
        return state.data["selection"]
    candidates = state.reviewed
    if not candidates:
        return {"selected_aweme_id": None, "reason": "没有实际下载并完整审看的候选", "limitations": []}
    for candidate in candidates:
        verify_cached(candidate["media"])
    ids = {row["aweme_id"] for row in candidates}
    def check(value):
        if value.get("selected_aweme_id") is not None and value["selected_aweme_id"] not in ids:
            raise ValueError("selection_unknown_candidate")
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            raise ValueError("selection_reason_missing")
        if not isinstance(value.get("limitations"), list) or not all(isinstance(s, str) for s in value["limitations"]):
            raise ValueError("selection_limitations_invalid")
    value = omni_call(state, runner, "selection", prompts.SELECT,
                      {"candidates": [row["review"] for row in candidates]}, check)
    state.data["selection"] = value
    state.save()
    return value


def handoff(state, selection, *, stage="screenplay", execute=None):
    aid = selection["selected_aweme_id"]
    if aid is None:
        return state.result(status="no_candidate", reason=selection["reason"])
    candidate = state.data["candidates"][aid]
    media = verify_cached(candidate["media"])
    selected = {"aweme_id": aid, "page_url": candidate["page_url"], **media,
                "selection": selection, "review": candidate["review"],
                "limitations": list(dict.fromkeys(selection["limitations"] + candidate["review"]["limitations"]))}
    pipeline.write(state.output / "selected_reference.json", selected)
    result = {"status": "reference_selected", "selected_reference": str(state.output / "selected_reference.json"),
              "video": media["analysis_path"], "limitations": selected["limitations"]}
    if stage == "screenplay":
        output = state.output / "screenplay"
        if output.exists():
            result_file = output / "result.json"
            lineage_file = output / "input_lineage.json"
            if not result_file.exists() or not lineage_file.exists():
                raise DiscoveryStopped("incomplete_screenplay_preserved_no_automatic_replay")
            lineage = json.loads(lineage_file.read_text(encoding="utf-8"))
            if lineage["video_sha256"] != media["analysis_sha256"]:
                raise DiscoveryStopped("screenplay_input_sha_mismatch")
            screenplay = json.loads(result_file.read_text(encoding="utf-8"))
            if screenplay["status"] not in {"model_checked_screenplay_candidate", "screenplay_needs_review"}:
                raise DiscoveryStopped("failed_screenplay_preserved_no_automatic_replay")
            actual = output / "screenplay.json"
            if not actual.is_file() or pipeline.sha(actual) != screenplay["screenplay_sha256"]:
                raise DiscoveryStopped("screenplay_cache_sha_mismatch")
        else:
            # Only the verified local video crosses the creative boundary.
            with state.suspend_time():
                screenplay = (execute or pipeline.execute)(Path(media["analysis_path"]), output)
        result.update(status=screenplay["status"], screenplay=screenplay, screenplay_output=str(output))
    return state.result(**result)
