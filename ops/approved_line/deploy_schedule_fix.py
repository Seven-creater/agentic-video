from pathlib import Path
import json,subprocess,hashlib
c=Path(__file__).resolve().parent;root=c.parent.parent
ssh=json.loads((root/'服务器运行_20261018/ssh-args.json').read_text())
prior=json.loads((c/'latest_fast_template.json').read_text())
files={str(p.relative_to(c/'fast_source')):p.read_text() for pattern in ('*.py','*.mjs') for p in (c/'fast_source/src').rglob(pattern) if '__pycache__' not in p.parts}
params=dict(BASE=prior['plan']['release'],FILES=files,RUNNER=(c/'quick_preview_runner.py').read_text(),TESTS={p.name:p.read_text() for p in [c/n for n in ('test_source_voice_protocol.py','source_actor_alias_fixture.json','source_negative_audio_fixture.json','test_source_turn_conflicts.py','source_visibility_conflict_fixture.json','test_continuity_intervals.py','continuity_zero_duration_fixtures.json','test_voice_reference_pairs.py','test_role_identity_gate.py','test_identity_intro_continuity.py','test_cast_candidate_switch.py','test_dialogue_finish.py','test_schedule_audit.py','test_browser_comparison.py','test_scene_edge_extension.py','test_scene_grouping_band_anchor.py','test_blind_visual_protocol.py','blind_timed_facts_fixture.json','test_source_offscreen_basis.py','source_offscreen_basis_fixture.json','test_auto_review_off.py','quick_preview_runner.py','test_optional_cast_state.py','test_continuity_transport.py','test_overview_protocol.py','test_source_voice_anchors.py','overview_received_fixtures.json')]})
code=r'''
from pathlib import Path
import json,hashlib,shutil,os,subprocess
h=Path('/home/ubuntu/apps/agentic-video');base=Path(BASE)
assert shutil.disk_usage(h).free>5.6*2**30
manifest={n:hashlib.sha256(v.encode()).hexdigest() for n,v in FILES.items()}
digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
release=h/'releases'/('scene_v1_schedule_'+digest[:12])
if not release.exists():
 def copy(src,dst):
  if Path(src).is_relative_to(base/'vendor'):os.link(src,dst);return dst
  return shutil.copy2(src,dst)
 shutil.copytree(base,release,copy_function=copy,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 for n,v in FILES.items():
  p=release/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(v)
 (release/'schedule_manifest.json').write_text(json.dumps(dict(sha256=digest,files=manifest,base=str(base))))
for n,sha in manifest.items():assert hashlib.sha256((release/n).read_bytes()).hexdigest()==sha
folder=h/'baselines/approved_chiikawa_dialogue_20261114_v1'
runner=folder/('reviewed_runner_'+hashlib.sha256(RUNNER.encode()).hexdigest()[:12]+'.py');runner.write_text(RUNNER)
testdir=release/'schedule_tests';testdir.mkdir(exist_ok=True)
for n,v in TESTS.items():(testdir/n).write_text(v)
env=dict(os.environ,PATH=str(h/'runtime/node/bin')+':'+os.environ['PATH'],PYTHONPATH=str(release/'src')+':'+str(release/'vendor'))
r=subprocess.run([str(h/'.venv/bin/python'),'-m','pytest',*[str(testdir/n) for n in TESTS if n.endswith('.py') and n.startswith('test_')],'-q'],env=env,capture_output=True,text=True)
print(json.dumps(dict(release=str(release),runner=str(runner),tests_returncode=r.returncode,tests=r.stdout[-5000:]+r.stderr[-1000:]),ensure_ascii=False))
'''
r=subprocess.run([*ssh,'python3 -'],input='\n'.join(k+'='+repr(v) for k,v in params.items())+'\n'+code,capture_output=True,text=True,timeout=120)
if r.returncode:raise SystemExit(r.stderr[-3000:])
receipt=json.loads(r.stdout);(c/'schedule_fix_release.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False))
