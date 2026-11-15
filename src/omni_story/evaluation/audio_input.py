"""Native audio transport for evaluation only; production API remains untouched."""
import base64
from pathlib import Path
from omni_story.api import curl_json


class NativeAudioAPI:
    def __init__(self, api):
        self.api, self.key = api, api.key
        self.endpoint = api.endpoint
        self.cfg = {**api.cfg, 'input_media_kind': 'input_audio', 'audio_format': 'wav'}

    def request(self, text, *, media):
        path = Path(media)
        if path.suffix != '.wav':
            raise ValueError('native_audio_requires_wav')
        encoded = base64.b64encode(path.read_bytes()).decode('ascii')
        if len(encoded) >= 10_000_000:
            raise ValueError('native_audio_inline_limit_exceeded')
        payload = dict(model=self.cfg['model'], messages=[dict(role='user', content=[
            dict(type='input_audio', input_audio=dict(data='data:audio/wav;base64,'+encoded, format='wav')),
            dict(type='text', text=text)])], modalities=['text'], temperature=0,
            max_tokens=6000, reasoning_effort='none', response_format=dict(type='json_object'))
        return curl_json(self.api.curl, self.endpoint, self.key, 'POST', payload)


def review_native_audio(path, folder, *, prompt, api):
    """Audio-specific received-reply ledger; no video wrapper or hidden retries."""
    import hashlib
    import json
    from omni_story.library.audio_review import _result, _safe_body
    from omni_story.library.media import sha256_file
    from omni_story.library.state import file_lock, json_sha, write_json
    path, folder = Path(path).resolve(strict=True), Path(folder).resolve()
    digest = sha256_file(path)
    request = dict(policy='native-audio-evaluation-v1', media_path=str(path), audio_sha256=digest,
                   prompt=prompt, configuration=api.cfg, media_kind='input_audio', audio_format='wav')
    fingerprint = json_sha(request)
    folder.mkdir(parents=True, exist_ok=True)
    with file_lock(folder/'.audio_review.lock'):
        state_path, response_path = folder/'state.json', folder/'response.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state['request_sha256'] != fingerprint or json_sha(json.loads((folder/'request.json').read_text())) != fingerprint:
                raise ValueError('native_audio_request_binding_changed')
            if state['status'] != 'received':
                raise RuntimeError('native_audio_request_outcome_unknown_no_replay')
            if sha256_file(response_path) != state['response_sha256']:
                raise ValueError('native_audio_response_changed')
            return _result(json.loads(response_path.read_text()), state, cached=True)
        if (folder/'request.json').exists() or response_path.exists():
            raise RuntimeError('native_audio_orphan_request_no_replay')
        write_json(folder/'request.json', request)
        state = dict(status='submitted', request_sha256=fingerprint, audio_sha256=digest, video_sha256=digest)
        write_json(state_path, state)
        try:
            response = api.request(prompt, media=path)
        except Exception:
            state['status'] = 'uncertain'; write_json(state_path, state)
            raise
        saved = dict(http_status=response.get('http_status'),
                     body_text=_safe_body(response['body_text'], (api.key,)), elapsed_s=response.get('elapsed_s'))
        write_json(response_path, saved)
        state.update(status='received', response_sha256=sha256_file(response_path),
                     body_sha256=hashlib.sha256(saved['body_text'].encode()).hexdigest())
        if sha256_file(path) != digest:
            state['status'] = 'source_changed'; write_json(state_path, state)
            raise ValueError('native_audio_changed_during_request')
        write_json(state_path, state)
        return _result(saved, state, cached=False)
