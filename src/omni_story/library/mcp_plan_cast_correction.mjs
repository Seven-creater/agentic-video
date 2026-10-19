// Exact same-task grant for one correction after received cast diagnostics.
// A failed local stage guard is preserved; no unknown or submitted work is replayed.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';

const sha = raw => crypto.createHash('sha256').update(raw).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file,'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value==='object' ?
  Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])) : value;
const same = (a,b) => JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const require = (ok,reason) => {if(!ok)throw new Error('library_plan_cast_'+reason);};

export function planCastCorrection(root,state) {
  const prefix=fs.existsSync(path.join(root,'plan_cast_transport_v1.claim.json')) ?
    'plan_cast_transport_v1' : 'plan_cast_correction_v1';
  const file=path.join(root,prefix+'.claim.json');
  if(!fs.existsSync(file))return null;
  const claim=read(file);
  require(sha(fs.readFileSync(claim.proof_path))===claim.proof_sha256,'proof_changed');
  const proof=read(claim.proof_path),transport=prefix==='plan_cast_transport_v1';
  require(proof.policy===(transport?'received_plan_cast_transport_v1':'received_plan_cast_correction_v1') &&
    proof.output===root && proof.plan_name==='plan_1' && proof.max_new_correction_calls===1,'wrong_scope');
  const raw=fs.readFileSync(proof.baseline_snapshot);
  require(sha(raw)===proof.state_sha256,'baseline_changed');
  const baseline=JSON.parse(raw),count=baseline.calls.length;
  require(count===proof.baseline_request_count && count===(transport?11:10) &&
    same(state.calls.slice(0,count),baseline.calls) && state.task_id===baseline.task_id &&
    same(state.input_lock,baseline.input_lock),'original_calls_changed');
  for(const [file,digest] of Object.entries(proof.protected_sha256)) {
    const actual=file===path.join(root,'mcp_stop') ? path.join(root,prefix+'.original_mcp_stop') : file;
    require(sha(fs.readFileSync(actual))===digest,'protected_file_changed');
  }
  const paid=baseline.calls.slice(0,10);
  require(paid.every(call=>call.status==='received') && paid[8].name==='plan_1' &&
    paid[9].name==='plan_1_repair' && paid[9].repair_of===paid[8].id,'paid_baseline_invalid');
  let failed=null;
  if(transport) {
    failed=baseline.calls[10];
    const folder=path.join(root,'calls',failed.id),local=read(path.join(folder,'failure.json'));
    require(failed.id===proof.failed_preflight_id && failed.name==='plan_1_cast_correction_v1' &&
      failed.status==='failed_known' && local.uncertain===false &&
      local.error.includes('library_mcp_opencode_stage_duplicate_or_not_permitted'),'not_local_preflight');
    require(sha(fs.readFileSync(proof.previous_proof))===proof.previous_proof_sha256,'previous_proof_changed');
    const previous=read(proof.previous_proof),original=read(path.join(folder,'request.json'));
    require(previous.policy==='received_plan_cast_correction_v1' &&
      persistedJsonHash(fs.readFileSync(path.join(folder,'request.json'),'utf8'))===failed.request_sha256 &&
      same(original,previous.expected_request) && same(proof.expected_request,
        {...original,arguments:{...original.arguments,prompt:original.arguments.prompt+proof.transport_prompt_suffix}}),
      'preflight_request_changed');
    const journal=path.join(root,'mcp_http.jsonl');
    require(!fs.existsSync(journal) || !fs.readFileSync(journal,'utf8').split('\n').filter(Boolean)
      .some(line=>{const row=JSON.parse(line);return row.job_id===failed.id && row.type==='request';}),
      'preflight_post_exists');
  }
  const alias=transport?'plan_1_cast_correction_v1_preflight_v1':'plan_1_cast_correction_v1';
  require(!transport || proof.correction_alias===alias,'alias_changed');
  const corrections=state.calls.slice(count).filter(call=>call.name.startsWith('plan_1_cast_correction'));
  require(corrections.length===1 && corrections[0].name===alias && !corrections[0].repair_of &&
    ['submitted','received'].includes(corrections[0].status),'single_correction_required');
  const correction=corrections[0],requestFile=path.join(root,'calls',correction.id,'request.json');
  require(same(read(requestFile),proof.expected_request) &&
    persistedJsonHash(fs.readFileSync(requestFile,'utf8'))===correction.request_sha256,'correction_request_changed');
  require(!fs.existsSync(path.join(root,'mcp_stop')),'user_stop');
  return {alias,failedPreflightId:failed?.id};
}
