"""Reference-grounded audiovisual performance and editing assessment."""
import json

POLICY = 'reference-pair-v2'
REFERENCE_DIMENSIONS = {
    'expression': '参考表达是否达成', 'visual': '画面演绎与人物互动',
    'editing': '剪辑组织与节奏', 'audiovisual': '声音表达与声画协同',
}
# Viewing effects are included above, rather than counted again in a second score group.
QUALITY_DIMENSIONS = {}
STATUSES = ('preserved', 'equivalent', 'partial', 'missing', 'contradictory', 'uncertain')
STATUS_LABELS = dict(zip(STATUSES, ('保留', '等效改编', '部分保留', '缺失', '矛盾', '无法判断')))


def observe_prompt(kind, duration, has_audio, transcript):
    prompt = '''独立观察实际成片，不知道生成剧本和制作计划。用中文JSON回答。
先看听完整媒体，再按叙事作用分段，含无对白段、关键反应、高潮和结尾。
heard只写实际听到的内容，seen只写实际可见动作；字幕、对白提及、原电影常识都不能证明动作发生。
不能把“他说要离开”写成“他已经走开”。无法确认时明确写入uncertainties。
记录选镜和顺序如何交代上下文、谁对谁说、何时切听者反应、停顿与转场怎样服务表达。
角色以实际外观辨认，不按字幕位置或原电影剧情猜身份；同一演员跨镜头沿用编号。
不要评生成剧本文笔；最终实际说出的语义仍属于视频表达。时间以本片0秒开始。
输出 {"summary":"成片实际传达的事情、态度或笑点", "roles":[{"id":"r1","appearance":"外观","relationship":"可确认的关系"}],
"segments":[{"id":"s1","start_s":0,"end_s":1,"heard":"实际语音/音乐/安静","seen":"可见事实，不以台词补造",
"edit_function":"选镜、顺序、反应和切换的实际作用"}], "uncertainties":[]}。
'''
    if kind == 'reference':
        prompt += '''这是参考视频。额外输出requirements：先冻结这条参考必须迁移的表达单元，再观察新片。
每项含id(q1等)、dimension、importance(critical/supporting)、evidence_mode(visual/audio/joint)、
reference_segment_ids、reference_span:[起秒,止秒]、requirement、permitted_changes。
只列有实际证据的要求，不为每个维度凑条数。critical只用于缺了会改变核心表达/关系/结构的内容。
要求写“高潮得到突出”“误听逐轮累积”等功能，不能无条件要求同款黑白定格、正反打、走路或每秒切镜。
可更换演员、物件、措辞和地点；保留具体关键信息与机制，不只概括“搞笑/有原则”。
evidence_mode=visual用于必须实际看见或等效视觉表达的动作/反应；audio用于听觉足以完成的语义；joint用于必须声画结合的关系。
抽象观点/过去或将来事件不要求现场实演；谐音不要求静音也能理解。
不默认必须同曲BGM：音乐/歌词构成梗或关键节奏才是重要表达；普通配乐风格和环境音细节不升格为核心要求。
dimension可从以下四项选择：''' + json.dumps(REFERENCE_DIMENSIONS, ensure_ascii=False)
    return prompt + '\n' + json.dumps(dict(video_kind=kind, measured_duration_s=duration,
        original_has_audio=has_audio, independent_asr=transcript,
        asr_warning='自动识别可能错误，不是实听真值'), ensure_ascii=False)


def modality_prompt(kind, duration):
    if kind == 'audio':
        return '''本媒体是直接输入的WAV音频，没有画面。只听本段完整声音，不猜画面或参考内容。按声音变化分段，尤其记录短暂音乐与人声退场。
对白清晰不代表没有背景音乐；有声音能量不代表一定是音乐。无法辨认用uncertain。
本段是原视频局部，坐标从本段0秒开始，不要猜原片绝对时间。注意切片边界可能在一句话中间，不把这种切片现象判为原片截断。
输出中文JSON {"segments":[{"start_s":0,"end_s":1,"speech_status":"audible/absent/uncertain",
"music_status":"audible/absent/uncertain","heard":"实际语音文字、旋律、伴奏、安静或听不清的依据"}],
"speech_notes":["可懂度、数字、谐音、语气等实际疑点"],"uncertainties":[]}。
片段实测时长为''' + str(duration) + '秒。'
    return '''本视频没有声音，完整画面未遮挡。独立看全片，不能使用字幕或画面文字推断动作、嘴动和人物身份。
seen仅写实际看见的人物外观、动作、嘴部运动、视线、反应、场景、切镜与结束动作；文字信息不能混入seen。
即使看到了“离开”字幕，也只有看到动作才记录离场。无法观察到嘴时不能臆测讲话。
按镜头/事件分段，覆盖无对白反应和结尾，不知道台词、剧本或制作计划。
输出中文JSON {"segments":[{"id":"v1","start_s":0,"end_s":1,"seen":"实际可见事实"}],
"uncertainties":["遮挡、字幕残留和短动作等观察限制"]}。本片时长为''' + str(duration) + '秒。'


def compare_prompt(reference, candidate, reel, modality_checks):
    return '''评测参考驱动的异源素材改编。媒体顺序为REFERENCE与CREATED，均有实际声音；坐标用各自0秒，不能用拼接后的全局时间。
主要评最终视频有没有把参考表达重新演出来，而非剧本文笔。台词正确不能替代需要画面表达的动作、反应和关系。
独立音频与视觉观察是可出错的证据，须与本次实际声画交叉核对。明确区分实际看见、实际听见、联合成立、矛盾和不确定。
同一故事角色应由同一实际演员承担，同一角色声音前后稳定，不能无意互换台词或关系；不要求使用参考原演员或同音色。
support是新片中实际支撑该要求成立的模态。要求冻结的evidence_mode为visual或joint时，只有声音支撑不能判preserved/equivalent，须据实判partial/missing/uncertain；不能为通过校验虚填joint。
合理画外接话、J/L cut、听者反应可以承载连续对白，不要求每秒看说话人的嘴。检查明显错人/持续闭嘴配同步讲话，不虚构逐音素或毫秒口型结论。
剪辑看选镜相关性、上下文、顺序因果、切点动机、反应、场景连续、停顿、高潮与收尾。允许等效手法，不强求同款滤镜、镜头数、切速、走路。
长短和循环本身不扣分；需要说明哪些节拍被破坏。音乐主导片检查乐句/情绪组织，不以每拍切镜为好剪辑。
声音重点查可懂、完整、说话人、轮次、语气与动作相容。普通环境声丰富度、音色审美不作核心失败理由。
BGM曲目是否相同与实际效果分开：没有可靠匹配证据，track_match只能uncertain；双方无音乐则not_applicable。听起来都像某风格不能证明同曲。
配乐不默认必须贯穿或同曲；参考中确属表达关键时按冻结要求核对。缺少普通配乐不能自动认定对白差、人物错或整片低质。
所有音乐存在性以分时段听辨为明确核对对象：任一audio.segments的music_status=audible时，不能写“全片无音乐”。
如认为独立听辨误报，明确写入observation_corrections并将music_presence设uncertain，不默默覆盖。
不存在无对白段、从未离场等否定判断须核对全片相关区间；有停顿不自动代表其情绪作用等效。
每项参考要求必须且只能对齐一次，不能改要求或重要性；若参考要求被实际原片否定，标uncertain并说明。
如果顺序对照媒体中候选声音听不清，而candidate_audio明确听到具体对白，不能声称新片静音；记录播放/感知冲突并标uncertain，不以猜测声音缺失给1分。
不能根据原电影知识推断英语口型，不以未测的逐字口型一致性判劣质配音；只检查实际可确认的粗粒度发言归属。
给四项adaptation_scores，quality_scores固定为空数组，避免同一问题在两组分数重复包装。无综合总分。
等级：5关键作用清楚成立无实质问题；4主要作用成立局部不足；3基本可懂但重要表现明显变弱；2多处关键缺失/冲突；1核心表达不成立或相反。
该维度有缺失/部分/矛盾/未知要求不可给5；全部未知则null。确实不适用时applicability=not_applicable且score=null。
reason必须联系实际观看影响和具体片段，不写“商业感不足”等泛化审美。缺少证据用null与uncertain，不给假精确置信概率。
每个实际问题只建一个issue，dimensions可关联多个维度，不能把同一事实重复成多个问题。major需具体说明如何破坏关键表达/可懂度/语音完整性。
输出中文JSON：
{"summary":"实际表达是否成立、优点、主要差距和不确定项",
"alignment":[{"requirement_id":"q1","status":"preserved/equivalent/partial/missing/contradictory/uncertain",
"support":"visual/audio/joint/none/uncertain","candidate_spans":[[0,1]],"reference_evidence":"参考实见实听",
"candidate_evidence":"新片实见实听","visual_evidence":"独立可见内容或无支持","audio_evidence":"独立实际听见或无支持","difference":"对表达的作用和差距"}],
"adaptation_scores":[{"dimension":"四项维度之一","score":4,"applicability":"applicable/not_applicable/uncertain",
"reason":"具体表现及看片影响","requirement_ids":["该维度全部要求"]}],"quality_scores":[],
"diagnostics":{"character_identity":{"status":"pass/partial/fail/uncertain/not_applicable","evidence":"人物跨镜头稳定、角色分工和声音归属的实际证据"},
"speech_delivery":{"status":"pass/partial/fail/uncertain/not_applicable","candidate_presence":"audible/absent/uncertain","evidence":"清晰完整、轮次、语气和关键语音依据"},
"music":{"reference_presence":"audible/absent/uncertain","candidate_presence":"audible/absent/uncertain",
"track_match":"uncertain/not_applicable","role":"essential/supporting/none/uncertain","evidence":"局部音乐与实际效果，不猜同曲"},
"editing":{"status":"pass/partial/fail/uncertain/not_applicable","evidence":"顺序、反应、停顿、高潮与收尾的实际作用"}},
"issues":[{"id":"i1","severity":"major/minor/uncertain","dimensions":["四项维度之一"],"requirement_ids":["q1"],
"reference_span":[0,1],"candidate_span":[0,1],"description":"具体错误、证据和对观看的影响"}],
"limitations":[],"observation_corrections":[],
"evidence_reconciliation":[{"aspect":"sound/visible_actions_objects/speech各一项","status":"consistent/revised/uncertain","finding":"核对及冲突处理"}]}。
''' + json.dumps(dict(reference=reference, candidate=dict(summary=candidate['summary'], roles=candidate['roles']),
        reel_time_mapping=reel, modality_checks=modality_checks, reference_dimensions=REFERENCE_DIMENSIONS), ensure_ascii=False)
