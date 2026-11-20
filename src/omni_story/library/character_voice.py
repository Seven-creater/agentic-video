"""Source-bound voice enrollment and synthesis for a selected animation cast."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4
import wave

import numpy as np

from omni_story.api import curl_json
from .audio_render import SAMPLE_RATE, _samples
from .media import sha256_file
from .resource_guard import before_request
from .state import json_sha, write_json
from .tts import normalize_received_wav


MODEL = 'qwen-audio-3.1-tts-plus'


class VoiceUnavailable(ValueError):
    pass


class CharacterVoices:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.key = os.environ['DASHSCOPE_API_KEY']

    def source_media(self, source, clip, *, pad_s=8):
        if source.get('audio_stream_index') is None:
            raise VoiceUnavailable('The source has no original audio')
        start = max(0.,clip['source_start_s']-pad_s)
        end = min(source['duration_s'],clip['source_end_s']+pad_s)
        media = self.folder/'source_with_sound.mp4'
        receipt = self.folder/'source_audio_binding.json'
        binding = dict(source_sha256=source['sha256'],start_s=start,end_s=end)
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if not media.is_file() or saved != dict(binding,media_sha256=sha256_file(media)):
                raise VoiceUnavailable('Original voice-review media changed')
        else:
            temporary = media.with_suffix('.part.mp4')
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-threads','1','-ss',str(start),'-i',source['path'],
                '-t',str(end-start),'-map','0:v:0','-map',f"0:{source['audio_stream_index']}",
                '-filter_threads','1','-vf',
                r"scale=640:-2,fps=10,pad=iw:ih+28:0:28:black,drawtext=text='LOCAL %{pts\:hms}':x=6:y=3:fontsize=16:fontcolor=white",
                '-c:v','libx264','-preset','veryfast','-crf','25','-threads','1',
                '-c:a','aac','-ac','2','-b:a','96k',str(temporary)],
                check=True,capture_output=True,timeout=90)
            temporary.replace(media)
            write_json(receipt,dict(binding,media_sha256=sha256_file(media)))
        return media,binding

    def check_samples(self, rows, actors, duration, *, minimum_s=3,stop_after_s=8):
        if (not isinstance(rows,list) or len(rows)!=len(actors)
                or any(not isinstance(r,dict) for r in rows)
                or {r.get('actor') for r in rows}!=set(actors)):
            raise VoiceUnavailable('Could not verify original voices for every actor')
        spans = {}
        for row in rows:
            selected = []
            samples = row.get('samples')
            if not isinstance(samples,list) or any(not isinstance(s,dict) for s in samples):
                raise VoiceUnavailable('Invalid original voice sample list')
            for sample in samples[:8]:
                a,b = sample.get('start_s'),sample.get('end_s')
                if (type(a) not in (float,int) or type(b) not in (float,int) or
                        not 0<=a<b<=duration+.01 or not .3<=b-a<=15 or not sample.get('evidence')):
                    raise VoiceUnavailable('Invalid original voice evidence')
                if any(a<y and x<b for existing in spans.values() for x,y in existing) or any(
                        a<y and x<b for x,y in selected):
                    raise VoiceUnavailable('Original voice samples overlap')
                selected.append((a,b))
                if stop_after_s is not None and sum(y-x for x,y in selected)>=stop_after_s:
                    break
            if sum(b-a for a,b in selected)<minimum_s:
                raise VoiceUnavailable('Insufficient isolated original voice for '+row['actor'])
            spans[row['actor']] = selected
        return spans

    def prepare(self, media, binding, cast, lines, rows, spans, reuse=None):
        write_json(self.folder/'voice_evidence.json',dict(source=binding,cast=cast,observations=rows,used=spans))
        reuse = reuse or {}
        voices = {role:(reuse[role] if role in reuse else self.enroll(role,media,spans[role])) for role in cast}
        speech = {line['line_id']:self.render_line(line,voices[line['role']]) for line in lines}
        return voices,speech

    def render_line(self, line, voice):
        file = self.synthesize(line,voice)
        samples = _samples(file)
        level = np.max(np.abs(samples),axis=1)
        active = np.flatnonzero(level>=.012)
        if 0 < len(active) < .12*SAMPLE_RATE:
            # Quiet replies can have a clear peak but few samples over the normal floor.
            peak = float(level.max())
            if peak >= .03:
                active = np.flatnonzero(level>=max(.006,min(.012,.15*peak)))
        if len(active)<.12*SAMPLE_RATE:
            raise VoiceUnavailable('Voice has insufficient audible speech: '+line['line_id'])
        lead,stop = max(0,int(active[0])-SAMPLE_RATE//100),min(len(samples),int(active[-1])+SAMPLE_RATE//50)
        samples = samples[lead:stop].copy()
        rms = float(np.sqrt(np.mean(samples**2)))
        samples *= min(.085/rms,.85/float(np.max(np.abs(samples))))
        line.update(voice=voice,tts_audio_path=str(file),lead_s=lead/SAMPLE_RATE,voiced_s=len(samples)/SAMPLE_RATE)
        return samples

    def enroll(self, role, media, intervals):
        parts = []
        for a,b in intervals:
            pcm = subprocess.check_output(['ffmpeg','-nostdin','-v','error','-ss',str(a),'-i',str(media),
                '-t',str(b-a),'-map','0:a:0','-ac','1','-ar','24000','-f','s16le','-'],
                stderr=subprocess.PIPE,timeout=30)
            y = np.frombuffer(pcm,'<i2').astype(np.float32)
            if len(y)<7200:
                raise VoiceUnavailable('Truncated original voice sample for '+role)
            y[:192] *= np.linspace(0,1,192)
            y[-192:] *= np.linspace(1,0,192)
            parts.extend([y,np.zeros(2880,dtype=np.float32)])
        audio = np.concatenate(parts[:-1])
        peak = float(np.max(np.abs(audio)))
        if peak<200:
            raise VoiceUnavailable('Original voice sample is silent for '+role)
        sample = self.folder/('sample_'+json_sha(role)[:12]+'.wav')
        with wave.open(str(sample),'wb') as output:
            output.setparams((1,2,24000,0,'NONE','not compressed'))
            output.writeframes((audio*(25000/peak)).astype('<i2').tobytes())
        spec = dict(role=role,model=MODEL,sample_sha256=sha256_file(sample))
        retry_of=None
        for attempt in range(2):
            state = self.folder/(sample.stem+('_enrollment.json' if attempt==0 else '_enrollment_recovery_1.json'))
            if state.exists():
                saved = json.loads(state.read_text())
                if saved.get('binding')!=spec:raise VoiceUnavailable('Enrollment sample binding changed for '+role)
                if saved['status']=='received':
                    self.enrollment_path=state
                    return saved['voice']
                if saved['status']!='rejected':
                    raise RuntimeError('Voice enrollment outcome unknown; do not replay for '+role)
            else:
                before_request()
                write_json(state,dict(binding=spec,status='submitted',retry_of=retry_of))
                response = curl_json(shutil.which('curl'),'https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization',
                    self.key,'POST',dict(model='voice-enrollment',input=dict(action='create_voice',target_model=MODEL,
                        prefix='anim'+json_sha(spec)[:6],audio='data:audio/wav;base64,'+base64.b64encode(sample.read_bytes()).decode())),
                    timeout=60,noproxy='*')
                body = json.loads(response['body_text'])
                voice = (body.get('output') or {}).get('voice_id')
                ok = response['http_status']==200 and isinstance(voice,str) and bool(voice)
                rejected=not ok and response['http_status']>=400 and bool(body.get('code'))
                saved=dict(binding=spec,status='received' if ok else 'rejected' if rejected else 'uncertain',
                    request_id=body.get('request_id'),voice=voice,code=body.get('code'),http_status=response['http_status'],
                    message=str(body.get('message',''))[:1000].replace(self.key,'[REDACTED]'),retry_of=retry_of)
                write_json(state,saved)
                if ok:
                    self.enrollment_path=state
                    return voice
                if not rejected:raise RuntimeError('Voice enrollment outcome unknown; do not replay for '+role)
            if attempt==0 and saved.get('code') in {'InternalError','ServiceUnavailable','Throttling'}:
                retry_of=dict(path=str(state),request_id=saved.get('request_id'))
                import time
                time.sleep(2)
                continue
            raise VoiceUnavailable('Voice enrollment rejected for '+role+':'+str(saved.get('code')))

    def synthesize(self,line,voice):
        # User 11-17: a role whose actor has too little original voice gets a preset voice,
        # synthesized by the verified preset-voice TTS, never by cloning.
        from .tts import VERIFIED_VOICES, synthesize_line
        if voice in VERIFIED_VOICES:
            ident = 'speech_'+json_sha(dict(preset=voice,text=line['text']))[:16]
            for prior in getattr(self,'reuse_folders',[]):
                state_path=Path(prior)/'preset_voices'/ident/'state.json'
                if not state_path.exists():continue
                saved=json.loads(state_path.read_text())
                if saved.get('voice')!=voice or saved.get('text')!=line['text']:continue
                if saved.get('status')!='received':raise VoiceUnavailable('Prior identical preset speech outcome is not confirmed; no replay')
                audio=Path(saved['audio_path'])
                if not audio.is_file() or sha256_file(audio)!=saved.get('audio_sha256'):raise VoiceUnavailable('Prior received preset speech changed')
                return audio
            record = synthesize_line(line['text'], voice, self.folder/'preset_voices', line_id=ident)
            return Path(record['audio_path'])
        import websocket

        spec = dict(model=MODEL,voice=voice,text=line['text'])
        name = 'speech_'+json_sha(spec)[:16]
        state,raw = self.folder/(name+'.json'),self.folder/(name+'.wav')
        if not state.exists():
            for prior in getattr(self,'reuse_folders',[]):
                receipt=Path(prior)/(name+'.json')
                if not receipt.exists():continue
                saved=json.loads(receipt.read_text())
                if saved.get('binding')!=spec:continue
                if saved.get('status')!='received':raise VoiceUnavailable('Prior identical speech outcome is not confirmed; no replay')
                if sha256_file(saved['file'])!=saved['sha256']:raise VoiceUnavailable('Prior received speech changed')
                return Path(saved['file'])
        if state.exists():
            saved = json.loads(state.read_text())
            if (saved.get('binding')!=spec or saved['status']!='received'
                    or sha256_file(saved['file'])!=saved['sha256']):
                raise VoiceUnavailable('Cloned speech has no confirmed reusable receipt')
            return Path(saved['file'])
        before_request()
        task_id = uuid4().hex
        ws = websocket.create_connection('wss://dashscope.aliyuncs.com/api-ws/v1/inference',
                                         header={'Authorization':'Bearer '+self.key},timeout=30)
        def send(action, payload):
            ws.send(json.dumps(dict(header=dict(action=action,task_id=task_id,streaming='duplex'),
                                    payload=payload),ensure_ascii=False))
        try:
            write_json(state,dict(binding=spec,status='submitted',task_id=task_id))
            send('run-task',dict(task_group='audio',task='tts',function='SpeechSynthesizer',
                model=MODEL,input={},parameters=dict(text_type='PlainText',voice=voice,format='wav',
                    sample_rate=24000,volume=50,rate=1,pitch=1)))
            chunks = []
            while True:
                data = ws.recv()
                if isinstance(data,bytes):
                    chunks.append(data)
                    continue
                header = json.loads(data)['header']
                event = header['event']
                if event=='task-started':
                    send('continue-task',dict(input=dict(text=line['text'])))
                    send('finish-task',dict(input={}))
                elif event=='task-finished':
                    break
                elif event=='task-failed':
                    code = re.sub(r'[^a-zA-Z0-9_.-]','',str(header.get('error_code')))
                    write_json(state,dict(binding=spec,status='rejected',task_id=task_id,code=code))
                    raise VoiceUnavailable('Cloned speech was rejected: '+code)
        finally:
            ws.close()
        raw.write_bytes(b''.join(chunks))
        fixed = normalize_received_wav(raw,self.folder/(name+'_normalized.wav'))
        file = Path(fixed['normalized_audio_path']) if fixed else raw
        write_json(state,dict(binding=spec,status='received',task_id=task_id,file=str(file),sha256=sha256_file(file)))
        return file
