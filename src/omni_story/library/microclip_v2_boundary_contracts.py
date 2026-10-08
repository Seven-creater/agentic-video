"""Mechanical decisions for bounded, same-event boundary observation.

A resolution is a model assertion, not proof that an action or editing choice
is correct. Old blocked replies remain separate immutable input evidence.
"""
from __future__ import annotations

from .microclip_v2_contracts import _ids, _number, _require, _strings, _text


MAX_OBSERVATION_S = .6
MAX_LOCAL_ROUNDS = 3

NAVIGATION_FORMAT = {
    'event_id': '<same original event>',
    'anchor_frame_id': '<same original candidate>',
    'obligation_ids': ['<original event obligation>'],
    'action': 'observe',
    'cut_intent': 'existing_shot_boundary',
    'question': 'Specific missing observable information',
    'resolution': '',
    'source_start_s': 0,
    'source_end_s': .2,
    'confirmed_frame_id': None,
    'evidence_frame_ids': ['<already observed evidence>'],
    'blocking_questions': ['Unresolved question'],
    'limitations': [],
}

# Forward prompt guidance: these examples select no actual source interval or
# frame. They only describe mutually exclusive output fields. Keep the original
# single example for code imports that only need its common field names.
NAVIGATION_ACTION_EXAMPLES = {
    'observe': {
        **NAVIGATION_FORMAT,
        'question': '需要哪些尚未出现的状态或变化来核验当前边界？',
        'resolution': '',
        'source_start_s': 0,
        'source_end_s': .2,
        'confirmed_frame_id': None,
        'blocking_questions': ['该具体可见变化尚未确认'],
    },
    'confirm': {
        **NAVIGATION_FORMAT,
        'action': 'confirm',
        'question': '',
        'resolution': '根据所引用的真实帧解释怎样解决上轮具体阻塞，'
                      '及原事件每项义务为什么仍然保留；这不是仅重复动作标签。',
        'source_start_s': None,
        'source_end_s': None,
        'confirmed_frame_id': '<actual already observed candidate frame ID>',
        'evidence_frame_ids': ['<actual already observed candidate frame ID>',
                               '<actual evidence resolving the previous block>'],
        'blocking_questions': [],
    },
    'blocked': {
        **NAVIGATION_FORMAT,
        'action': 'blocked',
        'question': '哪些必要证据仍然缺失，为什么当前无法解决？',
        'resolution': '',
        'source_start_s': None,
        'source_end_s': None,
        'confirmed_frame_id': None,
        'evidence_frame_ids': [],
        'blocking_questions': ['当前无法解决的具体必要证据缺项'],
    },
}

NAVIGATION_FIELD_RULES = (
    '输出一个且只有一个动作 JSON 对象，不输出三个示例的合集。'
    'event_id、anchor_frame_id、obligation_ids 保持同一原事件身份与全部义务。'
    'cut_intent必须是existing_shot_boundary或semantic_trim这两个合法字符串之一；'
    '定位原片已有镜头边界用existing_shot_boundary，主动镜头内部裁切用semantic_trim；'
    '不得使用active_in_shot_cut等自造名称或别名。'
    'action=observe：source_start_s/source_end_s 必须是自行选择的真实源时间数值，'
    'slot内 start<end 且范围不超过0.6秒；这是观察请求而非裁点。'
    'confirmed_frame_id 必须为null或省略，question和blocking_questions说明具体未解决缺项。'
    'action=confirm：source_start_s和source_end_s必须都为JSON null或同时省略；'
    '数值0不是null，不要填0/0，也不要保留或复制上轮observe的范围。'
    'confirmed_frame_id选择真正已观察的帧，最终邻帧核验时仅可选当前附件帧；'
    'evidence_frame_ids引用实际证据，resolution解释如何消解上轮阻塞及保留原义务，'
    'blocking_questions必须为空。仍缺证据时不能confirm。'
    'action=blocked：source_start_s、source_end_s、confirmed_frame_id都必须为null或省略；'
    'blocking_questions必须具体且非空，不提出可执行观察或裁点。'
    'start_frame_id/end_frame_id不属于这三个动作的输出字段，须省略或为null。'
    '示例中的数字、帧ID和解释仅说明字段类型，不能直接作为实际视频答案。'
)


def validate_navigation(value, catalog, slot, event, intent, previous,
                        *, new_frame_ids=(), shown_frame_ids=None):
    """Validate model navigation without converting semantic blocks to failures.

    Observation envelopes are numerical source-time requests, not executable
    edit points. Confirmation needs actual observed IDs plus new evidence, or a switch
    from searching an existing cut to a semantic cut within the shot.
    """
    _require(isinstance(value, dict), 'boundary_navigation_object')
    action = value.get('action')
    _require(action in {'observe', 'confirm', 'blocked'}, 'boundary_action')
    _require(value.get('cut_intent') in {'existing_shot_boundary', 'semantic_trim'},
             'boundary_cut_intent: allowed values are existing_shot_boundary or semantic_trim; '
             'do not use aliases such as active_in_shot_cut')
    _require(value.get('event_id') == event['event_id'], 'boundary_event_unchanged')
    _require(value.get('anchor_frame_id') in catalog, 'boundary_observed_anchor')
    if previous.get('anchor_frame_id'):
        _require(value['anchor_frame_id'] == previous['anchor_frame_id'],
                 'boundary_candidate_unchanged')
    wanted = set(event['obligation_ids'])
    allowed = {row['obligation_id'] for row in intent['obligations']}
    _require(wanted <= allowed, 'boundary_event_obligations_exist')
    supplied = _ids(value.get('obligation_ids'), wanted, 'boundary_obligation_ids')
    _require(supplied == wanted, 'boundary_all_original_event_obligations')
    _strings(value.get('blocking_questions'), 'boundary_blocking_questions')
    _strings(value.get('limitations'), 'boundary_limitations')
    for question in value['blocking_questions']:
        _text(question, 'boundary_specific_blocking_question')
    for key in ('question', 'resolution'):
        _require(isinstance(value.get(key), str), 'boundary_' + key)
    evidence = _ids(value.get('evidence_frame_ids'), catalog,
                    'boundary_observed_evidence', empty=action == 'blocked')
    anchor_sha = catalog[value['anchor_frame_id']]['source_sha256']
    _require(all(catalog[ident]['source_sha256'] == anchor_sha for ident in evidence),
             'boundary_evidence_same_source')
    if action == 'observe':
        _text(value['question'], 'boundary_specific_observation_question')
        _require(bool(value['blocking_questions']), 'boundary_observe_needs_unresolved_question')
        _require(value.get('confirmed_frame_id') is None, 'boundary_observe_not_confirmed')
        start, end = value.get('source_start_s'), value.get('source_end_s')
        _number(start, 'boundary_observation_start_s')
        _number(end, 'boundary_observation_end_s')
        _require(slot['start_s'] <= start < end <= slot['end_s'] + 1e-6,
                 'boundary_observation_in_slot')
        _require(end - start <= MAX_OBSERVATION_S + 1e-6, 'boundary_observation_capacity')
        _require(value.get('start_frame_id') is None and value.get('end_frame_id') is None,
                 'boundary_observation_request_is_not_a_cut')
    elif action == 'confirm':
        ident = value.get('confirmed_frame_id')
        _require(isinstance(ident, str) and ident in catalog,
                 'boundary_confirmed_observed_frame')
        row = catalog[ident]
        _require(row['source_sha256'] == anchor_sha, 'boundary_confirmation_same_source')
        _require(slot['start_s'] <= row['source_time_s'] < slot['end_s']
                 and row.get('frame_end_s') is not None
                 and row['frame_end_s'] <= slot['end_s'] + 1e-6,
                 'boundary_confirmed_frame_in_slot')
        _require(ident in evidence, 'boundary_confirmation_cites_selected_frame')
        if shown_frame_ids is not None:
            _require(ident in set(shown_frame_ids), 'boundary_confirmed_frame_in_current_attachment')
        _require(not value['blocking_questions'], 'boundary_confirm_cannot_keep_blockers')
        _require(value.get('start_frame_id') is None and value.get('end_frame_id') is None
                 and value.get('source_start_s') is None and value.get('source_end_s') is None,
                 'boundary_confirm_has_no_observation_envelope: action=confirm requires '
                 'source_start_s/source_end_s/start_frame_id/end_frame_id to be JSON null '
                 'or omitted; numeric 0 is not null; do not retain the previous observe range')
        _text(value['resolution'], 'boundary_explained_resolution')
        _require(value['resolution'].strip() not in {'confirm', 'confirmed', 'semantic_trim',
                 'existing_shot_boundary'}, 'boundary_resolution_not_action_label')
        new = set(new_frame_ids)
        _require(new <= set(catalog), 'boundary_new_evidence_is_observed')
        old_intent = previous.get('cut_intent', 'existing_shot_boundary')
        changed_intent = (old_intent == 'existing_shot_boundary'
                          and value['cut_intent'] == 'semantic_trim')
        _require(bool(new & evidence) or changed_intent,
                 'boundary_confirmation_needs_new_evidence_or_explicit_semantic_trim')
    else:
        _require(bool(value['blocking_questions']), 'boundary_blocked_needs_question')
        _require(value.get('confirmed_frame_id') is None
                 and value.get('start_frame_id') is None
                 and value.get('end_frame_id') is None
                 and value.get('source_start_s') is None
                 and value.get('source_end_s') is None,
                 'boundary_blocked_has_no_executable_choice: action=blocked requires '
                 'source_start_s/source_end_s/confirmed_frame_id/start_frame_id/end_frame_id '
                 'to be JSON null or omitted; numeric 0 is not null; do not retain the previous observe range')
    return value
