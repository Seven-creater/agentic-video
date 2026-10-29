# INC-20261030-04 close 的 same_character_as_anchor=null 否决 fine 已核验的锚点绑定,双人窗口被静默掏空,plan_0 修复耗尽停机(粉色娇嫩,176 次调用零产出)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 上午(e2e 自动修复第 15 轮;出险 run 是第 14 轮修复 49ef83d 部署后的嵌套重跑,10-30 06:38:28 停机) |
| 影响范围 | 创作路线(reference_rough_skill_v1)qfs 精看→窗口观察→粗剪规划的身份绑定;致命停机——凡 close 候选如实回报 `same_character_as_anchor=null` 的模板(双角色、低辨识度参考普遍如此),fine 已核验的角色会被静默降级、窗口 usable_ranges 被剥空,plan 在"提示词说已同意、校验说无此角色"的矛盾状态下烧完修复预算,整跑零产出 |
| 状态 | 已修复(6d743fb);部署后同模板嵌套重跑结果待补 |
| 相关提交 | 6d743fb(修复,7 文件 +199/−6,生产代码仅 `question_first.py`);出险版本为部署 release `aa47a93_creative_49ef83d`;否决条件的引入提交未记录 |
| 相关任务 | 服务器 run `/home/ubuntu/apps/agentic-video/shared/jobs/e2e_18_粉色娇嫩_10300421/evaluations/fix13/evaluations/fix14`(模板 18_粉色娇嫩/01.mp4,10-30 06:14:48 启动);证据 `out/e2e/evidence_18_粉色娇嫩_14.txt`;根因分析 `out/e2e/analysis_15.md`;修复报告 `out/e2e/fix_15.md` 及 `docs/fix_e2e_anchor_agreement_20261030.md`;审核 `out/e2e/review_15.md`(pass);闸门 `out/e2e/progress.log` round 15 行 |

## 1. 问题长什么样
run_outcome.json:`outcome/status: stopped`、`stage: plan_0`、`error: model_protocol_repair_exhausted:plan_0`、`exception_type: ValueError`、`raised_at: …/releases/aa47a93_creative_49ef83d/src/omni_story/library/pipeline.py:1078`、`model_calls: 176`、`repairs: 2`、`final_video: null`。chain_failure.json:`requests: 176`、911,978 prompt + 109,743 completion tokens(token 口径 `captured_responses_only_unknown_charges_not_zero`)、`uncertain_calls: []`;调用计数 `Counter({'received': 176})`——与上一轮 run 13 的 17 次 fine 全灭不同,本次 176 次调用全部收到回复,fine 观察 13 窗全部 watched,死在 plan。

plan_0 三次尝试、三种错误(逐次 protocol_failure.json 原文在证据内):
- attempt 0(glm_174_plan_0,证据 :84-86):`plan:cast_entity_not_supported_by_observation`——`cast_elder` 把乌龟锚点实体绑到 `(window_e41db…,A)` 与 `(window_929f…,A)`,而 929f 的该角色已不在 allowed_bindings;
- attempt 1(glm_175_plan_0_repair,:97-101):`plan/confirmed_roles:unknown_or_duplicate_ref`——模型删掉了问题绑定,但 segments 仍保留 `seg_3/seg_4 → window_929f, role_ids:["A","B"]`,该窗口已确认角色为空集;
- attempt 2(glm_176_plan_0_repair2,:112-116):`visual_library:range_outside_content`——模型彻底弃用 929f、重组 cast 通过全部 cast/focus 检查,但把时域换成了窗口局部秒(`seg_1 source_in_s:0` 等)→ `$.segments[0].source_in_s` 越界,修复预算(repair_limit=2)耗尽,`pipeline.py:1078` 抛 `model_protocol_repair_exhausted:plan_0`。

矛盾现场:glm_173(`fine_929f0731c70aeca7`,:68-81)对窗口 929f 的 role A 明确回报 `identity_confirmed:true`、`same_character_as_anchor:true`、逐字复制锚点实体 id 并给出视觉比对依据(龟壳纹路/披挂/弯杖与锚点剧照一致),`usable_ranges.role_ids=["A","B"]`;而 plan 合同侧同一窗口的可绑角色为空。

## 2. 怎么查出来的
- 证据包逐字段核对(run_outcome、chain_failure、traceback、三次 protocol_failure、glm_173 fine 回复、close_004 候选),并与 golden 路径(`docs/golden_path_heihu_98e9427.md`,黑虎阿福)逐阶段对照(analysis_15 §一):前四阶段一致,**第一个偏离的阶段是精看(close)**,偏离字段为候选级 `same_character_as_anchor`——golden 为 `true`,本次决定性候选 close_004 为 `null`(证据 :476),连锁把 `verdict` 由 usable 降为 partial。close 提示词本身把 null 当合法默认值(`question_first.py:67` 示例即 null、:57"身份不确定请写不确定"),模型如实回报是被合同允许的行为。
- 用 pfix 代码复现三次尝试撞的三个校验点:`contracts.py:809-811`(validate_task_cast 对照 `plan_field_contract` 的 allowed_bindings,后者只收 `identity_confirmed=true` 的角色,`contracts.py:155-171`)、`contracts.py:914-915`+`contract.py:43-47`(confirmed_roles 的 refs 抛裸 ValueError,无 violation、无 allowed 列表)、`content_ranges.py:19-32`(时域越界)。并用两种 anchor 状态跑真实的 `_fine_navigation_points`/`validate_fine`/`plan_field_contract`:close=true 时全 PASS,close=null 时 `allowed_bindings=[]`、usable 角色被剥空——929f 被掏空是校验侧事实,不是模型错觉。
- 反证法钉死 attempt 0 被拒的三元组:`(e41db,A,E_t)` 同三元组在 attempt 2 通过了 cast 检查、死于更晚的 segments 检查,因此 attempt 0 被拒的必然是 `(929f,A,E_t)`。
- 根因代码定位:`bind_observed_entities` 的否决条件(修复前 `question_first.py:1030-1031`)要求 close 级 `same_character_as_anchor is True`;降级分支(:1036-1039)置 `identity_confirmed=False`、删 entity_id 但**不动 identity_status**,残留模型照抄来的 `qfs_anchor_agreed_by_window_observation`(该字符串只应由同意分支写入,模型从 already_watched_roles 上下文学来,`pipeline.py:1904-1906`);随后 `pipeline.py:223-235` 把未确认角色从 usable_ranges 剥离,929f 的 usable 由 `["A","B"]` 变 `[]`。

## 3. 根因
**直接原因**:plan 阶段三次尝试分别撞上 cast、角色引用、时域三道校验,repair_limit=2(`pipeline.py:968-970`)耗尽;`finecut_continuation.py:139-164` 的"带警告继续"只救台词窗类违规,cast/角色/时域无兜底。

**根本原因**:代码问题,证据单调性倒挂——close(单张剧照+短切片、合同明示可填 null)的"未核验"否决了 fine(更长窗口+锚点剧照随窗下发+显式 same=true+逐字实体复制+事件/区间证据全链校验)的"已核验"(`question_first.py:1030-1031`)。降级静默且说谎(不改 identity_status,"与锚点一致"的 evidence 文本照常进入 plan 上下文),usable 被剥空后,plan 面对的是提示词与校验自相矛盾的状态;而本模板 4 个节拍全部需要两角色互动、929f 是唯一双人窗口,合法解被系统性清零,模型只能在"删角色/换实体/换时域"间试错至耗尽。四类定性(analysis_15 §四):校验过严/证据链设计倒挂、提示词与校验不一致、反馈不可操作、容错缺失,均属代码问题。

**不是素材不足**:929f 的 8 秒含 b2→b4 完整情绪弧线(惊愕仰望→缓和→讨好笑容,glm_173 events),长者身份有锚点剧照比对证据;attempt 2 的事实反证——模型把两个已确认实体正确组进 cast 并通过全部 cast/focus 校验,缺的只是那个被掏空的双人窗口。golden 路线没踩中,只因太郎单角色高辨识度、close 全程给 true;golden §5 自我标注双角色路径未被覆盖。

## 4. 解决方案(6d743fb)
生产修改仅 `src/omni_story/library/question_first.py`(18 插入/4 删除):
- 新增 `observed_identity_contract()` helper(`question_first.py:1013` 起):合同文本明确 true/false/null 三态语义——`required_role_fields={identity_confirmed: true, same_character_as_anchor: true}`、`allowed_close_agreements=[true, null]`;close 的 null 仅表示"尚未确认,可由本窗口核验",close 的 false 是明确不一致、仍不可绑定。该 helper 同时供 `observation_context`(fine 提示词下发 `qfs_identity_contract`)与 `bind_observed_entities`(校验)消费——提示词与校验从此共用同一份合同。
- `bind_observed_entities` 否决条件改为:fine 级两字段仍须**严格布尔 true**(对象身份比较,数值 1 不冒充 true),close 级仅明确 `False` 否决(null 不再否决),一窗口一实体一角色(`entity not in matched`)、逐字复制 entity_id 等既有检查全部保留。
- 降级分支覆写 `identity_status='qfs_anchor_binding_rejected'` 并沿用"画面可用、个体身份不可绑定"规则——状态文本不再说谎。

**通用性**:纯证据优先级规则(后到、条件更严的核验 ≥ 先到的单切片"未核验"),不含任何影片/人物/台词/角色数特判;锚点实体的建立门槛未降(仍只能由 close `same=true` 的 usable 候选建立,`update_cast_anchors`+`apply_close_constraints`,review_15 独立沿代码核实),放宽的只是"建窗候选的 null"。对 close 已核验的模板(golden 路径)输出逐字节不变(新测试断言 close=true 与 close=null 结果完全一致);对任何 close 诚实回报 null 的双角色/低辨识度模板不再制造死局。无新增拒绝条件、无自动重试/重发/修复预算。配套:`tests/test_qfs_anchor_agreement.py` 5 个用例(+101 行)、fixture `tests/fixtures/e2e_anchor_agreement_20261030/`(glm_173 完整回复、close_004 完整候选、glm_174 cast 数组,均自证据逐字复制,README 记录行号与重建边界)、`tests/test_question_first.py` 1 处断言修改(保留"close 本身不能授予身份",窗口输入改为实际未确认)。

## 5. 为什么不用其他方案
- 调大 repair_limit:治标——状态矛盾下再多预算也是随机游走,attempt 2 已演示模型会在别的维度(时域)引入新错(analysis_15 明确否决)。
- 对粉色娇嫩/乌龟/具体台词加特判:任务规范明令禁止,且证据优先级规则已足够闭合。
- 把 close 的 null 一律当 true:丧失证据单调性——close 的未核验不能当授权,只有 fine 的显式核验才可解除;反幻觉性质(逐字实体、一实体一角色、事件证据)必须保留。
- 反向收紧(close 提示词禁止填 null):把诚实回报改成强迫猜测,增加幻觉风险;null 本是合同明示的合法值,错在下游把它当否决票。
- analysis_15 C(refs 类反馈挂 violation/allowed 集)、D(双角色节拍缺确认窗口时前置暴露为 material_gap):本轮未实施——修复作者判定现有证据不足以认定其为修复本根因所必需;review_15 认定为范围判断而非漏修。留作后续(见 §8)。

## 6. 怎么验收(真实输出,详见 `docs/fix_e2e_anchor_agreement_20261030.md`)
- 新增 5 用例先固化 bug:改前 `5 failed in 0.06s`(`/private/tmp/pfix_anchor_before.txt`);修复后与既有 question-first 测试合计 `199 passed, 1 skipped`。首用例同时断言:close=true 黄金路径输出与 close=null 完全一致、未确认的 B 不被提升、事件证据校验生效、原始回复不变、再归一化幂等、真实提示词携带共享合同;末用例断言修复后 glm_174 的真实重复 cast 被准确暴露为 `plan:duplicate_cast_entity`(带 path/value/expected/repair_hint)而非否决伪影。
- 绑定行为前后实测(真实 glm_173 回复+真实 close_004 候选+同一重建上下文,过真实 `_fine_navigation_points`/`validate_fine`/`plan_field_contract`):改前 `confirmed=[]/usable_roles=[]/allowed_bindings=0` 且残留 `identity_status='qfs_anchor_agreed_by_window_observation'`;改后 `confirmed=["A"]/usable_roles=["A"]/allowed_bindings=1`。
- 全量 pytest:`9 failed, 1617 passed, 104 skipped, 24 errors`——9 个 FAILED 与 `baseline_2879e5c.txt` 逐条一致;24 个 ERROR 均为沙箱禁止 socket.bind(`PermissionError: [Errno 1] Operation not permitted`,test_tts_http.py 本地 TLS 服务),未为绕沙箱改代码,不宣称全绿。
- replay_check:TOTAL ok 143/regression 6/fixed 21/still_failing 33,与 `replay_baseline_98e9427.json` 逐行完全一致;164 条保护行 0 退化、0 缺失。
- fault_check:E1/R1/M1_crash/M2_json/M2_crash 均 0。
- GLM 审核 review_15:`verdict: pass, findings: []`(fixture 与证据逐字一致、锚点建立门槛未降、放宽仅一处且有实测依据、无重发、被改旧断言由真实证据测试补强、独立复跑全量与回放判据集合一致,六项逐条核查)。
- 机械闸门 progress.log:`07:07:45 round 15: GLM review=pass`;`07:09:24 round 15: gates extra_failed=0 new_regressions=0 fault_bad=0`;随后 `DEPLOYED_6d743fb`。
- 部署后同模板嵌套重跑:待跑(截至本记录,progress.log 尚无新 run 行),结果写入 progress.log 后于下一轮补充本记录。

## 7. 量化数据
- 修复前(出险 run):模型调用 176 次(全部 received;fine 观察 13 窗全部 watched,末次 fine 调用 glm_173;逐阶段 reference/questions/skim/close 拆分未记录——推断 fine 为 glm_161–173 共 13 次,与其余 160 次合计 176)、repairs 2、plan 尝试 3 次 3 种错误、911,978 prompt + 109,743 completion tokens、成片 0;06:14:48 启动→06:38:28 停机,约 23 分 40 秒;progress.log 该行记 spent ¥0.0275(与相邻 run 同值,推断为记账口径而非逐 run 实际花费,未核实)。
- 修复后(本地):窗口 929f 的 allowed_bindings 0→1、usable 角色 `[]`→`["A"]`、确认角色 `[]`→`["A"]`;新 5 用例由 5 败→全过;相关测试 199 通过/1 跳过;全量 1617 通过/9 基线失败/104 跳过/24 沙箱错误;回放 0 新退化;故障注入指定 5 项全 0;生产代码净改动 1 文件(18+/4−,全 commit 7 文件 +199/−6)。
- 部署后嵌套重跑对比(耗时/调用数/结局):未记录(待跑,结果写 progress.log 后补)。

## 8. 经验与后续
- 身份类判定应遵守**证据单调性**:后到、条件更严的核验(fine 带锚点剧照)不应被先到的单切片"未核验"(close null)否决。null 是合同允许的诚实回报,下游把它当否决票,就把诚实变成了死局。
- 下发给模型的状态文本必须与执行校验的授权逐字段一致:降级必须覆写 status。本轮修了 `identity_status`,但同类"反馈不可操作"仍在——cast 反馈的 allowed_bindings 只列失败窗口且为空列表(`contracts.py:807`)、refs 裸 ValueError 无 allowed 集(analysis_15 C 未实施),遇新的状态矛盾仍可能重演"越修越糟直至耗尽"(与 INC-20261029-11 同型)。
- analysis_15 D(双人节拍缺确认窗口时前置暴露为 material_gap)未实施:作为纵深防御,若将来再出现"合法解被系统性清零",应把它变成显式缺项走 partial 路径,而不是让协议修复循环烧预算。
- plan 期修正耗尽即整跑报废(零产出)仍无降级通道——与 INC-20261027-07 修的"渲染后耗尽丢已渲染版本"不同层面;本轮证据表明正确解法是消除矛盾状态,而非加预算。
- 本 run 同时是 INC-20261030-03(49ef83d)修复后的嵌套重跑:fine 窗口 13/13 全部收到回复并 watched,该修复线上验证通过;INC-20261030-02(61dca10,draft_outside_real_segments)仍未获得线上验证(本 run 未到渲染)。
- 遗留:账本里含"close=null 且 fine 已确认"旧观察的在途任务,resume 时重推导与存储视图不一致会触发 `watched_window_not_bound_to_received_fine` 显式停机(review_15 边界说明,属既有防篡改设计;受保护 replay 语料无此行)。
- 部署后同模板嵌套重跑结果待补(progress.log)。
