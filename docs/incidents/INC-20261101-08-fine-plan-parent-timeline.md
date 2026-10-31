# INC-20261101-08 精剪规划照抄粗剪的原片秒数和窗口，反馈又把唯一正确窗口标成未知，修复耗尽只交粗剪

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 创作路线精剪 chain_e2e_v1_fine_plan（对白类模板）；部分交付（只交粗剪） |
| 状态 | 已修复（fcb0b83）；雪山 fix4 验证中 |
| 相关提交 | fcb0b83 |
| 相关任务 | sb_02_雪山救狐狸07_11010245/evaluations/fix1/evaluations/fix2/evaluations/fix3 |

## 1. 问题长什么样
`outcome: partial`，`partial_reasons: ["model_protocol_repair_exhausted:chain_e2e_v1_fine_plan"]`，交付 45.0 s 粗剪。三次精剪规划：
- attempt 0 `library_render_invalid_number:source_in_s`：片段用 `chain_parent` / `full_parent_observed` 正确，但时间是粗剪规划里的原电影秒数（215.0–221.0、522.5–526.0、2499.4…），而父片只有 45 s；对白说话人抄粗剪窗口 `{"window_id":"window_7537e9cfcc672cfa","role_id":"D"}`。
- 反馈 `valid_window_ids: []`、`unknown_segment_window_ids: ["full_parent_observed"]`；attempt 1 照反馈改成粗剪窗口 → `fixed_parent_source_and_observed_window_only`。
- attempt 2 窗口和时间改对，说话人变成 `{"cast_id":"panda"}` → `plan/audio_draft/speaker/window_id:text_required`。

## 2. 怎么查出来的
拉取三次 protocol_failure.json，与同版本成功交付的不烧心 fix2 精剪规划（全部旁白、无说话人、时间在父片时间轴）逐字段对照。

## 3. 根因
- 精剪上下文同时含粗剪规划（原片时间、粗剪窗口），提示词没有给出精剪阶段唯一合法的窗口和说话人形式；
- `add_valid_choices` 在精剪阶段用粗剪窗口集合计算合法选择，得到空表并把正确的 `full_parent_observed` 标为未知，反馈与校验矛盾；
- 原片时间→父片时间、粗剪说话人→精剪说话人本是可由记录确定的机械换算，却交给模型。
属于提示词/反馈与校验不一致（代码问题）。分镜让对白类成片变长（16 句）后更容易触发；旁白类（不烧心）不受影响。

## 4. 解决方案
- contracts.fine_plan_contract：精剪阶段的来源、合法窗口与说话人形式，提示词模板、校验 `_validate_plan`、`add_valid_choices` 共用。
- plan_normalisation：片段时间完全落在唯一一个粗剪片段的原片范围内时，按记录的父片位置与速度换算为父片时间，记录 `map_rough_source_time_to_parent`；跨段或部分匹配不换算。说话人可由父片记录唯一确定时还原，记录 `map_rough_speaker_to_fine`。
- 创作校验与修复预算不变；旁白类成功规划逐字段不变。

## 5. 为什么不用其他方案
- 从精剪上下文删掉粗剪规划：模型需要它理解镜头来源与台词。
- 增加修复次数：反馈本身错误时多修也无效。

## 6. 怎么验收
真实回复回放：attempt 0 换算 22 个时间字段后通过、attempt 2 还原 11 个说话人后通过、attempt 1 仍按规则拒绝；成功样例不变；跨粗剪段区间不换算。全量测试失败集合等于基线，回放 0 退化，fault_check 关键项为 0。

## 7. 量化数据
修复前 3/3 次失败；修复后同样的回复 2/3 次可直接通过。

## 8. 经验与后续
多阶段流程中，后一阶段的合法取值必须由程序给出并与校验同源；前一阶段的数据只作参考。
