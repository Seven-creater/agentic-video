"""Single direct HTTPS POST, incrementally read using Qwen's official SSE mode.

No retries, redirects, proxies, or response bodies in diagnostic receipts. The
caller still owns write-before-send and validates the downloaded audio itself.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from ..api import APITransportError

MAX_BODY = 16 * 1024 * 1024
MAX_EVENT = 2 * 1024 * 1024


class _Deadline(Exception):
    pass


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Deadline()
    return remaining


def _resolve(host, port, deadline):
    # getaddrinfo itself has no portable timeout. This worker cannot send HTTP;
    # a late DNS result is discarded after the bounded caller has stopped.
    result = queue.Queue(maxsize=1)

    def lookup():
        try:
            result.put((socket.getaddrinfo(host, port, type=socket.SOCK_STREAM), None))
        except Exception as error:
            result.put((None, error))

    threading.Thread(target=lookup, daemon=True).start()
    try:
        addresses, error = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise _Deadline() from None
    if error:
        raise error
    return addresses


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, *, deadline):
        super().__init__(host, port, context=ssl.create_default_context())
        self.deadline = deadline

    def connect(self):
        addresses = _resolve(self.host, self.port, self.deadline)
        last_error = None
        for family, kind, protocol, _, address in addresses:
            raw = socket.socket(family, kind, protocol)
            try:
                raw.settimeout(_remaining(self.deadline))
                raw.connect(address)
                raw.settimeout(_remaining(self.deadline))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                _remaining(self.deadline)
                return
            except Exception as error:
                raw.close()
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
                last_error = error
                _remaining(self.deadline)
        raise last_error or OSError('tts_no_resolved_address')


class _Events:
    """Byte framing keeps split UTF-8/CRLF intact; only JSON data is decoded."""

    def __init__(self):
        self.pending = b''
        self.data = []
        self.size = 0

    def feed(self, chunk):
        self.pending += chunk
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            line = line.removesuffix(b'\r')
            self.size += len(line)
            if self.size > MAX_EVENT:
                raise ValueError('tts_event_too_large')
            if not line:
                if self.data:
                    yield b'\n'.join(self.data)
                self.data = []
                self.size = 0
            elif line.startswith(b'data:'):
                value = line[5:]
                self.data.append(value[1:] if value.startswith(b' ') else value)
        if self.size + len(self.pending) > MAX_EVENT:
            raise ValueError('tts_event_too_large')


def _request_id(value, key):
    return value if (isinstance(value, str) and (not key or key not in value) and
                     re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', value)) else ''


def request_tts(endpoint, key, payload, *, timeout_s=120, connect_timeout_s=15,
                idle_timeout_s=60, header_idle_timeout_s=20):
    """Return a complete JSON reply as soon as its final SSE event arrives.

    A final SSE event requires stop + URL + a request ID (possibly supplied by
    an earlier event). EOF, partial data and all timeouts remain unknown. JSON
    replies from non-streaming gateways are also accepted without waiting for
    a broken Content-Length/connection to expire.
    """
    for value in (timeout_s, connect_timeout_s, idle_timeout_s, header_idle_timeout_s):
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError('tts_invalid_transport_timeout')
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.fragment or parsed.port not in {None, 443}):
        raise ValueError('tts_transport_requires_https')
    started = time.monotonic()
    deadline = started + timeout_s
    evidence = dict(transport='native_https_sse_v1', phase='connect',
                    http_status=None, request_id='', response_bytes=0, events=0,
                    connect_timeout_s=connect_timeout_s, idle_timeout_s=idle_timeout_s,
                    header_idle_timeout_s=header_idle_timeout_s, total_timeout_s=timeout_s)
    digest = hashlib.sha256()
    connection = response = watchdog = None
    body = bytearray()
    merged = {}
    events = _Events()

    def snapshot():
        return dict(evidence, elapsed_s=round(time.monotonic() - started, 6),
                    response_sha256=digest.hexdigest())

    def complete(reply, *, stream):
        if not isinstance(reply, dict):
            raise ValueError('tts_response_not_object')
        for name in ('request_id', 'usage', 'status_code', 'code', 'message'):
            if name in reply:
                if name == 'request_id' and not reply[name]:
                    continue
                if name == 'usage' and isinstance(reply[name], dict):
                    merged[name] = dict(merged.get(name, {}), **reply[name])
                else:
                    merged[name] = reply[name]
        if 'request_id' in merged:
            evidence['request_id'] = _request_id(merged['request_id'], key)
        elif evidence['request_id']:
            merged['request_id'] = evidence['request_id']
        status = merged.get('status_code', evidence['http_status'])
        if evidence['http_status'] != 200 or status != 200:
            return dict(reply, **merged)
        if merged.get('code'):
            raise ValueError('tts_provider_error_without_status')
        output = reply.get('output', {})
        audio = output.get('audio', {}) if isinstance(output, dict) else {}
        if (isinstance(audio, dict) and isinstance(audio.get('url'), str) and
                audio['url'] and evidence['request_id'] and
                (output.get('finish_reason') == 'stop' if stream else
                 output.get('finish_reason') in {None, 'stop'})):
            return dict(reply, **merged)
        return None

    try:
        connection = _HTTPSConnection(parsed.hostname, parsed.port or 443,
                                     deadline=min(deadline, started + connect_timeout_s))
        connection.connect()
        evidence['connected_s'] = round(time.monotonic() - started, 6)
        try:
            evidence['peer_ip'] = connection.sock.getpeername()[0]
        except Exception:
            evidence['peer_ip'] = None
        evidence['phase'] = 'send'
        sock = connection.sock
        def expire():
            # A socket timeout alone is per blocking read, so slow header/chunk
            # trickles could otherwise extend the total deadline indefinitely.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        watchdog = threading.Timer(_remaining(deadline), expire)
        watchdog.daemon = True
        watchdog.start()
        sock.settimeout(min(idle_timeout_s, _remaining(deadline)))
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        connection.request('POST', parsed.path + ('?' + parsed.query if parsed.query else ''),
                           body=encoded, headers={'Authorization': 'Bearer ' + key,
                           'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                           'X-DashScope-SSE': 'enable'})
        evidence['sent_s'] = round(time.monotonic() - started, 6)
        evidence['phase'] = 'headers'
        sock.settimeout(min(header_idle_timeout_s, _remaining(deadline)))
        response = connection.getresponse()
        evidence['headers_s'] = round(time.monotonic() - started, 6)
        evidence['http_status'] = response.status
        for name in ('x-request-id', 'x-dashscope-request-id', 'x-acs-request-id'):
            identity = _request_id(response.getheader(name), key)
            if identity:
                evidence['request_id'] = identity
                break
        content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
        streaming = content_type == 'text/event-stream'
        evidence['response_mode'] = 'sse' if streaming else 'json'
        evidence['phase'] = 'body'
        while True:
            sock.settimeout(min(idle_timeout_s, _remaining(deadline)))
            chunk = response.read1(65536)
            _remaining(deadline)
            if not chunk:
                if evidence['http_status'] != 200:
                    # Preserve explicit HTTP rejection even for an HTML gateway.
                    reply_text = bytes(body).decode('utf-8', errors='replace')
                    break
                raise ValueError('tts_incomplete_response')
            evidence.setdefault('first_body_s', round(time.monotonic() - started, 6))
            digest.update(chunk)
            evidence['response_bytes'] += len(chunk)
            if evidence['response_bytes'] > MAX_BODY:
                raise ValueError('tts_response_too_large')
            if streaming:
                reply = None
                for event in events.feed(chunk):
                    if not event.strip() or event.strip() == b'[DONE]':
                        continue
                    evidence['events'] += 1
                    reply = complete(json.loads(event), stream=True)
                    if reply is not None:
                        break
            else:
                body.extend(chunk)
                try:
                    decoded = json.loads(body)
                except (ValueError, UnicodeError):
                    continue
                reply = complete(decoded, stream=False)
            if reply is not None:
                reply_text = json.dumps(reply, ensure_ascii=False)
                break
        evidence['phase'] = 'complete'
        return dict(http_status=evidence['http_status'], body_text=reply_text,
                    elapsed_s=time.monotonic() - started, transport_evidence=snapshot())
    except Exception as error:
        kind = ('total_timeout' if time.monotonic() >= deadline else
                'connect_timeout' if evidence['phase'] == 'connect' and
                isinstance(error, (_Deadline, TimeoutError)) else
                'idle_timeout' if isinstance(error, TimeoutError) else type(error).__name__)
        evidence['failure'] = kind
        # Exception messages can contain credentials or signed URLs. Keep only
        # an allowlisted local reason and never a provider body/exception string.
        if isinstance(error, ValueError) and re.fullmatch(r'tts_[a-z_]+', str(error)):
            evidence['reason'] = str(error)
        if type(evidence['http_status']) is int and 400 <= evidence['http_status'] < 500:
            # A complete HTTP rejection header is definitive even if its error
            # body is broken. Do not turn a known rejection into an unknown POST.
            evidence['explicit_http_rejection'] = True
            return dict(http_status=evidence['http_status'],
                        body_text=bytes(body).decode('utf-8', errors='replace'),
                        elapsed_s=time.monotonic() - started, transport_evidence=snapshot())
        raise APITransportError('tts_' + kind, snapshot()) from None
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
