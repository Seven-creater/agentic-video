// Used by this Codex task to connect the official vision MCP; no custom model endpoint.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import {jobHttpEvents} from './mcp_http_events.mjs';
const root = path.resolve(process.argv[2] || '');
const packageRoot = path.resolve(process.argv[3] || '');
// Match the installed official server's default. An explicit local setting may
// reduce this bound, but must not exceed that model-supported output range.
const maxTokensSetting = process.env.Z_AI_VISION_MODEL_MAX_TOKENS ?? '131072';
if (!/^[1-9][0-9]*$/.test(maxTokensSetting) || Number(maxTokensSetting) > 131072) {
  throw new Error('Z_AI_VISION_MODEL_MAX_TOKENS_must_be_integer_1_to_131072');
}
const maxOutputTokens = Number(maxTokensSetting);
const {connectionTimeouts} = await import('./mcp_timeouts.mjs');
const connection = connectionTimeouts(root);
const {modelTimeoutMs, toolTimeoutMs} = connection;
const {slotParallelConfiguration} = await import('./mcp_slot_finecut_guard.mjs');
const {forwardParallelConfiguration} = await import('./mcp_forward_slot_guard.mjs');
const {parentCutParallelConfiguration} = await import('./mcp_parent_cut_guard.mjs');
const {microclipConfiguration} = await import('./mcp_microclip_guard.mjs');
const {microclipV2Configuration, microclipV2FrozenSlotJob} = await import('./mcp_microclip_v2_guard.mjs');
const visualStoryGuard = process.env.OMNI_LIBRARY_VISUAL_STORY_AUTH_FILE || process.env.OMNI_LIBRARY_VISUAL_STORY_AUTH_SHA256
  ? await import('./mcp_visual_story_guard.mjs') : null;
const visualStory = visualStoryGuard?.visualStoryConfiguration(root) ?? null;
const microclip = visualStory ? null : microclipV2Configuration(root) || microclipConfiguration(root);
const concurrency = process.env.OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY ?? '1';
if (!['1', '2'].includes(concurrency)) throw new Error('library_mcp_slot_concurrency_must_be_1_or_2');
const parallel = concurrency === '2';
if (microclip && parallel) throw new Error('library_mcp_microclip_single_lane_required');
if (visualStory && parallel) throw new Error('library_mcp_visual_story_single_lane_required');
const forwardParallel = visualStory || microclip ? null : parentCutParallelConfiguration(root) || forwardParallelConfiguration(root);
if (parallel && !(forwardParallel || slotParallelConfiguration(root))) throw new Error('library_mcp_slot_parallel_authorization_required');
const {frozenSlotJob} = await import('./mcp_frozen_slot_job.mjs');
const skipFrozenJob = visualStoryGuard?.visualStoryFrozenJob(root) || microclipV2FrozenSlotJob(root) || frozenSlotJob(root);
const here = path.dirname(fileURLToPath(import.meta.url));
const sdkRoot = path.join(packageRoot, 'node_modules/@modelcontextprotocol/sdk/dist/esm/client');
const {Client} = await import(pathToFileURL(path.join(sdkRoot, 'index.js')).href);
const {StdioClientTransport} = await import(pathToFileURL(path.join(sdkRoot, 'stdio.js')).href);
const secret = process.env.Z_AI_API_KEY;
if (!secret) throw new Error('Z_AI_API_KEY_missing');
const queue = path.join(root, 'mcp_queue');
fs.mkdirSync(queue, {recursive: true});
function save(file, value) {
  fs.writeFileSync(file + '.tmp', JSON.stringify(value, null, 2));
  fs.renameSync(file + '.tmp', file);
}
// One official client/server per parent keeps HTTP attribution separate without
// modifying either tool arguments or the official model request body.
const lanes = (parallel ? [0, 3] : [null]).map(parent => {
  const suffix = parent === null ? '' : '_sf_' + parent;
  const currentFile = path.join(root, 'mcp_current' + suffix + '.json');
  const journal = path.join(root, 'mcp_http' + suffix + '.jsonl');
  const laneEnv = {...process.env, Z_AI_MODE: 'ZHIPU', OMNI_LIBRARY_MCP_ROOT: root,
    OMNI_LIBRARY_MCP_PACKAGE_ROOT: packageRoot, Z_AI_VISION_MODEL: 'glm-5.3-flash',
    Z_AI_VISION_MODEL_MAX_TOKENS: String(maxOutputTokens), Z_AI_TIMEOUT: String(modelTimeoutMs),
    Z_AI_RETRY_COUNT: '0'};
  if (parallel) Object.assign(laneEnv, {
    OMNI_LIBRARY_MCP_CURRENT_FILE: currentFile, OMNI_LIBRARY_MCP_HTTP_JOURNAL: journal});
  else {
    delete laneEnv.OMNI_LIBRARY_MCP_CURRENT_FILE;
    delete laneEnv.OMNI_LIBRARY_MCP_HTTP_JOURNAL;
  }
  const transport = new StdioClientTransport({command: process.execPath,
    args: ['--import', pathToFileURL(path.join(here, 'mcp_guard.mjs')).href,
      path.join(packageRoot, 'node_modules/@z_ai/mcp-server/build/index.js')],
    env: laneEnv, stderr: 'pipe'});
  transport.stderr?.on('data', chunk => fs.appendFileSync(path.join(root, 'mcp_server' + suffix + '.log'),
    String(chunk).replaceAll(secret, '[REDACTED]')));
  const client = new Client({name: 'codex-reference-library' + suffix, version: '0.1'}, {capabilities: {}});
  return {parent, currentFile, journal, client, transport};
});
async function submit(requestFile, lane) {
  if (skipFrozenJob(requestFile)) return 'frozen';
  const responseFile = requestFile.replace('.request.json', '.response.json');
  const startedFile = requestFile.replace('.request.json', '.started.json');
  if (fs.existsSync(startedFile)) {
    save(responseFile, {status: 'unknown', error: 'previous_mcp_submission_has_no_reply; not replayed'});
    return 'unknown';
  }
  const job = JSON.parse(fs.readFileSync(requestFile, 'utf8'));
  if (parallel && (path.basename(requestFile) !== job.job_id + '.request.json' || parentFor(job.job_id) !== lane.parent)) {
    throw new Error('library_mcp_slot_parallel_job_binding_invalid');
  }
  if (!['analyze_image', 'analyze_video'].includes(job.tool)) throw new Error('unsupported_library_mcp_tool');
  const started = Date.now();
  save(startedFile, {job_id: job.job_id, submitted_at: new Date().toISOString()});
  save(lane.currentFile, {job_id: job.job_id});
  try {
    const result = await lane.client.callTool({name: job.tool, arguments: job.arguments}, undefined, {timeout: toolTimeoutMs});
    const entries = jobHttpEvents(lane.journal, job.job_id);
    const unknown = entries.some(e => e.type === 'unknown_result') || entries.filter(e => e.type === 'request').some(e => !entries.some(r => r.type === 'response' && r.seq === e.seq));
    const status = result.isError ? (unknown ? 'unknown' : 'error') : 'complete';
    save(responseFile, {status, result, elapsed_s: (Date.now() - started) / 1000});
    console.log(JSON.stringify({job: job.job_id, status, seconds: (Date.now()-started)/1000}));
    return status;
  } catch (error) {
    save(responseFile, {status: 'unknown', error: String(error).replaceAll(secret, '[REDACTED]'), elapsed_s: (Date.now()-started)/1000});
    console.log(JSON.stringify({job: job.job_id, status: 'unknown'}));
    return 'unknown';
  }
}
function parentFor(jobId) {
  const timelineParent = /^glm_[0-9]+_pc_(0|3)_/.exec(jobId)?.[1];
  if (timelineParent !== undefined) return Number(timelineParent);
  const forwardParent = /^glm_[0-9]+_sfv2_(0|3)_/.exec(jobId)?.[1];
  if (forwardParent !== undefined) return Number(forwardParent);
  const parent = /^glm_[0-9]+_sf_(0|3)_/.exec(jobId)?.[1];
  if (parent !== undefined) return Number(parent);
  const round = /^glm_[0-9]+_semantic_(?:slice|claims)_(20|21)_/.exec(jobId)?.[1];
  if (round !== undefined) return round === '20' ? 0 : 3;
  throw new Error('library_mcp_slot_parallel_job_parent_invalid');
}
async function parallelQueue() {
  const running = new Map();
  let halted = false;
  try { while ((!halted && !fs.existsSync(path.join(root, 'mcp_stop'))) || running.size) {
    if (!halted && !fs.existsSync(path.join(root, 'mcp_stop'))) {
      for (const file of fs.readdirSync(queue).filter(f => f.endsWith('.request.json')).sort()) {
        const requestFile = path.join(queue, file);
        if (fs.existsSync(requestFile.replace('.request.json', '.response.json'))) continue;
        if (skipFrozenJob(requestFile)) continue;
        const parent = parentFor(file.slice(0, -'.request.json'.length));
        if (running.has(parent)) continue;
        const lane = lanes.find(l => l.parent === parent);
        if (fs.existsSync(requestFile.replace('.request.json', '.started.json'))) {
          await submit(requestFile, lane);
          halted = true;
          if (!fs.existsSync(path.join(root, 'mcp_stop'))) save(path.join(root, 'mcp_stop'), {
            reason: 'parallel_unknown_outcome_no_new_dispatch', job_id: file.slice(0, -'.request.json'.length)});
          break;
        }
        const promise = submit(requestFile, lane).then(status => {
          if (status === 'unknown' || status === 'error') {
            halted = true;
            if (!fs.existsSync(path.join(root, 'mcp_stop'))) save(path.join(root, 'mcp_stop'), {
              reason: status === 'unknown' ? 'parallel_unknown_outcome_no_new_dispatch' : 'parallel_tool_error_no_new_dispatch',
              job_id: file.slice(0, -'.request.json'.length)});
          }
        }).finally(() => running.delete(parent));
        running.set(parent, promise);
      }
    }
    await Promise.race([...running.values(), new Promise(resolve => setTimeout(resolve, 500))]);
  } } finally { await Promise.allSettled([...running.values()]); }
}
try {
  await Promise.all(lanes.map(lane => lane.client.connect(lane.transport)));
  save(path.join(root, 'mcp_tools.json'), await lanes[0].client.listTools());
  save(path.join(root, 'mcp_ready.json'), {package: '@z_ai/mcp-server', package_version:'0.1.5',
    model: 'glm-5.3-flash', max_output_tokens: maxOutputTokens, model_timeout_ms: modelTimeoutMs,
    tool_timeout_ms: toolTimeoutMs, timeout_policy: connection.policy,
    max_concurrent_parents: lanes.length,
    ...(visualStory ? {execution_policy: visualStory.policy} : {}),
    ...(parallel ? {parallel_policy: forwardParallel?.policy ?? 'sf_parallel_execution_v1',
      journals: lanes.map(lane => lane.journal), current_files: lanes.map(lane => lane.currentFile)} : {}),
    node_version:process.version, connected_at: new Date().toISOString()});
  console.log('official_vision_mcp_connected');
  if (parallel) await parallelQueue();
  else while (!fs.existsSync(path.join(root, 'mcp_stop'))) {
    for (const file of fs.readdirSync(queue).filter(f => f.endsWith('.request.json')).sort()) {
      const requestFile = path.join(queue, file);
      const responseFile = requestFile.replace('.request.json', '.response.json');
      if (fs.existsSync(responseFile)) continue;
      if (skipFrozenJob(requestFile)) continue;
      await submit(requestFile, lanes[0]);
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
} finally { await Promise.allSettled(lanes.map(lane => lane.client.close())); }
