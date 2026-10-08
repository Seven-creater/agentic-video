// Read-only proof for exactly one explicitly authorized known HTTP 500 retry.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {jsonHash,fileHash,prefixHash,scope,canonical} from './mcp_forward_slot_guard.mjs';

const ARTIFACT='mc2_known_http500_resume',POLICY='microclip_v2_known_http500_retry_v1',BASELINE=224;
const FAILED='glm_224_mc2_region_1',STAGE='mc2_region_1',RETRY='mc2_region_1_retry';
const DISPATCH_ARTIFACT='mc2_known_http500_dispatch_resume',DISPATCH_POLICY='microclip_v2_known_http500_dispatch_resume_v1';
const DISPATCH_FAILED='glm_225_mc2_region_1_retry',DISPATCH='mc2_region_1_retry_dispatch',DISPATCH_BASELINE=225;
const STAGES=/^mc2_(overview|motion|intent|region_[0-5]|anchors|edge_(?:[0-9]|1[01])|plan|slice_(?:[0-9]|1[01])|source_check|blind_video|blind_page_[0-9]|review)(_repair)?$/;
const REPAIR_PREFIX='\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。';
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const fail=reason=>{throw new Error('library_mcp_microclip_known500_'+reason);};
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const journalCache=new Map();
function files(folder){const out=[];function visit(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){
  const f=path.join(dir,e.name);if(e.isDirectory())visit(f);else if(e.isFile())out.push(path.relative(folder,f));
}}visit(folder);return out.sort();}
function events(file,job){
  const s=fs.statSync(file,{bigint:true}),stamp=[s.size,s.mtimeNs,s.ctimeNs,s.ino].join(':');
  const key=file+'|'+job,old=journalCache.get(key);if(old?.stamp===stamp)return old.rows;
  const fd=fs.openSync(file,'r'),buffer=Buffer.alloc(1024*1024),needle=Buffer.from(job);
  let rows=[],carry=Buffer.alloc(0),offset=0,line=0,position=0,digest=crypto.createHash('sha256');
  if(old&&Number(s.size)>=old.size&&old.complete){
    if(prefixHash(file,old.size)!==old.digest.copy().digest('hex'))fail('cached_prefix_changed');
    rows=[...old.rows];offset=position=old.size;line=old.line;digest=old.digest.copy();
  }
  function add(bytes){line++;if(bytes.includes(needle)){const event=JSON.parse(bytes.toString('utf8'));
    if(event.job_id===job)rows.push({event,offset,bytes:bytes.length,line,sha256:hash(bytes)});
  }offset+=bytes.length;}
  try {let n;while((n=fs.readSync(fd,buffer,0,buffer.length,position))){
    position+=n;digest.update(buffer.subarray(0,n));
    const chunk=Buffer.concat([carry,buffer.subarray(0,n)]);let start=0,end;
    while((end=chunk.indexOf(10,start))!==-1){add(chunk.subarray(start,end+1));start=end+1;}
    carry=Buffer.from(chunk.subarray(start));
  }if(carry.length)add(carry);}finally{fs.closeSync(fd);}
  const after=fs.statSync(file,{bigint:true});if([after.size,after.mtimeNs,after.ctimeNs,after.ino].join(':')!==stamp)fail('journal_changed_during_scan');
  journalCache.set(key,{stamp,rows,size:Number(s.size),line,digest:digest.copy(),complete:carry.length===0});return rows;
}

function verifyKnownFailure(root,state,p,r){
  const call=state.calls[BASELINE-1],folder=path.join(root,'calls',FAILED);
  if(state.request_count!==state.calls.length||state.calls.length<BASELINE||call.id!==FAILED||call.name!==STAGE||
      call.status!=='failed_known'||call.repair_of||state.calls.slice(218,BASELINE-1).some(c=>c.status!=='received'))fail('known_224_required');
  if(['response.json','parsed.json','protocol_failure.json'].some(f=>fs.existsSync(path.join(folder,f))))fail('failed_call_has_model_result');
  const failureFile=path.join(folder,'failure.json'),failure=read(failureFile),requestFile=path.join(folder,'request.json'),request=read(requestFile);
  if(failure.uncertain!==false||typeof failure.error!=='string'||!failure.error||jsonHash(requestFile)!==call.request_sha256||
      request.provider!=='official_vision_mcp_in_codex'||request.tool!=='analyze_image'||'known_failure_retry_of'in request||
      !same(Object.keys(request.arguments||{}).sort(),['image_source','prompt'])||typeof request.arguments.prompt!=='string'||
      !request.arguments.prompt||fileHash(request.arguments.image_source)!==request.media_sha256)fail('original_request_or_failure_changed');
  scope(request.observation_scope);
  const rows=state.artifacts['mc2_input_'+STAGE];if(rows?.length!==1)fail('original_descriptor_required');
  const descriptorFile=rows[0].path,d=read(descriptorFile);
  if(jsonHash(descriptorFile)!==rows[0].sha256||d.stage!==STAGE||d.tool!==request.tool||
      path.resolve(d.media_path)!==path.resolve(request.arguments.image_source)||d.media_sha256!==request.media_sha256||
      !same(d.observation_scope,request.observation_scope)||fileHash(d.lineage_path)!==d.lineage_sha256)fail('original_descriptor_changed');
  const queued=read(path.join(root,'mcp_queue',FAILED+'.request.json'));
  if(queued.job_id!==FAILED||queued.tool!==request.tool||!same(queued.arguments,request.arguments))fail('original_queue_request_changed');
  const replyFile=path.join(root,'mcp_queue',FAILED+'.response.json'),reply=read(replyFile),result=reply.result||{};
  if(reply.status!=='error'||result.isError!==true||!Array.isArray(result.content)||!result.content.length||
      result.content.some(c=>c.type!=='text'||typeof c.text!=='string'||!c.text.startsWith('Error:'))||
      ['choices','usage','model'].some(k=>k in reply||k in result))fail('error_only_queue_response_required');
  const journal=path.join(root,'mcp_http.jsonl'),rowsHttp=events(journal,FAILED);
  if(rowsHttp.length!==2||rowsHttp[0].event.type!=='request'||rowsHttp[1].event.type!=='response')fail('exactly_one_http_attempt_required');
  const sent=rowsHttp[0].event,got=rowsHttp[1].event;
  if(sent.seq!==got.seq||!Number.isInteger(sent.seq)||sent.url!=='https://open.bigmodel.cn/api/paas/v4/chat/completions'||got.status!==500)fail('matched_http500_required');
  const body=JSON.parse(got.body);
  if(!same(Object.keys(body),['error'])||!body.error||!same(Object.keys(body.error).sort(),['code','message'])||
      String(body.error.code)!=='1234'||typeof body.error.message!=='string'||
      !body.error.message.startsWith('Internal network failure, error id: '))fail('only_internal_network_failure_supported');
  const native=sent.body,blocks=(native?.messages||[]).filter(m=>Array.isArray(m.content)).flatMap(m=>m.content),
    texts=blocks.filter(c=>c.type==='text').map(c=>c.text),images=blocks.filter(c=>c.type==='image_url').map(c=>c.image_url?.url);
  if(native?.model!=='glm-5.3-flash'||!same(texts,[request.arguments.prompt])||images.length!==1||
      typeof images[0]!=='string'||!images[0].startsWith('data:image/png;base64,'))fail('native_prompt_media_binding_changed');
  const b64=images[0].slice('data:image/png;base64,'.length),bytes=Buffer.from(b64,'base64');
  if(bytes.toString('base64')!==b64||hash(bytes)!==request.media_sha256)fail('native_image_bytes_changed');
  const recovered=state.artifacts.mc2_recovered_result;if(recovered?.length!==1||!state.artifacts.mc2_infrastructure_resume)fail('previous_cpu_resume_and_stop_required');
  const receiptFile=recovered[0].path,receipt=read(receiptFile),resultFile=path.join(p.execution_directory,'result_recovered.json'),stopped=read(resultFile);
  if(jsonHash(receiptFile)!==recovered[0].sha256||receipt.policy!=='microclip_slot_finecut_v2'||
      path.resolve(receipt.result_path)!==path.resolve(resultFile)||receipt.model_status!=='stopped'||stopped.status!=='stopped'||
      stopped.error!=='official_MCP_failure:'+FAILED||stopped.model_quality_gate_passed!==false||
      ['final_video','final_sha256','measured_duration_s'].some(k=>stopped[k]!==null))fail('known_http_stop_only');
  if(r.baseline_artifacts.mc2_render_claim||r.baseline_artifacts.mc2_network_result||fs.existsSync(r.unused_render_sentinel))fail('historical_unused_render_changed');
  const known={bound_request:request,bound_input_descriptor:d,descriptor_path:path.resolve(descriptorFile),descriptor_sha256:fileHash(descriptorFile),
    failed_result_path:path.resolve(resultFile),failed_result_sha256:fileHash(resultFile),failed_receipt_path:path.resolve(receiptFile),failed_receipt_sha256:fileHash(receiptFile),
    failure_path:path.resolve(failureFile),failure_sha256:fileHash(failureFile),queue_response_path:path.resolve(replyFile),queue_response_sha256:fileHash(replyFile),
    original_http:{path:path.resolve(journal),seq:sent.seq,status:500,error_body:body,events:rowsHttp.map(({offset,bytes,line,sha256})=>({offset,bytes,line,sha256}))}};
  for(const [k,v]of Object.entries(known))if(!same(r[k],v))fail('known_failure_proof_changed');
}

export function loadKnownHttp500Resume(root,state,p){
  const rows=state.artifacts?.[ARTIFACT];if(!rows)return null;
  if(rows.length!==1||jsonHash(rows[0].path)!==rows[0].sha256||!path.resolve(rows[0].path).startsWith(path.resolve(root,'artifacts')+path.sep))fail('one_retry_authorization_required');
  const r=read(rows[0].path),authFile=state.artifacts.microclip_slot_finecut_v2?.[0]?.path,stateFile=path.join(root,'library_state.json');
  if(r.policy!==POLICY||r.task_id!==state.task_id||r.baseline_request_count!==BASELINE||
      r.original_authorization_sha256!==fileHash(authFile)||r.failed_call_id!==FAILED||r.failed_stage!==STAGE||r.retry_stage!==RETRY||
      r.max_transport_retries!==1||r.repairs_per_stage!==1||r.new_renders!==0||r.reuse_original_unused_render!==true||r.goal_resumed!==false||
      typeof r.user_instruction!=='string'||!r.user_instruction.trim()||
      path.resolve(r.unused_render_sentinel)!==path.resolve(p.execution_directory,'.known500_original_unused_render')||
      r.prefix_calls_sha256!==jsonHash(stateFile,v=>v.calls.slice(0,BASELINE))||
      !same(Object.keys(r.baseline_call_files).sort(),state.calls.slice(0,BASELINE).map(c=>c.id).sort()))fail('retry_authorization_binding_changed');
  for(const key of Object.keys(r.baseline_artifacts))if(jsonHash(stateFile,v=>v.artifacts[key])!==jsonHash(rows[0].path,v=>v.baseline_artifacts[key]))fail('historical_artifact_changed');
  for(const [id,names]of Object.entries(r.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('historical_call_files_changed');
  for(const proof of r.protected_files)if(fileHash(proof.path)!==proof.sha256)fail('historical_bytes_changed');
  for(const proof of r.journal_prefixes)if(prefixHash(proof.path,proof.bytes)!==proof.sha256)fail('historical_journal_changed');
  verifyKnownFailure(root,state,p,r);
  const dispatch=loadKnownHttp500DispatchResume(root,state,p,r),added=state.calls.slice(BASELINE),seen=new Set();
  if(added.length&&(added[0].id!=='glm_225_'+RETRY||added[0].name!==RETRY||added[0].repair_of))fail('retry_must_be_first_new_call');
  for(let i=0;i<added.length;i++){
    const call=added[i],file=path.join(root,'calls',call.id,'request.json'),request=read(file);
    if(seen.has(call.name)||!['received','submitted','uncertain','failed_known'].includes(call.status)||jsonHash(file)!==call.request_sha256)fail('new_stage_or_request_changed');
    seen.add(call.name);
    if([DISPATCH,DISPATCH+'_repair'].includes(call.name)){
      if(!dispatch)fail('dispatch_resume_required');
    }else if([RETRY,RETRY+'_repair'].includes(call.name)){
      const copy=structuredClone(request);if(copy.known_failure_retry_of!==FAILED)fail('retry_parent_required');delete copy.known_failure_retry_of;
      if(call.name.endsWith('_repair')){
        if(i!==1||call.repair_of!==added[0].id||added[0].status!=='received'||
            !copy.arguments.prompt.startsWith(r.bound_request.arguments.prompt+REPAIR_PREFIX))fail('sole_retry_format_repair_required');
        copy.arguments.prompt=r.bound_request.arguments.prompt;
      }
      if(!same(copy,r.bound_request))fail('retry_request_differs_from_original');
    }else if(!STAGES.test(call.name)||call.name.startsWith(STAGE)||'known_failure_retry_of'in request)fail('later_retry_not_authorized');
  }
  return r;
}

function verifyDispatchStop(root,state,p,known,r){
  const call=state.calls[DISPATCH_BASELINE-1],folder=path.join(root,'calls',DISPATCH_FAILED);
  if(state.request_count!==state.calls.length||state.calls.length<DISPATCH_BASELINE||call.id!==DISPATCH_FAILED||
      call.name!==RETRY||call.status!=='failed_known'||call.repair_of)fail('known_no_dispatch_225_required');
  if(['response.json','parsed.json','protocol_failure.json'].some(f=>fs.existsSync(path.join(folder,f))))fail('undispatched_call_has_model_result');
  const requestFile=path.join(folder,'request.json'),request=read(requestFile),expected={...known.bound_request,known_failure_retry_of:FAILED};
  if(!same(request,expected)||jsonHash(requestFile)!==call.request_sha256)fail('original_225_request_changed');
  const failureFile=path.join(folder,'failure.json'),failure=read(failureFile);
  if(failure.uncertain!==false||typeof failure.error!=='string'||!failure.error.includes('library_mcp_retry_or_budget_blocked'))fail('exact_local_dispatch_error_required');
  const queued=read(path.join(root,'mcp_queue',DISPATCH_FAILED+'.request.json'));
  if(queued.job_id!==DISPATCH_FAILED||queued.tool!==request.tool||!same(queued.arguments,request.arguments))fail('undispatched_queue_request_changed');
  const responseFile=path.join(root,'mcp_queue',DISPATCH_FAILED+'.response.json'),response=read(responseFile),
    message='Error: Unexpected error: analyze-image analysis failed: Network error: library_mcp_retry_or_budget_blocked';
  if(response.status!=='error'||!same(response.result,{content:[{type:'text',text:message}],isError:true}))fail('only_known_dispatch_guard_error_supported');
  const journals=fs.readdirSync(root).filter(name=>/^mcp_http.*\.jsonl$/.test(name)).sort().map(name=>path.join(root,name));
  if(!journals.length||!journals.includes(path.join(root,'mcp_http.jsonl')))fail('dispatch_http_journal_required');
  for(const journal of journals)if(events(journal,DISPATCH_FAILED).length)fail('225_has_actual_http_evidence');
  const rows=state.artifacts['mc2_input_'+RETRY];if(rows?.length!==1)fail('undispatched_input_descriptor_required');
  const descriptorFile=rows[0].path,d=read(descriptorFile);
  if(jsonHash(descriptorFile)!==rows[0].sha256||!same(d,{...known.bound_input_descriptor,stage:RETRY}))fail('undispatched_descriptor_changed');
  const stopRows=state.artifacts.mc2_network_result;if(stopRows?.length!==1)fail('dispatch_stop_receipt_required');
  const receiptFile=stopRows[0].path,receipt=read(receiptFile),resultFile=path.join(p.execution_directory,'result_network_recovered.json'),result=read(resultFile);
  if(jsonHash(receiptFile)!==stopRows[0].sha256||receipt.policy!=='microclip_slot_finecut_v2'||
      path.resolve(receipt.result_path)!==path.resolve(resultFile)||receipt.model_status!=='stopped'||result.status!=='stopped'||
      result.error!=='official_MCP_failure:'+DISPATCH_FAILED||result.model_quality_gate_passed!==false||
      ['final_video','final_sha256','measured_duration_s'].some(k=>result[k]!==null))fail('known_no_dispatch_stop_only');
  if(r.baseline_artifacts.mc2_render_claim||r.baseline_artifacts.mc2_dispatch_result||fs.existsSync(r.unused_render_sentinel))fail('dispatch_unused_original_render_required');
  const parentRows=state.artifacts[ARTIFACT];if(parentRows?.length!==1||!same(read(parentRows[0].path),known))fail('original_known500_grant_changed');
  const current={parent_resume_path:parentRows[0].path,parent_resume_sha256:fileHash(parentRows[0].path),bound_request:known.bound_request,
    bound_input_descriptor:d,descriptor_path:path.resolve(descriptorFile),descriptor_sha256:fileHash(descriptorFile),
    failed_result_path:path.resolve(resultFile),failed_result_sha256:fileHash(resultFile),failed_receipt_path:path.resolve(receiptFile),failed_receipt_sha256:fileHash(receiptFile),
    failure_path:path.resolve(failureFile),failure_sha256:fileHash(failureFile),queue_response_path:path.resolve(responseFile),queue_response_sha256:fileHash(responseFile)};
  for(const [k,v]of Object.entries(current))if(!same(r[k],v))fail('no_dispatch_proof_changed');
  for(const row of r.no_dispatch_journal_prefixes)if(prefixHash(row.path,row.bytes)!==row.sha256)fail('no_dispatch_prefix_changed');
}

export function loadKnownHttp500DispatchResume(root,state,p,known){
  const rows=state.artifacts?.[DISPATCH_ARTIFACT];if(!rows)return null;
  if(rows.length!==1||jsonHash(rows[0].path)!==rows[0].sha256||!path.resolve(rows[0].path).startsWith(path.resolve(root,'artifacts')+path.sep))fail('one_dispatch_recovery_required');
  const r=read(rows[0].path),authFile=state.artifacts.microclip_slot_finecut_v2?.[0]?.path,stateFile=path.join(root,'library_state.json');
  if(r.policy!==DISPATCH_POLICY||r.task_id!==state.task_id||r.baseline_request_count!==DISPATCH_BASELINE||
      r.original_authorization_sha256!==fileHash(authFile)||r.failed_call_id!==DISPATCH_FAILED||r.failed_stage!==RETRY||r.retry_stage!==DISPATCH||
      r.max_transport_retries!==1||r.repairs_per_stage!==1||r.actual_post_count_for_failed_call!==0||r.new_renders!==0||
      r.reuse_original_unused_render!==true||r.goal_resumed!==false||
      typeof r.user_instruction!=='string'||!r.user_instruction.trim()||
      path.resolve(r.unused_render_sentinel)!==path.resolve(p.execution_directory,'.known500_dispatch_unused_render')||
      r.prefix_calls_sha256!==jsonHash(stateFile,v=>v.calls.slice(0,DISPATCH_BASELINE))||
      !same(Object.keys(r.baseline_call_files).sort(),state.calls.slice(0,DISPATCH_BASELINE).map(c=>c.id).sort()))fail('dispatch_policy_binding_changed');
  for(const key of Object.keys(r.baseline_artifacts))if(jsonHash(stateFile,v=>v.artifacts[key])!==jsonHash(rows[0].path,v=>v.baseline_artifacts[key]))fail('dispatch_historical_artifact_changed');
  for(const [id,names]of Object.entries(r.baseline_call_files))if(!same(files(path.join(root,'calls',id)),names))fail('dispatch_historical_call_files_changed');
  for(const proof of r.protected_files)if(fileHash(proof.path)!==proof.sha256)fail('dispatch_historical_bytes_changed');
  for(const proof of r.journal_prefixes)if(prefixHash(proof.path,proof.bytes)!==proof.sha256)fail('dispatch_journal_changed');
  verifyDispatchStop(root,state,p,known,r);
  const added=state.calls.slice(DISPATCH_BASELINE),seen=new Set();
  if(added.length&&(added[0].id!=='glm_226_'+DISPATCH||added[0].name!==DISPATCH||added[0].repair_of))fail('dispatch_must_be_first_new_call');
  for(let i=0;i<added.length;i++){
    const call=added[i],file=path.join(root,'calls',call.id,'request.json'),request=read(file);
    if(seen.has(call.name)||jsonHash(file)!==call.request_sha256)fail('dispatch_stage_or_request_changed');seen.add(call.name);
    if([DISPATCH,DISPATCH+'_repair'].includes(call.name)){
      const copy=structuredClone(request);if(copy.known_failure_retry_of!==DISPATCH_FAILED)fail('dispatch_parent_required');delete copy.known_failure_retry_of;
      if(call.name.endsWith('_repair')){
        if(i!==1||call.repair_of!==added[0].id||added[0].status!=='received'||
            !copy.arguments.prompt.startsWith(r.bound_request.arguments.prompt+REPAIR_PREFIX))fail('sole_dispatch_format_repair_required');
        copy.arguments.prompt=r.bound_request.arguments.prompt;
      }
      if(!same(copy,r.bound_request))fail('dispatch_request_differs_from_original');
    }else if(!STAGES.test(call.name)||call.name.startsWith(STAGE)||'known_failure_retry_of'in request)fail('another_dispatch_retry_not_authorized');
  }
  return r;
}
