"""One explicit continuation of a received search rejected by local ID handling."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

from . import pipeline, server_jobs
from .media import sha256_file
from .state import LibraryStopped, file_lock, json_sha

POLICY = 'received_search_window_recovery_v1'
PLAN_POLICY = 'received_plan_cast_correction_v1'
TRANSPORT_POLICY = 'received_plan_cast_transport_v1'
MAIN_POLICY = 'main_authored_plan_binding_split_v1'
CAPTION_POLICY = 'main_plan_caption_reflow_v1'
TTS_POLICY = 'human_authorized_tts_audio_resume_v1'


def _prefix(policy):
    return ('tts_audio_resume_v1' if policy==TTS_POLICY else 'caption_reflow_repair_v1' if policy==CAPTION_POLICY else 'plan_binding_repair_v1' if policy==MAIN_POLICY else 'plan_cast_transport_v1' if policy==TRANSPORT_POLICY else
            'plan_cast_correction_v1' if policy==PLAN_POLICY else 'search_recovery_v1')


def validate(output, proof_path):
    output, proof_path = Path(output).resolve(strict=True), Path(proof_path).resolve(strict=True)
    proof = json.loads(proof_path.read_text())
    if proof.get('policy') == TTS_POLICY:
        from .tts_retry_resume import validate as validate_audio_resume
        return validate_audio_resume(output, proof_path)
    if proof.get('policy') in {MAIN_POLICY,CAPTION_POLICY}:
        from .main_plan_binding import validate as validate_main
        return validate_main(output,proof_path)
    if proof.get('policy') not in {POLICY, PLAN_POLICY, TRANSPORT_POLICY} or proof.get('output') != str(output):
        raise LibraryStopped('search_recovery:wrong_scope')
    required = {output/name for name in ('chain_failure.json', 'chain_started', 'mcp_stop')}
    if proof['policy'] in {PLAN_POLICY,TRANSPORT_POLICY}:
        required.add(output/'search_recovery_v1.chain_failure.json')
        required.add(Path(proof['baseline_snapshot']))
    transport=proof['policy']==TRANSPORT_POLICY
    if transport:
        required.update([output/'plan_cast_correction_v1.chain_failure.json',Path(proof['previous_proof']),
            output/'calls'/proof['failed_preflight_id']/'failure.json'])
    saved = json.loads((output/'library_state.json').read_text())
    for call in saved['calls']:
        required.add(output/'calls'/call['id']/'request.json')
        if call['status']=='received':required.add(output/'calls'/call['id']/'response.json')
        failure = output/'calls'/call['id']/'protocol_failure.json'
        if failure.exists():
            required.add(failure)
    if not {str(path) for path in required} <= set(proof['protected_sha256']):
        raise LibraryStopped('search_recovery:original_pins_required')
    for path, digest in proof['protected_sha256'].items():
        if sha256_file(path) != digest:
            raise LibraryStopped('search_recovery:original_changed:' + str(path))
    if not any(Path(row['output']).resolve() == output.parent for row in proof['failed_jobs']):
        raise LibraryStopped('search_recovery:original_worker_binding_required')
    for binding in proof['failed_jobs']:
        job = server_jobs.status(binding['output'])
        if (job['job_id'] != binding['job_id'] or job['state'] != 'failed' or
                job.get('supervisor_alive') or job.get('stop_requested') or
                server_jobs._group_running(job.get('child_pid'))):
            raise LibraryStopped('search_recovery:original_not_settled_failed')
    baseline=saved['calls'][:-1] if transport else saved['calls']
    if transport:
        previous_path=Path(proof['previous_proof']);previous=json.loads(previous_path.read_text())
        prior_state=json.loads(Path(previous['baseline_snapshot']).read_text())
        failed=saved['calls'][-1]
        failure_path=output/'calls'/failed['id']/'failure.json'
        local_failure=json.loads(failure_path.read_text())
        journal=output/'mcp_http.jsonl'
        if (previous['policy']!=PLAN_POLICY or sha256_file(previous_path)!=proof['previous_proof_sha256'] or
                baseline!=prior_state['calls'] or saved['input_lock']!=prior_state['input_lock'] or
                sha256_file(previous['baseline_snapshot'])!=previous['state_sha256'] or
                failed['id']!=proof['failed_preflight_id'] or failed['name']!='plan_1_cast_correction_v1' or
                failed['status']!='failed_known' or local_failure.get('uncertain') is not False or
                'library_mcp_opencode_stage_duplicate_or_not_permitted' not in local_failure['error'] or
                any(json.loads(line).get('job_id')==failed['id'] and json.loads(line).get('type')=='request'
                    for line in journal.read_text().splitlines() if line.strip())):
            raise LibraryStopped('search_recovery:preflight_not_proven_local')
        original_failed=json.loads((output/'calls'/failed['id']/'request.json').read_text())
        expected=proof['expected_request']
        restored={**expected,'arguments':{**expected['arguments'],'prompt':original_failed['arguments']['prompt']}}
        if (original_failed!=previous['expected_request'] or restored!=original_failed or
                expected['arguments']['prompt']!=original_failed['arguments']['prompt']+proof['transport_prompt_suffix'] or
                proof.get('correction_alias')!='plan_1_cast_correction_v1_preflight_v1'):
            raise LibraryStopped('search_recovery:preflight_input_changed')
    if (sha256_file(output/'library_state.json') != proof['state_sha256'] or
            saved['task_id'] != proof['task_id'] or
            saved['request_count'] != len(saved['calls']) or
            not baseline or any(call['status'] != 'received' for call in baseline)):
        raise LibraryStopped('search_recovery:received_baseline_required')
    search = proof['search_name']
    if not isinstance(search, str) or not re.fullmatch(r'search_[0-9]+', search):
        raise LibraryStopped('search_recovery:invalid_stage')
    failure = json.loads((output/'chain_failure.json').read_text())
    if failure['error'] != 'model_protocol_repair_exhausted:' + search:
        raise LibraryStopped('search_recovery:wrong_failure')
    if proof['policy'] in {PLAN_POLICY,TRANSPORT_POLICY}:
        pair=baseline[-2:]
        stage=proof.get('plan_name')
        if (stage != 'plan_1' or len(saved['calls']) != proof.get('baseline_request_count') or
                proof.get('max_new_correction_calls') != 1 or
                [call['name'] for call in pair] != [stage,stage+'_repair'] or
                pair[1].get('repair_of') != pair[0]['id']):
            raise LibraryStopped('search_recovery:plan_correction_scope_changed')
        prior_failure=json.loads((output/'search_recovery_v1.chain_failure.json').read_text())
        if prior_failure['error'] != 'model_protocol_repair_exhausted:'+stage:
            raise LibraryStopped('search_recovery:plan_failure_required')
        for call in pair:
            diagnostic=json.loads((output/'calls'/call['id']/'protocol_failure.json').read_text())
            if diagnostic['error'] != 'plan:cast_entity_not_supported_by_observation':
                raise LibraryStopped('search_recovery:known_cast_failure_required')
        original=json.loads((output/'calls'/pair[0]['id']/'request.json').read_text())
        expected=proof['expected_request']
        restored={**expected,'arguments':{**expected['arguments'],'prompt':original['arguments']['prompt']}}
        if (restored != original or not expected['arguments']['prompt'].startswith(original['arguments']['prompt']+'\n') or
                expected['arguments']['prompt']==original['arguments']['prompt']):
            raise LibraryStopped('search_recovery:plan_correction_input_changed')
        media=expected['arguments'].get('image_source') or expected['arguments'].get('video_source')
        if sha256_file(media)!=expected['media_sha256']:
            raise LibraryStopped('search_recovery:plan_correction_media_changed')
    if (output/'chain_result.json').exists() or (output/'result.json').exists():
        raise LibraryStopped('search_recovery:already_completed')
    stop = output/'mcp_stop'
    if stop.read_text() != 'reference_rough_skill_settled':
        raise LibraryStopped('search_recovery:user_stop_cannot_be_cleared')
    state = SimpleNamespace(output=output, data=saved)
    windows = pipeline._received_windows(state, visual_only=True)
    entry = saved['artifacts']['shared_visual_library'][-1]
    snapshot = json.loads(Path(entry['path']).read_text())
    if json_sha(snapshot) != entry['sha256']:
        raise LibraryStopped('search_recovery:candidate_snapshot_changed')
    cards = {row['observation_id']: row for row in snapshot['candidates']}
    rejected = set()
    round_no = int(search.removeprefix('search_'))
    for index in range(round_no):
        prior = json.loads((output/f'search_{index}.json').read_text())
        rejected.update(prior.get('rejected_observation_ids', []))
        path = output/f'retrieval_{index}.json'
        if path.exists():
            retrieval = json.loads(path.read_text())
            if (json_sha(retrieval['result']) != retrieval['sha256'] or
                    retrieval['result']['search_sha256'] != json_sha(prior)):
                raise LibraryStopped('search_recovery:retrieval_snapshot_changed')
            cards.update((row['observation_id'], row) for row in retrieval['result']['candidates'])
    catalog = json.loads((output/'catalog/inventory.json').read_text())
    if [dict(source_id=row['source_id'], sha256=row['sha256']) for row in catalog['sources']] != saved['input_lock']['library_sources']:
        raise LibraryStopped('search_recovery:original_source_catalog_changed')
    selected = pipeline._received_library_search(state, search, catalog,
        [row for ident, row in cards.items() if ident not in rejected],
        max_windows=None, received_windows=windows)
    if selected is None:
        raise LibraryStopped('search_recovery:received_search_required')
    return proof, selected


def claim(output, proof_path):
    output = Path(output)
    with file_lock(output/'.mcp-stop.lock'):
        scope=json.loads(Path(proof_path).read_text())
        prefix=_prefix(scope.get('policy'))
        original_stop = output/(prefix+'.original_mcp_stop')
        if original_stop.exists():
            raise LibraryStopped('search_recovery:already_claimed')
        proof, _ = validate(output, proof_path)
        receipt = dict(policy=proof.get('policy', POLICY), proof_path=str(proof_path), proof_sha256=sha256_file(proof_path),
                       original_task_id=proof['task_id'], original_state_sha256=proof['state_sha256'])
        # Exclusive claim grants one continuation; original supervisor, failures,
        # requests and responses remain in place. This is never an automatic retry.
        with (output/(prefix+'.claim.json')).open('x') as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
        (output/'mcp_stop').rename(original_stop)
        # Also catch a stop written between validation and the atomic rename.
        # Never overwrite a newer marker that arrived after the rename.
        if original_stop.read_text() != 'reference_rough_skill_settled':
            try:
                with (output/'mcp_stop').open('x') as handle:
                    handle.write(original_stop.read_text())
            except FileExistsError:
                pass
            raise LibraryStopped('search_recovery:concurrent_user_stop')
    return output/(prefix+'.chain_failure.json')


def correct_plan(state, model, stage, validator):
    """One separately bound correction after new deterministic cast diagnostics."""
    from .main_plan_binding import load
    authored=load(state,stage,validator)
    if authored is not None:return authored
    claim_path=state.output/'plan_cast_transport_v1.claim.json'
    if not claim_path.exists():claim_path=state.output/'plan_cast_correction_v1.claim.json'
    if not claim_path.exists():
        return None
    claim=json.loads(claim_path.read_text());proof_path=Path(claim['proof_path'])
    if sha256_file(proof_path)!=claim['proof_sha256']:
        raise LibraryStopped('plan_correction:proof_changed')
    proof=json.loads(proof_path.read_text())
    if proof['plan_name']!=stage:
        return None
    snapshot=Path(proof['baseline_snapshot'])
    if sha256_file(snapshot)!=proof['state_sha256']:
        raise LibraryStopped('plan_correction:baseline_changed')
    baseline=json.loads(snapshot.read_text());state._reload()
    if (state.data['input_lock']!=baseline['input_lock'] or state.data['task_id']!=baseline['task_id'] or
            state.data['calls'][:len(baseline['calls'])]!=baseline['calls']):
        raise LibraryStopped('plan_correction:original_calls_changed')
    alias=proof.get('correction_alias',stage+'_cast_correction_v1')
    aliases=[row for row in state.data['calls'] if row['name']==alias]
    request=proof['expected_request']
    if (len(aliases)>1 or any(row['status']!='received' for row in aliases) or
            any(row['request_sha256']!=json_sha(request) for row in aliases) or
            len(state.data['calls'])!=len(baseline['calls'])+len(aliases)):
        raise LibraryStopped('plan_correction:unresolved_original_no_replay')
    media=request['arguments'].get('image_source') or request['arguments'].get('video_source')
    if sha256_file(media)!=request['media_sha256'] or (state.output/'mcp_stop').exists():
        raise LibraryStopped('plan_correction:media_changed_or_stopped')
    call,reply=model._submit(alias,request)
    state._reload()
    call=next(row for row in state.data['calls'] if row['id']==call['id'])
    if call['name']!=alias or call['request_sha256']!=json_sha(request) or call['status']!='received':
        raise LibraryStopped('plan_correction:bound_call_required')
    text='\n'.join(row['text'] for row in reply['result']['content'] if row.get('type')=='text')
    try:
        value=pipeline.contracts.parse_model_json(text)
        validator(value)
    except (ValueError,TypeError,KeyError) as error:
        from .visual_story_trial import write_once
        write_once(state.output/'calls'/call['id']/'protocol_failure.json',
            dict(error=str(error),model_text=text,one_bound_correction=True))
        raise LibraryStopped('plan_correction:single_received_correction_invalid:'+str(error)) from error
    from .visual_story_trial import write_once
    write_once(state.output/'calls'/call['id']/'parsed.json',value)
    return value


def mark_settled(output):
    """A chain's completion must never replace an existing user stop marker."""
    output = Path(output)
    with file_lock(output/'.mcp-stop.lock'):
        try:
            with (output/'mcp_stop').open('x') as handle:
                handle.write('reference_rough_skill_settled')
        except FileExistsError:
            pass
