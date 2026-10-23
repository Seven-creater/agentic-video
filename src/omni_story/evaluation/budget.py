"""Opt-in cross-process request reservations; reservations are never refunded."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from decimal import Decimal, ROUND_CEILING

from ..library.state import json_sha, write_json


def _token_upper(usage, output_price):
    if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
            for key in ('prompt_tokens', 'completion_tokens'))):
        return None
    return (Decimal(usage['prompt_tokens'])*Decimal('0.8') +
            Decimal(usage['completion_tokens'])*Decimal(output_price))/Decimal(1000000)


def liability(saved):
    """Conservative list-price exposure, not a bill or a refund of operation slots.

    Missing/failed/uncertain receipts keep the entire per-operation reservation.
    Only current, received Beijing GLM/Qwen/TTS receipts reduce cash exposure.
    """
    profile = saved['cost_control']
    reserve = Decimal(str(profile['per_operation_reserve_cny']))
    reserved = {}
    for row in saved['requests']:
        reserved[row['run_id']] = reserved.get(row['run_id'], 0)+1
    total, received, evidence = Decimal(0), 0, []
    if profile.get('prior_budget_file'):
        from ..library.media import sha256_file
        prior_path = Path(profile['prior_budget_file'])
        if sha256_file(prior_path) != profile['prior_budget_sha256']:
            raise RuntimeError('reliable_prior_budget_changed')
        prior = liability(json.loads(prior_path.read_text()))
        total += Decimal(prior['upper_cny'])
        evidence.append(dict(prior_budget_file=str(prior_path), upper_cny=prior['upper_cny']))
    root = Path(profile['experiment_root'])
    for run_id, count in reserved.items():
        experiment, case, method = run_id.split('/')
        if experiment != saved['experiment_id']:
            raise RuntimeError('evaluation_cost_run_identity_changed')
        folder, known = root/'runs'/case/method, []
        for path in folder.rglob('library_state.json'):
            state = json.loads(path.read_text())
            config = state.get('input_lock', {}).get('configuration', {})
            if config.get('vision_model') != 'glm-5.3-flash':
                continue
            for call in state['calls']:
                if call['status'] == 'received':
                    upper = _token_upper(call.get('usage'), '2.8')
                    if upper is not None:
                        known.append(upper)
                        evidence.append(dict(path=str(path), call_id=call['id'], upper_cny=str(upper)))
        for path in folder.rglob('state.json'):
            state = json.loads(path.read_text())
            if state.get('status') != 'received':
                continue
            upper = None
            if state.get('schema_version') == 'tts_line_v1' and state.get('model') == 'qwen3-tts-flash':
                text = state.get('text')
                if (isinstance(text, str) and 0 < len(text) <= 600 and state.get('request_count') == 1 and
                        state.get('binding', {}).get('payload', {}).get('input', {}).get('text') == text and
                        json_sha(state['binding']) == state.get('request_sha256')):
                    upper = Decimal(2*len(text))*Decimal('0.8')/Decimal(10000)
            elif state.get('policy') == 'actual-audio-video-review-v1':
                request = json.loads(path.with_name('request.json').read_text())
                response = json.loads(path.with_name('response.json').read_text())
                if (request['configuration']['model'] == 'qwen3.8-omni-flash' and
                        request['endpoint'] == 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions' and
                        response.get('http_status') == 200):
                    upper = _token_upper(json.loads(response['body_text']).get('usage'), '2.7')
            if upper is not None:
                known.append(upper)
                evidence.append(dict(path=str(path), upper_cny=str(upper)))
        if len(known) > count or any(value > reserve for value in known):
            raise RuntimeError('evaluation_cost_profile_or_receipt_count_exceeded')
        received += len(known)
        total += sum(known, Decimal(0))+(count-len(known))*reserve
    return dict(upper_cny=str(total.quantize(Decimal('.000001'), rounding=ROUND_CEILING)),
        received_priced_operations=received, unresolved_reserved_operations=len(saved['requests'])-received,
        evidence=evidence, scope='conservative_list_price_exposure_not_invoice')


@contextmanager
def budget_lock(path):
    import fcntl
    with Path(path).open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reserve_request(*, reservation_key=None, audit=None):
    name = os.environ.get('OMNI_EVAL_BUDGET_FILE')
    if not name:
        return
    path = Path(name).resolve(strict=True)
    with budget_lock(path.with_suffix('.lock')):
        saved = json.loads(path.read_text())
        run_id = os.environ['OMNI_EVAL_RUN_ID']
        generation = os.environ.get('OMNI_EVAL_REQUEST_SCOPE', 'generation') == 'generation'
        requests = saved['requests']
        if (saved.get('stop_reason') or (generation and saved.get('generation_stop_reason')) or
                time.time() >= saved['deadline_unix']):
            raise RuntimeError('evaluation_budget_stopped_before_request')
        if reservation_key is not None:
            previous = next((r for r in requests if r.get('reservation_key') == reservation_key), None)
            if previous is not None:
                return previous
        if generation and sum(row.get('scope', 'generation') == 'generation' for row in requests) >= saved['generation_call_limit']:
            raise RuntimeError('evaluation_generation_budget_reserved_for_independent_judge')
        if (len(requests) >= saved['max_model_calls'] or
                sum(row['run_id'] == run_id for row in requests) >= saved['per_run_max_model_calls']):
            raise RuntimeError('evaluation_request_budget_exhausted_before_request')
        if saved.get('cost_control'):
            exposure = liability(saved)
            profile = saved['cost_control']
            if (Decimal(exposure['upper_cny'])+Decimal(str(profile['per_operation_reserve_cny'])) >
                    Decimal(str(profile['spend_envelope_cny']))):
                saved['cost_snapshot'] = exposure
                write_json(path, saved)
                raise RuntimeError('evaluation_currency_envelope_exhausted_before_request')
            saved['cost_snapshot'] = exposure
        requests.append(dict(sequence=len(requests)+1, run_id=run_id,
                             scope='generation' if generation else 'independent_evaluation',
                             reserved_at=datetime.now(timezone.utc).isoformat()))
        if reservation_key is not None:
            requests[-1].update(reservation_key=reservation_key, external_reference_audit=audit or {})
        if saved.get('cost_control'):
            saved['cost_snapshot']['upper_cny'] = str(Decimal(saved['cost_snapshot']['upper_cny'])+
                Decimal(str(saved['cost_control']['per_operation_reserve_cny'])))
            saved['cost_snapshot']['unresolved_reserved_operations'] += 1
        write_json(path, saved)
        return requests[-1]


def stop_new_requests(path, reason, *, generation_only=False):
    path = Path(path)
    with budget_lock(path.with_suffix('.lock')):
        saved = json.loads(path.read_text())
        saved['generation_stop_reason' if generation_only else 'stop_reason'] = reason
        write_json(path, saved)
