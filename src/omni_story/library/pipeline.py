"""Local execution for a Codex task using the official vision MCP.

The queue is an external supported-agent tool connection, not a Coding Plan
model API endpoint. The optional OpenCode adapter supplies a supported independent
agent connection; it does not change the existing Codex route.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
import traceback

from . import contracts, prompts
from .media import (MEDIA_EXTENSIONS, create_contact_sheet, inventory_sources, prepare_window,
                    probe_media, sha256_file, verify_source)
from .state import LibraryState, LibraryStopped, json_sha, write_json, scope_fingerprint
from .editing import (compact_timeline, validate_candidate_dispositions, validate_method_review,
                      validate_fine_editing)
from .shot_timeline import detect_shot_timeline, associate_edl_boundaries


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _speech_context(speech):
    """Pack exact segment evidence; raw/word-level records stay in local files."""
    if not isinstance(speech, dict):
        return {'status':'no_speech_evidence'}
    keys = ('status','source_id','source_sha256','source_start_s','source_end_s',
            'source_offset_s','audio_stream_index','language','language_probability','evidence_limit')
    packed = {key:speech[key] for key in keys if key in speech}
    segment_keys = ('segment_id','source_start_s','source_end_s','local_start_s','local_end_s',
                    'text','avg_logprob','no_speech_prob')
    packed['segments'] = [{key:segment[key] for key in segment_keys if key in segment}
                          for segment in speech.get('segments',[])]
    packed['evidence_limit'] = 'Unverified ASR segment text and timings; no speaker identity or music/beat evidence.'
    return packed


def _window_context(window, *, include_speech=True):
    """Keep all model observations and source mapping without codec/cache noise."""
    keys = ('window_id','source_id','source_sha256','source_start_s','source_end_s',
            'source_offset_s','media_duration_s','duration_s','sha256','audio_stream_index',
            'audio_present','time_mapping','mapping_tolerance_s','status','observation','editing_timeline')
    packed = {key:window[key] for key in keys if key in window}
    if include_speech and 'asr' in window:
        packed['asr'] = _speech_context(window['asr'])
    return packed


def _usage_for(output, job_id):
    for entry in reversed(_http_evidence(output, job_id)):
        if entry.get('type') == 'response' and entry.get('job_id') == job_id:
            try:
                return json.loads(entry['body']).get('usage', {})
            except (ValueError, TypeError):
                return {}
    return {}


def _http_evidence(output, job_id):
    records = []
    for name in ('mcp_http.jsonl', 'mcp_http_sf_0.jsonl', 'mcp_http_sf_3.jsonl'):
        journal = Path(output) / name
        if not journal.exists():
            continue
        lines = journal.read_bytes().split(b'\n')
        for index, line in enumerate(lines):
            if not line:
                continue
            # A different lane may still be appending its final line. Its
            # incomplete bytes are not evidence for this job or a lost reply.
            try:
                entry = json.loads(line.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                if index == len(lines) - 1:
                    continue
                raise LibraryStopped('corrupt_complete_http_journal_line:' + name) from error
            if entry.get('job_id') == job_id:
                records.append(entry)
    return records


def _captured_reply(entries):
    for entry in reversed(entries):
        if entry.get('type') == 'response' and entry.get('status') == 200:
            body = json.loads(entry['body'])
            if body.get('choices'):
                choice = body['choices'][0]
                content = choice.get('message', {}).get('content') or ''
                return entry, {'status': 'complete', 'recovered_from_original_http': True,
                    'finish_reason': choice.get('finish_reason'),
                    'result': {'content': [{'type': 'text', 'text': content}]}}
    return None, None


def _outcome_unknown(entries):
    requests = {e.get('seq') for e in entries if e.get('type') == 'request'}
    responses = {e.get('seq') for e in entries if e.get('type') == 'response'}
    return bool(requests - responses) or any(e.get('type') == 'unknown_result' for e in entries)


class CodexMCP:
    """File handoff to the official MCP connected by the active Codex agent.

Only structural validation/format repair happens here. All creative responses
remain model authored. It never constructs or sends model HTTP requests.
"""
    provider = 'official_vision_mcp_in_codex'
    def __init__(self, state, *, timeout_s=720):
        self.state = state
        self.output = state.output
        self.timeout_s = timeout_s
        self.queue = self.output / 'mcp_queue'
        self.queue.mkdir(exist_ok=True)
        if not (self.output / 'mcp_ready.json').exists():
            raise LibraryStopped('connect_official_vision_mcp_in_codex_first')
        self.recover_received()

    def recover_received(self):
        # A received reply may outlive the local process; never resend its POST.
        self.state._reload()
        for call in list(self.state.data['calls']):
            if call['status'] not in {'submitted', 'uncertain', 'failed_known'}:
                continue
            entries = _http_evidence(self.output, call['id'])
            evidence, captured = _captured_reply(entries)
            if captured:
                self.state.reconcile_received(call, captured, evidence=evidence)
                continue
            if call['status'] == 'failed_known' and _outcome_unknown(entries):
                self.state.reclassify_uncertain(call, evidence=entries)
                continue
            response_path = self.queue / (call['id'] + '.response.json')
            if response_path.exists():
                reply = _read(response_path)
                if reply['status'] == 'complete':
                    self.state.complete_call(call, reply, usage=_usage_for(self.output, call['id']))
                else:
                    if call['status'] == 'submitted':
                        self.state.fail_call(call, reply.get('error', 'MCP_error'),
                            uncertain=reply['status'] == 'unknown' or _outcome_unknown(entries))

    def _submit(self, name, request, *, repair_of=None):
        digest = json_sha(request)
        self.state._reload()
        previous = next((c for c in self.state.data['calls'] if c['request_sha256'] == digest), None)
        if previous:
            if previous['status'] == 'received':
                folder = self.output/'calls'/previous['id']
                saved,reply = _read(folder/'request.json'),_read(folder/'response.json')
                if json_sha(saved) != previous['request_sha256'] or json_sha(reply) != previous['response_sha256']:
                    raise LibraryStopped('recorded_model_request_or_reply_modified:' + previous['id'])
                return previous,reply
            if previous['status'] != 'submitted':
                raise LibraryStopped('recorded_request_not_received_no_replay:' + previous['id'])
            call = previous  # Wait for the existing original submission, never send again.
        else:
            if (self.output / 'mcp_stop').exists():
                raise LibraryStopped('official_MCP_connection_stopped_reconnect_in_Codex')
            call, _ = self.state.begin_call(name, request, repair_of=repair_of)
            write_json(self.queue / (call['id'] + '.request.json'),
                       {'job_id': call['id'], 'tool': request['tool'], 'arguments': request['arguments']})
        response_path = self.queue / (call['id'] + '.response.json')
        deadline = time.monotonic() + self.timeout_s
        while not response_path.exists():
            if time.monotonic() > deadline:
                # Keep submitted: the server may still return a paid reply.
                raise LibraryStopped('MCP_wait_timed_out_inspect_original_job_no_replay:' + call['id'])
            time.sleep(0.25)
        reply = _read(response_path)
        if reply['status'] != 'complete':
            entries = _http_evidence(self.output, call['id'])
            evidence, captured = _captured_reply(entries)
            if captured:
                self.state.reconcile_received(call, captured, evidence=evidence)
                return call, captured
            self.state.fail_call(call, reply.get('error', str(reply.get('result'))),
                                 uncertain=reply['status'] == 'unknown' or _outcome_unknown(entries))
            raise LibraryStopped('official_MCP_failure:' + call['id'])
        self.state.complete_call(call, reply, usage=_usage_for(self.output, call['id']))
        return call, reply

    def call(self, name, prompt, media, validator, *, image=False, scope=None):
        media = Path(media).resolve(strict=True)
        if media.stat().st_size >= 8_000_000:
            raise ValueError('official_mcp_media_limit_8mb')
        argument = 'image_source' if image else 'video_source'
        original = {'tool': 'analyze_image' if image else 'analyze_video',
                    'arguments': {argument: str(media), 'prompt': prompt},
                    'media_sha256': sha256_file(media), 'provider': self.provider,
                    'policy_version': prompts.POLICY_VERSION}
        # Preserve the exact digest of already paid original work. New independent
        # jobs carry a measured source scope; changing encoding cannot replay an
        # unknown observation of that same original source interval.
        self.state._reload()
        recorded = any(c['request_sha256'] == json_sha(original) for c in self.state.data['calls'])
        if scope is None and not recorded and self.state.data.get('continuation_policy'):
            lineage_path = media.parent / 'lineage.json'
            if lineage_path.exists():
                lineage = _read(lineage_path)
                scope = {k:lineage[k] for k in ('kind','source_sha256','source_start_s','source_end_s')}
            else:
                scope = {'kind':'complete_file','source_sha256':original['media_sha256'],
                         'source_start_s':0,'source_end_s':probe_media(media)['duration_s']}
        if scope is not None:
            lineage_path = media.parent / 'lineage.json'
            if lineage_path.exists() and scope_fingerprint(scope) != scope_fingerprint(_read(lineage_path)):
                raise LibraryStopped('observation_scope_does_not_match_actual_media_lineage')
            original['observation_scope'] = scope
        prior_stage = next((c for c in self.state.data['calls'] if c['name'] == name and not c.get('repair_of')),None)
        if prior_stage:
            saved = _read(self.output/'calls'/prior_stage['id']/'request.json')
            if (json_sha(saved) != prior_stage['request_sha256'] or saved['media_sha256'] != original['media_sha256']
                    or saved['tool'] != original['tool']):
                raise LibraryStopped('paid_stage_input_changed_use_original_artifacts:' + name)
            original = saved
            prompt = saved['arguments']['prompt']
        request = original
        parent = None
        for attempt in range(2):
            call, reply = self._submit(name if attempt == 0 else name + '_repair', request, repair_of=parent)
            expected_name = name if attempt == 0 else name + '_repair'
            historical_cache = call['name'] != expected_name
            result_text = '\n'.join(c['text'] for c in reply['result']['content'] if c.get('type') == 'text')
            try:
                value = contracts.parse_model_json(result_text)
                validator(value)
                parsed_path = self.output / 'calls' / call['id'] / 'parsed.json'
                if parsed_path.exists():
                    if _read(parsed_path) != value:
                        raise LibraryStopped('recorded_parsed_reply_modified:' + call['id'])
                elif historical_cache:
                    raise LibraryStopped('historical_reply_requires_explicit_derived_binding:' + call['id'])
                else:
                    write_json(parsed_path, value)
                return value
            except (ValueError, TypeError, KeyError) as error:
                failure = {'error': str(error), 'attempt': attempt, 'model_text': result_text}
                if reply.get('finish_reason') == 'length':
                    self.state._reload()
                    usage = next(c['usage'] for c in self.state.data['calls'] if c['id'] == call['id'])
                    failure['output_limit'] = {'finish_reason': 'length',
                        'completion_tokens': usage.get('completion_tokens'),
                        'reasoning_tokens': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens'),
                        'content_characters': len(result_text)}
                failure_path = self.output / 'calls' / call['id'] / 'protocol_failure.json'
                if historical_cache or failure_path.exists():
                    # Revalidating a paid reply under a later contract must never
                    # rewrite its original parse/failure history.
                    diagnostic = {'stage': name, 'call_id': call['id'],
                        'request_sha256': call['request_sha256'],
                        'response_sha256': call['response_sha256'], **failure}
                    path = self.output / 'artifacts' / 'cached_reply_validation' / (json_sha(diagnostic) + '.json')
                    if not path.exists():
                        write_json(path, diagnostic)
                else:
                    write_json(failure_path, failure)
                if attempt:
                    raise ValueError('model_protocol_repair_exhausted:' + name) from error
                parent = call
                self.state._reload()
                repair = next((c for c in self.state.data['calls'] if c.get('repair_of') == call['id']), None)
                if repair:
                    # Preserve the sole repair's actual prompt and request hash,
                    # even when a new validator reports a different first error.
                    request = _read(self.output / 'calls' / repair['id'] / 'request.json')
                    if json_sha(request) != repair['request_sha256']:
                        raise LibraryStopped('recorded_model_request_modified:' + repair['id'])
                    continue
                if historical_cache:
                    raise LibraryStopped('historical_format_failure_no_new_repair:' + call['id']) from error
                request = {**original, 'arguments': {**original['arguments'], 'prompt': prompt +
                    '\n上次输出未通过本地协议校验。只修复JSON字段、ID和时间域，不得补造画面证据。' +
                    ('\n上次输出达到生成上限。减少重复论述和长篇理由，优先完整输出所有必需结构；不得删字段或证据。'
                     if reply.get('finish_reason') == 'length' else '') +
                    json.dumps({'validation_error': str(error), 'previous_response': result_text}, ensure_ascii=False)}}
        raise AssertionError('unreachable')


def _catalog(paths, output):
    saved = Path(output) / 'inventory.json'
    if saved.exists():
        catalog = _read(saved)
        actual = {str(p.resolve()) for p in Path(paths).iterdir()
                  if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS} if Path(paths).is_dir() else {str(Path(paths).resolve())}
        if {s['path'] for s in catalog['sources']} != actual:
            raise LibraryStopped('library_file_set_changed')
        for source in catalog['sources']:
            verify_source(source)
        return catalog
    return inventory_sources(paths, output)


def _status(output, stage, **details):
    value = {'stage': stage, 'at_unix': time.time(), **details}
    write_json(Path(output) / 'current_status.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _adaptive_coarse(state, glm, sources, reference_reading, cache, *, frames, span_s):
    """Overview then model-selected zooms, with old observations kept as evidence.

    This strategy is recorded separately; it cannot reset locked inputs or usage.
    Completed observations are recovered from hashed raw model replies, not edited
    index summaries. A pending original call must settle before this function runs.
    """
    output = state.output
    coarse, failures, by_range = [], [], {}
    last_valid_sheet = None
    source_map = {s['source_id']:s for s in sources['sources']}
    state._reload()
    for call in state.data['calls']:
        if call['status'] == 'uncertain':
            request = _read(output/'calls'/call['id']/'request.json')
            media = request.get('arguments',{}).get('image_source') or request.get('arguments',{}).get('video_source')
            lineage_path = Path(media).parent/'lineage.json' if media else None
            lineage = _read(lineage_path) if lineage_path and lineage_path.exists() else {}
            failures.append({'call_id':call['id'],'status':'unobserved_unknown_paid_result',
                'source_id':lineage.get('source_id'),
                'requested_coverage_s':[lineage.get('source_start_s'),lineage.get('source_end_s')]})
        if call['status'] != 'received' or call['name'] == 'coarse_zoom_search' or not call['name'].startswith(('coarse_', 'overview_', 'zoom_')):
            continue
        folder = output / 'calls' / call['id']
        request, reply = _read(folder / 'request.json'), _read(folder / 'response.json')
        if json_sha(request) != call['request_sha256']:
            raise LibraryStopped('recorded_model_request_modified:' + call['id'])
        if json_sha(reply) != call['response_sha256']:
            raise LibraryStopped('recorded_model_reply_modified:' + call['id'])
        media = request['arguments'].get('image_source')
        if not media:
            continue
        lineage = _read(Path(media).parent / 'lineage.json')
        if sha256_file(media) != request['media_sha256']:
            raise LibraryStopped('recorded_coarse_image_modified:' + call['id'])
        if any(lineage[k] != lineage['spec'][k] for k in ('kind','source_sha256','source_start_s','source_end_s')):
            raise LibraryStopped('recorded_coarse_lineage_spec_modified:' + call['id'])
        try:
            model_text = '\n'.join(c['text'] for c in reply['result']['content'] if c.get('type') == 'text')
            value = contracts.parse_model_json(model_text)
            contracts.validate_coarse(value, sources)
            if (value['source_id'] != lineage['source_id'] or value['coverage_s'] !=
                    [lineage['source_start_s'],lineage['source_end_s']] or
                    lineage['source_sha256'] != source_map[value['source_id']]['sha256']):
                raise ValueError('coarse_reply_does_not_match_original_media')
            if any(min(abs(event['timestamp_s']-frame['source_time_s']) for frame in lineage['frames']) > 1.0
                   for event in value['events']):
                raise ValueError('legacy_coarse_event_not_at_sample_time')
        except (ValueError, TypeError, KeyError) as error:
            failures.append({'call_id':call['id'],'status':'unobserved_protocol_failure','error':str(error)})
            continue
        key = (value['source_id'],*value['coverage_s'])
        by_range[key] = value
        last_valid_sheet = lineage
    coarse = list(by_range.values())

    def observe(source, start, end, name, phase):
        nonlocal last_valid_sheet
        key = (source['source_id'],start,end)
        sheet = create_contact_sheet(source,start,end,cache,frame_count=frames)
        if key in by_range:
            last_valid_sheet = sheet
            return sheet
        if any(f.get('status') == 'unobserved_unknown_paid_result' and
               f.get('source_id') == source['source_id'] and f.get('requested_coverage_s') == [start,end]
               for f in failures):
            return sheet  # Never resubmit an unknown page, including on resume.
        _status(output,phase,source_id=source['source_id'],source_range=[start,end],requests=state.usage()['requests'])
        known = {r['role_id']:r for c in coarse if c['source_id'] == source['source_id'] for r in c['roles']}
        context = {'reference':reference_reading,'known_roles':list(known.values()),
                   'source_filename_metadata':Path(source['path']).name,
                   'evidence_limit':'Only sampled instants observed; gaps and continuous actions remain unknown.'}
        def validate(value):
            contracts.validate_coarse(value,sources)
            if value['source_id'] != source['source_id'] or value['coverage_s'] != [start,end]:
                raise ValueError('coarse_does_not_match_requested_page')
            if any(min(abs(event['timestamp_s']-f['source_time_s']) for f in sheet['frames']) > 1.0
                   for event in value['events']):
                raise ValueError('coarse_event_not_at_provided_sample_time')
        try:
            value = glm.call(name,prompts.coarse_prompt(source['source_id'],[start,end],
                [{k:f[k] for k in ('frame_id','source_time_s')} for f in sheet['frames']],context)+
                '\ncoverage_s必须原样照抄'+json.dumps([start,end])+'，它是请求范围，不能替换为首尾抽样帧时间。'
                '只选最多6个有导航价值的可见瞬间，每条一句话最多60字，人物最多8个，未知最多4项。不要逐帧长篇分析。',
                sheet['path'],validate,image=True,
                scope={k:sheet[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
        except (ValueError,LibraryStopped) as error:
            if not (str(error).startswith('model_protocol_repair_exhausted:') or str(error).startswith('official_MCP_failure:')):
                raise
            failure = {'source_id':source['source_id'],'requested_coverage_s':[start,end],
                       'status':'unobserved_protocol_failure','error':str(error)}
            failures.append(failure)
            state.set_artifact('coarse_protocol_failure',failure)
        else:
            coarse.append(value)
            by_range[key] = value
            last_valid_sheet = sheet
        write_json(output/'coarse_index.json',coarse)
        write_json(output/'coarse_failures.json',failures)
        return sheet

    for source in sources['sources']:
        sheet = observe(source,0,source['duration_s'],'overview_'+source['source_id'][-8:],'global_overview')
    if last_valid_sheet is None:
        raise LibraryStopped('no_valid_coarse_evidence_available_for_search')
    compact = {'sources':[{k:s[k] for k in ('source_id','sha256','duration_s','audio_stream_index')}
                         | {'filename':Path(s['path']).name} for s in sources['sources']]}
    zoom_context = {'reference':reference_reading,'catalog':compact,'coarse_index':coarse,
                    'coarse_failures':failures,'max_windows':4,'max_window_s':span_s}
    zoom_prompt = (prompts.BASE + '这些概览间隔很大，仅用于导航。围绕固定参考，选择最有必要进一步抽样的区域，'
        '用于定位角色事件及支持连续精看。全库最多4个区域，每个区域不超过'+str(span_s)+'秒。'
        '不要重复已经提供的相同coverage；缺乏证据时可探索未观察区域。这里尚不输出剪辑入出点。'
        '只返回JSON {"reason":"需要展开哪些缺项","windows":[{"source_id":"ID","start_s":0,"end_s":600,'
        '"question":"要确认什么","role_ids":["candidate_A"]}]}\n'+json.dumps(zoom_context,ensure_ascii=False))
    zoom_media = last_valid_sheet
    state._reload()
    prior_zoom = next((c for c in state.data['calls'] if c['name'] == 'coarse_zoom_search' and not c.get('repair_of')),None)
    if prior_zoom:
        prior_request = _read(output/'calls'/prior_zoom['id']/'request.json')
        zoom_media = _read(Path(prior_request['arguments']['image_source']).parent/'lineage.json')
    _status(output,'choosing_coarse_regions',requests=state.usage()['requests'],valid_sparse_pages=len(coarse))
    try:
        zoom = glm.call('coarse_zoom_search',zoom_prompt,zoom_media['path'],
                        lambda v:contracts.validate_search(v,sources,max_windows=4,max_window_s=span_s),image=True,
                        scope={k:zoom_media[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
    except ValueError as error:
        if not str(error).startswith('model_protocol_repair_exhausted:'):
            raise
        state.set_artifact('coarse_zoom_protocol_failure',{'error':str(error),
            'action':'Request continuous windows directly from verified sparse overviews; no invented regional decision.'})
        zoom = {'windows':[]}
    else:
        write_json(output/'coarse_zoom_search.json',zoom)
    for requested in zoom['windows']:
        key = json_sha(requested)[:16]
        sheet = observe(source_map[requested['source_id']],requested['start_s'],requested['end_s'],
                        'zoom_'+key,'query_driven_coarse')
    return coarse,failures,last_valid_sheet['path']


def execute(reference, library, output, *, span_s=600, frames=18, max_fine=16, max_requests=80,
            asr=True, editing_v2=False, semantic_audit=False, active_finecut=False,
            model_factory=None, provider_config=None, registry_path=None, reference_seed=None,
            failure_report_name='failure.json'):
    if failure_report_name not in {'failure.json', 'failure_capacity_recovery_v1.json',
                                  'failure_remaining_candidate_v1.json'}:
        raise ValueError('unsupported_failure_report_name')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = _catalog(library, output / 'catalog')
    ref_catalog = _catalog(reference, output / 'reference_catalog')
    ref = ref_catalog['sources'][0]
    source_map = {s['source_id']: s for s in sources['sources']}
    config = {'span_s': span_s, 'frames': frames, 'max_fine': max_fine, 'asr': asr,
              'max_rounds': 2, 'max_renders': 2, 'provider': 'official_vision_mcp_in_codex'}
    if provider_config is not None:
        if model_factory is None:
            raise ValueError('provider_configuration_requires_model_factory')
        config.update(provider_config)
    if reference_seed is not None:
        config['reference_seed_sha256'] = json_sha(reference_seed)
    lock = {'reference_sha256': ref['sha256'], 'library_sources':
            [{'source_id': s['source_id'], 'sha256': s['sha256']} for s in sources['sources']], 'configuration': config}
    state = LibraryState(output, lock, max_requests=max_requests, registry_path=registry_path)
    from . import active_finecut as precision
    active_finecut = precision.enable_policy(state, active_finecut)
    from . import semantic_audit as semantic, semantic_prompts, semantic_pipeline
    semantic_audit = semantic_pipeline.enable_policy(state, semantic_audit or active_finecut)
    editing_v2 = editing_v2 or semantic_audit
    # The input and hard-budget lock stays intact. A forward policy cannot turn
    # old paid plans into new work or authorize an additional render.
    editing_policy = state.data['artifacts'].get('editing_execution_policy')
    if isinstance(editing_policy,list):
        if editing_policy:
            record = editing_policy[-1]
            editing_policy = _read(record['path'])
            if json_sha(editing_policy) != record['sha256']:
                raise LibraryStopped('recorded_editing_execution_policy_modified')
        else:
            editing_policy = None
    if editing_v2 and not editing_policy:
        if any(c['name'].startswith('plan_') for c in state.data['calls']):
            raise LibraryStopped('editing_v2_cannot_reinterpret_existing_plans_or_reset_render_budget')
        editing_policy = {
            'policy':prompts.EDITING_PROTOCOL, 'input_and_hard_budgets_unchanged':True,
            'applies_to':'New plans and their actual-output reviews only.',
            'quality_is_not_implied_by_structural_validation':True,
            'scene_change_threshold_percent':3.0}
        state.set_artifact('editing_execution_policy',editing_policy)
    editing_v2 = bool(editing_policy)
    if editing_v2 and editing_policy.get('policy') != prompts.EDITING_PROTOCOL:
        raise LibraryStopped('unsupported_recorded_editing_execution_policy')
    glm = (model_factory or CodexMCP)(state)
    cache = output / 'media_cache'
    reference_media = reference if Path(reference).stat().st_size < 8_000_000 else prepare_window(
        ref,0,ref['duration_s'],cache,fps=12)['path']
    model = None
    def transcript(source, start, end):
        nonlocal model
        if not asr or source['audio_stream_index'] is None:
            return {'status': 'not_requested'}
        from .asr import transcribe_window
        if model is None:
            from faster_whisper import WhisperModel
            model = WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=4,
                                 download_root=str(output.parent / 'library_models'))
        try:
            return transcribe_window(source, start, end, output / 'asr_cache', model=model)
        except ValueError as error:
            # Speech is optional. Reject failed alignment rather than inventing
            # words/times; cache integrity or changed inputs remain fatal.
            if not str(error).startswith(('asr_timestamp_', 'asr_segment_', 'asr_word_timestamp_')):
                raise
            rejected = {'status':'rejected_local_asr_alignment','error':str(error),
                        'source_id':source['source_id'],'source_range':[start,end],
                        'segments':[],'evidence_limit':'No usable speech evidence supplied.'}
            state.set_artifact('local_asr_alignment_failure',rejected)
            return rejected

    try:
        _status(output, 'reference_observation', requests=state.usage()['requests'])
        ref_asr = transcript(ref, 0, ref['duration_s'])
        write_json(output / 'reference_asr.json', ref_asr)
        if reference_seed is None:
            reference_reading = glm.call('reference', prompts.reference_prompt(ref['sha256'], ref['duration_s']) +
                '\n本地ASR是未核验语言证据，音乐不在其范围：' + json.dumps(ref_asr, ensure_ascii=False),
                reference_media, lambda v: contracts.validate_reference(v, ref['sha256'], ref['duration_s']))
        else:
            reference_reading = reference_seed['full_response']['reference']
            contracts.validate_reference(reference_reading, ref['sha256'], ref['duration_s'])
            state.set_artifact('server_cached_reference_navigation', reference_seed)
        write_json(output / 'reference_reading.json', reference_reading)
        editing_reference = None
        if editing_v2:
            reference_timeline = compact_timeline(detect_shot_timeline(ref, cache / 'shot_timelines',threshold=3.0))
            if reference_seed is None:
                editing_reference = glm.call('editing_reference_v2',
                    prompts.editing_reference_prompt(reference_reading, reference_timeline), reference_media,
                    lambda v:contracts.validate_editing_reference(v,ref['sha256'],ref['duration_s'],reference_reading))
            else:
                editing_reference = reference_seed['full_response']['editing_reference']
                contracts.validate_editing_reference(editing_reference, ref['sha256'],ref['duration_s'],reference_reading)
            write_json(output / 'editing_reference_v2.json',editing_reference)
        if not state.data['artifacts'].get('strategy_transition'):
            state.set_artifact('strategy_transition', {
                'strategy':'adaptive_coarse_v2','previous':'fixed_30_page_grid',
                'overview_frames_per_source':frames,'model_selected_regions_max':4,
                'region_max_s':span_s,'fine_soft_cap_per_round':8,
                'input_lock_and_hard_budgets_unchanged':True,
                'unknown_results_excluded_from_evidence':True})
        state.enable_independent_continuation()
        if not state.data['artifacts'].get('theme_transfer_policy'):
            state.set_artifact('theme_transfer_policy', {
                'policy':'same_meaning_different_story_facts_v2',
                'user_requirement':'Reference fixes intended meaning; library may change characters, events and slot structure.',
                'reason':'The first review treated the specific reference protagonist as a mandatory identity match.',
                'scope':'Future prompts and reviews only. Historical reference reading and reviews remain unchanged.',
                'no_forced_quality_verdict':True})
        if not state.data['artifacts'].get('model_context_policy'):
            state.set_artifact('model_context_policy', {
                'policy':'exact_evidence_packing_v2',
                'reason':'First plan input used 178988 prompt tokens; codec metadata and word records were duplicated.',
                'retained':['all fine observations','window and source SHAs','source time mapping','exact ASR segment text and timings'],
                'excluded_from_future_prompts':['codec/container metadata','local cache paths','duplicated ASR word records'],
                'original_records_unchanged':True,'new_requests_only':True})
        navigation_reference = ({**reference_reading,'editing_reference':editing_reference}
                                if editing_v2 else reference_reading)
        if reference_seed is not None:
            navigation_reference = {**navigation_reference,
                'reference_protocol_limit': reference_seed['evidence_limit']}
        coarse, coarse_failures, planning_image = _adaptive_coarse(
            state,glm,sources,navigation_reference,cache,frames=frames,span_s=span_s)
        compact_catalog = {'sources': [{k:s[k] for k in ('source_id','sha256','duration_s','audio_stream_index')}
                                       | {'filename': Path(s['path']).name} for s in sources['sources']]}
        windows = []
        plans = {}
        renders = []
        last_review = None
        first_round = 0
        if state.data['artifacts'].get('server_remaining_candidate_feedback'):
            from .server_capacity_recovery import remaining_candidate
            continuation = remaining_candidate(output)
            windows = _read(continuation['watched_windows_path'])
            first_round = continuation['remaining_candidate']
            last_review = continuation['feedback']
        for round_no in range(first_round, 2):
            remaining = (state.max_requests - state.usage()['requests']
                         if state.max_requests is not None else None)
            recorded_plan = any(c['name'] == f'plan_{round_no}' for c in state.data['calls'])
            recorded_search = any(c['name'] == f'search_{round_no}' for c in state.data['calls'])
            if (renders and remaining is not None and
                    remaining < (20 if active_finecut else 16 if semantic_audit else 12) and not recorded_plan):
                reservation = {'stage':'revision', 'action':'retain_actual_render',
                    'reason':'Insufficient requests for another watched window, plan and actual-render reviews.',
                    'remaining':remaining}
                if semantic_audit:
                    semantic_pipeline.record_budget_reservation(state,reservation)
                else:
                    state.set_artifact('budget_reservation',reservation)
                break
            window_cap = (precision.fine_window_budget(state,round_no,max_fine,len(windows),remaining)
                          if active_finecut else semantic_pipeline.fine_window_budget(state,round_no,max_fine,len(windows),remaining)
                          if semantic_audit else min(8, max_fine-len(windows),
                                                     max(0,(remaining-8)//2) if remaining is not None else 8))
            if recorded_search and not semantic_audit:
                # Previously submitted searches are recovered through the
                # original request/response, not charged or replanned. A lower
                # remaining budget cannot erase already completed evidence.
                window_cap = min(8,max_fine-len(windows))
            if window_cap == 0:
                if not windows:
                    raise LibraryStopped('budget_reserved_but_no_fine_window_possible')
                search = {'reason':'No remaining observation budget; plan from existing watched evidence.', 'windows':[]}
            else:
                search = None
            _status(output, 'requesting_fine_windows', round=round_no, requests=state.usage()['requests'])
            search_context = {'reference': reference_reading, 'catalog': compact_catalog,
                              'coarse_index': coarse, 'already_watched': [_window_context(w) for w in windows],
                              'remaining_window_budget': max_fine-len(windows),
                              'max_windows_this_round':window_cap, 'coarse_failures':coarse_failures,
                              'previous_review': last_review,
                              'instruction': '最多选择8个精看窗口；第二轮只补具体缺项；不要换参考。'}
            if editing_v2:
                search_context['editing_reference'] = editing_reference
            if search is None:
                search = glm.call(f'search_{round_no}', prompts.search_prompt(search_context), planning_image,
                                  lambda v: contracts.validate_search(v, sources, max_windows=window_cap), image=True)
            write_json(output / f'search_{round_no}.json', search)
            for requested in search['windows']:
                if len(windows) >= max_fine:
                    break
                source = source_map[requested['source_id']]
                key = json_sha({'source_sha256':source['sha256'], 'start':requested['start_s'], 'end':requested['end_s']})[:16]
                window_id = 'window_' + key
                if any(w['window_id'] == window_id for w in windows):
                    continue
                _status(output, 'fine_observation', window_id=window_id, question=requested['question'],
                        requests=state.usage()['requests'])
                window = prepare_window(source, requested['start_s'], requested['end_s'], cache, fps=12)
                window['window_id'] = window_id
                if editing_v2:
                    proxy_source = inventory_sources(window['path'],cache / ('shot_catalog_' + key))['sources'][0]
                    window['editing_timeline'] = {
                        'time_domain':'analysis_proxy_local_seconds',
                        'source_mapping':'Add source_start_s for a source-second estimate; not original-film native PTS.',
                        'proxy_fps':12,
                        'timeline':compact_timeline(detect_shot_timeline(proxy_source,cache / 'shot_timelines',threshold=3.0))}
                speech = transcript(source, requested['start_s'], requested['end_s'])
                window['asr'] = speech
                fine_context = {'reference':reference_reading, 'question': requested['question'],
                                'candidate_role_ids': requested['role_ids'], 'already_watched_roles':
                                [r for w in windows for r in w['observation']['roles']],
                                'asr_original_timestamps': _speech_context(speech),
                                'render_capabilities': ['source_audio','reference_audio','mix','fit','crop','grayscale']}
                if editing_v2:
                    fine_context['editing_reference'] = editing_reference
                    fine_context['render_capabilities'] += ['freeze_tail','static_caption']
                def validate_current_fine(value):
                    contracts.validate_fine(value,window)
                    if editing_v2:
                        validate_fine_editing(value,window,editing_reference)
                try:
                    observation = glm.call('fine_' + key, (prompts.editing_fine_prompt if editing_v2 else prompts.fine_prompt)(
                        _window_context(window, include_speech=False), fine_context), window['path'],
                                           validate_current_fine)
                except ValueError as error:
                    if not str(error).startswith('model_protocol_repair_exhausted:'):
                        raise
                    state.set_artifact('fine_protocol_failure', {'window_id':window_id,'error':str(error),
                        'status':'not_watched_no_EDL_permission'})
                    continue
                window.update(status='watched', observation=observation)
                windows.append(window)
                write_json(output / 'watched_windows.json', windows)
            _status(output, 'planning_slots_and_edit', round=round_no, requests=state.usage()['requests'])
            if not state.data['artifacts'].get('role_identity_policy'):
                state.set_artifact('role_identity_policy', {
                    'policy':'window_local_roles_with_model_owned_focus_bindings_v1',
                    'reason':'Observed role IDs collide across continuous windows; strings alone are not global identities.',
                    'scope':'New plans only; old raw observations remain unchanged.',
                    'human_identity_assignments':[]})
            context = {'reference':reference_reading, 'reference_duration_s':ref['duration_s'],
                       'reference_audio_stream_index':ref['audio_stream_index'], 'catalog':compact_catalog,
                       'watched_windows': [_window_context(w) for w in windows], 'previous_review':last_review,
                       'role_identity_scope':'每个fine的角色编号仅在(window_id, role_id)内有效；相同字母不能直接当作跨窗口同一人。焦点人物跨窗口对应必须由你根据实际外形/身份线索在focus_role_bindings中明确给出，不能引用外部剧情常识替代观察。',
                       'audio_semantics_policy':'参考音频中的具体人物和事实不能与新画面冲突；根据已提供ASR核验，冲突时采用其他可用音频模式并记录局限。',
                       'framing_behavior':{'fit':'Keep full frame with letterbox padding.',
                                           'crop':'Center crop only; no tracking or adaptive reframing.'},
                       'render_capabilities': {'speed':[0.5,2], 'max_duration_s':180, 'max_segments':32,
                            'audio_modes':['reference','source','mix','silent'],
                            'unsupported':['J/L_cut','audio_source_separation','synthetic_video']}}
            if editing_v2:
                context['editing_reference'] = editing_reference
                context['render_capabilities'].update(freeze_tail_s=[0,10],static_caption=True)
            if reference_seed is not None:
                context['reference_protocol_limit'] = reference_seed['evidence_limit']
            if semantic_audit:
                maximum_segments = (precision.plan_budget(state, round_no) if active_finecut
                                    else semantic_pipeline.plan_budget(state, round_no))
                context['render_capabilities']['max_segments'] = maximum_segments
            def validate_current_plan(value):
                contracts.validate_plan(value,sources,windows,ref['sha256'],ref['duration_s'],
                    reference_audio_stream_index=ref['audio_stream_index'],editing_reference=editing_reference)
                if editing_v2:
                    validate_candidate_dispositions(value,windows)
                    from .render import validate_caption_layout, compile_library_plan
                    compile_library_plan(sources,value,fps=value['fps'],width=value['width'],height=value['height'])
                    validate_caption_layout(value,value['width'],value['height'])
                if semantic_audit:
                    semantic_pipeline.validate_plan_claims(value,windows,maximum_segments)
            plan = glm.call(f'plan_{round_no}',
                            (semantic_prompts.plan_prompt if semantic_audit else
                             prompts.editing_plan_prompt if editing_v2 else prompts.plan_prompt)(context),
                            planning_image if reference_seed is not None else reference_media,
                            validate_current_plan, **({'image': True} if reference_seed is not None else {}))
            refinement = None
            if active_finecut:
                write_json(output / f'draft_plan_{round_no}.json', plan)
                draft = plan
                def validate_refinement(value):
                    precision.validate_refinement(value, draft, maximum_segments)
                    validate_current_plan(value['plan'])
                _status(output, 'active_finecut', round=round_no, requests=state.usage()['requests'])
                refinement = glm.call(f'finecut_{round_no}', precision.refinement_prompt(state, draft, context),
                                      planning_image if reference_seed is not None else reference_media,
                                      validate_refinement, **({'image': True} if reference_seed is not None else {}))
                write_json(output / f'finecut_{round_no}.json', refinement)
                plan = refinement['plan']
            write_json(output / f'plan_{round_no}.json', plan)
            plans[round_no] = plan
            slice_audit = None
            if semantic_audit:
                _status(output,'auditing_exact_slices',round=round_no,segments=len(plan['segments']),
                        requests=state.usage()['requests'])
                slice_audit = semantic_pipeline.observe_selected_slices(
                    glm,plan,source_map,windows,cache,output,round_no)
                if active_finecut:
                    slice_audit = precision.bind_draft_obligations(slice_audit, draft, refinement)
                    write_json(output / 'semantic_audit' / f'round_{round_no}' / 'manifest.json', slice_audit)
            from .render import render_library_video
            _status(output, 'rendering', round=round_no, segments=len(plan['segments']))
            rendered = render_library_video(sources, plan, output / f'render_{round_no}', reference_path=reference,
                                           fps=plan['fps'], width=plan['width'], height=plan['height'])
            output_media = prepare_window(inventory_sources(rendered['rendered_path'],
                output / f'render_catalog_{round_no}')['sources'][0], 0, rendered['measured_duration_s'], cache, fps=12)
            _status(output, 'blind_review', round=round_no, requests=state.usage()['requests'])
            blind = glm.call(f'blind_{round_no}',
                semantic_prompts.blind_prompt(rendered['measured_duration_s'],rendered['sha256'])
                if semantic_audit else prompts.blind_prompt(rendered['measured_duration_s']),
                output_media['path'], lambda v: semantic.validate_visual_blind(
                    v,rendered['measured_duration_s'],rendered['sha256']) if semantic_audit
                else contracts.validate_blind_reading(v,rendered['measured_duration_s']))
            write_json(output / f'blind_reading_{round_no}.json', blind)
            economy = None
            if active_finecut:
                manifest = precision.economy_manifest(plan, rendered)
                write_json(output / f'economy_manifest_{round_no}.json', manifest)
                _status(output, 'independent_economy_review', round=round_no, requests=state.usage()['requests'])
                economy = glm.call(f'economy_{round_no}', precision.economy_prompt(manifest, blind),
                    output_media['path'], lambda v: precision.validate_economy_review(v, manifest))
                write_json(output / f'economy_review_{round_no}.json', economy)
            review_context = {'reference':reference_reading, 'actual_render_sha256':rendered['sha256'],
                 'blind_reading':blind, 'plan':plan, 'provenance':rendered['provenance'],
                 'audio_review_limit':'GLM vision MCP has not heard actual output audio; preserve limitation.'}
            if semantic_audit:
                review_context.pop('plan')
                review_context.update(protocol=semantic.SEMANTIC_PROTOCOL,video_sha256=rendered['sha256'],
                    required_claims=slice_audit['required_claims'],
                    source_observations=slice_audit['observations'],segment_checks=slice_audit['segment_checks'])
            if editing_v2:
                actual_source = inventory_sources(rendered['rendered_path'],output / f'render_catalog_{round_no}')['sources'][0]
                actual_timeline = detect_shot_timeline(actual_source,cache / 'shot_timelines',threshold=3.0)
                boundary_associations = associate_edl_boundaries(actual_timeline,rendered)
                write_json(output / f'editing_evidence_{round_no}.json',boundary_associations)
                review_context.update(editing_reference=editing_reference,
                    output_duration_s=rendered['measured_duration_s'],
                    measured_output_timeline=compact_timeline(actual_timeline),
                    edl_boundary_associations=boundary_associations)
            def validate_current_review(value):
                contracts.validate_review(value,ref['sha256'])
                if editing_v2:
                    validate_method_review(value,editing_reference,rendered['measured_duration_s'])
                if semantic_audit:
                    semantic.validate_semantic_review(value,blind,slice_audit['observations'],
                        slice_audit['required_claims'],rendered['measured_duration_s'],rendered['sha256'],
                        segment_checks=slice_audit['segment_checks'])
            if reference_seed is not None:
                review_context['reference_protocol_limit'] = reference_seed['evidence_limit']
            review = glm.call(f'review_{round_no}',
                (semantic_prompts.review_prompt if semantic_audit else
                 prompts.editing_review_prompt if editing_v2 else prompts.review_prompt)(review_context),
                output_media['path'],validate_current_review)
            write_json(output / f'review_{round_no}.json', review)
            last_review = review
            if active_finecut:
                last_review = {**review, 'independent_economy_review': economy,
                               'unresolved_information_obligations': [c for c in refinement['obligation_coverage']
                                                                      if c['status'] == 'unresolved']}
            renders.append({'render':rendered, 'blind':blind, 'review':review, 'round':round_no,
                            **({'refinement':refinement, 'economy':economy} if active_finecut else {}),
                            **({'segment_checks':slice_audit['segment_checks'],
                                'expected_segment_ids':[s['segment_id'] for s in plan['segments']]}
                               if semantic_audit else {})})
            if ((semantic.semantic_review_passes(review,blind,slice_audit['segment_checks'],
                    expected_segment_ids=[s['segment_id'] for s in plan['segments']])
                    if semantic_audit else review['theme_status'] == 'pass' and review['continuity_status'] == 'pass')
                    and (not editing_v2 or review['editing_status'] == 'pass') and not review['revision_requests']
                    and (not active_finecut or precision.passes(refinement, economy))):
                break
        selected = renders[0]['round']
        if len(renders) > 1:
            choice_prompt = (prompts.BASE + '比较两个实际成片的盲读和审核记录，保留同主旨且连续性最好的有效版本。' +
                '只返回JSON {"selected_round":0,"reason":"依据及保留局限"}。' + json.dumps(
                    [{'round':r['round'],'blind':r['blind'],'review':r['review']} for r in renders],ensure_ascii=False))
            def validate_selection(value):
                if semantic_audit:
                    if active_finecut:
                        precision.validate_selection(value,renders)
                    else:
                        semantic.validate_semantic_selection(value,renders)
                    return
                if type(value.get('selected_round')) is not int or not 0 <= value['selected_round'] < len(renders) or not value.get('reason'):
                    raise ValueError('invalid_render_selection')
            selection_media = reference_media
            if semantic_audit:
                choice_prompt = semantic_prompts.selection_prompt([
                    {k:r[k] for k in ('round','blind','review','segment_checks','expected_segment_ids')} for r in renders])
                if active_finecut:
                    choice_prompt += '\n同时比较信息保留和独立精炼审核；不能用更短或技巧更多证明更好。'
                    choice_prompt += '若有联合通过候选必须从中选择；否则在limitations保留未解决的原表达义务和冗余问题：' + json.dumps([
                        {'round':r['round'],'obligation_coverage':r['refinement']['obligation_coverage'],
                         'economy':r['economy']} for r in renders],ensure_ascii=False)
                selection_media = output_media['path']
            decision = glm.call('select_render', choice_prompt, selection_media, validate_selection)
            selected = decision['selected_round']
            write_json(output / 'render_selection.json', decision)
        best = next(r for r in renders if r['round'] == selected)
        current_review_path = output / f'review_{selected}.json'
        prior_review_call = next(c for c in state.data['calls']
                                if c['name'] == f'review_{selected}' and not c.get('repair_of'))
        original_review_prompt = _read(output/'calls'/prior_review_call['id']/'request.json')['arguments']['prompt']
        if (not editing_v2 and state.data['artifacts'].get('editing_review_policy') and
                '故事段落顺序相似或slot数量相似' not in original_review_prompt):
            # Compare the selected film under the current task definition. Do not
            # turn a selection rationale into a pass or overwrite the old review.
            old_result = output / 'result.json'
            archive = output / 'result_before_selected_audit.json'
            if old_result.exists() and not archive.exists():
                archive.write_bytes(old_result.read_bytes())
            if not state.data['artifacts'].get('selected_audit_policy'):
                state.set_artifact('selected_audit_policy', {
                    'policy':'review_selected_actual_film_under_current_criteria_v2',
                    'reason':'Selected film was last compared before theme and editing criteria were clarified.',
                    'historical_review':str(current_review_path),'historical_review_sha256':sha256_file(current_review_path),
                    'selection_rationale_is_not_quality_verdict':True,
                    'input_and_budgets_unchanged':True,'no_extra_render':True})
            selected_source = inventory_sources(best['render']['rendered_path'],
                output / f'render_catalog_{selected}')['sources'][0]
            selected_media = prepare_window(selected_source,0,best['render']['measured_duration_s'],cache,fps=12)
            _status(output,'selected_quality_audit',selected_round=selected,requests=state.usage()['requests'])
            current_review = glm.call(f'selected_review_v2_{selected}', prompts.review_prompt({
                'reference':reference_reading,'actual_render_sha256':best['render']['sha256'],
                'blind_reading':best['blind'],'plan':plans[selected],
                'provenance':best['render']['provenance'],
                'audio_review_limit':'GLM vision MCP has not heard actual output audio; preserve limitation.'}),
                selected_media['path'],lambda v:contracts.validate_review(v,ref['sha256']))
            current_review_path = output / f'selected_review_v2_{selected}.json'
            write_json(current_review_path,current_review)
            best['review'] = current_review
        success = all(best['review'][key] == 'pass' for key in
                      ('theme_status','editing_status','continuity_status'))
        if semantic_audit:
            success = semantic.semantic_review_passes(best['review'],best['blind'],best['segment_checks'],
                expected_segment_ids=best['expected_segment_ids'])
        if active_finecut:
            success = success and precision.passes(best['refinement'],best['economy'])
        result = {'status':'model_checked_library_candidate' if success else 'library_candidate_with_limitations',
                  'final_video':best['render']['rendered_path'], 'final_sha256':best['render']['sha256'],
                  'reference_sha256':ref['sha256'], 'selected_round':selected, 'review':best['review'],
                  'selected_review_path':str(current_review_path),
                  'usage':state.usage(), 'actual_fine_windows':len(windows), 'coarse_pages':len(coarse),
                  'human_creative_inputs':[], 'source_generation_requests':0,
                  'evidence_limit':'Model review is not human truth; audio rhythm remains unverified by vision MCP.'}
        if editing_v2:
            result['editing_protocol'] = prompts.EDITING_PROTOCOL
            result['editing_evidence_path'] = str(output / f'editing_evidence_{selected}.json')
        if semantic_audit:
            result['semantic_protocol'] = semantic.SEMANTIC_PROTOCOL
            result['semantic_evidence_path'] = str(output / 'semantic_audit' / f'round_{selected}' / 'manifest.json')
            result['semantic_gate_passed'] = success
        if active_finecut:
            result.update(active_finecut_protocol=precision.POLICY, active_finecut_gate_passed=success,
                refinement_path=str(output / f'finecut_{selected}.json'),
                economy_review_path=str(output / f'economy_review_{selected}.json'))
        if reference_seed is not None:
            result['reference_navigation_limit'] = reference_seed['evidence_limit']
            result['reference_seed_sha256'] = json_sha(reference_seed)
            result['reference_observation_reused_no_new_reference_request'] = True
        write_json(output / 'result.json', result)
        _status(output, 'completed', **result)
        return result
    except Exception as error:
        failure = {'error':str(error), 'type':type(error).__name__,
                   'traceback':traceback.format_exc(), 'usage':state.usage(), 'no_automatic_paid_replay':True}
        state.set_artifact('failure', failure)
        write_json(output / failure_report_name, failure)
        _status(output, 'stopped', error=str(error), requests=state.usage()['requests'])
        raise
