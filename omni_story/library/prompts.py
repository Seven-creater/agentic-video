"""Model-owned observations and decisions. No human-written plot or source picks."""
from __future__ import annotations

import json

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
    return BASE + f"""完整观看本条固定参考（SHA={reference_sha}，时长{duration_s}秒）。
分析主旨所需的关系、行动和结果，而非仅关键词或情绪。剪法区分形式和表达作用；
镜头切点为观看估计，音乐卡点待核验。输出：
{{"reference_sha256":"{reference_sha}","theme":"主旨","intended_takeaway":"观众应该理解什么",
"visible_evidence":[{{"start_s":0,"end_s":1,"observed_fact":"实际画面","supports":"支持哪一关系"}}],
"editing_methods":[{{"method":"剪法","function":"作用","visual_evidence":"画面证据","start_s":0,"end_s":1}}],
"uncertainties":["未确认事项"]}}"""


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
    return BASE + """依据固定参考和已观察素材提出下一轮精看请求。先考虑整条人物路线是否有
足够可见证据，再安排具体slots。可以补上下文或探索未观察区域；不要拿电影常识当已看片。
一次请求不超过12个连续窗口，每窗口不超过90秒，区间必须在catalog实际时长内。
question具体指出要确认的动作、身份状态、结果或衔接。输出：
{"reason":"目前缺项与路线考虑","windows":[{"source_id":"ID","start_s":0,"end_s":30,
"question":"需要确认什么","role_ids":["candidate_A"]}]}\n""" + json.dumps(context, ensure_ascii=False)


def fine_prompt(window: dict, context: dict) -> str:
    return BASE + """这是按请求取得的真实连续窗口。下列local时间从该小片的0秒开始，
不能填原电影秒数。仅把实际观看且人物身份确认的片段放入usable_ranges。
角色ID可以从候选修正；identity_confirmed必须基于此窗口画面身份线索，不能仅凭电影常识。
记录每个角色的外形阶段、服装/道具/伤势等状态和适用的连续性限制。
输出：{"window_id":"ID","source_id":"ID","roles":[{"role_id":"A","identity_confirmed":true,
"state":"实际状态","identity_evidence":"身份线索"}],
"events":[{"local_start_s":0,"local_end_s":1,"observed_fact":"动作与可见结果","role_ids":["A"]}],
"usable_ranges":[{"local_in_s":0,"local_out_s":1,"event_indices":[0],"role_ids":["A"],
"continuity_notes":"前提、结尾与状态限制"}],"uncertainties":[]}\n""" + json.dumps(
        {"window": window, "context": context}, ensure_ascii=False)


def plan_prompt(context: dict) -> str:
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
"width":720,"height":1280,"fps":30,"limitations":[]}\n"""
    template = template.replace("__AUDIO_MODE__", json.dumps("reference" if has_reference_audio else "source"))
    template = template.replace("__AUDIO_EXAMPLE__", json.dumps(audio_example, ensure_ascii=False))
    return BASE + template + json.dumps(context, ensure_ascii=False)


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
