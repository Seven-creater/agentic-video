"""Official, account/model-bound DashScope uploads; no public file server."""
import hashlib
import http.client
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid

MAX_VIDEO_BYTES = 256 * 1024**2


def upload_video(path, key, model):
    path = Path(path)
    size = path.stat().st_size
    if not 0 < size <= MAX_VIDEO_BYTES:
        raise ValueError('temporary_media:video_size_out_of_bounds')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            checksum.update(chunk)
    digest = checksum.hexdigest()
    query = urlencode(dict(action='getPolicy', model=model))
    request = Request('https://dashscope.aliyuncs.com/api/v1/uploads?' + query,
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urlopen(request, timeout=30) as response:
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise RuntimeError('temporary_media:upload_policy_failed')
        policy = json.loads(raw)['data']
    host = urlsplit(policy['upload_host'])
    if (host.scheme != 'https' or not host.hostname or not host.hostname.endswith('.aliyuncs.com')
            or host.username or host.password or host.query or host.fragment or host.port not in (None, 443)):
        raise ValueError('temporary_media:official_https_upload_host_required')
    object_key = policy['upload_dir'].rstrip('/') + '/' + digest + '.mp4'
    fields = {'OSSAccessKeyId': policy['oss_access_key_id'], 'Signature': policy['signature'],
              'policy': policy['policy'], 'x-oss-object-acl': policy['x_oss_object_acl'],
              'x-oss-forbid-overwrite': policy['x_oss_forbid_overwrite'],
              'key': object_key, 'success_action_status': '200'}
    boundary = 'omni' + uuid.uuid4().hex
    prefix = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      f'{value}\r\n').encode() for name, value in fields.items())
    prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{digest}.mp4"'
               '\r\nContent-Type: video/mp4\r\n\r\n').encode()
    suffix = f'\r\n--{boundary}--\r\n'.encode()
    connection = http.client.HTTPSConnection(host.hostname, timeout=180)
    try:
        connection.putrequest('POST', host.path or '/')
        connection.putheader('Content-Type', 'multipart/form-data; boundary=' + boundary)
        connection.putheader('Content-Length', str(len(prefix) + size + len(suffix)))
        connection.endheaders()
        connection.send(prefix)
        actual = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024**2), b''):
                actual.update(chunk)
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        response.read(4096)
        if response.status != 200:
            raise RuntimeError('temporary_media:upload_http_' + str(response.status))
        if actual.hexdigest() != digest:
            raise ValueError('temporary_media:source_changed_during_upload')
    finally:
        connection.close()
    return 'oss://' + object_key
