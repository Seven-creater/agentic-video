# INC-20261101-07 核心格无素材时规划只能违规：反馈要求用不存在的窗口，片段归属笔误也耗掉修复

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 分镜路径规划 plan_0；致命停机（雪山 fix2，226 次调用零产出） |
| 状态 | 已修复（029dee9）；雪山 fix3 验证中 |
| 相关提交 | 029dee9 |
| 相关任务 | sb_02_雪山救狐狸07_11010245/evaluations/fix1/evaluations/fix2 |

## 1. 问题长什么样
`model_protocol_repair_exhausted:plan_0`：
- attempt 0 `audio_line_window_below_minimum`（创作时长问题）；
- attempt 1 `plan:segments_must_belong_to_exactly_one_slot`：slot b4 写成 `["seg_2"]`，而 seg_3 自身 `slot_id:"b4"`；
- attempt 2 `plan:storyboard_core_cell_uncovered`：核心格 s1（救狐盖箱）、s4（自报快递员）的节拍 b1、b5 没有任何观察窗口（`no_minimal_visual_candidate`）。模型把它们写进 `storyboard_merges`（into b2/b6，理由“无可用原片画面；由旁白与庭院全景承接”），被“核心格不能合并”拒绝；反馈却要求“选择其节拍已观察窗口的真实片段”，而 `observed_window_ids: []`。

## 2. 怎么查出来的
拉取三次 protocol_failure.json，对照 search_0.json 的 gaps 与 storyboard 核心格，再统计 attempt 1 的 slot/segment 成员关系。

## 3. 根因
- 片段归属：slots[].segment_ids 与 segments[].slot_id 是冗余字段，模型抄错一个就整份作废（机械字段交给模型）。
- 核心格：校验本来允许核心格挂在任何有片段的 slot 上，但提示词只说“核心格不得合并”，反馈只给“用自己的窗口”这一条不可能的路；模型的真实意图（由相邻画面加配音承载）没有合法写法可循。属于提示词/反馈与校验不一致（代码问题）。

## 4. 解决方案
- `normalise_storyboard_slots`：每个片段都有有效 slot_id 时，按 segments 顺序重建 slots[].segment_ids 并记录；任一片段无效或 slot 变空则不动。
- 核心格写进 storyboard_merges 且 into_slot_id 是有片段的现有 slot：移入该 slot 的 storyboard_cell_ids（按格时间排序），记录 `core_cell_carried_by_slot` 与模型理由；核心覆盖规则本身不变，30% 上限只计非核心格。
- 无窗口核心格的反馈与提示词共用一段文字：列入相邻有片段 slot 的 storyboard_cell_ids，由该画面配合配音承载。
- pipeline 把分镜传给 normalise_plan（审核发现 GPT 版本未接线，Opus 补上）。

## 5. 为什么不用其他方案
- 核心格无素材直接停：违背“重释优先”，相邻画面加配音可以承载；是否讲清由精审逐格判断。
- 允许核心格进 storyboard_merges：会和“非核心并入上限”混在一起，记录语义不清。

## 6. 怎么验收
用 attempt 1、2 的真实回复回放：规范化后两份都通过全部规划校验（rebuild=1；s1→b2、s4→b6）。负例：未知 slot_id 不重建、并入未知 slot 仍拒绝、旧路径不变。全量测试失败集合等于基线，回放 0 退化，fault_check 关键项为 0。

## 7. 量化数据
修复前 3 次尝试中 2 次可由程序消除；修复后同样两份回复 0 违规。

## 8. 经验与后续
程序给出的修复路径必须在当前数据下可执行；冗余字段由程序以单一来源重建。
