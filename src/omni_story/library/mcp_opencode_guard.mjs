// Validate the new OpenCode lane before the unchanged official MCP sends a POST.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';
import {capacityRecovery} from './server_capacity_recovery.mjs';

const PROVIDER = 'official_vision_mcp_in_opencode';
const TRANSPORT = 'opencode_cli_official_mcp_v1';
const POLICY = 'finite_stages_no_retry_v1';
// These are the names emitted by pipeline.execute and observe_selected_slices;
// extension/Goal stages and rounds beyond the two existing rounds are excluded.
const STAGE = /^(?:reference|editing_reference_v2|coarse_zoom_search|overview_[a-f0-9]{8}|(?:zoom|fine)_[a-f0-9]{16}|(?:search|plan|finecut|blind|economy|review)_[01]|semantic_(?:slice|claims)_[01]_[a-f0-9]{16}|semantic_claims_batch_[01]|select_render|selected_review_v2_[01])$/;
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const require = (condition, reason) => { if (!condition) throw new Error('library_mcp_opencode_' + reason); };
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;


function recorded(root, call, filename, expectedHash) {
  require(/^[A-Za-z0-9_-]+$/.test(call.id), 'unsafe_call_id');
  const file = path.join(root, 'calls', call.id, filename);
  const relative = path.relative(fs.realpathSync(root), fs.realpathSync(file));
  require(relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative),
    'record_outside_task');
  const raw = fs.readFileSync(file, 'utf8');
  require(/^[a-f0-9]{64}$/.test(expectedHash) && persistedJsonHash(raw) === expectedHash,
    'recorded_' + filename.replace('.json', '') + '_changed');
  return JSON.parse(raw);
}

export function opencodeRequestLimit(root, currentJob, nativeBody) {
  const stateFile = path.join(root, 'library_state.json');
  if (!fs.existsSync(stateFile)) return null;
  const state = read(stateFile);
  const config = state.input_lock?.configuration;
  if (config?.provider !== PROVIDER) return null;
  require(config.progress_policy === POLICY && config.transport === TRANSPORT &&
    config.vision_model === 'glm-5.3-flash' &&
    config.agent_model === 'zhipuai-coding-plan/glm-5.3-flash' && state.max_requests === null,
    'configuration_or_budget_changed');
  require(Array.isArray(state.calls) && Number.isInteger(state.request_count) &&
    state.request_count === state.calls.length && state.request_count > 0,
    'call_count_changed');
  const call = state.calls.at(-1);
  require(currentJob?.job_id === call.id && call.status === 'submitted' &&
    state.calls.slice(0, -1).every(row => row.status === 'received') &&
    new Set(state.calls.map(row => row.id)).size === state.calls.length,
    'current_job_or_previous_outcome_invalid');
  const repairing = call.name?.endsWith('_repair');
  const stem = repairing ? call.name.slice(0, -7) : call.name;
  const recovery = capacityRecovery(root);
  const capacityAlias = recovery && stem === recovery.authorization.alias;
  require((STAGE.test(stem) || capacityAlias) && state.calls.filter(row => row.name === call.name).length === 1,
    'stage_duplicate_or_not_permitted');
  const request = recorded(root, call, 'request.json', call.request_sha256);
  require(request.provider === PROVIDER && request.policy_version === state.policy_version &&
    ['analyze_image', 'analyze_video'].includes(request.tool), 'request_provider_or_tool_changed');
  const argument = request.tool === 'analyze_image' ? 'image_source' : 'video_source';
  const args = request.arguments;
  require(args && Object.keys(args).length === 2 && typeof args[argument] === 'string' &&
    path.isAbsolute(args[argument]) && typeof args.prompt === 'string' && args.prompt.trim().length > 0 &&
    /^[a-f0-9]{64}$/.test(request.media_sha256), 'request_arguments_invalid');
  require(sha(fs.readFileSync(args[argument])) === request.media_sha256, 'local_media_changed');
  if (repairing) {
    const parent = state.calls.at(-2);
    require(parent?.name === stem && parent.status === 'received' && !parent.repair_of &&
      call.repair_of === parent.id && state.calls.filter(row => row.repair_of === parent.id).length === 1,
      'repair_parent_invalid');
    const original = recorded(root, parent, 'request.json', parent.request_sha256);
    recorded(root, parent, 'response.json', parent.response_sha256);
    const sameInput = canonical({...request, arguments: {...args, prompt: original.arguments?.prompt}});
    require(JSON.stringify(canonical(original)) === JSON.stringify(sameInput) && args.prompt.startsWith(original.arguments.prompt +
      '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'),
      'repair_input_changed');
  } else {
    require(call.repair_of == null && !state.calls.slice(0, -1).some(row =>
      row.name === stem || row.name === stem + '_repair'), 'stage_already_paid');
    if (capacityAlias) {
      const old = recovery.baseline.calls.at(-2);
      const original = recorded(root, old, 'request.json', old.request_sha256);
      const expected = {...original, arguments:{...original.arguments,
        prompt:original.arguments.prompt + recovery.authorization.prompt_suffix}};
      require(JSON.stringify(canonical(request)) === JSON.stringify(canonical(expected)),
        'capacity_alias_input_changed');
    }
  }
  const body = typeof nativeBody === 'string' ? JSON.parse(nativeBody) : nativeBody;
  const generation = config.vision_generation || recovery?.generation;
  if (generation) require(body?.max_tokens === generation.max_output_tokens,
    'native_output_capacity_changed');
  const messages = body?.messages;
  const image = request.tool === 'analyze_image';
  require(body?.model === config.vision_model && body.stream === false && Array.isArray(messages) &&
    messages.length === (image ? 2 : 1) && (!image ||
      messages[0].role === 'system' && typeof messages[0].content === 'string' && messages[0].content.length > 0),
    'native_model_or_messages_changed');
  const user = messages.at(-1);
  const content = user.content;
  const type = image ? 'image_url' : 'video_url';
  require(user.role === 'user' && Array.isArray(content) && content.length === 2 &&
    content[0].type === type && content[1].type === 'text' && content[1].text === args.prompt,
    'native_prompt_or_tool_changed');
  const dataUrl = content[0][type]?.url;
  const match = typeof dataUrl === 'string' && /^data:([^;,]+);base64,([A-Za-z0-9+/]*={0,2})$/.exec(dataUrl);
  require(match && match[1].startsWith(image ? 'image/' : 'video/') && match[2].length > 0,
    'native_media_not_bound_local_bytes');
  const bytes = Buffer.from(match[2], 'base64');
  require(bytes.toString('base64') === match[2] && sha(bytes) === request.media_sha256,
    'native_media_changed');
  return Infinity;
}
