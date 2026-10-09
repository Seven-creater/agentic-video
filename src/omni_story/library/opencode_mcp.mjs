// One fixed job exposed to a real OpenCode agent, delegated to the official MCP.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL, fileURLToPath} from 'node:url';
import {visionGeneration} from './mcp_timeouts.mjs';
const [root, packageRoot] = process.argv.slice(2).map(p => path.resolve(p));
const generation = visionGeneration(root);
const jobId = process.env.OMNI_LIBRARY_OPENCODE_JOB;
if (!/^glm_[0-9]+_[a-z0-9_]+$/.test(jobId || '')) throw new Error('bound_job_missing');
const queue = path.join(root, 'mcp_queue');
const requestFile = path.join(queue, jobId + '.request.json');
const responseFile = path.join(queue, jobId + '.response.json');
const startedFile = path.join(queue, jobId + '.started.json');
const job = JSON.parse(fs.readFileSync(requestFile, 'utf8'));
if (job.job_id !== jobId || !['analyze_image', 'analyze_video'].includes(job.tool)) throw new Error('bound_job_invalid');
const sdk = path.join(packageRoot, 'node_modules/@modelcontextprotocol/sdk/dist/esm');
const imp = relative => import(pathToFileURL(path.join(sdk, relative)).href);
const [{Server}, {StdioServerTransport}, {ListToolsRequestSchema, CallToolRequestSchema},
       {Client}, {StdioClientTransport}] = await Promise.all([
  imp('server/index.js'), imp('server/stdio.js'), imp('types.js'),
  imp('client/index.js'), imp('client/stdio.js')]);
function save(file, data) {
  fs.writeFileSync(file + '.tmp', JSON.stringify(data, null, 2));
  fs.renameSync(file + '.tmp', file);
}
let called = false, client;
const server = new Server({name:'omni-bound-vision-job',version:'1.0'}, {capabilities:{tools:{}}});
server.setRequestHandler(ListToolsRequestSchema, async () => ({tools:[{
  name:'execute',description:'Execute the one immutable official GLM vision job. Call once with {}.',
  inputSchema:{type:'object',properties:{},additionalProperties:false}}]}));
server.setRequestHandler(CallToolRequestSchema, async request => {
  if (request.params.name !== 'execute' || Object.keys(request.params.arguments || {}).length ||
      called || fs.existsSync(startedFile) || fs.existsSync(responseFile)) {
    throw new Error('job_already_started_or_arguments_modified_no_retry');
  }
  called = true;
  save(startedFile,{job_id:jobId,started_at:new Date().toISOString()});
  const here = path.dirname(fileURLToPath(import.meta.url));
  const env = {...process.env, Z_AI_MODE:'ZHIPU', Z_AI_VISION_MODEL:'glm-5.3-flash',
    Z_AI_VISION_MODEL_MAX_TOKENS:String(generation?.max_output_tokens ?? 16384),
    Z_AI_TIMEOUT:String(generation?.model_timeout_ms ?? 600000), Z_AI_RETRY_COUNT:'0',
    OMNI_LIBRARY_MCP_ROOT:root, OMNI_LIBRARY_MCP_PACKAGE_ROOT:packageRoot};
  const transport = new StdioClientTransport({command:process.execPath,env,stderr:'pipe',
    args:['--import',pathToFileURL(path.join(here,'mcp_guard.mjs')).href,
      path.join(packageRoot,'node_modules/@z_ai/mcp-server/build/index.js')]});
  const secret = process.env.Z_AI_API_KEY || '';
  transport.stderr?.on('data',chunk => fs.appendFileSync(path.join(root,'mcp_server.log'),
    String(chunk).replaceAll(secret || '__no_key__','[REDACTED]')));
  client = new Client({name:'opencode-reference-library',version:'1.0'}, {capabilities:{}});
  try {
    await client.connect(transport);
    const result = await client.callTool({name:job.tool,arguments:job.arguments},undefined,
      {timeout:generation?.tool_timeout_ms ?? 1260000});
    save(responseFile,{status:result.isError ? 'error':'complete',result});
    return {content:[{type:'text',text:result.isError ? 'Vision job failed. Do not retry.':'DONE. Original vision reply saved.'}]};
  } catch (error) {
    save(responseFile,{status:'error',error:String(error).replaceAll(secret || '__no_key__','[REDACTED]')});
    return {isError:true,content:[{type:'text',text:'Vision job failed. Do not retry.'}]};
  } finally { await client.close(); }
});
const close = async () => { await client?.close(); await server.close(); process.exit(0); };
process.on('SIGTERM',close); process.on('SIGINT',close); process.stdin.on('end',close);
await server.connect(new StdioServerTransport());
