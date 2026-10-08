// Read-only mirror of the separate known-assembly request binding.
import fs from 'node:fs';
import path from 'node:path';
import {validateSourceFeedback} from './mcp_slot_source_feedback.mjs';

const POLICY = 'sf_request_bound_assembly_v1_3';
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fail = name => { throw new Error('library_mcp_slot_request_bound_assembly_' + name); };
function contexts(prompt) {
  const result = [];
  for (let start = prompt.indexOf('\n{'); start >= 0; start = prompt.indexOf('\n{', start + 2)) {
    let depth = 0, quoted = false, escaped = false, end = start + 1;
    for (; end < prompt.length; end++) {
      const char = prompt[end];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === '{' || char === '[') depth++;
      else if ((char === '}' || char === ']') && --depth === 0) break;
    }
    try {
      const value = JSON.parse(prompt.slice(start + 1, end + 1));
      if (value && ['parent', 'outline', 'local_proposals', 'response_contract'].every(k => Object.hasOwn(value, k))) result.push(value);
    } catch {}
  }
  return result;
}

function validateOperationBindings(body, selected, outline, proposals) {
  if (!body.plan || typeof body.plan !== 'object' || Array.isArray(body.plan) ||
      !Array.isArray(body.selections) || !Array.isArray(body.plan.slots) || !Array.isArray(body.plan.segments) ||
      !Array.isArray(body.segment_bindings) || !Array.isArray(body.timing_checks) || !Array.isArray(body.transition_checks) ||
      !Array.isArray(body.limitations)) fail('contract');
  const ids = rows => rows.map(row => row.slot_id);
  if (new Set(ids(body.selections)).size !== outline.slots.length || body.selections.length !== outline.slots.length ||
      !eq([...ids(body.selections)].sort(), [...ids(outline.slots)].sort()) || !eq(ids(body.plan.slots), ids(outline.slots))) fail('slot_order');
  const expected = [];
  for (const [index, slot] of outline.slots.entries()) {
    const target = body.plan.slots[index].intended_takeaway;
    if (typeof target !== 'string' || target.replace(/[“”]/g, '') !== slot.intended_takeaway.replace(/[“”]/g, '')) fail('target_words');
    const selection = body.selections.find(row => row.slot_id === slot.slot_id);
    const candidate = proposals.find(row => row.slot_id === slot.slot_id)?.candidates.find(row => row.candidate_id === selection.candidate_id);
    if (!candidate) fail('selected_candidate');
    for (const [operation_index, operation] of candidate.operations.entries()) expected.push({slot, candidate, operation_index, operation});
  }
  if (expected.length !== body.plan.segments.length || expected.length !== body.segment_bindings.length ||
      expected.length !== body.timing_checks.length) fail('operation_count');
  const claimIds = new Set();
  for (const [index, item] of expected.entries()) {
    const segment = body.plan.segments[index], binding = body.segment_bindings[index], operation = item.operation;
    if (!eq(binding, {segment_id: segment.segment_id, slot_id: item.slot.slot_id,
      candidate_id: item.candidate.candidate_id, operation_index: item.operation_index})) fail('operation_binding');
    const original = selected.provenance[operation.parent_segment_index];
    if (!original || segment.slot_id !== item.slot.slot_id || segment.source_id !== original.source_id ||
        segment.window_id !== original.window_id || !eq(segment.role_ids, original.role_ids) ||
        ['source_in_s', 'source_out_s', 'speed', 'freeze_tail_s'].some(k => (segment[k] ?? 0) !== operation[k])) fail('creative_operation_changed');
    if (!(Number.isFinite(segment.source_in_s) && Number.isFinite(segment.source_out_s) && segment.source_in_s < segment.source_out_s &&
        .5 <= segment.speed && segment.speed <= 2 && 0 <= (segment.freeze_tail_s ?? 0))) fail('operation_contract');
    if (!Array.isArray(segment.visual_claims) || !segment.visual_claims.length || segment.visual_claims.some(claim =>
      !claim.claim_id || claimIds.has(claim.claim_id) || !['visual_action', 'visual_outcome', 'identity', 'continuity'].includes(claim.kind) || !claim.description?.trim())) fail('claim_contract');
    for (const claim of segment.visual_claims) claimIds.add(claim.claim_id);
    const timing = body.timing_checks[index], core = segment.visual_claims.map(c => c.claim_id);
    if (timing.segment_id !== segment.segment_id || !Array.isArray(timing.essential_claims) ||
        !eq(timing.essential_claims.map(row => row.essential_interval_index), operation.essential_intervals.map((_, i) => i))) fail('timing_contract');
    const covered = new Set();
    for (const row of timing.essential_claims) {
      if (!Array.isArray(row.claim_ids) || !row.claim_ids.length || new Set(row.claim_ids).size !== row.claim_ids.length ||
          row.claim_ids.some(id => !core.includes(id))) fail('timing_claim_contract');
      for (const id of row.claim_ids) covered.add(id);
    }
    if (covered.size !== core.length) fail('timing_claim_contract');
  }
  const pairs = body.plan.segments.slice(1).map((row, i) => [body.plan.segments[i].segment_id, row.segment_id]);
  if (!eq(body.transition_checks.map(row => [row.from_segment_id, row.to_segment_id]), pairs) ||
      body.transition_checks.some(row => !['planned', 'unresolved'].includes(row.status) || !row.relation?.trim())) fail('transition_contract');
}

export function validateRequestBoundAssembly(root, state, grant, helpers) {
  const {read, fileDigest, jsonDigest, hash, scope, stageParent, responseBody, sameFiles, digest} = helpers;
  const entries = state.artifacts[POLICY];
  if (!entries) return null;
  if (entries.length !== 1) fail('changed');
  const file = entries[0].path, policy = read(file), count = policy.baseline_request_count;
  if (entries[0].sha256 !== fileDigest(file) || policy.policy !== POLICY || policy.task_id !== state.task_id ||
      policy.preparation_id !== grant.preparation_id || policy.parent_round !== 3 || policy.stage !== 'sf_3_assemble' ||
      policy.original_contract_status !== 'failed' || policy.old_parsed_created !== false || policy.semantic_truth_established !== false ||
      policy.target_comparison_policy !== 'curly_double_quote_only_v1' || !Number.isInteger(count) || count < grant.baseline_request_count ||
      count > state.calls.length || fileDigest(path.join(root, 'library_state.json'), value => value.calls.slice(0, count)) !== policy.prefix_calls_sha256 ||
      state.calls.slice(grant.baseline_request_count, count).some(call => call.status !== 'received')) fail('changed');
  const calls = state.calls.slice(grant.baseline_request_count).filter(c => ['sf_3_assemble', 'sf_3_assemble_repair'].includes(c.name));
  if (calls.length !== 2 || calls[0].name !== policy.stage || calls[1].name !== policy.stage + '_repair' ||
      calls[0].repair_of || calls[1].repair_of !== calls[0].id || calls.some(c => c.status !== 'received') ||
      policy.original_call_id !== calls[0].id || policy.repair_call_id !== calls[1].id || policy.chosen_call_id !== calls[1].id) fail('reply_changed');
  const preparation = read(grant.preparation_path), selected = preparation.parents.find(p => p.round === 3);
  const folder = path.join(grant.execution_directory, 'render_3'), outlinePath = path.join(folder, 'outline.json'),
    proposalsPath = path.join(folder, 'proposals.json'), catalogPath = path.join(root, 'catalog', 'inventory.json');
  const outline = read(outlinePath), proposals = read(proposalsPath), catalog = read(catalogPath), files = [], requests = [];
  let chosen;
  for (const [attempt, call] of calls.entries()) {
    const callFolder = path.join(root, 'calls', call.id), requestPath = path.join(callFolder, 'request.json'), responsePath = path.join(callFolder, 'response.json'),
      failurePath = path.join(callFolder, 'protocol_failure.json');
    const request = read(requestPath), response = read(responsePath), failure = read(failurePath);
    if (fs.existsSync(path.join(callFolder, 'parsed.json')) || fileDigest(requestPath) !== call.request_sha256 ||
        fileDigest(responsePath) !== call.response_sha256 || failure.attempt !== attempt || typeof failure.error !== 'string') fail('reply_changed');
    if (attempt === 0) {
      let failed = false;
      try { responseBody(response); } catch { failed = true; }
      if (!failed || !failure.error.includes('delimiter')) fail('syntax_failure_changed');
    } else {
      chosen = responseBody(response);
      if (Object.hasOwn(chosen.value, 'baseline_id') || Object.hasOwn(chosen.value, 'parent_sha256') ||
          failure.error !== 'slot_finecut:assembly_parent_changed') fail('wrong_parent_fields');
    }
    for (const p of [requestPath, responsePath, failurePath]) files.push({path: p, sha256: hash(p)});
    requests.push(request);
  }
  const original = requests[0], repair = requests[1], matched = contexts(original.arguments.prompt);
  const view = Object.fromEntries(['baseline_id', 'sha256', 'duration_s', 'provenance', 'output_mapping'].map(k => [k, selected[k]]));
  const additions = {baseline_id: selected.baseline_id, parent_sha256: selected.sha256};
  if (matched.length !== 1 || digest(matched[0].parent) !== digest(view) || digest(matched[0].outline) !== digest(outline) ||
      digest(matched[0].local_proposals) !== digest(proposals) || Object.entries(additions).some(([k, v]) => matched[0].response_contract[k] !== v) ||
      digest(policy.program_field_additions) !== digest(additions) || digest(policy.body) !== digest({...chosen.value, ...additions}) ||
      policy.model_body_sha256 !== jsonDigest(chosen.raw) || fileDigest(file, value => {
        const body = {...value.body}; delete body.baseline_id; delete body.parent_sha256; return body;
      }) !== policy.model_body_sha256) fail('body_changed');
  validateOperationBindings(policy.body, selected, outline, proposals);
  const media = fs.realpathSync(original.arguments.video_source), lineagePath = path.join(path.dirname(media), 'lineage.json'), lineage = read(lineagePath);
  const expectedScope = {kind: 'continuous_window', source_sha256: selected.sha256, source_start_s: 0, source_end_s: selected.duration_s};
  if (original.provider !== 'official_vision_mcp_in_codex' || repair.provider !== original.provider || original.tool !== 'analyze_video' || repair.tool !== original.tool ||
      path.resolve(repair.arguments.video_source) !== path.resolve(media) || hash(media) !== original.media_sha256 || repair.media_sha256 !== original.media_sha256 ||
      lineage.sha256 !== original.media_sha256 || scope(original.observation_scope) !== scope(expectedScope) || scope(repair.observation_scope) !== scope(expectedScope) ||
      scope(lineage) !== scope(expectedScope) || lineage.source_offset_s !== 0 || Math.abs(lineage.media_duration_s - selected.duration_s) > .001 ||
      lineage.time_mapping !== 'source_time_s = source_offset_s + proxy_time_s' || policy.media_sha256 !== original.media_sha256 ||
      scope(policy.observation_scope) !== scope(expectedScope)) fail('media_changed');
  const stops = state.artifacts.sf_parent_protocol_stop_3_point_navigation_resume, results = state.artifacts.sf_result_3_point_navigation_resume;
  if (!stops || stops.length !== 1 || !results || results.length !== 1) fail('stop_changed');
  const stopPath = stops[0].path, receiptPath = results[0].path, stop = read(stopPath), receipt = read(receiptPath),
    resultPath = path.join(folder, 'result_point_navigation_resume.json'), result = read(resultPath);
  if (fileDigest(stopPath) !== stops[0].sha256 || fileDigest(receiptPath) !== results[0].sha256 ||
      stop.original_call_id !== calls[0].id || stop.repair_call_id !== calls[1].id || stop.error !== 'model_protocol_repair_exhausted:sf_3_assemble' ||
      path.resolve(receipt.result_path) !== path.resolve(resultPath) || hash(resultPath) !== receipt.result_sha256 ||
      result.status !== 'stopped_protocol_failure' || digest(result.stop_receipt) !== digest(stop) ||
      result.baseline_id !== selected.baseline_id || result.parent_sha256 !== selected.sha256) fail('stop_changed');
  for (const p of [grant.preparation_path, catalogPath, grant.reference_methods_path, grant.knowledge_path, outlinePath, proposalsPath,
    media, lineagePath, stopPath, receiptPath, resultPath]) files.push({path: p, sha256: hash(p)});
  for (const item of result.completed_files) {
    if (hash(item.path) !== item.sha256) fail('completed_history_changed');
    if (!files.some(row => path.resolve(row.path) === path.resolve(item.path))) files.push(item);
  }
  if (!sameFiles(files, policy.protected_files)) fail('history_changed');
  const first = policy.body.plan.segments[0], source = catalog.sources.find(row => row.source_id === first.source_id);
  const segmentHash = fileDigest(file, value => ({segment: value.body.plan.segments[0].segment_id,
    sha: source.sha256, in: value.body.plan.segments[0].source_in_s, out: value.body.plan.segments[0].source_out_s})).slice(0, 16);
  if (policy.next_stage !== 'semantic_slice_21_' + segmentHash) fail('next_stage_changed');
  const later = state.calls.slice(count).filter(c => stageParent(c.name) === 3);
  const feedback = validateSourceFeedback(root, state, grant, {read, fileDigest, hash, scope});
  if (later.length && later[0].name !== (feedback ? feedback.stage : policy.next_stage)) fail('resume_stage');
  return policy;
}
