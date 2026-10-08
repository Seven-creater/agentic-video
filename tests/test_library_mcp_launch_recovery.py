"""Launcher passes only a validated, byte-bound frozen-job policy to the bridge."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import mcp_launch, slot_finecut_budget, independent_slot_recovery


def setup_launcher(tmp_path, monkeypatch, *, reject=False):
    output = tmp_path / 'task'
    output.mkdir()
    policy = output / 'recovery.json'
    policy.write_text('{"policy":"synthetic_bound_recovery"}', encoding='utf-8')
    recorded = {'artifacts': {'slot_finecut_comparison_v1': [{}], 'sf_parallel_execution_v1': [{}],
                             'sf_independent_slot_recovery_v1': [{'path': str(policy)}]}}
    (output / 'library_state.json').write_text(json.dumps(recorded), encoding='utf-8')
    marker = output / 'mcp_stop'
    marker.write_text('old stop preserved until validation succeeds', encoding='utf-8')
    grant = {'authorization_path': 'synthetic_authorization', 'authorization_sha256': 'bound-sha'}
    monkeypatch.setattr(slot_finecut_budget, 'SlotFinecutState', lambda path: SimpleNamespace(data=recorded))
    monkeypatch.setattr(slot_finecut_budget, 'load_authorization', lambda path: grant)
    validations = []
    def validate(path, data, authorization):
        validations.append((path, data, authorization))
        if reject:
            raise ValueError('synthetic_changed_recovery')
        return {'validated': True}
    monkeypatch.setattr(independent_slot_recovery, 'read_validate', validate)
    monkeypatch.setattr('sys.argv', ['mcp_launch', '--output', str(output), '--package-root', str(tmp_path / 'package')])
    monkeypatch.setenv('Z_AI_API_KEY', 'synthetic-launch-secret')
    monkeypatch.setenv('OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE', 'poisoned-inherited-policy')
    monkeypatch.setenv('OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256', 'poisoned-inherited-sha')
    monkeypatch.setattr(mcp_launch.getpass, 'getpass', lambda *args: pytest.fail('unexpected credential prompt'))
    children = []
    monkeypatch.setattr(mcp_launch.subprocess, 'call', lambda command, env: children.append((command, dict(env))) or 17)
    return output, policy, grant, validations, children


def test_launcher_validates_recovery_and_replaces_inherited_transport_policy(tmp_path, monkeypatch):
    output, policy, grant, validations, children = setup_launcher(tmp_path, monkeypatch)
    assert mcp_launch.main() == 17
    assert len(validations) == 1 and validations[0][0] == output and validations[0][2] == grant
    assert not (output / 'mcp_stop').exists()
    command, env = children[0]
    assert env['OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE'] == str(policy)
    assert env['OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256'] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert env['OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY'] == '2'
    assert 'OMNI_LIBRARY_MAX_REQUESTS' not in env
    assert env['Z_AI_API_KEY'] == 'synthetic-launch-secret'
    assert 'synthetic-launch-secret' not in ' '.join(command)


def test_launcher_rejects_changed_recovery_before_removing_stop_or_connecting(tmp_path, monkeypatch):
    output, _, _, validations, children = setup_launcher(tmp_path, monkeypatch, reject=True)
    with pytest.raises(ValueError, match='synthetic_changed_recovery'):
        mcp_launch.main()
    assert len(validations) == 1 and not children
    assert (output / 'mcp_stop').read_text() == 'old stop preserved until validation succeeds'
