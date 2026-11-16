"""Serial night run: every template family once, same release, no template-specific input.

Exits with code 3 when a case needs attention so the failure can be fixed
generically and resumed; rerunning this script continues where it stopped.
"""
from pathlib import Path
import json,subprocess,sys,time,shutil
c=Path(__file__).resolve().parent;root=c.parent.parent
ssh=json.loads((root/'服务器运行_20261018/ssh-args.json').read_text())
SKIP={'01_','03_','21_'}  # already delivered earlier today
ORDER=['02_','04_','13_','15_','17_','18_','19_','20_','22_','23_','24_','25_','26_','27_','28_','29_','30_','31_','32_','33_','34_']
import os
# Retest mode (one case at a time, user rule): NIGHT_ONLY=18_ handles just that case and exits.
if os.environ.get('NIGHT_ONLY'):ORDER=[p for p in ORDER if p in os.environ['NIGHT_ONLY'].split(',')]
state_file=c/'night_batch_20261115.json';receipts=c/'night_receipts';receipts.mkdir(exist_ok=True)
state=json.loads(state_file.read_text()) if state_file.exists() else dict(release=json.loads((c/'schedule_fix_release.json').read_text())['release'],cases={})
def save():state_file.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
def log(msg):
    line=time.strftime('%m-%d %H:%M:%S')+' '+msg;print(line,flush=True)
    with (c/'night_batch_20261115.log').open('a') as f:f.write(line+'\n')
POLL=r'''
import json,shutil
from pathlib import Path
R=Path(OUT);f=json.loads((R/'fast_run.json').read_text()) if (R/'fast_run.json').exists() else {}
T=Path(f.get('creative_output','/nonexistent'))
def rd(p):
    try:return json.loads(Path(p).read_text())
    except Exception:return {}
print(json.dumps(dict(state=f.get('state'),error=f.get('error'),stage=rd(T/'current_status.json').get('stage'),
  free_gib=round(shutil.disk_usage('/home/ubuntu').free/2**30,2)),ensure_ascii=False))
'''
def poll(out):
    for attempt in range(30):  # read-only poll; ride out local network drops (11-16: one 5-min drop stopped the driver)
        try:
            r=subprocess.run([*ssh,'python3 -'],input='OUT='+repr(out)+'\n'+POLL,text=True,capture_output=True,timeout=90)
            if r.returncode==0:return json.loads(r.stdout)
        except Exception:pass
        time.sleep(60)
    raise SystemExit('ssh poll failed 30 times')
for prefix in ORDER:
    assert prefix not in SKIP
    row=state['cases'].setdefault(prefix,dict(state='pending'))
    if row['state'] in {'delivered','skipped','material_gap','failed_skipped'}:continue
    if row['state']=='needs_attention':
        log(prefix+' still needs attention: '+str(row.get('error')));save();sys.exit(3)
    if row['state']=='pending':
        r=subprocess.run([sys.executable,str(c/'launch_reviewed_template.py'),prefix],capture_output=True,text=True,timeout=120)
        if r.returncode:
            row.update(state='needs_attention',error='launch_failed:'+r.stderr[-800:]);save();log(prefix+' launch failed');sys.exit(3)
        shutil.copy(c/'latest_reviewed_template.json',receipts/(prefix+'.json'))
        receipt=json.loads((receipts/(prefix+'.json')).read_text())
        row.update(state='running',output=receipt['output'],unit=receipt['unit'],case_id=receipt['case']['case_id'],started=time.strftime('%m-%d %H:%M'),receipt='night_receipts/'+prefix+'.json')
        save();log(prefix+' launched '+receipt['case']['case_id'])
    last=None
    while True:
        s=poll(row['output'])
        if s['stage']!=last:log(prefix+' '+str(s['state'])+' '+str(s['stage'])+' free='+str(s['free_gib']));last=s['stage']
        if s['state']=='completed':break
        if s['state']=='needs_attention' and 'provider_content_rejected' in str(s['error']):
            # A refused reference is replaced, not fixed (content filter). Material gaps are no
            # longer a result (user 11-17): they mean a rule boundary needs a generic fix.
            row.update(state='material_gap',error=s['error'],failed_stage=s['stage'],finished=time.strftime('%m-%d %H:%M'));save()
            log(prefix+' MATERIAL GAP '+str(s['error']));break
        if s['state']=='needs_attention':
            row.update(state='needs_attention',error=s['error'],failed_stage=s['stage']);save();log(prefix+' NEEDS ATTENTION '+str(s['error']));sys.exit(3)
        time.sleep(60)
    if row['state']=='material_gap':continue
    label=row['case_id'].split('_01_')[0][3:]+'_夜跑'
    r=subprocess.run([sys.executable,str(c/'fetch_verified_output.py'),row['receipt'],label],capture_output=True,text=True,timeout=600)
    if r.returncode:
        row.update(state='needs_attention',error='fetch_failed:'+r.stderr[-800:]);save();log(prefix+' fetch failed');sys.exit(3)
    row.update(state='delivered',finished=time.strftime('%m-%d %H:%M'),check=json.loads(r.stdout.strip().splitlines()[-1]));save()
    log(prefix+' DELIVERED '+json.dumps(row['check'],ensure_ascii=False))
log('all cases finished');save()
