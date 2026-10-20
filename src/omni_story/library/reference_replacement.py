"""Verify human-authorized reference replacement; never rewrite the old outcome."""
from copy import deepcopy
import json
from pathlib import Path

from . import contracts
from .media import sha256_file
from .state import LibraryStopped, json_sha


def received_cache(origin):
    origin = Path(origin).resolve()
    marker = origin/'reference_replacement.json'
    if not marker.exists():
        return None
    read = lambda p: json.loads(Path(p).read_text())
    proof = read(marker)
    def require(condition):
        if not condition:
            raise LibraryStopped('authorized_reference_replacement_evidence_changed')
    require(proof.get('policy') == 'human_authorized_received_reference_repair_v1')
    for value in proof['files'].values():
        require(sha256_file(value['path']) == value['sha256'])
    files = proof['files']
    auth, result, cache, adoption = (read(files[name]['path']) for name in ('authorization','result','cache','adoption'))
    root = origin.parents[2]
    folder = Path(auth['source_ledger']).parent
    auth_dir = root/'analysis/case13_protocol_repair_once'
    require(Path(files['authorization']['path']) == auth_dir/'authorization.json'
        and Path(files['result']['path']) == auth_dir/'result.json'
        and Path(files['cache']['path']) == auth_dir/'received-reference-cache.json'
        and Path(files['adoption']['path']) == auth_dir/'offline-adoption.json'
        and folder == origin/'authorized_reference_after_preflight_fix'
        and auth['question_id'] == 'call_b331a1a411ad41ddba736add5b9fde4c'
        and auth['user_answer'] == '允许一次协议修复并继续原计划'
        and auth['max_additional_model_requests'] == 1 and auth['same_cumulative_cap_cny'] == 100
        and adoption['status'] == 'received_fields_merged_offline'
        and adoption['cache_sha256'] == files['cache']['sha256'])
    claim = read(auth_dir/'claim.json')
    require(claim['authorization'] == files['authorization']['path']
        and claim['sha256'] == files['authorization']['sha256'] and claim['max_requests'] == 1)
    for path, digest in {**auth['original_files_sha256'], **auth['parent_files_sha256']}.items():
        require(Path(path).is_relative_to(origin) and sha256_file(path) == digest)
    for name, digest in auth['source_hashes'].items():
        require(sha256_file(Path(auth['client_release'])/'src/omni_story/library'/name) == digest)
    original = read(origin/'library_state.json')
    require(original['request_count'] == len(original['calls']) == 1
        and original['calls'][0]['status'] == 'uncertain'
        and str(origin/'library_state.json') in auth['original_files_sha256'])
    before, current = auth['source_ledger_before'], read(auth['source_ledger'])
    initial_binding = before['input_lock']['configuration']['human_authorized_reference_once']
    require(sha256_file(initial_binding['path']) == initial_binding['sha256'])
    initial_auth = read(initial_binding['path'])
    initial_claim = read(origin/'authorized_reference_after_preflight_fix.claim.json')
    require(initial_auth['question_id'] == 'call_8fc86073985e40638b63f17e1246d3fe'
        and initial_auth['user_answer'] == '允许仅重调一次，计入100元上限'
        and initial_auth['proposed_live_verification_calls'] == 1
        and initial_auth['cumulative_cash_cap_cny'] == 100
        and initial_claim['authorization'] == initial_binding['path']
        and initial_claim['authorization_sha256'] == initial_binding['sha256']
        and initial_claim['max_additional_requests'] == 1)
    for path, digest in initial_auth['original_files_sha256'].items():
        require(sha256_file(path) == digest)
    expected_lock = deepcopy(original['input_lock'])
    expected_lock['configuration']['human_authorized_reference_once'] = initial_binding
    require(before['input_lock'] == expected_lock)
    initial_reservations = [r for r in auth['budget_before']['requests']
        if r.get('explicit_human_exception', {}).get('authorization') == initial_binding['path']]
    require(len(initial_reservations) == 1
        and initial_reservations[0]['explicit_human_exception']['sha256'] == initial_binding['sha256']
        and initial_reservations[0]['explicit_human_exception']['original_request_sha256']
            == original['calls'][0]['request_sha256'])
    require(read(auth_dir/'ledger_before.json') == before
        and before['request_count'] == len(before['calls']) == 1
        and before['calls'][0] == auth['received_call'] and before['calls'][0]['status'] == 'received'
        and before['calls'][0]['request_sha256'] == original['calls'][0]['request_sha256']
        and current['input_lock'] == before['input_lock'] and current['max_requests'] is None
        and current['request_count'] == len(current['calls']) == 2
        and current['calls'][0] == before['calls'][0])
    call = current['calls'][1]
    require(call == cache['call'] and call['status'] == 'received'
        and call['name'] == 'reference_repair' and call['repair_of'] == before['calls'][0]['id']
        and cache['request'] == auth['request'] and json_sha(cache['request']) == call['request_sha256']
        == auth['request_sha256'] and json_sha(cache['response']) == call['response_sha256'])
    require((folder/'mcp_http.jsonl').read_bytes().startswith((auth_dir/'journal_before.jsonl').read_bytes()))
    from .pipeline import _http_evidence, _captured_reply, _outcome_unknown
    readings = []
    for row in current['calls']:
        path = folder/'calls'/row['id']
        request, response = read(path/'request.json'), read(path/'response.json')
        require(json_sha(request) == row['request_sha256'] and json_sha(response) == row['response_sha256'])
        events = _http_evidence(folder, row['id'])
        receipt, captured = _captured_reply(events)
        sent = [e for e in events if e.get('type') == 'request']
        require(len(sent) == 1 and receipt and receipt['seq'] == sent[0]['seq']
            and captured == response and not _outcome_unknown(events) and response.get('finish_reason') == 'stop')
        readings.append(contracts.parse_model_json('\n'.join(c['text'] for c in response['result']['content']
                                                           if c.get('type') == 'text')))
    reading = readings[1]
    from .reference_repair import merge_missing_fields
    reading, derivation = merge_missing_fields(readings[0], reading)
    require(reading == adoption['reading'] and derivation == adoption['derivation']
        and cache['original_received'] == dict(call=before['calls'][0],
            request=read(folder/'calls'/before['calls'][0]['id']/'request.json'),
            response=read(folder/'calls'/before['calls'][0]['id']/'response.json')))
    request = cache['request']; scope = request['observation_scope']
    require(sha256_file(request['arguments']['video_source']) == request['media_sha256']
        and original['input_lock']['reference_sha256'] == scope['source_sha256'])
    contracts.validate_reference(reading, scope['source_sha256'], scope['source_end_s'])
    require(all(k in reading.get('strategy', {}) for k in ('adaptation_mode','speech_mode','timing_driver')))
    contracts.text(reading.get('speech_form'), 'reference/speech_form')
    form = reading.get('visual_form')
    require(isinstance(form, dict) and isinstance(form.get('evidence'), list))
    contracts.text(form.get('description'), 'reference/visual_form/description')
    for evidence in form['evidence']:
        require(isinstance(evidence, dict) and any(all(evidence.get(k) == actual[k]
            for k in ('start_s','end_s','observed_fact')) for actual in readings[0]['visible_evidence']))
    prior, after = auth['budget_before'], result['budget_after']
    require(after['requests'][:-1] == prior['requests'] and len(after['requests']) == len(prior['requests'])+1
        and after['stop_reason'] == prior['stop_reason']
        == 'unknown_child_no_new_work:'+str(origin/'library_state.json')
        and after.get('generation_stop_reason') == prior.get('generation_stop_reason')
        and after['cost_control'] == prior['cost_control'] and after['batch_job_id'] == prior['batch_job_id'])
    reserved = after['requests'][-1]
    require(reserved['sequence'] == len(after['requests']) and reserved['scope'] == 'generation'
        and reserved['run_id'] == '/'.join((after['experiment_id'], origin.parent.name, origin.name))
        and reserved['explicit_human_exception'] == dict(authorization=files['authorization']['path'],
            sha256=files['authorization']['sha256'],claim=str(auth_dir/'claim.json'),
            request_sha256=call['request_sha256'],repair_of=call['repair_of']))
    budget = read(root/'request_budget.json')
    require(budget['requests'][:len(after['requests'])] == after['requests']
        and budget['cost_control'] == after['cost_control'] and budget['batch_job_id'] == after['batch_job_id'])
    if (budget.get('stop_reason'), budget.get('generation_stop_reason')) != (
            after.get('stop_reason'), after.get('generation_stop_reason')):
        resume_path = auth_dir/'resume-budget.json'
        resume = read(resume_path)
        require(budget.get('authorized_reference_resume') == dict(path=str(resume_path), sha256=sha256_file(resume_path))
            and resume['authorization_sha256'] == files['authorization']['sha256']
            and resume['cache_sha256'] == files['cache']['sha256']
            and resume['before']['requests'] == after['requests']
            and resume['before']['stop_reason'] == after['stop_reason']
            and resume['before'].get('generation_stop_reason') == after.get('generation_stop_reason')
            and resume['permitted_stop_reason'] is None
            and resume['before']['cost_control'] == after['cost_control'])
    for key in ('experiment_id','manifest_sha256','max_model_calls','per_run_max_model_calls','generation_call_limit'):
        require(prior[key] == after[key] == budget[key])
    return Path(files['cache']['path'])
