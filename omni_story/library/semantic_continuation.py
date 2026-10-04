"""One human-authorized, append-only semantic end-to-end continuation."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ..contract import require, text, rows, number
from . import contracts, semantic_audit as audit, semantic_pipeline, semantic_prompts
from .editing import validate_candidate_dispositions, validate_method_review, compact_timeline
from .media import prepare_window, sha256_file, inventory_sources
from .pipeline import CodexMCP, _catalog, _read, _status, _window_context
from .render import compile_library_plan, render_library_video, validate_caption_layout
from .shot_timeline import detect_shot_timeline, associate_edl_boundaries
from .state import LibraryState, LibraryStopped, json_sha, file_lock, write_json

POLICY='one_authorized_semantic_continuation_v1'
AUTHORIZATION='semantic_continuation_authorization'


def _state(output):
    saved=_read(Path(output)/'library_state.json')
    return LibraryState(output,saved['input_lock'],max_requests=saved['max_requests'])


def _authorization(state):
    entries=state.data['artifacts'].get(AUTHORIZATION,[])
    if not entries:
        raise LibraryStopped('semantic_continuation_requires_explicit_user_authorization')
    require(len(entries)==1,'semantic_continuation:one_authorization_only')
    value=_read(entries[0]['path'])
    require(json_sha(value)==entries[0]['sha256'],'semantic_continuation:authorization_modified')
    require(value['policy']==POLICY and value['task_id']==state.data['task_id'],
            'semantic_continuation:task_or_policy_changed')
    require(value['input_lock_sha256']==json_sha(state.data['input_lock']) and value['max_requests']==state.max_requests,
            'semantic_continuation:input_or_budget_changed')
    require(value['render_index']==3 and value['authorized_additional_renders']==1 and value['effective_render_limit']==4,
            'semantic_continuation:invalid_render_extension')
    baseline=value['baseline_request_count']
    require(baseline==len(value['original_calls']) and state.data['request_count']==len(state.data['calls'])
            and state.data['calls'][:baseline]==value['original_calls'], 'semantic_continuation:call_ledger_changed')
    require(type(value['max_segments']) is int and value['max_segments']>=1
            and value['reserved_max_requests']==2*(5+value['max_segments'])
            and value['reserved_max_requests']<=value['remaining_at_authorization']
            and baseline+value['remaining_at_authorization']==state.max_requests,
            'semantic_continuation:invalid_request_reservation')
    for row in value['protected_files']:
        require(Path(row['path']).is_file() and sha256_file(row['path'])==row['sha256'],
                'semantic_continuation:historical_file_changed:'+row['path'])
    for call in value['original_calls']:
        require(next((c for c in state.data['calls'] if c['id']==call['id']),None)==call,
                'semantic_continuation:historical_call_changed:'+call['id'])
    require(not any(p.name[7:].isdigit() and int(p.name[7:])>3 for p in state.output.glob('render_*') if p.is_dir()),
            'semantic_continuation:unapproved_extra_render')
    return value


def authorized_render_indices(state):
    if not state.data['artifacts'].get(AUTHORIZATION):
        return set()
    return {_authorization(state)['render_index']}


def authorize_semantic_continuation(output, authorization_text):
    text(authorization_text,'semantic_continuation/user_authorization')
    with file_lock(Path(output).resolve(strict=True)/'.semantic_continuation.lock'):
        state=_state(output)
        if state.data['artifacts'].get(AUTHORIZATION):
            return _authorization(state)
        from .revision import _authorization as previous_authorization
        previous_authorization(state)
        require((state.output/'result_revision_2.json').is_file(),'semantic_continuation:previous_revision_required')
        require(not (state.output/'render_3').exists(),'semantic_continuation:render_3_without_authorization')
        require(not any(c['status']=='submitted' for c in state.data['calls']), 'semantic_continuation:pending_call')
        remaining=state.max_requests-state.usage()['requests']
        maximum=min(32,(remaining-10)//2)
        require(maximum>=1,'semantic_continuation:insufficient_budget_for_complete_run')
        protected=set()
        for directory in ('calls','render_0','render_1','render_2','semantic_audit'):
            protected.update(p for p in (state.output/directory).rglob('*') if p.is_file())
        protected.update(p for p in state.output.glob('*.json') if p.name not in
            {'library_state.json','current_status.json','mcp_ready.json','mcp_current.json','mcp_tools.json'})
        protected.update(state.output.glob('*catalog/inventory.json'))
        protected.update(state.output.glob('media_cache/*/lineage.json'))
        protected.update(p for p in (state.output/'artifacts/editing_revision_v1').rglob('*') if p.is_file())
        value={'policy':POLICY,'task_id':state.data['task_id'],'user_authorization':authorization_text,
            'input_lock_sha256':json_sha(state.data['input_lock']),'max_requests':state.max_requests,
            'baseline_request_count':state.data['request_count'],'remaining_at_authorization':remaining,
            'reserved_max_requests':2*(maximum+5),'max_segments':maximum,
            'render_index':3,'authorized_additional_renders':1,'effective_render_limit':4,
            'no_new_unique_fine_windows':True,'fine_window_limit':state.data['input_lock']['configuration']['max_fine'],
            'reference_observation':'Full fixed reference; no historical interpretation, prescribed seconds or editing answers.',
            'comparison':'All independent exact-source observations finish before batched claim comparison.',
            'original_calls':deepcopy(state.data['calls']),
            'protected_files':[{'path':str(p.resolve()),'sha256':sha256_file(p)} for p in sorted(protected)]}
        state.set_artifact(AUTHORIZATION,value)
        return _authorization(state)


def _validate_full_reference(value, reference_sha, duration):
    contracts.validate_reference(value.get('reference'),reference_sha,duration)
    contracts.validate_editing_reference(value.get('editing_reference'),reference_sha,duration,value['reference'])
    text(value.get('observation_strategy'),'semantic_continuation/observation_strategy')
    cursor=0
    for part in rows(value.get('coverage'),'semantic_continuation/coverage'):
        start=number(part.get('start_s'),'semantic_continuation/coverage/start')
        end=number(part.get('end_s'),'semantic_continuation/coverage/end')
        require(abs(start-cursor)<=.001 and start<end<=duration+.001,'semantic_continuation:reference_coverage_gap')
        text(part.get('observed_content'),'semantic_continuation/coverage/content')
        cursor=end
    require(abs(cursor-duration)<=.001,'semantic_continuation:reference_tail_not_covered')


def reconcile_partial_reference(output):
    """Salvage valid navigation subobjects, never a failed full-contract pass.

    Only a received original plus its one received repair are eligible. No
    request, reply, protocol failure or old parsed cache is changed. The original
    coverage rows remain model statements, not measured watched/unwatched spans.
    """
    state=_state(output)
    _authorization(state)
    records=state.data['artifacts'].get('partial_reference_reconciliation',[])
    if records:
        value=_read(records[-1]['path'])
        require(json_sha(value)==records[-1]['sha256'],'semantic_continuation:reconciliation_modified')
        return value
    original=next(c for c in state.data['calls'] if c['name']=='continuation_3_reference')
    repair=next(c for c in state.data['calls'] if c.get('repair_of')==original['id'])
    require(original['status']==repair['status']=='received','semantic_continuation:known_replies_required')
    folder=state.output/'calls'/repair['id']
    request,reply=_read(folder/'request.json'),_read(folder/'response.json')
    require(json_sha(request)==repair['request_sha256'] and json_sha(reply)==repair['response_sha256'],
            'semantic_continuation:recorded_reference_changed')
    failure=_read(folder/'protocol_failure.json')
    require(failure['error'].startswith(('semantic_continuation:reference_coverage_gap',
                                        'semantic_continuation:reference_tail_not_covered')),
            'semantic_continuation:only_reported_coverage_failure_can_be_reconciled')
    require(not (folder/'parsed.json').exists(),'semantic_continuation:failed_reply_cannot_be_full_pass')
    value=contracts.parse_model_json('\n'.join(c['text'] for c in reply['result']['content'] if c.get('type')=='text'))
    sha=state.data['input_lock']['reference_sha256']
    duration=_read(state.output/'reference_catalog/inventory.json')['sources'][0]['duration_s']
    contracts.validate_reference(value['reference'],sha,duration)
    contracts.validate_editing_reference(value['editing_reference'],sha,duration,value['reference'])
    text(value.get('observation_strategy'),'semantic_continuation/observation_strategy')
    gaps=[]
    cursor=0
    for part in rows(value.get('coverage'),'semantic_continuation/coverage'):
        start=number(part.get('start_s'),'semantic_continuation/coverage/start')
        end=number(part.get('end_s'),'semantic_continuation/coverage/end')
        require(0<=start<end<=duration+.001 and start>=cursor-.001,
                'semantic_continuation:reported_coverage_outside_or_overlapping')
        text(part.get('observed_content'),'semantic_continuation/coverage/content')
        if start>cursor+.001:
            gaps.append([cursor,start])
        cursor=end
    if cursor<duration-.001:
        gaps.append([cursor,duration])
    record={'policy':'partial_reference_navigation_reconciliation_v1','source_call_id':repair['id'],
        'request_sha256':repair['request_sha256'],'response_sha256':repair['response_sha256'],
        'media_sha256':request['media_sha256'],'reference_sha256':sha,
        'full_response':value,'reported_coverage_gaps':gaps,
        'status':'core_contract_valid_coverage_report_partial_model_estimate',
        'evidence_limit':'Complete reference media was supplied. The model coverage report is noncontiguous; '
            'it proves neither actual unwatched intervals nor complete editing-method recognition. '
            'Original full-contract failure remains. Reference descriptions remain unverified model estimates.',
        'no_model_replay_or_human_content_repair':True}
    state.set_artifact('partial_reference_reconciliation',record)
    return record


def _reconciled_reference(state, reference_media):
    saved=state.data['artifacts'].get('partial_reference_reconciliation',[])
    if not saved:
        return None
    value=_read(saved[-1]['path'])
    require(json_sha(value)==saved[-1]['sha256'],'semantic_continuation:reconciliation_modified')
    call=next(c for c in state.data['calls'] if c['id']==value['source_call_id'])
    folder=state.output/'calls'/call['id']
    request,reply=_read(folder/'request.json'),_read(folder/'response.json')
    require(call['status']=='received' and json_sha(request)==value['request_sha256']==call['request_sha256']
            and json_sha(reply)==value['response_sha256']==call['response_sha256']
            and sha256_file(reference_media)==value['media_sha256']==request['media_sha256'],
            'semantic_continuation:reconciled_reference_binding_changed')
    actual=contracts.parse_model_json('\n'.join(c['text'] for c in reply['result']['content'] if c.get('type')=='text'))
    require(actual==value['full_response'],'semantic_continuation:reconciled_model_content_changed')
    return value


def execute_semantic_continuation(reference,library,output):
    state=_state(output)
    _authorization(state)
    with file_lock(state.output/'.semantic_continuation.lock'):
        try:
            return _execute(reference,library,state)
        except Exception as error:
            failure={'error':str(error),'type':type(error).__name__,'usage':state.usage(),
                     'no_automatic_paid_replay':True,'historical_records_preserved':True}
            state.set_artifact('semantic_continuation_failure',failure)
            _status(state.output,'semantic_continuation_stopped',**failure)
            raise


def _plan_diagnostics(plan, windows):
    """Measured contract metadata only; never choose a role, event or cut."""
    by_window={w['window_id']:w for w in windows}
    diagnostics=[]
    for segment in plan.get('segments',[]):
        window=by_window.get(segment.get('window_id'))
        if not window:
            continue
        local_in=segment['source_in_s']-window['source_start_s']
        local_out=segment['source_out_s']-window['source_start_s']
        matching=[u for u in window['observation']['usable_ranges']
                  if u['local_in_s']-.001<=local_in and local_out<=u['local_out_s']+.001
                  and set(segment['role_ids'])<=set(u['role_ids'])]
        if not matching:
            diagnostics.append({'segment_id':segment['segment_id'],
                'error':'plan:range_not_supported_by_fine_observation',
                'selected_local_range':[local_in,local_out],
                'asserted_roles':segment['role_ids'],
                'recorded_usable_ranges':window['observation']['usable_ranges']})
        caption=segment.get('caption')
        if caption:
            for binding in caption.get('evidence',[]):
                for index in binding.get('event_indices',[]):
                    if type(index) is not int or not 0<=index<len(window['observation']['events']):
                        continue
                    event=window['observation']['events'][index]
                    if not(event['local_start_s']<local_out and local_in<event['local_end_s']):
                        diagnostics.append({'segment_id':segment['segment_id'],
                            'error':'plan:caption_event_outside_selected_range','event_index':index,
                            'selected_local_range':[local_in,local_out],
                            'cited_event_local_range':[event['local_start_s'],event['local_end_s']]})
    return diagnostics


def _semantic_replan(glm,state,context,media,windows,validator,policy):
    """One substantive model revision after a known evidence-contract failure.

    This is not another format repair. It consumes a separately reserved stage,
    narrows the slice cap, and keeps both failed plan replies unchanged.
    """
    originals=[c for c in state.data['calls'] if c['name']=='continuation_3_plan' and not c.get('repair_of')]
    require(len(originals)==1,'semantic_continuation:one_failed_original_plan_required')
    original=originals[0]
    repairs=[c for c in state.data['calls'] if c.get('repair_of')==original['id']]
    require(len(repairs)==1 and original['status']==repairs[0]['status']=='received',
            'semantic_continuation:known_plan_and_repair_required')
    allowed={'plan:range_not_supported_by_fine_observation','plan:caption_event_outside_selected_range',
             'semantic:caption_evidence_not_visible_during_display'}
    bindings=[]
    for call in (original,repairs[0]):
        folder=state.output/'calls'/call['id']
        request,reply=_read(folder/'request.json'),_read(folder/'response.json')
        require(json_sha(request)==call['request_sha256'] and json_sha(reply)==call['response_sha256'],
                'semantic_continuation:failed_plan_records_changed')
        require(not (folder/'parsed.json').exists(),'semantic_continuation:successful_plan_cannot_be_replanned')
        failure=_read(folder/'protocol_failure.json')
        require(failure['error'] in allowed,'semantic_continuation:plan_failure_not_semantic_replan_eligible')
        bindings.append({'call_id':call['id'],'request_sha256':call['request_sha256'],
            'response_sha256':call['response_sha256'],'failure_sha256':json_sha(failure)})
    previous=contracts.parse_model_json('\n'.join(c['text'] for c in reply['result']['content'] if c.get('type')=='text'))
    saved=state.data['artifacts'].get('semantic_replan_budget',[])
    if saved:
        require(len(saved)==1,'semantic_continuation:one_replan_allocation_only')
        entry=saved[-1]
        allocation=_read(entry['path'])
        require(json_sha(allocation)==entry['sha256'] and allocation['failed_call_bindings']==bindings,
                'semantic_continuation:replan_budget_or_bindings_changed')
    else:
        require(not any(c['name'].startswith('continuation_3_replan') for c in state.data['calls']),
                'semantic_continuation:paid_replan_requires_original_allocation')
        remaining=state.max_requests-state.data['request_count']
        maximum=min(policy['max_segments'],(remaining-8)//2)
        require(maximum>=1,'semantic_continuation:insufficient_budget_for_semantic_replan')
        allocation={'policy':'one_evidence_feedback_replan_v1','failed_call_bindings':bindings,
            'baseline_request_count':state.data['request_count'],'remaining_at_allocation':remaining,
            'max_segments':maximum,'reserved_max_requests':2*maximum+8,
            'fixed_stages':['continuation_3_replan','batched_claim_check','silent_blind','output_review'],
            'original_failure_preserved':True,'diagnostics':_plan_diagnostics(previous,windows)}
        state.set_artifact('semantic_replan_budget',allocation)
    baseline=next(i+1 for i,c in enumerate(state.data['calls']) if c['id']==repairs[0]['id'])
    require(allocation['baseline_request_count']==baseline
            and allocation['max_segments']==min(policy['max_segments'],(state.max_requests-baseline-8)//2)
            and allocation['reserved_max_requests']==2*allocation['max_segments']+8
            and allocation['reserved_max_requests']<=allocation['remaining_at_allocation']
            and allocation['baseline_request_count']+allocation['remaining_at_allocation']==state.max_requests
            and 1<=allocation['max_segments']<=policy['max_segments'],
            'semantic_continuation:invalid_replan_reservation')
    revised=deepcopy(context)
    revised['render_capabilities']['max_segments']=allocation['max_segments']
    revised['failed_model_plan']=previous
    revised['contract_diagnostics']=allocation['diagnostics']
    prompt=semantic_prompts.plan_prompt(revised)+('\n前一创作计划及其一次协议修复均未通过证据契约。'
        '本次是一次独立的语义重规划，不是继续修JSON。请依据已有素材证据自主重选、删减或重组段落、'
        '片段、角色声明和字幕依据。诊断只说明约束冲突，不提供应选答案；不要仅抄回原计划。'
        '保留参考主旨；无证据时承认缺项，不增加不可观察的事件。此后不再追加语义重规划。')
    _status(state.output,'semantic_continuation_evidence_feedback_replan',
            max_segments=allocation['max_segments'],requests=state.usage()['requests'])
    return glm.call('continuation_3_replan',prompt,media,
                    lambda value:validator(value,allocation['max_segments']))


def reconcile_missing_observation_uncertainties(glm,name,segment,source,proxy):
    """Recover only an omitted report field, without inventing observation facts.

    The conservative added note is program metadata, not a model claim that no
    uncertainties exist. Raw replies/failures never become full protocol passes.
    """
    state=glm.state
    original=next((c for c in state.data['calls'] if c['name']==name and not c.get('repair_of')),None)
    if not original or original['status']!='received':
        return None
    repairs=[c for c in state.data['calls'] if c.get('repair_of')==original['id']]
    if len(repairs)!=1 or repairs[0]['status']!='received':
        return None
    repair=repairs[0]
    folder=state.output/'calls'/repair['id']
    failure=_read(folder/'protocol_failure.json') if (folder/'protocol_failure.json').exists() else None
    if not failure or failure['error']!='semantic/uncertainties:list_required':
        return None
    require(not (folder/'parsed.json').exists(),'semantic_continuation:failed_observation_cannot_be_full_pass')
    bindings=[]
    for call in (original,repair):
        path=state.output/'calls'/call['id']
        request,reply=_read(path/'request.json'),_read(path/'response.json')
        require(json_sha(request)==call['request_sha256'] and json_sha(reply)==call['response_sha256']
                and request['media_sha256']==proxy['sha256'],
                'semantic_continuation:observation_reply_or_media_changed')
        bindings.append({'call_id':call['id'],'request_sha256':call['request_sha256'],
            'response_sha256':call['response_sha256'],
            'failure_sha256':json_sha(_read(path/'protocol_failure.json'))})
    raw=contracts.parse_model_json('\n'.join(c['text'] for c in reply['result']['content'] if c.get('type')=='text'))
    require('uncertainties' not in raw,'semantic_continuation:only_omitted_uncertainty_field_eligible')
    normalized=deepcopy(raw)
    note=('Program protocol limitation: the model omitted its uncertainties field. '
          'The absence of further uncertainty is unknown. Any extra untyped inference is not validated evidence.')
    normalized['uncertainties']=[note]
    audit.validate_segment_observation(normalized,segment,source['sha256'],proxy)
    for entry in state.data['artifacts'].get('source_observation_reconciliation',[]):
        saved=_read(entry['path'])
        require(json_sha(saved)==entry['sha256'],'semantic_continuation:observation_reconciliation_modified')
        if saved['source_call_id']==repair['id']:
            require(saved['call_bindings']==bindings and saved['raw_model_object']==raw
                    and saved['observation']==normalized,
                    'semantic_continuation:reconciled_observation_facts_changed')
            return normalized
    state.set_artifact('source_observation_reconciliation',{
        'policy':'omitted_uncertainties_report_reconciliation_v1','source_call_id':repair['id'],
        'call_bindings':bindings,'raw_model_object':raw,'observation':normalized,
        'program_added_field':'uncertainties','protocol_limit':note,
        'original_failure_preserved':True,'facts_times_identities_unchanged':True,
        'additional_model_requests':0})
    return normalized


def _normalize_batch_report(raw):
    """Bounded punctuation repair and sourced reason copies; no verdict editing."""
    escaped=raw
    repairs=[]
    for _ in range(18):
        try:
            value=json.loads(escaped)
            break
        except json.JSONDecodeError as error:
            if (error.msg=="Expecting ',' delimiter" and error.pos==len(escaped)
                    and escaped.lstrip().startswith('{') and escaped.rstrip().endswith(']')
                    and not any(r.get('operation')=='close_root_object' for r in repairs)):
                repairs.append({'position_in_previous_text':len(escaped),'inserted':'}',
                                'operation':'close_root_object'})
                escaped+='}'
                continue
            position=error.pos-1
            while position>=0 and escaped[position] in ' \t\r\n':
                position-=1
            require(sum(r['inserted']=='\\' for r in repairs)<16 and error.msg=="Expecting ',' delimiter"
                    and 0<error.pos<len(escaped) and position>=0 and escaped[position]=='"'
                    and escaped[error.pos] not in '\\"{}[]:,0123456789',
                    'semantic_continuation:batch_syntax_not_quote_only_recoverable')
            escaped=escaped[:position]+'\\'+escaped[position:]
            repairs.append({'position_in_previous_text':position,'inserted':'\\'})
    else:
        raise ValueError('semantic_continuation:batch_quote_repair_limit')
    require(isinstance(value,dict),'semantic_continuation:batch_object_required')
    copies=[]
    for checked in rows(value.get('segment_checks'),'semantic_continuation/batch/segment_checks'):
        for claim in rows(checked.get('claim_checks'),'semantic_continuation/batch/claim_checks'):
            if claim.get('status') in {'partial','unsupported','unverifiable'} and claim.get('limitations')==[]:
                reason=claim.get('reason')
                text(reason,'semantic_continuation/batch/reason')
                claim['limitations']=[reason]
                copies.append({'segment_id':checked['segment_id'],'claim_id':claim['claim_id'],'reason':reason})
    return value,repairs,copies


def reconcile_batch_report(glm,name,records,validator):
    """Use a known complete report after its empty, exhausted format repair."""
    state=glm.state
    original=next((c for c in state.data['calls'] if c['name']==name and not c.get('repair_of')),None)
    if not original or original['status']!='received':
        return None
    repairs=[c for c in state.data['calls'] if c.get('repair_of')==original['id']]
    if len(repairs)!=1 or repairs[0]['status']!='received':
        return None
    bindings=[]
    for call in (original,repairs[0]):
        folder=state.output/'calls'/call['id']
        if (folder/'parsed.json').exists() or not (folder/'protocol_failure.json').exists():
            return None
        request,reply,failure=_read(folder/'request.json'),_read(folder/'response.json'),_read(folder/'protocol_failure.json')
        require(json_sha(request)==call['request_sha256'] and json_sha(reply)==call['response_sha256']
                and request['media_sha256']==sha256_file(records[0]['proxy_path']),
                'semantic_continuation:batch_reply_or_media_changed')
        bindings.append({'call_id':call['id'],'request_sha256':call['request_sha256'],
            'response_sha256':call['response_sha256'],'failure_sha256':json_sha(failure)})
        content='\n'.join(c['text'] for c in reply['result']['content'] if c.get('type')=='text')
        if call is original:
            if not failure['error'].startswith("Expecting ',' delimiter:"):
                return None
            raw=content
        elif content.strip() or not failure['error'].startswith('Expecting value:'):
            return None
    value,quotes,copies=_normalize_batch_report(raw)
    validator(value)
    record={'policy':'punctuation_and_sourced_limitations_reconciliation_v1','source_call_id':original['id'],
        'call_bindings':bindings,'raw_model_text':raw,'quote_repairs':quotes,'reason_copies':copies,
        'batch_report':value,'original_failure_preserved':True,'additional_model_requests':0,
        'statuses_claim_ids_evidence_ids_unchanged':True,
        'protocol_limit':'Known batch report normalized by bounded punctuation repair and same-claim model reason copies. '
            'Original JSON failure and empty token-exhausted repair remain; this is not a full model protocol pass.'}
    for entry in state.data['artifacts'].get('batch_report_reconciliation',[]):
        saved=_read(entry['path'])
        require(json_sha(saved)==entry['sha256'],'semantic_continuation:batch_reconciliation_modified')
        if saved['source_call_id']==original['id']:
            require(saved==record,'semantic_continuation:reconciled_batch_report_changed')
            return value
    state.set_artifact('batch_report_reconciliation',record)
    return value


def _review_protocol_failure(state,name,error):
    """A known failed review may not conceal an already valid actual render."""
    require(str(error)=='model_protocol_repair_exhausted:'+name,
            'semantic_continuation:only_exhausted_review_can_deliver_incomplete_candidate')
    original=next(c for c in state.data['calls'] if c['name']==name and not c.get('repair_of'))
    repairs=[c for c in state.data['calls'] if c.get('repair_of')==original['id']]
    require(len(repairs)==1 and original['status']==repairs[0]['status']=='received',
            'semantic_continuation:unknown_or_pending_review_cannot_be_completed')
    calls=[]
    for call in (original,repairs[0]):
        folder=state.output/'calls'/call['id']
        request,reply,failure=_read(folder/'request.json'),_read(folder/'response.json'),_read(folder/'protocol_failure.json')
        require(json_sha(request)==call['request_sha256'] and json_sha(reply)==call['response_sha256']
                and not (folder/'parsed.json').exists(),
                'semantic_continuation:review_failure_records_changed')
        calls.append({'call_id':call['id'],'request_sha256':call['request_sha256'],
            'response_sha256':call['response_sha256'],'failure_sha256':json_sha(failure),'error':failure['error']})
    record={'policy':'deliver_actual_candidate_with_incomplete_review_v1','calls':calls,
        'review_status':'incomplete_protocol_failure','error':str(error),
        'model_review':None,'semantic_gate_passed':False,'additional_model_requests':0,
        'limit':'Final review did not return a valid protocol after its one repair. '
            'No model verdict or successful semantic evaluation has been fabricated.'}
    saved=state.data['artifacts'].get('incomplete_output_review',[])
    if saved:
        entry=saved[-1]
        require(len(saved)==1 and json_sha(_read(entry['path']))==entry['sha256']
                and _read(entry['path'])==record,'semantic_continuation:incomplete_review_record_changed')
    else:
        state.set_artifact('incomplete_output_review',record)
    return record


def _execute(reference,library,state):
    policy=_authorization(state)
    output=state.output
    folder=output/'artifacts/semantic_continuation_v1'
    folder.mkdir(parents=True,exist_ok=True)
    catalog=_catalog(library,output/'catalog')
    ref=_catalog(reference,output/'reference_catalog')['sources'][0]
    require(ref['sha256']==state.data['input_lock']['reference_sha256'],'semantic_continuation:reference_changed')
    require([{'source_id':s['source_id'],'sha256':s['sha256']} for s in catalog['sources']]
        ==state.data['input_lock']['library_sources'],'semantic_continuation:library_changed')
    windows=deepcopy(_read(output/'watched_windows.json'))
    require(len(windows)<=policy['fine_window_limit'] and all(w['status']=='watched' for w in windows),
            'semantic_continuation:invalid_watched_windows')
    for window in windows:
        require(sha256_file(window['path'])==window['sha256'],'semantic_continuation:watched_proxy_changed')
        lineage=_read(Path(window['path']).parent/'lineage.json')
        require(all(lineage[k]==window[k] for k in ('source_id','source_sha256','source_start_s','source_end_s')),
                'semantic_continuation:watched_proxy_lineage_changed')
        require(any(c['status']=='received' and c['name'].startswith('fine_')
                    and (output/'calls'/c['id']/'parsed.json').is_file()
                    and _read(output/'calls'/c['id']/'parsed.json')==window['observation'] for c in policy['original_calls']),
                'semantic_continuation:watched_observation_not_completed')
    reference_media=Path(reference).resolve()
    if reference_media.stat().st_size>=8_000_000:
        reference_media=Path(prepare_window(ref,0,ref['duration_s'],output/'media_cache',fps=30)['path'])
    glm=CodexMCP(state)
    state.enable_independent_continuation()
    _status(output,'semantic_continuation_full_reference',requests=state.usage()['requests'])
    reconciled=_reconciled_reference(state,reference_media)
    if reconciled:
        full=reconciled['full_response']
    else:
        full=glm.call('continuation_3_reference',semantic_prompts.autonomous_reference_prompt(ref['sha256'],ref['duration_s']),
            reference_media,lambda v:_validate_full_reference(v,ref['sha256'],ref['duration_s']))
    write_json(folder/'reference_analysis.json',full)
    reading,methods=full['reference'],full['editing_reference']
    context={'reference':reading,'editing_reference':methods,'reference_duration_s':ref['duration_s'],
        'reference_audio_stream_index':ref['audio_stream_index'],
        'catalog':{'sources':[{k:s[k] for k in ('source_id','sha256','duration_s','audio_stream_index')}
                    | {'filename':Path(s['path']).name} for s in catalog['sources']]},
        'watched_windows':[_window_context(w) for w in windows],
        'role_identity_scope':'局部角色ID仅在各窗口内有效；跨窗口焦点人物身份必须由实际外形依据绑定。',
        'audio_semantics_policy':'用户已确认参考只有BGM。内容依据静音画面；视觉MCP没有实际音频审听，音乐节奏保持未知。',
        'framing_behavior':{'fit':'Preserve full source frame with padding.','crop':'Center crop; no tracking.'},
        'render_capabilities':{'speed':[.5,2],'max_duration_s':180,'max_segments':policy['max_segments'],
            'freeze_tail_s':[0,10],'static_caption':True,'audio_modes':['reference','source','mix','silent'],
            'unsupported':['J/L_cut','audio_source_separation','synthetic_video']},
        'instruction':'按你对完整参考的新观察自主确定创作目标、角色路线、slots与精切。历史窗口描述是检索导航，'
            '不是入选精切的已确认真值。不得依据电影常识填不存在的动作。只用完成观察的usable_ranges；'
            '片段数是全部事实核查预算上限，不是要求用满。不要依赖新增文字替代缺失行动或结果。'}
    if reconciled:
        context['reference_observation_limit']=reconciled['evidence_limit']
    def validate_plan(value,maximum=policy['max_segments']):
        contracts.validate_plan(value,catalog,windows,ref['sha256'],ref['duration_s'],
            reference_audio_stream_index=ref['audio_stream_index'],editing_reference=methods)
        validate_candidate_dispositions(value,windows)
        compile_library_plan(catalog,value,fps=value['fps'],width=value['width'],height=value['height'])
        validate_caption_layout(value,value['width'],value['height'])
        semantic_pipeline.validate_plan_claims(value,windows,maximum)
    _status(output,'semantic_continuation_autonomous_planning',requests=state.usage()['requests'])
    if state.data['artifacts'].get('semantic_replan_budget'):
        plan=_semantic_replan(glm,state,context,reference_media,windows,validate_plan,policy)
    else:
        try:
            plan=glm.call('continuation_3_plan',semantic_prompts.plan_prompt(context),reference_media,validate_plan)
        except ValueError as error:
            if str(error)!='model_protocol_repair_exhausted:continuation_3_plan':
                raise
            plan=_semantic_replan(glm,state,context,reference_media,windows,validate_plan,policy)
    write_json(folder/'plan.json',plan)
    source_map={s['source_id']:s for s in catalog['sources']}
    _status(output,'semantic_continuation_exact_slice_facts',segments=len(plan['segments']),requests=state.usage()['requests'])
    checked=semantic_pipeline.observe_selected_slices(glm,plan,source_map,windows,output/'media_cache',output,3,
        batched_comparison=True,observation_reconciler=reconcile_missing_observation_uncertainties,
        batch_reconciler=reconcile_batch_report)
    _authorization(state)
    _status(output,'semantic_continuation_rendering',segments=len(plan['segments']),requests=state.usage()['requests'])
    rendered=render_library_video(catalog,plan,output/'render_3',reference_path=reference,
                                  fps=plan['fps'],width=plan['width'],height=plan['height'])
    actual=inventory_sources(rendered['rendered_path'],output/'render_catalog_3')['sources'][0]
    output_media=prepare_window(actual,0,rendered['measured_duration_s'],output/'media_cache',fps=30)
    timeline=detect_shot_timeline(actual,output/'media_cache/shot_timelines',threshold=3)
    evidence=associate_edl_boundaries(timeline,rendered)
    write_json(folder/'editing_evidence.json',evidence)
    _status(output,'semantic_continuation_silent_blind',requests=state.usage()['requests'])
    blind=glm.call('continuation_3_blind',semantic_prompts.blind_prompt(rendered['measured_duration_s'],rendered['sha256']),
        output_media['path'],lambda v:audit.validate_visual_blind(v,rendered['measured_duration_s'],rendered['sha256']))
    write_json(folder/'blind_reading.json',blind)
    review_context={'reference':reading,'editing_reference':methods,'protocol':audit.SEMANTIC_PROTOCOL,
        'video_sha256':rendered['sha256'],'actual_render_sha256':rendered['sha256'],
        'output_duration_s':rendered['measured_duration_s'],'blind_reading':blind,
        'source_observations':checked['observations'],'segment_checks':checked['segment_checks'],
        'required_claims':checked['required_claims'],'provenance':rendered['provenance'],
        'measured_output_timeline':compact_timeline(timeline),'edl_boundary_associations':evidence,
        'audio_review_limit':'Visual-only MCP: actual audio and music rhythm unverified.'}
    if reconciled:
        review_context['reference_observation_limit']=reconciled['evidence_limit']
    def validate_review(value):
        contracts.validate_review(value,ref['sha256'])
        validate_method_review(value,methods,rendered['measured_duration_s'])
        audit.validate_semantic_review(value,blind,checked['observations'],checked['required_claims'],
            rendered['measured_duration_s'],rendered['sha256'],segment_checks=checked['segment_checks'])
    _status(output,'semantic_continuation_output_review',requests=state.usage()['requests'])
    review_failure=None
    try:
        review=glm.call('continuation_3_review',semantic_prompts.review_prompt(review_context),output_media['path'],validate_review)
    except ValueError as error:
        review_failure=_review_protocol_failure(state,'continuation_3_review',error)
        review=None
    if review is not None:
        write_json(folder/'review.json',review)
    success=bool(review is not None and audit.semantic_review_passes(review,blind,checked['segment_checks'],
                        expected_segment_ids=[s['segment_id'] for s in plan['segments']]))
    observation_limits=[_read(e['path'])['protocol_limit']
                        for e in state.data['artifacts'].get('source_observation_reconciliation',[])]
    batch_limits=[_read(e['path'])['protocol_limit']
                  for e in state.data['artifacts'].get('batch_report_reconciliation',[])]
    result={'status':'model_checked_library_candidate' if success and not reconciled and not observation_limits and not batch_limits
                    else 'library_candidate_with_limitations',
        'final_video':rendered['rendered_path'],'final_sha256':rendered['sha256'], 'selected_round':3,
        'reference_sha256':ref['sha256'],'review':review,'semantic_gate_passed':success,
        'review_status':'completed_protocol' if review is not None else 'incomplete_protocol_failure',
        'review_failure':review_failure,'blind_reading_path':str(folder/'blind_reading.json'),
        'semantic_protocol':audit.SEMANTIC_PROTOCOL,'continuation_policy':POLICY,
        'semantic_evidence_path':str(output/'semantic_audit/round_3/manifest.json'),
        'reference_analysis_path':str(folder/'reference_analysis.json'),'usage':state.usage(),
        'actual_fine_windows':len(windows),'new_unique_fine_windows':0,'effective_render_limit':4,
        'human_creative_inputs':[],'source_generation_requests':0,
        'source_observation_protocol_limits':observation_limits,
        'batch_report_protocol_limits':batch_limits,
        'evidence_limit':'Model review is not human truth; current candidate is not a fresh comparison with historical outputs.'}
    if reconciled:
        result['reference_analysis_status']=reconciled['status']
        result['reference_analysis_limit']=reconciled['evidence_limit']
    _authorization(state)
    write_json(output/'result_semantic_revision_3.json',result)
    _status(output,'semantic_continuation_completed',**result)
    return result
