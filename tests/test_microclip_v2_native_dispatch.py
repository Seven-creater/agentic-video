"""Real global fetch guard, stubbed proof boundary and HTTP transport.

No official server, model, network request or real task directory is used here.
The companion state/guard tests validate the actual authorization proof loader.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


LIBRARY = Path(__file__).resolve().parents[1] / "omni_story" / "library"
ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ORIGINAL = "glm_224_mc2_region_1"
ALIAS = "glm_226_mc2_region_1_retry_dispatch"
BODY = '{"model":"synthetic-model","messages":[{"role":"user","content":"synthetic"}]}'


def _request(body=BODY, *, job=ORIGINAL, seq=1):
    return {"type": "request", "seq": seq, "job_id": job,
            "hash": hashlib.sha256(body.encode()).hexdigest(), "url": ENDPOINT,
            "body": json.loads(body)}


def _response(*, job=ORIGINAL, seq=1, status=500):
    return {"type": "response", "seq": seq, "job_id": job, "status": status,
            "body": '{"error":{"code":"500","message":"synthetic known failure"}}'}


def _proof():
    return {"dispatchResume": {"retry_stage": "mc2_region_1_retry_dispatch",
                               "baseline_request_count": 225},
            "networkResume": {"failed_call_id": ORIGINAL,
                              "retry_stage": "mc2_region_1_retry",
                              "original_http": {"seq": 1, "status": 500}},
            "state": {"calls": [{"id": ALIAS,
                                   "name": "mc2_region_1_retry_dispatch",
                                   "status": "submitted"}]}}


@pytest.fixture
def native_guard(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Native fetch guard integration requires Node.js")
    copied = tmp_path / "guard"
    copied.mkdir()
    for source in LIBRARY.glob("*.mjs"):
        shutil.copy2(source, copied / source.name)
    # Only the proof-loading boundary is replaced. The global fetch guard and
    # its duplicate journal logic are the repository's actual implementation.
    (copied / "mcp_microclip_v2_guard.mjs").write_text(
        "export function microclipV2RequestLimit() { return Infinity; }\n"
        "export function loadMicroclipV2() {\n"
        "  if (process.env.TEST_PROOF_INVALID === '1') throw new Error('synthetic_invalid_proof');\n"
        "  return JSON.parse(process.env.TEST_PROOF || 'null');\n"
        "}\n", encoding="utf-8")
    package = tmp_path / "package"
    undici = package / "node_modules" / "undici"
    undici.mkdir(parents=True)
    (undici / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (undici / "index.js").write_text("export class Agent {}", encoding="utf-8")
    task = tmp_path / "synthetic_task"
    task.mkdir()
    current = task / "mcp_current.json"
    current.write_text(json.dumps({"job_id": ALIAS}), encoding="utf-8")
    journal = task / "mcp_http.jsonl"
    journal.write_text("\n".join(json.dumps(row) for row in
                                   [_request(), _response()]) + "\n", encoding="utf-8")
    # Pytest can display fixture locals on failure. Use only process plumbing,
    # never inherit API credentials into this synthetic test fixture.
    allowed_environment = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    env = {key: value for key, value in os.environ.items()
           if key.upper() in allowed_environment}
    env.update(OMNI_LIBRARY_MCP_ROOT=str(task), OMNI_LIBRARY_MCP_PACKAGE_ROOT=str(package),
               TEST_PROOF=json.dumps(_proof()))
    script = """
let transports = 0;
globalThis.fetch = async () => {
  transports += 1;
  return new Response('{"fixture":true}', {status:200});
};
await import(process.argv[1]);
const results = [];
for (let i = 0; i < Number(process.argv[3]); i += 1) {
  try {
    await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions',
      {method:'POST', body:process.argv[2]});
    results.push('accepted');
  } catch (error) { results.push(String(error.message)); }
}
console.log(JSON.stringify({transports, results}));
"""

    def run(*, body=BODY, attempts=1, proof=None, invalid=False):
        local = dict(env)
        if proof is not None:
            local["TEST_PROOF"] = json.dumps(proof)
        if invalid:
            local["TEST_PROOF_INVALID"] = "1"
        result = subprocess.run(["node", "--input-type=module", "-e", script,
                                 (copied / "mcp_guard.mjs").as_uri(), body, str(attempts)],
                                env=local, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return {"run": run, "journal": journal, "current": current, "env": env}


def _records(native_guard):
    return [json.loads(line) for line in native_guard["journal"].read_text(encoding="utf-8").splitlines()]


def test_first_explicit_alias_can_post_exact_known_failure_body_once(native_guard):
    result = native_guard["run"]()
    assert result == {"transports": 1, "results": ["accepted"]}
    requests = [row for row in _records(native_guard) if row["type"] == "request"]
    assert [row["job_id"] for row in requests] == [ORIGINAL, ALIAS]
    assert requests[0]["hash"] == requests[1]["hash"]
    assert requests[0]["body"] == requests[1]["body"]


def test_sdk_second_attempt_in_same_process_is_blocked(native_guard):
    result = native_guard["run"](attempts=2)
    assert result["transports"] == 1
    assert result["results"][0] == "accepted"
    assert "retry_or_budget_blocked" in result["results"][1]
    assert sum(row["type"] == "request" for row in _records(native_guard)) == 2


def test_restart_cannot_repost_alias_that_has_already_posted(native_guard):
    assert native_guard["run"]()["transports"] == 1
    result = native_guard["run"]()
    assert result["transports"] == 0
    assert "retry_or_budget_blocked" in result["results"][0]
    assert sum(row["type"] == "request" for row in _records(native_guard)) == 2


@pytest.mark.parametrize("proof_kind", ["absent", "no_dispatch_resume", "invalid"])
def test_duplicate_without_valid_dispatch_proof_is_blocked(native_guard, proof_kind):
    if proof_kind == "absent":
        native_guard["env"]["TEST_PROOF"] = "null"
    elif proof_kind == "no_dispatch_resume":
        native_guard["env"]["TEST_PROOF"] = json.dumps({**_proof(), "dispatchResume": None})
    result = native_guard["run"](invalid=proof_kind == "invalid")
    assert result["transports"] == 0
    assert result["results"][0] != "accepted"
    assert sum(row["type"] == "request" for row in _records(native_guard)) == 1


@pytest.mark.parametrize("already_seen", [False, True])
def test_wrong_hash_cannot_borrow_original_failure_authorization(native_guard, already_seen):
    wrong = '{"model":"synthetic-model","messages":[{"role":"user","content":"different"}]}'
    if already_seen:
        with native_guard["journal"].open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_request(wrong, job="unrelated_known_job", seq=2)) + "\n")
            handle.write(json.dumps(_response(job="unrelated_known_job", seq=2)) + "\n")
    result = native_guard["run"](body=wrong)
    assert result["transports"] == 0
    assert "known500_native_body_changed" in result["results"][0]
    assert sum(row["type"] == "request" for row in _records(native_guard)) == (2 if already_seen else 1)


def test_hash_posted_by_another_job_cannot_receive_exception(native_guard):
    # The real proof loader separately rejects unknown/200 original responses;
    # at this stubbed boundary, exercise the native hash-owner check itself.
    records = [_request(), _response(), _request(job="another_posted_job", seq=2),
               _response(job="another_posted_job", seq=2)]
    native_guard["journal"].write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
    result = native_guard["run"]()
    assert result["transports"] == 0
    assert "known500_native_body_changed" in result["results"][0]


def test_unapproved_job_identity_cannot_use_dispatch_permission(native_guard):
    native_guard["current"].write_text(json.dumps({"job_id": "glm_227_mc2_region_1_retry_dispatch"}), encoding="utf-8")
    result = native_guard["run"]()
    assert result["transports"] == 0
    assert "retry_or_budget_blocked" in result["results"][0]


def test_chunked_journal_reads_long_utf8_line_without_losing_hash_owners(native_guard):
    request = {**_request(), "synthetic_padding": "视觉边界🌟" * 30000}
    original = (json.dumps(request, ensure_ascii=False) + "\n" +
                json.dumps(_response()) + "\n").encode("utf-8")
    assert len(original) > 256 * 1024
    native_guard["journal"].write_bytes(original)
    assert native_guard["run"]() == {"transports": 1, "results": ["accepted"]}
    assert native_guard["journal"].read_bytes().startswith(original)
    result = native_guard["run"]()
    assert result["transports"] == 0
    assert "retry_or_budget_blocked" in result["results"][0]


def test_chunked_journal_retains_request_in_final_line_without_newline(native_guard):
    records = [_request(), _response(), _request(job=ALIAS, seq=2)]
    original = "\r\n".join(json.dumps(row) for row in records).encode("utf-8")
    native_guard["journal"].write_bytes(original)
    result = native_guard["run"]()
    assert result["transports"] == 0
    assert "retry_or_budget_blocked" in result["results"][0]
    assert native_guard["journal"].read_bytes() == original
