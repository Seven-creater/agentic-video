"""V2 CPU proof reuse never exempts changed bytes or decoded PTS from checks."""
from copy import deepcopy
from pathlib import Path

import pytest

from omni_story.library import microclip_v2_state as mc, microclip_frames
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json


@pytest.fixture
def grid(tmp_path, monkeypatch):
    source, image, png = tmp_path / "parent.mp4", tmp_path / "grid.png", tmp_path / "f0.png"
    source.write_bytes(b"immutable actual source")
    image.write_bytes(b"immutable grid")
    png.write_bytes(b"immutable decoded RGB/PNG")
    manifest = tmp_path / "manifest.json"
    original = {"grid": {"path": str(image), "sha256": sha256_file(image)},
                "source": {"sha256": sha256_file(source)},
                "request": {"start_s": 0.0, "end_s": 1.0},
                "frames": [{"frame_id": "f0", "pts": 0, "source_time_s": 0.0,
                            "png_path": str(png), "png_sha256": sha256_file(png)}]}
    write_json(manifest, original)
    manifest.with_name("manifest.sha256").write_text(sha256_file(manifest), encoding="ascii")
    calls = []
    def verify(path, *, source_path):
        calls.append((path, source_path))
        value = mc._read(path)
        if (sha256_file(source_path) != original["source"]["sha256"]
                or sha256_file(value["grid"]["path"]) != original["grid"]["sha256"]
                or value["frames"] != original["frames"]
                or sha256_file(png) != original["frames"][0]["png_sha256"]
                or value["request"] != original["request"]):
            raise ValueError("changed actual source/frame/PTS/request")
        return deepcopy(value)
    monkeypatch.setattr(microclip_frames, "verify_grid", verify)
    monkeypatch.setattr(mc, "_VERIFIED_GRIDS", {})
    return manifest, source, original, calls


def test_identical_bound_inputs_skip_redecode_and_force_revalidates(grid):
    manifest, source, _, calls = grid
    first = mc._verify_grid(manifest, source_path=source)
    first["frames"][0]["pts"] = "caller mutation must not affect cached proof"
    second = mc._verify_grid(manifest, source_path=source)
    assert second["frames"][0]["pts"] == 0 and len(calls) == 1
    mc._verify_grid(manifest, source_path=source, force=True)
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["png", "source", "grid", "pts", "request"])
def test_changed_actual_bytes_and_time_cannot_use_old_cached_proof(grid, change):
    manifest, source, original, calls = grid
    mc._verify_grid(manifest, source_path=source)
    changed = deepcopy(original)
    if change in {"png", "source", "grid"}:
        path = {"source": source, "grid": Path(original["grid"]["path"]),
                "png": Path(original["frames"][0]["png_path"])}[change]
        path.write_bytes(b"changed actual media bytes")
    else:
        if change == "pts":
            changed["frames"][0]["pts"] = 1
        else:
            changed["request"]["end_s"] = 2.0
        write_json(manifest, changed)
        manifest.with_name("manifest.sha256").write_text(sha256_file(manifest), encoding="ascii")
    with pytest.raises(ValueError, match="changed actual"):
        mc._verify_grid(manifest, source_path=source)
    assert len(calls) == 2
