// Read-only native check for the explicitly authorized original-method trial.
// Old ledgers and unknown observations remain bound; no failed job is retried.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';

const NAME = 'restored_original_method_v1';
const POLICY = 'explicit_original_rough_method_restoration_v1';
const LIMITS = {rough_candidates:2, fine_windows:16, fine_renders:2,
  fine_actual_revisions:1, repairs_per_stage:1};
const require = (ok, reason) => { if (!ok) throw new Error('restoration_policy_' + reason); };
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function restorationStage(stem) {
  return typeof stem === 'string' && /^(?:coarse_zoom_search|overview_[a-f0-9]{8}|(?:zoom|fine)_[a-f0-9]{16}|(?:search|plan|blind|economy|review)_[01]|select_render|selected_review_v2_[01]|chain_e2e_v1_fine_(?:observe|inspect_[0-3]|detail_[0-3]_[0-5]|plan|blind(?:_r)?|review(?:_r)?|revise))$/.test(stem);
}

function handoff(root, state) {
  const entries = state.artifacts?.restored_rough_handoff;
  require(entries?.length === 1, 'actual_rough_handoff_required');
  const file = path.resolve(entries[0].path), relative = path.relative(root, file);
  require(relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative), 'rough_handoff_changed');
  const raw = fs.readFileSync(file, 'utf8');
  require(persistedJsonHash(raw) === entries[0].sha256, 'rough_handoff_changed');
  const binding = JSON.parse(raw), handoffFile = path.resolve(binding.path);
  const handoffRelative = path.relative(root, handoffFile);
  require(handoffRelative !== '..' && !handoffRelative.startsWith('..' + path.sep) && !path.isAbsolute(handoffRelative),
    'rough_handoff_changed');
  const handoffRaw = fs.readFileSync(handoffFile, 'utf8');
  require(persistedJsonHash(handoffRaw) === binding.sha256, 'rough_handoff_changed');
  const item = JSON.parse(handoffRaw);
  const call = state.calls.find(row => row.id === item.rough_review_call_id);
  require([0,1].includes(item.selected_round) && call?.status === 'received' &&
    /^(?:review|selected_review_v2)_[01](?:_repair)?$/.test(call.name) &&
    fs.existsSync(path.join(root, 'calls', call.id, 'parsed.json')), 'actual_rough_review_required');
  const video = path.resolve(item.rough_video_path), videoRelative = path.relative(root, video);
  require(videoRelative !== '..' && !videoRelative.startsWith('..' + path.sep) && !path.isAbsolute(videoRelative) &&
    sha(fs.readFileSync(video)) === item.rough_source_sha256, 'rough_video_changed');
}

export function restorationAuthorization(root) {
  root = fs.realpathSync(root);
  const state = read(path.join(root, 'library_state.json'));
  const config = state.input_lock?.configuration;
  if (!config?.restoration_policy) return null;
  require(config.restoration_policy === NAME && state.max_requests === null, 'configuration_changed');
  const file = path.join(root, 'authorization.json');
  require(config.restoration_authorization_path === file &&
    sha(fs.readFileSync(file)) === config.restoration_authorization_sha256, 'authorization_changed');
  const auth = read(file);
  require(auth.policy === POLICY && auth.name === NAME && auth.lane === root &&
    root === path.join(auth.parent_output, 'artifacts', NAME) &&
    same(auth.limits, LIMITS) && auth.parent_requests === 36 && auth.historical_requests === 274 &&
    auth.goal_resumed === false && auth.old_ledgers_unchanged === true &&
    auth.new_autonomous_decisions_not_teacher_EDL === true && auth.whole_reference_POST_forbidden === true &&
    typeof auth.user_instruction === 'string' && auth.user_instruction.trim().length > 0 &&
    auth.reference_seed?.source_call_id === 'glm_001_reference' &&
    auth.reference_seed.reference_sha256 === auth.reference_seed.full_response?.reference?.reference_sha256,
    'authorization_scope_changed');
  require(Array.isArray(auth.protected_files) && auth.protected_files.length > 72 &&
    new Set(auth.protected_files.map(item => item.path)).size === auth.protected_files.length,
    'bound_history_file_set_changed');
  for (const item of auth.protected_files)
    require(path.isAbsolute(item.path) && fs.realpathSync(item.path) === item.path &&
      sha(fs.readFileSync(item.path)) === item.sha256, 'bound_history_changed');
  require(state.request_count === state.calls.length &&
    new Set(state.calls.map(row => row.id)).size === state.calls.length, 'lane_count_changed');
  const names = new Set(), repairs = new Set();
  let previous;
  for (const call of state.calls) {
    require(!previous || previous.status === 'received', 'work_after_unsettled_request');
    const repairing = call.name?.endsWith('_repair');
    const stem = repairing ? call.name.slice(0, -7) : call.name;
    require(restorationStage(stem) && !names.has(call.name), 'stage_not_permitted');
    const raw = fs.readFileSync(path.join(root, 'calls', call.id, 'request.json'), 'utf8');
    require(persistedJsonHash(raw) === call.request_sha256, 'lane_request_changed');
    const request = JSON.parse(raw), scope = request.observation_scope || {};
    const reference = auth.reference_seed.reference_sha256;
    require(request.media_sha256 !== reference &&
      !(scope.source_sha256 === reference && (scope.kind === 'complete_file' ||
        scope.source_start_s === 0 && scope.source_end_s >= auth.reference_duration_s - .001)), 'whole_reference_POST_forbidden');
    if (scope.source_sha256 === reference)
      require(scope.kind === 'continuous_window' && Number.isFinite(scope.source_start_s) &&
        Number.isFinite(scope.source_end_s) && 0 <= scope.source_start_s &&
        scope.source_start_s < scope.source_end_s && scope.source_end_s <= auth.reference_duration_s &&
        scope.source_end_s - scope.source_start_s <= 6, 'reference_local_scope_invalid');
    for (const old of auth.unknown_inputs) {
      const prior = old.scope || {};
      const sameScope = scope.source_sha256 != null && scope.source_sha256 === prior.source_sha256 &&
        scope.source_start_s === prior.source_start_s && scope.source_end_s === prior.source_end_s;
      require(request.media_sha256 !== old.media_sha256 && call.request_sha256 !== old.request_sha256 && !sameScope,
        'historical_unknown_observation_no_replay:' + old.call_id);
    }
    if (repairing) {
      require(previous?.status === 'received' && previous.name === stem && !previous.repair_of &&
        call.repair_of === previous.id && !repairs.has(previous.id), 'extra_or_unbound_repair');
      repairs.add(previous.id);
    } else require(call.repair_of == null, 'repair_without_parent');
    if (stem.startsWith('chain_e2e_v1_fine_')) handoff(root, state);
    names.add(call.name); previous = call;
  }
  require([...names].filter(name => /^fine_/.test(name) && !name.endsWith('_repair')).length <= 16 &&
    [...names].filter(name => /^zoom_/.test(name) && !name.endsWith('_repair')).length <= 4,
    'observation_scope_exhausted');
  return {authorization:auth};
}
