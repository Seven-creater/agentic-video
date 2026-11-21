import numpy as np
import pytest

from omni_story.library.dialogue_mouth_bundle import voice_frames
from omni_story.library.dialogue_mouth_edit import (
    checked_span,
    frame_at,
    score,
    splice,
    validate_cuts,
    weighted_cuts,
)


def case():
    return {
        "total": 12,
        "clips": {"A": {"frames": 12}, "B": {"frames": 8}},
        "lines": [
            {
                "id": "q",
                "actor": "x",
                "text": "问",
                "at": 2,
                "end": 7,
                "voice_frames": [2, 3, 5, 6],
            },
            {
                "id": "voiceover",
                "actor": "y",
                "text": "旁白",
                "at": 8,
                "end": 11,
                "voice_frames": [8, 9, 10],
                "on_screen": False,
            },
        ],
    }


def test_neither_freeze_nor_reverse_interval_can_cover_dialogue():
    with pytest.raises(ValueError):
        validate_cuts([{"at": 0, "clip": "B", "in": 0}], case())
    assert not checked_span(4, 4, 9)
    with pytest.raises(ValueError):
        validate_cuts([{"at": 0, "clip": "A", "in": 0, "crop": [1, 0, 0, 1]}], case())


def test_only_a_static_loopable_clip_may_replay_to_cover_a_long_line():
    from omni_story.library.dialogue_mouth_edit import expand_loops, require_coverage

    project = case()
    project["clips"]["B"]["loopable"] = True
    cuts = expand_loops([{"at": 0, "clip": "B", "in": 2}], project)
    assert [(c["at"], c["in"]) for c in cuts] == [(0, 2), (6, 2)]
    validate_cuts(cuts, project)
    assert expand_loops([{"at": 0, "clip": "A", "in": 2}], project) == [{"at": 0, "clip": "A", "in": 2}]
    with pytest.raises(ValueError, match="画面不够"):
        require_coverage(dict(case(), total=21))
    require_coverage(dict(project, total=21))


def test_local_repair_preserves_source_on_both_sides():
    cuts = [{"at": 0, "clip": "A", "in": 0}]
    alt = splice(cuts, {"q": [{"at": 2, "clip": "B", "in": 1}]}, case())
    assert [frame_at(alt, n)[:2] for n in (1, 2, 6, 7, 11)] == [
        ("A", 1),
        ("B", 1),
        ("B", 5),
        ("A", 7),
        ("A", 11),
    ]


def test_uncertain_closed_mouth_and_missing_source_stay_in_denominator():
    cuts = [{"at": 0, "clip": "A", "in": 0}]
    obs = [
        {
            "id": "q",
            "spans": [
                {
                    "start": 2,
                    "end": 5,
                    "status": "talking",
                    "actor": "x",
                    "others": True,
                },
                {
                    "start": 5,
                    "end": 7,
                    "status": "uncertain",
                    "actor": "x",
                    "others": False,
                },
            ],
        }
    ]
    slots = {
        "A": {
            "slots": [
                {
                    "actor": "x",
                    "start": 3,
                    "end": 7,
                    "witness": [3, 6],
                    "center": [[0.5, 0.5], [0.5, 0.5]],
                }
            ]
        }
    }
    result = score(case(), cuts, obs, slots)
    assert (
        result["passed"],
        result["total"],
        result["voiced_frames"],
        result["confirmed_frames"],
        result["other_visible_frames"],
    ) == (0, 1, 4, 1, 2)
    assert result["lines"][0]["confirmed_at"] == [3]
    with pytest.raises(ValueError):
        score(case(), cuts, [], slots)


def test_for_weighted_control_budget_must_fit_the_source():
    v = dict(case(), blocks=[{"at": 0, "end": 12, "clips": ["A", "B"]}])
    cuts = weighted_cuts(
        [
            {
                "cuts": [
                    {"clip": "A", "in": 0, "weight": 1},
                    {"clip": "B", "in": 0, "weight": 1},
                ]
            }
        ],
        v,
    )
    assert [r["at"] for r in cuts] == [0, 6]
    with pytest.raises(ValueError):
        weighted_cuts([{"cuts": [{"clip": "B", "in": 1, "weight": 2}]}], v)


def test_voice_measurement_excludes_silence_and_keeps_clock_offset():
    samples = np.zeros(1600 * 4, dtype=np.int16)
    samples[3200:4800] = 1200
    assert voice_frames(samples, 1, 4) == [2]


def test_a_confirmed_source_mouth_can_be_lost_in_the_output():
    from omni_story.library.dialogue_mouth_edit import mouth_survives_crop

    c = case()
    c["fps"] = 30
    slot = {"witness": [1, 8], "center": [[0.2, 0.6], [0.2, 0.6]]}
    assert not mouth_survives_crop(
        c, {"clip": "A", "crop": [0.3, 0.2, 0.8, 0.7]}, slot, 4
    )
    c["clips"]["A"]["masks"] = [
        {"start_s": 0, "end_s": 0.3, "x": 0.1, "y": 0.5, "w": 0.2, "h": 0.2}
    ]
    assert not mouth_survives_crop(c, {"clip": "A"}, slot, 4)
    assert mouth_survives_crop(c, {"clip": "A"}, slot, 10)


def test_every_frame_of_a_cut_and_every_repair_obeys_the_reference_scene():
    from omni_story.library.dialogue_mouth_edit import validate_scenes

    project = case()
    project["blocks"] = [
        {"at": 0, "end": 7, "clips": ["A"]},
        {"at": 7, "end": 12, "clips": ["B"]},
    ]
    validate_scenes([{"at": 0, "clip": "A"}, {"at": 7, "clip": "B"}], project)
    for boundary in [6, 8]:
        with pytest.raises(ValueError):
            validate_scenes(
                [{"at": 0, "clip": "A"}, {"at": boundary, "clip": "B"}], project
            )
    with pytest.raises(ValueError):
        validate_scenes(
            [{"at": 0, "clip": "A"}], dict(project, blocks=project["blocks"][:1])
        )


def test_stationary_open_mouth_and_a_single_change_cannot_cover_dialogue():
    from omni_story.library.dialogue_mouth_tracks import cycles

    def motion(areas):
        return cycles([(n, (0, 0, 21, 12, a)) for n, a in enumerate(areas)], 30)

    assert not motion([60] * 45)
    assert not motion([0] * 20 + [50] * 20)
    assert not motion([50] * 5 + [0] * 40 + [50] * 5)
    assert motion([0] * 3 + [50] * 5 + [0] * 3) == [[0, 11]]


def test_exported_ink_must_match_the_same_mouth_not_a_subtitle_or_frozen_frame():
    from omni_story.library.dialogue_mouth_tracks import matching_pose
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 360), "white")
    pen = ImageDraw.Draw(image)
    pen.ellipse((302, 168, 338, 192), outline="black", width=4)
    original = np.asarray(image)
    full = [0, 0, 1, 1]
    area = matching_pose(original, original, (0.5, 0.5), 37 / 640, full)
    assert area is not None and area > 8
    blank = np.full_like(original, 180)
    assert matching_pose(original, blank, (0.5, 0.5), 37 / 640, full) is None
    assert (
        matching_pose(original, original, (0.5, 0.5), 37 / 640, [0, 0, 0.51, 0.51])
        is None
    )


def test_verified_track_cannot_be_attached_to_modified_media(tmp_path):
    import json
    from omni_story.library.dialogue_mouth_edit import load_verified_sources

    path = tmp_path / "checked.json"
    path.write_text(
        json.dumps(
            {"A": {"method": "contour_cycle", "source_sha256": "old", "slots": []}}
        )
    )
    with pytest.raises(ValueError):
        load_verified_sources({"clips": {"A": {"sha256": "new"}}}, path)


def test_an_opening_elsewhere_in_the_source_does_not_certify_a_static_cut(
    monkeypatch, tmp_path
):
    from PIL import Image, ImageDraw
    from omni_story.library import dialogue_mouth_tracks as tracks

    frames = []
    for opened in [False] * 3 + [True] * 3 + [False] * 3:
        image = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(image)
        if opened:
            draw.ellipse((302, 168, 338, 192), outline="black", width=4)
        else:
            draw.line((302, 180, 338, 180), fill="black", width=4)
        frames.append(np.array(image))
    monkeypatch.setattr(tracks, "sampled_frames", lambda path, step: frames)
    project = {
        "total": 9,
        "fps": 30,
        "clips": {"A": {"path": "A"}},
        "lines": [{"id": "line", "actor": "A", "at": 0, "end": 9}],
    }
    observations = {
        "A": {
            "slots": [
                {
                    "actor": "A",
                    "start": 0,
                    "end": 9,
                    "center": [(0.5, 0.5)] * 9,
                    "widths": [37 / 640] * 9,
                }
            ]
        }
    }
    cuts = [{"at": 0, "clip": "A", "in": 0}]
    assert tracks.rendered_cycles(project, tmp_path, "out", cuts, observations)[
        "line"
    ] == list(range(9))
    cuts += [{"at": 3, "clip": "A", "in": 3}, {"at": 6, "clip": "A", "in": 6}]
    # Three cuts with different framing break the observed motion; each retained shot is still.
    for i, cut in enumerate(cuts[1:]):
        cut["crop"] = [0.01 * (i + 1), 0.01 * (i + 1), 1, 1]
    assert not tracks.rendered_cycles(project, tmp_path, "out", cuts, observations)[
        "line"
    ]


def test_local_recut_is_an_independent_vision_request(monkeypatch, tmp_path):
    from omni_story.library import dialogue_mouth_edit as edit

    project = dict(case(), actors={"x": "person"}, media="test.mp4", context="dialogue")
    slot = dict(actor="x", start=0, end=5, evidence="visible changes")

    def choose(case, out, stage, prompt, media, validate):
        # The transport reserves names ending in _repair for recovery of a parent response.
        assert not stage.endswith("_repair")
        return validate({"patches": [], "reason": "keep this cut"})

    monkeypatch.setattr(edit, "ask", choose)
    cuts = [{"clip": "A", "in": 0, "at": 0}]
    obs = {"A": {"slots": [slot]}, "B": {"slots": []}}
    score0 = {"lines": [{"id": "q", "actor": "x", "voiced": 3, "approved": 0}]}
    assert (
        edit.repair_dialogue(project, tmp_path, tmp_path, cuts, [], obs, score0)[
            "selected"
        ]
        == "draft"
    )


def test_editorial_recutter_checks_aliases_blocked_and_late_visible_actor():
    from omni_story.library.dialogue_scene_review import timeline_findings

    project = {
        "fps": 9,
        "clips": {
            "A": {"source_sha256": "same", "source_start_s": 0},
            "B": {"source_sha256": "same", "source_start_s": 0},
        },
        "lines": [
            {"id": "a", "actor": "x", "at": 0, "end": 4, "voice_frames": [0, 1, 2, 3]},
            {"id": "b", "actor": "y", "at": 4, "end": 8, "voice_frames": [4, 5, 6, 7]},
        ],
    }
    brief = {
        "A": {"faces": [], "blocked": []},
        "B": {
            "faces": [[0, ["x"]], [2, ["x"]]],
            "blocked": [{"actor": "y", "start": 2, "end": 4}],
        },
    }
    cuts = [{"at": 0, "clip": "A", "in": 0}, {"at": 4, "clip": "B", "in": 0}]
    issues = timeline_findings(project, cuts, [{"at": 0, "end": 8}], brief)
    assert [(i["kind"], i["first"], i["end"]) for i in issues] == [
        ("onset_face_unconfirmed", 4, 5),
        ("replayed_picture", 4, 8),
        ("blocked_speaker", 6, 8),
    ]


def test_new_repeated_action_cannot_be_offset_by_better_mouth_score():
    from omni_story.library.dialogue_scene_review import compare_reviews

    old = {
        "timeline": [],
        "story": {"issues": [], "unknown": []},
        "score": {"confirmed_frames": 83, "lines": []},
    }
    current = dict(
        old,
        timeline=[{"kind": "replayed_picture", "line": "next", "first": 10, "end": 26}],
        score={"confirmed_frames": 182, "lines": []},
    )
    assert "重复" in compare_reviews(old, current, [], [])


def test_review_does_not_blame_recut_for_a_report_on_unchanged_frames():
    from omni_story.library.dialogue_scene_review import introduced_story_issues

    original = [{"at": 0, "clip": "A", "in": 0}]
    candidate = original + [{"at": 10, "clip": "B", "in": 0}]
    before = {"issues": []}
    after = {
        "issues": [
            {"line": "x", "kind": "payoff_missing", "f": 8},
            {"line": "y", "kind": "actor_missing", "f": 11},
        ]
    }
    assert (
        introduced_story_issues(before, after, original, candidate)
        == after["issues"][1:]
    )


def test_missing_closing_action_can_trigger_recut_even_without_an_issue_list(tmp_path):
    from omni_story.library.dialogue_scene_review import review_scene

    shot = [{"clip": "a", "in": 0, "at": 0}]
    line = {
        "id": "last",
        "actor": "x",
        "at": 0,
        "end": 12,
        "voice_frames": [0],
        "text": "看这张牌",
    }
    project = {"total": 15, "fps": 30, "lines": [line], "actors": {"x": "演员"}}

    def model(case, out, name, prompt, media, validate):
        return validate(
            {
                "issues": [],
                "unknown": [],
                "ending": "只看见演员拿着牌",
                "closing": {"state": "missing", "f": 14, "why": "从没举到对方面前"},
            }
        )

    report = review_scene(
        project,
        tmp_path,
        np.zeros((15, 36, 64, 3), dtype=np.uint8),
        shot,
        {"at": 0, "end": 15},
        [line],
        "draft",
        {"a": {"actions": [], "faces": []}},
        model,
        frame_at,
    )
    assert report["issues"] == [
        {"line": "last", "kind": "payoff_missing", "f": 14, "why": "从没举到对方面前"}
    ]


def test_same_missing_scene_result_is_not_new_when_cited_at_another_frame():
    from omni_story.library.dialogue_scene_review import introduced_story_issues

    draft = [{"at": 0, "clip": "a", "in": 0}]
    changed = [{"at": 0, "clip": "b", "in": 0}]
    old = {"line": "last", "kind": "payoff_missing", "f": 22}
    reviewed = dict(old, f=14)
    assert (
        introduced_story_issues(
            {"issues": [old]}, {"issues": [reviewed]}, draft, changed, {14}
        )
        == []
    )


def test_confirmed_second_mouth_cannot_pass_a_line(monkeypatch, tmp_path):
    from PIL import Image, ImageDraw
    from omni_story.library import dialogue_mouth_tracks as tracks

    pictures = []
    for opened in [False] * 3 + [True] * 3 + [False] * 3:
        image = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(image)
        for x in (180, 440):
            if opened:
                draw.ellipse((x - 18, 168, x + 18, 192), outline="black", width=4)
            else:
                draw.line((x - 18, 180, x + 18, 180), fill="black", width=4)
        pictures.append(np.array(image))

    monkeypatch.setattr(tracks, "sampled_frames", lambda path, step: pictures)
    project = {
        "total": 9,
        "fps": 30,
        "clips": {"A": {"path": "A"}},
        "lines": [
            {
                "id": "l1",
                "actor": "speaker",
                "at": 0,
                "end": 9,
                "text": "一句",
                "voice_frames": list(range(9)),
            }
        ],
    }
    slots = [
        {
            "actor": actor,
            "start": 0,
            "end": 9,
            "center": [(x / 640, 0.5)] * 9,
            "track_frames": list(range(9)),
            "widths": [37 / 640] * 9,
        }
        for actor, x in (("speaker", 180), ("listener", 440))
    ]
    observations = {"A": {"slots": slots}}
    cuts = [{"at": 0, "clip": "A", "in": 0}]
    all_mouths = tracks.rendered_mouths_by_actor(
        project, tmp_path, "out", cuts, observations
    )
    assert all_mouths["l1"] == {"speaker": list(range(9)), "listener": list(range(9))}
    report = score(
        project,
        cuts,
        None,
        observations,
        {"l1": all_mouths["l1"]["speaker"]},
        {"l1": {"listener": all_mouths["l1"]["listener"]}},
    )
    assert report["confirmed_frames"] == 0
    assert report["competing_mouth_frames"] == 9
    assert report["passed"] == 0


def test_known_listener_chewing_only_counts_during_voice_and_in_full_frame():
    from omni_story.library.dialogue_scene_review import timeline_findings

    project = {
        "fps": 30,
        "clips": {"A": {"sha256": "source"}},
        "lines": [
            {
                "id": "l1",
                "actor": "speaker",
                "at": 0,
                "end": 8,
                "voice_frames": [1, 2, 3, 4, 7],
            }
        ],
    }
    sources = {
        "A": {
            "faces": [],
            "blocked": [
                {"actor": "listener", "start": 3, "end": 7, "action": "eating"}
            ],
        }
    }
    blocks = [{"at": 0, "end": 8}]
    cuts = [{"at": 0, "clip": "A", "in": 0}]
    issues = timeline_findings(project, cuts, blocks, sources)
    assert [(i["actor"], i["kind"], i["first"], i["end"]) for i in issues] == [
        ("listener", "other_mouth_moving", 3, 5)
    ]
    cropped = timeline_findings(
        project, [dict(cuts[0], crop=[0, 0, 0.5, 0.5])], blocks, sources
    )
    assert [(i["kind"], i["first"], i["end"]) for i in cropped] == [
        ("other_mouth_unverified", 3, 5)
    ]


def test_full_picture_change_surfaces_inner_cut_in_same_edit_shot():
    from omni_story.library.dialogue_mouth_detail import major_picture_changes

    frames = np.zeros((8, 50, 100, 3), dtype=np.uint8)
    frames[2:5, :10, :10] = 255
    frames[5:] = 210
    assert major_picture_changes(frames) == [5]


def test_source_mouth_center_inside_crop_does_not_prove_whole_mouth_fits():
    from omni_story.library.dialogue_mouth_edit import mouth_survives_crop

    project = {"fps": 30, "clips": {"A": {"masks": []}}}
    cut = {"clip": "A", "crop": [0.2, 0.2, 0.65, 0.65]}
    slot = {
        "track_frames": [10, 11],
        "center": [[0.4, 0.50], [0.4, 0.62]],
        "widths": [0.06, 0.06],
    }
    assert mouth_survives_crop(project, cut, slot, 10)
    assert not mouth_survives_crop(project, cut, slot, 11)


def test_character_voice_keeps_short_quiet_reply_and_rejects_background(monkeypatch):
    from omni_story.library import character_voice as cv

    speech = np.zeros((24_000, 2), dtype=np.float32)
    speech[4800:12_000] = 0.009
    speech[6800:10_800] = 0.05
    monkeypatch.setattr(cv, "_samples", lambda _: speech)
    service = cv.CharacterVoices.__new__(cv.CharacterVoices)
    monkeypatch.setattr(service, "synthesize", lambda line, voice: "received.wav")
    line = {"line_id": "reply"}
    assert len(service.render_line(line, "voice")) >= 7200

    speech.fill(0.009)
    with pytest.raises(cv.VoiceUnavailable, match="insufficient audible speech"):
        service.render_line(line, "voice")
