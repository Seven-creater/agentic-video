"""Restoration dispatch checks; no model or server request is performed."""
from copy import deepcopy
from pathlib import Path
from types import ModuleType
import sys

import pytest

from omni_story.library import restored_rough
from omni_story.library.state import LibraryStopped


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    seed = {
        'source_call_id': 'glm_001_reference',
        'full_response': {'reference': {'theme': 'model received observation'}},
        'evidence_limit': 'Original model observation; audio is unverified.',
    }
    provider = {
        'provider': 'official_vision_mcp_in_opencode',
        'progress_policy': 'finite_stages_no_retry_v1',
    }
    historical = ModuleType('omni_story.library.resources.original_rough_v1')
    historical.render_library_video = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, historical.__name__, historical)
    # Import machinery may retain a previously loaded module on its package.
    from omni_story.library import resources
    monkeypatch.setattr(resources, 'original_rough_v1', historical, raising=False)
    captured = []
    returned = {'final_video': 'model_selected_actual_rough.mp4'}
    def execute(*args, **kwargs):
        captured.append((args, kwargs))
        return returned
    monkeypatch.setattr(restored_rough.pipeline, 'execute', execute)
    model_factory = lambda state: object()
    kwargs = dict(reference_seed=seed, provider_config=provider,
                  model_factory=model_factory, registry_path=tmp_path / 'registry.json')
    return tmp_path, kwargs, captured, returned, historical


def test_dispatch_restores_generation_before_any_finecut(inputs):
    tmp_path, kwargs, captured, returned, historical = inputs
    old_seed, old_provider = deepcopy(kwargs['reference_seed']), deepcopy(kwargs['provider_config'])
    result = restored_rough.run_rough('reference.mp4', 'movies', tmp_path / 'rough', **kwargs)
    assert result is returned
    assert len(captured) == 1
    args, options = captured[0]
    assert args == ('reference.mp4', 'movies', tmp_path / 'rough')
    assert options['span_s'] == 600 and options['frames'] == 18
    assert options['max_fine'] == 16 and options['max_requests'] is None
    assert options['asr'] is True
    assert not options['editing_v2'] and not options['semantic_audit'] and not options['active_finecut']
    assert options['prompt_module'] is historical
    assert options['render_fn'] is historical.render_library_video
    assert options['model_factory'] is kwargs['model_factory']
    assert options['provider_config']['restoration_method'] == 'original_rough_v1'
    assert options['reference_seed'] == old_seed
    assert kwargs['reference_seed'] == old_seed and kwargs['provider_config'] == old_provider
    assert not any(key in options for key in ('rough_plan', 'target_s', 'source_ranges', 'teacher_edl'))


@pytest.mark.parametrize('call_id', ['glm_060', 'glm_060_semantic_reference', 'glm_131', None])
def test_later_or_unknown_reference_cannot_replace_original_received001(inputs, call_id):
    tmp_path, kwargs, captured, _, _ = inputs
    kwargs['reference_seed']['source_call_id'] = call_id
    with pytest.raises(LibraryStopped, match='original_received_reference_required'):
        restored_rough.run_rough('reference', 'movies', tmp_path / 'rough', **kwargs)
    assert captured == []


@pytest.mark.parametrize('edit', [
    lambda seed: seed.pop('evidence_limit'),
    lambda seed: seed.update(full_response={'reference': None}),
    lambda seed: seed.update(full_response='not an object'),
])
def test_missing_reference_evidence_stops_before_pipeline(inputs, edit):
    tmp_path, kwargs, captured, _, _ = inputs
    edit(kwargs['reference_seed'])
    with pytest.raises(LibraryStopped, match='original_received_reference_required'):
        restored_rough.run_rough('reference', 'movies', tmp_path / 'rough', **kwargs)
    assert captured == []


@pytest.mark.parametrize('edit', [
    lambda provider: provider.update(provider='standard_model_api'),
    lambda provider: provider.pop('progress_policy'),
])
def test_uncapped_requests_require_the_existing_finite_official_provider(inputs, edit):
    tmp_path, kwargs, captured, _, _ = inputs
    edit(kwargs['provider_config'])
    with pytest.raises(LibraryStopped, match='finite_official_provider_required'):
        restored_rough.run_rough('reference', 'movies', tmp_path / 'rough', **kwargs)
    assert captured == []


def test_offline_asr_model_directory_is_passed_to_pipeline(inputs):
    tmp_path, kwargs, captured, _, _ = inputs
    model_dir = tmp_path / 'whisper-small'
    model_dir.mkdir()
    restored_rough.run_rough('reference', 'movies', tmp_path / 'rough', asr_model_dir=model_dir, **kwargs)
    assert captured[0][1]['asr_model_dir'] == model_dir.resolve()


def test_asr_file_is_rejected_before_model_start(inputs):
    tmp_path, kwargs, captured, _, _ = inputs
    model_file = tmp_path / 'whisper-small'
    model_file.write_bytes(b'not a directory')
    with pytest.raises(ValueError, match='asr_model_directory_required'):
        restored_rough.run_rough('reference', 'movies', tmp_path / 'rough', asr_model_dir=model_file, **kwargs)
    assert captured == []


def test_pipeline_terminal_failure_is_propagated_without_dispatch_loop(inputs, monkeypatch):
    tmp_path, kwargs, captured, _, _ = inputs
    def failing(*args, **options):
        captured.append((args, options))
        raise LibraryStopped('known_protocol_failure')
    monkeypatch.setattr(restored_rough.pipeline, 'execute', failing)
    with pytest.raises(LibraryStopped, match='known_protocol_failure'):
        restored_rough.run_rough('reference', 'movies', tmp_path / 'rough', **kwargs)
    assert len(captured) == 1
