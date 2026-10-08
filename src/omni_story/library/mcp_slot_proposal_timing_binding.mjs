// Read-only mirror of the one known preliminary-proposal timing binding.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {parentPointNavigation} from './mcp_slot_finecut_guard.mjs';

export const PROPOSAL_RETIMING_POLICY = 'sf_request_bound_proposal_retiming_v1';
export const PROPOSAL_RETIMING_ARTIFACT = PROPOSAL_RETIMING_POLICY + '_0';
const STAGE = 'sf_0_proposal_cf0cecff65df19b1';
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const requireBound = (ok, reason) => { if (!ok) throw new Error('library_mcp_slot_proposal_retiming_' + reason); };
class NumericLiteral { constructor(token) { this.token = token; } }
function jsonDigest(raw, select = value => value) {
  const parsed = JSON.parse(raw, (key, value, context) => {
    if (typeof value !== 'number') return value;
    requireBound(Boolean(context?.source), 'numeric_lexemes_unavailable');
    return new NumericLiteral(context.source);
  });
  function serialize(value) {
    if (value instanceof NumericLiteral) return value.token;
    if (Array.isArray(value)) return '[' + value.map(serialize).join(',') + ']';
    if (value && typeof value === 'object') return '{' + Object.keys(value).sort()
      .map(k => JSON.stringify(k) + ':' + serialize(value[k])).join(',') + '}';
    return JSON.stringify(value);
  }
  return crypto.createHash('sha256').update(serialize(select(parsed))).digest('hex');
}
const fileDigest = (file, select) => jsonDigest(fs.readFileSync(file, 'utf8'), select);
const sameFiles = (a, b) => Array.isArray(b) && a.length === b.length && a.every((r, i) =>
  path.resolve(r.path) === path.resolve(b[i].path) && r.sha256 === b[i].sha256);
const sameScope = (a, b) => equal(['kind', 'source_sha256', 'source_start_s', 'source_end_s'].map(k => a?.[k]),
  ['kind', 'source_sha256', 'source_start_s', 'source_end_s'].map(k => b?.[k]));
function artifact(state, key) {
  const entries = state.artifacts[key];
  requireBound(entries?.length === 1, 'one_' + key + '_required');
  requireBound(fileDigest(entries[0].path) === entries[0].sha256, 'artifact_changed');
  return {file: entries[0].path, value: read(entries[0].path)};
}
function originalContext(prompt) {
  const values = [];
  // Decode complete JSON objects embedded after newlines; appended diagnostics
  // must never hide the original creative request object.
  for (let i = 0; i < prompt.length; i++) {
    if (prompt.slice(i, i + 2) !== '\n{') continue;
    let braces = 0, quoted = false, escaped = false;
    for (let j = i + 1; j < prompt.length; j++) {
      const c = prompt[j];
      if (quoted) { if (escaped) escaped = false; else if (c === '\\') escaped = true; else if (c === '"') quoted = false; }
      else if (c === '"') quoted = true;
      else if (c === '{') braces++;
      else if (c === '}' && --braces === 0) {
        const value = JSON.parse(prompt.slice(i + 1, j + 1));
        if (value.parent && value.slot && value.parent_output_facts && value.watched_windows) values.push(value);
        break;
      }
    }
  }
  requireBound(values.length === 1, 'one_original_context_required');
  return values[0];
}
function proposalChecks(body, selected, slot, navigation, windows) {
  requireBound(body.baseline_id === selected.baseline_id && body.parent_sha256 === selected.sha256 && body.slot_id === slot.slot_id,
    'body_parent_binding');
  requireBound(Array.isArray(body.candidates) && body.candidates.length > 0 && body.candidates.length <= 3 &&
    new Set(body.candidates.map(c => c.candidate_id)).size === body.candidates.length, 'candidate_ids');
  const evidence = new Set(navigation.model_report.evidence.map(e => e.evidence_id)), shortfalls = [];
  const refs = values => Array.isArray(values) && values.length > 0 && new Set(values).size === values.length && values.every(v => evidence.has(v));
  for (const candidate of body.candidates) {
    requireBound(['preserved', 'unresolved'].includes(candidate.meaning_status) && candidate.rationale?.trim() &&
      Array.isArray(candidate.limitations) && candidate.limitations.every(v => typeof v === 'string' && v.trim()) &&
      (candidate.meaning_status !== 'unresolved' || candidate.limitations.length > 0) &&
      Array.isArray(candidate.operations) && candidate.operations.length > 0, 'candidate_contract');
    candidate.operations.forEach((op, operationIndex) => {
      const source = selected.provenance[op.parent_segment_index], w = windows.find(w => w.window_id === source?.window_id);
      requireBound(Number.isInteger(op.parent_segment_index) && source && w && w.source_id === source.source_id &&
        w.source_sha256 === source.source_sha256 && source.output_in_s < slot.end_s && slot.start_s < source.output_out_s &&
        Number.isFinite(op.source_in_s) && Number.isFinite(op.source_out_s) && op.source_in_s < op.source_out_s &&
        w.source_start_s <= op.source_in_s && op.source_out_s <= w.source_end_s &&
        w.observation.usable_ranges.some(r => w.source_start_s + r.local_in_s - 1e-6 <= op.source_in_s &&
          op.source_out_s <= w.source_start_s + r.local_out_s + 1e-6 && source.role_ids.every(id => r.role_ids.includes(id))), 'range_or_role');
      requireBound(Number.isFinite(op.speed) && .5 <= op.speed && op.speed <= 2 && Number.isFinite(op.freeze_tail_s) &&
        0 <= op.freeze_tail_s && op.freeze_tail_s <= 10 && op.reason?.trim() && refs(op.evidence_ids) &&
        Array.isArray(op.essential_intervals) && op.essential_intervals.length > 0, 'operation_contract');
      op.essential_intervals.forEach((essential, essentialIndex) => {
        const a = essential.source_start_s, b = essential.source_end_s, tail = essential.continues_in_tail_frame ?? false;
        requireBound(Number.isFinite(a) && Number.isFinite(b) && op.source_in_s <= a && a < b && b <= op.source_out_s &&
          Number.isFinite(essential.min_readable_s) && essential.min_readable_s >= .001 && essential.information?.trim() &&
          refs(essential.evidence_ids) && typeof tail === 'boolean' && (!tail || Math.abs(b - op.source_out_s) <= 1e-6), 'essential_contract');
        const exposure = (b - a) / op.speed + (tail ? op.freeze_tail_s : 0);
        if (candidate.meaning_status === 'preserved' && exposure + 1e-6 < essential.min_readable_s) shortfalls.push({
          candidate_id: candidate.candidate_id, operation_index: operationIndex, essential_interval_index: essentialIndex,
          source_interval_duration_s: b - a, speed: op.speed, hold_s: op.freeze_tail_s, continues_in_tail_frame: tail,
          exposure_s: exposure, model_min_readable_s: essential.min_readable_s});
      });
    });
  }
  requireBound(equal(shortfalls.map(r => [r.candidate_id, r.operation_index, r.essential_interval_index]),
    [['c1', 3, 0], ['c2', 4, 0], ['c3', 2, 0]]), 'shortfall_scope_changed');
  return shortfalls;
}

export function readValidateProposalRetiming(root, state, grant) {
  const entries = state.artifacts[PROPOSAL_RETIMING_ARTIFACT];
  if (!entries) return null;
  const {file, value: policy} = artifact(state, PROPOSAL_RETIMING_ARTIFACT), count = policy.baseline_request_count;
  requireBound(policy.policy === PROPOSAL_RETIMING_POLICY && policy.task_id === state.task_id &&
    policy.preparation_id === grant.preparation_id && policy.parent_round === 0 && policy.stage === STAGE &&
    policy.next_stage === 'sf_0_assemble' && policy.original_contract_status === 'failed' &&
    policy.retroactive_proposal_pass === false && policy.old_parsed_created === false && policy.semantic_truth_established === false &&
    equal(policy.program_transform_changes, []) && equal(policy.allowed_model_changes, ['speed', 'freeze_tail_s']), 'policy_changed');
  requireBound(Number.isInteger(count) && grant.baseline_request_count <= count && count <= state.calls.length &&
    fileDigest(path.join(root, 'library_state.json'), d => d.calls.slice(0, count)) === policy.prefix_calls_sha256 &&
    state.calls.slice(grant.baseline_request_count, count).every(c => c.status === 'received'), 'settled_prefix_changed');
  const calls = state.calls.slice(grant.baseline_request_count).filter(c => [STAGE, STAGE + '_repair'].includes(c.name));
  requireBound(calls.length === 2 && calls[0].name === STAGE && calls[1].name === STAGE + '_repair' && !calls[0].repair_of &&
    calls[1].repair_of === calls[0].id && calls.every(c => c.status === 'received') && policy.original_call_id === calls[0].id &&
    policy.repair_call_id === calls[1].id && policy.chosen_call_id === calls[1].id, 'known_sole_repair_required');
  const selected = read(grant.preparation_path).parents.find(p => p.round === 0), folder = path.join(grant.execution_directory, 'render_0');
  const outlinePath = path.join(folder, 'outline.json'), outline = read(outlinePath), files = [], requests = [], bodies = [], failures = [], rawBodies = [];
  for (const [attempt, call] of calls.entries()) {
    const callFolder = path.join(root, 'calls', call.id), requestFile = path.join(callFolder, 'request.json'), responseFile = path.join(callFolder, 'response.json');
    const failure = read(path.join(callFolder, 'protocol_failure.json'));
    requireBound(!fs.existsSync(path.join(callFolder, 'parsed.json')) && fileDigest(requestFile) === call.request_sha256 &&
      fileDigest(responseFile) === call.response_sha256 && failure.attempt === attempt, 'reply_binding_changed');
    const response = read(responseFile), raw = response.result.content.filter(c => c.type === 'text').map(c => c.text).join('\n'), body = JSON.parse(raw);
    requireBound(equal(body, JSON.parse(failure.model_text)), 'failure_body_changed');
    requests.push(read(requestFile)); bodies.push(body); failures.push(failure); rawBodies.push(raw);
    for (const name of ['request.json', 'response.json', 'protocol_failure.json']) { const p = path.join(callFolder, name); files.push({path: p, sha256: hash(p)}); }
  }
  const [original, repair] = requests, body = bodies[1], context = originalContext(original.arguments.prompt), slot = outline.slots.find(s => s.slot_id === body.slot_id);
  requireBound(outline.baseline_id === selected.baseline_id && outline.parent_sha256 === selected.sha256 &&
    slot && equal(slot, outline.slots.at(-1)) && equal(slot, context.slot) &&
    equal(context.parent, Object.fromEntries(Object.keys(context.parent).map(k => [k, selected[k]]))) &&
    context.response_contract.baseline_id === selected.baseline_id && context.response_contract.parent_sha256 === selected.sha256 &&
    context.response_contract.slot_id === body.slot_id, 'parent_slot_binding_changed');
  const navigationPath = path.join(folder, 'slots', STAGE.split('_').at(-1), 'navigation.json'), navigation = read(navigationPath);
  requireBound(equal(navigation, context.parent_navigation) && equal(navigation.model_report, context.parent_output_facts) &&
    navigation.request_binding.baseline_id === selected.baseline_id && navigation.request_binding.parent_sha256 === selected.sha256 &&
    navigation.request_binding.slot_id === slot.slot_id && navigation.request_binding.parent_slot_start_s === slot.start_s &&
    navigation.request_binding.parent_slot_end_s === slot.end_s && navigation.request_binding.local_start_s === 0 &&
    navigation.request_binding.local_end_s === slot.end_s - slot.start_s, 'navigation_changed');
  const windowsPath = path.join(root, 'watched_windows.json'), allWindows = read(windowsPath), windows = context.watched_windows;
  requireBound(windows.length > 0 && new Set(windows.map(w => w.window_id)).size === windows.length && windows.every(w => {
    const old = allWindows.find(o => o.window_id === w.window_id); return old && equal(w, Object.fromEntries(Object.keys(w).map(k => [k, old[k]])));
  }), 'selected_windows_changed');
  const media = fs.realpathSync(original.arguments.video_source), lineagePath = path.join(path.dirname(media), 'lineage.json'), lineage = read(lineagePath), scope = original.observation_scope;
  requireBound(original.provider === 'official_vision_mcp_in_codex' && repair.provider === original.provider && original.tool === 'analyze_video' &&
    repair.tool === original.tool && fs.realpathSync(repair.arguments.video_source) === media && hash(media) === original.media_sha256 &&
    repair.media_sha256 === original.media_sha256 && lineage.sha256 === original.media_sha256 && sameScope(scope, repair.observation_scope) &&
    sameScope(scope, lineage) && scope.source_sha256 === selected.sha256 && scope.source_start_s <= slot.start_s && scope.source_end_s >= slot.end_s &&
    lineage.source_offset_s === scope.source_start_s && Math.abs(lineage.media_duration_s - (scope.source_end_s - scope.source_start_s)) <= .001 &&
    lineage.time_mapping === 'source_time_s = source_offset_s + proxy_time_s' && policy.media_sha256 === original.media_sha256 &&
    sameScope(policy.observation_scope, scope), 'media_changed');
  requireBound(failures[0].error === 'slot_finecut:operation_not_in_one_compatible_usable_range' &&
    failures[1].error.startsWith('slot_finecut:declared_readability_shortfall:'), 'old_failure_changed');
  const shortfalls = proposalChecks(body, selected, slot, navigation, windows), recorded = JSON.parse(failures[1].error.split(':').slice(2).join(':')).mechanical_shortfalls;
  requireBound(equal(shortfalls, recorded) && equal(policy.mechanical_shortfalls, shortfalls) && policy.slot_id === slot.slot_id &&
    equal(policy.retiming_operations, shortfalls.map(r => ({slot_id: slot.slot_id, candidate_id: r.candidate_id, operation_index: r.operation_index}))) &&
    equal(policy.body, body) && policy.model_body_sha256 === jsonDigest(rawBodies[1]) &&
    fileDigest(file, d => d.body) === policy.model_body_sha256, 'body_or_shortfalls_changed');
  requireBound(Boolean(parentPointNavigation(root, state, grant, 0)), 'prior_point_navigation_required');
  const point = artifact(state, 'sf_parent_point_navigation_v1_0'), forward = artifact(state, 'sf_forward_parent_navigation_contract_v1'), fc = forward.value.baseline_request_count;
  requireBound(forward.value.policy === 'sf_forward_parent_navigation_contract_v1' && forward.value.task_id === state.task_id &&
    Number.isInteger(fc) && grant.baseline_request_count <= fc && fc <= state.calls.length &&
    fileDigest(path.join(root, 'library_state.json'), d => d.calls.slice(0, fc)) === forward.value.prefix_calls_sha256 &&
    fileDigest(forward.file, d => d.contract) === forward.value.contract_sha256, 'forward_contract_changed');
  const stop = artifact(state, 'sf_parent_protocol_stop_0_point_navigation_resume'), receipt = artifact(state, 'sf_result_0_point_navigation_resume');
  const resultPath = path.join(folder, 'result_point_navigation_resume.json'), result = read(resultPath);
  requireBound(stop.value.parent_round === 0 && stop.value.preparation_id === grant.preparation_id && stop.value.original_call_id === calls[0].id &&
    stop.value.repair_call_id === calls[1].id && stop.value.error === 'model_protocol_repair_exhausted:' + STAGE && sameFiles(files, stop.value.files) &&
    path.resolve(receipt.value.result_path) === path.resolve(resultPath) && hash(resultPath) === receipt.value.result_sha256 &&
    equal(result.stop_receipt, stop.value) && result.status === 'stopped_protocol_failure' && result.baseline_id === selected.baseline_id &&
    result.parent_sha256 === selected.sha256, 'stop_or_result_changed');
  for (const p of [grant.preparation_path, outlinePath, navigationPath, windowsPath, lineagePath, media, point.file, forward.file, stop.file, receipt.file, resultPath]) files.push({path: p, sha256: hash(p)});
  requireBound(sameFiles(files, policy.protected_files), 'protected_files_changed');
  const later = state.calls.slice(count).filter(c => c.name.startsWith('sf_0_') || c.name.startsWith('semantic_') && c.name.includes('_20_'));
  requireBound(!later.length || later[0].name === policy.next_stage, 'first_resume_stage_changed');
  return policy;
}

export function proposalRetimingBodyForStage(root, state, grant, stage, mediaSha = null) {
  const policy = readValidateProposalRetiming(root, state, grant);
  if (!policy || stage !== policy.stage) return null;
  requireBound(mediaSha === null || mediaSha === policy.media_sha256, 'current_media_changed');
  return structuredClone(policy.body);
}

export function checkProposalRetimingInput(policy, stage, request) {
  if (!policy) return;
  requireBound(![policy.stage, policy.stage + '_repair'].includes(stage), 'no_third_proposal');
  if (stage.includes('_facts_') || stage.startsWith('semantic_slice_')) requireBound(request.media_sha256 !== policy.media_sha256 &&
    !sameScope(request.observation_scope, policy.observation_scope), 'no_third_observation');
}
