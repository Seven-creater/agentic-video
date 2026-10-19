"""Explicit known-502 recovery is bounded; unknown calls never replay."""
from io import BytesIO
import json
from unittest.mock import patch
import urllib.error

import pytest

from omni_story.discovery.hot_capture import CaptureAPIError, Wellbyte
from omni_story.discovery import hot_capture
from omni_story.library.state import LibraryStopped


ENDPOINT = '/v1/douyin/billboard/fetch_hot_challenge_list'
PARAMS = {'page': 1, 'page_size': 20}


def test_changed_example_selection_cannot_reuse_old_comparison_or_ledger(tmp_path):
    old=[dict(id=str(i),sha256=str(i)*64) for i in (1,2,3)]
    folder=hot_capture._legacy_template_folder(tmp_path,'123',None)
    folder.mkdir();(folder/'concat.txt').write_text("file '1.mp4'\nfile '2.mp4'\nfile '3.mp4'\n")
    state=folder/'audio_review/state.json';state.parent.mkdir();state.write_text('{"status":"received"}')
    bound=hot_capture._template_folder(tmp_path,'123',None,old)
    assert bound!=folder
    assert hot_capture._template_folder(tmp_path,'123',None,old)==bound
    new=[old[0],old[2],dict(id='4',sha256='4'*64)]
    assert hot_capture._template_folder(tmp_path,'123',None,new)!=folder
    assert hot_capture._template_folder(tmp_path,'123',None,new)!=bound
    changed_bytes=[dict(old[0],sha256='f'*64),*old[1:]]
    assert hot_capture._template_folder(tmp_path,'123',None,changed_bytes)!=bound
    assert state.read_text()=='{"status":"received"}'
    state.write_text('{"status":"uncertain"}')
    with pytest.raises(LibraryStopped,match='existing_unknown'):
        hot_capture._template_folder(tmp_path,'123',None,new)
    state.write_text('{"status":"received"}')


def test_comparison_rejects_changed_source_manifest_before_using_cached_clips(tmp_path):
    media=[dict(id='123',sha256='a'*64)]
    (tmp_path/'comparison_sources.json').write_text(json.dumps([dict(id='123',sha256='b'*64)]))
    with patch.object(hot_capture,'probe_media') as probe:
        with pytest.raises(LibraryStopped,match='comparison_sources_changed'):
            hot_capture._comparison(media,tmp_path)
        probe.assert_not_called()


@pytest.mark.parametrize('case,expected_requests', [
    ('missing_then_valid', 2), ('missing_twice', 2), ('negative_valid', 1),
    ('wrong_type', 1), ('other_missing', 1), ('http_400', 1), ('stale_failure', 1), ('context_missing', 1)])
def test_only_missing_limitations_gets_one_bound_corrective_assessment(tmp_path, monkeypatch, case, expected_requests):
    from omni_story.library import audio_review, server_cli
    from omni_story.library.media import sha256_file
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'offline-fixture-key')
    monkeypatch.setattr(server_cli, 'settings', lambda _: {})
    monkeypatch.setattr(server_cli, 'load_history', lambda _: {})
    proxy = tmp_path/'comparison-fixture.bin';proxy.write_bytes(b'No actual media or model used.')
    monkeypatch.setattr(hot_capture, '_comparison', lambda media, folder: (proxy, []))
    original = dict(same_template=False,shared_pattern='',member_ids=[],member_evidence=[],
                    representative_id=None,representative_brief=None,limitations=[])
    issued=[]
    def review(video, output, **kwargs):
        output.mkdir(parents=True, exist_ok=True)
        result=dict(original)
        if len(issued)==0 and case in {'missing_then_valid','missing_twice','stale_failure','context_missing'}:result.pop('limitations')
        if len(issued)==1 and case=='missing_twice':result.pop('limitations')
        if case=='wrong_type':result['limitations']='invalid-array'
        if case=='other_missing':result.pop('shared_pattern')
        state=output/'state.json';state.write_text(json.dumps(dict(status='received')))
        response=output/'response.json';response.write_text(json.dumps(result))
        issued.append(dict(path=output,prompt=kwargs['prompt'],state=state.read_bytes(),response=response.read_bytes()))
        return dict(parsed=result,http_status=400 if case=='http_400' else 200,parse_error=None,
                    status='received',response_sha256=sha256_file(response))
    monkeypatch.setattr(audio_review,'review_audio_video',review)
    args=(tmp_path,tmp_path,dict(sentence_id='123',sentence='fixture-only'),[],[])
    if case=='stale_failure':
        folder=hot_capture._template_folder(tmp_path,'123',None,[]);folder.mkdir()
        (folder/'template_protocol_failed.json').write_text('{"ledger_path":"unrelated-ledger"}')
    if case in {'missing_then_valid','negative_valid'}:
        result=hot_capture._verify_template(*args)
        assert result['same_template'] is False  # A corrected shape is never a positive verdict.
        if case=='missing_then_valid':
            proof=result['protocol_correction']
            assert proof['original_ledger_sha256']==sha256_file(issued[0]['path']/'state.json')
            assert proof['original_response_sha256']==sha256_file(issued[0]['path']/'response.json')
            assert proof['max_corrective_requests']==1
            assert issued[0]['path']!=issued[1]['path']
            assert issued[0]['prompt']!=issued[1]['prompt']
    else:
        error=LibraryStopped if case=='stale_failure' else ValueError
        match='original_failure_binding_changed' if case=='stale_failure' else 'capture_template_received_invalid'
        with pytest.raises(error,match=match):
            hot_capture._verify_template(*args,correct_missing_limitations=case!='context_missing')
    assert len(issued)==expected_requests
    assert (issued[0]['path']/'state.json').read_bytes()==issued[0]['state']
    assert (issued[0]['path']/'response.json').read_bytes()==issued[0]['response']


def test_received_action_recall_requires_novel_fact_hits_and_preserves_limits(monkeypatch):
    from omni_story.library.visual_library import VisualLibrary
    previous=[dict(observation_id='old')]
    def card(identity,kind='visual_fact',strength='lexical',terms=None):
        return dict(observation_id=identity,source_sha256='a'*64,source_start_s=10,source_end_s=20,
            summary='Recorded cooking scene',affordances=['conditional use'],limitations=['silent observation'],
            uncertainties=['utensil not identified'],match_strength=strength,
            reasons=[dict(kind=kind,matched_terms=terms or ['揉面'])])
    monkeypatch.setattr(VisualLibrary,'__init__',lambda *_:None)
    monkeypatch.setattr(VisualLibrary,'search',lambda *a,**k:[card('new'),card('use-only','conditional_use'),card('weak',terms=['面'])])
    review=dict(representative_id='one',representative_brief=dict(adaptation_anchors=['揉面']),
        member_evidence=[dict(id='one',events=[dict(visible='actual kneading')])])
    result=hot_capture._observed_library_candidates('unused',review,previous)
    assert [r['observation_id'] for r in result]==['new','old']
    assert result[0]['limitations']==['silent observation'] and result[0]['uncertainties']==['utensil not identified']
    assert previous==[dict(observation_id='old')]
    monkeypatch.setattr(VisualLibrary,'search',lambda *a,**k:[card('old')])
    assert hot_capture._observed_library_candidates('unused',review,previous) is None


def test_library_recall_admission_pins_one_context_across_new_capture_runs(tmp_path,monkeypatch):
    from omni_story.library.media import sha256_file
    ledger=tmp_path/'template_original/audio_review/state.json';ledger.parent.mkdir(parents=True)
    ledger.write_text('{"status":"received"}');response=ledger.parent/'response.json';response.write_text('{}')
    review=dict(ledger_path=str(ledger),ledger_sha256=sha256_file(ledger),response_sha256=sha256_file(response))
    previous=[dict(observation_id='old')];media=[dict(id='actual-source',sha256='a'*64)]
    palettes=[[dict(observation_id='first-new')],[dict(observation_id='later-new')]]
    with patch.object(hot_capture,'_observed_library_candidates',side_effect=palettes) as recall:
        first,proof=hot_capture._library_recall_admission('library',review,previous,media)
        second,saved=hot_capture._library_recall_admission('library',review,previous,media)
        assert first==second==palettes[0] and saved==proof
        assert recall.call_count==1  # A later palette cannot buy another assessment.
    response.write_text('{"changed":true}')
    with pytest.raises(LibraryStopped,match='original_binding_changed'):
        hot_capture._library_recall_admission('library',review,previous,media)


def http_error(status=502):
    return urllib.error.HTTPError('https://api.wellbyte.net' + ENDPOINT,
                                 status, 'failure', {}, BytesIO(b'error code: 502\n'))


class Success(BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json', 'X-Request-Id': 'offline-request'}


def failed_api(tmp_path, status=502):
    with patch('omni_story.discovery.hot_capture._key', return_value='offline-key'):
        api = Wellbyte(tmp_path, tmp_path/'capture')
    with patch('urllib.request.urlopen', side_effect=http_error(status)):
        with pytest.raises(LibraryStopped, match='capture_non_json_response'):
            api.call('hot_challenges', ENDPOINT, PARAMS)
    return api


def test_explicit_502_recovery_preserves_failure_and_reuses_success(tmp_path):
    api = failed_api(tmp_path)
    original_row = dict(api.calls[0])
    original_raw = (api.output/'hot_challenges.response.json').read_bytes()
    with patch('urllib.request.urlopen', return_value=Success(b'{"code":0,"data":{}}')) as request:
        with pytest.raises(CaptureAPIError):
            api.call('hot_challenges', ENDPOINT, PARAMS)
        request.assert_not_called()
        result = api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        assert api.call('hot_challenges', ENDPOINT, PARAMS) == result
        assert request.call_count == 1
    saved = json.loads(api.ledger.read_text())
    assert saved[0] == original_row
    assert (api.output/'hot_challenges.response.json').read_bytes() == original_raw
    assert saved[1]['status'] == 'received'
    assert saved[1]['retry_of_response_sha256'] == original_row['response_sha256']
    assert saved[1]['response_path'] != original_row['response_path']


def test_explicit_recovery_stops_after_three_attempts(tmp_path):
    api = failed_api(tmp_path)
    with patch('urllib.request.urlopen', side_effect=lambda *a, **k: http_error()) as request:
        with pytest.raises(LibraryStopped, match='capture_non_json_response'):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        with pytest.raises(LibraryStopped, match='capture_non_json_response'):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        with pytest.raises(CaptureAPIError):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        assert request.call_count == 2
    assert len(api.calls) == 3
    assert len({call['response_path'] for call in api.calls}) == 3


def test_recovery_after_gateway_returns_preserves_both_failed_attempts(tmp_path):
    api = failed_api(tmp_path)
    with patch('urllib.request.urlopen', side_effect=http_error()):
        with pytest.raises(LibraryStopped):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
    original = json.loads(api.ledger.read_text())
    raw = [open(row['response_path'], 'rb').read() for row in original]
    with patch('urllib.request.urlopen', return_value=Success(b'{"code":0,"data":{}}')) as request:
        with pytest.raises(CaptureAPIError):
            api.call('hot_challenges', ENDPOINT, PARAMS)
        api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        api.call('hot_challenges', ENDPOINT, PARAMS)
        assert request.call_count == 1
    assert api.calls[:2] == original
    assert [open(row['response_path'], 'rb').read() for row in original] == raw
    assert api.calls[2]['attempt'] == 3
    assert api.calls[2]['response_headers']['X-Request-Id'] == 'offline-request'


@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 429, 500, 503, 504])
def test_other_failed_statuses_cannot_be_retried(tmp_path, status):
    api = failed_api(tmp_path, status)
    with patch('urllib.request.urlopen') as request:
        with pytest.raises(CaptureAPIError):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        request.assert_not_called()


@pytest.mark.parametrize('status', ['submitted', 'uncertain'])
def test_unknown_at_call_time_stops_explicit_recovery(tmp_path, status):
    api = failed_api(tmp_path)
    api.calls.append(dict(name='other', status=status))
    with patch('urllib.request.urlopen') as request:
        with pytest.raises(LibraryStopped, match='unknown_no_replay'):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        request.assert_not_called()


@pytest.mark.parametrize('change', ['endpoint', 'params', 'method', 'raw'])
def test_changed_binding_or_raw_stops_explicit_recovery(tmp_path, change):
    api = failed_api(tmp_path)
    endpoint, params, method = ENDPOINT, PARAMS, 'GET'
    if change == 'endpoint':
        endpoint += '_changed'
    elif change == 'params':
        params = {'page': 2, 'page_size': 20}
    elif change == 'method':
        method = 'POST'
    else:
        (api.output/'hot_challenges.response.json').write_bytes(b'changed')
    with patch('urllib.request.urlopen') as request:
        with pytest.raises(LibraryStopped):
            api.call('hot_challenges', endpoint, params, method=method, retry_failed=True)
        request.assert_not_called()


def test_network_result_during_recovery_stays_unknown(tmp_path):
    api = failed_api(tmp_path)
    with patch('urllib.request.urlopen', side_effect=TimeoutError) as request:
        with pytest.raises(LibraryStopped, match='network_result_unknown'):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        with pytest.raises(LibraryStopped, match='unknown_no_replay'):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
        assert request.call_count == 1
    assert api.calls[-1]['status'] == 'uncertain'


def test_network_error_evidence_redacts_credentials(tmp_path):
    api = failed_api(tmp_path)
    error = urllib.error.URLError('TLS failure; credential=offline-key')
    with patch('urllib.request.urlopen', side_effect=error):
        with pytest.raises(LibraryStopped, match='network_result_unknown'):
            api.call('hot_challenges', ENDPOINT, PARAMS, retry_failed=True)
    saved = json.loads(api.ledger.read_text())
    assert saved[-1]['status'] == 'uncertain'
    assert saved[-1]['error_reason_type'] == 'str'
    assert 'TLS failure' in saved[-1]['error_detail']
    assert 'offline-key' not in saved[-1]['error_detail']
