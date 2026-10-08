// One newly authorized skill trial; the old editing routes remain frozen.
import fs from 'node:fs';
import path from 'node:path';
import {jsonHash, fileHash, prefixHash, scope, canonical} from './mcp_forward_slot_guard.mjs';

const POLICY = 'visual_story_skill_trial_v1', BASELINE = 250;
const RESUME = 'vss_unknown_265_resume_v1', RESUME_BASELINE = 265;
const LOST_CALL = 'glm_265_vss_detail_3_0', LOST_STAGE = 'vss_detail_3_0';
const STAGES = /^vss_(observe|inspect_[0-9]+|detail_[0-9]+_[0-9]+|plan|selected_[0-9]+|blind|review|revise|selected_r_[0-9]+|blind_r|review_r)(?:_repair)?$/;
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const fail = reason => { throw new Error('library_mcp_visual_story_' + reason); };
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function sameUnknownObservation(a, b) {
  if (scope(a) === scope(b)) return true;
  // complete_file and continuous_window cannot disguise the same continuous
  // observation after transcoding or rounding its reported container duration.
  const continuous = value => ['complete_file', 'continuous_window'].includes(value.kind);
  return continuous(a) && continuous(b) && a.source_sha256 === b.source_sha256 &&
    Math.abs(a.source_start_s - b.source_start_s) <= 1e-6 && Math.abs(a.source_end_s - b.source_end_s) <= 1e-6;
}
function sameSourceRange(a, b) {
  // A missing image reply cannot be replayed as a continuous clip or a newly
  // encoded grid. The independently authorized neighbouring pages are distinct.
  return a?.source_sha256 === b?.source_sha256 &&
    Number.isFinite(a?.source_start_s) && Number.isFinite(b?.source_start_s) &&
    Number.isFinite(a?.source_end_s) && Number.isFinite(b?.source_end_s) &&
    Math.abs(a.source_start_s - b.source_start_s) <= 1e-6 &&
    Math.abs(a.source_end_s - b.source_end_s) <= 1e-6;
}
const inside = (file, directory) => {
  const relative = path.relative(fs.realpathSync(directory), fs.realpathSync(file));
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};

function loadResume(root, state, authorizationFile) {
  const rows = state.artifacts?.[RESUME];
  if (!rows) return null;
  if (rows.length !== 1 || !inside(rows[0].path, path.join(root, 'artifacts')) ||
      jsonHash(rows[0].path) !== rows[0].sha256) fail('resume_authorization_changed');
  const r = read(rows[0].path);
  if (r.policy !== RESUME || r.task_id !== state.task_id ||
      r.authorization_sha256 !== jsonHash(authorizationFile) ||
      r.input_lock_sha256 !== jsonHash(path.join(root, 'library_state.json'), v => v.input_lock) ||
      r.baseline_request_count !== RESUME_BASELINE || state.calls.length < RESUME_BASELINE ||
      r.prefix_calls_sha256 !== jsonHash(path.join(root, 'library_state.json'), v => v.calls.slice(0, RESUME_BASELINE)) ||
      r.lost_call_id !== LOST_CALL || !same(r.skip_stages, [LOST_STAGE]) ||
      r.new_renders !== 0 || r.goal_resumed !== false || r.teacher_answers_forbidden !== true ||
      r.progress_policy !== 'skip_one_lost_page_continue_original_trial_no_POST_replay_no_new_round' ||
      typeof r.user_instruction !== 'string' || !r.user_instruction.trim()) fail('resume_scope_or_prefix_changed');
  if (!inside(r.baseline_state_path, path.join(root, 'artifacts')) ||
      fileHash(r.baseline_state_path) !== r.baseline_state_sha256) fail('resume_baseline_modified');
  const baseline = read(r.baseline_state_path);
  if (baseline.request_count !== RESUME_BASELINE || baseline.calls.length !== RESUME_BASELINE ||
      baseline.task_id !== state.task_id || !same(baseline.calls, state.calls.slice(0, RESUME_BASELINE)) ||
      baseline.policy_version !== state.policy_version || baseline.max_requests !== state.max_requests ||
      !same(baseline.input_lock, state.input_lock) ||
      baseline.artifacts?.[RESUME]) fail('resume_baseline_changed');
  for (const [key, value] of Object.entries(baseline.artifacts || {})) {
    if (!same(state.artifacts[key], value)) fail('resume_historical_artifact_changed');
  }
  if (!Array.isArray(r.protected_files) || !r.protected_files.length || !Array.isArray(r.journal_prefixes)) {
    fail('resume_proofs_required');
  }
  for (const row of r.protected_files) {
    if (fileHash(row.path) !== row.sha256) fail('resume_protected_bytes_changed');
  }
  for (const row of r.journal_prefixes) {
    if (!Number.isSafeInteger(row.bytes) || row.bytes < 0 || prefixHash(row.path, row.bytes) !== row.sha256) {
      fail('resume_journal_prefix_changed');
    }
  }
  const lost = baseline.calls[RESUME_BASELINE - 1];
  if (lost.id !== LOST_CALL || lost.name !== LOST_STAGE || lost.status !== 'uncertain' ||
      baseline.calls.slice(BASELINE, RESUME_BASELINE - 1).some(c => c.status !== 'received') ||
      lost.request_sha256 !== r.lost_request_sha256) fail('resume_lost_call_changed');
  const requestFile = path.join(root, 'calls', LOST_CALL, 'request.json'), request = read(requestFile);
  if (jsonHash(requestFile) !== r.lost_request_sha256 || request.media_sha256 !== r.lost_media_sha256 ||
      scope(request.observation_scope) !== scope(r.lost_scope)) fail('resume_lost_input_changed');
  return r;
}

function load(root, env = process.env) {
  const file = env.OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE, sha = env.OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256;
  if (!file && !sha) return null;
  if (!file || !sha || fileHash(file) !== sha) fail('authorization_modified');
  const stateFile = path.join(root, 'library_state.json'), state = read(stateFile), p = read(file);
  const rows = state.artifacts?.[POLICY];
  if (rows?.length !== 1 || path.resolve(rows[0].path) !== path.resolve(file) ||
      rows[0].sha256 !== jsonHash(file) || !inside(file, path.join(root, 'artifacts')) ||
      p.policy !== POLICY || p.task_id !== state.task_id || p.baseline_request_count !== BASELINE ||
      p.input_lock_sha256 !== jsonHash(stateFile, v => v.input_lock) ||
      state.calls.length !== state.request_count || state.calls.length < BASELINE ||
      p.prefix_calls_sha256 !== jsonHash(stateFile, v => v.calls.slice(0, BASELINE)) ||
      p.max_concurrency !== 1 || p.max_renders !== 2 || p.repairs_per_stage !== 1 ||
      p.numeric_total_request_limit !== null || p.teacher_answers_forbidden !== true ||
      typeof p.user_instruction !== 'string' || !p.user_instruction.trim()) fail('policy_or_prefix_changed');
  if (!inside(p.baseline_state_path, path.join(root, 'artifacts')) ||
      fileHash(p.baseline_state_path) !== p.baseline_state_sha256) fail('baseline_modified');
  const baseline = read(p.baseline_state_path);
  if (baseline.request_count !== BASELINE || baseline.calls.length !== BASELINE ||
      !same(baseline.calls, state.calls.slice(0, BASELINE)) || baseline.task_id !== state.task_id ||
      baseline.policy_version !== state.policy_version || baseline.max_requests !== state.max_requests ||
      !same(baseline.input_lock, state.input_lock)) fail('baseline_changed');
  for (const [key, value] of Object.entries(baseline.artifacts || {})) {
    if (!same(state.artifacts[key], value)) fail('historical_artifact_changed');
  }
  if (!Array.isArray(p.protected_files) || !Array.isArray(p.journal_prefixes) ||
      !Array.isArray(p.knowledge_files) || !p.knowledge_files.length || !Array.isArray(p.unknown_inputs)) {
    fail('proofs_required');
  }
  for (const row of [...p.protected_files, ...p.knowledge_files]) {
    if (fileHash(row.path) !== row.sha256) fail('protected_bytes_changed');
  }
  for (const row of p.journal_prefixes) {
    if (!Number.isSafeInteger(row.bytes) || row.bytes < 0 || prefixHash(row.path, row.bytes) !== row.sha256) {
      fail('journal_prefix_changed');
    }
  }
  const unknowns = baseline.calls.filter(c => c.status === 'uncertain');
  if (!same(p.unknown_inputs.map(u => u.call_id).sort(), unknowns.map(c => c.id).sort())) fail('unknown_exclusions_changed');
  for (const unknown of p.unknown_inputs) {
    const old = unknowns.find(c => c.id === unknown.call_id);
    const oldFile = path.join(root, 'calls', old.id, 'request.json'), request = read(oldFile);
    let observation = request.observation_scope;
    if (!observation) {
      const media = request.arguments.image_source || request.arguments.video_source;
      observation = read(path.join(path.dirname(media), 'lineage.json'));
    }
    if (unknown.request_sha256 !== old.request_sha256 || jsonHash(oldFile) !== old.request_sha256 ||
        unknown.media_sha256 !== request.media_sha256 || scope(unknown.scope) !== scope(observation)) {
      fail('unknown_exclusion_binding_changed');
    }
  }
  const added = state.calls.slice(BASELINE), names = new Set();
  for (const call of added) {
    if (!STAGES.test(call.name) || names.has(call.name) ||
        !['submitted', 'received', 'uncertain', 'failed_known'].includes(call.status)) fail('stage_or_status_invalid');
    const folder = path.join(root, 'calls', call.id);
    if (jsonHash(path.join(folder, 'request.json')) !== call.request_sha256) fail('new_request_changed');
    if (call.status === 'received' && jsonHash(path.join(folder, 'response.json')) !== call.response_sha256) fail('new_response_changed');
    if (call.name.endsWith('_repair')) {
      const stem = call.name.slice(0, -7), previous = added[added.indexOf(call) - 1];
      if (!previous || previous.name !== stem || previous.status !== 'received' || previous.repair_of ||
          call.repair_of !== previous.id || added.some(c => c !== call && c.repair_of === previous.id)) fail('sole_repair_binding');
      const original = read(path.join(root, 'calls', previous.id, 'request.json'));
      const repaired = read(path.join(folder, 'request.json'));
      if (original.media_sha256 !== repaired.media_sha256 || scope(original.observation_scope) !== scope(repaired.observation_scope)) fail('repair_input_changed');
    } else if (call.repair_of) fail('original_has_repair');
    names.add(call.name);
  }
  if (added.filter(c => c.status === 'submitted').length > 1) fail('single_lane_required');
  const resume = loadResume(root, state, file);
  return {policy: p, state, added, resume};
}

export function visualStoryConfiguration(root, env = process.env) {
  const value = load(root, env);
  return value ? {policy: POLICY, maxConcurrency: 1} : null;
}

export function visualStoryRequestLimit(root, job, env = process.env) {
  const value = load(root, env);
  if (!value) return null;
  const {state, policy: p, added, resume} = value;
  const call = added.find(c => c.id === job.job_id);
  if (!call || call.status !== 'submitted' || added.at(-1) !== call ||
      added.some(c => c !== call && c.status !== 'received' && !(resume && c.id === resume.lost_call_id && c.status === 'uncertain')) ||
      resume && (state.calls.indexOf(call) < RESUME_BASELINE || resume.skip_stages.includes(call.name.replace(/_repair$/, '')))) {
    fail('unsettled_predecessor_or_job');
  }
  const requestFile = path.join(root, 'calls', call.id, 'request.json'), request = read(requestFile);
  const queue = read(path.join(root, 'mcp_queue', call.id + '.request.json'));
  const stem = call.name.replace(/_repair$/, ''), rows = state.artifacts['vss_input_' + stem];
  if (rows?.length !== 1 || jsonHash(rows[0].path) !== rows[0].sha256 ||
      !inside(rows[0].path, path.join(root, 'artifacts'))) fail('input_descriptor_changed');
  const d = read(rows[0].path), image = request.tool === 'analyze_image';
  const media = request.arguments?.[image ? 'image_source' : 'video_source'];
  if (request.provider !== 'official_vision_mcp_in_codex' || !['analyze_image', 'analyze_video'].includes(request.tool) ||
      queue.job_id !== call.id || queue.tool !== request.tool || !same(queue.arguments, request.arguments) ||
      job.job_id !== call.id || d.stage !== stem || d.tool !== request.tool ||
      typeof d.purpose !== 'string' || !d.purpose.trim() || !media ||
      path.resolve(d.media_path) !== path.resolve(media) || d.media_sha256 !== request.media_sha256 ||
      fileHash(media) !== request.media_sha256 || scope(d.scope) !== scope(request.observation_scope)) fail('bound_input_required');
  for (const lost of p.unknown_inputs) {
    if (lost.request_sha256 === call.request_sha256 || lost.media_sha256 === request.media_sha256 ||
        sameUnknownObservation(lost.scope, request.observation_scope)) fail('unknown_input_replay');
  }
  if (resume && (resume.lost_request_sha256 === call.request_sha256 ||
      resume.lost_media_sha256 === request.media_sha256 || sameSourceRange(resume.lost_scope, request.observation_scope))) {
    fail('unknown_input_replay');
  }
  return Infinity;
}

export function visualStoryFrozenJob(root, env = process.env) {
  const value = load(root, env);
  if (!value) return null;
  const frozen = new Map(value.state.calls.slice(0, value.resume ? RESUME_BASELINE : BASELINE).map(c => [
    path.resolve(root, 'mcp_queue', c.id + '.request.json'), c.id]));
  return requestFile => {
    const id = frozen.get(path.resolve(requestFile));
    if (!id) return false;
    load(root, env);
    const job = read(requestFile), request = read(path.join(root, 'calls', id, 'request.json'));
    if (job.job_id !== id || job.tool !== request.tool || !same(job.arguments, request.arguments)) fail('frozen_queue_binding_changed');
    return true;
  };
}
