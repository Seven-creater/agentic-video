// Guard the official MCP server: never retry an uncertain model POST.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const originalFetch = globalThis.fetch;
const root = process.env.OMNI_LIBRARY_MCP_ROOT;
if (!root) throw new Error('library_mcp_root_missing');
const {Agent} = await import(pathToFileURL(path.join(process.env.OMNI_LIBRARY_MCP_PACKAGE_ROOT,
  'node_modules/undici/index.js')).href);
// The official server is non-streaming; keep the underlying HTTP deadline aligned
// with its configured model timeout rather than Node's shorter header deadline.
const dispatcher = new Agent({headersTimeout: 600000, bodyTimeout: 600000, connect: {timeout: 30000}});
const journal = path.join(root, 'mcp_http.jsonl');
const seen = new Set();
let count = 0;
for (const line of fs.existsSync(journal) ? fs.readFileSync(journal, 'utf8').split('\n').filter(Boolean) : []) {
  const record = JSON.parse(line);
  if (record.type === 'request') { seen.add(record.hash); count += 1; }
}
function log(record) { fs.appendFileSync(journal, JSON.stringify(record) + '\n'); }
globalThis.fetch = async (input, init = {}) => {
  const url = String(input);
  if (init.method !== 'POST') return originalFetch(input, init);
  if (url !== 'https://open.bigmodel.cn/api/paas/v4/chat/completions') {
    throw new Error('library_mcp_endpoint_blocked');
  }
  const body = String(init.body);
  const hash = crypto.createHash('sha256').update(body).digest('hex');
  const limit = Number(process.env.OMNI_LIBRARY_MAX_REQUESTS || 80);
  if (seen.has(hash) || count >= limit) throw new Error('library_mcp_retry_or_budget_blocked');
  const seq = ++count;
  seen.add(hash);
  const currentJob = JSON.parse(fs.readFileSync(path.join(root, 'mcp_current.json'), 'utf8'));
  log({type: 'request', seq, job_id: currentJob.job_id, hash, url, body: JSON.parse(body), at: new Date().toISOString()});
  try {
    const response = await originalFetch(input, {...init, dispatcher});
    const raw = await response.clone().text();
    log({type: 'response', seq, job_id: currentJob.job_id, status: response.status, body: raw,
      headers: Object.fromEntries([...response.headers].filter(([k]) => /request|usage|limit|quota/i.test(k))), at: new Date().toISOString()});
    return response;
  } catch (error) {
    log({type: 'unknown_result', seq, job_id: currentJob.job_id, error: String(error),
      cause: error.cause ? {name: error.cause.name, code: error.cause.code, message: error.cause.message} : null,
      at: new Date().toISOString()});
    throw error;
  }
};
