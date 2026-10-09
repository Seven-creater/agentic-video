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


def test_second_candidate_uses_historical_clarification_without_changing_blind_or_search(inputs):
    from copy import deepcopy
    reference, library, output = inputs
    base_answer = _fixture_responses(reference, library)
    seed = {'source_call_id': 'glm_001_reference',
            'full_response': {'reference': base_answer({'job_id': 'glm_001_reference'})},
            'evidence_limit': 'synthetic received reference fixture'}

    def answer(job):
        name = job['job_id'].split('_', 2)[2]
        if name == 'search_1':
            value = base_answer({'job_id': 'glm_000_search_0'})
            value['windows'][0].update(start_s=2, end_s=5)
            return value
        if name.startswith('fine_'):
            value = base_answer(job)
            prompt = job['arguments']['prompt']
            context = json.loads(prompt[prompt.index('{"window":'):])
            value['window_id'] = context['window']['window_id']
            return value
        if name in ('plan_1', 'blind_1', 'review_1'):
            return base_answer({**job, 'job_id': job['job_id'].rsplit('_', 1)[0] + '_0'})
        if name == 'select_render':
            return {'selected_round': 1, 'reason': 'Synthetic second-candidate plumbing check.'}
        value = base_answer(job)
        if name == 'review_0':
            value = deepcopy(value)
            value.update(theme_status='fail', revision_requests=['Inspect a further interval.'])
        return value

    with _bridge(output, answer) as requests:
        result = pipeline.execute(reference, library, output, span_s=3, frames=2, max_fine=2,
            max_requests=30, asr=False, reference_seed=seed, prompt_module=historical,
            render_fn=historical.render_library_video)
    jobs = {job['job_id'].split('_', 2)[2]: job for job in requests}
    fine_jobs = [job for name, job in jobs.items() if name.startswith('fine_')]
    assert len(fine_jobs) == 2
    marker = '本任务是异源素材的主旨迁移'
    assert marker not in fine_jobs[0]['arguments']['prompt']
    assert marker in fine_jobs[1]['arguments']['prompt']
    for stage in ('plan', 'review'):
        assert marker not in jobs[stage + '_0']['arguments']['prompt']
        assert marker in jobs[stage + '_1']['arguments']['prompt']
    assert marker not in jobs['search_1']['arguments']['prompt']
    for name, job in jobs.items():
        if name.startswith('blind_'):
            assert marker not in job['arguments']['prompt']
    # Identical synthetic renders reuse the received blind request by digest.
    assert json.loads((output / 'blind_reading_1.json').read_text()) == json.loads(
        (output / 'blind_reading_0.json').read_text())
    assert marker in jobs['select_render']['arguments']['prompt']
    assert result['selected_round'] == 1
    assert (output / 'render_0/final.mp4').is_file()
    assert sha256_file(result['final_video']) == result['final_sha256']
