// Direct parent-timeline policy; old reconstruction stages remain read-only.
import fs from 'node:fs';
import path from 'node:path';
import {jsonHash, fileHash, prefixHash, scope, canonical, parsed, received} from './mcp_forward_slot_guard.mjs';
const POLICY = 'sf_parent_timeline_finecut_v1', BASELINE = 194;
const STAGES = /^pc_(0|3)_(slot_[a-f0-9]{16}|blind|review)(_repair)?$/;
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const fail = reason => { throw new Error('library_mcp_parent_cut_' + reason); };
const same = (a,b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function files(folder) {
  const result = [];
  function visit(dir) { for (const e of fs.readdirSync(dir, {withFileTypes:true})) {
    const file = path.join(dir, e.name);
    if (e.isDirectory()) visit(file); else if (e.isFile()) result.push(path.relative(folder,file));
  } }
  visit(folder); return result.sort();
}
export function loadParentCut(root, env = process.env) {
  const file = env.OMNI_LIBRARY_PARENT_CUT_AUTH_FILE, sha = env.OMNI_LIBRARY_PARENT_CUT_AUTH_SHA256;
  if (!file && !sha) return null;
  if (!file || !sha || fileHash(file) !== sha) fail('authorization_modified');
  const stateFile = path.join(root,'library_state.json'), state = read(stateFile), p = read(file), rows = state.artifacts?.[POLICY];
  if (rows?.length !== 1 || path.resolve(rows[0].path) !== path.resolve(file) || rows[0].sha256 !== jsonHash(file) ||
      p.policy !== POLICY || p.task_id !== state.task_id || path.resolve(p.original_output) !== path.resolve(root) ||
      p.input_lock_sha256 !== jsonHash(stateFile, v=>v.input_lock) || p.base_request_limit !== state.max_requests ||
      p.baseline_policy_version !== state.policy_version || p.baseline_request_count !== BASELINE ||
      state.calls.length < BASELINE || state.calls.length !== state.request_count ||
      p.prefix_calls_sha256 !== jsonHash(stateFile,v=>v.calls.slice(0,BASELINE)) || p.stage_pattern !== STAGES.source ||
      !same(p.parent_rounds,[0,3]) || p.max_concurrent_parents !== 2 || p.renders_per_parent !== 1 || p.repairs_per_stage !== 1 ||
      p.numeric_total_request_limit !== null || p.automatic_round_loops !== false || p.goal_resumed !== false ||
      fileHash(p.knowledge_path)!==p.knowledge_sha256 ||
      fileHash(p.original_authorization_path) !== p.original_authorization_sha256) fail('policy_or_prefix_changed');
  const grant = read(p.original_authorization_path);
  if (grant.renders_per_parent !== 1 || path.resolve(p.execution_directory) !== path.resolve(grant.execution_directory,'parent_timeline') ||
      !same(p.allowed_render_directories,[0,3].map(i=>path.join(p.execution_directory,`render_${i}`,'render'))) ||
      grant.allowed_render_directories.some(d=>fs.existsSync(d))) fail('original_grant_changed_or_used');
  if(!same(fs.readdirSync(root,{withFileTypes:true}).filter(d=>d.isDirectory()&&d.name.startsWith('render_')).map(d=>d.name).sort(),p.baseline_render_directories)) fail('unapproved_legacy_render');
  for (const [key,old] of Object.entries(p.baseline_artifacts)) if (
      jsonHash(stateFile,v=>v.artifacts[key]) !== jsonHash(file,v=>v.baseline_artifacts[key])) fail('historical_artifact_changed');
  for (const [id,names] of Object.entries(p.baseline_call_files)) if (!same(files(path.join(root,'calls',id)),names)) fail('historical_call_files_changed');
  for (const row of p.protected_files) if (fileHash(row.path) !== row.sha256) fail('historical_bytes_changed');
  for (const row of p.journal_prefixes) if (prefixHash(row.path,row.bytes) !== row.sha256) fail('journal_prefix_changed');
  for (const lost of p.unknown_inputs) if (fs.existsSync(path.join(root,'calls',lost.call_id,'response.json')) ||
      fs.existsSync(path.join(root,'calls',lost.call_id,'parsed.json'))) fail('unknown_reply_fabricated');
  const names = new Map(), last = new Map(), pending=[];
  for (const c of state.calls.slice(BASELINE)) {
    const m=STAGES.exec(c.name); if (!m || names.has(c.name) || !['received','submitted','uncertain','failed_known'].includes(c.status)) fail('new_stage_or_status_invalid');
    const parent=Number(m[1]), reqFile=path.join(root,'calls',c.id,'request.json'), req=read(reqFile);
    if (jsonHash(reqFile)!==c.request_sha256) fail('new_request_changed');
    if (m[3]) {
      const old=names.get(c.name.slice(0,-7));
      if (!old || old.status!=='received' || c.repair_of!==old.id || last.get(parent)!==old.name) fail('sole_repair_binding');
      const original=read(path.join(root,'calls',old.id,'request.json'));
      if (req.media_sha256!==original.media_sha256 || scope(req.observation_scope)!==scope(original.observation_scope)) fail('repair_input_changed');
    } else if(c.repair_of) fail('original_has_repair');
    if(c.status==='received' && jsonHash(path.join(root,'calls',c.id,'response.json'))!==c.response_sha256) fail('new_response_changed');
    if(c.status==='submitted') pending.push(parent);
    names.set(c.name,c); last.set(parent,c.name);
  }
  if(pending.length>2 || new Set(pending).size!==pending.length) fail('same_parent_pending');
  for(const [key,entries] of Object.entries(state.artifacts)) if(key.startsWith('pc_')) {
    if(entries.length!==1 || jsonHash(entries[0].path)!==entries[0].sha256) fail('new_artifact_changed');
    for(const proof of read(entries[0].path).completed_files || []) if(fileHash(proof.path)!==proof.sha256) fail('completed_bytes_changed');
  }
  for(const parent of [0,3]) if(fs.existsSync(p.allowed_render_directories[[0,3].indexOf(parent)]) && !state.artifacts[`pc_${parent}_render_claim`]) fail('render_without_claim');
  return {policy:p,state,file};
}
export function parentCutParallelConfiguration(root,env=process.env) { return loadParentCut(root,env)?.policy ?? null; }
export function parentCutRequestLimit(root,job,env=process.env) {
  const value=loadParentCut(root,env); if(!value) return null;
  const {policy:p,state}=value, added=state.calls.slice(BASELINE), call=added.find(c=>c.id===job.job_id), m=call && STAGES.exec(call.name);
  if(!call || call.status!=='submitted' || !m) fail('submitted_parent_call_required');
  const parent=Number(m[1]), kind=m[2], own=added.filter(c=>Number(STAGES.exec(c.name)[1])===parent);
  if(own.at(-1)!==call || added.some(c=>!['received','submitted'].includes(c.status)) || state.artifacts[`pc_result_${parent}`]) fail('unknown_pending_or_finished');
  if(m[3]) {const old=own.at(-2);if(!old || old.name!==call.name.slice(0,-7) || old.status!=='received' || old.id!==call.repair_of || old.repair_of || added.filter(c=>c.repair_of===old.id).length!==1) fail('sole_repair_required');}
  else {
    if(call.repair_of) fail('original_has_repair');
    if(own.length>1) parsed(root,own.at(-2));
    if(kind.startsWith('slot_') && own.some(c=>/_(blind|review)(?:_repair)?$/.test(c.name))) fail('slot_after_render_review');
    if(kind==='review') received(root,own,`pc_${parent}_blind`);
  }
  const req=read(path.join(root,'calls',call.id,'request.json')), queued=read(path.join(root,'mcp_queue',call.id+'.request.json'));
  if(req.provider!=='official_vision_mcp_in_codex' || req.tool!=='analyze_video' || fileHash(req.arguments.video_source)!==req.media_sha256 ||
      queued.job_id!==job.job_id || queued.tool!==req.tool || !same(queued.arguments,req.arguments) ||
      (job.tool!==undefined && job.tool!==req.tool) || (job.arguments!==undefined && !same(job.arguments,req.arguments))) fail('actual_job_binding');
  const fingerprint=scope(req.observation_scope), media=path.resolve(req.arguments.video_source);
  for(const lost of p.unknown_inputs) if(call.request_sha256===lost.request_sha256 || req.media_sha256===lost.media_sha256 || fingerprint===scope(lost.scope)) fail('unknown_input_replay');
  if(kind.startsWith('slot_')) {
    const src=p.parents.find(s=>s.round===parent), s=req.observation_scope;
    const outlineFile=p.outline_paths[String(parent)], outlines=read(outlineFile).slots;
    const index=outlines.findIndex((_,i)=>kind==='slot_'+jsonHash(outlineFile,v=>v.slots[i]).slice(0,16)), slot=outlines[index];
    if(!slot || !['continuous_window','complete_file'].includes(s.kind) || s.source_sha256!==src.sha256 || s.source_start_s!==slot.start_s ||
        s.source_end_s!==slot.end_s || s.source_start_s<0 || s.source_end_s>src.duration_s) fail('parent_timeline_only');
    if(media===path.resolve(src.path)) {if(s.source_start_s!==0 || s.source_end_s!==src.duration_s) fail('full_parent_scope_required');}
    else {const lineage=read(path.join(path.dirname(media),'lineage.json'));if(scope(lineage)!==fingerprint || lineage.sha256!==req.media_sha256 || path.resolve(lineage.path)!==media || path.resolve(lineage.source_path)!==path.resolve(src.path)) fail('actual_crop_lineage_required');}
  } else {
    const final=path.resolve(p.execution_directory,`render_${parent}`,'render','final.mp4'), actual=read(path.join(path.dirname(final),'render_result.json')), s=req.observation_scope;
    if(!state.artifacts[`pc_${parent}_render_claim`] || !['complete_file','continuous_window'].includes(s.kind) ||
        s.source_sha256!==actual.sha256 || actual.sha256!==fileHash(final) || s.source_start_s!==0 || s.source_end_s!==actual.measured_duration_s) fail('review_actual_output_only');
    if(media!==final){const lineage=read(path.join(path.dirname(media),'lineage.json'));if(scope(lineage)!==fingerprint || lineage.sha256!==req.media_sha256 || path.resolve(lineage.path)!==media || path.resolve(lineage.source_path)!==final) fail('actual_output_proxy_lineage_required');}
  }
  return Infinity;
}
export function parentCutFrozenSlotJob(root,env=process.env) {
  const value=loadParentCut(root,env); if(!value) return null;
  const paths=new Map(value.policy.unknown_inputs.map(row=>[path.resolve(root,'mcp_queue',row.call_id+'.request.json'),row.call_id]));
  return file=>{const id=paths.get(path.resolve(file));if(!id)return false;const current=loadParentCut(root,env),old=current.state.calls.slice(0,BASELINE).find(c=>c.id===id),job=read(file),req=read(path.join(root,'calls',id,'request.json'));if(old?.status!=='uncertain' || job.job_id!==id || job.tool!==req.tool || !same(job.arguments,req.arguments)) fail('frozen_queue_binding');return true;};
}
