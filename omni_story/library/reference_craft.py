"""Bounded, model-selected reference re-observation; no movie edits or new budget."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

from ..contract import ids, number, refs, require, rows, text
from .media import prepare_window, sha256_file, validate_window, verify_source
from .pipeline import CodexMCP, _catalog, _read, _status
from .prompts import BASE
from .state import LibraryState, file_lock, json_sha
from .contracts import parse_model_json

POLICY = "reference_craft_observation_v1"
ALLOCATION = "reference_craft_allocation"
RESULT = "reference_craft_result"
PACKAGE = Path(__file__).with_name("craft_knowledge")


def load_knowledge(directory=PACKAGE):
    directory = Path(directory).resolve(strict=True)
    catalogue = _read(directory / "catalogue.json")
    cards = {}
    for row in catalogue["cards"]:
        require(row["id"] not in cards, "craft:duplicate_card")
        path = (directory / row["path"]).resolve(strict=True)
        require(path.is_relative_to(directory), "craft:unsafe_card_path")
        cards[row["id"]] = path.read_text(encoding="utf-8")
    paths = [directory / "SKILL.md", directory / "catalogue.json"]
    paths += [(directory / r["path"]).resolve() for r in catalogue["cards"]]
    files = {p.relative_to(directory).as_posix(): sha256_file(p) for p in paths}
    return {"catalogue": catalogue, "cards": cards,
            "playbook": (directory / "SKILL.md").read_text(encoding="utf-8"),
            "files": files, "sha256": json_sha(files)}


def _range(row, source, *, bound=None):
    start, end = number(row.get("start_s"), "craft/start"), number(row.get("end_s"), "craft/end")
    validate_window(source, start, end)
    if bound:
        require(bound[0] <= start < end <= bound[1], "craft:outside_completed_inspection")
    return start, end


def validate_selection(value, source, knowledge):
    require(value.get("reference_sha256") == source["sha256"], "craft:reference_binding")
    text(value.get("visual_takeaway"), "craft/visual_takeaway")
    cursor = 0.0
    for part in rows(value.get("coverage"), "craft/coverage"):
        start, end = _range(part, source)
        require(abs(start - cursor) <= .001, "craft:coverage_gap")
        text(part.get("visible_information"), "craft/visible_information")
        cursor = end
    require(abs(cursor - source["duration_s"]) <= .001, "craft:coverage_tail")
    windows = rows(value.get("inspection_windows"), "craft/inspection_windows", nonempty=False)
    require(len(windows) <= 3, "craft:window_limit")
    ids(windows, "window_id", "craft/windows", nonempty=False)
    for window in windows:
        start, end = _range(window, source)
        require(end - start <= 12.001, "craft:window_too_long")
        text(window.get("question"), "craft/window/question")
        refs(window.get("card_ids"), set(knowledge["cards"]), "craft/card_ids")
    text(value.get("observation_limits"), "craft/observation_limits")


def validate_analysis(value, source, knowledge, selection, inspection):
    require(value.get("reference_sha256") == source["sha256"], "craft:reference_binding")
    window_ids = {w["window_id"] for w in selection["inspection_windows"]}
    answers = rows(value.get("inspection_answers"), "craft/answers", nonempty=False)
    require(ids(answers, "window_id", "craft/answers", nonempty=False) == window_ids,
            "craft:inspection_question_unanswered")
    for answer in answers:
        text(answer.get("answer"), "craft/answer")
        text(answer.get("remaining_uncertainty"), "craft/uncertainty")
    methods = rows(value.get("methods"), "craft/methods", nonempty=False)
    ids(methods, "method_id", "craft/methods", nonempty=False)
    renderer = knowledge["catalogue"]["renderer"]
    operations = set(renderer["supported_operations"] + renderer["unavailable_operations"] + ["none"])
    for method in methods:
        require(method.get("card_id") in {*knowledge["cards"], "unknown", "other"}, "craft:unknown_card")
        require(method.get("status") in {"supported", "hypothesis", "unknown", "not_present"}, "craft:status")
        _range(method, source, bound=(inspection["source_start_s"], inspection["source_end_s"]))
        for field in ("visible_evidence", "alternative_explanation", "intended_function",
                      "material_requirements", "application_condition", "verification", "uncertainty"):
            text(method.get(field), "craft/" + field)
        require(method.get("operation") in operations, "craft:unknown_operation")
        require(method.get("implementation") in {"supported", "unavailable", "not_chosen"}, "craft:implementation")
        if method["operation"] in renderer["unavailable_operations"]:
            require(method["implementation"] != "supported", "craft:unavailable_operation_claimed")
        require(method.get("reference_speed_factor") is None, "craft:unmeasured_reference_speed")
    text(value.get("visual_takeaway"), "craft/visual_takeaway")
    text(value.get("remaining_gaps"), "craft/remaining_gaps")
    require(value.get("audio_rhythm_status") == "unverified", "craft:audio_not_observed")


def _selection_prompt(source, knowledge):
    contract = {"reference_sha256": source["sha256"], "visual_takeaway": "画面表达的含义",
        "coverage": [{"start_s": 0, "end_s": "依观察分段，连续覆盖全片", "visible_information": "可见信息"}],
        "inspection_windows": [{"window_id": "w1", "start_s": "自主选择", "end_s": "自主选择",
            "question": "下一次观察要解决的具体疑问", "card_ids": ["自主选择目录ID或空列表"]}],
        "observation_limits": "采样和判断边界"}
    return BASE + "\n" + knowledge["playbook"] + "\n" + json.dumps({
        "phase": "完整参考中性扫描。只依据当前媒体自主提出复看需求；不参考历史答案。",
        "reference_sha256": source["sha256"], "duration_s": source["duration_s"],
        "mapping": "source_time_s = proxy_time_s; full duration, normal playback speed",
        "sampling_limit": "输入代理12fps，云端实际采样未知，不代表逐帧观看。",
        "catalogue": knowledge["catalogue"], "response_contract": contract,
        "limits": "最多3个窗口，各最多12秒；可为0。目录不是必须出现的清单。只输出一个JSON对象。"
    }, ensure_ascii=False)


def _analysis_prompt(source, knowledge, selection, inspection):
    chosen = {card for w in selection["inspection_windows"] for card in w["card_ids"]}
    contract = {"reference_sha256": source["sha256"], "visual_takeaway": "当前画面支持的含义",
        "inspection_answers": [{"window_id": "原window_id", "answer": "实际观察结论", "remaining_uncertainty": "未解决项或无"}],
        "methods": [{"method_id": "m1", "card_id": "目录ID/unknown/other",
            "start_s": "原参考时间", "end_s": "原参考时间", "status": "supported/hypothesis/unknown/not_present",
            "visible_evidence": "具体变化、动作、边界证据", "alternative_explanation": "替代解释或无",
            "intended_function": "作用推测及依据", "material_requirements": "迁移所需素材，不捏造库内内容",
            "operation": "目录执行操作ID或none", "implementation": "supported/unavailable/not_chosen",
            "application_condition": "何时采用或放弃，不能强制技巧", "verification": "输出应如何验证",
            "reference_speed_factor": None, "uncertainty": "依据与不足"}],
        "audio_rhythm_status": "unverified", "remaining_gaps": "其他未观察部分或未解决问题"}
    return BASE + "\n" + knowledge["playbook"] + "\n" + json.dumps({
        "phase": "执行你自主选定的复看，再核验手法假设。不要仅重复术语。",
        "selection": selection, "selected_knowledge_cards": {c: knowledge["cards"][c] for c in sorted(chosen)},
        "renderer": knowledge["catalogue"]["renderer"],
        "actual_continuous_inspection": {k: inspection[k] for k in ("sha256", "source_sha256",
            "source_start_s", "source_end_s", "source_offset_s", "time_mapping")},
        "media_boundary": "所有所选窗口包含在一个连续包络内，没有拼接、代理加减速或人工转场；代理30fps，云端实际采样未知。",
        "evidence_boundary": "方法时间必须在此实际复看包络内。原参考速度倍率没有原始素材对照，一律null。声画节拍未验证。",
        "response_contract": contract
    }, ensure_ascii=False)


def _artifact(state, name):
    records = state.data["artifacts"].get(name, [])
    if not records:
        return None
    require(len(records) == 1, "craft:duplicate_stage_artifact:" + name)
    value = _read(records[0]["path"])
    require(json_sha(value) == records[0]["sha256"], "craft:artifact_modified:" + name)
    return value


def _verify_allocation(state, allocation):
    from .extension_budget import historical_state
    state = historical_state(state)
    require(allocation["policy"] == POLICY and allocation["task_id"] == state.data["task_id"], "craft:task_binding")
    require(allocation["input_lock_sha256"] == json_sha(state.data["input_lock"])
            and allocation["max_requests"] == state.max_requests, "craft:locked_inputs_changed")
    baseline = allocation["baseline_requests"]
    require(baseline == len(allocation["original_calls"]) and state.data["request_count"] == len(state.data["calls"])
            and allocation["reserved_requests"] == 4 and baseline + 4 <= state.max_requests,
            "craft:invalid_request_reservation")
    require(allocation["stages"] == ["craft_scan", "craft_inspect"] and allocation["repairs_per_stage"] == 1
            and allocation["new_library_fine_windows"] == allocation["new_renders"] == 0, "craft:stage_policy_changed")
    require(state.data["calls"][:baseline] == allocation["original_calls"], "craft:historical_calls_changed")
    appended = state.data["calls"][baseline:]
    require(len(appended) <= 4 and all(c["name"] in {
        "craft_scan", "craft_scan_repair", "craft_inspect", "craft_inspect_repair"} for c in appended),
        "craft:request_allocation_changed")
    for name in allocation["stages"]:
        originals = [c for c in appended if c["name"] == name]
        repairs = [c for c in appended if c["name"] == name + "_repair"]
        require(len(originals) <= 1 and len(repairs) <= 1, "craft:duplicate_paid_stage")
        require(not originals or originals[0].get("repair_of") is None, "craft:invalid_original")
        require(not repairs or (originals and repairs[0].get("repair_of") == originals[0]["id"]
                and originals[0]["status"] == "received"), "craft:invalid_repair_parent")
    for call in appended:
        folder = state.output / "calls" / call["id"]
        require(json_sha(_read(folder / "request.json")) == call["request_sha256"], "craft:recorded_request_modified")
        if call["status"] == "received":
            reply = _read(folder / "response.json")
            require(json_sha(reply) == call["response_sha256"], "craft:recorded_response_modified")
            if (folder / "parsed.json").exists():
                actual = parse_model_json("\n".join(c["text"] for c in reply["result"]["content"] if c.get("type") == "text"))
                require(_read(folder / "parsed.json") == actual, "craft:parsed_model_content_changed")
    for row in allocation["protected_files"]:
        require(Path(row["path"]).is_file() and sha256_file(row["path"]) == row["sha256"],
                "craft:historical_file_changed:" + row["path"])
    require(sorted(p.name for p in state.output.glob("render_*") if p.is_dir()) == allocation["render_directories"],
            "craft:new_render_forbidden")


def _allocate(state, source):
    from .extension_budget import historical_state
    state = historical_state(state)
    saved = _artifact(state, ALLOCATION)
    if saved:
        _verify_allocation(state, saved)
        return saved
    require(not any(c["status"] == "submitted" for c in state.data["calls"]), "craft:pending_original_call")
    require(state.max_requests - state.usage()["requests"] >= 4, "craft:four_request_reservation_required")
    folder = state.output / "artifacts" / POLICY
    folder.mkdir(parents=True, exist_ok=True)
    knowledge = load_knowledge()
    snapshot = folder / "knowledge"
    for relative in knowledge["files"]:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / relative, target)
    protected = {p for p in state.output.glob("*.json") if p.name not in {
        "library_state.json", "current_status.json", "mcp_ready.json", "mcp_current.json", "mcp_tools.json"}}
    for name in ("calls", "semantic_audit", "artifacts"):
        protected.update(p for p in (state.output / name).rglob("*.json") if not p.is_relative_to(folder))
    protected.update(state.output.glob("*catalog/inventory.json"))
    for directory in state.output.glob("render_*"):
        protected.update(p for p in directory.rglob("*") if p.is_file())
    value = {"policy": POLICY, "task_id": state.data["task_id"],
        "user_authorization": "2026-10-04: 那进行下一步；接入通用剪辑知识并验证自主参考复看。",
        "input_lock_sha256": json_sha(state.data["input_lock"]), "reference_sha256": source["sha256"],
        "max_requests": state.max_requests, "baseline_requests": state.usage()["requests"], "reserved_requests": 4,
        "stages": ["craft_scan", "craft_inspect"], "repairs_per_stage": 1,
        "new_library_fine_windows": 0, "new_renders": 0, "knowledge_sha256": knowledge["sha256"],
        "knowledge_snapshot": str(snapshot), "original_calls": deepcopy(state.data["calls"]),
        "render_directories": sorted(p.name for p in state.output.glob("render_*") if p.is_dir()),
        "protected_files": [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in sorted(protected)]}
    state.set_artifact(ALLOCATION, value)
    return value


def _verify_completed(state, result, source, knowledge):
    for name, key in (("craft_scan", "selection"), ("craft_inspect", "analysis")):
        binding = result["model_bindings"][name]
        call = next(c for c in state.data["calls"] if c["id"] == binding["call_id"])
        require(call["status"] == "received" and call["request_sha256"] == binding["request_sha256"]
                and call["response_sha256"] == binding["response_sha256"], "craft:completed_call_binding")
        require(_read(state.output / "calls" / call["id"] / "parsed.json") == result[key], "craft:result_differs_from_model")
    for key in ("overview_media", "inspection"):
        media = result[key]
        require(sha256_file(media["path"]) == media["sha256"] and media["source_sha256"] == source["sha256"],
                "craft:inspection_media_changed")
        require(_read(Path(media["path"]).parent / "lineage.json") == media, "craft:inspection_lineage_changed")
    validate_selection(result["selection"], source, knowledge)
    validate_analysis(result["analysis"], source, knowledge, result["selection"], result["inspection"])


def execute_reference_craft(reference, library, output):
    output = Path(output).resolve(strict=True)
    saved = _read(output / "library_state.json")
    state = LibraryState(output, saved["input_lock"], max_requests=saved["max_requests"])
    with file_lock(output / ".reference_craft.lock"):
        source = _catalog(reference, output / "reference_catalog")["sources"][0]
        catalog = _catalog(library, output / "catalog")
        require([{k: s[k] for k in ("source_id", "sha256")} for s in catalog["sources"]]
                == state.data["input_lock"]["library_sources"], "craft:fixed_library_changed")
        require(source["sha256"] == state.data["input_lock"]["reference_sha256"], "craft:fixed_reference_changed")
        allocation = _allocate(state, source)
        knowledge = load_knowledge(allocation["knowledge_snapshot"])
        require(knowledge["sha256"] == allocation["knowledge_sha256"], "craft:knowledge_snapshot_changed")
        result = _artifact(state, RESULT)
        if result:
            _verify_completed(state, result, source, knowledge)
            return result
        try:
            glm = CodexMCP(state)
            whole = prepare_window(source, 0, source["duration_s"], output / "media_cache", fps=12)
            _status(output, "reference_craft_scan", requests=state.usage()["requests"])
            selection = glm.call("craft_scan", _selection_prompt(source, knowledge), whole["path"],
                lambda value: validate_selection(value, source, knowledge))
            windows = selection["inspection_windows"]
            start = min(w["start_s"] for w in windows) if windows else 0
            end = max(w["end_s"] for w in windows) if windows else source["duration_s"]
            inspection = prepare_window(source, start, end, output / "media_cache", fps=30)
            _status(output, "reference_craft_inspect", requests=state.usage()["requests"], source_start_s=start, source_end_s=end)
            analysis = glm.call("craft_inspect", _analysis_prompt(source, knowledge, selection, inspection), inspection["path"],
                lambda value: validate_analysis(value, source, knowledge, selection, inspection))
            verify_source(source)
            _verify_allocation(state, allocation)
            bindings = {}
            for name in ("craft_scan", "craft_inspect"):
                call = next(c for c in reversed(state.data["calls"]) if c["name"] in {name, name + "_repair"})
                bindings[name] = {"call_id": call["id"], "request_sha256": call["request_sha256"],
                                  "response_sha256": call["response_sha256"]}
            result = {"policy": POLICY, "status": "model_reference_craft_observation_completed",
                "reference_sha256": source["sha256"], "knowledge_sha256": knowledge["sha256"],
                "selection": selection, "overview_media": whole, "inspection": inspection, "analysis": analysis,
                "model_bindings": bindings,
                "usage": state.usage(), "new_requests": state.usage()["requests"] - allocation["baseline_requests"],
                "new_library_fine_windows": 0, "new_renders": 0,
                "evidence_limit": "Model observations, not human truth or improved-film validation. Cloud sampling is unknown.",
                "planning_handoff": {"role": "additional model evidence, never replace the fixed reference or old records",
                    "methods": analysis["methods"], "requires_actual_library_evidence": True,
                    "requires_output_review": True, "audio_rhythm_verified": False}}
            state.set_artifact(RESULT, result)
            _status(output, "reference_craft_complete", requests=state.usage()["requests"], new_renders=0)
            return result
        except Exception as error:
            state.set_artifact("reference_craft_failure", {"policy": POLICY, "error": str(error), "usage": state.usage(),
                "no_automatic_paid_replay": True, "new_renders": 0})
            _status(output, "reference_craft_stopped", error=str(error), requests=state.usage()["requests"])
            raise
