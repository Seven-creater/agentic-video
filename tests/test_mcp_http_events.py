"""Streaming job attribution; no official SDK, model or network requests."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import omni_story.library as library_package

MODULE = (Path(library_package.__file__).resolve().parent / 'mcp_http_events.mjs').as_uri()
pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Requires Node')


def run(tmp_path, raw, job='actual-job'):
    journal = tmp_path / 'http.jsonl'
    journal.write_bytes(raw)
    script = f"""
import fs from 'node:fs';
import {{jobHttpEvents}} from {json.dumps(MODULE)};
const file={json.dumps(str(journal))}, job={json.dumps(job)}, original=fs.readFileSync;
fs.readFileSync=(name,...args)=>{{if(String(name)===file)throw new Error('whole_journal_read_forbidden');return original(name,...args);}};
console.log(JSON.stringify(jobHttpEvents(file,job)));
"""
    # Do not inherit API credentials into a test subprocess.
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATHEXT'}}
    return subprocess.run(['node', '--input-type=module', '-e', script], env=env,
                          capture_output=True, text=True, encoding='utf-8')


def test_streaming_matches_only_exact_job_and_preserves_unicode_body(tmp_path):
    entries = [{'type': 'request', 'job_id': 'actual-job', 'seq': 1,
                'body': {'text': '帧' * 150000}},
               {'type': 'response', 'job_id': 'other', 'body': 'actual-job inside text'},
               {'type': 'response', 'job_id': 'actual-job', 'seq': 1, 'status': 200,
                'body': '{"choices":[{"message":{"content":"原回复"}}]}' }]
    raw = '\r\n'.join(json.dumps(v, ensure_ascii=False) for v in entries).encode('utf8')
    result = run(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [entries[0], entries[2]]


def test_unknown_event_is_retained_with_missing_response(tmp_path):
    entries = [{'type': 'request', 'job_id': 'actual-job', 'seq': 2},
               {'type': 'unknown_result', 'job_id': 'actual-job', 'seq': 2, 'error': 'timeout'}]
    result = run(tmp_path, b'\n'.join(json.dumps(v).encode() for v in entries) + b'\n')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == entries


def test_corrupt_matching_event_does_not_become_success(tmp_path):
    result = run(tmp_path, b'{"job_id":"actual-job", broken}\n')
    assert result.returncode != 0
    assert 'SyntaxError' in result.stderr


def test_nonmatching_incomplete_other_lane_is_not_this_jobs_evidence(tmp_path):
    entry = {'type': 'response', 'job_id': 'actual-job', 'seq': 3, 'status': 500}
    result = run(tmp_path, json.dumps(entry).encode() + b'\n{"job_id":"other",')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [entry]
