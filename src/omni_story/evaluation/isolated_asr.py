"""Preserve original Whisper parameters/results, release its RAM before GLM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace, ModuleType


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def restore_segments(rows):
    return [SimpleNamespace(**dict(row,words=[SimpleNamespace(**w) for w in row.get('words',[])])) for row in rows]


def oom_or_killed(returncode,stderr):
    return (returncode<0 or returncode==137
        or any(marker in (stderr or '').lower() for marker in ('killed','oom','out of memory')))


def install(root,production_root,*,profile=None):
    runner=lock=None
    if profile is not None:
        if (not isinstance(profile,dict) or not isinstance(profile.get('runner'),list)
                or not profile.get('runner')
                or not all(isinstance(item,str) for item in profile.get('runner',[]))
                or type(profile.get('memory_max_bytes')) is not int
                or profile.get('memory_max_bytes',0)<=0
                or not isinstance(profile.get('lock_path'),str)):
            raise ValueError('isolated_asr_profile_invalid')
        runner,lock=profile['runner'],profile['lock_path']
        # Imported here, not at module top: the child runs this file as a script
        # before production_root/src reaches sys.path, so top-level would break it.
        from omni_story.library.state import file_lock
    root=Path(root);root.mkdir(parents=True,exist_ok=True)

    class IsolatedWhisper:
        def __init__(self,*args,**kwargs):self.args,self.kwargs=args,kwargs

        def transcribe(self,audio,**kwargs):
            job=dict(constructor_args=self.args,constructor_kwargs=self.kwargs,
                     audio=str(audio),audio_sha256=sha(audio),transcribe_kwargs=kwargs,
                     production_root=str(production_root))
            # The key deliberately ignores the profile: a rerun in another resource
            # tier reuses the collected transcript instead of decoding again.
            key=hashlib.sha256(json.dumps(job,sort_keys=True).encode()).hexdigest()
            input_path,result_path=root/(key+'.input.json'),root/(key+'.result.json')
            if not result_path.exists():
                input_path.write_text(json.dumps(job))
                command=[sys.executable,str(Path(__file__).resolve()),str(input_path),str(result_path)]
                if runner is not None:command=runner+command
                started=time.monotonic()
                if runner is None:
                    result=subprocess.run(command,text=True,capture_output=True,timeout=1200)
                else:
                    with file_lock(lock,wait=True):
                        result=subprocess.run(command,text=True,capture_output=True,timeout=1200)
                    (root/(key+'.profile.json')).write_text(json.dumps(dict(profile=profile,
                        returncode=result.returncode,elapsed_s=time.monotonic()-started)))
                if result.returncode:
                    message='isolated_original_asr_failed:'+result.stderr[-1200:]
                    if oom_or_killed(result.returncode,result.stderr):message+='|oom_or_killed'
                    raise RuntimeError(message)
            saved=json.loads(result_path.read_text())
            if saved['job']!=json.loads(json.dumps(job)) or sha(audio)!=job['audio_sha256']:
                raise ValueError('isolated_asr_input_changed')
            return iter(restore_segments(saved['segments'])),SimpleNamespace(**saved['info'])

    # Importing the real module here retains CTranslate2/NumPy in the parent
    # while the recognizer child loads them again. Only the child needs them.
    shim=ModuleType('faster_whisper')
    shim.WhisperModel=IsolatedWhisper
    sys.modules['faster_whisper']=shim


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
