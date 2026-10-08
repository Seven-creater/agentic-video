"""One explicitly authorized, append-only editing revision of an existing task."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ..contract import require, rows, text
from . import contracts, prompts
from .editing import compact_timeline, validate_candidate_dispositions, validate_fine_editing, validate_method_review
from .media import inventory_sources, prepare_window, sha256_file
from .pipeline import CodexMCP, _catalog, _read, _status, _window_context, _speech_context
from .render import compile_library_plan, render_library_video, validate_caption_layout
from .shot_timeline import associate_edl_boundaries, detect_shot_timeline
from .state import LibraryState, LibraryStopped, file_lock, json_sha, write_json


REVISION_POLICY = 'one_authorized_editing_revision_v1'
AUTHORIZATION_ARTIFACT = 'editing_revision_authorization'


def _state(output):
    output = Path(output).resolve(strict=True)
    recorded = _read(output / 'library_state.json')
    return LibraryState(output,recorded['input_lock'],max_requests=recorded['max_requests'])


def _authorization(state):
    from .extension_budget import historical_state
    state = historical_state(state)
    entries = state.data['artifacts'].get(AUTHORIZATION_ARTIFACT,[])
    if not entries:
        raise LibraryStopped('editing_revision_requires_explicit_user_authorization')
    require(len(entries) == 1,'revision:only_one_authorization_allowed')
    policy = _read(entries[0]['path'])
    require(json_sha(policy) == entries[0]['sha256'],'revision:authorization_artifact_modified')
    require(policy['policy'] == REVISION_POLICY and policy['task_id'] == state.data['task_id'],
            'revision:authorization_task_or_policy_mismatch')
    require(policy['render_index'] == 2 and policy['authorized_additional_renders'] == 1
            and policy['effective_render_limit'] == 3,'revision:invalid_render_extension')
    require(policy['input_lock_sha256'] == json_sha(state.data['input_lock']), 'revision:input_lock_changed')
    require(policy['max_requests'] == state.max_requests,'revision:request_budget_changed')
    for item in policy['protected_files']:
        require(Path(item['path']).is_file() and sha256_file(item['path']) == item['sha256'],
                'revision:historical_artifact_changed:' + item['path'])
    for call in policy['original_calls']:
        require(next((c for c in state.data['calls'] if c['id'] == call['id']),None) == call,
                'revision:original_call_record_changed:' + call['id'])
    from .semantic_continuation import authorized_render_indices
    extra_authorized=authorized_render_indices(state)
    require(not any(p.is_dir() and p.name.removeprefix('render_').isdigit()
                    and int(p.name.removeprefix('render_')) > 2
                    and int(p.name.removeprefix('render_')) not in extra_authorized for p in state.output.glob('render_*')),
            'revision:unapproved_additional_render_present')
    return policy


def authorize_editing_revision(output, authorization_text):
    """Record trusted human authorization once; this never opens a new budget."""
    with file_lock(Path(output).resolve(strict=True)/'.editing_revision.lock'):
        return _authorize_editing_revision(output,authorization_text)


def _authorize_editing_revision(output,authorization_text):
    text(authorization_text,'revision/user_authorization')
    state = _state(output)
    if state.data['artifacts'].get(AUTHORIZATION_ARTIFACT):
        return _authorization(state)
    require(state.data['input_lock']['configuration']['max_renders'] == 2,'revision:base_render_limit_must_be_two')
    require(not any(c['status'] == 'submitted' for c in state.data['calls']), 'revision:original_request_pending')
    require(not (state.output/'render_2').exists(),'revision:render_2_exists_without_authorization')
    result = _read(state.output/'result.json')
    require(sha256_file(result['final_video']) == result['final_sha256'],'revision:old_selected_video_changed')
    protected = {state.output/'result.json',state.output/'reference_reading.json',state.output/'watched_windows.json'}
    protected.update(p for p in state.output.glob('*catalog/inventory.json'))
    protected.update(p for p in state.output.glob('media_cache/*/lineage.json'))
    if (state.output/'reference_asr.json').exists():
        protected.add(state.output/'reference_asr.json')
    if (state.output/'artifacts/editing_revision_v1/window_timelines.json').exists():
        protected.add(state.output/'artifacts/editing_revision_v1/window_timelines.json')
    protected.update(p for p in state.output.glob('calls/*/*.json'))
    protected.update(p for p in state.output.glob('render_*/*') if p.is_file())
    protected.update(p for p in state.output.glob('*_*.json') if p.name.startswith(('plan_','review_','blind_reading_','selected_review_')))
    policy = {'policy':REVISION_POLICY,'task_id':state.data['task_id'],
        'user_authorization':authorization_text,'base_render_limit':2,'effective_render_limit':3,
        'authorized_additional_renders':1,'render_index':2,
        'input_lock_sha256':json_sha(state.data['input_lock']),
        'max_requests':state.max_requests,'fine_window_limit':state.data['input_lock']['configuration']['max_fine'],
        'baseline_request_count':state.data['request_count'],'original_calls':deepcopy(state.data['calls']),
        'protected_files':[{'path':str(p.resolve()),'sha256':sha256_file(p)} for p in sorted(protected)],
        'new_protocol':prompts.EDITING_PROTOCOL,'old_policies_and_results_unchanged':True,
        'unknown_requests_never_replayed':True,'new_unique_fine_windows':0}
    state.set_artifact(AUTHORIZATION_ARTIFACT,policy)
    return _authorization(state)


def _validate_window_choice(value,windows,maximum):
    text(value.get('reason'),'revision/selection/reason')
    chosen = rows(value.get('window_ids'),'revision/selection/window_ids',nonempty=False)
    require(len(chosen) <= maximum and len(set(chosen)) == len(chosen),'revision:too_many_or_duplicate_windows')
    require(all(isinstance(w,str) and w in windows for w in chosen),'revision:unknown_window')


def execute_editing_revision(reference,library,output):
    _authorization(_state(output))  # Reject missing permission before creating the execution lock.
    with file_lock(Path(output).resolve(strict=True)/'.editing_revision.lock'):
        return _run_editing_revision(reference,library,output)


def _run_editing_revision(reference,library,output):
    state = _state(output)
    _authorization(state)  # Missing permission cannot create a failure artifact.
    try:
        return _execute_editing_revision(reference,library,output)
    except Exception as error:
        state.set_artifact('editing_revision_failure',{'error':str(error),'type':type(error).__name__,
            'usage':state.usage(),'no_automatic_paid_replay':True,'original_result_preserved':True})
        _status(state.output,'editing_revision_stopped',error=str(error),requests=state.usage()['requests'])
        raise


def _execute_editing_revision(reference,library,output):
    """Reuse the original task, make at most render_2, and retain old results."""
    state = _state(output)
    policy = _authorization(state)
    output = state.output
    if state.data['artifacts'].get('semantic_continuation_authorization'):
        # A later authorized run snapshots this historical result. Recover it
        # as recorded; newer cumulative usage must not overwrite old evidence.
        return _read(output/'result_revision_2.json')
    sources = _catalog(library,output/'catalog')
    ref = _catalog(reference,output/'reference_catalog')['sources'][0]
    require(ref['sha256'] == state.data['input_lock']['reference_sha256'],'revision:reference_changed')
    require([{'source_id':s['source_id'],'sha256':s['sha256']} for s in sources['sources']]
            == state.data['input_lock']['library_sources'],'revision:library_changed')
    windows = deepcopy(_read(output/'watched_windows.json'))
    require(len(windows) <= policy['fine_window_limit'] and all(w.get('status') == 'watched' for w in windows),
            'revision:invalid_completed_windows')
    by_window = {w['window_id']:w for w in windows}
    require(len(by_window) == len(windows),'revision:duplicate_window')
    for window in windows:
        require(sha256_file(window['path']) == window['sha256'],'revision:watched_proxy_changed')
        lineage = _read(Path(window['path']).parent/'lineage.json')
        require(all(lineage[k] == window[k] for k in ('source_id','source_sha256','source_start_s','source_end_s')),
                'revision:watched_proxy_lineage_changed')
        originals = [c for c in state.data['calls'] if c['status'] == 'received' and c['name'].startswith('fine_')
                     and (output/'calls'/c['id']/'parsed.json').is_file()]
        require(any(_read(output/'calls'/c['id']/'parsed.json').get('window_id') == window['window_id']
                    and _read(output/'calls'/c['id']/'parsed.json') == window['observation'] for c in originals),
                'revision:window_not_bound_to_received_fine_observation')
    reference_reading = _read(output/'reference_reading.json')
    contracts.validate_reference(reference_reading,ref['sha256'],ref['duration_s'])
    old_result = _read(output/'result.json')
    directory = output/'artifacts'/'editing_revision_v1'
    directory.mkdir(parents=True,exist_ok=True)
    cache = output/'media_cache'
    # Only the new overlay is modified; historical fine events/ranges keep their
    # original event indices for captions and source evidence.
    timeline_path = directory/'window_timelines.json'
    if timeline_path.exists():
        prepared = _read(timeline_path)
        require(prepared['watched_windows_sha256'] == sha256_file(output/'watched_windows.json'),
                'revision:prepared_timeline_windows_changed')
        require(json_sha({k:v for k,v in prepared.items() if k != 'record_sha256'}) == prepared['record_sha256'],
                'revision:prepared_timeline_record_changed')
        for window in windows:
            item = prepared['windows'][window['window_id']]
            require(item['proxy_sha256'] == window['sha256'],'revision:prepared_timeline_proxy_changed')
            window['editing_timeline'] = item['editing_timeline']
    reference_media = Path(reference).resolve()
    if reference_media.stat().st_size >= 8_000_000:
        reference_media = Path(prepare_window(ref,0,ref['duration_s'],cache,fps=12)['path'])
    glm = CodexMCP(state)
    _status(output,'editing_revision_reference',requests=state.usage()['requests'])
    methods = glm.call('revision_2_methods',prompts.editing_reference_prompt(reference_reading,
        compact_timeline(detect_shot_timeline(ref,cache/'shot_timelines',threshold=3.0))),reference_media,
        lambda v:contracts.validate_editing_reference(v,ref['sha256'],ref['duration_s'],reference_reading))
    write_json(directory/'reference_methods.json',methods)
    remaining = state.max_requests-state.usage()['requests']
    # Search, plan, blind, review and selection each reserve one format repair.
    max_reread = min(6,len(windows),max(0,(remaining-10)//2))
    stage = next((c for c in state.data['calls'] if c['name'] == 'revision_2_select_windows'),None)
    if stage:
        max_reread = min(6,len(windows))  # Recover paid work despite lower remaining budget.
    selection_context = {'reference':reference_reading,'editing_reference':methods,
        'watched_windows':[_window_context(w) for w in windows],'previous_review':old_result['review'],
        'maximum_reread_windows':max_reread,'no_new_unique_windows':True}
    selected = glm.call('revision_2_select_windows',prompts.BASE+prompts.EDITING_KNOWLEDGE+
        '从原已完成连续观察的窗口自主选择需要重看剪辑条件的窗口。不能新增窗口，最多'+str(max_reread)+
        '个；可以选空。不要决定新剧情或猜不存在的画面。只输出 {"reason":"具体缺项", "window_ids":["已有ID"]}\n'+
        json.dumps(selection_context,ensure_ascii=False),reference_media,
        lambda v:_validate_window_choice(v,by_window,max_reread))
    write_json(directory/'window_selection.json',selected)
    for window_id in selected['window_ids']:
        window = by_window[window_id]
        if 'editing_timeline' not in window:
            proxy = inventory_sources(window['path'],cache/('revision_shot_catalog_'+window_id))['sources'][0]
            window['editing_timeline'] = {'time_domain':'analysis_proxy_local_seconds','proxy_fps':12,
                'source_mapping':'Add source_start_s for source-second estimate; not original-film native PTS.',
                'timeline':compact_timeline(detect_shot_timeline(proxy,cache/'shot_timelines',threshold=3.0))}
        context = {'reference':reference_reading,'editing_reference':methods,'window':_window_context(window)}
        def validate_overlay(value):
            validate_fine_editing({**window['observation'],'editing_observations':value.get('editing_observations')},
                                  window,methods)
            for conflict in rows(value.get('observation_conflicts'),'revision/conflicts',nonempty=False):
                text(conflict,'revision/conflict')
        _status(output,'editing_revision_reread',window_id=window_id,requests=state.usage()['requests'])
        overlay = glm.call('revision_2_observe_'+window_id,prompts.BASE+prompts.EDITING_KNOWLEDGE+
            '实际重看当前连续代理，只补剪辑条件，不改写历史角色编号、event_indices或usable_ranges。'
            'local时间从代理0秒开始。如历史描述与当前可见画面不符，在observation_conflicts指出；不能默改历史。'
            '输出 {"editing_observations":[{"method_id":"已有ID","local_start_s":0,"local_end_s":1,'
            '"observed_form":"景别、动作阶段/方向、接切、停留等可见事实",'
            '"potential_use":"本任务潜在用途解释","limitations":[]}],"observation_conflicts":[]}\n'+
            json.dumps(context,ensure_ascii=False),window['path'],validate_overlay)
        write_json(directory/(window_id+'_editing_observation.json'),overlay)
        window['observation']['editing_observations'] = overlay['editing_observations']
        window['observation']['editing_observation_conflicts'] = overlay['observation_conflicts']
        if overlay['observation_conflicts']:
            window['observation']['historical_usable_ranges'] = window['observation']['usable_ranges']
            window['observation']['usable_ranges'] = []
            window['observation']['revision_edl_permission'] = 'blocked_due_to_observation_conflicts'
    write_json(directory/'planning_windows.json',windows)
    context = {'reference':reference_reading,'editing_reference':methods,'reference_duration_s':ref['duration_s'],
        'reference_audio_stream_index':ref['audio_stream_index'],'previous_review':old_result['review'],
        'watched_windows':[_window_context(w) for w in windows],
        'catalog':{'sources':[{k:s[k] for k in ('source_id','sha256','duration_s','audio_stream_index')}
                             | {'filename':Path(s['path']).name} for s in sources['sources']]},
        'render_capabilities':{'speed':[0.5,2],'max_duration_s':180,'max_segments':32,'freeze_tail_s':[0,10],
            'static_caption':True,'audio_modes':['reference','source','mix','silent'],
            'unsupported':['J/L_cut','audio_source_separation','synthetic_video']},
        'instruction':'根据全部真实素材与剪法条件自主重构。文字必须有实际动作和结果支撑；不能靠字幕掩盖主旨缺失。'
            '有新观察冲突的窗口本轮usable_ranges为空，不能使用；历史范围仅供解释，不是剪入许可。'
            '只有当前提供的已看usable_ranges可用于新EDL。角色ID在窗口内有效，跨窗口身份由你绑定。'}
    if (output/'reference_asr.json').exists():
        context['reference_speech_evidence'] = _speech_context(_read(output/'reference_asr.json'))
    context['audio_semantics_policy'] = '参考原声包含的人物身份与具体事实不能与新画面冲突。ASR仅作未核验语言证据，参考原声不等于分离的背景音乐。由你决定可用音频模式并保留局限。'
    def validate_plan(value):
        contracts.validate_plan(value,sources,windows,ref['sha256'],ref['duration_s'],
            reference_audio_stream_index=ref['audio_stream_index'],editing_reference=methods)
        validate_candidate_dispositions(value,windows)
        compile_library_plan(sources,value,fps=value['fps'],width=value['width'],height=value['height'])
        validate_caption_layout(value,value['width'],value['height'])
    _status(output,'editing_revision_planning',requests=state.usage()['requests'])
    plan = glm.call('revision_2_plan',prompts.editing_plan_prompt(context),reference_media,validate_plan)
    write_json(directory/'plan.json',plan)
    _authorization(state)  # No old history can change between planning and render.
    _status(output,'editing_revision_rendering',segments=len(plan['segments']))
    rendered = render_library_video(sources,plan,output/'render_2',reference_path=reference,
        fps=plan['fps'],width=plan['width'],height=plan['height'])
    render_source = inventory_sources(rendered['rendered_path'],output/'render_catalog_2')['sources'][0]
    output_media = prepare_window(render_source,0,rendered['measured_duration_s'],cache,fps=12)
    evidence = associate_edl_boundaries(detect_shot_timeline(render_source,cache/'shot_timelines',threshold=3.0),rendered)
    write_json(directory/'editing_evidence.json',evidence)
    _status(output,'editing_revision_blind_review',requests=state.usage()['requests'])
    blind = glm.call('revision_2_blind',prompts.blind_prompt(rendered['measured_duration_s']),output_media['path'],
        lambda v:contracts.validate_blind_reading(v,rendered['measured_duration_s']))
    write_json(directory/'blind_reading.json',blind)
    def validate_review(value):
        contracts.validate_review(value,ref['sha256'])
        validate_method_review(value,methods,rendered['measured_duration_s'])
    review = glm.call('revision_2_review',prompts.editing_review_prompt({'reference':reference_reading,
        'editing_reference':methods,'output_duration_s':rendered['measured_duration_s'],
        'actual_render_sha256':rendered['sha256'],'blind_reading':blind,'plan':plan,
        'provenance':rendered['provenance'],'edl_boundary_associations':evidence,
        'audio_review_limit':'Vision MCP has not heard actual output audio.'}),output_media['path'],validate_review)
    write_json(directory/'review.json',review)
    candidates = []
    for marker in sorted(output.glob('render_*/render_result.json')):
        round_no = int(marker.parent.name.split('_')[-1])
        require(round_no in {0,1,2},'revision:unexpected_render_round')
        record = _read(marker)
        require(sha256_file(record['rendered_path']) == record['sha256'],'revision:comparison_media_changed')
        if round_no == 2:
            reading,assessment = blind,review
        else:
            reading = _read(output/f'blind_reading_{round_no}.json')
            assessment = old_result['review'] if round_no == old_result['selected_round'] else _read(output/f'review_{round_no}.json')
        candidates.append({'round':round_no,'render':record,'blind':reading,'review':assessment})
    def validate_choice(value):
        require(type(value.get('selected_round')) is int and value['selected_round'] in {c['round'] for c in candidates},
                'revision:invalid_final_selection')
        text(value.get('reason'),'revision/final_selection/reason')
    choice = glm.call('revision_2_select_render',prompts.BASE+prompts.EDITING_KNOWLEDGE+
        '比较实际成片的盲读和审核，选择最能保留固定参考主旨及剪法、人物连贯的有效版本。'
        '不要因为是新版本就选它。旧版本审核没有新逐方法字段，不能猜它们已经通过；新版本亦须保留partial。'
        '只输出 {"selected_round":2,"reason":"证据、权衡和保留局限"}\n'+json.dumps(
        {'reference':reference_reading,'editing_reference':methods,
         'candidates':[{'round':c['round'],'blind':c['blind'],'review':c['review']} for c in candidates]},ensure_ascii=False),
        reference_media,validate_choice)
    write_json(output/'selection_revision_2.json',choice)
    best = next(c for c in candidates if c['round'] == choice['selected_round'])
    success = all(best['review'][key] == 'pass' for key in ('theme_status','editing_status','continuity_status'))
    result = {'status':'model_checked_library_candidate' if success else 'library_candidate_with_limitations',
        'final_video':best['render']['rendered_path'],'final_sha256':best['render']['sha256'],
        'new_video':rendered['rendered_path'],'new_sha256':rendered['sha256'],'new_review':review,
        'selected_round':choice['selected_round'],'selection_reason':choice['reason'],'review':best['review'],
        'reference_sha256':ref['sha256'],'editing_protocol':prompts.EDITING_PROTOCOL,'revision_policy':REVISION_POLICY,
        'usage':state.usage(),'actual_fine_windows':len(windows),'actual_renders':len(candidates),
        'effective_render_limit':3,'human_creative_inputs':[],'source_generation_requests':0,
        'evidence_limit':'Model review is not human truth; actual audio rhythm remains unverified.'}
    _authorization(state)
    write_json(output/'result_revision_2.json',result)
    _status(output,'editing_revision_completed',**result)
    return result
