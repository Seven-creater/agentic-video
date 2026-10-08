// Optional separately authorized slot comparison. No authorization is created here.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {validateRequestBoundAssembly} from './mcp_slot_finecut_assembly_binding.mjs';
import {readValidateProposalRetiming, checkProposalRetimingInput} from './mcp_slot_proposal_timing_binding.mjs';
import {validateSourceFeedback, validateSourceFeedbackRequest, SOURCE_FEEDBACK_PATTERN} from './mcp_slot_source_feedback.mjs';
import {validateIndependentSlotRecovery, checkIndependentSlotRecoveryInput,
  independentRecoveryAdmissibleStatus, LOCAL_REPLAN_PATTERN} from './mcp_independent_slot_recovery.mjs';

const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
const digest = value => crypto.createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const fileProofs = new Map();
class NumericLiteral {
  constructor(token) { this.token = token; }
}
function jsonDigest(raw, select = value => value) {
  // Python json_sha preserves 1.0; plain JS stringify would silently turn it
  // into 1 and reject a valid immutable record. Node 22 exposes numeric lexemes.
  const value = JSON.parse(raw, (key, item, context) => {
    if (typeof item !== 'number') return item;
    if (!context?.source) throw new Error('library_mcp_slot_numeric_lexemes_unavailable');
    return new NumericLiteral(context.source);
  });
  function serialize(item) {
    if (item instanceof NumericLiteral) return item.token;
    if (Array.isArray(item)) return '[' + item.map(serialize).join(',') + ']';
    if (item && typeof item === 'object') return '{' + Object.keys(item).sort()
      .map(key => JSON.stringify(key) + ':' + serialize(item[key])).join(',') + '}';
    return JSON.stringify(item);
  }
  return crypto.createHash('sha256').update(serialize(select(value))).digest('hex');
}
const fileDigest = (file, select) => jsonDigest(fs.readFileSync(file, 'utf8'), select);
function hash(file) {
  const actual = fs.realpathSync(file);
  const stamp = () => {
    const s = fs.statSync(actual, {bigint: true});
    if (!s.isFile()) throw new Error('library_mcp_slot_proof_requires_regular_file');
    return [s.size, s.mtimeNs, s.ctimeNs, s.ino, s.dev].join(':');
  };
  const before = stamp(), cached = fileProofs.get(actual);
  if (cached && cached.stamp === before) {
    if (stamp() !== before) throw new Error('library_mcp_slot_file_changed_during_proof');
    return cached.hash;
  }
  fileProofs.delete(actual);
  const h = crypto.createHash('sha256'), buffer = Buffer.alloc(1024 * 1024);
  const fd = fs.openSync(actual, 'r');
  try { let size; while ((size = fs.readSync(fd, buffer)) > 0) h.update(buffer.subarray(0, size)); }
  finally { fs.closeSync(fd); }
  if (stamp() !== before) throw new Error('library_mcp_slot_file_changed_while_hashing');
  const value = h.digest('hex');
  fileProofs.set(actual, {stamp: before, hash: value});
  return value;
}
function scope(scope) {
  if (!scope || !['continuous_window', 'sparse_contact_sheet', 'complete_file'].includes(scope.kind) ||
      !/^[a-f0-9]{64}$/.test(scope.source_sha256) || !Number.isFinite(scope.source_start_s) ||
      !Number.isFinite(scope.source_end_s) || !(0 <= scope.source_start_s && scope.source_start_s < scope.source_end_s)) {
    throw new Error('library_mcp_slot_scope_required');
  }
  return digest([scope.kind, scope.source_sha256, scope.source_start_s, scope.source_end_s]);
}
const STAGES = /^(?:sf_(?:0|3)_(?:outline|facts_[a-f0-9]{16}|proposal_[a-f0-9]{16}|assemble|blind|economy|review)|semantic_(?:slice|claims)_(?:20|21)_[a-f0-9]{16})(?:_repair)?$/;
const REPLACEMENT = /^sf_0_outline_v2(?:_repair)?$/;
function stageParent(name) {
  if (!STAGES.test(name) && !REPLACEMENT.test(name) && !SOURCE_FEEDBACK_PATTERN.test(name) && !LOCAL_REPLAN_PATTERN.test(name)) throw new Error('library_mcp_slot_budget_or_stage_blocked');
  const parts = name.split('_');
  return parts[0] === 'sf' ? Number(parts[1]) : (parts[2] === '20' ? 0 : 3);
}

export function loadParallel(root) {
  const stateFile = path.join(root, 'library_state.json'), state = read(stateFile);
  const entries = state.artifacts.sf_parallel_execution_v1;
  if (!entries) return null;
  const file = process.env.OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE;
  if (!file || hash(file) !== process.env.OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256) {
    throw new Error('library_mcp_slot_authorization_modified');
  }
  const grant = read(file), grants = state.artifacts.slot_finecut_comparison_v1;
  if (entries.length !== 1 || !grants || grants.length !== 1 ||
      path.resolve(grants[0].path) !== path.resolve(file) || grants[0].sha256 !== fileDigest(file) ||
      grant.policy !== 'slot_finecut_comparison_v1' || grant.task_id !== state.task_id ||
      path.resolve(grant.original_output) !== path.resolve(root)) throw new Error('library_mcp_slot_parallel_authorization_changed');
  const policy = read(entries[0].path), count = policy.baseline_request_count;
  const promptHash = typeof policy.prompt_policy === 'string' ?
    crypto.createHash('sha256').update(policy.prompt_policy, 'utf8').digest('hex') :
    fileDigest(entries[0].path, value => value.prompt_policy);
  if (entries[0].sha256 !== fileDigest(entries[0].path) || policy.policy !== 'sf_parallel_execution_v1' ||
      policy.task_id !== state.task_id || policy.preparation_id !== grant.preparation_id ||
      policy.authorization_record_sha256 !== grants[0].sha256 || policy.max_concurrent_parents !== 2 ||
      JSON.stringify(policy.parent_rounds) !== '[0,3]' || policy.replacement_outline_stage !== 'sf_0_outline_v2' ||
      !policy.user_instruction?.trim() || promptHash !== policy.prompt_policy_sha256 ||
      !Number.isInteger(count) || count < grant.baseline_request_count + 2 || count > state.calls.length ||
      fileDigest(stateFile, value => value.calls.slice(0, count)) !== policy.prefix_calls_sha256) {
    throw new Error('library_mcp_slot_parallel_authorization_changed');
  }
  const originals = state.calls.slice(grant.baseline_request_count, count).filter(c => stageParent(c.name) === 0);
  if (originals.length !== 2 || originals[0].name !== 'sf_0_outline' || originals[1].name !== 'sf_0_outline_repair' ||
      originals[0].repair_of || originals[1].repair_of !== originals[0].id ||
      originals.some(c => c.status !== 'received') ||
      JSON.stringify(policy.failed_call_ids) !== JSON.stringify(originals.map(c => c.id))) {
    throw new Error('library_mcp_slot_parallel_original_failure_changed');
  }
  const files = [];
  for (const [attempt, call] of originals.entries()) {
    const folder = path.join(root, 'calls', call.id);
    if (fs.existsSync(path.join(folder, 'parsed.json')) || read(path.join(folder, 'protocol_failure.json')).attempt !== attempt) {
      throw new Error('library_mcp_slot_parallel_original_failure_changed');
    }
    for (const name of ['request.json', 'response.json', 'protocol_failure.json']) {
      const filePath = path.join(folder, name);
      files.push({path: filePath, sha256: hash(filePath)});
    }
  }
  if (files.length !== policy.protected_failure_files.length || files.some((row, i) =>
      path.resolve(row.path) !== path.resolve(policy.protected_failure_files[i].path) ||
      row.sha256 !== policy.protected_failure_files[i].sha256)) throw new Error('library_mcp_slot_parallel_failure_files_changed');
  return policy;
}
export const slotParallelConfiguration = loadParallel;

export function requestBoundParentFacts(root, state, grant) {
  const entries = state.artifacts.sf_request_bound_parent_facts_v1;
  if (!entries) return null;
  if (entries.length !== 1) throw new Error('library_mcp_slot_request_bound_fact_changed');
  const file = entries[0].path, policy = read(file), count = policy.baseline_request_count;
  if (entries[0].sha256 !== fileDigest(file) || policy.policy !== 'sf_request_bound_parent_facts_v1' ||
      policy.task_id !== state.task_id || policy.preparation_id !== grant.preparation_id || policy.parent_round !== 0 ||
      policy.taxonomy_version !== 'parent_fact_inference_nonvisual_v2' || policy.original_contract_status !== 'failed' ||
      JSON.stringify(policy.program_field_additions) !== '{"time_domain":"slot_local_output"}' ||
      policy.semantic_truth_established !== false || policy.old_parsed_created !== false ||
      !/^sf_0_facts_[a-f0-9]{16}$/.test(policy.stage) || policy.next_stage !== policy.stage.replace('_facts_', '_proposal_') ||
      !Number.isInteger(count) || count < grant.baseline_request_count || count > state.calls.length ||
      fileDigest(path.join(root, 'library_state.json'), value => value.calls.slice(0, count)) !== policy.prefix_calls_sha256) {
    throw new Error('library_mcp_slot_request_bound_fact_changed');
  }
  const calls = state.calls.slice(grant.baseline_request_count).filter(c => [policy.stage, policy.stage + '_repair'].includes(c.name));
  if (calls.length !== 2 || calls[0].id !== policy.original_call_id || calls[1].id !== policy.repair_call_id ||
      calls[0].name !== policy.stage || calls[1].name !== policy.stage + '_repair' || calls[0].repair_of ||
      calls[1].repair_of !== calls[0].id || calls.some(c => c.status !== 'received')) {
    throw new Error('library_mcp_slot_request_bound_fact_reply_changed');
  }
  const preparation = read(grant.preparation_path), parent = preparation.parents.find(p => p.round === 0);
  const folder = path.join(grant.execution_directory, 'render_0'), outlineFile = path.join(folder, 'outline.json');
  const outline = read(outlineFile), bodies = [], requests = [], files = [];
  for (const [attempt, call] of calls.entries()) {
    const callFolder = path.join(root, 'calls', call.id), requestFile = path.join(callFolder, 'request.json');
    const responseFile = path.join(callFolder, 'response.json'), failureFile = path.join(callFolder, 'protocol_failure.json');
    const request = read(requestFile), response = read(responseFile), failure = read(failureFile);
    if (fs.existsSync(path.join(callFolder, 'parsed.json')) || fileDigest(requestFile) !== call.request_sha256 ||
        fileDigest(responseFile) !== call.response_sha256 || failure.attempt !== attempt || failure.error !== 'slot_finecut:fact_time_binding_changed') {
      throw new Error('library_mcp_slot_request_bound_fact_reply_changed');
    }
    let text = response.result.content.filter(c => c.type === 'text').map(c => c.text).join('\n').trim();
    if (text.startsWith('```')) {
      const match = /^```(?:json)?\s*\n([\s\S]+?)\n```$/.exec(text);
      if (!match) throw new Error('library_mcp_slot_request_bound_fact_reply_changed');
      text = match[1];
    }
    const body = JSON.parse(text);
    if (Object.hasOwn(body, 'time_domain')) throw new Error('library_mcp_slot_request_bound_fact_wrong_domain');
    bodies.push({value: body, text}); requests.push(request);
    for (const name of ['request.json', 'response.json', 'protocol_failure.json']) {
      const filePath = path.join(callFolder, name); files.push({path: filePath, sha256: hash(filePath)});
    }
  }
  const original = requests[0], repair = requests[1], chosen = bodies[1];
  const slot = outline.slots.find(s => s.slot_id === chosen.value.slot_id);
  if (!slot || slot.start_s !== 0 || fileDigest(outlineFile, value => ({parent: parent.sha256, slot: value.slots.find(s => s.slot_id === slot.slot_id)})).slice(0, 16) !== policy.stage.split('_').at(-1) ||
      jsonDigest(chosen.text) !== policy.model_body_sha256 ||
      jsonDigest(chosen.text, value => ({...value, time_domain: 'slot_local_output'})) !== fileDigest(file, value => value.body)) {
    throw new Error('library_mcp_slot_request_bound_fact_body_changed');
  }
  for (const {value: body} of bodies) {
    if (body.baseline_id !== parent.baseline_id || body.parent_sha256 !== parent.sha256 || body.slot_id !== slot.slot_id ||
        body.slot_start_s !== slot.start_s || body.slot_end_s !== slot.end_s || !Array.isArray(body.evidence) ||
        !Array.isArray(body.limitations) || !Array.isArray(body.uncertainties)) throw new Error('library_mcp_slot_request_bound_fact_contract');
    const ids = new Set();
    for (const e of body.evidence) {
      if (typeof e.evidence_id !== 'string' || !e.evidence_id.trim() || ids.has(e.evidence_id) || !Number.isFinite(e.start_s) ||
          !Number.isFinite(e.end_s) || !(0 <= e.start_s && e.start_s < e.end_s && e.end_s <= slot.end_s - slot.start_s + .001) ||
          typeof e.observed_fact !== 'string' || !e.observed_fact.trim() ||
          !['action', 'state', 'identity', 'result', 'reaction', 'shape', 'inference'].includes(e.kind) ||
          !['visual', 'visible_text', 'inference'].includes(e.basis) || e.kind === 'inference' && e.basis === 'visual') {
        throw new Error('library_mcp_slot_request_bound_fact_contract');
      }
      ids.add(e.evidence_id);
    }
  }
  const prompt = original.arguments.prompt, at = prompt.lastIndexOf('\n{');
  if (at < 0 || !prompt.includes('所有start_s/end_s是这个提供片段的局部输出秒数，从0开始')) {
    throw new Error('library_mcp_slot_request_bound_fact_prompt');
  }
  const context = JSON.parse(prompt.slice(at + 1)), contract = context.response_contract;
  if (contract.time_domain !== 'slot_local_output' || ['baseline_id', 'parent_sha256', 'slot_id', 'slot_start_s', 'slot_end_s']
      .some(k => context[k] !== contract[k] || contract[k] !== chosen.value[k])) throw new Error('library_mcp_slot_request_bound_fact_prompt');
  const media = original.arguments.video_source, lineageFile = path.join(path.dirname(media), 'lineage.json'), lineage = read(lineageFile);
  const expected = {kind: 'continuous_window', source_sha256: parent.sha256, source_start_s: slot.start_s, source_end_s: slot.end_s};
  if (original.provider !== 'official_vision_mcp_in_codex' || repair.provider !== original.provider || original.tool !== 'analyze_video' || repair.tool !== original.tool ||
      path.resolve(repair.arguments.video_source) !== path.resolve(media) || hash(media) !== original.media_sha256 || original.media_sha256 !== repair.media_sha256 ||
      lineage.sha256 !== original.media_sha256 || policy.media_sha256 !== original.media_sha256 ||
      scope(original.observation_scope) !== scope(expected) || scope(repair.observation_scope) !== scope(expected) ||
      scope(lineage) !== scope(expected) || scope(policy.observation_scope) !== scope(expected) || lineage.source_offset_s !== slot.start_s ||
      Math.abs(lineage.media_duration_s - (slot.end_s - slot.start_s)) > .001 || lineage.time_mapping !== 'source_time_s = source_offset_s + proxy_time_s') {
    throw new Error('library_mcp_slot_request_bound_fact_media');
  }
  const stopEntries = state.artifacts.sf_parent_protocol_stop_0, resultEntries = state.artifacts.sf_result_0;
  if (!stopEntries || stopEntries.length !== 1 || !resultEntries || resultEntries.length !== 1) throw new Error('library_mcp_slot_request_bound_fact_stop');
  const stop = read(stopEntries[0].path), resultReceipt = read(resultEntries[0].path), resultFile = path.join(folder, 'result.json');
  if (fileDigest(stopEntries[0].path) !== stopEntries[0].sha256 || stop.original_call_id !== policy.original_call_id ||
      stop.repair_call_id !== policy.repair_call_id || stop.error !== 'model_protocol_repair_exhausted:' + policy.stage ||
      fileDigest(resultEntries[0].path) !== resultEntries[0].sha256 || hash(resultFile) !== resultReceipt.result_sha256 ||
      read(resultFile).status !== 'stopped_protocol_failure') throw new Error('library_mcp_slot_request_bound_fact_stop');
  for (const extra of [outlineFile, lineageFile, media, resultFile, stopEntries[0].path, resultEntries[0].path]) files.push({path: extra, sha256: hash(extra)});
  if (files.length !== policy.protected_files.length || files.some((row, i) => path.resolve(row.path) !== path.resolve(policy.protected_files[i].path) ||
      row.sha256 !== policy.protected_files[i].sha256)) throw new Error('library_mcp_slot_request_bound_fact_history');
  const later = state.calls.slice(stop.request_count).filter(c => stageParent(c.name) === 0);
  if (later.length && later[0].name !== policy.next_stage) throw new Error('library_mcp_slot_request_bound_fact_resume_stage');
  return policy;
}

const POINT_POLICY = 'sf_parent_point_navigation_v1';
const POINT_STAGES = {0: 'sf_0_facts_d106649756615136', 3: 'sf_3_facts_cbdaef3a7197c66f'};
function responseBody(response) {
  let raw = response.result.content.filter(c => c.type === 'text').map(c => c.text).join('\n').trim();
  if (raw.startsWith('```')) {
    const match = /^```(?:json)?\s*\n([\s\S]+?)\n```$/.exec(raw);
    if (!match) throw new Error('library_mcp_slot_point_navigation_reply_changed');
    raw = match[1];
  }
  return {raw, value: JSON.parse(raw)};
}
function sameFiles(actual, expected) {
  return Array.isArray(expected) && actual.length === expected.length && actual.every((row, i) =>
    path.resolve(row.path) === path.resolve(expected[i].path) && row.sha256 === expected[i].sha256);
}
function forbiddenNavigationKeys(value) {
  if (!value || typeof value !== 'object') return false;
  const forbidden = new Set(['source_in_s', 'source_out_s', 'source_start_s', 'source_end_s', 'source_sha256',
    'intended_takeaway', 'required_claims', 'claim_checks', 'plan']);
  return Object.entries(value).some(([key, child]) => !Array.isArray(value) && forbidden.has(key) || forbiddenNavigationKeys(child));
}

export function parentPointNavigation(root, state, grant, parent) {
  const entries = state.artifacts[`${POINT_POLICY}_${parent}`];
  if (!entries) return null;
  if (entries.length !== 1) throw new Error('library_mcp_slot_point_navigation_changed');
  const file = entries[0].path, policy = read(file), count = policy.baseline_request_count;
  if (entries[0].sha256 !== fileDigest(file) || policy.policy !== POINT_POLICY ||
      policy.task_id !== state.task_id || policy.preparation_id !== grant.preparation_id || policy.parent_round !== parent ||
      policy.stage !== POINT_STAGES[parent] || policy.next_stage !== policy.stage.replace('_facts_', '_proposal_') ||
      policy.original_contract_status !== 'failed' || policy.retroactive_fact_pass !== false ||
      policy.old_parsed_created !== false || policy.semantic_truth_established !== false ||
      !Number.isInteger(count) || count < grant.baseline_request_count || count > state.calls.length ||
      fileDigest(path.join(root, 'library_state.json'), value => value.calls.slice(0, count)) !== policy.prefix_calls_sha256 ||
      state.calls.slice(grant.baseline_request_count, count).some(c => c.status !== 'received')) {
    throw new Error('library_mcp_slot_point_navigation_changed');
  }
  const calls = state.calls.slice(grant.baseline_request_count).filter(c => [policy.stage, policy.stage + '_repair'].includes(c.name));
  if (calls.length !== 2 || calls[0].id !== policy.original_call_id || calls[1].id !== policy.repair_call_id ||
      calls[0].name !== policy.stage || calls[1].name !== policy.stage + '_repair' || calls[0].repair_of ||
      calls[1].repair_of !== calls[0].id || calls.some(c => c.status !== 'received')) {
    throw new Error('library_mcp_slot_point_navigation_reply_changed');
  }
  const selected = read(grant.preparation_path).parents.find(p => p.round === parent);
  const folder = path.join(grant.execution_directory, `render_${parent}`), outlineFile = path.join(folder, 'outline.json');
  const outline = read(outlineFile), requests = [], bodies = [], files = [];
  if (!selected || typeof selected.baseline_id !== 'string' || !selected.baseline_id.trim() ||
      !/^[a-f0-9]{64}$/.test(selected.sha256) || !Number.isFinite(selected.duration_s) || selected.duration_s < .001 ||
      outline.baseline_id !== selected.baseline_id || outline.parent_sha256 !== selected.sha256 ||
      !Array.isArray(outline.slots) || !outline.slots.length || outline.slots.length > 32 ||
      !Array.isArray(outline.limitations) || !Array.isArray(outline.uncertainties) ||
      [...outline.limitations, ...outline.uncertainties].some(s => typeof s !== 'string' || !s.trim())) {
    throw new Error('library_mcp_slot_point_navigation_outline');
  }
  let cursor = 0;
  const slotIds = new Set();
  for (const slot of outline.slots) {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(slot.slot_id) || slotIds.has(slot.slot_id) ||
        !Number.isFinite(slot.start_s) || !Number.isFinite(slot.end_s) ||
        !(0 <= slot.start_s && slot.start_s < slot.end_s && slot.end_s <= selected.duration_s + .001) ||
        Math.abs(slot.start_s - cursor) > .001 || ['intended_takeaway', 'entry_state', 'exit_state', 'link_to_previous', 'link_to_next']
          .some(k => typeof slot[k] !== 'string' || !slot[k].trim())) throw new Error('library_mcp_slot_point_navigation_outline');
    slotIds.add(slot.slot_id); cursor = slot.end_s;
  }
  if (Math.abs(cursor - selected.duration_s) > .001) throw new Error('library_mcp_slot_point_navigation_outline');
  const errors = ['slot_finecut/fact:outside_bound_interval', parent === 3 ?
    'slot_finecut:fact_time_binding_changed' : 'slot_finecut/fact:outside_bound_interval'];
  for (const [attempt, call] of calls.entries()) {
    const callFolder = path.join(root, 'calls', call.id), requestFile = path.join(callFolder, 'request.json');
    const responseFile = path.join(callFolder, 'response.json'), failureFile = path.join(callFolder, 'protocol_failure.json');
    const failure = read(failureFile);
    if (fs.existsSync(path.join(callFolder, 'parsed.json')) || fileDigest(requestFile) !== call.request_sha256 ||
        fileDigest(responseFile) !== call.response_sha256 || failure.attempt !== attempt || failure.error !== errors[attempt]) {
      throw new Error('library_mcp_slot_point_navigation_reply_changed');
    }
    requests.push(read(requestFile)); bodies.push(responseBody(read(responseFile)));
    for (const name of ['request.json', 'response.json', 'protocol_failure.json']) {
      const filePath = path.join(callFolder, name); files.push({path: filePath, sha256: hash(filePath)});
    }
  }
  const chosenIndex = parent === 3 ? 1 : 0, chosen = bodies[chosenIndex], original = requests[0], repair = requests[1];
  const slot = outline.slots.find(s => s.slot_id === chosen.value.slot_id);
  const [start, end] = parent === 3 ? [16, 27] : [29, 41], duration = end - start;
  if (!selected || !slot || outline.baseline_id !== selected.baseline_id || outline.parent_sha256 !== selected.sha256 ||
      slot.start_s !== start || slot.end_s !== end || bodies[0].value.slot_start_s !== start || bodies[0].value.slot_end_s !== end ||
      bodies.some(b => b.value.time_domain !== 'slot_local_output') ||
      parent === 3 && (chosen.value.slot_start_s !== 0 || chosen.value.slot_end_s !== duration ||
        digest(bodies[0].value.evidence) !== digest(chosen.value.evidence)) ||
      policy.chosen_call_id !== calls[chosenIndex].id || jsonDigest(chosen.raw) !== policy.model_body_sha256 ||
      fileDigest(file, value => value.envelope.model_report) !== jsonDigest(chosen.raw)) {
    throw new Error('library_mcp_slot_point_navigation_body_changed');
  }
  const prompt = original.arguments.prompt, at = prompt.lastIndexOf('\n{');
  if (at < 0 || !prompt.includes('所有start_s/end_s是这个提供片段的局部输出秒数，从0开始')) {
    throw new Error('library_mcp_slot_point_navigation_prompt');
  }
  const context = JSON.parse(prompt.slice(at + 1)), contract = context.response_contract;
  if (contract.time_domain !== 'slot_local_output' || ['baseline_id', 'parent_sha256', 'slot_id', 'slot_start_s', 'slot_end_s']
      .some(k => context[k] !== contract[k] || contract[k] !== bodies[0].value[k])) {
    throw new Error('library_mcp_slot_point_navigation_prompt');
  }
  const media = original.arguments.video_source, lineageFile = path.join(path.dirname(media), 'lineage.json'), lineage = read(lineageFile);
  const expected = {kind: 'continuous_window', source_sha256: selected.sha256, source_start_s: start, source_end_s: end};
  if (original.provider !== 'official_vision_mcp_in_codex' || repair.provider !== original.provider || original.tool !== 'analyze_video' ||
      repair.tool !== original.tool || path.resolve(repair.arguments.video_source) !== path.resolve(media) ||
      hash(media) !== original.media_sha256 || original.media_sha256 !== repair.media_sha256 || lineage.sha256 !== original.media_sha256 ||
      policy.media_sha256 !== original.media_sha256 || scope(original.observation_scope) !== scope(expected) ||
      scope(repair.observation_scope) !== scope(expected) || scope(lineage) !== scope(expected) ||
      scope(policy.observation_scope) !== scope(expected) || lineage.source_offset_s !== start ||
      Math.abs(lineage.media_duration_s - duration) > .001 || lineage.time_mapping !== 'source_time_s = source_offset_s + proxy_time_s') {
    throw new Error('library_mcp_slot_point_navigation_media');
  }
  const envelope = policy.envelope, binding = envelope.request_binding;
  if (envelope.schema_version !== POINT_POLICY || binding.baseline_id !== selected.baseline_id || binding.parent_sha256 !== selected.sha256 ||
      binding.slot_id !== slot.slot_id || binding.parent_slot_start_s !== start || binding.parent_slot_end_s !== end ||
      binding.local_start_s !== 0 || binding.local_end_s !== duration || binding.time_domain !== 'slot_local_output' ||
      binding.media_sha256 !== original.media_sha256 || scope(binding.observation_scope) !== scope(expected) ||
      chosen.value.baseline_id !== selected.baseline_id || chosen.value.parent_sha256 !== selected.sha256 ||
      chosen.value.slot_id !== slot.slot_id || ![[0, duration], [start, end]].some(([a, b]) =>
        chosen.value.slot_start_s === a && chosen.value.slot_end_s === b) ||
      !Array.isArray(chosen.value.limitations) || !Array.isArray(chosen.value.uncertainties) ||
      [...chosen.value.limitations, ...chosen.value.uncertainties].some(s => typeof s !== 'string' || !s.trim()) ||
      forbiddenNavigationKeys(chosen.value)) throw new Error('library_mcp_slot_point_navigation_binding');
  const ids = new Set();
  if (!Array.isArray(chosen.value.evidence)) throw new Error('library_mcp_slot_point_navigation_contract');
  for (const e of chosen.value.evidence) {
    if (typeof e.evidence_id !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(e.evidence_id) || ids.has(e.evidence_id) || !Number.isFinite(e.start_s) ||
        !Number.isFinite(e.end_s) || !(0 <= e.start_s && e.start_s <= e.end_s && e.end_s <= duration) ||
        typeof e.observed_fact !== 'string' || !e.observed_fact.trim() ||
        !['action', 'state', 'identity', 'result', 'reaction', 'shape', 'inference'].includes(e.kind) ||
        !['visual', 'visible_text', 'inference'].includes(e.basis) || e.kind === 'inference' && e.basis === 'visual') {
      throw new Error('library_mcp_slot_point_navigation_contract');
    }
    ids.add(e.evidence_id);
  }
  const support = chosen.value.evidence.map(e => ({evidence_id: e.evidence_id, start_s: e.start_s, end_s: e.end_s,
    temporal_kind: e.start_s === e.end_s ? 'point' : 'interval', instant_only: e.start_s === e.end_s,
    duration_unknown: e.start_s === e.end_s, cannot_prove_completed_action: e.start_s === e.end_s, exposure_proof: false}));
  const disagreements = [...new Set([...Object.keys(bodies[0].value), ...Object.keys(bodies[1].value)])].sort()
    .filter(k => digest(bodies[0].value[k] ?? null) !== digest(bodies[1].value[k] ?? null))
    .map(k => ({field: k, original_present: Object.hasOwn(bodies[0].value, k), repair_present: Object.hasOwn(bodies[1].value, k),
      original_value: bodies[0].value[k] ?? null, repair_value: bodies[1].value[k] ?? null}));
  if (!support.some(e => e.instant_only) || digest(envelope.temporal_support) !== digest(support) ||
      digest(envelope.unresolved_model_disagreements) !== digest(disagreements)) {
    throw new Error('library_mcp_slot_point_navigation_support_changed');
  }
  const stopKey = parent === 0 ? 'sf_parent_protocol_stop_0_metadata_bound_resume' : 'sf_parent_protocol_stop_3';
  const resultKey = parent === 0 ? 'sf_result_0_metadata_bound_resume' : 'sf_result_3';
  const stopEntries = state.artifacts[stopKey], resultEntries = state.artifacts[resultKey];
  if (!stopEntries || stopEntries.length !== 1 || !resultEntries || resultEntries.length !== 1) {
    throw new Error('library_mcp_slot_point_navigation_stop');
  }
  const stop = read(stopEntries[0].path), receipt = read(resultEntries[0].path);
  const resultFile = path.join(folder, parent === 0 ? 'result_metadata_bound_resume.json' : 'result.json');
  const result = read(resultFile);
  if (fileDigest(stopEntries[0].path) !== stopEntries[0].sha256 || stop.original_call_id !== calls[0].id ||
      stop.repair_call_id !== calls[1].id || stop.error !== 'model_protocol_repair_exhausted:' + policy.stage ||
      fileDigest(resultEntries[0].path) !== resultEntries[0].sha256 || hash(resultFile) !== receipt.result_sha256 ||
      result.status !== 'stopped_protocol_failure' || result.baseline_id !== selected.baseline_id ||
      result.parent_sha256 !== selected.sha256 || digest(result.stop_receipt) !== digest(stop)) {
    throw new Error('library_mcp_slot_point_navigation_stop');
  }
  for (const extra of [outlineFile, lineageFile, media, resultFile, stopEntries[0].path, resultEntries[0].path]) {
    files.push({path: extra, sha256: hash(extra)});
  }
  if (parent === 0) {
    const old = state.artifacts.sf_parent_protocol_stop_0, oldResult = state.artifacts.sf_result_0;
    const oldResultFile = path.join(folder, 'result.json');
    if (!old || old.length !== 1 || fileDigest(old[0].path) !== old[0].sha256 ||
        !oldResult || oldResult.length !== 1 || fileDigest(oldResult[0].path) !== oldResult[0].sha256 ||
        read(oldResultFile).status !== 'stopped_protocol_failure' || hash(oldResultFile) !== read(oldResult[0].path).result_sha256) {
      throw new Error('library_mcp_slot_point_navigation_old_stop');
    }
    const parallelStops = fs.readdirSync(grant.execution_directory).filter(name => /^parallel_stop_.*\.json$/.test(name))
      .map(name => path.join(grant.execution_directory, name)).filter(filePath => {
        const value = read(filePath);
        return value.request_count === 153 && value.errors?.some(e => e.baseline_id === 'render_0' && e.error === 'slot_finecut:parent_stop_error_changed');
      });
    if (parallelStops.length !== 1) throw new Error('library_mcp_slot_point_navigation_parallel_stop');
    for (const extra of [old[0].path, oldResultFile, oldResult[0].path, parallelStops[0]]) files.push({path: extra, sha256: hash(extra)});
  }
  if (!sameFiles(files, policy.protected_files)) throw new Error('library_mcp_slot_point_navigation_history');
  const later = state.calls.slice(count).filter(c => stageParent(c.name) === parent);
  if (later.length && later[0].name !== policy.next_stage) throw new Error('library_mcp_slot_point_navigation_resume_stage');
  return policy;
}

export function requestBoundAssembly(root, state, grant) {
  return validateRequestBoundAssembly(root, state, grant,
    {read, fileDigest, jsonDigest, hash, scope, stageParent, responseBody, sameFiles, digest});
}

export function parentProtocolStops(root, state, grant, {parallel = null, boundFacts = null, pointNavigation = {},
  boundAssembly = null, boundProposalRetiming = null, sourceFeedback = null} = {}) {
  const stops = {};
  for (const parent of [0, 3]) {
    for (const suffix of ['', ...(parent === 0 ? ['_metadata_bound_resume'] : []), '_point_navigation_resume',
      ...(parent === 0 ? ['_timing_bound_resume'] : []),
      ...(parent === 3 ? ['_assembly_bound_resume', '_source_feedback_resume'] : []), '_independent_resume']) {
      const entries = state.artifacts[`sf_parent_protocol_stop_${parent}${suffix}`];
      if (!entries) continue;
      if (entries.length !== 1) throw new Error('library_mcp_slot_parent_stop_changed');
      const receipt = read(entries[0].path), count = receipt.request_count;
      if (entries[0].sha256 !== fileDigest(entries[0].path) || receipt.policy !== 'slot_finecut_independent_parent_after_known_protocol_stop_v1' ||
          receipt.preparation_id !== grant.preparation_id || receipt.parent_round !== parent || receipt.independent_parent_round !== (parent === 0 ? 3 : 0) ||
          !Number.isInteger(count) || count < grant.baseline_request_count + 2 || count > state.calls.length) {
        throw new Error('library_mcp_slot_parent_stop_changed');
      }
      const own = state.calls.slice(grant.baseline_request_count, count).filter(c => stageParent(c.name) === parent);
      const [original, repair] = own.slice(-2);
      if (!original || !repair || original.id !== receipt.original_call_id || repair.id !== receipt.repair_call_id || original.repair_of ||
          repair.name !== original.name + '_repair' || repair.repair_of !== original.id || original.status !== 'received' ||
          repair.status !== 'received' || receipt.error !== 'model_protocol_repair_exhausted:' + original.name) {
        throw new Error('library_mcp_slot_parent_stop_changed');
      }
      const files = [];
      for (const [attempt, call] of [original, repair].entries()) {
        const folder = path.join(root, 'calls', call.id);
        if (fs.existsSync(path.join(folder, 'parsed.json')) || read(path.join(folder, 'protocol_failure.json')).attempt !== attempt ||
            fileDigest(path.join(folder, 'request.json')) !== call.request_sha256 ||
            fileDigest(path.join(folder, 'response.json')) !== call.response_sha256) throw new Error('library_mcp_slot_parent_stop_reply_changed');
        for (const name of ['request.json', 'response.json', 'protocol_failure.json']) {
          const filePath = path.join(folder, name); files.push({path: filePath, sha256: hash(filePath)});
        }
      }
      if (!sameFiles(files, receipt.files)) throw new Error('library_mcp_slot_parent_stop_history');
      const later = state.calls.slice(count).filter(c => stageParent(c.name) === parent), point = pointNavigation[parent];
      const assemblyAllowed = parent === 3 && boundAssembly && boundAssembly.repair_call_id === repair.id &&
        later[0]?.name === (sourceFeedback ? sourceFeedback.stage : boundAssembly.next_stage);
      const timingAllowed = parent === 0 && boundProposalRetiming && boundProposalRetiming.repair_call_id === repair.id && later[0]?.name === boundProposalRetiming.next_stage;
      const allowed = ['_assembly_bound_resume', '_timing_bound_resume', '_source_feedback_resume', '_independent_resume'].includes(suffix) ? false : suffix === '_point_navigation_resume' ?
        assemblyAllowed || timingAllowed : suffix === '_metadata_bound_resume' ?
        point && point.repair_call_id === repair.id && later[0]?.name === point.next_stage :
        boundFacts && parent === 0 && boundFacts.repair_call_id === repair.id && later[0]?.name === boundFacts.next_stage ||
        point && point.repair_call_id === repair.id && later[0]?.name === point.next_stage ||
        parallel && parent === 0 && original.name === 'sf_0_outline' && later[0]?.name === 'sf_0_outline_v2';
      if (later.length && !allowed) throw new Error('library_mcp_slot_stopped_parent_cannot_repeat');
      stops[parent] = receipt;
    }
  }
  return stops;
}

export function slotRequestLimit(root, currentJob) {
  const file = process.env.OMNI_LIBRARY_SLOT_FINECUT_AUTH_FILE;
  if (!file) return null;
  if (hash(file) !== process.env.OMNI_LIBRARY_SLOT_FINECUT_AUTH_SHA256) {
    throw new Error('library_mcp_slot_authorization_modified');
  }
  const grant = read(file), state = read(path.join(root, 'library_state.json'));
  const parallel = loadParallel(root);
  const boundFacts = requestBoundParentFacts(root, state, grant);
  const pointNavigation = Object.fromEntries([0, 3].map(parent => [parent, parentPointNavigation(root, state, grant, parent)]));
  const boundAssembly = requestBoundAssembly(root, state, grant);
  const boundProposalRetiming = readValidateProposalRetiming(root, state, grant);
  const sourceFeedback = validateSourceFeedback(root, state, grant, {read, fileDigest, hash, scope});
  const recovery = validateIndependentSlotRecovery(root, state, grant, {read, fileDigest, hash, scope});
  parentProtocolStops(root, state, grant, {parallel, boundFacts, pointNavigation, boundAssembly, boundProposalRetiming, sourceFeedback});
  const entries = state.artifacts.slot_finecut_comparison_v1;
  const call = state.calls.find(c => c.id === currentJob.job_id);
  const baseline = read(grant.baseline_calls_path);
  if (grant.policy !== 'slot_finecut_comparison_v1' || grant.task_id !== state.task_id ||
      path.resolve(grant.original_output) !== path.resolve(root) ||
      grant.request_limit_policy !== 'progress_guard_no_numeric_request_cap_v1' ||
      grant.base_request_limit !== state.max_requests ||
      fileDigest(path.join(root, 'library_state.json'), value => value.input_lock) !== grant.input_lock_sha256 ||
      !entries || entries.length !== 1 || path.resolve(entries[0].path) !== path.resolve(file) ||
      entries[0].sha256 !== fileDigest(file) || hash(grant.baseline_calls_path) !== grant.baseline_calls_sha256 ||
      !Number.isInteger(grant.baseline_request_count) || grant.baseline_request_count !== baseline.length ||
      fileDigest(path.join(root, 'library_state.json'), value => value.calls.slice(0, baseline.length)) !== grant.prefix_calls_sha256 ||
      fileDigest(grant.baseline_calls_path) !== grant.prefix_calls_sha256 || state.request_count !== state.calls.length ||
      !call || call.status !== 'submitted' || (!parallel && state.calls.at(-1) !== call) ||
      state.calls.indexOf(call) < baseline.length ||
      !(STAGES.test(call.name) && new RegExp(grant.allowed_stage_pattern).test(call.name) || parallel && REPLACEMENT.test(call.name) ||
        sourceFeedback && SOURCE_FEEDBACK_PATTERN.test(call.name) || recovery && LOCAL_REPLAN_PATTERN.test(call.name))) {
    throw new Error('library_mcp_slot_budget_or_stage_blocked');
  }
  const added = state.calls.slice(baseline.length), own = added.filter(c => stageParent(c.name) === stageParent(call.name));
  const pending = added.filter(c => c.status === 'submitted');
  if (parallel && (own.at(-1) !== call || pending.length > 2 || new Set(pending.map(c => stageParent(c.name))).size !== pending.length) ||
      added.some(c => c !== call &&
      (!independentRecoveryAdmissibleStatus(recovery, c) ||
       c.status === 'submitted' && (!parallel || stageParent(c.name) === stageParent(call.name))))) {
    throw new Error('library_mcp_slot_new_unsettled_call');
  }
  if (hash(grant.preparation_path) !== grant.preparation_sha256 ||
      hash(grant.reference_methods_path) !== grant.reference_methods_sha256 ||
      hash(grant.knowledge_path) !== grant.knowledge_sha256) {
    throw new Error('library_mcp_slot_preparation_changed');
  }
  for (const row of grant.protected_files) {
    if (hash(row.path) !== row.sha256) throw new Error('library_mcp_slot_history_changed');
  }
  const request = read(path.join(root, 'calls', call.id, 'request.json'));
  if (fileDigest(path.join(root, 'calls', call.id, 'request.json')) !== call.request_sha256 || request.provider !== 'official_vision_mcp_in_codex' ||
      !['analyze_image', 'analyze_video'].includes(request.tool)) throw new Error('library_mcp_slot_request_changed');
  const newScope = scope(request.observation_scope);
  checkIndependentSlotRecoveryInput(recovery, call, request, {scope, requestDigest: call.request_sha256});
  validateSourceFeedbackRequest(sourceFeedback, call, request, {scope});
  checkProposalRetimingInput(boundProposalRetiming, call.name, request);
  if (boundAssembly && /^sf_3_assemble(?:_repair)?$/.test(call.name)) {
    throw new Error('library_mcp_slot_request_bound_assembly_no_third_assembly');
  }
  if (boundAssembly && /^(?:semantic_(?:slice|claims)_21_|sf_3_(?:blind|economy|review))/.test(call.name)) {
    // The complete known assembly remains the predecessor; a source request
    // uses different movie media and is checked by the existing no-replay rules.
    if (!boundAssembly.body?.plan) throw new Error('library_mcp_slot_request_bound_assembly_predecessor');
  }
  if (boundFacts && /^(?:sf_0_facts_|semantic_slice_20_)/.test(call.name) &&
      (scope(boundFacts.observation_scope) === newScope || request.media_sha256 === boundFacts.media_sha256)) {
    throw new Error('library_mcp_slot_request_bound_fact_no_third_observation');
  }
  if (/^(?:sf_(?:0|3)_facts_|semantic_slice_(?:20|21)_)/.test(call.name) && Object.values(pointNavigation).some(point =>
      point && (scope(point.observation_scope) === newScope || request.media_sha256 === point.media_sha256))) {
    throw new Error('library_mcp_slot_point_navigation_no_third_observation');
  }
  for (const id of grant.admitted_unknown_call_ids) {
    const prior = state.calls.find(c => c.id === id);
    if (!prior || prior.status !== 'uncertain') throw new Error('library_mcp_slot_unknown_baseline_changed');
    const original = read(path.join(root, 'calls', prior.id, 'request.json'));
    const media = original.arguments.image_source || original.arguments.video_source;
    const lineage = original.observation_scope || read(path.join(path.dirname(media), 'lineage.json'));
    if (fileDigest(path.join(root, 'calls', prior.id, 'request.json')) !== prior.request_sha256 || original.media_sha256 === request.media_sha256 ||
        prior.request_sha256 === call.request_sha256 || scope(lineage) === newScope) {
      throw new Error('library_mcp_slot_unknown_input_replay');
    }
  }
  const stem = call.name.replace(/_repair$/, '');
  if (!call.name.endsWith('_repair') &&
      grant.exhausted_source_inputs.some(row => scope(row.scope) === newScope)) {
    throw new Error('library_mcp_slot_exhausted_source_replay');
  }
  if (call.name.endsWith('_repair')) {
    const parent = state.calls.find(c => c.id === call.repair_of);
    if (!parent || parent.name !== stem || parent.status !== 'received' || parent.repair_of ||
        (parallel ? own.at(-2) : state.calls.at(-2)) !== parent || state.calls.filter(c => c.repair_of === parent.id).length !== 1) {
      throw new Error('library_mcp_slot_repair_parent_invalid');
    }
    const original = read(path.join(root, 'calls', parent.id, 'request.json'));
    if (original.media_sha256 !== request.media_sha256 || scope(original.observation_scope) !== newScope) {
      throw new Error('library_mcp_slot_repair_input_changed');
    }
  } else if (call.repair_of || state.calls.some(c => c !== call && c.name === stem)) {
    throw new Error('library_mcp_slot_stage_repetition');
  }
  return Infinity;
}
