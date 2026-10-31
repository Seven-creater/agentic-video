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
        editing_methods=[{"method": "反应镜头交替"}], strategy={"roles": {"A": "辩解者", "B": "质问者"}},
        cultural_context=[])
    actions = ["B 打开冰箱，里面堆满纸币，A 在旁边", "A 看着冰箱转头说话", "A 指着冰箱说话",
               "A 摇头，B 看着他", "B 指向冰箱质问 A", "A 哭泣，朝 B 诉说并恳求", "A 继续哭诉"]
    intents = [None, "A 问 B：这是谁放的", "A 对 B 推说不知道谁把钱放进自己家", "A 对 B 继续推诿",
               "B 反问 A：这是你家冰箱", "A 向 B 哭诉家贫并恳求", "A 向 B 强调舍不得花钱"]
    assignments = [[], [0], [1], [2], [3], [4, 5], [6]]
    boundaries = [0, 4, 8, 12, 15.5, 19, 26, 30.03]
    cells = [dict(id=f"s{i + 1}", start_s=boundaries[i], end_s=boundaries[i + 1], roles=["A", "B"],
        action=action, line=None if intents[i] is None else dict(speaker="B" if i == 4 else "A",
            intent=intents[i], ref_asr_ids=[asr[n]["segment_id"] for n in assignments[i]]),
        story_role=["揭露", "铺垫", "对峙", "对峙", "转折", "高潮", "结尾"][i],
        basis="visible" if i == 0 else "visible+asr", core=i in (0, 2, 4, 5, 6))
        for i, action in enumerate(actions)]
    return SimpleNamespace(duration=30.03, asr=asr, reading=reading,
                           data=dict(storyboard=cells, dialogue_turns=3, pace="先快后慢，最后长段哭诉"))


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


@pytest.mark.parametrize("start,end", [(-.01, 4), (4, 4), (5, 4), (0, 30.07),
                                      (True, 4), (0, float("inf")), (float("nan"), 4)])
def test_time_range_violation(reference, start, end):
    reference.data["storyboard"][0].update(start_s=start, end_s=end)
    feedback = violation(reference, "time_range")
    assert feedback["issues"][0]["repair_hint"]


def test_frame_tolerance_is_clamped_and_recorded(reference):
    reference.data["storyboard"][-1]["end_s"] += 1 / 30
    result = validate(reference)
    assert result["storyboard"][-1]["end_s"] == 30.03
    assert result["normalisations"][0]["path"] == "$.storyboard[6].end_s"


def test_out_of_order_is_not_silently_sorted(reference):
    cells = reference.data["storyboard"]
    cells[1], cells[2] = cells[2], cells[1]
    assert violation(reference, "time_order")["issues"][0]["cell_pairs"]


def test_overlaps_are_listed(reference):
    reference.data["storyboard"][3]["end_s"] = 15.8
    pairs = violation(reference, "overlap")["overlapping_cell_pairs"]
    assert pairs[0]["cell_ids"] == ["s4", "s5"]
    assert pairs[0]["overlap_s"] == pytest.approx(.3)


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
    reference.data["storyboard"][1]["line"]["ref_asr_ids"] = []
    assert violation(reference, "asr_assignment")["unassigned_asr_ids"] == [
        dict(id=reference.asr[0]["segment_id"], start_s=6.13, end_s=6.93, text="这谁啊")]


@pytest.mark.parametrize("mode", ["duplicate_cell", "duplicate_within_line", "unknown", "wrong_time"])
def test_bad_asr_assignments(reference, mode):
    cells = reference.data["storyboard"]
    sid = reference.asr[0]["segment_id"]
    if mode == "duplicate_cell":
        cells[2]["line"]["ref_asr_ids"].append(sid)
    elif mode == "duplicate_within_line":
        cells[1]["line"]["ref_asr_ids"].append(sid)
    elif mode == "unknown":
        cells[1]["line"]["ref_asr_ids"].append("invented")
    else:
        cells[1]["line"]["ref_asr_ids"].remove(sid)
        cells[6]["line"]["ref_asr_ids"].append(sid)
    detail = violation(reference, "asr_assignment")["issues"][0]
    assert detail[{"duplicate_cell": "duplicate_asr_ids", "duplicate_within_line": "duplicate_asr_ids",
                   "unknown": "unknown_asr_ids", "wrong_time": "mistimed_asr_ids"}[mode]]


def test_nearest_cell_within_one_second_is_allowed_but_farther_cell_is_not(reference):
    reference.asr.append(dict(id="gap", start_s=29.5, end_s=29.8, text="嗯"))
    reference.data["storyboard"][-1]["end_s"] = 28.8
    reference.data["storyboard"][-1]["line"]["ref_asr_ids"].append("gap")
    validate(reference)
    reference.asr[-1].update(start_s=29.81, end_s=30)
    assert violation(reference, "asr_assignment")["issues"][0]["mistimed_asr_ids"][0]["distance_s"] > 1


def test_within_one_second_must_also_be_nearest_cell(reference):
    reference.asr[0].update(source_start_s=8.4, source_end_s=8.8)
    assert violation(reference, "asr_assignment")["issues"][0]["mistimed_asr_ids"][0]["nearest_cell_ids"] == ["s3"]


def test_ignorable_asr_threshold_empty_text_and_nonempty_below_threshold(reference):
    reference.asr.extend([dict(id="noise", start_s=1, end_s=2, text="误识别", no_speech_prob=.6),
                          dict(id="empty", start_s=2, end_s=3, text="  ", no_speech_prob=0)])
    assert [row["id"] for row in validate(reference)["ignorable_asr"]] == ["noise", "empty"]
    reference.asr[-2]["no_speech_prob"] = .5999
    assert violation(reference, "asr_assignment")["unassigned_asr_ids"][0]["id"] == "noise"


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


def test_dialogue_turns_without_lines_and_with_unknown_speakers(reference):
    reference.asr = []
    for cell in reference.data["storyboard"]:
        cell["line"] = None
    assert validate(reference)["dialogue_turns"] == 0
    for cell, speaker in zip(reference.data["storyboard"], ["A", None, None, "B"]):
        cell["line"] = dict(speaker=speaker, intent="向对方说话", ref_asr_ids=[])
    assert validate(reference)["dialogue_turns"] == 3


@pytest.mark.parametrize("field,value", [("roles", "A"), ("core", "true"), ("basis", {}),
                                         ("story_role", "总结"), ("action", ""), ("line", {})])
def test_malformed_cells_produce_repairable_schema_feedback(reference, field, value):
    reference.data["storyboard"][0][field] = value
    assert violation(reference, "schema")["issues"][0]["path"]


def test_prompt_receives_reading_and_adapted_asr_without_mutation(reference):
    before = deepcopy(reference.asr)
    prompt = storyboard_prompt(reference.reading, reference.asr, reference.duration)
    context = json.loads(prompt.split("输入：\n", 1)[1])
    assert set(context["reference_reading"]) == set(READING_FIELDS)
    assert context["reference_reading"] == reference.reading
    assert context["asr"][0] == dict(id=before[0]["segment_id"], start_s=6.13, end_s=6.93, text="这谁啊")
    assert "画面可见字幕优先" in prompt and "每一次对话" in prompt and "不使用《功夫熊猫》" in prompt
    assert before == reference.asr
    reference.data["storyboard"][1]["line"]["ref_asr_ids"] = []
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


def test_trial_repairs_new_request_and_writes_artifacts(reference, memory_job, monkeypatch):
    bad = deepcopy(reference.data)
    bad["storyboard"][1]["line"]["ref_asr_ids"] = []
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
    urllib.error.HTTPError("https://example.invalid", 503, "test-secret", {}, io.BytesIO(b"test-secret"))])
def test_network_errors_stop_job_once_without_persisting_exception(reference, memory_job, monkeypatch, error):
    post = Mock(side_effect=error)
    monkeypatch.setattr(trial.qfs, "http_post", post)
    result = trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")
    assert result["attempts"] == post.call_count == 1 and result["status"] == "failed"
    assert "test-secret" not in str(memory_job.files)
    failure = json.loads(memory_job.files[memory_job.out / "failure.json"])
    assert failure["automatic_resend"] is False and failure["outcome_unknown"] is True


def test_max_repairs_and_invalid_json_are_bounded(reference, memory_job, monkeypatch):
    post = Mock(return_value={"choices": [{"message": {"content": "{malformed"}}]})
    monkeypatch.setattr(trial.qfs, "http_post", post)
    assert trial.run_job(memory_job.job, memory_job.out, 2, "test-secret")["attempts"] == 3
    assert post.call_count == 3
    assert post.call_args_list[1].args[2] != post.call_args_list[2].args[2]
    assert json.loads(memory_job.files[memory_job.out / "failure.json"])["code"] == "storyboard:invalid_json"


def test_zero_repairs_stops_on_first_content_violation(reference, memory_job, monkeypatch):
    bad = deepcopy(reference.data)
    bad["storyboard"][1]["line"]["ref_asr_ids"] = []
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

