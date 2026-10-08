// Same-event observation continuation. Never changes a historical model reply.
import fs from 'node:fs';
import path from 'node:path';
import {jsonHash,fileHash,prefixHash,canonical,scope} from './mcp_forward_slot_guard.mjs';
import {loadBoundaryMetadata,metadataParsed,metadataReceived} from './mcp_microclip_boundary_metadata.mjs';

const ARTIFACT='mc2_boundary_navigation_resume',POLICY='microclip_v2_boundary_navigation_resume_v1',BASELINE=231;
const PATTERN='^mc2_edge_([0-9]|1[01])_(nav|confirm)_([0-3])(_repair)?$|^mc2_edge_([0-9]|1[01])_view_([0-2])_([0-3])(_repair)?$';
const STAGES=new RegExp(PATTERN);
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const fail=reason=>{throw new Error('library_mcp_microclip_boundary_'+reason);};
const parsed=(root,call)=>metadataParsed(root,read(path.join(root,'library_state.json')),call);
const received=(root,calls,stem)=>metadataReceived(root,read(path.join(root,'library_state.json')),stem);
export function boundaryStage(name) {
  const m=STAGES.exec(name);if(!m)return null;
  return m[1]!==undefined?{index:Number(m[1]),phase:m[2],round:Number(m[3]),page:null,repair:Boolean(m[4])}:
    {index:Number(m[5]),phase:'view',round:Number(m[6]),page:Number(m[7]),repair:Boolean(m[8])};
}
function files(folder) {
  const result=[];function visit(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){
    const file=path.join(dir,e.name);if(e.isDirectory())visit(file);else if(e.isFile())result.push(path.relative(folder,file));
  }}visit(folder);return result.sort();
}
function blocked(root,calls,index) {
  const stem='mc2_edge_'+index,call=[...calls].reverse().find(c=>[stem,stem+'_repair'].includes(c.name));
  const value=parsed(root,call);if(value.status!=='blocked'||!value.blocking_questions?.length)fail('original_semantic_block_required');
  return {call,value};
}
export function effectiveBoundary(root,state,index) {
  const rows=state.artifacts['mc2_effective_edge_'+index];if(!rows)return null;
  if(rows.length!==1||jsonHash(rows[0].path)!==rows[0].sha256)fail('effective_edge_binding_changed');
  const r=read(rows[0].path),call=state.calls.slice(BASELINE).find(c=>c.id===r.confirmed_call_id),m=call&&boundaryStage(call.name);
  if(!call||!m||m.index!==index||m.phase!=='confirm')fail('confirmed_navigation_required');
  const value=parsed(root,call),original=blocked(root,state.calls,index),rowsD=state.artifacts['mc2_input_'+call.name.replace(/_repair$/,'')];
  if(r.policy!==POLICY||r.edge_index!==index||r.event_index!==Math.floor(index/2)||rowsD?.length!==1||
      r.input_descriptor_sha256!==rowsD[0].sha256||r.original_blocked_call_id!==original.call.id||
      r.model_response_sha256!==call.response_sha256||r.parsed_sha256!==jsonHash(path.join(root,'calls',call.id,'parsed.json'))||
      value.action!=='confirm'||value.confirmed_frame_id!==r.confirmed_frame_id||value.blocking_questions?.length)fail('effective_reply_changed');
  const descriptor=read(rowsD[0].path),shown=read(descriptor.lineage_path).frames;
  const event=received(root,state.calls,'mc2_anchors').events[Math.floor(index/2)];
  if(value.event_id!==event.event_id||value.anchor_frame_id!==event[['start','end'][index%2]+'_frame_id']||
      !same([...value.obligation_ids].sort(),[...event.obligation_ids].sort())||!shown.some(f=>f.frame_id===value.confirmed_frame_id))fail('effective_event_or_candidate_changed');
  return {call,value:{...value,status:'confirmed'},descriptor};
}
export function loadBoundaryMicroclip(root,env=process.env) {
  const stateFile=path.join(root,'library_state.json'),state=read(stateFile),rows=state.artifacts?.[ARTIFACT];
  if(!rows)return null;
  if(rows.length!==1||jsonHash(rows[0].path)!==rows[0].sha256)fail('permission_artifact_changed');
  const file=rows[0].path,r=read(file),p=r.base_authorization;
  if(r.policy!==POLICY||r.task_id!==state.task_id||r.baseline_request_count!==BASELINE||
      state.calls.length!==state.request_count||state.calls.length<BASELINE||
      r.prefix_calls_sha256!==jsonHash(stateFile,v=>v.calls.slice(0,BASELINE))||
      r.input_lock_sha256!==jsonHash(stateFile,v=>v.input_lock)||r.base_policy_version!==state.policy_version||
      r.base_request_limit!==state.max_requests||r.new_renders!==0||r.reuse_original_unused_render!==true||
      r.goal_resumed!==false||r.automatic_round_loops!==false||r.numeric_total_request_limit!==null||r.stage_pattern!==PATTERN||
      r.first_stage!=='mc2_edge_1_nav_0'||typeof r.user_instruction!=='string'||!r.user_instruction.trim()||
      fileHash(p.authorization_path)!==p.authorization_sha256||
      path.resolve(env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE||'')!==path.resolve(p.authorization_path)||
      env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256!==p.authorization_sha256||
      fileHash(p.parent.path)!==p.parent.sha256||!read(p.outline_path).slots.some(s=>same(s,p.slot))||
      fileHash(r.knowledge_path)!==r.knowledge_sha256)fail('permission_or_prefix_changed');
  for(const key of Object.keys(r.baseline_artifacts))if(jsonHash(stateFile,v=>v.artifacts[key])!==jsonHash(file,v=>v.baseline_artifacts[key]))fail('historical_artifact_changed');
  for(const [id,names] of Object.entries(r.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('historical_call_files_changed');
  for(const row of r.protected_files)if(fileHash(row.path)!==row.sha256)fail('historical_bytes_changed');
  for(const row of r.journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('historical_journal_changed');
  for(const lost of p.unknown_inputs)if(['response.json','parsed.json'].some(n=>fs.existsSync(path.join(root,'calls',lost.call_id,n))))fail('unknown_reply_fabricated');
  if(!same(fs.readdirSync(root,{withFileTypes:true}).filter(e=>e.isDirectory()&&e.name.startsWith('render_')).map(e=>e.name).sort(),p.baseline_render_directories))fail('unapproved_legacy_render');
  if(fs.existsSync(p.allowed_render_directory)&&!state.artifacts.mc2_render_claim)fail('render_without_claim');
  const names=new Map();let pending=0;
  for(const c of state.calls.slice(BASELINE)) {
    const m=boundaryStage(c.name),oldPattern=new RegExp(p.stage_pattern),base=oldPattern.exec(c.name),repair=m?m.repair:Boolean(base?.[2]);
    if(!m&&!base||names.has(c.name)||!['submitted','received','uncertain','failed_known'].includes(c.status))fail('new_stage_or_status_invalid');
    const reqFile=path.join(root,'calls',c.id,'request.json'),req=read(reqFile);
    if(jsonHash(reqFile)!==c.request_sha256)fail('new_request_changed');
    if(repair) {
      const old=names.get(c.name.slice(0,-7));if(!old||old.status!=='received'||[...names.keys()].at(-1)!==old.name||c.repair_of!==old.id)fail('sole_repair_binding');
      const previous=read(path.join(root,'calls',old.id,'request.json'));
      if(previous.media_sha256!==req.media_sha256||scope(previous.observation_scope)!==scope(req.observation_scope))fail('repair_input_changed');
    }else if(c.repair_of)fail('original_has_repair');
    if(c.status==='received'&&jsonHash(path.join(root,'calls',c.id,'response.json'))!==c.response_sha256)fail('new_response_changed');
    pending+=c.status==='submitted';names.set(c.name,c);
  }
  if(pending>1)fail('single_lane_required');
  for(const [key,entries] of Object.entries(state.artifacts))if(key.startsWith('mc2_')) {
    if(entries.length!==1||jsonHash(entries[0].path)!==entries[0].sha256)fail('artifact_changed');
    const value=read(entries[0].path);
    if(key.startsWith('mc2_input_')&&(fileHash(value.media_path)!==value.media_sha256||fileHash(value.lineage_path)!==value.lineage_sha256))fail('input_bytes_changed');
    for(const proof of value.completed_files||[])if(fileHash(proof.path)!==proof.sha256)fail('completed_bytes_changed');
    if(key.startsWith('mc2_effective_edge_'))effectiveBoundary(root,state,Number(key.split('_').at(-1)));
    if(key.startsWith('mc2_boundary_page_plan_'))pagePlanProof(root,state,p,value);
  }
  return {policy:p,state,file:p.authorization_path,resume:p.infrastructure_resume,networkResume:p.known_failure_resume,
    dispatchResume:p.known_failure_dispatch_resume,boundaryResume:r,
    boundaryMetadataResume:loadBoundaryMetadata(root,state,p,r)};
}
function pagePlan(state,index,round) {
  const rows=state.artifacts[`mc2_boundary_page_plan_${index}_${round}`];if(rows?.length!==1)fail('bound_page_plan_required');
  const plan=read(rows[0].path);if(!Array.isArray(plan.pages)||plan.pages.length<1||plan.pages.length>4||!plan.new_frame_ids?.length)fail('new_evidence_need_required');
  return plan;
}
export function pagePlanProof(root,state,p,plan) {
  const decisionIndex=state.calls.findIndex(c=>c.id===plan.decision_call_id),call=state.calls[decisionIndex],m=call&&boundaryStage(call.name);
  if(!call||!m||!['nav','confirm'].includes(m.phase)||m.round>=3)fail('page_decision_call_required');
  const decision=parsed(root,call);
  if(call.name.replace(/_repair$/,'')!==plan.decision_stage||decision.action!=='observe'||
      plan.source_start_s!==decision.source_start_s||plan.source_end_s!==decision.source_end_s||
      !(p.slot.start_s<=plan.source_start_s&&plan.source_start_s<plan.source_end_s&&plan.source_end_s<=p.slot.end_s&&
        plan.source_end_s-plan.source_start_s<=.600001)||plan.pages?.length<1||plan.pages?.length>4)fail('page_decision_scope_changed');
  const observed=new Set();
  for(const c of state.calls.slice(0,decisionIndex+1)) {
    const folder=path.join(root,'calls',c.id),rows=state.artifacts['mc2_input_'+c.name.replace(/_repair$/,'')];
    if(c.status!=='received'||!rows||!fs.existsSync(path.join(folder,'parsed.json'))||fs.existsSync(path.join(folder,'protocol_failure.json')))continue;
    const d=read(rows[0].path);if(d.tool==='analyze_image'&&d.observation_scope.source_sha256===p.parent.sha256)
      for(const f of read(d.lineage_path).frames)observed.add(f.frame_id);
  }
  const continuous=new Map();
  for(const row of plan.pages) {
    const manifest=read(row.manifest_path);
    if(manifest.frames?.length>6||manifest.source.sha256!==p.parent.sha256||fileHash(row.manifest_path)!==
        fs.readFileSync(path.join(path.dirname(row.manifest_path),'manifest.sha256'),'ascii').trim())fail('page_manifest_changed');
    for(const f of manifest.frames)if(plan.source_start_s<=f.source_time_s&&f.source_time_s<plan.source_end_s) {
      if(f.source_sha256!==p.parent.sha256||fileHash(f.png_path)!==f.png_sha256)fail('page_frame_binding');
      continuous.set(f.frame_id,f);
    }
  }
  const frames=[...continuous.values()].sort((a,b)=>a.source_time_s-b.source_time_s),fresh=[...continuous.keys()].filter(id=>!observed.has(id)).sort();
  if(!frames.length||!fresh.length||!same(fresh,[...plan.new_frame_ids].sort())||
      frames.some((f,i)=>i&&f.decode_frame_index!==frames[i-1].decode_frame_index+1))fail('actual_new_continuous_frames_required');
  return plan;
}
export function boundaryPredecessor(root,state,p,call) {
  const m=boundaryStage(call.name),stem=`mc2_edge_${m.index}`,original=blocked(root,state.calls,m.index);
  const events=received(root,state.calls,'mc2_anchors').events;
  if(m.index>=events.length*2||state.artifacts[`mc2_effective_edge_${m.index}`])fail('same_unresolved_event_required');
  if(m.phase==='nav'&&m.round) {
    const plan=pagePlan(state,m.index,m.round-1);for(let i=0;i<plan.pages.length;i++)received(root,state.calls,`${stem}_view_${m.round-1}_${i}`);
  } else if(m.phase==='view') {
    const plan=pagePlan(state,m.index,m.round),decision=received(root,state.calls,plan.decision_stage);
    if(decision.action!=='observe')fail('model_requested_observation_required');
    if(m.page)received(root,state.calls,`${stem}_view_${m.round}_${m.page-1}`);
  }else if(m.phase==='confirm') {
    if(received(root,state.calls,`${stem}_nav_${m.round}`).action!=='confirm')fail('model_selected_candidate_required');
  }
}
export function boundaryInputBinding(root,state,p,call,d,lineage) {
  const m=boundaryStage(call.name),stem=`mc2_edge_${m.index}`,event=received(root,state.calls,'mc2_anchors').events[Math.floor(m.index/2)];
  if(d.edge_index!==m.index||d.event_id!==event.event_id||d.anchor_key!==['start','end'][m.index%2]||d.boundary_round!==m.round||d.phase!==m.phase)fail('same_event_descriptor_required');
  if(m.phase==='view') {
    const plan=pagePlanProof(root,state,p,pagePlan(state,m.index,m.round)),decisionCall=[...state.calls].reverse().find(c=>[plan.decision_stage,plan.decision_stage+'_repair'].includes(c.name));
    const decision=received(root,state.calls,plan.decision_stage),nav=[...state.calls].reverse().find(c=>[`${stem}_nav_${m.round}`,`${stem}_nav_${m.round}_repair`].includes(c.name));
    if(![`${stem}_nav_${m.round}`,`${stem}_confirm_${m.round}`].includes(plan.decision_stage)||
        plan.decision_call_id!==decisionCall.id||plan.nav_call_id!==nav.id||d.nav_call_id!==nav.id||d.page_index!==m.page||m.page>=plan.pages.length||
        d.lineage_path!==plan.pages[m.page].manifest_path||plan.source_start_s!==decision.source_start_s||plan.source_end_s!==decision.source_end_s||
        !(plan.source_start_s<plan.source_end_s&&plan.source_end_s-plan.source_start_s<=.600001))fail('new_model_requested_frames_required');
  }else if(m.phase==='confirm') {
    const nav=[...state.calls].reverse().find(c=>[`${stem}_nav_${m.round}`,`${stem}_nav_${m.round}_repair`].includes(c.name));
    const decision=parsed(root,nav),candidate=lineage.frames.find(f=>f.frame_id===decision.confirmed_frame_id);
    const scopeValue=read(path.join(root,'calls',call.id,'request.json')).observation_scope;
    if(d.nav_call_id!==nav.id||!candidate||d.selected_frame_id!==candidate.frame_id||
        scopeValue.source_start_s<Math.max(p.slot.start_s,candidate.source_time_s-.25)||
        scopeValue.source_start_s>candidate.source_time_s||scopeValue.source_end_s<=candidate.source_time_s||
        scopeValue.source_end_s>Math.min(p.slot.end_s,candidate.source_time_s+.25)+1e-6)fail('confirmation_observation_binding_required');
  }else if(m.round) {
    const plan=pagePlan(state,m.index,m.round-1);if(d.lineage_path!==plan.pages.at(-1).manifest_path)fail('navigation_must_reuse_received_page');
  }else {
    const old=read(state.artifacts[`mc2_input_mc2_edge_${m.index}`][0].path);
    if(d.lineage_path!==old.lineage_path)fail('first_navigation_reuses_blocked_evidence');
  }
}
