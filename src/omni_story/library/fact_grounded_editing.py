"""One forward, model-owned reconstruction from immutable observed footage.

Old paid contracts and failed results are not revalidated as successes here.
Source facts precede creative plans; source checks and output operations have
different scopes. Importing this module does not authorize execution.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json

from ..contract import ids, number, require, rows, text
from . import contracts, semantic_audit as audit, semantic_prompts
from .active_finecut import economy_manifest, economy_prompt, validate_economy_review
from .media import inventory_sources, sha256_file
from .slot_finecut import prepare_window
from .pipeline import CodexMCP
from .render import render_library_video as render_plan
from .slot_finecut_baselines import load_preparation
from .state import LibraryStopped, json_sha, write_json


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _finish(state, parent_round, payload):
    result = {'baseline_id':f'render_{parent_round}',**payload}
    return _read(state.finish(parent_round,result))


def received_facts(output, catalog, *, diagnostics=None):
    """Read only valid, received neutral observations; never normalize failures."""
    state = _read(output / 'library_state.json')
    sources = {s['source_id']: s for s in catalog['sources']}
    found = []
    for call in state['calls']:
        if call['status'] != 'received' or not ('semantic_slice_' in call['name']):
            continue
        folder = output / 'calls' / call['id']
        parsed = folder / 'parsed.json'
        if not parsed.exists():
            continue
        request, response, observation = _read(folder/'request.json'), _read(folder/'response.json'), _read(parsed)
        require(json_sha(request) == call['request_sha256'] and json_sha(response) == call['response_sha256'],
                'fact_grounded:received_cache_changed')
        model_text = '\n'.join(c['text'] for c in response['result']['content'] if c.get('type')=='text')
        require(contracts.parse_model_json(model_text)==observation,'fact_grounded:parsed_fact_not_original_model_body')
        source = sources.get(observation.get('source_id'))
        if source is None or observation.get('source_sha256') != source['sha256']:
            continue
        scope = request.get('observation_scope', {})
        if not all(scope.get(k) == v for k, v in (
                ('kind','continuous_window'), ('source_sha256',source['sha256']),
                ('source_start_s',observation['source_in_s']), ('source_end_s',observation['source_out_s']))):
            continue
        proxy = prepare_window(source, observation['source_in_s'], observation['source_out_s'], output/'media_cache', fps=30)
        if observation.get('proxy_sha256') != proxy['sha256']:
            continue
        segment = {'segment_id': observation['segment_id'], 'source_id': source['source_id'],
                   'source_in_s': observation['source_in_s'], 'source_out_s': observation['source_out_s']}
        try:
            audit.validate_segment_observation(observation, segment, source['sha256'], proxy)
        except (ValueError,TypeError,KeyError) as error:
            # An old parsed file need not satisfy this forward neutral contract.
            # Keep it unchanged and explicitly decline cache admission.
            if diagnostics is not None:
                diagnostics.append({'call_id':call['id'],'status':'ineligible_forward_cache',
                    'error':str(error),'original_parsed_file_sha256':sha256_file(parsed)})
            continue
        found.append({'observation': observation, 'origin_call_id': call['id'],
            'origin_request_sha256':call['request_sha256'], 'origin_response_sha256':call['response_sha256'],
            'origin_parsed_file_sha256':sha256_file(parsed)})
    return found


def _bind(segment, item):
    observation = item['observation']
    return {'segment_id': segment['segment_id'], 'observation_sha256': json_sha(observation),
            **{k:observation[k] for k in ('source_id','source_sha256','source_in_s','source_out_s','proxy_sha256')},
            **{k:item[k] for k in ('origin_call_id','origin_request_sha256','origin_response_sha256','origin_parsed_file_sha256')}}


def observe_sources(glm, state, parent, plan, catalog, cached, folder):
    sources = {s['source_id']:s for s in catalog['sources']}
    observations, bindings, envelopes = [], [], []
    for segment in plan['segments']:
        source = sources[segment['source_id']]
        proxy = prepare_window(source,segment['source_in_s'],segment['source_out_s'],state.output/'media_cache',fps=30)
        matching = next((item for item in cached if all(item['observation'].get(k) == v for k,v in (
            ('source_id',segment['source_id']), ('source_sha256',source['sha256']),
            ('source_in_s',segment['source_in_s']), ('source_out_s',segment['source_out_s']),
            ('proxy_sha256',proxy['sha256'])))), None)
        if matching is None:
            key = json_sha([segment['segment_id'],source['sha256'],segment['source_in_s'],segment['source_out_s']])[:16]
            name = f'sfv2_{parent["round"]}_slice_{key}'
            observation = glm.call(name, semantic_prompts.explicit_slice_observation_prompt(segment,source,proxy),
                proxy['path'], lambda v,s=segment,src=source,p=proxy:audit.validate_segment_observation(v,s,src['sha256'],p),
                scope={k:proxy[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
            state._reload()
            call = next(c for c in reversed(state.data['calls']) if c['name'] in (name,name+'_repair') and
                        (state.output/'calls'/c['id']/'parsed.json').exists())
            matching = {'observation':observation,'origin_call_id':call['id'],
                'origin_request_sha256':call['request_sha256'],'origin_response_sha256':call['response_sha256'],
                'origin_parsed_file_sha256':sha256_file(state.output/'calls'/call['id']/'parsed.json')}
        observation = matching['observation']
        binding = _bind(segment,matching)
        observations.append(observation)
        bindings.append(binding)
        envelopes.append({'segment_id':segment['segment_id'],'observation':observation,
                          'observation_sha256':json_sha(observation)})
    write_json(folder/'source_observation_bindings.json',{'observations':observations,'bindings':bindings})
    return observations, bindings, envelopes


def evidence_blockers(reconstruction, observations, comparison):
    """Measure a shared information obligation once on the actual output axis."""
    from .fact_grounded_edit_plan import proposed_group_exposure
    by_obs = {row['segment_id']:row['observation'] for row in observations}
    by_check = {row['segment_id']:row for row in comparison['segments']}
    blockers = []
    for segment_id, report in by_check.items():
        for claim in report['claim_checks']:
            if claim['status'] != 'supported':
                blockers.append({'segment_id':segment_id,'claim_id':claim['claim_id'],
                    'kind':'missing_source_support','status':claim['status'],'reason':claim['reason']})
    # Source intervals reported by the model stay literal; absent coverage is
    # evidence insufficiency, not an assertion that footage does not exist.
    for group in reconstruction['essential_groups']:
        observed = []
        for exposure in group['exposures']:
            sid = exposure['segment_id']
            observation = by_obs[sid]
            checks = {c['claim_id']:c for c in by_check[sid]['claim_checks']}
            facts = {e['evidence_id']:e for e in observation['evidence']}
            for cid in exposure['source_claim_ids']:
                check = checks[cid]
                if check['status'] != 'supported':
                    continue
                for eid in check['evidence_ids']:
                    fact = facts[eid]
                    if fact['kind'] not in audit.VISUAL_KINDS:
                        continue
                    a = max(exposure['source_start_s'], observation['source_in_s']+fact['local_start_s'])
                    b = min(exposure['source_end_s'], observation['source_in_s']+fact['local_end_s'])
                    if a < b:
                        observed.append({**exposure,'source_start_s':a,'source_end_s':b,
                            'continues_in_tail_frame':exposure['continues_in_tail_frame'] and
                            abs(b-observation['source_out_s']) < .001})
        actual = proposed_group_exposure(reconstruction['plan'], {'exposures':observed})
        if actual + .001 < group['min_readable_s']:
            blockers.append({'group_id':group['group_id'],'kind':'insufficient_observation_exposure',
                'recorded_exposure_s':actual,'model_minimum_s':group['min_readable_s'],
                'reason':'Recorded direct visual coverage is insufficient; actual visibility has not been disproved.'})
    return blockers


def required_output_claims(reconstruction):
    from .scoped_edit_evidence import scoped_output_claims
    claims = scoped_output_claims(reconstruction['plan'], reconstruction['plan']['slots'])
    claims += [{'claim_id':'output_'+g['group_id'],'kind':'information_takeaway','scope':'actual_output',
        'owner_id':g['slot_id'],'description':g['information'],'min_readable_s':g['min_readable_s']}
        for g in reconstruction['essential_groups']]
    ids(claims,'claim_id','fact_grounded/output_claims')
    return claims


def output_review_prompt(parent, reconstruction, reference, methods, blind, rendered):
    claims = required_output_claims(reconstruction)
    template = {'protocol':'scoped_output_review_v1','video_sha256':rendered['sha256'],
        'theme_status':'partial','editing_status':'partial','continuity_status':'partial',
        'claim_checks':[{'claim_id':c['claim_id'],'status':'unverifiable','reason':'实际画面理由',
            'evidence_ids':[],'limitations':['未核验原因'],
            **({'executed_value':c['expected_value']} if c['kind'] in {'output_speed','output_tail_hold'} else {})} for c in claims],
        'contradictions':[], 'limitations':[]}
    return ('只返回一个完整JSON对象。审核实际剪辑输出，不以计划文字替代画面证据。'
        '保留原slot主旨，source事实只用于核对素材；慢放、尾停、字幕及跨镜叙事在这里检查。'
        'evidence_ids必须引用独立blind_reading证据；不能用字幕或推断证明画面动作。'
        '当独立盲读与目标解释不一致时如实记录contradictions，不给虚假pass。'
        '音乐未听到/未验证不能自动否定画面叙事，但不得声称节奏卡点已通过。\n'+json.dumps({
            'reference':reference['cached_reading'],'reference_methods':methods,
            'original_slot_obligations':reconstruction['plan']['slots'], 'required_claims':claims,
            'blind_reading':blind,'actual_duration_s':rendered['measured_duration_s'],
            'actual_provenance':rendered['provenance'],'response_contract':template,
            'allowed_statuses':['pass','partial','fail','unverifiable'],
            'allowed_claim_statuses':['supported','partial','unsupported','unverifiable']},ensure_ascii=False))


def validate_output_review(value, reconstruction, blind, rendered):
    require(value.get('protocol')=='scoped_output_review_v1' and value.get('video_sha256')==rendered['sha256'],
            'fact_grounded:actual_review_binding')
    for field in ('theme_status','editing_status','continuity_status'):
        require(value.get(field) in audit.REVIEW_STATUSES,'fact_grounded:review_status')
    claims = required_output_claims(reconstruction)
    by_id = {e['evidence_id']:e for e in blind['evidence']}
    def lookup(check):
        require(isinstance(check.get('evidence_ids'),list) and all(e in by_id for e in check['evidence_ids']),
                'fact_grounded:review_unknown_evidence')
        return [by_id[e] for e in check['evidence_ids']]
    audit._checks(value.get('claim_checks'),claims,lookup)
    checks = {row['claim_id']:row for row in value['claim_checks']}
    segments = {segment['segment_id']:actual for segment,actual in
                zip(reconstruction['plan']['segments'], rendered['provenance'], strict=True)}
    plan_segments = {s['segment_id']:s for s in reconstruction['plan']['segments']}
    for claim in claims:
        check = checks[claim['claim_id']]
        if check['status'] != 'supported':
            continue
        if claim['kind'] in {'output_speed','output_tail_hold'}:
            require(check.get('executed_value')==claim['expected_value'],
                    'fact_grounded:operation_execution_value_mismatch')
            actual = segments[claim['segment_id']]
            a,b = actual['output_in_s'],actual['output_out_s']
            if claim['kind']=='output_tail_hold':
                fps = reconstruction['plan']['fps']
                hold = round(plan_segments[claim['segment_id']]['freeze_tail_s']*fps)/fps
                a = b-hold
            require(any(e['kind'] in audit.VISUAL_KINDS and
                        max(a,e['start_s']) < min(b,e['end_s']) for e in lookup(check)),
                    'fact_grounded:operation_needs_own_actual_output_evidence')
    for field in ('contradictions','limitations'):
        for item in rows(value.get(field),'fact_grounded/'+field,nonempty=False):
            text(item,'fact_grounded/'+field)
    if all(value[field]=='pass' for field in ('theme_status','editing_status','continuity_status')):
        require(not value['contradictions'],'fact_grounded:pass_with_unresolved_contradiction')
    require(value['theme_status']!='pass' or blind['text_dependency'] not in {'essential','unverifiable'},
            'fact_grounded:visual_theme_pass_with_unresolved_text_dependency')
    return value


def _parent(glm,state,preparation,parent,old_folder,folder,methods,knowledge,cached):
    from . import fact_grounded_edit_plan as planning, scoped_edit_evidence as scoped
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'result.json').exists():
        return _finish(state,parent['round'],_read(folder/'result.json'))
    outline, proposals = _read(old_folder/'outline.json'), _read(old_folder/'proposals.json')
    prior_file = old_folder/('assembly.json' if parent['round']==0 else 'assembly_before_source_feedback.json')
    prior = _read(prior_file)
    original = planning.canonical_information_groups(outline,proposals,prior_assembly=prior)
    catalog = _read(state.output/'catalog/inventory.json')
    windows = parent['allowed_windows']
    auth = state.authorization
    blocked = auth['exhausted_source_inputs'] + auth['unknown_inputs']
    source = inventory_sources(parent['path'],folder/'parent_catalog')['sources'][0]
    # A genuinely cropped, known-input parent excerpt guides this NEW task.
    # The facts ledger supplies library content; the full unknown parent is absent.
    last = outline['slots'][-1]
    proxy = prepare_window(source,last['start_s'],last['end_s'],state.output/'media_cache',fps=30)
    relevant_facts = [c['observation'] for c in cached if any(
        w['source_id']==c['observation']['source_id'] and w['source_start_s'] <= c['observation']['source_in_s'] and
        c['observation']['source_out_s'] <= w['source_end_s'] for w in windows)]
    prompt = planning.reconstruction_prompt(parent,outline,catalog,windows,preparation['reference'],methods,
        original,relevant_facts,prior_assembly=prior,blocked_scopes=blocked,knowledge=knowledge)
    prompt += '\n'+json.dumps({'planning_media_parent_start_s':last['start_s'],
        'planning_media_parent_end_s':last['end_s'],
        'media_role':'Only one parent excerpt; NOT a new neutral observation or whole-parent viewing.',
        'facts_first':'Use bound observations and watched-window context; do not normalize failed old replies.'},ensure_ascii=False)
    reconstruction = glm.call(f'sfv2_{parent["round"]}_reconstruct',prompt,proxy['path'],
        lambda v:planning.validate_reconstruction(v,parent,outline,catalog,windows,preparation['reference'],methods,
            original,blocked_scopes=blocked,prior_assembly=prior),
        scope={k:proxy[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
    write_json(folder/'reconstruction.json',reconstruction)
    if reconstruction['status']=='unavailable':
        return _finish(state,parent['round'],{'status':'stopped_unavailable','limitations':reconstruction['limitations'],
                                            'model_quality_gate_passed':False})
    plan = reconstruction['plan']
    state.assert_source_inputs(plan,catalog)
    observations,bindings,envelopes = observe_sources(glm,state,parent,plan,catalog,cached,folder)
    claims = {s['segment_id']:scoped.scoped_segment_claims(plan,s) for s in plan['segments']}
    context = {s['segment_id']:scoped.window_identity_context(plan,s) for s in plan['segments']}
    # This is a new scoped task, not a third attempt of an exhausted old comparison.
    comparison = glm.call(f'sfv2_{parent["round"]}_compare',
        scoped.comparison_prompt(envelopes,claims,context,plan_sha256=json_sha(plan)),proxy['path'],
        lambda v:scoped.validate_batch_comparison(v,envelopes,claims,plan_sha256=json_sha(plan)),
        scope={k:proxy[k] for k in ('kind','source_sha256','source_start_s','source_end_s')})
    state._reload()
    compare_call = next(c for c in reversed(state.data['calls']) if c['name'] in
        (f'sfv2_{parent["round"]}_compare',f'sfv2_{parent["round"]}_compare_repair') and
        (state.output/'calls'/c['id']/'parsed.json').exists())
    manifest = {'protocol':scoped.PROTOCOL,'plan_sha256':json_sha(plan),
        'observations':observations,'observation_bindings':bindings,'comparison':comparison,
        'compare_call_id':compare_call['id'],'blockers':evidence_blockers(reconstruction,envelopes,comparison)}
    write_json(folder/'source_manifest.json',manifest)
    if manifest['blockers']:
        return _finish(state,parent['round'],{'status':'stopped_source_evidence','model_quality_gate_passed':False,
            'blockers':manifest['blockers'],'limitations':['Source support or recorded exposure is insufficient; no automatic replan.']})
    directory = state.claim_render(parent['round'],plan,manifest)
    reference = preparation['reference']
    rendered = render_plan(catalog,plan,directory,reference_path=reference['path'],
                          fps=plan['fps'],width=plan['width'],height=plan['height'])
    actual = inventory_sources(rendered['rendered_path'],folder/'output_catalog')['sources'][0]
    output = prepare_window(actual,0,rendered['measured_duration_s'],state.output/'media_cache',fps=30)
    blind_prompt = semantic_prompts.blind_prompt(rendered['measured_duration_s'],rendered['sha256']) + \
        '\nconfusions只记录画面叙事、人物或因果不清；没有听音频本身不是视觉叙事困惑，音乐节奏另标未核验。'
    blind = glm.call(f'sfv2_{parent["round"]}_blind',blind_prompt,
        output['path'],lambda v:audit.validate_visual_blind(v,rendered['measured_duration_s'],rendered['sha256']))
    write_json(folder/'blind_reading.json',blind)
    economy_input = economy_manifest(plan,rendered)
    prompt = economy_prompt(economy_input,blind).replace('pass/partial/fail','partial').replace('necessary/redundant/uncertain','uncertain')
    prompt += '\n枚举分别从pass,partial,fail及necessary,redundant,uncertain选一个；示例不是固定答案。'
    economy = glm.call(f'sfv2_{parent["round"]}_economy',prompt,output['path'],lambda v:validate_economy_review(v,economy_input))
    write_json(folder/'economy_review.json',economy)
    review = glm.call(f'sfv2_{parent["round"]}_review',output_review_prompt(parent,reconstruction,reference,methods,blind,rendered),
        output['path'],lambda v:validate_output_review(v,reconstruction,blind,rendered))
    write_json(folder/'review.json',review)
    passed = (all(review[f]=='pass' for f in ('theme_status','editing_status','continuity_status')) and
        all(c['status']=='supported' for c in review['claim_checks']) and
        economy['economy_status']==economy['narrative_readability']=='pass' and
        blind['text_dependency'] not in {'essential','unverifiable'} and not blind['confusions'] and not review['contradictions'])
    result = _finish(state,parent['round'],{'status':'model_checked_candidate' if passed else 'candidate_with_limitations',
        'final_video':rendered['rendered_path'],'final_sha256':rendered['sha256'],
        'measured_duration_s':rendered['measured_duration_s'],'model_quality_gate_passed':passed,
        'limitations':reconstruction['limitations']+review['limitations']+economy['limitations']})
    print(json.dumps({'final_video':rendered['rendered_path'],'folder':str(Path(rendered['rendered_path']).parent)},ensure_ascii=False),flush=True)
    return result


def execute(output):
    from .forward_slot_budget import ForwardSlotState
    state = ForwardSlotState(output)
    auth = state.authorization
    preparation = load_preparation(auth['preparation_path'])
    catalog = _read(state.output/'catalog/inventory.json')
    diagnostics = []
    cached = received_facts(state.output,catalog,diagnostics=diagnostics)
    admissibility = {'eligible_neutral_observations':len(cached),'declined_historical_observations':diagnostics,
        'historical_records_modified':False}
    admission_path = Path(auth['execution_directory'])/'cache_admissibility.json'
    if admission_path.exists():
        # Completed routes may add new neutral facts; that does not replace the
        # first cache-admission snapshot or change historical evidence.
        pass
    else:
        write_json(admission_path,admissibility)
    methods = _read(auth['reference_methods_path'])
    knowledge = Path(auth['knowledge_path']).read_text(encoding='utf-8')
    old = Path(auth['old_execution_directory'])
    directory = Path(auth['execution_directory'])
    lanes = {p['round']:(ForwardSlotState(output),None if (directory/p['baseline_id']/'result.json').exists()
                       else CodexMCP(ForwardSlotState(output))) for p in preparation['parents']}
    def run(parent):
        lane,glm = lanes[parent['round']]
        try:
            return _parent(glm,lane,preparation,parent,old/parent['baseline_id'],directory/parent['baseline_id'],methods,knowledge,cached)
        except (ValueError,RuntimeError,OSError) as error:
            lane._reload()
            if any(c['status']=='submitted' and c['name'].startswith(f'sfv2_{parent["round"]}_') for c in lane.data['calls']):
                raise
            values = {'status':'stopped_protocol_failure' if str(error).startswith('model_protocol_repair_exhausted:') else 'stopped_execution',
                'error':str(error),'model_quality_gate_passed':False,'limitations':['Known failure retained; no automatic repetition.']}
            movie = directory/parent['baseline_id']/'render/render_result.json'
            if movie.exists():
                actual = _read(movie)
                values.update(final_video=actual['rendered_path'],final_sha256=actual['sha256'],review_status='incomplete')
            return _finish(lane,parent['round'],values)
    results = []
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='fact_grounded') as pool:
        jobs = [pool.submit(run,p) for p in preparation['parents']]
        for job in as_completed(jobs):
            results.append(job.result())
    state.assert_protected()
    record = {'protocol':'fact_grounded_editing_v1','results':sorted(results,key=lambda v:v['baseline_id']),
              'requests_total':len(state.data['calls']),'quality_limit':'Model reviews are not human viewer truth.'}
    path = directory/'comparison.json'
    if path.exists():
        old_record = _read(path)
        require(old_record['results']==record['results'],'fact_grounded:cached_comparison_changed')
        return old_record
    write_json(path,record)
    return record
