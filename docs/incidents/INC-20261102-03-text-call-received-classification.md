# INC-20261102-03 reasoning 撞满 8192 输出上限的已应答文本调用被记 uncertain，又被当"未知媒体"拦住粗剪渲染复用：赵德汉、雪山 default3 双停；完成步骤＝已送达、文本调用不绑媒体

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-02 |
| 影响范围 | 文本调用（text_completion）结果分类、未知调用的媒体拦截、旧账本接纳（parent_baseline/--resume-from）；picture_aware 的 advisory 审查按规格"绝不为此停机"却把两个任务停死——致命停机（赵德汉 default3、雪山 default3） |
| 状态 | 已修复（50f5a71；两条低危边角遗留，见第 8 节） |
| 相关提交 | 50f5a71；前置 5e697f6/d5f88ae（INC-20261102-02）、93edb02（S25 引入审查） |
| 相关任务 | sb_20_赵德汉藏钱_11010204/evaluations/theme1/evaluations/film1/evaluations/default3（release d5f88ae；证据副本 .agent-team/out/s27/evidence/：agent_events.jsonl、request.json、failure.json、library_state.json、run_outcome.json、silent_review.json）；雪山 default3（同族停机）；旧账本 sb_01_不烧心_11010232/…/default1；任务 .agent-team/tasks/S27_text_call_received_classification.prompt.md；报告 .agent-team/out/s27/S27_fix.md、S27_review.json（审查 verdict pass，2 条低危发现） |

## 1. 问题长什么样
- 赵德汉 default3（默认风格 picture_aware）：`calls/glm_009_silent_review_1`（text_completion，request 29.8 KB）的 agent_events.jsonl 有 `step_start` → `step_finish`（`reason: "length"`），tokens output 12、reasoning 8180——推理吃光 8192 输出预算，正文无文本。
- `_submit_text` 对此抛 ValueError 并记 `uncertain`（failure.json `{"error":"ValueError","uncertain":true}`）。服务商**已经应答**：这是"已收到、无效的回复"，不是"结果未知"。
- 随后整跑停 `unknown_media_must_not_be_resubmitted`（当时 state.py:249 的 begin_call）：该 uncertain 文本调用与粗剪渲染媒体 render_1 关联，精剪阶段合法复用该渲染被拦——advisory 审查（S25 规格"绝不为此停机"）实际把跑停了。
- 同族另一形状（旧账本）：不烧心 default1 `glm_009_silent_review_0`（INC-20261102-02 的 spawn E2BIG：OSError、空事件）在传输修复后仍拦 resume/parent_baseline：`parent_text_check_outcome_unknown_no_replay`。
- 受影响停机任务：赵德汉 default3、雪山 default3。

## 2. 怎么查出来的
- 读 agent_events.jsonl：完成步骤（step_finish）在场、reason＝length、reasoning 8180＋output 12＝8192 恰好撞顶——服务商已应答，只是正文为空。
- 读 failure.json 对照代码：`_submit_text` 只有拿到可解析文本才算成功，任何异常一律 uncertain——缺"完成步骤＝已送达"这层判定。
- 追停机链：state.py begin_call 的 unknown 媒体拦截不区分调用是否真的携带媒体——text_completion 从不发送媒体，却被计入 render_1 的未知依赖，拦住精剪对该渲染的合法复用。
- 服务器复放（修复期验证）：对同一截断请求分别用 8192 与 32768 输出上限重放——8192 时该步停止、reasoning 5774、JSON 可解析；32768 时停止、reasoning 3663、JSON 可解析。原截断是推理预算的巧合，不是请求本身坏。

## 3. 根因
三个代码问题叠加：
1. 分类缺失（提示词/校验与账本语义不一致）：没有"agent 步骤已完成（step_finish，任意 reason 含 length）即已收到回复"的判定；空正文回复（已应答）被当"结果未知"，进而触发全局 no-replay。
2. 媒体耦合错误（流程分支）：unknown 媒体拦截（防"未知付费媒体被重复提交"）把从不携带媒体的 text_completion 也计入——文本审查的未知毒化了渲染媒体的复用。
3. 输出预算过低（配置）：文本检查 agent 沿用 8192 输出上限，而 reasoning 型模型的思考 token 计入输出预算，长证据下仅推理即可吃光预算（8180＋12＝8192）。

直接原因是一次运气不好的截断；根本原因是分类以"我们是否拿到正文"为准而非"服务商是否应答"（事件日志），且 unknown 保护未按"发送了什么"（媒体/文本/无）区分。

## 4. 解决方案
50f5a71：
- **分类**（`src/omni_story/library/state.py:32` `text_agent_reply`；`src/omni_story/library/pipeline.py:1423`/:1430 调用）：事件日志含 `step_finish`（任意 reason，含 length）即 RECEIVED，把（可能为空的）文本当普通无效回复交给校验/格式修复链；picture_aware 下修复耗尽/无效→`review_unavailable` 继续跑；有 step_start 无 step_finish（超时、中途崩溃）维持 uncertain；spawn 期 OSError（空日志）维持 failed_known（S26 语义不破坏，`log.stat().st_size or …` 只会更倾向 uncertain）。
- **文本调用不绑媒体**（state.py:289–290；拦截 raise 现位于 state.py:300）：仅 `tool == 'text_completion'` 跳过 unknown 媒体拦截（文本传输从不携带媒体）；digest 校验、request_already_recorded 与其他 unknown 调用规则原样保留（媒体未知调用照旧拦，有测试钉住）。
- **旧记录只读重解释**（state.py:60 `text_call_reinterpretation`；server_cli 的 parent_baseline/--resume-from 接纳，现 :225–229/:373–387）：(a) uncertain 文本调用＋空事件＋OSError→`failed_known`（从未发送）；(b) uncertain 文本调用＋事件含完成步骤→`received_invalid`；两者均 never_resend、记录证据哈希（篡改即 `resume_from_silent_text_evidence_changed` 停止）；其余形状维持原拦截；纯只读（file_bytes 前后比对钉住）。
- **输出上限**（`src/omni_story/library/opencode_provider.py:33` `TEXT_MAX_OUTPUT_TOKENS = 32768`）：与视觉通道既有容量一致；设置记 `text_generation.json`（pipeline.py:1385–1386）。（据 S27_fix.md，落地的是提高上限；未记载引入 reasoning effort 旋钮——推断 provider 配置不支持，按任务规格依赖前三项。）
- picture_first 严门不放松（空回复耗尽或旧记录 no-replay 在 picture_first 下依旧停机，仅理由标签变化）；录制任务回放路径零改动。
- 通用性：判定只看事件日志形状与调用工具类型，与具体任务/风格无关，对任何 text_completion 调用同样成立。

## 5. 为什么不用其他方案
- 只提高输出上限、不改分类与媒体耦合：服务器复放显示 8192 下也可能出合法 JSON（预算巧合），分类与耦合缺陷会继续咬住任何真截断/崩溃后的已应答调用——治标不治本。
- 整体放宽 unknown 媒体拦截：那是对"未知付费媒体被重复提交"的真实保护（历史事故由来），只应排除从不携带媒体的调用。
- 重写旧任务账本文件以消除 uncertain：违反录制不可改纪律；接纳时只读重解释＋留痕即可。
- 压缩审查输出的 JSON schema 以避开截断：截断发生在 reasoning 阶段而非正文阶段，压缩 schema 不解决推理吃预算，且复放证明 8192 也能成功——问题在分类不在 schema。
- 把 advisory 审查后置或移除：违背 S25 用户裁定。

## 6. 怎么验收
- 新增 `tests/test_text_call_classification.py` 与 fixtures/s27_*.json（与证据逐字段一致，monkeypatch subprocess.run 走真实生产路径）：证据事件（step_finish length、无文本）→received→修复/advisory `review_unavailable`→继续且无 unknown 媒体拦截；空事件＋OSError 旧记录→baseline/resume 接纳为 failed_known；真 uncertain（有 start 无 finish）仍拦；picture_first 不变；只读性与零重发被钉住（call_text 直接 fail、file_bytes 前后对比）。
- 三门（作者实测，S27_fix.md）：定向 184 通过；全量离线 2523 passed、9 失败均属 baseline（24 个端口测试沙箱排除）；replay ok/fixed 降级 0；fault E1/R1/M1_crash/M2_json/M2_crash 全 0；6 文件 +410/−29；无 uv.lock。
- 服务器复放：8192 上限→该步停止、reasoning 5774、JSON 可解析；32768→停止、reasoning 3663、JSON 可解析。
- 真实恢复运行交付：赵德汉 default4（18 次调用）、雪山 default4（17 次调用）——advisory 审查"绝不为此停机"首次在真实任务上成立到交付。
- S27 审查 verdict pass、2 条低危发现（见第 8 节；审查沙箱拒跑 pytest，按代码逐 hunk 对照规格与证据核验）。

## 7. 量化数据
- 修复前：截断调用形状 reasoning 8180＋output 12＝8192（恰好撞顶）；赵德汉 default3、雪山 default3 两个任务停机（各自总调用数未记录）；旧不烧心 default1 账本拦 parent_baseline。
- 修复后：同类"已应答空正文"调用获得 1 次格式修复机会，修复耗尽也仅 review_unavailable 不停机；文本 unknown 不再拦任何媒体；恢复运行交付 赵德汉 default4 18 次调用、雪山 default4 17 次调用。
- 输出上限：8192→32768（与视觉通道一致，设置落 text_generation.json）。
- 回放/故障：ok/fixed 降级 0；fault 五项 0。
- 修复开发自身花费：未记录（离线开发任务）。

## 8. 经验与后续
- 分类以"服务商是否已应答"为准（事件日志），不以"我们是否拿到能解析的正文"为准；unknown 保护要按"发送了什么"（媒体/文本/无）区分，否则免费的文本检查会毒化付费媒体的复用。
- reasoning 型模型的输出预算含思考 token：给 JSON 检查类调用设上限时按 reasoning＋正文合计（本次 8180＋12＝8192 即反例）。
- **遗留（S27 审查 2 条低危，未修，触发条件窄，需用户决定是否扩权另修）**：
  1. parent_baseline 的 parallel_tasks 分支合并 sibling 账本时丢弃 `other['text_call_reinterpretations']`——sibling 独有祖先账本中被重解释的旧文本调用在新任务里失去 no-replay 钉扎（前置苛刻：需构造字节相同的 text_completion 请求，跨族系几乎不可能相同）。
  2. `write_json(response_path, reply)` 移出 try 块：该写入恰遇本地 IO 故障（磁盘满/权限）时裸 OSError 逃逸，调用停留 `submitted` 终态，resume 永久拒绝接纳（`resume_from_submitted_result_not_settled`）；旧代码此形状会记 uncertain、可经重解释接纳。
- 后续：default4 已交付，回填 advisory 评分与成片对照（接 INC-20261101-19、INC-20261102-01 第 7 节承诺）。
