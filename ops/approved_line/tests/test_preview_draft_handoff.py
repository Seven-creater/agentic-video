"""The creative worker hands off a reviewed draft without a stop or a TTS request."""
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_story.library import joint_dialogue, server_cli, server_jobs
from omni_story.library.media import sha256_file
from omni_story.library.state import write_json
from omni_story.library.story_dialogue import StoryDialogue


def test_reviewed_script_is_terminal_for_draft_worker(tmp_path, monkeypatch):
    state = Mock()
    state.data = {'calls': []}
    state.usage.return_value = {'requests': 0}
    service = SimpleNamespace(voices={'顾客': 'voice'})
    story = StoryDialogue.__new__(StoryDialogue)
    story.route = SimpleNamespace(reference={})
    story.state, story.glm, story.voice_service, story.shots = state, None, service, []
    lock = {'scenes': [], 'cast_bindings': {}, 'reference_contract': {'policy': 'test'}}
    story.lock = lambda: ([], lock)
    story.plan_adaptation = lambda lock: lock.update(adapted=True)
    script = {'lines': [{'line_id': 'one', 'text': '多少钱？'}]}
    monkeypatch.setattr(joint_dialogue, 'write_script', lambda *args: script)
    monkeypatch.setattr(joint_dialogue, 'adapt_to_picture', lambda value, *args: value)
    story.review_script = Mock(side_effect=lambda value, *args: value)
    monkeypatch.setattr(server_cli, '_run_task', lambda args: story.prepare(tmp_path, tmp_path, draft_only=True))
    result = server_cli._run(SimpleNamespace(output=tmp_path))
    story.review_script.assert_called_once()
    assert result['status'] == 'preview_draft_ready'
    assert json.loads((tmp_path/'material_lock.json').read_text())['adapted']
    assert json.loads((tmp_path/'dialogue_script.json').read_text()) == script
    outcome = json.loads((tmp_path/'run_outcome.json').read_text())
    assert outcome['outcome'] == 'completed' and outcome['final_video'] is None
    assert Path(outcome['checkpoint_path']).is_file()
    assert not (tmp_path/'mcp_stop').exists()


@pytest.mark.parametrize('fault', [None, 'worker', 'unsettled'])
def test_controller_starts_preview_only_on_settled_handoff(tmp_path, monkeypatch, fault):
    root = tmp_path
    creative = root/'creative'
    ref = root/'reference.mp4'
    ref.write_bytes(b'reference')
    write_json(root/'plan.json', dict(reference=str(ref), reference_sha256=sha256_file(ref),
        python='python', entry_path='entry.py', preview_runner='preview.py', release=str(root),
        preview_draft_only=True, asr_model='test'))
    for name, data in [('dialogue_script.json', {'facts': [], 'lines': ['one']}),
                       ('material_lock.json', {}), ('watched_windows.json', []),
                       ('autonomous_material/character_voices/cast_voices.json', {}),
                       ('library_state.json', {'calls': [{'id': '1', 'status': 'submitted' if fault == 'unsettled' else 'received'}]}),
                       ('run_outcome.json', {'status': 'preview_draft_ready', 'outcome': 'completed'})]:
        write_json(creative/name, data)
    def launch(command, **kwargs):
        assert '--preview-draft-only' in command
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(subprocess, 'run', launch)
    monkeypatch.setattr(server_jobs, 'status', lambda path: {'state': 'failed' if fault=='worker' else 'succeeded'})
    monkeypatch.setattr(server_jobs, '_group_running', lambda pid: False)
    def preview(command, **kwargs):
        assert fault is None
        write_json(Path(command[-1])/'dialogue_result.json', {'final_video':str(ref), 'final_sha256':sha256_file(ref)})
        return SimpleNamespace(poll=lambda: 0, returncode=0)
    monkeypatch.setattr(subprocess, 'Popen', preview)
    monkeypatch.setenv('OMNI_WORKER_OUTPUT', str(creative))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(sys, 'argv', ['controller', str(root)])
    controller = Path(__file__).with_name('fast_template_controller.py')
    if not controller.exists():
        controller = controller.parent.parent/controller.name
    if fault:
        with pytest.raises(AssertionError, match='creative_handoff_failed|creative_calls_require_reconciliation'):
            runpy.run_path(str(controller))
    else:
        runpy.run_path(str(controller))
    status = json.loads((root/'fast_run.json').read_text())
    assert status['state'] == ('needs_attention' if fault else 'completed')
    assert fault or status['stage'] == 'completed'
    assert not (creative/'mcp_stop').exists()
