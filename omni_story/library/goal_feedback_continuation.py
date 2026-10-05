"""One append-only feedback round for the user-authorized editing Goal.

The old reference and library observations remain model estimates. New craft
observations supplement them without converting them into canonical facts. This
module never grants permission, searches new library windows, or chooses cuts.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path
from types import SimpleNamespace

from ..contract import require
from . import active_finecut as finecut, contracts, semantic_audit as audit
from . import semantic_pipeline, semantic_prompts
from .editing import compact_timeline, validate_candidate_dispositions, validate_method_review
from .media import inventory_sources, prepare_window, sha256_file
from .pipeline import CodexMCP, _catalog, _read, _window_context
from .render import compile_library_plan, render_library_video, validate_caption_layout
from .shot_timeline import associate_edl_boundaries, detect_shot_timeline
from .state import LibraryStopped, file_lock, json_sha, write_json


POLICY = "goal_feedback_round_v1"
from .finecut_continuation import (_artifact, _status, _canonical_reference, _completed_windows,
                                  _craft_evidence, _known_protocol_failure)


def _handbook_view(state, policy):
    from .goal_budget import AUTHORIZATION
    return SimpleNamespace(data={'artifacts':{finecut.POLICY_ARTIFACT:state.data['artifacts'][AUTHORIZATION]}})


def _economy_prompt(manifest, blind):
    from .goal_output_contracts import ECONOMY_INSTRUCTION
    return finecut.economy_prompt(manifest, blind) + '\n' + ECONOMY_INSTRUCTION + '\n追加独立段内精剪审查。不要读取草案或解释，不要把整段有关就当整段必要。' \
        '逐段区分必要动作瞬间、必要理解停留、重复/等待/多余前后置、无法判断。给实际输出时间证据。' \
        '额外返回 microcut_checks，每个实际segment_id恰一项，字段 status=necessary/redundant/uncertain，' \
        'necessary_intervals=[{start_s,end_s,observed_fact}]，removable_intervals=[{start_s,end_s,observed_fact}]，reason。' \
        'necessary要求至少一条必要瞬间证据，不能仅复述整段有关；有可删过程则不得economy_status=pass。' \
        '镜头内无动作也可因必要信息揭示而保留；未知不伪造。所有时间只能来自实际输出。' \
        'pass时必要区间的并集应覆盖该段整个播放时域；只标一个高光，其余时间未审查，不足以通过。'


def _validate_economy(value, manifest):
    finecut.validate_economy_review(value, manifest)
    checks = value.get('microcut_checks')
    actual = {s['segment_id']:s for s in manifest['provenance']}
    require(isinstance(checks,list) and len(checks)==len(actual) and all(isinstance(s,dict) for s in checks)
            and {s.get('segment_id') for s in checks}==set(actual),
            'goal:microcut_checks_must_cover_all_segments')
    for check in checks:
        require(check.get('status') in {'necessary','redundant','uncertain'} and isinstance(check.get('reason'),str)
                and check['reason'].strip(), 'goal:microcut_status_reason')
        row = actual[check['segment_id']]
        for field in ('necessary_intervals','removable_intervals'):
            require(isinstance(check.get(field),list), 'goal:microcut_intervals_required')
            for evidence in check[field]:
                require(isinstance(evidence,dict),'goal:microcut_evidence_object_required')
                require(type(evidence.get('start_s')) in (int,float) and type(evidence.get('end_s')) in (int,float)
                        and row['output_in_s'] <= evidence['start_s'] < evidence['end_s'] <= row['output_out_s']+.001
                        and isinstance(evidence.get('observed_fact'),str) and evidence['observed_fact'].strip(),
                        'goal:microcut_evidence_outside_segment')
        require(check['status']!='necessary' or bool(check['necessary_intervals']), 'goal:necessary_instant_missing')
        if value['economy_status']=='pass':
            cursor=row['output_in_s']
            for interval in sorted(check['necessary_intervals'],key=lambda s:s['start_s']):
                require(interval['start_s']<=cursor+.034,'goal:unreviewed_internal_gap')
                cursor=max(cursor,interval['end_s'])
            require(cursor>=row['output_out_s']-.034,'goal:unreviewed_segment_tail')
    require(value['economy_status']!='pass' or all(c['status']=='necessary' and not c['removable_intervals'] for c in checks),
            'goal:pass_with_internal_redundancy')
    return value


def substantive_edl(catalog, plan):
    """Executable footage operations; creative labels do not constitute progress."""
    compiled=compile_library_plan(catalog,plan,fps=plan['fps'],width=plan['width'],height=plan['height'])
    segments=[]
    for row in compiled['segments']:
        caption = deepcopy(row.get('caption'))
        if caption:
            caption={k:caption[k] for k in ('text','position','font_size','start_frame','end_frame')}
        segments.append({'source_sha256':row['source_sha256'],
            'in':round(row['source_in_s'],6),'out':round(row['source_out_s'],6),
            'speed':round(row.get('speed',1),6),'freeze_tail_s':round(row.get('freeze_tail_s',0),6),
            'framing':row['framing'],'look':row['look'],'caption':caption})
    region=plan.get('reference_audio',{})
    audio={'mode':compiled['audio_mode']}
    if audio['mode'] in {'source','mix'}:
        audio.update(source_gain_db=compiled['source_gain_db'],
                     source_streams=[s['source_audio_stream_index'] for s in compiled['segments']])
    if audio['mode'] in {'reference','mix'}:
        audio.update(reference_gain_db=compiled['reference_gain_db'],
            reference={k:region.get(k,default) for k,default in
                       (('stream_index',None),('start_s',0),('end_s',None),('loop',False))})
    return {'segments':segments,'fps':plan['fps'],'width':plan['width'],'height':plan['height'],
            'audio':audio}


def _find_repeated_plan(state,catalog,plan,round_no):
    target = substantive_edl(catalog,plan)
    for call in state.data['calls']:
        if call['name'].startswith(f'active_{round_no}_') or call['status']!='received':
            continue
        path = state.output/'calls'/call['id']/'parsed.json'
        if not path.is_file():
            continue
        value = _read(path)
        candidate = value.get('plan',value) if isinstance(value,dict) else None
        if not isinstance(candidate,dict) or not all(k in candidate for k in ('segments','fps','width','height')):
            continue
        try:
            same = substantive_edl(catalog,candidate)==target
        except (ValueError,KeyError,TypeError):
            continue
        if same:
            return {'same_as_call':call['id'],'substantive_edl_sha256':json_sha(target),
                    'renamed_ids_or_reasons_are_not_progress':True}
    return None


def _feedback(state,round_no):
    """Only actual bound model replies/failures; absent renders have no review."""
    from .goal_budget import _call_value
    values=[]
    for call in state.data['calls']:
        name=call['name']
        relevant = name.startswith(f'active_{round_no-1}_') or name.startswith(f'semantic_slice_{round_no-1}_') \
            or name.startswith(f'semantic_claims_{round_no-1}_')
        if not relevant or call['status']!='received':
            continue
        folder=state.output/'calls'/call['id']
        record={'call_id':call['id'],'stage':name,'status':'known_reply','request_sha256':call['request_sha256'],
                'response_sha256':call['response_sha256'],'fallible_model_evidence':True}
        if (folder/'parsed.json').is_file():
            record['model_value']=_call_value(state.output,call)[1]
        elif (folder/'protocol_failure.json').is_file():
            record['protocol_failure']=_read(folder/'protocol_failure.json')
            record['no_valid_typed_fact_or_verdict']=True
        values.append(record)
    for name in state.data['artifacts']:
        if name.startswith(f'goal_cached_{round_no-1}_') or (round_no==5 and name.startswith('active_4_cached_evidence_')):
            values.append({'artifact':name,'bound_record':_artifact(state,name),'fallible_model_evidence':True})
        if name in {'active_finecut_continuation_failure',f'goal_failure_{round_no-1}'}:
            for entry in state.data['artifacts'][name]:
                value=_read(entry['path'])
                require(json_sha(value)==entry['sha256'],'goal:failure_record_changed')
                values.append({'artifact':name,'record_sha256':entry['sha256'],'actual_technical_failure':value,
                               'no_valid_movie_review_implied':True})
    render = state.output/f'render_{round_no-1}/render_result.json'
    return {'previous_round':round_no-1,'actual_render_exists':render.is_file(),
            'actual_render':_read(render) if render.is_file() else None,'records':values,
            'instruction':'Use source counterevidence to reconstruct this new draft; old failed slots are not obligations. '
                'Protocol failures are failures to establish facts, not alternative facts. No absent film review is invented.'}


def _exhausted_slice_scopes(state):
    scopes=[]
    for call in state.data['calls']:
        if call['status']!='received' or call.get('repair_of') or not call['name'].startswith('semantic_slice_'):
            continue
        repairs=[c for c in state.data['calls'] if c.get('repair_of')==call['id']]
        if len(repairs)!=1 or repairs[0]['status']!='received' or any(
                (state.output/'calls'/c['id']/'parsed.json').exists() for c in (call,repairs[0])):
            continue
        request=_read(state.output/'calls'/call['id']/'request.json')
        scope=request.get('observation_scope')
        if scope:
            scopes.append({'scope':scope,'original_call':call['id'],'repair_call':repairs[0]['id'],
                           'status':'known_protocol_exhausted_no_typed_facts'})
    return scopes


def _result(state,round_no):
    entries=state.data['artifacts'].get(f'goal_result_{round_no}',[])
    if not entries:
        return None
    record=_artifact(state,f'goal_result_{round_no}')
    result=_read(state.output/f'result_goal_feedback_{round_no}.json')
    require(json_sha(result)==record['result_sha256'],'goal:result_changed')
    for row in record['completed_files']:
        require(sha256_file(row['path'])==row['sha256'],'goal:completed_file_changed')
    return result


def _stop(state,round_no,status,details):
    result={'status':status,'selected_round':round_no,'new_renders':0,'model_goal_gate_passed':False,
        'actual_candidate':None,'details':details,'usage':state.usage(),'continuation_policy':POLICY,
        'no_automatic_paid_replay':True}
    path=state.output/f'result_goal_feedback_{round_no}.json'
    write_json(path,result)
    protected={path}
    protected.update(Path(e['path']) for e in state.data['artifacts'].get(f'goal_navigation_{round_no}', []))
    for directory in (state.output/f'artifacts/goal_feedback_round_{round_no}',state.output/f'semantic_audit/round_{round_no}'):
        protected.update(p for p in directory.rglob('*') if p.is_file())
    for call in state.data['calls']:
        if call['name'].startswith(f'active_{round_no}_') or call['name'].startswith(f'semantic_slice_{round_no}_') \
                or call['name'].startswith(f'semantic_claims_{round_no}_'):
            protected.update(p for p in (state.output/'calls'/call['id']).rglob('*') if p.is_file())
    state.set_artifact(f'goal_result_{round_no}',{'policy':POLICY,'result_sha256':json_sha(result),
        'completed_files':[{'path':str(p),'sha256':sha256_file(p)} for p in sorted(protected)]})
    return result


def execute_goal_continuation(reference,library,output,*,round_no=None,start_next=False):
    """Execute/resume one registered round. Explicit start_next appends the next."""
    from .goal_budget import get_authorization,stage_state
    output=Path(output).resolve(strict=True)
    probe=SimpleNamespace(output=output)
    policy=get_authorization(probe)
    saved=_read(output/'library_state.json')
    require((output/'catalog/inventory.json').is_file() and (output/'reference_catalog/inventory.json').is_file(),
            'goal:locked_catalog_missing')
    catalog=_catalog(library,output/'catalog')
    ref=_catalog(reference,output/'reference_catalog')['sources'][0]
    require(ref['sha256']==saved['input_lock']['reference_sha256'] and
            [{k:s[k] for k in ('source_id','sha256')} for s in catalog['sources']]==saved['input_lock']['library_sources'],
            'goal:inputs_changed')
    rounds=sorted(int(n.rsplit('_',1)[1]) for n in saved['artifacts'] if re.fullmatch(r'goal_round_[0-9]+', n))
    selected=round_no if round_no is not None else (rounds[-1]+1 if rounds and start_next else rounds[-1] if rounds else 5)
    require(type(selected) is int and selected>=5,'goal:invalid_round')
    read_state=SimpleNamespace(output=output,data=saved)
    completed=_result(read_state,selected)
    if completed:
        return completed
    require(selected in rounds or selected==(rounds[-1]+1 if rounds else 5),'goal:round_gap')
    if rounds and (start_next or selected>rounds[-1]):
        previous=_result(read_state,rounds[-1])
        require(previous is not None and previous['status'] not in {'stopped_no_new_edit','stopped_technical_failure'},
                'goal:no_progress_stop_requires_new_external_evidence')
    with file_lock(output/'.goal_feedback_execution.lock'):
        state=stage_state(output)
        policy=get_authorization(state)
        require(not any(c['status'] in {'submitted','uncertain','failed_known'}
                        for c in state.data['calls'][policy['baseline_request_count']:]),'goal:unsettled_call_no_execution')
        catalog=_catalog(library,output/'catalog')
        ref=_catalog(reference,output/'reference_catalog')['sources'][0]
        require(ref['sha256']==state.data['input_lock']['reference_sha256'] and
                [{k:s[k] for k in ('source_id','sha256')} for s in catalog['sources']]==state.data['input_lock']['library_sources'],
                'goal:inputs_changed')
        if not state.data['artifacts'].get(f'goal_round_{selected}'):
            state.set_artifact(f'goal_round_{selected}',{'policy':POLICY,'round':selected,
                'task_id':state.data['task_id'],
                'input_lock_sha256':json_sha(state.data['input_lock']),'goal_guide_sha256':policy['goal_guide_sha256'],
                'new_unique_windows':0,'max_renders':1,'observation_compatibility':policy['observation_compatibility']})
        try:
            return _execute(reference,library,state,policy,selected,_feedback(state,selected))
        except Exception as error:
            state._reload()
            if not any(c['status'] in {'submitted','uncertain'} for c in state.data['calls'][policy['baseline_request_count']:]):
                if isinstance(error,ValueError) and str(error).startswith('model_protocol_repair_exhausted:'):
                    name=str(error).split(':',1)[1]
                    failure=_known_protocol_failure(state,name,error)
                    return _stop(state,selected,'stopped_protocol_failure',failure)
                state.set_artifact(f'goal_failure_{selected}',{'policy':POLICY,'round':selected,
                    'type':type(error).__name__,'error':str(error),'usage':state.usage(),
                    'no_fabricated_render_or_review':True,'no_automatic_paid_replay':True})
            raise


execute_one_round = execute_goal_continuation

def _execute(reference, library, state, policy, round_no, feedback):
    from .goal_budget import get_authorization
    output = state.output
    catalog = _catalog(library, output / "catalog")
    ref = _catalog(reference, output / "reference_catalog")["sources"][0]
    require(ref["sha256"] == state.data["input_lock"]["reference_sha256"], "finecut_continuation:reference_changed")
    require([{k: s[k] for k in ("source_id", "sha256")} for s in catalog["sources"]]
            == state.data["input_lock"]["library_sources"], "finecut_continuation:library_changed")
    reading, methods = _canonical_reference(state, ref)
    windows = _completed_windows(state, catalog, policy)
    craft_context, craft = _craft_evidence(state, ref)
    reference_media = craft["overview_media"]["path"]  # Full-duration, normal-speed reference proxy.
    require(craft["overview_media"]["source_start_s"] == 0
            and craft["overview_media"]["source_end_s"] == ref["duration_s"],
            "finecut_continuation:complete_reference_media_required")
    folder = output / "artifacts" / f"goal_feedback_round_{round_no}"
    folder.mkdir(parents=True, exist_ok=True)
    glm = CodexMCP(state)
    get_authorization(state)
    require(not any(c["status"] in {"submitted", "uncertain", "failed_known"}
                    for c in state.data["calls"][policy["baseline_request_count"]:]),
            "finecut_continuation:unsettled_extension_call_no_new_submission")
    context = {"reference": reading, "editing_reference": methods,
        "reference_duration_s": ref["duration_s"], "reference_audio_stream_index": ref["audio_stream_index"],
        "reference_observation_status": "Historical canonical interpretation is a fallible model estimate, not a creative answer.",
        "craft_supplement": craft_context,
        "watched_windows": [_window_context(w, include_speech=False) for w in windows],
        "catalog": {"sources": [{k: s[k] for k in ("source_id", "sha256", "duration_s", "audio_stream_index")}
                                 | {"filename": Path(s["path"]).name} for s in catalog["sources"]]},
        "render_capabilities": {"max_segments": policy["max_segments"], "max_duration_s": 180,
            "speed": [.5, 2], "freeze_tail_s": [0, 10], "static_caption": True,
            "audio_modes": ["reference", "source", "mix", "silent"],
            "unsupported": ["smooth_speed_ramp", "optical_flow", "J/L_cut", "dynamic_reframing", "synthetic_video"]},
        "audio_semantics_policy": "用户确认参考只有BGM；内容以静音画面可读为主。没有实际音频听审，音乐节拍未知。",
        "observation_boundary": "Completed windows and craft reports remain fallible model observations. "
            "No Codex frame audit, replacement movie cuts, prescribed reference seconds or human story is supplied.",
        "actual_feedback": feedback, "known_exhausted_slice_inputs": _exhausted_slice_scopes(state),
        "goal_guide": Path(policy["goal_guide_path"]).read_text(encoding="utf-8"),
        "instruction": "依据当前完整固定参考、历史导航证据和独立craft观察自主写新草案。前次slot和声明若被源事实反证，应重新构造，不能将旧错声明当必须保留的表达义务。"
            "主旨保留，段落可变；未知参考技巧继续未知，可以主动一般精剪。"
            "只能从已完成精看的usable_ranges中自主选片，随后全部最终短片都要独立取证。"}
    if round_no >= 6:
        from . import goal_source_ranges as navigation
        name = f"goal_navigation_{round_no}"
        if state.data['artifacts'].get(name):
            bound = _artifact(state, name)
            require(bound['policy'] == navigation.POLICY and bound['round'] == round_no and
                    bound['input_lock_sha256'] == json_sha(state.data['input_lock']) and
                    bound['derived_usable_ranges'] == navigation.range_table(windows),
                    'goal:recorded_navigation_changed')
        else:
            failed = []
            for call in state.data['calls']:
                if call['status'] != 'received' or not call['name'].startswith(f'active_{round_no-1}_'):
                    continue
                call_folder = output / 'calls' / call['id']
                if not (call_folder / 'protocol_failure.json').is_file():
                    continue
                response = _read(call_folder / 'response.json')
                try:
                    raw = '\n'.join(c['text'] for c in response['result']['content'] if c.get('type') == 'text')
                    value = contracts.parse_model_json(raw)
                    candidate = value.get('plan', value)
                except (ValueError, KeyError, TypeError):
                    continue
                failed.append({'call_id': call['id'], 'response_sha256': call['response_sha256'],
                    'mechanical_blockers': navigation.plan_diagnostics(candidate, windows, context['known_exhausted_slice_inputs']),
                    'no_retroactive_protocol_pass': True})
            bound = {'policy': navigation.POLICY, 'round': round_no,
                'input_lock_sha256': json_sha(state.data['input_lock']),
                'derived_usable_ranges': navigation.range_table(windows),
                'known_exhausted_slice_inputs': context['known_exhausted_slice_inputs'],
                'previous_known_failure_diagnostics': failed,
                'instruction': navigation.INSTRUCTION, 'no_model_facts_or_cuts_modified': True}
            state.set_artifact(name, bound)
        context['source_range_navigation'] = bound
        context['known_exhausted_slice_inputs'] = bound['known_exhausted_slice_inputs']
    coarse_path = output / "coarse_index.json"
    if coarse_path.is_file():
        coarse = _read(coarse_path)
        for row in coarse:
            contracts.validate_coarse(row, catalog)
            require(any(c["status"] == "received" and (output / "calls" / c["id"] / "parsed.json").is_file()
                        and _read(output / "calls" / c["id"] / "parsed.json") == row
                        for c in _read(policy["baseline_state_path"])["calls"]), "finecut_continuation:coarse_model_binding_missing")
        context["coarse_index"] = coarse

    def validate_plan(value):
        if round_no >= 6:
            navigation.enforce_plan_ranges(value, windows, context['known_exhausted_slice_inputs'])
        contracts.validate_plan(value, catalog, windows, ref["sha256"], ref["duration_s"],
            reference_audio_stream_index=ref["audio_stream_index"], editing_reference=methods)
        validate_candidate_dispositions(value, windows)
        compile_library_plan(catalog, value, fps=value["fps"], width=value["width"], height=value["height"])
        validate_caption_layout(value, value["width"], value["height"])
        semantic_pipeline.validate_plan_claims(value, windows, policy["max_segments"])
        sources={s['source_id']:s['sha256'] for s in catalog['sources']}
        for segment in value['segments']:
            scope={'kind':'continuous_window','source_sha256':sources[segment['source_id']],
                   'source_start_s':segment['source_in_s'],'source_end_s':segment['source_out_s']}
            require(not any(row['scope']==scope for row in context['known_exhausted_slice_inputs']),
                    'goal:known_exhausted_slice_input_no_third_observation_select_new_evidence')

    _status(folder, "active_finecut_draft", usage=state.usage())
    draft = glm.call(f"active_{round_no}_draft", semantic_prompts.plan_prompt(context), reference_media, validate_plan)
    write_json(folder / "draft_plan.json", draft)
    prompt_view = _handbook_view(state, policy)
    def validate_refinement(value):
        validate_plan(value.get("plan"))
        finecut.validate_refinement(value, draft, policy["max_segments"])
    _status(folder, "active_finecut_refinement", usage=state.usage())
    refinement = glm.call(f"active_{round_no}_finecut", finecut.refinement_prompt(prompt_view, draft, context),
                          reference_media, validate_refinement)
    write_json(folder / "refinement.json", refinement)
    plan = refinement["plan"]
    write_json(folder / "plan.json", plan)
    from . import active_observation_compat
    repeated = _find_repeated_plan(state, catalog, plan, round_no)
    if repeated:
        return _stop(state, round_no, "stopped_no_new_edit", repeated)
    _status(folder, "active_finecut_exact_facts_and_claims", segments=len(plan["segments"]), usage=state.usage())
    checked = semantic_pipeline.observe_selected_slices(glm, plan,
        {s["source_id"]: s for s in catalog["sources"]}, windows, output / "media_cache", output, round_no,
        observation_validator=lambda v,s,sha,p: active_observation_compat.validate_observation(v,s,sha,p,enabled=True),
        claim_validator=lambda v,o,c: active_observation_compat.validate_claims(v,o,c,enabled=True))
    checked = finecut.bind_draft_obligations(checked, draft, refinement)
    write_json(output / f"semantic_audit/round_{round_no}/manifest.json", checked)
    get_authorization(state)
    _status(folder, "active_finecut_rendering", usage=state.usage())
    rendered = render_library_video(catalog, plan, output / f"render_{round_no}", reference_path=reference,
        fps=plan["fps"], width=plan["width"], height=plan["height"])
    actual = inventory_sources(rendered["rendered_path"], output / f"render_catalog_{round_no}")["sources"][0]
    output_media = prepare_window(actual, 0, rendered["measured_duration_s"], output / "media_cache", fps=30)
    timeline = detect_shot_timeline(actual, output / "media_cache/shot_timelines", threshold=3)
    evidence = associate_edl_boundaries(timeline, rendered)
    write_json(folder / "editing_evidence.json", evidence)
    blind = economy = review = None
    failure = None
    name = f"active_{round_no}_blind"
    try:
        _status(folder, "active_finecut_silent_blind", usage=state.usage())
        blind = glm.call(name, semantic_prompts.blind_prompt(rendered["measured_duration_s"], rendered["sha256"]),
            output_media["path"], lambda v: audit.validate_visual_blind(v, rendered["measured_duration_s"], rendered["sha256"]))
        write_json(folder / "blind_reading.json", blind)
        name = f"active_{round_no}_economy"
        manifest = finecut.economy_manifest(plan, rendered)
        for row,segment in zip(manifest['provenance'],plan['segments']):
            row['slot_id']=segment['slot_id']
        economy = glm.call(name, _economy_prompt(manifest, blind), output_media["path"],
                           lambda v: _validate_economy(v, manifest))
        write_json(folder / "economy_review.json", economy)
        name = f"active_{round_no}_review"
        review_context = {"reference": reading, "editing_reference": methods, "craft_supplement": craft_context,
            "actual_output_manifest":manifest,
            "protocol": audit.SEMANTIC_PROTOCOL, "video_sha256": rendered["sha256"],
            "actual_render_sha256": rendered["sha256"], "output_duration_s": rendered["measured_duration_s"],
            "blind_reading": blind, "source_observations": checked["observations"],
            "segment_checks": checked["segment_checks"], "required_claims": checked["required_claims"],
            "provenance": rendered["provenance"], "measured_output_timeline": compact_timeline(timeline),
            "edl_boundary_associations": evidence, "audio_review_limit": "Actual audio and music rhythm remain unverified.",
            "reference_observation_limit": context["reference_observation_status"]}
        def validate_review(value):
            from .goal_output_contracts import validate_goal_review
            contracts.validate_review(value, ref["sha256"])
            validate_method_review(value, methods, rendered["measured_duration_s"])
            audit.validate_semantic_review(value, blind, checked["observations"], checked["required_claims"],
                rendered["measured_duration_s"], rendered["sha256"], segment_checks=checked["segment_checks"])
            validate_goal_review(value,blind,checked['required_claims'],manifest)
        from .goal_output_contracts import REVIEW_INSTRUCTION
        review = glm.call(name, semantic_prompts.review_prompt(review_context)+'\n'+REVIEW_INSTRUCTION, output_media["path"], validate_review)
        write_json(folder / "review.json", review)
    except ValueError as error:
        failure = _known_protocol_failure(state, name, error)
        write_json(folder / "incomplete_output_stage.json", failure)
    semantic_pass = bool(review is not None and audit.semantic_review_passes(review, blind, checked["segment_checks"],
                         expected_segment_ids=[s["segment_id"] for s in plan["segments"]]))
    finecut_pass = bool(economy is not None and finecut.passes(refinement, economy) and all(c["status"] == "necessary" and not c["removable_intervals"] for c in economy["microcut_checks"]))
    result = {"status": "model_checked_library_candidate" if semantic_pass and finecut_pass else "library_candidate_with_limitations",
        "actual_output_support_policy":"goal_output_visible_support_v1",
        "final_video": rendered["rendered_path"], "final_sha256": rendered["sha256"], "selected_round": round_no,
        "delivery_mode": "sole_authorized_new_candidate_no_historical_comparison_or_selection_POST",
        "reference_sha256": ref["sha256"], "review": review,
        "review_status": "completed_protocol" if review is not None else "incomplete_protocol_failure",
        "review_failure": failure, "semantic_gate_passed": semantic_pass, "active_finecut_gate_passed": finecut_pass and semantic_pass,
        "active_finecut_protocol": finecut.POLICY, "continuation_policy": POLICY,
        "observation_compatibility_policy": policy['observation_compatibility']['policy'],
        "semantic_protocol": audit.SEMANTIC_PROTOCOL, "refinement_path": str(folder / "refinement.json"),
        "blind_reading_path": str(folder / "blind_reading.json") if blind is not None else None,
        "economy_review_path": str(folder / "economy_review.json") if economy is not None else None,
        "semantic_evidence_path": str(output / f"semantic_audit/round_{round_no}/manifest.json"),
        "craft_evidence_role": craft_context["binding_role"], "usage": state.usage(),
        "actual_fine_windows": len(windows), "new_unique_fine_windows": 0, "new_renders": 1,
        "effective_render_limit": round_no + 1, "model_goal_gate_passed": finecut_pass and semantic_pass, "human_quality_confirmation": False, "request_limit_policy": policy["request_limit_policy"],
        "human_creative_inputs": [], "source_generation_requests": 0,
        "evidence_limit": "Model checks are fallible, not human truth; no new-film quality improvement is presumed. "
            "Unidentified reference techniques remain unknown; general optimization is not certified reference transfer."}
    get_authorization(state)
    write_json(output / f"result_goal_feedback_{round_no}.json", result)
    protected = {p for directory in (folder, output / f"semantic_audit/round_{round_no}", output / f"render_{round_no}")
                 for p in directory.rglob("*") if p.is_file()}
    protected.add(output / f"result_goal_feedback_{round_no}.json")
    protected.update(Path(e['path']) for e in state.data['artifacts'].get(f'goal_navigation_{round_no}', []))
    protected.update(p for p in (output / "calls").glob("*/*")
                     if p.is_file() and p.parent.name in {c["id"] for c in state.data["calls"][policy["baseline_request_count"]:]})
    protected.update({Path(output_media["path"]), Path(output_media["path"]).parent / "lineage.json"})
    sources={s['source_id']:s for s in catalog['sources']}
    for segment in plan['segments']:
        proxy=prepare_window(sources[segment['source_id']],segment['source_in_s'],segment['source_out_s'],output/'media_cache',fps=30)
        protected.update({Path(proxy['path']),Path(proxy['path']).parent/'lineage.json'})
        protected.update(Path(frame['path']) for frame in proxy.get('frames',[]))
    state.set_artifact(f"goal_result_{round_no}", {"policy": POLICY, "result_sha256": json_sha(result),
        "completed_files": [{"path": str(p.resolve()), "sha256": sha256_file(p)} for p in sorted(protected)]})
    get_authorization(state)
    return result
