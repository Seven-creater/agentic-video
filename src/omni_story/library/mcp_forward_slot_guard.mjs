// Independent forward namespace. Never makes an authorization or repairs history.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const POLICY = 'sf_fact_grounded_reconstruction_v1';
const BASELINE = 191;
const STAGES = /^sfv2_(0|3)_(reconstruct|slice_[a-f0-9]{16}|compare|blind|economy|review)(_repair)?$/;
const FROZEN = ['glm_004_coarse_978d5360_01', 'glm_131_active_10_draft', 'glm_166_sf_3_source_feedback_replan_v1'];
const fail = reason => { throw new Error('library_mcp_forward_slot_' + reason); };
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const hashBytes = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
const digest = value => hashBytes(JSON.stringify(canonical(value)));
class NumericLiteral { constructor(token) { this.token = token; } }
const jsonTrees = new Map();
function jsonHash(file, select = value => value) {
  // Only the active v2 route opts into tree reuse. Every selector still runs:
  // identical callback source can capture a different artifact key each time.
  const cached = Boolean(process.env.OMNI_LIBRARY_MICROCLIP_V2_AUTH_FILE);
  const actual = cached ? fs.realpathSync(file) : file;
  const stampOf = stat => [stat.size, stat.mtimeNs, stat.ctimeNs, stat.ino, stat.dev].join(':');
  let stamp, previous;
  if (cached) {
    const stat = fs.statSync(actual, {bigint: true});
    if (!stat.isFile()) fail('regular_file_required');
    stamp = stampOf(stat);
    previous = jsonTrees.get(actual);
  }
  let raw;
  const reused = cached && previous?.stamp === stamp;
  if (reused) raw = previous.raw;
  else {
    raw = JSON.parse(fs.readFileSync(actual, 'utf8'), (key, value, context) => {
      if (typeof value !== 'number') return value;
      if (!context?.source) fail('numeric_lexemes_unavailable');
      return new NumericLiteral(context.source);
    });
    if (cached) {
      const freeze = value => {
        if (value && typeof value === 'object') {
          for (const child of Object.values(value)) freeze(child);
          Object.freeze(value);
        }
      };
      freeze(raw);
    }
  }
  const stringify = value => value instanceof NumericLiteral ? value.token : Array.isArray(value)
    ? '[' + value.map(stringify).join(',') + ']' : value && typeof value === 'object'
    ? '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + stringify(value[k])).join(',') + '}'
    : JSON.stringify(value);
  const result = hashBytes(stringify(select(raw)));
  if (cached) {
    if (stampOf(fs.statSync(actual, {bigint: true})) !== stamp) fail('json_changed_during_proof');
    if (!reused) jsonTrees.set(actual, {stamp, raw});
  }
  return result;
}
const proofs = new Map();
function fileHash(file) {
  const actual = fs.realpathSync(file), s = fs.statSync(actual, {bigint: true});
  if (!s.isFile()) fail('regular_file_required');
  const stamp = [s.size, s.mtimeNs, s.ctimeNs, s.ino, s.dev].join(':');
  if (proofs.get(actual)?.stamp === stamp) return proofs.get(actual).hash;
  const hash = crypto.createHash('sha256'), buf = Buffer.alloc(1024 * 1024), fd = fs.openSync(actual, 'r');
  try { let size; while ((size = fs.readSync(fd, buf, 0, buf.length, null))) hash.update(buf.subarray(0, size)); }
  finally { fs.closeSync(fd); }
  const end = fs.statSync(actual, {bigint: true});
  if ([end.size, end.mtimeNs, end.ctimeNs, end.ino, end.dev].join(':') !== stamp) fail('file_changed_during_proof');
  const result = hash.digest('hex'); proofs.set(actual, {stamp, hash: result}); return result;
}
const prefixProofs = new Map();
function prefixHash(file, size) {
  const actual = fs.realpathSync(file), s = fs.statSync(actual, {bigint: true});
  const stamp = [s.size, s.mtimeNs, s.ctimeNs, s.ino, s.dev].join(':'), key = actual + ':' + size;
  if (prefixProofs.get(key)?.stamp === stamp) return prefixProofs.get(key).hash;
  const hash = crypto.createHash('sha256'), buf = Buffer.alloc(1024 * 1024), fd = fs.openSync(file, 'r');
  try { let remaining = size; while (remaining) {
    const n = fs.readSync(fd, buf, 0, Math.min(remaining, buf.length), null);
    if (!n) fail('journal_prefix_truncated'); hash.update(buf.subarray(0, n)); remaining -= n;
  } } finally { fs.closeSync(fd); }
  const end = fs.statSync(actual, {bigint: true});
  const result = hash.digest('hex');
  if ([end.size, end.mtimeNs, end.ctimeNs, end.ino, end.dev].join(':') === stamp) prefixProofs.set(key, {stamp, hash: result});
  return result;
}
function scope(value) {
  if (!value || !['continuous_window', 'complete_file', 'sparse_contact_sheet'].includes(value.kind) ||
      !/^[a-f0-9]{64}$/.test(value.source_sha256) || !Number.isFinite(value.source_start_s) ||
      !Number.isFinite(value.source_end_s) || value.source_start_s < 0 || value.source_end_s <= value.source_start_s) fail('bound_scope_required');
  return digest([value.kind, value.source_sha256, value.source_start_s, value.source_end_s]);
}
export function forwardStageParent(name) {
  const match = STAGES.exec(name); if (!match) fail('stage_not_authorized'); return Number(match[1]);
}
function parsed(root, call) {
  if (!call || call.status !== 'received') fail('predecessor_not_received');
  const file = path.join(root, 'calls', call.id, 'parsed.json');
  if (!fs.existsSync(file)) fail('predecessor_not_parsed');
  if (fs.existsSync(path.join(root, 'calls', call.id, 'protocol_failure.json'))) fail('failed_reply_cannot_be_relabelled');
  const value = read(file), reply = read(path.join(root, 'calls', call.id, 'response.json'));
  let raw = reply.result.content.filter(c => c.type === 'text').map(c => c.text).join('\n').trim();
  if (raw.startsWith('```')) {
    const match = /^```(?:json)?\s*\n([\s\S]+?)\n```$/.exec(raw);
    if (!match) fail('parsed_body_not_original'); raw = match[1];
  }
  if (JSON.stringify(canonical(JSON.parse(raw))) !== JSON.stringify(canonical(value))) fail('parsed_body_not_original');
  return value;
}
function received(root, calls, stem) {
  const original = calls.find(c => c.name === stem);
  const repair = original && calls.find(c => c.repair_of === original.id);
  for (const c of [repair, original]) if (c?.status === 'received' && fs.existsSync(path.join(root, 'calls', c.id, 'parsed.json'))) return parsed(root, c);
  fail('predecessor_not_parsed');
}

export function loadForwardSlot(root, env = process.env) {
  const file = env.OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE;
  if (!file && !env.OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256) return null;
  if (!file || !env.OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256 || fileHash(file) !== env.OMNI_LIBRARY_FORWARD_SLOT_AUTH_SHA256) fail('authorization_modified');
  const stateFile = path.join(root, 'library_state.json'), state = read(stateFile), policy = read(file);
  const entries = state.artifacts?.[POLICY];
  if (entries?.length !== 1 || path.resolve(entries[0].path) !== path.resolve(file) || entries[0].sha256 !== jsonHash(file) ||
      policy.policy !== POLICY || policy.task_id !== state.task_id || path.resolve(policy.original_output) !== path.resolve(root) ||
      policy.input_lock_sha256 !== jsonHash(stateFile, v => v.input_lock) || policy.base_request_limit !== state.max_requests ||
      policy.baseline_policy_version !== state.policy_version || policy.baseline_request_count !== BASELINE ||
      state.calls.length < BASELINE || state.request_count !== state.calls.length ||
      policy.prefix_calls_sha256 !== jsonHash(stateFile, v => v.calls.slice(0, BASELINE)) ||
      JSON.stringify(policy.parent_rounds) !== '[0,3]' || policy.max_concurrent_parents !== 2 ||
      policy.max_slices_per_parent !== 32 || policy.renders_per_parent !== 1 || policy.repairs_per_stage !== 1 ||
      policy.new_unique_windows !== 0 || policy.numeric_total_request_limit !== null || policy.automatic_round_loops !== false ||
      policy.goal_resumed !== false || policy.stage_pattern !== STAGES.source ||
      fileHash(policy.original_authorization_path) !== policy.original_authorization_sha256) fail('policy_or_prefix_changed');
  const grant = read(policy.original_authorization_path);
  if (grant.renders_per_parent !== 1 || grant.repairs_per_stage !== 1 || JSON.stringify(grant.parent_rounds) !== '[0,3]' ||
      path.resolve(policy.execution_directory) !== path.resolve(grant.execution_directory, 'fact_grounded_v1') ||
      JSON.stringify(policy.allowed_render_directories.map(p => path.resolve(p))) !==
        JSON.stringify([0, 3].map(p => path.resolve(policy.execution_directory, `render_${p}`, 'render')))) fail('original_grant_changed');
  const oldRenderDirs = fs.readdirSync(root, {withFileTypes: true}).filter(p => p.isDirectory() && p.name.startsWith('render_')).map(p => p.name).sort();
  if (grant.allowed_render_directories.some(p => fs.existsSync(p)) ||
      JSON.stringify(oldRenderDirs) !== JSON.stringify(policy.baseline_render_directories)) fail('unapproved_original_render_directory');
  for (const [name, rows] of Object.entries(policy.baseline_artifacts)) {
    if (jsonHash(stateFile, v => v.artifacts[name]) !== jsonHash(file, v => v.baseline_artifacts[name])) fail('old_artifacts_changed');
  }
  function relativeFiles(folder) {
    const result = [];
    function visit(dir) { for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) visit(file); else if (entry.isFile()) result.push(path.relative(folder, file));
    } }
    visit(folder); return result.sort();
  }
  for (const [id, names] of Object.entries(policy.baseline_call_files)) if (
      JSON.stringify(relativeFiles(path.join(root, 'calls', id))) !== JSON.stringify(names)) fail('historical_call_files_changed');
  for (const row of policy.protected_files) if (fileHash(row.path) !== row.sha256) fail('history_file_changed');
  for (const row of policy.journal_prefixes) if (prefixHash(row.path, row.bytes) !== row.sha256) fail('journal_prefix_changed');
  const uncertain = state.calls.slice(0, BASELINE).filter(c => c.status === 'uncertain');
  if (JSON.stringify(uncertain.map(c => c.id).sort()) !== JSON.stringify([...FROZEN].sort())) fail('unknown_baseline_changed');
  for (const c of uncertain) if (fs.existsSync(path.join(root, 'calls', c.id, 'response.json')) ||
      fs.existsSync(path.join(root, 'calls', c.id, 'parsed.json'))) fail('unknown_reply_fabricated');
  const names = new Map(), last = new Map(), pending = [];
  for (const c of state.calls.slice(BASELINE)) {
    const match = STAGES.exec(c.name); if (!match || names.has(c.name)) fail('stage_repetition');
    const parent = Number(match[1]);
    if (!['received', 'submitted', 'uncertain', 'failed_known'].includes(c.status)) fail('new_call_status_invalid');
    if (match[3]) {
      const old = names.get(c.name.slice(0, -7));
      if (!old || old.status !== 'received' || c.repair_of !== old.id || last.get(parent) !== old.name) fail('repair_binding_changed');
    } else if (c.repair_of) fail('original_has_repair_parent');
    const requestFile = path.join(root, 'calls', c.id, 'request.json');
    if (jsonHash(requestFile) !== c.request_sha256) fail('new_request_changed');
    if (match[3]) {
      const request = read(requestFile), original = read(path.join(root, 'calls', c.repair_of, 'request.json'));
      if (request.media_sha256 !== original.media_sha256 || scope(request.observation_scope) !== scope(original.observation_scope)) fail('repair_input_changed');
    }
    if (c.status === 'received' && jsonHash(path.join(root, 'calls', c.id, 'response.json')) !== c.response_sha256) fail('new_response_changed');
    if (c.status === 'submitted') pending.push(parent);
    names.set(c.name, c); last.set(parent, c.name);
  }
  if (pending.length > 2 || new Set(pending).size !== pending.length) fail('parallel_pending_limit');
  for (const p of [0, 3]) {
    if (state.artifacts[`sf_${p}_render_claim`]) fail('old_render_claim_conflict');
    const rows = state.artifacts[`sfv2_${p}_render_claim`] || [], directory = path.join(policy.execution_directory, `render_${p}`, 'render');
    if (rows.length > 1 || fs.existsSync(directory) && !rows.length) fail('render_without_claim');
    if (rows.length) {
      const claim = read(rows[0].path);
      if (jsonHash(rows[0].path) !== rows[0].sha256 || claim.directory !== directory || claim.parent_round !== p ||
          claim.max_renders !== 1 || fileHash(claim.plan_path) !== claim.plan_file_sha256 ||
          fileHash(claim.source_manifest_path) !== claim.source_manifest_sha256) fail('render_claim_changed');
    }
  }
  for (const [name, rows] of Object.entries(state.artifacts)) if (name.startsWith('sfv2_')) {
    if (rows.length !== 1 || jsonHash(rows[0].path) !== rows[0].sha256) fail('new_artifact_changed');
    if (name.startsWith('sfv2_result_')) {
      const receipt = read(rows[0].path);
      if (fileHash(receipt.result_path) !== receipt.result_sha256 || receipt.completed_files.some(p => fileHash(p.path) !== p.sha256)) fail('completed_result_changed');
    }
  }
  return {policy, state, file};
}

export function forwardParallelConfiguration(root, env = process.env) {
  const value = loadForwardSlot(root, env);
  return value?.policy ?? null;
}

export function forwardSlotRequestLimit(root, job, env = process.env) {
  const loaded = loadForwardSlot(root, env); if (!loaded) return null;
  const {policy, state} = loaded, added = state.calls.slice(BASELINE);
  const call = added.find(c => c.id === job.job_id), match = call && STAGES.exec(call.name);
  if (!call || call.status !== 'submitted' || !match) fail('submitted_forward_call_required');
  const parent = Number(match[1]), kind = match[2], own = added.filter(c => forwardStageParent(c.name) === parent);
  if (own.at(-1) !== call || added.some(c => c !== call && (!['received', 'submitted'].includes(c.status) ||
      c.status === 'submitted' && forwardStageParent(c.name) === parent))) fail('new_unknown_or_own_pending');
  if (state.artifacts[`sfv2_result_${parent}`]) fail('finished_parent_read_only');
  if (match[3]) {
    const old = own.at(-2);
    if (!old || old.name !== call.name.slice(0, -7) || old.id !== call.repair_of || old.status !== 'received' ||
        old.repair_of || added.filter(c => c.repair_of === old.id).length !== 1) fail('sole_repair_required');
  } else {
    if (call.repair_of || added.filter(c => c.name === call.name).length !== 1) fail('stage_repetition');
    if (own.length > 1) parsed(root, own.at(-2));
    if (kind === 'reconstruct') { if (own.length !== 1) fail('one_reconstruction_only'); }
    else {
      received(root, own, `sfv2_${parent}_reconstruct`);
      if (kind.startsWith('slice_')) {
        if (own.some(c => /_(compare|blind|economy|review)(?:_repair)?$/.test(c.name)) ||
            own.filter(c => /_slice_[a-f0-9]{16}$/.test(c.name)).length > 32) fail('slice_stage_or_count');
      } else if (kind === 'compare') {
        if (own.some(c => /_(blind|economy|review)(?:_repair)?$/.test(c.name))) fail('compare_after_review');
      } else {
        received(root, own, `sfv2_${parent}_compare`);
        if (!state.artifacts[`sfv2_${parent}_render_claim`]) fail('review_requires_render_claim');
        if (['economy', 'review'].includes(kind)) received(root, own, `sfv2_${parent}_blind`);
        if (kind === 'review') received(root, own, `sfv2_${parent}_economy`);
      }
    }
  }
  const requestFile = path.join(root, 'calls', call.id, 'request.json'), request = read(requestFile);
  const queued = read(path.join(root, 'mcp_queue', call.id + '.request.json'));
  if (request.provider !== 'official_vision_mcp_in_codex' || request.tool !== 'analyze_video' ||
      !request.arguments?.video_source || fileHash(request.arguments.video_source) !== request.media_sha256 ||
      queued.job_id !== job.job_id || queued.tool !== request.tool ||
      JSON.stringify(canonical(queued.arguments)) !== JSON.stringify(canonical(request.arguments))) fail('actual_request_binding');
  const fingerprint = scope(request.observation_scope), lineage = path.join(path.dirname(request.arguments.video_source), 'lineage.json');
  if (fs.existsSync(lineage) && scope(read(lineage)) !== fingerprint) fail('actual_lineage_changed');
  for (const row of policy.unknown_inputs) if (call.request_sha256 === row.request_sha256 ||
      request.media_sha256 === row.media_sha256 || fingerprint === scope(row.scope)) fail('unknown_input_replay');
  if (kind.startsWith('slice_') && !match[3] && policy.exhausted_source_inputs.some(row => scope(row.scope) === fingerprint)) fail('exhausted_source_replay');
  return Infinity;
}

export function forwardFrozenSlotJob(root, env = process.env) {
  if (!env.OMNI_LIBRARY_FORWARD_SLOT_AUTH_FILE) return null;
  const loaded = loadForwardSlot(root, env), policy = loaded.policy;
  const paths = new Set(policy.unknown_inputs.map(row => path.resolve(root, 'mcp_queue', row.call_id + '.request.json')));
  return requestFile => {
    if (!paths.has(path.resolve(requestFile))) return false;
    const current = loadForwardSlot(root, env);
    const job = read(requestFile), old = current.state.calls.slice(0, BASELINE).find(c => c.id === job.job_id);
    if (!old || old.status !== 'uncertain' || !FROZEN.includes(old.id)) fail('frozen_queue_binding_changed');
    const request = read(path.join(root, 'calls', old.id, 'request.json'));
    if (job.tool !== request.tool || JSON.stringify(canonical(job.arguments)) !== JSON.stringify(canonical(request.arguments))) fail('frozen_queue_request_changed');
    return true;
  };
}

// Shared read-only byte/JSON proofs; exporting these does not activate a policy.
export {jsonHash, fileHash, prefixHash, scope, canonical, parsed, received};
