// One local-slot experiment; older stages and unknown inputs remain read-only.
import fs from 'node:fs';
import path from 'node:path';
import {jsonHash, fileHash, prefixHash, scope, canonical, parsed, received} from './mcp_forward_slot_guard.mjs';
const POLICY='microclip_finecut_v1', BASELINE=207;
const RESUME_POLICY='microclip_grid_publication_resume_v1', RESUME_BASELINE=209;
const STAGES=/^mc_(observe_[0-3]|motion|anchors|edges_[0-2]|plan|blind|review)(_repair)?$/;
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const fail=reason=>{throw new Error('library_mcp_microclip_'+reason);};
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
function files(folder) {
  const result=[];
  function visit(dir) {for(const e of fs.readdirSync(dir,{withFileTypes:true})) {
    const file=path.join(dir,e.name);
    if(e.isDirectory())visit(file);else if(e.isFile())result.push(path.relative(folder,file));
  }}
  visit(folder);return result.sort();
}
function infrastructureResume(root,state,p) {
  const rows=state.artifacts.mc_infrastructure_resume;
  if(!rows)return null;
  if(rows.length!==1||jsonHash(rows[0].path)!==rows[0].sha256)fail('infrastructure_resume_artifact_changed');
  const r=read(rows[0].path),old=state.artifacts.mc_result,resultFile=path.resolve(p.execution_directory,'result.json');
  if(old?.length!==1)fail('resume_old_result_required');
  const receiptFile=path.resolve(old[0].path),receipt=read(receiptFile),result=read(resultFile);
  const prior=state.calls.slice(BASELINE,RESUME_BASELINE),match=/^\[WinError 5\].*?: '(.+)' -> '(.+)'$/.exec(result.error??'');
  if(prior.length!==2||!same(prior.map(c=>c.name),['mc_observe_0','mc_observe_1'])||
      prior.some(c=>c.status!=='received'||c.repair_of)||!match||
      receipt.policy!==POLICY||path.resolve(receipt.result_path)!==resultFile||
      receipt.model_status!=='stopped'||result.status!=='stopped'||result.model_quality_gate_passed!==false||
      result.final_video!==null||result.final_sha256!==null||result.measured_duration_s!==null||
      result.observed_frames!==12||result.observation_rounds!==2)fail('resume_cpu_stop_only');
  const [temporary,destination]=match.slice(1).map(v=>path.resolve(v.replace(/\\+/g,'\\'))),
        token=/^\._grid_([a-f0-9]{12})_[a-f0-9]{8}$/.exec(path.basename(temporary)),
        view=path.resolve(p.execution_directory,'frames','view_2');
  if(!token||path.dirname(temporary)!==view||path.dirname(destination)!==view||
      !/^grid_[a-f0-9]{20}$/.test(path.basename(destination))||path.basename(destination).slice(5,17)!==token[1]||
      !fs.statSync(temporary).isDirectory())fail('resume_view_2_publication_only');
  const frameIds=new Set();
  for(let index=0;index<prior.length;index++) {
    const c=prior[index],descriptor=read(state.artifacts['mc_input_mc_observe_'+index][0].path),
          shown=read(descriptor.lineage_path).frames,value=parsed(root,c);
    for(const frame of shown)frameIds.add(frame.frame_id);
    if(value.next_observation?.action!=='zoom'||!Array.isArray(value.frames)||
        value.frames.length!==shown.length||!same(value.frames.map(f=>f.frame_id).sort(),shown.map(f=>f.frame_id).sort()))fail('resume_original_zoom_required');
  }
  if(frameIds.size!==result.observed_frames)fail('resume_observed_frame_count_changed');
  if(r.policy!==RESUME_POLICY||r.task_id!==state.task_id)fail('infrastructure_resume_binding_changed');
  const authorization=state.artifacts[POLICY][0].path;
  if(r.original_authorization_sha256!==jsonHash(authorization)||r.baseline_request_count!==RESUME_BASELINE||
      state.calls.length<RESUME_BASELINE||r.prefix_calls_sha256!==jsonHash(path.join(root,'library_state.json'),v=>v.calls.slice(0,RESUME_BASELINE))||
      path.resolve(r.result_path)!==resultFile||path.resolve(r.receipt_path)!==receiptFile||
      r.known_cpu_error!==result.error||r.next_stage!=='mc_observe_2'||r.new_renders!==0||
      r.reuse_original_unused_render!==true||typeof r.user_instruction!=='string'||!r.user_instruction.trim()||
      fileHash(resultFile)!==r.result_sha256||fileHash(receiptFile)!==r.receipt_sha256||
      !same(Object.keys(r.baseline_call_files).sort(),prior.map(c=>c.id).sort())||
      ['mc_render_claim','mc_recovered_result','mc_input_mc_observe_2'].some(key=>r.baseline_artifacts[key]))fail('infrastructure_resume_binding_changed');
  for(const key of Object.keys(r.baseline_artifacts))if(jsonHash(path.join(root,'library_state.json'),v=>v.artifacts[key])!==jsonHash(rows[0].path,v=>v.baseline_artifacts[key]))fail('resume_historical_artifact_changed');
  for(const [id,names] of Object.entries(r.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('resume_historical_call_files_changed');
  for(const row of r.protected_files)if(fileHash(row.path)!==row.sha256)fail('resume_historical_bytes_changed');
  for(const row of r.journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('resume_journal_prefix_changed');
  return r;
}
export function loadMicroclip(root,env=process.env) {
  const file=env.OMNI_LIBRARY_MICROCLIP_AUTH_FILE, sha=env.OMNI_LIBRARY_MICROCLIP_AUTH_SHA256;
  if(!file&&!sha)return null;
  if(!file||!sha||fileHash(file)!==sha)fail('authorization_modified');
  const stateFile=path.join(root,'library_state.json'),state=read(stateFile),p=read(file),rows=state.artifacts?.[POLICY];
  if(rows?.length!==1 || path.resolve(rows[0].path)!==path.resolve(file) || rows[0].sha256!==jsonHash(file) ||
      p.policy!==POLICY || p.task_id!==state.task_id || path.resolve(p.original_output)!==path.resolve(root) ||
      p.input_lock_sha256!==jsonHash(stateFile,v=>v.input_lock) || p.base_request_limit!==state.max_requests ||
      p.baseline_policy_version!==state.policy_version || p.baseline_request_count!==BASELINE ||
      state.calls.length<BASELINE || state.calls.length!==state.request_count ||
      p.prefix_calls_sha256!==jsonHash(stateFile,v=>v.calls.slice(0,BASELINE)) || p.stage_pattern!==STAGES.source ||
      p.max_concurrency!==1 || p.new_renders!==1 || p.repairs_per_stage!==1 ||
      p.numeric_total_request_limit!==null || p.automatic_round_loops!==false || p.goal_resumed!==false ||
      p.reference_and_library_new_inputs_forbidden!==true || fileHash(p.knowledge_path)!==p.knowledge_sha256 ||
      fileHash(p.original_authorization_path)!==p.original_authorization_sha256 ||
      path.resolve(p.execution_directory)!==path.resolve(root,'artifacts',POLICY) ||
      path.resolve(p.allowed_render_directory)!==path.resolve(p.execution_directory,'render') ||
      fileHash(p.parent.path)!==p.parent.sha256 || !read(p.outline_path).slots.some(s=>same(s,p.slot)))fail('policy_or_prefix_changed');
  if(!same(fs.readdirSync(root,{withFileTypes:true}).filter(d=>d.isDirectory()&&d.name.startsWith('render_')).map(d=>d.name).sort(),p.baseline_render_directories))fail('unapproved_legacy_render');
  for(const key of Object.keys(p.baseline_artifacts))if(jsonHash(stateFile,v=>v.artifacts[key])!==jsonHash(file,v=>v.baseline_artifacts[key]))fail('historical_artifact_changed');
  for(const [id,names] of Object.entries(p.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('historical_call_files_changed');
  for(const row of p.protected_files)if(fileHash(row.path)!==row.sha256)fail('historical_bytes_changed');
  for(const row of p.journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('journal_prefix_changed');
  for(const lost of p.unknown_inputs)if(['response.json','parsed.json'].some(name=>fs.existsSync(path.join(root,'calls',lost.call_id,name))))fail('unknown_reply_fabricated');
  const names=new Map();let pending=0;
  for(const c of state.calls.slice(BASELINE)) {
    const m=STAGES.exec(c.name),reqFile=path.join(root,'calls',c.id,'request.json');
    if(!m||names.has(c.name)||!['received','submitted','uncertain','failed_known'].includes(c.status))fail('new_stage_or_status_invalid');
    const req=read(reqFile);if(jsonHash(reqFile)!==c.request_sha256)fail('new_request_changed');
    if(m[2]) {
      const old=names.get(c.name.slice(0,-7));
      if(!old || old.status!=='received' || c.repair_of!==old.id || [...names.keys()].at(-1)!==old.name)fail('sole_repair_binding');
      const orig=read(path.join(root,'calls',old.id,'request.json'));
      if(orig.media_sha256!==req.media_sha256 || scope(orig.observation_scope)!==scope(req.observation_scope))fail('repair_input_changed');
    } else if(c.repair_of)fail('original_has_repair');
    if(c.status==='received'&&jsonHash(path.join(root,'calls',c.id,'response.json'))!==c.response_sha256)fail('new_response_changed');
    pending+=c.status==='submitted';names.set(c.name,c);
  }
  if(pending>1)fail('single_lane_required');
  for(const [key,entries] of Object.entries(state.artifacts))if(key.startsWith('mc_')) {
    if(entries.length!==1||jsonHash(entries[0].path)!==entries[0].sha256)fail('new_artifact_changed');
    const artifact=read(entries[0].path);
    if(key.startsWith('mc_input_')&&(fileHash(artifact.media_path)!==artifact.media_sha256||fileHash(artifact.lineage_path)!==artifact.lineage_sha256))fail('input_bytes_changed');
    for(const proof of artifact.completed_files||[])if(fileHash(proof.path)!==proof.sha256)fail('completed_bytes_changed');
  }
  if(fs.existsSync(p.allowed_render_directory)&&!state.artifacts.mc_render_claim)fail('render_without_claim');
  const resume=infrastructureResume(root,state,p);
  return {policy:p,state,file,resume};
}
export function microclipConfiguration(root,env=process.env){return loadMicroclip(root,env)?.policy??null;}
export function microclipRequestLimit(root,job,env=process.env) {
  const value=loadMicroclip(root,env);if(!value)return null;
  const {policy:p,state,resume}=value,added=state.calls.slice(BASELINE),call=added.find(c=>c.id===job.job_id),m=call&&STAGES.exec(call.name);
  if(!call||call.status!=='submitted'||!m||added.at(-1)!==call||added.slice(0,-1).some(c=>c.status!=='received')||
      state.artifacts.mc_result&&!resume||state.artifacts.mc_recovered_result)fail('submitted_new_call_required');
  if(resume&&state.calls.length===RESUME_BASELINE+1&&call.name!==resume.next_stage)fail('resume_next_stage_required');
  const kind=m[1],stem=call.name.replace(/_repair$/,'');
  if(m[2]) {
    const old=added.at(-2),folder=old&&path.join(root,'calls',old.id);
    if(!old||old.name!==stem||old.status!=='received'||call.repair_of!==old.id||old.repair_of||
        added.filter(c=>c.repair_of===old.id).length!==1 || !fs.existsSync(path.join(folder,'protocol_failure.json')) ||
        fs.existsSync(path.join(folder,'parsed.json')))fail('sole_known_format_repair_required');
  } else {
    if(call.repair_of)fail('original_has_repair');
    if(added.length>1)parsed(root,added.at(-2));
    if(kind==='observe_0'&&added.length!==1)fail('first_observation_required');
    if(kind.startsWith('observe_')&&kind!=='observe_0') {
      if(added.slice(0,-1).some(c=>['motion','anchors','plan','blind','review'].includes(STAGES.exec(c.name)[1])))fail('observe_after_plan');
      received(root,added,'mc_observe_'+(Number(kind.at(-1))-1));
    }
    if(kind==='motion'){received(root,added,'mc_observe_0');received(root,added,'mc_observe_1');}
    if(kind==='anchors')received(root,added,'mc_motion');
    if(kind.startsWith('edges_')) {
      received(root,added,'mc_anchors');
      if(kind!=='edges_0')received(root,added,'mc_edges_'+(Number(kind.at(-1))-1));
    }
    if(kind==='plan') {
      received(root,added,'mc_anchors');
      for(let index=0;index<3;index++)received(root,added,'mc_edges_'+index);
    }
    if(kind==='blind'&&!state.artifacts.mc_render_claim)fail('render_required');
    if(kind==='review')received(root,added,'mc_blind');
  }
  const req=read(path.join(root,'calls',call.id,'request.json')),queued=read(path.join(root,'mcp_queue',call.id+'.request.json'));
  const image=kind.startsWith('observe_')||kind.startsWith('edges_'),argument=image?'image_source':'video_source',media=path.resolve(req.arguments[argument]);
  const entries=state.artifacts['mc_input_'+stem];if(entries?.length!==1)fail('input_descriptor_required');
  const d=read(entries[0].path),fingerprint=scope(req.observation_scope),s=req.observation_scope;
  if(req.provider!=='official_vision_mcp_in_codex'||req.tool!==(image?'analyze_image':'analyze_video')||
      d.policy!==POLICY||d.stage!==stem||d.tool!==req.tool||path.resolve(d.media_path)!==media||
      fileHash(media)!==d.media_sha256||d.media_sha256!==req.media_sha256||
      fileHash(d.lineage_path)!==d.lineage_sha256||scope(d.observation_scope)!==fingerprint||
      queued.job_id!==job.job_id||queued.tool!==req.tool||!same(queued.arguments,req.arguments)||
      job.tool!==undefined&&job.tool!==req.tool||job.arguments!==undefined&&!same(job.arguments,req.arguments))fail('actual_job_binding');
  for(const lost of p.unknown_inputs)if(call.request_sha256===lost.request_sha256||req.media_sha256===lost.media_sha256||fingerprint===scope(lost.scope))fail('unknown_input_replay');
  const lineage=read(d.lineage_path);
  if(['blind','review'].includes(kind)) {
    const final=path.resolve(p.allowed_render_directory,'final.mp4'),actual=read(path.join(p.allowed_render_directory,'render_result.json'));
    if(!state.artifacts.mc_render_claim||!['continuous_window','complete_file'].includes(s.kind)||s.source_sha256!==actual.sha256||
        actual.sha256!==fileHash(final)||s.source_start_s!==0||s.source_end_s!==actual.measured_duration_s ||
        scope(lineage)!==fingerprint||lineage.sha256!==req.media_sha256||path.resolve(lineage.path)!==media||
        path.resolve(lineage.source_path)!==final)fail('actual_output_full_lineage_required');
  } else {
    if(s.source_sha256!==p.parent.sha256||s.source_start_s<p.slot.start_s||s.source_end_s>p.slot.end_s)fail('parent_slot_only');
    if(image) {
      const mf=path.resolve(d.lineage_path),r=lineage.request,src=lineage.source,g=lineage.grid;
      if(s.kind!=='sparse_contact_sheet'||lineage.schema!=='microclip_grid_v1'||path.resolve(lineage.manifest_path)!==mf||
          fs.readFileSync(path.join(path.dirname(mf),'manifest.sha256'),'ascii').trim()!==d.lineage_sha256||
          src.sha256!==p.parent.sha256||r.source_sha256!==src.sha256||path.resolve(src.path)!==path.resolve(p.parent.path)||
          path.resolve(r.source_path)!==path.resolve(src.path)||s.source_start_s!==r.start_s||s.source_end_s!==r.end_s||
          path.resolve(g.path)!==media||g.sha256!==req.media_sha256||g.crop_box!==null||g.resize_only!==true||
          !Array.isArray(lineage.frames)||lineage.frames.length<1||lineage.frames.length>6)fail('actual_grid_lineage_required');
      let last=-Infinity;const ids=new Set();
      for(const f of lineage.frames) {
        if(f.source_sha256!==p.parent.sha256||f.source_time_s<s.source_start_s||f.source_time_s>=s.source_end_s||
            f.source_time_s<=last||ids.has(f.frame_id)||fileHash(f.png_path)!==f.png_sha256||
            Math.abs(f.pts_time_s-f.pts*f.time_base[0]/f.time_base[1])>1e-9||
            Math.abs(f.source_time_s-(f.pts_time_s-src.timeline_origin_s))>1e-9)fail('grid_frame_binding');
        last=f.source_time_s;ids.add(f.frame_id);
      }
      if(kind.startsWith('edges_')) {
        const anchors=received(root,added,'mc_anchors'),key=['start','peak','end'][Number(kind.at(-1))],id=anchors[key+'_frame_id'];
        const prior=[];
        for(const [name,entries] of Object.entries(state.artifacts))if(name.startsWith('mc_input_mc_observe_')) {
          prior.push(...read(read(entries[0].path).lineage_path).frames);
        }
        const anchor=prior.find(f=>f.frame_id===id);
        if(!anchor||d.anchor_key!==key||d.anchor_frame_id!==id||
            s.source_start_s<Math.max(p.slot.start_s,anchor.source_time_s-.25)||
            s.source_start_s>anchor.source_time_s||s.source_end_s<=anchor.source_time_s||
            s.source_end_s>Math.min(p.slot.end_s,anchor.source_time_s+.25))fail('model_anchor_neighborhood_binding');
      }
    } else if(s.kind!=='continuous_window'||s.source_start_s!==p.slot.start_s||s.source_end_s!==p.slot.end_s||
        scope(lineage)!==fingerprint||lineage.sha256!==req.media_sha256||path.resolve(lineage.path)!==media||
        path.resolve(lineage.source_path)!==path.resolve(p.parent.path))fail('normal_speed_full_slot_lineage_required');
  }
  return Infinity;
}
export function microclipFrozenSlotJob(root,env=process.env) {
  const value=loadMicroclip(root,env);if(!value)return null;
  const paths=new Map(value.policy.unknown_inputs.map(row=>[path.resolve(root,'mcp_queue',row.call_id+'.request.json'),row.call_id]));
  return file=>{const id=paths.get(path.resolve(file));if(!id)return false;
    const current=loadMicroclip(root,env),old=current.state.calls.slice(0,BASELINE).find(c=>c.id===id),job=read(file),req=read(path.join(root,'calls',id,'request.json'));
    if(old?.status!=='uncertain'||job.job_id!==id||job.tool!==req.tool||!same(job.arguments,req.arguments))fail('frozen_queue_binding');return true;};
}
