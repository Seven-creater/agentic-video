"""Finite, model-owned local observation of one blocked event boundary."""
import json
from pathlib import Path

from .microclip import _frame_context, _read
from .microclip_v2 import FRAME_FORMAT, _image_call
from .microclip_v2_contracts import validate_frames
from .microclip_v2_media import anchor_grid
from .state import LibraryStopped


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _context(index, event, key, round_index, phase, **extra):
    return {'edge_index': index, 'event_id': event['event_id'], 'anchor_key': key,
            'boundary_round': round_index, 'phase': phase, **extra}


def _event_route_context(state):
    """Read the model's original route; do not create new event contributions."""
    events = state._events()
    shared = {}
    for event in events:
        for ident in event['obligation_ids']:
            shared.setdefault(ident, []).append(event['event_id'])
    return {'original_events': events,
            'shared_obligation_event_ids': {k: v for k, v in shared.items() if len(v) > 1},
            'evidence_limit': 'Original model nominations and reasons are fallible; '
                'shared IDs do not prove that every event independently realizes the whole obligation.'}


EVENT_ROUTE_RULE = (
    '原事件路线只读。一项必要信息可由多个事件共同承载；核验当前事件原reason中的贡献和衔接，'
    '不要要求每个贡献事件单独完成同一全局义务的所有过程、结果和反应。'
    '仍保留当前event原obligation_ids，完整义务在全部片段的整体核验中检查。'
    '不要把未分配给本事件的后续反应强加给本事件。'
    '重选边界须考虑全部事件的源时间顺序和不重叠；其他原提名是待确认方案，不能假称已确认。')


def _navigation_call(glm, state, directory, name, manifest, prompt, validator, **extra):
    # A single explicitly bound old repair contains a model-owned confirmation
    # nomination plus its prior observation range. The CPU receipt preserves
    # that range as non-executable metadata. It is never a third paid request,
    # and never creates a retroactive parsed/pass file for the failed reply.
    if state.authorization.get('boundary_metadata_resume'):
        from .microclip_v2_boundary_metadata import received_projection
        projection = received_projection(state.output, state.data, name)
        if projection:
            _, value = projection
            validator(value)
            print(_json({'stage': name, 'received_metadata_projection': True}), flush=True)
            return value
    return _image_call(glm, state, directory, name, manifest, prompt, validator, **extra)


def _restore_views(state, index, catalog, observations):
    """Only successful original replies establish observed new frame IDs."""
    state._reload()
    for call in state.data['calls']:
        if not call['name'].startswith(f'mc2_edge_{index}_view_') or call['status'] != 'received':
            continue
        stem = call['name'].removesuffix('_repair')
        parsed = state.output / 'calls' / call['id'] / 'parsed.json'
        if not parsed.exists():
            continue
        _, facts = state._received(stem)
        descriptor = _read(state.data['artifacts']['mc2_input_' + stem][0]['path'])
        manifest = _read(descriptor['lineage_path'])
        catalog.update({f['frame_id']: f for f in manifest['frames']})
        # A repair and its original can never both be successful under this adapter.
        if facts not in observations:
            observations.append(facts)


def resolve_boundary(glm, state, directory, source, slot, index, event, key,
                     original_manifest, blocked, intent, catalog, observations):
    from .microclip_v2_boundary_contracts import (
        MAX_LOCAL_ROUNDS, NAVIGATION_ACTION_EXAMPLES, NAVIGATION_FIELD_RULES, validate_navigation)
    from .microclip_v2_boundary_media import boundary_pages
    from .microclip_v2_boundary_state import effective_edge, record_effective_edge

    if not state.authorization.get('boundary_resume'):
        raise LibraryStopped('boundary_missing_essential_evidence:' + _json(blocked))
    state.assert_protected()
    effective = effective_edge(state.output, state.data, index)
    if effective:
        _restore_views(state, index, catalog, observations)
        manifest = _read(effective[2]['lineage_path'])
        catalog.update({f['frame_id']: f for f in manifest['frames']})
        return effective[1]

    folder = Path(directory) / 'boundary_navigation'
    knowledge = Path(state.authorization['boundary_resume']['knowledge_path']).read_text(encoding='utf-8')
    initially_seen = set(catalog)
    previous, current_manifest = blocked, original_manifest
    history = []
    anchor_id = event[key + '_frame_id']
    route_context = _event_route_context(state)
    # At most three new observation envelopes. A fourth navigation can confirm
    # the final evidence, but cannot ask for a fourth paid observation round.
    for round_index in range(MAX_LOCAL_ROUNDS + 1):
        name = f'mc2_edge_{index}_nav_{round_index}'
        seen = {**catalog, **{f['frame_id']: f for f in current_manifest['frames']}}
        decision = _navigation_call(glm, state, folder, name, current_manifest,
            '只处理同一事件的这一个未确认边界，保留原事件全部义务。旧判断可能被新帧推翻。'
            '选择observe补看新连续帧、confirm提名实际已观察候选，或blocked报告无法解决。'
            '区分原片已有切换与主动镜头内裁切；后者须明确说明改变意图及保留信息的依据。'
            'observe自主给源时间source_start_s/source_end_s，单次最多0.6秒，不能要求已看完的同一批帧。'
            '明确当前缺项和什么新可见变化会改变判断。confirm只提名，随后还要看候选本体及真实邻帧。'
            'event_id、anchor_frame_id和obligation_ids保持原事件身份，anchor_frame_id仍是原提名身份，'
            'confirmed_frame_id才是新选择。最多三次观察，不能删义务或把文字依赖改为纯画面成立。仅JSON：'
            + _json(NAVIGATION_ACTION_EXAMPLES) + '\n动作字段规则：' + NAVIGATION_FIELD_RULES
            + '\n边界用途：' + key + '\n同一事件：' + _json(event)
            + '\n全部必要信息（只读）：' + _json(intent['obligations'])
            + '\n完整原事件路线与共享义务：' + _json(route_context) + '\n事件范围规则：' + EVENT_ROUTE_RULE
            + '\n原始未确认回复（只读）：' + _json(blocked)
            + '\n上一步：' + _json(previous) + '\n新增观察历史：' + _json(history)
            + '\n已收到的独立帧事实：' + _json(observations)
            + '\n真正已观察帧映射：' + _json(_frame_context(list(catalog.values())))
            + '\n局部准则：\n' + knowledge,
            lambda v: validate_navigation(v, seen, slot, event, intent, previous,
                new_frame_ids=set(seen) - initially_seen),
            **_context(index, event, key, round_index, 'nav', anchor_frame_id=anchor_id))
        catalog.update({f['frame_id']: f for f in current_manifest['frames']})
        nav_call, _ = state._received(name)
        decision_call, decision_stage = nav_call, name
        history.append({'stage': name, 'decision': decision})
        if decision['action'] == 'blocked':
            raise LibraryStopped('boundary_navigation_blocked:' + _json(decision))
        if decision['action'] == 'confirm':
            selected_id = decision['confirmed_frame_id']
            confirmed_grid = anchor_grid(source['path'], slot, selected_id,
                folder / 'frames' / f'edge_{index}_confirm_{round_index}')
            candidate_catalog = {**catalog, **{f['frame_id']: f for f in confirmed_grid['frames']}}
            shown_ids = {f['frame_id'] for f in confirmed_grid['frames']}
            confirm_name = f'mc2_edge_{index}_confirm_{round_index}'
            confirmed = _image_call(glm, state, folder, confirm_name, confirmed_grid,
                '附件是新候选本体及其连续真实邻帧。最终核验同一事件边界；这不是JSON修复。'
                '逐项确认原事件义务是否保留，裁点后还有动作不等于必须继续保留，但若声称原片切换，'
                '需要实际切换证据。明确撤回被反证的旧理由。'
                'confirm的confirmed_frame_id只能选当前附件的真实ID；引用当前帧证据解释如何解决原阻塞。'
                '仍缺项可observe自主指定新源时间（<=0.6秒），或blocked。不可为了完成而确认。仅JSON：'
                + _json(NAVIGATION_ACTION_EXAMPLES) + '\n动作字段规则：' + NAVIGATION_FIELD_RULES
                + '\n同一事件：' + _json(event)
                + '\n边界用途：' + key + '\n原始阻塞：' + _json(blocked)
                + '\n提名决策：' + _json(decision) + '\n局部观察历史：' + _json(history)
                + '\n全部原义务：' + _json(intent['obligations'])
                + '\n完整原事件路线与共享义务：' + _json(route_context) + '\n事件范围规则：' + EVENT_ROUTE_RULE
                + '\n准则：\n' + knowledge,
                lambda v: validate_navigation(v, candidate_catalog, slot, event, intent, blocked,
                    new_frame_ids=set(candidate_catalog) - initially_seen, shown_frame_ids=shown_ids),
                **_context(index, event, key, round_index, 'confirm', nav_call_id=nav_call['id'],
                    selected_frame_id=selected_id, anchor_frame_id=anchor_id))
            catalog.update({f['frame_id']: f for f in confirmed_grid['frames']})
            confirm_call, _ = state._received(confirm_name)
            history.append({'stage': confirm_name, 'decision': confirmed})
            if confirmed['action'] == 'confirm':
                record_effective_edge(state, index, confirm_call, confirmed)
                state._reload()
                return effective_edge(state.output, state.data, index)[1]
            if confirmed['action'] == 'blocked':
                raise LibraryStopped('boundary_neighbor_confirmation_blocked:' + _json(confirmed))
            decision, decision_call, decision_stage = confirmed, confirm_call, confirm_name

        if round_index >= MAX_LOCAL_ROUNDS:
            raise LibraryStopped('no_progress:boundary_observation_capacity_exhausted:' + _json(decision))
        bundle = boundary_pages(source['path'], slot, decision['source_start_s'], decision['source_end_s'],
            anchor_id, folder / 'frames' / f'edge_{index}_view_{round_index}', observed_frame_ids=set(catalog))
        # This immutable plan binds every actual page to the model's requested
        # envelope, including the separately labelled old-anchor comparison.
        plan = {'policy': state.authorization['boundary_resume']['policy'], 'edge_index': index,
            'boundary_round': round_index, 'nav_call_id': nav_call['id'],
            'decision_call_id': decision_call['id'], 'decision_stage': decision_stage,
            'source_start_s': decision['source_start_s'], 'source_end_s': decision['source_end_s'],
            'pages': [{'manifest_path': p['manifest_path'], 'request_sha256': p['request_sha256'],
                       'grid_sha256': p['grid']['sha256']} for p in bundle['pages']],
            'new_frame_ids': bundle['new_frame_ids'], 'continuous_source_range': bundle['continuous_source_range'],
            'page_bindings': bundle['page_bindings'], 'limitations': bundle['limitations']}
        state.set_artifact(f'mc2_boundary_page_plan_{index}_{round_index}', plan)
        page_facts = []
        for page_index, page in enumerate(bundle['pages']):
            value = _image_call(glm, state, folder,
                f'mc2_edge_{index}_view_{round_index}_{page_index}', page,
                '独立观察实际附件逐帧事实。描述状态与变化、文字和未知，不给剪辑答案，'
                '不把未出现的动作结果补出来。当前需要核验的问题：' + decision['question']
                + '\n该页范围性质：' + _json(bundle['page_bindings'][page_index])
                + '\n仅JSON：' + _json(FRAME_FORMAT),
                lambda v, rows=page['frames']: validate_frames(v, rows),
                **_context(index, event, key, round_index, 'view', nav_call_id=nav_call['id'],
                    decision_call_id=decision_call['id'], page_index=page_index,
                    page_count=len(bundle['pages']), anchor_frame_id=anchor_id))
            catalog.update({f['frame_id']: f for f in page['frames']})
            observations.append(value)
            page_facts.append(value)
        current_manifest = bundle['pages'][-1]
        history.append({'decision_call_id': decision_call['id'], 'question': decision['question'],
            'requested_range': [decision['source_start_s'], decision['source_end_s']],
            'new_frame_ids': bundle['new_frame_ids'], 'facts': page_facts})
        previous = decision
    raise AssertionError('finite_boundary_navigation')
