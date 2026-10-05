"""Goal authorization keeps previous stage snapshots strict and read only."""
from copy import deepcopy
import shutil

import pytest

from omni_story.library import extension_budget as extension
from omni_story.library import goal_budget as goal
from omni_story.library import finecut_continuation as continuation
from omni_story.library.state import LibraryStopped, json_sha, write_json
from test_library_extension_budget import base, new_call, request
from test_library_execute import _read, inputs


def _frozen_round(tmp_path):
    original = base(tmp_path)
    extension.authorize(original.output, 'synthetic continuation authorization')
    state = extension.stage_state(original.output)
    new_call(state, 'active_4_draft', 100)
    new_call(state, 'active_4_finecut', 101)
    goal.authorize(state.output, 'synthetic user authorizes Goal to complete actual editing')
    return original, state


def _bytes(output):
    return {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}


def test_pure_v2_snapshot_does_not_validate_foreign_goal_stages(tmp_path):
    original = base(tmp_path)
    policy = extension.authorize(original.output, 'synthetic continuation authorization')
    saved = deepcopy(_read(original.path))
    before = _bytes(original.output)
    assert extension.validate_authorization_snapshot(original.output, saved, ['render_0']) == policy
    assert _bytes(original.output) == before
    saved['calls'].append({'id': 'foreign', 'name': 'active_5_draft', 'status': 'submitted'})
    saved['request_count'] += 1
    with pytest.raises(LibraryStopped, match='stage_not_authorized'):
        extension.validate_authorization_snapshot(original.output, saved, ['render_0'])
    assert _bytes(original.output) == before


def test_v2_goal_read_requires_complete_live_goal_validation(tmp_path, monkeypatch):
    original, _ = _frozen_round(tmp_path)
    checked = []
    validate = goal.get_authorization
    def track(state):
        checked.append(state)
        return validate(state)
    monkeypatch.setattr(goal, 'get_authorization', track)
    before = _bytes(original.output)
    value = extension.get_authorization(original)
    assert value['policy'] == extension.POLICY
    assert checked == [original]
    assert extension.historical_state(original).usage()['requests'] == 1
    assert _bytes(original.output) == before


def test_round4_state_constructor_refuses_goal_before_registry_or_state_writes(tmp_path):
    original, _ = _frozen_round(tmp_path)
    before = _bytes(tmp_path)
    with pytest.raises(LibraryStopped, match='round4_frozen_by_goal_use_goal_entry'):
        extension.stage_state(original.output)
    assert _bytes(tmp_path) == before


def test_invalid_goal_grant_cannot_hide_v2_validation_or_read_old_cache(tmp_path):
    original, _ = _frozen_round(tmp_path)
    saved = _read(original.path)
    entry = saved['artifacts'][goal.AUTHORIZATION][0]
    grant = _read(entry['path'])
    grant['user_authorization'] = ''
    write_json(entry['path'], grant)
    entry['sha256'] = json_sha(grant)
    write_json(original.path, saved)
    before = _bytes(original.output)
    with pytest.raises(LibraryStopped):
        extension.get_authorization(original)
    with pytest.raises(LibraryStopped):
        continuation.execute_finecut_continuation('unused-reference', 'unused-library', original.output)
    assert _bytes(original.output) == before


@pytest.mark.parametrize('method', ['begin', 'complete', 'fail', 'reconcile', 'artifact'])
def test_round4_writer_is_frozen_after_goal_grant(tmp_path, method):
    original, state = _frozen_round(tmp_path)
    call = state.data['calls'][-1]
    operations = {'begin': lambda: state.begin_call('active_4_draft_repair', request(102), repair_of=call),
        'complete': lambda: state.complete_call(call, {'new': True}),
        'fail': lambda: state.fail_call(call, 'changed historical outcome'),
        'reconcile': lambda: state.reconcile_received(call, {'new': True}, evidence={}),
        'artifact': lambda: state.set_artifact('new_legacy_record', {'new': True})}
    before = _bytes(original.output)
    with pytest.raises(LibraryStopped, match='round4_frozen_by_goal_use_goal_entry'):
        operations[method]()
    assert _bytes(original.output) == before


@pytest.mark.parametrize('old_complete', [False, True])
def test_old_entry_uses_completed_cache_or_refuses_incomplete_without_writer(tmp_path, monkeypatch, old_complete):
    """Unit isolation: no registry, lock, MCP, catalog writes or fake completion."""
    original = base(tmp_path)
    extension.authorize(original.output, 'synthetic continuation authorization')
    state = extension.stage_state(original.output)
    new_call(state, 'active_4_draft', 100)
    new_call(state, 'active_4_finecut', 101)
    if old_complete:
        result = {'status': 'historical_library_candidate_with_limitations', 'selected_round': 4,
            'semantic_gate_passed': False, 'usage': state.usage()}
        path = state.output / continuation.RESULT_FILE
        write_json(path, result)
        from omni_story.library.media import sha256_file
        state.set_artifact(continuation.RESULT, {'policy': continuation.POLICY,
            'result_sha256': json_sha(result), 'completed_files': [{'path': str(path), 'sha256': sha256_file(path)}]})
    write_json(state.output / 'reference_catalog/inventory.json', {'synthetic': 'exists'})
    write_json(state.output / 'catalog/inventory.json', {'synthetic': 'exists'})
    goal.authorize(state.output, 'synthetic user authorizes Goal completion')
    calls = []
    def read_catalog(paths, directory):
        calls.append(paths)
        if paths == 'synthetic-ref':
            return {'sources': [{'sha256': original.input_lock['reference_sha256']}]}
        return {'sources': deepcopy(original.input_lock['library_sources'])}
    monkeypatch.setattr(continuation, '_catalog', read_catalog)
    monkeypatch.setattr(extension, 'stage_state', lambda *args: pytest.fail('no old writable state'))
    monkeypatch.setattr(continuation, '_execute', lambda *args: pytest.fail('no old model execution'))
    monkeypatch.setattr(continuation, 'file_lock', lambda *args: pytest.fail('no old execution lock'))
    before = _bytes(state.output)
    if old_complete:
        assert continuation.execute_finecut_continuation('synthetic-ref', 'synthetic-lib', state.output) == result
    else:
        with pytest.raises(LibraryStopped, match='round4_frozen_incomplete_read_only_use_goal_entry'):
            continuation.execute_finecut_continuation('synthetic-ref', 'synthetic-lib', state.output)
    assert calls == ['synthetic-ref', 'synthetic-lib']
    assert _bytes(state.output) == before


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Requires FFmpeg/FFprobe')
def test_completed_actual_synthetic_video_remains_zero_call_cache_after_goal(inputs):
    from test_library_execute import _bridge
    from test_library_finecut_continuation import _setup, _answer
    reference, library, output = inputs
    _setup(inputs)
    with _bridge(output, _answer(inputs)) as jobs:
        result = continuation.execute_finecut_continuation(reference, library, output)
        assert result['active_finecut_gate_passed'] is True
        count = len(jobs)
        goal.authorize(output, 'synthetic Goal authorization after an actual synthetic video')
        (output / 'mcp_ready.json').unlink()
        (output / 'mcp_stop').write_text('preserve stopped bridge', encoding='utf-8')
        before = _bytes(output)
        assert continuation.execute_finecut_continuation(reference, library, output) == result
        assert len(jobs) == count
        assert _bytes(output) == before
