"""Forward-only prompts separating source facts from planned interpretations."""
from __future__ import annotations

import json

from . import prompts
from .semantic_audit import SEMANTIC_PROTOCOL
from .state import json_sha


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def plan_prompt(context):
    return prompts.editing_plan_prompt(context) + """
本次采用独立精切事实审核。每个segments增加非空visual_claims：
[{"claim_id":"vc_segment_1_action","kind":"visual_action","description":"这几秒应实际看见的动作"}]。
claim_id全计划唯一，kind仅visual_action/visual_outcome/identity。只写本段能直接证明的事实。
一个slot可以包含多个不连续的短片段，不要求一段连续素材讲完一个slot。
先找表达所需的动作、可见效果和反应，再删除等待/重复/无关对白。不要仅按事件相关性拼长段。
短片段需保留理解动作所必需的前后信息；不能删去结果却写成功，也不能把跨时刻蒙太奇假装同一连续动作。
关键动作可慢放以便看清，重复过程可加速；speed是源播放倍速，输出时长=(out-in)/speed+freeze_tail。
每段默认1倍，变速应在editing_bindings.operation说明用途；不为凑秒数任意加速/慢放。
字幕证据必须与字幕实际显示时正在播放的源时间相交；不要依靠字幕替代可见行动与结果。
片段数量必须满足render_capabilities.max_segments，这是预留完整事实审核及一次格式修复的预算上限。
整个slot的表达在成片审核，单个短片段只负责其局部事实。
"""


def slice_observation_prompt(segment, source, proxy):
    # Deliberately exclude slot, role guesses, reference and desired outcomes.
    metadata = {"protocol": SEMANTIC_PROTOCOL, "segment_id": segment["segment_id"],
                "source_id": source["source_id"], "source_sha256": source["sha256"],
                "source_in_s": segment["source_in_s"], "source_out_s": segment["source_out_s"],
                "proxy_sha256": proxy["sha256"], "observed_duration_s": proxy["duration_s"]}
    return """你是独立无声视频事实观察员。没有创作计划或目标答案。只观察当前完整短片段。
这是源文件精切的正常速度代理，不带后加文字或定格。不依靠电影常识、音频或先前剧情补动作。
逐项分开可见动作visual_action、可见结果visual_outcome、画内文字visible_text与推断inference。
角色用本片段局部ID和实际外形，未知真实身份可以未知。事件没有发生也要如实保留缺项。
只返回JSON，将下列metadata逐字保留并增加characters/evidence/uncertainties：
characters=[{"character_id":"observed_1","appearance":"实际外形"}]，可为空。
evidence=[{"evidence_id":"e1","kind":"visual_action","local_start_s":0,"local_end_s":1,
"description":"实际画面","character_ids":["observed_1"],"basis_evidence_ids":[]}]。
时间为当前代理局部秒；inference必须引用直接证据，直接事实basis_evidence_ids为空。
不要输出plan/reference/theme/required_claims/claim_checks字段。
metadata：""" + _json(metadata)


def explicit_slice_observation_prompt(segment, source, proxy):
    """Complete fact shape for unpaid evidence-first rounds; legacy bytes stay fixed."""
    template = {"protocol": SEMANTIC_PROTOCOL, "segment_id": segment["segment_id"],
        "source_id": source["source_id"], "source_sha256": source["sha256"],
        "source_in_s": segment["source_in_s"], "source_out_s": segment["source_out_s"],
        "proxy_sha256": proxy["sha256"], "observed_duration_s": proxy["duration_s"],
        "characters": [{"character_id": "observed_1", "appearance": "填写当前画内实际外形；未知身份保持未知。"}],
        "evidence": [{"evidence_id": "e1", "kind": "visual_action", "local_start_s": 0,
            "local_end_s": min(1, proxy["duration_s"]), "description": "替换成这段时间实际可见的事实。",
            "character_ids": ["observed_1"], "basis_evidence_ids": []}], "uncertainties": []}
    return """你是独立无声源视频事实观察员，没有创作计划、人物猜测或目标答案。
只观察当前完整精切短片段：正常速度、无后加文字、无剪辑定格。
不依靠电影常识、声音、更长窗口或先前剧情补动作、结果、动机和身份。
下面是一份扁平的完整输出模板；占位描述不是观察真值，必须替换，不得照抄。
只返回一个完整JSON对象，根字段protocol、segment_id、source_id、source_sha256、
source_in_s、source_out_s、proxy_sha256、observed_duration_s、characters、evidence、uncertainties全部必需。
前八个绑定字段逐字保留，禁止包入metadata/observation/data等子对象。
characters是数组，每项完整包含character_id和非空appearance；只描述画内实际外形。
使用片段局部人物ID，不能用未确认的真实身份；无人出现可写[]。
evidence是非空数组，每项必须包含七个字段：evidence_id、kind、local_start_s、local_end_s、
description、character_ids、basis_evidence_ids。所有evidence_id唯一，description非空。
kind只能选择一个literal：visual_action、visual_outcome、visible_text、inference。
时间只用当前代理局部秒，严格满足0 <= local_start_s < local_end_s <= observed_duration_s。
不使用源文件全局秒、不写负数或零长区间；模板的时间仅示例，须据实际画面独立填写。
character_ids必须是characters中实际出现ID的无重复数组；无对应人物可写[]。
visual_action、visual_outcome、visible_text是直接事实，basis_evidence_ids必须显式为[]。
inference只能作为evidence中的kind，不得用根inference/inferences正文替代完整typed证据表。
每条inference的basis_evidence_ids必须非空，仅引用本evidence表内其它直接事实的ID；
不能引用自己、其它inference、不存在的证据、创作主张或片段外事件。
可见结果只能写实际显示的结果；只有动作没有结果时保留这个缺项，不写成功或失败的猜测为直接事实。
uncertainties必须显式为字符串数组list[str]，每项是一句非空文字；没有其它不确定性时写[]。
禁止省略uncertainties、写null、单个字符串、对象、或[{"description":"..."}]等对象数组。
未知身份、缺少可见结果、视线遮挡或动作关系不清都可以在字符串中如实记录。
不要输出plan/reference/theme/intended_takeaway/required_claims/claim_checks字段。
metadata：""" + _json(template)


def slice_claim_prompt(observation, claims, role_hypotheses):
    template = {"protocol": SEMANTIC_PROTOCOL, "segment_id": observation["segment_id"],
        "observation_sha256": json_sha(observation), "claim_checks": [
            {"claim_id": "逐个覆盖required_claims", "status": "supported|partial|unsupported|unverifiable",
             "evidence_ids": [], "reason": "依据或缺项", "limitations": ["非supported不能为空"]}],
        "uncertainties": []}
    return """现在比较不可改写的精切事实与待验证说法。这不是生成新事实的环节。
role_hypotheses和required_claims是创作方的说法，不能当作观察结果。仅引用observation.evidence里的ID。
对于动作、结果、身份，字幕和推断均不能证明其真实发生；角色不在画内就不能通过。
文字所描述的事实没有在片段出现，不能靠所看过的更长窗口补齐。
只返回JSON：""" + _json(template) + "\nobservation：" + _json(observation) + "\nrequired_claims：" + _json(claims) + "\nrole_hypotheses：" + _json(role_hypotheses)


def blind_prompt(duration_s, video_sha256):
    return prompts.blind_prompt(duration_s) + """
本次进一步区分画面事实、文字和推断，判断不读文字还能否看懂人物行动与结果。
增加protocol、video_sha256和text_dependency（none/assists/essential/unverifiable）。
evidence每条增加唯一evidence_id、唯一claim_id、kind和basis_evidence_ids；
kind仅visual_action/visual_outcome/visible_text/inference。推断引用直接证据ID；直接事实basis为空。
保留start_s/end_s/observed_fact。不要把可见文字的内容写成可见动作。
人物无法确认、动作关系不清楚或没有可见结果，要放进confusions，不依据电影常识解释。
绑定：""" + _json({"protocol": SEMANTIC_PROTOCOL, "video_sha256": video_sha256})


def batch_claim_prompt(records):
    packed=[{k:r[k] for k in ('observation','required_claims','role_hypotheses')} for r in records]
    template={'protocol':SEMANTIC_PROTOCOL,'segment_checks':[
        {'protocol':SEMANTIC_PROTOCOL,'segment_id':r['observation']['segment_id'],
         'observation_sha256':json_sha(r['observation']), 'claim_checks':[
             {'claim_id':c['claim_id'],'status':'supported|partial|unsupported|unverifiable',
              'evidence_ids':[],'reason':'依据或缺项','limitations':[]} for c in r['required_claims']],
         'uncertainties':[]} for r in records]}
    return """所有短片段的独立事实观察已先完成。现在逐段核对待验证说法，不改写这些事实表。
本调用只附第一段视频作为工具载体，其余视频不在当前调用输入。其它片段仅按其独立事实表比较，
不要声称同时观看了全部视频；不得拿第一段或更长窗口补其他片段不存在的事实。
required_claims和role_hypotheses是待验证说法，不是事实。每段只引用该段observation.evidence IDs。
supported必须有直接视觉事实支持，结果必须有visual_outcome；字幕/推断不能证明动作或身份。
未知或缺失就partial/unsupported/unverifiable并写limitations；不得为满足计划改写观察。
只返回一个JSON，逐个覆盖segment及其所有claims，保留各observation_sha256绑定。
模板："""+_json(template)+'\nrecords：'+_json(packed)


def autonomous_reference_prompt(reference_sha, duration_s):
    return prompts.reference_prompt(reference_sha,duration_s)+"""
本次从实际完整参考自主形成新的理解，不提供旧解读或人工剪法答案。
请自行决定哪些位置值得细看、哪些组织方式对表达最关键；不要只识别一个片段或只概括主题。
先观察全片的画面变化与信息关系，再决定可迁移的组织方式和实施条件。不要依赖影片常识补事件。
将上面的reference JSON放入reference字段，同时自主生成editing_reference.methods。
每个新发现的reference.editing_methods索引恰好对应一个methods条目，不受历史方法列表限制。
editing_reference={"reference_sha256":"固定SHA","methods":[{"method_id":"method_0",
"reference_method_index":0,"form":"实际观察的组织形式","function":"表达作用",
"source_start_s":0,"source_end_s":1,"evidence_type":"model_estimate","requires_audio":false,
"material_requirements":["迁移所需的实际素材条件"],"verification_rule":"成片可观察的核验办法",
"uncertainties":[]}],"uncertainties":[]}。
增加coverage=[{"start_s":0,"end_s":1,"observed_content":"这一部分实际包含什么"}]，
由你划分覆盖完整参考的相邻区间，不遗漏首尾；coverage是你的观察声明，不是机器证明。
增加observation_strategy，简述你选择细看位置的依据和仍缺少的观察信息，不输出长篇推理。
最终只返回 {"reference":{...},"editing_reference":{...},"coverage":[...],"observation_strategy":"..."}。
不要填无法从成片核验的原始拍摄时长、原始速度或背景声音信息。
"""


def review_prompt(context):
    return prompts.editing_review_prompt(context) + """
增加protocol、video_sha256、visual_narrative_status与fact_checks、contradictions。
视觉表达为主要准则，字幕可辅助但不能代替行动；音频未经听审不作事实。
required_claims是待审表达，source_observations是独立精切事实。审核当前实际成片及接缝，不把计划当答案。
segment_checks里partial/unsupported/unverifiable的同一局部claim，不得在全片fact_checks升级supported。
这些局部事实需要新的独立观察才能改变，不能靠全片解释改变；slot整体组合要求另行评价。
fact_checks恰好覆盖required_claims的全部claim_id和blind_reading.evidence的全部claim_id：
[{"claim_id":"ID","status":"supported|partial|unsupported|unverifiable",
"evidence_refs":[{"segment_id":"seg1","evidence_id":"e1"}],
"blind_evidence_ids":["blind_e1"],"reason":"真实支持或缺失","limitations":[]}]
动作和结果supported必须有直接视觉证据；身份须有source角色证据。非supported的limitations不能为空。
逐条核对盲读事实。动作类型、人物身份或结果与精切事实不一致时，禁止直接忽略。
contradictions=[{"contradiction_id":"conflict1","claim_ids":["目标ID","盲读ID"],
"status":"unresolved|resolved","reason":"双方哪里矛盾","resolution":null,
"evidence_refs":[],"blind_evidence_ids":[]}]，没有冲突才可为空。
resolved需非空resolution和实际直接视觉证据，不得靠计划解释消除矛盾。
已用直接视觉证据解决的错误盲读可在contradiction.rejected_claim_ids保留其旧claim_id；
这些旧事实检查仍为unsupported/unverifiable，不能改成supported；不能豁免required_claims。
存在unresolved、未解决的非supported事实、盲读confusions或essential/unverifiable文字依赖时，
visual_narrative_status不得pass，主题/剪法/连续性不得全部pass。保留问题并提出下一轮具体缺项。
"""


def selection_prompt(candidates):
    return """比较实际成片审核中已核验的事实，选择现有候选的最佳可用版本。所附视频仅是最后一个候选。
其他候选依据各自独立事实审核记录比较，不能声称当前同时观看了所有候选。不要依据参考画面编造成片动作。
只返回JSON {"selected_round":0,"reason":"有证据的比较",
"evidence_claim_ids":["所选候选supported的fact_checks.claim_id"],"limitations":[]}。
保留所有未解决矛盾和partial/fail事实；只选可播放版本不代表质量通过。局限候选limitations不能为空。
候选：""" + _json(candidates)
