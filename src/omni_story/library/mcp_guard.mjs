// Guard the official MCP server: never retry an uncertain model POST.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {slotRequestLimit} from './mcp_slot_finecut_guard.mjs';
import {forwardSlotRequestLimit} from './mcp_forward_slot_guard.mjs';
import {parentCutRequestLimit} from './mcp_parent_cut_guard.mjs';
import {microclipRequestLimit} from './mcp_microclip_guard.mjs';
import {microclipV2RequestLimit, loadMicroclipV2} from './mcp_microclip_v2_guard.mjs';
const visualStoryRequestLimit = process.env.OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE || process.env.OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256
  ? (await import('./mcp_visual_story_guard.mjs')).visualStoryRequestLimit : () => null;
const originalFetch = globalThis.fetch;
const root = process.env.OMNI_LIBRARY_MCP_ROOT;
if (!root) throw new Error('library_mcp_root_missing');
const {Agent} = await import(pathToFileURL(path.join(process.env.OMNI_LIBRARY_MCP_PACKAGE_ROOT,
  'node_modules/undici/index.js')).href);
// The official server is non-streaming; keep the underlying HTTP deadline aligned
// with its configured model timeout rather than Node's shorter header deadline.
const {connectionTimeouts} = await import('./mcp_timeouts.mjs');
const {modelTimeoutMs} = connectionTimeouts(root);
const dispatcher = new Agent({headersTimeout: modelTimeoutMs, bodyTimeout: modelTimeoutMs, connect: {timeout: 30000}});
const currentFile = process.env.OMNI_LIBRARY_MCP_CURRENT_FILE || path.join(root, 'mcp_current.json');
const journal = process.env.OMNI_LIBRARY_MCP_HTTP_JOURNAL || path.join(root, 'mcp_http.jsonl');
const lane = /^mcp_http_sf_(0|3)\.jsonl$/.exec(path.basename(journal))?.[1];
if (path.resolve(path.dirname(currentFile)) !== path.resolve(root) ||
    path.resolve(path.dirname(journal)) !== path.resolve(root) ||
    (lane === undefined ? path.basename(currentFile) !== 'mcp_current.json' || path.basename(journal) !== 'mcp_http.jsonl' :
      path.basename(currentFile) !== `mcp_current_sf_${lane}.json` || process.env.OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY !== '2')) {
  throw new Error('library_mcp_lane_files_invalid');
}
const historyJournals = lane === undefined && !process.env.OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE ? [journal] :
  ['mcp_http.jsonl', 'mcp_http_sf_0.jsonl', 'mcp_http_sf_3.jsonl'].map(file => path.join(root, file));
const seen = new Set();
const seenJobs = new Set();
const seenHashJobs = new Map();
let count = 0;
let sequence = 0;
function* journalLines(file) {
  const fd = fs.openSync(file, 'r'), chunk = Buffer.alloc(256 * 1024);
  let carry = Buffer.alloc(0);
  try {
    let size;
    while ((size = fs.readSync(fd, chunk, 0, chunk.length, null))) {
      const buffer = Buffer.concat([carry, chunk.subarray(0, size)]);
      let start = 0, end;
      while ((end = buffer.indexOf(10, start)) !== -1) {
        if (end > start) yield buffer.subarray(start, end).toString('utf8');
        start = end + 1;
      }
      carry = Buffer.from(buffer.subarray(start));
    }
    if (carry.length) yield carry.toString('utf8');
  } finally { fs.closeSync(fd); }
}
function remember(hash, job) {
  seen.add(hash); seenJobs.add(job);
  if (!seenHashJobs.has(hash)) seenHashJobs.set(hash, new Set());
  seenHashJobs.get(hash).add(job);
}
for (const history of historyJournals) {
  for (const line of fs.existsSync(history) ? journalLines(history) : []) {
    const record = JSON.parse(line);
    if (record.type === 'request') {
      remember(record.hash, record.job_id); count += 1;
      if (history === journal) sequence = Math.max(sequence, record.seq || 0);
    }
  }
}
function log(record) { fs.appendFileSync(journal, JSON.stringify(record) + '\n'); }
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const fileHash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function inside(file, directory) {
  const relative = path.relative(fs.realpathSync(directory), fs.realpathSync(file));
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
}
function scopeKey(scope) {
  if (!scope || !['continuous_window', 'sparse_contact_sheet', 'complete_file'].includes(scope.kind) ||
      !/^[a-f0-9]{64}$/.test(scope.source_sha256) || !Number.isFinite(scope.source_start_s) ||
      !Number.isFinite(scope.source_end_s) || scope.source_start_s < 0 || scope.source_end_s <= scope.source_start_s) {
    throw new Error('library_mcp_independent_scope_invalid');
  }
  return JSON.stringify([scope.kind, scope.source_sha256, scope.source_start_s, scope.source_end_s]);
}
function independentPolicy(state, grant, call) {
  const file = process.env.OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE;
  if (!file) return null;
  const entries = state.artifacts?.goal_research_independent_11;
  if (entries?.length !== 1 || fs.realpathSync(entries[0].path) !== fs.realpathSync(file) ||
      !inside(file, path.join(root, 'artifacts')) || fileHash(file) !== process.env.OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256) {
    throw new Error('library_mcp_independent_authorization_modified');
  }
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (record.policy !== 'independent_library_evidence_after_unknown_v1' || record.round !== 11 ||
      record.task_id !== state.task_id || record.input_lock_sha256 !== grant.input_lock_sha256 ||
      record.reference_sha256 !== state.input_lock.reference_sha256 || record.baseline_request_count !== 131 ||
      record.activation_baseline_requests !== 131 || record.new_unique_windows !== 0 || record.new_renders !== 1 ||
      record.repairs_per_stage !== 1 || record.reference_media_forbidden !== true || record.old_calls_unchanged !== true ||
      record.no_numeric_request_ceiling !== true || record.reference_is_known_cached_model_interpretation !== true ||
      record.model_timeout_ms !== 1200000 || record.tool_timeout_ms !== 1260000 ||
      !inside(record.baseline_state_path, path.join(root, 'artifacts')) ||
      fileHash(record.baseline_state_path) !== record.baseline_state_sha256) {
    throw new Error('library_mcp_independent_scope_changed');
  }
  const baseline = JSON.parse(fs.readFileSync(record.baseline_state_path, 'utf8'));
  if (baseline.request_count !== 131 || baseline.calls.length !== 131 ||
      !same(state.calls.slice(0, 131), baseline.calls) ||
      !same(record.admitted_unknown_call_ids, baseline.calls.filter(c => c.status === 'uncertain').map(c => c.id)) ||
      !Array.isArray(record.prefacts) || record.prefacts.length !== 6 ||
      new Set(record.prefacts.map(p => p.stage)).size !== 6 ||
      record.planning_carrier_stage !== record.prefacts[0].stage) {
    throw new Error('library_mcp_independent_baseline_changed');
  }
  for (const item of record.protected_files) {
    if (!inside(item.path, path.join(root, 'calls')) || fileHash(item.path) !== item.sha256) {
      throw new Error('library_mcp_independent_protected_file_changed');
    }
  }
  if (record.unknown_inputs.length !== record.admitted_unknown_call_ids.length) {
    throw new Error('library_mcp_independent_exclusions_changed');
  }
  for (const [i, id] of record.admitted_unknown_call_ids.entries()) {
    const prior = baseline.calls.find(c => c.id === id);
    const input = record.unknown_inputs[i];
    const request = JSON.parse(fs.readFileSync(path.join(root, 'calls', id, 'request.json'), 'utf8'));
    let scope = request.observation_scope;
    if (!scope) {
      const source = request.arguments.image_source || request.arguments.video_source;
      scope = JSON.parse(fs.readFileSync(path.join(path.dirname(source), 'lineage.json'), 'utf8'));
    }
    if (input.call_id !== id || input.request_sha256 !== prior.request_sha256 ||
        input.media_sha256 !== request.media_sha256 || scopeKey(input.scope) !== scopeKey(scope)) {
      throw new Error('library_mcp_independent_exclusions_changed');
    }
  }
  const round = /^active_(\d+)_/.exec(call?.name || '')?.[1] || /^semantic_(?:slice|claims)_(\d+)_/.exec(call?.name || '')?.[1];
  if (round !== '11' || state.calls.indexOf(call) < 131 ||
      state.calls.some(c => c !== call && (c.status === 'submitted' ||
        ['uncertain', 'failed_known'].includes(c.status) && !record.admitted_unknown_call_ids.includes(c.id)))) {
    throw new Error('library_mcp_independent_new_outcome_unknown');
  }
  const request = JSON.parse(fs.readFileSync(path.join(root, 'calls', call.id, 'request.json'), 'utf8'));
  const scope = request.observation_scope;
  if (scope?.source_sha256 === record.reference_sha256 || record.unknown_inputs.some(input =>
      input.request_sha256 === call.request_sha256 || input.media_sha256 === request.media_sha256 ||
      scopeKey(input.scope) === scopeKey(scope))) {
    throw new Error('library_mcp_independent_unknown_input_replay');
  }
  scopeKey(scope);
  const stem = call.name.replace(/_repair$/, '');
  const prefact = record.prefacts.find(p => p.stage === stem);
  const priorCalls = state.calls.slice(131).filter(c => c !== call);
  const newNames = state.calls.slice(131).map(c => c.name);
  if (new Set(newNames).size !== newNames.length) throw new Error('library_mcp_independent_duplicate_stage');
  const known = stage => priorCalls.filter(c => [stage, stage + '_repair'].includes(c.name)).at(-1);
  const received = stage => {
    const prior = known(stage);
    return prior?.status === 'received' && fs.existsSync(path.join(root, 'calls', prior.id, 'parsed.json'));
  };
  if (call.name.endsWith('_repair')) {
    const parent = known(stem);
    if (parent?.status !== 'received' || parent.repair_of || call.repair_of !== parent.id ||
        priorCalls.at(-1) !== parent || priorCalls.some(c => c.repair_of === parent.id)) {
      throw new Error('library_mcp_independent_repair_parent_invalid');
    }
  } else if (call.repair_of != null) {
    throw new Error('library_mcp_independent_repair_parent_invalid');
  }
  if (prefact) {
    const expected = {kind: 'continuous_window', source_sha256: prefact.source_sha256,
      source_start_s: prefact.segment.source_in_s, source_end_s: prefact.segment.source_out_s};
    if (scopeKey(scope) !== scopeKey(expected) || request.tool !== 'analyze_image' ||
        fileHash(request.arguments.image_source) !== request.media_sha256 || known('active_11_draft')) {
      throw new Error('library_mcp_independent_prefact_binding_changed');
    }
  } else if (stem === 'active_11_draft') {
    if (!record.prefacts.every(p => {
      if (!received(p.stage)) return false;
      const prior = known(p.stage);
      const value = JSON.parse(fs.readFileSync(path.join(root, 'calls', prior.id, 'parsed.json'), 'utf8'));
      return value.source_sha256 === p.source_sha256 && Object.keys(p.segment).every(k => same(value[k], p.segment[k]));
    })) throw new Error('library_mcp_independent_prefacts_incomplete');
  } else if (stem === 'active_11_finecut') {
    if (!received('active_11_draft')) throw new Error('library_mcp_independent_predecessor_unsettled');
  } else if (/^semantic_(slice|claims)_11_/.test(stem) || stem === 'active_11_blind') {
    if (!received('active_11_finecut') || stem.startsWith('semantic_claims_') &&
        !received(stem.replace('semantic_claims_', 'semantic_slice_'))) {
      throw new Error('library_mcp_independent_predecessor_unsettled');
    }
  } else if (stem === 'active_11_economy' || stem === 'active_11_review') {
    if (!received(stem === 'active_11_economy' ? 'active_11_blind' : 'active_11_economy')) {
      throw new Error('library_mcp_independent_predecessor_unsettled');
    }
  }
  return record;
}
function requestLimit(currentJob) {
  const visualStoryLimit = visualStoryRequestLimit(root, currentJob);
  if (visualStoryLimit !== null) return visualStoryLimit;
  const microclipV2Limit = microclipV2RequestLimit(root, currentJob);
  if (microclipV2Limit !== null) return microclipV2Limit;
  const microclipLimit = microclipRequestLimit(root, currentJob);
  if (microclipLimit !== null) return microclipLimit;
  const parentLimit = parentCutRequestLimit(root, currentJob);
  if (parentLimit !== null) return parentLimit;
  const forwardLimit = forwardSlotRequestLimit(root, currentJob);
  if (forwardLimit !== null) return forwardLimit;
  const slotLimit = slotRequestLimit(root, currentJob);
  if (slotLimit !== null) return slotLimit;
  const authorizationFile = process.env.OMNI_LIBRARY_EXTENSION_AUTH_FILE;
  if (!authorizationFile) return Math.min(80, Number(process.env.OMNI_LIBRARY_MAX_REQUESTS || 80));
  const raw = fs.readFileSync(authorizationFile);
  if (crypto.createHash('sha256').update(raw).digest('hex') !== process.env.OMNI_LIBRARY_EXTENSION_AUTH_SHA256) {
    throw new Error('library_mcp_extension_authorization_modified');
  }
  const grant = JSON.parse(raw.toString('utf8'));
  const state = JSON.parse(fs.readFileSync(path.join(root, 'library_state.json'), 'utf8'));
  const call = state.calls.find(row => row.id === currentJob.job_id);
  if (grant.policy === 'goal_feedback_extension_v1') {
    const independent = independentPolicy(state, grant, call);
    const stages = /^(?:active_([5-9]|[1-9][0-9]+)_(?:draft|finecut|blind|economy|review)|semantic_(?:slice|claims)_([5-9]|[1-9][0-9]+)_[a-f0-9]{16})(?:_repair)?$/;
    let localStage = false;
    if (call && /^active_8_trim_[a-f0-9]{16}(?:_repair)?$/.test(call.name)) {
      const entries = state.artifacts.goal_research_local_8;
      if (entries?.length === 1) {
        const local = JSON.parse(fs.readFileSync(entries[0].path, 'utf8'));
        const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
        const digest = crypto.createHash('sha256').update(JSON.stringify(canonical(local))).digest('hex');
        const cardHash = crypto.createHash('sha256').update(fs.readFileSync(local.knowledge_path)).digest('hex');
        localStage = digest === entries[0].sha256 && local.policy === 'local_counterfactual_trim_v1' &&
          local.round === 8 && Number.isInteger(local.activation_baseline_requests) &&
          local.input_lock_sha256 === grant.input_lock_sha256 &&
          local.additional_stage_pattern === '^active_8_trim_[a-f0-9]{16}(?:_repair)?$' &&
          local.one_local_proposal_per_parent === true && local.repairs_per_stage === 1 &&
          local.new_unique_windows === 0 && local.parent_inputs.some(p => p.stage === call.name.replace(/_repair$/, '')) &&
          state.calls.indexOf(call) >= local.activation_baseline_requests && cardHash === local.knowledge_sha256;
      }
    }
    if (grant.task_id !== state.task_id ||
        grant.request_limit_policy !== 'progress_guard_no_numeric_request_cap_v1' ||
        process.env.OMNI_LIBRARY_REQUEST_LIMIT_POLICY !== grant.request_limit_policy ||
        grant.base_request_limit !== state.max_requests || grant.additional_requests !== null ||
        !Number.isInteger(state.max_requests) || state.max_requests < 1 || state.max_requests > 80 ||
        grant.effective_request_limit !== null ||
        !Number.isInteger(grant.baseline_request_count) || grant.baseline_request_count < 0 ||
        state.request_count !== state.calls.length || !call || call.status !== 'submitted' ||
        state.calls.at(-1) !== call ||
        state.calls.indexOf(call) < grant.baseline_request_count ||
        state.calls.slice(grant.baseline_request_count).some(row => row !== call &&
          (row.status === 'submitted' || row.status === 'uncertain' &&
            !independent?.admitted_unknown_call_ids.includes(row.id))) ||
        !localStage && (!stages.test(call.name) || !new RegExp(grant.allowed_stage_pattern).test(call.name))) {
      throw new Error('library_mcp_goal_budget_or_stage_blocked');
    }
    return Infinity;
  }
  if (grant.policy !== 'active_finecut_extension_v2' || grant.task_id !== state.task_id ||
      grant.request_limit_policy !== 'progress_guard_no_numeric_request_cap_v1' ||
      process.env.OMNI_LIBRARY_REQUEST_LIMIT_POLICY !== grant.request_limit_policy ||
      grant.base_request_limit !== state.max_requests || grant.additional_requests !== null ||
      grant.effective_request_limit !== null || !call || call.status !== 'submitted' ||
      state.calls.indexOf(call) < grant.baseline_request_count ||
      !new RegExp(grant.allowed_stage_pattern).test(call.name)) {
    throw new Error('library_mcp_extension_budget_or_stage_blocked');
  }
  return Infinity;
}
function captured500BodyPermit(currentJob, hash) {
  if (!/^glm_\d+_mc2_region_1_retry(?:_dispatch)?$/.test(currentJob.job_id)) return false;
  const value = loadMicroclipV2(root), known = value?.networkResume;
  if (!known) return false;
  const grant = value.dispatchResume || known;
  const call = value.state.calls.find(c => c.id === currentJob.job_id);
  if (!call || call.status !== 'submitted' || call.name !== grant.retry_stage) return false;
  // The exceptional duplicate must be the exact native body of the captured
  // 224 HTTP500. Any POST by this alias, including after process restart,
  // remains blocked by seenJobs. No unknown or other failed body is admitted.
  const owners = seenHashJobs.get(hash);
  if (owners?.size !== 1 || !owners.has(known.failed_call_id)) {
    throw new Error('library_mcp_known500_native_body_changed');
  }
  return true;
}
globalThis.fetch = async (input, init = {}) => {
  const url = String(input);
  if (init.method !== 'POST') return originalFetch(input, init);
  if (url !== 'https://open.bigmodel.cn/api/paas/v4/chat/completions') {
    throw new Error('library_mcp_endpoint_blocked');
  }
  const body = String(init.body);
  const hash = crypto.createHash('sha256').update(body).digest('hex');
  const currentJob = JSON.parse(fs.readFileSync(currentFile, 'utf8'));
  if (lane !== undefined) {
    const parent = /^glm_[0-9]+_(?:sf(?:v2)?|pc)_(0|3)_/.exec(currentJob.job_id)?.[1];
    const semantic = /^glm_[0-9]+_semantic_(?:slice|claims)_(20|21)_/.exec(currentJob.job_id)?.[1];
    const actual = parent ?? (semantic === '20' ? '0' : semantic === '21' ? '3' : null);
    if (actual !== lane) throw new Error('library_mcp_lane_job_parent_mismatch');
  }
  const limit = requestLimit(currentJob);
  if (seenJobs.has(currentJob.job_id) || count >= limit) throw new Error('library_mcp_retry_or_budget_blocked');
  const known500Duplicate = captured500BodyPermit(currentJob, hash);
  if (seen.has(hash) && !known500Duplicate) throw new Error('library_mcp_retry_or_budget_blocked');
  count += 1;
  const seq = ++sequence;
  remember(hash, currentJob.job_id);
  log({type: 'request', seq, job_id: currentJob.job_id, hash, url, body: JSON.parse(body), at: new Date().toISOString()});
  try {
    const response = await originalFetch(input, {...init, dispatcher});
    const raw = await response.clone().text();
    log({type: 'response', seq, job_id: currentJob.job_id, status: response.status, body: raw,
      headers: Object.fromEntries([...response.headers].filter(([k]) => /request|usage|limit|quota/i.test(k))), at: new Date().toISOString()});
    return response;
  } catch (error) {
    log({type: 'unknown_result', seq, job_id: currentJob.job_id, error: String(error),
      cause: error.cause ? {name: error.cause.name, code: error.cause.code, message: error.cause.message} : null,
      at: new Date().toISOString()});
    throw error;
  }
};
