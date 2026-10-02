"""Video-only autonomous production continuation, resumable without human creative overrides."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import traceback

from .api import QwenAPI
from . import editing, reference, production_prompts as prompts
from .media_backends import AliyunImages, MiniMaxH3, load
from .pipeline import code_snapshot, execute, json_sha, probe, sha, write

LIMITS = {"model_calls": 36, "image_jobs": 12, "video_jobs": 6,
          "image_repairs": 1, "edit_revisions": 1, "protocol_repairs_per_call": 1}


def ids(rows, key):
    values = [row[key] for row in rows]
    if not values or len(set(values)) != len(values) or any(
        not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", v) for v in values):
        raise ValueError("production_ids_invalid:" + key)
    return set(values)


def validate_plan(value, story, limits, transfer=None):
    if value.get("schema_version") != "production_plan_v1":
        raise ValueError("production_plan_schema")
    asset_ids = {r["id"] for k in ("characters", "locations", "props") for r in story[k]}
    if ids(value["assets"], "asset_id") != asset_ids:
        raise ValueError("production_asset_coverage")
    ids(value["materials"], "material_id")
    if len(value["materials"]) > limits["video_jobs"]:
        raise ValueError("production_video_budget")
    if len(value["assets"]) + len(value["materials"]) > limits["image_jobs"]:
        raise ValueError("production_image_budget")
    units = {u["unit_id"] for u in story["segments"]}
    event_units = {e["event_id"]: u["unit_id"] for u in story["segments"] for e in u["events"]}
    covered = set()
    for row in value["materials"]:
        if not row["unit_ids"] or not set(row["unit_ids"]) <= units:
            raise ValueError("production_unknown_unit")
        if not row["event_ids"] or not set(row["event_ids"]) <= set(event_units):
            raise ValueError("production_unknown_event")
        if any(event_units[e] not in row["unit_ids"] for e in row["event_ids"]):
            raise ValueError("production_event_unit_mismatch")
        if not row["asset_ids"] or not set(row["asset_ids"]) <= asset_ids:
            raise ValueError("production_unknown_asset")
        if type(row["generation_duration_s"]) is not int or not 4 <= row["generation_duration_s"] <= 15:
            raise ValueError("production_duration")
        for key in ("entry_state", "main_action", "exit_state", "essential_evidence", "start_frame_prompt", "video_prompt"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError("production_empty:" + key)
        if any(k in row for k in ("in_s", "out_s", "timeline_start_s", "timeline_end_s")):
            raise ValueError("premature_source_slice")
        covered.update(row["unit_ids"])
    if covered != units:
        raise ValueError("production_unit_coverage")
    for row in value["assets"]:
        if not isinstance(row.get("master_prompt"), str) or not row["master_prompt"].strip():
            raise ValueError("production_master_prompt")
    if transfer is not None:
        reference.validate_coverage(value, transfer)


def normalize_plan_ids(value, story):
    """Only a literal, unambiguous master suffix alias; never fuzzy-match a new character."""
    known = {r["id"] for k in ("characters", "locations", "props") for r in story[k]}
    result = deepcopy(value)
    aliases = {}
    for row in result.get("assets", []):
        original = row.get("asset_id")
        if isinstance(original, str) and original not in known and original.endswith("_master"):
            canonical = original[:-len("_master")]
            if canonical in known:
                aliases[original] = canonical
                row["asset_id"] = canonical
    for material in result.get("materials", []):
        if isinstance(material.get("asset_ids"), list):
            material["asset_ids"] = [aliases.get(v, v) for v in material["asset_ids"]]
    if aliases:
        result["asset_id_aliases"] = aliases
    return result


def validate_image_review(value):
    if value.get("decision") not in ("pass", "revise"):
        raise ValueError("image_review_decision")
    for key in ("visible_facts", "blocking_issues", "risks"):
        if not isinstance(value.get(key), list):
            raise ValueError("image_review_list:" + key)
    if (value["decision"] == "revise") != bool(value["blocking_issues"]):
        raise ValueError("image_review_inconsistent")
    if value["decision"] == "revise" and not isinstance(value.get("repair_prompt"), str):
        raise ValueError("image_repair_prompt_missing")


def frame_review_intent(material, bible):
    """Use master images for identity; asset prose can also contain lifecycle states."""
    descriptions = {a: {"id": bible[a]["id"]} for a in material["asset_ids"]}
    current_fields = ("material_id", "asset_ids", "entry_state", "start_frame_prompt")
    current = {k: deepcopy(material[k]) for k in current_fields if k in material}
    return {"material": current, "asset_descriptions": descriptions,
            "state_scope": {"candidate_start_state": "material.entry_state and material.start_frame_prompt",
                            "identity_masters": "stable identity and geometry; poses and states may change"}}


def verify_parent(directory, video):
    result = load(directory / "result.json")
    if result.get("status") not in ("model_checked_screenplay_candidate", "screenplay_needs_review"):
        raise ValueError("screenplay_not_completed")
    lineage = load(directory / "input_lineage.json")
    if lineage["video_sha256"] != sha(video):
        raise ValueError("parent_reference_mismatch")
    for row in load(directory / "manifest.json"):
        path = (directory / row["path"]).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path) != row["sha256"]:
            raise ValueError("parent_manifest_mismatch:" + row["path"])
    return result


def load_reference_transfer(directory, video):
    """One parent-bound handoff, no late parallel re-interpretation. Legacy absence stays explicit."""
    path = directory / "reference_transfer.json"
    if not path.exists():
        return None
    transfer = load(path)
    reading_path = directory / "reference_reading.json"
    if (transfer.get("reference_sha256") != sha(video)
            or transfer.get("reading_sha256") != sha(reading_path)):
        raise ValueError("reference_transfer_parent_mismatch")
    expected = reference.build_transfer(load(reading_path), transfer["reference_duration_s"],
                                        sha(video), sha(reading_path))
    if transfer != expected:
        raise ValueError("reference_transfer_changed")
    return transfer


class Calls:
    def __init__(self, out, runner, frozen, limits):
        self.out, self.runner, self.frozen, self.limits = out, runner, frozen, limits
        self.root = out / "calls"
        self.root.mkdir(exist_ok=True)

    def call(self, name, prompt, payload, validator, *, media=None, images=(), tokens=6500, normalizer=None):
        media_shas = [sha(p) for p in ([media] if media else images)]
        identity = json_sha({"prompt": prompt, "payload": payload, "media_shas": media_shas,
                             "model_config": self.runner.cfg, "tokens": tokens})
        directory = self.root / name
        marker = directory / "request.json"
        parsed = directory / "parsed.json"
        if parsed.exists():
            if load(marker)["identity"] != identity:
                raise ValueError("model_cached_input_changed:" + name)
            value = load(parsed)
            validator(value)
            return value
        if marker.exists():
            if load(marker)["identity"] != identity:
                raise ValueError("model_cached_input_changed:" + name)
            failed_response = directory / "response.json"
            if failed_response.exists() and not name.endswith("_json_contract_recovery"):
                response = load(failed_response)
                if response.get("http_status") == 400 and (
                    "'messages' must contain the word 'json'" in response.get("body_text", "")):
                    # Definitive parameter rejection, not a lost reply or semantic retry.
                    # Keep the rejected request and count the one corrected call in the same budget.
                    value = self.call(name + "_json_contract_recovery", prompt, payload, validator,
                                      media=media, images=images, tokens=tokens, normalizer=normalizer)
                    write(directory / "transport_recovery.json", {
                        "failed_response_sha256": sha(failed_response),
                        "recovery_call": name + "_json_contract_recovery",
                        "reason": "provider_rejected_missing_JSON_format_instruction",
                        "creative_prompt_and_payload_unchanged": True})
                    write(parsed, value)
                    return value
            # An execution-only identifier adapter can recover an already returned response.
            # Preserve raw text and failed validation; do not issue a third paid request.
            if normalizer:
                repair = self.root / (name + "_protocol_repair")
                source = repair / "raw.txt" if (repair / "raw.txt").exists() else directory / "raw.txt"
                if source.exists():
                    raw_value = json.loads(source.read_text(encoding="utf-8"))
                    value = normalizer(raw_value)
                    validator(value)
                    write(directory / "normalization.json", {"source": str(source), "source_sha256": sha(source),
                        "normalized_sha256": json_sha(value), "rule": "literal_known_ID_master_suffix_only",
                        "code": self.frozen, "additional_model_calls": 0})
                    write(directory / "parsed.json", value)
                    return value
            raise RuntimeError("model_request_uncertain_or_invalid:no_automatic_replay:" + name)
        for attempt in range(2):
            if code_snapshot() != self.frozen:
                raise ValueError("code_changed_during_run")
            if len(list(self.root.glob("*/request.json"))) >= self.limits["model_calls"]:
                raise ValueError("production_model_budget")
            current = directory if attempt == 0 else self.root / (name + "_protocol_repair")
            current.mkdir(exist_ok=False)
            text = prompt + "\nInput: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if attempt:
                text = ("Repair JSON format/types only, not story or editorial decisions. Return the required object.\n"
                        + prompt + "\nInvalid response: " + raw + "\nError: " + error)
            current.joinpath("request.txt").write_text(text, encoding="utf-8")
            write(current / "request.json", {"identity": identity, "media_shas": media_shas,
                  "config": self.runner.cfg, "max_tokens": tokens, "code": self.frozen,
                  "requested_fps": 4 if media is not None and not attempt else None,
                  "actual_sampling": "provider_not_reported" if media is not None and not attempt else None})
            print(f"Production call {len(list(self.root.glob('*/request.json')))}/{self.limits['model_calls']}: "
                  + current.name, flush=True)
            try:
                kwargs = {"tokens": tokens}
                if media is not None and not attempt:
                    kwargs.update(media=media, fps=4)
                if images and not attempt:
                    kwargs.update(images=images)
                response = self.runner.request(text, **kwargs)
                write(current / "response.json", response)
                if response["http_status"] != 200:
                    raise RuntimeError("production_model_http:" + str(response["http_status"]))
                body = json.loads(response["body_text"])
                raw = body["choices"][0]["message"]["content"]
                current.joinpath("raw.txt").write_text(raw, encoding="utf-8")
                raw_json = raw.strip()
                if raw_json.startswith("```"):
                    raw_json = raw_json.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                value = json.loads(raw_json)
                if normalizer:
                    original = value
                    value = normalizer(value)
                    if original != value:
                        write(current / "normalization.json", {"raw_sha256": json_sha(original),
                            "normalized_sha256": json_sha(value), "rule": "literal_known_ID_master_suffix_only"})
                validator(value)
                write(current / "parsed.json", value)
                if attempt:
                    write(directory / "parsed.json", value)
                return value
            except (KeyError, TypeError, ValueError) as exc:
                error = str(exc)
                write(current / "validation_error.json", {"error": error})
                if attempt or "raw" not in locals():
                    raise
        raise ValueError("protocol_repair_exhausted")


def validate_object(value):
    if not isinstance(value, dict):
        raise ValueError("object_required")


def image_job_count(out):
    return sum(len(list(root.glob("*/submission.json"))) for root in out.glob("image_jobs*") if root.is_dir())


def cloud_restore_lineage(out, lineage):
    """Preserve the failed former backend, and account for its attempt instead of resetting budget."""
    original = out / "input_lineage.json"
    if not original.exists():
        return original
    prior = load(original)
    old_model, new_model = prior["models"]["image"], lineage["models"]["image"]
    if old_model == new_model:
        return original
    if old_model.get("backend") != "installed_image_skill_helper" or new_model.get("backend") != "aliyun_dashscope":
        raise ValueError("image_backend_transition_not_supported")
    restored = out / "aliyun_api_lineage.json"
    transition = out / "backend_restore.json"
    if restored.exists():
        if not transition.exists() or load(transition)["original_lineage_sha256"] != sha(original):
            raise ValueError("backend_restore_parent_changed")
        return restored  # existing API jobs continue under their already frozen new lineage
    for path in (out / "image_jobs").glob("*/submission.json"):
        job = load(path)
        if job.get("status") != "failed_or_uncertain" or job.get("path") or job.get("task_id"):
            raise ValueError("backend_restore_requires_resolving_existing_image_job")
    if list((out / "video_jobs").glob("*/submission.json")):
        raise ValueError("backend_restore_cannot_replace_existing_video_inputs")
    if not transition.exists():
        write(transition, {"reason": "user_explicitly_requested_original_Aliyun_and_H3_APIs",
            "original_lineage_sha256": sha(original), "new_image_config": new_model,
            "H3_config_unchanged": prior["models"]["video"] == lineage["models"]["video"],
            "original_image_attempts": image_job_count(out), "prior_model_calls_reused": True,
            "human_story_or_editing_guidance": False})
    return restored


def edit_sources(video, out, story, sources, calls, limits, reference_probe):
    """Omni observes real sources and owns every slice and edit; generation is not called."""
    reference_duration = float(reference_probe["format"]["duration"])
    transfer = load_reference_transfer(out.parent, video)
    if transfer is not None and abs(transfer["reference_duration_s"] - reference_duration) > 1 / editing.FPS:
        raise ValueError("reference_transfer_duration_mismatch")
    observations = []
    for source in sources:
        mid = source["material_id"]
        preview = out / (mid + "_review.mp4")
        if not preview.exists():
            editing.review_copy(Path(source["path"]), preview)

        def check_watch(value, expected=source):
            if value.get("material_id") != expected["material_id"] or not isinstance(value.get("events"), list):
                raise ValueError("source_watch_protocol")
            for event in value["events"]:
                a = editing.number(event.get("start_s"), 0, expected["measured_duration_s"], "event_start")
                b = editing.number(event.get("end_s"), 0, expected["measured_duration_s"], "event_end")
                if a >= b or not isinstance(event.get("visible"), str):
                    raise ValueError("source_watch_interval")
        observations.append(calls.call("watch_" + mid, prompts.WATCH,
            {"material_id": mid, "measured_duration_s": source["measured_duration_s"],
             "character_descriptions": story["characters"], "reference_transfer": transfer}, check_watch, media=preview))
    write(out / "source_observations.json", observations)

    def check_reference(value):
        if not all(isinstance(value.get(k), list) for k in ("observed_editing", "transferable_preferences", "uncertain")):
            raise ValueError("reference_editing_protocol")
        region = value.get("music_region")
        if region:
            a = editing.number(region.get("start_s"), 0, reference_duration, "music_start")
            b = editing.number(region.get("end_s"), 0, reference_duration, "music_end")
            if a >= b or not any(s["codec_type"] == "audio" for s in reference_probe["streams"]):
                raise ValueError("music_interval_or_audio_missing")
    if transfer is None:
        # Compatibility for frozen v1 parents only; never claim they used unified analysis.
        reference_edit = calls.call("reference_editing", prompts.REFERENCE_EDITING,
            {"measured_duration_s": reference_duration}, check_reference, media=video)
        reference_origin = "legacy_late_reference_call"
    else:
        reference_edit = transfer["editing"]
        reference_origin = "shared_initial_reference_analysis"
    write(out / "reference_editing.json", reference_edit)
    write(out / "reference_editing_lineage.json", {"origin": reference_origin,
        "reference_transfer_sha256": sha(out.parent / "reference_transfer.json") if transfer else None,
        "additional_full_reference_calls": 0 if transfer else 1})
    catalog = [{k: s[k] for k in ("material_id", "measured_duration_s", "unit_ids", "event_ids")} for s in sources]
    context = {"screenplay": story, "actual_source_observations": observations, "catalog": catalog,
        "reference_editing": reference_edit, "reference_transfer": transfer,
        "reference_duration_s": reference_duration}
    check_edit = lambda v: editing.compile_plan(v, sources, reference_edit.get("music_region"),
                                               reference_duration, transfer=transfer)
    plan = calls.call("edit_plan", prompts.EDIT, context, check_edit, tokens=8000)
    candidates = []
    for iteration in range(limits["edit_revisions"] + 1):
        compiled = check_edit(plan)
        write(out / f"edit_plan_{iteration}.json", plan)
        final = editing.render(compiled, out / f"render_{iteration}", video, reference_probe)
        audio = editing.audio_measurements(final)
        write(final.parent / "audio_measurements.json", audio)
        metrics = editing.edit_metrics(compiled, sources)
        write(final.parent / "edit_metrics.json", metrics)
        final_duration = float(probe(final)["format"]["duration"])

        def check_final(value):
            if value.get("decision") not in ("accept", "revise", "unable"):
                raise ValueError("final_review_protocol")
            if value["decision"] == "revise":
                check_edit(value.get("replacement_plan"))
            elif value.get("replacement_plan") is not None:
                raise ValueError("final_review_replacement_inconsistent")
            if transfer is not None:
                reference.validate_style_review(value.get("style_review"), transfer, final_duration)
        review = calls.call("watch_final_" + str(iteration) + "_audio_measured", prompts.FINAL_REVIEW,
            {"story": story, "inspected_sources": observations, "current_plan": plan,
             "measured_duration_s": final_duration,
             "reference_duration_s": reference_duration, "measured_audio": audio,
             "reference_transfer": transfer, "measured_edit_metrics": metrics,
             "revision_available": iteration < limits["edit_revisions"]},
            check_final, media=final.parent / "review.mp4", tokens=8000)
        review_path = out / f"final_review_{iteration}.json"
        if review_path.exists() and load(review_path) != review:
            write(out / "final_review_history" / (sha(review_path) + ".json"), load(review_path))
        write(review_path, review)
        candidates.append({"candidate_id": "render_" + str(iteration), "path": str(final),
                           "sha256": sha(final), "review": review, "plan": plan})
        if review["decision"] != "revise" or iteration == limits["edit_revisions"]:
            break
        plan = review["replacement_plan"]
    selected = candidates[-1]
    if len(candidates) > 1 and selected["review"]["decision"] != "accept":
        def check_selection(value):
            if value.get("selected_candidate_id") not in {c["candidate_id"] for c in candidates}:
                raise ValueError("final_selection_unknown_candidate")
            if not isinstance(value.get("reason"), str):
                raise ValueError("final_selection_reason")
        selection = calls.call("best_available_final_render", prompts.SELECT_RENDER,
            {"candidates": [{k: c[k] for k in ("candidate_id", "review", "plan")} for c in candidates]},
            check_selection, tokens=1800)
        selected = next(c for c in candidates if c["candidate_id"] == selection["selected_candidate_id"])
    else:
        selection = {"selected_candidate_id": selected["candidate_id"],
                     "reason": "Only available render or Omni accepted its revision."}
    write(out / "final_candidate_selection.json", selection)
    final, review = Path(selected["path"]), selected["review"]
    style_ok = transfer is not None and bool(transfer["editing"]["methods"]) and all(
        m["status"] == "observed" for m in transfer["editing"]["methods"]) and all(
        r["status"] in {"visible", "adapted"} for r in review["style_review"])
    style = {"status": "model_checked_candidate" if style_ok else "partial_or_unknown" if transfer else "not_evaluated",
             "reviewed_video_sha256": selected["sha256"], "reference_origin": reference_origin,
             "reference_transfer_sha256": sha(out.parent / "reference_transfer.json") if transfer else None,
             "review": review.get("style_review", [])}
    write(out / "style_transfer_result.json", style)
    accepted = review["decision"] == "accept" and (style_ok or transfer is None)
    return {"status": "model_checked_final_video" if accepted else "video_candidate_with_limitations",
        "final_video": str(final), "final_video_sha256": sha(final), "media_probe": probe(final),
        "model_review": review, "candidate_selection": selection, "image_jobs": image_job_count(out),
        "video_jobs": len(sources), "actual_model_calls": len(list(calls.root.glob("*/response.json"))),
        "human_creative_inputs": [], "audio_measurement_review": True,
        "style_transfer": style,
        "music_tempo_changed": False, "production_release_allowed": False}


def finish_existing_media(video, story_dir, *, runner=None, limits=None):
    """Resume editing completed jobs without constructing or calling paid generation backends."""
    video, story_dir = Path(video).resolve(), Path(story_dir).resolve()
    out = story_dir / "production"
    parent = verify_parent(story_dir, video)
    plan = load(out / "production_plan.json")
    sources = []
    for material in plan["materials"]:
        record = load(out / "video_jobs" / material["material_id"] / "submission.json")
        if record.get("status") != "completed" or sha(record["path"]) != record["sha256"]:
            raise ValueError("completed_source_invalid:" + material["material_id"])
        sources.append({**record, "measured_duration_s": float(probe(record["path"])["format"]["duration"]),
                        **{k: material[k] for k in ("material_id", "unit_ids", "event_ids")}})
    runner = runner or QwenAPI()
    limits = dict(limits or LIMITS)
    frozen = code_snapshot()
    lineage = {"reference_sha256": sha(video), "parent_manifest_sha256": sha(story_dir / "manifest.json"),
               "production_plan_sha256": sha(out / "production_plan.json"),
               "source_sha256s": {s["material_id"]: s["sha256"] for s in sources},
               "code": frozen, "model_config": runner.cfg, "budgets": limits,
               "content_policy": "best_available_with_limitations", "human_creative_inputs": [],
               "new_image_jobs": 0, "new_video_jobs": 0}
    marker = out / "editing_input_lineage.json"
    if marker.exists():
        prior_lineage = load(marker)
        if {k: v for k, v in prior_lineage.items() if k != "code"} != {k: v for k, v in lineage.items() if k != "code"}:
            raise ValueError("editing_frozen_inputs_changed")
        # A completed film keeps its own recorded execution version. Returning that artifact
        # does not rerun it under newly edited prompts or claim a new review took place.
        if (out / "result.json").exists():
            prior_result = load(out / "result.json")
            if (prior_result.get("status") in ("model_checked_final_video", "video_candidate_with_limitations")
                    and prior_result.get("audio_measurement_review")):
                if sha(prior_result["final_video"]) != prior_result["final_video_sha256"]:
                    raise ValueError("final_output_changed")
                return prior_result
        changed = {k for k in set(prior_lineage["code"]) | set(frozen)
                   if prior_lineage["code"].get(k) != frozen.get(k)}
        if any(k.replace("\\", "/") not in {"omni_story/editing.py", "omni_story/production.py"} for k in changed):
            raise ValueError("editing_semantic_code_changed")
        if changed:
            path = out / "editing_execution_code_revisions.json"
            history = load(path) if path.exists() else []
            revision = {"original_lineage_sha256": sha(marker), "execution_code": frozen,
                        "changed_files": sorted(changed), "model_inputs_and_budgets_unchanged": True}
            if revision not in history:
                write(path, [*history, revision])
    else:
        write(marker, lineage)
    if (out / "result.json").exists():
        prior = load(out / "result.json")
        if prior.get("status") in ("model_checked_final_video", "video_candidate_with_limitations"):
            if sha(prior["final_video"]) != prior["final_video_sha256"]:
                raise ValueError("final_output_changed")
            if prior.get("audio_measurement_review"):
                return prior
            # Keep the earlier media acceptance intact; new tool evidence gets a new call,
            # under the same shared budget, not a replay or silently changed old review.
            history = out / "result_history" / (sha(out / "result.json") + ".json")
            if not history.exists():
                write(history, prior)
    write(out / "source_inventory.json", sources)
    write(out / "editing_execution_state.json", {"status": "running", "pid": os.getpid()})
    calls = Calls(out, runner, frozen, limits)
    try:
        result = edit_sources(video, out, load(story_dir / "screenplay.json"), sources, calls, limits, probe(video))
        result.update(parent_screenplay_status=parent["status"], new_image_jobs=0, new_video_jobs=0)
        write(out / "result.json", result)
        write(out / "editing_execution_state.json", {"status": result["status"], "pid": os.getpid()})
        return result
    except Exception as exc:
        failure = {"status": "blocked", "reason": str(exc), "exception": type(exc).__name__,
                   "traceback": traceback.format_exc(), "stage": "editing_existing_media"}
        write(out / "failures" / (json_sha(failure) + ".json"), failure)
        write(out / "editing_execution_state.json", {**failure, "pid": os.getpid()})
        return failure


def continue_music_coverage(video, story_dir, result, *, runner=None):
    """One bounded Omni music decision; no image/video generator or human edit selection."""
    if result.get("status") not in ("model_checked_final_video", "video_candidate_with_limitations"):
        return result
    video, story_dir = Path(video).resolve(), Path(story_dir).resolve()
    out = story_dir / "production"
    verify_parent(story_dir, video)
    if sha(result["final_video"]) != result["final_video_sha256"]:
        raise ValueError("final_output_changed")
    sources = load(out / "source_inventory.json")
    if any(sha(s["path"]) != s["sha256"] for s in sources):
        raise ValueError("completed_source_changed")
    if result.get("music_coverage_continuation"):
        return result
    plan_path = out / ("edit_plan_" + result["candidate_selection"]["selected_candidate_id"].removeprefix("render_") + ".json")
    plan = load(plan_path)
    region = load(out / "reference_editing.json").get("music_region")
    compiled = editing.compile_plan(plan, sources, region, float(probe(video)["format"]["duration"]))
    if (not plan["music"]["enabled"] or plan["music"]["mode"] != "trim" or region is None
            or compiled["duration_s"] <= region["end_s"] - region["start_s"]):
        return result
    try:
        runner = runner or QwenAPI()
        frozen = code_snapshot()
        scope = {"parent_result_sha256": sha(out / "result.json"), "picture_sha256": result["final_video_sha256"],
                 "reference_sha256": sha(video), "plan_sha256": sha(plan_path),
                 "source_sha256s": {s["material_id"]: s["sha256"] for s in sources},
                 "code": frozen, "model_config": runner.cfg, "budgets": LIMITS,
                 "scope": "music_only_picture_locked", "new_image_jobs": 0, "new_video_jobs": 0}
        marker = out / "music_coverage_scope.json"
        if marker.exists() and load(marker) != scope:
            raise ValueError("music_coverage_inputs_changed")
        write(marker, scope)
        calls = Calls(out, runner, frozen, LIMITS)
        def check_music(value):
            if value.get("decision") not in ("keep", "adjust") or not isinstance(value.get("reason"), str):
                raise ValueError("music_coverage_decision_invalid")
            if value["decision"] == "keep" and value.get("music") != plan["music"]:
                raise ValueError("music_keep_settings_changed")
            if not isinstance(value.get("limitations"), list):
                raise ValueError("music_coverage_limitations_invalid")
            editing.compile_plan({**plan, "music": value.get("music")}, sources, region, compiled["reference_duration_s"])
        decision = calls.call("music_coverage_decision", prompts.MUSIC_REPAIR,
            {"film_duration_s": compiled["duration_s"], "music_region": region,
             "music_excerpt_duration_s": region["end_s"] - region["start_s"], "current_music": plan["music"],
             "measured_audio": editing.audio_measurements(result["final_video"])}, check_music,
            media=Path(result["final_video"]).parent / "review.mp4", tokens=2200)
        write(out / "music_coverage_decision.json", decision)
        final = Path(result["final_video"])
        if decision["decision"] == "adjust":
            revised = {**plan, "music": decision["music"]}
            write(out / "edit_plan_music_1.json", revised)
            final = editing.mux_music(editing.compile_plan(revised, sources, region, compiled["reference_duration_s"]),
                                      final, out / "render_music_1", video)
        measured = editing.audio_measurements(final)
        write(out / "music_coverage_verification.json", {"final_sha256": sha(final), "measured_audio": measured,
            "picture_slices_unchanged": True, "music_tempo_changed": False, "new_image_jobs": 0, "new_video_jobs": 0})
        # The earlier review watched the earlier mix, not the newly remuxed film.
        updated = {**result, "status": "video_candidate_with_limitations" if final != Path(result["final_video"]) else result["status"],
            "final_video": str(final), "final_video_sha256": sha(final), "media_probe": probe(final),
            "music_coverage_continuation": decision, "actual_model_calls": len(list(calls.root.glob("*/response.json"))),
            "prior_model_review_video_sha256": result["final_video_sha256"],
            "final_mix_model_review": "not_run" if final != Path(result["final_video"]) else "unchanged_mix",
            "measured_audio": measured}
        if final != Path(result["final_video"]) and "style_transfer" in result:
            updated["style_transfer"] = {**result["style_transfer"], "status": "prior_mix_only",
                                          "new_mix_review": "not_run"}
        write(out / "result_history" / (sha(out / "result.json") + ".json"), result)
        write(out / "result.json", updated)
        write(out / "editing_execution_state.json", {"status": updated["status"], "stage": "music_finished", "pid": os.getpid()})
        return updated
    except Exception as exc:
        write(out / "music_coverage_failure.json", {"reason": str(exc), "traceback": traceback.format_exc(),
            "existing_video_preserved": result["final_video"], "model_budget_not_reset": True})
        return result


def execute_production(video, story_dir, *, runner=None, image_backend=None, video_backend=None, limits=None):
    video, story_dir = Path(video).resolve(), Path(story_dir).resolve()
    out = story_dir / "production"
    out.mkdir(exist_ok=True)
    if (out / "run_failure.json").exists():
        previous_failure = load(out / "run_failure.json")
        archived_failure = out / "failures" / (json_sha(previous_failure) + ".json")
        if not archived_failure.exists():
            write(archived_failure, previous_failure)
    limits = dict(limits or LIMITS)
    try:
        parent = verify_parent(story_dir, video)
        story = load(story_dir / "screenplay.json")
        transfer = load_reference_transfer(story_dir, video)
        reference_probe = probe(video)
        reference_duration = float(reference_probe["format"]["duration"])
        runner = runner or QwenAPI()
        image_backend = image_backend or AliyunImages()
        video_backend = video_backend or MiniMaxH3()
        lineage = {"reference_sha256": sha(video), "screenplay_sha256": sha(story_dir / "screenplay.json"),
            "parent_manifest_sha256": sha(story_dir / "manifest.json"), "code": code_snapshot(),
            "models": {"omni": runner.cfg, "image": image_backend.cfg, "video": video_backend.cfg},
            "budgets": limits, "human_creative_inputs": [], "decision_owner": "Omni",
            "executor": "media tools only"}
        frozen_file = cloud_restore_lineage(out, lineage)
        if frozen_file.exists():
            prior = load(frozen_file)
            if {k: v for k, v in prior.items() if k != "code"} != {k: v for k, v in lineage.items() if k != "code"}:
                raise ValueError("production_frozen_inputs_changed")
            changed = {k for k in set(prior["code"]) | set(lineage["code"])
                       if prior["code"].get(k) != lineage["code"].get(k)}
            execution_only = {"omni_story/production.py", "omni_story/editing.py", "omni_story/media_backends.py",
                              "omni_story/api.py"}
            if any(k.replace("\\", "/") not in execution_only for k in changed):
                raise ValueError("production_semantic_code_changed")
            if changed:
                revisions = out / "execution_code_revisions.json"
                history = load(revisions) if revisions.exists() else []
                revision = {"parent_code": prior["code"], "execution_code": lineage["code"],
                            "changed_files": sorted(changed), "budgets_unchanged": True,
                            "creative_prompts_unchanged": True,
                            "transport_JSON_hint_corrected": "omni_story/api.py" in {k.replace("\\", "/") for k in changed}}
                if revision not in history:
                    write(revisions, [*history, revision])
        else:
            write(frozen_file, lineage)
        if (out / "result.json").exists() and load(out / "result.json").get("status") in (
                "model_checked_final_video", "video_candidate_with_limitations"):
            old = load(out / "result.json")
            if sha(old["final_video"]) != old["final_video_sha256"]:
                raise ValueError("final_output_changed")
            return old
        calls = Calls(out, runner, lineage["code"], limits)
        write(out / "execution_state.json", {"status": "running", "pid": os.getpid(), "stage": "production_plan"})
        plan = calls.call("production_plan", prompts.PLAN, {"screenplay": story,
            "budgets": limits, "reference_duration_s": reference_duration, "reference_transfer": transfer},
            lambda v: validate_plan(v, story, limits, transfer), tokens=9000,
            normalizer=lambda v: normalize_plan_ids(v, story))
        write(out / "production_plan.json", plan)
        repairs_file = out / "repair_budget.json"
        repairs = load(repairs_file) if repairs_file.exists() else {"used": [], "maximum": limits["image_repairs"]}
        assets, sources = {}, []
        image_jobs = out / ("image_jobs_aliyun" if image_backend.cfg.get("backend") == "aliyun_dashscope" else "image_jobs")

        def create_and_review(name, prompt, references, intent, *, kind="frame"):
            write(out / "execution_state.json", {"status": "running", "pid": os.getpid(), "stage": name})
            job = image_jobs / name
            if not (job / "submission.json").exists() and image_job_count(out) >= limits["image_jobs"]:
                raise ValueError("image_job_budget_exhausted")
            width, height = editing.canvas(reference_probe)
            image_size = "1536x1024" if width >= height else "1024x1536"
            if image_backend.cfg.get("backend") == "aliyun_dashscope":
                image_size = {"character": "928x1664", "location": "1664x928", "prop": "1328x1328"}.get(
                    kind, "1664x928" if width >= height else "928x1664")
            backend_options = {"kind": kind} if image_backend.cfg.get("backend") == "aliyun_dashscope" else {}
            result = image_backend.generate(job, prompt, references, size=image_size, **backend_options)
            candidate = Path(result["path"])
            review_payload = {"intent": intent, "labels": ["candidate"] + ["identity master " + str(i + 1)
                              for i in range(len(references))]}
            review_name = name + ("_coarse_current_frame_visual_identity_review" if "material" in intent else "_coarse_review")
            review = calls.call(review_name, prompts.IMAGE_REVIEW, review_payload,
                                validate_image_review, images=[candidate, *references])
            candidates = [{**result, "candidate_id": name, "review": review}]
            if review["decision"] == "revise":
                repair_name = name + "_repair"
                can_repair = ((repair_name in repairs["used"] or len(repairs["used"]) < repairs["maximum"])
                              and ((image_jobs / repair_name / "submission.json").exists()
                                   or image_job_count(out) < limits["image_jobs"]))
                if not can_repair:
                    # One real candidate still exists. Preserve its rejection, not a fake pass.
                    return {**candidates[0], "selection": {"selected_candidate_id": name,
                            "reason": "Only available image; semantic repair budget exhausted."}}
                if repair_name not in repairs["used"]:
                    repairs["used"].append(repair_name)
                    write(repairs_file, repairs)
                job = image_jobs / repair_name
                if not (job / "submission.json").exists() and image_job_count(out) >= limits["image_jobs"]:
                    raise ValueError("image_job_budget_exhausted")
                result = image_backend.generate(job, review["repair_prompt"], [candidate, *references],
                                                size=image_size, **backend_options)
                # A rejected candidate is not an identity master or a consecutive video frame.
                # Re-check the repaired image against the same fixed masters, never the old error.
                review = calls.call(repair_name + "_coarse_current_frame_visual_identity_review", prompts.IMAGE_REVIEW,
                    review_payload, validate_image_review, images=[Path(result["path"]), *references])
                candidates.append({**result, "candidate_id": repair_name, "review": review})
                if review["decision"] != "pass":
                    def check_choice(value):
                        if value.get("selected_candidate_id") not in {c["candidate_id"] for c in candidates}:
                            raise ValueError("image_selection_unknown_candidate")
                        if not isinstance(value.get("reason"), str):
                            raise ValueError("image_selection_reason")
                    choice = calls.call(name + "_best_available", prompts.SELECT_IMAGE,
                        {"intent": intent, "candidates": [{"candidate_id": c["candidate_id"], "review": c["review"]}
                                                         for c in candidates],
                         "labels": [c["candidate_id"] for c in candidates]}, check_choice,
                        images=[Path(c["path"]) for c in candidates], tokens=1800)
                    write(out / "image_selections" / (name + ".json"), choice)
                    return {**next(c for c in candidates if c["candidate_id"] == choice["selected_candidate_id"]),
                            "selection": choice}
            return {**result, "review": review}

        bible = {r["id"]: r for k in ("characters", "locations", "props") for r in story[k]}
        kinds = {r["id"]: kind for group, kind in (("characters", "character"), ("locations", "location"), ("props", "prop"))
                 for r in story[group]}
        for asset in plan["assets"]:
            asset_id = asset["asset_id"]
            assets[asset_id] = create_and_review("master_" + asset_id, asset["master_prompt"], [], bible[asset_id],
                                                kind=kinds[asset_id])
        write(out / "asset_inventory.json", assets)
        frames = {}
        for material in plan["materials"]:
            labels = material["asset_ids"]
            refs = [Path(assets[a]["path"]) for a in labels]
            prompt = "Labeled reference identities, in image order: " + ", ".join(labels) + ".\n" + material["start_frame_prompt"]
            frames[material["material_id"]] = create_and_review("frame_" + material["material_id"], prompt, refs,
                frame_review_intent(material, bible))
        write(out / "start_frame_inventory.json", frames)
        w, h = editing.canvas(reference_probe)
        ratio = "16:9" if w > h else "9:16" if h > w else "1:1"
        write(out / "execution_state.json", {"status": "running", "pid": os.getpid(), "stage": "H3_materials"})

        def generate(material):
            mid = material["material_id"]
            result = video_backend.generate(out / "video_jobs" / mid, material["video_prompt"],
                Path(frames[mid]["path"]), material["generation_duration_s"], ratio)
            return {**result, "material_id": mid, "unit_ids": material["unit_ids"], "event_ids": material["event_ids"]}

        # Concurrent independent materials; no speculative retries or automatic video repair.
        with ThreadPoolExecutor(max_workers=3) as pool:
            for result in pool.map(generate, plan["materials"]):
                # The request length is not proof of generated-media length.
                result["measured_duration_s"] = float(probe(result["path"])["format"]["duration"])
                sources.append(result)
                write(out / "source_inventory.json", sources)
        result = edit_sources(video, out, story, sources, calls, limits, reference_probe)
        result["parent_screenplay_status"] = parent["status"]
        write(out / "result.json", result)
        write(out / "execution_state.json", {"status": result["status"], "pid": os.getpid(), "stage": "finished"})
        manifest = [{"path": str(p.relative_to(out)), "size": p.stat().st_size, "sha256": sha(p)}
                    for p in sorted(out.rglob("*")) if p.is_file() and p.name != "manifest.json"]
        write(out / "manifest.json", manifest)
        return result
    except Exception as exc:
        result = {"status": "blocked", "reason": str(exc), "exception": type(exc).__name__}
        failure = {**result, "traceback": traceback.format_exc()}
        write(out / "failures" / (json_sha(failure) + ".json"), failure)
        write(out / "run_failure.json", failure)
        write(out / "execution_state.json", {**result, "pid": os.getpid()})
        return result


def full_run(video, output):
    output = Path(output).resolve()
    if not (output / "result.json").exists():
        result = execute(video, output)
        if result["status"] not in ("model_checked_screenplay_candidate", "screenplay_needs_review"):
            return result
    production_dir = output / "production"
    if (production_dir / "production_plan.json").exists():
        materials = load(production_dir / "production_plan.json")["materials"]
        records = [production_dir / "video_jobs" / m["material_id"] / "submission.json" for m in materials]
        if records and all(r.exists() and load(r).get("status") == "completed" for r in records):
            return continue_music_coverage(video, output, finish_existing_media(video, output))
    return continue_music_coverage(video, output, execute_production(video, output))
