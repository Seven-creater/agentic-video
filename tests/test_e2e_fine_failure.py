"""Regression evidence: evidence_01_不烧心_1.txt; no provider/network calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omni_story.library import opencode_provider, pipeline, prompts, question_first as qfs
from omni_story.library.state import LibraryState, LibraryStopped
from test_question_first import (anchor, cache_fixture, close_reply, film_cast_fixture,
                                 observation_fixture, questions, source_fixture)


FIXTURES = Path(__file__).parent / 'fixtures/e2e_fine_failure_20261029'


def evidence(name):
    return qfs.read_json(FIXTURES / (name + '.json'))


def close_job():
    beats = questions()
    return dict(stage='close', beat=beats['beats'][0], beat_ids=['b1', 'b2', 'b3', 'b4'],
        window=(0, 20), anchors={}, prompt=qfs.close_prompt(beats['beats'][0], beats, {}, (0, 20)))


def test_real_close_replies_preserve_negative_verdict_and_reuse_paid_bytes(tmp_path, monkeypatch):
    replies = [evidence(name) for name in ['glm_161_qfs_close_22', 'glm_162_qfs_close_23', 'glm_163_qfs_close_18']]
    http = Mock(side_effect=deepcopy(replies))
    monkeypatch.setattr(qfs, 'http_post', http)
    state = LibraryState(tmp_path / 'run', {}, max_requests=80, registry_path=tmp_path / 'registry.json')
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'fixture'), max_parallel=1)
    jobs = [(f'qfs_close_{n}', dict(close_job(), prompt=close_job()['prompt'] + str(n))) for n in [22, 23, 18]]
    results = runner._run_jobs(jobs)
    assert len(results) == 3 and runner.unknown_calls == []
    for (_, _, result), reply in zip(results, replies):
        original = json.loads(reply['choices'][0]['message']['content'])
        assert result == dict(original, tts_needed=[])
        assert result['verdict'] == 'no' and 'film_in_s' not in result
    assert runner._run_jobs(jobs) == results
    assert http.call_count == state.usage()['requests'] == 3
    saved = qfs.read_json(state.output / 'calls' / state.data['calls'][-1]['id'] / 'response.json')
    assert json.loads(saved['choices'][0]['message']['content'])['tts_needed'] is None


def test_tts_contract_is_shared_and_invalid_values_remain_repairable(tmp_path, monkeypatch):
    reply = evidence('glm_163_qfs_close_18')
    data = json.loads(reply['choices'][0]['message']['content'])
    job = close_job()
    contract = qfs.close_tts_contract()
    context, _ = json.JSONDecoder().raw_decode(job['prompt'].split('问题：\n', 1)[1])
    assert context['tts_needed_contract'] == contract
    # Null means no supplied TTS content; it never invents content or upgrades evidence.
    for verdict in ['no', 'partial']:
        checked = qfs.validate_reply(dict(data, verdict=verdict), job)
        assert checked['tts_needed'] == [] and checked['verdict'] == verdict
    for invalid in ['abc', 1, {}, [None], [1]]:
        with pytest.raises(ValueError) as caught:
            qfs.validate_reply(dict(data, tts_needed=invalid), job)
        assert caught.value.violation == dict(path='$.tts_needed', value=invalid, **contract)
    # The existing runner must retain the same actionable violation, without resending.
    reply['choices'][0]['message']['content'] = json.dumps(dict(data, tts_needed='abc'))
    http = Mock(return_value=reply)
    monkeypatch.setattr(qfs, 'http_post', http)
    state = LibraryState(tmp_path / 'run', {}, max_requests=80, registry_path=tmp_path / 'registry.json')
    runner = qfs.QuestionFirstRunner(state, [], transport=qfs.DirectTransport(state, 'fixture'))
    assert runner._run_jobs([('qfs_close_18', job)]) == []
    assert runner.unknown_calls[0]['violation'] == dict(path='$.tts_needed', value='abc', **contract)
    assert runner._run_jobs([('qfs_close_18', job)]) == []
    http.assert_called_once()


@pytest.fixture
def fine_run(tmp_path, monkeypatch):
    source = source_fixture(tmp_path)
    source['subtitle_bar'] = dict(source_sha256=source['sha256'], native_width=1920, native_height=1080,
        picture_bottom_px=950, measurement=dict(method='ffmpeg_gray_column_median_v2'),
        mask=dict(x=0, y=950 / 1080, w=1, h=130 / 1080, color='#000000'))
    cache = cache_fixture(tmp_path, source)
    refpath = tmp_path / 'reference.mp4'
    refpath.write_bytes(b'reference')
    ref = dict(source_id='ref', path=str(refpath), sha256=qfs.sha256_file(refpath), duration_s=4, audio_stream_index=None)
    catalog = dict(sources=[source])
    monkeypatch.setattr(pipeline, '_catalog', lambda path, output: deepcopy(
        dict(sources=[ref]) if path == refpath else catalog))
    monkeypatch.setattr(pipeline, 'create_contact_sheet', lambda *a, **k: dict(path=str(refpath)))
    monkeypatch.setattr(pipeline, 'create_navigation_sheet', lambda *a, **k: dict(path=str(refpath)))
    from omni_story.library import visual_story_trial
    monkeypatch.setattr(visual_story_trial, 'proxy', lambda *a, **k: dict(path=str(refpath)))
    monkeypatch.setattr(opencode_provider.subprocess, 'Popen', Mock(side_effect=AssertionError('no resend')))
    def prepare(row, start, end, cache, **options):
        return dict(path=row['path'], sha256=row['sha256'], source_id=row['source_id'],
            source_sha256=row['sha256'], source_start_s=start, source_end_s=end,
            source_visual_policy='silent_subtitle_masked', source_subtitle_masks=options.get('subtitle_masks', []))
    monkeypatch.setattr(pipeline, 'prepare_window', prepare)
    candidates = [dict(close_reply(), source_id=source['source_id'], film=1, film_in_s=a, film_out_s=b, clip=f'c{i}.mp4')
                  for i, (a, b) in enumerate([(0.5, 1), (2.5, 3.5)])]
    report = dict(beats=[dict(questions()['beats'][0], candidates=candidates)],
        cast_anchors={'A_boastful_villain': anchor(clip='c0.mp4')}, question_first_gaps=[])
    monkeypatch.setattr(qfs.QuestionFirstRunner, 'run', lambda *a: deepcopy(report))
    reading = json.loads(prompts.reference_prompt(ref['sha256'], 4).split('输出：\n', 1)[1])
    reading['strategy'] = dict(adaptation_mode='reinterpret', speech_mode='none', timing_driver='action')
    visual = tmp_path / 'visual'
    qfs.write_json(visual / 'cast.json', film_cast_fixture())
    qfs.write_json(visual / 'content_ranges.json', dict(schema_version='content_ranges_v1', sources={
        source['sha256']: dict(content_start_s=0, content_end_s=4, verified_by='fixture', excluded=[])}))
    fixture = SimpleNamespace(output=tmp_path / 'run', seen=[], fine_count=0, failures=1,
        uncertain=False, recorded=False, workflow='reference_rough_skill_v1', plan_context=None)
    outcome = evidence('run_outcome')
    assert evidence('chain_failure')['error'] == outcome['error']
    assert evidence('chain_failure')['usage']['uncertain_calls'] == []

    class ReachedPlan(Exception):
        pass

    class Model:
        def __init__(self, state):
            self.state = fixture.state = state

        def call(self, name, prompt, media, validator, **kwargs):
            fixture.seen.append(name)
            if name == 'reference':
                value = deepcopy(reading)
            elif name.startswith('fine_'):
                fixture.fine_count += 1
                window = dict(zip(validator.__code__.co_freevars,
                    (c.cell_contents for c in validator.__closure__)))['window']
                if fixture.fine_count <= fixture.failures:
                    request = dict(media_sha256=qfs.sha256_file(media), name=name)
                    call, _ = self.state.begin_call(name, request)
                    self.state.fail_call(call, outcome['error'], uncertain=fixture.uncertain)
                    if fixture.recorded:
                        # Exercise the actual provider's digest replay guard on an existing failure.
                        opencode_provider.OpenCodeMCP._submit(
                            SimpleNamespace(state=self.state, output=self.state.output), name, request)
                    raise LibraryStopped(outcome['error_code'] + ':' + call['id'])
                value = observation_fixture(source, window['qfs_identity'][0], window['window_id'])
                value.update(events=[dict(local_start_s=0, local_end_s=1, observed_fact='张嘴挥拳', role_ids=['A'])],
                    usable_ranges=[dict(local_in_s=0, local_out_s=1, event_indices=[0], role_ids=['A'],
                                        camera='中景', continuity_notes='连续动作')])
            elif name == 'plan_0':
                from omni_story.library.replay_check import prompt_context
                fixture.plan_context = prompt_context(dict(prompt=prompt), 'reference', 'catalog', 'watched_windows')
                raise ReachedPlan()
            else:
                pytest.fail('unexpected model call: ' + name)
            validator(value)
            return value

    def run():
        config = dict(creative_audio=True, workflow=fixture.workflow, material_search='question_first',
            shot_cache=str(cache), visual_library_root=str(visual))
        pipeline.execute(refpath, tmp_path, fixture.output, asr=False, provider_config=config,
            model_factory=Model, registry_path=tmp_path / 'registry.json')
    fixture.run, fixture.ReachedPlan = run, ReachedPlan
    return fixture


@pytest.mark.parametrize('recorded', [False, True])
def test_known_fine_failure_is_unobserved_and_other_window_reaches_plan(fine_run, recorded):
    case = fine_run
    case.recorded = recorded
    with pytest.raises(case.ReachedPlan):
        case.run()
    assert case.fine_count == 2
    windows = qfs.read_json(case.output / 'watched_windows.json')
    assert len(windows) == 1
    marker = case.state.data['artifacts']['fine_observation_transport_failure'][0]
    failure = qfs.read_json(marker['path'])
    assert failure['call_id'] == case.state.data['calls'][0]['id']
    assert failure['window_id'] not in [w['window_id'] for w in windows]
    assert failure['source_start_s'] < failure['source_end_s']
    assert failure['status'] == 'not_watched_no_EDL_permission'
    assert case.plan_context['reference']['fine_observation_failures'] == [failure]
    assert case.state.usage()['requests'] == 1
    assert case.state.data['calls'][0]['status'] == 'failed_known'
    opencode_provider.subprocess.Popen.assert_not_called()
    # Unknown outcomes and unrelated stops must never acquire a skip permission.
    call = case.state.data['calls'][0]
    state = SimpleNamespace(input_lock=deepcopy(case.state.input_lock), data=dict(calls=[deepcopy(call)]),
                            _reload=lambda: None)
    error = LibraryStopped(evidence('run_outcome')['error_code'] + ':' + call['id'])
    name = call['name']
    for status in ['uncertain', 'submitted', 'received']:
        state.data['calls'][0]['status'] = status
        assert pipeline._known_fine_transport_failure(state, error, name, {}) is None
    state.data['calls'][0]['status'] = 'failed_known'
    assert pipeline._known_fine_transport_failure(state, error, 'fine_other', {}) is None
    assert pipeline._known_fine_transport_failure(state, LibraryStopped('model_request_budget_exhausted'), name, {}) is None
    state.input_lock['configuration']['workflow'] = 'visual_library_v1'
    assert pipeline._known_fine_transport_failure(state, error, name, {}) is None


def test_all_known_fine_failures_hit_existing_no_footage_gate(fine_run):
    case = fine_run
    case.failures = 2
    with pytest.raises(LibraryStopped, match='^no_watched_window_available_for_planning$'):
        case.run()
    assert case.fine_count == 2 and case.plan_context is None
    assert len(case.state.data['artifacts']['fine_observation_transport_failure']) == 2
    assert not (case.output / 'watched_windows.json').exists()
    opencode_provider.subprocess.Popen.assert_not_called()
