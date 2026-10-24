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
