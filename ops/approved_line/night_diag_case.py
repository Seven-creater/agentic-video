"""Collect failure evidence for one night-batch case into a local text file (read-only on the server)."""
from pathlib import Path
import json,subprocess,sys
c=Path(__file__).resolve().parent;root=c.parent.parent
ssh=json.loads((root/'服务器运行_20261018/ssh-args.json').read_text())
prefix,out_file=sys.argv[1],Path(sys.argv[2])
row=json.loads((c/'night_batch_20261115.json').read_text())['cases'][prefix]
code=r'''
import json
from pathlib import Path
R=Path(OUT);fr=json.loads((R/'fast_run.json').read_text())
print('== fast_run.json');print(json.dumps({k:fr.get(k) for k in ('state','error','stage','creative_output','preview_output')},ensure_ascii=False,indent=1))
co=Path(fr['creative_output'])
for name in ('run_outcome.json','current_status.json'):
    p=co/name
    if p.exists():print('== '+name);print(p.read_text()[:3000])
pf=Path(fr.get('preview_output',''))/'preview_failure.json'
if pf.exists():print('== preview_failure.json');print(pf.read_text()[:4000])
calls=sorted((co/'calls').iterdir()) if (co/'calls').exists() else []
print('== total calls in this run',len(calls))
for d in calls[-5:]:
    print('== call',d.name,sorted(p.name for p in d.iterdir()))
    for f in ('failure.json','protocol_failure.json'):
        if (d/f).exists():print('-- '+f);print((d/f).read_text()[:2500])
    if (d/'request.json').exists():
        r=json.loads((d/'request.json').read_text());print('-- prompt (first 1500 chars)');print(str(r.get('arguments',{}).get('prompt',''))[:1500])
    if (d/'response.json').exists():
        try:t=json.loads((d/'response.json').read_text())['result']['content'][0]['text']
        except Exception as e:t='unreadable '+str(e)
        print('-- response (first 2500 chars)');print(t[:2500])
'''
r=subprocess.run([*ssh,'python3 -'],input='OUT='+repr(row['output'])+'\n'+code,text=True,capture_output=True,timeout=120)
out_file.parent.mkdir(parents=True,exist_ok=True)
out_file.write_text('case '+prefix+' '+row.get('case_id','')+'\nerror: '+str(row.get('error'))+'\nfailed_stage: '+str(row.get('failed_stage'))+'\n\n'+r.stdout+'\n'+r.stderr[-2000:])
print(out_file)
