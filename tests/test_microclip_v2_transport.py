"""The retry keeps tool arguments intact and has a distinct local identity."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story.library import microclip_v2_transport as module
from omni_story.library.pipeline import CodexMCP
from omni_story.library.state import LibraryStopped, json_sha, write_json


@pytest.fixture
def client(tmp_path):
    media = tmp_path / 'actual_grid.png'
    media.write_bytes(b'bound synthetic image')
    scope = {'kind': 'sparse_contact_sheet', 'source_sha256': 'source', 'source_start_s': 6, 'source_end_s': 9}
    original = {'tool': 'analyze_image', 'arguments': {'image_source': str(media), 'prompt': 'Exact original question.'},
        'observation_scope': scope, 'media_sha256': 'synthetic_sha', 'provider': 'official_vision_mcp_in_codex'}
    ident = 'glm_224_mc2_region_1'
    write_json(tmp_path / 'calls' / ident / 'request.json', original)
    descriptor = tmp_path / 'input.json'
    write_json(descriptor, {'stage': 'mc2_region_1', 'media_path': str(media), 'bound': True})
    artifacts = {'mc2_input_mc2_region_1': [{'path': str(descriptor)}]}
    state = SimpleNamespace(output=tmp_path, data={'artifacts': artifacts}, authorization={
        'known_failure_resume': {'failed_stage': 'mc2_region_1', 'failed_call_id': ident,
                                 'retry_stage': 'mc2_region_1_retry'}})
    recorded = []
    state.set_artifact = lambda name, value: recorded.append((name, deepcopy(value)))
    result = object.__new__(module.KnownFailureMCP)
    result.output, result.state = tmp_path, state
    return result, media, scope, original, recorded


def test_only_failed_phase_is_routed_and_uses_original_prompt(client, monkeypatch):
    result, media, scope, original, artifacts = client
    received = []
    monkeypatch.setattr(CodexMCP, 'call', lambda self, *args, **kwargs: received.append((args, kwargs)) or {'ok': True})
    assert result.call('mc2_region_1', 'Program wording after reload.', media, lambda v: v,
                       image=True, scope=scope) == {'ok': True}
    assert received[0][0][:2] == ('mc2_region_1_retry', original['arguments']['prompt'])
    assert artifacts[0][0] == 'mc2_input_mc2_region_1_retry'
    assert artifacts[0][1]['stage'] == 'mc2_region_1_retry'
    result.call('mc2_region_2', 'New independent region.', media, lambda v: v, image=True, scope=scope)
    assert received[-1][0][:2] == ('mc2_region_2', 'New independent region.')
    assert len(artifacts) == 1


def test_metadata_changes_local_digest_without_changing_provider_arguments(client, monkeypatch):
    result, media, scope, original, artifacts = client
    sent = []
    monkeypatch.setattr(CodexMCP, '_submit', lambda self, name, request, **kw: sent.append((name, request, kw)))
    result._submit('mc2_region_1_retry', original)
    retry = sent[0][1]
    assert retry['known_failure_retry_of'] == 'glm_224_mc2_region_1'
    assert retry['arguments'] == original['arguments']
    assert json_sha(retry) != json_sha(original)
    assert 'known_failure_retry_of' not in original
    result._submit('mc2_region_1_retry_repair', original, repair_of='retry_call')
    assert sent[1][2]['repair_of'] == 'retry_call'
    assert sent[1][1]['known_failure_retry_of'] == 'glm_224_mc2_region_1'
    result._submit('mc2_region_2', original)
    assert sent[2][1] == original


def test_undispatched_retry_uses_same_model_input_and_separate_local_parent(client, monkeypatch):
    result, media, scope, original, artifacts = client
    dispatch = {'failed_call_id': 'glm_225_mc2_region_1_retry', 'retry_stage': 'mc2_region_1_retry_dispatch'}
    result.state.authorization['known_failure_dispatch_resume'] = dispatch
    calls, requests = [], []
    monkeypatch.setattr(CodexMCP, 'call', lambda self, *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(CodexMCP, '_submit', lambda self, name, req, **kw: requests.append(req))
    result.call('mc2_region_1', 'changed wording', media, lambda v: v, image=True, scope=scope)
    assert calls[0][0][:2] == (dispatch['retry_stage'], original['arguments']['prompt'])
    assert artifacts[0][0] == 'mc2_input_' + dispatch['retry_stage']
    result._submit(dispatch['retry_stage'], original)
    assert requests[0] == {**original, 'known_failure_retry_of': dispatch['failed_call_id']}
    assert requests[0]['arguments'] == original['arguments']


@pytest.mark.parametrize('change', ['scope', 'tool', 'media'])
def test_retry_cannot_change_bound_input(client, monkeypatch, change):
    result, media, scope, original, artifacts = client
    monkeypatch.setattr(CodexMCP, 'call', lambda *a, **kw: pytest.fail('Changed input reached provider.'))
    if change == 'scope':
        scope = {**scope, 'source_end_s': 10}
    elif change == 'media':
        media = Path(str(media) + '.other')
    with pytest.raises(LibraryStopped, match='retry_input_changed'):
        result.call('mc2_region_1', 'Question', media, lambda v: v, image=change != 'tool', scope=scope)
    assert not artifacts
