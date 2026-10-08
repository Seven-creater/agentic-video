"""Small flow checks with synthetic media and fake queue replies; no MCP launch."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from omni_story.library import visual_story_trial as flow
from omni_story.library.media import probe_media, sha256_file
from omni_story.library.state import LibraryStopped, scope_fingerprint, write_json


def auth():
    return {"parent": {"duration_s": 77.366667}, "reference": {"duration_s": 21.933333},
            "target_duration_s": 21.933333, "target_tolerance_s": 2.0}


def source():
    return {"source_id": "parent77", "path": "synthetic-parent.mp4", "sha256": "2" * 64,
            "duration_s": 77.366667, "audio_stream_index": None}


def plan():
    return {"segments": [{"segment_id": "clip_a", "source_id": "parent77",
                          "window_id": "full_parent_observed", "source_in_s": 18.5,
                          "source_out_s": 40.2, "speed": 1.0, "freeze_tail_s": 0,
                          "look": "none", "framing": "fit", "contribution_to": ["beat_a"],
                          "reason": "synthetic visible information contribution"}], "limitations": []}

def test_rewording_or_segment_id_is_not_new_executed_editing():
    original=plan();rewritten=deepcopy(original)
    rewritten['segments'][0].update(reason='different explanation only',segment_id='different_name')
    assert flow.editing_fingerprint(original,source())==flow.editing_fingerprint(rewritten,source())
    rewritten['segments'][0]['speed']=1.1
    assert flow.editing_fingerprint(original,source())!=flow.editing_fingerprint(rewritten,source())

def test_resume_skips_lost_page_before_extracting_or_calling_model(tmp_path,monkeypatch):
    from PIL import Image
    from omni_story.library import microclip_frames
    trial=flow.Trial.__new__(flow.Trial)
    trial.base=tmp_path;trial.roi={'crop':[0,0,100,60]}
    lost={'kind':'sparse_contact_sheet','source_sha256':'2'*64,'source_start_s':54,'source_end_s':55.5}
    trial.auth={'parent':{'path':'synthetic-parent','sha256':'2'*64,'duration_s':58},
                'unknown_resume':{'skip_stages':['vss_detail_3_0'],'lost_scope':lost,'lost_call_id':'lost_265'}}
    trial.inspections=[{'request_sha256':str(i)} for i in range(3)]
    calls=[];extracted=[]
    trial.call=lambda name,*args,**kwargs: calls.append(name) or {'facts':[],'uncertainties':[]}
    monkeypatch.setattr(flow,'proxy',lambda *a,**k:{'path':'synthetic-window'})
    monkeypatch.setattr(microclip_frames,'frame_catalog',lambda *a,**k:{'source':{'source_end_s':58}})
    def grid(source_path,start,end,output_dir,**kwargs):
        extracted.append((start,end));folder=Path(output_dir);folder.mkdir(parents=True)
        frame=folder/'frame.png';Image.new('RGB',(100,60),'blue').save(frame)
        display=folder/'grid.png';Image.new('RGB',(100,60),'blue').save(display)
        return {'grid':{'path':str(display)},'manifest_path':str(folder/'manifest.json'),
                'frames':[{'png_path':str(frame),'source_time_s':start}]}
    monkeypatch.setattr(microclip_frames,'extract_grid',grid)
    trial.inspect([{'target':'parent','start_s':54,'end_s':58,'step_s':.25,'question':'synthetic missing information'}])
    assert calls==['vss_inspect_3','vss_detail_3_1','vss_detail_3_2']
    assert extracted==[(55.5,57),(57,58)]
    skipped=trial.inspections[-1]['frame_sheets'][0]
    assert skipped['status']=='unknown_not_replayed' and skipped['call_id']=='lost_265'
    assert 'facts' not in skipped


def test_json_parser_uses_one_object_and_fenced_object_only():
    assert flow.parse_model_json('{"facts":[]}') == {"facts": []}
    assert flow.parse_model_json('```json\n{"facts":[]}\n```') == {"facts": []}
    for text in ('[]', '{"facts":}', 'answer: {"facts":[]}'):
        with pytest.raises(ValueError):
            flow.parse_model_json(text)


@pytest.mark.parametrize("mutation", ["negative", "past_end", "reverse", "too_long", "too_dense", "nan", "bool"])
def test_local_inspection_rejects_invalid_ranges_and_precision(mutation):
    row = {"target": "parent", "start_s": 20.0, "end_s": 22.0,
           "step_s": 0.1, "question": "What visible state changes?"}
    if mutation == "negative": row["start_s"] = -1
    elif mutation == "past_end": row["end_s"] = 80
    elif mutation == "reverse": row["end_s"] = 19
    elif mutation == "too_long": row["end_s"] = 27
    elif mutation == "too_dense": row["end_s"] = 25
    elif mutation == "nan": row["start_s"] = float("nan")
    else: row["start_s"] = True
    with pytest.raises(ValueError):
        flow.inspect_check([row], auth())


@pytest.mark.parametrize("value", [{"story": [1], "facts": []}, {"segments": [1]}])
def test_nested_nonobjects_receive_format_repair_compatible_errors(value):
    with pytest.raises((ValueError, TypeError, KeyError)):
        if "story" in value:
            flow.observe_check(value, auth())
        else:
            flow.plan_check(value, auth(), source(), [{"id": "beat_a"}])


def test_parent_time_plan_accepts_independent_real_range_and_original_music_length():
    value = plan()
    flow.plan_check(value, auth(), source(), [{"id": "beat_a"}])
    compiled = flow.compile_library_plan([source()], {"segments": value["segments"], "audio_mode": "silent"},
                                         fps=30, width=1280, height=720)
    assert compiled["segments"][0]["source_in_s"] == 18.5
    assert compiled["duration_s"] == pytest.approx(21.7)


@pytest.mark.parametrize("mutation", ["too_short", "music_overrun", "source_overrun", "nan", "caption", "unsupported_speed", "wrong_source"])
def test_plan_rejects_invalid_source_ranges_or_music_overrun(mutation):
    value = plan()
    row = value["segments"][0]
    if mutation == "too_short": row["source_out_s"] = 22.0
    elif mutation == "music_overrun": row.update(source_in_s=0, source_out_s=22.2)
    elif mutation == "source_overrun": row["source_out_s"] = 80
    elif mutation == "nan": row["speed"] = float("nan")
    elif mutation == "caption": row["caption"] = {"text": "invented explanatory answer"}
    elif mutation == "unsupported_speed": row["speed"] = 5
    else: row["source_id"] = "unbound_source"
    with pytest.raises(ValueError):
        flow.plan_check(value, auth(), source(), [{"id": "beat_a"}])


@pytest.fixture(scope="module")
def actual_media(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg required for actual synthetic media checks")
    root = tmp_path_factory.mktemp("vss_synthetic_media")
    path = root / "parent.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=red:s=160x90:r=30:d=4", "-f", "lavfi", "-i",
                    "sine=frequency=700:sample_rate=48000:duration=4", "-vf",
                    "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,2)'",
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True, timeout=30)
    meta = probe_media(path)
    return {"path": str(path), "sha256": sha256_file(path), "duration_s": meta["duration_s"],
            "video_stream_index": meta["video_stream_index"], "audio_stream_index": 1}


def frame_rgb(path, timestamp):
    return subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(timestamp), "-i", str(path),
                           "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                          check=True, capture_output=True, timeout=30).stdout[:3]


def test_actual_proxy_preserves_full_and_local_timeline_speed_and_omits_audio(actual_media, tmp_path):
    whole = flow.proxy(actual_media, 0, 4, tmp_path, label="whole")
    local = flow.proxy(actual_media, 1.5, 3.5, tmp_path, label="local")
    for result, duration in ((whole, 4), (local, 2)):
        meta = probe_media(result["path"])
        video = next(s for s in meta["streams"] if s["codec_type"] == "video")
        assert meta["duration_s"] == pytest.approx(duration, abs=0.04)
        assert int(video["nb_frames"]) == duration * 30
        assert not any(s["codec_type"] == "audio" for s in meta["streams"])
        assert result["source_sha256"] == actual_media["sha256"]
    assert whole["source_start_s"] == 0 and whole["source_end_s"] == 4
    assert local["source_offset_s"] == 1.5
    red, blue = frame_rgb(local["path"], 0.1), frame_rgb(local["path"], 0.8)
    assert red[0] > 150 and red[2] < 70
    assert blue[2] > 150 and blue[0] < 70
    assert flow.proxy(actual_media, 1.5, 3.5, tmp_path, label="local") == local


def test_renderer_receives_model_parent_seconds_and_operations_unchanged(tmp_path, monkeypatch):
    instance = flow.Trial.__new__(flow.Trial)
    instance.base = tmp_path
    instance.render_source = source()
    instance.state = SimpleNamespace(set_artifact=lambda *args: None)
    instance.status = lambda *args, **kwargs: None
    calls = []
    monkeypatch.setattr(flow, "render_library_video", lambda catalog, value, output, **kwargs:
                        calls.append((catalog, value, output, kwargs)) or {"rendered": True})
    value = plan()
    value["segments"][0].update(speed=0.75, freeze_tail_s=0.25)
    original = deepcopy(value)
    instance.render(value, 0)
    assert value == original
    assert calls[0][0] == [source()]
    assert calls[0][1]["segments"] == original["segments"]
    assert calls[0][1]["audio_mode"] == "silent"


def test_blind_input_has_actual_lineage_and_no_story_or_teacher_answers(actual_media, tmp_path):
    instance = flow.Trial.__new__(flow.Trial)
    instance.base = tmp_path
    instance.reference = {"SECRET_REFERENCE_ANSWER": "reference answers"}
    instance.observed = {"story": [{"id": "beat_a", "contribution": "SECRET_STORY_ANSWER"}]}
    instance.knowledge = "Generic workflow only"
    instance.inspections = []
    instance.auth = {"target_duration_s": 4}
    result = {"rendered_path": actual_media["path"], "sha256": actual_media["sha256"], "duration_s": 4}
    blind = flow.proxy(actual_media, 0, 4, tmp_path, roi=[0, 0, 160, 80], label="blind_test")
    instance.blind_proxy = lambda selected: blind
    captured = []
    def fake_call(name, prompt, media, scope, validator, **kwargs):
        lineage = flow.read(Path(media).parent / "lineage.json")
        assert scope_fingerprint(scope) == scope_fingerprint(lineage)
        captured.append((name, prompt, kwargs["purpose"]))
        value = {"apparent_story": "synthetic picture-only state", "status": "partial", "problems": []}
        validator(value)
        return value
    instance.call = fake_call
    instance.review({"segments": [{"SECRET_EDL_ANSWER": 123}]}, result, 0)
    assert len(captured) == 2
    assert captured[0][0] == "vss_blind" and captured[1][0] == "vss_review"
    for secret in ("SECRET_REFERENCE_ANSWER", "SECRET_STORY_ANSWER", "SECRET_EDL_ANSWER"):
        assert secret not in captured[0][1]
    assert "SECRET_EDL_ANSWER" in captured[1][1]


def test_recovery_only_scans_new_calls_and_never_replays_or_reclassifies_250_baseline(tmp_path, monkeypatch):
    old = [{"id": f"old_{i}", "status": "uncertain" if i in (4, 131, 166) else "received"}
           for i in range(250)]
    new = {"id": "glm_251_vss_observe", "status": "submitted"}
    entries = []
    class State:
        data = {"calls": old + [new]}
        output = tmp_path
        def _reload(self): pass
        def reconcile_received(self, call, reply, **kwargs): entries.append(call["id"])
        def complete_call(self, *args, **kwargs): pytest.fail("no queued transport used")
        def fail_call(self, *args, **kwargs): pytest.fail("no failed transport used")
    instance = flow.TrialMCP.__new__(flow.TrialMCP)
    instance.state = State()
    instance.output = tmp_path
    instance.queue = tmp_path / "mcp_queue"
    inspected = []
    monkeypatch.setattr(flow, "_http_evidence", lambda root, ident: inspected.append(ident) or [])
    monkeypatch.setattr(flow, "_captured_reply", lambda evidence: ({"status": 200}, {"captured": True}))
    instance.recover_received()
    assert inspected == [new["id"]] and entries == [new["id"]]


def test_delivery_mux_retains_reference_audio_speed_and_full_tail(actual_media, tmp_path, monkeypatch):
    import array
    picture = flow.proxy(actual_media, 0, 2, tmp_path, label="silent_selected")
    instance = flow.Trial.__new__(flow.Trial)
    instance.base = tmp_path
    instance.output = tmp_path
    instance.auth = {"reference": actual_media}
    stored = []
    instance.state = SimpleNamespace(_reload=lambda: None, data={"request_count": 251},
                                     set_artifact=lambda name, value: stored.append((name, value)))
    instance.status = lambda *args, **kwargs: None
    monkeypatch.setattr(flow, "get_auth", lambda *args, **kwargs: {})
    selected = {"rendered_path": picture["path"], "sha256": picture["sha256"], "duration_s": 2}
    result = instance.finish(selected, {"target": {"status": "partial"}})
    metadata = probe_media(result["final_video"])
    assert metadata["duration_s"] == pytest.approx(2, abs=0.04)
    assert any(s["codec_type"] == "audio" for s in metadata["streams"])
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", result["final_video"],
                          "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                         check=True, capture_output=True, timeout=30).stdout
    samples = array.array("h", raw)
    central = samples[4800:48000]
    hz = sum(a <= 0 < b for a, b in zip(central, central[1:])) / (len(central) / 48000)
    assert hz == pytest.approx(700, abs=8), "retiming visual clips must not retime reference music"
    tail = samples[int(1.6 * 48000):int(1.8 * 48000)]
    assert max(abs(v) for v in tail) > 100, "no silent post-reference tail"
    assert result["author"] == "GLM-5.3-Flash" and result["new_requests"] == 1
