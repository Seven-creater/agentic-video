"""Feature-content boundaries and the single task-wide TTS resend exception."""
from copy import deepcopy
import hashlib
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from omni_story.api import APITransportError
from omni_story.library import contracts, pipeline, prompts, server_cli, tts, tts_http, tts_retry
from omni_story.library.run_outcome import write_run_outcome
from omni_story.library.state import LibraryStopped

SHA = 'cf2fec1024243517c1811dce0bba3c1790af887823ab93ec543627606323b64e'
SID = 'src_cf2fec1024243517'
CONTENT = dict(content_start_s=66.0, content_end_s=4999.0,
    excluded=[dict(start_s=0.0, end_s=66.0, kind='logo'),
              dict(start_s=4999.0, end_s=5694.7, kind='end_credits')], verified_by='Opus')


@pytest.fixture
def content(tmp_path):
    class LazyContentModule:
        def __getattr__(self, name):
            assert importlib.util.find_spec('omni_story.library.content_ranges') is not None, 'content ranges support is required'
            return getattr(importlib.import_module('omni_story.library.content_ranges'), name)
    module = LazyContentModule()
    catalog = dict(sources=[dict(source_id=SID, sha256=SHA, duration_s=5694.7, audio_stream_index=1, path='original-film.mp4')])
    data = dict(schema_version='content_ranges_v1', sources={SHA: deepcopy(CONTENT)})
    path = tmp_path/'content_ranges.json'
    path.write_text(json.dumps(data))
    return module, catalog, data, path


def test_loader_valid_file_and_sha(content):
    module, catalog, data, path = content
    result = module.load_content_ranges(path.parent, catalog)
    assert result == dict(data, file_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize('change', ['missing_file', 'missing_source', 'negative', 'end_over_duration',
                                       'nan', 'infinity', 'reversed', 'bool', 'excluded_over_duration'])
def test_loader_rejects_invalid_data(content, change):
    module, catalog, data, path = content
    if change == 'missing_file':
        path.unlink()
    else:
        row = data['sources'][SHA]
        if change == 'missing_source':
            data['sources'] = {}
        elif change == 'excluded_over_duration':
            row['excluded'][0]['end_s'] = 6000
        else:
            row.update({'negative': dict(content_start_s=-1), 'end_over_duration': dict(content_end_s=6000),
                'nan': dict(content_start_s=float('nan')), 'infinity': dict(content_end_s=float('inf')),
                'reversed': dict(content_start_s=5000), 'bool': dict(content_start_s=True)}[change])
        path.write_text(json.dumps(data))
    with pytest.raises(LibraryStopped, match='visual_library:content_ranges_missing_or_invalid:'):
        module.load_content_ranges(path.parent, catalog)


def test_start_missing_ranges_stops_before_any_configuration_or_model_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(server_cli, 'settings', lambda *a: calls.append('configuration'))
    monkeypatch.setattr(server_cli, 'OpenCodeMCP', lambda *a, **kw: calls.append('model'))
    monkeypatch.setattr(pipeline, '_catalog', lambda *a: dict(sources=[dict(sha256=SHA, duration_s=5694.7)]))
    args = SimpleNamespace(output=tmp_path, library=tmp_path, visual_library=tmp_path, creative_audio=True, home=tmp_path)
    with pytest.raises(LibraryStopped, match='visual_library:content_ranges_missing_or_invalid:'):
        server_cli._run(args)
    assert calls == []
    outcome = json.loads((tmp_path/'run_outcome.json').read_text())
    assert outcome['outcome'] == 'stopped' and outcome['raised_at']
    assert outcome['error'].startswith('visual_library:content_ranges_missing_or_invalid:')


def bound_catalog(content):
    module, catalog, data, path = content
    return module.bind_content_ranges(catalog, module.load_content_ranges(path.parent, catalog))


def search(start=5006, end=5010, revisit=False):
    row = dict(source_id=SID, start_s=start, end_s=end, question='确认可见动作', role_ids=[])
    if revisit:
        row['revisit_window_id'] = 'vl_375f00a8c821d094'
    return dict(reason='只选可见动作', windows=[row])


@pytest.mark.parametrize('revisit', [False, True])
def test_search_rejects_real_end_credits_with_precise_violation(content, revisit):
    catalog = bound_catalog(content)
    with pytest.raises(ValueError) as raised:
        pipeline._validate_library_search(search(revisit=revisit), catalog, [], max_windows=12,
            received_windows=[dict(window_id='vl_375f00a8c821d094', source_sha256=SHA,
                source_start_s=4980, source_end_s=5040, status='watched')])
    violation = raised.value.violation
    assert violation['path'] == '$.windows[0].start_s' and violation['value'] == 5006
    assert violation['expected'] == [66.0, 4999.0] and violation['repair_hint']


def rough(start, end):
    window = dict(window_id='vl_375f00a8c821d094', source_id=SID, source_sha256=SHA,
                  source_start_s=4980, source_end_s=5040, status='watched')
    window['observation'] = dict(window_id=window['window_id'], source_id=SID, roles=[], events=[],
        usable_ranges=[dict(local_in_s=0, local_out_s=60, role_ids=[], event_indices=[],
                            camera='墨画背景上的实际文字', continuity_notes='连续')], uncertainties=[])
    value = dict(reference_sha256='a'*64, focus_role_id=None, segments=[dict(segment_id='seg', slot_id='slot',
        source_id=SID, window_id=window['window_id'], role_ids=[], source_in_s=start, source_out_s=end,
        speed=1, look='none', framing='fit')], slots=[dict(slot_id='slot', intended_takeaway='可见动作',
        segment_ids=['seg'])], audio_mode='silent', width=640, height=360, fps=30, limitations=[])
    return value, window


def test_rough_plan_rejects_u3_heihu02_5006_to_5010(content):
    value, window = rough(5006, 5010)
    with pytest.raises(ValueError) as raised:
        contracts.validate_plan(value, bound_catalog(content), [window], 'a'*64, allow_unfocused=True)
    assert raised.value.violation == dict(path='$.segments[0].source_in_s', value=5006,
        expected=[66.0, 4999.0], repair_hint=raised.value.violation['repair_hint'])
    assert raised.value.violation['repair_hint']


def test_content_range_is_accepted_by_search_and_rough_plan(content):
    catalog = bound_catalog(content)
    assert contracts.validate_search(search(4985, 4990), catalog)
    value, window = rough(4985, 4990)
    assert contracts.validate_plan(value, catalog, [window], 'a'*64, allow_unfocused=True)


@pytest.mark.parametrize('kind', ['search', 'plan', 'fine', 'production_search', 'production_plan', 'production_fine'])
def test_prompts_share_recorded_content_range(content, kind):
    from omni_story.library.clean_chain import ROUGH_PROMPTS
    catalog = bound_catalog(content)
    context = dict(catalog=catalog)
    window = dict(source_id=SID, content_range=CONTENT)
    text = {'search': lambda: prompts.search_prompt(context), 'plan': lambda: prompts.plan_prompt(context),
        'fine': lambda: prompts.fine_prompt(window, context),
        'production_search': lambda: ROUGH_PROMPTS.search_prompt(context),
        'production_plan': lambda: ROUGH_PROMPTS.plan_prompt(context),
        'production_fine': lambda: ROUGH_PROMPTS.for_round(0).fine_prompt(window, context)}[kind]()
    assert '66.0' in text and '4999.0' in text and 'content_range_s' in text
    assert 'logos' in text and 'title cards' in text and 'end credits' in text


def test_catalog_hides_outside_windows_and_exposes_partial_usable_range(content, tmp_path):
    from omni_story.library.visual_library import VisualLibrary, compact_candidates
    catalog = bound_catalog(content)
    store = VisualLibrary(tmp_path/'catalog', content_sources=catalog)
    for index, (start, end) in enumerate([(0, 60), (5006, 5010), (4980, 5040), (100, 160)]):
        observation = dict(summary='动作', roles=[], events=[], usable_ranges=[], limitations=[], uncertainties=[])
        window = dict(window_id=f'w{index}', source_id=SID, source_sha256=SHA, source_start_s=start,
            source_end_s=end, status='watched', source_visual_policy='silent_subtitle_masked', observation=observation)
        store.add_observation(dict(observation_id=f'o{index}', window=window,
            evidence=dict(status='received', ledger_path='/original', call_id='received',
                          request_sha256='a'*64, response_sha256='b'*64)))
    cards = store.search('')
    assert {row['observation_id'] for row in cards} == {'o2', 'o3'}
    partial = next(row for row in compact_candidates(cards) if row['observation_id'] == 'o2')
    assert partial['usable_source_range_s'] == [4980, 4999.0]


def test_recorded_ranges_survive_library_change_and_old_tasks_are_unrestricted(content):
    module, catalog, data, path = content
    snapshot = module.recorded_content_ranges(path.parent, path.parent, catalog)
    (path.parent/'library_state.json').write_text(json.dumps(dict(input_lock=dict(configuration=dict(content_ranges=snapshot)))))
    path.unlink()
    assert module.recorded_content_ranges(path.parent, path.parent, catalog) == snapshot
    (path.parent/'library_state.json').write_text(json.dumps(dict(input_lock=dict(configuration={}))))
    assert module.recorded_content_ranges(path.parent, path.parent, catalog) is None
    assert module.content_range_for(catalog['sources'][0]) is None


def test_fine_source_ranges_and_rendered_parent_time_domains(content):
    from omni_story.library import visual_story_trial as trial
    catalog = bound_catalog(content)
    source = catalog['sources'][0]
    request = dict(target='parent', start_s=5006, end_s=5010, step_s=.2, question='可见事实')
    auth = dict(parent=source, reference=source)
    with pytest.raises(ValueError) as raised:
        trial.inspect_check([request], auth)
    assert raised.value.violation['path'] == '$.inspect[0].start_s'
    trial.inspect_check([request], dict(auth, parent=dict(source, time_domain='output')))
    value, window = rough(5006, 5010)
    value['segments'][0].update(window_id='full_parent_observed', reason='可见动作', contribution_to=['beat'])
    auth.update(target_duration_s=4, target_tolerance_s=0)
    with pytest.raises(ValueError) as raised:
        trial.plan_check(value, auth, source, [dict(id='beat')], reference_audio_required=False)
    assert raised.value.violation['path'] == '$.segments[0].source_in_s'
    trial.plan_check(value, auth, dict(source, time_domain='output'), [dict(id='beat')], reference_audio_required=False)


SIGNATURE = dict(transport='native_https_sse_v1', phase='headers', http_status=None,
                 response_bytes=0, events=0, failure='idle_timeout', connected_s=.05, sent_s=.1)


@pytest.fixture
def task(tmp_path):
    (tmp_path/'chain_started').write_text('reference_rough_skill_v1')
    return tmp_path


class Transport:
    def __init__(self, errors=(), before_send=None):
        self.errors, self.calls, self.before_send = list(errors), 0, before_send

    def request(self, payload):
        self.calls += 1
        if self.before_send:
            self.before_send(self.calls)
        if self.errors:
            error = self.errors.pop(0)
            if error:
                raise error
        return dict(http_status=200, body_text=json.dumps(dict(request_id='actual-replacement',
            output=dict(audio=dict(url='https://dashscope-result.oss-cn-beijing.aliyuncs.com/a.wav')))))

    def download(self, url, path):
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 12000)


def hung(**changes):
    return APITransportError('tts_idle_timeout', dict(SIGNATURE, **changes))


def speak(task, client, line='first', fine=False):
    assert getattr(tts_retry, 'AUTO_POLICY', None) == 'automatic_zero_byte_header_timeout_v1'
    output = task/('skill_finecut/creative_audio/tts' if fine else 'rough_audio/tts')
    return tts.synthesize_line('先吃饭。', 'Ethan', output, line_id=line, transport=client)


def test_exact_hang_resends_once_archives_original_and_claims_before_post(task):
    path = task/'rough_audio/tts/first/state.json'
    original_bytes = []
    def before_send(count):
        if count == 2:
            archive = path.with_name('original_uncertain.json')
            original_bytes.append(path.read_bytes())
            assert archive.read_bytes() == path.read_bytes()
            ledger = json.loads((task/'tts_automatic_resend.json').read_text())
            assert ledger['additional_requests_claimed'] == 1
    client = Transport([hung()], before_send)
    result = speak(task, client)
    assert client.calls == 2 and result['request_count'] == 2 and result['audio_verified']
    assert path.with_name('original_uncertain.json').read_bytes() == original_bytes[0]
    auth = json.loads(path.with_name('automatic_resend_authorization.json').read_text())
    assert auth['kind'] == tts_retry.AUTO_POLICY and auth['max_additional_requests'] == 1
    assert auth['allow_possible_duplicate_charge'] is True and auth['signature'] == SIGNATURE
    assert auth['original_ledger_sha256'] == hashlib.sha256(original_bytes[0]).hexdigest()
    tts_retry.validate_replacement(path)


def test_second_hung_line_across_fine_audio_has_no_resend(task):
    client = Transport([hung(), None, hung()])
    speak(task, client)
    with pytest.raises(tts.TTSUnknown):
        speak(task, client, line='second', fine=True)
    assert client.calls == 3


@pytest.mark.parametrize('changes', [dict(response_bytes=12), dict(events=1), dict(http_status=200),
    dict(failure='total_timeout'), dict(phase='connect', failure='connect_timeout'),
    dict(phase='connect', failure='ConnectionError'), dict(phase='body', response_bytes=12),
    dict(transport='curl', curl_exit_code=28)])
def test_other_signatures_never_resend(task, changes):
    client = Transport([hung(**changes)])
    with pytest.raises(tts.TTSUnknown):
        speak(task, client)
    assert client.calls == 1 and not (task/'tts_automatic_resend.json').exists()


@pytest.mark.parametrize('replacement_error', [hung(), TimeoutError('connect'), ValueError('invalid reply')])
def test_failed_resend_keeps_both_attempts_and_run_outcome(task, replacement_error):
    client = Transport([hung(), replacement_error])
    with pytest.raises(tts.TTSUnknown) as raised:
        speak(task, client)
    assert client.calls == 2
    outcome = write_run_outcome(task, error=raised.value)
    lineage = outcome['tts_automatic_resend']
    assert json.loads(Path(lineage['original']['path']).read_text())['status'] == 'uncertain'
    assert json.loads(Path(lineage['attempt_path']).read_text())['status'] == 'uncertain'
    with pytest.raises(tts.TTSUnknown):
        speak(task, client)
    assert client.calls == 2


def test_resume_after_successful_resend_cannot_spend_another_quota(task):
    client = Transport([hung()])
    assert speak(task, client)['request_count'] == 2
    before = (task/'tts_automatic_resend.json').read_bytes()
    assert speak(task, client)['reused'] and client.calls == 2
    client.errors = [hung()]
    with pytest.raises(tts.TTSUnknown):
        speak(task, client, line='resumed', fine=True)
    assert client.calls == 3 and (task/'tts_automatic_resend.json').read_bytes() == before


def test_header_idle_timeout_is_20_and_body_remains_60(monkeypatch):
    params = inspect.signature(tts_http.request_tts).parameters
    assert params['header_idle_timeout_s'].default == 20 and params['idle_timeout_s'].default == 60
    class Sock:
        def settimeout(self, value):
            timeouts.append(value)
        def shutdown(self, *args):
            pass
    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = Sock()
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            raise TimeoutError()
        def close(self):
            pass
    timeouts = []
    monkeypatch.setattr(tts_http, '_HTTPSConnection', Connection)
    with pytest.raises(APITransportError) as raised:
        tts_http.request_tts(tts.ENDPOINT, 'test-key', {})
    evidence = raised.value.evidence
    assert timeouts == [60, 20]
    assert evidence['header_idle_timeout_s'] == 20 and evidence['idle_timeout_s'] == 60


def test_pipeline_pins_ranges_before_constructing_model_and_replays_that_snapshot(content, tmp_path, monkeypatch):
    module, catalog, data, path = content
    output = tmp_path/'task'
    monkeypatch.setattr(pipeline, '_catalog', lambda *a: deepcopy(catalog))
    captured = []
    class ReachedModel(Exception):
        pass
    def factory(state):
        captured.append(deepcopy(state.input_lock['configuration']['content_ranges']))
        raise ReachedModel()
    options = dict(model_factory=factory, asr=False,
        provider_config=dict(creative_audio=True, visual_library_root=str(path.parent)))
    with pytest.raises(ReachedModel):
        pipeline.execute(tmp_path, tmp_path, output, **options)
    assert captured[0]['sources'][SHA] == CONTENT
    assert captured[0]['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    with pytest.raises(ReachedModel):
        pipeline.execute(tmp_path, tmp_path, output, **options)
    assert captured == [captured[0], captured[0]]



@pytest.mark.parametrize('start,end,field', [(4998,5001,'end_s'), (0,10,'start_s')])
def test_content_boundary_paths_cover_outpoint_and_opening_logos(content, start, end, field):
    with pytest.raises(ValueError) as raised:
        contracts.validate_search(search(start, end), bound_catalog(content))
    assert raised.value.violation['path'] == '$.windows[0].' + field
    assert raised.value.violation['repair_hint']


def test_new_task_cannot_bypass_missing_file_using_supplied_snapshot(content):
    module, catalog, data, path = content
    snapshot = module.load_content_ranges(path.parent, catalog)
    path.unlink()
    with pytest.raises(LibraryStopped, match='content_ranges_missing_or_invalid:'):
        module.recorded_content_ranges(path.parent, path.parent, catalog, snapshot)


def test_task_quota_uses_chain_ledger_even_without_marker_or_with_shared_cache(task):
    (task/'chain_started').unlink()
    (task/'library_state.json').write_text(json.dumps(dict(input_lock=dict(configuration=dict(workflow='reference_rough_skill_v1')))))
    fine = task/'skill_finecut/creative_audio'
    fine.mkdir(parents=True)
    (fine/'library_state.json').write_text(json.dumps(dict(input_lock=dict(configuration=dict(workflow='visual_audio_v1')))))
    client = Transport([hung()])
    assert speak(task, client)['audio_verified']
    assert tts_retry.task_output_for(fine/'tts') == task
    client.errors = [hung()]
    shared_cache = task.parent/(task.name+'_shared_cache')
    with pytest.raises(tts.TTSUnknown):
        tts.synthesize_line('新句子', 'Ethan', shared_cache, line_id='outside_cache', transport=client, task_output=task)
    assert client.calls == 3
    assert not (shared_cache/'tts_automatic_resend.json').exists()
