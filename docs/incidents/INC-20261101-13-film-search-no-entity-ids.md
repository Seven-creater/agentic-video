# INC-20261101-13 原片检索模式不给已确认角色分配实体编号，带对白的规划不可能通过

| 项 | 内容 |
|---|---|
| 发现日期 | 2026-11-01 |
| 影响范围 | 新增的 `--material-search film`（主旨迁移 + 只看原片检索）；所有带对白的规划；致命停机 |
| 状态 | 已修复（5d1fb42），三个模板真实重跑验证中 |
| 相关提交 | 762d7f2（新增 film 模式）、5d1fb42（修复） |
| 相关任务 | sb_20_赵德汉藏钱_11010204/evaluations/theme1/evaluations/film1、sb_01_不烧心_…/theme1/evaluations/film1（均失败）；film2 / 雪山 film1（验证） |

## 1. 问题长什么样
- 赵德汉 film1（25 次调用）：plan_0 三次失败，依次是 `plan/cast:list_required`（cast 写成单个对象）、`plan/slot/coverage/gaps:list_required`、`plan/slot/coverage/visual_goal:text_required`。
- 不烧心 film1（22 次调用）：三次都是 `plan/cast/cast_id:text_required`，模型自己编了 `panda_A@window_…` 这样的实体编号。
- 两条任务每次反馈里都是 `bindable_triples: []`。

## 2. 怎么查出来的
查了 watched_windows.json：赵德汉 6 个窗口里所有角色都是 `identity_confirmed: true`，但没有一个带 `entity_id`。规划合同（`plan_field_contract(...)['cast']`、`validate_task_cast`）要求对白角色绑定的 entity_id 必须逐字照抄已确认的观察角色。素材库模式的实体编号来自素材库，question_first 模式来自片中角色表和锚点，film 模式没有任何来源。不烧心的失败形态不同，但根因相同。

## 3. 根因
- 直接原因：模型拿不到实体编号，cast 只能空着、编造或写错形状。
- 根本原因：新开的检索模式和已有的身份合同没有对齐：合同默认每个已确认角色都有实体编号，film 模式却没有提供。这是代码问题（流程分支漏了身份来源），不是模型能力问题。
- 次要原因：coverage 字段的形状（字符串还是数组）在修复轮次中来回摆，没有程序整理。

## 4. 解决方案
- `pipeline.py`：film 模式下，已确认且没有实体编号的角色，获得一个确定性的窗口级编号 `<source_sha256>:film_<window_id>:<role_id>`，并记录 `entity_id_origin: "film_window_scoped"`。不跨窗口合并身份：同一人物在不同窗口算不同的 cast 项，可以共用音色。续跑时重新推导出同样的编号。
- `plan_normalisation.py`：coverage 形状整理（gaps 字符串改成数组，visual_goal/audio_goal 数组用"；"拼接）；cast 写成单个对象时包成数组。两项都会记录。
- `protocol_feedback.py`：没有可绑定身份时，反馈里明确说出来。
- library 和 question_first 模式不受影响。

## 5. 为什么不用其他方案
- 让模型在精看时自己写 entity_id：模型会编造不稳定的编号，不烧心已经出现过这种情况，而且续跑时无法复现。
- 跨窗口按外形自动合并身份：容易把不同角色误判成同一个，违反"不编造身份"。
- 放宽 cast 校验：会让对白音色一致性检查失效。

## 6. 怎么验收
- 新增 `tests/test_film_identity.py`（28 项）：证据窗口的可绑定角色从 0 个变成 13 个。三份真实回复重放后，剩下的都是真实的创作或时间问题（cast 缺 cast_id、一组台词差 2.3 秒、一处原片时间写错），没有被掩盖。
- 全量 pytest：9 failed，全部属于基线；历史回放 203 行，降级 0；故障检查五项均为 0。Opus 与 GLM 各自复跑一遍；GLM 审查 pass。
- 真实重跑：结果待补。

## 7. 量化数据
- 修复前，film 模式 2/2 停在 plan_0，可绑定身份 0 个。
- 修复后，同一份证据有 13 个可绑定身份。

## 8. 经验与后续
- 新增检索模式时，要逐项核对下游合同依赖的字段（实体编号、字幕遮罩、内容范围）在新模式里有没有来源。
- GLM 审查的非阻断意见：
  - F1：派生编号让 parsed.json 和窗口观察不再逐字节相等，几条需要人工授权的特殊恢复流程会安全拒绝继续；以后在那些流程里改成按规范化后的观察比较。
  - F2：没有身份来源的默认 creative 运行里，"改用旁白"的提示和对白合同略有冲突。
