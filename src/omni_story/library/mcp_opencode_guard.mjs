// Validate the new OpenCode lane before the unchanged official MCP sends a POST.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';
import {capacityRecovery} from './server_capacity_recovery.mjs';
import {chainAuthorization, chainStage} from './server_chain_authorization.mjs';
import {restorationAuthorization, restorationStage} from './restoration_policy.mjs';
import {intervalResume} from './server_interval_resume.mjs';
import {validateAudioNewWork} from './mcp_audio_new_work_guard.mjs';
import {planCastCorrection} from './mcp_plan_cast_correction.mjs';

const PROVIDER = 'official_vision_mcp_in_opencode';
const TRANSPORT = 'opencode_cli_official_mcp_v1';
const POLICY = 'finite_stages_no_retry_v1';
// These are the names emitted by pipeline.execute and observe_selected_slices;
// extension/Goal stages and rounds beyond the two existing rounds are excluded.
const STAGE = /^(?:reference|editing_reference_v2|coarse_zoom_search|overview_[a-f0-9]{8}|(?:zoom|fine)_[a-f0-9]{16}|(?:search|plan|finecut|blind|economy|review)_[01]|semantic_(?:slice|claims)_[01]_[a-f0-9]{16}|semantic_claims_batch_[01]|select_render|selected_review_v2_[01])$/;
const CLEAN_STAGE = /^(?:reference|final_layout_review_[a-f0-9]{16}|coarse_zoom_search|overview_[a-f0-9]{8}|(?:zoom|fine|subtitle_regions)_[a-f0-9]{16}|(?:search|plan|blind|review)_\d+|select_render|selected_review_v2_\d+|rough_audio_timing_1|chain_e2e_v1_fine_(?:observe|inspect_\d+|detail_\d+_[0-5]|plan|blind(?:_r\d*)?|review(?:_r\d*)?|revise(?:_\d+)?))$/;
const AUDIO_STAGE = /^audio_(?:subtitle_regions|mask_review_v\d+|mask_detail_v\d+_\d+|observe(?:_v\d+)?|plan_v\d+|picture_review_v\d+|speaker_review_v\d+_\d+(?:_dense_\d+)?)(?:_preflight_v\d+)?$/;
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const require = (condition, reason) => { if (!condition) throw new Error('library_mcp_opencode_' + reason); };
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const canonical = value => Array.isArray(value) ? value.map(canonical) : value && typeof value === 'object' ?
  Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;

export function storagePreflight(root, mediaBytes, maxTokens) {
  const disk = fs.statfsSync(root);
  const required = mediaBytes * 4 + maxTokens * 32 + 64 * 1024 * 1024;
  require(disk.bavail * disk.bsize >= required, 'storage_preflight_insufficient');
}


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
  const audioRecovery = config.workflow === 'visual_audio_v1' && fs.existsSync(path.join(root,'audio_preflight_recovery.json'))
    ? read(path.join(root,'audio_preflight_recovery.json')) : [];
  const preflightFailure = row => {
    const grant = audioRecovery.find(g => g.failed_call_id === row.id);
    if (!grant || row.status !== 'failed_known' || row.request_sha256 !== grant.request_sha256) return false;
    const base = name => typeof name === 'string' ? name.replace(/_preflight_v\d+$/, '') : null;
    if (!AUDIO_STAGE.test(grant.stage) || /_preflight_v\d+$/.test(grant.stage) ||
        base(grant.alias) !== grant.stage || base(row.name) !== grant.stage ||
        !AUDIO_STAGE.test(grant.alias) || !/_preflight_v\d+$/.test(grant.alias)) return false;
    if (row.name !== grant.failed_stage || !['module_import','endpoint_guard'].includes(grant.failure_kind) ||
        !['agent/mcp_server.log','../../mcp_queue/'+row.id+'.response.json'].includes(grant.evidence_relative_path)) return false;
    const evidence = path.join(root,'calls',row.id,grant.evidence_relative_path);
    if (sha(fs.readFileSync(evidence)) !== grant.evidence_sha256 || !fs.readFileSync(evidence,'utf8').includes(
      grant.failure_kind === 'module_import' ? 'ERR_MODULE_NOT_FOUND' : 'library_mcp_endpoint_blocked')) return false;
    const journal = path.join(root,'mcp_http.jsonl');
    return !fs.existsSync(journal) || !fs.readFileSync(journal,'utf8').trim().split('\n').filter(Boolean)
      .some(line => { const e = JSON.parse(line); return e.job_id === row.id && e.type === 'request'; });
  };
  require(config.progress_policy === POLICY && config.transport === TRANSPORT &&
    config.vision_model === 'glm-5.3-flash' &&
    config.agent_model === 'zhipuai-coding-plan/glm-5.3-flash' && state.max_requests === null,
    'configuration_or_budget_changed');
  require(Array.isArray(state.calls) && Number.isInteger(state.request_count) &&
    state.request_count === state.calls.length && state.request_count > 0,
    'call_count_changed');
  const call = state.calls.at(-1);
  const planCorrection=config.workflow==='reference_rough_skill_v1' ? planCastCorrection(root,state) : null;
  require(currentJob?.job_id === call.id && call.status === 'submitted' &&
    state.calls.slice(0, -1).every(row => row.status === 'received' || preflightFailure(row) ||
      planCorrection?.failedPreflightId===row.id && row.status==='failed_known') &&
    new Set(state.calls.map(row => row.id)).size === state.calls.length,
    'current_job_or_previous_outcome_invalid');
  const repairing = typeof call.name === 'string' && /_repair2?$/.test(call.name);
  const stem = repairing ? call.name.replace(/_repair2?$/, '') : call.name;
  const clean = config.workflow === 'reference_rough_skill_v1';
  const library = config.workflow === 'visual_library_v1';
  const audio = config.workflow === 'visual_audio_v1';
  if (audio) {
    require(['ZHIPU', 'ZAI'].includes(config.vision_platform), 'audio_vision_platform_invalid');
    for (const source of [state.input_lock.video, state.input_lock.reference]) {
      require(source && path.isAbsolute(source.path) && sha(fs.readFileSync(source.path)) === source.sha256,
        'audio_source_changed');
    }
  }
  if (clean && config.parent_baseline) {
    const baseline = config.parent_baseline;
    require(Array.isArray(baseline.linked_ledgers) && baseline.linked_ledgers.length > 0 &&
      baseline.linked_ledgers.every(row => path.isAbsolute(row.path) &&
        sha(fs.readFileSync(row.path)) === row.sha256), 'clean_parent_ledger_changed');
    const parent = path.dirname(baseline.path);
    const relative = path.relative(path.join(parent, 'evaluations'), root);
    require(relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative),
      'clean_evaluation_outside_parent');
  }
  if (clean && stem.startsWith('chain_e2e_v1_fine_')) {
    require(fs.existsSync(path.join(root, 'rough_handoff.json')) && fs.existsSync(path.join(root, 'result.json')),
      'clean_actual_rough_handoff_missing');
    const handoff = read(path.join(root, 'rough_handoff.json'));
    const rough = read(path.join(root, 'result.json'));
    require(handoff.rough_video_path === rough.final_video && handoff.rough_source_sha256 === rough.final_sha256 &&
      sha(fs.readFileSync(handoff.rough_video_path)) === handoff.rough_source_sha256,
      'clean_actual_rough_handoff_changed');
  }
  const restoration = !clean && config.restoration_policy ? restorationAuthorization(root) : null;
  const recovery = clean || restoration ? null : capacityRecovery(root);
  const chain = clean || restoration ? null : chainAuthorization(root);
  const interval = clean ? null : intervalResume(root);
  const capacityAlias = recovery && stem === recovery.authorization.alias;
  const intervalAlias = interval && stem === interval.authorization.alias;
  require((library ? /^(?:fine|subtitle_regions)_[a-f0-9]{16}$/.test(stem) :
    audio ? AUDIO_STAGE.test(stem) : clean ? CLEAN_STAGE.test(stem) || planCorrection?.alias===stem : restoration ? restorationStage(stem) : chain ? chainStage(call.name) : STAGE.test(stem) || capacityAlias || intervalAlias) &&
    state.calls.filter(row => row.name === call.name).length === 1,
    'stage_duplicate_or_not_permitted');
  const request = recorded(root, call, 'request.json', call.request_sha256);
  if (audio && config.audio_new_work) validateAudioNewWork(root,state,request);
  if (audio && /_preflight_v\d+$/.test(stem)) {
    const grant = audioRecovery.find(g => g.alias === stem);
    const failed = grant && state.calls.find(row => row.id === grant.failed_call_id);
    require(failed && preflightFailure(failed), 'audio_preflight_alias_without_proven_grant');
    const original = recorded(root, failed, 'request.json', failed.request_sha256);
    const expected = {...original,arguments:{...original.arguments,prompt:original.arguments.prompt+'\n'+grant.prompt_suffix}};
    require(JSON.stringify(canonical(request))===JSON.stringify(canonical(expected)), 'audio_preflight_alias_input_changed');
  }
  require(request.provider === PROVIDER && request.policy_version === state.policy_version &&
    ['analyze_image', 'analyze_video'].includes(request.tool), 'request_provider_or_tool_changed');
  const argument = request.tool === 'analyze_image' ? 'image_source' : 'video_source';
  const args = request.arguments;
  require(args && Object.keys(args).length === 2 && typeof args[argument] === 'string' &&
    path.isAbsolute(args[argument]) && typeof args.prompt === 'string' && args.prompt.trim().length > 0 &&
    /^[a-f0-9]{64}$/.test(request.media_sha256), 'request_arguments_invalid');
  require(sha(fs.readFileSync(args[argument])) === request.media_sha256, 'local_media_changed');
  if (clean && config.parent_baseline?.unknown_inputs?.length) {
    let scope = request.observation_scope;
    const lineagePath = path.join(path.dirname(args[argument]), 'lineage.json');
    if (!scope && fs.existsSync(lineagePath)) {
      scope = read(lineagePath);
      require(scope.sha256 === request.media_sha256, 'clean_parent_lineage_changed');
    }
    require(!config.parent_baseline.unknown_inputs.some(row => row.media_sha256 === request.media_sha256 ||
      scope && ['source_sha256', 'source_start_s', 'source_end_s'].every(key => row.scope[key] === scope[key])),
      'clean_parent_unknown_media_no_replay');
  }
  if (repairing) {
    const second = call.name.endsWith('_repair2'), parent = state.calls.at(-2);
    const originalCall = second ? state.calls.at(-3) : parent;
    require(originalCall?.name === stem && originalCall.status === 'received' && !originalCall.repair_of &&
      parent?.name === stem + (second ? '_repair' : '') && parent.status === 'received' &&
      (second ? parent.repair_of === originalCall.id &&
        state.calls.filter(row => row.repair_of === originalCall.id).length === 1 : !parent.repair_of) &&
      call.repair_of === parent.id && state.calls.filter(row => row.repair_of === parent.id).length === 1,
      'repair_parent_invalid');
    const original = recorded(root, originalCall, 'request.json', originalCall.request_sha256);
    recorded(root, originalCall, 'response.json', originalCall.response_sha256);
    if (second) {
      recorded(root, parent, 'request.json', parent.request_sha256);
      recorded(root, parent, 'response.json', parent.response_sha256);
    }
    const sameInput = canonical({...request, arguments: {...args, prompt: original.arguments?.prompt}});
    require(JSON.stringify(canonical(original)) === JSON.stringify(sameInput) && args.prompt.startsWith(original.arguments.prompt +
      '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。'),
      'repair_input_changed');
  } else {
    require(call.repair_of == null && !state.calls.slice(0, -1).some(row =>
      row.name === stem || row.name === stem + '_repair' || row.name === stem + '_repair2'), 'stage_already_paid');
    if (capacityAlias) {
      const old = recovery.baseline.calls.at(-2);
      const original = recorded(root, old, 'request.json', old.request_sha256);
      const expected = {...original, arguments:{...original.arguments,
        prompt:original.arguments.prompt + recovery.authorization.prompt_suffix}};
      require(JSON.stringify(canonical(request)) === JSON.stringify(canonical(expected)),
        'capacity_alias_input_changed');
    }
    if (intervalAlias) require(JSON.stringify(canonical(request)) ===
      JSON.stringify(canonical(interval.authorization.expected_request)), 'interval_alias_input_changed');
  }
  const body = typeof nativeBody === 'string' ? JSON.parse(nativeBody) : nativeBody;
  require(Object.hasOwn(config, 'vision_reasoning_effort')
    ? config.vision_reasoning_effort === 'low' && body?.reasoning_effort === config.vision_reasoning_effort
    : body?.reasoning_effort === undefined, 'native_reasoning_effort_changed');
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
  if (clean || library) {
    storagePreflight(root, bytes.length, generation?.max_output_tokens ?? 16384);
  }
  return Infinity;
}
