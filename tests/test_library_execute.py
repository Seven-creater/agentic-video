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


@pytest.mark.parametrize('seeded', [False, True])
def test_clean_full_timeline_proxies_are_silent_and_cached_reference_is_not_reencoded(inputs, monkeypatch, seeded):
    from omni_story.library import pipeline, visual_story_trial
    from omni_story.library.clean_chain import ROUGH_PROMPTS
    from omni_story.library.opencode_provider import PROVIDER
    from omni_story.library.resources import original_rough_v1
    reference, library, output = inputs
    answer = _fixture_responses(reference, library)
    seed = (dict(full_response={'reference': answer({'job_id': 'glm_000_reference'})},
        evidence_limit='synthetic received reference fixture') if seeded else None)
    source_hash = sha256_file(reference)
    proxy_calls = []
    native_proxy, native_prepare = visual_story_trial.proxy, pipeline.prepare_window

    def visual_proxy(source, start, end, base, **options):
        assert start == 0 and end == source['duration_s']
        result = native_proxy(source, start, end, base, **options)
        proxy_calls.append((options['label'], source, result))
        return result

    def local_window(source, start, end, base, **options):
        assert start != 0 or end != source['duration_s'], 'clean full timeline used the capped audio proxy'
        return native_prepare(source, start, end, base, **options)

    class QueueMCP(pipeline.CodexMCP):
        provider = PROVIDER['provider']

    monkeypatch.setattr(visual_story_trial, 'proxy', visual_proxy)
    monkeypatch.setattr(pipeline, 'prepare_window', local_window)
    with _bridge(output, answer) as requests:
        result = execute(reference, library, output, span_s=3, frames=2, max_fine=None,
            max_requests=None, asr=False, reference_seed=seed, prompt_module=ROUGH_PROMPTS,
            render_fn=original_rough_v1.render_library_video, model_factory=QueueMCP,
            provider_config={**PROVIDER, 'workflow': 'reference_rough_skill_v1'})
    assert [row[0] for row in proxy_calls] == (
        ['clean_actual_rough'] if seeded else ['clean_reference_full', 'clean_actual_rough'])
    for _, source, proxy in proxy_calls:
        assert proxy['source_sha256'] == source['sha256']
        assert proxy['source_start_s'] == 0 and proxy['source_end_s'] == source['duration_s']
        assert proxy['metadata']['duration_s'] == pytest.approx(source['duration_s'], abs=.075)
        assert proxy['audio_present'] is False
        assert not any(row['codec_type'] == 'audio' for row in proxy['metadata']['streams'])
        assert sha256_file(proxy['path']) == proxy['sha256']
    reference_jobs = [job for job in requests if job['job_id'].endswith('_reference')]
    assert bool(reference_jobs) is not seeded
    if not seeded:
        assert 'physically omits audio' in reference_jobs[0]['arguments']['prompt']
        assert 'complete reference timeline' in reference_jobs[0]['arguments']['prompt']
        request_path = output / 'calls' / reference_jobs[0]['job_id'] / 'request.json'
        request = _read(request_path)
        assert request['observation_scope']['source_sha256'] == source_hash
        assert request['observation_scope']['source_end_s'] == probe_media(reference)['duration_s']
        assert request['media_sha256'] == proxy_calls[0][2]['sha256']
    assert sha256_file(reference) == source_hash
    assert any(row['codec_type'] == 'audio' for row in probe_media(result['final_video'])['streams'])
    review_job = next(job for job in requests if job['job_id'].endswith('_review_0'))
    assert 'physically omits audio' in review_job['arguments']['prompt']
    assert 'native cloud frame sampling' in review_job['arguments']['prompt']


def test_silent_dynamic_proxy_preserves_a_real_long_timeline_beyond_audio_proxy_budget(tmp_path):
    from omni_story.library.media import inventory_sources, prepare_window
    from omni_story.library.visual_story_trial import proxy
    source_path = tmp_path / 'long.mp4'
    _run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'color=blue:s=32x32:r=1:d=480', '-f', 'lavfi', '-i',
        'sine=frequency=700:sample_rate=8000:duration=480', '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-b:a', '24k', str(source_path)])
    source = inventory_sources(source_path, tmp_path / 'catalog')['sources'][0]
    with pytest.raises(ValueError, match='proxy_range_too_long_for_size_budget'):
        prepare_window(source, 0, source['duration_s'], tmp_path / 'legacy')
    actual = proxy(source, 0, source['duration_s'], tmp_path / 'visual', label='clean_actual_rough')
    assert actual['source_end_s'] == source['duration_s'] == pytest.approx(480)
    assert actual['metadata']['duration_s'] == pytest.approx(480, abs=.075)
    assert actual['source_sha256'] == sha256_file(source_path)
    assert actual['audio_present'] is False
    assert Path(actual['path']).stat().st_size < 8_000_000
    streams = actual['metadata']['streams']
    assert not any(row['codec_type'] == 'audio' for row in streams)
    video = next(row for row in streams if row['codec_type'] == 'video')
    assert video['r_frame_rate'] == '30/1' and int(video['nb_frames']) == 14_400
    assert proxy(source, 0, source['duration_s'], tmp_path / 'visual', label='clean_actual_rough') == actual


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


def test_progress_driven_chain_stops_identical_edl_without_another_render(inputs):
    from omni_story.library.pipeline import CodexMCP
    from omni_story.library.opencode_provider import PROVIDER
    from omni_story.library.clean_chain import ROUGH_PROMPTS
    reference, library, output = inputs
    original = _fixture_responses(reference, library)
    def answer(job):
        name = job['job_id'].split('_', 2)[2]
        if name == 'search_1':
            return {'reason': 'No additional evidence to observe', 'windows': []}
        if name == 'plan_1':
            job = {**job, 'job_id': 'glm_001_plan_0'}
        value = original(job)
        if name == 'review_0':
            value.update(theme_status='fail', continuity_status='partial')
        return value
    with _bridge(output, answer) as requests:
        result = execute(reference, library, output, span_s=3, frames=2, max_fine=None,
            max_requests=None, asr=False, model_factory=CodexMCP,
            provider_config={**PROVIDER, 'workflow': 'reference_rough_skill_v1'}, prompt_module=ROUGH_PROMPTS)
    assert result['selected_round'] == 0
    assert (output / 'render_0/final.mp4').is_file()
    assert not (output / 'render_1/final.mp4').exists()
    state = _read(output / 'library_state.json')
    assert state['max_requests'] is None
    assert all(state['input_lock']['configuration'][key] is None for key in ('max_fine','max_rounds','max_renders'))
    assert state['artifacts']['rough_no_progress']
    assert sum(job['job_id'].endswith('_plan_1') for job in requests) == 1


def test_clean_empty_search_without_watched_footage_stops_before_plan(inputs):
    from omni_story.library.clean_chain import ROUGH_PROMPTS
    from omni_story.library.opencode_provider import PROVIDER
    from omni_story.library.pipeline import CodexMCP
    from omni_story.library.state import LibraryStopped
    reference, library, output = inputs
    original = _fixture_responses(reference, library)
    reason = 'Observed coarse evidence does not justify a continuous footage window.'

    def answer(job):
        name = job['job_id'].split('_', 2)[2]
        if name == 'search_0':
            return {'reason': reason, 'windows': []}
        assert not name.startswith(('fine_', 'plan_', 'blind_', 'review_')), name
        return original(job)

    with _bridge(output, answer) as requests:
        with pytest.raises(LibraryStopped, match='no_watched_window_available_for_planning'):
            execute(reference, library, output, span_s=3, frames=2, max_fine=None,
                max_requests=None, asr=False, model_factory=CodexMCP,
                provider_config={**PROVIDER, 'workflow': 'reference_rough_skill_v1'},
                prompt_module=ROUGH_PROMPTS)
    names = [job['job_id'].split('_', 2)[2] for job in requests]
    assert names[-1] == 'search_0'
    assert not any(name.startswith(('fine_', 'plan_', 'blind_', 'review_')) for name in names)
    assert _read(output / 'search_0.json') == {'reason': reason, 'windows': []}
    state = _read(output / 'library_state.json')
    evidence = _read(state['artifacts']['rough_no_observed_footage'][0]['path'])
    assert evidence == {'round': 0, 'reason': 'No watched footage is available for a valid edit plan.',
        'search_reason': reason, 'requested_window_count': 0,
        'watched_window_count': 0, 'new_observed_window_count': 0}
    assert all(call['status'] == 'received' for call in state['calls'])
    assert _read(output / 'failure.json')['error'] == 'no_watched_window_available_for_planning'
    assert _read(output / 'current_status.json')['stage'] == 'stopped'
    assert not (output / 'plan_0.json').exists()
    assert not (output / 'render_0').exists()
    assert not (output / 'result.json').exists()


def test_clean_inherited_unknown_range_is_reported_and_skipped_before_observation(inputs):
    from omni_story.library.clean_chain import ROUGH_PROMPTS
    from omni_story.library.opencode_provider import PROVIDER
    from omni_story.library.pipeline import CodexMCP
    from omni_story.library.state import LibraryStopped
    reference, library, output = inputs
    source_hash = sha256_file(library / 'a.mkv')
    scope = dict(kind='continuous_window', source_sha256=source_hash, source_start_s=1, source_end_s=4)
    lost = dict(call_id='glm_016_original_unknown', media_sha256='b' * 64, scope=scope)
    original = _fixture_responses(reference, library)
    def answer(job):
        name = job['job_id'].split('_', 2)[2]
        assert not name.startswith(('fine_', 'plan_', 'blind_', 'review_')), name
        if name == 'search_0':
            assert 'unavailable_observation_ranges' in job['arguments']['prompt']
            assert source_hash in job['arguments']['prompt']
            assert 'Result unknown; this original source range must not be replayed.' in job['arguments']['prompt']
        return original(job)
    with _bridge(output, answer) as requests:
        with pytest.raises(LibraryStopped, match='no_watched_window_available_for_planning'):
            execute(reference, library, output, span_s=3, frames=2, max_fine=None, max_requests=None,
                asr=False, model_factory=CodexMCP, prompt_module=ROUGH_PROMPTS,
                provider_config={**PROVIDER, 'workflow': 'reference_rough_skill_v1',
                    'parent_baseline': {'unknown_inputs': [lost]}})
    assert requests[-1]['job_id'].endswith('_search_0')
    state = _read(output / 'library_state.json')
    skipped = _read(state['artifacts']['unknown_observation_query_skipped'][0]['path'])
    assert skipped['call_id'] == lost['call_id'] and skipped['scope'] == scope
    assert skipped['status'] == 'unobserved_unknown_paid_result_no_replay'
    assert not (output / 'watched_windows.json').exists()
    assert not (output / 'plan_0.json').exists()


def test_clean_real_media_reuses_parent_overviews_and_bound_zoom_repair(inputs, monkeypatch):
    import sys
    from omni_story.library import opencode_provider, server_cli
    from omni_story.library.clean_chain import ROUGH_PROMPTS
    from omni_story.library.media import inventory_sources, prepare_window
    from omni_story.library.pipeline import CodexMCP
    from omni_story.library.state import LibraryState
    reference, library, parent = inputs
    answer = _fixture_responses(reference, library)
    seed = dict(full_response={'reference': answer({'job_id': 'glm_000_reference'})},
        evidence_limit='synthetic received reference fixture')
    class QueueMCP(CodexMCP):
        provider = opencode_provider.PROVIDER['provider']
    def original_reply(job):
        name = job['job_id'].split('_', 2)[2]
        if name.startswith('zoom_') and not name.endswith('_repair'):
            value = answer(job)
            return {**value, 'coverage_s': [0, 2]}
        if name.startswith('zoom_') and name.endswith('_repair'):
            job = {**job, 'job_id': job['job_id'].removesuffix('_repair')}
        return answer(job)
    config = {**opencode_provider.PROVIDER, 'workflow': 'reference_rough_skill_v1'}
    with _bridge(parent, original_reply):
        execute(reference, library, parent, span_s=3, frames=2, max_fine=None, max_requests=None,
            asr=False, model_factory=QueueMCP, prompt_module=ROUGH_PROMPTS,
            provider_config=config, reference_seed=seed)
    old = _read(parent / 'library_state.json')
    state = LibraryState(parent, old['input_lock'], max_requests=None)
    source = inventory_sources(library / 'a.mkv', parent / 'unknown_catalog')['sources'][0]
    proxy = prepare_window(source, 4, 5, parent / 'unknown_media')
    request = dict(tool='analyze_video', arguments={'video_source': proxy['path'], 'prompt': 'unknown original query'},
        media_sha256=proxy['sha256'], provider=opencode_provider.PROVIDER['provider'], policy_version=old['policy_version'])
    lost, _ = state.begin_call('fine_0000000000000000', request)
    state.fail_call(lost, 'result unknown', uncertain=True)
    output = parent / 'evaluations/new'
    baseline = server_cli.parent_baseline(parent, output)
    sealed = {str(path): path.read_bytes() for path in parent.rglob('*.json')}
    package = parent / 'package'
    index = package / 'node_modules/@z_ai/mcp-server/build/index.js'
    index.parent.mkdir(parents=True); index.write_text('// never executed')
    monkeypatch.setenv('Z_AI_API_KEY', 'synthetic-key')
    class QueueOpenCode(opencode_provider.OpenCodeMCP):
        _submit = CodexMCP._submit
    def factory(state):
        write_json(output / 'mcp_ready.json', {'test_fake': True})
        return QueueOpenCode(state, package_root=package, executable=sys.executable,
            exclusions=baseline['unknown_inputs'])
    output.mkdir(parents=True)
    with _bridge(output, answer) as requests:
        result = execute(reference, library, output, span_s=3, frames=2, max_fine=None, max_requests=None,
            asr=False, model_factory=factory, prompt_module=ROUGH_PROMPTS, reference_seed=seed,
            provider_config={**config, 'parent_baseline': baseline, 'prior_requests': baseline['request_count']})
    names = [job['job_id'].split('_', 2)[2] for job in requests]
    assert not any(name.startswith(('overview_', 'zoom_', 'fine_')) or name == 'coarse_zoom_search' for name in names)
    assert names == ['search_0', 'plan_0', 'blind_0', 'review_0']
    current = _read(output / 'library_state.json')
    receipts = [_read(row['path']) for row in current['artifacts']['parent_observation_reuse']]
    assert sum(row['current_stage'].startswith('overview_') for row in receipts) == 2
    repaired = next(row for row in receipts if row['current_stage'].startswith('zoom_'))
    assert repaired['source_call_id'].endswith('_repair')
    assert not repaired['source_original_call_id'].endswith('_repair')
    assert result['usage']['requests'] == 4
    assert {path: Path(path).read_bytes() for path in sealed} == sealed


def test_entrypoint_refuses_unconfirmed_focus_identity_before_render(inputs):
    reference, library, output = inputs
    with _bridge(output, _fixture_responses(reference, library, confirmed=False)):
        with pytest.raises(ValueError, match="model_protocol_repair_exhausted:plan_0"):
            _execute(inputs)
    assert not (output / "result.json").exists()
    assert not (output / "render_0" / "final.mp4").exists()
    failures = list((output / "calls").glob("*/protocol_failure.json"))
    assert any("unknown_or_duplicate_ref" in _read(path)["error"] for path in failures)


def _editing_fixture_responses(reference, library, *, omit_first_binding=False):
    base = _fixture_responses(reference, library)
    reference_sha = sha256_file(reference)
    def answer(job):
        name = job["job_id"].split("_", 2)[2]
        if name == "editing_reference_v2":
            return {"reference_sha256": reference_sha, "methods": [{"method_id": "method_0",
                "reference_method_index": 0, "form": "continuous view with a tail hold",
                "function": "keep the geometric subject clearly visible",
                "source_start_s": 0, "source_end_s": 1, "evidence_type": "model_estimate",
                "requires_audio": False, "material_requirements": ["a stable visible subject"],
                "verification_rule": "subject remains visible through the held output tail", "uncertainties": []}],
                "uncertainties": ["synthetic fixture does not assess creative quality"]}
        value = base(job)
        if name.startswith("fine_"):
            value["editing_observations"] = [{"method_id": "method_0", "local_start_s": 0.5,
                "local_end_s": 2.5, "observed_form": "stable colored geometry in an uninterrupted frame",
                "potential_use": "hold the visible subject", "limitations": []}]
        if name.startswith("plan_0"):
            value["fps"] = 30
            segment = value["segments"][0]
            segment.update(freeze_tail_s=0.5, caption={"text": "红色方块\n保持可见", "start_s": 2,
                "end_s": 2.5, "position": "center", "font_size": 18,
                "evidence": [{"window_id": segment["window_id"], "event_indices": [0]}]})
            value["editing_bindings"] = [{"method_id": "method_0", "status": "planned",
                "segment_ids": [segment["segment_id"]], "intended_relation": "subject remains visible",
                "operation": "play the selected range then hold its actual final frame",
                "verification": "inspect the last half second and its visible caption", "limitations": []}]
            value["candidate_dispositions"] = [{"window_id": segment["window_id"], "decision": "selected",
                "reason": "the observed range contains the stable visible square"}]
            if omit_first_binding and name == "plan_0":
                value.pop("editing_bindings")
        if name == "review_0":
            value["editing_status"] = "pass"
            value["method_checks"] = [{"method_id": "method_0", "form_status": "pass",
                "function_status": "pass", "audio_status": "not_applicable",
                "output_evidence": [{"start_s": 2, "end_s": 2.5,
                    "observed_fact": "red square remains visible during the held tail and Chinese caption"}],
                "limitations": ["synthetic protocol fixture, not creative-quality evidence"]}]
        return value
    return answer


def _execute_editing(inputs):
    reference, library, output = inputs
    return execute(reference, library, output, span_s=3, frames=2, max_fine=1,
                   max_requests=24, asr=False, editing_v2=True)


def test_editing_v2_real_render_records_timelines_caption_hold_and_policy_resume(inputs):
    from omni_story.library.prompts import EDITING_PROTOCOL
    reference, library, output = inputs
    with _bridge(output, _editing_fixture_responses(reference, library)) as requests:
        result = _execute_editing(inputs)
        assert len(requests) == result["usage"]["requests"] == 11
        assert result["status"] == "model_checked_library_candidate"
        assert result["editing_protocol"] == EDITING_PROTOCOL
        final = Path(result["final_video"])
        prior_requests = {str(path.relative_to(output)): path.read_bytes()
                          for path in (output / "calls").glob("*/request.json")}
        final_before = final.read_bytes()
        policy_before = _read(output / "library_state.json")["artifacts"]["editing_execution_policy"]
        # Omitting the flag still obeys the policy recorded in this same task.
        repeated = _execute(inputs)
        assert repeated == result
        assert len(requests) == repeated["usage"]["requests"] == 11
        assert final.read_bytes() == final_before
        assert {str(path.relative_to(output)): path.read_bytes()
                for path in (output / "calls").glob("*/request.json")} == prior_requests
        assert _read(output / "library_state.json")["artifacts"]["editing_execution_policy"] == policy_before
    assert final == output / "render_0" / "final.mp4"
    assert result["final_sha256"] == sha256_file(final)
    manifest = _read(output / "render_0" / "render_result.json")
    assert manifest["renderer_version"] == "library_render_v2"
    assert manifest["duration_s"] == pytest.approx(2.5)
    assert probe_media(final)["duration_s"] == pytest.approx(2.5, abs=0.05)
    row = manifest["provenance"][0]
    assert (row["motion_frames"], row["freeze_frames"], row["frames"]) == (60, 15, 75)
    assert (row["source_in_s"], row["source_out_s"]) == (1.5, 3.5)
    assert row["freeze_source"]["source_sha256"] == sha256_file(library / "a.mkv")
    caption = row["caption"]
    assert (caption["start_frame"], caption["end_frame"]) == (60, 75)
    assert caption["text"] == "红色方块\n保持可见"
    assert sha256_file(output / "render_0" / caption["font_file"]) == caption["font_sha256"]
    raw = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(final), "-pix_fmt", "rgb24",
                "-f", "rawvideo", "-"])
    size = 160 * 240 * 3
    frames = [raw[i:i + size] for i in range(0, len(raw), size)]
    def white_pixels(frame):
        return sum(all(channel > 185 for channel in frame[i:i + 3]) for i in range(0, len(frame), 3))
    assert len(frames) == 75
    assert all(white_pixels(frame) == 0 for frame in frames[:60])
    assert all(white_pixels(frame) > 30 for frame in frames[60:])
    watched = _read(output / "watched_windows.json")[0]
    assert watched["editing_timeline"]["time_domain"] == "analysis_proxy_local_seconds"
    timelines = [_read(path) for path in (output / "media_cache" / "shot_timelines").glob("*/timeline.json")]
    by_sha = {record["source_sha256"]: record for record in timelines}
    assert {sha256_file(reference), sha256_file(final), watched["sha256"]} <= by_sha.keys()
    assert by_sha[sha256_file(reference)]["spec"]["source_path"] == str(reference.resolve())
    assert by_sha[sha256_file(final)]["spec"]["source_path"] == str(final.resolve())
    assert by_sha[watched["sha256"]]["spec"]["source_path"] == str(Path(watched["path"]).resolve())
    assert _read(output / "editing_reference_v2.json")["methods"][0]["method_id"] == "method_0"
    assert _read(result["editing_evidence_path"])["render_sha256"] == result["final_sha256"]
    assert result["review"]["method_checks"][0]["audio_status"] == "not_applicable"


def test_completed_legacy_task_rejects_editing_v2_without_record_changes(inputs):
    from omni_story.library.state import LibraryStopped
    reference, library, output = inputs
    with _bridge(output, _fixture_responses(reference, library)) as requests:
        result = _execute(inputs)
        before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        with pytest.raises(LibraryStopped, match="editing_v2_cannot_reinterpret_existing_plans_or_reset_render_budget"):
            _execute_editing(inputs)
        after = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        assert after == before
        assert len(requests) == result["usage"]["requests"] == 10
        assert not (output / "editing_reference_v2.json").exists()


def test_editing_v2_missing_method_binding_gets_one_budgeted_protocol_repair(inputs):
    reference, library, output = inputs
    with _bridge(output, _editing_fixture_responses(reference, library, omit_first_binding=True)) as requests:
        result = _execute_editing(inputs)
    assert len(requests) == result["usage"]["requests"] == 12
    state = _read(output / "library_state.json")
    plan_call = next(call for call in state["calls"] if call["name"] == "plan_0")
    repair = next(call for call in state["calls"] if call["name"] == "plan_0_repair")
    assert repair["repair_of"] == plan_call["id"]
    assert plan_call["status"] == repair["status"] == "received"
    assert "editing_bindings" in _read(output / "calls" / plan_call["id"] / "protocol_failure.json")["error"]
    assert not (output / "calls" / plan_call["id"] / "parsed.json").exists()
    assert _read(output / "plan_0.json")["editing_bindings"][0]["method_id"] == "method_0"
    assert result["status"] == "model_checked_library_candidate"


def test_low_remaining_budget_reuses_completed_legacy_evidence_on_resume(inputs):
    reference, library, output = inputs
    def run_task():
        return execute(reference, library, output, span_s=3, frames=2, max_fine=1,
                       max_requests=16, asr=False)
    with _bridge(output, _fixture_responses(reference, library)) as requests:
        first = run_task()
        prior_requests = {str(path.relative_to(output)): path.read_bytes()
                          for path in (output / "calls").glob("*/request.json")}
        final_before = Path(first["final_video"]).read_bytes()
        assert first["usage"]["requests"] == 10 and first["usage"]["max_requests"] == 16
        resumed = run_task()
        assert resumed == first
        assert len(requests) == 10
        assert Path(resumed["final_video"]).read_bytes() == final_before
        assert {str(path.relative_to(output)): path.read_bytes()
                for path in (output / "calls").glob("*/request.json")} == prior_requests


def _semantic_fixture_responses(reference, library, *, unsupported=False, contradiction=False):
    from omni_story.library.semantic_audit import SEMANTIC_PROTOCOL
    base = _editing_fixture_responses(reference, library)
    def answer(job):
        name = job['job_id'].split('_', 2)[2]
        prompt = job['arguments']['prompt']
        if name.startswith('semantic_slice_'):
            value = json.loads(prompt.split('metadata：', 1)[1])
            value.update(characters=[{'character_id':'observed_1','appearance':'red geometric square'}],
                evidence=[{'evidence_id':'source_visible','kind':'visual_action','local_start_s':0,
                    'local_end_s':value['observed_duration_s'],'description':'red square visible on black field',
                    'character_ids':['observed_1'],'basis_evidence_ids':[]}], uncertainties=[])
            return value
        if name.startswith('semantic_claims_'):
            observation = json.loads(prompt.split('observation：', 1)[1].split('\nrequired_claims：', 1)[0])
            claims = json.loads(prompt.split('\nrequired_claims：', 1)[1].split('\nrole_hypotheses：', 1)[0])
            return {'protocol':SEMANTIC_PROTOCOL,'segment_id':observation['segment_id'],
                'observation_sha256':json_sha(observation), 'claim_checks':[
                    {'claim_id':c['claim_id'],'status':'unsupported' if unsupported and c['kind']=='visual_action' else 'supported',
                     'evidence_ids':[] if unsupported and c['kind']=='visual_action' else ['source_visible'],
                     'reason':'synthetic evidence comparison fixture',
                     'limitations':['fixture missing required action'] if unsupported and c['kind']=='visual_action' else []}
                    for c in claims], 'uncertainties':[]}
        if name.startswith('review_0'):
            value = base({**job,'job_id':job['job_id'].replace(name,'review_0')})
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
            blind = context['blind_reading']
            claim_rows = context['required_claims'] + [{'claim_id':e['claim_id'],'kind':e['kind']} for e in blind['evidence']]
            value.update(protocol=SEMANTIC_PROTOCOL,video_sha256=context['video_sha256'],
                visual_narrative_status='partial' if unsupported or contradiction else 'pass',
                fact_checks=[{'claim_id':c['claim_id'],
                    'status':'unsupported' if unsupported and c['kind']=='visual_action' and c['claim_id']!='blind_stable' else 'supported',
                    'evidence_refs':[{'segment_id':'segment_1','evidence_id':'source_visible'}],
                    'blind_evidence_ids':[], 'reason':'synthetic evidence comparison',
                    'limitations':['required action absent'] if unsupported and c['kind']=='visual_action' and c['claim_id']!='blind_stable' else []}
                    for c in claim_rows], contradictions=[])
            if unsupported or contradiction:
                value.update(theme_status='partial',editing_status='partial')
            if contradiction:
                value['contradictions']=[{'contradiction_id':'c1','claim_ids':[claim_rows[0]['claim_id'],'blind_stable'],
                    'status':'unresolved','reason':'synthetic contradictory reading', 'resolution':None,
                    'evidence_refs':[],'blind_evidence_ids':[]}]
                # First all-pass claim must get a single protocol repair.
                if name=='review_0':
                    value.update(visual_narrative_status='pass',theme_status='pass',editing_status='pass')
            return value
        value = base(job)
        if name.startswith('plan_0'):
            value['segments'][0]['visual_claims']=[{'claim_id':'vc_stable','kind':'visual_action',
                                                   'description':'square remains visible'}]
        if name=='blind_0':
            if '绑定：' in prompt:
                binding = json.loads(prompt.split('绑定：',1)[1])
            else:
                template = json.loads(prompt.split('完整JSON模板：',1)[1])
                binding = {key:template[key] for key in ('protocol','video_sha256')}
            value.update(**binding,text_dependency='assists')
            value['evidence'][0].update(evidence_id='blind_visible',claim_id='blind_stable',
                kind='visual_action',basis_evidence_ids=[])
        return value
    return answer


@pytest.mark.parametrize('unsupported,contradiction', [(False,False),(True,False),(False,True)])
def test_semantic_exact_slice_gate_real_media_and_resume(inputs, unsupported, contradiction):
    reference, library, output = inputs
    def run_task(*, flag=True):
        return execute(reference,library,output,span_s=3,frames=2,max_fine=1,max_requests=24,
                       asr=False,semantic_audit=flag)
    with _bridge(output,_semantic_fixture_responses(reference,library,unsupported=unsupported,
                                                    contradiction=contradiction)) as requests:
        result = run_task()
        count = len(requests)
        assert count==result['usage']['requests']==(14 if contradiction else 13)
        assert result['semantic_gate_passed'] is (not unsupported and not contradiction)
        assert result['status']==('model_checked_library_candidate' if result['semantic_gate_passed']
                                  else 'library_candidate_with_limitations')
        slice_job=next(job for job in requests if '_semantic_slice_' in job['job_id'])
        # Source cut 1.5..3.5, not the containing fine window 1..4.
        lineage=_read(Path(slice_job['arguments']['video_source']).parent/'lineage.json')
        assert (lineage['source_start_s'],lineage['source_end_s'])==(1.5,3.5)
        assert lineage['source_sha256']==sha256_file(library/'a.mkv')
        assert probe_media(lineage['path'])['duration_s']==pytest.approx(2,abs=.05)
        prompt=slice_job['arguments']['prompt']
        assert 'square remains visible' not in prompt
        assert 'intended_takeaway' not in prompt and 'slot_1' not in prompt
        assert 'red_square' not in prompt
        blind_job=next(job for job in requests if job['job_id'].endswith('_blind_0'))
        assert 'slot_1' not in blind_job['arguments']['prompt']
        review_job=next(job for job in requests if job['job_id'].endswith('_review_0'))
        review_prompt=review_job['arguments']['prompt']
        context=json.JSONDecoder().raw_decode(review_prompt[review_prompt.index('{"reference":'):])[0]
        assert 'plan' not in context and context['source_observations']
        before={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*')
                if p.is_file() and p.name!='current_status.json'}
        assert run_task(flag=False)==result  # Resume recovers policy without new CLI flag.
        assert len(requests)==count
        after={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*')
               if p.is_file() and p.name!='current_status.json'}
        assert all(after[name]==raw for name,raw in before.items())
        added=set(after)-set(before)
        assert len(added)==(1 if contradiction else 0)
        for name in added:
            path=Path(name)
            assert path.parts[:2]==('artifacts','cached_reply_validation')
            diagnostic=json.loads(after[name])
            call=next(c for c in _read(output/'library_state.json')['calls'] if c['name']=='review_0')
            failure=_read(output/'calls'/call['id']/'protocol_failure.json')
            assert diagnostic=={'stage':'review_0','call_id':call['id'],
                'request_sha256':call['request_sha256'],'response_sha256':call['response_sha256'],**failure}
            assert path.stem==json_sha(diagnostic)
        # Revalidating the known bad original may append a hash-bound diagnostic,
        # but never overwrites history, repeats its sole repair or grows on resume.
        assert run_task(flag=False)==result and len(requests)==count
        assert {str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*')
                if p.is_file() and p.name!='current_status.json'}==after
    assert Path(result['final_video']).is_file()
    assert len(list(output.glob('render_*/final.mp4')))==1


def test_semantic_cannot_reinterpret_completed_legacy_run(inputs):
    from omni_story.library.state import LibraryStopped
    reference,library,output=inputs
    with _bridge(output,_fixture_responses(reference,library)) as requests:
        _execute(inputs)
        before={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}
        with pytest.raises(LibraryStopped,match='semantic_audit_cannot_reinterpret_existing_plans_or_reset_budgets'):
            execute(reference,library,output,span_s=3,frames=2,max_fine=1,max_requests=24,
                    asr=False,semantic_audit=True)
        assert len(requests)==10
        after={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}
        assert after==before


def test_semantic_reserves_search_and_fine_repairs_and_recovers_low_budget_cap(inputs):
    reference,library,output=inputs
    base=_semantic_fixture_responses(reference,library)
    def reply(job):
        name=job['job_id'].split('_',2)[2]
        if name=='search_0_repair':
            return base({**job,'job_id':job['job_id'].replace(name,'search_0')})
        value=base(job)
        if name=='search_0':
            value['windows'][0]['end_s']=90  # Outside the real six-second fixture.
        if name.startswith('fine_') and not name.endswith('_repair'):
            value['events'][0]['local_end_s']=9
        return value
    def run_task():
        return execute(reference,library,output,span_s=3,frames=2,max_fine=2,max_requests=22,
                       asr=False,semantic_audit=True)
    with _bridge(output,reply) as requests:
        result=run_task()
        assert result['usage']['requests']==len(requests)==15
        state=_read(output/'library_state.json')
        search_budget=_read(state['artifacts']['semantic_search_budget_0'][0]['path'])
        assert search_budget['remaining_at_allocation']==16 and search_budget['window_cap']==1
        plan_budget=_read(state['artifacts']['semantic_plan_budget_0'][0]['path'])
        assert plan_budget['remaining_at_allocation']==12 and plan_budget['max_segments']==1
        assert result['semantic_gate_passed'] is True
        assert run_task()==result
        assert len(requests)==15


def test_semantic_one_slot_can_render_disjoint_microclips_at_different_speeds(inputs):
    from copy import deepcopy
    reference,library,output=inputs
    base=_semantic_fixture_responses(reference,library)
    def answer(job):
        value=base(job)
        name=job['job_id'].split('_',2)[2]
        if name.startswith('plan_0'):
            first=value['segments'][0]
            first.pop('caption')
            first.pop('freeze_tail_s')
            second=deepcopy(first)
            first.update(source_in_s=1.5,source_out_s=2,speed=2)
            second.update(segment_id='segment_2',source_in_s=3,source_out_s=3.5,speed=.5,
                visual_claims=[{'claim_id':'vc_second','kind':'visual_action','description':'square stays visible'}])
            value['segments'].append(second)
            value['slots'][0]['segment_ids'].append('segment_2')
            binding=value['editing_bindings'][0]
            binding.update(segment_ids=['segment_1','segment_2'],
                operation='omit the middle source interval; first 2x, second 0.5x for synthetic retiming verification',
                verification='check two source intervals and output duration, not creative quality')
        if name=='review_0':
            value['method_checks'][0]['output_evidence'][0].update(start_s=0,end_s=1.25,
                observed_fact='two synthetic source ranges use different speeds')
        return value
    with _bridge(output,answer) as requests:
        result=execute(reference,library,output,span_s=3,frames=2,max_fine=1,max_requests=28,
                       asr=False,semantic_audit=True)
        assert result['usage']['requests']==len(requests)==15
    rendered=_read(output/'render_0'/'render_result.json')
    assert len(rendered['provenance'])==2
    assert [(s['source_in_s'],s['source_out_s']) for s in rendered['provenance']]==[(1.5,2),(3,3.5)]
    assert rendered['duration_s']==pytest.approx(1.266666667,abs=.01)  # 0.25 rounds to eight frames at 30fps.
    assert probe_media(result['final_video'])['duration_s']==pytest.approx(1.266666667,abs=.05)
    evidence=_read(result['semantic_evidence_path'])
    assert {s['segment_id'] for s in evidence['observations']}=={'segment_1','segment_2'}
