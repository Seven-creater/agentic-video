"""Explicit main-authored cast edit, preserved separately from model evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from . import contracts, pipeline, server_jobs
from .media import sha256_file
from .state import LibraryStopped

POLICY='main_authored_plan_binding_split_v1'
CAPTION_POLICY='main_plan_caption_reflow_v1'


def unparsed_received_plan(folder,reply):
    if (Path(folder)/'parsed.json').exists():raise LibraryStopped('main_plan_binding:original_rejection_must_remain')
    return contracts.parse_model_json('\n'.join(row['text'] for row in reply['result']['content'] if row.get('type')=='text'))


def apply_binding_split(source,windows,patch):
    """Execute the recorded author choice; never infer a cross-window identity."""
    result=deepcopy(source)
    if patch['operation']!='split_one_unsupported_cast_binding':
        raise LibraryStopped('main_plan_binding:unsupported_edit')
    binding=patch['binding'];character=next(c for c in result['cast'] if c['cast_id']==patch['cast_id'])
    window=next(w for w in windows if w['window_id']==binding['window_id'])
    role=next(r for r in window['observation']['roles'] if r['role_id']==binding['role_id'])
    if (window['status']!='watched' or role['identity_confirmed'] is not True or
            role['entity_id']==character['entity_id'] or binding not in character['bindings'] or
            patch['new_cast_id'] in {c['cast_id'] for c in result['cast']}):
        raise LibraryStopped('main_plan_binding:unsupported_identity_split')
    character['bindings'].remove(binding)
    result['cast'].append(dict(cast_id=patch['new_cast_id'],entity_id=role['entity_id'],
        voice=character['voice'],bindings=[deepcopy(binding)]))
    known={window['window_id']:window for window in windows}
    for edit in patch.get('segment_role_removals',[]):
        segment=next(row for row in result['segments'] if row['segment_id']==edit['segment_id'])
        window=known[segment['window_id']];offset=window['source_start_s']
        overlapping={role for event in window['observation']['events']
            if event['local_start_s']<segment['source_out_s']-offset and segment['source_in_s']-offset<event['local_end_s']
            for role in event['role_ids']}
        if edit['role_id'] not in segment['role_ids'] or edit['role_id'] in overlapping:
            raise LibraryStopped('main_plan_binding:role_removal_not_supported')
        segment['role_ids'].remove(edit['role_id'])
        segment['reason']=edit['reason']
    return result


def validate(output,proof_path,*,claimed=False):
    output=Path(output);proof_path=Path(proof_path);proof=json.loads(proof_path.read_text())
    if proof['policy'] not in {POLICY,CAPTION_POLICY} or proof['output']!=str(output) or proof['author']!='current_conversation_main_agent':
        raise LibraryStopped('main_plan_binding:wrong_scope')
    caption=proof['policy']==CAPTION_POLICY
    prefix='caption_reflow_repair_v1' if caption else 'plan_binding_repair_v1'
    baseline=Path(proof['baseline_snapshot'])
    if sha256_file(baseline)!=proof['state_sha256']:
        raise LibraryStopped('main_plan_binding:baseline_changed')
    old=json.loads(baseline.read_text());now=json.loads((output/'library_state.json').read_text())
    if (old['task_id']!=proof['task_id'] or old['request_count']!=12 or len(old['calls'])!=12 or
            now['calls'][:12]!=old['calls'] or (not claimed and len(now['calls'])!=12) or
            now['request_count']!=len(now['calls']) or
            any(c['status']!='received' for c in now['calls'][12:]) or
            now['task_id']!=old['task_id'] or now['input_lock']!=old['input_lock'] or
            any(c['status']!='received' for c in old['calls'][:10]+old['calls'][11:]) or
            old['calls'][10]['status']!='failed_known' or
            any(c['status'] in {'submitted','uncertain','unknown'} for c in now['calls'])):
        raise LibraryStopped('main_plan_binding:original_calls_not_settled')
    if not claimed and sha256_file(output/'library_state.json')!=proof['state_sha256']:
        raise LibraryStopped('main_plan_binding:admission_state_changed')
    required={str(baseline),str(output/'mcp_stop'),str(output/'plan_cast_transport_v1.chain_failure.json'),
        proof['plan_path'],proof['previous_proof'],str(output/'calls'/proof['source_call_id']/'protocol_failure.json')}
    if caption:
        required.update([proof['caption_parent_proof'],str(output/'plan_binding_repair_v1.chain_failure.json'),str(output/'plan_1.json')])
        parent=json.loads(Path(proof['caption_parent_proof']).read_text())
        failure=json.loads((output/'plan_binding_repair_v1.chain_failure.json').read_text())
        if (sha256_file(proof['caption_parent_proof'])!=proof['caption_parent_proof_sha256'] or
                parent['policy']!=POLICY or parent['main_patch']!=proof['main_patch'] or
                parent['plan_path']!=proof['plan_path'] or
                failure['error']!='library_render_caption_layout_outside_canvas:0' or
                proof['caption_reflow_policy']!='same_words_safe_line_breaks_v1' or
                not claimed and any((output/'render_1').iterdir())):
            raise LibraryStopped('main_plan_binding:caption_recovery_scope_changed')
    for call in old['calls']:
        required.add(str(output/'calls'/call['id']/'request.json'))
        required.add(str(output/'calls'/call['id']/('failure.json' if call['status']=='failed_known' else 'response.json')))
    if not required<=set(proof['protected_sha256']):
        raise LibraryStopped('main_plan_binding:original_pins_required')
    for name,digest in proof['protected_sha256'].items():
        actual=output/(prefix+'.original_mcp_stop') if claimed and name==str(output/'mcp_stop') else Path(name)
        if sha256_file(actual)!=digest:raise LibraryStopped('main_plan_binding:protected_file_changed:'+name)
    if not claimed:
        for binding in proof['failed_jobs']:
            job=server_jobs.status(binding['output'])
            if (job['job_id']!=binding['job_id'] or job['state']!='failed' or job.get('supervisor_alive') or
                    job.get('stop_requested') or server_jobs._group_running(job.get('child_pid'))):
                raise LibraryStopped('main_plan_binding:old_job_not_settled')
        if (output/'mcp_stop').read_text()!='reference_rough_skill_settled':
            raise LibraryStopped('main_plan_binding:user_stop')
    elif (output/'mcp_stop').exists():raise LibraryStopped('main_plan_binding:user_stop')
    previous=json.loads(Path(proof['previous_proof']).read_text())
    if (sha256_file(proof['previous_proof'])!=proof['previous_proof_sha256'] or
            previous['policy']!='received_plan_cast_transport_v1' or previous['output']!=str(output) or
            sha256_file(previous['baseline_snapshot'])!=previous['state_sha256'] or
            old['calls'][:11]!=json.loads(Path(previous['baseline_snapshot']).read_text())['calls']):
        raise LibraryStopped('main_plan_binding:previous_transport_changed')
    failed=old['calls'][10];failure=json.loads((output/'calls'/failed['id']/'failure.json').read_text())
    if (failed['id']!=previous['failed_preflight_id'] or failure.get('uncertain') is not False or
            'library_mcp_opencode_stage_duplicate_or_not_permitted' not in failure['error'] or
            any(json.loads(line).get('job_id')==failed['id'] and json.loads(line).get('type')=='request'
                for line in (output/'mcp_http.jsonl').read_text().splitlines() if line.strip())):
        raise LibraryStopped('main_plan_binding:preflight_outcome_not_known')
    call=old['calls'][11]
    if call['id']!=proof['source_call_id'] or call['name']!=previous['correction_alias']:
        raise LibraryStopped('main_plan_binding:wrong_received_source')
    folder=output/'calls'/call['id'];request=json.loads((folder/'request.json').read_text());reply=json.loads((folder/'response.json').read_text())
    if json.loads((folder/'protocol_failure.json').read_text())['error']!='plan:cast_entity_not_supported_by_observation':
        raise LibraryStopped('main_plan_binding:known_cast_failure_required')
    from .state import json_sha
    if (json_sha(request)!=call['request_sha256'] or json_sha(reply)!=call['response_sha256'] or
            request!=previous['expected_request']):
        raise LibraryStopped('main_plan_binding:received_source_changed')
    source=unparsed_received_plan(folder,reply)
    windows=pipeline._received_windows(SimpleNamespace(output=output,data=now),visual_only=True,
        normalize=pipeline.contracts.creative_time_normalization(now.get('input_lock', {}).get('configuration', {})))
    authorized=dict(operation='split_one_unsupported_cast_binding',cast_id='cast_hero',new_cast_id='cast_hero_musician',
        binding=dict(window_id='window_99132eec2140becc',role_id='A'),
        segment_role_removals=[dict(segment_id='seg_15a',role_id='A',reason='反派惊恐反应，衔接后续主角的能量爆发')])
    if proof['main_patch']!=authorized:raise LibraryStopped('main_plan_binding:unapproved_author_choice')
    expected=apply_binding_split(source,windows,proof['main_patch'])
    authored=json.loads(Path(proof['plan_path']).read_text())
    if authored!=expected:raise LibraryStopped('main_plan_binding:unapproved_plan_edit')
    return proof,authored


def load(state,stage,validator):
    claim=state.output/'caption_reflow_repair_v1.claim.json'
    if not claim.exists():claim=state.output/'plan_binding_repair_v1.claim.json'
    if not claim.exists():return None
    receipt=json.loads(claim.read_text());path=Path(receipt['proof_path'])
    if sha256_file(path)!=receipt['proof_sha256']:raise LibraryStopped('main_plan_binding:claim_changed')
    proof=json.loads(path.read_text())
    if proof['plan_name']!=stage:return None
    proof,plan=validate(state.output,path,claimed=True)
    validator(plan)
    state.set_artifact('main_authored_plan_binding',dict(author=proof['author'],proof_path=str(path),
        proof_sha256=receipt['proof_sha256'],source_call_id=proof['source_call_id'],plan_path=proof['plan_path'],
        plan_sha256=sha256_file(proof['plan_path']),model_received_claim=False,edit=proof['main_patch']))
    return plan
