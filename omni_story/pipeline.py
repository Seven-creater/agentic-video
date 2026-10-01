"""One video -> one bounded autonomous run; no approval/resume/story-seed inputs."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import traceback

from . import contract, prompts
from .api import QwenAPI

ROOT = Path(__file__).resolve().parents[1]
MAX_CALLS = 32


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def probe(video):
    value = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,width,height,r_frame_rate", "-of", "json", str(video)],
        check=True, capture_output=True, text=True).stdout)
    duration = float(value["format"]["duration"])
    contract.require(math.isfinite(duration) and duration > 0, "video_duration_invalid")
    contract.require(any(s["codec_type"] == "video" for s in value["streams"]), "video_stream_missing")
    return value


def code_snapshot():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / "omni_story").glob("*.py"))}


def validate_reading(value, duration):
    contract.require(value.get("schema_version") == "qwen38_reference_reading_v1", "reading_schema")
    contract.ids(value.get("sections"), "section_id", "sections")
    for section in value["sections"]:
        start = contract.number(section.get("start_s"), "section/start_s")
        end = contract.number(section.get("end_s"), "section/end_s")
        contract.require(start < end <= duration + 0.2, "reading_interval_outside_video")
        for key in ("visible_events", "video_text_statements", "audible_events"):
            contract.rows(section.get(key), key, nonempty=False)
            contract.require(all(isinstance(t, str) for t in section[key]), "reading_list_type")
        for key in ("new_information", "interpretation_hypothesis"):
            contract.text(section.get(key), key)
    for key in ("audience_takeaway", "initial_viewer_judgment", "updated_viewer_judgment", "ending_effect"):
        contract.text(value.get(key), key)
    contract.rows(value.get("uncertainties"), "uncertainties", nonempty=False)


def validate_routes(value):
    contract.require(value.get("schema_version") == "autonomous_routes_v1", "routes_schema")
    ids = contract.ids(value.get("routes"), "route_id", "routes")
    contract.require(ids == {"R1", "R2", "R3"} and value.get("selected_route_id") in ids, "routes_selection")
    relation = value.get("reference_relation")
    contract.require(isinstance(relation, dict), "reference_relation_type")
    for key in ("audience_takeaway", "evidence_mechanism", "ending_function"):
        contract.text(relation.get(key), key)
    contract.rows(relation.get("information_sequence"), "information_sequence")
    for route in value["routes"]:
        for key in ("title", "premise", "visible_action_and_result", "why_original"):
            contract.text(route.get(key), key)


def pointer(target, path):
    contract.require(isinstance(path, str) and path.startswith("/"), "review_pointer_invalid")
    value = target
    try:
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                contract.require(part.isdecimal(), "review_list_index_invalid")
                value = value[int(part)]
            else:
                value = value[part]
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("review_pointer_not_found:" + path) from exc
    return value


def validate_review(value, target):
    contract.require(value.get("schema_version") == "autonomous_review_v1", "review_schema")
    contract.require(value.get("verdict") in {"pass", "revise"}, "review_verdict")
    issues = contract.rows(value.get("issues"), "issues", nonempty=False)
    for row in issues:
        pointer(target, row.get("path"))
        contract.require(row.get("severity") in {"blocking", "risk", "polish"}, "review_severity")
        contract.require(row.get("kind") in {"contradiction", "missing_evidence", "meaning_mismatch", "detail"},
                         "review_kind")
        contract.text(row.get("reason"), "review_reason")
    blocked = any(row["severity"] == "blocking" for row in issues)
    contract.require((value["verdict"] == "revise") == blocked, "review_decision_inconsistent")


def blind_view(screenplay):
    # Exclude author meaning, reference, causes, title and viewer updates.
    return {**{key: screenplay[key] for key in ("characters", "locations", "props")},
        "units": [{**{key: row[key] for key in
            ("unit_id", "character_ids", "location_id", "prop_ids", "entry_state", "exit_state")},
            "events": [{key: event[key] for key in
                ("event_id", "before", "action", "after", "observable_evidence")}
                for event in row["events"]]} for row in screenplay["segments"]]}


def validate_blind(value, screenplay):
    contract.require(value.get("schema_version") == "autonomous_blind_v1", "blind_schema")
    contract.text(value.get("viewer_takeaway"), "viewer_takeaway")
    ids = {row["unit_id"] for row in screenplay["segments"]}
    covered = contract.ids(value.get("sequence_reading"), "unit_id", "sequence_reading")
    contract.require(covered == ids, "blind_unit_coverage")
    contract.require(value.get("verdict") in {"usable", "needs_revision"}, "blind_verdict")
    for row in contract.rows(value.get("issues"), "issues", nonempty=False):
        contract.refs(row.get("unit_ids"), ids, "blind_issue_unit_ids", nonempty=True)
        contract.require(row.get("severity") in {"blocking", "risk", "polish"}, "blind_issue_severity")
        contract.text(row.get("reason"), "blind_issue_reason")
    blocked = any(row["severity"] == "blocking" for row in value["issues"])
    contract.require((value["verdict"] == "needs_revision") == blocked, "blind_decision_inconsistent")


def validate_alignment(value):
    contract.require(value.get("schema_version") == "autonomous_alignment_v1", "alignment_schema")
    for key in ("takeaway", "evidence_mechanism"):
        row = value.get(key)
        contract.require(isinstance(row, dict) and row.get("status") in
                         {"supported", "uncertain", "contradicted"}, "alignment_status")
        contract.text(row.get("reason"), "alignment_reason")


class Loop:
    def __init__(self, output, runner, frozen):
        self.output, self.runner, self.frozen = output, runner, frozen
        self.state = {"status": "running", "pid": os.getpid(), "calls": 0,
                      "max_calls": MAX_CALLS, "stages": {}, "human_approval_used": False}

    def save(self):
        write(self.output / "execution_state.json", self.state)

    def call(self, name, prompt, payload, validator, *, media=None, tokens=6000):
        original = payload
        for attempt in (0, 1):  # one format repair, never a transport retry
            contract.require(self.state["calls"] < MAX_CALLS, "model_call_budget_exhausted")
            contract.require(code_snapshot() == self.frozen, "code_changed_during_frozen_run")
            self.state["calls"] += 1
            self.state["current_stage"] = name
            self.save()
            folder = self.output / "calls" / f"{self.state['calls']:03d}_{name}"
            folder.mkdir(parents=True)
            request = prompt + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            (folder / "request.txt").write_text(request, encoding="utf-8")
            write(folder / "request_metadata.json", {"input_sha": json_sha(payload),
                "prompt_sha": json_sha(prompt), "request_sha": sha(folder / "request.txt"),
                "model_config_sha": json_sha(self.runner.cfg), "media_sha": sha(media) if media else None,
                "requested_fps": self.runner.cfg.get("reference_fps_requested") if media else None,
                "actual_sampling": "provider_not_reported" if media else None,
                "human_story_seed": False, "format_repair": bool(attempt)})
            print(f"Call {self.state['calls']}/{MAX_CALLS}: {name}", flush=True)
            response = self.runner.request(request, media=media, tokens=tokens)
            (folder / "api_response.json").write_text(response["body_text"], encoding="utf-8")
            body = json.loads(response["body_text"])
            write(folder / "response_metadata.json", {"http_status": response["http_status"],
                "elapsed_s": response["elapsed_s"], "usage": body.get("usage", {}),
                "response_sha": sha(folder / "api_response.json")})
            contract.require(200 <= response["http_status"] < 300,
                             "API_http_" + str(response["http_status"]))
            message = body["choices"][0]["message"]["content"]
            if isinstance(message, list):
                message = "".join(row.get("text", "") for row in message if isinstance(row, dict))
            contract.require(isinstance(message, str), "API_text_missing")
            (folder / "raw_response.txt").write_text(message, encoding="utf-8")
            try:
                cleaned = message.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                value = json.loads(cleaned)
                contract.require(isinstance(value, dict), "JSON_object_required")
                write(folder / "parsed.json", value)
                validator(value)
                write(folder / "validation.json", {"status": "pass"})
                return value
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                write(folder / "validation.json", {"status": "invalid_protocol", "reason": str(exc)})
                if attempt:
                    raise ValueError("protocol_repair_exhausted:" + name + ":" + str(exc)) from exc
                payload = {"original_input": original, "invalid_response": message,
                           "protocol_error": str(exc)}
                # Repair returned text; do NOT pay for another full AV reading.
                prompt = "Repair only the JSON/schema error. Preserve semantic content.\n" + prompt
                media = None

    def stage(self, name, prompt, payload, validator, *, feedback=None):
        for version in (1, 2):  # original and one autonomous semantic revision
            draft = self.call(f"{name}_draft_{version}", prompt if version == 1 else prompts.REVISION,
                payload if version == 1 else {"original_request": payload, "target": draft,
                    "review": review, "output_contract": prompt}, validator)
            write(self.output / f"{name}_draft_{version}.json", draft)
            review = self.call(f"{name}_review_{version}", prompts.REVIEW,
                {"target": draft, "context": payload, "additional_feedback": feedback},
                lambda value: validate_review(value, draft), tokens=2600)
            write(self.output / f"{name}_review_{version}.json", review)
            if review["verdict"] == "pass":
                write(self.output / f"{name}.json", draft)
                self.state["stages"][name] = {"status": "model_checked_text", "drafts": version,
                                             "sha256": sha(self.output / f"{name}.json")}
                self.save()
                return draft
        raise ValueError("semantic_revision_budget_exhausted:" + name)


def execute(video, output, *, runner=None, media_probe=None):
    video, output = Path(video).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("output_exists_no_repeat")
    contract.require(video.is_file(), "video_missing")
    contract.require(video.stat().st_size < 10_000_000, "inline_video_exceeds_10mb")
    measured = (media_probe or probe)(video)
    duration = float(measured["format"]["duration"])
    runner = runner or QwenAPI()
    frozen = code_snapshot()
    identity = json_sha({"input_video_sha": sha(video), "code": frozen, "prompts": prompts.ALL,
                         "model_config": runner.cfg, "max_calls": MAX_CALLS})
    registry = output.parent / ".run_registry"
    registry.mkdir(parents=True, exist_ok=True)
    with (registry / (identity + ".json")).open("x", encoding="utf-8") as handle:
        json.dump({"output": str(output), "max_calls": MAX_CALLS}, handle)
    output.mkdir(parents=True)
    write(output / "prompts.json", prompts.ALL)
    write(output / "input_lineage.json", {"video": str(video), "video_sha256": sha(video),
        "media_metadata": measured, "model_config": runner.cfg, "code_shas": frozen,
        "prompt_sha": json_sha(prompts.ALL), "run_identity": identity,
        "semantic_inputs": ["reference_video_only"], "prior_outputs_loaded": False,
        "human_approval_used": False, "max_model_calls": MAX_CALLS,
        "semantic_revisions_per_stage": 1, "protocol_repairs_per_call": 1,
        "image_video_generation": False, "production_release_allowed": False})
    loop = Loop(output, runner, frozen)
    try:
        reading = loop.call("reference", prompts.REFERENCE, {"interval": [0.0, duration]},
            lambda value: validate_reading(value, duration), media=video, tokens=6000)
        write(output / "reference_reading.json", reading)
        routes = loop.call("routes", prompts.ROUTES, {"reference_reading": reading}, validate_routes)
        write(output / "routes.json", routes)
        relation = routes["reference_relation"]
        chosen = next(r for r in routes["routes"] if r["route_id"] == routes["selected_route_id"])
        plan = loop.stage("outline", prompts.OUTLINE,
            {"selected_premise": chosen, "abstract_reference_relation": relation},
            lambda value: contract.validate_outline(value, value))
        assets = {key: plan[key] for key in ("characters", "locations", "props")}
        write(output / "asset_bible.json", assets)
        segments = []
        for unit in plan["units"]:
            def validate(value):
                contract.validate_segment(value, unit, plan)
                contract.require(value.get("dialogue_or_voiceover") is None
                                 and value.get("on_screen_text") is None, "pictures_only_required")
            segment = loop.stage(unit["unit_id"], prompts.SEGMENT,
                {"outline": plan, "abstract_reference_relation": relation, "current_unit": unit,
                 "locked_previous_segments": segments,
                 "prior_state_after": segments[-1]["state_after"] if segments else None}, validate)
            segments.append(segment)
            write(output / "screenplay_candidate.json", {"schema_version": "story_screenplay_v2",
                "title": plan["title"], "intended_takeaway": plan["intended_takeaway"],
                **assets, "segments": segments})
        screenplay = json.loads((output / "screenplay_candidate.json").read_text(encoding="utf-8"))
        blind = loop.call("final_blind", prompts.BLIND, {"visible_story": blind_view(screenplay)},
                         lambda value: validate_blind(value, screenplay), tokens=4000)
        write(output / "blind_reading.json", blind)
        alignment = loop.call("final_alignment", prompts.ALIGNMENT,
            {"abstract_reference_relation": relation, "screenplay": screenplay, "blind_reading": blind},
            validate_alignment, tokens=3000)
        write(output / "alignment_review.json", alignment)
        passed = blind["verdict"] == "usable" and all(alignment[k]["status"] == "supported"
                    for k in ("takeaway", "evidence_mechanism"))
        status = "model_checked_screenplay_candidate" if passed else "screenplay_needs_review"
        write(output / "screenplay.json", screenplay)
        write(output / "screenplay_package.json", {"schema_version": "autonomous_screenplay_package_v1",
            "status": status, "screenplay": screenplay, "asset_bible": assets,
            "blind_reading": blind, "alignment_review": alignment, "human_approval_used": False,
            "media_verified": False, "material_generation": "not_run", "clip_selection": "not_run",
            "production_release_allowed": False})
        result = {"status": status, "title": plan["title"], "units": len(segments),
                  "screenplay_sha256": sha(output / "screenplay.json"),
                  "asset_counts": {k: len(v) for k, v in assets.items()}}
    except Exception as exc:
        result = {"status": "blocked", "reason": str(exc), "error_type": type(exc).__name__}
        write(output / "run_failure.json", {**result, "traceback": traceback.format_exc()})
    calls = [json.loads(p.read_text(encoding="utf-8")) for p in output.glob("calls/*/response_metadata.json")]
    result.update(actual_model_calls=loop.state["calls"], max_model_calls=MAX_CALLS,
        usage={k: sum(row.get("usage", {}).get(k) or 0 for row in calls)
               for k in ("prompt_tokens", "completion_tokens")},
        elapsed_model_s=sum(row["elapsed_s"] for row in calls), human_approval_used=False,
        media_verified=False, image_video_generation=False, production_release_allowed=False,
        input_unchanged=sha(video) == json.loads((output / "input_lineage.json").read_text(encoding="utf-8"))["video_sha256"])
    if not result["input_unchanged"]:
        result.update(status="blocked", reason="input_changed_during_run")
    write(output / "result.json", result)
    loop.state.update(status="completed", result=result)
    loop.save()
    write(output / "manifest.json", [{"path": str(p.relative_to(output)), "size": p.stat().st_size,
        "sha256": sha(p)} for p in sorted(output.rglob("*")) if p.is_file()])
    return result
