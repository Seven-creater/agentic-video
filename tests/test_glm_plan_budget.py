"""GLM Coding Plan reservations count operations without consuming currency exposure."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from omni_story.evaluation.budget import liability, reserve_request
from omni_story.library.state import write_json


def budget_file(tmp_path, **extra):
    path = tmp_path/'budget.json'
    write_json(path, dict(dict(experiment_id='exp', max_model_calls=5, per_run_max_model_calls=5,
        generation_call_limit=5, deadline_unix=time.time()+60, requests=[],
        cost_control=dict(experiment_root=str(tmp_path), spend_envelope_cny=1,
            per_operation_reserve_cny=1)), **extra))
    return path


def test_glm_reserves_after_the_currency_envelope_is_full(tmp_path, monkeypatch):
    path = budget_file(tmp_path)
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE', str(path))
    monkeypatch.setenv('OMNI_EVAL_RUN_ID', 'exp/case/full')
    reserve_request()  # A list-price Qwen call fills the 1 CNY envelope.
    with pytest.raises(RuntimeError, match='currency_envelope_exhausted'):
        reserve_request()
    row = reserve_request(provider='glm_coding_plan')
    assert row['provider'] == 'glm_coding_plan'
    saved = json.loads(path.read_text())
    assert [r['provider'] for r in saved['requests']] == ['list_price', 'glm_coding_plan']
    assert float(saved['cost_snapshot']['upper_cny']) == 1  # the plan row added no reserve
    assert saved['cost_snapshot']['unresolved_reserved_operations'] == 1


def test_glm_rows_still_consume_operation_counts(tmp_path, monkeypatch):
    path = budget_file(tmp_path, max_model_calls=5, per_run_max_model_calls=1)
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE', str(path))
    monkeypatch.setenv('OMNI_EVAL_RUN_ID', 'exp/case/full')
    reserve_request(provider='glm_coding_plan')
    with pytest.raises(RuntimeError, match='request_budget_exhausted'):
        reserve_request(provider='glm_coding_plan')
    monkeypatch.setenv('OMNI_EVAL_RUN_ID', 'exp/case/baseline')
    reserve_request(provider='glm_coding_plan')
    narrow = budget_file(tmp_path/'narrow', max_model_calls=2, per_run_max_model_calls=2)
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE', str(narrow))
    monkeypatch.setenv('OMNI_EVAL_RUN_ID', 'exp/case/full')
    reserve_request(provider='glm_coding_plan')
    reserve_request(provider='glm_coding_plan')
    with pytest.raises(RuntimeError, match='request_budget_exhausted'):
        reserve_request(provider='glm_coding_plan')


def test_liability_ignores_glm_money_and_keeps_legacy_rows_priced(tmp_path):
    saved = dict(experiment_id='exp', requests=[
        dict(run_id='exp/case/full'),  # legacy row without a provider field
        dict(run_id='exp/case/full', provider='list_price'),
        dict(run_id='exp/case/full', provider='glm_coding_plan')],
        cost_control=dict(experiment_root=str(tmp_path), per_operation_reserve_cny=1))
    result = liability(saved)
    assert float(result['upper_cny']) == 2  # only the two list-price rows reserve 1 CNY each
    assert result['glm_plan_operations'] == 1
    assert result['received_priced_operations'] == 0
    assert result['unresolved_reserved_operations'] == 2


def glm_call(call_id, prompt_tokens):
    return dict(id=call_id, status='received',
                usage=dict(prompt_tokens=prompt_tokens, completion_tokens=0))


def test_glm_receipts_are_plan_covered_not_token_priced(tmp_path):
    folder = tmp_path/'runs/case/full'; folder.mkdir(parents=True)
    # 1M prompt tokens would cost 0.8 CNY and exceed the 1 CNY per-operation reserve.
    write_json(folder/'library_state.json', dict(
        input_lock=dict(configuration=dict(vision_model='glm-5.3-flash')),
        calls=[glm_call('glm_001', 1000000)]))
    saved = dict(experiment_id='exp',
        requests=[dict(run_id='exp/case/full', provider='glm_coding_plan')],
        cost_control=dict(experiment_root=str(tmp_path), per_operation_reserve_cny=1))
    result = liability(saved)
    assert float(result['upper_cny']) == 0 and result['glm_plan_operations'] == 1


def test_mixed_run_prices_only_the_uncovered_glm_receipts(tmp_path):
    folder = tmp_path/'runs/case/full'; folder.mkdir(parents=True)
    write_json(folder/'library_state.json', dict(
        input_lock=dict(configuration=dict(vision_model='glm-5.3-flash')),
        calls=[glm_call('glm_001', 100000), glm_call('glm_002', 200000)]))
    saved = dict(experiment_id='exp', requests=[
        dict(run_id='exp/case/full', provider='list_price'),
        dict(run_id='exp/case/full', provider='glm_coding_plan')],
        cost_control=dict(experiment_root=str(tmp_path), per_operation_reserve_cny=1))
    result = liability(saved)
    assert float(result['upper_cny']) == 0.16  # the larger receipt stays priced
    assert result['received_priced_operations'] == 1 and result['glm_plan_operations'] == 1


def test_unknown_provider_is_rejected_before_any_reservation():
    with pytest.raises(ValueError, match='evaluation_budget_provider_invalid'):
        reserve_request(provider='qwen_monthly_pass')


def test_reference_gate_passes_provider_through(tmp_path, monkeypatch):
    path = budget_file(tmp_path)
    monkeypatch.setenv('OMNI_EVAL_BUDGET_FILE', str(path))
    monkeypatch.setenv('OMNI_EVAL_RUN_ID', 'exp/case/full')
    env = {**os.environ,
           'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')+os.pathsep+os.environ.get('PYTHONPATH', '')}

    def gate(*args):
        done = subprocess.run([sys.executable, '-m', 'omni_story.evaluation.reference_budget_gate', *args],
                              capture_output=True, text=True, timeout=60, env=env)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    first = gate('--key', 'subject_understanding', '--evidence', 'runs/case/full/library_state.json',
                 '--provider', 'glm_coding_plan')
    second = gate('--key', 'tts_line', '--evidence', 'runs/case/full/tts')
    rows = json.loads(path.read_text())['requests']
    assert rows[first['sequence']-1]['provider'] == 'glm_coding_plan'
    assert rows[second['sequence']-1]['provider'] == 'list_price'
    assert gate('--key', 'subject_understanding', '--evidence', 'runs/case/full/library_state.json',
                '--provider', 'glm_coding_plan')['sequence'] == first['sequence']
