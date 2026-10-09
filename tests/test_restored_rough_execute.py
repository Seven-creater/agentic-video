"""Real synthetic media through restored templates and the original renderer."""
import json
from pathlib import Path

import pytest

from omni_story.library import pipeline
from omni_story.library.resources import original_rough_v1 as historical
from omni_story.library.media import sha256_file
from test_library_execute import inputs, _bridge, _fixture_responses


def test_real_restored_templates_render_actual_rough_before_fine_stage(inputs):
    reference, library, output = inputs
    answer = _fixture_responses(reference, library)
    reference_reading = answer({'job_id':'glm_001_reference'})
    seed = {'source_call_id':'glm_001_reference','full_response':{'reference':reference_reading},
            'evidence_limit':'synthetic received reference fixture'}
    with _bridge(output,answer) as requests:
        result = pipeline.execute(reference, library, output, span_s=3, frames=2, max_fine=1,
            max_requests=20, asr=False, reference_seed=seed, prompt_module=historical,
            render_fn=historical.render_library_video)
    names=[job['job_id'].split('_',2)[2] for job in requests]
    assert 'reference' not in names
    assert 'plan_0' in names and 'blind_0' in names and 'review_0' in names
    assert not any(name.startswith(('finecut_','economy_','semantic_')) for name in names)
    compiled=json.loads((output/'render_0/render_input.json').read_text(encoding='utf-8'))['compiled']
    assert compiled['renderer_version'] == 'library_render_v1'
    assert sha256_file(result['final_video']) == result['final_sha256']
    plan_job=next(job for job in requests if job['job_id'].endswith('_plan_0'))
    assert plan_job['tool'] == 'analyze_image'
    assert plan_job['arguments']['image_source'] != str(reference)
    assert Path(result['final_video']).is_file()
