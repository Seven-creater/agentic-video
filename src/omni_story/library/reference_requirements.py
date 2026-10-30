"""Shared visible-minimum and meaning-transfer policy, independent of templates."""
from copy import deepcopy
import json


def minimal_visual_contract():
    # Unicode characters (not bytes): enough for two role labels and one relation,
    # one verb class, or one framing instruction; not a miniature screenplay.
    return dict(policy='minimal_visible_v1', max_characters=dict(
        who_relation=48, action_class=16, shot_need=24), boolean_fields=['mouth_open_needed'],
        principle='素材不必复演参考。原电影故事、台词和声音不重要，剪辑与TTS重赋意义（断章取义）。'
            'must_see仅写配音无法伪造的最小可见事实：谁及关系、动作类别、镜头需要、是否需嘴动。'
            '道具、具体手势、场景、结果、情绪及转变仅放nice_to_have或dub_can_carry；'
            '动机、主题和说什么由配音承载。nice_to_have只用于排序，不能否决可用素材。'
            '保留画音一致、每角色唯一规范身份、3D-only和正文范围约束。',
        repair_hint='只保留who_relation/action_class/shot_need短语和mouth_open_needed布尔值；'
            '未知键、类型不符或超长值完整移入nice_to_have，不截字拼造新的硬条件。')


def _violations(value, path):
    contract = minimal_visual_contract()
    if not isinstance(value, dict):
        return [dict(path=path, value=deepcopy(value), expected=contract,
                     repair_hint=contract['repair_hint'], reason='object_required')]
    failures = []
    for key, item in value.items():
        limit = contract['max_characters'].get(key)
        reason = None
        if key in contract['boolean_fields']:
            if type(item) is not bool:
                reason = 'boolean_required'
        elif limit is None:
            reason = 'unknown_key'
        elif not isinstance(item, str):
            reason = 'string_required'
        elif len(item) > limit:
            reason = 'value_too_long'
        if reason:
            failures.append(dict(path=path + '.' + key, value=deepcopy(item),
                expected=contract, repair_hint=contract['repair_hint'], reason=reason))
    return failures


def validate_must_see(value, path='$.must_see'):
    failures = _violations(value, path)
    if failures:
        error = ValueError('qfs_questions:invalid_minimal_must_see')
        error.violation = failures[0]
        raise error
    return value


def normalise_must_see(beat):
    """Questions have no repair round: preserve overflow as optional, with an audit."""
    value = beat.get('must_see')
    failures = _violations(value, '$.must_see')
    nice = beat.setdefault('nice_to_have', [])
    if not isinstance(nice, list):
        nice = beat['nice_to_have'] = [json.dumps(nice, ensure_ascii=False)]
    for failure in failures:
        key = failure['path'].removeprefix('$.must_see.')
        nice.append(key + ': ' + json.dumps(failure['value'], ensure_ascii=False))
        beat.setdefault('normalised_must_see', []).append(failure)
        if isinstance(value, dict):
            value.pop(key, None)
    if not isinstance(value, dict):
        value = beat['must_see'] = {}
    value.setdefault('mouth_open_needed', False)
    validate_must_see(value)
    return beat


def meaning_transfer_contract():
    return dict(policy='meaning_transfer_v1', principle=minimal_visual_contract()['principle'],
        meaning='判断实际画面+配音是否传达参考intended_takeaway和价值立场；'
            '静音盲读无需独自表达已合理分配给配音的含义。'
            '人物、场景、调度、手势、道具以及对白改旁白等形式差异本身不是missing_core_meaning。'
            '仅当配音无法承载而不与可见事实矛盾，或组合后的核心含义确实缺失，才列为核心缺项。'
            '不把reference.visual_form、speech_form或旧coverage中的具体实例当硬条件。',
        picture_audio_coherence='独立检查画音一致性，返回picture_audio_coherence字段：'
            'coherent/contradictory/unverifiable；逐项列明实际时间、可见事实、实际听到'
            '（精剪阶段为计划）的配音及冲突；对白须挂在可见说话者或有已建立身份的合理画外音，'
            '旁白不能与可见人物、动作矛盾。原片说什么、逐音素口型不同步不构成矛盾。'
            '严重割裂或冒认说话者仍须修复，不能借形式自由放行。'
            '用issues或problems中的picture_audio_coherence:前缀单列，影响核心时同时填写'
            'missing_core_meaning，并遵守既有next_action/repair_at一致性合同。')


def meaning_transfer_prompt():
    return '\n意义迁移与画音一致性合同：' + json.dumps(meaning_transfer_contract(), ensure_ascii=False)
