"""Single flat output, batched diagnostics and bounded repair in production queue."""
from copy import deepcopy
import json
import shutil
import pytest

from omni_story.library import flat_refinement, research_resume
from omni_story.library.goal_feedback_continuation import execute_goal_continuation
from test_library_execute import inputs, _bridge, _read
from test_library_goal_feedback_continuation import prepare, answer
from test_library_research_goal_execute import _research_answer, _all_bytes

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                                reason='Requires FFmpeg/FFprobe')


def test_flat_round7_reports_multiple_errors_in_its_sole_repair_and_caches_actual_render(inputs):
    reference, library, output = inputs
    prepare(inputs)
    research_resume.enable(output, 'synthetic resumed research then Goal', first_round=6)
    for r in (5, 6):
        with _bridge(output, answer(inputs, fail=f'active_{r}_draft')):
            result = execute_goal_continuation(reference, library, output, start_next=r == 6)
            assert result['status'] == 'stopped_protocol_failure' and result['new_renders'] == 0
    old_calls = deepcopy(_read(output / 'library_state.json')['calls'])
    flat_refinement.enable(output, 'synthetic forward-only output clarification')
    base = _research_answer(inputs)
    def reply(job):
        stage = job['job_id'].split('_', 2)[2]
        if stage.startswith('active_7_finecut'):
            prompt = job['arguments']['prompt']
            inp = json.JSONDecoder().raw_decode(prompt[prompt.index('{"original_draft":'):])[0]
            # The old test oracle expects its historical prompt shape. Adapt only
            # this synthetic oracle; the recorded real queue input stays flat.
            shim = deepcopy(job)
            shim['arguments']['prompt'] = json.dumps({'policy': 'fixture_only',
                'draft': inp['original_draft'], 'evidence': inp['observations'],
                'fixture_note': 'timing_checks evidence_timing_refinement_v1'})
            value = base(shim)
            if stage == 'active_7_finecut':
                value['draft'] = value.pop('plan')
                value['timing_checks'][0]['purpose'] = 'action/result'
                value['duration']['total_s'] += 1
            else:
                assert 'mechanical_refinement_issues' in prompt
                assert 'purpose' in prompt and 'duration' in prompt and 'wrong_location' in prompt
            return value
        return base(job)
    with _bridge(output, reply) as jobs:
        result = execute_goal_continuation(reference, library, output, start_next=True)
        assert result['selected_round'] == 7 and result['new_renders'] == 1
        assert result['active_finecut_gate_passed'] is True
        assert len([j for j in jobs if 'active_7_finecut' in j['job_id']]) == 2
        assert (output / 'calls' / next(j['job_id'] for j in jobs if j['job_id'].endswith('active_7_finecut')) /
                'protocol_failure.json').is_file()
        count = len(jobs)
        before = _all_bytes(output)
        assert execute_goal_continuation(reference, library, output) == result
        assert len(jobs) == count and _all_bytes(output) == before
    assert _read(output / 'library_state.json')['calls'][:len(old_calls)] == old_calls
