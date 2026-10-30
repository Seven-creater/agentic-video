# INC-20261030-05 plan 段角色事件证据错误无 violation,兜底反馈把修复引向时间域误改,3 次机会振荡耗尽(马冬梅,168 次调用零产出)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 上午(e2e 自动修复第 17 轮;出险 run `e2e_19_马冬梅_10300756`,10-30 07:56:37 启动、08:18:12 停机) |
| 影响范围 | 创作路线(reference_rough_skill_v1)粗剪规划 plan_0 的角色-事件证据校验与修复反馈;致命停机——凡"第二角色未确认/未绑定锚点"的双角色模板,plan 选段写已确认角色而所选子区间无其事件证据时,错误无诊断载荷、反馈退化为"查时间域",修复预算在换角色/换时间域间振荡烧光,整跑零产出(本 run 168 次调用) |
| 状态 | 已修复(de13326);部署后同模板嵌套重跑结果待补 |
| 相关提交 | de13326(修复,8 文件 +728/−12,生产代码仅 `contracts.py` 与 `protocol_feedback.py`);出险版本为部署 release `aa47a93_creative_6d743fb`(第 15 轮修复);该检查的引入提交未记录 |
| 相关任务 | 服务器 run `/home/ubuntu/apps/agentic-video/shared/jobs/e2e_19_马冬梅_10300756`(模板 19_马冬梅,检索源 `src_24675c65978d5360`《功夫熊猫2008》);证据 `out/e2e/evidence_19_马冬梅_16.txt`;根因分析 `out/e2e/analysis_17.md`;修复报告 `out/e2e/fix_17.md` 及 `docs/fix_e2e_plan_role_events_20261030.md`;审核 `out/e2e/review_17.md`(pass);闸门 `out/e2e/progress.log` round 17 行 |

## 1. 问题长什么样
run_outcome.json:`outcome/status: stopped`、`stage: plan_0`、`error: model_protocol_repair_exhausted:plan_0`、`exception_type: ValueError`、`raised_at: …/releases/aa47a93_creative_6d743fb/src/omni_story/library/pipeline.py:1078`、`model_calls: 168`、`repairs: 2`、`final_video: null`、`last_call_id: glm_168_plan_0_repair2`。chain_failure.json:`requests: 168`、808,695 prompt + 111,655 completion tokens(token 口径 `captured_responses_only_unknown_charges_not_zero`)、`uncertain_calls: []`;调用计数 `Counter({'received': 168})`——168 次调用全部收到回复,窗口观察全部 watched,死在 plan。

plan_0 三次尝试、错误序列(逐次 protocol_failure.json 原文在证据内):
- attempt 0(glm_166_plan_0,证据 :82-96):`plan:segment_role_missing_overlapping_event_evidence`。seg3:`window_0e838dee4b8e796e`、`source_in_s: 518.0 / source_out_s: 521.0`、`role_ids: ["A_asker"]`(可定位的违规段为 seg3 属推断,依据见 §2);
- attempt 1(glm_167_plan_0_repair,:97-111):`visual_library:range_outside_content`。模型把 5 段时间全改成 `0.0-1.0 / 6.0-7.0 / 11.0-13.0 / 14.0-17.0 / 0.0-11.0`——seg5 的 `[0,11]` 恰等于 window_5384d979fab6b4a5 的 usable 局部区间 `[0,11]`,即把窗口局部秒当原片绝对秒填入 `source_in_s/source_out_s`,落在影片 content_range 之外被拒;seg3 角色同时改成未确认的 `["B_seated_listener"]`;
- attempt 2(glm_168_plan_0_repair2,:112-126):时间按修复 1 反馈的窗口边界提示改回绝对秒 `212/218/518-520/521-524/1719.5-1730.5`,seg3 角色改回 `["A_asker"]`(区间仅缩 1 秒)→ 与首答同错复现 → 修复预算(repair_limit=2)耗尽。

窗口观察现场(glm_165_fine_5384d979fab6b4a5,证据 :68-81):A_asker `identity_confirmed: true` 且逐字绑定 qfs 锚点实体;B_seated_listener `identity_confirmed: false`("无对应锚点实体…未绑定qfs实体");events 三条(0-2/3-8/9-11 秒)均含双角色;usable_ranges 0-11 秒全程 `role_ids: ["A_asker","B_seated_listener"]`——观察在向规划"广告"A 全程可用。

## 2. 怎么查出来的
- 证据包逐字段核对(run_outcome、chain_failure、traceback、三次 protocol_failure、glm_165 回复、questions/skim/close/report),并与黑虎阿福 golden 路径(`docs/golden_path_heihu_98e9427.md`)逐阶段对照(analysis_17 §一):阶段 1–5 全部在标准路线上——出题 4 节拍 b1–b4 含 must_see/equivalents/dub_can_carry;粗看 `qfs_skim_2_6` "no tiles list" 丢单张按 c3beebb 规则记入 unknown_calls 不停跑;精看 report.json 有 partial/usable 候选、casting=阿宝+平先生、无 material_gap;窗口观察全部 watched。**第一个偏离阶段是阶段 6 plan_0**。
- 违规段定位(推断):① `validate_plan` 按序检查,seg3 已通过 content range、窗口边界、confirmed_roles、usable_range 支持四关,否则会先抛别的错;② 修复 2 中模型对 seg3 的自述"窗口内该区间角色标签为已确认问话者,鸟类角色未确认故不作台词"说明被抱怨的正是该段。修复报告同时指出边界:出险窗口 window_0e838dee 的完整 fine 观察**不在脱敏证据内**,"518–521 内只有未确认 B 角色事件"这一事件边界是推断而非原始事实;glm_165 是另一窗口(window_5384)的观察,其自身 2–3 秒/8–9 秒的事件空隙已足以独立复现同一条裸错误(测试据此构造,与真实回复明确区分)。
- 时域误改的反推:seg5 局部 `[0,11]` 与 window_5384 的 usable 局部区间逐值相等 → 模型把局部秒填进绝对秒字段,落在影片 content_range 之外(片头厂标区)被 `content_ranges.py:29` 拒绝。
- 代码定位(修复前行号):该错误由裸 `require` 抛出、**无 `.violation`**(`contracts.py:930-934`;对比十行之上 `plan:range_not_supported_by_fine_observation` 用 PlanEvidenceError+violation+allowed 列表,`contracts.py:916-927`);`protocol_feedback._plan` 的逐段扫描(`protocol_feedback.py:178-211`)没有角色-事件重叠检测器 → `diagnose` 走兜底分支(`protocol_feedback.py:339-341`),hint=`按validation_error核对必填字段、类型、枚举、ID及时间域；保留实际证据，不补造画面。`;修复提示词固定前言也在说"只修复JSON字段、ID和时间域"(`pipeline.py:1117`)。
- 修复阶段对分析的两处修正(经独立核对):基础规划模板 `prompts.py:109` 本就写过子区间事件规则,分析中"完全没有告诉模型"过强,缺口实在 `selected_range` 字段合同(`contracts.py:141-142` 只要求"完整区间及 role_ids 受同一条 usable_range 支持")与诊断未落实;"固定前言导致改时间""跨轮记忆必不可少"两点证据不足以定因果,据此划定了本轮修复边界(见 §4/§5)。

## 3. 根因
**直接原因**:seg3 的 `role_ids=["A_asker"]` 在所选子区间内没有该角色的事件证据,被 plan 级子区间检查拒绝;两次修复在"换角色/换时间域"之间振荡,3 次机会(repair_limit=2)耗尽,`pipeline.py:1078` 抛 `model_protocol_repair_exhausted:plan_0`,整跑 stopped。

**根本原因(代码问题,非素材不足;三类叠加)**:
1. **校验口径跨阶段不一致(陷阱本身)**:fine 级只保证 usable_range **整条区间**上"角色 ∈ 事件角色并集"(`contracts.py:760-761`),plan 级却要求**子区间**与该角色事件相交(`contracts.py:930-934`)。creative 路线 `visual_only=True`(`pipeline.py:1562`)下,未确认角色被从 usable_ranges.role_ids 剥离(`pipeline.py:223-235`,因 `validate_fine` 要求 usable 角色已确认),观察向规划广告"A 全程 0–11 可用",模型照选 518–521 再被子区间检查拒绝;`selected_range` 字段合同(`contracts.py:141-142`)只写"受同一条 usable_range 支持"——模型完全遵守该句仍被十行之下的另一条规则拒绝,属提示词/合同与校验不一致。
2. **反馈不可操作且带误导**:该错误无 violation、无对应检测器,`diagnose` 退化为通用兜底,兜底 hint 与固定前言双双指向"时间域",把一次可收敛的修复引向必然失败的时间域改写(修复 1 被浪费的全部原因——分析认定;修复报告认为前言因果证据不足,见 §2)。
3. **容错缺失(反馈失忆)**:每轮修复反馈只由刚失败的回复生成(`pipeline.py:1091-1120`),首轮未解决的角色违规在第 2 轮反馈中消失,模型把时间改回去、角色问题原样复现(分析认定;本轮未改,理由见 §5)。

**不是素材不足的证明**:合法解一直存在且完全机械可推导——把区间挪到与任一 A_asker 事件相交即可(半开区间严格不等式,1 帧重叠都行,`contracts.py:931`);或对未确认角色的反应镜头写 `role_ids=[]`(`refs(..., nonempty=False)` 与两处子集判断均接受空集,正是剥离规则 `allowed_use='visual_only_not_individual_dialogue'` 承诺的用法)。出题阶段就为双角色做了准备(casting=阿宝+平先生、b2/b3 的 dub_can_carry),首答计划也确实绕开了给 B 配音——它只需要 B 的像素,而系统设计本就允许只用像素。黑虎阿福能过此阶段,只因单确认角色模板不经过这条检查路径;同类"第二角色未确认"的双角色问答模板都会踩中,与本模板具体台词无关。

## 4. 解决方案(de13326)
生产代码 2 文件(+70/−12):
- `src/omni_story/library/contracts.py`:新增 `plan_segment_role_contract()`(`contracts.py:130`),由窗口偏移、roles、events **机械推导**已确认角色集合与每个角色的原片绝对秒事件区间(`role_event_ranges`);`plan_field_contract` 把同一 helper 的结果下发进创作规划提示,`selected_range` 合同补上完整条件("所选子区间须与每个所写角色的至少一个事件区间相交;未确认角色不得写入 role_ids,其反应镜头用 role_ids=[]"),repair_hint 同时写明 role_event_ranges 是原片绝对秒、不能直接填窗口局部秒数。校验侧 `validate_plan_segment_roles()`(`contracts.py:159`)复用同一合同,错误保留原错误码 `plan:segment_role_missing_overlapping_event_evidence`,补齐 `path/value/expected/repair_hint`(含 `confirmed_role_ids`、各角色事件区间、`overlapping_role_ids`);重叠判断保持原有的局部时间半开区间严格不等式,不因改用绝对秒浮点运算而变边界。
- `src/omni_story/library/protocol_feedback.py`:`_plan` 沿现有逐段扫描收集当前可检测的**全部**角色问题——时间错误先触发时也能同时指出未确认角色;时间不在窗口内的段落只检查身份(`check_overlap=False`),不生成误导性事件重叠建议。
- **明确不做**:不放宽检查(错误码、refs 语义、半开区间不等式、位置与容差均原样;`role_ids=[]` 与区间相交两条合法通道是**原有行为**,测试只钉住未被破坏);不新增重试/重发/跨轮记忆,`repair_limit` 与修复前言未动(修复作者对分析根因 3 不采纳有据:单轮内全量反馈已覆盖"失忆"的主要面;review_17 认定为范围判断而非漏修)。

**通用性**:合同与 violation 全部由 `(roles, events, 窗口偏移)` 机械推导,不含任何模板/角色/台词/影片特化;提示、校验、反馈三处共用同一 helper(测试断言两侧 `role_event_ranges` 逐值相等)。对任意参考模板(任意角色数、任意确认状态组合、任意窗口数)成立;库模式共用同一校验器,只共享诊断增强,接受条件未变。黑虎阿福类单角色模板的既有违规反馈不受影响(只增不改)。

配套:`tests/test_plan_role_event_feedback.py` 7 用例;fixtures `tests/fixtures/e2e_plan_role_events_20261030/` 5 文件(glm_165 完整回复 + 三轮 plan 未截断的六个顶层字段,README 记录证据行号来源与截断边界,未补造缺失的首个失败窗口观察)。

## 5. 为什么不用其他方案
- 调大 repair_limit:治标——反馈不可操作时再多预算也是振荡,attempt 1 已演示模型会在别的维度(时域)引入新错。
- 对马冬梅/熊猫/具体台词加特判:任务规范禁止,且机械推导已闭合,无此必要。
- 把 usable_range 按角色事件覆盖拆成带角色标签子段(analysis_17 B 的强变体):拆段改变 fine 合同形状、影响 `recorded_view` 新旧视图比对——本轮先做 additive 形式(字段合同补条件+诊断载荷),拆段留作口径差再现时的选项。
- 跨轮携带未解决违规 + 按错误类别生成前言 + 振荡检测(analysis_17 C):证据不足以证明其因果必要性,本轮未做;若修复循环再次出现"改回原值"式振荡,优先补这条。
- 给该错误开"带警告继续"的口子(analysis_17 D):角色事件证据是证据完整性约束,放行等于允许给像素凭空派角色;`accept_exhausted_line_window_plan` 只放行台词窗类(`pipeline.py:1074-1078`)维持不动。
- review_17 两条 low 保留意见不阻塞、未修:helper 对非有限时间值的潜在 KeyError(当前两个调用点均先经数值/区间校验,调用图不可达)、时间越界段落 expected 里的空 `overlapping_role_ids` 噪声(不参与判定)。

## 6. 怎么验收(真实输出详见 `docs/fix_e2e_plan_role_events_20261030.md`)
- 7 个新用例先红后绿:改前 `7 failed in 0.21s`;改后相关测试(含字幕证据校验)`191 passed in 4.60s`。覆盖:事件空隙复现同一条裸错误、共享提示合同两侧 `role_event_ranges` 逐值相等、原片时间域、三轮真实规划字段、独立问题同时反馈、原有修复上限与历史请求不重发。
- 全量 pytest:`9 failed, 1624 passed, 104 skipped, 24 errors`(15.52s);9 个 FAILED 与 `baseline_2879e5c.txt` 逐条一致(`FAILED outside baseline: []`),24 个 ERROR 均为沙箱禁止本地端口监听的 `PermissionError: [Errno 1] Operation not permitted`,未为绕沙箱改代码,不称全绿。
- replay_check:TOTAL ok 143/regression 6/fixed 21/still_failing 33,与 `replay_baseline_98e9427.json` 完全一致;164 条保护行 0 退化、0 缺失、0 转 regression/still_failing。
- fault_check(9,984 次验证):指定五项 E1/R1/M1_crash/M2_json/M2_crash 全部为 0。
- GLM 审核 review_17:`verdict: pass`(fixture 与证据逐字一致、提示与校验共用同一 helper 且逐值相等、未放宽任何检查、无重试/重发、库模式仅共享诊断;沙箱禁 Python 未独立复跑,接受度声明与粘贴输出核对+全代码路径手动追踪);两条 low 保留意见见 §5。
- 机械闸门 progress.log:`10-30 08:48:51 round 17: GLM review=pass`;`10-30 08:50:32 round 17: gates extra_failed=0 new_regressions=0 fault_bad=0`;随后 `DEPLOYED_de13326`。
- 部署后同模板嵌套重跑:待跑(截至本记录 progress.log 尚无新 run 行),结果写入后于下一轮补充本记录。
- 范围声明:本轮为离线修复验证,未跑付费端到端;不把反馈改善宣称为已生成成功成片。

## 7. 量化数据
- 修复前(出险 run):模型调用 168 次(全部 received;逐阶段 reference/questions/skim/close/fine 拆分未记录)、repairs 2、plan 尝试 3 次 2 种错误(角色证据 ×2、时域 ×1)、808,695 prompt + 111,655 completion tokens、成片 0;07:56:37 启动→08:18:12 停机,约 21 分 35 秒;progress.log 该行记 spent ¥0.0524(推断为记账口径而非逐 run 实际花费,与 INC-20261030-04 同疑,未核实)。
- 修复后(本地):7 用例由 7 败→全过;相关测试 191 通过;全量 1,624 通过/9 基线失败/104 跳过/24 沙箱错误;回放 0 新退化(164 条保护行完整);故障注入指定 5 项全 0;生产代码净改动 2 文件 +70/−12,连同测试与夹具 commit de13326 共 8 文件 +728/−12。
- 部署后嵌套重跑对比(耗时/调用数/结局):未记录(待跑,结果写 progress.log 后补)。

## 8. 经验与后续
- 每一道校验约束都要有**可达的可执行反馈通道**:黑虎阿福在 plan 阶段收敛,靠的是 `plan:duplicate_cast_entity`/台词窗违规都带 violation+allowed 清单;本错误裸 require+无检测器,就退化成"查时间域"的兜底。新增约束前先问"拒绝时模型能拿到什么"。(与 INC-20261029-11、INC-20261030-04 同型第三例。)
- 提示词、字段合同、校验、反馈应**共用同一份机械推导**:本轮一份 `plan_segment_role_contract` 三处消费,审核专门核对两侧输出逐值相等——这是防止"合同文本与校验不一致"再发生的结构性办法。
- fine"整条 usable_range 角色并集"与 plan"子区间相交"的口径差**仍在**(本轮未改 fine 合同形状,只在 plan 侧补条件与诊断):若同类"广告了却选不得"的空洞再现,再评估拆段变体(analysis_17 B 强变体)。
- 修复循环跨轮失忆/前言误导(analysis_17 C)未修,留作振荡复现时的第一优先项;review_17 两条 low(helper 潜在 KeyError、越界段落空 `overlapping_role_ids` 噪声)留作观察。
- 部署验证状态:同一 release(aa47a93_creative_6d743fb)上,INC-20261030-04(6d743fb)的嵌套重跑已于 07:56:32 通过 plan_0 到达终审(以 `needs_visual_revision` 停,属终审修订需求,非 plan_0 耗尽)——该修复获得线上验证;本 run 是该 release 的下一个新模板首跑。de13326 部署后的同模板嵌套重跑结果待补。
- progress.log 未见 round 16 行(第 15 轮重跑结果与 e2e_19 启动之间无修复轮,round 17 直接处理 e2e_19);原因未记录。
