"""纯内存参考/ASR 与文件系统替身；禁止真实 HTTP、转码和任务目录写入。"""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error
from unittest.mock import MagicMock, Mock

import pytest

from omni_story.library.storyboard import (
    READING_FIELDS, StoryboardViolation, storyboard_prompt, storyboard_repair_prompt, validate_storyboard,
)
from scripts import storyboard_trial as trial


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(trial.qfs, "http_post", Mock(side_effect=AssertionError("禁止网络")))
    monkeypatch.setattr(trial.subprocess, "run", Mock(side_effect=AssertionError("禁止真实子进程")))


@pytest.fixture
def reference():
    texts = ["这谁啊", "这谁把这么多钱放在我们家冰箱里了", "这这谁都不承认是", "你瞧瞧这是你家冰箱了",
             "我们家祖祖被连终会动了", "求他", "一分家都不肯终全"]
    times = [(6.13, 6.93), (8.2, 11.6), (12.4, 15.1), (16.1, 18.8),
             (20.4, 23.8), (24.5, 25.2), (27.1, 29.7)]
    asr = [dict(segment_id=f"src_zhaodehan_{start:.3f}", source_start_s=start,
                source_end_s=end, text=text, no_speech_prob=.05)
           for (start, end), text in zip(times, texts)]
    reading = dict(viewer_takeaway="家中藏有巨款的人先推诿，面对质问又哭穷。", theme="藏钱与哭穷的反差",
        intended_takeaway="看清推诿的荒诞", value_judgment={"stance": "讽刺"},
        visible_evidence=[{"start_s": 0, "end_s": 4, "observed_fact": "打开冰箱露出钱"},
                          {"start_s": 19, "end_s": 26, "observed_fact": "男子哭泣"},
                          {"start_s": 26, "end_s": 30, "observed_fact": "男子继续诉说"}],
        editing_methods=[{"method": "反应镜头交替"}], strategy={"roles": {"A": "质问者", "B": "辩解者"}},
        cultural_context=[])
    actions = ["A 打开冰箱，里面堆满纸币，B 在旁边", "B 看着冰箱转头说话", "B 指着冰箱说话",
               "B 摇头，A 看着他", "A 指向冰箱质问 B", "B 哭泣，朝 A 诉说并恳求", "B 继续哭诉"]
    intents = [None, "B 问 A：这是谁放的", "B 对 A 推说不知道谁把钱放进自己家", "B 对 A 继续推诿",
               "A 反问 B：这是你家冰箱", "B 向 A 哭诉家贫并恳求", "B 向 A 强调舍不得花钱"]
    assignments = [[], [0], [1], [2], [3], [4, 5], [6]]
    boundaries = [0, 4, 8, 12, 15.5, 19, 26, 30.03]
    cells = [dict(id=f"s{i + 1}", start_s=boundaries[i], end_s=boundaries[i + 1], roles=["A", "B"],
        action=action, lines=[] if intents[i] is None else [dict(speaker="A" if i == 4 else "B",
            intent=intents[i], ref_asr_ids=[asr[n]["segment_id"] for n in assignments[i]])],
        story_role=["揭露", "铺垫", "对峙", "对峙", "转折", "高潮", "结尾"][i],
        basis="visible" if i == 0 else "visible+asr", core=i in (0, 2, 4, 5, 6))
        for i, action in enumerate(actions)]
    return SimpleNamespace(duration=30.03, asr=asr, reading=reading,
                           data=dict(storyboard=cells, dialogue_turns=3, pace="先快后慢，最后长段哭诉"))


@pytest.fixture
def snow_mountain(reference):
    reference.duration = 37.916009
    reference.asr = []
    boundaries = [0, 6, 12, 18, 24, 29, 34, 38]
    for index, cell in enumerate(reference.data["storyboard"]):
        cell.update(start_s=boundaries[index], end_s=boundaries[index + 1], lines=[],
                    roles=[], action="雪山中的狐狸救援镜头", basis="visible")
    reference.data.update(dialogue_turns=0, pace="雪山救援按时间顺序展开")
    return reference


@pytest.fixture
def zhaodehan_two_speaker(reference):
    cells = reference.data["storyboard"]
    cells[1]["lines"].extend(cells[2]["lines"])
    cells[1]["end_s"] = 12
    cells[2].update(start_s=12, end_s=19, action="B 推诿，A 指向冰箱反问", lines=[
        dict(speaker="B", intent="推说谁都不承认这些钱是自己的", ref_asr_ids=[reference.asr[2]["segment_id"]]),
        dict(speaker="A", intent="反问：你瞧瞧，这是你家冰箱", ref_asr_ids=[reference.asr[3]["segment_id"]])])
    reference.data["storyboard"] = [cells[i] for i in (0, 1, 2, 5, 6)]
    for index, cell in enumerate(reference.data["storyboard"]):
        cell["id"] = f"s{index + 1}"
    reference.data["dialogue_turns"] = 1
    return reference


@pytest.fixture
def xuezhenzhu():
    texts = ["Base", "张晓三", "你听好了", "罗子军的妈妈", "你给我站住", "你不要脸了",
             "我怎么养了你这么个东西", "你还有理了是吧", "我告诉你张晓三", "你给我等着", "罗子军你管管你妈"]
    times = [(0, .8), (2.5, 3.5), (5.5, 7), (8, 9.5), (10.5, 12), (12.6, 14),
             (15.5, 17), (18, 19.5), (20.5, 22), (23, 24.5), (26, 28.5)]
    asr = [dict(segment_id=f"src_xzz_{start}", source_start_s=start, source_end_s=end,
                text=text, no_speech_prob=.62, avg_logprob=-.6)
           for (start, end), text in zip(times, texts)]
    reading = dict(viewer_takeaway="母亲与年轻男子当街争吵，互相戳破对方底细。", theme="市井争吵的身份反转",
        intended_takeaway="看吵闹背后的护短", value_judgment={"stance": "嘲笑"},
        visible_evidence=[{"start_s": 0, "end_s": 30, "observed_fact": "两人在街边激烈争吵"}],
        editing_methods=[{"method": "正反打快速交替"}], strategy={"roles": {"A": "母亲", "B": "年轻男子"}},
        cultural_context=[])
    grouped_asr = [[1], [2, 3], [4, 5], [6, 7], [8, 9], [10]]
    speakers = ["A", "B"] * 5
    boundaries = [0, 5, 10, 15, 20, 25, 30]
    cells, next_line = [], 0
    for index, group in enumerate(grouped_asr):
        cell_lines = [dict(speaker=speakers[next_line + offset], intent=f"争吵发言{next_line + offset + 1}",
                           ref_asr_ids=[asr[group[offset]]["segment_id"]])
                      for offset in range(len(group))]
        next_line += len(group)
        cells.append(dict(id=f"s{index + 1}", start_s=boundaries[index], end_s=boundaries[index + 1],
            roles=["A", "B"], action="两人在街边激烈争吵，正反打快速交替", lines=cell_lines,
            story_role=["铺垫", "对峙", "对峙", "转折", "高潮", "结尾"][index],
            basis="visible+asr", core=index == 4))
    return SimpleNamespace(duration=30.0, asr=asr, reading=reading,
        data=dict(storyboard=cells, dialogue_turns=10, pace="连环短句快速来回争吵",
                  asr_rejected=[dict(id="src_xzz_0", reason="误识别")]))


def remove_assignment(ref, *indices):
    ids = {ref.asr[index]["segment_id"] for index in indices}
    for cell in ref.data["storyboard"]:
        for line in cell["lines"]:
            line["ref_asr_ids"] = [sid for sid in line["ref_asr_ids"] if sid not in ids]


def validate(ref):
    return validate_storyboard(ref.data, duration_s=ref.duration, asr_segments=ref.asr)


def violation(ref, code):
    with pytest.raises(StoryboardViolation) as caught:
        validate(ref)
    error = caught.value
    assert error.code == "storyboard:" + code
    assert error.feedback["repair_hint"]
    assert all(key in error.feedback for key in ("uncovered_ranges", "unassigned_asr_ids",
               "overlapping_cell_pairs", "too_long_cells", "too_short_cells"))
    json.dumps(error.feedback, ensure_ascii=False)
    return error.feedback


def test_realistic_storyboard_preserves_all_asr_and_seven_second_closeup(reference):
    before = deepcopy(reference.data)
    result = validate(reference)
    assert reference.data == before
    assert len(result["storyboard"]) == 7 > len(reference.reading["visible_evidence"])
    assert result["normalisations"] == result["ignorable_asr"] == []
    assert result["dialogue_turns"] == 3
    assert validate_storyboard(result, duration_s=reference.duration, asr_segments=reference.asr) == result


@pytest.mark.parametrize("start,end", [(-.5001, 4), (4, 4), (5, 4), (0, 30.5301),
                                      (True, 4), (0, float("inf")), (float("nan"), 4)])
def test_time_range_violation(reference, start, end):
    reference.data["storyboard"][0].update(start_s=start, end_s=end)
    feedback = violation(reference, "time_range")
    assert feedback["issues"][0]["repair_hint"]


@pytest.mark.parametrize("offset", [1 / 30, .5])
def test_end_rounding_is_clamped_and_recorded(reference, offset):
    reference.data["storyboard"][-1]["end_s"] += offset
    result = validate(reference)
    assert result["storyboard"][-1]["end_s"] == 30.03
    assert result["normalisations"] == [dict(path="$.storyboard[6].end_s", before=30.03 + offset,
        after=30.03, reason="将不超过 0.5 秒的末尾舍入超时收回参考时长")]


def test_real_snow_mountain_last_cell_34_to_38_is_clamped(snow_mountain):
    before = deepcopy(snow_mountain.data)
    result = validate(snow_mountain)
    assert result["storyboard"][-1]["start_s"] == 34
    assert result["storyboard"][-1]["end_s"] == 37.916009
    assert result["normalisations"] == [dict(path="$.storyboard[6].end_s", before=38,
        after=37.916009, reason="将不超过 0.5 秒的末尾舍入超时收回参考时长")]
    assert snow_mountain.data == before
    assert validate_storyboard(result, duration_s=snow_mountain.duration, asr_segments=[]) == result


@pytest.mark.parametrize("start", [-.01, -.5])
def test_negative_start_rounding_is_clamped_and_recorded(reference, start):
    reference.data["storyboard"][0]["start_s"] = start
    result = validate(reference)
    assert result["storyboard"][0]["start_s"] == 0
    assert result["normalisations"] == [dict(path="$.storyboard[0].start_s", before=start,
        after=0, reason="将不超过 0.5 秒的负起点舍入误差收为 0")]


@pytest.mark.parametrize("overlap", [.01, .3])
def test_adjacent_rounding_overlap_is_clamped_and_recorded(reference, overlap):
    reference.data["storyboard"][3]["end_s"] = 15.5 + overlap
    result = validate(reference)
    assert result["storyboard"][3]["end_s"] == result["storyboard"][4]["start_s"] == 15.5
    assert result["normalisations"] == [dict(path="$.storyboard[3].end_s", before=15.5 + overlap,
        after=15.5, reason="将相邻格不超过 0.3 秒的舍入重叠收至后格起点")]


@pytest.mark.parametrize("boundary", ["start", "end", "overlap"])
def test_minimum_length_is_checked_after_rounding_clamps(reference, boundary):
    cells = reference.data["storyboard"]
    if boundary == "start":
        cells[0].update(start_s=-.2, end_s=1.4)
        cell_id = "s1"
    elif boundary == "end":
        cells[-1].update(start_s=28.6, end_s=30.5)
        cell_id = "s7"
    else:
        cells[0]["end_s"] = 1.6
        cells[1]["start_s"] = 1.4
        cell_id = "s1"
    assert violation(reference, "cell_duration")["too_short_cells"][0]["cell_id"] == cell_id


def test_out_of_order_is_not_silently_sorted(reference):
    cells = reference.data["storyboard"]
    cells[1], cells[2] = cells[2], cells[1]
    assert violation(reference, "time_order")["issues"][0]["cell_pairs"]


def test_overlaps_are_listed(reference):
    reference.data["storyboard"][3]["end_s"] = 15.8001
    pairs = violation(reference, "overlap")["overlapping_cell_pairs"]
    assert pairs[0]["cell_ids"] == ["s4", "s5"]
    assert pairs[0]["overlap_s"] == pytest.approx(.3001)


def test_containment_is_not_treated_as_rounding_overlap(reference):
    reference.data["storyboard"][1].update(start_s=3.8, end_s=3.9)
    assert violation(reference, "overlap")["overlapping_cell_pairs"][0]["overlap_s"] == pytest.approx(.1)


@pytest.mark.parametrize("index,end,field", [(0, 1.49, "too_short_cells"), (6, 34.01, "too_long_cells")])
def test_cell_length_feedback(reference, index, end, field):
    if index == 6:
        reference.duration = end
    reference.data["storyboard"][index]["end_s"] = end
    assert violation(reference, "cell_duration")[field][0]["cell_id"] == f"s{index + 1}"


def test_coverage_reports_exact_head_and_tail_gaps(reference):
    reference.data["storyboard"][0]["start_s"] = 2
    reference.data["storyboard"][-1]["end_s"] = 28
    assert violation(reference, "coverage")["uncovered_ranges"] == [
        {"start_s": 0, "end_s": 2}, {"start_s": 28, "end_s": 30.03}]


def test_ninety_percent_coverage_and_minimum_length_are_inclusive(reference):
    reference.data["storyboard"][0]["start_s"] = 1.5
    reference.data["storyboard"][-1]["end_s"] = 28.527
    assert validate(reference)["storyboard"][0]["start_s"] == 1.5
    reference.data["storyboard"][0]["start_s"] = 2.5
    reference.data["storyboard"][-1]["end_s"] = 30.03
    assert validate(reference)["storyboard"][0]["end_s"] - 2.5 == 1.5


@pytest.mark.parametrize("count", [3, 22])
def test_cell_count_limits_have_actionable_bounds(reference, count):
    template = reference.data["storyboard"][0]
    reference.data["storyboard"] = [dict(template, start_s=i * 30.03 / count,
        end_s=(i + 1) * 30.03 / count) for i in range(count)]
    detail = violation(reference, "cell_count")["issues"][0]
    assert (detail["minimum"], detail["maximum"], detail["count"]) == (4, 21, count)


def test_empty_storyboard_reports_cell_count_and_lists_unassigned_asr(reference):
    reference.data["storyboard"] = []
    feedback = violation(reference, "cell_count")
    assert [row["id"] for row in feedback["unassigned_asr_ids"]] == [
        row["segment_id"] for row in reference.asr]


def test_unassigned_asr_contains_id_time_and_original_text(reference):
    reference.data["storyboard"][1]["lines"][0]["ref_asr_ids"] = []
    assert violation(reference, "asr_assignment")["unassigned_asr_ids"] == [
        dict(id=reference.asr[0]["segment_id"], start_s=6.13, end_s=6.93, text="这谁啊")]


@pytest.mark.parametrize("mode", ["duplicate_cell", "duplicate_within_line", "duplicate_across_lines", "unknown", "wrong_time"])
def test_bad_asr_assignments(reference, mode):
    cells = reference.data["storyboard"]
    sid = reference.asr[0]["segment_id"]
    if mode == "duplicate_cell":
        cells[2]["lines"][0]["ref_asr_ids"].append(sid)
    elif mode == "duplicate_within_line":
        cells[1]["lines"][0]["ref_asr_ids"].append(sid)
    elif mode == "duplicate_across_lines":
        cells[1]["lines"].append(dict(speaker="B", intent="回应 A", ref_asr_ids=[sid]))
    elif mode == "unknown":
        cells[1]["lines"][0]["ref_asr_ids"].append("invented")
    else:
        cells[1]["lines"][0]["ref_asr_ids"].remove(sid)
        cells[6]["lines"][0]["ref_asr_ids"].append(sid)
    detail = violation(reference, "asr_assignment")["issues"][0]
    assert detail[{"duplicate_cell": "duplicate_asr_ids", "duplicate_within_line": "duplicate_asr_ids",
                   "duplicate_across_lines": "duplicate_asr_ids",
                   "unknown": "unknown_asr_ids", "wrong_time": "mistimed_asr_ids"}[mode]]


def test_nearest_cell_within_one_second_is_allowed_but_farther_cell_is_not(reference):
    reference.asr.append(dict(id="gap", start_s=29.5, end_s=29.8, text="嗯"))
    reference.data["storyboard"][-1]["end_s"] = 28.8
    reference.data["storyboard"][-1]["lines"][0]["ref_asr_ids"].append("gap")
    validate(reference)
    reference.asr[-1].update(start_s=29.81, end_s=30)
    assert violation(reference, "asr_assignment")["issues"][0]["mistimed_asr_ids"][0]["distance_s"] > 1


def test_within_one_second_must_also_be_nearest_cell(reference):
    reference.asr[0].update(source_start_s=8.4, source_end_s=8.8)
    assert violation(reference, "asr_assignment")["issues"][0]["mistimed_asr_ids"][0]["nearest_cell_ids"] == ["s3"]


def test_ignorable_asr_is_empty_text_only_no_speech_prob_is_informational(reference):
    reference.asr.extend([dict(id="noise", start_s=1, end_s=2, text="误识别", no_speech_prob=.6),
                          dict(id="empty", start_s=2, end_s=3, text="  ", no_speech_prob=0)])
    reference.data["storyboard"][0]["lines"].append(dict(speaker="A", intent="质问这是谁", ref_asr_ids=["noise"]))
    for probability in (.6, .95):
        reference.asr[-2]["no_speech_prob"] = probability
        assert [row["id"] for row in validate(reference)["ignorable_asr"]] == ["empty"]
    reference.data["storyboard"][0]["lines"][-1]["ref_asr_ids"] = []
    for probability in (.6, .95):
        reference.asr[-2]["no_speech_prob"] = probability
        assert violation(reference, "asr_assignment")["unassigned_asr_ids"][0]["id"] == "noise"


def test_real_xuezhenzhu_constant_062_probability_still_requires_assignment(xuezhenzhu):
    assert len(xuezhenzhu.asr) == 11 and all(row["no_speech_prob"] == .62 for row in xuezhenzhu.asr)
    before = deepcopy(xuezhenzhu.data)
    result = validate(xuezhenzhu)
    assert result["ignorable_asr"] == [] and result["normalisations"] == []
    assert result["asr_rejected"] == [dict(id="src_xzz_0", reason="误识别")]
    assert all("src_xzz_0" not in line["ref_asr_ids"] for cell in result["storyboard"] for line in cell["lines"])
    assert xuezhenzhu.data == before
    assert validate_storyboard(result, duration_s=xuezhenzhu.duration, asr_segments=xuezhenzhu.asr) == result


def test_real_xuezhenzhu_rejecting_four_of_eleven_exceeds_the_quarter_cap(xuezhenzhu):
    remove_assignment(xuezhenzhu, 1, 2, 3)
    rejected = [n for n in (0, 1, 2, 3)]
    xuezhenzhu.data["asr_rejected"] = [dict(id=xuezhenzhu.asr[n]["segment_id"], reason="噪声") for n in rejected]
    detail = violation(xuezhenzhu, "asr_rejected")["issues"][0]
    assert (detail["count"], detail["limit"], detail["non_empty_asr"]) == (4, 3, 11)
    assert [row["id"] for row in detail["rejected_asr"]] == [
        xuezhenzhu.asr[n]["segment_id"] for n in rejected]
    assert [row["text"] for row in detail["rejected_asr"]][:2] == ["Base", "张晓三"]


def test_real_xuezhenzhu_rejecting_exactly_the_rounded_quarter_cap_passes(xuezhenzhu):
    remove_assignment(xuezhenzhu, 1, 2)
    xuezhenzhu.data["asr_rejected"] = [dict(id=xuezhenzhu.asr[n]["segment_id"], reason="噪声")
                                       for n in (0, 1, 2)]
    assert len(validate(xuezhenzhu)["asr_rejected"]) == 3


def test_real_xuezhenzhu_high_probability_line_without_rejection_or_assignment_is_unassigned(xuezhenzhu):
    remove_assignment(xuezhenzhu, 5)
    feedback = violation(xuezhenzhu, "asr_assignment")
    assert [row["id"] for row in feedback["unassigned_asr_ids"]] == [xuezhenzhu.asr[5]["segment_id"]]
    assert feedback["unassigned_asr_ids"][0]["text"] == "你不要脸了"
    assert xuezhenzhu.asr[5]["no_speech_prob"] == .62


def test_rejected_asr_id_must_exist(xuezhenzhu):
    xuezhenzhu.data["asr_rejected"].append(dict(id="invented", reason="噪声"))
    assert violation(xuezhenzhu, "asr_rejected")["issues"][0]["unknown_asr_ids"] == ["invented"]


def test_rejected_asr_id_may_not_stay_assigned_to_a_line(xuezhenzhu):
    xuezhenzhu.data["asr_rejected"].append(dict(id=xuezhenzhu.asr[5]["segment_id"], reason="误识别"))
    detail = violation(xuezhenzhu, "asr_rejected")["issues"][0]
    assert detail["rejected_in_lines"] == [dict(id=xuezhenzhu.asr[5]["segment_id"], cell_ids=["s3"])]


def test_duplicate_rejected_ids_keep_first_entry_and_are_recorded(xuezhenzhu):
    xuezhenzhu.data["asr_rejected"].append(dict(id="src_xzz_0", reason="噪声"))
    result = validate(xuezhenzhu)
    assert result["asr_rejected"] == [dict(id="src_xzz_0", reason="误识别")]
    assert result["normalisations"] == [dict(path="$.asr_rejected",
        before=[dict(id="src_xzz_0", reason="误识别"), dict(id="src_xzz_0", reason="噪声")],
        after=[dict(id="src_xzz_0", reason="误识别")],
        reason="去掉 asr_rejected 中重复的 id，保留每个 id 的首次出现")]


@pytest.mark.parametrize("value", ["误识别", [dict(id="src_xzz_0")], [dict(id="", reason="噪声")],
    [dict(reason="噪声")], [dict(id="src_xzz_0", reason="  ")], [dict(id=1, reason="噪声")]])
def test_malformed_asr_rejected_is_repairable_schema_feedback(xuezhenzhu, value):
    xuezhenzhu.data["asr_rejected"] = value
    assert violation(xuezhenzhu, "schema")["issues"][0]["path"] == "$.asr_rejected"


def test_core_required(reference):
    for cell in reference.data["storyboard"]:
        cell["core"] = False
    violation(reference, "core_required")


def test_ids_are_renumbered_with_before_after_reasons(reference):
    reference.data["storyboard"][0]["id"] = "b99"
    reference.data["storyboard"][1].pop("id")
    records = validate(reference)["normalisations"]
    assert [(row["path"], row["before"], row["after"]) for row in records] == [
        ("$.storyboard[0].id", "b99", "s1"), ("$.storyboard[1].id", None, "s2")]
    assert all(row["reason"] for row in records)


@pytest.mark.parametrize("claimed", [99, None, True, "3"])
def test_dialogue_turns_are_recomputed_instead_of_rejected(reference, claimed):
    reference.data["dialogue_turns"] = claimed
    result = validate(reference)
    assert result["dialogue_turns"] == 3
    assert result["normalisations"][-1]["after"] == 3
    assert result["normalisations"][-1]["path"] == "$.dialogue_turns"


def test_real_zhaodehan_preserves_two_speakers_in_cell_and_counts_both(zhaodehan_two_speaker):
    before = deepcopy(zhaodehan_two_speaker.data)
    result = validate(zhaodehan_two_speaker)
    lines = result["storyboard"][2]["lines"]
    assert [line["speaker"] for line in lines] == ["B", "A"]
    assert [line["ref_asr_ids"] for line in lines] == [
        [row["segment_id"]] for row in zhaodehan_two_speaker.asr[2:4]]
    assert result["dialogue_turns"] == 3
    assert result["normalisations"][-1]["path"] == "$.dialogue_turns"
    assert result["normalisations"][-1]["before"] == 1
    assert result["normalisations"][-1]["after"] == 3
    assert zhaodehan_two_speaker.data == before


@pytest.mark.parametrize("speakers,turns", [([], 0), ([None], 0), ([None, None], 0),
    (["A", "A"], 1), (["A", "B", "A"], 3), ([None, "A"], 1), (["A", None], 1),
    (["A", None, "A"], 1), (["A", None, "B"], 3), (["A", None, None, "B"], 3),
    ([None, "A", None, "B", None], 3), (["A", None, "B", None, "A"], 5)])
def test_dialogue_turns_across_all_lines_with_documented_null_rule(reference, speakers, turns):
    reference.asr = []
    for cell in reference.data["storyboard"]:
        cell["lines"] = []
    midpoint = len(speakers) // 2
    for cell, group in zip(reference.data["storyboard"], (speakers[:midpoint], speakers[midpoint:])):
        cell["lines"] = [dict(speaker=speaker, intent="向对方说话", ref_asr_ids=[]) for speaker in group]
    assert validate(reference)["dialogue_turns"] == turns


def test_legacy_line_objects_and_silence_are_normalised_and_recorded(reference):
    before = deepcopy(reference.data)
    for cell in reference.data["storyboard"]:
        lines = cell.pop("lines")
        cell["line"] = lines[0] if lines else None
    legacy = deepcopy(reference.data)
    result = validate(reference)
    assert result["storyboard"] == before["storyboard"]
    assert all("line" not in cell for cell in result["storyboard"])
    assert result["normalisations"] == [dict(path=f"$.storyboard[{i}].lines",
        before=cell["line"], after=before["storyboard"][i]["lines"],
        reason="将旧版 line 转为按发言顺序排列的 lines") for i, cell in enumerate(legacy["storyboard"])]
    assert reference.data == legacy
    assert validate_storyboard(result, duration_s=reference.duration, asr_segments=reference.asr) == result


@pytest.mark.parametrize("field,value", [("roles", "A"), ("core", "true"), ("basis", {}),
                                         ("story_role", "总结"), ("action", ""), ("lines", {}),
                                         ("lines", None), ("lines", [None]), ("lines", [{}]),
                                         ("lines", [dict(speaker="C", intent="说话", ref_asr_ids=[])]),
                                         ("lines", [dict(speaker="A", intent="", ref_asr_ids=[])]),
                                         ("lines", [dict(speaker="A", intent="说话", ref_asr_ids=[1])])])
def test_malformed_cells_produce_repairable_schema_feedback(reference, field, value):
    reference.data["storyboard"][0][field] = value
    assert violation(reference, "schema")["issues"][0]["path"]


def test_missing_lines_and_legacy_line_is_a_schema_violation(reference):
    reference.data["storyboard"][0].pop("lines")
    assert violation(reference, "schema")["issues"][0]["path"] == "$.storyboard[0].lines"


def test_legacy_line_alongside_lines_is_a_schema_violation(reference):
    reference.data["storyboard"][0]["line"] = None
    assert violation(reference, "schema")["issues"][0]["path"] == "$.storyboard[0].line"


def test_prompt_receives_reading_and_adapted_asr_without_mutation(reference):
    before = deepcopy(reference.asr)
    prompt = storyboard_prompt(reference.reading, reference.asr, reference.duration)
    context = json.loads(prompt.split("输入：\n", 1)[1])
    assert set(context["reference_reading"]) == set(READING_FIELDS)
    assert context["reference_reading"] == reference.reading
    assert context["asr"][0] == dict(id=before[0]["segment_id"], start_s=6.13, end_s=6.93,
                                     text="这谁啊", no_speech_prob=.05)
    assert "画面可见字幕优先" in prompt and "每一次对话" in prompt and "不使用《功夫熊猫》" in prompt
    assert '"lines":[{"speaker":"A"' in prompt and "无台词时 lines=[]" in prompt
    assert "按时间顺序逐条列入 lines" in prompt and "一格会超过 8 秒时再拆格" in prompt
    assert "不同说话者的来回要分格" not in prompt
    assert "连续 null 视为一组" in prompt
    assert "no_speech_prob" in prompt and "整段音频窗口的常数" in prompt and "不按它豁免归属" in prompt
    assert '"asr_rejected":[{"id"' in prompt and "只有确认不是人物台词时才能剔除" in prompt
    assert "以字幕为准" in prompt and "歌词和画外音如果是故事的一部分要保留" in prompt
    assert "不得超过非空 ASR 句数的 25%（向上取整，至少允许剔除 1 句）" in prompt
    assert before == reference.asr
    reference.data["storyboard"][1]["lines"][0]["ref_asr_ids"] = []
    with pytest.raises(StoryboardViolation) as caught:
        validate(reference)
    repair = storyboard_repair_prompt("上次回复", caught.value)
    assert "上次回复" in repair and "unassigned_asr_ids" in repair and "这谁啊" in repair


def test_request_shape_and_record_redact_key_and_strip_base64():
    media, key = b"standalone-reference-video", "synthetic-secret-key"
    request = trial.build_request("提示词 " + key, media, key)
    assert request["endpoint"] == trial.qfs.ENDPOINT
    assert request["headers"] == {"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                  "X-Title": "4.5V MCP Local"}
    body = request["payload"]
    assert {k: body[k] for k in ("model", "thinking", "temperature", "max_tokens")} == dict(
        model="glm-5.3-flash", thinking={"type": "disabled"}, temperature=.2, max_tokens=8192)
    encoded = base64.b64encode(media).decode()
    assert body["messages"][0]["content"][0]["video_url"]["url"] == "data:video/mp4;base64," + encoded
    record = trial.request_record(request, key)
    serialized = json.dumps(record)
    assert key not in serialized and encoded not in serialized and "base64," not in serialized
    assert record["payload"]["messages"][0]["content"][0]["video_url"]["url"] == dict(
        mime="video/mp4", sha256=hashlib.sha256(media).hexdigest(), size_bytes=len(media))
    assert request["headers"]["Authorization"] == "Bearer " + key


@pytest.mark.parametrize("out", ["/virtual/job/output", "/virtual/job", "/virtual"])
def test_out_inside_job_or_targeting_job_is_refused_without_writes(monkeypatch, out):
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    mkdir = Mock(side_effect=AssertionError("不得写入"))
    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(SystemExit) as caught:
        trial.main(["/virtual/job", "--out", out])
    assert caught.value.code == 2
    mkdir.assert_not_called()


@pytest.fixture
def memory_job(reference, monkeypatch):
    job, out = Path("/virtual/job"), Path("/virtual/output/job")
    files = {job / "reference_reading.json": json.dumps(reference.reading),
             job / "reference_asr.json": json.dumps({"segments": reference.asr}),
             job / "reference_catalog/inventory.json": json.dumps({"sources": [
                 {"path": "/virtual/video.mp4", "duration_s": reference.duration}]}),
             Path("/virtual/video.mp4"): b"video-bytes"}
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "read_text", lambda path, **kwargs: files[path])
    monkeypatch.setattr(Path, "read_bytes", lambda path: files[path])
    monkeypatch.setattr(Path, "write_text", lambda path, value, **kwargs: files.__setitem__(path, value))
    monkeypatch.setattr(Path, "stat", lambda path: SimpleNamespace(st_size=len(files[path])))
    return SimpleNamespace(job=job, out=out, files=files)


def envelope(data):
    return {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}


def test_real_snow_mountain_rounding_succeeds_without_repair(snow_mountain, memory_job, monkeypatch):
    source_path = memory_job.job / "reference_catalog/inventory.json"
    inventory = json.loads(memory_job.files[source_path])
    inventory["sources"][0]["duration_s"] = snow_mountain.duration
    memory_job.files[source_path] = json.dumps(inventory)
    memory_job.files[memory_job.job / "reference_asr.json"] = json.dumps({"segments": snow_mountain.asr})
    post = Mock(side_effect=[envelope(snow_mountain.data),
        urllib.error.HTTPError("https://example.invalid", 500, "server error", {}, io.BytesIO())])
    monkeypatch.setattr(trial.qfs, "http_post", post)
    result = trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")
    assert result["status"] == "ok" and result["attempts"] == post.call_count == 1
    storyboard = json.loads(memory_job.files[memory_job.out / "storyboard.json"])
    assert storyboard["storyboard"][-1]["end_s"] == 37.916009
    assert storyboard["normalisations"][0]["before"] == 38
    assert memory_job.out / "request_2.json" not in memory_job.files
    assert memory_job.out / "failure.json" not in memory_job.files


def test_zhaodehan_markdown_keeps_speaker_and_asr_order_in_one_cell(zhaodehan_two_speaker):
    storyboard = validate(zhaodehan_two_speaker)
    markdown = trial.storyboard_markdown(storyboard, zhaodehan_two_speaker.asr)
    rows = [row for row in markdown.splitlines() if row.startswith("| s")]
    assert len(rows) == len(storyboard["storyboard"])
    columns = rows[2].split(" | ")
    assert columns[4] == "B：推说谁都不承认这些钱是自己的／A：反问：你瞧瞧，这是你家冰箱"
    sid_b, sid_a = [row["segment_id"] for row in zhaodehan_two_speaker.asr[2:4]]
    assert columns[5] == f"B：{sid_b}：这这谁都不承认是／A：{sid_a}：你瞧瞧这是你家冰箱了"
    assert "对话轮次：3" in markdown


def test_markdown_lists_rejected_asr_at_the_end(xuezhenzhu):
    markdown = trial.storyboard_markdown(validate(xuezhenzhu), xuezhenzhu.asr)
    tail = markdown.rstrip("\n").splitlines()[-1]
    assert tail.startswith("剔除 ASR")
    assert "src_xzz_0" in tail and "误识别" in tail and "Base" in tail


def test_trial_repairs_new_request_and_writes_artifacts(reference, memory_job, monkeypatch):
    bad = deepcopy(reference.data)
    bad["storyboard"][1]["lines"][0]["ref_asr_ids"] = []
    post = Mock(side_effect=[envelope(bad), envelope(reference.data)])
    monkeypatch.setattr(trial.qfs, "http_post", post)
    result = trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")
    assert result == dict(job="job", duration=30.03, asr_lines=7, cells=7, core_cells=5,
                          dialogue_turns=3, attempts=2, status="ok")
    assert post.call_count == 2
    first, second = [call.args[2] for call in post.call_args_list]
    assert first != second and "unassigned_asr_ids" in second["messages"][0]["content"][-1]["text"]
    for name in ("request_1.json", "request_2.json", "response_1.json", "response_2.json", "storyboard.json", "storyboard.md"):
        assert memory_job.out / name in memory_job.files
    assert "对应ASR原文" in memory_job.files[memory_job.out / "storyboard.md"]
    assert "我们家祖祖被连终会动了" in memory_job.files[memory_job.out / "storyboard.md"]
    assert "test-secret" not in str(memory_job.files)


@pytest.mark.parametrize("error", [TimeoutError("test-secret"), urllib.error.URLError("test-secret"),
    urllib.error.HTTPError("https://example.invalid", 500, "test-secret", {}, io.BytesIO(b"test-secret")),
    urllib.error.HTTPError("https://example.invalid", 503, "test-secret", {}, io.BytesIO(b"test-secret"))])
def test_network_errors_stop_job_once_without_persisting_exception(reference, memory_job, monkeypatch, error):
    post = Mock(side_effect=error)
    monkeypatch.setattr(trial.qfs, "http_post", post)
    result = trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")
    assert result["attempts"] == post.call_count == 1 and result["status"] == "failed"
    assert "test-secret" not in str(memory_job.files)
    failure = json.loads(memory_job.files[memory_job.out / "failure.json"])
    assert failure["automatic_resend"] is False and failure["outcome_unknown"] is True


def test_http_500_on_repair_stops_without_resend(reference, memory_job, monkeypatch):
    bad = deepcopy(reference.data)
    bad["storyboard"][1]["lines"][0]["ref_asr_ids"] = []
    post = Mock(side_effect=[envelope(bad),
        urllib.error.HTTPError("https://example.invalid", 500, "test-secret", {}, io.BytesIO())])
    monkeypatch.setattr(trial.qfs, "http_post", post)
    result = trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")
    assert result["status"] == "failed" and result["attempts"] == post.call_count == 2
    failure = json.loads(memory_job.files[memory_job.out / "failure.json"])
    assert failure["http_status"] == 500 and failure["automatic_resend"] is False


def test_max_repairs_and_invalid_json_are_bounded(reference, memory_job, monkeypatch):
    post = Mock(return_value={"choices": [{"message": {"content": "{malformed"}}]})
    monkeypatch.setattr(trial.qfs, "http_post", post)
    assert trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")["attempts"] == 3
    assert post.call_count == 3
    assert post.call_args_list[1].args[2] != post.call_args_list[2].args[2]
    assert json.loads(memory_job.files[memory_job.out / "failure.json"])["code"] == "storyboard:invalid_json"


def test_zero_repairs_stops_on_first_content_violation(reference, memory_job, monkeypatch):
    bad = deepcopy(reference.data)
    bad["storyboard"][1]["lines"][0]["ref_asr_ids"] = []
    post = Mock(return_value=envelope(bad))
    monkeypatch.setattr(trial.qfs, "http_post", post)
    result = trial.run_job(memory_job.job, memory_job.out, 0, "test-secret")
    assert result["status"] == "failed" and result["attempts"] == post.call_count == 1
    failure = json.loads(memory_job.files[memory_job.out / "failure.json"])
    assert failure["feedback"]["unassigned_asr_ids"][0]["text"] == "这谁啊"


def test_missing_key_stops_before_preparing_media(memory_job, monkeypatch):
    prepare = Mock(side_effect=AssertionError("不应转码"))
    monkeypatch.setattr(trial, "prepare_video", prepare)
    result = trial.run_job(memory_job.job, memory_job.out, 2, "")
    assert result["attempts"] == 0 and result["status"] == "failed"
    assert json.loads(memory_job.files[memory_job.out / "failure.json"])["code"] == "trial:missing_api_key"
    prepare.assert_not_called()


@pytest.mark.parametrize("count,workers", [(1, 1), (7, 5)])
def test_main_caps_parallelism_and_writes_and_prints_summary(monkeypatch, capsys, count, workers):
    files = {}
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    monkeypatch.setattr(Path, "exists", lambda path: False)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, "write_text", lambda path, value, **kwargs: files.__setitem__(path, value))
    monkeypatch.setenv("Z_AI_API_KEY", "test-secret")
    pool = MagicMock()
    pool.__enter__.return_value = pool
    pool.map.side_effect = lambda function, jobs: map(function, jobs)
    executor = Mock(return_value=pool)
    monkeypatch.setattr(trial, "ThreadPoolExecutor", executor)
    run = Mock(side_effect=lambda job, folder, repairs, key: dict(job=job.name, duration=30.03,
        asr_lines=7, cells=7, core_cells=5, dialogue_turns=3, attempts=1, status="ok"))
    monkeypatch.setattr(trial, "run_job", run)
    jobs = [f"/virtual/inputs/job{i}" for i in range(count)]
    assert trial.main([*jobs, "--out", "/virtual/output"]) == 0
    executor.assert_called_once_with(max_workers=workers)
    assert run.call_count == count
    assert len(json.loads(files[Path("/virtual/output/summary.json")])) == count
    output = capsys.readouterr().out
    assert "job | duration | asr_lines | cells | core_cells | dialogue_turns | attempts | status" in output
    assert "test-secret" not in output


def test_large_video_transcodes_only_into_output_with_audio(memory_job, monkeypatch):
    original = Path("/virtual/video.mp4")
    monkeypatch.setattr(Path, "stat", lambda path: SimpleNamespace(st_size=8_000_000 if path == original else 1_000_000))
    run = Mock(side_effect=[SimpleNamespace(stdout=b""), SimpleNamespace(stdout=json.dumps(
        {"format": {"duration": "30.03"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}).encode())])
    monkeypatch.setattr(trial.subprocess, "run", run)
    assert trial.prepare_video(original, memory_job.out) == memory_job.out / "reference_480p.mp4"
    command = run.call_args_list[0].args[0]
    assert "-nostdin" in command and command[command.index("-crf") + 1] == "30"
    assert command[command.index("-vf") + 1] == "scale=-2:480"
    assert "0:a?" in command and "-an" not in command
    assert command[-1] == str(memory_job.out / "reference_480p.mp4")


@pytest.mark.parametrize("size,sendable", [(7_500_001, True), (8 * 1024 ** 2, True),
                                           (8 * 1024 ** 2 + 1, False)])
def test_transcoded_between_7_5_and_8_mb_is_sent_and_above_8_mb_fails(memory_job, monkeypatch, size, sendable):
    original = Path("/virtual/video.mp4")
    sizes = {original: 9_000_000, memory_job.out / "reference_480p.mp4": size}
    monkeypatch.setattr(Path, "stat", lambda path: SimpleNamespace(st_size=sizes.get(path, 1_000_000)))
    monkeypatch.setattr(trial.subprocess, "run", Mock(side_effect=[
        SimpleNamespace(stdout=b""),
        SimpleNamespace(stdout=json.dumps({"format": {"duration": "30.03"},
                                           "streams": [{"codec_type": "video"}]}).encode())]))
    if sendable:
        assert trial.prepare_video(original, memory_job.out) == memory_job.out / "reference_480p.mp4"
    else:
        with pytest.raises(ValueError) as caught:
            trial.prepare_video(original, memory_job.out)
        assert "8 MB" in str(caught.value)

