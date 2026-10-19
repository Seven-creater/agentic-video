import json

import pytest

from omni_story.library import tts_retry_resume as resume
from omni_story.library import search_recovery
from omni_story.library.media import sha256_file
from omni_story.library.state import LibraryStopped, write_json


@pytest.fixture
def bound(tmp_path, monkeypatch):
    out = tmp_path/'line/edit'
    out.mkdir(parents=True)
    calls = [dict(id=f'c{i}', name='rough_audio_timing_1' if i==12 else f'stage{i}',
        status='failed_known' if i==10 else 'received') for i in range(13)]
    ledger = dict(task_id='original-task',calls=calls, request_count=13, input_lock={'same': 'inputs'})
    baseline, parent_baseline = tmp_path/'baseline.json', tmp_path/'parent_baseline.json'
    write_json(baseline, ledger)
    write_json(parent_baseline, {**ledger, 'calls': calls[:12], 'request_count': 12})
    write_json(out/'library_state.json', ledger)
    parent = tmp_path/'caption_proof.json'
    write_json(parent, dict(policy='main_plan_caption_reflow_v1', output=str(out),
        baseline_snapshot=str(parent_baseline), protected_sha256={str(parent_baseline):sha256_file(parent_baseline)}))
    write_json(out/'caption_reflow_repair_v1.chain_failure.json',dict(type='TTSUnknown',
        error='tts_request_outcome_unknown:API_transport_failed:28'))
    tts = out/'rough_audio/tts/utt_test/state.json'
    write_json(tts, {'authorized_retry_once': {}})
    (out/'mcp_stop').write_text('reference_rough_skill_settled')
    for name in ('result.json','plan_1.json','render_1/render_result.json',
                 'render_1/final.mp4','caption_reflow_repair_v1.claim.json'):
        path = out/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{}')
    for c in calls:
        write_json(out/'calls'/c['id']/'request.json',{})
        write_json(out/'calls'/c['id']/('failure.json' if c['status']=='failed_known' else 'response.json'),{})
    pins = {str(p):sha256_file(p) for p in [*out.rglob('*'), baseline,parent_baseline,parent] if p.is_file()}
    proof = tmp_path/'resume.json'
    write_json(proof,dict(policy=resume.POLICY, task_id='original-task',output=str(out), baseline_snapshot=str(baseline),
        state_sha256=sha256_file(baseline), caption_proof=str(parent), tts_ledger=str(tts),
        protected_sha256=pins, failed_jobs=[dict(output=str(out.parent),job_id='old')]))
    monkeypatch.setattr(resume, 'validate_replacement', lambda *args: None)
    monkeypatch.setattr(resume.server_jobs, 'status', lambda *args:dict(job_id='old',state='failed',supervisor_alive=False))
    monkeypatch.setattr(resume.server_jobs, '_group_running', lambda *args:False)
    return out,proof


def test_exact_failed_audio_admitted_and_claimed_once(bound):
    out,proof=bound
    resume.validate(out,proof)
    path=search_recovery.claim(out,proof)
    assert path.name=='tts_audio_resume_v1.chain_failure.json'
    assert (out/'tts_audio_resume_v1.original_mcp_stop').read_text()=='reference_rough_skill_settled'
    assert (out/'tts_audio_resume_v1.claim.json').exists()
    with pytest.raises(LibraryStopped,match='already_claimed'):
        search_recovery.claim(out,proof)


@pytest.mark.parametrize('name',['plan_1.json','render_1/final.mp4','result.json','calls/c12/response.json'])
def test_changed_original_artifact_stops(bound,name):
    out,proof=bound
    (out/name).write_text('changed')
    with pytest.raises(LibraryStopped,match='original_file_changed'):
        resume.validate(out,proof)


def test_new_uncertain_vision_call_stops(bound):
    out,proof=bound
    saved=json.loads((out/'library_state.json').read_text());saved['calls'][-1]['status']='uncertain'
    write_json(out/'library_state.json',saved)
    with pytest.raises(LibraryStopped,match='exact_received_prefix'):
        resume.validate(out,proof)


def test_live_old_job_stops(bound,monkeypatch):
    out,proof=bound
    monkeypatch.setattr(resume.server_jobs,'status',lambda *args:dict(job_id='old',state='running',supervisor_alive=True))
    with pytest.raises(LibraryStopped,match='old_job_not_settled'):
        resume.validate(out,proof)
