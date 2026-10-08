// Read-only transport isolation for the one explicitly frozen, lost slot reply.
// Skipping its old queue entry never creates a reply or submits a model request.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {forwardFrozenSlotJob} from './mcp_forward_slot_guard.mjs';
import {parentCutFrozenSlotJob} from './mcp_parent_cut_guard.mjs';
import {microclipFrozenSlotJob} from './mcp_microclip_guard.mjs';

const POLICY = 'sf_independent_slot_recovery_v1';
const FROZEN_JOB = 'glm_166_sf_3_source_feedback_replan_v1';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const fileHash = file => hash(fs.readFileSync(file));
const fail = name => { throw new Error('library_mcp_frozen_slot_' + name); };
class NumericLiteral { constructor(token) { this.token = token; } }
function jsonFileHash(file, select = value => value) {
  // Preserve the Python ledger's numeric lexemes (for example 0.0 versus 0).
  const value = JSON.parse(fs.readFileSync(file, 'utf8'), (key, item, context) => {
    if (typeof item !== 'number') return item;
    if (!context?.source) fail('numeric_lexemes_unavailable');
    return new NumericLiteral(context.source);
  });
  function serialize(item) {
    if (item instanceof NumericLiteral) return item.token;
    if (Array.isArray(item)) return '[' + item.map(serialize).join(',') + ']';
    if (item && typeof item === 'object') return '{' + Object.keys(item).sort()
      .map(key => JSON.stringify(key) + ':' + serialize(item[key])).join(',') + '}';
    return JSON.stringify(item);
  }
  return hash(serialize(select(value)));
}

export function frozenSlotJob(root, env = process.env) {
  const microclip = microclipFrozenSlotJob(root, env);
  if (microclip) return microclip;
  const parent = parentCutFrozenSlotJob(root, env);
  if (parent) return parent;
  const forward = forwardFrozenSlotJob(root, env);
  if (forward) return forward;
  const file = env.OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE;
  const digest = env.OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256;
  if (!file && !digest) return () => false;
  if (!file || !digest || fileHash(file) !== digest) fail('authorization_changed');
  const policy = read(file), statePath = path.join(root, 'library_state.json'), state = read(statePath);
  const entries = state.artifacts?.[POLICY], binding = policy.transport_binding;
  if (entries?.length !== 1 || path.resolve(entries[0].path) !== path.resolve(file) ||
      entries[0].sha256 !== jsonFileHash(file) || policy.policy !== POLICY ||
      policy.task_id !== state.task_id || policy.input_lock_sha256 !== jsonFileHash(statePath, v => v.input_lock) ||
      policy.baseline_request_count !== 166 || state.calls.length < 166 ||
      policy.prefix_calls_sha256 !== jsonFileHash(statePath, v => v.calls.slice(0, 166)) ||
      !Array.isArray(policy.frozen_unknown_call_ids) ||
      JSON.stringify(policy.frozen_unknown_call_ids) !== JSON.stringify([
        'glm_004_coarse_978d5360_01', 'glm_131_active_10_draft', FROZEN_JOB]) ||
      binding?.call_id !== FROZEN_JOB) fail('scope_changed');
  const old = state.calls.find(c => c.id === FROZEN_JOB);
  if (!old || old.status !== 'uncertain' || old.request_sha256 !== binding.request_sha256 ||
      state.calls[165].id !== FROZEN_JOB) fail('ledger_changed');
  const queue = path.resolve(root, 'mcp_queue');
  const requestPath = path.join(queue, FROZEN_JOB + '.request.json');
  const startedPath = path.join(queue, FROZEN_JOB + '.started.json');
  if (path.resolve(binding.queue_request_path) !== requestPath || path.resolve(binding.queue_started_path) !== startedPath ||
      path.resolve(binding.call_request_path) !== path.resolve(root, 'calls', FROZEN_JOB, 'request.json')) fail('queue_binding_changed');
  const verifyFiles = () => {
    for (const [name, sha] of [['queue_request_path', 'queue_request_byte_sha256'],
      ['queue_started_path', 'queue_started_byte_sha256'], ['call_request_path', 'call_request_byte_sha256'],
      ['reclassification_path', 'reclassification_byte_sha256']]) {
      if (!binding[name] || !binding[sha] || fileHash(binding[name]) !== binding[sha]) fail('frozen_input_changed');
    }
    if (jsonFileHash(binding.call_request_path) !== old.request_sha256 || read(requestPath).job_id !== FROZEN_JOB) {
      fail('request_changed');
    }
  };
  verifyFiles();
  return requestFile => {
    if (path.resolve(requestFile) !== requestPath) return false;
    verifyFiles();
    const current = read(statePath), call = current.calls.find(c => c.id === FROZEN_JOB);
    if (call?.status !== 'uncertain' || call.request_sha256 !== binding.request_sha256 ||
        jsonFileHash(statePath, v => v.calls.slice(0, 166)) !== policy.prefix_calls_sha256) fail('ledger_changed');
    return true;
  };
}
