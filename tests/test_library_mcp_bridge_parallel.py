"""Queue scheduling with SDK/auth stubs; no process, MCP or HTTP is started."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


LIBRARY = Path(__file__).resolve().parents[1] / "omni_story/library"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Bridge queue requires Node.js")


def run_queue(tmp_path, jobs, *, stop_at=0, stop_on_start=False, started=(), frozen_policy=False,
              policy_mutation=None):
    root = tmp_path / "task"
    queue = root / "mcp_queue"
    queue.mkdir(parents=True)
    for ident, args in jobs:
        (queue / (ident + ".request.json")).write_text(json.dumps({
            "job_id": ident, "tool": "analyze_image", "arguments": args}), encoding="utf-8")
    for ident in started:
        (queue / (ident + ".started.json")).write_text('{"preserved":true}', encoding="utf-8")
    policy_env = {}
    if frozen_policy:
        ident = 'glm_166_sf_3_source_feedback_replan_v1'
        canonical_hash = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True,
            separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        byte_hash = lambda file: hashlib.sha256(file.read_bytes()).hexdigest()
        folder = root / 'calls' / ident
        folder.mkdir(parents=True)
        request_file = folder / 'request.json'
        request = {'media_sha256': 'synthetic-media', 'observation_scope': {'start_s': 0.0, 'end_s': 34.0}}
        request_file.write_text(json.dumps(request), encoding='utf-8')
        receipt = root / 'frozen_receipt.json'
        receipt.write_text(json.dumps({'call_id': ident, 'no_replay': True}), encoding='utf-8')
        calls = [{'id': f'synthetic_{i}', 'status': 'received'} for i in range(166)]
        calls[3] = {'id': 'glm_004_coarse_978d5360_01', 'status': 'uncertain'}
        calls[130] = {'id': 'glm_131_active_10_draft', 'status': 'uncertain'}
        calls[165] = {'id': ident, 'status': 'uncertain', 'request_sha256': canonical_hash(request)}
        state = {'task_id': 'synthetic-task', 'input_lock': {'sha256': 'synthetic-lock', 'duration_s': 34.0},
                 'calls': calls, 'artifacts': {}}
        policy = {'policy': 'sf_independent_slot_recovery_v1', 'task_id': state['task_id'],
                  'input_lock_sha256': canonical_hash(state['input_lock']),
                  'baseline_request_count': 166, 'prefix_calls_sha256': canonical_hash(calls),
                  'frozen_unknown_call_ids': [calls[3]['id'], calls[130]['id'], ident],
                  'transport_binding': {'call_id': ident, 'request_sha256': canonical_hash(request),
                    'queue_request_path': str(queue / (ident + '.request.json')),
                    'queue_request_byte_sha256': byte_hash(queue / (ident + '.request.json')),
                    'queue_started_path': str(queue / (ident + '.started.json')),
                    'queue_started_byte_sha256': byte_hash(queue / (ident + '.started.json')),
                    'call_request_path': str(request_file), 'call_request_byte_sha256': byte_hash(request_file),
                    'reclassification_path': str(receipt), 'reclassification_byte_sha256': byte_hash(receipt)}}
        policy_file = root / 'frozen_policy.json'
        policy_file.write_text(json.dumps(policy), encoding='utf-8')
        state['artifacts']['sf_independent_slot_recovery_v1'] = [{'path': str(policy_file),
                                                                'sha256': canonical_hash(policy)}]
        (root / 'library_state.json').write_text(json.dumps(state), encoding='utf-8')
        policy_env = {'OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE': str(policy_file),
                      'OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256': byte_hash(policy_file)}
        if policy_mutation:
            policy_mutation(root, queue, policy_file)
    # The guard's independently tested authorization is stubbed at its read-only
    # boundary. Both official SDK classes are stubs; no child server is spawned.
    code = tmp_path / "code"
    code.mkdir()
    shutil.copyfile(LIBRARY / "mcp_bridge.mjs", code / "mcp_bridge.mjs")
    shutil.copyfile(LIBRARY / "mcp_http_events.mjs", code / "mcp_http_events.mjs")
    shutil.copyfile(LIBRARY / "mcp_timeouts.mjs", code / "mcp_timeouts.mjs")
    shutil.copyfile(LIBRARY / "mcp_frozen_slot_job.mjs", code / "mcp_frozen_slot_job.mjs")
    shutil.copyfile(LIBRARY / "mcp_forward_slot_guard.mjs", code / "mcp_forward_slot_guard.mjs")
    shutil.copyfile(LIBRARY / "mcp_parent_cut_guard.mjs", code / "mcp_parent_cut_guard.mjs")
    shutil.copyfile(LIBRARY / "mcp_microclip_guard.mjs", code / "mcp_microclip_guard.mjs")
    shutil.copyfile(LIBRARY / "mcp_microclip_known500.mjs", code / "mcp_microclip_known500.mjs")
    shutil.copyfile(LIBRARY / "mcp_microclip_v2_guard.mjs", code / "mcp_microclip_v2_guard.mjs")
    shutil.copyfile(LIBRARY / "mcp_microclip_boundary_guard.mjs", code / "mcp_microclip_boundary_guard.mjs")
    shutil.copyfile(LIBRARY / "mcp_microclip_boundary_metadata.mjs", code / "mcp_microclip_boundary_metadata.mjs")
    (code / "mcp_slot_finecut_guard.mjs").write_text(
        "export function slotParallelConfiguration() { return {policy:'sf_parallel_execution_v1'}; }",
        encoding="utf-8")
    sdk = tmp_path / "package/node_modules/@modelcontextprotocol/sdk"
    client = sdk / "dist/esm/client"
    client.mkdir(parents=True)
    (sdk / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (client / "stdio.js").write_text('''
export class StdioClientTransport { constructor(options) { this.options = options; } }
''', encoding="utf-8")
    (client / "index.js").write_text('''
import fs from 'node:fs';
import path from 'node:path';
let active = 0, calls = 0;
const trace = record => fs.appendFileSync(process.env.BRIDGE_TEST_TRACE, JSON.stringify(record)+'\\n');
export class Client {
  constructor(metadata) { this.name = metadata.name; this.seq = 0; }
  async connect(transport) { this.env = transport.options.env; trace({type:'connect', client:this.name,
    current:this.env.OMNI_LIBRARY_MCP_CURRENT_FILE, journal:this.env.OMNI_LIBRARY_MCP_HTTP_JOURNAL,
    retries:this.env.Z_AI_RETRY_COUNT}); }
  async listTools() { return {tools: [{name:'analyze_image'}]}; }
  async callTool(input) {
    const current = JSON.parse(fs.readFileSync(this.env.OMNI_LIBRARY_MCP_CURRENT_FILE, 'utf8'));
    const number = ++calls, seq = ++this.seq;
    const root = this.env.OMNI_LIBRARY_MCP_ROOT;
    const stop = () => fs.writeFileSync(path.join(root,'mcp_stop'), 'synthetic queue test');
    active++;
    trace({type:'start', client:this.name, active, job:current.job_id, input});
    fs.appendFileSync(this.env.OMNI_LIBRARY_MCP_HTTP_JOURNAL, JSON.stringify({type:'request', seq,
      job_id:current.job_id, hash:'synthetic-'+current.job_id})+'\\n');
    if (process.env.BRIDGE_TEST_STOP_ON_START === '1' && number === Number(process.env.BRIDGE_TEST_STOP_AT)) stop();
    await new Promise(resolve => setTimeout(resolve, input.arguments.delay_ms || 20));
    active--;
    if (input.arguments.unknown) {
      trace({type:'unknown', client:this.name, active, job:current.job_id});
      throw new Error('synthetic connection lost, no retry');
    }
    fs.appendFileSync(this.env.OMNI_LIBRARY_MCP_HTTP_JOURNAL, JSON.stringify({type:'response', seq,
      job_id:current.job_id, status:200, body:'{}'})+'\\n');
    trace({type:'end', client:this.name, active, job:current.job_id});
    if (process.env.BRIDGE_TEST_STOP_ON_START !== '1' && number === Number(process.env.BRIDGE_TEST_STOP_AT)) stop();
    return {isError:Boolean(input.arguments.tool_error), content:[{type:'text', text:'{}'}]};
  }
  async close() { trace({type:'close', client:this.name, active}); }
}
''', encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    env = {**os.environ, "Z_AI_API_KEY": "synthetic-test-secret", "BRIDGE_TEST_TRACE": str(trace),
           "BRIDGE_TEST_STOP_AT": str(stop_at), "BRIDGE_TEST_STOP_ON_START": "1" if stop_on_start else "0",
           "OMNI_LIBRARY_SLOT_FINECUT_MAX_CONCURRENCY": "2"}
    env.pop('OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_FILE', None)
    env.pop('OMNI_LIBRARY_SLOT_FINECUT_RECOVERY_SHA256', None)
    env.update(policy_env)
    env.pop("Z_AI_VISION_MODEL_MAX_TOKENS", None)
    checked = subprocess.run(["node", str(code / "mcp_bridge.mjs"), str(root), str(tmp_path / "package")],
                             env=env, text=True, capture_output=True, timeout=15)
    trace_text = trace.read_text(encoding="utf-8") if trace.exists() else ''
    rows = [json.loads(line) for line in trace_text.splitlines()]
    assert "synthetic-test-secret" not in checked.stdout + checked.stderr + trace_text
    return root, queue, checked, rows


def job(number, parent, kind="outline", **args):
    return f"glm_{number:03d}_sf_{parent}_{kind}", {"prompt": "unchanged synthetic prompt", **args}


def test_two_official_client_lanes_overlap_but_each_parent_remains_serial(tmp_path):
    jobs = [job(134, 0, "outline_v2", delay_ms=100), job(135, 3, delay_ms=30),
            job(136, 0, "facts_0123456789abcdef", delay_ms=20)]
    root, queue, checked, rows = run_queue(tmp_path, jobs, stop_at=3)
    assert checked.returncode == 0, checked.stderr
    starts = [row for row in rows if row["type"] == "start"]
    assert max(row["active"] for row in starts) == 2
    assert {row["client"] for row in starts} == {"codex-reference-library_sf_0", "codex-reference-library_sf_3"}
    assert starts[2]["job"] == jobs[2][0]
    assert next(i for i, row in enumerate(rows) if row.get("job") == jobs[0][0] and row["type"] == "end") < rows.index(starts[2])
    assert [row["input"] for row in starts] == [{"name": "analyze_image", "arguments": args} for _, args in jobs]
    for row in [row for row in rows if row["type"] == "connect"]:
        parent = row["client"].rsplit("_", 1)[1]
        assert row["current"] == str(root / f"mcp_current_sf_{parent}.json")
        assert row["journal"] == str(root / f"mcp_http_sf_{parent}.jsonl")
        assert row["retries"] == "0"
    assert all(row["active"] == 0 for row in rows if row["type"] == "close")
    assert all(json.loads((queue / (ident + ".response.json")).read_text())["status"] == "complete" for ident, _ in jobs)


def test_stop_marker_waits_for_both_inflight_calls_without_dispatching_next_job(tmp_path):
    jobs = [job(134, 0, delay_ms=100), job(135, 3, delay_ms=20), job(136, 0, "facts_0123456789abcdef")]
    _, queue, checked, rows = run_queue(tmp_path, jobs, stop_at=2, stop_on_start=True)
    assert checked.returncode == 0, checked.stderr
    assert len([row for row in rows if row["type"] == "start"]) == 2
    assert all(row["active"] == 0 for row in rows if row["type"] == "close")
    assert all((queue / (ident + ".response.json")).exists() for ident, _ in jobs[:2])
    assert not (queue / (jobs[2][0] + ".started.json")).exists()


def test_exact_semantic_rounds_are_assigned_to_their_parent_lane(tmp_path):
    jobs = [("glm_134_semantic_slice_20_0123456789abcdef", {"prompt": "source facts 20", "delay_ms": 100}),
            ("glm_135_semantic_claims_21_0123456789abcdef", {"prompt": "source claims 21", "delay_ms": 20})]
    _, _, checked, rows = run_queue(tmp_path, jobs, stop_at=2, stop_on_start=True)
    assert checked.returncode == 0, checked.stderr
    assert [(row["job"], row["client"]) for row in rows if row["type"] == "start"] == [
        (jobs[0][0], "codex-reference-library_sf_0"), (jobs[1][0], "codex-reference-library_sf_3")]


def test_unknown_call_halts_new_dispatch_and_preserves_other_inflight_reply(tmp_path):
    jobs = [job(134, 0, delay_ms=20, unknown=True), job(135, 3, delay_ms=100),
            job(136, 0, "facts_0123456789abcdef"), job(137, 3, "facts_0123456789abcdef")]
    root, queue, checked, rows = run_queue(tmp_path, jobs)
    assert checked.returncode == 0, checked.stderr
    assert (root / "mcp_stop").exists()
    assert len([row for row in rows if row["type"] == "start"]) == 2
    assert [json.loads((queue / (ident + ".response.json")).read_text())["status"] for ident, _ in jobs[:2]] == ["unknown", "complete"]
    assert all(not (queue / (ident + ".started.json")).exists() for ident, _ in jobs[2:])
    assert all(row["active"] == 0 for row in rows if row["type"] == "close")


def test_existing_started_marker_never_replays_or_dispatches_another_parent(tmp_path):
    jobs = [job(134, 0), job(135, 3)]
    root, queue, checked, rows = run_queue(tmp_path, jobs, started=[jobs[0][0]])
    assert checked.returncode == 0, checked.stderr
    assert not any(row["type"] == "start" for row in rows)
    assert (queue / (jobs[0][0] + ".started.json")).read_text() == '{"preserved":true}'
    reply = json.loads((queue / (jobs[0][0] + ".response.json")).read_text())
    assert reply["status"] == "unknown" and "not replayed" in reply["error"]
    assert (root / "mcp_stop").exists()
    assert not (queue / (jobs[1][0] + ".started.json")).exists()


def test_known_mcp_tool_error_halts_dispatch_but_waits_for_other_paid_reply(tmp_path):
    jobs = [job(134, 0, delay_ms=20, tool_error=True), job(135, 3, delay_ms=100),
            job(136, 0, "facts_0123456789abcdef")]
    root, queue, checked, rows = run_queue(tmp_path, jobs)
    assert checked.returncode == 0, checked.stderr
    assert json.loads((root / "mcp_stop").read_text())["reason"] == "parallel_tool_error_no_new_dispatch"
    assert len([row for row in rows if row["type"] == "start"]) == 2
    assert [json.loads((queue / (ident + ".response.json")).read_text())["status"] for ident, _ in jobs[:2]] == ["error", "complete"]
    assert not (queue / (jobs[2][0] + ".started.json")).exists()
    assert all(row["active"] == 0 for row in rows if row["type"] == "close")


def test_bad_queued_parent_waits_for_already_started_call_before_close(tmp_path):
    jobs = [job(134, 0, delay_ms=100), ("glm_135_legacy_stage", {"prompt": "invalid parallel parent"})]
    _, queue, checked, rows = run_queue(tmp_path, jobs)
    assert checked.returncode != 0 and "parallel_job_parent_invalid" in checked.stderr
    assert len([row for row in rows if row["type"] == "start"]) == 1
    assert (queue / (jobs[0][0] + ".response.json")).exists()
    assert all(row["active"] == 0 for row in rows if row["type"] == "close")


def test_explicit_frozen_unknown_is_skipped_without_reply_or_replay(tmp_path):
    frozen = job(166, 3, 'source_feedback_replan_v1')
    jobs = [frozen, job(167, 0, 'assemble'), job(168, 3, 'local_replan', delay_ms=50)]
    _, queue, checked, rows = run_queue(tmp_path, jobs, stop_at=2, stop_on_start=True,
                                      started=[frozen[0]], frozen_policy=True)
    assert checked.returncode == 0, checked.stderr
    assert [r['job'] for r in rows if r['type'] == 'start'] == [jobs[1][0], jobs[2][0]]
    assert not (queue / (frozen[0] + '.response.json')).exists()
    assert (queue / (frozen[0] + '.started.json')).read_text() == '{"preserved":true}'
    assert all(row['active'] == 0 for row in rows if row['type'] == 'close')


def test_frozen_policy_does_not_skip_another_started_unknown(tmp_path):
    frozen = job(166, 3, 'source_feedback_replan_v1')
    another = job(167, 0, 'assemble')
    jobs = [frozen, another, job(168, 3, 'local_replan')]
    root, queue, checked, rows = run_queue(tmp_path, jobs, started=[frozen[0], another[0]], frozen_policy=True)
    assert checked.returncode == 0, checked.stderr
    assert not any(row['type'] == 'start' for row in rows)
    assert not (queue / (frozen[0] + '.response.json')).exists()
    assert json.loads((queue / (another[0] + '.response.json')).read_text())['status'] == 'unknown'
    assert json.loads((root / 'mcp_stop').read_text())['job_id'] == another[0]


@pytest.mark.parametrize('changed', ['request', 'started', 'policy', 'ledger'])
def test_changed_frozen_binding_rejected_before_connect_or_model_dispatch(tmp_path, changed):
    frozen = job(166, 3, 'source_feedback_replan_v1')
    def mutation(root, queue, policy_file):
        if changed in ('request', 'started'):
            (queue / (frozen[0] + f'.{changed}.json')).write_text('{"changed":true}', encoding='utf-8')
        elif changed == 'policy':
            policy_file.write_text('{"changed":true}', encoding='utf-8')
        else:
            state_file = root / 'library_state.json'
            state = json.loads(state_file.read_text())
            state['calls'][165]['status'] = 'submitted'
            state_file.write_text(json.dumps(state), encoding='utf-8')
    _, queue, checked, rows = run_queue(tmp_path, [frozen, job(167, 0)], started=[frozen[0]],
                                      frozen_policy=True, policy_mutation=mutation)
    assert checked.returncode != 0 and 'library_mcp_frozen_slot_' in checked.stderr
    assert not rows
    assert not (queue / (frozen[0] + '.response.json')).exists()
