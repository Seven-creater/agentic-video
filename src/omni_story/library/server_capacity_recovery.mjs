// Read-only proof of the one explicitly registered known-output capacity remedy.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const require = (ok, reason) => {if (!ok) throw new Error('server_capacity_recovery_' + reason);};
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (a,b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const GENERATION = {policy:'opencode_vision_capacity_v2',max_output_tokens:32768,
  model_timeout_ms:1200000,tool_timeout_ms:1260000};
export const CAPACITY_SUFFIX = '\n本次是已记录的生成容量修正：前两次请求均返回已知的空内容及 length，'
  + '现在官方生成上限为32768。请简洁思考，优先完整输出要求的JSON；保留全部必需字段、'
  + '来源绑定和画面证据，勿重复复述上下文。不得编造素材或修改创作目标。';

export function capacityRecovery(root) {
  const file = path.join(root, 'library_state.json');
  if (!fs.existsSync(file)) return null;
  const state = read(file);
  const entries = state.artifacts?.server_output_capacity_recovery;
  if (!entries?.length) return null;
  require(entries.length === 1, 'duplicate_authorization');
  const inside = file => {
    const relative = path.relative(fs.realpathSync(root), fs.realpathSync(file));
    require(relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative),
      'file_outside_task');
  };
  inside(entries[0].path);
  const raw = fs.readFileSync(entries[0].path, 'utf8');
  require(persistedJsonHash(raw) === entries[0].sha256, 'authorization_changed');
  const auth = JSON.parse(raw);
  require(auth.policy === 'opencode_known_output_starvation_recovery_v1' &&
    auth.stage === 'plan_0' && auth.alias === 'plan_0_capacity_v2' &&
    auth.recovery_name === 'output_capacity_v1' && auth.output === path.resolve(root) &&
    auth.task_id === state.task_id && state.max_requests === null &&
    same(auth.input_lock, state.input_lock) && same(auth.generation, GENERATION) &&
    auth.prompt_suffix === CAPACITY_SUFFIX && auth.new_rounds === 0 &&
    auth.candidate_limit_unchanged === 2 && auth.fine_window_limit_unchanged === 16 &&
    auth.no_unknown_replay === true && auth.alias_original_and_one_repair_only === true,
    'scope_changed');
  inside(auth.baseline_state_path);
  require(sha(fs.readFileSync(auth.baseline_state_path)) === auth.baseline_state_sha256, 'baseline_changed');
  const old = read(auth.baseline_state_path), count = auth.baseline_request_count;
  require(Number.isSafeInteger(count) && count >= 2 && old.request_count === count &&
    old.calls.length === count && old.task_id === state.task_id && old.max_requests === null &&
    same(old.input_lock, state.input_lock) && same(old.calls, state.calls.slice(0,count)) &&
    old.calls.every(call => call.status === 'received') && state.request_count === state.calls.length,
    'history_changed');
  require(Object.entries(old.artifacts || {}).every(([key, entries]) =>
    same(entries, (state.artifacts?.[key] || []).slice(0,entries.length))), 'old_artifacts_changed');
  for (const item of auth.protected_files) {
    inside(item.path);
    require(sha(fs.readFileSync(item.path)) === item.sha256, 'original_file_changed');
  }
  require(sha(fs.readFileSync(auth.user_authorization_path)) === auth.user_authorization_sha256,
    'user_authorization_changed');
  require(Number.isSafeInteger(auth.http_prefix_bytes) && auth.http_prefix_bytes > 0, 'HTTP_prefix_invalid');
  const prefix = Buffer.alloc(auth.http_prefix_bytes);
  const fd = fs.openSync(path.join(root,'mcp_http.jsonl'),'r');
  let bytes;
  try {bytes = fs.readSync(fd,prefix,0,prefix.length,0);} finally {fs.closeSync(fd);}
  require(bytes === prefix.length && sha(prefix) === auth.http_prefix_sha256, 'old_HTTP_journal_changed');
  const pair = old.calls.slice(-2);
  require(pair[0].name === auth.stage && pair[1].name === auth.stage + '_repair' &&
    pair[0].repair_of === null && pair[1].repair_of === pair[0].id, 'original_pair_changed');
  for (const call of pair) {
    const requestPath = path.join(root,'calls',call.id,'request.json');
    const replyPath = path.join(root,'calls',call.id,'response.json');
    require(persistedJsonHash(fs.readFileSync(requestPath,'utf8')) === call.request_sha256 &&
      persistedJsonHash(fs.readFileSync(replyPath,'utf8')) === call.response_sha256,
      'original_pair_changed');
    const reply = read(replyPath);
    require(reply.finish_reason === 'length' &&
      !reply.result.content.map(c => c.text || '').join(''), 'not_known_empty_length');
  }
  const aliases = state.calls.slice(count).filter(c => c.name.startsWith(auth.alias));
  require(aliases.length <= 2 && new Set(aliases.map(c => c.name)).size === aliases.length &&
    aliases.every(c => [auth.alias,auth.alias+'_repair'].includes(c.name)), 'alias_limit_changed');
  return {generation:auth.generation,authorization:auth,baseline:old};
}
