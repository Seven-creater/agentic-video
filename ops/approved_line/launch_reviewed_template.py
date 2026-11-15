"""Select a reference by its inventory family prefix; never alter creative prompts."""
from pathlib import Path
import json,subprocess,sys,hashlib
c=Path(__file__).resolve().parent;root=c.parent.parent
prefix=sys.argv[1];ssh=json.loads((root/'服务器运行_20261018/ssh-args.json').read_text())
controller=(c/'fast_template_controller.py').read_text()
runner=(c/'quick_preview_runner.py').read_text()
active_runs=[json.loads((c/n).read_text()) for n in ('latest_fast_template.json','latest_reviewed_recut.json','latest_reviewed_template.json') if (c/n).exists()]
fixed=json.loads((c/'schedule_fix_release.json').read_text());assert fixed['tests_returncode']==0
receipt_file=sys.argv[sys.argv.index('--resume-receipt')+1] if '--resume-receipt' in sys.argv else 'latest_reviewed_template.json'
prior=json.loads((c/receipt_file).read_text()) if '--resume' in sys.argv else None
code=r'''
from pathlib import Path
import json,datetime,hashlib,subprocess,shutil,sys
h=Path('/home/ubuntu/apps/agentic-video');base=h/'baselines/approved_chiikawa_dialogue_20261114_v1'
original=json.loads((h/'trials/approved_line_24_templates_stability_20261114_062612/plan.json').read_text())
matches=[r for r in original['cases'] if r['case_id'].startswith(PREFIX)];assert len(matches)==1
case=matches[0];release=Path(FIXED['release']);original['release']=str(release)
old_entry=Path(original['entry_path']);entry=base/('reviewed_entry_'+release.name+'.py');entry.write_text(old_entry.read_text())
recipe=json.loads(old_entry.with_suffix('.json').read_text());recipe['release']=str(release);entry.with_suffix('.json').write_text(json.dumps(recipe,ensure_ascii=False,indent=2))
original.update(entry_path=str(entry),code_sha256=json.loads((release/'schedule_manifest.json').read_text())['sha256'])
sys.path[:0]=[str(release/'src'),str(release/'vendor')]
from omni_story.library import server_jobs
parent=None
if PRIOR:
 assert PRIOR['case']['case_id']==case['case_id']
 previous_root=Path(PRIOR['output']);saved=json.loads((previous_root/'fast_run.json').read_text());parent=Path(saved['creative_output'])
 job=server_jobs.status(parent);assert job['state'] in {'failed','stopped'} and not server_jobs._group_running(job.get('child_pid'))
 for ledger in parent.rglob('library_state.json'):
  assert all(r['status'] in {'received','failed_known'} for r in json.loads(ledger.read_text())['calls'])
 for folder in parent.glob('**/character_voices'):
  for pattern in ('**/state.json','**/*enrollment*.json','**/speech_*.json'):
   for receipt in folder.glob(pattern):
    assert json.loads(receipt.read_text()).get('status') in {'received','rejected'},'voice request requires reconciliation: '+str(receipt)
for active in ACTIVE_RUNS:
 state=subprocess.run(['systemctl','--user','show',active['unit'],'--property=SubState'],capture_output=True,text=True).stdout
 assert 'SubState=start' not in state and 'SubState=running' not in state,'another worker active: '+active['unit']
for campaign in (h/'trials').glob('approved_line_24_templates_stability_*/batch.json'):
 for row in json.loads(campaign.read_text()).get('cases',{}).values():
  if row.get('state') in {'starting','running'}:
   job=server_jobs.status(Path(row['output']));assert job['state'] not in {'starting','running','unknown'},'another creative job is active'
assert shutil.disk_usage(h).free>5.6*2**30
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S');out=h/'trials'/('fast_template_'+stamp+'_'+case['case_id']);out.mkdir()
controller=base/('fast_template_controller_'+hashlib.sha256(CONTROLLER.encode()).hexdigest()[:12]+'.py');controller.write_text(CONTROLLER)
preview=base/('quick_preview_runner_'+hashlib.sha256(RUNNER.encode()).hexdigest()[:12]+'.py')
if preview.exists():assert preview.read_text()==RUNNER
else:preview.write_text(RUNNER)
plan=dict(reference=case['reference'],reference_sha256=case['reference_sha256'],release=str(release),
 entry_path=original['entry_path'],asr_model=original['asr_model'],python=str(h/'.venv/bin/python'),
 preview_runner=str(preview),preview_runner_sha256=hashlib.sha256(RUNNER.encode()).hexdigest(),
 creative_code_sha256=original['code_sha256'],controller_sha256=hashlib.sha256(CONTROLLER.encode()).hexdigest())
if parent:plan.update(resume_from=str(parent),creative_output=str(parent/'evaluations'/('fast_resume_'+stamp)),source_campaign=PRIOR['output'])
(out/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2))
resources=Path(json.loads((h/'shared/data/visual_library/chiikawa_v2/active_build.json').read_text())['run'])/'resources'
env=dict(PYTHONPATH=str(release/'src')+':'+str(release/'vendor'),PATH=str(resources/'bin')+':'+str(h/'runtime/node/bin')+':/usr/local/bin:/usr/bin:/bin',OMNI_RESOURCE_DIR=str(resources),OMNI_SUBTITLE_BAND_DIR=str(h/'shared/data/visual_library/chiikawa_v2/subtitle_bands'),OMNI_MIN_FREE_BYTES=str(5*2**30),OMNI_WORKER_MAX_BYTES=str(1000*2**20),OMNI_FFMPEG_SLOTS='1',OMNI_FFMPEG_THREADS='1',OMNI_ASR_THREADS='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
unit='omni-fast-template-'+stamp.replace('_','-')
command=['systemd-run','--user','--no-block','--unit='+unit,'--property=Type=oneshot','--property=RemainAfterExit=yes','--property=MemoryHigh=2600M','--property=MemoryMax=3000M','--property=MemorySwapMax=512M','--property=CPUQuota=180%','--property=Nice=15','--property=IOWeight=10']
command+=['--setenv='+k+'='+v for k,v in env.items()]
r=subprocess.run([*command,str(h/'.venv/bin/python'),str(controller),str(out)],text=True,capture_output=True)
print(json.dumps(dict(launched=r.returncode==0,output=str(out),unit=unit,case=case,plan=plan,stderr=r.stderr[-1000:]),ensure_ascii=False))
'''
r=subprocess.run([*ssh,'python3 -'],input='PREFIX='+repr(prefix)+'\nCONTROLLER='+repr(controller)+'\nRUNNER='+repr(runner)+'\nPRIOR='+repr(prior)+'\nFIXED='+repr(fixed)+'\nACTIVE_RUNS='+repr(active_runs)+'\n'+code,text=True,capture_output=True,timeout=45)
if r.returncode:raise SystemExit(r.stderr[-2000:])
receipt=json.loads(r.stdout);(c/'latest_reviewed_template.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(receipt,ensure_ascii=False))
