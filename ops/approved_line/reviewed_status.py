from pathlib import Path
import json,subprocess,sys
c=Path(__file__).resolve().parent;ssh=json.loads((c.parent.parent/'服务器运行_20261018/ssh-args.json').read_text());receipt=json.loads((c/('latest_reviewed_recut.json' if len(sys.argv)<2 else sys.argv[1])).read_text())
code=r'''
from pathlib import Path
import json,subprocess,shutil,time
out=Path(R['output'])
result=dict(disk_free_gib=round(shutil.disk_usage(out).free/2**30,2))
suite=out/'suite_status.json'
if suite.exists():
 v=json.loads(suite.read_text());result['suite']=dict(stage=v.get('stage'),error=v.get('error'),current_prefix=v.get('current_prefix'),cases=[])
 for row in (v.get('cases',[]) or ([v['source_case']] if v.get('source_case') else [])):
  path=Path(row['output']);status=path/'fast_run.json'
  saved=json.loads(status.read_text()) if status.exists() else {}
  if not saved and (path/'preview_progress.json').exists():saved=dict(state='verified_finish',creative_output=str(path),preview_output=str(path))
  brief=dict(case=row['case']['case_id'],output=str(path),state={k:saved.get(k) for k in ('state','job_state','stage','error','creative_output','final_video','quality_status')})
  creative=Path(brief['state'].get('creative_output',str(path/'creative')))
  p=creative/'current_status.json'
  if p.exists():
   brief['stage']=json.loads(p.read_text())
   brief['stage_age_seconds']=round(time.time()-brief['stage'].get('at_unix',time.time()))
  browser=creative/'autonomous_material/browser'
  coverage=browser/'coverage.json'
  if coverage.exists():
   v=json.loads(coverage.read_text())
   brief['catalog_coverage']={k:v.get(k) for k in ('total_windows','total_pages','completed_pages','complete')}
   brief['watched_windows']=len(list(browser.glob('vl_*_visual.json')))
  pages=creative/'autonomous_material/browser/pages'
  if pages.exists():
   page_calls=[]
   for ledger in sorted(pages.glob('*/library_state.json')):
    calls=json.loads(ledger.read_text()).get('calls',[])
    if calls:page_calls.append(dict(page=ledger.parent.name,**{k:calls[-1].get(k) for k in ('name','status','submitted_at','completed_at','error')}))
   brief['catalog_pages']=dict(started=len(page_calls),received=sum(r['status']=='received' for r in page_calls),active=[r for r in page_calls if r['status']!='received'])
  model_calls=[]
  for ledger in creative.rglob('library_state.json'):
   if 'evaluations' in ledger.relative_to(creative).parts:continue
   for call in json.loads(ledger.read_text()).get('calls',[]):
    model_calls.append({k:call.get(k) for k in ('name','status','submitted_at','completed_at','error')})
  if model_calls:
   brief['model_requests']=dict(total=len(model_calls),received=sum(r['status']=='received' for r in model_calls),active=[r for r in model_calls if r['status'] not in {'received','failed_known'}])
  audio_reviews=[]
  for folder in (creative/'autonomous_material/source_speakers',creative/'autonomous_material/character_voices'):
   for receipt in folder.glob('**/state.json'):
    v=json.loads(receipt.read_text())
    if v.get('policy')!='actual-audio-video-review-v1':continue
    audio_reviews.append(dict(stage=str(receipt.parent.relative_to(creative)),**{k:v.get(k) for k in ('status','submitted_at','received_at')}))
  if audio_reviews:
   brief['audio_reviews']=dict(total=len(audio_reviews),received=sum(r['status']=='received' for r in audio_reviews),active=[r for r in audio_reviews if r['status'] not in {'received','rejected'}])
  preview=Path(saved.get('preview_output',str(creative/'evaluations/fast_preview')))
  if preview.exists():
   brief['preview']={}
   preview_calls=[]
   for ledger in preview.rglob('library_state.json'):
    for call in json.loads(ledger.read_text()).get('calls',[]):
     preview_calls.append({k:call.get(k) for k in ('name','status','submitted_at','completed_at','error')})
   if preview_calls:
    brief['preview']['requests']=dict(total=len(preview_calls),received=sum(r['status']=='received' for r in preview_calls),active=[r for r in preview_calls if r['status'] not in {'received','failed_known'}])
   pair_statuses=[]
   for f in (preview/'dialogue_review/av_identity_v0/reference_pairs').glob('sound_pair_*/state.json'):
    pair_statuses.append(json.loads(f.read_text()).get('status'))
   if pair_statuses:brief['preview']['audio_pairs']={k:pair_statuses.count(k) for k in set(pair_statuses)}
   for filename in ('preview_progress.json','current_status.json','schedule_audit.json','preview_failure.json','dialogue_result.json'):
    f=preview/filename
    if f.exists():
     v=json.loads(f.read_text())
     brief['preview'][filename]=({k:v.get(k) for k in ('quality_status','final_video','content_delivery_ready','review_error')} if filename=='dialogue_result.json' else {k:x for k,x in v.items() if k not in {'lines','allocation','items'}})
  delivered=brief.get('preview',{}).get('dialogue_result.json',{})
  if delivered.get('content_delivery_ready') is True:
   brief=dict(case=row['case']['case_id'],output=str(path),state='completed',quality_status=delivered['quality_status'],final_video=delivered['final_video'])
  result['suite']['cases'].append(brief)
result['service']=subprocess.run(['systemctl','--user','show',R['unit'],'--property=ActiveState,SubState,ExecMainStatus'],capture_output=True,text=True).stdout
for n in ('preview_progress.json','preview_failure.json','schedule_audit.json','dialogue_result.json','fast_run.json','current_status.json','run_outcome.json','audio_pair_review_status.json'):
 p=out/n
 if p.exists():
  v=json.loads(p.read_text());result[n]=({k:v.get(k) for k in ('status','final_video','final_sha256','quality_status','review_error','content_delivery_ready')} if n in {'dialogue_result.json','run_outcome.json'} else {k:x for k,x in v.items() if k not in {'lines','timeline','configuration','windows','sources','allocation','items'}})
p=out/'library_state.json'
if p.exists():
 v=json.loads(p.read_text());result['requests']=[{k:r.get(k) for k in ('id','name','status','error')} for r in v.get('calls',[])][-5:]
pair_states=[]
for f in (out/'dialogue_review/av_identity_v0/reference_pairs').glob('sound_pair_*/state.json'):
 v=json.loads(f.read_text());pair_states.append(v.get('status'))
if pair_states:result['audio_pairs']={k:pair_states.count(k) for k in set(pair_states)}
result['log']=subprocess.run(['journalctl','--user','-u',R['unit'],'-n','12','--no-pager','-o','cat'],capture_output=True,text=True).stdout[-2500:]
print(json.dumps(result,ensure_ascii=False))
'''
r=subprocess.run([*ssh,'python3 -'],input='R='+repr(receipt)+'\n'+code,capture_output=True,text=True,timeout=30)
print(r.stdout if r.returncode==0 else r.stderr[-2500:])
