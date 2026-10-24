import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import omni_story.library.state as state
from omni_story.evaluation import isolated_asr
from omni_story.library.state import write_json


def fake_child(runs,events=None,returncode=0,stderr=''):
    def child(command,**kwargs):
        runs.append(list(command))
        if events is not None:events.append('run')
        if not returncode:
            job=json.loads(Path(command[-2]).read_text())
            write_json(Path(command[-1]),dict(job=job,segments=[],
                info=dict(language='zh',language_probability=1)))
        return SimpleNamespace(returncode=returncode,stderr=stderr)
    return child


def install_shim(monkeypatch,cache,profile,child):
    monkeypatch.setitem(sys.modules,'faster_whisper',None)
    isolated_asr.install(cache,'/frozen/production',profile=profile)
    monkeypatch.setattr(isolated_asr.subprocess,'run',child)
    return sys.modules['faster_whisper'].WhisperModel('frozen-model',device='cpu')


def record_lock(monkeypatch,events):
    lock_calls=[]

    @contextmanager
    def file_lock(path,*,wait=False):
        lock_calls.append((str(path),wait));events.append('lock');yield;events.append('unlock')

    monkeypatch.setattr(state,'file_lock',file_lock)
    return lock_calls


def fixture_audio(tmp_path,name='audio.wav',content=b'local fixture audio'):
    path=tmp_path/name;path.write_bytes(content);return path


def expected_argv(cache,runner):
    script=str(Path(isolated_asr.__file__).resolve())
    input_path=next(cache.glob('*.input.json'))
    return runner+[sys.executable,script,str(input_path),str(input_path)[:-len('.input.json')]+'.result.json']


def test_default_profile_keeps_child_argv_and_writes_no_receipt(tmp_path,monkeypatch):
    events=[];lock_calls=record_lock(monkeypatch,events);runs=[]
    model=install_shim(monkeypatch,tmp_path/'cache',None,fake_child(runs,events))
    rows,info=model.transcribe(str(fixture_audio(tmp_path)),word_timestamps=True)
    assert list(rows)==[] and info.language=='zh'
    assert runs==[expected_argv(tmp_path/'cache',[])]
    assert list((tmp_path/'cache').glob('*.profile.json'))==[]
    assert lock_calls==[] and events==['run']


def test_profile_prefixes_runner_argv(tmp_path,monkeypatch):
    events=[];record_lock(monkeypatch,events);runs=[]
    profile=dict(runner=['systemd-run','--scope','--quiet','-p','MemoryMax=2G','-p','MemorySwapMax=0','--'],
        memory_max_bytes=2*1024**3,lock_path=str(tmp_path/'asr.lock'))
    model=install_shim(monkeypatch,tmp_path/'cache',profile,fake_child(runs))
    rows,info=model.transcribe(str(fixture_audio(tmp_path)),word_timestamps=True)
    assert list(rows)==[] and info.language=='zh'
    assert runs==[expected_argv(tmp_path/'cache',profile['runner'])]


def test_profile_run_takes_machine_lock_with_wait_and_releases_after_child(tmp_path,monkeypatch):
    events=[];lock_calls=record_lock(monkeypatch,events);runs=[]
    profile=dict(runner=['systemd-run','--scope','--quiet','--'],memory_max_bytes=2*1024**3,
        lock_path=str(tmp_path/'asr.lock'))
    model=install_shim(monkeypatch,tmp_path/'cache',profile,fake_child(runs,events))
    assert list(model.transcribe(str(fixture_audio(tmp_path)))[0])==[]
    assert lock_calls==[(profile['lock_path'],True)]
    assert events==['lock','run','unlock']


@pytest.mark.parametrize('profile',[
    'high',['systemd-run'],{},
    dict(runner=[],memory_max_bytes=1,lock_path='/tmp/asr.lock'),
    dict(runner='systemd-run',memory_max_bytes=1,lock_path='/tmp/asr.lock'),
    dict(runner=['systemd-run',5],memory_max_bytes=1,lock_path='/tmp/asr.lock'),
    dict(runner=['systemd-run'],memory_max_bytes=0,lock_path='/tmp/asr.lock'),
    dict(runner=['systemd-run'],memory_max_bytes=-1,lock_path='/tmp/asr.lock'),
    dict(runner=['systemd-run'],memory_max_bytes='2G',lock_path='/tmp/asr.lock'),
    dict(runner=['systemd-run'],memory_max_bytes=True,lock_path='/tmp/asr.lock'),
    dict(runner=['systemd-run'],memory_max_bytes=2048),
    dict(runner=['systemd-run'],memory_max_bytes=2048,lock_path=123),
    dict(memory_max_bytes=2048,lock_path='/tmp/asr.lock'),
])
def test_invalid_profile_is_rejected(tmp_path,monkeypatch,profile):
    monkeypatch.setitem(sys.modules,'faster_whisper',None)
    with pytest.raises(ValueError,match='isolated_asr_profile_invalid'):
        isolated_asr.install(tmp_path/'cache','/frozen/production',profile=profile)


def test_profile_run_writes_receipt_with_returncode_and_elapsed(tmp_path,monkeypatch):
    events=[];record_lock(monkeypatch,events);runs=[]
    profile=dict(runner=['systemd-run','--scope','--quiet','--'],memory_max_bytes=2*1024**3,
        lock_path=str(tmp_path/'asr.lock'))
    model=install_shim(monkeypatch,tmp_path/'cache',profile,fake_child(runs))
    assert list(model.transcribe(str(fixture_audio(tmp_path)),word_timestamps=True)[0])==[]
    receipt=json.loads(next((tmp_path/'cache').glob('*.profile.json')).read_text())
    assert receipt['profile']==profile
    assert receipt['returncode']==0
    assert isinstance(receipt['elapsed_s'],float) and receipt['elapsed_s']>=0


@pytest.mark.parametrize('returncode,stderr',[(-9,''),(137,''),(1,'bash: line 1: 12345 Killed')])
def test_killed_child_reports_oom_or_killed(tmp_path,monkeypatch,returncode,stderr):
    events=[];record_lock(monkeypatch,events);runs=[]
    profile=dict(runner=['systemd-run','--scope','--quiet','--'],memory_max_bytes=2*1024**3,
        lock_path=str(tmp_path/'asr.lock'))
    model=install_shim(monkeypatch,tmp_path/'cache',profile,
        fake_child(runs,returncode=returncode,stderr=stderr))
    with pytest.raises(RuntimeError,match='isolated_original_asr_failed.*oom_or_killed'):
        model.transcribe(str(fixture_audio(tmp_path)),word_timestamps=True)
    receipt=json.loads(next((tmp_path/'cache').glob('*.profile.json')).read_text())
    assert receipt['returncode']==returncode and receipt['profile']==profile


def test_plain_child_failure_keeps_original_error_without_oom_marker(tmp_path,monkeypatch):
    events=[];record_lock(monkeypatch,events);runs=[]
    profile=dict(runner=['systemd-run','--scope','--quiet','--'],memory_max_bytes=2*1024**3,
        lock_path=str(tmp_path/'asr.lock'))
    model=install_shim(monkeypatch,tmp_path/'cache',profile,
        fake_child(runs,returncode=1,stderr='whisper decode failed'))
    with pytest.raises(RuntimeError,match='isolated_original_asr_failed:whisper decode failed') as raised:
        model.transcribe(str(fixture_audio(tmp_path)),word_timestamps=True)
    assert 'oom_or_killed' not in str(raised.value)


def test_cache_key_ignores_profile_and_reuses_collected_result(tmp_path,monkeypatch):
    events=[];record_lock(monkeypatch,events);runs=[]
    cache=tmp_path/'cache'
    profile_a=dict(runner=['systemd-run','--scope','--quiet','-p','MemoryMax=2G','--'],
        memory_max_bytes=2*1024**3,lock_path=str(tmp_path/'asr.lock'))
    profile_b=dict(runner=['nice','-n','5'],memory_max_bytes=1024**3,lock_path=str(tmp_path/'asr.lock'))
    audio=fixture_audio(tmp_path)
    model=install_shim(monkeypatch,cache,profile_a,fake_child(runs,events))
    assert list(model.transcribe(str(audio),word_timestamps=True)[0])==[]
    assert len(runs)==1
    model=install_shim(monkeypatch,cache,profile_b,fake_child(runs,events))
    assert list(model.transcribe(str(audio),word_timestamps=True)[0])==[]
    model=install_shim(monkeypatch,cache,None,fake_child(runs,events))
    rows,info=model.transcribe(str(audio),word_timestamps=True)
    assert list(rows)==[] and info.language=='zh' and len(runs)==1
    receipts=list(cache.glob('*.profile.json'))
    assert len(receipts)==1 and json.loads(receipts[0].read_text())['profile']==profile_a
    audio2=fixture_audio(tmp_path,'audio2.wav',b'different fixture audio')
    model=install_shim(monkeypatch,cache,profile_b,fake_child(runs,events))
    assert list(model.transcribe(str(audio2),word_timestamps=True)[0])==[]
    assert len(runs)==2
    by_tier={json.loads(p.read_text())['profile']['memory_max_bytes']:p for p in cache.glob('*.profile.json')}
    assert set(by_tier)=={2*1024**3,1024**3}
    assert json.loads(by_tier[1024**3].read_text())['profile']==profile_b
    assert events==['lock','run','unlock']*2
