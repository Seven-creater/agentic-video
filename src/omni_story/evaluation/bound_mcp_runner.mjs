#!/usr/bin/env node
// Resource adapter only: execute the frozen bridge's immutable job once.
// No agent/model call, no understanding prompt, and no fabricated agent usage.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const secret = process.env.Z_AI_API_KEY || '';
const safe = value => secret ? String(value).replaceAll(secret, '[REDACTED]') : String(value);
let client;
try {
  const config = JSON.parse(process.env.OPENCODE_CONFIG_CONTENT || '{}');
  const bridge = config.mcp?.omni;
  const command = bridge?.command;
  const jobId = process.env.OMNI_LIBRARY_OPENCODE_JOB;
  if (!/^glm_[0-9]+_[a-z0-9_]+$/.test(jobId || '') || !Array.isArray(command) ||
      command.length !== 4 || command[0] !== 'node' || path.basename(command[1]) !== 'opencode_mcp.mjs' ||
      !command.slice(1).every(p => path.isAbsolute(p))) throw new Error('invalid_frozen_bound_bridge');
  const sdk = path.join(command[3], 'node_modules/@modelcontextprotocol/sdk/dist/esm');
  const [{Client}, {StdioClientTransport}] = await Promise.all([
    import(pathToFileURL(path.join(sdk, 'client/index.js')).href),
    import(pathToFileURL(path.join(sdk, 'client/stdio.js')).href),
  ]);
  const transport = new StdioClientTransport({command:process.execPath,args:command.slice(1),
    env:process.env,stderr:'pipe'});
  transport.stderr?.on('data', chunk => process.stderr.write(safe(chunk)));
  client = new Client({name:'evaluation-bound-job-resource-adapter',version:'1.0'}, {capabilities:{}});
  await client.connect(transport);
  const {tools} = await client.listTools();
  if (tools.length !== 1 || tools[0].name !== 'execute') throw new Error('unexpected_bound_bridge_tool');
  const result = await client.callTool({name:'execute',arguments:{}}, undefined,
    {timeout:bridge.timeout || 1260000});
  if (result.isError) throw new Error('bound_mcp_execution_failed_no_retry');
  process.stdout.write(JSON.stringify({type:'direct_bound_mcp_execution',job_id:jobId,
    agent_model_called:false}) + '\n');
} catch (error) {
  process.stderr.write(safe(error.message) + '\n');
  process.exitCode = 1;
} finally {
  await client?.close();
}
