// CPU projection of exactly one received repair's inactive range metadata.
// No historical parsed file, model verdict or executable cut is created here.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {jsonHash,fileHash,prefixHash,canonical,parsed as baseParsed,received as baseReceived} from './mcp_forward_slot_guard.mjs';

const ARTIFACT='mc2_boundary_inactive_range_resume',POLICY='microclip_v2_boundary_inactive_range_projection_v1',BASELINE=238;
const CALL_ID='glm_238_mc2_edge_1_nav_1_repair',ORIGINAL_ID='glm_237_mc2_edge_1_nav_1',PRIOR_ID='glm_232_mc2_edge_1_nav_0';
const STEM='mc2_edge_1_nav_1',NEXT='mc2_edge_1_confirm_1',ERROR='microclip_v2:boundary_confirm_has_no_observation_envelope';
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const digest=v=>crypto.createHash('sha256').update(JSON.stringify(canonical(v))).digest('hex');
const fail=reason=>{throw new Error('library_mcp_microclip_boundary_metadata_'+reason);};
function files(folder) {
  const result=[];function visit(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){
    const file=path.join(dir,e.name);if(e.isDirectory())visit(file);else if(e.isFile())result.push(path.relative(folder,file));
  }}visit(folder);return result.sort();
}
function raw(root,call) {
  if(!call||call.status!=='received'||jsonHash(path.join(root,'calls',call.id,'response.json'))!==call.response_sha256)fail('known_received_reply_required');
  const response=read(path.join(root,'calls',call.id,'response.json')),
    text=response.result.content.filter(c=>c.type==='text').map(c=>c.text).join('\n');
  let content=text.trim();
  if(content.startsWith('```')) {
    const lines=content.split(/\r?\n/);if(!['```','```json'].includes(lines[0])||lines.at(-1)!=='```')fail('known_json_fence_required');
    content=lines.slice(1,-1).join('\n');
  }
  return {value:JSON.parse(content),text};
}
function text(value){return typeof value==='string'&&value.trim().length>0;}
function strings(value){return Array.isArray(value)&&value.every(v=>typeof v==='string');}
function ids(values,wanted,empty=false){return Array.isArray(values)&&(empty||values.length>0)&&new Set(values).size===values.length&&values.every(v=>typeof v==='string'&&wanted.has(v));}
function semanticConfirm(value,catalog,p,event,intent,previous,fresh) {
  const wanted=new Set(event.obligation_ids),allowed=new Set(intent.obligations.map(o=>o.obligation_id));
  if(value.action!=='confirm'||!['existing_shot_boundary','semantic_trim'].includes(value.cut_intent)||value.event_id!==event.event_id||
      !catalog.has(value.anchor_frame_id)||previous.anchor_frame_id&&value.anchor_frame_id!==previous.anchor_frame_id||
      [...wanted].some(id=>!allowed.has(id))||!ids(value.obligation_ids,wanted)||value.obligation_ids.length!==wanted.size||
      !strings(value.blocking_questions)||value.blocking_questions.length||!strings(value.limitations)||
      typeof value.question!=='string'||typeof value.resolution!=='string'||!text(value.resolution)||
      ['confirm','confirmed','semantic_trim','existing_shot_boundary'].includes(value.resolution.trim())||
      !ids(value.evidence_frame_ids,new Set(catalog.keys()))||!value.evidence_frame_ids.includes(value.confirmed_frame_id)||
      !catalog.has(value.confirmed_frame_id)||value.start_frame_id!=null||value.end_frame_id!=null||value.source_start_s!=null||value.source_end_s!=null)fail('projected_semantic_contract_failed');
  const row=catalog.get(value.confirmed_frame_id),sha=catalog.get(value.anchor_frame_id).source_sha256;
  if(value.evidence_frame_ids.some(id=>catalog.get(id).source_sha256!==sha)||row.source_sha256!==sha||
      !(p.slot.start_s<=row.source_time_s&&row.source_time_s<p.slot.end_s&&row.frame_end_s!=null&&row.frame_end_s<=p.slot.end_s+1e-6)||
      !(value.evidence_frame_ids.some(id=>fresh.has(id))||
        (previous.cut_intent??'existing_shot_boundary')==='existing_shot_boundary'&&value.cut_intent==='semantic_trim'))fail('projected_semantic_contract_failed');
}
function projection(root,state,p) {
  const orig=state.calls[236],call=state.calls[237],prior=state.calls[231];
  if(orig?.id!==ORIGINAL_ID||orig.name!==STEM||orig.repair_of||call?.id!==CALL_ID||call.name!==STEM+'_repair'||
      call.repair_of!==ORIGINAL_ID||prior?.id!==PRIOR_ID)fail('sole_known_repair_binding');
  const actual=raw(root,call),original=raw(root,orig);
  for(const [c,r,attempt] of [[orig,original,0],[call,actual,1]]) {
    const folder=path.join(root,'calls',c.id);if(fs.existsSync(path.join(folder,'parsed.json')))fail('historical_failure_cannot_be_relabelled');
    const failure=read(path.join(folder,'protocol_failure.json'));
    if(failure.error!==ERROR||failure.attempt!==attempt||failure.model_text!==r.text)fail('only_inactive_range_failure_allowed');
  }
  const value=actual.value,old=original.value,observed=baseParsed(root,prior),planRows=state.artifacts.mc2_boundary_page_plan_1_0;
  if(old.action!=='confirm'||value.action!=='confirm'||old.source_start_s!==0||old.source_end_s!==0||
      value.source_start_s==null||value.source_end_s==null||value.blocking_questions?.length||observed.action!=='observe'||
      value.source_start_s!==observed.source_start_s||value.source_end_s!==observed.source_end_s||planRows?.length!==1)fail('known_confirm_projection_only');
  const plan=read(planRows[0].path);
  if(plan.decision_call_id!==PRIOR_ID||plan.decision_stage!==prior.name||plan.source_start_s!==observed.source_start_s||
      plan.source_end_s!==observed.source_end_s)fail('completed_observation_plan_changed');
  const catalog=new Map(),pages=new Set();
  for(const candidate of state.calls.slice(0,BASELINE)) {
    const folder=path.join(root,'calls',candidate.id),stem=candidate.name.replace(/_repair$/,''),rows=state.artifacts['mc2_input_'+stem];
    if(candidate.status!=='received'||!rows||!fs.existsSync(path.join(folder,'parsed.json'))||fs.existsSync(path.join(folder,'protocol_failure.json')))continue;
    baseParsed(root,candidate);const d=read(rows[0].path);
    if(d.tool==='analyze_image'&&d.observation_scope.source_sha256===p.parent.sha256){
      for(const f of read(d.lineage_path).frames)catalog.set(f.frame_id,f);pages.add(stem);
    }
  }
  if(plan.pages.some((row,i)=>!pages.has('mc2_edge_1_view_0_'+i)))fail('all_observation_pages_received_required');
  const previous=baseReceived(root,state.calls,'mc2_edge_1'),event=baseReceived(root,state.calls,'mc2_anchors').events[0],intent=baseReceived(root,state.calls,'mc2_intent');
  const projected={...value,source_start_s:null,source_end_s:null};
  semanticConfirm(projected,catalog,p,event,intent,previous,new Set(plan.new_frame_ids));
  const context={kind:'non_executable_prior_observation',prior_call_id:PRIOR_ID,prior_response_sha256:prior.response_sha256,
    page_plan_sha256:planRows[0].sha256,source_start_s:value.source_start_s,source_end_s:value.source_end_s};
  return {call,rawValue:value,projected,context};
}
export function loadBoundaryMetadata(root,state,p,boundaryResume) {
  const rows=state.artifacts?.[ARTIFACT];if(!rows)return null;
  if(rows.length!==1||!boundaryResume||jsonHash(rows[0].path)!==rows[0].sha256)fail('one_bound_metadata_resume_required');
  const file=rows[0].path,r=read(file),stateFile=path.join(root,'library_state.json');
  if(r.policy!==POLICY||r.task_id!==state.task_id||r.baseline_request_count!==BASELINE||state.calls.length!==state.request_count||state.calls.length<BASELINE||
      r.prefix_calls_sha256!==jsonHash(stateFile,v=>v.calls.slice(0,BASELINE))||
      r.boundary_authorization_sha256!==jsonHash(state.artifacts.mc2_boundary_navigation_resume[0].path)||
      r.next_stage!==NEXT||r.new_renders!==0||r.reuse_original_unused_render!==true||r.goal_resumed!==false||
      r.automatic_round_loops!==false||r.numeric_total_request_limit!==null||!text(r.user_instruction))fail('permission_or_prefix_changed');
  for(const key of Object.keys(r.baseline_artifacts))if(jsonHash(stateFile,v=>v.artifacts[key])!==jsonHash(file,v=>v.baseline_artifacts[key]))fail('historical_artifact_changed');
  for(const [id,names] of Object.entries(r.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('historical_call_files_changed');
  for(const row of r.protected_files)if(fileHash(row.path)!==row.sha256)fail('historical_bytes_changed');
  for(const row of r.journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('historical_journal_changed');
  const actual=projection(root,state,p);
  if(r.call_id!==actual.call.id||r.model_response_sha256!==actual.call.response_sha256||r.raw_value_sha256!==digest(actual.rawValue)||
      !same(r.projected_value,actual.projected)||r.projected_value_sha256!==jsonHash(file,v=>v.projected_value)||
      !same(r.prior_observation_context,actual.context))fail('projection_binding_changed');
  return r;
}
export function receivedProjection(root,state,stem) {
  if(![STEM,STEM+'_repair'].includes(stem)||!state.artifacts?.[ARTIFACT])return null;
  const boundary=read(state.artifacts.mc2_boundary_navigation_resume[0].path),p=boundary.base_authorization,
    r=loadBoundaryMetadata(root,state,p,boundary);
  return {call:state.calls[BASELINE-1],value:r.projected_value};
}
export function metadataReceived(root,state,stem) {return receivedProjection(root,state,stem)?.value??baseReceived(root,state.calls,stem);}
export function metadataParsed(root,state,call) {
  if(call?.id===CALL_ID){const projected=receivedProjection(root,state,call.name);if(projected)return projected.value;}
  return baseParsed(root,call);
}
