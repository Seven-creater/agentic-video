"""One reference -> unchanged creative lane -> accepted stable-dialogue preview.

No template-specific prompts, actor assignments, script edits or shot IDs.
"""
from pathlib import Path
import json,os,subprocess,sys,time

root=Path(sys.argv[1]);plan=json.loads((root/'plan.json').read_text())
release=Path(plan['release']);sys.path[:0]=[str(release/'src'),str(release/'vendor')]
from omni_story.library import server_jobs
from omni_story.library.state import write_json
from omni_story.library.media import sha256_file

creative=Path(plan.get('creative_output',str(root/'creative')));preview=creative/'evaluations/fast_preview'
status=dict(state='starting',reference=plan['reference'],creative_output=str(creative),preview_output=str(preview))
def save(**values):
    status.update(values);write_json(root/'fast_run.json',status)
def read(p):return json.loads(p.read_text()) if p.is_file() else {}

try:
    assert sha256_file(plan['reference'])==plan['reference_sha256']
    os.environ['OMNI_WORKER_OUTPUT']=str(creative)
    command=[plan['python'],plan['entry_path'],'start','--reference',plan['reference'],
        '--output',str(creative),'--asr','--asr-model-dir',plan['asr_model'],
        '--evaluation-rerun','User requested a new reference through fixed automatic creative + quick preview workflow.']
    if plan.get('resume_from'):command+=['--resume-from',plan['resume_from']]
    save(state='creative_starting',command=command)
    started=subprocess.run(command,capture_output=True,text=True)
    write_json(root/'creative_launch.json',dict(returncode=started.returncode,stdout=started.stdout[-4000:],stderr=started.stderr[-2000:]))
    assert started.returncode==0,'creative_start_failed'
    checkpoint=False
    while True:
        job=server_jobs.status(creative)
        script=read(creative/'dialogue_script.json')
        ready=(script.get('lines') and 'facts' in script and (creative/'material_lock.json').is_file()
               and (creative/'watched_windows.json').is_file()
               and (creative/'autonomous_material/character_voices/cast_voices.json').is_file())
        if ready and not checkpoint:
            # Existing provider boundary lets in-flight replies settle, then
            # prevents additional semantic scheduling requests. TTS may finish.
            (creative/'mcp_stop').touch(exist_ok=True)
            write_json(creative/'quick_preview_checkpoint.json',dict(reason='Fixed fast workflow: received GLM draft and actor voices are ready; render before further semantic iterations.'))
            checkpoint=True
        save(state='waiting_for_creative_checkpoint' if checkpoint else 'creating',job_state=job['state'],
            stage=read(creative/'current_status.json').get('stage'),script_ready=bool(ready))
        if job['state'] in {'starting','running'}:time.sleep(1);continue
        assert job['state']!='unknown','creative_job_requires_reconciliation'
        break
    ledger=read(creative/'library_state.json')
    unknown=[c['id'] for c in ledger.get('calls',[]) if c['status'] not in {'received','failed_known'}]
    assert not unknown,'creative_calls_require_reconciliation:'+str(unknown)
    assert not server_jobs._group_running(job.get('child_pid')),'old_worker_still_running'
    assert ready,'creative_stopped_before_usable_draft:'+str(read(creative/'run_outcome.json').get('error'))
    os.environ['OMNI_WORKER_OUTPUT']=str(preview)
    save(state='preview_rendering',creative_requests=len(ledger.get('calls',[])))
    with (root/'preview.log').open('ab') as log:
        child=subprocess.Popen([plan['python'],plan['preview_runner'],str(creative),str(preview)],stdout=log,stderr=log)
        while child.poll() is None:
            result=read(preview/'dialogue_result.json')
            save(state='preview_ready_review_pending' if result.get('final_video') else 'preview_rendering',
                preview_progress=read(preview/'preview_progress.json'),final_video=result.get('final_video'),final_sha256=result.get('final_sha256'))
            time.sleep(2)
        assert child.returncode==0,'preview_failed:'+str(read(preview/'preview_failure.json'))
    result=read(preview/'dialogue_result.json')
    assert Path(result['final_video']).is_file() and sha256_file(result['final_video'])==result['final_sha256']
    save(state='completed',final_video=result['final_video'],final_sha256=result['final_sha256'],
        quality_status=result.get('quality_status'),preview_progress=read(preview/'preview_progress.json'))
except BaseException as exc:
    save(state='needs_attention',error=type(exc).__name__+':'+str(exc));raise
