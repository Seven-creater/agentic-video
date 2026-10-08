"""Check installed resources and CLI help without running models or media jobs."""
from __future__ import annotations

import argparse
from importlib.metadata import distribution
import json
from pathlib import Path
import subprocess
import sys
import sysconfig

import omni_story
import omni_story.library as library
from omni_story.pipeline import code_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-wheel", action="store_true")
    args = parser.parse_args()
    package = Path(omni_story.__file__).resolve().parent
    metadata = distribution("omni-autonomous-screenplay")
    direct_url = json.loads(metadata.read_text("direct_url.json") or "{}")
    if args.expect_wheel and direct_url.get("dir_info", {}).get("editable"):
        raise RuntimeError("Expected a wheel installation, but imported editable sources")
    resource_root = Path(library.__file__).resolve().parent
    resources = [
        "mcp_bridge.mjs", "mcp_guard.mjs", "mcp_visual_story_guard.mjs",
        "craft_knowledge/SKILL.md", "craft_knowledge/catalogue.json",
        "craft_knowledge/GLM_MICROCLIP_FINECUT.md", "craft_knowledge/PARENT_FINECUT.md",
        "craft_knowledge/GLM_SLOT_MICROCLIP_V2.md",
        "craft_knowledge/GLM_BOUNDARY_NAVIGATION_V1.md",
        "resources/visual_story_finecut/SKILL.md",
        "resources/visual_story_finecut/references/decision-cards.md",
    ]
    for name in resources:
        if not (resource_root / name).is_file() or not (resource_root / name).stat().st_size:
            raise RuntimeError(f"Missing installed resource: {name}")
    if not code_snapshot():
        raise RuntimeError("Source fingerprint is empty")
    for module in ("omni_story", "omni_story.discovery", "omni_story.library"):
        subprocess.run([sys.executable, "-I", "-m", module, "--help"],
                       check=True, capture_output=True, text=True, timeout=30)
    scripts = Path(sysconfig.get_path("scripts"))
    suffix = ".exe" if sys.platform == "win32" else ""
    for name in ("omni-story", "omni-discover", "omni-library"):
        subprocess.run([str(scripts / (name + suffix)), "--help"],
                       check=True, capture_output=True, text=True, timeout=30)
    print(f"Installation OK: {package}; {len(resources)} resources; 3 CLI help checks")


if __name__ == "__main__":
    main()
