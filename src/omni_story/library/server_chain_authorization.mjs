// Read-only binding of the explicitly authorized, same-task rough-to-fine chain.
// This does not register permission, rewrite old replies, or grant another round.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';

const KEY = 'server_one_chain_e2e_authorization';
const PREFIX = 'chain_e2e_v1_';
const COUNT = 35;
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const require = (ok, reason) => {if (!ok) throw new Error('server_chain_authorization_' + reason);};
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

// Four local inspections, each with at most 36 PTS frames in six-frame pages.
// A single actual-output revision uses the literal _r suffix, never a new round.
export function chainStage(stem) {
  return typeof stem === 'string' && /^chain_e2e_v1_(?:rough_(?:blind|review)|fine_(?:observe|inspect_[0-3]|detail_[0-3]_[0-5]|plan|blind(?:_r)?|review(?:_r)?|revise))$/.test(stem);
}

export function chainDependencies(stem) {
  require(chainStage(stem), 'unbound_or_replayed_new_call');
  const stage = stem.slice(PREFIX.length);
  const fixed = {rough_blind: [], rough_review: ['rough_blind'], fine_observe: ['rough_review'],
    fine_plan: ['fine_observe'], fine_blind: ['fine_plan'], fine_review: ['fine_blind'],
    fine_revise: ['fine_review'], fine_blind_r: ['fine_revise'], fine_review_r: ['fine_blind_r']};
  if (fixed[stage]) return fixed[stage].map(name => PREFIX + name);
  const inspect = /^fine_inspect_([0-3])$/.exec(stage);
  if (inspect) return [PREFIX + 'fine_observe', ...(Number(inspect[1]) > 0 ?
    [PREFIX + 'fine_inspect_' + (Number(inspect[1]) - 1)] : [])];
  const detail = /^fine_detail_([0-3])_([0-5])$/.exec(stage);
  return [PREFIX + 'fine_inspect_' + detail[1], ...(Number(detail[2]) > 0 ?
    [PREFIX + 'fine_detail_' + detail[1] + '_' + (Number(detail[2]) - 1)] : [])];
}

function inside(root, file) {
  require(typeof file === 'string' && path.isAbsolute(file), 'task_path_invalid');
  const absolute = path.resolve(file), relative = path.relative(root, absolute);
  require(relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative),
    'file_outside_task');
  let cursor = absolute;
  while (cursor !== root) {
    require(!fs.lstatSync(cursor).isSymbolicLink(), 'linked_task_file');
    cursor = path.dirname(cursor);
  }
  require(fs.realpathSync(absolute) === absolute, 'linked_task_file');
  return absolute;
}

function record(root, call, filename, digest) {
  require(typeof call.id === 'string' && /^[A-Za-z0-9_-]+$/.test(call.id), 'unsafe_call_id');
  const raw = fs.readFileSync(inside(root, path.join(root, 'calls', call.id, filename)), 'utf8');
  require(/^[a-f0-9]{64}$/.test(digest || '') && persistedJsonHash(raw) === digest,
    'recorded_' + filename.replace('.json', '') + '_changed');
  return JSON.parse(raw);
}

function parsedReply(root, call) {
  require(call?.status === 'received', 'stage_dependency_not_parsed');
  const file = path.join(root, 'calls', call.id, 'parsed.json');
  require(fs.existsSync(file), 'stage_dependency_not_parsed');
  const parsed = read(inside(root, file));
  const reply = record(root, call, 'response.json', call.response_sha256);
  let text = reply.result?.content?.filter(item => item.type === 'text').map(item => item.text).join('\n').trim();
  require(typeof text === 'string', 'parsed_reply_changed');
  if (text.startsWith('```')) {
    const fenced = /^```(?:json)?\s*\n([\s\S]+?)\n```$/.exec(text);
    require(fenced, 'parsed_reply_changed');
    text = fenced[1];
  }
  const actual = JSON.parse(text);
  require(actual && typeof actual === 'object' && !Array.isArray(actual) && same(actual, parsed), 'parsed_reply_changed');
  return parsed;
}

function callFiles(root, calls) {
  const files = [];
  const visit = directory => {
    for (const item of fs.readdirSync(directory, {withFileTypes: true})) {
      const file = inside(root, path.join(directory, item.name));
      if (item.isDirectory()) visit(file);
      else if (item.isFile()) files.push(file);
    }
  };
  for (const call of calls) {
    record(root, call, 'request.json', call.request_sha256);
    record(root, call, 'response.json', call.response_sha256);
    visit(inside(root, path.join(root, 'calls', call.id)));
  }
  return files.sort();
}

function newCalls(root, calls, old) {
  const digests = new Set(old.calls.map(call => call.request_sha256));
  const names = new Set(), repairs = new Set(), accepted = new Map();
  const terminal = old.calls.slice(-2).map(call => record(root, call, 'request.json', call.request_sha256));
  let previous;
  for (const call of calls.slice(COUNT)) {
    require(!previous || previous.status === 'received', 'new_work_after_unsettled_call');
    const repairing = typeof call.name === 'string' && call.name.endsWith('_repair');
    const stem = repairing ? call.name.slice(0, -7) : call.name;
    require(chainStage(stem) && !names.has(call.name) && !digests.has(call.request_sha256),
      'unbound_or_replayed_new_call');
    const request = record(root, call, 'request.json', call.request_sha256);
    const scope = request.observation_scope || {};
    require(terminal.every(prior => request.media_sha256 !== prior.media_sha256 &&
      !(scope.source_sha256 === prior.observation_scope?.source_sha256 &&
        scope.source_start_s === prior.observation_scope?.source_start_s &&
        scope.source_end_s === prior.observation_scope?.source_end_s)), 'old_terminal_slice_replayed');
    if (repairing) {
      require(previous?.name === stem && previous.repair_of == null &&
        call.repair_of === previous.id && !repairs.has(previous.id), 'extra_or_unbound_repair');
      const original = record(root, previous, 'request.json', previous.request_sha256);
      require(same(original, {...request, arguments: {...request.arguments, prompt: original.arguments?.prompt}}) &&
        typeof request.arguments?.prompt === 'string' && request.arguments.prompt.startsWith(original.arguments.prompt +
          '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'), 'repair_input_changed');
      repairs.add(previous.id);
    } else {
      require(call.repair_of == null && !names.has(stem + '_repair'), 'duplicate_original_stage');
      for (const name of chainDependencies(stem)) {
        require(names.has(name), 'stage_dependency_missing');
        parsedReply(root, accepted.get(name));
      }
    }
    if (call.status === 'received') record(root, call, 'response.json', call.response_sha256);
    require(['received', 'submitted', 'uncertain', 'failed_known'].includes(call.status), 'new_call_status_invalid');
    names.add(call.name); accepted.set(stem, call); digests.add(call.request_sha256); previous = call;
  }
}

export function chainAuthorization(root) {
  root = fs.realpathSync(root);
  const state = read(path.join(root, 'library_state.json'));
  const entries = state.artifacts?.[KEY];
  if (!entries?.length) return null;
  require(entries.length === 1, 'duplicate_authorization');
  const raw = fs.readFileSync(inside(root, entries[0].path), 'utf8');
  require(persistedJsonHash(raw) === entries[0].sha256, 'authorization_changed');
  const auth = JSON.parse(raw);
  require(auth.policy === 'opencode_one_chain_e2e_v1' && auth.recovery_name === 'one_chain_e2e_v1' &&
    auth.stage_prefix === PREFIX && auth.output === root && auth.task_id === state.task_id &&
    same(auth.input_lock, state.input_lock) && auth.baseline_request_count === COUNT &&
    same(auth.render_grant, {rough: 1, fine: 2, total: 3}) && auth.new_candidate_rounds === 0 &&
    auth.max_fine_actual_revisions === 1 && auth.repairs_per_stage === 1 &&
    auth.no_unknown_replay === true && auth.goal_resumed === false && auth.old_wrapper_consumed === false &&
    !state.artifacts?.server_slice_uncertainty_wrapper_reconciliation?.length &&
    typeof auth.user_instruction === 'string' && auth.user_instruction.trim().length > 0 &&
    state.max_requests === null, 'authorization_scope_changed');
  require(auth.execution_directory === path.join(root, 'artifacts', 'one_chain_e2e_v1') &&
    auth.rough_directory === path.join(root, 'artifacts', 'one_chain_e2e_v1', 'rough') &&
    auth.fine_target_duration_s === auth.reference?.duration_s && auth.fine_target_tolerance_s === 2 &&
    auth.source_integrity_policy === 'actual_SHA_at_registration_then_immutable_catalog_and_stat_on_load' &&
    auth.semantic_joint_policy === 'actual_rough_content_and_character_echo_then_picture_only_fine_review' &&
    auth.rough_edl_is_original_model_plan === true && auth.old_152_second_refinement_is_not_fine_input === true &&
    auth.original_failures_preserved === true, 'chain_policy_changed');
  const baseline = inside(root, auth.baseline_state_path);
  require(sha(fs.readFileSync(baseline)) === auth.baseline_state_sha256, 'baseline_changed');
  const old = read(baseline);
  require(old.request_count === COUNT && old.calls.length === COUNT && old.max_requests === null &&
    old.task_id === state.task_id && same(old.input_lock, state.input_lock) &&
    same(old.calls, state.calls.slice(0, COUNT)) && old.calls.every(call => call.status === 'received') &&
    state.request_count === state.calls.length && state.calls.length >= COUNT &&
    new Set(state.calls.map(call => call.id)).size === state.calls.length &&
    Object.entries(old.artifacts || {}).every(([key, rows]) => same(rows, (state.artifacts?.[key] || []).slice(0, rows.length))),
    'history_changed');
  require(Array.isArray(auth.protected_files) && new Set(auth.protected_files.map(item => item.path)).size === auth.protected_files.length,
    'protected_file_set_invalid');
  for (const item of auth.protected_files)
    require(sha(fs.readFileSync(inside(root, item.path))) === item.sha256, 'old_file_changed');
  const oldFiles = callFiles(root, old.calls);
  require(same(oldFiles, auth.old_call_files) && oldFiles.every(file => auth.protected_files.some(item => item.path === file)),
    'old_call_file_set_changed');
  require(Number.isSafeInteger(auth.http_prefix_bytes) && auth.http_prefix_bytes > 0, 'HTTP_prefix_invalid');
  const prefix = Buffer.alloc(auth.http_prefix_bytes), fd = fs.openSync(path.join(root, 'mcp_http.jsonl'), 'r');
  let bytes;
  try {bytes = fs.readSync(fd, prefix, 0, prefix.length, 0);} finally {fs.closeSync(fd);}
  require(bytes === prefix.length && sha(prefix) === auth.http_prefix_sha256, 'old_HTTP_prefix_changed');
  const catalog = read(inside(root, path.join(root, 'catalog/inventory.json')));
  const reference = read(inside(root, path.join(root, 'reference_catalog/inventory.json'))).sources[0];
  const windows = read(inside(root, path.join(root, 'watched_windows.json')));
  const rough = read(inside(root, path.join(root, 'draft_plan_1.json')));
  require(same(auth.catalog, catalog) && same(auth.reference, reference) && same(auth.windows, windows) &&
    windows.length === 12 && new Set(windows.map(item => item.window_id)).size === 12 &&
    same(auth.rough_plan, rough) && persistedJsonHash(fs.readFileSync(path.join(root, 'draft_plan_1.json'), 'utf8')) === auth.rough_plan_sha256,
    'input_evidence_changed');
  const paid = old.calls.filter(call => ['plan_1', 'plan_1_repair'].includes(call.name)).at(-1);
  require(paid && paid.id === auth.paid_rough_plan_call_id && paid.request_sha256 === auth.paid_rough_plan_request_sha256 &&
    paid.response_sha256 === auth.paid_rough_plan_response_sha256 &&
    same(read(inside(root, path.join(root, 'calls', paid.id, 'parsed.json'))), rough), 'rough_plan_paid_reply_changed');
  // Node 22 reviver source retains Python nanosecond integers beyond 2^53.
  const exact = JSON.parse(raw, (key, value, context) => key === 'mtime_ns' ? context.source : value);
  const sources = [...catalog.sources, reference];
  require(Array.isArray(auth.source_files) && auth.source_files.length === sources.length, 'source_file_set_changed');
  for (let index = 0; index < sources.length; index++) {
    const source = sources[index], item = auth.source_files[index];
    require(path.isAbsolute(item.path) && fs.realpathSync(item.path) === item.path &&
      item.path === source.path && item.sha256 === source.sha256 && /^[a-f0-9]{64}$/.test(item.sha256), 'source_binding_changed');
    const stat = fs.statSync(item.path, {bigint: true});
    require(stat.isFile() && stat.size === BigInt(item.size_bytes) &&
      stat.mtimeNs === BigInt(exact.source_files[index].mtime_ns), 'source_stat_changed');
  }
  newCalls(root, state.calls, old);
  return {authorization: auth, baseline: old};
}
