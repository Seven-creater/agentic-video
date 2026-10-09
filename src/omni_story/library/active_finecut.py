"""Forward-only model-owned refinement and independent output economy review."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from ..contract import ids, number, refs, require, rows, text
from .media import sha256_file
from .prompts import BASE
from .state import LibraryStopped, json_sha

POLICY = "active_fine_cut_v1"
POLICY_ARTIFACT = "active_finecut_policy"
HANDBOOK = Path(__file__).with_name("craft_knowledge") / "ACTIVE_FINE_CUT.md"


def enable_policy(state, requested):
    saved = state.data["artifacts"].get(POLICY_ARTIFACT, [])
    if saved:
        require(len(saved) == 1, "finecut:multiple_policies")
        record = json.loads(Path(saved[0]["path"]).read_text(encoding="utf-8"))
        require(json_sha(record) == saved[0]["sha256"] and record["policy"] == POLICY,
                "finecut:recorded_policy_changed")
        require(sha256_file(record["handbook_path"]) == record["handbook_sha256"], "finecut:handbook_snapshot_changed")
        return True
    if not requested:
        return False
    if any(c["name"].startswith("plan_") or "_plan" in c["name"] for c in state.data["calls"]) or any(state.output.glob("render_*")):
        raise LibraryStopped("finecut:cannot_reinterpret_paid_plans_or_authorize_another_render")
    folder = state.output / "artifacts" / POLICY
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "ACTIVE_FINE_CUT.md"
    snapshot.write_bytes(HANDBOOK.read_bytes())
    state.set_artifact(POLICY_ARTIFACT, {"policy": POLICY, "handbook_path": str(snapshot),
        "handbook_sha256": sha256_file(snapshot), "input_and_hard_budgets_unchanged": True,
        "applies_to": "Unsubmitted plans only; all final slices receive independent observation.",
        "extra_reserved_requests_per_round": 4, "model_review_is_not_human_truth": True})
    return True


def handbook(state):
    record = state.data["artifacts"][POLICY_ARTIFACT][0]
    value = json.loads(Path(record["path"]).read_text(encoding="utf-8"))
    require(json_sha(value) == record["sha256"] and sha256_file(value["handbook_path"]) == value["handbook_sha256"],
            "finecut:knowledge_binding_changed")
    return Path(value["handbook_path"]).read_text(encoding="utf-8")


def validate_refinement(value, draft, max_segments):
    plan = value.get("plan")
    require(isinstance(plan, dict), "finecut:plan_required")
    segment_ids = ids(plan.get("segments"), "segment_id", "finecut/segments")
    require(len(segment_ids) <= max_segments, "finecut:reserved_segment_limit")
    decision_ids = ids(value.get("decisions"), "segment_id", "finecut/decisions")
    require(decision_ids == segment_ids, "finecut:every_final_segment_needs_decision")
    for decision in value["decisions"]:
        for field in ("new_information", "in_out_reason", "speed_reason", "hold_reason"):
            text(decision.get(field), "finecut/" + field)
        require(decision.get("origin") in {"general_optimization", "reference_transfer"}, "finecut:origin")
        method_ids = decision.get("reference_method_ids")
        refs(method_ids, {b["method_id"] for b in draft.get("editing_bindings", [])}, "finecut/method_ids",
             nonempty=decision["origin"] == "reference_transfer")
        require(decision["origin"] != "general_optimization" or not method_ids, "finecut:optimization_is_not_reference_evidence")
    original_slots = {s["slot_id"]: s for s in draft["slots"]}
    coverage = value.get("obligation_coverage")
    require(ids(coverage, "slot_id", "finecut/obligations") == set(original_slots), "finecut:missing_information_obligation")
    for row in coverage:
        require(row.get("status") in {"preserved", "unresolved"}, "finecut:obligation_status")
        refs(row.get("segment_ids"), segment_ids, "finecut/obligation_segments", nonempty=row["status"] == "preserved")
        text(row.get("reason"), "finecut/obligation_reason")
    for slot in plan["slots"]:
        if slot["slot_id"] in original_slots:
            require(slot["intended_takeaway"] == original_slots[slot["slot_id"]]["intended_takeaway"], "finecut:existing_obligation_rewritten")
    originals = {s["segment_id"]: s for s in draft["segments"]}
    final = {s["segment_id"]: s for s in plan["segments"]}
    dispositions = value.get("draft_dispositions")
    require(ids(dispositions, "segment_id", "finecut/dispositions") == set(originals), "finecut:draft_segments_unaccounted")
    for row in dispositions:
        decision = row.get("decision")
        require(decision in {"retained", "replaced", "removed"}, "finecut:disposition")
        replacements = row.get("replacement_segment_ids")
        refs(replacements, segment_ids, "finecut/replacements", nonempty=decision == "replaced")
        text(row.get("reason"), "finecut/disposition_reason")
        if decision == "removed":
            require(not replacements and row["segment_id"] not in final, "finecut:removed_segment_still_selected")
        if decision == "retained":
            prior, current = originals[row["segment_id"]], final.get(row["segment_id"])
            require(current is not None and all(prior.get(k) == current.get(k) for k in
                ("source_id", "window_id", "source_in_s", "source_out_s")), "finecut:retained_source_changed")
    total = 0
    for segment in plan["segments"]:
        start, end = number(segment["source_in_s"], "finecut/in"), number(segment["source_out_s"], "finecut/out")
        speed, hold = number(segment.get("speed", 1), "finecut/speed", .5), number(segment.get("freeze_tail_s", 0), "finecut/hold")
        require(start < end and speed <= 2 and hold <= 10, "finecut:invalid_transform")
        total += (end - start) / speed + hold
    duration = value.get("duration")
    require(isinstance(duration, dict), "finecut:duration_required")
    target = number(duration.get("target_s"), "finecut/target", .001)
    require(abs(number(duration.get("total_s"), "finecut/total", .001) - total) <= .05, "finecut:duration_math")
    require(isinstance(duration.get("over_target_reason"), str), "finecut/over_target_reason:string_required")
    if total > target + .05:
        text(duration["over_target_reason"], "finecut/over_target_reason")
    require(target <= 180 and total <= 180, "finecut:duration_cap")
    return value


def refinement_prompt(state, draft, context):
    return BASE + "\n" + handbook(state) + "\n" + json.dumps({
        "policy": POLICY, "draft": draft, "evidence": context,
        "instruction": "自主精剪。可将一个长事件拆成不连续关键瞬间；必须基于已看窗口选择。技巧都可不采用。"
        "保留原slot表达义务，允许重划段落；沿用slot_id须保留其intended_takeaway。缺证据标unresolved，不写成功。"
        "原草案片段逐项交代去留，最终每段交代新增信息和入出点理由。"
        "retained须保留原segment_id、source_id、window_id和原source_in_s/source_out_s（可改变speed或hold）；"
        "改变源区间或以多个关键瞬间替代时用replaced，并在replacement_segment_ids列出实际最终ID。"
        "短不等于好；目标时长可参考原片量级。",
        "response_contract": {"plan": "完整最终EDL，使用原计划协议的所有字段",
            "decisions": [{"segment_id": "最终ID", "new_information": "新增可见信息", "in_out_reason": "切点依据",
                "speed_reason": "保持或改变速度的理由", "hold_reason": "保持或不保持尾帧的理由",
                "origin": "general_optimization/reference_transfer", "reference_method_ids": []}],
            "obligation_coverage": [{"slot_id": "原slotID", "segment_ids": ["最终ID"],
                "status": "preserved/unresolved", "reason": "保留或缺失哪些信息"}],
            "draft_dispositions": [{"segment_id": "原片段ID", "decision": "retained/replaced/removed",
                "replacement_segment_ids": [], "reason": "去留依据"}],
            "duration": {"target_s": "目标秒数", "total_s": "sum((out-in)/speed+freeze)",
                "over_target_reason": "超目标须说明必要信息，否则写无"}}}, ensure_ascii=False)


def economy_manifest(plan, rendered):
    require(len(plan["segments"]) == len(rendered["provenance"]), "finecut:render_segment_count")
    provenance = []
    for segment, actual in zip(plan["segments"], rendered["provenance"]):
        require(all(segment[k] == actual[k] for k in ("source_id", "window_id", "source_in_s", "source_out_s")),
                "finecut:render_range_mismatch")
        provenance.append({"segment_id": segment["segment_id"], "output_in_s": actual["output_in_s"],
                           "output_out_s": actual["output_out_s"]})
    return {"sha256": rendered["sha256"], "measured_duration_s": rendered["measured_duration_s"], "provenance": provenance}


def validate_economy_review(value, rendered):
    require(value.get("video_sha256") == rendered["sha256"], "finecut:output_sha")
    for field in ("economy_status", "narrative_readability"):
        require(value.get(field) in {"pass", "partial", "fail"}, "finecut:review_status")
    segments = {s["segment_id"]: s for s in rendered["provenance"]}
    checks = value.get("segment_checks")
    require(ids(checks, "segment_id", "finecut/economy_checks") == set(segments), "finecut:incomplete_economy_review")
    for check in checks:
        require(check.get("status") in {"necessary", "redundant", "uncertain"}, "finecut:segment_economy_status")
        text(check.get("reason"), "finecut/economy_reason")
        segment = segments[check["segment_id"]]
        for evidence in rows(check.get("output_evidence"), "finecut/output_evidence"):
            start, end = number(evidence.get("start_s"), "finecut/evidence_start"), number(evidence.get("end_s"), "finecut/evidence_end")
            require(segment["output_in_s"] <= start < end <= segment["output_out_s"] + .001, "finecut:evidence_outside_output_segment")
            text(evidence.get("observed_fact"), "finecut/evidence_fact")
    for limitation in rows(value.get("limitations"), "finecut/limitations", nonempty=False):
        text(limitation, "finecut/limitation")
    require(value["economy_status"] != "pass" or (value["narrative_readability"] == "pass" and
            all(c["status"] == "necessary" for c in checks)), "finecut:pass_with_redundancy_or_unreadable_story")
    return value


def economy_prompt(manifest, blind):
    return (BASE + "\n独立静音审核实际输出的精炼程度。没有剧情答案或精剪理由。"
    "每段是否增加行动、关系、结果或必要停留？重复表情和空白过程有哪些？不能仅因长或短判好坏。\n" + json.dumps({
        "actual_output": manifest, "independent_silent_reading": blind,
        "response_contract": {"video_sha256": manifest["sha256"], "economy_status": "pass/partial/fail",
            "narrative_readability": "pass/partial/fail", "segment_checks": [{"segment_id": "实际ID",
                "status": "necessary/redundant/uncertain", "output_evidence": [{"start_s": "输出秒数",
                    "end_s": "输出秒数", "observed_fact": "具体画面及新增/重复信息"}], "reason": "保留或删减理由"}],
            "limitations": []}}, ensure_ascii=False))


def _budget(state, name, value):
    entries = state.data["artifacts"].get(name, [])
    if entries:
        record = json.loads(Path(entries[0]["path"]).read_text(encoding="utf-8"))
        require(len(entries) == 1 and json_sha(record) == entries[0]["sha256"] and record["policy"] == POLICY,
                "finecut:budget_record_changed")
        return record
    state.set_artifact(name, value)
    return value


def plan_budget(state, round_no):
    remaining = state.max_requests - state.usage()["requests"] if state.max_requests is not None else None
    maximum = min(32, (remaining - 12) // 4) if remaining is not None else 32
    name = f"active_finecut_plan_budget_{round_no}"
    if not state.data["artifacts"].get(name) and maximum < 1:
        raise LibraryStopped("finecut:insufficient_budget_for_refinement_and_actual_output_review")
    return _budget(state, name, {"policy": POLICY, "max_segments": maximum,
        "remaining_at_allocation": remaining, "reserved_fixed_requests": 12, "reserved_per_segment": 4})["max_segments"]


def fine_window_budget(state, round_no, max_fine, watched_count, remaining):
    return _budget(state, f"active_finecut_search_budget_{round_no}", {"policy": POLICY,
        "window_cap": min(8, max_fine - watched_count,
                          max(0, (remaining - 18) // 2) if remaining is not None else 8),
        "remaining_at_allocation": remaining, "reserved_search": 2,
        "reserved_one_final_slice_and_output": 16})["window_cap"]


def passes(refinement, economy):
    return (all(c["status"] == "preserved" for c in refinement["obligation_coverage"])
            and economy["economy_status"] == economy["narrative_readability"] == "pass"
            and all(c["status"] == "necessary" for c in economy["segment_checks"]))


def bind_draft_obligations(manifest, draft, refinement):
    """Audit original information needs against output, even after slot restructuring."""
    result = deepcopy(manifest)
    coverage = {row["slot_id"]: row for row in refinement["obligation_coverage"]}
    by_id = {claim["claim_id"]: claim for claim in result["required_claims"]}
    for slot in draft["slots"]:
        description = slot["intended_takeaway"]
        claim = {"claim_id": "draft_claim_" + json_sha([POLICY, slot["slot_id"], description])[:16],
                 "kind": "slot_takeaway", "description": description, "owner_id": slot["slot_id"],
                 "origin": "draft_obligation", "segment_ids": list(coverage[slot["slot_id"]]["segment_ids"])}
        if claim["claim_id"] in by_id:
            require(by_id[claim["claim_id"]] == claim, "finecut:original_obligation_binding_changed")
        else:
            result["required_claims"].append(claim)
            by_id[claim["claim_id"]] = claim
    return result


def validate_selection(choice, candidates):
    from .semantic_audit import semantic_review_passes, validate_semantic_selection
    validate_semantic_selection(choice, candidates)
    passing = [candidate["round"] for candidate in candidates
               if passes(candidate["refinement"], candidate["economy"])
               and semantic_review_passes(candidate["review"], candidate["blind"], candidate["segment_checks"],
                                          expected_segment_ids=candidate["expected_segment_ids"])]
    require(not passing or choice["selected_round"] in passing,
            "finecut:selection_discards_available_semantic_and_economy_pass")
    selected = next(candidate for candidate in candidates if candidate["round"] == choice["selected_round"])
    if not passes(selected["refinement"], selected["economy"]):
        require(bool(choice["limitations"]), "finecut:limited_candidate_selection_needs_limitations")
    return choice


def prepare_existing_task(output, *, reference=None, library=None):
    """Append a CPU-only, concrete next-run proposal; never authorize its costs."""
    from .pipeline import _catalog, _read
    from .semantic_continuation import _state
    from .reference_craft import _artifact, _verify_allocation, ALLOCATION
    state = _state(output)
    if reference is not None:
        source = _catalog(reference, state.output / 'reference_catalog')['sources'][0]
        require(source['sha256'] == state.data['input_lock']['reference_sha256'], 'finecut:preflight_reference_changed')
    if library is not None:
        catalog = _catalog(library, state.output / 'catalog')['sources']
        require([{k:s[k] for k in ('source_id','sha256')} for s in catalog] == state.data['input_lock']['library_sources'],
                'finecut:preflight_library_changed')
    if state.data["artifacts"].get(ALLOCATION):
        _verify_allocation(state, _artifact(state, ALLOCATION))
    usage = state.usage()
    renders = sorted(p.name for p in state.output.glob("render_*/final.mp4"))
    final = state.output / "render_3" / "render_result.json"
    measured = _read(final) if final.is_file() else None
    proposal = {"policy": POLICY, "status": "prepared_not_authorized_or_submitted", "task_id": state.data["task_id"],
        "current_usage": usage, "remaining_requests": usage["max_requests"] - usage["requests"],
        "known_render_count": len(renders), "additional_render_authorized": False,
        "input_lock_sha256": json_sha(state.data["input_lock"]), "call_ledger_sha256": json_sha(state.data["calls"]),
        "handbook_sha256": sha256_file(HANDBOOK), "new_requests": 0, "new_renders": 0,
        "proposed_max_final_segments": 8, "proposed_max_additional_requests": 44,
        "reservation": {"draft": 2, "finecut": 2, "exact_slice_observation_and_claim_check": 4 * 8,
            "silent_blind": 2, "economy_review": 2, "reference_and_semantic_review": 2, "selection": 2},
        "proposed_additional_renders": 1, "new_unique_library_windows": 0,
        "reuse_boundary": "Only completed original-file-bound observations; all new final slices are independently checked.",
        "authorization_boundary": "Needs explicit same-directory request-budget and one-render extension; original lock/history remain.",
        "measured_previous_output": None if measured is None else {"sha256": measured["sha256"],
            "duration_s": measured["measured_duration_s"], "segments": len(measured["provenance"]),
            "source_ranges": [{k: s.get(k) for k in ("source_in_s", "source_out_s", "speed", "freeze_tail_s")}
                              for s in measured["provenance"]]}}
    existing = state.data["artifacts"].get("active_finecut_preflight", [])
    if existing and _read(existing[-1]["path"]) == proposal:
        return proposal
    state.set_artifact("active_finecut_preflight", proposal)
    return proposal
