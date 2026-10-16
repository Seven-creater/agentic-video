// Verify fresh audio authorization from the original files before native POST.
// This module only reads evidence; it never retries or resolves an unknown call.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {persistedJsonHash} from './persisted_json_hash.mjs';

const POLICY = 'audio_new_work_after_unknown_v1';
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const require = (ok, reason) => {if (!ok) throw new Error('library_mcp_audio_new_work_' + reason);};
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const canonical = value => Array.isArray(value) ? value.map(canonical) : isObject(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])])) : value;
const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
const fields = (row, keys) => isObject(row) && same(Object.keys(row).sort(), [...keys].sort());
const order = (left, right) => left < right ? -1 : left > right ? 1 : 0;
const fileHash = file => digest(fs.readFileSync(file));

function binding(row) {
  require(fields(row, ['path', 'sha256']) && typeof row.path === 'string' &&
    path.isAbsolute(row.path) && hash(row.sha256), 'binding_fields_required');
  const file = fs.realpathSync(row.path);
  require(fs.statSync(file).isFile() && fileHash(file) === row.sha256, 'bound_file_changed');
  return {path: file, sha256: row.sha256};
}

function scope(value) {
  require(isObject(value) && ['sparse_contact_sheet', 'continuous_window', 'complete_file'].includes(value.kind) &&
    hash(value.source_sha256) && Number.isFinite(value.source_start_s) && Number.isFinite(value.source_end_s) &&
    value.source_start_s >= 0 && value.source_start_s < value.source_end_s, 'bound_media_scope_required');
  return [value.kind, value.source_sha256, value.source_start_s, value.source_end_s];
}

function source(value) {
  require(fields(value, ['path', 'sha256', 'duration_s', 'video_stream_index']), 'source_fields_required');
  const record = binding({path: value.path, sha256: value.sha256});
  scope({kind: 'complete_file', source_sha256: record.sha256, source_start_s: 0, source_end_s: value.duration_s});
  require(Number.isSafeInteger(value.video_stream_index) && value.video_stream_index >= 0, 'source_stream_required');
  return {...record, duration_s: value.duration_s, video_stream_index: value.video_stream_index};
}

function authorization(value) {
  require(fields(value, ['source', 'date', 'summary']) && value.source === 'human_user',
    'explicit_human_authorization_required');
  const stamp = typeof value.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value.date)
    ? new Date(value.date + 'T00:00:00.000Z') : null;
  require(stamp && Number(value.date.slice(0, 4)) > 0 && Number.isFinite(stamp.getTime()) &&
    stamp.toISOString().slice(0, 10) === value.date, 'authorization_date_required');
  require(typeof value.summary === 'string' && value.summary.trim().length > 0 &&
    [...value.summary].length <= 1000, 'sanitized_authorization_summary_required');
  require(!/bearer\s|sk-[a-z0-9]{8,}|(?:api[_ -]?key|auth[_ -]?token|secret)\s*[:=]|\b[a-f0-9]{32,}\b|\b[a-z0-9_-]{48,}\b/i.test(value.summary),
    'authorization_must_not_contain_credentials');
  return value;
}

function unknown(ledger, saved, call) {
  require(typeof call.id === 'string' && /^[A-Za-z0-9_-]+$/.test(call.id), 'unsafe_call_id');
  const requestPath = path.join(path.dirname(ledger.path), 'calls', call.id, 'request.json');
  const raw = fs.readFileSync(requestPath, 'utf8'), request = JSON.parse(raw);
  require(hash(call.request_sha256) && persistedJsonHash(raw) === call.request_sha256, 'unknown_request_changed');
  require(['analyze_image', 'analyze_video'].includes(request.tool), 'unknown_tool_required');
  const args = request.arguments, argument = request.tool === 'analyze_image' ? 'image_source' : 'video_source';
  require(isObject(args) && typeof args.prompt === 'string' && args.prompt.trim().length > 0, 'unknown_prompt_required');
  const media = binding({path: args[argument], sha256: request.media_sha256});
  let observation = request.observation_scope, lineage = null;
  const lineagePath = path.join(path.dirname(media.path), 'lineage.json');
  if (fs.existsSync(lineagePath)) {
    const value = read(lineagePath);
    const derived = Object.fromEntries(['kind', 'source_sha256', 'source_start_s', 'source_end_s'].map(key => [key, value[key]]));
    require(value.sha256 === media.sha256 && typeof value.path === 'string' &&
      fs.realpathSync(value.path) === media.path && isObject(value.spec ?? {}) &&
      Object.entries(derived).every(([key, item]) => !Object.hasOwn(value.spec ?? {}, key) || same(value.spec[key], item)),
      'unknown_lineage_changed');
    require(observation == null || same(scope(observation), scope(derived)), 'unknown_scope_changed');
    observation = derived;
    lineage = {path: lineagePath, sha256: fileHash(lineagePath)};
  }
  scope(observation);
  const pairs = [];
  const journals = fs.readdirSync(path.dirname(ledger.path)).filter(name => /^mcp_http.*\.jsonl$/.test(name)).sort();
  for (const name of journals) {
    const journal = path.join(path.dirname(ledger.path), name);
    const entries = fs.readFileSync(journal, 'utf8').split(/\r?\n/).filter(line => line.trim())
      .map(rawLine => ({raw: rawLine, event: JSON.parse(rawLine)})).filter(row => row.event.job_id === call.id);
    if (!entries.length) continue;
    require(!entries.some(row => row.event.type === 'response'), 'unknown_has_captured_response');
    const requests = entries.filter(row => row.event.type === 'request');
    const unknowns = entries.filter(row => row.event.type === 'unknown_result');
    require(requests.length === 1 && unknowns.length === 1 && Number.isSafeInteger(requests[0].event.seq) &&
      requests[0].event.seq > 0 && requests[0].event.seq === unknowns[0].event.seq, 'unknown_http_evidence_required');
    const original = requests[0], lost = unknowns[0];
    const body = original.event.body;
    require(isObject(body) && Array.isArray(body.messages), 'unknown_http_prompt_changed');
    const text = body.messages.filter(message => message.role === 'user' && Array.isArray(message.content))
      .flatMap(message => message.content.filter(row => isObject(row) && row.type === 'text').map(row => row.text));
    require(same(text, [args.prompt]), 'unknown_http_prompt_changed');
    require(hash(original.event.hash), 'unknown_native_body_hash_required');
    pairs.push({journal_path: journal, journal_sha256: fileHash(journal), seq: original.event.seq,
      native_body_sha256: original.event.hash, request_event_sha256: persistedJsonHash(original.raw),
      unknown_event_sha256: persistedJsonHash(lost.raw)});
  }
  require(pairs.length === 1, 'unknown_http_evidence_required');
  return {call_id: call.id, source_task_id: saved.task_id, source_ledger_path: ledger.path,
    source_ledger_sha256: ledger.sha256, request_path: requestPath, request_sha256: call.request_sha256,
    request_file_sha256: fileHash(requestPath), tool: request.tool, media_path: media.path,
    media_sha256: media.sha256, scope: observation, prompt_sha256: digest(args.prompt), lineage,
    http_evidence: pairs[0]};
}

function priors(rows) {
  require(Array.isArray(rows) && rows.length > 0, 'all_prior_roots_required');
  const roots = new Map(rows.map(row => {const item = binding(row); return [item.path, item];}));
  const pending = [...roots.values()], seen = new Set(), ledgers = [], exclusions = [], services = [];
  while (pending.length) {
    const record = binding(pending.pop());
    if (seen.has(record.path)) continue;
    seen.add(record.path);
    const saved = read(record.path), calls = saved.calls;
    require(Array.isArray(calls) && Number.isSafeInteger(saved.request_count) && saved.request_count === calls.length &&
      calls.every(call => isObject(call) && typeof call.id === 'string' && /^[A-Za-z0-9_-]+$/.test(call.id)) &&
      new Set(calls.map(call => call.id)).size === calls.length, 'prior_call_count_changed');
    require(calls.every(call => ['received', 'failed_known', 'uncertain'].includes(call.status)),
      'prior_pending_or_unrecognized_outcome');
    const statuses = {};
    for (const call of calls) statuses[call.status] = (statuses[call.status] ?? 0) + 1;
    ledgers.push({...record, task_id: saved.task_id, requests: calls.length, statuses});
    const linked = saved.input_lock?.configuration?.prior_audio_evaluations ?? [];
    require(Array.isArray(linked), 'prior_links_required');
    pending.push(...linked);
    for (const call of calls) if (call.status === 'uncertain') exclusions.push(unknown(record, saved, call));
    const directory = path.dirname(record.path);
    const entries = fs.readdirSync(directory, {withFileTypes: true});
    const tts = path.join(directory, 'tts');
    const ttsFolders = fs.existsSync(tts) ? fs.readdirSync(tts, {withFileTypes: true})
      .filter(entry => entry.isDirectory()).map(entry => path.join(tts, entry.name)) : [];
    const avFolders = entries.filter(entry => entry.isDirectory() && /^av_review_v.*$/.test(entry.name))
      .map(entry => path.join(directory, entry.name));
    for (const [kind, folders] of [['tts', ttsFolders], ['audiovisual_review', avFolders]]) {
      for (const folder of folders.sort()) {
        const servicePath = path.join(folder, 'state.json');
        if (!fs.existsSync(servicePath)) continue;
        const service = read(servicePath);
        require(['received', 'rejected'].includes(service.status), 'prior_audio_service_unknown');
        const row = {path: fs.realpathSync(servicePath), sha256: fileHash(servicePath), status: service.status, kind};
        if (kind === 'audiovisual_review' && service.status === 'received') {
          const response = path.join(folder, 'response.json');
          require(fs.existsSync(response) && fileHash(response) === service.response_sha256,
            'prior_audio_service_response_changed');
          Object.assign(row, {response_path: fs.realpathSync(response), response_sha256: fileHash(response)});
        }
        services.push(row);
      }
    }
  }
  require(exclusions.length > 0, 'prior_unknown_required');
  return {prior_roots: [...roots.values()].sort((a, b) => order(a.path, b.path)),
    prior_ledgers: ledgers.sort((a, b) => order(a.path, b.path)),
    audio_services: services.sort((a, b) => order(a.path, b.path)),
    unknown_exclusions: exclusions.sort((a, b) => order(a.source_ledger_path, b.source_ledger_path) || order(a.call_id, b.call_id))};
}

export function validateAudioNewWork(root, state, request) {
  const config = state.input_lock?.configuration, record = config?.audio_new_work;
  if (record == null) return null;
  require(config.subtitle_region_seed == null, 'subtitle_seed_not_allowed');
  const receiptBinding = binding(record), receipt = read(receiptBinding.path);
  require(receipt.policy === POLICY, 'receipt_policy_required');
  const video = source(state.input_lock.video), reference = source(state.input_lock.reference);
  const history = priors(config.prior_audio_evaluations);
  require(history.unknown_exclusions.every(row => video.sha256 !== row.media_sha256 &&
    video.sha256 !== row.scope.source_sha256), 'fresh_source_required');
  const expected = {policy: POLICY, authorization: authorization(receipt.authorization),
    fresh_source_required: true, no_subtitle_seed: true, video, reference, ...history};
  require(same(receipt, expected), 'receipt_sources_history_or_exclusions_changed');
  require(isObject(request) && hash(request.media_sha256), 'request_media_sha_required');
  const currentScope = request.observation_scope;
  scope(currentScope);
  const call = state.calls?.at(-1);
  require(call && typeof call.id === 'string' && /^[A-Za-z0-9_-]+$/.test(call.id), 'current_request_required');
  const requestPath = path.join(root, 'calls', call.id, 'request.json');
  const raw = fs.readFileSync(requestPath, 'utf8'), requestHash = persistedJsonHash(raw);
  require(hash(call.request_sha256) && requestHash === call.request_sha256 && same(JSON.parse(raw), request),
    'current_request_changed');
  for (const old of history.unknown_exclusions) {
    const previous = old.scope;
    const overlap = currentScope.source_sha256 === previous.source_sha256 &&
      currentScope.source_start_s < previous.source_end_s && previous.source_start_s < currentScope.source_end_s;
    require(request.media_sha256 !== old.media_sha256 && requestHash !== old.request_sha256 && !overlap,
      'unknown_request_must_not_be_replayed_' + old.call_id);
  }
  return history.unknown_exclusions;
}
