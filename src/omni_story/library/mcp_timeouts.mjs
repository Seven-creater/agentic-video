// Forward connection settings come only from the bound independent-input strategy.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {capacityRecovery} from './server_capacity_recovery.mjs';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])])) : value;
const GENERATION = Object.freeze({policy:'opencode_vision_capacity_v2', max_output_tokens:32768,
  model_timeout_ms:1200000, tool_timeout_ms:1260000});
export function validateVisionGeneration(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== Object.keys(GENERATION).length ||
      Object.entries(GENERATION).some(([key, expected]) => value[key] !== expected ||
        (key !== 'policy' && !Number.isSafeInteger(value[key])))) {
    throw new Error('server_vision_generation_invalid');
  }
  return Object.freeze({...value});
}
export function visionGeneration(root) {
  const file = path.join(root, 'library_state.json');
  if (!fs.existsSync(file)) return null;
  const config = JSON.parse(fs.readFileSync(file, 'utf8')).input_lock?.configuration;
  let generation;
  if (config && Object.hasOwn(config, 'vision_generation')) generation = config.vision_generation;
  else {
    const recovery = capacityRecovery(root);
    if (!recovery) return null;
    generation = recovery.generation;
  }
  if (config.provider !== 'official_vision_mcp_in_opencode') {
    throw new Error('server_vision_generation_wrong_provider');
  }
  return validateVisionGeneration(generation);
}
export function connectionTimeouts(root, env = process.env) {
  const generation = visionGeneration(root);
  if (generation) return {modelTimeoutMs:generation.model_timeout_ms,
    toolTimeoutMs:generation.tool_timeout_ms, policy:generation.policy};
  const file = env.OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_FILE;
  const digest = env.OMNI_LIBRARY_INDEPENDENT_SOURCE_AUTH_SHA256;
  if (!file && !digest) return {modelTimeoutMs: 600000, toolTimeoutMs: 660000, policy: null};
  if (!file || !digest || hash(fs.readFileSync(file)) !== digest) throw new Error('independent_timeout_authorization_changed');
  const state = JSON.parse(fs.readFileSync(path.join(root, 'library_state.json'), 'utf8'));
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  const entries = state.artifacts.goal_research_independent_11;
  if (entries?.length !== 1 || path.resolve(entries[0].path) !== path.resolve(file) ||
      entries[0].sha256 !== hash(JSON.stringify(canonical(record))) ||
      record.policy !== 'independent_library_evidence_after_unknown_v1' || record.round !== 11 ||
      record.task_id !== state.task_id || record.input_lock_sha256 !== hash(JSON.stringify(canonical(state.input_lock))) ||
      record.model_timeout_ms !== 1200000 || record.tool_timeout_ms !== 1260000 ||
      record.reference_media_forbidden !== true || record.old_calls_unchanged !== true ||
      hash(fs.readFileSync(record.baseline_state_path)) !== record.baseline_state_sha256) {
    throw new Error('independent_timeout_scope_changed');
  }
  const old = JSON.parse(fs.readFileSync(record.baseline_state_path, 'utf8'));
  if (record.baseline_request_count !== 131 || record.activation_baseline_requests !== 131 ||
      JSON.stringify(canonical(old.calls)) !== JSON.stringify(canonical(state.calls.slice(0, 131)))) {
    throw new Error('independent_timeout_history_changed');
  }
  return {modelTimeoutMs: record.model_timeout_ms, toolTimeoutMs: record.tool_timeout_ms, policy: record.policy};
}
