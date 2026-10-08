"""No real model calls; verify lane isolation and simultaneous local routing."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from omni_story.library import pipeline, slot_finecut as sf, slot_finecut_budget as budget
from omni_story.library.state import write_json


def test_lane_journal_usage_and_unknown_are_bound_to_their_job(tmp_path):
    for name, entries in {
        'mcp_http.jsonl': [{'job_id': 'old', 'type': 'unknown_result', 'seq': 1}],
        'mcp_http_sf_0.jsonl': [{'job_id': 'left', 'type': 'request', 'seq': 1},
            {'job_id': 'left', 'type': 'response', 'seq': 1, 'status': 200,
             'body': json.dumps({'usage': {'total_tokens': 11}})}],
        'mcp_http_sf_3.jsonl': [{'job_id': 'right', 'type': 'request', 'seq': 1}],
    }.items():
        (tmp_path / name).write_text('\n'.join(map(json.dumps, entries)), encoding='utf-8')
    assert pipeline._usage_for(tmp_path, 'left') == {'total_tokens': 11}
    assert not pipeline._outcome_unknown(pipeline._http_evidence(tmp_path, 'left'))
    assert pipeline._outcome_unknown(pipeline._http_evidence(tmp_path, 'right'))
    assert pipeline._outcome_unknown(pipeline._http_evidence(tmp_path, 'old'))


def test_inflight_partial_journal_tail_is_not_a_lost_reply(tmp_path):
    journal = tmp_path / 'mcp_http_sf_0.jsonl'
    complete = json.dumps({'job_id': 'left', 'type': 'request', 'seq': 1}).encode() + b'\n'
    journal.write_bytes(complete + b'{"job_id":"left","type":"response","body":"\xe4')
    assert pipeline._http_evidence(tmp_path, 'left') == [{'job_id': 'left', 'type': 'request', 'seq': 1}]
    response = {'job_id': 'left', 'type': 'response', 'seq': 1, 'status': 200, 'body': '{}'}
    journal.write_bytes(complete + json.dumps(response).encode() + b'\n')
    assert not pipeline._outcome_unknown(pipeline._http_evidence(tmp_path, 'left'))


def test_parallel_router_starts_both_parents_before_either_finishes(tmp_path, monkeypatch):
    folder = tmp_path / 'artifacts/test'
    folder.mkdir(parents=True)
    knowledge = folder / 'SLOT_FINECUT.md'
    knowledge.write_text('Generic test knowledge', encoding='utf-8')
    methods = folder / 'methods.json'
    write_json(methods, {})
    preparation = {'preparation_id': 'fixture', 'parents': [
        {'baseline_id': f'render_{n}', 'round': n} for n in (0, 3)]}
    grant = {'preparation_path': 'unused', 'knowledge_sha256': sf.sha256_file(knowledge),
             'reference_methods_path': str(methods), 'reference_methods_sha256': sf.sha256_file(methods)}
    def new_state(output):
        return SimpleNamespace(output=tmp_path, data={'artifacts': {'sf_parallel_execution_v1': [{}]}, 'calls': []},
                               assert_protected=lambda: None)
    monkeypatch.setattr(budget, 'SlotFinecutState', new_state)
    monkeypatch.setattr(budget, 'load_authorization', lambda output: grant)
    monkeypatch.setattr(budget, 'execution_folder', lambda *args: folder)
    monkeypatch.setattr(sf, 'load_preparation', lambda path: preparation)
    monkeypatch.setattr(sf, 'CodexMCP', lambda state: None)
    barrier = threading.Barrier(2)
    seen = []
    def run(glm, state, preparation, parent, *args):
        seen.append(parent['round'])
        barrier.wait(timeout=3)
        return {'baseline_id': parent['baseline_id'], 'status': 'synthetic_no_video'}
    monkeypatch.setattr(sf, '_run_parent', run)
    result = sf.execute(tmp_path)
    assert sorted(seen) == [0, 3]
    assert [r['baseline_id'] for r in result['results']] == ['render_0', 'render_3']
    assert result['selection'] is None
