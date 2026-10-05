"""Evidence reaches the unpaid draft; synthetic queue/render/resume only."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from omni_story.library import research_resume, flat_refinement, audience_obligations
from omni_story.library.goal_feedback_continuation import execute_goal_continuation
from omni_story.library.state import json_sha
from test_library_execute import inputs, _bridge, _read
from test_library_goal_feedback_continuation import prepare, answer
from test_library_research_goal_execute import _research_answer, _all_bytes

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='Needs FFmpeg')


def test_tenth_round_audience_facts_shared_refinement_explicit_claims_and_resume(inputs):
    reference, library, output = inputs
    prepare(inputs)
    research_resume.enable(output, 'synthetic evidence-first work', first_round=5)
    with _bridge(output, _research_answer(inputs)):
        assert execute_goal_continuation(reference, library, output)['new_renders'] == 1
    with _bridge(output, answer(inputs, fail='active_6_draft')):
        assert execute_goal_continuation(reference, library, output, start_next=True)['new_renders'] == 0
    flat_refinement.enable(output, 'synthetic serialization')
    for r in (7, 8):
        with _bridge(output, answer(inputs, fail=f'active_{r}_draft')):
            assert execute_goal_continuation(reference, library, output, start_next=True)['new_renders'] == 0
    strategy = research_resume.enable_preplanning(output, 'synthetic forward strategy')
    with _bridge(output, answer(inputs, fail='active_9_draft')):
        assert execute_goal_continuation(reference, library, output, start_next=True)['new_renders'] == 0
    old_calls = deepcopy(_read(output / 'library_state.json')['calls'])
    old_requests = {str(p): p.read_bytes() for p in (output / 'calls').glob('*/request.json')}
    audience = audience_obligations.enable(output, 'synthetic audience and sampled-source strategy')
    oracle = _research_answer(inputs, variant=True)
    contexts = {}

    def reply(job):
        stage = job['job_id'].split('_', 2)[2].removesuffix('_repair')
        prompt = job['arguments']['prompt']
        if stage == 'active_10_draft':
            context = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reference":'):])[0]
            contexts['draft'] = context
            assert context['context_projection']['policy'] == strategy['policy']
            assert context['research_refinement_evidence']['source_observations']
            assert context['audience_obligations']['knowledge_sha256'] == audience['knowledge_sha256']
            assert 'intended_takeaway全文原样保留' in context['audience_obligations']['knowledge']
            assert all('model_text' not in row.get('protocol_failure', {}) for row in context['actual_feedback']['records'])
        if stage == 'active_10_finecut':
            inp = json.JSONDecoder().raw_decode(prompt[prompt.index('{"original_draft":'):])[0]
            contexts['finecut'] = inp['observations']
            assert contexts['finecut']['research_refinement_evidence'] == contexts['draft']['research_refinement_evidence']
            assert contexts['finecut']['watched_windows'] == contexts['draft']['watched_windows']
            assert contexts['finecut']['audience_obligations'] == contexts['draft']['audience_obligations']
            shim = deepcopy(job)
            shim['arguments']['prompt'] = json.dumps({'policy': 'test only', 'draft': inp['original_draft'],
                'evidence': inp['observations'], 'test_note': 'timing_checks evidence_timing_refinement_v1'})
            return oracle(shim)
        if stage.startswith('semantic_slice_10_'):
            assert 'uncertainties必须显式为字符串数组list[str]' in prompt
            assert '0 <= local_start_s < local_end_s <= observed_duration_s' in prompt
            assert '每项必须包含七个字段' in prompt
            assert job['tool'] == 'analyze_image'
            assert 'image_source' in job['arguments'] and 'video_source' not in job['arguments']
            request = _read(output / 'calls' / job['job_id'] / 'request.json')
            assert request['observation_scope']['kind'] == 'continuous_window'
        if stage.startswith('semantic_claims_10_'):
            assert '全部五个字段' in prompt and '输入事实与待核说法' in prompt
            inp = json.JSONDecoder().raw_decode(prompt[prompt.index('{"observation":'):])[0]
            shim = deepcopy(job)
            shim['arguments']['prompt'] = '\nobservation：' + json.dumps(inp['observation']) + \
                '\nrequired_claims：' + json.dumps(inp['required_claims']) + '\nrole_hypotheses：' + json.dumps(inp['role_hypotheses'])
            return oracle(shim)
        return oracle(job)

    with _bridge(output, reply) as jobs:
        result = execute_goal_continuation(reference, library, output, start_next=True)
        assert result['selected_round'] == 10 and result['new_renders'] == 1
        assert result['active_finecut_gate_passed'] is True
        data = _read(output / 'library_state.json')
        proof = data['artifacts']['goal_research_projection_10'][0]
        projection = _read(proof['path'])
        assert json_sha(projection) == proof['sha256']
        assert projection['projected_context'] == contexts['draft']
        assert data['calls'][:len(old_calls)] == old_calls
        assert all(Path(path).read_bytes() == raw for path, raw in old_requests.items())
        count = len(jobs)
        before = _all_bytes(output)
        assert execute_goal_continuation(reference, library, output) == result
        assert len(jobs) == count and _all_bytes(output) == before
