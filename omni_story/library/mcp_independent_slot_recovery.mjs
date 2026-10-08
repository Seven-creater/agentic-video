// Explicit local continuation; this helper never changes or submits a job.
import fs from 'node:fs';
import path from 'node:path';

export const INDEPENDENT_SLOT_RECOVERY_POLICY = 'sf_independent_slot_recovery_v1';
export const LOCAL_REPLAN_STAGE = 'sf_3_local_source_replan_v2';
export const LOCAL_REPLAN_PATTERN = /^sf_3_local_source_replan_v2(?:_repair)?$/;
const LOST = 'glm_166_sf_3_source_feedback_replan_v1';
const FROZEN = ['glm_004_coarse_978d5360_01', 'glm_131_active_10_draft', LOST];
const STANDARD = /^(?:sf_(?:0|3)_(?:assemble|blind|economy|review)|semantic_(?:slice|claims)_(?:20|21)_[a-f0-9]{16})(?:_repair)?$/;
const fail = name => { throw new Error('library_mcp_independent_slot_recovery_' + name); };
const canonical = v => Array.isArray(v) ? v.map(canonical) : v && typeof v === 'object' ?
  Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

export function independentRecoveryParent(name) {
  if (typeof name !== 'string' || (!STANDARD.test(name) && !LOCAL_REPLAN_PATTERN.test(name))) fail('stage_not_authorized');
  const parts = name.split('_');
  return parts[0] === 'sf' ? Number(parts[1]) : (parts[2] === '20' ? 0 : 3);
}

export function independentRecoveryAdmissibleStatus(policy, call) {
  if (['received', 'submitted'].includes(call.status)) return true;
  return !!policy && call.status === 'uncertain' && policy.frozen_unknown_inputs.some(
    old => old.call_id === call.id && old.request_sha256 === call.request_sha256);
}

export function validateIndependentSlotRecovery(root, state, grant, helpers) {
  const {read, fileDigest, hash, scope} = helpers;
  const entries = state.artifacts[INDEPENDENT_SLOT_RECOVERY_POLICY];
  if (!entries) return null;
  if (entries.length !== 1) fail('one_policy_required');
  const file = entries[0].path, policy = read(file), count = policy.baseline_request_count;
  if (fileDigest(file) !== entries[0].sha256 || policy.policy !== INDEPENDENT_SLOT_RECOVERY_POLICY ||
      policy.task_id !== state.task_id || policy.preparation_id !== grant.preparation_id ||
      path.resolve(policy.output) !== path.resolve(root) ||
      fileDigest(path.join(root, 'library_state.json'), v => v.input_lock) !== policy.input_lock_sha256 ||
      policy.base_request_limit !== state.max_requests || policy.base_request_limit !== grant.base_request_limit ||
      count !== 166 || state.request_count !== state.calls.length || state.calls.length < count ||
      fileDigest(path.join(root, 'library_state.json'), v => v.calls.slice(0, count)) !== policy.prefix_calls_sha256 ||
      !equal(policy.frozen_unknown_call_ids, FROZEN) || policy.first_stages?.['0'] !== 'sf_0_assemble' ||
      policy.first_stages?.['3'] !== LOCAL_REPLAN_STAGE || Object.keys(policy.first_stages).length !== 2 ||
      policy.repairs_per_stage !== 1 || policy.renders_per_parent !== 1 || policy.max_slices_per_parent !== 32 ||
      policy.new_unique_windows !== 0 || policy.effective_request_limit !== null ||
      policy.no_numeric_request_ceiling !== true || policy.old_failures_preserved !== true ||
      policy.no_automatic_replanning !== true || policy.goal_resumed !== false ||
      policy.local_input_is_new_scope_not_unknown_reply !== true || !policy.user_instruction?.trim()) fail('policy_or_prefix_changed');
  const baseline = state.calls.slice(0, count);
  if (baseline.some(c => !['received', 'uncertain'].includes(c.status)) ||
      !equal(baseline.filter(c => c.status === 'uncertain').map(c => c.id), FROZEN) ||
      baseline.at(-1).id !== LOST || baseline.at(-1).name !== 'sf_3_source_feedback_replan_v1' || baseline.at(-1).repair_of) fail('unexpected_unknown_or_pending_baseline');
  if (!Array.isArray(policy.unknown_inputs) || policy.unknown_inputs.length !== 3 ||
      fileDigest(file, v => v.unknown_inputs) !== fileDigest(file, v => v.frozen_unknown_inputs)) fail('unknown_inputs_changed');
  for (const [index, old] of policy.unknown_inputs.entries()) {
    const call = baseline.find(c => c.id === FROZEN[index]), folder = path.join(root, 'calls', call.id);
    const requestFile = path.join(folder, 'request.json'), request = read(requestFile);
    const originalScope = request.observation_scope ?? read(path.join(path.dirname(request.arguments.image_source ?? request.arguments.video_source), 'lineage.json'));
    if (old.call_id !== call.id || old.request_sha256 !== call.request_sha256 || fileDigest(requestFile) !== call.request_sha256 ||
        old.media_sha256 !== request.media_sha256 || scope(old.scope) !== scope(originalScope) ||
        fs.existsSync(path.join(folder, 'response.json')) || fs.existsSync(path.join(folder, 'parsed.json'))) fail('unknown_request_or_reply_changed');
  }
  for (const [name, entries] of Object.entries(policy.baseline_artifacts)) {
    if (!equal(state.artifacts[name], entries)) fail('old_artifact_ledger_changed');
    for (const entry of entries) if (fileDigest(entry.path) !== entry.sha256) fail('old_artifact_changed');
  }
  const obsEntries = policy.baseline_artifacts.sf_unknown_166_observation_v1;
  if (!obsEntries || obsEntries.length !== 1) fail('unknown_observation_receipt_required');
  const observation = read(obsEntries[0].path), transport = policy.transport_binding;
  if (observation.call_id !== LOST || observation.response_captured !== false || observation.no_replay !== true ||
      observation.request_count_before !== 166 || observation.new_requests !== 0 || observation.new_renders !== 0 ||
      transport.call_id !== LOST || transport.request_sha256 !== baseline.at(-1).request_sha256) fail('unknown_observation_receipt_changed');
  const previous = {...baseline.at(-1), status: 'submitted'};
  delete previous.reconciled_at;
  const reclassification = read(transport.reclassification_path);
  if (!equal(observation.previous_call, previous) || !equal(reclassification.previous_record, previous) ||
      !reclassification.http_evidence.some(e => equal(e.observation_artifact, obsEntries[0]))) fail('uncertain_reclassification_changed');
  for (const label of ['call_request', 'queue_request', 'queue_started', 'reclassification']) {
    if (hash(transport[label + '_path']) !== transport[label + '_byte_sha256']) fail('lost_queue_changed');
  }
  const queue = read(transport.queue_request_path), callRequest = read(transport.call_request_path);
  if (queue.job_id !== LOST || queue.tool !== callRequest.tool ||
      fileDigest(transport.queue_request_path, v => v.arguments) !== fileDigest(transport.call_request_path, v => v.arguments) ||
      read(transport.queue_started_path).job_id !== LOST ||
      fs.existsSync(path.join(root, 'mcp_queue', LOST + '.response.json'))) fail('lost_queue_changed');
  const local = policy.local_replan_input, preparation = read(grant.preparation_path);
  const parent = preparation.parents.find(p => p.round === 3);
  const expectedScope = {kind: 'continuous_window', source_sha256: parent.sha256, source_start_s: 27, source_end_s: 34};
  const lost = policy.unknown_inputs.at(-1);
  if (Math.abs(parent.duration_s - 34) > .001 || hash(parent.path) !== parent.sha256 ||
      scope(lost.scope) !== scope({...expectedScope, source_start_s: 0}) || local.stage !== LOCAL_REPLAN_STAGE ||
      local.parent_round !== 3 || local.parent_sha256 !== parent.sha256 || scope(local.observation_scope) !== scope(expectedScope) ||
      local.source_offset_s !== 27 || local.duration_s !== 7 || local.fps !== 30 ||
      local.time_mapping !== 'source_time_s = source_offset_s + proxy_time_s') fail('local_media_mapping_changed');
  const known = baseline.filter(c => c.status === 'received' && c.name.startsWith('sf_3_facts_') && !c.repair_of)
    .filter(c => scope(read(path.join(root, 'calls', c.id, 'request.json')).observation_scope) === scope(expectedScope));
  if (known.length !== 1 || known[0].id !== local.known_media_call_id) fail('known_local_parent_media_required');
  const knownFile = path.join(root, 'calls', known[0].id, 'request.json'), request = read(knownFile), lineage = read(local.lineage_path);
  if (fileDigest(knownFile) !== known[0].request_sha256 || request.provider !== 'official_vision_mcp_in_codex' ||
      request.tool !== 'analyze_video' || path.resolve(request.arguments.video_source) !== path.resolve(local.path) ||
      path.resolve(lineage.path) !== path.resolve(local.path) || hash(local.path) !== local.media_sha256 ||
      request.media_sha256 !== local.media_sha256 || lineage.sha256 !== local.media_sha256 ||
      hash(local.lineage_path) !== local.lineage_sha256 || scope(lineage) !== scope(expectedScope) ||
      scope(lineage.spec) !== scope(expectedScope) || lineage.spec.fps !== 30 || lineage.source_offset_s !== 27 ||
      Math.abs(lineage.media_duration_s - 7) > .001 || lineage.time_mapping !== local.time_mapping) fail('local_media_mapping_changed');
  const expectedFiles = new Set([transport.queue_request_path, transport.queue_started_path, local.path, local.lineage_path].map(p => path.resolve(p)));
  for (const old of policy.unknown_inputs) {
    const request = read(path.join(root, 'calls', old.call_id, 'request.json'));
    if (!request.observation_scope) expectedFiles.add(path.resolve(path.dirname(request.arguments.image_source ?? request.arguments.video_source), 'lineage.json'));
  }
  for (const entries of Object.values(policy.baseline_artifacts)) for (const entry of entries) expectedFiles.add(path.resolve(entry.path));
  for (const call of baseline) {
    const folder = path.join(root, 'calls', call.id);
    if (!fs.existsSync(folder)) continue;
    for (const entry of fs.readdirSync(folder, {withFileTypes: true})) if (entry.isFile()) expectedFiles.add(path.resolve(folder, entry.name));
  }
  if (!Array.isArray(policy.protected_files) || policy.protected_files.length !== expectedFiles.size ||
      new Set(policy.protected_files.map(p => path.resolve(p.path))).size !== expectedFiles.size) fail('history_changed');
  for (const proof of policy.protected_files) if (!expectedFiles.has(path.resolve(proof.path)) || hash(proof.path) !== proof.sha256) fail('history_changed');
  if (state.calls.some(c => !independentRecoveryAdmissibleStatus(policy, c))) fail('new_outcome_unknown');
  const later = state.calls.slice(count), pending = later.filter(c => c.status === 'submitted');
  if (pending.length > 2 || new Set(pending.map(c => independentRecoveryParent(c.name))).size !== pending.length) fail('one_pending_per_parent');
  for (const parent of [0, 3]) {
    const own = later.filter(c => independentRecoveryParent(c.name) === parent);
    if (own.length && own[0].name !== policy.first_stages[String(parent)]) fail('first_independent_stage_changed');
  }
  const localCalls = later.filter(c => LOCAL_REPLAN_PATTERN.test(c.name));
  if (localCalls.length > 2 || localCalls.some((c, i) => c.name !== (i === 0 ? LOCAL_REPLAN_STAGE : LOCAL_REPLAN_STAGE + '_repair') ||
      (i === 0 ? c.repair_of : c.repair_of !== localCalls[0].id || localCalls[0].status !== 'received'))) fail('local_stage_or_repair_repeated');
  return policy;
}

export function checkIndependentSlotRecoveryInput(policy, call, request, helpers) {
  if (!policy) return false;
  independentRecoveryParent(call.name);
  if (request.provider !== 'official_vision_mcp_in_codex' || request.tool !== 'analyze_video') fail('official_provider_required');
  if (policy.unknown_inputs.some(old => helpers.requestDigest === old.request_sha256 || request.media_sha256 === old.media_sha256 ||
      helpers.scope(request.observation_scope) === helpers.scope(old.scope))) fail('unknown_input_replay_forbidden');
  if (!LOCAL_REPLAN_PATTERN.test(call.name)) return false;
  const local = policy.local_replan_input;
  if (request.media_sha256 !== local.media_sha256 || helpers.scope(request.observation_scope) !== helpers.scope(local.observation_scope) ||
      path.resolve(request.arguments.video_source) !== path.resolve(local.path)) fail('local_replan_input_changed');
  return true;
}

export function independentRecoveryFirstStage(policy, state, parent, name) {
  if (!policy) return false;
  if (![0, 3].includes(parent) || independentRecoveryParent(name) !== parent) fail('parent_changed');
  const own = state.calls.slice(policy.baseline_request_count).filter(c => independentRecoveryParent(c.name) === parent);
  if (own.length) return false;
  if (name !== policy.first_stages[String(parent)]) fail('first_independent_stage_changed');
  return true;
}
