"""Synthetic dense-frame transport checks; these are not model quality verdicts."""
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from omni_story.library import dense_source_frames as dense, semantic_pipeline, semantic_prompts
from omni_story.library.media import prepare_window, sha256_file
from omni_story.library.state import LibraryStopped
from test_library_semantic_audit import comparison, slice_data, SOURCE_SHA


requires_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="Requires FFmpeg for tiny synthetic media only",
)


@pytest.fixture(scope="module")
def synthetic_source(tmp_path_factory):
    folder = tmp_path_factory.mktemp("dense_source_frames")
    path = folder / "synthetic.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=160x90:rate=30:duration=9", "-c:v", "libx264", "-preset",
        "ultrafast", "-pix_fmt", "yuv420p", "-an", str(path),
    ], check=True, capture_output=True, timeout=30)
    return folder, {"source_id": "synthetic_movie", "path": str(path),
                    "sha256": sha256_file(path), "duration_s": 9,
                    "video_stream_index": 0, "audio_stream_index": None}


def synthetic_segment(source, start, end):
    return {"segment_id": "synthetic_segment", "source_id": source["source_id"],
            "source_in_s": start, "source_out_s": end,
            "slot_id": "HIDDEN_SLOT_ANSWER", "window_id": "synthetic_window",
            "visual_claims": [{"claim_id": "hidden_claim", "kind": "visual_action",
                               "description": "HIDDEN_DESIRED_ACTION"}],
            "caption": {"text": "HIDDEN_CAPTION_ANSWER"},
            "reason": "HIDDEN_THEME_ANSWER"}


@pytest.fixture(scope="module")
def dense_fixture(synthetic_source):
    folder, source = synthetic_source
    segment = synthetic_segment(source, 1, 2.1)
    proxy = prepare_window(source, 1, 2.1, folder / "cache", fps=30)
    result = dense.make_media(segment, source, proxy)
    return segment, source, proxy, result


def read_manifest(result):
    return json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))


def snapshot(folder):
    return {path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in Path(folder).rglob("*") if path.is_file()}


@requires_ffmpeg
def test_actual_proxy_frames_are_uniform_bound_and_transport_sized(dense_fixture):
    from PIL import Image

    segment, source, proxy, result = dense_fixture
    folder = Path(proxy["path"]).parent / "dense_frames"
    manifest = read_manifest(result)
    assert Path(result["path"]) == folder / "sheet.jpg"
    assert Path(result["manifest_path"]) == folder / "manifest.json"
    assert result["image"] is True
    assert result["sha256"] == sha256_file(result["path"])
    assert result["manifest_sha256"] == sha256_file(result["manifest_path"])
    assert manifest["normal_proxy"] == proxy
    assert manifest["policy"]
    assert not (folder / "lineage.json").exists()

    frames = manifest["frames"]
    assert len(frames) == min(36, math.ceil(proxy["duration_s"] * 6))
    assert len({frame["frame_id"] for frame in frames}) == len(frames)
    times = [frame["local_time_s"] for frame in frames]
    assert times == sorted(set(times))
    assert all(0 <= time < proxy["duration_s"] for time in times)
    if len(times) > 2:
        gaps = [b - a for a, b in zip(times, times[1:])]
        # Uniform target times are quantized to actual 30 fps frame indices.
        assert max(gaps) - min(gaps) <= 1 / 30 + 1e-9
    for frame in frames:
        assert Path(frame["path"]).parent == folder
        assert Path(frame["path"]).name.startswith("frame")
        assert frame["sha256"] == sha256_file(frame["path"])
        assert frame["source_time_s"] == pytest.approx(segment["source_in_s"] + frame["local_time_s"])
        with Image.open(frame["path"]) as image:
            assert image.width <= 480
    with Image.open(result["path"]) as image:
        assert image.width == 4 * 480
        assert max(image.size) <= 6000
    assert Path(result["path"]).stat().st_size < 5_000_000
    assert source["sha256"] == proxy["source_sha256"]


@requires_ffmpeg
@pytest.mark.parametrize("duration", [.2, 7])
def test_frame_count_handles_subsecond_and_cap_without_assuming_full_frame_viewing(
        synthetic_source, duration):
    folder, source = synthetic_source
    start, end = .5, .5 + duration
    segment = synthetic_segment(source, start, end)
    proxy = prepare_window(source, start, end, folder / "cache", fps=30)
    result = dense.make_media(segment, source, proxy)
    assert len(read_manifest(result)["frames"]) == min(36, math.ceil(proxy["duration_s"] * 6))


@requires_ffmpeg
def test_prompt_binds_actual_image_and_frames_without_desired_claims(dense_fixture):
    segment, source, proxy, result = dense_fixture
    before = deepcopy((segment, source, proxy))
    prompt = result["prompt"]
    metadata = json.dumps(result["prompt_metadata"], ensure_ascii=False)
    manifest = read_manifest(result)
    assert result["prompt_metadata"]["continuous"] is False
    assert result["prompt_metadata"]["audio_present"] is False
    assert "not continuous playback" in manifest["limitations"]
    assert "Unsampled action, absence" in manifest["limitations"]
    for digest in (proxy["sha256"], result["sha256"], result["manifest_sha256"]):
        assert digest in metadata and digest in prompt
    for frame in manifest["frames"]:
        assert frame["sha256"] in metadata and frame["frame_id"] in metadata
        assert frame["sha256"] in prompt and frame["frame_id"] in prompt
        assert str(frame["local_time_s"]) in metadata
        assert str(frame["source_time_s"]) in metadata
    for hidden in ("HIDDEN_SLOT_ANSWER", "HIDDEN_DESIRED_ACTION", "HIDDEN_CAPTION_ANSWER", "HIDDEN_THEME_ANSWER"):
        assert hidden not in prompt and hidden not in metadata
    assert dense.prompt(segment, source, proxy) == prompt
    assert (segment, source, proxy) == before


@requires_ffmpeg
def test_extraction_decodes_normal_proxy_and_draws_local_frame_labels(synthetic_source, monkeypatch):
    from PIL import ImageDraw

    folder, source = synthetic_source
    segment = synthetic_segment(source, 3, 3.5)
    proxy = prepare_window(source, 3, 3.5, folder / "cache", fps=30)
    original_proxy = Path(proxy["path"]).read_bytes()
    original_lineage = (Path(proxy["path"]).parent / "lineage.json").read_bytes()
    run, draw = dense._run, ImageDraw.ImageDraw.text
    commands, labels = [], []

    def record_run(args, **kwargs):
        commands.append(list(args))
        return run(args, **kwargs)

    def record_draw(self, xy, text, *args, **kwargs):
        labels.append(text)
        return draw(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(dense, "_run", record_run)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_draw)
    result = dense.make_media(segment, source, proxy)
    frames = read_manifest(result)["frames"]
    assert len(commands) == len(labels) == len(frames)
    for command, label, frame in zip(commands, labels, frames):
        assert command[command.index("-i") + 1] == proxy["path"]
        assert "select=eq(n" in command[command.index("-vf") + 1]
        assert frame["frame_id"] in label
        assert f"local {frame['local_time_s']:.3f}s" in label
    assert Path(proxy["path"]).read_bytes() == original_proxy
    assert (Path(proxy["path"]).parent / "lineage.json").read_bytes() == original_lineage


@requires_ffmpeg
def test_reuse_and_saved_validation_are_read_only_without_extraction(dense_fixture, monkeypatch):
    segment, source, proxy, result = dense_fixture
    folder = Path(proxy["path"]).parent
    before = snapshot(folder)

    def forbidden_run(*args, **kwargs):
        pytest.fail("A complete dense-frame cache must not execute FFmpeg again.")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    saved = dense.saved_media(proxy)
    assert saved["path"] == result["path"]
    assert saved["sha256"] == result["sha256"]
    assert saved["manifest_sha256"] == result["manifest_sha256"]
    assert dense.make_media(segment, source, proxy) == result
    assert dense.prompt(segment, source, proxy) == result["prompt"]
    assert snapshot(folder) == before


@requires_ffmpeg
@pytest.mark.parametrize("target", ["manifest", "image", "frame", "proxy"])
def test_saved_media_rejects_hash_mutation_and_never_repairs_in_place(dense_fixture, target):
    _, _, proxy, result = dense_fixture
    manifest = read_manifest(result)
    paths = {"manifest": result["manifest_path"], "image": result["path"],
             "frame": manifest["frames"][0]["path"], "proxy": proxy["path"]}
    path = Path(paths[target])
    original = path.read_bytes()
    if target == "manifest":
        changed = deepcopy(manifest)
        changed["policy"] = "tampered_synthetic_policy"
        tampered = json.dumps(changed).encode("utf-8")
    else:
        tampered = original + b"\nsynthetic hash mutation"
    path.write_bytes(tampered)
    before = snapshot(Path(proxy["path"]).parent)
    try:
        with pytest.raises((ValueError, LibraryStopped)):
            dense.saved_media(proxy)
        assert snapshot(Path(proxy["path"]).parent) == before
    finally:
        path.write_bytes(original)
    assert dense.saved_media(proxy)["sha256"] == result["sha256"]


def test_observation_media_uses_image_but_keeps_original_scope_and_claim_video(
        tmp_path, slice_data, monkeypatch):
    plan, segment, proxy, observation = slice_data
    proxy.update(path=str(tmp_path / "normal.mp4"), kind="continuous_window")
    source = {"source_id": segment["source_id"], "sha256": SOURCE_SHA}
    image = {"path": str(tmp_path / "sheet.jpg"), "image": True,
             "prompt": "Independent sampled frames; no creative answer.", "sha256": "d" * 64,
             "manifest_path": str(tmp_path / "manifest.json"), "manifest_sha256": "e" * 64,
             "prompt_metadata": {"normal_proxy_sha256": proxy["sha256"]}}
    monkeypatch.setattr(semantic_pipeline, "prepare_window", lambda *args, **kwargs: deepcopy(proxy))
    callbacks, calls = [], []
    _, check = comparison(plan, segment, observation)

    def observation_media(s, source_record, normal):
        callbacks.append(deepcopy((s, source_record, normal)))
        return image

    class FakeGLM:
        def call(self, name, prompt, media, validator, **kwargs):
            calls.append((name, prompt, media, kwargs))
            value = deepcopy(observation if name.startswith("semantic_slice_") else check)
            assert validator(value) is value
            return value

    manifest = semantic_pipeline.observe_selected_slices(
        FakeGLM(), plan, {segment["source_id"]: source},
        [{"window_id": segment["window_id"], "observation": {"roles": []}}],
        tmp_path / "cache", tmp_path / "out", 10, observation_media=observation_media)
    assert callbacks == [(segment, source, proxy)]
    fact, claims = calls
    assert fact[1:3] == (image["prompt"], image["path"])
    assert fact[3]["image"] is True
    assert fact[3]["scope"] == {k: proxy[k] for k in
                                ("kind", "source_sha256", "source_start_s", "source_end_s")}
    assert fact[3]["scope"]["kind"] == "continuous_window"
    assert claims[2] == proxy["path"] and not claims[3].get("image", False)
    assert claims[1] == semantic_prompts.slice_claim_prompt(observation,
        comparison(plan, segment, observation)[0], [])
    assert manifest["observations"] == [observation]
    assert manifest["segment_checks"] == [check]


@pytest.mark.parametrize("round_no", [4, 8, 9])
def test_dense_hook_rejects_old_round_before_preparing_media(tmp_path, slice_data, monkeypatch, round_no):
    plan, segment, _, _ = slice_data

    def no_old_media(*args, **kwargs):
        pytest.fail("The new image transport must not reinterpret an old round.")

    monkeypatch.setattr(semantic_pipeline, "prepare_window", no_old_media)
    with pytest.raises((ValueError, LibraryStopped)):
        semantic_pipeline.observe_selected_slices(
            None, plan, {segment["source_id"]: {"sha256": SOURCE_SHA}}, [],
            tmp_path / "cache", tmp_path / "out", round_no, observation_media=no_old_media)


@pytest.mark.parametrize("round_no", [4, 9, 10])
def test_default_fact_transport_retains_exact_legacy_arguments(tmp_path, slice_data, monkeypatch, round_no):
    plan, segment, proxy, observation = slice_data
    proxy.update(path=str(tmp_path / "normal.mp4"), kind="continuous_window")
    source = {"source_id": segment["source_id"], "sha256": SOURCE_SHA}
    monkeypatch.setattr(semantic_pipeline, "prepare_window", lambda *args, **kwargs: deepcopy(proxy))
    calls = []
    _, check = comparison(plan, segment, observation)

    class FakeGLM:
        def call(self, name, prompt, media, validator, **kwargs):
            calls.append((name, prompt, media, kwargs))
            value = deepcopy(observation if name.startswith("semantic_slice_") else check)
            assert validator(value) is value
            return value

    semantic_pipeline.observe_selected_slices(FakeGLM(), plan, {segment["source_id"]: source},
        [{"window_id": segment["window_id"], "observation": {"roles": []}}],
        tmp_path / "cache", tmp_path / "out", round_no)
    assert calls[0][1] == semantic_prompts.slice_observation_prompt(segment, source, proxy)
    assert calls[0][2] == proxy["path"]
    assert calls[0][3] == {"scope": {k: proxy[k] for k in
                                   ("kind", "source_sha256", "source_start_s", "source_end_s")}}
    assert calls[1][2:] == (proxy["path"], {})
