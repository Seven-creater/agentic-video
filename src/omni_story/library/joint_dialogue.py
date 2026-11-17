"""Reference-grounded writing and finite, independent audio/video scheduling."""
from copy import deepcopy
from collections import deque
import json
import math
from pathlib import Path
import re

from . import audio_render
from .state import LibraryStopped, json_sha, write_json

FPS = 30
BEAM = 24
# Q3: words follow the locked picture (ea3fb17: lines cite what is visible).
ADAPT_GUIDE = (
    '按实际画面改编：reference_objects是参考画面里的具体物件；台词提到的物件和动作，尽量是所选shot_ids的action/objects里实际可见的。'
    'must_preserve里点名的关键物件和关键说法推动情节（如揭穿作弊的吸铁石），保留原词，台词可以提到画面外的东西，不换成不相干的物件；'
    'verbatim_terms是从参考原句冻结的关键原词，必须逐字保留在对应台词中，合并原句时保留各句全部关键原词；'
    '其他参考物件在所选镜头里看不到时，换成这些镜头里真有、且在剧情里作用相同的物件或动作（参照must_see.replaceable与footage.visible），'
    '保持must_preserve的信息、问答关系和结尾反差；visual_reason写明该句对应哪个可见事实。画面里确实没有可替代物时才保留原话。'
    'must_see.footage.status为substitute时，画面是以替代方式承担must_see.function，台词须让观众把这个画面理解成同一作用。'
)
ADAPT_MAX_GROWTH = 1.2
# One-character nouns ("钱") also match unrelated words ("价钱"); only 2+ characters are compared.
ADAPT_MIN_TERM = 2
INTEGRITY_STOPS = ('resume_from_', 'recorded_', 'historical_')


class CoverageError(LibraryStopped):
    def __init__(self, scene_id, detail, reason='joint_coverage_insufficient'):
        self.scene_ids = [scene_id] if isinstance(scene_id, str) else list(scene_id)
        self.scene_id = self.scene_ids[0]
        self.reason = reason
        # Set by prepare() once the script and all speech exist, so a degraded
        # delivery can reuse exactly what this attempt received.
        self.prepared = None
        super().__init__(f'joint_coverage_insufficient:{self.scene_ids}:{detail}')


def speaker_view(shot,actor):
    return set(shot['speaking'])=={actor} or (not shot['speaking'] and actor in shot.get('dub_speaker_ids',[]))


def anchor_view(shot,actor,bound):
    """A shot that can introduce a new voice: the audience can tell it is this performer.

    A real source speaker qualifies; a dub-only view must show no other bound performer,
    or have been checked to make this performer the clear visual focus (focus_ids: in front
    or close-up, facing the camera or acting while the others turn away or listen quietly;
    user 11-17 for pairs who never appear apart). Shared by casting and the scheduler.
    """
    return speaker_view(shot,actor) and (actor not in shot.get('dub_speaker_ids',[])
        or set(shot['visible'])&set(bound)=={actor} or actor in shot.get('focus_ids',[]))


def eligible(scene, shot, role=None):
    bound = {b['role_id'] for b in scene['role_bindings'].values()}
    if not bound.intersection(shot['visible']):
        return False
    if role is None:
        return not shot['speaking']
    if scene['role_bindings'][role].get('voice_only'):
        # An unseen call may accompany the addressee working or reacting.
        # Semantic approval below still has to justify this exact action/line.
        return not shot['speaking']
    speaker = scene['role_bindings'][role]['role_id']
    return (speaker_view(shot,speaker) or
            (not shot['speaking'] and speaker not in shot['visible'] and
             bool((bound-{speaker}).intersection(shot.get('listeners', [])))))


MIN_KEEP = .4


def reusable(shot):
    """A static in-place shot of one speaker or of silent listeners may be shown again, with no
    play limit (user 11-11: cut the found exchange to the new dialogue and come back to it).
    Only loopable footage qualifies (user rule 11-05: same shot, in place, mouth or small
    moves): walking, props being used or anything else that changes state would visibly
    rewind. 03 (77ea53ca): a fixed limit of 3 plays left 35 s of dialogue without picture
    and the run fell to L2."""
    return shot.get('loopable') is True and len(shot.get('speaking') or []) <= 1


# User 11-17: the same picture must not keep coming back. Delivered films the user accepted
# show each frame about once (不烧心 1.0x, 粉色娇嫩 1.0x; 华强 2.3x); 薛甄珠 showed 17-22 s of
# footage under 110-121 s of speech (5-7x, one shot 31-46% of the film). Speech may at most
# replay the scene's distinct footage this many times; longer references condense.
REUSE_MAX = 2.0


def footage_budget(scene):
    from .scene_coverage import capacity
    unique = capacity(scene['coverage_shots'])
    return dict(unique_footage_s=round(unique, 2), max_speech_s=round(REUSE_MAX * unique, 2), reuse_max=REUSE_MAX)


def role_needs(scene):
    """Characters each role needs at least: MIN_KEEP of its words, and MIN_LINE_CHARS per turn."""
    lines = scene['reference_lines']
    words = {}
    for r in lines:
        words[r['role']] = words.get(r['role'], 0) + _chars(r.get('text') or '')
    return {role: max(math.ceil(MIN_KEEP * w), MIN_LINE_CHARS * role_turns(lines, role))
            for role, w in words.items() if w}, words


def char_limits(scene, voices, *, may_omit=False):
    """Hard per-role character ceiling from the scene's footage budget.

    The scene budget (REUSE_MAX x distinct footage) is split by what each role needs at least
    (MIN_KEEP of its words, MIN_LINE_CHARS per turn), bounded by REUSE_MAX x the footage that
    can carry the role, then converted by its voice rate. Splitting by raw words starved short
    roles: 薛甄珠 (82d75c818816) gave 前台 8 characters for 4 turns and the script could not be
    written. Loopable shots do not lift the ceiling.
    27 r4: an unenforced budget let 313 characters through to paid synthesis; no 0.85 margin
    here (measured TTS ran faster, about 5.1 chars/s, than the table rate).
    03 (third retry): 8 characters for an 85-character lead lost the meaning. Where the story
    may drop secondary reference lines (may_omit) the adaptation plan does so until every role
    fits; otherwise a ceiling below MIN_KEEP of the words is not enforced."""
    from .scene_coverage import capacity
    from .dialogue_cut import RATES
    budget = footage_budget(scene)
    needs, words = role_needs(scene)
    total = sum(needs.values())
    limits = {}
    for role in scene['role_bindings']:
        if role not in needs:
            continue
        rate = RATES.get(voices.get(role), max(RATES.values()))
        own = REUSE_MAX * capacity([s for s in scene['coverage_shots'] if eligible(scene, s, role)])
        limit = math.floor(min(own, budget['max_speech_s'] * needs[role] / total) * rate)
        if may_omit or limit >= MIN_KEEP * words[role]:
            limits[role] = limit
    return limits


# A kept turn needs room to be a line, not a gasp: 不烧心 (accepted) runs about 1.4 s, about
# 6 characters, per line. 薛甄珠 (82d75c818816) left short roles 2 characters per turn; all three
# script attempts spent the whole output budget reasoning (0 characters out).
MIN_LINE_CHARS = 6


def role_turns(lines, role):
    """Runs of consecutive reference lines by this role (a run may become one script line)."""
    turns, previous = 0, None
    for r in lines:
        if r['role'] == role and previous != role:
            turns += 1
        previous = r['role']
    return turns


def fit_problems(scene, voices, kept=None):
    """Why the kept reference cannot be condensed into the footage budget.

    Shares follow need, so a share-bound role fits exactly when the scene's total need is at most
    max_speech_s x its rate ('scene_total': keep less in the whole scene). A role short of its own
    footage must drop its own lines ('role_footage')."""
    from .scene_coverage import capacity
    from .dialogue_cut import RATES
    if kept is not None:
        scene = dict(scene, reference_lines=[r for r in scene['reference_lines'] if r['line_id'] in kept])
    needs, words = role_needs(scene)
    total, budget = sum(needs.values()), footage_budget(scene)
    rows = []
    for role, limit in char_limits(scene, voices, may_omit=True).items():
        if limit >= needs[role]:
            continue
        rate = RATES.get(voices.get(role), max(RATES.values()))
        own = math.floor(REUSE_MAX * capacity([s for s in scene['coverage_shots'] if eligible(scene, s, role)]) * rate)
        base = dict(scene_id=scene['scene_id'], role=role, reference_chars=words[role],
                    turns=role_turns(scene['reference_lines'], role), max_chars=limit, needed_chars=needs[role],
                    min_keep=MIN_KEEP, min_line_chars=MIN_LINE_CHARS)
        if own < needs[role]:
            rows.append(dict(base, kind='role_footage', target_role_chars=math.floor(own / MIN_KEEP),
                             target_role_turns=max(1, own // MIN_LINE_CHARS)))
        else:
            rows.append(dict(base, kind='scene_total', scene_need=total, unique_footage_s=budget['unique_footage_s'],
                             target_scene_need=math.floor(budget['max_speech_s'] * rate),
                             role_needs=dict(sorted(needs.items(), key=lambda kv: -kv[1]))))
    return rows


def prompt(lock, reference, previous=None):
    from .scene_coverage import capacity
    from .dialogue_cut import voices_for, RATES
    from .reference_story import dialogue_adaptation
    approximate=dialogue_adaptation(lock.get('reference_contract',{}))
    voices = voices_for(lock)
    adaptation_guide = (
        '道具和地点可换成实际镜头中可见且承担同一作用的事物；替换须贯穿前后问答、证据和结局。'
        'must_preserve描述的是须保住的信息与戏剧作用，其中的原作道具名称不是强制复刻清单。'
        '保留辨识度高的问句、反问和包袱结构，不把反复质问改成一次普通询问，不把对抗改成和解。'
        '动作不能凭台词假造；找不到等价画面时保留缺口供重新选材。'
        if lock.get('reference_contract') else ADAPT_GUIDE)
    if approximate:
        adaptation_guide=(
            '本次用户要以找到的连续对话场景创作合理剧本，不精确复刻参考画面。'
            '先看这些人物正在什么场所交流、有什么可用物品，围绕这个场景改编具体台词。'
            '保留原对话关系、提问回应、质疑反驳、递进与包袱，道具、具体事实和措辞可作一致替换。'
            'reference_lines中的must_preserve按对话作用理解，不能误当原作道具或动作的复刻清单。'
            '同场普通讲话和听者反应即可承载新台词；中性或微笑表演可作询问、调侃或质疑。'
            '不要求台词中的画外物品或事件真的出现在画面，不为追逐精确动作跳换场景。'
            'visual_reason只说明真实说话/反应及合理联系，不宣称没有发生的可见动作。'
            '可以精简、合并同角色相邻原句，但须保住完整的问答递进，不能缩成几句无关对白。'
            + ADDRESSEE_GUIDE)
    scenes = []
    for scene in lock['scenes']:
        row = {k: scene.get(k) for k in ('scene_id', 'start_s', 'end_s', 'template', 'visual_function',
            'role_bindings', 'reference_lines', 'conversation', 'coverage_shots', 'introduced_roles')}
        if approximate:
            # Creative adaptation follows dialogue functions, while authentic
            # per-line clocks remain in the lock for the later audio scheduler.
            row['reference_lines']=[{k:v for k,v in r.items() if k not in {'start_s','end_s'}} for r in scene['reference_lines']]
        if scene.get('adaptation_plan'):row['adaptation_plan']=scene['adaptation_plan']
        if scene.get('must_see') or scene.get('reference_objects'):
            verdicts = (scene.get('picture') or {}).get('must_see') or {}
            row['must_see'] = [dict(item, footage=verdicts.get(item['id'], {})) for item in scene.get('must_see', [])]
            row['reference_objects'] = scene.get('reference_objects', [])
        row['dialogue_budget'] = {}
        row['usable_shots'] = {role: dict(
            dialogue=[s['slot_id'] for s in scene['coverage_shots'] if eligible(scene, s, role)],
            speaking=[s['slot_id'] for s in scene['coverage_shots'] if speaker_view(s,binding['role_id'])])
            for role, binding in scene['role_bindings'].items()}
        limits = char_limits(scene, voices, may_omit=approximate)
        row['footage_budget'] = footage_budget(scene)
        for role, binding in scene['role_bindings'].items():
            count = sum(len(re.findall(r'[\u3400-\u9fffA-Za-z0-9]', r['text']))
                        for r in scene['reference_lines'] if r['role'] == role)
            if not count:
                continue
            usable = capacity([s for s in scene['coverage_shots'] if eligible(scene, s, role)])
            speaking = capacity([s for s in scene['coverage_shots'] if speaker_view(s,binding['role_id'])])
            repeatable = [s['slot_id'] for s in scene['coverage_shots'] if reusable(s) and eligible(scene, s, role)]
            row['dialogue_budget'][role] = dict(reference_chars=count, speaking_s=round(speaking, 2),
                speech_and_reaction_s=round(usable, 2), reusable_dialogue_shots=repeatable,
                suggested_chars=min(count, limits[role]) if role in limits else count,
                **({'max_chars': limits[role], 'target_chars': char_target(limits[role])} if role in limits else {}))
        scenes.append(row)
    return ('根据已核实的参考与实际有限镜头，联合编写对白和画面用途。'+
        ('按已审adaptation_plan建立新场景的具体话题和物件，保留参考每句的对话作用、关系、递进与包袱。'
         '原作物件、数值、职业和具体事实可连贯改编；不要机械粘贴原台词，也不要为了动作细节牺牲稳定交流。' if approximate else
         '保持原始问答、关键事实、转折、结尾反差。must_preserve/function是参考分析固定的要求，不由改写重新定义。'
         '原话可适配角色和可见物件，但不可删去语义。')+
        '当前角色绑定是唯一身份；姓名未知时用外观一致的称呼或新故事名字，不能冒认其他已知角色。'
        '先通读全场：loopable=true的原地讲话或原地倾听镜头可由程序回切重复使用；其余镜头只播放一次。'
        'footage_budget是本场不重复画面秒数与对白总时长上限（不超过画面的2倍），避免同一画面反复出现；'
        'reusable_dialogue_shots列出可回切的讲话或听者镜头，回切也计入这个上限。'
        'dialogue_budget给出每个角色的画面容量和字数上限；在上限内简洁表达，不无谓扩写。'
        'suggested_chars是自然语速的保守估计；max_chars是该角色本场全部台词字数的硬上限（超过则画面放不下），'
        '自己数字数有误差，请按target_chars写（比上限少约5%）；'
        '超出时合并同角色相邻句、精简措辞，保留must_preserve的核心信息和全部line_id。'
        '一条完整声音可跨多个镜头；shot_ids按适合程度列出多个可替代镜头，程序依据真实TTS时长选择区间。'
        'shot_ids只能从该角色usable_shots.dialogue中选择；人物在全片的首次露脸发言从usable_shots.speaking中选择。'
        '人物首次露脸发言须由该人物实际讲话画面承载，让观众认清是谁在说；之后的台词可用同场听者反应、被点名者或其他已建立的同场画面，'
        '由对白把意思讲清楚。introduced_roles是此前场景已露脸发言过的角色。'
        '参考行on_screen=false表示原本就是画外发言，可保留画外声音配真实对话对象倾听；'
        '这种句子不强制首次露脸，不需要另外补一个讲话演员，不能把参考中露脸的台词也改成画外声。'
        'voice_only=true的角色没有出镜演员，其画外声可配同场对话对象的合理反应或与本句相关的工作动作；'
        '必须解释动作与本句的联系，不能选无关表演或另一人开口讲话。'
        '不能选另一个人正在讲话的镜头作听者，不能将咀嚼当成讲话。镜头用途须与实际动作、起止姿态相容。'
        '反应镜头须显示已核实同一连续事件的另一位角色，选倾听或能承接对白的安静反应，不能用无关动作；'
        '不能在说话者本人嘴部清晰可见却闭嘴的画面上播放他的声音。dub_speaker_ids单独登记真实嘴被面具或固定造型遮住的人，'
        'concealed_dub_ids可作为遮嘴配音表演，stylized_dub_ids仅在用户启用动画简化口型策略时可用普通静态表演；'
        '两者都不得说成原片嘴动。首次发言优先该角色单独清楚出镜，其他人实际开口仍不可混配。'
        '连续原句可以合并为一条，限同角色且保持全部reference_line_ids及顺序，不能变成事后复述。'
        '每个场景reference_lines里的每一个line_id都必须按原顺序出现在某一句的reference_line_ids中，一个都不能漏；'
        '画面容量紧时合并同角色的相邻普通句、压缩措辞，绝不能丢掉line_id。'
        '每条visual_reason说明实际画面如何承接内容，一句话即可。不得自行编原片时间点。' + adaptation_guide +
        ('previous_lines是上一场已写好的最后几句，只用于衔接语气和称呼，不要重复或改写它们。' if previous else '') +
        '无对白场景也须选择能表达visual_function/template的shot_ids，action_reason说明可见依据；不能用无关画面凑秒。' +
        ('本次reference_contract冻结完整参考；道具和地点可做一致的功能替换，但必须保留每轮信息、'
         '问答、反驳、冲突递进和结局。不能因为参考原词点名了道具，就给不存在的动作硬配台词。'
         '无对白片段也必须保留叙事作用，素材不足应报告，不压缩成另一段故事。'
         if lock.get('reference_contract') and not approximate else '') +
        '只返回JSON {"lines":[{"line_id":"l1","scene_id":"scene_1","role":"角色",'
        '"reference_line_ids":[1],'+('"addressee_roles":["交谈对象角色"],' if approximate else '')+
        '"text":"完整台词","shot_ids":["镜头ID"],"visual_reason":"选择依据"}],'
        '"visual_scenes":[{"scene_id":"scene_2","shot_ids":["镜头ID"],"action_reason":"视觉表达依据"}]}。\n' +
        json.dumps(dict(reference={k: reference.get(k) for k in ('theme', 'intended_takeaway')}, scenes=scenes,
                        **({'previous_lines': previous} if previous else {})), ensure_ascii=False))


ADDRESSEE_GUIDE = (
    '每句另给addressee_roles：本句在新片里实际交谈对象的role，只能是本场已绑定的其他角色，无对象填[]。'
    '默认沿用参考行的addressee_roles；coverage_shots的events与addressee_ids记录了说话者在镜头里实际面向谁。'
    '若参考对象在本场从未被说话者面向（例如两人从不同框），而说话者实际面向另一位已绑定角色，'
    '就把本句改为对实际面向的角色说，并改写台词与后续应答使对话成立，保住原句的对话作用；'
    '不能让画面里朝甲说话、台词却在吩咐乙。')


def line_addressees(scene, line):
    """Whom a line is said to in the new film.

    The writer may re-address a line to the person the speaker actually faces in the
    found footage (dialogue adaptation only); otherwise the reference relation holds.
    Shared by script validation and the shot review so both judge the same relation."""
    if (scene.get('visual_policy') == 'continuous_dialogue_adaptation_v1'
            and isinstance(line.get('addressee_roles'), list)):
        return sorted(dict.fromkeys(line['addressee_roles']))
    refs = {r['line_id']: r for r in scene['reference_lines']}
    return sorted({role for rid in line['reference_line_ids'] for role in refs[rid].get('addressee_roles', [])})


def validate_script(value, lock):
    """27 r2: each repair hit a different first error (merged anchor line, then a
    dropped range, then a missing scene) and the budget ran out. Every problem of a
    reply is now reported together with its line and the allowed values; the
    accepted set is unchanged."""
    lines = value.get('lines', [])
    # A reference with no speech lines at all (silent subtitled edit) migrates to a
    # purely visual piece; dialogue is only mandatory when reference lines exist.
    speech_scenes = {s['scene_id'] for s in lock['scenes'] if s['reference_lines']}
    if (not lines and speech_scenes) or len({r.get('line_id') for r in lines}) != len(lines):
        raise ValueError('lines须为有唯一line_id的非空数组')
    problems, shot_errors = [], []
    silent_scenes={s['scene_id'] for s in lock['scenes'] if not s['reference_lines']}
    extra_visual={r.get('scene_id') for r in value.get('visual_scenes',[])}-silent_scenes
    if extra_visual:
        problems.append(f'visual_scenes只能列独立无对白场景{sorted(silent_scenes)}；当前多出{sorted(extra_visual)}。'
            '对白场景的收尾反应应在该场台词镜头用途及既有停顿中安排，不能再创建同ID的无对白场景；保留对白与收尾作用。')
    unknown = sorted({str(r.get('scene_id')) for r in lines} - {s['scene_id'] for s in lock['scenes']})
    if unknown:
        problems.append(f'台词不能新增未规划场景{unknown}；只能用{[s["scene_id"] for s in lock["scenes"]]}')
    for scene in lock['scenes']:
        sid = scene['scene_id']
        shots = {s['slot_id']: s for s in scene['coverage_shots']}
        refs = {r['line_id']: r for r in scene['reference_lines']}
        rows = [r for r in lines if r.get('scene_id') == sid]
        if refs:
            covered = []
            for row in rows:
                ids, role = row.get('reference_line_ids', []), row.get('role')
                if not ids or any(i not in refs or refs[i]['role'] != role for i in ids):
                    problems.append(f'{sid}: 第{row.get("line_id")}句只能承接同角色的实际参考行；'
                                    f'role={role!r}，reference_line_ids={ids}')
                    continue
                declared = row.get('addressee_roles')
                if scene.get('visual_policy') == 'continuous_dialogue_adaptation_v1' and declared is not None:
                    others = sorted(set(scene['role_bindings']) - {role})
                    if not isinstance(declared, list) or any(t not in others for t in declared):
                        problems.append(f'{sid}: 第{row.get("line_id")}句addressee_roles={declared!r}须为数组，'
                                        f'只能含本场其他已绑定角色{others}，无对象填[]')
                if not row.get('text', '').strip() or not row.get('visual_reason'):
                    problems.append(f'{sid}: 第{row.get("line_id")}句的text与visual_reason不能为空')
                missing_terms = sorted(t for t in key_terms(scene, row) if t not in row.get('text', ''))
                if missing_terms:
                    problems.append(f'{sid}: 第{row.get("line_id")}句须逐字保留参考关键原词{missing_terms}，不能删去或替换')
                selected = row.get('shot_ids', [])
                if not selected or any(i not in shots or not eligible(scene, shots[i], role) for i in selected):
                    allowed = [i for i, s in shots.items() if eligible(scene, s, role)]
                    shot_errors.append(f'{row.get("line_id")}: 选正确说话者或静默同场听者，允许{allowed}')
                covered.extend(ids)
            if covered != list(refs):
                missing = [i for i in refs if i not in covered]
                repeated = sorted({i for i in covered if covered.count(i) > 1})
                detail = (f'；缺少参考行{missing}' if missing else '') + (f'；重复参考行{repeated}' if repeated else '')
                if not missing and not repeated:
                    # Name the first misplaced pair instead of two long lists (03: 17/18 swapped).
                    at = next(k for k, (a, b) in enumerate(zip(covered, refs)) if a != b)
                    detail = f'；第{at + 1}个位置应为参考行{list(refs)[at]}，当前是{covered[at]}（参考行{list(refs)[at]}须排在{covered[at]}之前）'
                problems.append(f'{sid}: 参考内容须完整按序覆盖{list(refs)}，当前{covered}' + detail)
        else:
            if rows:
                problems.append(f'{sid}: 该场景没有参考台词，不能新增台词；只规划无对白事件')
            visual = [r for r in value.get('visual_scenes', []) if r.get('scene_id') == sid]
            bound = {b['role_id'] for b in scene['role_bindings'].values()}
            if len(visual) != 1 or not visual[0].get('action_reason') or not visual[0].get('shot_ids'):
                problems.append(f'{sid}: 须规划无对白事件及实际镜头依据')
            elif any(i not in shots or not bound.intersection(shots[i]['visible']) for i in visual[0]['shot_ids']):
                allowed = [ident for ident, shot in shots.items() if bound.intersection(shot['visible'])]
                shot_errors.append(f'{sid}: 无对白镜头须包含已绑定角色，允许{allowed}；保留该场景，不得删除')
    if shot_errors:
        problems.append('镜头引用：\n' + '\n'.join(shot_errors))
    if lock.get('cast_bindings'):
        from .dialogue_cut import voices_for
        voices = voices_for(lock)
        for scene in lock['scenes']:
            from .reference_story import dialogue_adaptation
            may_omit = dialogue_adaptation(lock.get('reference_contract', {}))
            for role, limit in char_limits(scene, voices, may_omit=may_omit).items():
                own = [r for r in lines if r.get('scene_id') == scene['scene_id'] and r.get('role') == role]
                written = sum(_chars(r.get('text') or '') for r in own)
                if written > limit:
                    # 21 (d75343de): three repairs came back 8, 2 and 1 characters over; the
                    # model cannot count characters reliably, so give it the program's counts
                    # and a target with a counting margin (the ceiling itself is unchanged).
                    counts = sorted(((_chars(r.get('text') or ''), str(r.get('line_id'))) for r in own), reverse=True)
                    problems.append(f'{scene["scene_id"]}: {role}台词共{written}字，超过画面可容纳的{limit}字（max_chars），'
                                    f'至少删{written-limit}字，建议写到{char_target(limit)}字左右；程序统计的各句字数（长→短）：'
                                    + '、'.join(f'{ident}={n}' for n, ident in counts[:8]) +
                                    '；优先精简最长的几句，合并相邻句，保留must_preserve核心信息和全部line_id')
    if problems:
        raise ValueError('须同时修复以下所有问题：\n' + '\n'.join(problems))
    return value

def tidy_script_part(value, scene_id):
    """Fix in place what the program can fix, so the model only repairs real content problems.

    21 (7f626fc5): one 47-line reply repeated line_id l1 and added an empty placeholder line;
    the whole reply was rewritten and the JSON broke. Line ids are renumbered after merging,
    every row belongs to the scene being written, and placeholders without text or reference
    lines are dropped."""
    if not isinstance(value, dict):
        return value
    rows = [r for r in value.get('lines') or [] if isinstance(r, dict) and
            ((r.get('text') or '').strip() or r.get('reference_line_ids'))]
    for k, row in enumerate(rows, 1):
        row.update(line_id=f'l{k}', scene_id=scene_id)
        if isinstance(row.get('text'), str):
            row['text'] = row['text'].strip()
    value['lines'] = rows
    value['visual_scenes'] = [v for v in value.get('visual_scenes') or [] if isinstance(v, dict) and v.get('scene_id') == scene_id]
    return value


def write_script(lock, reference, state, glm, number):
    """Write the dialogue one scene per call and merge (user 11-11).

    Replies longer than about 6k characters needed a repair 25-29% of the time on the
    server (3% under 3k), and a repair rewrites the whole reply (21 failed after a 10k-
    character, six-scene reply was rewritten three times). Decomposed writing follows
    LongWriter (arXiv 2408.07055) and Decomposed Prompting (arXiv 2210.02406); the previous
    scene's last lines keep the voice continuous."""
    from .pipeline import _material_call
    lines, visual = [], []
    for index, scene in enumerate(lock['scenes'], 1):
        part = dict(lock, scenes=[scene])
        def validate(value, part=part, sid=scene['scene_id']):
            tidy_script_part(value, sid)
            return validate_script(value, part)
        validate.repair_limit = 2
        validate.revalidated_protocol_repair = True
        previous = [dict(role=r['role'], text=r['text']) for r in lines[-4:]]
        reply = _material_call(state, glm, f'material_scene_script_{index}_{number}',
                               prompt(part, reference, previous), validate)
        lines += reply.get('lines', [])
        visual += reply.get('visual_scenes', [])
    for k, row in enumerate(lines, 1):
        row['line_id'] = f'l{k}'
    return dict(lines=lines, visual_scenes=visual)


def _chars(text):
    return len(re.findall(r'[㐀-鿿A-Za-z0-9]', text))


def char_target(limit):
    """Aim below the hard ceiling by a counting margin (about 5%, at least 3 characters)."""
    return max(0, limit - max(3, math.ceil(.05 * limit)))


def _adapt_limit(text):
    return max(_chars(text) + 4, math.ceil(_chars(text) * ADAPT_MAX_GROWTH))


def shot_text(shots):
    """Everything the coverage observation saw in these shots, as one searchable string."""
    return ' '.join(' '.join([s.get('place', ''), s.get('action', ''), s.get('entry', ''), s.get('exit', ''),
                              *s.get('objects', [])]) for s in shots)


def key_terms(scene, row):
    """Frozen reference words survive initial writing and both picture rewrite paths.

    New partitions extract literal words before footage adaptation, so a semantic
    description using a synonym cannot change the protected source spelling. Old
    partitions still protect object names shared by the source and must_preserve.
    """
    refs = [r for r in scene.get('reference_lines', []) if r['line_id'] in row.get('reference_line_ids', [])]
    return {t for r in refs for t in r.get('verbatim_terms', [])} | {
        t for r in refs for t in scene.get('reference_objects') or []
        if t and t in str(r.get('must_preserve') or '') and t in r.get('text', '')}


def unseen_objects(script, lock):
    """Q3, mechanical: lines naming a reference object that none of their shots shows.

    Objects that must_preserve names stay as spoken (user 11-11): 03 (fc6699b6) turned the
    magnet that exposes the rigged scale into 'the rabbit toy is fake', losing the plot."""
    findings = []
    for scene in lock['scenes']:
        terms = [t for t in scene.get('reference_objects') or [] if len(t) >= ADAPT_MIN_TERM]
        shots = {s['slot_id']: s for s in scene.get('coverage_shots', [])}
        for row in script.get('lines', []):
            if not terms or row.get('scene_id') != scene['scene_id']:
                continue
            seen = shot_text([shots[i] for i in row.get('shot_ids', []) if i in shots])
            kept = key_terms(scene, row)
            missing = [t for t in terms if t in row.get('text', '') and t not in seen and t not in kept]
            if missing:
                findings.append(dict(line_id=row['line_id'], scene_id=scene['scene_id'], terms=missing,
                                     shot_ids=list(row.get('shot_ids', []))))
    return findings


def adapt_prompt(script, lock, findings):
    rows = []
    for finding in findings:
        line = next(r for r in script['lines'] if r['line_id'] == finding['line_id'])
        scene = next(s for s in lock['scenes'] if s['scene_id'] == finding['scene_id'])
        refs = [r for r in scene['reference_lines'] if r['line_id'] in line.get('reference_line_ids', [])]
        rows.append(dict(line_id=line['line_id'], role=line['role'], text=line['text'], unseen_terms=finding['terms'],
            must_preserve=[r.get('must_preserve') or r.get('function') for r in refs],
            verbatim_terms=sorted(key_terms(scene, line)),
            shots=[{k: s.get(k) for k in ('slot_id', 'place', 'action', 'objects')}
                   for s in scene['coverage_shots'] if s['slot_id'] in finding['shot_ids']],
            must_see=scene.get('must_see', []), max_chars=_adapt_limit(line['text'])))
    return ('以下台词提到了所配镜头里看不到的参考物件unseen_terms。逐句改写text：把这些物件换成shots里实际可见、'
        '且在剧情里作用相同的物件或动作，表达同一作用；找不到作用相同的就原样返回原句；保持must_preserve的信息、语气及与上下句的问答关系，不增加新情节；'
        'verbatim_terms中的关键原词必须逐字保留，即使画面外也不删改；其他改写物件须在shots里看得到。'
        '字数不超过max_chars。按给定顺序逐句返回，不增删句子。'
        '只返回JSON {"lines":[{"line_id":"l1","text":"改写后台词","visible_basis":"对应的可见画面"}]}。\n' +
        json.dumps(dict(lines=rows), ensure_ascii=False))


def validate_adaptation(value, findings, script, lock):
    rows = value.get('lines') if isinstance(value, dict) else None
    if not isinstance(rows, list) or [r.get('line_id') if isinstance(r, dict) else None for r in rows] != \
            [f['line_id'] for f in findings]:
        raise ValueError('lines须按顺序逐句返回：' + json.dumps([f['line_id'] for f in findings]))
    for finding, row in zip(findings, rows):
        original = next(r for r in script['lines'] if r['line_id'] == finding['line_id'])
        scene = next(s for s in lock['scenes'] if s['scene_id'] == finding['scene_id'])
        text = row.get('text')
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f'{finding["line_id"]}: text不能为空')
        if _chars(text) > _adapt_limit(original['text']):
            raise ValueError(f'{finding["line_id"]}: 不超过{_adapt_limit(original["text"])}字')
        missing = sorted(t for t in key_terms(scene, original) if t not in text)
        if missing:
            raise ValueError(f'{finding["line_id"]}: 须逐字保留参考关键原词{missing}，不能删去或替换')
        if text.strip() == original['text'].strip():
            continue  # no visible object plays the same part: the original words stay
        seen = shot_text([s for s in scene['coverage_shots'] if s['slot_id'] in finding['shot_ids']])
        still = [t for t in scene['reference_objects'] if len(t) >= ADAPT_MIN_TERM and t in text and t not in seen
                 and t not in key_terms(scene, original)]
        if still:
            raise ValueError(f'{finding["line_id"]}: 仍提到镜头里看不到的{still}，换成shots中实际可见的物件或动作')
    return value


def adapt_to_picture(script, lock, state, glm, output, number):
    """Q3 is best effort: any failure keeps the accepted script (today's behaviour)."""
    from .pipeline import _fallback_applies, _material_call
    if not any(scene.get('reference_objects') for scene in lock['scenes']):
        return script  # No Q1 data: exactly the baseline path, nothing recorded.
    findings = unseen_objects(script, lock)
    record = dict(policy='adapt_to_picture_v1', findings=findings, changes=[])
    if not findings:
        record['status'] = 'nothing_to_adapt'
    else:
        groups = [[f for f in findings if f['scene_id'] == s['scene_id']] for s in lock['scenes']]
        groups = [g for g in groups if g]
        try:
            rows = []
            # One scene per call, like the script itself (user 11-11: long replies fail).
            for k, group in enumerate(groups, 1):
                def validate(value, group=group):
                    return validate_adaptation(value, group, script, lock)
                validate.repair_limit = 1
                name = f'material_scene_adapt_1_{number}' + (f'_{k}' if len(groups) > 1 else '')
                rows += _material_call(state, glm, name, adapt_prompt(script, lock, group), validate)['lines']
            adapted = deepcopy(script)
            for row in rows:
                line = next(r for r in adapted['lines'] if r['line_id'] == row['line_id'])
                record['changes'].append(dict(line_id=row['line_id'], before=line['text'], after=row['text'].strip(),
                                              visible_basis=row.get('visible_basis', '')))
                line.update(text=row['text'].strip(), adapted_from=line['text'],
                            visible_basis=row.get('visible_basis', ''))
            validate_script(adapted, lock)
            script, record['status'] = adapted, 'adapted'
        except Exception as error:  # noqa: BLE001 - the original script is still a valid delivery
            # Ledger/resume integrity stops keep their meaning, like every other scene stage.
            if (isinstance(error, LibraryStopped) and str(error).startswith(INTEGRITY_STOPS) or
                    not _fallback_applies(state.input_lock['configuration'], output, error)):
                raise
            record.update(status='kept_original', error=f'{type(error).__name__}:{str(error)[:300]}')
    state.set_artifact('scene_script_adaptation', record)
    write_json(Path(output)/'script_adaptation.json', record)
    return script


def unused(shot, rows):
    """Subtract source intervals globally, including overlapping library windows."""
    spans = [(round(shot['source_start_s']*FPS), round(shot['source_end_s']*FPS))]
    for row in rows:
        if row['source_sha256'] != shot['source_sha256']:
            continue
        x, y = row['first'], row['last']
        spans = [(a, b) for low, high in spans for a, b in
                 ((low, min(high, x)), (max(low, y), high)) if a < b]
    return spans


def _atoms_for(shots):
    """Split overlapping windows into shared, non-overlapping source atoms."""
    atoms, shot_atoms = [], {ident: set() for ident in shots}
    for source in {s['source_sha256'] for s in shots.values()}:
        source_shots = {k: s for k, s in shots.items() if s['source_sha256'] == source}
        points = sorted({round(s[k]*FPS) for s in source_shots.values()
                         for k in ('source_start_s', 'source_end_s')})
        for first, last in zip(points, points[1:]):
            owners = [k for k, s in source_shots.items()
                      if round(s['source_start_s']*FPS) <= first < last <= round(s['source_end_s']*FPS)]
            if owners:
                for ident in owners:
                    shot_atoms[ident].add(len(atoms))
                atoms.append((source, first, last))
    return atoms, shot_atoms


def _fits(items, shots, used=(), atoms_spec=None):
    """Max-flow feasibility: every item's frames must fit in its allowed shots."""
    atoms, shot_atoms = atoms_spec if atoms_spec is not None else _atoms_for(shots)
    future = [item for item in items if item['frames'] > 0]
    if not future:
        return True
    capacities = [last-first-sum(max(0, min(last, r['last'])-max(first, r['first']))
                  for r in used if r['source_sha256'] == source) for source, first, last in atoms]
    source, sink = len(future)+len(atoms), len(future)+len(atoms)+1
    graph = [dict() for _ in range(sink+1)]
    def edge(a, b, amount):
        graph[a][b] = amount
        graph[b][a] = 0
    total = sum(item['frames'] for item in future)
    for i, item in enumerate(future):
        edge(source, i, item['frames'])
        available = set().union(*(shot_atoms[ident] for ident in item['shot_ids']))
        for atom in available:
            if capacities[atom] > 0:
                edge(i, len(future)+atom, total)
    for atom, amount in enumerate(capacities):
        if amount > 0:
            edge(len(future)+atom, sink, amount)
    flow = 0
    while flow < total:
        parent, queue = {source: None}, deque([source])
        while queue and sink not in parent:
            node = queue.popleft()
            for other, amount in graph[node].items():
                if amount > 0 and other not in parent:
                    parent[other] = node
                    queue.append(other)
        if sink not in parent:
            return False
        amount, node = total-flow, sink
        while node != source:
            before = parent[node]
            amount = min(amount, graph[before][node])
            node = before
        node = sink
        while node != source:
            before = parent[node]
            graph[before][node] -= amount
            graph[node][before] += amount
            node = before
        flow += amount
    return True


def speaker_anchors(scene, refs, rows):
    """Only a role's first on-screen line in the whole film needs the true speaker.

    The audience must learn who is talking once; after that, professional practice
    lets dialogue run over listeners and reactions (Dubbing in Practice, arXiv
    2212.12137: on-screen lip-sync holds for ~12% of speech time; EditIQ, arXiv
    2502.02172: speaker-only cutting narrates worse than story-driven shot choice).
    27 r2: per-scene and 'key line' anchors pinned a tail scene's single speaker and
    starved the lead role. Roles already introduced in an earlier scene are listed in
    scene['introduced_roles']."""
    anchors, seen = {}, set(scene.get('introduced_roles', []))
    for row in rows:
        offscreen = all(refs[r].get('on_screen') is False for r in row['reference_line_ids'])
        anchors[row['line_id']] = not offscreen and row['role'] not in seen
        if not offscreen:
            seen.add(row['role'])
    return anchors


def introduce_roles(lock):
    """Record, per scene, the roles that already spoke on screen in earlier scenes."""
    seen = set()
    for scene in lock['scenes']:
        scene['introduced_roles'] = sorted(seen)
        seen |= {r['role'] for r in scene.get('reference_lines', []) if r.get('on_screen', True) and r.get('role')}
    return lock


def film_cast(lock, scene):
    """Story roles seen elsewhere in the film but not bound in this scene.

    17 (6c184902893f): a boss line meant for his henchman played over the boss facing the
    village chief of the previous scene; that chief was unbound in this scene, so the review
    took him for an anonymous bystander. The audience already knows him."""
    here = set(scene['role_bindings'])
    out = {}
    for other in lock.get('scenes', []):
        for role, binding in other['role_bindings'].items():
            if role not in here and role not in out and binding.get('role_id'):
                out[role] = {k: binding.get(k) for k in ('role_id', 'appearance')}
    return out


def review_shot_uses(scene, lines, state, glm, number, others=None):
    """Approve the actual action and addressee of every scheduling alternative."""
    from .pipeline import _material_call
    shots = {s['slot_id']: s for s in scene['coverage_shots']}
    refs = {r['line_id']: r for r in scene['reference_lines']}
    requests = []
    for line in lines:
        targets = line_addressees(scene, line)
        line['addressee_roles'] = targets
        line['reference_offscreen'] = all(refs[rid].get('on_screen') is False for rid in line['reference_line_ids'])
        requests.append(dict(line_id=line['line_id'], role=line['role'], text=line['text'],
            addressee_roles=targets, reference_offscreen=line['reference_offscreen'],
            voiced_s=line['voiced_s'], proposed_shots=line['shot_ids'],
            eligible_shots=[ident for ident, shot in shots.items() if eligible(scene, shot, line['role'])]))
    pauses = [ident for ident, shot in shots.items() if eligible(scene, shot)]
    batches = [requests[i:i+REVIEW_BATCH] for i in range(0, len(requests), REVIEW_BATCH)] or [[]]
    merged = dict(lines=[], pause_shots=[], issues=[])
    for k, batch in enumerate(batches, 1):
        name = number if len(batches) == 1 else f'{number}_{k}'
        reply = _material_call(state, glm, name, review_prompt(scene, batch, pauses, shots, others),
                               review_validator(batch, pauses))
        merged['lines'] += reply['lines']
        merged['pause_shots'] += [i for i in reply['pause_shots'] if i not in merged['pause_shots']]
        merged['issues'] += list(reply.get('issues') or [])
    reply = merged
    empty = []
    for line, row in zip(lines, reply['lines']):
        line['shot_options'] = row['options']
        if not row['options']:
            empty.append(line['line_id'])
    if empty:
        error = CoverageError(scene['scene_id'], f'{",".join(empty)}: no action-compatible shot; {reply.get("issues", [])}',
                              reason='no_action_compatible_shot')
        # Every unplaceable line with the reviewer's reasons, for one picture refit (prepare).
        error.refit = dict(scene_id=scene['scene_id'], line_ids=empty, issues=list(reply.get('issues') or []))
        raise error
    return reply


# 03 (77ea53ca): one review of 18 lines against every shot answered 6-9k characters; replies
# over 6k characters needed a repair 25-29% of the time (3% under 3k). Batches stay small.
REVIEW_BATCH = 6


def review_validator(requests, pauses):
    """Drop what the program can judge (unknown or repeated shot ids, foreign pause ids) and
    put rows back in order; only missing lines or a malformed reply go back to the model."""
    def validate(value):
        rows = value.get('lines') if isinstance(value, dict) else None
        wanted = [r['line_id'] for r in requests]
        by_id = {r.get('line_id'): r for r in rows or [] if isinstance(r, dict)}
        missing = [i for i in wanted if i not in by_id]
        if not isinstance(rows, list) or missing:
            raise ValueError(f'须审核每句line_id，缺少{missing}；不改台词或人物')
        for request in requests:
            row = by_id[request['line_id']]
            options, seen = [], set()
            for option in row.get('options') if isinstance(row.get('options'), list) else []:
                ident = option.get('shot_id') if isinstance(option, dict) else None
                if ident in request['eligible_shots'] and ident not in seen:
                    seen.add(ident)
                    options.append(dict(shot_id=ident, reason=str(option.get('reason') or '').strip() or '相容'))
            row['options'] = options
        value['lines'] = [by_id[i] for i in wanted]
        value['pause_shots'] = [i for i in value.get('pause_shots') or [] if i in pauses]
        return value
    return validate


def review_prompt(scene, requests, pauses, shots, others=None):
    approximate=scene.get('visual_policy')=='continuous_dialogue_adaptation_v1'
    return ('你是剪辑审核者。独立审核这些台词与所有可用镜头的动作、对话对象是否相容。'
        'proposed_shots只是写稿建议，不是正确答案。原始observations中的具体动作不能被美化或省略。'
        '逐句独立检查所有eligible_shots，把真正相容的镜头全部列入options，按适合程度排序。'
        'options是可选关系，不是镜头分配结果：同一镜头可同时出现在多句options中，不得因它已列在另一句而删除。'
        '这里只审核相容性，不管时长：原地讲话或倾听的静态镜头由调度器按需重复使用。'
        '凡真正相容的镜头都要列入options，宁可多列由调度器挑选，不可保守少列。'+
        ('用户允许大致相容的对话表演：中性或微笑可承载询问、调侃、质疑和反驳，不要求表情手势逐句匹配；'
         '原画面的表情和情绪（微笑、惊讶、委屈、含泪、生气等）不是排除理由，情绪由新台词和配音表达，'
         '不能因“情绪与本句不符”删去镜头；只核对说话人身份、对话对象和明确动作是否冲突。'
         '同场正确人物讲话或听者反应即可，台词可谈画外事物。不把未出现的动作假说成已经看见。' if approximate else
         '镜头里的表情、手势和情绪也要与本句相容：笑着的镜头不能配争吵，明显的手势要与台词意思对得上。')+
        'dub_speaker_ids是明确授权的后期配音用途：concealed_dub_ids为遮嘴造型，stylized_dub_ids为动画简化口型表演；'
        '它们都不是原片嘴动事实，姿态与语境相容即可，'
        '不要以看不见嘴动为由否定这类画面；别人实际张嘴讲话仍排除。'
        '优先正确说话者面对本句addressee_roles讲话的镜头。若明确朝另一人物说话，不因说话人对就通过；'
        '特写看不见听者时可结合同场前后方向判断，不能臆造交谈对象。'+
        ('film_cast是全片其他场景已出现、本场未绑定的故事角色（按role_id或外观辨认）。观众已认识他们：'
         '说话者明确朝其中某人说话，就属于朝另一人物，不能当作朝本句addressee_roles说话；'
         '只有既非本场角色也非film_cast角色的路人，才可按同场前后方向判断。' if others else '')+
        '触碰或摆弄他人身体、打闹、吃东西、展示物品、离开、快速摇镜等具有明确动作含义，'
        '不能一概解释为倾听/叮嘱/解释，只有本句确实能承接该动作才通过。'
        '特别核对主动作与台词：画面正在展示某物，台词却把它指称为另一物，必须排除或由写稿阶段重新适配。'
        '一般拿着道具不必与台词逐字相同，但道具成为展示或互动中心时不能忽略。'
        '反应镜头必须是本句对话对象的安静倾听或有因果联系的反应；不能用另一个人的主动表演承载整句回答。'
        'reference_offscreen=false只说明参考这句的说话者出镜，不禁止新片切到对话对象的合理反应。'
        '相容的听者反应也须列入options；首次发言和关键句的真实说话镜头要求由后续调度器统一执行，不在这里把整句限定为嘴动镜头。'
        'reference_offscreen=true表示允许保留原有画外发言，可用对话对象的合理反应、倾听或与本句直接相关的工作动作，'
        '也可用正确说话者本人讲话。voice_only角色未绑定出镜演员：对话对象边做事边听画外呼叫是正常叙事，'
        '不因listeners未标注就排除，但必须有实际动作及叙事关系依据；不能用无关表演或其他人开口代替。'
        '它免除露脸要求，不是禁止露脸。'
        'options中的整个镜头区间都必须适用；不能以“截掉后半段才可用”等未落实的条件批准含冲突动作的镜头。'
        'reason用一句话（30字以内）写实际动作、朝向谁、为何与这句相容，不抄旧visual_reason。'
        'pause_shots选择承接本场问答的静默反应或参考所需的相关动作，排除无关表演和转场。不能为了凑时长放宽条件。'
        '如果没有相容镜头，options填[]并在issues说明不足，程序会重新选素材；不要删台词。'
        '只返回JSON {"lines":[{"line_id":"l1","options":[{"shot_id":"镜头ID","reason":"实际依据"}]}],'
        '"pause_shots":["镜头ID"],"issues":["不相容或不足的原因"]}。\n' +
        json.dumps(dict(template=scene.get('template'), cast=scene['role_bindings'], **({'film_cast': others} if others else {}),
            lines=requests, pause_candidates=pauses, observations=list(shots.values())), ensure_ascii=False))


def schedule(items, shots):
    """Small beam search allocates real source frames; no looping, padding or audio cuts."""
    # Split overlapping windows into shared source resources. A small max-flow
    # lookahead reserves enough footage for all remaining speech and pauses,
    # before the beam discards alternatives based on editing cost.
    atoms_spec = _atoms_for(shots)

    def future_fits(future, used):
        return _fits(future, shots, used, atoms_spec=atoms_spec)

    beam = [(0.0, [])]
    for item_index, item in enumerate(items):
        if item['frames'] <= 0:
            continue
        active = [(cost, rows, item['frames']) for cost, rows in beam]
        finished = []
        for _ in range(item['frames']):
            next_states = []
            for cost, rows, remaining in active:
                for rank, ident in enumerate(item['shot_ids']):
                    shot = shots[ident]
                    for first, last in unused(shot, rows):
                        length = min(last-first, remaining)
                        def viable(amount):
                            rest = ([dict(item, frames=remaining-amount)] if amount < remaining else [])
                            reservation = dict(source_sha256=shot['source_sha256'], first=first, last=first+amount)
                            return future_fits(rest+items[item_index+1:], rows+[reservation])
                        if not viable(length):
                            # Reserve a later speaker anchor inside this very shot,
                            # rather than greedily using its entire remaining span.
                            low, high = 0, length
                            while low < high:
                                middle = (low+high+1)//2
                                if viable(middle):
                                    low = middle
                                else:
                                    high = middle-1
                            length = low
                        if not length:
                            continue
                        previous = rows[-1] if rows else None
                        continuation = (previous and previous['slot_id'] == ident and previous['last'] == first)
                        transition = 0 if continuation or previous is None else 1
                        backwards = bool(previous and previous['scene_id'] == item['scene_id'] and
                            previous['source_sha256'] == shot['source_sha256'] and first < previous['last'])
                        reaction = (item['kind'] == 'speech' and 'speaking_ids' in item and
                                    ident not in item['speaking_ids'])
                        replay = bool(shot.get('loop_of'))  # fresh footage first, replays only when needed
                        score = cost + transition + .2*rank + 5*backwards + 3*reaction*length/FPS + 1.5*replay*length/FPS
                        if length < (FPS*.8 if item.get('stable_conversation') else FPS/3) and remaining > length:
                            score += 8 if item.get('stable_conversation') else 2
                        if item['kind'] == 'speech' and length < remaining:
                            score += 2  # user 11-11: a line cut into many pieces hides who is talking
                        piece = dict(slot_id=ident, first=first, last=first+length,
                            source_sha256=shot['source_sha256'], scene_id=item['scene_id'],
                            item_id=item['item_id'], kind=item['kind'], line_id=item.get('line_id'),
                            voice_role=item.get('role'), reason=item.get('shot_reasons', {}).get(ident, item['reason']))
                        state = (score, rows+[piece])
                        if length == remaining:
                            finished.append(state)
                        else:
                            next_states.append((*state, remaining-length))
            if not next_states:
                break
            active = sorted(next_states, key=lambda s: (s[0], s[2]))[:BEAM]
        if not finished:
            raise CoverageError(item['scene_id'], f'{item["item_id"]} needs {item["frames"]/FPS:.2f}s; finite eligible shots exhausted',
                                reason='finite_shots_exhausted')
        # Keep alternative allocations so early speech cannot greedily consume all later anchors.
        unique = {}
        for score, rows in sorted(finished, key=lambda s: s[0]):
            key = tuple((r['slot_id'], r['first'], r['last']) for r in rows)
            unique.setdefault(key, (score, rows))
            if len(unique) >= BEAM:
                break
        beam = list(unique.values())
    return min(beam, key=lambda s: s[0])[1]


REUSE_POLICY = 'dialogue_shot_reuse_v1'


def reuse_schedule(items, shots):
    """Fill every utterance with picture; unseen footage first, then static shots again.

    Editing rules from dialogue-editing work (Leake et al., SIGGRAPH 2017 idioms; EditIQ,
    arXiv 2502.02172; video-texture work on non-periodic reuse, arXiv 2104.02687):
    the speaker is shown first; the same shot never follows itself unless nothing else is
    allowed (a jump cut); among reused shots the least recently shown wins, so the order
    is not a visible fixed cycle; no flash cuts shorter than a third of a second; a role's
    first on-screen line (anchor) stays on unseen footage when it can. Only reusable()
    shots are shown twice. Speech must be filled completely; pauses and visual events
    end where their footage ends."""
    from .dialogue_cut import MIN_FRAMES
    rows, shown = [], {}
    for item in items:
        remaining, placed = item['frames'], 0
        while remaining > 0:
            best, previous = None, (rows[-1] if rows else None)
            for rank, ident in enumerate(item['shot_ids']):
                shot = shots[ident]
                options = [(first, last, False) for first, last in unused(shot, rows)[:1]]
                if reusable(shot):
                    options.append((round(shot['source_start_s']*FPS), round(shot['source_end_s']*FPS), True))
                for first, last, again in options:
                    length = min(last-first, remaining)
                    if length <= 0:
                        continue
                    same = previous is not None and previous['slot_id'] == ident
                    continuation = same and not again and previous['last'] == first
                    score = (0 if previous is None or continuation else 1) + .2*rank + 5*(same and not continuation)
                    if item['kind'] == 'speech' and ident not in item.get('speaking_ids', [ident]):
                        score += 3*length/FPS                      # speaker visible first
                    if again:
                        since = len(rows) - shown.get(ident, -len(items)-len(rows))
                        score += 1.5*length/FPS + 4/(since+1)      # least recently shown first
                        score += 5*bool(item.get('anchor'))        # a first appearance on unseen footage
                    if length < (FPS*.8 if item.get('stable_conversation') else FPS/3) and remaining > length:
                        score += 8 if item.get('stable_conversation') else 2
                    if item['kind'] == 'speech' and length < remaining:
                        score += 2                                 # one shot per line when it fits
                    if best is None or score < best[0]:
                        best = (score, ident, first, length, again)
            if best is None:
                if item['kind'] == 'speech':
                    raise CoverageError(item['scene_id'], f'{item["item_id"]} needs {item["frames"]/FPS:.2f}s; '
                                        'no reusable or unseen footage left', reason='finite_shots_exhausted')
                if item['kind'] == 'visual' and placed < MIN_FRAMES:
                    raise CoverageError(item['scene_id'], 'visual event footage is taken by shared speech',
                                        reason='no_visual_event_coverage')
                break
            _, ident, first, length, again = best
            shot = shots[ident]
            rows.append(dict(slot_id=ident, first=first, last=first+length, source_sha256=shot['source_sha256'],
                scene_id=item['scene_id'], item_id=item['item_id'], kind=item['kind'], line_id=item.get('line_id'),
                voice_role=item.get('role'), reason=item.get('shot_reasons', {}).get(ident, item['reason']),
                **({'reused': True} if again else {})))
            shown[ident] = len(rows)
            remaining -= length
            placed += length
    return rows


def fit_pacing(items, shots):
    """Fit pauses and visual events to the footage the scenes actually share.

    Each scene sizes its pauses and visual event against its own shot pool, but
    scenes of one window share source frames, so together they can overcommit
    footage that every utterance itself fits in. Speech is never changed; pauses
    shrink first, then visual events, each kind by one common ratio, to the most
    the shared pool holds. When speech alone does not fit, items are returned
    unchanged and the scheduler reports the real speech shortage."""
    atoms = _atoms_for(shots)
    fits = lambda rows: _fits(rows, shots, atoms_spec=atoms)
    if fits(items) or not fits([i for i in items if i['kind'] not in ('reaction', 'visual')]):
        return items, []
    current = items
    for kind in ('reaction', 'visual'):
        top = max((i['frames'] for i in current if i['kind'] == kind), default=0)
        if not top:
            continue
        at = lambda k, rows=current, kind=kind, top=top: [
            dict(i, frames=i['frames']*k//top) if i['kind'] == kind else i for i in rows]
        if not fits(at(0)):
            current = at(0)
            continue
        low, high = 0, top  # at(low) fits, at(top) does not
        while high-low > 1:
            middle = (low+high)//2
            low, high = (middle, high) if fits(at(middle)) else (low, middle)
        current = at(low)
        break
    from .dialogue_cut import MIN_FRAMES
    # A pause shrunk below a visible cut would only flash; dropping it only frees footage.
    current = [dict(b, frames=0) if b['kind'] == 'reaction' and b['frames'] != a['frames'] and
               b['frames'] < MIN_FRAMES else b for a, b in zip(items, current)]
    trims = [dict(item_id=a['item_id'], scene_id=a['scene_id'], kind=a['kind'], planned_frames=a['frames'],
                  kept_frames=b['frames']) for a, b in zip(items, current) if a['frames'] != b['frames']]
    return current, trims


def speech_capacity_preflight(scene, lines):
    """Test the physical eligible pool before paying for semantic approvals."""
    from .scene_coverage import capacity
    refs = {r['line_id']: r for r in scene['reference_lines']}
    anchors = speaker_anchors(scene, refs, lines)
    shots = {s['slot_id']: s for s in scene['coverage_shots']}
    items, roles = [], {}
    for line in lines:
        role = line['role']; rid = scene['role_bindings'][role]['role_id']
        ids = [key for key, shot in shots.items() if eligible(scene, shot, role) and
               (scene.get('visual_policy')=='continuous_dialogue_adaptation_v1' or not anchors[line['line_id']] or speaker_view(shot,rid))]
        frames = math.ceil(line['voiced_s']*FPS)
        items.append(dict(item_id=line['line_id'], frames=frames, shot_ids=ids))
        row = roles.setdefault(role, dict(tts_frames=0, eligible_shot_ids=set()))
        row['tts_frames'] += frames; row['eligible_shot_ids'].update(ids)
    # A line with a reusable shot can always be filled; only the others need unique footage.
    fresh_only = [i for i in items if not any(reusable(shots[k]) for k in i['shot_ids'])]
    return dict(scene_id=scene['scene_id'], feasible=_fits(items, shots) or _fits(fresh_only, shots),
        reusable_shot_ids=sorted(k for k, shot in shots.items() if reusable(shot)),
        pool_unique_s=capacity(list(shots.values())), demand_kind='actual_tts_not_reference_duration',
        roles={role: dict(tts_s=row['tts_frames']/FPS,
            eligible_unique_s=capacity([shots[i] for i in row['eligible_shot_ids']]),
            eligible_shot_ids=sorted(row['eligible_shot_ids'])) for role, row in roles.items()})


def unplaceable_roles(scene):
    """Bound on-screen speakers of this scene for whom no coverage shot is eligible. A scene
    without coverage shots is already missing material (prepare's empty_event)."""
    if not scene.get('coverage_shots'):
        return set()
    talking = {r.get('role') for r in scene.get('reference_lines', [])}
    return {role for role, binding in scene.get('role_bindings', {}).items()
            if role in talking and not binding.get('voice_only') and
            not any(eligible(scene, shot, role) for shot in scene.get('coverage_shots', []))}


def voice_unfilmed_roles(lock):
    """A supporting role with no speaking footage in its scene is voiced off screen there.

    03 (retry): one stable stall window held the two leads speaking, but the
    four-line stall keeper's actor never opened his mouth, so the whole film fell
    to L2. Leads (the two roles with the most reference characters) still need
    their own mouths; any other first speaker without speech in the chosen footage
    keeps every line, voiced over its addressee's reaction (off-screen voices are
    ordinary in dubbing practice, arXiv 2212.12137). Recorded per scene."""
    from .scene_coverage import lead_roles
    lines = [r for scene in lock['scenes'] for r in scene.get('reference_lines', [])]
    chars = {r['role'] for r in lines if r.get('role')}
    leads = lead_roles(lines)   # shared with casting (scene_coverage.cast_for_story)
    notes = []
    for scene in lock['scenes']:
        spoken = {r['role'] for r in scene.get('reference_lines', []) if r.get('on_screen', True)}
        required = spoken - set(scene.get('introduced_roles', []))
        available = {s.get('role') for s in scene.get('speech_slots', [])}
        unbound = {r for r in scene.get('roles', []) if r not in scene['role_bindings'] and r in chars}
        # 25 (501ed20f): a bound speaker visible in every shot but never speaking alone had
        # no usable shot at all (max_chars 0, allowed []), yet had to keep its line on
        # screen; three script repairs could not satisfy that.
        unplaceable = unplaceable_roles(scene)
        for role in sorted((required - available | unbound | unplaceable) - leads):
            binding = scene['role_bindings'].get(role, {})
            if binding.get('voice_only'):
                continue
            ident = 'voice_' + json_sha(role)[:12]
            if role not in scene.get('roles', []):
                scene['roles'] = [*scene.get('roles', []), role]
            scene['role_bindings'][role] = dict(window_id=(scene.get('window_ids') or [None])[0], role_id=ident,
                actor=role, entity_id=ident, appearance='选定画面中没有该角色开口，台词改为画外发言',
                gender=binding.get('gender', 'unclear'), voice_only=True)
            # A never-cast role is absent from cast_bindings, which voices_for reads
            # (GLM review: KeyError in the script prompt); register it like
            # scene_coverage.bind_offscreen_voices does.
            lock.setdefault('cast_bindings', {}).setdefault(role, dict(actor=role, gender='unclear', voice_only=True,
                bindings=[], entity_ids=[], entity_bindings={}))
            lock.setdefault('cast', {}).setdefault(role, role)
            for r in scene['reference_lines']:
                if r['role'] == role and r.get('on_screen', True):
                    r.update(on_screen=False, offscreen_reason='no_speaking_footage_voiced_offscreen')
            notes.append(dict(scene_id=scene['scene_id'], role=role, previous_role_id=binding.get('role_id')))
    return notes


REFIT_REVIEW_BASE = 100   # re-review call numbers after a refit (stage names allow digits only)


def refit_prompt(script, lock, refit):
    scene = next(s for s in lock['scenes'] if s['scene_id'] == refit['scene_id'])
    refs = {r['line_id']: r for r in scene['reference_lines']}
    rows = []
    for line_id in refit['line_ids']:
        line = next(r for r in script['lines'] if r['line_id'] == line_id)
        shots = [s for s in scene['coverage_shots'] if eligible(scene, s, line['role'])]
        rows.append(dict(line_id=line_id, role=line['role'], text=line['text'], max_chars=_adapt_limit(line['text']),
            must_preserve=[refs[i].get('must_preserve') or refs[i].get('function') for i in line.get('reference_line_ids', []) if i in refs],
            verbatim_terms=sorted(key_terms(scene, line)),
            offscreen=all(refs[i].get('on_screen') is False for i in line.get('reference_line_ids', []) if i in refs),
            eligible_shots=[{k: s.get(k) for k in ('slot_id', 'place', 'action', 'visible', 'speaking', 'listeners', 'objects')}
                            for s in shots]))
    context = [dict(line_id=r['line_id'], role=r['role'], text=r['text']) for r in script['lines']
               if r.get('scene_id') == refit['scene_id']]
    return ('镜头审核认为以下台词在现有素材里没有能承载的镜头，原因见issues。素材有限，不能更换素材。'
        '逐句改写text，使它能被eligible_shots中镜头的实际人物、动作、对话对象承载：画外旁白或画外呼叫可改为描述、'
        '回应画面里正在发生的事，或对画面中人物说话；保持must_preserve的核心信息、语气及与上下句的问答关系；'
        'verbatim_terms中的关键原词必须逐字保留，即使镜头看不到也不能替换或删去；'
        '不新增情节，不删句，不改说话角色；字数不超过max_chars。visible_basis写改写后对应的可见画面。'
        '按给定顺序逐句返回。只返回JSON {"lines":[{"line_id":"l1","text":"改写后台词","visible_basis":"对应的可见画面"}]}。\n' +
        json.dumps(dict(issues=refit['issues'], lines=rows, scene_dialogue=context), ensure_ascii=False))


def validate_refit(value, refit, script, lock):
    rows = value.get('lines') if isinstance(value, dict) else None
    ids = refit['line_ids']
    if not isinstance(rows, list) or [r.get('line_id') if isinstance(r, dict) else None for r in rows] != ids:
        raise ValueError('lines须按顺序逐句返回：' + json.dumps(ids, ensure_ascii=False))
    problems = []
    for row in rows:
        original = next(r for r in script['lines'] if r['line_id'] == row['line_id'])
        text = row.get('text')
        if not isinstance(text, str) or not text.strip():
            problems.append(f'{row["line_id"]}: text不能为空')
        elif _chars(text) > _adapt_limit(original['text']):
            problems.append(f'{row["line_id"]}: 共{_chars(text)}字，不超过{_adapt_limit(original["text"])}字')
        if not isinstance(row.get('visible_basis'), str) or not row['visible_basis'].strip():
            problems.append(f'{row["line_id"]}: visible_basis须写对应的可见画面')
    if problems:
        raise ValueError('须同时修复：' + '；'.join(problems))
    adapted = deepcopy(script)
    for row in rows:
        next(r for r in adapted['lines'] if r['line_id'] == row['line_id'])['text'] = row['text'].strip()
    validate_script(adapted, lock)
    return value


def refit_to_footage(script, lock, refit, state, glm, output, number):
    """03 (80701c61): the reviewer found no shot for a narration about a passer-by and an
    off-screen call; the only remedy was reselection, which returned the same pool, so the
    film fell to L1. Picture-first / reinterpret-first: rewrite those lines once to what
    the footage shows (same mechanism as adapt_to_picture, for actions and events).
    Returns the adapted script, or None to keep the original failure."""
    from .pipeline import _fallback_applies, _material_call
    record = dict(policy='refit_lines_to_footage_v1', scene_id=refit['scene_id'], line_ids=refit['line_ids'],
                  issues=refit['issues'], changes=[])
    def validate(value):
        return validate_refit(value, refit, script, lock)
    validate.repair_limit = 1
    try:
        reply = _material_call(state, glm, f'material_scene_adapt_2_{number}', refit_prompt(script, lock, refit), validate)
    except Exception as error:  # noqa: BLE001 - the original coverage failure still decides
        if (isinstance(error, LibraryStopped) and str(error).startswith(INTEGRITY_STOPS) or
                not _fallback_applies(state.input_lock['configuration'], output, error)):
            raise
        record.update(status='kept_original', error=f'{type(error).__name__}:{str(error)[:300]}')
        state.set_artifact('scene_script_refit', record)
        write_json(Path(output)/'script_refit.json', record)
        return None
    adapted = deepcopy(script)
    for row in reply['lines']:
        line = next(r for r in adapted['lines'] if r['line_id'] == row['line_id'])
        record['changes'].append(dict(line_id=row['line_id'], before=line['text'], after=row['text'].strip(),
                                      visible_basis=row['visible_basis']))
        line.update(text=row['text'].strip(), refit_from=line['text'], visible_basis=row['visible_basis'])
    record['status'] = 'refit'
    state.set_artifact('scene_script_refit', record)
    write_json(Path(output)/'script_refit.json', record)
    return adapted


def omit_unfilmed_silent_roles(lock):
    """A role without any line in a dialogue scene is only an onlooker there; when the
    chosen footage never shows its actor it is omitted from that scene instead of
    failing the film (15 at 8b67dc78: an onlooker bound to an actor absent from every
    coverage shot). Reinterpret-first rule: the picture needs only its minimal visible
    conditions. Scenes without dialogue keep every role: there the roles are the event."""
    notes = []
    for scene in lock['scenes']:
        if not scene.get('reference_lines'):
            continue
        talking = {r.get('role') for r in scene['reference_lines']}
        visible = {r for shot in scene.get('coverage_shots', []) for r in shot['visible']}
        for role in [r for r in scene.get('roles', []) if r not in talking]:
            binding = scene['role_bindings'].get(role)
            if binding and (binding.get('voice_only') or binding.get('role_id') in visible):
                continue
            scene['roles'] = [r for r in scene['roles'] if r != role]
            scene['role_bindings'].pop(role, None)
            scene.setdefault('omitted_silent_roles', []).append(role)
            notes.append(dict(scene_id=scene['scene_id'], role=role,
                              previous_role_id=(binding or {}).get('role_id')))
    cast = {role for scene in lock['scenes'] for role in scene.get('role_bindings', {})}
    for role in {n['role'] for n in notes} - cast:
        lock.get('cast_bindings', {}).pop(role, None)   # no scene shows or voices it any more
    return notes


def settle_offscreen_roles(lock):
    """Voicing a role off screen moves its first on-screen line to a later scene, which
    may itself lack speech footage (GLM review): recompute introductions until stable.
    Only on-screen -> off-screen flips happen, so this terminates."""
    voiced = []
    while True:
        introduce_roles(lock)
        more = voice_unfilmed_roles(lock)
        if not more:
            return voiced
        voiced += more


def drop_unfit_visual_scenes(lock):
    """A scene without dialogue is only cut in when its picture fits (user 11-11).

    03 (fc6699b6): the closing 'fight and drive-off' had no fitting footage; the check said
    setting_match=false, but the closest window (a ribbon dance) was kept for 17 s. Without
    words nothing explains such a picture, so the film ends on the last line instead
    (user 11-04: footage unrelated to the mood may be cut). Dialogue scenes are never dropped."""
    kept = [s for s in lock['scenes'] if s.get('reference_lines') or (s.get('picture') or {}).get('setting_match') is not False]
    dropped = [dict(scene_id=s['scene_id'], reason=(s.get('picture') or {}).get('setting_reason', ''))
               for s in lock['scenes'] if s not in kept]
    if dropped and kept:
        lock['scenes'] = kept
        return dropped
    return []


def prepare(lock, windows, reference, state, glm, cache, output, *, voice_service=None, script_review=None):
    from .pipeline import _material_call
    from .dialogue_cut import _speech, voices_for
    dropped = [] if lock.get('reference_contract') else drop_unfit_visual_scenes(lock)
    if dropped:
        state.set_artifact('scene_unfit_visual_dropped', dict(policy='drop_unfit_visual_scene_v1', scenes=dropped))
    omitted = omit_unfilmed_silent_roles(lock)
    if omitted:
        state.set_artifact('scene_unfilmed_silent_roles_omitted', dict(policy='omit_unfilmed_onlookers_v1', roles=omitted))
    voiced = settle_offscreen_roles(lock)
    voices = voice_service.voices if voice_service else voices_for(lock)
    if voiced:
        state.set_artifact('scene_unfilmed_roles_voiced', dict(policy='voice_unfilmed_supporting_roles_v1', roles=voiced))
    missing_scenes = {}
    for scene in lock['scenes']:
        # Only roles speaking on screen for the first time here need their own speech
        # footage; introduced roles may be voiced over listeners (speaker_anchors).
        required = ({r['role'] for r in scene['reference_lines'] if r.get('on_screen', True)} -
                    set(scene.get('introduced_roles', [])))
        available = {s.get('role') for s in scene.get('speech_slots', [])}
        bound = {b['role_id'] for b in scene['role_bindings'].values() if not b.get('voice_only')}
        visible = {r for s in scene.get('coverage_shots', []) for r in s['visible']}
        unbound = set(scene['roles']) - set(scene['role_bindings'])
        # A lead with no usable shot stops here as missing material, not as three
        # unsatisfiable script repairs (supporting roles were voiced off screen above).
        unplaceable = unplaceable_roles(scene)
        if (unbound or required - available or not scene.get('coverage_shots') or bound - visible or
                scene.get('unverified_cast') or unplaceable):
            missing_scenes[scene['scene_id']] = dict(unbound_roles=sorted(unbound), speakers=sorted(required-available),
                invisible_ids=sorted(bound-visible), unverified_cast=scene.get('unverified_cast', []),
                empty_event=not bool(scene.get('coverage_shots')), unplaceable_roles=sorted(unplaceable))
    if missing_scenes:
        raise CoverageError(missing_scenes, json.dumps(missing_scenes, ensure_ascii=False),
                            reason='scene_material_missing')
    for scene in lock['scenes']:
        for row in scene['reference_lines']:
            a,b=row.get('start_s'),row.get('end_s')
            if not (type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b) and 0<=a<b):
                raise ValueError('reference_line_timing_required_before_generation:'+str(row.get('line_id')))
    number = 1+sum(c['name'].startswith('material_scene_script_1_') and '_repair' not in c['name']
                   for c in state.data['calls'])
    script = write_script(lock, reference, state, glm, number)
    script = adapt_to_picture(script, lock, state, glm, output, number)
    if script_review:
        script = script_review(script, lock, number)
    script['facts'] = [dict(scene_id=s['scene_id'], line_id=r['line_id'], text=r['text'],
        requirement=r.get('must_preserve', r.get('function')), verbatim_terms=sorted(key_terms(s, dict(reference_line_ids=[r['line_id']]))))
        for s in lock['scenes'] for r in s['reference_lines']]
    write_json(output/'dialogue_script.json', script)
    lines, samples_by_id = [], {}
    for raw in script['lines']:
        line = dict(raw, id=f'line_{len(lines)+1}', mode='dialogue', picture_relation='planned_dialogue')
        scene = next(s for s in lock['scenes'] if s['scene_id'] == line['scene_id'])
        binding = scene['role_bindings'][line['role']]
        line['speaker'] = {k: binding[k] for k in ('window_id', 'role_id')}
        samples = (voice_service.render_line(line, voices[line['role']]) if voice_service else
                   _speech(line, voices[line['role']], state, output))
        if not len(samples):
            raise LibraryStopped('dialogue_silent_line:' + line['line_id'])
        lines.append(line)
        samples_by_id[line['line_id']] = samples
    prepared = dict(lock=lock, windows=windows, voices=voices, script=script, lines=lines,
                    samples=samples_by_id, cache=Path(cache))
    review_number = number
    while True:
        try:
            allocation, timing = _allocate(lock, lines, script, state, glm, output, review_number)
            break
        except CoverageError as exc:
            refit = getattr(exc, 'refit', None)
            adapted = (refit_to_footage(script, lock, refit, state, glm, output, number)
                       if refit and review_number == number else None)
            if adapted is None:
                exc.prepared = prepared
                raise
            if script_review:
                adapted = script_review(adapted, lock, number + REFIT_REVIEW_BASE)
            # Only the rewritten lines are synthesised again, on copies: if any of them
            # fails, the original lines and speech stay and the original failure decides
            # (GLM review: a failed re-synthesis must not stop a run that had full speech).
            fresh = {}
            try:
                for line in lines:
                    row = next(r for r in adapted['lines'] if r['line_id'] == line['line_id'])
                    if row['text'] != line['text']:
                        trial = dict(line, text=row['text'], refit_from=row.get('refit_from'),
                                     visible_basis=row.get('visible_basis'))
                        samples = (voice_service.render_line(trial, voices[line['role']]) if voice_service else
                                   _speech(trial, voices[line['role']], state, output))
                        if not len(samples):
                            raise LibraryStopped('dialogue_silent_line:' + line['line_id'])
                        fresh[line['line_id']] = (trial, samples)
            except Exception as error:  # noqa: BLE001 - keep the complete original speech
                if isinstance(error, LibraryStopped) and str(error).startswith(INTEGRITY_STOPS):
                    raise
                state.set_artifact('scene_script_refit_resynthesis', dict(status='kept_original',
                    error=f'{type(error).__name__}:{str(error)[:300]}'))
                exc.prepared = prepared
                raise exc
            script = adapted
            write_json(output/'dialogue_script.json', script)
            for line in lines:
                if line['line_id'] in fresh:
                    trial, samples = fresh[line['line_id']]
                    line.clear(); line.update(trial)
                    samples_by_id[line['line_id']] = samples
            prepared.update(script=script)
            review_number = number + REFIT_REVIEW_BASE
    timeline = to_timeline(allocation, lock, windows)
    audio_lines, captions = place_audio(lines, timeline.rows, samples_by_id, cache, 'finite_joint_schedule')
    write_json(output/'joint_schedule.json', dict(policy='joint_av_v1', timing=timing, allocation=allocation))
    return voices, script, timeline, lines, audio_lines, captions


def _allocate(lock, lines, script, state, glm, output, number):
    """Approve shot uses and allocate finite footage; CoverageError when it cannot hold the speech."""
    shots = {s['slot_id']: s for scene in lock['scenes'] for s in scene['coverage_shots']}
    items, timing = [], []
    for scene_index, scene in enumerate(lock['scenes'], 1):
        sid = scene['scene_id']
        rows = [r for r in lines if r['scene_id'] == sid]
        target = round((scene['end_s']-scene['start_s'])*FPS)
        if not rows:
            visual = next(v for v in script['visual_scenes'] if v['scene_id'] == sid)
            from .scene_coverage import capacity
            selected = list(visual['shot_ids'])
            available = math.floor(capacity([shots[i] for i in selected])*FPS)
            frames = min(target, available)
            if frames <= 0:
                raise CoverageError(sid, 'no visual event coverage', reason='no_visual_event_coverage')
            items.append(dict(item_id=sid+'_visual', scene_id=sid, frames=frames, shot_ids=selected,
                              kind='visual', reason=visual['action_reason']))
            timing.append(dict(scene_id=sid, reference_s=target/FPS, planned_s=frames/FPS))
            continue
        refs = {r['line_id']: r for r in scene['reference_lines']}
        if rows and not refs:
            # Speech can only migrate actual reference lines; a scene without them
            # is planned as a visual event, so a stray dialogue row is a stop, not
            # an index crash over an empty reference set.
            raise LibraryStopped('dialogue_scene_without_reference_lines:' + sid)
        preflight = speech_capacity_preflight(scene, rows)
        state.set_artifact('joint_capacity_preflight', preflight)
        if not preflight['feasible']:
            raise CoverageError(sid, 'physical_pool_insufficient:' + json.dumps(preflight, ensure_ascii=False),
                                reason='physical_pool_insufficient')
        review = review_shot_uses(scene, rows, state, glm, f'material_scene_schedule_{scene_index}_{number}',
                                  film_cast(lock, scene))
        # Preserve the writer's ranking among independently approved options.
        for row in rows:
            proposed={ident:i for i,ident in enumerate(row['shot_ids'])}
            row['shot_options'].sort(key=lambda o:proposed.get(o['shot_id'],len(proposed)))
        write_json(output/f'shot_compatibility_{sid}.json', review)
        speech_frames = [math.ceil(r['voiced_s']*FPS) for r in rows]
        gaps = [max(0, refs[rows[0]['reference_line_ids'][0]]['start_s']-scene['start_s'])]
        for i, row in enumerate(rows):
            end = refs[row['reference_line_ids'][-1]]['end_s']
            following = refs[rows[i+1]['reference_line_ids'][0]]['start_s'] if i+1 < len(rows) else scene['end_s']
            gaps.append(max(0, following-end))
        silence = review['pause_shots']
        from .scene_coverage import capacity
        # Listener footage is shared by split edits and pauses. Reserve what
        # complete utterances need before spending the remainder on pacing.
        reaction_needed = 0
        for role, binding in scene['role_bindings'].items():
            needed = sum(frames for row, frames in zip(rows, speech_frames) if row['role'] == role)
            approved = {o['shot_id'] for row in rows if row['role'] == role for o in row['shot_options']}
            speaking = [s for s in scene['coverage_shots']
                        if s['slot_id'] in approved and speaker_view(s,binding['role_id'])]
            reaction_needed += max(0, needed-math.floor(capacity(speaking)*FPS))
        gap_budget = min(max(0, target-sum(speech_frames)),
                         max(0, math.floor(capacity([shots[i] for i in silence])*FPS)-reaction_needed))
        weights = sum(gaps)
        gap_frames = [round(gap_budget*sum(gaps[:i+1])/weights)-round(gap_budget*sum(gaps[:i])/weights)
                      if weights else 0 for i in range(len(gaps))]
        anchors = speaker_anchors(scene, refs, rows)
        for i, row in enumerate(rows):
            if gap_frames[i]:
                items.append(dict(item_id=f'{sid}_pause_{i}', scene_id=sid, frames=gap_frames[i],
                    shot_ids=silence, kind='reaction', reason='保留参考回答前的停顿与同场反应'))
            anchor = anchors[row['line_id']]
            allowed = [o['shot_id'] for o in row['shot_options']]
            rid = scene['role_bindings'][row['role']]['role_id']
            own=[ident for ident in allowed if speaker_view(shots[ident],rid)]
            if not allowed or anchor and not own:
                raise CoverageError(sid, row['line_id']+' has no correct speaker anchor', reason='no_speaker_anchor')
            row['visual_anchor'] = anchor
            common=dict(line_id=row['line_id'],scene_id=sid,stable_conversation=scene.get('visual_policy')=='continuous_dialogue_adaptation_v1',
                role=row['role'],kind='speech',reason=row['visual_reason'],speaking_ids=own,
                shot_reasons={o['shot_id']:o['reason'] for o in row['shot_options']})
            if anchor and scene.get('visual_policy')=='continuous_dialogue_adaptation_v1':
                # Establish the new voice on its own identifiable performer;
                # the uninterrupted remainder may use a genuine listener.
                bound={b['role_id'] for b in scene['role_bindings'].values()}
                own=[key for key in own if anchor_view(shots[key],rid,bound)]
                if not own:raise CoverageError(sid,'no unambiguous first appearance for '+row['role'],reason='no_speaker_anchor')
                available=max(round((shots[k]['source_end_s']-shots[k]['source_start_s'])*FPS) for k in own)
                intro=min(speech_frames[i],round(.8*FPS),available)
                items.append(dict(common,item_id=row['line_id']+'_intro',anchor=True,frames=intro,shot_ids=own))
                if speech_frames[i]>intro:
                    items.append(dict(common,item_id=row['line_id']+'_body',anchor=False,frames=speech_frames[i]-intro,shot_ids=allowed))
            else:
                items.append(dict(common,item_id=row['line_id'],anchor=anchor,frames=speech_frames[i],shot_ids=own if anchor else allowed))
        if gap_frames[-1]:
            items.append(dict(item_id=sid+'_pause_end', scene_id=sid, frames=gap_frames[-1],
                shot_ids=silence, kind='reaction', reason='保留参考收尾反应'))
        timing.append(dict(scene_id=sid, reference_s=target/FPS, speech_s=sum(speech_frames)/FPS,
                          planned_s=(sum(speech_frames)+sum(gap_frames))/FPS))
    planned = items
    if not _fits([i for i in items if i['kind'] == 'speech'], shots):
        # Fresh footage cannot hold the speech: cut the found exchange to the new dialogue
        # and reuse its static shots as often as needed (user 11-11).
        write_json(output/'joint_schedule_input.json', dict(items=items, shots=shots, timing=timing, policy=REUSE_POLICY))
        allocation = reuse_schedule(items, shots)
        reused = [r for r in allocation if r.get('reused')]
        state.set_artifact('joint_shot_reuse', dict(policy=REUSE_POLICY,
            reused_s=round(sum(r['last']-r['first'] for r in reused)/FPS, 2),
            total_s=round(sum(r['last']-r['first'] for r in allocation)/FPS, 2),
            reused_shots=sorted({r['slot_id'] for r in reused})))
        for row in timing:
            kept = sum(r['last']-r['first'] for r in allocation if r['scene_id'] == row['scene_id'])/FPS
            if abs(kept-row['planned_s']) > 1/FPS:
                row['pacing_trimmed_s'] = round(row['planned_s']-kept, 3)
                row['planned_s'] = kept
        return allocation, timing
    items, trims = fit_pacing(items, shots)
    from .dialogue_cut import MIN_FRAMES
    for trim in trims:
        row = next(t for t in timing if t['scene_id'] == trim['scene_id'])
        row['planned_s'] -= (trim['planned_frames']-trim['kept_frames'])/FPS
        row['pacing_trimmed_s'] = row.get('pacing_trimmed_s', 0)+(trim['planned_frames']-trim['kept_frames'])/FPS
    write_json(output/'joint_schedule_input.json', dict(items=items, shots=shots, timing=timing,
                                                        planned_items=planned if trims else None, pacing_trims=trims))
    for trim in trims:
        if trim['kind'] == 'visual' and trim['kept_frames'] < MIN_FRAMES:
            raise CoverageError(trim['scene_id'], 'visual event footage is taken by shared speech',
                                reason='no_visual_event_coverage')
    return schedule(items, shots), timing


def to_timeline(allocation, lock, windows):
    from .dialogue_cut import _Timeline
    shots = {s['slot_id']: s for scene in lock['scenes'] for s in scene['coverage_shots']}
    timeline = _Timeline(windows)
    scenes = {s['scene_id']: s for s in lock['scenes']}
    for piece in allocation:
        scene = scenes[piece['scene_id']]
        shot = shots[piece['slot_id']]
        visible = [role for role, binding in scene['role_bindings'].items() if binding['role_id'] in shot['visible']]
        role = (piece['voice_role'] if piece['voice_role'] in visible else visible[0] if visible else
                piece['voice_role'] or next(iter(scene['role_bindings'])))
        kind = piece['kind']
        if kind == 'speech' and not speaker_view(shot,scene['role_bindings'][piece['voice_role']]['role_id']):
            kind = 'reaction'
        row = timeline.append(scene, dict(shot, role=role), piece['first']/FPS, piece['last']-piece['first'], kind, piece['line_id'])
        row.update(role_ids=[scene['role_bindings'][r]['role_id'] for r in visible], visible_roles=visible,
                   speaking_role_ids=shot['speaking'], observed_action=shot['action'], planned_use=piece['reason'])
        if 'degraded' in piece:
            row['degraded'] = piece['degraded']
        if piece.get('reused'):
            row['reused_shot'] = True
    return timeline


def place_audio(lines, rows, samples_by_id, cache, fit_action):
    """Each complete utterance starts with its first picture row and must end inside its own rows."""
    audio_lines, captions = [], []
    for line in lines:
        pieces = [p for p in rows if p['line_id'] == line['line_id']]
        line.update(start_s=pieces[0]['start_s'], end_s=pieces[-1]['end_s'],
                    segment_ids=[p['segment_id'] for p in pieces], fit_action=fit_action)
        path = Path(cache)/(line['id']+'.wav')
        samples = samples_by_id[line['line_id']]
        audio_render._wav(path, samples)
        end = line['start_s']+len(samples)/audio_render.SAMPLE_RATE
        if end > line['end_s']+1/FPS:
            raise LibraryStopped('joint_schedule_audio_truncated:' + line['line_id'])
        audio_lines.append(dict(id=line['id'], audio_path=str(path), start_s=line['start_s'], end_s=end))
        captions.append(dict(text=line['text'], start_s=line['start_s'], end_s=end))
    return audio_lines, captions
