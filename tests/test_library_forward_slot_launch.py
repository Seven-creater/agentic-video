"""Forward launcher selection happens before the historical stopped adapter."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import mcp_launch, forward_slot_budget, slot_finecut_budget
from omni_story.library.state import write_json


@pytest.mark.parametrize("reject", [False, True])
def test_launcher_uses_new_policy_and_cleans_inherited_provider_env(tmp_path, monkeypatch, reject):
    output = tmp_path / "existing_task"
    write_json(output / "library_state.json", {"artifacts": {
        forward_slot_budget.ARTIFACT: [{"path": "validated_new_policy"}],
        "slot_finecut_comparison_v1": [{"path": "old_stopped_policy"}]}})
    marker = output / "mcp_stop"
    marker.write_text("Existing stopped connection", encoding="utf-8")
    calls = []
    def state(path):
        calls.append(path)
        if reject:
            raise ValueError("synthetic_bad_forward_policy")
        return SimpleNamespace()
    monkeypatch.setattr(forward_slot_budget, "ForwardSlotState", state)
    monkeypatch.setattr(forward_slot_budget, "get_auth", lambda path: {
        "authorization_path": "validated_new_policy", "authorization_sha256": "new-byte-sha"})
    monkeypatch.setattr(slot_finecut_budget, "SlotFinecutState", lambda path: pytest.fail("old stopped adapter selected"))
    monkeypatch.setattr("sys.argv", ["mcp_launch", "--output", str(output), "--package-root", str(tmp_path / "package")])
    monkeypatch.setenv("Z_AI_API_KEY", "synthetic-secret-not-an-account-key")
    for name in ("OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE", "OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE",
                 "OMNI_LIBRARY_EXTENSION_AUTH_FILE", "OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE", "OMNI_LIBRARY_MAX_REQUESTS"):
        monkeypatch.setenv(name, "poisoned inherited value")
    children = []
    monkeypatch.setattr(mcp_launch.subprocess, "call", lambda command, env: children.append((command, dict(env))) or 13)
    if reject:
        with pytest.raises(ValueError, match="synthetic_bad_forward_policy"):
            mcp_launch.main()
        assert marker.exists() and not children
    else:
        assert mcp_launch.main() == 13
        assert not marker.exists()
        command, env = children[0]
        assert env["OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE"] == "validated_new_policy"
        assert env["OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256"] == "new-byte-sha"
        assert env["OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY"] == "2"
        assert all(name not in env for name in ("OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE",
            "OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE", "OMNI_LIBRARY_EXTENSION_AUTH_FILE", "OMNI_LIBRARY_MAX_REQUESTS"))
        assert "synthetic-secret" not in " ".join(command)
    assert calls == [output]
