"""Finish the current reference with settled model receipts, then run reference 03."""
from pathlib import Path
import ast,json,subprocess,sys
c=Path(__file__).resolve().parent;ssh=json.loads((c.parent.parent/'服务器运行_20261018/ssh-args.json').read_text())
def source(name):
 tree=ast.parse((c/name).read_text())
 return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='code' for t in n.targets))
fixed=json.loads((c/'schedule_fix_release.json').read_text());assert fixed['tests_returncode']==0
original=json.loads((c/'latest_reviewed_template.json').read_text());upgrade=json.loads((c/'full_case1_preview_receipt.json').read_text())
after_review=json.loads((c/'latest_audio_pair_review.json').read_text()) if '--after-review' in sys.argv else None
config=dict(AFTER_REVIEW=after_review,FIXED=fixed,ORIGINAL=original,UPGRADE=upgrade,RECUT_CODE=source('launch_reviewed_recut.py'),
 LAUNCH_CODE=source('launch_reviewed_template.py'),CONTROLLER=(c/'fast_template_controller.py').read_text(),RUNNER=(c/'quick_preview_runner.py').read_text())
worker=r'''
from pathlib import Path
import json,subprocess,time,io,contextlib,sys
root=Path(sys.argv[1]);cfg=json.loads((root/'plan.json').read_text());state=dict(stage='waiting_for_current_model_schedule',cases=[],source_case=cfg['ORIGINAL'])
def save(): (root/'suite_status.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
def wait_unit(unit):
 while True:
  value=subprocess.run(['systemctl','--user','show',unit,'--property=SubState,ExecMainStatus'],capture_output=True,text=True).stdout
  if 'SubState=start' not in value and 'SubState=running' not in value:
   assert 'ExecMainStatus=0' in value,value
   return
  time.sleep(10)
def execute(code,scope):
 captured=io.StringIO()
 with contextlib.redirect_stdout(captured):exec(code,scope)
 return json.loads(captured.getvalue())
try:
 save()
 if cfg.get('AFTER_REVIEW'):
  wait_unit(cfg['AFTER_REVIEW']['unit'])
  receipt=dict(cfg['AFTER_REVIEW']['original_case'])
  receipt['reviewer_release']=cfg['AFTER_REVIEW']['release']
 else:
  save();wait_unit(cfg['ORIGINAL']['unit'])
  original_status=json.loads((Path(cfg['ORIGINAL']['output'])/'fast_run.json').read_text())
  assert original_status['state']=='completed',original_status
  state['stage']='starting_verified_finish';save()
  receipt=execute(cfg['RECUT_CODE'],dict(RELEASE=cfg['FIXED'],PRIOR=cfg['ORIGINAL'],UPGRADE=cfg['UPGRADE']))
  assert receipt['returncode']==0,receipt
 receipt.update(case=cfg['ORIGINAL']['case'],artifact_role='verified_finish',source_case=cfg['ORIGINAL']['output'])
 state['cases'].append(receipt);state['stage']='verified_finish';save();wait_unit(receipt['unit'])
 result=json.loads((Path(receipt['output'])/'dialogue_result.json').read_text())
 receipt['result']={k:result.get(k) for k in ('final_video','final_sha256','quality_status','content_delivery_ready')};save()
 assert result['artifact_ready'] and result['quality_status'] in {'passed','auto_review_off'} and result['content_delivery_ready'],receipt['result']
 state['stage']='launching';state['current_prefix']='03_';save()
 new=execute(cfg['LAUNCH_CODE'],dict(PREFIX='03_',PRIOR=None,FIXED=cfg['FIXED'],ACTIVE_RUNS=[cfg['ORIGINAL'],receipt],CONTROLLER=cfg['CONTROLLER'],RUNNER=cfg['RUNNER']))
 assert new['launched'],new
 state['cases'].append(new);state['stage']='running';save();wait_unit(new['unit'])
 status=json.loads((Path(new['output'])/'fast_run.json').read_text());assert status['state']=='completed',status
 final=Path(status['preview_output'])/'dialogue_result.json';result=json.loads(final.read_text())
 new['result']={k:result.get(k) for k in ('final_video','final_sha256','quality_status','content_delivery_ready')};save()
 assert result['artifact_ready'] and result['quality_status'] in {'passed','auto_review_off'} and result['content_delivery_ready'],new['result']
 state['stage']='completed';save()
except BaseException as exc:
 state.update(stage='needs_attention',error=type(exc).__name__+':'+str(exc));save();raise
'''
code=r'''
from pathlib import Path
import json,datetime,subprocess
h=Path('/home/ubuntu/apps/agentic-video');stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')
root=h/'trials'/('verified_creation_suite_'+stamp);root.mkdir();(root/'plan.json').write_text(json.dumps(CONFIG,ensure_ascii=False));(root/'controller.py').write_text(WORKER)
unit='omni-verified-creation-suite-'+stamp.replace('_','-')
r=subprocess.run(['systemd-run','--user','--no-block','--unit='+unit,'--property=Type=oneshot','--property=RemainAfterExit=yes',str(h/'.venv/bin/python'),str(root/'controller.py'),str(root)],capture_output=True,text=True)
print(json.dumps(dict(output=str(root),unit=unit,returncode=r.returncode,stderr=r.stderr[-1000:])))
'''
r=subprocess.run([*ssh,'python3 -'],input='CONFIG='+repr(config)+'\nWORKER='+repr(worker)+'\n'+code,capture_output=True,text=True,timeout=40)
if r.returncode:raise SystemExit(r.stderr[-3000:])
receipt=json.loads(r.stdout);(c/'latest_reviewed_suite.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
