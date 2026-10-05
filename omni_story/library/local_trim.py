"""One forward local deletion experiment, using only model-owned parent ranges."""
from copy import deepcopy
from pathlib import Path
import json
import math

from ..contract import require
from .media import prepare_window, sha256_file
from .pipeline import _read
from .state import json_sha

ARTIFACT = 'goal_research_local_8'
POLICY = 'local_counterfactual_trim_v1'
CARD = Path(__file__).with_name('craft_knowledge') / 'LOCAL_TRIM.md'


def verify(state):
    entries = state.data['artifacts'].get(ARTIFACT, [])
    if not entries:
        return None
    require(len(entries) == 1, 'local:one_strategy_required')
    record = _read(entries[0]['path'])
    require(json_sha(record) == entries[0]['sha256'] and record['policy'] == POLICY
            and record['round'] == 8 and record['input_lock_sha256'] == json_sha(state.data['input_lock']),
            'local:strategy_changed')
    require(record['additional_stage_pattern'] == r'^active_8_trim_[a-f0-9]{16}(?:_repair)?$'
            and record['one_local_proposal_per_parent'] is True and record['repairs_per_stage'] == 1
            and record['new_unique_windows'] == 0
            and record['explicit_claim_protocol'] == 'explicit_slice_claim_check_v1', 'local:scope_changed')
    path = Path(record['knowledge_path']).resolve(strict=True)
    require(path.is_relative_to(state.output) and sha256_file(path) == record['knowledge_sha256'],
            'local:knowledge_changed')
    call = next(c for c in state.data['calls'] if c['id'] == record['source_draft_call'])
    require(call['status'] == 'received' and call['name'] in {'active_7_draft', 'active_7_draft_repair'}
            and call['request_sha256'] == record['source_request_sha256']
            and call['response_sha256'] == record['source_response_sha256'], 'local:draft_call_changed')
    from .goal_budget import _call_value
    _, draft = _call_value(state.output, call)
    require(json_sha(draft) == record['source_draft_sha256'], 'local:source_draft_changed')
    parents = record['parent_inputs']
    require(len(parents) == len(draft['segments']) and len({p['stage'] for p in parents}) == len(parents),
            'local:parent_inputs_missing_duplicate')
    for bound, parent in zip(parents,draft['segments']):
        lineage_path = Path(bound['lineage_path']).resolve(strict=True)
        media = _read(lineage_path)
        require(lineage_path.is_relative_to(state.output) and sha256_file(lineage_path) == bound['lineage_sha256']
                and bound['parent_segment_id'] == parent['segment_id'] and bound['parent_sha256'] == json_sha(parent)
                and media['sha256'] == bound['media_sha256'] == sha256_file(bound['media_path'])
                and media['path'] == bound['media_path'] and media['source_id'] == parent['source_id']
                and media['source_sha256'] == next(s['sha256'] for s in state.data['input_lock']['library_sources']
                                                   if s['source_id'] == parent['source_id'])
                and media['source_start_s'] == parent['source_in_s'] and media['source_end_s'] == parent['source_out_s']
                and media['source_offset_s'] == parent['source_in_s']
                and media['kind'] == 'continuous_window' and media['spec']['fps'] == 30
                and bound['stage'] == 'active_8_trim_'+json_sha({'parent':parent,'proxy_sha256':media['sha256']})[:16],
                'local:parent_media_binding_changed')
    return record


def enable(output, instruction):
    from .goal_budget import stage_state, get_authorization
    state = stage_state(output)
    if state.data['artifacts'].get(ARTIFACT):
        return verify(state)
    require(isinstance(instruction, str) and instruction.strip(), 'local:instruction_required')
    grant = get_authorization(state)
    require(not any(c['status'] in {'submitted', 'uncertain'}
                    for c in state.data['calls'][grant['baseline_request_count']:]), 'local:unsettled_call')
    require(not any(c['name'].startswith('active_8_') for c in state.data['calls']), 'local:paid_round_changed')
    require(_read(state.output/'result_goal_feedback_7.json')['new_renders'] == 0,
            'local:expected_known_pre_render_stop')
    calls = [c for c in state.data['calls'] if c['name'] in {'active_7_draft', 'active_7_draft_repair'}
             and c['status'] == 'received' and (state.output/'calls'/c['id']/'parsed.json').is_file()]
    require(bool(calls), 'local:valid_model_draft_required')
    from .goal_budget import _call_value
    call = calls[-1]
    _, draft = _call_value(state.output, call)
    sources={s['source_id']:s for s in _read(state.output/'catalog/inventory.json')['sources']}
    parents=[]
    for parent in draft['segments']:
        media=prepare_window(sources[parent['source_id']],parent['source_in_s'],parent['source_out_s'],state.output/'media_cache',fps=30)
        lineage=Path(media['path']).parent/'lineage.json'
        parents.append({'parent_segment_id':parent['segment_id'],'parent_sha256':json_sha(parent),
            'media_path':media['path'],'media_sha256':media['sha256'],'lineage_path':str(lineage),
            'lineage_sha256':sha256_file(lineage),
            'stage':'active_8_trim_'+json_sha({'parent':parent,'proxy_sha256':media['sha256']})[:16]})
    folder = state.output/'artifacts'/POLICY
    folder.mkdir(exist_ok=False)
    card = folder/CARD.name
    card.write_bytes(CARD.read_bytes())
    state.set_artifact(ARTIFACT, {'policy': POLICY, 'round': 8, 'user_instruction': instruction,
        'activation_baseline_requests': state.data['request_count'],
        'input_lock_sha256': json_sha(state.data['input_lock']),
        'source_draft_call': call['id'], 'source_request_sha256': call['request_sha256'],
        'source_response_sha256': call['response_sha256'], 'source_draft_sha256': json_sha(draft),
        'parent_inputs':parents,
        'knowledge_path': str(card), 'knowledge_sha256': sha256_file(card),
        'additional_stage_pattern': r'^active_8_trim_[a-f0-9]{16}(?:_repair)?$',
        'draft_reuse': 'Exact received model draft, not a new model reply or retroactive round8 call.',
        'one_local_proposal_per_parent': True, 'repairs_per_stage': 1,
        'old_replies_unchanged': True, 'new_unique_windows': 0,
        'explicit_claim_protocol': 'explicit_slice_claim_check_v1'})
    return verify(state)


def draft(state, round_no):
    record = verify(state)
    if not record or round_no != 8:
        return None
    from .goal_budget import _call_value
    call = next(c for c in state.data['calls'] if c['id'] == record['source_draft_call'])
    return deepcopy(_call_value(state.output, call)[1])


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate(value, parent, max_cuts):
    require(isinstance(value, dict) and value.get('parent_segment_id') == parent['segment_id'], 'local:parent_id')
    for field in ('limitations', 'uncertainties'):
        require(isinstance(value.get(field), list) and all(isinstance(x, str) for x in value[field]),
                'local:'+field+'_required')
    require(value.get('obligation_status') in {'preserved', 'unresolved'}, 'local:obligation_status')
    cuts = value.get('kept_slices')
    require(isinstance(cuts, list) and len(cuts) <= max_cuts, 'local:cuts_required')
    require(value['obligation_status'] != 'preserved' or bool(cuts), 'local:preserved_without_picture')
    for cut in cuts:
        require(isinstance(cut, dict) and all(_number(cut.get(k)) for k in
                ('source_in_s', 'source_out_s', 'speed', 'freeze_tail_s')), 'local:cut_numbers')
        require(parent['source_in_s'] <= cut['source_in_s'] < cut['source_out_s'] <= parent['source_out_s']
                and .5 <= cut['speed'] <= 2 and 0 <= cut['freeze_tail_s'] <= 10, 'local:cut_range_transform')
        for key in ('visible_information', 'reason'):
            require(isinstance(cut.get(key), str) and cut[key].strip(), 'local:'+key+'_required')
        essential = cut.get('essential_interval')
        require(isinstance(essential, dict) and all(_number(essential.get(k))
                for k in ('source_start_s', 'source_end_s', 'min_readable_s')), 'local:essential_numbers')
        require(cut['source_in_s'] <= essential['source_start_s'] < essential['source_end_s'] <= cut['source_out_s']
                and essential['min_readable_s'] > 0, 'local:essential_range')
        exposure = (essential['source_end_s'] - essential['source_start_s'])/cut['speed']
        if essential['source_end_s'] == cut['source_out_s']:
            exposure += cut['freeze_tail_s']
        require(exposure+.001 >= essential['min_readable_s'], 'local:insufficient_exposure')
    partitions = value.get('deletion_checks')
    require(isinstance(partitions, list) and bool(partitions), 'local:deletion_checks_required')
    require(all(isinstance(p,dict) and all(_number(p.get(k)) for k in ('source_start_s','source_end_s'))
                for p in partitions),'local:deletion_interval_numbers')
    cursor = parent['source_in_s']
    for interval in sorted(partitions, key=lambda p:p.get('source_start_s', -1)):
        require(isinstance(interval, dict) and all(_number(interval.get(k)) for k in ('source_start_s','source_end_s'))
                and abs(interval['source_start_s']-cursor) <= .001
                and cursor < interval['source_end_s'] <= parent['source_out_s'], 'local:partition_gap_or_overlap')
        require(interval.get('decision') in {'keep','omit'} and isinstance(interval.get('information_lost'), str)
                and isinstance(interval.get('reason'), str) and interval['reason'].strip(), 'local:deletion_explanation')
        matching = [c for c in cuts if c['source_in_s'] < interval['source_end_s']-.001
                    and c['source_out_s'] > interval['source_start_s']+.001]
        require((interval['decision'] == 'keep') == bool(matching), 'local:deletion_cut_conflict')
        if matching:
            covered = interval['source_start_s']
            for c in sorted(matching,key=lambda c:c['source_in_s']):
                require(c['source_in_s'] <= covered+.001, 'local:kept_interval_unselected_gap')
                covered = max(covered,c['source_out_s'])
            require(covered >= interval['source_end_s']-.001, 'local:kept_interval_unselected_tail')
        cursor = interval['source_end_s']
    require(abs(cursor-parent['source_out_s']) <= .001, 'local:partition_tail_missing')
    return value


def proposals(state, glm, original, windows, catalog, context, round_no):
    if round_no != 8 or not verify(state):
        return None
    record = verify(state)
    saved=state.data['artifacts'].get('goal_research_local_proposals_8',[])
    if saved:
        require(len(saved)==1,'local:multiple_proposals')
        value=_read(saved[0]['path'])
        require(json_sha(value)==saved[0]['sha256'],'local:proposals_changed')
        return value['rows']
    knowledge = Path(record['knowledge_path']).read_text(encoding='utf-8')
    result = []
    slots = {s['slot_id']:s for s in original['slots']}
    candidates = {r['window_id']:r for r in context.get('source_cut_navigation',{}).get('records',[])}
    for index,parent in enumerate(original['segments']):
        bound=record['parent_inputs'][index]
        proxy = _read(bound['lineage_path'])
        packet = {'parent_segment':parent, 'original_slot_obligation':slots[parent['slot_id']],
            'neighbor_context': [{'segment_id':p['segment_id'],'slot_id':p['slot_id'],
                'proposed_visual_claims':p['visual_claims'],
                'original_slot_obligation':slots[p['slot_id']]['intended_takeaway']}
                for p in original['segments'][max(0,index-1):index+2]
                if p['segment_id'] != parent['segment_id']],
            'reference_duration_s':context['reference_duration_s'],
            'local_reference_intent':context['editing_reference'],
            'source_video_binding':{k:proxy[k] for k in ('source_sha256','source_start_s','source_end_s','source_offset_s','duration_s','sha256')},
            'visual_change_candidates': [r for r in candidates.get(parent['window_id'],{}).get('candidate_table',[])
                 if parent['source_in_s'] <= r[0] <= parent['source_out_s']],
            'candidate_limit':'Proxy visual-change estimates, not action facts or prescribed cuts.',
            'draft_claim_limit':'Every parent description is a fallible proposal. Correct observed details yourself; missing results stay missing.',
            'output_duration_limit':'Historical 45-second assembly is not the desired target. Allocate this event only sufficient informative moments.'}
        template = {'parent_segment_id':parent['segment_id'],'kept_slices':[{
            'source_in_s':'your numeric cut-in','source_out_s':'your numeric cut-out', 'speed':1,'freeze_tail_s':0,
            'visible_information':'实际所见新信息','essential_interval':{'source_start_s':'your numeric essential start',
                'source_end_s':'your numeric essential end','min_readable_s':1},'reason':'保留和速度的具体依据'}],
            'deletion_checks':[{'source_start_s':parent['source_in_s'],'source_end_s':parent['source_out_s'],
                'decision':'keep','information_lost':'删去会丢失什么','reason':'逐区间比较'}],
            'obligation_status':'unresolved','limitations':[],'uncertainties':[]}
        prompt = knowledge+'\n当前单段正常速度实际视频。先自己看，再作局部精剪；时间用原电影绝对秒。\n'+json.dumps(packet,ensure_ascii=False)+\
            '\n输出一个完整JSON，所有字段显式存在。kept_slices可以是数个不连续关键瞬间；deletion_checks必须恰覆盖父段整个源时域，不重叠或漏尾。'+\
            '父段范围不可扩大。保留和省略区间要与新切片相符。未知如实unresolved。模板是形状，不是切点答案：\n'+json.dumps(template,ensure_ascii=False)
        stage = bound['stage']
        value = glm.call(stage,prompt,proxy['path'],lambda v:validate(v,parent,32))
        result.append({'stage':stage,'parent_segment_id':parent['segment_id'],'parent_segment':parent,'proposal':value,'media':proxy})
    require(sum(len(r['proposal']['kept_slices']) for r in result) <= 32,'local:combined_segment_limit')
    state.set_artifact('goal_research_local_proposals_8',{'policy':POLICY,'round':8,
        'input_lock_sha256':json_sha(state.data['input_lock']),'source_draft_sha256':json_sha(original),'rows':result,
        'evidence_role':'Model creative trimming proposals, not independent source facts or a rendered output.'})
    return result


def unchanged(proposals):
    for row in proposals:
        cuts, parent = row['proposal']['kept_slices'], row['parent_segment']
        if len(cuts) != 1 or any(abs(cuts[0][k]-parent.get(k,0)) > .001
                                for k in ('source_in_s','source_out_s','speed','freeze_tail_s')):
            return False
    return True


def compact_context(context, proposals):
    """Archive whole navigation; present only relevant parent evidence to assembly."""
    return {k:context[k] for k in ('reference','editing_reference','reference_duration_s','reference_audio_stream_index',
        'catalog','render_capabilities','audio_semantics_policy','known_exhausted_slice_inputs')} | {
        'local_trim_proposals':proposals,
        'watched_windows': [w for w in context['watched_windows'] if w['window_id'] in {
            row['parent_segment']['window_id'] for row in proposals}],
        'instruction':'You own final order, slots, roles, claims, captions and editing bindings. Final source intervals/speed/hold must exactly match a kept_slices proposal; do not revert to the untrimmed assembly. Local observations are creative estimates; all final slices still need independent facts and claims.'}


def enforce_final(plan, proposals):
    available = [(r['parent_segment'],c) for r in proposals for c in r['proposal']['kept_slices']]
    for s in plan['segments']:
        match = next((i for i,(p,c) in enumerate(available) if s['source_id'] == p['source_id'] and s['window_id'] == p['window_id']
                   and all(abs(s.get(k,0)-c[k]) <= .001 for k in
                           ('source_in_s','source_out_s','speed','freeze_tail_s'))),None)
        require(match is not None,'local:final_slice_not_selected_by_local_model')
        available.pop(match)
    return plan
