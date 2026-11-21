from pathlib import Path
import json,subprocess,sys
c=Path(__file__).resolve().parent;ssh=json.loads((c.parent.parent/'服务器运行_20261018/ssh-args.json').read_text())
release=json.loads((c/'schedule_fix_release.json').read_text());assert release['tests_returncode']==0
upgrade_name=sys.argv[sys.argv.index('--upgrade-receipt')+1] if '--upgrade-receipt' in sys.argv else 'latest_reviewed_recut.json'
upgrade=json.loads((c/upgrade_name).read_text()) if '--upgrade' in sys.argv else None
parent_name=sys.argv[sys.argv.index('--template-receipt')+1] if '--template-receipt' in sys.argv else 'latest_fast_template.json'
prior=json.loads((c/parent_name).read_text())
code=r'''
from pathlib import Path
import json,sys,datetime,subprocess,shutil,hashlib
h=Path('/home/ubuntu/apps/agentic-video')
if RELEASE.get('runner_sha256'):
 assert hashlib.sha256(Path(RELEASE['runner']).read_bytes()).hexdigest()==RELEASE['runner_sha256']
release=Path(RELEASE['release']);sys.path[:0]=[str(release/'src'),str(release/'vendor')]
from omni_story.library import server_jobs
saved=json.loads((Path(PRIOR['output'])/'fast_run.json').read_text());parent=Path(saved['creative_output'])
job=server_jobs.status(parent);assert job['state'] not in {'starting','running','unknown'} and not server_jobs._group_running(job.get('child_pid'))
for ledger in parent.rglob('library_state.json'):
 assert all(r['status'] in {'received','failed_known'} for r in json.loads(ledger.read_text())['calls'])
extra=[]
if UPGRADE:
 previous=Path(UPGRADE['output'])
 status=subprocess.run(['systemctl','--user','show',UPGRADE['unit'],'--property=SubState'],capture_output=True,text=True).stdout
 assert 'SubState=running' not in status and 'SubState=start' not in status,status
 ledger=previous/'library_state.json'
 assert all(c['status'] in {'received','failed_known'} for c in json.loads(ledger.read_text())['calls'])
 for receipt in previous.rglob('state.json'):
  record=json.loads(receipt.read_text())
  assert record.get('status') not in {'submitted','uncertain'},'review outcome requires reconciliation:'+str(receipt)
 extra=['--resume-ledger',str(ledger)]
assert shutil.disk_usage(h).free>5.6*2**30
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S');out=parent/'evaluations'/('reviewed_recut_'+stamp)
resources=Path(json.loads((h/'shared/data/visual_library/chiikawa_v2/active_build.json').read_text())['run'])/'resources'
env=dict(OMNI_SERVER_HOME=str(h),PYTHONPATH=str(release/'src')+':'+str(release/'vendor'),PATH=str(resources/'bin')+':'+str(h/'runtime/node/bin')+':/usr/local/bin:/usr/bin:/bin',OMNI_RESOURCE_DIR=str(resources),OMNI_WORKER_OUTPUT=str(out),OMNI_MIN_FREE_BYTES=str(5*2**30),OMNI_WORKER_MAX_BYTES=str(1000*2**20),OMNI_FFMPEG_SLOTS='1',OMNI_FFMPEG_THREADS='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
unit='omni-reviewed-recut-'+stamp.replace('_','-')
command=['systemd-run','--user','--no-block','--unit='+unit,'--property=Type=oneshot','--property=RemainAfterExit=yes','--property=MemoryHigh=2600M','--property=MemoryMax=3000M','--property=MemorySwapMax=512M','--property=CPUQuota=180%','--property=Nice=15','--property=IOWeight=10']
command+=['--setenv='+k+'='+v for k,v in env.items()]
r=subprocess.run([*command,str(h/'.venv/bin/python'),RELEASE['runner'],str(parent),str(out),'--reuse-speech-only',*extra],capture_output=True,text=True)
print(json.dumps(dict(output=str(out),unit=unit,parent=str(parent),release=str(release),returncode=r.returncode,stderr=r.stderr[-1000:])))
'''
r=subprocess.run([*ssh,'python3 -'],input='RELEASE='+repr(release)+'\nPRIOR='+repr(prior)+'\nUPGRADE='+repr(upgrade)+'\n'+code,capture_output=True,text=True,timeout=40)
if r.returncode:raise SystemExit(r.stderr[-3000:])
receipt=json.loads(r.stdout);(c/'latest_reviewed_recut.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
