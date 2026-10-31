"""Reference-only storyboards; creative content is never inferred by validation."""
from copy import deepcopy
import json
import math
import re


MIN_CELL_S_GROUPS_SHORT_REFERENCE_CUTS = 1.5
MAX_CELL_S_PRESERVES_SEVEN_SECOND_CLOSEUPS = 8.0
FRAME_TOLERANCE_S = 1 / 30
STORY_ROLES = {"铺垫", "揭露", "对峙", "转折", "高潮", "包袱", "结尾", "其他"}
READING_FIELDS = ("viewer_takeaway", "theme", "intended_takeaway", "value_judgment",
                  "visible_evidence", "editing_methods", "strategy", "cultural_context")


class StoryboardViolation(ValueError):
    def __init__(self, code, feedback):
        self.code, self.feedback = code, feedback
        super().__init__(code)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def asr_rows(asr_segments):
    """Adapt source timestamps, retaining IDs and recognizer uncertainty."""
    rows, seen = [], set()
    if not isinstance(asr_segments, list):
        raise StoryboardViolation("storyboard:asr_input", {"repair_hint": "ASR 输入必须是 segments 数组。"})
    for index, segment in enumerate(asr_segments):
        if not isinstance(segment, dict):
            segment = {}
        row = dict(id=segment.get("segment_id", segment.get("id")),
                   start_s=segment.get("source_start_s", segment.get("start_s")),
                   end_s=segment.get("source_end_s", segment.get("end_s")), text=segment.get("text"))
        probability = segment.get("no_speech_prob", 0)
        if (not isinstance(row["id"], str) or not row["id"] or row["id"] in seen
                or not all(_finite(row[k]) for k in ("start_s", "end_s"))
                or not 0 <= row["start_s"] < row["end_s"] or not isinstance(row["text"], str)
                or not _finite(probability) or not 0 <= probability <= 1):
            raise StoryboardViolation("storyboard:asr_input", dict(index=index,
                repair_hint="检查 ASR：非空唯一 segment_id、有效起止秒数、字符串 text、0..1 no_speech_prob。"))
        row["ignorable_reasons"] = (["empty_text"] if not row["text"].strip() else []) + (
            ["no_speech_prob>=0.6"] if probability >= .6 else [])
        rows.append(row)
        seen.add(row["id"])
    return rows


def storyboard_prompt(reading: dict, asr_segments: list, duration_s: float) -> str:
    if not _finite(duration_s) or duration_s <= 0:
        raise StoryboardViolation("storyboard:duration_input", dict(repair_hint="参考时长必须是有限正数。"))
    rows = asr_rows(asr_segments)
    context = dict(duration_s=duration_s,
        reference_reading={key: reading.get(key) for key in READING_FIELDS},
        asr=[{key: row[key] for key in ("id", "start_s", "end_s", "text")} for row in rows],
        ignorable_asr=[row for row in rows if row["ignorable_reasons"]])
    return """完整观看随请求附上的参考视频，只为这条参考写完整分镜故事。
像普通观众复述故事一样，按时间顺序讲清起因、经过、结果，不要只摘支持主旨的几条证据，
格数也不由 visible_evidence 条数决定。保留每一次对话交流：谁对谁说什么、对方怎样回应，
连续短句可以归为同一格，但不同说话者的来回要分格，不能压成一句主题总结。
人物用 A、B、C 等抽象字母，全片同一人始终同字母；若参考理解 strategy 已有角色，
沿用其角色对应关系（如 A_xxx 对应 A），不要互换身份。无人空镜 roles 可为 []。
action 只写可见动作/镜头内容的中性描述。line.intent 写这一格台词的意思，可重释，
应说明对谁说；说话者无法确认时 speaker=null，无台词时 line=null。
ASR 可能误识别，画面可见字幕优先；不要照着错字编故事。即使根据字幕重释，也保留对应 ASR id。
这里仅讨论参考本身，不选电影素材，不使用《功夫熊猫》的角色、剧情或电影知识补故事。
core=true 仅用于删掉这一格就使故事不成立的格，至少有一格为 true。
时间从视频 0 秒起，格按顺序、不重叠，每格 1.5–8 秒（短切镜可合为故事格，长特写可保留），
覆盖至少 90% 的参考时长；片头片尾可有少量未覆盖，总未覆盖不超过 10%。
格数在 ceil(duration_s/8) 与 ceil(duration_s/1.5) 之间。所有非 ignorable 的 ASR id
必须恰好归到一个 line.ref_asr_ids；同格可包含多条 ASR，不能遗漏或重复，也不能编造 id。
归属格与 ASR 至少重叠 0.1 秒，或为距离不超过 1 秒的最近格。ignorable_asr 可不归属。
id 可按 s1..sN 填写，程序会修正机械编号并记录 normalisations；dialogue_turns 也由程序
按有 line 的格中 speaker 变化次数加一重算（没有 line 则为 0），不替代保留完整对话的要求。
仅输出 JSON，结构如下，story_role 和 basis 从列出的值中选一个：
{"storyboard":[{"id":"s1","start_s":0,"end_s":5,"roles":["A","B"],
"action":"可见动作/镜头内容（中性描述）","line":{"speaker":"A",
"intent":"这一格台词的意思（可重释）","ref_asr_ids":["..."]},
"story_role":"铺垫|揭露|对峙|转折|高潮|包袱|结尾|其他","basis":"visible|asr|visible+asr",
"core":true}],"dialogue_turns":1,"pace":"一句话节奏描述"}
输入：
""" + json.dumps(context, ensure_ascii=False, allow_nan=False)


def validate_storyboard(data, *, duration_s, asr_segments) -> dict:
    feedback = dict(uncovered_ranges=[], unassigned_asr_ids=[], overlapping_cell_pairs=[],
                    too_long_cells=[], too_short_cells=[], issues=[])

    def issue(code, hint, **details):
        feedback["issues"].append(dict(code="storyboard:" + code, repair_hint=hint, **details))

    def fail():
        first = feedback["issues"][0]
        raise StoryboardViolation(first["code"], dict(feedback, repair_hint=first["repair_hint"]))

    def schema(path, hint):
        issue("schema", hint, path=path)
        fail()

    if not _finite(duration_s) or duration_s <= 0:
        schema("$.duration_s", "参考时长必须是有限正数。")
    rows = asr_rows(asr_segments)
    if not isinstance(data, dict) or not isinstance(data.get("storyboard"), list):
        schema("$.storyboard", "返回 JSON 对象，storyboard 必须是分镜格数组。")
    result = deepcopy(data)
    cells = result["storyboard"]
    if not isinstance(result.get("pace"), str) or not result["pace"].strip():
        schema("$.pace", "用一句非空文本描述参考节奏。")
    if not isinstance(result.get("normalisations", []), list):
        schema("$.normalisations", "机械修正记录必须是数组，首次输出可省略。")
    changes = result.setdefault("normalisations", [])

    def change(path, before, after, reason):
        changes.append(dict(path=path, before=before, after=after, reason=reason))

    for index, cell in enumerate(cells):
        path = f"$.storyboard[{index}]"
        if not isinstance(cell, dict):
            schema(path, "每格必须是 JSON 对象。")
        if not all(_finite(cell.get(key)) for key in ("start_s", "end_s")):
            issue("time_range", "start_s/end_s 必须是有限数字。", path=path)
            fail()
        roles = cell.get("roles")
        if (not isinstance(roles, list) or any(not isinstance(role, str) or
                not re.fullmatch(r"[A-Z]", role) for role in roles) or len(set(roles)) != len(roles)):
            schema(path + ".roles", "roles 使用不重复的抽象字母 A、B、C；无人空镜填 []。")
        if not isinstance(cell.get("action"), str) or not cell["action"].strip():
            schema(path + ".action", "填写非空的中性可见动作描述。")
        if (not isinstance(cell.get("story_role"), str) or cell["story_role"] not in STORY_ROLES
                or not isinstance(cell.get("basis"), str) or cell["basis"] not in {"visible", "asr", "visible+asr"}):
            schema(path, "story_role/basis 必须各选择提示词给出的一个枚举值。")
        if type(cell.get("core")) is not bool:
            schema(path + ".core", "core 必须是 JSON true/false。")
        if "line" not in cell:
            schema(path + ".line", "无台词填 null，有台词填 speaker/intent/ref_asr_ids 对象。")
        line = cell["line"]
        if line is not None:
            if (not isinstance(line, dict) or "speaker" not in line or
                    (line["speaker"] is not None and line["speaker"] not in roles) or
                    not isinstance(line.get("intent"), str) or not line["intent"].strip() or
                    not isinstance(line.get("ref_asr_ids"), list) or
                    any(not isinstance(sid, str) or not sid for sid in line["ref_asr_ids"])):
                schema(path + ".line", "line 需包含本格角色或 null 的 speaker、非空 intent、ASR id 字符串数组。")
        expected = f"s{index + 1}"
        if cell.get("id") != expected:
            change(path + ".id", cell.get("id"), expected, "按模型原有时间顺序确定机械编号")
            cell["id"] = expected
        start, end = cell["start_s"], cell["end_s"]
        if not 0 <= start < end <= duration_s + FRAME_TOLERANCE_S + 1e-9 or start >= duration_s:
            issue("time_range", "每格需满足 0≤start_s<end_s≤参考时长（末尾容差 1/30 秒）。",
                  cell_id=cell["id"], start_s=start, end_s=end, duration_s=duration_s)
        elif end > duration_s:
            change(path + ".end_s", end, duration_s, "将末尾一帧容差内的时间收回参考时长")
            cell["end_s"] = duration_s

    out_of_order = [[a["id"], b["id"]] for a, b in zip(cells, cells[1:]) if a["start_s"] > b["start_s"]]
    feedback["overlapping_cell_pairs"] = [dict(cell_ids=[a["id"], b["id"]], overlap_s=min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"]))
        for i, a in enumerate(cells) for b in cells[i + 1:]
        if min(a["end_s"], b["end_s"]) > max(a["start_s"], b["start_s"])]
    if out_of_order:
        issue("time_order", "按 start_s 升序重写分镜，不要改变故事的时间顺序。", cell_pairs=out_of_order)
    if feedback["overlapping_cell_pairs"]:
        issue("overlap", "调整相邻边界或重拆冲突格，分镜不得重叠。")
    minimum, maximum = math.ceil(duration_s / MAX_CELL_S_PRESERVES_SEVEN_SECOND_CLOSEUPS), math.ceil(duration_s / MIN_CELL_S_GROUPS_SHORT_REFERENCE_CUTS)
    if not minimum <= len(cells) <= maximum:
        issue("cell_count", "按格数上下限拆分或合并分镜，不以主题证据条数代替故事。",
              count=len(cells), minimum=minimum, maximum=maximum)
    for cell in cells:
        length = cell["end_s"] - cell["start_s"]
        detail = dict(cell_id=cell["id"], start_s=cell["start_s"], end_s=cell["end_s"], length_s=length)
        if length < MIN_CELL_S_GROUPS_SHORT_REFERENCE_CUTS - 1e-9:
            feedback["too_short_cells"].append(detail)
        if length > MAX_CELL_S_PRESERVES_SEVEN_SECOND_CLOSEUPS + 1e-9:
            feedback["too_long_cells"].append(detail)
    if feedback["too_short_cells"] or feedback["too_long_cells"]:
        issue("cell_duration", "合并短格或拆分长格，保证每格 1.5–8 秒，同时保留对话来回。")
    cursor = 0
    for cell in sorted(cells, key=lambda cell: cell["start_s"]):
        start, end = max(0, min(duration_s, cell["start_s"])), max(0, min(duration_s, cell["end_s"]))
        if start > cursor:
            feedback["uncovered_ranges"].append(dict(start_s=cursor, end_s=start))
        cursor = max(cursor, end)
    if cursor < duration_s:
        feedback["uncovered_ranges"].append(dict(start_s=cursor, end_s=duration_s))
    uncovered = sum(row["end_s"] - row["start_s"] for row in feedback["uncovered_ranges"])
    if uncovered > duration_s * .1 + 1e-9:
        issue("coverage", "补齐 uncovered_ranges 中的故事内容，覆盖参考时长至少 90%。",
              covered_s=duration_s - uncovered, required_s=duration_s * .9)

    assignments = {}
    for cell in cells:
        for sid in cell["line"]["ref_asr_ids"] if cell["line"] else []:
            assignments.setdefault(sid, []).append(cell)
    known = {row["id"] for row in rows}
    duplicates = [dict(id=sid, cell_ids=[cell["id"] for cell in mapped])
                  for sid, mapped in assignments.items() if len(mapped) > 1]
    unknown = sorted(set(assignments) - known)
    mismatched = []
    for row in rows:
        mapped = assignments.get(row["id"], [])
        if not mapped and not row["ignorable_reasons"]:
            feedback["unassigned_asr_ids"].append({key: row[key] for key in ("id", "start_s", "end_s", "text")})
        if not mapped:
            continue
        def distance(cell):
            return max(0, cell["start_s"] - row["end_s"], row["start_s"] - cell["end_s"])
        nearest = min(distance(cell) for cell in cells)
        for cell in mapped:
            overlap = min(cell["end_s"], row["end_s"]) - max(cell["start_s"], row["start_s"])
            if overlap < .1 - 1e-9 and not (distance(cell) <= 1 + 1e-9 and distance(cell) <= nearest + 1e-9):
                mismatched.append(dict(id=row["id"], start_s=row["start_s"], end_s=row["end_s"],
                    text=row["text"], cell_id=cell["id"], distance_s=distance(cell),
                    nearest_cell_ids=[c["id"] for c in cells if distance(c) <= nearest + 1e-9]))
    if feedback["unassigned_asr_ids"] or duplicates or unknown or mismatched:
        issue("asr_assignment", "每句非忽略 ASR 恰好归属一格；删除重复/未知 id，将错位台词归到重叠≥0.1秒或1秒内最近格。",
              duplicate_asr_ids=duplicates, unknown_asr_ids=unknown, mistimed_asr_ids=mismatched)
    if not any(cell["core"] for cell in cells):
        issue("core_required", "至少标出一格 core=true；只标删掉后会破坏故事的核心格。")
    if feedback["issues"]:
        fail()
    speakers = [cell["line"]["speaker"] for cell in cells if cell["line"] is not None]
    turns = 1 + sum(a != b for a, b in zip(speakers, speakers[1:])) if speakers else 0
    if type(result.get("dialogue_turns")) is not int or result["dialogue_turns"] != turns:
        change("$.dialogue_turns", result.get("dialogue_turns"), turns, "按有台词的格中说话者变化次数加一重算")
    result["dialogue_turns"] = turns
    result["ignorable_asr"] = [row for row in rows if row["ignorable_reasons"]]
    return result


def storyboard_repair_prompt(previous_reply_text, violation):
    return """上一份分镜未通过机械校验。根据反馈修复并重新输出完整 JSON，不要只返回补丁。
仍须完整观看参考、保留故事和每次对话来回；不得靠删掉未分配 ASR 或把所有格标为核心来绕过校验。
程序只会修正编号与对话轮次，时间、格长、覆盖和 ASR 归属须由你修复。
""" + json.dumps(dict(previous_reply_text=previous_reply_text, code=violation.code,
                       feedback=violation.feedback), ensure_ascii=False)
