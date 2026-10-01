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
from . import editing, production_prompts as prompts
from .media_backends import ImageHelper, MiniMaxH3, load
from .pipeline import code_snapshot, execute, json_sha, probe, sha, write

LIMITS = {"model_calls": 36, "image_jobs": 12, "video_jobs": 6,
          "image_repairs": 1, "edit_revisions": 1, "protocol_repairs_per_call": 1}


def ids(rows, key):
    values = [row[key] for row in rows]
    if not values or len(set(values)) != len(values) or any(
        not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", v) for v in values):
        raise ValueError("production_ids_invalid:" + key)
    return set(values)


def validate_plan(value, story, limits):
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


def verify_parent(directory, video):
    result = load(directory / "result.json")
    if result.get("status") != "model_checked_screenplay_candidate":
        raise ValueError("screenplay_not_completed")
    lineage = load(directory / "input_lineage.json")
    if lineage["video_sha256"] != sha(video):
        raise ValueError("parent_reference_mismatch")
    for row in load(directory / "manifest.json"):
        path = (directory / row["path"]).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path) != row["sha256"]:
            raise ValueError("parent_manifest_mismatch:" + row["path"])
    return result


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
                  "config": self.runner.cfg, "max_tokens": tokens, "code": self.frozen})
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


def execute_production(video, story_dir, *, runner=None, image_backend=None, video_backend=None, limits=None):
    video, story_dir = Path(video).resolve(), Path(story_dir).resolve()
    out = story_dir / "production"
    out.mkdir(exist_ok=True)
    limits = dict(limits or LIMITS)
    try:
        parent = verify_parent(story_dir, video)
        story = load(story_dir / "screenplay.json")
        reference_probe = probe(video)
        reference_duration = float(reference_probe["format"]["duration"])
        runner = runner or QwenAPI()
        image_backend = image_backend or ImageHelper()
        video_backend = video_backend or MiniMaxH3()
        lineage = {"reference_sha256": sha(video), "screenplay_sha256": sha(story_dir / "screenplay.json"),
            "parent_manifest_sha256": sha(story_dir / "manifest.json"), "code": code_snapshot(),
            "models": {"omni": runner.cfg, "image": image_backend.cfg, "video": video_backend.cfg},
            "budgets": limits, "human_creative_inputs": [], "decision_owner": "Omni",
            "executor": "media tools only"}
        frozen_file = out / "input_lineage.json"
        if frozen_file.exists():
            prior = load(frozen_file)
            if {k: v for k, v in prior.items() if k != "code"} != {k: v for k, v in lineage.items() if k != "code"}:
                raise ValueError("production_frozen_inputs_changed")
            changed = {k for k in set(prior["code"]) | set(lineage["code"])
                       if prior["code"].get(k) != lineage["code"].get(k)}
            execution_only = {"omni_story/production.py", "omni_story/editing.py", "omni_story/media_backends.py"}
            if any(k.replace("\\", "/") not in execution_only for k in changed):
                raise ValueError("production_semantic_code_changed")
            if changed:
                revisions = out / "execution_code_revisions.json"
                history = load(revisions) if revisions.exists() else []
                revision = {"parent_code": prior["code"], "execution_code": lineage["code"],
                            "changed_files": sorted(changed), "budgets_and_model_inputs_unchanged": True}
                if revision not in history:
                    write(revisions, [*history, revision])
        else:
            write(frozen_file, lineage)
        if (out / "result.json").exists() and load(out / "result.json").get("status") == "model_checked_final_video":
            old = load(out / "result.json")
            if sha(old["final_video"]) != old["final_video_sha256"]:
                raise ValueError("final_output_changed")
            return old
        calls = Calls(out, runner, lineage["code"], limits)
        write(out / "execution_state.json", {"status": "running", "pid": os.getpid(), "stage": "production_plan"})
        plan = calls.call("production_plan", prompts.PLAN, {"screenplay": story,
            "budgets": limits, "reference_duration_s": reference_duration},
            lambda v: validate_plan(v, story, limits), tokens=9000,
            normalizer=lambda v: normalize_plan_ids(v, story))
        write(out / "production_plan.json", plan)
        repairs_file = out / "repair_budget.json"
        repairs = load(repairs_file) if repairs_file.exists() else {"used": [], "maximum": limits["image_repairs"]}
        assets, sources = {}, []
        image_jobs = out / "image_jobs"

        def create_and_review(name, prompt, references, intent):
            write(out / "execution_state.json", {"status": "running", "pid": os.getpid(), "stage": name})
            job = image_jobs / name
            if not (job / "submission.json").exists() and len(list(image_jobs.glob("*/submission.json"))) >= limits["image_jobs"]:
                raise ValueError("image_job_budget_exhausted")
            width, height = editing.canvas(reference_probe)
            image_size = "1536x1024" if width >= height else "1024x1536"
            result = image_backend.generate(job, prompt, references, size=image_size)
            candidate = Path(result["path"])
            review_payload = {"intent": intent, "labels": ["candidate"] + ["identity master " + str(i + 1)
                              for i in range(len(references))]}
            review = calls.call(name + "_review", prompts.IMAGE_REVIEW, review_payload,
                                validate_image_review, images=[candidate, *references])
            if review["decision"] == "revise":
                repair_name = name + "_repair"
                if repair_name not in repairs["used"]:
                    if len(repairs["used"]) >= repairs["maximum"]:
                        raise ValueError("image_semantic_repair_budget_exhausted")
                    repairs["used"].append(repair_name)
                    write(repairs_file, repairs)
                job = image_jobs / repair_name
                if not (job / "submission.json").exists() and len(list(image_jobs.glob("*/submission.json"))) >= limits["image_jobs"]:
                    raise ValueError("image_job_budget_exhausted")
                result = image_backend.generate(job, review["repair_prompt"], [candidate, *references],
                                                size=image_size)
                review = calls.call(repair_name + "_review", prompts.IMAGE_REVIEW,
                    {"intent": intent, "labels": ["repaired candidate", "prior candidate"]},
                    validate_image_review, images=[Path(result["path"]), candidate])
                if review["decision"] != "pass":
                    raise ValueError("image_still_explicitly_blocked:" + name)
            return {**result, "review": review}

        bible = {r["id"]: r for k in ("characters", "locations", "props") for r in story[k]}
        for asset in plan["assets"]:
            asset_id = asset["asset_id"]
            assets[asset_id] = create_and_review("master_" + asset_id, asset["master_prompt"], [], bible[asset_id])
        write(out / "asset_inventory.json", assets)
        frames = {}
        for material in plan["materials"]:
            labels = material["asset_ids"]
            refs = [Path(assets[a]["path"]) for a in labels]
            prompt = "Labeled reference identities, in image order: " + ", ".join(labels) + ".\n" + material["start_frame_prompt"]
            frames[material["material_id"]] = create_and_review("frame_" + material["material_id"], prompt, refs,
                {"material": material, "asset_descriptions": {a: bible[a] for a in labels}})
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
                sources.append(result)
                write(out / "source_inventory.json", sources)
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
                 "character_descriptions": story["characters"]}, check_watch, media=preview))
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
        reference = calls.call("reference_editing", prompts.REFERENCE_EDITING,
            {"measured_duration_s": reference_duration}, check_reference, media=video)
        write(out / "reference_editing.json", reference)
        catalog = [{k: s[k] for k in ("material_id", "measured_duration_s", "unit_ids", "event_ids")} for s in sources]
        context = {"screenplay": story, "actual_source_observations": observations, "catalog": catalog,
            "reference_editing": reference, "reference_duration_s": reference_duration}
        check_edit = lambda v: editing.compile_plan(v, sources, reference.get("music_region"), reference_duration)
        plan = calls.call("edit_plan", prompts.EDIT, context, check_edit, tokens=8000)
        for iteration in range(limits["edit_revisions"] + 1):
            compiled = editing.compile_plan(plan, sources, reference.get("music_region"), reference_duration)
            write(out / f"edit_plan_{iteration}.json", plan)
            final = editing.render(compiled, out / f"render_{iteration}", video, reference_probe)

            def check_final(value):
                if value.get("decision") not in ("accept", "revise", "unable"):
                    raise ValueError("final_review_protocol")
                if value["decision"] == "revise":
                    check_edit(value.get("replacement_plan"))
                elif value.get("replacement_plan") is not None:
                    raise ValueError("final_review_replacement_inconsistent")
            review = calls.call("watch_final_" + str(iteration), prompts.FINAL_REVIEW,
                {"story": story, "inspected_sources": observations, "current_plan": plan,
                 "measured_duration_s": float(probe(final)["format"]["duration"]),
                 "reference_duration_s": reference_duration, "revision_available": iteration < limits["edit_revisions"]},
                check_final, media=final.parent / "review.mp4", tokens=8000)
            write(out / f"final_review_{iteration}.json", review)
            if review["decision"] != "revise" or iteration == limits["edit_revisions"]:
                break
            plan = review["replacement_plan"]
        result = {"status": "model_checked_final_video" if review["decision"] == "accept" else "video_candidate_with_limitations",
            "final_video": str(final), "final_video_sha256": sha(final), "media_probe": probe(final),
            "model_review": review, "image_jobs": len(list(image_jobs.glob("*/submission.json"))),
            "video_jobs": len(sources), "actual_model_calls": len(list(calls.root.glob("*/response.json"))),
            "parent_screenplay_status": parent["status"], "human_creative_inputs": [],
            "music_tempo_changed": False, "production_release_allowed": False}
        write(out / "result.json", result)
        write(out / "execution_state.json", {"status": result["status"], "pid": os.getpid(), "stage": "finished"})
        manifest = [{"path": str(p.relative_to(out)), "size": p.stat().st_size, "sha256": sha(p)}
                    for p in sorted(out.rglob("*")) if p.is_file() and p.name != "manifest.json"]
        write(out / "manifest.json", manifest)
        return result
    except Exception as exc:
        result = {"status": "blocked", "reason": str(exc), "exception": type(exc).__name__}
        write(out / "run_failure.json", {**result, "traceback": traceback.format_exc()})
        write(out / "execution_state.json", {**result, "pid": os.getpid()})
        return result


def full_run(video, output):
    output = Path(output).resolve()
    if not (output / "result.json").exists():
        result = execute(video, output)
        if result["status"] != "model_checked_screenplay_candidate":
            return result
    return execute_production(video, output)
