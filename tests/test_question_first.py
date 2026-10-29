"""离线验证独立实验；绝不访问真实 API 或长片。"""
import json
from pathlib import Path
import shutil
import subprocess
import urllib.error
import os
import sys
import threading
import time
from copy import deepcopy
from unittest.mock import Mock
from types import SimpleNamespace

import numpy as np
import pytest

# Collect the same behavior cases on the pre-feature archive too: absent APIs
# fail individual tests, rather than ending the suite at module collection.
try:
    from omni_story.library import question_first as qfs
except ImportError:
    qfs = SimpleNamespace()
from omni_story.library.state import LibraryState, LibraryStopped, json_sha



@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(qfs, "http_post", Mock(side_effect=AssertionError("测试禁止真实HTTP")), raising=False)


def questions():
    return {"casting": {"A_boastful_villain": [{"name": "大龙", "aliases": ["太郎", "残豹", "Tai Lung"],
                                              "films": [1], "rough_part": "后段"}]},
            "beats": [{"id": name, "function": description,
                       "must_see": {"who_role": "A_boastful_villain", "action": "出招" if i == 0 else "摔倒",
                                    "object": "对手", "result": "动作结果可见", "shot_size": "中近景",
                                    "mouth_open_needed": i == 0},
                       "equivalents": ["被打飞", "被落物砸", "撞进杂物"],
                       "dub_can_carry": ["台词内容", "逞强的动机"], "roles": ["A_boastful_villain"]}
                      for i, (name, description) in enumerate(zip(["b1", "b2"], ["反派动作时喊话", "同一反派受挫"]))]}


def reference():
    return dict(visible_evidence=[dict(start_s=i, end_s=i + 1, observed_fact=b["function"],
                                     supports=b["function"]) for i, b in enumerate(questions()["beats"])])


@pytest.fixture
def state(tmp_path):
    return LibraryState(tmp_path / "task", {"synthetic": True}, max_requests=80)


@pytest.fixture
def call(state):
    def invoke(job, key):
        job = dict(job)
        if job["stage"] == "questions":
            job["beats"] = qfs.reference_beats(reference())
        count = state.usage()["requests"]
        event = dict(id=job["id"], stage=job["stage"], called=False, ok=False, latency_s=0)
        try:
            result = qfs.DirectTransport(state, key).call("qfs_" + job["id"], job)
            event["ok"] = True
        except Exception as error:
            result = None
            event["error"] = type(error).__name__
        event["called"] = state.usage()["requests"] > count
        return result, event
    return invoke


@pytest.fixture
def execute_jobs(call):
    def invoke(jobs, key, parallel, calls):
        results = {}
        for job in jobs:
            result, event = call(job, key)
            calls.append(event)
            if event["ok"]:
                results[event["id"]] = result
        return results
    return invoke


def envelope(data):
    return {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}


def tile(n, timestamp, film=1):
    return dict(n=n, film=film, timestamp=timestamp, shot_start=timestamp - .5,
                shot_end=timestamp + 1, keyframe=f"frame_{film}_{n}.jpg", position=n % 36)


def skim_tile(n, confidence="high", beats=None):
    return dict(n=n, beats=["b1", "b2"] if beats is None else beats, confidence=confidence,
                who=["大龙"], fact="角色张嘴挥拳")


def close_reply(**updates):
    data = dict(satisfies="must_see", equivalent=None, equivalent_id=None, in_s=1, out_s=3, who=["大龙"],
                mouth_moving=True, tts_needed=["招式名"], verdict="usable", reason="张嘴挥拳",
                confidence="high", style="3d", same_character_as_anchor=True, also_satisfies=[])
    data.update(updates)
    return data


def test_cut_detection_threshold_unsigned_and_empty():
    frames = np.array([0, 25, 51, 255, 255, 0], dtype=np.uint8)[:, None, None]
    assert qfs.detect_cuts(frames) == [0, 2, 3, 5]  # 等于25不算切镜，255→0不能溢出。
    assert qfs.detect_cuts(np.zeros((4, 18, 32), dtype=np.uint8)) == [0]
    assert qfs.detect_cuts(np.zeros((1, 18, 32), dtype=np.uint8)) == [0]
    assert qfs.detect_cuts(np.empty((0, 18, 32))) == []
    frames = np.zeros((2, 2, 2), dtype=np.uint8)
    frames[1, 0, 0] = 100
    assert qfs.detect_cuts(frames) == [0]  # 使用全帧平均差，不是最大像素差。


@pytest.mark.parametrize("number,title,duration,bounds", [
    (1, "功夫熊猫2008", 5529.5, (34, 4991)),
    (2, "功夫熊猫2", 5423.9, (21, 4869)), (3, "功夫熊猫3", 5694.7, (66, 4999)),
])
def test_duration_mapping_tolerance(number, title, duration, bounds):
    for offset in [-1, 0, 1]:
        info = qfs.match_duration(duration + offset)
        assert (info["film"], info["title"], info["bounds"]) == (number, title, list(bounds))
    with pytest.raises(ValueError, match="不匹配"):
        qfs.match_duration(duration + 1.01)


def test_duration_mismatch_is_loud():
    for duration in [4, 0, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="不匹配"):
            qfs.match_duration(duration)


def test_clip_clamps_to_content_and_twenty_seconds():
    bounds = (34, 4991)
    assert qfs.clip_window(dict(start_s=34, end_s=35, anchor=34.5), bounds) == (34, 39)
    assert qfs.clip_window(dict(start_s=4989, end_s=4991, anchor=4990), bounds) == (4985, 4991)
    assert qfs.clip_window(dict(start_s=100, end_s=160, anchor=140), bounds) == (130, 150)
    with pytest.raises(ValueError):
        qfs.clip_window(dict(start_s=0, end_s=1, anchor=.5), bounds)


def test_mapping_round_trip_uses_global_index_not_position(tmp_path):
    sheets = [dict(id="s0", path="s0.jpg", tiles=[tile(35, 100)]),
              dict(id="s1", path="s1.jpg", tiles=[tile(36, 140), tile(37, 160)])]
    qfs.write_json(tmp_path / "mapping.json", sheets)
    saved = qfs.read_json(tmp_path / "mapping.json")
    assert saved == sheets and saved[1]["tiles"][1]["position"] == 1
    result = qfs.select_regions(questions()["beats"], saved, {"s1": {"tiles": [skim_tile(37)]}})
    assert result["b1"][0]["anchor"] == 160
    assert result["b2"][0]["start_s"] == 159.5


def test_region_merging_same_film_eight_seconds():
    hits = [tile(0, 10), tile(1, 18), tile(2, 26.01), tile(3, 12, film=2)]
    sheet = dict(id="s", tiles=hits)
    skim = {"s": {"tiles": [skim_tile(n, c) for n, c in enumerate(["low", "high", "medium", "high"])]}}
    regions = qfs.select_regions(questions()["beats"], [sheet], skim)["b1"]
    assert len(regions) == 3
    merged = next(r for r in regions if r["film"] == 1 and r["start_s"] == 9.5)
    assert merged["end_s"] == 19 and merged["confidence"] == "high" and merged["anchor"] == 18
    assert any(r["film"] == 2 for r in regions)


@pytest.mark.parametrize("confidences,high,explore,total", [
    (["high"] * 8 + ["medium", "low"], 4, 2, 6),
    (["high"] * 8 + ["low"], 5, 1, 6),
    (["high"] * 8, 6, 0, 6),
    (["medium", "low"], 0, 2, 2),
])
def test_exploration_quota(confidences, high, explore, total):
    sheet = dict(id="s", tiles=[tile(n, 100 + n * 20) for n in range(len(confidences))])
    skim = {"s": {"tiles": [skim_tile(n, c) for n, c in enumerate(confidences)]}}
    regions = qfs.select_regions(questions()["beats"], [sheet], skim)["b2"]
    assert len(regions) == total
    assert sum(r["exploration"] for r in regions) == explore
    assert sum(r["confidence"] == "high" for r in regions) == high


def test_clip_relative_conversion():
    assert qfs.film_times(100, 120, 2.125, 7.5) == (102.125, 107.5)
    assert qfs.film_times(100, 120, 0, 20.02) == (100, 120)
    for pair in [(-1, 3), (2, 1), (2, 21), (1, float("nan")), (True, 3)]:
        with pytest.raises(ValueError):
            qfs.film_times(100, 120, *pair)


@pytest.mark.parametrize("media_name,part,mime", [(None, None, None), ("sheet.jpg", "image_url", "image/jpeg"),
                                                ("clip.mp4", "video_url", "video/mp4")])
def test_request_billing_thinking_and_media(call, monkeypatch, tmp_path, media_name, part, mime):
    transport = Mock(return_value=envelope(questions()))
    monkeypatch.setattr(qfs, "http_post", transport)
    job = dict(id="q", stage="questions", prompt="中文参考阅读")
    if media_name:
        media = tmp_path / media_name
        media.write_bytes(b"synthetic-media")
        job["media"] = media
    result, event = call(job, "synthetic-key")
    assert event["ok"] and event["called"] and event["latency_s"] >= 0
    transport.assert_called_once()
    url, headers, body = transport.call_args.args
    assert url == qfs.ENDPOINT and headers == {"Authorization": "Bearer synthetic-key",
        "Content-Type": "application/json", "X-Title": "4.5V MCP Local"}
    assert body["thinking"] == {"type": "disabled"} and body["model"] == "glm-5.3-flash"
    assert body["temperature"] == .2 and body["max_tokens"] == 8192
    content = body["messages"][0]["content"]
    assert content[-1] == dict(type="text", text="中文参考阅读")
    if part:
        assert content[0][part]["url"].startswith(f"data:{mime};base64,")


@pytest.mark.parametrize("bad", ["{malformed", "", "null", '{"beats":[]}', '{"bad": NaN}'])
def test_malformed_reply_recorded_once_without_retry(execute_jobs, monkeypatch, tmp_path, bad):
    transport = Mock(return_value={"choices": [{"message": {"content": bad}}]})
    monkeypatch.setattr(qfs, "http_post", transport)
    calls = []
    jobs = [dict(stage="questions", id=str(n), prompt=f"参考{n}") for n in range(3)]
    assert execute_jobs(jobs, "not-a-real-key", 2, calls) == {}
    assert transport.call_count == 3  # 每个请求恰好一次，失败仍继续其余请求。
    assert len(calls) == 3 and all(c["called"] and not c["ok"] for c in calls)


def test_key_not_in_failure_logs_or_reports(call, monkeypatch, tmp_path, capsys, caplog):
    key = "private-test-secret-never-log"
    transport = Mock(side_effect=urllib.error.HTTPError(qfs.ENDPOINT, 429, key, {"echo": key}, None))
    monkeypatch.setattr(qfs, "http_post", transport)
    result, event = call(dict(stage="questions", id="q", prompt="参考"), key)
    assert result is None and event["error"] == "UnknownQFSCall"
    transport.assert_called_once()
    for file in tmp_path.rglob("*.json"):
        assert key not in file.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err + caplog.text


def test_key_echo_redacted_in_success(call, monkeypatch, tmp_path):
    key = "private-test-secret-never-log"
    data = questions()
    data["beats"][0]["dub_can_carry"] = ["服务端回显 " + key]
    monkeypatch.setattr(qfs, "http_post", Mock(return_value=envelope(data)))
    result, event = call(dict(stage="questions", id="q", prompt="参考"), key)
    assert event["ok"] and "[REDACTED]" in result["beats"][0]["dub_can_carry"][0]
    assert all(key not in f.read_text(encoding="utf-8") for f in tmp_path.rglob("*.json"))


@pytest.mark.parametrize("suffix,limit", [(".jpg", 5), (".mp4", 8)])
def test_media_limit_checked_before_http(call, monkeypatch, tmp_path, suffix, limit):
    media = tmp_path / ("oversize" + suffix)
    with media.open("wb") as stream:
        stream.truncate(limit * 1024 ** 2 + 1)
    transport = Mock()
    monkeypatch.setattr(qfs, "http_post", transport)
    _, event = call(dict(stage="skim", id="s", prompt="问题", media=media), "secret")
    assert not event["called"] and not event["ok"] and event["error"] == "ValueError"
    transport.assert_not_called()
    assert qfs.NoRedirect().redirect_request(None, None, 307, "redirect", {}, "https://unused.test") is None


def test_close_verification_mouth_required_and_times(call, monkeypatch):
    data = close_reply()
    data["mouth_moving"] = False
    monkeypatch.setattr(qfs, "http_post", Mock(return_value=envelope(data)))
    job = dict(stage="close", id="c", prompt="核验", beat=questions()["beats"][0], window=(100, 110))
    result, event = call(job, "secret")
    assert event["ok"] and result["verdict"] == "partial"
    assert (result["film_in_s"], result["film_out_s"]) == (101, 103)
    assert "未证实" in result["reason"]


def test_partial_visual_evidence_is_retained(call, monkeypatch):
    data = dict(close_reply(), verdict="partial", satisfies="no", reason="只有摔倒过程，结果被遮挡")
    monkeypatch.setattr(qfs, "http_post", Mock(return_value=envelope(data)))
    job = dict(stage="close", id="c", prompt="核验", beat=questions()["beats"][1], window=(100, 110))
    result, event = call(job, "secret")
    assert event["ok"] and result["verdict"] == "partial" and result["film_out_s"] == 103


def test_invalid_skim_tile_is_dropped_and_invalid_close_fails(call, monkeypatch):
    transport = Mock(side_effect=[envelope({"tiles": [skim_tile(99)]}),
                                 envelope(dict(close_reply(), out_s=11))])
    monkeypatch.setattr(qfs, "http_post", transport)
    skim_job = dict(stage="skim", id="s", prompt="扫片", sheet={"tiles": [tile(0, 10)]}, beat_ids=["b1", "b2"])
    close_job = dict(stage="close", id="c", prompt="核验", beat=questions()["beats"][0], window=(100, 110))
    result, event = call(skim_job, "secret")
    assert event["called"] and event["ok"] and result["tiles"] == []
    assert result["dropped_tiles"] == [dict(index=0, n=99, reason="invalid_n")]
    result, event = call(close_job, "secret")
    assert result is None and event["called"] and not event["ok"]
    assert transport.call_count == 2


@pytest.mark.parametrize("bad,reason", [
    (dict(skim_tile(1), beats=["unknown"]), "invalid_beat_ids"),
    (skim_tile(0), "duplicate_n"),
    (skim_tile(99), "invalid_n"),
    (dict(skim_tile(1), confidence="certain"), "invalid_confidence"),
    (dict(skim_tile(1), who="大龙"), "invalid_who"),
    (dict(skim_tile(1), fact=None), "invalid_fact"),
    (dict(skim_tile(1), extra=float("nan")), "non_finite_value"),
    (None, "tile_not_object"),
])
def test_bad_skim_tile_keeps_other_tiles_and_records_reason(call, monkeypatch, tmp_path, bad, reason):
    good = [skim_tile(0), skim_tile(2), skim_tile(3, beats=[])]
    transport = Mock(return_value=envelope({"tiles": [good[0], bad, *good[1:]]}))
    monkeypatch.setattr(qfs, "http_post", transport)
    job = dict(stage="skim", id="sheet1", prompt="扫片", sheet={"tiles": [tile(i, i * 20) for i in range(4)]},
               beat_ids=["b1", "b2"])
    result, event = call(job, "secret")
    transport.assert_called_once()
    assert event["ok"] and result["tiles"] == good
    assert len(result["dropped_tiles"]) == 1 and result["dropped_tiles"][0]["reason"] == reason
    assert result["dropped_tiles"][0]["index"] == 1


def test_invalid_first_duplicate_does_not_hide_later_valid_tile():
    good = skim_tile(0)
    result = qfs.validate_reply({"tiles": [dict(good, beats=["invalid"]), good]},
        dict(stage="skim", sheet={"tiles": [tile(0, 1)]}, beat_ids=["b1", "b2"]))
    assert result["tiles"] == [good] and len(result["dropped_tiles"]) == 1


def test_empty_beats_needs_no_other_match_fields():
    no_match = dict(n=0, beats=[])
    result = qfs.validate_reply({"tiles": [no_match, skim_tile(1)]},
        dict(stage="skim", sheet={"tiles": [tile(0, 1), tile(1, 30)]}, beat_ids=["b1", "b2"]))
    assert result["tiles"] == [no_match, skim_tile(1)] and result["dropped_tiles"] == []
    regions = qfs.select_regions(questions()["beats"],
        [dict(id="s", tiles=[tile(0, 1), tile(1, 30)])], {"s": result})
    assert regions["b2"][0]["anchor"] == 30


@pytest.mark.parametrize("prefix,suffix", [
    ("```json\n", "\n```"), ("```json\n", "\n```\n补充说明"),
    ("这是结果：\n", "\n补充说明"), ("", '\n{"second_object":true}'),
])
def test_fenced_or_trailing_json_extraction_without_retry(call, monkeypatch, prefix, suffix):
    data = questions()
    transport = Mock(return_value={"choices": [{"message": {
        "content": prefix + json.dumps(data, ensure_ascii=False) + suffix}}]})
    monkeypatch.setattr(qfs, "http_post", transport)
    result, event = call(dict(stage="questions", id="q", prompt="参考"), "secret")
    assert event["ok"]
    transport.assert_called_once()


@pytest.mark.parametrize("fields,expected", [
    ({"equivalent_id": "E2"}, "E2"),
    ({"equivalent": " 被，打 飞！ "}, "E1"),
    ({"equivalent": "反派被打飞后重重摔在地上"}, "E1"),
    ({"equivalent": "角色因反击倒地，明显狼狈"}, None),
])
def test_equivalent_id_normalised_paraphrase_and_unmatched_keep_verdict(call, monkeypatch, fields, expected):
    transport = Mock(return_value=envelope(close_reply(satisfies="equivalent", **fields)))
    monkeypatch.setattr(qfs, "http_post", transport)
    beat = questions()["beats"][1]
    job = dict(stage="close", id="c", prompt=qfs.close_prompt(beat, questions(), {}, (100, 110)),
               beat=beat, window=(100, 110))
    result, event = call(job, "secret")
    assert event["ok"] and result["verdict"] == "usable"
    assert result.get("equivalent_id") == expected
    assert result.get("satisfies_unmatched", False) is (expected is None)
    if expected:
        assert result["equivalent"] == beat["equivalents"][int(expected[1:]) - 1]
    assert '"E1": "被打飞"' in job["prompt"] and "equivalent_id" in job["prompt"]
    transport.assert_called_once()


def test_invalid_equivalent_id_fails_without_retry(call, monkeypatch):
    transport = Mock(return_value=envelope(close_reply(satisfies="equivalent", equivalent_id="E99")))
    monkeypatch.setattr(qfs, "http_post", transport)
    result, event = call(dict(stage="close", id="c", prompt="核验", beat=questions()["beats"][1],
                                      window=(100, 110)), "secret")
    assert result is None and not event["ok"]
    transport.assert_called_once()


def anchor(still="anchor.jpg", **updates):
    return dict(dict(name="大龙", aliases=["太郎", "残豹", "Tai Lung"], still=str(still), film=1,
                     beat_id="b1", clip="anchor.mp4", confidence="high"), **updates)


@pytest.mark.parametrize("same,verdict", [(True, "usable"), (False, "partial")])
def test_anchor_identity_downgrade_and_two_media_parts(call, monkeypatch, tmp_path, same, verdict):
    still, video = tmp_path / "anchor.jpg", tmp_path / "clip.mp4"
    still.write_bytes(b"anchor-image")
    video.write_bytes(b"candidate-video")
    anchors = {"A_boastful_villain": anchor(still)}
    beat = questions()["beats"][1]
    transport = Mock(return_value=envelope(close_reply(same_character_as_anchor=same)))
    monkeypatch.setattr(qfs, "http_post", transport)
    job = dict(stage="close", id="c", beat=beat, anchors=anchors, window=(100, 110), media=video,
               prompt=qfs.close_prompt(beat, questions(), anchors, (100, 110)))
    result, event = call(job, "secret")
    assert event["ok"] and result["verdict"] == verdict
    if not same:
        assert "identity_mismatch" in result["reason"]
    content = transport.call_args.args[2]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image_url", "video_url", "text"]
    assert content[0]["image_url"]["url"] == "data:image/jpeg;base64,YW5jaG9yLWltYWdl"
    assert content[1]["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert '"anchor_name": "大龙"' in content[2]["text"]
    transport.assert_called_once()


def test_anchor_aliases_rank_before_unrelated_high_confidence():
    sheet = dict(id="s", tiles=[tile(n, n * 20 + 100) for n in range(9)])
    tiles = [dict(skim_tile(n), who=["沈王爷"]) for n in range(9)]
    tiles[-1].update(who=["残豹"], confidence="low")
    regions = qfs.select_regions(questions()["beats"], [sheet], {"s": {"tiles": tiles}},
                                 {"A_boastful_villain": anchor()})["b2"]
    assert len(regions) == 6 and regions[0]["anchor"] == 260 and regions[0]["anchor_match"]


def test_anchor_picks_highest_usable_confidence_and_never_switches():
    beat = questions()["beats"][0]
    candidates = [dict(close_reply(confidence=c), beat_id=beat["id"], still=f"{c}.jpg", film=1, clip=f"{c}.mp4")
                  for c in ["low", "high", "medium"]]
    candidates.append(dict(candidates[0], verdict="partial", confidence="high", who=["沈王爷"]))
    anchors = {}
    qfs.update_cast_anchors(beat, candidates, questions()["casting"], anchors)
    assert anchors["A_boastful_villain"]["still"] == "high.jpg"
    assert "残豹" in anchors["A_boastful_villain"]["aliases"]
    later = questions()["beats"][1]
    qfs.update_cast_anchors(later, [dict(candidates[1], beat_id=later["id"], who=["沈王爷"])],
                            questions()["casting"], anchors)
    assert anchors["A_boastful_villain"]["name"] == "大龙"


@pytest.mark.parametrize("source_verdict", ["no", "partial", "usable"])
def test_also_satisfies_routes_even_when_primary_beat_is_no(source_verdict):
    beats = questions()["beats"]
    data = close_reply(verdict=source_verdict, satisfies="no" if source_verdict == "no" else "must_see",
                       mouth_moving=False, also_satisfies=["b2", "b2"])
    job = dict(stage="close", beat=beats[0], window=(4550, 4560), beat_ids=["b1", "b2"])
    validated = qfs.validate_reply(data, job)
    result = dict(validated, beat_id=beats[0]["id"], film=1, title="合成影片", clip="defeat.mp4", still="defeat.jpg")
    routed = qfs.route_candidates(result, job, beats, {})
    assert len(routed) == 1
    candidate = routed[0]
    assert candidate["beat_id"] == "b2" and candidate["routed_from"] == "b1"
    assert candidate["verdict"] == "usable" and candidate["satisfies"] == "must_see"
    assert candidate["film_in_s"] == 4551 and candidate["film_out_s"] == 4553
    assert candidate["style"] == "3d" and candidate["still"] == "defeat.jpg"


def test_routing_keeps_target_mouth_and_identity_constraints():
    beats = questions()["beats"]
    anchors = {"A_boastful_villain": anchor()}
    result = dict(close_reply(same_character_as_anchor=False, mouth_moving=False, also_satisfies=[beats[0]["id"]]),
                  beat_id=beats[1]["id"], film=1, clip="candidate.mp4")
    routed = qfs.route_candidates(result, dict(beat=beats[1], anchors=anchors), beats, anchors)
    assert routed[0]["verdict"] == "partial"
    assert "identity_mismatch" in routed[0]["reason"] and "未证实" in routed[0]["reason"]


@pytest.mark.parametrize("style", ["3d", "mixed", "2d_stylized"])
def test_close_style_field_is_retained(style):
    result = qfs.validate_reply(close_reply(style=style),
        dict(stage="close", beat=questions()["beats"][1], window=(100, 110)))
    assert result["style"] == style


@pytest.mark.parametrize("style", [None, "live_action"])
def test_invalid_style_fails_without_retry(call, monkeypatch, style):
    transport = Mock(return_value=envelope(close_reply(style=style)))
    monkeypatch.setattr(qfs, "http_post", transport)
    result, event = call(dict(stage="close", id="c", prompt="核验", beat=questions()["beats"][1],
                                      window=(100, 110)), "secret")
    assert result is None and not event["ok"]
    transport.assert_called_once()


@pytest.mark.parametrize("stage", ["questions", "skim", "close"])
@pytest.mark.parametrize("failure", ["http", "timeout", "json", "schema"])
def test_no_retry_at_any_stage(execute_jobs, monkeypatch, stage, failure):
    if failure == "http":
        transport = Mock(side_effect=urllib.error.HTTPError(qfs.ENDPOINT, 503, "unavailable", {}, None))
    elif failure == "timeout":
        transport = Mock(side_effect=TimeoutError("timeout"))
    else:
        text = "{malformed" if failure == "json" else '{"unexpected":true}'
        transport = Mock(return_value={"choices": [{"message": {"content": text}}]})
    monkeypatch.setattr(qfs, "http_post", transport)
    job = dict(stage=stage, id="one", prompt="核验", beat=questions()["beats"][0], window=(100, 110),
               sheet={"tiles": [tile(0, 100)]}, beat_ids=["b1", "b2"])
    calls = []
    assert execute_jobs([job], "secret", 2, calls) == {}
    transport.assert_called_once()
    assert len(calls) == 1 and calls[0]["called"] and not calls[0]["ok"]


def test_questions_generic_function_and_stable_beat_contract():
    assert "通用条件" in qfs.QUESTIONS and "具体参考细节只放进 equivalents" in qfs.QUESTIONS
    assert "先前逞强的同一反派明显落败或受辱" in qfs.QUESTIONS
    assert "不得增加、删除、合并节拍" in qfs.QUESTIONS


def fake_film(tmp_path):
    media = tmp_path / "synthetic.mp4"
    media.write_bytes(b"mock-local-film")
    return dict(film=1, title="合成影片", path=str(media), duration=4, bounds=[0, 4])


def test_prepare_cache_and_sheet_mapping_without_ffmpeg(tmp_path, monkeypatch):
    info = fake_film(tmp_path)
    commands = []
    decode_threads = []
    # 每个采样点反色，37个镜头应成为36+1两张表；最后一格的 n 必须为36。
    samples = np.repeat((np.arange(37) % 2 * 255).astype(np.uint8)[:, None, None], 18, axis=1)
    samples = np.repeat(samples, 32, axis=2)
    info.update(duration=18.5, bounds=[0, 18.5])

    def ffmpeg(args, threads=1):
        commands.append(args)
        if args[-1] == "pipe:1" or "%06d" in str(args[-1]):
            decode_threads.append(threads)
        if args[-1] == "pipe:1":
            return samples.tobytes()
        if "%06d" in str(args[-1]):
            for n in range(37):
                Path(str(args[-1]) % n).write_bytes(b"jpeg")
        else:
            Path(args[-1]).write_bytes(b"jpeg")
        return b""

    monkeypatch.setattr(qfs, "media_command", ffmpeg)
    first = qfs.prepare_film(info, tmp_path)
    assert len(first["frames"]) == 37 and len(first["sheets"]) == 2
    assert first["sheets"][1]["tiles"][0]["n"] == 36
    assert first["sheets"][1]["tiles"][0]["position"] == 0
    assert all(info["bounds"][0] <= f["timestamp"] < info["bounds"][1] for f in first["frames"])
    assert any("tile=6x6:padding=4:color=white" in str(c) for c in commands)
    assert any("drawtext" in str(c) and "scale=320" in str(c) for c in commands)
    assert decode_threads == [4, 4]
    count = len(commands)
    assert qfs.prepare_film(info, tmp_path) == first
    assert len(commands) == count  # 完整缓存不再解码。
    Path(first["sheets"][0]["path"]).unlink()
    qfs.prepare_film(info, tmp_path)
    assert len(commands) > count


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="沙箱无ffmpeg/ffprobe，已覆盖纯函数和媒体命令模拟")
def test_tiny_synthetic_media(tmp_path):
    source = tmp_path / "testsrc.mp4"
    qfs.media_command(["-f", "lavfi", "-i", "testsrc=size=96x64:rate=8:duration=4", "-an",
                       "-c:v", "libx264", "-threads", 1, "-pix_fmt", "yuv420p", source])
    info = dict(film=1, title="testsrc", path=str(source), duration=4, bounds=[0, 4])
    cache = qfs.prepare_film(info, tmp_path)
    assert cache["decoded_frames"] == 8 and cache["frames"] and cache["sheets"]
    assert all(Path(s["path"]).stat().st_size < 5 * 1024 ** 2 for s in cache["sheets"])
    assert qfs.prepare_film(info, tmp_path) == cache
    clip = qfs.make_clip(tmp_path / "close.mp4", info, (0, 3))
    assert clip.stat().st_size < 8 * 1024 ** 2
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(clip)],
                           check=True, capture_output=True)
    streams = json.loads(probe.stdout)["streams"]
    assert len(streams) == 1 and streams[0]["width"] == 640 and streams[0]["r_frame_rate"] == "8/1"


@pytest.mark.parametrize('indices,n,expected,ambiguous', [
    ([36, 37, 38], 37, 37, False),
    ([36, 37, 38], 2, 37, False),
    ([0, 1, 2], 1, 1, True),
    ([0, 1, 2], 3, 2, False),
    ([1, 2, 3], 2, 2, False),
])
def test_tile_global_local_and_ambiguous_mapping(indices, n, expected, ambiguous):
    result = qfs.validate_skim(dict(tiles=[skim_tile(n)]),
        dict(sheet=dict(tiles=[dict(tile(n, 10 + n), position=i) for i, n in enumerate(indices)]),
             beat_ids=['b1', 'b2']))
    assert result['tiles'][0]['n'] == expected
    assert bool(result['ambiguous_n']) is ambiguous
    if ambiguous:
        assert result['ambiguous_n'][0] == dict(index=0, n=1, local_n=0, selected_n=1)
    assert 'n 用格子左上角印的编号' in qfs.SKIM


def test_beats_are_deterministic_from_received_evidence():
    reading = reference()
    fixed = qfs.reference_beats(reading)
    assert [b['id'] for b in fixed] == ['b1', 'b2']
    assert [b['reference_evidence'] for b in fixed] == reading['visible_evidence']
    assert [b['function'] for b in fixed] == [e['supports'] for e in reading['visible_evidence']]
    fixed[0]['reference_evidence']['observed_fact'] = 'caller mutation'
    assert qfs.reference_beats(reading)[0]['reference_evidence']['observed_fact'] != 'caller mutation'
    data = questions()
    data['beats'][0]['function'] = 'model tried to change the beat'
    result = qfs.validate_reply(data, dict(stage='questions', beats=qfs.reference_beats(reading)))
    assert result['beats'][0]['function'] == reading['visible_evidence'][0]['supports']


@pytest.mark.parametrize('change', ['add', 'drop', 'reorder', 'duplicate'])
def test_model_cannot_add_drop_or_reorder_beats(change):
    data = questions()
    if change == 'add':
        data['beats'].append(dict(data['beats'][0], id='b3'))
    elif change == 'drop':
        data['beats'].pop()
    elif change == 'reorder':
        data['beats'].reverse()
    else:
        data['beats'][1]['id'] = 'b1'
    with pytest.raises((ValueError, AssertionError)):
        qfs.validate_reply(data, dict(stage='questions', beats=qfs.reference_beats(reference())))


def source_fixture(tmp_path, duration=4):
    info = fake_film(tmp_path)
    source = dict(source_id='source_1', sha256=qfs.sha256_file(info['path']), path=info['path'],
                  duration_s=duration, audio_stream_index=None)
    return source


def cache_fixture(tmp_path, source, *, with_sha=True):
    folder = tmp_path / 'shots' / 'film_1'
    folder.mkdir(parents=True)
    image = folder / 'frame.jpg'; image.write_bytes(b'synthetic-jpeg')
    info = dict(film=1, title='合成影片', path=source['path'], duration=source['duration_s'], bounds=[0, source['duration_s']])
    shot = dict(tile(0, 1), keyframe='frame.jpg')
    saved = dict(info=info, frames=[shot], sheets=[dict(id='s', path='frame.jpg', tiles=[shot])])
    if with_sha:
        saved['source_sha256'] = source['sha256']
    qfs.write_json(folder / 'shots.json', saved)
    return folder.parent


def report_fixture(source):
    candidates = []
    for i, (style, verdict, same) in enumerate([
            ('2d_stylized', 'usable', True), ('3d', 'usable', False),
            ('3d', 'usable', True), ('mixed', 'usable', True), ('3d', 'partial', True)]):
        candidates.append(dict(close_reply(style=style, verdict=verdict, same_character_as_anchor=same),
            source_id=source['source_id'], film=1, clip=f'c{i}.mp4',
            film_in_s=10 + i * 20, film_out_s=15 + i * 20))
    return dict(beats=[dict(questions()['beats'][0], candidates=candidates)],
                cast_anchors={'A_boastful_villain': anchor()}, question_first_gaps=[])


def test_conversion_padding_clamp_caps_order_and_partial_fallback(tmp_path):
    source = source_fixture(tmp_path, 200)
    source['content_range'] = dict(content_start_s=11, content_end_s=95)
    report = report_fixture(source)
    result = qfs.to_search_result(report, dict(sources=[source]), max_windows=3, max_window_s=6)
    assert [w['start_s'] for w in result['windows']] == [48, 68, 28]
    assert all(w['end_s'] - w['start_s'] <= 6 for w in result['windows'])
    all_windows = qfs.to_search_result(report, dict(sources=[source]), max_windows=None)['windows']
    assert [w['start_s'] for w in all_windows] == [48, 68, 28, 11]
    assert all_windows[-1]['end_s'] == 17
    report['beats'][0]['candidates'] = [report['beats'][0]['candidates'][-1]]
    fallback = qfs.to_search_result(report, dict(sources=[source]), max_windows=8)['windows'][0]
    assert (fallback['start_s'], fallback['end_s']) == (88, 95)
    assert qfs.to_search_result(report, dict(sources=[source]), max_windows=0)['windows'] == []


def test_synthetic_search_validates_slots_deduplicates_and_records_visual_gaps(tmp_path):
    from omni_story.library import contracts
    source = source_fixture(tmp_path, 200)
    report = report_fixture(source)
    report['beats'].extend([dict(questions()['beats'][1], candidates=deepcopy(report['beats'][0]['candidates'])),
                           dict(questions()['beats'][0], id='b3', candidates=[])])
    report['question_first_gaps'] = qfs.material_gaps(report)
    assert [g['beat_id'] for g in report['question_first_gaps']] == ['b3']
    result = qfs.to_search_result(report, dict(sources=[source]), max_windows=8)
    assert len(result['windows']) == 4
    assert 'b2' in result['windows'][0]['question']
    assert [s['slot_id'] for s in result['slots']] == ['b1', 'b2', 'b3']
    assert result['slots'][2]['coverage']['gaps']
    assert contracts.validate_search(result, dict(sources=[source]), max_windows=8) is result


def test_anchor_context_is_in_actual_observation_prompt(tmp_path):
    from omni_story.library import prompts
    source = source_fixture(tmp_path, 200)
    identities = qfs.to_search_result(report_fixture(source), dict(sources=[source]), max_windows=1)['windows'][0]['qfs_identity']
    prompt = prompts.fine_prompt(dict(window_id='w'), qfs.observation_context(identities))
    for text in ['大龙', 'Tai Lung', 'anchor.jpg', 'same_character_as_anchor',
                 'This window was selected because it contains', 'name roles consistently', 'identity_evidence']:
        assert text in prompt


def observation_fixture(source, ident, window_id='w1', *, agrees=True, confirmed=True):
    value = dict(window_id=window_id, source_id=source['source_id'],
        roles=[dict(role_id='A', identity_confirmed=confirmed, identity_evidence='同一灰白豹，眉眼和衣着相符',
                    state='张嘴挥拳', qfs_anchor_entity_id=ident['entity_id'], same_character_as_anchor=agrees)],
        events=[], usable_ranges=[], uncertainties=[])
    return value


def test_canonical_entity_reuse_and_existing_cast_contract(tmp_path):
    from omni_story.library import pipeline, contracts
    source = source_fixture(tmp_path, 200)
    selected = qfs.to_search_result(report_fixture(source), dict(sources=[source]), max_windows=2)['windows']
    identities = [r['qfs_identity'][0] for r in selected]
    assert identities[0]['entity_id'] == identities[1]['entity_id']
    windows = []
    for i, ident in enumerate(identities):
        window = dict(window_id=f'w{i}', source_id=source['source_id'], source_sha256=source['sha256'],
                      source_start_s=0, source_end_s=4, status='watched', qfs_identity=[ident])
        raw = observation_fixture(source, ident, window['window_id'])
        window['observation'] = pipeline._fine_navigation_points(raw, window)
        contracts.validate_fine(window['observation'], window)
        assert 'entity_id' not in raw['roles'][0]  # raw received response is preserved
        windows.append(window)
    cast = [dict(cast_id='villain', entity_id=identities[0]['entity_id'],
                 bindings=[dict(window_id=w['window_id'], role_id='A') for w in windows])]
    assert contracts.validate_task_cast(cast, windows)['villain']['entity_id'] == identities[0]['entity_id']
    for agrees, confirmed in [(False, True), (None, True), (True, False)]:
        raw = observation_fixture(source, identities[0], agrees=agrees, confirmed=confirmed)
        qfs.bind_observed_entities(raw, [identities[0]])
        assert raw['roles'][0]['identity_confirmed'] is False
        assert 'entity_id' not in raw['roles'][0]
    with pytest.raises(ValueError, match='cast_entity_not_supported'):
        bad = deepcopy(windows)
        bad[0]['observation']['roles'][0]['identity_confirmed'] = False
        contracts.validate_task_cast(cast, bad)


def test_direct_received_resume_ledger_no_retry_or_key_leak(state, monkeypatch, capsys):
    key = 'do-not-log-this-key'
    response = envelope(questions())
    response['echo'] = key
    transport = Mock(return_value=response)
    monkeypatch.setattr(qfs, 'http_post', transport)
    job = dict(stage='questions', beats=qfs.reference_beats(reference()), prompt='reference ' + key)
    direct = qfs.DirectTransport(state, key)
    expected = direct.call('qfs_questions', job)
    assert direct.call('qfs_questions', job) == expected
    transport.assert_called_once()
    assert state.usage()['requests'] == 1
    record = state.data['calls'][0]
    folder = state.output / 'calls' / record['id']
    assert record['status'] == 'received' and (folder / 'request.json').is_file() and (folder / 'response.json').is_file()
    assert all(key not in p.read_text() for p in state.output.rglob('*.json'))
    assert key not in capsys.readouterr().out
    assert transport.call_args.args[2]['thinking'] == dict(type='disabled')
    assert transport.call_args.args[1]['X-Title'] == '4.5V MCP Local'
    assert qfs.NoRedirect().redirect_request(None, None, 307, '', {}, '') is None


@pytest.mark.parametrize('status', ['submitted', 'uncertain'])
def test_unknown_call_is_never_resent(state, monkeypatch, status):
    original = Mock(side_effect=KeyboardInterrupt())
    monkeypatch.setattr(qfs, 'http_post', original)
    job = dict(stage='questions', beats=qfs.reference_beats(reference()), prompt='reference')
    direct = qfs.DirectTransport(state, 'key')
    with pytest.raises(KeyboardInterrupt):
        direct.call('qfs_questions', job)
    record = state.data['calls'][0]
    if status == 'uncertain':
        state.fail_call(record, 'unknown')
    monkeypatch.setattr(qfs, 'http_post', Mock(side_effect=AssertionError('resent')))
    with pytest.raises(LibraryStopped, match='outcome_unknown_no_replay'):
        direct.call('qfs_questions', job)
    assert state.usage()['requests'] == 1


def test_runner_uses_shared_cache_and_resume_reuses_all_calls(state, tmp_path, monkeypatch):
    source = source_fixture(tmp_path)
    source['content_range'] = dict(content_start_s=0, content_end_s=4)
    root = cache_fixture(tmp_path, source)
    films = qfs.load_shot_cache(root, dict(sources=[source]))
    def ffmpeg(args, **kwargs):
        Path(args[-1]).write_bytes(b'synthetic-media')
    monkeypatch.setattr(qfs, 'media_command', ffmpeg)
    monkeypatch.setattr(qfs, 'valid_media', lambda path: Path(path).is_file() and
                        Path(path).read_bytes() == b'synthetic-media')
    def respond(url, headers, payload):
        content = payload['messages'][0]['content']
        if any(c['type'] == 'video_url' for c in content):
            return envelope(close_reply())
        if any(c['type'] == 'image_url' for c in content):
            return envelope(dict(tiles=[skim_tile(1)]))  # local 1 → global 0
        return envelope(questions())
    http = Mock(side_effect=respond)
    monkeypatch.setattr(qfs, 'http_post', http)
    runner = qfs.QuestionFirstRunner(state, films, transport=qfs.DirectTransport(state, 'key'))
    report = runner.run(reference())
    assert http.call_count == 4
    assert [c['name'] for c in state.data['calls']] == ['qfs_questions', 'qfs_skim_1_0', 'qfs_close_0', 'qfs_close_1']
    assert not report['question_first_gaps']
    assert runner.run(reference()) == report
    assert http.call_count == 4 and state.usage()['requests'] == 4
    last = http.call_args.args[2]['messages'][0]['content']
    assert [c['type'] for c in last] == ['image_url', 'video_url', 'text']
    assert qfs.read_json(runner.out / 'report.json') == report


@pytest.mark.parametrize('with_sha', [True, False])
def test_cache_match_and_mismatch_before_any_model(tmp_path, monkeypatch, with_sha):
    source = source_fixture(tmp_path)
    source['content_range'] = dict(content_start_s=0, content_end_s=4)
    root = cache_fixture(tmp_path, source, with_sha=with_sha)
    assert qfs.load_shot_cache(root, dict(sources=[source]))[0]['info']['source_id'] == source['source_id']
    path = root / 'film_1/shots.json'
    saved = qfs.read_json(path)
    if with_sha:
        saved['source_sha256'] = 'b' * 64
    else:
        saved['info']['duration'] = 10
    qfs.write_json(path, saved)
    with pytest.raises(ValueError, match='shot cache mismatch'):
        qfs.load_shot_cache(root, dict(sources=[source]))
    qfs.http_post.assert_not_called()


@pytest.mark.parametrize('flags', [
    ['--material-search', 'question_first'],
    ['--creative-audio', '--material-search', 'question_first'],
    ['--shot-cache', '/private/tmp'], ['--material-search', 'invalid'],
])
def test_cli_requires_creative_audio_and_shot_cache(flags, capsys):
    from omni_story.library import server_cli
    with pytest.raises(SystemExit) as error:
        server_cli.main(['start', '--reference', 'ref', '--library', 'lib', '--output', 'out', *flags])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert ('invalid choice' if 'invalid' in flags else 'requires') in message


def test_cli_synthetic_cache_preflight_and_conversion_no_network(tmp_path, monkeypatch, capsys):
    from omni_story.library import server_cli as cli, pipeline
    source = source_fixture(tmp_path)
    library = tmp_path / 'library'; library.mkdir()
    Path(source['path']).rename(library / 'tiny.mp4'); source['path'] = str(library / 'tiny.mp4')
    root = cache_fixture(tmp_path, source)
    visual = tmp_path / 'visual'
    qfs.write_json(visual / 'content_ranges.json', dict(schema_version='content_ranges_v1', sources={
        source['sha256']: dict(content_start_s=0, content_end_s=4, verified_by='synthetic fixture', excluded=[])}))
    home = tmp_path / 'home'; home.mkdir()
    qfs.write_json(home / 'server.json', dict(project_root=str(tmp_path), mcp_package_root='/fake', opencode_executable='/fake'))
    qfs.write_json(home / 'credentials.json', dict(Z_AI_API_KEY='synthetic-key'))
    (home / 'credentials.json').chmod(0o600)
    reference_file = tmp_path / 'ref.mp4'; reference_file.write_bytes(b'synthetic-reference')
    monkeypatch.setattr(pipeline, '_catalog', lambda *args: dict(sources=[deepcopy(source)]))
    seen = []
    monkeypatch.setattr(cli.server_jobs, 'start', lambda command, *args: seen.append(command) or dict(state='validated_only'))
    args = ['--home', str(home), 'start', '--creative-audio', '--material-search', 'question_first',
            '--shot-cache', str(root), '--visual-library', str(visual), '--reference', str(reference_file),
            '--library', str(library), '--output', str(tmp_path / 'task')]
    assert cli.main(args) == 0
    assert seen[0][seen[0].index('--material-search') + 1] == 'question_first'
    assert seen[0][seen[0].index('--shot-cache') + 1] == str(root.resolve())
    cfg = dict(creative_audio=True, material_search='question_first', shot_cache=str(root), visual_library_root=str(visual))
    catalog = dict(sources=[deepcopy(source)])
    qfs.configure(cfg, catalog, tmp_path / 'task')
    candidate = dict(close_reply(), source_id=source['source_id'], film=1, film_in_s=1, film_out_s=3, clip='candidate.mp4')
    report = dict(beats=[dict(questions()['beats'][0], candidates=[candidate])], cast_anchors={}, question_first_gaps=[])
    search = qfs.to_search_result(report, catalog, max_windows=1)
    assert (search['windows'][0]['start_s'], search['windows'][0]['end_s']) == (0, 4)
    qfs.http_post.assert_not_called()
    print('CLI preflight: validated; shot cache: matched; window: [0, 4]; network calls: 0')
    path = root / 'film_1/shots.json'
    data = qfs.read_json(path)
    qfs.write_json(path, {'frames': [], 'sheets': []})
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2 and len(seen) == 1
    assert 'info must be an object' in capsys.readouterr().err
    data['source_sha256'] = 'b' * 64
    qfs.write_json(path, data)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2 and len(seen) == 1


def test_pipeline_injects_search_zero_then_runs_normal_observations(tmp_path, monkeypatch):
    from omni_story.library import pipeline, prompts
    source = source_fixture(tmp_path)
    cache = cache_fixture(tmp_path, source)
    refpath = tmp_path / 'reference.mp4'; refpath.write_bytes(b'reference')
    ref = dict(source_id='ref', path=str(refpath), sha256=qfs.sha256_file(refpath), duration_s=4, audio_stream_index=None)
    catalog = dict(sources=[source])
    monkeypatch.setattr(pipeline, '_catalog', lambda path, output:
                        deepcopy(dict(sources=[ref]) if path == refpath else catalog))
    monkeypatch.setattr(pipeline, '_adaptive_coarse', lambda *a, **k: pytest.fail('QFS must bypass coarse/library search'))
    source['subtitle_bar'] = dict(source_sha256=source['sha256'], native_width=1920, native_height=1080,
        picture_bottom_px=950, measurement=dict(method='ffmpeg_gray_column_median_v2'),
        mask=dict(x=0, y=950 / 1080, w=1, h=130 / 1080, color='#000000'))
    # Catalog closure above retains this deterministic, source-bound film info.
    catalog['sources'][0]['subtitle_bar'] = source['subtitle_bar']
    prepared = []
    def prepare(row, start, end, cache, **options):
        prepared.append((start, end, options))
        return dict(path=row['path'], sha256=row['sha256'], source_id=row['source_id'], source_sha256=row['sha256'],
                    source_start_s=start, source_end_s=end, source_visual_policy='silent_subtitle_masked',
                    source_subtitle_masks=options.get('subtitle_masks', []))
    monkeypatch.setattr(pipeline, 'prepare_window', prepare)
    monkeypatch.setattr(pipeline, 'create_contact_sheet', lambda *a, **k: dict(path=str(refpath)))
    monkeypatch.setattr(pipeline, 'create_navigation_sheet', lambda *a, **k: dict(path=str(refpath)))
    candidates = [dict(close_reply(), source_id=source['source_id'], film=1, film_in_s=a, film_out_s=b, clip=f'c{i}.mp4')
                  for i, (a, b) in enumerate([(0.5, 1), (2.5, 3.5)])]
    report = dict(beats=[dict(questions()['beats'][0], candidates=candidates)],
                  cast_anchors={'A_boastful_villain': anchor(clip='c0.mp4')}, question_first_gaps=[])
    monkeypatch.setattr(qfs.QuestionFirstRunner, 'run', lambda self, reading: deepcopy(report))
    reading = json.loads(prompts.reference_prompt(ref['sha256'], 4).split('输出：\n', 1)[1])
    reading['strategy'] = dict(adaptation_mode='reinterpret', speech_mode='none', timing_driver='action')
    seen = []
    class ReachedPlan(Exception):
        pass
    class Model:
        def __init__(self, state):
            self.state = state

        def call(self, name, prompt, media, validator, **kwargs):
            seen.append(name)
            if name == 'reference':
                result = deepcopy(reading)
            elif name.startswith('fine_'):
                assert all(t in prompt for t in ['canonical_name', '大龙', 'anchor.jpg', 'same_character_as_anchor'])
                # The actual current window is in the fine validator's closure.
                window = dict(zip(validator.__code__.co_freevars, (c.cell_contents for c in validator.__closure__)))['window']
                ident = window['qfs_identity'][0]
                result = observation_fixture(source, ident, window['window_id'])
                result.update(events=[dict(local_start_s=0, local_end_s=1, observed_fact='张嘴挥拳', role_ids=['A'])],
                    usable_ranges=[dict(local_in_s=0, local_out_s=1, event_indices=[0], role_ids=['A'],
                                        camera='中景', continuity_notes='连续动作')])
            elif name in {'plan_0', 'plan_1'}:
                assert 'qfs_anchor_agreed_by_window_observation' in prompt and 'cast_contract' in prompt
                assert '"slot_id": "b1"' in prompt
                raise ReachedPlan()
            else:
                pytest.fail('unexpected model call: ' + name)
            validator(result)
            if name.startswith('fine_'):
                call, _ = self.state.begin_call(name, dict(media_sha256=qfs.sha256_file(media), name=name))
                self.state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(result))])))
            return result
    config = dict(creative_audio=True, material_search='question_first', shot_cache=str(cache),
                  content_ranges=dict(sources={source['sha256']: dict(content_start_s=0, content_end_s=4)}))
    output = tmp_path / 'run'
    with pytest.raises(ReachedPlan):
        pipeline.execute(refpath, tmp_path, output, asr=False, provider_config=config, model_factory=lambda state: Model(state))
    assert seen[0] == 'reference' and seen[-1] == 'plan_0'
    assert len([n for n in seen if n.startswith('fine_')]) == 2 and 'search_0' not in seen
    assert len(prepared) == 4
    windows = qfs.read_json(output / 'watched_windows.json')
    assert len(windows) == 2
    assert windows[0]['observation']['roles'][0]['entity_id'] == windows[1]['observation']['roles'][0]['entity_id']
    assert qfs.read_json(output / 'search_0.json')['slots'][0]['slot_id'] == 'b1'
    # Resume directly at candidate 1: use the pinned search_0 slots, with no
    # fresh selection or fine calls. Missing/tampered pins must stop safely.
    from omni_story.library import server_capacity_recovery
    saved_state = qfs.read_json(output / 'library_state.json')
    resumed = LibraryState(output, saved_state['input_lock'], max_requests=80)
    resumed.set_artifact('server_remaining_candidate_feedback', {'fixture': True})
    monkeypatch.setattr(server_capacity_recovery, 'remaining_candidate', lambda output: dict(
        watched_windows_path=str(output / 'watched_windows.json'), remaining_candidate=1, feedback={}))
    seen.clear()
    with pytest.raises(ReachedPlan):
        pipeline.execute(refpath, tmp_path, output, asr=False, provider_config=config, model_factory=lambda state: Model(state))
    assert seen == ['reference', 'plan_1']
    marker = resumed.data['artifacts']['question_first_search_0'][0]
    pinned = qfs.read_json(marker['path'])
    pinned['search']['slots'][0]['slot_id'] = 'tampered'
    qfs.write_json(marker['path'], pinned)
    seen.clear()
    with pytest.raises(LibraryStopped, match='recorded_search_changed'):
        pipeline.execute(refpath, tmp_path, output, asr=False, provider_config=config, model_factory=lambda state: Model(state))
    assert not any(n.startswith('plan_') for n in seen)


@pytest.mark.parametrize('visual_only', [True, False])
def test_canonical_entity_derivation_survives_received_fine_resume(state, tmp_path, visual_only):
    from omni_story.library import pipeline
    source = source_fixture(tmp_path, 200)
    ident = qfs.to_search_result(report_fixture(source), dict(sources=[source]), max_windows=1)['windows'][0]['qfs_identity'][0]
    window = dict(window_id='w1', source_id=source['source_id'], source_sha256=source['sha256'],
                  source_start_s=0, source_end_s=4, path=source['path'], sha256=source['sha256'],
                  qfs_identity=[ident], status='watched')
    if visual_only:
        window.update(source_visual_policy='silent_subtitle_masked', source_subtitle_masks=[])
    raw = observation_fixture(source, ident)
    request = dict(media_sha256=source['sha256'], arguments=dict(prompt='fixture'))
    call, folder = state.begin_call('fine_fixture', request)
    state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(raw))])))
    window['observation'] = qfs.bind_observed_entities(deepcopy(raw), [ident])
    qfs.write_json(folder / 'parsed.json', window['observation'])
    qfs.write_json(state.output / 'watched_windows.json', [window])
    for normalize in [False, True]:
        restored = pipeline._received_windows(state, visual_only=visual_only, normalize=normalize)
        assert restored[0]['observation']['roles'][0]['entity_id'] == ident['entity_id']


def test_synthetic_search_resume_keeps_initial_window_cap(state, tmp_path):
    source = source_fixture(tmp_path, 200)
    report = report_fixture(source)
    first = qfs.recorded_search_result(state, report, dict(sources=[source]), max_windows=3)
    assert len(first['windows']) == 3
    assert qfs.recorded_search_result(state, report, dict(sources=[source]), max_windows=0) == first
    report['beats'][0]['function'] = 'changed'
    with pytest.raises(LibraryStopped, match='recorded_search_changed'):
        qfs.recorded_search_result(state, report, dict(sources=[source]), max_windows=3)
    assert state.usage()['requests'] == 0


def test_direct_write_before_send_unknown_failure_does_not_retry(state, monkeypatch):
    key = 'secret-never-log'
    def send(*args):
        state._reload()
        record = state.data['calls'][0]
        assert record['status'] == 'submitted' and state.usage()['requests'] == 1
        assert (state.output / 'calls' / record['id'] / 'request.json').is_file()
        raise TimeoutError(key)
    http = Mock(side_effect=send)
    monkeypatch.setattr(qfs, 'http_post', http)
    direct = qfs.DirectTransport(state, key)
    job = dict(stage='questions', beats=qfs.reference_beats(reference()), prompt='reference')
    for _ in range(2):
        with pytest.raises(LibraryStopped, match='outcome_unknown_no_replay'):
            direct.call('qfs_questions', job)
    http.assert_called_once()
    assert state.data['calls'][0]['status'] == 'uncertain'
    assert all(key not in p.read_text() for p in state.output.rglob('*.json'))


def test_identity_disagreement_retains_pixels_without_cast_binding(tmp_path):
    from omni_story.library import pipeline, contracts
    source = source_fixture(tmp_path, 200)
    ident = qfs.to_search_result(report_fixture(source), dict(sources=[source]), max_windows=1)['windows'][0]['qfs_identity'][0]
    raw = observation_fixture(source, ident, agrees=False)
    raw.update(events=[dict(local_start_s=0, local_end_s=1, observed_fact='豹挥拳', role_ids=['A'])],
        usable_ranges=[dict(local_in_s=0, local_out_s=1, event_indices=[0], role_ids=['A'],
                            camera='中景', continuity_notes='角色身份不明')])
    window = dict(window_id='w1', source_id=source['source_id'], source_sha256=source['sha256'],
                  source_start_s=0, source_end_s=4, qfs_identity=[ident])
    value = pipeline._fine_navigation_points(raw, window)
    contracts.validate_fine(value, window)
    assert value['roles'][0]['identity_confirmed'] is False
    assert value['usable_ranges'][0]['role_ids'] == []
    assert raw['usable_ranges'][0]['role_ids'] == ['A']


def test_duration_fallback_needs_no_visual_library_and_pins_cache(tmp_path):
    source = source_fixture(tmp_path, 5529.5)
    root = cache_fixture(tmp_path, source, with_sha=False)
    config = dict(creative_audio=True, material_search='question_first', shot_cache=str(root))
    catalog = dict(sources=[source])
    films = qfs.configure(config, catalog, tmp_path / 'run')
    assert films[0]['info']['bounds'] == [34, 4991]
    assert source['content_range']['content_start_s'] == 34
    (root / 'film_1/frame.jpg').write_bytes(b'changed-image')
    with pytest.raises(LibraryStopped, match='recorded_shot_cache_changed'):
        qfs.configure(config, catalog, tmp_path / 'run')


def test_multi_role_beat_does_not_anchor_every_role_to_the_primary_actor():
    beat = questions()['beats'][0]
    beat['roles'].append('B_hero')
    casting = questions()['casting']
    casting['B_hero'] = [dict(name='阿宝', aliases=['Po'])]
    candidates = [dict(close_reply(who=['Tai Lung']), beat_id='b1', film=1, still='villain.jpg', clip='villain.mp4')]
    anchors = {}
    qfs.update_cast_anchors(beat, candidates, casting, anchors)
    assert list(anchors) == ['A_boastful_villain']
    assert anchors['A_boastful_villain']['name'] == '大龙'


def batch_jobs(stage, count=16):
    if stage == 'skim':
        return [(f'qfs_skim_1_{i}', dict(stage='skim', prompt=f'skim {i}',
            sheet=dict(tiles=[tile(0, 1)]), beat_ids=['b1', 'b2'])) for i in range(count)]
    return [(f'qfs_close_{i}', dict(stage='close', prompt=f'close {i}', beat=questions()['beats'][0],
        window=[0, 4], beat_ids=['b1', 'b2'])) for i in range(count)]


def test_actual_runner_parallelizes_skim_and_each_close_batch(state, tmp_path, monkeypatch):
    info = dict(fake_film(tmp_path), duration=400, bounds=[0, 400], source_id='source_1')
    sheets = [dict(id=f's{i}', path='sheet.jpg', tiles=[dict(tile(i, 10 + i * 20), position=0)])
              for i in range(16)]
    active, peak = dict(skim=0, close=0), dict(skim=0, close=0)
    lock = threading.Lock()
    class Transport:
        def call(self, name, job):
            stage = job['stage']
            if stage == 'questions':
                return qfs.validate_reply(questions(), job)
            with lock:
                active[stage] += 1
                peak[stage] = max(peak[stage], active[stage])
            time.sleep(.03)
            with lock:
                active[stage] -= 1
            if stage == 'skim':
                return dict(tiles=[skim_tile(job['sheet']['tiles'][0]['n'], beats=['b1'])])
            return qfs.validate_reply(close_reply(), job)
    monkeypatch.setattr(qfs, 'valid_media', lambda path: True)
    runner = qfs.QuestionFirstRunner(state, [dict(info=info, sheets=sheets)], transport=Transport())
    report = runner.run(reference())
    assert peak == dict(skim=8, close=6)
    assert active == dict(skim=0, close=0)
    assert len(report['skim']) == 16 and len(report['beats'][0]['candidates']) == 6


@pytest.mark.parametrize('stage', ['skim', 'close'])
def test_parallel_qfs_two_rounds_write_before_send_and_valid_ledger(state, monkeypatch, stage):
    delay, active, peak = .25, 0, 0
    lock = threading.Lock()
    def send(url, headers, payload):
        nonlocal active, peak
        # Inspect disk rather than the shared mutable state.data snapshot.
        ledger = qfs.read_json(state.path)
        prompt = payload['messages'][0]['content'][-1]['text']
        matches = [c for c in ledger['calls'] if qfs.read_json(
            state.output / 'calls' / c['id'] / 'request.json')['payload'] == payload]
        assert len(matches) == 1 and matches[0]['status'] == 'submitted'
        assert len([c for c in ledger['calls'] if c['status'] == 'submitted']) <= 8
        assert prompt.startswith(stage)
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(delay)
        with lock:
            active -= 1
        return envelope(dict(tiles=[]) if stage == 'skim' else close_reply())
    monkeypatch.setattr(qfs, 'http_post', send)
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'key'))
    start = time.monotonic()
    results = runner._run_jobs(batch_jobs(stage))
    elapsed = time.monotonic() - start
    assert len(results) == 16 and peak == 8 and active == 0
    assert 2 * delay <= elapsed < 4 * delay
    ledger = qfs.read_json(state.path)
    assert ledger['request_count'] == len(ledger['calls']) == 16
    assert len({c['id'] for c in ledger['calls']}) == 16
    for call in ledger['calls']:
        folder = state.output / 'calls' / call['id']
        assert call['status'] == 'received'
        assert json_sha(qfs.read_json(folder / 'request.json')) == call['request_sha256']
        assert json_sha(qfs.read_json(folder / 'response.json')) == call['response_sha256']
    monkeypatch.setattr(qfs, 'http_post', Mock(side_effect=AssertionError('resent')))
    assert runner._run_jobs(batch_jobs(stage)) == results
    assert qfs.read_json(state.path)['request_count'] == 16
    print(f'{stage}: 16 calls, max_parallel=8, peak={peak}, sleep={delay:.2f}s, elapsed={elapsed:.3f}s; '
          'ledger=16 received/16 unique; resume POST=0')


@pytest.mark.parametrize('stage', ['skim', 'close'])
def test_process_crash_during_parallel_send_never_resends(tmp_path, monkeypatch, stage):
    output = tmp_path / 'crash_task'
    script = '''
import os, sys, threading
from omni_story.library import question_first as qfs
from omni_story.library.state import LibraryState
state = LibraryState(sys.argv[1], {'synthetic_crash': True})
barrier = threading.Barrier(8)
def send(*args):
    barrier.wait(timeout=10)
    os._exit(91)
qfs.http_post = send
stage = sys.argv[2]
jobs = [(f'qfs_{stage}_{i}', dict(stage='skim', prompt=f'{stage} {i}',
    sheet={'tiles':[{'n':0,'position':0}]}, beat_ids=['b1'])) for i in range(16)]
qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'key'))._run_jobs(jobs)
'''
    crashed = subprocess.run([sys.executable, '-c', script, str(output), stage], capture_output=True, timeout=20)
    assert crashed.returncode == 91, crashed.stderr.decode()
    ledger = qfs.read_json(output / 'library_state.json')
    assert ledger['request_count'] == 8 and all(c['status'] == 'submitted' for c in ledger['calls'])
    lost = {c['name'] for c in ledger['calls']}
    resumed = LibraryState(output, {'synthetic_crash': True})
    direct = qfs.DirectTransport(resumed, 'key')
    direct.recover_submitted()
    sent = []
    def send(url, headers, payload):
        sent.append(payload['messages'][0]['content'][-1]['text'])
        return envelope(dict(tiles=[]))
    monkeypatch.setattr(qfs, 'http_post', send)
    jobs = [(f'qfs_{stage}_{i}', dict(stage='skim', prompt=f'{stage} {i}',
        sheet={'tiles':[{'n':0,'position':0}]}, beat_ids=['b1'])) for i in range(16)]
    runner = qfs.QuestionFirstRunner(resumed, [], transport=direct)
    assert len(runner._run_jobs(jobs)) == 8
    assert len(sent) == 8 and all(f'qfs_{text.replace(" ", "_")}' not in lost for text in sent)
    assert {c['name'] for c in runner.unknown_calls} == lost
    final = qfs.read_json(resumed.path)
    assert final['request_count'] == 16 and len({c['id'] for c in final['calls']}) == 16
    assert sum(c['status'] == 'received' for c in final['calls']) == 8
    assert sum(c['status'] == 'uncertain' for c in final['calls']) == 8
    monkeypatch.setattr(qfs, 'http_post', Mock(side_effect=AssertionError('resent')))
    runner._run_jobs(jobs)
    assert qfs.read_json(resumed.path)['request_count'] == 16
    print(f'{stage} hard crash: 8 submitted -> 8 unknown; resume sent only 8 new calls; second resume POST=0')


@pytest.mark.parametrize('max_parallel', [1, 3, 8])
def test_runner_uses_configured_parallelism_and_continues_unknowns(state, monkeypatch, max_parallel):
    active, peak = 0, 0
    lock = threading.Lock()
    def send(url, headers, payload):
        nonlocal active, peak
        prompt = payload['messages'][0]['content'][-1]['text']
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.02)
        with lock:
            active -= 1
        if prompt == 'skim 0':
            raise TimeoutError('unknown')
        return envelope(dict(tiles=[]))
    monkeypatch.setattr(qfs, 'http_post', send)
    direct = qfs.DirectTransport(state, 'key', max_parallel=max_parallel)
    runner = qfs.QuestionFirstRunner(state, [], transport=direct, max_parallel=max_parallel)
    assert len(runner._run_jobs(batch_jobs('skim'))) == 15
    assert peak == max_parallel
    assert runner.unknown_calls[0]['name'] == 'qfs_skim_1_0'
    ledger = qfs.read_json(state.path)
    assert ledger['request_count'] == 16
    assert sum(c['status'] == 'uncertain' for c in ledger['calls']) == 1


def qfs_request(**changes):
    return dict(dict(provider='qfs_direct_https_v1', stage='question_first', endpoint=qfs.ENDPOINT,
                     payload=dict(model='glm-5.3-flash'), index=0), **changes)


@pytest.mark.parametrize('pending_status', ['submitted', 'uncertain'])
@pytest.mark.parametrize('kind', ['qwen', 'tts', 'non_qfs', 'mcp', 'other_stage', 'other_endpoint'])
def test_parallel_exception_cannot_bypass_other_unknown_calls(state, kind, pending_status):
    name, request = 'qfs_old', qfs_request()
    if kind in {'qwen', 'tts'}:
        request['payload']['model'] = kind
    elif kind == 'non_qfs':
        name = 'plan_0'
    elif kind == 'mcp':
        request['provider'] = 'official_vision_mcp_in_codex'
    elif kind == 'other_stage':
        request['stage'] = 'plan'
    else:
        request['endpoint'] = 'https://example.invalid'
    call, _ = state.begin_call(name, request)
    if pending_status == 'uncertain':
        state.fail_call(call, 'lost')
    with pytest.raises(LibraryStopped, match='request_outcome_unknown_no_replay'):
        state.begin_call('qfs_new', qfs_request(index=1), qfs_max_parallel=8)
    with pytest.raises(ValueError, match='parallel_submission_requires'):
        state.begin_call(name, request, qfs_max_parallel=8)
    assert state.usage()['requests'] == 1


def test_non_qfs_submissions_block_on_in_flight_qfs(state):
    state.begin_call('qfs_old', qfs_request(), qfs_max_parallel=8)
    with pytest.raises(LibraryStopped, match='request_outcome_unknown_no_replay'):
        state.begin_call('plan_0', {'provider': 'paid', 'prompt': 'plan'})
    assert state.usage()['requests'] == 1


def test_finished_unknown_qfs_does_not_block_later_stages(state):
    # Real stop (4e8d2d9): one qfs_close ended unknown, then the first window observation
    # hit unknown_media_must_not_be_resubmitted and the whole task stopped.
    call, _ = state.begin_call('qfs_old', qfs_request(), qfs_max_parallel=8)
    state.fail_call(call, 'lost', uncertain=True)
    later, _ = state.begin_call('plan_0', {'provider': 'paid', 'prompt': 'plan'})
    assert later['name'] == 'plan_0'


def test_unknown_non_qfs_call_still_blocks(state):
    call, _ = state.begin_call('plan_0', {'provider': 'paid', 'prompt': 'plan'})
    state.fail_call(call, 'lost', uncertain=True)
    with pytest.raises(LibraryStopped):
        state.begin_call('plan_1', {'provider': 'paid', 'prompt': 'plan again'})


def test_parallel_ledger_keeps_cap_budget_digest_and_repair_checks(state):
    first, _ = state.begin_call('qfs_0', qfs_request(), qfs_max_parallel=2)
    second, _ = state.begin_call('qfs_1', qfs_request(index=1), qfs_max_parallel=2)
    with pytest.raises(LibraryStopped, match='max_parallel_submissions_reached'):
        state.begin_call('qfs_2', qfs_request(index=2), qfs_max_parallel=2)
    state.complete_call(first, {})
    with pytest.raises(LibraryStopped, match='request_already_recorded'):
        state.begin_call('qfs_duplicate', qfs_request(), qfs_max_parallel=2)
    with pytest.raises(ValueError, match='parallel_submission_requires'):
        state.begin_call('qfs_0_repair', qfs_request(index=3), repair_of=first, qfs_max_parallel=2)
    folder = state.output / 'calls' / second['id']
    qfs.write_json(folder / 'request.json', {'modified': True})
    with pytest.raises(LibraryStopped, match='unknown_original_request_modified'):
        state.begin_call('qfs_2', qfs_request(index=2), qfs_max_parallel=2)


def test_concurrent_reservations_cannot_overspend_quota(tmp_path, monkeypatch):
    state = LibraryState(tmp_path / 'limited', {'synthetic_quota': True}, max_requests=5)
    http = Mock(side_effect=lambda *args: (time.sleep(.02), envelope(dict(tiles=[])))[1])
    monkeypatch.setattr(qfs, 'http_post', http)
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'key'))
    with pytest.raises(LibraryStopped, match='budget_exhausted'):
        runner._run_jobs(batch_jobs('skim'))
    ledger = qfs.read_json(state.path)
    assert ledger['request_count'] == len(ledger['calls']) == http.call_count == 5
    assert all(c['status'] == 'received' for c in ledger['calls'])


def test_position_fallback_uses_grid_position_not_array_order():
    tiles = [dict(tile(38, 3), position=2), dict(tile(36, 1), position=0), dict(tile(37, 2), position=1)]
    result = qfs.validate_skim(dict(tiles=[skim_tile(1), skim_tile(2)]),
        dict(sheet=dict(tiles=tiles), beat_ids=['b1', 'b2']))
    assert [t['n'] for t in result['tiles']] == [36, 37]


@pytest.mark.parametrize('positions', [[None, 1], [0, 0], [-1, 1], [0, 2], [True, 0], ['0', 1]])
def test_invalid_grid_positions_rejected(positions):
    tiles = [dict(tile(36 + i, 1 + i), position=p) for i, p in enumerate(positions)]
    with pytest.raises(ValueError, match='tile.position'):
        qfs.validate_skim(dict(tiles=[]), dict(sheet=dict(tiles=tiles), beat_ids=['b1']))


@pytest.mark.parametrize('field,value', [
    ('root', []), ('info', []), ('film', None), ('title', None), ('duration', 'bad'),
    ('frames', None), ('sheets', {}), ('frame', None), ('keyframe', 12),
    ('timestamp', None), ('sheet', []), ('path', None), ('tiles', None), ('position', None),
    ('n', []), ('shot_end', None),
])
def test_malformed_shot_cache_has_explicit_value_error(tmp_path, field, value):
    source = source_fixture(tmp_path)
    root = cache_fixture(tmp_path, source)
    path = root / 'film_1/shots.json'
    saved = qfs.read_json(path)
    if field == 'root':
        saved = value
    elif field in {'info', 'frames', 'sheets'}:
        saved[field] = value
    elif field in {'film', 'title', 'duration'}:
        saved['info'][field] = value
    elif field == 'frame':
        saved['frames'][0] = value
    elif field == 'sheet':
        saved['sheets'][0] = value
    elif field in {'path', 'tiles'}:
        saved['sheets'][0][field] = value
    elif field == 'position':
        saved['sheets'][0]['tiles'][0].pop('position')
    else:
        saved['frames'][0][field] = value
    qfs.write_json(path, saved)
    with pytest.raises(ValueError, match='question_first:'):
        qfs.load_shot_cache(root, dict(sources=[source]))


@pytest.mark.parametrize('command', ['start', '_run'])
def test_cli_rejects_reuse_plan_with_qfs_before_any_io(command, capsys):
    from omni_story.library import server_cli
    with pytest.raises(SystemExit) as error:
        server_cli.main([command, '--reference', 'ref', '--library', 'lib', '--output', 'out',
            '--creative-audio', '--material-search', 'question_first', '--shot-cache', '/private/tmp', '--reuse-plan'])
    assert error.value.code == 2
    assert '--reuse-plan cannot be combined with --material-search question_first' in capsys.readouterr().err


@pytest.mark.parametrize('suffix', ['.mp4', '.jpg'])
def test_media_atomic_publish_replaces_truncated_and_reuses_only_valid(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('close' + suffix)
    path.write_bytes(b'truncated')
    monkeypatch.setattr(qfs, 'valid_media', lambda p: Path(p).is_file() and Path(p).read_bytes() == b'decodable')
    generated = []
    def generate(temporary):
        generated.append(temporary)
        assert temporary.parent == path.parent and temporary.suffix == suffix and temporary != path
        temporary.write_bytes(b'partial')
        assert path.read_bytes() == b'truncated'
        temporary.write_bytes(b'decodable')
    assert qfs.atomic_media(path, generate) == path
    assert path.read_bytes() == b'decodable' and len(generated) == 1
    qfs.atomic_media(path, generate)
    assert len(generated) == 1
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('failure', [KeyboardInterrupt, RuntimeError])
def test_media_crash_cannot_publish_partial_file(tmp_path, monkeypatch, failure):
    path = tmp_path / 'close.mp4'
    monkeypatch.setattr(qfs, 'valid_media', lambda path: False)
    def crash(temporary):
        temporary.write_bytes(b'partial')
        assert not path.exists()
        raise failure()
    with pytest.raises(failure):
        qfs.atomic_media(path, crash)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('suffix', ['.mp4', '.jpg'])
@pytest.mark.parametrize('duration', ['0', '-1', 'nan', '1.0'])
def test_reuse_requires_nonempty_ffprobe_video_with_positive_duration(tmp_path, monkeypatch, suffix, duration):
    path = tmp_path / ('close' + suffix)
    path.write_bytes(b'not-empty')
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(dict(
        format=dict(duration=duration), streams=[dict(codec_type='video')])).encode()))
    monkeypatch.setattr(qfs.subprocess, 'run', run)
    assert qfs.valid_media(path) is (duration == '1.0')
    assert run.call_args.args[0][0] == 'ffprobe'
    if suffix == '.jpg':
        assert 'image2' in run.call_args.args[0]
    path.write_bytes(b'')
    run.reset_mock()
    assert qfs.valid_media(path) is False
    run.assert_not_called()


def test_invalid_outer_json_never_returns_inner_tile_as_whole_reply():
    from omni_story.library import question_first as qf
    # Real server reply (qfs_skim_2_16): one extra ']' breaks the outer object.
    text = ('{"tiles":[{"n":587,"beats":["b1"],"confidence":"medium","who":["狼帮众"],"fact":"逼近"},'
            '{"n":589,"beats":["b3"],"confidence":"medium","who":["狼帮众"],"fact":"被击飞"]}]}')
    with pytest.raises(ValueError):
        qf.parse_json_object(text)
    assert [t['n'] for t in qf.salvage_tiles(text)] == [587]


def test_fenced_json_is_parsed_as_the_outer_object():
    from omni_story.library import question_first as qf
    assert qf.parse_json_object('```json\n{"tiles":[{"n":1}]}\n```') == {'tiles': [{'n': 1}]}


def test_unusable_reply_loses_only_that_job(tmp_path):
    from omni_story.library import question_first as qf
    class Transport:
        def call(self, name, job):
            if name == 'bad':
                raise KeyError('tiles')
            return {'ok': name}
    runner = qf.QuestionFirstRunner.__new__(qf.QuestionFirstRunner)
    runner.transport, runner.max_parallel, runner.out, runner.unknown_calls = Transport(), 2, tmp_path, []
    result = runner._run_jobs([('good', {}), ('bad', {}), ('good2', {})])
    assert [r[0] for r in result] == ['good', 'good2']
    assert runner.unknown_calls[0]['name'] == 'bad' and runner.unknown_calls[0]['status'] == 'received_unusable'


def test_uncast_supporting_roles_are_allowed_without_anchor():
    from omni_story.library import question_first as qf
    root = Path(__file__).parent / 'fixtures/question_first'
    # Real server reply (qfs_questions, 1013f88): roles B_trembling_victim/C_intimidating_opponent/B_blocking_hero are uncast.
    reference = json.loads((root / 'heihu_reference_reading_1013f88.json').read_text(encoding='utf-8'))
    reference = reference.get('reference', reference)
    data = json.loads((root / 'heihu_questions_1013f88.json').read_text(encoding='utf-8'))
    qf.validate_reply(data, dict(stage='questions', beats=qf.reference_beats(reference)))
    assert data['casting']['B_trembling_victim'] == [] and data['casting']['A_boastful_villain']
