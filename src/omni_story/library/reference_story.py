"""Immutable reference requirements shared by writing and delivered-video review."""
from copy import deepcopy
import json

from .state import json_sha


def make_contract(reference, scenes, lines, *, visual_policy=None, creative_brief=None):
    payload = dict(
        reference_sha256=reference.get('reference_sha256'),
        goal={k: reference.get(k) for k in ('theme', 'intended_takeaway', 'core_action_transfer')},
        lines=[{k: deepcopy(r.get(k)) for k in
                ('line_id', 'role', 'text', 'function', 'must_preserve', 'on_screen', 'addressee_roles')}
               for r in lines],
        scenes=[{k: deepcopy(s.get(k)) for k in
                 ('scene_id', 'line_ids', 'template', 'visual_function', 'must_see', 'start_s', 'end_s')}
                for s in scenes])
    if visual_policy:payload['visual_policy']=visual_policy
    if creative_brief:payload['creative_brief']=creative_brief
    return dict(policy='reference_story_v1', sha256=json_sha(payload), **payload)


def dialogue_adaptation(contract):
    return contract.get('visual_policy')=='continuous_dialogue_adaptation_v1'


def restore_line_timing(contract,timed_lines,*,optional_ids=()):
    """Join original semantic rows to authentic annotated times, never guessed clocks."""
    import math
    check_contract(contract)
    timed={r['line_id']:r for r in timed_lines};result=[]
    for original in contract['lines']:
        row=timed.get(original['line_id'],{});a,b=row.get('start_s'),row.get('end_s')
        valid=(row.get('text')==original['text'] and all(type(t) in (int,float) and math.isfinite(t) for t in (a,b))
            and 0<=a<b)
        if not valid:
            if original['line_id'] not in optional_ids:raise ValueError('original_reference_timing_missing_or_unaligned:'+str(original['line_id']))
            result.append(deepcopy(original));continue
        result.append(dict(deepcopy(original),start_s=a,end_s=b))
    return result


def required_lines(contract):
    optional=set(contract.get('optional_reference_line_ids',[])) if dialogue_adaptation(contract) else set()
    return [r for r in contract['lines'] if r['line_id'] not in optional]


def with_dialogue_scope(contract, optional_ids, rationale):
    """Keep the original evidence; explicitly record model-chosen secondary omissions."""
    check_contract(contract)
    if not dialogue_adaptation(contract):raise ValueError('secondary_omission_not_authorized')
    known={r['line_id'] for r in contract['lines']}
    if not set(optional_ids)<known or not rationale:raise ValueError('invalid_secondary_dialogue_scope')
    result=deepcopy(contract)
    result.update(original_contract_sha256=contract['sha256'],optional_reference_line_ids=list(optional_ids),
                  scope_rationale=rationale)
    result['sha256']=json_sha({k:v for k,v in result.items() if k not in ('policy','sha256')})
    return result


def required_scenes(contract):
    ids={r['line_id'] for r in required_lines(contract)}
    return [s for s in contract['scenes'] if set(s['line_ids']) & ids or not dialogue_adaptation(contract)]


def check_contract(contract):
    payload = {k: v for k, v in contract.items() if k not in ('sha256', 'policy')}
    if contract.get('policy') != 'reference_story_v1' or json_sha(payload) != contract.get('sha256'):
        raise ValueError('reference_contract_changed')


def check_coverage(script, contract):
    """IDs are necessary structural evidence, never semantic proof."""
    check_contract(contract)
    originals = {r['line_id']: r for r in required_lines(contract)}
    covered = []
    for row in script['lines']:
        ids = row.get('reference_line_ids', [])
        if not ids or any(i not in originals or originals[i]['role'] != row['role'] for i in ids):
            raise ValueError('reference_role_or_line_changed')
        covered.extend(ids)
    if covered != list(originals):
        raise ValueError('reference_lines_missing_repeated_or_reordered')
    silent = {s['scene_id'] for s in required_scenes(contract) if not s['line_ids']}
    if {s['scene_id'] for s in script.get('visual_scenes', [])} != silent:
        raise ValueError('reference_silent_story_scene_missing')


def check_timeline(rows, lines, contract):
    """A complete paper script cannot hide footage or audio lost in scheduling."""
    check_contract(contract)
    if {r['scene_id'] for r in rows} != {s['scene_id'] for s in required_scenes(contract)}:
        raise ValueError('reference_scene_missing_from_actual_timeline')
    for line in lines:
        parts=[r for r in rows if r.get('line_id')==line['line_id']]
        if not parts or sum(r['end_s']-r['start_s'] for r in parts)+1/30 < line['voiced_s']:
            raise ValueError('reference_speech_missing_or_truncated:'+line['line_id'])


def review_instructions(contract):
    check_contract(contract)
    if dialogue_adaptation(contract):
        return (
            '\n本次用户选择continuous_dialogue_adaptation_v1：以实际找到的稳定连续对话场景创作，'
            '画面大致相容即可，不逐个复刻原片道具、动作、表情或纯动作尾声。此创作约定优先于原作的画面清单。'
            '原参考完整保留供对照，并不代表每个动作必须出现在成片。编剧应围绕当前场景合理改写，'
            '保住说话人关系、问答、反问、承诺与反驳、递进和包袱；道具、职业、数值、具体事实可作连贯适配。'
            '原话可合并精简，但不可把主要问答和冲突压成几句无关闲聊，也不可让角色变成替他人复述。'
            '对白可谈画外事件或物品，同场讲话与听者反应即可承载；不因没拍出所谈动作就判失败。'
            '原片纯动作尾声已明确不要求复刻，允许在改编对白的收尾处结束；不得据此谎称那些动作在新片发生。'
            '核对实际声音、字幕、人物稳定、同场连续性和剧本逻辑；明显错人、字幕错句、无关场景跳切仍须报告。'
            'reference_checks逐条覆盖未列入optional_reference_line_ids的参考台词line_id，'
            'preserved表示其对话作用通过合理改编保留，不要求原物件与原事实照搬。'
            'optional_reference_line_ids是编剧提议省去的次要插曲，原句仍完整列在lines中。'
            '若存在这些省略，另给omission_checks逐条覆盖它们，preserved表示虽省去此句但主要角色关系、'
            '问答、核心冲突与包袱仍成立，evidence须说明原句作用与省去后的实际影响。'
            '如果省掉了核心信息则必须false，不能因已列为可选就默认批准。'
            'story_checks仅覆盖包含台词的参考scene_id，检查改编后的问答递进与收尾是否成立。'
            '每项包含line_id或scene_id、preserved布尔值、evidence实际依据；不能只给总体好评。\n'+
            json.dumps(dict(reference_contract=contract),ensure_ascii=False))
    return (
        '\nreference_contract是选材前冻结的完整参考，优先于改编意图、生成剧本和任何范围缩减。'
        '按原始台词的实际意思、说话人与回应关系逐条核验，编号相同不等于意思保留。'
        '允许相邻同角色台词合并、道具地点人物替换及自然措辞调整；同一物件替换须前后一致。'
        '必须保住问题与回答、反问反驳、信息揭示、态度变化、冲突递进、因果及收尾作用。'
        '关键物证或动作可由有相同叙事作用的真实画面替代，不能拿普通闲聊或道歉替代冲突升级。'
        '无对白场景同样核对visual_function与结局；不能因没有台词就丢弃。'
        '参考台词已合并时，各条可引用同一生成台词；逐条判断原义是否仍成立。'
        '只依据本次提供的实际成片或待审核剧本，不得把参考内容当作已经出现。'
        '在正常回复中增加reference_checks:[{line_id:参考行号,preserved:true或false,evidence:"实际对应或缺失"}]，'
        '覆盖完整reference_contract.lines；增加story_checks:[{scene_id:"参考场景ID",preserved:true或false,'
        'evidence:"实际因果、递进、结局与必要画面的对应或缺失"}]，覆盖每个原始场景。'
        '无法核实用false；任一核心意思缺失就报告，不以整体流畅抵消。\n' +
        json.dumps(dict(reference_contract=contract), ensure_ascii=False))


def check_review(value, contract):
    check_contract(contract)
    problems = []
    checks=[('reference_checks', 'line_id', [r['line_id'] for r in required_lines(contract)]),
        ('story_checks', 'scene_id', [s['scene_id'] for s in required_scenes(contract)])]
    if contract.get('optional_reference_line_ids'):
        checks.append(('omission_checks','line_id',contract['optional_reference_line_ids']))
    for key, ident, expected in checks:
        rows = value.get(key) if isinstance(value, dict) else None
        if key=='reference_checks' and isinstance(rows,list):
            # Some replies append scene evidence to the line array as well as
            # story_checks. Classify only explicit, known scene rows; retain
            # their negative judgements and never fill a missing line/scene.
            scene_ids={s['scene_id'] for s in required_scenes(contract)}
            extra=[r for r in rows if isinstance(r,dict) and 'line_id' not in r
                and r.get('scene_id') in scene_ids and type(r.get('preserved')) is bool
                and isinstance(r.get('evidence'),str) and r['evidence'].strip()]
            rows=[r for r in rows if r not in extra]
            problems.extend(dict(kind='meaning_not_preserved',**r) for r in extra if not r['preserved'])
        complete = (isinstance(rows, list) and len(rows) == len(expected) and
                    all(isinstance(r, dict) and r.get(ident) in expected and
                        type(r.get('preserved')) is bool and isinstance(r.get('evidence'), str) and
                        r['evidence'].strip() for r in rows) and
                    {r[ident] for r in rows} == set(expected))
        if not complete:
            problems.append(dict(kind='incomplete_review', field=key))
        else:
            problems.extend(dict(kind='meaning_not_preserved', **r) for r in rows if not r['preserved'])
    return dict(passed=not problems, contract_sha256=contract['sha256'], issues=problems)
