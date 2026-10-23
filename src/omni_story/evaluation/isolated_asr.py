"""Preserve original Whisper parameters/results, release its RAM before GLM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def restore_segments(rows):
    return [SimpleNamespace(**dict(row,words=[SimpleNamespace(**w) for w in row.get('words',[])])) for row in rows]


def install(root,production_root):
    import faster_whisper
    root=Path(root);root.mkdir(parents=True,exist_ok=True)

    class IsolatedWhisper:
        def __init__(self,*args,**kwargs):self.args,self.kwargs=args,kwargs

        def transcribe(self,audio,**kwargs):
            job=dict(constructor_args=self.args,constructor_kwargs=self.kwargs,
                     audio=str(audio),audio_sha256=sha(audio),transcribe_kwargs=kwargs,
                     production_root=str(production_root))
            key=hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest()
            input_path,result_path=root/(key+'.input.json'),root/(key+'.result.json')
            if not result_path.exists():
                input_path.write_text(json.dumps(job))
                result=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(input_path),str(result_path)],
                    text=True,capture_output=True,timeout=1200)
                if result.returncode:raise RuntimeError('isolated_original_asr_failed:'+result.stderr[-1200:])
            saved=json.loads(result_path.read_text())
            if saved['job']!=json.loads(json.dumps(job)) or sha(audio)!=job['audio_sha256']:
                raise ValueError('isolated_asr_input_changed')
            return iter(restore_segments(saved['segments'])),SimpleNamespace(**saved['info'])

    faster_whisper.WhisperModel=IsolatedWhisper


def main():
    job_path,result_path=map(Path,sys.argv[1:])
    job=json.loads(job_path.read_text())
    sys.path.insert(0,str(Path(job['production_root'])/'src'))
    from faster_whisper import WhisperModel
    from omni_story.library.asr import _raw_segment
    from omni_story.library.state import write_json
    if sha(job['audio'])!=job['audio_sha256']:raise ValueError('isolated_asr_audio_changed')
    model=WhisperModel(*job['constructor_args'],**job['constructor_kwargs'])
    rows,info=model.transcribe(job['audio'],**job['transcribe_kwargs'])
    write_json(result_path,dict(job=job,segments=[_raw_segment(r) for r in rows],
        info=dict(language=info.language,language_probability=float(info.language_probability))))


if __name__=='__main__':main()
