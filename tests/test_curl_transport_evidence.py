"""No live requests: preserve provider evidence without replay or URL disclosure."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omni_story import api
from omni_story.library import tts


def test_timeout_preserves_redacted_headers_and_partial_evidence_without_retry(tmp_path,monkeypatch):
    calls=[];key='synthetic-private-key'
    def failed(argv,**kw):
        calls.append(argv)
        assert key not in repr(argv)
        header=Path(argv[argv.index('--dump-header')+1]);header.write_text('HTTP/1.1 200 OK\nx-request-id: actual-request-123\n')
        return SimpleNamespace(returncode=28,stdout=b'{"partial":\n200',stderr=('timeout '+key+' https://a.aliyuncs.com/?Signature=secret').encode())
    monkeypatch.setattr(api.subprocess,'run',failed)
    with pytest.raises(api.APITransportError) as caught:
        api.curl_json('curl','https://example.test',key,'POST',dict(text='hello'))
    error=caught.value
    assert len(calls)==1 and error.response is None
    assert error.evidence['http_status']==200 and error.evidence['request_id']=='actual-request-123'
    assert not error.evidence['complete_json']
    assert key not in json.dumps(error.evidence) and 'Signature=' not in json.dumps(error.evidence)
    assert not Path(calls[0][calls[0].index('--dump-header')+1]).exists()


def test_tts_uses_single_native_stream_request(monkeypatch):
    body=dict(request_id='original-response-123',output=dict(audio=dict(url='https://dashscope-result.oss-cn-beijing.aliyuncs.com/voice.wav?Signature=secret')))
    sent=[]
    def request(endpoint,key,payload,**kw):
        sent.append((endpoint,key,payload,kw))
        return dict(http_status=200,body_text=json.dumps(body))
    monkeypatch.setattr(tts,'request_tts',request)
    client=object.__new__(tts.QwenTTS);client.curl='curl';client.key='fake-key';client.timeout_s=120
    response=client.request(dict(model=tts.MODEL))
    assert len(sent)==1 and sent[0]==(tts.ENDPOINT,'fake-key',dict(model=tts.MODEL),dict(timeout_s=120))
    assert json.loads(response['body_text'])==body


@pytest.mark.parametrize('status,body',[(200,dict(output=dict(audio=dict(url='https://a.aliyuncs.com/a.wav')))),
    (200,dict(request_id='id',output=dict(audio=dict(url='https://attacker.test/a.wav')))),
    (503,dict(request_id='id',code='InternalError'))])
def test_partial_or_error_tts_outcomes_stay_unknown_without_retry(monkeypatch,status,body):
    error=api._transport_error(28,json.dumps(body).encode()+b'\n'+str(status).encode(),b'', '', 'fake',1.0)
    calls=[]
    def request(*args,**kw):calls.append(1);raise error
    monkeypatch.setattr(tts,'request_tts',request)
    client=object.__new__(tts.QwenTTS);client.curl='curl';client.key='fake';client.timeout_s=120
    with pytest.raises(api.APITransportError):client.request({})
    assert len(calls)==1


def test_transport_unknown_receipt_remains_uncertain_and_cannot_be_replayed(tmp_path):
    error=api._transport_error(28,b'\n000',b'No response','', 'fake-key',120)
    class Transport:
        calls=0
        def request(self,payload):self.calls+=1;raise error
    client=Transport()
    with pytest.raises(tts.TTSUnknown):tts.synthesize_line('原文','Ethan',tmp_path,line_id='original',transport=client)
    saved=json.loads((tmp_path/'original/state.json').read_text())
    assert saved['status']=='uncertain' and saved['request_count']==1 and not saved['audio_verified']
    assert saved['transport_evidence']['http_status'] is None and saved['transport_evidence']['response_bytes']==0
    with pytest.raises(tts.TTSUnknown,match='no_replay'):tts.synthesize_line('原文','Ethan',tmp_path,line_id='original',transport=client)
    assert client.calls==1
