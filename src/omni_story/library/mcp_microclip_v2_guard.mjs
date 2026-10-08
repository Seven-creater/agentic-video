// Transport isolation for one corrected whole-slot trial, with frozen history.
import fs from 'node:fs';
import path from 'node:path';
import {jsonHash, fileHash, prefixHash, scope, canonical, parsed, received as baseReceived} from './mcp_forward_slot_guard.mjs';
import {loadKnownHttp500Resume,loadKnownHttp500DispatchResume} from './mcp_microclip_known500.mjs';
import {loadBoundaryMicroclip,boundaryStage,effectiveBoundary,boundaryPredecessor,boundaryInputBinding} from './mcp_microclip_boundary_guard.mjs';
import {receivedProjection,metadataParsed} from './mcp_microclip_boundary_metadata.mjs';

const POLICY='microclip_slot_finecut_v2', BASELINE=218;
const RESUME_POLICY='microclip_v2_frame_catalog_resume_v1', RESUME_BASELINE=222;
const STAGES=/^mc2_(overview|motion|intent|region_[0-5]|anchors|edge_(?:[0-9]|1[01])|plan|slice_(?:[0-9]|1[01])|source_check|blind_video|blind_page_[0-9]|review)(_repair)?$/;
const RETRY_STAGE='mc2_region_1_retry';
const DISPATCH_STAGE='mc2_region_1_retry_dispatch';
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const fail=reason=>{throw new Error('library_mcp_microclip_v2_'+reason);};
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const stableFrame=row=>Object.fromEntries(Object.entries(row).filter(([key])=>!['png_path','requested_time_s'].includes(key)));
function stage(name) {
  const navigation=boundaryStage(name);
  if(navigation)return [name,'boundary',navigation.repair?'_repair':undefined];
  if([RETRY_STAGE,RETRY_STAGE+'_repair',DISPATCH_STAGE,DISPATCH_STAGE+'_repair'].includes(name))return [name,'region_1',name.endsWith('_repair')?'_repair':undefined];
  return STAGES.exec(name);
}
function received(root,calls,stem) {
  const metadata=receivedProjection(root,read(path.join(root,'library_state.json')),stem);
  if(metadata)return metadata.value;
  const match=/^mc2_edge_([0-9]|1[01])$/.exec(stem);
  if(match){const state=read(path.join(root,'library_state.json'));if(state.artifacts.mc2_boundary_navigation_resume){
    const effective=effectiveBoundary(root,state,Number(match[1]));if(effective)return effective.value;
  }}
  if(['mc2_region_1',RETRY_STAGE].includes(stem)&&calls.some(c=>c.name===DISPATCH_STAGE))stem=DISPATCH_STAGE;
  else if(stem==='mc2_region_1'&&calls.some(c=>c.name===RETRY_STAGE))stem=RETRY_STAGE;
  return baseReceived(root,calls,stem);
}
const settled=(call,resume,dispatch)=>call.status==='received'||resume&&call.id===resume.failed_call_id&&call.status==='failed_known'||
  dispatch&&call.id===dispatch.failed_call_id&&call.status==='failed_known';
function files(folder) {
  const result=[];
  function visit(dir) {for(const e of fs.readdirSync(dir,{withFileTypes:true})) {
    const file=path.join(dir,e.name);
    if(e.isDirectory())visit(file);else if(e.isFile())result.push(path.relative(folder,file));
  }}
  visit(folder);return result.sort();
}
function infrastructureResume(root,state,p) {
  const rows=state.artifacts.mc2_infrastructure_resume;
  if(!rows)return null;
  if(rows.length!==1||jsonHash(rows[0].path)!==rows[0].sha256)fail('infrastructure_resume_artifact_changed');
  const r=read(rows[0].path),old=state.artifacts.mc2_result,resultFile=path.resolve(p.execution_directory,'result.json');
  if(old?.length!==1)fail('resume_original_stop_required');
  const receiptFile=path.resolve(old[0].path),receipt=read(receiptFile),result=read(resultFile);
  const prior=state.calls.slice(BASELINE,RESUME_BASELINE);
  if(prior.length!==4||!same(prior.map(c=>c.name),['mc2_overview','mc2_motion','mc2_motion_repair','mc2_intent'])||
      prior.some(c=>c.status!=='received')||prior[2].repair_of!==prior[1].id||
      [prior[0],prior[1],prior[3]].some(c=>c.repair_of)||receipt.policy!==POLICY||
      path.resolve(receipt.result_path)!==resultFile||receipt.model_status!=='stopped'||result.status!=='stopped'||
      result.error!=='microclip_v2:frame_id_conflict'||result.model_quality_gate_passed!==false||
      result.final_video!==null||result.final_sha256!==null||result.measured_duration_s!==null||
      result.observed_source_frames!==10)fail('resume_cpu_catalog_stop_only');
  const overview=parsed(root,prior[0]),motion=parsed(root,prior[2]),intent=parsed(root,prior[3]),failedFolder=path.join(root,'calls',prior[1].id);
  if(!fs.existsSync(path.join(failedFolder,'protocol_failure.json'))||fs.existsSync(path.join(failedFolder,'parsed.json')))fail('resume_preserves_known_motion_failure');
  const original=read(read(state.artifacts.mc2_input_mc2_overview[0].path).lineage_path),catalog=new Map(original.frames.map(f=>[f.frame_id,f]));
  if(!Array.isArray(overview.frames)||!same(overview.frames.map(f=>f.frame_id).sort(),[...catalog.keys()].sort())||
      !['complete','limited'].includes(motion.observation_status)||!Array.isArray(motion.events)||!motion.events.length||
      motion.events.some(e=>!(0<=e.start_s&&e.start_s<e.end_s&&e.end_s<=p.slot.end_s-p.slot.start_s+.05))||
      !Array.isArray(intent.obligations)||!intent.obligations.length||!Array.isArray(intent.search_regions)||!intent.search_regions.length)fail('resume_known_parsed_predecessors_required');
  const originals=Object.fromEntries(['intended_takeaway','entry_state','exit_state','link_to_previous','link_to_next']
    .filter(key=>typeof p.slot[key]==='string'&&p.slot[key].trim()).map(key=>[key,p.slot[key]]));
  if(!same(intent.original_claim_checks.map(c=>c.claim_id).sort(),Object.keys(originals).sort())||
      intent.original_claim_checks.some(c=>c.original_text!==originals[c.claim_id]))fail('resume_original_intent_binding');
  const regionRows=state.artifacts.mc2_input_mc2_region_0;
  if(regionRows?.length!==1)fail('resume_prepared_region_required');
  const region=read(read(regionRows[0].path).lineage_path),repeated=region.frames.filter(f=>catalog.has(f.frame_id));
  if(!repeated.length||!repeated.some(f=>f.requested_time_s!==catalog.get(f.frame_id).requested_time_s)||
      repeated.some(f=>!same(stableFrame(f),stableFrame(catalog.get(f.frame_id))))||
      new Set([...catalog.keys(),...region.frames.map(f=>f.frame_id)]).size!==10)fail('resume_selector_metadata_conflict_only');
  if(r.policy!==RESUME_POLICY||r.task_id!==state.task_id||r.original_authorization_sha256!==jsonHash(state.artifacts[POLICY][0].path)||
      r.baseline_request_count!==RESUME_BASELINE||state.calls.length<RESUME_BASELINE||
      r.prefix_calls_sha256!==jsonHash(path.join(root,'library_state.json'),v=>v.calls.slice(0,RESUME_BASELINE))||
      path.resolve(r.result_path)!==resultFile||path.resolve(r.receipt_path)!==receiptFile||r.known_cpu_error!==result.error||
      r.next_stage!=='mc2_region_0'||r.new_renders!==0||r.reuse_original_unused_render!==true||
      r.baseline_artifacts.mc2_render_claim||r.baseline_artifacts.mc2_recovered_result||
      !same(Object.keys(r.baseline_call_files).sort(),prior.map(c=>c.id).sort())||
      typeof r.user_instruction!=='string'||!r.user_instruction.trim()||fileHash(resultFile)!==r.result_sha256||
      fileHash(receiptFile)!==r.receipt_sha256)fail('infrastructure_resume_binding_changed');
  for(const key of Object.keys(r.baseline_artifacts))if(jsonHash(path.join(root,'library_state.json'),v=>v.artifacts[key])!==jsonHash(rows[0].path,v=>v.baseline_artifacts[key]))fail('resume_historical_artifact_changed');
  for(const [id,names] of Object.entries(r.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('resume_historical_call_files_changed');
  for(const row of r.protected_files)if(fileHash(row.path)!==row.sha256)fail('resume_historical_bytes_changed');
  for(const row of r.journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('resume_journal_prefix_changed');
  return r;
}

export function loadMicroclipV2(root,env=process.env) {
  const file=env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE,sha=env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_SHA256;
  if(!file&&!sha)return null;
  if(!file||!sha||fileHash(file)!==sha)fail('authorization_modified');
  const boundary=loadBoundaryMicroclip(root,env);if(boundary)return boundary;
  const stateFile=path.join(root,'library_state.json'),state=read(stateFile),p=read(file),rows=state.artifacts?.[POLICY];
  if(rows?.length!==1||path.resolve(rows[0].path)!==path.resolve(file)||rows[0].sha256!==jsonHash(file)||
      p.policy!==POLICY||p.task_id!==state.task_id||path.resolve(p.original_output)!==path.resolve(root)||
      p.input_lock_sha256!==jsonHash(stateFile,v=>v.input_lock)||p.base_request_limit!==state.max_requests||
      p.baseline_policy_version!==state.policy_version||p.baseline_request_count!==BASELINE||
      state.calls.length<BASELINE||state.calls.length!==state.request_count||
      p.prefix_calls_sha256!==jsonHash(stateFile,v=>v.calls.slice(0,BASELINE))||p.stage_pattern!==STAGES.source||
      p.max_concurrency!==1||p.new_renders!==1||p.repairs_per_stage!==1||
      p.numeric_total_request_limit!==null||p.automatic_round_loops!==false||p.goal_resumed!==false||
      p.max_regions!==6||p.max_events!==6||p.max_blind_pages!==10||
      p.reference_and_library_new_inputs_forbidden!==true||p.original_slot_intent_is_fallible_model_navigation!==true||
      fileHash(p.knowledge_path)!==p.knowledge_sha256||
      fileHash(p.original_authorization_path)!==p.original_authorization_sha256||
      fileHash(p.original_result_path)!==p.original_result_sha256||
      path.resolve(p.execution_directory)!==path.resolve(root,'artifacts',POLICY)||
      path.resolve(p.allowed_render_directory)!==path.resolve(p.execution_directory,'render')||
      fileHash(p.parent.path)!==p.parent.sha256||!read(p.outline_path).slots.some(s=>same(s,p.slot)))fail('policy_or_prefix_changed');
  if(!same(fs.readdirSync(root,{withFileTypes:true}).filter(d=>d.isDirectory()&&d.name.startsWith('render_')).map(d=>d.name).sort(),p.baseline_render_directories))fail('unapproved_legacy_render');
  for(const key of Object.keys(p.baseline_artifacts))if(jsonHash(stateFile,v=>v.artifacts[key])!==jsonHash(file,v=>v.baseline_artifacts[key]))fail('historical_artifact_changed');
  for(const [id,names] of Object.entries(p.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('historical_call_files_changed');
  for(const row of p.protected_files)if(fileHash(row.path)!==row.sha256)fail('historical_bytes_changed');
  for(const row of p.journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('journal_prefix_changed');
  for(const lost of p.unknown_inputs)if(['response.json','parsed.json'].some(name=>fs.existsSync(path.join(root,'calls',lost.call_id,name))))fail('unknown_reply_fabricated');
  const networkResume=loadKnownHttp500Resume(root,state,p);
  const dispatchResume=loadKnownHttp500DispatchResume(root,state,p,networkResume);
  const names=new Map();let pending=0;
  for(const c of state.calls.slice(BASELINE)) {
    const m=stage(c.name),reqFile=path.join(root,'calls',c.id,'request.json');
    if(m?.[1]==='boundary')fail('boundary_permission_required');
    if((c.name===RETRY_STAGE||c.name===RETRY_STAGE+'_repair')&&!networkResume)fail('known_failure_resume_required');
    if((c.name===DISPATCH_STAGE||c.name===DISPATCH_STAGE+'_repair')&&!dispatchResume)fail('known_failure_dispatch_resume_required');
    if(!m||names.has(c.name)||!['received','submitted','uncertain','failed_known'].includes(c.status))fail('new_stage_or_status_invalid');
    const req=read(reqFile);if(jsonHash(reqFile)!==c.request_sha256)fail('new_request_changed');
    if(m[2]) {
      const old=names.get(c.name.slice(0,-7));
      if(!old||old.status!=='received'||c.repair_of!==old.id||[...names.keys()].at(-1)!==old.name)fail('sole_repair_binding');
      const orig=read(path.join(root,'calls',old.id,'request.json'));
      if(orig.media_sha256!==req.media_sha256||scope(orig.observation_scope)!==scope(req.observation_scope))fail('repair_input_changed');
    } else if(c.repair_of)fail('original_has_repair');
    if(c.status==='received'&&jsonHash(path.join(root,'calls',c.id,'response.json'))!==c.response_sha256)fail('new_response_changed');
    pending+=c.status==='submitted';names.set(c.name,c);
  }
  if(pending>1)fail('single_lane_required');
  for(const [key,entries] of Object.entries(state.artifacts))if(key.startsWith('mc2_')) {
    if(entries.length!==1||jsonHash(entries[0].path)!==entries[0].sha256)fail('new_artifact_changed');
    const value=read(entries[0].path);
    if(key.startsWith('mc2_input_')&&(fileHash(value.media_path)!==value.media_sha256||fileHash(value.lineage_path)!==value.lineage_sha256))fail('input_bytes_changed');
    for(const proof of value.completed_files||[])if(fileHash(proof.path)!==proof.sha256)fail('completed_bytes_changed');
  }
  if(fs.existsSync(p.allowed_render_directory)&&!state.artifacts.mc2_render_claim)fail('render_without_claim');
  const resume=infrastructureResume(root,state,p);
  return {policy:p,state,file,resume,networkResume,dispatchResume};
}

export function microclipV2Configuration(root,env=process.env){return loadMicroclipV2(root,env)?.policy??null;}

function modelRegions(root,added) {
  const intent=received(root,added,'mc2_intent'),rows=intent.search_regions;
  if(intent.status!=='ready'||intent.blocking_questions?.length||
      !Array.isArray(intent.original_claim_checks)||intent.original_claim_checks.some(c=>
        ['intended_takeaway','entry_state','exit_state'].includes(c.claim_id)&&c.verdict==='unsupported')||
      !Array.isArray(rows)||rows.length<1||rows.length>6||rows.some((r,i)=>r.region_id!=='r'+i))fail('ready_model_regions_required');
  return rows;
}
function modelEvents(root,added) {
  const value=received(root,added,'mc2_anchors'),events=value.events;
  if(value.status!=='ready'||value.blocking_questions?.length||!Array.isArray(events)||
      events.length<1||events.length>6||events.some((e,i)=>e.event_id!=='e'+i))fail('ready_model_events_required');
  return events;
}
function sourceGate(root,added) {
  const value=received(root,added,'mc2_source_check'),intent=received(root,added,'mc2_intent');
  const wanted=intent.obligations.map(o=>o.obligation_id),checks=value.obligation_checks;
  if(value.status!=='ready'||value.blocking_questions?.length||!Array.isArray(checks)||checks.length!==wanted.length||
      !same(checks.map(c=>c.obligation_id).sort(),wanted.sort())||
      checks.some(c=>!['supported','partial'].includes(c.verdict)))fail('source_semantics_blocked');
}
function blindPages(state,p) {
  const rows=state.artifacts.mc2_blind_page_plan;
  if(rows?.length!==1)fail('actual_output_page_plan_required');
  const value=read(rows[0].path),actual=read(path.join(p.allowed_render_directory,'render_result.json')),pages=value.pages;
  if(fileHash(value.coverage_path)!==value.coverage_sha256)fail('output_coverage_bytes_changed');
  const coverage=read(value.coverage_path);
  if(value.policy!==POLICY||value.final_sha256!==actual.sha256||!Array.isArray(pages)||pages.length<1||pages.length>10||
      coverage.schema!=='microclip_dense_coverage_v1'||coverage.source_sha256!==actual.sha256||coverage.start_s!==0||
      Math.abs(coverage.end_s-actual.measured_duration_s)>1e-6||coverage.page_count!==pages.length||
      coverage.page_bindings?.length!==pages.length)fail('actual_output_page_plan_changed');
  let end=0;const frames=[];
  for(let i=0;i<pages.length;i++) {
    const row=pages[i],binding=coverage.page_bindings[i],manifest=read(binding.manifest_path);
    if(row.stage!=='mc2_blind_page_'+i||row.start_s<end-1e-6||
        !(row.start_s<row.end_s&&row.end_s<=actual.measured_duration_s+1e-6)||
        manifest.request.start_s!==row.start_s||manifest.request.end_s!==row.end_s||
        manifest.request_sha256!==binding.request_sha256||manifest.grid.sha256!==binding.grid_sha256||
        !same(manifest.frames.map(f=>f.frame_id),binding.frame_ids))fail('output_page_binding_changed');
    frames.push(...manifest.frames);
    end=row.end_s;
  }
  if(frames[0].frame_id!==coverage.first_frame_id||frames.at(-1).frame_id!==coverage.last_frame_id||
      frames[0].source_time_s>1e-6||Math.abs(frames.at(-1).frame_end_s-actual.measured_duration_s)>1e-6)fail('output_frame_endpoints_incomplete');
  return pages;
}
function plannedSlices(root,state,p,added) {
  const plan=received(root,added,'mc2_plan'),frames=shownFrames(state),boundaries=new Set(),ordered=[];
  for(let i=0;i<2*modelEvents(root,added).length;i++) {
    const edge=received(root,added,'mc2_edge_'+i),effective=state.artifacts.mc2_boundary_navigation_resume?effectiveBoundary(root,state,i):null,
      d=effective?.descriptor??read(state.artifacts['mc2_input_mc2_edge_'+i][0].path);
    if(edge.status!=='confirmed'||edge.blocking_questions?.length||
        !read(d.lineage_path).frames.some(f=>f.frame_id===edge.confirmed_frame_id))fail('unconfirmed_boundary');
    boundaries.add(edge.confirmed_frame_id);
    ordered.push(frames.get(edge.confirmed_frame_id));
  }
  if(state.artifacts.mc2_boundary_navigation_resume)for(let i=0;i<ordered.length;i+=2) {
    if(!ordered[i]||!ordered[i+1]||ordered[i].source_time_s>ordered[i+1].source_time_s||
        i&&ordered[i-1].frame_end_s>ordered[i].source_time_s+1e-6)fail('event_boundary_order_invalid');
  }
  if(!Array.isArray(plan.shots)||plan.shots.length<1||plan.shots.length>12)fail('model_shots_required');
  let previous=p.slot.start_s;
  return plan.shots.map(shot=> {
    const a=frames.get(shot.start_frame_id),b=frames.get(shot.end_frame_id);
    if(!a||!b||!boundaries.has(a.frame_id)||!boundaries.has(b.frame_id)||
        a.source_sha256!==p.parent.sha256||b.source_sha256!==p.parent.sha256||
        a.source_time_s<previous-1e-6||
        !(p.slot.start_s<=a.source_time_s&&a.source_time_s<=b.source_time_s&&
          b.source_time_s<b.frame_end_s&&b.frame_end_s<=p.slot.end_s+1e-6))fail('exact_model_slice_range');
    previous=b.frame_end_s;
    return {source_in_s:a.source_time_s,source_out_s:b.frame_end_s};
  });
}
function predecessor(root,state,p,added,kind) {
  if(kind==='overview'&&added.length!==1)fail('first_overview_required');
  if(kind==='motion')received(root,added,'mc2_overview');
  if(kind==='intent')received(root,added,'mc2_motion');
  if(kind.startsWith('region_')) {
    const index=Number(kind.split('_').at(-1));
    if(index>=modelRegions(root,added).length||added.slice(0,-1).some(c=>
        ['anchors','plan','source_check','blind_video','review'].includes(stage(c.name)[1])))fail('region_not_model_requested');
    if(index)received(root,added,'mc2_region_'+(index-1));
  }
  if(kind==='anchors')for(let i=0;i<modelRegions(root,added).length;i++)received(root,added,'mc2_region_'+i);
  if(kind.startsWith('edge_')) {
    const index=Number(kind.split('_').at(-1));
    if(index>=2*modelEvents(root,added).length)fail('edge_not_model_requested');
    if(index)received(root,added,'mc2_edge_'+(index-1));
  }
  if(kind==='plan')for(let i=0;i<2*modelEvents(root,added).length;i++) {
    const edge=received(root,added,'mc2_edge_'+i);
    if(edge.status!=='confirmed'||edge.blocking_questions?.length)fail('unconfirmed_boundary');
  }
  if(kind.startsWith('slice_')) {
    const index=Number(kind.split('_').at(-1));
    if(index>=plannedSlices(root,state,p,added).length)fail('slice_not_model_selected');
    if(index)received(root,added,'mc2_slice_'+(index-1));
  }
  if(kind==='source_check')for(let i=0;i<plannedSlices(root,state,p,added).length;i++)received(root,added,'mc2_slice_'+i);
  if(kind==='blind_video') {sourceGate(root,added);if(!state.artifacts.mc2_render_claim)fail('render_required');}
  if(kind.startsWith('blind_page_')) {
    received(root,added,'mc2_blind_video');
    const index=Number(kind.split('_').at(-1));
    if(index>=blindPages(state,p).length)fail('blind_page_not_requested');
    if(index)received(root,added,'mc2_blind_page_'+(index-1));
  }
  if(kind==='review') {
    received(root,added,'mc2_blind_video');
    for(let i=0;i<blindPages(state,p).length;i++)received(root,added,'mc2_blind_page_'+i);
  }
}
function shownFrames(state) {
  const result=new Map();
  for(const [key,entries] of Object.entries(state.artifacts))if(key.startsWith('mc2_input_')&&!key.startsWith('mc2_input_mc2_blind_page_')) {
    const d=read(entries[0].path);
    if(d.tool==='analyze_image')for(const f of read(d.lineage_path).frames) {
      if(result.has(f.frame_id)&&!same(stableFrame(result.get(f.frame_id)),stableFrame(f)))fail('frame_id_conflict');
      result.set(f.frame_id,f);
    }
  }
  return result;
}
export function microclipV2RequestLimit(root,job,env=process.env) {
  const value=loadMicroclipV2(root,env);if(!value)return null;
  const {policy:p,state,resume,networkResume,dispatchResume,boundaryResume,boundaryMetadataResume}=value,added=state.calls.slice(BASELINE),call=added.find(c=>c.id===job.job_id),m=call&&stage(call.name);
  if(!call||call.status!=='submitted'||!m||added.at(-1)!==call||added.slice(0,-1).some(c=>!settled(c,networkResume,dispatchResume))||
      state.artifacts.mc2_result&&!resume||state.artifacts.mc2_recovered_result&&!networkResume||
      state.artifacts.mc2_network_result&&!dispatchResume||state.artifacts.mc2_dispatch_result&&!boundaryResume||
      state.artifacts.mc2_boundary_result&&!boundaryMetadataResume||state.artifacts.mc2_boundary_metadata_result)fail('submitted_new_call_required');
  if(resume&&state.calls.length===RESUME_BASELINE+1&&call.name!==resume.next_stage)fail('resume_next_stage_required');
  const firstKnownRetry=networkResume&&state.calls.length===networkResume.baseline_request_count+1;
  if(firstKnownRetry&&call.name!==networkResume.retry_stage)fail('known_failure_next_stage_required');
  const firstDispatch=dispatchResume&&state.calls.length===dispatchResume.baseline_request_count+1;
  if(firstDispatch&&call.name!==dispatchResume.retry_stage)fail('known_failure_dispatch_next_stage_required');
  if(boundaryResume&&state.calls.length===boundaryResume.baseline_request_count+1&&call.name!==boundaryResume.first_stage)fail('boundary_next_stage_required');
  if(boundaryMetadataResume&&state.calls.length===boundaryMetadataResume.baseline_request_count+1&&call.name!==boundaryMetadataResume.next_stage)fail('boundary_metadata_next_stage_required');
  const kind=m[1],stem=call.name.replace(/_repair$/,'');
  if(m[2]) {
    const old=added.at(-2),folder=old&&path.join(root,'calls',old.id);
    if(!old||old.name!==stem||old.status!=='received'||call.repair_of!==old.id||old.repair_of||
        added.filter(c=>c.repair_of===old.id).length!==1||!fs.existsSync(path.join(folder,'protocol_failure.json'))||
        fs.existsSync(path.join(folder,'parsed.json')))fail('sole_known_format_repair_required');
  } else {
    if(call.repair_of)fail('original_has_repair');
    if(added.length>1&&!(firstKnownRetry||firstDispatch))metadataParsed(root,state,added.at(-2));
    if(kind==='boundary') {if(!boundaryResume)fail('boundary_permission_required');boundaryPredecessor(root,state,p,call);}
    else predecessor(root,state,p,added,kind);
  }
  const req=read(path.join(root,'calls',call.id,'request.json')),queued=read(path.join(root,'mcp_queue',call.id+'.request.json'));
  const image=['overview','intent','anchors','boundary'].includes(kind)||['region_','edge_','blind_page_'].some(k=>kind.startsWith(k));
  const media=path.resolve(req.arguments[image?'image_source':'video_source']),entries=state.artifacts['mc2_input_'+stem];
  if(entries?.length!==1)fail('input_descriptor_required');
  const d=read(entries[0].path),fingerprint=scope(req.observation_scope),s=req.observation_scope;
  if(req.provider!=='official_vision_mcp_in_codex'||req.tool!==(image?'analyze_image':'analyze_video')||
      d.policy!==POLICY||d.stage!==stem||d.tool!==req.tool||path.resolve(d.media_path)!==media||
      fileHash(media)!==d.media_sha256||d.media_sha256!==req.media_sha256||
      fileHash(d.lineage_path)!==d.lineage_sha256||scope(d.observation_scope)!==fingerprint||
      queued.job_id!==job.job_id||queued.tool!==req.tool||!same(queued.arguments,req.arguments)||
      job.tool!==undefined&&job.tool!==req.tool||job.arguments!==undefined&&!same(job.arguments,req.arguments))fail('actual_job_binding');
  for(const lost of p.unknown_inputs)if(call.request_sha256===lost.request_sha256||req.media_sha256===lost.media_sha256||fingerprint===scope(lost.scope))fail('unknown_input_replay');
  const lineage=read(d.lineage_path),outputStage=['blind_video','review'].includes(kind)||kind.startsWith('blind_page_');
  let sourcePath,sourceSha,start,end;
  if(outputStage) {
    const final=path.resolve(p.allowed_render_directory,'final.mp4'),actual=read(path.join(p.allowed_render_directory,'render_result.json'));
    if(!state.artifacts.mc2_render_claim||actual.sha256!==fileHash(final))fail('actual_output_only');
    sourcePath=final;sourceSha=actual.sha256;start=0;end=actual.measured_duration_s;
  } else {sourcePath=path.resolve(p.parent.path);sourceSha=p.parent.sha256;start=p.slot.start_s;end=p.slot.end_s;}
  if(s.source_sha256!==sourceSha||s.source_start_s<start||s.source_end_s>end+1e-6)fail('authorized_source_range_only');
  if(image) {
    const mf=path.resolve(d.lineage_path),r=lineage.request,src=lineage.source,g=lineage.grid;
    if(s.kind!=='sparse_contact_sheet'||lineage.schema!=='microclip_grid_v1'||path.resolve(lineage.manifest_path)!==mf||
        fs.readFileSync(path.join(path.dirname(mf),'manifest.sha256'),'ascii').trim()!==d.lineage_sha256||
        src.sha256!==sourceSha||r.source_sha256!==src.sha256||path.resolve(src.path)!==sourcePath||
        path.resolve(r.source_path)!==sourcePath||s.source_start_s!==r.start_s||s.source_end_s!==r.end_s||
        path.resolve(g.path)!==media||g.sha256!==req.media_sha256||g.crop_box!==null||g.resize_only!==true||
        !Array.isArray(lineage.frames)||lineage.frames.length<1||lineage.frames.length>6)fail('actual_grid_lineage_required');
    let last=-Infinity;const ids=new Set();
    for(const f of lineage.frames) {
      if(f.source_sha256!==sourceSha||f.source_time_s<s.source_start_s||f.source_time_s>=s.source_end_s||
          f.source_time_s<=last||ids.has(f.frame_id)||fileHash(f.png_path)!==f.png_sha256||
          Math.abs(f.pts_time_s-f.pts*f.time_base[0]/f.time_base[1])>1e-9||
          Math.abs(f.source_time_s-(f.pts_time_s-src.timeline_origin_s))>1e-9)fail('grid_frame_binding');
      last=f.source_time_s;ids.add(f.frame_id);
    }
    if(kind==='boundary')boundaryInputBinding(root,state,p,call,d,lineage);
    else if(kind.startsWith('region_')) {
      const region=modelRegions(root,added)[Number(kind.split('_').at(-1))],frames=shownFrames(state),a=frames.get(region.start_frame_id),b=frames.get(region.end_frame_id);
      if(!a||!b||d.region_id!==region.region_id||s.source_start_s!==a.source_time_s||s.source_end_s!==b.frame_end_s)fail('model_region_binding_required');
    } else if(kind.startsWith('edge_')) {
      const index=Number(kind.split('_').at(-1)),event=modelEvents(root,added)[Math.floor(index/2)],key=['start','end'][index%2],id=event[key+'_frame_id'],anchor=shownFrames(state).get(id);
      if(!anchor||d.anchor_frame_id!==id||d.anchor_key!==key||d.event_id!==event.event_id||!ids.has(id)||
          s.source_start_s<Math.max(start,anchor.source_time_s-.25)||s.source_start_s>anchor.source_time_s||
          s.source_end_s<=anchor.source_time_s||s.source_end_s>Math.min(end,anchor.source_time_s+.25)+1e-6)fail('anchor_must_be_shown_with_neighbors');
    } else if(kind.startsWith('blind_page_')) {
      const row=blindPages(state,p)[Number(kind.split('_').at(-1))];
      if(s.source_start_s!==row.start_s||s.source_end_s!==row.end_s)fail('actual_page_scope_changed');
    } else if(s.source_start_s!==start||s.source_end_s!==end)fail('whole_slot_overview_required');
  } else {
    if(kind.startsWith('slice_')) {
      const index=Number(kind.split('_').at(-1)),range=plannedSlices(root,state,p,added)[index];
      if(d.slice_index!==index||!same(d.source_range,range)||s.source_start_s!==range.source_in_s||s.source_end_s!==range.source_out_s)fail('exact_model_slice_required');
    } else if(s.source_start_s!==start||s.source_end_s!==end)fail('full_continuous_video_required');
    if(s.kind!=='continuous_window'||scope(lineage)!==fingerprint||lineage.sha256!==req.media_sha256||
        path.resolve(lineage.path)!==media||path.resolve(lineage.source_path)!==sourcePath||lineage.audio_present!==false)fail('silent_normal_speed_video_lineage_required');
  }
  return Infinity;
}
export function microclipV2FrozenSlotJob(root,env=process.env) {
  const value=loadMicroclipV2(root,env);if(!value)return null;
  const paths=new Map(value.policy.unknown_inputs.map(row=>[path.resolve(root,'mcp_queue',row.call_id+'.request.json'),row.call_id]));
  return file=>{const id=paths.get(path.resolve(file));if(!id)return false;
    const current=loadMicroclipV2(root,env),old=current.state.calls.slice(0,BASELINE).find(c=>c.id===id),job=read(file),req=read(path.join(root,'calls',id,'request.json'));
    if(old?.status!=='uncertain'||job.job_id!==id||job.tool!==req.tool||!same(job.arguments,req.arguments))fail('frozen_queue_binding');return true;};
}
