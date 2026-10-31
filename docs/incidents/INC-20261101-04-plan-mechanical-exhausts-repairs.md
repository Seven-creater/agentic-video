# INC-20261101-04 分镜规划片段更多后，机械错误耗尽规划修复预算（不烧心 plan_0 停机）

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线规划 plan_0（新旧路径都受益）；致命停机 |
| 状态 | 已修复（496022c），嵌套重跑 sb_01_不烧心_11010232/evaluations/fix1 进行中 |
| 相关提交 | 496022c |
| 相关任务 | sb_01_不烧心_11010232 |

## 1. 问题长什么样
`model_protocol_repair_exhausted:plan_0`，3 次尝试各错一条不同规则：
- attempt 0：`plan/cast/bindings:list_required`，`$.cast[1].bindings = []`；
- attempt 1：`plan:range_not_supported_by_fine_observation`，`$.segments[5]` 2859.0–2862.0（A），窗口允许 2859–2861（A）和 2861–2862（A）两条相邻范围；
- attempt 2：`plan:repeated_source_footage`，seg_7 与 seg_3 重叠 2 s。
三份规划本身合格：31.5 / 37.5 / 36.5 s（参考 34.4 s）、8–9 句、7 个 slot 按分镜排列。

## 2. 怎么查出来的
拉取三次调用的 protocol_failure.json（证据副本 .agent-team/out/s7/evidence/），逐条对照违规与允许范围，判定前两条是机械问题，第三条是创作冲突。

## 3. 根因
分镜驱动后片段和台词增多，模型在机械字段（跨范围边界、空 cast）上出错的机会变多；这些错误都占用只有 2 次的修复预算。属于反馈/机械字段交给模型的设计问题（代码问题），与 INC-20261031-01 同一原则。

## 4. 解决方案
`src/omni_story/library/plan_normalisation.py`：
- 片段完全落在同一窗口多条首尾相连的可用范围内、且每一段都通过同一套 segment_roles/事件校验时，在边界处拆段，同步所有引用片段 id 的字段（plan_field_contract 列出的 slots、audio_draft、identity_anchor、audio_relation 等）、字幕按帧量化裁切；拆分后超过段数上限则不拆；记录 `split_contiguous_usable_ranges`。
- 无绑定且无台词引用的 cast 项删除并记录；唯一确认实体时回填绑定；有歧义且被引用时仍报违规。
- 重复原片等创作校验、修复预算不变；二次运行幂等。
规则只依赖窗口可用范围和确认身份，对任何模板成立。

## 5. 为什么不用其他方案
- 增加修复次数：多花调用，机械错误仍可能占满预算。
- 放宽“不可合并多条范围”：会允许跨越角色/事件不同的范围。
- 删除被台词引用的空 cast：会让台词失去说话人。

## 6. 怎么验收
新增测试用三份真实回复做夹具：attempt 1 的 segment[5] 被拆成 2859–2861、2861–2862；attempt 0 的空 cast 被删除；attempt 2 仍报 `plan:repeated_source_footage`。两个旧测试断言由 `bindings == []` 改为 `cast == []`，规划仍被拒绝（改为 focus 绑定缺失），本意不变。GLM 审核另修 4 处（字幕帧量化、绑定幂等、段数上限、浮点容差）。全量测试失败集合等于基线，回放 0 退化，fault_check 关键项为 0。

## 7. 量化数据
修复前 3 次尝试中 2 次花在机械问题上。修复后服务器结果待补。

## 8. 经验与后续
规划越丰富，越要把机械簿记交给程序；修复预算只留给创作冲突。
