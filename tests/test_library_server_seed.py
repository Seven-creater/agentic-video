"""Real FFmpeg seed/resume plumbing, with every model reply simulated locally."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from omni_story.library.media import probe_media, sha256_file
from omni_story.library.opencode_provider import PROVIDER
from omni_story.library.pipeline import CodexMCP, execute
from omni_story.library.state import json_sha
from test_library_active_finecut_execute import _responses
from test_library_execute import _bridge, _read, _semantic_fixture_responses, inputs


pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                               reason='Real FFmpeg/FFprobe integration requires both tools')


class SimulatedOpenCodeQueue(CodexMCP):
    """Only the transport is simulated; use the new provider's locked identity."""
    provider = PROVIDER['provider']


def test_server_seed_uses_independent_media_and_resumes_with_zero_new_requests(inputs):
    reference, library, output = inputs
    base = _semantic_fixture_responses(reference, library)
    def cached(name):
        return base({'job_id': 'glm_000_' + name, 'arguments': {'prompt': 'synthetic seed fixture'}})
    seed = {
        'source_call_id': 'synthetic_received_seed', 'reference_sha256': sha256_file(reference),
        'full_response': {'reference': cached('reference'),
                          'editing_reference': cached('editing_reference_v2')},
        'evidence_limit': 'Synthetic cached navigation only; full-reference coverage remains unestablished.',
    }
    original_seed = deepcopy(seed)
    def run():
        return execute(reference, library, output, span_s=3, frames=2, max_fine=1,
                       max_requests=None, asr=False, active_finecut=True,
                       model_factory=SimulatedOpenCodeQueue, provider_config=PROVIDER,
                       reference_seed=seed)
    with _bridge(output, _responses(reference, library)) as jobs:
        result = run()
        count = len(jobs)
        assert count == result['usage']['requests'] == 15
        assert result['usage']['max_requests'] is None
        assert result['reference_navigation_limit'] == seed['evidence_limit']
        assert result['reference_seed_sha256'] == json_sha(seed)
        assert seed == original_seed
        names = {job['job_id'].split('_', 2)[2] for job in jobs}
        assert 'reference' not in names and 'editing_reference_v2' not in names
        assert {'plan_0', 'finecut_0'} <= names
        all_requests = [_read(path) for path in (output / 'calls').glob('*/request.json')]
        assert all(request['provider'] == PROVIDER['provider'] for request in all_requests)
        assert all(request['media_sha256'] != sha256_file(reference) for request in all_requests)
        selected_library_shas = {sha256_file(path) for path in library.iterdir()}
        for job in jobs:
            name = job['job_id'].split('_', 2)[2]
            if name in {'plan_0', 'finecut_0'}:
                assert job['tool'] == 'analyze_image'
                assert 'image_source' in job['arguments'] and 'video_source' not in job['arguments']
                media = Path(job['arguments']['image_source'])
                lineage = _read(media.parent / 'lineage.json')
                assert lineage['source_sha256'] in selected_library_shas
                assert any(prior['arguments'].get('image_source') == str(media)
                           and prior['job_id'].split('_', 2)[2].startswith(('overview_', 'zoom_'))
                           for prior in jobs[:jobs.index(job)])
                assert seed['evidence_limit'] in job['arguments']['prompt']
        review_job = next(job for job in jobs if job['job_id'].endswith('_review_0'))
        assert seed['evidence_limit'] in review_job['arguments']['prompt']
        state = _read(output / 'library_state.json')
        assert state['max_requests'] is None
        assert state['input_lock']['configuration']['reference_seed_sha256'] == json_sha(seed)
        for name in ('active_finecut_search_budget_0', 'active_finecut_plan_budget_0'):
            allocation = _read(state['artifacts'][name][0]['path'])
            assert allocation['remaining_at_allocation'] is None
        plan_budget = _read(state['artifacts']['active_finecut_plan_budget_0'][0]['path'])
        assert plan_budget['max_segments'] == 32
        final = Path(result['final_video'])
        assert final.is_file()
        assert probe_media(final)['duration_s'] == pytest.approx(1.466667, abs=.05)
        assert sha256_file(final) == result['final_sha256']
        before_calls = deepcopy(state['calls'])
        before_request_bytes = {path: path.read_bytes() for path in (output / 'calls').glob('*/*.json')}
        before_video = final.read_bytes()
        resumed = run()
        assert resumed == result
        assert len(jobs) == count
        assert _read(output / 'library_state.json')['calls'] == before_calls
        assert final.read_bytes() == before_video
        assert all(path.read_bytes() == content for path, content in before_request_bytes.items())
    assert len(list(output.glob('render_*/final.mp4'))) == 1
