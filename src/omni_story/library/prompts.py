"""Model-owned observations and decisions. No human-written plot or source picks."""
from __future__ import annotations

import json
import re

from .contracts import creative_reference_contract, storyboard_core_cell_repair_hint

POLICY_VERSION = "reference_library_glm_mcp_v1"
BASE = """你是参考驱动电影素材库剪辑系统。严格区分可见事实、外部电影常识、
本次表达用途和未核验推测。参考主旨固定；素材可以改变段落数、事件和顺序。
本任务是异源素材的主旨迁移：允许改变参考的具体人物、人物属性、纪实/动画形式和具体情节。
必须保留的是参考想让观众理解的意义及其行动、变化和结果关系，而不是复制参考人物的传记事实。
reference.theme若写成了特定故事标题，应结合intended_takeaway与实际证据区分主旨和故事事实；
不能仅因换了人物或片种判定主旨失败，也不能仅有相似关键词就判定成功。
人物身份与状态、因果联系需要画面证据。不得捏造未看素材、时间区间或成功评价。
只返回一个JSON对象，无Markdown或解释。所有文字可中文，所有ID使用ASCII。
GLM视觉MCP当前不保证音频理解：未提供ASR或音频测量时，不声称听到了台词、音乐或节拍。
"""


def reference_prompt(reference_sha: str, duration_s: float) -> str:
    contract = creative_reference_contract()
    example = {"viewer_takeaway": contract['format_example']['viewer_takeaway'],
        "reference_sha256": reference_sha, "theme": "主旨", "intended_takeaway": "观众应该理解什么",
        "visible_evidence": [{"start_s": 0, "end_s": min(1, duration_s),
            "observed_fact": "实际画面", "supports": "支持哪一关系"}],
        "editing_methods": [{"method": "剪法", "function": "作用", "visual_evidence": "画面证据",
            "start_s": 0, "end_s": min(1, duration_s)}], "uncertainties": ["未确认事项"],
        **contract['format_example']}
    example['value_judgment']['evidence'][0]['end_s'] = min(1, duration_s)
    return """请先写必填字段viewer_takeaway（非空字符串），像普通观众一样，用一两句话说：这个视频想让观众明白什么？
只说内容和主旨，不讲剪辑手法或拍摄手法，不需要列证据；可结合常识，包括你知道的影视作品、网络梗和社会常识。
viewer_takeaway是唯一允许推断的字段，不受其余字段的事实/推测区分和证据要求限制。
先写viewer_takeaway，再写其他字段；theme、intended_takeaway、value_judgment等应与它一致，不得互相矛盾。
其余字段的事实/推测区分规则保持不变，不得把viewer_takeaway中的推断当成可见事实或已核验台词。
""" + BASE.replace('人物身份与状态、因果联系需要画面证据。',
        '人物身份与状态、因果联系作为可见事实时需要画面证据。') + f"""完整观看本条固定参考（SHA={reference_sha}，时长{duration_s}秒）。
分析主旨所需的关系、行动和结果，而非仅关键词或情绪。剪法区分形式和表达作用；
镜头切点为观看估计，音乐卡点待核验。
value_judgment回答“表达了什么价值/态度”，core_message用一句话直接说明，不写剪辑方法。
theme继续保留可迁移主旨，intended_takeaway继续说明观众应该理解什么；editing_methods继续写剪法，三者不要互相替代。
不要求所有视频都讲道德，情感、讽刺、幽默也算；what_is_upheld写肯定或珍视的事物，情感类写情感对象；
what_is_rejected写否定或嘲讽的事物，没有则写“无”。stance从{'|'.join(contract['stance_values'])}选一个，可用冒号或括号简短说明。
其余字段的参考理解允许使用作品/角色背景、梗、谐音、社会语境等外部文化常识；仍须严格区分可见事实、ASR与外部常识。
其余字段使用的外部常识必须逐条写进cultural_context并标明confidence，不得冒充画面或台词事实，也不必仅放入uncertainties。
kind仅可{'|'.join(contract['cultural_kinds'])}，confidence仅可{'|'.join(contract['confidence_values'])}；supports说明它支撑主旨的哪部分。
没有使用外部文化常识时cultural_context为[]；低置信解释标low并在uncertainties保留未确认事项。
value_judgment.evidence至少一条，必须引用参考内的start_s/end_s，basis仅可{'|'.join(contract['evidence_bases'])}：
visible为实际画面或可见字幕，asr仅引用本地ASR传入的转写且保留识别不确定性，cultural引用cultural_context中的常识。
文化常识的时间指其在参考中被触发或用于解释的区间，note指出所引用的claim及其支持关系，不代表常识由画面或台词证实。
{contract['repair_instruction']}
以下仅为JSON格式示例，不能照抄为观察事实。输出：
""" + json.dumps(example, ensure_ascii=False)


def coarse_prompt(source_id: str, coverage_s: list, frame_times: list, context: dict) -> str:
    return BASE + """这是抽样联系表，不是连续原片。每幅图旁的时间是原电影时间；
只能记录看到的角色与瞬间，不能把图间空白补成事件，也不能输出精确剪辑入出点。
用任务需求指导观察但保留其他可能有价值的事件；没有看见不等于电影没有。
同一角色尽量复用已有ID，身份不明确用候选ID并写入uncertainties。
输出：{"source_id":"ID","coverage_s":[0,1],"roles":[{"role_id":"candidate_A","description":"实际外形"}],
"events":[{"timestamp_s":0,"observed_fact":"可见事实","role_ids":["candidate_A"]}],"uncertainties":[]}\n""" + json.dumps(
        {"source_id": source_id, "coverage_s": coverage_s, "frame_times": frame_times, "context": context},
        ensure_ascii=False)


def search_prompt(context: dict) -> str:
    from .content_ranges import content_range_prompt
    template = """依据固定参考和已观察素材提出下一轮精看请求。先考虑整条人物路线是否有
足够可见证据，再安排具体slots。可以补上下文或探索未观察区域；不要拿电影常识当已看片。
一次请求不超过__MAX_WINDOWS__个连续窗口，每窗口不超过90秒，区间必须在catalog实际时长内。
role_ids必须始终提供，可为[]。slot_id若提供，须匹配[A-Za-z][A-Za-z0-9_-]{0,63}。
question具体指出要确认的动作、身份状态、结果或衔接。先输出windows，再用简短reason说明目前缺项与路线考虑。输出：
{"windows":[{"source_id":"ID","start_s":0,"end_s":30,
"question":"需要确认什么","role_ids":["candidate_A"]}],"reason":"简短说明目前缺项与路线考虑"}\n"""
    return BASE + content_range_prompt(context.get("catalog", {})) + template.replace("__MAX_WINDOWS__", str(context.get("max_windows_this_round", 12))) + json.dumps(context, ensure_ascii=False)


def fine_prompt(window: dict, context: dict) -> str:
    from .content_ranges import content_range_prompt
    return BASE + content_range_prompt(window) + """这是按请求取得的真实连续窗口。下列local时间从该小片的0秒开始，
不能填原电影秒数。仅把实际观看且人物身份确认的片段放入usable_ranges。
角色ID可以从候选修正；identity_confirmed必须基于此窗口画面身份线索，不能仅凭电影常识。
记录每个角色的外形阶段、服装/道具/伤势等状态和适用的连续性限制。
usable_ranges可用camera描述实际环境或细节画面；event_indices=[]时必须role_ids=[]且camera为非空字符串。
输出：{"window_id":"ID","source_id":"ID","roles":[{"role_id":"A","identity_confirmed":true,
"state":"实际状态","identity_evidence":"身份线索"}],
"events":[{"local_start_s":0,"local_end_s":1,"observed_fact":"动作与可见结果","role_ids":["A"]}],
"usable_ranges":[{"local_in_s":0,"local_out_s":1,"event_indices":[0],"role_ids":["A"],
"camera":"实际可见环境、景别或细节","continuity_notes":"前提、结尾与状态限制"}],"uncertainties":[]}\n""" + json.dumps(
        {"window": window, "context": context}, ensure_ascii=False)


def storyboard_plan_prompt(template, context: dict, *, max_duration_s=None, max_segments=None) -> str:
    """Wrap the selected route template, leaving legacy request bytes untouched."""
    storyboard = context.get('reference', {}).get('reference_storyboard')
    if storyboard is None:
        return template(context)
    from .storyboard import storyboard_targets
    targets = storyboard_targets(storyboard, context['reference_duration_s'],
                                 max_duration_s=max_duration_s, max_segments=max_segments)
    context = {**context, 'storyboard_targets': targets,
               'storyboard_merges': context['reference'].get('storyboard_merges', [])}
    prompt = template(context).replace('自主决定段落和片段，保留参考的主旨与有证据的剪辑意图。',
        '按分镜的段落顺序、对话轮次和节奏组织段落和片段，保留参考主旨；画面内容和台词可以重释。')
    prompt = prompt.replace('不必复刻参考故事结构。', '按分镜的段落顺序、对话轮次和节奏组织；画面内容和台词可以重释。')
    prompt = prompt.replace('素材可以改变段落数、事件和顺序。', '画面与台词可以重释，段落顺序、对话轮次和节奏按分镜组织。')
    if 'max_segments' in targets['caps_applied']:
        prompt += ('\n本次核心格数超过max_segments：一个slot的storyboard_cell_ids可列出多个核心格，'
            '每个slot只需至少一个segment即可覆盖其列出的各核心格，不要求每个核心格独占segment。'
            '仍须按分镜顺序完整承载所有核心格，核心格不得写入storyboard_merges。')
    return prompt + '''
storyboard_targets为本次粗剪合同：sum((source_out_s-source_in_s)/speed)须在target_duration_s区间内，
不以定格充当故事时长。cells.suggested_s供逐格安排停留。每个slot必须输出storyboard_cell_ids:[格ID]，
核心格至少由一个含segment的slot承载。slot_id沿用context.slots的节拍ID时，程序只补齐缺失的格ID并记录plan_normalisations。
顶层输出storyboard_merges:[{cell_id,into_slot_id,reason}]，明确非核心格并入相邻slot的原因。
''' + storyboard_core_cell_repair_hint() + '''
并入格的台词由同一角色的反应镜头或环境镜头承载，不得重复使用同一段原片。
搜索阶段成功合并也计入总格数的30%上限（向上取整、按格去重）；保留其目标slot_id，或在计划合并表明确新的into_slot_id。
未合并格必须由slot承载，不能无声丢掉。speech_mode为dialogue/mixed/narration时，audio_draft句数
至少为dialogue_lines_min，每句挂在承载相应格的segment_ids；speech_mode=none跳过句数下限。
素材不足或时长不足时自行调整真实可用画面的选择、切点和文稿；程序不会补写或自动拉长创作内容。
'''


def landscape_plan_prompt(prompt, context):
    """Adapt archived examples only for locks that opted into the fixed canvas."""
    from .plan_normalisation import LANDSCAPE_CANVAS, landscape_canvas_key
    if context.get('output_canvas') != landscape_canvas_key():
        return prompt
    width, height = LANDSCAPE_CANVAS
    prompt = prompt.replace('"width":720,"height":1280', f'"width":{width},"height":{height}', 1)
    return prompt + (f'\n输出画布固定为横屏{width}×{height}，width={width}、height={height}；'
        '不按参考片或父片改画布，fps沿用原合同，framing仍由你选择fit或crop。')


def plan_prompt(context: dict) -> str:
    from .content_ranges import content_range_prompt
    from .line_windows import line_window_prompt
    audio_stream = context.get("reference_audio_stream_index")
    has_reference_audio = type(audio_stream) is int and audio_stream >= 0
    audio_example = {"start_s": 0, "end_s": min(10, context.get("reference_duration_s", 10)),
                     "stream_index": audio_stream, "loop": False} if has_reference_audio else None
    template = """自主决定段落和片段，保留参考的主旨与有证据的剪辑意图。
不必复刻参考故事结构。只有watched窗口的usable_ranges能用于EDL；source_in_s/source_out_s
必须是source_start_s加上fine局部时间所得原电影秒数，不能直接填local秒数或联系表时间。
所有段落要能组合出连贯人物路线，不能独立挑最高相关片段。角色状态冲突需要显式处理。
每个fine中的role_id仅在(window_id,role_id)局部有效。同名ID在不同窗口不代表同一人物，
不同ID也可能指同一人物。focus_role_id由你选为稳定的焦点身份标签；通过focus_role_bindings
自主给出每个相关窗口的局部role_id与视觉身份依据，不得依据字符串或电影常识自动合并。
每个窗口至多一个焦点binding，必须已watched且该角色identity_confirmed；绑定证据不能为空。
segments.role_ids只引用该窗口局部角色，且必须有与实际所选子区间相交的事件证据。
至少一个选入片段包含绑定焦点；允许其他角色的反应镜头及环境镜头，不要求焦点每镜出场。
未证实的声音/音乐卡点不能写为已实现。音频可选reference/source/mix/silent。
最多32个segments，总成片时长sum((source_out_s-source_in_s)/speed)不得超过180秒。
只使用渲染器支持的speed 0.5至2、look none/grayscale、framing fit/crop；fps必须为1至60的整数。
reference_audio.stream_index必须使用上下文reference_audio_stream_index的实际音轨索引，
start_s/end_s必须在reference_duration_s内。参考无音轨时只能选source或silent，reference_audio可为null。
输出：{"reference_sha256":"固定SHA","focus_role_id":"focus_main",
"focus_role_bindings":[{"window_id":"w_1","role_id":"A","identity_evidence":"该窗口可见身份线索与其他绑定的对应依据"}],
"slots":[{"slot_id":"slot_1","intended_takeaway":"该段让观众理解什么","segment_ids":["seg_1"]}],
"segments":[{"segment_id":"seg_1","slot_id":"slot_1","source_id":"movie_1","window_id":"w_1",
"source_in_s":100,"source_out_s":103,"speed":1,"look":"none","framing":"fit","role_ids":["A"]}],
"audio_mode":__AUDIO_MODE__,"source_gain_db":-12,"reference_gain_db":0,
"reference_audio":__AUDIO_EXAMPLE__,
"width":__WIDTH__,"height":__HEIGHT__,"fps":30,"limitations":[]}\n"""
    from .plan_normalisation import LANDSCAPE_CANVAS, landscape_canvas_key
    # Preserve historical request bytes when the input lock has no policy key.
    width, height = LANDSCAPE_CANVAS if context.get('output_canvas') == landscape_canvas_key() else (720, 1280)
    template = template.replace('__WIDTH__', str(width)).replace('__HEIGHT__', str(height))
    template = template.replace("__AUDIO_MODE__", json.dumps("reference" if has_reference_audio else "source"))
    template = template.replace("__AUDIO_EXAMPLE__", json.dumps(audio_example, ensure_ascii=False))
    received = context.get('received_audio_for_edit', {})
    rule = line_window_prompt(received.get('draft', []), segments=context.get('segments', []),
        received_tts=received.get('actual_tts_lengths', []), fps=context.get('fps'), program_arranged=True) if context.get('creative_audio') else ''
    return BASE + content_range_prompt(context.get("catalog", {})) + template + json.dumps(context, ensure_ascii=False) + rule


EDITING_PROTOCOL = "reference_methods_to_operations_v2"

EDITING_KNOWLEDGE = """
剪法知识仅用于观察与操作，不提供故事答案：
对比需要前后可见关系；蒙太奇要组合出信息或变化；动作接切需核验动作阶段和方向；
反应镜头需观众能理解反应对象。文字与画面关系需核验文字是否受实际动作/结果支持。
定格是保留真实尾帧，不能补造新的生活画面。切点数量与slot数量都不证明剪法迁移。
scdet是图像变化候选，闪光、字幕变化及渐变可能误检；连续素材范围内也可能有多个镜头。
节拍、J/L cut和声音语义需要实际音频证据；本视觉MCP未经听审，不得声称已验证。
形式与表达作用分别检查，素材不支持时明确unavailable或unverifiable，不能省略困难手法。
"""


def editing_reference_prompt(reference: dict, timeline: dict) -> str:
    return BASE + EDITING_KNOWLEDGE + """再次观察同一固定参考，只补充剪法的实施条件。
原reference.editing_methods的每个索引恰好一次，不删方法、不改变主旨。时间域为参考视频秒数。
source_start_s/source_end_s是模型估计证据范围，不是检测器已确认的原生剪切边界。
requires_audio声明该手法的完整判断是否依赖音乐、声音或声画关系。
输出：{"reference_sha256":"固定SHA","methods":[{"method_id":"method_0",
"reference_method_index":0,"form":"可见组织方式","function":"产生的理解或情绪作用",
"source_start_s":0,"source_end_s":1,"evidence_type":"model_estimate","requires_audio":false,
"material_requirements":["需要的可见动作/结果/反应或画面条件"],
"verification_rule":"成片如何可观察地核验","uncertainties":[]}],"uncertainties":[]}
""" + json.dumps({'reference':reference,'measured_reference_timeline':timeline}, ensure_ascii=False)


def editing_plan_prompt(context: dict) -> str:
    return plan_prompt(context) + EDITING_KNOWLEDGE + """
本次启用reference_methods_to_operations_v2。在上述plan JSON中增加以下字段：
editing_bindings必须覆盖editing_reference.methods每个method_id一次；segment_ids按实际EDL顺序。
{"editing_bindings":[{"method_id":"method_0","status":"planned",
"segment_ids":["seg_1"],"intended_relation":"这些具体片段一起表达什么关系",
"operation":"实际采用的次序/接缝/停留/文字操作","verification":"实际成片中的检查办法",
"limitations":[]}],"candidate_dispositions":[{"window_id":"w_1",
"decision":"selected","reason":"有实际素材证据的采用或弃用原因"}]}
status可planned/unavailable/unverifiable；后两者limitations不能为空，不得删除难以迁移的方法。
candidate_dispositions覆盖所有watched窗口，使用了其中EDL片段时为selected，否则not_selected。
可选segments.freeze_tail_s为0至10秒，播放所选片段后复制最后真实输出帧；总时长含定格≤180秒。
源音频在定格尾部静音，参考音频按所选模式继续。不能把定格当成新的素材事件。
可选segments.caption={"text":"你依据可见证据写的中文文字","start_s":0,"end_s":1,
"position":"bottom","font_size":36,"evidence":[{"window_id":"w_1","event_indices":[0]}]}。
字幕起止是该segment输出局部秒数，含定格；不得超过segment时长。
仅支持静态top/center/bottom文字，字体由本地选择，不指定font路径；最多300字符，允许换行。
文字含描边必须完整位于画布8%安全边距内；根据实际画幅自主减小字号或显式换行，禁止裁字。
字幕每个event_indices必须来自同一窗口且与实际所选source区间相交；不能用文字捏造事件。
这些能力是选项，不要求为迁移不相关的剪法而加字幕或定格。
"""


def editing_fine_prompt(window: dict, context: dict) -> str:
    return fine_prompt(window,context) + EDITING_KNOWLEDGE + """
在上述fine JSON增加editing_observations数组，可为空；只记录实际连续观看发现的剪辑条件。
{"editing_observations":[{"method_id":"method_0","local_start_s":0,"local_end_s":1,
"observed_form":"可见景别、动作阶段/方向、视线、反应对象、镜头切换或停留的实际事实",
"potential_use":"这些条件在本次参考剪法中的可能用途，独立于事实",
"limitations":["不确定的关系或实现条件"]}]}。
每条绑定editing_reference中的method_id，时间从这个代理窗口0秒开始，不填原电影秒。
只说实际看见的条件，不靠故事常识补镜头；检测器候选需要观察确认，不自动成立。
缺少素材条件时明确记录局限；仍只有原usable_ranges允许剪入EDL。
"""


def editing_review_prompt(context: dict) -> str:
    return review_prompt(context) + EDITING_KNOWLEDGE + """
在上述review JSON增加method_checks，覆盖editing_reference.methods每项恰好一次：
{"method_checks":[{"method_id":"method_0","form_status":"partial",
"function_status":"partial","audio_status":"not_applicable",
"output_evidence":[{"start_s":0,"end_s":1,"observed_fact":"实际成片可见事实"}],
"limitations":[]}]}。
form_status/function_status仅pass/partial/fail/unverifiable。任何pass必须有实际输出秒数证据。
音频未经本MCP听审，audio_status仅unverifiable/not_applicable；requires_audio为true必须unverifiable。
editing_status为pass须全部form/function为pass且没有依赖音频但未经核验的方法。
检测器切点候选和计划绑定是导航，不是质量结论。分别核验文字/画面/动作/停留与表达作用。
"""


def blind_prompt(duration_s: float) -> str:
    return """你是独立视频观察员。仅依据当前实际画面描述，严格区分可见事实与推测。
只返回一个JSON对象，无Markdown或额外解释。GLM视觉MCP不保证音频理解，
没有音频证据时不得声称听到了台词、音乐或节拍；可见字幕属于画面证据。
""" + f"""只观看这条实际成片（{duration_s}秒），没有剧本、参考解释或slot。
独立描述看到了什么、人物行动及结果、能从画面推断出的含义。看不懂要如实报告。
不要依据电影常识补剧情。输出：{{"observed_story":"可见故事","main_characters":["人物外形"],
"apparent_theme":"仅据画面理解的主旨","evidence":[{{"start_s":0,"end_s":1,
"observed_fact":"支持的画面"}}],"confusions":[]}}"""


def picture_first_search_prompt(prompt, context):
    if context.get('creative_style') == 'picture_aware':
        return prompt + '\n优先选择可见动作、人物关系和情绪贴合主旨的素材，优先连续场景而非零散镜头。'
    if context.get('creative_style') != 'picture_first':
        return prompt
    return prompt + '''
picture_first：先输出顶层visual_script数组（3–8个静音视觉节拍），再提出windows或observation_ids。
每拍必须有非空字符串beat_id、who、to_whom、action、emotion、outcome、why_silent_readable，beat_id唯一。
从reference.theme/intended_takeaway迁移主旨，不复制参考的具体人物和事件；只描述必须看见的
人物互动、动作、情绪和结果，不写对白、不写旁白词。只读这些画面也应能理解主旨。
按这些情境寻找连续的一场戏，优先一段连续素材承担多个节拍，尽量少换场景；question询问
谁对谁做什么及可见结果，不按预写台词逐句匹配关键词。已有visual_script时逐字保留它。
'''


def picture_first_plan_prompt(prompt, context):
    if context.get('creative_style') == 'picture_aware':
        result = prompt + '''
picture_aware：迁移参考主旨，不要求静音画面独自演出价值判断，不要求visual_script或分镜。
每条audio_draft必须有picture_relation，只能是：
sync_speech：mode=dialogue，说话人在本句所挂镜头中可见且正在开口说话，沿用speaker可见性校验；
parallel：画面演出台词的意思，而不是台词的字眼；
additive：台词为正在发生的画面增补情境或潜台词；
divergent：有意反讽，画面自身的行动与结果仍应清楚。
sync_speech不能用此前身份锚或画外对白代替本句说话人出镜。
禁止字面关键词插图：店铺对白“规矩立住了，路才越走越宽”配熊猫在山崖小路上走路，
并没有表达经营有原则而赢得信任，不能因为有“路”就选走路镜头。
每个segment最多承载两条segment_ids恰好只含该segment的audio_draft；跨多个segment的句子不计入单镜头限额。
超限时拆分镜头范围或移动一句到其他合适镜头。静音审查仅供建议；修订只用同一批watched_windows，不重新检索。
''' + (('\n静音审查反馈：' + json.dumps(context['silent_feedback'], ensure_ascii=False))
       if context.get('silent_feedback') else '')
        if (context.get('silent_feedback') or {}).get('must_fix'):
            result = result.replace('静音审查仅供建议；', '内容审查中的must_fix必须修好，其他问题仅供建议；')
        return result
    if context.get('creative_style') != 'picture_first':
        return prompt
    prompt = prompt.replace('主题和价值判断由配音承载。', '主题和关键因果必须由静音画面讲清，配音只作增益。')
    return prompt + '''
picture_first顺序：先选择segments，使静音序列演出visual_script的全部节拍；优先同场景连续素材，
再对着选定画面写贴合的audio_draft。每个slot必须有visual_beat_ids:[beat_id,...]，只能引用
visual_script中的ID，全部节拍至少由一个有segment的slot承载。修订也仅使用同一批watched_windows。
每条audio_draft必须有picture_relation，只能是：
sync_speech：mode=dialogue，说话人在本句所挂镜头中可见且正在开口说话，沿用speaker可见性校验；
parallel：画面演出台词的意思，而不是台词的字眼；
additive：台词为正在发生的画面增补情境或潜台词；
divergent：有意反讽，画面自身的行动与结果仍应清楚。
禁止字面关键词插图：“路才越走越宽”配熊猫在山崖上走路，不等于演出经营有原则而赢得信任。
配音不能替不可见的关键事件补故事；sync_speech不能用此前身份锚或画外对白代替本句说话人出镜。
'''


def silent_review_prompt(blind, reference, timeline, *, render_sha256, legacy=False, content_check=True, cast=None):
    # One copy per selected segment, even when several lines share its evidence.
    segments = {}
    lines = []
    for row in timeline:
        for segment in row['segments']:
            segments.setdefault(segment['segment_id'], segment)
        lines.append({**{key: row[key] for key in
            ('line_id', 'text', 'picture_relation', 'mode', 'speaker', 'start_s', 'end_s')},
            'segment_ids': [segment['segment_id'] for segment in row['segments']]})
        if content_check and not legacy and 'cast_id' in row:
            lines[-1]['cast_id'] = row['cast_id']
    theme = reference.get('intended_takeaway') or reference.get('theme', '')
    theme = re.split(r'(?<=[。！？.!?])|\n', theme.strip(), maxsplit=1)[0]
    context = (dict(blind_reading=blind,
        reference_theme={key: reference[key] for key in ('theme', 'intended_takeaway', 'value_judgment') if key in reference},
        timeline=timeline, actual_render_sha256=render_sha256) if legacy else
        dict(blind_reading=blind, reference_theme=theme, timeline=lines,
             segments=list(segments.values()), actual_render_sha256=render_sha256))
    if content_check and not legacy:
        context.update(content_check=True, cast=cast or [])
        return '''你是画面与台词关系审查员，依据实际粗剪盲看、已看素材及timeline台词和speaker核对。
逐句返回issue：contradiction=台词与可见行动或结果冲突；keyword_illustration=只配字面关键词插图；
speaker_mismatch=sync_speech说话人未在本句镜头可见说话；invisible_claim=台词补充不可见事实或价值判断；无问题为ok。
不要求静音画面独自表达抽象价值；先按台词成立所需的最小可见条件判断，不能把主题或台词当可见事实。
additive可补潜台词，divergent可有意反讽，不自动判矛盾；不用电影常识或私人隐喻补证据。
roles必须结合实际画面与timeline台词/speaker（cast仅作对应线索）判断：clear=观众能否辨明各故事角色是谁；
consistent=每个有台词的故事角色是否全片由同一可见人物扮演，可暂不出镜，不能换人或拆给多人。
证据说明角色首次出现时画面或台词是否交代身份，指出含混或换人的line_id/镜头及人物。
仅contradiction或roles.clear/consistent=false必须修好；其他issue和scene_jumps_confusing均为建议。
readable_situation仍仅描述静音可读情境，未知写“无法判断”，只供参考，不据此判roles失败。
只返回JSON：{"lines":[{"line_id":"ID","issue":"ok|contradiction|keyword_illustration|speaker_mismatch|invisible_claim",
"evidence":"具体画面证据或缺失"}],"roles":{"clear":true,"consistent":true,"evidence":"角色对应证据"},
"scene_jumps_confusing":false,"readable_situation":{"who":"人物","where":"地点","relationship":"关系","emotion":"情绪"}}。
每条台词恰好一次；clear、consistent、scene_jumps_confusing必须为布尔值。
''' + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return '''你是画面与台词关系审查员。这是纯文本silent_review，只根据实际粗剪的独立盲看和已看素材证据核对。
逐句检查picture_relation，不要求静音画面单独表达抽象价值判断，不把参考主题或台词当成可见事实。
第1级：contradiction=台词与可见行动或结果冲突；keyword_illustration=只配字面关键词插图
（如店铺对白“规矩立住了，路才越走越宽”配熊猫走山崖小路）；speaker_mismatch=sync_speech说话人未在本句镜头可见说话。
第2级：invisible_claim=台词增补画面看不出的事实或价值判断，仅供参考；无问题为ok。
additive可增补潜台词，divergent可有意反讽，不自动判为contradiction。不得用电影常识或私人隐喻补证据。
场景跳转若使人物关系或情境无法理解，scene_jumps_confusing=true。
readable_situation仅描述静音可读的who、where、relationship、emotion，均为字符串，未知写“无法判断”，仅供参考。
只返回JSON：{"lines":[{"line_id":"ID","issue":"ok|contradiction|keyword_illustration|speaker_mismatch|invisible_claim",
"evidence":"盲看或已看观察中的具体证据/缺失"}],"scene_jumps_confusing":false,
"readable_situation":{"who":"人物","where":"地点","relationship":"关系","emotion":"情绪"}}。
每条台词恰好一次；scene_jumps_confusing必须为布尔值。
''' + (json.dumps(context, ensure_ascii=False) if legacy else
       json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def silent_check_prompt(visual_script, blind, *, render_sha256=None, round_no=None):
    return '''你是静音故事检查员。这是纯文本核对，没有剧本台词、配音或原片知识。
仅比较独立盲看的observed_story及其可见证据与visual_script，不把目标描述当成实际已发生。
逐拍判断who/to_whom/action/emotion/outcome是否已由画面讲出；证据不足须told=false。
场景跳转若令人无法理解人物关系或因果，scene_jumps_confusing=true。
只返回JSON：{"beats":[{"beat_id":"ID","told":true,"evidence":"盲看中的具体证据或缺失"}],
"scene_jumps_confusing":false}。每个目标节拍恰好一次，told及scene_jumps_confusing必须是布尔值。
''' + json.dumps(dict(visual_script=visual_script, blind_reading=blind,
                     actual_render_sha256=render_sha256, round=round_no), ensure_ascii=False)


def review_prompt(context: dict) -> str:
    return BASE + """对照固定参考、实际成片盲读与观测证据，评价主旨、剪法和人物连续性。
主旨评价要自行说明可迁移的观众理解是什么，再检查实际成片有没有用行动和结果表达它。
参考具体人物身份与新素材的人物身份不同是来源差异，不能单独作为主旨fail的依据；
如果差异导致实际含义改变，仍应如实partial/fail，并指出实际缺失的关系或证据。
editing_status必须逐项对照reference.editing_methods的形式与表达作用，并引用实际成片时间证据。
故事段落顺序相似或slot数量相似，不能证明镜头组织、停留、切换、图文关系和节奏迁移成功。
只迁移部分可见剪法时应partial；未实现的重要手法要保留，音频节奏未核验不能声称完整声画迁移pass。
不要因为计划声称实现就判pass；可播放不是主旨成立。预算内提出具体补看或重构需要。
每项status只能pass/partial/fail/unverifiable。若音频未经审看，声画迁移必须保留限制。
输出：{"reference_sha256":"固定SHA","theme_status":"partial","editing_status":"partial",
"continuity_status":"partial","evidence":["参考与成片中的具体证据"],
"limitations":[],"revision_requests":[]}\n""" + json.dumps(context, ensure_ascii=False)
