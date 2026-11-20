"""Render the already-received GLM draft, with measured source-clone speech.

Search, casting and writing are retained; existing semantic scheduling is restored.
The preview keeps the saved cast and words; its limitations remain explicit.
"""
from pathlib import Path
from copy import deepcopy
import json,math,os,sys,traceback

parent,out=map(Path,sys.argv[1:3]);out.mkdir(parents=True,exist_ok=True)
home=Path('/home/ubuntu/apps/agentic-video')
for key,value in json.loads((home/'credentials.json').read_text()).items():
    if key in {'DASHSCOPE_API_KEY','Z_AI_API_KEY'}:os.environ[key]=value
from omni_story.library.state import LibraryState,write_json
from omni_story.library.media import sha256_file
from omni_story.library.character_voice import CharacterVoices
from omni_story.library import joint_dialogue as joint
from omni_story.library.dialogue_cut import deliver
from omni_story.library.contracts import reference_strategy

def read(name):return json.loads((parent/name).read_text())
old=read('library_state.json')
assert all(c['status'] in {'received','failed_known'} for c in old['calls'])
input_lock=deepcopy(old['input_lock'])
input_lock['configuration']['quick_preview']=dict(parent=str(parent),output=str(out),
    reason='Generic reviewed shot scheduling with retained GLM words, cast and original voices.')
input_lock['configuration']['independent_final_visual_v1']=True
if '--resume-ledger' in sys.argv:
    ledger=Path(sys.argv[sys.argv.index('--resume-ledger')+1])
    assert all(c['status'] in {'received','failed_known'} for c in json.loads(ledger.read_text())['calls'])
    input_lock['configuration']['resume_from']=dict(ledger=dict(path=str(ledger),sha256=sha256_file(ledger)))
state=LibraryState(out,input_lock,max_requests=20,registry_path=out.parent/'quick_preview_runs.json')
write_json(out/'preview_provenance.json',dict(parent=str(parent),
    source_script_sha256=sha256_file(parent/'dialogue_script.json'),
    source_lock_sha256=sha256_file(parent/'material_lock.json'),
    authored_by='existing_received_GLM_draft',picture_schedule='reviewed_joint_dialogue_v2',
    cast_and_words_changed=False,production_quality_approved=False))
try:
    lock=read('material_lock.json');windows=read('watched_windows.json');script=read('dialogue_script.json')
    voices=read('autonomous_material/character_voices/cast_voices.json')
    service=CharacterVoices(out/'character_voices');service.voices=voices
    service.reuse_folders=[str(parent/'evaluations/fast_preview/character_voices'),str(parent/'autonomous_material/character_voices')]
    if '--resume-ledger' in sys.argv:service.reuse_folders.insert(0,str(ledger.parent/'character_voices'))
    scenes={s['scene_id']:s for s in lock['scenes']}
    shots={s['slot_id']:s for scene in lock['scenes'] for s in scene['coverage_shots']}
    lines=[];samples={}
    for raw in script['lines']:
        line=dict(raw,id='line_'+str(len(lines)+1),mode='dialogue',picture_relation='quick_preview')
        scene=scenes[line['scene_id']];binding=scene['role_bindings'][line['role']]
        line['speaker']={k:binding[k] for k in ('window_id','role_id')}
        if '--reuse-speech-only' in sys.argv:
            from omni_story.library.character_voice import MODEL
            from omni_story.library.state import json_sha
            key='speech_'+json_sha(dict(model=MODEL,voice=voices[line['role']],text=line['text']))[:16]+'.json'
            assert any((Path(folder)/key).exists() for folder in service.reuse_folders),'confirmed_speech_missing:'+line['line_id']
        audio=service.render_line(line,voices[line['role']])
        assert len(audio)>0
        lines.append(line);samples[line['line_id']]=audio
        write_json(out/'preview_progress.json',dict(stage='synthesizing',completed_lines=len(lines),total_lines=len(script['lines'])))
    from omni_story.library.server_cli import settings
    from omni_story.library.opencode_provider import OpenCodeMCP
    from omni_story.library.schedule_audit import audit_schedule,bind_performance_permissions
    runtime=settings(home)
    glm=OpenCodeMCP(state,package_root=runtime['mcp_package_root'],executable=runtime.get('opencode_executable','opencode'))
    allocation,timing=joint._allocate(lock,lines,script,state,glm,out,1)
    audit=audit_schedule(script,lines,allocation,shots)
    write_json(out/'schedule_audit.json',audit)
    if audit['collapsed_plan']:
        # One bounded repair: keep independently approved GLM ranking, but
        # restrict each line to its highest-ranked option to preserve intent.
        for line in lines:
            approved=line['shot_options']
            line['shot_ids']=[approved[0]['shot_id']] if approved else line['shot_ids']
        allocation,timing=joint._allocate(lock,lines,script,state,glm,out,2)
        audit=audit_schedule(script,lines,allocation,shots)
        audit['repair_attempted']=True
        write_json(out/'schedule_audit.json',audit)
        # An unresolved quality concern must not discard a playable artifact.
    timeline=joint.to_timeline(allocation,lock,windows)
    from omni_story.library.dialogue_finish import remove_micro_tails,verified_masks
    write_json(out/'micro_tail_finish.json',remove_micro_tails(timeline.rows,lines))
    final_allocation=[dict(slot_id=r['slot_id'],line_id=r.get('line_id'),source_sha256=shots[r['slot_id']]['source_sha256'],
        first=round(r['source_in_s']*joint.FPS),last=round(r['source_out_s']*joint.FPS)) for r in timeline.rows]
    audit=audit_schedule(script,lines,final_allocation,shots)
    write_json(out/'schedule_audit.json',audit)
    cache=out/'audio_cache';cache.mkdir(exist_ok=True)
    audio_lines,captions=joint.place_audio(lines,timeline.rows,samples,cache,'quick_preview_measured_speech')
    for row in timeline.rows:
        shot=shots[row['slot_id']]
        row.update(role_ids=[shot['local_actor_bindings'][a] for a in shot['visible']],
            speaking_role_ids=[shot['local_actor_bindings'][a] for a in shot['speaking']],
            actor_ids=shot['visible'],speaking_actor_ids=shot['speaking'])
    bind_performance_permissions(lock,lines,timeline.rows,shots)
    proxies={wid:read('autonomous_material/browser/'+wid+'_media.json') for wid in {r['window_id'] for r in timeline.rows}}
    observations={wid:read('autonomous_material/browser/'+wid+'_visual.json') for wid in proxies}
    masks=verified_masks(timeline.rows,proxies,observations)
    assert masks is not None,'subtitle_mask_evidence_missing'
    write_json(out/'subtitle_mask_finish.json',masks)
    write_json(out/'dialogue_script.json',script);write_json(out/'material_lock.json',lock)
    write_json(out/'joint_schedule.json',dict(policy='reviewed_joint_dialogue_v2',timing=timing,allocation=allocation))
    write_json(out/'preview_progress.json',dict(stage='rendering',completed_lines=len(lines),total_lines=len(lines)))
    reference=read('reference_reading.json')
    result=deliver(lock,reference,reference_strategy(reference),read('catalog/inventory.json'),state,cache,out,
        voices,script,timeline.rows,lines,audio_lines,captions,[],degradation=None,auto_review=False)
    write_json(out/'preview_progress.json',dict(stage='completed',final_video=result['final_video']))
except BaseException as exc:
    write_json(out/'preview_failure.json',dict(error=type(exc).__name__+':'+str(exc)))
    traceback.print_exc();raise
