"""Real local TLS/SSE controls. No external requests or cloud credentials."""
import http.server
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

from omni_story.api import APITransportError
from omni_story.library import tts, tts_http

KEY = 'synthetic-secret-for-tests'
URL = 'https://dashscope-result.oss-cn-beijing.aliyuncs.com/a.wav?Signature=private'


def final(**extra):
    return dict(request_id='real-local-request', output=dict(
        finish_reason='stop', audio=dict(url=URL, data='')), **extra)


def event(value):
    return b'data: ' + json.dumps(value, ensure_ascii=False).encode() + b'\r\n\r\n'


@pytest.fixture(scope='module')
def certificate(tmp_path_factory):
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('local TLS control requires openssl')
    folder = tmp_path_factory.mktemp('tls')
    cert, key = folder/'cert.pem', folder/'key.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1',
                    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                   check=True, capture_output=True, timeout=15)
    return cert, key


@pytest.fixture
def wire(monkeypatch, certificate):
    cert, key = certificate
    calls = []
    selected = {'mode': 'sse', 'reply': final()}
    release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(dict(headers=dict(self.headers), body=json.loads(
                self.rfile.read(int(self.headers['Content-Length'])))))
            try:
                self.respond()
            except (OSError, ssl.SSLError):
                pass

        def respond(self):
            mode = selected['mode']
            if mode == 'no_headers':
                release.wait(2)
                return
            if mode == 'slow_headers':
                for byte in b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n':
                    self.connection.sendall(bytes([byte]))
                    time.sleep(.02)
                return
            streaming = mode not in {'json', '400', '429', '503', 'html400', 'truncated400'}
            status = int(mode) if mode in {'400', '429', '503'} else 400 if mode in {'html400', 'truncated400'} else 200
            self.send_response(status)
            self.send_header('Content-Type', 'text/event-stream' if streaming else 'application/json')
            self.send_header('x-request-id', 'local-header-id')
            self.send_header('Content-Length', '1000000')  # Deliberately never finish the HTTP body.
            self.end_headers()
            if mode == 'idle':
                release.wait(2)
                return
            if mode == 'body_trickle':
                while not release.wait(.03):
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                return
            if mode == 'json':
                self.wfile.write(json.dumps(selected['reply']).encode())
            elif mode in {'400', '429', '503'}:
                self.wfile.write(json.dumps(dict(code='ControlledError', message='safe')).encode())
            elif mode == 'html400':
                self.wfile.write(b'<html>rejected</html>')
                self.wfile.flush()
                self.close_connection = True
                return
            elif mode == 'truncated400':
                self.wfile.write(b'{"code":')
            elif mode == 'partial':
                self.wfile.write(event(dict(request_id='partial-id', output=dict(
                    finish_reason=None, audio=dict(data='YWJj', url='')))))
                self.wfile.flush()
                self.close_connection = True
                return
            elif mode == 'missing_final':
                self.wfile.write(event(selected['reply']))
                self.wfile.flush()
                self.close_connection = True
                return
            elif mode == 'malformed':
                self.wfile.write(b'data: {bad json}\n\n')
            else:
                first = dict(request_id='real-local-request', usage=dict(characters=22),
                             output=dict(finish_reason=None, audio=dict(data='YWJj', url='')),
                             message='中文')
                # Multi-line data, comments, empty events, split CRLF and UTF-8.
                stream = b': heartbeat\r\n\r\ndata:\r\n\r\n' + event(first)
                last = dict(selected['reply'])
                last.pop('request_id', None)
                stream += b'data: {\r\ndata: ' + json.dumps(last, ensure_ascii=False).encode()[1:] + b'\r\n\r\n'
                for byte in stream:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
            self.wfile.flush()
            release.wait(2)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_context = ssl.create_default_context
    monkeypatch.setattr(tts_http.ssl, 'create_default_context',
                        lambda: original_context(cafile=str(cert)))
    monkeypatch.setattr(tts_http, '_resolve', lambda host, port, deadline:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', server.server_address)])

    def call(**options):
        return tts_http.request_tts(options.pop('endpoint', 'https://localhost/test'), KEY,
                                   dict(model=tts.MODEL, input=dict(text='中文', voice='Ethan')),
                                   **dict(dict(timeout_s=1, connect_timeout_s=.5,
                                               idle_timeout_s=.3), **options))
    yield selected, calls, call
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize('mode', ['sse', 'json'])
def test_complete_reply_returns_before_http_body_or_connection_ends(wire, mode):
    selected, calls, call = wire
    selected['mode'] = mode
    started = time.monotonic()
    response = call()
    assert time.monotonic() - started < .8
    body = json.loads(response['body_text'])
    assert body['output']['audio']['url'] == URL
    assert body['request_id'] == 'real-local-request'
    if mode == 'sse':
        assert body['usage']['characters'] == 22
        assert response['transport_evidence']['events'] == 2
    assert len(calls) == 1
    assert calls[0]['headers']['X-DashScope-SSE'] == 'enable'
    assert calls[0]['body']['input']['text'] == '中文'
    evidence = response['transport_evidence']
    assert evidence['phase'] == 'complete' and evidence['first_body_s'] >= evidence['headers_s']
    assert KEY not in json.dumps(evidence) and 'Signature=' not in json.dumps(evidence)


@pytest.mark.parametrize('mode,status', [('400', 400), ('429', 429), ('503', 503), ('html400', 400), ('truncated400', 400)])
def test_explicit_http_error_preserves_status_without_retry(wire, mode, status):
    selected, calls, call = wire
    selected['mode'] = mode
    response = call()
    assert response['http_status'] == status and len(calls) == 1


@pytest.mark.parametrize('mode,phase', [('no_headers', 'headers'), ('idle', 'body'),
                                      ('partial', 'body'), ('malformed', 'body')])
def test_partial_and_stalled_reply_has_phase_evidence_and_no_retry(wire, mode, phase):
    selected, calls, call = wire
    selected['mode'] = mode
    with pytest.raises(APITransportError) as caught:
        call(idle_timeout_s=.12)
    evidence = caught.value.evidence
    assert evidence['phase'] == phase and len(calls) == 1
    assert KEY not in json.dumps(evidence) and 'Signature=' not in json.dumps(evidence)
    if mode == 'partial':
        assert evidence['request_id'] == 'partial-id' and evidence['events'] == 1


@pytest.mark.parametrize('mode', ['slow_headers', 'body_trickle'])
def test_slow_trickle_cannot_extend_total_deadline(wire, mode):
    selected, calls, call = wire
    selected['mode'] = mode
    started = time.monotonic()
    with pytest.raises(APITransportError) as caught:
        call(timeout_s=.16, idle_timeout_s=.1)
    assert time.monotonic() - started < .6
    assert caught.value.evidence['failure'] == 'total_timeout' and len(calls) == 1


def test_tls_hostname_verification_cannot_be_bypassed(wire):
    with pytest.raises(APITransportError) as caught:
        wire[2](endpoint='https://mismatch.test/test')
    assert caught.value.evidence['phase'] == 'connect'
    assert caught.value.evidence['failure'] == 'SSLCertVerificationError'
    assert wire[1] == []


@pytest.mark.parametrize('status', [400, 503, None])
def test_stream_error_keeps_existing_rejected_or_unknown_classification(wire, tmp_path, status):
    selected, calls, call = wire
    selected['reply'] = dict(code='ControlledError', message='safe')
    if status is not None:
        selected['reply']['status_code'] = status
    class Client:
        def request(self, payload):
            return call()
    with pytest.raises(tts.TTSRejected if status == 400 else tts.TTSUnknown):
        tts.synthesize_line('原文', 'Ethan', tmp_path, line_id='line', transport=Client())
    state = json.loads((tmp_path/'line/state.json').read_text())
    assert state['status'] == ('rejected' if status == 400 else 'uncertain')
    assert len(calls) == 1


@pytest.mark.parametrize('reply', [dict(request_id='id', output=dict(finish_reason='stop', audio=dict(url=''))),
    dict(request_id='id', output=dict(finish_reason=None, audio=dict(url=URL)))])
def test_missing_final_fields_are_unknown(wire, reply):
    selected, calls, call = wire
    selected.update(mode='missing_final', reply=reply)
    with pytest.raises(APITransportError):
        call()
    assert len(calls) == 1


@pytest.mark.parametrize('limit', ['MAX_BODY', 'MAX_EVENT'])
def test_response_memory_limits(wire, monkeypatch, limit):
    monkeypatch.setattr(tts_http, limit, 20)
    with pytest.raises(APITransportError) as caught:
        wire[2]()
    assert caught.value.evidence['reason'].endswith('too_large')
    assert len(wire[1]) == 1


def test_dns_is_bounded_without_starting_http(monkeypatch):
    def slow_lookup(*args, **kwargs):
        time.sleep(.3)
        raise OSError('secret-bearing network error ' + KEY)
    monkeypatch.setattr(socket, 'getaddrinfo', slow_lookup)
    started = time.monotonic()
    with pytest.raises(APITransportError) as caught:
        tts_http.request_tts('https://localhost/test', KEY, {}, timeout_s=.5, connect_timeout_s=.05)
    assert time.monotonic() - started < .25
    assert caught.value.evidence['failure'] == 'connect_timeout'
    assert caught.value.evidence['response_bytes'] == 0 and KEY not in str(caught.value.evidence)


def test_interrupted_stream_persists_unknown_and_will_not_post_again(wire, tmp_path):
    selected, calls, call = wire
    selected['mode'] = 'partial'
    class Client:
        def request(self, payload):
            return call()
    for _ in range(2):
        with pytest.raises(tts.TTSUnknown):
            tts.synthesize_line('原文', 'Ethan', tmp_path, line_id='line', transport=Client())
    state = json.loads((tmp_path/'line/state.json').read_text())
    assert state['status'] == 'uncertain' and state['request_count'] == 1
    assert state['transport_evidence']['events'] == 1 and len(calls) == 1


@pytest.mark.parametrize('bad_url', [False, True])
def test_final_event_does_not_bypass_download_and_wav_validation(wire, tmp_path, bad_url):
    selected, calls, call = wire
    if bad_url:
        selected['reply'] = dict(request_id='id', output=dict(finish_reason='stop', audio=dict(url='https://attacker.test/a.wav')))
    downloads = []
    class Client:
        def request(self, payload):
            return call()
        def download(self, url, path):
            downloads.append(url)
            path.write_bytes(b'not a WAV')
    with pytest.raises(tts.TTSStopped, match='artifact_unverified'):
        tts.synthesize_line('原文', 'Ethan', tmp_path, line_id='line', transport=Client())
    state = json.loads((tmp_path/'line/state.json').read_text())
    assert state['status'] == 'received' and not state['audio_verified']
    assert len(calls) == 1 and len(downloads) == (0 if bad_url else 1)
