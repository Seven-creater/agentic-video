"""Run the historical rough-generation method with newly model-owned choices.

The original reference observation is supplied as received evidence.  No old
movie route, editing table, or duration target is supplied to the new model.
Authorization, source verification and the append-only parent linkage belong
to the server restoration controller; this adapter does not launch a job.
"""
from __future__ import annotations

from pathlib import Path

from . import pipeline
from .state import LibraryStopped

METHOD = 'original_rough_v1'
REFERENCE_CALL = 'glm_001_reference'


def run_rough(reference, library, base, *, reference_seed, provider_config,
              model_factory, registry_path, asr_model_dir=None):
    """Generate and review at most two rough candidates using the old method.

    ``model_factory`` receives the exact state created by ``pipeline.execute``;
    the controller can retain that state and MCP for the following finecut.
    The reference seed must bind the original received001 observation.  It is
    reused without issuing another full-reference model request.
    """
    if (not isinstance(reference_seed, dict) or
            reference_seed.get('source_call_id') != REFERENCE_CALL or
            not isinstance(reference_seed.get('full_response'), dict) or
            not isinstance(reference_seed['full_response'].get('reference'), dict) or
            not reference_seed.get('evidence_limit')):
        raise LibraryStopped('restored_rough_original_received_reference_required')
    if (not isinstance(provider_config, dict) or
            provider_config.get('provider') != 'official_vision_mcp_in_opencode' or
            provider_config.get('progress_policy') != 'finite_stages_no_retry_v1'):
        raise LibraryStopped('restored_rough_finite_official_provider_required')
    if not callable(model_factory):
        raise ValueError('restored_rough_model_factory_required')
    if asr_model_dir is not None:
        asr_model_dir = Path(asr_model_dir).resolve(strict=True)
        if not asr_model_dir.is_dir():
            raise ValueError('restored_rough_asr_model_directory_required')

    from .resources import original_rough_v1 as historical

    configuration = {**provider_config, 'restoration_method': METHOD}
    return pipeline.execute(
        reference, library, base,
        span_s=600, frames=18, max_fine=16, max_requests=None, asr=True,
        editing_v2=False, semantic_audit=False, active_finecut=False,
        model_factory=model_factory, provider_config=configuration,
        registry_path=registry_path, reference_seed=reference_seed,
        prompt_module=historical, render_fn=historical.render_library_video,
        asr_model_dir=asr_model_dir,
    )
