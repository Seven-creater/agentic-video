# INC-20261030-03 遗留 1 次 uncertain qfs 调用令 JS 发送护栏拒发全部 17 个 fine 窗口,零观察窗口死路停机(粉色娇嫩,185 次调用零产出)

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-10-30 凌晨(e2e 自动修复第 14 轮;出险 run 是第 13 轮修复部署后的嵌套重跑,05:42:20 停机) |
| 影响范围 | 创作路线(reference_rough_skill_v1)qfs 检索→fine 窗口观察;致命停机——账本里遗留**任一** uncertain qfs 调用,后续所有走 opencode MCP 通道的付费调用(fine/plan)都会在 HTTP 发送前被拒,整跑零产出 |
| 状态 | 已修复(49ef83d);部署后同模板嵌套重跑结果待补 |
| 相关提交 | 49ef83d(修复,6 文件 +188 行,其中生产代码仅 1 文件 +11 行);出险版本为部署 release `aa47a93_creative_61dca10`;护栏"全部历史调用须 received"要求的引入提交未记录 |
| 相关任务 | 服务器 run `/home/ubuntu/apps/agentic-video/shared/jobs/e2e_18_粉色娇嫩_10300421/evaluations/fix13`(模板 18_粉色娇嫩/01.mp4,10-30 05:20:47 启动);证据 `out/e2e/evidence_18_粉色娇嫩_13.txt`;第一轮分析 `out/e2e/analysis_14.md`(传输故障说,后被修正);修复报告 `out/e2e/fix_14.md` 及 `docs/fix_e2e_qfs_unknown_20261030.md`;审核 `out/e2e/review_14.md`(pass);闸门 `out/e2e/progress.log` round 14 行 |

## 1. 问题长什么样
run_outcome.json:`outcome/status: stopped`、`stage: pipeline.execute`、`error: no_watched_window_available_for_planning`、`exception_type: LibraryStopped`、`raised_at: …/releases/aa47a93_creative_61dca10/src/omni_story/library/pipeline.py:2010`、`model_calls: 185`、`repairs: 0`、`last_call_id: glm_185_fine_9196937b42bd2fac`、`final_video: null`。chain_failure.json:`requests: 185`、761,177 prompt + 95,272 completion tokens、`uncertain_calls: ["glm_020_qfs_skim_1_17"]`、`automatic_restart: false`。账本计数:`185 = received 167 + failed_known 17 + uncertain 1`。

算术拆解:`185 = 1(reference) + 1(questions) + 136(skim) + 30(close) + 17(fine)`;`167 received = 1 + 1 + 135 + 30`。即检索链(出题→粗看→精看)全部成功、产出 17 个可观察窗口;**17 个 fine 窗口调用 0 个收到回复、全部记 failed_known、0 次修复**,逐窗被丢弃后 `windows == []`,死于 `pipeline.py:2010` 的死路停止码——早于任何 plan 调用。`question_first:material_gap` 未触发(可观察窗口本有 17 个),反证不是素材不足。17 次失败的逐次诊断串(failure.json 正文、mcp_http.jsonl)不在证据内(见 §2 证据缺口)。

## 2. 怎么查出来的
- 证据包逐字段核对 + 与 golden(黑虎阿福)及 run 12(同库同模板,840ea5c,fine 18 窗口 + 2 修复全部 received)逐阶段对照:前四阶段一致,唯一致命偏离是 fine 阶段 0 received;`repairs: 0` 与"修复通道只覆盖已收到但校验失败的回复"互证(analysis_14.md §1)。
- 第一轮定位(analysis_14.md,方向后被修正):把 17 个 failed_known 理解为服务端传输故障,据 `_outcome_unknown`(pipeline.py:833)定义推断"账本可证未付费、可安全重发",提出 F1 有界重试/F2 新停止码+熔断/F3 续跑准入。但证据缺逐次 failure.json 与 mcp_http.jsonl,**服务端故障说无法证实**。
- 修复代理改从代码链路找确定性解释(fix_14,review_14 复核确认):
  - Python 账本层早已允许"uncertain qfs 之后继续"——`state.py:187` `_qfs_direct()`、`state.py:218` `free_qfs()` 核对原始请求哈希后,独立 qfs 检索的 uncertain 结果只丢该次证据不阻塞后续(黄金路径 a5d692d 规则,主题同 INC-20261029-08);`question_first.py` 把它记入 unknown_calls 后照常前进,原请求绝不重发。本次 uncertain 的 `glm_020_qfs_skim_1_17` 正是这样被丢掉一张 sheet 后继续的——其余 135 次粗看、30 次精看全部 received,qfs 直连通道(`qfs_direct_https_v1`→open.bigmodel.cn)不经 MCP 护栏。
  - 但 JS 发送护栏 `mcp_opencode_guard.mjs` 的发送前准入 `require(… state.calls.slice(0,-1).every(row => row.status === 'received' || …))` 扫描**不分通道的全账本历史**且要求全部 received。reference/questions 在 uncertain 行出现之前已完成,fine 是之后第一批(也是仅有的)opencode MCP 通道调用——于是每个 fine 调用在 Python 已准许创建后、真正 HTTP POST 之前被拒。
  - 死亡链:发送前被拒→无 HTTP 请求/响应条目→`_outcome_unknown`(pipeline.py:833)=False→记 failed_known→命中 `_known_fine_transport_failure`(pipeline.py:586)的传输失败通道→记 `fine_observation_transport_failure` 工件(pipeline.py:1944)并 `continue` 丢窗→17/17 丢完→`pipeline.py:2010` 停。**无需假设任何服务端故障即可解释全部 17 次失败**(review_14 第 1 项)。
- 复现固化:新增 2 个测试对旧代码实际运行,均失败于 `library_mcp_opencode_current_job_or_previous_outcome_invalid`、无实际发送(修复报告留有改前真实输出);修复后通过。GLM 审核独立复跑全量 pytest,9 个 FAILED 与 `baseline_2879e5c.txt` 完全一致。

## 3. 根因
**直接原因**:17 个 fine 调用全部在 HTTP 发送之前被 MCP 发送护栏以"既往调用结果无效"拒绝,账本记为 failed_known,每个窗口被 `_known_fine_transport_failure` 依设计丢弃,零观察窗口触发死路停止码。
**根本原因**:代码问题(流程分支/容错缺失,跨层规则不同步)——"单次独立 qfs 检索结果未知只丢该次证据、不阻塞后续工作"这条通用规则只在 Python 账本层(INC-20261029-08 修账本层、a5d692d 黄金规则)落地,**没有同步到 JS 发送护栏**;两道门对同一账本事实判定相反,第二道门把第一道门已放行的调用在发送边界杀掉。一个遗留的 uncertain qfs 行就能让此后所有 MCP 通道付费调用全灭。
**不是**:素材不足(17 个可观察窗口已产出、material_gap 未触发);提示词/校验问题(0 个 fine 回复到达,校验未运行);也无需假设外部服务故障(代码级复现已解释全部 17 次;服务端是否另有故障因证据缺口不可证)。附带可运维性缺陷:停止码 `no_watched_window_available_for_planning` + `stage: pipeline.execute` 把护栏/传输类故障误指向"规划/素材"侧,triage 按 golden 清单查规划项全部查空(analysis_14 §2)。

## 4. 解决方案(49ef83d)
生产修改仅 `src/omni_story/library/mcp_opencode_guard.mjs:79-88` 新增 `droppedQuestionFirst(row)`,并在 `:90-91` 加入发送前 `.every()` 准入。放行需**同时**满足:
- `config.workflow === 'reference_rough_skill_v1'`(创作路线;qfs 只在该 workflow 下与 fine 共存,`server_cli.py:415` 强制);
- `row.status === 'uncertain'` 且 `row.name` 以 `qfs_` 开头;
- `recorded()` 按 `request_sha256` 哈希核验后的原始 request.json 逐项匹配 Python `_qfs_direct()` 的四项协议身份:`stage === 'question_first'`、`provider === 'qfs_direct_https_v1'`、`endpoint === 'https://open.bigmodel.cn/api/paas/v4/chat/completions'`、`payload.model === 'glm-5.3-flash'`。

语义:把账本层既有的"uncertain qfs 不毒化后续创作工作"规则**原样延伸到真正发送请求的边界**,常量与 Python 逐项相同;哈希核验保证篡改 request.json、伪装名称、错 stage/provider/endpoint/model(即使重算哈希)均仍被拒。**不变式**:不加任何自动重试/重发(unknown 同 digest 仍抛 `recorded_request_not_received_no_replay`,opencode_provider.py:287);library/audio 工作流行为不变;传输健康且无遗留 uncertain qfs 时零行为变化(golden 路径不触发新分支)。
配套:`tests/test_qfs_mcp_continuation.py` 2 个用例(+130 行);fixture `tests/fixtures/e2e_qfs_unknown_20261030/`(run_outcome/unknown_calls 逐字节取自本次证据,fine_response 取自 run 12 真实回复;缺失的原始失败请求由本地 DirectTransport 合成,README 区分真实证据与合成输入——review_14 逐字节核对确认)。
**通用性**:判断只依赖协议身份字段与账本状态,不含模板、人物、台词、影片、窗口编号、节拍数;任何参考模板走到 fine 阶段遇到遗留 uncertain qfs 调用同样受益。

## 5. 为什么不用其他方案
- analysis_14 F1:对 failed_known fine 调用做有界重试(每窗口 ≤1 次、全局 ≤N):重发违反仓库 no-replay 铁律;且根因不在传输层,重试只会再被同一护栏拒 17 次(review_14 第 4 项核实未采纳)。
- analysis_14 F2:新停止码 `fine_transport_all_windows_lost` + 同签名熔断:根因消除后该分支不可达;熔断只是少烧几次仍零产出,治标不治本。停止码语义拆分留待将来真正需要时再做(见 §8)。
- analysis_14 F3:续跑准入放行已知失败窗口重请求:扩大重发授权,违背防重复付费护栏的设计意图。
- 放宽 `failed_known` 归类或 `_outcome_unknown` 判定:破坏防重复付费护栏语义——修复报告明确"不能把 failed_known 等同于已证明没有计费"。
- 反向对齐(Python 侧把 uncertain qfs 改为阻塞后续):退回 INC-20261029-08 修掉的"一次未知免费调用挡住全部付费流程",方向反了。
- 对粉色娇嫩/具体窗口特判:任务规范明令禁止,且协议身份谓词已足够闭合。

## 6. 怎么验收(均为真实输出,详见 `docs/fix_e2e_qfs_unknown_20261030.md`)
- 新增 2 测试先固化 bug:改前 `2 failed in 0.20s`,错误均为 `library_mcp_opencode_current_job_or_previous_outcome_invalid`(无实际发送);修复后相关测试 `249 passed, 1 skipped in 8.08s`。通过用例断言:fine、received 格式修复、plan 各真实发送一次(共 3 次)、无 retry_permit、同摘要请求仍拒重发;负对照(submitted/failed_known 状态、library/audio 工作流、篡改 stage/provider/endpoint/model 重算哈希后仍拒、伪装名称、篡改 request.json 哈希不匹配拒)全部仍拒。
- 全量 pytest:`9 failed, 1612 passed, 104 skipped, 24 errors`——9 个 FAILED 与 `baseline_2879e5c.txt` 逐条一致;24 个 ERROR 均为沙箱禁止 socket.bind(`PermissionError: [Errno 1] Operation not permitted`,test_tts_http.py 本地 TLS 服务),未为绕沙箱改代码,不宣称全绿。
- replay_check:TOTAL ok 143/regression 6/fixed 21/still_failing 33,与 `replay_baseline_98e9427.json` 完全一致;164 条保护行 0 退化、0 缺失。
- fault_check:E1/R1/M1_crash/M2_json/M2_crash 均 0。
- GLM 审核 review_14:`verdict: pass, findings: []`(根因代码级可证、非模板特化、未放宽过度、无重发、fixture 真实性、独立复跑六项逐条核查)。
- 机械闸门 progress.log:`06:08:23 round 14: GLM review=pass`;`06:10:02 round 14: gates extra_failed=0 new_regressions=0 fault_bad=0`;随后 `DEPLOYED_49ef83d`。
- 部署后同模板嵌套重跑:待跑(截至本记录,progress.log 尚无新 run 行),结果写入 progress.log 后于下一轮补充本记录。

## 7. 量化数据
- 修复前(出险 run):模型调用 185 次(167 received/17 failed_known/1 uncertain;761,177 prompt + 95,272 completion tokens)、repairs 0、观察窗口 0/17、成片 0;05:20:47 启动→05:42:20 停机,约 21 分 33 秒;progress.log 该行记 spent ¥0.0275(与相邻两 run 同值,推断为记账口径而非逐 run 实际花费,未核实)。
- 修复后(本地):新增 2 用例 2 失败→2 通过;相关测试 249 通过/1 跳过;全量 1612 通过/9 基线失败/104 跳过/24 沙箱错误;回放 0 新退化;故障注入指定 5 项全 0;生产代码净增 11 行(1 文件)。
- 部署后嵌套重跑对比(耗时/调用数/结局):未记录(进行中,结果写 progress.log 后补)。

## 8. 经验与后续
- 同一条规则在跨语言的两道门(Python 账本准入 + JS 发送护栏)必须同步维护:INC-20261029-08 修了账本层,发送边界残留同病,本次才暴露。四个协议常量现于 Python/JS 各一份,需同步改(review_14 备注 a:与该护栏文件 PROVIDER/TRANSPORT/POLICY 既有跨语言重复风格一致,暂按惯例接受)。
- 证据缺口会带偏排查方向:analysis_14 因缺逐次 failure.json 与 mcp_http.jsonl,把 failed_known 误判为服务端传输故障并导出重试类方案。账本计数只能钉死"17 个 fine 全部已知失败",钉不死失败发生在哪一层。今后 e2e 证据采集应包含逐次 failure.json 正文与 mcp_http.jsonl(analysis_14 §6 已列)。
- 停止码语义未改:`no_watched_window_available_for_planning` 仍会把"观察阶段全灭"误指向规划/素材。本轮根因消除后该路径概率大降,语义拆分留待真正需要时再做。
- 遗留 1:已被毒化的旧任务目录(17 个 failed_known)续跑仍会被 no-replay 策略拒绝(review_14 备注 b:既有策略,非本修复范围;新任务不再触发本根因)。
- 遗留 2:本次嵌套重跑停在 fine 阶段、未到 plan 期,INC-20261030-02(61dca10)的线上验证仍待后续重跑。
- 遗留 3:部署后同模板嵌套重跑结果待补(progress.log)。
