from io import BytesIO
import json
from unittest.mock import patch

import pytest

from omni_story import api, temporary_media
from omni_story.library import audio_review


def policy(host='https://test.oss-cn-beijing.aliyuncs.com'):
    response = BytesIO(json.dumps(dict(data=dict(upload_host=host, upload_dir='account/model',
        oss_access_key_id='private-id', signature='private-signature', policy='private-policy',
        x_oss_object_acl='private', x_oss_forbid_overwrite='true'))).encode())
    response.status = 200
    return response


def test_official_upload_streams_exact_complete_video_and_keeps_secrets_off_argv(tmp_path):
    path = tmp_path/'actual.mp4'; path.write_bytes(b'actual full sound and pictures')
    class Connection:
        def __init__(self, host, **kwargs): self.data=b''; self.headers={}
        def putrequest(self, method, target): assert (method,target)==('POST','/')
        def putheader(self,k,v): self.headers[k]=v
        def endheaders(self): pass
        def send(self,data): self.data+=data
        def getresponse(self):
            assert len(self.data)==int(self.headers['Content-Length'])
            assert path.read_bytes() in self.data
            assert b'name="file"' in self.data
            response=BytesIO(b'');response.status=200;return response
        def close(self): pass
    with patch.object(temporary_media,'urlopen',return_value=policy()) as request, \
         patch.object(temporary_media.http.client,'HTTPSConnection',Connection):
        url=temporary_media.upload_video(path,'synthetic-key','qwen3.8-omni-flash')
    assert url.startswith('oss://account/model/') and url.endswith('.mp4')
    assert 'synthetic-key' not in request.call_args.args[0].full_url


@pytest.mark.parametrize('host',['http://test.aliyuncs.com','https://aliyuncs.com.evil.test','https://user:pw@test.aliyuncs.com'])
def test_upload_policy_cannot_redirect_credentials_or_media(tmp_path,host):
    path=tmp_path/'actual.mp4';path.write_bytes(b'actual')
    with patch.object(temporary_media,'urlopen',return_value=policy(host)), \
         patch.object(temporary_media.http.client,'HTTPSConnection') as connection:
        with pytest.raises(ValueError,match='official_https_upload_host_required'):
            temporary_media.upload_video(path,'synthetic-key','qwen3.8-omni-flash')
        connection.assert_not_called()


def test_large_api_input_uses_bound_official_upload_and_resolution_header(tmp_path,monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY','synthetic-key')
    monkeypatch.delenv('DASHSCOPE_BASE_URL',raising=False)
    path=tmp_path/'large.mp4';path.write_bytes(b'x'*7_500_000)
    with patch.object(temporary_media,'upload_video',return_value='oss://account/bound.mp4') as upload, \
         patch.object(api,'curl_json',return_value=dict(http_status=200,body_text='oss://account/bound.mp4')) as model:
        with pytest.raises(ValueError,match='inline_video_exceeds'):
            api.QwenAPI().request('Return JSON',media=path)
        model.assert_not_called();upload.assert_not_called()
        result=api.QwenAPI(temporary_upload=True).request('Return JSON',media=path)
    upload.assert_called_once_with(path,'synthetic-key','qwen3.8-omni-flash')
    assert model.call_args.kwargs['headers']==('X-DashScope-OssResourceResolve: enable',)
    assert model.call_args.args[4]['messages'][0]['content'][1]['video_url']['url']=='oss://account/bound.mp4'
    assert result['body_text']=='[REDACTED_MEDIA_URL]'


def test_large_review_retains_write_before_send_and_unknown_guard(tmp_path,monkeypatch):
    path=tmp_path/'large.mp4';path.write_bytes(b'x'*7_500_000)
    monkeypatch.setattr(audio_review,'probe_media',lambda p:dict(duration_s=742.141,streams=[dict(index=1,codec_type='audio')]))
    class UploadAPI:
        temporary_upload=True
        cfg=dict(model='qwen3.8-omni-flash',media_transport='official_account_model_bound_upload_48h')
        endpoint='https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
        calls=0
        def request(self,prompt,*,media):
            self.calls+=1
            assert json.loads((tmp_path/'review/state.json').read_text())['status']=='submitted'
            raise TimeoutError('no replay')
    runner=UploadAPI()
    for _ in range(2):
        with pytest.raises(RuntimeError,match='request_outcome_unknown_no_replay'):
            audio_review.review_audio_video(path,tmp_path/'review',prompt='Return JSON',api=runner,allow_temporary_upload=True)
    assert runner.calls==1
    request=json.loads((tmp_path/'review/request.json').read_text())
    assert request['duration_s']==742.141 and request['configuration']['media_transport'].startswith('official_')
