"""Observe a resumed case without launching additional templates or model calls."""
from pathlib import Path
import json,subprocess,shutil

c=Path(__file__).resolve().parent
ssh=json.loads((c.parent.parent/'服务器运行_20261018/ssh-args.json').read_text())
prior=json.loads((c/'latest_reviewed_suite.json').read_text())
current=json.loads((c/'latest_reviewed_template.json').read_text())
worker=r'''
from pathlib import Path
import json,subprocess,time,sys
root=Path(sys.argv[1]);cfg=json.loads((root/'plan.json').read_text());state=cfg['state'];row=cfg['current']
def save():
 temporary=root/'suite_status.tmp';temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2));temporary.replace(root/'suite_status.json')
try:
 state.update(stage='running',error=None,current_prefix=row['case']['template']);save()
 while True:
  service=subprocess.run(['systemctl','--user','show',row['unit'],'--property=SubState'],capture_output=True,text=True).stdout
  if 'SubState=start' not in service and 'SubState=running' not in service:break
  time.sleep(10)
 status=json.loads((Path(row['output'])/'fast_run.json').read_text())
 assert status['state']=='completed',status
 result=json.loads((Path(status['preview_output'])/'dialogue_result.json').read_text())
 state['cases'][-1]['result']={k:result.get(k) for k in ('final_video','final_sha256','quality_status','content_delivery_ready')}
 assert result['artifact_ready'] and result['quality_status'] in {'passed','auto_review_off'} and result['content_delivery_ready'],state['cases'][-1]['result']
 state['stage']='completed';save()
except BaseException as exc:
 state.update(stage='needs_attention',error=type(exc).__name__+':'+str(exc));save();raise
'''
code=r'''
from pathlib import Path
import json,datetime,subprocess
h=Path('/home/ubuntu/apps/agentic-video')
old=json.loads((Path(PRIOR['output'])/'suite_status.json').read_text())
assert old['stage']=='needs_attention' and old['cases'][-1]['case']['case_id']==CURRENT['case']['case_id']
old.setdefault('previous_attempts',[]).append(old['cases'][-1]);old['cases'][-1]=CURRENT
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
root=h/'trials'/('verified_creation_suite_resumed_'+stamp);root.mkdir()
(root/'plan.json').write_text(json.dumps(dict(state=old,current=CURRENT,previous_suite=PRIOR),ensure_ascii=False))
(root/'controller.py').write_text(WORKER)
unit='omni-verified-creation-resumed-'+stamp.replace('_','-')
r=subprocess.run(['systemd-run','--user','--no-block','--unit='+unit,'--property=Type=oneshot','--property=RemainAfterExit=yes',str(h/'.venv/bin/python'),str(root/'controller.py'),str(root)],capture_output=True,text=True)
assert r.returncode==0,r.stderr
print(json.dumps(dict(output=str(root),unit=unit,returncode=r.returncode,stderr=r.stderr[-1000:])))
'''
r=subprocess.run([*ssh,'python3 -'],input='PRIOR='+repr(prior)+'\nCURRENT='+repr(current)+'\nWORKER='+repr(worker)+'\n'+code,capture_output=True,text=True,timeout=30)
if r.returncode:raise SystemExit(r.stderr[-2000:])
value=json.loads(r.stdout)
shutil.copy2(c/'latest_reviewed_suite.json',c/('suite_before_resume_'+prior['unit']+'.json'))
(c/'latest_reviewed_suite.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(value,ensure_ascii=False))
