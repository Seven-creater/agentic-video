"""Keep installed imports, runtime resources and historical code keys intact."""
from pathlib import Path

import pytest

import omni_story
import omni_story.library as library_package
from omni_story import pipeline
from omni_story.library.reference_craft import load_knowledge


def test_editable_package_comes_from_src():
    repository = Path(__file__).resolve().parents[1]
    package = Path(omni_story.__file__).resolve().parent
    assert package == repository / "src" / "omni_story"
    assert not (repository / "omni_story").exists()


def test_library_runtime_resources_are_installed_with_package():
    library = Path(library_package.__file__).resolve().parent
    for name in ("mcp_bridge.mjs", "mcp_guard.mjs", "mcp_visual_story_guard.mjs"):
        assert (library / name).is_file()
        assert (library / name).stat().st_size > 0
    knowledge = load_knowledge()
    assert knowledge["cards"]
    assert all(knowledge["cards"].values())
    assert "SKILL.md" in knowledge["files"]
    assert "catalogue.json" in knowledge["files"]
    assert all(len(digest) == 64 for digest in knowledge["files"].values())


def test_code_snapshot_preserves_legacy_keys_and_hashes_real_sources():
    package = Path(omni_story.__file__).resolve().parent
    sources = sorted(package.glob("*.py"))
    assert sources
    expected = {
        str(Path("omni_story") / source.name): pipeline.sha(source)
        for source in sources
    }
    assert pipeline.code_snapshot() == expected


@pytest.mark.parametrize("repository_file,package_file", [
    ("craft_knowledge/GLM_MICROCLIP_FINECUT.md", "craft_knowledge/GLM_MICROCLIP_FINECUT.md"),
    ("craft_knowledge/PARENT_FINECUT.md", "craft_knowledge/PARENT_FINECUT.md"),
    ("craft_knowledge/GLM_SLOT_MICROCLIP_V2.md", "craft_knowledge/GLM_SLOT_MICROCLIP_V2.md"),
    ("craft_knowledge/GLM_BOUNDARY_NAVIGATION_V1.md", "craft_knowledge/GLM_BOUNDARY_NAVIGATION_V1.md"),
    ("skills/visual-story-finecut/SKILL.md", "resources/visual_story_finecut/SKILL.md"),
    ("skills/visual-story-finecut/references/decision-cards.md",
     "resources/visual_story_finecut/references/decision-cards.md"),
])
def test_runtime_resource_copies_preserve_original_bytes(repository_file, package_file):
    repository = Path(__file__).resolve().parents[1]
    library = Path(library_package.__file__).resolve().parent
    source = (repository / repository_file).read_bytes()
    assert source
    assert (library / package_file).read_bytes() == source
