from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,subprocess,sys,hashlib
c=Path(__file__).resolve().parent;ssh=json.loads((c.parent.parent/'服务器运行_20261018/ssh-args.json').read_text())
receipt=json.loads((c/sys.argv[1]).read_text());label=sys.argv[2]
code=r'''
from pathlib import Path
import json,subprocess,os
os.nice(15)
p=Path(OUT)
if (p/'suite_status.json').exists():p=Path(json.loads((p/'suite_status.json').read_text())['cases'][-1]['output'])
if (p/'fast_run.json').exists():p=Path(json.loads((p/'fast_run.json').read_text())['preview_output'])
v=json.loads((p/'dialogue_result.json').read_text());assert v['artifact_ready']
subprocess.run(['ffmpeg','-threads','1','-v','error','-i',v['final_video'],'-f','null','-'],check=True)
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',v['final_video']]))
video_stream=next(s for s in probe['streams'] if s['codec_type']=='video');a,b=video_stream['avg_frame_rate'].split('/');fps=float(a)/float(b)
frames=sorted({int((r['start_s']+r['end_s'])/2*fps) for r in v['audio_lines']})
columns=5;rows=(len(frames)+columns-1)//columns
contact=p/'all_utterances_contact.jpg';select='+'.join('eq(n,%d)'%f for f in frames)
subprocess.run(['ffmpeg','-threads','1','-v','error','-y','-i',v['final_video'],'-vf',"select='"+select+"',scale=384:216,tile=%dx%d"%(columns,rows),'-frames:v','1',str(contact)],check=True)
extra={}
for name in ('schedule_audit.json','micro_tail_finish.json','subtitle_mask_finish.json','preview_provenance.json','audio_pair_review_status.json'):
 f=p/name
 if f.exists():extra[name]=json.loads(f.read_text())
print(json.dumps(dict(output=str(p),result=v,probe=probe,evidence=extra,contact=str(contact),full_decode_passed=True),ensure_ascii=False))
'''
x=subprocess.run([*ssh,'python3 -'],input='OUT='+repr(receipt['output'])+'\n'+code,capture_output=True,text=True,timeout=60)
if x.returncode:raise SystemExit(x.stderr[-2000:])
data=json.loads(x.stdout);target=c.parent.parent/'交付_20261115'/label;target.mkdir(parents=True,exist_ok=True)
(target/'检查记录.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
def download(pair):
 remote,local=pair
 script='from pathlib import Path\nimport sys\nsys.stdout.buffer.write(Path('+repr(remote)+').read_bytes())\n'
 r=subprocess.run([*ssh,'python3 -'],input=script.encode(),capture_output=True,timeout=60)
 assert r.returncode==0,r.stderr.decode()[-1000:]
 local.write_bytes(r.stdout)
with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(download,[(data['result']['final_video'],target/(label+'.mp4')),(data['contact'],target/'逐句画面检查.jpg')]))
video=target/(label+'.mp4');assert hashlib.sha256(video.read_bytes()).hexdigest()==data['result']['final_sha256']
review=data['result'].get('review') or {};identity=review.get('identity_verification') or {}
print(json.dumps(dict(video=str(video),contact=str(target/'逐句画面检查.jpg'),duration=data['probe']['format']['duration'],
 quality=data['result']['quality_status'],ready=data['result']['content_delivery_ready'],lines=len(data['result']['lines']),
 identity_passed=identity.get('passed'),identity_checks=len(identity.get('checks',[])),shots=data['evidence']['schedule_audit.json']['used_shot_count']),ensure_ascii=False))
