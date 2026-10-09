// One explicitly authorized correction of the settled zero-length evidence pair.
// Reading this proof never changes old calls, retries a POST, or grants a round.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';

const KEY = 'server_interval_resume';
const POLICY = 'opencode_known_interval_resume_v1';
const ALIAS = 'semantic_interval_resume_v1';
const COUNT = 29;
const SCHEMA = {key: 'server_schema_resume', policy: 'opencode_known_schema_resume_v1',
  alias: 'semantic_schema_resume_v1', count: 39, instruction: '继续',
  stage: 'semantic_slice_0_f8a4f183a2d3df76'};
const REPAIR_MARKER = '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。';
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const require = (ok, reason) => {if (!ok) throw new Error('server_interval_resume_' + reason);};
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

function inside(root, file) {
  require(typeof file === 'string' && path.isAbsolute(file), 'task_path_invalid');
  const relative = path.relative(fs.realpathSync(root), fs.realpathSync(file));
  require(relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative),
    'file_outside_task');
  return file;
}

function recorded(root, call, filename, digest) {
  require(/^[A-Za-z0-9_-]+$/.test(call.id || ''), 'unsafe_call_id');
  const raw = fs.readFileSync(inside(root, path.join(root, 'calls', call.id, filename)), 'utf8');
  require(/^[a-f0-9]{64}$/.test(digest || '') && persistedJsonHash(raw) === digest, 'old_call_file_changed');
  return JSON.parse(raw);
}

function authorization(root, state, spec) {
  const {key, policy, alias, count, instruction} = spec;
  const entries = state.artifacts?.[key];
  if (!entries?.length) return null;
  require(entries.length === 1, 'duplicate_authorization');
  const raw = fs.readFileSync(inside(root, entries[0].path), 'utf8');
  require(persistedJsonHash(raw) === entries[0].sha256, 'authorization_changed');
  const auth = JSON.parse(raw), config = state.input_lock?.configuration;
  require(auth.policy === policy && auth.output === path.resolve(root) && auth.task_id === state.task_id &&
    same(auth.input_lock, state.input_lock) && state.max_requests === null &&
    auth.baseline_request_count === count && /^semantic_slice_[01]_[a-f0-9]{16}$/.test(auth.stage || '') &&
    (!spec.stage || auth.stage === spec.stage) &&
    auth.alias === alias && auth.user_instruction === instruction && auth.new_rounds === 0 &&
    auth.max_rounds === 2 && auth.max_renders === 2 && auth.max_fine === 16 &&
    config?.max_rounds === auth.max_rounds && config.max_renders === auth.max_renders &&
    config.max_fine === auth.max_fine && auth.no_unknown_replay === true &&
    auth.alias_original_and_one_repair_only === true, 'scope_changed');
  const baseline = inside(root, auth.baseline_state_path);
  require(sha(fs.readFileSync(baseline)) === auth.baseline_state_sha256, 'baseline_changed');
  const old = read(baseline);
  require(old.request_count === count && old.calls?.length === count && old.task_id === state.task_id &&
    old.max_requests === null && same(old.input_lock, state.input_lock) &&
    same(old.calls, state.calls?.slice(0, count)) && old.calls.every(call => call.status === 'received') &&
    state.request_count === state.calls.length && state.request_count >= count &&
    new Set(state.calls.map(call => call.id)).size === state.calls.length, 'history_changed');
  require(Object.entries(old.artifacts || {}).every(([key, prior]) =>
    same(prior, (state.artifacts?.[key] || []).slice(0, prior.length))), 'old_artifacts_changed');
  require(Array.isArray(auth.protected_files) && auth.protected_files.length > 0 &&
    Array.isArray(auth.runtime_files) && auth.runtime_files.length > 0, 'bound_files_required');
  for (const item of auth.protected_files) {
    inside(root, item.path);
    require(sha(fs.readFileSync(item.path)) === item.sha256, 'original_file_changed');
  }
  for (const item of auth.runtime_files) {
    require(typeof item.path === 'string' && path.isAbsolute(item.path) &&
      sha(fs.readFileSync(item.path)) === item.sha256, 'runtime_file_changed');
  }
  require(Number.isSafeInteger(auth.http_prefix_bytes) && auth.http_prefix_bytes > 0, 'HTTP_prefix_invalid');
  const prefix = Buffer.alloc(auth.http_prefix_bytes), fd = fs.openSync(path.join(root, 'mcp_http.jsonl'), 'r');
  let bytes;
  try {bytes = fs.readSync(fd, prefix, 0, prefix.length, 0);} finally {fs.closeSync(fd);}
  require(bytes === prefix.length && sha(prefix) === auth.http_prefix_sha256, 'old_HTTP_journal_changed');
  const pair = old.calls.slice(-2);
  require(pair[0].name === auth.stage && pair[1].name === auth.stage + '_repair' &&
    pair[0].repair_of == null && pair[1].repair_of === pair[0].id &&
    old.calls.filter(call => call.repair_of === pair[0].id).length === 1, 'original_pair_changed');
  let original;
  for (const call of old.calls) {
    const request = recorded(root, call, 'request.json', call.request_sha256);
    recorded(root, call, 'response.json', call.response_sha256);
    if (call.id === pair[0].id) original = request;
  }
  const expected = auth.expected_request;
  require(expected?.arguments && typeof expected.arguments.prompt === 'string' &&
    expected.arguments.prompt.startsWith(original.arguments.prompt) &&
    expected.arguments.prompt.length > original.arguments.prompt.length &&
    same(original, {...expected, arguments: {...expected.arguments, prompt: original.arguments.prompt}}),
    'alias_input_binding_changed');
  const aliases = state.calls.slice(count).filter(call => call.name?.startsWith(alias));
  require(aliases.length <= 2 && new Set(aliases.map(call => call.name)).size === aliases.length &&
    aliases.every(call => [alias, alias + '_repair'].includes(call.name)) &&
    !state.calls.slice(count).some(call => [auth.stage, auth.stage + '_repair'].includes(call.name)),
    'alias_limit_changed');
  for (const [index, call] of aliases.entries()) {
    require(call.name === (index === 0 ? alias : alias + '_repair') &&
      (index === 0 ? call.repair_of == null : call.repair_of === aliases[0].id), 'alias_pair_changed');
    const request = recorded(root, call, 'request.json', call.request_sha256);
    require(index === 0 ? same(request, expected) :
      same(expected, {...request, arguments: {...request.arguments, prompt: expected.arguments.prompt}}) &&
      request.arguments.prompt.startsWith(expected.arguments.prompt + REPAIR_MARKER), 'alias_request_changed');
    if (call.status === 'received') recorded(root, call, 'response.json', call.response_sha256);
  }
  return {authorization: auth, baseline: old};
}

export function intervalResume(root) {
  const stateFile = path.join(root, 'library_state.json');
  if (!fs.existsSync(stateFile)) return null;
  const state = read(stateFile);
  const parent = authorization(root, state, {key: KEY, policy: POLICY, alias: ALIAS,
    count: COUNT, instruction: '那接着后续剪辑'});
  if (!state.artifacts?.[SCHEMA.key]?.length) return parent;
  require(parent !== null, 'schema_parent_required');
  const latest = authorization(root, state, SCHEMA);
  require(latest.authorization.parent_authorization_sha256 === state.artifacts[KEY][0].sha256 &&
    same(latest.baseline.artifacts?.[KEY], state.artifacts[KEY]), 'schema_parent_changed');
  return latest;
}
