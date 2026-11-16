# INC-20261115-08 场景人物少于故事角色时，选角被当成格式错误，修正次数耗尽而停机

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-15 |
| 影响范围 | scene_v1 快速线选角阶段（`material_scene_discovery_decide_cast_*`）；凡是参考里说话角色多于所选场景人物数的模板都会中招。严重程度：致命停机 |
| 状态 | 已修复，release `scene_v1_schedule_68ebde903e6f`；从断点续跑验证中 |
| 相关提交 | 尚未提交（GitHub 分支 scene-v1-approved-line-20261115 上是修复前的 206c0d1710a6） |
| 相关任务 | `trials/fast_template_20261115_123739_02_雪山救狐狸_01_e5a516763632`；续跑 `creative/evaluations/fast_resume_20261115_141316` |

## 1. 问题长什么样
夜跑第一条 02_雪山救狐狸在 22:10 停下：`AssertionError:creative_stopped_before_usable_draft:model_protocol_repair_exhausted:material_scene_discovery_decide_cast_1`。

GLM 的三次回复依次是：
1. 只给 4 个角色配了演员，漏掉红狐、青衣精怪、老铁匠中的若干个，报错 `cast must bind every story role exactly once`；
2. 给 5 个角色都配了演员，但让绿骑士同时演青衣精怪和老铁匠，报错 `different story roles cannot share an actor`；
3. 去掉老铁匠只配 4 个角色，又报 `cast must bind every story role exactly once`。

## 2. 怎么查出来的
- 查 `run_outcome.json`：停在 `pipeline.py:1898`，`repairs=9`，最后一次调用是 `glm_057_..._decide_cast_1_repair2`。
- 逐个读 `calls/glm_055~057/protocol_failure.json` 和 `response.json`，得到上面三次回复。
- 读 `autonomous_material/dialogue_requirements.json`：`optional_roles=[]`，GLM 认为 5 个角色都承担主线或关键反转，不能省。参考共有 5 个说话角色，分布在 scene_1/3/8/10/13 等多个场景。
- 所选洞窟场景（rank0）只有 4 位可区分的个体演员：灰骑士、白猫、黄骑士、绿骑士。
- 定位到 `src/omni_story/library/material_plan.py:10` 的 `validate_cast`（要求每个角色恰好一个演员，且演员互不相同），以及 `scene_casting.py` 的 `direct_cast` 提示词。

## 3. 根因
- 直接原因：4 个人演不了 5 个互不重复的角色，`finish` 在这组场景里根本不可能通过。
- 根本原因：选角提示词没说"每个角色要配一个不同的演员"这条硬规则，也没给出本组的人数；报错只有一句英文笼统规则，没说明还缺哪些角色、本组人数不够、应当换场景（candidate）或扩展画面（expand）。GLM 只能在"漏角色"和"一人演两角"之间来回改，三次就把修正次数用完了。
- 问题类型：提示词与校验不一致，反馈不可操作。11-10 冻结批次里 21 号模板的"容量不足被当格式错误"属于同一类。

## 4. 解决方案
改动都在 `src/omni_story/library/scene_casting.py`：
- 新增 `cast_capacity(story)`：统计当前场景实际出镜的可区分个体演员，群体不算。
- 选角上下文新增 `cast_requirements`，写明故事角色、需要的不同演员数、本组个体演员和必须覆盖的场景 ID。提示词里写明硬规则：人数不够时不能 finish，锁角前改选候选场景，或者扩展画面。
- 新增 `explain_cast_errors`，在原校验之前给出具体反馈：人数不够时说明不能 finish、该怎么继续（锁角后只提示 expand）；列出缺少或多出的角色；列出被共用的演员；列出漏掉的场景 ID。`validate_cast` 原样保留，作为最终把关。

为什么是通用的：只用角色数、出镜人数、场景 ID 这些对所有模板都存在的结构信息，没有任何模板名、角色名、演员或窗口的特判。

## 5. 为什么不用其他方案
- 允许一个演员分饰两个不同场的角色：同一个形象演两个人，观众会认错人，违反"角色不清必须修好"的要求。
- 在台词需求阶段强制删减角色：会删掉 GLM 判定承担关键反转的人物，伤主线；是否省略应由创作判断，不该由程序硬删。
- 提高修正次数上限：反馈不可操作时，次数再多也只是重复同样的错误。
- 程序自动替 GLM 换候选场景：替代了创作判断，而且换到哪个场景也不确定；现在的做法是把真实约束交给 GLM 去选。

## 6. 怎么验收
- 新增 `test_cast_capacity_errors.py` 共 6 项：人数统计不算群体；人数不足时提示不能 finish 并给出 candidate/expand，锁角后不再提 candidate；缺角色时点名；共用演员时点名；漏场景时点名；合法的选角能通过。
- 服务器全量测试 158 项通过（原来 152 项，加上新增 6 项）。
- 真实续跑：从停下的任务断点续跑，只重发选角这一步。结果待补。

## 7. 量化数据
- 修复前：选角阶段 3 次 GLM 调用全部被拒，整条任务停机，已用掉约 1.5 小时（20:37 → 22:10）。
- 修复后：续跑结果待补。

## 8. 经验与后续
- 凡是"这一组不可能满足"的情况，报错里必须写清不可能的原因，以及合法的下一步动作，不能只复述规则。
- 遗留风险：换场景或扩展画面以后，可能仍然找不到 5 位同场人物，任务会以"素材不足"停下。按 [素材不足就停下] 的规则，这属于合理停止。

## 补充（22:28）：扩展请求被原样交回，扩展失效

- 现象：在 68ebde903e6f 上续跑后，选角阶段正确返回了 `expand`，理由写明"需要第 5 位同场个体"。但挑窗口阶段（`browser_decide_5`）一个新窗口都没看，直接把原来两个只有 4 人的候选交了回来，自己也写明"凑不齐 5 位"。程序随即以 `cast_expansion_no_new_video_evidence`（`scene_casting.py:463`）停下。
- 根因：`material_browser.run(feedback)` 处理补充请求时，没有要求先看新画面就允许 finish，扩展请求等于没执行。这属于判定矛盾：上游提出了缺口，下游自己承认没补上，却仍被当成正常完成。
- 修复（release `scene_v1_schedule_650f5b42b5af`）：
  - 收到补充请求后，如果库里还有没看过的窗口，就必须先 watch 至少 1 个新窗口，才允许 finish；报错里带上缺口原因。
  - 选角阶段最多扩展 `CAST_EXPANSION_LIMIT=3` 次，超过就写入 `cast_material_gap` 并以 `cast_material_gap_after_expansions` 停下，明确标成素材不足，不无限循环。
- 验收：新增 `test_browser_followup_requires_new_watch.py`（2 项），服务器全量测试 160 项通过；真实续跑结果待补。

## 补充（22:33）：续跑复用旧回复时，新规则直接报错停机

- 现象：在 650f5b42b5af 上续跑时，`browser_decide_5` 的请求没有变化，于是复用了上次已收到的回复（原样交回旧候选）。这条旧回复过不了新加的规则，`material_browser.py:798` 直接抛出 ValueError，任务停止，期间没有发出任何新的 GLM 调用。
- 根因：`pipeline._material_call` 已经有现成机制：复用的旧回复过不了新规则时，只发一次有上限的修正请求（stage 加 `_protocol_revalidated` 后缀）。这个机制要求校验函数带 `revalidated_protocol_repair=True`，之前只有 `joint_dialogue` 开了。
- 修复（release `scene_v1_schedule_d314fec42dd1`）：挑窗口导航和选角决策两处的校验函数都开启这个标记。这是通用规则，适用于以后任何"新规则让旧回复失效"的续跑。
- 验收：测试里断言挑窗口校验函数带这个标记，服务器全量测试 160 项通过；真实续跑结果待补。

## 补充（11-16 00:30）：GLM 判断库里找不到，但没有正当的停止出口

- 现象：第二轮扩展时，`browser_decide_6`、`decide_7` 以及 `decide_7` 的修正回复，都在没看新窗口的情况下交回旧候选。GLM 在 limitations 中写明"仅 4 位个体，缺第 5 人"，即它认为素材库里没有能补上的画面。按原来的规则，这会耗尽修正次数，最终被当成协议错误停机。
- 根因：补充请求只有"继续看"这一条路，没有"如实判定素材不足"的出口；GLM 的真实判断被错当成格式错误。
- 修复（release `scene_v1_schedule_eed8045fcb68`）：补充请求下，第一次没看新窗口就交回，照旧打回并要求去看；修正后仍坚持交回，就记录 `browser_followup_material_gap`，以 `LibraryStopped('browser_followup_material_gap')` 停下。夜跑驱动把它当作素材不足处理，自动换下一个模板。
- 验收：新增测试"两次拒绝即判定素材不足"，服务器全量测试 164 项通过。
