"""The bun-built opencode binary must keep its per-run temp files inside the per-call directory.

Each `opencode run` extracts an embedded .so to the OS temp dir and never deletes it
(2026-11-02: 1363 leaked /tmp/.<hex>-00000000.so, 7.1 GB). Both spawn sites pin the
child TMPDIR/TMP/TEMP to the per-call TemporaryDirectory so the leak is removed with it.
"""
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from omni_story.library import opencode_provider, pipeline
from omni_story.library.state import write_json
from test_creative_server_retry import fixture
from test_text_call_transport import model  # noqa: F401  (pytest fixture)


def test_vision_submit_pins_child_tmpdir_to_per_call_cwd(tmp_path, monkeypatch):
    state, _, _ = fixture(tmp_path)
    first = state.data['calls'][0]
    state.complete_call(first, dict(status='complete', result=dict(content=[])))
    request = pipeline._read(state.output / 'calls' / first['id'] / 'request.json')
    request['arguments']['prompt'] = 'Per-call temp dir probe'
    mcp = opencode_provider.OpenCodeMCP.__new__(opencode_provider.OpenCodeMCP)
    mcp.state, mcp.output = state, state.output
    mcp.package_root, mcp.executable, mcp.timeout_s = tmp_path, 'local-fixture', 10
    mcp.parent_baseline, mcp.audio_new_work_exclusions, mcp.exclusions = None, None, []
    mcp.queue = state.output / 'mcp_queue'
    mcp.queue.mkdir()
    monkeypatch.setenv('Z_AI_API_KEY', 'local-fixture-no-api')
    parent_environ = dict(os.environ)
    seen = {}

    class Process:
        def __init__(self, argv, **kwargs):
            seen['env'], seen['cwd'] = kwargs['env'], kwargs['cwd']

        def wait(self, **kwargs):
            job = seen['env']['OMNI_LIBRARY_OPENCODE_JOB']
            write_json(mcp.queue / (job + '.response.json'),
                       dict(status='complete', result=dict(content=[dict(type='text', text='{}')])))
            return 0

    monkeypatch.setattr(opencode_provider.subprocess, 'Popen', Process)
    call, reply = mcp._submit('chain_e2e_v1_fine_review', request)
    state._reload()
    assert reply['status'] == 'complete'
    assert state.data['calls'][-1]['status'] == 'received'
    for key in ('TMPDIR', 'TMP', 'TEMP'):
        assert seen['env'][key] == seen['cwd']
    assert not Path(seen['cwd']).exists()
    assert dict(os.environ) == parent_environ


def test_text_submit_pins_child_tmpdir_to_per_call_cwd(model, monkeypatch):
    parent_environ = dict(os.environ)
    seen = {}

    def run(argv, **kwargs):
        seen['env'], seen['cwd'] = kwargs['env'], kwargs['cwd']
        kwargs['stdout'].write(b'{"type":"text","part":{"text":"{\\"ok\\":true}"}}\n')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, 'run', run)
    assert model.call_text('silent_review_0', 'prompt', lambda value: None) == {'ok': True}
    for key in ('TMPDIR', 'TMP', 'TEMP'):
        assert seen['env'][key] == seen['cwd']
    assert not Path(seen['cwd']).exists()
    assert dict(os.environ) == parent_environ
