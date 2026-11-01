# INC-20261101-12 素材缺格时规划留下空 slot、无证据角色、空操作合并和矛盾的画面关系，修复次数耗尽；程序按校验规则整理

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 分镜规划 plan_0；素材不全、多格合并的模板；致命停机 |
| 状态 | 已修复（1b79617），雪山 fix8 真实重跑验证中 |
| 相关提交 | 1b79617 |
| 相关任务 | sb_02_雪山救狐狸07_11010245/…/fix7（失败），…/fix7/evaluations/fix8（验证） |

## 1. 问题长什么样
fix7（release 7a9be4c）停在 `model_protocol_repair_exhausted:plan_0`：
- attempt 0 `plan/slot_segments:list_required`：slot b1 的 segment_ids 为 []，只有 cell s1（片库里没有 s1 的画面）；
- attempt 1 `plan:segments_must_belong_to_exactly_one_slot`：b1 与 b3 重复挂了 seg3；
- attempt 2 `plan:segment_role_missing_overlapping_event_evidence`，位置 `$.segments[4].role_ids` ["A","B"]：所选 2389–2392 秒里没有 A 的事件。

前两条修好之后，回放又暴露出两处：
- `plan:on_screen_speaker_not_visible`：d7 已设身份锚点 seg5，关系却仍写成 seg3 画面内说话；
- storyboard_merges 共 4 条，超过上限 3 条，其中 s7→b9 是把格并入它自己节拍的 slot，属于空操作。

## 2. 怎么查出来的
- 对比三次回复的 slots/segments：b3 已列出 s1 并有画面，b1 是多余的。
- 角色问题：查 `role_event_ranges`，A 的事件与 2389–2392 严格不相交。
- 合并问题：用 question_first/questions.json 的 beats.cell_ids 对照，b9 自己就拥有 s7。s2、s4、s6 分别属于 b2、b5、b7/b8，是真合并。
- d7：identity_anchor_segment_id=seg5 有效，但 audio_relation 写的是 on_screen。`normalise_plan` 的对白循环遇到有效锚点就直接 `continue`，矛盾的关系被保留下来。

## 3. 根因
- 直接原因：模型处理"缺素材格由相邻 slot 承载"时，留下了形式上不一致的字段。
- 根本原因：这些不一致都能按校验器自己的规则唯一判定，程序却只拒绝、不整理，修复次数被形式问题耗光。
- 类型：容错缺失。程序已有锚点处理逻辑，但没处理锚点和关系相互矛盾的情况。

## 4. 解决方案
在 `src/omni_story/library/plan_normalisation.py` 新增四项整理，都按校验器的同一套规则判定，逐条记录，幂等：
- `drop_empty_slot_cells_carried`：删除没有片段、所有格都已由其他有画面的 slot 承载、且没有其他地方引用的空 slot；
- `drop_role_without_event_evidence`：删除所选区间内没有严格相交事件的已确认角色，之后交给已有的对白规则；不新增角色，不改 cast；
- `drop_noop_storyboard_merge`：删除"把格并入自己节拍 slot"的合并条目，并把该格补进 slot 的 storyboard_cell_ids；真合并、上限和策略都不动；
- `anchored_relation_offscreen`：身份锚点有效时，把说话人不可见片段上的 on_screen 改成 offscreen；不新增锚点。

`protocol_feedback.py` 在空 slot 或重复 slot 的反馈里写明格与承载 slot。

## 5. 为什么不用其他方案
- 放宽合并上限或 slot 校验：会掩盖真正的素材不足，违反"素材不足就停下"。
- 自动给说话人重选画面：属于创作选择，程序不该做。
- 只改提示词：同类形式错误已多次出现，提示词改动无法保证模型不再犯。

## 6. 怎么验收
- 新增 `tests/test_slot_role_slips.py`，用三份真实回复走生产校验路径：
  - attempt 0、1 整理后完整通过；
  - attempt 2 剩下真实缺口 `audio_line_window_below_minimum`（3.73 < 4.86 秒），没有被掩盖。
- 单元测试覆盖：格未被承载时保留、被引用时保留、有证据的角色保留、真合并保留、无锚点时不改关系、幂等。
- 全量 pytest：9 failed，全部属于 baseline_2879e5c；回放 203 行，降级 0；故障检查五项均为 0。Opus 与 GLM 各自复跑一遍；GLM 审查 pass。
- 真实重跑：雪山 fix8（release 1b79617），结果待补。

## 7. 量化数据
- fix7 的 3 次规划回复：整理前 0/3 通过，整理后 2/3 通过。
- 合并条目 4→3（去掉 1 条空操作）。

## 8. 经验与后续
- 每次嵌套重跑都会重新找素材，素材和错误形态都会变；一个模板反复失败时，问题分布比单次错误更有参考价值。
- 是否在嵌套重跑时复用父任务的素材和参考理解，需要用户决定。
