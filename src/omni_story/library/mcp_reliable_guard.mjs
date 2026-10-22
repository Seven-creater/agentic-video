// A finite reliable job permits received history and proven transient failures.
// There is no network-unknown retry permit in this workflow.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const require = (ok, reason) => { if (!ok) throw new Error('reliable_mcp_' + reason); };

function record(root, call) {
  const file = path.join(root, 'calls', call.id, 'request.json');
  const raw = fs.readFileSync(file, 'utf8');
  require(persistedJsonHash(raw) === call.request_sha256, 'request_changed');
  return JSON.parse(raw);
}

function knownFailure(root, call) {
  if (call.status !== 'failed_known') return false;
  const file = path.join(root, 'mcp_http.jsonl');
  if (!fs.existsSync(file)) return false;
  const all = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(JSON.parse);
  const sent = all.filter(e => e.type === 'request' && e.job_id === call.id);
  const events = all.filter(e => sent.some(s => s.seq === e.seq));
  return sent.length === 1 && !events.some(e => e.type === 'unknown_result') &&
    events.some(e => e.type === 'response' && e.seq === sent[0].seq && [429,500,502,503,504].includes(e.status));
}

export function reliableRequestLimit(root, currentJob, nativeBody) {
  const state = read(path.join(root, 'library_state.json'));
  const config = state.input_lock.configuration;
  require(config.provider === 'official_vision_mcp_in_opencode' &&
    config.transport === 'opencode_cli_official_mcp_v1' && config.progress_policy === 'finite_stages_no_retry_v1' &&
    config.vision_model === 'glm-5.3-flash' && config.agent_model === 'zhipuai-coding-plan/glm-5.3-flash',
    'provider_configuration_changed');
  require(state.max_requests === 20 && state.request_count === state.calls.length && state.calls.length <= 20,
    'call_budget_changed');
  const call = state.calls.at(-1);
  require(call?.id === currentJob?.job_id && call.status === 'submitted' &&
    /^(?:reference|plan)(?:_repair)?(?:_retry[12])?$/.test(call.name), 'stage_invalid');
  require(state.calls.slice(0,-1).every(c => c.status === 'received' || knownFailure(root,c)), 'unknown_no_replay');
  for (const previous of state.calls.slice(0,-1)) {
    record(root,previous);
    if (previous.status === 'received') {
      const raw = fs.readFileSync(path.join(root,'calls',previous.id,'response.json'),'utf8');
      require(persistedJsonHash(raw) === previous.response_sha256, 'response_changed');
    }
  }
  const request = record(root,call);
  const args = request.arguments;
  require(request.provider === 'official_vision_mcp_in_opencode' && request.policy_version === state.policy_version &&
    request.tool === 'analyze_video' && args && Object.keys(args).length === 2 &&
    typeof args.prompt === 'string' && args.prompt.length > 0 && path.isAbsolute(args.video_source) &&
    sha(fs.readFileSync(args.video_source)) === request.media_sha256, 'bound_media_changed');
  if (request.reliable_retry_of) {
    const original = state.calls.find(c => c.request_sha256 === request.reliable_retry_of);
    require(original && knownFailure(root, original) && [1,2].includes(request.reliable_attempt), 'retry_not_known_failure');
    const originalRequest = record(root,original);
    const copied = {...request}; delete copied.reliable_retry_of; delete copied.reliable_attempt;
    require(persistedJsonHash(JSON.stringify(copied)) === original.request_sha256, 'retry_payload_changed');
    if (request.reliable_attempt === 2) {
      const first = state.calls.find(c => {
        const r = record(root,c);
        return r.reliable_retry_of === original.request_sha256 && r.reliable_attempt === 1;
      });
      require(first && knownFailure(root,first), 'retry_previous_not_failed');
    }
  }
  if (call.repair_of) {
    const parent = state.calls.find(c => c.id === call.repair_of);
    require(parent?.status === 'received' && !parent.repair_of, 'repair_requires_received');
  }
  if (nativeBody !== undefined) {
    const body = typeof nativeBody === 'string' ? JSON.parse(nativeBody) : nativeBody;
    const messages = body?.messages;
    require(body?.model === config.vision_model && body.stream === false &&
      body.max_tokens === config.vision_generation.max_output_tokens &&
      (Object.hasOwn(config,'vision_reasoning_effort') ? body.reasoning_effort === config.vision_reasoning_effort :
        body.reasoning_effort === undefined) && Array.isArray(messages) && messages.length === 1,
      'native_model_or_messages_changed');
    const user = messages[0];
    require(user.role === 'user' && Array.isArray(user.content) && user.content.length === 2 &&
      user.content[0].type === 'video_url' && user.content[1].type === 'text' && user.content[1].text === args.prompt,
      'native_prompt_changed');
    const match = /^data:video\/[^;,]+;base64,([A-Za-z0-9+/]*={0,2})$/.exec(user.content[0].video_url?.url || '');
    require(match && match[1].length > 0 && sha(Buffer.from(match[1],'base64')) === request.media_sha256,
      'native_media_changed');
    const disk = fs.statfsSync(root);
    require(disk.bavail * disk.bsize > 64*1024*1024 + fs.statSync(args.video_source).size * 4,
      'storage_preflight_insufficient');
  }
  return 20;
}

export function reliableKnownRetry(root, currentJob) {
  const state = read(path.join(root,'library_state.json'));
  if (state.input_lock?.configuration?.workflow !== 'reliable_v1') return false;
  reliableRequestLimit(root,currentJob);
  return Boolean(record(root,state.calls.at(-1)).reliable_retry_of);
}
