// Used by this Codex task to connect the official vision MCP; no custom model endpoint.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
const root = path.resolve(process.argv[2] || '');
const packageRoot = path.resolve(process.argv[3] || '');
// Match the installed official server's default. An explicit local setting may
// reduce this bound, but must not exceed that model-supported output range.
const maxTokensSetting = process.env.Z_AI_VISION_MODEL_MAX_TOKENS ?? '131072';
if (!/^[1-9][0-9]*$/.test(maxTokensSetting) || Number(maxTokensSetting) > 131072) {
  throw new Error('Z_AI_VISION_MODEL_MAX_TOKENS_must_be_integer_1_to_131072');
}
const maxOutputTokens = Number(maxTokensSetting);
const modelTimeoutMs = 600000;
const toolTimeoutMs = 660000;
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
const transport = new StdioClientTransport({command: process.execPath,
  args: ['--import', pathToFileURL(path.join(here, 'mcp_guard.mjs')).href,
    path.join(packageRoot, 'node_modules/@z_ai/mcp-server/build/index.js')],
  env: {...process.env, Z_AI_MODE: 'ZHIPU', OMNI_LIBRARY_MCP_ROOT: root,
    OMNI_LIBRARY_MCP_PACKAGE_ROOT: packageRoot,
    Z_AI_VISION_MODEL: 'glm-5.3-flash',
    Z_AI_VISION_MODEL_MAX_TOKENS: String(maxOutputTokens), Z_AI_TIMEOUT: String(modelTimeoutMs),
    Z_AI_RETRY_COUNT: '0'}, stderr: 'pipe'});
transport.stderr?.on('data', chunk => fs.appendFileSync(path.join(root, 'mcp_server.log'),
  String(chunk).replaceAll(secret, '[REDACTED]')));
const client = new Client({name: 'codex-reference-library', version: '0.1'}, {capabilities: {}});
try {
  await client.connect(transport);
  save(path.join(root, 'mcp_tools.json'), await client.listTools());
  save(path.join(root, 'mcp_ready.json'), {package: '@z_ai/mcp-server', package_version:'0.1.5',
    model: 'glm-5.3-flash', max_output_tokens: maxOutputTokens, model_timeout_ms: modelTimeoutMs,
    tool_timeout_ms: toolTimeoutMs, node_version:process.version, connected_at: new Date().toISOString()});
  console.log('official_vision_mcp_connected');
  while (!fs.existsSync(path.join(root, 'mcp_stop'))) {
    for (const file of fs.readdirSync(queue).filter(f => f.endsWith('.request.json')).sort()) {
      const requestFile = path.join(queue, file);
      const responseFile = requestFile.replace('.request.json', '.response.json');
      const startedFile = requestFile.replace('.request.json', '.started.json');
      if (fs.existsSync(responseFile)) continue;
      if (fs.existsSync(startedFile)) {
        save(responseFile, {status: 'unknown', error: 'previous_mcp_submission_has_no_reply; not replayed'});
        continue;
      }
      const job = JSON.parse(fs.readFileSync(requestFile, 'utf8'));
      if (!['analyze_image', 'analyze_video'].includes(job.tool)) throw new Error('unsupported_library_mcp_tool');
      const started = Date.now();
      save(startedFile, {job_id: job.job_id, submitted_at: new Date().toISOString()});
      save(path.join(root, 'mcp_current.json'), {job_id: job.job_id});
      try {
        const result = await client.callTool({name: job.tool, arguments: job.arguments}, undefined, {timeout: toolTimeoutMs});
        const entries = fs.existsSync(path.join(root, 'mcp_http.jsonl')) ? fs.readFileSync(path.join(root, 'mcp_http.jsonl'), 'utf8').split('\n').filter(Boolean).map(JSON.parse).filter(e => e.job_id === job.job_id) : [];
        const unknown = entries.some(e => e.type === 'unknown_result') || entries.filter(e => e.type === 'request').some(e => !entries.some(r => r.type === 'response' && r.seq === e.seq));
        save(responseFile, {status: result.isError ? (unknown ? 'unknown' : 'error') : 'complete', result, elapsed_s: (Date.now() - started) / 1000});
        console.log(JSON.stringify({job: job.job_id, status: result.isError ? 'error' : 'complete', seconds: (Date.now()-started)/1000}));
      } catch (error) {
        save(responseFile, {status: 'unknown', error: String(error).replaceAll(secret, '[REDACTED]'), elapsed_s: (Date.now()-started)/1000});
        console.log(JSON.stringify({job: job.job_id, status: 'unknown'}));
      }
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
} finally { await client.close(); }
