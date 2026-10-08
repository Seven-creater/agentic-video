"""Pure contracts for refining an existing movie edit one narrative slot at a time.

These checks bind timestamps, sources and proposals. They neither establish
semantic truth nor issue model requests, choose footage, render, or alter runs.
Parent-output observations are deliberately separate from exact source facts.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from ..contract import forbid_keys, ids, number, refs, require, rows, text


POLICY = "slot_finecut_v1"
HANDBOOK = Path(__file__).with_name("craft_knowledge") / "SLOT_FINECUT.md"
FACT_KINDS = {"action", "state", "identity", "result", "reaction", "shape", "inference"}
FACT_BASES = {"visual", "visible_text", "inference"}
EPSILON = .001
ROOT_OUTPUT_INSTRUCTION = (
    '返回格式：下文JSON是输入上下文，其中response_contract只描述你的输出模板。'
    '最终JSON的最外层必须直接是response_contract列出的字段；'
    '不要返回parent、previously_recorded_reference、response_contract等输入包装。'
    '不得复制整个输入JSON。只输出结果JSON，不加Markdown代码围栏。\n')
FORWARD_NAVIGATION_POLICY = {
    'version': 'sf_forward_parent_navigation_contract_v1',
    'scope': 'only unsubmitted preliminary parent-output facts after activation',
    'point_semantics': 'instant observed, duration unknown, no action completion or exposure proof',
    'old_paid_contracts': 'unchanged',
    'final_source_and_output_gates': 'unchanged independent evidence',
}


def _object(value, name):
    require(isinstance(value, dict), "slot_finecut/" + name + ":object_required")
    return value


def _sha(value, name):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
            "slot_finecut/" + name + ":sha256_required")


def _strings(value, name):
    for item in rows(value, "slot_finecut/" + name, nonempty=False):
        text(item, "slot_finecut/" + name)


def _interval(start, end, lower, upper, name):
    start = number(start, "slot_finecut/" + name + "/start")
    end = number(end, "slot_finecut/" + name + "/end")
    require(lower - EPSILON <= start < end <= upper + EPSILON,
            "slot_finecut/" + name + ":outside_bound_interval")
    return start, end


def _parent(parent):
    _object(parent, "parent")
    text(parent.get("baseline_id"), "slot_finecut/parent/baseline_id")
    _sha(parent.get("sha256"), "parent")
    return number(parent.get("duration_s"), "slot_finecut/parent/duration", .001)


def _binding(value, parent, slot=None):
    _object(value, "response")
    _parent(parent)
    require(value.get("baseline_id") == parent["baseline_id"]
            and value.get("parent_sha256") == parent["sha256"],
            "slot_finecut:parent_binding_changed")
    if slot is not None:
        require(value.get("slot_id") == slot["slot_id"], "slot_finecut:slot_binding_changed")


def validate_slots(value, parent):
    """Require the model's ordered slots to cover the actual parent timeline."""
    _binding(value, parent)
    duration = parent["duration_s"]
    ids(value.get("slots"), "slot_id", "slot_finecut/slots")
    require(len(value['slots']) <= 32, 'slot_finecut:slots_exceed_final_renderer_segment_capacity')
    cursor = 0.0
    for slot in value["slots"]:
        start, end = _interval(slot.get("start_s"), slot.get("end_s"), 0, duration, "slot")
        require(abs(start - cursor) <= EPSILON, "slot_finecut:slots_must_cover_without_gaps_or_overlaps")
        for field in ("intended_takeaway", "entry_state", "exit_state", "link_to_previous", "link_to_next"):
            text(slot.get(field), "slot_finecut/slots/" + field)
        cursor = end
    require(abs(cursor - duration) <= EPSILON, "slot_finecut:slots_must_cover_actual_parent")
    _strings(value.get("limitations"), "limitations")
    _strings(value.get("uncertainties"), "uncertainties")
    return value


def validate_facts(value, parent, slot):
    """Validate neutral facts in slot-local output seconds, never source seconds."""
    _binding(value, parent, slot)
    start, end = _interval(slot["start_s"], slot["end_s"], 0, parent["duration_s"], "slot")
    require(value.get("slot_start_s") == slot["start_s"] and value.get("slot_end_s") == slot["end_s"]
            and value.get("time_domain") == "slot_local_output", "slot_finecut:fact_time_binding_changed")
    ids(value.get("evidence"), "evidence_id", "slot_finecut/facts", nonempty=False)
    for evidence in value["evidence"]:
        _interval(evidence.get("start_s"), evidence.get("end_s"), 0, end - start, "fact")
        text(evidence.get("observed_fact"), "slot_finecut/fact/observed_fact")
        require(evidence.get("kind") in FACT_KINDS, "slot_finecut:fact_kind")
        require(evidence.get("basis") in FACT_BASES, "slot_finecut:fact_basis")
        require(evidence.get('kind') != 'inference' or evidence.get('basis') != 'visual',
                'slot_finecut:inference_kind_cannot_be_visual_fact')
    _strings(value.get("limitations"), "limitations")
    _strings(value.get("uncertainties"), "uncertainties")
    forbid_keys(value, {"source_in_s", "source_out_s", "source_start_s", "source_end_s", "source_sha256",
                        "intended_takeaway", "required_claims", "claim_checks", "plan"})
    return value


def validate_parent_navigation(value, parent, slot):
    """Validate a request-bound preliminary report, without creating durations.

    The raw model report remains intact. Point observations are navigation
    cues; they cannot prove action completion or readable exposure. Exact movie
    slice and actual-output contracts still require independent interval facts.
    """
    _object(value, 'parent_navigation')
    require(value.get('schema_version') == 'sf_parent_point_navigation_v1',
            'slot_finecut:parent_navigation_version')
    binding = _object(value.get('request_binding'), 'parent_navigation_binding')
    require(binding.get('baseline_id') == parent['baseline_id']
            and binding.get('parent_sha256') == parent['sha256']
            and binding.get('slot_id') == slot['slot_id']
            and binding.get('parent_slot_start_s') == slot['start_s']
            and binding.get('parent_slot_end_s') == slot['end_s']
            and binding.get('local_start_s') == 0
            and binding.get('local_end_s') == slot['end_s'] - slot['start_s']
            and binding.get('time_domain') == 'slot_local_output',
            'slot_finecut:parent_navigation_binding')
    report = _object(value.get('model_report'), 'parent_navigation_report')
    _binding(report, parent, slot)
    raw_bounds = (report.get('slot_start_s'), report.get('slot_end_s'))
    require(report.get('time_domain') == 'slot_local_output' and raw_bounds in {
                (0, binding['local_end_s']), (slot['start_s'], slot['end_s'])},
            'slot_finecut:parent_navigation_raw_bounds')
    identifiers = ids(report.get('evidence'), 'evidence_id', 'slot_finecut/navigation_evidence', nonempty=False)
    temporal = rows(value.get('temporal_support'), 'slot_finecut/navigation_support', nonempty=False)
    require(ids(temporal, 'evidence_id', 'slot_finecut/navigation_support', nonempty=False) == identifiers,
            'slot_finecut:parent_navigation_support_ids')
    support = {row['evidence_id']: row for row in temporal}
    for evidence in report['evidence']:
        start = number(evidence.get('start_s'), 'slot_finecut/navigation/start')
        end = number(evidence.get('end_s'), 'slot_finecut/navigation/end')
        require(0 <= start <= end <= binding['local_end_s'], 'slot_finecut:parent_navigation_time_bounds')
        text(evidence.get('observed_fact'), 'slot_finecut/navigation/observed_fact')
        require(evidence.get('kind') in FACT_KINDS and evidence.get('basis') in FACT_BASES,
                'slot_finecut:parent_navigation_evidence_kind')
        require(evidence.get('kind') != 'inference' or evidence.get('basis') != 'visual',
                'slot_finecut:inference_kind_cannot_be_visual_fact')
        row, point = support[evidence['evidence_id']], start == end
        require(row.get('start_s') == start and row.get('end_s') == end
                and row.get('temporal_kind') == ('point' if point else 'interval')
                and row.get('instant_only') is point and row.get('duration_unknown') is point
                and row.get('exposure_proof') is False
                and (not point or row.get('cannot_prove_completed_action') is True),
                'slot_finecut:parent_navigation_cannot_create_duration')
    _strings(report.get('limitations'), 'navigation/limitations')
    _strings(report.get('uncertainties'), 'navigation/uncertainties')
    forbid_keys(report, {'source_in_s', 'source_out_s', 'source_start_s', 'source_end_s',
                        'source_sha256', 'intended_takeaway', 'required_claims', 'claim_checks', 'plan'})
    return value


def parent_navigation(report, parent, slot, *, media_sha256=None, observation_scope=None):
    """Keep one model body verbatim in a separate program-bound envelope."""
    value = {'schema_version': 'sf_parent_point_navigation_v1',
             'request_binding': {'baseline_id': parent['baseline_id'], 'parent_sha256': parent['sha256'],
                 'slot_id': slot['slot_id'], 'parent_slot_start_s': slot['start_s'],
                 'parent_slot_end_s': slot['end_s'], 'local_start_s': 0,
                 'local_end_s': slot['end_s'] - slot['start_s'], 'time_domain': 'slot_local_output',
                 'media_sha256': media_sha256, 'observation_scope': observation_scope},
             'model_report': report,
             'temporal_support': [{'evidence_id': e['evidence_id'], 'start_s': e['start_s'], 'end_s': e['end_s'],
                 'temporal_kind': 'point' if e['start_s'] == e['end_s'] else 'interval',
                 'instant_only': e['start_s'] == e['end_s'], 'duration_unknown': e['start_s'] == e['end_s'],
                 'exposure_proof': False,
                 'cannot_prove_completed_action': e['start_s'] == e['end_s']} for e in report['evidence']]}
    return validate_parent_navigation(value, parent, slot)


def navigation_facts_prompt(parent, slot):
    """Forward format for navigation, never the exact movie evidence prompt."""
    prompt = neutral_facts_prompt(parent, slot)
    return prompt.replace(
        '每项区间必须0<=start_s<end_s<=observed_duration_s；无法确认持续时间写uncertainties，'
        '不要为通过校验编造或扩大时间区间。',
        '本阶段仅做导航。持续观察用0<=start_s<end_s<=observed_duration_s；'
        '仅能定位一个瞬间时允许start_s==end_s，表示持续时间未知，不能证明动作完成或观看曝光。'
        '不得为了扩大持续时间而编造区间。最终源片会另行独立精看。')


def _window_map(windows):
    if isinstance(windows, dict):
        windows = list(windows.values())
    ids(windows, "window_id", "slot_finecut/windows", nonempty=False)
    return {row["window_id"]: row for row in windows}


def _operation_source(operation, parent, slot, windows):
    index = operation.get("parent_segment_index")
    provenance = rows(parent.get("provenance"), "slot_finecut/provenance")
    require(type(index) is int and 0 <= index < len(provenance), "slot_finecut:unknown_parent_segment")
    original = _object(provenance[index], "parent_segment")
    a, b = _interval(original.get("output_in_s"), original.get("output_out_s"),
                     0, parent["duration_s"], "parent_segment_output")
    require(a < slot["end_s"] and slot["start_s"] < b, "slot_finecut:parent_segment_outside_slot")
    window = windows.get(original.get("window_id"))
    require(window is not None and window.get("source_id") == original.get("source_id")
            and window.get("source_sha256") == original.get("source_sha256"),
            "slot_finecut:source_window_binding_changed")
    _sha(original.get("source_sha256"), "parent_segment_source")
    start, end = _interval(operation.get("source_in_s"), operation.get("source_out_s"),
                           window["source_start_s"], window["source_end_s"], "operation_source")
    role_ids = rows(original.get("role_ids"), "slot_finecut/parent_roles", nonempty=False)
    require(all(isinstance(role, str) for role in role_ids) and len(role_ids) == len(set(role_ids)),
            "slot_finecut:invalid_parent_roles")
    observation = _object(window.get("observation"), "window_observation")
    usable = rows(observation.get("usable_ranges"), "slot_finecut/usable_ranges", nonempty=False)
    # Range and identity compatibility must come from ONE row, not pooled rows.
    compatible = [row for row in usable
                  if window["source_start_s"] + row["local_in_s"] - EPSILON <= start < end
                  <= window["source_start_s"] + row["local_out_s"] + EPSILON
                  and set(role_ids) <= set(row["role_ids"])]
    require(bool(compatible), "slot_finecut:operation_not_in_one_compatible_usable_range")
    return start, end


def validate_proposal(value, parent, slot, facts, windows, *, navigation=None, enforce_readability=True):
    """Validate executable proposals; a preserved label remains a model claim.

    Parent-output evidence motivates editing but cannot prove expanded movie
    slices. Each selected final slice still needs the exact-source protocol.
    """
    _binding(value, parent, slot)
    if navigation is None:
        validate_facts(facts, parent, slot)
    else:
        validate_parent_navigation(navigation, parent, slot)
        require(facts == navigation['model_report'], 'slot_finecut:parent_navigation_report_changed')
    window_map = _window_map(windows)
    candidates = rows(value.get("candidates"), "slot_finecut/candidates")
    ids(candidates, "candidate_id", "slot_finecut/candidates")
    require(len(candidates) <= 3, "slot_finecut:at_most_three_candidates")
    evidence_ids = {row["evidence_id"] for row in facts["evidence"]}
    readability_shortfalls = []
    for candidate in candidates:
        status = candidate.get("meaning_status")
        require(status in {"preserved", "unresolved"}, "slot_finecut:meaning_status")
        text(candidate.get("rationale"), "slot_finecut/candidate/rationale")
        _strings(candidate.get("limitations"), "candidate/limitations")
        require(status != "unresolved" or bool(candidate["limitations"]),
                "slot_finecut:unresolved_requires_limitations")
        for operation_index, operation in enumerate(rows(candidate.get("operations"), "slot_finecut/operations")):
            _object(operation, "operation")
            start, end = _operation_source(operation, parent, slot, window_map)
            speed = number(operation.get("speed"), "slot_finecut/speed", .5)
            hold = number(operation.get("freeze_tail_s"), "slot_finecut/freeze_tail_s")
            require(speed <= 2 and hold <= 10, "slot_finecut:unsupported_transform")
            text(operation.get("reason"), "slot_finecut/operation/reason")
            refs(operation.get("evidence_ids"), evidence_ids, "slot_finecut/operation/evidence", nonempty=True)
            for essential_index, essential in enumerate(rows(operation.get("essential_intervals"), "slot_finecut/essential_intervals")):
                _object(essential, "essential_interval")
                a, b = _interval(essential.get("source_start_s"), essential.get("source_end_s"),
                                 start, end, "essential_interval")
                minimum = number(essential.get("min_readable_s"), "slot_finecut/min_readable_s", .001)
                text(essential.get("information"), "slot_finecut/essential_interval/information")
                refs(essential.get("evidence_ids"), evidence_ids, "slot_finecut/essential_interval/evidence", nonempty=True)
                tail = essential.get("continues_in_tail_frame", False)
                require(type(tail) is bool, "slot_finecut:tail_continuation_boolean")
                require(not tail or abs(b - end) <= EPSILON, "slot_finecut:hold_cannot_expose_earlier_information")
                exposure = (b - a) / speed + (hold if tail else 0)
                if status == 'preserved' and exposure + EPSILON < minimum:
                    readability_shortfalls.append({'candidate_id': candidate['candidate_id'],
                        'operation_index': operation_index, 'essential_interval_index': essential_index,
                        'source_interval_duration_s': b-a, 'speed': speed, 'hold_s': hold,
                        'continues_in_tail_frame': tail, 'exposure_s': exposure,
                        'model_min_readable_s': minimum})
    require(not enforce_readability or not readability_shortfalls, 'slot_finecut:declared_readability_shortfall:'
            + json.dumps({'mechanical_shortfalls': readability_shortfalls,
                'instruction': '模型自行修改方案或如实标unresolved及limitations，不能编造时间证据。'}, ensure_ascii=False))
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def slots_prompt(parent, reference_cached):
    return (ROOT_OUTPUT_INSTRUCTION + "只返回一个JSON对象。自主观察当前父视频，按实际表达划分连续故事slots，数量和边界由你决定。"
            "不以某个固定段数或人工电影剧情为答案。每段说明进入/离开状态和与相邻段的关系；"
            "相邻关系不足或身份不明照实写入limitations/uncertainties。时长本身不决定是否粗剪过关；"
            "previously_recorded_reference是另一部参考视频的记录，定义迁移目标，不是当前父视频的内容描述。"
            "两者人物和事件不同是预期；不要把异源参考当作错误父片信息。"
            "slots必须无缝覆盖0到实际duration_s，不留空隙；边界和段数由你观察决定。"
            "本阶段不删除镜头、不选择原片切点、不声称语义已通过独立审核。\n" + _json({
                "parent": {key: parent[key] for key in ("baseline_id", "sha256", "duration_s")},
                "previously_recorded_reference": reference_cached,
                "time_domain": "parent output seconds, 0..duration_s",
                "response_contract": {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
                    "slots": [{"slot_id": "s1", "start_s": 0, "end_s": "实际父输出秒数",
                        "intended_takeaway": "必须留下的观众理解，不是动作清单",
                        "entry_state": "进入本段时可理解的状态", "exit_state": "离开本段时新增的状态",
                        "link_to_previous": "与前段关联或开篇", "link_to_next": "与后段关联或收束"}],
                    "limitations": [], "uncertainties": []}}))


def neutral_facts_prompt(parent, slot):
    # Only binding/timing, deliberately excluding the slot's desired meaning.
    return (ROOT_OUTPUT_INSTRUCTION + "静音独立观察提供的实际父输出片段，只返回一个JSON对象。你没有剧情目标或精剪答案。"
            "列出可观察动作、状态、身份外形、结果和反应，分别标记画面、字幕和推断。"
            "kind=inference只保存尚未验证的解释，basis只能inference或visible_text，不能是visual。"
            "字幕不能替代画面中没有出现的行动或身份；未看清的内容记为不确定。"
            "所有start_s/end_s是这个提供片段的局部输出秒数，从0开始，绝不是原电影秒数。"
            "time_domain必须原样返回slot_local_output，不能用observed_duration_s替代这个必填字段。"
            "slot_start_s/slot_end_s照模板保留父输出坐标，只有evidence时间从片段0秒开始。"
            "每项区间必须0<=start_s<end_s<=observed_duration_s；无法确认持续时间写uncertainties，"
            "不要为通过校验编造或扩大时间区间。"
            "不写原片范围、剪辑计划、成功判定或参考故事。\n" + _json({
                "baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
                "slot_id": slot["slot_id"], "slot_start_s": slot["start_s"], "slot_end_s": slot["end_s"],
                "time_domain": "slot_local_output",
                "observed_duration_s": slot["end_s"] - slot["start_s"],
                "response_contract": {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
                    "slot_id": slot["slot_id"], "slot_start_s": slot["start_s"], "slot_end_s": slot["end_s"],
                    "time_domain": "slot_local_output", "evidence": [{"evidence_id": "e1", "start_s": 0,
                        "end_s": "片段内实际局部秒数", "observed_fact": "具体观察",
                        "kind": "action", "basis": "visual"}],
                    "allowed_kind_values": sorted(FACT_KINDS), "allowed_basis_values": sorted(FACT_BASES),
                    "limitations": [], "uncertainties": []}}))


def proposal_prompt(parent, slot, facts, windows, *, navigation=None):
    return (ROOT_OUTPUT_INSTRUCTION + "只返回一个JSON对象。根据实际父输出事实，自主为这一slot提出1到3种精剪方案。"
            "保留intended_takeaway、进入/离开状态及相邻关联，缺依据标unresolved。"
            "kind=inference或basis=visible_text/inference不证明真实画面动作；这些仅是文字或解释线索。"
            "若提供parent_navigation，model_report保持原模型局部标注，request_binding才是程序父坐标。"
            "temporal_support中的point是瞬间线索，持续时间未知，不能证明动作完成或观看曝光；"
            "不能将点时间人工扩展为事实区间，最终原片仍需独立观察验证。"
            "可以缩短过程，也可以为必要动作、人物辨認、指向关系或结果加长；不要求每段更短。"
            "操作引用父provenance列表的零基index，原片秒数只从同window的一个已看usable_range内选择。"
            "source_in_s/source_out_s、essential的source_start_s/source_end_s、speed、freeze_tail_s和"
            "min_readable_s必须是number。operation及每个essential的evidence_ids都必须是非空ID列表。"
            "usable_range的local_in_s/local_out_s是窗口局部时间；原片域等于window.source_start_s加局部值。"
            "可以超出父片已选的原片区间，但扩展内容仍是待验证提案；父输出facts不证明新原片动作。"
            "模型估计min_readable_s不是人类阅读测量。尾帧只保留实际末帧，不能增加早先动作的观看时间。"
            "没有严格观测到的参考变速不能冒称复刻；使用通用优化须如实说明。\n"
            + HANDBOOK.read_text(encoding="utf-8") + "\n" + _json({
                "parent": parent, "slot": slot, "parent_output_facts": facts,
                **({'parent_navigation': navigation} if navigation is not None else {}),
                "watched_windows": windows,
                "response_contract": {"baseline_id": parent["baseline_id"], "parent_sha256": parent["sha256"],
                    "slot_id": slot["slot_id"], "candidates": [{"candidate_id": "c1",
                        "operations": [{"parent_segment_index": 0, "source_in_s": "原片秒数",
                            "source_out_s": "原片秒数", "speed": 1, "freeze_tail_s": 0,
                            "evidence_ids": ["父输出事实ID"], "reason": "新增信息/精切/速度/停留依据",
                            "essential_intervals": [{"source_start_s": "原片秒数", "source_end_s": "原片秒数",
                                "min_readable_s": "本次模型估计的必要观看秒数", "information": "必须看清的信息",
                                "evidence_ids": ["父输出事实ID"], "continues_in_tail_frame": False}]}],
                        "meaning_status": "preserved", "rationale": "候选理由", "limitations": []}]}}))
