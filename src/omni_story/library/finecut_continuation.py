"""One explicitly authorized fine-cut continuation in the original task folder.

The old reference and library observations remain model estimates. New craft
observations supplement them without converting them into canonical facts. This
module never grants permission, searches new library windows, or chooses cuts.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from ..contract import require
from . import active_finecut as finecut, contracts, semantic_audit as audit
from . import semantic_pipeline, semantic_prompts
from .editing import compact_timeline, validate_candidate_dispositions, validate_method_review
from .media import inventory_sources, prepare_window, sha256_file
from .pipeline import CodexMCP, _catalog, _read, _window_context
from .render import compile_library_plan, render_library_video, validate_caption_layout
from .shot_timeline import associate_edl_boundaries, detect_shot_timeline
from .state import LibraryStopped, file_lock, json_sha, write_json

POLICY = "authorized_active_finecut_continuation_v2"
RESULT = "active_finecut_continuation_result"
RESULT_FILE = "result_active_finecut_4.json"
FOLDER = "active_finecut_continuation_v1"


def _artifact(state, name):
    records = state.data["artifacts"].get(name, [])
    require(len(records) == 1, "finecut_continuation:one_artifact_required:" + name)
    value = _read(records[0]["path"])
    require(json_sha(value) == records[0]["sha256"], "finecut_continuation:artifact_changed:" + name)
    return value


def _status(folder, stage, **details):
    # The previous task's current_status is also retained as historical evidence.
    value = {"stage": stage, **details}
    write_json(folder / "current_status.json", value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _canonical_reference(state, ref):
    reading = _read(state.output / "reference_reading.json")
    candidates = [state.output / "artifacts/editing_revision_v1/reference_methods.json",
                  state.output / "editing_reference_v2.json"]
    path = next((p for p in candidates if p.is_file()), None)
    require(path is not None, "finecut_continuation:completed_canonical_methods_required")
    methods = _read(path)
    contracts.validate_reference(reading, ref["sha256"], ref["duration_s"])
    contracts.validate_editing_reference(methods, ref["sha256"], ref["duration_s"], reading)
    for value in (reading, methods):
        require(any(c["status"] == "received"
                    and (state.output / "calls" / c["id"] / "parsed.json").is_file()
                    and _read(state.output / "calls" / c["id"] / "parsed.json") == value
                    for c in state.data["calls"]), "finecut_continuation:canonical_model_binding_missing")
    return reading, methods


def _completed_windows(state, catalog, policy):
    windows = deepcopy(_read(state.output / "watched_windows.json"))
    baseline_calls = _read(policy["baseline_state_path"])["calls"]
    require(0 < len(windows) <= state.data["input_lock"]["configuration"]["max_fine"], "finecut_continuation:window_limit")
    require(len({w["window_id"] for w in windows}) == len(windows), "finecut_continuation:duplicate_window")
    sources = {s["source_id"]: s for s in catalog["sources"]}
    for window in windows:
        require(window.get("status") == "watched", "finecut_continuation:incomplete_window")
        require(window["source_id"] in sources and window["source_sha256"] == sources[window["source_id"]]["sha256"],
                "finecut_continuation:window_source_changed")
        require(sha256_file(window["path"]) == window["sha256"], "finecut_continuation:window_media_changed")
        lineage = _read(Path(window["path"]).parent / "lineage.json")
        require(all(lineage[k] == window[k] for k in ("source_id", "source_sha256", "source_start_s", "source_end_s")),
                "finecut_continuation:window_lineage_changed")
        require(any(c["status"] == "received" and c["name"].startswith("fine_")
                    and (state.output / "calls" / c["id"] / "parsed.json").is_file()
                    and _read(state.output / "calls" / c["id"] / "parsed.json") == window["observation"]
                    for c in baseline_calls), "finecut_continuation:window_model_binding_missing")
    return windows


def _craft_evidence(state, ref):
    from .extension_budget import historical_state
    from .reference_craft import ALLOCATION, RESULT as CRAFT_RESULT, _verify_completed, load_knowledge
    historical = historical_state(state)
    allocation = _artifact(historical, ALLOCATION)
    craft = _artifact(historical, CRAFT_RESULT)
    knowledge = load_knowledge(allocation["knowledge_snapshot"])
    require(knowledge["sha256"] == allocation["knowledge_sha256"], "finecut_continuation:craft_knowledge_changed")
    _verify_completed(historical, craft, ref, knowledge)
    return {"status": craft["status"], "reference_sha256": craft["reference_sha256"],
        "knowledge_sha256": craft["knowledge_sha256"], "model_bindings": craft["model_bindings"],
        "analysis": craft["analysis"], "inspection": craft["inspection"],
        "evidence_limit": craft["evidence_limit"],
        "binding_role": "Additional fallible model observations, not canonical method conversion or movie evidence."}, craft


def _handbook_view(state, policy):
    # Only the prompt helper reads this view. No old forward-policy installation
    # or state mutation is performed; the handbook lives in the new permission.
    from .extension_budget import AUTHORIZATION
    require(sha256_file(policy["handbook_path"]) == policy["handbook_sha256"],
            "finecut_continuation:handbook_changed")
    return SimpleNamespace(data={"artifacts": {
        finecut.POLICY_ARTIFACT: state.data["artifacts"][AUTHORIZATION]}})


def _known_protocol_failure(state, name, error):
    require(str(error) == "model_protocol_repair_exhausted:" + name,
            "finecut_continuation:only_known_exhausted_stage_can_deliver_incomplete_candidate")
    original = next(c for c in state.data["calls"] if c["name"] == name and not c.get("repair_of"))
    repairs = [c for c in state.data["calls"] if c.get("repair_of") == original["id"]]
    require(len(repairs) == 1 and original["status"] == repairs[0]["status"] == "received",
            "finecut_continuation:unknown_stage_cannot_be_delivered_as_completed")
    bindings = []
    for call in (original, repairs[0]):
        folder = state.output / "calls" / call["id"]
        request, response, failure = (_read(folder / filename) for filename in
                                     ("request.json", "response.json", "protocol_failure.json"))
        require(json_sha(request) == call["request_sha256"] and json_sha(response) == call["response_sha256"]
                and not (folder / "parsed.json").exists(), "finecut_continuation:failed_stage_records_changed")
        bindings.append({"call_id": call["id"], "request_sha256": call["request_sha256"],
                         "response_sha256": call["response_sha256"], "failure_sha256": json_sha(failure)})
    return {"stage": name, "status": "incomplete_protocol_failure", "calls": bindings,
            "error": str(error), "model_verdict": None, "no_additional_request": True}


def _completed_result(state):
    if not state.data["artifacts"].get(RESULT):
        require(not (state.output / RESULT_FILE).exists(), "finecut_continuation:unbound_result_file")
        return None
    record = _artifact(state, RESULT)
    result = _read(state.output / RESULT_FILE)
    require(json_sha(result) == record["result_sha256"], "finecut_continuation:result_changed")
    for row in record["completed_files"]:
        require(Path(row["path"]).is_file() and sha256_file(row["path"]) == row["sha256"],
                "finecut_continuation:completed_file_changed:" + row["path"])
    return result


def execute_finecut_continuation(reference, library, output):
    from .extension_budget import get_authorization, stage_state, GOAL_AUTHORIZATION, _goal_snapshot
    # Later Goal runs freeze this complete or incomplete attempt. Cache-only
    # reads must not instantiate a writable state, execution lock or MCP queue.
    output_path = Path(output).resolve(strict=True)
    live = _read(output_path / 'library_state.json')
    if live['artifacts'].get(GOAL_AUTHORIZATION):
        frozen, _ = _goal_snapshot(output_path, live)
        state = SimpleNamespace(output=output_path, data=frozen)
        get_authorization(state)
        require((output_path / 'reference_catalog/inventory.json').is_file()
                and (output_path / 'catalog/inventory.json').is_file(), 'finecut_continuation:frozen_catalog_missing')
        ref = _catalog(reference, output_path / 'reference_catalog')['sources'][0]
        catalog = _catalog(library, output_path / 'catalog')
        require(ref['sha256'] == frozen['input_lock']['reference_sha256'] and
                [{k:s[k] for k in ('source_id','sha256')} for s in catalog['sources']] == frozen['input_lock']['library_sources'],
                'finecut_continuation:frozen_input_changed')
        completed = _completed_result(state)
        if completed:
            return completed
        raise LibraryStopped('finecut_continuation:round4_frozen_incomplete_read_only_use_goal_entry')
    state = stage_state(output)  # Missing permission is rejected before lock/write/MCP.
    get_authorization(state)
    with file_lock(state.output / ".active_finecut_continuation.lock"):
        state = stage_state(output)
        policy = get_authorization(state)
        ref = _catalog(reference, state.output / "reference_catalog")["sources"][0]
        catalog = _catalog(library, state.output / "catalog")
        require(ref["sha256"] == state.data["input_lock"]["reference_sha256"], "finecut_continuation:reference_changed")
        require([{k: s[k] for k in ("source_id", "sha256")} for s in catalog["sources"]]
                == state.data["input_lock"]["library_sources"], "finecut_continuation:library_changed")
        completed = _completed_result(state)
        if completed:
            return completed
        try:
            return _execute(reference, library, state, policy)
        except Exception as error:
            # Technical failures preserve raw calls. This does not create a film
            # result or pretend an uncertain paid request has completed.
            state.set_artifact("active_finecut_continuation_failure", {"policy": POLICY,
                "error": str(error), "type": type(error).__name__, "usage": state.usage(),
                "no_automatic_paid_replay": True, "old_results_preserved": True})
            raise


def _execute(reference, library, state, policy):
    from .extension_budget import get_authorization
    output = state.output
    catalog = _catalog(library, output / "catalog")
    ref = _catalog(reference, output / "reference_catalog")["sources"][0]
    require(ref["sha256"] == state.data["input_lock"]["reference_sha256"], "finecut_continuation:reference_changed")
    require([{k: s[k] for k in ("source_id", "sha256")} for s in catalog["sources"]]
            == state.data["input_lock"]["library_sources"], "finecut_continuation:library_changed")
    reading, methods = _canonical_reference(state, ref)
    windows = _completed_windows(state, catalog, policy)
    craft_context, craft = _craft_evidence(state, ref)
    reference_media = craft["overview_media"]["path"]  # Full-duration, normal-speed reference proxy.
    require(craft["overview_media"]["source_start_s"] == 0
            and craft["overview_media"]["source_end_s"] == ref["duration_s"],
            "finecut_continuation:complete_reference_media_required")
    folder = output / "artifacts" / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    glm = CodexMCP(state)
    get_authorization(state)
    require(not any(c["status"] in {"submitted", "uncertain", "failed_known"}
                    for c in state.data["calls"][policy["baseline_request_count"]:]),
            "finecut_continuation:unsettled_extension_call_no_new_submission")
    context = {"reference": reading, "editing_reference": methods,
        "reference_duration_s": ref["duration_s"], "reference_audio_stream_index": ref["audio_stream_index"],
        "reference_observation_status": "Historical canonical interpretation is a fallible model estimate, not a creative answer.",
        "craft_supplement": craft_context,
        "watched_windows": [_window_context(w, include_speech=False) for w in windows],
        "catalog": {"sources": [{k: s[k] for k in ("source_id", "sha256", "duration_s", "audio_stream_index")}
                                 | {"filename": Path(s["path"]).name} for s in catalog["sources"]]},
        "render_capabilities": {"max_segments": policy["max_segments"], "max_duration_s": 180,
            "speed": [.5, 2], "freeze_tail_s": [0, 10], "static_caption": True,
            "audio_modes": ["reference", "source", "mix", "silent"],
            "unsupported": ["smooth_speed_ramp", "optical_flow", "J/L_cut", "dynamic_reframing", "synthetic_video"]},
        "audio_semantics_policy": "用户确认参考只有BGM；内容以静音画面可读为主。没有实际音频听审，音乐节拍未知。",
        "observation_boundary": "Completed windows and craft reports remain fallible model observations. "
            "No Codex frame audit, replacement movie cuts, prescribed reference seconds or human story is supplied.",
        "instruction": "依据当前完整固定参考、历史导航证据和独立craft观察自主写草案。"
            "主旨保留，段落可变；未知参考技巧继续未知，可以主动一般精剪。"
            "只能从已完成精看的usable_ranges中自主选片，随后全部最终短片都要独立取证。"}
    coarse_path = output / "coarse_index.json"
    if coarse_path.is_file():
        coarse = _read(coarse_path)
        for row in coarse:
            contracts.validate_coarse(row, catalog)
            require(any(c["status"] == "received" and (output / "calls" / c["id"] / "parsed.json").is_file()
                        and _read(output / "calls" / c["id"] / "parsed.json") == row
                        for c in _read(policy["baseline_state_path"])["calls"]), "finecut_continuation:coarse_model_binding_missing")
        context["coarse_index"] = coarse

    def validate_plan(value):
        contracts.validate_plan(value, catalog, windows, ref["sha256"], ref["duration_s"],
            reference_audio_stream_index=ref["audio_stream_index"], editing_reference=methods)
        validate_candidate_dispositions(value, windows)
        compile_library_plan(catalog, value, fps=value["fps"], width=value["width"], height=value["height"])
        validate_caption_layout(value, value["width"], value["height"])
        semantic_pipeline.validate_plan_claims(value, windows, policy["max_segments"])

    _status(folder, "active_finecut_draft", usage=state.usage())
    draft = glm.call("active_4_draft", semantic_prompts.plan_prompt(context), reference_media, validate_plan)
    write_json(folder / "draft_plan.json", draft)
    prompt_view = _handbook_view(state, policy)
    def validate_refinement(value):
        validate_plan(value.get("plan"))
        finecut.validate_refinement(value, draft, policy["max_segments"])
    _status(folder, "active_finecut_refinement", usage=state.usage())
    refinement = glm.call("active_4_finecut", finecut.refinement_prompt(prompt_view, draft, context),
                          reference_media, validate_refinement)
    write_json(folder / "refinement.json", refinement)
    plan = refinement["plan"]
    write_json(folder / "plan.json", plan)
    from . import active_observation_compat
    active_observation_compat.enable(state)
    _status(folder, "active_finecut_exact_facts_and_claims", segments=len(plan["segments"]), usage=state.usage())
    checked = semantic_pipeline.observe_selected_slices(glm, plan,
        {s["source_id"]: s for s in catalog["sources"]}, windows, output / "media_cache", output, 4,
        observation_validator=active_observation_compat.observation_validator(state),
        claim_validator=active_observation_compat.claim_validator(state))
    checked = finecut.bind_draft_obligations(checked, draft, refinement)
    write_json(output / "semantic_audit/round_4/manifest.json", checked)
    get_authorization(state)
    _status(folder, "active_finecut_rendering", usage=state.usage())
    rendered = render_library_video(catalog, plan, output / "render_4", reference_path=reference,
        fps=plan["fps"], width=plan["width"], height=plan["height"])
    actual = inventory_sources(rendered["rendered_path"], output / "render_catalog_4")["sources"][0]
    output_media = prepare_window(actual, 0, rendered["measured_duration_s"], output / "media_cache", fps=30)
    timeline = detect_shot_timeline(actual, output / "media_cache/shot_timelines", threshold=3)
    evidence = associate_edl_boundaries(timeline, rendered)
    write_json(folder / "editing_evidence.json", evidence)
    blind = economy = review = None
    failure = None
    name = "active_4_blind"
    try:
        _status(folder, "active_finecut_silent_blind", usage=state.usage())
        blind = glm.call(name, semantic_prompts.blind_prompt(rendered["measured_duration_s"], rendered["sha256"]),
            output_media["path"], lambda v: audit.validate_visual_blind(v, rendered["measured_duration_s"], rendered["sha256"]))
        write_json(folder / "blind_reading.json", blind)
        name = "active_4_economy"
        manifest = finecut.economy_manifest(plan, rendered)
        economy = glm.call(name, finecut.economy_prompt(manifest, blind), output_media["path"],
                           lambda v: finecut.validate_economy_review(v, manifest))
        write_json(folder / "economy_review.json", economy)
        name = "active_4_review"
        review_context = {"reference": reading, "editing_reference": methods, "craft_supplement": craft_context,
            "protocol": audit.SEMANTIC_PROTOCOL, "video_sha256": rendered["sha256"],
            "actual_render_sha256": rendered["sha256"], "output_duration_s": rendered["measured_duration_s"],
            "blind_reading": blind, "source_observations": checked["observations"],
            "segment_checks": checked["segment_checks"], "required_claims": checked["required_claims"],
            "provenance": rendered["provenance"], "measured_output_timeline": compact_timeline(timeline),
            "edl_boundary_associations": evidence, "audio_review_limit": "Actual audio and music rhythm remain unverified.",
            "reference_observation_limit": context["reference_observation_status"]}
        def validate_review(value):
            contracts.validate_review(value, ref["sha256"])
            validate_method_review(value, methods, rendered["measured_duration_s"])
            audit.validate_semantic_review(value, blind, checked["observations"], checked["required_claims"],
                rendered["measured_duration_s"], rendered["sha256"], segment_checks=checked["segment_checks"])
        review = glm.call(name, semantic_prompts.review_prompt(review_context), output_media["path"], validate_review)
        write_json(folder / "review.json", review)
    except ValueError as error:
        failure = _known_protocol_failure(state, name, error)
        write_json(folder / "incomplete_output_stage.json", failure)
    semantic_pass = bool(review is not None and audit.semantic_review_passes(review, blind, checked["segment_checks"],
                         expected_segment_ids=[s["segment_id"] for s in plan["segments"]]))
    finecut_pass = bool(economy is not None and finecut.passes(refinement, economy))
    result = {"status": "model_checked_library_candidate" if semantic_pass and finecut_pass else "library_candidate_with_limitations",
        "final_video": rendered["rendered_path"], "final_sha256": rendered["sha256"], "selected_round": 4,
        "delivery_mode": "sole_authorized_new_candidate_no_historical_comparison_or_selection_POST",
        "reference_sha256": ref["sha256"], "review": review,
        "review_status": "completed_protocol" if review is not None else "incomplete_protocol_failure",
        "review_failure": failure, "semantic_gate_passed": semantic_pass, "active_finecut_gate_passed": finecut_pass and semantic_pass,
        "active_finecut_protocol": finecut.POLICY, "continuation_policy": POLICY,
        "observation_compatibility_policy": active_observation_compat.POLICY,
        "semantic_protocol": audit.SEMANTIC_PROTOCOL, "refinement_path": str(folder / "refinement.json"),
        "blind_reading_path": str(folder / "blind_reading.json") if blind is not None else None,
        "economy_review_path": str(folder / "economy_review.json") if economy is not None else None,
        "semantic_evidence_path": str(output / "semantic_audit/round_4/manifest.json"),
        "craft_evidence_role": craft_context["binding_role"], "usage": state.usage(),
        "actual_fine_windows": len(windows), "new_unique_fine_windows": 0, "new_renders": 1,
        "effective_render_limit": 5, "request_limit_policy": policy["request_limit_policy"],
        "human_creative_inputs": [], "source_generation_requests": 0,
        "evidence_limit": "Model checks are fallible, not human truth; no new-film quality improvement is presumed. "
            "Unidentified reference techniques remain unknown; general optimization is not certified reference transfer."}
    get_authorization(state)
    write_json(output / RESULT_FILE, result)
    protected = {p for directory in (folder, output / "semantic_audit/round_4", output / "render_4")
                 for p in directory.rglob("*") if p.is_file()}
    protected.add(output / RESULT_FILE)
    protected.add(Path(state.data['artifacts'][active_observation_compat.ARTIFACT][0]['path']))
    protected.update(p for p in (output / "calls").glob("*/*")
                     if p.is_file() and p.parent.name in {c["id"] for c in state.data["calls"][policy["baseline_request_count"]:]})
    protected.update({Path(output_media["path"]), Path(output_media["path"]).parent / "lineage.json"})
    state.set_artifact(RESULT, {"policy": POLICY, "result_sha256": json_sha(result),
        "completed_files": [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in sorted(protected)]})
    get_authorization(state)
    return result
