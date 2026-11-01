"""Coarse page bounds are echoes; sampled events still require visual evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from omni_story.library import contracts, pipeline
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json
from test_protocol_feedback import harness


REQUESTED = [34.0, 4991.0]
SAMPLES = [171.7, 722.5, 1548.8, 2650.2, 3751.5, 4853.3]
SOURCE_ID = 'src_24675c65978d5360'


def reply():
    return dict(source_id=SOURCE_ID, coverage_s=[SAMPLES[0], SAMPLES[-1]],
        roles=[dict(role_id='candidate_A', description='黑白相间、体型圆胖的熊猫')],
        events=[dict(timestamp_s=time, observed_fact='熊猫站在室内，面向身旁角色。',
                     role_ids=['candidate_A']) for time in SAMPLES],
        uncertainties=['抽样帧之间的连续动作尚未观察。'])


@pytest.fixture
def page(tmp_path, monkeypatch):
    mcp, media, sent = harness(tmp_path, monkeypatch, [reply(), reply()])
    source = dict(source_id=SOURCE_ID, sha256='a' * 64, duration_s=5025.0,
        path=str(tmp_path / 'original.mp4'), audio_stream_index=None,
        content_range=dict(content_start_s=REQUESTED[0], content_end_s=REQUESTED[1]))
    sources = dict(sources=[source])
    scope = dict(kind='sparse_contact_sheet', source_sha256=source['sha256'],
                 source_start_s=REQUESTED[0], source_end_s=REQUESTED[1])
    sheet = dict(scope, source_id=SOURCE_ID, path=str(media), spec=deepcopy(scope),
                 frames=[dict(frame_id=f'f{i}', source_time_s=time) for i, time in enumerate(SAMPLES)])
    write_json(media.parent / 'lineage.json', sheet)
    monkeypatch.setattr(pipeline, 'create_contact_sheet', lambda *a, **k: deepcopy(sheet))
    call = mcp.call
    observation_calls = []

    def local_call(name, prompt, media_path, validator, **kwargs):
        if name == 'coarse_zoom_search':
            return dict(windows=[])
        observation_calls.append((name, prompt, media_path, validator, kwargs))
        return call(name, prompt, media_path, validator, **kwargs)

    monkeypatch.setattr(mcp, 'call', local_call)

    def run():
        return pipeline._adaptive_coarse(mcp.state, mcp, sources, {}, tmp_path / 'cache',
                                         frames=len(SAMPLES), span_s=600)

    return SimpleNamespace(mcp=mcp, media=media, sent=sent, sources=sources, sheet=sheet,
                           run=run, call=call, observation_calls=observation_calls)


def test_frame_range_echo_is_accepted_recorded_and_idempotent(page):
    coarse, failures, image = page.run()
    assert failures == [] and image == str(page.media)
    value, = coarse
    assert value['coverage_s'] == REQUESTED
    assert value['events'] == reply()['events']
    assert value['normalization'] == [dict(kind='coarse_coverage_echo',
        requested=REQUESTED, written=reply()['coverage_s'])]
    validate = page.observation_calls[0][3]
    before = deepcopy(value)
    validate(value)
    assert value == before
    output = page.mcp.output
    assert json.loads((output / 'coarse_index.json').read_text()) == coarse
    folder = output / 'calls' / page.mcp.state.data['calls'][0]['id']
    assert json.loads((folder / 'parsed.json').read_text()) == reply()
    assert contracts.parse_model_json(json.loads((folder / 'response.json').read_text())
        ['result']['content'][0]['text']) == reply()
    assert len(page.sent) == 1
    assert not (folder / 'protocol_failure.json').exists()


@pytest.mark.parametrize('coverage', [[33.0, 4992.0], [34.0, 4991.0], [171.7, 171.7]])
def test_echo_bounds_tolerance_and_exact_echo(page, coverage):
    page.run()
    value = reply()
    value['coverage_s'] = coverage[:]
    page.observation_calls[0][3](value)
    assert value['coverage_s'] == REQUESTED
    assert value.get('normalization', []) == ([] if coverage == REQUESTED else
        [dict(kind='coarse_coverage_echo', requested=REQUESTED, written=coverage)])


@pytest.mark.parametrize('coverage', [
    [32.99, 4853.3], [171.7, 4992.01], [4853.3, 171.7],
    ['171.7', 4853.3], [True, 4853.3], [float('nan'), 4853.3],
    [171.7, float('inf')], [171.7], {'start_s': 171.7, 'end_s': 4853.3}])
def test_invalid_coverage_is_not_normalized(page, coverage):
    page.run()
    value = reply()
    value['coverage_s'] = deepcopy(coverage)
    with pytest.raises((ValueError, TypeError)):
        page.observation_calls[0][3](value)
    assert 'normalization' not in value


def test_other_known_source_is_rejected_without_normalization(page):
    page.run()
    other = dict(page.sources['sources'][0], source_id='another_film')
    page.sources['sources'].append(other)
    value = dict(reply(), source_id=other['source_id'])
    with pytest.raises(ValueError, match='coarse_does_not_match_requested_page'):
        page.observation_calls[0][3](value)
    assert value['coverage_s'] == reply()['coverage_s'] and 'normalization' not in value


def test_event_off_sample_time_is_still_rejected(page):
    page.run()
    value = reply()
    value['events'][0]['timestamp_s'] = 200.0
    with pytest.raises(ValueError, match='coarse_event_not_at_provided_sample_time'):
        page.observation_calls[0][3](value)


@pytest.mark.parametrize('normalize_times', [False, True])
@pytest.mark.parametrize('saved_kind', ['raw', 'normalized'])
def test_recorded_reply_replays_identically_without_rewriting(page, normalize_times, saved_kind):
    page.mcp.state.normalize_observation_times = normalize_times
    expected = page.run()
    folder = page.mcp.output / 'calls' / page.mcp.state.data['calls'][0]['id']
    if saved_kind == 'normalized':
        write_json(folder / 'parsed.json', expected[0][0])
    before = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    name, prompt, media, validate, kwargs = page.observation_calls[0]
    assert page.call(name, prompt, media, validate, **kwargs) == expected[0][0]
    assert page.run() == expected
    assert {p: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
    assert len(page.sent) == 1 and len(page.observation_calls) == 1


@pytest.mark.parametrize('bad_field,invalid,error', [
    ('coverage_s', [32.99, 4853.3], 'coarse_reply_does_not_match_original_media'),
    ('coverage_s', [171.7, 4992.01], 'coarse_reply_does_not_match_original_media'),
    ('source_id', 'another_film', 'coarse_reply_does_not_match_original_media'),
    ('events', [dict(timestamp_s=200.0, observed_fact='熊猫站立。', role_ids=['candidate_A'])],
     'legacy_coarse_event_not_at_sample_time')])
def test_legacy_reload_keeps_page_source_and_sample_checks(page, monkeypatch, bad_field, invalid, error):
    raw = reply()
    raw[bad_field] = invalid
    page.sources['sources'].append(dict(page.sources['sources'][0], source_id='another_film'))
    request = dict(tool='analyze_image', arguments=dict(image_source=str(page.media), prompt='recorded'),
                   media_sha256=sha256_file(page.media))
    call, _ = page.mcp.state.begin_call('overview_978d5360', request)
    page.mcp.state.complete_call(call, dict(result=dict(content=[dict(type='text', text=json.dumps(raw))])))
    folder = page.mcp.output / 'calls' / call['id']
    before = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    def no_new_observations(*args, **kwargs):
        raise LibraryStopped('model_protocol_repair_exhausted:no_new_observations_in_reload_test')

    monkeypatch.setattr(page.mcp, 'call', no_new_observations)
    with pytest.raises(LibraryStopped, match='no_valid_coarse_evidence_available_for_search'):
        page.run()
    failures = json.loads((page.mcp.output / 'coarse_failures.json').read_text())
    assert any(failure.get('call_id') == call['id'] and failure['error'] == error for failure in failures)
    assert {p: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before
