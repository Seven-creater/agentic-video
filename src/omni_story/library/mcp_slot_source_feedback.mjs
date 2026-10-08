// Read-only hash-bound authorization for one distinct source-feedback stage.
import path from 'node:path';

export const SOURCE_FEEDBACK_POLICY = 'sf_source_feedback_replan_v1_3';
export const SOURCE_FEEDBACK_STAGE = 'sf_3_source_feedback_replan_v1';
export const SOURCE_FEEDBACK_PATTERN = /^sf_3_source_feedback_replan_v1(?:_repair)?$/;
const fail = name => { throw new Error('library_mcp_slot_source_feedback_' + name); };

export function validateSourceFeedback(root, state, grant, helpers) {
  const {read, fileDigest, hash, scope} = helpers;
  const entries = state.artifacts[SOURCE_FEEDBACK_POLICY];
  if (!entries) return null;
  if (entries.length !== 1) fail('one_record_required');
  const file = entries[0].path, policy = read(file), count = policy.baseline_request_count;
  if (entries[0].sha256 !== fileDigest(file) || policy.policy !== SOURCE_FEEDBACK_POLICY ||
      policy.task_id !== state.task_id || policy.preparation_id !== grant.preparation_id || policy.parent_round !== 3 ||
      policy.stage !== SOURCE_FEEDBACK_STAGE || policy.planning_attempts !== 1 || policy.repairs_per_stage !== 1 ||
      policy.new_unique_windows !== 0 || policy.renders_per_parent !== 1 || policy.no_automatic_replanning !== true ||
      policy.old_failures_preserved !== true || policy.semantic_truth_established !== false ||
      policy.result_name !== 'result_source_feedback_resume.json' || !policy.user_instruction?.trim() ||
      !Number.isInteger(count) || count < grant.baseline_request_count || count > state.calls.length ||
      fileDigest(path.join(root, 'library_state.json'), value => value.calls.slice(0, count)) !== policy.prefix_calls_sha256 ||
      state.calls.slice(grant.baseline_request_count, count).some(c => c.status !== 'received')) fail('policy_changed');
  const bindings = state.artifacts.sf_request_bound_assembly_v1_3;
  if (!bindings || bindings.length !== 1 || path.resolve(bindings[0].path) !== path.resolve(policy.assembly_binding_path) ||
      bindings[0].sha256 !== policy.assembly_binding_sha256 || fileDigest(bindings[0].path) !== bindings[0].sha256) fail('assembly_changed');
  const binding = read(bindings[0].path);
  if (fileDigest(file, value => value.original_assembly) !== fileDigest(bindings[0].path, value => value.body) ||
      policy.media_sha256 !== binding.media_sha256 || scope(policy.observation_scope) !== scope(binding.observation_scope) ||
      fileDigest(file, value => value.original_operation.essential_intervals) !== policy.original_essential_intervals_sha256 ||
      policy.max_replacement_operations !== 2 || policy.max_final_slices !== binding.body.plan.segments.length + 1) fail('assembly_changed');
  const blocked = policy.blocked_segment, operation = policy.original_operation;
  if (!blocked || !operation || blocked.slot_id !== policy.blocked_slot_id ||
      operation.source_in_s !== blocked.scope.source_start_s || operation.source_out_s !== blocked.scope.source_end_s ||
      !policy.original_assembly.plan.segments.some(s => s.segment_id === blocked.segment_id && s.slot_id === blocked.slot_id &&
        s.source_in_s === operation.source_in_s && s.source_out_s === operation.source_out_s)) fail('blocked_input_changed');
  for (const id of [blocked.original_call, blocked.repair_call]) {
    const call = state.calls.find(c => c.id === id);
    const folder = path.join(root, 'calls', id);
    if (!call || call.status !== 'received' || fileDigest(path.join(folder, 'request.json')) !== call.request_sha256 ||
        fileDigest(path.join(folder, 'response.json')) !== call.response_sha256 ||
        scope(read(path.join(folder, 'request.json')).observation_scope) !== scope(blocked.scope)) fail('exhausted_input_changed');
  }
  if (!Array.isArray(policy.protected_files) || !policy.protected_files.length) fail('history_changed');
  for (const row of policy.protected_files) if (hash(row.path) !== row.sha256) fail('history_changed');
  const later = state.calls.slice(count).filter(c => /^(?:sf_3_|semantic_(?:slice|claim|claims)_21_)/.test(c.name));
  if (later.length && later[0].name !== SOURCE_FEEDBACK_STAGE) fail('first_stage_changed');
  const calls = state.calls.slice(count).filter(c => SOURCE_FEEDBACK_PATTERN.test(c.name));
  if (calls.length > 2 || calls.some((c, i) => i === 0 ? c.name !== SOURCE_FEEDBACK_STAGE || c.repair_of :
      c.name !== SOURCE_FEEDBACK_STAGE + '_repair' || c.repair_of !== calls[0].id || calls[0].status !== 'received')) fail('stage_repeated');
  return policy;
}

export function validateSourceFeedbackRequest(policy, call, request, helpers) {
  if (!SOURCE_FEEDBACK_PATTERN.test(call.name)) return false;
  if (!policy) fail('authorization_required');
  if (request.provider !== 'official_vision_mcp_in_codex' || request.tool !== 'analyze_video' ||
      request.media_sha256 !== policy.media_sha256 || helpers.scope(request.observation_scope) !== helpers.scope(policy.observation_scope)) fail('request_media_changed');
  return true;
}
