"""Newest authorization selects its adapter before the frozen historical one."""
from types import SimpleNamespace

import pytest

from omni_story.library import mcp_launch, microclip_state, microclip_v2_state
from omni_story.library.state import write_json


@pytest.mark.parametrize("reject", [False, True])
def test_corrected_slot_launcher_cleans_old_auth_and_preserves_stop_on_rejection(tmp_path, monkeypatch, reject):
    output = tmp_path / "task"
    write_json(output / "library_state.json", {"artifacts": {
        microclip_v2_state.ARTIFACT: [{"path": "new"}], microclip_state.ARTIFACT: [{"path": "old"}]}})
    marker = output / "mcp_stop"
    marker.write_text("stopped", encoding="utf-8")
    checks = []
    def state(path):
        checks.append(path)
        if reject:
            raise ValueError("synthetic_invalid_v2")
        return SimpleNamespace()
    monkeypatch.setattr(microclip_v2_state, "SlotMicroclipState", state)
    monkeypatch.setattr(microclip_v2_state, "get_auth", lambda path: {
        "authorization_path": "validated_v2", "authorization_sha256": "new-byte-sha"})
    monkeypatch.setattr(microclip_state, "MicroclipState", lambda path: pytest.fail("frozen v1 adapter used"))
    monkeypatch.setattr("sys.argv", ["mcp_launch", "--output", str(output), "--package-root", str(tmp_path / "package")])
    monkeypatch.setenv("Z_AI_API_KEY", "synthetic-test-key")
    for name in ("OMNI_LIBRARY_MICROCLIP_AUTH_FILE", "OMNI_LIBRARY_PARENT_CUT_AUTH_FILE",
                 "OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE", "OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE", "OMNI_LIBRARY_MAX_REQUESTS"):
        monkeypatch.setenv(name, "inherited-invalid")
    children = []
    monkeypatch.setattr(mcp_launch.subprocess, "call", lambda command, env: children.append((command, dict(env))) or 17)
    if reject:
        with pytest.raises(ValueError, match="synthetic_invalid_v2"):
            mcp_launch.main()
        assert marker.exists() and not children
    else:
        assert mcp_launch.main() == 17
        assert not marker.exists()
        command, env = children[0]
        assert env["OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE"] == "validated_v2"
        assert env["OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256"] == "new-byte-sha"
        assert env["OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY"] == "1"
        assert all(name not in env for name in ("OMNI_LIBRARY_MICROCLIP_AUTH_FILE", "OMNI_LIBRARY_PARENT_CUT_AUTH_FILE",
                                              "OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE", "OMNI_LIBRARY_MAX_REQUESTS"))
        assert "synthetic-test-key" not in " ".join(command)
    assert checks == [output]
