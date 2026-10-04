"""Execute the real local entrypoint with synthetic media and a fake MCP queue.

Only external model replies are fixtures. Media inventory, extraction, evidence
validation, editing, rendering, and resume all use the production implementation.
These tests measure plumbing/provenance, not model creative quality.
"""
from contextlib import contextmanager
import array
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from omni_story.library.media import probe_media, sha256_file
from omni_story.library.pipeline import execute
from omni_story.library.state import json_sha, write_json

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="Real FFmpeg/FFprobe integration requires both tools")


def _run(arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture
def inputs(tmp_path):
    pytest.importorskip("PIL")
    library = tmp_path / "library"
    library.mkdir()
    for filename, color in (("a.mkv", "red"), ("b.mkv", "blue")):
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=160x90:r=12:d=6",
              "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=16000:duration=6",
              "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=16000:duration=6",
              "-vf", f"drawbox=x=20:y=15:w=35:h=35:color={color}:t=fill",
              "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
              "-c:a", "pcm_s16le", "-metadata:s:a:0", "title=English",
              "-metadata:s:a:1", "title=国语", str(library / filename)])
    reference = tmp_path / "reference.mp4"
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "color=yellow:s=90x160:r=15:d=4",
          "-f", "lavfi", "-i", "sine=frequency=200:sample_rate=48000:duration=4",
          "-f", "lavfi", "-i", "sine=frequency=1700:sample_rate=48000:duration=4",
          "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=zho", str(reference)])
    output = tmp_path / "run"
    output.mkdir()
    write_json(output / "mcp_ready.json", {"model": "test_schema_fixture", "test_fake": True})
    return reference, library, output


@contextmanager
def _bridge(output, answer):
    """Respond to actual recorded jobs, without replacing CodexMCP or validators."""
    queue = output / "mcp_queue"
    queue.mkdir(exist_ok=True)
    stopped = threading.Event()
    received, failures = [], []
    def serve():
        while not stopped.is_set():
            for path in sorted(queue.glob("*.request.json")):
                response = path.with_name(path.name.replace(".request.json", ".response.json"))
                if response.exists():
                    continue
                job = _read(path)
                try:
                    value = answer(job)
                    received.append(job)
                    write_json(response, {"status": "complete", "result": {"content": [
                        {"type": "text", "text": json.dumps(value)}]}})
                except Exception as error:
                    failures.append(repr(error))
                    write_json(response, {"status": "error", "error": repr(error)})
            stopped.wait(0.01)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield received
    finally:
        stopped.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert not failures, failures


def _fixture_responses(reference, library, *, confirmed=True):
    reference_sha = sha256_file(reference)
    source_sha = sha256_file(library / "a.mkv")
    source_id = "src_" + source_sha[:16]
    key = json_sha({"source_sha256": source_sha, "start": 1, "end": 4})[:16]
    window_id = "window_" + key
    def answer(job):
        name = job["job_id"].split("_", 2)[2]
        if name == "reference":
            return {"reference_sha256": reference_sha, "theme": "visible geometric subject",
                "intended_takeaway": "one visible subject is maintained",
                "visible_evidence": [{"start_s": 0, "end_s": 1, "observed_fact": "yellow field", "supports": "a stable subject"}],
                "editing_methods": [{"method": "hold", "function": "make subject visible",
                                     "visual_evidence": "continuous field", "start_s": 0, "end_s": 1}],
                "uncertainties": ["synthetic fixture, no creative quality claim"]}
        if name.startswith(("overview_", "zoom_")):
            lineage = _read(Path(job["arguments"]["image_source"]).parent / "lineage.json")
            return {"source_id": lineage["source_id"],
                "coverage_s": [lineage["source_start_s"], lineage["source_end_s"]],
                "roles": [{"role_id": "red_square", "description": "colored square on black field"}],
                "events": [{"timestamp_s": lineage["frames"][0]["source_time_s"],
                            "observed_fact": "colored square visible", "role_ids": ["red_square"]}], "uncertainties": []}
        if name == "coarse_zoom_search":
            return {"reason": "inspect the first source more closely", "windows": [
                {"source_id": source_id, "start_s": 0, "end_s": 3, "question": "is the square stable", "role_ids": ["red_square"]}]}
        if name == "search_0":
            return {"reason": "observe a continuous interval", "windows": [
                {"source_id": source_id, "start_s": 1, "end_s": 4, "question": "confirm identity and uninterrupted visibility",
                 "role_ids": ["red_square"]}]}
        if name.startswith("fine_"):
            return {"window_id": window_id, "source_id": source_id,
                "roles": [{"role_id": "red_square", "identity_confirmed": confirmed,
                           "state": "red square at the left", "identity_evidence": "stable red color and geometry"}],
                "events": [{"local_start_s": 0.5, "local_end_s": 2.5, "observed_fact": "red square stays visible",
                            "role_ids": ["red_square"]}],
                "usable_ranges": [{"local_in_s": 0.5, "local_out_s": 2.5, "event_indices": [0],
                    "role_ids": ["red_square"], "continuity_notes": "same color and location"}] if confirmed else [],
                "uncertainties": [] if confirmed else ["subject identity unconfirmed"]}
        if name.startswith("plan_0"):
            return {"reference_sha256": reference_sha, "focus_role_id": "red_square",
                "focus_role_bindings": [{"window_id": window_id, "role_id": "red_square",
                    "identity_evidence": "stable red color and geometry in the watched window"}],
                "slots": [{"slot_id": "slot_1", "intended_takeaway": "stable subject", "segment_ids": ["segment_1"]}],
                "segments": [{"segment_id": "segment_1", "slot_id": "slot_1", "source_id": source_id,
                    "window_id": window_id, "source_in_s": 1.5, "source_out_s": 3.5,
                    "speed": 1, "look": "none", "framing": "fit", "role_ids": ["red_square"]}],
                "audio_mode": "reference", "source_gain_db": -12, "reference_gain_db": 0,
                "reference_audio": {"start_s": 0, "end_s": 2, "stream_index": 2, "loop": False},
                "width": 160, "height": 240, "fps": 15, "limitations": ["synthetic test only"]}
        if name == "blind_0":
            return {"observed_story": "a red square remains visible", "main_characters": ["red square"],
                "apparent_theme": "stable subject", "evidence": [{"start_s": 0, "end_s": 1,
                     "observed_fact": "red square on black field"}], "confusions": []}
        if name == "review_0":
            return {"reference_sha256": reference_sha, "theme_status": "pass", "editing_status": "partial",
                "continuity_status": "pass", "evidence": ["synthetic subject remains visible"],
                "limitations": ["fixture response is not model or human evaluation"], "revision_requests": []}
        if name == "selected_review_v2_0":
            return {"reference_sha256":reference_sha,"theme_status":"partial","editing_status":"partial",
                "continuity_status":"pass","evidence":["selected synthetic footage has only part of the intended relation"],
                "limitations":["synthetic protocol test, not a creative-quality assessment"],"revision_requests":[]}
        raise AssertionError("Unexpected pipeline job: " + name)
    return answer


def _execute(inputs):
    reference, library, output = inputs
    return execute(reference, library, output, span_s=3, frames=2, max_fine=1, max_requests=24, asr=False)


def test_selected_legacy_review_is_audited_once_without_overwriting_history(inputs, monkeypatch):
    from omni_story.library import prompts
    from omni_story.library.state import LibraryState
    reference, library, output = inputs
    current_prompt = prompts.review_prompt
    marker = '故事段落顺序相似或slot数量相似'
    monkeypatch.setattr(prompts,'review_prompt',lambda context:current_prompt(context).replace(marker,''))
    with _bridge(output,_fixture_responses(reference,library)) as requests:
        first = _execute(inputs)
        prior_result = (output/'result.json').read_bytes()
        prior_review = (output/'review_0.json').read_bytes()
        prior_requests = {str(p.relative_to(output)):p.read_bytes() for p in (output/'calls').glob('*/request.json')}
        state_data = _read(output/'library_state.json')
        state = LibraryState(output,state_data['input_lock'],max_requests=24)
        state.set_artifact('editing_review_policy',{'policy':'forward_criteria_clarification_fixture'})
        monkeypatch.setattr(prompts,'review_prompt',current_prompt)
        audited = _execute(inputs)
        assert len(requests) == 11 and audited['usage']['requests'] == first['usage']['requests']+1
        assert audited['status'] == 'library_candidate_with_limitations'
        assert audited['review']['theme_status'] == 'partial'
        assert (output/'review_0.json').read_bytes() == prior_review
        assert (output/'result_before_selected_audit.json').read_bytes() == prior_result
        for name,raw in prior_requests.items():
            assert (output/name).read_bytes() == raw
        again = _execute(inputs)
        assert len(requests) == 11 and again['usage']['requests'] == audited['usage']['requests']


def test_real_entrypoint_renders_and_resumes_without_new_requests(inputs):
    reference, library, output = inputs
    with _bridge(output, _fixture_responses(reference, library)) as requests:
        result = _execute(inputs)
        assert len(requests) == 10
        count = result["usage"]["requests"]
        prior_requests = {str(p.relative_to(output)): p.read_bytes() for p in (output / "calls").glob("*/request.json")}
        final = Path(result["final_video"])
        final_before = final.read_bytes()
        repeated = _execute(inputs)
        assert repeated["usage"]["requests"] == count
        assert len(requests) == 10
        assert {str(p.relative_to(output)): p.read_bytes()
                for p in (output / "calls").glob("*/request.json")} == prior_requests
        assert final.read_bytes() == final_before
    assert result["final_sha256"] == sha256_file(final)
    assert result["reference_sha256"] == sha256_file(reference)
    assert result["actual_fine_windows"] == 1
    assert result["human_creative_inputs"] == []
    _run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"])
    metadata = probe_media(final)
    video = next(row for row in metadata["streams"] if row["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (160, 240, "15/1")
    manifest = _read(output / "render_0" / "render_result.json")
    assert manifest["reference_audio"]["stream_index"] == 2
    provenance = manifest["provenance"][0]
    assert (provenance["source_in_s"], provenance["source_out_s"]) == (1.5, 3.5)
    assert provenance["source_sha256"] == sha256_file(library / "a.mkv")
    assert provenance["source_audio_stream_index"] == 2
    watched = _read(output / "watched_windows.json")[0]
    assert watched["observation"]["roles"][0]["identity_confirmed"] is True
    assert (watched["source_start_s"], watched["source_end_s"]) == (1, 4)
    decoded = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(final), "-map", "0:a:0",
                    "-ar", "16000", "-ac", "1", "-f", "s16le", "-"])
    samples = array.array("h", decoded)[1600:-1600]
    hz = sum(a <= 0 < b for a, b in zip(samples, samples[1:])) / (len(samples) / 16000)
    assert hz == pytest.approx(1700, abs=10), "Actual final audio must come from selected reference stream 2"
    # Zoom input was overview B; the last observed coarse image became zoom A.
    zoom_job = next(job for job in requests if job["job_id"].endswith("coarse_zoom_search"))
    zoom_input = _read(Path(zoom_job["arguments"]["image_source"]).parent / "lineage.json")
    last_index = _read(output / "coarse_index.json")[-1]
    assert zoom_input["source_id"] != last_index["source_id"]


def test_entrypoint_refuses_unconfirmed_focus_identity_before_render(inputs):
    reference, library, output = inputs
    with _bridge(output, _fixture_responses(reference, library, confirmed=False)):
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted:plan_0"):
            _execute(inputs)
    assert not (output / "result.json").exists()
    assert not (output / "render_0" / "final.mp4").exists()
    failures = list((output / "calls").glob("*/protocol_failure.json"))
    assert any("unknown_or_duplicate_ref" in _read(path)["error"] for path in failures)
